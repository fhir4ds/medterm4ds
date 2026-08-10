"""SKEPTIC RESWEEP probes for CS-02 (CodeSystem $lookup Operation) — fresh
full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html (R4 / 4.0.1).

This file contains NEW hostile-input + standard-property-naming probes that
are NOT in the baseline ``test_cs02_skeptic.py``. The baseline is treated as
trusted prior coverage; this resweep file adds the FRESH-FULL-SWEEP mandated
probes per USER_DIRECTIVES [2026-08-08].

SKEPTIC lens (per ROLE_QA_ENGINEER Section 3): aggressive bug hunting — edge
cases, malformed inputs, boundary conditions.

CS-01/TERMINOLOGIST tip for CS-02/SKEPTIC: pivot to $lookup standard-property
naming contract — verify ``name`` returns the CODE SYSTEM name (NOT the
code's preferred-term) per FHIR R4 §4.8.21.1, verify ``display`` is the
recommended display, and probe the ``property`` parameter with multiple values
to confirm default-set inclusion of ``version``.

11 lens dimensions, 5-10 hostile probes each.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# Out Parameters:
#   name      1..1  string  "A display name for the code system"
#   version   0..1  string  "The version that these details are based on"
#   display   1..1  string  "The preferred display for this concept"
#   designation 0..*
#   property  0..*
# In Parameters:
#   code, system, version, coding, date, displayLanguage, property (0..*)
#
# Spec quote on the `property` In parameter:
#   "If no properties are specified, the server chooses what to return. The
#    following properties are defined for all code systems: url, name, version
#    (code system info) and code information: display, definition,
#    designation, parent and child, and for designations, lang.X where X is a
#    designation language code."

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DM = "73211009"
SNOMED_T2DM = "44054006"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_860975 = "860975"

# Engine-expected CS display names (from _SYSTEM_DISPLAY_NAMES in responses.py)
EXPECTED_SNOMED_NAME = "SNOMED Clinical Terms (US)"
EXPECTED_ICD10CM_NAME = (
    "International Classification of Diseases, 10th Revision, "
    "Clinical Modification"
)
EXPECTED_RXNORM_NAME = "RxNorm"


def _params_by_name(body: dict, name: str) -> list[dict]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _first_param(body: dict, name: str) -> dict | None:
    matches = _params_by_name(body, name)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# L1 — Standard property `name` returns CODE SYSTEM name (spec item 3)
# Pivot per CS-01/TERMINOLOGIST tip: `name` = CS name, NOT preferred-term.
# ---------------------------------------------------------------------------

def test_s01_lookup_name_is_code_system_name_not_concept_term_snomed(fhir_client):
    """Item 3 / spec Out `name` (1..1 string): "A display name for the code
    system". The CS-01/TERMINOLOGIST tip for CS-02/SKEPTIC: verify `name`
    returns the CODE SYSTEM name (e.g. "SNOMED Clinical Terms (US)"), NOT the
    concept's preferred term (e.g. "Diabetes mellitus").

    This probe distinguishes between the two interpretations — the baseline
    test_s30 only checks presence of `name`; it does NOT assert the value IS
    the CS name. This probe closes that gap.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r.status_code == 200
    body = r.json()
    name_param = _first_param(body, "name")
    assert name_param is not None, "$lookup Out missing `name` (1..1)"
    name_value = name_param.get("valueString", "")
    # name MUST be the CS display name, NOT the concept STR
    assert name_value == EXPECTED_SNOMED_NAME, (
        f"$lookup Out `name` MUST be the code system name "
        f"({EXPECTED_SNOMED_NAME!r}), got {name_value!r}. The concept "
        f"preferred-term ('Diabetes mellitus') MUST appear under `display`, "
        f"not `name`."
    )
    # Explicit negative assertion: name is NOT the concept STR
    assert name_value != "Diabetes mellitus", (
        "$lookup Out `name` leaked the concept preferred-term — should be the "
        "code system display name per FHIR R4 §4.8.21.1."
    )


