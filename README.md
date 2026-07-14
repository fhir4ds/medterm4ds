# Medical Terminology for Data Science

`medterm4ds` is the Python package and CLI for Medical Terminology for Data
Science: UMLS-backed terminology lookup, mapping, patient-friendly names,
value set optimization, and interoperability workflows.

The current slice focuses on exact code lookup, source inventory/search,
same-CUI plus bounded hierarchy source mapping, hierarchy traversal, and
patient-friendly names:

- typed domain results
- batch-first service API
- active atom lookup for one or many codes
- source statistics, code samples, TTY inspection, and name search
- same-CUI mappings from source codes to target vocabularies
- same-source parent, child, ancestor, and descendant traversal
- ConceptMap rows derived from patient-friendly results
- local DuckDB engine
- dirty-working-tree parity adapter for `/mnt/d/medterm`
- output helpers for records, JSONL, CSV, FHIR ConceptMap JSON, and optional
  pandas or Polars DataFrames
- versioned public output schemas for core result records
- structured provenance through `matched_via.steps`
- real-data validation scripts for bulk throughput and mapping quality review
- optional openFDA label and PubMed guideline evidence adapters for MCP/domain
  compatibility tools

## Notebook Quickstart

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb", memory_profile="low")

info = terms.lookup("ICD10CM", "E11.9")
friendly = terms.patient_friendly("ICD10CM", "E11.9")
mappings = terms.map("ICD10CM", "E11.9", target_sources=["SNOMEDCT_US"])

df = terms.patient_friendly_df("ICD10CM", ["E11.9", "E11.40", "E11.42"])
```

Use the CLI for automation, validation, and bulk file exports. Use the service
functions and engines directly when you need lower-level control.

## Layout

```text
src/medterm4ds/
  core/                  # CodeRef, CodeInfo, CodeMapping, CodeRelation models
  sources/               # Source-specific preparation rules (TTY, hierarchy, friendly strategy)
  engines/
    duckdb/              # local DuckDB execution, split into focused modules:
      engine.py          #   dispatcher + remaining helpers (~2100 lines)
      hierarchy.py       #   parent/child/ancestor/descendant traversal
      mappings.py        #   source-to-target code mappings (same-CUI + ancestors)
      resolution.py      #   active/historical/obsolete/NDC code resolution
      patient_friendly.py #  per-source patient-friendly name resolvers
      indications.py     #   condition->medication may_treat/may_prevent traversal
      prepared.py        #   mt4ds prepared-schema builders
    api/                 # remote API engine (HTTP client)
  services/              # public batch-first service functions
  ds.py                  # DataFrame-friendly service wrappers
  domains/               # Diagnosis/lab/procedure/drug/vaccine + evidence wrappers
  outputs/               # serialization, FHIR ConceptMap, checkpoint/resume
  apps/                  # CLI, FastAPI, MCP, FHIR R4 terminology server adapters
```

See [docs/architecture.md](docs/architecture.md) for the design principles and
module boundaries.

## FHIR R4 Terminology Server

medterm4ds includes a FHIR R4 terminology server that exposes all standard
terminology operations plus a custom text-to-code search.

### Quick start

**Local dev** (with existing UMLS DuckDB):
```bash
pip install -e '.[fhir]'
export MEDTERM4DS_DB=/mnt/d/medterm4ds/data/umls_current.duckdb
python -m medterm4ds.apps.fhir_api
# Server runs on http://127.0.0.1:8001/fhir/
```

**Docker** (builds lookup.duckdb from UMLS RRF — license-compliant):
```bash
docker build -f deploy/hf-spaces/fhir-server/Dockerfile -t medterm4ds-fhir .
docker run -p 7860:7860 \
  -e UMLS_API_KEY=your_key \
  -e HF_TOKEN=your_hf_token \
  medterm4ds-fhir
