---
title: Docker & HF Spaces
---

# Docker Deployment

Deploy the full FHIR terminology server as a Docker container. Builds lookup.duckdb from UMLS RRF using your own NLM API key — fully license-compliant.

## Quick start

```bash
# Build
docker build -f deploy/hf-spaces/fhir-server/Dockerfile -t medterm4ds-fhir .

# Run
docker run -p 7860:7860 \
  -e UMLS_API_KEY=your_umls_api_key \
  -v medterm4ds-data:/data \
  medterm4ds-fhir
```

First start takes ~10 minutes (downloads UMLS RRF + builds DB + downloads search indexes). Subsequent starts with cached volume: ~3 seconds.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `UMLS_API_KEY` | **Yes** | NLM UMLS API key |
| `HF_TOKEN` | No | Only needed for private HF datasets |
| `UMLS_RELEASE` | No | UMLS version (default: `2026AA`) |
| `MEDTERM4DS_MEMORY_PROFILE` | No | DuckDB memory limit (default: `low` = 512MB) |

## Data provisioning

| Data | How | Size |
|---|---|---|
| lookup.duckdb | Built from UMLS RRF (your NLM key) | 217 MB |
| patient_friendly JSONs | Downloaded from HF (derived) | 225 MB |
| BM25 indexes | Downloaded from HF (derived) | 167 MB |
| SapBERT + FAISS | Downloaded from HF (derived) | 2.5 GB |

## Persistent storage

```bash
docker volume create medterm4ds-data
docker run -p 7860:7860 -e UMLS_API_KEY=xxx -v medterm4ds-data:/data medterm4ds-fhir
```

## Hugging Face Spaces

The Dockerfile is HF Spaces compatible (Docker SDK, port 7860). Set `UMLS_API_KEY` as a Space secret.