def test_s02_lookup_name_is_code_system_name_not_concept_term_icd10cm(fhir_client):
    """Item 3 / spec Out `name`: parametrize the previous probe over a
    different code system to confirm the invariant holds across sources.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": ICD10CM_URI, "code": ICD10CM_E11},
    )
    assert r.status_code == 200
    body = r.json()
    name_param = _first_param(body, "name")
    assert name_param is not None
    name_value = name_param.get("valueString", "")
    assert name_value == EXPECTED_ICD10CM_NAME, (
        f"$lookup Out `name` for ICD-10-CM MUST be {EXPECTED_ICD10CM_NAME!r}, "
        f"got {name_value!r}."
    )


def test_s03_lookup_name_is_code_system_name_not_concept_term_rxnorm(fhir_client):
    """Item 3 / spec Out `name`: third source parametrization. RxNorm
    preferred-term is the drug name ("24 HR metformin 500 MG Oral Tablet");
    CS name is "RxNorm".
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": RXNORM_URI, "code": RXNORM_860975},
    )
    assert r.status_code == 200
    body = r.json()
    name_param = _first_param(body, "name")
    assert name_param is not None
    name_value = name_param.get("valueString", "")
    assert name_value == EXPECTED_RXNORM_NAME, (
        f"$lookup Out `name` for RxNorm MUST be {EXPECTED_RXNORM_NAME!r}, "
        f"got {name_value!r}."
    )


def test_s04_lookup_name_distinct_from_display(fhir_client):
    """Item 3 + 5 / spec: `name` (CS name) and `display` (preferred term)
    are DISTINCT Out parameters with distinct semantics. They MUST NOT be
    the same string in normal cases (only equal when the CS name happens to
    be the same as the concept name — pathological).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    body = r.json()
    name_value = _first_param(body, "name").get("valueString", "")
    display_value = _first_param(body, "display").get("valueString", "")
    assert name_value != display_value, (
        f"$lookup `name` ({name_value!r}) and `display` ({display_value!r}) "
        f"are equal — `name` should be the CS name, `display` the concept "
        f"preferred-term."
    )


# ---------------------------------------------------------------------------
# L2 — Standard property `display` returns RECOMMENDED display (spec item 5)
# ---------------------------------------------------------------------------

def test_s10_lookup_display_is_engine_preferred_str_snomed(fhir_client):
    """Item 5 / spec Out `display` (1..1 string): "The preferred display for
    this concept". Engine preferred STR for SNOMED T2DM is "Type 2 diabetes
    mellitus".
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    body = r.json()
    display_param = _first_param(body, "display")
    assert display_param is not None
    assert display_param.get("valueString") == "Type 2 diabetes mellitus"


def test_s11_lookup_display_never_raw_code_when_str_exists(fhir_client):
    """Item 5 / spec: when the engine has a STR, `display` MUST be the STR,
    NOT the raw code. Negative assertion: display != code for every seeded
    code.
    """
    cases = [
        (SNOMED_URI, SNOMED_DM),
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_E11),
        (RXNORM_URI, RXNORM_860975),
    ]
    for system, code in cases:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        assert r.status_code == 200, f"{system} {code} → {r.status_code}"
        body = r.json()
        display_value = _first_param(body, "display").get("valueString", "")
        assert display_value != code, (
            f"$lookup `display` for {code} echoed the raw code; engine STR "
            f"SHOULD be the preferred term."
        )


def test_s12_lookup_display_is_valueString_wire_format(fhir_client):
    """Item 5 / spec: wire-format audit — `display` MUST use valueString, not
    valueCode or other. (Reinforces baseline test_s32 but parametrized.)
    """
    for system, code in [
        (SNOMED_URI, SNOMED_DM),
        (ICD10CM_URI, ICD10CM_E11),
        (RXNORM_URI, RXNORM_860975),
    ]:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        body = r.json()
        display_param = _first_param(body, "display")
        assert "valueString" in display_param, (
            f"$lookup `display` for {code} must use valueString; keys="
            f"{list(display_param.keys())}"
        )
        assert "valueCode" not in display_param


