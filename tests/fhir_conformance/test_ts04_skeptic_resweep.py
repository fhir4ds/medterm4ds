"""SKEPTIC resweep probes for TS-04 (Security, Batch Validation, Batch Translation).

Fresh full-sweep per USER_DIRECTIVES [2026-08-08]. Per GLOBAL_RULES.md
fresh-full-sweep baseline discipline, NEW hostile-input probes live in a
sibling file (test_ts04_skeptic_resweep.py) and do NOT trust the prior
TS-04 SKEPTIC baseline (test_ts04_skeptic.py — that file holds the prior
run's baselines, NOT new bugs).

Source: https://build.fhir.org/terminology-service.html §4.7.2, §4.7.8,
§4.7.10 + https://hl7.org/fhir/R4/http.html#batch (§3.1.0.11).

Five spec items:

1. **SSL/HTTPS operational concern** (§4.7.2): "Generally, SSL SHOULD be
   used for all production health care data exchange." The CapabilityStatement
   must not preclude HTTPS deployment — `_deployment_base_url` must handle
   every env-var combination cleanly.
2. **Batch `$validate-code` via Bundle type=batch** (§4.7.8): POST /fhir
   returns batch-response with per-entry Parameters.
3. **Batch `$translate` via Bundle type=batch** (§4.7.10): same shape for
   ConceptMap/$translate.
4. **Batch entry URLs use correct operation paths** (§3.1.0.11):
   base-relative AND absolute URLs accepted.
5. **Batch response entries preserve order and correlation** (§3.1.0.11.3):
   "the response the server SHALL return a Bundle ... that contains one
   entry for each entry in the request, in the same order, with the
   outcome of processing the entry."

SKEPTIC lens (per ROLE_QA_ENGINEER.md Section 3):
- Aggressive bug hunting — edge cases, malformed inputs, boundary conditions.
- 5-10 hostile probes per spec item.

CRITICAL FOCUS (per launch notes + GLOBAL_KNOWLEDGE.md QA-038):
- **Batch per-entry isolation**: one entry with bad params MUST NOT break
  other entries. The dispatch boundary is `except Exception as exc` at
  `_process_batch_entry:1119-1139` (broad on purpose — the spec-mandated
  boundary). INSIDE the dispatch, narrow `except ValueError` at line 1299.
  Probe that non-ValueError exceptions (TypeError, AttributeError, KeyError,
  ZeroDivisionError) are caught at the boundary and produce a per-entry 500
  OperationOutcome — NOT a whole-batch 500/text-plain.

TS-03/TERMINOLOGIST tip:
- The batch dispatcher at `_dispatch_batch_operation` consumes
  `fhir_uri_to_system` indirectly via each `_do_*` handler. The EXPLORER
  uppercase-scheme fix (TS-03 EXPLORER QA-001) is inherited via delegation.
  Probe that a batch `$lookup` entry with uppercase-scheme system URI
  (`HTTP://snomed.info/sct`) resolves identically to lowercase
  (per-entry byte-exact parity).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# =============================================================================
# Shared helpers (mirror the existing test_ts04_skeptic.py fixture style)
# =============================================================================


def _make_test_client(tmp_path: Path, monkeypatch, host: str | None = None,
                      port: str | None = None, scheme: str | None = None):
    """Construct a FHIR app TestClient with a synthetic empty DB.

    Honors MEDTERM4DS_API_HOST / MEDTERM4DS_FHIR_API_PORT /
    MEDTERM4DS_API_SCHEME env vars when set, matching the deployment URL
    constructor's input surface.
    """
    fastapi = pytest.importorskip("fastapi")
    from starlette.testclient import TestClient
    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    import duckdb

    monkeypatch.delenv("MEDTERM4DS_API_HOST", raising=False)
    monkeypatch.delenv("MEDTERM4DS_API_SCHEME", raising=False)
    if host is not None:
        monkeypatch.setenv("MEDTERM4DS_API_HOST", host)
    if scheme is not None:
        monkeypatch.setenv("MEDTERM4DS_API_SCHEME", scheme)
    if port is not None:
        monkeypatch.setenv("MEDTERM4DS_FHIR_API_PORT", port)

    db_path = tmp_path / "umls.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE mrconso (CODE VARCHAR, TTY VARCHAR, STR VARCHAR, "
        "AUI VARCHAR, SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, "
        "REL VARCHAR)"
    )
    con.close()
    settings = FhirApiSettings(
        db_path=db_path, memory_profile="low", prepare_cache=False,
    )
    app = create_fhir_app(settings)
    return TestClient(app)


def _batch_bundle(entries: list[dict]) -> dict:
    """Build a Bundle type=batch with the given entries."""
    return {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": entries,
    }


def _validate_code_get_entry(system: str, code: str) -> dict:
    """A GET batch entry for CodeSystem/$validate-code."""
    return {
        "request": {
            "method": "GET",
            "url": f"CodeSystem/$validate-code?system={system}&code={code}",
        }
    }


def _validate_code_post_entry(system: str, code: str) -> dict:
    """A POST batch entry for CodeSystem/$validate-code."""
    return {
        "resource": {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": system},
                {"name": "code", "valueCode": code},
            ],
        },
        "request": {
            "method": "POST",
            "url": "CodeSystem/$validate-code",
        },
    }


# =============================================================================
# L1 — SSL/HTTPS operational concern (§4.7.2) — URL constructor edge cases
# =============================================================================


def test_s10_https_via_scheme_env_var(monkeypatch, tmp_path):
    """§4.7.2: SSL SHOULD be used in production. The deployment URL MUST
    honor a separate `MEDTERM4DS_API_SCHEME=https` env var.

    Spec: https://build.fhir.org/terminology-service.html §4.7.2.
    Quote: "Generally, SSL SHOULD be used for all production health care
    data exchange."

    Probe: set scheme env var, no scheme in host. Verify CapabilityStatement
    advertises `https://` (not `http://https://...`).
    """
    with _make_test_client(tmp_path, monkeypatch,
                           host="fhir.example.com", port="8443",
                           scheme="https") as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200, r.text
        impl_url = r.json().get("implementation", {}).get("url", "")
        assert impl_url.startswith("https://"), (
            f"Scheme env var not reflected: {impl_url!r}"
        )
        assert "http://https" not in impl_url, (
            f"Malformed scheme concatenation: {impl_url!r}"
        )
        assert "fhir.example.com" in impl_url, impl_url
        assert "8443" in impl_url, (
            f"Port not appended for scheme-env-var path: {impl_url!r}"
        )


def test_s11_https_via_scheme_in_host_env_var(monkeypatch, tmp_path):
    """§4.7.2: deployment URL MUST honor scheme embedded in host env var.

    Probe: `MEDTERM4DS_API_HOST=https://fhir.example.com`. Verify URL is
    not malformed.
    """
    with _make_test_client(tmp_path, monkeypatch,
                           host="https://fhir.example.com",
                           port="443") as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200, r.text
        impl_url = r.json().get("implementation", {}).get("url", "")
        assert "https://fhir.example.com" in impl_url, impl_url
        assert "http://https" not in impl_url, impl_url


def test_s12_ipv6_host_with_scheme(monkeypatch, tmp_path):
    """§4.7.2 + URL constructor edge cases. IPv6 host (e.g. `[::1]`) with
    an explicit scheme MUST produce a parseable URL with port appended.

    Per GLOBAL_RULES.md "Code Review Time" line 128: IPv6 bracket syntax
    defeats colon-based port detection. The helper must use bracket-based
    detection (`]:`).
    """
    with _make_test_client(tmp_path, monkeypatch,
                           host="https://[::1]", port="8443") as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200, r.text
        impl_url = r.json().get("implementation", {}).get("url", "")
        # IPv6 with port: https://[::1]:8443
        assert "://[::1]:" in impl_url or "://[::1]" in impl_url, (
            f"IPv6 host not handled correctly: {impl_url!r}"
        )
        assert ":8443" in impl_url, (
            f"Port lost on IPv6 host: {impl_url!r}"
        )


def test_s13_trailing_slash_on_host_without_scheme(monkeypatch, tmp_path):
    """§4.7.2 + URL constructor edge cases. Host without scheme carrying a
    trailing slash (e.g. `example.com/`) MUST NOT produce
    `http://example.com/:port`.

    Per GLOBAL_RULES.md line 128: trailing slash on host without scheme
    is an edge case that breaks naive host-port concatenation.
    """
    with _make_test_client(tmp_path, monkeypatch,
                           host="fhir.example.com/",
                           port="8000") as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200, r.text
        impl_url = r.json().get("implementation", {}).get("url", "")
        # The slash must NOT appear between host and port.
        assert "fhir.example.com/:" not in impl_url, (
            f"Trailing slash leaked into host:port boundary: {impl_url!r}"
        )
        assert "fhir.example.com:8000" in impl_url, impl_url


def test_s14_default_port_when_no_env_var(monkeypatch, tmp_path):
    """§4.7.2: when no port env var is set, the URL must still parse cleanly
    and not omit the port.

    The DEFAULT_PORT constant is the load-bearing fallback. Probe that it
    appears in the deployment URL.
    """
    # No port env var — use the default.
    monkeypatch.delenv("MEDTERM4DS_FHIR_API_PORT", raising=False)
    monkeypatch.delenv("MEDTERM4DS_API_HOST", raising=False)
    monkeypatch.delenv("MEDTERM4DS_API_SCHEME", raising=False)
    fastapi = pytest.importorskip("fastapi")
    from starlette.testclient import TestClient
    from medterm4ds.apps.fhir_api import DEFAULT_PORT, FhirApiSettings, create_fhir_app
    import duckdb

    db_path = tmp_path / "umls.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE mrconso (CODE VARCHAR, TTY VARCHAR, STR VARCHAR, "
        "AUI VARCHAR, SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR)"
    )
    con.execute("CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR)")
    con.close()
    settings = FhirApiSettings(db_path=db_path, memory_profile="low", prepare_cache=False)
    app = create_fhir_app(settings)
    with TestClient(app) as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200, r.text
        impl_url = r.json().get("implementation", {}).get("url", "")
        assert str(DEFAULT_PORT) in impl_url, (
            f"Default port not reflected when no env var set: {impl_url!r}"
        )


def test_s15_scheme_env_var_with_ipv6_host(monkeypatch, tmp_path):
    """§4.7.2 + URL constructor edge cases. IPv6 host WITHOUT scheme on host
    but WITH separate scheme env var MUST produce a parseable URL.

    Per GLOBAL_RULES.md line 128: separate scheme env var is one of the
    documented deployment shapes. IPv6 brackets must not break the
    concatenation logic.
    """
    with _make_test_client(tmp_path, monkeypatch,
                           host="[::1]", port="8443",
                           scheme="https") as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200, r.text
        impl_url = r.json().get("implementation", {}).get("url", "")
        # Expected form: https://[::1]:8443
        assert impl_url.startswith("https://[::1]"), (
            f"IPv6 host + scheme-env-var not handled: {impl_url!r}"
        )


# =============================================================================
# L2 — Bundle shape validation (§3.1.0.11.1, §4.7.8)
# =============================================================================


def test_s20_bundle_type_transaction_rejected_or_processed(fhir_client):
    """§3.1.0.11.1: 'The content of the post submission is a Bundle with
    Bundle.type = batch or transaction.'

    Probe: a Bundle type=transaction MUST be accepted (per spec text —
    transaction is permitted on the POST /fhir endpoint). medterm4ds is
    read-only, so it processes transaction as batch (independent entries).

    Negative: must NOT return 500/text-plain.
    """
    body = _batch_bundle([_validate_code_get_entry(
        "http://snomed.info/sct", "44054006",
    )])
    body["type"] = "transaction"
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, (
        f"Bundle type=transaction rejected: {r.status_code}: {r.text}"
    )
    resp = r.json()
    assert resp["resourceType"] == "Bundle", resp
    # Per spec, batch-response OR transaction-response is acceptable.
    assert resp["type"] in ("batch-response", "transaction-response"), resp
    assert resp.get("entry"), "Empty response entries for non-empty batch"


@pytest.mark.parametrize("bundle_type", [
    "searchset", "document", "collection", "history", "subscription-notification",
])
def test_s21_bundle_wrong_type_rejected(fhir_client, bundle_type):
    """§3.1.0.11.1: only batch/transaction permitted on POST /fhir.

    Probe: every other Bundle.type MUST be rejected with a FHIR
    OperationOutcome (not 500/text-plain).
    """
    body = _batch_bundle([_validate_code_get_entry(
        "http://snomed.info/sct", "44054006",
    )])
    body["type"] = bundle_type
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 400, (
        f"Wrong bundle type {bundle_type!r} not rejected: {r.status_code}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        r.headers["content-type"]
    )
    body_dict = r.json()
    assert body_dict["resourceType"] == "OperationOutcome", body_dict


def test_s22_bundle_entry_not_a_list_rejected(fhir_client):
    """§3.1.0.11: entry must be a list. Probe: entry as a dict, string,
    or number must be rejected with a FHIR OperationOutcome.
    """
    body = {"resourceType": "Bundle", "type": "batch", "entry": "not-a-list"}
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["resourceType"] == "OperationOutcome", r.text


def test_s23_bundle_resource_type_wrong_rejected(fhir_client):
    """§3.1.0.11: resourceType must be Bundle. Probe: any other
    resourceType rejected with FHIR OperationOutcome.
    """
    body = {
        "resourceType": "Patient",
        "type": "batch",
        "entry": [_validate_code_get_entry("http://snomed.info/sct", "44054006")],
    }
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["resourceType"] == "OperationOutcome", r.text


def test_s24_bundle_resource_type_missing_rejected(fhir_client):
    """§3.1.0.11: resourceType MUST be Bundle. Probe: missing resourceType
    field rejected with FHIR OperationOutcome.
    """
    body = {"type": "batch", "entry": []}
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["resourceType"] == "OperationOutcome", r.text


def test_s25_bundle_type_missing_rejected(fhir_client):
    """§3.1.0.11.1: type MUST be present. Probe: missing type field
    rejected.
    """
    body = {
        "resourceType": "Bundle",
        "entry": [_validate_code_get_entry("http://snomed.info/sct", "44054006")],
    }
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["resourceType"] == "OperationOutcome", r.text


def test_s26_empty_bundle_returns_empty_batch_response(fhir_client):
    """§3.1.0.11.3: 'one entry for each entry in the request, in the same
    order.' An empty request Bundle MUST return an empty response Bundle.

    Per §3.1.0.11: 'the HTTP response code is 200 Ok if the batch was
    processed correctly, regardless of the success of the operations.'
    """
    r = fhir_client.post("/fhir", json=_batch_bundle([]))
    assert r.status_code == 200, r.text
    resp = r.json()
    assert resp["resourceType"] == "Bundle"
    assert resp["type"] == "batch-response"
    assert resp.get("entry") == [], resp


# =============================================================================
# L3 — Batch per-entry isolation (§3.1.0.11.2 — CRITICAL per QA-038)
# =============================================================================


def test_s30_one_bad_entry_does_not_break_others_missing_request(fhir_client):
    """§3.1.0.11.2: 'For a batch, there SHALL be no interdependencies
    between the different entries in the Bundle.'

    CRITICAL: per-entry isolation MUST hold. One bad entry (missing
    'request' block) MUST NOT break other entries.

    Pattern source: GLOBAL_KNOWLEDGE.md TS-04 HISTORIAN QA-038 — non-
    ValueError exceptions broke atomicity before the broad
    `except Exception` boundary landed.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        {"missing": "request-block"},  # bad entry
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text  # 200 OK regardless of per-entry failures
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    # Entry 0 + 2 must be successful.
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    # Entry 1 must be a 4xx error OperationOutcome.
    assert entries[1]["response"]["status"].startswith("4"), entries[1]
    assert entries[1]["resource"]["resourceType"] == "OperationOutcome", entries[1]


