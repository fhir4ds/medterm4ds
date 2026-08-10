"""EXPLORER resweep probes for TS-04 (Security, Batch Validation, Batch Translation).

Fresh full-sweep per USER_DIRECTIVES [2026-08-08]. Per GLOBAL_RULES.md
fresh-full-sweep baseline discipline, NEW lateral probes live in a sibling
file (test_ts04_explorer_resweep.py) and do NOT trust the prior TS-04
EXPLORER baseline (test_ts04_explorer.py — that file holds the prior run's
baselines, NOT new bugs).

Source: https://build.fhir.org/terminology-service.html §4.7.2, §4.7.8,
§4.7.10 + https://hl7.org/fhir/R4/http.html#transaction (§3.1.0.11).

EXPLORER lens (per ROLE_QA_ENGINEER.md Section 3 "What's not yet tested?"):
Lateral thinking. Unusual parameter combinations, undocumented features,
integration corners. SKEPTIC + HISTORIAN hardened the surface with hostile
inputs and regression pins; EXPLORER probes lateral combinations that the
regression lens doesn't exercise.

HISTORIAN tip for EXPLORER (high-priority): probe lateral combinations on
the batch surface:
  - Batch with mixed operation types AND mixed encoding (GET + POST in
    same batch — POST with Parameters body for $lookup next to GET
    $validate-code).
  - Batch with very long URLs (>1000 chars in entry.request.url).
  - Batch with deeply-nested Parameters bodies (multi-level codeableConcept
    containing nested coding in $validate-code entry).
  - Batch with very large entry counts (1000+ entries — perf + correctness).
  - Batch with mixed-case HTTP method names ('get', 'Get', 'GET' — RFC
    7230 §3.1.1 method case-sensitive per RFC 7230 §3.1.1).
  - Batch with entry URL containing query string AND POST body Parameters
    (param precedence).
  - Batch with operations on different resource types in same batch
    (CodeSystem + ValueSet + ConceptMap operations interleaved).
  - Batch entry with full URL (http://server/fhir/CodeSystem/$lookup) vs
    path-only (/fhir/CodeSystem/$lookup).
"""

from __future__ import annotations

import ast
import inspect
import time
from pathlib import Path

import pytest


# =============================================================================
# Shared helpers (mirror HISTORIAN resweep helpers for AST source-reading)
# =============================================================================


def _source_module_text() -> str:
    """Read the apps.fhir_api source text for source-read probes."""
    from medterm4ds.apps import fhir_api
    return inspect.getsource(fhir_api)


def _get_func_source(module_text: str, func_name: str) -> str:
    """Extract a nested function's source text using AST + line offsets.

    Walks BOTH ast.FunctionDef AND ast.AsyncFunctionDef (extends TS-01
    HISTORIAN methodology — sync-only walk missed _process_batch_entry and
    _dispatch_batch_operation which are async).
    """
    tree = ast.parse(module_text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == func_name:
            return ast.get_source_segment(module_text, node) or ""
    return ""


def _batch_bundle(entries: list[dict]) -> dict:
    """Build a Bundle type=batch with the given entries."""
    return {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": entries,
    }


def _validate_code_get_entry(system: str, code: str) -> dict:
    """Build a GET entry for ValueSet/$validate-code."""
    return {
        "request": {
            "method": "GET",
            "url": f"ValueSet/$validate-code?system={system}&code={code}",
        }
    }


def _validate_code_post_entry(system: str, code: str) -> dict:
    """Build a POST entry for CodeSystem/$validate-code with Parameters body."""
    return {
        "request": {
            "method": "POST",
            "url": "CodeSystem/$validate-code",
        },
        "resource": {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": system},
                {"name": "code", "valueCode": code},
            ],
        }
    }


def _lookup_post_entry(system: str, code: str) -> dict:
    """Build a POST entry for CodeSystem/$lookup with Parameters body."""
    return {
        "request": {
            "method": "POST",
            "url": "CodeSystem/$lookup",
        },
        "resource": {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": system},
                {"name": "code", "valueCode": code},
            ],
        }
    }


# =============================================================================
# L1 — Mixed operation types AND mixed encoding (GET + POST in same batch)
# §3.1.0.11 Batch: entries MAY include "a mix of other interactions defined
# on this page". Each entry is processed independently.
# =============================================================================


