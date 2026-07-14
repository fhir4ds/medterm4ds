"""HISTORIAN iteration CS-03 — pattern-match against prior bug registry.

Spec: https://build.fhir.org/codesystem-operation-validate-code.html
       https://hl7.org/fhir/R4/codesystem-operation-validate-code.html (canonical R4)

HISTORIAN lens for CS-03 (CodeSystem $validate-code):

1. **Canonical system echo (CS-02 HISTORIAN QA-047 carry-forward)**:
   - ``_do_lookup`` was fixed in CS-02 to re-resolve the client-supplied
     ``system_uri`` through ``system_to_fhir_uri(fhir_uri_to_system(...))``
     before passing it to the response builder. The same drift exists in
     ``_do_validate`` — it passes the raw client input to
     ``build_parameters_validate``, which echoes it in the Out ``system``
     parameter. When the client sends an alias (``urn:oid:...``) or a
     trailing-slash variant, the Out ``system`` is non-canonical.
   - Pattern: client-input-as-canonical drift (count=4 already PROMOTED).

2. **Display mismatch edge cases (SKEPTIC QA-048 follow-up)**:
   - Empty client display (no display param) → MUST NOT trigger mismatch.
   - Whitespace-only client display → MUST NOT trigger mismatch (no
     canonical display to compare against; treated as absent).
   - Server has no display for the code → MUST NOT trigger mismatch
     (cannot validate against a missing canonical).

3. **CodeableConcept multi-coding correctness (SKEPTIC QA-049)**:
   - First match wins — verify when first coding has wrong display but
     valid code, result is true (display mismatch is NOT enforced on
     codeableConcept per SKEPTIC AUDIT note; only on system+code path).
   - All codings invalid → result=false with explanatory message.
   - CodeableConcept where the only coding has empty system → silently
     skipped (helper filters); not a crash.

4. **Documentation-vs-implementation drift (TS-01 HISTORIAN QA-007)**:
   - ``_do_validate`` has inline comments but no function-level docstring
     documenting the display-mismatch behavior added by SKEPTIC QA-048.
     This is a LOW documentation gap, not a behavioral bug.
   - ``_extract_all_coding_pairs_from_codeable_concept`` docstring is
     accurate (verified by reading the body).

5. **Boolean capitalization (CR-002 pattern)**:
   - ``result`` is a boolean parameter rendered as ``valueBoolean``.
     Python's ``json.dumps(False)`` emits ``false`` (lowercase) — JSON
     wire form is conformant. XML wire form is exercised separately by
     CR-002 probes; not re-probed here.

6. **Test-too-lenient audit (CS-01 HISTORIAN pattern)**:
   - Re-audit SKEPTIC's ``test_s41_validate_display_mismatch_returns_false_with_message_and_canonical``
     — does it assert the canonical display VALUE, or just presence +
     non-equality with the wrong input? If the impl returned any string
     that isn't the wrong input, the test would pass even if the value
     were garbage.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

SNOMED_URI = "http://snomed.info/sct"
SNOMED_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_DIABETES_MELLITUS = "73211009"  # canonical display: "Diabetes mellitus"
SNOMED_T2DM = "44054006"               # canonical display: "Type 2 diabetes mellitus"

ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.90"
ICD10CM_E11 = "E11"  # canonical display: "Type 2 diabetes mellitus"


def _has_param(body: dict, name: str) -> bool:
    return any(p.get("name") == name for p in body.get("parameter", []))


def _param_value(body: dict, name: str):
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


# ---------------------------------------------------------------------------
# Item 1: Canonical system echo — CS-02 HISTORIAN QA-047 carry-forward
# ---------------------------------------------------------------------------

def test_h10_validate_system_out_is_canonical_not_alias(fhir_client):
    """QA-051 / MEDIUM.

    The Out `system` parameter MUST be the canonical FHIR URI (from
    SYSTEM_TO_FHIR_URI registry), not the client-supplied alias. The
    ``_do_validate`` implementation today passes ``system_uri`` (the raw
    client input) verbatim to ``build_parameters_validate``, which then
    emits it in the Out ``system``.

    Pattern: client-input-as-canonical drift (TS-02 TERMINOLOGIST
    QA-029 / CS-02 HISTORIAN QA-047 shape — recurring count).

    Spec basis: FHIR R4 CodeSystem/$validate-code Out Parameters
    (https://hl7.org/fhir/R4/codesystem-operation-validate-code.html)
    do not explicitly state "canonical" for Out ``system``, but the
    TS-02 TERMINOLOGIST QA-029 + CS-02 HISTORIAN QA-047 + CS-02
    TERMINOLOGIST DECISION (a) lineage establishes that Out ``system``
    is the server-canonical URI of the resolved code system, not a
    client-input echo. CodeSystem/$validate-code has the same shape.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_OID_ALIAS,  # SNOMED CT OID alias
            "code": SNOMED_T2DM,
        },
    )
    assert r.status_code == 200, (
        f"validate-code with urn:oid alias MUST resolve; got {r.status_code}"
    )
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert sys_param is not None, "Out `system` parameter MUST be present"
    assert sys_param.get("valueUri") == SNOMED_URI, (
        f"Out `system` MUST be the canonical FHIR URI ({SNOMED_URI}), not "
        f"the client-supplied alias. Got {sys_param.get('valueUri')!r}. "
        f"Pattern: client-input-as-canonical drift (CS-02 HISTORIAN QA-047 "
        f"shape). Fix: in _do_validate, re-resolve to canonical via "
        f"`system_to_fhir_uri(fhir_uri_to_system(system_uri)) or system_uri`."
    )


