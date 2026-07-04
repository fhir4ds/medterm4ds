---
title: FHIR Terminology Server
emoji: 🏥
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# medterm4ds FHIR R4 Terminology Server

A FHIR R4 terminology server backed by UMLS 2026AA, providing code lookup,
validation, mapping, subsumption, ValueSet expansion, and text-to-code search
(lexical + semantic via fine-tuned SapBERT).

## Operations

| Operation | Description |
|---|---|
| `$lookup` | Code → display name + properties (patient-friendly, canonical, tty) |
| `$validate-code` | Validate a code exists in a code system |
| `$translate` | Map codes between systems (e.g., SNOMED → ICD-10) |
| `$subsumes` | Check hierarchy relationships (is A an ancestor of B?) |
| `$expand` | Expand ValueSets (filter, intensional is-a, explicit, fhir_vs) |
| `$closure` | Maintain closure tables for fast subsumption |
| `$search` | Text → ranked codes (lexical, hybrid, semantic modes) |

## Usage

```bash
# Lookup a SNOMED code
curl "https://[space-name].hf.space/fhir/CodeSystem/\$lookup?system=http://snomed.info/sct&code=44054006"

# Search for codes by text
curl "https://[space-name].hf.space/fhir/CodeSystem/\$search?query=high+blood+sugar&searchMode=semantic"

# Expand a ValueSet
curl "https://[space-name].hf.space/fhir/ValueSet/\$expand?filter=diabetes&count=10"
```

## Data

- UMLS 2026AA (filtered to 8 clinical sources)
- Patient-friendly names for 1.1M codes
- 2.86M condition→medication associations
- BM25 + SapBERT semantic search indexes

## Local development

```bash
# Build and run locally
docker build -t medterm4ds-fhir .
docker run -p 7860:7860 medterm4ds-fhir

# Test
curl http://localhost:7860/fhir/metadata
```

## Environment variables

Set at container start to tune behavior. All optional unless noted.

**Required for data provisioning:**

| Var | Purpose |
|---|---|
| `UMLS_API_KEY` | NLM UTS API key. Used to download UMLS RRF and build `lookup.duckdb` (license-compliant — your key, your license). |
| `HF_TOKEN` | Hugging Face token. Required only if `MEDTERM4DS_HF_DATASET` is private. |

**Server bind:**

| Var | Default | Purpose |
|---|---|---|
| `MEDTERM4DS_API_HOST` | `127.0.0.1` (localhost sidecar) / `0.0.0.0` (HF Spaces) | Bind host. The Dockerfile forces `0.0.0.0` for HF Spaces reachability — see `SECURITY.md` for the auth implications. |
| `MEDTERM4DS_FHIR_API_PORT` | `8001` (local) / `7860` (HF Spaces) | Bind port. HF Spaces requires 7860. |

**Search + extraction assets:**

| Var | Default | Purpose |
|---|---|---|
| `MEDTERM4DS_SEARCH_INDEX_DIR` | `/mnt/d/fhir4px-model/dist/naming_bm25` | Directory containing `<category>_bm25.json` (6 categories). `$search` lexical/hybrid returns 503 if missing. |
| `MEDTERM4DS_EMBEDDING_MODEL_DIR` | `/mnt/d/fhir4px-model/data/sapbert_finetuned` | SapBERT model dir (must contain `model.safetensors` + `config.json`). `$search` semantic/hybrid returns 503 if missing. |
| `MEDTERM4DS_FHIR4PX_BASELINE` | `/mnt/d/medterm4ds/reports/fhir4px` | Directory containing `patient_friendly_<source>.json` (5 sources). `$lookup` skips patient-friendly custom properties if missing. |

**Request caps:**

| Var | Default | Purpose |
|---|---|---|
| `FHIR_VS_MAX_DEPTH` | `5` | Max depth for `$expand?fhir_vs=isa` descendant walk (layer-by-layer BFS). Covers clinical value-set definitions; deeper needs pre-computed closure. |
| `MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS` | `100000` | Max chars for `$extract` input text. Caps NER executor starvation from megabyte-text inputs. |

**Data provisioning overrides (advanced):**

| Var | Default | Purpose |
|---|---|---|
| `MEDTERM4DS_DATA_DIR` | `/data` | Where `lookup.duckdb` and downloaded derived data live. HF Spaces mounts this as a persistent volume. |
| `MEDTERM4DS_HF_DATASET` | `joelmontavon/medterm4ds-data` | HF dataset repo for BM25 + SapBERT + patient_friendly JSONs. |
| `UMLS_RELEASE` | `2026AA` | UMLS Metathesaurus release version. |
| `MEDTERM4DS_MEMORY_PROFILE` | `low` (in Docker) / `balanced` (elsewhere) | DuckDB memory profile (`low`, `balanced`, `high`). |

**Operational:**

| Var | Default | Purpose |
|---|---|---|
| `MEDTERM4DS_DISABLE_CVX_GROUPS` | unset | If set, disables the runtime CDC CVX-group fetch (CVX lookups fall back through hierarchy). |
| `MEDTERM4DS_CVX_GROUP_URL` | (CDC default) | Override URL for CVX group data. **Must be https + cdc.gov** — anything else is rejected as an SSRF guard. |
