"""Batch-first patient-friendly service."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, FriendlyNameResult
from medterm4ds.engines.base import PatientFriendlyEngine
from medterm4ds.services.resolution import effective_code_refs


def get_patient_friendly_names(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: PatientFriendlyEngine,
    max_depth: int = 5,
    resolve_mode: str = "active_only",
) -> list[FriendlyNameResult]:
    """Resolve patient-friendly names for one or many codes.

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
