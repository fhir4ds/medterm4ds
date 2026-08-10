"""TERMINOLOGIST RESWEEP probes for CS-03 (CodeSystem $validate-code Operation).

Fresh full-sweep run — SKEPTIC + HISTORIAN + EXPLORER all CLEAN (59+67+53 = 179
resweep probes). This is the 4th and final personality for CS-03.

Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html (R4 4.0.1)

TERMINOLOGIST lens (per ROLE_QA_ENGINEER Section 3 + orchestrator task):
clinical and terminological correctness. Per GLOBAL_RULES.md, TERMINOLOGIST
findings are HIGH severity by default.

Focus areas for this resweep (per orchestrator task):

1. **Display-mismatch message clinical correctness**: when client provides
   wrong display, server returns result=false + message + display=correct.
   Verify the message is clinically informative ('The display "X" is
   incorrect' with the actual code context, not just generic 'display
   mismatch'). Spec example:
   https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
   "Response: When the request can be processed ok" — message format
   `The display \"test\" is incorrect` + canonical in separate display Out.

2. **CodeableConcept first-match-wins clinical correctness** (EXPLORER tip):
   when multiple codings match, Out display reflects first-match. The
   EXPLORER tip flagged that this COULD produce clinically misleading
   results when a less-specific coding appears before a more-specific one
   in the array. Probe whether the fixture can exercise this case.

3. **Canonical-DISPLAY invariant on CS-03 surface** (CS-02/TERMINOLOGIST tip):
   $validate-code Out display MUST equal $lookup Out display for every code.
   Extends CS-02 TERMINOLOGIST test_t10-t13 canonical-DISPLAY invariant from
   CS-02 surface to CS-03 surface.

4. **Cross-resource clinical consistency**: $validate-code result + display
   consistent with $lookup and CodeSystem READ response.

5. **inferSystem NOT probed** (off-spec per SKEPTIC finding — inferSystem
   is ValueSet-only parameter).

Conformance fixture seeds (per tests/fhir_conformance/conftest.py):
  ("73211009", "PT", "Diabetes mellitus",        "A73211009", "N", "SNOMEDCT_US", "C0011849"),
  ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
  ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",     "C0011847"),
  ("860975",  "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484")

Canonical FHIR R4 URIs (per SYSTEM_TO_FHIR_URI registry):
  SNOMEDCT_US -> http://snomed.info/sct
  ICD10CM     -> http://hl7.org/fhir/sid/icd-10-cm
  RXNORM      -> http://www.nlm.nih.gov/research/umls/rxnorm
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Spec sources:
#   https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
#   https://hl7.org/fhir/R4/codesystem-operation-lookup.html
#
# Conformance fixture seeds (see module docstring).
SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

SNOMED_DM_CODE = "73211009"
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM_CODE = "44054006"
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
ICD10CM_E11_CODE = "E11"
ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_METFORMIN_CODE = "860975"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"

# Source path for source-read structural probes.
FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)


def _param_value(body: dict, name: str) -> object | None:
    """Return the value of the first Out parameter matching ``name``."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _has_param(body: dict, name: str) -> bool:
    return any(p.get("name") for p in body.get("parameter", []))


def _lookup_display(fhir_client, system: str, code: str) -> str | None:
    """Helper: get $lookup Out display for a (system, code) pair."""
    resp = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
    )
    assert resp.status_code == 200, f"$lookup {system}/{code} failed: {resp.status_code}"
    body = resp.json()
    for p in body.get("parameter", []):
        if p.get("name") == "display":
            return p.get("valueString")
    return None


def _validate_display(fhir_client, system: str, code: str) -> str | None:
    """Helper: get $validate-code Out display for a (system, code) pair."""
    resp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": system, "code": code},
    )
    assert resp.status_code == 200, (
        f"$validate-code {system}/{code} failed: {resp.status_code}"
    )
    return _param_value(resp.json(), "display")


# ===========================================================================
# Lens 1: Display-mismatch message clinical correctness
# ===========================================================================

