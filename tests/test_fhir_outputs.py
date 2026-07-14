from __future__ import annotations

import json

from medterm4ds import CodeRef, ConceptMapRow, Provenance, ProvenanceStep
from medterm4ds.outputs.fhir import (
    PATIENT_FRIENDLY_SYSTEM,
    code_system_uri,
    concept_map_to_fhir,
    fhir_equivalence,
    write_fhir_concept_map,
)


def _row(
    source: str,
    code: str,
    display: str,
    relationship: str,
    *,
    match_type: str,
) -> ConceptMapRow:
    return ConceptMapRow(
        source=CodeRef(source, code),
        source_display=f"Technical {code}",
        target=CodeRef("PATIENT_FRIENDLY", f"{source}:{code}"),
        target_display=display,
        relationship=relationship,
        friendly_source="CHV",
        match_type=match_type,
        match_depth=1,
        matched_via=Provenance.from_steps(
            "test",
            [
                ProvenanceStep(op="input", source=source, code=code),
                ProvenanceStep(op="friendly_atom", source="CHV", name=display, depth=1),
            ],
        ),
    )


def test_concept_map_to_fhir_groups_by_source_and_target_system():
    resource = concept_map_to_fhir(
        [
            _row("ICD10CM", "E11.9", "Diabetes", "equivalent", match_type="exact"),
            _row(
                "SNOMEDCT_US",
                "123",
                "Heart condition",
                "source-is-narrower-than-target",
                match_type="broader_exact",
            ),
            _row("CVX", "208", "COVID-19 vaccine", "not-translated", match_type="original"),
            _row("LOCAL", "NONE", "No match", "unmatched", match_type="none"),
        ],
        date="2026-05-31T00:00:00+00:00",
    )

    assert resource["resourceType"] == "ConceptMap"
    assert resource["status"] == "draft"
    assert resource["date"] == "2026-05-31T00:00:00+00:00"
    assert [group["source"] for group in resource["group"]] == [
        "http://hl7.org/fhir/sid/icd-10-cm",
        "http://snomed.info/sct",
        "http://hl7.org/fhir/sid/cvx",
        "urn:medterm4ds:CodeSystem:LOCAL",
    ]
    assert all(group["target"] == PATIENT_FRIENDLY_SYSTEM for group in resource["group"])

    icd_target = resource["group"][0]["element"][0]["target"][0]
    assert icd_target["equivalence"] == "equivalent"
    assert icd_target["code"] == "ICD10CM:E11.9"
    assert {extension["url"].rsplit("/", 1)[-1] for extension in icd_target["extension"]} == {
        "relationship",
        "friendly-source",
        "match-type",
        "match-depth",
        "matched-via",
    }

    snomed_target = resource["group"][1]["element"][0]["target"][0]
    assert snomed_target["equivalence"] == "wider"

    cvx_target = resource["group"][2]["element"][0]["target"][0]
    # Per R4 spec a "not-translated" relationship means there is no
    # translation in the target system. The R4 catch-all for "no mapping"
    # is ``unmatched`` (NOT ``equivalent``). The prior assertion pinned the
    # wrong behavior — fixed by SKEPTIC iteration CM-01 (CM01-SKEPTIC-002).
    assert cvx_target["equivalence"] == "unmatched"
    relationship_extension = [
        extension
        for extension in cvx_target["extension"]
        if extension["url"].endswith("/relationship")
    ][0]
    assert relationship_extension["valueCode"] == "not-translated"

    unmatched_target = resource["group"][3]["element"][0]["target"][0]
    assert unmatched_target == {
        "equivalence": "unmatched",
        "extension": [
            {
                "url": "urn:medterm4ds:StructureDefinition/relationship",
                "valueCode": "unmatched",
            },
            {
                "url": "urn:medterm4ds:StructureDefinition/friendly-source",
                "valueCode": "CHV",
            },
            {
                "url": "urn:medterm4ds:StructureDefinition/match-type",
                "valueCode": "none",
            },
            {
                "url": "urn:medterm4ds:StructureDefinition/match-depth",
                "valueInteger": 1,
            },
            {
                "url": "urn:medterm4ds:StructureDefinition/matched-via",
                "valueString": "{\"steps\":[{\"code\":\"NONE\",\"op\":\"input\",\"source\":\"LOCAL\"},{\"depth\":1,\"name\":\"No match\",\"op\":\"friendly_atom\",\"source\":\"CHV\"}],\"strategy\":\"test\"}",
            },
        ],
    }


def test_code_system_uri_allows_unknown_sources():
    assert code_system_uri("LOINC") == "http://loinc.org"
    assert code_system_uri("LOCAL") == "urn:medterm4ds:CodeSystem:LOCAL"


def test_fhir_equivalence_uses_r4_codes():
    assert fhir_equivalence("equivalent") == "equivalent"
    assert fhir_equivalence("source-is-narrower-than-target") == "wider"
    assert fhir_equivalence("source-is-broader-than-target") == "narrower"
    assert fhir_equivalence("related-to") == "relatedto"
    # CR-024 (milestone-3 review): the parallel maps in ``outputs/fhir.py`` and
    # ``responses.py`` were consolidated into ``engines/fhir/equivalence.py``.
    # The prior divergent spellings (``not-related-to`` vs ``not-relatedto``)
    # and values (``disjoint`` vs ``unmatched``) are now unified — both
    # spellings map to the R4 catch-all ``unmatched`` (the conservative
    # default for "unknown engine vocabulary token"); ``disjoint`` would
    # wrongly imply the concepts are explicitly disconnected.
    assert fhir_equivalence("not-related-to") == "unmatched"
    assert fhir_equivalence("not-relatedto") == "unmatched"
    assert fhir_equivalence("unmatched") == "unmatched"


def test_write_fhir_concept_map(tmp_path):
    output_path = tmp_path / "conceptmap.json"
    write_fhir_concept_map(
        [_row("ICD10CM", "E11.9", "Diabetes", "equivalent", match_type="exact")],
        output_path,
    )

    resource = json.loads(output_path.read_text(encoding="utf-8"))
    assert resource["resourceType"] == "ConceptMap"
    assert resource["group"][0]["element"][0]["code"] == "E11.9"
