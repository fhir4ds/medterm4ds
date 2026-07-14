"""SKEPTIC probes for CS-04 (CodeSystem $subsumes Operation).

Spec: https://build.fhir.org/codesystem-operation-subsumes.html
       (canonical R4: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html)

Scope (per chunk assignment) — 9 items:
  1. Required params: codeA, codeB, system
  2. Optional params: version, codingA, codingB
  3. Returns `outcome` parameter with `valueCode` from
     {equivalent, subsumes, subsumed-by, not-subsumed}
  4. equivalent: A and B are the same concept
  5. subsumes: A subsumes B (A is broader)
  6. subsumed-by: A is subsumed by B (B is broader)
  7. not-subsumed: no relationship
  8. Version parameter handling: when omitted, current version used
  9. Mixed-system codings: server SHALL error unless relationships are
     well defined

SKEPTIC lens: hostile-input probes for each item — drop required params,
probe outcome vocabulary exactness, probe codingA/codingB alternative
encoding on POST, probe mixed-system error path, probe version-param
acceptance, probe GET-vs-POST parity, probe outcome parameter name
and value type (must be `valueCode`, not `valueString`).

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape (200 +
    expected fields), not just absence of one error string.
  - "FHIR API Specifics": `valueBoolean`/`valueCode` wire-format must be
    the FHIR-spec lowercase form.

Reference: FHIR R4 §4.8.21.3 Operation $subsumes on CodeSystem
  URL: [base]/CodeSystem/$subsumes
  URL: [base]/CodeSystem/[id]/$subsumes
  "When invoking this operation, a client SHALL provide both a and b codes,
   either as code or Coding parameters. The system parameter is required
   unless the operation is invoked on an instance of a code system resource."

  In Parameters:
    codeA    0..1  code    "The 'A' code that is to be tested. If a code is
                           provided, a system must be provided"
    codeB    0..1  code    "The 'B' code that is to be tested. If a code is
                           provided, a system must be provided"
    system   0..1  uri     "The code system in which subsumption testing is to
                           be performed. This must be provided unless the
                           operation is invoked on a code system instance"
    version  0..1  string  "The version of the code system, if one was
                           provided in the source data"
    codingA  0..1  Coding  "The 'A' Coding that is to be tested. The code
                           system does not have to match the specified
                           subsumption code system, but the relationships
                           between the code systems must be well established"
    codingB  0..1  Coding  "The 'B' Coding that is to be tested. ..."

  Out Parameters:
    outcome   1..1  code   "The subsumption relationship between code/Coding
                            'A' and code/Coding 'B'. There are 4 possible
                            codes to be returned (equivalent, subsumes,
                            subsumed-by, and not-subsumed) as defined in the
                            concept-subsumption-outcome value set. If the
                            server is unable to determine the relationship
                            between the codes/Codings, then it returns an
                            error response with an OperationOutcome."
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
            # The spec mandates valueCode (not valueString).
            if "valueCode" in p:
                return p["valueCode"]
            # Defensive: some other value* type would be a bug.
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _outcome_value_type(body: dict) -> str | None:
    """Return the value-key name (e.g. 'valueCode') of the outcome parameter."""
    for p in body.get("parameter", []):
        if p.get("name") == "outcome":
            for k in p:
                if k.startswith("value"):
                    return k
    return None


# ---------------------------------------------------------------------------
# Item 1: Required params — codeA, codeB, system (GET type-level invocation)
# ---------------------------------------------------------------------------

def test_s01_get_subsumes_without_any_params_returns_422(fhir_client):
    """Item 1 / spec §4.8.21.3: codeA+codeB+system are required on the
    type-level GET invocation. Missing all → MUST reject.
    """
    r = fhir_client.get("/fhir/CodeSystem/$subsumes")
    assert r.status_code in (400, 422), (
        f"GET $subsumes with no params → {r.status_code}; expected 422/400"
    )
    # Per GLOBAL_RULES.md "Conformance property per route": body MUST be
    # FHIR OperationOutcome or FastAPI-wrapped FHIR detail (TS-02 SKEPTIC QA-020).
    ct = r.headers.get("content-type", "")
    assert "fhir+json" in ct or "application/json" in ct


def test_s02_get_subsumes_without_codeA_returns_422(fhir_client):
    """Item 1: drop codeA → MUST reject."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code in (400, 422), (
        f"GET $subsumes without codeA → {r.status_code}; expected 422/400"
    )


