"""EXPLORER probes for CS-04 (CodeSystem $subsumes Operation).

Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
       (build: https://build.fhir.org/codesystem-operation-subsumes.html)

EXPLORER lens: lateral thinking — unusual parameter combinations,
integration corners, route-coverage gaps, and shape probes that no
prior test has tried. Per the iteration prompt, the focus areas are:

  1. **4-shape POST Content-Type probes** (CF-EXPLORER-CS02-01 partial-
     close pattern, per CS-03 EXPLORER): one Content-Type probe per
     spec-listed input encoding AND the error path:
       (a) system + codeA + codeB body
       (b) codingA + codingB body
       (c) version-included body
       (d) error path (mixed-system) body
     HISTORIAN test_h90/h91/h92 cover 3 error paths; the version-included
     success path is closed here.

  2. **Instance-level route gap** (CF-SKEPTIC-CS04-01): confirm
     `/fhir/CodeSystem/{id}/$subsumes` falls through to a conformant 404
     (not a Starlette default JSON 405 / not a silent 200 from a stale
     route registration).

  3. **Directionality mirror invariant** (systematic probe): subsumes(A,B)
     and subsumed-by(B,A) MUST be exact mirrors; not-subsumed is
     symmetric. HISTORIAN test_h100/h101 cover 2 cases; EXPLORER probes
     the invariant with cross-source codes and the same-code path.

  4. **`equivalent` outcome variants**:
       (a) codeA == codeB (same code, same system)
       (b) codingA == codingB (same coding object on POST)
     SKEPTIC test_s10 covers the scalar variant; EXPLORER probes the
     coding-object variant.

  5. **Self-subsumption edge case**: codeA subsumes codeA via coding
     object (same code) → equivalent (not subsumes).

  6. **Multi-system probing**:
       (a) codingA from SNOMED, codingB from ICD-10-CM, system=SNOMED —
           the mixed-system check (item 9) MUST reject.
       (b) codingA from SNOMED US edition, codingB from SNOMED
           international, system=SNOMED — `http://snomed.info/sct` is
           the canonical URI; the US-edition URL form
           `http://snomed.info/sct/731000124108` is NOT registered as
           an alias → mixed-system error (per spec wording; client
           passes consistent URIs).

  7. **Version param edge cases**:
       (a) `version` for non-existent version (still 200; version is
           accepted but ignored per NOT A BUG registry).
       (b) `version` for current version (explicit).
       (c) `version` for historical version (numeric/iso).
     SKEPTIC test_s60/s61/s113 cover basic acceptance; EXPLORER probes
     the unusual version values.

  8. **Coding with display but no code**: per spec, a Coding without
     code is malformed; the helper returns None and the missing-scalar
     check fires. (Verifies HISTORIAN test_h21 for the coding-object
     specifically when display IS present.)

  9. **Coding with version embedded**: a Coding MAY carry its own
     `version` field (FHIR R4 Coding.version). The helper MUST NOT use
     it to override the `version` param; the helper extracts only
     (system, code). Per spec, the In `version` param is the version
     of the code system; the Coding.version is metadata about the
     coding. Confirm no implicit override.

 10. **All supported systems**: parametrize the equivalent (same-code)
     probe over every seeded system in the conformance fixture.

 11. **Combined scalar + coding inputs**:
       (a) `codeA` (scalar) + `codingB` (Coding) — server handles?
       (b) `codingA` (Coding) + `codeB` (scalar) — server handles?
     HISTORIAN test_h80/h81 cover the success path; EXPLORER adds the
     directionality invariant for the combined-encoding case.

 12. **Large code values**: >1000 chars (SKEPTIC test_s111 covers GET;
     EXPLORER adds the POST path).

 13. **Special chars in codes**: URL-encoded, unicode (SKEPTIC test_s112
     covers GET; EXPLORER adds POST).

 14. **Outcome parameter shape**: `valueCode` (not valueString); name is
     exactly `outcome`. SKEPTIC test_s100-s103 + test_s50 cover this on
     GET; EXPLORER re-verifies on the POST coding-object path.

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts POSITIVE
success shape (200 + expected fields) OR a specific error message
content, not just the absence of one error string.
"""

from __future__ import annotations

import urllib.parse

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
#
# Out `outcome` MUST be one of these 4 strings (closed enum,
# ConceptSubsumptionOutcome value set).
VALID_OUTCOMES = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}

SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_US = "http://snomed.info/sct/731000124108"  # US edition URL form
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child (descendant of 73211009)
SNOMED_VIRAL_HEPATITIS = "3738000"     # unrelated to diabetes branch
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"            # unrelated to diabetes branch
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"                    # Type 2 diabetes mellitus (ICD-10-CM)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

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


def _outcome_value_type(body: dict) -> str | None:
    """Return the value-key name (e.g. 'valueCode') of the outcome parameter."""
    for p in body.get("parameter", []):
        if p.get("name") == "outcome":
            for k in p:
                if k.startswith("value"):
                    return k
    return None


