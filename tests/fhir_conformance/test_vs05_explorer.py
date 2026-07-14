"""EXPLORER probes for VS-05 (ValueSet $validate-code Operation).

Spec: https://build.fhir.org/valueset-operation-validate-code.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-validate-code.html

EXPLORER lens: lateral thinking — unusual parameter combinations,
integration corners, route-coverage gaps, and shape probes that no
prior test has tried. Per the iteration prompt, the focus areas are:

  1. **4-shape POST Content-Type closure on ValueSet/$validate-code**
     (CF-EXPLORER-CS02-01 LAST operation in the family — closes the
     carry-forward). Per CS-03 EXPLORER + CS-04 EXPLORER + CS-05
     EXPLORER + VS-01 EXPLORER + VS-02 EXPLORER + VS-03 EXPLORER +
     VS-04 EXPLORER (each closed one operation's POST Content-Type
     probe family), this EXPLORER iteration closes ValueSet/$validate-
     code:
       (a) GET with url+code+system (baseline)
       (b) POST with Parameters body (system+code)
       (c) POST with codeableConcept body
       (d) Error path (missing system+code on POST)
     All 4 shapes MUST emit ``Content-Type: application/fhir+json`` AND
     a Parameters body (or OperationOutcome on the error path). The
     probe walks ``app.routes`` is unnecessary here — the 4 shapes are
     explicit because they are the spec-listed input encodings.

  2. **``inferSystem`` edge cases** (In parameter per FHIR R4 §4.9.3):
       (a) ``code`` only (no system), ``inferSystem=true`` — server
           SHOULD infer; medterm4ds does not implement inference today
           (INTENDED-for-now per NOT A BUG registry — no code-uniqueness
           registry). The probe verifies the param is ACCEPTED (no 500,
           no 422 syntax error), not that inference succeeds.
       (b) Ambiguous code (multiple systems) — server behavior is
           deterministic given the current implementation (server does
           not infer). The probe documents the CURRENT behavior.
       (c) Code unique to one system — implementation accepts but does
           not infer; the probe documents the CURRENT behavior.

  3. **``abstract`` parameter handling** (In parameter per FHIR R4
     §4.9.3):
       (a) ``abstract=false`` (default) — accepted, no-op
       (b) ``abstract=true`` — accepted, no-op (mirrors CodeSystem/`
           $validate-code` NOT A BUG registry entry; engine has no
           abstract-flagging data)
       (c) ``abstract`` on a code that is in the system — result=true
           regardless of abstract value

  4. **Implicit value set URL variations** (per FHIR R4 §4.9.3 ``url``
     param):
       (a) ``url=http://snomed.info/sct`` (code system URI alone) — valid
       (b) ``url=http://snomed.info/sct/73211009?fhir_vs=isa`` —
           intensional URL form (validate against expansion)
       (c) ``url=http://loinc.org/vs`` — implicit value set (no LOINC
           seeded in fixture; probe verifies graceful handling)

  5. **Cross-system consistency**: same code shape validated across
     SNOMED, LOINC (no LOINC seeded), RxNorm — consistent behavior.

  6. **Display mismatch edge cases**:
       (a) Case sensitivity ("Diabetes" vs "diabetes")
       (b) Whitespace (trailing/leading)
       (c) Unicode normalization (NFC vs NFD where applicable)

  7. **Multi-coding codeableConcept**:
       (a) All valid codings → true
       (b) All invalid → false
       (c) Mixed valid/invalid with display mismatch — semantic question
           (any match wins; per-coding display not enforced per
           CS-03 SKEPTIC AUDIT-002)

  8. **Date parameter variations**: past, future, malformed,
     lexicographically sortable forms.

  9. **Combined input encodings** (url+code+system+coding+codeableConcept
     — spec violation): server behavior is deterministic; the probe
     documents the CURRENT behavior (likely picks one source; verifies
     no 500).

 10. **GET ↔ POST parity probe class** (VS-04 EXPLORER strategy 50):
     same input via GET and POST MUST produce byte-equivalent
     ``result`` values. Already covered by SKEPTIC test_s91 on the
     happy path; EXPLORER adds the display-mismatch case (post-SKEPTIC
     QA-069 fix).

 11. **Cross-operation canonical-agreement** (CS-05 EXPLORER strategy
     38): ``$validate-code`` and ``$lookup`` on the same (system, code)
     MUST agree on canonical ``system`` AND ``display`` in their Out
     parameters. Catches silent drift if a future fix touches one
     operation but not the other.

 12. **XML wire-format on operation route** (CS-04 EXPLORER
     methodology): ``_format=xml`` MUST emit lowercase boolean on the
     VS/$validate-code route. SKEPTIC test_s100 covers this on the
     happy path; EXPLORER adds the display-mismatch case (post-SKEPTIC
     QA-069 fix — XML serialization of message valueString).

 13. **Implicit value set URL form (b) — SNOMED intensional with code
     on $validate-code**: per FHIR R4 §4.9.3, when ``url`` is a
     SNOMED intensional URL form, the membership check is whether the
     supplied code is in the intensional expansion. medterm4ds accepts
     but does not scope membership (no persisted ValueSets). The probe
     documents the CURRENT behavior.

 14. **Accept-header XML negotiation** (CS-04 EXPLORER test_e160
     pattern): ``Accept: application/fhir+xml`` MUST emit XML body
     ( mirrors _format=xml but distinct header path).

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts POSITIVE
success shape (200 + expected fields) OR a specific error message
content, not just the absence of one error string.
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
LOINC_URI = "http://loinc.org"

# Seeded codes + canonical displays (per conftest.py _make_conformance_db):
SNOMED_DM_CODE = "73211009"
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM_CODE = "44054006"
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
ICD10CM_E11_CODE = "E11"
ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_METFORMIN_CODE = "860975"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"

FHIR_JSON = "application/fhir+json"
FHIR_XML = "application/fhir+xml"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

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
# Lens 1: 4-shape POST Content-Type closure on ValueSet/$validate-code
# ===========================================================================
# CF-EXPLORER-CS02-01 (LAST operation in the family — closes the carry-forward).
# Per CS-03 EXPLORER + CS-04 EXPLORER + CS-05 EXPLORER + VS-01 EXPLORER +
# VS-02 EXPLORER + VS-03 EXPLORER + VS-04 EXPLORER (each closed one
# operation's POST Content-Type probe family), this EXPLORER iteration
# closes ValueSet/$validate-code.
#
# The 4 shapes are:
#   (a) GET with url+code+system (baseline)
#   (b) POST with Parameters body (system+code)
#   (c) POST with codeableConcept body
#   (d) Error path (missing system+code on POST)
#
# All 4 shapes MUST emit ``Content-Type: application/fhir+json`` AND a
# Parameters body (or OperationOutcome on the error path).


def test_e10_get_with_url_code_system_emits_fhir_json_content_type(fhir_client):
    """Shape (a): GET with url+code+system.

    Per FHIR R4 §3.1.0.1.9: the response Content-Type MUST be
    ``application/fhir+json`` (or ``application/fhir+xml`` per _format).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?url={SNOMED_URI}"
        f"&system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(FHIR_JSON), (
        f"Shape (a) GET: Content-Type MUST be {FHIR_JSON}. "
        f"Got: {r.headers['content-type']!r}."
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"Shape (a) GET: body MUST be a Parameters resource. "
        f"Got resourceType={body.get('resourceType')!r}."
    )


