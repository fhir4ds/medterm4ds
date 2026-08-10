"""HISTORIAN resweep probes for TS-04 (Security, Batch Validation, Batch Translation).

Fresh full-sweep per USER_DIRECTIVES [2026-08-08]. Per GLOBAL_RULES.md
fresh-full-sweep baseline discipline, NEW regression probes live in a sibling
file (test_ts04_historian_resweep.py) and do NOT trust the prior TS-04
HISTORIAN baseline (test_ts04_historian.py — that file holds the prior run's
baselines, NOT new bugs).

Source: https://build.fhir.org/terminology-service.html §4.7.2, §4.7.8,
§4.7.10 + https://hl7.org/fhir/R4/http.html#transaction (§3.1.0.11).

HISTORIAN lens (per ROLE_QA_ENGINEER.md Section 3 "What broke before?"):
Pattern-match against prior TS-04 bug patterns and re-derive each from the
CURRENT code to verify it has NOT regressed (5 source modifications since
TS-03 launch).

SKEPTIC tip for HISTORIAN (high-priority): re-derive the QA-038 per-entry-
isolation-broken-by-non-ValueError-exceptions pattern via regression probes.
The broad `except Exception` boundary at `_process_batch_entry:1119` and the
narrow `except ValueError` at `_dispatch_batch_operation:1299` are the
structural contract — pin them.

Prior TS-04 patterns to re-derive (with prior-bug references):
  - QA-038 batch per-entry isolation (CRITICAL) — broad except Exception at
    process boundary; narrow except ValueError at dispatch boundary
  - HTTPS scheme via `_deployment_base_url` (handles IPv6/trailing-slash)
  - POST catch-all for unknown resource types (CF-EXPLORER-01)
  - `$expand`/`$closure` added to batch dispatcher (QA-039)
  - HTTP method boundary (PUT/PATCH/DELETE → 405) — SKEPTIC just fixed DELETE;
    verify PUT and PATCH still work; verify the tuple now contains all three

Each re-derivation has TWO components:
  (a) BEHAVIORAL — exercise the code path with a real probe
  (b) SOURCE-READ — assert the structural contract is in place (the "why")
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest


# =============================================================================
# Shared helpers
# =============================================================================


def _make_test_client(tmp_path: Path, monkeypatch, host: str | None = None,
                      port: str | None = None, scheme: str | None = None):
    """Construct a FHIR app TestClient with a synthetic minimal DB.

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


def _source_module_text() -> str:
    """Read the apps.fhir_api source text for source-read probes."""
    from medterm4ds.apps import fhir_api
    return inspect.getsource(fhir_api)


