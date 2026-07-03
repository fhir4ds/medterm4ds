# Text-to-Concepts Extraction Service — Implementation Plan

## Context

medterm4ds currently answers "given a **code**, what do we know about it?" The
inverse — "given **free text**, what codes are in it?" — is the missing half.
This plan adds a text extraction service that takes clinical free text and
returns coded medical concepts, using NER + clinical NLP + the existing search
engine (BM25 + SapBERT).

This is a **generic terminology capability**, not specific to any consumer
(FHIR, CQL, ETL, notebooks). fhir4ds, the app team, or any downstream
consumer calls medterm4ds for extraction and handles their own
resource-specific concerns.

## Pipeline

```
Free text
  │
  ├─ 1. medspaCy preprocessing
  │     ├── Sentence segmentation
  │     ├── Section detection (Assessment, PMH, Problem List, etc.)
  │     └── ConText (negation, uncertainty, temporality)
  │
  ├─ 2. NER (token-classification model)
  │     └── Extract entity spans with type labels
  │
  ├─ 3. Filtering
  │     ├── Section allow-list (default: problem-list-equivalent sections)
  │     └── ConText status (exclude negated, uncertain by default)
  │
  ├─ 4. Category mapping
  │     └── NER type → search category (disease → condition, drug → medication, etc.)
  │
  ├─ 5. Code search
  │     └── Each surviving span → SearchService.search(span, mode=hybrid)
  │
  ├─ 6. Confidence filtering
  │     └── Keep results above threshold (default: match_grade='certain')
  │
  └─ 7. Deduplication + ranking
        └── Same code from overlapping spans → keep highest score
```

## Result model

```python
@dataclass
class ExtractedConcept:
    """One medical concept extracted from free text."""

    code: str
    source: str            # SNOMEDCT_US, RXNORM, ICD10CM, etc.
    display: str           # patient-friendly or canonical display
    matched_text: str      # the NER span that triggered this concept
    status: str            # "affirmed" | "negated" | "uncertain" | "history_of"
    section: str | None    # medspaCy section name (e.g., "Assessment")
    confidence: float      # search score (0.0–1.0)
    match_grade: str       # "certain" | "probable" | "possible"
    category: str          # "condition" | "medication" | "lab" | etc.
    span_start: int        # character offset in source text
    span_end: int          # character offset in source text

    def to_dict(self) -> dict[str, Any]: ...
    def to_coderef(self) -> CodeRef: ...
```

## Dependencies

| Dependency | Size | Purpose | Optional? |
|---|---|---|---|
| `medspacy` | ~50 MB (pip) | Section detection + ConText NLP | **Required** for extraction |
| `spacy` (medspaCy dep) | ~50 MB | Tokenization, sentence segmentation | Auto-installed with medspaCy |
| medspaCy section model | ~20 MB | Section classification | Auto-downloaded on first use |
| NER model (`d4data/biomedical-ner-all`) | ~440 MB | Entity span extraction | Auto-downloaded on first use |
| Existing SearchService | — | BM25 + SapBERT code search | Already deployed |

**Total additional footprint**: ~560 MB (models). No GPU required.

Behind a new `[medterm4ds,extraction]` extra:

```toml
extraction = [
    "medspacy>=1.0.0",
    "transformers>=4.30.0",
    # Search deps (torch, faiss) already in [fhir-semantic]
]
```

## API design

### Python (module-level)

```python
import medterm4ds as mt

# Single text
concepts = mt.extract_concepts(
    "Patient has T2DM on metformin. No CKD. Denies chest pain.",
    categories=["condition", "medication"],
)
# → [
#   ExtractedConcept(code="44054006", display="Type 2 diabetes mellitus",
#       matched_text="T2DM", status="affirmed", confidence=0.92, ...),
#   ExtractedConcept(code="860975", display="Metformin Oral Product",
#       matched_text="metformin", status="affirmed", confidence=0.95, ...),
#   # CKD and chest pain EXCLUDED (negated)
# ]

# Batch with caching
results = mt.extract_concepts_batch(
    texts=[note1, note2, ...],
    categories=["condition"],
    mode="hybrid",
    min_confidence=0.8,
)
```

