---
title: Quickstart
---

Start from Python. The common notebook pattern is to connect once, then call
terminology methods that return typed objects or DataFrames.

```python
import medterm4ds as mt

DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
terms = mt.connect(DB, memory_profile="low")
```

Look up a code:

```python
info = terms.lookup("ICD10CM", "E11.9")
info.to_dict()
```

Get a patient-friendly name:

```python
friendly = terms.patient_friendly("ICD10CM", "E11.9")
friendly.to_dict()
```

Map a diagnosis code to SNOMED CT:

```python
mappings = terms.map("ICD10CM", "E11.9", target_sources=["SNOMEDCT_US"])
[row.to_dict() for row in mappings]
```

Optimize a value set:

```python
optimized = terms.optimize("ICD10CM", ["E11.40", "E11.41", "E11.42"])
optimized.to_dict()
```

Work directly with pandas:

```python
df = terms.patient_friendly_df("ICD10CM", ["E11.9", "E11.40", "E11.42"])
df[["source", "code", "name", "match_type", "match_depth"]]
```

Use the CLI for automation, bulk exports, and files:

```bash
medterm4ds bulk patient-friendly \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM \
  --limit 100 \
  --output patient-friendly-icd10.jsonl
```
