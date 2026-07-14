"""VS-05 TERMINOLOGIST: ValueSet $validate-code Operation.

Spec: https://build.fhir.org/valueset-operation-validate-code.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
In/Out Parameters reference:
  https://hl7.org/fhir/R4/operation-valueset-validate-code.html

TERMINOLOGIST lens for VS-05 — clinical/terminological correctness.
Default severity HIGH.

9 lens items (per chunk assignment):

  Lens 1 — Display mismatch message clinical correctness.
    (a) Message format byte-exact with spec example
        (`The display "X" is incorrect`).
    (b) Wrong display value cited in message.
    (c) Canonical display returned in Out `display` (engine canonical,
        NOT echo of client input per TS-02 TERMINOLOGIST QA-029).

  Lens 2 — Cross-operation canonical agreement (VS $validate-code ↔
    CodeSystem $validate-code). Same (system, code, display) validated
    via both operations MUST return same result, same message shape,
    same canonical display. Catches silent drift if a future fix touches
    one handler but not the sibling.

  Lens 3 — CodeableConcept per-coding display semantics.
    When client sends codeableConcept with multiple codings (one valid
    + one invalid), Out `display` MUST reflect the MATCHED coding's
    canonical — not the first coding's display.

  Lens 4 — Patient-friendly name quality.
    The Out `display` parameter is "A display to show to the user"
    (per spec). For LOINC codes where PF exists, $lookup surfaces PF.
    For VS/$validate-code, the Out `display` is the engine canonical
    (per TS-02 TERMINOLOGIST QA-029 fix). Verify the canonical name is
    clinically sensible (not a technical long-name when PF exists).

  Lens 5 — Implicit value set clinical safety.
    (a) Validating against `url=http://snomed.info/sct` (implicit all-
        of-SNOMED) — any SNOMED code in the underlying system SHOULD
        validate as result=true.
    (b) Validating against a SNOMED intensional URL form — medterm4ds
        does not scope membership (INTENDED per AGENTS.md NOT A BUG
        registry); the probe documents the CURRENT behavior.

  Lens 6 — `abstract` parameter clinical safety.
    With `abstract=false` (default), abstract concepts SHOULD fail.
    The implementation accepts but ignores the param (INTENDED-for-now
    per AGENTS.md NOT A BUG registry — engine has no abstract-flagging
    data). Probes pin the CURRENT behavior (accepted, no-op) and
    document the clinical-safety gap for future enhancement.

  Lens 7 — `inferSystem` clinical correctness.
    When code is ambiguous (exists in multiple systems), inference may
    give wrong answer clinically. The implementation accepts but does
    not implement inference today (INTENDED-for-now per AGENTS.md NOT
    A BUG registry — no code-uniqueness registry). Probes verify the
    param is ACCEPTED without 500.

  Lens 8 — Unknown-code clinical-actionable message.
    When result=false due to unknown code, the message MUST be
    clinically actionable: include BOTH the code AND the system URI so
    a clinician's EHR can present a meaningful error. Mirrors CS-03
    TERMINOLOGIST test_t91 methodology on the VS surface.

  Lens 9 — Cross-system canonical display parametrization.
    For each seeded system (SNOMED, ICD-10-CM, RxNorm), the Out
    `display` for a known code MUST match the engine canonical name
    for that code. Catches silent drift if a future fix touches one
    system's display resolution.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # 44054006 is-a 73211009

The fixture does NOT seed LOINC codes (Lens 4 LOINC probes are
structural; the engine canonical name is `code_info.name` from
mrconso.STR for the matching SAB). The fixture does NOT seed abstract
concepts (Lens 6 probes pin the accepted-no-op behavior; the
clinical-safety gap is documented for future enhancement).
"""

from __future__ import annotations

import pytest

# Spec sources:
#   https://build.fhir.org/valueset-operation-validate-code.html
#   https://hl7.org/fhir/R4/valueset-operation-validate-code.html
#   https://hl7.org/fhir/R4/operation-valueset-validate-code.html
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

