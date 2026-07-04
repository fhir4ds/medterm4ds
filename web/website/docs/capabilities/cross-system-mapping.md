---
title: Cross-System Mapping
---

# Cross-System Mapping

Map codes between terminology systems (e.g., SNOMED CT to ICD-10-CM).

## Quick example

```python
import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb")

# SNOMED → ICD-10-CM
mappings = terms.map("SNOMEDCT_US", "44054006", target_sources=["ICD10CM"])
for m in mappings:
    print(f"{m.target.source} {m.target.code}: {m.target_display}")
# → ICD10CM E11: Type 2 diabetes mellitus
```

## How it works

Mapping uses two strategies:

### 1. Same-CUI mapping (exact)

Codes that share a UMLS Concept Unique Identifier (CUI) are mapped directly. This is the most reliable mapping — it means both codes represent the same medical concept.

### 2. Hierarchy-walking mapping (broader)

When a code has no direct CUI match to the target system, medterm4ds walks up the source hierarchy to find a parent code that DOES have a CUI match. The result is marked with the depth walked.

```python
# E11.65 has no direct SNOMED match, but its parent E11 does
mappings = terms.map("ICD10CM", "E11.65", target_sources=["SNOMEDCT_US"], max_depth=3)
for m in mappings:
    print(f"  depth={m.match_depth} type={m.match_type}")
# → depth=1 type=source_ancestor_same_cui
```

## Result fields

| Field | Description |
|---|---|
| `target.source` | Target code system |
| `target.code` | Target code |
| `target_display` | Display name |
| `match_type` | `same_cui` or `source_ancestor_same_cui` |
| `match_depth` | 0 = direct match, 1+ = hierarchy walk |

## Supported mappings

All 8 clinical sources can map to each other:

| From \ To | SNOMED | ICD-10 | RxNorm | LOINC | CPT | HCPCS | CVX |
|---|---|---|---|---|---|---|---|
| SNOMED | — | CUI | CUI | CUI | CUI | CUI | CUI |
| ICD-10-CM | CUI | — | — | — | — | — | — |
| RxNorm | CUI | — | — | — | — | — | — |

Most cross-system mappings go through SNOMED CT as the common hub via shared CUIs.

## FHIR $translate

```bash
curl "http://127.0.0.1:8001/fhir/ConceptMap/\$translate?system=http://snomed.info/sct&code=44054006&targetsystem=http://hl7.org/fhir/sid/icd-10-cm"
```
