# Changelog

All notable changes to medterm4ds are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_No unreleased changes yet. v0.0.2 shipped on 2026-08-16; subsequent work tracks
here until the next tag._

## [0.0.2] - 2026-08-16

Systematic QC sweep across 22 domains: 497 defects found, ~340 fixed
(331 during the sweep plus 6 in the final review pass), including 7 CRITICAL
and 83 HIGH-severity fixes. **This release requires a one-time rebuild of
derived tables on existing databases** — see Upgrade notes.

### Breaking changes

- **`mt.connect()` no longer creates a database at a nonexistent path** (default
  `read_only=True` unchanged). A typo'd path previously materialized a silent
  12 KB junk DuckDB file; now `connect()` raises `RuntimeError` with build
  guidance. Paths containing `?` (DuckDB 1.5 URI suffixes, e.g. `…?mode=ro`)
  are also rejected. Build with `medterm4ds data build-duckdb` or omit
  `db_path` to auto-provision. [QC-468]
- **`mt.connect(cache_indexes=...)` default changed `True → False`**, now
  matching the documented server default (`MEDTERM4DS_CACHE_INDEXES=false`).
  Index creation adds ~28 s and ~0.8 GB to a production prepare for little
  gain on read-mostly workloads; pass `cache_indexes=True` explicitly if you
  need temp indexes. [QC-470]
- **`/optimize` response envelope key renamed `result` → `results`**, aligning
  it with the other 12 data endpoints. The v0.0.2 `RemoteApiEngine` still
  accepts the legacy `result` key, but a v0.0.1 engine against a v0.0.2 server
  fails with `RuntimeError` — upgrade engine and server together. [QC-490]
- **Stricter input validation — previously "successful" garbage now errors.**
  Empty/whitespace sources and codes, URI/OID-form sources (`http://…`,
  `urn:oid:…`), and empty `target_sources` now raise `ValueError` from the
  Python service layer (mapping to HTTP 422/400 on the servers, which
  previously returned 500 for an empty source string). An unknown-but-well-formed
  source remains valid (resolves to not-found); a source absent from the
  database returns HTTP 400 instead of empty data. [QC-422, QC-489]
- **`mt.connect()` now honors the documented engine environment variables**
  (`MEDTERM4DS_MEMORY_PROFILE`, `…_MEMORY_LIMIT`, `…_TEMP_DIR`, `…_THREADS`,
  `…_QUERY_CHUNK_SIZE`) as fallback defaults, matching the three servers.
  Explicit arguments always win. [QC-464]
- **`connect(prepare_cache=True)` now prepares ATC too** (9-source
  `DEFAULT_INVENTORY_SOURCES`), so a prepared Python engine answers ATC
  lookups the same way CLI/MCP/API/FHIR do. [QC-469]

### Added

- **`include_retired` opt-out from the active-only hierarchy walks** (the
  QC-238 query-time retired-concept pruning). `get_code_relations` /
  `get_descendants_bfs` / `get_ancestors_bfs` and the
  `parents`/`children`/`ancestors`/`descendants`/`hierarchy` facade methods
  accept `include_retired=True` to include retired/editorial-suppressed
  concepts as walk targets on both the prepared and raw-mrrel engine paths;
  wired through the REST `/hierarchy` request, MCP
  (`get_parents`/`get_children`/`get_ancestors`/`get_descendants`/
  `code_relations`/`discover`), and `domains.terminology.discover`.
  Defaults are unchanged everywhere — active-only.
- **FHIR `$expand` `activeOnly` is honored** (was silently ignored, QC-315).
  `activeOnly=true` matches the server default; `activeOnly=false` includes
  retired concepts for the isa/fhir_vs, implicit-value-set, and intensional
  compose expansions (GET, POST Parameters `valueBoolean`, and `$batch`).
  **Documented divergence from R4**: the spec default for the parameter is
  `false`; this server's default when `activeOnly` is OMITTED narrows to
  active-only, matching the engine-wide QC-238 contract. Filter-based (text
  search) expansions reject `activeOnly=false` with 400 — the search index
  is active-only and cannot cheaply express concept activity.
- **Pinned GLiNER NER model revision** (`knowledgator/gliner-bi-small-v2.0` @
  `3d74c1bf459b8b1c0be1ecbddd679416ce005418`) so Hugging Face weight drift
  can't silently change extraction recall (drift observed 2026-08-14).
  Env overrides preserved: `MEDTERM4DS_NER_MODEL` and
  `MEDTERM4DS_NER_MODEL_REVISION` (empty value unpins).

### Changed

- **Default remote timeout raised 30 s → 300 s.** `connect_remote()` /
  `RemoteApiEngine` now default to `DEFAULT_REMOTE_TIMEOUT = 300.0` — the old
  default was below the server's own measured workload domain (`optimize`/`map`
  over SNOMED 55–82 s; a 10,000-code batch ~415 s cold). The timeout counts
  time queued behind other requests on the single-worker DB executor; pass
  `timeout=600` for bulk batches at the 10,000-code cap. Constructor arguments
  are validated up front (`base_url` must be http(s), `timeout` positive).
  [QC-485]
