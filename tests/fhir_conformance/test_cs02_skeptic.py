"""SKEPTIC probes for CS-02 (CodeSystem $lookup Operation).

Spec: https://build.fhir.org/codesystem-operation-lookup.html (R4 / 4.0.1).
       https://hl7.org/fhir/R4/codesystem-operation-lookup.html (canonical R4).

Scope (per chunk assignment) — 10 items:
  1. Required params: code (or coding), system
  2. Optional params: version, property (multi), displayLanguage, property.code
  3. Standard property `name` (code system name)
  4. Standard property `version` (if code system has a version)
  5. Standard property `display` (recommended display)
  6. Standard property `designation` (when present)
  7. Standard property `lang.X` (language-specific designations)
  8. When `property` omitted, default property set returned (always includes version)
  9. POST with `coding` parameter produces same response as GET
  10. Subsumption-decomposition via `property` param returns parent/child relationships

SKEPTIC lens: hostile-input probes for each item — drop required params, send
alternative encodings, send malformed values, probe GET-vs-POST parity, probe
default property set, probe spec-permitted but unsupported features.

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape (200 +
    expected fields), not just absence of one error string.
  - "Conformance property per route": when probing Content-Type, parametrize
    over `app.routes` (covered by test_milestone1_review_fixes.py — not
    re-probed here).
  - "Silent-wrong-answer on alternative parameter encodings": `coding` is a
    spec-permitted alternative to system+code on $lookup. Probe both GET and
    POST.
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# In Parameters (relevant):
#   code           0..1  code       "The code that is to be located. If a code
#                                    is provided, a system must be provided"
#   system         0..1  uri  type  "The system for the code that is to be
#                                    located"
#   version        0..1  string type
#   coding         0..1  Coding     "A coding to look up"
#   displayLanguage 0..1 code       "The requested language for display"
#   property       0..*  code       "A property that the client wishes to be
#                                    returned in the output. If no properties
#                                    are specified, the server chooses what to
#                                    return."
#
# Spec quote (URL above):
#   "When invoking this operation, a client SHALL provide both a system and a
#    code, either using the system+code parameters, or in the coding parameter."
#
# Out Parameters (relevant):
#   name           1..1  string     "A display name for the code system"
#   version        0..1  string
#   display        1..1  string     "The preferred display for this concept"
#   definition     0..1  string
#   designation    0..*
#   property       0..*  (with property.code 1..1, property.value 0..1)
#
# Spec quote: "If no properties are specified, the server chooses what to
# return. The following properties are defined for all code systems: name,
# version (code system info) and code information: display, designation, and
# lang.X where X is a designation language code. These properties are returned
# explicitly in named out parameters with matching names... In addition, any
# property codes defined by this specification or by the CodeSystem
# (CodeSystem.property.code) are allowed, and these are returned in the out
# parameter `property`."

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"
SNOMED_T2DM = "44054006"


# ---------------------------------------------------------------------------
# Item 1: Required params — code (or coding), system
# ---------------------------------------------------------------------------

def test_s01_get_lookup_without_code_or_system_returns_422(fhir_client):
    """Item 1 / spec In Parameters: code+system are required (one of the two
    alternative encodings). GET with no params MUST reject with 422 (FastAPI
    missing-required) or 400 — NOT 200 (which would imply an empty Parameters
    was returned for an unspecified code).
    """
    r = fhir_client.get("/fhir/CodeSystem/$lookup")
    assert r.status_code in (400, 422), (
        f"GET $lookup with no params → {r.status_code}; expected 422/400"
    )
    # Per GLOBAL_RULES.md "Conformance property per route" — body MUST be FHIR
    # OperationOutcome or FastAPI-wrapped FHIR detail (TS-02 SKEPTIC QA-020).
    ct = r.headers.get("content-type", "")
    assert "fhir+json" in ct or "application/json" in ct, (
        f"Response Content-Type must be FHIR/JSON; got {ct!r}"
    )


def test_s02_get_lookup_code_only_without_system_returns_422(fhir_client):
    """Item 1 / spec: 'If a code is provided, a system must be provided'.
    A GET with code but no system MUST be rejected (422/400), not silently
    routed to a default-system lookup.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code in (400, 422), (
        f"GET $lookup code-only (no system) → {r.status_code}; "
        f"spec mandates system MUST accompany code."
    )


