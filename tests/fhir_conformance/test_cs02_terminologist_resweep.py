"""TERMINOLOGIST RESWEEP probes for CS-02 (CodeSystem $lookup Operation) —
fresh full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
       (canonical R4 / 4.0.1; same content as
       build.fhir.org/codesystem-operation-lookup.html)

TERMINOLOGIST lens (per ROLE_QA_ENGINEER Section 3): clinical and
terminological correctness. The other personalities find technical bugs;
TERMINOLOGIST finds domain bugs. Per GLOBAL_RULES.md "TERMINOLOGIST
Findings Are HIGH Severity", all findings default to HIGH severity.

EXPLORER tip for TERMINOLOGIST — probe the canonical-DISPLAY consistency
across operations. EXPLORER verified canonical-URI consistency via the
$lookup → $subsumes → $translate round-trip (test_e30-e33). DISPLAY is the
next clinical-correctness layer.

TERMINOLOGIST lens for CS-02 resweep, 10 lens dimensions:

  L1 — Canonical-DISPLAY consistency across operations (EXPLORER tip):
       $lookup Out display ↔ $validate-code Out display for the SAME code
       MUST be clinically consistent. Both are the engine's preferred term
       for the code; a mismatch would be silent-wrong-answer.
  L2 — $lookup display clinical sensibility (per seeded source):
       display is engine preferred term (clinically meaningful, not raw
       code) for SNOMED, ICD-10-CM, RxNorm. T2DM distinguishable from DM.
  L3 — $translate target concept display ↔ $lookup target display:
       the match.concept.display in $translate response MUST equal the
       target code's $lookup Out display (consistency of target display
       across operations).
  L4 — Property 'designation' clinical correctness: per R4 spec, designation
       is 0..*; sub-parts are language/use/value. medterm4ds fixture is
       single-language; designation absence is spec-conformant.
  L5 — displayLanguage parameter clinical correctness: per R4 spec
       displayLanguage "controls which language the preferred display
       string is returned in". medterm4ds has no localized data, so the
       In displayLanguage parameter is accepted but the Out display is
       the engine's only (English) preferred term. INTENDED per SKEPTIC
       resweep L8.
  L6 — Subsumption-decomposition property 'parent'/'child' clinical
       correctness: per R4 spec, parent and child are properties "defined
       for all code systems". The fixture seeds T2DM → DM via mrrel PAR.
       medterm4ds does not honor parent/child decomposition in $lookup
       today (deferred feature enhancement, not a bug — SKEPTIC/HISTORIAN/
       EXPLORER resweep consensus). Verified via carry-forward-as-probe.
  L7 — Cross-resource clinical consistency: $lookup Out system+code+display
       consistent with CodeSystem READ response for the same system.
  L8 — name = code system name (NOT concept term): per spec "A display name
       for the code system" — the CS-01/TERMINOLOGIST load-bearing contract.
  L9 — display = recommended display (per R4 §4.8.21.1): wire-format is
       valueString (NOT valueCode); display never echoes raw code when STR
       exists.
  L10 — Source-read structural contracts: builders delegate canonical
        display through engine `code_info.name`; never echo client input
        when engine has a canonical display.

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts the POSITIVE
success shape (200 + Parameters body with the expected fields), not just
absence of an error string.

Per GLOBAL_RULES.md "Right-level test": TERMINOLOGIST does not use
automated proxies for clinical correctness. The display string comparison
is the load-bearing test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DM = "73211009"          # Diabetes mellitus
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM = "44054006"        # Type 2 diabetes mellitus
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"
ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_860975 = "860975"
RXNORM_860975_DISPLAY = "24 HR metformin 500 MG Oral Tablet"

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


# ---------------------------------------------------------------------------
# Source-read helpers (TS-04 HISTORIAN methodology — walks both
# ast.FunctionDef AND ast.AsyncFunctionDef for nested handlers).
# ---------------------------------------------------------------------------

def _get_func_source(file_path: Path, func_name: str) -> str:
    """Extract source text of a function (possibly nested) by name."""
    tree = ast.parse(file_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(file_path.read_text(), node)
    return ""


def _params_by_name(body: dict, name: str) -> list[dict]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _first_param(body: dict, name: str) -> dict | None:
    params = _params_by_name(body, name)
    return params[0] if params else None


def _param_value(body: dict, name: str, value_key: str = "valueString"):
    p = _first_param(body, name)
    return p.get(value_key) if p else None


# ---------------------------------------------------------------------------
# L1 — Canonical-DISPLAY consistency across operations (EXPLORER tip)
# Spec: $lookup Out display "The preferred display for this concept"
#       $validate-code Out display "A display to show to the user when the
#       system doesn't know what to do with the code, or to verify the code
#       is the right one."
# Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
#         https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
# ---------------------------------------------------------------------------

def test_t10_lookup_display_matches_validate_code_display_snomed_dm(fhir_client):
    """HIGH — canonical-DISPLAY consistency across operations (EXPLORER tip).

    For SNOMED DM (73211009), the Out `display` from $lookup MUST equal the
    Out `display` from $validate-code. Both operations resolve the same code
    against the same engine — a display mismatch would be silent-wrong-answer
    (the client sees two different "preferred terms" for the same concept).

    Spec citations:
      $lookup Out display: "The preferred display for this concept"
        (1..1 string, https://hl7.org/fhir/R4/codesystem-operation-lookup.html)
      $validate-code Out display: "A display for the concept that is
        validated" (0..1 string, https://hl7.org/fhir/R4/codesystem-
        operation-validate-code.html)

    Acceptance criteria:
      - $lookup Out display == "Diabetes mellitus"
      - $validate-code Out display == "Diabetes mellitus"
      - Both are byte-exact equal
    """
    # $lookup DM
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r_lookup.status_code == 200, f"lookup DM: {r_lookup.text[:200]!r}"
    lookup_display = _param_value(r_lookup.json(), "display")
    assert lookup_display == SNOMED_DM_DISPLAY, (
        f"lookup display MUST be the engine's preferred term; got "
        f"{lookup_display!r}"
    )

    # $validate-code DM
    r_validate = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r_validate.status_code == 200, f"validate DM: {r_validate.text[:200]!r}"
    validate_display = _param_value(r_validate.json(), "display")
    assert validate_display == SNOMED_DM_DISPLAY, (
        f"validate display MUST be the engine's preferred term; got "
        f"{validate_display!r}"
    )

    # Cross-operation consistency
    assert lookup_display == validate_display, (
        f"DISPLAY DRIFT across operations: lookup={lookup_display!r} "
        f"validate={validate_display!r}. Both operations MUST resolve the "
        f"same code against the same engine — the canonical display MUST "
        f"be identical."
    )


def test_t11_lookup_display_matches_validate_code_display_snomed_t2dm(fhir_client):
    """HIGH — canonical-DISPLAY consistency across operations for T2DM.

    Same as test_t10 but for T2DM (44054006). The T2DM display MUST be
    distinguishable from DM (clinically distinct conditions).
    """
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r_lookup.status_code == 200
    lookup_display = _param_value(r_lookup.json(), "display")
    assert lookup_display == SNOMED_T2DM_DISPLAY

    r_validate = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r_validate.status_code == 200
    validate_display = _param_value(r_validate.json(), "display")
    assert validate_display == SNOMED_T2DM_DISPLAY

    assert lookup_display == validate_display, (
        f"DISPLAY DRIFT: lookup={lookup_display!r} validate={validate_display!r}"
    )


@pytest.mark.parametrize(
    "system,code,expected_display",
    [
        (SNOMED_URI, SNOMED_DM, SNOMED_DM_DISPLAY),
        (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM_DISPLAY),
        (ICD10CM_URI, ICD10CM_E11, ICD10CM_E11_DISPLAY),
        (RXNORM_URI, RXNORM_860975, RXNORM_860975_DISPLAY),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10cm-e11", "rxnorm-metformin"],
)
def test_t12_lookup_validate_display_consistent_parametrized(
    fhir_client, system, code, expected_display
):
    """HIGH — canonical-DISPLAY consistency parametrized over every seeded code.

    For each seeded code across SNOMED, ICD-10-CM, and RxNorm, $lookup and
    $validate-code MUST agree on the Out `display`. Catches engine-internal
    display-resolution divergence (e.g., one path uses PT atom while the
    other uses LO/HT atom).
    """
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
    )
    assert r_lookup.status_code == 200, (
        f"lookup {code}: {r_lookup.text[:200]!r}"
    )
    lookup_display = _param_value(r_lookup.json(), "display")

    r_validate = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": system, "code": code},
    )
    assert r_validate.status_code == 200, (
        f"validate {code}: {r_validate.text[:200]!r}"
    )
    validate_display = _param_value(r_validate.json(), "display")

    assert lookup_display == expected_display, (
        f"lookup display for {code} MUST be {expected_display!r}; got "
        f"{lookup_display!r}"
    )
    assert validate_display == expected_display, (
        f"validate display for {code} MUST be {expected_display!r}; got "
        f"{validate_display!r}"
    )
    assert lookup_display == validate_display, (
        f"DISPLAY DRIFT for {code}: lookup={lookup_display!r} "
        f"validate={validate_display!r}"
    )


def test_t13_validate_code_display_does_not_echo_client_input_when_mismatched(fhir_client):
    """HIGH — TS-02 TERMINOLOGIST QA-029 regression defense.

    When the client supplies a `display` that doesn't match the engine's
    canonical display, $validate-code MUST return the engine's canonical
    display (NOT echo the client's input). The client's display drives
    `result=false`; the Out display is the canonical "correct" answer.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
    Out display = "A display for the concept that is validated".
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "display": "WRONG-CLIENT-PROVIDED-DISPLAY",
        },
    )
    assert r.status_code == 200
    body = r.json()
    # result must be false (display mismatch)
    result = _param_value(body, "result", "valueBoolean")
    assert result is False, (
        f"display mismatch MUST produce result=false; got result={result}"
    )
    # Out display MUST be the canonical, NOT the client's input
    display = _param_value(body, "display")
    assert display == SNOMED_T2DM_DISPLAY, (
        f"Out display MUST be the engine's canonical {SNOMED_T2DM_DISPLAY!r}; "
        f"got {display!r}. NEVER echo client input as canonical (TS-02 "
        f"TERMINOLOGIST QA-029 client-input-as-canonical drift pattern)."
    )


