"""SKEPTIC iteration VS-05 — ValueSet $validate-code Operation.

Spec: https://build.fhir.org/valueset-operation-validate-code.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-validate-code.html

SKEPTIC lens for VS-05 — adversarial bug hunting on 6 spec items + edge cases:

1. Required params: drop each of ``url`` (or instance-level ValueSet),
   ``code``, ``system`` — expect 422 or 400 (per spec the server SHALL reject).
2. Optional params:
   - ``systemVersion``: probe behavior (accepted, no-op for single-snapshot engine)
   - ``display``: display mismatch — CF-SKEPTIC-CS03-01 (prime probe for this
     carry-forward: VS/$validate-code likely has the SAME display mismatch bug
     as CodeSystem/$validate-code per CS-03 SKEPTIC QA-048).
   - ``date``: past / future / malformed — accepted, no-op for snapshot engine
   - ``coding``: POST body with valueCoding as alternative to system+code
   - ``codeableConcept``: POST body with multiple codings
   - ``inferSystem``: when set, server tries to infer system from code
   - ``abstract``: when set, server includes abstract concepts in validation
3. Returns Parameters with ``result`` (boolean), ``message`` (string, optional),
   ``display`` (string, optional).
4. CodeableConcept input: any coding matches returns ``result=true``.
5. When display mismatch: ``result=false``, ``message`` explains,
   ``display`` = canonical display (CF-SKEPTIC-CS03-01 fix shape).
6. Implicit value set: code system URI alone is valid as ValueSet URL.

Critical carry-forward: CF-SKEPTIC-CS03-01 (MEDIUM) —
ValueSet/$validate-code does NOT enforce display mismatch. The CodeSystem
counterpart was fixed in CS-03 SKEPTIC QA-048 (``_do_validate``). The
ValueSet counterpart (``_do_vs_validate``) was NEVER patched. This iteration
closes the carry-forward. Pinned by CS-03 TERMINOLOGIST test_t60 (which
asserts the CURRENT — buggy — behavior); that probe MUST be updated in the
same PR that fixes the underlying gap.

Conformance fixture seeds (per tests/fhir_conformance/conftest.py):
  SNOMED 73211009 = "Diabetes mellitus"
  SNOMED 44054006 = "Type 2 diabetes mellitus"
  ICD-10-CM E11   = "Type 2 diabetes mellitus"
  RxNorm  860975  = "24 HR metformin 500 MG Oral Tablet"
"""

from __future__ import annotations

import pytest

# Spec sources:
#   https://build.fhir.org/valueset-operation-validate-code.html
#   https://hl7.org/fhir/R4/valueset-operation-validate-code.html
#
# Canonical FHIR R4 URIs (per SYSTEM_TO_FHIR_URI registry):
SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

# Seeded codes + canonical displays:
SNOMED_DM_CODE = "73211009"
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM_CODE = "44054006"
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
ICD10CM_E11_CODE = "E11"
ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_METFORMIN_CODE = "860975"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"


def _param_value(body: dict, name: str) -> object | None:
    """Return the value of the first Out parameter matching ``name``."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _has_param(body: dict, name: str) -> bool:
    return any(p.get("name") == name for p in body.get("parameter", []))


# ===========================================================================
# Item 1: Required params — drop each of url, code, system
# ===========================================================================
# Per FHIR R4 §4.9.3 In Parameters for ValueSet/$validate-code: when invoked
# at the type level, the caller SHALL provide either ``url`` OR a
# codeableConcept/coding that resolves to one, AND ``code`` AND ``system``
# (unless codeableConcept is provided). The medterm4ds implementation
# requires system+code (the spec-listed minimum for type-level invocation
# without codeableConcept). Source:
#   https://hl7.org/fhir/R4/valueset-operation-validate-code.html


def test_s01_get_validate_without_code_returns_4xx(fhir_client):
    """Drop ``code`` — server MUST reject (422 via FastAPI Query or 400)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
    )
    assert r.status_code in (400, 422), (
        f"Drop code: expected 400/422, got {r.status_code}. Body: {r.text[:300]}"
    )


