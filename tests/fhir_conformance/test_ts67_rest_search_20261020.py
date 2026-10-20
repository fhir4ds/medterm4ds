"""TS-67 REST facade /search-names + body-param matrix (2026-10-20).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261020).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the REST facade's /search-names endpoint (never audited —
TS-25 covered /resolve modes, PY1/PY2 the pf miss shape) plus the
facade-wide unknown-body-param posture vs the FHIR surface's
fixbatch-era rejections.

SN1 (LOW) — /search-names truncation unobservable (MC1's REST
    branch). limit=2 → 2 rows; limit=9 → 3 (universe=3); envelope is
    bare {results} with no count, truncated flag, or has-more — the
    SC1/VS02-01/MC1 family on the fourth surface. Pinned sn10/sn11.

SN2 (LOW) — unknown body params silently accepted across the REST
    facade (/search-names, /patient-friendly, /conceptmap/
    patient-friendly all 200 with bogus body keys) while the FHIR
    surface 400s the same class (EA/W4). Per-surface doctrine:
    FastAPI Body models tolerate extras; findings-only note that the
    two facades' strictness postures now DIVERGE after fixbatches
    2-4 (a client hardening against FHIR typos gets no protection
    here). Pinned sn20/sn21.

Controls (fresh): whitespace query 400 'query must not be empty';
    missing query 422 (typed); limit floor enforced by the model
    (0 → 422 greater_than_equal); GET 405 (POST-only documented);
    row shape carries source/code/name/cui/tty/aui/match_type;
    pf + conceptmap row shapes pinned (friendly_source/match_type/
    matched_via family).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

SNOMED = "SNOMEDCT_US"
T2DM = "44054006"


@pytest.fixture(scope="module")
def rest_client(tmp_path_factory):
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts67_rest") / "umls.duckdb"
    _make_conformance_db(db)
    app = create_app(ApiSettings(db_path=db, prepare_cache=False))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestSN1TruncationUnobservable:
    """SN1 — capped results indistinguishable (MC1's REST branch)."""

    def test_sn10_limit2_returns_2(self, rest_client):
        """limit=2 → exactly 2 rows, no count/truncated field — with
        universe=3 this was a cap the envelope does not reveal. Flip
        when count/truncation fields land (MC1's fix family)."""
        r = rest_client.post(
            "/search-names", json={"query": "diabetes", "limit": 2}
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"results"}
        assert len(body["results"]) == 2

    def test_sn11_limit9_reveals_universe(self, rest_client):
        """The same query at limit=9 returns 3 — the
        fixture-establishing control for sn10."""
        r = rest_client.post(
            "/search-names", json={"query": "diabetes", "limit": 9}
        )
        assert r.status_code == 200
        assert len(r.json()["results"]) == 3


class TestSN2UnknownBodyParams:
    """SN2 — facade tolerates unknown body keys (FHIR 400s them)."""

    def test_sn20_search_names_bogus_key(self, rest_client):
        """bogus body key → 200 (dropped). Contrast: FHIR $expand/
        $translate/$closure 400 the same class post-fixbatch. Flip if
        the facade adopts strict body validation."""
        r = rest_client.post(
            "/search-names",
            json={"query": "diabetes", "bogus": 1},
        )
        assert r.status_code == 200

    def test_sn21_pf_bogus_key(self, rest_client):
        r = rest_client.post(
            "/patient-friendly",
            json={
                "codes": [{"source": SNOMED, "code": T2DM}],
                "bogus": 1,
            },
        )
        assert r.status_code == 200


class TestTS67Controls:
    """Controls — validation posture held."""

    def test_c10_whitespace_query_400(self, rest_client):
        r = rest_client.post("/search-names", json={"query": "   "})
        assert r.status_code == 400
        assert "empty" in r.json()["detail"]

    def test_c11_missing_query_422(self, rest_client):
        r = rest_client.post("/search-names", json={"limit": 2})
        assert r.status_code == 422

    def test_c12_limit_floor_422(self, rest_client):
        r = rest_client.post(
            "/search-names", json={"query": "diabetes", "limit": 0}
        )
        assert r.status_code == 422

    def test_c13_get_405(self, rest_client):
        r = rest_client.get(
            "/search-names", params={"query": "diabetes"}
        )
        assert r.status_code == 405

    def test_c14_row_shape(self, rest_client):
        r = rest_client.post(
            "/search-names", json={"query": "diabetes", "limit": 2}
        )
        first = r.json()["results"][0]
        assert {"source", "code", "name", "match_type"} <= set(first)

    def test_c15_pf_row_shape(self, rest_client):
        r = rest_client.post(
            "/patient-friendly",
            json={"codes": [{"source": SNOMED, "code": T2DM}]},
        )
        first = r.json()["results"][0]
        assert {
            "source", "code", "name", "match_type", "friendly_source",
        } <= set(first)
