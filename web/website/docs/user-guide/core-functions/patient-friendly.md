---
title: Patient-Friendly Names
---

Patient-friendly naming produces consumer-oriented labels where terminology data supports them.

For notebook review:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

df = terms.patient_friendly_df(
    "ICD10CM",
    ["E11.9", "E11.40", "E11.42"],
)
df[["source", "code", "name", "friendly_source", "match_type", "match_depth"]]
```

For bulk file output:

```bash
medterm4ds bulk patient-friendly \
  --db "$DB" \
  --sources ICD10CM,LNC,RXNORM,CVX,CPT,HCPCS \
  --output patient_friendly.jsonl \
  --format jsonl \
  --memory-profile low
```

Results preserve original code, resolved code, selected name, match type, match depth, and route.
