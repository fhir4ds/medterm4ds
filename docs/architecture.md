# medterm4ds Architecture

`medterm4ds` is organized around one terminology core with multiple thin
interfaces. Bulk processing, API serving, MCP tools, and CLI commands should all
call the same service layer.

## Layers

```text
core/       Typed records, normalization, and configuration.
sources/    Source-specific preparation rules and policy metadata.
engines/    Execution backends and DuckDB prepared-schema support.
services/   Batch-first lookup, walk, crosswalk, selection, and workflows.
outputs/    Serialization and export helpers.
ds.py       Notebook/DataFrame-friendly wrappers over services.
domains/    Diagnosis, lab, procedure, drug, vaccine, and compatibility helpers.
apps/       CLI, API, and MCP adapters.
scripts/    Benchmarks, parity checks, and acceptance checks.
```

## Core Contracts

The core domain records are:

- `CodeRef`: normalized source/code input.
- `CodeInfo`: active UMLS atom metadata for exact code lookup.
- `CodeMapping`: source-to-target same-CUI mapping row.
- `CodeRelation`: one same-source hierarchy relationship row.
- `CodeResolution`: one input-to-effective-code resolution row for active,
  historical, obsolete, ambiguous, missing, and NDC inputs.
- `SourceStats`: code and atom counts for a terminology source.
- `NameSearchResult`: one active atom name search hit.
- `FriendlyNameResult`: one patient-friendly display result plus provenance.
- `ConceptMapRow`: source-to-target mapping row used by exports.
- `OptimizeResult` and `OptimizeRule`: valueset include/exclude compaction
  results.
- `Provenance` and `ProvenanceStep`: structured path showing how a result was
  selected.

Services depend on protocols, not concrete engines. `LocalDuckDBEngine`, the
remote `RemoteApiEngine`, or a test double should be able to implement the same
service contract.

Each public result model has a versioned schema exposed through
`get_output_schema(...)`. The schema field order is expected to match the
model's `to_dict()` output and is treated as a stable downstream contract within
the schema version.

## Execution Model

`LocalDuckDBEngine` is the default local engine. It keeps large terminology data
inside DuckDB and resolves codes in batches. The target runtime path queries
prepared `mt4ds` tables rather than repeatedly joining raw UMLS `mrconso`,
`mrrel`, and `mrsat` tables.

Patient-friendly naming has an additional scale requirement: one code, an
arbitrary list of codes, and a whole code system export must use the same
semantics and must have reasonable performance. For that workflow, prepared
walk/crosswalk tables are necessary but not sufficient. The target production
path reads materialized patient-friendly resolutions for the prepared UMLS
release and policy version, while trace/debug tools can inspect the candidate
and path rows used to build those resolutions.

`RemoteApiEngine` implements the same protocols by POSTing to the FastAPI
surface. Python code can therefore switch from local DuckDB execution to a
remote terminology process without changing service calls.

## Normalized Terminology Data

Raw UMLS files are build inputs. Runtime terminology services should use
normalized, same-shaped tables that hide source-specific graph mechanics.

The target local DuckDB layout is:

```text
umls.mrconso
umls.mrrel
umls.mrsat

mt4ds.atoms
mt4ds.best_atoms
mt4ds.hierarchy_edges
mt4ds.walk_edges
mt4ds.walk_closure_limited  # optional depth-limited acceleration over UMLS walk_edges
mt4ds.same_cui_edges        # build/source compatibility layer
mt4ds.crosswalk_edges       # canonical runtime crosswalk table
mt4ds.friendly_atoms
mt4ds.rxnorm_allowed_tty_edges
mt4ds.rxnorm_tty_paths
mt4ds.rxnorm_tty_path_steps
mt4ds.rxnorm_tty_edges
mt4ds.cvx_metadata
mt4ds.code_replacements
mt4ds.snomed_top_level_depth
mt4ds.patient_friendly_strategy
```

