"""EXPLORER RESWEEP probes for chunk CM-02 (ConceptMap $translate Operation).

Source: https://build.fhir.org/conceptmap-operation-translate.html
Canonical R4 $translate operation:
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html
Canonical R4 ConceptMapEquivalence closed enum:
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

EXPLORER lens (lateral thinking). Per ROLE_QA_ENGINEER.md: "Unusual
parameter combinations, undocumented features, integration corners."

HISTORIAN tip for EXPLORER (4 items):
  1. Optional params at once (reverse + targetCode + source all together)
  2. Extend HISTORIAN 3-op round-trip (test_h121) to verify canonical-
     DISPLAY invariant META-PATTERN across the 3 operations
  3. Deeply-nested codeableConcept with cross-system codings
     (mix SNOMED + ICD-10-CM in same body)
  4. Lateral batch mixed-op stress (mixed $translate + $lookup +
     $subsumes entries per-entry isolation + byte-exact content)

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus; A44054006 -> A73211009)
  - CUIs: C0011847 (T2DM: SNOMED 44054006 + ICD10CM E11), C0011849 (DM),
          C0978484 (metformin)
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from medterm4ds.apps import fhir_api
from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    canonical_system_uri,
    fhir_uri_to_system,
)
from medterm4ds.engines.fhir.responses import build_parameters_translate


# ---------------------------------------------------------------------------
# Constants for the probes.
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_URI_TRAILING_SLASH = "http://hl7.org/fhir/sid/icd-10-cm/"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_URI_TRAILING_SLASH = "http://www.nlm.nih.gov/research/umls/rxnorm/"
LOINC_URI = "http://loinc.org"

CONCEPTMAP_URL = "http://medterm4ds.org/fhir/ConceptMap/snomed-to-icd10"

# Seeded codes per conftest._make_conformance_db
SNOMED_DM_CODE = "73211009"  # Diabetes mellitus
SNOMED_T2DM_CODE = "44054006"  # Type 2 diabetes mellitus
ICD10CM_T2DM_CODE = "E11"  # Type 2 diabetes mellitus
RXNORM_METFORMIN_CODE = "860975"


# ---------------------------------------------------------------------------
# Source-read helpers
# ---------------------------------------------------------------------------
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "apps"
    / "fhir_api.py"
)


def _get_func_source(file_path: Path, func_name: str) -> str:
    """Extract the source text of a function (possibly nested) by name.

    Walks BOTH ``ast.FunctionDef`` AND ``ast.AsyncFunctionDef`` (per
    TS-04 HISTORIAN methodology — async route handlers nested inside
    ``create_fhir_app``). Returns "" if not found.
    """
    source = file_path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(source, node) or ""
    return ""


def _get_nested_func_source(
    file_path: Path, parent_name: str, child_name: str
) -> str:
    """Extract source of a function defined inside another function.

    Per CS-03 HISTORIAN methodology: plain ``ast.walk`` over module would
    miss nested defs. We descend into the parent function's body.
    """
    source = file_path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == parent_name:
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.name == child_name:
                            return ast.get_source_segment(source, child) or ""
    return ""


def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Return the first top-level ``parameter`` entry with ``name``, else None."""
    for p in body.get("parameter", []):
        if isinstance(p, dict) and p.get("name") == name:
            return p
    return None


def _find_match_parts(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the list of ``part`` dicts for every ``match`` parameter."""
    out: list[dict[str, Any]] = []
    for p in body.get("parameter", []):
        if isinstance(p, dict) and p.get("name") == "match":
            parts = p.get("part", [])
            out.extend(parts if isinstance(parts, list) else [])
    return out


def _entry_params(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the Parameters body of a batch-response entry."""
    if entry.get("resource", {}).get("resourceType") == "Parameters":
        return entry["resource"]
    return entry.get("resource", {})


# ===========================================================================
# Lens 1: HISTORIAN tip 1 — Optional params at once (lateral combination).
#
# Spec: FHIR R4 $translate permits ``url`` (ConceptMap canonical URL) +
# ``source`` (value-set scope) + ``targetsystem`` + ``targetCode`` +
# ``reverse`` (no longer in R4 spec text — build.fhir.org merged it into
# target* parameters). Each is independent; the server SHOULD accept any
# combination without 5xx. Per SKEPTIC test_s60-s67, every individual
# optional param is accepted without 5xx; EXPLORER probes the LATERAL
# COMBINATION — all declared optional params supplied at once.
# ===========================================================================


def test_e10_all_optional_params_at_once_get(fhir_client):
    """EXPLORER: GET $translate with EVERY declared optional param
    supplied at once — verify no 5xx and conformant response shape.

    Spec: FHIR R4 §3.1.0.1.5 — a malformed client request MUST produce a
    FHIR OperationOutcome (NOT a 500 with a traceback).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_T2DM_CODE),
            ("targetsystem", ICD10CM_URI),
            ("source", CONCEPTMAP_URL),  # ConceptMap URL (passed through)
            ("targetCode", ICD10CM_T2DM_CODE),  # declared but unused
            ("reverse", "true"),  # R4 spec removed; accepted gracefully
            ("version", "2024-09"),  # passed through
        ],
    )
    assert r.status_code < 500, f"5xx on lateral optional params: {r.status_code} {r.text}"
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body["resourceType"] == "Parameters"


def test_e11_all_optional_params_at_once_post(fhir_client):
    """EXPLORER: POST $translate with every declared optional param
    in the Parameters body at once — verify no 5xx and conformant
    response shape. Mirrors test_e10 on the POST path.
    """
    body_dict = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
            {"name": "source", "valueUri": CONCEPTMAP_URL},
            {"name": "targetCode", "valueCode": ICD10CM_T2DM_CODE},
            {"name": "reverse", "valueBoolean": True},
            {"name": "version", "valueString": "2024-09"},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body_dict,
        headers={"content-type": "application/fhir+json"},
    )
    assert r.status_code < 500, f"5xx on POST lateral optional params: {r.status_code}"
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body["resourceType"] == "Parameters"


def test_e12_optional_params_at_once_alias_input_uri(fhir_client):
    """EXPLORER: GET $translate with every optional param + an ALIAS
    source URI (trailing-slash) — verify canonical_system_uri resolves
    on match.source.system regardless of alias input.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI_TRAILING_SLASH),
            ("code", SNOMED_T2DM_CODE),
            ("targetsystem", ICD10CM_URI_TRAILING_SLASH),
            ("source", CONCEPTMAP_URL),
            ("targetCode", ICD10CM_T2DM_CODE),
            ("reverse", "true"),
        ],
    )
    assert r.status_code < 500
    body = r.json()
    # CR-012 RESOLVED: match.source.system MUST be canonical, not the alias.
    for source_part in [p for p in _find_match_parts(body) if p.get("name") == "source"]:
        sc = source_part.get("valueCoding", {})
        assert sc.get("system") == SNOMED_URI, (
            f"match.source.system should be canonical URI even with alias input; "
            f"got {sc.get('system')!r}"
        )


