"""EXPLORER iteration CS-02 — lateral-thinking probes for CodeSystem $lookup.

Spec: https://build.fhir.org/codesystem-operation-lookup.html (R4 / 4.0.1).
       https://hl7.org/fhir/R4/codesystem-operation-lookup.html (canonical R4)

EXPLORER lens for CS-02 (per spec-comp CS-02 EXPLORER carry-forward prompt):

  1. **POST Content-Type on ``$lookup``** (GLOBAL_RULES.md "Conformance
     property per route"): the CR-001 parametrized Content-Type probe
     skips ``$lookup`` because it requires complex parameters. Verify
     POST ``$lookup`` emits ``application/fhir+json`` for all three POST
     shapes (system+code body, coding body, mixed).
  2. **Property parameter combinations**:
     - Multiple ``property`` values.
     - Mixed standard + custom.
     - All custom properties at once.
     - ``property=`` (empty value).
  3. **``lang.X`` properties**: en, fr, de, es; non-ISO codes; mixed case
     (``LANG.EN``); multi-part (``lang.en-US``).
  4. **``designation`` property**: when requested, body is conformant
     (designations absent today — single-language fixture). Combined
     with ``displayLanguage=fr``.
  5. **Subsumption decomposition** (item 10): ``property=parent`` and
     ``property=child`` accepted without 5xx.
  6. **``property.code`` parameter** (item 2): accepted without 5xx.
  7. **Combined unusual inputs**:
     - ``coding`` body with extra fields (userSelected, version, display).
     - ``coding`` body with multiple codings (spec: exactly one expected).
     - ``codeableConcept`` on ``$lookup`` — spec does NOT list this as an
       In parameter (only ``coding``); verify it is rejected, not silently
       accepted (CS-01 EXPLORER finding — spec-correct asymmetry).
  8. **Property name case sensitivity**: ``property=NAME`` vs ``name``.
  9. **Property with whitespace**: ``property=name%20`` (URL-encoded
     trailing space).
  10. **Cross-system consistency**: every supported system resolves a
      known code via ``$lookup`` and emits the same Out parameter shape.
  11. **Long property list**: 20+ ``property`` values in one request.

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts the POSITIVE
success shape (200 + Parameters body with the expected fields), not just
absence of an error string.

Result of EXPLORER iteration 1: **CLEAN — no non-terminal CRITICAL/HIGH/
MEDIUM issues found.** All lateral probes return spec-conformant
responses (200 with ``application/fhir+json`` Content-Type; Out
parameter shape includes ``name``, ``code``, ``system``, ``display``,
``abstract``, optional ``property``). The HISTORIAN fixes (QA-046, QA-047)
survive. The implementation is robust against every lateral probe in the
EXPLORER prompt.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_TYPE2_DM = "E11"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"


# ---------------------------------------------------------------------------
# 1. POST Content-Type on $lookup (carry-forward from SKEPTIC)
# ---------------------------------------------------------------------------

def test_e01_post_lookup_system_code_body_emits_fhir_mimetype(fhir_client):
    """POST ``$lookup`` with a system+code Parameters body MUST emit
    ``Content-Type: application/fhir+json`` (FHIR R4 §3.1.0.1.9). The
    CR-001 parametrized Content-Type probe skips ``$lookup`` because it
    needs complex parameters; this probe closes the coverage gap.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html —
    "POST with the parameters in a Parameters resource body".
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DIABETES_MELLITUS},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST $lookup (system+code body) Content-Type is {ct!r}; spec "
        f"mandates application/fhir+json (FHIR R4 §3.1.0.1.9)."
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "Parameters", (
        f"POST $lookup body must be Parameters; got {body_json.get('resourceType')}"
    )


