"""Shared primitives for prepared mt4ds terminology tables.

These helpers expose elemental operations used by higher-level services:
preferred atom lookup, hierarchy walk acceleration, and same-CUI crosswalk
table selection. They do not apply patient-friendly naming policy.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import uuid4

from medterm4ds.core.models import CodeInfo, CodeRef

WALK_CLOSURE_MAX_DEPTH = 5


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


def walk_closure_table(con, max_depth: int) -> str | None:
    """Return the bounded parent-walk closure table when it can satisfy depth."""
    if max_depth <= WALK_CLOSURE_MAX_DEPTH and table_exists(con, "walk_closure_limited"):
        return "mt4ds.walk_closure_limited"
    return None


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
