"""Hierarchy walk over normalized mt4ds tables.

Provides direct access to hierarchy traversal using the ``mt4ds.walk_edges``
prepared table, without going through the full engine layer.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import uuid4

from medterm4ds.core.models import CodeRef, CodeRelation

logger = logging.getLogger(__name__)


@contextmanager
def _temp_codes(con, codes: Sequence[str]) -> Iterator[str]:
    """Create a temp table of codes, yield its name, then drop it."""
    table = f"_mt4ds_walk_codes_{uuid4().hex}"
    con.execute(f"CREATE TEMP TABLE {table} (code VARCHAR)")
    try:
        con.executemany(
            f"INSERT INTO {table} VALUES (?)",
            [(str(code),) for code in codes],
        )
        yield table
    finally:
        con.execute(f"DROP TABLE IF EXISTS {table}")


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
    for source, source_codes in _group_codes_by_source(codes).items():
        with _temp_codes(con, _dedupe_values(source_codes)) as temp:
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
    for source, source_codes in _group_codes_by_source(codes).items():
        with _temp_codes(con, _dedupe_values(source_codes)) as temp:
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

    for source, source_codes in _group_codes_by_source(codes).items():
        results.extend(
            _walk_transitive(
                source=source,
                seed_codes=_dedupe_values(source_codes),
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
    for source, source_codes in _group_codes_by_source(codes).items():
        results.extend(
            _walk_transitive(
                source=source,
                seed_codes=_dedupe_values(source_codes),
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
            with _temp_codes(con, _dedupe_values(codes_at_depth)) as temp:
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


def _group_codes_by_source(codes: Sequence[CodeRef]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for code in codes:
        grouped[code.source].append(code.code)
    return grouped


def _dedupe_values(values: Sequence[str]) -> list[str]:
    """Return deduplicated strings preserving input order."""
    return list(dict.fromkeys(values))
