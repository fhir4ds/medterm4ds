---
title: Source Inventory
---

Source inventory tools answer basic questions about the database.

Examples:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

terms.source_stats_df()
terms.sample_codes_df("SNOMEDCT_US", per_source=10)
terms.code_ttys_df("RXNORM", "847630")
```

Use inventory before running broad exports. It helps catch missing sources, unexpected term types, and release differences.

For the verified source list and counts, see [Supported Sources](./supported-sources.md).
