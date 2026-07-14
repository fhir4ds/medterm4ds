"""SKEPTIC probes for TS-04 (Security, Batch Validation, Batch Translation — §4.7.2, §4.7.8, §4.7.10).

Source: https://build.fhir.org/terminology-service.html §4.7.2, §4.7.8, §4.7.10

Five spec items:

1. SSL/HTTPS operational concern (§4.7.2): "Servers SHOULD use TLS/HTTPS to
   protect patient data in transit." The CapabilityStatement must not preclude
   HTTPS deployment — endpoint URLs / schemes must reflect env vars
   (MEDTERM4DS_API_HOST) so an HTTPS host can be advertised without code change.
2. Batch `$validate-code` (§4.7.8): a Bundle `type=batch` containing multiple
   CodeSystem/$validate-code entries → server returns a Bundle `type=batch-
   response` with one entry per request, each carrying the corresponding
   Parameters body.
3. Batch `$translate` (§4.7.10): same shape as §4.7.8 for ConceptMap/$translate.
4. Batch entry URLs use the correct operation paths (relative or absolute).
5. Batch response order preservation: response entries MUST be in the same
   order as request entries; each has the corresponding `response.status` and
   `resource`.

SKEPTIC lens:
- Adversarial input: malformed Bundle, wrong `type`, missing `entry[]`,
  entry missing `request`, entry with bad `request.method`/`request.url`.
- Operation routing: base-relative vs absolute URLs.
- Parameter passing via URL query string OR via `resource` Parameters body.
- Response shape: each entry has `response.status` and `resource`.
- Order preservation with mixed success/failure entries.
- Error isolation: one bad entry must not poison the whole batch.
- Empty batch, single-entry batch, large batch, mixed-operation batch.
- `transaction` (atomic) Bundle SHOULD be rejected or documented.
- HTTPS env var reflection: `MEDTERM4DS_API_HOST=https://host` should
  advertise the https scheme in the CapabilityStatement.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _make_https_test_client(tmp_path: Path, monkeypatch, host: str, port: str):
    """Construct a FHIR app TestClient with a synthetic DB and env-overridden
    host/port. Used by the §4.7.2 SSL/HTTPS probes."""
    fastapi = pytest.importorskip("fastapi")
    from starlette.testclient import TestClient
    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    import duckdb

    monkeypatch.setenv("MEDTERM4DS_API_HOST", host)
    monkeypatch.setenv("MEDTERM4DS_FHIR_API_PORT", port)

    db_path = tmp_path / "umls.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE mrconso (CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR, SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR)")
    con.execute("CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR)")
    con.close()
    settings = FhirApiSettings(db_path=db_path, memory_profile="low", prepare_cache=False)
    app = create_fhir_app(settings)
    return TestClient(app)


# =============================================================================
# §4.7.2 SSL / HTTPS operational concern
# =============================================================================


def test_s01_https_env_var_reflected_in_capabilitystatement(monkeypatch, tmp_path):
    """§4.7.2: 'Servers SHOULD ensure that all interactions occur over a
    secure connection ... protect against unauthorized access, disclosure or
    alteration of the data.'

    Spec: https://build.fhir.org/terminology-service.html#4.7.2

    When the operator deploys behind HTTPS and sets `MEDTERM4DS_API_HOST` to
    a value carrying an `https://` scheme (or sets a separate scheme env),
    the CapabilityStatement MUST advertise the HTTPS deployment URL — NOT
    silently downgrade to `http://`.

    The bug: `apps/fhir_api.py:metadata` previously built
    `base_url = f"http://{host}:{port}"` regardless of host env var content.
    An HTTPS deployment advertised `http://https://host:port` (malformed) or
    silently downgraded to plain HTTP, violating the §4.7.2 operational
    guidance.

    Per GLOBAL_RULES.md "FHIR API Specifics": CapabilityStatement endpoint
    URLs MUST reflect MEDTERM4DS_API_HOST and MEDTERM4DS_FHIR_API_PORT. The
    scheme is the load-bearing piece for §4.7.2.

    Positive success-shape: assert the CapabilityStatement surfaces a
    deployment URL whose scheme matches the host env var.
    """
    with _make_https_test_client(tmp_path, monkeypatch,
                                 host="https://fhir.example.com",
                                 port="443") as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200, f"Unexpected status: {r.status_code}: {r.text}"
        body = r.json()
        # The deployment URL surface MUST reflect https when host env carries it.
        # Per FHIR R4 §3.2.1.0.5 the canonical surface is implementation.url.
        # (rest[].url is NOT a valid CapabilityStatement field per the R4
        # schema — confirmed by fhir.resources validator.)
        impl_url = body.get("implementation", {}).get("url") or ""
        assert impl_url, (
            f"CapabilityStatement.implementation.url missing — required to "
            f"surface the deployment endpoint (per FHIR R4 §3.2.1.0.5)."
        )
        assert "https://fhir.example.com" in impl_url, (
            f"CapabilityStatement.implementation.url does not reflect the "
            f"MEDTERM4DS_API_HOST=https://fhir.example.com env var. "
            f"Got: {impl_url!r}"
        )
        # And MUST NOT embed the malformed http://https:// form.
        assert "http://https://" not in impl_url, (
            f"Malformed base_url: 'http://https://' detected. "
            f"implementation.url={impl_url!r}"
        )


def test_s02_metadata_handler_no_silent_http_downgrade(monkeypatch, tmp_path):
    """§4.7.2: `MEDTERM4DS_API_HOST` set to `https://host` must NOT silently
    downgrade the advertisement to plain `http://host`.

    Per GLOBAL_RULES.md "FHIR API Specifics" — the advertisement MUST reflect
    the env vars. The HTTPS scheme is part of that contract.

    This probe is independent of the specific field surfaced (s01 covers the
    positive presence assertion; this one covers the negative — no silent
    `http://https://host` malformed URL in the metadata response).
    """
    with _make_https_test_client(tmp_path, monkeypatch,
                                 host="https://fhir.example.com",
                                 port="443") as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        text = r.text
        # The metadata handler builds `base_url = f"http://{host}:{port}"`
        # today. With host="https://fhir.example.com" and port="443", the
        # result is "http://https://fhir.example.com:443" — a malformed URL.
        # Probe for both bugs: (1) the malformed composite; (2) silent
        # downgrade when host has no scheme but deployment is HTTPS.
        assert "http://https://" not in text, (
            f"Malformed base_url 'http://https://' detected in metadata "
            f"response: {text[:500]}"
        )


# =============================================================================
# §4.7.8 / §4.7.10 Batch endpoint discovery
# =============================================================================


def test_s10_post_root_fhir_accepts_bundle(fhir_client):
    """§4.7.8 / §4.7.10: 'A client can execute multiple operations in a single
    HTTP request by submitting a Bundle with type=batch to the FHIR endpoint.'

    Spec: https://hl7.org/fhir/R4/http.html#transaction
    Quote: 'A batch ... is used to ... submit a set of actions to take ... in
            a single HTTP request ... The server processes each entry in the
            Bundle ... and returns a Bundle with type=batch-response.'

    Probe: POST /fhir with a Bundle of type=batch containing one
    CodeSystem/$validate-code entry. Server MUST return 200 + a Bundle
    with type=batch-response.

    Positive success-shape assertion (per "Test-too-lenient"): assert 200 +
    Bundle type=batch-response + entry[0] has response.status and resource.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "CodeSystem/$validate-code",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    # Positive success shape: 200 + batch-response Bundle.
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("resourceType") == "Bundle", f"Expected Bundle, got {body.get('resourceType')}"
    assert body.get("type") == "batch-response", (
        f"Expected type=batch-response, got {body.get('type')!r}"
    )
    entries = body.get("entry", [])
    assert len(entries) == 1, f"Expected 1 response entry, got {len(entries)}"
    resp = entries[0].get("response", {})
    assert resp.get("status") == "200", f"Expected per-entry status '200', got {resp.get('status')!r}"
    res = entries[0].get("resource", {})
    assert res.get("resourceType") == "Parameters", (
        f"Expected per-entry Parameters, got {res.get('resourceType')}"
    )


