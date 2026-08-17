---
title: Supported Sources
---

Medical Terminology for Data Science is built around UMLS-backed source vocabularies.

Local database contents are UMLS-release specific. A recent local build includes
these core sources:

| Source | Primary use | Verified active codes |
| --- | --- | ---: |
| `ICD10CM` | diagnosis coding | 98,506 |
| `ICD10PCS` | inpatient procedure coding | 192,560 |
| `SNOMEDCT_US` | clinical concepts and hierarchy | 386,110 |
| `RXNORM` | medications and RxCUIs | 124,919 |
| `LNC` | LOINC labs and observations | 301,558 |
| `CVX` | vaccines | 288 |
| `CPT` | procedures | 15,468 |
| `HCPCS` | procedures, supplies, drugs | 7,685 |

Regenerate counts for your build with:

```bash
medterm4ds data verify \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --sources ICD10CM,ICD10PCS,SNOMEDCT_US,RXNORM,LNC,CVX,CPT,HCPCS
```

Supported operations vary by source because UMLS coverage differs by vocabulary. Lookup and search are broad. Mapping, hierarchy, obsolete-code handling, and patient-friendly naming depend on available atoms, relationships, and attributes.
