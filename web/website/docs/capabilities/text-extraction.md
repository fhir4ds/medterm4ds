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
# → 'T2DM' type=disease status=affirmed
# → 'metformin' type=medication status=affirmed
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
| `"annotated"` | dict: `concepts` + `annotated_text` + `spans` | medspaCy + NER + SapBERT | ~250ms |

Same parameter across all surfaces:

```bash
# CLI
medterm4ds extract "Patient has diabetes" --format codes
medterm4ds extract "Patient has diabetes" --format terms
medterm4ds extract "Patient has diabetes" --format annotated

# MCP
extract(text="Patient has diabetes", format="codes")

# FHIR
POST /fhir/CodeSystem/$extract?text=...&format=codes
```

## Batch extraction

`extract()` accepts a single text or a **list of texts** — one result per
text, input order, any format. Batch inputs are processed under a single
lock acquisition with both heavy stages pooled cross-text: GLiNER runs one
batched inference over all sentences (`MEDTERM4DS_EXTRACT_BATCH_SIZE`,
default 32) and canonical resolution deduplicates entity texts across the
whole batch before embedding (`MEDTERM4DS_EMBED_BATCH_SIZE`, default 64).
Measured: ~34x vs per-text calls on repeat-heavy drug-label corpora.

```python
results = mt.extract([note1, note2, note3], format="codes")
# results[0] has exactly the shape extract(note1, format="codes") returns
```

Batched inference shifts span scores at the last float digits (same drift
class as GPU-vs-CPU) — campaign runs should pick one mode and stay in it.
See [Engine Configuration](../getting-started/engine-configuration.md) for
the batch-size knobs.

## Annotated output & marker fields

`format="annotated"` returns the concepts **plus** inline-marked text and
full span metadata (`match_grade`, `source`, `code`, `canonical_id`,
`status`, `ner_score`, offsets). The `annotation_fields` option selects
and orders the inline marker fields — `text`, `name`, `type`,
`source_code` (`SOURCE:code`), `canonical_id`, `status`. Unresolved
fields render as `UNKNOWN` so field positions stay stable:

```python
mt.extract("Patient takes metformin 500 mg daily.", format="annotated",
           annotation_fields=["source_code", "text"])
# annotated_text: "Patient takes [RXNORM:6809|metformin] [UNKNOWN|500 mg] daily."
```

Available on every surface (comma-separated on wire surfaces):
`annotation_fields=` (Python/MCP), `annotationFields=`
(FHIR `$extract` GET/POST), `--annotation-fields` (CLI). Default
`text,type` reproduces the historical `[entity|label]` marker.

## GPU acceleration

Set `MEDTERM4DS_DEVICE` (default `auto`) to place GLiNER and SapBERT on
CUDA/MPS when available; explicit unavailable requests raise. Deterministic
pipelines should pin `MEDTERM4DS_DEVICE=cpu`. Details in
[Engine Configuration](../getting-started/engine-configuration.md).

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

Default: `E3-JSI/gliner-multi-med-ner-synthetic-v1` (GLiNER zero-shot, ~250ms/note on CPU).

Switched from `d4data/biomedical-ner-all` (spaCy NER) in mid-2026 after
testing showed d4data missed acronyms like "T2DM" and short attestations
like "CKD" entirely. GLiNER is zero-shot — labels are passed at query time,
not baked into the model. Default labels:

- `disease` (mapped to category `condition`)
- `medication` (mapped to category `medication`)
- `symptom` (mapped to category `condition`)
- `procedure` (mapped to category `procedure`)
- `lab test` (mapped to category `lab`)
- `body structure` (mapped to category `body_structure`)

Override labels via `ExtractionService(labels=[...])`. Model is swappable
via environment variable:
```bash
export MEDTERM4DS_NER_MODEL="your-org/your-model"
```

A false-positive blocklist filters common non-medical words ("Patient",
"male", "female", "year", "old") that GLiNER may emit at threshold 0.3.
Inline negation triggers (e.g., "No evidence of CKD" extracted as one span)
are detected via regex and the trigger stripped before code resolution.

## Input length cap

`$extract` input text is capped at `MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS`
(default 100000 chars — ~50 pages of clinical text). POST bodies larger
than that return 400 with a clear OperationOutcome. Override via env var
if you have a pipeline sending larger documents:

```bash
export MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS=200000
```

## Dependencies

```bash
pip install medterm4ds[extraction]  # adds gliner + medspacy + transformers
```

CPU is sufficient — ~250ms/note (medspaCy ~30ms + GLiNER ~120ms + search
~100ms); optional GPU acceleration via
[Engine Configuration](../getting-started/engine-configuration.md).