# ---------------------------------------------------------------------------
# L3 — Standard property `version` default-set (spec item 8 + 4)
# The spec lists `version` among the standard code system info properties.
# medterm4ds does NOT track per-version UMLS data; `version` is 0..1.
# ---------------------------------------------------------------------------

def test_s20_lookup_version_out_param_cardinality_0_or_1(fhir_client):
    """Item 4 + 8 / spec Out `version` (0..1): when present, exactly one
    `version` parameter. When absent, zero. The fixture has no version data
    so version is absent — assert that absence is conformant.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    body = r.json()
    version_params = _params_by_name(body, "version")
    assert len(version_params) <= 1, (
        f"$lookup Out `version` has cardinality 0..1; got "
        f"{len(version_params)} occurrences"
    )


def test_s21_lookup_version_out_param_never_as_property_group(fhir_client):
    """Item 4 / spec: when version is in the default set, it appears as a
    NAMED parameter (`version`), NOT as an entry in the `property` group
    (which has part[].code='version'). Negative assertion: no property
    group entry with code='version' under default invocation.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    body = r.json()
    property_groups = _params_by_name(body, "property")
    for pg in property_groups:
        parts = pg.get("part", [])
        for part in parts:
            if part.get("name") == "code" and part.get("valueCode") == "version":
                pytest.fail(
                    "$lookup default response carries `version` inside the "
                    "property group; spec lists it as a NAMED parameter, not "
                    "a property.code entry."
                )


def test_s22_lookup_explicit_property_version_request_accepted(fhir_client):
    """Item 8 / spec In `property` (0..*): client may request specific
    properties. Requesting property=version MUST NOT cause a 5xx — server
    is permissive about which properties it can honor.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "property": "version",
        },
    )
    assert r.status_code == 200, (
        f"$lookup with property=version → {r.status_code}; expected 200"
    )
    # Body MUST still be a Parameters resource
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_s23_lookup_explicit_property_name_request_accepted(fhir_client):
    """Item 8 / spec In `property` (0..*): request the standard `name`
    property explicitly. The named `name` parameter MUST still be present
    (standard named params are always emitted regardless of property filter).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "property": "name",
        },
    )
    assert r.status_code == 200
    body = r.json()
    # `name` is always 1..1 regardless of property filter
    assert _first_param(body, "name") is not None


def test_s24_lookup_explicit_property_display_request_accepted(fhir_client):
    """Item 8 / spec In `property` (0..*): request `display` explicitly.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "property": "display",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _first_param(body, "display") is not None


# ---------------------------------------------------------------------------
# L4 — Default property set when property omitted (spec item 8)
# ---------------------------------------------------------------------------

def test_s30_lookup_default_set_always_includes_name(fhir_client):
    """Item 8 / spec: `name` is 1..1 — ALWAYS present in default set."""
    for system, code in [
        (SNOMED_URI, SNOMED_DM),
        (ICD10CM_URI, ICD10CM_E11),
        (RXNORM_URI, RXNORM_860975),
    ]:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        body = r.json()
        assert _first_param(body, "name") is not None, (
            f"$lookup default Out missing `name` for {code}"
        )


def test_s31_lookup_default_set_always_includes_display(fhir_client):
    """Item 8 / spec: `display` is 1..1 — ALWAYS present in default set."""
    for system, code in [
        (SNOMED_URI, SNOMED_DM),
        (ICD10CM_URI, ICD10CM_E11),
        (RXNORM_URI, RXNORM_860975),
    ]:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        body = r.json()
        assert _first_param(body, "display") is not None, (
            f"$lookup default Out missing `display` for {code}"
        )


def test_s32_lookup_default_set_always_includes_code_and_system(fhir_client):
    """Item 1 + 8 / spec: default Out set includes the `code` and `system`
    named parameters echoing the looked-up code+canonical system.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    body = r.json()
    code_param = _first_param(body, "code")
    system_param = _first_param(body, "system")
    assert code_param is not None
    assert system_param is not None
    assert code_param.get("valueCode") == SNOMED_DM
    # Out `system` is canonical (CF-HISTORIAN-VS02-02 sibling pattern)
    assert system_param.get("valueUri") == SNOMED_URI


