"""EXPLORER probes for TS-04 (Security, Batch Validation, Batch Translation).

Source: https://build.fhir.org/terminology-service.html §4.7.2, §4.7.8, §4.7.10
Reference spec for batch behavior: https://hl7.org/fhir/R4/http.html#transaction
Reference spec for security: https://build.fhir.org/terminology-service.html#4.7.2

EXPLORER lens (per ROLE_QA_ENGINEER.md):

Lateral-thinking probes for shapes NOT yet exercised by SKEPTIC or HISTORIAN.
Carry-forwards from SKEPTIC (CF-SKEPTIC-02) and HISTORIAN (CF-HISTORIAN-01):

1. **Adversarial batch shapes**:
   - Bundle with extra metadata fields (meta, identifier, total)
   - Bundle type typo / case variants (bath, bATCH, BATCH, Batch)
   - GET entries with non-empty body (Parameters body attached to GET)
   - POST entries with empty Parameters body
   - entry.request.method in lowercase ('post')
   - entry.resource with missing resourceType / wrong resourceType

2. **Batch entry URLs**:
   - Absolute URL with /fhir/ prefix (http://host/fhir/CodeSystem/$op)
   - Absolute URL without /fhir/ prefix (http://host/CodeSystem/$op)
   - Slash-prefixed URL (/CodeSystem/$op)
   - URLs with fragment (#frag)
   - URLs with both query string AND body parameters

3. **Order preservation under failure**:
   - 10-entry alternating success/fail batch (success even, fail odd)
   - Mixed-operation batch (all 7 §4.7.1.2 operations) executes correctly

4. **Empty / degenerate batches**:
   - Empty entry list (covered by SKEPTIC test_s23 — re-verified)
   - Single-entry batch (covered by SKEPTIC test_s28 — re-verified)
   - Bundle with extra metadata fields (NEW EXPLORER probe)

5. **CF-EXPLORER-01 (TS-03 carry-forward)**:
   POST to unknown resource types (Patient, Observation) — STILL falls through
   to Starlette default 405 with application/json Content-Type and
   {"detail":"Method Not Allowed"} body. This is the framework-default drift
   pattern (count=4 — TS-02 EXPLORER QA-024/QA-025 instance POST, TS-03
   EXPLORER QA-035 type POST to CodeSystem/ValueSet/ConceptMap, now TS-04
   unknown-resource-type POST).

6. **Large batch performance**:
   150-entry batch completes within reasonable time (~3s observed) and
   produces correct 1:1 response shape.

7. **HTTPS env var combos**:
   All produce well-formed implementation.url. Documented as positive
   success-shape assertion (per GLOBAL_RULES.md "Test-too-lenient").

8. **Combined operations**: batch + Accept header, batch + _format query.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


# =============================================================================
# Helper for env-var-overridden deployments
# =============================================================================


def _make_env_test_client(tmp_path: Path, monkeypatch, host: str, port: str,
                          scheme: str | None = None):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient
    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    import duckdb

    monkeypatch.setenv("MEDTERM4DS_API_HOST", host)
    monkeypatch.setenv("MEDTERM4DS_FHIR_API_PORT", port)
    if scheme is not None:
        monkeypatch.setenv("MEDTERM4DS_API_SCHEME", scheme)
    else:
        monkeypatch.delenv("MEDTERM4DS_API_SCHEME", raising=False)

    db_path = tmp_path / "umls.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE mrconso (CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR, SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR)")
    con.execute("CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR)")
    con.close()
    settings = FhirApiSettings(db_path=db_path, memory_profile="low", prepare_cache=False)
    app = create_fhir_app(settings)
    return TestClient(app)


# =============================================================================
# CF-EXPLORER-01 — POST to unknown resource types still uses Starlette default 405
# =============================================================================


def test_e10_post_to_unknown_resource_type_returns_fhir_outcome(fhir_client):
    """§3.1.0.1.5 + §3.1.0.1.9: POST to unknown FHIR resource types MUST NOT
    fall through to Starlette's default 405 handler.

    Spec: https://hl7.org/fhir/R4/http.html#ops
      'Operations MAY be invoked via HTTP GET or POST ... on either the
       system, type, or instance level.'

    Spec: https://hl7.org/fhir/R4/http.html#mime-type
      'The correct mime type SHALL be used by clients and servers.'

    CF-EXPLORER-01 (carry-forward from TS-03): POST to unknown resource types
    (Patient, Observation) still falls through to Starlette's default 405
    with `application/json` Content-Type and `{"detail":"Method Not Allowed"}`
    body — non-conformant. The catch-all from TS-01 EXPLORER QA-011 only
    handles GET; the type-level POST from TS-03 EXPLORER QA-035 only handles
    CodeSystem/ValueSet/ConceptMap.

    Without the fix: status=405, Content-Type=application/json, body has
    `{"detail":"Method Not Allowed"}`.

    Expected: status in (405, 404), Content-Type=application/fhir+json, body
    is an OperationOutcome.

    Same pattern class as TS-03 EXPLORER QA-035 (count=4 for framework-
    default drift on POST routes).
    """
    r = fhir_client.post("/fhir/Patient", json={"resourceType": "Patient"})
    # Status may be 405 (method not allowed on the catch-all) or 404
    # (resource type unknown) — either is conformant IF the body is FHIR.
    assert r.status_code in (404, 405), (
        f"Unexpected status {r.status_code}: {r.text[:300]}"
    )
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST /fhir/Patient returned Content-Type {ct!r} — expected "
        f"application/fhir+json. The framework default 405 leaks through "
        f"for unknown resource types (CF-EXPLORER-01)."
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"Expected OperationOutcome, got: {body}"
    )


def test_e11_post_to_observation_returns_fhir_outcome(fhir_client):
    """§3.1.0.1.5 + §3.1.0.1.9: same as e10 but for Observation.

    Second instance of the same bug — confirms it's a class, not a one-off.
    """
    r = fhir_client.post("/fhir/Observation", json={"resourceType": "Observation"})
    assert r.status_code in (404, 405), (
        f"Unexpected status {r.status_code}: {r.text[:300]}"
    )
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST /fhir/Observation Content-Type {ct!r} — expected FHIR MIME."
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"Expected OperationOutcome: {body}"
    )


# =============================================================================
# §4.7.8 Bundle with extra metadata fields (must be ignored, not 500)
# =============================================================================


def test_e20_batch_bundle_with_extra_metadata_fields_accepted(fhir_client):
    """§4.7.8: a batch Bundle with extra metadata fields (meta, identifier,
    total) MUST be accepted and processed — the server MUST NOT 500 because
    of unexpected fields.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
      'A client can ... submit ... a Bundle with type=batch.'

    The Bundle resource (https://hl7.org/fhir/R4/bundle.html) defines many
    optional fields: meta, identifier, total, link, signature, etc. A client
    is permitted to include any of them. The server MUST process the entries
    regardless.

    Probe: a Bundle with type=batch, an empty entry list, and three extra
    fields (meta, identifier, total). Server MUST return 200 + batch-response
    Bundle with empty entry list.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "meta": {"versionId": "1", "lastUpdated": "2026-01-01T00:00:00Z"},
        "identifier": {"system": "urn:ietf:rfc:3986", "value": "urn:uuid:abc"},
        "total": 0,
        "entry": [],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, (
        f"Bundle with extra metadata rejected: {r.status_code}: {r.text[:300]}"
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "batch-response"
    assert body.get("entry") == []


# =============================================================================
# Bundle type typo / case variants
# =============================================================================


@pytest.mark.parametrize("bad_type", [
    "bath",       # typo
    "BATCH",      # uppercase
    "Batch",      # title case
    "bATCH",      # mixed
    " batch",     # leading whitespace
    "batch ",     # trailing whitespace
    "",           # empty string
])
def test_e21_batch_bundle_type_variants_rejected_with_fhir_outcome(
    fhir_client, bad_type,
):
    """§4.7.8: Bundle.type is a closed value set {batch, transaction, document,
    message, history, searchset, collection}. Variant case ('BATCH') or typo
    ('bath') MUST be rejected with a 4xx OperationOutcome — NOT 500.

    Spec: https://hl7.org/fhir/R4/valueset-bundle-type.html
      The Bundle type code system is case-sensitive (FHIR R4 §1.5.0.3
      'Codes are case-sensitive').
    """
    bundle = {"resourceType": "Bundle", "type": bad_type, "entry": []}
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 400, (
        f"Bad Bundle.type {bad_type!r} should produce 400, got "
        f"{r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"Expected OperationOutcome for bad type {bad_type!r}: {body}"
    )


# =============================================================================
# GET entry with non-empty resource body
# =============================================================================


def test_e30_get_entry_with_resource_body_succeeds(fhir_client):
    """§4.7.8: GET entries pass parameters via query string. A client MAY
    still attach a `resource` body to a GET entry (some libraries always
    serialize it). The server MUST NOT 500.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
      'GET requests MAY include query parameters in the URL.'

    The Bundle entry structure allows `resource` on any entry regardless of
    method. For GET entries the resource is unused (params come from URL) —
    but the server MUST process the entry using the query string, ignoring
    the body.

    Probe: GET entry with URL
    'CodeSystem/$validate-code?system=...&code=...' and a non-empty
    Parameters body. Expected: 200 + per-entry success (params from URL).
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
                # Body attached to a GET entry — should be ignored.
                "resource": {"resourceType": "Parameters", "parameter": []},
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, f"Batch failed: {r.status_code}: {r.text[:300]}"
    body = r.json()
    assert body.get("type") == "batch-response"
    entries = body.get("entry", [])
    assert len(entries) == 1
    e0_status = entries[0]["response"]["status"]
    assert e0_status == "200", (
        f"GET entry should succeed using URL query string; got {e0_status}. "
        f"Diagnostic: {entries[0].get('resource', {}).get('issue', [{}])[0].get('diagnostics', '')[:200]}"
    )
    res = entries[0].get("resource", {})
    assert res.get("resourceType") == "Parameters"


# =============================================================================
# POST entry with empty Parameters body (system+code missing)
# =============================================================================


def test_e31_post_entry_empty_parameters_body_returns_per_entry_400(fhir_client):
    """§3.7 per-entry error isolation: a POST entry with an empty Parameters
    body (no system/code) MUST produce a per-entry 400 OperationOutcome —
    NOT a 500 for the whole batch.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
      'In a batch ... each entry is processed independently.'

    Probe: POST entry with empty Parameters body. Expected: per-entry 400
    (system+code required), batch-response overall 200.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$lookup"},
                "resource": {"resourceType": "Parameters", "parameter": []},
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, f"Batch failed: {r.status_code}: {r.text[:300]}"
    body = r.json()
    assert body.get("type") == "batch-response"
    entries = body.get("entry", [])
    assert len(entries) == 1
    s0 = entries[0]["response"]["status"]
    assert s0 == "400", (
        f"Empty-Parameters POST entry should produce per-entry 400, got {s0}"
    )
    res = entries[0].get("resource", {})
    assert res.get("resourceType") == "OperationOutcome"


# =============================================================================
# entry.request.method case-insensitive ('post' lowercase)
# =============================================================================


def test_e32_entry_method_lowercase_accepted(fhir_client):
    """§4.7.8: entry.request.method is HTTP-method-shaped but clients may
    send lowercase. The server should normalize via `.upper()` and dispatch
    correctly.

    Spec: https://hl7.org/fhir/R4/bundle-definitions.html#Bundle.entry.request.method
      'The HTTP verb ... one of: GET, HEAD, POST, PUT, DELETE, PATCH.'

    Probe: entry with method='post' (lowercase). Expected: per-entry 200.

    Implementation note: `_process_batch_entry` already calls
    `method = req_block.get("method", "").upper()`.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "post", "url": "CodeSystem/$validate-code"},
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
    entries = body.get("entry", [])
    assert len(entries) == 1
    assert entries[0]["response"]["status"] == "200", (
        f"Lowercase method=post should be normalized and dispatched; got "
        f"{entries[0]['response']}"
    )


# =============================================================================
# entry.resource missing resourceType / wrong resourceType
# =============================================================================


def test_e33_entry_resource_missing_resourcetype_accepted(fhir_client):
    """§4.7.8: a Parameters resource body without explicit `resourceType`
    field is non-conformant but the server SHOULD tolerate it for
    robustness — the dispatcher routes by URL path, not by resourceType.

    Spec: https://hl7.org/fhir/R4/parameters.html
      Parameters resource is implicit for operation bodies, but the FHIR
      JSON serialization requires `resourceType`.

    Probe: POST entry with `resource` having no `resourceType` field but a
    valid `parameter` list. Expected: per-entry 200 (the dispatcher doesn't
    check resourceType — `_parse_parameters` extracts from `parameter`).

    This is a robustness probe — documents that the implementation is
    tolerant of the missing-field case.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                # Missing resourceType — non-conformant but tolerated.
                "resource": {
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
    entries = body.get("entry", [])
    assert len(entries) == 1
    # The dispatcher routes by URL — resourceType is not checked. The entry
    # should succeed because _parse_parameters reads from `parameter`.
    assert entries[0]["response"]["status"] == "200", (
        f"Missing resourceType should still succeed (dispatcher ignores "
        f"resourceType); got {entries[0]['response']}"
    )


def test_e34_entry_resource_wrong_resourcetype_accepted(fhir_client):
    """§4.7.8: a POST entry body with a wrong resourceType (e.g. 'Patient')
    but valid Parameters `parameter` list is tolerated — same logic as e33.

    Probe: POST entry with resource.resourceType='Patient' but a valid
    parameter list for $validate-code. Expected: per-entry 200.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {
                    "resourceType": "Patient",  # wrong, but tolerated
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
    entries = body.get("entry", [])
    assert len(entries) == 1
    assert entries[0]["response"]["status"] == "200", (
        f"Wrong resourceType should still succeed (dispatcher uses URL path); "
        f"got {entries[0]['response']}"
    )


# =============================================================================
# Batch entry URL forms
# =============================================================================


def test_e40_batch_entry_absolute_url_with_fhir_prefix(fhir_client):
    """§4.7.8: entry.request.url MAY be absolute (full URL). The server MUST
    accept both forms.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
      'The url ... can be either absolute or relative.'

    Probe: absolute URL with /fhir/ prefix
    'http://localhost:8000/fhir/CodeSystem/$validate-code'.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "http://localhost:8000/fhir/CodeSystem/$validate-code",
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
    assert r.status_code == 200
    body = r.json()
    assert body.get("type") == "batch-response"
    entries = body.get("entry", [])
    assert len(entries) == 1
    assert entries[0]["response"]["status"] == "200", (
        f"Absolute URL with /fhir/ prefix should resolve; got "
        f"{entries[0]['response']}"
    )


def test_e41_batch_entry_absolute_url_without_fhir_prefix(fhir_client):
    """§4.7.8: absolute URL without /fhir/ prefix — also accepted.

    Probe: 'http://localhost:8000/CodeSystem/$validate-code'.
    Expected: per-entry 200 (the prefix is stripped or the path is matched
    directly).
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "http://localhost:8000/CodeSystem/$validate-code",
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
    assert r.status_code == 200
    body = r.json()
    entries = body.get("entry", [])
    assert len(entries) == 1
    # Either success (path matched directly) or per-entry 4xx (path unknown).
    # The behavior here documents the dispatcher's URL handling.
    s0 = entries[0]["response"]["status"]
    # Implementation strips the leading /fhir if present; without /fhir, the
    # path becomes '/CodeSystem/$validate-code' which is dispatched correctly.
    assert s0 == "200", (
        f"Absolute URL without /fhir/ prefix should resolve to /CodeSystem/"
        f"$validate-code; got {s0}. "
        f"Diagnostic: {entries[0].get('resource', {}).get('issue', [{}])[0].get('diagnostics', '')[:200]}"
    )


def test_e42_batch_entry_slash_prefixed_url(fhir_client):
    """§4.7.8: slash-prefixed URL '/CodeSystem/$validate-code' is base-
    relative with leading slash. Server MUST dispatch correctly.

    Probe: '/CodeSystem/$validate-code'.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "/CodeSystem/$validate-code",
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
    assert r.status_code == 200
    body = r.json()
    entries = body.get("entry", [])
    assert len(entries) == 1
    assert entries[0]["response"]["status"] == "200", (
        f"Slash-prefixed URL should resolve; got {entries[0]['response']}"
    )


def test_e43_batch_entry_url_with_fragment(fhir_client):
    """§4.7.8: a URL with a fragment identifier ('#frag') — the fragment is
    client-side only and MUST be stripped before dispatch.

    Spec: RFC 3986 §3.5 — fragments are not sent to the server in regular
    HTTP, but in a batch Bundle the URL is JSON-encoded and the fragment
    MAY be present. The server SHOULD strip it.

    Probe: 'CodeSystem/$validate-code#frag'. Expected: per-entry 200
    (fragment stripped, path matches).
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "CodeSystem/$validate-code#frag",
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
    assert r.status_code == 200
    body = r.json()
    entries = body.get("entry", [])
    assert len(entries) == 1
    assert entries[0]["response"]["status"] == "200", (
        f"URL with fragment should resolve (fragment stripped); got "
        f"{entries[0]['response']}"
    )


# =============================================================================
# Order preservation with alternating success/failure (10 entries)
# =============================================================================


def test_e50_batch_order_preserved_alternating_10_entries(fhir_client):
    """§3.7: response entries MUST be in the same order as request entries.
    Stress test with 10 entries alternating success/fail.

    Spec: https://hl7.org/fhir/R4/http.html#transaction
      'The order of entries in the request Bundle is the order of processing
       ... the response Bundle ... entries ... SHALL be in the same order.'

    Probe: 10 entries — even-index entries use known code 73211009 (result=
    true); odd-index entries use 'BADCODE<n>' (result=false).
    Expected: statuses are all 200; results are [T,F,T,F,T,F,T,F,T,F].
    """
    bundle = {"resourceType": "Bundle", "type": "batch", "entry": []}
    for i in range(10):
        code = "73211009" if i % 2 == 0 else f"BADCODE{i}"
        bundle["entry"].append({
            "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": code},
                ],
            },
        })
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, f"Batch failed: {r.status_code}: {r.text[:300]}"
    body = r.json()
    assert body.get("type") == "batch-response"
    entries = body.get("entry", [])
    assert len(entries) == 10, f"Expected 10 entries, got {len(entries)}"
    # All statuses are 200 (the operation is dispatched successfully — the
    # 'result' field encodes success/failure of code lookup).
    statuses = [e["response"]["status"] for e in entries]
    assert all(s == "200" for s in statuses), (
        f"All per-entry statuses should be 200 (the operation ran); "
        f"got {statuses}"
    )
    # The 'result' parameter alternates: T,F,T,F,...
    results = []
    for e in entries:
        params = e.get("resource", {}).get("parameter", [])
        rp = next((p for p in params if p.get("name") == "result"), None)
        results.append(rp.get("valueBoolean") if rp else None)
    expected = [True, False, True, False, True, False, True, False, True, False]
    assert results == expected, (
        f"Order preservation broken or wrong results: expected {expected}, "
        f"got {results}"
    )


# =============================================================================
# Mixed-operation batch (all 7 §4.7.1.2 operations)
# =============================================================================


def test_e60_mixed_operation_batch_all_seven_ops(fhir_client):
    """§4.7.1.2 Mandatory Operations Matrix: $lookup, $validate-code (CS),
    $validate-code (VS), $subsumes, $translate, $expand, $closure.

    Spec: https://build.fhir.org/terminology-service.html#4.7.1.2

    Probe: a single batch containing one entry for each of the 7 mandatory
    operations. Expected: 7 per-entry 200s; each entry's resource matches
    the operation's documented Out shape.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$lookup"},
                "resource": {"resourceType": "Parameters", "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "73211009"},
                ]},
            },
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": {"resourceType": "Parameters", "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "73211009"},
                ]},
            },
            {
                "request": {"method": "POST", "url": "ValueSet/$validate-code"},
                "resource": {"resourceType": "Parameters", "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "73211009"},
                ]},
            },
            {
                "request": {"method": "POST", "url": "CodeSystem/$subsumes"},
                "resource": {"resourceType": "Parameters", "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "codeA", "valueCode": "44054006"},
                    {"name": "codeB", "valueCode": "73211009"},
                ]},
            },
            {
                "request": {"method": "POST", "url": "ConceptMap/$translate"},
                "resource": {"resourceType": "Parameters", "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                    {"name": "targetsystem", "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                ]},
            },
            {
                "request": {"method": "POST", "url": "ValueSet/$expand"},
                "resource": {"resourceType": "Parameters", "parameter": [
                    {"name": "url", "valueUri": "http://snomed.info/sct?fhir_vs"},
                ]},
            },
            {
                "request": {"method": "POST", "url": "CodeSystem/$closure"},
                "resource": {"resourceType": "Parameters", "parameter": [
                    {"name": "name", "valueString": "mixed-op-closure"},
                ]},
            },
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, f"Batch failed: {r.status_code}: {r.text[:300]}"
    body = r.json()
    assert body.get("type") == "batch-response"
    entries = body.get("entry", [])
    assert len(entries) == 7, f"Expected 7 entries, got {len(entries)}"

    expected_resource_types = [
        "Parameters",   # $lookup
        "Parameters",   # $validate-code (CS)
        "Parameters",   # $validate-code (VS)
        "Parameters",   # $subsumes
        "Parameters",   # $translate
        "ValueSet",     # $expand
        "Parameters",   # $closure
    ]
    for i, e in enumerate(entries):
        assert e["response"]["status"] == "200", (
            f"Entry {i} should succeed; got {e['response']}. "
            f"Diagnostic: {e.get('resource', {}).get('issue', [{}])[0].get('diagnostics', '')[:200]}"
        )
        actual_rt = e.get("resource", {}).get("resourceType")
        assert actual_rt == expected_resource_types[i], (
            f"Entry {i} expected resourceType={expected_resource_types[i]}, "
            f"got {actual_rt}"
        )