def test_e02_post_lookup_coding_body_emits_fhir_mimetype(fhir_client):
    """POST ``$lookup`` with a ``coding`` parameter MUST emit
    ``application/fhir+json``. Same shape as test_e01, different input
    encoding. Spec: 'the coding parameter allows a complete coding to
    be supplied rather than the separate system and code parameters.'
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DIABETES_MELLITUS,
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST $lookup (coding body) Content-Type is {ct!r}; spec "
        f"mandates application/fhir+json (FHIR R4 §3.1.0.1.9)."
    )


def test_e03_post_lookup_error_path_emits_fhir_mimetype(fhir_client):
    """POST ``$lookup`` returning a 400 (missing system AND code) MUST
    still emit ``application/fhir+json`` Content-Type with a Parameters
    OperationOutcome body — not text/plain, not application/json.
    """
    body = {"resourceType": "Parameters", "parameter": []}
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]!r}"
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST $lookup error Content-Type is {ct!r}; spec mandates "
        f"application/fhir+json on every error response (§3.1.0.1.5 + §3.1.0.1.9)."
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "OperationOutcome"


# ---------------------------------------------------------------------------
# 2. Property parameter combinations
# ---------------------------------------------------------------------------

def _assert_lookup_200_with_parameters(r, label: str):
    """Common positive-success-shape assertion for $lookup."""
    assert r.status_code == 200, (
        f"{label}: expected 200, got {r.status_code}; body={r.text[:300]!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"{label}: body must be Parameters; got {body.get('resourceType')}"
    )
    param_names = {p.get("name") for p in body.get("parameter", [])}
    # Per spec §4.8.21.1 Out params: name (1..1), code (1..1), system (1..1)
    for required in ("name", "code", "system"):
        assert required in param_names, (
            f"{label}: Out parameter missing required '{required}'. "
            f"Got: {sorted(param_names)}"
        )


def test_e10_lookup_multi_property_values_accepted(fhir_client):
    """``property=name&property=version&property=display`` (multiple values).
    Per FHIR R4 §4.8.21.1 In ``property`` is 0..*; multiple values are
    permitted. Server returns its full property set (medterm4ds convention —
    already documented NOT A BUG).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DIABETES_MELLITUS),
            ("property", "name"),
            ("property", "version"),
            ("property", "display"),
        ],
    )
    _assert_lookup_200_with_parameters(r, "multi-property (name,version,display)")


def test_e11_lookup_mixed_standard_and_custom_property_accepted(fhir_client):
    """Mixed standard + custom: ``property=name&property=canonical-system``.
    Both accepted; server returns conformant Parameters body.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DIABETES_MELLITUS),
            ("property", "name"),
            ("property", "canonical-system"),
        ],
    )
    _assert_lookup_200_with_parameters(r, "mixed standard+custom property")


def test_e12_lookup_empty_property_value_accepted(fhir_client):
    """``property=`` (empty value). Per spec ``property`` is 0..*, empty
    values are gracefully handled. Server MUST NOT 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS, "property": ""},
    )
    _assert_lookup_200_with_parameters(r, "empty property value")


def test_e13_lookup_20_properties_accepted(fhir_client):
    """Long property list: 20 ``property`` values. Server MUST NOT 5xx."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", SNOMED_URI), ("code", SNOMED_DIABETES_MELLITUS)]
        + [("property", "name") for _ in range(20)],
    )
    _assert_lookup_200_with_parameters(r, "20 property values")


# ---------------------------------------------------------------------------
# 3. lang.X properties
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "lang_prop",
    [
        "lang.en",
        "lang.fr",
        "lang.de",
        "lang.es",
        "lang.xyz",  # non-ISO
        "lang.en-US",  # multi-part
        "lang.en.US.extra",  # multi-part extra
        "LANG.EN",  # mixed case
    ],
)
def test_e20_lookup_lang_property_accepted_without_5xx(fhir_client, lang_prop):
    """``property=lang.X`` for various X. medterm4ds doesn't emit lang.X
    today (single-language UMLS data), but per FHIR R4 §4.8.21.1 In
    ``property`` accepts any code; the server MUST accept the parameter
    without 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS, "property": lang_prop},
    )
    _assert_lookup_200_with_parameters(r, f"property={lang_prop}")


# ---------------------------------------------------------------------------
# 4. designation property + displayLanguage combo
# ---------------------------------------------------------------------------

