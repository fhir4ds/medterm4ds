---
title: Patient-Friendly Names
---

# Patient-Friendly Names

Translate clinical codes into language patients can understand. The resolver prefers MEDLINEPLUS names, falls back to CHV (Consumer Health Vocabulary), then to the shortest active atom.

## Quick example

```python
import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb")

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

## Resolution strategies

The patient-friendly resolver walks the source hierarchy to find the most consumer-comprehensible display name for any code.

- **Exact**: same-CUI match to MEDLINEPLUS/CHV
- **Broader**: walks up the hierarchy to find a parent with a friendly name
- **Same CUI**: SNOMED-to-target mapping via shared concept
- **Original**: falls back to the source's preferred term

## See also

- [Code Lookup](./code-lookup.md)
- [Patient-Friendly Names recipe](../examples/recipes/patient-friendly-names.md)