# Cross-system code-system/code pairs for Lens 9 + Lens 2 parametrization.
SEEDED_SYSTEMS = [
    (
        SNOMED_URI,
        SNOMED_T2DM_CODE,
        SNOMED_T2DM_DISPLAY,
        "SNOMED-CT T2DM",
    ),
    (
        ICD10CM_URI,
        ICD10CM_E11_CODE,
        ICD10CM_E11_DISPLAY,
        "ICD-10-CM E11",
    ),
    (
        RXNORM_URI,
        RXNORM_METFORMIN_CODE,
        RXNORM_METFORMIN_DISPLAY,
        "RxNorm metformin",
    ),
]


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
# Lens 1 — Display mismatch message clinical correctness
# ===========================================================================
# Spec: FHIR R4 ValueSet/$validate-code Out `message` (0..1 string):
#   "Error details about the validate operation if result=false".
#   Per FHIR R4 §4.9.3 cross-reference to CodeSystem operation, the
#   spec example message format is: `The display "X" is incorrect`
#   where X is the WRONG display value the client supplied.
# Source:
#   https://hl7.org/fhir/R4/operation-valueset-validate-code.html
#   https://hl7.org/fhir/R4/operation-codesystem-validate-code.html
#
# VS-05 SKEPTIC QA-069 (CF-SKEPTIC-CS03-01 CLOSED) wired display-mismatch
# enforcement into `_do_vs_validate`. The TERMINOLOGIST lens verifies
# the message format is byte-exact with the spec example AND clinically
# actionable (cites the wrong value, not a generic error).

def test_t10_message_format_byte_exact_with_spec_example(fhir_client):
    """The display mismatch message MUST be byte-exact with the spec
    example: `The display "X" is incorrect` (with double quotes around
    the client-supplied wrong value). A regression that drops the
    quotes or changes the wording is clinically misleading (clinician
    cannot tell what display value was rejected).

    Spec example response (CodeSystem operation, mirrored here per FHIR
    R4 §4.9.3 cross-reference):
        {"name": "message", "valueString": "The display \"test\" is incorrect"}
    """
    wrong_display = "definitely-incorrect-display"
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={wrong_display}"
    )
    assert r.status_code == 200
    msg = _param_value(r.json(), "message")
    # Pin the EXACT format with double quotes around the client's wrong value.
    expected = f'The display "{wrong_display}" is incorrect'
    assert msg == expected, (
        f"VS/$validate-code display mismatch message MUST be byte-exact with "
        f"spec example: {expected!r}; got {msg!r}. A clinician's EHR relies on "
        "this format to present the rejected display value."
    )


def test_t11_message_does_not_cite_canonical_display(fhir_client):
    """The wrong-display message MUST cite the WRONG value (client
    input), NOT the engine's canonical display. Citing the canonical
    would silently mask the clinical discrepancy. Mirrors CS-03
    TERMINOLOGIST test_t90 byte-exact contract on the VS surface."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG-VALUE"
    )
    assert r.status_code == 200
    body = r.json()
    msg = _param_value(body, "message")
    assert msg is not None
    msg_str = str(msg)
    # The wrong value MUST be cited.
    assert "WRONG-VALUE" in msg_str, (
        f"Message MUST cite the WRONG display value (client input); "
        f"got {msg_str!r}."
    )
    # The canonical display MUST NOT be the cited value.
    assert SNOMED_T2DM_DISPLAY not in msg_str, (
        f"Message MUST NOT cite the canonical display value in the "
        f"'is incorrect' clause; got {msg_str!r}. The canonical belongs "
        "in the separate Out `display` parameter."
    )


def test_t12_canonical_display_returned_in_out_display(fhir_client):
    """On display mismatch, the Out `display` parameter MUST return the
    engine's canonical preferred term (NOT an echo of the client's wrong
    value). Per TS-02 TERMINOLOGIST QA-029 fix: the In parameter is for
    verification, the Out parameter is for revelation of the server's
    canonical value. CS-03 SKEPTIC QA-048 wired this on CodeSystem;
    VS-05 SKEPTIC QA-069 mirrored it on ValueSet."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG-VALUE"
    )
    assert r.status_code == 200
    body = r.json()
    # Result MUST be false.
    assert _param_value(body, "result") is False
    # Out `display` MUST be the engine canonical.
    display = _param_value(body, "display")
    assert display == SNOMED_T2DM_DISPLAY, (
        f"Out `display` MUST be the engine canonical preferred term "
        f"({SNOMED_T2DM_DISPLAY!r}); got {display!r}. The TS-02 "
        "TERMINOLOGIST QA-029 fix preserves canonical precedence over "
        "client input; VS-05 SKEPTIC QA-069 mirrors it on ValueSet."
    )


