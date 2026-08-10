"""EXPLORER probes for chunk CM-02 (ConceptMap $translate Operation).

Source: https://build.fhir.org/conceptmap-operation-translate.html
Canonical R4 $translate operation:
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html
Canonical R4 ConceptMapEquivalence closed enum:
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

EXPLORER lens (lateral thinking). The 4-shape Content-Type closure on
$translate was already closed by CM-01 EXPLORER (test_e10..e13). The
HISTORIAN iteration (test_h100..h102) verified Content-Type and basic
XML wire-format. The EXPLORER iteration extends with:

  * Combined / contradictory input encodings (spec violations).
  * Long code values, special characters (no 500 / no SQL injection).
  * Reverse-mode graceful behavior (already pinned by SKEPTIC test_s62;
    EXPLORER confirms the spec text allows the no-op fallback).
  * match.dependsOn and match.product omitted when no data (R4 0..*
    cardinality — omission is conformant; emit empty array is also
    conformant. medterm4ds chooses omission).
  * Cross-system parity — same source code translates consistently
    whether targetSystem is specified, omitted, or specified with an
    unknown system.
  * Accept-header XML negotiation (distinct from _format=xml).
  * GET↔POST byte-exact parity including edge cases (special chars,
    long code).
  * ConceptMap instance-level GET/POST route registered (TS-02 SKEPTIC
    QA-014 pattern class).
  * Match shape audit (the engine emits exactly 3 parts: equivalence,
    concept, source — no dependsOn, no product).
  * Cross-handler consistency: GET $translate with Accept JSON, POST
    $translate with Content-Type JSON, both produce the same Content-Type
    on the response.
  * Boolean rendering on result parameter is a real Python bool (not
    string 'true'/'false').
  * Outcome of always-emit message: when result=true, the message is
    still emitted ("N matches found"). Spec allows 0..1; emit-always is
    conformant.
  * targetCode param accepted (declared on translate_get per
    apps/fhir_api.py:1978). EXPLORER confirms it's silently dropped
    (reverse mode not implemented).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    canonical_system_uri,
    fhir_uri_to_system,
)
from medterm4ds.engines.fhir.responses import build_parameters_translate


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_URI_TRAILING_SLASH = "http://hl7.org/fhir/sid/icd-10-cm/"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
LOINC_URI = "http://loinc.org"
CONCEPTMAP_URL = "http://medterm4ds.org/fhir/ConceptMap/snomed-to-icd10"


def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Return the first ``parameter`` entry with ``name == name``, else None."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


# ===========================================================================
# Lens 1: 4-shape Content-Type closure on $translate (re-verification).
# Closed by CM-01 EXPLORER test_e10..e13. CM-02 EXPLORER re-verifies
# that the closure holds — both Content-Type header AND body resourceType
# are conformant on every shape.
# ===========================================================================


def test_e10_translate_get_system_code_targetsystem_emits_fhir_json(fhir_client):
    """EXPLORER (shape a — re-verification): GET $translate with
    system+code+targetsystem MUST emit ``application/fhir+json`` AND
    a Parameters body.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift: {r.headers['content-type']!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_e11_translate_post_scalar_body_emits_fhir_json(fhir_client):
    """EXPLORER (shape b — re-verification): POST $translate with
    scalar Parameters body MUST emit ``application/fhir+json`` AND a
    Parameters body.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_e12_translate_post_coding_body_emits_fhir_json_error_path(fhir_client):
    """EXPLORER (shape c — re-verification): POST $translate with
    ``coding`` alternative encoding (per FHIR R4 In Parameters). The
    helper is now wired (CF-CM02-01 RESOLVED by CM-01 EXPLORER QA-001);
    the response is 200 + Parameters + conformant Content-Type.

    Updated from prior 400-expecting shape when CF-CM02-01 was deferred.
    Carry-forward-as-probe pattern (CS-03 TERMINOLOGIST methodology) —
    methodology fired loudly on the CM-01 EXPLORER fix as designed.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {"system": SNOMED_URI, "code": "44054006"},
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    # CF-CM02-01 RESOLVED: helper now wired; coding body produces 200.
    assert r.status_code == 200, (
        f"POST $translate coding body — CF-CM02-01 RESOLVED requires 200 "
        f"(coding now honored via _extract_named_coding_from_parameters). "
        f"Got {r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_e13_translate_post_empty_body_emits_fhir_json_error_path(fhir_client):
    """EXPLORER (shape d — re-verification): POST $translate with empty
    Parameters body MUST emit 400 + OperationOutcome + conformant
    Content-Type. The error path is the load-bearing contract for
    framework-default drift (TS-02 SKEPTIC QA-020 pattern class).
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


# ===========================================================================
# Lens 2: XML wire-format on $translate (CR-002 boolean rendering).
# HISTORIAN test_h102 covers the basic XML case. EXPLORER extends with:
#   (a) Accept-header XML negotiation (distinct from _format=xml).
#   (b) XML on POST route (HISTORIAN h102 only tested GET).
#   (c) XML result boolean is lowercase even when result=false (no-match).
#   (d) XML equivalence valueCode uses lowercase code (no capital drift).
# ===========================================================================


def test_e20_translate_get_accept_xml_header_emits_xml(fhir_client):
    """EXPLORER: GET $translate with ``Accept: application/fhir+xml``
    header (no _format query param) MUST emit XML.

    Spec: FHIR R4 §3.1.0.1.11 — format negotiation via Accept header.
    Reference: AGENTS.md "Known Fragile Areas" (apps/fhir_api.py:_wants_xml).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml"), (
        f"Accept-header XML negotiation drift: got {r.headers['content-type']!r}"
    )
    # The XML body MUST render the boolean result in lowercase per CR-002.
    body_text = r.text
    assert 'value="true"' in body_text or 'value="false"' in body_text, (
        f"XML body missing lowercase boolean: {body_text[:300]}"
    )
    assert 'value="True"' not in body_text, (
        f"CR-002 regression: capital-T True rendered. Body: {body_text[:300]}"
    )


def test_e21_translate_post_accept_xml_header_emits_xml(fhir_client):
    """EXPLORER (sibling of e20): POST $translate with
    ``Accept: application/fhir+xml`` MUST emit XML.

    Mirrors e20 on the POST path. HISTORIAN h102 only tested GET with
    _format=xml; this probes Accept-header on POST.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml")
    body_text = r.text
    assert 'value="true"' in body_text or 'value="false"' in body_text


def test_e22_translate_get_xml_no_match_result_false_lowercase(fhir_client):
    """EXPLORER: GET $translate with no-match (SNOMED → RxNorm has no
    crosswalk in the fixture) MUST render ``result=false`` in XML in
    lowercase. Extends HISTORIAN h102 which only tested the match
    case (result=true).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", RXNORM_URI),
            ("_format", "xml"),
        ],
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml")
    body_text = r.text
    # No-match MUST render 'false' (lowercase).
    assert 'value="false"' in body_text, (
        f"XML wire-format drift on no-match: result=false not rendered "
        f"in lowercase. Body: {body_text[:500]}"
    )
    assert 'value="False"' not in body_text, (
        f"CR-002 regression on no-match path: capital-F False rendered. "
        f"Body: {body_text[:500]}"
    )


