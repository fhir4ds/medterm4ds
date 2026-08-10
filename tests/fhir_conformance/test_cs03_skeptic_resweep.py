"""SKEPTIC RESWEEP probes for CS-03 (CodeSystem $validate-code Operation) —
fresh full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html (R4 4.0.1).

This file contains NEW hostile-input + canonical-DISPLAY-invariant probes that
are NOT in the baseline ``test_cs03_skeptic.py``. The baseline is treated as
trusted prior coverage; this resweep file adds the FRESH-FULL-SWEEP mandated
probes per USER_DIRECTIVES [2026-08-08].

SKEPTIC lens (per ROLE_QA_ENGINEER Section 3): aggressive bug hunting — edge
cases, malformed inputs, boundary conditions. 5-10 hostile probes per spec
item.

CS-02/TERMINOLOGIST tip for CS-03/SKEPTIC: verify canonical-DISPLAY invariant
holds on CS-03 surface — for every code, $validate-code Out ``display`` MUST
equal $lookup Out ``display`` AND $translate match.concept.display. Plus
$validate-code-specific display-mismatch enforcement (CS-03 SKEPTIC QA-048
carry-forward) parametrized over every seeded code, and verify
codeableConcept multi-coding "any match" semantic (CS-03 SKEPTIC QA-049)
produces byte-exact display agreement across the matched-coding resolution
path.

R4 spec note: ``inferSystem`` is listed in the chunk assignment item 2 but
does NOT appear on the CodeSystem $validate-code R4 spec page (it is a
ValueSet $validate-code In parameter). The R4 spec In parameters actually
list: ``url``, ``codeSystem``, ``code``, ``version``, ``display``,
``coding``, ``codeableConcept``, ``date``, ``abstract``, ``displayLanguage``.
Probes parametrize over the spec-actual set.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
#
# In Parameters (R4):
#   url             0..1  uri        "CodeSystem URL"
#   codeSystem      0..1  CodeSystem "CodeSystem resource inline"
#   code            0..1  code       "The code that is to be validated"
#   version         0..1  string     "The version of the code system"
#   display         0..1  string     "The display associated with the code, if
#                                     provided. If a display is provided a code
#                                     must be provided."
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
#   message         0..1  string     "Error details, if result = false"
#   display         0..1  string     "A valid display for the concept"

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DM = "73211009"        # canonical display: "Diabetes mellitus"
SNOMED_T2DM = "44054006"      # canonical display: "Type 2 diabetes mellitus"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"           # canonical display: "Type 2 diabetes mellitus"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"   # canonical display: "24 HR metformin 500 MG Oral Tablet"

# Canonical display strings (per conformance fixture — single source of truth)
EXPECTED_SNOMED_DM_DISPLAY = "Diabetes mellitus"
EXPECTED_SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
EXPECTED_ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
EXPECTED_RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"


def _params_by_name(body: dict, name: str) -> list[dict]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _first_param(body: dict, name: str) -> dict | None:
    matches = _params_by_name(body, name)
    return matches[0] if matches else None


def _param_value(body: dict, name: str) -> object | None:
    p = _first_param(body, name)
    if p is None:
        return None
    for k, v in p.items():
        if k.startswith("value"):
            return v
    return None


def _has_param(body: dict, name: str) -> bool:
    return any(p.get("name") == name for p in body.get("parameter", []))


# ---------------------------------------------------------------------------
# L1 — Canonical-DISPLAY invariant on CS-03 surface (CS-02/TERMINOLOGIST tip)
# For every code, $validate-code Out display MUST equal $lookup Out display.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "system,code,expected",
    [
        (SNOMED_URI, SNOMED_DM, EXPECTED_SNOMED_DM_DISPLAY),
        (SNOMED_URI, SNOMED_T2DM, EXPECTED_SNOMED_T2DM_DISPLAY),
        (ICD10CM_URI, ICD10CM_E11, EXPECTED_ICD10CM_E11_DISPLAY),
        (RXNORM_URI, RXNORM_METFORMIN, EXPECTED_RXNORM_METFORMIN_DISPLAY),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10cm-e11", "rxnorm-metformin"],
)
def test_s01_validate_display_matches_lookup_display(fhir_client, system, code, expected):
    """CS-02/TERMINOLOGIST tip: canonical-DISPLAY invariant. For every seeded
    code, the Out ``display`` returned by ``$validate-code`` MUST byte-exact
    match the Out ``display`` returned by ``$lookup`` for the same (system,
    code). Per FHIR R4 §4.7.5, the recommended display is the same canonical
    STR the engine holds; the two operations MUST agree.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html Out
    ``display``: "A valid display for the concept if the system wishes to
    display this to a user".
    """
    # 1. $lookup returns display
    lookup_resp = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
    )
    assert lookup_resp.status_code == 200
    lookup_display = _param_value(lookup_resp.json(), "display")
    assert lookup_display == expected, (
        f"$lookup Out display drift: expected {expected!r}, got {lookup_display!r}"
    )
    # 2. $validate-code returns display
    validate_resp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": system, "code": code},
    )
    assert validate_resp.status_code == 200
    validate_display = _param_value(validate_resp.json(), "display")
    assert validate_display is not None, "$validate-code Out missing display"
    # 3. Invariant: validate_display == lookup_display
    assert validate_display == lookup_display, (
        f"canonical-DISPLAY invariant VIOLATED for system={system} code={code}: "
        f"$lookup={lookup_display!r} vs $validate-code={validate_display!r}"
    )


def test_s02_validate_out_system_is_canonical_not_alias(fhir_client):
    """CS-02/TERMINOLOGIST tip: canonical-DISPLAY invariant extends to ``system``.
    Alias inputs (urn:oid, trailing-slash) MUST resolve to the canonical URI in
    the Out ``system`` parameter, mirroring $lookup behavior.

    Spec: FHIR R4 §4.8.21.1 Out ``system``: the canonical URI from the registry.
    Mirrors CS-02 HISTORIAN QA-047 (_do_lookup) + CS-03 HISTORIAN QA-051
    (_do_validate).
    """
    # Alias input: trailing-slash variant
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": f"{SNOMED_URI}/", "code": SNOMED_T2DM},
    )
    assert r.status_code == 200
    out_system = _param_value(r.json(), "system")
    assert out_system == SNOMED_URI, (
        f"Out system MUST be canonical {SNOMED_URI!r} not alias; got {out_system!r}"
    )


def test_s03_validate_out_system_canonical_for_oid_alias(fhir_client):
    """CS-02/TERMINOLOGIST tip: ``urn:oid:`` alias also MUST resolve to
    canonical in Out ``system``. Per GLOBAL_RULES.md client-input-as-canonical
    drift pattern (count=8+1 PROMOTED).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": "urn:oid:2.16.840.1.113883.6.90", "code": ICD10CM_E11},
    )
    assert r.status_code == 200, f"urn:oid alias rejected: {r.status_code} {r.text[:200]}"
    out_system = _param_value(r.json(), "system")
    assert out_system == ICD10CM_URI, (
        f"Out system for urn:oid alias MUST be canonical {ICD10CM_URI!r}, "
        f"got {out_system!r}"
    )