# ---------------------------------------------------------------------------
# L2 — $lookup display clinical sensibility per source
# Per R4 spec Out display: "The preferred display for this concept"
# Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# ---------------------------------------------------------------------------

def test_t20_lookup_display_snomed_dm_clinically_sensible(fhir_client):
    """HIGH — SNOMED DM (73211009) Out display is "Diabetes mellitus".

    Clinical correctness:
      - The display is the SNOMED PT term (clinically preferred).
      - The display is NOT a layperson name (e.g., "high blood sugar").
      - The display is NOT a raw code.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r.status_code == 200
    display = _param_value(r.json(), "display")
    assert display == SNOMED_DM_DISPLAY, (
        f"SNOMED DM display MUST be the clinically-preferred PT "
        f"{SNOMED_DM_DISPLAY!r}; got {display!r}"
    )
    # Clinically sensible: not a raw code, not a layperson term
    assert display != SNOMED_DM, "display MUST NOT echo raw code"
    assert "high blood sugar" not in display.lower(), (
        "display MUST NOT be a layperson term (high blood sugar is a PF name)"
    )


def test_t21_lookup_display_snomed_t2dm_distinguishable_from_dm(fhir_client):
    """HIGH — SNOMED T2DM (44054006) display MUST be distinguishable from DM.

    Clinical correctness: T2DM is a clinically distinct condition from DM
    (the broader parent). The display strings MUST differ — a client using
    them for chart documentation MUST see distinct terms.
    """
    r_t2dm = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    r_dm = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r_t2dm.status_code == 200 and r_dm.status_code == 200
    t2dm_display = _param_value(r_t2dm.json(), "display")
    dm_display = _param_value(r_dm.json(), "display")
    assert t2dm_display == SNOMED_T2DM_DISPLAY
    assert dm_display == SNOMED_DM_DISPLAY
    assert t2dm_display != dm_display, (
        f"T2DM display {t2dm_display!r} MUST differ from DM display "
        f"{dm_display!r} — these are clinically distinct concepts"
    )


def test_t22_lookup_display_rxnorm_includes_drug_name_and_form(fhir_client):
    """HIGH — RxNorm 860975 display includes drug name + dose + form.

    Clinical correctness: RxNorm SCD (Semantic Clinical Drug) atoms carry
    the full clinical drug description — drug name + dose + form + route.
    The Out display MUST be the SCD string, not an abbreviation.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": RXNORM_URI, "code": RXNORM_860975},
    )
    assert r.status_code == 200
    display = _param_value(r.json(), "display")
    assert display == RXNORM_860975_DISPLAY, (
        f"RxNorm display MUST be the full SCD string; got {display!r}"
    )
    # Drug name + dose + form invariants
    assert "metformin" in display.lower(), "display MUST include drug name"
    assert "500 MG" in display, "display MUST include dose"
    assert "Oral Tablet" in display, "display MUST include form + route"


