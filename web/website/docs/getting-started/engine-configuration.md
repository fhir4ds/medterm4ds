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

## Artifact cache and staying current

Search artifacts (canonical anchors, SapBERT + FAISS indexes, lexical
indexes) are downloaded lazily from the Hugging Face repo
(`fhir4ds/medterm4ds`) on first use. Two layouts are accepted:

**Split layout (current, preferred)** — `models/<embedding_space_id>/`
(SapBERT weights, content-addressed and pinned by an in-code acceptance
registry) and `data/<data_revision>/` (canonical value sets + concept
FAISS index, floats to the latest published revision; the resolved
revision is logged and surfaced by `cache-info`). Every unit ships a
`manifest.json`; the runtime validates embedding-space identity and md5
lineage at component load and hard-refuses on mismatch (a stale index
warns and serves during a deprecation window).

**Legacy layout (tag `v0.0.5`)** — flat
`~/.cache/medterm4ds/<revision>/{canonical,semantic,lexical}/`. Still
served when present, with a deprecation notice pointing at
`cache-refresh --split`.

- `MEDTERM4DS_LAYOUT=legacy` forces the legacy layout (kill-switch /
  instant rollback); default `auto` prefers split.
- `MEDTERM4DS_DATA_REVISION` pins a specific data revision (e.g.
  `cdb_2026_09_07`) instead of floating to latest.
- `MEDTERM4DS_SEMANTIC_INDEX_DIR` overrides the per-category FAISS index
  source. By default the split model reuses the legacy `semantic/`
  indexes when weights + tokenizer are byte-identical (md5 bridge);
  otherwise per-category semantic search degrades to empty while canonical
  concept search keeps working.
- `MEDTERM4DS_HF_REVISION` still selects the legacy-layout tag (default
  `v0.0.5`); the data family floats from `main` regardless.
- Setting `MEDTERM4DS_CACHE_DIR` switches to an operator-managed cache
  root: used as-is (the `deploy.sh`/data-dir contract), no revision
  keying, `cache-refresh` refuses rather than delete.
- `MEDTERM4DS_NER_ALLOW_UNCALIBRATED=1` loads a GLiNER config whose
  calibration id is not in the acceptance registry (warns with both ids).
- Commands: `medterm4ds data cache-info` (what is cached, resolved
  revisions, manifests, provenance), `cache-refresh [--revision R]`
  (legacy force re-download), `cache-refresh --split [--data-revision R]`
  (download/migrate to the split layout; legacy dirs can be deleted
  afterward), and `cache-list` (tags/branches available in the repo).

Background downloads are never automatic unless a component is first used
and nothing is cached; campaigns stay reproducible by pinning
`MEDTERM4DS_DATA_REVISION` (and/or the layout kill-switch).

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

Passing a list of texts to `extract()` pools both heavy stages across the
whole batch: GLiNER inference over all sentences
(`MEDTERM4DS_EXTRACT_BATCH_SIZE`, default 32 — measured 3.7x faster than
per-text calls on GPU for the NLP stage) and canonical resolution over the
deduplicated entity texts of every input (`MEDTERM4DS_EMBED_BATCH_SIZE`,
default 64). Corpora that repeat entity texts across documents (drug
labels, guidelines) collapse the embed workload substantially.

Batched inference changes span scores at the last float digits (padded
batches, same class of drift as GPU-vs-CPU), so a span exactly on the
detection threshold can resolve differently between single and batch
modes. Campaign runs should pick one mode and stay in it.

See the [Text Extraction capability](../capabilities/text-extraction.md)
for the full batch API, annotated output, and marker-field options.