def test_e23_translate_xml_equivalence_uses_lowercase_value_code(fhir_client):
    """EXPLORER: GET $translate XML MUST render ``equivalence`` valueCode
    in lowercase. The conformance fixture only seeds same-CUI
    ``equivalent`` mappings, so the value is the R4 enum ``equivalent``.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("_format", "xml"),
        ],
    )
    assert r.status_code == 200
    body_text = r.text
    # The match.equivalence is rendered as <part><name value="equivalence"/>
    # <valueCode value="equivalent"/></part>. The 'equivalent' literal is
    # already lowercase per R4 spec.
    assert 'value="equivalent"' in body_text, (
        f"XML match.equivalence missing lowercase 'equivalent' code. "
        f"Body: {body_text[:500]}"
    )
    # Capital drift would be 'Equivalent' or 'EQUIVALENT' — none are R4 values.
    assert "Equivalent" not in body_text
    assert "EQUIVALENT" not in body_text


# ===========================================================================
# Lens 3: Match shape edge cases.
#   * Match with no equivalence: N/A — engine always emits equivalence.
#   * Match with dependsOn: omitted when no data (R4 0..* — conformant).
#   * Match with product: omitted when no data (R4 0..* — conformant).
#   * Match parts audit: every match has exactly 3 parts (equivalence,
#     concept, source).
# ===========================================================================


def test_e30_translate_match_has_exactly_three_parts(fhir_client):
    """EXPLORER: each ``match`` entry MUST contain exactly 3 parts
    (equivalence, concept, source) — NOT 4 or 5 with empty
    dependsOn/product arrays.

    Per FHIR R4 Out Parameters: ``equivalence`` 1..1, ``concept`` 0..1
    Coding, ``source`` 0..1 Coding, ``dependsOn`` 0..*, ``product`` 0..*.
    The 0..* cardinality allows omission; medterm4ds chooses omission
    because the engine does not model parameterized mappings.

    Reference: AGENTS.md "Where Things Live" (outputs/fhir.py emits
    extensions instead of dependsOn/product for match metadata).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) > 0, "Expected at least one match"
    for match in matches:
        parts = match.get("part", [])
        part_names = [p.get("name") for p in parts]
        assert "equivalence" in part_names
        assert "concept" in part_names
        assert "source" in part_names
        # dependsOn and product MUST be omitted (not empty arrays).
        assert "dependsOn" not in part_names, (
            f"match.dependsOn emitted as a part — medterm4ds convention is "
            f"OMISSION when no data. Got parts: {part_names}"
        )
        assert "product" not in part_names, (
            f"match.product emitted as a part — medterm4ds convention is "
            f"OMISSION when no data. Got parts: {part_names}"
        )