- **Request caps enforced for real**: 10,000 codes / 256 chars per code /
  64 chars per source / ≤1,000 list items / 10 MB body on the API, and a
  50 MiB client-side response cap on `RemoteApiEngine` (bind wide hierarchy
  walks with `descendants(..., limit=)` — now available on the facade).
  [QC-474, QC-494]
- **Empty-input DataFrame parity**: all `*_df()` methods return a
  correctly-columned 0-row DataFrame on empty or all-missing input instead of
  a 0-column frame. [QC-045, QC-072, QC-073, QC-080, QC-105, QC-106]
- **API robustness**: `/health` no longer leaks the DB filesystem path; error
  bodies echoed into `RuntimeError` messages truncate at 2,048 chars (was
  430,291 chars on a 10,001-code batch error); body-size cap actually enforced
  on list length. [QC-477]
- **CLI**: `--limit`/`--threads`/`--memory-limit` garbage values fail with a
  clean one-line error, no raw tracebacks; `--max-depth` no longer silently
  overridden to 1 for `parents`/`children`. [QC-058, QC-380, CR-043]

### Fixed

Of ~340 fixes in this release; the most user-visible:

- **Extraction lab-vs-med disambiguation now arbitrates with three signals
  in priority order.** Two higher-precision signals run before the ConText
  tie-breaker: head-noun analysis of the noun phrase containing the span
  (en_core_web_sm dependency parse — "vancomycin level was 8" → lab,
  "vancomycin dose adjusted" → medication) and unit-type detection on the
  number following the span (concentration "5.2 mEq/L" → lab, dose
  "40 mEq" → medication). ConText MEASUREMENT/ADMINISTRATION cues remain
  the Signal 3 fallback; no signal fired keeps the GLiNER label mapping.
  Evaluation on the 212-item v2 corpus (docs/.ai_loop/qc_comp/
  three_signal_results.md): signals 1-2 fired 62/62 correct, overall
  in-scope accuracy 78.3% → 84.4%, TDM group E 65% → 94%. Requires the new
  `en-core-web-sm` dependency in the `extraction` extra (absent model
  degrades gracefully to ConText-only arbitration).
- **Extraction lab-vs-med disambiguation now uses medspaCy ConText as a
  tie-breaker.** The old analyte heuristic (a 24-name hand list plus an
  administration-verb regex and a TDM "level" adjacency rule) is replaced
  by two custom ConText categories — MEASUREMENT and ADMINISTRATION cue
  lexicons read from sentence context — evaluated at 100% accuracy on
  decided items over a 60-item corpus (the biggest NER wrong-type pattern:
  1,046 lab analytes typed "therapeutic agent"). A measurement-only
  context overrides the GLiNER label to search `lab` anchors
  ("carbamazepine level was 8"), administration-only to `medication`
  ("potassium chloride 20 mEq IV given"), both fire search both and let
  anchor ranking decide, and no decision conservatively keeps the label
  mapping. Also fixes `medspacy.load(disable=...)` → `medspacy_disable=`
  — the old kwarg was silently swallowed, so the unused target matcher
  ran in the pipeline anyway.
- **`search --result-types` with a small `--limit` returned zero results.**
  The CLI applied the category filter client-side AFTER the service had
  truncated to `--limit`, discarding truncated slots instead of backfilling
  (`search "Potassium" --result-types lab --limit 1` → 0 results; `--limit 8`
  → the LOINC potassium anchor at 0.999). The filter now forwards
  service-side for every mode: canonical filters by canonical_id prefix
  (with the existing filter-aware over-fetch), and lexical/semantic/hybrid
  restrict the category indexes searched before retrieval, so `--limit`
  caps the filtered set. Unknown `--result-types` values still return an
  empty result (with the QC-129 warning on legacy modes), never a crash.
- **Prepared-table correctness** — CPT prepared priority ranked ETCF above PT,
  returning the wrong CUI/display/TTY for ~90% of CPT codes; the RxNorm
  hierarchy was entirely absent from `mt4ds.walk_edges` (0 of 238,329 isa
  edges); LOINC ignored the official multiaxial hierarchy (284,774 `class_of`
  edges); an AUI mismatch silently lost 34,471 PT-child relationships.
  [QC-016, QC-349, QC-350, QC-070 — needs the rebuild below]
- **`resolve_mode` actually works now.** `historical` and `resolve_current`
  produced byte-identical output; CLI `--resolve-mode` was a silent no-op on
  production databases; an NDC-lookup regression in the default mode; SUPPRESS
  'E' (editorial) no longer conflated with 'O' (obsolete). [QC-017, QC-398,
  QC-401, QC-406, QC-119, QC-120]
- **ConceptMap `equivalence` mislabeling.** `snomed_fallback`, TTY-traversal
  `group` matches, and 6+ other depth>0 match types were emitted as
  `equivalent`; in a production-scale sample 3,970/3,970 RxNorm rows at
  `match_depth>0` were mislabeled; CVX product→family edges now `narrower`.
  [QC-074, QC-081, QC-094, QC-088, QC-095 — CRITICAL/HIGH]
- **`$closure` no longer OOMs.** ~100 concepts exhausted 32 GB RSS and ran
  >17 min, wedging every other FHIR DB operation; ordinary 3–4 concept payloads
  returned wrong subsumption states on the balanced profile. [QC-261, QC-281]
