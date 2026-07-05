"""Exact terminology code lookup services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeInfo, CodeRef
from medterm4ds.engines.base import LookupEngine
from medterm4ds.services.prepared_primitives import (
    group_codes_by_source,
    preferred_atom_lookup,
)
from medterm4ds.services.resolution import effective_code_refs


def get_code_infos(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: LookupEngine,
    *,
    resolve_mode: str = "active_only",
) -> list[CodeInfo | None]:
    """Look up canonical atom info for one or many codes.

    Tuple inputs use the medterm convention `(source, code)` — same as
    ``CodeRef.from_pair``. (The `(source, code)` order matches the rest of the
    public API: ``mt.lookup("SNOMEDCT_US", "44054006")``.)
    """
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    effective, _resolutions = effective_code_refs(
        normalized,
        engine=engine,
        resolve_mode=resolve_mode,
    )
    return engine.get_code_infos(effective)


def get_code_info(
    code: CodeRef | tuple[str, str],
    engine: LookupEngine,
    *,
    resolve_mode: str = "active_only",
) -> CodeInfo | None:
    """Look up one code through the batch contract."""
    return get_code_infos([code], engine=engine, resolve_mode=resolve_mode)[0]


def get_code_infos_prepared(
    codes: Sequence[CodeRef | tuple[str, str]],
    con,
) -> list[CodeInfo | None]:
    """Look up preferred atom info directly from prepared ``mt4ds.best_atoms``."""
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    lookups: dict[tuple[str, str], CodeInfo] = {}
    for source, source_codes in group_codes_by_source(normalized).items():
        for code, info in preferred_atom_lookup(con, source, source_codes).items():
            lookups[(source, code)] = info
    return [lookups.get((code.source, code.code)) for code in normalized]


def get_code_info_prepared(
    code: CodeRef | tuple[str, str],
    con,
) -> CodeInfo | None:
    """Look up one preferred atom from prepared ``mt4ds.best_atoms``."""
    return get_code_infos_prepared([code], con)[0]