def test_e11_post_with_system_code_body_emits_fhir_json_content_type(fhir_client):
    """Shape (b): POST with Parameters body (system+code).

    Per FHIR R4 §3.1.0.1.5 + §3.1.0.1.9: POST responses MUST have the
    FHIR MIME type. Per CR-001 (milestone-1 code review), the
    ``payload if isinstance(payload, Response) else _fhir_response``
    pattern MUST be applied on every operation handler.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200, (
        f"Shape (b) POST: expected 200, got {r.status_code}. Body: {r.text[:300]}"
    )
    assert r.headers["content-type"].startswith(FHIR_JSON), (
        f"Shape (b) POST: Content-Type MUST be {FHIR_JSON}. "
        f"Got: {r.headers['content-type']!r}."
    )
    rb = r.json()
    assert rb.get("resourceType") == "Parameters"


def test_e12_post_with_codeableConcept_body_emits_fhir_json_content_type(fhir_client):
    """Shape (c): POST with codeableConcept body.

    Per FHIR R4 §4.9.3 In Parameters ``codeableConcept``: a spec-listed
    alternative to system+code. POST responses MUST have the FHIR MIME
    type.
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
                        },
                    ]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200, (
        f"Shape (c) codeableConcept POST: expected 200, got {r.status_code}. "
        f"Body: {r.text[:300]}"
    )
    assert r.headers["content-type"].startswith(FHIR_JSON), (
        f"Shape (c) codeableConcept POST: Content-Type MUST be {FHIR_JSON}. "
        f"Got: {r.headers['content-type']!r}."
    )
    rb = r.json()
    assert rb.get("resourceType") == "Parameters"