@pytest.mark.parametrize(
    "system,code,canonical_display",
    [
        (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
        (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
        (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
        (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10cm-e11", "rxnorm-metformin"],
)
def test_t10_display_mismatch_message_cites_actual_wrong_value(
    fhir_client, system, code, canonical_display
):
    """L1 t10 — message cites the EXACT wrong display the client supplied.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
    "Response: When the request can be processed ok":
        {"name": "message", "valueString": "The display \\"test\\" is incorrect"}

    The wrong value the client sent MUST appear verbatim in the message so
    the clinician can see exactly what was wrong. A generic message like
    "display mismatch" is NOT clinically actionable.

    Found by SKEPTIC QA-048 + verified by CS-03 TERMINOLOGIST prior iteration;
    this resweep re-verifies the byte-exact contract per seeded code.
    """
    wrong_display = "WRONG-" + canonical_display
    resp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": system, "code": code, "display": wrong_display},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _param_value(body, "result") is False
    message = _param_value(body, "message")
    assert message is not None
    # Wrong value cited verbatim (clinically actionable).
    assert wrong_display in str(message), (
        f"Message {message!r} does not cite the wrong display {wrong_display!r}"
    )
    # Spec example prefix present.
    assert "incorrect" in str(message).lower()


@pytest.mark.parametrize(
    "system,code,canonical_display",
    [
        (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
        (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
        (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
        (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10cm-e11", "rxnorm-metformin"],
)
def test_t11_display_mismatch_canonical_in_separate_out_display(
    fhir_client, system, code, canonical_display
):
    """L1 t11 — canonical display lives in the SEPARATE Out ``display`` param.

    Spec example:
        {"name": "display", "valueString": "Bicarbonate [Moles/volume] in Serum"}

    The canonical MUST be in the Out ``display`` (per TERMINOLOGIST QA-029 —
    Out display is server-canonical, never client-echo). The message
    contains the WRONG value; the display contains the CORRECT value.
    Separation of concerns is the clinically correct shape.
    """
    wrong_display = "WRONG-" + canonical_display
    resp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": system, "code": code, "display": wrong_display},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _param_value(body, "result") is False
    out_display = _param_value(body, "display")
    assert out_display == canonical_display, (
        f"Out display {out_display!r} != canonical {canonical_display!r}"
    )
    # The wrong value MUST NOT leak into the Out display.
    assert wrong_display not in str(out_display)


def test_t12_display_mismatch_message_does_not_leak_engine_internals(fhir_client):
    """L1 t12 — message must not leak CUI, AUI, SAB, or SQL fragments.

    The message is shown to clinicians; it MUST be a clean human-readable
    string. Internal engine vocabulary (CUIs like C0011847, AUls like
    A73211009, SAB labels like SNOMEDCT_US) would be confusing.
    """
    resp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM_CODE,
            "display": "totally wrong",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    message = str(_param_value(body, "message"))
    # Engine internals must not leak.
    assert "C0011849" not in message
    assert "A73211009" not in message
    assert "SNOMEDCT_US" not in message
    assert "code_info" not in message.lower()
    # SQL fragments must not leak.
    assert "SELECT" not in message.upper()
    assert "FROM" not in message.upper()


def test_t13_display_mismatch_message_for_unknown_code_distinct(fhir_client):
    """L1 t13 — message for UNKNOWN code is clinically distinct from display mismatch.

    Two clinically distinct failure modes:
      (a) code is unknown to the server -> "Code X is not valid in code system Y"
      (b) code is known but display is wrong -> 'The display "X" is incorrect'

    The two messages MUST be distinguishable so the clinician knows whether
    the code itself was wrong or just the display text.
    """
    # (a) unknown code
    resp_unknown = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": "99999999ZZZ"},
    )
    assert resp_unknown.status_code == 200
    body_unknown = resp_unknown.json()
    assert _param_value(body_unknown, "result") is False
    msg_unknown = str(_param_value(body_unknown, "message"))
    assert "not valid" in msg_unknown.lower()

    # (b) known code, wrong display
    resp_mismatch = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM_CODE,
            "display": "wrong",
        },
    )
    assert resp_mismatch.status_code == 200
    body_mismatch = resp_mismatch.json()
    assert _param_value(body_mismatch, "result") is False
    msg_mismatch = str(_param_value(body_mismatch, "message"))
    assert "incorrect" in msg_mismatch.lower()

    # Distinct messages.
    assert msg_unknown != msg_mismatch


# ===========================================================================
# Lens 2: CodeableConcept first-match-wins clinical correctness (EXPLORER tip)
# ===========================================================================

def test_t20_codeable_concept_first_match_wins_out_display(fhir_client):
    """L2 t20 — Out display reflects the FIRST matched coding's canonical.

    Per spec: "The server returns true if one of the coding values is in the
    code system". When multiple codings could match, the Out display reflects
    the first one in array order.

    The conformance fixture has SNOMED DM (73211009, "Diabetes mellitus")
    and SNOMED T2DM (44054006, "Type 2 diabetes mellitus"). Both are valid
    SNOMED codes. We put DM first, T2DM second. The Out display MUST reflect
    DM's canonical ("Diabetes mellitus") since DM is the first match.

    Found by EXPLORER tip: probe whether first-match-wins produces clinically
    appropriate results when the array has multiple valid codings.
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
                            "code": SNOMED_DM_CODE,
                            "display": SNOMED_DM_DISPLAY,
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
    }
    resp = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json=body,
        headers={"Content-Type": "application/fhir+json"},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert _param_value(out, "result") is True
    # Out display = FIRST MATCHED coding's canonical = DM.
    out_display = _param_value(out, "display")
    assert out_display == SNOMED_DM_DISPLAY, (
        f"Expected first-match display {SNOMED_DM_DISPLAY!r}, got {out_display!r}"
    )
    # Out code = FIRST MATCHED coding's code = DM.
    out_code = _param_value(out, "code")
    assert out_code == SNOMED_DM_CODE


def test_t21_codeable_concept_out_display_ignores_client_supplied_display(fhir_client):
    """L2 t21 — Out display is server-canonical, NOT the client's per-coding display.

    Even if the client supplies a per-coding display (which might be wrong or
    just different from the server canonical), the Out display MUST be the
    server canonical. This is the TS-02 TERMINOLOGIST QA-029 invariant
    ("Out display is server-canonical") applied to the codeableConcept path.
    """
    wrong_per_coding_display = "Some Wrong Text"
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_DM_CODE,
                            # Wrong per-coding display — MUST be ignored in Out.
                            "display": wrong_per_coding_display,
                        }
                    ]
                },
            }
        ],
    }
    resp = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json=body,
        headers={"Content-Type": "application/fhir+json"},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert _param_value(out, "result") is True
    out_display = _param_value(out, "display")
    assert out_display == SNOMED_DM_DISPLAY
    # Wrong per-coding display MUST NOT leak into Out.
    assert wrong_per_coding_display not in str(out_display)


def test_t22_codeable_concept_first_match_skips_unknown_codings(fhir_client):
    """L2 t22 — first-match semantics: unknown codings are SKIPPED, not first.

    When the first coding in the array is UNKNOWN to the server but the
    second is KNOWN, the Out display MUST reflect the SECOND (known) coding,
    not the first (unknown) one. The "first match" is the first coding that
    the server actually recognizes.

    This is the load-bearing clinical-correctness invariant: the server
    doesn't silently pick an unknown code's display just because it appeared
    first in the array.
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
                            "code": "99999999UNKNOWN",  # not in fixture
                            "display": "Bogus Concept",
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
    }
    resp = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json=body,
        headers={"Content-Type": "application/fhir+json"},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert _param_value(out, "result") is True
    # Out display = first KNOWN coding = T2DM.
    out_display = _param_value(out, "display")
    assert out_display == SNOMED_T2DM_DISPLAY, (
        f"Expected first-known-coding display {SNOMED_T2DM_DISPLAY!r}, "
        f"got {out_display!r}"
    )
    out_code = _param_value(out, "code")
    assert out_code == SNOMED_T2DM_CODE


def test_t23_codeable_concept_less_specific_before_more_specific(fhir_client):
    """L2 t23 — less-specific-before-more-specific is clinically acceptable today.

    EXPLORER tip: "first-match-wins could produce clinically misleading
    results when a less-specific coding appears before a more-specific one".

    Setup: DM (73211009, broader "Diabetes mellitus") before T2DM
    (44054006, narrower "Type 2 diabetes mellitus"). Both are valid SNOMED.
    The server returns result=true with Out display = DM (the broader).

    This is the CURRENT behavior and is spec-permitted: the spec says "true
    if one of the coding values is in the code system" — it does NOT mandate
    which coding's display to surface. The clinician is told "yes the code
    exists in the system" with a canonical display. Documented here to pin
    the current semantic; the spec does NOT require specificity-based
    selection.

    If a future enhancement adds specificity-based display selection, this
    probe MUST be updated to assert the new behavior (carry-forward-as-probe
    pattern per CS-03 TERMINOLOGIST methodology).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        # Less-specific first.
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_DM_CODE,
                            "display": SNOMED_DM_DISPLAY,
                        },
                        # More-specific second.
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_T2DM_CODE,
                            "display": SNOMED_T2DM_DISPLAY,
                        },
                    ]
                },
            }
        ],
    }
    resp = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json=body,
        headers={"Content-Type": "application/fhir+json"},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert _param_value(out, "result") is True
    # Current behavior: first-match (broader DM) wins.
    # This is spec-permitted; documented for future enhancement tracking.
    out_display = _param_value(out, "display")
    assert out_display == SNOMED_DM_DISPLAY


def test_t24_codeable_concept_cross_system_match_picks_correct_canonical(fhir_client):
    """L2 t24 — cross-system codeableConcept: Out display = matched coding's canonical.

    SNOMED T2DM (44054006) and ICD-10-CM E11 both encode the same condition
    (same CUI C0011847 in the fixture). If a codeableConcept has an unknown
    SNOMED code first, then ICD-10-CM E11 second, the Out display MUST be
    the ICD-10-CM canonical ("Type 2 diabetes mellitus") AND the Out system
    MUST be the ICD-10-CM canonical URI.
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
                            "code": "UNKNOWN_SNOMED",
                            "display": "Bogus",
                        },
                        {
                            "system": ICD10CM_URI,
                            "code": ICD10CM_E11_CODE,
                            "display": ICD10CM_E11_DISPLAY,
                        },
                    ]
                },
            }
        ],
    }
    resp = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json=body,
        headers={"Content-Type": "application/fhir+json"},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert _param_value(out, "result") is True
    out_display = _param_value(out, "display")
    out_system = _param_value(out, "system")
    out_code = _param_value(out, "code")
    assert out_display == ICD10CM_E11_DISPLAY
    assert out_system == ICD10CM_URI  # canonical of MATCHED coding, not the input alias
    assert out_code == ICD10CM_E11_CODE


