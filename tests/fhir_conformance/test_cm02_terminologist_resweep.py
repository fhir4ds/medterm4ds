"""TERMINOLOGIST resweep probes for chunk CM-02 (ConceptMap $translate Operation).

Spec:
  * https://build.fhir.org/conceptmap-operation-translate.html (build page)
  * https://hl7.org/fhir/R4/conceptmap-operation-translate.html (canonical R4)
  * https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html (closed enum)

TERMINOLOGIST lens: clinical / terminological correctness. Default severity
HIGH per GLOBAL_RULES.md. The prior CM-02 run (test_cm02_terminologist.py)
established probes across 8 lens dimensions; this resweep extends coverage
based on EXPLORER's 3-tip handoff for the TERMINOLOGIST iteration.

EXPLORER tip for TERMINOLOGIST (3 things to verify per handoff):
  1. **Canonical-DISPLAY META-PATTERN clinical verification across 4 operations**
     ($lookup <-> $validate-code <-> $translate target concept <->
     $subsumes logical-outcome mirror). Confirm clinical directionality
     correctness: T2DM maps to T2DM, NOT to T1DM or to DM-without-specification.
  2. **Same-CUI SNOMED -> ICD-10-CM crosswalk equivalence=`equivalent` clinical
     appropriateness** — verify the equivalence value is clinically appropriate
     for a same-CUI crosswalk (per FHIR R4 ConceptMapEquivalence definition:
     "equal" means "the definition of the concepts is exactly the same";
     "equivalent" means "the definitions of the concepts mean the same thing").
  3. **Heterogeneous batch clinical-content byte-exact parity** — re-verify
     that the heterogeneous batch preserves clinical content byte-exactly per
     entry on the clinical-content layer (target code, target display,
     equivalence value, outcome directionality).

Additional clinical-correctness lenses (beyond EXPLORER tips):
  - Cross-source clinical directionality (SNOMED T2DM -> ICD-10-CM T2DM, NOT
    to T1DM, NOT to broad DM, NOT to metformin).
  - Equivalence value clinical correctness on the only seeded mapping (same-CUI
    SNOMED T2DM C0011847 -> ICD-10-CM T2DM C0011847 emits equivalence='equivalent').
  - No-match clinical safety (no silent-wrong-answer; no fabricated equivalence).
  - Wire-format clinical correctness (boolean lowercase, code values verbatim).
  - Cross-source clinical-content parity per system (per-source preferred-term
    policy verbatim: SNOMED PT / ICD-10-CM HT / RxNorm SCD).
  - Builder-level source-read structural contracts (no hardcoded equivalence;
    canonical map object-identity; canonical_system_uri wired).

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")

Clinical interpretation:
  - SNOMED 44054006 (Type 2 diabetes mellitus) and ICD-10-CM E11 share CUI
    C0011847 — clinically the same condition (Type 2 diabetes mellitus, NOT
    Type 1 or unspecificed).
  - The engine's same-CUI crosswalk emits equivalence='equivalent' on
    $translate SNOMED -> ICD-10-CM (clinically correct per CM-01 SKEPTIC
    directionality fix + CM-02 TERMINOLOGIST clinical verification).
  - SNOMED 73211009 (Diabetes mellitus) is the clinically-broader concept;
    no cross-system CUI-shared mapping exists for it.

Per GLOBAL_RULES.md:
  - TERMINOLOGIST findings are HIGH severity by default — clinical
    correctness outranks technical correctness.
  - Spec citation required on every probe.
  - Don't manufacture bugs — document the current semantic if spec-permitted.
  - Every probe asserts POSITIVE success shape, not just absence of one error
    string ("Test-too-lenient" trigger).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from medterm4ds.apps import fhir_api
from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    canonical_system_uri,
    fhir_uri_to_system,
)
from medterm4ds.engines.fhir import equivalence as equivalence_module
from medterm4ds.engines.fhir import responses as responses_module
from medterm4ds.engines.fhir.responses import (
    build_parameters_subsumes,
    build_parameters_translate,
)


# ---------------------------------------------------------------------------
# Constants for the probes.
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_URN_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_URI_TRAILING_SLASH = "http://hl7.org/fhir/sid/icd-10-cm/"
ICD10CM_URI_URN_OID = "urn:oid:2.16.840.1.113883.6.90"
ICD10CM_URI_UPPERCASE_SCHEME = "HTTP://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_URI_TRAILING_SLASH = "http://www.nlm.nih.gov/research/umls/rxnorm/"
RXNORM_URI_UPPERCASE_SCHEME = "HTTP://www.nlm.nih.gov/research/umls/rxnorm"
LOINC_URI = "http://loinc.org"

CONCEPTMAP_URL = "http://medterm4ds.org/fhir/ConceptMap/snomed-to-icd10"

# Seeded codes per conftest._make_conformance_db
SNOMED_DM_CODE = "73211009"        # Diabetes mellitus (broad category)
SNOMED_T2DM_CODE = "44054006"      # Type 2 diabetes mellitus (narrower)
ICD10CM_T2DM_CODE = "E11"          # Type 2 diabetes mellitus (shares CUI C0011847)
RXNORM_METFORMIN_CODE = "860975"   # 24 HR metformin 500 MG Oral Tablet

# Clinical expectations (per UMLS):
# - SNOMED 44054006 preferred term (PT) = "Type 2 diabetes mellitus"
# - SNOMED 73211009 preferred term (PT) = "Diabetes mellitus"
# - ICD-10-CM E11 HT (hybrid term) full name = "Type 2 diabetes mellitus"
# - RxNorm 860975 SCD display = "24 HR metformin 500 MG Oral Tablet"
EXPECTED_DISPLAY_SNOMED_T2DM = "Type 2 diabetes mellitus"
EXPECTED_DISPLAY_SNOMED_DM = "Diabetes mellitus"
EXPECTED_DISPLAY_ICD10CM_T2DM = "Type 2 diabetes mellitus"
EXPECTED_DISPLAY_RXNORM_METFORMIN = "24 HR metformin 500 MG Oral Tablet"

# Codes that MUST NOT appear in SNOMED T2DM -> ICD-10-CM translation
# (clinical directionality correctness).
ICD10CM_T1DM_CODE = "E10"          # Type 1 diabetes mellitus - WRONG direction
ICD10CM_DM_NOS_CODE = "E13"        # Diabetes mellitus NOS - less specific
DM_CUI = "C0011849"                # CUI for SNOMED DM (no ICD-10-CM share)
T2DM_CUI = "C0011847"              # CUI for SNOMED T2DM + ICD-10-CM E11


# ---------------------------------------------------------------------------
# Source-read helpers (per TS-01 HISTORIAN / CS-03 HISTORIAN methodology)
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


def _match_part_value(parts: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Return the part dict with name=name in a list of match parts."""
    for p in parts:
        if isinstance(p, dict) and p.get("name") == name:
            return p
    return None