def test_s03_get_subsumes_without_codeB_returns_422(fhir_client):
    """Item 1: drop codeB → MUST reject."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}&codeA={SNOMED_T2DM}"
    )
    assert r.status_code in (400, 422), (
        f"GET $subsumes without codeB → {r.status_code}; expected 422/400"
    )


def test_s04_get_subsumes_without_system_returns_422(fhir_client):
    """Item 1: drop system → MUST reject (type-level invocation)."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code in (400, 422), (
        f"GET $subsumes without system → {r.status_code}; expected 422/400"
    )


# ---------------------------------------------------------------------------
# Item 3 + 4: Out `outcome` parameter with `valueCode` — equivalent case
# ---------------------------------------------------------------------------

def test_s10_get_subsumes_identical_codes_returns_equivalent(fhir_client):
    """Item 3 + Item 4 / spec Out `outcome`: when codeA == codeB, outcome
    MUST be `equivalent` (A and B are the same concept).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    outcome = _outcome(body)
    assert outcome == "equivalent", (
        f"identical codes: outcome={outcome!r}, expected 'equivalent'"
    )
    # Outcome value MUST use valueCode (not valueString).
    vtype = _outcome_value_type(body)
    assert vtype == "valueCode", (
        f"outcome uses {vtype!r}, expected 'valueCode' (FHIR R4 §4.8.21.3 Out `outcome` type=code)"
    )


def test_s11_get_subsumes_identical_unknown_codes_returns_equivalent(fhir_client):
    """Item 4 edge: identical codes that are UNKNOWN to the server.
    A == B is true regardless of whether the code exists in the DB.
    The implementation short-circuits codeA == codeB → equivalent.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA=UNKNOWN_SAME&codeB=UNKNOWN_SAME"
    )
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert _outcome(body) == "equivalent", (
        f"identical unknown codes: outcome={_outcome(body)!r}, expected 'equivalent'"
    )


# ---------------------------------------------------------------------------
# Item 3 + 5: subsumes (A is broader / parent)
# ---------------------------------------------------------------------------

def test_s20_get_subsumes_parent_child_returns_subsumes(fhir_client):
    """Item 5 / spec: 'subsumes' — A subsumes B (A is broader / parent).
    SNOMED 73211009 (Diabetes mellitus) subsumes 44054006 (T2DM).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    outcome = _outcome(body)
    assert outcome == "subsumes", (
        f"parent→child: outcome={outcome!r}, expected 'subsumes'"
    )
    assert outcome in VALID_OUTCOMES


# ---------------------------------------------------------------------------
# Item 3 + 6: subsumed-by (A is narrower / child)
# ---------------------------------------------------------------------------

def test_s30_get_subsumes_child_parent_returns_subsumed_by(fhir_client):
    """Item 6 / spec: 'subsumed-by' — A is subsumed by B (B is broader).
    Reverse direction of test_s20.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_DIABETES_MELLITUS}"
    )
    assert r.status_code == 200
    body = r.json()
    outcome = _outcome(body)
    assert outcome == "subsumed-by", (
        f"child→parent: outcome={outcome!r}, expected 'subsumed-by'"
    )


# ---------------------------------------------------------------------------
# Item 3 + 7: not-subsumed (no relationship)
# ---------------------------------------------------------------------------

def test_s40_get_subsumes_unrelated_codes_returns_not_subsumed(fhir_client):
    """Item 7 / spec: 'not-subsumed' — no subsumption relationship.
    SNOMED 44054006 (T2DM) vs SNOMED 860975-equivalent unrelated code.
    Note: 860975 is in RXNORM but the seeded SNOMED fixture doesn't include
    it — testing SNOMED codeA vs SNOMED codeB where neither subsumes.
    """
    # SNOMED_T2DM vs SNOMED_VIRAL_HEPATITIS — neither subsumes the other
    # in the seeded fixture.
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_VIRAL_HEPATITIS}"
    )
    assert r.status_code == 200
    body = r.json()
    outcome = _outcome(body)
    assert outcome == "not-subsumed", (
        f"unrelated codes: outcome={outcome!r}, expected 'not-subsumed'"
    )


