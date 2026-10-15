"""TS-42 fixbatch6 consumer matrix (2026-10-14).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261014d).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: fixbatch6 changed the pf service protocol behavior (uniform
miss rows) and the search endpoint (result_types forwarding). This
controls-iteration pins the new contracts at EVERY consumer — MCP,
REST /patient-friendly, the ConceptMap export — across engine paths
(raw fixture + prepared umls_current) and resolve modes, plus the G1
fix through both HTTP verbs. Zero findings: the contracts compose
cleanly; this suite makes drift loud.

Consumer matrix pinned:
    MCP patient_friendly_names on PRODUCTION (prepared path): miss
    row name=null + match_type='none' (the fix uniform shape rides
    the MCP envelope). (Verified live this run before writing.)
    REST /patient-friendly on the raw fixture: same shape; the
    resolve_mode=historical × unknown interaction stays uniform
    (R1's territory — resolution consulted first, unknown AFTER).
    /conceptmap/patient-friendly: unknown rows carry relationship=
    'unmatched' (the CORRECT R4 failure value via conceptmap_
    relationship('none')) with target_display=''; mixed batches
    compose [equivalent, unmatched] — the RS2/PR1 fixes compose
    through the export without interfering.
    G1 through GET and POST: semantic+resultTypes=lab filters on
    both verbs (the endpoint fix covers the Parameters body path).
"""

from __future__ import annotations

import pathlib

import pytest

SNOMED = "SNOMEDCT_US"
T2DM = "44054006"
PROD_DB = pathlib.Path("/mnt/d/medterm4ds/data/umls_current.duckdb")


@pytest.fixture(scope="module")
def rest_client(tmp_path_factory):
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts42") / "umls.duckdb"
    _make_conformance_db(db)
    app = create_app(ApiSettings(db_path=db))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _code(c: str, source: str = SNOMED) -> dict:
    return {"source": source, "code": c}


class TestMcpConsumerPrepared:
    """MCP on the production (prepared) path."""

    def test_c10_mcp_miss_shape(self):
        if not PROD_DB.exists():
            pytest.skip("production DB not present")
        from medterm4ds.apps.mcp import McpRuntime, McpSettings

        rt = McpRuntime(settings=McpSettings(db_path=PROD_DB))
        rt.open()
        try:
            out = rt.patient_friendly_names(
                codes=["99999999", T2DM], sources=[SNOMED]
            )
        finally:
            rt.close()
        miss, hit = out["results"]
        assert miss["match_type"] == "none"
        assert miss["name"] is None
        assert hit["match_type"] == "same_cui"
        assert hit["name"] == "Diabetes Type 2"


class TestRestConsumerRaw:
    """REST /patient-friendly on the raw fixture."""

    def test_c20_default_miss(self, rest_client):
        r = rest_client.post(
            "/patient-friendly", json={"codes": [_code("99999999")]}
        )
        rec = r.json()["results"][0]
        assert rec["match_type"] == "none"
        assert rec["name"] is None

    def test_c21_historical_unknown_uniform(self, rest_client):
        """resolve_mode=historical on an unknown code: resolution is
        consulted first, the unknown verdict AFTER — uniform miss
        (R1's ordering contract held)."""
        r = rest_client.post(
            "/patient-friendly",
            json={
                "codes": [_code("99999999")],
                "resolve_mode": "historical",
            },
        )
        rec = r.json()["results"][0]
        assert rec["match_type"] == "none"
        assert rec["name"] is None


class TestConceptMapConsumer:
    """The ConceptMap export composes the RS2+PR1 fixes."""

    def test_c30_unknown_row_unmatched(self, rest_client):
        """Unknown code → relationship='unmatched' (the CORRECT R4
        failure value), target_display empty — not the pre-fix
        identifier-echo or 'not-translated'."""
        r = rest_client.post(
            "/conceptmap/patient-friendly",
            json={"codes": [_code("99999999")]},
        )
        rec = r.json()["results"][0]
        assert rec["relationship"] == "unmatched"
        assert rec["match_type"] == "none"
        assert not rec["target_display"]

    def test_c31_mixed_batch_composes(self, rest_client):
        """[known, unknown] → ['equivalent', 'unmatched'] — the two
        fixbatch6 fixes compose without interfering."""
        r = rest_client.post(
            "/conceptmap/patient-friendly",
            json={"codes": [_code(T2DM), _code("99999999")]},
        )
        rels = [x["relationship"] for x in r.json()["results"]]
        assert rels == ["equivalent", "unmatched"]

    def test_c32_hit_still_equivalent(self, rest_client):
        r = rest_client.post(
            "/conceptmap/patient-friendly",
            json={"codes": [_code(T2DM)]},
        )
        assert r.json()["results"][0]["relationship"] == "equivalent"


class TestG1BothVerbs:
    """G1's fix covers GET and the POST Parameters body."""

    @pytest.fixture(scope="class")
    def fhir_client(self, tmp_path_factory):
        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import (
            FhirApiSettings,
            create_fhir_app,
        )

        from .conftest import _make_conformance_db

        db = tmp_path_factory.mktemp("ts42f") / "umls.duckdb"
        _make_conformance_db(db)
        app = create_fhir_app(
            FhirApiSettings(
                db_path=db,
                memory_profile="low",
                search_index_dir=str(
                    tmp_path_factory.mktemp("ts42-idx")
                ),
                prepare_cache=False,
            )
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    def test_c40_get_filters(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes",
                "searchMode": "semantic",
                "resultTypes": "lab",
                "count": 5,
            },
        )
        assert r.status_code == 200
        systems = {
            e["resource"]["system"] for e in r.json()["entry"]
        }
        assert "http://hl7.org/fhir/sid/icd-10-cm" not in systems

    def test_c41_post_filters(self, fhir_client):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "query", "valueString": "diabetes"},
                {"name": "searchMode", "valueCode": "semantic"},
                {"name": "resultTypes", "valueCode": "lab"},
                {"name": "count", "valueInteger": 5},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$search", json=body)
        assert r.status_code == 200
        systems = {
            e["resource"]["system"] for e in r.json()["entry"]
        }
        assert "http://hl7.org/fhir/sid/icd-10-cm" not in systems