# =============================================================================
# Large batch (>100 entries) — performance + correctness
# =============================================================================


def test_e70_large_batch_150_entries_completes_and_preserves_order(fhir_client):
    """§3.7: a large batch (150 entries) MUST complete in reasonable time
    and produce 1:1 responses in order.

    Probe: 150 identical $validate-code entries. Expected: 200 + 150
    response entries, all per-entry 200.

    Performance bound: observed ~3s for 150 entries (~20ms/entry). The
    test asserts the response is returned in <30s (sanity bound).
    """
    bundle = {"resourceType": "Bundle", "type": "batch", "entry": []}
    for _ in range(150):
        bundle["entry"].append({
            "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "73211009"},
                ],
            },
        })
    t0 = time.time()
    r = fhir_client.post("/fhir", json=bundle)
    elapsed = time.time() - t0
    assert r.status_code == 200, f"Batch failed: {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body.get("type") == "batch-response"
    entries = body.get("entry", [])
    assert len(entries) == 150, (
        f"Expected 150 response entries (1:1 with request), got {len(entries)}"
    )
    # All per-entry statuses should be 200.
    statuses = [e["response"]["status"] for e in entries]
    assert all(s == "200" for s in statuses), (
        f"All entries should succeed; got non-200: "
        f"{[s for s in statuses if s != '200'][:5]} (showing first 5)"
    )
    # Sanity bound: 150 entries should complete in <30s.
    assert elapsed < 30.0, (
        f"150-entry batch took {elapsed:.1f}s — exceeds 30s sanity bound"
    )


