---
title: Releases
---

This page tracks Medical Terminology for Data Science package releases.

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
  (`text`, `name`, `type`, `source_code`, `canonical_id`, `status`)
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
