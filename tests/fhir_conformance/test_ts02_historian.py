"""HISTORIAN iteration TS-02 — pattern-match SKEPTIC fixes against v0.0.1 bug patterns.

Source: https://build.fhir.org/terminology-service.html#summary §4.7.1.2

HISTORIAN lens:
1. Re-test SKEPTIC's 9 fixes for completeness (esp. silent-fallback shapes).
2. Pattern-match the new code (route registrations, param wiring, error handlers)
   against v0.0.1 review bug patterns (B1, B2/B3, B6, A1, A2) and the
   HCPCS URI drift pattern (QA-012) and the docstring-vs-implementation
   drift pattern (QA-007).
3. Verify the new RequestValidationError handler is narrowly scoped and
   correctly distinguishes missing-required vs wrong-type.
4. Verify every wrapped operation handler actually produces XML when asked
   (not just one or two).
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Pattern: A1 silent-wrong-answer — POST $lookup / $validate-code with `coding`
# parameter (spec-allowed alternative to system+code) silently rejected.
#
# Spec (https://hl7.org/fhir/R4/codesystem-operation-lookup.html):
#   "In addition, the 'coding' parameter allows a complete coding to be supplied
#    rather than the separate system and code parameters."
# Spec (https://hl7.org/fhir/R4/codesystem-operation-validate-code.html):
#   "When invoking this operation, a client SHALL provide one (and only one) of
#    the parameters (code+system, coding, or codeableConcept)."
#
# Pre-TS-02 SKEPTIC: _parse_parameters in apps/fhir_api.py extracts only
# valueString/valueUri/valueCode/valueInteger/valueBoolean — NOT valueCoding.
# Result: POST $lookup with coding → falls through to "system and code are
# required." rejection. This is silent-wrong-answer: a spec-compliant client
# request is rejected with a 400 that says the request is missing required
# params when in fact it provided the spec-allowed alternative encoding.
# ---------------------------------------------------------------------------


def test_h01_lookup_post_with_coding_param_accepted(fhir_client):
    """HISTORIAN: POST $lookup with `coding` parameter (Coding type) MUST be
    accepted as the spec-allowed alternative to system+code.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    Quote: 'In addition, the 'coding' parameter allows a complete coding to
    be supplied rather than the separate system and code parameters.'
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": "http://snomed.info/sct",
                        "code": "73211009",
                    },
                }
            ],
        },
    )
    # Conformant: 200 with Parameters resource containing the looked-up code.
    # Non-conformant: 400 "system and code are required." (silent reject).
    assert r.status_code != 400, (
        f"POST $lookup with coding parameter rejected: {r.json()}"
    )


def test_h02_validate_code_post_with_coding_param_accepted(fhir_client):
    """HISTORIAN: POST $validate-code with `coding` parameter MUST be accepted.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
    Quote: 'a client SHALL provide one (and only one) of the parameters
    (code+system, coding, or codeableConcept). Other parameters (including
    version and display) are optional.'
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": "http://snomed.info/sct",
                        "code": "73211009",
                    },
                }
            ],
        },
    )
    assert r.status_code != 400, (
        f"POST $validate-code with coding parameter rejected: {r.json()}"
    )


# ---------------------------------------------------------------------------
# Pattern: A1 — POST boolean parsing. Spec-allowed boolean params must use
# `valueBoolean` correctly in Parameters bodies, not be misinterpreted due to
# str(True)=='True' issues.
# ---------------------------------------------------------------------------


def test_h03_validate_code_abstract_param_accepted(fhir_client):
    """HISTORIAN: CodeSystem/$validate-code accepts the `abstract` boolean
    parameter (per spec table).

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
    Quote: 'abstract: 0..1 boolean — If this parameter has a value of true,
    the client is stating that the validation is being performed in a context
    where a concept designated as 'abstract' is appropriate/allowed to be used,
    and the server should regard abstract codes as valid.'

    Pattern match: TS-01 A1 was about POST boolean parsing for `$extract`.
    The newly-wired $validate-code declares `display` (QA-018) but the spec
    table also lists `abstract`. Verify the handler accepts it on GET.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "73211009"),
            ("abstract", "true"),
        ],
    )
    # Conformant: 200 with Parameters; non-conformant: 422 FastAPI default.
    assert r.status_code == 200, (
        f"abstract param rejected with status {r.status_code}: {r.text[:300]}"
    )