def _diagnostics(body: dict) -> str:
    """Extract the diagnostics string from an OperationOutcome."""
    for issue in body.get("issue", []):
        if "diagnostics" in issue:
            return issue["diagnostics"]
    return ""


# ---------------------------------------------------------------------------
# 1. POST Content-Type per spec-listed input encoding (CF-EXPLORER-CS02-01)
#    4-shape family: system+codeA+codeB / codingA+codingB / version-included /
#    error-path (mixed-system). HISTORIAN test_h90/h91/h92 cover 3 error paths;
#    this closes the version-included success path AND adds the success-path
#    Content-Type assertions (no prior probe asserts Content-Type on the
#    success shape for every encoding).
# ---------------------------------------------------------------------------

def test_e10_post_subsumes_system_code_body_emits_fhir_mimetype(fhir_client):
    """POST ``$subsumes`` with system+codeA+codeB Parameters body MUST
    emit ``Content-Type: application/fhir+json`` (FHIR R4 §3.1.0.1.9).
    The CR-001 parametrized Content-Type probe skips ``$subsumes`` because
    it requires complex parameters; this probe closes that coverage gap.

    Spec basis (https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    "Request: Using Parameters"): a POST with scalar codeA/codeB + system
    is the spec-documented encoding.
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
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST $subsumes (system+codeA+codeB) Content-Type is {ct!r}; spec "
        f"mandates application/fhir+json (FHIR R4 §3.1.0.1.9)."
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"
    assert _outcome(body_json) == "subsumes"


def test_e11_post_subsumes_coding_body_emits_fhir_mimetype(fhir_client):
    """POST ``$subsumes`` with codingA+codingB (valueCoding) MUST emit
    ``application/fhir+json``. Per spec POST example "Request: Using
    Codings" at https://hl7.org/fhir/R4/codesystem-operation-subsumes.html.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"
    assert _outcome(body_json) == "subsumes"


def test_e12_post_subsumes_version_included_body_emits_fhir_mimetype(fhir_client):
    """POST ``$subsumes`` with an explicit ``version`` parameter in the
    body MUST emit ``application/fhir+json``. The HISTORIAN architect
    handoff explicitly flagged this shape as the open variant after
    HISTORIAN test_h90/h91/h92 covered the 3 error-path Content-Types.

    Per spec In `version`: "The version of the code system, if one was
    provided in the source data". medterm4ds accepts the param for spec-
    compatibility (NOT A BUG registry: "version parameter accepted but
    ignored on $lookup / $validate-code / $subsumes").
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "version", "valueString": "2024-09"},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST $subsumes (version-included) Content-Type is {ct!r}; spec "
        f"mandates application/fhir+json."
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"
    assert _outcome(body_json) == "subsumes"


def test_e13_post_subsumes_mixed_system_error_emits_fhir_mimetype(fhir_client):
    """POST ``$subsumes`` returning 400 (mixed-system) MUST still emit
    ``application/fhir+json`` Content-Type with an OperationOutcome body.
    Complements HISTORIAN test_h91 (which checks Content-Type) by also
    asserting the body shape on the mixed-system path.
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
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST $subsumes error Content-Type is {ct!r}; spec mandates "
        f"application/fhir+json on every error response (§3.1.0.1.5 + §3.1.0.1.9)."
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "OperationOutcome"


# ---------------------------------------------------------------------------
# 2. Instance-level route gap (CF-SKEPTIC-CS04-01)
#    Confirm /fhir/CodeSystem/{id}/$subsumes falls through to a conformant
#    response (not a silent 200, not a Starlette default JSON 405).
# ---------------------------------------------------------------------------

