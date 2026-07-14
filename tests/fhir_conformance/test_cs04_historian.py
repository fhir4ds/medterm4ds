"""HISTORIAN probes for CS-04 (CodeSystem $subsumes Operation).

Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
       (build: https://build.fhir.org/codesystem-operation-subsumes.html)

HISTORIAN lens: pattern-match against v0.0.1 + cross-chunk patterns found
by prior HISTORIAN iterations. Source patterns:

  - TS-04 HISTORIAN QA-038 — "alternative-failure-path at error-isolation
    boundary": probe what happens when an exception fires INSIDE the
    dispatched operation (not in pre-dispatch validation). For $subsumes,
    the concern is `_do_subsumes` calling `is_descendant` which queries
    DuckDB; a transient `duckdb.Error` would propagate past the GET/POST
    handlers (no `try/except` wraps `_run_db`) to Starlette's default 500
    with `text/plain` body. Audited by code inspection + documented as a
    systemic pattern (applies to every `_do_*` handler, not just
    `_do_subsumes`) — NOT a CS-04-specific bug.

  - TS-02 HISTORIAN QA-022/QA-023 — "alternative-encoding silent-reject":
    already fixed by SKEPTIC QA-053 (codingA/codingB). HISTORIAN verifies
    the new `_extract_named_coding_from_parameters` helper handles
    MALFORMED valueCoding gracefully (missing system, missing code,
    wrong type, empty dict).

  - TS-03 HISTORIAN QA-034 — "test-too-lenient (negative-only assertion)":
    SKEPTIC's mixed-system probe (test_s80) only asserts the status code
    and resourceType=OperationOutcome. A future regression that produced
    a generic error message ("cross-system error") would ship under green
    CI. HISTORIAN tightens: assert the diagnostics string NAMES the
    conflicting systems AND the offending parameter (codingA vs codingB).

  - CS-01 HISTORIAN QA-044 — "silent-fallback at helper-call-site": the
    new `_extract_named_coding_from_parameters` returns None for malformed
    valueCoding, which the caller treats as "no coding supplied". This is
    correct behavior (malformed codings should fall through to the
    missing-scalar check), NOT a silent-fallback bug — verified by probes
    h20-h23.

  - CS-02 HISTORIAN QA-047 + CS-03 HISTORIAN QA-051 — "client-input-as-
    canonical drift": $subsumes does NOT emit an Out `system` parameter,
    so the canonical-re-resolution pattern does not apply. Verified by
    probe h60 (no Out `system` in the response).

  - CS-03 HISTORIAN QA-052 — "carry-forward notes about HTTP behavior MUST
    be verified by a probe": HISTORIAN verifies the SKEPTIC QA-053
    carry-forward claim that the mixed-system check "fires AFTER the
    missing-scalar check" by constructing a probe that distinguishes the
    two error messages (h50).

  - TS-01 HISTORIAN QA-007 — "documentation-vs-implementation drift":
    `_extract_named_coding_from_parameters` docstring claims it "Returns
    (system, code) if a coding with both fields is present under the
    named parameter, else None." Verified by probes h20-h23 (malformed
    codings all return None correctly).

Scope: 9 spec items (see SKEPTIC test file). HISTORIAN probes the
FAILURE-PATH and EDGE-CASE surface, complementing SKEPTIC's happy-path
coverage.

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts POSITIVE
success shape (200 + expected fields) OR a specific error message
content, not just the absence of one error string.
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
#
# Out `outcome` MUST be one of these 4 strings (closed enum,
# ConceptSubsumptionOutcome value set).
VALID_OUTCOMES = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child (descendant of 73211009)
SNOMED_VIRAL_HEPATITIS = "3738000"     # unrelated to diabetes branch
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"            # unrelated to diabetes branch
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"


def _outcome(body: dict) -> str | None:
    """Return the value of the Out `outcome` parameter."""
    for p in body.get("parameter", []):
        if p.get("name") == "outcome":
            if "valueCode" in p:
                return p["valueCode"]
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _diagnostics(body: dict) -> str:
    """Extract the diagnostics string from an OperationOutcome."""
    for issue in body.get("issue", []):
        if "diagnostics" in issue:
            return issue["diagnostics"]
    return ""


# ---------------------------------------------------------------------------
# Lens 1: Malformed valueCoding — the new _extract_named_coding_from_parameters
# helper MUST handle every malformed shape gracefully (return None, fall
# through to missing-scalar check). Per TS-02 HISTORIAN QA-022 + CS-01
# HISTORIAN QA-044.
# ---------------------------------------------------------------------------

def test_h20_coding_missing_system_falls_through_to_missing_scalar(fhir_client):
    """HISTORIAN lens: valueCoding with `code` but no `system`.

    Per spec In `codingA` 0..1 Coding, a Coding without system is
    malformed. The helper `_extract_named_coding_from_parameters` MUST
    return None (it requires both system AND code) so the caller falls
    through to the missing-scalar check. Without this guard, a KeyError
    or AttributeError on `coding.get("system")` could propagate.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    # Malformed codingA → no codeA extracted → missing-scalar check fires.
    assert r.status_code == 400, (
        f"malformed valueCoding (missing system): {r.status_code} {r.text[:300]}"
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "OperationOutcome"
    # The error message MUST be the missing-scalar message (proves the
    # helper returned None correctly rather than raising).
    diag = _diagnostics(body_json)
    assert "required" in diag.lower(), (
        f"expected missing-scalar message; got: {diag!r}"
    )


def test_h21_coding_missing_code_falls_through_to_missing_scalar(fhir_client):
    """HISTORIAN lens: valueCoding with `system` but no `code`.

    Symmetric to test_h20. The helper MUST require both fields.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 400
    diag = _diagnostics(r.json())
    assert "required" in diag.lower()


def test_h22_coding_wrong_type_falls_through(fhir_client):
    """HISTORIAN lens: valueCoding as a STRING (wrong type).

    Per the helper's docstring: "If the coding is not a dict, the helper
    skips it." Without the `isinstance(coding, dict)` guard, a
    `coding.get("system")` on a string would raise `AttributeError`.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": "not-a-coding"},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 400, (
        f"wrong-type valueCoding: {r.status_code} {r.text[:300]}"
    )
    diag = _diagnostics(r.json())
    assert "required" in diag.lower()


