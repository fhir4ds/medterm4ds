---
title: Data Setup
---

Data setup helpers download UMLS release files, build the compact local DuckDB
database, and verify the result.

## Download

```python
mt.download_umls_release(
    *,
    output_dir="data/umls",
    api_key=None,
    release_type="umls-metathesaurus-full-subset",
    release_version=None,
    current=None,
    extract=False,
) -> Path
```

If `api_key` is omitted, medterm4ds reads `UMLS_API_KEY` and then
`UTS_API_KEY`.

```python
import medterm4ds as mt

archive = mt.download_umls_release(
    output_dir="data/umls",
    release_version="2025AB",
    extract=True,
)
```

## Build DuckDB

```python
mt.build_umls_duckdb(
    *,
    rrf_dir,
    output_db,
    replace=False,
    batch_size=100_000,
) -> Path
```

The builder accepts:

- flat `MR*.RRF` files
- `MR*.RRF.gz` files
- UMLS `.nlm` archives containing `MR*.RRF.*.gz` shards

`MRCONSO` and `MRREL` are required. `MRSAT` is optional, but NDC-to-RxCUI
resolution depends on `MRSAT` NDC attributes.

Builds also create derived guardrail tables, including
`snomed_top_level_depth`, which is used to suppress overly broad non-exact
SNOMED mapping targets.

```python
db_path = mt.build_umls_duckdb(
    rrf_dir="data/umls/umls-2025AB-metathesaurus-full/2025AB/META",
    output_db="data/umls_current.duckdb",
    replace=True,
)
```

```python
mt.annotate_umls_duckdb(
    db_path,
    *,
    db_role=None,
    release_version=None,
    source_archive=None,
) -> dict[str, str]
```

```python
mt.annotate_umls_duckdb(
    db_path,
    db_role="current_candidate",
    release_version="2025AB",
)
```

## Prepare Existing DuckDB

```python
mt.prepare_umls_duckdb(
    db_path,
    *,
    replace=True,
) -> dict[str, object]
```

Creates or refreshes derived tables without rebuilding from RRF files.

```python
mt.prepare_umls_duckdb("data/umls_current.duckdb")
```

## Verify

```python
mt.verify_umls_duckdb(
    db_path,
    *,
    sources=None,
) -> dict[str, object]
```

When `sources` is omitted, verification checks these default sources:

```python
mt.DEFAULT_UMLS_VERIFY_SOURCES
```

The report includes:

```python
db, tables, has_required_tables, has_snomed_top_level_depth, source_counts
```

```python
report = mt.verify_umls_duckdb("data/umls_current.duckdb")
report["has_required_tables"]
report["source_counts"]
```
