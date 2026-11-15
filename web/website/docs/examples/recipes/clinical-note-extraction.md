---
title: Clinical Note Extraction
---

# Recipe: Clinical Note Extraction

Extract coded medical concepts from clinical note text.

## Problem

You have clinical notes as free text:
```
"65yo M with T2DM on metformin, lisinopril, atorvastatin. No evidence of CKD or retinopathy."
```

You need structured coded concepts — with negation filtered out.

## Solution

```python
import medterm4ds as mt

note = "65yo M with T2DM on metformin, lisinopril, atorvastatin. No evidence of CKD or retinopathy."

# Full pipeline: text → coded concepts
concepts = mt.extract(note, format="codes", result_types=["condition", "medication"])

for c in concepts:
    print(f"  {c.source:15s} {c.code:12s} {c.display:35s} matched='{c.matched_text}'")
```

Output (medterm4ds 0.0.13, release 2026.11.14.2):
```
  SNOMEDCT_US  44054006     Type 2 Diabetes                      matched='T2DM'
  RXNORM       6809         Metformin                            matched='metformin'
  RXNORM       29046        Lisinopril                           matched='lisinopril'
  RXNORM       83367        Atorvastatin                         matched='atorvastatin'
  # CKD and retinopathy NOT returned — negated by ConText
```

## NLP only (no code resolution)

If you just want to know what medical terms are in the text (for highlighting, indexing, or manual review):

```python
spans = mt.extract(note, format="terms")

for s in spans:
    print(f"  '{s.text}' type={s.entity_type} status={s.status}")
# → 'T2DM' type=disorder status=affirmed
# → 'metformin' type=therapeutic agent status=affirmed
# → 'CKD' type=disorder status=negated (excluded by default)
```

## Including negated mentions

For problem-list extraction where you want everything including negations:

```python
all_concepts = mt.extract(note, format="terms", include_negated=True)
for s in all_concepts:
    print(f"  '{s.text}' type={s.entity_type} status={s.status}")
# → 'T2DM' type=disorder status=affirmed
# → 'CKD' type=disorder status=negated
# → 'retinopathy' type=disorder status=negated
```

## CLI

```bash
medterm4ds extract "65yo M with T2DM on metformin" --format codes
medterm4ds extract "65yo M with T2DM on metformin" --format terms
```

## Performance

| Step | Latency (CPU) |
|---|---|
| medspaCy pipeline | ~30ms |
| NER model | ~150ms |
| SapBERT search (per span) | ~70ms warm (v0.0.5) |
| **Total (typical note)** | **~250ms** |

No GPU required.