# ---------------------------------------------------------------------------
# Pattern: A2 — hardcoded port / host in defaults.
# Pre-TS-01: build_capability_statement used hardcoded DEFAULT_PORT in URL.
# TS-01 fix: handler passes env-var-derived base_url.
# TS-02 SKEPTIC: extended build_capability_statement and added module-level
# constants. Verify the new code didn't reintroduce the hardcoded-port pattern
# in the constants or the default.
# ---------------------------------------------------------------------------


def test_h04_capability_statement_constants_have_no_hardcoded_port():
    """HISTORIAN: the new OPDEF_* constants in responses.py MUST be canonical
    HL7 URIs (no port), not server-local URLs.

    Spec: https://hl7.org/fhir/R4/capabilitystatement.html#definition
    Pattern match: TS-01 A2 fix made /fhir/metadata read MEDTERM4DS_API_HOST
    and MEDTERM4DS_FHIR_API_PORT. SKEPTIC's QA-016 added canonical URIs but
    if any include ':port' or 'localhost' it would be the same drift.
    """
    from medterm4ds.engines.fhir.responses import (
        OPDEF_LOOKUP,
        OPDEF_CS_VALIDATE_CODE,
        OPDEF_SUBSUMES,
        OPDEF_CLOSURE,
        OPDEF_EXPAND,
        OPDEF_VS_VALIDATE_CODE,
        OPDEF_TRANSLATE,
    )

    for label, uri in [
        ("OPDEF_LOOKUP", OPDEF_LOOKUP),
        ("OPDEF_CS_VALIDATE_CODE", OPDEF_CS_VALIDATE_CODE),
        ("OPDEF_SUBSUMES", OPDEF_SUBSUMES),
        ("OPDEF_CLOSURE", OPDEF_CLOSURE),
        ("OPDEF_EXPAND", OPDEF_EXPAND),
        ("OPDEF_VS_VALIDATE_CODE", OPDEF_VS_VALIDATE_CODE),
        ("OPDEF_TRANSLATE", OPDEF_TRANSLATE),
    ]:
        # Canonical HL7 URIs start with http://hl7.org/fhir/OperationDefinition/
        assert uri.startswith(
            "http://hl7.org/fhir/OperationDefinition/"
        ), f"{label}={uri!r} is not a canonical HL7 OperationDefinition URI"
        # No port component
        assert ":80" not in uri and ":800" not in uri and ":443" not in uri, (
            f"{label}={uri!r} contains a hardcoded port"
        )
        # No localhost
        assert "localhost" not in uri and "127.0.0.1" not in uri, (
            f"{label}={uri!r} contains localhost"
        )


# ---------------------------------------------------------------------------
# Pattern: B1 — silent `except ImportError: pass`.
# Verify no new silent ImportError in the new SKEPTIC code paths.
# ---------------------------------------------------------------------------


def test_h05_no_silent_importerror_in_fhir_modules():
    """HISTORIAN: scan the FHIR API + responses modules for the B1 pattern
    `except ImportError: pass`.

    Pattern match: TS-01 B1 fix in medterm4ds/__init__.py required Warning-level
    logging on optional-import failures. The new TS-02 SKEPTIC code (handler
    wrapping, exception handler, canonical URI constants) should not introduce
    any new silent ImportError.
    """
    import re
    from pathlib import Path

    fhir_api = Path(__file__).resolve().parents[2] / "src/medterm4ds/apps/fhir_api.py"
    responses = (
        Path(__file__).resolve().parents[2]
        / "src/medterm4ds/engines/fhir/responses.py"
    )
    pattern = re.compile(r"except\s+ImportError\s*:\s*\s*pass")

    for f in [fhir_api, responses]:
        text = f.read_text()
        assert not pattern.search(text), (
            f"B1 regression: silent 'except ImportError: pass' found in {f}"
        )