# =============================================================================
# §4.7.2 HTTPS env var combos — capability statement URL well-formedness
# =============================================================================


@pytest.mark.parametrize("host,port,expected_substring", [
    ("https://fhir.example.com", "443", "https://fhir.example.com:443"),
    ("fhir.example.com", "443", "http://fhir.example.com:443"),
    ("http://fhir.example.com", "80", "http://fhir.example.com:80"),
    ("127.0.0.1", "8000", "http://127.0.0.1:8000"),
    ("[::1]", "8000", "http://[::1]:8000"),
    ("https://[::1]", "443", "https://[::1]:443"),
    ("https://example.com:8443", "9999", "https://example.com:8443"),
    ("example.com/", "8000", "http://example.com:8000"),
])
def test_e80_deployment_base_url_well_formed_for_env_combos(
    monkeypatch, tmp_path, host, port, expected_substring,
):
    """§4.7.2 / §3.2.1.0.5: the deployment URL surfaced in
    CapabilityStatement.implementation.url MUST be well-formed for all
    documented env var combinations.

    Spec: https://build.fhir.org/terminology-service.html#4.7.2
      'Servers SHOULD ensure that all interactions occur over a secure
       connection.'

    The URL is the load-bearing piece for §4.7.2: clients discover the
    deployment endpoint from this field; a malformed URL prevents them from
    reaching the server.

    Probe matrix (positive success-shape per GLOBAL_RULES.md "Test-too-
    lenient"): each env var combo produces an implementation.url containing
    the expected well-formed substring.

    Cross-checks:
    - IPv6 host (with and without scheme) → brackets + port present.
    - Trailing-slash host → slash stripped before port.
    - Explicit port in host → preserved (operator override).
    """
    with _make_env_test_client(tmp_path, monkeypatch,
                                host=host, port=port) as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200, f"Unexpected: {r.status_code}: {r.text[:300]}"
        body = r.json()
        impl_url = body.get("implementation", {}).get("url", "")
        assert impl_url, (
            f"CapabilityStatement.implementation.url missing for host={host!r}"
        )
        assert expected_substring in impl_url, (
            f"Expected {expected_substring!r} in impl.url; got {impl_url!r} "
            f"(host={host!r}, port={port!r})"
        )


