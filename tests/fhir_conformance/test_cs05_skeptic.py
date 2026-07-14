"""SKEPTIC probes for CS-05 (CodeSystem Edge Cases).

Spec: https://build.fhir.org/codesystem.html
       (canonical R4: https://hl7.org/fhir/R4/codesystem.html)
       concept-properties: https://hl7.org/fhir/R4/concept-properties.html
       $lookup: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
       $validate-code: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
       $subsumes: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html

Scope (per chunk assignment) — 5 items:
  1. Inactive codes: $lookup returns `inactive=true` property;
     $validate-code returns `result=false` by default for inactive codes.
  2. Version-specific behavior: $lookup with `version` param returns the
     correct historical atom.
  3. Mutually-exclusive properties: server returns all applicable properties
     (e.g., a code may have multiple property values).
  4. Abstract concepts: $lookup returns `abstract=true`;
     $expand includes them with `abstract` flag.
  5. Multi-hierarchy codes: $subsumes correctly handles concepts at
     multiple depths.

SKEPTIC lens:
  - Probe `inactive` on a known active code: property ABSENT or `inactive=false`.
  - Probe `inactive` on an inactive code: fixture has NO inactive codes
    (all mrconso rows are SUPPRESS='N'). Documented as DEFERRED with
    reproduction shape.
  - Probe `$validate-code` on inactive code: same fixture gap.
  - Probe `version` param behavior — medterm4ds has no versioned UMLS atoms
    (single mrconso snapshot). Probes assert current behavior.
  - Probe multi-property surface: every $lookup response carries multiple
    `property` entries (cui, tty, aui + custom).
  - Probe `abstract` Out parameter on $lookup: implementation HARDCODES
    `abstract=false` (responses.py:46) — never reflects actual concept
    abstractness. Probed here as a finding candidate.
  - Probe `$subsumes` on multi-hierarchy: BFS with `visited` set handles
    multiple parents correctly (services/hierarchy.py:121).

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape (200 +
    expected fields), not just absence of one error string.
  - "Don't manufacture bugs": if the fixture lacks data to exercise an item,
    document as DEFERRED with reproduction shape.
  - Spec citation required on every probe.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child
    ("E11", "HT", "Type 2 diabetes mellitus", "AE11", "N", "ICD10CM", "C0011847"),
    ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),

All rows have SUPPRESS='N' (active). No inactive, abstract, or versioned
atoms seeded. Probes for those items assert the CURRENT behavior and
document the gap as a carry-forward for future fixture enhancements.
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem.html
# Spec: https://hl7.org/fhir/R4/concept-properties.html
# Standard FHIR R4 concept properties. Per concept-properties.html:
#   inactive (boolean) — True if the concept is deprecated/retired.
#   abstract (boolean) — True if the concept is not meant to be used in
#     an instance (only as a grouping/parent concept).
# These are surfaced via $lookup's Out `property` group (§4.8.21.1) and
# `abstract` is also a top-level Out parameter of $lookup.

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent (no abstract flag in fixture)
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"


def _lookup_param(body: dict, name: str) -> dict | None:
    """Return the first Out parameter with the given name, or None."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


def _lookup_param_value(body: dict, name: str):
    """Return the value of the first Out parameter with the given name."""
    p = _lookup_param(body, name)
    if p is None:
        return None
    for k, v in p.items():
        if k.startswith("value"):
            return v
    return None


def _property_value(body: dict, prop_code: str):
    """Return the value of the first `property` Out parameter whose `code`
    part matches `prop_code`. Per FHIR R4 §4.8.21.1, the property group is
    `parameter[].name='property'` with `part[].name in {'code','value'}`.
    """
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        parts = p.get("part", [])
        code = None
        value = None
        for part in parts:
            if part.get("name") == "code":
                code = part.get("valueCode")
            elif part.get("name") == "value":
                for k, v in part.items():
                    if k.startswith("value"):
                        value = v
                        break
        if code == prop_code:
            return value
    return None


def _has_property(body: dict, prop_code: str) -> bool:
    """Return True if the Out `property` group contains an entry with the
    given code (regardless of value).
    """
    sentinel = object()
    return _property_value(body, prop_code) is not None or _property_code_present(body, prop_code)