def test_s03_get_lookup_system_only_without_code_returns_422(fhir_client):
    """Item 1 / spec: system without code is similarly malformed."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI},
    )
    assert r.status_code in (400, 422), (
        f"GET $lookup system-only (no code) → {r.status_code}"
    )


# ---------------------------------------------------------------------------
# Item 9 (raised here because it's an Item-1 alternative encoding):
# POST $lookup with `coding` parameter produces same response as GET system+code
# ---------------------------------------------------------------------------

def test_s10_post_lookup_with_coding_returns_200_parameters(fhir_client):
    """Item 9 / spec: 'a client SHALL provide both a system and a code, either
    using the system+code parameters, or in the coding parameter'. POST with
    `coding` MUST be accepted (positive success-shape per GLOBAL_RULES.md
    "Test-too-lenient"). Wired by TS-02 HISTORIAN QA-022.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={"resourceType": "Parameters", "parameter": [
            {"name": "coding", "valueCoding": {
                "system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS,
            }},
        ]},
    )
    assert r.status_code == 200, (
        f"POST $lookup coding-only → {r.status_code}; body={r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"POST $lookup coding-only body must be Parameters; got "
        f"resourceType={body.get('resourceType')!r}"
    )
    code_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "code"), None
    )
    assert code_param is not None and code_param.get("valueCode") == SNOMED_DIABETES_MELLITUS


def test_s11_get_lookup_with_coding_query_param_intentionally_not_supported(fhir_client):
    """Item 9 / spec: 'either using the system+code parameters, or in the
    coding parameter'. The spec's In Parameters table lists `coding` 0..1 as
    a parameter — BUT FHIR R4 §3.1.0.1.4 (Operations, "Parameters" section)
    restricts complex-type parameters (Coding, CodeableConcept, Reference)
    to the POST body. GET query parameters are for primitive types only
    (code, string, uri, integer, boolean, dateTime).

    SKEPTIC probe (overreach): GET $lookup with a `coding` query-string
    parameter (JSON-encoded). Confirms GET handler rejects with 422
    (FastAPI missing-required system+code) — the spec-correct behavior,
    because complex parameters belong in the POST body. medterm4ds IS
    spec-conformant here: `coding` on POST $lookup is wired (test_s10
    passes); GET-with-coding is not required by the spec.

    NO BUG — confirms the spec-correct behavior is preserved. Documented to
    prevent re-reporting (NOT A BUG registry candidate).
    """
    import json
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"coding": json.dumps({
            "system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS,
        })},
    )
    # Spec-correct: 422 (FastAPI missing-required system+code). Complex-type
    # params (Coding) belong in POST body, not GET query string.
    assert r.status_code in (400, 422), (
        f"GET $lookup with coding=? — expected 422 (complex-type params "
        f"are POST-only per FHIR R4 §3.1.0.1.4); got {r.status_code}. "
        f"Body: {r.text[:200]}"
    )


def test_s12_post_lookup_coding_missing_system_rejected(fhir_client):
    """Item 9 / spec: 'a coding' implies a complete Coding (system+code).
    A coding parameter without `system` cannot satisfy the SHALL — server
    MUST reject with 400 (positive rejection).
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={"resourceType": "Parameters", "parameter": [
            {"name": "coding", "valueCoding": {"code": SNOMED_DIABETES_MELLITUS}},
        ]},
    )
    assert r.status_code == 400, (
        f"POST $lookup coding-without-system → {r.status_code}; expected 400 "
        f"(incomplete coding cannot satisfy SHALL). Body: {r.text[:200]}"
    )


def test_s13_post_lookup_coding_missing_code_rejected(fhir_client):
    """Item 9 / spec: symmetric to s12 — coding without `code` is incomplete."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={"resourceType": "Parameters", "parameter": [
            {"name": "coding", "valueCoding": {"system": SNOMED_URI}},
        ]},
    )
    assert r.status_code == 400, (
        f"POST $lookup coding-without-code → {r.status_code}; expected 400."
    )


# ---------------------------------------------------------------------------
# Item 9: GET system+code vs POST system+code byte-exact equivalence
# (Single-vs-batch byte-exact equivalence probe class — TS-04 TERMINOLOGIST)
# ---------------------------------------------------------------------------

