"""Hierarchy walk over normalized mt4ds tables.

Provides direct access to hierarchy traversal using the ``mt4ds.walk_edges``
prepared table, without going through the full engine layer.

Design note (Tier C Phase 6 investigation, 2026-06-26):
These functions are a LOW-LEVEL PUBLIC API for callers who want direct
walk_edges access without engine enrichment. They intentionally do NOT
include display-name lookup, provenance, or input-order preservation.

The engine's hierarchy traversal (`engines/duckdb/hierarchy.py`) is a
SEPARATE, richer implementation that uses walk_edges but adds best_atoms
display lookups, Provenance tracking, and ordinal-based ordering. It is
not a refactor target for these primitives -- forcing the engine to use
them would require gutting their simplicity (display lookups, provenance,
ordinals) which defeats the purpose of having a low-level API.

Both implementations coexist by design:
- `services.walk.*` -- programmatic callers wanting simple walk access
- `engines/duckdb/hierarchy.py` -- the engine's enriched public API

If you find yourself wanting the engine to use these primitives, the
right move is to extend the primitives to support the engine's needs
(display names, provenance) as opt-in parameters -- not to remove
functionality from the engine.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, CodeRelation
from medterm4ds.services.prepared_primitives import (
    dedupe_values,
    group_codes_by_source,
    temp_codes,
    walk_closure_table,
)

logger = logging.getLogger(__name__)


def get_parents_prepared(
    codes: Sequence[CodeRef],
    con,
) -> list[CodeRelation]:
    """Walk direct parents using mt4ds.walk_edges.

    Parameters
    ----------
    codes:
        Input codes to walk.  All codes must share the same source.
    con:
        DuckDB connection with ``mt4ds.walk_edges`` available.

    Returns
    -------
    list[CodeRelation]
        Direct parent relationships (depth=1).
    """
    if not codes:
        return []

    results: list[CodeRelation] = []
    for source, source_codes in group_codes_by_source(codes).items():
        with temp_codes(con, dedupe_values(source_codes), prefix="_mt4ds_walk_codes") as temp:
            rows = con.execute(
                f"""
                SELECT we.from_code, we.to_code, we.from_cui, we.to_cui,
                       we.from_aui, we.to_aui, we.relationship, we.edge_source
                FROM mt4ds.walk_edges we
                WHERE we.source = ?
                  AND we.direction = 'parent'
                  AND we.from_code IN (SELECT code FROM {temp})
                ORDER BY we.from_code, we.to_code, we.from_aui, we.to_aui
                """,
                [source],
            ).fetchall()

        for from_code, to_code, from_cui, to_cui, from_aui, to_aui, rel, _edge_src in rows:
            results.append(
                CodeRelation(
                    source=CodeRef(source=source, code=from_code),
                    target=CodeRef(source=source, code=to_code),
                    relationship="parent",
                    depth=1,
                    source_cui=from_cui,
                    target_cui=to_cui,
                    source_aui=from_aui,
                    target_aui=to_aui,
                    rel=rel,
                )
            )
    return results


def get_children_prepared(
    codes: Sequence[CodeRef],
    con,
) -> list[CodeRelation]:
    """Walk direct children using reverse traversal over mt4ds.walk_edges."""
    if not codes:
        return []

    results: list[CodeRelation] = []
    for source, source_codes in group_codes_by_source(codes).items():
        with temp_codes(con, dedupe_values(source_codes), prefix="_mt4ds_walk_codes") as temp:
            rows = con.execute(
                f"""
                SELECT we.from_code, we.to_code, we.from_cui, we.to_cui,
                       we.from_aui, we.to_aui, we.relationship, we.edge_source
                FROM mt4ds.walk_edges we
                WHERE we.source = ?
                  AND we.direction = 'parent'
                  AND we.to_code IN (SELECT code FROM {temp})
                ORDER BY we.to_code, we.from_code, we.to_aui, we.from_aui
                """,
                [source],
            ).fetchall()

        for from_code, to_code, from_cui, to_cui, from_aui, to_aui, rel, _edge_src in rows:
            results.append(
                CodeRelation(
                    source=CodeRef(source=source, code=to_code),
                    target=CodeRef(source=source, code=from_code),
                    relationship="child",
                    depth=1,
                    source_cui=to_cui,
                    target_cui=from_cui,
                    source_aui=to_aui,
                    target_aui=from_aui,
                    rel=rel,
                )
            )
    return results


def get_ancestors_prepared(
    codes: Sequence[CodeRef],
    con,
    *,
    max_depth: int = 20,
) -> list[CodeRelation]:
    """Walk ancestors using iterative BFS over mt4ds.walk_edges.

    Parameters
    ----------
    codes:
        Input codes to walk.  All codes must share the same source.
    con:
        DuckDB connection with ``mt4ds.walk_edges`` available.
    max_depth:
        Maximum traversal depth (default 20).

    Returns
    -------
    list[CodeRelation]
        Ancestor relationships at each depth reached.
    """
    if not codes:
        return []

    results: list[CodeRelation] = []

    for source, source_codes in group_codes_by_source(codes).items():
        results.extend(
            _walk_transitive(
                source=source,
                seed_codes=dedupe_values(source_codes),
                con=con,
                max_depth=max_depth,
                upward=True,
            )
        )

    return results


def get_descendants_prepared(
    codes: Sequence[CodeRef],
    con,
    *,
    max_depth: int = 20,
) -> list[CodeRelation]:
    """Walk descendants using reverse iterative BFS over mt4ds.walk_edges."""
    if not codes:
        return []

    results: list[CodeRelation] = []
    for source, source_codes in group_codes_by_source(codes).items():
        results.extend(
            _walk_transitive(
                source=source,
                seed_codes=dedupe_values(source_codes),
                con=con,
                max_depth=max_depth,
                upward=False,
            )
        )
    return results


def _walk_transitive(
    *,
    source: str,
    seed_codes: Sequence[str],
    con,
    max_depth: int,
    upward: bool,
) -> list[CodeRelation]:
    closure_table = walk_closure_table(con, max_depth)
    if closure_table:
        return _walk_transitive_closure(
            source=source,
            seed_codes=seed_codes,
            con=con,
            max_depth=max_depth,
            upward=upward,
            closure_table=closure_table,
        )

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
            with temp_codes(con, dedupe_values(codes_at_depth), prefix="_mt4ds_walk_codes") as temp:
                if upward:
                    rows = con.execute(
                        f"""
                        SELECT we.from_code, we.to_code, we.from_cui, we.to_cui,
                               we.from_aui, we.to_aui, we.relationship, we.edge_source
                        FROM mt4ds.walk_edges we
                        WHERE we.source = ?
                          AND we.direction = 'parent'
                          AND we.from_code IN (SELECT code FROM {temp})
                        ORDER BY we.from_code, we.to_code, we.from_aui, we.to_aui
                        """,
                        [source],
                    ).fetchall()
                else:
                    rows = con.execute(
                        f"""
                        SELECT we.from_code, we.to_code, we.from_cui, we.to_cui,
                               we.from_aui, we.to_aui, we.relationship, we.edge_source
                        FROM mt4ds.walk_edges we
                        WHERE we.source = ?
                          AND we.direction = 'parent'
                          AND we.to_code IN (SELECT code FROM {temp})
                        ORDER BY we.to_code, we.from_code, we.to_aui, we.from_aui
                        """,
                        [source],
                    ).fetchall()

            next_frontier: list[str] = []
            for from_code, to_code, from_cui, to_cui, from_aui, to_aui, rel, _edge_src in rows:
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
                        source=CodeRef(source=source, code=source_code),
                        target=CodeRef(source=source, code=target_code),
                        relationship=relationship,
                        depth=depth,
                        source_cui=source_cui,
                        target_cui=target_cui,
                        source_aui=source_aui,
                        target_aui=target_aui,
                        rel=rel,
                    )
                )
                if next_code not in visited:
                    visited.add(next_code)
                    next_frontier.append(next_code)

            for next_code in next_frontier:
                queue.append((next_code, depth + 1))

    return results


def _walk_transitive_closure(
    *,
    source: str,
    seed_codes: Sequence[str],
    con,
    max_depth: int,
    upward: bool,
    closure_table: str,
) -> list[CodeRelation]:
    with temp_codes(con, dedupe_values(seed_codes), prefix="_mt4ds_walk_codes") as temp:
        if upward:
            rows = con.execute(
                f"""
                SELECT c.from_code, c.to_code, c.from_cui, c.to_cui,
                       c.from_aui, c.to_aui, c.depth
                FROM {closure_table} c
                WHERE c.source = ?
                  AND c.depth <= ?
                  AND c.from_code IN (SELECT code FROM {temp})
                ORDER BY c.from_code, c.depth, c.to_code, c.from_aui, c.to_aui
                """,
                [source, max_depth],
            ).fetchall()
        else:
            rows = con.execute(
                f"""
                SELECT c.from_code, c.to_code, c.from_cui, c.to_cui,
                       c.from_aui, c.to_aui, c.depth
                FROM {closure_table} c
                WHERE c.source = ?
                  AND c.depth <= ?
                  AND c.to_code IN (SELECT code FROM {temp})
                ORDER BY c.to_code, c.depth, c.from_code, c.to_aui, c.from_aui
                """,
                [source, max_depth],
            ).fetchall()

    results: list[CodeRelation] = []
    seen: set[tuple[str, str, int, str | None, str | None]] = set()
    for from_code, to_code, from_cui, to_cui, from_aui, to_aui, depth in rows:
        if upward:
            source_code = from_code
            target_code = to_code
            source_cui = from_cui
            target_cui = to_cui
            source_aui = from_aui
            target_aui = to_aui
            relationship = "ancestor"
        else:
            source_code = to_code
            target_code = from_code
            source_cui = to_cui
            target_cui = from_cui
            source_aui = to_aui
            target_aui = from_aui
            relationship = "descendant"
        key = (str(source_code), str(target_code), int(depth), source_aui, target_aui)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            CodeRelation(
                source=CodeRef(source=source, code=source_code),
                target=CodeRef(source=source, code=target_code),
                relationship=relationship,
                depth=int(depth),
                source_cui=source_cui,
                target_cui=target_cui,
                source_aui=source_aui,
                target_aui=target_aui,
                rel="isa",
            )
        )
    return results


def _group_codes_by_source(codes: Sequence[CodeRef]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for code in codes:
        grouped[code.source].append(code.code)
    return grouped


def _dedupe_values(values: Sequence[str]) -> list[str]:
    """Return deduplicated strings preserving input order."""
    return list(dict.fromkeys(values))
