---
title: Text-to-Code Search
---

# Text-to-Code Search

Find medical codes from free text using BM25 (lexical) and fine-tuned SapBERT embeddings (semantic).

This is medterm4ds's key differentiator. Traditional terminology servers require you to already know the code. medterm4ds lets you search by natural language and get ranked code candidates with confidence scores.

## Quick example

```python
import medterm4ds as mt

# Lexical: BM25 token matching (~1ms)
results = mt.search("diabetes", mode="lexical", count=5)
for r in results:
    print(f"  {r.score:.2f} {r.match_grade:8s} {r.source:15s} {r.code:12s} {r.display}")
# → 0.87 certain  SNOMEDCT_US  73211009  Diabetes

# Semantic: catches novel phrasings where BM25 fails
results = mt.search("high blood sugar", mode="semantic")
# → 0.80 probable  ICD10CM  R73  Elevated Blood Glucose Level

# Hybrid: best of both worlds
results = mt.search("metformin pill", mode="hybrid")
# → 1.00 certain  RXNORM  1161611  Metformin Pill
```

## Three search modes

| Mode | Engine | Latency | Best for |
|---|---|---|---|
| **`lexical`** (default) | BM25 inverted index | ~1ms | Known medical terms ("diabetes", "metformin") |
| **`semantic`** | SapBERT embeddings + FAISS | ~100ms | Novel phrasings ("high blood sugar" → Hyperglycemia) |
| **`hybrid`** | BM25 retrieve → SapBERT re-rank | ~110ms | Best accuracy (default for extraction) |

### Why three modes?

**Lexical** (BM25) is fast and deterministic — it matches tokens. But it fails on:
- Acronyms: "T2DM" → no BM25 match for "Type 2 Diabetes Mellitus"
- Synonyms: "high blood sugar" → no token overlap with "Hyperglycemia"
- Paraphrasing: "water pill" → no match for "Diuretic"

**Semantic** (SapBERT) catches these by embedding the query and finding nearby medical concepts in vector space. But it's 100x slower per query.

**Hybrid** runs BM25 first (retrieve 50 candidates), then SapBERT re-ranks them. Gets the speed of BM25 with the semantic understanding of SapBERT.

## Match grades

Each result has a confidence grade (modeled after FHIR Patient `$match`):

| Grade | Score range | Meaning |
|---|---|---|
| `certain` | ≥ 0.8 | High confidence match |
| `probable` | ≥ 0.4 | Likely match, review recommended |
| `possible` | < 0.4 | Possible match, needs human review |

## Search indexes

The search service lazy-loads pre-built indexes on first use:

| Index | Size | Source |
|---|---|---|
| BM25 JSONs (6 categories) | 192 MB | HF dataset `joelmontavon/medterm4ds-data` |
| SapBERT model | 438 MB | Same dataset |
| FAISS indexes (6 categories) | 2.0 GB | Same dataset |

No GPU required. All search runs on CPU.

## Filtering by source

```python
# Search only medications
results = mt.search("insulin", sources=["RXNORM"])

# Search conditions + medications
results = mt.search("diabetes medication", sources=["SNOMEDCT_US", "RXNORM"])
```

## Available across all interfaces

```bash
# CLI
medterm4ds search "diabetes" --mode hybrid

# MCP
search(query="diabetes", mode="hybrid", count=10)

# FHIR API
GET /fhir/CodeSystem/$search?query=diabetes&searchMode=hybrid
```
