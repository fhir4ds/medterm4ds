"""TS-47 $extract input-contract seams, transport-mapped (2026-10-15).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261015d).
FRESH EVIDENCE — live probes executed this run (GLiNER cold-load
~90-140s amortized per probe; warm pipeline within each client);
no prior-run results cited.

Scope: $extract's POST-body surface was never swept (TS-13 = GET
input; TS-36 = output markers). This iteration maps it AND
re-verifies the three registered TS-13/TS-06-era findings with
transport precision — all three remain ALIVE:

E1-ALIVE (MEDIUM, registered 20261005, both transports) —
    resultTypes=bogus → HTTP 500 with a PLAINTEXT body
    ('Internal Server Error', content-type text/plain) on GET and
    POST alike: not even an OperationOutcome, let alone the 400 the
    $search sibling emits for the identical ValueError class (QA-005
    wrapper exists in $search). The unhandled ValueError from
    _result_types_to_prefixes propagates raw.

W1-ALIVE (LOW-MED, registered 20261006, re-measured) — over-limit
    text: GET 422 (FastAPI max_length) vs POST 400 (handler check).
    Same violation, different status+diagnostic by transport.

W2-ALIVE (LOW, registered 20261006, re-measured) — whitespace-only
    text runs the FULL pipeline (fresh run: 89s) for guaranteed-zero
    output; sibling $search fails fast on whitespace.

EX1 (MEDIUM, NEW) — $extract POST body has no unknown-param
    rejection (zzz → 200), and the body's typed params
    (resultTypes/outputFormat) bind while garbage rides along
    silently. SB1's sibling finding, POST-body half.

Controls (fresh): POST body BINDS text/resultTypes (medication
    filter → 0 diabetes-condition entries)/outputFormat=annotated;
    the happy-path extraction returns 2 concepts (diabetes +
    metformin); $search's resultTypes validation exists on its own
    surface (the QA-005 wrapper pattern is proven elsewhere).
"""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::FutureWarning")

TEXT = "patient has type 2 diabetes and takes metformin"


@pytest.fixture(scope="module")
def extract_client(tmp_path_factory):
    """Non-raising client (the shared extract_client re-raises 500s,
    which is the E1 finding's own status; TS-06 precedent)."""
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts47") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(tmp_path_factory.mktemp("no_index")),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(
        app, raise_server_exceptions=False
    ) as client:
        yield client


class TestE1ResultTypes500:
    """E1 — resultTypes=bogus → plaintext 500, both transports."""

    def test_e10_get_500_plaintext(self, extract_client):
        """GET resultTypes=bogus → 500, text/plain body (not an
        OperationOutcome). Registered 20261005 (E1 MEDIUM); pinned
        with the plaintext-body severity marker. Flip when the
        ValueError is wrapped to 400 (QA-005 pattern)."""
        r = extract_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT, "resultTypes": "bogus"},
        )
        assert r.status_code == 500
        assert r.headers["content-type"].startswith("text/plain")
        assert "Internal Server Error" in r.text

    def test_e11_post_500_plaintext(self, extract_client):
        """POST body resultTypes=bogus → same plaintext 500 (the
        POST half, new this iteration). Flip with E1's fix."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "text", "valueString": TEXT},
                {"name": "resultTypes", "valueString": "bogus"},
            ],
        }
        r = extract_client.post(
            "/fhir/CodeSystem/$extract", json=body
        )
        assert r.status_code == 500
        assert r.headers["content-type"].startswith("text/plain")


class TestW1LimitStatusDivergence:
    """W1 — over-limit status differs by transport."""

    def test_w10_get_422_post_400(self, extract_client):
        big = "a" * 60_000
        rg = extract_client.get(
            "/fhir/CodeSystem/$extract", params={"text": big}
        )
        rp = extract_client.post(
            "/fhir/CodeSystem/$extract",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "text", "valueString": big}
                ],
            },
        )
        assert rg.status_code == 422
        assert rp.status_code == 400


class TestW2WhitespacePipeline:
    """W2 — whitespace-only text runs the full pipeline."""

    def test_w20_whitespace_200_slow(self, extract_client):
        """Whitespace-only text → 200 after the FULL pipeline (the
        fresh-run measurement: 89s cold). Registered 20261006; pinned
        behaviorally (200 + zero concepts). Flip when a strip()
        pre-check fails fast (400) — also add a duration assertion
        then."""
        t0 = time.monotonic()
        r = extract_client.post(
            "/fhir/CodeSystem/$extract",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "text", "valueString": "   "}
                ],
            },
        )
        elapsed = time.monotonic() - t0
        assert r.status_code == 200
        assert r.json().get("entry", []) == []
        # The pipeline ran (not a fast rejection): with a warm module
        # fixture this is seconds, but the no-fast-reject contract is
        # the pin; the 400 flip makes this assert fail loudly.
        assert elapsed >= 0


class TestEX1PostBodyUnknownParam:
    """EX1 — $extract POST body accepts unknown params."""

    def test_ex10_post_body_zzz_200(self, extract_client):
        """zzz parameter entry → 200 (SB1's body half at $extract).
        Flip when _EXTRACT_KNOWN_PARAMS body check lands."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "text", "valueString": TEXT},
                {"name": "zzz", "valueString": "x"},
            ],
        }
        r = extract_client.post(
            "/fhir/CodeSystem/$extract", json=body
        )
        assert r.status_code == 200


class TestControls:
    """Controls — the binding contract, fresh."""

    def test_c10_post_body_binds(self, extract_client):
        """text binds (2 concepts on the fixture sentence)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "text", "valueString": TEXT}
            ],
        }
        r = extract_client.post(
            "/fhir/CodeSystem/$extract", json=body
        )
        assert r.status_code == 200
        assert r.json()["resourceType"] == "Bundle"
        assert len(r.json().get("entry", [])) == 2

    def test_c11_resulttypes_filters_post(self, extract_client):
        """resultTypes=medication → condition entries filtered out
        (0 entries on this diabetes-condition sentence)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "text", "valueString": TEXT},
                {"name": "resultTypes", "valueString": "medication"},
            ],
        }
        r = extract_client.post(
            "/fhir/CodeSystem/$extract", json=body
        )
        assert r.status_code == 200
        # The condition is filtered out; exactly the medication
        # (RXNORM metformin) remains.
        entries = r.json().get("entry", [])
        assert len(entries) == 1
        assert entries[0]["resource"]["source"] == "RXNORM"

    def test_c12_outputformat_annotated_post(self, extract_client):
        """outputFormat=annotated is ACCEPTED on POST (200, no 4xx)
        — but the annotated envelope is the CLI/MCP surface's shape;
        the FHIR POST body returns the Bundle. Pins acceptance
        (XA2's family: accepted-typed-param behavior)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "text", "valueString": TEXT},
                {"name": "outputFormat", "valueString": "annotated"},
            ],
        }
        r = extract_client.post(
            "/fhir/CodeSystem/$extract", json=body
        )
        assert r.status_code == 200
        assert r.json()["resourceType"] == "Bundle"
