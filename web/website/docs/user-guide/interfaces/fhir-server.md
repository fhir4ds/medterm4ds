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

**Performance contract for `?fhir_vs=isa` and intensional `is-a` filters:**
descendant walks use layer-by-layer BFS bounded by `FHIR_VS_MAX_DEPTH` (default
5 levels). Covers every clinical value-set definition we've seen; deeper
hierarchies need pre-computed closure (planned). When the depth or count cap is
hit, the response includes the canonical HL7 extension
`http://hl7.org/fhir/StructureDefinition/valueset-toocostly` with
`valueBoolean: true` so clients can detect partial expansions.

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
modeled after FHIR Patient `$match`. The response's `expansion.search.mode` echoes
the requested mode (`lexical`, `semantic`, or `hybrid`) — `hybrid` will internally
fall back to semantic-only if BM25 returns no candidates, but the mode label
stays `hybrid`.

### $extract (custom operation)

Extract coded medical concepts from free text. NER (GLiNER) + clinical NLP
(medspaCy ConText for negation/uncertainty/historical) + code resolution via
the same `$search` machinery.

```bash
# Default: return coded concepts (negated/uncertain excluded)
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$extract?text=Patient%20has%20T2DM.%20No%20CKD."

# Terms only (skip code resolution)
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$extract?text=...&format=terms"
```

Input text is capped at `MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS` (default 100000
chars — ~50 pages of clinical text). POST bodies larger than that return 400
with a clear OperationOutcome.

### /health (liveness probe)

```bash
curl "http://127.0.0.1:8001/health"
# → {"status":"ok","ready":true}
```

Pure async, no DB/executor/model touch. Returns in under 5ms even under peak load.
Use this for liveness/readiness probes — `/fhir/metadata` works too but does
JSON building that could regress.

## System URI mapping

| Internal source | FHIR canonical URI |
|---|---|
| SNOMEDCT_US | `http://snomed.info/sct` |
| RXNORM | `http://www.nlm.nih.gov/research/umls/rxnorm` |
| ICD10CM | `http://hl7.org/fhir/sid/icd-10-cm` |
| ICD10PCS | `http://hl7.org/fhir/sid/icd-10-pcs` |
| LNC | `http://loinc.org` |
| CPT | `http://www.ama-assn.org/go/cpt` |
| HCPCS | `http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets` |
| CVX | `http://hl7.org/fhir/sid/cvx` |

OID aliases (e.g., `urn:oid:2.16.840.1.113883.6.96` for SNOMED) are also accepted.

## Deployment

### Local Docker (recommended)

The container builds lookup.duckdb from UMLS RRF files using your own NLM
API key — fully license-compliant, no UMLS data is redistributed.

```bash
# One-command rebuild + restart (scripts/rebuild_fhir_docker.sh)
scripts/rebuild_fhir_docker.sh

# Or manual:
docker build -f deploy/hf-spaces/fhir-server/Dockerfile -t medterm4ds-fhir .
docker run -p 8001:7860 \
  -e UMLS_API_KEY=your_umls_api_key \
  -e HF_TOKEN=your_hf_token \
  -v fhir4ds-data:/data \
  medterm4ds-fhir
```

First start takes ~10 minutes (download UMLS RRF from NLM + build + download
search indexes). Subsequent starts with cached volume: ~3 seconds. The image
ships with a `HEALTHCHECK` hitting `/health` every 30s — `docker ps` shows
health status.

Without `HF_TOKEN`, the server still works for all operations except `$search`
(BM25 + SapBERT indexes require HF download).

### Tunable env vars

Set at container start. All optional unless noted.

| Var | Default | Purpose |
|---|---|---|
| `UMLS_API_KEY` | (required) | NLM UTS API key — used to download UMLS RRF and build `lookup.duckdb`. |
| `HF_TOKEN` | (optional) | Hugging Face token — only needed if `MEDTERM4DS_HF_DATASET` is private. |
| `MEDTERM4DS_API_HOST` | `127.0.0.1` (local) / `0.0.0.0` (HF Spaces) | Bind host. Docker forces `0.0.0.0` — see [SECURITY.md](https://github.com/fhir4ds/medterm4ds/blob/main/SECURITY.md) for auth implications. |
| `MEDTERM4DS_FHIR_API_PORT` | `8001` (local) / `7860` (HF Spaces) | Bind port. HF Spaces requires 7860. |
| `MEDTERM4DS_SEARCH_INDEX_DIR` | (built-in default) | Directory containing `<category>_bm25.json` (6 categories). `$search` lexical/hybrid returns 503 if missing. |
| `MEDTERM4DS_EMBEDDING_MODEL_DIR` | (built-in default) | SapBERT model dir (must contain `model.safetensors` + `config.json`). `$search` semantic/hybrid returns 503 if missing. |
| `MEDTERM4DS_FHIR4PX_BASELINE` | (built-in default) | Directory containing `patient_friendly_<source>.json` (5 sources). `$lookup` skips patient-friendly properties if missing. |
| `FHIR_VS_MAX_DEPTH` | `5` | Max depth for `$expand?fhir_vs=isa` descendant walk. Covers clinical value-set definitions; deeper needs pre-computed closure (planned). |
| `MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS` | `100000` | Max chars for `$extract` input text. Caps NER executor starvation from megabyte-text inputs. |

The startup banner prints every tunable env var with its current value, so you
can verify config at boot.

### Hugging Face Spaces

The Dockerfile is compatible with HF Spaces (Docker SDK, port 7860). Set
`UMLS_API_KEY` and `HF_TOKEN` as Space secrets. See
[deploy/hf-spaces/](https://github.com/fhir4ds/medterm4ds/tree/main/deploy/hf-spaces)
for details.

### Data licensing

| Data | Source | Licensed? |
|---|---|---|
| `lookup.duckdb` | Built from UMLS RRF (user's NLM key) | User's own UMLS license |
| `patient_friendly_*.json` | HF dataset (derived) | Derived |
| `bm25/*_bm25.json` | HF dataset (derived) | Derived |
| `sapbert/` | HF dataset (derived) | Fine-tuned model |

## Conformance testing

```bash
make fhir-conformance
```

35 declarative test cases covering all 7 operations + `$search` + `$extract` +
error paths (400 missing params, 503 service starting, 503 search assets
missing). Validated structurally via `fhir.resources` pydantic models.

## Demo notebook

See [`notebooks/fhir_terminology_server_demo.ipynb`](https://github.com/fhir4ds/medterm4ds/blob/main/notebooks/fhir_terminology_server_demo.ipynb)
for a complete walkthrough with real clinical examples.
