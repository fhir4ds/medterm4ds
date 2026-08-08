"""LOINC part-model relationship primitives.

Handles the LOINC ``component_of`` / ``class_of`` relationships and the
LP↔LP CHD/PAR sub-hierarchy that ``services.walk`` does not expose.

``services.walk`` queries ``mt4ds.walk_edges``, which is ISA-only by design
(see ``engines/duckdb/prepared.py:_prepare_hierarchy_edges``). LOINC's part
model layers an additional relationship vocabulary on top:

* ``component_of`` — links a LOINC test (e.g. ``2160-0`` Creatinine) to its
  analyte component LP code (e.g. ``LP14319-6`` Creatinine).
* ``class_of`` — links a LOINC test to its LOINC class LP code (e.g.
  ``LP7780-3`` Chemistry).
* LP↔LP ``CHD`` / ``PAR`` — sub-hierarchy between component parts
  (e.g. ``LP29041-8`` Cortisol Free is a child of ``LP14161-1`` Cortisol).

This module exposes both as batch-friendly primitives returning
``CodeRelation`` objects consistent with ``services.walk``.

DATA NOTE: ``umls_local.duckdb`` currently does NOT preserve LOINC
``component_of`` / ``class_of`` edges between LNC atoms (see
``canonical_anchors/.temp/phase_1c_investigation.md`` and the note in
``build_snomed_loinc_crosswalk.py:57``). The LP↔LP CHD/PAR sub-hierarchy IS
preserved (47,795 edges) and is already exposed via ``mt4ds.walk_edges``.

As a result, ``get_lp_children`` / ``get_lp_descendants`` / ``get_lp_ancestors``
work against production data today; ``get_component_tests`` and
``get_class_of`` return empty until the local DuckDB is rebuilt with these
RELA edges preserved. They remain correct against synthetic test data and
against any future DB build that restores the edges.
"""
from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, CodeRelation
from medterm4ds.services.prepared_primitives import (
    dedupe_values,
    temp_codes,
)

# LOINC source label as normalized by CodeRef.
_LOINC_SOURCE = "LNC"


def get_lp_children(
    lps: Sequence[CodeRef],
    con,
) -> list[CodeRelation]:
    """Return direct LP↔LP child relationships for the given LP codes.

    Uses the LP↔LP CHD/PAR sub-hierarchy already exposed in
    ``mt4ds.walk_edges`` as LNC ``isa`` rows.

    Parameters
    ----------
    lps:
        Input LP codes (``source='LNC'``). Non-LNC inputs are ignored.
    con:
        DuckDB connection with ``mt4ds.walk_edges`` available.

    Returns
    -------
    list[CodeRelation]
        Direct child relationships (depth=1).
    """
    lnc_codes = _filter_lnc_codes(lps)
    if not lnc_codes:
        return []
    deduped = dedupe_values(lnc_codes)
    results: list[CodeRelation] = []
    with temp_codes(con, deduped, prefix="_mt4ds_lp_codes") as temp:
        # walk_edges orientation: from=child, to=parent, direction='parent'.
        # Children of seed = rows where seed is the parent (to_code).
        rows = con.execute(
            f"""
            SELECT we.from_code, we.to_code, we.from_cui, we.to_cui,
                   we.from_aui, we.to_aui
            FROM mt4ds.walk_edges we
            WHERE we.source = ?
              AND we.direction = 'parent'
              AND we.to_code IN (SELECT code FROM {temp})
            ORDER BY we.to_code, we.from_code
            """,
            [_LOINC_SOURCE],
        ).fetchall()

    for child_code, parent_code, child_cui, parent_cui, child_aui, parent_aui in rows:
        results.append(
            CodeRelation(
                source=CodeRef(source=_LOINC_SOURCE, code=parent_code),
                target=CodeRef(source=_LOINC_SOURCE, code=child_code),
                relationship="child",
                depth=1,
                source_cui=parent_cui,
                target_cui=child_cui,
                source_aui=parent_aui,
                target_aui=child_aui,
                rel="isa",
            )
        )
    return results


def get_lp_descendants(
    lps: Sequence[CodeRef],
    con,
    *,
    max_depth: int = 20,
) -> list[CodeRelation]:
    """Return transitive LP descendants of the given LP codes via walk_edges.

    Uses the ISA closure path in ``services.walk`` semantics. Walks
    ``mt4ds.walk_edges`` iteratively down the LP↔LP sub-hierarchy.
    """
    lnc_codes = _filter_lnc_codes(lps)
    if not lnc_codes:
        return []
    return _walk_lp_transitive(
        seed_codes=dedupe_values(lnc_codes),
        con=con,
        max_depth=max_depth,
        upward=False,
    )