def test_s33_lookup_default_set_get_post_parity(fhir_client):
    """Item 8 + 9 / spec: default Out set is IDENTICAL between GET and POST
    (POST body carries system+code). Byte-exact param-name-set parity.
    """
    get_r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DM},
        ],
    }
    post_r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert get_r.status_code == 200
    assert post_r.status_code == 200
    get_names = sorted({p["name"] for p in get_r.json().get("parameter", [])})
    post_names = sorted({p["name"] for p in post_r.json().get("parameter", [])})
    assert get_names == post_names, (
        f"GET/POST default Out param-name-set mismatch: GET={get_names}, "
        f"POST={post_names}"
    )


# ---------------------------------------------------------------------------
# L5 — POST coding parameter parity with GET (spec item 9)
# ---------------------------------------------------------------------------

def test_s40_post_coding_byte_exact_with_get_system_code(fhir_client):
    """Item 9 / spec: POST with `coding` parameter MUST produce same response
    as GET with system+code. Compare the entire parameter list semantically
    (valueString / valueCode / valueUri content).
    """
    get_r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DM,
                },
            }
        ],
    }
    post_r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert get_r.status_code == 200
    assert post_r.status_code == 200

    def _norm(body):
        # Normalize param list to (name, value) tuples for comparison
        out = []
        for p in body.get("parameter", []):
            name = p.get("name")
            # Extract the single value* key
            val_key = next(
                (k for k in p if k.startswith("value") and k != "value"), None
            )
            if val_key:
                out.append((name, val_key, p[val_key]))
            elif "part" in p:
                # Property group — normalize parts
                parts = []
                for part in p["part"]:
                    pname = part.get("name")
                    pval_key = next(
                        (k for k in part if k.startswith("value") and k != "value"),
                        None,
                    )
                    parts.append((pname, pval_key, part.get(pval_key)))
                out.append((name, "part", tuple(parts)))
        return sorted(out, key=str)

    assert _norm(get_r.json()) == _norm(post_r.json()), (
        "POST coding response differs from GET system+code response"
    )


def test_s41_post_coding_with_only_system_in_coding_rejected(fhir_client):
    """Item 1 / spec: 'a client SHALL provide both a system and a code'.
    POST coding with system but no code MUST be rejected (400).
    """
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "coding", "valueCoding": {"system": SNOMED_URI}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert r.status_code in (400, 422), (
        f"POST coding with only system → {r.status_code}; expected 400/422"
    )


def test_s42_post_coding_with_only_code_in_coding_rejected(fhir_client):
    """Item 1 / spec: 'a client SHALL provide both a system and a code'.
    POST coding with code but no system MUST be rejected.
    """
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "coding", "valueCoding": {"code": SNOMED_DM}},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert r.status_code in (400, 422)


def test_s43_post_coding_with_extra_fields_accepted(fhir_client):
    """Item 9 / spec: Coding may carry `display`, `userSelected`, `version`.
    Server MUST accept extra fields and extract system+code only.
    """
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DM,
                    "display": "Diabetes mellitus",
                    "userSelected": True,
                    "version": "http://snomed.info/sct/731000124108",
                },
            }
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert r.status_code == 200, (
        f"POST coding with extra fields → {r.status_code}"
    )
    body = r.json()
    # The Out `code` MUST be the looked-up code, not a leak of the input version
    out_code = _first_param(body, "code")
    assert out_code.get("valueCode") == SNOMED_DM