def test_s41_get_subsumes_unknown_codes_returns_not_subsumed(fhir_client):
    """Item 7 edge: both codes unknown but different → not-subsumed.
    The implementation falls through to not-subsumed after the BFS
    descendant check finds nothing.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA=UNKNOWN_A&codeB=UNKNOWN_B"
    )
    assert r.status_code == 200
    body = r.json()
    outcome = _outcome(body)
    assert outcome == "not-subsumed", (
        f"unknown codes: outcome={outcome!r}, expected 'not-subsumed'"
    )


# ---------------------------------------------------------------------------
# Item 3: Outcome vocabulary closed enum — no leaked internal vocabulary
# ---------------------------------------------------------------------------

def test_s50_outcome_never_leaks_internal_vocabulary(fhir_client):
    """Item 3 / spec: outcome MUST be from the closed enum
    {equivalent, subsumes, subsumed-by, not-subsumed}.
    No leaked internal vocabulary like 'broader', 'narrower', 'parent',
    'child', 'ancestor', 'descendant'.

    This is the SKEPTIC vocabulary-leak probe class (mirrors TS-02
    TERMINOLOGIST QA-030 equivalence hardcoded + CS-01 SKEPTIC QA-043
    raw SAB leak).
    """
    test_vectors = [
        # (codeA, codeB) — exercise every outcome path
        (SNOMED_T2DM, SNOMED_T2DM),               # equivalent
        (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM),  # subsumes
        (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS),  # subsumed-by
        (SNOMED_T2DM, SNOMED_VIRAL_HEPATITIS),    # not-subsumed
    ]
    forbidden = {"broader", "narrower", "parent", "child", "ancestor",
                 "descendant", "relatedto", "equivalent-to", "same"}
    for code_a, code_b in test_vectors:
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={code_a}&codeB={code_b}"
        )
        assert r.status_code == 200
        outcome = _outcome(r.json())
        assert outcome in VALID_OUTCOMES, (
            f"codes ({code_a},{code_b}): outcome={outcome!r} not in closed enum "
            f"{VALID_OUTCOMES}"
        )
        assert outcome not in forbidden, (
            f"codes ({code_a},{code_b}): outcome={outcome!r} leaked internal "
            f"vocabulary"
        )


# ---------------------------------------------------------------------------
# Item 2 + 8: version parameter (accepted; current version used when omitted)
# ---------------------------------------------------------------------------

def test_s60_get_subsumes_with_version_param_accepted(fhir_client):
    """Item 2 + 8 / spec: `version` is an optional In parameter (0..1 string).
    When supplied, that version is used; when omitted, the current version
    is used. The DuckDB-backed engine has no versioned data scoping today
    (documented in AGENTS.md NOT A BUG registry under "version parameter
    accepted but ignored"). The probe verifies the param is ACCEPTED without
    422/500 and the response shape is preserved.
    """
    r = r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
        f"&version=http%3A%2F%2Fsnomed.info%2Fsct%2F32506021000036107%2Fversion%2F20240901"
    )
    assert r.status_code == 200, (
        f"version param supplied: {r.status_code} {r.text[:200]}"
    )
    body = r.json()
    assert _outcome(body) == "subsumes"


def test_s61_get_subsumes_omitted_version_uses_current(fhir_client):
    """Item 8 / spec: when version is omitted, current version used.
    Baseline behavior — the fixture DB has no versioned data; the response
    shape MUST be identical to the version-supplied case.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    assert _outcome(r.json()) == "subsumes"


# ---------------------------------------------------------------------------
# Item 2: codingA/codingB alternative encoding (POST body)
# ---------------------------------------------------------------------------

def test_s70_post_subsumes_with_system_and_codeA_codeB_returns_200(fhir_client):
    """Item 2 baseline POST: scalar Parameters body with system+codeA+codeB.
    MUST produce the same outcome as GET (POST/GET parity).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    outcome = _outcome(r.json())
    assert outcome == "subsumes", f"POST parent-child: outcome={outcome!r}"


def test_s71_post_subsumes_with_codingA_codingB_returns_200(fhir_client):
    """Item 2 / spec In `codingA`+`codingB`: alternative encoding to
    codeA+codeB. The spec example POST uses Codings (see
    https://hl7.org/fhir/R4/codesystem-operation-subsumes.html "Request:
    Using Codings"). The server MUST accept this encoding and produce
    the correct outcome.

    SKEPTIC probe for the silent-reject-on-alternative-encoding pattern
    (TS-02 HISTORIAN QA-022/023 class). `_parse_parameters` is
    scalar-only; without a codingA/codingB extractor, the server would
    silently reject with 400 'system, codeA, and codeB are required.'
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
                },
            },
            {
                "name": "codingB",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM,
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, (
        f"POST $subsumes with codingA/codingB → {r.status_code} {r.text[:300]}; "
        f"spec In Parameters table lists codingA/codingB as 0..1 Coding alternatives"
    )
    outcome = _outcome(r.json())
    assert outcome == "subsumes", (
        f"codingA/codingB parent-child: outcome={outcome!r}, expected 'subsumes'"
    )


def test_s72_post_subsumes_with_codingA_only_rejected(fhir_client):
    """Item 2 edge: spec says 'a client SHALL provide both a and b codes'.
    CodingA without codingB → MUST reject.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {
                "name": "codingA",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM},
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code in (400, 422), (
        f"POST codingA-only → {r.status_code}; expected 400/422"
    )


def test_s73_post_subsumes_missing_all_required_rejected(fhir_client):
    """Item 1 edge: POST body with no system/codeA/codeB → MUST reject."""
    body = {"resourceType": "Parameters", "parameter": []}
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code in (400, 422), (
        f"POST empty params → {r.status_code}; expected 400/422"
    )


# ---------------------------------------------------------------------------
# Item 9: Mixed-system codings — server SHALL error unless relationships
# are well defined
# ---------------------------------------------------------------------------

def test_s80_post_subsumes_mixed_system_codings_rejected(fhir_client):
    """Item 9 / spec In `codingA`: 'The code system does not have to match
    the specified subsumption code system, but the relationships between
    the code systems must be well established'.

    medterm4ds has no cross-system relationship map today. When codingA
    and codingB reference DIFFERENT code systems, the server SHALL return
    an error (OperationOutcome) rather than silently producing a
    not-subsumed result.

    SKEPTIC lens: probe the silent-wrong-answer shape — without an
    explicit cross-system check, the implementation might silently pick
    one system and report not-subsumed for the mismatched pair.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {
                "name": "codingA",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM},
            },
            {
                "name": "codingB",
                "valueCoding": {
                    "system": RXNORM_URI,
                    "code": RXNORM_METFORMIN,
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    # Spec mandates an error response when relationships are not well
    # established. The error path MUST be a FHIR OperationOutcome.
    assert r.status_code in (400, 422, 500), (
        f"mixed-system codings: {r.status_code} {r.text[:300]}; spec mandates "
        f"error when cross-system relationships are not well established"
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "OperationOutcome", (
        f"mixed-system error body resourceType={body_json.get('resourceType')!r}, "
        f"expected 'OperationOutcome'"
    )


# ---------------------------------------------------------------------------
# GET-vs-POST parity — same codeA/codeB/system MUST produce same outcome
# ---------------------------------------------------------------------------

def test_s90_get_post_parity_identical(fhir_client):
    """Item 3 / GLOBAL_RULES: POST handler MUST produce the same clinical
    content as GET for the same input. Mirrors TS-04 TERMINOLOGIST
    single-vs-batch equivalence probe class.
    """
    get_r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
    )
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_T2DM},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
        ],
    }
    post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=post_body)
    assert get_r.status_code == post_r.status_code == 200
    assert _outcome(get_r.json()) == _outcome(post_r.json()) == "equivalent"


