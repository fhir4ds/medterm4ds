---
title: UMLS Release Info
---

medterm4ds local data should be built from a recorded UMLS release.

Recommended raw-data location:

```text
data/umls/
```

Recommended DuckDB artifact:

```text
data/umls_current.duckdb
```

Build characteristics:

- local DuckDB database
- approximately 7.1GB
- compact tables: `mrconso`, `mrrel`, `mrsat`
- built from UMLS Metathesaurus Full Subset release files
- supports NDC-to-RxCUI resolution through `MRSAT`

The downloader defaults to `releaseType=umls-metathesaurus-full-subset`.
Use `release_version` or `--release-version` for reproducible builds, for
example `2026AA`.

Some UMLS release packages are flat `MR*.RRF` files. Other packages may contain
`.nlm` archives with split `MR*.RRF.*.gz` shards. Those shards are chunks of one
logical file, so the builder decompresses and concatenates them in order before
DuckDB reads the table.

Release-sensitive behavior includes:

- source counts
- obsolete-code relationships
- RxNorm NDC attributes
- same-CUI mappings
- hierarchy relationships
- source-specific term types

Record the UMLS release version with exported ConceptMaps, value sets, and patient-friendly datasets.
