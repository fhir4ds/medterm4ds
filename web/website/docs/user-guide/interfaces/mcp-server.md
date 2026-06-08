---
title: MCP Server
---

The MCP server exposes compact terminology tools for agents.

```bash
export MEDTERM4DS_DB=/mnt/d/medterm4ds/data/umls_current.duckdb
python -m medterm4ds.apps.mcp
```

MCP tools are wrappers over the same services used by Python and CLI:

- lookup
- patient-friendly names
- mapping
- hierarchy
- discovery
- NDC/RxNorm resolution
- optimize
- domain wrappers for diagnosis, lab, drug, procedure, vaccine, and HCPCS workflows

Prefer `output_format: "table"` or `output_format: "tree"` for compact responses.
