---
title: Python
---

Python notebooks and data science pipelines should start with the convenience
facade. It keeps the connection and engine setup out of the main workflow while
still returning the same public model objects.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

info = terms.lookup("ICD10CM", "E11.9")
friendly = terms.patient_friendly("ICD10CM", "E11.9")
mappings = terms.map("ICD10CM", "E11.9", target_sources=["SNOMEDCT_US"])
```

Batch calls use a source plus a list of codes:

```python
codes = ["E11.9", "E11.40", "E11.42"]

lookup_df = terms.lookup_df("ICD10CM", codes)
friendly_df = terms.patient_friendly_df("ICD10CM", codes)
mapping_df = terms.map_df("ICD10CM", codes, target_sources=["SNOMEDCT_US"])
```

Mixed-source batches should use `CodeRef` objects:

```python
refs = [
    mt.CodeRef("ICD10CM", "E11.9"),
    mt.CodeRef("CVX", "208"),
    mt.CodeRef("RXNORM", "1049502"),
]

terms.lookup_df(refs)
```

Use `with` when a short script should close the DuckDB connection
automatically:

```python
with mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb") as terms:
    df = terms.search_df("metformin", sources=["RXNORM"], limit=20)
```

The same facade can wrap the remote API engine:

```python
terms = mt.connect_remote("http://localhost:8000")
terms.lookup("ICD10CM", "E11.9")
```

Advanced users can still use engines and service functions directly when they
need lower-level control:

```python
import duckdb
from medterm4ds import CodeRef, get_code_infos
from medterm4ds.engines.duckdb import LocalDuckDBEngine

con = duckdb.connect("/mnt/d/medterm4ds/data/umls_current.duckdb", read_only=True)
engine = LocalDuckDBEngine(con)

rows = get_code_infos([CodeRef("ICD10CM", "E11.9")], engine=engine)
```