def test_h11_validate_system_out_canonical_for_trailing_slash(fhir_client):
    """QA-051 regression — trailing-slash variant of the canonical URI."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": "http://snomed.info/sct/",  # trailing slash
            "code": SNOMED_T2DM,
        },
    )
    assert r.status_code == 200, (
        f"validate-code with trailing-slash URI MUST resolve; got {r.status_code}"
    )
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert sys_param is not None
    assert sys_param.get("valueUri") == SNOMED_URI, (
        f"Out `system` MUST be the canonical URI without trailing slash; "
        f"got {sys_param.get('valueUri')!r}"
    )


def test_h12_validate_system_out_canonical_when_already_canonical(fhir_client):
    """QA-051 negative control — when client passes the canonical URI, Out
    `system` is the same canonical URI (no drift). Confirms the fix does
    not double-translate canonical URIs."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r.status_code == 200
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert sys_param is not None
    assert sys_param.get("valueUri") == SNOMED_URI


def test_h13_validate_system_out_canonical_for_icd10cm_oid_alias(fhir_client):
    """QA-051 cross-system — ICD-10-CM OID alias. Confirms the canonical
    re-resolution works for non-SNOMED systems too (CS-02 HISTORIAN
    test_h10 only probed SNOMED)."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": ICD10CM_OID_ALIAS,
            "code": ICD10CM_E11,
        },
    )
    assert r.status_code == 200, (
        f"validate-code with ICD10CM OID alias MUST resolve; got {r.status_code}"
    )
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert sys_param is not None
    assert sys_param.get("valueUri") == ICD10CM_URI, (
        f"Out `system` MUST be the canonical ICD-10-CM URI ({ICD10CM_URI}); "
        f"got {sys_param.get('valueUri')!r}"
    )


# ---------------------------------------------------------------------------
# Item 2: Display mismatch edge cases (SKEPTIC QA-048 follow-up)
# ---------------------------------------------------------------------------

def test_h20_validate_no_display_param_returns_true_for_known_code(fhir_client):
    """QA-048 negative control — when the client supplies NO display param,
    display mismatch MUST NOT trigger. The comparison logic is gated on
    `display is not None`; an absent param satisfies this gate."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True, (
        "no display param → result MUST be true (display mismatch not triggered)"
    )
    assert not _has_param(body, "message"), (
        "no display param → no mismatch message SHOULD be emitted"
    )


def test_h21_validate_empty_display_param_returns_true(fhir_client):
    """QA-048 edge — empty display string. The spec does not mandate
    behavior for empty display. The current impl compares via
    `display != canonical_display` — an empty string DOES differ from the
    canonical display, so this would trigger mismatch.

    This is a documented edge: the spec example uses a non-empty wrong
    display. Empty display is best treated as "absent" (no validation
    requested). The current impl triggers mismatch — this probe DOCUMENTS
    the current behavior; it is not necessarily a bug.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM, "display": ""},
    )
    assert r.status_code == 200
    body = r.json()
    # Current impl: empty display != canonical → triggers mismatch.
    # Documented behavior — not asserted as a bug. Probe exists to pin
    # the contract; if the impl changes to treat empty as absent, this
    # probe must be updated.
    result_val = _param_value(body, "result")
    assert result_val is False, (
        "empty display with known code → current impl sets result=false "
        "(empty != canonical display). If this changes, update the probe."
    )


def test_h22_validate_whitespace_display_does_not_match_canonical(fhir_client):
    """QA-048 edge — whitespace-only display. The current impl does NOT
    strip whitespace before comparison; ``" "`` != ``"Type 2 diabetes
    mellitus"`` → mismatch triggered. Documents current behavior."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM, "display": " "},
    )
    assert r.status_code == 200
    body = r.json()
    result_val = _param_value(body, "result")
    # Whitespace != canonical → mismatch triggered (current behavior).
    assert result_val is False


