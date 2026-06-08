---
title: NDC to RxCUI
---

Resolve an NDC in Python:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

resolution = terms.resolve("NDC", "0002-0821-01")
resolution.to_dict()
```

NDC inputs can include dashes. medterm4ds normalizes to NDC11 where possible
and resolves through RxNorm attributes.

For batches, use a DataFrame:

```python
df = terms.resolve_df("NDC", ["0002-0821-01", "0002-0800-01"])
df[["source", "code", "resolved_source", "resolved_code", "status", "match_type"]]
```

Use the CLI for file-based checks:

```bash
medterm4ds resolve \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source NDC \
  --code 0002-0821-01 \
  --format table
```

For historical pharmacy data, keep both the original NDC and resolved RxCUI.