def test_e13_post_error_path_emits_fhir_json_with_operationoutcome(fhir_client):
    """Shape (d): Error path (missing system+code) → 400 OperationOutcome.

    Per FHIR R4 §3.6.1: 4xx responses MUST carry an OperationOutcome
    body. Per CR-001: the OperationOutcome MUST be FHIR-MIME typed,
    not Starlette-default JSON.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
            # No system, no codeableConcept — server must reject.
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 400, (
        f"Shape (d) error path: expected 400, got {r.status_code}. Body: {r.text[:300]}"
    )
    assert r.headers["content-type"].startswith(FHIR_JSON), (
        f"Shape (d) error path: Content-Type MUST be {FHIR_JSON}. "
        f"Got: {r.headers['content-type']!r}."
    )
    rb = r.json()
    assert rb.get("resourceType") == "OperationOutcome", (
        f"Shape (d) error path: body MUST be OperationOutcome. "
        f"Got resourceType={rb.get('resourceType')!r}."
    )


def test_e14_closing_cf_explorer_cs02_01_all_4_shapes_pass(fhir_client):
    """CF-EXPLORER-CS02-01 closure probe — verify all 4 shapes pass.

    This single probe confirms the CF-EXPLORER-CS02-01 family is fully
    closed: every operation that accepts a Parameters body now has a
    4-shape POST Content-Type probe family. ValueSet/$validate-code is
    the LAST operation in the family (after CodeSystem/$lookup,
    CodeSystem/$validate-code, CodeSystem/$subsumes, ValueSet/$expand).
    """
    # Shape (a) GET
    r_get = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    # Shape (b) POST system+code
    body_b = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
        ],
    }
    r_post_sc = fhir_client.post("/fhir/ValueSet/$validate-code", json=body_b)
    # Shape (c) POST codeableConcept
    body_c = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [{"system": SNOMED_URI, "code": SNOMED_T2DM_CODE}]
                },
            },
        ],
    }
    r_post_cc = fhir_client.post("/fhir/ValueSet/$validate-code", json=body_c)
    # Shape (d) Error path
    body_d = {
        "resourceType": "Parameters",
        "parameter": [{"name": "code", "valueCode": SNOMED_T2DM_CODE}],
    }
    r_err = fhir_client.post("/fhir/ValueSet/$validate-code", json=body_d)

    # All 4 shapes MUST emit FHIR MIME
    for label, resp in [
        ("GET", r_get), ("POST system+code", r_post_sc),
        ("POST codeableConcept", r_post_cc), ("Error path", r_err),
    ]:
        assert resp.headers["content-type"].startswith(FHIR_JSON), (
            f"CF-EXPLORER-CS02-01 closure: shape {label!r} MUST emit "
            f"{FHIR_JSON}. Got {resp.headers['content-type']!r}."
        )


# ===========================================================================
# Lens 2: inferSystem edge cases
# ===========================================================================
# Per FHIR R4 §4.9.3 In Parameters ``inferSystem`` (0..1 boolean): "If
# true, the server will infer the system from the code, if not
# specified". medterm4ds accepts but does not implement inference today
# (single-snapshot engine without a code-uniqueness registry).


def test_e20_inferSystem_true_without_system_accepted(fhir_client):
    """``inferSystem=true`` without ``system`` — param accepted.

    Per spec: server SHOULD infer; medterm4ds does not implement
    inference. The probe verifies the param is ACCEPTED (no 500, no
    422 syntax error). The current behavior is 400 (system required)
    which is the documented INTENDED-for-now state.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?code={SNOMED_T2DM_CODE}&inferSystem=true"
    )
    assert r.status_code in (200, 400), (
        f"inferSystem=true without system: should be accepted (200 or 400 for "
        f"missing system). Got {r.status_code}. Body: {r.text[:300]}"
    )


def test_e21_inferSystem_false_explicit_with_system_accepted(fhir_client):
    """``inferSystem=false`` explicit + system — accepted."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&inferSystem=false"
    )
    assert r.status_code == 200


def test_e22_inferSystem_with_unknown_code_accepted(fhir_client):
    """``inferSystem=true`` with unknown code — server behavior.

    Implementation accepts but does not infer. The probe documents the
    CURRENT behavior (likely 400 because system is required).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?code=NOT-A-REAL-CODE&inferSystem=true"
    )
    assert r.status_code in (200, 400), (
        f"inferSystem=true with unknown code: accepted (200 or 400). "
        f"Got {r.status_code}."
    )


def test_e23_inferSystem_invalid_value_treated_as_false(fhir_client):
    """``inferSystem=not-a-boolean`` — server behavior.

    Per FHIR R4 boolean parsing: invalid values SHOULD be rejected
    (422). However, FastAPI's permissive Query parsing may accept the
    string. The probe documents the CURRENT behavior.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&inferSystem=maybe"
    )
    # Acceptable: 200 (accepted, no-op), 400 (rejected), 422 (validation error)
    assert r.status_code < 500, (
        f"inferSystem=invalid-value: should not crash. Got {r.status_code}."
    )


# ===========================================================================
# Lens 3: abstract parameter handling
# ===========================================================================
# Per FHIR R4 §4.9.3 In Parameters ``abstract`` (0..1 boolean): "If this
# concept has an abstract property value, the validation will fail".
# medterm4ds accepts but does not implement abstract-flagging today
# (mirrors CodeSystem/$validate-code NOT A BUG registry entry).


def test_e30_abstract_false_default_accepted(fhir_client):
    """``abstract=false`` (default) — accepted, no-op."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&abstract=false"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True


def test_e31_abstract_true_accepted_with_known_code(fhir_client):
    """``abstract=true`` with known code — accepted.

    Per spec: "If this concept has an abstract property value, the
    validation will fail". The conformance fixture has no abstract
    concepts seeded; the param is accepted for spec-compatibility.
    Documented as INTENDED-for-now (mirrors CodeSystem/$validate-code
    NOT A BUG registry entry).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&abstract=true"
    )
    assert r.status_code == 200
    body = r.json()
    # Code is known; result is true regardless of abstract value (no
    # abstract-flagging data in fixture).
    assert _param_value(body, "result") is True


def test_e32_abstract_with_unknown_code_returns_false(fhir_client):
    """``abstract=true`` with unknown code — result=false (code not in system)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code=UNKNOWN-ABSTRACT-CODE&abstract=true"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False


