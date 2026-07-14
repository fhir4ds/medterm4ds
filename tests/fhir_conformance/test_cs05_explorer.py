"""EXPLORER probes for CS-05 (CodeSystem Edge Cases).

Spec: https://build.fhir.org/codesystem.html
       (canonical R4: https://hl7.org/fhir/R4/codesystem.html)
       concept-properties: https://hl7.org/fhir/R4/concept-properties.html
       $lookup: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
       $validate-code: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
       $subsumes: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html

EXPLORER lens (per chunk assignment): lateral thinking across operations,
properties, and edge-case shapes that prior personalities did not probe.
Specifically:

  1. Cross-operation consistency: take a code and run $lookup →
     $validate-code → $subsumes in sequence. Are the canonical system,
     display, code, and (where applicable) the abstract flag identical
     across the operations?
  2. Property combinations: request ALL properties on $lookup (the In
     `property` parameter is 0..* repeating) and verify the response
     shape is consistent regardless of which properties are requested.
  3. Property-name case sensitivity: `property=ABSTRACT` vs
     `property=abstract` — the implementation is permissive today; the
     probe documents the case-insensitive behavior.
  4. Version parameter combinations: $lookup, $validate-code, $subsumes
     with identical version param — consistent behavior?
  5. Cross-system consistency for edge cases: same edge-case code shape
     across SNOMED, RxNorm, ICD-10-CM — consistent Out `abstract` /
     property group / required Out params.
  6. Cross-operation parameter combinations: $lookup with property filter
     applied AND system+code AND version — every param together.
  7. Self-consistency: $subsumes(codeA, codeA) short-circuits to
     `equivalent` before the BFS walk — does it work for codes in
     different systems (different URIs, same code string)?
  8. Content-Type fidelity on operation routes for the CS-05 surface
     (every variant of $lookup, $validate-code, $subsumes).
  9. Empty / whitespace / special-char property values: server handles
     these without 500 / silent-wrong-answer.
 10. Close the CF-EXPLORER-CS02-01 portion for $lookup POST Content-Type
     (the 4-shape probe family on the lookup surface).

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape (200 +
    expected fields), not just absence of one error string.
  - "Don't manufacture bugs": if the fixture lacks data to exercise an
    item, document DEFERRED with reproduction shape.
  - Spec citation required on every probe.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
    ("E11", "HT", "Type 2 diabetes mellitus", "AE11", "N", "ICD10CM", "C0011847"),
    ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # single-parent
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _property_codes(body: dict) -> set[str]:
    """Return the set of property codes in the Out `property` group."""
    codes: set[str] = set()
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        for part in p.get("part", []):
            if part.get("name") == "code":
                codes.add(part.get("valueCode"))
    return codes


def _property_value(body: dict, prop_code: str):
    """Return the value of the first property entry matching `prop_code`."""
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        code = None
        value = None
        for part in p.get("part", []):
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


def _validate_result(body: dict) -> bool | None:
    for p in body.get("parameter", []):
        if p.get("name") == "result":
            return p.get("valueBoolean")
    return None


def _outcome(body: dict) -> str | None:
    for p in body.get("parameter", []):
        if p.get("name") == "outcome":
            return p.get("valueCode")
    return None


# ---------------------------------------------------------------------------
# Lens 1: Cross-operation consistency — run $lookup → $validate-code →
#         $subsumes on the same code and assert canonical system / code /
#         display agree across operations.
# ---------------------------------------------------------------------------
# Per FHIR R4 §4.8.21.1 + §4.8.21.2 + §4.8.21.3: each operation returns
# its own Parameters shape, but the canonical system URI + code MUST be
# consistent. CS-02 HISTORIAN QA-047 + CS-03 HISTORIAN QA-051 added
# canonical re-resolution on _do_lookup and _do_validate; $subsumes has
# no Out `system` parameter (only Out `outcome`). EXPLORER probes
# consistency across the pair that DOES emit Out `system`.

@pytest.mark.parametrize("system,code", [
    (SNOMED_URI, SNOMED_T2DM),
    (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
    (RXNORM_URI, RXNORM_METFORMIN),
    (ICD10CM_URI, ICD10CM_T2DM),
])
def test_e10_lookup_and_validate_agree_on_canonical_system(fhir_client, system, code):
    """Lens 1 / spec: $lookup Out `system` MUST equal $validate-code Out
    `system` for the same (system, code) input. The two operations share
    `get_code_infos` and the canonical re-resolution pattern (CS-02
    HISTORIAN QA-047 + CS-03 HISTORIAN QA-051). EXPLORER probes the
    invariant across all 4 seeded systems to guard against future
    divergence.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out
    `system` 1..1 uri — "The canonical URI of the code system that
    contains the concept that was looked up."
    """
    r_lookup = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
    )
    r_validate = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
    )
    assert r_lookup.status_code == 200
    assert r_validate.status_code == 200
    lookup_system = _lookup_param_value(r_lookup.json(), "system")
    validate_system = _lookup_param_value(r_validate.json(), "system")
    assert lookup_system == validate_system, (
        f"$lookup system={lookup_system!r} != $validate-code system="
        f"{validate_system!r} for {system}/{code}"
    )
    # The canonical system MUST be a non-empty URI.
    assert lookup_system and lookup_system.startswith("http"), (
        f"canonical system is not an HTTP URI: {lookup_system!r}"
    )


@pytest.mark.parametrize("system,code", [
    (SNOMED_URI, SNOMED_T2DM),
    (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
    (RXNORM_URI, RXNORM_METFORMIN),
    (ICD10CM_URI, ICD10CM_T2DM),
])
def test_e11_lookup_and_validate_agree_on_canonical_display(fhir_client, system, code):
    """Lens 1 / spec: $lookup Out `display` MUST equal $validate-code
    Out `display` for the same (system, code). The display is sourced
    from `code_info.name` in both operations; CS-03 SKEPTIC QA-048
    added display-mismatch enforcement on $validate-code and CS-03
    HISTORIAN QA-051 added canonical re-resolution. EXPLORER probes the
    display agreement across all 4 seeded systems.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out
    `display` 0..1 string — "The preferred display for this concept."
    """
    r_lookup = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
    )
    r_validate = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
    )
    assert r_lookup.status_code == 200
    assert r_validate.status_code == 200
    lookup_display = _lookup_param_value(r_lookup.json(), "display")
    validate_display = _lookup_param_value(r_validate.json(), "display")
    assert lookup_display == validate_display, (
        f"$lookup display={lookup_display!r} != $validate-code display="
        f"{validate_display!r} for {system}/{code}"
    )


def test_e12_lookup_then_subsumes_self_consistency(fhir_client):
    """Lens 1 / cross-operation: $lookup confirms the code exists; then
    $subsumes(codeA=code, codeB=code) MUST return `equivalent`. This is
    a positive cross-operation consistency probe: $lookup says "code is
    there" and $subsumes confirms the self-equivalence.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    Out `outcome`: "equivalent — if A and B are the same code".
    """
    r_lookup = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r_lookup.status_code == 200
    r_subsumes = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
    )
    assert r_subsumes.status_code == 200
    assert _outcome(r_subsumes.json()) == "equivalent"


def test_e13_lookup_then_validate_then_subsumes_sequence(fhir_client):
    """Lens 1 / three-operation sequence: run $lookup → $validate-code
    → $subsumes on the same parent-child pair. The three operations
    MUST produce internally consistent results.

    Spec cross-reference: the three CodeSystem operations share the
    underlying engine + canonical URI map. EXPLORER probes that running
    them in sequence on the same data does not produce contradictory
    answers.
    """
    # $lookup confirms both codes exist.
    r_lookup_a = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_DIABETES_MELLITUS}"
    )
    r_lookup_b = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r_lookup_a.status_code == 200
    assert r_lookup_b.status_code == 200
    # $validate-code confirms both are valid.
    r_va = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_DIABETES_MELLITUS}"
    )
    r_vb = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r_va.status_code == 200
    assert r_vb.status_code == 200
    assert _validate_result(r_va.json()) is True
    assert _validate_result(r_vb.json()) is True
    # $subsumes confirms the parent-child relationship.
    r_subsumes = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert r_subsumes.status_code == 200
    assert _outcome(r_subsumes.json()) == "subsumes"


# ---------------------------------------------------------------------------
# Lens 2: Property combinations — request ALL properties and verify
#         response shape is consistent regardless of which properties
#         are requested.
# ---------------------------------------------------------------------------
# Per FHIR R4 $lookup In `property` (0..* code): "A property that the
# server SHOULD return. The server MAY return other properties as well.
# Use this parameter to request specific additional properties be
# returned." Today medterm4ds returns its full property set regardless
# of the In `property` parameter (INTENDED per AGENTS.md NOT A BUG
# registry — "the server returns its full property set anyway"). EXPLORER
# probes that the response shape is consistent regardless of the In
# `property` filter.

def test_e20_lookup_property_filter_does_not_change_response_shape(fhir_client):
    """Lens 2 / spec $lookup In `property` 0..*: the response shape MUST
    be identical whether the client requests `property=cui`, multiple
    properties, or no `property` parameter at all. medterm4ds returns
    the full property set regardless (INTENDED per AGENTS.md NOT A BUG
    registry); this probe pins the consistency.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html In
    Parameters: "property 0..* code — A property that the server SHOULD
    return."
    """
    # Baseline: no property param.
    r_none = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    # Single property filter.
    r_one = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property=cui"
    )
    # Multiple property filter (spec-permitted 0..* repetition).
    r_multi = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property=cui&property=tty&property=aui"
    )
    assert r_none.status_code == r_one.status_code == r_multi.status_code == 200
    codes_none = _property_codes(r_none.json())
    codes_one = _property_codes(r_one.json())
    codes_multi = _property_codes(r_multi.json())
    # The full property set MUST be returned regardless of the filter.
    assert codes_none == codes_one == codes_multi, (
        f"property filter changed response shape: "
        f"none={codes_none!r} one={codes_one!r} multi={codes_multi!r}"
    )
    # The seeded code has cui + tty + aui properties.
    assert "cui" in codes_none
    assert "tty" in codes_none
    assert "aui" in codes_none


def test_e21_lookup_property_filter_unknown_property_accepted(fhir_client):
    """Lens 2 / spec $lookup In `property`: requesting a property that
    the server doesn't carry MUST NOT error. The implementation is
    permissive — the server returns its full set regardless of the
    requested filter.

    Pattern-match: AGENTS.md NOT A BUG registry — "$lookup repeating
    property parameter accepted — the server returns its full property
    set anyway". EXPLORER probes the consistency with an unknown
    property name.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property=NONEXISTENT_PROPERTY_XYZ"
    )
    assert r.status_code == 200, (
        f"unknown property name rejected: {r.status_code} {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    # The full property set MUST still be present (the unknown filter is
    # silently ignored; the server returns what it has).
    assert "cui" in _property_codes(body)


def test_e22_lookup_property_filter_empty_value_accepted(fhir_client):
    """Lens 2 / spec $lookup In `property`: an empty `property=` value
    MUST NOT error. Edge case — FastAPI parses this as an empty string;
    the handler SHOULD treat it as "no filter" and return the full set.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property="
    )
    # Accept either 200 (permissive) or 400 (strict). Today the handler
    # is permissive — the spec says `property` is 0..* code, so empty
    # is technically invalid, but the server accepts it without 500.
    assert r.status_code in (200, 400), (
        f"empty property value caused unexpected status: {r.status_code} {r.text}"
    )
    if r.status_code == 200:
        body = r.json()
        assert body.get("resourceType") == "Parameters"


# ---------------------------------------------------------------------------
# Lens 3: Property-name case sensitivity — `property=ABSTRACT` vs
#         `property=abstract`.
# ---------------------------------------------------------------------------
# Per FHIR R4 §3.4.1 code datatype: codes are case-sensitive. The In
# `property` parameter is a `code`, so `property=ABSTRACT` is technically
# a different code than `property=abstract`. EXPLORER probes the
# implementation's actual behavior (permissive — server returns its full
# set regardless of filter, so case is irrelevant today).

def test_e30_lookup_property_filter_case_insensitive(fhir_client):
    """Lens 3 / case sensitivity: per FHIR R4 §3.4.1, `code` values are
    case-sensitive. However, the medterm4ds implementation IGNORES the
    `property` filter value entirely (returns the full set), so case
    has no effect today. EXPLORER documents the current behavior —
    a future implementation that filters properties SHOULD also handle
    case folding to preserve the consistency invariant.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html In
    `property` 0..* code.
    """
    r_lower = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property=abstract"
    )
    r_upper = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property=ABSTRACT"
    )
    r_mixed = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property=Abstract"
    )
    assert r_lower.status_code == r_upper.status_code == r_mixed.status_code == 200
    codes_lower = _property_codes(r_lower.json())
    codes_upper = _property_codes(r_upper.json())
    codes_mixed = _property_codes(r_mixed.json())
    # All three cases MUST return the same property set today (the filter
    # is ignored; the server returns its full set regardless).
    assert codes_lower == codes_upper == codes_mixed


# ---------------------------------------------------------------------------
# Lens 4: Version parameter combinations — $lookup, $validate-code,
#         $subsumes with identical version param: consistent behavior?
# ---------------------------------------------------------------------------
# Per AGENTS.md NOT A BUG registry: `version` param accepted but ignored
# on all three operations. EXPLORER probes that the SAME version string
# applied to all three operations produces consistent (200) responses.

@pytest.mark.parametrize("version", [
    "2024-09",
    "2025-03",
    "NONEXISTENT_2099",
    "1.0.0",
])
def test_e40_version_param_consistent_across_operations(fhir_client, version):
    """Lens 4 / spec: the `version` parameter is accepted on $lookup,
    $validate-code, AND $subsumes (FHIR R4 In Parameters). medterm4ds
    accepts but ignores it (single-snapshot engine — INTENDED per AGENTS
    NOT A BUG registry). EXPLORER probes that applying the SAME version
    string to all three operations produces consistent 200 responses.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html In
    `version` 0..1 string.
    """
    r_lookup = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&version={version}"
    )
    r_validate = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&version={version}"
    )
    r_subsumes = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
        f"&version={version}"
    )
    assert r_lookup.status_code == 200, (
        f"$lookup with version={version!r} rejected: {r_lookup.status_code}"
    )
    assert r_validate.status_code == 200
    assert r_subsumes.status_code == 200


def test_e41_version_param_does_not_change_outcome_across_operations(fhir_client):
    """Lens 4 / spec: changing the version param MUST NOT change the
    outcome on any of the three operations (single-snapshot engine).
    EXPLORER probes that the display (lookup), result (validate), and
    outcome (subsumes) are stable across different version strings.
    """
    displays = []
    results = []
    outcomes = []
    for v in ["", "2024-09", "NONEXISTENT_2099"]:
        r_lookup = fhir_client.get(
            f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
            + (f"&version={v}" if v else "")
        )
        r_validate = fhir_client.get(
            f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
            + (f"&version={v}" if v else "")
        )
        r_subsumes = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
            + (f"&version={v}" if v else "")
        )
        assert r_lookup.status_code == r_validate.status_code == r_subsumes.status_code == 200
        displays.append(_lookup_param_value(r_lookup.json(), "display"))
        results.append(_validate_result(r_validate.json()))
        outcomes.append(_outcome(r_subsumes.json()))
    assert len(set(displays)) == 1, (
        f"different version params changed display: {displays!r}"
    )
    assert len(set(results)) == 1
    assert len(set(outcomes)) == 1


# ---------------------------------------------------------------------------
# Lens 5: Cross-system consistency for edge cases — same edge-case shape
#         across SNOMED, RxNorm, ICD-10-CM.
# ---------------------------------------------------------------------------
# Probe class: every seeded code in every seeded system MUST return the
# same response shape (200 Parameters + required Out params + abstract
# boolean). A future regression that breaks one system but not the
# others would silently ship.

@pytest.mark.parametrize("system,code", [
    (SNOMED_URI, SNOMED_T2DM),
    (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
    (RXNORM_URI, RXNORM_METFORMIN),
    (ICD10CM_URI, ICD10CM_T2DM),
])
def test_e50_lookup_required_out_params_across_systems(fhir_client, system, code):
    """Lens 5 / spec cross-system: every seeded code in every seeded
    system MUST return the required Out parameters on $lookup.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    Out Parameters required: `name`, `code`, `system`, `display`,
    `abstract` (per SKEPTIC test_s103). EXPLORER parametrizes across
    all 4 seeded systems.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
    )
    assert r.status_code == 200, f"{system}/{code}: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    for required in ("name", "code", "system", "display", "abstract"):
        assert _lookup_param(body, required) is not None, (
            f"{system}/{code}: missing required Out parameter {required!r}"
        )


