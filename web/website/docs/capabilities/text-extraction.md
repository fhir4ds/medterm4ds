---
title: Text Extraction
---

# Text Extraction

Extract medical concepts from free clinical text using NER + clinical NLP + code resolution.

Given a clinical note, medterm4ds finds medical entity mentions, filters by clinical context (negation, uncertainty, temporality), and resolves each to a specific code.

## Quick example

```python
import medterm4ds as mt

# Full pipeline: text → codes
concepts = mt.extract(
    "65yo M with T2DM on metformin. No evidence of CKD. Denies chest pain.",
    format="codes",
    categories=["condition", "medication"],
)
for c in concepts:
    print(f"  {c.code:12s} {c.display:35s} matched='{c.matched_text}' status={c.status}")
# → 44054006     Type 2 diabetes mellitus            matched='T2DM'      status=affirmed
# → 860975       Metformin Oral Product              matched='metformin' status=affirmed
# CKD and chest pain EXCLUDED — negated by ConText

# NLP only: text spans without code resolution (no SapBERT needed)
spans = mt.extract("Patient has T2DM on metformin. No CKD.", format="terms")
for s in spans:
    print(f"  '{s.text}' type={s.entity_type} status={s.status}")
# → 'T2DM' type=Disease_disorder status=affirmed
# → 'metformin' type=Medication status=affirmed
```

## Decomposed architecture

The extraction service is split into two independent steps:

### `find_terms(text)` — clinical NLP only

Requires: medspaCy + NER model (~560 MB). Does NOT require SapBERT/BM25.

Returns: `FilteredSpan` objects — text spans with NLP metadata, no codes.

```python
spans = mt.find_terms("Patient has T2DM. No CKD.")
# → [FilteredSpan(text="T2DM", status="affirmed"), ...]
```

### `resolve_spans(spans)` — code resolution only

Requires: SearchService (BM25 + SapBERT). Does NOT require medspaCy.

Returns: `ExtractedConcept` objects — spans resolved to specific codes.

```python
concepts = mt.resolve_spans(spans)
# → [ExtractedConcept(code="44054006", display="Type 2 diabetes mellitus"), ...]
```

### `extract(text, format=...)` — convenience

Calls both steps. The `format` parameter controls depth:
- `"codes"` (default): full pipeline → `ExtractedConcept`
- `"terms"`: NLP only → `FilteredSpan` (skips SapBERT)

## The `format` parameter

| `format` | Returns | Requires | Latency |
|---|---|---|---|
| `"codes"` (default) | `ExtractedConcept` with resolved codes | medspaCy + NER + SapBERT | ~250ms |
| `"terms"` | `FilteredSpan` with text spans only | medspaCy + NER only | ~180ms |

Same parameter across all surfaces:

```bash
# CLI
medterm4ds extract "Patient has diabetes" --format codes
medterm4ds extract "Patient has diabetes" --format terms

# MCP
extract(text="Patient has diabetes", format="codes")

# FHIR
POST /fhir/CodeSystem/$extract?text=...&format=codes
```

## ConText: clinical context filtering

medspaCy ConText analyzes the syntactic context around each entity mention:

| Status | Example | Default action |
|---|---|---|
| `affirmed` | "Patient has diabetes" | **Include** |
| `negated` | "No evidence of diabetes" | **Exclude** |
| `uncertain` | "Possibly pneumonia" | **Exclude** |
| `historical` | "History of MI" | **Exclude** |

Include filtered statuses with parameters:

```python
# Include negated mentions (for problem-list-style extraction)
spans = mt.find_terms(text, include_negated=True)

# Include historical (for past medical history)
spans = mt.find_terms(text, include_historical=True)
```

## NER model

Default: `d4data/biomedical-ner-all` (~80-85% F1, ~150ms/note on CPU).

Entity types: Disease/Disorder, Medication, Chemical, Diagnostic Procedure, Biological Structure.

Model is swappable via environment variable:
```bash
export MEDTERM4DS_NER_MODEL="your-org/your-model"
```

## Dependencies

```bash
pip install medterm4ds[extraction]  # adds medspaCy + transformers
```

No GPU required. ~250ms/note on CPU (medspaCy ~30ms + NER ~150ms + search ~100ms).
