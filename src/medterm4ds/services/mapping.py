"""Source-to-source terminology mapping services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeMapping, CodeRef
from medterm4ds.core.normalize import (
    normalize_source,
    validate_code_nonempty,
    validate_source_sab,
)
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
    if not isinstance(max_results_per_code, int) or isinstance(max_results_per_code, bool):
        raise TypeError(
            f"max_results_per_code must be int, got {type(max_results_per_code).__name__}"
        )
    if max_results_per_code < 1:
        raise ValueError("max_results_per_code must be at least 1")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool):
        raise TypeError(
            f"max_depth must be int, got {type(max_depth).__name__}"
        )
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    if target_sources is None:
        raise ValueError("target_sources must not be empty")
    if not isinstance(target_sources, Sequence) or isinstance(target_sources, (str, bytes)):
        raise TypeError(
            f"target_sources must be a sequence of strings, got {type(target_sources).__name__}"
        )
    normalized_codes = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    # QC-422 (MEDIUM): same service-boundary guard as lookup — empty/URI-form
    # source or empty code previously produced a silent 0-row success on the
    # Python facade while CLI/MCP rejected the identical input.
    for ref in normalized_codes:
        validate_source_sab(ref.source)
        validate_code_nonempty(str(ref.code))
    normalized_targets = tuple(dict.fromkeys(normalize_source(source) for source in target_sources))
    if not normalized_targets:
        raise ValueError("target_sources must not be empty")
    # QC-431 (LOW): strip-based emptiness — normalize_source(' ') returns
    # ' ' unchanged, so a truthiness-only check let a whitespace target
    # through to a silent 0-row result on Python/MCP (CLI rejected it).
    invalid = [t for t in normalized_targets if not (t and t.strip())]
    if invalid:
        raise ValueError(
            "target_sources contains empty or None entries after normalization; "
            f"invalid values: {invalid!r}"
        )
    # QC-414 (MEDIUM): a FHIR system URI in target_sources silently
    # returned [] — indistinguishable from 'code has no mapping'. Reject the
    # wrong-surface form with the parameter named, mirroring the input-side
    # QC-322/389 guards. (Unknown-but-well-formed SABs are validated against
    # the database by the DuckDB engine — see _MappingOps.get_code_mappings.)
    uri_targets = [
        t for t in normalized_targets
        if "://" in t or t.lower().startswith("urn:oid:")
    ]
    if uri_targets:
        raise ValueError(
            "target_sources expects UMLS SAB strings (e.g. ICD10CM), got "
            f"{uri_targets!r} (looks like URI/OID). FHIR system URIs are not "
            "accepted here; use the SAB form."
        )
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