def test_s04_validate_uppercase_scheme_alias_resolves_to_canonical(fhir_client):
    """CS-02/TERMINOLOGIST tip: uppercase-scheme URIs (per TS-03 EXPLORER fix)
    are accepted on input; Out ``system`` is the lowercase canonical."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": f"HTTP://snomed.info/sct", "code": SNOMED_T2DM},
    )
    assert r.status_code == 200, f"uppercase-scheme rejected: {r.status_code}"
    out_system = _param_value(r.json(), "system")
    assert out_system == SNOMED_URI, (
        f"Out system for uppercase-scheme input MUST be canonical {SNOMED_URI!r}, "
        f"got {out_system!r}"
    )


# ---------------------------------------------------------------------------
# L2 — QA-048 carry-forward: display-mismatch enforcement parametrized
# Per chunk assignment CS-02/TERMINOLOGIST tip.
# Spec example response: result=false + message 'The display "X" is incorrect'
# + canonical display. https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "system,code,canonical,wrong",
    [
        (SNOMED_URI, SNOMED_DM, EXPECTED_SNOMED_DM_DISPLAY, "Diabetes"),
        (SNOMED_URI, SNOMED_T2DM, EXPECTED_SNOMED_T2DM_DISPLAY, "Type 2 DM"),
        (ICD10CM_URI, ICD10CM_E11, EXPECTED_ICD10CM_E11_DISPLAY, "T2DM"),
        (RXNORM_URI, RXNORM_METFORMIN, EXPECTED_RXNORM_METFORMIN_DISPLAY, "metformin"),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10cm-e11", "rxnorm-metformin"],
)
def test_s10_display_mismatch_returns_false_with_message_and_canonical(
    fhir_client, system, code, canonical, wrong,
):
    """CS-03 SKEPTIC QA-048 carry-forward per CS-02/TERMINOLOGIST tip.
    Parametrized over EVERY seeded code.

    Spec example (https://hl7.org/fhir/R4/codesystem-operation-validate-code.html):
      {
        "parameter": [
          {"name": "result", "valueBoolean": "false"},
          {"name": "message", "valueString": "The display \"test\" is incorrect"},
          {"name": "display", "valueString": "Bicarbonate [Moles/volume] in Serum"}
        ]
      }

    When client supplies wrong ``display``, server MUST return: (1) result=false,
    (2) message citing the wrong value, (3) display = engine canonical (NOT echo).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": system, "code": code, "display": wrong},
    )
    assert r.status_code == 200
    body = r.json()
    # 1. result=false
    assert _param_value(body, "result") is False, (
        f"Display mismatch MUST produce result=false; got {_param_value(body, 'result')!r}"
    )
    # 2. message cites wrong value (spec example format)
    msg = _param_value(body, "message")
    assert msg is not None, "Display mismatch MUST produce a message"
    assert wrong in str(msg), (
        f"Message MUST cite the wrong value {wrong!r}; got {msg!r}"
    )
    # 3. display = engine canonical (NOT client echo)
    out_display = _param_value(body, "display")
    assert out_display == canonical, (
        f"Out display MUST be engine canonical {canonical!r}, not echo of "
        f"client input {wrong!r}; got {out_display!r}"
    )
    assert out_display != wrong, (
        "Out display MUST NOT echo client's wrong display value"
    )