def test_t13_message_does_not_use_clinically_misleading_phrasing(fhir_client):
    """Clinical-message forbidden-phrase audit. The display mismatch
    message MUST convey the terminological fact (the supplied display
    is incorrect), not imply server limitation. Forbidden phrases:
    'could not compute', 'unable to compute', 'server error',
    'unknown'. Per CS-04/CS-05 TERMINOLOGIST methodology (strategy 34
    in GLOBAL_KNOWLEDGE.md)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG"
    )
    assert r.status_code == 200
    msg = str(_param_value(r.json(), "message") or "")
    forbidden = ["could not compute", "unable to compute", "server error",
                 "unknown error"]
    for phrase in forbidden:
        assert phrase.lower() not in msg.lower(), (
            f"Display mismatch message MUST NOT use clinically misleading "
            f"phrase {phrase!r}; got {msg!r}. The terminological fact is "
            "'the display is incorrect', not a server limitation."
        )


# ===========================================================================
# Lens 2 — Cross-operation canonical agreement (VS ↔ CS $validate-code)
# ===========================================================================
# Spec: FHIR R4 §4.9.3 — ValueSet/$validate-code Out Parameters share
# the structural shape with CodeSystem/$validate-code (cross-reference).
# Same (system, code, display) validated via both operations MUST
# return same result, same message shape, same canonical display.
#
# VS-05 HISTORIAN strategy 53 (cross-handler byte-exact parity probe)
# verified this on a single system. TERMINOLOGIST extends the
# parametrization to 3 seeded systems + display-mismatch case.

@pytest.mark.parametrize(
    "system, code, display, label",
    SEEDED_SYSTEMS,
    ids=[s[3] for s in SEEDED_SYSTEMS],
)
def test_t20_vs_and_cs_validate_agree_on_result_for_known_code(
    fhir_client, system, code, display, label
):
    """For a known code, VS/$validate-code and CS/$validate-code MUST
    return the same `result` value (both true). Catches silent drift
    if a future fix touches one handler but not the sibling."""
    r_vs = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={system}&code={code}"
    )
    r_cs = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
    )
    assert r_vs.status_code == 200
    assert r_cs.status_code == 200
    assert _param_value(r_vs.json(), "result") is True
    assert _param_value(r_cs.json(), "result") is True


@pytest.mark.parametrize(
    "system, code, display, label",
    SEEDED_SYSTEMS,
    ids=[s[3] for s in SEEDED_SYSTEMS],
)
def test_t21_vs_and_cs_validate_agree_on_canonical_display(
    fhir_client, system, code, display, label
):
    """For a known code, VS/$validate-code and CS/$validate-code MUST
    return the same Out `display` (the engine canonical preferred term
    for that code). Catches silent drift if a future fix touches one
    handler's display resolution but not the sibling."""
    r_vs = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={system}&code={code}"
    )
    r_cs = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
    )
    assert r_vs.status_code == 200
    assert r_cs.status_code == 200
    vs_display = _param_value(r_vs.json(), "display")
    cs_display = _param_value(r_cs.json(), "display")
    assert vs_display == cs_display == display, (
        f"VS and CS $validate-code MUST agree on canonical display for "
        f"({label}: system={system!r}, code={code!r}). Expected "
        f"{display!r}; VS={vs_display!r}, CS={cs_display!r}."
    )