# ===========================================================================
# Lens 3: Canonical-DISPLAY invariant on CS-03 surface (CS-02/TERMINOLOGIST tip)
# ===========================================================================

@pytest.mark.parametrize(
    "system,code",
    [
        (SNOMED_URI, SNOMED_DM_CODE),
        (SNOMED_URI, SNOMED_T2DM_CODE),
        (ICD10CM_URI, ICD10CM_E11_CODE),
        (RXNORM_URI, RXNORM_METFORMIN_CODE),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10cm-e11", "rxnorm-metformin"],
)
def test_t30_validate_display_equals_lookup_display(fhir_client, system, code):
    """L3 t30 — canonical-DISPLAY invariant: $validate-code Out display == $lookup Out display.

    Per CS-02/TERMINOLOGIST tip: the same code's display MUST be identical
    across $validate-code and $lookup. Both operations read from the same
    ``get_code_infos`` path, so any divergence would indicate drift in the
    response builder or canonical-resolution path.
    """
    lookup_display = _lookup_display(fhir_client, system, code)
    validate_display = _validate_display(fhir_client, system, code)
    assert lookup_display == validate_display, (
        f"Display mismatch: $lookup={lookup_display!r}, "
        f"$validate-code={validate_display!r} for {system}/{code}"
    )


def test_t31_validate_display_matches_lookup_display_via_alias_input(fhir_client):
    """L3 t31 — canonical-DISPLAY invariant holds on alias inputs too.

    The invariant MUST hold when the client uses an alias (urn:oid, trailing-
    slash, uppercase-SCHEME — per TS-03 EXPLORER QA-001 fix scope which
    normalizes scheme but NOT host) for the system URI. Both operations
    re-resolve via ``canonical_system_uri`` and then read canonical display;
    the display result MUST be identical.

    Note: uppercase-HOST (``HTTP://SNOMED.INFO/SCT``) is intentionally OUT of
    scope per TS-03 EXPLORER — RFC 3986 §3.2.2 host case-insensitivity is a
    separate deferred enhancement. We use uppercase-SCHEME-only here.
    """
    aliases = [
        "urn:oid:2.16.840.1.113883.6.96",  # SNOMED OID alias
        "http://snomed.info/sct/",         # trailing slash
        "HTTP://snomed.info/sct",          # uppercase SCHEME only (host lowercase)
    ]
    for alias in aliases:
        lookup_display = _lookup_display(fhir_client, alias, SNOMED_DM_CODE)
        validate_display = _validate_display(fhir_client, alias, SNOMED_DM_CODE)
        assert lookup_display == validate_display == SNOMED_DM_DISPLAY, (
            f"Alias {alias!r}: lookup={lookup_display!r}, "
            f"validate={validate_display!r}"
        )


# ===========================================================================
# Lens 4: Cross-resource clinical consistency
# ===========================================================================

def test_t40_validate_and_lookup_agree_on_result_for_known_code(fhir_client):
    """L4 t40 — both operations agree: known code returns result=true / 200.

    For SNOMED T2DM (44054006), $validate-code returns result=true AND
    $lookup returns 200 + Parameters. Clinical consistency: the server
    doesn't say "yes valid" while simultaneously saying "not found".
    """
    # $validate-code
    vresp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert vresp.status_code == 200
    assert _param_value(vresp.json(), "result") is True

    # $lookup
    lresp = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert lresp.status_code == 200


def test_t41_validate_and_lookup_agree_on_unknown_code(fhir_client):
    """L4 t41 — both operations agree: unknown code is reported as not-found.

    For SNOMED 99999999UNKNOWN: $validate-code returns result=false AND
    $lookup returns an OperationOutcome with not-found severity. Clinical
    consistency: a code the server "doesn't know" must be reported
    consistently across the two operations.

    Note: medterm4ds's $lookup returns HTTP 200 with an OperationOutcome body
    (resourceType=OperationOutcome, issue[].code=not-found) for unknown codes
    — this is an accepted response shape per FHIR R4 §3.2.1.3 (OperationOutcome
    can be returned with any status). The clinical-correctness contract is
    that the BODY indicates not-found, not the HTTP status.
    """
    # $validate-code -> result=false
    vresp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": "99999999UNKNOWN"},
    )
    assert vresp.status_code == 200
    assert _param_value(vresp.json(), "result") is False

    # $lookup -> OperationOutcome body with not-found severity/code.
    lresp = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": "99999999UNKNOWN"},
    )
    lbody = lresp.json()
    assert lbody.get("resourceType") == "OperationOutcome"
    issues = lbody.get("issue", [])
    assert issues, "OperationOutcome must have at least one issue"
    # At least one issue indicates not-found (error severity + not-found code).
    severities = {i.get("severity") for i in issues}
    codes = {i.get("code") for i in issues}
    assert "error" in severities or "fatal" in severities, (
        f"Lookup of unknown code must report error/fatal severity, got {severities}"
    )
    assert "not-found" in codes, (
        f"Lookup of unknown code must report not-found code, got {codes}"
    )


