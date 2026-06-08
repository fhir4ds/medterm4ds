---
title: ValueSet Optimization
---

Optimize a set of ICD10CM child codes in Python:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

result = terms.optimize(
    "ICD10CM",
    ["E11.40", "E11.41", "E11.42", "E11.43", "E11.44"],
)

result.to_dict()
```

Expected shape:

```python
{
    "source": "ICD10CM",
    "relationship": "isa",
    "rules": [{"include_source": "ICD10CM", "include": "E11.4", "exclude": ["E11.49"]}],
}
```

Use the CLI when you want a compact tree in the terminal:

```bash
medterm4ds optimize \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source ICD10CM \
  --code E11.40 \
  --code E11.41 \
  --code E11.42 \
  --code E11.43 \
  --code E11.44 \
  --format tree
```

CLI tree shape:

```text
include E11.4
exclude E11.49
```

This is compact, but the result still needs review against the intended value set definition.