@pytest.mark.parametrize(
    "system, code, display, label",
    SEEDED_SYSTEMS,
    ids=[s[3] for s in SEEDED_SYSTEMS],
)
def test_t22_vs_and_cs_validate_agree_on_canonical_system(
    fhir_client, system, code, display, label
):
    """For a known code, VS/$validate-code and CS/$validate-code MUST
    return the same Out `system` (canonical URI). Catches silent drift
    if a future fix touches one handler's canonical_system_uri
    resolution but not the sibling (CR-011)."""
    r_vs = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={system}&code={code}"
    )
    r_cs = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
    )
    assert r_vs.status_code == 200
    assert r_cs.status_code == 200
    vs_system = _param_value(r_vs.json(), "system")
    cs_system = _param_value(r_cs.json(), "system")
    assert vs_system == cs_system == system, (
        f"VS and CS $validate-code MUST agree on canonical system for "
        f"({label}). Expected {system!r}; VS={vs_system!r}, "
        f"CS={cs_system!r}."
    )


def test_t23_vs_and_cs_validate_agree_on_display_mismatch_message(fhir_client):
    """For the same (system, code, wrong display) on both operations,
    the display mismatch message MUST agree byte-exact. Catches silent
    drift if a future fix changes the message format on one handler
    but not the sibling (CS-03 TERMINOLOGIST test_t90 + VS-05 SKEPTIC
    test_s53 set the precedent)."""
    wrong = "wrong-display-value"
    r_vs = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={wrong}"
    )
    r_cs = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={wrong}"
    )
    assert r_vs.status_code == 200
    assert r_cs.status_code == 200
    vs_msg = _param_value(r_vs.json(), "message")
    cs_msg = _param_value(r_cs.json(), "message")
    assert vs_msg == cs_msg, (
        f"VS and CS $validate-code display mismatch message MUST agree "
        f"byte-exact. VS={vs_msg!r}, CS={cs_msg!r}."
    )


# ===========================================================================
# Lens 3 — CodeableConcept per-coding display semantics
# ===========================================================================
# Spec: FHIR R4 ValueSet/$validate-code In `codeableConcept` (0..1):
#   "A full codeableConcept to validate. The server returns true if
#   one of the coding values is in the code system, and may also
#   validate that the codings are not in conflict with each other if
#   more than one is present".
# Source:
#   https://hl7.org/fhir/R4/operation-valueset-validate-code.html
#
# VS-05 SKEPTIC QA-070 wired `_extract_all_coding_pairs_from_codeable_concept`
# (all-pairs helper) into `vs_validate_post` + batch dispatcher. The
# TERMINOLOGIST lens verifies: when multiple codings are supplied, the
# Out `display` reflects the MATCHED coding's canonical preferred term
# — NOT the first coding's display.

def test_t30_codeable_concept_out_display_reflects_matched_coding(fhir_client):
    """When codeableConcept has [INVALID, VALID], the Out `display`
    MUST reflect the VALID coding's canonical preferred term — NOT the
    INVALID first coding's display. Per CS-03 SKEPTIC AUDIT-002 +
    VS-05 SKEPTIC AUDIT-002, per-coding display is NOT enforced, but
    the MATCHED coding's canonical MUST be what the Out `display`
    shows."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": SNOMED_URI,
                            "code": "INVALID-CODE-1",
                            "display": "Invalid First Coding Display",
                        },
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_T2DM_CODE,
                            "display": "Valid Second Coding Display",
                        },
                    ]
                },
            }
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    response_body = r.json()
    assert _param_value(response_body, "result") is True
    # Out `display` MUST be the engine canonical for the MATCHED code
    # (SNOMED_T2DM_CODE = "Type 2 diabetes mellitus"), NOT the client-
    # supplied display for the matched coding ("Valid Second Coding
    # Display").
    display = _param_value(response_body, "display")
    assert display == SNOMED_T2DM_DISPLAY, (
        f"Out `display` MUST reflect the MATCHED coding's engine canonical "
        f"preferred term ({SNOMED_T2DM_DISPLAY!r}); got {display!r}. The "
        "TS-02 TERMINOLOGIST QA-029 fix preserves canonical precedence "
        "even when client supplies a display for the matched coding."
    )


def test_t31_codeable_concept_out_code_reflects_matched_coding(fhir_client):
    """When codeableConcept has [INVALID, VALID], the Out `code` MUST
    reflect the MATCHED coding's code, not the first coding's code.
    Mirrors Lens 3 Out `display` contract."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "INVALID-CODE-1"},
                        {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                    ]
                },
            }
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    response_body = r.json()
    assert _param_value(response_body, "result") is True
    # Out `code` MUST be the matched coding's code.
    assert _param_value(response_body, "code") == SNOMED_T2DM_CODE, (
        f"Out `code` MUST reflect the MATCHED coding's code "
        f"({SNOMED_T2DM_CODE!r}); got "
        f"{_param_value(response_body, 'code')!r}."
    )


