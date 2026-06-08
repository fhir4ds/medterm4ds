---
title: JSON, JSONL, and CSV
---

Structured output is intended for scripts, batch workflows, and downstream data pipelines.

Common formats:

- JSON for single-command inspection
- JSONL for streaming bulk results
- CSV for spreadsheet review or simple ingestion

Example:

```bash
medterm4ds bulk lookup \
  --db "$DB" \
  --sources ICD10CM \
  --output lookup.jsonl \
  --format jsonl
```