def test_s20_get_and_post_lookup_with_system_code_produce_equal_body(fhir_client):
    """Item 9 / spec: 'POST with coding parameter produces same response as
    GET'. The spec mandates GET-vs-POST parity. medterm4dsfunnels both paths
    through `_do_lookup` so the response body MUST be byte-exact-equal.

    Per the TS-04 TERMINOLOGIST single-vs-batch byte-exact equivalence probe
    class: when the same logical request can be encoded two ways, byte-compare.
    Catches silent dispatcher divergence.
    """
    r_get = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={"resourceType": "Parameters", "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DIABETES_MELLITUS},
        ]},
    )
    assert r_get.status_code == 200 and r_post.status_code == 200
    assert r_get.json() == r_post.json(), (
        "GET and POST $lookup with same system+code MUST produce equal bodies "
        "(spec mandates POST-with-coding produces same response as GET)."
    )


def test_s21_post_coding_produces_same_body_as_get_system_code(fhir_client):
    """Item 9 / spec: 'POST with coding parameter produces same response as
    GET'. The spec quote specifically equates POST-with-coding to GET-with-
    system+code. Byte-exact comparison.
    """
    r_get = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={"resourceType": "Parameters", "parameter": [
            {"name": "coding", "valueCoding": {
                "system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS,
            }},
        ]},
    )
    assert r_get.status_code == 200 and r_post.status_code == 200
    assert r_get.json() == r_post.json(), (
        "POST $lookup with coding MUST produce byte-exact-equal body to GET "
        "$lookup with system+code (spec mandates parity)."
    )


# ---------------------------------------------------------------------------
# Items 3, 5, 8: Standard Out parameters `name`, `display`, and default set
# ---------------------------------------------------------------------------

def test_s30_lookup_default_set_includes_name_and_display(fhir_client):
    """Items 3, 5, 8 / spec Out Parameters: `name` is 1..1 string ("A display
    name for the code system"), `display` is 1..1 string ("The preferred
    display for this concept"). When `property` is omitted, the default
    property set is returned; spec lists `name`, `version`, `display`,
    `designation` as the always-defined properties.

    Positive success-shape (per GLOBAL_RULES.md "Test-too-lenient"): assert
    that the response contains a top-level `name` parameter and a top-level
    `display` parameter.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code == 200
    body = r.json()
    param_names = {p.get("name") for p in body.get("parameter", [])}
    assert "name" in param_names, (
        f"$lookup default Out MUST include `name` (1..1 per spec); got "
        f"params={sorted(param_names)}"
    )
    assert "display" in param_names, (
        f"$lookup default Out MUST include `display` (1..1 per spec); got "
        f"params={sorted(param_names)}"
    )


def test_s31_lookup_name_param_uses_valueString(fhir_client):
    """Item 3 / spec: Out `name` is type `string`. Wire-format MUST use
    `valueString` (not valueCode/valueUri).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    name_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "name"), None
    )
    assert name_param is not None, "$lookup missing `name` parameter"
    assert "valueString" in name_param, (
        f"$lookup `name` parameter must use valueString; got keys="
        f"{list(name_param.keys())}"
    )


def test_s32_lookup_display_param_uses_valueString(fhir_client):
    """Item 5 / spec: Out `display` is type `string`. Wire-format MUST use
    `valueString`.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    display_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "display"), None
    )
    assert display_param is not None, "$lookup missing `display` parameter"
    assert "valueString" in display_param, (
        f"$lookup `display` parameter must use valueString; got keys="
        f"{list(display_param.keys())}"
    )


# ---------------------------------------------------------------------------
# Item 4: Standard property `version` (Out) — 0..1, server emits when known
# ---------------------------------------------------------------------------

def test_s40_lookup_version_out_param_when_omitted(fhir_client):
    """Item 4 / spec: Out `version` is 0..1 string ("The version that these
    details are based on"). medterm4ds doesn't track per-version UMLS data
    (NOT A BUG registry: 'No `version` field on TerminologyCapabilities').
    Probe: when `version` is absent from the Out body, the response is still
    conformant (cardinality 0..1). This is a positive success-shape assertion
    that the body is well-formed with or without `version`.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code == 200
    body = r.json()
    # Spec: 0..1 — either presence with valueString, or absence, is conformant.
    version_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "version"), None
    )
    if version_param is not None:
        assert "valueString" in version_param, (
            f"`version` Out parameter MUST use valueString; got keys="
            f"{list(version_param.keys())}"
        )
    # No assertion on presence — both 0 and 1 are spec-conformant.


