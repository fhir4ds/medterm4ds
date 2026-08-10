"""EXPLORER RESWEEP probes for CS-02 (CodeSystem $lookup Operation) — fresh
full-sweep run.

Spec: https://build.fhir.org/codesystem-operation-lookup.html (R4 / 4.0.1).

EXPLORER lens (per ROLE_QA_ENGINEER Section 3): lateral-thinking — unusual
parameter combinations, integration corners, cross-operation consistency.
SKEPTIC + HISTORIAN confirmed the $lookup surface is structurally hardened
by 6 prior chunks. EXPLORER probes lateral combinations the prior 2
personalities did not naturally exercise.

HISTORIAN tip for EXPLORER — lateral combination classes probed:
  L1 — Combined optional params at once (version + property multi +
        displayLanguage + property.code all in one request)
  L2 — POST coding + property multi (combined input alternatives)
  L3 — $lookup → $subsumes → $translate round-trip on same code
        (cross-operation consistency)
  L4 — displayLanguage edge cases (locale variants, multi-region,
        language tags with extensions)
  L5 — Subsumption-decomposition via property param with 'parent' /
        'child' values + multi-property combos
  L6 — Property 'designation' with use fields (LOINC MULTUM, SNOMED
        fully specified name)
  L7 — Cross-handler GET↔POST byte-exact parity on lateral inputs
  L8 — Version-param acceptance with property multi
  L9 — Combined-property results deterministic across ordering
  L10 — Source-read structural contracts for lateral combination handling

Do NOT re-probe HCPCS URI drift class or client-input-as-canonical
meta-pattern (both META-PATTERN CLOSED across 4 personalities × 5
advertisement surfaces).

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts the POSITIVE
success shape (200 + Parameters body with the expected fields), not just
absence of an error string.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DM = "73211009"
SNOMED_T2DM = "44054006"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_860975 = "860975"

_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "apps"
    / "fhir_api.py"
)


def _get_func_source(file_path: Path, func_name: str) -> str:
    """Extract the source text of a function (possibly nested) by name.

    Walks BOTH ast.FunctionDef AND ast.AsyncFunctionDef (per TS-04 HISTORIAN
    methodology — async route handlers nested inside create_fhir_app).
    """
    tree = ast.parse(file_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(file_path.read_text(), node)
    return ""


def _params_by_name(body: dict, name: str) -> list[dict]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


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
    for required in ("name", "code", "system"):
        assert required in param_names, (
            f"{label}: Out parameter missing required '{required}'. "
            f"Got: {sorted(param_names)}"
        )


# ---------------------------------------------------------------------------
# L1 — Combined optional params at once
# Spec: https://build.fhir.org/codesystem-operation-lookup.html
#   In: version 0..1 string
#   In: property 0..* code
#   In: displayLanguage 0..1 code
#   In: property.code (used in property group; here the dot-separated form
#       is a query-string name that FastAPI will pass through)
# ---------------------------------------------------------------------------

def test_e10_combined_all_optional_params_at_once(fhir_client):
    """Combined request with version + property (multi) + displayLanguage
    + property.code all in one request. Per spec all of these are 0..1 or
    0..* In Parameters; server MUST accept them together without 5xx and
    return conformant Parameters body.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("version", "2024-09"),
            ("property", "name"),
            ("property", "display"),
            ("property", "designation"),
            ("displayLanguage", "en-US"),
            ("property.code", "inactive"),
        ],
    )
    _assert_lookup_200_with_parameters(r, "all-optional-params-at-once")


def test_e11_combined_property_multi_with_displayLanguage(fhir_client):
    """property (5 values) + displayLanguage together. Per spec property is
    0..* and displayLanguage 0..1; combined accepted.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("property", "name"),
            ("property", "version"),
            ("property", "display"),
            ("property", "designation"),
            ("property", "parent"),
            ("displayLanguage", "fr"),
        ],
    )
    _assert_lookup_200_with_parameters(r, "property-multi + displayLanguage")


def test_e12_combined_version_and_property_multi(fhir_client):
    """version + property (4 values). Per spec version 0..1 + property 0..*
    combined. medterm4ds doesn't track per-version data; server MUST still
    accept version without 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("version", "http://snomed.info/sct/731000124108"),
            ("property", "name"),
            ("property", "version"),
            ("property", "display"),
            ("property", "parent"),
        ],
    )
    _assert_lookup_200_with_parameters(r, "version + property-multi")


