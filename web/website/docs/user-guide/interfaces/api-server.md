---
title: API Server
---

The API server exposes lookup, mapping, hierarchy, resolve, optimize, discovery, and patient-friendly services over HTTP.

## Starting the server

```bash
export MEDTERM4DS_DB=/mnt/d/medterm4ds/data/umls_current.duckdb
python -m medterm4ds.apps.api
```

Or via uvicorn directly (note: must bind to localhost):

```bash
uvicorn medterm4ds.apps.api:create_app --factory --host 127.0.0.1
```

## Local-only multi-process model

The API server binds to `127.0.0.1` by default, making it a local-only sidecar:

- **Local processes** (notebooks, scripts, MCP server) can connect via `http://127.0.0.1:8000`.
- **External networks** cannot reach the server (the OS doesn't route remote traffic to localhost).
- **No auth** is configured — acceptable for localhost-only (local access implies host access).

To override the binding (e.g., behind a reverse proxy):

```bash
export MEDTERM4DS_API_HOST=0.0.0.0  # logs a startup warning
python -m medterm4ds.apps.api
```

See [SECURITY.md](https://github.com/fhir4ds/medterm4ds/blob/main/SECURITY.md) for the full exposure model, enforced limits, and what's NOT protected.

## Request limits

All batch endpoints cap at 10,000 codes per request (`MAX_CODES_PER_REQUEST`). Search queries are capped at 256 characters. The `/health` endpoint returns readiness without leaking the database filesystem path.