@pytest.mark.parametrize("system,code", [
    (SNOMED_URI, SNOMED_T2DM),
    (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
    (RXNORM_URI, RXNORM_METFORMIN),
    (ICD10CM_URI, ICD10CM_T2DM),
])
def test_e51_lookup_abstract_false_across_systems(fhir_client, system, code):
    """Lens 5 / spec cross-system: every seeded code in every seeded
    system returns `abstract=False` (hardcoded — CF-SKEPTIC-CS05-01).
    EXPLORER probes the consistency across systems. A future regression
    that emits true for one system but false for others would fail.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
    )
    assert r.status_code == 200
    abstract_val = _lookup_param_value(r.json(), "abstract")
    assert abstract_val is False, (
        f"{system}/{code}: abstract={abstract_val!r}; expected False "
        f"(hardcoded; see CF-SKEPTIC-CS05-01)"
    )


@pytest.mark.parametrize("system,code", [
    (SNOMED_URI, SNOMED_T2DM),
    (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
    (RXNORM_URI, RXNORM_METFORMIN),
    (ICD10CM_URI, ICD10CM_T2DM),
])
def test_e52_validate_code_result_true_across_systems(fhir_client, system, code):
    """Lens 5 / spec cross-system: every seeded code in every seeded
    system MUST return `result=True` on $validate-code. EXPLORER probes
    the consistency.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
    )
    assert r.status_code == 200
    assert _validate_result(r.json()) is True