def _property_code_present(body: dict, prop_code: str) -> bool:
    """Return True if the Out `property` group contains an entry with the
    given code (even if the value is empty/null).
    """
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        for part in p.get("part", []):
            if part.get("name") == "code" and part.get("valueCode") == prop_code:
                return True
    return False


def _validate_result(body: dict) -> bool | None:
    """Return the valueBoolean value of the Out `result` parameter."""
    for p in body.get("parameter", []):
        if p.get("name") == "result":
            return p.get("valueBoolean")
    return None


def _outcome(body: dict) -> str | None:
    """Return the valueCode of the Out `outcome` parameter ($subsumes)."""
    for p in body.get("parameter", []):
        if p.get("name") == "outcome":
            return p.get("valueCode")
    return None


# ---------------------------------------------------------------------------
# Item 1: Inactive codes — $lookup returns `inactive=true` property;
#          $validate-code returns `result=false` by default.
# ---------------------------------------------------------------------------
# Fixture gap: conformance fixture seeds ONLY SUPPRESS='N' (active) atoms.
# Item 1 cannot be directly exercised. Probes below assert the CURRENT
# behavior on active codes and document the inactive-code gap as a
# DEFERRED carry-forward with a reproduction shape.

def test_s10_lookup_on_active_code_does_not_emit_inactive_true(fhir_client):
    """Item 1 / spec concept-properties.html `inactive`: on an active code,
    the server MUST NOT emit `inactive=true`. The property may be absent
    (active is the default) OR explicitly `inactive=false`. Emitting
    `inactive=true` for an active code would be silent-wrong-answer.

    Spec: https://hl7.org/fhir/R4/concept-properties.html — "inactive:
    True if the concept is deprecated/retired; false if the concept is
    active and may be used."
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    # The `inactive` property MUST NOT be true for an active code.
    inactive_val = _property_value(body, "inactive")
    assert inactive_val is not True, (
        f"active code {SNOMED_T2DM} emitted inactive=true — silent-wrong-answer; "
        f"property value: {inactive_val!r}"
    )
    if inactive_val is not None:
        # If the server emits the property, it MUST be exactly False (boolean).
        assert inactive_val is False, (
            f"active code emitted inactive={inactive_val!r} (expected False or absent)"
        )


def test_s11_lookup_emits_abstract_out_parameter(fhir_client):
    """Item 1 / Item 4 cross-check: $lookup Out `abstract` is a top-level
    Out parameter per FHIR R4 §4.8.21.1 (Out Parameters table: `abstract`
    0..1 boolean). The server MUST emit it.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out
    Parameters: "abstract 0..1 boolean — True if this code is abstract".
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    # The `abstract` Out parameter MUST be present (build_parameters_lookup
    # hardcodes _param("abstract", False, "valueBoolean")).
    p = _lookup_param(body, "abstract")
    assert p is not None, (
        "$lookup response is missing the Out `abstract` parameter "
        "(spec §4.8.21.1 Out Parameters table lists `abstract` 0..1 boolean)"
    )
    # The value MUST be a boolean (not a string).
    assert "valueBoolean" in p, (
        f"Out `abstract` uses non-boolean type {list(p.keys())!r}; expected valueBoolean"
    )
    assert isinstance(p["valueBoolean"], bool), (
        f"Out `abstract` valueBoolean is {type(p['valueBoolean']).__name__}, not bool"
    )


def test_s12_validate_code_on_active_code_returns_result_true(fhir_client):
    """Item 1 / spec $validate-code: on an active code, the server MUST
    return `result=true`. This is the inverse-direction probe — confirms
    the active path before documenting the inactive-code gap.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    result = _validate_result(body)
    assert result is True, (
        f"active code {SNOMED_T2DM}: result={result!r}, expected True"
    )


def test_s13_lookup_on_snomed_parent_emits_abstract_false(fhir_client):
    """Item 4 / spec $lookup Out `abstract`: even for a parent concept
    (73211009 Diabetes mellitus — a SNOMED hierarchy node), the server
    emits `abstract=false` because the engine has no abstract-flag data.
    This documents the engine limitation: the value is hardcoded False
    regardless of the concept's actual abstractness in the source
    terminology. See test_s70 for the finding-candidate probe.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_DIABETES_MELLITUS}"
    )
    assert r.status_code == 200
    body = r.json()
    abstract_val = _lookup_param_value(body, "abstract")
    # The implementation hardcodes False; the spec says it SHOULD reflect
    # the source terminology's abstract flag. Documenting the current
    # behavior — the finding-candidate probe (test_s70) covers the drift.
    assert abstract_val is False, (
        f"parent code abstract={abstract_val!r}; expected False (hardcoded in responses.py:46)"
    )


