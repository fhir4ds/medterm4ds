# medterm4ds

`medterm4ds` is a refactor/prototype for batch-first medical terminology workflows.

The current slice focuses on exact code lookup, same-CUI source mapping,
hierarchy traversal, and patient-friendly names:

- typed domain results
- batch-first service API
- active atom lookup for one or many codes
- same-CUI mappings from source codes to target vocabularies
- same-source parent, child, ancestor, and descendant traversal
- ConceptMap rows derived from patient-friendly results
- DuckDB `LocalLiteEngine`
- dirty-working-tree parity adapter for `/mnt/d/medterm`
- output helpers for records, JSONL, CSV, FHIR ConceptMap JSON, and optional
  pandas DataFrames
- structured provenance through `matched_via.steps`

## Layout

```text
src/medterm4ds/
  core/                  # CodeRef, CodeInfo, CodeMapping, CodeRelation models
  engines/
    duckdb/              # LocalLite DuckDB execution
    medterm_baseline/    # comparison adapter for /mnt/d/medterm
  services/              # public batch-first service functions
  outputs/               # serialization/dataframe helpers
```

See [docs/architecture.md](docs/architecture.md) for the design principles and
module boundaries.

## Development

Common commands are captured in the `Makefile`:

```bash
make test
make compile
make verify
make parity-smoke
make acceptance-smoke
```

Install extras as needed:

```bash
pip install -e '.[duckdb]'
pip install -e '.[api]'
pip install -e '.[mcp]'
pip install -e '.[dev]'
```

## Current Scope

The lookup vertical slice is `get_code_infos(...)`. It returns one active UMLS
atom row per input code, preserving input order and returning `None` for missing
or suppressed-only codes.

```python
from medterm4ds import CodeRef, get_code_infos

infos = get_code_infos(
    [CodeRef("ICD10CM", "E11.9")],
    engine=engine,
)
```

The mapping vertical slice is `get_code_mappings(...)`. It returns active
same-CUI target mappings for one or many source codes, preserving input order
and using structured provenance to show the selected source atom and target atom.

```python
from medterm4ds import CodeRef, get_code_mappings

mappings = get_code_mappings(
    [CodeRef("ICD10CM", "E11.9")],
    engine=engine,
    target_sources=["SNOMEDCT_US"],
)
```

The hierarchy vertical slice is `get_code_relations(...)`. It returns flat
`CodeRelation` rows for same-source parent, child, ancestor, or descendant
relationships.

```python
from medterm4ds import CodeRef, get_ancestors

relations = get_ancestors(
    [CodeRef("ICD10CM", "E11.9")],
    engine=engine,
    max_depth=3,
)
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

The LocalLite engine is DuckDB-only and avoids loading a full in-memory graph.
It uses temporary input tables and set-based SQL for source grouping,
cross-reference, hierarchy fallback, and source-specific strategies.

`get_concept_map(...)` and `iter_concept_map(...)` build on the same service
contract. They turn patient-friendly results into `ConceptMapRow` records and
support chunked iteration for bulk exports without introducing a separate bulk
transform implementation.

```python
from medterm4ds import CodeRef, get_concept_map

rows = get_concept_map(
    [CodeRef("ICD10CM", "E11.9")],
    engine=engine,
)
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

## CLI

For exact active atom lookup:

```bash
medterm4ds lookup \
  --db /mnt/d/medterm/data/umls_local.duckdb \
  --source ICD10CM \
  --code E11.9
```

Provide one `--source` for all codes, or one `--source` per `--code`. Lookup
output can be JSON, JSONL, or CSV.

For source-to-source mapping:

```bash
medterm4ds map \
  --db /mnt/d/medterm/data/umls_local.duckdb \
  --source ICD10CM \
  --code E11.9 \
  --target-source SNOMEDCT_US
```

Provide one `--source` for all codes, or one `--source` per `--code`. Repeat
`--target-source` for multiple targets. Mapping output can be JSON, JSONL, or
CSV.

For hierarchy traversal:

```bash
medterm4ds hierarchy ancestors \
  --db /mnt/d/medterm/data/umls_local.duckdb \
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
  --db /mnt/d/medterm/data/umls_local.duckdb \
  --sources ICD10CM,ICD10PCS,SNOMEDCT_US,RXNORM,LNC,CVX,CPT,HCPCS \
  --output patient_friendly_conceptmap.jsonl \
  --memory-profile balanced \
  --progress
```

Output can be JSONL or CSV:

```bash
medterm4ds conceptmap patient-friendly \
  --db /mnt/d/medterm/data/umls_local.duckdb \
  --sources ICD10CM,RXNORM,LNC \
  --output patient_friendly_conceptmap.csv \
  --format csv
```