# Server runs on http://localhost:7860/fhir/
```

### Operations

| Operation | Endpoint | Description |
|---|---|---|
| `$lookup` | `/fhir/CodeSystem/$lookup` | Code → display + properties (patient-friendly, canonical, tty) |
| `$validate-code` | `/fhir/CodeSystem/$validate-code` | Validate a code exists |
| `$translate` | `/fhir/ConceptMap/$translate` | Map codes between systems |
| `$subsumes` | `/fhir/CodeSystem/$subsumes` | Check hierarchy relationships |
| `$expand` | `/fhir/ValueSet/$expand` | Expand ValueSets (filter, intensional, explicit) |
| `$closure` | `/fhir/CodeSystem/$closure` | Pre-compute subsumption for O(1) checks |
| `$search` | `/fhir/CodeSystem/$search` | Text → ranked codes (lexical / hybrid / semantic) |
| `$extract` | `/fhir/CodeSystem/$extract` | Free-text concept extraction (NER + ConText + search) |
| Batch | `POST /fhir` | Bundle of operations with per-entry error isolation (FHIR R4 §3.7) |

### Search modes

`$search` supports three modes via the `searchMode` parameter:

- **`lexical`** (default): BM25 token matching (~1ms). Covers 80-90% of queries.
- **`semantic`**: Fine-tuned SapBERT embeddings + FAISS ANN (~100ms). Catches novel phrasings like "high blood sugar" → Hyperglycemia.
- **`hybrid`**: BM25 retrieve top-50 + SapBERT re-rank (~110ms). Best accuracy.

Results include a match-grade (`certain` / `probable` / `possible`), modeled after FHIR Patient `$match`.

### Conformance testing

```bash
make fhir-conformance    # 2546 conformance probes across 18 FHIR R4 spec chunks, ~8 minutes
```

See the [demo notebook](notebooks/fhir_terminology_server_demo.ipynb) for a
full walkthrough, and [deploy/hf-spaces/](deploy/hf-spaces/) for Docker /
Hugging Face Spaces deployment.

### Data licensing

The Docker container builds `lookup.duckdb` from UMLS RRF files using the
user's own NMLS API key — no UMLS data is redistributed. Derived search
indexes (BM25, SapBERT, patient-friendly JSONs) are downloaded from
[joelmontavon/medterm4ds-data](https://huggingface.co/datasets/joelmontavon/medterm4ds-data)
on HuggingFace.

## Development

Common commands are captured in the `Makefile`:

```bash
make test
make compile
make verify
make notebook-smoke
make acceptance-smoke
make bulk-validation-smoke
make mapping-quality-smoke
make build
make wheel-install-smoke
```

Install extras as needed:

```bash
pip install -e '.[duckdb]'
pip install -e '.[api]'
pip install -e '.[mcp]'
pip install -e '.[fhir]'
pip install -e '.[dataframe]'
pip install -e '.[dev]'
```

## Current Scope

For notebooks, prefer the `Terminology` facade created by `connect(...)` or
`connect_remote(...)`. The lookup vertical slice is also available directly as
`get_code_infos(...)`. It returns one active UMLS atom row per input code,
preserving input order and returning `None` for missing or suppressed-only
codes.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")
info = terms.lookup("ICD10CM", "E11.9")
```

Historical, obsolete, and NDC inputs are handled by `resolve_codes(...)`.
Resolution is explicit for normal code systems and automatic for `source="NDC"`
when a service receives an NDC input.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")
row = terms.resolve("NDC", "0002-0821-01")
```

Resolution rows preserve the input, the current/effective code when one is
available, `status`, `match_type`, `normalized_code`, candidate replacements,
and `matched_via` provenance. Lookup, mapping, patient-friendly, API, and MCP
surfaces accept `resolve_mode` where resolving historical inputs should be
explicit.

The discovery vertical slice exposes inventory and search helpers:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")
results = terms.search("diabetes", sources=["ICD10CM", "SNOMEDCT_US"], limit=25)
```