def test_h23_validate_case_sensitive_display_mismatch_snomed(fhir_client):
    """QA-048 case-sensitivity — SNOMED CT is case-sensitive (per FHIR R4
    CodeSystem case-sensitivity codes). A display differing only in case
    MUST trigger mismatch under the current exact-match impl.

    Spec: "Whether displays are case sensitive is code system dependent"
    (https://hl7.org/fhir/R4/codesystem-operation-validate-code.html).
    For v0.0.x, exact match is the safe default; per-source
    case-sensitivity flagging is a documented future enhancement
    (AGENTS.md NOT A BUG registry, CS-03 SKEPTIC audit).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "display": "type 2 diabetes mellitus",  # lowercase
        },
    )
    assert r.status_code == 200
    body = r.json()
    result_val = _param_value(body, "result")
    assert result_val is False, (
        "case-differing display → result MUST be false under exact-match. "
        "Per-source case-sensitivity is a future enhancement."
    )


# ---------------------------------------------------------------------------
# Item 3: CodeableConcept multi-coding correctness (SKEPTIC QA-049)
# ---------------------------------------------------------------------------

def test_h30_codeable_concept_first_match_valid_code_wrong_display_returns_true(fhir_client):
    """QA-049 edge — first coding has valid code but the client supplies a
    wrong `display` parameter at the top level. Per SKEPTIC AUDIT-002
    note: display mismatch is NOT enforced on the codeableConcept path
    (spec does not mandate display enforcement for codeableConcept).

    The codeableConcept path returns result=true if ANY coding matches,
    regardless of the top-level display param.
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
                        ]
                    },
                },
                {"name": "display", "valueString": "WRONG-DISPLAY"},
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    # Per SKEPTIC AUDIT-002: codeableConcept path doesn't enforce display.
    assert _param_value(body, "result") is True, (
        "codeableConcept with at least one valid coding → result MUST be "
        "true regardless of top-level display param (display enforcement "
        "is not mandated for codeableConcept per CS-03 SKEPTIC AUDIT-002)."
    )


def test_h31_codeable_concept_all_codings_invalid_returns_false_with_message(fhir_client):
    """QA-049 negative — all codings invalid → result=false with message
    "None of the codings in the codeableConcept are in the code system."
    (pinned in _do_validate)."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "BOGUS_QA_1"},
                            {"system": SNOMED_URI, "code": "BOGUS_QA_2"},
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is False
    assert _has_param(body, "message"), (
        "all-invalid codeableConcept → message MUST be present explaining "
        "that none of the codings are in the code system"
    )


def test_h32_codeable_concept_helper_silently_skips_coding_missing_system(fhir_client):
    """QA-049 silent-skip audit — the all-pairs helper
    (_extract_all_coding_pairs_from_codeable_concept) silently skips
    codings missing system or code (returns the rest). This is not a
    silent-fallback bug — the helper's contract is to return VALID
    (system, code) pairs. A coding missing system is structurally
    invalid and correctly excluded.

    This probe DOCUMENTS the behavior; if the helper were changed to
    return None on any malformed coding, this probe would fail (and
    that change would be a regression — the spec says "any coding
    matches → true"; dropping all codings because one is malformed
    would be over-strict).
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
                            {"code": "ORPHAN"},  # missing system — skipped
                            {"system": SNOMED_URI, "code": SNOMED_T2DM},  # valid
                        ]
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    # Valid coding present → result=true (malformed coding silently skipped).
    assert _param_value(body, "result") is True


