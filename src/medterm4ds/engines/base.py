"""Engine protocols."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from medterm4ds.core.models import CodeInfo, CodeRef, FriendlyNameResult


class LookupEngine(Protocol):
    """Batch-first exact code lookup engine."""

    def get_code_infos(
        self,
        codes: Sequence[CodeRef],
    ) -> list[CodeInfo | None]:
        """Return one code info row per input code, preserving order."""
        ...


class PatientFriendlyEngine(Protocol):
    """Batch-first patient-friendly name engine."""

    def get_patient_friendly_names(
        self,
        codes: Sequence[CodeRef],
        max_depth: int = 5,
    ) -> list[FriendlyNameResult]:
        """Resolve patient-friendly names for one or many codes."""
        ...


class TerminologyEngine(LookupEngine, PatientFriendlyEngine, Protocol):
    """Shared terminology engine contract for services.

    Engines may be local, remote, or test doubles. Service modules should depend
    on this protocol instead of a concrete execution mode.
    """
