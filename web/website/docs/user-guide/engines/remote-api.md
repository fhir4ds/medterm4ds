---
title: Remote API Engine
---

The remote API engine lets Python clients call the same terminology services through a server.

Use it when:

- client machines should not download or store UMLS data
- hardware cannot comfortably run local terminology workflows
- a shared service should centralize UMLS licensing and database management
- fast startup matters more than local isolation

Start the API server:

```bash
export MEDTERM4DS_DB=/mnt/d/medterm4ds/data/umls_current.duckdb
uvicorn medterm4ds.apps.api:create_app --factory --host 0.0.0.0 --port 8000
```

Use the same notebook facade against the remote service:

```python
import medterm4ds as mt

terms = mt.connect_remote("http://localhost:8000")
terms.lookup("ICD10CM", "E11.9")
```

The API server should remain a thin interface over the same service layer used by local Python and CLI calls.