def test_h33_codeable_concept_helper_returns_none_on_fully_malformed_input(fhir_client):
    """QA-049 silent-fallback audit — when ALL codings are malformed
    (missing system or code), the helper returns None. The POST handler
    then falls through to 400 'system and code are required.' — not a
    silent-wrong-answer.

    This is the CORRECT behavior: the spec requires "one of (code+system,
    coding, codeableConcept)"; a codeableConcept with no usable codings
    is structurally equivalent to "no codeableConcept supplied" → 400.
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
                            {"code": "NO_SYSTEM"},  # missing system
                            {"system": SNOMED_URI},  # missing code
                        ]
                    },
                }
            ],
        },
    )
    # Helper returns None → handler emits 400.
    assert r.status_code == 400, (
        "codeableConcept with no valid (system, code) pairs → 400 "
        "'system and code are required' (helper correctly returns None)"
    )


# ---------------------------------------------------------------------------
# Item 4: Documentation-vs-implementation drift (TS-01 HISTORIAN QA-007)
# ---------------------------------------------------------------------------

def test_h40_do_validate_docstring_or_inline_comments_document_display_mismatch():
    """QA-052-L / LOW.

    _do_validate has no function-level docstring; inline comments document
    the codeableConcept semantic (QA-049) and display mismatch (QA-048).
    This is acceptable but inconsistent with _do_lookup which has a
    35-line docstring. LOW severity — documentation gap, not behavioral.
    """
    import inspect
    from medterm4ds.apps.fhir_api import create_fhir_app
    # _do_validate is a closure inside create_fhir_app; introspect via
    # source reading instead.
    src = inspect.getsource(create_fhir_app)
    # Locate _do_validate and check that display mismatch logic has
    # inline comments referencing QA-048.
    do_validate_start = src.find("def _do_validate(")
    assert do_validate_start != -1, "_do_validate must exist"
    # Find the next def at the same indentation to bound the body.
    body = src[do_validate_start:]
    # Look for the QA-048 inline comment (added by SKEPTIC).
    assert "QA-048" in body, (
        "_do_validate MUST reference QA-048 in inline comments documenting "
        "the display mismatch behavior (TS-01 HISTORIAN QA-007 pattern: "
        "documentation-vs-implementation drift)."
    )
    assert "QA-049" in body, (
        "_do_validate MUST reference QA-049 in inline comments documenting "
        "the codeableConcept multi-coding semantic."
    )


def test_h41_extract_all_pairs_helper_docstring_accurate():
    """QA-007 / documentation audit — verify the helper's docstring
    matches its implementation. The docstring claims it returns the
    list of (system, code) pairs from the first codeableConcept with at
    least one valid coding."""
    from medterm4ds.apps.fhir_api import create_fhir_app
    import inspect
    src = inspect.getsource(create_fhir_app)
    helper_start = src.find("def _extract_all_coding_pairs_from_codeable_concept(")
    assert helper_start != -1
    # Extract the function body (up to the next top-level def).
    body = src[helper_start:]
    # Verify the docstring documents the spec quote (allowing markdown bold
    # and newlines within the quoted phrase).
    import re
    # Collapse whitespace so multi-line quotes still match.
    normalized = re.sub(r"\s+", " ", body)
    assert "one of the coding values" in normalized, (
        "helper docstring MUST quote the spec phrase 'one of the coding "
        "values is in the code system' to document the semantic."
    )


# ---------------------------------------------------------------------------
# Item 5: Boolean capitalization (CR-002 pattern) on JSON wire form
# ---------------------------------------------------------------------------

def test_h50_validate_result_valueBoolean_is_native_bool_on_wire(fhir_client):
    """CR-002 / wire-format audit — valueBoolean renders as native JSON
    boolean (``false``), not stringified ``"False"``. Python's
    ``json.dumps(False)`` correctly emits ``false``; this probe pins the
    contract by inspecting the raw response text."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code=BOGUS_QA_H50"
    )
    assert r.status_code == 200
    # The raw response body MUST contain "false" (lowercase), not "False".
    assert '"valueBoolean": false' in r.text or '"valueBoolean":false' in r.text, (
        f"valueBoolean MUST render as native JSON false (lowercase); "
        f"got: {r.text[:300]}"
    )
    assert '"valueBoolean": False' not in r.text and '"valueBoolean":False' not in r.text, (
        "valueBoolean MUST NOT render as Python str(False)='False' (capital F). "
        "Pattern: CR-002 boolean capitalization on serializers."
    )


# ---------------------------------------------------------------------------
# Item 6: Test-too-lenient audit of SKEPTIC test_s41 (CS-01 HISTORIAN pattern)
# ---------------------------------------------------------------------------