- **`$expand` correctness**: `offset` no longer silently ignored;
  `compose.exclude` with a `filter` no longer silently ignored;
  `valueset-toocostly` no longer fires on terminal empty pages; filter mode
  returns the preferred term, not the matched synonym; the spec-canonical
  `?fhir_vs=isa/<sctid>` URL form accepted. [QC-241, QC-242, QC-316, QC-258,
  QC-411]
- **`$extract` correctness**: family history now detected (`is_family` was
  ignored); a negated mention can no longer REPLACE the affirmed mention of
  the same condition; the default-mode matrix no longer silently drops
  affirmed concepts; `format=annotated` returns 200 on GET and POST (CLI and
  MCP too). [QC-152, QC-183, QC-165, QC-151, QC-164]
- **Valueset optimizer**: dead exclude branch restored;
  `original_count`/`optimized_count`/`reduction` computed on the same basis
  (previously reported up to 98.85% "reduction" on valuesets whose expansion
  *grew*); the lexical tie-break no longer prefers hierarchy ancestors over
  the user's own input code; deep-subtree leaves no longer dropped silently.
  [QC-192, QC-194, QC-215, QC-212, QC-214]
- **Search**: empty/whitespace queries no longer return "results" in
  semantic/hybrid/canonical modes; unknown `resultTypes` returns a clean 400
  (was plaintext 500); `result_types` filter works on every path;
  `total_member_count` no longer 0 on every result; accented queries
  (Guillain-Barré, Chédiak) no longer silently return 0 hits. [QC-122,
  QC-123, QC-153, QC-143, QC-148, QC-329]
- **`$subsumes` correctness** (`codeA=parent, codeB=child` returned
  `not-subsumed`; `valueBoolean:true` coerced to the string `'True'` across
  all 5 FHIR POST handlers). [QC-071, QC-056]
- **FHIR XML/batch hardening**: control characters in client codes produced
  not-well-formed XML with HTTP 200; a batch entry with a non-string
  `request.method` crashed before the per-entry isolation boundary.
  [QC-300, QC-284]
- **CSV formula-injection sanitizer no longer permanently mutates data** (the
  leading `'` was baked into cells, so non-Excel consumers re-parsed a
  different value). [QC-373]
- **Schema-skew detection loud**: a version mismatch between the DB manifest
  and the package no longer silently disables the prepared patient-friendly
  path (LNC raises `NotImplementedError` / HTTP 501 with remediation text);
  `umls.*` views no longer bake the build-time catalog filename into their DDL
  (a copy/rename of a built DB broke them). [QC-435, QC-459]
- **Remote/local parity**: `patient_friendly` no longer silently forces
  `resolve_current` on the remote leg regardless of the caller's mode.
  [QC-495]
- **NER wrong-type resolution** (external QC report: 10,433 wrong-type
  extractions). `resolve_spans` now constrains each span's canonical search
  to its GLiNER label's anchor categories first (disorder → condition/symptom,
  therapeutic agent → medication/drug_class, lab test → lab, …) and retries
  unfiltered only when nothing clears the grade floor, so a constraint can
  never reduce recall. This stops diseases resolving to lab anchors
  (diabetes → LOINC LP128793-9) and drugs resolving to their drug-level
  LOINC (carbamazepine → LP16061-1). Ambiguous analytes (creatinine,
  potassium, glucose, …) labeled as drugs prefer lab anchors unless an
  administration context (give/administered/infusion/supplement/…) is
  present; a drug span adjacent to "level(s)/concentration" prefers lab
  (TDM), while plain drug mentions stay drugs. Population terms (adults,
  women, men, children, infants, elderly, neonates, males, females) are
  added to the NER false-positive blocklist (previously extracted as
  disorders). Explicit caller `result_types` remains a hard filter.
- **CR-031 (HIGH) — closure-accelerated walks returned `[]` for
  RXNORM/ATC/MSH.** `walk_closure_table()` dispatched to
  `mt4ds.walk_closure_limited` whenever the table existed and depth ≤ 5,
  with no per-source coverage probe — for a source with zero closure rows
  every ancestor walk silently returned `[]` (hierarchy-assisted mappings,
  prepared `walk` paths, and the SNOMED patient-friendly fallback) while
  `mt4ds.walk_edges` had the edges. The dispatch now probes per source
  (memoized `LIMIT 1`, warning on the miss) and falls back to the
  `walk_edges` BFS path, so answers are correct on ANY prepared DB
  regardless of build vintage. The build-side seed whitelist is also derived
  from `SOURCE_STRATEGIES` (was a hardcoded 6-source list excluding
  RXNORM/ATC/MSH), so the closure table gains those sources on the NEXT
  rebuild — a 0.9 rebuild that predates this change serves those sources via
  the BFS fallback (correct, unaccelerated); `PREPARED_SCHEMA_VERSION`
  intentionally stays 0.9 so an in-flight rebuild's output is not gated
  stale for this.
