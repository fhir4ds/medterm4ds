# medterm parity matrix

This matrix tracks the expected behavior alignment between the current
`medterm4ds` implementation and the legacy `/mnt/d/medterm` implementation.
The goal is semantic parity unless the new library deliberately fixes a bug,
adds provenance, or changes an output shape for notebook/API/MCP ergonomics.

## Patient-friendly names

| Legacy surface | medterm4ds surface | Parity target | Known allowed differences | Test status |
| --- | --- | --- | --- | --- |
| `functional.transforms.patient_friendly.get_patient_friendly_name` | `get_patient_friendly_name`, `Terminology.patient_friendly`, CLI/MCP/API patient-friendly tools | Same `name`, `friendly_source`, `match_type`, and `match_depth` for active codes; hierarchy fallback selects the closest acceptable MEDLINEPLUS/CHV frontier, with MEDLINEPLUS preferred over CHV only at the same depth | `matched_via` is structured in medterm4ds; obsolete/NDC inputs may resolve to current codes first | Real-data matrix run; CPT and SNOMED display differences reviewed below |
| `bulk.transforms.patient_friendly.get_patient_friendly_names` | `get_patient_friendly_names`, `iter_patient_friendly_bulk`, ConceptMap export | Same batch semantics and source-specific rules | medterm4ds preserves input order and can emit versioned schemas/FHIR; known old CPT/HCPCS key bug is not preserved | Synthetic parity plus source-by-source real-data matrix coverage |
| `bulk.transforms.patient_friendly_refactored._resolve_rxnorm` | `LocalDuckDBEngine._resolve_rxnorm` | RxNorm TTY topology traversal to SCDG, MIN, or IN using RxNav default paths | medterm4ds uses batch SQL path tables instead of whole-graph in-memory neighbor caches; includes the SBDC topology edge as a correction | Focused synthetic and TTY-stratified real-data parity coverage |
| LOINC component strategy | `LocalDuckDBEngine._resolve_loinc` | LPDN first-axis, then component MEDLINEPLUS/CHV, then LC common name, then SNOMED fallback where applicable | Tie-breaks must be documented if broader real-data parity differs | Source-by-source real-data matrix passes |
| CPT strategy | `LocalDuckDBEngine._resolve_cpt` | Exact MEDLINEPLUS/CHV, CPT parent walk, then HCPCS/ICD10CM/SNOMED crosswalk fallback | Old CPT/HCPCS replacement bug is not preserved; original display selection is deterministic where old SQL was order-sensitive and favors CPT consumer-facing `ETCF`/`ETCLIN` names over all-caps synonyms or long technical procedure text | Source-by-source real-data matrix has reviewed name-only display differences |
| CVX strategy | `LocalDuckDBEngine._resolve_cvx` | CDC vaccine group name when available, original name otherwise | medterm4ds lazily loads CDC groups by default and allows injected groups or `MEDTERM4DS_DISABLE_CVX_GROUPS=1` for offline use | Synthetic coverage plus source-by-source real-data matrix passes |
| SNOMED strategy | `LocalDuckDBEngine._resolve_snomed` | Explicit drug/product same-CUI routes may use RXNORM first; other targets use `ICD10CM > ICD10PCS > LNC > CPT > HCPCS`, with each target hierarchy walked before guarded direct SNOMED fallback | SNOMED top-level guard is broader and intentional; RxNorm routing is limited to explicit drug/product same-CUI targets; structured provenance is added; original display uses the SNOMED preferred term while preserving the fully specified name as `technical_name` | Source-by-source real-data matrix needs regeneration after the SNOMED/RxNorm target-route policy change |

## Lookup, resolve, mapping, hierarchy

| Legacy surface | medterm4ds surface | Parity target | Known allowed differences | Test status |
| --- | --- | --- | --- | --- |
| `TerminologySource.get_code_info` | `get_code_info`, `lookup_code`, `lookup_codes` | Active canonical atom lookup by code/source | medterm4ds can optionally resolve historical/obsolete/NDC inputs | Unit coverage exists |
| `TerminologySource.cross_reference` and `functional.transforms.crosswalk` | `get_code_mappings`, `cross_reference`, `map_codes` | Exact same-CUI mappings, broader/narrower hierarchy-assisted mappings where requested | medterm4ds adds `match_type`, `match_depth`, `matched_via`, and expanded CVX/CPT/HCPCS defaults | Synthetic coverage plus real-data smoke and mapping-quality sampling; broader clinical review remains data-quality work |
| `functional.transforms.hierarchy` | `get_code_relations`, parents/children/ancestors/descendants | Same hierarchy direction and depth semantics for active codes | medterm4ds returns typed rows rather than legacy `Tree` nodes | Unit coverage and real-data smoke exist |
| `functional.transforms.resolve` | `search_names`, `resolve_codes` | Search/resolve active terminology names and codes | medterm4ds separates name search from obsolete/NDC code resolution | Synthetic obsolete/NDC coverage plus real-data search smoke |
| No direct legacy equivalent | `optimize_codes` | Not a legacy parity item | New valueset-management feature | Unit coverage and notebook smoke exist |

