"""Terminology inventory and name discovery services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeInfo, CodeRef, NameSearchResult, SourceStats
from medterm4ds.engines.base import DiscoveryEngine
from medterm4ds.services.inventory import normalize_sources


def get_source_stats(
    engine: DiscoveryEngine,
    *,
    sources: Sequence[str] | str | None = None,
) -> list[SourceStats]:
    """Return code and atom counts by source."""
    normalized = normalize_sources(sources) if sources is not None else None
    return engine.get_source_stats(normalized)


def sample_source_codes(
    engine: DiscoveryEngine,
    *,
    sources: Sequence[str] | str | None = None,
    per_source: int = 10,
) -> list[CodeRef]:
    """Return sample active codes by source."""
    if per_source < 1:
        raise ValueError("per_source must be at least 1")
    normalized = normalize_sources(sources)
    return engine.sample_source_codes(normalized, per_source=per_source)


def get_code_ttys(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: DiscoveryEngine,
) -> list[CodeInfo]:
    """Return active atoms and TTYs for one or many codes."""
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    return engine.get_code_ttys(normalized)


def search_names(
    query: str,
    engine: DiscoveryEngine,
    *,
    sources: Sequence[str] | str | None = None,
    tty_filters: Sequence[str] | str | None = None,
    limit: int = 25,
) -> list[NameSearchResult]:
    """Search active terminology names."""
    if not query.strip():
        raise ValueError("query must not be empty")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    normalized_sources = normalize_sources(sources) if sources is not None else None
    normalized_ttys = _normalize_ttys(tty_filters)
    return engine.search_names(
        query,
        sources=normalized_sources,
        tty_filters=normalized_ttys,
        limit=limit,
    )


def _normalize_ttys(values: Sequence[str] | str | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    if isinstance(values, str):
        raw_values = [part.strip() for part in values.split(",")]
    else:
        raw_values = [str(value).strip() for value in values]
    normalized = [value.upper() for value in raw_values if value]
    return tuple(dict.fromkeys(normalized))
