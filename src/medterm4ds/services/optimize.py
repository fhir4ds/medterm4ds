"""Valueset optimization services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, OptimizeResult
from medterm4ds.engines.base import OptimizeEngine


def optimize_codes(
    codes: Sequence[CodeRef | tuple[str, str]],
    *,
    engine: OptimizeEngine,
    source: str | None = None,
    relationship: str | None = None,
    output_format: str = "compact",
    include_codes: bool = False,
) -> OptimizeResult:
    """Compact codes into hierarchy include/exclude rules."""
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    if source is not None:
        normalized = [CodeRef(source=source, code=ref.code) for ref in normalized]
    return engine.optimize_codes(
        normalized,
        relationship=relationship,
        output_format=output_format,
        include_codes=include_codes,
    )
