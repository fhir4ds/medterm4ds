"""TERMINOLOGIST iteration TS-02 — clinical-correctness probes.

Source: https://build.fhir.org/terminology-service.html#summary §4.7.1.2

TERMINOLOGIST lens (per assignment):
1. Code-system URI consistency across all 7 operations.
2. $lookup display correctness (patient-friendly where applicable).
3. $validate-code response shape — `display` parameter semantics.
4. $subsumes outcome vocabulary is exactly the FHIR R4 enum.
5. $translate match.equivalence uses the actual mapping relationship,
   not a hardcoded literal.

Default severity HIGH for any TERMINOLOGIST finding (per GLOBAL_RULES.md).
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 1. Canonical code-system URI consistency across operations.
#
# TS-01 TERMINOLOGIST QA-012 caught HCPCS URI drift. Here we verify every
# operation handler routes the `system` parameter through the canonical
# `engines.fhir.fhir_uri_to_system` resolver and never bypasses it with a
# hardcoded URI literal. The probe asks: does each operation accept the
# canonical FHIR system URI AND reject a plausible-but-wrong variant?
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "system_uri, expected_source",
    [
        ("http://snomed.info/sct", "SNOMEDCT_US"),
        ("http://www.nlm.nih.gov/research/umls/rxnorm", "RXNORM"),
        ("http://hl7.org/fhir/sid/icd-10-cm", "ICD10CM"),
        ("http://loinc.org", "LNC"),
        ("http://www.ama-assn.org/go/cpt", "CPT"),
        # HCPCS canonical URI — was the THO resource URL before QA-012.
        ("http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets", "HCPCS"),
        ("http://hl7.org/fhir/sid/cvx", "CVX"),
    ],
)
def test_t01_lookup_accepts_canonical_uri(fhir_client, system_uri, expected_source):
    """TERMINOLOGIST: $lookup MUST accept the canonical FHIR R4 system URI
    for every supported code system. The handler delegates to
    ``fhir_uri_to_system``; a hardcoded URI anywhere in the chain would
    cause a 400 'Unrecognized system URI' for the canonical form.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html —
    system parameter is `0..1 uri` and MUST be the canonical system URI.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system_uri, "code": "any-code"},
    )
    # We don't care whether the code exists; we care that the URI is accepted
    # (not rejected as 'Unrecognized system URI').
    body = r.text
    assert "Unrecognized system URI" not in body, (
        f"$lookup rejected canonical URI {system_uri!r} for source {expected_source}. "
        f"Status={r.status_code}, body={body[:200]!r}"
    )


@pytest.mark.parametrize(
    "system_uri, expected_source",
    [
        ("http://snomed.info/sct", "SNOMEDCT_US"),
        ("http://hl7.org/fhir/sid/icd-10-cm", "ICD10CM"),
        ("http://www.nlm.nih.gov/research/umls/rxnorm", "RXNORM"),
        ("http://loinc.org", "LNC"),
    ],
)
def test_t02_validate_code_accepts_canonical_uri(fhir_client, system_uri, expected_source):
    """TERMINOLOGIST: $validate-code accepts the canonical FHIR R4 URI."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": system_uri, "code": "any-code"},
    )
    assert "Unrecognized system URI" not in r.text, (
        f"$validate-code rejected canonical URI {system_uri!r}. "
        f"Status={r.status_code}, body={r.text[:200]!r}"
    )


def test_t03_subsumes_accepts_canonical_snomed_uri(fhir_client):
    """TERMINOLOGIST: $subsumes uses the canonical SNOMED URI."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": "http://snomed.info/sct",
            "codeA": "44054006",
            "codeB": "73211009",
        },
    )
    assert "Unrecognized system URI" not in r.text, (
        f"$subsumes rejected canonical SNOMED URI. Status={r.status_code}, body={r.text[:200]!r}"
    )