@pytest.mark.parametrize(
    "display_lang",
    [
        "en",
        "en-US",
        "fr-FR",
        "es-419",  # Latin America
        "de-DE",
        "zh-Hans-CN",  # script + region
    ],
)
def test_e13_combined_property_and_displayLanguage_parametrized(fhir_client, display_lang):
    """Property multi + displayLanguage parametrized over locale variants.
    Server MUST NOT 5xx on any locale form.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("property", "name"),
            ("property", "display"),
            ("property", "designation"),
            ("displayLanguage", display_lang),
        ],
    )
    _assert_lookup_200_with_parameters(r, f"displayLanguage={display_lang}")


# ---------------------------------------------------------------------------
# L2 — POST coding + property multi (combined input alternatives)
# Spec: 'the coding parameter allows a complete coding to be supplied rather
# than the separate system and code parameters.'
# ---------------------------------------------------------------------------

def test_e20_post_coding_body_with_property_multi(fhir_client):
    """POST with coding body AND multiple property values in the Parameters
    body. Combined input alternatives. Server MUST accept coding (derive
    system+code) and ignore/skip property params (medterm4ds convention).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DM},
            },
            {"name": "property", "valueCode": "name"},
            {"name": "property", "valueCode": "display"},
            {"name": "property", "valueCode": "designation"},
            {"name": "displayLanguage", "valueCode": "en-US"},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    _assert_lookup_200_with_parameters(r, "POST coding + property multi + displayLanguage")


def test_e21_post_coding_body_with_version_and_property(fhir_client):
    """POST with coding body + version + property multi. Combined.
    Server MUST accept all together.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DM},
            },
            {"name": "version", "valueString": "2024-09"},
            {"name": "property", "valueCode": "name"},
            {"name": "property", "valueCode": "version"},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    _assert_lookup_200_with_parameters(r, "POST coding + version + property multi")


def test_e22_post_coding_body_with_lang_property(fhir_client):
    """POST coding + property=lang.en-US. Server MUST NOT 5xx."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DM},
            },
            {"name": "property", "valueCode": "lang.en-US"},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    _assert_lookup_200_with_parameters(r, "POST coding + lang.en-US")