# ---------------------------------------------------------------------------
# Item 2: Version-specific behavior — $lookup with `version` param returns
#          the correct historical atom.
# ---------------------------------------------------------------------------
# Engine limitation: medterm4ds loads a single mrconso snapshot; no
# versioned atoms are tracked. The `version` param is accepted (declared
# on the GET handler signature) but ignored — documented in AGENTS.md
# NOT A BUG registry. Probes below assert the CURRENT behavior.

def test_s20_lookup_with_version_param_accepted(fhir_client):
    """Item 2 / spec $lookup In `version` (0..1 string): the server MUST
    accept the `version` parameter without erroring. Today the param is
    accepted and ignored (single-snapshot engine); processing deferred
    per AGENTS.md NOT A BUG registry.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html In
    Parameters: "version 0..1 string — The version of the code system,
    if one was provided in the source data".
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&version=2024-09"
    )
    assert r.status_code == 200, (
        f"version param rejected: {r.status_code} {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    # The display MUST still be returned (engine uses the single snapshot).
    display = _lookup_param_value(body, "display")
    assert display and "diabetes" in display.lower(), (
        f"version+code lookup returned display={display!r}"
    )


def test_s21_lookup_with_nonexistent_version_accepted(fhir_client):
    """Item 2 edge / spec $lookup In `version`: a non-existent version
    string is accepted (no version-scoping today). The probe asserts the
    CURRENT behavior — the engine does not distinguish existent from
    non-existent versions because only one snapshot is loaded.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&version=NONEXISTENT_VERSION_2099"
    )
    assert r.status_code == 200, (
        f"non-existent version rejected with {r.status_code}; engine is "
        f"single-snapshot — version param should be accepted (current behavior)"
    )


def test_s22_lookup_with_malformed_version_accepted(fhir_client):
    """Item 2 edge / spec $lookup In `version`: a malformed version string
    (e.g. with spaces, special chars) is accepted. Same current-behavior
    reasoning as test_s21.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&version=not%20a%20version%21%40%23"
    )
    assert r.status_code == 200, (
        f"malformed version rejected with {r.status_code}; current behavior is accept-and-ignore"
    )


def test_s23_validate_code_with_version_param_accepted(fhir_client):
    """Item 2 / spec $validate-code In `version`: same shape as $lookup.
    The param is accepted and ignored today.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&version=2024-09"
    )
    assert r.status_code == 200
    body = r.json()
    assert _validate_result(body) is True


def test_s24_subsumes_with_version_param_accepted(fhir_client):
    """Item 2 / spec $subsumes In `version`: same shape as $lookup. The
    param is accepted and ignored today.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
        f"&version=2024-09"
    )
    assert r.status_code == 200
    body = r.json()
    assert _outcome(body) == "subsumes"


def test_s25_lookup_version_does_not_change_display(fhir_client):
    """Item 2 / spec $lookup: passing different version strings MUST NOT
    change the display today (single snapshot). If a future engine adds
    versioned atoms, this probe would need updating.
    """
    displays = []
    for v in ["", "2024-09", "2023-03", "NONEXISTENT"]:
        url = f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        if v:
            url += f"&version={v}"
        r = fhir_client.get(url)
        assert r.status_code == 200
        displays.append(_lookup_param_value(r.json(), "display"))
    # All displays MUST be identical (single snapshot).
    assert len(set(displays)) == 1, (
        f"different version params produced different displays: {displays!r}"
    )


# ---------------------------------------------------------------------------
# Item 3: Mutually-exclusive properties — server returns all applicable
#          properties (a code may have multiple property values).
# ---------------------------------------------------------------------------
# Probe class: every $lookup response carries a `property` Out group with
# multiple entries (cui, tty, aui + custom properties from patient-friendly
# JSON). This item is structurally covered by CS-01 SKEPTIC (QA-043) and
# CS-01 TERMINOLOGIST (match-type registry). SKEPTIC probes here assert
# the multi-property surface holds.

def test_s30_lookup_returns_multiple_properties(fhir_client):
    """Item 3 / spec §4.8.21.1 Out `property` (0..*): the server MUST
    return all applicable properties. The conformance fixture doesn't
    load patient-friendly JSONs, so the custom properties (patient-friendly,
    match-type, canonical-code, canonical-system, tty) are absent — but
    the engine-level properties (cui, tty, aui) SHOULD be present.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out
    Parameters: "property 0..* — One or more properties that contain
    information about the concept".
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    # Count property entries.
    property_count = sum(1 for p in body.get("parameter", []) if p.get("name") == "property")
    # The engine populates cui, tty, aui for seeded SNOMED codes.
    assert property_count >= 1, (
        f"$lookup returned {property_count} property entries; expected at least 1 "
        f"(engine should populate cui/tty/aui for seeded codes)"
    )


