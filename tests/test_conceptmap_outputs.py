from __future__ import annotations

import csv
import json

import pytest

from medterm4ds import CodeRef, FriendlyNameResult, Provenance, ProvenanceStep
from medterm4ds.outputs import to_records, write_csv, write_jsonl
from medterm4ds.services.conceptmap import get_concept_map, iter_concept_map


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
            ("E11.9", "ICD10CM"),
            CodeRef("SNOMEDCT_US", "123"),
            ("208", "CVX"),
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


def test_output_helpers_write_concept_map_records(tmp_path):
    row = get_concept_map(
        [("E11.9", "ICD10CM")],
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