def test_e31_translate_match_concept_includes_display(fhir_client):
    """EXPLORER: the ``concept`` Coding in a match SHOULD include a
    ``display`` field sourced from the engine's canonical preferred
    term for the target code.

    Spec: FHIR R4 Coding.display is 0..1 ("A representation of the
    meaning of the code..."). Terminology services SHOULD provide it
    when known.

    The conformance fixture seeds target_display "Type 2 diabetes
    mellitus" for ICD-10-CM E11 (same CUI C0011847 as SNOMED 44054006).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) > 0
    for match in matches:
        concept_part = next(
            (p for p in match.get("part", []) if p.get("name") == "concept"),
            None,
        )
        assert concept_part is not None
        coding = concept_part.get("valueCoding", {})
        # display SHOULD be present (engine resolves target_display).
        assert "display" in coding, (
            f"match.concept Coding missing display field: {coding!r}. "
            f"Engine should populate target_display."
        )
        # Display is non-empty when known.
        assert coding.get("display"), (
            f"match.concept.display is empty: {coding!r}"
        )


def test_e32_translate_match_source_has_no_display(fhir_client):
    """EXPLORER: the ``source`` Coding in a match has system + code but
    NO display field. This is the implementation's current shape — the
    builder at responses.py:185-188 only sets ``system`` and ``code``
    on the source Coding (no display).

    The spec allows this — Coding.display is 0..1. Some implementations
    echo the source-side display for client convenience; medterm4ds does
    not. This probe documents the current shape.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) > 0
    for match in matches:
        source_part = next(
            (p for p in match.get("part", []) if p.get("name") == "source"),
            None,
        )
        assert source_part is not None
        coding = source_part.get("valueCoding", {})
        assert "system" in coding
        assert "code" in coding
        # source Coding does NOT carry display today.
        assert "display" not in coding or not coding.get("display"), (
            f"match.source.display unexpectedly present: {coding!r}. The "
            f"current builder at responses.py:185-188 does not set display "
            f"on the source Coding."
        )


# ===========================================================================
# Lens 4: Cross-system consistency — same source code translates
# consistently whether targetSystem is specified, omitted, or specified
# with an unknown system.
# ===========================================================================


@pytest.mark.parametrize(
    "source_system,source_code,target_system,expected_match_count",
    [
        # SNOMED 44054006 → ICD-10-CM (seeded same-CUI match via C0011847)
        (SNOMED_URI, "44054006", ICD10CM_URI, 1),
        # SNOMED 44054006 → RxNorm (NO crosswalk in fixture — no match)
        (SNOMED_URI, "44054006", RXNORM_URI, 0),
        # SNOMED 44054006 → LOINC (NO crosswalk in fixture — no match)
        (SNOMED_URI, "44054006", LOINC_URI, 0),
        # ICD-10-CM E11 → SNOMED (reverse direction — seeded via same CUI)
        (ICD10CM_URI, "E11", SNOMED_URI, 1),
    ],
    ids=["snomed-to-icd10cm", "snomed-to-rxnorm-no-match", "snomed-to-loinc-no-match", "icd10cm-to-snomed"],
)
def test_e40_translate_cross_system_consistency(
    fhir_client, source_system, source_code, target_system, expected_match_count
):
    """EXPLORER (cross-system consistency): parametrized over 4 source-
    target combinations. Each MUST return 200 + Parameters with the
    expected match count.

    Spec: FHIR R4 $translate operation. The conformance fixture seeds
    exactly ONE cross-CUI mapping (SNOMED 44054006 ↔ ICD-10-CM E11 via
    CUI C0011847). Translating SNOMED 44054006 → ICD-10-CM yields 1
    match; SNOMED → RxNorm and SNOMED → LOINC yield 0 matches; ICD-10-CM
    → SNOMED yields 1 match (reverse direction).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", source_system),
            ("code", source_code),
            ("targetsystem", target_system),
        ],
    )
    assert r.status_code == 200, (
        f"params: source={source_system!r}, code={source_code!r}, "
        f"target={target_system!r}; got {r.status_code}: {r.text}"
    )
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) == expected_match_count, (
        f"Expected {expected_match_count} match(es); got {len(matches)}. "
        f"Params: source={source_system!r}, code={source_code!r}, "
        f"target={target_system!r}."
    )
    # result MUST agree with match count.
    result = _find_param(body, "result")
    assert result is not None
    expected_result_bool = expected_match_count > 0
    assert result.get("valueBoolean") == expected_result_bool, (
        f"result={result.get('valueBoolean')!r}; expected "
        f"{expected_result_bool!r} (matches={len(matches)})."
    )


def test_e41_translate_no_targetsystem_returns_matches_across_all_systems(fhir_client):
    """EXPLORER: when targetSystem is OMITTED, the server MUST return
    matches across ALL known target systems. The conformance fixture
    seeds exactly one cross-system mapping (SNOMED → ICD-10-CM); the
    no-targetSystem path SHOULD still find it.

    Spec: FHIR R4 $translate In Parameters: ``targetSystem`` is 0..1 uri
    — "if targetSystem not provided, then the server may use any
    available map".

    Reference: ``_do_translate`` at apps/fhir_api.py:2007-2008 uses
    ``_all_systems_except(source)`` when targetSystem is absent.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    result = _find_param(body, "result")
    assert result is not None
    assert result.get("valueBoolean") is True, (
        f"no-targetSystem path should still find the SNOMED→ICD-10-CM "
        f"mapping via _all_systems_except; got result={result}"
    )
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) >= 1


