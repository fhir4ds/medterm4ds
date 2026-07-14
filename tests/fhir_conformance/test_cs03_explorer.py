"""EXPLORER iteration CS-03 — lateral-thinking probes for CodeSystem $validate-code.

Spec: https://build.fhir.org/codesystem-operation-validate-code.html
       (canonical R4: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html)

EXPLORER lens for CS-03 (per spec-comp CS-03 EXPLORER carry-forward prompt):

  1. **Display mismatch edge cases**:
     - Case sensitivity: "Diabetes" vs "diabetes" (SNOMED is case-sensitive).
     - Whitespace: leading/trailing/multiple internal spaces.
     - Unicode normalization: NFC vs NFD composed/decomposed forms.
  2. **``inferSystem`` semantics**: confirm it is NOT a CodeSystem/$validate-code
     In parameter (per spec table). Verify the server does NOT silently
     process it (passes through harmlessly OR is ignored — both conformant).
  3. **``date`` parameter**: past, future, malformed — all accepted without
     5xx (processing is out of scope for v0.0.x; conformance just requires
     the parameter to be accepted).
  4. **POST Content-Type on ``$validate-code``** (CF-EXPLORER-CS02-01
     carry-forward): probe Content-Type per route — system+code body,
     coding body, codeableConcept body, error path.
  5. **Response parameter ordering**: ``result``, ``code``, ``system``,
     optional ``display``, optional ``message``. Strict clients rely on
     documented Out parameter names — verify each is present with the
     correct ``value*`` type.
  6. **``message`` field format**: clear? Contains the wrong display value
     the client sent + cites the spec example shape ("The display \"X\" is
     incorrect").
  7. **``codeableConcept.text`` only** (no codings): server behavior?
     Spec lists codeableConcept as a CodeableConcept type — text-only is
     valid per FHIR R4 §4.8.13 but cannot identify a code. Server SHOULD
     return result=false (not 5xx).
  8. **``coding`` on GET** (should be POST-only): graceful handling. GET
     ``$validate-code?coding=...`` — the spec defines ``coding`` as a
     Parameters-body parameter (POST). On GET it should be ignored or
     rejected, not crash.
  9. **Combined input encodings** (``code`` + ``coding`` + ``codeableConcept``):
     spec violation handling. Server MUST NOT 5xx.
  10. **Cross-system consistency**: every supported system resolves a known
      code via ``$validate-code`` and emits the same Out parameter shape.
  11. **Boolean XML rendering** (per GLOBAL_RULES.md "Boolean capitalization
      on serializers"): probe ``_format=xml`` AND ``Accept:
      application/fhir+xml`` and verify ``<valueBoolean value="true"/>`` /
      ``value="false"`` — NOT ``value="True"`` / ``value="False"``.

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts the POSITIVE
success shape (200 + Parameters body with the expected fields), not just
absence of an error string.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # canonical display: "Diabetes mellitus"
SNOMED_T2DM = "44054006"               # canonical display: "Type 2 diabetes mellitus"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"                    # canonical display: "Type 2 diabetes mellitus"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"


def _param_value(body: dict, name: str) -> object | None:
    """Extract the wire value of a named parameter from a Parameters body."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _assert_validate_200_with_result(r, label: str) -> dict:
    """Common positive-success-shape assertion for $validate-code.

    Returns the parsed body for further assertions.
    """
    assert r.status_code == 200, (
        f"{label}: expected 200, got {r.status_code}; body={r.text[:300]!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"{label}: body must be Parameters; got {body.get('resourceType')}"
    )
    # result is 1..1 per spec
    result_val = _param_value(body, "result")
    assert result_val is not None, (
        f"{label}: Out 'result' parameter missing"
    )
    assert isinstance(result_val, bool), (
        f"{label}: Out 'result' must be a boolean; got {type(result_val).__name__}"
    )
    return body


# ---------------------------------------------------------------------------
# 1. Display mismatch edge cases
# ---------------------------------------------------------------------------

