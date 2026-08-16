from __future__ import annotations

import pytest

from medterm4ds import CodeInfo, CodeMapping, CodeRef, CodeRelation, FriendlyNameResult
from medterm4ds.services.bulk import (
    iter_batches,
    iter_hierarchy_bulk,
    iter_lookup_bulk,
    iter_mapping_bulk,
    iter_patient_friendly_bulk,
)


class StaticEngine:
    def __init__(self):
        self.lookup_batches: list[list[CodeRef]] = []
        self.mapping_batches: list[list[CodeRef]] = []
        self.hierarchy_batches: list[list[CodeRef]] = []
        self.friendly_batches: list[list[CodeRef]] = []

    def get_code_infos(self, codes):
        self.lookup_batches.append(list(codes))
        return [
            CodeInfo(code=code, name=f"Name {code.code}") if code.code != "NOPE" else None
            for code in codes
        ]

    def get_code_mappings(
        self,
        codes,
        *,
        target_sources,
        max_results_per_code=50,
        max_depth=0,
        include_target_ancestors=False,
        include_target_descendants=False,
    ):
        self.mapping_batches.append(list(codes))
        return [
            CodeMapping(
                source=code,
                target=CodeRef(target_sources[0], f"T-{code.code}"),
                relationship="equivalent",
                match_type="same_cui",
            )
            for code in codes
        ]

    def get_code_relations(self, codes, *, direction, max_depth=1, limit=None, include_retired=False):
        self.hierarchy_batches.append(list(codes))
        return [
            CodeRelation(
                source=code,
                target=CodeRef(code.source, f"P-{code.code}"),
                relationship="parent",
            )
            for code in codes
        ]

    def get_patient_friendly_names(self, codes, max_depth=5):
        self.friendly_batches.append(list(codes))
        return [
            FriendlyNameResult(
                code=code,
                name=f"Friendly {code.code}",
                friendly_source="TEST",
                match_type="exact",
            )
            for code in codes
        ]


def test_iter_batches_validates_and_chunks_iterables():
    assert list(iter_batches([1, 2, 3], 2)) == [[1, 2], [3]]

    with pytest.raises(ValueError, match="batch size"):
        list(iter_batches([], 0))


def test_bulk_iterators_reuse_service_contracts():
    engine = StaticEngine()
    codes = [CodeRef("ICD10CM", "A"), CodeRef("ICD10CM", "NOPE"), CodeRef("CVX", "C")]

    lookup_rows = list(iter_lookup_bulk(codes, engine=engine, batch_size=2))
    mapping_rows = list(
        iter_mapping_bulk(
            codes,
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            batch_size=2,
        )
    )
    hierarchy_rows = list(
        iter_hierarchy_bulk(codes, engine=engine, direction="parents", batch_size=2)
    )
    friendly_rows = list(iter_patient_friendly_bulk(codes, engine=engine, batch_size=2))

    assert [len(batch) for batch in engine.lookup_batches] == [2, 1]
    assert lookup_rows[1] == {
        "source": "ICD10CM",
        "code": "NOPE",
        "name": None,
        "cui": None,
        "aui": None,
        "tty": None,
        "suppress": None,
    }
    assert mapping_rows[0].target == CodeRef("SNOMEDCT_US", "T-A")
    assert hierarchy_rows[0].target == CodeRef("ICD10CM", "P-A")
    assert friendly_rows[0].name == "Friendly A"