def test_s11_display_match_returns_true_no_message(fhir_client):
    """Item 4 boundary: when client-supplied display MATCHES engine canonical,
    result=true. Spec Out ``message`` cardinality 0..1 — absent on success is
    conformant (the impl emits no message on display match).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "display": EXPECTED_SNOMED_T2DM_DISPLAY,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True
    out_display = _param_value(body, "display")
    assert out_display == EXPECTED_SNOMED_T2DM_DISPLAY


def test_s12_display_mismatch_message_format_byte_exact(fhir_client):
    """CS-03 TERMINOLOGIST test_t90 mirror: spec example message format byte
    audit. Format is ``'The display "X" is incorrect'`` — double quotes around
    the wrong value.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM, "display": "wrong"},
    )
    assert r.status_code == 200
    msg = _param_value(r.json(), "message")
    assert msg == 'The display "wrong" is incorrect', (
        f"Message format MUST byte-match spec example; got {msg!r}"
    )


def test_s13_display_mismatch_case_sensitive_exact_match(fhir_client):
    """Hostile probe: case-sensitivity boundary. Spec: "Whether displays are
    case sensitive is code system dependent". medterm4ds is exact-match today
    (per AGENTS.md Known Fragile Areas, CF line 77: "Case-sensitivity is
    exact-match today; per-source case-sensitivity flagging is a future
    enhancement"). A lowercase "type 2 diabetes mellitus" vs canonical
    "Type 2 diabetes mellitus" MUST be flagged as mismatch (result=false).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "display": EXPECTED_SNOMED_T2DM_DISPLAY.lower(),
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False, (
        "Case-sensitivity boundary: lowercase variant of canonical display MUST "
        "be flagged as mismatch under current exact-match semantic"
    )
    out_display = _param_value(body, "display")
    assert out_display == EXPECTED_SNOMED_T2DM_DISPLAY


def test_s14_display_with_unknown_code_does_not_trigger_mismatch(fhir_client):
    """Hostile probe: when the code is UNKNOWN, display enforcement MUST NOT
    fire — the unknown-code branch fires first (result=false, message="Code X
    is not valid", no canonical display because code_info is None)."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": "99999999", "display": "anything"},
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False
    msg = _param_value(body, "message")
    # Unknown-code branch message; NOT display-mismatch message
    assert "not valid" in str(msg).lower() or "not" in str(msg).lower(), (
        f"Unknown-code path must produce 'not valid' message; got {msg!r}"
    )
    assert "incorrect" not in str(msg).lower(), (
        f"Unknown-code MUST NOT trigger display-mismatch message; got {msg!r}"
    )


# ---------------------------------------------------------------------------
# L3 — QA-049 carry-forward: codeableConcept multi-coding "any match" semantic
# Per chunk assignment CS-02/TERMINOLOGIST tip.
# ---------------------------------------------------------------------------

def test_s20_codeable_concept_first_invalid_second_valid_returns_true(fhir_client):
    """CS-03 SKEPTIC QA-049 carry-forward. Spec In ``codeableConcept``:
    "The server returns true if one of the coding values is in the code system".

    Hostile probe: codeableConcept = [INVALID, VALID]. Single-pair helper would
    pick the FIRST coding (invalid) → result=false. All-pairs helper iterates
    and returns true on the second (valid) coding.
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
                            {"system": SNOMED_URI, "code": "99999999"},
                            {"system": SNOMED_URI, "code": SNOMED_T2DM},
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True, (
        "Any-match semantic VIOLATED: codeableConcept with [INVALID, VALID] "
        "MUST return result=true (QA-049 carry-forward)"
    )
    # Out display MUST reflect MATCHED coding's canonical, not the first
    out_display = _param_value(body, "display")
    assert out_display == EXPECTED_SNOMED_T2DM_DISPLAY, (
        f"Out display MUST reflect MATCHED coding canonical "
        f"{EXPECTED_SNOMED_T2DM_DISPLAY!r}, not first coding; got {out_display!r}"
    )


def test_s21_codeable_concept_byte_exact_display_vs_lookup_matched_coding(fhir_client):
    """CS-02/TERMINOLOGIST tip: verify codeableConcept multi-coding produces
    byte-exact display agreement across the matched-coding resolution path.
    The Out display from codeableConcept path MUST equal $lookup display for
    the MATCHED code.
    """
    # codeableConcept with [INVALID, SNOMED_T2DM] → matched code is T2DM
    r_validate = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "99999999"},
                            {"system": SNOMED_URI, "code": SNOMED_T2DM},
                        ]
                    },
                }
            ],
        },
    )
    assert r_validate.status_code == 200
    validate_display = _param_value(r_validate.json(), "display")

    # $lookup for T2DM (matched code)
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r_lookup.status_code == 200
    lookup_display = _param_value(r_lookup.json(), "display")

    assert validate_display == lookup_display, (
        f"canonical-DISPLAY invariant on codeableConcept path VIOLATED: "
        f"$validate-code Out display={validate_display!r} vs "
        f"$lookup Out display for matched code={lookup_display!r}"
    )


def test_s22_codeable_concept_all_invalid_returns_false(fhir_client):
    """CS-03 SKEPTIC QA-049 carry-forward. All-invalid codeableConcept MUST
    return result=false with a message.
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
                            {"system": SNOMED_URI, "code": "99999999"},
                            {"system": SNOMED_URI, "code": "88888888"},
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
    assert msg is not None, "All-invalid codeableConcept MUST produce a message"


def test_s23_codeable_concept_mixed_systems_returns_true_on_any_match(fhir_client):
    """Hostile probe: codeableConcept spans MULTIPLE systems. Spec says "true
    if one of the coding values is in the code system". The current impl does
    not constrain to a single system — it resolves each coding's system via
    ``fhir_uri_to_system`` and finds ANY matching code. The semantic is
    "any coding in any known system" — confirmed via this probe.
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
                            {"system": SNOMED_URI, "code": "99999999"},
                            {"system": ICD10CM_URI, "code": ICD10CM_E11},
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True
    out_display = _param_value(body, "display")
    assert out_display == EXPECTED_ICD10CM_E11_DISPLAY, (
        f"Matched coding canonical MUST be ICD-10-CM E11 display "
        f"{EXPECTED_ICD10CM_E11_DISPLAY!r}; got {out_display!r}"
    )


def test_s24_codeable_concept_out_system_reflects_matched_coding(fhir_client):
    """CS-02/TERMINOLOGIST tip: canonical-DISPLAY + canonical-SYSTEM invariant
    on codeableConcept path. Out ``system`` MUST reflect the matched coding's
    canonical URI (per CR-025 fix that wired canonical_system_uri into the
    codeableConcept branch)."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": f"{SNOMED_URI}/", "code": "99999999"},
                            {"system": SNOMED_URI, "code": SNOMED_T2DM},
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    out_system = _param_value(r.json(), "system")
    assert out_system == SNOMED_URI, (
        f"Out system on codeableConcept path MUST be canonical (CR-025); "
        f"got {out_system!r}"
    )