def test_t23_lookup_display_icd10cm_e11_clinically_sensible(fhir_client):
    """HIGH — ICD-10-CM E11 Out display is "Type 2 diabetes mellitus".

    Clinical correctness: E11 is the ICD-10-CM code for "Type 2 diabetes
    mellitus" (the HT atom). The display is the clinically-preferred term,
    NOT the raw code.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": ICD10CM_URI, "code": ICD10CM_E11},
    )
    assert r.status_code == 200
    display = _param_value(r.json(), "display")
    assert display == ICD10CM_E11_DISPLAY, (
        f"ICD-10-CM E11 display MUST be the clinically-preferred HT term; "
        f"got {display!r}"
    )
    assert display != ICD10CM_E11, "display MUST NOT echo raw code"


@pytest.mark.parametrize(
    "system,code",
    [
        (SNOMED_URI, SNOMED_DM),
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_E11),
        (RXNORM_URI, RXNORM_860975),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10cm-e11", "rxnorm-metformin"],
)
def test_t24_lookup_display_never_empty_nor_raw_code_nor_alias(fhir_client, system, code):
    """HIGH — Out display MUST never be empty / raw code / alias URI.

    Clinical correctness: an empty display or a raw-code-as-display would
    be silent-wrong-answer (clients relying on display for human-readable
    output would see nothing or a meaningless code string).

    Per R4 spec: Out display is 1..1 string "The preferred display for
    this concept". An empty string is a violation of the cardinality
    contract in spirit.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
    )
    assert r.status_code == 200
    display = _param_value(r.json(), "display")
    assert display is not None, "Out display parameter missing"
    assert display != "", "Out display MUST NOT be empty string"
    assert display != code, (
        f"Out display MUST NOT echo raw code ({code!r}); got {display!r}"
    )