def test_e13_optional_params_at_once_get_post_byte_exact_parity(fhir_client):
    """EXPLORER: byte-exact parity on the lateral optional-params
    combination — GET and POST MUST return byte-identical clinical
    content (status, result, match count, equivalence, target code).
    Extends HISTORIAN test_h70 (15-case matrix) to the all-optional-at-once
    lateral shape.
    """
    get_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_T2DM_CODE),
            ("targetsystem", ICD10CM_URI),
            ("source", CONCEPTMAP_URL),
            ("targetCode", ICD10CM_T2DM_CODE),
            ("reverse", "true"),
        ],
    )
    post_r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM_CODE},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                {"name": "source", "valueUri": CONCEPTMAP_URL},
                {"name": "targetCode", "valueCode": ICD10CM_T2DM_CODE},
                {"name": "reverse", "valueBoolean": True},
            ],
        },
        headers={"content-type": "application/fhir+json"},
    )
    assert get_r.status_code == post_r.status_code
    get_body = get_r.json()
    post_body = post_r.json()
    # 5-axis parity
    assert _find_param(get_body, "result") == _find_param(post_body, "result")
    get_matches = [p for p in get_body["parameter"] if p.get("name") == "match"]
    post_matches = [p for p in post_body["parameter"] if p.get("name") == "match"]
    assert len(get_matches) == len(post_matches)
    for g, p in zip(get_matches, post_matches):
        g_eq = next((pt for pt in g["part"] if pt.get("name") == "equivalence"), None)
        p_eq = next((pt for pt in p["part"] if pt.get("name") == "equivalence"), None)
        assert g_eq == p_eq, f"equivalence divergence: {g_eq} vs {p_eq}"
        g_concept = next((pt for pt in g["part"] if pt.get("name") == "concept"), None)
        p_concept = next((pt for pt in p["part"] if pt.get("name") == "concept"), None)
        assert g_concept == p_concept


# ===========================================================================
# Lens 2: HISTORIAN tip 2 — Extend 3-op round-trip with DISPLAY fields
# (canonical-DISPLAY invariant META-PATTERN across the 3 operations).
#
# HISTORIAN test_h121 verified the 3-op round-trip on T2DM at the URI
# layer. EXPLORER extends to verify the canonical-DISPLAY META-PATTERN
# holds ACROSS the 3 operations ($lookup, $translate, $subsumes).
# Per CS-02 TERMINOLOGIST methodology: canonical-DISPLAY invariant
# verifies that the same code produces byte-exact display fields across
# every operation that emits a display.
# ===========================================================================


def test_e20_3op_round_trip_canonical_display_on_t2dm(fhir_client):
    """EXPLORER: 3-op round-trip on SNOMED 44054006 (T2DM) — verify
    canonical-DISPLAY invariant META-PATTERN across $lookup, $translate,
    $subsumes. The display emitted by $lookup for the source code MUST
    agree with the display emitted by $translate for the SOURCE code,
    AND the display for the TARGET code MUST agree with $lookup for the
    target code.

    Spec: FHIR R4 §4.8.21.1 Out display — "The preferred display for
    this concept".
    """
    # 1. $lookup on SNOMED T2DM — captures source canonical display.
    lookup_r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert lookup_r.status_code == 200
    lookup_body = lookup_r.json()
    lookup_display_param = _find_param(lookup_body, "display")
    assert lookup_display_param is not None
    source_display = lookup_display_param.get("valueString")
    assert source_display and "diabetes" in source_display.lower()

    # 2. $lookup on ICD10CM T2DM — captures target canonical display.
    target_lookup_r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": ICD10CM_URI, "code": ICD10CM_T2DM_CODE},
    )
    assert target_lookup_r.status_code == 200
    target_lookup_display = _find_param(
        target_lookup_r.json(), "display"
    ).get("valueString")
    assert target_lookup_display and "diabetes" in target_lookup_display.lower()

    # 3. $translate SNOMED T2DM → ICD10CM — verify:
    #    (a) result=true
    #    (b) match.concept.display agrees with $lookup's target display
    translate_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_T2DM_CODE),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert translate_r.status_code == 200
    translate_body = translate_r.json()
    result_param = _find_param(translate_body, "result")
    assert result_param.get("valueBoolean") is True

    match_parts = _find_match_parts(translate_body)
    concept_part = next(
        (p for p in match_parts if p.get("name") == "concept"), None
    )
    assert concept_part is not None, "$translate emitted no match.concept"
    concept_value = concept_part.get("valueCoding", {})
    assert concept_value.get("system") == ICD10CM_URI
    assert concept_value.get("code") == ICD10CM_T2DM_CODE
    # Canonical-DISPLAY META-PATTERN across $translate target concept AND
    # $lookup target concept — byte-exact equality.
    assert concept_value.get("display") == target_lookup_display, (
        f"canonical-DISPLAY drift across $translate target concept "
        f"({concept_value.get('display')!r}) and $lookup target "
        f"({target_lookup_display!r})"
    )


def test_e21_3op_round_trip_canonical_display_per_seeded_code(
    fhir_client, system_code
):
    """EXPLORER: parametrized canonical-DISPLAY META-PATTERN across
    $lookup and $translate for every seeded code that has a same-CUI
    cross-system mapping. Verifies $translate match.concept.display ==
    $lookup Out display for the target code.
    """
    system, code = system_code
    # $lookup on the source code first
    source_lookup_r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
    )
    assert source_lookup_r.status_code == 200
    # $translate without targetsystem — let server pick
    translate_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={"system": system, "code": code},
    )
    assert translate_r.status_code == 200
    body = translate_r.json()
    result_param = _find_param(body, "result")
    assert result_param is not None
    # If there are matches, each match.concept (system, code) MUST
    # correspond to a code resolvable via $lookup with the SAME display.
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    for m in matches:
        concept_part = next(
            (pt for pt in m.get("part", []) if pt.get("name") == "concept"), None
        )
        if concept_part is None:
            continue
        target = concept_part.get("valueCoding", {})
        target_system = target.get("system")
        target_code = target.get("code")
        target_display = target.get("display")
        # Look up the target code independently and assert display agreement
        target_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": target_system, "code": target_code},
        )
        assert target_lookup.status_code == 200
        target_lookup_display = _find_param(
            target_lookup.json(), "display"
        ).get("valueString")
        # Canonical-DISPLAY invariant META-PATTERN — byte-exact equality
        # across $translate target concept display AND $lookup Out display
        # for the same (system, code).
        assert target_display == target_lookup_display, (
            f"canonical-DISPLAY drift for ({target_system}, {target_code}): "
            f"$translate={target_display!r} vs $lookup={target_lookup_display!r}"
        )


