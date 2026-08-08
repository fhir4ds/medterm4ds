"""Walk + same-CUI crosswalk primitive (general-purpose).

Phase 1b of the canonical_anchors improvement plan (see
``canonical_anchors/docs/improvement_plan.md`` section 4.3).

``find_via_walk`` generalizes the walk-up + same-CUI pattern previously
embedded inline in ``patient_friendly_prepared._snomed_fallback`` and the
ad-hoc 1-level walk-up SQL in ``build_snomed_*_crosswalk`` builders. It
supports four directions via a single ``direction`` parameter:

* ``"self"`` — direct same-CUI crosswalk (depth 0).
* ``"up"``   — walk ancestors, crosswalk at each depth.
* ``"down"`` — walk descendants, crosswalk at each depth.
* ``"both"`` — both directions.

The ``relationship`` parameter controls which RELA to traverse. ``"isa"``
uses the prepared ``mt4ds.walk_edges`` closure path. Non-ISA relationships
(``"component_of"``, ``"has_active_ingredient"``, ``"active_ingredient_of"``,
``"class_of"``, etc.) fall back to a raw ``mrrel`` + ``mrconso`` join —
see ``canonical_anchors/.temp/phase_1c_investigation.md`` for the rationale.

Per architecture decision 4.3, depth ≥3 matches are NOT auto-returned (the
caller must use a manual curated connection via lookup for those).

Each returned ``CodeMapping`` carries a ``match_depth`` field:
* 0 = direct same-CUI crosswalk
* 1 = parent/child
* 2 = grandparent/grandchild
"""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from typing import Literal

from medterm4ds.core.models import CodeMapping, CodeRef
from medterm4ds.services.prepared_primitives import (
    dedupe_values,
    group_codes_by_source,
    same_cui_crosswalk_sql,
    temp_codes,
)

Direction = Literal["self", "up", "down", "both"]

# Architecture decision 4.3: depth ≥3 not auto-returned.
_MAX_AUTO_DEPTH = 2

# Relationships handled via prepared walk_edges (ISA-only). Anything else
# falls through to raw mrrel.
_ISA_RELATIONSHIPS = frozenset({"isa", "inverse_isa"})


