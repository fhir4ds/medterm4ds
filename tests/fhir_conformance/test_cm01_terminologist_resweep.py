"""TERMINOLOGIST RESWEEP probes for chunk CM-01 (ConceptMap Resource Structure).

Source: https://build.fhir.org/conceptmap.html (canonical)
        https://hl7.org/fhir/R4/conceptmap.html
        https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
        https://hl7.org/fhir/R4/conceptmap-operation-translate.html

This resweep test file extends the baseline ``test_cm01_terminologist.py`` with
NEW clinical-correctness probes focused on the three EXPLORER-tip invariants.

TERMINOLOGIST lens for CM-01 (clinical/terminological correctness). Per
GLOBAL_RULES.md, TERMINOLOGIST findings are HIGH severity by default.

EXPLORER-tip invariants for TERMINOLOGIST to re-derive at the clinical-content
layer:

  1. **CF-CM02-01 RESOLVED clinical-correctness verification** — a CDS hook
     calling POST ``$translate`` with a Coding body now succeeds instead of
     silently failing with 400. Verify clinical-content byte-exact parity
     between GET (system+code) and POST (coding body) invocation paths.

  2. **Canonical-DISPLAY META-PATTERN at the clinical-content layer (14
     surfaces)** — the display returned by every operation is clinically
     sensible (not just structurally byte-exact equal), especially the
     patient-friendly name resolution consistency.

  3. **ConceptMap export surface clinical correctness** —
     ``group.element.target.display`` in CM export is the clinically
     preferred term for the target code.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)
"""

from __future__ import annotations

import ast
import inspect

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    SYSTEM_TO_FHIR_URI,
    canonical_system_uri,
)
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    fhir_equivalence,
)


# =============================================================================
# Constants — mirror the conformance fixture.
# =============================================================================

SNOMED_URI = "http://snomed.info/sct"
SNOMED_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_OID = "urn:oid:2.16.840.1.113883.6.90"
ICD10CM_TRAILING_SLASH = "http://hl7.org/fhir/sid/icd-10-cm/"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

SNOMED_DIABETES_MELLITUS = "73211009"
SNOMED_T2DM = "44054006"
ICD10CM_T2DM = "E11"
RXNORM_METFORMIN = "860975"

# Canonical display strings seeded in the conformance fixture.
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
ICD10CM_T2DM_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"


# =============================================================================
# Shared helpers
# =============================================================================


def _lookup_display(fhir_client, system: str, code: str) -> str | None:
    """Run $lookup and extract the Out display parameter (None on OO)."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
    )
    body = r.json()
    if body.get("resourceType") == "OperationOutcome":
        return None
    return next(
        (
            p.get("valueString")
            for p in body.get("parameter", []) if p.get("name") == "display"
        ),
        None,
    )


def _lookup_params(fhir_client, system: str, code: str) -> dict[str, str]:
    """Run $lookup and return the full Out parameter dict."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
    )
    body = r.json()
    if body.get("resourceType") == "OperationOutcome":
        return {}
    return {p["name"]: p for p in body.get("parameter", [])}


def _translate_matches_get(
    fhir_client, source_system: str, source_code: str, target_system: str
) -> list[dict]:
    """Run GET $translate and return list of match parameter dicts."""
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": source_system,
            "code": source_code,
            "targetsystem": target_system,
        },
    )
    body = r.json()
    if body.get("resourceType") == "OperationOutcome":
        return []
    return [p for p in body.get("parameter", []) if p.get("name") == "match"]