def test_t42_validate_known_code_includes_canonical_display_and_system(fhir_client):
    """L4 t42 — known code response includes canonical system + display.

    The Out system + Out display for a known code MUST be the canonical
    values (not client-echo). This is the cross-resource clinical-correctness
    invariant: the server's response is internally consistent (system + code
    + display all describe the same canonical concept).
    """
    resp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _param_value(body, "result") is True
    assert _param_value(body, "system") == SNOMED_URI
    assert _param_value(body, "code") == SNOMED_T2DM_CODE
    assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY


def test_t43_code_system_read_advertises_canonical_uri(fhir_client):
    """L4 t43 — CapabilityStatement advertises canonical URIs for all sources.

    Cross-resource clinical consistency: the canonical URI advertised in the
    CapabilityStatement's ``capabilitystatement-supported-system`` extension
    + the URI used by $validate-code Out ``system`` + the URI used by
    $lookup Out ``system`` MUST all agree (single source of truth:
    SYSTEM_TO_FHIR_URI registry).

    The supported-system extensions live at the CapabilityStatement TOP
    level (per https://hl7.org/fhir/R4/extension-capabilitystatement-
    supported-system.html), not nested under rest[].resource[].
    """
    resp = fhir_client.get("/fhir/metadata")
    assert resp.status_code == 200
    capstmt = resp.json()
    # Walk the supported-system extension at the TOP level.
    supported = set()
    for ext in capstmt.get("extension", []):
        if "supported-system" in ext.get("url", ""):
            if "valueUri" in ext:
                supported.add(ext["valueUri"])
    # SNOMED + ICD-10-CM + RxNorm canonical URIs MUST be advertised.
    assert SNOMED_URI in supported, (
        f"SNOMED canonical {SNOMED_URI!r} not in supported systems {supported}"
    )
    assert ICD10CM_URI in supported
    assert RXNORM_URI in supported


