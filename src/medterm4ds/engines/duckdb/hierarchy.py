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
    limit: int | None = None,
    include_retired: bool = False,
) -> list[tuple[int, CodeRelation]]:
    """Return same-source hierarchy rows for one source + chunk of codes.

    Dispatches to the prepared path if mt4ds.walk_edges + best_atoms exist AND
    walk_edges covers the requested source; otherwise falls back to raw
    mrrel/mrconso traversal.

    ``include_retired=True`` skips the QC-238 retired-concept pruning on both
    paths: retired/editorial-suppressed concepts are included as walk targets
    (and seeds), so the result is a superset of the default active-only walk.
    """
    # QC-402 (MEDIUM): the gate previously checked TABLE EXISTENCE only, so a
    # source with 0 rows in production walk_edges (RXNORM, MSH — the builder
    # fixes that added them postdate the production prepare) silently returned
    # [] while raw mrrel carried the edges. Per-source non-empty gate mirrors
    # the QC-398 fix on code_replacements.
    if (
        engine._table_exists("walk_edges")
        and engine._table_exists("best_atoms")
        and engine._walk_edges_cover_source(source)
    ):
        return get_source_code_relations_prepared(
            engine,
            source,
            code_ordinals,
            relationship=relationship,
            upward=upward,
            max_depth=max_depth,
            limit=limit,
            include_retired=include_retired,
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

    # include_retired=True: skip the SUPPRESS='N' pruning on the seed and on
    # every walked target (retired/editorial-suppressed concepts join the walk).
    _seed_suppress = "" if include_retired else "AND c.SUPPRESS = 'N'"
    _target_suppress = "" if include_retired else "AND t.SUPPRESS = 'N'"

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
                  {_seed_suppress}
            ),
            -- QC-349/350 (EC-15 HIGH): seed EVERY active atom of the input
            -- code, not just the rn=1 one — the same multi-atom fix the
            -- prepared path got for QC-067/QC-070. RxNorm codes carry a
            -- TMSY atom alongside the SCD/SCDG atom and LOINC class codes
            -- carry LPN alongside LPDN; the hierarchy edges attach to the
            -- non-display atom, so an rn=1-only seed silently returned 0
            -- children/parents (engine-mode divergence vs the prepared
            -- path). Canonical display fields still come from the rn=1
            -- atom; the ``ranked`` CTE dedups by (ordinal, target_code)
            -- so multi-atom seeds don't duplicate target rows.
            seed AS (
                SELECT b.ordinal, b.source_code,
                       d.source_name, d.source_cui,
                       b.source_aui
                FROM base b
                JOIN base d
                  ON d.ordinal = b.ordinal
                 AND d.rn = 1
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
                  {_target_suppress}

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
                  {_target_suppress}
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
    limit: int | None = None,
    include_retired: bool = False,
) -> list[tuple[int, CodeRelation]]:
    """Prepared-table path: traverse mt4ds.walk_edges instead of raw mrrel.

    `limit` caps the final result rows (after deduplication). It does NOT
    terminate the recursive walk early — DuckDB recursive CTEs don't support
    that — but it does avoid transferring the full result set to Python and
    building CodeRelation objects for rows the caller will discard. For
    pathological hierarchies (e.g. SNOMED diabetes), the walk itself is the
    bottleneck; see `_expand_url_pattern` for the depth-cap contract.

    ``include_retired=True`` skips the QC-238 is_active pruning on the seed
    and on walk targets (retired/editorial-suppressed concepts join the walk).
    """
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
        limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
        # QC-067/QC-070 (CRITICAL): the seed step previously joined to
        # ``mt4ds.best_atoms`` with ``rank = 1``, then matched ``walk_edges``
        # by AUI. When a SNOMED code has multiple atoms and a CHD/isa edge
        # in mrrel attaches to a non-rank-1 atom of the parent, that edge
        # is silently dropped from the walk — losing 34,471 PT-child
        # relationships across 5,848 SNOMED parent codes. Fix: seed against
        # ``mt4ds.best_atoms`` WITHOUT the ``rank = 1`` filter (best_atoms
        # is built from mt4ds.atoms and contains ALL atoms), so any AUI of
        # the input code can match ``walk_edges``. Canonical display fields
        # (source_name/source_cui) are taken from the rank-1 atom via a
        # correlated LEFT JOIN. The downstream ``ranked`` CTE dedups by
        # ``(ordinal, target_code)`` so multi-atom seeds don't produce
        # duplicate target rows.
        #
        # QC-238 (HIGH): filter retired (suppressed) atoms from the walk —
        # the raw-mrrel path below already enforces ``t.SUPPRESS = 'N'`` on
        # every walked target, but this prepared path had no ``is_active``
        # check, leaking 8,069 retired SNOMED concepts (16.2%) into the
        # depth-3 descendants of 404684003. The EXISTS check prunes edges
        # whose TARGET atom is suppressed, so paths through retired concepts
        # are cut exactly like the raw path.
        # ``include_retired=True`` disables both checks (seed + targets).
        _seed_active = "" if include_retired else "AND b.is_active = true"
        _active_target = (
            "true"
            if include_retired
            else (
                "EXISTS (SELECT 1 FROM mt4ds.best_atoms ba "
                "WHERE ba.source = e.source AND ba.aui = {aui} "
                "AND ba.is_active = true)"
            )
        )
        rows = engine.con.execute(
            f"""
            WITH RECURSIVE
            seed AS (
                SELECT i.ordinal, i.code AS source_code,
                       d.name AS source_name, d.cui AS source_cui,
                       b.aui AS source_aui
                FROM {temp} i
                JOIN mt4ds.best_atoms b
                  ON b.source = ?
                 AND b.code = i.code
                 {_seed_active}
                LEFT JOIN mt4ds.best_atoms d
                  ON d.source = b.source
                 AND d.code = b.code
                 AND d.rank = 1
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
                 AND {_active_target.format(aui=target_aui)}

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
                 AND {_active_target.format(aui=target_aui)}
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
            {limit_clause}
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
