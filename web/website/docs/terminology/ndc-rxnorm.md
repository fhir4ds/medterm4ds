---
title: NDC to RxNorm
---

NDC inputs are normalized to 11 digits and resolved through RxNorm attributes in `MRSAT.RRF`.

Examples:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

row = terms.resolve("NDC", "0002-0821-01")
row.to_dict()
```

Resolution output identifies:

- original NDC
- normalized NDC11
- RxNorm target code
- status
- resolution route

NDC handling matters for historical medication data. Some NDCs are obsolete, package-specific, or reused across source releases, so downstream drug analysis should preserve the resolved RxCUI and original NDC.