def test_e20_instance_level_subsumes_get_falls_through_conformant(fhir_client):
    """EXPLORER lens (CF-SKEPTIC-CS04-01): instance-level GET
    ``/fhir/CodeSystem/{id}/$subsumes`` is not registered as an explicit
    operation route. The spec lists this URL pattern. Per spec: "The
    system parameter is required unless the operation is invoked on an
    instance of a code system resource."

    medterm4ds has no CodeSystem resource persistence, so the route MUST
    fall through to a conformant response (either the type-level $ lookup
    rejection or a 404 OperationOutcome). It MUST NOT produce a
    Starlette default `{"detail":"Not Found"}` 404 with
    `application/json` Content-Type.

    Per CF-SKEPTIC-CS04-01, the conformance fixture doesn't seed
    CodeSystem resources with IDs, so instance-level system-resolution
    would be ambiguous even if the route existed.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/anything/$subsumes",
        params={"codeA": SNOMED_DIABETES_MELLITUS, "codeB": SNOMED_T2DM},
    )
    # Acceptable outcomes: 404 (read_unknown_resource_type catch-all),
    # 400 (if the route somehow matched but had no system), or 422
    # (validation). The KEY invariant: MUST be a FHIR OperationOutcome
    # with `application/fhir+json` Content-Type.
    assert r.status_code in (400, 404, 422), (
        f"instance-level GET $subsumes → {r.status_code}; expected 400/404/422. "
        f"Body: {r.text[:300]!r}"
    )
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"instance-level GET $subsumes Content-Type is {ct!r}; spec mandates "
        f"application/fhir+json on every response."
    )
    body_json = r.json()
    assert body_json.get("resourceType") in (
        "OperationOutcome",
        "Parameters",
    ), f"unexpected resourceType: {body_json.get('resourceType')!r}"


def test_e21_instance_level_subsumes_post_falls_through_conformant(fhir_client):
    """EXPLORER lens (CF-SKEPTIC-CS04-01): instance-level POST
    ``/fhir/CodeSystem/{id}/$subsumes`` is not registered as an explicit
    operation route. MUST fall through to a conformant response — NOT
    Starlette's default 405 with `application/json` body.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/anything/$subsumes", json=body)
    assert r.status_code in (400, 404, 405, 422), (
        f"instance-level POST $subsumes → {r.status_code}; expected 400/404/405/422. "
        f"Body: {r.text[:300]!r}"
    )
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"instance-level POST $subsumes Content-Type is {ct!r}; spec mandates "
        f"application/fhir+json."
    )
    body_json = r.json()
    assert body_json.get("resourceType") in (
        "OperationOutcome",
        "Parameters",
    ), f"unexpected resourceType: {body_json.get('resourceType')!r}"


# ---------------------------------------------------------------------------
# 3. Directionality mirror invariant — systematic probe
#    subsumes(A,B) ↔ subsumed-by(B,A); not-subsumed is symmetric;
#    equivalent is reflexive. HISTORIAN test_h100/h101 cover 2 cases;
#    EXPLORER adds the cross-source and POST parity variants.
# ---------------------------------------------------------------------------

def test_e30_directionality_mirror_on_post_coding(fhir_client):
    """EXPLORER lens: HISTORIAN test_h100 covered the mirror invariant on
    GET. EXPLORER probes the same invariant via POST with codingA/codingB
    — the POST path extracts codes from valueCoding; the engine MUST
    produce the same mirror outcome.

    A=parent, B=child via POST → subsumes.
    A=child, B=parent via POST → subsumed-by.
    """
    body_ab = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    body_ba = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
        ],
    }
    r_ab = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body_ab)
    r_ba = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body_ba)
    assert r_ab.status_code == r_ba.status_code == 200, (
        f"POST $subsumes status codes: ab={r_ab.status_code}, ba={r_ba.status_code}"
    )
    out_ab = _outcome(r_ab.json())
    out_ba = _outcome(r_ba.json())
    assert out_ab == "subsumes", f"A=parent,B=child: outcome={out_ab!r}"
    assert out_ba == "subsumed-by", f"A=child,B=parent: outcome={out_ba!r}"


def test_e31_directionality_mirror_not_subsumed_symmetric_on_post(fhir_client):
    """EXPLORER lens: not-subsumed is symmetric on the POST path.
    A=child, B=unrelated → not-subsumed.
    A=unrelated, B=child → not-subsumed.
    """
    body_ab = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_VIRAL_HEPATITIS}},
        ],
    }
    body_ba = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_VIRAL_HEPATITIS}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r_ab = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body_ab)
    r_ba = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body_ba)
    assert r_ab.status_code == r_ba.status_code == 200
    assert _outcome(r_ab.json()) == "not-subsumed"
    assert _outcome(r_ba.json()) == "not-subsumed"


# ---------------------------------------------------------------------------
# 4. `equivalent` outcome via POST coding object (same coding for A and B)
# ---------------------------------------------------------------------------

def test_e40_equivalent_outcome_via_identical_coding_objects(fhir_client):
    """EXPLORER lens: per spec In/Out Parameters table, `equivalent` is
    returned when A and B are the same concept. SKEPTIC test_s10 covered
    the scalar path (codeA == codeB). EXPLORER probes the POST path with
    IDENTICAL codingA and codingB objects (same system, same code).

    Spec basis (https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    Out `outcome`): "equivalent — A and B are the same concept".
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    outcome = _outcome(r.json())
    assert outcome == "equivalent", (
        f"identical codings should yield 'equivalent'; got {outcome!r}"
    )


def test_e41_self_subsumption_returns_equivalent_not_subsumes(fhir_client):
    """EXPLORER lens: per the spec outcome table, self-subsumption
    (codeA subsumes codeA) MUST return `equivalent`, NOT `subsumes`. The
    engine's first check is `if code_a == code_b: return equivalent`
    (apps/fhir_api.py:1756). Verify this is the actual behavior on POST
    with coding objects.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200
    outcome = _outcome(r.json())
    assert outcome == "equivalent", (
        f"self-subsumption should yield 'equivalent' (not 'subsumes'); "
        f"got {outcome!r}"
    )