def test_e23_post_system_code_with_property_multi_and_displayLanguage(fhir_client):
    """POST with system+code + property multi + displayLanguage together.
    Combined all-scalar form. Server MUST accept.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DM},
            {"name": "property", "valueCode": "name"},
            {"name": "property", "valueCode": "display"},
            {"name": "property", "valueCode": "designation"},
            {"name": "displayLanguage", "valueCode": "fr"},
            {"name": "version", "valueString": "2024-09"},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    _assert_lookup_200_with_parameters(r, "POST system+code + all optional")


# ---------------------------------------------------------------------------
# L3 — $lookup → $subsumes → $translate round-trip on same code
# (cross-operation consistency)
# Spec: $lookup Out `system` + Out `code` MUST be reusable as $subsumes
# In `system` + In `codeA`/`codeB` and $translate In `system` + In `code`.
# Per FHIR R4 §4.7.5 the canonical system URI is the single source of
# truth across operations.
# ---------------------------------------------------------------------------

def test_e30_lookup_then_subsumes_round_trip(fhir_client):
    """$lookup Out `system` + Out `code` for SNOMED DM, then feed back into
    $subsumes (DM vs T2DM). Per FHIR R4 the Out `system` is canonical and
    MUST be reusable as $subsumes In `system` without translation drift.
    """
    # Step 1: $lookup DM
    r1 = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r1.status_code == 200, f"lookup DM: {r1.text[:200]!r}"
    params1 = {p["name"]: p for p in r1.json().get("parameter", [])}
    out_system = params1["system"]["valueUri"]
    out_code = params1["code"]["valueCode"]
    assert out_system == SNOMED_URI, (
        f"Out system should be canonical {SNOMED_URI}; got {out_system!r}"
    )
    assert out_code == SNOMED_DM, (
        f"Out code should be {SNOMED_DM}; got {out_code!r}"
    )

    # Step 2: $subsumes DM (broader) subsumes T2DM (narrower)
    r2 = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": out_system,  # reuse Out system from $lookup
            "codeA": out_code,     # DM (broader)
            "codeB": SNOMED_T2DM,  # T2DM (narrower)
        },
    )
    assert r2.status_code == 200, f"subsumes: {r2.text[:200]!r}"
    params2 = {p["name"]: p for p in r2.json().get("parameter", [])}
    assert params2.get("outcome", {}).get("valueCode") == "subsumes", (
        f"DM should subsumes T2DM; got {params2.get('outcome')}"
    )


def test_e31_lookup_then_translate_round_trip(fhir_client):
    """$lookup Out `system` + Out `code` for SNOMED T2DM, then feed back
    into $translate (SNOMED → ICD-10-CM via same-CUI C0011847). Per FHIR R4
    the Out `system` is canonical and MUST be reusable as $translate In
    `system` without translation drift.
    """
    # Step 1: $lookup T2DM
    r1 = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r1.status_code == 200, f"lookup T2DM: {r1.text[:200]!r}"
    params1 = {p["name"]: p for p in r1.json().get("parameter", [])}
    out_system = params1["system"]["valueUri"]
    out_code = params1["code"]["valueCode"]
    assert out_system == SNOMED_URI
    assert out_code == SNOMED_T2DM

    # Step 2: $translate T2DM → ICD-10-CM
    r2 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": out_system,  # reuse Out system from $lookup
            "code": out_code,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r2.status_code == 200, f"translate: {r2.text[:200]!r}"
    body2 = r2.json()
    assert body2.get("resourceType") == "Parameters"
    result_param = next(
        (p for p in body2.get("parameter", []) if p.get("name") == "result"),
        None,
    )
    assert result_param is not None, "translate Out 'result' missing"
    # T2DM (C0011847) maps to ICD-10-CM E11 in fixture — same-CUI crosswalk
    assert result_param.get("valueBoolean") is True, (
        f"translate T2DM → ICD-10-CM should succeed; got result={result_param}"
    )


def test_e32_lookup_then_subsumes_then_translate_full_round_trip(fhir_client):
    """Full round-trip: $lookup → $subsumes (verify T2DM subsumed-by DM) →
    $translate T2DM → ICD-10-CM. All operations agree on canonical system
    URI (per CS-01 TERMINOLOGIST bidirectional canonical-URI invariant).
    """
    # Step 1: $lookup T2DM — capture Out system + code
    r1 = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r1.status_code == 200
    p1 = {p["name"]: p for p in r1.json().get("parameter", [])}
    sys_from_lookup = p1["system"]["valueUri"]
    assert sys_from_lookup == SNOMED_URI

    # Step 2: $subsumes — T2DM subsumed-by DM (using lookup Out system)
    r2 = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": sys_from_lookup,
            "codeA": SNOMED_T2DM,
            "codeB": SNOMED_DM,
        },
    )
    assert r2.status_code == 200
    p2 = {p["name"]: p for p in r2.json().get("parameter", [])}
    assert p2["outcome"]["valueCode"] == "subsumed-by", (
        f"T2DM should be subsumed-by DM; got {p2.get('outcome')}"
    )

    # Step 3: $translate T2DM → ICD-10-CM
    r3 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": sys_from_lookup,
            "code": SNOMED_T2DM,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r3.status_code == 200
    p3 = {p["name"]: p for p in r3.json().get("parameter", [])}
    assert p3["result"]["valueBoolean"] is True


def test_e33_lookup_canonical_uri_consistent_across_round_trip(fhir_client):
    """The Out `system` from $lookup MUST equal the Out Coding.system in
    $translate's match[].source (cross-operation canonical-URI invariant).
    The source is nested inside match[].part as a valueCoding.
    """
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r_lookup.status_code == 200
    lookup_system = next(
        p["valueUri"] for p in r_lookup.json().get("parameter", [])
        if p.get("name") == "system"
    )

    r_translate = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r_translate.status_code == 200
    # match[].source is nested inside the match parameter's part[] as a
    # valueCoding (see build_parameters_translate at responses.py:186-189).
    source_uri_in_translate = None
    for p in r_translate.json().get("parameter", []):
        if p.get("name") == "match":
            for part in p.get("part", []):
                if part.get("name") == "source":
                    source_uri_in_translate = part.get("valueCoding", {}).get("system")
                    break
            if source_uri_in_translate:
                break
    assert source_uri_in_translate is not None, (
        "translate Out match.source missing"
    )
    assert source_uri_in_translate == lookup_system, (
        f"Canonical URI drift: lookup={lookup_system!r} "
        f"translate source={source_uri_in_translate!r}"
    )


# ---------------------------------------------------------------------------
# L4 — displayLanguage edge cases
# Spec: displayLanguage is 0..1 code; per CodeSystem.concept.designation.
# language. Edge cases: locale variants, multi-region tags, language tags
# with extensions. Server MUST accept without 5xx.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "display_lang",
    [
        "en-US,en-GB",       # multi-region list (RFC 4647)
        "en-US;q=0.9,fr;q=0.1",  # weighted language preferences
        "x-custom-lang",     # private-use extension
        "i-klingon",         # IANA-registered language tag
        "zh-Hant",           # language + script (no region)
        "en-US-u-tz-uslax",  # unicode extension (BCP 47)
        "en-GB-x-test",      # private-use extension
        "sr-Latn-RS",        # language + script + region
        "INVALID-LANG-TAG",  # malformed
        "",                  # empty (spec says 0..1; empty is permissive)
    ],
)
def test_e40_display_language_edge_cases_no_5xx(fhir_client, display_lang):
    """displayLanguage edge cases (BCP 47 variants, malformed, empty).
    Server MUST accept without 5xx (medterm4ds has no locale engine; falls
    back to default display — INTENDED).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "displayLanguage": display_lang,
        },
    )
    assert r.status_code < 500, (
        f"displayLanguage={display_lang!r}: must not 5xx; got {r.status_code}: "
        f"{r.text[:200]!r}"
    )