def find_via_walk(
    codes: Sequence[CodeRef],
    con,
    *,
    target_sources: Sequence[str],
    direction: Direction = "up",
    relationship: str | Sequence[str] = "isa",
    max_depth: int = 2,
) -> list[CodeMapping]:
    """Find codes in ``target_sources`` by walking within source vocabulary,
    crosswalking via same-CUI at each visited node.

    Parameters
    ----------
    codes:
        Input codes to walk from.
    con:
        DuckDB connection with prepared ``mt4ds`` tables and (for non-ISA
        relationships) raw ``mrrel`` / ``mrconso`` available.
    target_sources:
        Vocabulary labels of the crosswalk targets (e.g. ``["SNOMEDCT_US"]``).
    direction:
        ``"self"`` (no walk, direct same-CUI), ``"up"`` (ancestors),
        ``"down"`` (descendants), or ``"both"``.
    relationship:
        RELA filter — ``"isa"`` (default), ``"component_of"``,
        ``"has_active_ingredient"``, ``"active_ingredient_of"``,
        ``"class_of"``, etc. Non-ISA triggers raw-mrrel fallback (see module
        docstring).
    max_depth:
        Maximum traversal depth. ``0`` means ``"self"`` only. Capped at
        ``_MAX_AUTO_DEPTH`` (2) per architecture decision 4.3 — callers
        requesting deeper walks get a ``ValueError`` rather than silently
        truncated results.

    Returns
    -------
    list[CodeMapping]
        Crosswalk mappings with ``match_depth`` set (0=direct, 1=parent/child,
        2=grandparent/grandchild). Deduplicated by
        ``(source, target, match_depth)`` so shallower matches win.

    Raises
    ------
    ValueError
        If ``max_depth`` is negative, exceeds ``_MAX_AUTO_DEPTH``, or
        ``direction`` is invalid.
    """
    if not codes:
        return []
    if max_depth < 0:
        raise ValueError(f"max_depth must be non-negative, got {max_depth}")
    if max_depth > _MAX_AUTO_DEPTH:
        raise ValueError(
            f"max_depth={max_depth} exceeds auto-return cap "
            f"({_MAX_AUTO_DEPTH}); per architecture decision 4.3, depth ≥3 "
            f"matches require manual curated connection. Lower max_depth to "
            f"≤ {_MAX_AUTO_DEPTH}."
        )
    if direction not in ("self", "up", "down", "both"):
        raise ValueError(
            f"direction must be one of 'self', 'up', 'down', 'both'; got {direction!r}"
        )

    relationships = (
        [relationship] if isinstance(relationship, str) else list(relationship)
    )
    use_isa_path = all(r in _ISA_RELATIONSHIPS for r in relationships)

    # "self" alone is direct crosswalk regardless of relationship filter.
    direct_mappings = _direct_same_cui(codes, con, target_sources)

    if direction == "self" or max_depth == 0:
        return direct_mappings

    # Walk to collect source codes at each depth (1..max_depth).
    walked: list[tuple[CodeRef, CodeRef, int]] = []  # (origin, walked, depth)
    if direction in ("up", "both"):
        walked.extend(_walk_depths(codes, con, upward=True, max_depth=max_depth,
                                   relationships=relationships, use_isa_path=use_isa_path))
    if direction in ("down", "both"):
        walked.extend(_walk_depths(codes, con, upward=False, max_depth=max_depth,
                                   relationships=relationships, use_isa_path=use_isa_path))

    if not walked:
        return direct_mappings

    # Crosswalk each walked source code via same-CUI, then attribute depth.
    walked_codes_by_source: dict[str, list[str]] = defaultdict(list)
    depth_index: dict[tuple[str, str], int] = {}  # (source, code) -> shallowest depth
    origin_index: dict[tuple[str, str], CodeRef] = {}
    for origin, walked_ref, depth in walked:
        key = (walked_ref.source, walked_ref.code)
        walked_codes_by_source[walked_ref.source].append(walked_ref.code)
        if key not in depth_index or depth < depth_index[key]:
            depth_index[key] = depth
            origin_index[key] = origin

    walked_codes = [
        CodeRef(source=src, code=code)
        for src, codes_list in walked_codes_by_source.items()
        for code in dedupe_values(codes_list)
    ]
    walked_mappings = _direct_same_cui(walked_codes, con, target_sources)

    # When target_sources includes the source vocabulary itself, walked codes
    # are valid same-source targets (e.g. SNOMED substance → SNOMED product
    # via has_active_ingredient). The walk is the crosswalk in this case.
    same_source_mappings = _same_source_walked_mappings(
        walked_codes=walked_codes,
        depth_index=depth_index,
        origin_index=origin_index,
        target_sources=target_sources,
        upward=direction in ("up", "both"),
    )

    # Annotate walked mappings with depth and origin.
    walked_results: list[CodeMapping] = []
    for m in walked_mappings:
        key = (m.source.source, m.source.code)
        depth = depth_index.get(key, 1)
        origin = origin_index.get(key, m.source)
        walked_results.append(
            CodeMapping(
                source=origin,
                target=m.target,
                relationship=_depth_relationship(depth, upward=direction in ("up", "both")),
                match_type=_depth_match_type(depth, upward=direction in ("up", "both")),
                match_depth=depth,
                source_display=m.source_display,
                target_display=m.target_display,
                source_cui=m.source_cui,
                target_cui=m.target_cui,
                source_aui=m.source_aui,
                target_aui=m.target_aui,
                target_tty=m.target_tty,
            )
        )

    # Deduplicate: prefer shallower matches. Same (source, target) pair at
    # different depths keeps only the shallowest.
    return _merge_results(direct_mappings, [*walked_results, *same_source_mappings])


# ---------------------------------------------------------------------------
# Walk dispatch
# ---------------------------------------------------------------------------

