---
title: First Notebook
---

Most medterm4ds work is expected to happen in Python notebooks. Keep one
connection open for the notebook session, then use typed results or DataFrames
depending on the task.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb", memory_profile="low")
```

Search terminology before choosing codes:

```python
terms.search_df("diabetes", sources=["ICD10CM", "SNOMEDCT_US"], limit=10)
```

Review exact lookup and patient-friendly names:

```python
codes = ["E11.9", "E11.40", "E11.42"]

lookup = terms.lookup_df("ICD10CM", codes)
friendly = terms.patient_friendly_df("ICD10CM", codes)

friendly[["source", "code", "name", "match_type", "match_depth", "matched_via"]]
```

Map codes and keep provenance visible:

```python
mapping = terms.map_df(
    "ICD10CM",
    codes,
    target_sources=["SNOMEDCT_US"],
    max_depth=2,
)

mapping[
    [
        "source",
        "code",
        "target_source",
        "target_code",
        "target_display",
        "match_type",
        "match_depth",
        "matched_via",
    ]
]
```

Optimize a value set:

```python
optimized = terms.optimize("ICD10CM", codes)
optimized.to_dict()
```

Close the connection when the notebook is finished:

```python
terms.close()
```