The mapping vertical slice is `get_code_mappings(...)`. It returns active
same-CUI target mappings for one or many source codes, preserving input order
and using structured provenance to show the selected source atom and target atom.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")
mappings = terms.map("ICD10CM", "E11.9", target_sources=["SNOMEDCT_US"])
```

The valueset optimization slice is `optimize_codes(...)`. It uses same-source
hierarchy relationships to compact a list of leaf codes into include/exclude
rules.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")
result = terms.optimize("ICD10CM", ["E11.40", "E11.41"])
```

The hierarchy vertical slice is `get_code_relations(...)`. It returns flat
`CodeRelation` rows for same-source parent, child, ancestor, or descendant
relationships.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")
relations = terms.ancestors("ICD10CM", "E11.9", max_depth=3)
```

The patient-friendly vertical slice is `get_patient_friendly_names(...)`. It
supports one or many codes through the same service contract and currently
covers:

- ICD-10-CM and ICD-10-PCS
- SNOMED CT
- RxNorm
- LOINC/LNC
- CVX
- CPT and HCPCS

The local DuckDB engine avoids loading a full in-memory graph. It uses
temporary input tables and set-based SQL for source grouping, cross-reference,
hierarchy fallback, and source-specific strategies.

## DataFrames and Schemas

The `Terminology` facade exposes `_df` methods for notebooks. They return
pandas by default, or Polars with `backend="polars"` when Polars is installed.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

lookup_df = terms.lookup_df("ICD10CM", ["E11.9", "E11.40"])
mapping_df = terms.map_df(
    "ICD10CM",
    ["E11.9", "E11.40"],
    target_sources=["SNOMEDCT_US"],
)
```

`medterm4ds.outputs.to_dataframe(...)` and the `medterm4ds.ds` module remain
available for lower-level service rows.

Stable result shapes are exposed through versioned schemas:

```python
from medterm4ds import get_output_schema

schema = get_output_schema("CodeMapping")
print(schema.version)
print(schema.field_names)
```

`get_concept_map(...)` and `iter_concept_map(...)` build on the same service
contract. They turn patient-friendly results into `ConceptMapRow` records and
support chunked iteration for bulk exports without introducing a separate bulk
transform implementation.

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")
rows = terms.conceptmap("ICD10CM", ["E11.9"])
```

For streaming export:

```python
from medterm4ds.outputs import write_jsonl
from medterm4ds.services.conceptmap import iter_concept_map

write_jsonl(
    iter_concept_map(codes, engine=engine, batch_size=5000),
    "patient_friendly_conceptmap.jsonl",
)
```

For non-ConceptMap bulk exports, use the shared `bulk` CLI namespace. These
commands stream source inventory through the same lookup, mapping, hierarchy, or
patient-friendly services and write JSONL or CSV with checkpoint sidecars:

```bash
medterm4ds bulk lookup \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,CVX \
  --output lookup.jsonl

medterm4ds bulk map \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,LNC,CPT,HCPCS \
  --target-sources SNOMEDCT_US \
  --output source_to_snomed.jsonl

medterm4ds bulk hierarchy \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM \
  --direction parents \
  --output icd10_parents.jsonl

medterm4ds bulk patient-friendly \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,RXNORM,LNC,CVX \
  --output friendly_names.csv
```

## CLI

For exact active atom lookup:

```bash
medterm4ds lookup \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source ICD10CM \
  --code E11.9
```

Provide one `--source` for all codes, or one `--source` per `--code`. Lookup
output can be JSON, JSONL, or CSV.

For source-to-source mapping:

```bash
medterm4ds map \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source ICD10CM \
  --code E11.9 \
  --target-source SNOMEDCT_US
```

Provide one `--source` for all codes, or one `--source` per `--code`. Repeat
`--target-source` for multiple targets. Mapping output can be JSON, JSONL, or
CSV. By default mapping is exact active same-CUI only. Use `--max-depth` to
allow source-ancestor broader mappings, and add `--include-target-ancestors` or
`--include-target-descendants` when target hierarchy expansion is desired.

For a bulk mapping ConceptMap:

```bash
medterm4ds conceptmap mapping \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,LNC,CPT,HCPCS \
  --target-sources SNOMEDCT_US \
  --output source_to_snomed_conceptmap.jsonl \
  --memory-profile low \
  --progress
