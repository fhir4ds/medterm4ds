---
title: ConceptMaps
---

ConceptMap export turns mapping results into downstream interoperability artifacts.

Build ConceptMap rows in Python:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")

mapping_rows = terms.mapping_conceptmap_df(
    "ICD10CM",
    ["E11.9", "E11.40"],
    target_sources=["SNOMEDCT_US"],
)

friendly_rows = terms.conceptmap_df("ICD10CM", ["E11.9", "E11.40"])
```

Use the CLI for FHIR JSON files. Mapping ConceptMap:

```bash
medterm4ds conceptmap mapping \
  --db "$DB" \
  --source ICD10CM \
  --target-source SNOMEDCT_US \
  --output icd10cm-snomed.json \
  --format fhir-json
```

Patient-friendly ConceptMap file:

```bash
medterm4ds conceptmap patient-friendly \
  --db "$DB" \
  --sources ICD10CM,RXNORM,LNC \
  --output patient-friendly.json \
  --format fhir-json
```

ConceptMap rows should preserve review fields so downstream teams can distinguish exact matches from hierarchy fallback.
