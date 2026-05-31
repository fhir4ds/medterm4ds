"""Shared bulk iterators over terminology services."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from medterm4ds.core.models import (
    CodeInfo,
    CodeMapping,
    CodeRef,
    CodeRelation,
    FriendlyNameResult,
)
from medterm4ds.engines.base import (
    HierarchyEngine,
    LookupEngine,
    MappingEngine,
    PatientFriendlyEngine,
)
from medterm4ds.services.hierarchy import get_code_relations
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.mapping import get_code_mappings
from medterm4ds.services.patient_friendly import get_patient_friendly_names

CodeInput = CodeRef | tuple[str, str]


def iter_batches(values: Iterable[CodeInput], size: int) -> Iterator[list[CodeInput]]:
    """Yield fixed-size batches from an iterable."""
    if size < 1:
        raise ValueError("batch size must be at least 1")
    batch: list[CodeInput] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def iter_lookup_bulk(
    codes: Iterable[CodeInput],
    engine: LookupEngine,
    *,
    batch_size: int = 5000,
    include_missing: bool = True,
) -> Iterator[CodeInfo | dict[str, Any]]:
    """Yield lookup rows for bulk exports."""
    for batch in iter_batches(codes, batch_size):
        refs = _normalize_codes(batch)
        infos = get_code_infos(refs, engine=engine)
        for ref, info in zip(refs, infos, strict=True):
            if info is not None:
                yield info
            elif include_missing:
                yield _missing_code_info(ref)


def iter_mapping_bulk(
    codes: Iterable[CodeInput],
    engine: MappingEngine,
    *,
    target_sources: Sequence[str],
    batch_size: int = 5000,
    max_results_per_code: int = 50,
    max_depth: int = 0,
    include_target_ancestors: bool = False,
    include_target_descendants: bool = False,
) -> Iterator[CodeMapping]:
    """Yield mapping rows for bulk exports."""
    for batch in iter_batches(codes, batch_size):
        yield from get_code_mappings(
            batch,
            engine=engine,
            target_sources=target_sources,
            max_results_per_code=max_results_per_code,
            max_depth=max_depth,
            include_target_ancestors=include_target_ancestors,
            include_target_descendants=include_target_descendants,
        )


def iter_hierarchy_bulk(
    codes: Iterable[CodeInput],
    engine: HierarchyEngine,
    *,
    direction: str,
    batch_size: int = 5000,
    max_depth: int = 1,
) -> Iterator[CodeRelation]:
    """Yield hierarchy rows for bulk exports."""
    for batch in iter_batches(codes, batch_size):
        yield from get_code_relations(
            batch,
            engine=engine,
            direction=direction,
            max_depth=max_depth,
        )


def iter_patient_friendly_bulk(
    codes: Iterable[CodeInput],
    engine: PatientFriendlyEngine,
    *,
    batch_size: int = 5000,
    max_depth: int = 5,
) -> Iterator[FriendlyNameResult]:
    """Yield patient-friendly rows for bulk exports."""
    for batch in iter_batches(codes, batch_size):
        yield from get_patient_friendly_names(batch, engine=engine, max_depth=max_depth)


def _normalize_codes(codes: Sequence[CodeInput]) -> list[CodeRef]:
    return [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]


def _missing_code_info(ref: CodeRef) -> dict[str, Any]:
    return {
        "source": ref.source,
        "code": ref.code,
        "name": None,
        "cui": None,
        "aui": None,
        "tty": None,
        "suppress": None,
    }
