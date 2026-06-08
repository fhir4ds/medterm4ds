---
title: Notebook Examples
---

Notebook examples focus on reviewable workflows rather than isolated API calls.

Executable notebooks are available in the repository `notebooks/` directory:

- `terminology_lookup.ipynb`: lookup, search, source inventory, and term type review
- `patient_friendly_mapping_review.ipynb`: patient-friendly names and ICD10CM-to-SNOMED mapping review
- `valueset_optimization.ipynb`: optimize diagnosis value sets and inspect include/exclude rules
- `ndc_rxnorm_resolution.ipynb`: normalize NDCs, resolve RxCUIs, and preserve original medication identifiers

Notebook examples should keep provenance columns visible. Do not hide `match_type`, `match_depth`, `matched_via`, original source code, resolved code, or review flags.

Run the notebook smoke suite locally:

```bash
make notebook-smoke
```

Base pattern:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb", memory_profile="low")

df = terms.search_df("diabetes", sources=["ICD10CM", "SNOMEDCT_US"], limit=25)
df.head()
```

For mixed terminology work, build `CodeRef` inputs:

```python
refs = [
    mt.CodeRef("ICD10CM", "E11.9"),
    mt.CodeRef("RXNORM", "1049502"),
    mt.CodeRef("CVX", "208"),
]

terms.patient_friendly_df(refs)
```