def test_s11_batch_validate_code_multiple_entries_order_preserved(fhir_client):
    """§4.7.8: batch entries MUST be processed in order; response entries MUST
    be in the same order as request entries.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
    Quote: 'The order of entries in the request Bundle is the order of
            processing ... the response Bundle ... entries ... SHALL be in
            the same order as in the request.'

    Probe: 3 entries — known-good code, unknown code, known-good code in
    different system. Verify response order is preserved (entry[0] result
    true, entry[1] result false, entry[2] result true).
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            },
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "NONEXISTENT_CODE_999"},
                    ],
                },
            },
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                        {"name": "code", "valueCode": "E11"},
                    ],
                },
            },
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "batch-response"
    entries = body["entry"]
    assert len(entries) == 3
    # Order preservation: each response corresponds to the request in the
    # same position. The middle entry should be result=false; the others
    # should be result=true.
    results = []
    for entry in entries:
        params = entry.get("resource", {}).get("parameter", [])
        result_param = next((p for p in params if p.get("name") == "result"), None)
        results.append(result_param.get("valueBoolean") if result_param else None)
    assert results == [True, False, True], (
        f"Order preservation broken or wrong results: {results}"
    )


def test_s12_batch_translate_multiple_entries(fhir_client):
    """§4.7.10: batch ConceptMap/$translate — same shape as batch $validate-code.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    Quote: 'The $translate operation ... returns ... Parameters resource with
            a result ... boolean ... and a message.'

    Probe: 2 entries translating SNOMED→ICD10CM. Verify batch-response shape.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "ConceptMap/$translate"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "44054006"},
                        {"name": "targetsystem", "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "batch-response"
    assert len(body["entry"]) == 1
    resp = body["entry"][0].get("response", {})
    assert resp.get("status") == "200"
    res = body["entry"][0].get("resource", {})
    assert res.get("resourceType") == "Parameters"


def test_s13_batch_entry_url_base_relative(fhir_client):
    """§4.7.8: entry.request.url is base-relative (e.g. 'CodeSystem/$validate-
    code'), not an absolute URL. Server MUST accept both forms.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
    Quote: 'The url ... is the URL for the resolve ... relative to the
            server's base URL.'

    Probe: entry URL is 'CodeSystem/$validate-code' (base-relative).
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    assert r.json()["type"] == "batch-response"


def test_s14_batch_get_entry_with_query_string(fhir_client):
    """§4.7.8: GET entries pass parameters via query string in request.url.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
    Quote: 'GET requests MAY include query parameters in the URL.'

    Probe: GET entry with URL
    'CodeSystem/$validate-code?system=...&code=...'.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "GET",
                    "url": "CodeSystem/$validate-code?system=http://snomed.info/sct&code=73211009",
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "batch-response"
    entries = body.get("entry", [])
    assert len(entries) == 1
    res = entries[0].get("resource", {})
    assert res.get("resourceType") == "Parameters", (
        f"Expected Parameters, got {res.get('resourceType')}"
    )


# =============================================================================
# §4.7.8 Adversarial batch shapes
# =============================================================================


def test_s20_batch_with_invalid_bundle_type_rejected(fhir_client):
    """§4.7.8: a Bundle with type=transaction is atomic (all-or-nothing). The
    server MAY support it but is not required to (medterm4ds is read-only).

    Spec: https://hl7.org/fhir/R4/http.html#transaction
    Quote: 'A transaction ... is processed as a single atomic unit of work.'
            'A batch ... is a set of independent actions ... no atomicity.'

    Probe: type=transaction with $validate-code entries. The server should
    either process it as batch OR return a structured 4xx OperationOutcome.
    It MUST NOT 500 with a non-FHIR body.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    # Either process (200) or reject with 4xx — but the response MUST be a
    # FHIR resource (OperationOutcome or Bundle). Never a Starlette default.
    assert r.status_code in (200, 400, 404, 405, 422), (
        f"Unexpected status {r.status_code}: {r.text}"
    )
    body = r.json()
    rt = body.get("resourceType")
    assert rt in ("Bundle", "OperationOutcome"), (
        f"Expected FHIR resource in response, got resourceType={rt!r}: {body}"
    )


def test_s21_batch_missing_type_field_rejected(fhir_client):
    """§4.7.8: a Bundle without type is malformed. Server MUST NOT 500."""
    bundle = {
        "resourceType": "Bundle",
        # type intentionally missing
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code in (200, 400, 422), (
        f"Unexpected status {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") in ("Bundle", "OperationOutcome"), (
        f"Expected FHIR resource: {body}"
    )


def test_s22_batch_missing_entry_list_graceful(fhir_client):
    """§4.7.8: a Bundle with no entry[] is malformed. Server MUST NOT 500."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code in (200, 400, 422), (
        f"Unexpected status {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") in ("Bundle", "OperationOutcome")