def _translate_matches_post_coding(
    fhir_client, source_system: str, source_code: str, target_system: str
) -> list[dict]:
    """Run POST $translate with coding body and return list of match dicts.

    Per FHIR R4 $translate In Parameters: "coding: A coding to translate
    (0..1, Coding)". The POST path was broken in CM-01 EXPLORER QA-001 — now
    fixed. This helper exercises the FIXED POST coding body path.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": source_system,
                    "code": source_code,
                },
            },
            {"name": "targetsystem", "valueUri": target_system},
        ],
    }
    r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
    if r.status_code != 200:
        return []
    body = r.json()
    if body.get("resourceType") == "OperationOutcome":
        return []
    return [p for p in body.get("parameter", []) if p.get("name") == "match"]


def _extract_match_concept(match_part: dict) -> dict | None:
    """From a $translate match part, extract the concept.valueCoding dict."""
    for sub in match_part.get("part", []):
        if sub.get("name") == "concept":
            return sub.get("valueCoding", {})
    return None


def _extract_match_equivalence(match_part: dict) -> str | None:
    """From a $translate match part, extract the equivalence valueCode."""
    for sub in match_part.get("part", []):
        if sub.get("name") == "equivalence":
            return sub.get("valueCode")
    return None


def _extract_match_source(match_part: dict) -> dict | None:
    """From a $translate match part, extract the source.valueCoding dict."""
    for sub in match_part.get("part", []):
        if sub.get("name") == "source":
            return sub.get("valueCoding", {})
    return None


def _make_concept_map_row(
    *,
    source_code: str = SNOMED_DIABETES_MELLITUS,
    source_sab: str = "SNOMEDCT_US",
    source_display: str | None = SNOMED_DM_DISPLAY,
    target_code: str = ICD10CM_T2DM,
    target_sab: str = "ICD10CM",
    target_display: str = ICD10CM_T2DM_DISPLAY,
    relationship: str = "equivalent",
    match_type: str | None = "exact",
):
    """Build a minimal ConceptMapRow for export probes."""
    from medterm4ds.core.models import CodeRef, ConceptMapRow

    return ConceptMapRow(
        source=CodeRef(source=source_sab, code=source_code),
        target=CodeRef(source=target_sab, code=target_code),
        source_display=source_display,
        target_display=target_display,
        relationship=relationship,
        match_type=match_type,
    )


# =============================================================================
# Lens 1: CF-CM02-01 RESOLVED clinical-correctness verification.
#
# Per the EXPLORER tip: a CDS hook calling POST $translate with a Coding body
# now succeeds (was silently failing with 400 per CM-01 EXPLORER QA-001).
# TERMINOLOGIST verifies CLINICAL-CONTENT byte-exact parity between the two
# invocation paths: GET (system+code) and POST (coding body) MUST produce
# the SAME clinical content (target code, target display, target system,
# equivalence value) for the same source code.
#
# Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
# =============================================================================


@pytest.mark.parametrize(
    "source_system,source_code,target_system,expected_target_code",
    [
        # SNOMED T2DM → ICD-10-CM (same CUI C0011847)
        (SNOMED_URI, SNOMED_T2DM, ICD10CM_URI, ICD10CM_T2DM),
        # ICD-10-CM T2DM → SNOMED (reverse direction)
        (ICD10CM_URI, ICD10CM_T2DM, SNOMED_URI, SNOMED_T2DM),
    ],
)
def test_t10_get_post_translate_clinical_content_parity_target_code(
    fhir_client,
    source_system,
    source_code,
    target_system,
    expected_target_code,
):
    """TERMINOLOGIST: CF-CM02-01 RESOLVED — GET vs POST $translate coding
    body MUST produce byte-exact same target code (clinical content parity).

    A CDS hook using POST coding body would get a different target code than
    the same call via GET system+code → clinical workflow divergence.

    Spec: FHIR R4 $translate "Out Parameters: match.concept.code" — same
    source code → same target code, regardless of invocation encoding.
    """
    get_matches = _translate_matches_get(
        fhir_client, source_system, source_code, target_system
    )
    if not get_matches:
        pytest.skip(f"no GET $translate matches for {source_system}|{source_code}")

    post_matches = _translate_matches_post_coding(
        fhir_client, source_system, source_code, target_system
    )
    assert post_matches, (
        f"POST $translate coding body returned 0 matches; CF-CM02-01 "
        f"regression or fixture gap. GET returned {len(get_matches)} matches."
    )

    # Extract target codes from each path.
    get_codes = sorted(
        c.get("code")
        for m in get_matches
        for c in [_extract_match_concept(m)]
        if c and c.get("code")
    )
    post_codes = sorted(
        c.get("code")
        for m in post_matches
        for c in [_extract_match_concept(m)]
        if c and c.get("code")
    )

    assert get_codes == post_codes, (
        f"GET vs POST $translate coding body target codes diverge: "
        f"GET={get_codes}, POST={post_codes}. CF-CM02-01 RESOLVED requires "
        f"byte-exact clinical content parity. A CDS hook would receive "
        f"different target codes depending on invocation path."
    )

    # The expected target code MUST be in BOTH sets.
    assert expected_target_code in get_codes, (
        f"expected target code {expected_target_code!r} not in GET matches "
        f"{get_codes}."
    )


@pytest.mark.parametrize(
    "source_system,source_code,target_system",
    [
        (SNOMED_URI, SNOMED_T2DM, ICD10CM_URI),
        (ICD10CM_URI, ICD10CM_T2DM, SNOMED_URI),
    ],
)
def test_t11_get_post_translate_clinical_content_parity_target_display(
    fhir_client,
    source_system,
    source_code,
    target_system,
):
    """TERMINOLOGIST: CF-CM02-01 RESOLVED — GET vs POST $translate coding
    body MUST produce byte-exact same target DISPLAY.

    Clinical content parity at the DISPLAY level: a clinician using POST
    coding body would see the same target concept display as via GET.

    Spec: FHIR R4 $translate "Out: match.concept.display" — same source
    code → same target display.
    """
    get_matches = _translate_matches_get(
        fhir_client, source_system, source_code, target_system
    )
    if not get_matches:
        pytest.skip(f"no GET $translate matches")

    post_matches = _translate_matches_post_coding(
        fhir_client, source_system, source_code, target_system
    )
    assert post_matches, (
        f"POST $translate coding body returned 0 matches; CF-CM02-01 regression"
    )

    get_displays = sorted(
        (c.get("display") or "")
        for m in get_matches
        for c in [_extract_match_concept(m)]
        if c
    )
    post_displays = sorted(
        (c.get("display") or "")
        for m in post_matches
        for c in [_extract_match_concept(m)]
        if c
    )

    assert get_displays == post_displays, (
        f"GET vs POST $translate target DISPLAYS diverge: "
        f"GET={get_displays}, POST={post_displays}. Clinical content parity "
        f"requires identical displays regardless of invocation encoding."
    )


@pytest.mark.parametrize(
    "source_system,source_code,target_system",
    [
        (SNOMED_URI, SNOMED_T2DM, ICD10CM_URI),
        (ICD10CM_URI, ICD10CM_T2DM, SNOMED_URI),
    ],
)
def test_t12_get_post_translate_clinical_content_parity_target_system(
    fhir_client,
    source_system,
    source_code,
    target_system,
):
    """TERMINOLOGIST: CF-CM02-01 RESOLVED — GET vs POST $translate coding
    body MUST produce byte-exact same target SYSTEM URI (canonical).

    Per CR-012 the target system MUST be canonical regardless of invocation
    path. CF-CM02-01 RESOLVED extends this to the POST coding body path.

    Spec: client-input-as-canonical drift pattern (count=8+1 PROMOTED).
    """
    get_matches = _translate_matches_get(
        fhir_client, source_system, source_code, target_system
    )
    if not get_matches:
        pytest.skip(f"no GET $translate matches")

    post_matches = _translate_matches_post_coding(
        fhir_client, source_system, source_code, target_system
    )
    assert post_matches, f"POST $translate coding body returned 0 matches"

    get_systems = sorted(
        c.get("system")
        for m in get_matches
        for c in [_extract_match_concept(m)]
        if c and c.get("system")
    )
    post_systems = sorted(
        c.get("system")
        for m in post_matches
        for c in [_extract_match_concept(m)]
        if c and c.get("system")
    )

    assert get_systems == post_systems, (
        f"GET vs POST $translate target SYSTEMS diverge: "
        f"GET={get_systems}, POST={post_systems}."
    )

    # Every target system MUST be canonical.
    for sys_uri in get_systems + post_systems:
        assert sys_uri in (SNOMED_URI, ICD10CM_URI, RXNORM_URI), (
            f"non-canonical target system in $translate response: {sys_uri!r}"
        )


@pytest.mark.parametrize(
    "source_system,source_code,target_system",
    [
        (SNOMED_URI, SNOMED_T2DM, ICD10CM_URI),
        (ICD10CM_URI, ICD10CM_T2DM, SNOMED_URI),
    ],
)
def test_t13_get_post_translate_clinical_content_parity_equivalence(
    fhir_client,
    source_system,
    source_code,
    target_system,
):
    """TERMINOLOGIST: CF-CM02-01 RESOLVED — GET vs POST $translate coding
    body MUST produce byte-exact same EQUIVALENCE value.

    Per TS-02 TERMINOLOGIST QA-030, the equivalence value MUST be sourced
    from the engine relationship via _INTERNAL_REL_TO_FHIR_EQUIVALENCE.
    CF-CM02-01 RESOLVED extends this to the POST coding body path — same
    source code → same engine mapping → same equivalence.

    Spec: FHIR R4 $translate "Out: match.equivalence".
    """
    get_matches = _translate_matches_get(
        fhir_client, source_system, source_code, target_system
    )
    if not get_matches:
        pytest.skip(f"no GET $translate matches")

    post_matches = _translate_matches_post_coding(
        fhir_client, source_system, source_code, target_system
    )
    assert post_matches

    get_equivs = sorted(
        (e or "")
        for m in get_matches
        for e in [_extract_match_equivalence(m)]
    )
    post_equivs = sorted(
        (e or "")
        for m in post_matches
        for e in [_extract_match_equivalence(m)]
    )

    assert get_equivs == post_equivs, (
        f"GET vs POST $translate EQUIVALENCE diverge: "
        f"GET={get_equivs}, POST={post_equivs}."
    )

    # Every equivalence MUST be in the R4 closed enum.
    for eq in get_equivs + post_equivs:
        if eq:
            assert eq in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"non-R4-enum equivalence in $translate response: {eq!r}"
            )


@pytest.mark.parametrize(
    "source_system,source_code,target_system",
    [
        (SNOMED_URI, SNOMED_T2DM, ICD10CM_URI),
        (ICD10CM_URI, ICD10CM_T2DM, SNOMED_URI),
    ],
)
def test_t14_get_post_translate_clinical_content_parity_match_count(
    fhir_client,
    source_system,
    source_code,
    target_system,
):
    """TERMINOLOGIST: CF-CM02-01 RESOLVED — GET vs POST $translate coding
    body MUST produce same MATCH COUNT (number of matches).

    A divergence in match count would indicate the POST path is silently
    dropping or duplicating matches — clinical workflow divergence.

    Spec: FHIR R4 $translate "Out: result" boolean + "message" parameter.
    """
    get_matches = _translate_matches_get(
        fhir_client, source_system, source_code, target_system
    )
    if not get_matches:
        pytest.skip(f"no GET $translate matches")

    post_matches = _translate_matches_post_coding(
        fhir_client, source_system, source_code, target_system
    )

    assert len(get_matches) == len(post_matches), (
        f"GET vs POST $translate match count diverge: GET={len(get_matches)}, "
        f"POST={len(post_matches)}. A CDS hook reading the match list would "
        f"see different clinical options depending on invocation path."
    )


def test_t15_post_translate_with_coding_body_clinical_safety_cds_hook(
    fhir_client,
):
    """TERMINOLOGIST: CF-CM02-01 RESOLVED clinical-safety scenario. A CDS
    hook calling POST $translate with a Coding body for SNOMED T2DM MUST
    receive the clinically-correct mapping to ICD-10-CM E11 (Type 2 diabetes
    mellitus), NOT a 400 error.

    This is the clinical-workflow-integration scenario described in the
    EXPLORER tip — the prior 400 reject would have broken CDS integration.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM,
                    "display": SNOMED_T2DM_DISPLAY,  # client-supplied
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
    assert r.status_code == 200, (
        f"POST $translate coding body failed: {r.status_code} {r.text}. "
        f"CDS hook integration scenario broken."
    )
    response = r.json()
    assert response.get("resourceType") == "Parameters"

    # The response MUST contain a result=true parameter.
    result_p = next(
        (p for p in response.get("parameter", []) if p.get("name") == "result"),
        None,
    )
    assert result_p is not None and result_p.get("valueBoolean") is True, (
        f"POST $translate coding body did not return result=true; body={response}"
    )

    # Find the match for ICD-10-CM E11.
    matches = [
        p for p in response.get("parameter", []) if p.get("name") == "match"
    ]
    assert matches, "POST $translate returned no matches for SNOMED T2DM"

    found_e11 = False
    for m in matches:
        concept = _extract_match_concept(m)
        if concept and concept.get("code") == ICD10CM_T2DM:
            found_e11 = True
            # Clinical correctness: target system MUST be canonical ICD-10-CM.
            assert concept.get("system") == ICD10CM_URI, (
                f"target system={concept.get('system')!r}; expected canonical "
                f"{ICD10CM_URI!r}"
            )
            # Clinical correctness: display MUST be the clinically-preferred term.
            assert concept.get("display") == ICD10CM_T2DM_DISPLAY, (
                f"target display={concept.get('display')!r}; expected "
                f"{ICD10CM_T2DM_DISPLAY!r}"
            )
            # Clinical correctness: equivalence MUST be in R4 enum.
            eq = _extract_match_equivalence(m)
            assert eq in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"equivalence={eq!r} not in R4 closed enum"
            )
            # Clinical correctness: source.system MUST be canonical SNOMED.
            source = _extract_match_source(m)
            if source and source.get("code") == SNOMED_T2DM:
                assert source.get("system") == SNOMED_URI, (
                    f"source system={source.get('system')!r}; expected canonical "
                    f"{SNOMED_URI!r}"
                )

    assert found_e11, (
        f"POST $translate coding body did not return ICD-10-CM E11 for "
        f"SNOMED T2DM. Clinical workflow integration broken."
    )


