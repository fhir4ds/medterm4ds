---
title: Releases
---

This page tracks Medical Terminology for Data Science package releases.

## 0.0.6

Athena vocabulary support release — the fhir4ds OMOP VD campaign's
join source. No breaking API changes.

Athena ingestion and the ConceptMap bridge:

- `medterm4ds data ingest-athena --athena-dir <download>` loads
  CONCEPT / CONCEPT_ANCESTOR / CONCEPT_RELATIONSHIP / VOCABULARY
  into `athena_*` tables in an existing UMLS DuckDB (same-DB
  namespaced; `--vocabularies` filters; a per-vocabulary licensing
  notice prints first and `--accept-licenses` records the
  acceptance in `athena_meta`). Real-data verified: 4,088,225
  default-vocabulary rows in 16-39s, 40.7M ancestry rows
  semi-joined to the loaded set (zero orphans), zero duplicate
  `(vocabulary_id, concept_code)` pairs.
- `medterm4ds data export-conceptmap --out <path>` emits the
  ConceptMap artifact in TWO forms: **monolithic** (one resource,
  nested groups per vocabulary — the standard artifact; the default
  for `.json`) and **thin** (one ConceptMap per mapping row, NDJSON
  — the scalar ON-pair join contract; the default for `.ndjson`).
  `--form` overrides with a mismatch warning. Identity
  vocabularies (ICD-10-CM, ICD-10-PCS, ATC, HCPCS, NDC) map
  `equal` regardless of the standard_concept flag; deprecated
  standard concepts carry `unmatched`; each group carries its
  `vocabulary_version` provenance. Export at scale: thin 4.1M rows
  in ~18s (~228k rows/s).
- Engine alias extensions: the HCPCS `urn:oid:2.16.840.1.113883.6.14`
  and `http://hl7.org/fhir/sid/ndc` forms now resolve —
  Athena-bridge system URIs are `$validate-code`-clean.

Licensing note: Athena downloads bundle vocabularies under terms
that differ from the UMLS Metathesaurus license (SNOMED CT use is
governed by the SNOMED Affiliates license; CPT requires the
UMLS/AMA agreement even via Athena). The loader surfaces the notice
table and requires explicit acceptance; redistribution of exported
artifacts follows the strictest source vocabulary.

Robustness (athena-batch1, from the nightly spec-comp program):
real-schema header detection, edge-table semi-joins, actionable
CLI boundary errors, loud unknown-vocabulary validation, and
form-from-extension exports. Full registry in
`docs/maintenance-findings-registry.md`.

Benchmarks: Athena ingest 4.09M rows in 16-39s; export ~228k
rows/s; search ladder unchanged (lexical ~1ms, semantic ~68ms,
hybrid ~81ms, canonical ~32ms warm).

## 0.0.5

Serving-stack and conformance-hardening release. No breaking API
changes; no DB rebuild required.

Serving stack (semantic + canonical search):

- The semantic path now serves the B-prime hybrid encoder with
  mixed-pool indexes (rotated via the split layout's
  `MEDTERM4DS_EMBEDDING_MODEL_DIR` / accepted-space registry).
  Condition-domain exact-match quality rises to 0.733 from 0.39 on
  the prior serving stack (fleet campaign record); lexical BM25 is
  unchanged.
- Display names carry per-entry `match_type` provenance
  (`code_name` / `card_overlay` / `cvx_group`) from the names-basis
  fold (associations `e277ffc9`, display_names v2 `60bb0e8e`,
  chaining the C1+C2 code-names layers).
- Embedding-space serving pin: `MEDTERM4DS_EMBEDDING_SPACE` is
  per-call overridable; a set-but-unknown pin now fails loud naming
  the accepted values (previously a silent lexicographic fallback
  that would have kept serving the OLD space after a rotation).
  `cache-info` reports `serving_space` + `serving_space_source`.