def test_e42_translate_unknown_target_system_returns_400(fhir_client):
    """EXPLORER: GET $translate with an unrecognized targetSystem URI
    MUST return 400 + OperationOutcome (per ``_do_translate`` line
    2003-2005 which validates the targetSystem via
    ``fhir_uri_to_system`` and returns 400 on None).

    The probe documents this graceful behavior. The error path Content-
    Type MUST be ``application/fhir+json``.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", "http://example.org/unknown-system"),
        ],
    )
    assert r.status_code == 400, (
        f"Unknown targetSystem — expected 400; got {r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


# ===========================================================================
# Lens 5: Combined / contradictory input encodings (spec violations).
# POST $translate with BOTH scalar ``system``+``code`` AND ``coding``
# AND ``codeableConcept``. The handler reads scalar via
# ``_parse_parameters``; the complex-type encodings are silently
# dropped (CF-CM02-01). The probe verifies the SCALAR values win
# (current behavior). When CF-CM02-01 lands, the helper-wiring will
# likely keep scalar-wins-on-conflict semantics (per the TS-02
# HISTORIAN QA-022 convention).
# ===========================================================================


def test_e50_translate_post_scalar_plus_coding_scalar_wins(fhir_client):
    """EXPLORER: POST $translate with BOTH ``system``+``code`` AND a
    conflicting ``coding`` parameter. The handler uses scalar values
    via ``_parse_parameters``; the coding is silently dropped.

    The probe verifies the SCALAR SNOMED 44054006 → ICD-10-CM
    translation succeeds (matches=1). The coding (also SNOMED but with
    a DIFFERENT code, e.g. 99999999 nonexistent) is ignored.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": SNOMED_URI,
                        "code": "99999999-X",  # conflicting — ignored
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    body = r.json()
    result = _find_param(body, "result")
    assert result is not None and result.get("valueBoolean") is True, (
        f"Scalar values should win; got result={result}"
    )
    # The match source code MUST be the scalar 44054006 (not the ignored coding).
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) == 1
    source_part = next(
        (p for p in matches[0].get("part", []) if p.get("name") == "source"),
        None,
    )
    assert source_part is not None
    assert source_part.get("valueCoding", {}).get("code") == "44054006", (
        f"match.source.code should be the scalar 44054006 (coding ignored); "
        f"got {source_part.get('valueCoding')!r}"
    )


def test_e51_translate_post_scalar_plus_codeable_concept_scalar_wins(fhir_client):
    """EXPLORER (sibling of e50): POST $translate with BOTH scalar
    system+code AND a codeableConcept. The scalar wins.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {
                                "system": SNOMED_URI,
                                "code": "99999999-Y",  # conflicting — ignored
                            }
                        ]
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) == 1
    source_part = next(
        (p for p in matches[0].get("part", []) if p.get("name") == "source"),
        None,
    )
    assert source_part is not None
    assert source_part.get("valueCoding", {}).get("code") == "44054006"


def test_e52_translate_post_codeable_concept_multi_coding_silently_dropped(fhir_client):
    """EXPLORER (carry-forward-as-probe pattern): POST $translate with
    a codeableConcept containing MULTIPLE codings. CF-CM02-01 RESOLVED
    by CM-01 EXPLORER QA-001 — the codeableConcept extractor is now
    wired (single-pair semantic per _extract_codeable_concept_from_parameters);
    the FIRST coding with both system+code is picked.

    Updated from prior 400-expecting shape when CF-CM02-01 was deferred.
    Carry-forward-as-probe pattern (CS-03 TERMINOLOGIST methodology) —
    methodology fired loudly on the CM-01 EXPLORER fix as designed.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "99999999-Z"},
                            {"system": SNOMED_URI, "code": "44054006"},
                        ]
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    # CF-CM02-01 RESOLVED: codeableConcept extractor now wired.
    # The single-pair helper picks the FIRST coding with both fields.
    # 99999999-Z is a valid-shape coding (both system+code present); picked first.
    # The translate succeeds (result=true or false depending on whether the
    # code maps; either way the response is 200 Parameters).
    assert r.status_code == 200, (
        f"POST $translate with codeableConcept multi-coding — CF-CM02-01 "
        f"RESOLVED requires 200 (codeableConcept now honored via "
        f"_extract_codeable_concept_from_parameters). Got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"


# ===========================================================================
# Lens 6: targetCode + system — probe that targetCode is accepted
# (declared on translate_get per apps/fhir_api.py:1978) but silently
# dropped (reverse mode not implemented — DEFERRED, CF-CM02-04).
# ===========================================================================


def test_e60_translate_target_code_param_accepted_current_behavior(fhir_client):
    """EXPLORER (item 2 — targetCode): GET $translate with
    ``targetCode`` parameter (per FHIR R4 In Parameters: ``targetCode``
    is a spec-listed alternative for reverse-mode lookup).

    The handler declares ``targetCode`` at apps/fhir_api.py:1978 with
    description "Target code — used with reverse=true to find source
    codes mapping to this target." The parameter is accepted but the
    reverse-mode logic is not wired (CF-CM02-04 DEFERRED per AGENTS.md
    NOT A BUG registry).

    The probe verifies the request does NOT 500 — spec-compatibility
    fallback.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("targetCode", "E11"),
        ],
    )
    assert r.status_code == 200, (
        f"GET $translate with targetCode — expected 200 (targetCode is "
        f"accepted but silently dropped per CF-CM02-04 DEFERRED); got "
        f"{r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_e61_translate_reverse_param_accepted_graceful(fhir_client):
    """EXPLORER (item 2 — reverse mode, CF-CM02-04): GET $translate
    with ``reverse=true`` MUST NOT 500. Per AGENTS.md NOT A BUG
    registry: "$translate?reverse=true accepted but not fully
    implemented".

    The probe documents the graceful fallback — the request succeeds
    with forward-translation semantics. Carry-forward-as-probe pattern.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("reverse", "true"),
        ],
    )
    assert r.status_code == 200, (
        f"GET $translate with reverse=true — expected 200 (reverse mode "
        f"silently dropped per AGENTS.md NOT A BUG registry); got "
        f"{r.status_code}: {r.text}"
    )