Source-specific behavior belongs in preparation, not scattered through runtime
workflows:

- ICD10CM, ICD10PCS, HCPCS, and LNC `REL='PAR'` hierarchy becomes normalized
  parent edges.
- SNOMED `isa` hierarchy and top-level guard metadata become normalized walk
  edges plus `mt4ds.snomed_top_level_depth`.
- `mt4ds.walk_closure_limited` may be materialized from `mt4ds.walk_edges` as a
  bounded acceleration table. It must contain only UMLS-derived walk edges and
  must not introduce prefix/range-inferred or otherwise synthetic hierarchy.
- RxNorm TTY topology becomes prepared TTY path and edge tables.
- CVX group metadata becomes lookup enrichment metadata.
- MEDLINEPLUS/CHV candidates become prepared friendly atom rows with broad-name
  flags.
- Patient-friendly candidates and final resolutions become materialized rows
  keyed by `(source, code, release, policy_version)` so runtime lookup is a
  join instead of live graph traversal.
- Patient-friendly policy changes must bump the policy version so stale
  materialized resolution rows are not silently reused.
- Patient-friendly hierarchy selection uses closest acceptable frontier first.
  MEDLINEPLUS is preferred over CHV only within the same frontier/depth.

The `sources/` package defines these source preparation rules and policy
metadata. It is not a public service layer. Public workflows live in
`services/`.

Prepared hierarchy edges must be derived from UMLS relationship data or an
explicit source-owned non-hierarchy topology such as RxNorm TTY adjacency. Do
not infer clinical hierarchy edges from code prefixes for patient-friendly
fallback. If UMLS does not provide a defensible path to a friendly candidate,
the selected result should remain the original/source display rather than jump
to an unrelated broad term.

## Primitive Services

The target service layer is built from reusable primitives:

- `lookup`: source/code to canonical atom metadata.
- `walk`: source graph traversal, including RxNorm TTY traversal as a
  source-specific walk.
- `crosswalk`: source-to-target mappings through same-CUI and bounded fallback.
- `select`: deterministic candidate ranking and frontier selection.

Prepared DuckDB implementations share low-level helpers in
`services.prepared_primitives`: preferred atom lookup, same-CUI crosswalk table
selection, temp-code tables, and optional `walk_closure_limited` use. Workflow
services should reuse those helpers rather than each re-implementing table
selection and hierarchy expansion.

Patient-friendly naming, optimize, ConceptMap export, MCP tools, CLI commands,
and notebook helpers should compose these primitives. Patient-friendly naming
should not contain raw source graph mechanics.

Patient-friendly naming is the most complex workflow and should be treated as a
policy layer over the primitives:

```text
lookup / walk / crosswalk / friendly atoms
  -> patient-friendly candidate materialization
  -> shared filtering and ranking
  -> patient-friendly resolution materialization
  -> runtime lookup by source/code
```

Source-specific patient-friendly policy stays explicit:

- ICD10CM, ICD10PCS, LNC, CPT, and HCPCS walk their source hierarchy first,
  then fallback crosswalk to SNOMEDCT_US and walk SNOMED only under guarded
  policy.
- SNOMEDCT_US may route drug/product concepts with explicit same-CUI RxNorm
  targets through the RxNorm patient-friendly strategy. Other SNOMED targets
  route through ICD10CM, ICD10PCS, LNC, CPT, and HCPCS, walk those target source
  hierarchies, and only then use target-to-SNOMED or direct guarded SNOMED
  fallback.
- RxNorm remains source-native for patient-friendly output and uses explicit
  TTY topology rather than MEDLINEPLUS/CHV hierarchy selection.
- If no defensible friendly candidate exists, patient-friendly returns the
  source/original display instead of jumping to an unrelated broad term.
- Patient-friendly `name` is display-normalized with conservative title casing
  after selection. `technical_name` preserves source/original casing for audit.