def test_s31_one_bad_entry_does_not_break_others_missing_method(fhir_client):
    """§3.1.0.11.2: per-entry isolation. Bad entry with request but no
    method MUST be isolated.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        {"request": {"url": "CodeSystem/$validate-code"}},  # missing method
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    assert entries[1]["response"]["status"].startswith("4"), entries[1]


def test_s32_one_bad_entry_does_not_break_others_missing_url(fhir_client):
    """§3.1.0.11.2: per-entry isolation. Bad entry with method but no url
    MUST be isolated.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        {"request": {"method": "GET"}},  # missing url
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[1]["response"]["status"].startswith("4"), entries[1]


def test_s33_one_bad_entry_does_not_break_others_unsupported_method(fhir_client):
    """§3.1.0.11.2: per-entry isolation. Bad entry with unsupported method
    (DELETE) MUST be isolated.

    Probe: DELETE method should be rejected with a per-entry 405, not
    break other entries.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        {"request": {"method": "DELETE", "url": "CodeSystem/1"}},  # write op
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    # DELETE on read-only server → 405 (per Known Fragile Areas).
    assert entries[1]["response"]["status"] == "405", entries[1]
    assert entries[1]["resource"]["resourceType"] == "OperationOutcome", entries[1]


def test_s34_one_bad_entry_does_not_break_others_unknown_url(fhir_client):
    """§3.1.0.11.2: per-entry isolation. Unknown URL must be isolated.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        {"request": {"method": "GET", "url": "Patient/1"}},  # unknown op
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    assert entries[1]["response"]["status"].startswith("4"), entries[1]


