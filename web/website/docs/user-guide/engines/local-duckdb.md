---
title: Local DuckDB Engine
---

The local DuckDB engine is the default for workstation, notebook, and bulk terminology workflows.

Use it when:

- terminology data should stay local
- users have a built UMLS DuckDB database
- workflows need lookup, mapping, hierarchy, optimize, or bulk export at scale
- memory needs to be bounded for commodity machines

Notebook example:

```python
import medterm4ds as mt

terms = mt.connect(
    "/mnt/d/medterm4ds/data/umls_current.duckdb",
    memory_profile="low",
)

terms.lookup("ICD10CM", "E11.9")
```

CLI example:

```bash
medterm4ds lookup \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --memory-profile low \
  --source ICD10CM \
  --code E11.9
```

The implementation class is `LocalDuckDBEngine`. The old `LocalLiteEngine` name remains as a compatibility alias for early adopters.
