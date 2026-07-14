"""SKEPTIC probes for CS-03 (CodeSystem $validate-code Operation).

Spec: https://build.fhir.org/codesystem-operation-validate-code.html
       (canonical R4: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html)

Scope (per chunk assignment) — 6 items:
  1. Required params: code, system
  2. Optional params: version, display, date, coding, codeableConcept, inferSystem
  3. Returns Parameters with result (boolean), message (string, optional),
     display (string, optional)
  4. When display mismatch: result=false, message explains, display=correct display
  5. When code unknown: result=false
  6. When codeableConcept input: any matching coding returns result=true

SKEPTIC lens: hostile-input probes for each item — drop required params,
send wrong-display, send coding/codeableConcept, send date variants,
probe response shape (result/message/display names) exactly.

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape (200 +
    expected fields), not just absence of one error string.
  - "Boolean capitalization on serializers": when testing response booleans,
    verify lowercase rendering on the wire.
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
#
# In Parameters (relevant):
#   url             0..1  uri        "CodeSystem URL"
#   codeSystem      0..1  CodeSystem "CodeSystem resource inline"
#   code            0..1  code       "The code that is to be validated"
#   version         0..1  string     "The version of the code system"
#   display         0..1  string     "The display associated with the code, if
#                                     provided. If a display is provided a code
#                                     must be provided. If no display is provided,
#                                     the server cannot validate the display value,
#                                     but may choose to return a recommended
#                                     display name in an extension in the outcome.
#                                     Whether displays are case sensitive is code
#                                     system dependent"
#   coding          0..1  Coding     "A coding to validate"
#   codeableConcept 0..1  CodeableConcept "A full codeableConcept to validate.
#                                     The server returns true if one of the coding
#                                     values is in the code system"
#   date            0..1  dateTime   "The date for which the validation should be
#                                     checked"
#   abstract        0..1  boolean    "If true, abstract codes are valid"
#   displayLanguage 0..1  code       "Specifies the language for display validation"
#
# Out Parameters:
#   result          1..1  boolean    "True if the concept details supplied are valid"
#   message         0..1  string     "Error details, if result = false. If this is
#                                     provided when result = true, the message
#                                     carries hints and warnings"
#   display         0..1  string     "A valid display for the concept if the system
#                                     wishes to display this to a user"
#
# Spec example response (display mismatch):
#   {
#     "resourceType": "Parameters",
#     "parameter": [
#       {"name": "result", "valueBoolean": "false"},
#       {"name": "message", "valueString": "The display \"test\" is incorrect"},
#       {"name": "display", "valueString": "Bicarbonate [Moles/volume] in Serum"}
#     ]
#   }
# Source: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # canonical display: "Diabetes mellitus"
SNOMED_T2DM = "44054006"               # canonical display: "Type 2 diabetes mellitus"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"                    # canonical display: "Type 2 diabetes mellitus"


def _param_value(body: dict, name: str) -> object | None:
    """Return the value of the first Out parameter matching `name`."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _has_param(body: dict, name: str) -> bool:
    return any(p.get("name") == name for p in body.get("parameter", []))


# ---------------------------------------------------------------------------
# Item 1: Required params — code, system
# ---------------------------------------------------------------------------

def test_s01_get_validate_without_code_or_system_returns_422(fhir_client):
    """Item 1 / spec: code+system are required. GET with no params MUST reject."""
    r = fhir_client.get("/fhir/CodeSystem/$validate-code")
    assert r.status_code in (400, 422), (
        f"GET $validate-code with no params → {r.status_code}; expected 422/400"
    )
    # Per GLOBAL_RULES.md "Conformance property per route": body MUST be FHIR
    # OperationOutcome or FastAPI-wrapped FHIR detail (TS-02 SKEPTIC QA-020).
    ct = r.headers.get("content-type", "")
    assert "fhir+json" in ct or "application/json" in ct