## MCP tools

| Legacy MCP tool | medterm4ds tool | Parity target | Known allowed differences | Test status |
| --- | --- | --- | --- | --- |
| `discover` | `discover`, `sample_codes`, `source_stats`, `code_ttys`, `search_names` | Same source/code discovery intent | medterm4ds returns compact dictionaries instead of legacy `Tree` | Unit coverage plus MCP import smoke |
| `cross_reference` | `cross_reference`, `map_codes` | Same default targets for legacy sources and same mapped codes | medterm4ds additionally defaults CVX/CPT/HCPCS targets | Unit coverage plus mapping real-data smoke |
| `diagnosis_codes` | `diagnosis_codes` | Search ICD10CM and SNOMEDCT_US, optional hierarchy context | Output shape differs | Basic unit coverage exists |
| `lab_codes`, `lab_value_codes` | `lab_codes`, `lab_value_codes` | Search same source groups | Output shape differs | Basic unit coverage exists |
| `procedure_codes`, `hcpcs_drugs`, `vaccine_codes` | Same names | Search same source groups | medterm4ds includes compact metadata and optional hierarchy context | Basic unit coverage exists |
| `search_drug` | `search_drug` | RxNorm name search, equivalents, optional NDCs | Legacy RxNav approximate/spelling suggestions are not yet reproduced in UMLS-only mode | Basic compatibility coverage; external RxNav behavior deferred |
| `drugs_by_class` | `drugs_by_class` | Drug class workflow | Legacy RxClass membership is not reproduced by the UMLS-only wrapper | Compatibility wrapper exists; richer RxClass behavior deferred |
| `drugs_for_indication` | `drugs_for_indication`, `indication_search` | Indication workflow | Legacy RxClass `may_treat` differs from medterm4ds terminology context plus openFDA evidence | Compatibility wrapper exists; richer RxClass behavior deferred |
| `indication_search`, `fda_label_by_rxcui` | Same names | openFDA evidence lookup | medterm4ds returns normalized dict payloads | Unit coverage exists |
| `guideline_search`, `guideline_recommendations`, `guideline_fulltext`, `guidelines_for_code` | Same names | Guideline evidence workflow | medterm4ds currently uses PubMed/openFDA adapters with compact dicts | Unit coverage exists |

## Parity gates

Before publishing, the replacement-readiness gate should include:

1. Synthetic parity tests for each source-specific patient-friendly branch.
2. Stratified real-data patient-friendly parity against `/mnt/d/medterm`.
3. Semantic MCP parity for legacy tool names and default source groups.
4. Cross-reference parity for exact, broader, and narrower modes.
5. Real-data smoke tests for lookup, resolve, hierarchy, mapping, optimize, bulk, and ConceptMap exports.
6. A reviewed allowlist for intentional differences, including obsolete/NDC resolution, structured provenance, expanded smart defaults, output shape, and fixed legacy bugs. The maintained allowlist is [`intentional-differences-allowlist.md`](intentional-differences-allowlist.md).

Use `scripts/run_patient_friendly_parity_matrix.py` for the source-by-source
patient-friendly gate. It writes per-source reports plus an aggregate index
under `reports/quality/patient_friendly_parity/`.

## 0.0.1 validation snapshot

The latest expanded patient-friendly matrix was run on 2026-06-01 against
`/mnt/d/medterm4ds/data/umls_local.duckdb` and the dirty legacy tree at
`/mnt/d/medterm`.

| Source | Status | Result |
| --- | --- | --- |
| `ICD10CM` | pass | 50/50 matched |
| `ICD10PCS` | pass | 50/50 matched |
| `HCPCS` | pass | 50/50 matched |
| `RXNORM` | pass | 46/46 matched with TTY-stratified sampling |
| `LNC` | pass | 50/50 matched |
| `CVX` | pass | 50/50 matched |
| `CPT` | reviewed | 28/50 matched; mismatches are name-only and reflect deterministic CPT display selection |
| `SNOMEDCT_US` | reviewed | 47/50 matched; mismatches are name-only preferred-term choices |

The CPT mismatches are accepted for 0.0.1 because the old baseline selects from
unordered CPT atoms and often returns all-caps synonyms or long technical names.
medterm4ds uses a deterministic source-specific display order that favors
`ETCF`, `ETCLIN`, `PT`, then `SY`.

The SNOMED mismatches are accepted for 0.0.1 because medterm4ds displays the
preferred term and keeps the fully specified name as `technical_name`. This
avoids legacy ordering-dependent synonym/FN output while preserving the
technical display for audit.

Release gate smoke checks also passed for lookup, mapping, hierarchy,
discovery, CLI output formats, notebooks, bulk validation, mapping-quality CSV
generation, API import, MCP import, package build, fresh-venv wheel install, and
the Docusaurus website build.