# ---------------------------------------------------------------------------
# Pattern: B2/B3 — broad except Exception. Verify the new
# `_fhir_validation_exception_handler` is narrowly scoped to
# RequestValidationError and does NOT swallow other exceptions.
# ---------------------------------------------------------------------------


def test_h06_validation_exception_handler_narrowly_scoped():
    """HISTORIAN: the @app.exception_handler registered by TS-02 SKEPTIC
    (QA-020) MUST be narrowly scoped to RequestValidationError.

    Spec: https://hl7.org/fhir/R4/http.html — OperationOutcome on 4xx/5xx.
    Pattern match: TS-01 B2/B3 fix required narrow exception types. If the
    new exception handler were registered for `Exception` it would swallow
    programming bugs (TypeError, AttributeError) as OperationOutcomes —
    silent-wrong-answer.
    """
    import inspect

    from medterm4ds.apps import fhir_api as mod

    src = inspect.getsource(mod)
    # Look for the decorator registration.
    # The handler must be registered as `@app.exception_handler(RequestValidationError)`.
    assert "@app.exception_handler(RequestValidationError)" in src, (
        "RequestValidationError handler not registered"
    )
    # Anti-pattern: registering for bare Exception would swallow programming bugs.
    assert "@app.exception_handler(Exception)" not in src, (
        "B2 regression: exception handler registered for bare Exception"
    )


# ---------------------------------------------------------------------------
# Pattern: B6 — silent fallback. Verify the new instance-level routes
# (ValueSet/{id}/$expand, ValueSet/{id}/$validate-code, ConceptMap/{id}/$translate)
# do NOT silently fall back to type-level behavior. They MUST return 404 for
# unknown ids, not silently run the type-level operation.
# ---------------------------------------------------------------------------


def test_h07_expand_instance_does_not_silently_run_type_level(fhir_client):
    """HISTORIAN: GET /fhir/ValueSet/{id}/$expand for an unknown id MUST
    return 404 OperationOutcome, NOT silently run a type-level expansion
    (which would be a B6-style silent fallback — wrong answer, no error).

    Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
    """
    r = fhir_client.get(
        "/fhir/ValueSet/unknown-id/$expand",
        params={"filter": "diabetes"},
    )
    assert r.status_code == 404, (
        f"Instance-level $expand returned {r.status_code}, expected 404. "
        f"Body: {r.text[:300]}"
    )
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"
    # NOT a type-level expansion result (which would be a ValueSet).
    assert body["resourceType"] != "ValueSet"


