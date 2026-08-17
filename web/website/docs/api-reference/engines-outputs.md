---
title: Engines and Outputs
---

The high-level `Terminology` client delegates to an engine. Output helpers turn
model objects into dictionaries, DataFrames, compact text, files, and FHIR R4
ConceptMap resources.

## LocalDuckDBEngine

```python
import duckdb
import medterm4ds as mt

con = duckdb.connect("data/umls_current.duckdb", read_only=True)
engine = mt.LocalDuckDBEngine(
    con,
    config=mt.local_duckdb_config("low"),
)
```

`connect(...)` creates this engine for you:

```python
terms = mt.connect("data/umls_current.duckdb", memory_profile="low")
```

Memory profiles:

```python
"fast"      # higher memory target, default threading
"balanced"  # moderate memory target
"low"       # lower memory target, one thread, smaller query chunks
```

`LocalLiteEngine`, `LocalLiteConfig`, `local_lite_config`, and
`LOCAL_LITE_MEMORY_PROFILES` remain compatibility aliases for pre-`0.0.1`
naming.

## RemoteApiEngine

```python
engine = mt.RemoteApiEngine(
    "http://localhost:8000",
    timeout=300.0,
    headers={"Authorization": "Bearer ..."},
)
```

`connect_remote(...)` creates a `Terminology` client backed by this engine:

```python
terms = mt.connect_remote("http://localhost:8000")
```

Remote and local clients expose the same high-level methods.

## Records and DataFrames

```python
from medterm4ds.outputs import (
    to_record,
    to_records,
    to_dataframe,
    to_pandas,
    to_polars,
    write_csv,
    write_jsonl,
)
```

```python
rows = terms.patient_friendly("ICD10CM", ["E11.9", "E11.40"])

records = to_records(rows)
df = to_dataframe(rows)
```

Client methods ending in `_df` use the same helpers:

```python
terms.map_df("ICD10CM", ["E11.9"], target_sources=["SNOMEDCT_US"])
terms.map_df("ICD10CM", ["E11.9"], target_sources=["SNOMEDCT_US"], backend="polars")
```

## Compact Rendering

```python
from medterm4ds.outputs import render_output, render_table, render_tree
```

```python
payload = {"results": [row.to_dict() for row in rows]}

render_output(payload, output_format="dict")
render_output(payload, output_format="table")
render_output(payload, output_format="tree")
```

These renderers are used by CLI and MCP surfaces when compact output is more
useful than nested JSON.

## FHIR ConceptMap

```python
from medterm4ds.outputs import (
    concept_map_to_fhir,
    write_fhir_concept_map,
    code_system_uri,
    fhir_equivalence,
)
```

```python
rows = terms.mapping_conceptmap(
    "ICD10CM",
    ["E11.9"],
    target_sources=["SNOMEDCT_US"],
)

resource = concept_map_to_fhir(
    rows,
    id_="icd10cm-to-snomed",
    url="urn:example:ConceptMap:icd10cm-to-snomed",
    title="ICD-10-CM to SNOMED CT ConceptMap",
    include_extensions=True,
)
```

`include_extensions=True` preserves medterm4ds review fields such as
`relationship`, `match_type`, `match_depth`, and `matched_via`.

Known source URIs are in `FHIR_CODE_SYSTEMS`. Unknown sources fall back to:

```python
urn:medterm4ds:CodeSystem:{SOURCE}
```