For a FHIR R4 ConceptMap JSON resource:

```bash
medterm4ds conceptmap patient-friendly \
  --db /mnt/d/medterm/data/umls_local.duckdb \
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
`--query-chunk-size`, or `--temp-dir`. The CLI prepares LocalLite temp caches by
default; use `--no-prepare-cache` for small smoke tests or debugging.

Long exports write a checkpoint sidecar by default:

```text
patient_friendly_conceptmap.jsonl.checkpoint.json
```

To resume an interrupted run, rerun the same command with `--resume`:

```bash
medterm4ds conceptmap patient-friendly \
  --db /mnt/d/medterm/data/umls_local.duckdb \
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

Tests compare semantic fields against the dirty `/mnt/d/medterm` working tree
where the exported bulk implementation is usable. The current medterm bulk path
raises a `KeyError: 'friendly_name'` for one CPT-to-HCPCS fallback branch, so
that specific branch is covered as a LocalLite behavior test instead of a parity
test.

For replacement-readiness checks, run the parity harness:

```bash
python3 scripts/compare_patient_friendly_parity.py \
  --db /mnt/d/medterm/data/umls_local.duckdb \
  --medterm-path /mnt/d/medterm \
  --sources ICD10CM,ICD10PCS,HCPCS,SNOMEDCT_US,RXNORM,LNC,CVX,CPT \
  --per-source 25 \
  --output-json parity_patient_friendly.json \
  --output-md parity_patient_friendly.md \
  --progress
```

The report compares `name`, `friendly_source`, `match_type`, and `match_depth`
against dirty `medterm`. Known old-medterm CPT fallback failures are marked as
`baseline_error_known` with the issue id
`medterm_cpt_hcpcs_friendly_name_keyerror`.

To smoke-test the CLI workflow end to end:

```bash
python3 scripts/run_cli_acceptance.py \
  --db /mnt/d/medterm/data/umls_local.duckdb \
  --sources ICD10CM,CVX \
  --limit 20 \
  --work-dir acceptance_outputs \
  --output-json acceptance_patient_friendly.json
```

The acceptance harness checks JSONL resume, CSV output, and FHIR R4 JSON shape.

## API

The API is a thin FastAPI wrapper over the same service layer. It is configured
as one DuckDB database per process, so the engine can be opened once and reused
across requests.

```bash
export MEDTERM4DS_DB=/mnt/d/medterm/data/umls_local.duckdb
export MEDTERM4DS_SOURCES=ICD10CM,ICD10PCS,SNOMEDCT_US,RXNORM,LNC,CVX,CPT,HCPCS
export MEDTERM4DS_MEMORY_PROFILE=balanced
export MEDTERM4DS_PREPARE_CACHE=true

uvicorn 'medterm4ds.apps.api:create_app' --factory --host 0.0.0.0 --port 8000
```

Supported endpoints:

- `GET /health`
- `POST /lookup`
- `POST /map`
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
export MEDTERM4DS_DB=/mnt/d/medterm/data/umls_local.duckdb
export MEDTERM4DS_SOURCES=ICD10CM,ICD10PCS,SNOMEDCT_US,RXNORM,LNC,CVX,CPT,HCPCS
export MEDTERM4DS_MEMORY_PROFILE=balanced
export MEDTERM4DS_PREPARE_CACHE=true

medterm4ds-mcp
```

Registered tools:

- `health`
- `lookup_code`
- `lookup_codes`
- `map_codes`
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

## Benchmarking

The benchmark script exercises the LocalLite patient-friendly path against a
real UMLS DuckDB file:

```bash
python3 scripts/benchmark_locallite_patient_friendly.py \
  --db /mnt/d/medterm/data/umls_local.duckdb \
  --prepare-cache \
  --no-cache-indexes \
  --memory-limit 1GB \
  --temp-dir /tmp/duckdb-medterm4ds \
  --sizes 1000,10000,100000 \
  --sample-mode balanced \
  --progress
```

Measured profiles on `/mnt/d/medterm/data/umls_local.duckdb`:

- Fast low-memory-ish: `--memory-limit 1GB`, default DuckDB threads, 83,212
  balanced codes in 100.62s, 827 codes/s, peak RSS 2.55 GB.
- Strict low-memory: `--memory-limit 512MB --threads 1 --query-chunk-size 1000`,
  83,212 balanced codes in 403.39s, 206 codes/s, peak RSS 1.10 GB.
- `512MB` without `--threads 1` can still OOM during recursive fallback.

DuckDB's memory limit is not a strict cap on total Python process RSS. It limits
DuckDB-managed memory, while Python and some DuckDB allocations can exceed it.
