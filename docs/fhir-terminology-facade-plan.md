# FHIR R4 Terminology Server Facade — Implementation Plan

## Context

medterm4ds has the terminology data and logic (hierarchy, mapping, lookup, patient-friendly naming, code resolution). A FHIR R4 terminology facade exposes this via standard FHIR operations, making it consumable by any FHIR client (EHRs, CDS Hooks, SMART apps). The app team has also built BM25 indexes and embedding models for text-to-code inference; a custom `$search` operation (modeled after Patient `$match`) exposes this as a ranked, FHIR-formatted search endpoint.

Design decisions (confirmed):
- **Separate module** (`apps/fhir_api.py`), not integrated into existing `apps/api.py`
- **FHIR R4** target
- **Localhost-only** deployment (same model as current API per `SECURITY.md`)
- **Pre-computed + live split**: $lookup/$validate-code serve from JSONs where possible, $expand/$subsumes/$search use live DuckDB or BM25

## Architecture

```
Client (FHIR SDK / curl / EHR)
       ↓ HTTP
apps/fhir_api.py (FastAPI, 127.0.0.1:8001)
       ↓
engines/fhir/ — FHIR response builders + system URI mapping
       ↓
services/ — existing lookup, mapping, hierarchy, discovery, patient_friendly
       ↓
engines/duckdb/ — existing DuckDB engine
```

## Existing search assets (at `/mnt/d/fhir4px-model`)

The app team has already built and fine-tuned both lexical and semantic search indexes.

### BM25 indexes (lexical mode)

**Location**: `/mnt/d/fhir4px-model/dist/naming_bm25/` (also published to `joelmontavon/fhir4px-bm25` on HuggingFace)

| File | Size | Records |
|---|---|---|
| `medication_bm25.json` | 29 MB | 124K |
| `lab_bm25.json` | 35 MB | 116K |
| `condition_bm25.json` | 48 MB | 201K |
| `procedure_bm25.json` | 46 MB | 155K |
| `vaccine_bm25.json` | 68 KB | 291 |
| `body_structure_bm25.json` | 9 MB | 40K |
| `bm25_resolver.js` | 8 KB | Browser-side resolver |
| **Total** | **192 MB** | |

### Fine-tuned SapBERT + FAISS indexes (semantic mode)

**Location**: `/mnt/d/fhir4px-model/data/sapbert_finetuned/`

| File | Size | Purpose |
|---|---|---|
| `model.safetensors` | 438 MB | Fine-tuned SapBERT (110M params) |
| `tokenizer.json` + `config.json` | small | Tokenizer + model config |
| `medication_faiss.index` + `medication_metadata.json` | 393 MB | 117K medication records (incl. ATC) |
| `lab_faiss.index` + `lab_metadata.json` | 377 MB | 116K lab records |
| `condition_faiss.index` + `condition_metadata.json` | 652 MB | 201K condition records |
| `procedure_faiss.index` + `procedure_metadata.json` | 509 MB | 155K procedure records |
| `vaccine_faiss.index` + `vaccine_metadata.json` | 937 KB | 291 vaccine records |
| `body_structure_faiss.index` + `body_structure_metadata.json` | 128 MB | 40K body structure records |
| **Total** | **2.4 GB** | |

Usage: load SapBERT model, embed a query text, search the FAISS index for matching codes.

## BM25 vs Embeddings — cascade design for $search

The $search operation uses a **two-stage cascade**, backed by the existing assets above:

```
Query: "my chest feels tight"
  │
  ├─ Stage 1: BM25 retrieval (always runs for lexical/hybrid)
  │   Input: query tokens → pre-built BM25 JSON index lookup
  │   Index: /mnt/d/fhir4px-model/dist/naming_bm25/<category>_bm25.json
  │   Output: top 50 candidates with BM25 scores
  │   Speed: ~1ms
  │   Coverage: 80-90% of queries (when tokens match)
  │
  ├─ If BM25 returns ≥3 results AND searchMode="hybrid":
  │   └─ Stage 2: SapBERT re-ranking
  │       Input: top 50 BM25 candidates + query embedding from fine-tuned SapBERT
  │       Model: /mnt/d/fhir4px-model/data/sapbert_finetuned/model.safetensors
  │       Index: /mnt/d/fhir4px-model/data/sapbert_finetuned/<category>_faiss.index
  │       Output: re-ranked top 10 by cosine similarity
  │       Speed: ~50-100ms (model inference on 50 candidates)
  │       Improvement: catches BM25 ranking errors
  │
  └─ If BM25 returns 0-2 results AND searchMode in ("hybrid","semantic"):
      └─ Fallback: SapBERT embedding-only search
          Input: query embedding → FAISS ANN search on full index
          Index: /mnt/d/fhir4px-model/data/sapbert_finetuned/<category>_faiss.index
          Output: top 10 by cosine similarity
          Speed: ~100ms
          Coverage: novel phrasings with zero token overlap
```