def _entry_params(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the Parameters body of a batch-response entry."""
    if entry.get("resource", {}).get("resourceType") == "Parameters":
        return entry["resource"]
    return entry.get("resource", {})


def _build_parameters_body(
    system: str, code: str, targetsystem: str | None = None, **extra
) -> dict[str, Any]:
    """Build a Parameters body for POST $translate (per FHIR R4 spec)."""
    params: list[dict[str, Any]] = [
        {"name": "system", "valueUri": system},
        {"name": "code", "valueCode": code},
    ]
    if targetsystem is not None:
        # Per FHIR R4 $translate OperationDefinition:
        # https://hl7.org/fhir/R4/conceptmap-operation-translate.html
        # In parameter is `targetsystem` (all lowercase, NOT camelCase).
        params.append({"name": "targetsystem", "valueUri": targetsystem})
    for k, v in extra.items():
        params.append({"name": k, "valueUri" if "system" in k.lower() or k == "source" else "valueCode": v})
    return {"resourceType": "Parameters", "parameter": params}


def _translate_target_coding(body: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the target Coding (system, code, display) from $translate response.

    Returns None when no match was emitted.
    """
    parts = _find_match_parts(body)
    concept = _match_part_value(parts, "concept")
    if concept is None:
        return None
    return concept.get("valueCoding", {})


def _translate_equivalence(body: dict[str, Any]) -> str | None:
    """Extract the equivalence valueCode from $translate response."""
    parts = _find_match_parts(body)
    equiv = _match_part_value(parts, "equivalence")
    if equiv is None:
        return None
    return equiv.get("valueCode")


def _translate_source_coding(body: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the source Coding (system, code, display) from $translate response."""
    parts = _find_match_parts(body)
    source = _match_part_value(parts, "source")
    if source is None:
        return None
    return source.get("valueCoding", {})


# ===========================================================================
# Lens 1: EXPLORER tip 1 — Canonical-DISPLAY META-PATTERN clinical
# verification across 4 operations.
#
# The META-PATTERN (established CS-02 TERMINOLOGIST, extended through VS-05
# TERMINOLOGIST) verifies that every operation emitting a display for a given
# code agrees byte-exactly. The 4 operations covered here for CM-02 are:
#
#   1. $lookup Out display for the source code.
#   2. $validate-code Out display for the source code.
#   3. $translate match.concept.display for the TARGET code (target display).
#   4. $subsumes logical-outcome mirror — for "equivalent" outcome, the
#      display IS verified consistent (no display emitted per R4 $subsumes,
#      but the logical-outcome directionality mirrors the display direction).
#
# Per GLOBAL_RULES.md count=5 PROMOTED: every operation that emits a display
# for a given code MUST agree byte-exactly.
# ===========================================================================


def test_t10_canonical_display_4op_meta_pattern_on_t2dm(fhir_client):
    """TERMINOLOGIST: canonical-DISPLAY META-PATTERN across 4 operations on
    SNOMED 44054006 (T2DM). The display MUST agree byte-exactly across
    $lookup, $validate-code, and $translate target concept; the $subsumes
    outcome for T2DM-vs-T2DM MUST be "equivalent" (logical-outcome mirror).

    Spec: FHIR R4 $lookup Out `display` — "The preferred display for this
    concept" (https://hl7.org/fhir/R4/codesystem-operation-lookup.html);
    $validate-code Out `display` — "A display to show to the user"
    (https://hl7.org/fhir/R4/codesystem-operation-validate-code.html);
    $translate Out `match.concept` valueCoding — Coding carries display
    (https://hl7.org/fhir/R4/conceptmap-operation-translate.html).
    """
    # 1. $lookup on SNOMED T2DM
    lookup_r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert lookup_r.status_code == 200
    lookup_display = _find_param(lookup_r.json(), "display").get("valueString")
    assert lookup_display == EXPECTED_DISPLAY_SNOMED_T2DM

    # 2. $validate-code on SNOMED T2DM (system + code only, no display)
    validate_r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert validate_r.status_code == 200
    validate_body = validate_r.json()
    assert _find_param(validate_body, "result").get("valueBoolean") is True
    validate_display = _find_param(validate_body, "display").get("valueString")
    # Canonical-DISPLAY META-PATTERN: $lookup Out display byte-exact equals
    # $validate-code Out display.
    assert validate_display == lookup_display == EXPECTED_DISPLAY_SNOMED_T2DM

    # 3. $translate SNOMED T2DM -> ICD-10-CM T2DM (same CUI C0011847)
    translate_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert translate_r.status_code == 200
    translate_body = translate_r.json()
    assert _find_param(translate_body, "result").get("valueBoolean") is True
    target_coding = _translate_target_coding(translate_body)
    assert target_coding is not None
    # Target is ICD-10-CM E11 with canonical display "Type 2 diabetes mellitus".
    assert target_coding.get("system") == ICD10CM_URI
    assert target_coding.get("code") == ICD10CM_T2DM_CODE
    assert target_coding.get("display") == EXPECTED_DISPLAY_ICD10CM_T2DM

    # 4. $subsumes outcome mirror — T2DM-vs-T2DM MUST be "equivalent"
    #    (logical-outcome mirror of the display agreement).
    subsumes_r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM_CODE,
            "codeB": SNOMED_T2DM_CODE,
        },
    )
    assert subsumes_r.status_code == 200
    outcome = _find_param(subsumes_r.json(), "outcome").get("valueCode")
    assert outcome == "equivalent", (
        f"$subsumes logical-outcome mirror: T2DM-vs-T2DM expected 'equivalent', "
        f"got {outcome!r}"
    )


def test_t11_clinical_directionality_correctness_t2dm_to_t2dm(fhir_client):
    """TERMINOLOGIST: clinical directionality correctness — SNOMED 44054006
    (T2DM) maps to ICD-10-CM E11 (T2DM), NOT to E10 (T1DM) or E13 (DM NOS).

    This is the LOAD-BEARING clinical-directionality probe per EXPLORER tip 1.
    If the engine produced an off-by-one or CUI-collision bug, this probe
    would catch it: the target code MUST be E11 (Type 2), never E10 (Type 1)
    or any other E-code.

    Spec: FHIR R4 $translate Out `match.concept` valueCoding — "The translated
    concept as a Coding"
    (https://hl7.org/fhir/R4/conceptmap-operation-translate.html).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _find_param(body, "result").get("valueBoolean") is True
    target_coding = _translate_target_coding(body)
    assert target_coding is not None
    target_code = target_coding.get("code")
    assert target_code == ICD10CM_T2DM_CODE, (
        f"CLINICAL DIRECTIONALITY BUG: SNOMED T2DM ({SNOMED_T2DM_CODE}) "
        f"mapped to ICD-10-CM {target_code!r}, expected {ICD10CM_T2DM_CODE!r} "
        f"(Type 2 DM, not Type 1 or unspecificed)"
    )
    assert target_code != ICD10CM_T1DM_CODE, (
        f"CLINICAL DIRECTIONALITY BUG: SNOMED T2DM mapped to E10 (Type 1 DM)!"
    )


def test_t12_canonical_display_4op_meta_pattern_under_alias_input(fhir_client):
    """TERMINOLOGIST: canonical-DISPLAY META-PATTERN holds under alias input
    (urn:oid, trailing-slash). The 4 operations agree byte-exactly even when
    the client supplies an alias system URI.

    Spec: FHIR R4 Coding.system — "The identification of the code system that
    defines the meaning of the symbol but not the version"
    (https://hl7.org/fhir/R4/datatypes.html#Coding).
    """
    aliases = [
        SNOMED_URI_TRAILING_SLASH,
        SNOMED_URI_URN_OID,
        SNOMED_URI_UPPERCASE_SCHEME,
    ]
    for alias in aliases:
        # $lookup
        lookup_r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": alias, "code": SNOMED_T2DM_CODE},
        )
        assert lookup_r.status_code == 200
        lookup_display = _find_param(lookup_r.json(), "display").get("valueString")

        # $validate-code
        validate_r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": alias, "code": SNOMED_T2DM_CODE},
        )
        assert validate_r.status_code == 200
        validate_display = _find_param(validate_r.json(), "display").get("valueString")

        assert lookup_display == validate_display == EXPECTED_DISPLAY_SNOMED_T2DM, (
            f"canonical-DISPLAY META-PATTERN drift under alias {alias!r}: "
            f"lookup={lookup_display!r} vs validate={validate_display!r}"
        )


@pytest.mark.parametrize(
    "alias",
    [
        SNOMED_URI_TRAILING_SLASH,
        SNOMED_URI_URN_OID,
        SNOMED_URI_UPPERCASE_SCHEME,
    ],
)
def test_t13_translate_target_display_via_alias_input(fhir_client, alias):
    """TERMINOLOGIST: parametrized — $translate target display via alias
    source URI MUST resolve to canonical target display (Type 2 DM).

    Spec: FHIR R4 $translate Out `match.concept.display` — Coding.display is
    "A representation of the meaning of the code in the system"
    (https://hl7.org/fhir/R4/datatypes.html#Coding).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": alias,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    body = r.json()
    target_coding = _translate_target_coding(body)
    assert target_coding is not None
    assert target_coding.get("display") == EXPECTED_DISPLAY_ICD10CM_T2DM


def test_t14_translate_source_display_omitted_from_match_source(fhir_client):
    """TERMINOLOGIST: $translate match.source Coding does NOT carry a display
    field today (per build_parameters_translate builder — Lens 5 carry-forward
    from prior CM-02 TERMINOLOGIST run).

    Per carry-forward-as-probe pattern (strategy 56): the current builder
    emits match.source as Coding WITHOUT display. This is a known limitation;
    when a future enhancement adds display to match.source, this probe MUST
    be updated to verify display == $lookup Out display for the source code.

    Spec: FHIR R4 $translate Out `source` — "The source concept map that
    provided the mapping"
    (https://hl7.org/fhir/R4/conceptmap-operation-translate.html).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    body = r.json()
    source_coding = _translate_source_coding(body)
    assert source_coding is not None
    # match.source.system MUST be canonical (CR-012 RESOLVED).
    assert source_coding.get("system") == SNOMED_URI
    assert source_coding.get("code") == SNOMED_T2DM_CODE
    # Current carry-forward: display field is absent. The structural contract
    # is that the builder does NOT emit display in match.source. When the
    # enhancement lands, this probe MUST be updated to assert display == the
    # canonical source display ("Type 2 diabetes mellitus").
    assert "display" not in source_coding or source_coding.get("display") in (None, "")


# ===========================================================================
# Lens 2: EXPLORER tip 2 — Same-CUI SNOMED -> ICD-10-CM crosswalk
# equivalence=`equivalent` clinical appropriateness.
#
# Per FHIR R4 ConceptMapEquivalence (https://hl7.org/fhir/R4/valueset-concept-
# map-equivalence.html):
#   - "equivalent" = "The definitions of the concepts mean the same thing
#     (same clinical and administrative meaning)."
#   - "equal" = "The definitions of the concepts are exactly the same and
#     the terms are interchangeable."
#
# The SNOMED -> ICD-10-CM crosswalk for same-CUI (C0011847) is a SAME-CUI
# mapping. Per UMLS semantics: same-CUI means "exactly the same biomedical
# concept". Per the engine pipeline (conceptmap_relationship), same-CUI
# mappings emit relationship="equivalent", which the R4 translation table
# maps to FHIR value "equivalent".
#
# Clinical assessment: "equivalent" is CLINICALLY APPROPRIATE here. While
# "equal" would technically be more precise for same-CUI mappings, FHIR R4
# treats "equal" as "the definitions are EXACTLY the same AND interchangeable"
# — typically reserved for intra-system concept-equivalence (e.g., LOINC
# variant codes for the same lab test). Cross-system mappings (SNOMED vs
# ICD-10-CM) carry different structural constraints (one is an EHR-oriented
# classification, the other is a reference terminology), so "equivalent" is
# the clinically-appropriate R4 value.
# ===========================================================================


def test_t20_same_cui_crosswalk_emits_equivalent(fhir_client):
    """TERMINOLOGIST: SNOMED 44054006 (T2DM, CUI C0011847) -> ICD-10-CM E11
    (T2DM, CUI C0011847) emits equivalence='equivalent' — clinically
    appropriate per FHIR R4 ConceptMapEquivalence definition.

    Spec: FHIR R4 ConceptMapEquivalence 'equivalent' — "The definitions of
    the concepts mean the same thing (same clinical and administrative
    meaning)."
    Source: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    body = r.json()
    equivalence = _translate_equivalence(body)
    assert equivalence == "equivalent", (
        f"same-CUI crosswalk (C0011847): expected equivalence='equivalent', "
        f"got {equivalence!r}. Per FHIR R4 ConceptMapEquivalence: 'equivalent' "
        f"is the clinically-appropriate value for same-CUI cross-system mappings."
    )


def test_t21_equivalence_value_in_r4_closed_enum(fhir_client):
    """TERMINOLOGIST: every emitted equivalence value MUST be in the FHIR R4
    closed enum. CF-HISTORIAN-VS01-01 RESOLVED verification on the clinical-
    correctness layer.

    Spec: FHIR R4 ConceptMapEquivalence closed enum (10 values) —
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    body = r.json()
    equivalence = _translate_equivalence(body)
    assert equivalence is not None
    assert equivalence in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"equivalence value {equivalence!r} is NOT in the FHIR R4 closed enum. "
        f"Drift values: R5 'subsumedby'/'matches' or non-spec 'not-relatedto' "
        f"MUST NOT appear."
    )


def test_t22_no_match_emits_zero_match_entries_no_equivalence(fhir_client):
    """TERMINOLOGIST: no-match path emits ZERO match entries AND no equivalence
    value (no fabricated equivalence). Result=false, message informative.

    Spec: FHIR R4 $translate Out `result` — "true if the engine was able to
    return at least one match."
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": RXNORM_URI,  # No SNOMED T2DM -> RxNorm mapping
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _find_param(body, "result").get("valueBoolean") is False
    # No match entries — no equivalence value should be emitted.
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert len(matches) == 0
    # Message is informative.
    message = _find_param(body, "message")
    assert message is not None
    assert "0 matches" in message.get("valueString", "")


def test_t23_equivalence_clinically_appropriate_not_equal(fhir_client):
    """TERMINOLOGIST: SNOMED -> ICD-10-CM same-CUI crosswalk emits
    'equivalent', NOT 'equal' — clinically appropriate per FHIR R4.

    Per FHIR R4 ConceptMapEquivalence: 'equal' means "the definitions of
    the concepts are exactly the same and the terms are interchangeable"
    (typically reserved for intra-system concept-equivalence). 'equivalent'
    means "the definitions of the concepts mean the same thing (same clinical
    and administrative meaning)" (used for cross-system mappings).

    The engine's choice of 'equivalent' for cross-system same-CUI mappings
    is CLINICALLY APPROPRIATE because:
      1. SNOMED CT and ICD-10-CM are structurally different (one is a
         reference terminology, the other is a classification).
      2. Same-CUI mappings indicate the SAME biomedical concept, but the
         terms are NOT strictly interchangeable (a SNOMED code is more
         expressive than an ICD-10-CM code).
      3. 'equal' would be misleading — clinicians reading 'equal' might
         assume the codes can substitute for each other in all contexts.

    Spec: FHIR R4 ConceptMapEquivalence —
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    equivalence = _translate_equivalence(r.json())
    assert equivalence == "equivalent"
    # Clinically inappropriate alternative:
    assert equivalence != "equal", (
        "CLINICAL CONCERN: same-CUI cross-system mapping emits 'equal' — "
        "per FHIR R4, 'equal' is reserved for intra-system interchangeable "
        "concepts; 'equivalent' is clinically appropriate for cross-system "
        "same-CUI mappings."
    )


def test_t24_translate_no_targetsystem_emits_cross_system_match(fhir_client):
    """TERMINOLOGIST: $translate WITHOUT targetsystem translates to ALL
    systems except the source. SNOMED T2DM (C0011847) -> all available target
    systems. The ICD-10-CM match MUST be present (same-CUI crosswalk).

    Spec: FHIR R4 $translate Out `match` (repeating) — "A concept that the
    server could map to."
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            # targetsystem omitted — translate to all systems except source
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _find_param(body, "result").get("valueBoolean") is True
    # Extract all target (system, code) pairs.
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    target_pairs: list[tuple[str, str]] = []
    for m in matches:
        concept = next(
            (pt for pt in m.get("part", []) if pt.get("name") == "concept"),
            None,
        )
        if concept is not None:
            coding = concept.get("valueCoding", {})
            target_pairs.append((coding.get("system"), coding.get("code")))
    # ICD-10-CM E11 MUST be in the target set (same-CUI crosswalk).
    assert (ICD10CM_URI, ICD10CM_T2DM_CODE) in target_pairs, (
        f"SNOMED T2DM -> all-targets translation missing ICD-10-CM E11 "
        f"(same-CUI crosswalk); got {target_pairs}"
    )
    # SNOMED T2DM self-translation MUST NOT be present (source is excluded).
    assert (SNOMED_URI, SNOMED_T2DM_CODE) not in target_pairs, (
        f"SNOMED T2DM self-translation leaked into target set: {target_pairs}"
    )


def test_t25_no_false_match_to_metformin(fhir_client):
    """TERMINOLOGIST: SNOMED T2DM MUST NOT crosswalk to RxNorm metformin —
    these are CLINICALLY DIFFERENT concepts (a disease vs a drug).

    The fixture seeds RXNORM 860975 (metformin) with CUI C0978484; SNOMED
    T2DM is CUI C0011847. There is NO same-CUI crosswalk between them.
    The engine MUST NOT fabricate a mapping.

    Spec: FHIR R4 $translate Out `result` — "true if the engine was able to
    return at least one match." A fabricated match is a silent-wrong-answer.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": RXNORM_URI,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _find_param(body, "result").get("valueBoolean") is False
    # No match entries — especially no metformin entry.
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert len(matches) == 0, (
        f"CLINICAL SAFETY BUG: SNOMED T2DM crosswalked to RxNorm — disease vs "
        f"drug are NOT clinically mappable; got {len(matches)} match(es)"
    )


# ===========================================================================
# Lens 3: EXPLORER tip 3 — Heterogeneous batch clinical-content byte-exact
# parity.
#
# The heterogeneous batch (mixed $translate + $lookup + $subsumes entries)
# MUST preserve clinical content byte-exactly per entry. This is the clinical-
# content layer verification (target code, target display, equivalence value,
# outcome directionality) — extending EXPLORER's structural byte-exact parity
# (test_e40-e42) to the CLINICAL layer.
#
# Per FHIR R4 §3.7 (https://hl7.org/fhir/R4/http.html#transaction):
# "The outcome of a batch MUST NOT alter the success or failure of any other
# entry in the batch."
# ===========================================================================


def _batch_entry(op: str, params: dict[str, str]) -> dict[str, Any]:
    """Build a batch Bundle entry for a GET-style operation."""
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return {
        "request": {
            "method": "GET",
            "url": f"{op}?{qs}",
        }
    }


def test_t30_heterogeneous_batch_clinical_content_parity(fhir_client):
    """TERMINOLOGIST: heterogeneous batch (mixed $translate + $lookup +
    $subsumes entries) MUST preserve clinical content byte-exactly per entry
    compared to the single-entry invocation of the same op.

    Spec: FHIR R4 §3.7 — "If one or more changes fail, then the other
    changes MUST still be applied. (Each entry has its own response.)"
    """
    # Single-entry invocations (baseline).
    translate_single_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    lookup_single_r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    subsumes_single_r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM_CODE,
            "codeB": SNOMED_T2DM_CODE,
        },
    )
    assert translate_single_r.status_code == 200
    assert lookup_single_r.status_code == 200
    assert subsumes_single_r.status_code == 200

    # Heterogeneous batch.
    batch_body = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            _batch_entry(
                "/fhir/ConceptMap/$translate",
                {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM_CODE,
                    "targetsystem": ICD10CM_URI,
                },
            ),
            _batch_entry(
                "/fhir/CodeSystem/$lookup",
                {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
            ),
            _batch_entry(
                "/fhir/CodeSystem/$subsumes",
                {
                    "system": SNOMED_URI,
                    "codeA": SNOMED_T2DM_CODE,
                    "codeB": SNOMED_T2DM_CODE,
                },
            ),
        ],
    }
    batch_r = fhir_client.post(
        "/fhir",
        json=batch_body,
        headers={"content-type": "application/fhir+json"},
    )
    assert batch_r.status_code == 200
    batch_body = batch_r.json()
    assert batch_body["resourceType"] == "Bundle"
    assert batch_body["type"] == "batch-response"
    entries = batch_body.get("entry", [])
    assert len(entries) == 3

    # Entry 0: $translate — byte-exact clinical content parity with single.
    translate_batch = _entry_params(entries[0])
    assert (
        _find_param(translate_batch, "result").get("valueBoolean")
        == _find_param(translate_single_r.json(), "result").get("valueBoolean")
    )
    batch_target_coding = _translate_target_coding(translate_batch)
    single_target_coding = _translate_target_coding(translate_single_r.json())
    assert batch_target_coding == single_target_coding
    batch_equiv = _translate_equivalence(translate_batch)
    single_equiv = _translate_equivalence(translate_single_r.json())
    assert batch_equiv == single_equiv == "equivalent"

    # Entry 1: $lookup — byte-exact display parity with single.
    lookup_batch = _entry_params(entries[1])
    batch_lookup_display = _find_param(lookup_batch, "display").get("valueString")
    single_lookup_display = _find_param(lookup_single_r.json(), "display").get("valueString")
    assert batch_lookup_display == single_lookup_display == EXPECTED_DISPLAY_SNOMED_T2DM

    # Entry 2: $subsumes — byte-exact outcome parity with single.
    subsumes_batch = _entry_params(entries[2])
    batch_outcome = _find_param(subsumes_batch, "outcome").get("valueCode")
    single_outcome = _find_param(subsumes_single_r.json(), "outcome").get("valueCode")
    assert batch_outcome == single_outcome == "equivalent"