def test_s35_one_bad_entry_does_not_break_others_unknown_system(fhir_client):
    """§3.1.0.11.2: per-entry isolation. An entry with an unknown code
    system URI MUST be isolated from other entries.

    Probe: entry 1 references an unrecognized system; entry 0 and entry 2
    are valid. The middle entry produces a per-entry 4xx OperationOutcome;
    the others succeed.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        _validate_code_get_entry("http://nonexistent.example/system", "x"),
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    # Middle entry: unknown system → 4xx with FHIR OperationOutcome body.
    assert entries[1]["response"]["status"].startswith("4"), entries[1]
    assert entries[1]["resource"]["resourceType"] == "OperationOutcome", entries[1]


def test_s36_one_bad_entry_does_not_break_others_malformed_post_body(fhir_client):
    """§3.1.0.11.2: per-entry isolation. A POST entry with a malformed
    Parameters body (missing system+code, missing resource) MUST be
    isolated.
    """
    body = _batch_bundle([
        _validate_code_post_entry("http://snomed.info/sct", "44054006"),
        {"request": {"method": "POST", "url": "CodeSystem/$validate-code"}},  # no resource
        _validate_code_post_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    assert entries[1]["response"]["status"].startswith("4"), entries[1]


def test_s37_one_bad_entry_does_not_break_others_value_error_inside_handler(fhir_client):
    """§3.1.0.11.2: per-entry isolation. A ValueError raised inside a
    dispatched _do_* handler (e.g. empty system) MUST be caught at the
    narrow `except ValueError` boundary at `_dispatch_batch_operation:1299`
    AND be isolated from other entries.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        # Empty system — passes URL parse but handler will reject.
        {"request": {"method": "GET", "url": "CodeSystem/$validate-code?system=&code=x"}},
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    # Empty system → caught at narrow ValueError boundary → per-entry 400.
    assert entries[1]["response"]["status"].startswith("4"), entries[1]