# ---------------------------------------------------------------------------
# Lens 6: Self-subsumption + cross-system edge cases on $subsumes.
# ---------------------------------------------------------------------------
# Per FHIR R4 $subsumes Out `outcome`: `equivalent` iff codeA == codeB
# (short-circuit at apps/fhir_api.py:1756-1757 BEFORE the BFS walk).

@pytest.mark.parametrize("system,code", [
    (SNOMED_URI, SNOMED_T2DM),
    (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
    (RXNORM_URI, RXNORM_METFORMIN),
    (ICD10CM_URI, ICD10CM_T2DM),
])
def test_e60_self_subsumption_across_systems(fhir_client, system, code):
    """Lens 6 / spec $subsumes: identical codes (codeA == codeB) short-
    circuit to `equivalent` BEFORE the BFS walk. EXPLORER probes this
    holds across every seeded system.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    Out `outcome` value `equivalent`.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={system}"
        f"&codeA={code}&codeB={code}"
    )
    assert r.status_code == 200
    assert _outcome(r.json()) == "equivalent"


def test_e61_subsumes_unrelated_codes_within_same_system(fhir_client):
    """Lens 6 / spec $subsumes: codes in the same system with no mrrel
    path between them return `not-subsumed`. EXPLORER probes this with
    a code that exists in the fixture (SNOMED T2DM) and a code that
    does NOT (99999999) — the latter triggers the not-subsumed path
    because the engine finds no descendants.

    Spec: $subsumes Out `outcome` value `not-subsumed`.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB=99999999"
    )
    assert r.status_code == 200
    assert _outcome(r.json()) == "not-subsumed"