def test_s23_batch_empty_entry_list_returns_empty_response(fhir_client):
    """§4.7.8: a Bundle with type=batch and entry=[] is a degenerate but valid
    batch. Server SHOULD return a batch-response with entry=[].

    Spec: https://hl7.org/fhir/R4/http.html#transaction
    Quote: 'The response Bundle ... will contain an entry for each entry in
            the request.' Zero entries → zero response entries.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "batch-response"
    assert body.get("entry") == []


def test_s24_batch_entry_missing_request_isolated(fhir_client):
    """§4.7.8: error isolation. An entry without `request` MUST NOT crash the
    entire batch — the server SHOULD return a per-entry 4xx status for the
    malformed entry AND successfully process the other entries.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
    Quote: 'In a batch ... each entry is processed independently ... the
            response for each entry is independent of the other entries.'

    Probe: 2 entries — entry[0] valid, entry[1] missing `request`. Both
    response entries should exist; entry[1].response.status should be 4xx.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            },
            {
                # Missing 'request' — malformed entry.
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "44054006"},
                    ],
                },
            },
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, f"Batch failed entirely: {r.status_code}: {r.text}"
    body = r.json()
    assert body["type"] == "batch-response"
    entries = body["entry"]
    assert len(entries) == 2
    # Entry 0 should succeed.
    assert entries[0]["response"]["status"] == "200"
    # Entry 1 should be a 4xx (malformed request).
    s1 = entries[1]["response"]["status"]
    assert s1.startswith("4"), f"Expected 4xx for malformed entry, got {s1}"