```

Mapping ConceptMaps can be written as JSONL, CSV, or FHIR R4 JSON:

```bash
medterm4ds conceptmap mapping \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM \
  --target-sources SNOMEDCT_US \
  --output icd10_to_snomed_conceptmap.json \
  --format fhir-json
```

For historical/obsolete input resolution:

```bash
medterm4ds resolve \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source NDC \
  --code 0002-0821-01 \
  --format table
```

For valueset optimization:

```bash
medterm4ds optimize \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source ICD10CM \
  --code E11.40 \
  --code E11.41 \
  --format table
```

Single-command lookup and map support `--resolve-mode resolve_current` when
historical inputs should be mapped forward before the operation.

For terminology discovery:

```bash
medterm4ds sources \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,LNC,SNOMEDCT_US

medterm4ds sample-codes \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,CVX \
  --per-source 10

medterm4ds code-ttys \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source ICD10CM \
  --code E11.9

medterm4ds search-names \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --query diabetes \
  --sources ICD10CM,SNOMEDCT_US \
  --tty PT \
  --limit 25
```

For hierarchy traversal:

```bash
medterm4ds hierarchy ancestors \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source ICD10CM \
  --code E11.9 \
  --max-depth 3
```

Supported hierarchy subcommands are `parents`, `children`, `ancestors`, and
`descendants`. Output can be JSON, JSONL, or CSV.

The first CLI workflow generates a patient-friendly ConceptMap directly from a
DuckDB UMLS database:

```bash
medterm4ds conceptmap patient-friendly \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,ICD10PCS,SNOMEDCT_US,RXNORM,LNC,CVX,CPT,HCPCS \
  --output patient_friendly_conceptmap.jsonl \
  --memory-profile balanced \
  --progress
```

Output can be JSONL or CSV:

```bash
medterm4ds conceptmap patient-friendly \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,RXNORM,LNC \
  --output patient_friendly_conceptmap.csv \
  --format csv
```

For a FHIR R4 ConceptMap JSON resource:

```bash
medterm4ds conceptmap patient-friendly \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,RXNORM,LNC \
  --output patient_friendly_conceptmap.json \
  --format fhir-json
```

FHIR JSON is written as a single R4 ConceptMap resource grouped by source and
target code system. Target mappings use R4 `equivalence` codes, while the
internal relationship, match type, match depth, friendly source, and provenance
are preserved as extensions. It is intended for smaller exports or
post-processing from JSONL/CSV; use JSONL or CSV for resumable full-vocabulary
bulk runs.

Named memory profiles:

- `fast`: `4GB`, default DuckDB threads, 5,000-code internal chunks.
- `balanced`: `1GB`, default DuckDB threads, 5,000-code internal chunks.
- `low`: `512MB`, one DuckDB thread, 1,000-code internal chunks.

Any profile setting can be overridden with `--memory-limit`, `--threads`,
`--query-chunk-size`, or `--temp-dir`. The CLI prepares local DuckDB temp caches by
default; use `--no-prepare-cache` for small smoke tests or debugging.

Long exports write a checkpoint sidecar by default:

```text
patient_friendly_conceptmap.jsonl.checkpoint.json
```

To resume an interrupted run, rerun the same command with `--resume`:

```bash
medterm4ds conceptmap patient-friendly \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,ICD10PCS,SNOMEDCT_US,RXNORM,LNC,CVX,CPT,HCPCS \
  --output patient_friendly_conceptmap.jsonl \
  --memory-profile low \
  --resume \
  --progress