def test_t32_codeable_concept_invalid_first_then_valid_no_per_coding_display(
    fhir_client,
):
    """Per CS-03 SKEPTIC AUDIT-002 + VS-05 SKEPTIC AUDIT-002, per-coding
    display is NOT enforced for codeableConcept. The MATCHED coding's
    canonical display wins regardless of any supplied display. This
    probe documents the current contract: even when the matched coding
    has a WRONG display, result=true (any-match-wins) AND Out `display`
    is the engine canonical."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "INVALID-CODE-1"},
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_T2DM_CODE,
                            "display": "WRONG-DISPLAY-FOR-VALID-CODE",
                        },
                    ]
                },
            }
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    response_body = r.json()
    # Per AUDIT-002, result=true even when matched coding has wrong display.
    assert _param_value(response_body, "result") is True
    # Out `display` MUST be engine canonical, not the wrong supplied display.
    assert _param_value(response_body, "display") == SNOMED_T2DM_DISPLAY


# ===========================================================================
# Lens 4 — Patient-friendly name quality (engine canonical is clinically sensible)
# ===========================================================================
# Spec: FHIR R4 ValueSet/$validate-code Out `display` (0..1 string):
#   "A display to show to the user when the system doesn't know what
#   to do with the code, or to verify the code is the right one."
# The implementation sources Out `display` from `code_info.name`
# (engine canonical preferred term). For SNOMED/ICD-10-CM/RxNorm,
# `code_info.name` is the clinically sensible preferred term from the
# source-of-truth. Lens 4 verifies the engine canonical IS the
# clinically sensible name (not a technical long-name).
#
# The conformance fixture seeds canonical PT strings that ARE
# clinically sensible (e.g., "Type 2 diabetes mellitus" not "T2DM").
# Patient-friendly (PF) surfacing on $validate-code is OUT OF SCOPE
# (PF is surfaced via $lookup custom properties, not via $validate-
# code Out `display`). The carry-forward GAP-T01 / CF-TERMINOLOGIST-01
# applies to $expand, NOT $validate-code.

@pytest.mark.parametrize(
    "system, code, expected_display, label",
    SEEDED_SYSTEMS,
    ids=[s[3] for s in SEEDED_SYSTEMS],
)
def test_t40_out_display_is_clinically_sensible(
    fhir_client, system, code, expected_display, label
):
    """The Out `display` returned by VS/$validate-code for a known code
    MUST be the clinically sensible canonical preferred term. For
    SNOMED T2DM: 'Type 2 diabetes mellitus' (not 'T2DM' or a code).
    For ICD-10-CM E11: 'Type 2 diabetes mellitus'. For RxNorm: the
    full drug name '24 HR metformin 500 MG Oral Tablet'.

    The conformance fixture seeds these as the `STR` column in
    `mrconso`; the engine sources `code_info.name` from there. A
    regression that returns a technical code, an empty string, or a
    non-clinical identifier would silently degrade clinical workflow.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={system}&code={code}"
    )
    assert r.status_code == 200
    display = _param_value(r.json(), "display")
    assert display == expected_display, (
        f"Out `display` for ({label}) MUST be the clinically sensible "
        f"canonical preferred term {expected_display!r}; got {display!r}. "
        "The engine canonical name is sourced from `code_info.name` "
        "(mrconso.STR); a regression that returns a technical code or "
        "empty string would silently degrade clinical workflow."
    )
    # Sanity check: display MUST NOT be the raw code value.
    assert display != code, (
        f"Out `display` MUST NOT be the raw code value {code!r}; got "
        f"{display!r}."
    )