def test_s02_get_validate_without_system_returns_4xx(fhir_client):
    """Drop ``system`` — server MUST reject (422 via FastAPI Query or 400)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code in (400, 422), (
        f"Drop system: expected 400/422, got {r.status_code}. Body: {r.text[:300]}"
    )


def test_s03_get_validate_without_code_or_system_returns_4xx(fhir_client):
    """Drop both — server MUST reject."""
    r = fhir_client.get("/fhir/ValueSet/$validate-code")
    assert r.status_code in (400, 422), (
        f"Drop both: expected 400/422, got {r.status_code}. Body: {r.text[:300]}"
    )


# ===========================================================================
# Item 2: Optional params — systemVersion, date, inferSystem, abstract,
# displayLanguage
# ===========================================================================


def test_s10_get_validate_with_systemVersion_accepted(fhir_client):
    """``systemVersion`` (0..1 canonical) — accepted, no-op for snapshot engine.

    Per FHIR R4 In Parameters ``systemVersion``: "The version of the code
    system to validate against". medterm4ds accepts but ignores it (single
    snapshot). INTENDED per AGENTS.md NOT A BUG registry (mirrors ``version``
    handling on CodeSystem operations).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&systemVersion=http://snomed.info/sct/900000000000207008"
    )
    assert r.status_code == 200, f"systemVersion accepted: {r.status_code} {r.text[:300]}"
    body = r.json()
    assert _param_value(body, "result") is True


def test_s11_get_validate_with_date_past_accepted(fhir_client):
    """``date`` (0..1 dateTime) — past date accepted.

    Per FHIR R4 In Parameters ``date``: "The date for which the validation
    should take place". medterm4ds accepts but ignores (no versioned data).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&date=2020-01-01"
    )
    assert r.status_code == 200


def test_s12_get_validate_with_date_future_accepted(fhir_client):
    """``date`` — future date accepted (no versioned data scoping)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&date=2099-12-31"
    )
    assert r.status_code == 200


def test_s13_get_validate_with_inferSystem_accepted(fhir_client):
    """``inferSystem`` (0..1 boolean) — accepted.

    Per FHIR R4 In Parameters ``inferSystem``: "If true, the server will
    infer the system from the code, if not specified". medterm4ds accepts
    but does not implement inference today (single-snapshot engine without
    a code-uniqueness registry). Documented as INTENDED-for-now.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?code={SNOMED_T2DM_CODE}&inferSystem=true"
    )
    # Without system, server returns 4xx (system required per current impl).
    # The probe verifies the param is ACCEPTED (no 500, no 422 syntax error);
    # the 400 is the documented behavior for "system required".
    assert r.status_code in (200, 400), (
        f"inferSystem param should be accepted (200 or 400 for missing system), "
        f"got {r.status_code}. Body: {r.text[:300]}"
    )


def test_s14_get_validate_with_abstract_param_accepted(fhir_client):
    """``abstract`` (0..1 boolean) — accepted.

    Per FHIR R4 In Parameters ``abstract``: "If this concept has an abstract
    property value, the validation will fail". medterm4ds accepts but does
    not implement abstract-flagging today (mirrors CodeSystem/$validate-code
    NOT A BUG registry entry on ``abstract``).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&abstract=true"
    )
    assert r.status_code == 200


def test_s15_get_validate_with_display_language_accepted(fhir_client):
    """``displayLanguage`` (0..1 code) — accepted, no-op (single-language)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&displayLanguage=en"
    )
    assert r.status_code == 200


def test_s16_post_validate_with_coding_alternative_encoding(fhir_client):
    """``coding`` In parameter (0..1 Coding) is a spec-listed alternative
    to system+code.

    Per FHIR R4 In Parameters ``coding``: "A coding to validate". The POST
    handler MUST accept the Parameters body with valueCoding. Mirrors TS-02
    HISTORIAN QA-022 pattern on CodeSystem/$validate-code. The CS-02 EXPLORER
    carried forward CF-EXPLORER-CS02-01 to verify ValueSet/$validate-code
    POST also accepts valueCoding.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM_CODE,
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200, (
        f"POST with coding alternative: expected 200, got {r.status_code}. "
        f"Body: {r.text[:300]}"
    )
    result_val = _param_value(r.json(), "result")
    assert result_val is True, (
        f"Known code via coding alt-encoding should validate as result=true. "
        f"Got result={result_val!r}."
    )


# ===========================================================================
# Item 3: Out Parameters shape — result, message (opt), display (opt)
# ===========================================================================