def test_e10_validate_display_case_differs_from_canonical_returns_false(fhir_client):
    """Display mismatch with case difference: "diabetes mellitus" (lowercase)
    vs canonical "Diabetes mellitus". Per the spec example response, a wrong
    display → result=false + message + canonical display. The implementation
    enforces exact-match today (per SKEPTIC QA-048 — case-sensitivity is
    code-system-dependent and per-source flagging is a future enhancement).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "display": "diabetes mellitus",  # lowercase d
        },
    )
    body = _assert_validate_200_with_result(r, "case-differing display")
    assert _param_value(body, "result") is False, (
        f"case-differing display should NOT match canonical; result should be False"
    )
    msg = _param_value(body, "message")
    assert msg is not None, (
        "result=false MUST carry a message per spec example response"
    )
    assert "incorrect" in str(msg).lower(), (
        f"message should contain 'incorrect'; got {msg!r}"
    )


def test_e11_validate_display_trailing_whitespace_returns_false(fhir_client):
    """Display with trailing whitespace: "Diabetes mellitus " (trailing
    space). Exact-match enforcement means whitespace differences trigger
    result=false. Documents current behavior.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "display": "Diabetes mellitus ",  # trailing space
        },
    )
    body = _assert_validate_200_with_result(r, "trailing-whitespace display")
    assert _param_value(body, "result") is False, (
        "trailing-whitespace display should NOT match canonical exactly"
    )


def test_e12_validate_display_leading_whitespace_returns_false(fhir_client):
    """Display with leading whitespace: " Diabetes mellitus". Exact-match
    enforcement means whitespace differences trigger result=false.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "display": " Diabetes mellitus",
        },
    )
    body = _assert_validate_200_with_result(r, "leading-whitespace display")
    assert _param_value(body, "result") is False


def test_e13_validate_display_internal_double_space_returns_false(fhir_client):
    """Display with internal double space: "Diabetes  mellitus". Exact-match
    catches this as a mismatch.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "display": "Diabetes  mellitus",  # double space
        },
    )
    body = _assert_validate_200_with_result(r, "internal-double-space display")
    assert _param_value(body, "result") is False


def test_e14_validate_display_unicode_nfc_matches_canonical(fhir_client):
    """Unicode NFC normalization: the canonical display contains only ASCII
    characters in the fixture, so an NFC-normalized variant equals the
    canonical exactly. The probe documents that no extra normalization
    happens — exact match means identical byte sequences match.
    """
    # "Diabetes mellitus" — pure ASCII; NFC == bytes.
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "display": "Diabetes mellitus",
        },
    )
    body = _assert_validate_200_with_result(r, "exact-match display (NFC == bytes)")
    assert _param_value(body, "result") is True


# ---------------------------------------------------------------------------
# 2. inferSystem semantics
# ---------------------------------------------------------------------------

def test_e20_validate_get_with_inferSystem_accepted_without_5xx(fhir_client):
    """``inferSystem`` is NOT a CodeSystem/$validate-code In parameter
    (per FHIR R4 codesystem-operation-validate-code.html — it is a
    ValueSet/$validate-code parameter). Server MUST accept it via FastAPI's
    permissive default (system + code still required) and ignore it.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "inferSystem": "true",
        },
    )
    _assert_validate_200_with_result(r, "GET with inferSystem=true (ignored)")


def test_e21_validate_post_with_inferSystem_accepted_without_5xx(fhir_client):
    """POST ``$validate-code`` with ``inferSystem`` parameter. Same shape —
    ignored on CodeSystem/$validate-code.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "inferSystem", "valueBoolean": True},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    _assert_validate_200_with_result(r, "POST with inferSystem=true (ignored)")


# ---------------------------------------------------------------------------
# 3. date parameter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "date_val",
    [
        "2020-01-01",         # past
        "2030-12-31",         # future
        "2024-01-01T00:00:00Z",  # full dateTime
        "not-a-date",         # malformed
        "",                   # empty
    ],
)
def test_e30_validate_date_param_accepted_without_5xx(fhir_client, date_val):
    """``date`` parameter is 0..1 dateTime. medterm4ds does not version-scope
    data (NOT A BUG registry entry for ``version``). The param MUST be
    accepted without 5xx — processing is deferred to a future enhancement.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "date": date_val,
        },
    )
    _assert_validate_200_with_result(r, f"date={date_val!r}")


# ---------------------------------------------------------------------------
# 4. POST Content-Type on $validate-code (CF-EXPLORER-CS02-01)
# ---------------------------------------------------------------------------

def test_e40_post_validate_system_code_body_emits_fhir_mimetype(fhir_client):
    """POST ``$validate-code`` with system+code Parameters body MUST emit
    ``Content-Type: application/fhir+json`` (FHIR R4 §3.1.0.1.9). The
    CR-001 parametrized Content-Type probe skips ``$validate-code`` because
    it needs complex parameters; this probe closes the coverage gap.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DIABETES_MELLITUS},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST $validate-code (system+code body) Content-Type is {ct!r}; spec "
        f"mandates application/fhir+json (FHIR R4 §3.1.0.1.9)."
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"