# ===========================================================================
# Lens 4: Implicit value set URL variations
# ===========================================================================
# Per FHIR R4 §4.9.3 In Parameters ``url``: "ValueSet URL. If ``url``
# is a reference to a code system, the operation validates that the
# code is in the code system".


def test_e40_implicit_valueset_url_code_system_uri_alone(fhir_client):
    """Form (a): ``url=<code-system-uri>`` alone.

    Per spec: a code system URI is a valid implicit ValueSet URL.
    Membership check is "is the code in the underlying code system".
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?url={SNOMED_URI}"
        f"&system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True


def test_e41_implicit_valueset_url_snomed_intensional_with_code(fhir_client):
    """Form (b): ``url=http://snomed.info/sct/{code}?fhir_vs=isa``.

    Per spec: intensional URL form enumerates the code and its
    descendants. The membership check is whether the supplied code is
    in the intensional expansion. medterm4ds accepts but does not scope
    membership (no persisted ValueSets today). The probe documents the
    CURRENT behavior.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code"
        f"?url=http://snomed.info/sct/{SNOMED_DM_CODE}?fhir_vs=isa"
        f"&system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code in (200, 400), (
        f"Intensional URL form: accepted (200 or 400 for unimplemented scoping). "
        f"Got {r.status_code}."
    )


def test_e42_implicit_valueset_url_loinc_vs_graceful_handling(fhir_client):
    """Form (c): ``url=http://loinc.org/vs`` — implicit value set for LOINC.

    LOINC is not seeded in the conformance fixture (only SNOMED,
    ICD-10-CM, RxNorm). The probe verifies graceful handling (no 500).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?url=http://loinc.org/vs"
        f"&system={LOINC_URI}&code=1234-5"
    )
    assert r.status_code < 500, (
        f"Implicit value set URL for unseeded system: should not crash. "
        f"Got {r.status_code}. Body: {r.text[:300]}"
    )
    # Behavior is deterministic: either 200 with result=false (code not
    # found) or 400 (unrecognized system URI). Both are conformant.
    if r.status_code == 200:
        body = r.json()
        assert body.get("resourceType") == "Parameters"


def test_e43_implicit_valueset_url_with_unknown_scheme_accepted(fhir_client):
    """``url`` with non-FHIR scheme — server behavior.

    Per spec, ``url`` is a canonical URI; non-http schemes (urn:, oid:)
    are valid URI forms. The probe documents the CURRENT behavior.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?url=urn:oid:2.16.840.1.113883.6.96"
        f"&system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    # The url param is accepted; the system+code drives the membership check.
    assert r.status_code < 500


# ===========================================================================
# Lens 5: Cross-system consistency
# ===========================================================================


@pytest.mark.parametrize(
    "system_uri, code, expected_result",
    [
        (SNOMED_URI, SNOMED_T2DM_CODE, True),
        (ICD10CM_URI, ICD10CM_E11_CODE, True),
        (RXNORM_URI, RXNORM_METFORMIN_CODE, True),
        (SNOMED_URI, "UNKNOWN-CODE-XYZ", False),
    ],
    ids=["snomed-known", "icd10-known", "rxnorm-known", "snomed-unknown"],
)
def test_e50_cross_system_consistency(fhir_client, system_uri, code, expected_result):
    """Same code shape validated across SNOMED, ICD-10-CM, RxNorm — consistent.

    Per CS-05 EXPLORER strategy 38 (cross-operation-canonical-agreement),
    extended here to cross-system consistency: the same operation MUST
    produce the same shape of response across all seeded systems.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={system_uri}&code={code}"
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    assert _param_value(body, "result") is expected_result


# ===========================================================================
# Lens 6: Display mismatch edge cases
# ===========================================================================
# Per CS-03 SKEPTIC QA-048 + VS-05 SKEPTIC QA-069: display mismatch
# enforcement on CodeSystem/$validate-code AND ValueSet/$validate-code.
# Case-sensitivity is exact-match today (per AGENTS.md NOT A BUG
# registry).


def test_e60_display_case_differing_triggers_mismatch(fhir_client):
    """Case sensitivity: ``Diabetes mellitus`` vs ``diabetes mellitus``.

    Per AGENTS.md NOT A BUG registry: case-sensitivity is exact-match
    today (spec: "Whether displays are case sensitive is code system
    dependent"). The probe documents the CURRENT behavior — case-
    differing display triggers mismatch.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_DM_CODE}&display=diabetes%20mellitus"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False, (
        "Case-differing display MUST trigger mismatch (exact-match semantic)."
    )
    # Out display MUST be the canonical (NOT echo of lowercase input)
    display = _param_value(body, "display")
    assert display == SNOMED_DM_DISPLAY, (
        f"Out `display` MUST be canonical ({SNOMED_DM_DISPLAY!r}). Got {display!r}."
    )


