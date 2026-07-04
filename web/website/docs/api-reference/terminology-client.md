---
title: Terminology Client
---

The Terminology client is the main Python API for notebooks, scripts, and
applications. It wraps a local or remote engine and delegates to the same
service layer used by the CLI, API server, MCP server, and bulk workflows.

```python
import medterm4ds as mt

terms = mt.connect("data/umls_current.duckdb", memory_profile="low")
```

## `connect`

```python
mt.connect(
    db_path,
    *,
    memory_profile="balanced",
    memory_limit=None,
    temp_directory=None,
    threads=None,
    query_chunk_size=None,
    read_only=True,
    prepare_cache=False,
    cache_sources=None,
    cache_indexes=True,
) -> Terminology
```

Creates a `Terminology` client backed by `LocalDuckDBEngine`.

| Parameter | Purpose |
| --- | --- |
| `db_path` | Local DuckDB database path. |
| `memory_profile` | One of `fast`, `balanced`, or `low`. |
| `memory_limit` | Optional DuckDB memory limit override. |
| `temp_directory` | Optional DuckDB spill directory. |
| `threads` | Optional DuckDB thread count. |
| `query_chunk_size` | Chunk size for local batch work. |
| `read_only` | Opens the database read-only by default. |
| `prepare_cache` | Creates temporary filtered tables for repeated local work. |
| `cache_sources` | Optional source list for cache preparation. |
| `cache_indexes` | Create indexes on prepared temp tables. |

Use the client as a context manager when the connection should close
automatically:

```python
with mt.connect("data/umls_current.duckdb", memory_profile="low") as terms:
    df = terms.lookup_df("ICD10CM", ["E11.9", "E11.40"])
```

For long-running notebooks, call `terms.close()` when finished.

## `connect_remote`

```python
mt.connect_remote(
    base_url,
    *,
    timeout=30.0,
    headers=None,
) -> Terminology
```

Creates the same `Terminology` client backed by `RemoteApiEngine`.

```python
terms = mt.connect_remote("http://localhost:8000")
terms.lookup("ICD10CM", "E11.9")
```

## Code Inputs

Single source/code input:

```python
terms.lookup("ICD10CM", "E11.9")
```

Batch input for one source:

```python
terms.lookup_df("ICD10CM", ["E11.9", "E11.40", "E11.42"])
```

Mixed-source input:

```python
refs = [
    mt.CodeRef("ICD10CM", "E11.9"),
    mt.CodeRef("RXNORM", "12345"),
    mt.CodeRef("CVX", "208"),
]

terms.patient_friendly_df(refs)
```

The client accepts `(source, code)` tuples, but `CodeRef` is clearer for
mixed-source work. Both forms use the same canonical order — `(source, code)`,
matching FHIR Coding `{system, code}`.

## Return Behavior

Single-code methods return a single model object:

```python
info = terms.lookup("ICD10CM", "E11.9")
```

Batch methods return lists:

```python
rows = terms.lookup("ICD10CM", ["E11.9", "E11.40"])
```

Methods ending in `_df` return pandas by default:

```python
df = terms.lookup_df("ICD10CM", ["E11.9", "E11.40"])
```

Use Polars with:

```python
df = terms.lookup_df("ICD10CM", ["E11.9"], backend="polars")
```