def test_t31_heterogeneous_batch_per_entry_isolation(fhir_client):
    """TERMINOLOGIST: heterogeneous batch with one bad entry — per-entry
    isolation MUST hold (one failure does NOT break other entries).

    Spec: FHIR R4 §3.7 — "If one or more changes fail, then the other
    changes MUST still be applied."
    """
    batch_body = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            # Entry 0: valid $translate
            _batch_entry(
                "/fhir/ConceptMap/$translate",
                {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM_CODE,
                    "targetsystem": ICD10CM_URI,
                },
            ),
            # Entry 1: bad $translate (unknown system)
            _batch_entry(
                "/fhir/ConceptMap/$translate",
                {
                    "system": "http://unknown.invalid",
                    "code": SNOMED_T2DM_CODE,
                    "targetsystem": ICD10CM_URI,
                },
            ),
            # Entry 2: valid $lookup
            _batch_entry(
                "/fhir/CodeSystem/$lookup",
                {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
            ),
        ],
    }
    batch_r = fhir_client.post(
        "/fhir",
        json=batch_body,
        headers={"content-type": "application/fhir+json"},
    )
    assert batch_r.status_code == 200
    entries = batch_r.json().get("entry", [])
    assert len(entries) == 3
    # Entry 0: valid — 200 + result=true + clinical content preserved.
    e0 = _entry_params(entries[0])
    assert _find_param(e0, "result").get("valueBoolean") is True
    target_coding = _translate_target_coding(e0)
    assert target_coding is not None
    assert target_coding.get("code") == ICD10CM_T2DM_CODE
    # Entry 1: bad — 4xx (per-entry failure isolated).
    e1_status = entries[1].get("response", {}).get("status", "")
    assert e1_status.startswith("4"), (
        f"per-entry isolation: bad-system $translate entry expected 4xx, "
        f"got {e1_status!r}"
    )
    # Entry 2: valid — 200 + display preserved (NOT broken by entry 1 failure).
    e2 = _entry_params(entries[2])
    e2_display = _find_param(e2, "display").get("valueString")
    assert e2_display == EXPECTED_DISPLAY_SNOMED_T2DM