def _walk_depths(
    codes: Sequence[CodeRef],
    con,
    *,
    upward: bool,
    max_depth: int,
    relationships: Sequence[str],
    use_isa_path: bool,
) -> list[tuple[CodeRef, CodeRef, int]]:
    """Return [(origin_code, walked_code, depth)] for codes visited 1..max_depth.

    The walked_code at depth N excludes the origin itself and any code already
    visited at a shallower depth (so each (origin, walked) pair appears once,
    at the shallowest depth).
    """
    results: list[tuple[CodeRef, CodeRef, int]] = []
    for source, source_codes in group_codes_by_source(codes).items():
        seeds = dedupe_values(source_codes)
        if use_isa_path:
            edges_at_depth = _isa_walk(source, seeds, con, upward=upward, max_depth=max_depth)
        else:
            edges_at_depth = _mrrel_walk(source, seeds, con, upward=upward,
                                         max_depth=max_depth, relationships=relationships)
        for origin_code, walked_code, depth in edges_at_depth:
            results.append((
                CodeRef(source=source, code=origin_code),
                CodeRef(source=source, code=walked_code),
                depth,
            ))
    return results


def _isa_walk(
    source: str,
    seeds: Sequence[str],
    con,
    *,
    upward: bool,
    max_depth: int,
) -> list[tuple[str, str, int]]:
    """BFS over mt4ds.walk_edges, tracking origin across depths."""
    seed_set = set(seeds)
    visited: set[str] = set(seeds)
    # Track which seeds reached each code (for multi-origin attribution).
    # For simplicity, attribute to first seed encountered.
    origin_for_code: dict[str, str] = {}
    queue: deque[tuple[str, str, int]] = deque((seed, seed, 0) for seed in seeds)
    out: list[tuple[str, str, int]] = []

    while queue:
        batch: list[tuple[str, str, int]] = []
        while queue and len(batch) < 500:
            batch.append(queue.popleft())
        if not batch:
            break

        codes_at_origin = defaultdict(list)  # depth -> list of (origin, current_code)
        for origin, current_code, depth in batch:
            codes_at_origin[depth].append((origin, current_code))

        for depth, items in codes_at_origin.items():
            if depth >= max_depth:
                continue
            codes_to_expand = dedupe_values([c for _, c in items])
            with temp_codes(con, codes_to_expand, prefix="_mt4ds_walk_origin") as temp:
                if upward:
                    rows = con.execute(
                        f"""
                        SELECT we.from_code, we.to_code
                        FROM mt4ds.walk_edges we
                        WHERE we.source = ?
                          AND we.direction = 'parent'
                          AND we.from_code IN (SELECT code FROM {temp})
                        """,
                        [source],
                    ).fetchall()
                else:
                    rows = con.execute(
                        f"""
                        SELECT we.from_code, we.to_code
                        FROM mt4ds.walk_edges we
                        WHERE we.source = ?
                          AND we.direction = 'parent'
                          AND we.to_code IN (SELECT code FROM {temp})
                        """,
                        [source],
                    ).fetchall()

            next_depth = depth + 1
            # Map current_code -> origin (re-derive from batch items)
            code_to_origin = {c: o for o, c in items}

            for from_code, to_code in rows:
                if upward:
                    current_code, walked_code = from_code, to_code
                else:
                    current_code, walked_code = to_code, from_code
                if current_code not in code_to_origin:
                    continue
                origin = code_to_origin[current_code]
                if walked_code in seed_set and origin != walked_code:
                    # Don't re-walk back to a seed we started from.
                    pass
                if walked_code == origin:
                    continue
                out.append((origin, walked_code, next_depth))
                if walked_code not in visited:
                    visited.add(walked_code)
                    queue.append((origin, walked_code, next_depth))
    return out


