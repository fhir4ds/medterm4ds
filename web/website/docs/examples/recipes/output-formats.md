---
title: Output Formats
---

# Output Formats

Choose the output format that fits the consumer: humans, scripts, notebooks, or interoperability pipelines.

## Tables and Trees

Compact output is designed for humans and MCP responses.

Use `table` when rows matter:

```bash
medterm4ds map --db "$DB" --source ICD10CM --target-source SNOMEDCT_US --code E11.9 --format table
```

Use `tree` when relationships or rules matter:

```bash
medterm4ds optimize --db "$DB" --source ICD10CM --code E11.40 --code E11.41 --format tree
```

Use dictionary output when another process needs to parse the response.

## JSON, JSONL, and CSV

Structured output is intended for scripts, batch workflows, and downstream data pipelines.

Common formats:

- JSON for single-command inspection
- JSONL for streaming bulk results
- CSV for spreadsheet review or simple ingestion

Example:

```bash
medterm4ds bulk lookup \
  --db "$DB" \
  --sources ICD10CM \
  --output lookup.jsonl \
  --format jsonl
```

## DataFrames

The notebook facade has DataFrame methods for the most common workflows.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

df = terms.lookup_df("ICD10CM", ["E11.9", "E11.40"])
```

Use `_df` methods when you want pandas or Polars output directly:

```python
friendly = terms.patient_friendly_df("ICD10CM", ["E11.9", "E11.40"])
mapping = terms.map_df(
    "ICD10CM",
    ["E11.9", "E11.40"],
    target_sources=["SNOMEDCT_US"],
)
hierarchy = terms.hierarchy_df("ICD10CM", "E11.9", direction="ancestors", max_depth=3)
search = terms.search_df("metformin", sources=["RXNORM"], limit=20)
```

Set `backend="polars"` when Polars is installed:

```python
df = terms.lookup_df("ICD10CM", ["E11.9"], backend="polars")
```

Lower-level helpers are still available for service results:

```python
from medterm4ds.outputs import to_dataframe

rows = terms.map("ICD10CM", "E11.9", target_sources=["SNOMEDCT_US"])
df = to_dataframe(rows)
```

Keep provenance fields visible during review, especially `match_type`, `match_depth`, and `matched_via`.

## FHIR ConceptMap

FHIR ConceptMap export is used for interoperability workflows.

```bash
medterm4ds conceptmap mapping \
  --db "$DB" \
  --source ICD10CM \
  --target-source SNOMEDCT_US \
  --output icd10cm-snomed.json \
  --format fhir-json
```

ConceptMap exports should preserve mapping provenance so consumers can distinguish exact matches from fallback mappings.