The `searchMode` parameter controls behavior:
- `lexical` (default): Stage 1 only. Fast, deterministic. Sufficient for 80-90% of queries.
- `hybrid`: Stage 1 + Stage 2. Best accuracy. ~110ms per query.
- `semantic`: Skip BM25, go straight to embedding-only. For queries where lexical is known to fail.

**Match-grade mapping** (modeled after Patient $match):
- BM25 score ≥ 0.8 OR embedding cosine ≥ 0.92 → `certain`
- BM25 score ≥ 0.4 OR embedding cosine ≥ 0.75 → `probable`
- Everything else → `possible`

## Implementation phases

### Phase 1 — MVP (1 week)

Four operations that cover the highest-value use cases.

#### 1.1 FHIR system URI mapping

**File**: `src/medterm4ds/engines/fhir/__init__.py` (new)

Extend the existing `FHIR_CODE_SYSTEMS` table from `outputs/fhir.py` into a bidirectional mapping:

```python
# Internal name → FHIR canonical URI (for responses)
SYSTEM_TO_FHIR_URI = {
    "SNOMEDCT_US": "http://snomed.info/sct",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10PCS": "http://hl7.org/fhir/sid/icd-10-pcs",
    "LNC": "http://loinc.org",
    "CPT": "http://www.ama-assn.org/go/cpt",
    "HCPCS": "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets",
    "CVX": "http://hl7.org/fhir/sid/cvx",
}

# FHIR canonical URI → Internal name (for request parsing)
FHIR_URI_TO_SYSTEM = {v: k for k, v in SYSTEM_TO_FHIR_URI.items()}
```

Reuses existing `outputs/fhir.py:code_system_uri()` for the forward direction.

#### 1.2 FHIR response builders

**File**: `src/medterm4ds/engines/fhir/responses.py` (new, ~300 lines)

Builders for each FHIR resource type the facade returns:

- `build_parameters_lookup(code_info: CodeInfo) -> dict` — Parameters resource for $lookup
- `build_parameters_validate(result: bool, code_info: CodeInfo | None) -> dict` — $validate-code
- `build_bundle_search(results: list[NameSearchResult], scores: list[float], grades: list[str]) -> dict` — Bundle for $search (modeled after Patient $match)
- `build_operation_outcome(severity, code, diagnostics) -> dict` — FHIR error responses
- `build_capability_statement() -> dict` — advertises supported operations

Custom properties for $lookup (surface medterm4ds-specific data via FHIR `property` parameter):
- `patient-friendly` — the patient-friendly display name
- `canonical-code` / `canonical-system` — ICD-10 equivalent (from patient_friendly JSONs)
- `tty` — RxNorm term type
- `ingredient-codes` — RxNorm ingredient IN codes
- `semantic-types` — UMLS TUIs
- `match-type` — how the patient-friendly name was resolved

#### 1.3 FHIR API server

**File**: `src/medterm4ds/apps/fhir_api.py` (new, ~400 lines)

FastAPI app with FHIR-compliant endpoints. Reuses the same `LocalDuckDBEngine` lifespan pattern from `apps/api.py`.

```
GET  /fhir/metadata                          — CapabilityStatement
GET  /fhir/CodeSystem/$lookup               — code → details + properties
POST /fhir/CodeSystem/$lookup               — same, via Coding in body
GET  /fhir/CodeSystem/$validate-code        — is code valid?
POST /fhir/CodeSystem/$validate-code        — same, via Coding in body
GET  /fhir/ConceptMap/$translate            — code → target system mapping
POST /fhir/ConceptMap/$translate            — same, via body
POST /fhir/CodeSystem/$search               — text → ranked codes (custom)
```