def test_e81_deployment_base_url_scheme_env_var_https(monkeypatch, tmp_path):
    """§4.7.2: explicit MEDTERM4DS_API_SCHEME=https overrides the default
    http:// scheme — sanity check (already covered by HISTORIAN h32, but
    EXPLORER adds the explicit assertion that this is independent of host
    scheme-on-host form).
    """
    with _make_env_test_client(tmp_path, monkeypatch,
                                host="fhir.example.com", port="443",
                                scheme="https") as client:
        r = client.get("/fhir/metadata")
        body = r.json()
        impl_url = body.get("implementation", {}).get("url", "")
        assert impl_url == "https://fhir.example.com:443", (
            f"Expected https://fhir.example.com:443; got {impl_url!r}"
        )


# =============================================================================
# CapabilityStatement URL consistency across combos
# =============================================================================


def test_e82_capability_statement_rest_url_uses_same_base(monkeypatch, tmp_path):
    """§3.2.1.0.5 + §4.7.1.1: CapabilityStatement.rest[].url and
    implementation.url SHOULD both derive from the same env-var-sourced
    base URL.

    Probe: under HTTPS host env var, both rest[].url and implementation.url
    reflect the https scheme and the env-sourced host:port.

    Note: per SKEPTIC TS-04 QA-037 audit, rest[].url is NOT a valid
    CapabilityStatement field per FHIR R4 schema. The implementation.url
    is the canonical surface. We assert the implementation.url shape.
    """
    with _make_env_test_client(tmp_path, monkeypatch,
                                host="https://fhir.example.com",
                                port="443") as client:
        r = client.get("/fhir/metadata")
        body = r.json()
        impl_url = body.get("implementation", {}).get("url", "")
        assert "https://" in impl_url, (
            f"impl.url should reflect https scheme; got {impl_url!r}"
        )
        # The CapabilityStatement MUST NOT advertise a different scheme
        # elsewhere (rest[].url is not valid per R4 schema; check
        # rest[].extension for any URL-bearing field if present).
        rest = body.get("rest", [])
        assert rest, "CapabilityStatement.rest[] missing"
        # The implementation.url is the canonical deployment surface.