def test_s30_validate_response_shape_is_parameters(fhir_client):
    """Per FHIR R4 Out Parameters: the response is a Parameters resource."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    assert "parameter" in body


def test_s31_validate_result_is_valueBoolean_always_present(fhir_client):
    """Per FHIR R4 Out ``result`` (1..1 boolean): always present."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    body = r.json()
    assert _has_param(body, "result"), (
        "Out `result` parameter MUST always be present (cardinality 1..1)."
    )
    val = _param_value(body, "result")
    assert isinstance(val, bool), (
        f"Out `result` MUST be a wire-boolean, got {type(val).__name__} = {val!r}"
    )


def test_s32_validate_result_valueBoolean_lowercase_on_wire(fhir_client):
    """Per FHIR R4 §3.4.1: wire-format boolean MUST be lowercase ``true``/
    ``false``, never Python ``True``/``False``.

    Mirrors CR-002 (XML serializer boolean capitalization). The JSON
    serializer must produce ``"valueBoolean": true`` (lowercase keyword),
    not ``"valueBoolean": True`` (Python str).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    raw = r.text
    assert '"valueBoolean": true' in raw or '"valueBoolean":false' in raw or '"valueBoolean": false' in raw, (
        "Wire format MUST use lowercase boolean keyword. "
        f"Raw body: {raw[:300]}"
    )
    assert '"valueBoolean": True' not in raw, (
        "Python str(True) capital-T form must NOT appear on the wire."
    )
    assert '"valueBoolean": False' not in raw


def test_s33_validate_response_includes_display_when_code_known(fhir_client):
    """Per FHIR R4 Out ``display`` (0..1 string): when code is known, the
    server SHOULD return the canonical display.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    body = r.json()
    assert _has_param(body, "display"), (
        "When code is known, Out `display` SHOULD be present."
    )
    display = _param_value(body, "display")
    assert display == SNOMED_T2DM_DISPLAY, (
        f"Out `display` MUST be the engine's canonical preferred term. "
        f"Expected {SNOMED_T2DM_DISPLAY!r}, got {display!r}."
    )


def test_s34_validate_response_message_uses_valueString_when_present(fhir_client):
    """Per FHIR R4 Out ``message`` (0..1 string): when present, uses valueString."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}&code=UNKNOWN999"
    )
    body = r.json()
    if _has_param(body, "message"):
        msg_entry = next(
            p for p in body["parameter"] if p.get("name") == "message"
        )
        assert "valueString" in msg_entry, (
            "Out `message` MUST use valueString wire-type."
        )


# ===========================================================================
# Item 4: CodeableConcept — any match → result=true
# ===========================================================================
# Per FHIR R4 In Parameters ``codeableConcept``: "A full codeableConcept to
# validate. The server returns true if one of the coding values is in the
# code system".
# Source: https://hl7.org/fhir/R4/valueset-operation-validate-code.html


def test_s40_post_codeableConcept_one_valid_one_invalid_returns_true(fhir_client):
    """Spec: 'The server returns true if one of the coding values is in the
    code system'.

    Probe with [INVALID, VALID] — first coding invalid, second valid. Per
    spec, result MUST be true. Mirrors CS-03 SKEPTIC QA-049 on
    CodeSystem/$validate-code; VS-05 SKEPTIC verifies the same semantic
    holds on ValueSet/$validate-code.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": SNOMED_URI,
                            "code": "UNKNOWN-INVALID-CODE",
                        },
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_T2DM_CODE,
                        },
                    ]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200, (
        f"POST codeableConcept [INVALID, VALID]: expected 200, got {r.status_code}. "
        f"Body: {r.text[:300]}"
    )
    result_val = _param_value(r.json(), "result")
    assert result_val is True, (
        f"Any coding match → result=true per spec. Got result={result_val!r}."
    )


def test_s41_post_codeableConcept_all_invalid_returns_false(fhir_client):
    """All codings invalid → result=false per spec."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "BAD1"},
                        {"system": SNOMED_URI, "code": "BAD2"},
                    ]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    result_val = _param_value(r.json(), "result")
    assert result_val is False


def test_s42_post_codeableConcept_with_wrong_display_then_correct_returns_true(fhir_client):
    """Per spec: codeableConcept with codings [valid_with_wrong_display,
    valid_with_correct_display] → result=true.

    The display parameter is for VERIFICATION (per spec) — the existence of
    a coding with a valid code in the system is sufficient for result=true
    per the "any match" semantic. Display mismatch semantics apply to the
    top-level ``display`` parameter, NOT to per-coding displays in a
    codeableConcept (CS-03 SKEPTIC AUDIT-002).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_T2DM_CODE,
                            "display": "WRONG-PER-CODING-DISPLAY",
                        },
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_DM_CODE,
                            "display": SNOMED_DM_DISPLAY,
                        },
                    ]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    result_val = _param_value(r.json(), "result")
    assert result_val is True, (
        "codeableConcept [valid_with_wrong_display, valid_with_correct] → "
        f"result=true (any match wins). Got result={result_val!r}."
    )