# ===========================================================================
# Lens 5 — Implicit value set clinical safety
# ===========================================================================
# Spec: FHIR R4 §4.9.3 — when `url` is supplied alone (no `system` +
# `code`), the server validates whether the code is in the implicit
# value set defined by the URL form. For an implicit all-of-system
# value set (`url=<system-uri>` or `url=<system-uri>/vs`), any code in
# the underlying code system SHOULD validate as result=true.
# Source:
#   https://hl7.org/fhir/R4/valueset-operation-validate-code.html
#
# medterm4ds does NOT scope membership to a persisted ValueSet today
# (AGENTS.md NOT A BUG registry). The implementation reduces membership
# to "is the code in the underlying code system". The TERMINOLOGIST
# lens verifies the implicit URL form is ACCEPTED and that a code in
# the underlying system validates as result=true.

def test_t50_implicit_valueset_url_system_uri_validates_known_code(fhir_client):
    """When `url=<code-system-uri>` (implicit all-of-system value set)
    and `code` is a known code in the underlying system, the result
    MUST be true. Clinically: validating a SNOMED code against the
    all-of-SNOMED implicit value set should succeed if the code exists
    in SNOMED."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?url={SNOMED_URI}"
        f"&system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True, (
        "Validating a known SNOMED code against the implicit all-of-SNOMED "
        "value set (url=http://snomed.info/sct) MUST return result=true. "
        f"Got: {body!r}"
    )


def test_t51_implicit_valueset_url_with_unknown_code_returns_false(fhir_client):
    """When `url=<code-system-uri>` (implicit all-of-system value set)
    and `code` is NOT in the underlying system, the result MUST be
    false. Clinically: validating a fake code against all-of-SNOMED
    should fail with result=false (not 200 with result=true silent-
    wrong-answer)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?url={SNOMED_URI}"
        f"&system={SNOMED_URI}&code=FAKE-CODE-NOT-IN-SNOMED"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False, (
        "Validating an unknown code against the implicit all-of-SNOMED "
        "value set MUST return result=false. Got: " + str(body)
    )


def test_t52_implicit_valueset_snomed_intensional_url_accepted(fhir_client):
    """When `url=http://snomed.info/sct/{code}?fhir_vs=isa` (SNOMED
    intensional URL form) and `code` is a descendant of the seeded
    intensional root, the result should be true per spec. medterm4ds
    does NOT scope membership to the intensional expansion today
    (AGENTS.md NOT A BUG registry); the probe verifies the URL form is
    ACCEPTED without 500 and the response is a Parameters body."""
    # T2DM (44054006) is a descendant of DM (73211009) in the seeded fixture.
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code"
        f"?url=http://snomed.info/sct/{SNOMED_DM_CODE}?fhir_vs=isa"
        f"&system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    # Implementation accepts and reduces to code-system-presence check.
    assert r.status_code == 200, (
        f"SNOMED intensional URL form on $validate-code MUST be accepted "
        f"without 5xx; got {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    # result MUST be present (1..1 per spec).
    assert _has_param(body, "result")


# ===========================================================================
# Lens 6 — `abstract` parameter clinical safety (default false)
# ===========================================================================
# Spec: FHIR R4 ValueSet/$validate-code In `abstract` (0..1 boolean,
# default false):
#   "If this parameter has the value true, the client is stating that
#   the validation is being performed in a context where abstract
#   concepts are permitted to be used/validated. If this parameter is
#   false, abstract codes are not permitted to be validated."
# Source:
#   https://hl7.org/fhir/R4/operation-valueset-validate-code.html
#
# Clinical safety: with `abstract=false` (default), abstract concepts
# SHOULD fail validation. medterm4ds accepts but ignores the param
# today (AGENTS.md NOT A BUG registry — engine has no abstract-
# flagging data; same shape as CodeSystem/$validate-code). The
# TERMINOLOGIST lens pins the CURRENT accepted-no-op behavior AND
# documents the clinical-safety gap for a future engine enhancement.

def test_t60_abstract_false_accepted_for_concrete_code(fhir_client):
    """With `abstract=false` (default), a concrete concept (the seeded
    fixture has only concrete codes) MUST validate as result=true.
    The implementation accepts the param as no-op per AGENTS.md NOT A
    BUG registry."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&abstract=false"
    )
    assert r.status_code == 200
    assert _param_value(r.json(), "result") is True


def test_t61_abstract_true_accepted_for_concrete_code(fhir_client):
    """With `abstract=true`, a concrete concept MUST still validate as
    result=true (concrete concepts are not excluded by abstract=true).
    The implementation accepts the param as no-op per AGENTS.md NOT A
    BUG registry."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&abstract=true"
    )
    assert r.status_code == 200
    assert _param_value(r.json(), "result") is True