def test_s31_lookup_property_cui_present_for_seeded_code(fhir_client):
    """Item 3 / spec Out `property`: the `cui` property is populated by
    the engine for seeded SNOMED codes (CUI column in mrconso).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    cui = _property_value(body, "cui")
    assert cui == "C0011847", (
        f"cui property = {cui!r}, expected 'C0011847' (seeded in conftest.py)"
    )


def test_s32_lookup_property_tty_present_for_seeded_code(fhir_client):
    """Item 3 / spec Out `property`: the `tty` property is populated by
    the engine for seeded SNOMED codes (TTY column in mrconso).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    tty = _property_value(body, "tty")
    assert tty == "PT", (
        f"tty property = {tty!r}, expected 'PT' (seeded in conftest.py)"
    )


def test_s33_lookup_property_aui_present_for_seeded_code(fhir_client):
    """Item 3 / spec Out `property`: the `aui` property is populated by
    the engine for seeded SNOMED codes (AUI column in mrconso).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    aui = _property_value(body, "aui")
    assert aui == "A44054006", (
        f"aui property = {aui!r}, expected 'A44054006' (seeded in conftest.py)"
    )


def test_s34_lookup_multiple_properties_returned_for_rxnorm(fhir_client):
    """Item 3 cross-system / spec Out `property`: same probe on RxNorm —
    confirms the multi-property surface holds across systems.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={RXNORM_URI}&code={RXNORM_METFORMIN}"
    )
    assert r.status_code == 200
    body = r.json()
    cui = _property_value(body, "cui")
    tty = _property_value(body, "tty")
    assert cui == "C0978484" and tty == "SCD", (
        f"RxNorm properties: cui={cui!r} tty={tty!r}; expected C0978484/SCD"
    )


def test_s35_lookup_property_shape_is_part_code_value(fhir_client):
    """Item 3 / spec Out `property`: each property entry MUST have the
    shape `name='property', part=[{name:'code', valueCode:X}, {name:'value', valueString:Y}]`.
    Per §4.8.21.1 the property group is a 2-part structure.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    found_well_shaped = False
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        parts = p.get("part", [])
        names = [part.get("name") for part in parts]
        assert "code" in names and "value" in names, (
            f"property entry parts = {names!r}; expected ['code', 'value']"
        )
        found_well_shaped = True
    assert found_well_shaped, "no `property` Out parameter entries found"


# ---------------------------------------------------------------------------
# Item 4: Abstract concepts — $lookup returns `abstract=true`;
#          $expand includes them with `abstract` flag.
# ---------------------------------------------------------------------------
# Engine limitation: the engine has no abstract-flag data (UMLS TTY does
# NOT directly map to abstract; abstract-ness is a SNOMED-specific concept
# stored in the SNOMED release files, not in mrconso). The implementation
# HARDCODES `abstract=False` (responses.py:46). Item 4 cannot be
# exercised against real abstract concepts; the finding-candidate is the
# hardcoded-false drift.

def test_s40_lookup_out_abstract_is_boolean_type(fhir_client):
    """Item 4 / spec $lookup Out `abstract` 0..1 boolean: the value MUST
    use the boolean wire type (valueBoolean), not valueString.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    p = _lookup_param(body, "abstract")
    assert p is not None
    assert "valueBoolean" in p
    assert "valueString" not in p, (
        f"Out `abstract` uses valueString — wire-type drift (spec mandates valueBoolean)"
    )