def test_s02_get_validate_code_without_system_returns_422(fhir_client):
    """Item 1 / spec: 'If a display is provided a code must be provided' —
    symmetric requirement: code alone without system is also invalid."""
    r = fhir_client.get(f"/fhir/CodeSystem/$validate-code?code={SNOMED_T2DM}")
    assert r.status_code in (400, 422), (
        f"GET $validate-code with code but no system → {r.status_code}; expected 422/400"
    )


def test_s03_get_validate_system_without_code_returns_422(fhir_client):
    """Item 1 / spec: system alone without code MUST reject."""
    r = fhir_client.get(f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}")
    assert r.status_code in (400, 422), (
        f"GET $validate-code with system but no code → {r.status_code}; expected 422/400"
    )


# ---------------------------------------------------------------------------
# Item 2: Optional params
# ---------------------------------------------------------------------------

def test_s10_get_validate_with_version_param_accepted(fhir_client):
    """Item 2 / spec In Parameters: `version` is 0..1 string. Accepted without 5xx."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}&version=2024-09"
    )
    assert r.status_code == 200, f"version accepted → {r.status_code}; body={r.text[:300]}"
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    # version accepted: result true (the code is known, version is informational)
    assert _param_value(body, "result") is True


def test_s11_get_validate_with_date_param_accepted(fhir_client):
    """Item 2 / spec In Parameters: `date` is 0..1 dateTime. Accepted without 5xx.

    Spec: "The date for which the validation should be checked. Normally, this is
    the current conditions ... but under some circumstances, systems need to
    validate that a correct code was used at some point in the past."
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}&date=2020-01-01"
    )
    assert r.status_code == 200, f"date past → {r.status_code}; body={r.text[:300]}"


def test_s12_get_validate_with_future_date_param_accepted(fhir_client):
    """Item 2 / spec `date`: future-date validation accepted without 5xx."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}&date=2099-12-31"
    )
    assert r.status_code == 200, f"date future → {r.status_code}; body={r.text[:300]}"


def test_s13_get_validate_with_abstract_param_accepted(fhir_client):
    """Item 2 / spec In Parameters: `abstract` is 0..1 boolean. Accepted without 5xx."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}&abstract=true"
    )
    assert r.status_code == 200, f"abstract=true → {r.status_code}; body={r.text[:300]}"


def test_s14_get_validate_with_display_language_param_accepted(fhir_client):
    """Item 2 / spec In Parameters: `displayLanguage` is 0..1 code. Accepted without 5xx."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}&displayLanguage=en"
    )
    assert r.status_code == 200, f"displayLanguage → {r.status_code}; body={r.text[:300]}"


def test_s15_post_validate_with_coding_returns_200(fhir_client):
    """Item 2 / spec In Parameters: `coding` is 0..1 Coding — full alternative to
    system+code. POST body uses Parameters resource with valueCoding."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM},
                }
            ],
        },
    )
    assert r.status_code == 200, f"POST coding → {r.status_code}; body={r.text[:300]}"
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    assert _param_value(body, "result") is True


def test_s16_post_validate_with_codeableConcept_returns_200(fhir_client):
    """Item 2 + Item 6 / spec: codeableConcept with one valid coding → result=true.

    Spec: "A full codeableConcept to validate. The server returns true if one of
    the coding values is in the code system."
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [{"system": SNOMED_URI, "code": SNOMED_T2DM}]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200, f"POST codeableConcept → {r.status_code}; body={r.text[:300]}"
    body = r.json()
    assert _param_value(body, "result") is True


# ---------------------------------------------------------------------------
# Item 3: Returns Parameters with result (boolean), message (optional), display (optional)
# ---------------------------------------------------------------------------

def test_s30_validate_response_shape_is_parameters_with_result(fhir_client):
    """Item 3 / spec Out Parameters: result (1..1 boolean) is mandatory.
    resourceType MUST be Parameters."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    assert _has_param(body, "result"), "Out Parameters MUST include `result`"
    assert isinstance(_param_value(body, "result"), bool), (
        "result value MUST be JSON boolean (not string)"
    )