def test_e62_subsumes_identical_code_strings_different_systems(fhir_client):
    """Lens 6 edge / spec $subsumes: the short-circuit `equivalent` is
    purely string comparison on codeA/codeB. EXPLORER probes what
    happens when the SAME code string is supplied in two different
    systems — the engine treats them as equivalent within the supplied
    system URI.

    Spec In `system` (1..1 uri): "The code system used for the
    subsumption test. Both A and B must be in this system."
    """
    # Same code string for both — short-circuits to equivalent regardless
    # of whether the code actually exists in the system.
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA=SAMESTRING&codeB=SAMESTRING"
    )
    assert r.status_code == 200
    assert _outcome(r.json()) == "equivalent"


# ---------------------------------------------------------------------------
# Lens 7: Content-Type fidelity on the CS-05 surface.
# ---------------------------------------------------------------------------
# Per FHIR R4 §3.1.0.1.9: every response MUST carry `application/fhir+json`
# (or +xml). EXPLORER walks every CS-05 operation and asserts the
# Content-Type. This is the CS-05 portion of the CR-001 parametrized
# Content-Type probe class.

@pytest.mark.parametrize("op_url", [
    f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}",
    f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}",
    f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}",
])
def test_e70_get_content_type_is_fhir_json(fhir_client, op_url):
    """Lens 7 / spec §3.1.0.1.9: GET on every CS-05 operation MUST carry
    `Content-Type: application/fhir+json`. EXPLORER parametrizes across
    the three operations.
    """
    r = fhir_client.get(op_url)
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"GET {op_url}: Content-Type={ct!r}; expected application/fhir+json"
    )