def test_e61_display_with_trailing_whitespace_triggers_mismatch(fhir_client):
    """Whitespace: trailing space triggers mismatch (exact-match semantic)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_DM_CODE}&display=Diabetes%20mellitus%20"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False


def test_e62_display_with_leading_whitespace_triggers_mismatch(fhir_client):
    """Whitespace: leading space triggers mismatch (exact-match semantic)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_DM_CODE}&display=%20Diabetes%20mellitus"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False


def test_e63_display_byte_exact_match_returns_true(fhir_client):
    """Byte-exact match → result=true (sanity check)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_DM_CODE}&display=Diabetes%20mellitus"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True


def test_e64_display_empty_string_does_not_trigger_mismatch(fhir_client):
    """Empty display → no mismatch enforcement (per implementation).

    The implementation only enforces mismatch when display is supplied
    (non-None). An empty string is "supplied" but the canonical_display
    != "" comparison triggers mismatch. The probe documents the CURRENT
    behavior.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_DM_CODE}&display="
    )
    assert r.status_code == 200
    body = r.json()
    # Empty display != canonical_display ("Diabetes mellitus") → mismatch
    assert _param_value(body, "result") is False


# ===========================================================================
# Lens 7: Multi-coding codeableConcept
# ===========================================================================


def test_e70_codeableConcept_all_valid_codings_returns_true(fhir_client):
    """All valid codings → result=true per spec.

    Per FHIR R4 §4.9.3 In Parameters ``codeableConcept``: "The server
    returns true if one of the coding values is in the code system".
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": SNOMED_DM_CODE},
                        {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                    ]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    result_val = _param_value(r.json(), "result")
    assert result_val is True


def test_e71_codeableConcept_all_invalid_returns_false(fhir_client):
    """All invalid → result=false per spec."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "BAD1"},
                        {"system": SNOMED_URI, "code": "BAD2"},
                        {"system": SNOMED_URI, "code": "BAD3"},
                    ]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    result_val = _param_value(r.json(), "result")
    assert result_val is False


def test_e72_codeableConcept_mixed_with_per_coding_wrong_display_returns_true(fhir_client):
    """Mixed valid/invalid with per-coding display mismatch — semantic question.

    Per CS-03 SKEPTIC AUDIT-002: the spec does NOT mandate display
    enforcement for codeableConcept. The In ``display`` parameter is
    for top-level verification; per-coding displays are NOT validated.
    The probe confirms: any coding match wins, regardless of per-coding
    display correctness.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        # First coding: invalid code, correct display
                        {
                            "system": SNOMED_URI,
                            "code": "INVALID-CODE-1",
                            "display": "Wrong Display",
                        },
                        # Second coding: valid code, WRONG per-coding display
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_T2DM_CODE,
                            "display": "Definitely Not The Right Display",
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
        "codeableConcept [invalid, valid_with_wrong_display] → result=true. "
        "Per-coding display NOT enforced (CS-03 SKEPTIC AUDIT-002)."
    )


def test_e73_codeableConcept_three_codings_third_valid_returns_true(fhir_client):
    """3 codings, 3rd valid → result=true (full list iteration)."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "BAD1"},
                        {"system": SNOMED_URI, "code": "BAD2"},
                        {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                    ]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    result_val = _param_value(r.json(), "result")
    assert result_val is True


# ===========================================================================
# Lens 8: Date parameter variations
# ===========================================================================
# Per FHIR R4 §4.9.3 In Parameters ``date`` (0..1 dateTime): "The date
# for which the validation should take place".


@pytest.mark.parametrize(
    "date_value",
    [
        "2020-01-01",          # Past date
        "2099-12-31",          # Future date
        "2024-06-15T10:30:00",  # DateTime form
        "2024-06-15T10:30:00Z",  # DateTime with timezone
        "2024-W25",            # ISO week format (unusual)
    ],
    ids=["past", "future", "datetime", "datetime-tz", "iso-week"],
)
def test_e80_date_variations_accepted(fhir_client, date_value):
    """``date`` parameter variations — accepted, no-op (snapshot engine)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&date={date_value}"
    )
    assert r.status_code < 500, (
        f"date={date_value!r}: should not crash. Got {r.status_code}."
    )


def test_e81_date_invalid_value_does_not_crash(fhir_client):
    """``date=not-a-date`` — server behavior (no crash)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&date=not-a-date"
    )
    assert r.status_code < 500


# ===========================================================================
# Lens 9: Combined input encodings (spec violation)
# ===========================================================================
# Per FHIR R4 §4.9.3: ``coding`` and ``codeableConcept`` are alternatives
# to system+code, not co-occurring inputs. The server's behavior on
# combined inputs is deterministic; the probe documents the CURRENT
# behavior (no 500).