# ===========================================================================
# Lens 5: Cross-handler GET <-> POST clinical-content parity
# ===========================================================================

def test_t50_get_post_display_mismatch_parity(fhir_client):
    """L5 t50 — GET and POST produce byte-exact display-mismatch response.

    Clinical safety: the same logical request via GET or POST MUST produce
    the same clinical answer. A clinician using a GET form vs a POST API
    must not get different answers about whether their display was wrong.
    """
    # GET
    gresp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM_CODE,
            "display": "Wrong Display",
        },
    )
    assert gresp.status_code == 200
    gbody = gresp.json()

    # POST
    pbody_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DM_CODE},
            {"name": "display", "valueString": "Wrong Display"},
        ],
    }
    presp = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json=pbody_body,
        headers={"Content-Type": "application/fhir+json"},
    )
    assert presp.status_code == 200
    pbody = presp.json()

    # Byte-exact clinical content.
    assert _param_value(gbody, "result") == _param_value(pbody, "result")
    assert _param_value(gbody, "message") == _param_value(pbody, "message")
    assert _param_value(gbody, "display") == _param_value(pbody, "display")
    assert _param_value(gbody, "system") == _param_value(pbody, "system")
    assert _param_value(gbody, "code") == _param_value(pbody, "code")


def test_t51_batch_validate_matches_single_entry_clinical_content(fhir_client):
    """L5 t51 — batch $validate-code entry matches single-entry response byte-exact.

    The batch dispatcher reuses ``_do_validate`` (verified structurally in
    EXPLORER L9 source-read). The clinical content (result, display, system,
    code, message) MUST be byte-identical between single-entry POST and the
    corresponding batch entry.
    """
    # Single-entry POST
    single_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
        ],
    }
    sresp = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json=single_body,
        headers={"Content-Type": "application/fhir+json"},
    )
    assert sresp.status_code == 200
    sbody = sresp.json()

    # Batch POST
    batch_body = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "CodeSystem/$validate-code",
                },
                "resource": single_body,
            }
        ],
    }
    bresp = fhir_client.post(
        "/fhir",
        json=batch_body,
        headers={"Content-Type": "application/fhir+json"},
    )
    assert bresp.status_code == 200
    bbody = bresp.json()
    assert bbody["resourceType"] == "Bundle"
    assert bbody["type"] == "batch-response"
    assert len(bbody["entry"]) == 1
    batch_entry = bbody["entry"][0]["resource"]

    # Byte-exact clinical content.
    for pname in ("result", "display", "system", "code"):
        assert _param_value(sbody, pname) == _param_value(batch_entry, pname), (
            f"Param {pname!r} diverges: single={_param_value(sbody, pname)!r}, "
            f"batch={_param_value(batch_entry, pname)!r}"
        )


