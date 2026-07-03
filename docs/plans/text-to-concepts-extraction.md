# Text Extraction Service — Implementation Plan

## Context

medterm4ds currently answers "given a **code**, what do we know about it?" The
inverse — "given **free text**, what codes are in it?" — is the missing half.
This plan adds a text extraction service that takes clinical free text and
returns medical concepts — either as text spans (NER + ConText only) or
resolved to codes (NER + ConText + search).

## Architecture: decomposed internals, unified surface

### Step 1: `find_terms(text)` — clinical NLP

Requires: medspaCy + NER model (~560 MB).
Does NOT require: SapBERT, BM25, DuckDB.

Returns: `list[FilteredSpan]` — text spans with NLP metadata, no codes.

### Step 2: `resolve_spans(spans)` — code resolution

Requires: SearchService (BM25 + SapBERT, existing).
Does NOT require: medspaCy, NER model.

Returns: `list[ExtractedConcept]` — spans resolved to codes.

### Convenience: `extract(text, format=...)` — full pipeline

`format="codes"` (default): find_terms + resolve_spans.
`format="terms"`: find_terms only (skips SapBERT).

## Result models

```python
@dataclass
class FilteredSpan:
    text: str              # "T2DM"
    entity_type: str       # "disease" | "drug" | "lab" | "procedure"
    status: str            # "affirmed" | "negated" | "uncertain" | "history_of"
    section: str | None    # "Assessment"
    span_start: int
    span_end: int
    ner_confidence: float

@dataclass
class ExtractedConcept:
    code: str
    source: str            # SNOMEDCT_US, RXNORM, etc.
    display: str
    matched_text: str      # the NER span
    status: str            # affirmed/negated/uncertain/history_of
    section: str | None
    confidence: float      # search score
    match_grade: str       # certain/probable/possible
    category: str
    span_start: int
    span_end: int
```

## Surface API (single function with format parameter)

```python
# Python
mt.extract("text", format="codes")    # default
mt.extract("text", format="terms")    # spans only
mt.find_terms("text")                 # decomposed: NLP only
mt.resolve_spans(spans)               # decomposed: search only

# CLI
medterm4ds extract "text"                         # default: codes
medterm4ds extract "text" --format terms          # spans only

# MCP
extract(text="text", format="codes")              # default
extract(text="text", format="terms")

# FHIR
POST /fhir/CodeSystem/$extract?text=...&format=codes
POST /fhir/CodeSystem/$extract?text=...&format=terms
```

## Phases

1. Core service (`services/extraction.py`) — ~1 day
2. Wire into 4 surfaces — ~half day
3. Caching — ~half day (deferred)
