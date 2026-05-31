"""ConceptMap services built on shared terminology engines."""

from __future__ import annotations

from typing import Iterable, Iterator, Literal

from medterm4ds.core.models import CodeRef, ConceptMapRow
from medterm4ds.engines.base import TerminologyEngine
from medterm4ds.services.patient_friendly import get_patient_friendly_names

ConceptMapTarget = Literal["patient_friendly"]


def iter_concept_map(
    codes: Iterable[CodeRef | tuple[str, str]],
    engine: TerminologyEngine,
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

    for batch in _batched(codes, batch_size):
        results = get_patient_friendly_names(batch, engine=engine, max_depth=max_depth)
        for result in results:
            yield ConceptMapRow.from_friendly_result(result, target_source=target_source)


def get_concept_map(
    codes: Iterable[CodeRef | tuple[str, str]],
    engine: TerminologyEngine,
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


def _batched(
    values: Iterable[CodeRef | tuple[str, str]],
    size: int,
) -> Iterator[list[CodeRef | tuple[str, str]]]:
    batch: list[CodeRef | tuple[str, str]] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