# ---------------------------------------------------------------------------
# 5. Multi-system probing — confirm the mixed-system check correctly fires
#    for cross-source pairs.
# ---------------------------------------------------------------------------

def test_e50_mixed_system_snomed_icd10cm_rejected(fhir_client):
    """EXPLORER lens: codingA from SNOMED, codingB from ICD-10-CM,
    system=SNOMED. The mixed-system check MUST reject with 400.

    Spec basis (https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    In `codingA`): "The code system does not have to match the specified
    subsumption code system, but the relationships between the code
    systems must be well established". medterm4ds has no cross-system
    relationship map; the check is conservative.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codingB", "valueCoding": {"system": ICD10CM_URI, "code": ICD10CM_E11}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:300]!r}"
    body_json = r.json()
    assert body_json.get("resourceType") == "OperationOutcome"
    diag = _diagnostics(body_json)
    # Diagnostic MUST name both systems (mirrors HISTORIAN test_h50).
    assert ICD10CM_URI in diag, f"diagnostics should name ICD-10-CM URI; got: {diag!r}"
    assert SNOMED_URI in diag, f"diagnostics should name SNOMED URI; got: {diag!r}"


def test_e51_mixed_system_snomed_us_edition_rejected(fhir_client):
    """EXPLORER lens: the SNOMED US-edition URL form
    (`http://snomed.info/sct/731000124108`) is NOT registered as an
    alias of the canonical `http://snomed.info/sct`. A client passing
    the US-edition URL as codingA.system MUST get a 400 (per the
    mixed-system check).

    Per CF-HISTORIAN-CS04-01, the mixed-system check uses STRING
    comparison. The US-edition URL form is acceptable to reject today
    (spec wording: client passes consistent URIs). Documented as a
    LOW carry-forward — future alias-resolution would resolve both to
    `source=SNOMEDCT_US`.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI_US, "code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:300]!r}"
    diag = _diagnostics(r.json())
    assert "codingA" in diag
    assert SNOMED_URI_US in diag
    assert SNOMED_URI in diag


