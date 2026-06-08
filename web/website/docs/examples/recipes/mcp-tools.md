---
title: MCP Tools
---

Start the MCP server:

```bash
export MEDTERM4DS_DB=/mnt/d/medterm4ds/data/umls_current.duckdb
python -m medterm4ds.apps.mcp
```

Useful compact tool calls:

```json
{
  "source": "ICD10CM",
  "code": "E11.9",
  "output_format": "table"
}
```

Use `output_format: "tree"` for hierarchy and optimize responses. Use `output_format: "dict"` when another tool needs to parse the result.