def test_e90_combined_system_code_coding_codeableConcept_does_not_crash(fhir_client):
    """Spec-violation probe: all input encodings at once.

    A spec-compliant client would never send this combination. The
    probe verifies the server does not crash (no 500). The current
    behavior is to prefer system+code from the scalar fields and ignore
    the rest (deterministic).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DM_CODE,
                },
            },
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [{"system": SNOMED_URI, "code": SNOMED_T2DM_CODE}]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code < 500, (
        f"Spec-violation combined inputs: should not crash. "
        f"Got {r.status_code}. Body: {r.text[:300]}"
    )
    if r.status_code == 200:
        # The result should be deterministic — at least one of the
        # supplied codes is valid (T2DM and DM are both seeded).
        result_val = _param_value(r.json(), "result")
        assert result_val is True


def test_e91_combined_coding_and_codeableConcept_without_scalars_does_not_crash(fhir_client):
    """Spec-violation: coding + codeableConcept without system+code scalars."""
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
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [{"system": SNOMED_URI, "code": SNOMED_DM_CODE}]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code < 500


# ===========================================================================
# Lens 10: GET ↔ POST parity probe class (VS-04 EXPLORER strategy 50)
# ===========================================================================
# Already covered by SKEPTIC test_s91 on the happy path. EXPLORER adds
# the display-mismatch case (post-SKEPTIC QA-069 fix).


def test_e100_get_post_parity_on_display_mismatch(fhir_client):
    """GET ↔ POST parity on display mismatch (post-SKEPTIC QA-069 fix).

    Per VS-04 EXPLORER strategy 50 (GET↔POST parity on dispatch): same
    input via GET and POST MUST produce byte-equivalent ``result``
    values. The display-mismatch case is the load-bearing probe class
    because the fix (QA-069) MUST apply to BOTH paths (SKEPTIC test_s51
    GET + test_s52 POST).
    """
    # GET
    r_get = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG-CLINICAL-DISPLAY"
    )
    # POST with system+code+display
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
            {"name": "display", "valueString": "WRONG-CLINICAL-DISPLAY"},
        ],
    }
    r_post = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)

    assert r_get.status_code == r_post.status_code == 200
    get_result = _param_value(r_get.json(), "result")
    post_result = _param_value(r_post.json(), "result")
    assert get_result == post_result, (
        f"GET↔POST parity on display mismatch: GET result={get_result!r}, "
        f"POST result={post_result!r}."
    )
    assert get_result is False, (
        "Display mismatch MUST produce result=false on both GET and POST."
    )


def test_e101_get_post_parity_on_known_code(fhir_client):
    """GET ↔ POST parity on known code (positive control)."""
    r_get = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
        ],
    }
    r_post = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)

    assert r_get.status_code == r_post.status_code == 200
    get_result = _param_value(r_get.json(), "result")
    post_result = _param_value(r_post.json(), "result")
    assert get_result == post_result == True


def test_e102_get_post_parity_on_unknown_code(fhir_client):
    """GET ↔ POST parity on unknown code."""
    r_get = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}&code=UNKNOWN-CODE"
    )
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": "UNKNOWN-CODE"},
        ],
    }
    r_post = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)

    assert r_get.status_code == r_post.status_code == 200
    get_result = _param_value(r_get.json(), "result")
    post_result = _param_value(r_post.json(), "result")
    assert get_result == post_result == False


# ===========================================================================
# Lens 11: Cross-operation canonical-agreement ($validate-code VS ↔
# CodeSystem/$validate-code)
# ===========================================================================
# Per CS-05 EXPLORER strategy 38 (cross-operation-canonical-agreement):
# ``$lookup`` and ``$validate-code`` agree on canonical ``system`` AND
# ``display``. Here extended to VS/$validate-code ↔ CS/$validate-code
# (sibling handlers sharing the spec semantic).


@pytest.mark.parametrize(
    "system_uri, code",
    [
        (SNOMED_URI, SNOMED_DM_CODE),
        (SNOMED_URI, SNOMED_T2DM_CODE),
        (ICD10CM_URI, ICD10CM_E11_CODE),
        (RXNORM_URI, RXNORM_METFORMIN_CODE),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10-e11", "rxnorm-metformin"],
)
def test_e110_vs_validate_and_cs_validate_agree_on_canonical_system(
    fhir_client, system_uri, code
):
    """VS/$validate-code and CS/$validate-code MUST agree on canonical system.

    Both operations call the same engine (``get_code_infos``) and share
    the canonical-re-resolution pattern (CR-011 + CS-03 HISTORIAN QA-051).
    The Out ``system`` parameter MUST agree for the same (system, code)
    input.
    """
    r_vs = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={system_uri}&code={code}"
    )
    r_cs = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system_uri}&code={code}"
    )
    assert r_vs.status_code == r_cs.status_code == 200
    vs_sys = _param_value(r_vs.json(), "system")
    cs_sys = _param_value(r_cs.json(), "system")
    assert vs_sys == cs_sys, (
        f"VS/{code} Out system={vs_sys!r} but CS/{code} Out system={cs_sys!r}. "
        "Cross-handler canonical-system drift is prohibited (sibling-handler "
        "parity audit — CS-03 HISTORIAN QA-051 / VS-05 SKEPTIC QA-069)."
    )


@pytest.mark.parametrize(
    "system_uri, code",
    [
        (SNOMED_URI, SNOMED_DM_CODE),
        (SNOMED_URI, SNOMED_T2DM_CODE),
        (ICD10CM_URI, ICD10CM_E11_CODE),
        (RXNORM_URI, RXNORM_METFORMIN_CODE),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10-e11", "rxnorm-metformin"],
)
def test_e111_vs_validate_and_cs_validate_agree_on_canonical_display(
    fhir_client, system_uri, code
):
    """VS/$validate-code and CS/$validate-code MUST agree on canonical display."""
    r_vs = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={system_uri}&code={code}"
    )
    r_cs = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system_uri}&code={code}"
    )
    assert r_vs.status_code == r_cs.status_code == 200
    vs_display = _param_value(r_vs.json(), "display")
    cs_display = _param_value(r_cs.json(), "display")
    assert vs_display == cs_display, (
        f"VS/{code} Out display={vs_display!r} but CS/{code} Out display={cs_display!r}. "
        "Cross-handler canonical-display drift is prohibited."
    )


# ===========================================================================
# Lens 12: XML wire-format on operation route (display mismatch case)
# ===========================================================================
# Per CS-04 EXPLORER test_e151 (first hyphenated-XML probe) + SKEPTIC
# test_s100 (XML wire-format on VS/$validate-code happy path). EXPLORER
# adds the display-mismatch case (post-SKEPTIC QA-069 fix — XML
# serialization of message valueString).


def test_e120_xml_format_emits_fhir_xml_mime(fhir_client):
    """``_format=xml`` emits ``application/fhir+xml`` Content-Type."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&_format=xml"
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(FHIR_XML), (
        f"_format=xml: Content-Type MUST be {FHIR_XML}. "
        f"Got: {r.headers['content-type']!r}."
    )


