---
title: Mapping
---

Mapping finds target vocabulary candidates for source codes.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

mappings = terms.map("ICD10CM", "E11.9", target_sources=["SNOMEDCT_US"])
[row.to_dict() for row in mappings]
```

For review tables:

```python
df = terms.map_df(
    "ICD10CM",
    ["E11.9", "E11.40"],
    target_sources=["SNOMEDCT_US"],
    max_depth=2,
)
```

Review `match_type`, `match_depth`, and `matched_via` before using mappings downstream.