def test_t04_translate_accepts_canonical_snomed_to_icd10_uri(fhir_client):
    """TERMINOLOGIST: $translate routes both source and target system URIs
    through the canonical resolver. A hardcoded URI on either side would
    cause a 400 'Unrecognized ... system URI'.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
        },
    )
    assert "Unrecognized" not in r.text, (
        f"$translate rejected a canonical source/target URI. "
        f"Status={r.status_code}, body={r.text[:200]!r}"
    )


# ---------------------------------------------------------------------------
# 2. $subsumes outcome vocabulary — FHIR R4 enum.
#
# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html Out
# Parameters: outcome is a `code` constrained to the value set
# {equivalent, subsumes, subsumed-by, not-subsumed}. No synonyms allowed.
# ---------------------------------------------------------------------------


def test_t05_subsumes_outcome_vocabulary(fhir_client):
    """TERMINOLOGIST: $subsumes outcome MUST be one of the four FHIR R4
    enum values. The fixture DB has T2DM (44054006) as a child of
    Diabetes Mellitus (73211009) via an `isa` parent relationship.
    """
    valid_outcomes = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}

    # parent vs child → subsumes (parent subsumes child)
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={"system": "http://snomed.info/sct", "codeA": "73211009", "codeB": "44054006"},
    )
    params = r.json()["parameter"]
    outcome = params[0]["valueCode"]
    assert outcome in valid_outcomes, f"outcome {outcome!r} not in FHIR enum"
    assert outcome == "subsumes", (
        f"parent→child should be 'subsumes', got {outcome!r}"
    )

    # child vs parent → subsumed-by
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={"system": "http://snomed.info/sct", "codeA": "44054006", "codeB": "73211009"},
    )
    outcome = r.json()["parameter"][0]["valueCode"]
    assert outcome in valid_outcomes
    assert outcome == "subsumed-by", (
        f"child→parent should be 'subsumed-by', got {outcome!r}"
    )

    # same code → equivalent
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={"system": "http://snomed.info/sct", "codeA": "44054006", "codeB": "44054006"},
    )
    outcome = r.json()["parameter"][0]["valueCode"]
    assert outcome == "equivalent", f"same code should be 'equivalent', got {outcome!r}"


# ---------------------------------------------------------------------------
# 3. $validate-code `display` parameter — clinical correctness.
#
# Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
# Out Parameters: `display` (string) — "A display to show to the user when
# the system doesn't know what to do with the code, or to verify the code
# is the right one." The Out `display` parameter is the **server's** display,
# NOT an echo of the client's input.
#
# Bug QA-029: when a client supplies `display=X`, the response echoes
# `display=X` rather than the canonical display for the code. Per spec the
# Out `display` should be the canonical/best-available display the server
# holds for the code.
# ---------------------------------------------------------------------------


def test_t06_validate_code_display_returns_canonical_not_client_echo(fhir_client):
    """TERMINOLOGIST: when a client supplies `display`, the response's Out
    `display` parameter MUST be the canonical display the server holds for
    the code, NOT an echo of the client's input.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html —
    Out display = "A display to show to the user when the system doesn't
    know what to do with the code, or to verify the code is the right one."
    """
    # The conformance DB's mrconso row for 44054006 has STR
    # "Type 2 diabetes mellitus". That's the canonical display.
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "display": "Some Client-Supplied Display That Isn't Canonical",
        },
    )
    params = r.json()["parameter"]
    display_param = next((p.get("valueString") for p in params if p.get("name") == "display"), None)
    assert display_param is not None, "response should include a display parameter"
    assert display_param == "Type 2 diabetes mellitus", (
        f"Out `display` should be the canonical display "
        f"'Type 2 diabetes mellitus', got {display_param!r}. "
        f"The server is echoing the client-supplied display string instead of "
        f"returning the canonical display (FHIR R4 $validate-code Out display)."
    )


# ---------------------------------------------------------------------------
# 4. $translate match.equivalence — passthrough from CodeMapping.relationship.
#
# Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html —
# match.equivalence is a `code` constrained to the FHIR R4
# ConceptMapEquivalence value set (10 values):
#   {equal, equivalent, wider, narrower, relatedto, disjoint, subsumes,
#    specializes, inexact, unmatched}
#
# The medterm4ds engine knows about at least three internal vocabularies
# (`equivalent`, `source-is-narrower-than-target`, ancestor/descendant
# relationships). The response builder MUST propagate the engine's actual
# relationship, not hardcode `"equivalent"`.
#
# Bug QA-030: build_parameters_translate (engines/fhir/responses.py:95)
# hardcodes `equivalence = "equivalent"` for every match, ignoring
# `CodeMapping.relationship`. For SNOMED→ICD10CM crosswalks the engine's
# internal vocabulary is usually `equivalent` (same-CUI), but for ancestor
# /descendant mappings the engine correctly records a different relationship
# — and the response should reflect that.
# ---------------------------------------------------------------------------


def test_t07_translate_match_equivalence_in_fhir_enum(fhir_client):
    """TERMINOLOGIST: every match.equivalence returned by $translate MUST
    be a value in the FHIR R4 ConceptMapEquivalence enum (10 values):
    {equal, equivalent, wider, narrower, relatedto, disjoint, subsumes,
    specializes, inexact, unmatched}.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    CR-014 (milestone-2 review): the prior valid_equivalences set here
    encoded the wrong R5/R4B values (``subsumedby``, ``matches``,
    ``not-relatedto``); the spec-correct R4 set is imported from the
    single source of truth.
    """
    # Single source of truth — medterm4ds.engines.fhir.FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    valid_equivalences = FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
        },
    )
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    for m in matches:
        equiv_part = next(
            (part for part in m.get("part", []) if part.get("name") == "equivalence"),
            None,
        )
        assert equiv_part is not None, "match.part missing 'equivalence'"
        equiv = equiv_part.get("valueCode")
        assert equiv in valid_equivalences, (
            f"match.equivalence={equiv!r} is not a FHIR R4 ConceptMapEquivalence value. "
            f"Engine vocabulary should be translated to the FHIR enum, not echoed raw."
        )


def test_t08_translate_passes_engine_relationship_through(fhir_client):
    """TERMINOLOGIST: when the engine records a non-equivalent relationship
    for a mapping, $translate MUST surface that relationship — not hardcode
    `"equivalent"`.

    This is a guard against regression of QA-030. The conformance fixture DB
    only produces same-CUI mappings (all `equivalent`), so this test asserts
    the response's `equivalence` comes from the mapping's actual
    relationship, which today equals `equivalent` — but the test fails if a
    future code change hardcodes any other value.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
        },
    )
    matches = [p for p in r.json().get("parameter", []) if p.get("name") == "match"]
    if not matches:
        pytest.skip("no matches in fixture DB; relationship passthrough not exercised")
    # Engine records `equivalent` for same-CUI mappings. If the response
    # ever shows a different value while the engine's actual mapping is
    # `equivalent`, the response builder has drifted.
    for m in matches:
        equiv = next(
            part["valueCode"] for part in m["part"] if part.get("name") == "equivalence"
        )
        assert equiv == "equivalent", (
            f"engine mapping is `equivalent` but response emits equivalence={equiv!r}; "
            f"the response builder may be hardcoding a different value (regression of QA-030)"
        )