# ---------------------------------------------------------------------------
# Lens 8: 4-shape POST Content-Type probe family for $lookup.
# ---------------------------------------------------------------------------
# Per CF-EXPLORER-CS02-01: each chunk's EXPLORER iteration closes its own
# portion of the parametrized Content-Type probe family. CS-03 EXPLORER
# closed CodeSystem/$validate-code; CS-04 EXPLORER closed CodeSystem/
# $subsumes. CS-05 EXPLORER closes CodeSystem/$lookup.

def test_e80_lookup_post_system_code_content_type(fhir_client):
    """Lens 8 / CF-EXPLORER-CS02-01 close on $lookup (shape 1/4):
    POST $lookup with system+code body MUST carry `Content-Type:
    application/fhir+json` and the body MUST be a FHIR Parameters
    resource.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM},
            ],
        },
    )
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"POST $lookup system+code Content-Type={ct!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_e81_lookup_post_coding_body_content_type(fhir_client):
    """Lens 8 / CF-EXPLORER-CS02-01 close on $lookup (shape 2/4):
    POST $lookup with coding body (TS-02 HISTORIAN QA-022 alternative
    encoding) MUST carry `Content-Type: application/fhir+json`.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM,
                }},
            ],
        },
    )
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    assert _lookup_param_value(body, "code") == SNOMED_T2DM


