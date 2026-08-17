from __future__ import annotations

import csv
import json

import pytest

from medterm4ds import CodeMapping, CodeRef, FriendlyNameResult, Provenance, ProvenanceStep
from medterm4ds.outputs import to_records, write_csv, write_jsonl
from medterm4ds.services.conceptmap import (
    get_concept_map,
    get_mapping_concept_map,
    iter_concept_map,
)


class StaticEngine:
    def __init__(self, results: dict[tuple[str, str], FriendlyNameResult]):
        self.results = results
        self.calls: list[list[CodeRef]] = []

    def get_patient_friendly_names(
        self,
        codes: list[CodeRef],
        max_depth: int = 5,
    ) -> list[FriendlyNameResult]:
        self.calls.append(list(codes))
        return [self.results[(code.source, code.code)] for code in codes]


class StaticMappingEngine:
    def __init__(self, mappings: list[CodeMapping]):
        self.mappings = mappings
        self.calls: list[list[CodeRef]] = []

    def get_code_mappings(
        self,
        codes: list[CodeRef],
        *,
        target_sources: list[str] | tuple[str, ...],
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
    ) -> list[CodeMapping]:
        self.calls.append(list(codes))
        requested = {(code.source, code.code) for code in codes}
        return [
            mapping for mapping in self.mappings
            if (mapping.source.source, mapping.source.code) in requested
        ]


def _friendly(
    source: str,
    code: str,
    name: str,
    match_type: str,
    *,
    depth: int = 0,
) -> FriendlyNameResult:
    return FriendlyNameResult(
        code=CodeRef(source, code),
        name=name,
        friendly_source="CHV",
        match_type=match_type,
        match_depth=depth,
        technical_name=f"Technical {code}",
        matched_via=Provenance.from_steps(
            "test",
            [
                ProvenanceStep(op="input", source=source, code=code),
                ProvenanceStep(op="friendly_atom", source="CHV", name=name, depth=depth),
            ],
        ),
    )


def test_get_concept_map_batches_patient_friendly_rows():
    engine = StaticEngine(
        {
            ("ICD10CM", "E11.9"): _friendly("ICD10CM", "E11.9", "Diabetes", "exact"),
            ("SNOMEDCT_US", "123"): _friendly(
                "SNOMEDCT_US",
                "123",
                "Heart condition",
                "broader_exact",
                depth=1,
            ),
            ("CVX", "208"): _friendly("CVX", "208", "COVID-19 vaccine", "original"),
        }
    )

    rows = get_concept_map(
        [
            ("ICD10CM", "E11.9"),
            CodeRef("SNOMEDCT_US", "123"),
            ("CVX", "208"),
        ],
        engine=engine,
        batch_size=2,
    )

    assert [len(call) for call in engine.calls] == [2, 1]
    assert rows[0].source == CodeRef("ICD10CM", "E11.9")
    assert rows[0].target == CodeRef("PATIENT_FRIENDLY", "ICD10CM:E11.9")
    assert rows[0].relationship == "equivalent"
    assert rows[1].relationship == "source-is-narrower-than-target"
    assert rows[2].relationship == "not-translated"
    assert rows[1].matched_via.to_dict()["steps"][1]["op"] == "friendly_atom"