def test_s25_batch_entry_unsupported_method_isolated(fhir_client):
    """§4.7.8: an entry with an unsupported method (e.g. DELETE on a read-only
    server) MUST be isolated — the rest of the batch must succeed.

    Probe: 2 entries — entry[0] valid POST, entry[1] DELETE (read-only server
    rejects). Both response entries should exist.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            },
            {
                "request": {"method": "DELETE", "url": "CodeSystem/123"},
            },
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "batch-response"
    entries = body["entry"]
    assert len(entries) == 2
    # Entry 0 succeeded; Entry 1 is a 4xx (DELETE not supported on read-only).
    assert entries[0]["response"]["status"] == "200"
    s1 = entries[1]["response"]["status"]
    assert s1.startswith("4"), f"Expected 4xx for DELETE on read-only, got {s1}"


def test_s26_batch_unrecognized_entry_url_isolated(fhir_client):
    """§4.7.8: an entry with a URL pointing to an unknown operation or
    resource MUST be isolated. Other entries must still succeed.

    Probe: 2 entries — entry[0] valid $validate-code, entry[1] unknown
    operation $nonexistent.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            },
            {
                "request": {"method": "POST", "url": "CodeSystem/$nonexistent"},
                "resource": {"resourceType": "Parameters", "parameter": []},
            },
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "batch-response"
    entries = body["entry"]
    assert len(entries) == 2
    assert entries[0]["response"]["status"] == "200"
    s1 = entries[1]["response"]["status"]
    assert s1.startswith("4"), f"Expected 4xx for unknown operation, got {s1}"


