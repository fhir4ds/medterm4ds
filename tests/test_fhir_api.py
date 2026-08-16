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
        # QC-367: PATIENT_FRIENDLY is an internal pseudo-source (output
        # namespace) that now lives in the canonical registry so every
        # surface resolves it identically; it is excluded from the
        # CapabilityStatement advertisement via PSEUDO_SYSTEM_SOURCES.
        expected = {
            "SNOMEDCT_US", "RXNORM", "ICD10CM", "ICD10PCS", "LNC", "CPT",
            "HCPCS", "CVX", "ATC", "PATIENT_FRIENDLY",
        }
        assert expected == set(SYSTEM_TO_FHIR_URI.keys())
        assert len(FHIR_URI_TO_SYSTEM) == len(SYSTEM_TO_FHIR_URI)
        assert system_to_fhir_uri("PATIENT_FRIENDLY") == (
            "urn:medterm4ds:CodeSystem:patient-friendly"
        )

    def test_atc_and_hcpcs_aliases_resolve(self):
        """Regression for QC-006 (CROSS_SURFACE HIGH): ATC missing, HCPCS URI drift.

        Pre-fix, ATC was absent from SYSTEM_TO_FHIR_URI (engine supports it
        via sources/__init__.py + _RELA_ISA_HIERARCHY_SOURCES but the FHIR
        surface rejected with 400). HCPCS was registered under the CMS
        canonical URI but the THO-deprecated ``http://hl7.org/fhir/sid/hcpcs``
        used by older clients was not aliased.
        """
        assert fhir_uri_to_system("http://www.whocc.no/atc") == "ATC"
        assert fhir_uri_to_system("http://hl7.org/fhir/sid/hcpcs") == "HCPCS"
        # Canonical HCPCS URI still resolves
        assert fhir_uri_to_system(
            "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
        ) == "HCPCS"

    def test_uppercase_scheme_and_host_normalize(self):
        """Regression for QC-010 (CROSS_SURFACE MEDIUM): scheme+host case-folding.

        Per RFC 3986 §3.1 (scheme) and §3.2.2 (host) both are case-insensitive.
        Pre-fix, only scheme was normalized; ``HTTP://SNOMED.INFO/sct`` was
        rejected with 400 because the uppercase host failed the exact-string
        lookup. Path remains case-sensitive per §3.2.1 (not normalized).
        """
        assert fhir_uri_to_system("HTTP://SNOMED.INFO/sct") == "SNOMEDCT_US"
        assert fhir_uri_to_system("HTTP://snomed.info/sct") == "SNOMEDCT_US"


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
            # QC-258 fixture: a prefix-matching synonym for 44054006 so
            # search_names('diabetes') ranks the synonym (prefix) above the
            # PT (contains). $expand contains[].display must still be the
            # engine preferred term (PT), matching $lookup on the same code.
            ("44054006", "SY", "diabetes mellitus type 2", "A44054006SY", "N", "SNOMEDCT_US", "C0011847"),
            ("E11", "HT", "Type 2 diabetes mellitus", "AE11", "N", "ICD10CM", "C0011847"),
            # QC-266 fixture: the SAME digit string (44054006) exists in a
            # second system (ICD10CM) so cross-system keying can be tested.
            # STR deliberately avoids 'diabetes' so search/filter tests are
            # unaffected.
            ("44054006", "PT", "Fixture alias concept", "A44054006I10", "N", "ICD10CM", "C0011847"),
            ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
            # activeOnly fixture: a RETIRED (SUPPRESS='Y') child of 73211009.
            # Default (active-only) expansions must never surface 8800001;
            # activeOnly=false must. STR avoids 'diabetes' so filter-mode
            # search tests are unaffected.
            ("8800001", "PT", "Retired glycemic disorder", "A8800001", "Y", "SNOMEDCT_US", "C_RETIRED"),
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
        [
            ("A44054006", "A73211009", "isa", "PAR"),
            ("A8800001", "A73211009", "isa", "PAR"),
        ],
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

    def test_lookup_post_parameter_null(self, fhir_app):
        """POST $lookup with body parameter:null returns FHIR OperationOutcome, not 500.

        Regression for QC-001 (CRITICAL): ``body.get("parameter", [])`` returns
        None when the client explicitly sends ``parameter: null``; iterating
        None raised TypeError that propagated as 500 + text/plain
        (information-disclosure surface, regression of the 10th PROMOTED
        pattern's intent). Same crash shape fires for parameter:42 / str /
        list-of-non-dict. The 4 POST handlers ($lookup, $validate-code,
        $subsumes, $translate) all share _parse_parameters; this probe
        covers $lookup and $validate-code (the most-exercised pair).
        """
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            for op in ("$lookup", "$validate-code"):
                resp = client.post(
                    f"/fhir/CodeSystem/{op}",
                    json={"resourceType": "Parameters", "parameter": None},
                )
                assert resp.status_code < 500, (
                    f"{op} returned {resp.status_code} for parameter:null — "
                    "must produce FHIR OperationOutcome, not 500 + traceback"
                )
                assert resp.headers["content-type"].startswith("application/fhir+json"), (
                    f"{op} content-type {resp.headers['content-type']!r} — "
                    "must be application/fhir+json for FHIR OperationOutcome"
                )
                body = resp.json()
                assert body["resourceType"] == "OperationOutcome"

    def test_subsumes_post_value_boolean_rejected_for_code_a(self, fhir_app):
        """POST $subsumes with valueBoolean:true for codeA must not be silently coerced.

        Regression for QC-046/QC-056 (HIGH): pre-fix, ``_parse_parameters``
        iterated over ``valueBoolean`` and called ``str(value)`` on it.
        Python's ``str(True) == 'True'`` (capital T) silently coerced a
        wrong-type parameter into the string 'True', which was then
        accepted as codeA — returning 200 + outcome=not-subsumed (silent-
        wrong-answer). The fix drops ``valueBoolean`` from the scalar-
        extraction iteration; without codeA, the handler returns 400
        'system, codeA and codeB are required parameters.'
        """
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.post(
                "/fhir/CodeSystem/$subsumes",
                json={
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "codeA", "valueBoolean": True},
                        {"name": "codeB", "valueCode": "44054006"},
                    ],
                },
            )
            # Must NOT be 200 + outcome (silent-wrong-answer).
            # Pre-fix it was 200 with outcome=not-subsumed.
            assert resp.status_code == 400, (
                f"expected 400 for valueBoolean on codeA, got "
                f"{resp.status_code}: {resp.text}"
            )

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

    def test_expand_empty_system_returns_400_not_default_fallback(self, fhir_app):
        """QC-226 (MEDIUM): system='' previously fell through ``if system_uri:``
        to the hidden 4-source default — a 200 with a mixed-system expansion
        the client never asked for — while system=NOSUCH correctly 400s.
        Present-but-empty must be rejected like any other unresolvable URI."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand", params={"filter": "diabetes", "system": ""}
            )
        assert resp.status_code == 400, (
            f"expected 400 for system='', got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_expand_unknown_system_returns_400(self, fhir_app):
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "system": "NOSUCH"},
            )
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

    # -- QC-315: $expand activeOnly --

    def test_expand_active_only_false_includes_retired_qc315(self, fhir_app):
        """QC-315 (MEDIUM): activeOnly honored on the isa (fhir_vs) mode.

        The fixture carries a RETIRED child (8800001, SUPPRESS='Y') under
        73211009. Default (omitted) and activeOnly=true are identical and
        exclude it; activeOnly=false includes it (strict superset).
        """
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            default = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct/73211009?fhir_vs=isa", "count": 100},
            ).json()
            explicit_true = client.get(
                "/fhir/ValueSet/$expand",
                params={
                    "url": "http://snomed.info/sct/73211009?fhir_vs=isa",
                    "count": 100, "activeOnly": "true",
                },
            ).json()
            explicit_false = client.get(
                "/fhir/ValueSet/$expand",
                params={
                    "url": "http://snomed.info/sct/73211009?fhir_vs=isa",
                    "count": 100, "activeOnly": "false",
                },
            ).json()
        default_codes = {c["code"] for c in default["expansion"]["contains"]}
        true_codes = {c["code"] for c in explicit_true["expansion"]["contains"]}
        false_codes = {c["code"] for c in explicit_false["expansion"]["contains"]}
        # activeOnly=true == default (both active-only).
        assert default_codes == true_codes
        assert "8800001" not in default_codes
        # activeOnly=false: strict superset containing the retired child.
        assert default_codes < false_codes
        assert "8800001" in false_codes

    def test_expand_active_only_post_body_boolean_qc315(self, fhir_app):
        """QC-315: POST Parameters-body activeOnly (valueBoolean) is honored."""
        from starlette.testclient import TestClient
        body_false = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": "http://snomed.info/sct/73211009?fhir_vs=isa"},
                {"name": "count", "valueInteger": 100},
                {"name": "activeOnly", "valueBoolean": False},
            ],
        }
        body_true = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": "http://snomed.info/sct/73211009?fhir_vs=isa"},
                {"name": "count", "valueInteger": 100},
                {"name": "activeOnly", "valueBoolean": True},
            ],
        }
        with TestClient(fhir_app) as client:
            false_resp = client.post("/fhir/ValueSet/$expand", json=body_false)
            true_resp = client.post("/fhir/ValueSet/$expand", json=body_true)
        assert false_resp.status_code == 200
        assert true_resp.status_code == 200
        false_codes = {c["code"] for c in false_resp.json()["expansion"]["contains"]}
        true_codes = {c["code"] for c in true_resp.json()["expansion"]["contains"]}
        assert "8800001" in false_codes
        assert "8800001" not in true_codes

    def test_expand_active_only_wrong_type_rejected_qc315(self, fhir_app):
        """QC-315: a non-boolean activeOnly body value 400s (QC-127/QC-136
        wrong-typed-parameter contract) instead of silently using the default."""
        from starlette.testclient import TestClient
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": "http://snomed.info/sct/73211009?fhir_vs=isa"},
                {"name": "activeOnly", "valueString": "yes"},
            ],
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=body)
        assert resp.status_code == 400
        assert resp.json()["resourceType"] == "OperationOutcome"

    def test_expand_active_only_false_filter_mode_400_qc315(self, fhir_app):
        """QC-315: activeOnly=false is rejected with 400 for filter-based
        expansions (the search path is active-only and cannot express
        concept activity) rather than silently ignored."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "activeOnly": "false"},
            )
        assert resp.status_code == 400
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"
        assert "filter" in body["issue"][0]["diagnostics"]

    def test_expand_active_only_true_filter_mode_ok_qc315(self, fhir_app):
        """QC-315: activeOnly=true (== the server default) still works on the
        filter mode."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "activeOnly": "true"},
            )
        assert resp.status_code == 200

    # -- EC-10 (FHIR $expand) remediation regression tests (QC-241..260) --

    def test_expand_offset_pages_qc241(self, fhir_app):
        """QC-241 (HIGH): offset must slice the page, not repeat page 1.

        Pre-fix, ``count=1&offset=1`` returned the SAME first concept and
        ``offset=999999`` returned the full first page — a client paging
        with count+N&offset=N missed every concept past page 1."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            full = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "count": 10},
            ).json()
            page1 = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "count": 1, "offset": 0},
            ).json()
            page2 = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "count": 1, "offset": 1},
            ).json()
            far = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "count": 5, "offset": 999},
            ).json()
        all_codes = [c["code"] for c in full["expansion"]["contains"]]
        assert len(all_codes) >= 2
        assert [c["code"] for c in page1["expansion"]["contains"]] == all_codes[:1]
        assert [c["code"] for c in page2["expansion"]["contains"]] == all_codes[1:2]
        # Page 2 must NOT be page 1 repeated.
        assert page1["expansion"]["contains"] != page2["expansion"]["contains"]
        # offset beyond the end: empty page, total unchanged. QC-330: FHIR
        # JSON convention omits valueless properties — no "contains": [] key.
        assert "contains" not in far["expansion"]
        assert far["expansion"]["total"] == full["expansion"]["total"]

    def test_expand_offset_pages_url_mode_qc241(self, fhir_app):
        """QC-241: offset also pages the fhir_vs URL mode."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            page1 = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct/73211009?fhir_vs=isa", "count": 1, "offset": 0},
            ).json()
            page2 = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct/73211009?fhir_vs=isa", "count": 1, "offset": 1},
            ).json()
        assert page1["expansion"]["contains"][0]["code"] == "73211009"
        assert page2["expansion"]["contains"][0]["code"] == "44054006"

    def test_expand_post_body_offset_honored_qc241(self, fhir_app):
        """QC-241: a POST Parameters-body offset no longer vanishes."""
        from starlette.testclient import TestClient
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": "http://snomed.info/sct/73211009?fhir_vs=isa"},
                {"name": "offset", "valueInteger": 1},
                {"name": "count", "valueInteger": 1},
            ],
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=body)
        assert resp.status_code == 200
        codes = [c["code"] for c in resp.json()["expansion"]["contains"]]
        assert codes == ["44054006"]

    def test_expand_exclude_filter_is_a_qc242(self, fhir_app):
        """QC-242 (HIGH): compose.exclude with an intensional filter must
        remove the filter's ISA set. Pre-fix only exclude.concept was read —
        the filter key was silently ignored and excluded concepts stayed in
        the expansion."""
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
                    "filter": [{"property": "concept", "op": "is-a", "value": "44054006"}],
                }],
            },
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=value_set)
        assert resp.status_code == 200
        codes = {c["code"] for c in resp.json()["expansion"]["contains"]}
        # Root survives; the excluded subtree (44054006 + descendants) is gone.
        assert codes == {"73211009"}

    def test_expand_system_only_include_qc243(self, fhir_app):
        """QC-243 (MEDIUM): compose.include[{system}] with no concept/filter
        means 'all codes in the code system'. Pre-fix it returned a silent
        empty expansion."""
        from starlette.testclient import TestClient
        value_set = {
            "resourceType": "ValueSet",
            "compose": {"include": [{"system": "http://snomed.info/sct"}]},
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=value_set)
        assert resp.status_code == 200
        body = resp.json()
        codes = {c["code"] for c in body["expansion"]["contains"]}
        assert {"73211009", "44054006"} <= codes
        # Displays resolved (never the raw code) per the display contract.
        for c in body["expansion"]["contains"]:
            if c["code"] in ("73211009", "44054006"):
                assert c["display"] and c["display"] != c["code"]

    def test_expand_exclude_scoped_to_exclude_system_qc244(self, fhir_app):
        """QC-244 (MEDIUM): an exclusion only removes codes in the exclude
        block's OWN system. Pre-fix the exclusion was a global code set, so
        an ICD-10-CM exclusion of '73211009' also removed SNOMED 73211009."""
        from starlette.testclient import TestClient
        value_set = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": "http://snomed.info/sct", "concept": [{"code": "73211009"}]},
                    {"system": "http://hl7.org/fhir/sid/icd-10-cm", "concept": [{"code": "E11"}]},
                    {"system": "http://hl7.org/fhir/sid/icd-10-cm", "concept": [{"code": "73211009"}]},
                ],
                "exclude": [{
                    "system": "http://hl7.org/fhir/sid/icd-10-cm",
                    "concept": [{"code": "73211009"}],
                }],
            },
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=value_set)
        assert resp.status_code == 200
        contains = resp.json()["expansion"]["contains"]
        pairs = {(c["system"], c["code"]) for c in contains}
        snomed = "http://snomed.info/sct"
        icd = "http://hl7.org/fhir/sid/icd-10-cm"
        assert (snomed, "73211009") in pairs  # SNOMED code survives
        assert (icd, "E11") in pairs
        assert (icd, "73211009") not in pairs  # ICD exclusion applied

    def test_expand_null_parameter_value_not_stringified_qc245(self, fhir_app):
        """QC-245 (MEDIUM): a JSON null value[x] means the parameter is
        absent — never search for the literal string 'None'."""
        from starlette.testclient import TestClient
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": None},
                {"name": "count", "valueInteger": 5},
            ],
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=body)
        assert resp.status_code == 400, (
            f"expected 400 (filter absent), got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["resourceType"] == "OperationOutcome"

    def test_expand_null_and_empty_concept_codes_rejected_qc246(self, fhir_app):
        """QC-246 (MEDIUM): null/empty concept codes must 400, not fabricate
        contains entries with code 'None' / ''."""
        from starlette.testclient import TestClient
        for bad_code in ("", None):
            value_set = {
                "resourceType": "ValueSet",
                "compose": {"include": [{
                    "system": "http://snomed.info/sct",
                    "concept": [{"code": bad_code}],
                }]},
            }
            with TestClient(fhir_app) as client:
                resp = client.post("/fhir/ValueSet/$expand", json=value_set)
            assert resp.status_code == 400, (
                f"expected 400 for concept code {bad_code!r}, got {resp.status_code}"
            )
            assert resp.json()["resourceType"] == "OperationOutcome"

    def test_expand_unknown_isa_root_returns_400_qc247(self, fhir_app):
        """QC-247 (MEDIUM): an unknown SNOMED isa root must surface as a 400
        OperationOutcome, not a silent {'total': 0} expansion."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct/99999999?fhir_vs=isa", "count": 20},
            )
        assert resp.status_code == 400
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"
        assert "99999999" in body["issue"][0]["diagnostics"]

    def test_expand_spec_form_isa_query_string_qc411(self, fhir_app):
        """QC-411 (HIGH): the FHIR R4 spec-canonical implicit ValueSet form
        carries the concept id in the QUERY STRING
        ('http://snomed.info/sct?fhir_vs=isa/<sctid>' per snomedct.html,
        Implicit Value Sets). Previously only the non-spec path form
        ('.../sct/<code>?fhir_vs=isa') was accepted, so any spec-conformant
        client (HAPI, SMART, VSAC-style) got a 400."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand",
                params={
                    "url": "http://snomed.info/sct?fhir_vs=isa/73211009",
                    "count": 20,
                },
            )
        assert resp.status_code == 200
        codes = [c["code"] for c in resp.json()["expansion"]["contains"]]
        # Same expansion as the accepted path form: root + descendants
        # (fixture edge: 44054006 isa 73211009).
        assert "73211009" in codes
        assert "44054006" in codes

    def test_expand_spec_form_and_path_form_agree_qc411(self, fhir_app):
        """QC-411: spec form and path form must expand identically."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            spec = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct?fhir_vs=isa/73211009"},
            )
            path = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct/73211009?fhir_vs=isa"},
            )
        assert spec.status_code == path.status_code == 200
        assert (
            spec.json()["expansion"]["contains"]
            == path.json()["expansion"]["contains"]
        )

    def test_expand_spec_form_refset_gets_dedicated_message_qc411(self, fhir_app):
        """QC-411: '?fhir_vs=refset/<sctid>' must hit the dedicated refset
        'not implemented' error, not the generic unsupported-pattern 400."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct?fhir_vs=refset/44054006"},
            )
        assert resp.status_code == 400
        diagnostics = resp.json()["issue"][0]["diagnostics"]
        assert "?fhir_vs=refset is not implemented" in diagnostics

    def test_search_empty_query_diagnostic_names_parameter_qc403(self, fhir_app):
        """QC-403 (LOW): a 422 on $search must name the parameter 'query'.
        The loc filter previously dropped every element equal to 'query',
        rendering "Parameter 'unknown'" for the only required $search param."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get("/fhir/CodeSystem/$search", params={"query": ""})
        assert resp.status_code == 422
        assert resp.json()["resourceType"] == "OperationOutcome"
        diagnostics = resp.json()["issue"][0]["diagnostics"]
        assert "Parameter 'query'" in diagnostics
        assert "unknown" not in diagnostics

    def test_expand_post_query_count_honored_for_parameters_body_qc251(self, fhir_app):
        """QC-251 (MEDIUM): POST with a Parameters-with-url body must honor
        the query-string count when the body doesn't specify one."""
        from starlette.testclient import TestClient
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": "http://snomed.info/sct/73211009?fhir_vs=isa"},
            ],
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", params={"count": 1}, json=body)
        assert resp.status_code == 200
        codes = [c["code"] for c in resp.json()["expansion"]["contains"]]
        assert codes == ["73211009"]

    def test_expand_unresolvable_compose_system_returns_400_qc252(self, fhir_app):
        """QC-252 (MEDIUM): compose.include with an unresolvable system must
        400 like the GET surface — not fabricate a concept whose display is
        the code string."""
        from starlette.testclient import TestClient
        value_set = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": "http://example.org/nope",
                "concept": [{"code": "25064002"}],
            }]},
        }
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/ValueSet/$expand", json=value_set)
        assert resp.status_code == 400
        assert resp.json()["resourceType"] == "OperationOutcome"

    def test_expand_filter_display_is_preferred_term_qc258(self, fhir_app):
        """QC-258 (HIGH): contains[].display must be the engine preferred
        term (same as $lookup), not the matched synonym. The fixture gives
        44054006 a prefix-matching synonym that search_names ranks above
        the PT for query 'diabetes'."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "system": "http://snomed.info/sct", "count": 10},
            )
        assert resp.status_code == 200
        by_code = {
            c["code"]: c["display"]
            for c in resp.json()["expansion"]["contains"]
        }
        assert by_code["44054006"] == "Type 2 diabetes mellitus"

    def test_expand_depth_cap_total_is_lower_bound_qc260(self, fhir_app, monkeypatch):
        """QC-260 (HIGH): a depth-truncated total must not read as the
        complete expansion size — bias to len(contains) + 1 like count
        truncation. FHIR_VS_MAX_DEPTH=0 forces depth_cap_hit on the isa
        URL path (root only, descendants beyond the cap)."""
        from starlette.testclient import TestClient
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct/73211009?fhir_vs=isa", "count": 10},
            )
        assert resp.status_code == 200
        body = resp.json()
        contains = body["expansion"]["contains"]
        assert len(contains) == 1  # root only
        # Total must signal incompleteness: at least len(contains) + 1.
        assert body["expansion"]["total"] == len(contains) + 1
        exts = body["expansion"].get("extension", [])
        assert any("valueset-toocostly" in e.get("url", "") for e in exts)

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
        assert closure.check("73211009", "44054006", "SNOMEDCT_US") == "subsumes"
        # Reverse should be subsumed-by
        assert closure.check("44054006", "73211009", "SNOMEDCT_US") == "subsumed-by"
        # Self-subsumption
        assert closure.check("73211009", "73211009", "SNOMEDCT_US") == "equivalent"

    def test_closure_all_malformed_concepts_rejected_not_reset_qc264(self, fhir_app):
        """QC-264 (HIGH): a POST whose concept entries are ALL malformed must
        400 — not fall through to the reset branch and silently wipe the
        closure state at 200 OK."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            # Seed with valid concepts
            seed = {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "qc264"},
                    {"name": "concept", "valueCoding": {
                        "system": "http://snomed.info/sct", "code": "73211009"}},
                    {"name": "concept", "valueCoding": {
                        "system": "http://snomed.info/sct", "code": "44054006"}},
                ],
            }
            assert client.post("/fhir/CodeSystem/$closure", json=seed).status_code == 200
            # All-malformed entries (missing system / missing code / non-dict)
            bad = {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "qc264"},
                    {"name": "concept", "valueCoding": {"code": "73211009"}},
                    {"name": "concept", "valueCoding": {"system": "http://snomed.info/sct"}},
                    {"name": "concept", "valueCoding": "not-a-coding"},
                ],
            }
            resp = client.post("/fhir/CodeSystem/$closure", json=bad)
            assert resp.status_code == 400
            assert resp.json()["resourceType"] == "OperationOutcome"
            # The previously-added concepts survive (no silent reset)
            after = client.post("/fhir/CodeSystem/$closure", json={
                "resourceType": "Parameters",
                "parameter": [{"name": "name", "valueString": "qc264-noreset"}],
            })
            # A body with NO concept entries still initializes/resets cleanly
            assert after.status_code == 200
            state = client.post("/fhir/CodeSystem/$closure", json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "qc264"},
                    {"name": "concept", "valueCoding": {
                        "system": "http://snomed.info/sct", "code": "73211009"}},
                ],
            }).json()
            codes = [p["valueCoding"]["code"] for p in state["parameter"]
                     if p["name"] == "concept"]
            assert "44054006" in codes  # survived the malformed POST

    def test_closure_unresolvable_system_uri_rejected_qc271(self, fhir_app):
        """QC-271 (HIGH): $closure must reject unresolvable system URIs like
        $lookup/$subsumes do, not store a dead concept under the raw URI."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            for bad_uri in ("http://snomed.info/SCT", "SNOMEDCT_US", "http://example.com/x"):
                body = {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "name", "valueString": f"qc271-{bad_uri}"},
                        {"name": "concept", "valueCoding": {
                            "system": bad_uri, "code": "44054006"}},
                    ],
                }
                resp = client.post("/fhir/CodeSystem/$closure", json=body)
                assert resp.status_code == 400, bad_uri
                assert resp.json()["resourceType"] == "OperationOutcome"
            # Resolvable alias still works
            ok = client.post("/fhir/CodeSystem/$closure", json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "qc271-ok"},
                    {"name": "concept", "valueCoding": {
                        "system": "urn:oid:2.16.840.1.113883.6.96",
                        "code": "44054006"}},
                ],
            })
            assert ok.status_code == 200
            systems = [p["valueCoding"]["system"] for p in ok.json()["parameter"]
                       if p["name"] == "concept"]
            assert systems == ["http://snomed.info/sct"]  # canonicalized

    def test_closure_cross_system_code_collision_qc266(self, fhir_app):
        """QC-266 (MEDIUM): concepts are keyed by (system, code) — the same
        digit string in two systems must both be retained, not last-write-wins."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/CodeSystem/$closure", json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "qc266"},
                    {"name": "concept", "valueCoding": {
                        "system": "http://snomed.info/sct", "code": "44054006"}},
                    {"name": "concept", "valueCoding": {
                        "system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "44054006"}},
                ],
            })
            assert resp.status_code == 200
            codings = [p["valueCoding"] for p in resp.json()["parameter"]
                       if p["name"] == "concept"]
            assert len(codings) == 2  # both retained (was 1 pre-fix)
            assert {c["system"] for c in codings} == {
                "http://snomed.info/sct", "http://hl7.org/fhir/sid/icd-10-cm"}

    def test_closure_display_canonicalized_qc282(self, fhir_app):
        """QC-282 (HIGH): the Out concept list carries the engine's canonical
        preferred term — client-supplied wrong displays are corrected and
        omitted displays are never fabricated from the raw code string."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/CodeSystem/$closure", json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "qc282"},
                    # Wrong client display
                    {"name": "concept", "valueCoding": {
                        "system": "http://snomed.info/sct", "code": "73211009",
                        "display": "Myocardial infarction"}},
                    # No display at all
                    {"name": "concept", "valueCoding": {
                        "system": "http://snomed.info/sct", "code": "44054006"}},
                ],
            })
            assert resp.status_code == 200
            codings = {p["valueCoding"]["code"]: p["valueCoding"]["display"]
                       for p in resp.json()["parameter"] if p["name"] == "concept"}
            assert codings["73211009"] == "Diabetes mellitus"  # corrected
            assert codings["44054006"] == "Type 2 diabetes mellitus"  # not the raw code

    def test_closure_unknown_code_rejected_qc269(self, fhir_app):
        """QC-269 (LOW): codes with no active atom are rejected (as $lookup
        does) instead of polluting the closure with unresolvable entries."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/CodeSystem/$closure", json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "qc269"},
                    {"name": "concept", "valueCoding": {
                        "system": "http://snomed.info/sct", "code": "99999999999"}},
                ],
            })
            assert resp.status_code == 400
            assert resp.json()["resourceType"] == "OperationOutcome"

    def test_closure_version_hash_content_deterministic_qc270_qc278(self, fhir_app):
        """QC-270/QC-278: identical content yields the identical hash
        regardless of POST batching, and re-adding present concepts is a
        no-op that does not churn the version token."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            def post(name, codes):
                return client.post("/fhir/CodeSystem/$closure", json={
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "name", "valueString": name},
                        *[{"name": "concept", "valueCoding": {
                            "system": "http://snomed.info/sct", "code": c}}
                          for c in codes],
                    ],
                }).json()

            def ret_hash(body):
                return [p["valueString"] for p in body["parameter"]
                        if p["name"] == "return"][0]

            one_shot = ret_hash(post("qc270-a", ["73211009", "44054006"]))
            first = ret_hash(post("qc270-b", ["73211009"]))
            second = ret_hash(post("qc270-b", ["44054006"]))
            assert one_shot == second  # batching-invariant
            # Re-adding already-present concepts: no-op, same hash
            re_add = ret_hash(post("qc270-b", ["73211009", "44054006"]))
            assert re_add == second

    def test_closure_incomplete_flag_on_wire_qc267(self):
        """QC-267 (MEDIUM): build_closure_response surfaces the
        incomplete_since degradation flag as an `incomplete` Out parameter."""
        from medterm4ds.engines.fhir.closure import (
            ClosureTable,
            build_closure_response,
        )
        closure = ClosureTable("qc267")
        healthy = build_closure_response(closure)
        flag = [p for p in healthy["parameter"] if p["name"] == "incomplete"]
        assert flag == [{"name": "incomplete", "valueBoolean": False}]
        closure.incomplete_since = True
        degraded = build_closure_response(closure)
        flag = [p for p in degraded["parameter"] if p["name"] == "incomplete"]
        assert flag == [{"name": "incomplete", "valueBoolean": True}]

    def test_closure_version_hash_includes_relations_qc283(self):
        """QC-283 (HIGH): the version token is a function of the FULL state —
        two closures with identical concepts but different subsumption
        relations must hash differently."""
        from medterm4ds.engines.fhir.closure import ClosureTable

        def seeded(relations):
            t = ClosureTable("qc283")
            key = ("SNOMEDCT_US", "73211009")
            key2 = ("SNOMEDCT_US", "44054006")
            t.concepts[key] = {"system": "SNOMEDCT_US", "display": "DM"}
            t.concepts[key2] = {"system": "SNOMEDCT_US", "display": "T2DM"}
            t._subsumes[(key, key)] = True
            t._subsumes[(key2, key2)] = True
            for a, b in relations:
                t._subsumes[(a, b)] = True
                t._subsumes[(b, a)] = False
            return t

        key, key2 = ("SNOMEDCT_US", "73211009"), ("SNOMEDCT_US", "44054006")
        healthy = seeded([(key, key2)])
        degraded = seeded([])  # relation silently missing (QC-281 shape)
        assert healthy.version_hash() != degraded.version_hash()

    def test_batch_subsumes_honors_coding_a_b_qc273(self, fhir_app):
        """QC-273 (MEDIUM): the batch dispatcher must consult the
        codingA/codingB extractors just like the dedicated POST route — the
        identical Parameters body must not 400 only because it went through
        POST /fhir."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            bundle = {
                "resourceType": "Bundle",
                "type": "batch",
                "entry": [{
                    "request": {"method": "POST", "url": "CodeSystem/$subsumes"},
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "system", "valueUri": "http://snomed.info/sct"},
                            {"name": "codingA", "valueCoding": {
                                "system": "http://snomed.info/sct",
                                "code": "73211009"}},
                            {"name": "codingB", "valueCoding": {
                                "system": "http://snomed.info/sct",
                                "code": "44054006"}},
                        ],
                    },
                }],
            }
            resp = client.post("/fhir", json=bundle)
            assert resp.status_code == 200
            entry = resp.json()["entry"][0]
            assert entry["response"]["status"] == "200", entry
            outcome = [p for p in entry["resource"]["parameter"]
                       if p["name"] == "outcome"][0]
            assert outcome["valueCode"] == "subsumes"

    # -- EC-12 (FHIR batch endpoint) regression tests --

    def test_batch_non_string_method_isolated_qc284(self, fhir_app):
        """QC-284 (HIGH): entry.request.method = null/int must produce a
        per-entry 400 — NOT a whole-batch 500. FHIR R4 §3.7 entry
        independence."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            bundle = {
                "resourceType": "Bundle",
                "type": "batch",
                "entry": [
                    {"request": {"method": "POST", "url": "CodeSystem/$lookup"},
                     "resource": {"resourceType": "Parameters", "parameter": [
                         {"name": "system", "valueUri": "http://snomed.info/sct"},
                         {"name": "code", "valueCode": "44054006"}]}},
                    {"request": {"method": None, "url": "CodeSystem/$lookup"}},
                    {"request": {"method": 123, "url": "CodeSystem/$lookup"}},
                ],
            }
            resp = client.post("/fhir", json=bundle)
            assert resp.status_code == 200, resp.text
            statuses = [e["response"]["status"] for e in resp.json()["entry"]]
            assert statuses == ["200", "400", "400"], statuses

    def test_batch_post_non_dict_resource_400_qc285(self, fhir_app):
        """QC-285 (MEDIUM): a non-dict entry.resource must be a per-entry
        400 'invalid' — not a 500 leaking AttributeError internals."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            bundle = {
                "resourceType": "Bundle",
                "type": "batch",
                "entry": [
                    {"request": {"method": "POST", "url": "CodeSystem/$lookup"},
                     "resource": 42},
                ],
            }
            resp = client.post("/fhir", json=bundle)
            assert resp.status_code == 200
            entry = resp.json()["entry"][0]
            assert entry["response"]["status"] == "400"
            issue = entry["resource"]["issue"][0]
            assert issue["code"] == "invalid"
            assert "AttributeError" not in issue["diagnostics"]

    def test_batch_expand_valueset_body_qc286(self, fhir_app):
        """QC-286 (MEDIUM): a bare ValueSet POST body must expand identically
        via $batch and via the direct POST route (dual-invocation parity)."""
        from starlette.testclient import TestClient
        value_set = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": "http://snomed.info/sct",
                "concept": [{"code": "44054006"}, {"code": "73211009"}],
            }]},
        }
        with TestClient(fhir_app) as client:
            direct = client.post("/fhir/ValueSet/$expand", json=value_set)
            assert direct.status_code == 200
            bundle = {
                "resourceType": "Bundle",
                "type": "batch",
                "entry": [{
                    "request": {"method": "POST", "url": "ValueSet/$expand"},
                    "resource": value_set,
                }],
            }
            resp = client.post("/fhir", json=bundle)
            assert resp.status_code == 200
            entry = resp.json()["entry"][0]
            assert entry["response"]["status"] == "200", entry
            codes = sorted(c["code"] for c in
                           entry["resource"]["expansion"]["contains"])
            assert codes == ["44054006", "73211009"]

    def test_batch_transaction_response_type_qc288(self, fhir_app):
        """QC-288 (LOW): response to type=transaction MUST be
        transaction-response per FHIR R4 http.html#transaction."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            bundle = {
                "resourceType": "Bundle",
                "type": "transaction",
                "entry": [{
                    "request": {
                        "method": "GET",
                        "url": ("CodeSystem/$lookup"
                                "?system=http://snomed.info/sct&code=44054006"),
                    },
                }],
            }
            resp = client.post("/fhir", json=bundle)
            assert resp.status_code == 200
            assert resp.json()["type"] == "transaction-response"

    def test_batch_translate_source_code_alias_qc289(self, fhir_app):
        """QC-289 (MEDIUM): batch GET $translate must accept the R4
        OperationDefinition parameter name 'sourceCode' like the direct
        route does."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            bundle = {
                "resourceType": "Bundle",
                "type": "batch",
                "entry": [{
                    "request": {
                        "method": "GET",
                        "url": ("ConceptMap/$translate"
                                "?system=http://snomed.info/sct"
                                "&sourceCode=44054006"
                                "&targetsystem=http://hl7.org/fhir/sid/icd-10-cm"),
                    },
                }],
            }
            resp = client.post("/fhir", json=bundle)
            assert resp.status_code == 200
            entry = resp.json()["entry"][0]
            assert entry["response"]["status"] == "200", entry

    def test_batch_closure_name_from_query_qc298(self, fhir_app):
        """QC-298 (MEDIUM): POST batch entries must honor the ?name= query
        param for $closure, mirroring the direct POST route."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            bundle = {
                "resourceType": "Bundle",
                "type": "batch",
                "entry": [{
                    "request": {"method": "POST",
                                "url": "CodeSystem/$closure?name=qc298cl"},
                    "resource": {"resourceType": "Parameters",
                                 "parameter": []},
                }],
            }
            resp = client.post("/fhir", json=bundle)
            assert resp.status_code == 200
            entry = resp.json()["entry"][0]
            assert entry["response"]["status"] == "200", entry

    def test_batch_unknown_op_issue_codes_qc297(self, fhir_app):
        """QC-297 (MEDIUM): batch error entries must mirror the direct
        routes' IssueType — unknown $operation -> processing, unknown
        resource path -> not-found (not blanket 'invalid')."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            bundle = {
                "resourceType": "Bundle",
                "type": "batch",
                "entry": [
                    {"request": {"method": "GET",
                                 "url": "CodeSystem/$no-such-op"}},
                    {"request": {"method": "GET", "url": "Patient/123"}},
                ],
            }
            resp = client.post("/fhir", json=bundle)
            assert resp.status_code == 200
            issues = [e["resource"]["issue"][0]
                      for e in resp.json()["entry"]]
            assert issues[0]["code"] == "processing"
            assert issues[1]["code"] == "not-found"

    # -- EC-13 (XML serialization + expand/search integrity) regression tests --

    def test_xml_control_chars_sanitized_qc300(self, fhir_app):
        """QC-300 (HIGH): control chars (0x01, 0x0B, 0x00) echoed into XML
        value attributes must be stripped — the body must parse as XML."""
        import xml.etree.ElementTree as ET
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            for ch in ("\x01", "\x0b", "\x00"):
                resp = client.get(
                    "/fhir/CodeSystem/$validate-code",
                    params={"system": "http://snomed.info/sct",
                            "code": f"a{ch}b", "_format": "xml"},
                )
                assert resp.status_code == 200
                assert "xml" in resp.headers["content-type"]
                ET.fromstring(resp.text)  # must be well-formed

    def test_xml_implementation_url_is_element_qc302(self, fhir_app):
        """QC-302 (MEDIUM): url hoists to an attribute ONLY on <extension>.
        CapabilityStatement.implementation must render <url value="..."/>."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            xml = client.get("/fhir/metadata", params={"_format": "xml"}).text
        assert "<implementation url=" not in xml
        assert "<url value=" in xml  # primitive child element form

    def test_xml_batch_resource_type_not_element_qc303(self, fhir_app):
        """QC-303 (MEDIUM): a contained resource's resourceType becomes the
        element name (<resource><Parameters>...), never a child element."""
        from starlette.testclient import TestClient
        bundle = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [{"request": {"method": "GET",
                                   "url": "CodeSystem/$lookup?system=http://snomed.info/sct&code=44054006"}}],
        }
        with TestClient(fhir_app) as client:
            xml = client.post("/fhir", params={"_format": "xml"},
                              json=bundle).text
        assert "<resourceType" not in xml
        assert "<resource><Parameters>" in xml

    def test_xml_none_renders_empty_qc304(self):
        """QC-304 (MEDIUM): Python None must not render as the literal
        string 'None' in a value attribute."""
        from medterm4ds.engines.fhir.xml import to_fhir_xml
        xml = to_fhir_xml({"resourceType": "Bundle", "section": None})
        assert 'value="None"' not in xml
        assert "<section" not in xml  # None element omitted entirely

    def test_error_path_honors_xml_format_qc301(self, fhir_app):
        """QC-301 (MEDIUM): operational errors (unrecognized system) must
        honor _format=xml, not unconditionally emit JSON."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": "http://bad.sys", "code": "1",
                        "_format": "xml"},
            )
        assert resp.status_code == 400
        assert "xml" in resp.headers["content-type"]
        assert resp.text.startswith("<?xml")
        assert "<OperationOutcome" in resp.text

    def test_closure_direct_query_name_qc306(self, fhir_app):
        """QC-306 (MEDIUM): direct POST $closure must read ?name= from the
        query string (batch already did)."""
        from starlette.testclient import TestClient
        body = {"resourceType": "Parameters", "parameter": []}
        with TestClient(fhir_app) as client:
            resp = client.post("/fhir/CodeSystem/$closure",
                               params={"name": "qc306cl"}, json=body)
        assert resp.status_code == 200
        assert resp.json()["resourceType"] == "Parameters"

    def test_expand_url_filter_combination_rejected_qc311(self, fhir_app):
        """QC-311 (HIGH): combining url and filter must not silently drop
        one of the two parameters — 400 instead."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            both = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct/73211009?fhir_vs=isa",
                        "filter": "zzzznomatch", "count": 3},
            )
            url_only = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct/73211009?fhir_vs=isa",
                        "count": 3},
            )
            filter_only = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "count": 3},
            )
        assert both.status_code == 400
        assert both.json()["resourceType"] == "OperationOutcome"
        assert url_only.status_code == 200
        assert filter_only.status_code == 200

    def test_expand_toocostly_not_on_terminal_page_qc316(self, fhir_app):
        """QC-316 (HIGH): the toocostly extension must not ride along on a
        short/empty terminal page — that leaves paging clients with no
        termination signal."""
        from starlette.testclient import TestClient

        def toocostly(body):
            return any("toocostly" in e.get("url", "")
                       for e in body["expansion"].get("extension", []))

        with TestClient(fhir_app) as client:
            page1 = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "count": 1, "offset": 0},
            ).json()
            far = client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "count": 5, "offset": 999},
            ).json()
        # QC-330: empty contains is OMITTED from FHIR JSON (valueless
        # properties are never emitted as empty arrays), so the terminal
        # page simply lacks the key — absence is the empty result.
        assert far["expansion"].get("contains", []) == []
        assert not toocostly(far)
        # A full truncated page still signals toocostly (page1 truncated
        # because more matches exist beyond count=1).
        if toocostly(page1):
            # sanity: page1 is full — the signal is legitimate there
            assert len(page1["expansion"]["contains"]) == 1

    def test_batch_expand_offset_qc307(self, fhir_app):
        """QC-307 (MEDIUM): batch $expand entries honor offset (direct/batch
        parity — pre-fix every batch page was page 1)."""
        from starlette.testclient import TestClient
        bundle = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [{"request": {
                "method": "GET",
                "url": "ValueSet/$expand?url=http://snomed.info/sct/73211009?fhir_vs=isa&count=1&offset=1"}}],
        }
        with TestClient(fhir_app) as client:
            direct = client.get(
                "/fhir/ValueSet/$expand",
                params={"url": "http://snomed.info/sct/73211009?fhir_vs=isa",
                        "count": 1, "offset": 1},
            ).json()
            batch = client.post("/fhir", json=bundle).json()
        assert batch["entry"][0]["response"]["status"] == "200"
        d_codes = [c["code"] for c in direct["expansion"]["contains"]]
        b_codes = [c["code"] for c in
                   batch["entry"][0]["resource"]["expansion"]["contains"]]
        assert b_codes == d_codes == ["44054006"]

    def test_batch_expand_post_query_count_qc308(self, fhir_app):
        """QC-308 (MEDIUM): query-string count on POST $expand applies as
        the request default in batch entries too."""
        from starlette.testclient import TestClient
        bundle = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [{
                "request": {"method": "POST",
                            "url": "ValueSet/$expand?count=1"},
                "resource": {"resourceType": "Parameters", "parameter": [
                    {"name": "url",
                     "valueUri": "http://snomed.info/sct/73211009?fhir_vs=isa"}]},
            }],
        }
        with TestClient(fhir_app) as client:
            batch = client.post("/fhir", json=bundle).json()
        assert batch["entry"][0]["response"]["status"] == "200"
        contains = batch["entry"][0]["resource"]["expansion"]["contains"]
        assert len(contains) == 1

    def test_search_display_is_preferred_term_qc317(
        self, fhir_app, monkeypatch,
    ):
        """QC-317 (MEDIUM): $search entry displays must be the engine
        preferred term of the (source, code) claimed — not the matched
        cross-source search synonym (QC-258 family)."""

        class _Row:
            def __init__(self, source, code, display):
                self.source, self.code, self.display = source, code, display
                self.score, self.match_grade = 1.0, "certain"

        class _FakeService:
            lexical_available = True
            semantic_available = True
            # QC-400: the handler now passes the engine so the SERVICE can
            # canonicalize displays (single source of truth). Record it to
            # assert the wiring; the canonicalization itself is covered by
            # test_apply_preferred_display_qc400 below against the fixture DB.
            received_engine = None

            def search(self, query, mode, sources, count, engine=None):
                _FakeService.received_engine = engine
                return [
                    _Row("SNOMEDCT_US", "73211009", "Diabetes"),
                    _Row("SNOMEDCT_US", "99999999", "Ghost synonym"),
                ]

        import medterm4ds.services.search as search_module
        monkeypatch.setattr(
            search_module, "get_search_service", lambda: _FakeService()
        )
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            resp = client.get(
                "/fhir/CodeSystem/$search",
                params={"query": "diabetes", "mode": "lexical", "count": 2},
            )
        assert resp.status_code == 200
        # The handler MUST hand the engine to the service (QC-400 wiring).
        assert _FakeService.received_engine is not None
        entries = [e["resource"] for e in resp.json()["entry"]]
        by_code = {e["code"]: e["display"] for e in entries}
        # The handler echoes what the service produced; the preferred-term
        # contract itself is enforced inside SearchService.search — verified
        # end-to-end (real service + real engine) in
        # test_apply_preferred_display_qc400 below.
        assert by_code == {
            "73211009": "Diabetes",
            "99999999": "Ghost synonym",
        }

    def test_apply_preferred_display_qc400(self, tmp_path):
        """QC-400 (MEDIUM): SearchService.search(engine=...) canonicalizes
        legacy-mode displays to the engine preferred term — the QC-317 rule,
        moved into the service so Python/MCP/FHIR share one convention."""
        import duckdb as _duckdb

        from medterm4ds.engines.duckdb import LocalDuckDBEngine
        from medterm4ds.services.search import SearchResult, apply_preferred_display

        db_path = tmp_path / "qc400.duckdb"
        _make_fhir_db(db_path)
        con = _duckdb.connect(str(db_path))
        try:
            engine = LocalDuckDBEngine(con)
            results = [
                SearchResult(code="73211009", source="SNOMEDCT_US", display="Diabetes",
                             score=1.0, match_grade="certain"),
                SearchResult(code="99999999", source="SNOMEDCT_US", display="Ghost synonym",
                             score=0.9, match_grade="probable"),
                SearchResult(code="44054006", source="SNOMEDCT_US", display="diabetes mellitus type 2",
                             score=0.8, match_grade="possible"),
            ]
            resolved = apply_preferred_display(results, engine)
        finally:
            con.close()
        # Resolvable codes: engine preferred term (PT), not the index synonym.
        assert resolved[0].display == "Diabetes mellitus"
        assert resolved[2].display == "Type 2 diabetes mellitus"
        # Unresolvable code: falls back to the matched synonym.
        assert resolved[1].display == "Ghost synonym"
        # Codes/scores/grades are untouched.
        assert [r.code for r in resolved] == ["73211009", "99999999", "44054006"]
        assert [r.score for r in resolved] == [1.0, 0.9, 0.8]

    def test_extract_annotated_xml_rejected_qc305(self, fhir_app):
        """QC-305 (MEDIUM): format=annotated is JSON-only; a negotiated
        _format=xml must 406 instead of silently downgrading the format."""
        from starlette.testclient import TestClient
        with TestClient(fhir_app) as client:
            get_resp = client.get(
                "/fhir/CodeSystem/$extract",
                params={"text": "type 2 diabetes", "format": "annotated",
                        "_format": "xml"},
            )
            post_resp = client.post(
                "/fhir/CodeSystem/$extract",
                params={"_format": "xml"},
                json={"resourceType": "Parameters", "parameter": [
                    {"name": "text", "valueString": "type 2 diabetes"},
                    {"name": "format", "valueCode": "annotated"}]},
            )
        assert get_resp.status_code == 406
        assert post_resp.status_code == 406
        # The 406 body honors the negotiated format (XML OperationOutcome).
        assert "xml" in get_resp.headers["content-type"]
        assert "<OperationOutcome" in get_resp.text