# ---------------------------------------------------------------------------
# L3 — $translate target concept display ↔ $lookup target display
# The match.concept.display in $translate response MUST equal the target
# code's $lookup Out display (target display consistency across operations).
# Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
#         https://hl7.org/fhir/R4/conceptmap-operation-translate.html
# ---------------------------------------------------------------------------

def test_t30_translate_target_display_matches_lookup_target_display(fhir_client):
    """HIGH — canonical-DISPLAY consistency on $translate target concept.

    When $translate resolves SNOMED T2DM (44054006) → ICD-10-CM E11 (same-CUI
    crosswalk via C0011847), the match.concept.display for the target (E11)
    MUST equal the $lookup Out display for E11.

    Rationale: both operations resolve the same target code against the same
    engine. A display mismatch would mean clients using $translate to get a
    target Coding would see a DIFFERENT display than clients using $lookup
    to verify the target code — silent-wrong-answer at the integration layer.

    Spec citations:
      $translate Out match.concept.display: "The Coding that was translated"
        (https://hl7.org/fhir/R4/conceptmap-operation-translate.html)
      $lookup Out display: "The preferred display for this concept"
    """
    # Step 1: $translate T2DM → ICD-10-CM
    r_translate = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r_translate.status_code == 200
    body = r_translate.json()
    # Find the match
    match_param = _first_param(body, "match")
    assert match_param is not None, "$translate Out 'match' parameter missing"
    # Navigate the nested part[] to find the concept Coding
    concept_display = None
    target_code = None
    target_system = None
    for part in match_param.get("part", []):
        if part.get("name") == "concept":
            coding = part.get("valueCoding", {})
            concept_display = coding.get("display")
            target_code = coding.get("code")
            target_system = coding.get("system")
            break
    assert target_code == ICD10CM_E11, (
        f"target code MUST be {ICD10CM_E11!r}; got {target_code!r}"
    )
    assert target_system == ICD10CM_URI, (
        f"target system MUST be {ICD10CM_URI!r}; got {target_system!r}"
    )
    assert concept_display is not None, (
        "$translate match.concept.display MUST be present"
    )

    # Step 2: $lookup the target code (E11)
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": ICD10CM_URI, "code": ICD10CM_E11},
    )
    assert r_lookup.status_code == 200
    lookup_display = _param_value(r_lookup.json(), "display")
    assert lookup_display == ICD10CM_E11_DISPLAY

    # Step 3: cross-operation display consistency
    assert concept_display == lookup_display, (
        f"TARGET DISPLAY DRIFT: $translate match.concept.display="
        f"{concept_display!r} vs $lookup Out display={lookup_display!r}. "
        f"Both operations resolve the SAME target code ({ICD10CM_E11}) "
        f"against the SAME engine — the display MUST be identical."
    )


def test_t31_translate_source_coding_carries_source_display_safety(fhir_client):
    """MEDIUM — $translate match.source Coding does NOT carry a display field.

    Per build_parameters_translate (responses.py:186-189), the match.source
    valueCoding contains system+code but NOT display. This is INTENDED —
    the source Coding's display would duplicate the $lookup Out display
    for the source code. Clients wanting the source display SHOULD call
    $lookup separately.

    This probe documents the contract (carry-forward-as-probe pattern).
    A future change that ADDS source.display MUST ensure it equals the
    $lookup Out display for the source code (canonical-DISPLAY invariant).
    """
    r_translate = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r_translate.status_code == 200
    body = r_translate.json()
    match_param = _first_param(body, "match")
    assert match_param is not None
    # match.source valueCoding
    source_coding = None
    for part in match_param.get("part", []):
        if part.get("name") == "source":
            source_coding = part.get("valueCoding", {})
            break
    assert source_coding is not None, "match.source valueCoding missing"
    assert source_coding.get("system") == SNOMED_URI
    assert source_coding.get("code") == SNOMED_T2DM
    # Document the absence of source.display (INTENDED today)
    if "display" in source_coding:
        # If a future change adds source.display, it MUST equal the $lookup
        # Out display for the source code (canonical-DISPLAY invariant).
        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r_lookup.status_code == 200
        lookup_display = _param_value(r_lookup.json(), "display")
        assert source_coding["display"] == lookup_display, (
            f"match.source.display={source_coding['display']!r} MUST equal "
            f"$lookup Out display={lookup_display!r} for source code "
            f"{SNOMED_T2DM} (canonical-DISPLAY invariant)."
        )


# ---------------------------------------------------------------------------
# L4 — Property 'designation' clinical correctness
# Per R4 spec: designation is 0..*; sub-parts are language/use/value.
# Spec text: "Additional representations for this concept"
# Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# ---------------------------------------------------------------------------