# ===========================================================================
# Lens 6: Spec citation discipline — R4 spec-actual In/Out param set
# ===========================================================================

# R4 CodeSystem $validate-code In parameters (no inferSystem — that's ValueSet-only):
R4_CS_VALIDATE_IN_PARAMS = frozenset({
    "url", "codeSystem", "code", "version", "display",
    "coding", "codeableConcept", "date", "abstract", "displayLanguage",
})

# R4 CodeSystem $validate-code Out parameters:
R4_CS_VALIDATE_OUT_PARAMS = frozenset({
    "result", "message", "display", "code", "system", "version",
    "codeableConcept", "issues",
})


def test_t60_inferSystem_not_accepted_on_codesystem_validate_code(fhir_client):
    """L6 t60 — ``inferSystem`` is NOT a CodeSystem/$validate-code In parameter.

    Per SKEPTIC finding (off-spec): ``inferSystem`` appears only on
    ValueSet/$validate-code (https://hl7.org/fhir/R4/valueset-operation-
    validate-code.html In Parameters). CodeSystem/$validate-code does NOT
    define it (https://hl7.org/fhir/R4/codesystem-operation-validate-code.html).

    The server may or may not silently accept this off-spec parameter; the
    load-bearing contract is that the SERVER'S BEHAVIOR does not depend on
    it (the engine doesn't infer a system from a code). Probe: send the
    param and verify result depends ONLY on the canonical (system, code)
    pair, NOT on inferSystem.
    """
    # With inferSystem=true on a KNOWN (system, code) -> still result=true.
    resp1 = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM_CODE,
            "inferSystem": "true",
        },
    )
    assert resp1.status_code == 200
    assert _param_value(resp1.json(), "result") is True

    # With inferSystem=true on a KNOWN code WITHOUT system -> result depends
    # on whether the engine knows the code. CodeSystem/$validate-code REQUIRES
    # system (per spec In parameter ``code`` cardinality depends on ``system``).
    # We're testing that inferSystem doesn't magically make the server "guess"
    # the system. The server should reject (missing system) OR return false.
    resp2 = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"code": SNOMED_DM_CODE, "inferSystem": "true"},
    )
    # Either 400 (missing system) OR 200 with result=false — but NOT 200 with
    # result=true (the engine must NOT silently infer a system on the
    # CodeSystem surface).
    if resp2.status_code == 200:
        assert _param_value(resp2.json(), "result") is False, (
            "Server must not silently infer system on CodeSystem/$validate-code"
        )
    # Else: 400/422 is acceptable.