def test_e10_mixed_get_and_post_entries_in_same_batch(fhir_client):
    """L1: GET $validate-code + POST $lookup in the same batch.

    Per §3.1.0.11: entries MAY include "a mix of other interactions". Each
    entry is processed independently. The GET entry uses query-string params;
    the POST entry uses a Parameters body.
    """
    entries = [
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        _lookup_post_entry("http://snomed.info/sct", "44054006"),
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "batch-response"
    assert len(body["entry"]) == 2
    # Both entries should succeed (the seeded code is valid).
    assert body["entry"][0]["response"]["status"] == "200"
    assert body["entry"][1]["response"]["status"] == "200"
    # First entry is a Parameters (validate-code result); second is Parameters
    # (lookup result). Different content but both 200.
    assert body["entry"][0]["resource"]["resourceType"] == "Parameters"
    assert body["entry"][1]["resource"]["resourceType"] == "Parameters"


def test_e11_mixed_encoding_same_operation_get_and_post(fhir_client):
    """L1: GET $lookup AND POST $lookup for the same code — both should
    return byte-exact identical Parameters bodies (mixed encoding must not
    drift)."""
    entries = [
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$lookup?system=http://snomed.info/sct&code=44054006",
            }
        },
        _lookup_post_entry("http://snomed.info/sct", "44054006"),
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entry"]) == 2
    assert body["entry"][0]["response"]["status"] == "200"
    assert body["entry"][1]["response"]["status"] == "200"
    # Byte-exact parity on the Parameters body (mixed encoding invariant).
    assert body["entry"][0]["resource"] == body["entry"][1]["resource"]