def _mrrel_walk(
    source: str,
    seeds: Sequence[str],
    con,
    *,
    upward: bool,
    max_depth: int,
    relationships: Sequence[str],
) -> list[tuple[str, str, int]]:
    """BFS over raw mrrel + mrconso for non-ISA relationships.

    Directionality:
    * ``upward=True``: walk in the direction that "narrows" the concept's
      context. For ``component_of`` (test → component), upward from a test
      reaches its component. For ``has_active_ingredient`` (substance →
      product per mrrel AUI1→AUI2), upward from a product reaches its
      substance.
    * ``upward=False``: the inverse direction.

    The mapping is RELA-aware: for each RELA we know which AUI side is the
    "narrower" concept. See ``_rela_orientation``.
    """
    seed_set = set(seeds)
    visited: set[str] = set(seeds)
    queue: deque[tuple[str, str, int]] = deque((seed, seed, 0) for seed in seeds)
    out: list[tuple[str, str, int]] = []

    while queue:
        batch: list[tuple[str, str, int]] = []
        while queue and len(batch) < 500:
            batch.append(queue.popleft())
        if not batch:
            break

        codes_at_depth: dict[int, list[tuple[str, str]]] = defaultdict(list)
        for origin, current_code, depth in batch:
            codes_at_depth[depth].append((origin, current_code))

        for depth, items in codes_at_depth.items():
            if depth >= max_depth:
                continue
            codes_to_expand = dedupe_values([c for _, c in items])
            with temp_codes(con, codes_to_expand, prefix="_mt4ds_mrrel_walk") as temp:
                rows = _query_mrrel_step(
                    con=con,
                    source=source,
                    codes_temp=temp,
                    relationships=relationships,
                    upward=upward,
                )

            next_depth = depth + 1
            code_to_origin = {c: o for o, c in items}

            for from_code, to_code in rows:
                if from_code not in code_to_origin:
                    continue
                origin = code_to_origin[from_code]
                if to_code == origin:
                    continue
                out.append((origin, to_code, next_depth))
                if to_code not in visited:
                    visited.add(to_code)
                    queue.append((origin, to_code, next_depth))
    return out


def _query_mrrel_step(
    con,
    *,
    source: str,
    codes_temp: str,
    relationships: Sequence[str],
    upward: bool,
) -> list[tuple[str, str]]:
    """One mrrel step: return [(from_code, to_code)] for the given seeds.

    ``from_code`` is the seed-side code, ``to_code`` is the walked-to code.
    Directionality depends on RELA orientation and ``upward``.

    Convention: ``upward=False`` ("down") follows the RELA in its natural
    stored direction (AUI1→AUI2 for most RELAs); ``upward=True`` ("up")
    follows the reverse.
    """
    parts: list[str] = []
    for rela in relationships:
        aui1_is_seed_down = _rela_aui1_is_seed_down(rela)
        if not upward:
            # "down": natural direction. seed=AUI1 if aui1_is_seed_down else AUI2.
            seed_aui_col = "r.AUI1" if aui1_is_seed_down else "r.AUI2"
            walked_aui_col = "r.AUI2" if aui1_is_seed_down else "r.AUI1"
        else:
            # "up": reverse direction.
            seed_aui_col = "r.AUI2" if aui1_is_seed_down else "r.AUI1"
            walked_aui_col = "r.AUI1" if aui1_is_seed_down else "r.AUI2"
        parts.append(
            f"""
            SELECT DISTINCT
              seed.CODE AS from_code,
              walked.CODE AS to_code
            FROM mrrel r
            JOIN mrconso seed ON seed.AUI = {seed_aui_col}
            JOIN mrconso walked ON walked.AUI = {walked_aui_col}
            JOIN {codes_temp} t ON t.code = seed.CODE
            WHERE r.RELA = '{rela}'
              AND seed.SAB = '{source}' AND walked.SAB = '{source}'
              AND seed.SUPPRESS = 'N' AND walked.SUPPRESS = 'N'
              AND seed.CODE != walked.CODE
            """
        )

    union_sql = " UNION ".join(parts)
    rows = con.execute(union_sql).fetchall()
    return [(str(a), str(b)) for a, b in rows]


