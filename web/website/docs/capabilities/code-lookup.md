---
title: Code Lookup & Patient-Friendly Names
---

# Code Lookup & Patient-Friendly Names

Look up a code to get its display name, properties, and patient-friendly name.

## Quick example

```python
import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb")

# Exact lookup
info = terms.lookup("SNOMEDCT_US", "44054006")
print(info.name)  # "Type 2 diabetes mellitus"

# Patient-friendly name
result = terms.patient_friendly("SNOMEDCT_US", "44054006")
print(result.name)  # "Diabetes Type 2"
```

## What you get

| Field | Description | Example |
|---|---|---|
| `code` | The code | `"44054006"` |
| `name` | Canonical display | `"Type 2 diabetes mellitus"` |
| `friendly_source` | Where the friendly name came from | `"MEDLINEPLUS"` |
| `match_type` | How it was resolved | `"exact"`, `"broader"`, `"same_cui"` |
| `cui` | UMLS Concept ID | `"C0011860"` |

## Patient-friendly resolution

The patient-friendly resolver walks the source hierarchy to find the most consumer-comprehensible display name for any code. It prefers MEDLINEPLUS names, falls back to CHV (Consumer Health Vocabulary), then to the shortest active atom.

Resolution strategies:
- **Exact**: same-CUI match to MEDLINEPLUS/CHV
- **Broader**: walks up the hierarchy to find a parent with a friendly name
- **Same CUI**: SNOMED-to-target mapping via shared concept
- **Original**: falls back to the source's preferred term

## Batch lookup

```python
# Batch: preserves input order, returns None for missing codes
infos = terms.lookup_batch([
    ("ICD10CM", "E11.9"),
    ("SNOMEDCT_US", "44054006"),
    ("RXNORM", "860975"),
])

# DataFrame-friendly
df = terms.lookup_df("ICD10CM", ["E11.9", "E11.40", "I10"])
```

## Supported sources

| Source | Codes | TTY filter |
|---|---|---|
| SNOMED CT US | 386,110 | All active |
| ICD-10-CM | 98,506 | All |
| ICD-10-PCS | 192,560 | All |
| RxNorm | 124,919 | IN, MIN, SCD, SBD, SCDG, etc. |
| LOINC | 301,558 | LN only |
| CPT | 15,468 | All |
| HCPCS | 7,685 | All |
| CVX | 288 | All |
