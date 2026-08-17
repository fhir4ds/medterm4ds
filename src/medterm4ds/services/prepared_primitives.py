"""Shared primitives for prepared mt4ds terminology tables.

These helpers expose elemental operations used by higher-level services:
preferred atom lookup, hierarchy walk acceleration, and same-CUI crosswalk
table selection. They do not apply patient-friendly naming policy.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from uuid import uuid4
from weakref import WeakKeyDictionary

from medterm4ds.core.models import CodeInfo, CodeRef

logger = logging.getLogger(__name__)

WALK_CLOSURE_MAX_DEPTH = 5

# CR-031: per-source walk_closure_limited coverage memo, keyed by connection
# then source (mirrors the QC-402 ``_walk_edges_cover_source`` engine gate).
# WeakKeyDictionary so a closed connection's entries drop out automatically.
_WALK_CLOSURE_SOURCE_CACHE: WeakKeyDictionary = WeakKeyDictionary()


def _walk_closure_source_cache_for(con) -> dict[str, bool] | None:
    """Return the per-connection source cache, or None when con cannot be
    weak-keyed (exotic wrappers) — the probe then simply runs unmemoized."""
    cache = _WALK_CLOSURE_SOURCE_CACHE.get(con)
    if cache is not None:
        return cache
    cache = {}
    try:
        _WALK_CLOSURE_SOURCE_CACHE[con] = cache
    except TypeError:
        return None
    return cache


def _walk_closure_covers_source(con, source: str) -> bool:
    """Return True when walk_closure_limited has at least one row for source.

    CR-031 (HIGH): dispatching closure-accelerated walks on TABLE EXISTENCE
    alone let a source with zero closure rows (RXNORM/ATC/MSH on DBs built
    before the seed whitelist was derived from SOURCE_STRATEGIES) silently
    return [] on every consumer while mt4ds.walk_edges had the edges. A
    ``LIMIT 1`` probe keeps this bounded (indexed on the leading ``source``
    column); memoized because the dispatch runs per (source, chunk).
    """
    cache = _walk_closure_source_cache_for(con)
    cached = cache.get(source) if cache is not None else None
    if cached is not None:
        return cached
    probe = con.execute(
        "SELECT 1 FROM mt4ds.walk_closure_limited WHERE source = ? LIMIT 1",
        [source],
    ).fetchone()
    covered = probe is not None
    if not covered:
        logger.warning(
            "mt4ds.walk_closure_limited has 0 rows for source %r — closure-"
            "accelerated hierarchy walks for this source fall back to the "
            "mt4ds.walk_edges BFS path (prepared closure table is stale or "
            "partial; rebuild with `medterm4ds data prepare-derived --db <db>` "
            "to add it).",
            source,
        )
    if cache is not None:
        cache[source] = covered
    return covered


def table_exists(con, table_name: str, *, schema: str = "mt4ds") -> bool:
    try:
        row = con.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = ?
              AND table_name = ?
            LIMIT 1
            """,
            [schema, table_name],
        ).fetchone()
    except Exception:
        return False
    return bool(row)


def same_cui_crosswalk_sql(con) -> tuple[str, str]:
    """Return the canonical same-CUI crosswalk table and SQL filter."""
    if table_exists(con, "crosswalk_edges"):
        return "mt4ds.crosswalk_edges", "AND sce.match_type = 'same_cui'"
    return "mt4ds.same_cui_edges", ""


def walk_closure_table(con, max_depth: int, sources: str | Iterable[str] | None = None) -> str | None:
    """Return the bounded parent-walk closure table when it can satisfy depth.

    CR-031 (HIGH): when ``sources`` is given, the closure table must ALSO have
    rows for every named source; a source with zero rows (e.g. RXNORM/ATC/MSH
    on a prepared DB built before the closure seed whitelist was derived from
    SOURCE_STRATEGIES) returns None so the caller falls back to the
    mt4ds.walk_edges BFS path instead of silently returning [] through the
    closure. ``sources`` may be a single source name or any iterable of them.
    """
    if max_depth > WALK_CLOSURE_MAX_DEPTH or not table_exists(con, "walk_closure_limited"):
        return None
    if sources is not None:
        source_names = {sources} if isinstance(sources, str) else set(sources)
        for source in source_names:
            if not _walk_closure_covers_source(con, source):
                return None
    return "mt4ds.walk_closure_limited"


@contextmanager
def temp_codes(con, codes: Sequence[str], *, prefix: str = "_mt4ds_codes") -> Iterator[str]:
    """Create a temp table of codes, yield its name, then drop it."""
    table = f"{prefix}_{uuid4().hex}"
    con.execute(f"CREATE TEMP TABLE {table} (code VARCHAR)")
    try:
        con.executemany(
            f"INSERT INTO {table} VALUES (?)",
            [(str(code),) for code in codes],
        )
        yield table
    finally:
        con.execute(f"DROP TABLE IF EXISTS {table}")


def dedupe_values(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def group_codes_by_source(codes: Sequence[CodeRef]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for code in codes:
        grouped[code.source].append(code.code)
    return grouped


def preferred_atom_lookup(
    con,
    source: str,
    codes: Sequence[str],
) -> dict[str, CodeInfo]:
    """Return rank-1 atom info keyed by code for one source."""
    if not codes:
        return {}
    deduped = dedupe_values(codes)
    with temp_codes(con, deduped, prefix="_mt4ds_lookup_codes") as temp:
        rows = con.execute(
            f"""
            SELECT code, name, cui, aui, tty, suppress
            FROM mt4ds.best_atoms
            WHERE source = ?
              AND rank = 1
              AND code IN (SELECT code FROM {temp})
            """,
            [source],
        ).fetchall()
    return {
        str(code): CodeInfo(
            code=CodeRef(source=source, code=str(code)),
            name=str(name) if name is not None else None,
            cui=str(cui) if cui is not None else None,
            aui=str(aui) if aui is not None else None,
            tty=str(tty) if tty is not None else None,
            suppress=str(suppress) if suppress is not None else None,
        )
        for code, name, cui, aui, tty, suppress in rows
    }


def source_display_lookup(
    con,
    source: str,
    codes: Sequence[str],
) -> dict[str, tuple[str, str, str]]:
    """Return {code: (name, cui, aui)} from rank-1 atoms for compatibility."""
    lookup = preferred_atom_lookup(con, source, codes)
    return {
        code: (
            info.name or "",
            info.cui or "",
            info.aui or "",
        )
        for code, info in lookup.items()
    }
