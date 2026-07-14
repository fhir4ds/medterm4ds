"""TERMINOLOGIST iteration CS-03 — clinical/terminological correctness.

Spec: https://build.fhir.org/codesystem-operation-validate-code.html
       (canonical R4: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html)

TERMINOLOGIST lens for CS-03 (CodeSystem $validate-code Operation):

1. **Display mismatch message clinical correctness**:
   The current message is `'The display "X" is incorrect'`. The spec example
   (https://hl7.org/fhir/R4/codesystem-operation-validate-code.html "Response")
   gives the canonical shape:
       {"name": "message", "valueString": "The display \"test\" is incorrect"},
       {"name": "display", "valueString": "Bicarbonate [Moles/volume] in Serum"}
   The wrong value lives in `message`; the canonical lives in the SEPARATE
   `display` Out parameter. Probes verify the implementation matches the
   spec example shape exactly.

2. **Canonical display returned on mismatch**:
   The Out `display` MUST be the engine's canonical STR (per TS-02
   TERMINOLOGIST QA-029). When the client supplies a wrong display, the
   response's `display` parameter MUST be the canonical (so a clinician
   sees the correct term), NEVER the client's wrong string echoed back.
   Cross-cutting: HISTORIAN QA-051 canonical URI re-resolution ensures
   Out `system` is also the canonical (not the client's alias/trailing-slash).

3. **codeableConcept multi-coding semantics**:
   The spec mandates: "The server returns true if one of the coding values
   is in the code system". When the first coding has a valid code but a
   wrong display, the spec permits (per SKEPTIC AUDIT-002) `result=true`
   since the code exists in the system. When a second coding has a valid
   code + correct display, the result must also be `true` (any match wins).
   Verify the implementation handles both cases without leaking the wrong
   display into the Out `display`.

4. **Code-system URI round-trips**:
   For a code X in system Y: `$validate-code` returns true. The Out `system`
   parameter returned MUST match what `$lookup` returns for the same code.
   The two operations MUST agree on canonical URIs.

5. **Cross-source clinical consistency**:
   The conformance fixture seeds: SNOMED (2 codes), ICD-10-CM (1 code),
   RxNorm (1 code). Every supported source validates with result=true for
   known codes and emits display + canonical system URI. HCPCS, CVX, CPT
   are not seeded in the conformance fixture (no production probes; this
   iteration documents the cross-source consistency check on the seeded
   surface only).

6. **Message field when code unknown**:
   When the code is unknown, the message MUST be clinically clear. Current
   text: `"Code X is not valid in code system Y."` This is a clinically
   actionable message — it tells the clinician (a) the code is not valid
   (b) which code system was consulted.

7. **Carry-forward CF-SKEPTIC-CS03-01**:
   ValueSet/$validate-code likely has the same display mismatch bug. Per
   the iteration prompt, TERMINOLOGIST notes this for VS-* chunks (out
   of CS-03 scope). The probe documents the carry-forward as INTENDED here.
"""

from __future__ import annotations

import pytest

# Spec sources:
#   https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
#   https://hl7.org/fhir/R4/codesystem-operation-lookup.html
#
# Conformance fixture seeds (per tests/fhir_conformance/conftest.py):
#   ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),
#   ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
#   ("E11",     "HT", "Type 2 diabetes mellitus", "AE11",     "N", "ICD10CM",      "C0011847"),
#   ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
#
# Canonical FHIR R4 URIs (per SYSTEM_TO_FHIR_URI registry):
SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

# Seeded codes + canonical displays:
SNOMED_DM_CODE = "73211009"           # canonical display: "Diabetes mellitus"
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM_CODE = "44054006"          # canonical display: "Type 2 diabetes mellitus"
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
ICD10CM_E11_CODE = "E11"              # canonical display: "Type 2 diabetes mellitus"
ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_METFORMIN_CODE = "860975"      # canonical display: "24 HR metformin 500 MG Oral Tablet"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"


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


# ===========================================================================
# Lens 1: Display mismatch message clinical correctness
# ===========================================================================

