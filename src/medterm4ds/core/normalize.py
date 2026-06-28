"""Source vocabulary normalization."""

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