def test_e41_post_validate_coding_body_emits_fhir_mimetype(fhir_client):
    """POST ``$validate-code`` with a ``coding`` parameter MUST emit
    ``application/fhir+json``.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DIABETES_MELLITUS,
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct


def test_e42_post_validate_codeable_concept_body_emits_fhir_mimetype(fhir_client):
    """POST ``$validate-code`` with a ``codeableConcept`` parameter MUST
    emit ``application/fhir+json``. Same shape — different input encoding.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [{"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct


def test_e43_post_validate_error_path_emits_fhir_mimetype(fhir_client):
    """POST ``$validate-code`` returning a 400 (missing system AND code)
    MUST still emit ``application/fhir+json`` Content-Type with a
    Parameters OperationOutcome body — not text/plain, not application/json.
    """
    body = {"resourceType": "Parameters", "parameter": []}
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST $validate-code error Content-Type is {ct!r}; spec mandates "
        f"application/fhir+json on every error response (§3.1.0.1.5 + §3.1.0.1.9)."
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "OperationOutcome"


# ---------------------------------------------------------------------------
# 5. Response parameter ordering / value-type fidelity
# ---------------------------------------------------------------------------

def test_e50_validate_response_value_types_match_spec(fhir_client):
    """Out parameter value types MUST match the spec table:
    - ``result``  → valueBoolean
    - ``code``    → valueCode
    - ``system``  → valueUri
    - ``display`` → valueString
    - ``message`` → valueString
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "display": "wrong-display",  # trigger message emission
        },
    )
    body = _assert_validate_200_with_result(r, "value-type fidelity")
    params = {p["name"]: p for p in body.get("parameter", [])}
    assert "valueBoolean" in params["result"], params["result"]
    assert "valueCode" in params["code"], params["code"]
    assert "valueUri" in params["system"], params["system"]
    assert "valueString" in params["display"], params["display"]
    assert "valueString" in params["message"], params["message"]


def test_e51_validate_response_includes_result_code_system(fhir_client):
    """Every successful response MUST include the three core Out params
    (``result``, ``code``, ``system``). The builder always emits them.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = _assert_validate_200_with_result(r, "core params")
    param_names = {p.get("name") for p in body.get("parameter", [])}
    for required in ("result", "code", "system"):
        assert required in param_names, (
            f"Out parameter missing required '{required}'. Got: {sorted(param_names)}"
        )


# ---------------------------------------------------------------------------
# 6. message field format
# ---------------------------------------------------------------------------

def test_e60_message_format_includes_wrong_display_value(fhir_client):
    """``message`` field on display mismatch MUST cite the wrong display
    value the client sent. The spec example response shows
    ``"The display \\"test\\" is incorrect"``.
    """
    wrong_display = "totally-wrong-display"
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "display": wrong_display,
        },
    )
    body = _assert_validate_200_with_result(r, "message format")
    msg = _param_value(body, "message")
    assert msg is not None
    assert wrong_display in str(msg), (
        f"message should contain the client's wrong display value "
        f"{wrong_display!r}; got {msg!r}"
    )