# ---------------------------------------------------------------------------
# 6. Version param edge cases — unusual version values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "version_val",
    [
        "9999-01",            # non-existent future version
        "current",            # explicit "current"
        "2024-09-01",         # ISO date form
        "v1.2.3",             # semver-like
        "0",                  # numeric string
    ],
    ids=["future", "current", "iso_date", "semver", "numeric_str"],
)
def test_e60_get_subsumes_unusual_version_accepted(fhir_client, version_val):
    """EXPLORER lens: the `version` parameter is accepted for spec-
    compatibility (per NOT A BUG registry: "version parameter accepted
    but ignored on $lookup / $validate-code / $subsumes"). EXPLORER
    probes unusual version values — non-existent, "current", ISO dates,
    semver, numeric strings.

    Spec basis (https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    In `version` 0..1 string): "The version of the code system, if one
    was provided in the source data".

    A server that rejected unusual values would silently break clients
    passing a valid semver or ISO date version string. Each probe asserts
    the positive success shape (200 + Parameters body).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
            "version": version_val,
        },
    )
    assert r.status_code == 200, (
        f"version={version_val!r}: {r.status_code} {r.text[:300]!r}"
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"
    assert _outcome(body_json) in VALID_OUTCOMES


def test_e61_post_subsumes_coding_with_embedded_version_not_overriding(fhir_client):
    """EXPLORER lens: a Coding MAY carry its own `version` field (FHIR R4
    Coding.version 0..1 string). The implementation's helper
    `_extract_named_coding_from_parameters` extracts only (system, code);
    the embedded version is ignored. The In `version` param at the
    Parameters level is the canonical version specifier.

    Spec basis (https://hl7.org/fhir/R4/datatypes.html#Coding): Coding
    has version 0..1 string "The version of the code system which was
    used when choosing this code." At the operation layer, the In
    `version` param is documented separately.

    This probe confirms the implementation doesn't silently use the
    embedded Coding.version as the operation's version specifier.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "version", "valueString": "2024-09"},
            {
                "name": "codingA",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DIABETES_MELLITUS,
                    "version": "1999-01",  # embedded, conflicting
                },
            },
            {
                "name": "codingB",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM,
                    "version": "1999-01",  # embedded, conflicting
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"
    # The operation should still resolve correctly; embedded version is
    # metadata, not the operation's version specifier.
    assert _outcome(body_json) in VALID_OUTCOMES


# ---------------------------------------------------------------------------
# 7. Coding with display but no code (malformed)
# ---------------------------------------------------------------------------

def test_e70_coding_with_display_no_code_falls_through(fhir_client):
    """EXPLORER lens: per HISTORIAN test_h21, a valueCoding without code
    is malformed and the helper returns None. EXPLORER probes the variant
    where the Coding has a `display` field (looking complete to a human
    reader) but no code — the helper MUST still treat it as malformed
    and fall through to the missing-scalar check.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {
                "name": "codingA",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "display": "Type 2 diabetes mellitus",
                    # no code
                },
            },
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 400, (
        f"coding with display but no code: {r.status_code} {r.text[:300]!r}"
    )
    diag = _diagnostics(r.json())
    assert "required" in diag.lower(), (
        f"missing-scalar message expected; got: {diag!r}"
    )


# ---------------------------------------------------------------------------
# 8. All supported systems — parametrize the equivalent probe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "system_uri,code",
    [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (RXNORM_URI, RXNORM_METFORMIN),
        (ICD10CM_URI, ICD10CM_E11),
    ],
    ids=["snomed", "rxnorm", "icd10cm"],
)
def test_e80_equivalent_outcome_for_every_seeded_system(fhir_client, system_uri, code):
    """EXPLORER lens: per spec, the `equivalent` outcome applies when
    codeA == codeB. EXPLORER parametrizes this over every seeded system
    in the conformance fixture. A system that produces anything other
    than `equivalent` for the same code would indicate an engine lookup
    failure for that source.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={"system": system_uri, "codeA": code, "codeB": code},
    )
    assert r.status_code == 200, (
        f"system={system_uri} code={code}: {r.status_code} {r.text[:300]!r}"
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"
    outcome = _outcome(body_json)
    assert outcome == "equivalent", (
        f"system={system_uri} self-subsumption should yield 'equivalent'; "
        f"got {outcome!r}"
    )


# ---------------------------------------------------------------------------
# 9. Combined scalar + coding inputs — directionality invariant
#    HISTORIAN test_h80/h81 covered the basic precedence; EXPLORER
#    verifies the directionality mirror for the combined encoding.
# ---------------------------------------------------------------------------

def test_e90_combined_scalar_codeA_codingB_directionality(fhir_client):
    """EXPLORER lens: codeA (scalar) + codingB (Coding) is spec-permitted.
    EXPLORER probes the directionality mirror in this mixed encoding:
      - A=parent (scalar), B=child (coding) → subsumes
      - A=child (scalar), B=parent (coding) → subsumed-by
    """
    # A=parent, B=child
    body1 = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    # A=child, B=parent
    body2 = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_T2DM},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
        ],
    }
    r1 = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body1)
    r2 = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body2)
    assert r1.status_code == r2.status_code == 200, (
        f"combined encoding status: r1={r1.status_code}, r2={r2.status_code}"
    )
    assert _outcome(r1.json()) == "subsumes", (
        f"A=parent (scalar), B=child (coding): expected 'subsumes'; "
        f"got {_outcome(r1.json())!r}"
    )
    assert _outcome(r2.json()) == "subsumed-by", (
        f"A=child (scalar), B=parent (coding): expected 'subsumed-by'; "
        f"got {_outcome(r2.json())!r}"
    )


def test_e91_combined_codingA_scalar_codeB_directionality(fhir_client):
    """EXPLORER lens: mirror of test_e90 — codingA (Coding) + codeB (scalar).
    """
    body1 = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
        ],
    }
    body2 = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            {"name": "codeB", "valueCode": SNOMED_DIABETES_MELLITUS},
        ],
    }
    r1 = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body1)
    r2 = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body2)
    assert r1.status_code == r2.status_code == 200
    assert _outcome(r1.json()) == "subsumes"
    assert _outcome(r2.json()) == "subsumed-by"


# ---------------------------------------------------------------------------
# 10. Large code values on POST (SKEPTIC test_s111 covered GET)
# ---------------------------------------------------------------------------

