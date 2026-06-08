---
title: Patient-Friendly Names
---

In notebooks, start with the Python facade:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

df = terms.patient_friendly_df("ICD10CM", ["E11.9", "E11.40", "E11.42"])
df[["source", "code", "name", "friendly_source", "match_type", "match_depth"]]
```

Build patient-friendly ConceptMap rows:

```python
conceptmap = terms.conceptmap_df("ICD10CM", ["E11.9", "E11.40"])
conceptmap[["source", "code", "target_display", "relationship", "match_type"]]
```

Use the CLI when you need a file export. Generate a small patient-friendly
sample:

```bash
medterm4ds bulk patient-friendly \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM \
  --limit 100 \
  --output patient-friendly-icd10.jsonl \
  --format jsonl
```

For downstream interoperability, export FHIR JSON from the CLI:

```bash
medterm4ds conceptmap patient-friendly \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM \
  --output patient-friendly-icd10.json \
  --format fhir-json
```