def test_t40_designation_absence_is_spec_conformant(fhir_client):
    """HIGH — Out `designation` 0..* absence is spec-conformant.

    Per R4 spec: designation is 0..* — the server MAY return designations
    if it has them, but absence is not a violation. medterm4ds fixture is
    single-language (English-only), so no designations are emitted.

    Clinical correctness: the server MUST NOT fabricate designations when
    none exist (silent-wrong-answer if it did — clients would see
    fabricated language variants).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r.status_code == 200
    body = r.json()
    # designation MAY be absent (single-language fixture)
    designations = _params_by_name(body, "designation")
    # If present, each MUST have a value sub-part per spec
    for d in designations:
        parts = d.get("part", [])
        value_part = next(
            (p for p in parts if p.get("name") == "value"), None
        )
        assert value_part is not None, (
            "designation without value sub-part violates spec — value is 1..1"
        )
        assert value_part.get("valueString"), (
            "designation.value MUST be a non-empty string"
        )


def test_t41_designation_use_field_must_be_coding_when_present(fhir_client):
    """HIGH — when a designation has a `use` sub-part, it MUST be a Coding.

    Per R4 spec: designation.use 0..1 Coding — "A code that details how
    this designation would be used". A string-as-Coding would violate the
    spec type contract.

    medterm4ds fixture emits no designations, so this probe is a
    defensive assertion: IF a future designation is added with a use
    field, it MUST be a Coding (system+code+display), not a scalar string.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r.status_code == 200
    body = r.json()
    designations = _params_by_name(body, "designation")
    for d in designations:
        parts = d.get("part", [])
        use_part = next(
            (p for p in parts if p.get("name") == "use"), None
        )
        if use_part is not None:
            coding = use_part.get("valueCoding")
            assert coding is not None, (
                "designation.use MUST be a valueCoding per spec; got "
                f"{use_part!r}"
            )
            assert "system" in coding, "designation.use Coding missing system"
            assert "code" in coding, "designation.use Coding missing code"


def test_t42_designation_request_property_does_not_break_response(fhir_client):
    """HIGH — requesting property=designation is accepted; response still
    carries the canonical display in Out `display` (not just in designation).

    Clinical correctness: requesting designation MUST NOT silently drop
    the Out `display` parameter. Display is 1..1 per spec; designation is
    additional representations.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "property": "designation",
        },
    )
    assert r.status_code == 200
    body = r.json()
    # display is still present
    display = _param_value(body, "display")
    assert display == SNOMED_DM_DISPLAY, (
        f"Out display MUST still be present when property=designation "
        f"requested; got {display!r}"
    )


# ---------------------------------------------------------------------------
# L5 — displayLanguage parameter clinical correctness
# Per R4 spec: "The requested language for display (see $expand.displayLanguage)"
# medterm4ds has no localized data, so display is always the English preferred
# term. INTENDED per SKEPTIC resweep L8.
# Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "display_lang",
    ["en", "en-US", "fr", "fr-FR", "es", "de-DE", "zh-Hans-CN"],
)
def test_t50_display_language_param_does_not_change_canonical_display(
    fhir_client, display_lang
):
    """HIGH — displayLanguage parameter does not change Out display.

    medterm4ds has no localized designation data; the Out display is always
    the engine's English preferred term regardless of displayLanguage input.
    A future localization feature MUST produce clinically-equivalent display
    strings in the requested language — and the Out `system`/`code` MUST
    remain identical across displayLanguage values (the concept identity
    is language-independent).

    Clinical safety: a client calling with displayLanguage=fr MUST NOT see
    a different code or system than a client calling with displayLanguage=en
    (the concept is the same; only the human-readable label differs).
    """
    # Baseline call (no displayLanguage)
    r_base = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r_base.status_code == 200
    base_body = r_base.json()
    base_system = _param_value(base_body, "system", "valueUri")
    base_code = _param_value(base_body, "code", "valueCode")

    # Call with displayLanguage
    r_lang = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "displayLanguage": display_lang,
        },
    )
    assert r_lang.status_code == 200, (
        f"displayLanguage={display_lang}: {r_lang.text[:200]!r}"
    )
    lang_body = r_lang.json()
    lang_system = _param_value(lang_body, "system", "valueUri")
    lang_code = _param_value(lang_body, "code", "valueCode")

    # Concept identity is language-independent
    assert lang_system == base_system, (
        f"displayLanguage={display_lang}: system MUST NOT change "
        f"(base={base_system!r}, lang={lang_system!r})"
    )
    assert lang_code == base_code, (
        f"displayLanguage={display_lang}: code MUST NOT change "
        f"(base={base_code!r}, lang={lang_code!r})"
    )
    # Display is the English preferred term (medterm4ds has no localized data)
    lang_display = _param_value(lang_body, "display")
    assert lang_display == SNOMED_T2DM_DISPLAY


# ---------------------------------------------------------------------------
# L6 — Subsumption-decomposition property 'parent'/'child' clinical
# correctness
# Per R4 spec In Parameters: parent and child are properties "defined for
# all code systems" — requesting property=parent SHOULD return the parent
# code as a property.
# Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# CARRY-FORWARD-AS-PROBE: SKEPTIC/HISTORIAN/EXPLORER resweep consensus is
# that parent/child non-honoring is a deferred feature enhancement, not a
# bug. TERMINOLOGIST confirms the engine is structurally correct: $subsumes
# honors the seeded hierarchy (DM subsumes T2DM), so the underlying mrrel
# data is consumed. $lookup simply does not surface the relationship under
# property=parent/child today.
# ---------------------------------------------------------------------------

def test_t60_subsumption_seeded_hierarchy_is_clinically_correct(fhir_client):
    """HIGH — fixture seeds T2DM PAR→DM; $subsumes honors this correctly.

    This is the underlying-clinical-correctness invariant: the engine DOES
    consume the mrrel PAR edge for $subsumes. DM (broader) subsumes T2DM
    (narrower); T2DM is subsumed-by DM. If $subsumes returned the wrong
    directionality, that would be a TERMINOLOGIST clinical-correctness bug.

    Per CS-01 TERMINOLOGIST L6 methodology (direct-clinical-directionality
    probe via $subsumes mirror): the parent/child relationship IS known to
    the engine via $subsumes. The carry-forward is purely about $lookup's
    property=parent surfacing, not about whether the engine has the data.
    """
    # DM subsumes T2DM (broader subsumes narrower)
    r1 = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DM,     # broader
            "codeB": SNOMED_T2DM,   # narrower
        },
    )
    assert r1.status_code == 200
    outcome1 = _param_value(r1.json(), "outcome", "valueCode")
    assert outcome1 == "subsumes", (
        f"DM (broader) MUST subsumes T2DM (narrower); got outcome={outcome1!r}"
    )

    # T2DM subsumed-by DM (reverse direction)
    r2 = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM,   # narrower
            "codeB": SNOMED_DM,     # broader
        },
    )
    assert r2.status_code == 200
    outcome2 = _param_value(r2.json(), "outcome", "valueCode")
    assert outcome2 == "subsumed-by", (
        f"T2DM (narrower) MUST be subsumed-by DM (broader); got "
        f"outcome={outcome2!r}"
    )


def test_t61_lookup_property_parent_does_not_break_clinical_data(fhir_client):
    """HIGH — requesting property=parent on T2DM does not break the
    canonical display/name/system/code Out parameters.

    Per R4 spec: when property=parent is requested, the server returns
    parent/child properties if known. medterm4ds does not surface the
    parent relationship in $lookup today (deferred feature enhancement
    per SKEPTIC/HISTORIAN/EXPLORER resweep consensus), BUT the canonical
    Out parameters (name, code, system, display, abstract) MUST still be
    present and clinically correct.

    This is the clinical-safety bound: even if parent/child decomposition
    is not honored, the canonical data MUST NOT be silently wrong.
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
    body = r.json()
    # Canonical Out parameters are still clinically correct
    assert _param_value(body, "name", "valueString") is not None
    assert _param_value(body, "code", "valueCode") == SNOMED_T2DM
    assert _param_value(body, "system", "valueUri") == SNOMED_URI
    assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY
    assert _param_value(body, "abstract", "valueBoolean") is False