def test_h23_coding_empty_dict_falls_through(fhir_client):
    """HISTORIAN lens: valueCoding as an EMPTY dict.

    `{}.get("system")` returns None, `{}.get("code")` returns None, so
    `if system and code` is False and the helper returns None. Verify.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 400
    diag = _diagnostics(r.json())
    assert "required" in diag.lower()


def test_h24_coding_with_extra_fields_accepted(fhir_client):
    """HISTORIAN lens: valueCoding with `display` and `version` extra fields.

    A valid Coding has system+code; extras like `display`, `version`,
    `userSelected` are spec-permitted and MUST NOT cause the helper to
    reject the coding. The helper should extract (system, code) cleanly.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {
                "name": "codingA",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DIABETES_MELLITUS,
                    "display": "Diabetes mellitus",
                    "version": "2024",
                    "userSelected": True,
                },
            },
            {
                "name": "codingB",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM},
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    assert _outcome(r.json()) == "subsumes"


# ---------------------------------------------------------------------------
# Lens 2: Mixed-system check — verify the error message CONTENT, not just
# the status code. Per TS-03 HISTORIAN QA-034 "test-too-lenient" pattern.
# SKEPTIC's test_s80 only asserts resourceType=OperationOutcome; this tightens.
# ---------------------------------------------------------------------------

def test_h50_mixed_system_codingB_names_both_systems(fhir_client):
    """HISTORIAN lens (TS-03 HISTORIAN QA-034 pattern): SKEPTIC's test_s80
    only asserts resourceType. A future regression that produced a generic
    "cross-system error" message would ship under green CI. HISTORIAN
    tightens: the diagnostics MUST name BOTH the offending system AND the
    expected system, AND identify which coding (A vs B) is the offender.

    Spec basis (https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    In `codingA`): "The code system does not have to match the specified
    subsumption code system, but the relationships between the code
    systems must be well established". A diagnostic that names the
    systems is actionable for the client (they can correct the mismatch).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            {"name": "codingB", "valueCoding": {"system": RXNORM_URI, "code": RXNORM_METFORMIN}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 400
    body_json = r.json()
    assert body_json.get("resourceType") == "OperationOutcome"
    diag = _diagnostics(body_json)
    # The diagnostic MUST name the offending parameter (codingB).
    assert "codingB" in diag, (
        f"diagnostics should name codingB as the offender; got: {diag!r}"
    )
    # The diagnostic MUST name BOTH systems (client can't fix what they
    # can't see). Both the offending system and the expected system.
    assert RXNORM_URI in diag, (
        f"diagnostics should name rxnorm URI; got: {diag!r}"
    )
    assert SNOMED_URI in diag, (
        f"diagnostics should name snomed URI (the expected); got: {diag!r}"
    )


def test_h51_mixed_system_codingA_names_both_systems(fhir_client):
    """HISTORIAN lens: mirror of test_h50 — codingA is the offender.

    The error path MUST correctly attribute the error to codingA (not
    codingB). A swapped attribution would silently mislead the client.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": RXNORM_URI, "code": RXNORM_METFORMIN}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 400
    diag = _diagnostics(r.json())
    assert "codingA" in diag, (
        f"diagnostics should name codingA; got: {diag!r}"
    )
    assert RXNORM_URI in diag
    assert SNOMED_URI in diag