def test_s25_codeable_concept_malformed_valueCoding_silently_dropped(fhir_client):
    """Hostile probe: codeableConcept with malformed coding entries (missing
    code, non-dict, etc.). Must not 500. Per GLOBAL_RULES.md "Silent Fallbacks",
    type errors MUST propagate; malformed shape SHOULD be silently dropped (the
    helper returns None for these cases) and fall through to 400 OR result=false.
    """
    # All malformed codings → no match → result=false with message
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI},  # missing code
                            {"code": SNOMED_T2DM},   # missing system
                            "not-a-dict",
                            None,
                        ]
                    },
                }
            ],
        },
    )
    # Must not 500; either 400 or 200 with result=false
    assert r.status_code in (200, 400), (
        f"Malformed codeableConcept MUST NOT 500; got {r.status_code}: {r.text[:200]}"
    )
    if r.status_code == 200:
        assert _param_value(r.json(), "result") is False


# ---------------------------------------------------------------------------
# L4 — POST body type mismatches (item 1+2 hostile probes)
# ---------------------------------------------------------------------------

def test_s30_post_body_not_parameters_resource(fhir_client):
    """Hostile probe: POST body is a generic dict (NOT a Parameters resource).
    Server must not crash; should return 400 OperationOutcome."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={"foo": "bar"},
    )
    assert r.status_code in (200, 400), f"Got {r.status_code}: {r.text[:200]}"
    if r.status_code == 200:
        # No system+code in body → result=false
        assert _param_value(r.json(), "result") is False


def test_s31_post_body_is_list_not_dict(fhir_client):
    """Hostile probe: POST body is a JSON list, not a dict. Must not 500."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json=[{"name": "system", "valueUri": SNOMED_URI}],
    )
    # FastAPI's body: dict[str, Any] signature SHOULD reject a list with 422
    assert r.status_code in (400, 422), (
        f"List body must be rejected (400/422); got {r.status_code}: {r.text[:200]}"
    )
    # Even error path must be a FHIR OperationOutcome (via exception handler)
    if r.status_code in (400, 422):
        body = r.json()
        # Must be OperationOutcome OR conformant error (not text/plain)
        assert r.headers.get("content-type", "").startswith("application/fhir+json") or \
               "OperationOutcome" in str(body) or "resourceType" in body, \
               f"Error body MUST be FHIR shape; got content-type={r.headers.get('content-type')!r}"