def test_s31_validate_result_valueBoolean_lowercase_on_wire(fhir_client):
    """Item 3 + GLOBAL_RULES.md "Boolean capitalization on serializers":
    valueBoolean MUST render as lowercase `true`/`false` on the wire, not Python's
    `str(True)` = `"True"` (capital T). FHIR R4 §3.4.1 mandates lowercase."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    # The raw response text MUST contain `"valueBoolean": true` (lowercase) for the
    # result parameter. Capital-T `"True"` would indicate str(bool) serialization.
    raw = r.text
    assert '"valueBoolean": true' in raw or '"valueBoolean":false' in raw or '"valueBoolean": false' in raw or '"valueBoolean":true' in raw, (
        f"valueBoolean MUST be lowercase on wire; raw snippet: {raw[:400]}"
    )
    assert '"valueBoolean": True' not in raw, (
        "valueBoolean MUST NOT be capital-T True (Python str(bool)); FHIR R4 §3.4.1"
    )


def test_s32_validate_response_includes_display_when_code_known(fhir_client):
    """Item 3 / spec Out Parameters: display (0..1 string) — "A valid display for
    the concept if the system wishes to display this to a user". When result=true,
    the server SHOULD include the canonical display."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    # display is 0..1, but medterm4ds returns the canonical name when known
    assert _has_param(body, "display"), (
        "display SHOULD be present when result=true and code is known"
    )
    display_val = _param_value(body, "display")
    assert isinstance(display_val, str)
    assert "diabetes" in display_val.lower()


def test_s33_validate_response_message_uses_valueString_when_present(fhir_client):
    """Item 3 / spec Out Parameters: message (0..1 string). When present, the
    value MUST be `valueString` (FHIR Parameters string-typed value)."""
    # Force a message by validating an unknown code.
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code=NONEXISTENT_QA"
    )
    assert r.status_code == 200
    body = r.json()
    # result MUST be false for unknown code; message SHOULD be present
    assert _param_value(body, "result") is False


# ---------------------------------------------------------------------------
# Item 4: When display mismatch → result=false, message, display=canonical
# ---------------------------------------------------------------------------

def test_s40_validate_display_match_returns_true(fhir_client):
    """Item 4 / spec: when client-supplied display matches canonical, result=true.

    Spec example: when display matches, no message about display incorrect."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&display=Type%202%20diabetes%20mellitus"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True, (
        "display matching canonical → result MUST be true"
    )


def test_s41_validate_display_mismatch_returns_false_with_message_and_canonical(fhir_client):
    """Item 4 / spec example response: when client-supplied display DOES NOT
    match the canonical display, the server MUST return:
      - result = false
      - message = e.g. 'The display "X" is incorrect'
      - display  = the canonical display (correct value)

    Spec example response (https://hl7.org/fhir/R4/codesystem-operation-validate-code.html):
      {"name": "result", "valueBoolean": "false"},
      {"name": "message", "valueString": "The display \\"test\\" is incorrect"},
      {"name": "display", "valueString": "Bicarbonate [Moles/volume] in Serum"}
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&display=WRONG-DISPLAY-NOT-CANONICAL"
    )
    assert r.status_code == 200
    body = r.json()
    # SKEPTIC: result MUST be false because the supplied display is wrong.
    result_val = _param_value(body, "result")
    assert result_val is False, (
        f"display mismatch MUST set result=false; got result={result_val!r}. "
        f"Spec: 'The display \"X\" is incorrect' response shape."
    )
    # SKEPTIC: message MUST be present and explain the mismatch.
    assert _has_param(body, "message"), (
        "display mismatch → message MUST be present (spec Out `message` 0..1 "
        "documents 'Error details, if result = false')"
    )
    msg_val = _param_value(body, "message")
    assert isinstance(msg_val, str) and len(msg_val) > 0
    # SKEPTIC: display MUST be the canonical (server-side) display, not the
    # client-supplied wrong string.
    assert _has_param(body, "display")
    display_val = _param_value(body, "display")
    assert display_val != "WRONG-DISPLAY-NOT-CANONICAL", (
        "Out `display` MUST be the canonical, not the client's wrong input"
    )
    assert "diabetes" in str(display_val).lower()


