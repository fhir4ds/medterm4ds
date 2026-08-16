"""Source vocabulary normalization."""

# Lowercase source label per source — used by SearchResult.system_label and
# ExtractionResult.system_label (previously duplicated inline in both files).
# Adding a new source here updates both surfaces; missing one means search
# hits and extraction hits for that source render with the raw SAB as the
# system URL.
SOURCE_LABELS: dict[str, str] = {
    "SNOMEDCT_US": "snomedct_us",
    "RXNORM": "rxnorm",
    "ICD10CM": "icd10",
    "ICD10PCS": "icd10pcs",
    "LNC": "lnc",
    "CPT": "cpt",
    "HCPCS": "hcpcs",
    "CVX": "cvx",
}


def source_label(source: str) -> str:
    """Return the lowercase system label for a source, defaulting to lower(source)."""
    return SOURCE_LABELS.get(source, source.lower())


SOURCE_MAP = {
    "LOINC": "LNC",
    "LNC": "LNC",
    "ICD10CM": "ICD10CM",
    "ICD10-CM": "ICD10CM",
    "ICD10PCS": "ICD10PCS",
    "ICD10-PCS": "ICD10PCS",
    "SNOMED": "SNOMEDCT_US",
    "SNOMEDCT": "SNOMEDCT_US",
    "SNOMEDCT_US": "SNOMEDCT_US",
    "SNOMED CT": "SNOMEDCT_US",
    "RXNORM": "RXNORM",
    "RXN": "RXNORM",
    "NDC": "NDC",
    "NDC11": "NDC",
    "ATC": "ATC",
    "MSH": "MSH",
    "MESH": "MSH",
    "CPT": "CPT",
    "HCPCS": "HCPCS",
    "CVX": "CVX",
}


def normalize_source(source: str) -> str:
    """Normalize source aliases to UMLS source abbreviations."""
    if not source or not source.strip():
        return source
    return SOURCE_MAP.get(source.strip().upper(), source.strip().upper())


def validate_source_sab(source: str) -> None:
    """Reject empty/whitespace and URI/OID-form source names.

    QC-422 (MEDIUM): the CLI (_code_source_pairs, QC-324/378) and MCP
    (_validate_source_sab, QC-322/389) boundaries reject these inputs, but
    the Python facade funneled the same inputs into the service layer, which
    returned success-shaped empty data. Canonical messages live here so every
    surface inherits identical diagnostics. An UNKNOWN-but-well-formed SAB is
    valid (it legitimately resolves to not-found); only malformed forms are
    rejected.
    """
    if not source.strip():
        raise ValueError(
            "source must be a non-empty vocabulary name (UMLS SAB, "
            f"e.g. SNOMEDCT_US), got {source!r}."
        )
    if "://" in source or source.lower().startswith("urn:oid:"):
        raise ValueError(
            f"source expects a UMLS SAB string (e.g. SNOMEDCT_US), got "
            f"{source!r} (looks like a URI/OID). FHIR URIs are not accepted "
            f"here; use the SAB form."
        )


def validate_code_nonempty(code: str) -> None:
    """Reject empty/whitespace code values (QC-324 empty-code family).

    An empty code is never a valid invocation — reject with a diagnostic
    instead of a success-shaped null-field record.
    """
    if not code.strip():
        raise ValueError(f"code must be a non-empty string, got {code!r}")


def normalize_icd10_to_category(code: str) -> str:
    """Map an ICD-10-CM code to its category-level parent.

    ICD-10-CM codes have a regular structure: letter + 2 digits + optional
    decimal detail. E.g., E11.65 → E11, E11.651 → E11, J45.909 → J45.

    The condition_associations.json table is keyed by category codes (E11, J45, etc.).
    This function strips the decimal portion so that child codes resolve to
    their category-level canonical code for association lookup.

    >>> normalize_icd10_to_category("E11.65")
    'E11'
    >>> normalize_icd10_to_category("E11")
    'E11'
    >>> normalize_icd10_to_category("A00.0")
    'A00'
    """
    if "." in code:
        return code.split(".")[0]
    return code
