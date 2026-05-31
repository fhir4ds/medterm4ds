"""Source-to-source terminology mapping services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeMapping, CodeRef
from medterm4ds.core.normalize import normalize_source
from medterm4ds.engines.base import MappingEngine
from medterm4ds.services.resolution import effective_code_refs


def get_code_mappings(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: MappingEngine,
    *,
    target_sources: Sequence[str],
    max_results_per_code: int = 50,
    max_depth: int = 0,
    include_target_ancestors: bool = False,
    include_target_descendants: bool = False,
    resolve_mode: str = "active_only",
) -> list[CodeMapping]:
    """Return same-CUI active target mappings for one or many codes."""
    if max_results_per_code < 1:
        raise ValueError("max_results_per_code must be at least 1")
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    normalized_codes = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    normalized_targets = tuple(dict.fromkeys(normalize_source(source) for source in target_sources))
    if not normalized_targets:
        raise ValueError("target_sources must not be empty")
    effective_codes, _resolutions = effective_code_refs(
        normalized_codes,
        engine=engine,
        resolve_mode=resolve_mode,
    )
    return engine.get_code_mappings(
        effective_codes,
        target_sources=normalized_targets,
        max_results_per_code=max_results_per_code,
        max_depth=max_depth,
        include_target_ancestors=include_target_ancestors,
        include_target_descendants=include_target_descendants,
    )