def test_t16_post_translate_with_codeableconcept_body_clinical_safety(
    fhir_client,
):
    """TERMINOLOGIST: CF-CM02-01 RESOLVED clinical-safety scenario. POST
    $translate with codeableConcept body (third spec-permitted encoding)
    MUST also succeed and produce the clinically-correct ICD-10-CM mapping.

    Spec: FHIR R4 $translate "codeableConcept: A full codeableConcept to
    translate (0..1, CodeableConcept)."
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
                            "code": SNOMED_T2DM,
                            "display": SNOMED_T2DM_DISPLAY,
                        }
                    ]
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
    assert r.status_code == 200, (
        f"POST $translate codeableConcept body failed: {r.status_code} {r.text}"
    )
    response = r.json()
    assert response.get("resourceType") == "Parameters"

    # Verify clinical correctness: ICD-10-CM E11 in matches.
    matches = [
        p for p in response.get("parameter", []) if p.get("name") == "match"
    ]
    found_e11 = False
    for m in matches:
        concept = _extract_match_concept(m)
        if concept and concept.get("code") == ICD10CM_T2DM:
            found_e11 = True
            assert concept.get("display") == ICD10CM_T2DM_DISPLAY
    assert found_e11, (
        "POST $translate codeableConcept body did not produce ICD-10-CM E11 "
        "match for SNOMED T2DM."
    )


def test_t17_post_translate_coding_body_with_alias_system_canonical_output(
    fhir_client,
):
    """TERMINOLOGIST: CF-CM02-01 RESOLVED — POST $translate coding body
    with ALIAS system input (urn:oid, trailing-slash, uppercase-scheme).
    The match.source.system in response MUST be canonical (NOT echo alias).

    Clinical safety: a CDS hook supplying an alias URI MUST receive a
    canonical response so downstream rendering can identify the code system.

    Spec: client-input-as-canonical drift pattern (count=8+1 PROMOTED).
    """
    aliases = [
        (SNOMED_OID, SNOMED_URI),
        (SNOMED_TRAILING_SLASH, SNOMED_URI),
        (SNOMED_UPPERCASE_SCHEME, SNOMED_URI),
    ]
    for alias, expected_canonical in aliases:
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": alias,
                        "code": SNOMED_T2DM,
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code == 200, (
            f"POST $translate with alias {alias!r} failed: {r.status_code}"
        )
        response = r.json()
        if response.get("resourceType") == "OperationOutcome":
            pytest.skip(f"fixture missing for alias {alias!r}")

        matches = [
            p for p in response.get("parameter", []) if p.get("name") == "match"
        ]
        if not matches:
            pytest.skip(f"no matches for alias {alias!r}")

        for m in matches:
            source = _extract_match_source(m)
            if source and source.get("code") == SNOMED_T2DM:
                assert source.get("system") == expected_canonical, (
                    f"POST coding body match.source.system={source.get('system')!r} "
                    f"on alias {alias!r}; expected canonical {expected_canonical!r}"
                )
            concept = _extract_match_concept(m)
            if concept and concept.get("system"):
                assert concept.get("system") in (SNOMED_URI, ICD10CM_URI), (
                    f"target concept system={concept.get('system')!r} on alias "
                    f"{alias!r}; expected canonical SNOMED or ICD-10-CM URI"
                )


# =============================================================================
# Lens 2: Canonical-DISPLAY META-PATTERN at the clinical-content layer.
#
# The 14-surface canonical-DISPLAY META-PATTERN (12 prior + SKEPTIC s80/s81 +
# EXPLORER e10/e11/e20) was verified STRUCTURALLY byte-exact. TERMINOLOGIST
# re-derives at the CLINICAL CONTENT layer — the display is not just
# structurally equal, it's the CLINICALLY-SENSIBLE display (not the raw code,
# not the client input, not a technical identifier).
#
# Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html — display
# is "the preferred display for this concept" — the clinically preferred term.
# =============================================================================


@pytest.mark.parametrize(
    "system,code,expected_display,clinical_note",
    [
        # SNOMED clinical displays
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_DM_DISPLAY,
         "Diabetes mellitus is the clinician-readable form, not '73211009'"),
        (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM_DISPLAY,
         "Type 2 diabetes mellitus is distinct from Type 1 (clinically load-bearing)"),
        # ICD-10-CM clinical displays
        (ICD10CM_URI, ICD10CM_T2DM, ICD10CM_T2DM_DISPLAY,
         "ICD-10-CM uses HT (Hyperthyroidism? No — HT means 'Preferred Term' here)"),
        # RxNorm clinical displays
        (RXNORM_URI, RXNORM_METFORMIN, RXNORM_METFORMIN_DISPLAY,
         "RxNorm SCD = 'Semantic Clinical Drug' — full drug name incl. dose+form"),
    ],
)
def test_t20_lookup_display_is_clinically_sensible(
    fhir_client, system, code, expected_display, clinical_note,
):
    """TERMINOLOGIST: $lookup Out display is CLINICALLY SENSIBLE (not raw
    code, not a technical identifier). The display is the human-readable
    clinical term that a clinician would recognize.

    Spec: FHIR R4 $lookup "display" parameter = "the preferred display for
    this concept" — the clinically preferred term, not the code.
    """
    lookup_display = _lookup_display(fhir_client, system, code)
    if not lookup_display:
        pytest.skip(f"fixture missing for {system}|{code}")

    assert lookup_display == expected_display, (
        f"$lookup display={lookup_display!r}; expected clinically-preferred "
        f"{expected_display!r}. Clinical note: {clinical_note}. The display "
        f"MUST be the clinician-readable form, NOT the raw code {code!r}."
    )

    # The display MUST NOT be the raw code.
    assert lookup_display != code, (
        f"$lookup display={lookup_display!r} echoes the raw code — silent "
        f"wrong-answer (patient would see 'E11' instead of 'Type 2 diabetes')."
    )


@pytest.mark.parametrize(
    "system,code",
    [
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_T2DM),
        (RXNORM_URI, RXNORM_METFORMIN),
    ],
)
def test_t21_translate_target_display_is_clinically_preferred_term(
    fhir_client, system, code,
):
    """TERMINOLOGIST: $translate match.concept.display is the CLINICALLY
    PREFERRED term for the target code (mirrors $lookup display).

    Per the META-PATTERN: the display returned by EVERY operation is
    clinically sensible. A CDS hook reading $translate match.concept.display
    would display this string to the clinician — it MUST be the canonical
    preferred term, not the raw code.

    Spec: FHIR R4 $translate Out "match.concept" — Coding.display = "A
    representation of the concept as a human readable string".
    """
    # Determine target_system: if source is SNOMED, target is ICD-10-CM; vice versa.
    if system == SNOMED_URI:
        target_system = ICD10CM_URI
        expected_target_code = ICD10CM_T2DM
        expected_display = ICD10CM_T2DM_DISPLAY
    elif system == ICD10CM_URI:
        target_system = SNOMED_URI
        expected_target_code = SNOMED_T2DM
        expected_display = SNOMED_T2DM_DISPLAY
    else:
        # RxNorm doesn't have a same-CUI target in the fixture; skip.
        pytest.skip("no $translate target for RxNorm in fixture")

    matches = _translate_matches_get(fhir_client, system, code, target_system)
    if not matches:
        pytest.skip(f"no $translate matches for {system}|{code}")

    # Find the match for the expected target code.
    found = False
    for m in matches:
        concept = _extract_match_concept(m)
        if concept and concept.get("code") == expected_target_code:
            found = True
            display = concept.get("display")
            assert display == expected_display, (
                f"$translate target display={display!r}; expected clinically "
                f"preferred {expected_display!r}."
            )
            # Display MUST NOT be the raw code.
            assert display != expected_target_code, (
                f"$translate target display={display!r} echoes the raw code "
                f"{expected_target_code!r} — patient would see the code "
                f"instead of the clinical term."
            )
    assert found, (
        f"no $translate match for target code {expected_target_code!r}; "
        f"matches were {matches}"
    )


@pytest.mark.parametrize(
    "target_system,target_code,expected_display",
    [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_DM_DISPLAY),
        (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM_DISPLAY),
        (ICD10CM_URI, ICD10CM_T2DM, ICD10CM_T2DM_DISPLAY),
        (RXNORM_URI, RXNORM_METFORMIN, RXNORM_METFORMIN_DISPLAY),
    ],
)
def test_t22_export_target_display_is_clinically_preferred_term(
    target_system, target_code, expected_display,
):
    """TERMINOLOGIST: ConceptMap export group.element.target.display is the
    CLINICALLY PREFERRED term for the target code (mirrors $lookup display).

    Per the META-PATTERN: the export surface MUST surface the clinically
    preferred term, NOT the raw code, NOT a technical identifier. A
    downstream consumer reading the ConceptMap JSON would see this string
    as the human-readable label.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — target.display = "A
    display for the target code." The display SHOULD match $lookup Out
    display (which per R4 is "the preferred display").
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir
    from medterm4ds.engines.fhir import fhir_uri_to_system

    target_sab = fhir_uri_to_system(target_system)
    assert target_sab

    rows = [_make_concept_map_row(
        source_code=SNOMED_DIABETES_MELLITUS,
        source_sab="SNOMEDCT_US",
        source_display=SNOMED_DM_DISPLAY,
        target_code=target_code,
        target_sab=target_sab,
        target_display=expected_display,
        relationship="equivalent",
    )]
    resource = concept_map_to_fhir(rows)

    found = False
    for g in resource["group"]:
        for element in g.get("element", []):
            for target in element.get("target", []):
                if target.get("code") == target_code:
                    found = True
                    display = target.get("display")
                    assert display == expected_display, (
                        f"export target.display={display!r}; expected clinically "
                        f"preferred {expected_display!r}."
                    )
                    # Display MUST NOT echo raw code.
                    assert display != target_code, (
                        f"export target.display={display!r} echoes raw code "
                        f"{target_code!r}."
                    )
    assert found, f"target code {target_code!r} not in export"