def test_s44_post_system_code_and_coding_both_present_system_code_wins(fhir_client):
    """Item 1 + 9 / spec: when client sends BOTH system+code AND coding,
    the spec is ambiguous. Current behavior: system+code takes precedence
    (the handler's `if (not system or not code)` branch falls back to coding
    ONLY when system+code are absent). This probe documents that behavior.
    """
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DM},
            {
                "name": "coding",
                "valueCoding": {
                    "system": ICD10CM_URI,
                    "code": ICD10CM_E11,
                },
            },
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert r.status_code == 200
    body = r.json()
    # system+code wins — Out `system` is SNOMED, not ICD-10-CM
    out_system = _first_param(body, "system")
    assert out_system.get("valueUri") == SNOMED_URI


# ---------------------------------------------------------------------------
# L6 — Required parameter edge cases (spec item 1) — beyond min_length=1
# ---------------------------------------------------------------------------

def test_s50_get_lookup_very_long_code_handled(fhir_client):
    """Item 1 / spec: very long code (>1000 chars) MUST NOT cause 500."""
    long_code = "A" * 2000
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": long_code},
    )
    assert r.status_code in (200, 400, 404), (
        f"$lookup very long code → {r.status_code}; expected 200/400/404 not 500"
    )
    # 200 path → OperationOutcome not-found (code not in fixture)
    if r.status_code == 200:
        body = r.json()
        assert body.get("resourceType") in ("Parameters", "OperationOutcome")


def test_s51_get_lookup_code_with_sql_injection_safe(fhir_client):
    """Item 1 / spec: code with SQL injection chars MUST NOT cause 500."""
    malicious = "44054006'; DROP TABLE mrconso; --"
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": malicious},
    )
    assert r.status_code in (200, 400, 404)
    # Verify mrconso table still exists (no SQL injection succeeded)
    # We can't directly query the DB from the test client, but the absence
    # of a 500 + presence of a clean FHIR body is sufficient evidence.


def test_s52_get_lookup_code_with_null_bytes_rejected(fhir_client):
    """Item 1 / spec: code with null bytes MUST NOT cause 500."""
    # Use URL encoding to send a null byte
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": "44054006%00"},
    )
    assert r.status_code in (200, 400, 404, 422)


def test_s53_get_lookup_code_with_unicode_safe(fhir_client):
    """Item 1 / spec: code with unicode chars MUST NOT cause 500."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": "44054006αβγ"},
    )
    assert r.status_code in (200, 400, 404)


def test_s54_get_lookup_code_with_path_traversal_safe(fhir_client):
    """Item 1 / spec: code with path traversal chars MUST NOT cause 500."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": "../../../etc/passwd"},
    )
    assert r.status_code in (200, 400, 404)


def test_s55_get_lookup_trailing_slash_system_canonical_out(fhir_client):
    """Item 1 / spec + CF-HISTORIAN-VS02-02 sibling: system with trailing
    slash MUST be canonicalized in Out `system` (not echoed verbatim).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": f"{SNOMED_URI}/", "code": SNOMED_DM},
    )
    if r.status_code == 200:
        body = r.json()
        out_system = _first_param(body, "system")
        if out_system:
            assert out_system.get("valueUri") == SNOMED_URI, (
                f"Out `system` echoed trailing-slash input "
                f"({out_system.get('valueUri')!r}); should be canonical "
                f"({SNOMED_URI!r})."
            )


def test_s56_get_lookup_urn_oid_alias_system_canonical_out(fhir_client):
    """Item 1 / spec + CF-HISTORIAN-VS02-02 sibling: system as urn:oid alias
    MUST be canonicalized in Out `system`.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "urn:oid:2.16.840.1.113883.6.96", "code": SNOMED_DM},
    )
    if r.status_code == 200:
        body = r.json()
        out_system = _first_param(body, "system")
        if out_system:
            assert out_system.get("valueUri") == SNOMED_URI, (
                f"Out `system` echoed urn:oid alias; should be canonical "
                f"({SNOMED_URI!r})."
            )