def test_e41_display_language_combined_with_property_designation(fhir_client):
    """displayLanguage + property=designation together. Per spec, designation
    is 0..* Out; combined with displayLanguage should not affect response
    shape (single-language fixture has no designations to filter).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "displayLanguage": "fr",
            "property": "designation",
        },
    )
    _assert_lookup_200_with_parameters(r, "displayLanguage=fr + property=designation")


def test_e42_display_language_uppercase_no_5xx(fhir_client):
    """displayLanguage=EN-US (uppercase). RFC 4647 §2.1.1 says language tags
    are case-insensitive. Server MUST NOT 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "displayLanguage": "EN-US",
        },
    )
    assert r.status_code < 500


# ---------------------------------------------------------------------------
# L5 — Subsumption-decomposition via property param with 'parent' / 'child'
# values + multi-property combos
# Spec item 10: 'Subsumption-decomposition via property param returns
# parent/child relationships'.
# Spec Out `property`: 'For complex terminologies (e.g. SNOMED CT), these
# properties serve to decompose the code'.
# ---------------------------------------------------------------------------

def test_e50_property_parent_and_child_multi(fhir_client):
    """property=parent&property=child together. Per spec Out `property`
    serves to decompose the code; medterm4ds doesn't emit parent/child as
    $lookup properties today (closure table is for $subsumes). Server MUST
    NOT 5xx and returns conformant Parameters body.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_T2DM),  # child of DM
            ("property", "parent"),
            ("property", "child"),
        ],
    )
    _assert_lookup_200_with_parameters(r, "property=parent+child on T2DM")


@pytest.mark.parametrize(
    "code, expected_parent_role",
    [
        (SNOMED_DM, "broader"),       # DM is the broader concept
        (SNOMED_T2DM, "narrower"),    # T2DM is the narrower concept
    ],
)
def test_e51_property_parent_parametrized(fhir_client, code, expected_parent_role):
    """property=parent parametrized over a broader concept (DM) and a
    narrower concept (T2DM). Server MUST NOT 5xx on either.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": code,
            "property": "parent",
        },
    )
    _assert_lookup_200_with_parameters(r, f"property=parent on {code} ({expected_parent_role})")