def test_s32_post_body_valueCoding_string_not_dict(fhir_client):
    """Hostile probe: ``coding`` parameter's valueCoding is a string, not a
    dict. The helper MUST defensively guard against this shape."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": "not-a-coding"},
            ],
        },
    )
    # Must not 500
    assert r.status_code in (200, 400), (
        f"valueCoding=string MUST NOT 500; got {r.status_code}: {r.text[:200]}"
    )


def test_s33_post_body_valueCoding_list_not_dict(fhir_client):
    """Hostile probe: valueCoding is a list, not a dict."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": [{"system": SNOMED_URI}]},
            ],
        },
    )
    assert r.status_code in (200, 400), (
        f"valueCoding=list MUST NOT 500; got {r.status_code}: {r.text[:200]}"
    )


def test_s34_post_body_valueCodeableConcept_string(fhir_client):
    """Hostile probe: codeableConcept is a string, not a dict."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeableConcept", "valueCodeableConcept": "not-a-cc"},
            ],
        },
    )
    assert r.status_code in (200, 400), (
        f"valueCodeableConcept=string MUST NOT 500; got {r.status_code}"
    )


def test_s35_post_body_valueCodeableConcept_missing_coding(fhir_client):
    """Hostile probe: codeableConcept is a dict but has no ``coding`` key."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeableConcept", "valueCodeableConcept": {"text": "diabetes"}},
            ],
        },
    )
    assert r.status_code in (200, 400)


def test_s36_post_body_valueCodeableConcept_coding_empty_list(fhir_client):
    """Hostile probe: codeableConcept.coding is an empty list."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {"coding": []},
                }
            ],
        },
    )
    assert r.status_code in (200, 400)


# ---------------------------------------------------------------------------
# L5 — Required parameter boundary (item 1)
# ---------------------------------------------------------------------------

def test_s40_get_empty_system_string_returns_422(fhir_client):
    """Item 1: required ``system`` empty-string treated as present by default
    FastAPI Query. min_length=1 fix (TS-02 SKEPTIC QA-002) must hold.
    Spec: "a client SHALL provide one (and only one) of (code+system, coding,
    codeableConcept)". Empty string is NOT "providing".
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": "", "code": SNOMED_T2DM},
    )
    assert r.status_code == 422, (
        f"Empty-string system MUST be rejected 422; got {r.status_code}: {r.text[:200]}"
    )