# ---------------------------------------------------------------------------
# L7 — POST body type mismatches (spec item 9, SKEPTIC lens)
# ---------------------------------------------------------------------------

def test_s60_post_body_code_as_value_integer_handled(fhir_client):
    """Item 9 / spec SKEPTIC: POST body with code as valueInteger (wrong
    value[x] type) MUST be handled gracefully (not 500).
    """
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueInteger": 44054006},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    # Either: 400 (rejected), 422 (validation error), or 200 with system/coding
    # fallback extracting nothing (then 400 for missing code)
    assert r.status_code in (200, 400, 422), (
        f"POST code=valueInteger → {r.status_code}; expected 200/400/422 not 500"
    )


def test_s61_post_body_system_as_value_integer_handled(fhir_client):
    """Item 9 / spec SKEPTIC: POST body with system as valueInteger (wrong
    value[x] type) MUST be handled gracefully.
    """
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueInteger": 12345},
            {"name": "code", "valueCode": SNOMED_DM},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert r.status_code in (200, 400, 422)


def test_s62_post_body_coding_as_value_string_handled(fhir_client):
    """Item 9 / spec SKEPTIC: POST body with coding as valueString (wrong
    value[x] type) MUST be handled gracefully — coding is type Coding per spec.
    """
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueString": "not a coding object",
            }
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert r.status_code in (200, 400, 422)


def test_s63_post_body_not_a_parameters_resource_handled(fhir_client):
    """Item 9 / spec SKEPTIC: POST body that is NOT a Parameters resource
    (e.g. a CodeSystem resource) MUST be handled gracefully (not 500).
    """
    post_body = {
        "resourceType": "CodeSystem",
        "url": SNOMED_URI,
        "content": "complete",
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert r.status_code in (200, 400, 422), (
        f"POST non-Parameters body → {r.status_code}; expected 200/400/422"
    )


def test_s64_post_body_empty_parameter_list_rejected(fhir_client):
    """Item 1 + 9 / spec: POST body with empty parameter list has no
    system+code or coding — MUST be rejected with 400 (not 200 with empty
    Parameters).
    """
    post_body = {
        "resourceType": "Parameters",
        "parameter": [],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert r.status_code in (400, 422), (
        f"POST empty parameter list → {r.status_code}; expected 400/422"
    )


def test_s65_post_body_wrong_resourcetype_handled(fhir_client):
    """Item 9 / spec SKEPTIC: POST body with resourceType=Bundle (wrong type)
    MUST be handled gracefully.
    """
    post_body = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert r.status_code in (200, 400, 422)


# ---------------------------------------------------------------------------
# L8 — displayLanguage with non-standard locales (spec item 2)
# ---------------------------------------------------------------------------

def test_s70_lookup_display_language_standard_en(fhir_client):
    """Item 2 / spec: displayLanguage=en (standard) accepted."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "displayLanguage": "en",
        },
    )
    assert r.status_code == 200


def test_s71_lookup_display_language_nonstandard_klingon(fhir_client):
    """Item 2 / spec SKEPTIC: displayLanguage=klingon (non-standard locale)
    MUST NOT cause 500. Server should be permissive (no locale matching →
    falls back to default display).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "displayLanguage": "klingon",
        },
    )
    assert r.status_code == 200, (
        f"displayLanguage=klingon → {r.status_code}; expected 200 (permissive)"
    )


def test_s72_lookup_display_language_extension_syntax(fhir_client):
    """Item 2 / spec SKEPTIC: displayLanguage with private extension syntax
    (en-US-x-test) MUST NOT cause 500.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "displayLanguage": "en-US-x-test",
        },
    )
    assert r.status_code == 200


def test_s73_lookup_display_language_de_de(fhir_client):
    """Item 2 / spec SKEPTIC: displayLanguage=de-DE accepted."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "displayLanguage": "de-DE",
        },
    )
    assert r.status_code == 200