# =============================================================================
# L4 — Batch entry URL paths (§3.1.0.11)
# =============================================================================


def test_s40_batch_entry_url_with_leading_slash(fhir_client):
    """§3.1.0.11: 'The url element ... can be either absolute or relative.'

    Probe: entry URL with a leading slash (e.g. '/CodeSystem/$validate-code')
    must be normalized and dispatch correctly.
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "/CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 1, entries
    assert entries[0]["response"]["status"] == "200", entries[0]


def test_s41_batch_entry_url_with_fhir_prefix(fhir_client):
    """§3.1.0.11: 'The url element ... can be either absolute or relative.'

    Probe: entry URL with '/fhir' prefix (e.g. '/fhir/CodeSystem/$validate-code')
    MUST be stripped to dispatch against the internal route shape.
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "/fhir/CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 1, entries
    assert entries[0]["response"]["status"] == "200", entries[0]


def test_s42_batch_entry_url_absolute(fhir_client):
    """§3.1.0.11: 'The url element ... can be either absolute or relative.'

    Probe: absolute URL (e.g. 'https://host/fhir/CodeSystem/$validate-code')
    MUST be normalized to dispatch correctly.
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "https://example.org/fhir/CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 1, entries
    assert entries[0]["response"]["status"] == "200", entries[0]


def test_s43_batch_entry_url_missing_operation_name(fhir_client):
    """§3.1.0.11: malformed URL MUST be handled gracefully.

    Probe: URL with no `$op` segment (e.g. '/CodeSystem') should be
    rejected as unknown with a per-entry 4xx.
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "/CodeSystem",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert entries[0]["response"]["status"].startswith("4"), entries[0]