def test_e100_post_subsumes_large_codes_does_not_crash(fhir_client):
    """EXPLORER lens: a POST body with very large code values (>1000
    chars) MUST NOT crash the server. Per SKEPTIC test_s111 (GET path),
    the engine handles unknown codes gracefully — returns `not-subsumed`.
    EXPLORER probes the POST path with the same input class.

    Spec basis: FHIR R4 §3.4.1 doesn't mandate a max code length; very
    large codes are valid input. A server that crashed on >1000-char
    codes would be a denial-of-service surface.
    """
    big_a = "A" * 1100
    big_b = "B" * 1100
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": big_a},
            {"name": "codeB", "valueCode": big_b},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    # Per NOT A BUG registry, large codes are unknown to the seeded
    # fixture → not-subsumed (no relationship found). Acceptance: 200
    # with not-subsumed (or equivalent if both codes are identical).
    assert r.status_code == 200, (
        f"large codes POST: {r.status_code} {r.text[:300]!r}"
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"
    assert _outcome(body_json) in VALID_OUTCOMES


def test_e101_post_subsumes_large_identical_codes_returns_equivalent(fhir_client):
    """EXPLORER lens: when codeA == codeB (both very large identical
    strings), the engine's first check `if code_a == code_b` short-
    circuits to `equivalent`. Verify this on POST with >1000-char codes.
    """
    big = "X" * 1100
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": big},
            {"name": "codeB", "valueCode": big},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200
    assert _outcome(r.json()) == "equivalent"


# ---------------------------------------------------------------------------
# 11. Special chars in codes on POST (SKEPTIC test_s112 covered GET)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "code_a,code_b",
    [
        ("a;b", "c|d"),                  # SQL-ish chars
        ("ünïcödé", "ünïcödé"),          # unicode (same → equivalent)
        ("a b c", "x y z"),              # whitespace
        ("100%", "50%"),                 # percent
    ],
    ids=["sql_chars", "unicode_same", "whitespace", "percent"],
)
def test_e110_post_subsumes_special_chars_does_not_crash(fhir_client, code_a, code_b):
    """EXPLORER lens: POST body with special characters in code values
    MUST NOT crash the server. SKEPTIC test_s112 covered the GET path
    (URL-encoded). EXPLORER probes the POST path with raw JSON values
    (no URL encoding needed since JSON handles arbitrary strings).

    The engine treats these as unknown codes → `not-subsumed` (or
    `equivalent` if code_a == code_b).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": code_a},
            {"name": "codeB", "valueCode": code_b},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, (
        f"codes={code_a!r},{code_b!r}: {r.status_code} {r.text[:300]!r}"
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"
    outcome = _outcome(body_json)
    assert outcome in VALID_OUTCOMES
    # For unicode-same case, expect equivalent.
    if code_a == code_b:
        assert outcome == "equivalent", (
            f"identical special-char codes should yield 'equivalent'; "
            f"got {outcome!r}"
        )


def test_e111_get_subsumes_url_encoded_special_chars_accepted(fhir_client):
    """EXPLORER lens: GET with URL-encoded special chars in codeA/codeB
    query params. FastAPI decodes them automatically; the engine treats
    them as unknown codes. Verify no crash + conformant response shape.
    """
    code_a = urllib.parse.quote("a;b|c", safe="")
    code_b = urllib.parse.quote("x/y+z", safe="")
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={code_a}&codeB={code_b}"
    )
    assert r.status_code == 200, (
        f"URL-encoded codes: {r.status_code} {r.text[:300]!r}"
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"
    assert _outcome(body_json) in VALID_OUTCOMES


# ---------------------------------------------------------------------------
# 12. Outcome parameter shape — valueCode on POST (SKEPTIC covered GET)
# ---------------------------------------------------------------------------

def test_e120_outcome_value_type_is_valueCode_on_post_coding_path(fhir_client):
    """EXPLORER lens: per spec Out `outcome` 1..1 code, the value MUST be
    a `valueCode` (not valueString, not valueCoding). SKEPTIC test_s103
    verified this on every GET path; EXPLORER re-verifies on the POST
    coding-object path.

    Spec basis (https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    Out `outcome` 1..1 code): "The subsumption relationship between
    code/Coding 'A' and code/Coding 'B'". Type=code → valueCode.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200
    body_json = r.json()
    value_type = _outcome_value_type(body_json)
    assert value_type == "valueCode", (
        f"outcome value type should be 'valueCode' on POST coding path; "
        f"got {value_type!r}. Body: {body_json}"
    )


def test_e121_outcome_parameter_name_is_exactly_outcome(fhir_client):
    """EXPLORER lens: per spec Out parameter is named `outcome` exactly
    (not `result`, not `value`). Re-verify on POST with mixed encoding.
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
    assert r.status_code == 200
    body_json = r.json()
    param_names = [p.get("name") for p in body_json.get("parameter", [])]
    assert "outcome" in param_names, (
        f"POST $subsumes Out parameter missing 'outcome'; got {param_names!r}"
    )
    # No spec-listed Out parameter other than `outcome`.
    assert set(param_names) == {"outcome"}, (
        f"unexpected Out parameters: {param_names!r}"
    )


# ---------------------------------------------------------------------------
# 13. POST body without resourceType field (defensive)
# ---------------------------------------------------------------------------

def test_e130_post_subsumes_no_resourceType_accepted(fhir_client):
    """EXPLORER lens: a POST body without `resourceType: "Parameters"` is
    technically not FHIR-spec-conformant at the wire level, but
    FastAPI/medterm4ds parses the body as a dict and the handler walks
    `body.get("parameter", [])`. A body that omits resourceType but
    includes the parameter array SHOULD still work (lenient parsing).

    Per FHIR R4 §3.6.1, the Parameters resource MUST declare
    resourceType. This probe documents the CURRENT lenient behavior —
    future hardening could enforce the resourceType check.
    """
    body = {
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    # Lenient parsing — handler walks parameter[] regardless of resourceType.
    assert r.status_code == 200, (
        f"no-resourceType body: {r.status_code} {r.text[:300]!r}"
    )
    body_json = r.json()
    assert _outcome(body_json) in VALID_OUTCOMES


# ---------------------------------------------------------------------------
# 14. Cross-shape consistency — POST with all 3 encodings present
# ---------------------------------------------------------------------------

def test_e140_post_subsumes_all_encodings_present_uses_scalar(fhir_client):
    """EXPLORER lens: when the POST body contains scalar codeA/codeB AND
    codingA/codingB AND version, the documented precedence is "scalar
    wins when present" (HISTORIAN test_h82). EXPLORER probes the case
    where the scalar values and coding values produce DIFFERENT outcomes
    — the scalar values win, and the engine resolves them.

    codeA=73211009 (parent), codeB=44054006 (child) → subsumes
    codingA.code=44054006 (child), codingB.code=73211009 (parent)
    The scalar values win → subsumes (not subsumed-by).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "version", "valueString": "2024"},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},  # parent
            {"name": "codeB", "valueCode": SNOMED_T2DM},               # child
            # codings would produce subsumed-by (swapped)
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200
    outcome = _outcome(r.json())
    # Scalar wins → subsumes (parent subsumes child).
    assert outcome == "subsumes", (
        f"scalar+coding precedence: expected 'subsumes' (scalar wins); "
        f"got {outcome!r}"
    )


