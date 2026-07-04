---
title: FHIR Terminology Server
---

# Recipe: FHIR R4 Terminology Server

Start a FHIR R4 conformant terminology server backed by UMLS data.

## Start the server

```bash
pip install medterm4ds[fhir]
export MEDTERM4DS_DB=/path/to/umls.duckdb
python -m medterm4ds.apps.fhir_api
# Server runs on http://127.0.0.1:8001/fhir/
```

## Lookup a code

```bash
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$lookup?system=http://snomed.info/sct&code=44054006"
```

Response includes display name, patient-friendly name, and canonical ICD-10 code:

```json
{
  "resourceType": "Parameters",
  "parameter": [
    {"name": "display", "valueString": "Type 2 diabetes mellitus"},
    {"name": "property", "part": [
      {"name": "code", "valueCode": "patient-friendly"},
      {"name": "value", "valueString": "Diabetes Type 2"}
    ]},
    {"name": "property", "part": [
      {"name": "code", "valueCode": "canonical-code"},
      {"name": "value", "valueCode": "E11"}
    ]}
  ]
}
```

## Search for codes by text

```bash
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$search?query=high+blood+sugar&searchMode=semantic"
```

## Conformance

```bash
make fhir-conformance    # 35 test cases covering all 7 operations + $search + $extract + error paths
```

See the [FHIR Terminology Server](../../interfaces/fhir-server.md) page for all 7 operations + custom `$search` + `$extract` + `/health`.