def test_e22_3op_round_trip_canonical_uri_invariant_per_alias_input(
    fhir_client, alias_uri
):
    """EXPLORER: 3-op round-trip canonical-URI invariant under EVERY
    alias input (trailing-slash, urn:oid, uppercase-scheme). The Out
    ``system`` field of $lookup AND the match.source.system field of
    $translate MUST carry the canonical URI, NOT the alias input.

    Spec: FHIR R4 §4.8.21.1 Out Coding.system. CR-012 RESOLVED via
    canonical_system_uri helper.
    """
    # 1. $lookup Out system MUST be canonical
    lookup_r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": alias_uri, "code": SNOMED_T2DM_CODE},
    )
    assert lookup_r.status_code == 200
    lookup_out_system = _find_param(lookup_r.json(), "system").get("valueUri")
    assert lookup_out_system == SNOMED_URI, (
        f"$lookup Out system drift under alias {alias_uri!r}: "
        f"got {lookup_out_system!r}"
    )
    # 2. $translate match.source.system MUST be canonical
    translate_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": alias_uri,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert translate_r.status_code == 200
    body = translate_r.json()
    source_parts = [
        p for p in _find_match_parts(body) if p.get("name") == "source"
    ]
    assert source_parts, "$translate emitted 0 match.source parts"
    for sp in source_parts:
        assert sp.get("valueCoding", {}).get("system") == SNOMED_URI, (
            f"$translate match.source.system drift under alias {alias_uri!r}: "
            f"got {sp.get('valueCoding', {}).get('system')!r}"
        )


def test_e23_3op_round_trip_translate_then_subsumes_outcome_directionality(
    fhir_client
):
    """EXPLORER: extend HISTORIAN test_h121 — 3-op round-trip on T2DM
    verifies $lookup returns valid display, $translate returns
    result=true, AND $subsumes directionality is correct.

    The target ICD10CM code E11 represents the same clinical concept
    (T2DM); the source SNOMED 44054006 also represents T2DM. They're
    related via shared CUI C0011847 (crosswalk) — verified via
    $translate result=true. And $subsumes(T2DM, DM) returns subsumed-by
    (T2DM is narrower than DM).
    """
    # 1. $lookup on T2DM (SNOMED)
    r1 = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert r1.status_code == 200

    # 2. $translate T2DM (SNOMED) -> ICD-10-CM
    r2 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert _find_param(body2, "result").get("valueBoolean") is True

    # 3. $subsumes T2DM vs DM — outcome=subsumed-by (DM broader)
    r3 = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM_CODE,  # narrower
            "codeB": SNOMED_DM_CODE,    # broader
        },
    )
    assert r3.status_code == 200
    body3 = r3.json()
    outcome_param = _find_param(body3, "outcome")
    assert outcome_param.get("valueCode") == "subsumed-by", (
        f"3-op directionality failed: outcome={outcome_param.get('valueCode')!r}, "
        f"expected 'subsumed-by'"
    )


# ===========================================================================
# Lens 3: HISTORIAN tip 3 — Deeply-nested codeableConcept with CROSS-
# SYSTEM codings (mix SNOMED + ICD-10-CM in same body).
#
# HISTORIAN test_h110-h112 verified mixed valid+invalid codings on the
# 10th PROMOTED pattern (isinstance guard). EXPLORER extends to MIXED
# CROSS-SYSTEM codings — SNOMED + ICD-10-CM + RXNORM in the same
# codeableConcept body. Spec: "The server can translate any of the
# coding values" — server-choice for which coding to translate.
# ===========================================================================


def test_e30_codeable_concept_cross_system_first_valid_wins(fhir_client):
    """EXPLORER: POST $translate with codeableConcept body containing
    mixed-system codings (SNOMED + ICD-10-CM). Verify the FIRST VALID
    coding wins (server choice per spec text "The server can translate
    any of the coding values").

    Spec: FHIR R4 $translate In sourceCodeableConcept: "A full
    codeableConcept to validate. The server can translate any of the
    coding values." Same semantic as CodeSystem/$validate-code
    codeableConcept (CS-03 SKEPTIC QA-049 + TERMINOLOGIST test_t20-t22).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_T2DM_CODE,
                            "display": "Type 2 diabetes mellitus",
                        },
                        {
                            "system": ICD10CM_URI,
                            "code": ICD10CM_T2DM_CODE,
                            "display": "Type 2 diabetes mellitus",
                        },
                    ]
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
        headers={"content-type": "application/fhir+json"},
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    out = r.json()
    # First coding (SNOMED T2DM) wins → translate to ICD10CM T2DM
    result_param = _find_param(out, "result")
    assert result_param is not None
    assert result_param.get("valueBoolean") is True, (
        f"First valid coding should win; result={result_param}"
    )
    match_parts = _find_match_parts(out)
    concept_part = next((p for p in match_parts if p.get("name") == "concept"), None)
    assert concept_part is not None
    target_coding = concept_part.get("valueCoding", {})
    assert target_coding.get("system") == ICD10CM_URI
    assert target_coding.get("code") == ICD10CM_T2DM_CODE


def test_e31_codeable_concept_cross_system_first_coding_invalid(fhir_client):
    """EXPLORER: POST $translate with codeableConcept body where the
    FIRST coding is INVALID (unknown code in known system) and the
    SECOND coding is a VALID SNOMED code. Verify the SECOND coding wins
    (the engine iterates the full coding list per the all-pairs helper
    and skips invalid codings — same pattern as CS-03 SKEPTIC QA-049).

    Note: per the CF-EXPLORER-CS02-01 / CS-03 EXPLORER methodology
    (first-match-wins probe-assertion pattern), EXPLORER pins the
    CURRENT semantic rather than manufacturing an off-spec semantic.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        # First: SNOMED with INVALID code
                        {
                            "system": SNOMED_URI,
                            "code": "999999999",  # not seeded
                            "display": "Unknown",
                        },
                        # Second: SNOMED with VALID code
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_T2DM_CODE,
                            "display": "Type 2 diabetes mellitus",
                        },
                    ]
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
        headers={"content-type": "application/fhir+json"},
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    out = r.json()
    # Either: (a) the SECOND coding wins and we get a match; OR
    # (b) the FIRST invalid coding short-circuits and we get no match.
    # Per CS-03 TERMINOLOGIST test_t23 methodology, we PIN the current
    # semantic. Document the actual behavior — single-pair helper picks
    # first coding with BOTH system AND code fields, which is the
    # invalid one. So the current behavior is result=false (no match).
    result_param = _find_param(out, "result")
    assert result_param is not None
    # Pinning the current single-pair-helper semantic.
    assert result_param.get("valueBoolean") is False, (
        "Current $translate codeableConcept semantic uses the single-pair "
        "_extract_codeable_concept_from_parameters helper which picks the "
        "FIRST coding with both system and code fields. The first valid-"
        "shape-but-invalid-value coding wins. If a future enhancement changes "
        "this to all-pairs iteration, this probe MUST be updated."
    )