# ===========================================================================
# Item 5: Display mismatch — CF-SKEPTIC-CS03-01 PRIME PROBE
# ===========================================================================
# Per FHIR R4 In Parameters ``display``: "A display to verify". Per Out
# ``display``: "A display to show to the user". When the client-supplied
# display does not match the engine's canonical, the spec-mandated behavior
# (mirroring CodeSystem/$validate-code per CS-03 SKEPTIC QA-048) is:
#   - result=false
#   - message="The display \"X\" is incorrect"
#   - Out display = engine canonical
# Source: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
# (the ValueSet operation In/Out Parameters are structurally identical to
# the CodeSystem operation per FHIR R4 §4.9.3 cross-reference)


def test_s50_get_validate_display_match_returns_true(fhir_client):
    """Sanity check: matching display → result=true."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={SNOMED_T2DM_DISPLAY}"
    )
    body = r.json()
    assert _param_value(body, "result") is True


def test_s51_get_validate_display_mismatch_returns_false_with_message_and_canonical(fhir_client):
    """CF-SKEPTIC-CS03-01 PRIME PROBE — display mismatch on VS/$validate-code.

    Per spec example (mirror of CodeSystem/$validate-code per CS-03 SKEPTIC
    QA-048): when a client supplies a ``display`` that does not match the
    engine's canonical display for the code, the response MUST carry:
      (1) result=false
      (2) message='The display "X" is incorrect' (citing wrong value)
      (3) display=<canonical preferred term> (engine canonical, NOT echo)

    The CF-SKEPTIC-CS03-01 carry-forward documented that
    ValueSet/$validate-code did NOT enforce display mismatch (mirroring the
    CS-03 CodeSystem bug). This iteration closes the carry-forward. Pinned
    by CS-03 TERMINOLOGIST test_t60 (asserts CURRENT-buggy behavior — MUST
    be updated when the fix lands).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG-CLINICAL-DISPLAY"
    )
    assert r.status_code == 200
    body = r.json()
    # (1) result MUST be false
    result_val = _param_value(body, "result")
    assert result_val is False, (
        "CF-SKEPTIC-CS03-01 fix: display mismatch → result=false. "
        f"Got result={result_val!r}."
    )
    # (2) message MUST cite the wrong value
    msg = _param_value(body, "message")
    assert msg is not None, "display mismatch → message MUST be present"
    assert "WRONG-CLINICAL-DISPLAY" in str(msg), (
        f"message MUST cite the wrong display value. Got: {msg!r}"
    )
    assert "incorrect" in str(msg).lower(), (
        f"message MUST indicate the display is incorrect. Got: {msg!r}"
    )
    # (3) Out display MUST be the engine canonical (NOT echo of wrong value)
    display = _param_value(body, "display")
    assert display == SNOMED_T2DM_DISPLAY, (
        f"Out `display` MUST be engine canonical ({SNOMED_T2DM_DISPLAY!r}), "
        f"got {display!r}. Client-input-as-canonical drift is prohibited."
    )


def test_s52_post_validate_display_mismatch_returns_false_with_message_and_canonical(fhir_client):
    """POST mirror of test_s51 — display mismatch on POST path.

    The POST and GET paths share ``_do_vs_validate`` (the inner handler),
    so both paths MUST agree on the display mismatch semantic. This probe
    guards against a regression that adds the fix to one path but not the
    other. Mirrors VS-02 TERMINOLOGIST test_t25/t26 (GET↔POST parity on
    CF-SKEPTIC-VS02-03).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
            {"name": "display", "valueString": "WRONG-CLINICAL-DISPLAY"},
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    rb = r.json()
    assert _param_value(rb, "result") is False
    msg = _param_value(rb, "message")
    assert msg is not None and "WRONG-CLINICAL-DISPLAY" in str(msg)
    assert _param_value(rb, "display") == SNOMED_T2DM_DISPLAY


def test_s53_validate_display_mismatch_message_format_matches_spec_example(fhir_client):
    """Spec example message format (mirror of CS-03 TERMINOLOGIST test_t90):

        The display "X" is incorrect

    The wrong value is quoted; the word "incorrect" is used (not synonyms
    like "wrong", "invalid", "mismatch"). Pinned byte-exact for cross-
    operation spec-example consistency.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=wrong-display"
    )
    body = r.json()
    msg = _param_value(body, "message")
    assert msg is not None
    assert msg == 'The display "wrong-display" is incorrect', (
        f"Spec example message format. Got: {msg!r}"
    )