def test_t01_display_mismatch_message_cites_wrong_value(fhir_client):
    """Spec example: message format = `The display "X" is incorrect`.

    The message cites the wrong (client-supplied) display value so a
    clinician reviewing the operation outcome can see what they sent.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG-CLINICAL-DISPLAY"
    )
    assert r.status_code == 200
    body = r.json()
    msg = _param_value(body, "message")
    assert isinstance(msg, str)
    assert "WRONG-CLINICAL-DISPLAY" in msg, (
        "Message MUST cite the client-supplied wrong display value so the "
        "clinician can identify what was sent."
    )
    assert "incorrect" in msg.lower(), (
        "Message MUST use the spec example word 'incorrect'."
    )


def test_t02_display_mismatch_canonical_lives_in_separate_display_param(fhir_client):
    """Spec example shape: canonical display lives in the Out `display`
    parameter (NOT in the message). Per
    https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
    "Response":
        {"name": "message", "valueString": "The display \"test\" is incorrect"},
        {"name": "display", "valueString": "Bicarbonate [Moles/volume] in Serum"}

    The clinician sees BOTH the message (what they sent was wrong) AND
    the display parameter (the canonical/preferred term the engine holds).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG-CLINICAL-DISPLAY"
    )
    assert r.status_code == 200
    body = r.json()
    display_out = _param_value(body, "display")
    assert isinstance(display_out, str)
    # Canonical display (Type 2 diabetes mellitus) MUST be in the Out `display`
    assert SNOMED_T2DM_DISPLAY in display_out, (
        f"Out `display` MUST carry the canonical preferred term; "
        f"got {display_out!r}."
    )


def test_t03_display_mismatch_message_does_not_leak_internal_engine_state(fhir_client):
    """Clinical message MUST NOT leak internal engine vocabulary (source
    labels like 'SNOMEDCT_US', AUI identifiers, CUI codes, etc.). The
    message should be human-readable in a clinical workflow.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=wrong"
    )
    assert r.status_code == 200
    body = r.json()
    msg = _param_value(body, "message") or ""
    # Internal engine vocabulary that should NEVER appear in clinical message.
    forbidden_tokens = ["SNOMEDCT_US", "AUI", "C0011847", "C0011849",
                        "CodeRef(", "engine="]
    for tok in forbidden_tokens:
        assert tok not in msg, (
            f"Message MUST NOT leak internal engine state {tok!r}: got {msg!r}"
        )


# ===========================================================================
# Lens 2: Canonical display returned on mismatch wins over client input
# ===========================================================================

def test_t10_canonical_display_wins_over_client_display_on_mismatch(fhir_client):
    """Per TS-02 TERMINOLOGIST QA-029 — when both client display AND engine
    canonical exist, the Out `display` MUST be the engine canonical. The
    CS-03 SKEPTIC QA-048 display mismatch fix preserves this precedence.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False
    display_out = _param_value(body, "display")
    assert display_out == SNOMED_T2DM_DISPLAY, (
        f"Out `display` MUST be the engine canonical '{SNOMED_T2DM_DISPLAY}', "
        f"got {display_out!r}."
    )


def test_t11_canonical_display_present_when_no_client_display_supplied(fhir_client):
    """When the client does NOT supply a display parameter, the engine
    canonical MUST still be returned in Out `display` (for the clinician
    to see what the engine holds)."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True
    display_out = _param_value(body, "display")
    assert display_out == SNOMED_T2DM_DISPLAY, (
        f"Out `display` MUST be the engine canonical; got {display_out!r}."
    )


def test_t12_canonical_display_for_known_code_no_message(fhir_client):
    """When result=true (code known, no display mismatch), the server
    SHOULD NOT emit a display-mismatch message. A message at result=true
    is permitted only for hints/warnings per spec."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={SNOMED_T2DM_DISPLAY.replace(' ', '+')}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True
    # No display-mismatch message when display matches canonical
    msg = _param_value(body, "message")
    if msg is not None:
        assert "incorrect" not in str(msg).lower(), (
            "Message MUST NOT say 'incorrect' when display matches canonical."
        )


# ===========================================================================
# Lens 3: codeableConcept multi-coding semantics
# ===========================================================================

