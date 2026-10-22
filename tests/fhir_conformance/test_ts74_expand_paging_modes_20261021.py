"""TS-74 $expand paging matrix on filter + compose modes (2026-10-21).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261021d).
FRESH EVIDENCE — live probes executed this run (real BM25 index for
filter mode); no prior-run results cited.

Scope: the count/offset paging matrix on the two modes TS-31's
controls did NOT cover (TS-31 pinned url-mode paging; filter and
compose paging were never matrixed).

RESULT: ZERO FINDINGS — controls-only iteration (8th clean surface).
Paging is uniform and correct across both modes: stable TRUE totals
under windows (total=3 at count=2 — the filter mode's window covers
its universe, no +1-probe semantics), disjoint slices that reassemble
the full set (off=0 cnt=2 ∪ off=2 cnt=2 == full), past-end offsets
return empty contains without error, negative offsets and zero counts
422 loudly, and the EC params echo carries the paging params
(offset=2 visible). The P1 registration (expansion.offset never
echoed) re-verified: keys are contains/params/timestamp/total — no
offset field; unchanged from its registration.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"


@pytest.fixture(scope="module")
def expand_client(tmp_path_factory):
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    from medterm4ds.services.search import DEFAULT_SEARCH_INDEX_DIR

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts74_page") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=DEFAULT_SEARCH_INDEX_DIR,
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


COMPOSE_VS = {
    "resourceType": "ValueSet",
    "compose": {
        "include": [
            {
                "system": SNOMED_URI,
                "concept": [
                    {"code": "73211009"},
                    {"code": "44054006"},
                    {"code": "3738000"},
                ],
            },
        ],
    },
}


class TestFilterModePaging:
    """Controls — filter-mode count/offset matrix."""

    def test_f10_stable_true_total(self, expand_client):
        """total=3 (the universe) even at count=2 — filter mode's
        window covers its universe; no lower-bound semantics."""
        r = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 2},
        )
        exp = r.json()["expansion"]
        assert exp["total"] == 3
        assert len(exp["contains"]) == 2

    def test_f11_disjoint_slices_reassemble(self, expand_client):
        page1 = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 2, "offset": 0},
        ).json()["expansion"]
        page2 = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 2, "offset": 2},
        ).json()["expansion"]
        full = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 10},
        ).json()["expansion"]
        union = [e["code"] for e in page1["contains"]] + [
            e["code"] for e in page2["contains"]
        ]
        assert sorted(union) == sorted(
            e["code"] for e in full["contains"]
        )

    def test_f12_past_end_empty(self, expand_client):
        """Past-end: contains OMITTED entirely (the TS-31-documented
        builder shape — empty lists are not emitted; both 0..* forms
        accepted)."""
        r = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 2, "offset": 4},
        )
        exp = r.json()["expansion"]
        assert not exp.get("contains", [])
        assert exp["total"] == 3

    @pytest.mark.parametrize("bad", [{"offset": -1}, {"count": 0}])
    def test_f13_bounds_422(self, expand_client, bad):
        r = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", **bad},
        )
        assert r.status_code == 422


class TestComposeModePaging:
    """Controls — compose-mode count/offset matrix (POST)."""

    def test_c10_page_slices(self, expand_client):
        page1 = expand_client.post(
            "/fhir/ValueSet/$expand",
            json=COMPOSE_VS,
            params={"count": 2, "offset": 0},
        ).json()["expansion"]
        page2 = expand_client.post(
            "/fhir/ValueSet/$expand",
            json=COMPOSE_VS,
            params={"count": 2, "offset": 2},
        ).json()["expansion"]
        assert page1["total"] == page2["total"] == 3
        assert [e["code"] for e in page1["contains"]] == [
            "73211009", "44054006",
        ]
        assert [e["code"] for e in page2["contains"]] == ["3738000"]


class TestEchoControls:
    """Controls — EC params echo carries paging; P1 unchanged."""

    def test_e10_params_echo_includes_offset(self, expand_client):
        r = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 2, "offset": 2},
        )
        params = r.json()["expansion"]["params"]
        assert "offset=2" in params
        assert "filter=diabetes" in params

    def test_e11_offset_field_still_absent(self, expand_client):
        """P1 re-verified: expansion.offset is still never emitted
        (keys are contains/params/timestamp/total) — unchanged from
        its registration; no drift."""
        r = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 2, "offset": 2},
        )
        assert "offset" not in r.json()["expansion"]
