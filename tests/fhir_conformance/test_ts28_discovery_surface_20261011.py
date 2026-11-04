"""TS-28 discovery-surface controls (2026-10-11).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261011b).
FRESH EVIDENCE — live probes executed this run against the REST
facade and the CLI; no prior-run results cited.

Scope: the discovery/inventory surface — /search-names, /code-ttys,
/sample-codes, /sources, /source-stats (+ CLI counterparts). After
five defect-bearing surfaces in a row, this one is (almost entirely)
CONFORMANT: this iteration pins the held contracts so drift fails
loudly, and registers the single oddity found.

DV1 (LOW, NEW) — /sources is POST-only.
    GET /sources → 405; the inventory endpoint every REST convention
    makes a GET. All 13 sibling routes are POST (the facade's batch
    convention — documented design), so this is consistent
    INTERNALLY; the oddity is registering the convention itself:
    a read-only inventory list requiring a POST body is unidiomatic
    for external consumers and undocumented. Fix shape: accept GET
    (empty-body default) alongside POST, or document the batch-POST
    convention in the OpenAPI description. Pinned: d10.

Controls (fresh, REST+CLI parity asserted where applicable):
    search-names: whitespace query 400 ('query must not be empty');
    empty query 422 (pydantic min_length); limit>=1 enforced both
    surfaces (422 / argparse exit 2); result shape carries full atom
    fields + match_type taxonomy (prefix/contains observed live).
    sample-codes + source-stats: unknown source 400 ('source(s) not
    found in this database') both surfaces — the QC-195 boundary
    HELD here (contrast BL1's bulk gap). code-ttys: unknown code →
    200 empty list (a clean miss convention — distinct from
    /lookup's null entry and /patient-friendly's identifier echo;
    noted for the miss-shape matrix, not itself a defect).
    /sources POST: full inventory (9 sources, sorted).
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

    db = tmp_path_factory.mktemp("ts28_rest") / "umls.duckdb"
    _make_conformance_db(db)
    app = create_app(ApiSettings(db_path=db))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestDV1SourcesPostOnly:
    """DV1 — the inventory endpoint requires POST."""

    def test_d10_get_sources_405(self, rest_client):
        """GET /sources → 405 (POST-only). Flip if a GET form is
        ever accepted (or keep + document the convention)."""
        r = rest_client.get("/sources")
        assert r.status_code == 405


class TestSearchNamesControls:
    """Controls — search-names validation + shape, fresh."""

    def test_c10_whitespace_query_400(self, rest_client):
        r = rest_client.post("/search-names", json={"query": "   "})
        assert r.status_code == 400
        assert "must not be empty" in r.json()["detail"]

    def test_c11_empty_query_422(self, rest_client):
        r = rest_client.post("/search-names", json={"query": ""})
        assert r.status_code == 422

    def test_c12_limit_floor(self, rest_client):
        for bad in (0, -1):
            r = rest_client.post(
                "/search-names",
                json={"query": "diabetes", "limit": bad},
            )
            assert r.status_code == 422

    def test_c13_result_shape(self, rest_client):
        r = rest_client.post(
            "/search-names",
            json={"query": "diabetes", "limit": 5},
        )
        results = r.json()["results"]
        assert results, "fixture has diabetes names"
        for field in (
            "source",
            "code",
            "name",
            "cui",
            "aui",
            "tty",
            "match_type",
        ):
            assert field in results[0]
        match_types = {e["match_type"] for e in results}
        assert match_types <= {"exact", "prefix", "contains"}


class TestDiscoveryControls:
    """Controls — inventory + ttys + miss conventions, fresh."""

    def test_c14_sample_codes_unknown_source_400(self, rest_client):
        """QC-195 boundary HELD here (contrast BL1's bulk gap)."""
        r = rest_client.post("/sample-codes", json={"sources": ["NOTASAB"]})
        assert r.status_code == 400

    def test_c15_source_stats_unknown_source_400(self, rest_client):
        r = rest_client.post("/source-stats", json={"sources": ["NOTASAB"]})
        assert r.status_code == 400

    def test_c16_code_ttys_unknown_empty(self, rest_client):
        """Unknown code → 200 [] (the cleanest miss convention on
        the facade — noted for the miss-shape matrix)."""
        r = rest_client.post(
            "/code-ttys",
            json={"codes": [{"source": SNOMED, "code": "99999999"}]},
        )
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_c17_sources_inventory(self, rest_client):
        r = rest_client.post("/sources", json={})
        assert r.status_code == 200
        sources = [s["source"] for s in r.json()["results"]]
        assert len(sources) >= 8  # the fixture's 8 SABs
        assert sources == sorted(sources)