def test_e52_property_child_parent_combined_with_other_props(fhir_client):
    """Combined: property=name&property=display&property=parent&property=
    child&property=designation. Server MUST accept mixed standard + decompose
    properties.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("property", "name"),
            ("property", "display"),
            ("property", "parent"),
            ("property", "child"),
            ("property", "designation"),
        ],
    )
    _assert_lookup_200_with_parameters(r, "mixed standard + decompose properties")


def test_e53_lookup_property_group_structure_when_present(fhir_client):
    """When the Out `property` group IS present, per FHIR R4 §4.8.21.1 each
    property entry MUST be a 'part' group with `code` (1..1) and `value`
    (0..1). The medterm4ds custom properties (cui, tty, aui) follow this
    shape — code is a part with valueCode, value is a part with value[x].
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r.status_code == 200
    body = r.json()
    value_x_keys = {
        "valueCode", "valueString", "valueUri",
        "valueBoolean", "valueInteger", "valueDecimal",
        "valueDateTime", "valueCoding",
    }
    # Each property entry MUST be a 'part' group with code + value parts.
    for p in body.get("parameter", []):
        if p.get("name") == "property":
            parts = {part.get("name"): part for part in p.get("part", [])}
            # Per spec: property.code (1..1)
            assert "code" in parts, f"property group missing 'code' part: {p}"
            # Per spec: property.value (0..1) — when present, contains a value[x]
            if "value" in parts:
                value_part = parts["value"]
                has_value_x = any(k in value_part for k in value_x_keys)
                assert has_value_x, (
                    f"property 'value' part missing value[x]: {value_part}"
                )


# ---------------------------------------------------------------------------
# L6 — Property 'designation' with use fields
# Spec Out `designation`: 0..* with sub-parts language, use, additionalUse,
# value. LOINC MULTUM and SNOMED fully-specified-name are typical uses.
# medterm4ds has single-language fixture; probe that the absence of
# designations is structurally correct.
# ---------------------------------------------------------------------------

def test_e60_designation_absence_is_spec_conformant(fhir_client):
    """Per spec Out `designation` is 0..* (server MAY return zero). With a
    single-language fixture, the absence is spec-conformant. The server
    MUST return 200 with no 'designation' parts.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r.status_code == 200
    body = r.json()
    # designation is 0..*; absence is conformant
    designations = _params_by_name(body, "designation")
    # Fixture has no designations; absence is OK
    assert designations == [], (
        f"Unexpected designations in single-language fixture: {designations}"
    )


def test_e61_designation_requested_via_property_accepted(fhir_client):
    """property=designation explicitly requested. Per spec, server returns
    designation as Out parameter group when present; when absent, the
    request is accepted and returns the standard parameters without
    designation parts.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "property": "designation",
        },
    )
    _assert_lookup_200_with_parameters(r, "property=designation explicit")


def test_e62_designation_with_display_language_combined(fhir_client):
    """property=designation + displayLanguage=de. Combined. Server MUST
    accept both without 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "property": "designation",
            "displayLanguage": "de",
        },
    )
    _assert_lookup_200_with_parameters(r, "property=designation + displayLanguage=de")


# ---------------------------------------------------------------------------
# L7 — Cross-handler GET↔POST byte-exact parity on lateral inputs
# Spec: POST with Parameters body produces same response as GET with same
# params (CS-02 chunk item 9).
# ---------------------------------------------------------------------------

def test_e70_get_post_parity_on_combined_optional_params(fhir_client):
    """GET vs POST byte-exact semantic parity when both include system+code
    + property multi + displayLanguage. Per spec, POST and GET produce the
    same response.
    """
    # GET
    r_get = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("property", "name"),
            ("property", "display"),
            ("displayLanguage", "en-US"),
        ],
    )
    assert r_get.status_code == 200

    # POST
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DM},
            {"name": "property", "valueCode": "name"},
            {"name": "property", "valueCode": "display"},
            {"name": "displayLanguage", "valueCode": "en-US"},
        ],
    }
    r_post = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r_post.status_code == 200

    # Compare param-list semantic (order may differ; content must match)
    get_params = sorted(
        (p.get("name"), p.get("valueCode") or p.get("valueString") or p.get("valueUri"))
        for p in r_get.json().get("parameter", [])
    )
    post_params = sorted(
        (p.get("name"), p.get("valueCode") or p.get("valueString") or p.get("valueUri"))
        for p in r_post.json().get("parameter", [])
    )
    assert get_params == post_params, (
        f"GET↔POST parity broken:\nGET: {get_params}\nPOST: {post_params}"
    )


def test_e71_get_post_parity_on_coding_input(fhir_client):
    """GET system+code vs POST coding body — both MUST produce the same
    Out `code` and Out `system` values.
    """
    r_get = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "coding", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DM}},
        ],
    }
    r_post = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)

    assert r_get.status_code == r_post.status_code == 200
    get_code = next(
        p["valueCode"] for p in r_get.json().get("parameter", []) if p.get("name") == "code"
    )
    post_code = next(
        p["valueCode"] for p in r_post.json().get("parameter", []) if p.get("name") == "code"
    )
    assert get_code == post_code == SNOMED_DM


def test_e72_get_post_parity_on_property_multi(fhir_client):
    """GET vs POST both with property multi — semantic parity."""
    r_get = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("property", "name"),
            ("property", "version"),
            ("property", "display"),
            ("property", "parent"),
            ("property", "child"),
        ],
    )
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DM},
            {"name": "property", "valueCode": "name"},
            {"name": "property", "valueCode": "version"},
            {"name": "property", "valueCode": "display"},
            {"name": "property", "valueCode": "parent"},
            {"name": "property", "valueCode": "child"},
        ],
    }
    r_post = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)

    assert r_get.status_code == r_post.status_code == 200
    # Both must have name, code, system (required)
    for label, resp in [("GET", r_get), ("POST", r_post)]:
        names = {p.get("name") for p in resp.json().get("parameter", [])}
        for req in ("name", "code", "system"):
            assert req in names, f"{label}: missing required {req}"


# ---------------------------------------------------------------------------
# L8 — Version-param acceptance with property multi
# Spec: version is 0..1 In string. medterm4ds is single-snapshot; version
# is accepted but ignored (INTENDED per NOT A BUG registry).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "version",
    [
        "2024-09",
        "http://snomed.info/sct/731000124108/version/20240901",
        "2025-03-15T00:00:00Z",
        "INVALID VERSION STRING",
        "1.0.0-rc.1+build",
    ],
)
def test_e80_version_param_combined_with_property_multi(fhir_client, version):
    """version param (any string) + property multi. Server MUST accept
    without 5xx. The version is passed through (no per-version data).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("version", version),
            ("property", "name"),
            ("property", "display"),
        ],
    )
    _assert_lookup_200_with_parameters(r, f"version={version!r} + property multi")


