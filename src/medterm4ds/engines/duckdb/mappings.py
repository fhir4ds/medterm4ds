"""Mapping subsystem for the local DuckDB engine.

Extracted from engines/duckdb/engine.py (Phase 3 of Tier C refactor). These
functions handle source-to-target code mappings: same-CUI crosswalk, ancestor
mappings via hierarchy walk, target-hierarchy expansion, and SNOMED/CPT
target routing.

Functions take the engine instance as their first parameter to access the
DuckDB connection and helpers. Same pattern as engines/duckdb/hierarchy.py.
Engine module-level helpers are accessed via function-level late imports to
avoid circular dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from medterm4ds.core.models import CodeMapping, CodeRef, Provenance, ProvenanceStep


def _filter_snomed_top_level_mappings(
    engine,
    rows: list[tuple[int, CodeMapping]],
) -> list[tuple[int, CodeMapping]]:
    """Suppress broad non-exact SNOMED targets when the derived depth table exists."""
    from medterm4ds.engines.duckdb.engine import (
        _SNOMED_TOP_LEVEL_GUARD_DEPTH,
        _SNOMED_TOP_LEVEL_GUARD_EXEMPT_MATCH_TYPES,
    )
    snomed_codes = sorted(
        {
            mapping.target.code
            for _ordinal, mapping in rows
            if mapping.target.source == "SNOMEDCT_US"
            and mapping.match_type not in _SNOMED_TOP_LEVEL_GUARD_EXEMPT_MATCH_TYPES
        }
    )
    if not snomed_codes:
        return rows
    depth_lookup = engine._snomed_top_level_depths(snomed_codes)
    if not depth_lookup:
        return rows
    return [
        (ordinal, mapping)
        for ordinal, mapping in rows
        if not (
            mapping.target.source == "SNOMEDCT_US"
            and mapping.match_type not in _SNOMED_TOP_LEVEL_GUARD_EXEMPT_MATCH_TYPES
            and depth_lookup.get(mapping.target.code, 999) <= _SNOMED_TOP_LEVEL_GUARD_DEPTH
        )
    ]

def _get_source_code_mappings(
    engine,
    source: str,
    code_ordinals: Sequence[tuple[int, str]],
    *,
    target_sources: Sequence[str],
    max_results_per_code: int,
) -> list[tuple[int, CodeMapping]]:
    if (
        engine._table_exists("best_atoms")
        and (
            engine._table_exists("crosswalk_edges")
            or engine._table_exists("same_cui_edges")
        )
    ):
        return engine._get_source_code_mappings_prepared(
            source,
            code_ordinals,
            target_sources=target_sources,
            max_results_per_code=max_results_per_code,
        )

    target_placeholders = ",".join(["?"] * len(target_sources))
    with engine._temp_code_ordinals(code_ordinals) as temp:
        rows = engine.con.execute(
            f"""
            WITH
            source_atoms AS (
                SELECT i.ordinal, i.code AS source_code, c.STR AS source_name,
                       c.CUI AS source_cui, c.AUI AS source_aui,
                       CASE c.TTY
                           WHEN 'PT' THEN 0
                           WHEN 'MH' THEN 1
                           WHEN 'LN' THEN 2
                           ELSE 3
                       END AS source_atom_rank
                FROM {temp} i
                JOIN mrconso c ON c.CODE = i.code
                WHERE c.SAB = ?
                  AND c.SUPPRESS = 'N'
            ),
            source_seed AS (
                SELECT ordinal, source_code, source_name, source_cui, source_aui,
                       source_atom_rank
                FROM source_atoms
            ),
            target_ranked AS (
                SELECT s.ordinal, s.source_code, s.source_name, s.source_cui,
                       s.source_aui, t.SAB AS target_source, t.CODE AS target_code,
                       t.STR AS target_name, t.CUI AS target_cui, t.AUI AS target_aui,
                       t.TTY AS target_tty,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.ordinal, t.SAB, t.CODE
                           ORDER BY
                               s.source_atom_rank,
                               s.source_aui,
                               CASE t.TTY
                                   WHEN 'PT' THEN 0
                                   WHEN 'MH' THEN 1
                                   WHEN 'LN' THEN 2
                                   ELSE 3
                               END,
                               t.AUI
                       ) AS atom_rn
                FROM source_seed s
                JOIN mrconso t ON t.CUI = s.source_cui
                WHERE t.SAB IN ({target_placeholders})
                  AND t.SUPPRESS = 'N'
            ),
            deduped_targets AS (
                SELECT *
                FROM target_ranked
                WHERE atom_rn = 1
            ),
            capped_targets AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ordinal
                           ORDER BY target_source, target_code, target_aui
                       ) AS result_rn
                FROM deduped_targets
            )
            SELECT ordinal, source_code, source_name, source_cui, source_aui,
                   target_source, target_code, target_name, target_cui,
                   target_aui, target_tty
            FROM capped_targets
            WHERE result_rn <= ?
            ORDER BY ordinal, target_source, target_code, target_aui
            """,
            [source, *target_sources, max_results_per_code],
        ).fetchall()

    return [
        (
            int(ordinal),
            CodeMapping(
                source=CodeRef(source=source, code=source_code),
                target=CodeRef(source=target_source, code=target_code),
                source_display=source_name,
                target_display=target_name,
                relationship="equivalent",
                match_type="same_cui",
                match_depth=0,
                source_cui=source_cui,
                target_cui=target_cui,
                source_aui=source_aui,
                target_aui=target_aui,
                target_tty=target_tty,
                matched_via=Provenance.from_steps(
                    "same_cui",
                    [
                        ProvenanceStep(
                            op="input_atom",
                            source=source,
                            code=source_code,
                            cui=source_cui,
                            aui=source_aui,
                            name=source_name,
                        ),
                        ProvenanceStep(
                            op="same_cui",
                            source=source,
                            code=source_code,
                            target_source=target_source,
                            target_code=target_code,
                            cui=source_cui,
                        ),
                        ProvenanceStep(
                            op="target_atom",
                            source=target_source,
                            code=target_code,
                            cui=target_cui,
                            aui=target_aui,
                            tty=target_tty,
                            name=target_name,
                        ),
                    ],
                ),
            ),
        )
        for (
            ordinal,
            source_code,
            source_name,
            source_cui,
            source_aui,
            target_source,
            target_code,
            target_name,
            target_cui,
            target_aui,
            target_tty,
        ) in rows
    ]

def _get_source_code_mappings_prepared(
    engine,
    source: str,
    code_ordinals: Sequence[tuple[int, str]],
    *,
    target_sources: Sequence[str],
    max_results_per_code: int,
) -> list[tuple[int, CodeMapping]]:
    from medterm4ds.engines.duckdb.engine import _dedupe
    from medterm4ds.services.crosswalk_prepared import get_crosswalk_mappings

    unique_codes = _dedupe([code for _ordinal, code in code_ordinals])
    prepared_mappings = [
        mapping
        for mapping in get_crosswalk_mappings(
            [CodeRef(source=source, code=code) for code in unique_codes],
            engine.con,
            target_sources=target_sources,
            max_depth=0,
        )
        if mapping.match_type == "same_cui"
    ]
    mappings_by_code: dict[str, list[CodeMapping]] = defaultdict(list)
    for mapping in prepared_mappings:
        mappings_by_code[mapping.source.code].append(mapping)

    rows: list[tuple[int, CodeMapping]] = []
    for ordinal, code in code_ordinals:
        for mapping in mappings_by_code.get(code, [])[:max_results_per_code]:
            rows.append((int(ordinal), mapping))
    return rows

def _get_source_ancestor_mappings(
    engine,
    source: str,
    code_ordinals: Sequence[tuple[int, str]],
    *,
    target_sources: Sequence[str],
    max_results_per_code: int,
    max_depth: int,
) -> list[tuple[int, CodeMapping]]:
    from medterm4ds.engines.duckdb.engine import _source_hierarchy_join_sql
    if (
        (
            engine._table_exists("crosswalk_edges")
            or engine._table_exists("same_cui_edges")
        )
        and engine._table_exists("best_atoms")
        and engine._table_exists("walk_edges")
    ):
        return engine._get_source_ancestor_mappings_prepared(
            source,
            code_ordinals,
            target_sources=target_sources,
            max_results_per_code=max_results_per_code,
            max_depth=max_depth,
        )

    target_placeholders = ",".join(["?"] * len(target_sources))
    source_join, source_target = _source_hierarchy_join_sql(
        source,
        "s.source_aui",
        upward=True,
    )
    recursive_join, recursive_target = _source_hierarchy_join_sql(
        source,
        "w.ancestor_aui",
        upward=True,
    )
    with engine._temp_code_ordinals(code_ordinals) as temp:
        rows = engine.con.execute(
            f"""
            WITH RECURSIVE
            source_atoms AS (
                SELECT i.ordinal, i.code AS source_code, c.STR AS source_name,
                       c.CUI AS source_cui, c.AUI AS source_aui,
                       CASE c.TTY
                           WHEN 'PT' THEN 0
                           WHEN 'MH' THEN 1
                           WHEN 'LN' THEN 2
                           ELSE 3
                       END AS source_atom_rank
                FROM {temp} i
                JOIN mrconso c ON c.CODE = i.code
                WHERE c.SAB = ?
                  AND c.SUPPRESS = 'N'
            ),
            source_seed AS (
                SELECT ordinal, source_code, source_name, source_cui, source_aui,
                       source_atom_rank
                FROM source_atoms
            ),
            exact_target_sources AS (
                SELECT DISTINCT s.ordinal, t.SAB AS target_source
                FROM source_seed s
                JOIN mrconso t ON t.CUI = s.source_cui
                WHERE t.SAB IN ({target_placeholders})
                  AND t.SUPPRESS = 'N'
            ),
            source_walk AS (
                SELECT s.ordinal, s.source_code, s.source_name, s.source_cui,
                       s.source_aui, p.CODE AS ancestor_code,
                       p.STR AS ancestor_name, p.CUI AS ancestor_cui,
                       p.AUI AS ancestor_aui, 1 AS source_depth,
                       s.source_atom_rank,
                       s.source_aui || '>' || p.AUI AS path
                FROM source_seed s
                JOIN mrrel r ON {source_join}
                JOIN mrconso p ON p.AUI = {source_target}
                WHERE p.SAB = ?
                  AND p.SUPPRESS = 'N'

                UNION ALL

                SELECT w.ordinal, w.source_code, w.source_name, w.source_cui,
                       w.source_aui, p.CODE AS ancestor_code,
                       p.STR AS ancestor_name, p.CUI AS ancestor_cui,
                       p.AUI AS ancestor_aui, w.source_depth + 1 AS source_depth,
                       w.source_atom_rank,
                       w.path || '>' || p.AUI AS path
                FROM source_walk w
                JOIN mrrel r ON {recursive_join}
                JOIN mrconso p ON p.AUI = {recursive_target}
                WHERE w.source_depth < ?
                  AND p.SAB = ?
                  AND p.SUPPRESS = 'N'
                  AND strpos('>' || w.path || '>', '>' || p.AUI || '>') = 0
            ),
            target_ranked AS (
                SELECT w.ordinal, w.source_code, w.source_name, w.source_cui,
                       w.source_aui, w.ancestor_code, w.ancestor_name,
                       w.ancestor_cui, w.ancestor_aui, w.source_depth,
                       t.SAB AS target_source, t.CODE AS target_code,
                       t.STR AS target_name, t.CUI AS target_cui,
                       t.AUI AS target_aui, t.TTY AS target_tty,
                       ROW_NUMBER() OVER (
                           PARTITION BY w.ordinal, t.SAB, t.CODE
                           ORDER BY
                               w.source_depth,
                               w.source_atom_rank,
                               w.ancestor_aui,
                               CASE t.TTY
                                   WHEN 'PT' THEN 0
                                   WHEN 'MH' THEN 1
                                   WHEN 'LN' THEN 2
                                   ELSE 3
                               END,
                               t.AUI
                       ) AS atom_rn
                FROM source_walk w
                JOIN mrconso t ON t.CUI = w.ancestor_cui
                WHERE t.SAB IN ({target_placeholders})
                  AND t.SUPPRESS = 'N'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM exact_target_sources e
                      WHERE e.ordinal = w.ordinal
                        AND e.target_source = t.SAB
                  )
            ),
            deduped_targets AS (
                SELECT *
                FROM target_ranked
                WHERE atom_rn = 1
            ),
            capped_targets AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ordinal
                           ORDER BY source_depth, target_source, target_code, target_aui
                       ) AS result_rn
                FROM deduped_targets
            )
            SELECT ordinal, source_code, source_name, source_cui, source_aui,
                   ancestor_code, ancestor_name, ancestor_cui, ancestor_aui,
                   source_depth, target_source, target_code, target_name,
                   target_cui, target_aui, target_tty
            FROM capped_targets
            WHERE result_rn <= ?
            ORDER BY ordinal, source_depth, target_source, target_code, target_aui
            """,
            [
                source,
                *target_sources,
                source,
                max_depth,
                source,
                *target_sources,
                max_results_per_code,
            ],
        ).fetchall()

    return [
        (
            int(ordinal),
            CodeMapping(
                source=CodeRef(source=source, code=source_code),
                target=CodeRef(source=target_source, code=target_code),
                source_display=source_name,
                target_display=target_name,
                relationship="source-is-narrower-than-target",
                match_type="source_ancestor_same_cui",
                match_depth=int(source_depth),
                source_cui=source_cui,
                target_cui=target_cui,
                source_aui=source_aui,
                target_aui=target_aui,
                target_tty=target_tty,
                matched_via=Provenance.from_steps(
                    "source_ancestor_same_cui",
                    [
                        ProvenanceStep(
                            op="input_atom",
                            source=source,
                            code=source_code,
                            cui=source_cui,
                            aui=source_aui,
                            name=source_name,
                        ),
                        ProvenanceStep(
                            op="source_ancestor",
                            source=source,
                            code=ancestor_code,
                            cui=ancestor_cui,
                            aui=ancestor_aui,
                            depth=int(source_depth),
                            name=ancestor_name,
                        ),
                        ProvenanceStep(
                            op="same_cui",
                            source=source,
                            code=ancestor_code,
                            target_source=target_source,
                            target_code=target_code,
                            cui=ancestor_cui,
                        ),
                        ProvenanceStep(
                            op="target_atom",
                            source=target_source,
                            code=target_code,
                            cui=target_cui,
                            aui=target_aui,
                            tty=target_tty,
                            name=target_name,
                        ),
                    ],
                ),
            ),
        )
        for (
            ordinal,
            source_code,
            source_name,
            source_cui,
            source_aui,
            ancestor_code,
            ancestor_name,
            ancestor_cui,
            ancestor_aui,
            source_depth,
            target_source,
            target_code,
            target_name,
            target_cui,
            target_aui,
            target_tty,
        ) in rows
    ]

def _get_source_ancestor_mappings_prepared(
    engine,
    source: str,
    code_ordinals: Sequence[tuple[int, str]],
    *,
    target_sources: Sequence[str],
    max_results_per_code: int,
    max_depth: int,
) -> list[tuple[int, CodeMapping]]:
    from medterm4ds.engines.duckdb.engine import _dedupe
    from medterm4ds.services.crosswalk_prepared import get_crosswalk_mappings

    unique_codes = _dedupe([code for _ordinal, code in code_ordinals])
    prepared_mappings = [
        mapping
        for mapping in get_crosswalk_mappings(
            [CodeRef(source=source, code=code) for code in unique_codes],
            engine.con,
            target_sources=target_sources,
            max_depth=max_depth,
        )
        if mapping.match_type == "source_ancestor_same_cui"
    ]
    mappings_by_code: dict[str, list[CodeMapping]] = defaultdict(list)
    for mapping in prepared_mappings:
        mappings_by_code[mapping.source.code].append(mapping)

    rows: list[tuple[int, CodeMapping]] = []
    for ordinal, code in code_ordinals:
        for mapping in mappings_by_code.get(code, [])[:max_results_per_code]:
            rows.append((int(ordinal), mapping))
    return rows

def _get_target_hierarchy_mappings(
    engine,
    source: str,
    code_ordinals: Sequence[tuple[int, str]],
    *,
    target_sources: Sequence[str],
    max_results_per_code: int,
    max_depth: int,
    upward: bool,
) -> list[tuple[int, CodeMapping]]:
    from medterm4ds.engines.duckdb.engine import _source_hierarchy_join_sql
    if (
        (
            engine._table_exists("crosswalk_edges")
            or engine._table_exists("same_cui_edges")
        )
        and engine._table_exists("best_atoms")
        and engine._table_exists("walk_edges")
    ):
        return engine._get_target_hierarchy_mappings_prepared(
            source,
            code_ordinals,
            target_sources=target_sources,
            max_results_per_code=max_results_per_code,
            max_depth=max_depth,
            upward=upward,
        )

    target_source = target_sources[0] if len(target_sources) == 1 else None
    target_placeholders = ",".join(["?"] * len(target_sources))
    if target_source:
        direct_join, direct_target = _source_hierarchy_join_sql(
            target_source,
            "e.exact_target_aui",
            upward=upward,
        )
        recursive_join, recursive_target = _source_hierarchy_join_sql(
            target_source,
            "w.target_aui",
            upward=upward,
        )
    else:
        direct_join = "r.AUI1 = e.exact_target_aui" if upward else "r.AUI2 = e.exact_target_aui"
        direct_target = "r.AUI2" if upward else "r.AUI1"
        recursive_join = "r.AUI1 = w.target_aui" if upward else "r.AUI2 = w.target_aui"
        recursive_target = "r.AUI2" if upward else "r.AUI1"
    relationship = "source-is-narrower-than-target" if upward else "source-is-broader-than-target"
    match_type = "target_ancestor" if upward else "target_descendant"
    step_op = match_type

    with engine._temp_code_ordinals(code_ordinals) as temp:
        rows = engine.con.execute(
            f"""
            WITH RECURSIVE
            source_atoms AS (
                SELECT i.ordinal, i.code AS source_code, c.STR AS source_name,
                       c.CUI AS source_cui, c.AUI AS source_aui,
                       CASE c.TTY
                           WHEN 'PT' THEN 0
                           WHEN 'MH' THEN 1
                           WHEN 'LN' THEN 2
                           ELSE 3
                       END AS source_atom_rank
                FROM {temp} i
                JOIN mrconso c ON c.CODE = i.code
                WHERE c.SAB = ?
                  AND c.SUPPRESS = 'N'
            ),
            source_seed AS (
                SELECT ordinal, source_code, source_name, source_cui, source_aui,
                       source_atom_rank
                FROM source_atoms
            ),
            exact_targets AS (
                SELECT s.ordinal, s.source_code, s.source_name, s.source_cui,
                       s.source_aui, s.source_atom_rank, t.SAB AS exact_target_source,
                       t.CODE AS exact_target_code, t.STR AS exact_target_name,
                       t.CUI AS exact_target_cui, t.AUI AS exact_target_aui,
                       t.TTY AS exact_target_tty,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.ordinal, t.SAB, t.CODE
                           ORDER BY
                               s.source_atom_rank,
                               s.source_aui,
                               CASE t.TTY
                                   WHEN 'PT' THEN 0
                                   WHEN 'MH' THEN 1
                                   WHEN 'LN' THEN 2
                                   ELSE 3
                               END,
                               t.AUI
                       ) AS atom_rn
                FROM source_seed s
                JOIN mrconso t ON t.CUI = s.source_cui
                WHERE t.SAB IN ({target_placeholders})
                  AND t.SUPPRESS = 'N'
            ),
            exact_seed AS (
                SELECT *
                FROM exact_targets
                WHERE atom_rn = 1
            ),
            target_walk AS (
                SELECT e.ordinal, e.source_code, e.source_name, e.source_cui,
                       e.source_aui, e.exact_target_source, e.exact_target_code,
                       e.exact_target_name, e.exact_target_cui, e.exact_target_aui,
                       e.exact_target_tty, t.CODE AS target_code,
                       t.STR AS target_name, t.CUI AS target_cui,
                       t.AUI AS target_aui, t.TTY AS target_tty,
                       1 AS target_depth,
                       e.source_atom_rank,
                       e.exact_target_aui || '>' || t.AUI AS path
                FROM exact_seed e
                JOIN mrrel r ON {direct_join}
                JOIN mrconso t ON t.AUI = {direct_target}
                WHERE t.SAB = e.exact_target_source
                  AND t.SUPPRESS = 'N'

                UNION ALL

                SELECT w.ordinal, w.source_code, w.source_name, w.source_cui,
                       w.source_aui, w.exact_target_source, w.exact_target_code,
                       w.exact_target_name, w.exact_target_cui, w.exact_target_aui,
                       w.exact_target_tty, t.CODE AS target_code,
                       t.STR AS target_name, t.CUI AS target_cui,
                       t.AUI AS target_aui, t.TTY AS target_tty,
                       w.target_depth + 1 AS target_depth,
                       w.source_atom_rank,
                       w.path || '>' || t.AUI AS path
                FROM target_walk w
                JOIN mrrel r ON {recursive_join}
                JOIN mrconso t ON t.AUI = {recursive_target}
                WHERE w.target_depth < ?
                  AND t.SAB = w.exact_target_source
                  AND t.SUPPRESS = 'N'
                  AND strpos('>' || w.path || '>', '>' || t.AUI || '>') = 0
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ordinal, exact_target_source, target_code
                           ORDER BY
                               target_depth,
                               source_atom_rank,
                               source_aui,
                               CASE target_tty
                                   WHEN 'PT' THEN 0
                                   WHEN 'MH' THEN 1
                                   WHEN 'LN' THEN 2
                                   ELSE 3
                               END,
                               target_aui
                       ) AS atom_rn
                FROM target_walk
            ),
            deduped_targets AS (
                SELECT *
                FROM ranked
                WHERE atom_rn = 1
            ),
            capped_targets AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ordinal
                           ORDER BY target_depth, exact_target_source, target_code, target_aui
                       ) AS result_rn
                FROM deduped_targets
            )
            SELECT ordinal, source_code, source_name, source_cui, source_aui,
                   exact_target_source, exact_target_code, exact_target_name,
                   exact_target_cui, exact_target_aui, exact_target_tty,
                   target_code, target_name, target_cui, target_aui,
                   target_tty, target_depth
            FROM capped_targets
            WHERE result_rn <= ?
            ORDER BY ordinal, target_depth, exact_target_source, target_code, target_aui
            """,
            [source, *target_sources, max_depth, max_results_per_code],
        ).fetchall()

    return [
        (
            int(ordinal),
            CodeMapping(
                source=CodeRef(source=source, code=source_code),
                target=CodeRef(source=exact_target_source, code=target_code),
                source_display=source_name,
                target_display=target_name,
                relationship=relationship,
                match_type=match_type,
                match_depth=int(target_depth),
                source_cui=source_cui,
                target_cui=target_cui,
                source_aui=source_aui,
                target_aui=target_aui,
                target_tty=target_tty,
                matched_via=Provenance.from_steps(
                    match_type,
                    [
                        ProvenanceStep(
                            op="input_atom",
                            source=source,
                            code=source_code,
                            cui=source_cui,
                            aui=source_aui,
                            name=source_name,
                        ),
                        ProvenanceStep(
                            op="same_cui",
                            source=source,
                            code=source_code,
                            target_source=exact_target_source,
                            target_code=exact_target_code,
                            cui=source_cui,
                        ),
                        ProvenanceStep(
                            op=step_op,
                            source=exact_target_source,
                            code=target_code,
                            cui=target_cui,
                            aui=target_aui,
                            depth=int(target_depth),
                            name=target_name,
                            metadata={"from_code": exact_target_code},
                        ),
                    ],
                ),
            ),
        )
        for (
            ordinal,
            source_code,
            source_name,
            source_cui,
            source_aui,
            exact_target_source,
            exact_target_code,
            _exact_target_name,
            _exact_target_cui,
            _exact_target_aui,
            _exact_target_tty,
            target_code,
            target_name,
            target_cui,
            target_aui,
            target_tty,
            target_depth,
        ) in rows
    ]

def _get_target_hierarchy_mappings_prepared(
    engine,
    source: str,
    code_ordinals: Sequence[tuple[int, str]],
    *,
    target_sources: Sequence[str],
    max_results_per_code: int,
    max_depth: int,
    upward: bool,
) -> list[tuple[int, CodeMapping]]:
    if upward:
        direct_join = "we.from_aui = e.exact_target_aui"
        recursive_join = "we.from_aui = w.target_aui"
        target_code = "we.to_code"
        target_cui = "we.to_cui"
        target_aui = "we.to_aui"
    else:
        direct_join = "we.to_aui = e.exact_target_aui"
        recursive_join = "we.to_aui = w.target_aui"
        target_code = "we.from_code"
        target_cui = "we.from_cui"
        target_aui = "we.from_aui"

    relationship = "source-is-narrower-than-target" if upward else "source-is-broader-than-target"
    match_type = "target_ancestor" if upward else "target_descendant"
    step_op = match_type
    crosswalk_table = (
        "mt4ds.crosswalk_edges"
        if engine._table_exists("crosswalk_edges")
        else "mt4ds.same_cui_edges"
    )
    crosswalk_filter = (
        "AND sce.match_type = 'same_cui'"
        if crosswalk_table == "mt4ds.crosswalk_edges"
        else ""
    )

    with engine._temp_code_ordinals(code_ordinals) as temp:
        rows = engine.con.execute(
            f"""
            WITH RECURSIVE
            source_seed AS (
                SELECT i.ordinal, i.code AS source_code,
                       b.name AS source_name, b.cui AS source_cui,
                       b.aui AS source_aui
                FROM {temp} i
                JOIN mt4ds.best_atoms b
                  ON b.source = ?
                 AND b.code = i.code
                 AND b.rank = 1
            ),
            exact_targets AS (
                SELECT s.ordinal, s.source_code, s.source_name,
                       s.source_cui, s.source_aui,
                       sce.target_source AS exact_target_source,
                       sce.target_code AS exact_target_code,
                       et.name AS exact_target_name,
                       sce.target_cui AS exact_target_cui,
                       sce.target_aui AS exact_target_aui,
                       sce.target_tty AS exact_target_tty
                FROM source_seed s
                JOIN {crosswalk_table} sce
                  ON sce.source = ?
                 AND sce.code = s.source_code
                JOIN mt4ds.best_atoms et
                  ON et.source = sce.target_source
                 AND et.code = sce.target_code
                 AND et.rank = 1
                WHERE sce.target_source IN (SELECT unnest(?))
                  {crosswalk_filter}
            ),
            target_walk AS (
                SELECT e.ordinal, e.source_code, e.source_name,
                       e.source_cui, e.source_aui,
                       e.exact_target_source, e.exact_target_code,
                       e.exact_target_name, e.exact_target_cui,
                       e.exact_target_aui, e.exact_target_tty,
                       {target_code} AS target_code,
                       {target_cui} AS target_cui,
                       {target_aui} AS target_aui,
                       1 AS target_depth,
                       e.exact_target_aui || '>' || {target_aui} AS path
                FROM exact_targets e
                JOIN mt4ds.walk_edges we
                  ON we.source = e.exact_target_source
                 AND we.direction = 'parent'
                 AND {direct_join}

                UNION ALL

                SELECT w.ordinal, w.source_code, w.source_name,
                       w.source_cui, w.source_aui,
                       w.exact_target_source, w.exact_target_code,
                       w.exact_target_name, w.exact_target_cui,
                       w.exact_target_aui, w.exact_target_tty,
                       {target_code} AS target_code,
                       {target_cui} AS target_cui,
                       {target_aui} AS target_aui,
                       w.target_depth + 1 AS target_depth,
                       w.path || '>' || {target_aui} AS path
                FROM target_walk w
                JOIN mt4ds.walk_edges we
                  ON we.source = w.exact_target_source
                 AND we.direction = 'parent'
                 AND {recursive_join}
                WHERE w.target_depth < ?
                  AND strpos('>' || w.path || '>', '>' || {target_aui} || '>') = 0
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ordinal, exact_target_source, target_code
                           ORDER BY target_depth, target_aui
                       ) AS atom_rn
                FROM target_walk
            ),
            capped_targets AS (
                SELECT r.*, t.name AS target_name, t.tty AS target_tty,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.ordinal
                           ORDER BY r.target_depth, r.exact_target_source,
                                    r.target_code, r.target_aui
                       ) AS result_rn
                FROM ranked r
                LEFT JOIN mt4ds.best_atoms t
                  ON t.source = r.exact_target_source
                 AND t.code = r.target_code
                 AND t.rank = 1
                WHERE r.atom_rn = 1
            )
            SELECT ordinal, source_code, source_name, source_cui, source_aui,
                   exact_target_source, exact_target_code, exact_target_name,
                   exact_target_cui, exact_target_aui, exact_target_tty,
                   target_code, COALESCE(target_name, target_code) AS target_name,
                   target_cui, target_aui, target_tty, target_depth
            FROM capped_targets
            WHERE result_rn <= ?
            ORDER BY ordinal, target_depth, exact_target_source, target_code, target_aui
            """,
            [source, source, list(target_sources), max_depth, max_results_per_code],
        ).fetchall()

    return [
        (
            int(ordinal),
            CodeMapping(
                source=CodeRef(source=source, code=source_code),
                target=CodeRef(source=exact_target_source, code=target_code),
                source_display=source_name,
                target_display=target_name,
                relationship=relationship,
                match_type=match_type,
                match_depth=int(target_depth),
                source_cui=source_cui,
                target_cui=target_cui,
                source_aui=source_aui,
                target_aui=target_aui,
                target_tty=target_tty,
                matched_via=Provenance.from_steps(
                    match_type,
                    [
                        ProvenanceStep(
                            op="input_atom",
                            source=source,
                            code=source_code,
                            cui=source_cui,
                            aui=source_aui,
                            name=source_name,
                        ),
                        ProvenanceStep(
                            op="same_cui",
                            source=source,
                            code=source_code,
                            target_source=exact_target_source,
                            target_code=exact_target_code,
                            cui=source_cui,
                        ),
                        ProvenanceStep(
                            op=step_op,
                            source=exact_target_source,
                            code=target_code,
                            cui=target_cui,
                            aui=target_aui,
                            depth=int(target_depth),
                            name=target_name,
                            metadata={"from_code": exact_target_code},
                        ),
                    ],
                ),
            ),
        )
        for (
            ordinal,
            source_code,
            source_name,
            source_cui,
            source_aui,
            exact_target_source,
            exact_target_code,
            _exact_target_name,
            _exact_target_cui,
            _exact_target_aui,
            _exact_target_tty,
            target_code,
            target_name,
            target_cui,
            target_aui,
            target_tty,
            target_depth,
        ) in rows
    ]

def _map_cpt_targets(engine, codes: Sequence[str]) -> dict[str, tuple[str, str]]:
    from medterm4ds.engines.duckdb.engine import _CPT_TARGET_PRIORITY
    if not codes:
        return {}
    with engine._temp_codes(codes) as temp:
        rows = engine.con.execute(
            f"""
            SELECT DISTINCT c.CODE AS cpt_code, t.SAB, t.CODE
            FROM mrconso c
            JOIN mrconso t ON t.CUI = c.CUI
            WHERE c.SAB = 'CPT' AND c.SUPPRESS = 'N'
              AND c.CODE IN (SELECT code FROM {temp})
              AND t.SAB IN ('HCPCS', 'ICD10CM', 'SNOMEDCT_US')
              AND t.SUPPRESS = 'N'
            ORDER BY c.CODE,
                     CASE t.SAB WHEN 'HCPCS' THEN 0 WHEN 'ICD10CM' THEN 1 ELSE 2 END,
                     t.CODE
            """
        ).fetchall()
    mapping: dict[str, tuple[str, str]] = {}
    for cpt_code, target_source, target_code in rows:
        current = mapping.get(cpt_code)
        candidate = (target_source, target_code)
        if current is None or _CPT_TARGET_PRIORITY[target_source] < _CPT_TARGET_PRIORITY[current[0]]:
            mapping[cpt_code] = candidate
    return mapping

def _map_snomed_codes(engine, codes: Sequence[str]) -> dict[str, tuple[str, str, bool]]:
    from medterm4ds.engines.duckdb.engine import (
        _SNOMED_TARGET_PRIORITY,
        _SNOMED_TARGET_SABS_LEGACY,
        _SNOMED_TARGET_SABS_WITH_MGSTY,
        _chunks,
        _dedupe,
        _has_mrsty_table,
        _snomed_tui_target_pairs_sql,
    )
    if not codes:
        return {}
    codes = _dedupe(codes)
    if len(codes) > engine.query_chunk_size:
        mapping: dict[str, tuple[str, str, bool]] = {}
        chunks = list(_chunks(codes, engine.query_chunk_size))
        for chunk_index, chunk in enumerate(chunks, 1):
            engine._progress(
                f"mapping SNOMEDCT_US chunk {chunk_index}/{len(chunks)} "
                f"({len(chunk)} codes)"
            )
            mapping.update(engine._map_snomed_codes(chunk))
        return mapping
    mapping: dict[str, tuple[str, str, bool]] = {}
    use_mrsty = _has_mrsty_table(engine.con)
    tui_pairs_sql = _snomed_tui_target_pairs_sql()
    sabs_sql = ", ".join(
        f"'{s}'" for s in (
            _SNOMED_TARGET_SABS_WITH_MGSTY if use_mrsty else _SNOMED_TARGET_SABS_LEGACY
        )
    )
    with engine._temp_codes(codes) as temp:
        if use_mrsty:
            direct_rows = engine.con.execute(
                f"""
                WITH raw_candidates AS (
                    SELECT DISTINCT sn.CODE AS sn_code, target.SAB AS target_source,
                           target.CODE AS target_code
                    FROM mrrel r
                    JOIN mrconso sn ON sn.AUI = r.AUI1
                    JOIN mrconso target ON target.AUI = r.AUI2
                    WHERE r.RELA = 'mapped_from'
                      AND sn.SAB = 'SNOMEDCT_US' AND sn.SUPPRESS = 'N'
                      AND sn.CODE IN (SELECT code FROM {temp})
                      AND target.SAB IN ({sabs_sql})
                      AND target.SUPPRESS = 'N'
                    UNION
                    SELECT DISTINCT sn.CODE AS sn_code, target.SAB AS target_source,
                           target.CODE AS target_code
                    FROM mrconso sn
                    JOIN mrconso target ON target.CUI = sn.CUI
                    WHERE sn.SAB = 'SNOMEDCT_US' AND sn.SUPPRESS = 'N'
                      AND sn.CODE IN (SELECT code FROM {temp})
                      AND target.SAB IN ({sabs_sql})
                      AND target.SUPPRESS = 'N'
                ),
                sn_tuis AS (
                    SELECT DISTINCT sn.CODE AS sn_code, m.tui
                    FROM mrconso sn
                    JOIN mrsty m ON m.cui = sn.CUI
                    WHERE sn.SAB = 'SNOMEDCT_US' AND sn.SUPPRESS = 'N'
                      AND sn.CODE IN (SELECT code FROM {temp})
                ),
                tui_targets AS (
                    SELECT t.sn_code, p.target_source
                    FROM (SELECT DISTINCT sn_code FROM sn_tuis) t
                    JOIN ({tui_pairs_sql}) p
                      ON p.tui IN (SELECT tui FROM sn_tuis WHERE sn_code = t.sn_code)
                ),
                candidates AS (
                    SELECT c.sn_code, c.target_source, c.target_code
                    FROM raw_candidates c
                    LEFT JOIN tui_targets a
                      ON a.sn_code = c.sn_code AND a.target_source = c.target_source
                    WHERE c.target_source = 'CVX'
                       OR a.sn_code IS NOT NULL
                       OR NOT EXISTS (
                           SELECT 1 FROM sn_tuis t WHERE t.sn_code = c.sn_code
                       )
                )
                SELECT sn_code, target_source, target_code
                FROM candidates
                ORDER BY sn_code,
                         CASE target_source WHEN 'CVX' THEN 0
                                            WHEN 'ICD10CM' THEN 1
                                            WHEN 'ICD10PCS' THEN 2
                                            WHEN 'LNC' THEN 3
                                            WHEN 'RXNORM' THEN 4
                                            WHEN 'CPT' THEN 5
                                            WHEN 'HCPCS' THEN 6
                                            ELSE 7 END,
                         target_code
                """
            ).fetchall()
        else:
            # MRSTY not loaded: fall back to legacy priority-only routing.
            direct_rows = engine.con.execute(
                f"""
                WITH candidates AS (
                    SELECT DISTINCT sn.CODE AS sn_code, target.SAB AS target_source,
                           target.CODE AS target_code
                    FROM mrrel r
                    JOIN mrconso sn ON sn.AUI = r.AUI1
                    JOIN mrconso target ON target.AUI = r.AUI2
                    WHERE r.RELA = 'mapped_from'
                      AND sn.SAB = 'SNOMEDCT_US' AND sn.SUPPRESS = 'N'
                      AND sn.CODE IN (SELECT code FROM {temp})
                      AND target.SAB IN ({sabs_sql})
                      AND target.SUPPRESS = 'N'
                    UNION
                    SELECT DISTINCT sn.CODE AS sn_code, target.SAB AS target_source,
                           target.CODE AS target_code
                    FROM mrconso sn
                    JOIN mrconso target ON target.CUI = sn.CUI
                    WHERE sn.SAB = 'SNOMEDCT_US' AND sn.SUPPRESS = 'N'
                      AND sn.CODE IN (SELECT code FROM {temp})
                      AND target.SAB IN ({sabs_sql})
                      AND target.SUPPRESS = 'N'
                )
                SELECT sn_code, target_source, target_code
                FROM candidates
                ORDER BY sn_code,
                         CASE target_source WHEN 'CVX' THEN 0
                                            WHEN 'ICD10CM' THEN 1
                                            WHEN 'ICD10PCS' THEN 2
                                            WHEN 'LNC' THEN 3
                                            WHEN 'RXNORM' THEN 4
                                            WHEN 'CPT' THEN 5
                                            WHEN 'HCPCS' THEN 6
                                            ELSE 7 END,
                         target_code
                """
            ).fetchall()
    for sn_code, target_source, target_code in direct_rows:
        current = mapping.get(sn_code)
        if current is None or _SNOMED_TARGET_PRIORITY[target_source] < _SNOMED_TARGET_PRIORITY[current[0]]:
            mapping[sn_code] = (target_source, target_code, False)

    unmatched = [code for code in codes if code not in mapping]
    if unmatched:
        for sn_code, target_source, target_code in engine._map_snomed_broader(unmatched):
            mapping.setdefault(sn_code, (target_source, target_code, True))
    return mapping

def _map_snomed_broader(engine, codes: Sequence[str]) -> list[tuple[str, str, str]]:
    from medterm4ds.engines.duckdb.engine import (
        _SNOMED_TARGET_SABS_LEGACY,
        _SNOMED_TARGET_SABS_WITH_MGSTY,
        _chunks,
        _has_mrsty_table,
        _snomed_tui_target_pairs_sql,
        _source_hierarchy_join_sql,
    )
    if len(codes) > engine.query_chunk_size:
        rows: list[tuple[str, str, str]] = []
        chunks = list(_chunks(codes, engine.query_chunk_size))
        for chunk_index, chunk in enumerate(chunks, 1):
            engine._progress(
                f"mapping broader SNOMEDCT_US chunk {chunk_index}/{len(chunks)} "
                f"({len(chunk)} codes)"
            )
            rows.extend(engine._map_snomed_broader(chunk))
        return rows
    snomed_join, snomed_target = _source_hierarchy_join_sql(
        "SNOMEDCT_US",
        "w.AUI",
        upward=True,
    )
    use_mrsty = _has_mrsty_table(engine.con)
    tui_pairs_sql = _snomed_tui_target_pairs_sql()
    sabs_sql = ", ".join(
        f"'{s}'" for s in (
            _SNOMED_TARGET_SABS_WITH_MGSTY if use_mrsty else _SNOMED_TARGET_SABS_LEGACY
        )
    )
    with engine._temp_codes(codes) as temp:
        if use_mrsty:
            rows = engine.con.execute(
                f"""
                WITH RECURSIVE walk AS (
                    SELECT CODE AS input_code, AUI, CUI, 0 AS depth
                    FROM mrconso
                    WHERE SAB = 'SNOMEDCT_US' AND SUPPRESS = 'N'
                      AND CODE IN (SELECT code FROM {temp})
                    UNION
                    SELECT w.input_code, p.AUI, p.CUI, w.depth + 1
                    FROM walk w
                    JOIN mrrel r ON {snomed_join}
                    JOIN mrconso p ON p.AUI = {snomed_target}
                    WHERE w.depth < 2
                      AND p.SAB = 'SNOMEDCT_US' AND p.SUPPRESS = 'N'
                ),
                ancestor_tuis AS (
                    SELECT DISTINCT w.input_code, m.tui
                    FROM walk w
                    JOIN mrsty m ON m.cui = w.CUI
                    WHERE w.depth > 0
                ),
                tui_targets AS (
                    SELECT t.input_code, p.target_source
                    FROM (SELECT DISTINCT input_code FROM ancestor_tuis) t
                    JOIN ({tui_pairs_sql}) p
                      ON p.tui IN (SELECT tui FROM ancestor_tuis WHERE input_code = t.input_code)
                ),
                candidates AS (
                    SELECT DISTINCT w.input_code, target.SAB, target.CODE, w.depth,
                           ROW_NUMBER() OVER (
                               PARTITION BY w.input_code
                               ORDER BY w.depth,
                                        CASE target.SAB WHEN 'CVX' THEN 0
                                                        WHEN 'ICD10CM' THEN 1
                                                        WHEN 'ICD10PCS' THEN 2
                                                        WHEN 'LNC' THEN 3
                                                        WHEN 'RXNORM' THEN 4
                                                        WHEN 'CPT' THEN 5
                                                        WHEN 'HCPCS' THEN 6
                                                        ELSE 7 END,
                                        target.CODE
                           ) AS rn
                    FROM walk w
                    JOIN mrconso target ON target.CUI = w.CUI
                    LEFT JOIN tui_targets a
                      ON a.input_code = w.input_code AND a.target_source = target.SAB
                    WHERE w.depth > 0
                      AND target.SAB IN ({sabs_sql})
                      AND target.SUPPRESS = 'N'
                      AND (
                          target.SAB = 'CVX'
                          OR a.input_code IS NOT NULL
                          OR NOT EXISTS (
                              SELECT 1 FROM ancestor_tuis t WHERE t.input_code = w.input_code
                          )
                      )
                )
                SELECT input_code, SAB, CODE FROM candidates WHERE rn = 1
                """
            ).fetchall()
        else:
            rows = engine.con.execute(
                f"""
                WITH RECURSIVE walk AS (
                    SELECT CODE AS input_code, AUI, CUI, 0 AS depth
                    FROM mrconso
                    WHERE SAB = 'SNOMEDCT_US' AND SUPPRESS = 'N'
                      AND CODE IN (SELECT code FROM {temp})
                    UNION
                    SELECT w.input_code, p.AUI, p.CUI, w.depth + 1
                    FROM walk w
                    JOIN mrrel r ON {snomed_join}
                    JOIN mrconso p ON p.AUI = {snomed_target}
                    WHERE w.depth < 2
                      AND p.SAB = 'SNOMEDCT_US' AND p.SUPPRESS = 'N'
                ),
                candidates AS (
                    SELECT DISTINCT w.input_code, target.SAB, target.CODE, w.depth,
                           ROW_NUMBER() OVER (
                               PARTITION BY w.input_code
                               ORDER BY w.depth,
                                    CASE target.SAB WHEN 'CVX' THEN 0
                                                    WHEN 'ICD10CM' THEN 1
                                                    WHEN 'ICD10PCS' THEN 2
                                                    WHEN 'LNC' THEN 3
                                                    WHEN 'RXNORM' THEN 4
                                                    WHEN 'CPT' THEN 5
                                                    WHEN 'HCPCS' THEN 6
                                                    ELSE 7 END,
                                    target.CODE
                       ) AS rn
                    FROM walk w
                    JOIN mrconso target ON target.CUI = w.CUI
                    WHERE w.depth > 0
                      AND target.SAB IN ({sabs_sql})
                      AND target.SUPPRESS = 'N'
                )
                SELECT input_code, SAB, CODE FROM candidates WHERE rn = 1
                """
            ).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]
