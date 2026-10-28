"""TS-46 $search input-contract seams (2026-10-15).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261015c).
FRESH EVIDENCE — live probes executed this run against the default
BM25 index dir (reports/fhir4px naming dist); no prior-run results
cited.

Scope: the $search/$extract pair is the last surface family without
the fail-loud treatment (XA1 registered the tail at iter 35; FA2/FA4/
FA5 mapped the CodeSystem ops' batch/POST seams). This suite pins the
$search input contract on BOTH transports with a live index.

POSITIVE finding first: the POST Parameters body BINDS — query,
searchMode (bogus → 400), count (valueInteger honored), and
resultTypes (filters to LOINC on both transports — the fixbatch6 G1
forwarding verified at the body layer too).

SB1 (MEDIUM, NEW) — $search has no unknown-param rejection on
    EITHER transport. GET zzz=1/bogusMode → 200; POST body zzz →
    200. Every CodeSystem/ValueSet/ConceptMap op rejects unknowns
    (fixbatches 2-4); the custom $search op (ADVERTISED in the
    CapabilityStatement) never joined the family. Same for
    $extract (BC1's 404-from-batch sibling surface).
    Fix shape: _SEARCH_KNOWN_PARAMS/_EXTRACT_KNOWN_PARAMS via
    reject_unknown_query_params + body name checks. Flip pins
    s10-s13.

SB2 (MEDIUM, refines XA1) — engine param accepted, never validated,
    both transports. engine=bogus → 200 running the DEFAULT pipeline
    on GET and POST alike. XA1 (iter 35) pinned the GET tail; this
    pins the POST body AND registers the precise mechanism: the
    engine In-param is declared in the signature but no enumeration
    check exists (searchMode DOES validate — bogus searchMode 400s —
    so the op knows how; the engine param simply lacks the check).
    Fix shape: validate engine against the known engine set (400),
    mirroring searchMode's validation. Flip pins s20/s21.

Controls (fresh): searchMode=bogus 400 on POST (validation exists for
    the sibling param); whitespace/empty query 400; count honored
    (2 entries at count=2); resultTypes filters to loinc-only on GET
    and POST (G1 held both transports); semantic mode 200 (model
    fetched, ~134s cold); lexical default returns 3 systems.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::FutureWarning")


@pytest.fixture(scope="module")
def search_client(tmp_path_factory):
    """search_client twin with the DEFAULT BM25 index dir wired.

    The shared fixture points search_index_dir at an empty tmp dir
    (503 without an index); these probes need the real lexical stack
    (the model dist naming indexes at DEFAULT_SEARCH_INDEX_DIR).
    """
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import (
        DEFAULT_SEARCH_INDEX_DIR,
        FhirApiSettings,
        create_fhir_app,
    )

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts46") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=DEFAULT_SEARCH_INDEX_DIR,
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app) as client:
        yield client


def _systems_of(r) -> set[str]:
    return {
        e["resource"]["system"]
        for e in r.json().get("entry", [])
    }






def _bm25_index_available() -> bool:
    """CI has the SapBERT model (downloads) but NOT the lexical BM25
    indexes — every suite needing real lexical/hybrid results guards
    on the index dir actually containing indexes."""
    from pathlib import Path

    from medterm4ds.apps.fhir_api import DEFAULT_SEARCH_INDEX_DIR

    d = Path(DEFAULT_SEARCH_INDEX_DIR)
    return d.exists() and any(d.glob("*_bm25.json"))

pytest.mark.skipif(not _bm25_index_available(), reason="BM25 indexes absent (CI)")
class TestSB1UnknownParamsBothTransports:
    """SB1 — $search accepts unknown params everywhere."""

    def test_s10_get_unknown_query_param(self, search_client):
        """GET zzz=1 → 200 (every other op 400s since fixbatch2-4).
        Flip when _SEARCH_KNOWN_PARAMS lands."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": "diabetes", "zzz": "1"},
        )
        assert r.status_code == 200

    def test_s11_get_unknown_mode_like_param(self, search_client):
        """A second unknown spelling (bogusMode) also 200s. Flip with
        s10."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": "diabetes", "bogusMode": "x"},
        )
        assert r.status_code == 200

    def test_s12_post_body_unknown_param(self, search_client):
        """POST body zzz → 200 (FA4 family; $search never got the
        body-side check). Flip with the body name-check."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "query", "valueString": "diabetes"},
                {"name": "zzz", "valueString": "x"},
            ],
        }
        r = search_client.post("/fhir/CodeSystem/$search", json=body)
        assert r.status_code == 200

    def test_s13_sibling_ops_reject_control(self, search_client):
        """Premise control: the family exists — $lookup 400s the same
        zzz param (fixbatch3), so the gap is $search-specific."""
        r = search_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": "http://snomed.info/sct",
                "code": "44054006", "zzz": "1",
            },
        )
        assert r.status_code == 400