### Python (Terminology facade)

```python
terms = mt.connect("/path/to/umls.duckdb")
concepts = terms.extract(
    "History of MI, currently on lisinopril and atorvastatin",
    categories=["condition", "medication"],
)
```

### CLI

```bash
# From a string
medterm4ds extract "Patient has diabetes, on metformin" --categories condition,medication

# From a file
medterm4ds extract --input clinical_notes.txt --output concepts.json

# Adjust thresholds
medterm4ds extract "..." --min-grade probable --mode semantic
```

### MCP tool

```python
extract_concepts(
    text="Patient has T2DM on metformin",
    categories=["condition", "medication"],
    mode="hybrid",
    min_grade="certain",
    section_allowlist=["Assessment", "Problem List"],
)
```

### FHIR operation (custom `$extract`)

```
POST /fhir/CodeSystem/$extract
Body: Parameters {
  text: "Patient has T2DM on metformin. No CKD.",
  categories: ["condition", "medication"],
  mode: "hybrid",
  minGrade: "certain"
}

→ Bundle of Coding resources with match-grade extensions,
  plus a `status` extension (affirmed/negated) and
  `matched-text` extension (the NER span).
```

## Configuration

| Env var | Default | Description |
|---|---|---|
| `MEDTERM4DS_SEARCH_INDEX_DIR` | `/mnt/d/fhir4px-model/dist/naming_bm25` | BM25 indexes (existing) |
| `MEDTERM4DS_EMBEDDING_MODEL_DIR` | `/mnt/d/fhir4px-model/data/sapbert_finetuned` | SapBERT + FAISS (existing) |
| `MEDTERM4DS_NER_MODEL` | `d4data/biomedical-ner-all` | HuggingFace NER model name |
| `MEDTERM4DS_EXTRACTION_MODE` | `hybrid` | Default search mode for extraction |
| `MEDTERM4DS_EXTRACTION_MIN_GRADE` | `certain` | Default minimum match grade |
| `MEDTERM4DS_SECTION_ALLOWLIST` | `Assessment,Problem List,Past Medical History,Diagnosis` | Default section filter |

## Category mapping

NER models output generic entity types. These map to medterm4ds search categories:

| NER type | Search category | Example |
|---|---|---|
| `DISEASE`, `DISORDER`, `SYMPTOM` | `condition` | "diabetes" |
| `CHEMICAL`, `DRUG` | `medication` | "metformin" |
| `DISEASE` (lab context) | `lab` | "HbA1c" |
| `PROCEDURE` | `procedure` | "appendectomy" |

Mapping table in the service, overridable via constructor.

## ConText status handling

medspaCy ConText assigns attributes to each entity mention:

| ConText status | Default action | Configurable? |
|---|---|---|
| `affirmed` (positive, current) | **Include** | Yes |
| `negated` ("no evidence of CKD") | **Exclude** | Yes |
| `uncertain` ("possibly pneumonia") | **Exclude** | Yes |
| `historical` ("history of MI") | **Exclude** (for current problems) | Yes |

The `status` field is preserved in `ExtractedConcept` so callers can filter
post-hoc if they want different rules (e.g., include historical for PMH lists).

## Caching

Text-hash cache for batch processing:

```sql
CREATE TABLE IF NOT EXISTS extraction_cache (
    text_hash VARCHAR,         -- sha256(normalized_text)
    categories VARCHAR,        -- comma-separated category filter
    search_mode VARCHAR,
    index_version VARCHAR,     -- search index version
    result_json VARCHAR,       -- JSON list[ExtractedConcept]
    cached_at TIMESTAMP,
    PRIMARY KEY (text_hash, categories, search_mode, index_version)
);
```