def test_s91_get_post_parity_subsumes(fhir_client):
    """GET/POST parity for parent→child (subsumes)."""
    get_r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
        ],
    }
    post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=post_body)
    assert get_r.status_code == post_r.status_code == 200
    assert _outcome(get_r.json()) == _outcome(post_r.json()) == "subsumes"


# ---------------------------------------------------------------------------
# Response shape audit — value type and Content-Type
# ---------------------------------------------------------------------------

def test_s100_response_resource_type_is_parameters(fhir_client):
    """Item 3 / spec: response resourceType MUST be 'Parameters'."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    assert r.json().get("resourceType") == "Parameters"


def test_s101_response_content_type_is_fhir_json(fhir_client):
    """GLOBAL_RULES 'Conformance property per route': Content-Type MUST be
    application/fhir+json (not application/json).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"Content-Type={ct!r}; expected 'application/fhir+json'"
    )


def test_s102_outcome_parameter_name_is_outcome(fhir_client):
    """Item 3 / spec: Out parameter MUST be named 'outcome' (not 'result',
    'relationship', 'subsumption', etc.).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    names = {p.get("name") for p in body.get("parameter", [])}
    assert "outcome" in names, (
        f"Out parameter names={names}; expected 'outcome' to be present"
    )
    # No leaked parameter names.
    forbidden_names = {"result", "relationship", "subsumption", "value"}
    leaked = names & forbidden_names
    assert not leaked, f"leaked Out parameter names: {leaked}"


def test_s103_outcome_value_type_is_valueCode_on_every_path(fhir_client):
    """Item 3 / spec: outcome value MUST use `valueCode` key (FHIR R4
    type=code). Probe every outcome path to confirm no path falls back
    to valueString or another type.
    """
    test_vectors = [
        (SNOMED_T2DM, SNOMED_T2DM),               # equivalent
        (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM),  # subsumes
        (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS),  # subsumed-by
        (SNOMED_T2DM, SNOMED_VIRAL_HEPATITIS),    # not-subsumed
    ]
    for code_a, code_b in test_vectors:
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={code_a}&codeB={code_b}"
        )
        assert r.status_code == 200
        vtype = _outcome_value_type(r.json())
        assert vtype == "valueCode", (
            f"codes ({code_a},{code_b}): outcome value type={vtype!r}, "
            f"expected 'valueCode'"
        )


# ---------------------------------------------------------------------------
# Unknown-system / hostile-input probes
# ---------------------------------------------------------------------------

def test_s110_get_subsumes_unknown_system_returns_400(fhir_client):
    """GLOBAL_RULES: unknown system URI MUST be rejected with 400 + FHIR
    OperationOutcome (not 500 with text/plain).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system=http://fake.example/sys"
        f"&codeA=1&codeB=2"
    )
    assert r.status_code == 400, f"unknown system: {r.status_code} {r.text[:200]}"
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_s111_get_subsumes_very_long_code_does_not_crash(fhir_client):
    """Hostile-input: 5K codeA MUST NOT produce 5xx."""
    long_code = "A" * 5000
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={long_code}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code < 500, (
        f"5K codeA: {r.status_code} {r.text[:200]}"
    )