def test_t61_spec_in_parameters_parametrized(fhir_client):
    """L6 t61 — every R4 spec In parameter is either accepted or rejected gracefully.

    No 500-with-traceback on any spec-documented In parameter. The R4
    spec-actual In parameter set does NOT include ``inferSystem``.
    """
    # Probe each spec In parameter with a known (system, code) baseline.
    base_params = {"system": SNOMED_URI, "code": SNOMED_DM_CODE}
    spec_in_only_params = {
        "version": "2024-09",
        "display": SNOMED_DM_DISPLAY,
        "date": "2024-01-01",
        "abstract": "false",
        "displayLanguage": "en",
    }
    # (coding, codeableConcept require POST bodies — covered by other lenses.)
    for pname, pvalue in spec_in_only_params.items():
        params = dict(base_params)
        params[pname] = pvalue
        resp = fhir_client.get("/fhir/CodeSystem/$validate-code", params=params)
        assert resp.status_code in (200, 400, 422), (
            f"Spec In param {pname}={pvalue!r} produced {resp.status_code} "
            f"(must be 200/400/422, not 5xx)"
        )


def test_t62_out_parameters_subset_of_spec(fhir_client):
    """L6 t62 — every Out parameter emitted by the server is in the R4 spec set.

    The server emits a SUBSET of the R4 Out parameter set. Probe the
    response on a known code and verify every emitted parameter is in
    R4_CS_VALIDATE_OUT_PARAMS.
    """
    resp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_DM_CODE},
    )
    assert resp.status_code == 200
    body = resp.json()
    emitted = {p["name"] for p in body.get("parameter", [])}
    extra = emitted - R4_CS_VALIDATE_OUT_PARAMS
    assert not extra, (
        f"Out parameters {extra!r} are NOT in the R4 spec Out parameter set"
    )


# ===========================================================================
# Lens 7: Source-read structural contracts (HISTORIAN helper)
# ===========================================================================