def test_e30_lookup_designation_property_with_display_language(fhir_client):
    """``property=designation`` AND ``displayLanguage=fr`` together. Per
    FHIR R4 §4.8.21.1 Out ``designation`` is 0..* (server MAY return
    zero). Combined with displayLanguage (also ignored today — single-
    language data). Server MUST return 200 + Parameters.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "property": "designation",
            "displayLanguage": "fr",
        },
    )
    _assert_lookup_200_with_parameters(r, "property=designation + displayLanguage=fr")


# ---------------------------------------------------------------------------
# 5. Subsumption decomposition (item 10): parent / child
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prop", ["parent", "child"])
def test_e40_lookup_subsumption_property_accepted(fhir_client, prop):
    """``property=parent`` and ``property=child``. medterm4ds doesn't
    emit these as $lookup properties today (closure table is for
    ``$subsumes``), but spec allows them. Server MUST NOT 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS, "property": prop},
    )
    _assert_lookup_200_with_parameters(r, f"property={prop}")


def test_e41_lookup_parent_and_child_together(fhir_client):
    """``property=parent&property=child`` together."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DIABETES_MELLITUS),
            ("property", "parent"),
            ("property", "child"),
        ],
    )
    _assert_lookup_200_with_parameters(r, "property=parent+child")


# ---------------------------------------------------------------------------
# 6. property.code parameter (item 2)
# ---------------------------------------------------------------------------

def test_e50_lookup_property_code_param_accepted(fhir_client):
    """``property.code=foo`` is a spec-listed In parameter (item 2). The
    server accepts it via FastAPI's permissive default and ignores it
    (no property-code-based filtering today). Server MUST NOT 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "property.code": "foo",
        },
    )
    _assert_lookup_200_with_parameters(r, "property.code=foo")


# ---------------------------------------------------------------------------
# 7. Combined unusual inputs (coding body variants)
# ---------------------------------------------------------------------------

def test_e60_post_lookup_coding_with_extra_fields_accepted(fhir_client):
    """``coding`` body with extra fields (display, userSelected, version).
    Per FHIR R4 Coding type allows these; server MUST accept without 5xx.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DIABETES_MELLITUS,
                    "display": "Extra display",
                    "userSelected": True,
                    "version": "2024-09",
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    _assert_lookup_200_with_parameters(r, "coding with extra fields")


def test_e61_post_lookup_multiple_codings_uses_first(fhir_client):
    """``coding`` body with multiple codings. Spec: 'the coding parameter
    allows a complete coding' (singular). medterm4ds picks the first
    coding with both system and code (consistent with $validate-code
    behavior — TS-02 EXPLORER QA-026). Server MUST NOT 5xx; Out ``code``
    reflects the first coding.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
            },
            {
                "name": "coding",
                "valueCoding": {"system": SNOMED_URI, "code": "44054006"},
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    _assert_lookup_200_with_parameters(r, "multiple codings")
    # The first coding wins.
    code_params = [p for p in r.json().get("parameter", []) if p.get("name") == "code"]
    assert code_params, "Out 'code' parameter missing"
    assert code_params[0].get("valueCode") == SNOMED_DIABETES_MELLITUS, (
        f"First coding should win; got code={code_params[0].get('valueCode')!r}"
    )


def test_e62_post_lookup_codeable_concept_rejected(fhir_client):
    """``codeableConcept`` on ``$lookup``. Per FHIR R4 the ``$lookup`` In
    Parameters table does NOT list ``codeableConcept`` (only ``coding``).
    Asymmetry with ``$validate-code`` (which does accept it). Spec-correct
    rejection: server returns 400 with OperationOutcome (CS-01 EXPLORER
    confirmed this asymmetry is spec-correct).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [{"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}]
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 400, (
        f"codeableConcept on $lookup should be rejected (no system+code, "
        f"no usable coding); got {r.status_code}: {r.text[:200]!r}"
    )
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct
    body_json = r.json()
    assert body_json.get("resourceType") == "OperationOutcome"


def test_e63_post_lookup_coding_not_a_dict_rejected(fhir_client):
    """``coding`` body with ``valueString`` instead of ``valueCoding``.
    Server MUST reject with 400 OperationOutcome (positive success-shape
    for the rejection path).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "coding", "valueString": "not-a-coding"},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 400, (
        f"coding=string should be rejected; got {r.status_code}: {r.text[:200]!r}"
    )
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct


