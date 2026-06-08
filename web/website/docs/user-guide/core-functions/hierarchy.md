---
title: Hierarchy
---

Hierarchy tools traverse parent, child, ancestor, and descendant relationships.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

parents = terms.parents("SNOMEDCT_US", "44054006")
ancestors = terms.hierarchy_df(
    "SNOMEDCT_US",
    "44054006",
    direction="ancestors",
    max_depth=3,
)
```

Hierarchy is used directly and also supports mapping fallback, optimize, and domain wrappers.