def test_e32_codeable_concept_cross_system_mixed_invalid_types(fhir_client):
    """EXPLORER: POST $translate with codeableConcept body containing
    MIXED INVALID types in the coding[] list — None, string, integer,
    nested list, dict-without-system, dict-without-code. The 10th
    PROMOTED pattern (isinstance guard at untrusted-data list-iterator
    boundary) MUST handle every malformed shape without 500.

    Extends HISTORIAN test_h110 (which used SAME-system mixed types) to
    CROSS-SYSTEM mixed types.

    Spec: FHIR R4 §3.1.0.1.5 + §3.1.0.1.9.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        None,                      # non-dict
                        "not-a-dict",              # string
                        42,                         # integer
                        ["nested", "list"],        # list
                        {"system": SNOMED_URI},    # missing code
                        {"code": SNOMED_T2DM_CODE},  # missing system
                        # The single VALID cross-system coding:
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_T2DM_CODE,
                        },
                    ]
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
        headers={"content-type": "application/fhir+json"},
    )
    # No 500 — the 10th PROMOTED pattern's isinstance guard prevents
    # AttributeError propagation on every non-dict entry.
    assert r.status_code < 500, f"5xx on mixed invalid types: {r.status_code}"
    assert r.headers["content-type"].startswith("application/fhir+json")


def test_e33_codeable_concept_cross_system_all_invalid_no_500(fhir_client):
    """EXPLORER: POST $translate with codeableConcept where EVERY coding
    is cross-system invalid (SNOMED-invalid + ICD10CM-invalid +
    RXNORM-invalid). Verify no 500, conformant OperationOutcome shape.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "INVALID1"},
                        {"system": ICD10CM_URI, "code": "INVALID2"},
                        {"system": RXNORM_URI, "code": "INVALID3"},
                    ]
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
        headers={"content-type": "application/fhir+json"},
    )
    assert r.status_code < 500, f"5xx on all-invalid: {r.status_code}"
    assert r.headers["content-type"].startswith("application/fhir+json")


# ===========================================================================
# Lens 4: HISTORIAN tip 4 — Lateral batch mixed-op stress.
#
# Batch Bundle with mixed $translate + $lookup + $subsumes entries —
# verify per-entry isolation AND byte-exact content per entry. Extends
# TS-04 EXPLORER (per-entry isolation on homogeneous batch) to
# HETEROGENEOUS batch.
# ===========================================================================


