"""FHIR R4 conformance tests.

Validates that every response builder produces resources that conform to the
FHIR R4 specification (via fhir.resources schema validation), publishes the
OperationDefinition for the custom $search operation, and exercises the full
terminology workflow end-to-end.
"""

from __future__ import annotations

import duckdb
import pytest
from pathlib import Path

from medterm4ds.core.models import CodeInfo, CodeMapping, CodeRef
from medterm4ds.engines.fhir.responses import (
    build_bundle_search,
    build_capability_statement,
    build_operation_outcome,
    build_parameters_lookup,
    build_parameters_subsumes,
    build_parameters_translate,
    build_parameters_validate,
    build_valueset_expand,
)
from medterm4ds.engines.fhir.closure import ClosureTable, build_closure_response
from medterm4ds.engines.fhir.closure import ClosureTable

fhir_resources = pytest.importorskip("fhir.resources")

from fhir.resources.parameters import Parameters
from fhir.resources.bundle import Bundle
from fhir.resources.valueset import ValueSet
from fhir.resources.operationoutcome import OperationOutcome
from fhir.resources.capabilitystatement import CapabilityStatement

_VALIDATORS = {
    "Parameters": Parameters,
    "Bundle": Bundle,
    "ValueSet": ValueSet,
    "OperationOutcome": OperationOutcome,
    "CapabilityStatement": CapabilityStatement,
}


# ---------------------------------------------------------------------------
# Schema validation: every response builder must produce valid FHIR R4 resources
# ---------------------------------------------------------------------------


class TestFhirResourceConformance:
    """Validate response builder output against FHIR R4 schema."""

    def _validate(self, resource_dict: dict, resource_class_name: str):
        """Parse a dict through fhir.resources — raises on schema violation."""
        cls = _VALIDATORS[resource_class_name]
        obj = cls.model_validate(resource_dict)
        assert obj is not None

    def test_parameters_lookup_valid(self):
        code_info = CodeInfo(
            code=CodeRef("SNOMEDCT_US", "44054006"),
            name="Type 2 diabetes mellitus",
            cui="C0011847",
            tty="PT",
        )
        params = build_parameters_lookup(
            code_info,
            system_uri="http://snomed.info/sct",
            custom_properties={"patient-friendly": "Diabetes Type 2"},
        )
        self._validate(params, "Parameters")

    def test_parameters_validate_valid(self):
        params = build_parameters_validate(
            True, system_uri="http://snomed.info/sct", code="44054006"
        )
        self._validate(params, "Parameters")

    def test_parameters_translate_valid(self):
        mapping = CodeMapping(
            source=CodeRef("SNOMEDCT_US", "44054006"),
            target=CodeRef("ICD10CM", "E11"),
            relationship="equivalent",
            match_type="same_cui",
            source_display="Type 2 diabetes",
            target_display="Type 2 diabetes mellitus",
        )
        params = build_parameters_translate(
            [mapping],
            source_system_uri="http://snomed.info/sct",
            source_code="44054006",
        )
        self._validate(params, "Parameters")

    def test_parameters_subsumes_valid(self):
        params = build_parameters_subsumes("subsumes")
        self._validate(params, "Parameters")

    def test_bundle_search_valid(self):
        """$search Bundle structure validation.

        Note: $search is a custom operation (non-standard). The Bundle
        structure (type=searchset, total, entry with search.score) is validated,
        but the inner entry resource is a bare Coding-like dict — not a standard
        FHIR resource type. This matches Patient $match's pattern.
        """
        results = [
            {"code": "44054006", "system": "http://snomed.info/sct", "display": "T2DM",
             "score": 0.92, "match_grade": "certain"},
        ]
        bundle = build_bundle_search(results, query="diabetes", search_mode="lexical")
        # Validate Bundle structure (not inner resources — $search is custom)
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"
        assert bundle["total"] == 1
        assert len(bundle["entry"]) == 1
        entry = bundle["entry"][0]
        assert entry["search"]["mode"] == "match"
        assert entry["search"]["score"] == 0.92
        assert "extension" in entry["search"]  # match-grade extension

    def test_valueset_expand_valid(self):
        contains = [
            {"system": "http://snomed.info/sct", "code": "44054006", "display": "T2DM"},
            {"system": "http://snomed.info/sct", "code": "73211009", "display": "Diabetes"},
        ]
        vs = build_valueset_expand(contains, url="http://example.com/vs/test")
        self._validate(vs, "ValueSet")

    def test_operation_outcome_valid(self):
        oo = build_operation_outcome("error", "not-found", "Code not found")
        self._validate(oo, "OperationOutcome")

    def test_capability_statement_valid(self):
        cs = build_capability_statement("http://127.0.0.1:8001")
        self._validate(cs, "CapabilityStatement")

    def test_closure_response_valid(self):
        closure = ClosureTable("test")
        closure.concepts["44054006"] = {"system": "SNOMEDCT_US", "display": "T2DM"}
        params = build_closure_response(closure)
        self._validate(params, "Parameters")


# ---------------------------------------------------------------------------
# OperationDefinition for $search
# ---------------------------------------------------------------------------


class TestOperationDefinition:
    """Verify the custom $search operation is properly defined."""

    def test_search_operation_in_capability_statement(self):
        cs = build_capability_statement()
        cs_obj = CapabilityStatement.model_validate(cs)
        # Find the CodeSystem resource and check search operation
        rest = cs_obj.rest[0]
        cs_resource = [r for r in rest.resource if r.type == "CodeSystem"][0]
        op_names = [op.name for op in (cs_resource.operation or [])]
        assert "search" in op_names

    def test_all_operations_advertised(self):
        cs = build_capability_statement()
        cs_obj = CapabilityStatement.model_validate(cs)
        rest = cs_obj.rest[0]
        all_ops: list[str] = []
        for r in rest.resource:
            for op in (r.operation or []):
                all_ops.append(op.name)
        # All 7 operations should be advertised
        expected = {"lookup", "validate-code", "subsumes", "closure", "search", "expand", "translate"}
        assert expected.issubset(set(all_ops)), f"Missing: {expected - set(all_ops)}"