def test_t62_lookup_property_child_does_not_break_clinical_data(fhir_client):
    """HIGH — requesting property=child on DM does not break the canonical
    Out parameters. Mirror of test_t61 for the reverse direction."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DM,
            "property": "child",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "code", "valueCode") == SNOMED_DM
    assert _param_value(body, "system", "valueUri") == SNOMED_URI
    assert _param_value(body, "display") == SNOMED_DM_DISPLAY


# ---------------------------------------------------------------------------
# L7 — Cross-resource clinical consistency: $lookup Out `system` ↔
# CapabilityStatement capabilitystatement-supported-system extension.
# Per R4 spec: the supported-system extension advertises the canonical URIs
# of every external code system the server recognizes. The $lookup Out
# `system` for a code MUST be a URI in that extension's value set.
# Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
#         https://hl7.org/fhir/R4/extension-capabilitystatement-supported-system.html
# medterm4ds does NOT persist CodeSystem resources (every READ returns 404);
# the supported-system extension is the canonical advertisement surface.
# ---------------------------------------------------------------------------

_SUPPORTED_SYSTEM_EXT_URL = (
    "http://hl7.org/fhir/StructureDefinition/capabilitystatement-supported-system"
)


def _get_supported_systems(client) -> list[str]:
    """Fetch the capabilitystatement-supported-system extension values."""
    r = client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.json()
    supported: list[str] = []
    for ext in body.get("extension", []):
        if ext.get("url") == _SUPPORTED_SYSTEM_EXT_URL:
            supported.append(ext.get("valueUri"))
    return supported


def test_t70_lookup_out_system_in_capabilitystatement_supported_systems(fhir_client):
    """HIGH — $lookup Out `system` MUST be in the supported-system extension.

    The $lookup Out `system` is the canonical URI of the resolved code
    system. The CapabilityStatement capabilitystatement-supported-system
    extension advertises every external code system URI the server
    recognizes. The $lookup Out `system` MUST be a URI in that set — a
    URI not advertised would be silent-wrong-answer (clients querying
    /fhir/metadata to discover supported systems would never know the
    server recognizes this one).

    Cross-resource consistency: $lookup ↔ CapabilityStatement MUST agree.
    """
    supported = _get_supported_systems(fhir_client)
    assert SNOMED_URI in supported, (
        f"SNOMED canonical URI MUST be in supported-system extension; "
        f"got supported={supported}"
    )

    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_T2DM},
    )
    assert r_lookup.status_code == 200
    lookup_system = _param_value(r_lookup.json(), "system", "valueUri")
    assert lookup_system in supported, (
        f"$lookup Out system={lookup_system!r} MUST be in the supported-"
        f"system extension values={supported!r}"
    )


@pytest.mark.parametrize(
    "system,code",
    [
        (SNOMED_URI, SNOMED_DM),
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_E11),
        (RXNORM_URI, RXNORM_860975),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10cm-e11", "rxnorm-metformin"],
)
def test_t71_lookup_out_system_in_supported_systems_parametrized(
    fhir_client, system, code
):
    """HIGH — for every seeded code, $lookup Out `system` is in the
    supported-system extension. Parametrized over all 4 seeded codes.

    Catches the case where a source is resolvable via $lookup (because
    the engine knows it) but NOT advertised in the supported-system
    extension (because the metadata builder missed it). Both surfaces
    MUST agree.
    """
    supported = _get_supported_systems(fhir_client)
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
    )
    assert r.status_code == 200
    out_system = _param_value(r.json(), "system", "valueUri")
    assert out_system in supported, (
        f"$lookup Out system={out_system!r} for {code} MUST be in the "
        f"supported-system extension. Supported={supported!r}"
    )


# ---------------------------------------------------------------------------
# L8 — name = code system name (NOT concept term)
# Per R4 spec: Out name = "A display name for the code system"
# This is the CS-01/TERMINOLOGIST load-bearing contract.
# Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "system,code,cs_name_substring",
    [
        (SNOMED_URI, SNOMED_DM, "SNOMED"),
        (ICD10CM_URI, ICD10CM_E11, "International Classification"),
        (RXNORM_URI, RXNORM_860975, "RxNorm"),
    ],
    ids=["snomed", "icd10cm", "rxnorm"],
)
def test_t80_lookup_name_is_code_system_name_not_concept_term(
    fhir_client, system, code, cs_name_substring
):
    """HIGH — Out `name` is the CODE SYSTEM display name (NOT concept term).

    Per R4 spec: Out name = "A display name for the code system" — e.g.
    "SNOMED CT" for http://snomed.info/sct. NOT the concept's display
    term (which is in `display`).

    Clinical correctness: a client using `name` as a column header MUST see
    the code system label (e.g., "SNOMED Clinical Terms"), not the concept
    name (e.g., "Diabetes mellitus"). Mixing these would be a clinical-
    documentation error.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
    )
    assert r.status_code == 200
    body = r.json()
    name = _param_value(body, "name")
    display = _param_value(body, "display")
    assert name is not None
    assert cs_name_substring in name, (
        f"Out name MUST contain {cs_name_substring!r}; got {name!r}"
    )
    # name MUST NOT equal display (different things)
    assert name != display, (
        f"Out name ({name!r}) MUST NOT equal display ({display!r}) — "
        f"name is the code system label, display is the concept term"
    )