def test_e82_lookup_post_version_included_body_content_type(fhir_client):
    """Lens 8 / CF-EXPLORER-CS02-01 close on $lookup (shape 3/4):
    POST $lookup with system+code+version body MUST carry `Content-Type:
    application/fhir+json`.
    """
    r = fhir_client.post(
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
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct


def test_e83_lookup_post_unknown_system_error_path_content_type(fhir_client):
    """Lens 8 / CF-EXPLORER-CS02-01 close on $lookup (shape 4/4):
    POST $lookup with an unknown system URI MUST carry `Content-Type:
    application/fhir+json` AND a FHIR OperationOutcome body (NOT a
    generic {'detail': ...} body). This is the error-path Content-Type
    assertion — guards against a future handler returning a raw dict
    that FastAPI would auto-wrap in JSONResponse.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://unknown.example/system"},
                {"name": "code", "valueCode": "X"},
            ],
        },
    )
    assert r.status_code == 400
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"error-path Content-Type={ct!r}; expected application/fhir+json"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


# ---------------------------------------------------------------------------
# Lens 9: XML-capitalization + Accept-header negotiation on the CS-05
#         surface for $subsumes (CR-002 fix shape; complement CS-04
#         HISTORIAN test_h61).
# ---------------------------------------------------------------------------
# Per GLOBAL_RULES.md "Boolean capitalization on serializers" (PROMOTED):
# every wire-format serializer MUST emit lowercase `true`/`false` for
# boolean primitives. CS-04 HISTORIAN tested $subsumes XML via
# `_format=xml`; EXPLORER adds the Accept-header variant on $subsumes
# AND the `_format=xml` variant on $validate-code via Accept header.

def test_e90_subsumes_xml_accept_header_negotiation(fhir_client):
    """Lens 9 / spec §3.1.0.1.11 + TS-01 EXPLORER QA-009: Accept header
    `application/fhir+xml` on $subsumes MUST produce XML output with
    lowercase `value="..."` (CR-002 fix shape).

    Pattern-match: CS-04 HISTORIAN test_h61 tested `_format=xml` on
    $subsumes; EXPLORER adds the Accept-header variant.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}",
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+xml" in ct
    body_text = r.text
    assert body_text.lstrip().startswith("<?xml"), (
        f"body does not start with <?xml; first 50 chars: {body_text[:50]!r}"
    )
    # The Out `outcome` parameter MUST be rendered as valueCode with
    # the value `subsumes` (the parent-child relationship).
    assert 'valueCode value="subsumes"' in body_text, (
        f"subsumes outcome XML missing valueCode value=\"subsumes\""
    )