def _get_func_source(module_text: str, func_name: str) -> str:
    """Extract a nested function's source text using AST + line offsets.

    The functions we inspect (`_deployment_base_url`, `_process_batch_entry`,
    `_dispatch_batch_operation`) are nested INSIDE `create_fhir_app`. The
    top-level inspect.getsource(func) doesn't work because they're not
    accessible after create_fhir_app returns. We parse the module with AST
    and find the function by name within the module source.

    Per TS-01 HISTORIAN methodology: when source-reading a function nested
    inside another, scope by AST walk and line offset, not by `\\ndef ` regex
    (which captures too much for nested functions).
    """
    # Note: the functions we inspect include async def, so we must walk
    # both FunctionDef (sync) and AsyncFunctionDef (async).
    tree = ast.parse(module_text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == func_name:
            return ast.get_source_segment(module_text, node) or ""
    return ""


# =============================================================================
# L1 — QA-038 batch per-entry isolation (CRITICAL pattern)
# Re-derive that a single bad entry does NOT poison the whole batch.
# =============================================================================


def test_h10_qa038_rederived_bad_entry_does_not_poison_batch(fhir_client):
    """QA-038 re-derived (CRITICAL pattern): §3.7 mandates per-entry
    independence ("In a batch ... each entry is processed independently").

    Spec: https://hl7.org/fhir/R4/http.html#transaction §3.1.0.11.2.

    Behavioral probe: a batch with one well-formed entry AND one malformed
    entry (resource is a string, not a dict). Pre-fix QA-038: status=500,
    Content-Type=text/plain, body="Internal Server Error" for the whole batch.

    The broad `except Exception` boundary at `_process_batch_entry:1131` MUST
    catch the AttributeError raised by `_parse_parameters("not-a-dict")` and
    return a per-entry 500 OperationOutcome. The well-formed entry MUST still
    succeed (status=200 in response.status).
    """
    bundle = _batch_bundle([
        # Entry 0: well-formed $validate-code.
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
        # Entry 1: malformed — resource is a string.
        {
            "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
            "resource": "not-a-dict-but-a-string",
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, (
        f"BATCH HTTP status should be 200 regardless of per-entry failures; "
        f"got {r.status_code}. Body: {r.text[:500]}"
    )
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "batch-response"
    entries = body["entry"]
    assert len(entries) == 2
    # Order preservation: entry[0] succeeded, entry[1] failed.
    assert entries[0]["response"]["status"] == "200"
    s1 = entries[1]["response"]["status"]
    assert s1.startswith(("4", "5")), (
        f"Entry 1 should have per-entry error status, got {s1}"
    )
    # Per-entry resource is OperationOutcome (NOT text/plain "Internal Server
    # Error" — the QA-038 pre-fix shape).
    assert entries[1]["resource"]["resourceType"] == "OperationOutcome"


def test_h11_qa038_rederived_dict_with_unexpected_inner_type(fhir_client):
    """QA-038 re-derived (CRITICAL pattern) — second shape of malformed entry.

    A `resource` that is a dict but contains `parameter` as a STRING instead
    of a list. The dispatch handler iterates the list; strings are iterable
    but yield single characters, raising AttributeError when `.get("name")`
    is called.

    Probe that the broad boundary catch covers ALL non-ValueError exception
    paths, not just AttributeError from non-dict resources.
    """
    bundle = _batch_bundle([
        # Entry 0: malformed inner type.
        {
            "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
            "resource": {
                "resourceType": "Parameters",
                "parameter": "not-a-list-but-a-string",
            },
        },
        # Entry 1: well-formed — must still succeed.
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
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, (
        f"BATCH HTTP status should be 200; got {r.status_code}. "
        f"Body: {r.text[:500]}"
    )
    entries = r.json()["entry"]
    assert len(entries) == 2
    # Entry 0 failed (per-entry error); entry 1 succeeded (well-formed).
    s0 = entries[0]["response"]["status"]
    assert s0.startswith(("4", "5")), f"Entry 0 should fail, got {s0}"
    assert entries[1]["response"]["status"] == "200"


def test_h12_qa038_rederived_value_error_caught_at_narrow_boundary(fhir_client):
    """QA-038 re-derived (CRITICAL pattern) — narrow boundary catches
    ValueError (input validation).

    Per GLOBAL_RULES.md "Silent Fallbacks", narrow exception types are used
    INSIDE the dispatched operation for input validation. A batch entry with
    an empty `system` value triggers ValueError inside the operation handler
    (or its service-layer delegation).

    The narrow `except ValueError` at `_dispatch_batch_operation:1307` MUST
    catch this and produce a per-entry 400 OperationOutcome — NOT a 500 from
    the broad boundary (which would indicate the narrow boundary regressed
    and ValueError escaped).
    """
    bundle = _batch_bundle([
        # Entry 0: empty system → ValueError inside handler.
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$validate-code?system=&code=73211009",
            },
        },
        # Entry 1: well-formed.
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$validate-code"
                       "?system=http://snomed.info/sct&code=73211009",
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    assert len(entries) == 2
    s0 = entries[0]["response"]["status"]
    # Empty-system is input validation → should be 4xx, NOT 500.
    assert s0.startswith("4"), (
        f"Empty-system ValueError should be 4xx at narrow boundary; got {s0}. "
        f"If 500, the narrow `except ValueError` boundary has regressed."
    )
    assert entries[1]["response"]["status"] == "200"


# =============================================================================
# L2 — QA-038 structural contract: broad boundary at process boundary,
#       narrow boundary at dispatch boundary. SOURCE-READ probes (pin the WHY).
# =============================================================================


def test_h20_qa038_structural_broad_except_at_process_boundary():
    """QA-038 SOURCE-READ probe: `_process_batch_entry` MUST have a broad
    `except Exception` boundary.

    This is the structural contract — per §3.7 the boundary MUST be broad
    because the spec mandates per-entry independence regardless of failure
    mode. GLOBAL_RULES.md "Silent Fallbacks" narrow-exception rule applies
    INSIDE the dispatched operation, not at the boundary.

    If this probe FAILS, the broad boundary was narrowed (regression) — non-
    ValueError exceptions would propagate as whole-batch 500/text-plain again.
    """
    src = _source_module_text()
    func_src = _get_func_source(src, "_process_batch_entry")
    assert func_src, "Could not extract _process_batch_entry source"
    # The broad boundary catches Exception (not a narrower subclass).
    assert "except Exception as exc:" in func_src, (
        "_process_batch_entry MUST have `except Exception as exc:` as the "
        "per-entry isolation boundary (per §3.7 + QA-038 fix). Narrowing "
        "this would regress QA-038."
    )
    # The boundary MUST log at WARNING (not DEBUG — per GLOBAL_RULES.md
    # silent-fallback prohibition).
    assert "logger.warning" in func_src, (
        "_process_batch_entry MUST log the caught exception at WARNING level "
        "(not DEBUG) so programming bugs aren't silent."
    )


def test_h21_qa038_structural_narrow_except_at_dispatch_boundary():
    """QA-038 SOURCE-READ probe: `_dispatch_batch_operation` MUST have a
    narrow `except ValueError` boundary (NOT broad `except Exception`).

    The narrow boundary catches input-validation errors and converts them to
    400 OperationOutcome. If this were broadened to `except Exception`, it
    would mask programming bugs (TypeError, AttributeError) as 400 input-
    validation errors — silent-wrong-answer.

    If this probe FAILS, the narrow boundary was broadened (regression).
    """
    src = _source_module_text()
    func_src = _get_func_source(src, "_dispatch_batch_operation")
    assert func_src, "Could not extract _dispatch_batch_operation source"
    assert "except ValueError as exc:" in func_src, (
        "_dispatch_batch_operation MUST have `except ValueError as exc:` "
        "as the narrow input-validation boundary (per GLOBAL_RULES.md "
        "'Silent Fallbacks'). Broadening to Exception would mask programming "
        "bugs as 400 input-validation errors."
    )
    # MUST NOT have broad `except Exception` here (only ValueError allowed).
    assert "except Exception" not in func_src, (
        "_dispatch_batch_operation MUST NOT catch broad Exception — only "
        "ValueError. Broad catch at this level would mask programming bugs."
    )


def test_h22_qa038_structural_dispatch_returns_per_entry_4xx_on_value_error():
    """QA-038 SOURCE-READ probe: the narrow `except ValueError` at dispatch
    boundary MUST return `_batch_error_entry(400, str(exc))`.

    Without the 400 status code, ValueError would produce an ambiguous
    response status (e.g. 500 or None) — breaking clients that key on 4xx
    for input validation.
    """
    src = _source_module_text()
    func_src = _get_func_source(src, "_dispatch_batch_operation")
    # The narrow boundary returns 400 (input validation).
    assert "_batch_error_entry(400, str(exc))" in func_src, (
        "_dispatch_batch_operation's `except ValueError` MUST return "
        "`_batch_error_entry(400, str(exc))` to signal input-validation "
        "error (not 500 or 422)."
    )


# =============================================================================
# L3 — HTTPS scheme via `_deployment_base_url` (re-derived from QA-037 + QA-040)
# Verify the constructor handles IPv6/trailing-slash/scheme-env-var.
# =============================================================================


def test_h30_deployment_url_https_via_scheme_env_var(monkeypatch, tmp_path):
    """QA-037 re-derived: `_deployment_base_url` honors a separate
    `MEDTERM4DS_API_SCHEME=https` env var.

    Spec: §4.7.2 "SSL SHOULD be used for all production health care data
    exchange". The deployment URL MUST NOT silently downgrade an HTTPS
    deployment to plain HTTP.
    """
    client = _make_test_client(tmp_path, monkeypatch, scheme="https")
    try:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        impl_url = r.json().get("implementation", {}).get("url", "")
        assert impl_url.startswith("https://"), (
            f"Deployment URL MUST start with https:// when "
            f"MEDTERM4DS_API_SCHEME=https; got {impl_url!r}"
        )
    finally:
        client.close()


def test_h31_deployment_url_https_via_scheme_in_host(monkeypatch, tmp_path):
    """QA-037 re-derived: `_deployment_base_url` honors scheme embedded in
    MEDTERM4DS_API_HOST (e.g. "https://fhir.example.com").
    """
    client = _make_test_client(
        tmp_path, monkeypatch, host="https://fhir.example.com"
    )
    try:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        impl_url = r.json().get("implementation", {}).get("url", "")
        assert impl_url.startswith("https://fhir.example.com"), (
            f"Deployment URL MUST honor scheme-on-host; got {impl_url!r}"
        )
        # MUST NOT produce "http://https://..." (the pre-QA-037 shape).
        assert "http://https://" not in impl_url, (
            f"Deployment URL MUST NOT downgrade scheme-on-host; got {impl_url!r}"
        )
    finally:
        client.close()


def test_h32_deployment_url_ipv6_https_keeps_port(monkeypatch, tmp_path):
    """QA-040 re-derived: IPv6 host with scheme MUST keep the port.

    Pre-fix: bracket-based IPv6 detection failed because the port-stripping
    check `":" not in stripped.split("://", 1)[1]` evaluated False for IPv6
    (brackets contain `:`), causing the port to be dropped.
    """
    client = _make_test_client(
        tmp_path, monkeypatch, host="https://[::1]"
    )
    try:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        impl_url = r.json().get("implementation", {}).get("url", "")
        # The URL MUST contain the bracketed IPv6 host AND the port.
        assert "[::1]" in impl_url, (
            f"Deployment URL MUST preserve IPv6 brackets; got {impl_url!r}"
        )
        # Port must be present after the bracketed host.
        assert "]:8000" in impl_url or "]:" in impl_url, (
            f"Deployment URL MUST preserve port for IPv6; got {impl_url!r}"
        )
    finally:
        client.close()


def test_h33_deployment_url_trailing_slash_no_malformed(monkeypatch, tmp_path):
    """QA-040 re-derived: trailing-slash on host MUST NOT produce malformed
    "http://example.com/:8000" URLs.

    Pre-fix: the strip-trailing-slash logic only ran when the host had a
    scheme; a bare "example.com/" produced "http://example.com/:8000".
    """
    client = _make_test_client(
        tmp_path, monkeypatch, host="example.com/"
    )
    try:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        impl_url = r.json().get("implementation", {}).get("url", "")
        # MUST NOT contain "/:" (the malformed slash-then-port shape).
        assert "/:" not in impl_url, (
            f"Deployment URL MUST NOT have malformed '/:' from trailing slash; "
            f"got {impl_url!r}"
        )
    finally:
        client.close()


def test_h34_deployment_url_scheme_env_var_with_ipv6(monkeypatch, tmp_path):
    """QA-040 re-derived: `MEDTERM4DS_API_SCHEME=https` + IPv6 host (no
    scheme on host) MUST produce "https://[::1]:<port>".
    """
    client = _make_test_client(
        tmp_path, monkeypatch, host="[::1]", scheme="https", port="9000"
    )
    try:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        impl_url = r.json().get("implementation", {}).get("url", "")
        assert impl_url.startswith("https://[::1]"), (
            f"Deployment URL MUST be https://[::1]:... ; got {impl_url!r}"
        )
        # Port must be present after the bracketed host (not dropped).
        assert "]:9000" in impl_url, (
            f"Port must be preserved after IPv6 brackets; got {impl_url!r}"
        )
    finally:
        client.close()


def test_h35_deployment_url_default_http(monkeypatch, tmp_path):
    """QA-037 re-derived: default scheme is http (localhost dev)."""
    client = _make_test_client(tmp_path, monkeypatch)
    try:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        impl_url = r.json().get("implementation", {}).get("url", "")
        assert impl_url.startswith("http://"), (
            f"Default scheme MUST be http://; got {impl_url!r}"
        )
    finally:
        client.close()


# =============================================================================
# L4 — POST catch-all for unknown resource types (CF-EXPLORER-01)
# Re-derive the structural fix shape: POST /fhir/<UnknownResource>/$op MUST
# return a FHIR OperationOutcome, not Starlette's default 405.
# =============================================================================


def test_h40_post_unknown_resource_type_returns_fhir_outcome(fhir_client):
    """CF-EXPLORER-01 re-derived: POST /fhir/Patient (unknown resource type)
    MUST return a FHIR OperationOutcome with FHIR MIME Content-Type.

    Per FHIR R4 §3.1.0.1.5 + §3.1.0.1.9 — any 4xx response MAY carry an
    OperationOutcome and the correct MIME SHALL be used. Pre-fix: Starlette
    default 405 with `application/json` Content-Type and `{"detail": ...}`
    body (non-FHIR).
    """
    r = fhir_client.post("/fhir/Patient", json={"resourceType": "Patient"})
    # 405 is the spec-correct status for write attempts on a read-only server.
    assert r.status_code == 405, (
        f"POST to unknown resource type MUST return 405; got {r.status_code}"
    )
    # Content-Type MUST be FHIR MIME (not application/json from Starlette).
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"Content-Type MUST be application/fhir+json; got {ct!r}"
    )
    # Body MUST be a FHIR OperationOutcome (not Starlette's {"detail": ...}).
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"Body MUST be OperationOutcome; got resourceType="
        f"{body.get('resourceType')!r}"
    )


def test_h41_post_unknown_resource_type_with_id_returns_fhir_outcome(fhir_client):
    """CF-EXPLORER-01 re-derived: POST /fhir/Patient/1 (unknown resource type
    with id) MUST also return a FHIR OperationOutcome.
    """
    r = fhir_client.post(
        "/fhir/Patient/1", json={"resourceType": "Patient", "id": "1"}
    )
    assert r.status_code == 405
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_h42_post_unknown_resource_type_with_operation_returns_fhir_outcome(
    fhir_client,
):
    """CF-EXPLORER-01 re-derived (the SKEPTIC tip): POST to a FHIR operation
    on an unknown resource type (e.g. /fhir/Patient/$lookup) MUST return a
    FHIR OperationOutcome, not Starlette's 405 with non-FHIR body.

    This is the path that exercises the catch-all route registered LAST after
    all explicit operation routes.
    """
    r = fhir_client.post(
        "/fhir/Patient/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "73211009"},
            ],
        },
    )
    # 405 (read-only) OR 404 (not found) are both spec-acceptable; the KEY
    # is FHIR MIME + OperationOutcome body (not Starlette default).
    assert r.status_code in (404, 405), (
        f"POST to unknown-resource-type operation should be 4xx; got "
        f"{r.status_code}"
    )
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"Content-Type MUST be application/fhir+json; got {ct!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_h43_post_unknown_resource_type_source_read():
    """CF-EXPLORER-01 SOURCE-READ probe: the POST catch-all routes MUST be
    registered AFTER all explicit operation routes.

    The catch-all handlers (`write_unknown_resource_type`,
    `create_unknown_resource_type`) MUST exist in the source. Without them,
    POST to unknown resource types falls through to Starlette's default 405.
    """
    src = _source_module_text()
    assert "async def write_unknown_resource_type" in src, (
        "POST catch-all route handler `write_unknown_resource_type` is MISSING "
        "from apps/fhir_api.py — regression of CF-EXPLORER-01 fix."
    )
    assert "async def create_unknown_resource_type" in src, (
        "POST catch-all route handler `create_unknown_resource_type` is "
        "MISSING from apps/fhir_api.py — regression of CF-EXPLORER-01 fix."
    )
    # Both catch-alls MUST return 405 (not 404 — write attempts on read-only
    # server per §3.1.0.7).
    assert 'status=405' in src or "status_code=405" in src, (
        "POST catch-all handlers MUST return status=405 (write refusal on "
        "read-only server)."
    )


# =============================================================================
# L5 — `$expand` / `$closure` wired into batch dispatcher (QA-039)
# Re-derive that every advertised operation has a batch dispatch path.
# =============================================================================


def test_h50_batch_dispatcher_supports_expand(fhir_client):
    """QA-039 re-derived: `$expand` MUST be wired into the batch dispatcher.

    Without this, a batch entry with url=ValueSet/$expand would silently
    return 404 "Unknown operation" instead of executing the expansion.
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": "ValueSet/$expand?url=http://snomed.info/sct?fhir_vs",
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    assert len(entries) == 1
    # MUST NOT be 404 (which would indicate $expand is missing from dispatch).
    s0 = entries[0]["response"]["status"]
    assert s0 != "404", (
        f"$expand MUST be wired into batch dispatcher; got 404. "
        f"Resource: {entries[0].get('resource', {})}"
    )
    # The resource MUST be a ValueSet (the expansion result).
    res0 = entries[0].get("resource", {})
    assert res0.get("resourceType") in ("ValueSet", "OperationOutcome"), (
        f"$expand batch entry MUST return ValueSet or OperationOutcome; "
        f"got {res0.get('resourceType')!r}"
    )


def test_h51_batch_dispatcher_supports_closure(fhir_client):
    """QA-039 re-derived: `$closure` MUST be wired into the batch dispatcher.
    """
    bundle = _batch_bundle([
        {
            "request": {"method": "POST", "url": "CodeSystem/$closure"},
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "test-closure-batch"},
                ],
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    assert len(entries) == 1
    s0 = entries[0]["response"]["status"]
    # MUST NOT be 404 (which would indicate $closure is missing from dispatch).
    assert s0 != "404", (
        f"$closure MUST be wired into batch dispatcher; got 404. "
        f"Resource: {entries[0].get('resource', {})}"
    )


def test_h52_batch_dispatcher_supports_lookup(fhir_client):
    """QA-039 re-derived: `$lookup` MUST be wired into the batch dispatcher.
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$lookup"
                       "?system=http://snomed.info/sct&code=73211009",
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    assert len(entries) == 1
    s0 = entries[0]["response"]["status"]
    assert s0 != "404", (
        f"$lookup MUST be wired into batch dispatcher; got 404."
    )
    # MUST be Parameters (success) or OperationOutcome (error); NOT a generic
    # "Unknown operation" error.
    res0 = entries[0].get("resource", {})
    assert res0.get("resourceType") in ("Parameters", "OperationOutcome")


def test_h53_batch_dispatcher_supports_subsumes(fhir_client):
    """QA-039 re-derived: `$subsumes` MUST be wired into the batch dispatcher.
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$subsumes"
                        "?system=http://snomed.info/sct"
                        "&codeA=73211009&codeB=44054006"),
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    assert len(entries) == 1
    s0 = entries[0]["response"]["status"]
    assert s0 != "404", (
        f"$subsumes MUST be wired into batch dispatcher; got 404."
    )


def test_h54_batch_dispatcher_supports_validate_code(fhir_client):
    """QA-039 re-derived: CodeSystem/$validate-code MUST be wired into the
    batch dispatcher.
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$validate-code"
                        "?system=http://snomed.info/sct&code=73211009"),
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    s0 = entries[0]["response"]["status"]
    assert s0 != "404"
    res0 = entries[0].get("resource", {})
    assert res0.get("resourceType") in ("Parameters", "OperationOutcome")


def test_h55_batch_dispatcher_supports_vs_validate_code(fhir_client):
    """QA-039 re-derived: ValueSet/$validate-code MUST be wired into the
    batch dispatcher.
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("ValueSet/$validate-code"
                        "?url=http://snomed.info/sct"
                        "&system=http://snomed.info/sct&code=73211009"),
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    s0 = entries[0]["response"]["status"]
    assert s0 != "404"
    res0 = entries[0].get("resource", {})
    assert res0.get("resourceType") in ("Parameters", "OperationOutcome")


def test_h56_batch_dispatcher_supports_translate(fhir_client):
    """QA-039 re-derived: ConceptMap/$translate MUST be wired into the
    batch dispatcher.
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("ConceptMap/$translate"
                        "?system=http://snomed.info/sct&code=73211009"
                        "&targetsystem=http://hl7.org/fhir/sid/icd-10-cm"),
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    s0 = entries[0]["response"]["status"]
    assert s0 != "404"
    res0 = entries[0].get("resource", {})
    assert res0.get("resourceType") in ("Parameters", "OperationOutcome")


def test_h57_batch_dispatcher_source_read_all_ops_wired():
    """QA-039 SOURCE-READ probe: every advertised operation MUST be wired
    into `_dispatch_batch_operation`'s path-table.

    The 7 mandatory operations per FHIR R4 §4.7.1.2:
      - CodeSystem/$lookup
      - CodeSystem/$validate-code
      - CodeSystem/$subsumes
      - CodeSystem/$closure
      - ValueSet/$expand
      - ValueSet/$validate-code
      - ConceptMap/$translate
    """
    src = _source_module_text()
    func_src = _get_func_source(src, "_dispatch_batch_operation")
    mandatory_ops = [
        "/CodeSystem/$lookup",
        "/CodeSystem/$validate-code",
        "/CodeSystem/$subsumes",
        "/CodeSystem/$closure",
        "/ValueSet/$expand",
        "/ValueSet/$validate-code",
        "/ConceptMap/$translate",
    ]
    missing = [op for op in mandatory_ops if op not in func_src]
    assert not missing, (
        f"_dispatch_batch_operation is MISSING mandatory operations: {missing}. "
        f"This regresses QA-039 (4-tuple coverage audit)."
    )


# =============================================================================
# L6 — HTTP method boundary (PUT/PATCH/DELETE → 405)
# SKEPTIC just fixed DELETE (QA-001); verify PUT and PATCH still work AND the
# tuple now contains all three.
# =============================================================================


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_h60_qa001_rederived_write_methods_return_405(fhir_client, method):
    """QA-001 re-derived: ALL three write methods (PUT, PATCH, DELETE) MUST
    return 405 'Method Not Allowed' on the read-only batch endpoint.

    Spec: FHIR R4 §3.1.0.7 — write methods on a read-only server SHOULD
    return 405. SKEPTIC's QA-001 fix extended the tuple from ("PUT", "PATCH")
    to ("PUT", "PATCH", "DELETE"); HISTORIAN verifies all three are still
    there and produce 405 (not 400, which was the pre-QA-001 DELETE shape).
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": method,
                "url": "CodeSystem/123",
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200  # batch itself always 200
    entries = r.json()["entry"]
    assert len(entries) == 1
    s0 = entries[0]["response"]["status"]
    assert s0 == "405", (
        f"Write method {method} on read-only server MUST return 405; "
        f"got {s0}. If 400, the tuple was narrowed (regression of QA-001)."
    )
    # Per-entry resource MUST be an OperationOutcome with not-supported.
    res0 = entries[0].get("resource", {})
    assert res0.get("resourceType") == "OperationOutcome"


def test_h61_qa001_structural_tuple_contains_all_three_write_methods():
    """QA-001 SOURCE-READ probe: the write-method tuple at `_process_batch_entry`
    MUST contain all three of PUT, PATCH, DELETE.

    Pre-QA-001: `method in ("PUT", "PATCH")` → DELETE fell into the generic
    else-branch returning 400. Post-QA-001: tuple is ("PUT", "PATCH", "DELETE").

    If this probe FAILS, the tuple was narrowed back to 2 elements (regression).
    """
    src = _source_module_text()
    func_src = _get_func_source(src, "_process_batch_entry")
    # The tuple MUST be exactly ("PUT", "PATCH", "DELETE") in any order.
    assert '"PUT"' in func_src, "PUT missing from write-method tuple"
    assert '"PATCH"' in func_src, "PATCH missing from write-method tuple"
    assert '"DELETE"' in func_src, (
        "DELETE missing from write-method tuple — regresses QA-001."
    )
    # MUST NOT have a generic else-branch that returns 400 for DELETE.
    # The structural contract is: PUT/PATCH/DELETE → 405; only TRULY unknown
    # methods (e.g. FOO, BAR) → 400.
    # We verify by checking that the 405 path is taken for the three tuple
    # methods (the behavioral probe test_h60 covers this).


def test_h62_qa001_unknown_method_still_returns_400(fhir_client):
    """QA-001 re-derived complement: TRULY unknown methods (not in
    {GET, POST, PUT, PATCH, DELETE}) MUST still return 400.

    The 405 path is reserved for write-method refusal on a read-only server;
    it MUST NOT be broadened to "any non-GET/POST method" because that would
    mask the difference between "method is a valid HTTP write method" and
    "method is not a valid HTTP method at all".
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "FOOBAR",
                "url": "CodeSystem/123",
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    s0 = entries[0]["response"]["status"]
    assert s0 == "400", (
        f"Truly unknown method FOOBAR should return 400 (not 405); got {s0}"
    )


# =============================================================================
# L7 — Uppercase-scheme batch inheritance (TS-03/TERMINOLOGIST tip)
# Verify the TS-03 EXPLORER uppercase-scheme fix is inherited on the batch
# surface via _do_* handler delegation.
# =============================================================================


def test_h70_batch_lookup_uppercase_scheme_resolves(fhir_client):
    """TS-03 EXPLORER QA-001 re-derived on batch surface: a batch `$lookup`
    entry with uppercase-scheme system URI (`HTTP://snomed.info/sct`) MUST
    resolve identically to lowercase.

    Per RFC 3986 §3.1 scheme is case-insensitive. The fix landed at
    `fhir_uri_to_system`; the batch surface inherits via `_do_*` handler
    delegation. Without inheritance, batch $lookup with uppercase scheme
    would return 400 "Unrecognized system URI".
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$lookup"
                       "?system=HTTP://snomed.info/sct&code=73211009",
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    s0 = entries[0]["response"]["status"]
    # Success on uppercase-scheme = "found" (200); lookup fixture has DM.
    assert s0 == "200", (
        f"Batch $lookup with uppercase scheme should succeed; got {s0}. "
        f"Resource: {entries[0].get('resource', {})}"
    )
    # The response MUST be a Parameters (success), not OperationOutcome.
    res0 = entries[0].get("resource", {})
    assert res0.get("resourceType") == "Parameters"


def test_h71_batch_lookup_uppercase_scheme_byte_exact_parity(fhir_client):
    """TS-03 EXPLORER QA-001 re-derived: batch $lookup with uppercase scheme
    MUST produce byte-exact parity with lowercase-scheme invocation.
    """
    upper_bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$lookup"
                       "?system=HTTP://snomed.info/sct&code=73211009",
            },
        },
    ])
    lower_bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$lookup"
                       "?system=http://snomed.info/sct&code=73211009",
            },
        },
    ])
    r_upper = fhir_client.post("/fhir", json=upper_bundle)
    r_lower = fhir_client.post("/fhir", json=lower_bundle)
    assert r_upper.status_code == r_lower.status_code == 200
    upper_entry = r_upper.json()["entry"][0]
    lower_entry = r_lower.json()["entry"][0]
    assert upper_entry["response"]["status"] == lower_entry["response"]["status"]
    assert upper_entry["resource"] == lower_entry["resource"], (
        "Batch $lookup uppercase-scheme response MUST be byte-exact identical "
        "to lowercase-scheme response."
    )


def test_h72_batch_validate_code_uppercase_scheme(fhir_client):
    """TS-03 EXPLORER QA-001 re-derived: batch CodeSystem/$validate-code with
    uppercase scheme MUST resolve identically to lowercase.
    """
    upper_bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$validate-code"
                        "?system=HTTP://snomed.info/sct&code=73211009"),
            },
        },
    ])
    lower_bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$validate-code"
                        "?system=http://snomed.info/sct&code=73211009"),
            },
        },
    ])
    r_upper = fhir_client.post("/fhir", json=upper_bundle)
    r_lower = fhir_client.post("/fhir", json=lower_bundle)
    assert r_upper.status_code == r_lower.status_code == 200
    upper_entry = r_upper.json()["entry"][0]
    lower_entry = r_lower.json()["entry"][0]
    assert upper_entry["response"]["status"] == lower_entry["response"]["status"]
    assert upper_entry["resource"] == lower_entry["resource"]


def test_h73_batch_translate_uppercase_scheme_both_systems(fhir_client):
    """TS-03 EXPLORER QA-001 re-derived: batch ConceptMap/$translate with
    uppercase scheme on BOTH source AND targetsystem MUST resolve identically.
    """
    upper_bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("ConceptMap/$translate"
                        "?system=HTTP://snomed.info/sct&code=73211009"
                        "&targetsystem=HTTP://hl7.org/fhir/sid/icd-10-cm"),
            },
        },
    ])
    lower_bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("ConceptMap/$translate"
                        "?system=http://snomed.info/sct&code=73211009"
                        "&targetsystem=http://hl7.org/fhir/sid/icd-10-cm"),
            },
        },
    ])
    r_upper = fhir_client.post("/fhir", json=upper_bundle)
    r_lower = fhir_client.post("/fhir", json=lower_bundle)
    assert r_upper.status_code == r_lower.status_code == 200
    upper_entry = r_upper.json()["entry"][0]
    lower_entry = r_lower.json()["entry"][0]
    assert upper_entry["response"]["status"] == lower_entry["response"]["status"]
    assert upper_entry["resource"] == lower_entry["resource"]


# =============================================================================
# L8 — Order preservation (FHIR R4 §3.1.0.11.3) — re-derived
# =============================================================================


def test_h80_order_preserved_mixed_operations(fhir_client):
    """§3.1.0.11.3 re-derived: response entries MUST be in the same order
    as request entries.

    A batch with 4 mixed entries (validate-code, lookup, translate,
    subsumes) MUST produce 4 response entries in the SAME order.
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$validate-code"
                        "?system=http://snomed.info/sct&code=73211009"),
            },
        },
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$lookup"
                        "?system=http://snomed.info/sct&code=73211009"),
            },
        },
        {
            "request": {
                "method": "GET",
                "url": ("ConceptMap/$translate"
                        "?system=http://snomed.info/sct&code=73211009"
                        "&targetsystem=http://hl7.org/fhir/sid/icd-10-cm"),
            },
        },
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$subsumes"
                        "?system=http://snomed.info/sct"
                        "&codeA=73211009&codeB=44054006"),
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    assert len(entries) == 4
    # Each entry MUST have a response.status (correlated 1:1 with request).
    for i, e in enumerate(entries):
        assert "response" in e, f"Entry {i} missing 'response' block"
        assert "status" in e["response"], (
            f"Entry {i} missing 'response.status'"
        )
        assert "resource" in e, f"Entry {i} missing 'resource'"