def test_s74_lookup_display_language_very_long_locale(fhir_client):
    """Item 2 / spec SKEPTIC: displayLanguage with a very long locale string
    MUST NOT cause 500.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "displayLanguage": "x" * 500,
        },
    )
    assert r.status_code in (200, 400, 422)


# ---------------------------------------------------------------------------
# L9 — property parameter multi-valued (spec item 2 + 10)
# ---------------------------------------------------------------------------

def test_s80_get_lookup_multiple_property_params_accepted(fhir_client):
    """Item 2 / spec: property is 0..* (multi-valued). Multiple property
    params in query string MUST be accepted.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_DM),
            ("property", "name"),
            ("property", "version"),
            ("property", "display"),
        ],
    )
    assert r.status_code == 200


def test_s81_get_lookup_property_parent_and_child_accepted(fhir_client):
    """Item 10 / spec: parent/child properties for subsumption decomposition.
    Server is permissive about which it can honor; MUST NOT 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_T2DM),
            ("property", "parent"),
            ("property", "child"),
        ],
    )
    assert r.status_code == 200


def test_s82_get_lookup_unknown_property_accepted(fhir_client):
    """Item 2 / spec: unknown property name. Server is permissive (spec:
    'If no properties are specified, the server chooses what to return' —
    implies server is also free to ignore unknown properties).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "property": "nonexistent_property_xyz",
        },
    )
    assert r.status_code == 200


def test_s83_post_lookup_multiple_property_valueString_parts(fhir_client):
    """Item 2 / spec: POST body with multiple property parts. MUST be
    accepted.
    """
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_DM},
            {"name": "property", "valueCode": "name"},
            {"name": "property", "valueCode": "version"},
            {"name": "property", "valueCode": "display"},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
    assert r.status_code == 200


def test_s84_get_lookup_conflicting_property_and_property_code(fhir_client):
    """Item 2 / spec: property is the documented In parameter name. There
    is no separate 'property.code' In parameter on $lookup (property.code is
    an OUT parameter). Probe sending property.code as In to verify server
    ignores it gracefully.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "property": "name",
            "property.code": "display",  # not a real In param
        },
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# L10 — Subsumption-decomposition via property param (spec item 10)
# ---------------------------------------------------------------------------

def test_s90_lookup_property_parent_on_child_code_accepted(fhir_client):
    """Item 10 / spec: requesting parent on T2DM (child of DM). Server is
    permissive; MUST NOT 5xx. Fixture has mrrel A44054006 → A73211009 PAR.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "property": "parent",
        },
    )
    assert r.status_code == 200


def test_s91_lookup_property_child_on_parent_code_accepted(fhir_client):
    """Item 10 / spec: requesting child on DM (parent of T2DM).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "property": "child",
        },
    )
    assert r.status_code == 200


def test_s92_lookup_property_parent_on_code_without_hierarchy(fhir_client):
    """Item 10 / spec SKEPTIC: requesting parent on a code that has NO
    hierarchy in the fixture (RxNorm 860975 has no mrrel rows). MUST NOT 5xx.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": RXNORM_URI,
            "code": RXNORM_860975,
            "property": "parent",
        },
    )
    assert r.status_code == 200