def test_s41_get_empty_code_string_returns_422(fhir_client):
    """Item 1: required ``code`` empty-string rejected 422."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": ""},
    )
    assert r.status_code == 422


def test_s42_post_missing_both_system_and_code_no_alternatives(fhir_client):
    """Item 1: POST with neither system+code NOR coding NOR codeableConcept
    → 400 OperationOutcome."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert r.status_code == 400
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_s43_post_only_system_no_code_no_alternatives(fhir_client):
    """Item 1: POST with system only (no code, no alternatives) → 400."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [{"name": "system", "valueUri": SNOMED_URI}],
        },
    )
    assert r.status_code == 400


def test_s44_post_system_code_and_coding_simultaneously(fhir_client):
    """Hostile probe: POST with system+code AND coding. Spec: "a client SHALL
    provide one (and only one) of". medterm4ds's POST handler treats
    system+code as winning (scalar-wins-on-conflict semantic per TS-02
    HISTORIAN QA-022). Verify both values agree OR scalar wins.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM},
                {
                    "name": "coding",
                    "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DM},
                },
            ],
        },
    )
    assert r.status_code == 200
    # Scalar-wins-on-conflict: T2DM is the system+code, coding is ignored
    out_display = _param_value(r.json(), "display")
    assert out_display == EXPECTED_SNOMED_T2DM_DISPLAY, (
        f"Scalar system+code MUST win on conflict; got display={out_display!r}"
    )


# ---------------------------------------------------------------------------
# L6 — `date` parameter hostile inputs (item 2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "date_value",
    [
        "2020-01-01",
        "2020-01-01T10:30:00",
        "2020-01-01T10:30:00Z",
        "2020-01-01T10:30:00+05:00",
        "2020",
        "2020-01",
        "2099-12-31",  # future
        "1900-01-01",  # past
    ],
    ids=["date", "datetime", "datetime-z", "datetime-offset", "year", "year-month", "future", "past"],
)
def test_s50_date_param_accepted_without_5xx(fhir_client, date_value):
    """Item 2 / spec In ``date``: dateTime type. RFC 3339 + partial dates
    accepted without 5xx. Per spec: "The date for which the validation should
    be checked." medterm4ds treats date as informational (single-snapshot
    engine) — it MUST accept any reasonable date string without crashing.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM, "date": date_value},
    )
    assert r.status_code in (200, 400), (
        f"date={date_value!r} MUST NOT 5xx; got {r.status_code}: {r.text[:200]}"
    )


def test_s51_date_param_malformed_does_not_crash(fhir_client):
    """Hostile probe: malformed date string. Server must accept-or-reject
    gracefully (200 or 400), not 500."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM, "date": "not-a-date"},
    )
    assert r.status_code in (200, 400), f"Got {r.status_code}: {r.text[:200]}"


# ---------------------------------------------------------------------------
# L7 — Response shape audit (item 3): Parameters with result/message/display
# ---------------------------------------------------------------------------

def test_s60_response_shape_result_is_valueBoolean_lowercase(fhir_client):
    """Item 3 / spec: ``result`` Out parameter is 1..1 boolean. On the wire,
    JSON must use lowercase ``true``/``false`` (FHIR R4 §3.4.1)."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r.status_code == 200
    # Parse JSON; assert Python bool
    body = r.json()
    result_val = _param_value(body, "result")
    assert isinstance(result_val, bool)
    assert result_val is True
    # Audit the raw wire body (must be lowercase "true", not "True")
    raw = r.text
    assert '"valueBoolean": true' in raw or '"valueBoolean":true' in raw, (
        f"Wire body MUST render lowercase boolean; raw excerpt: ...{raw[:400]}..."
    )
    assert '"valueBoolean": True' not in raw, (
        "Wire body MUST NOT render Python-style 'True' (capital T)"
    )


def test_s61_response_shape_message_uses_valueString(fhir_client):
    """Item 3 / spec: ``message`` Out parameter is 0..1 string. MUST use
    valueString not valueCode/valueInteger etc."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "display": "wrong",
        },
    )
    assert r.status_code == 200
    msg_param = _first_param(r.json(), "message")
    assert msg_param is not None
    assert "valueString" in msg_param
    # Explicit negative assertion: no wrong value type
    for wrong_key in ("valueCode", "valueInteger", "valueBoolean", "valueUri"):
        assert wrong_key not in msg_param, (
            f"message MUST use valueString, not {wrong_key}"
        )


