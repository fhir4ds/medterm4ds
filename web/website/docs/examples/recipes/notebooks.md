---
title: Notebooks
---

Notebook workflows should use the Python facade and DataFrame helpers rather
than shelling out to the CLI. The CLI is best for repeatable file exports,
validation scripts, and scheduled jobs.

Typical setup:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb", memory_profile="low")
```

Common review workflow:

```python
codes = ["E11.9", "E11.40", "E11.42"]

friendly = terms.patient_friendly_df("ICD10CM", codes)
mapping = terms.map_df(
    "ICD10CM",
    codes,
    target_sources=["SNOMEDCT_US"],
    max_depth=2,
)

friendly[["source", "code", "name", "match_type", "match_depth", "matched_via"]]
mapping[
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

Inventory and search are useful before building a value set:

```python
terms.source_stats_df(["ICD10CM", "SNOMEDCT_US", "RXNORM", "LNC"])
terms.search_df("hemoglobin a1c", sources=["LNC"], limit=20)
```

For large notebook batches, keep the database work local and materialize only
the columns needed for review:

```python
df = terms.patient_friendly_df("ICD10CM", codes)
df = df[["source", "code", "name", "match_type", "match_depth"]]
```

Close DuckDB connections when finished, especially in long-running notebooks:

```python
terms.close()
```
