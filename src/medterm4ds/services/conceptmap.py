"""ConceptMap services built on shared terminology engines."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Literal

from medterm4ds.core.models import CodeRef, ConceptMapRow
from medterm4ds.engines.base import MappingEngine, PatientFriendlyEngine
from medterm4ds.services.bulk import iter_batches
from medterm4ds.services.mapping import get_code_mappings
from medterm4ds.services.patient_friendly import get_patient_friendly_names

ConceptMapTarget = Literal["patient_friendly"]


def iter_concept_map(
    codes: Iterable[CodeRef | tuple[str, str]],
    engine: PatientFriendlyEngine,
    *,
    target: ConceptMapTarget = "patient_friendly",
    batch_size: int = 5000,
    max_depth: int = 5,
    target_source: str = "PATIENT_FRIENDLY",
) -> Iterator[ConceptMapRow]:
    """Yield ConceptMap rows without requiring all codes in memory."""
    if target != "patient_friendly":
        raise ValueError(f"Unsupported ConceptMap target: {target}")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    # QC-421 (LOW): target_source is the ConceptMap target system — an
    # empty/whitespace value previously flowed into every row
    # (resource.target.uri='' downstream). Reject like every other
    # empty-string input.
    if not target_source.strip():
        raise ValueError(
            f"target_source must be a non-empty name, got {target_source!r}"
        )

    for batch in iter_batches(codes, batch_size):
        results = get_patient_friendly_names(batch, engine=engine, max_depth=max_depth)
        for result in results:
            yield ConceptMapRow.from_friendly_result(result, target_source=target_source)


def get_concept_map(
    codes: Iterable[CodeRef | tuple[str, str]],
    engine: PatientFriendlyEngine,
    *,
    target: ConceptMapTarget = "patient_friendly",
    batch_size: int = 5000,
    max_depth: int = 5,
    target_source: str = "PATIENT_FRIENDLY",
) -> list[ConceptMapRow]:
    """Return ConceptMap rows for code collections that fit in memory."""
    return list(
        iter_concept_map(
            codes,
            engine=engine,
            target=target,
            batch_size=batch_size,
            max_depth=max_depth,
            target_source=target_source,
        )
    )


def iter_mapping_concept_map(
    codes: Iterable[CodeRef | tuple[str, str]],
    engine: MappingEngine,
    *,
    target_sources: list[str] | tuple[str, ...],
    batch_size: int = 5000,
    max_results_per_code: int = 50,
    max_depth: int = 0,
    include_target_ancestors: bool = False,
    include_target_descendants: bool = False,
) -> Iterator[ConceptMapRow]:
    """Yield ConceptMap rows for source-to-target code mappings."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    for batch in iter_batches(codes, batch_size):
        mappings = get_code_mappings(
            batch,
            engine=engine,
            target_sources=target_sources,
            max_results_per_code=max_results_per_code,
            max_depth=max_depth,
            include_target_ancestors=include_target_ancestors,
            include_target_descendants=include_target_descendants,
        )
        for mapping in mappings:
            yield ConceptMapRow.from_mapping(mapping)


def get_mapping_concept_map(
    codes: Iterable[CodeRef | tuple[str, str]],
    engine: MappingEngine,
    *,
    target_sources: list[str] | tuple[str, ...],
    batch_size: int = 5000,
    max_results_per_code: int = 50,
    max_depth: int = 0,
    include_target_ancestors: bool = False,
    include_target_descendants: bool = False,
) -> list[ConceptMapRow]:
    """Return ConceptMap rows for source-to-target code mappings."""
    return list(
        iter_mapping_concept_map(
            codes,
            engine=engine,
            target_sources=target_sources,
            batch_size=batch_size,
            max_results_per_code=max_results_per_code,
            max_depth=max_depth,
            include_target_ancestors=include_target_ancestors,
            include_target_descendants=include_target_descendants,
        )
    )