# ---------------------------------------------------------------------------
# L9 — display = recommended display (wire-format + value fidelity)
# Per R4 spec: Out display = "The preferred display for this concept" (1..1 string)
# Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "system,code",
    [
        (SNOMED_URI, SNOMED_DM),
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_E11),
        (RXNORM_URI, RXNORM_860975),
    ],
    ids=["snomed-dm", "snomed-t2dm", "icd10cm-e11", "rxnorm-metformin"],
)
def test_t90_lookup_display_wire_format_is_valueString(fhir_client, system, code):
    """HIGH — Out `display` parameter uses `valueString` wire type.

    Per R4 spec: Out display type = string. NOT valueCode, NOT valueUri.
    A valueCode here would let a server emit a non-display string (e.g.
    the raw code) as the "display" — silent-wrong-answer.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
    )
    assert r.status_code == 200
    body = r.json()
    display_param = _first_param(body, "display")
    assert display_param is not None
    assert "valueString" in display_param, (
        f"Out display MUST use valueString wire type; got keys="
        f"{list(display_param.keys())}"
    )
    assert "valueCode" not in display_param, (
        "Out display MUST NOT use valueCode wire type"
    )


def test_t91_lookup_abstract_always_false_for_concrete_codes(fhir_client):
    """HIGH — Out `abstract` is valueBoolean=False for concrete codes.

    Clinical correctness: all seeded codes (DM, T2DM, E11, metformin) are
    CONCRETE (not abstract). The Out `abstract` parameter MUST be False
    (lowercase valueBoolean per R4 wire format). An abstract=true response
    would mislead clients into excluding these codes from value set
    expansions.
    """
    for system, code in [
        (SNOMED_URI, SNOMED_DM),
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_E11),
        (RXNORM_URI, RXNORM_860975),
    ]:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        assert r.status_code == 200
        body = r.json()
        abstract_param = _first_param(body, "abstract")
        assert abstract_param is not None
        assert abstract_param.get("valueBoolean") is False, (
            f"abstract for {code} MUST be False (concrete code); got "
            f"{abstract_param}"
        )


# ---------------------------------------------------------------------------
# L10 — Source-read structural contracts
# Per R4 spec: builders delegate canonical display through engine code_info.name
# Source: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
#         engines/fhir/responses.py:build_parameters_lookup
#         engines/fhir/responses.py:build_parameters_validate
# ---------------------------------------------------------------------------

def test_t100_build_parameters_lookup_uses_code_info_name_for_display():
    """HIGH — source-read: build_parameters_lookup routes display through
    code_info.name (NOT client input).

    The Out `display` parameter at responses.py:58 is:
        _param("display", code_info.name or code_info.code.code)

    This is the load-bearing contract: the display is the engine's
    preferred term (code_info.name), NOT a client echo. The fallback to
    code_info.code.code (raw code) only fires when the engine has no name
    for the code (e.g., not-found path uses a different builder entirely).
    """
    src = _get_func_source(_RESPONSES_PATH, "build_parameters_lookup")
    assert src, "build_parameters_lookup source not found"
    # The display line MUST route through code_info.name
    assert 'code_info.name or code_info.code.code' in src, (
        "build_parameters_lookup MUST use code_info.name as the primary "
        "source for Out display (with code_info.code.code fallback). "
        "Found: " + src[:300]
    )
    # MUST NOT have a hardcoded display string literal as the Out value
    # (e.g., _param("display", "Diabetes mellitus") would be a drift)
    assert '_param("display", code_info' in src or (
        '_param("display", code_info.name' in src
    ), "display MUST be derived from code_info"


def test_t101_build_parameters_validate_uses_canonical_display_not_client_input():
    """HIGH — source-read: build_parameters_validate uses the engine's
    canonical display when code_info has a name.

    Per TS-02 TERMINOLOGIST QA-029: the Out `display` for $validate-code
    is the server's canonical display, NOT an echo of client-supplied
    display. The builder MUST prefer code_info.name.

    Source-read line (responses.py:112):
        canonical = (code_info.name if code_info and code_info.name
                     else None) or display
    """
    src = _get_func_source(_RESPONSES_PATH, "build_parameters_validate")
    assert src, "build_parameters_validate source not found"
    # The canonical-display-preferred contract MUST be present
    assert "code_info.name" in src, (
        "build_parameters_validate MUST use code_info.name as canonical "
        "display source. Found: " + src[:300]
    )
    # The builder MUST NOT have `_param("display", display)` (client echo)
    # as the only display-emission path
    assert "canonical" in src.lower(), (
        "build_parameters_validate MUST define a 'canonical' variable "
        "that prefers code_info.name over client display (TS-02 "
        "TERMINOLOGIST QA-029 contract)."
    )


def test_t102_do_lookup_routes_canonical_uri_via_canonical_system_uri():
    """HIGH — source-read: _do_lookup routes Out `system` through
    canonical_system_uri() helper (CS-02 HISTORIAN QA-047 contract).

    Without this delegation, the Out `system` would echo client input
    verbatim — including aliases (urn:oid:...) and trailing-slash variants.
    This is the client-input-as-canonical drift meta-pattern (count=8 PROMOTED).
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    assert src, "_do_lookup source not found"
    assert "canonical_system_uri" in src, (
        "_do_lookup MUST route Out `system` through canonical_system_uri() "
        "helper (CS-02 HISTORIAN QA-047). Found: " + src[:500]
    )


