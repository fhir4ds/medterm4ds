---
title: ICD-10 to SNOMED
---

In notebooks, map one code:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

mappings = terms.map("ICD10CM", "E11.9", target_sources=["SNOMEDCT_US"])
[row.to_dict() for row in mappings]
```

Map a batch and review provenance in a DataFrame:

```python
df = terms.map_df(
    "ICD10CM",
    ["E11.9", "E11.40", "E11.42"],
    target_sources=["SNOMEDCT_US"],
    max_depth=2,
)

df[
    [
        "source",
        "code",
        "target_source",
        "target_code",
        "target_display",
        "relationship",
        "match_type",
        "match_depth",
        "matched_via",
    ]
]
```

Use the CLI when you need terminal output:

```bash
medterm4ds map \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source ICD10CM \
  --target-source SNOMEDCT_US \
  --code E11.9 \
  --format table
```

Export a bounded sample with the CLI:

```bash
medterm4ds bulk map \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source ICD10CM \
  --target-source SNOMEDCT_US \
  --limit 1000 \
  --output icd10cm-snomed.jsonl
```

Review `match_type`, `match_depth`, and `matched_via` before using the output clinically.
