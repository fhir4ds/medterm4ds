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
    "ATC": "ATC",
    "MSH": "MSH",
    "MESH": "MSH",
    "CPT": "CPT",
    "HCPCS": "HCPCS",
    "CVX": "CVX",
}


def normalize_source(source: str) -> str:
    """Normalize source aliases to UMLS source abbreviations."""
    return SOURCE_MAP.get(source.upper(), source.upper())