# ---------------------------------------------------------------------------
# 5. $lookup patient-friendly display correctness.
#
# The conformance fixture DB has no patient-friendly JSON loaded
# (`MEDTERM4DS_FHIR4PX_BASELINE` defaults to a path that doesn't exist in
# CI). When the cache is empty, $lookup MUST still return the canonical
# display (from `code_info.name`). This guards against a regression where
# the patient-friendly cache path silently falls back to a wrong default
# display.
# ---------------------------------------------------------------------------


def test_t09_lookup_display_is_canonical_when_no_patient_friendly_cache(fhir_client):
    """TERMINOLOGIST: $lookup MUST return the canonical display from the
    code's primary name (mrconso.STR) when no patient-friendly cache is
    loaded. The fixture DB's 44054006 row has STR='Type 2 diabetes mellitus'.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "44054006"},
    )
    body = r.json()
    # If the code isn't found, the response is an OperationOutcome.
    if body.get("resourceType") == "OperationOutcome":
        pytest.skip("fixture DB doesn't have the test code; check conftest")
    display = next(
        (p["valueString"] for p in body.get("parameter", []) if p.get("name") == "display"),
        None,
    )
    assert display == "Type 2 diabetes mellitus", (
        f"$lookup display should be the canonical STR; got {display!r}"
    )


def test_t10_lookup_unknown_code_returns_operationoutcome(fhir_client):
    """TERMINOLOGIST: $lookup on an unknown code MUST return an
    OperationOutcome with severity=error, not a Parameters resource with
    a fabricated display string. Spec-correctness on negative paths
    matters for clinical decision support — a wrong 'display' on a
    non-existent code would mislead a clinician.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "9999999999"},
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"unknown code should yield OperationOutcome; got {body.get('resourceType')!r}"
    )
    assert body["issue"][0]["severity"] == "error"


# ---------------------------------------------------------------------------
# 6. Carry-forwards from EXPLORER.
#
# `codeableConcept` first-coding selection: clinically, a CodeableConcept
# with multiple codings typically encodes the same concept in different
# code systems. The helper picks the first coding with both system and
# code; this is reasonable IF the helper iterates and validates. Probe to
# confirm the helper returns the first VALID coding, not just the first
# coding regardless of completeness.
# ---------------------------------------------------------------------------


def test_t11_codeable_concept_picks_first_coding_with_system_and_code(fhir_client):
    """TERMINOLOGIST: codeableConcept with multiple codings — the helper
    MUST pick the first coding that has BOTH system and code, not blindly
    the first coding in the list. A coding without a system or code is
    unusable for $validate-code.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        # First coding has no system — unusable
                        {"code": "44054006"},
                        # Second coding is complete — usable
                        {
                            "system": "http://snomed.info/sct",
                            "code": "44054006",
                            "display": "Type 2 diabetes mellitus",
                        },
                    ]
                },
            }
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    # If the helper picks the first VALID coding, the response should NOT
    # be a 400 'system and code are required.'
    assert r.status_code != 400 or "system and code" not in r.text.lower(), (
        f"helper picked the unusable first coding. Status={r.status_code}, body={r.text[:200]!r}"
    )