pytest.mark.skipif(not _bm25_index_available(), reason="BM25 indexes absent (CI)")
class TestSB2EngineNeverValidated:
    """SB2 — engine param accepted, unvalidated, both transports."""

    def test_s20_get_bogus_engine_200(self, search_client):
        """GET engine=bogus → 200 on the DEFAULT pipeline (XA1 GET
        pin; kept here as the transport-complete pair). Flip when
        engine validates (400)."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": "diabetes", "engine": "bogus"},
        )
        assert r.status_code == 200

    def test_s21_post_bogus_engine_200(self, search_client):
        """POST body engine=bogus → 200 (NEW this iteration — XA1's
        POST half). Flip when engine validates."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "query", "valueString": "diabetes"},
                {"name": "engine", "valueString": "bogus"},
            ],
        }
        r = search_client.post("/fhir/CodeSystem/$search", json=body)
        assert r.status_code == 200

    def test_s22_searchmode_validates_control(self, search_client):
        """Control: the SIBLING param validates — searchMode=bogus
        400s on POST — so the op has the machinery; engine lacks the
        check."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "query", "valueString": "diabetes"},
                {"name": "searchMode", "valueString": "bogus"},
            ],
        }
        r = search_client.post("/fhir/CodeSystem/$search", json=body)
        assert r.status_code == 400


pytest.mark.skipif(not _bm25_index_available(), reason="BM25 indexes absent (CI)")
class TestControls:
    """Controls — the binding contract, fresh."""

    def test_c10_query_validation(self, search_client):
        """Empty query → 422 (typed Query min_length); whitespace →
        400 (handler strip check). Both fail loud."""
        r = search_client.get(
            "/fhir/CodeSystem/$search", params={"query": ""}
        )
        assert r.status_code == 422
        r = search_client.get(
            "/fhir/CodeSystem/$search", params={"query": "   "}
        )
        assert r.status_code == 400

    def test_c11_post_count_binds(self, search_client):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "query", "valueString": "diabetes"},
                {"name": "count", "valueInteger": 2},
            ],
        }
        r = search_client.post("/fhir/CodeSystem/$search", json=body)
        assert r.status_code == 200
        assert len(r.json()["entry"]) == 2

    def test_c12_resulttypes_filters_get(self, search_client):
        """G1 held: GET resultTypes=lab → loinc-only."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": "diabetes", "resultTypes": "lab"},
        )
        assert r.status_code == 200
        systems = _systems_of(r)
        assert systems and all(
            "loinc" in s for s in systems
        ), systems

    def test_c13_resulttypes_filters_post(self, search_client):
        """G1 held at the BODY layer: POST resultTypes=lab →
        loinc-only (the fixbatch6 forwarding verified on both
        transports)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "query", "valueString": "diabetes"},
                {"name": "resultTypes", "valueString": "lab"},
            ],
        }
        r = search_client.post("/fhir/CodeSystem/$search", json=body)
        assert r.status_code == 200
        systems = _systems_of(r)
        assert systems and all(
            "loinc" in s for s in systems
        ), systems

    def test_c14_lexical_default_multi_system(self, search_client):
        """Unfiltered lexical default spans 3 systems (the filter
        premise for c12/c13)."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": "diabetes"},
        )
        assert r.status_code == 200
        assert len(_systems_of(r)) >= 3