The primitive services still need to be fast and reusable on their own. They
power hierarchy APIs, mapping, optimize, ConceptMap generation, and patient-
friendly exports. The final patient-friendly API uses the live prepared runtime
resolver as the canonical path. On 2026-06-08, that resolver processed
1,127,094 reviewed production codes in 3:45.57 wall time with
`memory-profile fast`. The former final-resolution materialization path was
archived because it was not validated against current semantics and did not
complete within the expected performance envelope.

Source-to-source mapping is exact same-CUI by default. Broader/narrower mapping
is opt-in through bounded hierarchy traversal so high-volume exports do not
silently expand into noisy mappings.

Code resolution is a separate step from lookup and mapping. Normal current-code
workflows can keep `active_only`, while historical data workflows can resolve
obsolete or suppressed inputs to current replacements when the source data makes
that appropriate. NDC inputs are always normalized and resolved through RxNorm
`MRSAT` NDC attributes before downstream RxNorm workflows.

Memory behavior is controlled through `LocalDuckDBConfig` and named profiles:

- `fast`: more memory, higher throughput.
- `balanced`: default for typical local runs.
- `low`: one DuckDB thread and smaller query chunks for constrained machines.

DuckDB's memory limit is not a strict cap on total process RSS. Python overhead
and some DuckDB allocations can exceed the configured limit.

## Services

Current services:

- `get_code_info(...)`
- `get_code_infos(...)`
- `get_code_info_prepared(...)`
- `get_code_infos_prepared(...)`
- `resolve_codes(...)`
- `get_source_stats(...)`
- `sample_source_codes(...)`
- `get_code_ttys(...)`
- `search_names(...)`
- `get_code_mappings(...)`
- `get_code_relations(...)`
- `get_parents(...)`
- `get_children(...)`
- `get_ancestors(...)`
- `get_descendants(...)`
- `get_patient_friendly_names(...)`
- `get_concept_map(...)`
- `iter_concept_map(...)`
- `optimize_codes(...)`
- `get_crosswalk_mappings(...)` -- cross-source mapping over prepared tables
- source inventory helpers for DuckDB-backed code lists

Prepared-table services (over `mt4ds.*` normalized tables):

- `prepared_primitives.*` -- shared prepared lookup/walk/crosswalk helpers
- `rxnorm_tty_walk.get_rxnorm_patient_friendly(...)` -- RxNorm TTY path traversal
- `patient_friendly_prepared.get_non_rxnorm_patient_friendly(...)` -- non-RxNorm friendly
- `crosswalk_prepared.get_crosswalk_mappings(...)` -- prepared crosswalk
- `walk.get_parents_prepared(...)` / `walk.get_ancestors_prepared(...)` -- prepared hierarchy
- `selection.rank_candidates(...)` / `selection.select_frontier(...)` -- candidate ranking

These functions are batch-first. Single-code workflows should call the same
batch contract with one code.

Future/refactored services should preserve these public entry points while
moving internals to normalized primitives. Public models and output schemas
remain stable unless deliberately versioned.

`medterm4ds.ds` wraps these services for pandas or Polars use. DataFrame helpers
must not add terminology rules; they convert service outputs to tabular records
and preserve the same output schemas.

`outputs.render` owns compact ASCII table/tree rendering. Service and engine
layers should continue returning typed records or dictionaries; CLI/MCP adapters
decide when to render text for human/tool compactness.

`domains/` provides thin workflow names for common clinical use cases and MCP
compatibility. UMLS-backed tools must delegate to lookup, search, mapping, or
hierarchy services. External evidence tools should remain explicit adapter
boundaries. The current adapters wrap openFDA drug labels and PubMed
E-utilities with injectable HTTP clients, so tests and future deployments can
swap transport/authentication without changing the terminology service layer.
External service failures return structured error payloads at the domain/MCP
edge rather than surfacing as terminology-engine failures.