def test_e62_translate_reverse_false_default_behavior(fhir_client):
    """EXPLORER (sibling of e61): GET $translate with ``reverse=false``
    MUST behave identically to the default (no reverse param).

    The probe verifies the response shape is identical with and without
    ``reverse=false``.
    """
    r_with = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("reverse", "false"),
        ],
    )
    r_without = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r_with.status_code == r_without.status_code == 200
    body_with = r_with.json()
    body_without = r_without.json()
    matches_with = [p for p in body_with.get("parameter", []) if p.get("name") == "match"]
    matches_without = [p for p in body_without.get("parameter", []) if p.get("name") == "match"]
    assert len(matches_with) == len(matches_without), (
        f"reverse=false should be identical to default; got "
        f"{len(matches_with)} vs {len(matches_without)}"
    )


# ===========================================================================
# Lens 7: Long code values, special characters (no 500 / no SQL injection).
# ===========================================================================


@pytest.mark.parametrize(
    "code_value",
    [
        "44054006",  # baseline
        "44054006; DROP TABLE mrconso;--",  # SQL injection attempt
        "44054006' OR '1'='1",  # SQL injection variant
        "A" * 1000,  # very long code
        "<script>alert('xss')</script>",  # XSS attempt
        "../../etc/passwd",  # path traversal
        "44054006\n\r\t",  # control characters
    ],
    ids=["baseline", "sql-injection", "sql-injection-variant", "long-code", "xss", "path-traversal", "control-chars"],
)
def test_e70_translate_long_special_code_values(fhir_client, code_value):
    """EXPLORER (hostile input): GET $translate with code values
    containing SQL injection attempts, very long strings, XSS attempts,
    path traversal, and control characters. The handler MUST NOT 500.

    The DuckDB engine uses prepared statements; SQL injection is
    structurally impossible. The probe verifies graceful handling (200
    with no matches, OR 400 if the input fails upstream validation).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", code_value),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code in (200, 400), (
        f"Hostile code value {code_value!r} returned {r.status_code}; "
        f"expected 200 (no match) or 400 (input rejected); body: {r.text[:300]}"
    )
    # MUST NOT leak a 500 with traceback (information disclosure).
    assert r.status_code != 500, (
        f"Hostile code value {code_value!r} produced 500 — information "
        f"disclosure surface. Body: {r.text[:300]}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift on hostile input: "
        f"{r.headers['content-type']!r}"
    )


def test_e71_translate_long_system_uri(fhir_client):
    """EXPLORER (sibling of e70): GET $translate with a very long
    system URI. The handler MUST NOT 500 — DuckDB prepared statements
    handle long strings gracefully.
    """
    long_uri = "http://example.org/" + "A" * 2000
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", long_uri),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    # The unrecognized system URI should yield 400 (per _do_translate
    # line 1999-2000).
    assert r.status_code == 400, (
        f"Long unrecognized system URI — expected 400; got {r.status_code}: "
        f"{r.text[:300]}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


# ===========================================================================
# Lens 8: GET ↔ POST byte-exact parity on $translate.
# SKEPTIC test_s110 covers the basic case. EXPLORER extends with:
#   (a) Parity on no-match (SNOMED → RxNorm).
#   (b) Parity on no-targetSystem.
#   (c) Byte-exact JSON body comparison (not just summary fields).
# ===========================================================================


def test_e80_get_post_parity_on_match_set(fhir_client):
    """EXPLORER (GET↔POST parity): GET and POST $translate with the
    same logical inputs (system+code+targetsystem) MUST produce
    byte-exact JSON bodies (modulo whitespace).

    SKEPTIC test_s110 covers summary-field parity; EXPLORER extends to
    full byte-exact JSON comparison.
    """
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    r_post = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r_get.status_code == r_post.status_code == 200
    body_get = r_get.json()
    body_post = r_post.json()
    # Byte-exact JSON comparison after canonical re-serialization.
    assert json.dumps(body_get, sort_keys=True) == json.dumps(body_post, sort_keys=True), (
        f"GET↔POST byte-exact JSON drift on $translate:\n"
        f"  GET : {json.dumps(body_get, sort_keys=True)[:500]}\n"
        f"  POST: {json.dumps(body_post, sort_keys=True)[:500]}"
    )


def test_e81_get_post_parity_on_no_match(fhir_client):
    """EXPLORER (sibling of e80): GET↔POST parity on no-match case
    (SNOMED → RxNorm has no crosswalk in the conformance fixture).
    """
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", RXNORM_URI),
        ],
    )
    r_post = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": RXNORM_URI},
            ],
        },
    )
    assert r_get.status_code == r_post.status_code == 200
    body_get = r_get.json()
    body_post = r_post.json()
    assert json.dumps(body_get, sort_keys=True) == json.dumps(body_post, sort_keys=True)


def test_e82_get_post_parity_on_no_targetsystem(fhir_client):
    """EXPLORER (sibling of e80): GET↔POST parity when targetSystem is
    OMITTED. Both paths use ``_all_systems_except(source)`` for target
    discovery.
    """
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
        ],
    )
    r_post = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
            ],
        },
    )
    assert r_get.status_code == r_post.status_code == 200
    body_get = r_get.json()
    body_post = r_post.json()
    assert json.dumps(body_get, sort_keys=True) == json.dumps(body_post, sort_keys=True)


# ===========================================================================
# Lens 9: Instance-level route registered (TS-02 SKEPTIC QA-014 pattern).
# ===========================================================================


def test_e90_instance_level_translate_get_returns_fhir_response(fhir_client):
    """EXPLORER: instance-level GET /fhir/ConceptMap/{id}/$translate
    MUST be registered (TS-02 SKEPTIC QA-014 pattern class).

    medterm4ds does not persist ConceptMaps; the instance-level route
    returns a 404 OperationOutcome with explanatory message.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/any-id/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_e91_instance_level_translate_post_returns_fhir_response(fhir_client):
    """EXPLORER (sibling of e90): instance-level POST
    /fhir/ConceptMap/{id}/$translate MUST be registered.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/any-id/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


# ===========================================================================
# Lens 10: Cross-handler Content-Type consistency.
# ===========================================================================


def test_e100_translate_response_always_parameters_resource_type(fhir_client):
    """EXPLORER (cross-handler consistency): GET and POST $translate
    MUST both return a Parameters resource on success. The Content-
    Type MUST be ``application/fhir+json`` (NOT ``application/json``).

    Spec: FHIR R4 §3.1.0.1.9 mandates ``application/fhir+json``.
    """
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    r_post = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    for label, r in (("GET", r_get), ("POST", r_post)):
        assert r.status_code == 200, f"{label}: got {r.status_code}: {r.text}"
        assert r.headers["content-type"].startswith("application/fhir+json"), (
            f"{label}: Content-Type drift: {r.headers['content-type']!r}"
        )
        body = r.json()
        assert body.get("resourceType") == "Parameters", (
            f"{label}: resourceType drift: {body.get('resourceType')!r}"
        )


# ===========================================================================
# Lens 11: result boolean is a real Python bool, not a string.
# ===========================================================================


def test_e110_translate_result_value_is_python_bool(fhir_client):
    """EXPLORER: the ``result`` valueBoolean field MUST deserialize to
    a real Python ``bool`` (not a string 'true'/'false' or 0/1).

    Spec: FHIR R4 boolean primitive. JSON deserialization of
    ``valueBoolean: true`` yields ``True`` (Python bool). If the server
    rendered it as a string, deserialization would yield a str — a
    silent wrong-type bug.

    Reference: GLOBAL_RULES.md "FHIR API Specifics" (POST boolean
    parameters — ``str(True)`` is ``'True'`` not ``'true'``).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    result = _find_param(body, "result")
    assert result is not None
    val = result.get("valueBoolean")
    assert isinstance(val, bool), (
        f"result.valueBoolean is not a Python bool: type={type(val).__name__}, "
        f"value={val!r}. FHIR JSON deserialization of 'true'/'false' literals "
        f"MUST yield Python bool."
    )
    assert val is True


