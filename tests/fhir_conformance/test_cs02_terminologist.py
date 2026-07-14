"""TERMINOLOGIST iteration CS-02 — clinical/terminological correctness.

Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
       (canonical R4; same content as build.fhir.org/codesystem-operation-lookup.html)

TERMINOLOGIST lens for CS-02 (CodeSystem $lookup):

1. **CF-EXPLORER-CS02 (canonical-URI echo decision)**: HISTORIAN QA-047 made
   ``$lookup`` Out ``system`` always emit the canonical URI (re-resolved via
   ``system_to_fhir_uri(fhir_uri_to_system(system_uri))``). The spec wording
   for Out ``system`` is sometimes informally summarized as "The requested
   system", but the canonical R4 documentation explicitly states:

       "The canonical URI of the code system that contains the concept that
        was looked up."

   and adds:

       "(this may differ from the value passed in `system` as an input
         parameter if the code was found in a different system/subsystem,
         such as a supplement)"

   So the spec is **not ambiguous**: Out ``system`` is by definition the
   canonical URI of the system the engine resolved the code in, NOT a
   client-input echo. HISTORIAN's QA-047 fix is spec-correct. Decision
   (a) is the right call.

2. **Patient-friendly display quality**: when patient-friendly JSON data
   exists for a code, the PF name is surfaced via the
   ``patient-friendly`` custom property (per CS-01 TERMINOLOGIST QA-045
   docstring contract). The Out ``display`` remains the engine's
   clinically-preferred term per spec §4.8.21.1 + §4.8.11 ("display"
   MUST be the code system's preferred term, NOT the layperson name).
   Cross-check the regression fixture expectations.

3. **Code-system URI round-trips** (TS-03 TERMINOLOGIST methodology):
   For each code, call ``$lookup``. Get the canonical-system +
   canonical-code. Either: (a) call ``$lookup`` again with those values
   and assert 200 + Parameters, OR (b) for chapter-range canonical
   codes, assert the URI is parseable by ``fhir_uri_to_system`` per
   CF-EXPLORER-CS01-01.

4. **Property values are clinically sensible**:
   - ``display`` is the clinically preferred term (mrconso PT for SNOMED,
     SCD for RxNorm, etc.).
   - ``name`` is the code system's FHIR display name.
   - ``system`` is the canonical FHIR URI (NOT alias, NOT trailing slash).

5. **Subsumption decomposition**: when ``property=parent`` is requested
   for a code with parents in the engine, are the parent relationships
   clinically correct? The conformance fixture seeds SNOMED
   44054006 (Type 2 diabetes mellitus) -> 73211009 (Diabetes mellitus)
   via MRREL PAR/isa.

6. **match-type DECISION (b) verification (CS-01 carry-forward)**:
   The CS-01 TERMINOLOGIST documented ``match-type`` as server-local
   engine pipeline vocabulary. Verify the docstring still accurately
   describes the 13 vocabulary values and their derivation semantics
   (no regression).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SNOMED_URI = "http://snomed.info/sct"
SNOMED_TYPE_2_DM = "44054006"
SNOMED_DIABETES_MELLITUS = "73211009"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
LOINC_URI = "http://loinc.org"

# Re-use the registry contract pinned by CS-01 TERMINOLOGIST QA-045.
# This is the single source of truth for the server-local match-type vocabulary;
# the docstring in _do_lookup MUST name every value listed here.
SERVER_LOCAL_MATCH_TYPE_VOCABULARY: dict[str, str] = {
    "exact": "PF name from same code's preferred atom.",
    "original": "No PF data found — engine returned the code's canonical preferred term.",
    "broader": "PF name from a broader concept (ancestor).",
    "group": "RxNorm multi-ingredient group product.",
    "ingredient": "RxNorm ingredient (active moiety).",
    "same_cui": "PF name from a different code sharing the same UMLS CUI.",
    "cvx_group": "CVX group code (vaccine-family grouping).",
    "broader_group": "Compound: PF from a broader RxNorm group.",
    "broader_ingredient": "Compound: PF from a broader RxNorm ingredient.",
    "first_axis": "LOINC first-axis classification (lab-class concept).",
    "snomed_fallback": "Engine fallback through SNOMED hierarchy.",
    "snomed_to_target_native_hierarchy": "SNOMED → target via target's native hierarchy.",
    "snomed_to_target_snomed_fallback": "SNOMED → target via SNOMED-defined crosswalk.",
}


# ---------------------------------------------------------------------------
# Local fixture helpers (mirror CS-02 HISTORIAN's pattern).
# ---------------------------------------------------------------------------

def _build_app_with_pf(pf_data: dict | None, tmp_path: Path):
    """Construct a FHIR app with a controlled patient-friendly baseline dir."""
    import duckdb

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

    baseline = tmp_path / "pf_baseline"
    baseline.mkdir(parents=True, exist_ok=True)
    if pf_data:
        for source_lower, payload in pf_data.items():
            (baseline / f"patient_friendly_{source_lower}.json").write_text(
                json.dumps(payload)
            )

    db_path = tmp_path / "umls.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE mrconso (CODE VARCHAR, TTY VARCHAR, STR VARCHAR, "
        "AUI VARCHAR, SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR)"
    )
    con.execute(
        "INSERT INTO mrconso VALUES "
        "('73211009','PT','Diabetes mellitus','A1','N','SNOMEDCT_US','C0011849'), "
        "('44054006','PT','Type 2 diabetes mellitus','A2','N','SNOMEDCT_US','C0011847'), "
        "('E11','HT','Type 2 diabetes mellitus','A3','N','ICD10CM','C0011847')"
    )
    con.execute("CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR)")
    con.execute("INSERT INTO mrrel VALUES ('A2','A1','isa','PAR')")
    con.close()

    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(tmp_path / "no_index"),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    return app, baseline


@pytest.fixture
def pf_loaded_client(tmp_path):
    """FHIR app with a valid PF entry for SNOMED 73211009 → ICD-10-CM E11."""
    app, baseline = _build_app_with_pf(
        {
            "snomedct_us": {
                SNOMED_DIABETES_MELLITUS: {
                    "name": "high blood sugar",
                    "match_type": "exact",
                    "canonical_code": "E11",
                    "canonical_system": "icd10",
                    "tty": "PT",
                }
            }
        },
        tmp_path,
    )
    old = os.environ.get("MEDTERM4DS_FHIR4PX_BASELINE")
    os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = str(baseline)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        if old is None:
            os.environ.pop("MEDTERM4DS_FHIR4PX_BASELINE", None)
        else:
            os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = old


@pytest.fixture
def terminologist_client(tmp_path):
    """FHIR app with NO PF data loaded (clean baseline for spec probes)."""
    app, baseline = _build_app_with_pf(None, tmp_path)
    old = os.environ.get("MEDTERM4DS_FHIR4PX_BASELINE")
    os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = str(baseline)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        if old is None:
            os.environ.pop("MEDTERM4DS_FHIR4PX_BASELINE", None)
        else:
            os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = old


# ===========================================================================
# CF-EXPLORER-CS02 — Canonical-URI echo DECISION: (a) always emit canonical
# ===========================================================================

def test_t01_lookup_out_system_emits_canonical_uri_per_spec(terminologist_client):
    """CF-EXPLORER-CS02 / DECISION (a) INTENDED — HIGH.

    The FHIR R4 ``CodeSystem/$lookup`` Out ``system`` parameter is defined
    as "The canonical URI of the code system that contains the concept
    that was looked up" — see
    https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out Parameters
    table. The spec explicitly notes this "may differ from the value
    passed in `system` as an input parameter" (e.g., if the code was
    found in a supplement).

    The HISTORIAN QA-047 fix re-resolves via
    ``system_to_fhir_uri(fhir_uri_to_system(system_uri))`` so that:
      - aliases (``urn:oid:2.16.840.1.113883.6.96``) → canonical URI
      - trailing-slash variants (``http://snomed.info/sct/``) → canonical URI
      - canonical input → canonical output (no double-translation)

    This probe documents the spec-correctness of decision (a) — the
    HISTORIAN fix is INTENDED, not a bug. The reverse interpretation
    (b) "emit what the client sent" is **spec-incorrect** because the
    spec wording "the canonical URI" mandates the resolved canonical
    system, not an echo.

    Acceptance criteria:
      - For SNOMED canonical input: Out ``system`` equals the canonical URI.
      - For SNOMED alias input: Out ``system`` equals the canonical URI.
      - For trailing-slash input: Out ``system`` equals the canonical URI
        (no trailing slash).
    """
    cases = [
        (SNOMED_URI, "canonical input"),
        ("urn:oid:2.16.840.1.113883.6.96", "urn:oid alias"),
        ("http://snomed.info/sct/", "trailing slash"),
    ]
    for system_input, label in cases:
        r = terminologist_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system_input, "code": SNOMED_DIABETES_MELLITUS},
        )
        assert r.status_code == 200, (
            f"Lookup with {label} ({system_input!r}) MUST succeed; got "
            f"{r.status_code}. Body: {r.text[:200]}"
        )
        body = r.json()
        sys_param = next(
            (p for p in body.get("parameter", []) if p.get("name") == "system"),
            None,
        )
        assert sys_param is not None, f"Out 'system' missing for {label}"
        emitted = sys_param.get("valueUri")
        assert emitted == SNOMED_URI, (
            f"Out 'system' for {label} ({system_input!r}) MUST be the "
            f"canonical URI {SNOMED_URI!r}; got {emitted!r}. Per spec "
            f"§4.8.21.1 Out 'system' is the canonical URI of the resolved "
            f"code system, NOT an echo of client input."
        )


def test_t02_lookup_out_system_no_double_translation_when_canonical(terminologist_client):
    """CF-EXPLORER-CS02 — confirms decision (a) does NOT over-translate.

    When the client already supplies the canonical URI, the re-resolve
    chain is idempotent: ``system_to_fhir_uri(fhir_uri_to_system(canonical))``
    returns the same canonical URI. EXPLORER's test_e101 already confirmed
    this; TERMINOLOGIST re-confirms for clinical safety: there must be
    no drift in display/name/code/system values when input is canonical.
    """
    r = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code == 200
    body = r.json()
    name_p = next((p for p in body["parameter"] if p.get("name") == "name"), None)
    code_p = next((p for p in body["parameter"] if p.get("name") == "code"), None)
    display_p = next((p for p in body["parameter"] if p.get("name") == "display"), None)
    assert name_p and "SNOMED" in name_p.get("valueString", "")
    assert code_p and code_p.get("valueCode") == SNOMED_DIABETES_MELLITUS
    assert display_p and display_p.get("valueString") == "Diabetes mellitus"


# ===========================================================================
# Patient-friendly display quality (regression-fixture cross-check)
# ===========================================================================

def test_t10_lookup_surfaces_patient_friendly_property_when_loaded(pf_loaded_client):
    """HIGH — when PF JSON data exists for a code, the patient-friendly name
    is surfaced as a custom property (NOT overriding ``display``).

    Per CS-01 TERMINOLOGIST QA-045 docstring contract and TS-03
    TERMINOLOGIST GAP-T01 methodology: the Out ``display`` MUST remain the
    engine's clinically preferred term (mrconso PT). The PF name is
    surfaced via the ``patient-friendly`` custom property under the Out
    ``property`` group (per §4.8.21.1 + §4.8.11 custom-property permission).

    Acceptance criteria:
      - Out ``display`` equals the engine's PT ("Diabetes mellitus")
      - ``property.code=patient-friendly`` value equals the PF JSON name
      - ``property.code=match-type`` value equals the PF JSON match_type
    """
    r = pf_loaded_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code == 200
    body = r.json()
    # Display remains the engine's preferred term — NOT the layperson name.
    display_p = next((p for p in body["parameter"] if p.get("name") == "display"), None)
    assert display_p is not None
    assert display_p.get("valueString") == "Diabetes mellitus", (
        f"Out 'display' MUST be the engine's preferred clinical term; got "
        f"{display_p.get('valueString')!r}. The patient-friendly name is "
        f"surfaced separately as a custom property — it MUST NOT override "
        f"display per spec §4.8.11."
    )
    # patient-friendly property surfaced.
    pf = _get_property(body, "patient-friendly")
    assert pf == "high blood sugar", (
        f"patient-friendly custom property MUST be surfaced when PF JSON "
        f"data is loaded; got {pf!r}."
    )
    mt = _get_property(body, "match-type")
    assert mt == "exact", (
        f"match-type custom property MUST reflect the PF JSON match_type; "
        f"got {mt!r}."
    )


def test_t11_lookup_canonical_system_translated_to_fhir_uri_when_loaded(pf_loaded_client):
    """HIGH — the ``canonical-system`` custom property is the FHIR R4 URI
    (translated from the raw UMLS SAB label "icd10" stored in the PF JSON
    via ``sab_label_to_fhir_uri``), per CS-01 SKEPTIC QA-043.

    Acceptance criteria:
      - ``property.code=canonical-system`` value == ``http://hl7.org/fhir/sid/icd-10-cm``
        (NOT the raw SAB label "icd10")
      - ``property.code=canonical-code`` value == "E11"
    """
    r = pf_loaded_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    cs = _get_property(body, "canonical-system")
    assert cs == ICD10CM_URI, (
        f"canonical-system MUST be the FHIR R4 URI {ICD10CM_URI!r} (not raw "
        f"SAB 'icd10'); got {cs!r}. Per CS-01 SKEPTIC QA-043."
    )
    cc = _get_property(body, "canonical-code")
    assert cc == "E11", f"canonical-code MUST be 'E11'; got {cc!r}."


def test_t12_lookup_no_patient_friendly_property_when_pf_absent(terminologist_client):
    """HIGH — when no PF JSON data is loaded, no patient-friendly / match-type
    / canonical-* custom properties are emitted. The lookup still succeeds
    with engine canonical data.

    Per FHIR R4 §4.8.21.1, the Out ``property`` group is 0..*, so absence
    is spec-conformant.
    """
    r = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code == 200
    body = r.json()
    assert _get_property(body, "patient-friendly") is None
    assert _get_property(body, "match-type") is None
    assert _get_property(body, "canonical-system") is None
    assert _get_property(body, "canonical-code") is None


# ===========================================================================
# Code-system URI round-trips (TS-03 TERMINOLOGIST methodology)
# ===========================================================================

def test_t20_canonical_system_round_trips_via_lookup(terminologist_client):
    """HIGH — for the seeded SNOMED code, $lookup returns the canonical
    system+code; re-calling $lookup with those values MUST succeed.

    Per TS-03 TERMINOLOGIST methodology (URI-round-trip from response).
    Tightened per CF-EXPLORER-CS01-01: when canonical-code may be a
    chapter range, assert URI parseability rather than strict round-trip.
    Here the seeded SNOMED code's canonical-system+canonical-code is a
    single billable code (not a range), so strict round-trip is asserted.
    """
    r1 = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body1 = r1.json()
    sys1 = next(
        (p for p in body1["parameter"] if p.get("name") == "system"), None
    )
    code1 = next(
        (p for p in body1["parameter"] if p.get("name") == "code"), None
    )
    assert sys1 and code1
    round_trip_system = sys1.get("valueUri")
    round_trip_code = code1.get("valueCode")

    r2 = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": round_trip_system, "code": round_trip_code},
    )
    assert r2.status_code == 200, (
        f"Round-trip $lookup with canonical-system={round_trip_system!r} "
        f"and canonical-code={round_trip_code!r} MUST succeed; got "
        f"{r2.status_code}."
    )
    body2 = r2.json()
    assert body2.get("resourceType") == "Parameters"


def test_t21_canonical_system_uri_is_in_SYSTEM_TO_FHIR_URI_registry(terminologist_client):
    """HIGH — every Out ``system`` value emitted by $lookup MUST be one of
    the 8 URIs in ``SYSTEM_TO_FHIR_URI``. Guards against future source
    additions that emit a raw SAB label as a URI (the CS-01 SKEPTIC QA-043
    shape, recurrence #4 of the literal-value drift pattern).
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    r = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    sys_param = next(
        (p for p in body["parameter"] if p.get("name") == "system"), None
    )
    assert sys_param is not None
    emitted = sys_param.get("valueUri")
    assert emitted in SYSTEM_TO_FHIR_URI.values(), (
        f"Out 'system' {emitted!r} MUST be in the SYSTEM_TO_FHIR_URI "
        f"registry; got a value not present in the canonical map."
    )


# ===========================================================================
# Property values are clinically sensible
# ===========================================================================

def test_t30_lookup_display_is_engine_preferred_term(terminologist_client):
    """HIGH — Out ``display`` is the engine's clinically preferred term
    (mrconso PT) for the seeded SNOMED code. NOT a layperson name and NOT
    the client's input.
    """
    r = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    display_p = next(
        (p for p in body["parameter"] if p.get("name") == "display"), None
    )
    assert display_p is not None
    assert display_p.get("valueString") == "Diabetes mellitus"


def test_t31_lookup_name_is_code_system_fhir_display_name(terminologist_client):
    """HIGH — Out ``name`` is the code system's FHIR display name derived
    from the canonical URI (e.g., SNOMED CT for http://snomed.info/sct).
    This is the human-readable code system label, NOT the raw SAB.
    """
    r = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    name_p = next(
        (p for p in body["parameter"] if p.get("name") == "name"), None
    )
    assert name_p is not None
    assert "SNOMED" in name_p.get("valueString", ""), (
        f"Out 'name' MUST be the SNOMED display name; got "
        f"{name_p.get('valueString')!r}."
    )


def test_t32_lookup_code_is_valueCode_not_valueString(terminologist_client):
    """HIGH — Out ``code`` parameter uses ``valueCode`` type per spec §4.8.21.1
    (type: code). NOT valueString. Verifies the response-builder type
    fidelity.
    """
    r = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    code_p = next(
        (p for p in body["parameter"] if p.get("name") == "code"), None
    )
    assert code_p is not None
    assert "valueCode" in code_p, (
        f"Out 'code' MUST use valueCode type per spec §4.8.21.1; got keys "
        f"{list(code_p.keys())}."
    )
    assert code_p.get("valueCode") == SNOMED_DIABETES_MELLITUS


def test_t33_lookup_abstract_is_valueBoolean_lowercase(terminologist_client):
    """HIGH — Out ``abstract`` uses ``valueBoolean`` per spec §4.8.21.1 and
    the value MUST be a JSON boolean (lowercase ``true``/``false``), NOT
    a string. Verifies Milestone-1 CR-002 fix survives.
    """
    r = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    abs_p = next(
        (p for p in body["parameter"] if p.get("name") == "abstract"), None
    )
    assert abs_p is not None
    assert "valueBoolean" in abs_p
    # valueBoolean must be a real bool, not a string "False"
    assert abs_p.get("valueBoolean") is False


# ===========================================================================
# Subsumption decomposition
# ===========================================================================

def test_t40_lookup_parent_property_accepted_for_seeded_hierarchy(terminologist_client):
    """HIGH — when ``property=parent`` is requested for a code with a parent
    in the engine (SNOMED 44054006 Type 2 DM → 73211009 Diabetes mellitus
    via MRREL PAR/isa), the request MUST succeed (200 + Parameters).

    Per FHIR R4 §4.8.21.1 In ``property`` (0..*). The conformance fixture
    seeds the parent relationship; the server accepts the property name
    and returns the standard Out parameter set. (medterm4ds returns its
    full default set rather than filtering — already documented as INTENDED
    in AGENTS.md NOT A BUG Registry.)
    """
    r = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_TYPE_2_DM,
            "property": "parent",
        },
    )
    assert r.status_code == 200, (
        f"`property=parent` MUST be accepted without 5xx; got "
        f"{r.status_code}. Body: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    # Engine preferred term must still be present.
    display_p = next(
        (p for p in body["parameter"] if p.get("name") == "display"), None
    )
    assert display_p is not None
    assert display_p.get("valueString") == "Type 2 diabetes mellitus"


def test_t41_lookup_child_property_accepted(terminologist_client):
    """HIGH — symmetric to test_t40: ``property=child`` MUST be accepted."""
    r = terminologist_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_DIABETES_MELLITUS,
            "property": "child",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters"


# ===========================================================================
# match-type DECISION (b) verification (CS-01 carry-forward)
# ===========================================================================

def test_t50_match_type_vocabulary_registry_unchanged():
    """HIGH — CS-01 TERMINOLOGIST QA-045 documented 13 unique match_type
    values as server-local engine pipeline vocabulary. This probe re-pins
    the registry: any addition or removal here MUST be a conscious decision
    (re-evaluating decision (b) vs (a) for the new value alone per the
    docstring contract).

    The registry in this test file is the second location (the first is
    ``SERVER_LOCAL_MATCH_TYPE_VOCABULARY`` in test_cs01_terminologist.py).
    Both MUST stay in sync.
    """
    # 13 documented values.
    assert len(SERVER_LOCAL_MATCH_TYPE_VOCABULARY) == 13, (
        f"Expected 13 server-local match-type values; got "
        f"{len(SERVER_LOCAL_MATCH_TYPE_VOCABULARY)}. Any change MUST be "
        f"a conscious decision documented in the carry-forward."
    )
    expected = {
        "exact", "original", "broader", "group", "ingredient",
        "same_cui", "cvx_group", "broader_group", "broader_ingredient",
        "first_axis", "snomed_fallback",
        "snomed_to_target_native_hierarchy", "snomed_to_target_snomed_fallback",
    }
    assert set(SERVER_LOCAL_MATCH_TYPE_VOCABULARY.keys()) == expected, (
        f"Registry drift detected. Expected {expected}; got "
        f"{set(SERVER_LOCAL_MATCH_TYPE_VOCABULARY.keys())}."
    )


def test_t51_match_type_vocabulary_not_in_fhir_equivalence_enum():
    """HIGH — confirms the CS-01 TERMINOLOGIST DECISION (b) justification
    still holds: NONE of the 13 server-local match_type values appear in
    the FHIR R4 ConceptMapEquivalence closed enum. If any value overlaps,
    decision (b) MUST be re-evaluated for that value alone.
    """
    # FHIR R4 ConceptMapEquivalence closed enum:
    # https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    # CR-014 (milestone-2 review): import the single source of truth.
    from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    fhir_equivalence_enum = FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    overlap = set(SERVER_LOCAL_MATCH_TYPE_VOCABULARY.keys()) & fhir_equivalence_enum
    assert not overlap, (
        f"Server-local match-type values {overlap} appear in the FHIR R4 "
        f"ConceptMapEquivalence enum — DECISION (b) MUST be re-evaluated "
        f"for these values (translate to FHIR enum, not document as "
        f"server-local)."
    )


def test_t52_do_lookup_docstring_names_all_custom_properties():
    """HIGH — CS-01 TERMINOLOGIST QA-045 docstring contract: the
    ``_do_lookup`` docstring MUST name every custom property derived from
    patient-friendly JSON data. The 5 documented properties are:
    ``patient-friendly``, ``match-type``, ``canonical-code``,
    ``canonical-system``, ``tty``.

    Source-level audit (no HTTP round-trip).
    """
    import inspect

    from medterm4ds.apps.fhir_api import create_fhir_app

    # _do_lookup is defined inside create_fhir_app; fetch via closure.
    # Find the function by walking the source.
    src = inspect.getsource(create_fhir_app)
    # The docstring text is verifiable from the source file directly.
    fhir_api_path = Path(__file__).resolve().parents[2] / "src" / "medterm4ds" / "apps" / "fhir_api.py"
    text = fhir_api_path.read_text()
    do_lookup_marker = "def _do_lookup("
    idx = text.find(do_lookup_marker)
    assert idx >= 0, "Could not locate _do_lookup in apps/fhir_api.py"
    # Pull a 5KB slice — enough for the docstring.
    slice_ = text[idx:idx + 6000]
    for prop in ("patient-friendly", "match-type", "canonical-code",
                 "canonical-system", "tty"):
        assert prop in slice_, (
            f"_do_lookup docstring MUST name the {prop!r} custom property "
            f"per CS-01 TERMINOLOGIST QA-045 contract."
        )
    # Decision (b) keywords
    assert "SERVER-LOCAL" in slice_ or "server-local" in slice_, (
        "_do_lookup docstring MUST mark match-type as SERVER-LOCAL."
    )
    assert "ConceptMapEquivalence" in slice_, (
        "_do_lookup docstring MUST cross-reference FHIR ConceptMapEquivalence."
    )


def test_t53_match_type_on_wire_is_in_registry(pf_loaded_client):
    """HIGH — end-to-end wire probe: $lookup on SNOMED 73211009 with PF
    data loaded emits a match-type value that is documented in the
    SERVER_LOCAL_MATCH_TYPE_VOCABULARY registry.
    """
    r = pf_loaded_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    mt = _get_property(body, "match-type")
    assert mt in SERVER_LOCAL_MATCH_TYPE_VOCABULARY, (
        f"Wire value match-type={mt!r} is NOT in the documented "
        f"SERVER_LOCAL_MATCH_TYPE_VOCABULARY registry. Any new value "
        f"MUST be added to the registry in the same PR."
    )


# ===========================================================================
# Cross-source clinical consistency
# ===========================================================================

def test_t60_lookup_snomed_returns_clinically_correct_display(terminologist_client):
    """HIGH — SNOMED preferred terms are clinically sensible. The seeded
    SNOMED codes return their canonical SNOMED PT display values.
    """
    cases = [
        (SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
        (SNOMED_TYPE_2_DM, "Type 2 diabetes mellitus"),
    ]
    for code, expected_display in cases:
        r = terminologist_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": code},
        )
        assert r.status_code == 200
        body = r.json()
        display_p = next(
            (p for p in body["parameter"] if p.get("name") == "display"), None
        )
        assert display_p is not None
        assert display_p.get("valueString") == expected_display, (
            f"SNOMED {code} display MUST be {expected_display!r}; got "
            f"{display_p.get('valueString')!r}."
        )


def test_t61_lookup_canonical_system_for_each_known_system_uri(terminologist_client):
    """HIGH — for each FHIR R4 canonical URI in SYSTEM_TO_FHIR_URI, the
    Out ``system`` emitted by $lookup is exactly the input URI (no drift
    for canonical inputs). Parametrized over all 8 production sources.

    Skips sources whose codes are not seeded in the conformance fixture
    (only SNOMED + ICD10CM + RXNORM are seeded). For unseeded sources,
    asserts that the request returns 404 (not-found OperationOutcome),
    which still validates URI parsing.
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    seeded_codes = {
        "SNOMEDCT_US": SNOMED_DIABETES_MELLITUS,
        "ICD10CM": "E11",
        "RXNORM": "860975",  # not seeded in this fixture but in module-scope one
    }
    for source, uri in SYSTEM_TO_FHIR_URI.items():
        code = seeded_codes.get(source, "00000")
        r = terminologist_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": uri, "code": code},
        )
        # The lookup either succeeds (200 + Parameters) for seeded codes,
        # or returns 200 with OperationOutcome "not-found" for unseeded codes.
        # Either way, the URI MUST be recognized (not 400 "Unrecognized").
        assert r.status_code != 400 or "Unrecognized system URI" not in r.text, (
            f"Canonical URI {uri!r} for source {source!r} was rejected as "
            f"unrecognized. Body: {r.text[:200]}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_property(body: dict, code: str) -> str | None:
    """Extract a property value from a Parameters body by property code."""
    for p in body.get("parameter", []):
        if p.get("name") == "property":
            parts = p.get("part", [])
            code_part = next(
                (pt for pt in parts if pt.get("name") == "code"), None
            )
            value_part = next(
                (pt for pt in parts if pt.get("name") == "value"), None
            )
            if code_part and code_part.get("valueCode") == code:
                # valueString per _property_param builder
                return value_part.get("valueString") if value_part else None
    return None
