"""Batch-first patient-friendly service."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, FriendlyNameResult
from medterm4ds.core.normalize import validate_code_nonempty, validate_source_sab
from medterm4ds.engines.base import PatientFriendlyEngine
from medterm4ds.services.resolution import effective_code_refs

_logger = logging.getLogger(__name__)


def get_patient_friendly_names(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: PatientFriendlyEngine,
    max_depth: int = 5,
    resolve_mode: str = "active_only",
) -> list[FriendlyNameResult]:
    """Resolve patient-friendly names for one or many codes.

    Tuple inputs use the medterm convention `(source, code)` — same as
    ``CodeRef.from_pair``.
    """
    # QC-076/QC-077/QC-082 (MEDIUM): validate max_depth type BEFORE the
    # engine coerces it via int() — string values silently coerced, None
    # leaked a raw TypeError from int(None). Sibling of EC-03 FIX-007
    # (hierarchy) and EC-02 FIX-005 (mapping).
    if not isinstance(max_depth, int) or isinstance(max_depth, bool):
        raise TypeError(
            f"max_depth must be int, got {type(max_depth).__name__}"
        )
    # QC-075/QC-082 (MEDIUM): max_depth < 0 is a programming bug.
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    # QC-075/QC-082 (MEDIUM): max_depth == 0 silently skips the broader
    # walk, returning only exact/same_cui matches (or 'original' for
    # codes with no direct hit). This is a valid use case (caller wants
    # only direct hits), but the silent degradation was the bug — surface
    # a WARNING so the caller knows the broader walk was skipped.
    if max_depth == 0:
        _logger.warning(
            "patient_friendly called with max_depth=0: broader walk is "
            "skipped, only exact/same_cui matches will be returned"
        )
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    # QC-422 (MEDIUM): URI-form source previously produced a success-shaped
    # row whose name was the raw code (CLI/MCP reject the same input). Guard
    # at the service boundary, same as lookup/mapping.
    for ref in normalized:
        validate_source_sab(ref.source)
        validate_code_nonempty(str(ref.code))
    effective, _resolutions = effective_code_refs(
        normalized,
        engine=engine,
        resolve_mode=resolve_mode,
    )
    return engine.get_patient_friendly_names(effective, max_depth=max_depth)


def get_patient_friendly_name(
    code: CodeRef | tuple[str, str],
    engine: PatientFriendlyEngine,
    max_depth: int = 5,
    resolve_mode: str = "active_only",
) -> FriendlyNameResult:
    """Resolve one patient-friendly name through the batch contract."""
    results = get_patient_friendly_names(
        [code],
        engine=engine,
        max_depth=max_depth,
        resolve_mode=resolve_mode,
    )
    return results[0]
