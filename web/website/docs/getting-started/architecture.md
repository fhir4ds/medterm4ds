---
title: Architecture
---

medterm4ds keeps terminology logic in shared services and keeps interfaces thin.

```mermaid
flowchart LR
  UMLS[UMLS release] --> Build[DuckDB builder]
  Build --> DuckDB[(DuckDB)]
  DuckDB --> Engine[Local DuckDB engine]
  Engine --> Services[lookup / map / hierarchy / optimize / resolve]
  Services --> Python[Python]
  Services --> CLI[CLI]
  Services --> API[API]
  Services --> MCP[MCP]
  Services --> Bulk[Bulk exports]
```

Core rules:

- Services own behavior.
- Engines own data access.
- CLI, API, and MCP adapt inputs and outputs.
- Bulk workflows stream over the same services.
- Models carry provenance such as `match_type`, `match_depth`, and `matched_via`.

This keeps local mode, API mode, bulk mode, and MCP mode from becoming separate implementations.
