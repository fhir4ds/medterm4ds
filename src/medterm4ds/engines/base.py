"""Engine protocols."""

from __future__ import annotations

from typing import Protocol, Sequence

from medterm4ds.core.models import CodeRef, FriendlyNameResult


class PatientFriendlyEngine(Protocol):
    """Batch-first patient-friendly name engine."""

    def get_patient_friendly_names(
        self,
        codes: Sequence[CodeRef],
        max_depth: int = 5,
    ) -> list[FriendlyNameResult]:
        """Resolve patient-friendly names for one or many codes."""
        ...


class TerminologyEngine(PatientFriendlyEngine, Protocol):
    """Shared terminology engine contract for services.

    Engines may be local, remote, or test doubles. Service modules should depend
    on this protocol instead of a concrete execution mode.
    """