- **CR-032 (HIGH) — `$extract` POST now expresses the four `include*`
  booleans.** A POSTed `{name: "includeNegated", valueBoolean: true}` was
  silently dropped and defaulted to `false` with no 400 (POST was the only
  dead surface; GET and the CLI worked). The booleans are extracted through
  a parallel `valueBoolean` channel, and a wrong-typed scalar for them
  (`valueInteger`, `valueString`, …) now 400s naming `valueBoolean`
  (inverse of the QC-127 scalar contract; `valueBoolean: null` still means
  absent per QC-245).
- **CR-035 — `_EngineState` broad excepts narrowed.** The three remaining
  `except Exception: logger.debug` blocks (prepare-cache index DDL, SNOMED
  parent-link cache table/index) are now `except duckdb.Error` +
  `logger.warning`, so programming bugs propagate and degraded paths are
  operator-visible (EC-20 treatment).

### Known issues

- **Pre-existing environment failure**: `tests/test_fhir_conformance.py` —
  4 tests fail with pydantic `rest.0.url Extra inputs are not permitted`
  (`fhir.resources` library-version drift, not a code regression). Fix by
  pinning the `fhir.resources` version.
- **GLiNER model drift**: the default NER model
  (`knowledgator/gliner-bi-small-v2.0`) had its HF weights re-downloaded
  after the test baseline was captured, causing 3 environmental failures in
  `tests/test_extraction.py::TestFindTerms`. **Recommendation**: pin the model
  revision via `MEDTERM4DS_NER_MODEL` post-release.
- **Deferred (mitigated)**: executor fairness / head-of-line blocking on the
  single DB executor (QC-492/QC-410) — the 300 s default and `timeout=600`
  guidance mitigate the reported starvation timeline; architectural fix
  queued post-release.

### Upgrade notes

**REQUIRED — rebuild derived tables on every existing database** (prepared
schema 0.8 → 0.9). Without it you keep running on stale prepared tables with
known data defects (CPT wrong-display, missing RxNorm/MSH hierarchy, partial
ATC, retired SNOMED concepts in crosswalks), and LNC patient-friendly raises
`NotImplementedError` (HTTP 501 on the servers) on the version gate — loud by
design:

```bash
medterm4ds data prepare-derived --db data/umls_current.duckdb
# (= mt.prepare_umls_duckdb(db, replace=True); stamps manifest
#  prepared_schema_version=0.9)
medterm4ds data verify --db data/umls_current.duckdb   # no version mismatch
```

Expected effects: `mt4ds.walk_edges` gains RXNORM (~238 K isa edges), MSH
(~15 K), ATC → ~6,982, LNC +~284 K `class_of`; CPT 87143 lookup displays the
PT; `map('ICD10CM','E11.9')→SNOMED` returns no retired concepts; prepared
patient-friendly is re-enabled (restoring the ~0.8 s prepared path vs the
~6.5 s legacy fallback measured on the pre-rebuild database).

Also on upgrade: pair engine and server versions (the `/optimize` envelope
change breaks v0.0.1 engines against a v0.0.2 server); pass
`cache_indexes=True` to `mt.connect()` if you relied on temp indexes; fix any
code that passed URI-form sources or empty strings expecting success-shaped
nulls.

## [0.0.1] - 2026-07-14

### Breaking changes

- **Tuple convention unified to `(source, code)`.** `CodeRef.from_pair()` and
  `as_pair()` now use `(source, code)` order — same as the dataclass field order,
  same as the Terminology facade, same as FHIR Coding `{system, code}`. The
  legacy `(code, source)` convention that caused silent source/code swaps was
  removed. If you pass tuples to service functions, flip the order.

- **`$search` mode label simplified.** The FHIR `$search` response previously
  returned `"semantic-fallback"` in `expansion.search.mode` when hybrid search
  fell back to semantic-only. Now always returns the requested mode (`"hybrid"`).

### Added

- **FHIR R4 terminology-service spec compliance**: 18 spec chunks across
  terminology-service.html, codesystem.html, valueset.html, conceptmap.html,
  and per-operation definition pages — 2546 conformance probes, 4-personality
  QA rotation (SKEPTIC + HISTORIAN + EXPLORER + TERMINOLOGIST). All 18 chunks
  pass across all 4 personalities; 72 bugs found, 69 fixed during the run.
- **FHIR R4 batch endpoint** (`POST /fhir`) per §3.7 — submit a Bundle of
  operations in one HTTP round-trip with per-entry error isolation. CapabilityStatement
  now advertises `batch` + `transaction` in `rest[].interaction`.
- **XML response support** via `_format=xml` query param or
  `Accept: application/fhir+xml` header. New `engines/fhir/xml.py` serializer.
  CapabilityStatement advertises `format: ["json", "xml"]`.
- **CapabilityStatement** endpoint at `/fhir/metadata` (and `?mode=terminology`
  for TerminologyCapabilities) per FHIR R4 §3.2.1.0. Advertises supported systems
  via `capabilitystatement-supported-system` extension, canonical HL7
  OperationDefinition URIs, and per-resource interactions + search params.
- **Canonical `_canonical_system_uri` helper** in `engines.fhir.__init__` —
  single source of truth for the client-input-as-canonical URI drift pattern.
  Applied on every `_do_*` handler's Out `system` field.
- **Canonical `_equivalence` module** (`engines/fhir/equivalence.py`) — unifies
  the engine → R4 ConceptMapEquivalence translation across both the $translate
  HTTP surface and the ConceptMap export surface. Closed-enum membership
  assertion at module load applies uniformly to both.
