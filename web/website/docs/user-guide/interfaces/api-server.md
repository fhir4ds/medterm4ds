---
title: API Server
---

The API server exposes lookup, mapping, hierarchy, resolve, optimize, discovery, and patient-friendly services over HTTP.

```bash
export MEDTERM4DS_DB=/mnt/d/medterm4ds/data/umls_current.duckdb
uvicorn medterm4ds.apps.api:create_app --factory
```

The server should be deployed close to the UMLS database and protected according to the sensitivity of the calling environment.