# ---------------------------------------------------------------------------
# Integration: full workflow against synthetic DB
# ---------------------------------------------------------------------------


def _make_workflow_db(path: Path) -> None:
    """Create a DB with hierarchy for integration testing."""
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE mrconso (
        CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
        SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),
            ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
            ("E11", "HT", "Type 2 diabetes mellitus", "AE11", "N", "ICD10CM", "C0011847"),
            ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
        ],
    )
    con.execute("""CREATE TABLE mrrel (
        AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [("A44054006", "A73211009", "isa", "PAR")],
    )
    con.close()


fastapi = pytest.importorskip("fastapi")


class TestIntegrationWorkflow:
    """End-to-end: exercise all 7 operations in sequence."""

    @pytest.fixture
    def client(self, tmp_path):
        from starlette.testclient import TestClient
        from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
        db_path = tmp_path / "umls.duckdb"
        _make_workflow_db(db_path)
        settings = FhirApiSettings(
            db_path=db_path,
            memory_profile="low",
            search_index_dir=str(tmp_path / "no-index"),
            prepare_cache=False,
        )
        app = create_fhir_app(settings)
        with TestClient(app) as c:
            yield c

    def test_full_terminology_workflow(self, client):
        """Exercise the complete FHIR terminology workflow:
        metadata → lookup → validate → translate → subsumes → expand → closure
        """
        # 1. CapabilityStatement
        resp = client.get("/fhir/metadata")
        assert resp.status_code == 200
        assert resp.json()["fhirVersion"] == "4.0.1"

        # 2. $lookup — get details for a SNOMED code
        resp = client.get("/fhir/CodeSystem/$lookup", params={
            "system": "http://snomed.info/sct", "code": "44054006"
        })
        assert resp.status_code == 200
        display = [p for p in resp.json()["parameter"] if p["name"] == "display"][0]
        assert "diabetes" in display["valueString"].lower()

        # 3. $validate-code — confirm it's valid
        resp = client.get("/fhir/CodeSystem/$validate-code", params={
            "system": "http://snomed.info/sct", "code": "44054006"
        })
        assert resp.status_code == 200
        result = [p for p in resp.json()["parameter"] if p["name"] == "result"][0]
        assert result["valueBoolean"] is True

        # 4. $translate — map SNOMED to ICD-10
        resp = client.get("/fhir/ConceptMap/$translate", params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
        })
        assert resp.status_code == 200
        params = resp.json()["parameter"]
        result_param = [p for p in params if p["name"] == "result"][0]
        assert result_param["valueBoolean"] is True

        # 5. $subsumes — check hierarchy relationship
        resp = client.get("/fhir/CodeSystem/$subsumes", params={
            "system": "http://snomed.info/sct",
            "codeA": "73211009",
            "codeB": "44054006",
        })
        assert resp.status_code == 200
        outcome = [p for p in resp.json()["parameter"] if p["name"] == "outcome"][0]
        assert outcome["valueCode"] == "subsumes"

        # 6. $expand — text filter search
        resp = client.get("/fhir/ValueSet/$expand", params={
            "filter": "diabetes", "count": 10,
        })
        assert resp.status_code == 200
        assert resp.json()["expansion"]["total"] >= 1

        # 7. $closure — initialize, add concepts, verify subsumption
        resp = client.post("/fhir/CodeSystem/$closure", json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "name", "valueString": "workflow-test"},
                {"name": "concept", "valueCoding": {
                    "system": "http://snomed.info/sct",
                    "code": "73211009",
                    "display": "Diabetes mellitus",
                }},
                {"name": "concept", "valueCoding": {
                    "system": "http://snomed.info/sct",
                    "code": "44054006",
                    "display": "Type 2 diabetes mellitus",
                }},
            ],
        })
        assert resp.status_code == 200
        from medterm4ds.engines.fhir.closure import get_closure_manager
        closure = get_closure_manager().get("workflow-test")
        assert closure is not None
        assert closure.check("73211009", "44054006", "SNOMEDCT_US") == "subsumes"

    def test_error_handling_workflow(self, client):
        """Exercise error paths: invalid system, missing params, unknown codes."""
        # Invalid system URI
        resp = client.get("/fhir/CodeSystem/$lookup", params={
            "system": "http://invalid.example", "code": "123"
        })
        assert resp.status_code == 400
        assert resp.json()["resourceType"] == "OperationOutcome"

        # $validate-code on unknown code → result=false
        resp = client.get("/fhir/CodeSystem/$validate-code", params={
            "system": "http://snomed.info/sct", "code": "NONEXISTENT"
        })
        assert resp.status_code == 200
        result = [p for p in resp.json()["parameter"] if p["name"] == "result"][0]
        assert result["valueBoolean"] is False

        # $subsumes on identical codes → equivalent
        resp = client.get("/fhir/CodeSystem/$subsumes", params={
            "system": "http://snomed.info/sct",
            "codeA": "44054006",
            "codeB": "44054006",
        })
        assert resp.status_code == 200
        outcome = [p for p in resp.json()["parameter"] if p["name"] == "outcome"][0]
        assert outcome["valueCode"] == "equivalent"