def get_lp_ancestors(
    lps: Sequence[CodeRef],
    con,
    *,
    max_depth: int = 20,
) -> list[CodeRelation]:
    """Return transitive LP ancestors of the given LP codes via walk_edges."""
    lnc_codes = _filter_lnc_codes(lps)
    if not lnc_codes:
        return []
    return _walk_lp_transitive(
        seed_codes=dedupe_values(lnc_codes),
        con=con,
        max_depth=max_depth,
        upward=True,
    )


def get_component_tests(
    components: Sequence[CodeRef],
    con,
) -> list[CodeRelation]:
    """Return LOINC tests linked to the given LP components via ``component_of``.

    A test ``2160-0`` (Creatinine) has ``component_of → LP14319-6`` in UMLS.
    Given the LP code, this returns the test codes that point to it.

    Parameters
    ----------
    components:
        Input LP component codes (``source='LNC'``). Non-LNC or non-LP inputs
        are ignored.
    con:
        DuckDB connection with ``mrrel`` and ``mrconso`` available.

    Returns
    -------
    list[CodeRelation]
        ``component_of`` relationships (source=LP, target=test), depth=1.
        ``rel='component_of'``, ``rela='component_of'``.

    Notes
    -----
    DATA NOTE: ``umls_local.duckdb`` currently drops LOINC ``component_of``
    edges between LNC atoms — this function returns empty against production
    data until the DB is rebuilt (see module docstring).
    """
    lp_codes = _filter_lp_codes(components)
    if not lp_codes:
        return []
    deduped = dedupe_values(lp_codes)
    results: list[CodeRelation] = []
    with temp_codes(con, deduped, prefix="_mt4ds_lp_comp_codes") as temp:
        # component_of in mrrel: AUI1=test atom, AUI2=LP component atom.
        # Given the LP code, find tests where AUI2 resolves to that LP.
        rows = con.execute(
            f"""
            SELECT DISTINCT
              child.CODE AS test_code, child.AUI AS test_aui, child.CUI AS test_cui,
              parent.CODE AS lp_code, parent.AUI AS lp_aui, parent.CUI AS lp_cui
            FROM mrrel r
            JOIN mrconso child ON child.AUI = r.AUI1
            JOIN mrconso parent ON parent.AUI = r.AUI2
            JOIN {temp} t ON t.code = parent.CODE
            WHERE r.RELA = 'component_of'
              AND child.SAB = 'LNC' AND parent.SAB = 'LNC'
              AND child.SUPPRESS = 'N' AND parent.SUPPRESS = 'N'
              AND parent.CODE LIKE 'LP%'
              AND child.CODE NOT LIKE 'LP%'
            ORDER BY parent.CODE, child.CODE
            """,
        ).fetchall()

    for test_code, test_aui, test_cui, lp_code, lp_aui, lp_cui in rows:
        results.append(
            CodeRelation(
                source=CodeRef(source=_LOINC_SOURCE, code=lp_code),
                target=CodeRef(source=_LOINC_SOURCE, code=test_code),
                relationship="component_test",
                depth=1,
                source_cui=lp_cui,
                target_cui=test_cui,
                source_aui=lp_aui,
                target_aui=test_aui,
                rel="component_of",
                rela="component_of",
            )
        )
    return results


def get_class_of(
    test_codes: Sequence[CodeRef],
    con,
) -> dict[str, tuple[str, str]]:
    """Return ``{test_code: (class_code, class_display)}`` for LOINC tests.

    Uses the LOINC ``class_of`` relationship to map a test code (e.g.
    ``2160-0`` Creatinine) to its class LP code and display string
    (e.g. ``LP7780-3`` / "Chemistry").

    Parameters
    ----------
    test_codes:
        Input LOINC test codes (``source='LNC'``). Non-LNC inputs are ignored.
    con:
        DuckDB connection with ``mrrel`` and ``mrconso`` available.

    Returns
    -------
    dict[str, tuple[str, str]]
        Map of test code → ``(class_lp_code, class_display)``. Tests with no
        ``class_of`` row are omitted.

    Notes
    -----
    DATA NOTE: ``umls_local.duckdb`` currently drops LOINC ``class_of`` edges
    between LNC atoms — this function returns an empty dict against production
    data until the DB is rebuilt (see module docstring).
    """
    lnc_codes = _filter_lnc_codes(test_codes)
    if not lnc_codes:
        return {}
    deduped = dedupe_values(lnc_codes)
    out: dict[str, tuple[str, str]] = {}
    with temp_codes(con, deduped, prefix="_mt4ds_test_codes") as temp:
        rows = con.execute(
            f"""
            SELECT DISTINCT
              child.CODE AS test_code,
              parent.CODE AS class_code, parent.STR AS class_display
            FROM mrrel r
            JOIN mrconso child ON child.AUI = r.AUI1
            JOIN mrconso parent ON parent.AUI = r.AUI2
            JOIN {temp} t ON t.code = child.CODE
            WHERE r.RELA = 'class_of'
              AND child.SAB = 'LNC' AND parent.SAB = 'LNC'
              AND child.SUPPRESS = 'N' AND parent.SUPPRESS = 'N'
              AND parent.CODE LIKE 'LP%'
              AND child.CODE NOT LIKE 'LP%'
            """,
        ).fetchall()
    for test_code, class_code, class_display in rows:
        # First-wins on multi-class tests (matches prior build_lab_vital
        # LIMIT 1 behavior).
        if test_code in out:
            continue
        out[str(test_code)] = (str(class_code), str(class_display) if class_display else "")
    return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _filter_lnc_codes(codes: Sequence[CodeRef]) -> list[str]:
    """Return LNC code strings from a sequence of CodeRef inputs."""
    return [c.code for c in codes if c.source == _LOINC_SOURCE and c.code]