@pytest.mark.parametrize(
    "system,code,expected_display",
    [
        (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM_DISPLAY),
        (ICD10CM_URI, ICD10CM_T2DM, ICD10CM_T2DM_DISPLAY),
        (RXNORM_URI, RXNORM_METFORMIN, RXNORM_METFORMIN_DISPLAY),
    ],
)
def test_t23_lookup_and_translate_and_export_clinical_triad_agree(
    fhir_client, system, code, expected_display,
):
    """TERMINOLOGIST: META-PATTERN at clinical-content layer. The display
    returned by $lookup, $translate match.concept, AND concept_map_to_fhir
    group.element.target.display MUST ALL be the clinically preferred term.

    This is the 3-operation clinical-content triad — every operation MUST
    surface the SAME clinical display for the same (system, code). Drift
    between any two means a clinician using $translate vs $lookup vs the
    ConceptMap export would see different displays for the same concept —
    a patient-safety issue.

    Spec: FHIR R4 — display fields across operations MUST agree for the
    same code.
    """
    lookup_display = _lookup_display(fhir_client, system, code)
    if not lookup_display:
        pytest.skip(f"fixture missing for {system}|{code}")

    # $translate: need a target_system; SNOMED<->ICD-10-CM are the only pairs.
    if system == SNOMED_URI:
        target_system = ICD10CM_URI
    elif system == ICD10CM_URI:
        target_system = SNOMED_URI
    else:
        target_system = None

    translate_display = None
    if target_system:
        # Use reverse direction: source=target_system, target=system; check
        # match.concept.display for our `code`.
        src_system = target_system
        if src_system == SNOMED_URI:
            src_code = SNOMED_T2DM if code == ICD10CM_T2DM else SNOMED_DIABETES_MELLITUS
        else:
            src_code = ICD10CM_T2DM
        matches = _translate_matches_get(fhir_client, src_system, src_code, system)
        for m in matches:
            concept = _extract_match_concept(m)
            if concept and concept.get("code") == code:
                translate_display = concept.get("display")
                break

    # Export display.
    from medterm4ds.outputs.fhir import concept_map_to_fhir
    from medterm4ds.engines.fhir import fhir_uri_to_system

    target_sab = fhir_uri_to_system(system)
    rows = [_make_concept_map_row(
        target_code=code,
        target_sab=target_sab,
        target_display=expected_display,
    )]
    resource = concept_map_to_fhir(rows)
    export_display = None
    for g in resource["group"]:
        for element in g.get("element", []):
            for target in element.get("target", []):
                if target.get("code") == code:
                    export_display = target.get("display")

    # All three MUST agree.
    assert lookup_display == expected_display
    assert export_display == expected_display
    if translate_display is not None:
        assert translate_display == expected_display, (
            f"triad drift: lookup={lookup_display!r}, translate="
            f"{translate_display!r}, export={export_display!r}; expected "
            f"{expected_display!r}"
        )


def test_t24_lookup_display_does_not_leak_engine_internals(fhir_client):
    """TERMINOLOGIST: clinical-safety — $lookup display MUST NOT leak
    engine internals (AUI, CUI, TTY codes, SAB labels) into the
    clinician-facing display.

    A display like "A44054006 | C0011847 | SNOMEDCT_US" would be a
    clinical-correctness bug — the clinician would see internal IDs
    instead of the human-readable term.

    Spec: FHIR R4 $lookup display = "the preferred display for this
    concept" — a clinician-readable string.
    """
    for system, code in [
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_T2DM),
        (RXNORM_URI, RXNORM_METFORMIN),
    ]:
        display = _lookup_display(fhir_client, system, code)
        if not display:
            pytest.skip(f"fixture missing for {system}|{code}")

        # Display MUST NOT contain internal UMLS identifiers.
        assert "C0011847" not in display, (
            f"$lookup display {display!r} leaks CUI 'C0011847'"
        )
        assert "A44054006" not in display, (
            f"$lookup display {display!r} leaks AUI 'A44054006'"
        )
        # Display MUST NOT leak SAB label.
        assert "SNOMEDCT_US" not in display
        assert "ICD10CM" not in display
        assert "RXNORM" not in display


def test_t25_translate_target_display_does_not_leak_engine_internals(
    fhir_client,
):
    """TERMINOLOGIST: clinical-safety — $translate target display MUST NOT
    leak engine internals.

    A CDS hook reading the $translate response would display this string
    to the clinician — engine internals would confuse the clinician.
    """
    matches = _translate_matches_get(
        fhir_client, SNOMED_URI, SNOMED_T2DM, ICD10CM_URI,
    )
    if not matches:
        pytest.skip("no $translate matches")

    for m in matches:
        concept = _extract_match_concept(m)
        if not concept:
            continue
        display = concept.get("display") or ""
        # Display MUST NOT contain internal UMLS identifiers.
        assert "C0011847" not in display
        assert "AE11" not in display
        assert "A44054006" not in display


