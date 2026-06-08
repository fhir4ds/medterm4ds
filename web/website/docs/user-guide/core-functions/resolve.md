---
title: Resolve
---

Resolve normalizes and updates code inputs before downstream work.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

row = terms.resolve("NDC", "0002-0821-01")
row.to_dict()
```

For historical batches:

```python
df = terms.resolve_df("NDC", ["0002-0821-01", "0002-0800-01"])
```

Resolution can identify active codes, historical exact matches, replacement targets, ambiguous replacements, missing codes, and NDC-to-RxCUI matches.