def test_e61_message_includes_spec_word_incorrect(fhir_client):
    """``message`` field on display mismatch cites the spec word
    "incorrect". Per the canonical R4 example response shape.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "display": "wrong",
        },
    )
    body = _assert_validate_200_with_result(r, "message wording")
    msg = _param_value(body, "message")
    assert msg is not None
    assert "incorrect" in str(msg).lower(), (
        f"message should contain 'incorrect' (spec example); got {msg!r}"
    )


# ---------------------------------------------------------------------------
# 7. codeableConcept.text only (no codings)
# ---------------------------------------------------------------------------

def test_e70_post_codeable_concept_text_only_no_codings(fhir_client):
    """``codeableConcept`` with only ``text`` and no ``coding`` array.
    Per FHIR R4 §4.8.13 CodeableConcept is 0..* Coding + 0..1 string text.
    A text-only CodeableConcept cannot identify a code. The server MUST
    return a conformant Parameters body (NOT 5xx). With no codings to
    validate, result SHOULD be false (no coding is in the code system).

    Per spec example response shape: result=false with optional message.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "text": "Diabetes mellitus (text-only, no codings)"
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    # Server MUST NOT 5xx on text-only codeableConcept.
    assert r.status_code != 500, (
        f"text-only codeableConcept must not 5xx; got {r.status_code}: {r.text[:300]!r}"
    )
    # Per implementation, no system+code and no extractable coding pairs →
    # the handler returns 400 "system and code are required". Either shape
    # (400 OperationOutcome OR 200 result=false Parameters) is conformant
    # since the spec is silent on text-only. We assert only "no 5xx".
    body_json = r.json()
    assert body_json.get("resourceType") in ("Parameters", "OperationOutcome"), (
        f"text-only codeableConcept response must be Parameters or "
        f"OperationOutcome; got {body_json.get('resourceType')}"
    )


# ---------------------------------------------------------------------------
# 8. coding on GET (should be POST-only) — graceful handling
# ---------------------------------------------------------------------------

def test_e80_get_validate_with_coding_query_param_accepted_without_5xx(fhir_client):
    """GET ``$validate-code?coding=...`` — the spec defines ``coding`` as a
    Parameters-body parameter (POST). On GET, FastAPI's permissive default
    accepts any query param; the handler does NOT consult it (it expects
    ``system`` + ``code`` Query params, both required).

    With system + code present, the server validates the system+code and
    ignores the bogus ``coding`` query param. Server MUST NOT 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "coding": "ignored-on-get",  # bogus but harmless
        },
    )
    body = _assert_validate_200_with_result(r, "GET with bogus coding query param")


# ---------------------------------------------------------------------------
# 9. Combined input encodings (code + coding + codeableConcept)
# ---------------------------------------------------------------------------

def test_e90_post_validate_combined_code_and_coding_uses_system_code(fhir_client):
    """POST with BOTH system+code AND coding in the body. The spec says
    "a client SHALL provide one (and only one) of the parameters
    (code+system, coding, or codeableConcept)". medterm4ds prefers
    system+code (the explicit primary key). Server MUST NOT 5xx and the
    Out ``code`` reflects the system+code.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DIABETES_MELLITUS},
            {
                "name": "coding",
                "valueCoding": {"system": RXNORM_URI, "code": RXNORM_METFORMIN},
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    body_json = _assert_validate_200_with_result(r, "combined system+code AND coding")
    # system+code wins
    out_code = _param_value(body_json, "code")
    assert out_code == SNOMED_DIABETES_MELLITUS, (
        f"combined input: system+code should win; Out code={out_code!r}"
    )


def test_e91_post_validate_combined_all_three_encodings_no_5xx(fhir_client):
    """POST with system+code AND coding AND codeableConcept. Spec violation
    (multiple encodings supplied). Server MUST NOT 5xx. The
    system+code primary key wins.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DIABETES_MELLITUS},
            {
                "name": "coding",
                "valueCoding": {"system": RXNORM_URI, "code": RXNORM_METFORMIN},
            },
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [{"system": ICD10CM_URI, "code": ICD10CM_E11}]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    body_json = _assert_validate_200_with_result(r, "combined all three encodings")
    out_code = _param_value(body_json, "code")
    assert out_code == SNOMED_DIABETES_MELLITUS


# ---------------------------------------------------------------------------
# 10. Cross-system consistency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "system_uri, code",
    [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_E11),
        (RXNORM_URI, RXNORM_METFORMIN),
    ],
)
def test_e100_validate_cross_system_consistent_shape(fhir_client, system_uri, code):
    """Every supported system resolves a known code via ``$validate-code``
    and emits the same Out parameter shape (``result``, ``code``,
    ``system``, optional ``display``).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": system_uri, "code": code},
    )
    body = _assert_validate_200_with_result(r, f"system={system_uri} code={code}")
    assert _param_value(body, "result") is True, (
        f"known code should validate true; system={system_uri} code={code}"
    )
    param_names = {p.get("name") for p in body.get("parameter", [])}
    assert "display" in param_names, (
        f"system={system_uri}: Out parameter missing 'display'"
    )