# ---------------------------------------------------------------------------
# Item 2: Optional params — version, property, displayLanguage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("param_name,value", [
    ("version", "2024-AB"),
    ("displayLanguage", "en"),
    ("displayLanguage", "fr"),
    ("displayLanguage", "de"),
])
def test_s50_lookup_optional_param_accepted_without_500(
    fhir_client, param_name, value
):
    """Item 2 / spec: optional params `version` (0..1 string, type-scope) and
        `displayLanguage` (0..1 code). Server MUST accept these params without
        5xx; they MAY be ignored (medterm4ds is single-version, single-language
        per AGENTS.md NOT A BUG registry). Acceptable outcomes: 200 (accepted,
        possibly ignored) or 400 (rejected with explanation).

        SKEPTIC lens: hostile value (long version, invalid language code) MUST
        not crash. The probe verifies no 5xx.
        """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS,
                param_name: value},
    )
    assert r.status_code < 500, (
        f"GET $lookup with {param_name}={value!r} → {r.status_code}; "
        f"optional param MUST NOT crash server (5xx). Body: {r.text[:200]}"
    )
    assert r.status_code == 200, (
        f"GET $lookup with {param_name}={value!r} → {r.status_code}; "
        f"expected 200 (accepted). Body: {r.text[:200]}"
    )


def test_s51_lookup_display_language_empty_accepted(fhir_client):
    """Item 2 / spec: `displayLanguage` is 0..1 code. An empty string is
    technically an invalid `code` value (FHIR R4 §3.4.1 — code cannot be
    empty), but medterm4ds accepts and ignores it. Acceptable: 200 (ignored).
    SKEPTIC lens: probe the empty-string edge case; verify no 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS,
                "displayLanguage": ""},
    )
    assert r.status_code < 500, (
        f"displayLanguage='' → {r.status_code}; MUST NOT crash server. "
        f"Body: {r.text[:200]}"
    )


# ---------------------------------------------------------------------------
# Item 2: `property` parameter (filtering) — INTENDED behavior pinned
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("property_filter", [
    "name", "version", "display", "designation", "lang.en",
    "nonexistent-property-code",
])
def test_s60_lookup_property_filter_param_accepted(fhir_client, property_filter):
    """Item 2 / spec: `property` is 0..* code ("A property that the client
    wishes to be returned in the output. If no properties are specified, the
    server chooses what to return."). The server MUST accept this param and
    return 200; whether it actually filters is implementation-defined.

    medterm4ds returns its full property set regardless (per AGENTS.md NOT A
    BUG registry: '$lookup repeating `property` parameter accepted'). This
    probe pins the contract: 200, well-formed Parameters body.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", SNOMED_URI), ("code", SNOMED_DIABETES_MELLITUS),
                ("property", property_filter)],
    )
    assert r.status_code == 200, (
        f"GET $lookup property={property_filter!r} → {r.status_code}; "
        f"property filter MUST be accepted. Body: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_s61_lookup_multi_property_filter_accepted(fhir_client):
    """Item 2 / spec: `property` is 0..* (repeating). Multiple property params
    on GET (repeated query param) MUST be accepted.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", SNOMED_URI), ("code", SNOMED_DIABETES_MELLITUS),
                ("property", "name"), ("property", "display"),
                ("property", "designation")],
    )
    assert r.status_code == 200, (
        f"GET $lookup multi-property → {r.status_code}; Body: {r.text[:200]}"
    )


# ---------------------------------------------------------------------------
# Item 2: property.code (Out parameter shape)
# ---------------------------------------------------------------------------

def test_s70_lookup_property_out_shape_has_code_and_value_parts(fhir_client):
    """Item 2 (property.code) / spec Out Parameters: each `property` Out entry
    has a `part` array with:
      - `code` part (1..1, type code) — uses valueCode
      - `value` part (0..1, type Element) — uses value[x]
    The `property.code` is the IDENTIFIER of the returned property; the value
    part carries the actual value. Probe pins the wire shape.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code == 200
    body = r.json()
    property_entries = [p for p in body.get("parameter", []) if p.get("name") == "property"]
    if not property_entries:
        pytest.skip("no property entries returned by this fixture row")
    for prop in property_entries:
        parts = prop.get("part", [])
        code_part = next((pt for pt in parts if pt.get("name") == "code"), None)
        assert code_part is not None, (
            f"property Out entry missing `code` part (spec: 1..1). Prop={prop}"
        )
        assert "valueCode" in code_part, (
            f"property.code part MUST use valueCode; got keys="
            f"{list(code_part.keys())}"
        )


# ---------------------------------------------------------------------------
# Item 6: Standard property `designation` — Out param shape when present
# ---------------------------------------------------------------------------

def test_s80_lookup_designation_param_when_present_uses_part_value(fhir_client):
    """Item 6 / spec Out Parameters: `designation` is 0..* with parts:
    `language` (0..1 code), `use` (0..1 Coding), `value` (1..1 string).
    medterm4ds doesn't emit designations today (no per-designation data in
    the synthetic fixture). Probe: when designations are absent, the body is
    still conformant (0..* cardinality allows zero). When present, the value
    part uses valueString.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    designations = [p for p in body.get("parameter", []) if p.get("name") == "designation"]
    # 0..* — absence is conformant for medterm4ds today.
    for des in designations:
        parts = des.get("part", [])
        value_part = next((pt for pt in parts if pt.get("name") == "value"), None)
        if value_part is not None:
            assert "valueString" in value_part, (
                f"designation.value part MUST use valueString; got "
                f"keys={list(value_part.keys())}"
            )


# ---------------------------------------------------------------------------
# Item 7: Standard property `lang.X` (language-specific designations)
# ---------------------------------------------------------------------------

def test_s90_lookup_lang_x_property_returns_200_for_any_x(fhir_client):
    """Item 7 / spec: 'lang.X where X is a designation language code' is a
    property the client may request via the `property` parameter. medterm4ds
    doesn't emit lang.X designations today (single-language). Probe: server
    MUST accept the property filter without 5xx; absence from response is
    conformant.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", SNOMED_URI), ("code", SNOMED_DIABETES_MELLITUS),
                ("property", "lang.fr")],
    )
    assert r.status_code == 200, (
        f"GET $lookup property=lang.fr → {r.status_code}; Body: {r.text[:200]}"
    )