- Steady-state benchmarks (5 queries x 3 reps, count=5): lexical
  ~1 ms, semantic ~67 ms, hybrid ~76 ms, canonical ~30 ms; one-time
  cold SapBERT load ~1 min.

Conformance hardening (six fail-loud batches from the nightly
spec-comp program — iterations 55-82, ~120 findings registered, 15
resolved in-batch; full registry in
`docs/maintenance-findings-registry.md`):

- `$validate-code` ValueSet `url` now SCOPES membership; unresolvable
  ValueSet urls 400.
- `$translate`: `targetSystem` binds and is required (R4 §4.9.13.1;
  a param-name case mismatch previously dropped every conformant
  client's target filter); `reverse=true` is consulted (orientation
  flip + named message); url/version resolve against the implicit
  crosswalk map or 400.
- `$subsumes`: coding-only POST self-identifying; unknown/cross-system
  codes 400 with a `$translate` pointer.
- Unknown/unsupported parameters 400 across `$expand`, `$lookup`,
  `$validate-code`, `$subsumes`, `$translate`, `$closure` (GET query
  + POST bodies); `version`/`systemVersion` uniformly rejected
  (single-version server); `date` validated as FHIR dateTime;
  `$expand` echoes `expansion.params`.
- `$search` `resultTypes` filters on ALL modes (was canonical-only);
  patient-friendly miss rows uniform (`match_type="none"`) across
  engine paths.
- `$closure`: typo'd parameter names 400 at the boundary (the
  destructive wipe variant is closed).

Distribution note: serving models and indexes ship via the
`fhir4ds/medterm4ds` HF split layout and fetch through the artifact
cache at first use (operator caches grow to GB scale); the wheel
stays lean (~485 KB) and carries code only.

## 0.0.4

Artifact-governance and cross-surface parity release. No breaking API
changes; no DB rebuild required.

Artifact governance (manifests + split cache layout):

- Model and data artifacts from the `fhir4ds/medterm4ds` HF repo now ship
  `manifest.json` units: `models/&lt;embedding_space_id&gt;/`
  (content-addressed SapBERT, pinned by an in-code acceptance registry) and
  `data/&lt;data_revision&gt;/` (canonical value sets + concept index,
  floating to the latest published revision with the resolved revision
  logged). The runtime validates embedding-space identity and md5 lineage
  at component load and hard-refuses on mismatch — per-component, so
  lexical/canonical-only jobs are never blocked by the semantic gate.
- The legacy flat layout (`semantic/` + `canonical/` under a pinned tag)
  keeps working during a deprecation window; `medterm4ds data
  cache-refresh --split` migrates. Kill-switch: `MEDTERM4DS_LAYOUT=legacy`.
  Data-revision pin: `MEDTERM4DS_DATA_REVISION`.
- GLiNER extraction carries a calibration id (labels + threshold);
  an uncalibrated config refuses to load unless
  `MEDTERM4DS_NER_ALLOW_UNCALIBRATED=1` is set (warns with both ids).

Terminology and parity:

- CDC CPT↔CVX crosswalk merged into the mapping service (vendored
  single-best table; CDC rows win conflicts; reverse direction is the
  one-to-many inverse) — no rebuild needed. CVX group metadata persists
  `group_cvx`; CDC `VG.txt` is vendored (no runtime downloads).
- `annotation_fields` wired across all four surfaces (FHIR
  `$extract` `annotationFields` validated pre-NER, MCP parameter, CLI
  flag) — previously Python-only, with FHIR silently ignoring it.
- Default HF artifact revision → `v0.0.5` (shorthand class aliases,
  CDC vaccine groups, lab SNOMED crosswalk, ICD-10-PCS, guard hardening).

Robustness:

- Whitespace-only codes on `$translate`/`$lookup`/`$validate-code` return
  400 OperationOutcomes instead of 500s.
- CapabilityStatement no longer emits the STU3-only `rest[].url`
  (rejected by fhir.resources in R4).
