# Python Library Distribution Plan — `pip install medterm4ds`

## Context

medterm4ds is currently distributed as a Docker container. That works for
server deployments and works for the existing fhir4ds HTTP-sidecar
integration, but it does not work for the **analyst user profile** that
fhir4ds is designed to serve:

- Healthcare analysts working in Jupyter notebooks on macOS or Windows
- Comfortable with `pip install`, not comfortable with Docker Desktop
- Data sizes: 10k–100k patients per project
- Will tolerate a one-time ~10-minute setup; will not tolerate "go start
  the sidecar service" as a recurring operations burden
- Working with PHI, so a managed cloud terminology service is not an
  option

For these users, the only install command they can reliably run is
`pip install`. Today the in-process integration path exists in fhir4ds
(`InProcessTerminologyEndpoint`) but is not the default because it
requires `git clone medterm4ds && pip install -e .` — a developer
workflow, not an analyst workflow.

This plan covers what needs to be true on the medterm4ds side for
fhir4ds to flip its default to in-process, and more broadly for
medterm4ds to become a proper Python library usable by any Python
consumer (not just fhir4ds).

## Decisions (resolved)

These were open questions in earlier drafts. Resolved here so the plan
below can reference them without hedging.

1. **`lookup.duckdb` is built locally, not redistributed.** It contains
   raw UMLS content (concept names, CUIs, AUIs, hierarchy edges, TUIs).
   Any transformation that would make it redistributable (stripping
   names, hashing, etc.) also strips its value. The user obtains their
   own free UMLS license and the build runs from their own RRF download.
   Build time: ~8 min one-time, cached thereafter.

2. **Derived artifacts (BM25, SapBERT, patient_friendly) are published
   openly on Hugging Face.** No gating. The UMLS license explicitly
   allows redistribution of derivative works when (a) the license
   notice is included, (b) no charge, (c) clearly identified as
   UMLS-derived. BM25 indexes and SapBERT model weights are
   unambiguously transformed beyond recognition from the source strings.

3. **No UMLS API key validation service.** medterm4ds does not gate HF
   access behind key validation. NLM handles their own licensing; we
   respect it by building `lookup.duckdb` locally from the user's own
   RRF download, not by becoming a license-enforcement authority.

4. **Provisioning path lands before PyPI publish.** Publishing a package
   that can't do anything out of the box is worse than not publishing.
   `mt.connect()` with auto-provisioning must work before `pip install
   medterm4ds` goes to PyPI.

5. **The existing `Terminology` facade is the `connect()` return type.**
   Users already know `terms.lookup()`, `terms.search()`, etc. No new
   `engine.*` API shape — just auto-provisioning wired into the existing
   facade.

6. **Two-cache model.** `lookup.duckdb` lives in `~/.medterm4ds/cache/`
   (user-scoped, shared across projects). Derived artifacts live in the
   standard HF cache (`~/.cache/huggingface/`) via `snapshot_download()`,
   shared across projects AND with other HF-using tools.

## Architecture: clear ownership boundary

The principle: **medterm4ds owns the data and its distribution; consumers
own their integration.**

| Concern | Owner |
|---|---|
| UMLS API key handling (pass-through to NLM download) | medterm4ds |
| `lookup.duckdb` build, schema, versioning | medterm4ds |
| BM25 / SapBERT / patient_friendly distribution (open HF dataset) | medterm4ds |
| Two-cache model (`~/.medterm4ds/` + HF cache) | medterm4ds |
| Update cadence (new UMLS release → bump) | medterm4ds |
| FHIR R4 client (`HTTPTerminologyEndpoint`) | fhir4ds (consumer-side) |
| CQL integration (closure table, subsumption) | fhir4ds |
| Mode auto-detect (in-process vs HTTP vs disabled) | fhir4ds |
| Notebook UX, examples, error messages | fhir4ds |

fhir4ds's `InProcessTerminologyEndpoint` shrinks to roughly 20 lines:
`import medterm4ds; terms = medterm4ds.connect(); return
terms.search_text(...)`. fhir4ds does not know where data lives, how
it got there, or what license constraints apply.

## Detailed plan

### Phase 1 — Provisioning path (gate for everything else)