def test_h52_missing_scalar_check_fires_before_mixed_system(fhir_client):
    """HISTORIAN lens (CS-03 HISTORIAN QA-052 methodology — verify the
    carry-forward): SKEPTIC's handoff claims the mixed-system check fires
    AFTER the missing-scalar check. Verify by constructing a probe that
    distinguishes the two error messages.

    If only codingA is supplied (no codingB, no codeA/codeB scalar), the
    missing-scalar check fires FIRST with "system, codeA, and codeB are
    required." If the mixed-system check fired first, the message would
    be about cross-system relationships.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            # codingA with a DIFFERENT system (would trigger mixed-system
            # check if it fired first), but no codeB supplied at all.
            {"name": "codingA", "valueCoding": {"system": RXNORM_URI, "code": RXNORM_METFORMIN}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 400
    diag = _diagnostics(r.json())
    # The missing-scalar message fires first; the mixed-system message
    # is NOT in the response.
    assert "required" in diag.lower(), (
        f"missing-scalar should fire first; got: {diag!r}"
    )
    assert "differs from subsumption" not in diag, (
        f"mixed-system check should NOT have fired; got: {diag!r}"
    )


# ---------------------------------------------------------------------------
# Lens 3: Outcome vocabulary re-audit — no leaked internal vocabulary.
# (Complements SKEPTIC test_s50; HISTORIAN adds the alias/URN angle.)
# ---------------------------------------------------------------------------

def test_h60_outcome_never_leaks_internal_vocab_on_all_paths(fhir_client):
    """HISTORIAN lens (TS-02 TERMINOLOGIST QA-030 + CS-01 SKEPTIC QA-043
    pattern class): verify the outcome closed enum holds across EVERY
    outcome path. SKEPTIC test_s50 covers equivalent/subsumes/subsumed-by/
    not-subsumed via SNOMED codes; HISTORIAN adds the unknown-codes path
    and the cross-source-code path.

    The closed enum per spec Out `outcome`:
      {equivalent, subsumes, subsumed-by, not-subsumed}
    (ConceptSubsumptionOutcome value set).
    """
    forbidden = {"broader", "narrower", "parent", "child", "ancestor",
                 "descendant", "relatedto", "same", "equivalent-to",
                 "subsumedBy", "not-subsumed-by"}
    test_vectors = [
        # (system, codeA, codeB, description)
        (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM, "equivalent"),
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_T2DM, "subsumes"),
        (SNOMED_URI, SNOMED_T2DM, SNOMED_DIABETES_MELLITUS, "subsumed-by"),
        (SNOMED_URI, SNOMED_T2DM, SNOMED_VIRAL_HEPATITIS, "not-subsumed"),
        (SNOMED_URI, "UNKNOWN_A", "UNKNOWN_B", "unknown-not-subsumed"),
        (SNOMED_URI, SNOMED_T2DM, "E11", "cross-source-not-subsumed"),
    ]
    for system, code_a, code_b, desc in test_vectors:
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={system}"
            f"&codeA={code_a}&codeB={code_b}"
        )
        assert r.status_code == 200, f"{desc}: {r.status_code} {r.text[:200]}"
        outcome = _outcome(r.json())
        assert outcome in VALID_OUTCOMES, (
            f"{desc}: outcome={outcome!r} not in closed enum {VALID_OUTCOMES}"
        )
        assert outcome not in forbidden, (
            f"{desc}: outcome={outcome!r} leaked internal vocabulary"
        )


def test_h61_outcome_value_type_is_valueCode_on_xml_path(fhir_client):
    """HISTORIAN lens (CR-002 boolean capitalization + CR-001 conformance
    per route): the XML serializer MUST render the outcome valueCode with
    lowercase FHIR R4 wire form. Probe the XML path on $subsumes (the
    first operation route to be XML-probed for valueCode rendering).

    Per spec Out `outcome` type=code, the value MUST be a `code` type
    (wire form: valueCode attribute on the XML element).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}&_format=xml"
    )
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+xml" in ct, f"Content-Type={ct!r}"
    body_text = r.text
    # The XML MUST contain a valueCode element with the lowercase enum value.
    assert 'valueCode value="equivalent"' in body_text, (
        f"expected valueCode value=\"equivalent\"; got: {body_text[:300]}"
    )
    # The XML MUST NOT contain a capitalized variant (would indicate the
    # str()-based serializer bug from CR-002).
    assert 'valueCode value="Equivalent"' not in body_text
    assert 'valueCode value="EQUIVALENT"' not in body_text


