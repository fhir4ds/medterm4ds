"""FHIR R4 system URI mapping for the terminology facade."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Internal medterm4ds source name -> FHIR R4 canonical system URI.
SYSTEM_TO_FHIR_URI: dict[str, str] = {
    "SNOMEDCT_US": "http://snomed.info/sct",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10PCS": "http://hl7.org/fhir/sid/icd-10-pcs",
    "LNC": "http://loinc.org",
    "CPT": "http://www.ama-assn.org/go/cpt",
    "HCPCS": "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets",
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
    # Backwards-compat: the prior (incorrect) HCPCS URI was the THO
    # CodeSystem resource URL rather than the canonical system URI. Keep
    # it as an alias so existing clients that learned the wrong URI still
    # resolve. Per HL7 THO v5.5.0 the canonical URI is
    # http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets (see QA-012).
    "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II": "HCPCS",
}


def fhir_uri_to_system(uri: str) -> str | None:
    """Resolve a FHIR system URI to an internal medterm4ds source name.

    Per RFC 3986 §3.1 (referenced by FHIR R4 §3.1.0.1.9 for HTTP semantics):
    'Although schemes are case-insensitive... An implementation should accept
    uppercase letters as equivalent to lowercase in scheme names.' The
    registries ``FHIR_URI_TO_SYSTEM`` and ``FHIR_URI_ALIASES`` are keyed by
    canonical lowercase-scheme URIs; without scheme normalization, an
    uppercase-scheme URI (e.g. ``HTTP://snomed.info/sct``) would fail the
    exact-string lookup and be rejected. Found by EXPLORER iteration TS-03
    (QA-001).

    Note: per RFC 3986 §3.2.1 the path is case-sensitive and per §3.2.2 the
    host is case-insensitive — but this function does NOT normalize path or
    host case. Only the SCHEME is normalized (the narrowest fix required by
    RFC 3986 §3.1's SHOULD). Host case-insensitivity is a separate
    enhancement (e.g. ``HTTP://SNOMED.INFO/sct`` would still be rejected
    because the host is uppercase).
    """
    from urllib.parse import urlparse

    # Normalize scheme to lowercase. urlparse parses the scheme
    # case-insensitively (``urlparse("HTTP://x").scheme == "http"``), but
    # the ``.scheme`` attribute is already lowercased — we just need to
    # reconstruct the URI string via ``geturl()`` so the registry lookup
    # sees the canonical lowercase-scheme form. Reconstruct unconditionally
    # when the URI has a scheme (cheap operation; idempotent for already-
    # lowercase inputs).
    parsed = urlparse(uri)
    if parsed.scheme:
        uri = parsed.geturl()
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


def canonical_system_uri(system_uri: str, *, source: str | None = None) -> str:
    """Re-resolve a (possibly aliased / trailing-slash) system URI to the
    canonical FHIR R4 URI.

    Single source of truth for the canonical-re-resolution pattern used by
    every ``_do_*`` handler that emits an Out ``system`` parameter (CS-02
    HISTORIAN QA-047, CS-03 HISTORIAN QA-051). Structural fix for the
    client-input-as-canonical drift pattern (count=7 PROMOTED at milestone 2)
    — without this helper, every new ``_do_*`` handler that emits Out
    ``system`` is a new instance of the pattern (CR-011, CR-012, CR-013 in
    review-10.md).

    Behavior:
      1. If ``source`` is supplied AND resolves via ``system_to_fhir_uri``,
         use the canonical URI from ``SYSTEM_TO_FHIR_URI``.
      2. Otherwise re-resolve ``system_uri`` via ``fhir_uri_to_system`` and
         then ``system_to_fhir_uri``.
      3. Fall back to the client-supplied ``system_uri`` only when no
         canonical exists. Log at WARNING per GLOBAL_RULES.md "Silent
         Fallbacks" — silent raw-alias emission is silent-wrong-answer if
         a future source addition outpaces the registries.

    Args:
        system_uri: The (possibly aliased / trailing-slash) system URI
            supplied by the client.
        source: Optional internal source name if already resolved by the
            caller (skips the ``fhir_uri_to_system`` step).

    Returns:
        The canonical FHIR R4 URI when resolvable; else ``system_uri``.
    """
    if source is None:
        source = fhir_uri_to_system(system_uri)
    canonical = system_to_fhir_uri(source) if source else None
    if canonical:
        return canonical
    if system_uri:
        logger.warning(
            "canonical_system_uri: no canonical FHIR URI for system_uri=%r "
            "(source=%r) — emitting client-supplied value. If this is a new "
            "code system, add it to SYSTEM_TO_FHIR_URI in "
            "engines/fhir/__init__.py.",
            system_uri, source,
        )
    return system_uri


# Lowercase raw UMLS SAB labels stored in patient-friendly JSON artifacts
# (e.g. /mnt/d/medterm4ds/reports/fhir4px/patient_friendly_*.json) → canonical
# internal source name. The patient-friendly `canonical_system` field uses
# these raw labels (e.g. "icd10", "snomedct_us") rather than the canonical
# FHIR URI; this map normalizes them before emitting in $lookup responses.
# Per GLOBAL_RULES.md "Single Source of Truth": do NOT redefine the FHIR URI
# map downstream — translate to internal source name first, then call
# `system_to_fhir_uri`. Found by SKEPTIC iteration CS-01 (QA-043).
_SAB_LABEL_TO_SOURCE: dict[str, str] = {
    "snomedct_us": "SNOMEDCT_US",
    "rxnorm": "RXNORM",
    # Patient-friendly JSON uses "icd10" for ICD-10-CM (the most common
    # ICD-10 variant in UMLS). Map to ICD10CM; if a future entry uses
    # "icd10cm" explicitly, that also resolves via .upper() fallback below.
    "icd10": "ICD10CM",
    "icd10cm": "ICD10CM",
    "icd10pcs": "ICD10PCS",
    "lnc": "LNC",
    "cpt": "CPT",
    "hcpcs": "HCPCS",
    "cvx": "CVX",
}


def sab_label_to_fhir_uri(sab_label: str) -> str | None:
    """Translate a raw UMLS SAB label (lowercase, as stored in patient-friendly
    JSON artifacts) to the canonical FHIR R4 system URI.

    Returns None if the label is unrecognized (caller should fall back to the
    raw value, which is more useful than None for diagnostic purposes).

    Found by SKEPTIC iteration CS-01 (QA-043): the patient-friendly JSON stores
    `canonical_system` as the raw SAB label rather than the FHIR URI; the
    $lookup handler echoed it verbatim, producing e.g. `"icd10"` where the
    spec-correct value is `http://hl7.org/fhir/sid/icd-10-cm`.
    """
    if not sab_label:
        return None
    source = _SAB_LABEL_TO_SOURCE.get(sab_label.lower())
    if source is None:
        # Last-resort: try direct upper-case match against SYSTEM_TO_FHIR_URI keys.
        source = sab_label.upper()
    return system_to_fhir_uri(source)


# =============================================================================
# FHIR R4 closed-enum registries — single source of truth.
# =============================================================================
# Per milestone-2 review (CR-014): these enums were hardcoded in 5+ test files
# with WRONG values (R5/R4B `subsumedby`, R5-only `matches`, omitted R4
# `specializes`). That made the test-suite-as-contract actively protect the
# wrong values, blocking CM-* chunks from safely fixing
# `_INTERNAL_REL_TO_FHIR_EQUIVALENCE` (CF-HISTORIAN-VS01-01). The frozen-set
# constants below are imported by both production code and tests so future
# drift is impossible.
#
# Canonical R4 spec (verified 2026-07-13):
#   https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
#   https://hl7.org/fhir/R4/valueset.html#filter
FHIR_R4_CONCEPT_MAP_EQUIVALENCE: frozenset[str] = frozenset({
    "relatedto",
    "equivalent",
    "equal",
    "wider",
    "narrower",
    "subsumes",
    # NOTE: R4 uses ``specializes`` (NOT R5/R4B ``subsumedby``) for the
    # reverse-of-subsumes case. CF-HISTORIAN-VS01-01.
    "specializes",
    "inexact",
    "unmatched",
    "disjoint",
})

FHIR_R4_FILTER_OPERATORS: frozenset[str] = frozenset({
    "=",
    "is-a",
    # NOTE: spec spelling is ``descendent-of`` (Latin-derived), NOT
    # ``descendant-of`` (common English). VS-01 SKEPTIC QA-054.
    "descendent-of",
    "is-not-a",
    "regex",
    "in",
    "not-in",
    "generalizes",
    "exists",
})
