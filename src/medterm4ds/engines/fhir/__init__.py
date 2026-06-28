"""FHIR R4 system URI mapping for the terminology facade."""

from __future__ import annotations

# Internal medterm4ds source name -> FHIR R4 canonical system URI.
SYSTEM_TO_FHIR_URI: dict[str, str] = {
    "SNOMEDCT_US": "http://snomed.info/sct",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10PCS": "http://hl7.org/fhir/sid/icd-10-pcs",
    "LNC": "http://loinc.org",
    "CPT": "http://www.ama-assn.org/go/cpt",
    "HCPCS": "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II",
    "CVX": "http://hl7.org/fhir/sid/cvx",
}

# FHIR R4 canonical URI -> internal source name (for request parsing).
FHIR_URI_TO_SYSTEM: dict[str, str] = {v: k for k, v in SYSTEM_TO_FHIR_URI.items()}

# Common aliases that FHIR clients may use instead of the canonical URI.
FHIR_URI_ALIASES: dict[str, str] = {
    "http://loinc.org/": "LNC",
    "urn:oid:2.16.840.1.113883.6.1": "LNC",
    "http://snomed.info/sct/": "SNOMEDCT_US",
    "urn:oid:2.16.840.1.113883.6.96": "SNOMEDCT_US",
    "urn:oid:2.16.840.1.113883.6.88": "RXNORM",
    "urn:oid:2.16.840.1.113883.6.90": "ICD10CM",
    "urn:oid:2.16.840.1.113883.6.4": "ICD10PCS",
    "urn:oid:2.16.840.1.113883.6.59": "CVX",
    "urn:oid:2.16.840.1.113883.6.12": "CPT",
}


def fhir_uri_to_system(uri: str) -> str | None:
    """Resolve a FHIR system URI to an internal medterm4ds source name."""
    if uri in FHIR_URI_TO_SYSTEM:
        return FHIR_URI_TO_SYSTEM[uri]
    if uri in FHIR_URI_ALIASES:
        return FHIR_URI_ALIASES[uri]
    stripped = uri.rstrip("/")
    if stripped in FHIR_URI_TO_SYSTEM:
        return FHIR_URI_TO_SYSTEM[stripped]
    if stripped in FHIR_URI_ALIASES:
        return FHIR_URI_ALIASES[stripped]
    return None


def system_to_fhir_uri(source: str) -> str | None:
    """Convert an internal source name to a FHIR canonical system URI."""
    return SYSTEM_TO_FHIR_URI.get(source)
