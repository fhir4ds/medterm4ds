"""Engine protocols."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from medterm4ds.core.models import (
    CodeInfo,
    CodeMapping,
    CodeRef,
    CodeRelation,
    CodeResolution,
    FriendlyNameResult,
    NameSearchResult,
    OptimizeResult,
    SourceStats,
)

HierarchyDirection = Literal["parents", "children", "ancestors", "descendants"]


class LookupEngine(Protocol):
    """Batch-first exact code lookup engine."""

    def get_code_infos(
        self,
        codes: Sequence[CodeRef],
    ) -> list[CodeInfo | None]:
        """Return one code info row per input code, preserving order."""
        ...


class HierarchyEngine(Protocol):
    """Batch-first hierarchy traversal engine."""

    def get_code_relations(
        self,
        codes: Sequence[CodeRef],
        *,
        direction: HierarchyDirection,
        max_depth: int = 1,
        limit: int | None = None,
    ) -> list[CodeRelation]:
        """Return hierarchical relationship rows for input codes."""
        ...


class MappingEngine(Protocol):
    """Batch-first source-to-source mapping engine."""

    def get_code_mappings(
        self,
        codes: Sequence[CodeRef],
        *,
        target_sources: Sequence[str],
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
    ) -> list[CodeMapping]:
        """Return target mappings for input codes."""
        ...


class DiscoveryEngine(Protocol):
    """Batch-first terminology inventory and search engine."""

    def get_source_stats(self, sources: Sequence[str] | None = None) -> list[SourceStats]:
        """Return source inventory statistics."""
        ...

    def sample_source_codes(
        self,
        sources: Sequence[str],
        *,
        per_source: int = 10,
    ) -> list[CodeRef]:
        """Return sample active codes by source."""
        ...

    def get_code_ttys(self, codes: Sequence[CodeRef]) -> list[CodeInfo]:
        """Return active atoms/TTYs for input codes."""
        ...

    def search_names(
        self,
        query: str,
        *,
        sources: Sequence[str] | None = None,
        tty_filters: Sequence[str] | None = None,
        limit: int = 25,
    ) -> list[NameSearchResult]:
        """Search active atom names."""
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


class ResolutionEngine(Protocol):
    """Batch-first input-code resolution engine."""

    def resolve_codes(self, codes: Sequence[CodeRef]) -> list[CodeResolution]:
        """Resolve active, historical, obsolete, and NDC code inputs."""
        ...


class OptimizeEngine(Protocol):
    """Hierarchy-backed valueset optimization engine."""

    def optimize_codes(
        self,
        codes: Sequence[CodeRef],
        *,
        relationship: str | None = None,
        output_format: str = "compact",
        include_codes: bool = False,
    ) -> OptimizeResult:
        """Compact a code list into include/exclude hierarchy rules."""
        ...


class TerminologyEngine(
    LookupEngine,
    HierarchyEngine,
    MappingEngine,
    DiscoveryEngine,
    PatientFriendlyEngine,
    ResolutionEngine,
    OptimizeEngine,
    Protocol,
):
    """Shared terminology engine contract for services.

    Engines may be local, remote, or test doubles. Service modules should depend
    on this protocol instead of a concrete execution mode.
    """