def test_h08_vs_validate_instance_does_not_silently_run_type_level(fhir_client):
    """HISTORIAN: GET /fhir/ValueSet/{id}/$validate-code for an unknown id
    MUST return 404 OperationOutcome.

    Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
    """
    r = fhir_client.get(
        "/fhir/ValueSet/unknown-id/$validate-code",
        params={
            "system": "http://snomed.info/sct",
            "code": "73211009",
        },
    )
    assert r.status_code == 404, (
        f"Instance-level $validate-code returned {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"


def test_h09_translate_instance_does_not_silently_run_type_level(fhir_client):
    """HISTORIAN: GET /fhir/ConceptMap/{id}/$translate for an unknown id
    MUST return 404 OperationOutcome.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/unknown-id/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "73211009",
        },
    )
    assert r.status_code == 404, (
        f"Instance-level $translate returned {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"


# ---------------------------------------------------------------------------
# Pattern: HCPCS URI drift (TS-01 QA-012) — canonical-registry cross-check.
# Verify the new OPDEF_* canonical URIs match the canonical OperationDefinition
# URLs on hl7.org for each operation (not just one or two).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "constant_name, expected_canonical",
    [
        ("OPDEF_LOOKUP", "http://hl7.org/fhir/OperationDefinition/CodeSystem-lookup"),
        (
            "OPDEF_CS_VALIDATE_CODE",
            "http://hl7.org/fhir/OperationDefinition/CodeSystem-validate-code",
        ),
        ("OPDEF_SUBSUMES", "http://hl7.org/fhir/OperationDefinition/CodeSystem-subsumes"),
        ("OPDEF_CLOSURE", "http://hl7.org/fhir/OperationDefinition/CodeSystem-closure"),
        ("OPDEF_EXPAND", "http://hl7.org/fhir/OperationDefinition/ValueSet-expand"),
        (
            "OPDEF_VS_VALIDATE_CODE",
            "http://hl7.org/fhir/OperationDefinition/ValueSet-validate-code",
        ),
        (
            "OPDEF_TRANSLATE",
            "http://hl7.org/fhir/OperationDefinition/ConceptMap-translate",
        ),
    ],
)
def test_h10_canonical_operation_uris_match_hl7_registry(constant_name, expected_canonical):
    """HISTORIAN: each OPDEF_* constant must equal its canonical HL7 registry URI.

    Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html (and siblings)
    The CodeSystem-$lookup spec page explicitly states:
        'The official URL for this operation definition is
         http://hl7.org/fhir/OperationDefinition/CodeSystem-lookup'
    Each operation's spec page has an analogous 'official URL' block. Cross-check
    each SKEPTIC-introduced constant against the published canonical.

    Pattern match: TS-01 TERMINOLOGIST QA-012 found HCPCS URI drift where the
    THO CodeSystem resource URL was used instead of the canonical system URI.
    The same shape would apply if any OPDEF_* used the operation's *HTML page*
    URL (https://hl7.org/fhir/R4/codesystem-operation-lookup.html) instead of
    the canonical OperationDefinition URI (http://hl7.org/fhir/OperationDefinition/...).
    """
    import medterm4ds.engines.fhir.responses as mod

    actual = getattr(mod, constant_name)
    assert actual == expected_canonical, (
        f"{constant_name} drift: got {actual!r}, expected {expected_canonical!r}"
    )


# ---------------------------------------------------------------------------
# Pattern: Documentation-vs-implementation drift (TS-01 HISTORIAN QA-007).
# Read every docstring added in TS-02 fixes — does the body deliver what the
# docstring claims?
#
# Spot-check: the ValueSet/$validate-code handler docstring says the response
# is a Parameters resource with a `result` boolean. The body delivers that.
# Spot-check: the new instance-level routes' comments say they return 404
# OperationOutcome — verified above.
# ---------------------------------------------------------------------------


def test_h11_vs_validate_code_response_matches_docstring(fhir_client):
    """HISTORIAN: the ValueSet/$validate-code docstring promises 'Parameters
    body with `result` boolean and `display`'. Verify the body delivers that
    for a known-good code.

    Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$validate-code",
        params={
            "system": "http://snomed.info/sct",
            "code": "73211009",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resourceType"] == "Parameters"
    names = {p["name"] for p in body.get("parameter", [])}
    assert "result" in names, f"Missing 'result' parameter: {names}"


# ---------------------------------------------------------------------------
# Pattern: Carry-forward TS-01 EXPLORER QA-021 — operations honor
# Accept: application/fhir+xml. SKEPTIC wrapped 12 handlers in _fhir_response.
# Verify EVERY wrapped handler actually emits XML when asked — not just one.
# ---------------------------------------------------------------------------


_XML_ACCEPT = "application/fhir+xml"


@pytest.mark.parametrize(
    "method, url, payload, params",
    [
        # 1. GET /fhir/CodeSystem/$lookup
        ("GET", "/fhir/CodeSystem/$lookup", None,
         {"system": "http://snomed.info/sct", "code": "73211009"}),
        # 2. POST /fhir/CodeSystem/$lookup
        ("POST", "/fhir/CodeSystem/$lookup",
         {"resourceType": "Parameters", "parameter": [
             {"name": "system", "valueUri": "http://snomed.info/sct"},
             {"name": "code", "valueCode": "73211009"},
         ]}, None),
        # 3. GET /fhir/CodeSystem/$validate-code
        ("GET", "/fhir/CodeSystem/$validate-code", None,
         {"system": "http://snomed.info/sct", "code": "73211009"}),
        # 4. POST /fhir/CodeSystem/$validate-code
        ("POST", "/fhir/CodeSystem/$validate-code",
         {"resourceType": "Parameters", "parameter": [
             {"name": "system", "valueUri": "http://snomed.info/sct"},
             {"name": "code", "valueCode": "73211009"},
         ]}, None),
        # 5. GET /fhir/ValueSet/$validate-code
        ("GET", "/fhir/ValueSet/$validate-code", None,
         {"system": "http://snomed.info/sct", "code": "73211009"}),
        # 6. POST /fhir/ValueSet/$validate-code
        ("POST", "/fhir/ValueSet/$validate-code",
         {"resourceType": "Parameters", "parameter": [
             {"name": "system", "valueUri": "http://snomed.info/sct"},
             {"name": "code", "valueCode": "73211009"},
         ]}, None),
        # 7. GET /fhir/ConceptMap/$translate
        ("GET", "/fhir/ConceptMap/$translate", None,
         {"system": "http://snomed.info/sct", "code": "73211009"}),
        # 8. POST /fhir/ConceptMap/$translate
        ("POST", "/fhir/ConceptMap/$translate",
         {"resourceType": "Parameters", "parameter": [
             {"name": "system", "valueUri": "http://snomed.info/sct"},
             {"name": "code", "valueCode": "73211009"},
         ]}, None),
        # 9. GET /fhir/CodeSystem/$subsumes
        ("GET", "/fhir/CodeSystem/$subsumes", None,
         {"system": "http://snomed.info/sct", "codeA": "73211009", "codeB": "73211009"}),
        # 10. POST /fhir/CodeSystem/$subsumes
        ("POST", "/fhir/CodeSystem/$subsumes",
         {"resourceType": "Parameters", "parameter": [
             {"name": "system", "valueUri": "http://snomed.info/sct"},
             {"name": "codeA", "valueCode": "73211009"},
             {"name": "codeB", "valueCode": "73211009"},
         ]}, None),
        # 11. GET /fhir/ValueSet/$expand (filter mode)
        ("GET", "/fhir/ValueSet/$expand", None,
         {"filter": "diabetes", "system": "http://snomed.info/sct"}),
        # 12. POST /fhir/ValueSet/$expand (filter mode)
        ("POST", "/fhir/ValueSet/$expand",
         {"resourceType": "Parameters", "parameter": [
             {"name": "filter", "valueString": "diabetes"},
             {"name": "system", "valueUri": "http://snomed.info/sct"},
         ]}, None),
    ],
)
def test_h12_operation_accept_xml_returns_xml(fhir_client, method, url, payload, params):
    """HISTORIAN: every SKEPTIC-wrapped operation handler MUST honor
    Accept: application/fhir+xml. Test ALL 12 — not just one or two.

    Spec: https://hl7.org/fhir/R4/http.html#mime-type
    Pattern: TS-02 SKEPTIC QA-021 wrapped 12 handlers in _fhir_response. A
    single test on $lookup (test_s34) doesn't catch a regression on $subsumes
    or $expand.
    """
    headers = {"Accept": _XML_ACCEPT}
    if method == "GET":
        r = fhir_client.get(url, params=params, headers=headers)
    else:
        r = fhir_client.post(url, json=payload, headers=headers)
    assert r.status_code == 200, (
        f"{method} {url} returned {r.status_code}: {r.text[:200]}"
    )
    assert r.headers["content-type"].startswith("application/fhir+xml"), (
        f"{method} {url}: Content-Type={r.headers['content-type']}, "
        "expected application/fhir+xml"
    )
    # Body must be parseable XML containing a FHIR root element
    # (Parameters / ValueSet / Bundle — possibly namespace-prefixed).
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as exc:
        pytest.fail(f"{method} {url}: response is not valid XML: {exc}")
    # Strip XML namespace prefix if present (e.g. '{http://hl7.org/fhir}Parameters').
    tag = root.tag.rsplit("}", 1)[-1] if "}" in root.tag else root.tag
    assert tag in ("Parameters", "ValueSet", "Bundle"), (
        f"{method} {url}: unexpected XML root {root.tag!r}"
    )


# ---------------------------------------------------------------------------
# Pattern: ARCH-003 — instance-level POST routes.
# FHIR R4 §3.1.0.1.1 allows POST anywhere GET is allowed for operations.
# SKEPTIC registered instance-level POST only for $translate (ARCH-003 note).
# Verify the gap: instance-level POST $expand and $validate-code currently
# return 405 (gap) or 404 (acceptable). Document the gap.
# ---------------------------------------------------------------------------


def test_h13_instance_expand_post_route_status(fhir_client):
    """HISTORIAN: probe POST /fhir/ValueSet/{id}/$expand.

    FHIR R4 §3.1.0.1.1: 'Operations ... MAY be invoked using POST'. The
    instance-level $expand GET route exists; the POST route was NOT added
    (ARCH-003 note). Capture current behavior so the next iteration can
    decide: add POST, or document INTENT.
    """
    r = fhir_client.post(
        "/fhir/ValueSet/any-id/$expand",
        json={"resourceType": "Parameters", "parameter": []},
    )
    # Capture the current behavior. Either:
    # - 404 (instance-level POST wired, returns OperationOutcome) — preferred
    # - 405 (FastAPI default for missing POST) — gap
    # - 200 (silently ran type-level — silent fallback, B6) — BUG
    assert r.status_code != 200, (
        f"Instance POST $expand returned 200 — possible silent fallback: {r.text[:200]}"
    )


def test_h14_instance_validate_code_post_route_status(fhir_client):
    """HISTORIAN: probe POST /fhir/ValueSet/{id}/$validate-code."""
    r = fhir_client.post(
        "/fhir/ValueSet/any-id/$validate-code",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert r.status_code != 200, (
        f"Instance POST $validate-code returned 200 — possible silent fallback: "
        f"{r.text[:200]}"
    )


# ---------------------------------------------------------------------------
# Pattern: RequestValidationError handler correctness (TS-02 QA-020).
# The handler MUST correctly distinguish missing-required-param (FastAPI 422
# under Query(...)) from wrong-type-param (also 422). Both must produce
# OperationOutcome, NOT the FastAPI default `{'detail': [...]}` body.
# ---------------------------------------------------------------------------


def test_h15_missing_required_param_returns_operationoutcome(fhir_client):
    """HISTORIAN: missing required `system` on $lookup MUST return a FHIR
    OperationOutcome (not FastAPI's default 422 `{'detail': [...]}` body).

    Spec: https://hl7.org/fhir/R4/http.html — 'The OperationOutcome may be
    returned with any HTTP 4xx or 5xx response.'
    """
    r = fhir_client.get("/fhir/CodeSystem/$lookup", params={"code": "73211009"})
    assert r.status_code in (400, 422), (
        f"Expected 400/422, got {r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"Expected OperationOutcome, got: {body}"
    )


def test_h16_wrong_type_param_returns_operationoutcome(fhir_client):
    """HISTORIAN: wrong-type param (count=abc on $expand) MUST return a FHIR
    OperationOutcome, not FastAPI's default `{'detail': [...]}` body.

    Spec: https://hl7.org/fhir/R4/http.html
    Pattern match: $expand declares `count: int = Query(20, ge=1, le=1000)`.
    A non-integer triggers FastAPI's RequestValidationError.
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params={"filter": "diabetes", "count": "not-an-integer"},
    )
    assert r.status_code in (400, 422), (
        f"Expected 400/422, got {r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"Expected OperationOutcome, got: {body}"
    )


# ---------------------------------------------------------------------------
# Pattern: instance-level POST $translate (the ONE instance POST route SKEPTIC
# added) must not silently fall back to type-level. Verify it returns 404 for
# an unknown id, not a successful translation.
# ---------------------------------------------------------------------------


def test_h17_translate_instance_post_does_not_silently_translate(fhir_client):
    """HISTORIAN: POST /fhir/ConceptMap/{id}/$translate for an unknown id
    MUST return 404 OperationOutcome, not run type-level $translate and
    return a successful translation (silent fallback / B6).
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/unknown-id/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "73211009"},
            ],
        },
    )
    assert r.status_code == 404, (
        f"Instance POST $translate returned {r.status_code}, expected 404: "
        f"{r.text[:300]}"
    )
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"


# ---------------------------------------------------------------------------
# Pattern: CapabilityStatement now advertises ValueSet/$validate-code (QA-017).
# Verify the advertisement is wired AND that the canonical URI is the canonical
# HL7 one (cross-checking the QA-016 fix didn't accidentally regress QA-017).
# ---------------------------------------------------------------------------


def test_h18_capabilitystatement_advertises_vs_validate_code_with_canonical_uri(
    fhir_client,
):
    """HISTORIAN: CapabilityStatement ValueSet.operation[] MUST include
    'validate-code' with canonical URI.

    Spec: https://build.fhir.org/terminology-service.html#summary
    """
    r = fhir_client.get("/fhir/metadata")
    cs = r.json()
    vs_resource = next(
        (r for r in cs["rest"][0]["resource"] if r["type"] == "ValueSet"), None
    )
    assert vs_resource is not None
    ops = {op["name"]: op["definition"] for op in vs_resource.get("operation", [])}
    assert "validate-code" in ops, (
        f"ValueSet/$validate-code not advertised: {ops}"
    )
    assert ops["validate-code"] == (
        "http://hl7.org/fhir/OperationDefinition/ValueSet-validate-code"
    )


# ---------------------------------------------------------------------------
# Pattern: documentation-vs-implementation drift — verify every operation
# advertised in CapabilityStatement is actually reachable (not just advertised).
# This guards against the spec-table coverage drift pattern SKEPTIC caught.
# ---------------------------------------------------------------------------


def test_h19_every_advertised_operation_is_reachable(fhir_client):
    """HISTORIAN: every operation in CapabilityStatement.rest[].resource[].operation[]
    MUST be reachable on the wire. SKEPTIC's TS-02 fix added the missing
    ValueSet/$validate-code route; this probe guards against future regression
    where an operation is advertised but the route is missing (QA-013 pattern).

    Spec: https://build.fhir.org/terminology-service.html#summary

    Note: $closure is a POST-only operation (no GET route registered); test it
    via POST. All other advertised operations are tested via GET.
    """
    cs = fhir_client.get("/fhir/metadata").json()
    # For each advertised operation, hit the type-level endpoint with a smoke
    # request. The response must NOT be 404 (catch-all) — it must be 200, 400,
    # or 422 (any explicit handler response).
    smoke_get_params = {
        "$lookup": {"system": "http://snomed.info/sct", "code": "73211009"},
        "$validate-code": {"system": "http://snomed.info/sct", "code": "73211009"},
        "$subsumes": {
            "system": "http://snomed.info/sct",
            "codeA": "73211009",
            "codeB": "73211009",
        },
        "$expand": {"filter": "diabetes"},
        "$translate": {"system": "http://snomed.info/sct", "code": "73211009"},
    }
    post_only = {"$closure"}

    for res in cs["rest"][0]["resource"]:
        rtype = res["type"]
        for op in res.get("operation", []):
            op_name = op["name"]
            url = f"/fhir/{rtype}/${op_name}"
            if f"${op_name}" in post_only:
                # Probe via POST.
                r = fhir_client.post(
                    url,
                    json={
                        "resourceType": "Parameters",
                        "parameter": [{"name": "name", "valueString": "probe"}],
                    },
                )
            else:
                params = smoke_get_params.get(f"${op_name}", {})
                r = fhir_client.get(url, params=params)
            # 404 means the route fell through to the catch-all — silent gap.
            assert r.status_code != 404, (
                f"Advertised operation {url} returned 404 — route missing "
                f"(spec-table coverage drift, QA-013 pattern). Body: {r.text[:200]}"
            )


# ---------------------------------------------------------------------------
# Pattern: silent fallback in instance-level POST $translate body handling.
# The handler signature is `body: dict[str, Any] | None = None` (optional).
# A POST with NO body should not crash with 500; it should return 404 (the
# instance-level behavior for any id).
# ---------------------------------------------------------------------------


def test_h20_translate_instance_post_no_body_does_not_500(fhir_client):
    """HISTORIAN: POST /fhir/ConceptMap/{id}/$translate with no body MUST
    return 404, not 500.

    Pattern match: the handler signature `body: dict | None = None` is
    permissive; if the body parsing were fragile, a missing body could 500.
    """
    r = fhir_client.post("/fhir/ConceptMap/unknown-id/$translate")
    assert r.status_code != 500, (
        f"Instance POST $translate with no body returned 500: {r.text[:200]}"
    )
    assert r.status_code == 404