def test_s62_response_shape_display_uses_valueString(fhir_client):
    """Item 3 / spec: ``display`` Out parameter is 0..1 string. MUST use
    valueString."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r.status_code == 200
    display_param = _first_param(r.json(), "display")
    assert display_param is not None
    assert "valueString" in display_param


def test_s63_response_shape_unknown_code_message_uses_valueString(fhir_client):
    """Item 3 boundary: unknown-code path MUST also use valueString for
    message."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": "99999999"},
    )
    assert r.status_code == 200
    msg_param = _first_param(r.json(), "message")
    assert msg_param is not None
    assert "valueString" in msg_param


def test_s64_response_shape_result_always_present(fhir_client):
    """Item 3 / spec: ``result`` Out parameter is 1..1 (always present on 200).
    Path-coverage: display-match, display-mismatch, unknown-code, and
    codeableConcept all MUST emit result."""
    paths = [
        ("match", {"system": SNOMED_URI, "code": SNOMED_T2DM}),
        ("unknown-code", {"system": SNOMED_URI, "code": "99999999"}),
    ]
    for label, params in paths:
        r = fhir_client.get("/fhir/CodeSystem/$validate-code", params=params)
        assert r.status_code == 200, f"{label}: got {r.status_code}"
        assert _has_param(r.json(), "result"), (
            f"result Out parameter MUST always be present (1..1) on path={label}"
        )


# ---------------------------------------------------------------------------
# L8 — `coding` alternative encoding hostile probes (item 2)
# ---------------------------------------------------------------------------

def test_s70_post_coding_missing_code_returns_400(fhir_client):
    """Item 2 / spec In ``coding``: "A coding to validate. The system must
    match the specified code system". Coding with only system (no code) MUST
    be rejected — the helper returns None → handler falls through to 400."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {"system": SNOMED_URI}},
            ],
        },
    )
    assert r.status_code == 400, (
        f"Coding with no code MUST be 400; got {r.status_code}"
    )


def test_s71_post_coding_missing_system_returns_400(fhir_client):
    """Item 2: coding with only code (no system) MUST be 400."""
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


def test_s72_post_coding_byte_exact_parity_with_get_system_code(fhir_client):
    """CS-02/TERMINOLOGIST tip: GET system+code and POST coding for the same
    code MUST produce byte-exact display agreement. Verified on validate-code
    surface (extends CS-02 EXPLORER test_e70 + CS-03 baseline test_s80)."""
    r_get = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
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
    assert r_get.status_code == 200
    assert r_post.status_code == 200
    get_display = _param_value(r_get.json(), "display")
    post_display = _param_value(r_post.json(), "display")
    assert get_display == post_display == EXPECTED_SNOMED_T2DM_DISPLAY, (
        f"GET ↔ POST coding parity VIOLATED: get={get_display!r}, post={post_display!r}"
    )


# ---------------------------------------------------------------------------
# L9 — Long-code / special-char hostile probes (item 1+2)
# ---------------------------------------------------------------------------

def test_s80_very_long_code_does_not_crash(fhir_client):
    """Hostile probe: very long code value (>1000 chars). Must not 5xx."""
    long_code = "A" * 2000
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": long_code},
    )
    assert r.status_code == 200, f"Got {r.status_code}: {r.text[:200]}"
    # Code is unknown → result=false
    assert _param_value(r.json(), "result") is False


def test_s81_special_chars_in_code(fhir_client):
    """Hostile probe: SQL injection attempts + XSS attempts in code. DuckDB
    prepared statements handle these gracefully."""
    for special in ["'; DROP TABLE mrconso; --", "<script>alert(1)</script>", "code/../../etc"]:
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": special},
        )
        assert r.status_code == 200, f"code={special!r} got {r.status_code}"
        assert _param_value(r.json(), "result") is False


def test_s82_null_bytes_in_code(fhir_client):
    """Hostile probe: null byte in code value."""
    # Use the POST path to bypass URL encoding concerns
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "code\x00with-null"},
            ],
        },
    )
    assert r.status_code in (200, 400), f"Got {r.status_code}: {r.text[:200]}"


def test_s83_unicode_in_code(fhir_client):
    """Hostile probe: unicode in code value (CJK characters)."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": "糖尿病"},
    )
    assert r.status_code == 200
    assert _param_value(r.json(), "result") is False