- **Closed-enum registries** (`FHIR_R4_CONCEPT_MAP_EQUIVALENCE`,
  `FHIR_R4_FILTER_OPERATORS`) in `engines.fhir.__init__`, imported by both
  production code and tests — registry-as-contract pattern eliminates
  closed-enum drift.
- **`mt.connect()` auto-provisioning.** Omit `db_path` to trigger one-time
  setup: builds `lookup.duckdb` from UMLS RRF (~8 min), downloads derived
  artifacts from HF (~2 min), caches in `~/.medterm4ds/`.
- **Cache management:** `mt.cache_info()`, `mt.cache_versions()`, `mt.cache_clear()`.
- **Interactive setup wizard:** `python -m medterm4ds.setup`.
- **`/health` endpoint** on the FHIR server — pure async liveness probe (<5ms).
- **`$extract` endpoint** on the FHIR server — GLiNER NER + medspaCy ConText.
- **Request-timing middleware** on the FHIR server.
- **`FHIR_VS_MAX_DEPTH` env var** + canonical `valueset-toocostly` truncation extension.
- **`MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS` env var** — cap on `$extract` input.
- **Docker `HEALTHCHECK`** + OCI labels.
- **`scripts/rebuild_fhir_docker.sh`** — one-command rebuild + restart.
- **`LocalDuckDBEngine` mixin composition** — 2173-line god class → 9 focused mixins.
- **`get_descendants_bfs()` / `is_descendant()`** — O(nodes) BFS for hierarchy walks.
- **GLiNER NER model** (`knowledgator/gliner-bi-small-v2.0`, which superseded
  the earlier `E3-JSI/gliner-multi-med-ner-synthetic-v1` default later in the
  0.0.1 cycle) replaces d4data.
- **`.github/workflows/publish.yml`** — PyPI publish on tag push via trusted publishing.
- **Systemic `duckdb.Error` exception handler** — every per-operation `_do_*`
  handler now has a 503-OperationOutcome boundary for transient DB failures.

### Changed

- **`$subsumes` performance:** 5min+ timeout → ~750ms (BFS with early-exit).
- **`$expand?fhir_vs=isa` performance:** 5min+ timeout → <1s (layer-by-layer BFS).
- **`$search` consolidated** to `services.search.SearchService` (was duplicated).
- **NER model switched to GLiNER** — catches acronyms (T2DM, CKD) d4data missed.
- **Error messages sanitized** — control chars stripped, 256-char cap.
- **`duckdb` + `huggingface_hub`** now hard dependencies.
- **HCPCS canonical URI** corrected from
  `http://terminology.hl7.org/CodeSystem/hcpcs-Level-II` to
  `http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets` (old URI retained
  as alias for backwards compatibility).
- **`canonical_system_uri`** applied to all `$lookup`/`$validate-code`/
  `$translate`/`$expand` Out `system` fields — clients receive the canonical
  FHIR URI regardless of input alias form.
- **All FHIR routes funnel through `_fhir_response`** — uniform
  `application/fhir+json` / `application/fhir+xml` Content-Type on success
  and error paths.
- **`expansion.timestamp` and `CapabilityStatement.date`** now dynamic
  (were hardcoded stale literals).
- **CapabilityStatement.version** sourced from `medterm4ds.__version__`
  (was hardcoded).
- **`_PatientFriendlyCache`** now loads all 8 patient-friendly artifacts
  (cpt, cvx, hcpcs, icd10cm, icd10pcs, lnc, rxnorm, snomedct_us) — was 5.
- **`_all_systems_except`** now derives from `SYSTEM_TO_FHIR_URI` (was hardcoded).
- **`$subsumes` mixed-system check** normalizes through `canonical_system_uri`
  before comparing — accepts alias URIs (urn:oid:...) as same-system.

### Security

- CVX URL SSRF guard (https + cdc.gov allowlist).
- `$extract` input length cap (100K chars).
- `$expand` count validation tightened (POST: reject <1 and >1000; batch
  dispatcher now raises ValueError on invalid count instead of silently
  substituting default — was CR-006/CR-017).
- HF Spaces auth divergence documented in SECURITY.md.
- Patient-friendly cache parse failures now log at WARNING (was INFO) —
  output-degrading failures must be operator-visible (CR-004/CR-015).

### Fixed

- Silent truncation when `$expand` count=1 and root fills budget.
- `_expand_intensional` truncation flag was computed but never emitted.
- `ClosureTable.to_parameter_list` missing lock.
- `get_closure_manager()` singleton init race.
- Silent `except: pass` blocks → logged warnings (3 sites).
- `RemoteAPIEngine.get_code_relations` missing `limit` param (Protocol drift).
- MCP `extract` tool bypassing the single-worker executor.
- FHIR startup banner not appearing in `docker logs`.
- `_extract` `Bundle.entry.fullUrl` now uses `urn:uuid:<uuid4>` per FHIR R4
  §3.1.0.1.4 (was non-conformant relative `CodeSystem/<system>-<code>` form).
- `_load_bm25_indexes` exception narrowed to `(json.JSONDecodeError, OSError)`
  (was broad `Exception`).
