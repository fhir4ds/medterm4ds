---
title: DataFrames
---

The notebook facade has DataFrame methods for the most common workflows.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

df = terms.lookup_df("ICD10CM", ["E11.9", "E11.40"])
```

Use `_df` methods when you want pandas or Polars output directly:

```python
friendly = terms.patient_friendly_df("ICD10CM", ["E11.9", "E11.40"])
mapping = terms.map_df(
    "ICD10CM",
    ["E11.9", "E11.40"],
    target_sources=["SNOMEDCT_US"],
)
hierarchy = terms.hierarchy_df("ICD10CM", "E11.9", direction="ancestors", max_depth=3)
search = terms.search_df("metformin", sources=["RXNORM"], limit=20)
```

Set `backend="polars"` when Polars is installed:

```python
df = terms.lookup_df("ICD10CM", ["E11.9"], backend="polars")
```

Lower-level helpers are still available for service results:

```python
from medterm4ds.outputs import to_dataframe

rows = terms.map("ICD10CM", "E11.9", target_sources=["SNOMEDCT_US"])
df = to_dataframe(rows)
```

Keep provenance fields visible during review, especially `match_type`, `match_depth`, and `matched_via`.