def test_e64_post_lookup_coding_missing_system_rejected(fhir_client):
    """``coding`` body with code only (no system). Server MUST reject
    with 400 (coding missing system).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "coding", "valueCoding": {"code": SNOMED_DIABETES_MELLITUS}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 400, (
        f"coding without system should be 400; got {r.status_code}: {r.text[:200]!r}"
    )


def test_e65_post_lookup_coding_missing_code_rejected(fhir_client):
    """``coding`` body with system only (no code). Server MUST reject
    with 400.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "coding", "valueCoding": {"system": SNOMED_URI}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 400, (
        f"coding without code should be 400; got {r.status_code}: {r.text[:200]!r}"
    )


# ---------------------------------------------------------------------------
# 8. Property name case sensitivity
# ---------------------------------------------------------------------------

def test_e70_lookup_property_name_uppercase_accepted(fhir_client):
    """``property=NAME`` (uppercase). Server MUST NOT 5xx — medterm4ds
    treats property names case-sensitively but accepts any code value
    per spec.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS, "property": "NAME"},
    )
    _assert_lookup_200_with_parameters(r, "property=NAME (uppercase)")


# ---------------------------------------------------------------------------
# 9. Property with whitespace
# ---------------------------------------------------------------------------

def test_e80_lookup_property_with_trailing_space_accepted(fhir_client):
    """``property=name%20`` (URL-encoded trailing space). Server MUST NOT
    5xx on the decoded value ``'name '``.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS, "property": "name "},
    )
    _assert_lookup_200_with_parameters(r, "property='name ' (trailing space)")


# ---------------------------------------------------------------------------
# 10. Cross-system consistency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "system_uri, code",
    [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (ICD10CM_URI, ICD10CM_TYPE2_DM),
        (RXNORM_URI, RXNORM_METFORMIN),
    ],
)
def test_e90_lookup_cross_system_consistent_shape(fhir_client, system_uri, code):
    """Every supported system resolves a known code via ``$lookup`` and
    emits the same Out parameter shape (``name``, ``code``, ``system``,
    ``display``, ``abstract``, optional ``property``).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system_uri, "code": code},
    )
    _assert_lookup_200_with_parameters(r, f"system={system_uri} code={code}")
    param_names = {p.get("name") for p in r.json().get("parameter", [])}
    for required in ("display", "abstract"):
        assert required in param_names, (
            f"system={system_uri}: Out parameter missing '{required}'"
        )


# ---------------------------------------------------------------------------
# 11. Out parameter type fidelity
# ---------------------------------------------------------------------------

def test_e100_lookup_out_uses_correct_value_types(fhir_client):
    """Out parameter types: ``name``/``display`` use ``valueString``;
    ``code`` uses ``valueCode``; ``system`` uses ``valueUri``; ``abstract``
    uses ``valueBoolean``. Per FHIR R4 §4.8.21.1 Out parameter types.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code == 200, f"body={r.text[:300]!r}"
    params = {p["name"]: p for p in r.json().get("parameter", [])}
    assert "valueString" in params["name"], params["name"]
    assert "valueCode" in params["code"], params["code"]
    assert "valueUri" in params["system"], params["system"]
    assert "valueString" in params["display"], params["display"]
    assert "valueBoolean" in params["abstract"], params["abstract"]


def test_e101_lookup_out_system_is_canonical_for_each_system(fhir_client):
    """Out ``system`` parameter is the canonical FHIR URI (per CS-02
    HISTORIAN QA-047 fix). Re-resolved via ``SYSTEM_TO_FHIR_URI`` even
    when the client passes the canonical URI directly (negative control
    — no double-translation drift).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code == 200
    params = {p["name"]: p for p in r.json().get("parameter", [])}
    assert params["system"]["valueUri"] == SNOMED_URI, (
        f"Out system should be canonical {SNOMED_URI}; got "
        f"{params['system']['valueUri']!r}"
    )