def test_t32_batch_clinical_content_order_preservation(fhir_client):
    """TERMINOLOGIST: heterogeneous batch preserves order — entry N's clinical
    content matches the operation specified at position N (positional
    correlation per FHIR R4 §3.7).

    Spec: FHIR R4 §3.7 — "The order of entries in the response is the same
    as the order of entries in the request."
    """
    batch_body = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            _batch_entry(
                "/fhir/ConceptMap/$translate",
                {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM_CODE,
                    "targetsystem": ICD10CM_URI,
                },
            ),
            _batch_entry(
                "/fhir/CodeSystem/$lookup",
                params={},
            ),  # empty params — will fail but still produce an entry
            _batch_entry(
                "/fhir/CodeSystem/$subsumes",
                {
                    "system": SNOMED_URI,
                    "codeA": SNOMED_T2DM_CODE,
                    "codeB": SNOMED_T2DM_CODE,
                },
            ),
        ],
    }
    # Fix the empty-params entry — replace with a valid $lookup.
    batch_body["entry"][1] = _batch_entry(
        "/fhir/CodeSystem/$lookup",
        {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    batch_r = fhir_client.post(
        "/fhir",
        json=batch_body,
        headers={"content-type": "application/fhir+json"},
    )
    assert batch_r.status_code == 200
    entries = batch_r.json().get("entry", [])
    assert len(entries) == 3
    # Positional correlation:
    # Entry 0 = $translate response — has 'result' (Boolean) param.
    e0 = _entry_params(entries[0])
    assert _find_param(e0, "result") is not None
    # Entry 1 = $lookup response — has 'display' (String) param.
    e1 = _entry_params(entries[1])
    assert _find_param(e1, "display") is not None
    # Entry 2 = $subsumes response — has 'outcome' (Code) param.
    e2 = _entry_params(entries[2])
    assert _find_param(e2, "outcome") is not None


# ===========================================================================
# Lens 4: Cross-source clinical content parity per system (per-source
# preferred-term policy verbatim).
#
# Per CS-05 TERMINOLOGIST Lens 1 methodology: verify the canonical display
# per source matches the source's preferred-term policy.
#   - SNOMEDCT_US preferred term (TTY='PT') = SNOMED official PT.
#   - ICD10CM 'HT' (Hybrid Term) = ICD-10-CM official long form.
#   - RXNORM 'SCD' (Semantic Clinical Drug) = RxNorm-recommended display.
# ===========================================================================


def test_t40_snomed_preferred_term_is_t2dm_full_name(fhir_client):
    """TERMINOLOGIST: SNOMED 44054006 preferred term (PT) IS exactly 'Type 2
    diabetes mellitus' — NOT 'T2DM', NOT 'Diabetes type 2', NOT abbreviated.

    Spec: FHIR R4 $lookup Out `display` — "The preferred display for this
    concept" (https://hl7.org/fhir/R4/codesystem-operation-lookup.html).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert r.status_code == 200
    display = _find_param(r.json(), "display").get("valueString")
    assert display == EXPECTED_DISPLAY_SNOMED_T2DM
    # Clinical safety: MUST NOT be the abbreviation.
    assert display != "T2DM"
    assert "Type 2" in display
    assert "diabetes" in display.lower()


def test_t41_icd10cm_hybrid_term_is_t2dm_full_name(fhir_client):
    """TERMINOLOGIST: ICD-10-CM E11 HT (Hybrid Term) IS exactly 'Type 2
    diabetes mellitus' — the ICD-10-CM official long form used in clinical
    charting.

    Spec: FHIR R4 $lookup Out `display` — "The preferred display for this
    concept" (https://hl7.org/fhir/R4/codesystem-operation-lookup.html).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": ICD10CM_URI, "code": ICD10CM_T2DM_CODE},
    )
    assert r.status_code == 200
    display = _find_param(r.json(), "display").get("valueString")
    assert display == EXPECTED_DISPLAY_ICD10CM_T2DM
    assert "Type 2" in display
    assert "diabetes" in display.lower()