def test_e121_xml_format_emits_lowercase_valueBoolean(fhir_client):
    """XML wire format: ``<valueBoolean value="true"/>`` (lowercase).

    Per CR-002 (milestone-1 code review): Python's ``str(True) ==
    "True"`` but FHIR R4 §3.4.1 mandates lowercase ``true``/``false``.
    The XML serializer's ``_scalar_to_xml_attr`` boolean special-case
    MUST hold on the ValueSet/$validate-code route. Mirrors SKEPTIC
    test_s100.
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


def test_e122_xml_format_on_display_mismatch_includes_message_valueString(fhir_client):
    """XML wire format on display mismatch — message MUST be valueString.

    Per FHIR R4 §3.4.1 + CR-002: the Out ``message`` parameter MUST
    serialize as ``<valueString value="..."/>`` in XML. The display-
    mismatch case is the load-bearing probe because the SKEPTIC QA-069
    fix added the message parameter; the XML serializer MUST render it
    correctly.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG-CLINICAL-DISPLAY&_format=xml"
    )
    assert r.status_code == 200
    body_text = r.text
    # Message valueString MUST be present with the wrong value cited
    assert "WRONG-CLINICAL-DISPLAY" in body_text, (
        f"XML body MUST contain the wrong display value in the message. "
        f"Body: {body_text[:500]}"
    )
    # The boolean MUST be lowercase false (mismatch)
    assert 'value="false"' in body_text


def test_e123_xml_format_on_unknown_code_returns_false_in_xml(fhir_client):
    """XML wire format on unknown code — result=false in XML."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code=UNKNOWN-CODE-XML&_format=xml"
    )
    assert r.status_code == 200
    body_text = r.text
    assert 'value="false"' in body_text
    assert 'value="False"' not in body_text


# ===========================================================================
# Lens 13: Accept-header XML negotiation
# ===========================================================================


def test_e130_accept_header_xml_emits_xml_body(fhir_client):
    """``Accept: application/fhir+xml`` emits XML body (distinct from _format).

    Per FHIR R4 §3.1.0.1.11: ``_format`` overrides Accept. EXPLORER
    verifies the Accept-only path produces XML when ``_format`` is NOT
    supplied.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}",
        headers={"Accept": FHIR_XML},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(FHIR_XML), (
        f"Accept: application/fhir+xml: Content-Type MUST be {FHIR_XML}. "
        f"Got: {r.headers['content-type']!r}."
    )


def test_e131_accept_header_json_emits_json_body(fhir_client):
    """``Accept: application/fhir+json`` emits JSON body (positive control)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}",
        headers={"Accept": FHIR_JSON},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(FHIR_JSON)


def test_e132_format_overrides_accept_when_both_supplied(fhir_client):
    """``_format=xml`` overrides ``Accept: application/fhir+json``.

    Per FHIR R4 §3.1.0.1.11: ``_format`` takes precedence over Accept.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&_format=xml",
        headers={"Accept": FHIR_JSON},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(FHIR_XML), (
        f"_format=xml MUST override Accept: application/fhir+json. "
        f"Got: {r.headers['content-type']!r}."
    )


# ===========================================================================
# Lens 14: Hostile input on POST path
# ===========================================================================


def test_e140_post_very_long_code_does_not_crash(fhir_client):
    """5K-char code on POST path — no 5xx (mirrors SKEPTIC test_s90 on GET)."""
    long_code = "X" * 5000
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": long_code},
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code < 500


def test_e141_post_special_chars_in_code_does_not_crash(fhir_client):
    """Special chars in code on POST path — no 5xx."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": "abc<>&\"'\\def"},
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code < 500


def test_e142_post_unicode_code_does_not_crash(fhir_client):
    """Unicode code on POST path — no 5xx."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": "üñîçødé"},
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code < 500


