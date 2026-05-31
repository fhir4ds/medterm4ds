"""Batch-first patient-friendly service."""

from __future__ import annotations

from typing import Sequence

from medterm4ds.core.models import CodeRef, FriendlyNameResult
from medterm4ds.engines.base import TerminologyEngine


def get_patient_friendly_names(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: TerminologyEngine,
    max_depth: int = 5,
) -> list[FriendlyNameResult]:
    """Resolve patient-friendly names for one or many codes.

    Tuple inputs use the medterm convention `(code, source)`.
    """
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    return engine.get_patient_friendly_names(normalized, max_depth=max_depth)


def get_patient_friendly_name(
    code: CodeRef | tuple[str, str],
    engine: TerminologyEngine,
    max_depth: int = 5,
) -> FriendlyNameResult:
    """Resolve one patient-friendly name through the batch contract."""
    results = get_patient_friendly_names([code], engine=engine, max_depth=max_depth)
    return results[0]
