---
title: FHIR Terminology Server
---

# FHIR R4 Terminology Server

medterm4ds includes a FHIR R4 terminology server that exposes standard terminology
operations plus a custom text-to-code search backed by UMLS, BM25, and fine-tuned
SapBERT embeddings.

## Starting the server

```bash
pip install -e '.[fhir]'
export MEDTERM4DS_DB=/mnt/d/medterm4ds/data/umls_current.duckdb
python -m medterm4ds.apps.fhir_api
```

The server runs on `http://127.0.0.1:8001/fhir/` (localhost-only by default; see
[SECURITY.md](https://github.com/fhir4ds/medterm4ds/blob/main/SECURITY.md)).

## Supported operations

### $lookup

Returns the display name and custom properties for a code.

```bash
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$lookup?system=http://snomed.info/sct&code=44054006"
```

Custom properties returned:

| Property | Description |
|---|---|
| `patient-friendly` | Patient-friendly display name (e.g., "Diabetes Type 2") |
| `canonical-code` | ICD-10 canonical code for association lookup (e.g., "E11") |
| `canonical-system` | Canonical system (e.g., "icd10") |
| `tty` | RxNorm term type (SCD, IN, etc.) |
| `match-type` | How the friendly name was resolved |

### $validate-code

```bash
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$validate-code?system=http://snomed.info/sct&code=44054006"
# → result: true

curl "http://127.0.0.1:8001/fhir/CodeSystem/\$validate-code?system=http://snomed.info/sct&code=FAKE"
# → result: false
```

### $translate

Maps a code from one system to another.

```bash
curl "http://127.0.0.1:8001/fhir/ConceptMap/\$translate?system=http://snomed.info/sct&code=44054006&targetsystem=http://hl7.org/fhir/sid/icd-10-cm"
# → match: E11 (Type 2 diabetes mellitus)
```

### $subsumes

Checks if one code is an ancestor of another in the hierarchy.

```bash
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$subsumes?system=http://snomed.info/sct&codeA=73211009&codeB=44054006"
# → outcome: subsumes (Diabetes subsumes Type 2 diabetes)
```

Outcomes: `equivalent`, `subsumes`, `subsumed-by`, `not-subsumed`.

### $expand

Expands ValueSets — supports text filter, intensional definitions, and explicit lists.

```bash
# Text filter (EHR autocomplete)
curl "http://127.0.0.1:8001/fhir/ValueSet/\$expand?filter=diabetes&count=10"

# Intensional: all descendants of a concept
curl -X POST "http://127.0.0.1:8001/fhir/ValueSet/\$expand" \
  -H "Content-Type: application/fhir+json" \
  -d '{"resourceType":"ValueSet","compose":{"include":[{"system":"http://snomed.info/sct","filter":[{"property":"concept","op":"is-a","value":"73211009"}]}]}}'

# SNOMED fhir_vs URL shorthand
curl "http://127.0.0.1:8001/fhir/ValueSet/\$expand?url=http://snomed.info/sct/73211009?fhir_vs=isa"
```

### $closure

Pre-computes subsumption relationships for O(1) batch checks.

```bash
# Initialize
curl -X POST "http://127.0.0.1:8001/fhir/CodeSystem/\$closure" \
  -H "Content-Type: application/fhir+json" \
  -d '{"resourceType":"Parameters","parameter":[{"name":"name","valueString":"my-closure"}]}'

# Add concepts
curl -X POST "http://127.0.0.1:8001/fhir/CodeSystem/\$closure" \
  -H "Content-Type: application/fhir+json" \
  -d '{"resourceType":"Parameters","parameter":[{"name":"name","valueString":"my-closure"},{"name":"concept","valueCoding":{"system":"http://snomed.info/sct","code":"73211009","display":"Diabetes"}},{"name":"concept","valueCoding":{"system":"http://snomed.info/sct","code":"44054006","display":"Type 2 diabetes"}}]}'
```

### $search (custom operation)

Text-to-code search with three modes:

```bash
# Lexical (BM25, ~1ms) — default
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$search?query=diabetes&searchMode=lexical"

# Semantic (SapBERT + FAISS, ~100ms) — catches novel phrasings
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$search?query=high+blood+sugar&searchMode=semantic"
# → Hyperglycemia (0.80), Elevated Blood Glucose Level (0.80)

# Hybrid (BM25 + SapBERT re-rank, ~110ms)
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$search?query=metformin+pill&searchMode=hybrid"
# → Metformin Pill (1.00, certain)
```

Each result includes `search.score` and a match-grade extension (`certain` / `probable` / `possible`),
modeled after FHIR Patient `$match`.

## System URI mapping

| Internal source | FHIR canonical URI |
|---|---|
| SNOMEDCT_US | `http://snomed.info/sct` |
| RXNORM | `http://www.nlm.nih.gov/research/umls/rxnorm` |
| ICD10CM | `http://hl7.org/fhir/sid/icd-10-cm` |
| ICD10PCS | `http://hl7.org/fhir/sid/icd-10-pcs` |
| LNC | `http://loinc.org` |
| CPT | `http://www.ama-assn.org/go/cpt` |
| HCPCS | `http://terminology.hl7.org/CodeSystem/hcpcs-Level-II` |
| CVX | `http://hl7.org/fhir/sid/cvx` |

OID aliases (e.g., `urn:oid:2.16.840.1.113883.6.96` for SNOMED) are also accepted.

## Deployment

### Local Docker

```bash
python3 deploy/scripts/export_lookup_db.py --output /tmp/lookup.duckdb
docker build -t fhir4ds-fhir deploy/hf-spaces/fhir-server/
docker run -p 7860:7860 \
  -v /tmp/lookup.duckdb:/data/lookup.duckdb:ro \
  -v /mnt/d/medterm4ds/reports/fhir4px:/data/fhir4px:ro \
  fhir4ds-fhir
```

### Hugging Face Spaces

The Space Dockerfile downloads data from HF datasets on first start. See
[deploy/hf-spaces/](https://github.com/fhir4ds/medterm4ds/tree/main/deploy/hf-spaces)
for setup instructions.

## Conformance testing

```bash
make fhir-conformance
```

34 declarative test cases covering all 7 operations + error paths. Validated
against the HAPI FHIR reference server for structural compatibility.

## Demo notebook

See [`notebooks/fhir_terminology_server_demo.ipynb`](https://github.com/fhir4ds/medterm4ds/blob/main/notebooks/fhir_terminology_server_demo.ipynb)
for a complete walkthrough with real clinical examples.