```

Resume scans the existing output to find the last completed `source`/`code`,
then skips the DuckDB inventory through that point and appends the remaining
rows. This avoids duplicating the last row even if the process stopped after
writing output but before updating the checkpoint. Use `--checkpoint` to choose
a custom checkpoint path and `--checkpoint-every` to control update frequency.

## Baseline Notes

Historical patient-friendly parity harnesses (`compare_patient_friendly_parity.py`,
`run_patient_friendly_parity_matrix.py`, `benchmark_local_duckdb_patient_friendly.py`)
have been removed — the Tier 1-4 regression suite under `tests/regression/` covers
the same correctness ground with golden parity files and runs in CI.

To smoke-test the CLI workflow end to end:

```bash
python3 scripts/run_cli_acceptance.py \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,CVX \
  --limit 20 \
  --work-dir acceptance_outputs \
  --output-json acceptance_patient_friendly.json
```

The acceptance harness checks JSONL resume, CSV output, and FHIR R4 JSON shape.

To smoke-test lookup, mapping, and hierarchy against a real UMLS DuckDB file:

```bash
python3 scripts/run_real_data_smoke.py \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --source ICD10CM \
  --target-source SNOMEDCT_US \
  --output-json real_data_smoke.json
```

To run bounded real-data bulk workflow validation:

```bash
python3 scripts/run_bulk_validation.py \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --work-dir validation_outputs \
  --output-json bulk_validation_report.json \
  --limit 1000 \
  --batch-size 500 \
  --memory-profile low
```

The bulk validation report covers ICD-10-CM, LOINC, CPT/HCPCS to SNOMED CT
mapping trials plus patient-friendly names for ICD-10, SNOMED CT, RxNorm,
LOINC, CVX, CPT, and HCPCS. Use `--full` for full source inventories, and
`--prepare-cache` when you want the same warmed-cache path as production bulk
runs.

To sample crosswalk quality and flag rows for review:

```bash
python3 scripts/review_mapping_quality.py \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --pairs ICD10CM:SNOMEDCT_US,LNC:SNOMEDCT_US,CPT:SNOMEDCT_US,HCPCS:SNOMEDCT_US \
  --per-source 250 \
  --output-json reports/quality/mapping_quality_report.json \
  --output-csv reports/quality/mapping_review_cases.csv
```

The mapping quality report counts `match_type`, `relationship`, and review
flags such as hierarchy fallback, many targets for one source code, broad target
display names, and low source/target name overlap. The CSV writes one flagged
case per row with filterable flag columns and a blank `review_notes` column for
spreadsheet review. It is a triage aid, not a clinical validation substitute.

## Data Setup

Python helpers can download UTS release files with a UMLS API key and build the
compact local DuckDB schema:

```python
import os
import medterm4ds as mt

archive = mt.download_umls_release(
    output_dir="data/umls",
    api_key=os.environ["UMLS_API_KEY"],
    release_version="2025AB",  # optional; omit to use the latest returned release
    extract=True,
)

db_path = mt.build_umls_duckdb(
    rrf_dir="data/umls/<extracted-release>/2025AB/META",
    output_db="data/umls_current.duckdb",
)
mt.annotate_umls_duckdb(
    db_path,
    db_role="current_candidate",
    release_version="2025AB",
    source_archive=archive,
)

report = mt.verify_umls_duckdb(db_path)
```

The builder loads `MRCONSO.RRF`, `MRREL.RRF`, and `MRSAT.RRF` into the compact
tables used by local DuckDB workflows. It accepts flat `MR*.RRF` files,
`MR*.RRF.gz` files, or `.nlm` archives containing `MR*.RRF.*.gz` shards. The
default downloader uses the UMLS Metathesaurus Full Subset release type and
saves raw files under `data/umls` unless you choose another directory.
`MRSAT.RRF` is needed for NDC to RxCUI resolution. Builds also create derived
guardrail tables, including `snomed_top_level_depth`, which is used to suppress
overly broad non-exact SNOMED cross-reference targets.

For an existing database, refresh derived tables without rebuilding from RRF:

```python
mt.prepare_umls_duckdb("data/umls_current.duckdb", replace=True)
```

The CLI provides equivalent operational commands under `medterm4ds data`.
The repo also includes a setup script that stores raw downloads under `data/umls`
by default:

```bash
python3 scripts/download_umls_release.py \
  --release-version 2025AB \
  --output-dir data/umls \
  --extract \
  --build \
  --db-role current_candidate \
  --output-db data/umls_current.duckdb \
  --replace