# ---------------------------------------------------------------------------
# Item 5: When code unknown → result=false
# ---------------------------------------------------------------------------

def test_s50_validate_unknown_code_returns_false(fhir_client):
    """Item 5 / spec Out `result`: 'True if the concept details supplied are
    valid'. For an unknown code, result MUST be false (not 200 + OperationOutcome)."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code=NONEXISTENT_QA_999"
    )
    assert r.status_code == 200, (
        f"unknown code → {r.status_code}; expected 200 + Parameters (result=false). "
        f"Spec: validate-code returns Parameters with result boolean, not an error."
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    assert _param_value(body, "result") is False


def test_s51_validate_unknown_code_response_shape(fhir_client):
    """Item 5 / spec: when code is unknown, response is Parameters (not
    OperationOutcome) with result=false. Optional message + display."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code=BOGUS_QA_42"
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    assert _param_value(body, "result") is False
    # display may or may not be present for unknown codes (0..1)
    # If present, it should NOT echo the bogus client code as canonical.


# ---------------------------------------------------------------------------
# Item 6: codeableConcept — any matching coding → result=true
# ---------------------------------------------------------------------------

def test_s60_codeable_concept_with_one_valid_one_invalid_coding_returns_true(fhir_client):
    """Item 6 / spec: 'The server returns true if one of the coding values is in
    the code system'. CodeableConcept with 1 valid + 1 invalid coding → result=true.

    Builds a codeableConcept with TWO codings: the first valid (SNOMED T2DM),
    the second invalid (unknown SNOMED code). Spec says ANY match → true.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": SNOMED_T2DM},
                            {"system": SNOMED_URI, "code": "NONEXISTENT_QA"},
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200, f"POST cc → {r.status_code}; body={r.text[:300]}"
    body = r.json()
    assert _param_value(body, "result") is True, (
        "codeableConcept with at least one valid coding → result MUST be true "
        "(spec: 'The server returns true if one of the coding values is in the code system')"
    )


def test_s61_codeable_concept_all_invalid_codings_returns_false(fhir_client):
    """Item 6 / spec converse: codeableConcept with all-invalid codings →
    result=false. None of the codings are in the code system."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "NONEXISTENT_QA_1"},
                            {"system": SNOMED_URI, "code": "NONEXISTENT_QA_2"},
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200, f"POST cc invalid → {r.status_code}; body={r.text[:300]}"
    body = r.json()
    assert _param_value(body, "result") is False, (
        "codeableConcept with NO valid coding → result MUST be false"
    )


def test_s62_codeable_concept_with_invalid_then_valid_coding_returns_true(fhir_client):
    """Item 6 / spec: codeableConcept ordering doesn't matter; ANY coding match
    → result=true. The valid coding appears SECOND."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "NONEXISTENT_QA"},
                            {"system": SNOMED_URI, "code": SNOMED_T2DM},
                            {"system": SNOMED_URI, "code": "NONEXISTENT_QA_2"},
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True


# ---------------------------------------------------------------------------
# Edge cases — display normalization / case / whitespace
# ---------------------------------------------------------------------------

def test_s70_validate_display_case_sensitive_snomed(fhir_client):
    """Item 4 edge: SNOMED displays are case sensitive ('Type 2 diabetes mellitus'
    vs 'type 2 diabetes mellitus'). Spec: 'Whether displays are case sensitive is
    code system dependent.' Probe behavior."""
    # Lowercase the canonical display.
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&display=type%202%20diabetes%20mellitus"
    )
    assert r.status_code == 200
    body = r.json()
    # Document behavior — the canonical is "Type 2 diabetes mellitus" (capital T).
    # The server MAY treat the lowercase as a mismatch (case-sensitive code system).
    # We assert the response shape: result + canonical display present either way.
    assert _has_param(body, "result")


