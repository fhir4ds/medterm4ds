"""TERMINOLOGIST probes for TS-01 (Terminology Service RESTful API Conformance, §4.7.1.1).

Source: https://build.fhir.org/terminology-service.html §4.7.1.1
        https://terminology.hl7.org/5.5.0/CodeSystem-hcpcs-Level-II.html
Fixture: tests/fhir_conformance/conftest.py::fhir_client (synthetic DB)

TERMINOLOGIST lens: clinical / terminological correctness. Verify that
the CapabilityStatement and TerminologyCapabilities advertise code
systems using the canonical FHIR R4 system URIs that a clinical client
would use in a Coding.system field — not the THO CodeSystem resource
URLs.
"""

from __future__ import annotations

import pytest

from medterm4ds.engines.fhir import (
    FHIR_URI_ALIASES,
    FHIR_URI_TO_SYSTEM,
    SYSTEM_TO_FHIR_URI,
    fhir_uri_to_system,
)


# Canonical FHIR R4 system URIs as published by HL7 / owning authorities.
# Sources:
#   - SNOMED CT: https://terminology.hl7.org/3.0.2/SNOMEDCT.html
#   - RxNorm:    https://terminology.hl7.org/3.0.2/RXNORM.html
#   - ICD-10-CM: https://terminology.hl7.org/3.1.0/CodeSystem-icd10CM.html
#   - ICD-10-PCS:https://terminology.hl7.org/3.1.0/CodeSystem-icd10PCS.html
#   - LOINC:     https://terminology.hl7.org/3.0.2/LOINC.html
#   - CPT:       https://terminology.hl7.org/3.0.2/CPT.html
#   - HCPCS:     https://terminology.hl7.org/5.5.0/CodeSystem-hcpcs-Level-II.html
#                ("Official URL: http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets")
#   - CVX:       https://terminology.hl7.org/3.0.2/CVX.html
CANONICAL_FHIR_R4_URIS = {
    "SNOMEDCT_US": "http://snomed.info/sct",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10PCS": "http://hl7.org/fhir/sid/icd-10-pcs",
    "LNC": "http://loinc.org",
    "CPT": "http://www.ama-assn.org/go/cpt",
    "HCPCS": "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets",
    "CVX": "http://hl7.org/fhir/sid/cvx",
}


# --- Probe T01: SYSTEM_TO_FHIR_URI values are canonical (regression for QA-012) ---
@pytest.mark.parametrize("source", sorted(CANONICAL_FHIR_R4_URIS))
def test_t01_canonical_uri_no_drift(source):
    """Each SYSTEM_TO_FHIR_URI value MUST match the canonical FHIR R4 system
    URI published by the owning authority (HL7 / CMS / AMA / NLM / CDC).

    TERMINOLOGIST finding (QA-012): SYSTEM_TO_FHIR_URI['HCPCS'] was set to
    the THO CodeSystem resource URL
    ('http://terminology.hl7.org/CodeSystem/hcpcs-Level-II') rather than
    the canonical system URI used in Coding.system fields. Per HL7 THO
    v5.5.0 the canonical URI is
    'http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets'.

    Clinical impact: a client using the advertised URI in a Coding.system
    field would publish non-conformant data. This probe prevents future
    drift by pinning every URI to its canonical value.
    """
    expected = CANONICAL_FHIR_R4_URIS[source]
    actual = SYSTEM_TO_FHIR_URI.get(source)
    assert actual == expected, (
        f"SYSTEM_TO_FHIR_URI[{source!r}] drift detected.\n"
        f"  expected (canonical): {expected}\n"
        f"  actual:               {actual}\n"
        f"Reference: HL7 THO published Official URL for each code system."
    )


# --- Probe T02: TerminologyCapabilities advertises canonical URIs ---
@pytest.mark.parametrize(
    "source,expected_uri",
    sorted(CANONICAL_FHIR_R4_URIS.items()),
)
def test_t02_terminology_capabilities_canonical_uris(fhir_client, source, expected_uri):
    """`/fhir/metadata?mode=terminology` returns TerminologyCapabilities whose
    codeSystem[].uri entries match the canonical FHIR R4 system URIs.

    TERMINOLOGIST finding (QA-012): the HCPCS entry advertised the wrong
    URI on the wire. Clinical clients reading the advertisement to learn
    which URI to use would propagate the error.
    """
    r = fhir_client.get("/fhir/metadata?mode=terminology")
    assert r.status_code == 200
    body = r.json()
    uris = {cs["uri"] for cs in body.get("codeSystem", [])}
    assert expected_uri in uris, (
        f"TerminologyCapabilities.codeSystem[] is missing canonical URI for "
        f"{source}: {expected_uri!r}. "
        f"Advertised URIs: {sorted(uris)}"
    )


# --- Probe T03: reverse map resolves canonical URI back to source ---
@pytest.mark.parametrize("source,expected_uri", sorted(CANONICAL_FHIR_R4_URIS.items()))
def test_t03_reverse_map_canonical_uri(source, expected_uri):
    """fhir_uri_to_system(canonical_uri) MUST resolve back to the source.

    The reverse map is auto-derived from SYSTEM_TO_FHIR_URI, so this probe
    fails closed if anyone introduces drift in the forward map without
    also updating the canonical-to-source resolution path.
    """
    assert FHIR_URI_TO_SYSTEM.get(expected_uri) == source, (
        f"FHIR_URI_TO_SYSTEM[{expected_uri!r}] = "
        f"{FHIR_URI_TO_SYSTEM.get(expected_uri)!r}, expected {source!r}"
    )
    assert fhir_uri_to_system(expected_uri) == source


# --- Probe T04: backwards-compat alias for the prior wrong HCPCS URI ---
def test_t04_legacy_hcpcs_uri_alias_resolves():
    """The prior (incorrect) HCPCS URI was the THO CodeSystem resource URL.
    Keep it as a backwards-compat alias in FHIR_URI_ALIASES so existing
    clients that learned the wrong URI still resolve to HCPCS.

    This is a soft-deprecation path: the canonical URI is fixed, but
    inbound requests using the old URI don't break. Per GLOBAL_RULES.md
    silent-fallback prohibition, this alias is explicit (a known override
    with a comment in __init__.py), not a fallback heuristic.
    """
    legacy = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
    assert FHIR_URI_ALIASES.get(legacy) == "HCPCS"
    assert fhir_uri_to_system(legacy) == "HCPCS"


# --- Probe T05: $lookup with canonical HCPCS URI resolves (smoke) ---
def test_t05_lookup_resolves_canonical_hcpcs_uri(fhir_client):
    """$lookup with the canonical HCPCS URI must resolve to the HCPCS source.

    Prior to QA-012, sending the canonical URI
    'http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets' would have
    returned 400 'Unrecognized system URI' because the reverse map only
    knew the wrong URI. This probe locks in the fix end-to-end.
    """
    canonical = CANONICAL_FHIR_R4_URIS["HCPCS"]
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": canonical, "code": "NONEXISTENT_QA_PROBE"},
    )
    # The code doesn't exist in the synthetic DB, so the response is 200
    # OperationOutcome 'not-found' — but the system URI was accepted.
    # A 400 here would mean the URI was rejected, indicating regression.
    assert r.status_code == 200, (
        f"$lookup with canonical HCPCS URI returned HTTP {r.status_code}; "
        f"the URI should be recognized. Body: {r.text}"
    )