def test_e141_post_subsumes_coding_only_no_scalar_uses_coding(fhir_client):
    """EXPLORER lens: when ONLY codingA/codingB are present (no scalar
    codeA/codeB), the handler extracts codes from the codings. EXPLORER
    verifies this is the actual fallback path (not silent reject).

    Mirrors SKEPTIC test_s71 (the original bug-finder) with a different
    parent/child pair to ensure the engine's is_descendant path is
    exercised, not just the equivalent short-circuit.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            # No codeA/codeB scalar.
            {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
            {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    # Parent subsumes child.
    assert _outcome(r.json()) == "subsumes"


# ---------------------------------------------------------------------------
# 15. XML format on $subsumes (CR-002 methodology extension, mirror of
#     HISTORIAN test_h61 on additional paths)
# ---------------------------------------------------------------------------

def test_e150_subsumes_xml_format_renders_valueCode_lowercase(fhir_client):
    """EXPLORER lens (CR-002 methodology extension): ``_format=xml`` on
    the $subsumes operation route MUST render the outcome as
    ``<valueCode value="subsumes"/>`` (or any of the 4 enum values).
    HISTORIAN test_h61 confirmed the equivalent path renders lowercase;
    EXPLORER adds the subsumes path. The probe guards against a future
    handler bypassing `_fhir_response` (which routes through
    `_wants_xml` → `to_fhir_xml`).

    Spec basis: FHIR R4 §3.1.0.1.9 + CR-002 fix shape
    (`_scalar_to_xml_attr` boolean special-case in `engines/fhir/xml.py`).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params=[
            ("system", SNOMED_URI),
            ("codeA", SNOMED_DIABETES_MELLITUS),
            ("codeB", SNOMED_T2DM),
            ("_format", "xml"),
        ],
    )
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+xml" in ct, (
        f"_format=xml: Content-Type is {ct!r}; spec mandates application/fhir+xml"
    )
    body_text = r.text
    # The outcome MUST be rendered as <valueCode value="subsumes"/> (lowercase).
    assert 'value="subsumes"' in body_text, (
        f"valueCode=subsumes must render as value=\"subsumes\"; body snippet: "
        f"{body_text[:500]!r}"
    )
    # Defensive: the parameter name must be `outcome`. The XML serializer
    # renders it as <name value="outcome"/> inside a <parameter> element
    # (per FHIR R4 XML convention for Parameters resources).
    assert "outcome" in body_text, (
        f"'outcome' literal not found in XML body; snippet: {body_text[:500]!r}"
    )


def test_e151_subsumes_xml_format_renders_all_four_outcomes(fhir_client):
    """EXPLORER lens: confirm all 4 outcome values render correctly in
    XML (no capitalization, no encoding drift). HISTORIAN test_h61
    covered `equivalent`; EXPLORER adds `subsumes`, `subsumed-by`, and
    `not-subsumed`. The hyphen in `subsumed-by` and `not-subsumed` is
    the spec-correct form — a serializer bug might URL-encode or strip it.
    """
    cases = [
        # (codeA, codeB, expected_outcome)
        (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM, "subsumes"),
        (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS, "subsumed-by"),
        (SNOMED_T2DM, SNOMED_VIRAL_HEPATITIS, "not-subsumed"),
        (SNOMED_T2DM, SNOMED_T2DM, "equivalent"),
    ]
    for code_a, code_b, expected in cases:
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("system", SNOMED_URI),
                ("codeA", code_a),
                ("codeB", code_b),
                ("_format", "xml"),
            ],
        )
        assert r.status_code == 200, (
            f"codes={code_a},{code_b}: {r.status_code} {r.text[:300]!r}"
        )
        body_text = r.text
        assert f'value="{expected}"' in body_text, (
            f"expected value=\"{expected}\" in XML body; snippet: {body_text[:500]!r}"
        )