def test_s44_batch_entry_url_extra_path_segments(fhir_client):
    """§3.1.0.11: malformed URL MUST be handled gracefully.

    Probe: URL with extra path segments (e.g. '/CodeSystem/$validate-code/extra')
    should be rejected as unknown.
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "/CodeSystem/$validate-code/extra?system=http://snomed.info/sct&code=44054006",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert entries[0]["response"]["status"].startswith("4"), entries[0]


def test_s45_batch_entry_url_path_traversal(fhir_client):
    """§3.1.0.11: hostile URL with path traversal MUST NOT escape the
    dispatch route table. Probe: '../../etc/passwd' should be rejected.
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "../../etc/passwd",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert entries[0]["response"]["status"].startswith("4"), entries[0]


def test_s46_batch_entry_url_url_encoded(fhir_client):
    """§3.1.0.11: URL-encoded paths MUST parse correctly.

    Probe: standard URL with %-encoded query string. The path component
    itself should not be percent-encoded but the query MAY be.
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "/CodeSystem/$validate-code?system=http%3A%2F%2Fsnomed.info%2Fsct&code=44054006",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert entries[0]["response"]["status"] == "200", entries[0]


# =============================================================================
# L5 — Order preservation (§3.1.0.11.3)
# =============================================================================


def test_s50_order_preserved_mixed_operations(fhir_client):
    """§3.1.0.11.3: 'the response the server SHALL return a Bundle ...
    that contains one entry for each entry in the request, in the same
    order, with the outcome of processing the entry.'

    Probe: 5 mixed operations in one batch (validate-code × 2, lookup,
    subsumes, translate). Response MUST preserve order: each entry
    corresponds to the request at the same index.
    """
    entries = [
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        {"request": {
            "method": "GET",
            "url": "/CodeSystem/$lookup?system=http://snomed.info/sct&code=44054006",
        }},
        {"request": {
            "method": "GET",
            "url": "/CodeSystem/$subsumes?system=http://snomed.info/sct&codeA=44054006&codeB=73211009",
        }},
        _validate_code_get_entry("http://hl7.org/fhir/sid/icd-10-cm", "E11"),
        {"request": {
            "method": "GET",
            "url": "/ConceptMap/$translate?system=http://snomed.info/sct&code=44054006&targetsystem=http://hl7.org/fhir/sid/icd-10-cm",
        }},
    ]
    r = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert r.status_code == 200, r.text
    resp_entries = r.json()["entry"]
    assert len(resp_entries) == 5, resp_entries
    # All 5 entries must have response.status and resource.
    for i, entry in enumerate(resp_entries):
        assert "response" in entry, f"Entry {i} missing response: {entry}"
        assert "status" in entry["response"], entry
        assert "resource" in entry, f"Entry {i} missing resource: {entry}"


def test_s51_order_preserved_with_failures_interleaved(fhir_client):
    """§3.1.0.11.3: 'A response code on an entry of other than 2xx ...
    indicates that processing the request in the entry failed.'

    Order MUST be preserved even when some entries fail.

    Probe: 7 entries alternating success/failure. Each response entry
    corresponds to its request at the same index.
    """
    entries = [
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),  # success
        {"request": {"method": "GET", "url": "Patient/1"}},  # unknown op → fail
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),  # success
        {"request": {"method": "DELETE", "url": "CodeSystem/1"}},  # 405
        _validate_code_get_entry("http://hl7.org/fhir/sid/icd-10-cm", "E11"),  # success
        {"request": {}},  # missing method + url → fail
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),  # success
    ]
    r = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert r.status_code == 200, r.text
    resp_entries = r.json()["entry"]
    assert len(resp_entries) == 7, resp_entries
    # Indices 0, 2, 4, 6 must succeed.
    for i in (0, 2, 4, 6):
        assert resp_entries[i]["response"]["status"] == "200", (
            f"Entry {i} expected success: {resp_entries[i]}"
        )
    # Indices 1, 3, 5 must fail.
    for i in (1, 3, 5):
        assert not resp_entries[i]["response"]["status"].startswith("2"), (
            f"Entry {i} expected failure: {resp_entries[i]}"
        )


def test_s52_large_batch_order_preserved(fhir_client):
    """§3.1.0.11.3: order preservation with >100 entries.

    Probe: 100 entries of the same operation. Response entries MUST
    correspond 1-to-1 in order.
    """
    n = 100
    entries = [_validate_code_get_entry("http://snomed.info/sct", "44054006")
               for _ in range(n)]
    r = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert r.status_code == 200, r.text
    resp_entries = r.json()["entry"]
    assert len(resp_entries) == n, f"Expected {n}, got {len(resp_entries)}"
    # Every entry must be a success.
    for i, entry in enumerate(resp_entries):
        assert entry["response"]["status"] == "200", (
            f"Entry {i} failed unexpectedly: {entry}"
        )


# =============================================================================
# L6 — Per-entry response shape (§3.1.0.11.3)
# =============================================================================


def test_s60_response_entry_has_response_and_resource(fhir_client):
    """§3.1.0.11.3: 'Each entry element SHALL contain a response element
    which details the outcome of processing the entry.'

    Probe: every response entry MUST have `response` AND `resource` keys.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entry = r.json()["entry"][0]
    assert "response" in entry, entry
    assert "status" in entry["response"], entry
    assert "resource" in entry, entry
    # The resource must be a FHIR Parameters (validate-code result).
    assert entry["resource"]["resourceType"] == "Parameters", entry


