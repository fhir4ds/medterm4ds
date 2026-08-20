---
title: Engine Configuration
---

The local DuckDB engine is the default for workstation, notebook, and bulk terminology workflows.

Use it when:

- terminology data should stay local
- users have a built UMLS DuckDB database
- workflows need lookup, mapping, hierarchy, optimize, or bulk export at scale
- memory needs to be bounded for commodity machines

Notebook example:

```python
import medterm4ds as mt

terms = mt.connect(
    "/mnt/d/medterm4ds/data/umls_current.duckdb",
    memory_profile="low",
)

terms.lookup("ICD10CM", "E11.9")
```

CLI example:

```bash
medterm4ds lookup \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --memory-profile low \
  --source ICD10CM \
  --code E11.9
```

The implementation class is `LocalDuckDBEngine`. The old `LocalLiteEngine` name remains as a compatibility alias for early adopters.

## GPU acceleration (extraction and semantic search)

Text extraction (GLiNER) and semantic search (SapBERT) run their transformer
inference on a GPU when one is available. Device selection is controlled by
`MEDTERM4DS_DEVICE`:

| Value | Meaning |
|---|---|
| `auto` (default) | CUDA when available, else MPS, else CPU |
| `cpu` | Force CPU |
| `cuda`, `cuda:1`, ... | Force a specific GPU. Raises at model load if unavailable — an explicit GPU request never silently falls back to CPU |
| `mps` | Apple Silicon GPU. Raises if unavailable |

Auto-detection means fresh installs get the speedup with zero configuration.
FAISS index search stays on CPU — single-query ANN against these index sizes
is sub-millisecond, so the heavyweight GPU FAISS build brings no gain.

Two operational notes:

- Deterministic pipelines (tests, golden comparisons) should pin
  `MEDTERM4DS_DEVICE=cpu`: GPU float noise can flip spans sitting exactly on
  the extraction threshold.
- CUDA contexts cannot survive `fork()`. Worker-pool consumers of
  `extract()` should load the model lazily inside each worker, not in the
  parent process before forking.

## Batched extraction

Passing a list of texts to `extract()` pools all sentences into batched
GLiNER inference (`MEDTERM4DS_EXTRACT_BATCH_SIZE`, default 32) — measured
3.7x faster than per-text calls on GPU for the NLP stage. Batched inference
changes span scores at the last float digits (padded batches, same class of
drift as GPU-vs-CPU), so a span exactly on the detection threshold can
resolve differently between single and batch modes. Campaign runs should
pick one mode and stay in it.
