# Changelog

## Unreleased

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

## 0.0.1 - 2026-06-01

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