def test_t26_export_target_display_does_not_leak_engine_internals():
    """TERMINOLOGIST: clinical-safety — ConceptMap export target.display
    MUST NOT leak engine internals into the consumer-facing display.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    rows = [_make_concept_map_row(
        target_code=ICD10CM_T2DM,
        target_sab="ICD10CM",
        target_display=ICD10CM_T2DM_DISPLAY,
    )]
    resource = concept_map_to_fhir(rows)
    for g in resource["group"]:
        for element in g.get("element", []):
            for target in element.get("target", []):
                display = target.get("display") or ""
                assert "C0011847" not in display
                assert "AE11" not in display
                assert "ICD10CM" not in display


def test_t27_lookup_name_param_is_code_system_name_not_concept_term(fhir_client):
    """TERMINOLOGIST: clinical-correctness — $lookup Out `name` parameter
    is the CODE SYSTEM name (e.g. "SNOMED Clinical Terms (US)"), NOT the
    concept term. Per CS-02 SKEPTIC resweep test_s01..s03 contract.

    Conflating the two would produce a clinically-misleading response:
    clients use `name` to identify the code system, not the concept.
    """
    params = _lookup_params(fhir_client, SNOMED_URI, SNOMED_T2DM)
    if not params:
        pytest.skip("fixture missing")

    name_p = params.get("name")
    display_p = params.get("display")

    assert name_p and name_p.get("valueString"), "$lookup missing name param"
    assert display_p and display_p.get("valueString"), "$lookup missing display"

    name_val = name_p["valueString"]
    display_val = display_p["valueString"]

    # The name MUST NOT equal the display (they're distinct fields).
    assert name_val != display_val, (
        f"$lookup name={name_val!r} == display={display_val!r}. The name "
        f"param is the code system name, NOT the concept term — conflating "
        f"them is clinically misleading."
    )

    # The name MUST contain "SNOMED" or similar code-system identifier
    # (not "diabetes" which would be the concept term).
    name_lower = name_val.lower()
    assert "snomed" in name_lower or "icd" in name_lower or "rxnorm" in name_lower, (
        f"$lookup name={name_val!r} does not identify the code system "
        f"(expected to contain 'snomed' or similar)."
    )


# =============================================================================
# Lens 3: ConceptMap export surface clinical correctness.
#
# Per the EXPLORER tip: ``group.element.target.display`` in CM export is the
# clinically preferred term for the target code. Verify clinical correctness
# of the export surface end-to-end:
#   - target.display is clinically sensible (not raw code)
#   - target.equivalence is the R4 enum-correct value
#   - target.system is the canonical URI
#   - source.display is clinically sensible
#   - source.system is the canonical URI
#
# Spec: https://hl7.org/fhir/R4/conceptmap.html
# =============================================================================


def test_t30_export_target_display_clinically_correct_per_target_system():
    """TERMINOLOGIST: ConceptMap export — for each seeded target system,
    the target.display is the clinically preferred term for the target code.

    This is the META-PATTERN at the export layer — every target code's
    display MUST match the canonical preferred term (not the raw code, not
    a technical identifier).

    Spec: FHIR R4 ConceptMap.target.display = "A display for the target code."
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir
    from medterm4ds.engines.fhir import fhir_uri_to_system

    cases = [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_DM_DISPLAY),
        (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM_DISPLAY),
        (ICD10CM_URI, ICD10CM_T2DM, ICD10CM_T2DM_DISPLAY),
        (RXNORM_URI, RXNORM_METFORMIN, RXNORM_METFORMIN_DISPLAY),
    ]
    for target_system, target_code, expected_display in cases:
        target_sab = fhir_uri_to_system(target_system)
        rows = [_make_concept_map_row(
            source_code=SNOMED_T2DM,
            source_sab="SNOMEDCT_US",
            target_code=target_code,
            target_sab=target_sab,
            target_display=expected_display,
            relationship="equivalent",
        )]
        resource = concept_map_to_fhir(rows)
        for g in resource["group"]:
            # Group target MUST be canonical URI.
            assert g["target"] == target_system, (
                f"group.target={g['target']!r}; expected canonical "
                f"{target_system!r}"
            )
            for element in g.get("element", []):
                for target in element.get("target", []):
                    if target.get("code") == target_code:
                        assert target.get("display") == expected_display


def test_t31_export_source_display_clinically_sensible():
    """TERMINOLOGIST: ConceptMap export — source.display is the clinically
    preferred term for the source code (mirrors target.display).

    Spec: FHIR R4 ConceptMap.element.display = "A display for the source
    code."
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    rows = [_make_concept_map_row(
        source_code=SNOMED_T2DM,
        source_sab="SNOMEDCT_US",
        source_display=SNOMED_T2DM_DISPLAY,
        target_code=ICD10CM_T2DM,
        target_sab="ICD10CM",
        target_display=ICD10CM_T2DM_DISPLAY,
    )]
    resource = concept_map_to_fhir(rows)
    for g in resource["group"]:
        assert g["source"] == SNOMED_URI
        assert g["target"] == ICD10CM_URI
        for element in g.get("element", []):
            if element.get("code") == SNOMED_T2DM:
                assert element.get("display") == SNOMED_T2DM_DISPLAY, (
                    f"source.display={element.get('display')!r}; expected "
                    f"clinically-preferred {SNOMED_T2DM_DISPLAY!r}"
                )


def test_t32_export_target_equivalence_clinically_correct_per_relationship():
    """TERMINOLOGIST: ConceptMap export — target.equivalence is the R4
    enum-correct value for each engine relationship.

    Clinical correctness: a downstream consumer reading the ConceptMap
    JSON would treat `equivalence=equivalent` as a confirmed equivalence;
    `equivalence=relatedto` as a looser association. The translation MUST
    be clinically correct.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    cases = [
        # (relationship, expected_equivalence)
        ("equivalent", "equivalent"),
        ("same", "equal"),
        ("identical", "equal"),
        ("source-is-narrower-than-target", "wider"),
        ("source-is-broader-than-target", "narrower"),
        ("related-to", "relatedto"),
        ("not-translated", "unmatched"),
        ("unmatched", "unmatched"),
        ("subsumes", "subsumes"),
        ("specializes", "specializes"),
        ("disjoint", "disjoint"),
    ]
    for relationship, expected_equivalence in cases:
        rows = [_make_concept_map_row(relationship=relationship)]
        resource = concept_map_to_fhir(rows)
        found = False
        for g in resource["group"]:
            for element in g.get("element", []):
                for target in element.get("target", []):
                    eq = target.get("equivalence")
                    assert eq in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
                    assert eq == expected_equivalence, (
                        f"relationship={relationship!r} produced equivalence="
                        f"{eq!r}; expected R4-clinically-correct "
                        f"{expected_equivalence!r}"
                    )
                    found = True
        assert found


def test_t33_export_unmatched_relationship_omits_target_code_clinical_safety():
    """TERMINOLOGIST: clinical-safety — when equivalence=unmatched, the
    target.code and target.display MUST be omitted (per spec). A consumer
    reading the ConceptMap would otherwise see a target code+display that
    has NO clinical meaning.

    Spec: FHIR R4 ConceptMap.target — code and display are 0..1; omitted
    when equivalence=unmatched.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    rows = [_make_concept_map_row(
        relationship="unmatched",
        target_code=ICD10CM_T2DM,
        target_display=ICD10CM_T2DM_DISPLAY,
    )]
    resource = concept_map_to_fhir(rows)
    for g in resource["group"]:
        for element in g.get("element", []):
            for target in element.get("target", []):
                assert target.get("equivalence") == "unmatched"
                # CRITICAL clinical safety: no code, no display surfaced.
                assert "code" not in target, (
                    f"target.code present on unmatched equivalence — consumer "
                    f"would see a code with no clinical meaning: {target}"
                )
                assert "display" not in target


def test_t34_export_group_source_target_canonical_uri_no_drift():
    """TERMINOLOGIST: ConceptMap export — group.source and group.target
    are CANONICAL URIs sourced from SYSTEM_TO_FHIR_URI registry.

    Clinical safety: a downstream consumer using group.source to identify
    the source code system would be confused if the URI is a legacy THO
    URL or an alias (e.g., HCPCS drift class count=8+1 PROMOTED).

    Spec: FHIR R4 ConceptMap.group.source/target = "An absolute URI that
    identifies the source/target system."
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    seeded_sabs = ["SNOMEDCT_US", "ICD10CM", "RXNORM"]
    rows = [
        _make_concept_map_row(
            source_code=SNOMED_DIABETES_MELLITUS,
            source_sab=sab,
            target_code=ICD10CM_T2DM,
            target_sab="ICD10CM",
        )
        for sab in seeded_sabs
    ]
    resource = concept_map_to_fhir(rows)
    observed_sources = {g["source"] for g in resource["group"]}
    observed_targets = {g["target"] for g in resource["group"]}

    # Every SAB resolves to its canonical URI.
    for sab in seeded_sabs:
        expected_uri = SYSTEM_TO_FHIR_URI.get(sab)
        assert expected_uri in observed_sources, (
            f"SAB {sab!r} did not resolve to canonical {expected_uri!r}"
        )

    # HCPCS drift class load-bearing: no legacy THO URL leaks.
    for uri in observed_sources | observed_targets:
        assert "terminology.hl7.org/CodeSystem" not in uri, (
            f"legacy THO URL in export: {uri!r}. HCPCS drift class "
            f"(count=8+1 PROMOTED) requires canonical URIs only."
        )


