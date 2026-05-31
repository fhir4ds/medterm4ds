"""Source-to-source terminology mapping services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeMapping, CodeRef
from medterm4ds.core.normalize import normalize_source
from medterm4ds.engines.base import MappingEngine


def get_code_mappings(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: MappingEngine,
    *,
    target_sources: Sequence[str],
    max_results_per_code: int = 50,
) -> list[CodeMapping]:
    """Return same-CUI active target mappings for one or many codes."""
    if max_results_per_code < 1:
        raise ValueError("max_results_per_code must be at least 1")
    normalized_codes = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    normalized_targets = tuple(dict.fromkeys(normalize_source(source) for source in target_sources))
    if not normalized_targets:
        raise ValueError("target_sources must not be empty")
    return engine.get_code_mappings(
        normalized_codes,
        target_sources=normalized_targets,
        max_results_per_code=max_results_per_code,
    )
