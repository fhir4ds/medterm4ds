"""TS-01 SKEPTIC maintenance resweep (2026-10-01, fresh-sweep run).

Chunk: TS-01 Terminology Service RESTful API Conformance (§4.7.1.1).
Personality: SKEPTIC (maintenance idle-capacity iteration, worktree
maint/spec-comp-20261001). FRESH EVIDENCE — every probe in this file was
generated and executed this run; no prior-run results cited.

Lens coverage (hostile inputs NOT exercised by the prior TS-01 suites —
test_ts01_skeptic.py, test_ts01_skeptic_resweep.py, test_ts01_explorer*.py
were surveyed for gaps first):

L1 duplicate query parameters (mode, _format, search url) — RFC 3986 §3.4
   leaves repeated params undefined; the server must not 5xx or
   misdispatch.
L2 path/hostile resource ids (traversal ../, slashed a/b, bare %, ..) on
   the READ interaction — expect 404/405 OperationOutcome, never 500 or
   filesystem probing.
L3 control characters and whitespace shapes in mode (NUL, trailing,
   leading, embedded &) — expect 400 OperationOutcome per the handler's
   input-validation contract.
L4 malformed q-values in Accept (out-of-range q=5, negative q=-1,
   overflow q=1e999, non-numeric q=abc) per RFC 7231 §5.3.1 — the
   negotiation must stay deterministic, never 5xx.
L5 cross-shape sanity: metadata under every hostile Accept still returns
   a FHIR MIME type and a parseable resource.

Verbatim spec mandate (§4.7.1.1): "Servers SHALL ... support the
capabilities interaction" and §3.1.0.1.9: "Servers SHALL support
server-driven content negotiation"; §3.1.0.1.9 error paths use
OperationOutcome. Every probe below asserts one of those three.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def hostile_client(tmp_path_factory):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts01_maint") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(tmp_path_factory.mktemp("no_index")),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestL1DuplicateQueryParams:
    """L1 — repeated query parameters must not 5xx or misdispatch."""

    def test_m10_duplicate_mode_last_wins_no_5xx(self, hostile_client):
        r = hostile_client.get("/fhir/metadata?mode=terminology&mode=full")
        assert r.status_code == 200, r.text
        assert r.json()["resourceType"] in (
            "CapabilityStatement", 
            "TerminologyCapabilities",
        )

    def test_m11_duplicate_mode_reversed_still_defined(self, hostile_client):
        r = hostile_client.get("/fhir/metadata?mode=full&mode=terminology")
        assert r.status_code == 200
        # Either dispatch is RFC-permissible; the contract is: ONE defined
        # payload, FHIR content type, no 5xx.
        assert r.headers["content-type"].startswith("application/fhir+")
        assert r.json()["resourceType"] in (
            "CapabilityStatement", "TerminologyCapabilities",
        )

    def test_m12_duplicate_format_no_5xx_fhir_mime(self, hostile_client):
        r = hostile_client.get("/fhir/metadata?_format=xml&_format=json")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/fhir+")

    def test_m13_duplicate_search_url_total_is_int(self, hostile_client):
        r = hostile_client.get(
            "/fhir/CodeSystem?url=http://snomed.info/sct&url=http://loinc.org"
        )
        assert r.status_code == 200
        body = r.json()
        assert body["resourceType"] == "Bundle"
        assert isinstance(body.get("total"), int)


class TestL2HostileResourceIds:
    """L2 — traversal/slashed/percent ids: 4xx OperationOutcome, never 500."""

    @pytest.mark.parametrize(
        "raw_path",
        [
            "/fhir/CodeSystem/..%2F..%2Fetc",
            "/fhir/CodeSystem/a%2Fb",
            "/fhir/CodeSystem/%25",
        ],
    )
    def test_m20_hostile_id_4xx_operationoutcome(self, hostile_client, raw_path):
        r = hostile_client.get(raw_path)
        assert 400 <= r.status_code < 500, (
            f"{raw_path} must not 5xx (got {r.status_code})"
        )
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_m21_dotdot_id_405_not_500(self, hostile_client):
        # '..' collides with the /fhir/{type}/{id}[/...] route family as a
        # bare segment — the defined answer is 405 OperationOutcome.
        r = hostile_client.get("/fhir/CodeSystem/..")
        assert r.status_code in (404, 405)
        assert r.json()["resourceType"] == "OperationOutcome"


class TestL3ModeControlChars:
    """L3 — control characters / whitespace in mode → 400 OperationOutcome."""

    @pytest.mark.parametrize(
        "mode",
        ["full\x00", "full ", " full", "full\t", "full&mode=terminology"],
    )
    def test_m30_control_char_mode_400(self, hostile_client, mode):
        r = hostile_client.get("/fhir/metadata", params={"mode": mode})
        assert r.status_code == 400, (
            f"mode={mode!r} must be rejected as invalid input, got {r.status_code}"
        )
        assert r.json()["resourceType"] == "OperationOutcome"


class TestL4MalformedQValues:
    """L4 — malformed Accept q-values keep negotiation deterministic."""

    @pytest.mark.parametrize(
        "accept,expect_xml",
        [
            # Out-of-range q=5 (RFC 7231 §5.3.1 caps at 1): lenient parse,
            # XML entry wins — deterministic, never 5xx.
            ("application/fhir+xml;q=5, application/fhir+json;q=0.9", True),
            # Negative q means "not acceptable" — entry skipped, JSON wins.
            ("application/fhir+xml;q=-1, application/fhir+json;q=0.1", False),
            # Overflow q=1e999 parses as inf — XML wins deterministically.
            ("application/fhir+xml;q=1e999, application/fhir+json;q=0.5", True),
            # Non-numeric q=abc treated as default q=1 — XML wins.
            ("application/fhir+xml;q=abc, application/fhir+json;q=0.1", True),
            # text/json is a valid JSON-family MIME (§3.1.0.1.9) and
            # outranks a lower-q XML entry.
            ("text/json;q=1, application/fhir+xml;q=0.9", False),
        ],
    )
    def test_m40_malformed_q_deterministic(self, hostile_client, accept, expect_xml):
        r = hostile_client.get("/fhir/metadata", headers={"Accept": accept})
        assert r.status_code == 200
        ct = r.headers["content-type"]
        assert ("fhir+xml" in ct) is expect_xml, (
            f"Accept {accept!r}: content-type {ct!r}, expected "
            f"{'XML' if expect_xml else 'JSON'}"
        )


class TestL5CrossShapeSanity:
    """L5 — every hostile Accept still yields a FHIR MIME + parseable body."""

    @pytest.mark.parametrize(
        "accept",
        [
            "application/fhir+xml;q=5, application/fhir+json;q=0.9",
            "application/fhir+xml;q=-1, application/fhir+json;q=0.1",
            "application/fhir+xml;q=1e999, application/fhir+json;q=0.5",
            "application/fhir+xml;q=abc, application/fhir+json;q=0.1",
            "text/json;q=1, application/fhir+xml;q=0.9",
            ",,,",
            ";;;;",
        ],
    )
    def test_m50_metadata_survives_hostile_accept(self, hostile_client, accept):
        r = hostile_client.get("/fhir/metadata", headers={"Accept": accept})
        assert r.status_code == 200
        ct = r.headers["content-type"]
        assert ct.startswith("application/fhir+"), f"non-FHIR MIME {ct!r}"
        if "fhir+json" in ct:
            assert r.json()["resourceType"] == "CapabilityStatement"
        else:
            # XML path: must contain the root element, proving a parseable
            # body came back (not a truncated/500 text/plain).
            assert "<CapabilityStatement" in r.text
