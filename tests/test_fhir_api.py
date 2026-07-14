"""Tests for the FHIR R4 terminology facade."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.fhir import (
    FHIR_URI_TO_SYSTEM,
    SYSTEM_TO_FHIR_URI,
    fhir_uri_to_system,
    system_to_fhir_uri,
)
from medterm4ds.engines.fhir.responses import (
    build_bundle_search,
    build_capability_statement,
    build_operation_outcome,
    build_parameters_lookup,
    build_parameters_translate,
    build_parameters_validate,
)


# ---------------------------------------------------------------------------
# URI mapping tests
# ---------------------------------------------------------------------------


class TestUriMapping:
    def test_canonical_uris_resolve(self):
        assert fhir_uri_to_system("http://snomed.info/sct") == "SNOMEDCT_US"
        assert fhir_uri_to_system("http://www.nlm.nih.gov/research/umls/rxnorm") == "RXNORM"
        assert fhir_uri_to_system("http://hl7.org/fhir/sid/icd-10-cm") == "ICD10CM"
        assert fhir_uri_to_system("http://loinc.org") == "LNC"

    def test_oid_aliases_resolve(self):
        assert fhir_uri_to_system("urn:oid:2.16.840.1.113883.6.96") == "SNOMEDCT_US"
        assert fhir_uri_to_system("urn:oid:2.16.840.1.113883.6.1") == "LNC"

    def test_trailing_slash_handled(self):
        assert fhir_uri_to_system("http://snomed.info/sct/") == "SNOMEDCT_US"
        assert fhir_uri_to_system("http://loinc.org/") == "LNC"

    def test_unknown_uri_returns_none(self):
        assert fhir_uri_to_system("http://example.com/fake") is None

    def test_reverse_mapping(self):
        assert system_to_fhir_uri("SNOMEDCT_US") == "http://snomed.info/sct"
        assert system_to_fhir_uri("RXNORM") == "http://www.nlm.nih.gov/research/umls/rxnorm"
        assert system_to_fhir_uri("UNKNOWN") is None

    def test_all_mapped_sources_have_uris(self):
        expected = {"SNOMEDCT_US", "RXNORM", "ICD10CM", "ICD10PCS", "LNC", "CPT", "HCPCS", "CVX"}
        assert expected == set(SYSTEM_TO_FHIR_URI.keys())
        assert len(FHIR_URI_TO_SYSTEM) == len(SYSTEM_TO_FHIR_URI)


# ---------------------------------------------------------------------------
# Response builder tests
# ---------------------------------------------------------------------------


class TestResponseBuilders:
    def test_operation_outcome(self):
        oo = build_operation_outcome("error", "not-found", "Code missing")
        assert oo["resourceType"] == "OperationOutcome"
        assert oo["issue"][0]["severity"] == "error"
        assert oo["issue"][0]["code"] == "not-found"

    def test_bundle_search_format(self):
        results = [
            {"code": "44054006", "system": "http://snomed.info/sct", "display": "T2DM", "score": 0.92, "match_grade": "certain"},
            {"code": "73211009", "system": "http://snomed.info/sct", "display": "Diabetes", "score": 0.71, "match_grade": "probable"},
        ]
        bundle = build_bundle_search(results, query="diabetes", search_mode="lexical")
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"
        assert bundle["total"] == 2
        assert bundle["entry"][0]["search"]["mode"] == "match"
        assert bundle["entry"][0]["search"]["score"] == 0.92
        ext = bundle["entry"][0]["search"]["extension"][0]
        assert ext["valueCode"] == "certain"

    def test_capability_statement(self):
        cs = build_capability_statement()
        assert cs["resourceType"] == "CapabilityStatement"
        assert cs["fhirVersion"] == "4.0.1"
        operations = [
            op["name"]
            for r in cs["rest"]
            for res in r["resource"]
            for op in res.get("operation", [])
        ]
        assert "lookup" in operations
        assert "validate-code" in operations
        assert "translate" in operations
        assert "search" in operations

    def test_parameters_validate_true(self):
        params = build_parameters_validate(
            True, system_uri="http://snomed.info/sct", code="44054006"
        )
        assert params["resourceType"] == "Parameters"
        result_entry = [p for p in params["parameter"] if p["name"] == "result"][0]
        assert result_entry["valueBoolean"] is True

    def test_parameters_validate_false(self):
        params = build_parameters_validate(
            False, system_uri="http://snomed.info/sct", code="INVALID"
        )
        result_entry = [p for p in params["parameter"] if p["name"] == "result"][0]
        assert result_entry["valueBoolean"] is False

    def test_parameters_translate(self):
        from medterm4ds.core.models import CodeMapping, CodeRef
        mappings = [
            CodeMapping(
                source=CodeRef("SNOMEDCT_US", "44054006"),
                target=CodeRef("ICD10CM", "E11"),
                relationship="equivalent",
                match_type="same_cui",
                source_display="Type 2 diabetes",
                target_display="Type 2 diabetes mellitus",
            )
        ]
        params = build_parameters_translate(
            mappings, source_system_uri="http://snomed.info/sct", source_code="44054006"
        )
        result = [p for p in params["parameter"] if p["name"] == "result"][0]
        assert result["valueBoolean"] is True
        match = [p for p in params["parameter"] if p["name"] == "match"][0]
        concept = [part for part in match["part"] if part["name"] == "concept"][0]
        assert concept["valueCoding"]["code"] == "E11"


# ---------------------------------------------------------------------------
# Endpoint tests (synthetic DB)
# ---------------------------------------------------------------------------


def _make_fhir_db(path: Path) -> None:
    """Create a minimal DuckDB with mrconso + mrrel for FHIR tests."""
    con = duckdb.connect(str(path))
    con.execute(
        """CREATE TABLE mrconso (
            CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
            SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
        )"""
    )
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),
            ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
            ("E11", "HT", "Type 2 diabetes mellitus", "AE11", "N", "ICD10CM", "C0011847"),
            ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
        ],
    )
    con.execute(
        """CREATE TABLE mrrel (
            AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR
        )"""
    )
    # SNOMED hierarchy: 44054006 (Type 2 diabetes) → parent → 73211009 (Diabetes)
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [("A44054006", "A73211009", "isa", "PAR")],
    )
    con.close()


fastapi = pytest.importorskip("fastapi")


class TestFhirEndpoints:
    @pytest.fixture
    def fhir_app(self, tmp_path):
        from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
        db_path = tmp_path / "umls.duckdb"
        _make_fhir_db(db_path)
        settings = FhirApiSettings(
            db_path=db_path,
            memory_profile="low",
            search_index_dir=str(tmp_path / "nonexistent"),  # no BM25 index
            prepare_cache=False,
        )
        return create_fhir_app(settings)

    def test_metadata(self, fhir_app):
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get("/fhir/metadata")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resourceType"] == "CapabilityStatement"
        assert body["fhirVersion"] == "4.0.1"

    def test_lookup_snomed(self, fhir_app):
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": "http://snomed.info/sct", "code": "44054006"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resourceType"] == "Parameters"
        display = [p for p in body["parameter"] if p["name"] == "display"]
        assert len(display) == 1
        assert "diabetes" in display[0]["valueString"].lower()

    def test_lookup_unknown_system(self, fhir_app):
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": "http://example.com/fake", "code": "123"},
            )
        assert resp.status_code == 400
        assert resp.json()["resourceType"] == "OperationOutcome"

    def test_validate_code_valid(self, fhir_app):
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$validate-code",
                params={"system": "http://snomed.info/sct", "code": "44054006"},
            )
        assert resp.status_code == 200
        result = [p for p in resp.json()["parameter"] if p["name"] == "result"][0]
        assert result["valueBoolean"] is True

    def test_validate_code_invalid(self, fhir_app):
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$validate-code",
                params={"system": "http://snomed.info/sct", "code": "FAKE999"},
            )
        result = [p for p in resp.json()["parameter"] if p["name"] == "result"][0]
        assert result["valueBoolean"] is False

    def test_translate_snomed_to_icd10(self, fhir_app):
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ConceptMap/$translate",
                params={
                    "system": "http://snomed.info/sct",
                    "code": "44054006",
                    "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
                },
            )
        assert resp.status_code == 200
        result = [p for p in resp.json()["parameter"] if p["name"] == "result"]
        assert len(result) == 1

    def test_search_without_index_returns_503(self, fhir_app):
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$search",
                params={"query": "diabetes"},
            )
        assert resp.status_code == 503
        assert resp.json()["resourceType"] == "OperationOutcome"

    def test_search_hybrid_requires_indexes(self, fhir_app):
        """Hybrid mode returns 503 when BM25 or embedding index is unavailable."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$search",
                params={"query": "diabetes", "searchMode": "hybrid"},
            )
        assert resp.status_code == 503

    def test_search_semantic_requires_model(self, fhir_app):
        """Semantic mode returns 503 when embedding model is unavailable."""
        from pathlib import Path
        model_dir = Path("/mnt/d/fhir4px-model/data/sapbert_finetuned")
        if model_dir.exists():
            pytest.skip("SapBERT model is available on this machine — cannot test 503 path.")
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$search",
                params={"query": "diabetes", "searchMode": "semantic"},
            )
        assert resp.status_code == 503

    def test_subsumes_identical_codes(self, fhir_app):
        """Identical codes return 'equivalent'."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$subsumes",
                params={
                    "system": "http://snomed.info/sct",
                    "codeA": "44054006",
                    "codeB": "44054006",
                },
            )
        assert resp.status_code == 200
        outcome = [p for p in resp.json()["parameter"] if p["name"] == "outcome"][0]
        assert outcome["valueCode"] == "equivalent"

    def test_subsumes_different_system(self, fhir_app):
        """Unknown system returns 400."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$subsumes",
                params={
                    "system": "http://example.com/fake",
                    "codeA": "1",
                    "codeB": "2",
                },
            )
        assert resp.status_code == 400

    def test_expand_with_filter(self, fhir_app):
        """$expand with filter returns ValueSet containing matching codes."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand",
                params={
                    "filter": "diabetes",
                    "count": 10,
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resourceType"] == "ValueSet"
        assert "expansion" in body
        assert body["expansion"]["total"] >= 1
        contains = body["expansion"]["contains"]
        assert any("diabetes" in c.get("display", "").lower() for c in contains)

    def test_expand_without_filter_returns_400(self, fhir_app):
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get("/fhir/ValueSet/$expand")
        assert resp.status_code == 400

    def test_expand_intensional_is_a(self, fhir_app):
        """POST a ValueSet with compose.include.filter is-a — should return
        root code + all descendants."""
        from starlette.testclient import TestClient
        value_set = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": "http://snomed.info/sct",
                    "filter": [{
                        "property": "concept",
                        "op": "is-a",
                        "value": "73211009",
                    }],
                }],
            },
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=value_set)
        assert resp.status_code == 200
        body = resp.json()
        assert body["resourceType"] == "ValueSet"
        codes = {c["code"] for c in body["expansion"]["contains"]}
        # is-a includes the root (73211009) AND descendants (44054006)
        assert "73211009" in codes
        assert "44054006" in codes

    def test_expand_intensional_descendent_of(self, fhir_app):
        """POST a ValueSet with descendent-of filter — should return
        descendants but NOT the root code.

        Note: the FHIR R4 spec enum value is "descendent-of" (Latin-derived),
        NOT "descendant-of" (common English spelling). Found by SKEPTIC
        iteration VS-01 (QA-054) — the prior test name + op value used the
        off-spec spelling "descendant-of" which the implementation silently
        honored while silently dropping the spec-correct "descendent-of".
        """
        from starlette.testclient import TestClient
        value_set = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": "http://snomed.info/sct",
                    "filter": [{
                        "property": "concept",
                        "op": "descendent-of",
                        "value": "73211009",
                    }],
                }],
            },
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=value_set)
        assert resp.status_code == 200
        body = resp.json()
        codes = {c["code"] for c in body["expansion"]["contains"]}
        # descendent-of excludes the root (73211009), includes children (44054006)
        assert "73211009" not in codes
        assert "44054006" in codes

    def test_expand_explicit_concepts(self, fhir_app):
        """POST a ValueSet with explicit concept list."""
        from starlette.testclient import TestClient
        value_set = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": "http://snomed.info/sct",
                    "concept": [
                        {"code": "44054006", "display": "Type 2 diabetes"},
                        {"code": "73211009", "display": "Diabetes"},
                    ],
                }],
            },
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=value_set)
        assert resp.status_code == 200
        body = resp.json()
        codes = {c["code"] for c in body["expansion"]["contains"]}
        assert codes == {"44054006", "73211009"}

    def test_expand_intensional_with_exclude(self, fhir_app):
        """POST a ValueSet with compose.exclude — excluded codes removed."""
        from starlette.testclient import TestClient
        value_set = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": "http://snomed.info/sct",
                    "filter": [{"property": "concept", "op": "is-a", "value": "73211009"}],
                }],
                "exclude": [{
                    "system": "http://snomed.info/sct",
                    "concept": [{"code": "44054006"}],
                }],
            },
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=value_set)
        assert resp.status_code == 200
        body = resp.json()
        codes = {c["code"] for c in body["expansion"]["contains"]}
        # Root is included, but the excluded child is removed
        assert "73211009" in codes
        assert "44054006" not in codes

    def test_expand_fhir_vs_url_pattern(self, fhir_app):
        """GET with SNOMED fhir_vs URL — intensional shorthand."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct/73211009?fhir_vs=isa", "count": 100},
            )
        assert resp.status_code == 200
        body = resp.json()
        codes = {c["code"] for c in body["expansion"]["contains"]}
        assert "73211009" in codes
        assert "44054006" in codes

    def test_closure_initialize(self, fhir_app):
        """POST $closure with name but no concepts → initializes empty closure."""
        from starlette.testclient import TestClient
        body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "name", "valueString": "test-closure"}],
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/CodeSystem/$closure", json=body)
        assert resp.status_code == 200
        result_body = resp.json()
        assert result_body["resourceType"] == "Parameters"
        return_param = [p for p in result_body["parameter"] if p["name"] == "return"]
        assert len(return_param) == 1
        assert return_param[0]["valueString"]  # version hash present

    def test_closure_requires_name(self, fhir_app):
        """POST $closure without name → 400."""
        from starlette.testclient import TestClient
        body = {"resourceType": "Parameters", "parameter": []}
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/CodeSystem/$closure", json=body)
        assert resp.status_code == 400

    def test_closure_add_concepts_and_check_subsumption(self, fhir_app):
        """Add parent + child to closure, then verify subsumption via
        the closure table's check method."""
        from starlette.testclient import TestClient
        from medterm4ds.engines.fhir.closure import get_closure_manager

        # Add two concepts: Diabetes (73211009) and Type 2 diabetes (44054006)
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "name", "valueString": "diabetes-closure"},
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
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/CodeSystem/$closure", json=body)
        assert resp.status_code == 200

        # Verify the closure table recorded the subsumption
        manager = get_closure_manager()
        closure = manager.get("diabetes-closure")
        assert closure is not None
        # 73211009 (Diabetes) should subsume 44054006 (Type 2 diabetes)
        assert closure.check("73211009", "44054006") == "subsumes"
        # Reverse should be subsumed-by
        assert closure.check("44054006", "73211009") == "subsumed-by"
        # Self-subsumption
        assert closure.check("73211009", "73211009") == "equivalent"
