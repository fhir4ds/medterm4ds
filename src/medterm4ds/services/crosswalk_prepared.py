"""Crosswalk service over prepared mt4ds tables.

Provides ``get_crosswalk_mappings`` which returns source-to-target code
mappings using the canonical ``mt4ds.crosswalk_edges`` table for exact
same-CUI cross-source links, with fallback to legacy
``mt4ds.same_cui_edges`` when needed. It uses ``mt4ds.walk_edges`` for
broader/narrower ancestor-based mappings when *max_depth* > 0.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence

from medterm4ds.core.models import CodeMapping, CodeRef, Provenance, ProvenanceStep
from medterm4ds.services.prepared_primitives import (
    same_cui_crosswalk_sql as _same_cui_crosswalk_sql,
)
from medterm4ds.services.prepared_primitives import (
    source_display_lookup as _source_display_lookup,
)
from medterm4ds.services.prepared_primitives import (
    temp_codes as _temp_codes,
)
from medterm4ds.services.prepared_primitives import (
    walk_closure_table,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_crosswalk_mappings(
    codes: Sequence[CodeRef],
    con,
    *,
    target_sources: Sequence[str] | None = None,
    max_depth: int = 0,
) -> list[CodeMapping]:
    """Cross-source mappings using mt4ds prepared tables.

    Parameters
    ----------
    codes:
        Input codes to crosswalk.
    con:
        DuckDB connection with ``mt4ds.crosswalk_edges`` or legacy
        ``mt4ds.same_cui_edges`` available. ``mt4ds.walk_edges`` is also
        required when *max_depth* > 0.
    target_sources:
        If provided, only return mappings to these target sources.
    max_depth:
        When 0 (default), return only exact same-CUI mappings.
        When > 0, also walk ancestors via ``mt4ds.walk_edges`` and
        find same-CUI targets from those ancestors (broader mappings).

    Returns
    -------
    list[CodeMapping]
        Cross-source mappings ordered by input position.
    """
    if not codes:
        return []
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")

    # Preserve input order with ordinals
    ordered = [CodeRef(source=c.source, code=c.code) for c in codes]

    # Step 1: exact same-CUI mappings
    exact = _exact_mappings(ordered, con, target_sources=target_sources)

    if max_depth == 0:
        return _ordered_results(ordered, exact)

    # Step 2: broader mappings via ancestor walk
    broader = _broader_mappings(ordered, con, target_sources=target_sources, max_depth=max_depth)

    # Merge: exact first, then broader, deduped by (ordinal, target_source, target_code)
    seen: set[tuple[int, str, str]] = set()
    merged: list[tuple[int, CodeMapping]] = []
    for ordinal, mapping in exact:
        key = (ordinal, mapping.target.source, mapping.target.code)
        if key not in seen:
            seen.add(key)
            merged.append((ordinal, mapping))
    for ordinal, mapping in broader:
        key = (ordinal, mapping.target.source, mapping.target.code)
        if key not in seen:
            seen.add(key)
            merged.append((ordinal, mapping))

    return _ordered_results(ordered, merged)


# ---------------------------------------------------------------------------
# Internal: exact same-CUI mappings
# ---------------------------------------------------------------------------

def _exact_mappings(
    ordered: Sequence[CodeRef],
    con,
    *,
    target_sources: Sequence[str] | None = None,
) -> list[tuple[int, CodeMapping]]:
    """Return (ordinal, CodeMapping) for exact same-CUI cross-source links."""
    grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for ordinal, ref in enumerate(ordered):
        grouped[ref.source].append((ordinal, ref.code))

    results: list[tuple[int, CodeMapping]] = []

    for source, code_ordinals in grouped.items():
        deduped_codes = list(dict.fromkeys(code for _ord, code in code_ordinals))

        # Build display lookup for source codes
        display = _source_display_lookup(con, source, deduped_codes)
        code_to_ordinal: dict[str, list[int]] = defaultdict(list)
        for ord_val, code in code_ordinals:
            code_to_ordinal[code].append(ord_val)

        with _temp_codes(con, deduped_codes) as temp:
            edge_table, match_filter = _same_cui_crosswalk_sql(con)
            if target_sources:
                rows = con.execute(
                    f"""
                    SELECT sce.code, sce.cui,
                           sce.target_source, sce.target_code,
                           sce.target_aui, sce.target_cui, sce.target_tty
                    FROM {edge_table} sce
                    WHERE sce.source = ?
                      AND sce.code IN (SELECT code FROM {temp})
                      AND sce.target_source IN (SELECT unnest(?))
                      {match_filter}
                    """,
                    [source, list(target_sources)],
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT sce.code, sce.cui,
                           sce.target_source, sce.target_code,
                           sce.target_aui, sce.target_cui, sce.target_tty
                    FROM {edge_table} sce
                    WHERE sce.source = ?
                      AND sce.code IN (SELECT code FROM {temp})
                      {match_filter}
                    """,
                    [source],
                ).fetchall()

        # Build target display lookup
        target_display: dict[tuple[str, str], tuple[str, str, str]] = {}
        for _src_code, _src_cui, tgt_source, tgt_code, tgt_aui, tgt_cui, tgt_tty in rows:
            target_display.setdefault((tgt_source, tgt_code), (tgt_aui, tgt_cui, tgt_tty))

        target_codes_by_source: dict[str, list[str]] = defaultdict(list)
        for (ts, tc), _info in target_display.items():
            target_codes_by_source[ts].append(tc)

        target_names: dict[tuple[str, str], str | None] = {}
        for ts, tc_list in target_codes_by_source.items():
            tl = _source_display_lookup(con, ts, tc_list)
            for tc, (t_name, _t_cui, _t_aui) in tl.items():
                target_names[(ts, tc)] = t_name

        for src_code, src_cui, tgt_source, tgt_code, tgt_aui, tgt_cui, tgt_tty in rows:
            src_info = display.get(src_code)
            src_display = src_info[0] if src_info else None
            src_aui = src_info[2] if src_info else None
            tgt_display_name = target_names.get((tgt_source, tgt_code))

            for ordinal in code_to_ordinal.get(src_code, []):
                results.append((
                    ordinal,
                    CodeMapping(
                        source=CodeRef(source=source, code=src_code),
                        target=CodeRef(source=tgt_source, code=tgt_code),
                        relationship="equivalent",
                        match_type="same_cui",
                        match_depth=0,
                        source_display=src_display,
                        target_display=tgt_display_name,
                        source_cui=src_cui,
                        target_cui=tgt_cui,
                        source_aui=src_aui,
                        target_aui=tgt_aui,
                        target_tty=tgt_tty,
                        matched_via=Provenance.from_steps(
                            "same_cui",
                            [
                                ProvenanceStep(
                                    op="input_atom",
                                    source=source,
                                    code=src_code,
                                    cui=src_cui,
                                    aui=src_aui,
                                    name=src_display,
                                ),
                                ProvenanceStep(
                                    op="same_cui",
                                    source=source,
                                    code=src_code,
                                    target_source=tgt_source,
                                    target_code=tgt_code,
                                    cui=src_cui,
                                ),
                            ],
                        ),
                    ),
                ))

    return results


