"""Exact terminology code lookup services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeInfo, CodeRef
from medterm4ds.engines.base import LookupEngine
from medterm4ds.services.resolution import effective_code_refs


def get_code_infos(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: LookupEngine,
    *,
    resolve_mode: str = "active_only",
) -> list[CodeInfo | None]:
    """Look up canonical atom info for one or many codes.

    Tuple inputs use the medterm convention `(code, source)`.
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