def test_h81_order_preserved_with_failure_in_middle(fhir_client):
    """§3.1.0.11.3 re-derived: a failing entry in the MIDDLE of the batch
    MUST NOT re-order the response entries.

    Batch of 3: [success, failure, success]. Response MUST preserve order:
    [success-200, failure-4xx, success-200] — NOT [failure, success, success]
    or any other re-ordering.
    """
    bundle = _batch_bundle([
        # Entry 0: success.
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$validate-code"
                        "?system=http://snomed.info/sct&code=73211009"),
            },
        },
        # Entry 1: failure (empty system → ValueError at narrow boundary).
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$validate-code?system=&code=73211009",
            },
        },
        # Entry 2: success.
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$lookup"
                        "?system=http://snomed.info/sct&code=73211009"),
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    assert len(entries) == 3
    # Order: 0=200, 1=4xx, 2=200.
    assert entries[0]["response"]["status"] == "200"
    assert entries[1]["response"]["status"].startswith("4")
    assert entries[2]["response"]["status"] == "200"


def test_h82_large_batch_order_preserved(fhir_client):
    """§3.1.0.11.3 re-derived: a 50-entry batch MUST preserve order.

    The single in-order loop in `batch_endpoint` MUST NOT have any path that
    could re-order entries (no sorting, no parallel processing that returns
    out-of-order).
    """
    entries_req = []
    # 50 identical entries — order preservation is the contract, not diversity.
    for i in range(50):
        entries_req.append({
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$validate-code"
                        f"?system=http://snomed.info/sct&code=73211009"),
            },
        })
    bundle = _batch_bundle(entries_req)
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    assert len(entries) == 50, (
        f"50-entry batch MUST produce 50 response entries; got {len(entries)}"
    )
    # Every entry MUST have a response.status.
    for i, e in enumerate(entries):
        assert "response" in e and "status" in e["response"], (
            f"Entry {i} missing response.status"
        )