# ---------------------------------------------------------------------------
# 16. Accept-header negotiation mirrors _format (TS-01 EXPLORER QA-009)
# ---------------------------------------------------------------------------

def test_e160_subsumes_accept_header_xml_overrides_default_json(fhir_client):
    """EXPLORER lens: per TS-01 EXPLORER QA-009, the `_format` query
    parameter overrides `Accept` (FHIR R4 §3.1.0.1.11). EXPLORER probes
    the inverse: `Accept: application/fhir+xml` (without `_format`) on
    the $subsumes operation route. The server's `_wants_xml` helper
    inspects `Accept` first when `_format` is absent.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
        },
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+xml" in ct, (
        f"Accept: application/fhir+xml should produce XML Content-Type; got {ct!r}"
    )


# ---------------------------------------------------------------------------
# 17. Round-trip GET → POST → GET consistency (cross-method invariant)
# ---------------------------------------------------------------------------

def test_e170_get_post_round_trip_outcome_consistency(fhir_client):
    """EXPLORER lens: GET system+codeA+codeB and POST system+codeA+codeB
    MUST produce identical outcomes for the same logical codes. EXPLORER
    probes the round-trip across all 4 outcome paths.

    Complements SKEPTIC test_s90 (GET/POST parity for identical codes)
    by exercising every outcome path.
    """
    cases = [
        # (codeA, codeB, label)
        (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM, "subsumes_path"),
        (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS, "subsumed_by_path"),
        (SNOMED_T2DM, SNOMED_VIRAL_HEPATITIS, "not_subsumed_path"),
        (SNOMED_T2DM, SNOMED_T2DM, "equivalent_path"),
    ]
    for code_a, code_b, label in cases:
        get_r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={"system": SNOMED_URI, "codeA": code_a, "codeB": code_b},
        )
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codeA", "valueCode": code_a},
                {"name": "codeB", "valueCode": code_b},
            ],
        }
        post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=post_body)
        assert get_r.status_code == post_r.status_code == 200, (
            f"{label}: GET={get_r.status_code}, POST={post_r.status_code}"
        )
        get_out = _outcome(get_r.json())
        post_out = _outcome(post_r.json())
        assert get_out == post_out, (
            f"{label}: GET outcome={get_out!r}, POST outcome={post_out!r}"
        )
        assert get_out in VALID_OUTCOMES


# ---------------------------------------------------------------------------
# 18. Outcome vocabulary audit (mirror of SKEPTIC test_s50 on POST coding)
# ---------------------------------------------------------------------------

def test_e180_outcome_never_leaks_internal_vocabulary_on_post_paths(fhir_client):
    """EXPLORER lens: per SKEPTIC test_s50, the outcome MUST be sourced
    from the closed enum (ConceptSubsumptionOutcome). EXPLORER probes
    every outcome path on the POST coding-object route to confirm no
    internal vocabulary (broader, narrower, parent, child) leaks through.

    Spec basis (https://hl7.org/fhir/R4/valueset-concept-subsumption-outcome.html):
    closed enum = {equivalent, subsumes, subsumed-by, not-subsumed}.
    """
    cases = [
        (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM),     # parent → child
        (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS),     # child → parent
        (SNOMED_T2DM, SNOMED_VIRAL_HEPATITIS),       # unrelated
        (SNOMED_T2DM, SNOMED_T2DM),                  # identical
    ]
    for code_a, code_b in cases:
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": code_a}},
                {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": code_b}},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, (
            f"codes={code_a},{code_b}: {r.status_code} {r.text[:300]!r}"
        )
        outcome = _outcome(r.json())
        assert outcome in VALID_OUTCOMES, (
            f"outcome {outcome!r} not in closed enum {VALID_OUTCOMES}; "
            f"internal vocabulary may be leaking through."
        )


# ---------------------------------------------------------------------------
# 19. Body shape audit — every successful response has resourceType=Parameters
#     and a single `outcome` parameter (no extras)
# ---------------------------------------------------------------------------

def test_e190_successful_response_has_single_outcome_parameter(fhir_client):
    """EXPLORER lens: per spec Out Parameters table, $subsumes has exactly
    ONE Out parameter (`outcome`). EXPLORER audits the response shape:
      - resourceType MUST be "Parameters"
      - parameter[] MUST have length 1
      - the single parameter MUST be `outcome`
    A response with extra parameters would be a structural drift from
    the spec table.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
        },
    )
    assert r.status_code == 200
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters"
    params = body_json.get("parameter", [])
    assert len(params) == 1, (
        f"expected exactly 1 Out parameter; got {len(params)}: {params!r}"
    )
    assert params[0].get("name") == "outcome"
    assert "valueCode" in params[0]
    assert params[0]["valueCode"] in VALID_OUTCOMES