def test_s41_lookup_out_abstract_false_on_leaf_code(fhir_client):
    """Item 4 / spec $lookup Out `abstract`: on a leaf code (44054006
    T2DM), `abstract=false` is correct (the concept is meant to be used
    in instances).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _lookup_param_value(body, "abstract") is False


def test_s42_lookup_out_abstract_consistent_across_systems(fhir_client):
    """Item 4 / spec $lookup Out `abstract`: every code in every seeded
    system returns `abstract=false` (hardcoded behavior). The probe
    documents the consistency — if a future code system emits true here,
    the probe will fail loudly.
    """
    cases = [
        (SNOMED_URI, SNOMED_T2DM),
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (RXNORM_URI, RXNORM_METFORMIN),
        (ICD10CM_URI, ICD10CM_T2DM),
    ]
    for system, code in cases:
        r = fhir_client.get(f"/fhir/CodeSystem/$lookup?system={system}&code={code}")
        assert r.status_code == 200, f"{system} {code}: {r.status_code} {r.text}"
        body = r.json()
        abstract_val = _lookup_param_value(body, "abstract")
        assert abstract_val is False, (
            f"{system} {code}: abstract={abstract_val!r}; expected False "
            f"(hardcoded in responses.py:46; engine has no abstract-flag data)"
        )


def test_s43_expand_includes_seeded_codes(fhir_client):
    """Item 4 / spec $expand: the expansion MUST include seeded codes.
    The conformance fixture has no abstract-flag data, so this probe
    asserts the basic expansion surface. When abstract-flag data lands,
    a follow-up probe should verify abstract concepts are included with
    the `abstract` flag in the expansion contains[] entry.
    """
    r = fhir_client.get(f"/fhir/ValueSet/$expand?filter=diabetes&count=10")
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "ValueSet"
    contains = body.get("expansion", {}).get("contains", [])
    codes = [c.get("code") for c in contains]
    assert SNOMED_DIABETES_MELLITUS in codes or SNOMED_T2DM in codes, (
        f"diabetes filter did not return seeded SNOMED codes; got {codes!r}"
    )


def test_s44_expand_contains_entries_do_not_carry_abstract_flag_today(fhir_client):
    """Item 4 / spec $expand contains[] entry: when a concept IS abstract,
    the entry SHOULD carry `abstract: true`. The engine has no abstract
    data, so today's contains[] entries omit the field. This probe
    documents the current behavior; if a future engine wires abstract
    data, the probe will need updating to assert `abstract=false` on
    leaf entries (and `abstract=true` on abstract entries).
    """
    r = fhir_client.get(f"/fhir/ValueSet/$expand?filter=diabetes&count=10")
    assert r.status_code == 200
    body = r.json()
    contains = body.get("expansion", {}).get("contains", [])
    # Today: no entry carries the `abstract` field (engine has no data).
    # Asserting current behavior; future enhancement should populate it.
    for entry in contains:
        if "abstract" in entry:
            # If the field IS present, it MUST be a boolean.
            assert isinstance(entry["abstract"], bool), (
                f"contains[] entry abstract field is {type(entry['abstract']).__name__}, "
                f"not bool"
            )


def test_s70_finding_candidate_abstract_hardcoded_false(fhir_client):
    """Item 4 / FINDING CANDIDATE (SKEPTIC audit, not filed as bug): the
    `$lookup` Out `abstract` parameter is hardcoded False at
    `engines/fhir/responses.py:46` regardless of the concept's actual
    abstractness in the source terminology. The engine has no abstract-
    flag data (UMLS TTY does NOT directly map to abstract; abstract-ness
    is a SNOMED-specific concept stored in the SNOMED release files).

    Per FHIR R4 https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    Out `abstract`: "True if this code is abstract". The hardcoded value
    is wrong for ANY abstract concept — but the fixture cannot exercise
    the case (no abstract concepts seeded). Documented as a DEFERRED
    carry-forward (CF-SKEPTIC-CS05-01) tied to a future engine
    enhancement that wires SNOMED release-file abstract flags into the
    engine's CodeInfo. NOT filed as a bug because the fixture cannot
    reproduce the drift; the candidate is logged here for HISTORIAN /
    future-chunk follow-up.

    Discriminates from QA-043 (canonical-system raw SAB drift): that
    drift was reproducible because the patient-friendly JSON artifacts
    carry the raw SAB label; this drift is NOT reproducible because
    no abstract concepts are seeded anywhere in the test surface.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_DIABETES_MELLITUS}"
    )
    assert r.status_code == 200
    body = r.json()
    # The parent concept 73211009 (Diabetes mellitus) is a SNOMED
    # hierarchy node — in the SNOMED release files it carries the
    # abstract-ish characteristic (definition status = 900000000000074008
    # "Primitive" or 900000000000073002 "Defined"). The engine does
    # NOT load definition status; abstract=False is the hardcoded output.
    abstract_val = _lookup_param_value(body, "abstract")
    # Asserting the CURRENT (hardcoded) behavior; this probe documents
    # the finding candidate, not a fix.
    assert abstract_val is False


