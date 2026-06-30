---
title: FHIR Terminology Server
emoji: 🏥
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# medterm4ds FHIR R4 Terminology Server

A FHIR R4 terminology server backed by UMLS 2026AA, providing code lookup,
validation, mapping, subsumption, ValueSet expansion, and text-to-code search
(lexical + semantic via fine-tuned SapBERT).

## Operations

| Operation | Description |
|---|---|
| `$lookup` | Code → display name + properties (patient-friendly, canonical, tty) |
| `$validate-code` | Validate a code exists in a code system |
| `$translate` | Map codes between systems (e.g., SNOMED → ICD-10) |
| `$subsumes` | Check hierarchy relationships (is A an ancestor of B?) |
| `$expand` | Expand ValueSets (filter, intensional is-a, explicit, fhir_vs) |
| `$closure` | Maintain closure tables for fast subsumption |
| `$search` | Text → ranked codes (lexical, hybrid, semantic modes) |

## Usage

```bash
# Lookup a SNOMED code
curl "https://[space-name].hf.space/fhir/CodeSystem/\$lookup?system=http://snomed.info/sct&code=44054006"

# Search for codes by text
curl "https://[space-name].hf.space/fhir/CodeSystem/\$search?query=high+blood+sugar&searchMode=semantic"

# Expand a ValueSet
curl "https://[space-name].hf.space/fhir/ValueSet/\$expand?filter=diabetes&count=10"
```

## Data

- UMLS 2026AA (filtered to 8 clinical sources)
- Patient-friendly names for 1.1M codes
- 2.86M condition→medication associations
- BM25 + SapBERT semantic search indexes

## Local development

```bash
# Build and run locally
docker build -t medterm4ds-fhir .
docker run -p 7860:7860 medterm4ds-fhir

# Test
curl http://localhost:7860/fhir/metadata
```