Cache lives in a DuckDB sidecar (default: `extraction_cache.duckdb` in the
data directory). Normalization for hash: lowercase, strip punctuation,
collapse whitespace.

## Implementation phases

### Phase 1: Core extraction service (~1 day)

**Files to create:**
- `src/medterm4ds/services/extraction.py` — `ExtractionService`, `ExtractedConcept`
- `tests/test_extraction.py` — unit tests with synthetic text

**Exit criteria:** `extract("T2DM on metformin, no CKD")` returns diabetes +
metformin concepts, excludes CKD.

### Phase 2: Wire into all surfaces (~half day)

**Files to modify:**
- `src/medterm4ds/__init__.py` — export `extract_concepts`, `extract_concepts_batch`
- `src/medterm4ds/client.py` — `Terminology.extract()` method
- `src/medterm4ds/apps/cli.py` — `medterm4ds extract` subcommand
- `src/medterm4ds/apps/mcp.py` — `extract_concepts` tool
- `src/medterm4ds/apps/fhir_api.py` — `$extract` endpoint

**Exit criteria:** All four surfaces return the same results for the same text.

### Phase 3: Caching + batch optimization (~half day)

**Files to create:**
- `src/medterm4ds/services/extraction_cache.py` — DuckDB-backed text-hash cache

**Exit criteria:** Second call with duplicate text returns cached result in <1ms.

### Phase 4: NER model optimization (optional, ~1 day)

- Evaluate fine-tuned Bio_ClinicalBERT vs the default `d4data/biomedical-ner-all`
- Benchmark precision/recall on a labeled clinical note subset
- Make model swappable via config

## NER model choice

### v1: Pre-trained (`d4data/biomedical-ner-all`)

- **F1**: ~80-85% out of box
- **Latency**: ~150ms/note on CPU
- **Size**: ~440 MB
- **Setup**: zero training required
- **Entity types**: disease, chemical/drug, gene, protein (multi-type)

### v2: Fine-tuned (future)

- **F1**: ~90-93% with labeled data
- **Requirement**: labeled training data
- **Selection**: when v1 precision is insufficient

Model is swappable via `ExtractionService(ner_model="org/model-name")`.

## Deployment considerations

| Resource | v1 requirement |
|---|---|
| Additional disk | ~560 MB (medspaCy + NER model) |
| Additional RAM | ~1 GB (medspaCy pipeline + NER model in memory) |
| CPU | Fine for batch (1-3 notes/sec) |
| GPU | Not required |
| Cold start | ~10-15s (load medspaCy + NER model + SapBERT) |
| Warm latency | ~250ms/note (medspaCy ~30ms + NER ~150ms + search ~100ms) |

## Relationship to fhir4ds Phase 4

fhir4ds's Phase 4 (Clinical Notes NER Extension) becomes a thin consumer:

```python
# In fhir4ds:
import medterm4ds

concepts = medterm4ds.extract_concepts(
    note_text,
    categories=["condition", "medication"],
    mode="hybrid",
    min_grade="certain",
)

for concept in concepts:
    if concept.status == "affirmed":
        condition = Condition(
            code=CodeableConcept(coding=[Coding(
                system=concept.system_uri,
                code=concept.code,
                display=concept.display,
                userSelected=False,
            )]),
            subject=source_subject,
            evidence=[Evidence(detail=[Reference(f"{source_type}/{source_id}")])],
            verificationStatus="unconfirmed",
        )
```

fhir4ds handles: FHIR resource construction, extension attachment, CQL
integration, note path extraction. medterm4ds handles: NER, ConText, search,
result typing.

## Time estimate

| Phase | Effort | Deliverable |
|---|---|---|
| 1: Core service | 1 day | ExtractionService + ExtractedConcept + tests |
| 2: Wire into 4 surfaces | half day | Python/CLI/MCP/FHIR all working |
| 3: Caching | half day | Batch with text-hash cache |
| 4: NER optimization | 1 day (optional) | Model evaluation + tuning |
| **Total** | **~2-3 days** | Production-ready extraction service |