Each endpoint:
1. Parses FHIR parameters (system URI → internal source name, code string)
2. Calls existing service function (`get_code_infos`, `get_code_mappings`, etc.)
3. Wraps result in FHIR response builder
4. Returns FHIR resource (Parameters, Bundle, or OperationOutcome for errors)

For $lookup specifically: check patient_friendly JSONs first (pre-computed, fast), fall back to DuckDB `get_code_infos()` for properties not in JSONs.

#### 1.4 $search operation

**File**: `src/medterm4ds/apps/fhir_api.py` (within the same module)

The $search endpoint accepts:
```
POST /fhir/CodeSystem/$search
Body: Parameters {
  query: "high blood sugar"     (required)
  system: http://snomed.info/sct  (optional; searches all if omitted)
  count: 10                      (default 20)
  searchMode: "lexical" | "hybrid" | "semantic"  (default "lexical")
}
```

Returns a Bundle of Coding resources with `search.score` and `match-grade` extension.

**BM25 index**: Loads pre-built BM25 JSON indexes from `MEDTERM4DS_SEARCH_INDEX_DIR` (default: `/mnt/d/fhir4px-model/dist/naming_bm25/`). One JSON per category (`condition_bm25.json`, `medication_bm25.json`, etc.). These are the same indexes published to `joelmontavon/fhir4px-bm25` on HuggingFace. The endpoint loads all 6 category indexes on startup (~192 MB total, ~2s load time).

**Embedding index**: Deferred to Phase 2. The `searchMode: "hybrid"` and `"semantic"` options return 503 (not yet implemented) in Phase 1. `searchMode: "lexical"` is the Phase 1 default.

**Config**: `MEDTERM4DS_SEARCH_INDEX_DIR` env var controls the BM25 index directory. Defaults to `/mnt/d/fhir4px-model/dist/naming_bm25/`. Phase 2 adds `MEDTERM4DS_EMBEDDING_MODEL_DIR` (default: `/mnt/d/fhir4px-model/data/sapbert_finetuned/`).

### Phase 2 — Full terminology service (2-3 weeks)

Add the remaining operations and embedding integration.

#### 2.1 $subsumes

```
GET /fhir/CodeSystem/$subsumes?system=...&codeA=...&codeB=...
→ outcome: "equivalent" | "subsumes" | "subsumed-by" | "not-subsumed"
```

Implementation: check if codeB is in codeA's descendants → `subsumes`. Check if codeA is in codeB's descendants → `subsumed-by`. Both → `equivalent`. Neither → `not-subsumed`. Uses existing `get_descendants()` / `get_ancestors()`.

#### 2.2 $expand

```
GET /fhir/ValueSet/$expand?url=...&filter=...&count=...
→ ValueSet resource with expansion.contains[]
```

Three modes:
- **Explicit ValueSet** (by URL or inline): return the code list directly
- **Filter expansion**: `search_names()` with LIKE matching (powers EHR dropdowns)
- **Intensional expansion**: `get_descendants()` of a root code (for "all codes where is-a X")

#### 2.3 Embedding integration for $search

Add the cascade using the existing fine-tuned SapBERT + FAISS assets:
- Load fine-tuned SapBERT model from `MEDTERM4DS_EMBEDDING_MODEL_DIR` (default: `/mnt/d/fhir4px-model/data/sapbert_finetuned/`)
- Load pre-built FAISS indexes (one per category, ~2.4 GB total) from the same directory
- Embed the query text using SapBERT (110M params, ~438 MB model)
- Search the appropriate category FAISS index (L2 or cosine ANN)
- Implement `searchMode: "hybrid"` (BM25 retrieve → SapBERT re-rank top 50)
- Implement `searchMode: "semantic"` (SapBERT embedding-only search on full FAISS index)

Model loading: ~5-10 seconds on startup (438 MB safetensors → GPU or CPU inference). FAISS index loading: ~2-3 seconds per category. Total cold-start overhead: ~15-20 seconds if both BM25 + SapBERT are loaded. The server should load BM25 eagerly and SapBERT lazily (on first `hybrid` or `semantic` request).

