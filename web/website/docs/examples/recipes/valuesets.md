---
title: ValueSets
---

Value set work is one of the main reasons to use medterm4ds.

Typical tasks:

- inspect source inventory
- resolve obsolete codes
- optimize enumerations into include/exclude rules
- map across vocabularies
- export reviewable tables
- preserve provenance for every decision

Start with source stats:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

terms.source_stats_df(["ICD10CM", "SNOMEDCT_US"])
```

Then optimize:

```python
codes = ["E11.40", "E11.41", "E11.42"]
optimized = terms.optimize("ICD10CM", codes)
optimized.to_dict()
```

Map and review before publishing:

```python
mapping = terms.map_df(
    "ICD10CM",
    codes,
    target_sources=["SNOMEDCT_US"],
    max_depth=2,
)
```

Do not publish optimized rules without review.
