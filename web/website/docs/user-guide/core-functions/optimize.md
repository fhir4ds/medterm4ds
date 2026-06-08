---
title: Optimize
---

Optimize compacts a value set into include/exclude rules.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

result = terms.optimize("ICD10CM", ["E11.40", "E11.41", "E11.42"])
result.to_dict()
```

Use optimize for value set maintenance and review. Optimized rules should be reviewed before publication.