def test_t20_codeable_concept_first_coding_valid_wrong_display_returns_true(fhir_client):
    """codeableConcept with first coding: valid code, wrong display.

    Per SKEPTIC AUDIT-002: the spec doesn't mandate display enforcement
    on codeableConcept (display is an In parameter to verify against the
    top-level code, not against per-coding displays). When ANY coding
    matches a known code in the system, result=true.
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
                            {
                                "system": SNOMED_URI,
                                "code": SNOMED_T2DM_CODE,
                                "display": "wrong display",
                            }
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True, (
        "codeableConcept with valid code → result=true regardless of "
        "per-coding display (spec permits no display enforcement on CC)."
    )


def test_t21_codeable_concept_second_coding_valid_correct_display_returns_true(fhir_client):
    """codeableConcept with TWO codings: first (invalid code, wrong display),
    second (valid code, correct display). Spec mandates "any coding matches
    → result=true". The second coding fully validates; result MUST be true.
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
                            {
                                "system": SNOMED_URI,
                                "code": "NONEXISTENT_QA",
                                "display": "wrong display",
                            },
                            {
                                "system": SNOMED_URI,
                                "code": SNOMED_T2DM_CODE,
                                "display": SNOMED_T2DM_DISPLAY,
                            },
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True, (
        "codeableConcept: at least one valid coding → result=true "
        "(spec: 'The server returns true if one of the coding values is "
        "in the code system')."
    )
    # The Out `display` for a multi-coding match is the matched coding's
    # canonical (not the first coding's wrong display).
    display_out = _param_value(body, "display")
    assert display_out == SNOMED_T2DM_DISPLAY, (
        f"Out `display` MUST reflect the matched coding's canonical display; "
        f"got {display_out!r}."
    )


def test_t22_codeable_concept_no_valid_coding_message_is_clinically_clear(fhir_client):
    """When ALL codings are invalid, the message MUST be clinically clear
    about what was checked. Current message:
    "None of the codings in the codeableConcept are in the code system."
    A clinician reading this knows: the codeableConcept's codings are
    not recognized by the server.
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
                            {"system": SNOMED_URI, "code": "BOGUS1"},
                            {"system": SNOMED_URI, "code": "BOGUS2"},
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False
    msg = _param_value(body, "message")
    assert isinstance(msg, str)
    assert "codeableConcept" in msg.lower() or "coding" in msg.lower(), (
        f"Message MUST mention the codeableConcept/coding context: {msg!r}"
    )


# ===========================================================================
# Lens 4: Code-system URI round-trips ($validate-code ↔ $lookup)
# ===========================================================================

def test_t30_validate_and_lookup_agree_on_canonical_system_uri_snomed(fhir_client):
    """For a code X in system Y: $validate-code Out `system` MUST equal
    $lookup Out `system`. The two operations MUST NOT disagree on the
    canonical URI for the same code.
    """
    rv = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    rl = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    assert rv.status_code == 200 and rl.status_code == 200
    v_sys = _param_value(rv.json(), "system")
    l_sys = _param_value(rl.json(), "system")
    assert v_sys == l_sys, (
        f"$validate-code and $lookup MUST agree on canonical system URI; "
        f"validate={v_sys!r} lookup={l_sys!r}."
    )
    assert v_sys == SNOMED_URI


def test_t31_validate_and_lookup_agree_on_canonical_system_uri_icd10cm(fhir_client):
    """URI round-trip: $validate-code Out `system` = $lookup Out `system`
    for ICD-10-CM E11."""
    rv = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={ICD10CM_URI}"
        f"&code={ICD10CM_E11_CODE}"
    )
    rl = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={ICD10CM_URI}&code={ICD10CM_E11_CODE}"
    )
    assert rv.status_code == 200 and rl.status_code == 200
    v_sys = _param_value(rv.json(), "system")
    l_sys = _param_value(rl.json(), "system")
    assert v_sys == l_sys == ICD10CM_URI


def test_t32_validate_and_lookup_agree_on_canonical_system_uri_rxnorm(fhir_client):
    """URI round-trip: $validate-code Out `system` = $lookup Out `system`
    for RxNorm 860975."""
    rv = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={RXNORM_URI}"
        f"&code={RXNORM_METFORMIN_CODE}"
    )
    rl = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={RXNORM_URI}&code={RXNORM_METFORMIN_CODE}"
    )
    assert rv.status_code == 200 and rl.status_code == 200
    v_sys = _param_value(rv.json(), "system")
    l_sys = _param_value(rl.json(), "system")
    assert v_sys == l_sys == RXNORM_URI


def test_t33_validate_and_lookup_agree_on_canonical_display(fhir_client):
    """Cross-operation agreement: $validate-code Out `display` MUST equal
    $lookup Out `display` for the same code (the engine canonical)."""
    for system_uri, code in [
        (SNOMED_URI, SNOMED_T2DM_CODE),
        (ICD10CM_URI, ICD10CM_E11_CODE),
        (RXNORM_URI, RXNORM_METFORMIN_CODE),
    ]:
        rv = fhir_client.get(
            f"/fhir/CodeSystem/$validate-code?system={system_uri}&code={code}"
        )
        rl = fhir_client.get(
            f"/fhir/CodeSystem/$lookup?system={system_uri}&code={code}"
        )
        assert rv.status_code == 200 and rl.status_code == 200, (
            f"system={system_uri} code={code} validate_status={rv.status_code} "
            f"lookup_status={rl.status_code}"
        )
        v_disp = _param_value(rv.json(), "display")
        l_disp = _param_value(rl.json(), "display")
        assert v_disp == l_disp, (
            f"$validate-code and $lookup MUST agree on canonical display; "
            f"system={system_uri} validate={v_disp!r} lookup={l_disp!r}."
        )


# ===========================================================================
# Lens 4 (cont.): alias / trailing-slash canonical re-resolution on $validate-code
# ===========================================================================

def test_t34_validate_system_out_uses_canonical_not_oid_alias_snomed(fhir_client):
    """When client sends a SNOMED OID alias (`urn:oid:2.16.840.1.113883.6.96`),
    the Out `system` MUST be the canonical `http://snomed.info/sct`. This is
    the CS-03 HISTORIAN QA-051 fix (5th instance of client-input-as-canonical
    drift)."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code?system=urn:oid:2.16.840.1.113883.6.96"
        f"&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code == 200
    sys_out = _param_value(r.json(), "system")
    assert sys_out == SNOMED_URI, (
        f"Out `system` MUST be canonical {SNOMED_URI!r}, not the OID alias; "
        f"got {sys_out!r}."
    )