#### 2.4 $closure

Maintain transitive closure tables for fast $subsumes on large code sets. Optional; only needed if $subsumes performance is insufficient for real-time use.

### Phase 3 — Conformance (1 week)

- Publish CapabilityStatement with all supported operations
- Add OperationDefinition resources for $search
- Test against FHIR Connectathon test cases
- Verify compatibility with common FHIR client libraries (hapi-fhir, fhir.js, fhirclient)

## Files to create/modify

**New files**:
- `src/medterm4ds/apps/fhir_api.py` — FastAPI server with FHIR endpoints
- `src/medterm4ds/engines/fhir/__init__.py` — FHIR system URI mapping
- `src/medterm4ds/engines/fhir/responses.py` — FHIR response builders
- `tests/test_fhir_api.py` — endpoint tests
- `tests/test_fhir_responses.py` — response builder unit tests

**Modified files**:
- `src/medterm4ds/__init__.py` — export `create_fhir_app`
- `src/medterm4ds/outputs/fhir.py` — extend `FHIR_CODE_SYSTEMS` with bidirectional mapping
- `pyproject.toml` — add `fhir` optional extra (lightweight: same as `api` + `rank-bm25`)
- `Makefile` — add `fhir-smoke` target

**Reused without modification**:
- `src/medterm4ds/services/lookup.py` — `get_code_infos()` for $lookup
- `src/medterm4ds/services/mapping.py` — `get_code_mappings()` for $translate
- `src/medterm4ds/services/hierarchy.py` — `get_ancestors()` / `get_descendants()` for $subsumes
- `src/medterm4ds/services/discovery.py` — `search_names()` for $expand filter
- `src/medterm4ds/services/patient_friendly.py` — for patient-friendly $lookup properties
- `src/medterm4ds/outputs/fhir.py` — existing ConceptMap serialization, code_system_uri()

## pyproject.toml extras

```toml
fhir = [
    "duckdb>=0.9.0",
    "fastapi>=0.110.0",
    "uvicorn>=0.27.0",
    "rank-bm25>=0.2.2",     # BM25 for $search Phase 1
]
```

Phase 2 adds embedding deps:
```toml
fhir-semantic = [
    "sentence-transformers>=2.0.0",  # embedding models
    "faiss-cpu>=1.7.0",              # ANN index
]
```

## Verification

1. **Unit tests**: each response builder produces valid FHIR resources (JSON schema check)
2. **Endpoint tests**: each FHIR operation returns correct results for known codes
3. **$lookup**: `GET /fhir/CodeSystem/$lookup?system=http://snomed.info/sct&code=44054006` returns Parameters with display + patient-friendly property + canonical-code property
4. **$validate-code**: valid code returns result=true; invalid code returns result=false + OperationOutcome
5. **$translate**: SNOMED → ICD-10 returns ConceptMap element
6. **$search (bm25)**: "diabetes" returns ranked Bundle with match-grade extensions
7. **CapabilityStatement**: `/fhir/metadata` lists all supported operations
8. **FHIR validator**: responses pass http://validator.fhir.org validation
9. **Hermetic tests**: 320+ existing tests still pass (no behavioral change to existing code)

## Time estimate

| Phase | Effort | Deliverable |
|---|---|---|
| 1.1 URI mapping | 2 hours | Bidirectional source ↔ FHIR URI table |
| 1.2 Response builders | 1-2 days | Parameters, Bundle, OperationOutcome, CapabilityStatement |
| 1.3 FHIR API server | 2 days | $lookup, $validate-code, $translate endpoints |
| 1.4 $search (BM25) | 1-2 days | $search with BM25 index, match-grade ranking |
| Tests | 1 day | Unit + endpoint tests for all 4 operations |
| **Phase 1 total** | **~1 week** | MVP with $lookup, $validate-code, $translate, $search |
| Phase 2 | 2-3 weeks | $subsumes, $expand, embedding integration |
| Phase 3 | 1 week | Conformance, Connectathon, client compatibility |
| **Total** | **~4-5 weeks** | Full FHIR R4 terminology server |
