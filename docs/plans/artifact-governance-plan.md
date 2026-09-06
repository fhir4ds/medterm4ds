# Artifact Governance Plan — shape-bound model/data contracts

Status: REVIEWED — model team APPROVED (2026-09-05) with corrections
applied; fhir4ds consulted on consumer constraints (same date). Awaiting
owner sign-off before implementation.
Authors: medterm4ds session, with input from the model team (canonical) and
the fhir4ds team (both consulted 2026-09-05, reports on the session bus)
Target repos: `medterm4ds` (runtime), `medterm4ds-canonical` (build side)

## 1. Context

medterm4ds consumes three artifact families from the HF repo
`fhir4ds/medterm4ds`, cached revision-keyed under
`~/.cache/medterm4ds/<revision>/{semantic,lexical,canonical}`:

| Family | Contents | Depends on |
|---|---|---|
| `semantic/` | fine-tuned SapBERT (438MB, 768-dim) + per-category FAISS indexes (~2.4GB) | model **and** canonical text |
| `lexical/` | BM25 inverted-index JSONs | canonical build only |
| `canonical/` | anchor value sets (47K+) + FAISS concept index + metadata | value sets: build only; concept index: model **and** text |

Plus GLiNER (`knowledgator/gliner-bi-small-v2.0` @ `3d74c1b`) in the
extraction service, commit-pinned because its 9-label set + 0.15 threshold
are calibrated against that checkpoint.

**Problem:** everything is version-bound as one unit. A BM25/canonical data
wave forces a full revision bump even when the model is unchanged; nothing
can float to `latest` safely; and downstream (fhir4ds/fhir4px) coupling is
opaque.

**Goal:** data updates float automatically; model updates are rare,
deliberate, loudly signaled — keyed to *compatibility* (embedding space),
not artifact version.

## 2. The three axes (shared vocabulary)

| Axis | Governs | When it changes |
|---|---|---|
| **Embedding space** | vector *compatibility* — which FAISS indexes a model can serve | SapBERT retrain, or any text-render policy change |
| **Canonical build** | *content/freshness* of anchors and data artifacts | every canonical wave (daily–weekly) |
| **Index lineage** | auditability — what an index was built from | every index rebuild |

Critical consequence (model team finding, 2026-09-04 rename wave): the
text fed to the encoder is part of the space. Route-qualified name policy
('Acyclovir' → 'Acyclovir (Ophthalmic)', ~9.6K renames) changes every
vector without any model change. Therefore:

- Render **policy** (which fields, formatting, synonym inclusion,
  route qualification) → belongs in the space fingerprint.
- Render **input** (current canonical names) → belongs to the build axis;
  a rename wave forces an index rebuild but does NOT change the space id.

Rename waves rebuild indexes ~daily–weekly; retrains happen quarterly or
less. The layout must not re-ship 438MB of unchanged weights on every
wave.

## 3. Manifest schema

Every artifact directory ships `manifest.json`:

```jsonc
// models/<embedding_space_id>/manifest.json  (weights, tokenizer, config)
{
  "schema_version": 1,                  // manifest format itself
  "artifact_kind": "model",
  "embedding_space_id": "esp_9f3a...",  // content-addressed dir name
  "model_name": "sapbert_finetuned",
  "model_rev": "<weights md5 short>",   // + tokenizer md5
  "space_fingerprint": {
    "weights_md5": "...",
    "tokenizer_md5": "...",
    "pooling": "mean",
    "l2_normalize": true,
    "max_seq_length": 512,
    // COMPUTED, never hand-set (model-team correction): hash of the render
    // function source + field-selection config. Name policy is canonical-
    // owned (CONSUMER_CONTRACTS); canonical emits it in canonical_build.json
    // alongside the value_sets md5. medterm4ds validates presence + match;
    // it never computes it. A declared string drifts from reality exactly
    // the way md5-by-memory did.
    "render_policy_version": "rp_<hash of render fn + config>"
  },
  "published_at": "2026-09-05T12:00:00Z"
}

// data/<data_revision>/manifest.json    (BM25, anchor value sets)
{
  "schema_version": 1,
  "artifact_kind": "data",
  "data_revision": "cdb_2026_09_05_1",  // = canonical_build id
  "embedding_space_id": "esp_9f3a...",  // the space the pipeline used
  "render_policy_version": "rp_hash...", // REQUIRED (model-team correction 2):
                                         // a data dir is only servable by a
                                         // model whose space fingerprint
                                         // carries the SAME render policy —
                                         // without this, a policy change
                                         // looks like a pure model change
                                         // and stale-data dirs pass
  "published_at": "..."
}

// any FAISS-bearing directory additionally:
{
  "index_lineage": {
    "embedding_space_id": "esp_9f3a...",   // MUST match serving model
    "built_against_canonical_build": "cdb_2026_09_05_1",
    "index_md5": "...", "metadata_md5": "..."  // dual-edge pair (CONSUMER_CONTRACTS)
  }
}

// extraction NER manifest (extraction_ner/manifest.json)
{
  "schema_version": 1,
  "artifact_kind": "ner",
  "model_rev": "3d74c1bf459b8b1c0be1ecbddd679416ce005418",  // commit pin stays
  "calibration_id": "cal_a17b..."   // hash of labels + threshold
}
```