def test_t35_validate_system_out_strips_trailing_slash(fhir_client):
    """Trailing-slash system URI variant MUST resolve to canonical."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}/"
        f"&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code == 200
    sys_out = _param_value(r.json(), "system")
    assert sys_out == SNOMED_URI, (
        f"Out `system` MUST be canonical {SNOMED_URI!r} (no trailing slash); "
        f"got {sys_out!r}."
    )


# ===========================================================================
# Lens 5: Cross-source clinical consistency
# ===========================================================================

@pytest.mark.parametrize("system_uri, code, expected_display", [
    (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
    (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
    (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
    (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
])
def test_t40_validate_known_code_returns_true_for_each_supported_source(
    fhir_client, system_uri, code, expected_display
):
    """Cross-source consistency: every supported code in every supported
    source validates with result=true AND emits the canonical display.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system_uri}&code={code}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True, (
        f"Known code {code} in {system_uri} → result MUST be true."
    )
    display_out = _param_value(body, "display")
    assert display_out == expected_display, (
        f"Out `display` MUST be the clinically preferred term "
        f"{expected_display!r}; got {display_out!r}."
    )
    sys_out = _param_value(body, "system")
    assert sys_out == system_uri, (
        f"Out `system` MUST be canonical {system_uri!r}; got {sys_out!r}."
    )


def test_t41_unknown_code_returns_false_consistently_across_sources(fhir_client):
    """Cross-source: an unknown code in ANY supported source returns
    result=false with the same response shape."""
    for system_uri in [SNOMED_URI, ICD10CM_URI, RXNORM_URI]:
        r = fhir_client.get(
            f"/fhir/CodeSystem/$validate-code?system={system_uri}"
            f"&code=NOT-IN-ENGINE-QA"
        )
        assert r.status_code == 200, (
            f"unknown code in {system_uri} → 200 (not 5xx); "
            f"got {r.status_code}"
        )
        body = r.json()
        assert _param_value(body, "result") is False
        assert body.get("resourceType") == "Parameters"


# ===========================================================================
# Lens 6: Message field when code unknown — clinical clarity
# ===========================================================================

def test_t50_unknown_code_message_is_clinically_clear(fhir_client):
    """When code is unknown, message MUST tell the clinician (a) the code
    is not valid AND (b) which code system was consulted. Current shape:
    "Code X is not valid in code system Y."
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code=UNKNOWN-CLINICAL-CODE"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False
    msg = _param_value(body, "message")
    assert isinstance(msg, str)
    # Clinically actionable: names the code AND the system
    assert "UNKNOWN-CLINICAL-CODE" in msg, (
        f"Message MUST name the rejected code: {msg!r}"
    )
    assert SNOMED_URI in msg or "snomed" in msg.lower(), (
        f"Message MUST name the system consulted: {msg!r}"
    )


def test_t51_unknown_code_message_does_not_leak_engine_state(fhir_client):
    """Message MUST NOT leak internal engine vocabulary (source labels,
    CUI codes, AUI identifiers). The clinician sees a clean message.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code=UNKNOWN-QA"
    )
    assert r.status_code == 200
    msg = str(_param_value(r.json(), "message") or "")
    forbidden = ["SNOMEDCT_US", "CodeRef(", "engine=", "C0011"]
    for tok in forbidden:
        assert tok not in msg, (
            f"unknown-code message MUST NOT leak {tok!r}: got {msg!r}"
        )