# ---------------------------------------------------------------------------
# L10 — Cross-resource-source structural contracts (source-read audits)
# ---------------------------------------------------------------------------

_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
_RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)


def _get_func_source(source: str, name: str) -> str:
    """Return the source of a function defined in the given source string.

    Walks BOTH ast.FunctionDef AND ast.AsyncFunctionDef (CS-01 SKEPTIC
    methodology) to handle nested async route handlers inside create_fhir_app.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def test_s90_do_validate_calls_canonical_system_uri_on_scalar_path():
    """Source-read contract (per CS-02/TERMINOLOGIST tip + CS-03 HISTORIAN
    QA-051): ``_do_validate`` MUST call ``canonical_system_uri()`` on the
    scalar-system path to prevent client-input-as-canonical drift.

    Spec: FHIR R4 §4.8.21.1 Out ``system``: the canonical URI from the
    registry, not client's alias input.
    """
    source = _FHIR_API_PATH.read_text()
    func = _get_func_source(source, "_do_validate")
    assert func, "_do_validate not found in fhir_api.py"
    # canonical_system_uri MUST appear in the function body
    assert "canonical_system_uri" in func, (
        "_do_validate MUST call canonical_system_uri() on the scalar-system "
        "path (CS-03 HISTORIAN QA-051 fix). Client-input-as-canonical drift "
        "pattern (count=8+1 PROMOTED) — OUT system MUST be canonical, not echo."
    )


def test_s91_do_validate_uses_all_pairs_helper_for_codeable_concept():
    """Source-read contract (per CS-03 SKEPTIC QA-049): ``_do_validate`` MUST
    use the all-pairs helper ``_extract_all_coding_pairs_from_codeable_concept``
    for codeableConcept resolution (NOT the single-pair helper).
    """
    source = _FHIR_API_PATH.read_text()
    # validate_post must wire the all-pairs helper
    func = _get_func_source(source, "validate_post")
    assert func, "validate_post not found"
    assert "_extract_all_coding_pairs_from_codeable_concept" in func, (
        "validate_post MUST use _extract_all_coding_pairs_from_codeable_concept "
        "(CS-03 SKEPTIC QA-049). The single-pair helper silently wrong-answers "
        "on codeableConcept with [INVALID, VALID]."
    )


def test_s92_validate_post_handler_wires_coding_alternative():
    """Source-read contract (per TS-02 HISTORIAN QA-022/QA-023): ``validate_post``
    MUST consult ``_extract_coding_from_parameters`` when system+code are absent.
    """
    source = _FHIR_API_PATH.read_text()
    func = _get_func_source(source, "validate_post")
    assert func
    assert "_extract_coding_from_parameters" in func


def test_s93_build_parameters_validate_prefers_engine_canonical_display():
    """Source-read contract (per CS-02/TERMINOLOGIST tip + TS-02 TERMINOLOGIST
    QA-029): ``build_parameters_validate`` MUST prefer the engine's canonical
    display over the client-supplied value. The Out ``display`` is for
    revelation of the server's canonical, NOT echo of client input.
    """
    source = _RESPONSES_PATH.read_text()
    func = _get_func_source(source, "build_parameters_validate")
    assert func
    # The canonical-precedence pattern: code_info.name wins over display
    assert "code_info.name" in func, (
        "build_parameters_validate MUST derive display from code_info.name "
        "(engine canonical), not echo client display (TS-02 TERMINOLOGIST QA-029)."
    )


def test_s94_min_length_1_on_validate_get_required_queries():
    """Source-read contract (per TS-02 SKEPTIC QA-002 + GLOBAL_RULES.md
    line 138 PROMOTED pattern): the GET ``validate_get`` handler MUST declare
    ``min_length=1`` on required-string Query parameters (system, code).
    Without it, empty string is silently accepted as "present" → silent-wrong-
    answer.
    """
    source = _FHIR_API_PATH.read_text()
    func = _get_func_source(source, "validate_get")
    assert func
    # Find the Query declarations for system and code
    assert "min_length=1" in func, (
        "validate_get MUST declare min_length=1 on required-string Query "
        "parameters (TS-02 SKEPTIC QA-002)."
    )
    # The two required string params system+code both need min_length=1
    # Count occurrences
    min_length_count = func.count("min_length=1")
    assert min_length_count >= 2, (
        f"validate_get needs min_length=1 on BOTH system AND code; "
        f"found {min_length_count} occurrence(s)"
    )