`embedding_space_id` = hash of the full `space_fingerprint`. The
fingerprint computation lives in ONE place — `medterm4ds` (runtime) —
and `medterm4ds-canonical` imports it at build time (it already depends
on the medterm4ds package per BUILD.md prerequisites). No duplicate
implementations.

## 4. Cache layout

```
~/.cache/medterm4ds/
  models/<embedding_space_id>/        # content-addressed; never mutated
  data/<data_revision>/               # floats via 'latest' resolution
  extraction_ner/<calibration-scoped>/
```

- Model dirs are **content-addressed and immutable**: a retrain publishes
  a NEW dir; rollback = point back. No 438MB re-ship on data waves.
- Data dir resolves `latest` at load; the resolved revision is logged
  (fhir4ds: traceability) and pinned in the provenance marker.
- Downloads are atomic per **load unit** (model-team correction 4): each
  directory downloads via tmp + rename, and an FAISS index + its metadata
  download as ONE unit (same tmp dir, renamed together) — the dual-edge
  same-build rule breaks if index and metadata can ever be observed mixed.
  (fhir4ds requirement: no mixed model/FAISS half-states either.)
- Operator-managed `MEDTERM4DS_CACHE_DIR` keeps working: the dir is used
  as-is but its manifests are still validated.

### HF repo expression

Keep one repo. `main` carries the floating `data/` family (data manifest
declares which `embedding_space_id` the pipeline used). Model weights are
published under `models/<space_id>/` paths; the runtime downloads that dir
once per space. Version branches/tags remain for auditable snapshots.

## 5. Runtime gating (medterm4ds)

Placement and granularity (both fhir4ds requirements):

1. **Validation runs inside `connect()` / engine init and on lazy
   component load — never at import time.** fhir4ds's INV-1 zero-dep
   `import fhir4ds` must hold.
2. **Per-component gates.** Loading semantic search hard-fails on
   manifest violations; lexical-only consumers (e.g. fhir4ds DQM batch
   jobs) are never blocked by a semantic/data cache issue. BM25 load
   checks only its own data manifest.

Hard-fail conditions on the semantic path (model team's dual-edge rule,
CONSUMER_CONTRACTS 0a794cf):

- index `embedding_space_id` ≠ serving model `embedding_space_id`
- `index_md5`/`metadata_md5` pair mismatch (index and its metadata must
  be same-build)
- data manifest's declared `embedding_space_id` ≠ serving model space

Decision point RESOLVED (model team, plan review): stale index → **WARN
and serve, with a deprecation window keyed to the INDEX'S build, not
wall-clock** — "warn until the next index published after the data
revision that triggered it" (deterministic, no timing races across
consumers). Rationale: rename waves land intra-day; a hard refuse would
brick semantic search mid-fold for consumers who float data faster than
indexes rebuild. The window converts a correctness cliff into a
freshness lag — the right failure mode. The WARN must carry BOTH
revisions (index lineage vs data) so drift is visible in logs. A
rebuilt-but-stale index is NOT servable: the window serves the stale
index itself. Hard-fail only on the space/dual-edge conditions above.

GLiNER: refuse when `calibration_id` ≠ accepted unless
`MEDTERM4DS_NER_ALLOW_UNCALIBRATED=1`. The override LOADS the
uncalibrated model but LOGS the accepted-vs-served calibration ids on
every load (model-team note: never refuse silently, never override
silently) — the commit pin stays as `model_rev`.

### Accepted-space declaration

