---
title: FHIR ConceptMap
---

FHIR ConceptMap export is used for interoperability workflows.

```bash
medterm4ds conceptmap mapping \
  --db "$DB" \
  --source ICD10CM \
  --target-source SNOMEDCT_US \
  --output icd10cm-snomed.json \
  --format fhir-json
```

ConceptMap exports should preserve mapping provenance so consumers can distinguish exact matches from fallback mappings.