# ===========================================================================
# Lens 7: Carry-forward CF-SKEPTIC-CS03-01 — out-of-scope for CS-03
# ===========================================================================

def test_t60_value_set_validate_code_enforces_display_mismatch_post_cf_skeptic_cs03_01(fhir_client):
    """CF-SKEPTIC-CS03-01 (MEDIUM) — CLOSED in VS-05 SKEPTIC QA-069.

    The ValueSet/$validate-code operation (``_do_vs_validate``) NOW enforces
    display mismatch — mirroring the CS-03 SKEPTIC QA-048 fix on the sibling
    CodeSystem/$validate-code handler. When a client supplies a ``display``
    that does not match the engine's canonical display, the response is:

      - result=false
      - message='The display "X" is incorrect' (citing wrong value)
      - display=<engine canonical preferred term>

    The CS-03 TERMINOLOGIST iteration pinned the prior (deferred) behavior
    via the carry-forward-as-probe pattern; when the VS-05 SKEPTIC iteration
    fixed the carry-forward, this probe was updated to assert the NEW
    spec-correct behavior. 4th META confirmation of the carry-forward-as-
    probe methodology firing (3 HISTORIAN source-audit probes fired when
    VS-04 TERMINOLOGIST landed; here the carry-forward-as-probe was a
    forward-looking pin that fired loudly on the fix).
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$validate-code"
        f"?system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
        "&display=WRONG-CLINICAL-DISPLAY"
    )
    assert r.status_code == 200
    body = r.json()
    result_val = _param_value(body, "result")
    assert result_val is False, (
        "VS/$validate-code display mismatch is now ENFORCED (CF-SKEPTIC-CS03-01 "
        f"CLOSED in VS-05 SKEPTIC QA-069). Got result={result_val!r}."
    )
    msg = _param_value(body, "message")
    assert msg is not None and "WRONG-CLINICAL-DISPLAY" in str(msg)
    assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY


# ===========================================================================
# Cross-cutting: spec example response byte-exact on display mismatch
# ===========================================================================

def test_t70_display_mismatch_response_matches_spec_example_shape(fhir_client):
    """Spec example response (https://hl7.org/fhir/R4/codesystem-operation-
    validate-code.html "Response"):
        {"name": "result", "valueBoolean": "false"},
        {"name": "message", "valueString": "The display \"test\" is incorrect"},
        {"name": "display", "valueString": "Bicarbonate [Moles/volume] in Serum"}

    The TERMINOLOGIST lens verifies the implementation's response carries
    the same three Out parameters (result=false, message cites wrong value,
    display=canonical preferred term) so a clinician's EHR sees the
    correct clinical shape.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG"
    )
    assert r.status_code == 200
    body = r.json()
    # 1. result MUST be false (boolean false, not string)
    assert _param_value(body, "result") is False
    # 2. message MUST cite the wrong value AND use "incorrect"
    msg = _param_value(body, "message")
    assert isinstance(msg, str)
    assert "WRONG" in msg
    assert "incorrect" in msg.lower()
    # 3. display MUST be the canonical preferred term
    assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY


def test_t71_display_mismatch_response_does_not_include_message_at_result_true(fhir_client):
    """When the client sends a CORRECT display, result MUST be true AND
    the message parameter MUST NOT say "incorrect". (Per spec, message
    at result=true is permitted only for hints/warnings — a display-
    mismatch hint at result=true would be silent-wrong-answer.)"""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={SNOMED_T2DM_DISPLAY.replace(' ', '+')}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True
    msg = _param_value(body, "message")
    if msg is not None:
        assert "incorrect" not in str(msg).lower(), (
            "At result=true, message MUST NOT claim display is incorrect."
        )


# ===========================================================================
# Cross-cutting: GET vs POST parity on display mismatch enforcement
# ===========================================================================

