---
title: Obsolete Codes
---

Historical data often contains obsolete or suppressed codes.

medterm4ds resolution can return:

- active exact matches
- historical exact matches
- replacement targets
- ambiguous replacements
- missing codes
- NDC-to-RxCUI resolutions

Example:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

row = terms.resolve("RXNORM", "1190798")
row.to_dict()
```

Preserve the original code in downstream outputs. Replacement targets are useful, but they are not always clinically equivalent.