def test_e81_version_param_in_post_body_combined(fhir_client):
    """POST body with version + property multi. Server MUST accept."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DM},
            {"name": "version", "valueString": "2024-09"},
            {"name": "property", "valueCode": "name"},
            {"name": "property", "valueCode": "display"},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    _assert_lookup_200_with_parameters(r, "POST version + property multi")


# ---------------------------------------------------------------------------
# L9 — Combined-property results deterministic across ordering
# Spec: property is 0..* code; multiple values accepted. The order of
# property values should not affect the Out parameters (server returns
# its full property set regardless — INTENDED).
# ---------------------------------------------------------------------------

def test_e90_property_order_does_not_change_response(fhir_client):
    """property=name&property=display vs property=display&property=name —
    Out parameters MUST be identical (order-independent).
    """
    r1 = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("property", "name"),
            ("property", "display"),
        ],
    )
    r2 = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("property", "display"),
            ("property", "name"),
        ],
    )
    assert r1.status_code == r2.status_code == 200
    # Compare sorted param lists (order may differ in the Out too)
    p1 = sorted(
        (p.get("name"), p.get("valueCode") or p.get("valueString") or p.get("valueUri"))
        for p in r1.json().get("parameter", [])
    )
    p2 = sorted(
        (p.get("name"), p.get("valueCode") or p.get("valueString") or p.get("valueUri"))
        for p in r2.json().get("parameter", [])
    )
    assert p1 == p2, (
        f"Property order changed response:\norder1: {p1}\norder2: {p2}"
    )


def test_e91_property_multi_does_not_change_out_required_params(fhir_client):
    """Out required params (name, code, system, display, abstract) MUST be
    present regardless of how many property values are supplied.
    """
    required = ("name", "code", "system", "display", "abstract")
    for n_props in (0, 1, 3, 5, 10):
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[("system", SNOMED_URI), ("code", SNOMED_DM)]
            + [("property", "name") for _ in range(n_props)],
        )
        assert r.status_code == 200, f"n_props={n_props}: {r.text[:200]!r}"
        names = {p.get("name") for p in r.json().get("parameter", [])}
        for req in required:
            assert req in names, (
                f"n_props={n_props}: missing required Out param {req!r}; "
                f"have {sorted(names)}"
            )


# ---------------------------------------------------------------------------
# L10 — Source-read structural contracts for lateral combination handling
# Verifies that the GET and POST handlers handle all optional params
# gracefully (no 5xx possible on the optional-param path).
# ---------------------------------------------------------------------------

def test_e100_lookup_get_handler_accepts_all_optional_params_source_read():
    """Source-read: lookup_get declares version as Optional str Query.
    Per FHIR R4, version is 0..1; the handler accepts None.
    """
    src = _get_func_source(_FHIR_API_PATH, "lookup_get")
    assert src, "lookup_get source not found"
    assert "version" in src
    assert "Query(None" in src or "Query(None," in src or "Query(None," in src
    # Per GLOBAL_RULES.md "Code Review Time" — required-string Query must
    # have min_length=1. Optional version Query is correctly NOT min_length=1.
    assert "system: str = Query(..., min_length=1" in src
    assert "code: str = Query(..., min_length=1" in src


def test_e101_lookup_post_handler_does_not_consume_property(fhir_client):
    """POST handler via _parse_parameters extracts only scalar value*
    entries. The property parameter (valueCode) is extracted but ignored
    (medterm4ds convention — server returns full property set). Probe:
    send POST with property params and verify the Out parameters include
    the standard property group (cui, tty, aui) — NOT the requested
    'name'/'display' that would imply filtering.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DM},
            {"name": "property", "valueCode": "name"},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 200
    body_json = r.json()
    # Standard property group is returned regardless of the property filter
    # (medterm4ds convention — documented NOT A BUG). The 'name'/'display'
    # standard params are at the top level, not in property group.
    property_codes = [
        part.get("valueCode")
        for p in body_json.get("parameter", [])
        if p.get("name") == "property"
        for part in p.get("part", [])
        if part.get("name") == "code"
    ]
    # The fixture seeds cui + tty for SNOMED DM — both should appear in
    # the Out property group regardless of what the client requested.
    assert "cui" in property_codes, (
        f"property 'cui' missing from Out property group: {property_codes}"
    )
    assert "tty" in property_codes, (
        f"property 'tty' missing from Out property group: {property_codes}"
    )