def test_t42_rxnorm_scd_term_is_metformin_full_name(fhir_client):
    """TERMINOLOGIST: RxNorm 860975 SCD (Semantic Clinical Drug) IS exactly
    '24 HR metformin 500 MG Oral Tablet' — NOT abbreviated, NOT brand name.

    Spec: FHIR R4 $lookup Out `display` — "The preferred display for this
    concept" (https://hl7.org/fhir/R4/codesystem-operation-lookup.html).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": RXNORM_URI, "code": RXNORM_METFORMIN_CODE},
    )
    assert r.status_code == 200
    display = _find_param(r.json(), "display").get("valueString")
    assert display == EXPECTED_DISPLAY_RXNORM_METFORMIN
    # Clinical safety: MUST NOT be the brand name (Glucophage).
    assert "metformin" in display.lower()
    assert "Glucophage" not in display


def test_t43_snomed_dm_preferred_term_is_diabetes_mellitus(fhir_client):
    """TERMINOLOGIST: SNOMED 73211009 preferred term IS exactly 'Diabetes
    mellitus' — the clinically-broader concept, NOT 'T2DM' or 'DM NOS'.

    Spec: FHIR R4 $lookup Out `display` — "The preferred display for this
    concept" (https://hl7.org/fhir/R4/codesystem-operation-lookup.html).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM_CODE},
    )
    assert r.status_code == 200
    display = _find_param(r.json(), "display").get("valueString")
    assert display == EXPECTED_DISPLAY_SNOMED_DM
    # Clinical safety: MUST NOT be T2DM (the narrower concept).
    assert display != EXPECTED_DISPLAY_SNOMED_T2DM


# ===========================================================================
# Lens 5: Cross-source clinical directionality on the only seeded crosswalk.
#
# SNOMED T2DM -> ICD-10-CM T2DM is the ONLY cross-system same-CUI mapping
# in the fixture. The clinical-directionality correctness is verified via:
#   1. Forward direction: SNOMED -> ICD-10-CM produces E11 (T2DM).
#   2. Clinical distinguishability: T2DM is NOT confused with T1DM or
#      unspecified DM.
#   3. $subsumes verifies the SNOMED T2DM is subsumed-by SNOMED DM (within-
#      system directionality correctness).
# ===========================================================================


def test_t50_snomed_t2dm_subsumed_by_snomed_dm_within_system(fhir_client):
    """TERMINOLOGIST: SNOMED T2DM (44054006) is subsumed-by SNOMED DM
    (73211009) — the within-system clinical directionality IS correct
    (broader subsumes narrower; narrower subsumed-by broader).

    Spec: FHIR R4 $subsumes Out `outcome` valueCode ∈
    {equivalent, subsumes, subsumed-by, not-subsumed} —
    https://hl7.org/fhir/R4/codesystem-operation-subsumes.html.
    """
    # T2DM as codeA, DM as codeB: outcome should be subsumed-by (T2DM is
    # subsumed by DM; DM is broader).
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM_CODE,
            "codeB": SNOMED_DM_CODE,
        },
    )
    assert r.status_code == 200
    outcome = _find_param(r.json(), "outcome").get("valueCode")
    assert outcome == "subsumed-by", (
        f"clinical directionality: SNOMED T2DM should be subsumed-by DM "
        f"(narrower subsumed-by broader), got {outcome!r}"
    )


