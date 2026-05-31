"""DataFrame-friendly service wrappers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from medterm4ds.core.models import CodeRef
from medterm4ds.engines.base import (
    DiscoveryEngine,
    HierarchyEngine,
    MappingEngine,
    PatientFriendlyEngine,
)
from medterm4ds.outputs import to_dataframe
from medterm4ds.services.conceptmap import get_concept_map, get_mapping_concept_map
from medterm4ds.services.discovery import (
    get_code_ttys,
    get_source_stats,
    sample_source_codes,
    search_names,
)
from medterm4ds.services.hierarchy import get_code_relations
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.mapping import get_code_mappings
from medterm4ds.services.patient_friendly import get_patient_friendly_names

DataFrameBackend = Literal["pandas", "polars"]
CodeInput = CodeRef | tuple[str, str]


def lookup_dataframe(
    codes: Sequence[CodeInput],
    *,
    engine,
    backend: DataFrameBackend = "pandas",
):
    """Look up codes and return a pandas or Polars DataFrame."""
    refs = _normalize_codes(codes)
    infos = get_code_infos(refs, engine=engine)
    rows = [
        info.to_dict() if info else _missing_code_info(ref)
        for ref, info in zip(refs, infos, strict=True)
    ]
    return to_dataframe(rows, backend=backend)


def map_dataframe(
    codes: Sequence[CodeInput],
    *,
    engine: MappingEngine,
    target_sources: Sequence[str],
    max_results_per_code: int = 50,
    max_depth: int = 0,
    include_target_ancestors: bool = False,
    include_target_descendants: bool = False,
    backend: DataFrameBackend = "pandas",
):
    """Map codes to target sources and return a pandas or Polars DataFrame."""
    rows = get_code_mappings(
        codes,
        engine=engine,
        target_sources=target_sources,
        max_results_per_code=max_results_per_code,
        max_depth=max_depth,
        include_target_ancestors=include_target_ancestors,
        include_target_descendants=include_target_descendants,
    )
    return to_dataframe(rows, backend=backend)


def hierarchy_dataframe(
    codes: Sequence[CodeInput],
    *,
    engine: HierarchyEngine,
    direction: str,
    max_depth: int = 1,
    backend: DataFrameBackend = "pandas",
):
    """Traverse code hierarchy and return a pandas or Polars DataFrame."""
    rows = get_code_relations(codes, engine=engine, direction=direction, max_depth=max_depth)
    return to_dataframe(rows, backend=backend)


def patient_friendly_dataframe(
    codes: Sequence[CodeInput],
    *,
    engine: PatientFriendlyEngine,
    max_depth: int = 5,
    backend: DataFrameBackend = "pandas",
):
    """Resolve patient-friendly names and return a pandas or Polars DataFrame."""
    rows = get_patient_friendly_names(codes, engine=engine, max_depth=max_depth)
    return to_dataframe(rows, backend=backend)


def conceptmap_dataframe(
    codes: Sequence[CodeInput],
    *,
    engine: PatientFriendlyEngine,
    batch_size: int = 5000,
    max_depth: int = 5,
    target_source: str = "PATIENT_FRIENDLY",
    backend: DataFrameBackend = "pandas",
):
    """Generate patient-friendly ConceptMap rows as a DataFrame."""
    rows = get_concept_map(
        codes,
        engine=engine,
        batch_size=batch_size,
        max_depth=max_depth,
        target_source=target_source,
    )
    return to_dataframe(rows, backend=backend)


def mapping_conceptmap_dataframe(
    codes: Sequence[CodeInput],
    *,
    engine: MappingEngine,
    target_sources: Sequence[str],
    batch_size: int = 5000,
    max_results_per_code: int = 50,
    max_depth: int = 0,
    include_target_ancestors: bool = False,
    include_target_descendants: bool = False,
    backend: DataFrameBackend = "pandas",
):
    """Generate source-to-target ConceptMap rows as a DataFrame."""
    rows = get_mapping_concept_map(
        codes,
        engine=engine,
        target_sources=target_sources,
        batch_size=batch_size,
        max_results_per_code=max_results_per_code,
        max_depth=max_depth,
        include_target_ancestors=include_target_ancestors,
        include_target_descendants=include_target_descendants,
    )
    return to_dataframe(rows, backend=backend)


def source_stats_dataframe(
    *,
    engine: DiscoveryEngine,
    sources: Sequence[str] | str | None = None,
    backend: DataFrameBackend = "pandas",
):
    """Return source statistics as a DataFrame."""
    rows = get_source_stats(engine=engine, sources=sources)
    return to_dataframe(rows, backend=backend)


def sample_codes_dataframe(
    *,
    engine: DiscoveryEngine,
    sources: Sequence[str] | str | None = None,
    per_source: int = 10,
    backend: DataFrameBackend = "pandas",
):
    """Return sampled source codes as a DataFrame."""
    rows = sample_source_codes(engine=engine, sources=sources, per_source=per_source)
    return to_dataframe(({"source": row.source, "code": row.code} for row in rows), backend=backend)


def code_ttys_dataframe(
    codes: Sequence[CodeInput],
    *,
    engine: DiscoveryEngine,
    backend: DataFrameBackend = "pandas",
):
    """Return code atoms and TTYs as a DataFrame."""
    rows = get_code_ttys(codes, engine=engine)
    return to_dataframe(rows, backend=backend)


def search_names_dataframe(
    query: str,
    *,
    engine: DiscoveryEngine,
    sources: Sequence[str] | str | None = None,
    tty_filters: Sequence[str] | str | None = None,
    limit: int = 25,
    backend: DataFrameBackend = "pandas",
):
    """Return terminology name search results as a DataFrame."""
    rows = search_names(
        query,
        engine=engine,
        sources=sources,
        tty_filters=tty_filters,
        limit=limit,
    )
    return to_dataframe(rows, backend=backend)


def _normalize_codes(codes: Sequence[CodeInput]) -> list[CodeRef]:
    return [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]


def _missing_code_info(ref: CodeRef) -> dict[str, object]:
    return {
        "source": ref.source,
        "code": ref.code,
        "name": None,
        "cui": None,
        "aui": None,
        "tty": None,
        "suppress": None,
    }


__all__ = [
    "code_ttys_dataframe",
    "conceptmap_dataframe",
    "hierarchy_dataframe",
    "lookup_dataframe",
    "map_dataframe",
    "mapping_conceptmap_dataframe",
    "patient_friendly_dataframe",
    "sample_codes_dataframe",
    "search_names_dataframe",
    "source_stats_dataframe",
]