# ---------------------------------------------------------------------------
# Lens 4: Canonical-system echo — $subsumes does NOT emit Out `system`.
# This is the 5th instance of client-input-as-canonical drift (QA-047/QA-051)
# — verify it does NOT apply here.
# ---------------------------------------------------------------------------

def test_h70_no_out_system_parameter_in_response(fhir_client):
    """HISTORIAN lens (CS-02 HISTORIAN QA-047 + CS-03 HISTORIAN QA-051):
    `_do_lookup` and `_do_validate` echo an Out `system` parameter (which
    triggered the canonical-echo drift pattern). `_do_subsumes` does NOT
    emit Out `system` — the Out parameter is `outcome` only. Verify no
    Out `system` leaks, so the canonical-echo pattern does not apply.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    names = {p.get("name") for p in r.json().get("parameter", [])}
    assert "system" not in names, (
        f"Out `system` should not be emitted; got names={names}"
    )
    assert "outcome" in names


def test_h71_system_alias_resolves_correctly(fhir_client):
    """HISTORIAN lens: when the client passes an alias system URI
    (urn:oid:...), the engine MUST still resolve the system correctly.
    Per FHIR_URI_ALIASES (TS-01 TERMINOLOGIST QA-012 fix), aliases
    resolve to the canonical source. $subsumes doesn't echo the system,
    but a failure to resolve would cause a 400 "Unrecognized system URI".
    """
    # urn:oid:2.16.840.1.113883.6.96 is the SNOMED CT OID alias.
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system=urn:oid:2.16.840.1.113883.6.96"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_DIABETES_MELLITUS}"
    )
    assert r.status_code == 200, (
        f"urn:oid alias should resolve; got {r.status_code} {r.text[:300]}"
    )
    assert _outcome(r.json()) == "subsumed-by"


def test_h72_trailing_slash_system_resolves(fhir_client):
    """HISTORIAN lens: trailing-slash system URI should resolve the same
    as the canonical URI. Per CS-02 HISTORIAN QA-047, the canonical
    re-resolution handles trailing slashes for $lookup Out `system`;
    for $subsumes In `system`, the fhir_uri_to_system map handles it.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}/"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_DIABETES_MELLITUS}"
    )
    assert r.status_code == 200, (
        f"trailing-slash system should resolve; got {r.status_code} {r.text[:300]}"
    )
    assert _outcome(r.json()) == "subsumed-by"


# ---------------------------------------------------------------------------
# Lens 5: Mixed scalar + coding inputs — spec doesn't forbid supplying
# both; verify no silent precedence inversion.
# ---------------------------------------------------------------------------

def test_h80_scalar_codeA_with_codingB_accepted(fhir_client):
    """HISTORIAN lens: a client supplies scalar codeA AND codingB (mixed).
    The implementation extracts codeB from codingB; both are required, so
    mixed encoding should produce the correct outcome. No spec text
    forbids mixed encoding; the helper-based fallback design supports it.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    assert _outcome(r.json()) == "subsumes"


def test_h81_codingA_with_scalar_codeB_accepted(fhir_client):
    """HISTORIAN lens: mirror of test_h80 — codingA + scalar codeB."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200
    assert _outcome(r.json()) == "subsumes"