def _filter_lp_codes(codes: Sequence[CodeRef]) -> list[str]:
    """Return LP-prefixed LNC code strings from a sequence of CodeRef inputs."""
    return [c.code for c in codes if c.source == _LOINC_SOURCE and c.code.startswith("LP")]


def _walk_lp_transitive(
    *,
    seed_codes: Sequence[str],
    con,
    max_depth: int,
    upward: bool,
) -> list[CodeRelation]:
    """Iterative BFS over mt4ds.walk_edges restricted to LNC source.

    Mirrors the structure of ``services.walk._walk_transitive`` but is
    hardcoded to LNC source and uses depth-bounded batches. Avoids pulling
    in walk_closure_limited (which may not exist in all prepared DBs).
    """
    from collections import defaultdict, deque

    visited: set[str] = set(seed_codes)
    queue: deque[tuple[str, int]] = deque((code, 1) for code in seed_codes)
    results: list[CodeRelation] = []

    while queue:
        batch: dict[int, list[str]] = defaultdict(list)
        batch_items: list[tuple[str, int]] = []
        while queue and len(batch_items) < 500:
            code, depth = queue.popleft()
            if depth > max_depth:
                continue
            batch[depth].append(code)
            batch_items.append((code, depth))
        if not batch_items:
            break

        for depth, codes_at_depth in batch.items():
            with temp_codes(con, dedupe_values(codes_at_depth), prefix="_mt4ds_lp_walk") as temp:
                if upward:
                    rows = con.execute(
                        f"""
                        SELECT we.from_code, we.to_code, we.from_cui, we.to_cui,
                               we.from_aui, we.to_aui
                        FROM mt4ds.walk_edges we
                        WHERE we.source = ?
                          AND we.direction = 'parent'
                          AND we.from_code IN (SELECT code FROM {temp})
                        ORDER BY we.from_code, we.to_code
                        """,
                        [_LOINC_SOURCE],
                    ).fetchall()
                else:
                    rows = con.execute(
                        f"""
                        SELECT we.from_code, we.to_code, we.from_cui, we.to_cui,
                               we.from_aui, we.to_aui
                        FROM mt4ds.walk_edges we
                        WHERE we.source = ?
                          AND we.direction = 'parent'
                          AND we.to_code IN (SELECT code FROM {temp})
                        ORDER BY we.to_code, we.from_code
                        """,
                        [_LOINC_SOURCE],
                    ).fetchall()

            next_frontier: list[str] = []
            for from_code, to_code, from_cui, to_cui, from_aui, to_aui in rows:
                if upward:
                    source_code = from_code
                    target_code = to_code
                    source_cui = from_cui
                    target_cui = to_cui
                    source_aui = from_aui
                    target_aui = to_aui
                    next_code = to_code
                    relationship = "ancestor"
                else:
                    source_code = to_code
                    target_code = from_code
                    source_cui = to_cui
                    target_cui = from_cui
                    source_aui = to_aui
                    target_aui = from_aui
                    next_code = from_code
                    relationship = "descendant"

                results.append(
                    CodeRelation(
                        source=CodeRef(source=_LOINC_SOURCE, code=source_code),
                        target=CodeRef(source=_LOINC_SOURCE, code=target_code),
                        relationship=relationship,
                        depth=depth,
                        source_cui=source_cui,
                        target_cui=target_cui,
                        source_aui=source_aui,
                        target_aui=to_aui if upward else from_aui,
                        rel="isa",
                    )
                )
                if next_code not in visited:
                    visited.add(next_code)
                    next_frontier.append(next_code)
            for next_code in next_frontier:
                queue.append((next_code, depth + 1))
    return results