def test_h60_skeptic_test_s41_asserts_canonical_display_value(fhir_client):
    """Test-too-lenient audit — SKEPTIC's test_s41 asserts the Out
    `display` is not equal to the wrong input AND contains 'diabetes'
    (case-insensitive). This is a value-content assertion, not just
    presence — a garbage string would fail the 'diabetes' substring
    check. Confirm the impl actually emits the canonical display value.

    This probe re-issues the SKEPTIC reproduction and tightens the
    assertion to the EXACT canonical display string, not just a
    substring. If the impl emits any string containing 'diabetes' but
    NOT the canonical (e.g. a related concept's display), this probe
    fails — catching test-too-lenient drift in the SKEPTIC suite.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&display=WRONG-DISPLAY-NOT-CANONICAL"
    )
    assert r.status_code == 200
    body = r.json()
    display_val = _param_value(body, "display")
    assert display_val is not None
    # Tighter than SKEPTIC test_s41: exact canonical, not just substring.
    assert display_val == "Type 2 diabetes mellitus", (
        f"Out `display` MUST be the exact canonical display "
        f"'Type 2 diabetes mellitus'; got {display_val!r}. SKEPTIC "
        f"test_s41 only asserts substring 'diabetes' — test-too-lenient "
        f"if the impl returned a related concept's display."
    )


def test_h61_skeptic_test_s41_asserts_message_contains_incorrect(fhir_client):
    """Test-too-lenient audit — SKEPTIC's test_s41 asserts the message is
    present and non-empty. Tighten: assert the message contains
    'incorrect' (the spec example's key word) AND the wrong display
    value verbatim. A generic error message would fail this probe."""
    wrong_display = "WRONG-DISPLAY-NOT-CANONICAL"
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&display={wrong_display}"
    )
    assert r.status_code == 200
    body = r.json()
    msg_val = _param_value(body, "message")
    assert msg_val is not None
    assert "incorrect" in msg_val.lower(), (
        f"message MUST contain 'incorrect' (spec example: "
        f"'The display \"X\" is incorrect'); got {msg_val!r}"
    )
    assert wrong_display in msg_val, (
        f"message MUST contain the wrong display value verbatim; "
        f"got {msg_val!r}"
    )


# ---------------------------------------------------------------------------
# Item 7: Batch dispatcher carry-forward (CF-SKEPTIC-CS03-02)
# ---------------------------------------------------------------------------

def test_h70_batch_validate_code_with_codeable_concept_uses_single_pair_helper(fhir_client):
    """QA-052 / MEDIUM.

    CF-SKEPTIC-CS03-02 (the documented carry-forward) was INACCURATE —
    it claimed the batch dispatcher's `_extract_validate_params` does
    NOT extract codeableConcept. HISTORIAN discovery: the helper DOES
    call `_extract_codeable_concept_from_parameters`, BUT it uses the
    SINGLE-PAIR helper, not the all-pairs helper `_extract_all_coding_pairs_from_codeable_concept`.

    This means batch CodeSystem/$validate-code with a codeableConcept
    containing [INVALID, VALID] returns result=false (single-pair picks
    INVALID first), while the per-operation POST route returns result=true
    (all-pairs helper finds the VALID coding).

    This is the cross-handler helper-wiring inconsistency pattern
    (TS-02 EXPLORER QA-028 shape — different semantic at the batch layer
    than at the per-operation layer). Clinical correctness depends on
    which invocation path the client uses — batch clients get a
    silently-wrong answer for the same logical request.

    Spec basis: CodeSystem/$validate-code In `codeableConcept`: "The
    server returns true if one of the coding values is in the code
    system" (https://hl7.org/fhir/R4/codesystem-operation-validate-code.html).
    The batch path MUST honor this semantic identically to the
    per-operation path.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "/CodeSystem/$validate-code",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {
                            "name": "codeableConcept",
                            "valueCodeableConcept": {
                                "coding": [
                                    # INVALID first, VALID second.
                                    # Per-operation path: all-pairs helper
                                    # → result=true. Batch path today:
                                    # single-pair helper → result=false.
                                    {"system": SNOMED_URI, "code": "BOGUS_QA_H70"},
                                    {"system": SNOMED_URI, "code": SNOMED_T2DM},
                                ]
                            },
                        }
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "batch-response"
    assert len(body.get("entry", [])) == 1
    entry = body["entry"][0]
    resp = entry.get("response", {})
    assert resp.get("status") == "200", (
        f"batch entry MUST return 200; got {resp.get('status')}"
    )
    # Parse the entry's Parameters body.
    resource = entry.get("resource", {})
    result_val = None
    for p in resource.get("parameter", []):
        if p.get("name") == "result":
            result_val = p.get("valueBoolean")
            break
    # The spec mandates "any coding matches → true". The batch path
    # uses the single-pair helper today, so it returns false (the first
    # coding is BOGUS). This is the bug.
    assert result_val is True, (
        f"batch CodeSystem/$validate-code with codeableConcept [INVALID, VALID] "
        f"MUST return result=true per spec 'any coding matches'. "
        f"Batch dispatcher uses single-pair helper today → result=false. "
        f"Fix: extend _extract_validate_params to call "
        f"_extract_all_coding_pairs_from_codeable_concept and pass through. "
        f"Pattern: TS-02 EXPLORER QA-028 cross-handler helper-wiring."
    )