# ---------------------------------------------------------------------------
# Internal: broader ancestor-based mappings
# ---------------------------------------------------------------------------

def _broader_mappings(
    ordered: Sequence[CodeRef],
    con,
    *,
    target_sources: Sequence[str] | None = None,
    max_depth: int = 1,
) -> list[tuple[int, CodeMapping]]:
    """Return (ordinal, CodeMapping) for ancestor-based broader mappings."""
    grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for ordinal, ref in enumerate(ordered):
        grouped[ref.source].append((ordinal, ref.code))

    results: list[tuple[int, CodeMapping]] = []

    for source, code_ordinals in grouped.items():
        deduped_codes = list(dict.fromkeys(code for _ord, code in code_ordinals))
        code_to_ordinal: dict[str, list[int]] = defaultdict(list)
        for ord_val, code in code_ordinals:
            code_to_ordinal[code].append(ord_val)

        # Source display lookup
        display = _source_display_lookup(con, source, deduped_codes)

        # Walk ancestors via shared UMLS parent-walk primitive
        with _temp_codes(con, deduped_codes) as temp:
            # CR-031: per-source closure coverage gate — fall back to the
            # walk_edges recursive CTE when the closure table has no rows
            # for this source (stale/partial prepared DB).
            closure_table = walk_closure_table(con, max_depth, source)
            if closure_table:
                rows = con.execute(
                    f"""
                    SELECT DISTINCT from_code, to_code, to_cui, to_aui, depth
                    FROM (
                        SELECT c.from_code, c.to_code, c.to_cui, c.to_aui, c.depth,
                               ROW_NUMBER() OVER (
                                   PARTITION BY c.from_code, c.to_code
                                   ORDER BY c.depth
                               ) AS rn
                        FROM {closure_table} c
                        WHERE c.source = ?
                          AND c.depth <= ?
                          AND c.from_code IN (SELECT code FROM {temp})
                    )
                    WHERE rn = 1
                    ORDER BY from_code, depth, to_code
                    """,
                    [source, max_depth],
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    WITH RECURSIVE
                    seed AS (
                        SELECT we.from_code, we.to_code, we.to_cui, we.to_aui,
                               1 AS depth,
                               we.from_code || '>' || we.to_code AS path
                        FROM mt4ds.walk_edges we
                        WHERE we.source = ?
                          AND we.direction = 'parent'
                          AND we.from_code IN (SELECT code FROM {temp})

                        UNION ALL

                        SELECT w.from_code, we.to_code, we.to_cui, we.to_aui,
                               w.depth + 1,
                               w.path || '>' || we.to_code AS path
                        FROM seed w
                        JOIN mt4ds.walk_edges we
                          ON we.source = ?
                          AND we.direction = 'parent'
                          AND we.from_code = w.to_code
                        WHERE w.depth < ?
                          AND strpos('>' || w.path || '>', '>' || we.to_code || '>') = 0
                    )
                    SELECT DISTINCT from_code, to_code, to_cui, to_aui, depth
                    FROM (
                        SELECT from_code, to_code, to_cui, to_aui, depth,
                               ROW_NUMBER() OVER (
                                   PARTITION BY from_code, to_code
                                   ORDER BY depth
                               ) AS rn
                        FROM seed
                    )
                    WHERE rn = 1
                    ORDER BY from_code, depth, to_code
                    """,
                    [source, source, max_depth],
                ).fetchall()

        if not rows:
            continue

        # Collect unique ancestor codes
        ancestor_codes = list(dict.fromkeys(to_code for _fc, to_code, _tc, _ta, _d in rows))
        ancestor_display = _source_display_lookup(con, source, ancestor_codes)

        # Same-CUI mappings from ancestor codes to target sources
        with _temp_codes(con, ancestor_codes) as temp:
            edge_table, match_filter = _same_cui_crosswalk_sql(con)
            if target_sources:
                target_rows = con.execute(
                    f"""
                    SELECT sce.code, sce.cui,
                           sce.target_source, sce.target_code,
                           sce.target_aui, sce.target_cui, sce.target_tty
                    FROM {edge_table} sce
                    WHERE sce.source = ?
                      AND sce.code IN (SELECT code FROM {temp})
                      AND sce.target_source IN (SELECT unnest(?))
                      {match_filter}
                    """,
                    [source, list(target_sources)],
                ).fetchall()
            else:
                target_rows = con.execute(
                    f"""
                    SELECT sce.code, sce.cui,
                           sce.target_source, sce.target_code,
                           sce.target_aui, sce.target_cui, sce.target_tty
                    FROM {edge_table} sce
                    WHERE sce.source = ?
                      AND sce.code IN (SELECT code FROM {temp})
                      {match_filter}
                    """,
                    [source],
                ).fetchall()

        # Build ancestor -> target mapping
        ancestor_targets: dict[str, list[tuple[str, str, str, str, str, str]]] = defaultdict(list)
        for anc_code, anc_cui, tgt_source, tgt_code, tgt_aui, tgt_cui, tgt_tty in target_rows:
            ancestor_targets[anc_code].append((tgt_source, tgt_code, tgt_aui, tgt_cui, tgt_tty, anc_cui))

        # Build target display lookup
        all_target_codes: dict[str, list[str]] = defaultdict(list)
        for _anc_code, targets in ancestor_targets.items():
            for ts, tc, _ta, _tci, _tt, _ac in targets:
                all_target_codes[ts].append(tc)
        target_names: dict[tuple[str, str], str | None] = {}
        for ts, tc_list in all_target_codes.items():
            deduped_tc = list(dict.fromkeys(tc_list))
            tl = _source_display_lookup(con, ts, deduped_tc)
            for tc, (t_name, _t_cui, _t_aui) in tl.items():
                target_names[(ts, tc)] = t_name

        # Build ancestor display lookup for provenance
        ancestor_names: dict[str, str | None] = {}
        for code, (name, _cui, _aui) in ancestor_display.items():
            ancestor_names[code] = name

        # Produce mappings
        for from_code, to_code, to_cui, to_aui, depth in rows:
            src_info = display.get(from_code)
            src_display = src_info[0] if src_info else None
            src_cui = src_info[1] if src_info else None
            src_aui = src_info[2] if src_info else None
            anc_display = ancestor_names.get(to_code)

            for tgt_source, tgt_code, tgt_aui, tgt_cui, tgt_tty, anc_cui in ancestor_targets.get(to_code, []):
                tgt_display_name = target_names.get((tgt_source, tgt_code))
                for ordinal in code_to_ordinal.get(from_code, []):
                    results.append((
                        ordinal,
                        CodeMapping(
                            source=CodeRef(source=source, code=from_code),
                            target=CodeRef(source=tgt_source, code=tgt_code),
                            relationship="source-is-narrower-than-target",
                            match_type="source_ancestor_same_cui",
                            match_depth=int(depth),
                            source_display=src_display,
                            target_display=tgt_display_name,
                            source_cui=src_cui,
                            target_cui=tgt_cui,
                            source_aui=src_aui,
                            target_aui=tgt_aui,
                            target_tty=tgt_tty,
                            matched_via=Provenance.from_steps(
                                "source_ancestor_same_cui",
                                [
                                    ProvenanceStep(
                                        op="input_atom",
                                        source=source,
                                        code=from_code,
                                        cui=src_cui,
                                        aui=src_aui,
                                        name=src_display,
                                    ),
                                    ProvenanceStep(
                                        op="source_ancestor",
                                        source=source,
                                        code=to_code,
                                        cui=to_cui,
                                        aui=to_aui,
                                        depth=int(depth),
                                        name=anc_display,
                                    ),
                                    ProvenanceStep(
                                        op="same_cui",
                                        source=source,
                                        code=to_code,
                                        target_source=tgt_source,
                                        target_code=tgt_code,
                                        cui=anc_cui,
                                    ),
                                ],
                            ),
                        ),
                    ))

    return results


# ---------------------------------------------------------------------------
# Internal: ordering
# ---------------------------------------------------------------------------

def _ordered_results(
    ordered: Sequence[CodeRef],
    rows: list[tuple[int, CodeMapping]],
) -> list[CodeMapping]:
    """Sort mappings by (ordinal, match_depth, target.source, target.code)."""
    return [
        mapping
        for _ordinal, mapping in sorted(
            rows,
            key=lambda item: (
                item[0],
                item[1].match_depth,
                item[1].target.source,
                item[1].target.code,
            ),
        )
    ]