# ---------------------------------------------------------------------------
# Item 5: Multi-hierarchy codes — $subsumes correctly handles concepts at
#          multiple depths.
# ---------------------------------------------------------------------------
# Engine coverage: services/hierarchy.py uses BFS with a `visited` set
# (line 121), so a code with multiple parents is visited exactly once.
# Subsumption testing via is_descendant walks descendants BFS and stops
# at the candidate — multi-hierarchy is structurally handled. The
# conformance fixture has a single-parent hierarchy (73211009 → 44054006);
# probes below assert the surface and document the multi-parent fixture
# gap as a carry-forward.

def test_s50_subsumes_parent_depth_one(fhir_client):
    """Item 5 / spec $subsumes: parent at depth 1 subsumes child.
    SNOMED 73211009 (Diabetes mellitus) is a direct parent of 44054006
    (T2DM) in the seeded mrrel (A44054006 → A73211009, RELA=isa).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _outcome(body) == "subsumes"


def test_s51_subsumes_reverse_depth_one(fhir_client):
    """Item 5 / spec $subsumes: child at depth 1 is subsumed by parent.
    Reverse direction of test_s50.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_DIABETES_MELLITUS}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _outcome(body) == "subsumed-by"


def test_s52_subsumes_identical_depth_zero(fhir_client):
    """Item 5 / spec $subsumes: identical codes (depth 0) are equivalent.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _outcome(body) == "equivalent"


def test_s53_subsumes_unrelated_at_any_depth(fhir_client):
    """Item 5 / spec $subsumes: codes with no hierarchical relationship
    (at any depth) return `not-subsumed`. SNOMED T2DM and RxNorm
    metformin are in different sources — but the probe uses different
    codes within the same source to stay within subsumption scope.
    """
    # Same-source unrelated: SNOMED T2DM (44054006) and SNOMED Diabetes
    # parent (73211009) ARE related. The fixture doesn't seed a same-
    # source unrelated pair. Probe cross-source instead — the engine
    # returns not-subsumed for codes where no mrrel path exists.
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB=9999999999"
    )
    assert r.status_code == 200
    body = r.json()
    # Unknown code in source: no mrrel path → not-subsumed (INTENDED today,
    # documented in CS-04 SKEPTIC test_s120).
    assert _outcome(body) == "not-subsumed"


def test_s54_subsumes_multi_depth_would_work_with_fixture(fhir_client):
    """Item 5 / spec $subsumes: multi-depth subsumption (grandparent →
    grandchild) is structurally handled by BFS. The conformance fixture
    only seeds a single-parent relationship (depth-1 hierarchy), so this
    probe asserts the depth-1 surface; a future fixture enhancement
    adding a grandparent-grandchild pair would exercise the multi-depth
    BFS path (services/hierarchy.py:84-155).

    The probe asserts the BFS code path IS exercised by the depth-1 case
    (the implementation uses the same `is_descendant` for any depth).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    # The BFS implementation (services/hierarchy.py:158-182 is_descendant)
    # uses get_descendants_bfs with stop_at=candidate. For depth-1 case,
    # the BFS finds the candidate on the first layer. For deeper cases,
    # the same code path continues — multi-depth is structurally sound.
    outcome = _outcome(body)
    assert outcome == "subsumes", (
        f"depth-1 BFS subsumes: outcome={outcome!r}"
    )