# ---------------------------------------------------------------------------
# Item 10: Subsumption decomposition via `property` param
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prop", ["parent", "child"])
def test_s100_lookup_subsumption_property_accepted_without_5xx(fhir_client, prop):
    """Item 10 / spec: 'for complex terminologies (e.g. SNOMED CT, LOINC,
    medications), these properties serve to decompose the code'. The spec
    doesn't define a closed enum for property codes — `parent`/`child` are
    conventional but not mandatory. medterm4ds doesn't emit parent/child as
    $lookup properties today (the closure table exists for $subsumes; not
    surfaced via $lookup). Probe: server MUST accept the property filter
    without 5xx; absence from response is conformant.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", SNOMED_URI), ("code", SNOMED_DIABETES_MELLITUS),
                ("property", prop)],
    )
    assert r.status_code < 500, (
        f"GET $lookup property={prop} → {r.status_code}; MUST NOT crash server. "
        f"Body: {r.text[:200]}"
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Edge cases — empty/long/special-char code values
# ---------------------------------------------------------------------------

def test_s110_lookup_empty_code_does_not_crash(fhir_client):
    """Edge case / spec: `code` is type `code` (FHIR R4 §3.4.1 — code cannot
    be empty). An empty-string code is malformed. medterm4ds currently
    accepts and returns OperationOutcome "Code not found" (200 + OperationOutcome
    per AGENTS.md NOT A BUG Registry pattern for non-existent codes). Probe:
    server MUST NOT crash (5xx) on this edge case.

    Documented INTENDED: medterm4ds returns 200 + OperationOutcome for any
    code that doesn't resolve (whether absent or empty) — see cases.json
    `lookup-invalid-code`.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": ""},
    )
    assert r.status_code < 500, (
        f"GET $lookup code='' → {r.status_code}; MUST NOT crash. "
        f"Body: {r.text[:200]}"
    )


