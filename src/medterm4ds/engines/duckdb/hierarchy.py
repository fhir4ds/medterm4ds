"""Hierarchy traversal for the local DuckDB engine.

Extracted from engines/duckdb/engine.py (Phase 2 of Tier C refactor). These
functions handle same-source parent/child/ancestor/descendant traversal over
either prepared mt4ds.walk_edges tables or raw mrrel/mrconso.

The functions take the engine instance as their first parameter to access
the DuckDB connection and table-existence helpers. Future phases may refine
this to pass `con` + helpers explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, CodeRelation


def get_source_code_relations(
    engine,
    source: str,
    code_ordinals: Sequence[tuple[int, str]],
    *,
    relationship: str,
    upward: bool,
    max_depth: int,
) -> list[tuple[int, CodeRelation]]:
    """Return same-source hierarchy rows for one source + chunk of codes.

    Dispatches to the prepared path if mt4ds.walk_edges + best_atoms exist;
    otherwise falls back to raw mrrel/mrconso traversal.
    """
    if engine._table_exists("walk_edges") and engine._table_exists("best_atoms"):
        return get_source_code_relations_prepared(
            engine,
            source,
            code_ordinals,
            relationship=relationship,
            upward=upward,
            max_depth=max_depth,
        )

    # Late import to avoid circular dependency (engine module defines these helpers).
    from medterm4ds.engines.duckdb.engine import (
        _dedupe_relation_rows,
        _source_hierarchy_join_sql,
    )

    source_join, source_target = _source_hierarchy_join_sql(
        source,
        "s.source_aui",
        upward=upward,
    )
    recursive_join, recursive_target = _source_hierarchy_join_sql(
        source,
        "w.target_aui",
        upward=upward,
    )

    with engine._temp_code_ordinals(code_ordinals) as temp:
        rows = engine.con.execute(
            f"""
            WITH RECURSIVE
            base AS (
                SELECT i.ordinal, i.code AS source_code, c.STR AS source_name,
                       c.CUI AS source_cui, c.AUI AS source_aui,
                       ROW_NUMBER() OVER (
                           PARTITION BY i.ordinal
                           ORDER BY
                               CASE c.TTY
                                   WHEN 'PT' THEN 0
                                   WHEN 'MH' THEN 1
                                   WHEN 'LN' THEN 2
                                   ELSE 3
                               END,
                               c.AUI
                       ) AS rn
                FROM {temp} i
                JOIN mrconso c ON c.CODE = i.code
                WHERE c.SAB = ?
                  AND c.SUPPRESS = 'N'
            ),
            seed AS (
                SELECT ordinal, source_code, source_name, source_cui, source_aui
                FROM base
                WHERE rn = 1
            ),
            walk AS (
                SELECT s.ordinal, s.source_code, s.source_name, s.source_cui,
                       s.source_aui, t.CODE AS target_code, t.STR AS target_name,
                       t.CUI AS target_cui, t.AUI AS target_aui, r.REL AS rel,
                       r.RELA AS rela, 1 AS depth,
                       s.source_aui || '>' || t.AUI AS path
                FROM seed s
                JOIN mrrel r ON {source_join}
                JOIN mrconso t ON t.AUI = {source_target}
                WHERE t.SAB = ?
                  AND t.SUPPRESS = 'N'

                UNION ALL

                SELECT w.ordinal, w.source_code, w.source_name, w.source_cui,
                       w.source_aui, t.CODE AS target_code, t.STR AS target_name,
                       t.CUI AS target_cui, t.AUI AS target_aui, r.REL AS rel,
                       r.RELA AS rela, w.depth + 1 AS depth,
                       w.path || '>' || t.AUI AS path
                FROM walk w
                JOIN mrrel r ON {recursive_join}
                JOIN mrconso t ON t.AUI = {recursive_target}
                WHERE w.depth < ?
                  AND t.SAB = ?
                  AND t.SUPPRESS = 'N'
                  AND strpos('>' || w.path || '>', '>' || t.AUI || '>') = 0
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ordinal, target_code
                           ORDER BY depth, target_aui
                       ) AS rn
                FROM walk
            )
            SELECT ordinal, source_code, source_name, source_cui, source_aui,
                   target_code, target_name, target_cui, target_aui, rel, rela, depth
            FROM ranked
            WHERE rn = 1
            ORDER BY ordinal, depth, target_code, target_aui
            """,
            [source, source, max_depth, source],
        ).fetchall()

    relations = [
        (
            int(ordinal),
            CodeRelation(
                source=CodeRef(source=source, code=source_code),
                target=CodeRef(source=source, code=target_code),
                relationship=relationship,
                depth=int(depth),
                source_display=source_name,
                target_display=target_name,
                rel=rel,
                rela=rela,
                source_cui=source_cui,
                target_cui=target_cui,
                source_aui=source_aui,
                target_aui=target_aui,
            ),
        )
        for (
            ordinal,
            source_code,
            source_name,
            source_cui,
            source_aui,
            target_code,
            target_name,
            target_cui,
            target_aui,
            rel,
            rela,
            depth,
        ) in rows
    ]
    return _dedupe_relation_rows(relations)


def get_source_code_relations_prepared(
    engine,
    source: str,
    code_ordinals: Sequence[tuple[int, str]],
    *,
    relationship: str,
    upward: bool,
    max_depth: int,
) -> list[tuple[int, CodeRelation]]:
    """Prepared-table path: traverse mt4ds.walk_edges instead of raw mrrel."""
    if upward:
        first_join = "e.from_aui = s.source_aui"
        recursive_join = "e.from_aui = w.target_aui"
        target_code = "e.to_code"
        target_aui = "e.to_aui"
        target_cui = "e.to_cui"
    else:
        first_join = "e.to_aui = s.source_aui"
        recursive_join = "e.to_aui = w.target_aui"
        target_code = "e.from_code"
        target_aui = "e.from_aui"
        target_cui = "e.from_cui"

    with engine._temp_code_ordinals(code_ordinals) as temp:
        rows = engine.con.execute(
            f"""
            WITH RECURSIVE
            seed AS (
                SELECT i.ordinal, i.code AS source_code,
                       b.name AS source_name, b.cui AS source_cui,
                       b.aui AS source_aui
                FROM {temp} i
                JOIN mt4ds.best_atoms b
                  ON b.source = ?
                 AND b.code = i.code
                 AND b.rank = 1
            ),
            walk AS (
                SELECT s.ordinal, s.source_code, s.source_name,
                       s.source_cui, s.source_aui,
                       {target_code} AS target_code,
                       {target_cui} AS target_cui,
                       {target_aui} AS target_aui,
                       e.relationship AS rel,
                       1 AS depth,
                       s.source_aui || '>' || {target_aui} AS path
                FROM seed s
                JOIN mt4ds.walk_edges e
                  ON e.source = ?
                 AND e.direction = 'parent'
                 AND {first_join}

                UNION ALL

                SELECT w.ordinal, w.source_code, w.source_name,
                       w.source_cui, w.source_aui,
                       {target_code} AS target_code,
                       {target_cui} AS target_cui,
                       {target_aui} AS target_aui,
                       e.relationship AS rel,
                       w.depth + 1 AS depth,
                       w.path || '>' || {target_aui} AS path
                FROM walk w
                JOIN mt4ds.walk_edges e
                  ON e.source = ?
                 AND e.direction = 'parent'
                 AND {recursive_join}
                WHERE w.depth < ?
                  AND strpos('>' || w.path || '>', '>' || {target_aui} || '>') = 0
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ordinal, target_code
                           ORDER BY depth, target_aui
                       ) AS rn
                FROM walk
            )
            SELECT r.ordinal, r.source_code, r.source_name, r.source_cui,
                   r.source_aui, r.target_code,
                   COALESCE(t.name, r.target_code) AS target_name,
                   r.target_cui, r.target_aui, r.rel, r.depth
            FROM ranked r
            LEFT JOIN mt4ds.best_atoms t
              ON t.source = ?
             AND t.code = r.target_code
             AND t.rank = 1
            WHERE r.rn = 1
            ORDER BY r.ordinal, r.depth, r.target_code, r.target_aui
            """,
            [source, source, source, max_depth, source],
        ).fetchall()

    return [
        (
            int(ordinal),
            CodeRelation(
                source=CodeRef(source=source, code=source_code),
                target=CodeRef(source=source, code=target_code),
                relationship=relationship,
                depth=int(depth),
                source_display=source_name,
                target_display=target_name,
                rel=rel,
                rela=None,
                source_cui=source_cui,
                target_cui=target_cui,
                source_aui=source_aui,
                target_aui=target_aui,
            ),
        )
        for (
            ordinal,
            source_code,
            source_name,
            source_cui,
            source_aui,
            target_code,
            target_name,
            target_cui,
            target_aui,
            rel,
            depth,
        ) in rows
    ]


def source_display_lookup(
    engine,
    source: str,
    codes: Sequence[str],
) -> dict[str, tuple[str, str, str]]:
    """Return {code: (name, cui, aui)} for each code, picking the best atom.

    Uses mt4ds.best_atoms if prepared tables exist; otherwise falls back to
    ranking mrconso atoms by source-specific TTY/length ordering.
    """
    if not codes:
        return {}
    # Late import for the same cycle-avoidance reason.
    from medterm4ds.engines.duckdb.engine import _source_atom_order_sql

    with engine._temp_codes(codes) as temp:
        if engine._table_exists("best_atoms"):
            rows = engine.con.execute(
                f"""
                SELECT code, name, cui, aui
                FROM mt4ds.best_atoms
                WHERE source = ?
                  AND rank = 1
                  AND is_active = true
                  AND code IN (SELECT code FROM {temp})
                """,
                [source],
            ).fetchall()
        else:
            atom_order_sql = _source_atom_order_sql(source)
            rows = engine.con.execute(
                f"""
                WITH ranked AS (
                    SELECT CODE, STR, CUI, AUI,
                           ROW_NUMBER() OVER (
                               PARTITION BY CODE
                               ORDER BY {atom_order_sql}
                           ) AS rn
                    FROM mrconso
                    WHERE SAB = ?
                      AND SUPPRESS = 'N'
                      AND CODE IN (SELECT code FROM {temp})
                )
                SELECT CODE, STR, CUI, AUI
                FROM ranked
                WHERE rn = 1
                """,
                [source],
            ).fetchall()
    return {str(code): (str(name), str(cui), str(aui)) for code, name, cui, aui in rows}