def test_t62_abstract_param_accepted_no_5xx(fhir_client):
    """The `abstract` param MUST be accepted without 500 — verifies
    FastAPI's permissive default doesn't crash on the boolean param.
    (Same shape as version/offset per AGENTS.md NOT A BUG registry.)"""
    for value in ("true", "false"):
        r = fhir_client.get(
            f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
            f"&code={SNOMED_T2DM_CODE}&abstract={value}"
        )
        assert r.status_code < 500, (
            f"abstract={value} MUST NOT cause 5xx; got {r.status_code}."
        )


# ===========================================================================
# Lens 7 — `inferSystem` clinical correctness
# ===========================================================================
# Spec: FHIR R4 ValueSet/$validate-code In `inferSystem` (0..1 boolean):
#   "If set to true, the client is stating that the server should
#   infer the system from the code. The server MAY decline to do so."
# Source:
#   https://hl7.org/fhir/R4/operation-valueset-validate-code.html
#
# Clinical safety: when code is ambiguous (exists in multiple systems),
# inference may give wrong answer clinically. medterm4ds does NOT
# implement inference today (AGENTS.md NOT A BUG registry — no code-
# uniqueness registry). The TERMINOLOGIST lens verifies the param is
# ACCEPTED without 500 (NOT that inference succeeds).

def test_t70_infer_system_accepted_without_5xx(fhir_client):
    """The `inferSystem` param MUST be accepted without 5xx. The
    implementation may decline to infer (per spec); the probe verifies
    no crash, not that inference succeeds."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code"
        f"?code={SNOMED_T2DM_CODE}&inferSystem=true"
    )
    # Implementation requires system for $validate-code; with inferSystem
    # but no system, returns 400 (graceful rejection, not 5xx).
    assert r.status_code in (200, 400), (
        f"inferSystem=true MUST be accepted without 5xx; got "
        f"{r.status_code}. Body: {r.text[:300]}"
    )


def test_t71_infer_system_with_system_accepted(fhir_client):
    """When inferSystem=true AND system is supplied, the param is
    accepted; the system+code validate normally."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&inferSystem=true"
    )
    assert r.status_code == 200
    assert _param_value(r.json(), "result") is True


# ===========================================================================
# Lens 8 — Unknown-code clinical-actionable message
# ===========================================================================
# Spec: FHIR R4 ValueSet/$validate-code Out `message` (0..1 string):
#   "Error details about the validate operation if result=false."
# Clinical-actionable contract: the message MUST include BOTH the code
# AND the system URI so a clinician's EHR can present a meaningful
# error (e.g., "Code X is not valid in code system Y"). A message
# that drops either would degrade clinical workflow. Mirrors CS-03
# TERMINOLOGIST test_t91 on the VS surface.

def test_t80_unknown_code_message_includes_code_and_system(fhir_client):
    """When result=false due to unknown code, the message MUST cite
    both the code AND the system URI. The message is clinically
    actionable: a clinician's EHR can display the rejection in
    context. A message that drops either would degrade clinical
    workflow."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        "&code=DEFINITELY-UNKNOWN-CODE"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False
    msg = _param_value(body, "message")
    assert isinstance(msg, str)
    assert "DEFINITELY-UNKNOWN-CODE" in msg, (
        f"Unknown-code message MUST cite the code; got {msg!r}."
    )
    assert SNOMED_URI in msg, (
        f"Unknown-code message MUST cite the system URI; got {msg!r}."
    )


@pytest.mark.parametrize(
    "system, code, expected_display, label",
    SEEDED_SYSTEMS,
    ids=[s[3] for s in SEEDED_SYSTEMS],
)
def test_t81_unknown_code_message_includes_system_for_each_seeded_system(
    fhir_client, system, code, expected_display, label
):
    """Parametrized: for each seeded system, validating an unknown code
    MUST return result=false AND a message citing the system URI.
    Catches silent drift if a future fix changes the message format on
    one system's path but not the others."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={system}"
        "&code=UNKNOWN-CODE-XYZ"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False
    msg = str(_param_value(body, "message") or "")
    assert system in msg, (
        f"Unknown-code message for system {label} ({system!r}) MUST cite "
        f"the system URI; got {msg!r}."
    )