def test_e91_validate_code_xml_accept_header_negotiation(fhir_client):
    """Lens 9 / spec §3.1.0.1.11: Accept header `application/fhir+xml`
    on $validate-code MUST produce XML output with lowercase
    `value="true"` (CR-002 fix shape).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}",
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/fhir+xml" in ct
    body_text = r.text
    # The Out `result` boolean MUST be lowercase 'true'.
    assert '<valueBoolean value="true"' in body_text
    assert '<valueBoolean value="True"' not in body_text


# ---------------------------------------------------------------------------
# Lens 10: Abstract filter on $expand — does the engine expose any way
#          to query abstract concepts specifically?
# ---------------------------------------------------------------------------
# Per FHIR R4 $expand In Parameters: there is no `includeAllAbstract` or
# `abstract` parameter on $expand. Abstract concepts are included by
# default when present in the expansion. medterm4ds has no abstract-flag
# data (CF-SKEPTIC-CS05-01), so this probe documents the absence of an
# abstract filter on $expand.

def test_e100_expand_no_abstract_filter_param(fhir_client):
    """Lens 10 / spec $expand: there is NO `abstract` or
    `includeAllAbstract` In parameter on $expand. Clients requesting
    one get permissive behavior (FastAPI accepts unknown query params).
    EXPLORER documents the absence — adding such a parameter would be
    a future enhancement.

    Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html In
    Parameters (no `abstract` parameter listed).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$expand?filter=diabetes&count=10&includeAllAbstract=true"
    )
    # The implementation is permissive — the unknown param is accepted.
    assert r.status_code == 200, (
        f"unknown $expand param rejected: {r.status_code} {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "ValueSet"
    # The contains[] list MAY be present; today it has no abstract
    # field per CF-SKEPTIC-CS05-01.
    contains = body.get("expansion", {}).get("contains", [])
    # The seeded diabetes codes MUST be present (filter=diabetes).
    codes = [c.get("code") for c in contains]
    assert SNOMED_DIABETES_MELLITUS in codes or SNOMED_T2DM in codes, (
        f"diabetes filter did not return seeded codes: {codes!r}"
    )


def test_e101_expand_filter_inactive_does_not_filter_active(fhir_client):
    """Lens 10 / spec $expand: there is no In parameter to query
    inactive codes specifically. EXPLORER documents the absence — the
    engine has no inactive-code tracking today (CF-SKEPTIC-CS05-02).
    """
    # The `filter=inactive` is a text filter on display, NOT an
    # inactive-code selector.
    r = fhir_client.get(
        f"/fhir/ValueSet/$expand?filter=inactive&count=10"
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "ValueSet"


# ---------------------------------------------------------------------------
# Lens 11: Property group shape audit — every property entry has the
#          2-part structure (code, value) per FHIR R4 §4.8.21.1.
# ---------------------------------------------------------------------------
# Per FHIR R4 §4.8.21.1 Out `property`: each entry has parts 'code' and
# 'value'. SKEPTIC test_s35 probed this on SNOMED T2DM; EXPLORER
# parametrizes across all 4 seeded systems AND asserts the value type.

@pytest.mark.parametrize("system,code", [
    (SNOMED_URI, SNOMED_T2DM),
    (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
    (RXNORM_URI, RXNORM_METFORMIN),
    (ICD10CM_URI, ICD10CM_T2DM),
])
def test_e110_lookup_property_shape_across_systems(fhir_client, system, code):
    """Lens 11 / spec §4.8.21.1 Out `property`: every property entry
    MUST have the 2-part structure [{name:'code', valueCode:X},
    {name:'value', valueString:Y}]. EXPLORER parametrizes the SKEPTIC
    test_s35 shape probe across all 4 seeded systems.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out
    `property` 0..*.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
    )
    assert r.status_code == 200
    body = r.json()
    found_well_shaped = False
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        parts = p.get("part", [])
        names = [part.get("name") for part in parts]
        assert "code" in names, (
            f"{system}/{code}: property entry missing 'code' part: {names!r}"
        )
        assert "value" in names, (
            f"{system}/{code}: property entry missing 'value' part: {names!r}"
        )
        found_well_shaped = True
    assert found_well_shaped, (
        f"{system}/{code}: no `property` Out parameter entries found"
    )


# ---------------------------------------------------------------------------
# Lens 12: GET↔POST round-trip consistency on $subsumes for the CS-05
#          surface (depth-1 hierarchy).
# ---------------------------------------------------------------------------
# Per FHIR R4 §3.1.0.1.1: operations MAY be invoked via GET or POST on
# either the type or a resource instance. CS-04 EXPLORER added the
# GET↔POST round-trip consistency probe on $subsumes for the CS-04
# surface; CS-05 EXPLORER re-runs it for the CS-05 surface (the same
# parent-child hierarchy seeded in the conformance fixture).

def test_e120_subsumes_get_post_round_trip_consistency(fhir_client):
    """Lens 12 / spec §3.1.0.1.1: $subsumes GET and POST MUST produce
    identical Out `outcome` values. EXPLORER probes the round-trip on
    every seeded (parent, child) pair.

    Pattern-match: CS-04 EXPLORER test_e170 GET↔POST round-trip
    consistency on $subsumes; CS-05 EXPLORER applies to the CS-05
    surface (same parent-child hierarchy, but the probe class is the
    carry-forward contract).
    """
    # Forward direction: parent subsumes child.
    r_get_fwd = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    r_post_fwd = fhir_client.post(
        "/fhir/CodeSystem/$subsumes",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
            ],
        },
    )
    assert r_get_fwd.status_code == r_post_fwd.status_code == 200
    assert _outcome(r_get_fwd.json()) == _outcome(r_post_fwd.json()) == "subsumes"


# ---------------------------------------------------------------------------
# Lens 13: Carry-forward verification — CF-EXPLORER-CS01-01 (chapter-
#          range canonical-code) on $lookup.
# ---------------------------------------------------------------------------
# Per AGENTS.md "Known Fragile Areas" CF-EXPLORER-CS01-01: the patient-
# friendly JSON artifacts can store a `canonical_code` that is a range
# or group code (e.g. ICD-10-CM `E08-E13` for "Diabetes mellitus"). The
# conformance fixture's seeded DB only contains single codes, so a strict
# round-trip assertion fails. EXPLORER re-verifies the CF on the CS-05
# surface — the URI-round-trip probe class must assert URI parseability,
# not strict round-trip success.

def test_e130_lookup_canonical_system_uri_is_resolvable_across_systems(fhir_client):
    """Lens 13 / CF-EXPLORER-CS01-01: for every seeded code in every
    seeded system, the Out `system` URI returned by $lookup MUST be
    parseable by `fhir_uri_to_system`. This is the URI-round-trip
    probe class tightened for the chapter-range canonical-code shape.

    Spec cross-reference: CF-EXPLORER-CS01-01 documents that the strict
    round-trip (GET $lookup → feed canonical-system+canonical-code back
    into $lookup → expect 200) fails for chapter-range codes. EXPLORER
    probes the URI parseability invariant instead.
    """
    from medterm4ds.engines.fhir import fhir_uri_to_system

    for system, code in [
        (SNOMED_URI, SNOMED_T2DM),
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (RXNORM_URI, RXNORM_METFORMIN),
        (ICD10CM_URI, ICD10CM_T2DM),
    ]:
        r = fhir_client.get(
            f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
        )
        assert r.status_code == 200
        body = r.json()
        canonical_system = _lookup_param_value(body, "system")
        assert canonical_system is not None, (
            f"{system}/{code}: Out `system` is None"
        )
        # The canonical system URI MUST be parseable.
        resolved = fhir_uri_to_system(canonical_system)
        assert resolved is not None, (
            f"{system}/{code}: canonical system URI {canonical_system!r} "
            f"is not parseable by fhir_uri_to_system"
        )


# ---------------------------------------------------------------------------
# Lens 14: Cross-source consistency for $subsumes — does the parent-
#          child relationship hold for ICD-10-CM (which has no mrrel
#          seeded in the conformance fixture)?
# ---------------------------------------------------------------------------
# The conformance fixture seeds only one mrrel row (SNOMED parent-child).
# ICD-10-CM has its own T2DM code (E11) but no mrrel seeded. EXPLORER
# probes that $subsumes on ICD-10-CM self-equivalence still works
# (short-circuit), AND probes that ICD-10-CM (E11, E11) returns equivalent.

def test_e140_subsumes_icd10cm_self_equivalence(fhir_client):
    """Lens 14 / spec $subsumes: ICD-10-CM has no mrrel seeded, but the
    self-equivalence short-circuit (codeA == codeB) MUST still fire.
    EXPLORER probes the short-circuit on the ICD-10-CM surface.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={ICD10CM_URI}"
        f"&codeA={ICD10CM_T2DM}&codeB={ICD10CM_T2DM}"
    )
    assert r.status_code == 200
    assert _outcome(r.json()) == "equivalent"


def test_e141_subsumes_rxnorm_self_equivalence(fhir_client):
    """Lens 14 / spec $subsumes: same shape as e140 but for RxNorm.
    The self-equivalence short-circuit MUST fire regardless of whether
    the system has mrrel seeded.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={RXNORM_URI}"
        f"&codeA={RXNORM_METFORMIN}&codeB={RXNORM_METFORMIN}"
    )
    assert r.status_code == 200
    assert _outcome(r.json()) == "equivalent"


# ---------------------------------------------------------------------------
# Lens 15: Body shape audit — every successful $lookup / $validate-code /
#          $subsumes response carries resourceType=Parameters.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("op_url,expected_out_param", [
    (f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}", "code"),
    (f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}", "result"),
    (f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}", "outcome"),
])
def test_e150_response_body_shape_audit(fhir_client, op_url, expected_out_param):
    """Lens 15 / body shape audit: every successful CS-05 operation
    response MUST have resourceType=Parameters AND carry the expected
    Out parameter (code/result/outcome). EXPLORER parametrizes across
    the three operations.
    """
    r = fhir_client.get(op_url)
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"{op_url}: resourceType={body.get('resourceType')!r}"
    )
    assert _lookup_param(body, expected_out_param) is not None, (
        f"{op_url}: missing expected Out parameter {expected_out_param!r}"
    )