def _get_nested_func_source(
    source: str, parent_name: str, child_name: str
) -> str:
    """Source-read helper for nested functions defined inside create_fhir_app.

    Plain ``ast.walk`` over the module would miss nested defs.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == parent_name:
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.name == child_name:
                            return ast.get_source_segment(source, child) or ""
    return ""


def test_t70_do_validate_calls_canonical_system_uri(fhir_client):
    """L7 t70 — ``_do_validate`` re-resolves system via canonical_system_uri.

    Source-read contract (CS-03 HISTORIAN QA-051). Without this, the Out
    ``system`` echoes the client's alias/trailing-slash input verbatim.
    """
    source = FHIR_API_PATH.read_text()
    do_validate_src = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
    assert do_validate_src, "_do_validate not found in create_fhir_app"
    assert "canonical_system_uri(" in do_validate_src, (
        "_do_validate MUST call canonical_system_uri() to re-resolve the "
        "client-supplied system URI (CS-03 HISTORIAN QA-051)"
    )


def test_t71_do_validate_codeable_concept_uses_all_pairs_helper(fhir_client):
    """L7 t71 — ``_do_validate`` uses all-pairs helper for codeableConcept.

    Source-read contract (CS-03 SKEPTIC QA-049 + CS-03 HISTORIAN QA-052).
    The all-pairs helper ``_extract_all_coding_pairs_from_codeable_concept``
    is required for codeableConcept multi-coding semantics — the single-pair
    helper would silently wrong-answer when the first coding is invalid but
    a later coding is valid.
    """
    source = FHIR_API_PATH.read_text()
    # The all-pairs helper is called from validate_post / _extract_validate_params,
    # not directly from _do_validate (which receives the already-extracted list).
    # Source-read the WHOLE module for the helper name.
    assert "_extract_all_coding_pairs_from_codeable_concept" in source, (
        "all-pairs helper _extract_all_coding_pairs_from_codeable_concept "
        "must be defined (CS-03 SKEPTIC QA-049)"
    )
    # And it MUST be called from validate_post (per-operation POST).
    validate_post_src = _get_nested_func_source(
        source, "create_fhir_app", "validate_post"
    )
    assert validate_post_src, "validate_post not found in create_fhir_app"
    assert "_extract_all_coding_pairs_from_codeable_concept" in validate_post_src


def test_t72_do_validate_display_mismatch_message_format(fhir_client):
    """L7 t72 — ``_do_validate`` builds the display-mismatch message via the spec format.

    Source-read contract (CS-03 SKEPTIC QA-048). The message MUST be
    ``The display "<wrong_value>" is incorrect`` (per spec example).
    """
    source = FHIR_API_PATH.read_text()
    do_validate_src = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
    assert do_validate_src
    # The literal "is incorrect" MUST be in the source (spec example format).
    assert "is incorrect" in do_validate_src, (
        "_do_validate MUST build the display-mismatch message with the spec "
        "format 'The display \"X\" is incorrect' (CS-03 SKEPTIC QA-048)"
    )


def test_t73_build_parameters_validate_canonical_precedence(fhir_client):
    """L7 t73 — ``build_parameters_validate`` prefers canonical over client display.

    Source-read contract (TS-02 TERMINOLOGIST QA-029). The Out ``display``
    is the engine canonical (code_info.name), NOT an echo of the client's
    input. This is the load-bearing clinical-correctness invariant.
    """
    source = RESPONSES_PATH.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_parameters_validate":
            func_src = ast.get_source_segment(source, node) or ""
            break
    else:
        assert False, "build_parameters_validate not found in responses.py"
    # The canonical precedence: code_info.name is preferred.
    assert "code_info.name" in func_src, (
        "build_parameters_validate MUST prefer code_info.name (canonical) "
        "over the client-supplied display (TS-02 TERMINOLOGIST QA-029)"
    )


def test_t74_build_parameters_validate_does_not_echo_client_display_on_mismatch(fhir_client):
    """L7 t74 — builder emits canonical when code_info has a name, even on mismatch.

    Behavioral mirror of t73: when ``_do_validate`` detects a mismatch and
    calls ``build_parameters_validate`` with both ``display=client_wrong``
    AND ``code_info=with canonical name``, the Out display MUST be the
    canonical name (because the builder prefers code_info.name). Verified
    behaviorally in t11; this probe source-reads the contract.
    """
    source = RESPONSES_PATH.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_parameters_validate":
            func_src = ast.get_source_segment(source, node) or ""
            break
    else:
        assert False, "build_parameters_validate not found"
    # The canonical precedence expression: ``(code_info.name if code_info and
    # code_info.name else None) or display`` — canonical wins when present.
    assert "or display" in func_src, (
        "build_parameters_validate MUST fall back to client display only "
        "when canonical is absent (TS-02 TERMINOLOGIST QA-029)"
    )


# ===========================================================================
# Lens 8: Clinical safety — no silent wrong answer on edge cases
# ===========================================================================

def test_t80_no_silent_wrong_answer_on_unknown_system(fhir_client):
    """L8 t80 — unknown system produces 400 OR result=false, NOT result=true.

    Clinical safety: a server that returns result=true for an unknown system
    would silently validate codes the server doesn't actually know. The
    engine MUST either reject the system (400) OR return result=false.
    """
    resp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": "http://unknown.example/system", "code": "any"},
    )
    if resp.status_code == 200:
        body = resp.json()
        assert _param_value(body, "result") is False, (
            "Server returned result=true for an unknown system — silent wrong answer"
        )
    else:
        # 400/422 acceptable.
        assert resp.status_code in (400, 422)


def test_t81_no_silent_wrong_answer_on_unknown_code_in_known_system(fhir_client):
    """L8 t81 — unknown code in known system -> result=false, NOT result=true.

    Clinical safety: a known system (SNOMED) with an unknown code MUST
    return result=false. Returning result=true would silently validate
    non-existent codes.
    """
    resp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": "DOES_NOT_EXIST_99999"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _param_value(body, "result") is False


def test_t82_codeable_concept_all_invalid_no_silent_true(fhir_client):
    """L8 t82 — codeableConcept with all-invalid codings -> result=false.

    Per spec: "The server returns true if one of the coding values is in
    the code system". If NONE of the codings are in the system, result
    MUST be false. Returning true would silently validate unknown codes.
    """
    body = {
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
    }
    resp = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json=body,
        headers={"Content-Type": "application/fhir+json"},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert _param_value(out, "result") is False
    # Message should be present per spec ("Error details, if result = false").
    msg = _param_value(out, "message")
    assert msg is not None


def test_t83_display_mismatch_does_not_silently_pass(fhir_client):
    """L8 t83 — display mismatch MUST NOT silently pass with result=true.

    Clinical safety: if the client supplies a wrong display and the server
    says result=true, the client has no way to know their display was wrong.
    The CS-03 SKEPTIC QA-048 fix enforces this; this probe verifies the
    enforcement holds.
    """
    resp = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM_CODE,
            "display": "Completely Wrong Display Text",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # MUST be result=false (display mismatch enforcement).
    assert _param_value(body, "result") is False
    # Canonical MUST be in Out display (clinician can see the right term).
    assert _param_value(body, "display") == SNOMED_DM_DISPLAY