```

To build a fixed 2025AB parity fixture from an existing archive:

```bash
python3 scripts/download_umls_release.py \
  --archive /mnt/d/medterm/data/umls-2025AB-metathesaurus-full.zip \
  --output-dir data/umls \
  --extract \
  --build \
  --db-role medterm4ds_2025ab_fixture \
  --output-db data/umls_2025ab.duckdb \
  --replace
```

## API

The API is a thin FastAPI wrapper over the same service layer. It is configured
as one DuckDB database per process, so the engine can be opened once and reused
across requests.

```bash
export MEDTERM4DS_DB=/mnt/d/medterm4ds/data/umls_current.duckdb
export MEDTERM4DS_SOURCES=ICD10CM,ICD10PCS,SNOMEDCT_US,RXNORM,LNC,CVX,CPT,HCPCS
export MEDTERM4DS_MEMORY_PROFILE=balanced
export MEDTERM4DS_PREPARE_CACHE=true

uvicorn 'medterm4ds.apps.api:create_app' --factory --host 0.0.0.0 --port 8000
```

Supported endpoints:

- `GET /health`
- `POST /lookup`
- `POST /resolve`
- `POST /sources`
- `POST /source-stats`
- `POST /sample-codes`
- `POST /code-ttys`
- `POST /search-names`
- `POST /map`
- `POST /optimize`
- `POST /hierarchy`
- `POST /patient-friendly`
- `POST /conceptmap/patient-friendly`

Example lookup request:

```bash
curl -X POST http://localhost:8000/lookup \
  -H 'content-type: application/json' \
  -d '{
    "codes": [
      {"source": "ICD10CM", "code": "E11.9"},
      {"source": "CVX", "code": "208"}
    ]
}'
```

Example mapping request:

```bash
curl -X POST http://localhost:8000/map \
  -H 'content-type: application/json' \
  -d '{
    "codes": [
      {"source": "ICD10CM", "code": "E11.9"}
    ],
    "target_sources": ["SNOMEDCT_US"],
    "max_results_per_code": 50
  }'
```

Example hierarchy request:

```bash
curl -X POST http://localhost:8000/hierarchy \
  -H 'content-type: application/json' \
  -d '{
    "codes": [
      {"source": "ICD10CM", "code": "E11.9"}
    ],
    "direction": "ancestors",
    "max_depth": 3
  }'
```

Example patient-friendly request:

```bash
curl -X POST http://localhost:8000/patient-friendly \
  -H 'content-type: application/json' \
  -d '{
    "codes": [
      {"source": "ICD10CM", "code": "E11.9"},
      {"source": "CVX", "code": "208"}
    ],
    "max_depth": 5
}'
```

Python clients can use the same service functions against a running API process
with `RemoteApiEngine`:

```python
from medterm4ds import CodeRef, RemoteApiEngine, get_code_mappings

engine = RemoteApiEngine("http://localhost:8000")
rows = get_code_mappings(
    [CodeRef("ICD10CM", "E11.9")],
    engine=engine,
    target_sources=["SNOMEDCT_US"],
)
```

API environment variables:

- `MEDTERM4DS_DB`: required DuckDB path.
- `MEDTERM4DS_SOURCES`: comma-separated cache sources.
- `MEDTERM4DS_MEMORY_PROFILE`: `fast`, `balanced`, or `low`.
- `MEDTERM4DS_MEMORY_LIMIT`, `MEDTERM4DS_TEMP_DIR`, `MEDTERM4DS_THREADS`,
  `MEDTERM4DS_QUERY_CHUNK_SIZE`: optional profile overrides.
- `MEDTERM4DS_PREPARE_CACHE`: defaults to `true`.
- `MEDTERM4DS_CACHE_INDEXES`: defaults to `false`.

## MCP

The MCP server uses the same single-database process model as the API:

```bash
export MEDTERM4DS_DB=/mnt/d/medterm4ds/data/umls_current.duckdb
export MEDTERM4DS_SOURCES=ICD10CM,ICD10PCS,SNOMEDCT_US,RXNORM,LNC,CVX,CPT,HCPCS
export MEDTERM4DS_MEMORY_PROFILE=balanced
export MEDTERM4DS_PREPARE_CACHE=true