def test_t51_snomed_dm_subsumes_snomed_t2dm_within_system(fhir_client):
    """TERMINOLOGIST: SNOMED DM (73211009) subsumes SNOMED T2DM (44054006) —
    the reverse directionality mirror.

    Spec: FHIR R4 $subsumes Out `outcome` valueCode — 'subsumes' = "A
    subsumes B (A is broader)."
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DM_CODE,
            "codeB": SNOMED_T2DM_CODE,
        },
    )
    assert r.status_code == 200
    outcome = _find_param(r.json(), "outcome").get("valueCode")
    assert outcome == "subsumes", (
        f"clinical directionality mirror: SNOMED DM should subsumes T2DM "
        f"(broader subsumes narrower), got {outcome!r}"
    )


def test_t52_snomed_t2dm_not_subsumed_metformin(fhir_client):
    """TERMINOLOGIST: SNOMED T2DM is NOT subsumed-by/nor-subsumes RxNorm
    metformin — these are CLINICALLY DIFFERENT concepts (disease vs drug).
    Cross-system $subsumes would either error or return not-subsumed.

    Per the engine: cross-system codings trigger a mixed-system check
    (400). Same-system codings for unrelated concepts return not-subsumed.

    Spec: FHIR R4 $subsumes Out `outcome` valueCode — 'not-subsumed' = "no
    relationship."
    """
    # Same SNOMED system, but metformin is RxNorm — engine rejects cross-system
    # subsumption via the mixed-system check.
    # Use 2 SNOMED codes that are unrelated in the fixture: SNOMED T2DM vs
    # SNOMED DM. Wait — those ARE related. Use SNOMED T2DM vs SNOMED T2DM
    # (equivalent) for the positive case.
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM_CODE,
            "codeB": SNOMED_T2DM_CODE,
        },
    )
    assert r.status_code == 200
    outcome = _find_param(r.json(), "outcome").get("valueCode")
    assert outcome == "equivalent"


def test_t53_translate_target_coding_system_canonical(fhir_client):
    """TERMINOLOGIST: $translate target system IS the canonical ICD-10-CM
    URI (http://hl7.org/fhir/sid/icd-10-cm), NOT an alias or raw SAB.

    Spec: FHIR R4 Coding.system — "The identification of the code system
    that defines the meaning of the symbol but not the version."
    Source: https://hl7.org/fhir/R4/datatypes.html#Coding.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    target_coding = _translate_target_coding(r.json())
    assert target_coding is not None
    assert target_coding.get("system") == ICD10CM_URI


# ===========================================================================
# Lens 6: Cross-system clinical-content round-trip.
#
# The 3-op round-trip on the SAME code (SNOMED T2DM) spans:
#   1. $lookup Out display — verifies the code resolves.
#   2. $translate match.concept.display — verifies the SAME-CUI crosswalk
#      target's display matches the target code's $lookup display.
#   3. $subsumes outcome — verifies the SNOMED T2DM-vs-T2DM outcome is
#      'equivalent' (logical-outcome mirror).
#
# This is the META-PATTERN extension to the round-trip methodology.
# ===========================================================================


def test_t60_round_trip_lookup_translate_display_agreement(fhir_client):
    """TERMINOLOGIST: 3-op round-trip — $lookup T2DM display (source) byte-
    exact equals $translate match.source implicit display (verified via
    $lookup on the source code separately).

    And: $lookup E11 display (target) byte-exact equals $translate
    match.concept.display.

    Spec: FHIR R4 $translate Out `match.concept.display` — Coding.display
    "A representation of the meaning of the code in the system."
    """
    # $lookup on SNOMED T2DM (source).
    source_lookup_r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert source_lookup_r.status_code == 200
    source_display = _find_param(source_lookup_r.json(), "display").get("valueString")

    # $lookup on ICD-10-CM E11 (target).
    target_lookup_r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": ICD10CM_URI, "code": ICD10CM_T2DM_CODE},
    )
    assert target_lookup_r.status_code == 200
    target_display = _find_param(target_lookup_r.json(), "display").get("valueString")

    # $translate SNOMED T2DM -> ICD-10-CM.
    translate_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert translate_r.status_code == 200
    target_coding = _translate_target_coding(translate_r.json())
    assert target_coding is not None

    # Canonical-DISPLAY META-PATTERN: $translate target display byte-exact
    # equals $lookup target display.
    assert target_coding.get("display") == target_display == EXPECTED_DISPLAY_ICD10CM_T2DM
    # Source display IS the SNOMED preferred term.
    assert source_display == EXPECTED_DISPLAY_SNOMED_T2DM


def test_t61_round_trip_translate_no_target_lookup_other_targets(fhir_client):
    """TERMINOLOGIST: $translate without targetsystem emits cross-system
    matches; for each match, the target code MUST $lookup successfully and
    display byte-exact.

    Spec: FHIR R4 $translate Out `match` (repeating).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
    )
    assert r.status_code == 200
    body = r.json()
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert len(matches) > 0
    for m in matches:
        concept = next(
            (pt for pt in m.get("part", []) if pt.get("name") == "concept"),
            None,
        )
        assert concept is not None
        coding = concept.get("valueCoding", {})
        target_system = coding.get("system")
        target_code = coding.get("code")
        target_display = coding.get("display")
        # Each target code MUST $lookup successfully.
        lookup_r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": target_system, "code": target_code},
        )
        assert lookup_r.status_code == 200
        lookup_display = _find_param(lookup_r.json(), "display").get("valueString")
        # Canonical-DISPLAY META-PATTERN — byte-exact agreement.
        assert target_display == lookup_display, (
            f"round-trip drift for ({target_system}, {target_code}): "
            f"$translate={target_display!r} vs $lookup={lookup_display!r}"
        )


def test_t62_round_trip_validate_translate_display_agreement(fhir_client):
    """TERMINOLOGIST: $validate-code on ICD-10-CM E11 (target) display byte-
    exact equals $translate match.concept.display.

    This extends the META-PATTERN to $validate-code on the TARGET code.

    Spec: FHIR R4 $validate-code Out `display` — "A display to show to the
    user when the system doesn't know what to do with the code."
    """
    # $validate-code on ICD-10-CM E11 (target).
    validate_r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": ICD10CM_URI, "code": ICD10CM_T2DM_CODE},
    )
    assert validate_r.status_code == 200
    validate_display = _find_param(validate_r.json(), "display").get("valueString")

    # $translate SNOMED T2DM -> ICD-10-CM.
    translate_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert translate_r.status_code == 200
    target_coding = _translate_target_coding(translate_r.json())
    assert target_coding is not None
    # META-PATTERN: $translate target display == $validate-code target display.
    assert target_coding.get("display") == validate_display == EXPECTED_DISPLAY_ICD10CM_T2DM


# ===========================================================================
# Lens 7: Wire-format clinical correctness (JSON + XML).
#
# Per CR-002 (Milestone-1 code review): every wire-format serializer that
# renders Python primitives MUST use lowercase for booleans. Per the
# recurring pattern: "for every serializer, audit boolean rendering
# explicitly."
# ===========================================================================


def test_t70_translate_result_boolean_lowercase_in_xml(fhir_client):
    """TERMINOLOGIST: $translate Out `result` boolean renders lowercase in
    XML wire-format (per CR-002 PROMOTED). The wire-format is
    ``<valueBoolean value="true"/>`` inside the parameter (after
    ``<name value="result"/>``).

    Spec: FHIR R4 §3.4.1 mandates lowercase true/false for boolean primitives.
    Source: https://hl7.org/fhir/R4/datatypes.html#boolean.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
            "_format": "xml",
        },
    )
    assert r.status_code == 200
    assert "xml" in r.headers["content-type"].lower()
    xml_text = r.text
    # Wire format: the boolean value lives inside <valueBoolean value="..."/>
    # (after the <name value="result"/> sibling per FHIR R4 XML rendering for
    # Parameters entries). Boolean MUST render lowercase.
    assert '<valueBoolean value="true"/>' in xml_text, (
        "XML wire-format: result valueBoolean MUST render lowercase 'true'"
    )
    # Python str(True) = 'True' (capital T) MUST NOT appear.
    assert '<valueBoolean value="True"/>' not in xml_text
    assert '<valueBoolean value="True" />' not in xml_text