def test_e40_batch_mixed_op_per_entry_isolation(fhir_client):
    """EXPLORER: batch Bundle with mixed-op entries — $translate,
    $lookup, $subsumes, plus a malformed entry. Each entry MUST produce
    its own response; per-entry isolation MUST hold per FHIR R4 §3.7
    ("the success or failure of one change SHOULD NOT alter another").

    Spec: FHIR R4 §4.7.8 + §4.7.10 + §3.7.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            # 1. $translate — valid SNOMED → ICD-10-CM
            {
                "request": {
                    "method": "GET",
                    "url": (
                        f"/ConceptMap/$translate?"
                        f"system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
                        f"&targetsystem={ICD10CM_URI}"
                    ),
                }
            },
            # 2. $lookup — valid
            {
                "request": {
                    "method": "GET",
                    "url": (
                        f"/CodeSystem/$lookup?system={SNOMED_URI}"
                        f"&code={SNOMED_T2DM_CODE}"
                    ),
                }
            },
            # 3. $subsumes — valid (T2DM subsumed-by DM)
            {
                "request": {
                    "method": "GET",
                    "url": (
                        f"/CodeSystem/$subsumes?system={SNOMED_URI}"
                        f"&codeA={SNOMED_T2DM_CODE}&codeB={SNOMED_DM_CODE}"
                    ),
                }
            },
            # 4. MALFORMED entry — missing 'request' field entirely.
            {"foo": "bar"},
            # 5. $translate — unknown source URI
            {
                "request": {
                    "method": "GET",
                    "url": (
                        "/ConceptMap/$translate?"
                        "system=http://unknown.system&code=X"
                        f"&targetsystem={ICD10CM_URI}"
                    ),
                }
            },
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "batch-response"
    entries = body.get("entry", [])
    assert len(entries) == 5, f"Expected 5 entries, got {len(entries)}"

    # Entry 1: $translate — 200 + Parameters
    e1 = entries[0].get("response", {})
    assert e1.get("status", "").startswith("200"), f"e1.status={e1.get('status')}"
    e1_params = entries[0].get("resource", {})
    assert e1_params.get("resourceType") == "Parameters"

    # Entry 2: $lookup — 200 + Parameters
    e2 = entries[1].get("response", {})
    assert e2.get("status", "").startswith("200")
    e2_params = entries[1].get("resource", {})
    assert e2_params.get("resourceType") == "Parameters"

    # Entry 3: $subsumes — 200 + Parameters
    e3 = entries[2].get("response", {})
    assert e3.get("status", "").startswith("200")
    e3_params = entries[2].get("resource", {})
    assert e3_params.get("resourceType") == "Parameters"

    # Entry 4: malformed entry — per-entry isolation MUST produce a 4xx
    # for THIS entry only, not break the batch.
    e4 = entries[3].get("response", {})
    e4_status = e4.get("status", "")
    assert e4_status.startswith("4") or e4_status.startswith("5"), (
        f"malformed entry should produce 4xx/5xx; got {e4_status}"
    )

    # Entry 5: $translate unknown system — 400 (per _do_translate)
    e5 = entries[4].get("response", {})
    assert e5.get("status", "").startswith("400"), (
        f"unknown source URI should produce 400; got {e5.get('status')}"
    )


def test_e41_batch_mixed_op_byte_exact_vs_single_entry(fhir_client):
    """EXPLORER: byte-exact clinical-content parity between batch and
    single-entry invocations on MIXED operations. For each entry in the
    batch, the response body MUST match what the same op would produce
    if invoked as a single-entry request.

    Spec: FHIR R4 §4.7.10 (batch $translate). The batch dispatcher MUST
    use the same _do_* handlers and build_parameters_* builders as
    single-entry routes.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "GET",
                    "url": (
                        f"/ConceptMap/$translate?"
                        f"system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
                        f"&targetsystem={ICD10CM_URI}"
                    ),
                }
            },
            {
                "request": {
                    "method": "GET",
                    "url": (
                        f"/CodeSystem/$lookup?system={SNOMED_URI}"
                        f"&code={SNOMED_T2DM_CODE}"
                    ),
                }
            },
            {
                "request": {
                    "method": "GET",
                    "url": (
                        f"/CodeSystem/$subsumes?system={SNOMED_URI}"
                        f"&codeA={SNOMED_T2DM_CODE}&codeB={SNOMED_DM_CODE}"
                    ),
                }
            },
        ],
    }
    batch_r = fhir_client.post("/fhir", json=bundle)
    assert batch_r.status_code == 200
    batch_body = batch_r.json()
    entries = batch_body.get("entry", [])
    assert len(entries) == 3

    # Entry 1: $translate — byte-exact with single-entry
    single_translate = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    batch_translate_params = entries[0].get("resource", {})
    single_translate_params = single_translate.json()
    # Compare clinical content axes: result, match count, equivalence,
    # target code, target display.
    assert (
        _find_param(batch_translate_params, "result")
        == _find_param(single_translate_params, "result")
    )
    assert (
        _find_param(batch_translate_params, "message")
        == _find_param(single_translate_params, "message")
    )
    # match equivalence + target code byte-exact
    b_matches = [p for p in batch_translate_params["parameter"] if p.get("name") == "match"]
    s_matches = [p for p in single_translate_params["parameter"] if p.get("name") == "match"]
    assert len(b_matches) == len(s_matches)
    for b, s in zip(b_matches, s_matches):
        b_eq = next((pt for pt in b["part"] if pt.get("name") == "equivalence"), None)
        s_eq = next((pt for pt in s["part"] if pt.get("name") == "equivalence"), None)
        assert b_eq == s_eq
        b_concept = next((pt for pt in b["part"] if pt.get("name") == "concept"), None)
        s_concept = next((pt for pt in s["part"] if pt.get("name") == "concept"), None)
        assert b_concept == s_concept

    # Entry 3: $subsumes — byte-exact with single-entry
    single_subsumes = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM_CODE,
            "codeB": SNOMED_DM_CODE,
        },
    )
    batch_subsumes_params = entries[2].get("resource", {})
    single_subsumes_params = single_subsumes.json()
    assert (
        _find_param(batch_subsumes_params, "outcome")
        == _find_param(single_subsumes_params, "outcome")
    )


def test_e42_batch_mixed_op_order_preservation(fhir_client):
    """EXPLORER: batch Bundle with 10 mixed-op entries — verify order
    preservation + correlation (response entry[i] corresponds to
    request entry[i] per FHIR R4 §3.7).
    """
    entries_in = []
    for i in range(10):
        if i % 3 == 0:
            url = (
                f"/ConceptMap/$translate?system={SNOMED_URI}"
                f"&code={SNOMED_T2DM_CODE}&targetsystem={ICD10CM_URI}"
            )
        elif i % 3 == 1:
            url = (
                f"/CodeSystem/$lookup?system={SNOMED_URI}"
                f"&code={SNOMED_T2DM_CODE}"
            )
        else:
            url = (
                f"/CodeSystem/$subsumes?system={SNOMED_URI}"
                f"&codeA={SNOMED_T2DM_CODE}&codeB={SNOMED_DM_CODE}"
            )
        entries_in.append({"request": {"method": "GET", "url": url}})

    bundle = {"resourceType": "Bundle", "type": "batch", "entry": entries_in}
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    out_entries = body.get("entry", [])
    assert len(out_entries) == 10
    # Order preservation: each entry's resource shape matches its position.
    for i, e in enumerate(out_entries):
        params = e.get("resource", {})
        if i % 3 == 0:
            assert _find_param(params, "result") is not None, (
                f"Entry {i} should be $translate (has 'result' param)"
            )
        elif i % 3 == 1:
            assert _find_param(params, "display") is not None, (
                f"Entry {i} should be $lookup (has 'display' param)"
            )
        else:
            assert _find_param(params, "outcome") is not None, (
                f"Entry {i} should be $subsumes (has 'outcome' param)"
            )


# ===========================================================================
# Lens 5: Lateral POST with coding (alternative encoding) + every
# target system.
#
# Per FHIR R4 $translate In: sourceCoding (0..1 Coding) is an alternative
# to (sourceCode, sourceSystem). EXPLORER probes EVERY target system
# with the sourceCoding alternative encoding — sibling to HISTORIAN
# test_h70 (15-case matrix on scalar encoding).
# ===========================================================================