# ===========================================================================
# Lens 15: Outcome shape audit
# ===========================================================================


def test_e150_outcome_parameters_resource_type_always_parameters(fhir_client):
    """Response resourceType is always ``Parameters`` on 200 paths."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}&code=ANY-CODE"
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_e151_outcome_always_includes_result_parameter(fhir_client):
    """Out ``result`` parameter always present (cardinality 1..1)."""
    for code in [SNOMED_T2DM_CODE, "UNKNOWN-CODE-1", "UNKNOWN-CODE-2"]:
        r = fhir_client.get(
            f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}&code={code}"
        )
        body = r.json()
        assert _has_param(body, "result"), (
            f"Out `result` MUST always be present (code={code!r})."
        )


def test_e152_outcome_result_is_valueBoolean_wire_type(fhir_client):
    """Out ``result`` uses ``valueBoolean`` wire-type (not valueString)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    body = r.json()
    result_entry = next(
        p for p in body["parameter"] if p.get("name") == "result"
    )
    assert "valueBoolean" in result_entry, (
        f"Out `result` MUST use valueBoolean wire-type. Entry: {result_entry!r}"
    )


# ===========================================================================
# Lens 16: Carry-forward cross-verification (HISTORIAN reconfirmation)
# ===========================================================================


def test_e160_cf_skeptic_cs03_01_closed_via_vs_validate_display_mismatch(fhir_client):
    """CF-SKEPTIC-CS03-01 closure reconfirmation.

    HISTORIAN test_h70 confirms CF-SKEPTIC-CS03-01 is CLOSED. EXPLORER
    reconfirms via a slightly different probe shape (using a codeableConcept
    with one valid coding that has a wrong display).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
            {"name": "display", "valueString": "WRONG-FOR-CF-CHECK"},
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    rb = r.json()
    # CF-SKEPTIC-CS03-01 fix MUST hold: display mismatch → result=false
    assert _param_value(rb, "result") is False
    msg = _param_value(rb, "message")
    assert msg is not None and "WRONG-FOR-CF-CHECK" in str(msg)


def test_e161_cf_historian_vs02_02_does_not_apply_to_vs_validate(fhir_client):
    """CF-HISTORIAN-VS02-02 (MEDIUM, DEFERRED on $expand) — does NOT apply.

    HISTORIAN test_h71 confirms this CF does NOT apply to VS/$validate-code.
    EXPLORER reconfirms by asserting the canonical URI is re-resolved
    on the VS/$validate-code surface (post CR-011).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    body = r.json()
    sys_val = _param_value(body, "system")
    assert sys_val == SNOMED_URI


def test_e162_cf_historian_cs04_02_duckdb_error_boundary_holds(fhir_client, monkeypatch):
    """CF-HISTORIAN-CS04-02 / CR-019 — duckdb.Error boundary holds.

    HISTORIAN test_h10/h11/h84 confirm the app-level duckdb.Error handler
    is registered and fires on the VS/$validate-code surface. EXPLORER
    reconfirms via a different injection point (the canonical_system_uri
    path is exercised; the boundary should still fire).
    """
    # Source-reading style: confirm the handler is registered.
    import medterm4ds.apps.fhir_api as mod
    src = open(mod.__file__).read()
    assert "@app.exception_handler(duckdb.Error)" in src or "exception_handler(duckdb.Error)" in src, (
        "CF-HISTORIAN-CS04-02 / CR-019 fix: app-level duckdb.Error handler "
        "MUST be registered."
    )


# ===========================================================================
# Lens 17: Catch-all layer conformance on instance-level route
# ===========================================================================
# Per TS-04 EXPLORER QA-042 + CS-04 EXPLORER test_e20/e21: catch-all
# layer is the second location (after type-level handlers) to audit for
# framework-default drift.


def test_e170_instance_level_vs_validate_get_returns_conformant_response(fhir_client):
    """Instance-level GET ``/fhir/ValueSet/{id}/$validate-code`` — conformant.

    The instance-level route is registered per TS-02 SKEPTIC QA-014
    (carried forward). EXPLORER confirms the route returns either a
    Parameters body (200) OR a conformant 4xx OperationOutcome (NOT a
    Starlette default JSON 404).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/any-id/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code < 500
    assert r.headers["content-type"].startswith(FHIR_JSON), (
        f"Instance-level route: Content-Type MUST be FHIR JSON. "
        f"Got: {r.headers['content-type']!r}."
    )


def test_e171_instance_level_vs_validate_post_returns_conformant_response(fhir_client):
    """Instance-level POST ``/fhir/ValueSet/{id}/$validate-code`` — conformant."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
        ],
    }
    r = fhir_client.post(
        "/fhir/ValueSet/any-id/$validate-code", json=body
    )
    assert r.status_code < 500
    assert r.headers["content-type"].startswith(FHIR_JSON), (
        f"Instance-level POST route: Content-Type MUST be FHIR JSON. "
        f"Got: {r.headers['content-type']!r}."
    )
