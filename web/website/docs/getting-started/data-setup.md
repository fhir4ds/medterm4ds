---
title: Data Setup
---

medterm4ds can download UMLS releases and build the compact DuckDB database used by local workflows.

Python setup uses the same public package as notebook workflows:

```python
import os
import medterm4ds as mt

archive = mt.download_umls_release(
    output_dir="data/umls",
    api_key=os.environ["UMLS_API_KEY"],
    release_version="2026AA",
    extract=True,
)

archive
```

The default downloader uses the UMLS Metathesaurus Full Subset release type.
Those releases usually extract into flat `MR*.RRF` files:

```python
db_path = mt.build_umls_duckdb(
    rrf_dir="data/umls/umls-2026AA-metathesaurus-full/2026AA/META",
    output_db="data/umls_current.duckdb",
    replace=True,
)
mt.annotate_umls_duckdb(
    db_path,
    db_role="current_candidate",
    release_version="2026AA",
    source_archive=archive,
)

db_path
```

Verify the resulting database:

```python
report = mt.verify_umls_duckdb("data/umls_current.duckdb")
report["source_counts"]
```

Builds create derived guardrail tables used by mapping workflows. To refresh
those tables on an existing database:

```python
mt.prepare_umls_duckdb("data/umls_current.duckdb", replace=True)
```

The builder supports:

- flat `MRCONSO.RRF`, `MRREL.RRF`, and `MRSAT.RRF`
- compressed `MR*.RRF.gz` files
- UMLS `.nlm` archives containing split `MR*.RRF.*.gz` shards

`MRSAT.RRF` is required for NDC-to-RxCUI resolution.

CLI equivalents are available for operational scripts:

```bash
medterm4ds data download --output-dir data/umls --release-version 2026AA --extract
medterm4ds data build-duckdb \
  --rrf-dir data/umls/umls-2026AA-metathesaurus-full/2026AA/META \
  --output-db data/umls_current.duckdb \
  --db-role current_candidate \
  --release-version 2026AA \
  --replace
medterm4ds data prepare-derived --db data/umls_current.duckdb --replace
medterm4ds data verify --db data/umls_current.duckdb
```

The repository also includes a convenience script:

```bash
python3 scripts/download_umls_release.py \
  --release-version 2026AA \
  --output-dir data/umls \
  --extract \
  --build \
  --db-role current_candidate \
  --output-db data/umls_current.duckdb \
  --replace
```

For a fixed 2026AA parity fixture from an existing archive:

```bash
python3 scripts/download_umls_release.py \
  --archive /mnt/d/medterm/data/umls-2026AA-metathesaurus-full.zip \
  --output-dir data/umls \
  --extract \
  --build \
  --db-role medterm4ds_2026aa_fixture \
  --output-db data/umls_2026aa.duckdb \
  --replace
```

## OHDSI Athena vocabularies (v0.0.6+)

medterm4ds can ingest an [Athena](https://athena.ohdsi.org) vocabulary
download alongside the UMLS database — the source for OMOP/CDM
workflows and the ConceptMap bridge the fhir4ds OMOP views consume.

```bash
# 1. Download vocabularies from Athena (choose your license set),
#    then ingest into the SAME DuckDB as your UMLS data:
medterm4ds data ingest-athena \
  --db data/umls_current.duckdb \
  --athena-dir ~/Downloads/vocabulary_download_v5_... \
  --accept-licenses

# 2. Export the ConceptMap artifact (OMOP concept_id join source):
medterm4ds data export-conceptmap \
  --db data/umls_current.duckdb \
  --out data/athena_conceptmap.ndjson   # thin NDJSON (scalar joins)
# --out data/athena_conceptmap.json     # monolithic (one resource)
```

- `--vocabularies` filters the load (default: SNOMED, RxNorm, RxNorm
  Extension, LOINC, ICD-10-CM/PCS, CPT4, HCPCS, ATC); unknown ids
  error loudly with the download's actual vocabulary list.
- The licensing notice prints BEFORE any load and `--accept-licenses`
  records acceptance in the `athena_meta` provenance table. SNOMED CT
  use follows the SNOMED Affiliates license; CPT requires the
  UMLS/AMA agreement even when bundled by Athena.
- Edge tables (ancestry, relationships) are semi-joined to the loaded
  vocabulary set — no orphan rows.
- The two export forms: **monolithic** (standard artifact, `.json`)
  and **thin** (one ConceptMap per mapping row, `.ndjson` — scalar
  `(code, system)` ON pairs for SQL view-decomposition joins);
  `--form` overrides with a mismatch warning.

### Querying Athena concepts (v0.0.7+)

```bash
# Exact ancestry via the precomputed closure (no caps):
medterm4ds athena-hierarchy --db data/umls_current.duckdb \
  --concept-id 201826 --max-depth 3

# Search with Athena-native filters:
medterm4ds athena-concepts --db data/umls_current.duckdb \
  --query diabetes --domain Condition --vocabulary SNOMED
```

The `$subsumes` Athena fast path is opt-in
(`MEDTERM4DS_ATHENA_SUBSUMES=1`; OFF by default — a locked ruling:
the closure is 164x faster but includes OMOP tradename/ingredient
relationships the isa-only UMLS walk excludes). Databases ingested
before v0.0.6's athena-batch1 must re-run `data ingest-athena`.