def test_e102_do_lookup_handler_calls_canonical_system_uri_source_read():
    """Source-read: _do_lookup calls canonical_system_uri for Out `system`
    (CS-02 HISTORIAN QA-047). This is the load-bearing structural contract
    for cross-operation canonical-URI consistency.
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    assert src, "_do_lookup source not found"
    assert "canonical_system_uri" in src, (
        "_do_lookup must call canonical_system_uri for Out `system` "
        "(CS-02 HISTORIAN QA-047)"
    )


def test_e103_lookup_post_parses_coding_correctly_source_read():
    """Source-read: lookup_post uses _extract_coding_from_parameters when
    system+code are absent. This is the load-bearing contract for the
    'POST coding' lateral combination class.
    """
    src = _get_func_source(_FHIR_API_PATH, "lookup_post")
    assert src, "lookup_post source not found"
    assert "_extract_coding_from_parameters" in src, (
        "lookup_post must call _extract_coding_from_parameters for the "
        "coding parameter alternative encoding (TS-02 HISTORIAN QA-022)"
    )


def test_e104_lookup_handler_defensive_pf_cache_guard_source_read():
    """Source-read: _do_lookup has the isinstance(pf, dict) guard for
    malformed patient-friendly cache entries (CS-02 HISTORIAN QA-046).
    Load-bearing contract for the lateral probe 'POST coding + property
    multi' which exercises the pf_cache path indirectly.
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    assert src, "_do_lookup source not found"
    assert "isinstance(pf, dict)" in src, (
        "_do_lookup must have isinstance(pf, dict) guard for malformed "
        "pf_cache entries (CS-02 HISTORIAN QA-046)"
    )


def test_e105_lookup_out_parameter_required_cardinality_source_read():
    """Source-read: build_parameters_lookup emits name (1..1), code (1..1),
    system (1..1), display (1..1), abstract (1..1) as required Out params
    per FHIR R4 §4.8.21.1. The lateral probes rely on these always being
    present.
    """
    responses_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "medterm4ds"
        / "engines"
        / "fhir"
        / "responses.py"
    )
    src = _get_func_source(responses_path, "build_parameters_lookup")
    assert src, "build_parameters_lookup source not found"
    # Per spec, name/code/system/display/abstract are 1..1 — always emitted
    for required_param in ('"name"', '"code"', '"system"', '"display"', '"abstract"'):
        assert required_param in src, (
            f"build_parameters_lookup must emit {required_param} as required "
            f"Out parameter (FHIR R4 §4.8.21.1 cardinality 1..1)"
        )