# ===========================================================================
# Lens 9 — Cross-system canonical display parametrization
# ===========================================================================
# Spec: FHIR R4 ValueSet/$validate-code Out `display` (0..1 string):
#   "A display to show to the user."
# The implementation sources Out `display` from `code_info.name`
# (engine canonical preferred term). For each seeded system, the Out
# `display` MUST match the engine canonical name for that code. This
# is a regression guard against future per-system drift.
#
# (Lens 9 is partially covered by test_t40 and test_t21 above; the
# following probes verify the POST path AND the cross-system shape
# consistency.)

@pytest.mark.parametrize(
    "system, code, expected_display, label",
    SEEDED_SYSTEMS,
    ids=[s[3] for s in SEEDED_SYSTEMS],
)
def test_t90_post_path_canonical_display_consistent(
    fhir_client, system, code, expected_display, label
):
    """POST $validate-code with system+code Parameters body MUST return
    Out `display` matching the engine canonical for the matched code.
    Mirrors Lens 1/4 contract on the POST path."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": system},
            {"name": "code", "valueCode": code},
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200
    response_body = r.json()
    assert _param_value(response_body, "result") is True
    assert _param_value(response_body, "display") == expected_display


def test_t91_get_post_parity_on_canonical_display(fhir_client):
    """GET and POST $validate-code for the same (system, code) MUST
    return the same Out `display`. Mirrors VS-04 EXPLORER strategy 50
    (GET↔POST parity) on the canonical-display axis."""
    # GET path
    r_get = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    # POST path
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
        ],
    }
    r_post = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r_get.status_code == 200
    assert r_post.status_code == 200
    get_display = _param_value(r_get.json(), "display")
    post_display = _param_value(r_post.json(), "display")
    assert get_display == post_display == SNOMED_T2DM_DISPLAY, (
        f"GET and POST MUST agree on canonical display; GET={get_display!r}, "
        f"POST={post_display!r}."
    )


# ===========================================================================
# Lens 10 — Carry-forward verification
# ===========================================================================
# Verify prior carry-forwards remain in their expected state on the VS
# surface. These are NOT bugs to file; they are contract pins.

def test_t100_cf_skeptic_cs03_01_closed_vs_validate_enforces_display_mismatch(
    fhir_client,
):
    """CF-SKEPTIC-CS03-01 was CLOSED in VS-05 SKEPTIC QA-069. This
    probe is the load-bearing regression guard: if the display-
    mismatch enforcement regresses on the VS surface, this probe
    fires loudly. Mirrors CS-03 TERMINOLOGIST test_t60 forward-looking
    pin (carry-forward-as-probe pattern)."""
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=WRONG-DISPLAY"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False, (
        "VS/$validate-code MUST enforce display mismatch (CF-SKEPTIC-CS03-01 "
        "CLOSED in VS-05 SKEPTIC QA-069). Got result=true — regression."
    )
    msg = str(_param_value(body, "message") or "")
    assert "WRONG-DISPLAY" in msg
    assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY


def test_t101_cf_terminologist_vs01_01_out_of_scope_for_validate_code(fhir_client):
    """CF-TERMINOLOGIST-VS01-01 (supplied-display echo on
    `_expand_intensional`) is OUT OF SCOPE for VS/$validate-code. The
    VS/$validate-code surface has its own display-mismatch
    enforcement (CF-SKEPTIC-CS03-01 CLOSED), which OVERRIDES any
    client-supplied display with the engine canonical. This probe
    documents the structural separation: supplied display on
    $validate-code does NOT get echoed verbatim (it triggers mismatch
    enforcement when it differs from canonical)."""
    # Wrong supplied display → result=false + canonical in Out display.
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=client-supplied-wrong-display"
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False
    # The Out display is the engine canonical, NOT the client's wrong display.
    assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY
    assert _param_value(body, "display") != "client-supplied-wrong-display"
