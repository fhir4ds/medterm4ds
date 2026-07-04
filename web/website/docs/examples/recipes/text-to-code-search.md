---
title: Text-to-Code Search
---

# Recipe: Text-to-Code Search

Find medical codes from natural language using lexical, semantic, and hybrid search.

## Problem

A patient types "high blood sugar" in a symptom checker. You need to find the corresponding SNOMED CT or ICD-10 code. Traditional terminology servers can't help — they require the exact code as input.

## Solution

```python
import medterm4ds as mt

# Lexical: fast, deterministic
results = mt.search("high blood sugar", mode="lexical")
# → (may return nothing — no token overlap with "Hyperglycemia")

# Semantic: catches the meaning even when words don't match
results = mt.search("high blood sugar", mode="semantic", count=5)
for r in results:
    print(f"  {r.score:.2f} {r.match_grade:8s} {r.code:12s} {r.display}")
# → 0.80 probable  R73          Elevated Blood Glucose Level
# → 0.80 probable  80394007     Hyperglycemia
# → 0.79 probable  R73.9        Hyperglycemia

# Hybrid: best accuracy (BM25 + SapBERT re-rank)
results = mt.search("chest pain when lying down", mode="hybrid")
```

## Real-world examples

| Query | Mode | Top result | Score |
|---|---|---|---|
| "diabetes" | lexical | SNOMED 73211009 (Diabetes) | 0.87 |
| "high blood sugar" | semantic | ICD-10 R73 (Elevated Blood Glucose) | 0.80 |
| "metformin pill" | hybrid | RxNorm 1161611 (Metformin Pill) | 1.00 |
| "water pill" | semantic | RxNorm 310798 (Hydrochlorothiazide) | 0.75 |
| "T2DM" | semantic | SNOMED 44054006 (Type 2 diabetes) | 0.89 |

## CLI

```bash
medterm4ds search "high blood sugar" --mode semantic --limit 5
```

## FHIR API

```bash
curl "http://localhost:7860/fhir/CodeSystem/\$search?query=high+blood+sugar&searchMode=semantic&count=5"
```

## Tips

- **Default to `lexical`** for interactive UIs (autocomplete, search boxes). It's ~1ms.
- **Use `semantic`** when the user might use non-standard terminology (patient-facing apps).
- **Use `hybrid`** for batch processing where accuracy matters more than latency.
- **Filter by `match_grade="certain"`** for automated pipelines where false positives are costly.