- `_expand_intensional` docstring + comment spelling: `descendent-of` (Latin,
  per spec) — was common-English `descendant-of`.

### Earlier in 0.0.1 (Tier A/B/C refactor)

### Architecture refactor (Tier C)

### Security

- CVX URL SSRF guard (https + cdc.gov allowlist).
- `$extract` input length cap (100K chars).
- `$expand` count validation tightened (POST: reject <1 and >1000).
- HF Spaces auth divergence documented in SECURITY.md.

### Fixed

- Silent truncation when `$expand` count=1 and root fills budget.
- `_expand_intensional` truncation flag was computed but never emitted.
- `ClosureTable.to_parameter_list` missing lock.
- `get_closure_manager()` singleton init race.
- Silent `except: pass` blocks → logged warnings (3 sites).
- `RemoteAPIEngine.get_code_relations` missing `limit` param (Protocol drift).
- MCP `extract` tool bypassing the single-worker executor.
- FHIR startup banner not appearing in `docker logs`.

### Earlier in 0.0.1 (Tier A/B/C refactor, continued)

### Architecture refactor (Tier C)
- Split `engines/duckdb/engine.py` from 5,362 lines into 6 focused modules: `hierarchy.py`, `mappings.py`, `resolution.py`, `patient_friendly.py`, `indications.py`, plus a leaner `engine.py` (2,127 lines, -60%). No behavioral change; verified via full regression suite and chain-of-custody diff against pre-refactor output.
- Moved `drugs_for_indication` SQL out of the domain layer (`domains/terminology.py`) into `engines/duckdb/indications.py`. Domain layer now calls `engine.get_drugs_for_indication()` via protocol, eliminating `getattr(engine, "con")` protocol leakage.
- Consolidated duplicate constants: `_BROAD_CHV_NAMES`, `_BROAD_MEDLINEPLUS_NAMES` now imported from `sources.base` (was redefined in engine). Identity tests enforce the consolidation.
- Removed `engines/medterm_baseline/` parity adapter and all 4 parity/benchmark scripts. The fhir4px regression suite replaces the old medterm comparison.

### Regression test suite
- Added `tests/regression/` with 5 tiers (80 tests): curated clinical fixtures (15), build smoke + count pins (20), cross-deliverable consistency (3), per-record invariants (17), full content golden parity (17), TTY-pinned (7), drugs_for_indication parity (1). Runs against real `umls_current.duckdb` in ~12 min; hermetic CI unaffected (markers gate inclusion).
- Added `tests/regression/golden/` helpers for per-deliverable canonicalization (strip timestamps, sort unordered lists) and structured diff reporting.
- Added `pinned_meta.json` with exact record counts + SHA256 hashes + UMLS release pin for every fhir4px deliverable.

### Production fixes (Tier A)
- Fixed `atc.atc_name` non-determinism in `build_fhir4px_embedding_index.py` (214 medication records swapped names between runs). Added `atc_name` to the ROW_NUMBER ORDER BY.
- Reconciled RxNorm ingredient scope: added SCDC/SBDC/SBDF TTYs to `build_fhir4px_rxnorm_ingredients.py`. Mismatches vs embedding_index dropped from 13,797 to 0.
- Expanded SNOMED condition TUIs: added T033 (Finding) and T184 (Symptom) to `_CONDITION_TUIS`. Condition embedding grew 201K → 245K records.

### RxNorm TTY fix
- Fixed `_source_atom_order_sql` (missing RxNorm case) and `build_fhir4px_patient_friendly.py` (incomplete inline TTY priority). 11,410 RxNorm codes corrected from SY/TMSY/PSN to canonical TTYs (SBD/SCD/SCDG/etc.). Medication embedding grew from 124,540 to 135,469 (previously-hidden codes now correctly included).

### Security hardening (Tier B)
- Fixed zip-slip in `download_release(extract=True)`: validates each archive member stays inside `extract_dir`.
- Added HTTP body-size caps (50 MB) to all external HTTP responses (evidence.py, api/engine.py, data_setup.py) to prevent OOM from compromised endpoints.
- Sanitized `/health` endpoint: no longer leaks DB filesystem path.
- Added request-size caps (10k codes) to all API batch endpoints.
- Mitigated CSV formula injection: string values starting with `=/+/-/@` are prefixed with a single quote.
- Expanded openFDA Lucene escaping: all Lucene metacharacters escaped (was only `"`).
- Documented API/MCP exposure model in `SECURITY.md`: localhost-only multi-process sidecar (binds to `127.0.0.1` by default).

### Data deliverable updates
- Added `tty` field to `patient_friendly_rxnorm.json` entries (RxNorm term type code for downstream code-selection priority).
- Added `canonical_code`/`canonical_system` to all 8 `patient_friendly_*.json` files. SNOMED conditions resolve to shortest ICD-10 sharing CUI (e.g., 44054006 → E11). All other sources default to self.
- Updated `data-delivery-spec.md` to v3.1 with current counts, TTY distribution, and new field documentation.