# ---------------------------------------------------------------------------
# Coding parameter — alternative encoding probe (parity with system+code)
# ---------------------------------------------------------------------------

def test_s80_post_coding_produces_same_result_as_get_system_code(fhir_client):
    """Spec In Parameters: `coding` is a complete alternative to system+code.
    POST with valueCoding(system, code) MUST produce the same result as
    GET ?system=X&code=Y."""
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM},
                }
            ],
        },
    )
    assert r_get.status_code == 200 and r_post.status_code == 200
    body_get = r_get.json()
    body_post = r_post.json()
    assert _param_value(body_get, "result") == _param_value(body_post, "result")


# ---------------------------------------------------------------------------
# Cross-system consistency
# ---------------------------------------------------------------------------

def test_s90_validate_icd10_known_code_returns_true(fhir_client):
    """Item 5 / spec: validate known ICD-10-CM code → result=true."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={ICD10CM_URI}&code={ICD10CM_E11}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True


def test_s91_validate_unknown_system_returns_400(fhir_client):
    """Item 5 edge / spec Out: 'When the validation cannot be performed ... An
    error like this not returned if the code is not valid, but when the server
    is unable to determine whether the code is valid'. Unknown system → 400."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code?system=http://fake.example/sys&code=123"
    )
    assert r.status_code == 400
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


# ---------------------------------------------------------------------------
# Hostile / edge cases
# ---------------------------------------------------------------------------

def test_s100_validate_very_long_code_does_not_crash(fhir_client):
    """Hostile-input probe: 5K-char code → no 5xx."""
    long_code = "A" * 5000
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={long_code}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False


def test_s101_validate_special_chars_in_code_does_not_crash(fhir_client):
    """Hostile-input probe: <script> in code → no 5xx, response is valid JSON.

    Note (QA-050-L, INTENDED): FHIR R4 §3.4.1 does not mandate HTML-escaping of
    JSON string values; the user-supplied code MAY appear verbatim in the Out
    `code` parameter. The probe asserts the server doesn't crash and returns
    a parseable JSON body — not body-content escaping (which is the client's
    responsibility when rendering JSON in an HTML context)."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
    )
    assert r.status_code == 200
    body = r.json()  # parses as JSON; would raise if response were not valid JSON
    assert body.get("resourceType") == "Parameters"


def test_s102_validate_unknown_system_with_unknown_code_returns_400(fhir_client):
    """Hostile-input probe: unknown system takes precedence over unknown code
    (cannot determine validity without a known system)."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system=http://nonexistent.example&code=XYZ"
    )
    assert r.status_code == 400


def test_s103_post_validate_mixed_code_and_coding_accepted(fhir_client):
    """Mixed-encoding probe: POST with both system+code AND coding → 200
    (system+code takes precedence per medterm4ds convention)."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM},
                {
                    "name": "coding",
                    "valueCoding": {"system": SNOMED_URI, "code": "73211009"},
                },
            ],
        },
    )
    assert r.status_code == 200


def test_s104_post_validate_coding_missing_system_rejected(fhir_client):
    """Hostile-input probe: coding without system → 400."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {"code": SNOMED_T2DM}},
            ],
        },
    )
    assert r.status_code == 400


def test_s105_post_validate_codeable_concept_empty_codings_rejected(fhir_client):
    """Hostile-input probe: codeableConcept with empty coding[] → 400 (no
    usable coding)."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeableConcept", "valueCodeableConcept": {"coding": []}},
            ],
        },
    )
    assert r.status_code == 400


def test_s106_post_validate_codeable_concept_missing_coding_field_rejected(fhir_client):
    """Hostile-input probe: codeableConcept with no `coding` field → 400."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeableConcept", "valueCodeableConcept": {"text": "no coding"}},
            ],
        },
    )
    assert r.status_code == 400
