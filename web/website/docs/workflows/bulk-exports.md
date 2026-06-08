---
title: Bulk Exports
---

Bulk workflows stream source inventories through shared services.

Examples:

```bash
medterm4ds bulk lookup \
  --db "$DB" \
  --sources ICD10CM,SNOMEDCT_US \
  --output lookup.jsonl
```

```bash
medterm4ds bulk map \
  --db "$DB" \
  --source ICD10CM \
  --target-source SNOMEDCT_US \
  --output icd10cm-snomed.jsonl
```

Bulk workflows support checkpointing, batching, progress output, and multiple output formats. Use low memory profiles for commodity hardware.