def test_h82_scalar_codeA_overrides_when_codingA_also_present(fhir_client):
    """HISTORIAN lens: when BOTH scalar codeA AND codingA are supplied,
    the implementation uses the scalar (`if not code_a and coding_a_pair
    is not None`). The spec doesn't define precedence; verify the
    implementation's documented precedence (scalar wins).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            # Scalar codeA = 44054006 (child); codingA = 73211009 (parent).
            # If scalar wins, outcome = subsumed-by (44054006 subsumed by 73211009... wait, codeB=44054006).
            # Let me use distinct values: scalar codeA=73211009, codingA=44054006, codeB=44054006.
            # If scalar wins: codeA=73211009, codeB=44054006 → subsumes.
            # If codingA wins: codeA=44054006, codeB=44054006 → equivalent.
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200
    outcome = _outcome(r.json())
    # Scalar codeA wins per the implementation (`if not code_a`).
    assert outcome == "subsumes", (
        f"scalar codeA should win; outcome={outcome!r} "
        f"(expected 'subsumes' for parent→child, not 'equivalent' for codingA==codeB)"
    )


# ---------------------------------------------------------------------------
# Lens 6: Error-path Content-Type — the mixed-system and missing-scalar
# error responses MUST be application/fhir+json (not application/json).
# Per CR-001 conformance property per route.
# ---------------------------------------------------------------------------

def test_h90_missing_scalar_error_content_type_is_fhir_json(fhir_client):
    """HISTORIAN lens (CR-001): the 400 error path MUST emit
    application/fhir+json Content-Type, not Starlette's default
    application/json.
    """
    body = {"resourceType": "Parameters", "parameter": []}
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code in (400, 422)
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"error Content-Type={ct!r}; expected application/fhir+json"
    )


def test_h91_mixed_system_error_content_type_is_fhir_json(fhir_client):
    """HISTORIAN lens (CR-001): the mixed-system error path Content-Type."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            {"name": "codingB", "valueCoding": {"system": RXNORM_URI, "code": RXNORM_METFORMIN}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 400
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct


def test_h92_unknown_system_error_content_type_is_fhir_json(fhir_client):
    """HISTORIAN lens (CR-001): the unknown-system error path Content-Type."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system=http://fake.example/sys"
        f"&codeA=1&codeB=2"
    )
    assert r.status_code == 400
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct


# ---------------------------------------------------------------------------
# Lens 7: Outcome directionality — verify the spec mirror invariant.
# subsumes and subsumed-by are mirror images; swapping A/B MUST swap the
# outcome. (Complements SKEPTIC test_s20/test_s30.)
# ---------------------------------------------------------------------------

def test_h100_subsumes_subsumed_by_are_mirrors(fhir_client):
    """HISTORIAN lens: A subsumes B ↔ B subsumed-by A. The
    implementation calls `is_descendant(a, b)` for subsumes and
    `is_descendant(b, a)` for subsumed-by. A swapped-direction bug
    would silently wrong-answer (subsumes ↔ subsumed-by inverted).
    """
    # A=parent (73211009), B=child (44054006): A subsumes B.
    r_ab = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    # A=child (44054006), B=parent (73211009): A subsumed-by B.
    r_ba = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_DIABETES_MELLITUS}"
    )
    assert r_ab.status_code == r_ba.status_code == 200
    outcome_ab = _outcome(r_ab.json())
    outcome_ba = _outcome(r_ba.json())
    assert outcome_ab == "subsumes", f"A=parent,B=child: outcome={outcome_ab!r}"
    assert outcome_ba == "subsumed-by", f"A=child,B=parent: outcome={outcome_ba!r}"


def test_h101_unrelated_codes_symmetric_not_subsumed(fhir_client):
    """HISTORIAN lens: not-subsumed is symmetric — A vs B and B vs A both
    return not-subsumed when there's no relationship.
    """
    r_ab = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_VIRAL_HEPATITIS}"
    )
    r_ba = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_VIRAL_HEPATITIS}&codeB={SNOMED_T2DM}"
    )
    assert _outcome(r_ab.json()) == "not-subsumed"
    assert _outcome(r_ba.json()) == "not-subsumed"


# ---------------------------------------------------------------------------
# Lens 8: POST parity — POST with codingA/codingB MUST produce the same
# outcome as GET with system+codeA+codeB for the same logical codes.
# (Complements SKEPTIC test_s90/s91.)
# ---------------------------------------------------------------------------

def test_h110_get_post_parity_for_codingA_codingB(fhir_client):
    """HISTORIAN lens (TS-04 TERMINOLOGIST single-vs-batch equivalence
    probe class): GET system+codeA+codeB MUST produce the same outcome as
    POST codingA+codingB+system. The POST handler extracts code/code from
    valueCoding; clinical content MUST be identical.
    """
    get_r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=post_body)
    assert get_r.status_code == post_r.status_code == 200
    assert _outcome(get_r.json()) == _outcome(post_r.json()) == "subsumes"