def test_t103_do_validate_routes_canonical_uri_via_canonical_system_uri():
    """HIGH — source-read: _do_validate routes Out `system` through
    canonical_system_uri() helper (CS-03 HISTORIAN QA-051 contract).

    Cross-handler parity: every _do_* handler that emits Out `system` MUST
    route through canonical_system_uri(). This is the cross-handler
    helper-wiring consistency pattern (count=6 PROMOTED).
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_validate")
    assert src, "_do_validate source not found"
    assert "canonical_system_uri" in src, (
        "_do_validate MUST route Out `system` through canonical_system_uri() "
        "(CS-03 HISTORIAN QA-051 + cross-handler parity)."
    )


def test_t104_build_parameters_translate_target_display_routed_via_code_mapping():
    """MEDIUM — source-read: build_parameters_translate target display is
    routed through m.target_display (NOT a hardcoded literal).

    Per TS-02 TERMINOLOGIST QA-030 (equivalence hardcoded regression class):
    the Out match.concept.display MUST be sourced from the engine's
    CodeMapping.target_display, NOT a hardcoded string. Empty string is
    the fallback when target_display is None.
    """
    src = _get_func_source(_RESPONSES_PATH, "build_parameters_translate")
    assert src, "build_parameters_translate source not found"
    assert "m.target_display" in src, (
        "build_parameters_translate MUST source target display from "
        "m.target_display (engine CodeMapping)."
    )
    # The fallback is empty string (not a fabricated display)
    assert 'm.target_display or ""' in src or 'm.target_display or " "' in src, (
        "target display MUST fall back to empty string when target_display "
        "is None — NEVER fabricate a display"
    )