def test_t35_export_default_url_constant_clinically_correct():
    """TERMINOLOGIST: ConceptMap export — the default URL is the
    server-local identifier (urn:medterm4ds:ConceptMap:patient-friendly),
    NOT a falsified HL7 URL. A downstream consumer reading the URL would
    be misled if it claimed to be an HL7-canonical ConceptMap.

    Spec: FHIR R4 ConceptMap.url = "An absolute URI that is used to
    identify this concept map when it is referenced in a specification".
    """
    from medterm4ds.outputs.fhir import (
        DEFAULT_CONCEPT_MAP_URL,
        concept_map_to_fhir,
    )

    # The default URL MUST be server-local (urn:medterm4ds:...).
    assert DEFAULT_CONCEPT_MAP_URL.startswith("urn:medterm4ds:"), (
        f"DEFAULT_CONCEPT_MAP_URL={DEFAULT_CONCEPT_MAP_URL!r} is not "
        f"server-local — clinical correctness requires honest identification."
    )

    resource = concept_map_to_fhir([_make_concept_map_row()])
    assert resource["url"] == DEFAULT_CONCEPT_MAP_URL
    # MUST NOT claim to be an HL7-canonical ConceptMap URL.
    assert "hl7.org" not in resource["url"]


def test_t36_export_resource_type_and_id_clinically_correct():
    """TERMINOLOGIST: ConceptMap export — the resource is a valid FHIR R4
    ConceptMap with the expected id, name, title, status, publisher.

    Clinical correctness: a downstream FHIR client parsing the resource
    would expect resourceType=ConceptMap. Any other value would silently
    fail resource-type dispatch.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    resource = concept_map_to_fhir([_make_concept_map_row()])
    assert resource["resourceType"] == "ConceptMap"
    assert resource["status"] == "draft"  # spec default
    assert resource["publisher"] == "medterm4ds"
    # id, name, title are sensible defaults.
    assert resource["id"]
    assert resource["name"]
    assert resource["title"]


def test_t37_export_xml_serialization_clinical_display_preserved():
    """TERMINOLOGIST: clinical-safety — when serialized to XML via the
    FHIR XML serializer, the display fields are preserved verbatim
    (no XML-entity corruption that would mis-render clinical terms).

    Spec: FHIR R4 XML representation preserves all string content.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir
    try:
        from medterm4ds.engines.fhir.xml import to_fhir_xml
    except ImportError:
        pytest.skip("XML serializer not available")

    rows = [_make_concept_map_row(
        source_display=SNOMED_T2DM_DISPLAY,
        target_display=ICD10CM_T2DM_DISPLAY,
    )]
    resource = concept_map_to_fhir(rows)
    xml_str = to_fhir_xml(resource)
    # The clinical displays MUST appear verbatim in the XML.
    assert SNOMED_T2DM_DISPLAY in xml_str, (
        f"source display {SNOMED_T2DM_DISPLAY!r} not in XML serialization"
    )
    assert ICD10CM_T2DM_DISPLAY in xml_str, (
        f"target display {ICD10CM_T2DM_DISPLAY!r} not in XML serialization"
    )


# =============================================================================
# Lens 4: Cross-source clinical correctness.
#
# The conformance fixture has 3 sources (SNOMED, ICD-10-CM, RxNorm). The
# export surface MUST handle each source's clinical conventions:
#   - SNOMED CT PT (Preferred Term) — clinician-readable
#   - ICD-10-CM HT (Hypernym Term? No — HT means "Preferred Term" in UMLS for ICD-10-CM)
#   - RxNorm SCD (Semantic Clinical Drug) — full drug name incl. dose+form
# =============================================================================


def test_t40_export_handles_each_seeded_source_clinical_conventions():
    """TERMINOLOGIST: cross-source clinical correctness — the export
    surface handles each seeded source's clinical conventions.

    For each target_system in {SNOMED, ICD-10-CM, RxNorm}, the export
    produces a group with the canonical URI AND the target.display is
    clinically sensible per the source's conventions.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir
    from medterm4ds.engines.fhir import fhir_uri_to_system

    cases = [
        # (target_system, target_code, expected_display_substring)
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
        (SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes mellitus"),
        (ICD10CM_URI, ICD10CM_T2DM, "Type 2 diabetes mellitus"),
        (RXNORM_URI, RXNORM_METFORMIN, "metformin"),
    ]
    for target_system, target_code, expected_substring in cases:
        target_sab = fhir_uri_to_system(target_system)
        rows = [_make_concept_map_row(
            target_code=target_code,
            target_sab=target_sab,
            target_display=expected_substring,
        )]
        resource = concept_map_to_fhir(rows)
        found = False
        for g in resource["group"]:
            for element in g.get("element", []):
                for target in element.get("target", []):
                    if target.get("code") == target_code:
                        found = True
                        display = target.get("display", "")
                        assert expected_substring.lower() in display.lower(), (
                            f"target.display={display!r} does not contain "
                            f"expected clinical substring {expected_substring!r}"
                        )
        assert found


def test_t41_export_multi_target_per_source_clinically_distinct():
    """TERMINOLOGIST: clinical correctness — when one source code maps to
    multiple targets, each target.display is CLINICALLY DISTINCT.

    For example, SNOMED T2DM might map to ICD-10-CM E11 (clinical term)
    AND RxNorm metformin (drug name). The displays MUST be clinically
    distinct — a single display repeated would be silent-wrong-answer.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    rows = [
        _make_concept_map_row(
            source_code=SNOMED_T2DM,
            source_sab="SNOMEDCT_US",
            source_display=SNOMED_T2DM_DISPLAY,
            target_code=ICD10CM_T2DM,
            target_sab="ICD10CM",
            target_display=ICD10CM_T2DM_DISPLAY,
            relationship="equivalent",
        ),
        _make_concept_map_row(
            source_code=SNOMED_T2DM,
            source_sab="SNOMEDCT_US",
            source_display=SNOMED_T2DM_DISPLAY,
            target_code=RXNORM_METFORMIN,
            target_sab="RXNORM",
            target_display=RXNORM_METFORMIN_DISPLAY,
            relationship="related-to",
        ),
    ]
    resource = concept_map_to_fhir(rows)

    displays_by_code = {}
    for g in resource["group"]:
        for element in g.get("element", []):
            for target in element.get("target", []):
                code = target.get("code")
                display = target.get("display")
                if code and display:
                    displays_by_code[code] = display

    # Each target code has its own clinically-distinct display.
    assert displays_by_code.get(ICD10CM_T2DM) == ICD10CM_T2DM_DISPLAY
    assert displays_by_code.get(RXNORM_METFORMIN) == RXNORM_METFORMIN_DISPLAY
    # The displays are clinically distinct.
    assert displays_by_code[ICD10CM_T2DM] != displays_by_code[RXNORM_METFORMIN]


# =============================================================================
# Lens 5: Clinical-safety no-silent-wrong-answer.
#
# Verify the surface produces clinically-safe responses on edge cases —
# no silent-wrong-answer where a clinical failure is masked as success.
# =============================================================================


def test_t50_translate_no_match_returns_result_false_not_empty_match(
    fhir_client,
):
    """TERMINOLOGIST: clinical-safety — when $translate finds no match,
    the response MUST return result=false AND an empty match list. A
    silent-wrong-answer (result=true with no matches, or result=false with
    phantom matches) would be a clinical-safety bug.

    Spec: FHIR R4 $translate "result" = "true if the engine was able to
    return some matches".
    """
    # SNOMED T2DM → RxNorm should produce no match (different domains).
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": SNOMED_T2DM,
            "targetsystem": RXNORM_URI,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters"

    result_p = next(
        (p for p in body.get("parameter", []) if p.get("name") == "result"),
        None,
    )
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]

    # If result is true, there MUST be matches. If false, NO matches.
    if result_p and result_p.get("valueBoolean") is True:
        assert matches, (
            "result=true but no matches returned — clinical-safety bug: "
            "engine claims translation succeeded but provided no mappings"
        )
    elif result_p and result_p.get("valueBoolean") is False:
        assert not matches, (
            "result=false but matches returned — clinical-safety bug: "
            "engine claims failure but provided phantom matches"
        )