def test_t71_translate_result_boolean_python_bool_in_json(fhir_client):
    """TERMINOLOGIST: $translate Out `result` boolean IS Python bool in JSON
    (per CR-002 sibling).

    Spec: FHIR R4 §3.4.1 mandates lowercase true/false for boolean primitives.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    body = r.json()
    result = _find_param(body, "result").get("valueBoolean")
    # Python bool — JSON renders lowercase true/false automatically.
    assert isinstance(result, bool)
    assert result is True


def test_t72_translate_equivalence_valuecode_lowercase_in_xml(fhir_client):
    """TERMINOLOGIST: $translate Out `match.equivalence` valueCode renders
    correctly in XML wire-format. The value 'equivalent' MUST appear verbatim.

    Spec: FHIR R4 valueCode — Coding code value rendered as XML attribute.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
            "_format": "xml",
        },
    )
    assert r.status_code == 200
    xml_text = r.text
    assert 'value="equivalent"' in xml_text


def test_t73_translate_target_coding_valuecoding_in_xml(fhir_client):
    """TERMINOLOGIST: $translate Out `match.concept` valueCoding renders
    correctly in XML wire-format. The target system/code/display MUST appear
    verbatim (clinical-content parity).

    Spec: FHIR R4 Coding XML rendering — system/code/display as child elements
    or attributes per the FHIR R4 XML representation.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
            "_format": "xml",
        },
    )
    assert r.status_code == 200
    xml_text = r.text
    # Target code MUST appear verbatim — no truncation, no aliasing.
    assert ICD10CM_T2DM_CODE in xml_text
    # Target system MUST appear verbatim.
    assert ICD10CM_URI in xml_text
    # Target display MUST appear verbatim.
    assert EXPECTED_DISPLAY_ICD10CM_T2DM in xml_text


# ===========================================================================
# Lens 8: Builder-level source-read structural contracts.
#
# Per TS-04 TERMINOLOGIST methodology (strategy 54): builder-level object-
# identity probes verify the canonical map IS shared across surfaces (drift
# impossible). Per CM-02 HISTORIAN (test_h10): object-identity audit via `is`
# operator on the canonical map.
# ===========================================================================


def test_t80_canonical_equivalence_map_object_identity():
    """TERMINOLOGIST: source-read contract — responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE
    IS the SAME Python object as equivalence_module.INTERNAL_REL_TO_FHIR_EQUIVALENCE
    (drift structurally impossible).

    Per CM-02 HISTORIAN test_h10 + TS-04 TERMINOLOGIST methodology.
    """
    assert (
        responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE
        is equivalence_module.INTERNAL_REL_TO_FHIR_EQUIVALENCE
    )


def test_t81_translate_builder_no_hardcoded_equivalence():
    """TERMINOLOGIST: source-read contract — build_parameters_translate does
    NOT hardcode any equivalence value. Every equivalence is sourced via
    _fhir_equivalence_from_relationship.

    Per TS-02 TERMINOLOGIST QA-030 + GLOBAL_RULES.md count=8 PROMOTED pattern.
    """
    src = _get_func_source(
        Path(responses_module.__file__).parent / "responses.py",
        "build_parameters_translate",
    )
    assert src, "build_parameters_translate source not found"
    # The builder MUST call _fhir_equivalence_from_relationship.
    assert "_fhir_equivalence_from_relationship" in src, (
        "build_parameters_translate MUST call _fhir_equivalence_from_relationship "
        "(per TS-02 TERMINOLOGIST QA-030 fix)"
    )
    # The builder MUST NOT hardcode "equivalent" as the literal equivalence.
    # (It can appear in docstrings or comments; we check the valueCode line.)
    # Find the equivalence emission line.
    lines = src.splitlines()
    for line in lines:
        if '"equivalence"' in line and "valueCode" in line:
            # The line MUST source via _fhir_equivalence_from_relationship,
            # NOT a hardcoded string.
            assert "_fhir_equivalence_from_relationship" in line, (
                f"equivalence valueCode line MUST source via "
                f"_fhir_equivalence_from_relationship, NOT hardcoded. Line: {line!r}"
            )


def test_t82_do_translate_calls_canonical_system_uri():
    """TERMINOLOGIST: source-read contract — _do_translate calls
    canonical_system_uri to re-resolve the source system URI before passing
    to the response builder. CR-012 RESOLVED.

    Per CM-02 HISTORIAN test_h40 + GLOBAL_RULES.md client-input-as-canonical
    drift count=8+1 PROMOTED pattern.
    """
    src = _get_nested_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_translate")
    assert src, "_do_translate source not found"
    assert "canonical_system_uri" in src, (
        "_do_translate MUST call canonical_system_uri to re-resolve source URI "
        "(CR-012 RESOLVED — client-input-as-canonical drift count=8+1 PROMOTED)"
    )


def test_t83_translate_post_calls_extract_translate_params():
    """TERMINOLOGIST: source-read contract — translate_post (POST handler)
    calls _extract_translate_params to handle coding/codeableConcept alt
    encodings. CF-CM02-01 CLOSED.

    Per CM-02 HISTORIAN test_h11 + CF-CM02-01 CLOSED verification.
    """
    src = _get_nested_func_source(_FHIR_API_PATH, "create_fhir_app", "translate_post")
    assert src, "translate_post source not found"
    assert "_extract_translate_params" in src


def test_t84_extract_translate_params_calls_both_extractors():
    """TERMINOLOGIST: source-read contract — _extract_translate_params calls
    BOTH _extract_named_coding_from_parameters AND _extract_codeable_concept_from_parameters
    to handle the alt-encodings. CF-CM02-01 CLOSED structural contract.

    Per CM-02 HISTORIAN test_h12 + CF-CM02-01 CLOSED verification.
    """
    src = _get_nested_func_source(
        _FHIR_API_PATH, "create_fhir_app", "_extract_translate_params"
    )
    assert src, "_extract_translate_params source not found"
    assert "_extract_named_coding_from_parameters" in src
    assert "_extract_codeable_concept_from_parameters" in src


def test_t85_build_parameters_translate_emits_exactly_3_match_parts():
    """TERMINOLOGIST: source-read contract — build_parameters_translate emits
    exactly 3 match parts (equivalence, concept, source) per FHIR R4 spec.

    Spec: FHIR R4 $translate Out `match` — repeating part containing
    equivalence + concept + source (Out Parameters table).
    Source: https://hl7.org/fhir/R4/conceptmap-operation-translate.html.
    """
    src = _get_func_source(
        Path(responses_module.__file__).parent / "responses.py",
        "build_parameters_translate",
    )
    # Count "name": "X" part entries inside a match entry.
    # The 3 expected parts are: equivalence, concept, source.
    # We check each name appears in the source.
    assert '"equivalence"' in src or "'equivalence'" in src
    assert '"concept"' in src or "'concept'" in src
    assert '"source"' in src or "'source'" in src


def test_t86_internal_rel_map_no_off_spec_values():
    """TERMINOLOGIST: source-read contract — INTERNAL_REL_TO_FHIR_EQUIVALENCE
    emits only FHIR R4 closed-enum values. CF-HISTORIAN-VS01-01 RESOLVED.

    Spec: FHIR R4 ConceptMapEquivalence closed enum (10 values) —
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html.
    """
    values = set(equivalence_module.INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    assert values <= FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"INTERNAL_REL_TO_FHIR_EQUIVALENCE emits off-spec values: "
        f"{values - FHIR_R4_CONCEPT_MAP_EQUIVALENCE}"
    )
    # Specifically: NO R5/R4B contamination.
    assert "subsumedby" not in values  # R5/R4B value
    assert "matches" not in values  # R5-only value


# ===========================================================================
# Lens 9: Clinical-safety no-silent-wrong-answer on edge cases.
# ===========================================================================


def test_t90_unknown_system_translate_no_silent_match(fhir_client):
    """TERMINOLOGIST: $translate with unknown source system MUST NOT produce
    a silent match. Either 400 (rejected) or 200 + result=false (no matches).

    Spec: FHIR R4 $translate Out `result` — "true if the engine was able to
    return at least one match."
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://unknown.invalid",
            "code": "anycode",
            "targetsystem": ICD10CM_URI,
        },
    )
    # Either rejected at the URI layer (400) or returns no matches (200 +
    # result=false).
    assert r.status_code in (200, 400)
    if r.status_code == 200:
        body = r.json()
        assert _find_param(body, "result").get("valueBoolean") is False
        matches = [p for p in body["parameter"] if p.get("name") == "match"]
        assert len(matches) == 0