**Goal:** `mt.connect()` works end-to-end: detects cache state, builds
`lookup.duckdb` from the user's UMLS RRF if needed, downloads derived
artifacts from HF, returns a working `Terminology` instance.

Tasks:

1. **Wire auto-provisioning into the existing `connect()` function** in
   `client.py`. New signature:
   ```python
   import medterm4ds as mt

   terms = mt.connect(
       umls_api_key=None,       # reads UMLS_API_KEY env var if not passed
       cache_dir=None,          # default: ~/.medterm4ds/
       version="2026AA",        # UMLS release tag
       memory_profile="balanced",
   )
   # Returns: Terminology — same facade users already know
   # terms.lookup("SNOMEDCT_US", "44054006")
   # terms.parents([("SNOMEDCT_US", "44054006")])
   # terms.patient_friendly("SNOMEDCT_US", "44054006")
   # mt.search("diabetes", mode="hybrid")
   # mt.extract("Patient has T2DM.", format="codes")
   ```
   The `Terminology` facade is unchanged — `connect()` just auto-provisions
   the data before constructing the engine.

2. **Cache detection logic inside `connect()`**:
   - Check `~/.medterm4ds/cache/lookup-{version}.duckdb` — if exists,
     open read-only and return engine immediately.
   - If missing: read `UMLS_API_KEY` (env var or `~/.medterm4ds/config.toml`),
     download UMLS RRF from NLM, build `lookup.duckdb` via
     `services.lookup_builder.build_lookup_from_rrf()`, cache.
   - Download derived artifacts (BM25, SapBERT, patient_friendly) from
     open HF dataset via `huggingface_hub.snapshot_download()`. These
     land in the standard HF cache, shared across projects.
   - Set env vars (`MEDTERM4DS_DB`, `MEDTERM4DS_SEARCH_INDEX_DIR`,
     `MEDTERM4DS_EMBEDDING_MODEL_DIR`, `MEDTERM4DS_FHIR4PX_BASELINE`)
     so downstream services find the cached data.