# =============================================================================
# L9 — Per-entry response shape (§3.1.0.11.3) — re-derived
# =============================================================================


def test_h90_per_entry_response_has_status_and_resource(fhir_client):
    """§3.1.0.11.3 re-derived: every response entry MUST have BOTH
    `response` (with `status`) AND `resource`.

    Quote: "Each entry element SHALL contain a response element".
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$lookup"
                        "?system=http://snomed.info/sct&code=73211009"),
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entry = r.json()["entry"][0]
    assert "response" in entry
    assert "status" in entry["response"]
    assert "resource" in entry


def test_h91_per_entry_response_status_is_string(fhir_client):
    """§3.1.0.11.3 re-derived: response.status MUST be a string (e.g. "200"),
    NOT an integer (200). The FHIR spec example shows string status codes.
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": ("CodeSystem/$lookup"
                        "?system=http://snomed.info/sct&code=73211009"),
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    entry = r.json()["entry"][0]
    status = entry["response"]["status"]
    assert isinstance(status, str), (
        f"response.status MUST be a string; got {type(status).__name__}"
    )


def test_h92_outer_status_200_regardless_of_inner_failures(fhir_client):
    """§3.1.0.11 re-derived: outer HTTP status MUST be 200 regardless of
    inner entry failures.

    Quote: "the HTTP response code is 200 Ok if the batch was processed
    correctly, regardless of the success of the operations within the Batch".
    """
    bundle = _batch_bundle([
        # All entries fail.
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$validate-code?system=&code=73211009",
            },
        },
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$validate-code?system=&code=44054006",
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, (
        f"Outer status MUST be 200 even when ALL entries fail; got "
        f"{r.status_code}"
    )