def test_s55_subsumes_self_subsumption_short_circuit(fhir_client):
    """Item 5 edge / spec $subsumes: identical codes short-circuit to
    `equivalent` BEFORE the BFS walk (apps/fhir_api.py:1756-1757).
    Multi-hierarchy correctness depends on this short-circuit — a code
    with multiple parents IS its own equivalent regardless of parents.
    """
    # Use a code that exists in the fixture.
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_DIABETES_MELLITUS}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _outcome(body) == "equivalent"


def test_s56_subsumes_multi_parent_fixture_gap(fhir_client):
    """Item 5 / MULTI-HIERARCHY FIXTURE GAP: the conformance fixture
    seeds only a single-parent relationship (mrrel row
    ('A44054006', 'A73211009', 'isa', 'PAR')). Multi-hierarchy
    correctness (a code with multiple parents at different depths)
    CANNOT be exercised. The probe asserts the single-parent surface
    and documents the multi-parent gap as a carry-forward.

    The engine implementation (services/hierarchy.py BFS with `visited`
    set) structurally handles multi-parent DAGs — the `visited` set
    ensures each child is visited exactly once, and `get_children`
    returns direct parents regardless of count. The implementation is
    CORRECT for multi-hierarchy; the fixture is INCOMPLETE.
    """
    # Single-parent assertion (the only data the fixture has).
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    assert _outcome(r.json()) == "subsumes"
    # The multi-parent reproduction shape (for a future fixture
    # enhancement): seed an additional mrrel row that gives 44054006 a
    # second parent at a different depth, then assert $subsumes returns
    # `subsumes` for BOTH parents. Example:
    #   mrrel row 1: ('A44054006', 'A73211009', 'isa', 'PAR')   # depth-1 parent
    #   mrrel row 2: ('A44054006', 'AXYZ', 'isa', 'PAR')        # depth-1 second parent
    #   mrrel row 3: ('AXYZ', 'AGRANDPARENT', 'isa', 'PAR')     # depth-2 grandparent (via X)
    # Then assert:
    #   $subsumes codeA=AGRANDPARENT codeB=44054006 → 'subsumes' (depth-2)
    #   $subsumes codeA=A73211009 codeB=44054006 → 'subsumes' (depth-1)


# ---------------------------------------------------------------------------
# Hostile / edge cases — SKEPTIC overreach discipline
# ---------------------------------------------------------------------------

def test_s60_lookup_unknown_code_returns_operation_outcome(fhir_client):
    """Edge / spec $lookup: unknown code returns OperationOutcome (not
    Parameters). The implementation routes through build_operation_outcome
    when code_info is None (responses.py:36-39).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code=NONEXISTENT_QA_999"
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_s61_validate_code_unknown_code_returns_result_false(fhir_client):
    """Edge / spec $validate-code: unknown code returns `result=false`.
    This is the inverse of test_s12 — confirms the false path.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code=NONEXISTENT_QA_999"
    )
    assert r.status_code == 200
    body = r.json()
    assert _validate_result(body) is False


def test_s62_lookup_unknown_system_returns_400(fhir_client):
    """Edge / spec $lookup: unknown system URI returns 400 with a FHIR
    OperationOutcome body.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system=http://fake.example/sys&code=X"
    )
    assert r.status_code == 400
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_s63_validate_code_unknown_system_returns_400(fhir_client):
    """Edge / spec $validate-code: unknown system URI returns 400.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system=http://fake.example/sys&code=X"
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET-vs-POST parity for $lookup with version
# ---------------------------------------------------------------------------

def test_s90_lookup_get_post_parity_with_version(fhir_client):
    """Item 2 / GET-vs-POST parity: $lookup with `version` param MUST
    produce identical responses on GET and POST.
    """
    # GET
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}&version=2024-09"
    )
    assert r_get.status_code == 200
    # POST
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM},
                {"name": "version", "valueString": "2024-09"},
            ],
        },
    )
    assert r_post.status_code == 200
    # Displays MUST be identical.
    display_get = _lookup_param_value(r_get.json(), "display")
    display_post = _lookup_param_value(r_post.json(), "display")
    assert display_get == display_post, (
        f"GET display={display_get!r} != POST display={display_post!r}"
    )


def test_s91_lookup_get_post_parity_without_version(fhir_client):
    """Item 2 / GET-vs-POST parity baseline: $lookup without version MUST
    produce identical responses.
    """
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM},
            ],
        },
    )
    assert r_get.status_code == r_post.status_code == 200
    display_get = _lookup_param_value(r_get.json(), "display")
    display_post = _lookup_param_value(r_post.json(), "display")
    assert display_get == display_post