- `extract()` validates `annotation_fields`/`result_types`/`min_grade`
  before any NER work; `resolve_device` rejects out-of-range `cuda:&lt;n&gt;`
  with an actionable error.
- CI now runs on `dev` pushes (the Aug-16 gap that let 193 conformance
  tests drift red).

## 0.0.3

Performance and configurability release for extraction. No breaking
changes; no data rebuild required.

GPU acceleration:

- `MEDTERM4DS_DEVICE` (default `auto`) places GLiNER and SapBERT on CUDA/MPS
  when available — zero configuration on GPU hosts, CPU hosts unchanged.
  Explicit GPU requests that are unavailable raise instead of silently
  falling back. Deterministic pipelines should pin `cpu`.

Batch extraction:

- `extract()` accepts a single text or a list of texts (one result per
  text, input order)
- Cross-text batched GLiNER inference (`MEDTERM4DS_EXTRACT_BATCH_SIZE`)
- Pooled cross-text canonical resolve with corpus-wide entity-text
  deduplication (`MEDTERM4DS_EMBED_BATCH_SIZE`)
- Measured on a 50-source drug-label shard (RTX 4090): 7.9 sources/min
  single-process, up from ~1.0 on CPU per-text calls

Performance fix:

- The head-noun lab-vs-med arbiter no longer re-materializes
  `doc.noun_chunks` per span — an instrumented consumer profile showed it
  was 50% of batch runtime. Results unchanged; 2.7x on real workloads.

Extraction configurability:

- `annotation_fields` for `format="annotated"` customizes inline markers
  (`text`, `name`, `type`, `source_code`, `canonical_id`, `status`) —
  every surface: Python/MCP `annotation_fields`, FHIR `annotationFields`,
  CLI `--annotation-fields`
- Span metadata carries `match_grade` and `source`/`code`
- Direct multi-threaded use of the extraction service is safe (service-level
  lock); multiprocessing still requires lazy per-worker model loads

Other:

- Default Hugging Face artifact revision bumped to `v0.0.2` (corrected
  canonical indexes)

Known issues:

- 191 FHIR conformance test failures are pre-existing at 0.0.2 (verified
  against the v0.0.2 tag: 192 there) — environmental library drift in the
  source-read structural suites, not product regressions. All other suites
  pass (7,633 tests).

## 0.0.2

Quality-hardening release: 613 bugs found and fixed across a 22-domain
adversarial QC sweep, extraction pipeline overhaul, FHIR conformance
improvements, and new capabilities.

Compatibility:

- Python 3.10 and newer
- UMLS DuckDB builds from flat RRF, compressed RRF, or `.nlm` archives
- **Requires prepared-schema 0.9 rebuild** (`medterm4ds data prepare-derived --db <path>`) — see Upgrade notes below

New capabilities:

- `include_retired` parameter on hierarchy walks (Python, CLI, MCP, discover)
- FHIR `$expand activeOnly` parameter (GET, POST, batch)
- `--result-types` filter on CLI and MCP `search` (service-side, no truncation)
- `$extract` POST now accepts `includeNegated`/`includeUncertain`/`includeHistorical`/`includeFamily`

Extraction pipeline:

- Dependency pin fix: gliner 0.2.28+ and transformers capped below 5.0 (extraction was silently dead on lock installs due to a library-level ModernBERT break)
- Three-signal lab-vs-medication disambiguation: head-noun analysis, unit-type detection, and ConText cue matching — 100% precision on head noun and unit signals
- Label-constrained canonical search with fallback (fixes diseases-resolving-to-lab-anchors and drugs-resolving-to-TDM-levels)
- Population blocklist (adults, women, etc. no longer extracted as clinical entities)
- GLiNER model revision pinned to prevent HF weight drift

FHIR server:

- `$closure` migrated from path-enumerating CTE to bounded BFS (32GB OOM → bounded)
- `$expand` offset paging, exclude-with-filter, spec-canonical `?fhir_vs=isa/<code>` URLs, deterministic ordering
- XML serializer consolidation (control-char sanitization, url-attribute convention, resourceType rendering)
- Batch dispatch parity (method guards, ValueSet bodies, error outcomes, transaction-response type)
- Single db_executor (fixes dual-executor race that produced silent "Code not found" under load)
- Content negotiation conformance (+decode, 405/406, Accept exact-match)

Data pipeline:

- Prepared schema 0.8 → 0.9 (detects stale tables, enables RXNORM/ATC/MSH hierarchy, LOINC multiaxial, CPT preferred-term)
- Atomic builds (validate-before-replace, temp+rename, no junk DBs on failures)
- Connection-string path rejection (`?mode=ro` etc.)
- Catalog-qualified relocatable views (DB copies work)
- Verify verdict + golden-count drift detection

Cross-surface consistency:

- Strict input validation everywhere (empty strings, URI-form sources, unknown-source errors)
- CLI error envelopes (no raw tracebacks)
- `cache_indexes` default harmonized to `False` across all surfaces
- Engine env-var contract (`MEDTERM4DS_MEMORY_PROFILE` etc.) honored by all 5 surfaces

Upgrade notes:

- Run `medterm4ds data prepare-derived --db <path>` to rebuild the prepared schema from 0.8 to 0.9. Until rebuilt, hierarchy is partial for RXNORM/ATC/LOINC, CPT displays use ETCF instead of PT, and patient-friendly uses the slower legacy resolver.
- Install the spaCy parser model separately (not on PyPI): `uv pip install "en-core-web-sm>=3.7,<4" --find-links https://github.com/explosion/spacy-models/releases`
- `/optimize` envelope key changed from `result` to `results` (legacy key accepted as fallback)
- `connect()` now rejects nonexistent DB paths instead of silently creating empty files
- Remote engine default timeout raised from 30s to 300s

Known issues:

- Closure-accelerated ancestor walks at depth ≤5 may return empty for RXNORM/ATC/MSH on stale closure tables (workaround: `max_depth >= 6`; fixed by the next prepared-schema rebuild)
- 4 pre-existing FHIR conformance test failures (environmental `fhir.resources`/`annotated_types` library drift, not product bugs)
- Deferred executor fairness: one slow query can delay queued FHIR operations (mitigated by health-check bypass and bounded descendants)

## 0.0.1

Initial release candidate for the refactored medterm4ds package.

Compatibility:

- Python 3.10 and newer
- UMLS DuckDB builds from flat RRF files, compressed RRF files, or `.nlm` release archives
- Release-pinned UMLS builds are supported with `--release-version`

Public surfaces:

- Notebook-first `Terminology` client from `medterm4ds.connect(...)`
- Local DuckDB engine and `RemoteApiEngine`
- Lookup, resolve, patient-friendly names, mapping, hierarchy, optimize, discovery, and ConceptMap helpers
- CLI, API, and MCP adapters over the same service layer
- pandas and Polars DataFrame helpers
- JSON, JSONL, CSV, compact text, ASCII tree, and FHIR R4 ConceptMap outputs

Release checks:

- unit, lint, and compile verification
- real-data lookup, mapping, hierarchy, discovery smoke tests
- patient-friendly parity matrix against `/mnt/d/medterm`
- CLI acceptance smoke for JSONL resume, CSV, FHIR R4, lookup, map, and hierarchy
- notebook smoke for included examples
- bulk validation and mapping-quality CSV generation
- package build, metadata check, and fresh-venv wheel install smoke
- Docusaurus typecheck and production build

Known 0.0.1 parity decisions:

- CPT patient-friendly display intentionally uses deterministic source-specific term ordering instead of legacy order-dependent atom selection.
- SNOMED original display uses the preferred term and keeps the fully specified name as `technical_name`.
- Legacy RxNav approximate drug spelling/class workflows are represented by UMLS-backed compatibility wrappers; richer RxClass/RxNav behavior is deferred.

UMLS data release details live in [UMLS Release Info](./umls-release-info.md).
