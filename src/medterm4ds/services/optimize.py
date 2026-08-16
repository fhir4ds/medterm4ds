"""Valueset optimization services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, OptimizeResult
from medterm4ds.core.normalize import normalize_source
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
        # QC-199 (MEDIUM): the source override used to silently re-label
        # every code to the override source — a mixed-source valueset like
        # [(SNOMEDCT_US, 44054006), (ICD10CM, E11)] with source='SNOMEDCT_US'
        # ran as (SNOMEDCT_US, E11), burning minutes of SNOMED queries that
        # find no edges. Reject the mismatch instead.
        override = normalize_source(source)
        foreign = sorted({ref.source for ref in normalized if ref.source != override})
        if foreign:
            raise ValueError(
                f"source {source!r} conflicts with codes already sourced from "
                f"{', '.join(repr(s) for s in foreign)}; optimize_codes requires "
                f"all codes to use the same source (drop the source override)"
            )
        normalized = [CodeRef(source=override, code=ref.code) for ref in normalized]
    return engine.optimize_codes(
        normalized,
        relationship=relationship,
        output_format=output_format,
        include_codes=include_codes,
    )