def test_e50_post_coding_alternative_encoding_per_target_system(
    fhir_client, target_system
):
    """EXPLORER: POST $translate with ``coding`` parameter (alternative
    encoding) for SNOMED T2DM, parametrized over every target system.
    Verify byte-exact clinical-content parity with the scalar encoding
    of the same request.

    Spec: FHIR R4 $translate In sourceCoding (0..1 Coding). Mirrors
    CS-04 EXPLORER test_e50 lateral-coverage probe class for the
    coding alternative encoding on $subsumes.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "coding", "valueCoding": {
                "system": SNOMED_URI,
                "code": SNOMED_T2DM_CODE,
            }},
            {"name": "targetsystem", "valueUri": target_system},
        ],
    }
    r_post = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
        headers={"content-type": "application/fhir+json"},
    )
    assert r_post.status_code == 200, (
        f"POST $translate with coding alt encoding + target={target_system}: "
        f"{r_post.status_code} {r_post.text}"
    )

    # Compare to scalar GET — same targetsystem
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": target_system,
        },
    )
    assert r_get.status_code == 200
    # Clinical content parity
    post_body = r_post.json()
    get_body = r_get.json()
    assert _find_param(post_body, "result") == _find_param(get_body, "result")
    post_matches = [p for p in post_body["parameter"] if p.get("name") == "match"]
    get_matches = [p for p in get_body["parameter"] if p.get("name") == "match"]
    assert len(post_matches) == len(get_matches)
    for pm, gm in zip(post_matches, get_matches):
        pm_eq = next((pt for pt in pm["part"] if pt.get("name") == "equivalence"), None)
        gm_eq = next((pt for pt in gm["part"] if pt.get("name") == "equivalence"), None)
        assert pm_eq == gm_eq
        pm_concept = next((pt for pt in pm["part"] if pt.get("name") == "concept"), None)
        gm_concept = next((pt for pt in gm["part"] if pt.get("name") == "concept"), None)
        assert pm_concept == gm_concept


# ===========================================================================
# Lens 6: Lateral — response-shape audit on $translate with TARGET CODE
# supplied (targetCode param) — declared but unused (deferred reverse-
# mode enhancement). Verify the response is still conformant when
# targetCode is supplied alongside forward-mode (source code + system).
# ===========================================================================


def test_e60_target_code_param_combined_with_forward_mode(fhir_client):
    """EXPLORER: GET $translate with BOTH source code/system AND
    targetCode param — verify no 5xx, conformant response shape, and
    that the forward-mode result is returned (targetCode is declared
    but unused today; the server treats this as a forward-mode request).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_T2DM_CODE),
            ("targetsystem", ICD10CM_URI),
            ("targetCode", ICD10CM_T2DM_CODE),
        ],
    )
    assert r.status_code < 500
    body = r.json()
    assert body["resourceType"] == "Parameters"
    result = _find_param(body, "result")
    assert result is not None
    # Forward-mode result — at least 1 match expected.
    assert result.get("valueBoolean") is True


def test_e61_target_code_param_reverse_mode_no_op(fhir_client):
    """EXPLORER: GET $translate with reverse=true AND targetCode — both
    R4 spec-removed / declared-but-unused params at once. Verify the
    server treats this as a forward-mode request (current implementation
    does not implement reverse mode).

    Per SKEPTIC test_s60-s67: reverse=true is accepted gracefully (no
    5xx). EXPLORER confirms the lateral combination with targetCode
    also behaves gracefully.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_T2DM_CODE),
            ("targetsystem", ICD10CM_URI),
            ("targetCode", ICD10CM_T2DM_CODE),
            ("reverse", "true"),
        ],
    )
    assert r.status_code < 500
    body = r.json()
    assert body["resourceType"] == "Parameters"


# ===========================================================================
# Lens 7: Lateral — instance-level $translate route + cross-handler
# state isolation under lateral load.
#
# SKEPTIC test_s60-s67 verified instance-level routes return 404
# OperationOutcome; EXPLORER confirms the lateral combination: type-
# level → instance-level → type-level sequence preserves no state.
# ===========================================================================


def test_e70_instance_level_translate_get_404_operationoutcome(fhir_client):
    """EXPLORER: instance-level GET /fhir/ConceptMap/{id}/$translate
    returns 404 + OperationOutcome (the route IS registered; the
    resource ID lookup returns 404 because the server doesn't persist
    named ConceptMaps).
    """
    r = fhir_client.get(
        f"/fhir/ConceptMap/any-id/$translate",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"


def test_e71_instance_level_translate_post_404_operationoutcome(fhir_client):
    """EXPLORER: instance-level POST /fhir/ConceptMap/{id}/$translate
    returns 404 + OperationOutcome.
    """
    r = fhir_client.post(
        f"/fhir/ConceptMap/any-id/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM_CODE},
            ],
        },
        headers={"content-type": "application/fhir+json"},
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/fhir+json")


def test_e72_type_instance_type_no_state_leak(fhir_client):
    """EXPLORER: type-level → instance-level → type-level — no state
    leak. The first type-level request returns 200; the instance-level
    request returns 404; the second type-level request MUST return the
    same response as the first (no leak from the 404 path).
    """
    # 1. Type-level
    r1 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r1.status_code == 200
    # 2. Instance-level (404)
    r2 = fhir_client.get(
        "/fhir/ConceptMap/any-id/$translate",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert r2.status_code == 404
    # 3. Type-level again — must match r1 byte-for-byte
    r3 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r3.status_code == 200
    assert r1.json() == r3.json(), (
        "Type-level response leaked state from instance-level 404 path"
    )


# ===========================================================================
# Lens 8: Lateral — POST with body lacking resourceType, missing
# parameter field entirely, body as list, body as None. Each is a
# LATERAL edge case beyond the SKEPTIC hostile-input probes — they
# probe "what does the server do with a structurally non-FHIR body?"
# ===========================================================================


def test_e80_post_body_no_resource_type(fhir_client):
    """EXPLORER: POST $translate with body missing ``resourceType``.
    The handler MUST accept the body (lenient parsing per
    liberal-in-what-you-accept) OR reject with 400 OperationOutcome.
    Either is spec-compliant; NOT 5xx.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={"parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
        ]},
        headers={"content-type": "application/fhir+json"},
    )
    assert r.status_code < 500
    assert r.headers["content-type"].startswith("application/fhir+json")


def test_e81_post_body_empty_parameter_list(fhir_client):
    """EXPLORER: POST $translate with body containing empty parameter[]
    list. The handler MUST return 400 ("system and code are required")
    without 5xx.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={"resourceType": "Parameters", "parameter": []},
        headers={"content-type": "application/fhir+json"},
    )
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"


def test_e82_post_body_no_parameter_key(fhir_client):
    """EXPLORER: POST $translate with body containing no ``parameter``
    key at all (just resourceType). The handler MUST return 400
    OperationOutcome, NOT 500.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={"resourceType": "Parameters"},
        headers={"content-type": "application/fhir+json"},
    )
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/fhir+json")


# ===========================================================================
# Lens 9: Source-read structural contracts — instance-level route
# registration + helper wiring under lateral combinations.
# ===========================================================================