# ===========================================================================
# Item 6: Implicit value set — code system URI alone as ValueSet URL
# ===========================================================================
# Per FHIR R4 §4.9.3 In Parameters ``url``: "ValueSet URL. If ``url`` is a
# reference to a code system, the operation validates that the code is in
# the code system". A code system URI alone is a valid implicit ValueSet
# URL. Source: https://hl7.org/fhir/R4/valueset-operation-validate-code.html


def test_s60_get_validate_with_implicit_valueset_url_system_uri(fhir_client):
    """Implicit value set Form (per TS-03 SKEPTIC QA-032): code system URI
    alone is a valid ValueSet URL.

    Per spec: ``url`` MAY be a code system URI; the operation validates
    code+system membership. The implementation accepts ``url`` for spec-
    compatibility and reduces membership evaluation to "is the code present
    in the underlying code system" (no persisted ValueSets today).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?url={SNOMED_URI}"
        f"&system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True


def test_s61_get_validate_with_implicit_valueset_url_snomed_intensional(fhir_client):
    """SNOMED CT intensional URL form: ``http://snomed.info/sct/73211009?fhir_vs=isa``.

    Per TS-03 SKEPTIC QA-032 implicit value set conventions, the URL form
    ``?fhir_vs=isa`` enumerates the code and its descendants. For
    $validate-code, the membership check is whether the supplied code is in
    the intensional expansion. The seeded conformance fixture has T2DM
    (44054006) as a child of DM (73211009), so 44054006 IS in the expansion
    of 73211009's isa.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code"
        f"?url=http://snomed.info/sct/{SNOMED_DM_CODE}?fhir_vs=isa"
        f"&system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    # The implementation may or may not scope membership to the intensional
    # expansion (today: code presence check only). The probe verifies the
    # operation doesn't 500 on the intensional URL form.
    assert r.status_code in (200, 400), (
        f"Intensional URL form should be accepted (200 or 400 for unimplemented "
        f"scoping), got {r.status_code}. Body: {r.text[:300]}"
    )


# ===========================================================================
# Item 5 (continued): Unknown code → result=false, no OperationOutcome
# ===========================================================================


def test_s70_validate_unknown_code_returns_false_not_operationoutcome(fhir_client):
    """Per FHIR R4: unknown code returns 200 + Parameters with result=false.

    OperationOutcome is for operation failures (4xx/5xx), NOT for "code
    not in value set" (which is a successful negative result). Mirrors
    CS-03 SKEPTIC test_s50 on CodeSystem/$validate-code.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}&code=UNKNOWN999"
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    assert _param_value(body, "result") is False


def test_s71_validate_unknown_system_returns_400(fhir_client):
    """Unknown system URI → 400 (not 200 with result=false)."""
    r = fhir_client.get(
        "/fhir/ValueSet/$validate-code?system=http://fake.example/sys&code=123"
    )
    assert r.status_code == 400


# ===========================================================================
# Cross-system probes (mirror CS-03 SKEPTIC test_s90)
# ===========================================================================


def test_s80_validate_icd10_known_code_returns_true(fhir_client):
    """Cross-system: ICD-10-CM seeded code validates."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={ICD10CM_URI}"
        f"&code={ICD10CM_E11_CODE}"
    )
    body = r.json()
    assert _param_value(body, "result") is True