def test_e12_get_post_get_post_interleaved(fhir_client):
    """L1: 4-entry interleaved batch (GET, POST, GET, POST) — order
    preservation + independence. Each entry processed in declared order."""
    entries = [
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        _lookup_post_entry("http://snomed.info/sct", "44054006"),
        _validate_code_get_entry("http://snomed.info/sct", "73211009"),
        _lookup_post_entry("http://snomed.info/sct", "73211009"),
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entry"]) == 4
    statuses = [e["response"]["status"] for e in body["entry"]]
    assert all(s == "200" for s in statuses), f"expected all 200, got {statuses}"


# =============================================================================
# L2 — Very long URLs (>1000 chars in entry.request.url)
# §3.1.0.11: no upper bound on entry.request.url length. The server MUST
# handle gracefully (not 500).
# =============================================================================


def test_e20_batch_entry_with_very_long_url_via_long_code(fhir_client):
    """L2: a GET entry with a 2000-char code value embedded in the URL.

    The server should NOT crash. Expected: per-entry 200 with result=false
    (the code doesn't exist) OR per-entry 4xx for input validation — NOT a
    500 / NOT a poison-whole-batch.
    """
    long_code = "A" * 2000
    entries = [
        _validate_code_get_entry("http://snomed.info/sct", long_code),
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200  # outer batch always 200
    body = resp.json()
    assert len(body["entry"]) == 2
    # The long-code entry must NOT 500 (poison); it must return 4xx or 200-with-result-false.
    long_entry_status = body["entry"][0]["response"]["status"]
    assert long_entry_status.startswith(("2", "4")), \
        f"long-code entry status {long_entry_status!r} should be 2xx/4xx, not 5xx"
    # The well-formed entry MUST still succeed — per-entry isolation.
    assert body["entry"][1]["response"]["status"] == "200"


def test_e21_batch_entry_with_very_long_url_via_long_system(fhir_client):
    """L2: a GET entry with a 2000-char system URI embedded in the URL.

    Same shape as e20 but the long value is in the system param. The server
    should reject the entry with a 4xx (unrecognized system) — NOT 500.
    """
    long_system = "http://" + "x" * 1990 + ".example/org"
    entries = [
        {
            "request": {
                "method": "GET",
                "url": f"CodeSystem/$validate-code?system={long_system}&code=123",
            }
        },
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entry"]) == 2
    long_entry_status = body["entry"][0]["response"]["status"]
    assert long_entry_status.startswith(("2", "4")), \
        f"long-system entry status {long_entry_status!r} should be 2xx/4xx, not 5xx"
    assert body["entry"][1]["response"]["status"] == "200"


def test_e22_batch_entry_with_url_over_fastapi_default_limit(fhir_client):
    """L2: batch entry URL exceeding Starlette's default URL length (~4096
    chars). Per §3.1.0.11 there's no spec-mandated limit; verify the entry
    is isolated (4xx or 200-with-result-false) rather than poisoning the
    batch."""
    # 5000-char URL via a long query param value.
    long_value = "B" * 5000
    entries = [
        {
            "request": {
                "method": "GET",
                "url": f"CodeSystem/$validate-code?system=http://snomed.info/sct&code={long_value}",
            }
        },
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    # Outer batch status: 200 if Starlette accepted the POST body. The long
    # URL is INSIDE the JSON body, not the POST's own URL.
    assert resp.status_code == 200, \
        f"outer batch status {resp.status_code}; body: {resp.text[:500]}"
    body = resp.json()
    assert len(body["entry"]) == 2
    long_entry_status = body["entry"][0]["response"]["status"]
    assert long_entry_status.startswith(("2", "4")), \
        f"long-URL entry status {long_entry_status!r} should be 2xx/4xx, not 5xx"
    assert body["entry"][1]["response"]["status"] == "200"


# =============================================================================
# L3 — Deeply-nested Parameters bodies (multi-level codeableConcept with
# nested coding). §3.1.0.11: POST entries pass a Parameters body. The
# dispatcher's extractors should handle deeply-nested structures gracefully.
# =============================================================================


def test_e30_batch_validate_code_with_deeply_nested_codeable_concept(fhir_client):
    """L3: POST CodeSystem/$validate-code with a codeableConcept containing
    multiple codings (the standard nesting level). Verify the all-pairs
    helper finds the valid code among the invalid ones."""
    entries = [
        {
            "request": {
                "method": "POST",
                "url": "CodeSystem/$validate-code",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {
                        "name": "codeableConcept",
                        "valueCodeableConcept": {
                            "coding": [
                                {
                                    "system": "http://snomed.info/sct",
                                    "code": "INVALID-CODE-1",
                                    "display": "First invalid coding",
                                },
                                {
                                    "system": "http://snomed.info/sct",
                                    "code": "44054006",
                                    "display": "Type 2 diabetes mellitus",
                                },
                                {
                                    "system": "http://snomed.info/sct",
                                    "code": "INVALID-CODE-2",
                                    "display": "Third invalid coding",
                                },
                            ]
                        },
                    }
                ],
            },
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entry"]) == 1
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200"
    # Per CS-03 SKEPTIC QA-049: "any coding match → result=true". The valid
    # code (44054006) is in the middle of the list.
    params = entry["resource"]["parameter"]
    result_param = next(p for p in params if p["name"] == "result")
    assert result_param["valueBoolean"] is True


def test_e31_batch_validate_code_with_nested_codeableConcept_text(fhir_client):
    """L3: codeableConcept with `text` field AND nested coding. The extractor
    should ignore the text field and use the coding."""
    entries = [
        {
            "request": {
                "method": "POST",
                "url": "CodeSystem/$validate-code",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {
                        "name": "codeableConcept",
                        "valueCodeableConcept": {
                            "text": "Patient has type 2 diabetes",
                            "coding": [
                                {
                                    "system": "http://snomed.info/sct",
                                    "code": "44054006",
                                }
                            ],
                        },
                    }
                ],
            },
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200"
    params = entry["resource"]["parameter"]
    result_param = next(p for p in params if p["name"] == "result")
    assert result_param["valueBoolean"] is True


def test_e32_batch_validate_code_with_empty_coding_list(fhir_client):
    """L3: codeableConcept with an EMPTY coding list (just `text`). The
    extractor should NOT crash on the empty list — return a 4xx for missing
    system+code, NOT a 500."""
    entries = [
        {
            "request": {
                "method": "POST",
                "url": "CodeSystem/$validate-code",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {
                        "name": "codeableConcept",
                        "valueCodeableConcept": {
                            "text": "Unknown concept",
                            "coding": [],
                        },
                    }
                ],
            },
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200  # outer batch always 200
    body = resp.json()
    entry = body["entry"][0]
    # Per-entry: should be a 4xx (missing system+code) NOT a 5xx.
    status = entry["response"]["status"]
    assert status.startswith("4"), \
        f"empty-coding-list entry status {status!r} should be 4xx, not {status}"


def test_e33_batch_validate_code_with_coding_missing_system(fhir_client):
    """L3: codeableConcept with a coding that has code but no system. The
    extractor should skip this partial coding and look for the next complete
    one."""
    entries = [
        {
            "request": {
                "method": "POST",
                "url": "CodeSystem/$validate-code",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {
                        "name": "codeableConcept",
                        "valueCodeableConcept": {
                            "coding": [
                                # Partial coding — code but no system.
                                {"code": "PARTIAL"},
                                # Complete coding.
                                {
                                    "system": "http://snomed.info/sct",
                                    "code": "44054006",
                                },
                            ]
                        },
                    }
                ],
            },
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    # The complete coding should be found and result=true returned.
    assert entry["response"]["status"] == "200"
    params = entry["resource"]["parameter"]
    result_param = next(p for p in params if p["name"] == "result")
    assert result_param["valueBoolean"] is True


def test_e34_batch_validate_code_with_coding_being_a_string(fhir_client):
    """L3: codeableConcept.coding being a STRING (not a list of dicts).
    Malformed input — per-entry isolation should produce a 4xx, NOT a 5xx."""
    entries = [
        {
            "request": {
                "method": "POST",
                "url": "CodeSystem/$validate-code",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {
                        "name": "codeableConcept",
                        "valueCodeableConcept": {
                            "coding": "not-a-list"
                        },
                    }
                ],
            },
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    status = entry["response"]["status"]
    assert status.startswith(("2", "4")), \
        f"string-coding entry status {status!r} should be 2xx/4xx, not 5xx"


# =============================================================================
# L4 — Very large entry counts (1000+ entries — performance + correctness)
# §3.1.0.11.3: "one entry for each entry in the request, in the same order".
# Order preservation must hold at scale.
# =============================================================================


def test_e40_large_batch_100_entries_order_preserved(fhir_client):
    """L4: 100-entry batch. Verify order preservation + no batch-level 500."""
    n = 100
    entries = [
        _validate_code_get_entry("http://snomed.info/sct", f"code-{i:03d}")
        for i in range(n)
    ]
    # Mix in a few valid codes so we have variety.
    entries[0] = _validate_code_get_entry("http://snomed.info/sct", "44054006")
    entries[n // 2] = _validate_code_get_entry("http://snomed.info/sct", "73211009")
    entries[n - 1] = _validate_code_get_entry("http://snomed.info/sct", "44054006")

    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entry"]) == n
    # Order preserved: response[i] corresponds to request[i].
    # The valid codes at known indices should return 200; others should return
    # 4xx (not-found). The KEY invariant is len(response) == len(request) AND
    # the response order matches the request order.
    statuses = [e["response"]["status"] for e in body["entry"]]
    assert statuses[0] == "200", f"entry 0 should be 200 (valid code), got {statuses[0]}"
    assert statuses[n // 2] == "200", \
        f"entry {n // 2} should be 200 (valid code), got {statuses[n // 2]}"
    assert statuses[n - 1] == "200", \
        f"entry {n - 1} should be 200 (valid code), got {statuses[n - 1]}"


def test_e41_large_batch_500_entries_completes_under_30s(fhir_client):
    """L4: 500-entry batch. Verify it completes in reasonable time and
    preserves order. Performance + correctness invariant."""
    n = 500
    entries = [
        _validate_code_get_entry("http://snomed.info/sct", "44054006")
        for _ in range(n)
    ]
    t0 = time.perf_counter()
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entry"]) == n
    # All entries use the valid code 44054006 → all should be 200.
    statuses = [e["response"]["status"] for e in body["entry"]]
    assert all(s == "200" for s in statuses), \
        f"expected all 200, got non-200 count: {sum(1 for s in statuses if s != '200')}"
    # Performance guard: 500 simple lookups should complete in well under 30s.
    # This is a generous bound; the goal is to catch catastrophic regressions.
    assert elapsed < 30.0, f"500-entry batch took {elapsed:.1f}s (expected <30s)"


# =============================================================================
# L5 — Mixed-case HTTP method names ('get', 'Get', 'GET')
# RFC 7230 §3.1.1: "The request method is case-sensitive."
# medterm4ds normalizes via .upper() — verify behavior is well-defined.
# =============================================================================


def test_e50_batch_entry_method_uppercase_get(fhir_client):
    """L5: baseline — 'GET' (uppercase) is the canonical form. Verify it
    succeeds (control case for the lowercase/camelcase probes)."""
    entries = [
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            }
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert body["entry"][0]["response"]["status"] == "200"


def test_e51_batch_entry_method_lowercase_get_normalized(fhir_client):
    """L5: 'get' (lowercase). RFC 7230 §3.1.1 says methods are case-
    sensitive, BUT medterm4ds normalizes via `.upper()` (apps/fhir_api.py
    line 1064). The intent is to accept lowercase as a courtesy. Verify the
    normalization produces a 200 (not a 400 'Unsupported method')."""
    entries = [
        {
            "request": {
                "method": "get",
                "url": "CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            }
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry_status = body["entry"][0]["response"]["status"]
    # If .upper() normalization is in place, this is 200. If not, 400.
    # The probe asserts the CURRENT (normalized) behavior — a future
    # change that removes .upper() will fail loudly here.
    assert entry_status == "200", \
        f"lowercase 'get' should be normalized to GET (200); got {entry_status}"


def test_e52_batch_entry_method_camelcase_get_normalized(fhir_client):
    """L5: 'Get' (camelcase). Same normalization invariant as e51."""
    entries = [
        {
            "request": {
                "method": "Get",
                "url": "CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            }
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry_status = body["entry"][0]["response"]["status"]
    assert entry_status == "200", \
        f"camelcase 'Get' should be normalized to GET (200); got {entry_status}"


def test_e53_batch_entry_method_lowercase_post_normalized(fhir_client):
    """L5: 'post' (lowercase). Verify POST normalization works too — and
    that the Parameters body is correctly consumed."""
    entries = [
        {
            "request": {
                "method": "post",
                "url": "CodeSystem/$lookup",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                ],
            },
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry_status = body["entry"][0]["response"]["status"]
    assert entry_status == "200", \
        f"lowercase 'post' should be normalized to POST (200); got {entry_status}"


def test_e54_source_read_method_normalization_is_in_place():
    """L5 SOURCE-READ: verify the .upper() normalization is structurally
    present in _process_batch_entry. This is the load-bearing contract for
    e51/e52/e53."""
    src = _get_func_source(_source_module_text(), "_process_batch_entry")
    assert ".upper()" in src, \
        "_process_batch_entry must normalize method via .upper() to accept " \
        "lowercase/camelcase method names per RFC 7230 §3.1.1 courtesy"


# =============================================================================
# L6 — Entry URL containing query string AND POST body Parameters
# (param precedence). §3.1.0.11: POST entries pass params via the
# Parameters body. What if the URL ALSO has a query string?
# =============================================================================


def test_e60_post_entry_with_query_string_in_url_ignored(fhir_client):
    """L6: POST entry with BOTH a query string in the URL AND a Parameters
    body. The Parameters body should win (per §3.1.0.11: POST entries pass
    params via the resource body). The query string params should be ignored
    for POST (or, if the impl merges them, the body wins on conflict).

    Concretely: URL says code=WRONG, body says code=44054006. The body
    should win → result=true."""
    entries = [
        {
            "request": {
                "method": "POST",
                # Query string with WRONG code.
                "url": "CodeSystem/$validate-code?system=http://snomed.info/sct&code=WRONG",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},  # CORRECT
                ],
            },
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200"
    params = entry["resource"]["parameter"]
    result_param = next(p for p in params if p["name"] == "result")
    # The body code (44054006) is valid → result=true. If the query string
    # code (WRONG) won, result would be false.
    assert result_param["valueBoolean"] is True, \
        "POST body Parameters should take precedence over URL query string"


def test_e61_post_entry_with_query_string_in_url_consistent_with_per_op_route(fhir_client):
    """L6: byte-exact parity between batch POST (with query string in URL)
    and per-operation POST (Parameters body only). The query string in the
    batch URL must not alter the response."""
    # Batch entry: POST with query string in URL.
    batch_body = _batch_bundle([
        {
            "request": {
                "method": "POST",
                "url": "CodeSystem/$validate-code?foo=bar",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                ],
            },
        }
    ])
    batch_resp = fhir_client.post("/fhir", json=batch_body)
    assert batch_resp.status_code == 200
    batch_entry = batch_resp.json()["entry"][0]
    assert batch_entry["response"]["status"] == "200"

    # Per-operation POST: same Parameters body, no URL query string.
    per_op_resp = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "44054006"},
            ],
        },
    )
    assert per_op_resp.status_code == 200

    # Byte-exact parity on the Parameters body (clinical content).
    assert batch_entry["resource"] == per_op_resp.json()


def test_e62_get_entry_with_post_body_resource_ignored(fhir_client):
    """L6: GET entry that ALSO has a `resource` field. Per §3.1.0.11, GET
    entries pass params via the query string; the `resource` field is for
    POST/PUT/PATCH bodies. The impl should ignore the resource on GET and
    use the query string."""
    entries = [
        {
            "request": {
                "method": "GET",
                # Query string with the CORRECT code.
                "url": "CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            },
            # Resource body with WRONG code — should be IGNORED on GET.
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "WRONG"},
                ],
            },
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200"
    params = entry["resource"]["parameter"]
    result_param = next(p for p in params if p["name"] == "result")
    # GET uses query string → code=44054006 (valid) → result=true.
    # If the resource body were consulted, code=WRONG → result=false.
    assert result_param["valueBoolean"] is True, \
        "GET entry should ignore resource body and use query string params"


# =============================================================================
# L7 — Operations on different resource types interleaved in same batch
# (CodeSystem + ValueSet + ConceptMap operations). §3.1.0.11: entries MAY
# include "a mix of other interactions".
# =============================================================================


def test_e70_interleaved_cs_vs_cm_operations(fhir_client):
    """L7: CodeSystem/$lookup + ValueSet/$validate-code + ConceptMap/$translate
    in the same batch. All 3 resource types interleaved."""
    entries = [
        _lookup_post_entry("http://snomed.info/sct", "44054006"),
        {
            "request": {
                "method": "POST",
                "url": "ValueSet/$validate-code",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                ],
            },
        },
        {
            "request": {
                "method": "POST",
                "url": "ConceptMap/$translate",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                    {"name": "targetsystem", "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                ],
            },
        },
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entry"]) == 3
    statuses = [e["response"]["status"] for e in body["entry"]]
    # All 3 should succeed (each operation on its own resource type).
    assert all(s == "200" for s in statuses), \
        f"interleaved CS/VS/CM should all succeed, got {statuses}"


def test_e71_interleaved_get_operations_across_resource_types(fhir_client):
    """L7: GET entries for CodeSystem/$lookup, ValueSet/$validate-code,
    ConceptMap/$translate — all via query string, interleaved."""
    entries = [
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$lookup?system=http://snomed.info/sct&code=44054006",
            }
        },
        {
            "request": {
                "method": "GET",
                "url": "ValueSet/$validate-code?system=http://snomed.info/sct&code=44054006",
            }
        },
        {
            "request": {
                "method": "GET",
                "url": "ConceptMap/$translate?system=http://snomed.info/sct&code=44054006&targetsystem=http://hl7.org/fhir/sid/icd-10-cm",
            }
        },
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entry"]) == 3
    statuses = [e["response"]["status"] for e in body["entry"]]
    assert all(s == "200" for s in statuses), \
        f"interleaved GET across CS/VS/CM should all succeed, got {statuses}"


def test_e72_interleaved_known_and_unknown_operations(fhir_client):
    """L7: mix of known ops ($lookup) and unknown ops ($nonexistent) in the
    same batch. Per-entry isolation: the known op succeeds, the unknown op
    returns a per-entry 4xx, the batch outer status is still 200."""
    entries = [
        _lookup_post_entry("http://snomed.info/sct", "44054006"),
        {
            "request": {
                "method": "POST",
                "url": "CodeSystem/$nonexistent",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                ],
            },
        },
        _lookup_post_entry("http://snomed.info/sct", "73211009"),
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entry"]) == 3
    statuses = [e["response"]["status"] for e in body["entry"]]
    # Entry 0: known op → 200.
    assert statuses[0] == "200"
    # Entry 1: unknown op → 4xx (404 per the dispatcher's else-branch).
    assert statuses[1].startswith("4"), \
        f"unknown op should be 4xx, got {statuses[1]}"
    # Entry 2: known op → 200 (per-entry isolation — entry 1 didn't poison).
    assert statuses[2] == "200"


# =============================================================================
# L8 — Batch entry URL with full URL vs path-only
# §3.1.0.11.2: "When processing a 'POST' (create), the full URL is treated
# as the id of the resource on the source". For operations, the server
# strips the host prefix.
# =============================================================================


def test_e80_batch_entry_full_url_with_host(fhir_client):
    """L8: entry.request.url is a FULL URL with host
    (http://server/fhir/CodeSystem/$lookup). The _parse_batch_entry_url
    helper should strip the host and dispatch correctly."""
    entries = [
        {
            "request": {
                "method": "GET",
                "url": "http://example.org/fhir/CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            }
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200", \
        f"full-URL entry should be dispatched correctly (200); got {entry['response']['status']}"


def test_e81_batch_entry_full_url_with_https_host(fhir_client):
    """L8: entry.request.url is a FULL URL with HTTPS host."""
    entries = [
        {
            "request": {
                "method": "GET",
                "url": "https://fhir.example.org/fhir/CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            }
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200"


def test_e82_batch_entry_full_url_with_port(fhir_client):
    """L8: entry.request.url with host:port."""
    entries = [
        {
            "request": {
                "method": "GET",
                "url": "http://localhost:8000/fhir/CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            }
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200"


def test_e83_batch_entry_path_only_no_leading_slash(fhir_client):
    """L8: entry.request.url is path-only without leading slash
    (CodeSystem/$validate-code?...). _parse_batch_entry_url should add the
    leading slash."""
    entries = [
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            }
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200"


def test_e84_batch_entry_full_url_and_path_only_byte_exact_parity(fhir_client):
    """L8: a full-URL entry and a path-only entry for the SAME operation
    should return byte-exact identical Parameters bodies (the URL form must
    not alter clinical content)."""
    entries = [
        {
            "request": {
                "method": "GET",
                "url": "http://example.org/fhir/CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            }
        },
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            }
        },
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert body["entry"][0]["response"]["status"] == "200"
    assert body["entry"][1]["response"]["status"] == "200"
    # Byte-exact parity on the Parameters body.
    assert body["entry"][0]["resource"] == body["entry"][1]["resource"]


# =============================================================================
# L9 — Response Bundle shape on lateral combinations
# §3.1.0.11.3: "one entry for each entry in the request, in the same order".
# Verify the outer Bundle shape holds across all lateral combinations.
# =============================================================================


def test_e90_batch_response_resource_type_and_type_field(fhir_client):
    """L9: every batch response has resourceType=Bundle + type=batch-response,
    regardless of entry mix."""
    entries = [_validate_code_get_entry("http://snomed.info/sct", "44054006")]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    body = resp.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "batch-response"


def test_e91_batch_response_entry_count_matches_request(fhir_client):
    """L9: response entry count == request entry count, even with mixed
    success/failure entries."""
    entries = [
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),  # valid
        _validate_code_get_entry("http://snomed.info/sct", "INVALID"),    # invalid
        {
            "request": {
                "method": "PUT",
                "url": "CodeSystem/1",
            }
        },  # 405
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    body = resp.json()
    assert len(body["entry"]) == len(entries), \
        f"response entry count {len(body['entry'])} != request count {len(entries)}"


def test_e92_batch_response_entry_response_block_has_status_string(fhir_client):
    """L9: every response entry has entry.response.status as a STRING (per
    §3.1.0.11.3). Lateral mix: GET success + POST success + write-method
    rejection."""
    entries = [
        _validate_code_get_entry("http://snomed.info/sct", "44054006"),
        _validate_code_post_entry("http://snomed.info/sct", "44054006"),
        {"request": {"method": "DELETE", "url": "CodeSystem/1"}},
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    body = resp.json()
    for i, entry in enumerate(body["entry"]):
        assert "response" in entry, f"entry {i} missing response block"
        assert "status" in entry["response"], f"entry {i} missing response.status"
        assert isinstance(entry["response"]["status"], str), \
            f"entry {i} response.status should be str, got {type(entry['response']['status'])}"


# =============================================================================
# L10 — Uppercase-scheme batch inheritance extended to lateral combinations
# (extends SKEPTIC L8 + HISTORIAN L7 — verify the TS-03 EXPLORER fix holds
# across mixed-encoding AND mixed-resource-type batches)
# RFC 3986 §3.1: scheme is case-insensitive.
# =============================================================================


def test_e100_mixed_encoding_with_uppercase_scheme_both_entries(fhir_client):
    """L10: GET $validate-code with uppercase scheme AND POST $lookup with
    uppercase scheme, in the SAME batch. Both should resolve via the TS-03
    EXPLORER fix."""
    entries = [
        {
            "request": {
                "method": "GET",
                "url": "CodeSystem/$validate-code?system=HTTP://snomed.info/sct&code=44054006",
            }
        },
        {
            "request": {
                "method": "POST",
                "url": "CodeSystem/$lookup",
            },
            "resource": {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "HTTP://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                ],
            },
        },
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert body["entry"][0]["response"]["status"] == "200"
    assert body["entry"][1]["response"]["status"] == "200"


def test_e101_uppercase_scheme_on_translate_targetsystem_too(fhir_client):
    """L10: $translate with uppercase scheme on BOTH system AND targetsystem.
    Byte-exact parity with lowercase scheme on the same operation."""
    upper_entries = [{
        "request": {
            "method": "POST",
            "url": "ConceptMap/$translate",
        },
        "resource": {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "HTTP://snomed.info/sct"},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": "HTTP://hl7.org/fhir/sid/icd-10-cm"},
            ],
        },
    }]
    lower_entries = [{
        "request": {
            "method": "POST",
            "url": "ConceptMap/$translate",
        },
        "resource": {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
            ],
        },
    }]

    upper_resp = fhir_client.post("/fhir", json=_batch_bundle(upper_entries))
    lower_resp = fhir_client.post("/fhir", json=_batch_bundle(lower_entries))
    assert upper_resp.status_code == 200
    assert lower_resp.status_code == 200

    upper_entry = upper_resp.json()["entry"][0]
    lower_entry = lower_resp.json()["entry"][0]
    assert upper_entry["response"]["status"] == lower_entry["response"]["status"]
    # Byte-exact parity on clinical content (Parameters body).
    assert upper_entry["resource"] == lower_entry["resource"]


# =============================================================================
# L11 — Cross-handler batch-vs-per-operation byte-exact parity extended
# Verifies the batch dispatcher doesn't drift from per-operation POST routes
# on lateral input shapes.
# =============================================================================


def test_e110_batch_vs_per_op_validate_code_codeable_concept_parity(fhir_client):
    """L11: batch CodeSystem/$validate-code with codeableConcept should
    return byte-exact identical Parameters body as the per-operation POST
    route."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": "http://snomed.info/sct", "code": "INVALID"},
                        {"system": "http://snomed.info/sct", "code": "44054006"},
                    ]
                },
            }
        ],
    }
    batch_resp = fhir_client.post("/fhir", json=_batch_bundle([{
        "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
        "resource": body,
    }]))
    per_op_resp = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    assert batch_resp.status_code == 200
    assert per_op_resp.status_code == 200
    batch_entry = batch_resp.json()["entry"][0]
    assert batch_entry["response"]["status"] == "200"
    assert batch_entry["resource"] == per_op_resp.json()


def test_e111_batch_vs_per_op_lookup_coding_param_parity(fhir_client):
    """L11: batch $lookup with `coding` parameter (alternative encoding)
    should return byte-exact identical body as per-operation POST."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": "http://snomed.info/sct",
                    "code": "44054006",
                },
            }
        ],
    }
    batch_resp = fhir_client.post("/fhir", json=_batch_bundle([{
        "request": {"method": "POST", "url": "CodeSystem/$lookup"},
        "resource": body,
    }]))
    per_op_resp = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert batch_resp.status_code == 200
    assert per_op_resp.status_code == 200
    batch_entry = batch_resp.json()["entry"][0]
    assert batch_entry["response"]["status"] == "200"
    assert batch_entry["resource"] == per_op_resp.json()


# =============================================================================
# L12 — Empty / degenerate batch edge cases
# §3.1.0.11.1: "Bundle.entry may be empty" — empty batch returns empty
# batch-response. Verify lateral variants of degeneracy.
# =============================================================================


def test_e120_batch_with_one_valid_one_empty_resource_post(fhir_client):
    """L12: POST $lookup entry with an EMPTY Parameters body (no params).
    Per-entry isolation: this entry gets 4xx (missing system+code), the
    other entry succeeds."""
    entries = [
        _lookup_post_entry("http://snomed.info/sct", "44054006"),
        {
            "request": {"method": "POST", "url": "CodeSystem/$lookup"},
            "resource": {"resourceType": "Parameters", "parameter": []},
        },
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    assert body["entry"][0]["response"]["status"] == "200"
    empty_status = body["entry"][1]["response"]["status"]
    assert empty_status.startswith("4"), \
        f"empty-Parameters entry should be 4xx, got {empty_status}"


def test_e121_batch_with_entry_missing_resource_on_post(fhir_client):
    """L12: POST entry with NO resource field at all. Per the impl
    (_process_batch_entry), this is a 400 'POST entry requires a resource'."""
    entries = [
        {
            "request": {"method": "POST", "url": "CodeSystem/$lookup"},
            # No "resource" field.
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "400"


def test_e122_batch_with_null_resource_on_post(fhir_client):
    """L12: POST entry with `"resource": null`. The impl checks
    `body_resource is None` — null should trigger the 'requires resource'
    error path (4xx), NOT a 5xx."""
    entries = [
        {
            "request": {"method": "POST", "url": "CodeSystem/$lookup"},
            "resource": None,
        }
    ]
    resp = fhir_client.post("/fhir", json=_batch_bundle(entries))
    assert resp.status_code == 200
    body = resp.json()
    entry = body["entry"][0]
    status = entry["response"]["status"]
    assert status.startswith("4"), \
        f"null-resource entry should be 4xx, got {status}"
