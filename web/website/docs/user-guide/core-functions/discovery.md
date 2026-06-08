---
title: Discovery
---

Discovery tools inspect what is available in the terminology database.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

terms.source_stats_df(["ICD10CM", "RXNORM", "LNC"])
terms.sample_codes_df("LNC", per_source=10)
terms.code_ttys_df("RXNORM", "847630")
terms.search_df("insulin", sources=["RXNORM"], limit=20)
```

Discovery is the base layer for source inventory and domain-specific search tools.
