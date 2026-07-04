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
concepts = mt.extract(note, format="codes", categories=["condition", "medication"])

for c in concepts:
    print(f"  {c.source:15s} {c.code:12s} {c.display:35s} matched='{c.matched_text}'")
```

Output:
```
  SNOMEDCT_US  44054006     Type 2 diabetes mellitus            matched='T2DM'
  RXNORM       860975       Metformin Oral Product              matched='metformin'
  RXNORM       197361       Amlodipine Oral Product             matched='lisinopril'
  RXNORM       153165       Atorvastatin Oral Product           matched='atorvastatin'
  # CKD and retinopathy NOT returned — negated by ConText
```

## NLP only (no code resolution)

If you just want to know what medical terms are in the text (for highlighting, indexing, or manual review):

```python
spans = mt.extract(note, format="terms")

for s in spans:
    print(f"  '{s.text}' type={s.entity_type} status={s.status}")
# → 'T2DM' type=Disease_disorder status=affirmed
# → 'metformin' type=Medication status=affirmed
# → 'CKD' type=Disease_disorder status=negated (excluded by default)
```

## Including negated mentions

For problem-list extraction where you want everything including negations:

```python
all_concepts = mt.extract(note, format="terms", include_negated=True)
for s in all_concepts:
    print(f"  '{s.text}' status={s.status}")
# → 'T2DM' status=affirmed
# → 'CKD' status=negated
# → 'retinopathy' status=negated
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
| SapBERT search (per span) | ~100ms |
| **Total (typical note)** | **~250ms** |

No GPU required.