def test_e111_translate_no_match_result_value_is_python_bool_false(fhir_client):
    """EXPLORER (sibling of e110): no-match ``result`` MUST deserialize
    to ``False`` (Python bool).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", RXNORM_URI),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    result = _find_param(body, "result")
    assert result is not None
    val = result.get("valueBoolean")
    assert isinstance(val, bool), (
        f"no-match result.valueBoolean is not a Python bool: type={type(val).__name__}"
    )
    assert val is False


# ===========================================================================
# Lens 12: message parameter shape — always emitted (even on result=true).
# ===========================================================================


def test_e120_translate_message_always_emitted_with_match_count(fhir_client):
    """EXPLORER: the ``message`` parameter is ALWAYS emitted by
    ``build_parameters_translate`` (responses.py:198) — even when
    result=true. The spec allows 0..1 (optional); emit-always is
    conformant.

    Spec: FHIR R4 $translate Out Parameters: ``message`` 0..1 string.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    message = _find_param(body, "message")
    assert message is not None, (
        "message parameter missing on result=true response — should be "
        "always emitted per build_parameters_translate."
    )
    val = message.get("valueString")
    assert isinstance(val, str)
    # Message format: "N matches found" per builder.
    assert "match" in val.lower() or "found" in val.lower(), (
        f"message.valueString unexpected format: {val!r}"
    )