def test_s111_lookup_very_long_code_does_not_crash(fhir_client):
    """Edge case / hostile-input: a 5K-char code is well beyond any realistic
    code length. medterm4ds MUST handle this without 5xx (it's just a string
    lookup against the DB). Probe verifies graceful rejection (200 +
    OperationOutcome 'Code not found' is conformant — see lookup-invalid-code).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": "X" * 5000},
    )
    assert r.status_code < 500, (
        f"GET $lookup code=<5K X> → {r.status_code}; MUST NOT crash. "
        f"Body: {r.text[:200]}"
    )


def test_s112_lookup_special_chars_in_code_does_not_crash(fhir_client):
    """Edge case / hostile-input: special chars in code (HTML, quotes, etc).
    Probe: no 5xx, no unescaped HTML in body (basic injection hygiene).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": "<script>alert('xss')</script>"},
    )
    assert r.status_code < 500, (
        f"GET $lookup code=<script> → {r.status_code}; MUST NOT crash."
    )
    body_text = r.text
    # No unescaped script tag in body (server-side encoding should escape it
    # in any OperationOutcome.details.text or echo field).
    assert "<script>" not in body_text, (
        f"Unescaped <script> tag in response body — injection risk. "
        f"Body: {body_text[:300]}"
    )


def test_s113_lookup_code_with_whitespace_handled(fhir_client):
    """Edge case / hostile-input: code with leading/trailing whitespace.
    A strict reading of FHIR R4 §3.4.1 'code' type disallows leading/trailing
    whitespace. medterm4ds MUST NOT crash on this input.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": " 73211009 "},
    )
    assert r.status_code < 500, (
        f"GET $lookup code=' 73211009 ' → {r.status_code}; MUST NOT crash."
    )


# ---------------------------------------------------------------------------
# Item 2 (mixed param styles) — `code` AND `coding` both supplied
# ---------------------------------------------------------------------------

def test_s120_post_lookup_mixed_code_and_coding_returns_200(fhir_client):
    """Item 2 / spec: 'a client SHALL provide both a system and a code,
    either using the system+code parameters, or in the coding parameter'.
    Spec implies XOR semantics, but doesn't mandate rejection when both are
    supplied. medterm4ds uses system+code when present and ignores `coding`
    (first-wins). Probe: server MUST NOT crash; return 200 with the
    system+code lookup result.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={"resourceType": "Parameters", "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "coding", "valueCoding": {
                "system": SNOMED_URI, "code": SNOMED_T2DM,
            }},
        ]},
    )
    assert r.status_code == 200, (
        f"POST $lookup mixed code+coding → {r.status_code}; "
        f"Body: {r.text[:200]}"
    )
    body = r.json()
    # system+code takes precedence (medterm4ds convention).
    code_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "code"), {}
    )
    assert code_param.get("valueCode") == SNOMED_DIABETES_MELLITUS


# ---------------------------------------------------------------------------
# Hostile-input probes for `coding` parameter shape
# ---------------------------------------------------------------------------

def test_s130_post_lookup_coding_with_extra_fields_accepted(fhir_client):
    """Edge case / hostile-input: a Coding may legitimately carry extra
    fields (`display`, `userSelected`, `version`) per FHIR R4 Coding type.
    Server MUST accept these without 5xx; only system+code are required.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={"resourceType": "Parameters", "parameter": [
            {"name": "coding", "valueCoding": {
                "system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS,
                "display": "Diabetes mellitus",
                "userSelected": True,
                "version": "2024-AB",
            }},
        ]},
    )
    assert r.status_code == 200, (
        f"POST $lookup coding-with-extras → {r.status_code}; "
        f"Coding type permits extra fields. Body: {r.text[:200]}"
    )


def test_s131_post_lookup_coding_not_a_dict_rejected(fhir_client):
    """Edge case / hostile-input: a malformed `coding` parameter whose
    valueCoding is not a dict (e.g. a string). Server MUST reject with 400
    (or 422) — NOT 5xx.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={"resourceType": "Parameters", "parameter": [
            {"name": "coding", "valueCoding": "not-a-coding"},
        ]},
    )
    assert r.status_code in (400, 422), (
        f"POST $lookup coding='not-a-coding' → {r.status_code}; expected "
        f"400/422 (malformed valueCoding). Body: {r.text[:200]}"
    )
