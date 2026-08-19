---
title: Docker & HF Spaces
---

# Docker Deployment

Deploy the full FHIR terminology server as a Docker container. Builds lookup.duckdb from UMLS RRF using your own NLM API key — fully license-compliant.

## Quick start

```bash
# One-command rebuild + restart (stops existing container if any,
# waits for /health, prints startup banner)
scripts/rebuild_fhir_docker.sh

# Or manual:
docker build -f deploy/hf-spaces/fhir-server/Dockerfile -t medterm4ds-fhir .
docker run -p 8001:7860 \
  -e UMLS_API_KEY=your_umls_api_key \
  -v fhir4ds-data:/data \
  medterm4ds-fhir
```

First start takes ~10 minutes (downloads UMLS RRF + builds DB + downloads search indexes). Subsequent starts with cached volume: ~30 seconds. The image ships with a `HEALTHCHECK` hitting `/health` every 30s — `docker ps` shows health status, HF Spaces may pick this up.

## Environment variables

Required:

| Variable | Description |
|---|---|
| `UMLS_API_KEY` | NLM UMLS API key — used to download UMLS RRF and build `lookup.duckdb`. |

Optional (server bind):

| Variable | Default | Description |
|---|---|---|
| `MEDTERM4DS_API_HOST` | `127.0.0.1` (local) / `0.0.0.0` (HF Spaces) | Bind host. Docker forces `0.0.0.0` for HF Spaces reachability — see [SECURITY.md](https://github.com/fhir4ds/medterm4ds/blob/main/SECURITY.md) for auth implications. |
| `MEDTERM4DS_FHIR_API_PORT` | `8001` (local) / `7860` (HF Spaces) | Bind port. HF Spaces requires 7860. |

Optional (data provisioning):

| Variable | Default | Description |
|---|---|---|
| `HF_TOKEN` | unset | HF token — only needed for private datasets. |
| `UMLS_RELEASE` | `2026AA` | UMLS Metathesaurus release version. |
| `MEDTERM4DS_DATA_DIR` | `/data` | Where `lookup.duckdb` and downloaded derived data live. |
| `MEDTERM4DS_HF_DATASET` | `joelmontavon/medterm4ds-data` | HF dataset repo for BM25 + SapBERT + patient_friendly JSONs. |

Optional (search + extraction assets):

| Variable | Default | Description |
|---|---|---|
| `MEDTERM4DS_SEARCH_INDEX_DIR` | (built-in default) | Directory with `<category>_bm25.json`. `$search` lexical/hybrid returns 503 if missing. |
| `MEDTERM4DS_EMBEDDING_MODEL_DIR` | (built-in default) | SapBERT model dir. `$search` semantic/hybrid returns 503 if missing. |
| `MEDTERM4DS_FHIR4PX_BASELINE` | (built-in default) | Directory with `patient_friendly_<source>.json`. `$lookup` skips patient-friendly properties if missing. |

Optional (request caps):

| Variable | Default | Description |
|---|---|---|
| `FHIR_VS_MAX_DEPTH` | `5` | Cap on `$expand?fhir_vs=isa` descendant depth. Covers clinical value-set definitions; deeper needs pre-computed closure. |
| `MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS` | `100000` | Cap on `$extract` input length (~50 pages of clinical text). |

Optional (operational):

| Variable | Default | Description |
|---|---|---|
| `MEDTERM4DS_MEMORY_PROFILE` | `low` (in Docker) | DuckDB memory profile (`low`, `balanced`, `high`). |
| `MEDTERM4DS_DEVICE` | `auto` | torch device for GLiNER/SapBERT inference (`auto`, `cpu`, `cuda`, `cuda:<n>`, `mps`). GPU use in Docker requires the nvidia container runtime; with it, `auto` picks the GPU automatically. |
| `MEDTERM4DS_DISABLE_CVX_GROUPS` | unset | If set, disables the runtime CDC CVX-group fetch. |
| `MEDTERM4DS_CVX_GROUP_URL` | (CDC default) | Override URL for CVX group data. **Must be https + cdc.gov** — anything else is rejected as an SSRF guard. |

The startup banner prints every tunable env var with its current value.

## Data provisioning

| Data | How | Size |
|---|---|---|
| lookup.duckdb | Built from UMLS RRF (your NLM key) | 217 MB |
| patient_friendly JSONs | Downloaded from HF (derived) | 225 MB |
| BM25 indexes | Downloaded from HF (derived) | 167 MB |
| SapBERT + FAISS | Downloaded from HF (derived) | 2.5 GB |

## Persistent storage

```bash
docker volume create fhir4ds-data
docker run -p 8001:7860 -e UMLS_API_KEY=xxx -v fhir4ds-data:/data medterm4ds-fhir
```

The volume name `fhir4ds-data` is the legacy name (renaming would lose the cached `lookup.duckdb` — 8-min rebuild). Keep it as-is.

## Hugging Face Spaces

The Dockerfile is HF Spaces compatible (Docker SDK, port 7860). Set `UMLS_API_KEY` (required) and `HF_TOKEN` (only if your HF dataset is private) as Space secrets.

**Auth note:** HF Spaces does not authenticate inbound requests to a public Space. Anyone with the Space URL can call `$lookup`, `$extract`, `$search`, etc. against your UMLS-API-key-built DB. For non-personal deployments, use a private Space or front the deployment with an authenticating proxy. See [SECURITY.md](https://github.com/fhir4ds/medterm4ds/blob/main/SECURITY.md) for the full threat model.