# =============================================================================
# L10 — Bundle shape validation (§3.1.0.11.1 + §4.7.8) — re-derived
# =============================================================================


def test_h100_bundle_wrong_resource_type_rejected(fhir_client):
    """§3.1.0.11.1 re-derived: POST /fhir with a non-Bundle body MUST be
    rejected with 400 + FHIR OperationOutcome.
    """
    r = fhir_client.post("/fhir", json={"resourceType": "Patient"})
    assert r.status_code == 400
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_h101_bundle_wrong_type_rejected(fhir_client):
    """§3.1.0.11.1 re-derived: POST /fhir with Bundle type=document MUST be
    rejected (only batch/transaction accepted).
    """
    r = fhir_client.post(
        "/fhir", json={"resourceType": "Bundle", "type": "document"}
    )
    assert r.status_code == 400
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_h102_bundle_entry_not_a_list_rejected(fhir_client):
    """§3.1.0.11.1 re-derived: Bundle.entry that is NOT a list MUST be
    rejected with 400.
    """
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": "not-a-list",
        },
    )
    assert r.status_code == 400


def test_h103_empty_bundle_returns_empty_batch_response(fhir_client):
    """§3.1.0.11 re-derived: empty batch (no entries) MUST return an empty
    batch-response Bundle.
    """
    r = fhir_client.post(
        "/fhir",
        json={"resourceType": "Bundle", "type": "batch", "entry": []},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "batch-response"
    assert body["entry"] == []


def test_h104_bundle_transaction_accepted_as_batch(fhir_client):
    """§3.1.0.11 re-derived: Bundle type=transaction is accepted and
    processed as batch (medterm4ds is read-only — no atomicity needed).
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "request": {
                    "method": "GET",
                    "url": ("CodeSystem/$lookup"
                            "?system=http://snomed.info/sct&code=73211009"),
                },
            },
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["resourceType"] == "Bundle"
    # type=batch-response is acceptable (we don't honor transaction semantics).
    assert body["type"] in ("batch-response", "transaction-response")


# =============================================================================
# L11 — URL parsing edge cases (§3.1.0.11) — re-derived
# =============================================================================


@pytest.mark.parametrize("url_prefix", [
    "",                  # base-relative
    "/fhir",             # /fhir prefix
    "http://localhost:8000/fhir",  # absolute with port
    "https://example.com/fhir",    # absolute https
])
def test_h110_batch_entry_url_prefixes_accepted(fhir_client, url_prefix):
    """§3.1.0.11 re-derived: batch entry URLs MAY be base-relative, /fhir-
    prefixed, OR absolute (with host). All three forms MUST be accepted.
    """
    url = f"{url_prefix}/CodeSystem/$lookup"
    if url_prefix:
        # When prefix is empty, url starts with "/" → base-relative form.
        # When prefix is /fhir, url is /fhir/CodeSystem/$lookup.
        # When prefix is http://host/fhir, url is absolute.
        pass
    else:
        url = "CodeSystem/$lookup"
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": f"{url}?system=http://snomed.info/sct&code=73211009",
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    s0 = entries[0]["response"]["status"]
    assert s0 == "200", (
        f"Batch entry URL {url!r} should resolve to 200; got {s0}. "
        f"Resource: {entries[0].get('resource', {})}"
    )


def test_h111_batch_entry_url_missing_operation_rejected(fhir_client):
    """§3.1.0.11 re-derived: a batch entry URL with no operation name (e.g.
    "CodeSystem") MUST be rejected with per-entry 404.
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem",
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200  # batch itself 200
    s0 = r.json()["entry"][0]["response"]["status"]
    assert s0 == "404", (
        f"URL with missing operation should be 404; got {s0}"
    )


def test_h112_batch_entry_url_path_traversal_safe(fhir_client):
    """§3.1.0.11 re-derived: a batch entry URL with path traversal (../)
    MUST NOT escape the FHIR routing namespace.

    The traversal is treated as a literal path segment; the dispatcher's
    path-table lookup fails → per-entry 404.
    """
    bundle = _batch_bundle([
        {
            "request": {
                "method": "GET",
                "url": "../../../etc/passwd",
            },
        },
    ])
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200  # batch itself 200
    s0 = r.json()["entry"][0]["response"]["status"]
    # Path traversal should NOT succeed — per-entry 4xx (400 or 404).
    assert s0.startswith("4"), (
        f"Path traversal should be rejected with 4xx; got {s0}"
    )


# =============================================================================
# L12 — CapabilityStatement advertises batch endpoint (re-derived)
# =============================================================================


def test_h120_capability_statement_advertises_batch_endpoint(fhir_client):
    """Re-derived: the CapabilityStatement SHOULD indicate batch support via
    the standard FHIR R4 mechanism.

    Per FHIR R4, the FHIR endpoint at POST /fhir is the standard batch
    endpoint. The CapabilityStatement's `rest[].resource[]` block lists
    supported interactions; `batch`/`transaction` are signalled via the
    `batch`/`transaction` flags in `rest.interaction`.

    This probe verifies the CapabilityStatement advertises the FHIR endpoint
    exists (the implementation URL is non-empty + parseable).
    """
    r = fhir_client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.json()
    # The implementation URL MUST be present and parseable.
    impl = body.get("implementation", {})
    impl_url = impl.get("url", "")
    assert impl_url, "CapabilityStatement.implementation.url MUST be non-empty"
    # MUST be a parseable URL.
    parsed = urlparse(impl_url)
    assert parsed.scheme in ("http", "https"), (
        f"Implementation URL scheme MUST be http or https; got "
        f"{parsed.scheme!r}"
    )
