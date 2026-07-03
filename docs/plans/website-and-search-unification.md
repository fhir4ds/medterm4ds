# Website Rework + Search Unification Plan

## Problem

The current website and docs were written during the terminology normalization
refactor. They lead with the old migration story and don't reflect what
medterm4ds actually does today: a powerful medical terminology engine with
hierarchy traversal, cross-system mapping, patient-friendly naming, code
resolution, AND intelligent text-to-code search (BM25 + fine-tuned SapBERT).

The BM25 + SapBERT search is currently only available via the FHIR API.
Python, CLI, and MCP surfaces get the weak `search_names()` (substring LIKE).
This is an architecture problem — search should be a service that all surfaces
can call.

## Search unification

### Current state

| Surface | Text search | How |
|---|---|---|
| Python (`Terminology` facade) | `search_names()` | LIKE on mrconso — no ranking, no synonyms |
| CLI | Not exposed | — |
| MCP | Not exposed | — |
| FHIR API | `$search` (BM25 + SapBERT) | Full lexical + semantic + hybrid |

### Target state

Extract search into `services/search.py` that all surfaces delegate to:

```
services/search.py
  ├── SearchEngine class (lazy-loads BM25 + SapBERT)
  ├── lexical_search(query, categories, count) → list[SearchResult]
  ├── semantic_search(query, categories, count) → list[SearchResult]
  └── hybrid_search(query, categories, count) → list[SearchResult]

Surfaces:
  Python:  mt.search("diabetes", mode="hybrid")
  CLI:     medterm4ds search "diabetes" --mode hybrid
  MCP:     search tool
  FHIR:    $search (delegates to service)
```

### GPU requirement

No GPU needed. SapBERT runs on CPU via torch. FAISS uses faiss-cpu.
- Lexical: ~1ms (no model inference)
- Semantic on CPU: ~100ms (fine for interactive use)
- Semantic on GPU: ~10ms (only matters for batch processing)

## Website rework

### Core message

"Medical terminology data for data science, built from UMLS"

### Three pillars

1. **Terminology operations** — hierarchy walking, cross-system mapping,
   code resolution, patient-friendly names. This is what the engine does
   against the DuckDB at query time.

2. **Intelligent search** — text-to-code inference with BM25 (lexical,
   ~1ms) and fine-tuned SapBERT embeddings (semantic, ~100ms). The
   differentiator vs. traditional terminology servers. Catches novel
   phrasings like "high blood sugar" → Hyperglycemia.

3. **Multiple interfaces** — Python library, CLI, MCP server, FHIR R4
   terminology server, Docker/HF Spaces. All backed by the same engine.

### Proposed website structure

```
Home
  "Medical terminology for data science, powered by UMLS"
  Featured: text-to-code search demo + FHIR server quick start

Getting Started
  ├── Installation
  ├── First notebook (the demo notebook)
  └── Architecture (mermaid diagram with module split)

Capabilities (NEW section — the "what you can do")
  ├── Code lookup & patient-friendly names
  ├── Hierarchy traversal (parents/children/ancestors/descendants)
  ├── Cross-system mapping (SNOMED ↔ ICD-10 ↔ RxNorm)
  ├── Code resolution (active/historical/obsolete/NDC)
  └── Text-to-code search ← featured
      ├── Lexical (BM25)
      ├── Semantic (SapBERT + FAISS)
      └── Hybrid (cascade)

Interfaces (restructured)
  ├── Python library (Terminology facade + DataFrame helpers)
  ├── CLI (commands + bulk exports)
  ├── MCP server (37 tools)
  ├── FHIR R4 terminology server (7 operations + $search)
  └── Docker / HF Spaces deployment

Reference
  ├── Supported sources (table with counts)
  ├── Service functions (API reference)
  ├── Models (CodeInfo, CodeMapping, etc.)
  └── FHIR operations reference

Deployment
  ├── Local dev setup
  ├── Docker (UMLS API key → builds lookup.duckdb)
  └── HF Spaces

Terminology (existing technical docs, moved here)
  ├── NDC → RxNorm resolution
  ├── Obsolete codes
  ├── Source inventory
  ├── UMLS release info
  └── Licensing
```

### Key pages to rewrite

1. **Home page** — new hero message, search demo, "get started in 3 commands"
2. **Capabilities overview** — NEW page showing the 5 core capabilities
3. **Search page** — NEW page dedicated to lexical/semantic/hybrid search
4. **Interfaces overview** — restructured to show 4 unified surfaces
5. **Architecture page** — already updated (Tier C split), just needs the
   search service added to the diagram

### Key pages to keep (with minor updates)

- Installation, licensing, notebooks (unchanged)
- FHIR server page (already written, just needs to reference the unified
  search service instead of its own BM25/SapBERT loading)
- API reference pages (service functions, models — unchanged)

### Key pages to archive

- Parity matrix, intentional differences, patient-friendly operations,
  release checklist (already marked Historical)
- Terminology architecture requirements (the old migration spec)

## Implementation order

1. Extract search into `services/search.py` (this doc, step 1)
2. Wire into Python facade + CLI + MCP (this doc, step 2)
3. Rework website structure + write new pages (step 3, separate effort)
4. Update all docs to reflect unified search (step 4)