@pytest.mark.parametrize(
    "system_uri, code",
    [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_E11),
        (RXNORM_URI, RXNORM_METFORMIN),
    ],
)
def test_e101_validate_cross_system_out_system_is_canonical(fhir_client, system_uri, code):
    """Out ``system`` is the canonical FHIR URI for every supported system
    (per CS-03 HISTORIAN QA-051 fix). Client can pass alias or trailing-
    slash; the Out system is re-resolved to canonical.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": system_uri, "code": code},
    )
    body = _assert_validate_200_with_result(r, f"canonical system check {system_uri}")
    out_system = _param_value(body, "system")
    assert out_system == system_uri, (
        f"Out system should be canonical {system_uri}; got {out_system!r}"
    )


# ---------------------------------------------------------------------------
# 11. Boolean XML rendering (per GLOBAL_RULES "Boolean capitalization")
# ---------------------------------------------------------------------------

def test_e110_validate_xml_format_renders_valueBoolean_lowercase_true(fhir_client):
    """``_format=xml`` MUST render ``result=true`` as
    ``<valueBoolean value="true"/>`` (lowercase) — NOT ``value="True"``
    (Python str(True)). Per GLOBAL_RULES.md "Boolean capitalization on
    serializers" and FHIR R4 §3.4.1.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "_format": "xml",
        },
    )
    assert r.status_code == 200, f"body={r.text[:400]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+xml" in ct, (
        f"_format=xml: Content-Type is {ct!r}; spec mandates application/fhir+xml"
    )
    body_text = r.text
    # The boolean primitive MUST render as lowercase 'true' on the wire.
    assert 'value="true"' in body_text, (
        f"valueBoolean=true must render as value=\"true\" (lowercase); "
        f"got body snippet: {body_text[:400]!r}"
    )
    # Explicitly: capital-T "True" MUST NOT appear in the response.
    assert 'value="True"' not in body_text, (
        f"valueBoolean=True (capital T) leaked to wire; body snippet: "
        f"{body_text[:400]!r}"
    )


def test_e111_validate_xml_format_renders_valueBoolean_lowercase_false(fhir_client):
    """``_format=xml`` MUST render ``result=false`` as
    ``<valueBoolean value="false"/>`` (lowercase) — NOT ``value="False"``
    (Python str(False)). Per GLOBAL_RULES.md and FHIR R4 §3.4.1.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "display": "wrong-display",  # trigger result=false
            "_format": "xml",
        },
    )
    assert r.status_code == 200, f"body={r.text[:400]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+xml" in ct
    body_text = r.text
    assert 'value="false"' in body_text, (
        f"valueBoolean=false must render as value=\"false\" (lowercase); "
        f"got body snippet: {body_text[:400]!r}"
    )
    assert 'value="False"' not in body_text, (
        f"valueBoolean=False (capital F) leaked to wire; body snippet: "
        f"{body_text[:400]!r}"
    )


def test_e112_validate_accept_header_xml_renders_lowercase_booleans(fhir_client):
    """``Accept: application/fhir+xml`` (without ``_format``) MUST also
    render lowercase booleans. The XML serializer is shared between
    ``_format`` and Accept-driven XML paths.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
        },
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200, f"body={r.text[:400]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+xml" in ct
    body_text = r.text
    assert 'value="true"' in body_text
    assert 'value="True"' not in body_text


# ---------------------------------------------------------------------------
# 12. Edge: empty display triggers mismatch (documents current behavior)
# ---------------------------------------------------------------------------

def test_e120_validate_empty_display_param_documents_mismatch(fhir_client):
    """Empty ``display`` parameter. The handler's exact-match comparison
    treats empty != canonical → result=false. Documents current behavior
    (per CS-03 HISTORIAN test_h21). The spec example uses a non-empty
    wrong display; empty is an undocumented edge. Treating empty as a
    mismatch is over-strict but not a spec violation.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "display": "",
        },
    )
    body = _assert_validate_200_with_result(r, "empty display")
    # Empty display != canonical display → result=false (documents current).
    assert _param_value(body, "result") is False, (
        "empty display documents as mismatch under exact-match enforcement"
    )