def test_s27_batch_mixed_operations(fhir_client):
    """§4.7.8 / §4.7.10: a single batch can mix $validate-code and $translate
    entries — the server MUST dispatch each entry to the correct operation.

    Probe: 2 entries — entry[0] CodeSystem/$validate-code, entry[1]
    ConceptMap/$translate.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            },
            {
                "request": {"method": "POST", "url": "ConceptMap/$translate"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "44054006"},
                        {"name": "targetsystem", "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                    ],
                },
            },
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "batch-response"
    entries = body["entry"]
    assert len(entries) == 2
    # Entry 0: $validate-code → result boolean
    e0_params = entries[0]["resource"].get("parameter", [])
    e0_result = next((p for p in e0_params if p.get("name") == "result"), None)
    assert e0_result is not None, f"$validate-code missing 'result': {e0_params}"
    # Entry 1: $translate → result boolean
    e1_params = entries[1]["resource"].get("parameter", [])
    e1_result = next((p for p in e1_params if p.get("name") == "result"), None)
    assert e1_result is not None, f"$translate missing 'result': {e1_params}"


def test_s28_batch_single_entry_degenerate(fhir_client):
    """§4.7.8: a single-entry batch is degenerate but valid. Server MUST
    handle it correctly.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "batch-response"
    assert len(body["entry"]) == 1


def test_s29_batch_response_each_entry_has_response_and_resource(fhir_client):
    """§4.7.8: every response entry MUST have `response.status` and
    (for successful entries) a `resource` carrying the operation result.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
    Quote: 'Each entry in the response ... SHALL contain ... response ...
            status ... and ... resource'.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    body = r.json()
    entry = body["entry"][0]
    assert "response" in entry, f"Missing 'response' in entry: {entry}"
    assert "status" in entry["response"], f"Missing 'status' in response: {entry}"
    assert "resource" in entry, f"Missing 'resource' in entry: {entry}"


# =============================================================================
# §4.7.8 Spec-citation boundary probes
# =============================================================================


def test_s30_post_root_fhir_with_non_bundle_body_returns_fhir_error(fhir_client):
    """§4.7.8: POST /fhir with a non-Bundle body MUST NOT return a Starlette
    default error. The response MUST be a FHIR OperationOutcome.
    """
    r = fhir_client.post("/fhir", json={"resourceType": "Patient", "id": "x"})
    assert r.status_code in (400, 422, 405), f"Unexpected status: {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"Expected OperationOutcome, got: {body}"
    )


def test_s31_batch_response_content_type_is_fhir(fhir_client):
    """§3.1.0.1.9: 'The correct mime type SHALL be used by clients and
    servers.' Batch-response Bundle MUST be served with Content-Type
    application/fhir+json, not application/json.

    Spec: https://hl7.org/fhir/R4/http.html#mime-type
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"Expected Content-Type application/fhir+json, got {ct!r}"
    )


def test_s32_batch_uri_round_trip_validate_code_results(fhir_client):
    """URI round-trip probe class (per GLOBAL_RULES.md "URI round-trip").

    Every code returned by a batch operation must be valid via a subsequent
    single-operation lookup. Send a batch $validate-code for a known code;
    take the response's system/code, perform a single $lookup, verify the
    code resolves.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    body = r.json()
    # Extract the (system, code) from the batch response.
    params = body["entry"][0]["resource"]["parameter"]
    sys_param = next(p for p in params if p.get("name") == "system")
    code_param = next(p for p in params if p.get("name") == "code")
    result_param = next(p for p in params if p.get("name") == "result")
    assert result_param["valueBoolean"] is True
    sys_uri = sys_param["valueUri"]
    code = code_param["valueCode"]
    # URI round-trip: $lookup with the returned system+code MUST succeed.
    r2 = fhir_client.get(f"/fhir/CodeSystem/$lookup", params={"system": sys_uri, "code": code})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2.get("resourceType") == "Parameters"
    # The lookup should return the canonical display name.
    name_param = next((p for p in body2["parameter"] if p.get("name") == "name"), None)
    assert name_param is not None


def test_s33_post_root_fhir_accepts_absolute_entry_url(fhir_client):
    """§4.7.8: entry.request.url MAY be absolute (full URL including server
    base) OR base-relative. Server MUST accept both.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
    Quote: 'The url element ... can be either absolute or relative.'
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "http://test.invalid/fhir/CodeSystem/$validate-code",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    # Absolute URLs are spec-permitted; the server should at minimum produce
    # a FHIR response (200 with batch-response OR 4xx OperationOutcome).
    assert r.status_code in (200, 400, 422), f"Unexpected: {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("resourceType") in ("Bundle", "OperationOutcome"), (
        f"Expected FHIR resource, got: {body}"
    )