`medterm4ds` ships an acceptance list (embedding spaces + manifest
`schema_version`s + NER calibration ids) **in code** (RESOLVED, both
teams): a py constant, so every bump is a reviewed diff rather than a
data edit. This is the mechanism that makes `MEDTERM4DS_HF_REVISION=latest`
safe.

## 6. Work split

**Model team (`medterm4ds-canonical`)** — the build side:
1. Emit `manifest.json` for every artifact at build time (space
   fingerprint via the medterm4ds-provided helper, canonical_build id,
   dual-edge md5s). Manifests are **script-emitted with content-addressed
   md5s computed from the artifacts** — no transcribed values (the
   publish-report lesson, now standing discipline).
2. Publishing discipline (MAINTENANCE.md): data family → `main` (floats);
   model family → `models/<space_id>/` only on retrain/render-policy
   change; extraction_ner manifest on recalibration. SapBERT retraining
   itself is medterm4ds-side work (correction 1: fine-tuning lives with
   medterm4ds/canonical, NOT fhir4px-model) — canonical owns the PUBLISH
   of the new space dir, not the training.
3. Treat big rename waves as index-rebuild events (they already are —
   make the rebuild + republish explicit in the wave runbook;
   formalizing the informal 2026-09-04 practice).
4. Own the render-policy hash emission (render function source +
   field-selection config) in `canonical_build.json`.

**medterm4ds team (this repo)** — the runtime side:
1. `core/artifact_manifest.py`: fingerprint computation, manifest
   read/validate, accepted-space registry (single source of truth; the
   canonical repo imports this).
2. SearchService/SemanticSearchEngine: split cache layout, per-component
   lazy gates, atomic `_hf_download`, `latest` resolution with logged
   resolved revisions.
3. `extraction.py`: calibration manifest + override env.
4. CLI: `data cache-info` gains embedding_space_id, resolved data
   revision, manifest status (fhir4ds incident-correlation ask);
   `data cache-refresh` migrates the old `<revision>/` layout. Keep
   `data build-duckdb` / `prepare-derived` verbs stable (they appear in
   fhir4ds user-facing remediation text).
5. `FHIR4DS_TERMINOLOGY_SEARCH_INDEX_DIR` passthrough: keep it a single
   dir containing both index types + a manifest we validate.
6. `scripts/build_fhir4px_*.py` exports: `canonical_codes.csv` floats
   with data revisions; `embedding_index_*.jsonl` regenerates only on
   space change; frozen snapshots become fhir4px's pin to make.

**Joint:** manifest schema sign-off (this doc §3), rollout coordination.

## 7. Migration & rollout

1. **Phase 0** — schema agreement (this review).
2. **Phase 1** — medterm4ds: manifest module + fingerprint tooling +
   tests. No behavior change.
3. **Phase 2** — canonical: build-side emission; dual-publish one
   revision in BOTH old (`<revision>/` flat) and new (`models/` + `data/`)
   layouts.
4. **Phase 3** — medterm4ds runtime: new loader. Accepts both layouts for
   one release (old layout = current behavior + deprecation log).
   `cache-refresh` migrates.
5. **Phase 4** — flip `MEDTERM4DS_HF_REVISION=latest` default for the
   data family only; model stays pinned by acceptance list. Update
   MAINTENANCE.md (canonical) + README/docs (medterm4ds).

Rollback at every phase = previous HF tag; content-addressed model dirs
make model rollback instant.

## 8. What this does NOT solve (explicit non-goals)

- **API drift.** fhir4ds's `medterm4ds>=0.0.3,<0.0.4` bound exists because
  of behavioral changes (0.0.2 active-only flip, `$search` param moves).
  Manifest gating governs artifacts only; version pins for API stay.
- UMLS DuckDB versioning (separate contract, `umls_current.duckdb`).
- fhir4px's in-browser model (`fhir4px-embeddings-onnx`) — independent
  stack; only affected via the frozen exports they choose to pin.

## 9. Resolved decisions (from team review)

1. Stale-index handling (§5): WARN-and-serve window keyed to the index's
   build (see §5 for the full rule).
2. Acceptance list lives in code.
3. `MEDTERM4DS_NER_ALLOW_UNCALIBRATED`: loads but logs
   accepted-vs-served calibration ids every load.
4. Phase timing: Phase 2 (canonical dual-publish) rides the next
   canonical wave whenever medterm4ds starts Phase 1 — no coupling to the
   v0.0.6 tag. Flag it in the wave choreography when Phase 1 begins.
