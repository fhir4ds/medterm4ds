---
title: Lookup
---

Lookup returns exact terminology records for source/code pairs.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

info = terms.lookup("LNC", "4548-4")
info.to_dict()
```

For batches:

```python
df = terms.lookup_df("LNC", ["4548-4", "17856-6"])
```

Returned fields are based on `CodeInfo`: source, code, name, CUI, term type, and suppress status.
