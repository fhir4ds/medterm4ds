# Changelog

All notable changes to medterm4ds are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_No unreleased changes yet. v0.0.1 shipped on 2026-07-14; subsequent work tracks
here until the next tag._

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
- **GLiNER NER model** (`E3-JSI/gliner-multi-med-ner-synthetic-v1`) replaces d4data.
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

### Earlier in 0.0.2 (Tier A/B/C refactor)

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
