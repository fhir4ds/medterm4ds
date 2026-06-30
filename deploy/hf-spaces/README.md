# FHIR Terminology Server — Docker Deployment

## Quick start

```bash
# Build
docker build -f deploy/hf-spaces/fhir-server/Dockerfile -t fhir4ds-fhir .

# Run (requires UMLS API key — see below)
docker run -p 7860:7860 \
  -e UMLS_API_KEY=your_umls_api_key \
  -e HF_TOKEN=your_hf_token \
  fhir4ds-fhir

# Test
curl http://localhost:7860/fhir/metadata
curl "http://localhost:7860/fhir/CodeSystem/\$lookup?system=http://snomed.info/sct&code=44054006"
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `UMLS_API_KEY` | **Yes** | NLM UMLS API key. Used to download RRF files and build lookup.duckdb. Get one at https://uts.nlm.nih.gov/uts/edit-profile |
| `HF_TOKEN` | No | HuggingFace token. Only needed for private datasets or writing to HF Spaces. The derived data dataset is public — downloads work without a token. |
| `UMLS_RELEASE` | Optional | UMLS release version (default: `2026AA`) |
| `MEDTERM4DS_DATA_DIR` | Optional | Persistent storage path (default: `/data`) |
| `MEDTERM4DS_MEMORY_PROFILE` | Optional | DuckDB memory limit (default: `low` = 512MB) |

## First start timeline

```
0s          Container starts
~1s         Downloads UMLS RRF from NLM (~3 GB zip)
~5min       RRF download completes
~8min       lookup.duckdb built (filtered to 8 sources, 217 MB)
~9min       BM25 + SapBERT + JSONs downloaded from HF (~3 GB)
~10min      Server ready — first request served
```

Subsequent starts with cached `/data`: **~3 seconds**.

## Data provisioning

The container provisions two categories of data:

### Raw UMLS (built from source — license-compliant)

`lookup.duckdb` is built from the UMLS Metathesaurus RRF files using the
user's own NLM API key. This is license-compliant — no UMLS data is
redistributed. The build:

1. Downloads the full UMLS release zip from NLM (~3 GB)
2. Extracts RRF pipe-delimited files
3. Filters to 8 clinical sources (SNOMED, ICD-10, RxNorm, LOINC, CPT, HCPCS, CVX, ICD-10-PCS)
4. Writes a 217 MB DuckDB with 3 tables (mrconso, mrrel, mrsat)
5. Cleans up intermediate files

### Derived search data (downloaded from HuggingFace)

Pre-computed indexes are downloaded from the public HF dataset
`joelmontavon/medterm4ds-data`:

| Data | Size | What it powers |
|---|---|---|
| `patient_friendly_*.json` | ~225 MB | Custom properties in $lookup |
| `bm25/*_bm25.json` | ~167 MB | $search lexical mode |
| `sapbert/` (model + FAISS) | ~2.5 GB | $search semantic/hybrid mode |

These are derived data (computed via medterm4ds pipelines), not
raw vocabulary atoms.

## Operations

All 7 FHIR R4 operations work after first start:

| Operation | Data source | Latency |
|---|---|---|
| $lookup | lookup.duckdb + JSONs | ~5ms |
| $validate-code | lookup.duckdb | ~5ms |
| $translate | lookup.duckdb | ~10ms |
| $subsumes | lookup.duckdb | ~10ms |
| $expand | lookup.duckdb | ~50ms |
| $closure | lookup.duckdb | ~10ms |
| $search (lexical) | BM25 JSONs | ~1ms |
| $search (semantic) | SapBERT + FAISS | ~100ms |
| $search (hybrid) | Both | ~110ms |

## Persistent storage

Mount `/data` to a Docker volume for caching:

```bash
docker run -p 7860:7860 \
  -e UMLS_API_KEY=xxx \
  -e HF_TOKEN=xxx \
  -v fhir4ds-data:/data \
  fhir4ds-fhir
```

This caches the ~3.2 GB of data across container restarts. Without it, every
restart rebuilds from scratch (~10 minutes).

## HF Spaces deployment

The Dockerfile is compatible with HuggingFace Spaces (Docker SDK). Create
a Space with:

1. Copy `deploy/hf-spaces/fhir-server/` contents to the Space repo
2. Copy `src/` and `scripts/` from the medterm4ds repo
3. Set `UMLS_API_KEY` and `HF_TOKEN` as Space secrets
4. The Space builds the Dockerfile and serves on port 7860