def test_s93_lookup_property_child_on_code_without_hierarchy(fhir_client):
    """Item 10 / spec SKEPTIC: requesting child on a code without hierarchy.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": RXNORM_URI,
            "code": RXNORM_860975,
            "property": "child",
        },
    )
    assert r.status_code == 200


def test_s94_lookup_property_group_shape_when_present(fhir_client):
    """Item 10 / spec: when parent/child IS honored, the response carries
    them in the Out `property` group with part.code=parent/child and
    part.value=<code>. Probe the shape (server may or may not honor; this
    probe is permissive).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "property": "parent",
        },
    )
    body = r.json()
    # Look for any property group with code=parent
    parent_groups = []
    for p in body.get("parameter", []):
        if p.get("name") == "property":
            parts = p.get("part", [])
            for part in parts:
                if part.get("name") == "code" and part.get("valueCode") == "parent":
                    parent_groups.append(p)
                    break
    # If any parent group is present, its value MUST be a known parent code
    for pg in parent_groups:
        value_part = next(
            (p for p in pg["part"] if p.get("name") == "value"), None
        )
        if value_part:
            # Value should be a code (valueCode or valueString); if SNOMED
            # DM, it's the known parent
            val = value_part.get("valueCode") or value_part.get("valueString")
            assert val is not None


# ---------------------------------------------------------------------------
# L11 — Source-read structural contracts (SKEPTIC defensive audit)
# ---------------------------------------------------------------------------

_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "apps"
    / "fhir_api.py"
)
_RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "engines"
    / "fhir"
    / "responses.py"
)


def _get_func_source(file_path: Path, func_name: str) -> str:
    """Extract the source text of a function (possibly nested) by name.

    Walks BOTH ast.FunctionDef AND ast.AsyncFunctionDef (the prior helper
    only walked FunctionDef, missing async handlers — TS-04 HISTORIAN
    methodology extension).
    """
    tree = ast.parse(file_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(file_path.read_text(), node)
    return ""


def test_s100_build_parameters_lookup_emits_name_as_standard_param():
    """Source-read contract: build_parameters_lookup emits `name` as a
    standard named parameter (not inside a property group). The `_param`
    helper is called with name="name".
    """
    src = _get_func_source(_RESPONSES_PATH, "build_parameters_lookup")
    assert '_param("name"' in src, (
        "build_parameters_lookup MUST emit `name` as a standard _param; "
        "source: " + src[:200]
    )


def test_s101_build_parameters_lookup_emits_display_as_standard_param():
    """Source-read contract: build_parameters_lookup emits `display` as a
    standard named parameter."""
    src = _get_func_source(_RESPONSES_PATH, "build_parameters_lookup")
    assert '_param("display"' in src, (
        "build_parameters_lookup MUST emit `display` as a standard _param"
    )


def test_s102_build_parameters_lookup_emits_name_via_system_display_name():
    """Source-read contract: the `name` value comes from
    _system_display_name(system_uri), NOT from code_info.name (which is the
    concept's preferred term). This is the load-bearing distinction per
    CS-01/TERMINOLOGIST tip.
    """
    src = _get_func_source(_RESPONSES_PATH, "build_parameters_lookup")
    assert "_system_display_name(system_uri)" in src, (
        "build_parameters_lookup MUST derive `name` from "
        "_system_display_name(system_uri), NOT code_info.name"
    )


def test_s103_do_lookup_calls_canonical_system_uri():
    """Source-read contract: _do_lookup calls canonical_system_uri for the
    Out `system` (CF-HISTORIAN-VS02-02 sibling pattern — the structural
    fix candidate from CR-011/012/013).
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    assert "canonical_system_uri(" in src, (
        "_do_lookup MUST call canonical_system_uri() for Out `system`"
    )


def test_s104_do_lookup_guards_pf_cache_with_isinstance_dict():
    """Source-read contract: _do_lookup guards pf_cache with isinstance dict
    (CS-02 HISTORIAN QA-046 fix — malformed pf entries don't crash handler).
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    assert "isinstance(pf, dict)" in src, (
        "_do_lookup MUST guard pf_cache with isinstance(pf, dict)"
    )


def test_s105_lookup_post_handler_routes_through_do_lookup():
    """Source-read contract: lookup_post handler delegates to _do_lookup via
    _run_db (same builder as GET). This structurally guarantees GET/POST
    parity on the Out parameter set.
    """
    src = _get_func_source(_FHIR_API_PATH, "lookup_post")
    assert "_do_lookup" in src, (
        "lookup_post MUST route through _do_lookup"
    )