medterm4ds-mcp
```

Registered tools:

- `health`
- `lookup_code`
- `lookup_codes`
- `sources`
- `source_stats`
- `sample_codes`
- `code_ttys`
- `search_names`
- `resolve_codes`
- `discover`
- `cross_reference`
- `diagnosis_codes`
- `lab_codes`
- `lab_value_codes`
- `procedure_codes`
- `hcpcs_drugs`
- `vaccine_codes`
- `search_drug`
- `drugs_by_class`
- `drugs_for_indication`
- `indication_search`
- `fda_label_by_rxcui`
- `guideline_search`
- `guideline_recommendations`
- `guideline_fulltext`
- `guidelines_for_code`
- `map_codes`
- `optimize`
- `code_relations`
- `get_parents`
- `get_children`
- `get_ancestors`
- `get_descendants`
- `patient_friendly_name`
- `patient_friendly_names`
- `patient_friendly_concept_map`

The MCP tools return structured dictionaries/lists rather than ASCII trees, so
callers can use fields such as `relationship`, `depth`, `match_type`,
`match_depth`, source/target atom metadata, and `matched_via` directly.
Tools that may produce wider output also accept `output_format="table"` or
`output_format="tree"` for compact ASCII output.
Domain tools such as `diagnosis_codes`, `lab_codes`, `procedure_codes`,
`search_drug`, and `vaccine_codes` are wrappers over the same search, lookup,
map, and hierarchy services.

External evidence tools use public adapters:

- `indication_search` and `fda_label_by_rxcui` call the openFDA drug label API.
- `guideline_search`, `guideline_recommendations`, `guideline_fulltext`, and
  `guidelines_for_code` call PubMed through NCBI E-utilities.

Optional environment variables:

- `OPENFDA_API_KEY`: recommended for regular openFDA use.
- `NCBI_API_KEY`: optional NCBI API key.
- `NCBI_EMAIL`: recommended contact email for NCBI E-utilities requests.

External evidence responses include `status`. Service failures are returned as
structured `status: "error"` payloads instead of terminology-engine failures.
openFDA documents the drug label endpoint at
https://open.fda.gov/apis/drug/label/how-to-use-the-endpoint/ and NCBI
documents E-utilities at https://www.ncbi.nlm.nih.gov/books/NBK25499/.

## Benchmarking

Benchmark scripts for the local DuckDB patient-friendly path have been
removed alongside the parity harnesses (see Baseline Notes above).
Performance characteristics are now covered by the regression suite and
the `--benchmark` flag on `tests/test_engine_*.py` where applicable.

For ad-hoc measurement, use `time` against a representative lookup batch
via the CLI:

```bash
time python3 -m medterm4ds.apps.cli --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  patient-friendly --source SNOMEDCT_US --codes 44054006
```

DuckDB's memory limit is not a strict cap on total Python process RSS. It limits
DuckDB-managed memory, while Python and some DuckDB allocations can exceed it.

## Packaging

Build a wheel and source distribution:

```bash
make build
```

Publish targets are available once credentials are configured for Twine:

```bash
make publish-test
make publish
```

## License

Medical Terminology for Data Science is licensed under the GNU General Public
License version 3.0 only (`GPL-3.0-only`). UMLS and source terminology data are
licensed separately and require users to follow the applicable UMLS/source
vocabulary terms.