- Added `scripts/filter_embedding_index.py` to filter an existing embedding index JSONL to a specific list of (source, code) pairs. Reads a CSV with `source` and `code` columns; useful for producing per-ValueSet indices on demand.
- Generated the Encounter Type ValueSet lookup and index: `valueset_2.16.840.1.113762.1.4.1267.23_patient_friendly.csv` (231 of 233 codes — 99.1% — patient-friendly name coverage) and `embedding_index_valueset_encounter_type.jsonl` (231 records filtered from the full index, 0.2 MB).
- Added T058 (Health Care Activity) to the SNOMED procedure TUI set in `scripts/build_embedding_index_full.py`. Adds 7.7K new SNOMED codes covering patient encounters, evaluations, and care-plan activities. Without this, 60 codes in the Encounter Type ValueSet were missing from the index. Full index grew from 623K to 631K records.
- Applied fhir4px MEDTERM4DS_FOLLOWUP_CHANGES to `scripts/build_embedding_index_full.py`:
  - ICD10PCS hierarchy entries are cleaned: the `@`-template format is flattened to `Imaging - Veins - Computerized Tomography ...`. Same for ICD10PCS synonyms — the HX atom's `@`-format string is replaced with its space-joined cleaned form. Verified: zero `@` symbols remain in any ICD10PCS hierarchy or synonym.
  - ICD10PCS root section names ("Imaging", "Medical and Surgical", "Radiation Therapy", etc.) are surfaced as priority synonyms — these are the patient-friendly bucket names per the spec.
  - LOINC CLASS abbreviation is replaced with a human-readable name in hierarchy (e.g., `MICRO` → `Microbiology`, `HEM/BC` → `Hematology`, `CHEM` → `Chemistry`). The readable name is also prepended as a priority synonym. Curated mapping for the top-30 CLASS values plus common vital-sign `.ATOM` classes.
  - Spec change #3 (LOINC parent group/panel concepts) was investigated but not implemented: PAR walks from LNC codes land on Metathesaurus part atoms (e.g., MTHU "Chemistry") which duplicate the CLASS info already surfaced. The LOINC group/panel structure (e.g., "Acylcarnitines" as a parent of specific acylcarnitine tests) is not in UMLS mrrel; implementing change #3 would require loading the LOINC source files (Group.csv / MultiAxialGroup.csv) into the DuckDB.
- Applied fhir4px MEDTERM4DS_INDEX_SPEC changes to `scripts/build_embedding_index_full.py`. The full index grows from 546K to 623K records and now produces per-category splits:
  - RXNORM TTY filter expanded to include BN, PIN, SCDC, SBDC, SBDF, BPCK, GPCK on top of IN/MIN/SCDG/SCD/SBD. Brand-name records (BN/SBDC/SBDF/BPCK/SBD) carry the **generic ingredient** as `friendly_name` via the resolver crosswalk — e.g., BN "Lastacaft" has friendly_name "Alcaftadine".
  - LOINC COMPONENT added as `vectors.synonyms[0]` for LNC records (sourced from `mrsat.ATN='LOINC_COMPONENT'`) and surfaced as a top-level `component` field.
  - Combination-drug individual ingredients added as priority synonyms for RXNORM MIN/SCD/SBD/SCDG/SCDC/SBDC/SBDF/BPCK/GPCK records (sourced from Table 2 decomposition). A query mentioning only one ingredient of a combination product can now match.
  - Added top-level `tty` field (also kept under `code.tty`).
  - Added `body_structure` category for SNOMED anatomy TUIs (T023/T024/T025/T026/T029/T030/T031). 40K SNOMED anatomy codes newly addressable.
  - Split the full index into per-category files alongside the main full index: `embedding_index_{condition,lab,medication,procedure,vaccine,body_structure}.jsonl`.
