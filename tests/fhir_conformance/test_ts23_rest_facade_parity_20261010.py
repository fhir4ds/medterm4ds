"""TS-23 Python REST facade parity survey (2026-10-10).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261010b).
FRESH EVIDENCE — live probes executed this run against the REST facade
(``apps/api.py`` create_app) on a synthetic DB; no prior-run results
cited.

Scope: the Python REST facade (``medterm4ds.apps.api``, exposed via
ApiSettings/create_app — distinct from the FHIR facade) had never been
maintenance-swept. This suite pins its input contracts and its parity
with the three swept surfaces (FHIR, MCP iter-32, CLI iter-33).

PY1 (LOW, NEW) — a fourth miss-shape for the same lookup miss.
    Unknown source: FHIR $lookup 400s (boundary rejection);
    MCP returns a null-FIELD record (Z3); CLI returns a null-FIELD
    record + exit 0 (CLI3); the REST facade returns a NULL-ENTRY
    ({"results": [null]}) — a JSON null where a record-shaped object
    sits for valid codes. Four surfaces, three distinct miss shapes
    (MCP and CLI agree). Consumers composing surfaces must special-
    case each. Fix shape: pick ONE miss convention (null-field record
    recommended — most information-preserving) and align the facade;
    or document the matrix. Pinned: p10/p11.

PY2 (LOW, NEW) — per-code isolation HOLDS but is unpinned on this
    surface (C1's reference shape).
    Mixed batches ([valid, unknown-code] and [valid, unknown-source])
    return [record, null] at 200 — the C1 (closure poisoning) lesson
    already correct here, but nothing pinned it. This suite does
    (control class, documented for the parity matrix). Pinned: p20.

Controls (fresh) — the facade's VALIDATION is the strongest of the
    four surfaces: whitespace code 400 ('code must be a non-empty
    string' — the service-boundary QC-422 guard, surfaced via the
    ValueError handler); URI-form source 400 (QC-322 message);
    empty-string source 422 (pydantic min_length); map requires
    target_sources 400 ('must not be empty') + unknown target 400
    ('not found in this database') — V2/V3's gaps never existed here;
    hierarchy direction enum-gated 422; empty codes list 200-empty
    (documented batch convention); /health carries readiness + sources
    + version; /openapi.json enumerates all 14 routes.
"""

from __future__ import annotations

import pytest

SNOMED = "SNOMEDCT_US"


@pytest.fixture(scope="module")
def rest_client(tmp_path_factory):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts23_rest") / "umls.duckdb"
    _make_conformance_db(db)
    app = create_app(ApiSettings(db_path=db))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _code(c: str, source: str = SNOMED) -> dict:
    return {"source": source, "code": c}


class TestPY1MissShapes:
    """PY1 — a fourth miss-shape for the same lookup miss."""

    def test_p10_unknown_source_null_entry(self, rest_client):
        """Unknown source → 200 {"results": [null]} — a JSON null
        entry (vs FHIR 400 / MCP+CLI null-FIELD records). Flip when
        the facade aligns to one miss convention."""
        r = rest_client.post("/lookup", json={"codes": [_code("44054006", "NOTASAB")]})
        assert r.status_code == 200
        assert r.json()["results"] == [None]

    def test_p11_unknown_code_same_shape(self, rest_client):
        """Unknown code → the same null-entry shape (the two miss
        classes are indistinguishable here too — CLI3/Z3 family)."""
        r = rest_client.post("/lookup", json={"codes": [_code("99999999")]})
        assert r.status_code == 200
        assert r.json()["results"] == [None]


class TestPY2PerCodeIsolation:
    """PY2 — mixed-batch isolation pinned (C1's reference shape)."""

    def test_p20_mixed_batch_isolated(self, rest_client):
        """[valid, unknown] → [record, null]: one bad code neither
        poisons nor reorders the batch (the C1 lesson, held here)."""
        r = rest_client.post(
            "/lookup",
            json={"codes": [_code("44054006"), _code("99999999")]},
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 2
        assert results[0]["code"] == "44054006"
        assert results[1] is None

    def test_p21_mixed_source_isolated(self, rest_client):
        """[valid-source, unknown-source] → [record, null] as well."""
        r = rest_client.post(
            "/lookup",
            json={
                "codes": [
                    _code("44054006"),
                    _code("44054006", "NOTASAB"),
                ]
            },
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert results[0]["code"] == "44054006"
        assert results[1] is None


class TestRESTControls:
    """Controls — strongest validation of the four surfaces, fresh."""

    def test_c10_whitespace_code_400(self, rest_client):
        r = rest_client.post("/lookup", json={"codes": [_code("   ")]})
        assert r.status_code == 400
        assert "non-empty" in r.json()["detail"]

    def test_c11_uri_source_400(self, rest_client):
        r = rest_client.post(
            "/lookup",
            json={"codes": [_code("44054006", "http://snomed.info/sct")]},
        )
        assert r.status_code == 400
        assert "SAB" in r.json()["detail"]

    def test_c12_empty_source_422(self, rest_client):
        r = rest_client.post("/lookup", json={"codes": [{"source": "", "code": "X"}]})
        assert r.status_code == 422

    def test_c13_map_target_required(self, rest_client):
        r = rest_client.post("/map", json={"codes": [_code("44054006")]})
        assert r.status_code == 400
        assert "must not be empty" in r.json()["detail"]

    def test_c14_map_unknown_target_400(self, rest_client):
        r = rest_client.post(
            "/map",
            json={
                "codes": [_code("44054006")],
                "target_sources": ["NOTASAB"],
            },
        )
        assert r.status_code == 400
        assert "not found" in r.json()["detail"]

    def test_c15_hierarchy_direction_enum(self, rest_client):
        r = rest_client.post(
            "/hierarchy",
            json={"codes": [_code("44054006")], "direction": "sideways"},
        )
        assert r.status_code == 422

    def test_c16_empty_codes_empty_results(self, rest_client):
        r = rest_client.post("/lookup", json={"codes": []})
        assert r.status_code == 200
        assert r.json() == {"results": []}

    def test_c17_health_ready(self, rest_client):
        r = rest_client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["ready"] is True
        assert "sources" in body and "version" in body

    def test_c18_openapi_enumerates_routes(self, rest_client):
        r = rest_client.get("/openapi.json")
        assert r.status_code == 200
        paths = set(r.json()["paths"])
        for route in (
            "/health",
            "/lookup",
            "/map",
            "/hierarchy",
            "/resolve",
        ):
            assert route in paths