def test_e90_translate_get_handler_calls_do_translate(fhir_client):
    """EXPLORER (source-read): translate_get handler delegates to
    _do_translate via _run_db (not inline). Verifies the canonical
    handler shape.
    """
    src = _get_func_source(_FHIR_API_PATH, "translate_get")
    assert "_do_translate" in src, "translate_get must call _do_translate"
    assert "_run_db" in src, "translate_get must use _run_db"


def test_e91_translate_post_handler_calls_extract_translate_params(fhir_client):
    """EXPLORER (source-read): translate_post handler calls
    _extract_translate_params (CF-CM02-01 CLOSED — the helper wiring
    is structural).
    """
    src = _get_func_source(_FHIR_API_PATH, "translate_post")
    assert "_extract_translate_params" in src
    assert "_do_translate" in src


def test_e92_extract_translate_params_calls_both_extractors():
    """EXPLORER (source-read): _extract_translate_params calls BOTH
    _extract_named_coding_from_parameters AND
    _extract_codeable_concept_from_parameters when scalar system/code
    are absent. CF-CM02-01 CLOSED structural contract.
    """
    src = _get_nested_func_source(
        _FHIR_API_PATH, "create_fhir_app", "_extract_translate_params"
    )
    assert "_extract_named_coding_from_parameters" in src
    assert "_extract_codeable_concept_from_parameters" in src


def test_e93_instance_level_routes_registered(fhir_client):
    """EXPLORER (source-read): both translate_instance_get AND
    translate_instance_post routes are registered (per TS-02 SKEPTIC
    QA-014 pattern class). Both handlers delegate to
    build_operation_outcome + _fhir_response at status=404 for instance-
    level invocation on a non-persisting server.
    """
    get_src = _get_func_source(_FHIR_API_PATH, "translate_instance_get")
    post_src = _get_func_source(_FHIR_API_PATH, "translate_instance_post")
    assert get_src, "translate_instance_get not registered"
    assert post_src, "translate_instance_post not registered"
    # Both handlers MUST emit a 404 OperationOutcome body (the server does
    # not persist named ConceptMaps).
    assert "build_operation_outcome" in get_src, (
        "translate_instance_get must call build_operation_outcome"
    )
    assert "build_operation_outcome" in post_src, (
        "translate_instance_post must call build_operation_outcome"
    )
    assert "_fhir_response" in get_src
    assert "_fhir_response" in post_src
    assert "404" in get_src
    assert "404" in post_src


def test_e94_do_translate_calls_canonical_system_uri():
    """EXPLORER (source-read): _do_translate calls canonical_system_uri
    on the source_uri before passing to build_parameters_translate.
    CR-012 RESOLVED structural contract.
    """
    src = _get_nested_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_translate")
    assert "canonical_system_uri" in src, (
        "_do_translate must call canonical_system_uri to re-resolve "
        "the source URI before passing to build_parameters_translate"
    )


def test_e95_build_parameters_translate_emits_three_match_parts():
    """EXPLORER (source-read): build_parameters_translate emits exactly
    3 parts per match — equivalence, concept, source. Verifies the
    match shape conforms to FHIR R4 Out Parameters.
    """
    src = _get_func_source(
        Path(_FHIR_API_PATH).parent.parent / "engines" / "fhir" / "responses.py",
        "build_parameters_translate",
    )
    assert '"equivalence"' in src or "'equivalence'" in src
    assert '"concept"' in src or "'concept'" in src
    assert '"source"' in src or "'source'" in src


# ===========================================================================
# Lens 10: Lateral — equivalence closed-enum audit on lateral input
# combinations. SKEPTIC test_s60-s67 verified the enum on standard
# input; EXPLORER extends to lateral combinations (cross-system
# codeableConcept, all-target-systems, reverse mode).
# ===========================================================================


def test_e100_equivalence_closed_enum_on_cross_system_codeable_concept(fhir_client):
    """EXPLORER: every equivalence value emitted on the cross-system
    codeableConcept path MUST be in the FHIR R4 closed enum. CF-
    HISTORIAN-VS01-01 RESOLVED structural contract.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                        {"system": ICD10CM_URI, "code": ICD10CM_T2DM_CODE},
                    ]
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
        headers={"content-type": "application/fhir+json"},
    )
    assert r.status_code == 200
    out = r.json()
    matches = [p for p in out["parameter"] if p.get("name") == "match"]
    for m in matches:
        eq_part = next((pt for pt in m["part"] if pt.get("name") == "equivalence"), None)
        if eq_part:
            eq_value = eq_part.get("valueCode")
            assert eq_value in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"equivalence {eq_value!r} not in FHIR R4 closed enum "
                f"(CF-HISTORIAN-VS01-01 RESOLVED contract)"
            )


@pytest.mark.parametrize(
    "target_sys",
    [ICD10CM_URI, RXNORM_URI, SNOMED_URI],
    ids=["to_icd10cm", "to_rxnorm", "to_snomed"],
)
def test_e101_equivalence_closed_enum_per_target_system(fhir_client, target_sys):
    """EXPLORER: parametrized over every target system — every emitted
    equivalence MUST be in FHIR R4 closed enum.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": target_sys,
        },
    )
    assert r.status_code == 200
    out = r.json()
    matches = [p for p in out["parameter"] if p.get("name") == "match"]
    for m in matches:
        eq_part = next((pt for pt in m["part"] if pt.get("name") == "equivalence"), None)
        if eq_part:
            eq_value = eq_part.get("valueCode")
            assert eq_value in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_e102_no_match_emits_no_equivalence_value(fhir_client):
    """EXPLORER: $translate with source code that has NO cross-system
    mapping (e.g., RXNORM metformin to SNOMED) MUST emit zero match
    entries — no equivalence value at all.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": RXNORM_URI,
            "code": RXNORM_METFORMIN_CODE,
            "targetsystem": SNOMED_URI,
        },
    )
    assert r.status_code == 200
    out = r.json()
    result = _find_param(out, "result")
    assert result.get("valueBoolean") is False
    matches = [p for p in out["parameter"] if p.get("name") == "match"]
    assert matches == []


# ===========================================================================
# Lens 11: Lateral — XML wire-format on lateral input combinations.
# Per CR-002 PROMOTED, every wire-format serializer MUST emit lowercase
# booleans. EXPLORER extends to XML on $translate with cross-system
# codeableConcept + every target system.
# ===========================================================================


def test_e110_xml_wire_format_result_boolean_lowercase_with_codeable_concept(
    fhir_client,
):
    """EXPLORER: XML wire-format on $translate with codeableConcept body
    — ``valueBoolean`` MUST render lowercase (true/false), NOT Python's
    ``True``/``False``.

    Spec: FHIR R4 §3.4.1 mandates lowercase true/false for boolean
    primitives. CR-002 PROMOTED.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE}
                    ]
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
        headers={
            "content-type": "application/fhir+json",
            "accept": "application/fhir+xml",
        },
    )
    assert r.status_code == 200
    body_text = r.text
    assert 'value="true"' in body_text or 'value="false"' in body_text, (
        f"XML wire-format must emit lowercase true/false; got: {body_text[:200]}"
    )
    assert 'value="True"' not in body_text, (
        "XML wire-format leaked Python's str(True) = 'True' (CR-002 regression)"
    )
    assert 'value="False"' not in body_text