def _rela_aui1_is_seed_down(rela: str) -> bool:
    """Return True if walking "down" (expansion) means AUI1 → AUI2.

    Each non-ISA RELA in UMLS mrrel has a fixed AUI1/AUI2 orientation. We
    define "down" as following the relationship in its natural stored
    direction (the direction most useful for expansion in canonical_anchors
    use cases). Callers can flip with ``direction="up"`` to traverse reverse.

    * ``component_of``: AUI1=test, AUI2=LP component. Walking "down" from a
      test reaches its component (test → component). TRUE for test-side seed.
      (Note: callers walking down from an LP to its tests should use the
      inverse direction, since LP is the AUI2 side.)
    * ``has_active_ingredient``: AUI1=substance, AUI2=product. Walking "down"
      from a substance reaches its products (per plan section 4.6). TRUE.
    * ``class_of``: AUI1=test, AUI2=class. Walking "down" from a test reaches
      its class. TRUE.
    * ``inverse_isa``: AUI1=child, AUI2=parent in UMLS storage. TRUE.

    Inverse relationships (``active_ingredient_of``, ``has_component``,
    ``has_class``, ``isa``) return False — walking "down" follows AUI2→AUI1.

    For unknown RELAs, default to True (assume AUI1 → AUI2 is the natural
    expansion direction) and document the assumption.
    """
    return rela not in {
        "active_ingredient_of",
        "has_component",
        "has_class",
        "isa",
    }


# ---------------------------------------------------------------------------
# Crosswalk + result merging
# ---------------------------------------------------------------------------

def _direct_same_cui(
    codes: Sequence[CodeRef],
    con,
    target_sources: Sequence[str],
) -> list[CodeMapping]:
    """Delegate to services.crosswalk.get_same_cui_mappings (avoid import cycle)."""
    from medterm4ds.services.crosswalk import get_same_cui_mappings

    return get_same_cui_mappings(codes, con, target_sources=target_sources)


def _same_source_walked_mappings(
    *,
    walked_codes: Sequence[CodeRef],
    depth_index: dict[tuple[str, str], int],
    origin_index: dict[tuple[str, str], CodeRef],
    target_sources: Sequence[str],
    upward: bool,
) -> list[CodeMapping]:
    """Return same-source mappings for walked codes when the source vocabulary
    is in ``target_sources``.

    This handles cases like SNOMED substance → SNOMED product via
    ``has_active_ingredient``: the walked code is a valid target within the
    same vocabulary, even though there's no same-CUI edge to follow.
    """
    target_set = set(target_sources)
    out: list[CodeMapping] = []
    for walked_ref in walked_codes:
        if walked_ref.source not in target_set:
            continue
        key = (walked_ref.source, walked_ref.code)
        depth = depth_index.get(key)
        if depth is None:
            continue
        origin = origin_index.get(key)
        if origin is None or origin == walked_ref:
            continue
        out.append(
            CodeMapping(
                source=origin,
                target=walked_ref,
                relationship=_depth_relationship(depth, upward=upward),
                match_type=_depth_match_type_walk_same_source(depth, upward=upward),
                match_depth=depth,
            )
        )
    return out


def _depth_match_type_walk_same_source(depth: int, *, upward: bool) -> str:
    """Match type label for same-source walked targets (no same-CUI edge)."""
    if depth == 0:
        return "same_source"
    return "source_ancestor" if upward else "source_descendant"


def _depth_relationship(depth: int, *, upward: bool) -> str:
    if depth == 0:
        return "equivalent"
    return "source-is-narrower-than-target" if upward else "source-is-broader-than-target"


def _depth_match_type(depth: int, *, upward: bool) -> str:
    if depth == 0:
        return "same_cui"
    return "source_ancestor_same_cui" if upward else "source_descendant_same_cui"


def _merge_results(
    direct: list[CodeMapping],
    walked: list[CodeMapping],
) -> list[CodeMapping]:
    """Merge direct + walked mappings; dedupe by (source, target), keep shallowest.

    Note: when a code appears in both direct (depth=0) and walked (depth>0)
    sets, the direct match wins because it has lower depth. This matches
    services.crosswalk_prepared.get_crosswalk_mappings semantics.
    """
    seen: dict[tuple[str, str, str, str], CodeMapping] = {}
    for m in [*direct, *walked]:
        # Key includes source+target so the same target reached from two
        # different sources stays distinct.
        key = (m.source.source, m.source.code, m.target.source, m.target.code)
        existing = seen.get(key)
        if existing is None or m.match_depth < existing.match_depth:
            seen[key] = m
    return list(seen.values())