- Added `scripts/build_embedding_index_full.py` to produce `reports/fhir4px/embedding_index_full.jsonl` — the clinically-addressable companion to `embedding_index.jsonl`. Reads Table 1 (patient_friendly_names.csv) and emits one JSON record per addressable code: ICD10CM all 98K, ICD10PCS leaf-only ~80K (codes with no PAR/RB children), SNOMED TUI-filtered ~194K (condition/lab/procedure/medication/vaccine TUIs, plus CVX crosswalk), LNC TTY=LN only 104K, RXNORM TTY in {IN,MIN,SCDG,SCD,SBD} 46K, CPT/HCPCS/CVX all. Same 4-vector schema as the canonical index, plus a new `procedure` category for ICD10PCS/CPT/HCPCS/SNOMED procedures. ATC for SCD/SBD resolved via Table 2 decomposition (rxnorm_ingredient_decomposition.csv) since has_ingredient edges in this UMLS build don't directly link IN to SCD. 546K records, 361 MB, ~43s build.
- Added `scripts/load_mrconso_lat.py` to add a `lat` column to the existing `mrconso` table, populated from the LAT field in UMLS MRCONSO.RRF. One-time schema enrichment (~10s), idempotent. Result: 59.5% of atoms are ENG; the remaining 40% are SPA, POR, FRE, DUT, CZE, JPN, RUS, GER, ITA, POL, etc.
- Updated `scripts/build_embedding_index.py` to filter synonyms to `lat='ENG'`. Non-English atoms (MSHCZE, MSHRUS, LNC-ES-MX, SCTSPA, etc.) are no longer included in synonym vectors. Embedding index file size drops from 134 MB to 117 MB; synonym coverage 60.5% → 60.4% (negligible loss, since the dropped atoms were rarely in the top-K by source priority anyway).
- Added `scripts/build_embedding_index.py` to produce `reports/fhir4px/embedding_index.jsonl` from `canonical_codes.csv`. Each canonical code becomes one JSON record with 4 vector texts (technical, synonyms, friendly, hierarchy) plus metadata (semantic_types, ATC levels for medications, candidate_count, rule). Synonyms capped at K=8 per code, prioritized by source (MSH > MEDLINEPLUS > CHV > SNOMEDCT_US > ICD10CM > RXNORM > LNC). Hierarchy is source-specific 3-level ancestor chain. Output is 117 MB across 196,509 records.
- Added `scripts/load_mrsty.py` to load UMLS MRSTY.RRF into the local DuckDB as a `mrsty(cui, tui, sty)` table. One-time build, 3.9M rows, ~12s.
- Added TUI-driven SNOMED → target-system routing in `LocalDuckDBEngine._map_snomed_codes` and `_map_snomed_broader`. When MRSTY is loaded, SNOMED concept crosswalks are filtered by semantic type so a Pharmacologic Substance (T121) routes to RXNORM rather than LNC, a Disease (T047) routes to ICD10CM, a Lab Procedure (T059) routes to LNC, and a Therapeutic Procedure (T061) routes to CPT/ICD10PCS. CVX is also added as a target and preferred when a shared-CUI crosswalk exists (vaccines share generic substance TUIs and are detected via crosswalk presence). When MRSTY is absent, the legacy priority-only routing is preserved.
- Added `category=vaccine` to `canonical_codes.csv` for CVX codes plus a SNOMED-with-CVX-crosswalk fallback. Re-introduced SNOMED canonical candidates per category, gated by MRSTY TUI (e.g. SNOMED conditions only considered when no ICD10CM candidate exists). Added a `semantic_types` column to the enriched `patient_friendly_names.csv` carrying the comma-separated TUIs per CUI.
- Replaced the `drugs_for_indication` context-only stub with a UMLS Metathesaurus relationship walker that resolves medications for a condition via `may_treat` / `may_prevent` / `may_diagnose` / `contraindicated_with_disease` edges. The new uniform response shape drops `drug_name_context`, `reason`, and the `terminology_context_only` status; both success and fallback branches now return the same key set (`source`, `code`, `status`, `relationship_types`, `target_source`, `target_ttys`, `max_depth`, `include_product_groups`, `result_count`, `results`, `diagnosis_context`).
- Hardened the indication walker: substring-prone cycle detection now uses delimited membership; MIN (Multiple Ingredient) RxNorm targets get their real ingredient count instead of a default of 1; the recursive path uses a uniform ` -> ` delimiter so `path.split(" -> ")` yields clean segments; ORDER BY and dedup ROW_NUMBER tiebreakers were made deterministic.
- Added input validation for `drugs_for_indication`: empty `condition` and `code` without `source` (including `source=""`) now raise upfront instead of crashing deep in the call stack.
- Removed the unused `_rxnorm_product_group_expansions` helper.
- Stabilized patient-friendly naming around UMLS-only hierarchy traversal and removed synthetic hierarchy edge generation.
- Archived the old final-resolution materialization path; the live prepared runtime resolver is now the canonical patient-friendly path.
- Added smart title casing for patient-friendly `name` output while preserving `technical_name` source casing, clinical units, acronyms, mixed-case terms, and systematic chemical names.
- Added `--ignore-name-case` to the patient-friendly benchmark comparison so semantic regressions can be separated from display-case differences.
- Recorded the current all-source patient-friendly runtime baseline: 1,127,094 reviewed production codes in 3:47.17 wall time with `--memory-profile fast`.
- Refreshed CI compatibility by updating stale display expectations and lint issues.

### Initial codebase

- Added Medical Terminology for Data Science package identity and GPL-3.0-only license metadata.
- Added a notebook-first `Terminology` facade with `connect(...)`, `connect_remote(...)`, typed result methods, and DataFrame helpers.
- Added a local DuckDB terminology engine for low-memory local execution. `LocalLite*` names remain compatibility aliases for early pre-release users.
- Added exact lookup, hierarchy traversal, same-CUI mapping, and bounded broader/narrower mapping.
- Added patient-friendly name resolution for ICD-10, SNOMED CT, RxNorm, LOINC, CVX, CPT, and HCPCS.
- Added JSONL, CSV, and FHIR R4 ConceptMap outputs.
- Added source inventory, TTY inspection, and name search discovery tools.
- Added CLI, FastAPI, MCP, remote API engine, shared bulk exports, and DataFrame helpers.
- Added versioned public output schemas and real-data smoke scripts.
- Added executable notebook examples and notebook smoke testing against a synthetic DuckDB fixture.
- Added Hatch build/publish workflow and fresh-venv wheel install smoke testing.
- Added Docusaurus documentation with Python notebook quickstarts, terminology notes, UMLS licensing guidance, and release docs.
- Added MCP-compatible terminology, drug, and external evidence tool names. UMLS-backed tools return terminology results; external evidence tools return structured unavailable responses until data adapters are configured.