def test_t80_post_display_mismatch_enforcement_matches_get(fhir_client):
    """Display mismatch enforcement MUST apply identically to GET and POST.
    The CS-03 SKEPTIC QA-048 fix added the enforcement to `_do_validate`
    which is shared by both paths; verify parity.
    """
    rg = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG"
    )
    rp = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM_CODE},
                {"name": "display", "valueString": "WRONG"},
            ],
        },
    )
    assert rg.status_code == 200 and rp.status_code == 200
    assert _param_value(rg.json(), "result") is False
    assert _param_value(rp.json(), "result") is False
    assert _param_value(rg.json(), "display") == _param_value(rp.json(), "display")
    assert _param_value(rg.json(), "message") == _param_value(rp.json(), "message")


def test_t81_batch_validate_display_mismatch_matches_single_entry(fhir_client):
    """Batch CodeSystem/$validate-code with display mismatch MUST match
    single-entry GET response. The CS-03 HISTORIAN QA-052 fix wired the
    all-pairs helper into the batch dispatcher; verify display mismatch
    enforcement also fires in the batch path (since `_do_validate` is
    shared).
    """
    single = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG"
    )
    batch = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "GET",
                        "url": f"CodeSystem/$validate-code?system={SNOMED_URI}"
                               f"&code={SNOMED_T2DM_CODE}&display=WRONG",
                    }
                }
            ],
        },
    )
    assert single.status_code == 200 and batch.status_code == 200
    batch_body = batch.json()
    assert batch_body.get("resourceType") == "Bundle"
    assert batch_body.get("type") == "batch-response"
    assert len(batch_body.get("entry", [])) == 1
    batch_resp = batch_body["entry"][0].get("response", {})
    # Extract Parameters body from batch entry
    batch_params = batch_body["entry"][0].get("resource", {})
    assert batch_params.get("resourceType") == "Parameters"
    single_params = single.json()
    # result MUST agree
    assert _param_value(single_params, "result") == _param_value(batch_params, "result")
    assert _param_value(batch_params, "result") is False
    # message MUST agree
    assert _param_value(single_params, "message") == _param_value(batch_params, "message")
    # display (canonical) MUST agree
    assert _param_value(single_params, "display") == _param_value(batch_params, "display")


# ===========================================================================
# Cross-cutting: CodeSystem/$validate-code in batch with codeableConcept
# honors all-pairs helper (CS-03 HISTORIAN QA-052 carry-forward)
# ===========================================================================

def test_t82_batch_validate_codeable_concept_multi_coding_matches_single(fhir_client):
    """Batch CodeSystem/$validate-code with a codeableConcept containing
    [INVALID, VALID] MUST return result=true (per CS-03 SKEPTIC QA-049
    + CS-03 HISTORIAN QA-052). The batch path uses the all-pairs helper
    — clinically correct.
    """
    cc_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "NONEXISTENT_QA"},
                        {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                    ]
                },
            }
        ],
    }
    single = fhir_client.post("/fhir/CodeSystem/$validate-code", json=cc_body)
    batch = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": cc_body,
                }
            ],
        },
    )
    assert single.status_code == 200 and batch.status_code == 200
    single_result = _param_value(single.json(), "result")
    batch_body = batch.json()
    batch_params = batch_body["entry"][0].get("resource", {})
    batch_result = _param_value(batch_params, "result")
    assert single_result is True, (
        "Single POST CC [INVALID, VALID] → result=true (spec: any coding match)"
    )
    assert batch_result is True, (
        "Batch POST CC [INVALID, VALID] → result=true (MUST match single POST)"
    )
    assert single_result == batch_result


# ===========================================================================
# Cross-cutting: build_parameters_validate message format pins the spec example
# ===========================================================================

def test_t90_message_format_exact_text_for_display_mismatch(fhir_client):
    """Spec example message text format: `The display "X" is incorrect`.
    Pin the EXACT format (with double quotes around the wrong value) so
    a regression that drops the quotes or changes the wording is caught.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=wrong-value"
    )
    assert r.status_code == 200
    msg = _param_value(r.json(), "message")
    # Pin the exact format from the spec example.
    assert msg == 'The display "wrong-value" is incorrect', (
        f"Message MUST be exactly: 'The display \"wrong-value\" is incorrect'; "
        f"got {msg!r}."
    )


def test_t91_unknown_code_message_format_includes_system_and_code(fhir_client):
    """Unknown-code message format pins: "Code X is not valid in code system Y."
    The format MUST include both the code and the system URI so the message
    is clinically actionable. A regression that drops either would degrade
    clinical workflow."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code=BADCODE"
    )
    assert r.status_code == 200
    msg = _param_value(r.json(), "message")
    assert isinstance(msg, str)
    # Format MUST include the code AND the system URI (canonical).
    assert "BADCODE" in msg
    assert SNOMED_URI in msg