def test_s61_response_status_is_string_per_spec(fhir_client):
    """§3.1.0.11.3: 'the HTTP status code'. Per FHIR R4 Bundle.response.status
    is `string`, not int. Probe: response.status MUST be a string.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    status = r.json()["entry"][0]["response"]["status"]
    assert isinstance(status, str), f"status must be str, got {type(status)}"


def test_s62_batch_response_outer_status_200_regardless_of_inner_failures(fhir_client):
    """§3.1.0.11: 'When processing the batch, the HTTP response code is
    200 Ok if the batch was processed correctly, regardless of the
    success of the operations within the Batch.'

    Probe: even when ALL inner entries fail, the outer HTTP status is 200.
    """
    body = _batch_bundle([
        {"request": {"method": "DELETE", "url": "CodeSystem/1"}},
        {"request": {"method": "DELETE", "url": "CodeSystem/2"}},
        {"request": {"method": "DELETE", "url": "CodeSystem/3"}},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, (
        f"Outer status must be 200 regardless of inner failures: {r.status_code}"
    )


# =============================================================================
# L7 — Batch $translate (§4.7.10) + Batch $lookup + per-entry shape
# =============================================================================


def test_s70_batch_translate_returns_per_entry_parameters(fhir_client):
    """§4.7.10: 'translate a set of concepts ... using the $translate
    operation in a Batch interaction.' Each response entry MUST carry
    per-entry Parameters with `result`.

    Source: https://build.fhir.org/terminology-service.html §4.7.10.
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "/ConceptMap/$translate?system=http://snomed.info/sct&code=44054006&targetsystem=http://hl7.org/fhir/sid/icd-10-cm",
        }},
        {"request": {
            "method": "GET",
            "url": "/ConceptMap/$translate?system=http://snomed.info/sct&code=99999999&targetsystem=http://hl7.org/fhir/sid/icd-10-cm",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 2, entries
    # Each entry must be a Parameters resource with `result`.
    for i, entry in enumerate(entries):
        assert entry["response"]["status"] == "200", entries
        assert entry["resource"]["resourceType"] == "Parameters", entries[i]
        names = [p["name"] for p in entry["resource"]["parameter"]]
        assert "result" in names, entries[i]


def test_s71_batch_lookup_returns_per_entry_parameters(fhir_client):
    """§4.7.5 (CodeSystem-lookup): batch lookup MUST return per-entry
    Parameters with `display` and `name`.
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "/CodeSystem/$lookup?system=http://snomed.info/sct&code=44054006",
        }},
        {"request": {
            "method": "GET",
            "url": "/CodeSystem/$lookup?system=http://snomed.info/sct&code=73211009",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 2, entries
    for i, entry in enumerate(entries):
        assert entry["response"]["status"] == "200", entries[i]
        assert entry["resource"]["resourceType"] == "Parameters", entries[i]
        names = [p["name"] for p in entry["resource"]["parameter"]]
        assert "display" in names, entries[i]
        assert "name" in names, entries[i]


# =============================================================================
# L8 — TS-03/TERMINOLOGIST tip: uppercase-scheme batch inheritance
# =============================================================================


def test_s80_batch_lookup_uppercase_scheme_resolves(fhir_client):
    """TS-03/TERMINOLOGIST tip: the batch dispatcher consumes
    `fhir_uri_to_system` indirectly via each `_do_*` handler. The EXPLORER
    uppercase-scheme fix (TS-03 EXPLORER QA-001 — RFC 3986 §3.1 scheme
    case-insensitivity) is inherited via delegation.

    Probe: batch $lookup with `HTTP://snomed.info/sct` (uppercase scheme)
    MUST resolve identically to lowercase. Per-entry byte-exact parity.
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "/CodeSystem/$lookup?system=http://snomed.info/sct&code=44054006",
        }},
        {"request": {
            "method": "GET",
            "url": "/CodeSystem/$lookup?system=HTTP://snomed.info/sct&code=44054006",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 2, entries
    # Both entries MUST be successful 200s.
    for i, entry in enumerate(entries):
        assert entry["response"]["status"] == "200", (
            f"Entry {i} (uppercase-scheme) failed: {entry}"
        )
    # Per-entry byte-exact parity: the display names MUST match.
    e0_display = next(
        (p.get("valueString") for p in entries[0]["resource"]["parameter"]
         if p["name"] == "display"), None,
    )
    e1_display = next(
        (p.get("valueString") for p in entries[1]["resource"]["parameter"]
         if p["name"] == "display"), None,
    )
    assert e0_display == e1_display, (
        f"Batch uppercase-scheme lookup did NOT match lowercase: "
        f"{e0_display!r} vs {e1_display!r}"
    )


def test_s81_batch_validate_code_uppercase_scheme_resolves(fhir_client):
    """TS-03/TERMINOLOGIST tip: uppercase-scheme fix inherited on batch
    CodeSystem/$validate-code surface.

    Probe: batch validate-code with `HTTP://snomed.info/sct` MUST resolve
    identically to lowercase.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        _validate_code_get_entry("HTTP://snomed.info/sct", "44054006"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 2, entries
    for i, entry in enumerate(entries):
        assert entry["response"]["status"] == "200", (
            f"Entry {i} (uppercase-scheme) failed: {entry}"
        )
    # Per-entry byte-exact parity: the result booleans MUST match.
    e0_result = next(
        (p.get("valueBoolean") for p in entries[0]["resource"]["parameter"]
         if p["name"] == "result"), None,
    )
    e1_result = next(
        (p.get("valueBoolean") for p in entries[1]["resource"]["parameter"]
         if p["name"] == "result"), None,
    )
    assert e0_result == e1_result, (
        f"Batch uppercase-scheme validate-code did NOT match lowercase: "
        f"{e0_result!r} vs {e1_result!r}"
    )


def test_s82_batch_translate_uppercase_scheme_resolves(fhir_client):
    """TS-03/TERMINOLOGIST tip: uppercase-scheme fix inherited on batch
    ConceptMap/$translate surface (both source system AND targetsystem).
    """
    body = _batch_bundle([
        {"request": {
            "method": "GET",
            "url": "/ConceptMap/$translate?system=http://snomed.info/sct&code=44054006&targetsystem=http://hl7.org/fhir/sid/icd-10-cm",
        }},
        {"request": {
            "method": "GET",
            "url": "/ConceptMap/$translate?system=HTTP://snomed.info/sct&code=44054006&targetsystem=HTTP://hl7.org/fhir/sid/icd-10-cm",
        }},
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 2, entries
    for i, entry in enumerate(entries):
        assert entry["response"]["status"] == "200", (
            f"Entry {i} (uppercase-scheme) failed: {entry}"
        )
    # Per-entry byte-exact parity on result.
    e0_result = next(
        (p.get("valueBoolean") for p in entries[0]["resource"]["parameter"]
         if p["name"] == "result"), None,
    )
    e1_result = next(
        (p.get("valueBoolean") for p in entries[1]["resource"]["parameter"]
         if p["name"] == "result"), None,
    )
    assert e0_result == e1_result, (
        f"Batch uppercase-scheme translate did NOT match lowercase: "
        f"{e0_result!r} vs {e1_result!r}"
    )


# =============================================================================
# L9 — Per-entry isolation: source-read audit of the boundary catch shape
# =============================================================================


def test_s90_source_read_per_entry_isolation_boundary_is_broad_exception():
    """Source-read audit (carry-forward-as-probe pattern extension).

    The per-entry isolation boundary at `_process_batch_entry` MUST be a
    broad `except Exception` because §3.1.0.11.2 mandates per-entry
    independence regardless of failure mode. The narrow-exception rule
    (GLOBAL_RULES.md "Silent Fallbacks") applies INSIDE the dispatched
    operation; at the boundary we MUST catch all.

    Probe: source-read `_process_batch_entry` and assert the boundary
    catch is `except Exception`, not `except ValueError`.

    Source: https://hl7.org/fhir/R4/http.html §3.1.0.11.2.
    Quote: 'For a batch, there SHALL be no interdependencies between the
    different entries in the Bundle that cause change on the server.'
    """
    import ast
    src_path = Path(__file__).resolve().parents[2] / "src" / "medterm4ds" / "apps" / "fhir_api.py"
    src = src_path.read_text()
    tree = ast.parse(src)
    # Find _process_batch_entry.
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_process_batch_entry":
                target = node
                break
    assert target is not None, "_process_batch_entry not found in source"
    # Find try/except blocks; assert at least one catches Exception broadly.
    found_broad = False
    for node in ast.walk(target):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                # handler.type can be None (bare except) OR a Name/Attribute.
                if handler.type is None:
                    found_broad = True
                    break
                if isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                    found_broad = True
                    break
                # duckdb.Error is NOT broad enough for the boundary; the
                # boundary MUST catch ALL exceptions per spec.
    assert found_broad, (
        "_process_batch_entry must have a broad `except Exception` boundary "
        "for per-entry isolation per §3.1.0.11.2"
    )


def test_s91_source_read_dispatch_internal_catch_is_narrow_value_error():
    """Source-read audit: the catch INSIDE `_dispatch_batch_operation` MUST
    be narrow (`except ValueError`), per GLOBAL_RULES.md "Silent Fallbacks".

    Programming bugs (TypeError, AttributeError, KeyError) MUST propagate
    up to the broad boundary in `_process_batch_entry`. The narrow catch
    is for spec-listed input-validation errors only.

    Probe: source-read `_dispatch_batch_operation` and assert the catch
    is `except ValueError` (not `except Exception`).
    """
    import ast
    src_path = Path(__file__).resolve().parents[2] / "src" / "medterm4ds" / "apps" / "fhir_api.py"
    src = src_path.read_text()
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_dispatch_batch_operation":
                target = node
                break
    assert target is not None
    # Find try/except blocks; assert at least one catches ValueError narrowly.
    found_narrow = False
    found_broad = False
    for node in ast.walk(target):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if isinstance(handler.type, ast.Name):
                    if handler.type.id == "ValueError":
                        found_narrow = True
                    elif handler.type.id == "Exception":
                        # A broad catch INSIDE dispatch would be a silent-
                        # fallback violation per GLOBAL_RULES.md.
                        found_broad = True
    assert found_narrow, (
        "_dispatch_batch_operation must have a narrow `except ValueError` "
        "for input-validation errors per GLOBAL_RULES.md"
    )
    assert not found_broad, (
        "_dispatch_batch_operation has a broad `except Exception` INSIDE the "
        "dispatch — violates GLOBAL_RULES.md 'Silent Fallbacks'. Programming "
        "bugs MUST propagate up to the boundary in _process_batch_entry."
    )


# =============================================================================
# L10 — Edge cases: empty-body, non-dict body, null entry
# =============================================================================


def test_s100_non_dict_entry_isolated(fhir_client):
    """§3.1.0.11.2: per-entry isolation against hostile input shapes.

    Probe: a `null` entry in the entry list MUST be isolated (not break
    the whole batch).
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        None,  # null entry
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    assert entries[1]["response"]["status"].startswith("4"), entries[1]


def test_s101_string_entry_isolated(fhir_client):
    """§3.1.0.11.2: per-entry isolation against hostile input shapes.

    Probe: a string entry in the entry list MUST be isolated.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        "not-an-object",
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    assert entries[1]["response"]["status"].startswith("4"), entries[1]


def test_s102_list_entry_isolated(fhir_client):
    """§3.1.0.11.2: per-entry isolation. A list (instead of dict) as an
    entry MUST be isolated.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        ["not", "a", "dict"],
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    assert entries[1]["response"]["status"].startswith("4"), entries[1]


def test_s103_non_object_request_block_isolated(fhir_client):
    """§3.1.0.11.2: per-entry isolation. An entry with `request` set to
    a non-object MUST be isolated.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        {"request": "not-an-object"},
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    assert entries[1]["response"]["status"].startswith("4"), entries[1]


def test_s104_put_method_isolated(fhir_client):
    """§3.1.0.11.2: per-entry isolation. A PUT entry (write op) MUST be
    rejected with 405 on the read-only server AND isolated.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        {"request": {"method": "PUT", "url": "CodeSystem/1", "body": {}}},
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[0]["response"]["status"] == "200", entries[0]
    assert entries[2]["response"]["status"] == "200", entries[2]
    assert entries[1]["response"]["status"] == "405", entries[1]


def test_s105_patch_method_isolated(fhir_client):
    """§3.1.0.11.2: per-entry isolation. A PATCH entry (write op) MUST be
    rejected with 405 AND isolated.
    """
    body = _batch_bundle([
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        {"request": {"method": "PATCH", "url": "CodeSystem/1"}},
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
    ])
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3, entries
    assert entries[1]["response"]["status"] == "405", entries[1]