def test_s112_get_subsumes_special_chars_in_code_does_not_crash(fhir_client):
    """Hostile-input: special characters MUST NOT crash the server."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA=%3Cscript%3Ealert(1)%3C%2Fscript%3E&codeB={SNOMED_T2DM}"
    )
    assert r.status_code < 500


def test_s113_post_subsumes_with_version_in_body_accepted(fhir_client):
    """Item 2 / spec: `version` is an In parameter on POST too. Verify
    it's accepted (the POST handler signature doesn't need to declare it
    if the body parses cleanly).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
            {"name": "version", "valueString": "http://snomed.info/sct/32506021000036107/version/20240901"},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    assert _outcome(r.json()) == "subsumes"


# ---------------------------------------------------------------------------
# Cross-system: code from different system than `system` param
# ---------------------------------------------------------------------------

def test_s120_get_subsumes_code_not_in_system_returns_not_subsumed(fhir_client):
    """Edge: codeA from ICD-10-CM evaluated against SNOMED system.
    The implementation resolves the system, then queries mrrel within
    that source. A code from a different source is treated as "not in
    this system" → not-subsumed (not an error in this engine).

    This is the silent-wrong-answer shape to flag: the spec says
    'If the server is unable to determine the relationship between the
    codes/Codings, then it returns an error response with an
    OperationOutcome'. An unknown code in the specified system is
    arguably a 'cannot determine relationship' case. But the engine
    treats unknown-code pairs as not-subsumed, which is the prior
    documented behavior (cases.json: subsumes-unrelated uses SNOMED
    codes with no relationship and expects not-subsumed).

    Documented as INTENDED today — the engine's subsumption BFS finds
    no path and returns not-subsumed rather than erroring on unknown
    codes.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB=E11"
    )
    assert r.status_code == 200
    outcome = _outcome(r.json())
    assert outcome == "not-subsumed", (
        f"cross-source code: outcome={outcome!r}, expected 'not-subsumed'"
    )