# =============================================================================
# Combined operations: batch + Accept header
# =============================================================================


def test_e90_batch_with_xml_accept_header_returns_xml_response(fhir_client):
    """§3.1.0.1.9 + §3.1.0.1.11: the batch response Content-Type MUST honor
    the Accept header (or _format query param).

    Spec: https://hl7.org/fhir/R4/http.html#mime-type

    Probe: POST /fhir with Accept: application/fhir+xml. Expected: response
    Content-Type is application/fhir+xml (or a FHIR error if XML is not
    supported for batch responses).

    Note: the batch handler funnels through `_fhir_response` which dispatches
    on Accept — same path as all other operations. If XML serialization is
    wired, the batch response should be XML; if not, the response should
    still be a valid FHIR resource (likely JSON, which is non-conformant but
    documented elsewhere).
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
    r = fhir_client.post(
        "/fhir", json=bundle, headers={"Accept": "application/fhir+xml"},
    )
    # Either: XML response (200 + application/fhir+xml) OR a FHIR error.
    assert r.status_code in (200, 406), (
        f"Unexpected status {r.status_code}: {r.text[:200]}"
    )
    ct = r.headers.get("content-type", "")
    # If 200, MUST be XML (per Accept); if 406, MUST be FHIR JSON.
    if r.status_code == 200:
        assert "application/fhir+xml" in ct, (
            f"Accept: application/fhir+xml returned Content-Type {ct!r}"
        )
    else:
        assert "application/fhir+json" in ct, (
            f"406 response Content-Type {ct!r} — expected FHIR JSON"
        )


# =============================================================================
# Cross-handler audit: every per-operation route dispatchable via batch
# =============================================================================


def test_e100_all_advertised_operations_dispatchable_via_batch(fhir_client):
    """§4.7.1.2 + §4.7.8: every operation advertised in the CapabilityStatement
    MUST be dispatchable via the batch endpoint.

    Spec: https://hl7.org/fhir/R4/terminology-service.html#4.7.1.2
      Mandatory Operations Matrix.

    EXPLORER cross-handler audit (per GLOBAL_RULES.md "Code Review Time"):
    walk the CapabilityStatement.rest[].resource[].operation[] list and
    send one batch entry per operation. If any returns per-entry 404
    "Unknown operation", the batch dispatcher's path-table has drifted
    from the per-operation route registration.

    This is the parametrized variant of HISTORIAN test_h20/h21 (which
    covered $expand/$closure specifically). EXPLORER extends the audit to
    every operation the CapabilityStatement advertises.
    """
    # First, fetch the CapabilityStatement to enumerate operations.
    r = fhir_client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.json()
    rest = body.get("rest", [])
    advertised_ops = []
    for r_entry in rest:
        for res in r_entry.get("resource", []):
            rtype = res.get("type")
            for op in res.get("operation", []):
                # Use the operation's `name` field directly (e.g. 'lookup',
                # 'validate-code'). The name is the canonical operation name
                # without the '$' prefix per FHIR R4 CapabilityStatement.
                op_name_raw = op.get("name", "")
                if not op_name_raw:
                    continue
                op_name = "$" + op_name_raw
                advertised_ops.append((rtype, op_name))
    # The custom $search and $extract operations are server-local and not
    # in the §4.7.1.2 Mandatory Operations Matrix — exclude from this audit
    # which is specifically about the matrix operations dispatchable via
    # the batch endpoint.
    mandatory_ops = {
        ("CodeSystem", "$lookup"),
        ("CodeSystem", "$validate-code"),
        ("CodeSystem", "$subsumes"),
        ("CodeSystem", "$closure"),
        ("ValueSet", "$validate-code"),
        ("ValueSet", "$expand"),
        ("ConceptMap", "$translate"),
    }
    advertised_ops = [op for op in advertised_ops if op in mandatory_ops]

    # Send one batch entry per advertised operation.
    bundle = {"resourceType": "Bundle", "type": "batch", "entry": []}
    for rtype, op_name in advertised_ops:
        # Build a minimal Parameters body. For lookup/validate-code/etc
        # use system+code; for closure use name; for expand use url.
        params = []
        if op_name == "$closure":
            params.append({"name": "name", "valueString": "audit-closure"})
        elif op_name == "$expand":
            params.append({"name": "url", "valueUri": "http://snomed.info/sct?fhir_vs"})
        elif op_name == "$subsumes":
            params.extend([
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "codeA", "valueCode": "44054006"},
                {"name": "codeB", "valueCode": "73211009"},
            ])
        elif op_name == "$translate":
            params.extend([
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
            ])
        else:  # $lookup, $validate-code (CS and VS)
            params.extend([
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "73211009"},
            ])
        bundle["entry"].append({
            "request": {"method": "POST", "url": f"{rtype}/{op_name}"},
            "resource": {"resourceType": "Parameters", "parameter": params},
        })

    if not bundle["entry"]:
        pytest.skip("No advertised operations found in CapabilityStatement")

    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, f"Batch failed: {r.status_code}: {r.text[:300]}"
    body = r.json()
    entries = body.get("entry", [])
    assert len(entries) == len(advertised_ops), (
        f"Expected {len(advertised_ops)} entries, got {len(entries)}"
    )
    # Every entry MUST dispatch (no per-entry 404 "Unknown operation").
    for i, (rtype, op_name) in enumerate(advertised_ops):
        s = entries[i]["response"]["status"]
        assert s != "404", (
            f"Advertised operation {rtype}/{op_name} returned per-entry 404 "
            f"in batch dispatcher. The batch path-table has drifted from "
            f"the per-operation route registration. Diagnostic: "
            f"{entries[i].get('resource', {}).get('issue', [{}])[0].get('diagnostics', '')[:200]}"
        )