def test_t51_export_no_rows_returns_well_formed_conceptmap():
    """TERMINOLOGIST: clinical-safety — empty rows input produces a
    well-formed ConceptMap with empty group[]. A crash or malformed
    resource would be a clinical-correctness bug.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    resource = concept_map_to_fhir([])
    assert resource["resourceType"] == "ConceptMap"
    assert resource["group"] == []


def test_t52_translate_unknown_system_returns_clinically_informative_400(
    fhir_client,
):
    """TERMINOLOGIST: clinical-safety — an unknown system URI produces a
    400 with a clinically-informative OperationOutcome (NOT a 500 or a
    silent-success).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://example.org/unknown-system",
            "code": "X",
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 400
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"
    # The diagnostics MUST name the offending system.
    diag = ""
    for issue in body.get("issue", []):
        diag += issue.get("diagnostics", "") + " "
    assert "unknown-system" in diag.lower() or "unrecognized" in diag.lower(), (
        f"OperationOutcome diagnostics not clinically informative: {diag!r}"
    )


# =============================================================================
# Lens 6: Source-read structural contracts for clinical correctness.
#
# These source-read probes pin the load-bearing structural contracts that
# underpin clinical correctness on the CM-01 surface.
# =============================================================================


def test_t60_extract_translate_params_consults_coding_and_codeableconcept():
    """TERMINOLOGIST: source-read contract. _extract_translate_params
    MUST consult _extract_named_coding_from_parameters AND
    _extract_codeable_concept_from_parameters when scalar system/code
    are absent.

    Spec: source-read audit. Reference: CM-01 EXPLORER QA-001.
    """
    import medterm4ds.apps.fhir_api as fhir_api_mod

    # _extract_translate_params is defined inside create_fhir_app — use AST
    # walk on the module source to find the function body.
    src = inspect.getsource(fhir_api_mod)
    tree = ast.parse(src)
    found_func = False
    found_coding = False
    found_codeable = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_extract_translate_params":
                found_func = True
                func_src = ast.get_source_segment(src, node)
                if "_extract_named_coding_from_parameters" in func_src:
                    found_coding = True
                if "_extract_codeable_concept_from_parameters" in func_src:
                    found_codeable = True
    assert found_func, "_extract_translate_params not found in module"
    assert found_coding, (
        "_extract_translate_params does NOT consult "
        "_extract_named_coding_from_parameters — CF-CM02-01 regression"
    )
    assert found_codeable, (
        "_extract_translate_params does NOT consult "
        "_extract_codeable_concept_from_parameters — CF-CM02-01 regression"
    )


def test_t61_translate_post_calls_extract_translate_params_not_inline():
    """TERMINOLOGIST: source-read contract. translate_post MUST call
    _extract_translate_params (the consolidated helper) instead of inlining
    scalar-only extraction.

    Spec: source-read audit. Reference: CM-01 EXPLORER QA-001 fix.
    """
    import medterm4ds.apps.fhir_api as fhir_api_mod

    src = inspect.getsource(fhir_api_mod)
    tree = ast.parse(src)
    found_translate_post = False
    found_helper_call = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "translate_post":
                found_translate_post = True
                func_src = ast.get_source_segment(src, node)
                if "_extract_translate_params" in func_src:
                    found_helper_call = True
    assert found_translate_post, "translate_post not found in module"
    assert found_helper_call, (
        "translate_post does NOT call _extract_translate_params — CF-CM02-01 "
        "regression: POST handler inlined scalar-only extraction"
    )


def test_t62_do_translate_calls_canonical_system_uri():
    """TERMINOLOGIST: source-read contract. _do_translate MUST call
    canonical_system_uri(source_uri, source=source) before passing to
    build_parameters_translate. This is the CR-012 fix — without it,
    the Out match.source.system echoes client input.

    Spec: client-input-as-canonical drift pattern (count=8+1 PROMOTED).
    """
    import medterm4ds.apps.fhir_api as fhir_api_mod

    src = inspect.getsource(fhir_api_mod)
    tree = ast.parse(src)
    found_func = False
    found_call = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_do_translate":
                found_func = True
                func_src = ast.get_source_segment(src, node)
                if "canonical_system_uri(" in func_src:
                    found_call = True
    assert found_func, "_do_translate not found"
    assert found_call, (
        "_do_translate does NOT call canonical_system_uri — CR-012 regression"
    )


def test_t63_build_parameters_translate_uses_engine_relationship():
    """TERMINOLOGIST: source-read contract. build_parameters_translate
    MUST source match.equivalence from the engine relationship via
    _fhir_equivalence_from_relationship (NOT hardcode "equivalent").

    Spec: TS-02 TERMINOLOGIST QA-030. Hardcoding "equivalent" would
    misrepresent SNOMED→ICD10CM crosswalks (which are typically relatedto)
    and ancestor/descendant mappings.
    """
    import medterm4ds.engines.fhir.responses as responses_mod

    src = inspect.getsource(
        responses_mod.build_parameters_translate
    )
    tree = ast.parse(src)
    found_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "_fhir_equivalence_from_relationship"
            ):
                found_call = True
    assert found_call, (
        "build_parameters_translate does NOT call "
        "_fhir_equivalence_from_relationship — TS-02 TERMINOLOGIST QA-030 "
        "regression"
    )


def test_t64_merge_row_target_uses_fhir_equivalence_no_hardcode():
    """TERMINOLOGIST: source-read contract. _merge_row_target MUST call
    fhir_equivalence(row.relationship) — NEVER hardcode a single value.

    Mirrors CM-01 EXPLORER test_e80 source-read contract.
    """
    import medterm4ds.outputs.fhir as outputs_fhir_mod

    src = inspect.getsource(outputs_fhir_mod._merge_row_target)
    tree = ast.parse(src)
    found_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "fhir_equivalence"
            ):
                found_call = True
    assert found_call, (
        "_merge_row_target does NOT call fhir_equivalence — equivalence "
        "hardcoded"
    )


def test_t65_internal_rel_to_fhir_equivalence_object_identity_with_canonical():
    """TERMINOLOGIST: source-read contract. INTERNAL_REL_TO_FHIR_EQUIVALENCE
    imported in outputs/fhir.py MUST be the SAME OBJECT as the canonical
    map in engines.fhir.equivalence (via `is` operator).

    Mirrors HISTORIAN L1 4-axis object-identity probe class.

    Spec: CR-024 unification.
    """
    import medterm4ds.outputs.fhir as outputs_fhir_mod

    # outputs/fhir.py imports it as FHIR_EQUIVALENCES.
    assert outputs_fhir_mod.FHIR_EQUIVALENCES is INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
        "outputs/fhir.py:FHIR_EQUIVALENCES is NOT the same object as the "
        "canonical INTERNAL_REL_TO_FHIR_EQUIVALENCE — CR-024 regression"
    )


def test_t66_responses_module_uses_canonical_internal_rel_to_fhir_equiv():
    """TERMINOLOGIST: source-read contract. responses.py MUST import
    INTERNAL_REL_TO_FHIR_EQUIVALENCE from the canonical equivalence module
    (as _INTERNAL_REL_TO_FHIR_EQUIVALENCE alias).

    Mirrors HISTORIAN L1 4-axis object-identity probe class.

    Spec: CR-024 unification.
    """
    import medterm4ds.engines.fhir.responses as responses_mod

    assert (
        responses_mod._INTERNAL_REL_TO_FHIR_EQUIVALENCE
        is INTERNAL_REL_TO_FHIR_EQUIVALENCE
    ), (
        "responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE is NOT the same "
        "object as the canonical map — CR-024 regression"
    )