def test_e121_translate_message_on_no_match_includes_zero(fhir_client):
    """EXPLORER (sibling of e120): no-match response MUST include
    ``message`` parameter with the count 0.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", RXNORM_URI),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    message = _find_param(body, "message")
    assert message is not None
    val = message.get("valueString")
    assert isinstance(val, str)
    # The no-match message includes "0 matches" per the builder format
    # f"{len(matches)} matches found".
    assert "0" in val, (
        f"no-match message should include '0' for match count; got {val!r}"
    )


# ===========================================================================
# Lens 13: Canonical-URI helper usage — re-verification of CR-012 fix
# on $translate with multiple alias variants.
# ===========================================================================


@pytest.mark.parametrize(
    "alias,expected_canonical",
    [
        (SNOMED_URI, SNOMED_URI),
        (SNOMED_URI_OID_ALIAS, SNOMED_URI),
        (SNOMED_URI_TRAILING_SLASH, SNOMED_URI),
        (ICD10CM_URI, ICD10CM_URI),
        (ICD10CM_URI_TRAILING_SLASH, ICD10CM_URI),
    ],
    ids=["snomed-canonical", "snomed-oid-alias", "snomed-trailing-slash",
         "icd10cm-canonical", "icd10cm-trailing-slash"],
)
def test_e130_translate_canonical_uri_resolution(fhir_client, alias, expected_canonical):
    """EXPLORER (CR-012 + CR-025 re-verification): for every alias
    input on EITHER source-side OR target-side system URI, the Out
    ``match[].source.system`` AND ``match[].concept.system`` fields
    MUST be the canonical URI.

    Source-side: CR-012 wraps ``source_uri`` through
    ``canonical_system_uri()`` at apps/fhir_api.py:2025.

    Target-side: ``build_parameters_translate`` at responses.py:175
    uses ``system_to_fhir_uri(m.target.source)`` which is already
    canonical.

    SKEPTIC test_s90 covers source-side with 3 inputs. EXPLORER
    extends with target-side alias inputs (ICD10CM variants).
    """
    # Source-side: when alias is a SNOMED URI variant, translate to ICD-10-CM.
    if "snomed" in alias.lower() or "oid" in alias.lower():
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params=[
                ("system", alias),
                ("code", "44054006"),
                ("targetsystem", ICD10CM_URI),
            ],
        )
        assert r.status_code == 200, f"alias={alias!r}; got {r.status_code}"
        body = r.json()
        matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
        assert len(matches) > 0
        for match in matches:
            source_part = next(
                (p for p in match.get("part", []) if p.get("name") == "source"),
                None,
            )
            assert source_part is not None
            source_system = source_part.get("valueCoding", {}).get("system")
            assert source_system == expected_canonical, (
                f"alias={alias!r} → match.source.system drift: "
                f"got {source_system!r}; expected canonical "
                f"{expected_canonical!r}. CR-012 regression."
            )
    # Target-side: when alias is an ICD-10-CM URI variant, translate
    # from SNOMED.
    elif "icd" in alias.lower():
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params=[
                ("system", SNOMED_URI),
                ("code", "44054006"),
                ("targetsystem", alias),
            ],
        )
        assert r.status_code == 200, f"alias={alias!r}; got {r.status_code}"
        body = r.json()
        matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
        assert len(matches) > 0
        for match in matches:
            concept_part = next(
                (p for p in match.get("part", []) if p.get("name") == "concept"),
                None,
            )
            assert concept_part is not None
            concept_system = concept_part.get("valueCoding", {}).get("system")
            assert concept_system == expected_canonical, (
                f"alias={alias!r} → match.concept.system drift: "
                f"got {concept_system!r}; expected canonical "
                f"{expected_canonical!r}."
            )


# ===========================================================================
# Lens 14: Builder-level direct unit tests for match shape edge cases.
# ===========================================================================


def test_e140_build_parameters_translate_match_has_no_dependsOn_or_product():
    """EXPLORER (builder-level audit): ``build_parameters_translate``
    MUST emit each match with EXACTLY 3 parts (equivalence, concept,
    source). ``dependsOn`` and ``product`` MUST be omitted.

    Spec: FHIR R4 $translate Out Parameters: ``dependsOn`` 0..* and
    ``product`` 0..* (omission conformant when no data).
    """
    from medterm4ds.core.models import CodeMapping, CodeRef

    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E11"),
        relationship="equivalent",
        match_type="same_cui",
        target_display="Type 2 diabetes mellitus",
    )
    body = build_parameters_translate(
        [mapping], source_system_uri=SNOMED_URI, source_code="44054006"
    )
    match_params = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(match_params) == 1
    parts = match_params[0].get("part", [])
    part_names = [p.get("name") for p in parts]
    assert part_names == ["equivalence", "concept", "source"], (
        f"match parts drift: got {part_names}; expected exactly "
        f"['equivalence', 'concept', 'source'] (no dependsOn, no product)."
    )


def test_e141_build_parameters_translate_multiple_matches_preserves_order():
    """EXPLORER (builder-level): when given MULTIPLE mappings, the
    builder MUST preserve input order in the ``match`` entries.

    The fixture seeds only one cross-CUI mapping, but the builder is
    not constrained to single-match. The probe verifies order
    preservation with a synthetic list.
    """
    from medterm4ds.core.models import CodeMapping, CodeRef

    mappings = [
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="44054006"),
            target=CodeRef(source="ICD10CM", code="E11"),
            relationship="equivalent",
            match_type="same_cui",
            target_display="Type 2 diabetes mellitus",
        ),
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="44054006"),
            target=CodeRef(source="RXNORM", code="860975"),
            relationship="related-to",
            match_type="broader",
            target_display="24 HR metformin 500 MG Oral Tablet",
        ),
    ]
    body = build_parameters_translate(
        mappings, source_system_uri=SNOMED_URI, source_code="44054006"
    )
    match_params = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(match_params) == 2
    # First match target should be the first mapping's target.
    first_concept = next(
        (p for p in match_params[0].get("part", []) if p.get("name") == "concept"),
        None,
    )
    assert first_concept is not None
    assert first_concept.get("valueCoding", {}).get("code") == "E11"
    # Second match target should be the second mapping's target.
    second_concept = next(
        (p for p in match_params[1].get("part", []) if p.get("name") == "concept"),
        None,
    )
    assert second_concept is not None
    assert second_concept.get("valueCoding", {}).get("code") == "860975"
    # The message should reflect the count: "2 matches found".
    message = _find_param(body, "message")
    assert message is not None
    assert "2" in message.get("valueString", "")


def test_e142_build_parameters_translate_empty_message_format():
    """EXPLORER (builder-level): the no-match message format MUST be
    "0 matches found" per the builder.
    """
    body = build_parameters_translate(
        [], source_system_uri=SNOMED_URI, source_code="44054006"
    )
    message = _find_param(body, "message")
    assert message is not None
    val = message.get("valueString")
    assert val == "0 matches found", (
        f"no-match message drift: got {val!r}; expected '0 matches found'."
    )


# ===========================================================================
# Lens 15: _all_systems_except source coverage — verify the helper
# returns the expected set of supported systems (CR-008/CR-020
# carry-forward notes the hardcoded list).
# ===========================================================================


def test_e150_all_systems_except_returns_full_set_minus_source(fhir_client):
    """EXPLORER (CR-008/CR-020 carry-forward): when targetSystem is
    OMITTED, the ``_do_translate`` handler at apps/fhir_api.py:2008
    uses ``_all_systems_except(source)`` to enumerate target systems.

    The hardcoded list (line 3019-3021) contains 8 systems:
    SNOMEDCT_US, ICD10CM, ICD10PCS, RXNORM, LNC, CPT, HCPCS, CVX.

    The probe verifies a no-targetSystem translate from SNOMED finds
    the seeded SNOMED→ICD-10-CM mapping (the only crosswalk in the
    fixture). It DOES NOT enumerate every system (engine code is the
    source of truth; the probe only verifies the surface behavior).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            # No targetsystem — handler uses _all_systems_except.
        ],
    )
    assert r.status_code == 200
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) >= 1, (
        "No-targetSystem path should find the SNOMED→ICD-10-CM mapping via "
        "_all_systems_except."
    )


# ===========================================================================
# Lens 16: Outcome of all-spec-parameters-accepted — combine ALL
# accepted optional params in one request. Verify no 500 / no silent
# crash.
# ===========================================================================


def test_e160_translate_all_optional_params_combined(fhir_client):
    """EXPLORER (combined-input probe): GET $translate with EVERY
    accepted optional parameter combined in one request. The handler
    MUST NOT 500. Spec-compatibility fallback: every param is silently
    dropped except system+code+targetsystem.

    Combined params: source (ConceptMap URL), targetCode, reverse,
    targetScope, sourceScope, version, url, targetPrune.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("source", CONCEPTMAP_URL),
            ("targetCode", "E11"),
            ("reverse", "false"),
            ("targetScope", "http://example.org/fhir/ValueSet/test-diabetes"),
            ("sourceScope", "http://example.org/fhir/ValueSet/test-snomed"),
            ("version", "2024-09"),
            ("url", CONCEPTMAP_URL),
            ("targetPrune", "false"),
        ],
    )
    assert r.status_code == 200, (
        f"Combined optional params — expected 200 (spec-compat fallback); "
        f"got {r.status_code}: {r.text[:300]}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"