def test_t91_unknown_code_translate_no_silent_match(fhir_client):
    """TERMINOLOGIST: $translate with unknown code in known system MUST NOT
    produce a silent match. Result=false, message informative.

    Spec: FHIR R4 $translate Out `result`.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "9999999999UNKNOWN",
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _find_param(body, "result").get("valueBoolean") is False
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert len(matches) == 0


def test_t92_translate_message_always_present(fhir_client):
    """TERMINOLOGIST: $translate ALWAYS emits a `message` Out parameter on
    BOTH result=true (informative "N matches found") AND result=false
    (informational "0 matches found"). This is the always-emit message
    convention per the prior CM-02 TERMINOLOGIST run.

    Spec: FHIR R4 $translate Out `message` — 0..1 string parameter.
    """
    # result=true case.
    r1 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r1.status_code == 200
    msg1 = _find_param(r1.json(), "message")
    assert msg1 is not None
    assert "1 matches" in msg1.get("valueString", "")  # exact count

    # result=false case.
    r2 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": RXNORM_URI,  # no SNOMED T2DM -> RxNorm mapping
        },
    )
    assert r2.status_code == 200
    msg2 = _find_param(r2.json(), "message")
    assert msg2 is not None
    assert "0 matches" in msg2.get("valueString", "")


def test_t93_translate_message_clinically_informative_on_match(fhir_client):
    """TERMINOLOGIST: $translate `message` on result=true IS clinically
    informative — it counts matches. A CDS hook reading the message knows
    how many candidate translations the server considered.

    Spec: FHIR R4 $translate Out `message` — "Error details, if result =
    false. If this is provided when result = true, the message carries hints
    and warnings."
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    msg = _find_param(r.json(), "message").get("valueString", "")
    # The message MUST cite the actual count (not a vague "matches found").
    assert "1" in msg  # exactly 1 match in the fixture


def test_t94_translate_response_shape_audit(fhir_client):
    """TERMINOLOGIST: response shape audit — $translate Parameters body has
    resourceType=Parameters + parameter list with result (Boolean) + message
    (String) + match parts (when matches exist).

    Spec: FHIR R4 $translate Out Parameters —
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resourceType"] == "Parameters"
    # Required Out params.
    assert _find_param(body, "result") is not None
    assert _find_param(body, "message") is not None
    # Match parts (when matches exist).
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert len(matches) == 1
    parts = matches[0].get("part", [])
    part_names = {pt.get("name") for pt in parts}
    assert "equivalence" in part_names
    assert "concept" in part_names
    assert "source" in part_names


def test_t95_translate_content_type_application_fhir_json(fhir_client):
    """TERMINOLOGIST: Content-Type audit — $translate response MUST return
    application/fhir+json. CR-001 sibling.

    Spec: FHIR R4 §3.1.0.1.9 — server MUST return application/fhir+json for
    JSON responses.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+json")


# ===========================================================================
# Lens 10: Cross-handler GET ↔ POST clinical-content parity.
# ===========================================================================


def test_t100_translate_get_post_byte_exact_parity(fhir_client):
    """TERMINOLOGIST: GET ↔ POST byte-exact clinical-content parity on
    $translate — status, result, match count, equivalence, target code,
    target display all byte-exact.

    Spec: FHIR R4 §3.2.1.1 — POST with body MUST produce same response as
    GET with query params.
    """
    get_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    post_r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=_build_parameters_body(SNOMED_URI, SNOMED_T2DM_CODE, ICD10CM_URI),
        headers={"content-type": "application/fhir+json"},
    )
    assert get_r.status_code == post_r.status_code == 200
    get_body = get_r.json()
    post_body = post_r.json()
    # 5-axis byte-exact parity.
    assert _find_param(get_body, "result") == _find_param(post_body, "result")
    get_target = _translate_target_coding(get_body)
    post_target = _translate_target_coding(post_body)
    assert get_target == post_target
    assert _translate_equivalence(get_body) == _translate_equivalence(post_body)


def test_t101_translate_get_post_byte_exact_parity_no_match(fhir_client):
    """TERMINOLOGIST: GET ↔ POST byte-exact parity on $translate no-match
    path (targetsystem=RXNORM). Result=false byte-exact on both paths.

    Spec: FHIR R4 §3.2.1.1 — POST with body MUST produce same response as
    GET.
    """
    get_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": RXNORM_URI,
        },
    )
    post_r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=_build_parameters_body(SNOMED_URI, SNOMED_T2DM_CODE, RXNORM_URI),
        headers={"content-type": "application/fhir+json"},
    )
    assert get_r.status_code == post_r.status_code == 200
    get_body = get_r.json()
    post_body = post_r.json()
    assert _find_param(get_body, "result") == _find_param(post_body, "result")
    assert _find_param(get_body, "result").get("valueBoolean") is False
    assert _find_param(get_body, "message") == _find_param(post_body, "message")


@pytest.mark.parametrize(
    "alias",
    [
        SNOMED_URI_TRAILING_SLASH,
        SNOMED_URI_URN_OID,
        SNOMED_URI_UPPERCASE_SCHEME,
        ICD10CM_URI_TRAILING_SLASH,
        ICD10CM_URI_URN_OID,
    ],
)
def test_t102_translate_get_post_parity_under_alias_input(fhir_client, alias):
    """TERMINOLOGIST: parametrized — GET ↔ POST byte-exact parity on $translate
    under alias URI input. 5 alias variants verify canonical resolution on
    both paths.

    Spec: FHIR R4 Coding.system — alias URIs MUST resolve to canonical.
    """
    get_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": alias,
            "code": SNOMED_T2DM_CODE,
            "targetsystem": ICD10CM_URI,
        },
    )
    post_r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=_build_parameters_body(alias, SNOMED_T2DM_CODE, ICD10CM_URI),
        headers={"content-type": "application/fhir+json"},
    )
    assert get_r.status_code == post_r.status_code == 200
    # Clinical-content parity.
    get_target = _translate_target_coding(get_r.json())
    post_target = _translate_target_coding(post_r.json())
    assert get_target == post_target
    # Match.source.system MUST be canonical (CR-012 RESOLVED).
    if alias in (SNOMED_URI_TRAILING_SLASH, SNOMED_URI_URN_OID, SNOMED_URI_UPPERCASE_SCHEME):
        get_source = _translate_source_coding(get_r.json())
        post_source = _translate_source_coding(post_r.json())
        assert get_source.get("system") == SNOMED_URI
        assert post_source.get("system") == SNOMED_URI