def test_iter_concept_map_validates_options():
    engine = StaticEngine({})

    with pytest.raises(ValueError, match="Unsupported ConceptMap target"):
        list(iter_concept_map([], engine=engine, target="anything_else"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="batch_size"):
        list(iter_concept_map([], engine=engine, batch_size=0))


def test_mapping_concept_map_preserves_mapping_provenance():
    mapping = CodeMapping(
        source=CodeRef("ICD10CM", "E11.9"),
        target=CodeRef("SNOMEDCT_US", "44054006"),
        relationship="equivalent",
        source_display="Type 2 diabetes mellitus",
        target_display="Diabetes mellitus type 2",
        match_type="same_cui",
        match_depth=0,
        matched_via=Provenance.from_steps(
            "same_cui",
            [
                ProvenanceStep(op="input_atom", source="ICD10CM", code="E11.9"),
                ProvenanceStep(
                    op="same_cui",
                    source="ICD10CM",
                    code="E11.9",
                    target_source="SNOMEDCT_US",
                    target_code="44054006",
                    cui="C_DIAB",
                ),
            ],
        ),
    )
    engine = StaticMappingEngine([mapping])

    rows = get_mapping_concept_map(
        [CodeRef("ICD10CM", "E11.9")],
        engine=engine,
        target_sources=["SNOMEDCT_US"],
    )

    assert rows[0].source == CodeRef("ICD10CM", "E11.9")
    assert rows[0].target == CodeRef("SNOMEDCT_US", "44054006")
    assert rows[0].match_type == "same_cui"
    assert rows[0].match_depth == 0
    assert rows[0].matched_via.to_dict()["steps"][1]["op"] == "same_cui"


def test_output_helpers_write_concept_map_records(tmp_path):
    row = get_concept_map(
        [("ICD10CM", "E11.9")],
        engine=StaticEngine(
            {
                ("ICD10CM", "E11.9"): _friendly("ICD10CM", "E11.9", "Diabetes", "exact"),
            }
        ),
    )[0]

    records = to_records([row])
    assert records[0]["target_display"] == "Diabetes"
    assert records[0]["matched_via"]["strategy"] == "test"

    jsonl_path = write_jsonl([row], tmp_path / "conceptmap.jsonl")
    jsonl_record = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
    assert jsonl_record["target_code"] == "ICD10CM:E11.9"

    csv_path = write_csv([row], tmp_path / "conceptmap.csv")
    with csv_path.open(encoding="utf-8", newline="") as file:
        csv_record = next(csv.DictReader(file))
    assert csv_record["target_display"] == "Diabetes"
    assert json.loads(csv_record["matched_via"])["strategy"] == "test"


# =============================================================================
# Regression: conceptmap_relationship dispatch on (match_type, match_depth).
# Found by QC-074 / QC-081 / QC-094 / QC-095 (CRITICAL x3 + HIGH): pre-fix,
# conceptmap_relationship only checked match_type.startswith('broader'),
# mislabeling snomed_fallback / snomed_to_target_* / group depth>0 /
# ingredient depth>0 / cvx_group as 'equivalent' (100K+ production rows
# affected, including FHIR ConceptMap export's equivalence field).
# =============================================================================


def test_conceptmap_relationship_dispatches_on_match_type_and_depth():
    """conceptmap_relationship returns the clinically-correct relationship.

    The fix dispatches on (match_type, match_depth):
      * depth>0 always means the friendly name is broader (ancestor, generic
        group, disease family) -> 'source-is-narrower-than-target'.
      * depth==0 with a depth-self-hit match type (exact, same_cui, group,
        ingredient, cvx_group) -> 'equivalent'.
      * broader* -> 'source-is-narrower-than-target' (preserved from pre-fix).
      * component / first_axis / loinc_common -> 'related-to'.
      * original -> 'not-translated'.
      * None / 'none' -> 'unmatched'.
    """
    from medterm4ds.core.models import conceptmap_relationship as f

    # depth>0 hierarchical / fallback / TTY-traversal cases — the bug.
    assert f("snomed_fallback", match_depth=4) == "source-is-narrower-than-target"
    assert f("snomed_to_target_native_hierarchy", match_depth=4) == "source-is-narrower-than-target"
    assert f("snomed_to_target_snomed_fallback", match_depth=4) == "source-is-narrower-than-target"
    assert f("group", match_depth=2) == "source-is-narrower-than-target"
    assert f("ingredient", match_depth=1) == "source-is-narrower-than-target"
    assert f("cvx_group", match_depth=1) == "source-is-narrower-than-target"
    assert f("broader", match_depth=1) == "source-is-narrower-than-target"

    # depth==0 self-hit cases — equivalent.
    assert f("exact", match_depth=0) == "equivalent"
    assert f("same_cui", match_depth=0) == "equivalent"
    assert f("group", match_depth=0) == "equivalent"
    assert f("ingredient", match_depth=0) == "equivalent"
    assert f("cvx_group", match_depth=0) == "equivalent"

    # Other paths preserved.
    assert f("original", match_depth=0) == "not-translated"
    assert f("component", match_depth=0) == "related-to"
    assert f("first_axis", match_depth=0) == "related-to"
    assert f("loinc_common", match_depth=0) == "related-to"
    assert f(None, match_depth=0) == "unmatched"
    assert f("none", match_depth=0) == "unmatched"


def test_conceptmap_row_from_friendly_result_uses_match_depth():
    """ConceptMapRow.from_friendly_result passes match_depth so depth>0
    friendly results are correctly labeled 'source-is-narrower-than-target'.

    Regression for QC-074/QC-094 (CRITICAL): pre-fix, a snomed_fallback
    FriendlyNameResult at depth=4 produced relationship='equivalent'.
    Post-fix, it produces 'source-is-narrower-than-target'.
    """
    from medterm4ds import ConceptMapRow

    # depth>0 snomed_fallback result — must NOT be 'equivalent'.
    result_depth4 = FriendlyNameResult(
        code=CodeRef("ICD10CM", "C83.81"),
        name="Blood Disorders",
        friendly_source="MEDLINEPLUS",
        match_type="snomed_fallback",
        match_depth=4,
        technical_name="Other non-follicular lymphoma",
    )
    row = ConceptMapRow.from_friendly_result(result_depth4)
    assert row.relationship == "source-is-narrower-than-target", (
        f"snomed_fallback at depth=4 should be 'source-is-narrower-than-target', "
        f"got {row.relationship!r}"
    )

    # depth==0 exact result — equivalent.
    result_depth0 = FriendlyNameResult(
        code=CodeRef("ICD10CM", "E11.9"),
        name="Type 2 Diabetes",
        friendly_source="MEDLINEPLUS",
        match_type="exact",
        match_depth=0,
    )
    row0 = ConceptMapRow.from_friendly_result(result_depth0)
    assert row0.relationship == "equivalent"