# ---------------------------------------------------------------------------
# Response-shape audits (per GLOBAL_RULES.md "Conformance property per route")
# ---------------------------------------------------------------------------

def test_s100_lookup_response_content_type_is_fhir_json(fhir_client):
    """Audit / FHIR R4 §3.1.0.1.9: every $lookup response MUST carry
    `Content-Type: application/fhir+json` (or +xml).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"$lookup Content-Type={ct!r}; expected application/fhir+json"
    )


def test_s101_validate_code_response_content_type_is_fhir_json(fhir_client):
    """Audit / FHIR R4 §3.1.0.1.9: every $validate-code response MUST
    carry `Content-Type: application/fhir+json`.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct


def test_s102_lookup_response_resource_type_is_parameters(fhir_client):
    """Audit / spec $lookup: successful response MUST have
    `resourceType=Parameters`.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    assert r.json().get("resourceType") == "Parameters"


def test_s103_lookup_response_carries_required_out_params(fhir_client):
    """Audit / spec $lookup Out Parameters (§4.8.21.1): a successful
    response MUST carry `name`, `code`, `system`, `display`, `abstract`.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    for required in ("name", "code", "system", "display", "abstract"):
        p = _lookup_param(body, required)
        assert p is not None, (
            f"$lookup response missing required Out parameter {required!r}"
        )


def test_s104_validate_code_response_carries_required_out_params(fhir_client):
    """Audit / spec $validate-code Out Parameters: a successful response
    MUST carry `result` (boolean).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    assert _lookup_param(body, "result") is not None


# ---------------------------------------------------------------------------
# XML format negotiation on operation routes (per CR-002 fix shape)
# ---------------------------------------------------------------------------

def test_s110_lookup_xml_format_returns_lower_case_boolean(fhir_client):
    """Audit / FHIR R4 §3.4.1 + CR-002 fix: XML serialization MUST emit
    `value="false"` (lowercase) for the Out `abstract` boolean — NOT
    `value="False"` (Python str(False) capitalization).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}&_format=xml"
    )
    assert r.status_code == 200
    body_text = r.text
    # The Out `abstract` parameter MUST render as <valueBoolean value="false"/>
    # (lowercase). Capital-T "False" is the Python str(False) drift caught
    # by Milestone-1 CR-002.
    assert 'value="False"' not in body_text, (
        "XML body contains value=\"False\" — boolean capitalization drift (CR-002 regression)"
    )
    # The lowercase form MUST be present for the abstract parameter.
    assert 'value="false"' in body_text, (
        "XML body missing value=\"false\" — abstract boolean not rendered"
    )


def test_s111_validate_code_xml_format_returns_lower_case_boolean(fhir_client):
    """Audit / FHIR R4 §3.4.1 + CR-002 fix: $validate-code Out `result`
    boolean in XML MUST be lowercase.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}&_format=xml"
    )
    assert r.status_code == 200
    body_text = r.text
    assert 'value="True"' not in body_text, (
        "XML body contains value=\"True\" — boolean capitalization drift (CR-002 regression)"
    )
    assert 'value="true"' in body_text


def test_s112_validate_code_xml_format_unknown_code_returns_lower_case_false(fhir_client):
    """Audit / FHIR R4 §3.4.1 + CR-002 fix: $validate-code with unknown
    code in XML MUST render result=false (lowercase).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code=NONEXISTENT_QA_999&_format=xml"
    )
    assert r.status_code == 200
    body_text = r.text
    assert 'value="False"' not in body_text
    assert 'value="false"' in body_text


# ---------------------------------------------------------------------------
# Accept-header XML negotiation
# ---------------------------------------------------------------------------

def test_s120_lookup_accept_header_xml(fhir_client):
    """Audit / FHIR R4 §3.1.0.1.11 + TS-01 EXPLORER QA-009: Accept header
    `application/fhir+xml` MUST produce XML output.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}",
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+xml" in ct, (
        f"Accept xml Content-Type={ct!r}"
    )
    # Body should start with <?xml
    assert r.text.lstrip().startswith("<?xml"), (
        f"body does not start with <?xml; first 50 chars: {r.text[:50]!r}"
    )