## Bulk Is Not A Separate Terminology Layer

Bulk is treated as an execution/output mode, not as a separate transform tree.
The CLI streams source inventory through the same services and writes JSONL,
CSV, or FHIR output. Resume/checkpoint behavior lives in `outputs/` and the CLI,
not in terminology rules.

`services.bulk` provides shared batch iterators for lookup, mapping, hierarchy,
and patient-friendly workflows. CLI bulk commands should compose these iterators
with inventory streaming and checkpointed writers rather than creating separate
transform implementations.

Bulk patient-friendly is a lookup/export mode over the canonical live prepared
resolver. Source-wide exports should stream source inventory through shared
bulk iterators and write incrementally. If repeated static serving later needs
materialization, build it from runtime resolver output or shared SQL relation
builders so materialized and runtime semantics cannot drift.

Real-data validation follows the same rule. `scripts/run_bulk_validation.py`
streams source inventories through the shared bulk iterators, while
`scripts/review_mapping_quality.py` samples source-to-target mappings and flags
rows that need domain review. It writes a compact JSON summary and an optional
CSV with one flagged mapping per row for spreadsheet review. These scripts are
quality gates around the public services, not an alternate mapping engine.

Data setup lives in `services.data_setup` and the CLI `data` namespace. It
downloads UTS Metathesaurus Full Subset release artifacts, builds the compact
DuckDB tables from flat RRF files, `.RRF.gz` files, or UMLS `.nlm` release
archives, prepares the `mt4ds` runtime schema, and verifies required
tables/source counts. Runtime terminology services should not know how files
were downloaded or built.

Database role must be explicit in reports and release artifacts:

- `/mnt/d/medterm/data/umls_local.duckdb`: legacy 2025AB parity fixture,
  read-only.
- `/mnt/d/medterm4ds/data/umls_current.duckdb`: current medterm4ds production
  candidate after the new prepared schema is implemented.
- `/mnt/d/medterm4ds/data/umls_2025ab.duckdb`: optional medterm4ds-built 2025AB
  algorithm fixture.
- `/mnt/d/medterm4ds/data/umls_local.duckdb`: historical artifact unless
  explicitly promoted by review.

Reports should include DB path, DB role, UMLS release, and prepared schema
version.

## Interfaces

`apps/cli.py`, `apps/api.py`, and `apps/mcp.py` are intentionally thin:

- CLI handles arguments, inventory, output format, and resume.
- API owns one configured DuckDB engine per process and exposes service
  endpoints.
- MCP owns one configured DuckDB engine per process and registers structured
  tools with optional compact ASCII output.

None of these adapters should implement terminology rules.

## Compatibility

`engines/medterm_baseline` is a comparison adapter for the dirty
`/mnt/d/medterm` working tree. It is not a production engine. The parity script
uses it to compare semantic fields and record known old-medterm failures.

## Adding New Capabilities

When adding lookup, crosswalk, hierarchy/discover, drug, lab, or procedure
features:

1. Add or extend core result models only if the service needs a stable record.
2. Add service functions that operate on batches.
3. Add or update source preparation rules in `sources/` when code-system
   specific behavior is required.
4. Implement the engine method in `LocalDuckDBEngine` or a new engine.
5. Add output helpers only if there is a new export shape.
6. Expose the behavior through CLI/API/MCP as adapters.

Avoid duplicating terminology logic in `apps/`, `outputs/`, or scripts.

For the detailed target requirements around lookup, source-specific hierarchy
walking, crosswalk composition, RxNorm TTY topology, patient-friendly naming,
prepared SQL tables, and performance constraints, see
[`terminology-architecture-requirements.md`](terminology-architecture-requirements.md).

For the implementation phases, review gates, archive policy, database role
policy, tests, and acceptance criteria, see
[`plans/terminology-normalization-implementation-plan.md`](plans/terminology-normalization-implementation-plan.md).