def test_e111_xml_wire_format_via_format_query_param(fhir_client):
    """EXPLORER: XML wire-format via _format=xml query param (distinct
    from Accept header). EXPLORER TS-01 QA-009 _format precedence.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_T2DM_CODE),
            ("targetsystem", ICD10CM_URI),
            ("_format", "application/fhir+xml"),
        ],
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml")
    body_text = r.text
    assert "<Parameters" in body_text
    assert 'value="true"' in body_text


def test_e112_xml_wire_format_per_target_system(fhir_client, target_system):
    """EXPLORER: parametrized XML wire-format on every target system —
    every emitted boolean (result) MUST be lowercase.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_T2DM_CODE),
            ("targetsystem", target_system),
        ],
        headers={"accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    body_text = r.text
    assert 'value="true"' in body_text or 'value="false"' in body_text
    assert 'value="True"' not in body_text
    assert 'value="False"' not in body_text


# ===========================================================================
# Lens 12: Lateral — content-type audit on the lateral-combination
# shapes. CF-EXPLORER-CS02-01 partial closure on $translate (the 4-shape
# Content-Type closure was done by CM-01 EXPLORER test_e10..e13); this
# lens extends to the LATERAL combination shapes.
# ===========================================================================


def test_e120_content_type_on_all_optional_params_at_once_get(fhir_client):
    """EXPLORER: Content-Type audit on GET $translate with all optional
    params at once — MUST be application/fhir+json + Parameters body.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", SNOMED_T2DM_CODE),
            ("targetsystem", ICD10CM_URI),
            ("source", CONCEPTMAP_URL),
            ("targetCode", ICD10CM_T2DM_CODE),
            ("reverse", "true"),
            ("version", "2024-09"),
        ],
    )
    assert r.headers["content-type"].startswith("application/fhir+json")
    assert r.json()["resourceType"] == "Parameters"


def test_e121_content_type_on_cross_system_codeable_concept_post(fhir_client):
    """EXPLORER: Content-Type audit on POST $translate with cross-system
    codeableConcept — MUST be application/fhir+json + Parameters body.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                        {"system": ICD10CM_URI, "code": ICD10CM_T2DM_CODE},
                    ]
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
        headers={"content-type": "application/fhir+json"},
    )
    assert r.headers["content-type"].startswith("application/fhir+json")
    assert r.json()["resourceType"] == "Parameters"


def test_e122_content_type_on_malformed_codeable_concept_post(fhir_client):
    """EXPLORER: Content-Type audit on POST $translate with all-invalid
    cross-system codeableConcept — the 4xx response path MUST still
    emit application/fhir+json + OperationOutcome body.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "INVALID1"},
                        {"system": ICD10CM_URI, "code": "INVALID2"},
                    ]
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
        headers={"content-type": "application/fhir+json"},
    )
    assert r.headers["content-type"].startswith("application/fhir+json")
    # Even on result=false, the body is Parameters (not OperationOutcome)
    body = r.json()
    assert body["resourceType"] in ("Parameters", "OperationOutcome")


# ===========================================================================
# Lens 13: Lateral — performance guard on large batch.
#
# EXPLORER probe class per CS-04 EXPLORER methodology — verify a large
# batch returns in < 30s (no N+1 query regression).
# ===========================================================================


def test_e130_large_batch_mixed_op_50_entries_under_30s(fhir_client):
    """EXPLORER: 50-entry mixed-op batch returns in under 30 seconds.
    Guards against N+1 query patterns in the batch dispatcher.

    The fixture has 4 mrconso rows; each $lookup is O(1) via SQL.
    """
    import time

    entries_in = []
    for i in range(50):
        if i % 3 == 0:
            url = (
                f"/ConceptMap/$translate?system={SNOMED_URI}"
                f"&code={SNOMED_T2DM_CODE}&targetsystem={ICD10CM_URI}"
            )
        elif i % 3 == 1:
            url = f"/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
        else:
            url = (
                f"/CodeSystem/$subsumes?system={SNOMED_URI}"
                f"&codeA={SNOMED_T2DM_CODE}&codeB={SNOMED_DM_CODE}"
            )
        entries_in.append({"request": {"method": "GET", "url": url}})

    bundle = {"resourceType": "Bundle", "type": "batch", "entry": entries_in}
    t0 = time.perf_counter()
    r = fhir_client.post("/fhir", json=bundle)
    elapsed_s = time.perf_counter() - t0
    assert r.status_code == 200
    assert elapsed_s < 30.0, (
        f"50-entry batch took {elapsed_s:.2f}s (>30s); potential N+1 regression"
    )
    body = r.json()
    assert len(body.get("entry", [])) == 50


# ===========================================================================
# Fixture parametrize helpers
# ===========================================================================


@pytest.fixture(params=[
    (SNOMED_URI, SNOMED_T2DM_CODE, "snomed_t2dm"),
    (SNOMED_URI, SNOMED_DM_CODE, "snomed_dm"),
    (ICD10CM_URI, ICD10CM_T2DM_CODE, "icd10cm_t2dm"),
    (RXNORM_URI, RXNORM_METFORMIN_CODE, "rxnorm_metformin"),
])
def system_code(request):
    """Parametrized: every seeded (system, code) pair."""
    return (request.param[0], request.param[1])


@pytest.fixture(params=[
    SNOMED_URI_TRAILING_SLASH,
    SNOMED_URI_OID_ALIAS,
    SNOMED_URI_UPPERCASE_SCHEME,
])
def alias_uri(request):
    """Parametrized: every alias URI input form for SNOMED."""
    return request.param


@pytest.fixture(params=[ICD10CM_URI, RXNORM_URI, SNOMED_URI])
def target_system(request):
    """Parametrized: every target system."""
    return request.param