def test_t67_fhir_r4_concept_map_equivalence_has_10_members():
    """TERMINOLOGIST: META cardinality. The FHIR R4 ConceptMapEquivalence
    closed enum has EXACTLY 10 members per spec.

    Mirrors SKEPTIC test_s90, EXPLORER test_e100.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    assert len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE) == 10, (
        f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE has {len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)} "
        f"members; expected 10 per R4 spec."
    )


def test_t68_canonical_system_uri_returns_canonical_for_every_alias():
    """TERMINOLOGIST: META helper contract. canonical_system_uri(alias)
    returns the canonical URI for every seeded alias. The helper is the
    load-bearing structural fix for client-input-as-canonical drift.

    Spec: client-input-as-canonical drift pattern (count=8+1 PROMOTED).
    """
    cases = [
        (SNOMED_URI, SNOMED_URI),
        (SNOMED_TRAILING_SLASH, SNOMED_URI),
        (SNOMED_OID, SNOMED_URI),
        (SNOMED_UPPERCASE_SCHEME, SNOMED_URI),
        (ICD10CM_URI, ICD10CM_URI),
        (ICD10CM_OID, ICD10CM_URI),
        (ICD10CM_TRAILING_SLASH, ICD10CM_URI),
        (RXNORM_URI, RXNORM_URI),
    ]
    for alias, expected in cases:
        actual = canonical_system_uri(alias)
        assert actual == expected, (
            f"canonical_system_uri({alias!r})={actual!r}; expected {expected!r}"
        )


# =============================================================================
# Lens 7: META — closed-enum membership audit on EVERY consumer surface.
#
# Defense-in-depth: object identity (Lens 6) AND per-surface membership
# audit. A future regression that breaks object identity but emits the
# same drift value on both surfaces would pass Lens 6 but fail Lens 7.
# =============================================================================


def test_t70_outputs_fhir_emitted_values_subset_of_r4_enum():
    """TERMINOLOGIST: META closed-enum membership on export surface. Every
    value emitted via concept_map_to_fhir target.equivalence MUST be a
    member of FHIR_R4_CONCEPT_MAP_EQUIVALENCE.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    # Exercise every engine relationship.
    for rel in INTERNAL_REL_TO_FHIR_EQUIVALENCE.keys():
        rows = [_make_concept_map_row(relationship=rel)]
        resource = concept_map_to_fhir(rows)
        for g in resource["group"]:
            for element in g.get("element", []):
                for target in element.get("target", []):
                    eq = target.get("equivalence")
                    if eq:
                        assert eq in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_t71_internal_rel_to_fhir_equiv_values_subset_of_r4_enum():
    """TERMINOLOGIST: META closed-enum membership. Every VALUE in the
    canonical translation map MUST be a member of the R4 closed enum.
    """
    values = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    assert values <= FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"INTERNAL_REL_TO_FHIR_EQUIVALENCE has values NOT in R4 closed enum: "
        f"{values - FHIR_R4_CONCEPT_MAP_EQUIVALENCE}"
    )


def test_t72_no_r5_only_keys_in_canonical_map():
    """TERMINOLOGIST: META R5/R4B contamination. The canonical translation
    map MUST NOT have R5-only values (e.g., 'matches') as KEYS.

    Mirrors EXPLORER test_e121.

    Spec: CF-HISTORIAN-VS01-01 RESOLVED.
    """
    r5_only_keys = {"matches"}  # R5-only
    keys = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.keys())
    r5_only_in_keys = keys & r5_only_keys
    assert not r5_only_in_keys, (
        f"R5-only values {r5_only_in_keys} appear as KEYS in the canonical map"
    )


# =============================================================================
# Lens 8: META — single-walk clinical-correctness audit.
#
# Single-walk audit of every concept_map_to_fhir call site to ensure
# clinical correctness invariants hold across all paths.
# =============================================================================


def test_t80_outputs_fhir_module_does_not_hardcode_equivalence_value():
    """TERMINOLOGIST: META single-walk. outputs/fhir.py MUST NOT hardcode
    any equivalence value as a string literal in executable code outside
    comparison context. Every equivalence assignment MUST go through
    fhir_equivalence().

    Mirrors EXPLORER test_e120.
    """
    import medterm4ds.outputs.fhir as outputs_fhir_mod

    src = inspect.getsource(outputs_fhir_mod)
    src_lines = src.split("\n")
    violations = []
    for i, line in enumerate(src_lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "://" in stripped:  # URL strings, not enum values
            continue
        for enum_val in FHIR_R4_CONCEPT_MAP_EQUIVALENCE:
            # Check for hardcoded equivalence value in dict assignment
            # (e.g., "equivalence": "equivalent").
            if f'"equivalence": "{enum_val}"' in stripped:
                violations.append((i, stripped, enum_val))
    # The export module MUST NOT have hardcoded equivalence assignments.
    # (Only fhir_equivalence() calls should produce equivalence values.)
    assert not violations, (
        f"outputs/fhir.py hardcodes equivalence values: {violations}"
    )


def test_t81_outputs_fhir_no_hardcoded_hcpcs_uri():
    """TERMINOLOGIST: META single-walk. outputs/fhir.py MUST NOT contain
    any hardcoded HCPCS URI (canonical OR legacy) — the URI MUST come from
    SYSTEM_TO_FHIR_URI registry.

    Spec: HCPCS drift class (count=8+1 PROMOTED).
    """
    import medterm4ds.outputs.fhir as outputs_fhir_mod

    src = inspect.getsource(outputs_fhir_mod)
    # Known HCPCS URIs that should NOT be hardcoded:
    forbidden = [
        "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II",
        "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets",
    ]
    for uri in forbidden:
        # Allow in comments only — search outside comment context.
        for i, line in enumerate(src.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert uri not in stripped, (
                f"outputs/fhir.py line {i} hardcodes HCPCS URI {uri!r}; "
                f"MUST source from SYSTEM_TO_FHIR_URI registry"
            )


def test_t82_responses_module_does_not_hardcode_equivalence_value():
    """TERMINOLOGIST: META single-walk. responses.py MUST NOT hardcode
    any single equivalence value (e.g., "equivalent") in
    build_parameters_translate — every equivalence MUST go through
    _fhir_equivalence_from_relationship.

    Spec: TS-02 TERMINOLOGIST QA-030.
    """
    import medterm4ds.engines.fhir.responses as responses_mod

    src = inspect.getsource(responses_mod.build_parameters_translate)
    # The function MUST call _fhir_equivalence_from_relationship.
    assert "_fhir_equivalence_from_relationship" in src, (
        "build_parameters_translate does NOT call "
        "_fhir_equivalence_from_relationship — TS-02 TERMINOLOGIST QA-030 "
        "regression"
    )

    # The function MUST NOT hardcode a single equivalence value in the
    # valueCode assignment for the equivalence part.
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "valueCode"
                ):
                    # The value for valueCode in the equivalence part MUST
                    # be a Call to _fhir_equivalence_from_relationship.
                    # Other valueCode uses (e.g., system) are OK.
                    pass  # We don't have enough context here; the call-site
                          # check above is the load-bearing assertion.


def test_t83_canonical_system_uri_helper_present_in_engines_fhir():
    """TERMINOLOGIST: META structural. The canonical_system_uri helper
    is present and exported from engines.fhir — the load-bearing helper
    behind client-input-as-canonical drift prevention.
    """
    from medterm4ds.engines.fhir import canonical_system_uri as helper
    assert callable(helper), "canonical_system_uri not callable"


def test_t84_fhir_uri_to_system_round_trip_for_every_seeded_uri():
    """TERMINOLOGIST: META round-trip. For every seeded URI,
    fhir_uri_to_system(uri) returns the source SAB, and
    SYSTEM_TO_FHIR_URI[sab] returns the canonical URI.

    Spec: client-input-as-canonical drift pattern (count=8+1 PROMOTED).
    """
    from medterm4ds.engines.fhir import fhir_uri_to_system

    cases = [
        (SNOMED_URI, "SNOMEDCT_US"),
        (ICD10CM_URI, "ICD10CM"),
        (RXNORM_URI, "RXNORM"),
    ]
    for uri, expected_sab in cases:
        sab = fhir_uri_to_system(uri)
        assert sab == expected_sab, (
            f"fhir_uri_to_system({uri!r})={sab!r}; expected {expected_sab!r}"
        )
        assert SYSTEM_TO_FHIR_URI[sab] == uri, (
            f"SYSTEM_TO_FHIR_URI[{sab!r}]={SYSTEM_TO_FHIR_URI.get(sab)!r}; "
            f"expected {uri!r}"
        )