3. **Error handling for provisioning failures**:
   - Missing `UMLS_API_KEY`: raise with link to NLM signup
     (https://www.nlm.nih.gov/account/)
   - Bad key: NLM download fails with a clear error — no pre-validation
     service (NLM doesn't have a public "validate this key" endpoint;
     the download attempt IS the validation)
   - Network error: clear message, suggest `MEDTERM4DS_OFFLINE=1` if
     cache exists from a prior run
   - Build failure: preserve the traceback, suggest filing an issue

4. **Idempotent**: calling `connect()` twice returns the same engine
   (or reconnects to the same cached DB if the process was restarted).

5. **Offline mode**: if `MEDTERM4DS_OFFLINE=1` and cache exists, skip
   all network calls. Error if data is missing.

**Done when:** A fresh-machine user can run `mt.connect()` and get a
working `Terminology` instance in one call (with ~8 min one-time build
on first run).

### Phase 2 — PyPI publish

**Goal:** `pip install medterm4ds` produces a working Python package.

> **Prerequisite:** Phase 1 must be done first. Publishing a package
> that can't provision its own data is worse than not publishing.

Tasks:

1. **`pyproject.toml` is already mostly there** (hatchling backend,
   extras structure, Python 3.10+ pin). Verify:
   - Pure-Python wheel build (no extension modules)
   - `duckdb` as hard dep; `medspacy`, `transformers`, `torch`,
     `fastapi`, `uvicorn`, `fastmcp`, `huggingface_hub` as extras
   - Python version: `>=3.10`
2. **Add `huggingface_hub` as a hard dependency** — needed for derived
   artifact download in `connect()`. It's lightweight (~2 MB) and
   already used everywhere.
3. **CI: build wheel + sdist on tag push**, publish to PyPI via
   GitHub Actions using trusted publishing (no PyPI token needed if
   using GitHub OIDC).
4. **Verify** in a fresh venv:
   ```bash
   pip install medterm4ds[search,extraction]
   python -c "import medterm4ds as mt; t = mt.connect(); print(t.lookup('SNOMEDCT_US', '44054006').name)"
   ```
5. **Pin the `mt.connect()` contract** with integration tests so
   breaking changes trigger major version bumps.

**Done when:** PyPI install works without git or Docker, and `connect()`
auto-provisions on first run.

### Phase 3 — Cache management

**Goal:** One cache per machine, shared across projects, with
inspection and cleanup tools.

Two caches coexist:

```
~/.medterm4ds/                     ← medterm4ds-owned (lookup.duckdb)
├── config.toml                    # last_build_release, settings
├── cache/
│   ├── lookup-2026AA.duckdb       # built locally, ~217 MB
│   └── lookup-2026AB.duckdb       # can coexist during upgrade
└── lock                           # file lock for concurrent builds

~/.cache/huggingface/              ← HF-owned (derived artifacts)
├── hub/
│   ├── datasets--joelmontavon--medterm4ds-data/
│   │   ├── bm25/                  # ~167 MB
│   │   ├── sapbert/               # ~2.5 GB
│   │   └── patient_friendly/      # ~225 MB
```

Tasks:

1. **`MEDTERM4DS_HOME` env var** — overrides `~/.medterm4ds/` root for
   shared/enterprise setups (`/var/cache/medterm4ds/`, NFS mount, etc.).
2. **`HF_HOME` env var** — already supported by `huggingface_hub` for
   the derived-artifact cache. No custom code needed.
3. **Version-stamp `lookup.duckdb`** in filename so multiple UMLS
   releases can coexist. User upgrades by building the new version
   alongside the old, then switching.
4. **Cache inspection**: `mt.cache_info()` returns dict with paths,
   sizes, versions, build dates.
5. **Cache cleanup**: `mt.cache_clear(older_than="2026AA")` removes
   old lookup.duckdb versions. HF cache managed via `huggingface-cli
   delete-cache`.
6. **File locking**: use `filelock` library (already a transitive dep
   via `huggingface_hub`) to prevent concurrent builds on the same
   machine.

**Done when:** A user with 3 Python projects using medterm4ds has one
~3 GB cache (lookup.duckdb + derived artifacts), not 9 GB.

### Phase 4 — UMLS API key handling

**Goal:** License compliance is smooth, not painful.

Tasks:

1. **Key sources** (checked in order):
   - `umls_api_key=` parameter to `connect()`
   - `UMLS_API_KEY` environment variable (CI/CD, Docker, ephemeral)
   - `~/.medterm4ds/config.toml` `[umls]` section with `chmod 600`
   - Optional: OS keychain via `keyring` library when available
2. **No pre-validation service**. NLM doesn't offer a public key-check
   endpoint. The download attempt IS the validation — if the key is
   bad, the NLM download fails with a 403 and we surface a clear error
   with a link to NLM signup.
3. **`python -m medterm4ds.setup` interactive flow** (optional, for
   first-time users who prefer a wizard):
   ```
   $ python -m medterm4ds.setup
   Welcome to medterm4ds setup.

   medterm4ds uses UMLS Metathesaurus data, which requires an NLM API key.
   Get one (free): https://www.nlm.nih.gov/account/

   Enter your UMLS API key: ****-****-****
   Building lookup.duckdb from UMLS 2026AA (one-time, ~8 min)...
   Downloading derived artifacts from Hugging Face...
   Done! You can now use medterm4ds:
       import medterm4ds as mt
       terms = mt.connect()
   ```
4. **Key storage**: write to `~/.medterm4ds/config.toml` with `chmod 600`
   by default. `keyring` integration is optional (not all CI environments
   have a keyring backend).
5. **Air-gapped install**: document the offline path — download on a
   connected machine, copy `~/.medterm4ds/` to the target. `MEDTERM4DS_OFFLINE=1`
   skips all network calls.

**Done when:** Analyst can go from "I just installed medterm4ds" to
"working terminology engine" with one command and one key entry.

### Phase 5 — Open artifact distribution

**Goal:** Derived artifacts are published openly on Hugging Face so
first-use downloads are fast and shared across projects.

> **No gating.** These are derivative works, redistributable under the
> UMLS license with a notice. We are not a license-enforcement authority.

Tasks:

1. **Legal review** (lightweight — the UMLS license is clear on
   derivative works):
   - BM25 indexes: **redistributable** (tokenized, scored — transformed
     beyond recognition from source strings)
   - SapBERT + FAISS: **redistributable** (model weights + embedding
     indexes — industry standard for publishing)
   - patient_friendly JSONs: **likely redistributable** (hierarchy
     resolution + MEDLINEPLUS/CHV matching — transformed work). Needs
     a quick check on MEDLINEPLUS redistribution terms.
   - `lookup.duckdb`: **not redistributable** (raw UMLS content —
     concept names, CUIs, AUIs, hierarchy edges). Build locally.

2. **Publish to open HF dataset** (`joelmontavon/medterm4ds-data` or
   similar):
   - Include UMLS license notice in the dataset card
   - Clearly identify as UMLS-derived
   - No access gating — anyone can download

3. **`connect()` downloads via `huggingface_hub.snapshot_download()`**:
   - Resumable downloads, file locks, content-addressed dedup — all
     built in
   - Lands in standard HF cache, shared with other HF-using tools
   - Already used in the Docker container — same code path

4. **Document the data flow** so users understand what gets downloaded
   (derived, ~3 GB) vs built locally (lookup.duckdb, ~217 MB from RRF).

**Done when:** First-use on a fresh machine downloads ~3 GB from HF
(no key needed for derived artifacts) + builds lookup.duckdb locally
(~8 min with key). Total first-use: ~10 minutes.

### Phase 6 — Versioning contract

**Goal:** Consumers can pin and upgrade safely.

Tasks:

1. **Adopt semver strictly**: breaking API changes trigger major
   version bump; data schema changes trigger minor version bump; bug
   fixes trigger patch.
2. **Publish a CHANGELOG.md** following the keepachangelog format.
3. **Send breaking-change notices to consumers** (the email pattern
   used with fhir4ds — that worked well).
4. **Pin UMLS release in artifact filenames** (`lookup-2026AA.duckdb`)
   so users can opt into `2026AB` before it's the default.
5. **Deprecation policy**: old APIs work for one major version with
   warnings, then removed.

**Done when:** A consumer can pin `medterm4ds>=0.1,<1.0` and trust
that their code keeps working across that range.

### Phase 7 — Batch APIs (low priority, opt-in throughput optimization)

**Goal:** Enable true server-side batching for high-throughput
consumers (1M+ resource backfills).

**Priority:** Low. fhir4ds captures 70–80% of the available throughput
win on its own via request concurrency (parallel HTTP) and worker
parallelism (`multiprocessing.Pool`). This phase is the remaining
20–30%. Only worth doing if a real consumer asks for it.

Tasks:

1. **`mt.search_batch(queries)` Python API** — single call, N queries,
   returns N result lists. Lets SapBERT run as a single batched
   embedding call (one matrix multiply vs N forward passes).
   Significant for GPU users; modest for CPU.

2. **`mt.extract_batch(texts)` Python API** — same idea for the NER
   pipeline. medspaCy already amortizes pipeline load within a
   process; the win is making the batching explicit and letting
   SapBERT run batched.

3. **`$search-batch` FHIR operation** — single POST, N queries, single
   response Bundle. Avoids per-query HTTP overhead.

4. **Picklability guarantee** for `ExtractedConcept` and `SearchResult`
   — required for fhir4ds to use `multiprocessing.Pool` cleanly across
   process boundaries. Verify with `pickle.dumps()` round-trip tests.

5. **Document throughput expectations** — what's the practical max
   QPS for search_batch on commodity hardware? For extract_batch?
   Helps consumers plan capacity.

**Done when:** A consumer can call `mt.search_batch(queries)` or
`mt.extract_batch(texts)` and get results faster than looping the
single-call equivalents by at least 30%.

**Why this is low priority:**

fhir4ds's `batch_size` + `workers` design delivers the bulk of the
throughput improvement without requiring any medterm4ds changes:

- fhir4ds parallel HTTP via `ThreadPoolExecutor` captures wire-cost
  amortization today against the existing single-query `$search`
  endpoint.
- fhir4ds multiprocessing workers parallelize CPU-bound NLP work
  today against the existing single-text `extract()`.

This phase adds the additional compute optimizations (SapBERT batched
embeddings, explicit medspaCy pipeline reuse). Worth doing eventually
for production deployments, not blocking for analyst users.

## What fhir4ds will do once Phases 1–2 land

fhir4ds-side changes are small. Listed here so the medterm4ds team
knows what to expect from the consumer side:

1. **Bump `[fhir4ds,terminology]` extra** to depend on
   `medterm4ds[search]>=0.1`. One-line change in `pyproject.toml`.
2. **Simplify `InProcessTerminologyEndpoint`** to use `mt.connect()`.
   Existing implementation calls into `engines.local_duckdb` and
   `apps.fhir_api_helpers` directly — those go away.
3. **Factory auto-detect**: when `medterm4ds` is importable and no URL
   is set, prefer in-process. ~10 lines in `factory.py`.
4. **Documentation**: README and AGENTS.md updated to reflect "analyst
   = in-process, ops = HTTP" split.
5. **No changes** to the CQL integration, closure table, or
   auto-coder — those already work against either adapter via the
   `TerminologyEndpoint` Protocol.

Estimated fhir4ds work: **half a day, post-publish**. No new
architecture, just thinning out the in-process adapter and bumping the
dep.

## Migration path for existing fhir4ds users

- **HTTP sidecar users**: nothing changes. HTTP remains fully
  supported and is the right answer for production deployments.
- **Existing in-process users** (git-clone installs): replace with
  `pip install medterm4ds`, re-run setup. Cache location may change
  from project-local to `~/.medterm4ds/`.
- **New analyst users**: get the one-command install path from day
  one.

## Open questions

1. **Package name on PyPI**. `medterm4ds` matches the existing module
   name and Docker image. Check for conflicts with existing PyPI names
   early.
2. **Extras granularity**. Current split (`duckdb`, `search`,
   `extraction`, `fhir`, `mcp`, `all`) matches internal module
   boundaries. Confirm this is the right granularity or if it should
   be flatter.
3. **Python version floor**. Currently `>=3.10`. Confirm 3.10 is the
   floor or if 3.11+ is acceptable (3.10 reaches EOL Oct 2026).
4. **Update notification**. When UMLS releases 2026AB, how do users
   learn? Auto-detect on `connect()` with a soft prompt is one
   pattern; explicit `mt.update` command is another. Recommend the
   latter for analysts (no surprise 8-minute waits).

## Sequence

```
Phase 1 (provisioning path)        ← GATE: must land first
   │
   ├── Phase 2 (PyPI publish)      ← GATE: package must be useful out of the box
   │     │
   │     └── fhir4ds auto-detect change (~half day)
   │
   ├── Phase 3 (cache management)  ← iterative, after 1-2
   │
   ├── Phase 4 (UMLS key handling) ← can run in parallel with 1
   │
   ├── Phase 5 (open artifact distribution)  ← HF dataset already exists, just formalize
   │
   ├── Phase 6 (versioning)        ← ongoing discipline
   │
   └── Phase 7 (batch APIs)        ← low priority, fhir4ds covers most wins alone
```

**Phases 1 and 2 are the gates.** Phase 1 (provisioning) must work
before Phase 2 (publish). Once both land, fhir4ds can flip its
default and analysts have a one-command install. Phases 3–5 are
iterative UX improvements; Phase 6 is ongoing discipline.

## References

- Original fhir4ds ↔ medterm4ds integration plan:
  `fhir4ds-private/docs/plans/medterm4ds-integration.md`
- fhir4ds Phase 1 (terminology abstraction) FDD:
  `fhir4ds/docs/architecture/plans/FEATURE_MEDTERM4DS_PHASE1_TERMINOLOGY.md`
- fhir4ds Phase 4 (clinical notes NER) FDD:
  `fhir4ds/docs/architecture/plans/FEATURE_MEDTERM4DS_PHASE4_NER.md`
- medterm4ds text extraction plan:
  `docs/plans/text-to-concepts-extraction.md`
- HuggingFace Hub client library: `pip install huggingface_hub`
- UMLS Metathesaurus license: https://www.nlm.nih.gov/research/umls/license.html