def test_s81_validate_rxnorm_known_code_returns_true(fhir_client):
    """Cross-system: RxNorm seeded code validates."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={RXNORM_URI}"
        f"&code={RXNORM_METFORMIN_CODE}"
    )
    body = r.json()
    assert _param_value(body, "result") is True


def test_s82_validate_canonical_system_uri_returned_in_out_system(fhir_client):
    """Per CR-011 (milestone-2 review): Out `system` MUST be canonical URI.

    When a client supplies an alias (e.g., trailing slash), the Out
    `system` parameter MUST be the canonical URI from SYSTEM_TO_FHIR_URI.
    Mirrors CS-03 HISTORIAN QA-051 (canonical system echo drift fix).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}/"
        f"&code={SNOMED_T2DM_CODE}"
    )
    # Trailing-slash may or may not be recognized by fhir_uri_to_system;
    # the probe verifies that when recognized, the Out system is canonical.
    if r.status_code == 200:
        body = r.json()
        sys_val = _param_value(body, "system")
        assert sys_val == SNOMED_URI, (
            f"Out `system` MUST be canonical (no trailing slash). Got {sys_val!r}."
        )


# ===========================================================================
# Edge cases: hostile input + GET↔POST parity
# ===========================================================================


def test_s90_validate_very_long_code_does_not_crash(fhir_client):
    """5K-char code → no 5xx (mirrors TS-02 EXPLORER DoS probe)."""
    long_code = "X" * 5000
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}&code={long_code}"
    )
    assert r.status_code < 500, (
        f"5K code should not crash. Got {r.status_code}. Body: {r.text[:300]}"
    )


def test_s91_post_coding_produces_same_result_as_get_system_code(fhir_client):
    """GET↔POST parity: same (system, code) via GET and via POST with coding
    MUST produce byte-equivalent ``result`` values.

    Mirrors CS-03 SKEPTIC test_s80 + VS-04 EXPLORER GET↔POST parity probe
    class.
    """
    get_r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM_CODE,
                },
            },
        ],
    }
    post_r = fhir_client.post("/fhir/ValueSet/$validate-code", json=post_body)
    assert get_r.status_code == post_r.status_code == 200
    assert _param_value(get_r.json(), "result") == _param_value(post_r.json(), "result")


def test_s92_post_validate_mixed_code_and_coding_accepted(fhir_client):
    """Mixed scalar system+code AND coding in same body — server accepts."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM_CODE,
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200


def test_s93_post_validate_codeableConcept_empty_codings_handled(fhir_client):
    """codeableConcept with empty coding[] — server handles gracefully."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {"coding": []},
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    # Without system+code, server returns 4xx (system required).
    assert r.status_code in (400, 422)


# ===========================================================================
# XML rendering — boolean capitalization on operation route
# ===========================================================================


def test_s100_validate_xml_response_lowercase_valueBoolean(fhir_client):
    """XML wire format: ``<valueBoolean value="true"/>`` (lowercase).

    Per CR-002 (milestone-1 code review): Python's ``str(True) == "True"``
    but FHIR R4 §3.4.1 mandates lowercase ``true``/``false``. The XML
    serializer's ``_scalar_to_xml_attr`` boolean special-case MUST hold on
    the ValueSet/$validate-code route. Mirrors CS-04 EXPLORER test_e151
    (first hyphenated-XML probe) and CS-03 EXPLORER test_e110-e112.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&_format=xml"
    )
    assert r.status_code == 200
    body_text = r.text
    assert 'value="true"' in body_text, (
        f"XML wire format MUST use lowercase boolean. Body: {body_text[:300]}"
    )
    assert 'value="True"' not in body_text


# ===========================================================================
# Carry-forward confirmation probes — verify CF state, fail loudly on change
# ===========================================================================


def test_s110_cf_historian_vs02_02_implicit_path_canonical_uri_not_yet_wired(fhir_client):
    """CF-HISTORIAN-VS02-02 (MEDIUM, DEFERRED) — implicit value set path
    lacks canonical_system_uri() helper.

    This CF applies to ``_expand_implicit_value_set`` (the $expand path),
    NOT to ``_do_vs_validate`` (the $validate-code path which already has
    the helper post CR-011). The probe confirms CF-HISTORIAN-VS02-02 does
    NOT apply to the VS/$validate-code surface (cross-CF verification).
    """
    # On VS/$validate-code, the canonical URI IS re-resolved (CR-011).
    # The probe confirms the canonical URI appears in Out system, not the
    # raw alias.
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    body = r.json()
    sys_val = _param_value(body, "system")
    assert sys_val == SNOMED_URI, (
        f"VS/$validate-code Out `system` MUST be canonical SNOMED URI. "
        f"Got {sys_val!r}."
    )
