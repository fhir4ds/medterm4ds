# Terminology Normalization Implementation Plan

This plan describes how to refactor `medterm4ds` terminology logic around
normalized lookup, walk, crosswalk, and selection primitives. It is the
implementation companion to
[`../terminology-architecture-requirements.md`](../terminology-architecture-requirements.md).
It should be read together with [`../architecture.md`](../architecture.md),
which describes the package layering and public service boundaries.

The plan is intentionally explicit. Each section must be independently reviewed.
If any section does not meet 100% of its acceptance criteria, it returns to
development. No section is considered complete by partial credit.

## Background

The legacy `/mnt/d/medterm` implementation grew multiple execution paths:

- local mode
- API mode
- bulk transforms
- CLI
- MCP tools
- patient-friendly transforms embedded with source-specific logic

`medterm4ds` has already improved the public surface, but some internals still
mirror the same complexity. Patient-friendly naming in particular contains too
much source-specific graph logic directly in the engine.

The current problems are:

1. Runtime services sometimes query raw UMLS tables directly.
2. Patient-friendly logic embeds source-specific walking and crosswalking.
3. RxNorm TTY traversal was broadened to support suppressed intermediates, but
   the SQL path can explode when run against some databases.
4. Full benchmark runs can obscure source-specific issues.
5. UMLS release and loader differences can be confused with algorithm
   regressions.
6. Bulk, API, CLI, MCP, and notebooks must all use the same terminology rules.
7. Prepared traversal tables alone are not enough for patient-friendly scale.
   Runtime patient-friendly must be able to answer one code or an entire code
   system without rediscovering candidates every time.

The target architecture is:

```text
raw UMLS tables
  -> normalized mt4ds prepared tables/views
  -> lookup / walk / crosswalk / select primitives
  -> workflows such as patient_friendly, optimize, ConceptMap
  -> CLI/API/MCP/notebook adapters
```

## Decisions Already Made

These are treated as settled unless a later review explicitly reopens them.

1. Use `umls` schema for raw UMLS input tables:
   - `umls.mrconso`
   - `umls.mrrel`
   - `umls.mrsat`
2. Use `mt4ds` schema for prepared runtime tables/views.
3. Source-specific graph rules are normalized during table preparation.
4. Runtime services should query same-shaped `mt4ds` tables/views.
5. RxNorm TTY traversal is a source-specific flavor of hierarchy walking.
6. CVX group/display behavior is a source-specific lookup/enrichment strategy.
7. Patient-friendly naming is workflow composition, not a source graph engine.
8. Single-code and many-code calls must use the same batch-first path.
9. Full benchmark runs are not the first debugging tool. Start with focused
   source-specific cases, then run broader reports.
10. The old bulk file
    `/mnt/d/medterm/src/medterm/bulk/transforms/patient_friendly_refactored.py`
    is a behavioral reference, not a production dependency.
11. The corrected UMLS download default is Metathesaurus Full Subset.
12. UMLS release drift must be separated from algorithm parity.
13. Patient-friendly runtime/export should read materialized resolution rows
    keyed by source/code/release/policy version.
14. The patient-friendly candidate-generation pipeline must preserve useful
    source-specific policy from existing implementations while removing
    non-UMLS inferred hierarchy jumps.

## Target Directory Structure

Create or converge toward this structure:

```text
src/medterm4ds/
  engines/
    duckdb/
      engine.py
      prepared.py
      queries/
        lookup.sql
        walk.sql
        crosswalk.sql
        patient_friendly.sql
        rxnorm_tty_walk.sql

  sources/
    __init__.py
    base.py
    generic.py
    rxnorm.py
    cvx.py
    snomed.py
    icd.py
    loinc.py
    cpt_hcpcs.py

  services/
    lookup.py
    walk.py
    crosswalk.py
    selection.py
    patient_friendly.py
    optimize.py
    bulk.py
```

Rules:

1. `sources/` defines source-specific table-building rules and policy metadata.
2. `services/` defines public workflows and must not embed raw UMLS mechanics.
3. `engines/duckdb/prepared.py` owns DuckDB schema/table/view preparation.
4. `apps/`, `domains/`, `outputs/`, and scripts must not contain terminology
   rules.

## Database Usage Policy

Database purpose must be explicit in every script, report, and test. Do not use
an unlabeled `umls_local.duckdb` path in review artifacts.

### Database Roles

| Role | Preferred path | Purpose | Notes |
| --- | --- | --- | --- |
| Legacy parity fixture | `/mnt/d/medterm/data/umls_local.duckdb` | Compare medterm4ds algorithms against old medterm behavior on 2025AB-era data. | Read-only. Do not modify. Do not prepare new tables inside it during this refactor. |
| Legacy raw archive | `/mnt/d/medterm/data/umls-2025AB-metathesaurus-full.zip` | Rebuild 2025AB if loader parity is being tested. | Use only for controlled loader/build tests. |
| Current medterm4ds production candidate | `/mnt/d/medterm4ds/data/umls_current.duckdb` | Main current-release DB after the new prepared schema is implemented. | Built from latest approved Metathesaurus Full Subset release. |
| medterm4ds 2025AB algorithm fixture | `/mnt/d/medterm4ds/data/umls_2025ab.duckdb` | Optional medterm4ds-built 2025AB DB for algorithm-vs-loader isolation. | Build only after the new build path is fixed. |
| Existing medterm4ds DB | `/mnt/d/medterm4ds/data/umls_local.duckdb` | Historical/current work artifact. | Treat as archived input unless explicitly promoted after review. |
| Temporary build DB | `/tmp/medterm4ds_build_*.duckdb` | Stage large builds away from WSL mount I/O. | Copy to `data/` only after verification passes. |

### Database Rules

1. Every generated report must include:
   - DB path
   - UMLS release when known
   - prepared schema version
   - whether the DB is legacy, current, or temporary
2. Parity reports must say whether they use:
   - legacy 2025AB medterm DB
   - medterm4ds-built 2025AB DB
   - medterm4ds current-release DB
3. Do not run full patient-friendly benchmarks against an ambiguous DB path.
4. Do not modify `/mnt/d/medterm/data/umls_local.duckdb`.
5. Do not overwrite `/mnt/d/medterm4ds/data/umls_local.duckdb` during the
   refactor. Use named DBs instead.
6. Archive partial DBs and old reports before new reports are generated.
7. Data setup scripts should default to named output paths, not ambiguous
   `umls_local.duckdb`, once this plan is implemented.

## Review Protocol

Every phase has a developer and an independent reviewer.

### Phase Rehydration

Before starting any phase, the developer must rehydrate on the current design
documents:

1. Read [`../architecture.md`](../architecture.md).
2. Read [`../terminology-architecture-requirements.md`](../terminology-architecture-requirements.md).
3. Read this implementation plan.
4. Read the review log and implementation notes for all prior phases.
5. Check whether any prior phase returned to development and whether the
   current phase depends on it.

The developer must include a short note in the phase implementation summary:

```text
Rehydration complete:
- architecture.md reviewed: yes/no
- terminology-architecture-requirements.md reviewed: yes/no
- implementation plan reviewed: yes/no
- prior review logs checked: yes/no
- open return_to_dev dependencies: none | ...
```

Developer responsibilities:

1. Implement only the phase scope.
2. Run required tests.
3. Produce a short implementation note with:
   - files changed
   - decisions made
   - test output
   - known limitations
4. Do not mark the phase complete.

Independent reviewer responsibilities:

1. Read the requirements document and the implementation diff.
2. Read [`../architecture.md`](../architecture.md) and confirm the phase still
   respects package layering and public service boundaries.
3. Validate every acceptance criterion.
4. Run or inspect the required tests.
5. Check for hidden source-specific logic in runtime workflows.
6. Check performance and query-shape risks where relevant.
7. Mark the phase:
   - `pass`
   - `return_to_dev`

Return-to-dev rule:

```text
If any acceptance criterion fails, is unverified, or is ambiguous,
the section returns to development.
```

Re-review rule:

```text
After fixes, the reviewer re-reviews the failed section and every downstream
section that could be affected by the change.
```

No release rule:

```text
No release preparation starts while any phase has unresolved return_to_dev
status.
```

Review log template:

```text
Phase:
Reviewer:
Date:
Status: pass | return_to_dev
Architecture docs reviewed:
Prior phase logs reviewed:
Criteria reviewed:
Tests run:
Findings:
Required fixes:
Downstream sections requiring re-review:
```

## Phase 0: Baseline And Safety

### Purpose

Create a clean baseline for the refactor and prevent more ambiguity between
data, algorithm, and performance issues.

### Implementation Tasks

1. Record current git status.
2. Create archive directories:

```text
archive/refactor-prep/
archive/refactor-prep/reports/
archive/refactor-prep/scripts/
archive/refactor-prep/data-notes/
```

3. Move or copy confusing current artifacts into the archive before new work:
   - partial benchmark CSVs that do not represent a completed reviewed run
   - temporary comparison scripts not intended as permanent tools
   - notes about failed/stalled DB builds
   - stale partial DB files, only if they are not the current reviewed DB
4. Do not delete useful historical artifacts until they are archived or named in
   the baseline report.
5. Record current available databases:
   - `/mnt/d/medterm/data/umls_local.duckdb`
   - `/mnt/d/medterm4ds/data/umls_local.duckdb`
   - `/mnt/d/medterm4ds/data/umls_2025ab.duckdb`, if present
   - `/mnt/d/medterm4ds/data/umls_current.duckdb`, if present
6. Classify each DB by role:
   - legacy parity fixture
   - current medterm4ds artifact
   - medterm4ds-built parity fixture
   - temporary/partial build
   - unknown
7. Record UMLS release for each DB when discoverable.
8. Stop any lingering DuckDB benchmark/build processes.
9. Create a review log directory:

```text
reports/reviews/
```

10. Add a machine-readable baseline report:

```text
reports/reviews/phase0_baseline.json
```

Minimum fields:

```json
{
  "generated_at": "...",
  "git_status": "...",
  "databases": [
    {
      "path": "...",
      "exists": true,
      "tables": ["..."],
      "row_counts": {
        "mrconso": 0,
        "mrrel": 0,
        "mrsat": 0
      },
      "release": "unknown"
    }
  ],
  "running_processes": [],
  "archived_artifacts": [
    {
      "from": "...",
      "to": "...",
      "reason": "partial benchmark | stale script | partial db | notes"
    }
  ],
  "database_usage_policy": {
    "legacy_parity_fixture": "/mnt/d/medterm/data/umls_local.duckdb",
    "current_candidate": "/mnt/d/medterm4ds/data/umls_current.duckdb",
    "medterm4ds_2025ab_fixture": "/mnt/d/medterm4ds/data/umls_2025ab.duckdb"
  }
}
```

### Tests

1. Confirm no active `compare_patient_friendly_benchmark.py` process.
2. Confirm both DB paths either exist or are explicitly marked missing.
3. Confirm `git status --short` is captured.
4. Confirm archived artifacts are not used by new scripts by default.
5. Confirm no production/review report points to an ambiguous DB role.

### Acceptance Criteria

1. Baseline JSON exists.
2. No lingering DuckDB build/compare processes remain.
3. DB row counts are captured without expensive source-wide joins.
4. The report clearly distinguishes old medterm 2025AB DB from medterm4ds DB.
5. Partial or confusing reports/scripts are archived or explicitly documented as
   retained.
6. Database role policy is captured and unambiguous.
7. Independent reviewer confirms baseline is sufficient for later comparisons.

### Independent Review

The reviewer checks that phase 0 does not change runtime code. Any missing DB
metadata, ambiguous DB role, unarchived confusing artifact, or lingering process
returns this phase to development.

## Phase 1: Prepared Schema Infrastructure

### Purpose

Create the `umls` and `mt4ds` schema infrastructure and make preparation
idempotent.

### Implementation Tasks

1. Add `src/medterm4ds/engines/duckdb/prepared.py`.
2. Add public functions:

```python
def prepare_mt4ds_schema(con, *, replace: bool = False) -> dict[str, object]:
    ...

def verify_mt4ds_schema(con) -> dict[str, object]:
    ...
```

3. Create schemas:

```sql
CREATE SCHEMA IF NOT EXISTS umls;
CREATE SCHEMA IF NOT EXISTS mt4ds;
```

4. Support both DB layouts:
   - current tables in `main.mrconso`, `main.mrrel`, `main.mrsat`
   - future raw tables in `umls.mrconso`, `umls.mrrel`, `umls.mrsat`
5. If raw tables live in `main`, create compatibility views or copy them into
   `umls` according to a documented mode.
6. Do not break current `LocalDuckDBEngine` while compatibility views exist.
7. Add a preparation manifest table:

```sql
CREATE TABLE IF NOT EXISTS mt4ds.prepare_manifest (
  key VARCHAR PRIMARY KEY,
  value VARCHAR,
  updated_at TIMESTAMP
);
```

8. Record:
   - package version
   - UMLS release when known
   - prepared schema version
   - source table row counts
   - prepared table row counts

### Code Snippet

```python
PREPARED_SCHEMA_VERSION = "0.8"

def prepare_mt4ds_schema(con, *, replace: bool = False) -> dict[str, object]:
    con.execute("CREATE SCHEMA IF NOT EXISTS umls")
    con.execute("CREATE SCHEMA IF NOT EXISTS mt4ds")
    _ensure_raw_umls_views(con)
    report = {}
    report["atoms"] = prepare_atoms(con, replace=replace)
    report["friendly_atoms"] = prepare_friendly_atoms(con, replace=replace)
    report["hierarchy_edges"] = prepare_hierarchy_edges(con, replace=replace)
    report["rxnorm"] = prepare_rxnorm_tty(con, replace=replace)
    _write_manifest(con, report)
    return report
```

### Tests

1. Synthetic DuckDB with `main.mrconso`, `main.mrrel`, `main.mrsat`.
2. Synthetic DuckDB with `umls.mrconso`, `umls.mrrel`, `umls.mrsat`.
3. `replace=False` does not destroy existing prepared tables.
4. `replace=True` rebuilds prepared tables.
5. `verify_mt4ds_schema` fails with useful messages when required raw tables
   are missing.

### Acceptance Criteria

1. `prepare_mt4ds_schema` is idempotent.
2. `verify_mt4ds_schema` returns clear table/status metadata.
3. Existing tests using `main.mrconso` still pass.
4. No public service is required to know whether raw data is in `main` or
   `umls`.
5. `verify_mt4ds_schema` reports every required `mt4ds` runtime table with
   existence and row-count metadata, and flags missing prepared tables as
   errors.
6. Reviewer confirms the schema code contains no patient-friendly business
   logic.

### Independent Review

The reviewer checks idempotency, compatibility, and separation of concerns. Any
runtime rule embedded in schema plumbing returns this phase to development.

## Phase 2: Source Strategy Modules

### Purpose

Move source-specific preparation and policy metadata into `sources/`.

### Implementation Tasks

1. Add `src/medterm4ds/sources/base.py` with a protocol:

```python
class SourceStrategy(Protocol):
    source: str

    def atom_rank_sql(self) -> str: ...
    def hierarchy_edge_sql(self) -> str | None: ...
    def friendly_strategy_rows(self) -> list[dict[str, object]]: ...
```

2. Add source modules:
   - `generic.py`
   - `rxnorm.py`
   - `cvx.py`
   - `snomed.py`
   - `icd.py`
   - `loinc.py`
   - `cpt_hcpcs.py`
3. Define source registry:

```python
SOURCE_STRATEGIES = {
    "RXNORM": RxNormStrategy(),
    "SNOMEDCT_US": SnomedStrategy(),
    "ICD10CM": Icd10Strategy("ICD10CM"),
    ...
}
```

4. Encode source hierarchy rules:

| Source | Rule |
| --- | --- |
| `SNOMEDCT_US` | `RELA='isa'`, normalized child to parent, with `PAR`/`CHD` handling where applicable. |
| `ICD10CM` | `REL='PAR'`, `RELA IS NULL`, reverse `CHD`. |
| `ICD10PCS` | `REL='PAR'`, `RELA IS NULL`, reverse `CHD`. |
| `HCPCS` | `REL='PAR'`, `RELA IS NULL`, reverse `CHD`. |
| `LNC` | `REL='PAR'`, `RELA IS NULL`, reverse `CHD`. |
| `CPT` | `RELA='isa'`, normalized child to parent. |
| `ATC` | `RELA='isa'`, normalized child to parent. |
| `MSH` | `RELA='isa'`, normalized child to parent. |
| `RXNORM` | TTY topology, not raw `isa`, for patient-friendly walk. |

5. Encode RxNorm TTY topology exactly:

```python
RXNORM_TTY_TOPOLOGY = {
    "SCD": ("SBD", "SCDC", "SCDF", "SCDG", "GPCK", "DF", "MIN"),
    "SBD": ("BN", "SCD", "SBDF", "SBDG", "SBDC", "BPCK", "SCDC"),
    "SCDC": ("SCD", "SBD", "IN", "PIN"),
    "SCDF": ("SCD", "SBDF"),
    "SBDC": ("SBD", "SBDF", "IN"),
    "SBDF": ("SBD", "SCDF"),
    "SCDG": ("SCD", "SBDG", "DFG"),
    "SBDG": ("SBD", "SCDG"),
    "GPCK": ("SCD", "BPCK"),
    "BPCK": ("SBD", "GPCK"),
    "MIN": ("SCD", "IN"),
    "IN": ("SCDC", "MIN", "BN"),
    "PIN": ("SCDC",),
    "BN": ("SBD", "IN"),
    "DF": ("SCD",),
    "DFG": ("SCDG",),
}

RXNORM_GROUP_TARGET_TTYS = {
    "SCD", "SBD", "SBDC", "SCDF", "SBDF",
    "GPCK", "BPCK", "SBDG", "SCDG", "DFG",
}
```

### Tests

1. Unit test registry contains every supported source.
2. Unit test RxNorm topology exactly matches expected paths.
3. Unit test shortest path generation:
   - `SBD -> SCDG` returns `SBD, SCD, SCDG`.
   - `SBDC -> IN` can return `SBDC, IN` or reviewed legacy-compatible path
     if data requires `SBDC, BN, IN`.
   - `SCD -> SCDG` returns `SCD, SCDG`.
4. Unit test hierarchy SQL fragments for each source.
5. Unit test strategy rows for patient-friendly phases.

### Acceptance Criteria

1. Source rules are centralized in `sources/`.
2. No runtime patient-friendly code contains raw source hierarchy constants.
3. RxNorm TTY topology is explicit and tested.
4. Group-target TTY set is explicit and tested.
5. Reviewer confirms all source rules from the requirements doc are represented.

### Independent Review

The reviewer compares source modules against the requirements table. Any missing
source, missing edge, vague rule, or untested topology returns this phase to
development.

## Phase 3: Prepared Runtime Tables

### Purpose

Build normalized `mt4ds` runtime tables from raw UMLS.

### Tables To Implement

1. `mt4ds.atoms`
2. `mt4ds.best_atoms`
3. `mt4ds.hierarchy_edges`
4. `mt4ds.walk_edges`
5. `mt4ds.same_cui_edges`
6. `mt4ds.crosswalk_edges`
7. `mt4ds.friendly_atoms`
8. `mt4ds.rxnorm_allowed_tty_edges`
9. `mt4ds.rxnorm_tty_paths`
10. `mt4ds.rxnorm_tty_path_steps`
11. `mt4ds.rxnorm_tty_edges`
12. `mt4ds.cvx_metadata`
13. `mt4ds.code_replacements`
14. `mt4ds.snomed_top_level_depth`
15. `mt4ds.patient_friendly_strategy`
16. `mt4ds.patient_friendly_candidates`
17. `mt4ds.patient_friendly_candidate_paths`
18. `mt4ds.patient_friendly_resolutions`

### Implementation Details

#### Atoms

Create `mt4ds.atoms` from raw UMLS:

```sql
CREATE TABLE mt4ds.atoms AS
SELECT
  SAB AS source,
  CODE AS code,
  AUI AS aui,
  CUI AS cui,
  upper(TTY) AS tty,
  STR AS name,
  SUPPRESS AS suppress,
  CASE WHEN SUPPRESS = 'N' THEN true ELSE false END AS is_active
FROM umls.mrconso
WHERE CODE IS NOT NULL
  AND CODE != ''
  AND AUI IS NOT NULL
  AND AUI != '';
```

Indexes:

```sql
CREATE INDEX idx_atoms_source_code ON mt4ds.atoms(source, code);
CREATE INDEX idx_atoms_aui ON mt4ds.atoms(aui);
CREATE INDEX idx_atoms_cui_source ON mt4ds.atoms(cui, source);
```

#### Best Atoms

Create `mt4ds.best_atoms` using source-specific ranking from `sources/`.

Required fields:

```text
source, code, aui, cui, tty, name, suppress, is_active, rank, display_rank
```

Acceptance detail:

- SNOMED preferred term and fully specified name handling must be preserved.
- CPT display ranking must keep deterministic consumer-friendly behavior.
- Heading/range atoms must not be dropped if needed for hierarchy.

#### Friendly Atoms

Create `mt4ds.friendly_atoms`:

```text
cui, source, code, aui, tty, name, friendly_source, is_broad, is_heading
```

Rules:

1. Include `MEDLINEPLUS` and `CHV`.
2. Exclude heading terms during selection where required.
3. Compute broad flags during preparation.
4. Do not discard broad rows entirely; keep them for audit but mark them.

#### Hierarchy Edges

Create normalized child-to-parent rows:

```text
source, from_code, from_aui, from_cui, from_tty,
to_code, to_aui, to_cui, to_tty,
relationship, direction, edge_source
```

The runtime parent walk always reads `from -> to` where `direction='parent'`.

#### RxNorm TTY Tables

Create static topology tables:

```text
mt4ds.rxnorm_allowed_tty_edges(source_tty, target_tty)
mt4ds.rxnorm_tty_paths(path_id, start_tty, target_tty, match_type, target_order, path_depth)
mt4ds.rxnorm_tty_path_steps(path_id, step, tty)
```

Create materialized RxNorm AUI edges:

```sql
CREATE TABLE mt4ds.rxnorm_tty_edges AS
SELECT DISTINCT
  s.aui AS source_aui,
  s.code AS source_code,
  s.tty AS source_tty,
  s.name AS source_name,
  s.suppress AS source_suppress,
  t.aui AS target_aui,
  t.code AS target_code,
  t.tty AS target_tty,
  t.name AS target_name,
  t.suppress AS target_suppress,
  r.REL AS rel,
  r.RELA AS rela
FROM umls.mrrel r
JOIN mt4ds.atoms s ON s.aui = r.AUI1
JOIN mt4ds.atoms t ON t.aui = r.AUI2
JOIN mt4ds.rxnorm_allowed_tty_edges e
  ON e.source_tty = s.tty
 AND e.target_tty = t.tty
WHERE s.source = 'RXNORM'
  AND t.source = 'RXNORM';
```

Important:

- Preserve legacy raw direction `AUI1 -> AUI2` first.
- Add reverse direction only if focused tests prove it is needed.
- Include suppressed atoms, but rank active final targets first.

#### SNOMED Top-Level Depth

Compute `mt4ds.snomed_top_level_depth` from normalized SNOMED parent edges.

Rules:

1. Root depth starts at 1.
2. Accept patient-friendly fallback nodes only when `min_top_depth >= 4`.
3. Do not expand through depths 1-3 for broader fallback.

#### Patient-Friendly Strategy

Create data rows for workflow phases:

```text
source, phase, walk_kind, target_source, target_tty,
match_type, priority, max_depth, stop_on_hit, guard
```

Example RxNorm rows:

```text
RXNORM, group, tty_path, RXNORM, SCDG, group, 0, 4, true, none
RXNORM, ingredient_pin_scdc, tty_path, RXNORM, IN, ingredient, 1, 4, true, none
RXNORM, ingredient_pin_scdc, tty_path, RXNORM, MIN, ingredient, 2, 4, true, none
RXNORM, ingredient_default, tty_path, RXNORM, MIN, ingredient, 1, 4, true, none
RXNORM, ingredient_default, tty_path, RXNORM, IN, ingredient, 2, 4, true, none
```

#### Patient-Friendly Candidate And Resolution Tables

Create materialized candidate rows from prepared primitive tables:

```text
candidate_id, source, code, candidate_name, candidate_source,
match_type, match_depth, candidate_origin, walk_source, walk_code,
walk_depth, target_source, target_code, rank_features, policy_version
```

Candidate origins should be explicit and reviewable:

```text
exact_same_cui
native_hierarchy
source_native_tier
same_cui_crosswalk
snomed_fallback
snomed_to_target_native_hierarchy
snomed_to_target_snomed_fallback
direct_snomed_guarded_walk
rxnorm_tty
cvx_enrichment
original
```

`exact_same_cui` means a source-native MEDLINEPLUS/CHV candidate was found on
the input code's own CUI at walk depth 0. It should materialize with
`match_type='exact'` and `match_depth=0`. Ancestor hits must use
`native_hierarchy` or a fallback origin with a non-zero broader depth.
`same_cui_crosswalk` means the selected friendly candidate came through an
explicit cross-source same-CUI route, such as SNOMED target-source routing to an
ICD10CM/LNC/CPT/HCPCS code at depth 0. It should not be folded into
`native_hierarchy`, because no target hierarchy edge was walked.

Create optional path rows for debugging and review:

```text
candidate_id, step_order, op, source, code, aui, cui, target_source,
target_code, depth, name
```

Create final resolution rows:

```text
source, code, name, friendly_source, match_type, match_depth,
technical_name, selected_candidate_id, policy_version, generated_at
```

Runtime patient-friendly lookup should join input codes to
`mt4ds.patient_friendly_resolutions`. The candidate-generation SQL or Python
orchestration is a build/review path, not the normal runtime path.

### Tests

1. Synthetic table creation for every prepared table.
2. Synthetic hierarchy edges for every source rule.
3. Synthetic RxNorm path table generation.
4. Synthetic RxNorm edge materialization including suppressed intermediates.
5. Synthetic friendly atom broad-name flagging.
6. Synthetic patient-friendly candidate generation by source.
7. Synthetic patient-friendly resolution ranking and path trace rows.
8. Real-data smoke preparation against medterm4ds DB.
9. Validate table row counts are nonzero where expected.
10. Validate indexes exist.

### Acceptance Criteria

1. Every required table exists in `mt4ds`.
2. Runtime services can use `mt4ds` tables without raw UMLS joins for standard
   lookup/walk/candidate operations.
3. RxNorm TTY edge preparation is finite and indexed.
4. SNOMED guard table is present and populated for real data.
5. ICD10CM heading/range edge `L30 -> L20-L30` is present when built from a DB
   whose raw UMLS contains it.
6. Patient-friendly candidate and resolution tables exist and include policy
   version/build metadata.
7. `verify_mt4ds_schema` reports all required prepared runtime tables,
   including `mt4ds.crosswalk_edges`, and fails review when any are missing.
8. Reviewer confirms no expensive raw-recursive workflow remains in prepared
   table construction except bounded build-time preparation.

### Independent Review

The reviewer checks prepared SQL, indexes, row counts, and synthetic/real-data
cases. Any missing table, missing index, or source rule mismatch returns this
phase to development.

## Phase 4: Public Primitive Services

### Purpose

Expose lookup, walk, crosswalk, and selection over normalized tables.

### Implementation Tasks

1. Update or add `services/lookup.py` to query `mt4ds.best_atoms`.
2. Add `services/walk.py`.
3. Add `services/crosswalk.py` or refactor existing mapping service to use
   normalized tables.
4. Add `services/selection.py`.
5. Update `LocalDuckDBEngine` methods to call normalized primitive queries.
6. Keep public models stable:
   - `CodeInfo`
   - `CodeRelation`
   - `CodeMapping`
   - `FriendlyNameResult`
   - `ConceptMapRow`

### Lookup Query Snippet

```sql
WITH input_codes(source, code, input_order) AS (...)
SELECT
  i.input_order,
  a.source,
  a.code,
  a.cui,
  a.aui,
  a.tty,
  a.name,
  a.suppress
FROM input_codes i
LEFT JOIN mt4ds.best_atoms a
  ON a.source = i.source
 AND a.code = i.code
 AND a.rank = 1
ORDER BY i.input_order;
```

### Walk Query Snippet

```sql
WITH RECURSIVE
input_codes(source, code, input_order) AS (...),
base AS (
  SELECT i.input_order, a.*
  FROM input_codes i
  JOIN mt4ds.best_atoms a
    ON a.source = i.source
   AND a.code = i.code
   AND a.rank = 1
),
walk AS (
  SELECT input_order, source, code, aui, cui, tty, 0 AS depth
  FROM base
  UNION ALL
  SELECT w.input_order, e.source, e.to_code, e.to_aui, e.to_cui, e.to_tty,
         w.depth + 1
  FROM walk w
  JOIN mt4ds.walk_edges e
    ON e.source = w.source
   AND e.from_aui = w.aui
   AND e.direction = 'parent'
  WHERE w.depth < ?
)
SELECT * FROM walk;
```

### Crosswalk Query Snippet

```sql
WITH input_codes(source, code, input_order) AS (...),
lookup AS (...),
same_cui AS (
  SELECT l.input_order, l.source, l.code,
         e.target_source, e.target_code,
         'same_cui' AS match_type,
         0 AS match_depth
  FROM lookup l
  JOIN mt4ds.crosswalk_edges e
    ON e.source = l.source
   AND e.code = l.code
   AND e.match_type = 'same_cui'
)
SELECT * FROM same_cui;
```

### Tests

1. Synthetic lookup.
2. Synthetic parents/children/ancestors/descendants.
3. Synthetic same-CUI mapping.
4. Synthetic broader/narrower crosswalk.
5. Provenance generation for all non-exact paths.
6. Public service backward compatibility tests.

### Acceptance Criteria

1. Single-code calls and many-code calls produce the same results.
2. Public model fields and schemas remain stable unless deliberately versioned.
3. Primitive services do not embed source-specific SQL constants outside source
   preparation/strategy data.
4. All primitive tests pass.
5. Reviewer confirms query shapes use `mt4ds` tables.

### Independent Review

The reviewer checks service code for raw UMLS coupling and verifies model
compatibility. Any service-level source mechanics returns this phase to
development.

## Phase 5: RxNorm Patient-Friendly Proof Of Concept

### Purpose

Replace the current broad RxNorm recursive SQL with normalized TTY walk tables.
RxNorm is the proof that the architecture can be fast and source-faithful.

### Implementation Tasks

1. Implement RxNorm TTY path query over:
   - `mt4ds.rxnorm_tty_paths`
   - `mt4ds.rxnorm_tty_path_steps`
   - `mt4ds.rxnorm_tty_edges`
2. Remove or bypass broad `fallback_walk` over raw `main.mrrel`.
3. Implement target strategy from `mt4ds.patient_friendly_strategy`.
4. Preserve output:
   - `name`
   - `friendly_source='RXNORM'`
   - `match_type`
   - `match_depth`
   - `technical_name`
   - `matched_via`
5. Preserve expected examples:
   - `1149364 -> 1046770 -> 1165278`
   - `1604333 -> 1604332 -> 6922`
   - `1658659 -> 1856274`
6. Keep `IN` and `MIN` self rules.
7. Keep `PIN -> IN -> MIN`.

### Runtime Query Snippet

```sql
WITH RECURSIVE
input_codes(code, input_order) AS (...),
base AS (
  SELECT i.input_order, a.code, a.aui, a.tty, a.name AS technical_name
  FROM input_codes i
  JOIN mt4ds.best_atoms a
    ON a.source = 'RXNORM'
   AND a.code = i.code
   AND a.rank = 1
),
strategy AS (
  SELECT *
  FROM mt4ds.patient_friendly_strategy
  WHERE source = 'RXNORM'
),
paths AS (
  SELECT b.input_order, b.code AS input_code, b.aui AS start_aui,
         b.tty AS start_tty, b.technical_name,
         p.path_id, p.target_tty, p.match_type, p.target_order, p.path_depth
  FROM base b
  JOIN mt4ds.rxnorm_tty_paths p
    ON p.start_tty = b.tty
  JOIN strategy s
    ON s.target_tty = p.target_tty
   AND s.match_type = p.match_type
),
walk AS (
  SELECT input_order, input_code, technical_name,
         path_id, target_tty, match_type, target_order,
         0 AS step, start_aui AS aui
  FROM paths
  UNION ALL
  SELECT w.input_order, w.input_code, w.technical_name,
         w.path_id, w.target_tty, w.match_type, w.target_order,
         w.step + 1, e.target_aui
  FROM walk w
  JOIN mt4ds.rxnorm_tty_path_steps ps
    ON ps.path_id = w.path_id
   AND ps.step = w.step + 1
  JOIN mt4ds.rxnorm_tty_edges e
    ON e.source_aui = w.aui
   AND e.target_tty = ps.tty
),
hits AS (
  SELECT w.*, e.target_code, e.target_name, e.target_suppress
  FROM walk w
  JOIN mt4ds.rxnorm_tty_edges e
    ON e.target_aui = w.aui
  WHERE w.step = (
    SELECT max(step)
    FROM mt4ds.rxnorm_tty_path_steps ps
    WHERE ps.path_id = w.path_id
  )
),
ranked AS (
  SELECT *,
         row_number() OVER (
           PARTITION BY input_order
           ORDER BY target_order,
                    CASE target_suppress WHEN 'N' THEN 0 ELSE 1 END,
                    CASE WHEN regexp_matches(target_code, '^[0-9]+$') THEN 0 ELSE 1 END,
                    try_cast(target_code AS BIGINT),
                    target_code,
                    target_name
         ) AS rn
  FROM hits
)
SELECT * FROM ranked WHERE rn = 1;
```

The final implementation may simplify this query, but it must stay bounded by
static path steps.

### Tests

1. Synthetic RxNorm exact TTY path tests.
2. Synthetic suppressed intermediate test.
3. Synthetic active-final-target preference test.
4. Real-data focused tests for:
   - `1149364`
   - `1604333`
   - `1658659`
5. Real-data TTY-stratified sample.
6. Performance test:
   - 1 code
   - 100 codes
   - 1000 benchmark RxNorm codes

### Acceptance Criteria

1. The three focused RxNorm cases return expected targets.
2. No runtime query recursively joins raw `mrrel` and `mrconso`.
3. 1000 RxNorm benchmark codes complete in a reasonable runtime on the prepared
   medterm4ds DB. Target: under 10 seconds on the workstation that previously
   completed RxNorm in about 1.2 seconds.
4. Runtime memory does not approach total system memory for 1000 RxNorm codes.
5. Results match or intentionally improve legacy bulk behavior.
6. Reviewer confirms the query is bounded by static TTY path steps.

### Independent Review

The reviewer compares outputs against legacy bulk behavior and inspects query
shape. Any broad raw recursive join or missed focused case returns this phase
to development.

## Phase 6: Patient-Friendly Candidate And Resolution Materialization

### Purpose

Build patient-friendly candidates and final resolutions from normalized
primitives so runtime lookup works for one code, an arbitrary list, or a whole
code system with the same semantics.

### Implementation Tasks

1. Generate candidate rows for ICD10CM/ICD10PCS/LNC/HCPCS/CPT:
   - lookup
   - walk native parents
   - find friendly candidates
   - SNOMED fallback when native walk misses
   - do not use prefix-inferred hierarchy edges unless UMLS contains the
     relationship and preparation normalized it
2. Generate candidate rows for LOINC:
   - preserve component/axis/common-name tiers
   - use normalized tables for LNC-native hierarchy before guarded SNOMED
     fallback
3. Generate candidate rows for SNOMED:
   - target-source routing priority:
     - `ICD10CM`
     - `ICD10PCS`
     - `LNC`
     - `CPT`
     - `HCPCS`
   - for each target candidate, generate
     `snomed_to_target_native_hierarchy` candidates by walking the target
     source hierarchy first
   - if a target-native route misses, generate
     `snomed_to_target_snomed_fallback` candidates by entering guarded SNOMED
     fallback at most once
   - if no target-source route yields an acceptable candidate, generate
     `direct_snomed_guarded_walk` candidates
4. Generate candidate rows for RxNorm:
   - use explicit TTY topology/path tables
   - preserve the established group-target and ingredient-target strategy
   - rank active final targets before suppressed targets
   - use RxNorm source-native output, not MEDLINEPLUS/CHV
5. Generate candidate rows for CVX:
   - use lookup/enrichment metadata
   - no hierarchy fallback unless explicitly added later
6. Apply shared candidate filtering/ranking:
   - group candidates by route and walk-depth frontier
   - choose the closest frontier that has any acceptable non-broad
     MEDLINEPLUS/CHV candidate
   - MEDLINEPLUS over CHV only within that same closest frontier
   - do not pick a farther ancestor just because it has a better-looking label
     if the current frontier has an acceptable non-broad candidate
   - do not let a farther MEDLINEPLUS candidate override a closer acceptable
     CHV candidate
   - reject known broad MEDLINEPLUS/CHV labels
   - reject bad combination-name CHV candidates with no meaningful token overlap
   - deterministic tie-breakers
7. Materialize `mt4ds.patient_friendly_resolutions`.
8. Ensure patient-friendly returns:
   - original display fallback
   - `technical_name`
   - structured provenance
   - selected candidate/path identifier
   - policy version
   - a bumped policy version whenever patient-friendly selection or fallback
     semantics change in a way that could invalidate existing materialized
     resolution rows
9. Source-wide materialization reports must include:
   - total inputs
   - total resolution rows
   - missing resolution rows
   - true friendly resolutions
   - original-display fallbacks
   - selected match-type counts
   - aggregate and per-source resolution coverage ratios
   - per-source summaries with the same fields

### Non-RxNorm Query Snippet

```sql
WITH RECURSIVE
input_codes(source, code, input_order) AS (...),
lookup AS (...),
native_walk AS (... mt4ds.walk_edges ...),
native_friendly AS (
  SELECT w.input_order, w.depth, f.*
  FROM native_walk w
  JOIN mt4ds.friendly_atoms f ON f.cui = w.cui
  WHERE f.is_broad = false
),
native_ranked AS (...),
snomed_seed AS (... mt4ds.crosswalk_edges, same_cui_edges compatibility fallback ...),
snomed_walk AS (... guarded mt4ds.walk_edges ...),
snomed_friendly AS (...),
all_candidates AS (...),
ranked AS (...)
SELECT ...;
```

The ranking shape must compute `min_acceptable_depth` per route before applying
friendly-source priority. In other words, the order is:

```text
route priority
-> nearest acceptable walk-depth frontier
-> friendly source priority at that depth, MEDLINEPLUS before CHV
-> deterministic atom/name tie-breakers
```

This prevents over-walking to a more general MEDLINEPLUS topic when a closer
acceptable CHV candidate already exists.

The query above describes candidate generation. The runtime API should not run
that recursive candidate-generation query for normal requests once
`mt4ds.patient_friendly_resolutions` exists for the requested policy version.

### Required Edge Cases

1. `ICD10CM L30.1 -> L30 -> L20-L30 -> CHV 0000037198`
2. `ICD10CM S37.06 -> S37.0 -> kidney injury CHV`
3. `ICD10CM M99.75 -> SNOMED 203715007 -> 88230002 -> CHV`
4. `ICD10CM O26.7 -> SNOMED 199308008 -> 263038009 -> 263012009 -> CHV`
5. SNOMED walk does not expand into top-level depths 1-3.
6. CVX keeps high current match rate from prior reports.
7. LOINC source-native tiers are preserved.
8. `ICD10CM S43` must not jump to unrelated broad injury categories such as
   head injury through non-UMLS or over-broad fallback.
9. `ICD10CM L76.32` must not map to unrelated CHV/MEDLINEPLUS disease labels
   through weak broad fallback.
10. Codes without a defensible friendly candidate return original/source
    display rather than an unrelated broader term.
11. SNOMED candidate generation does not recurse indefinitely through
    SNOMED-to-target and target-to-SNOMED routes; each target route may enter
    guarded SNOMED fallback at most once.

### Tests

1. Synthetic patient-friendly tests per source.
2. Focused real-data tests for known cases.
3. Candidate-table row tests for each candidate origin.
4. Resolution-table lookup tests for one code, a list, and a full-source sample.
5. Per-source sampled parity reports.
6. Full benchmark report only after focused tests pass.

### Acceptance Criteria

1. All required edge cases pass.
2. SNOMED guard is enforced broadly.
3. Closest acceptable frontier wins, with MEDLINEPLUS-over-CHV preference
   applied only within the same frontier.
4. A farther MEDLINEPLUS candidate does not override an acceptable closer CHV
   candidate; MEDLINEPLUS preference is a same-depth tie-breaker, not a license
   to skip hierarchy distance.
5. Public output schema is unchanged unless deliberately versioned.
6. Runtime patient-friendly uses `mt4ds.patient_friendly_resolutions` when the
   table is present and current for the policy version.
7. One-code and source-wide patient-friendly calls use the same prepared
   resolution semantics.
8. Reviewer confirms patient-friendly contains orchestration, not raw source
   graph mechanics.
9. SNOMED patient-friendly resolution follows the target-first policy:
   `ICD10CM`, `ICD10PCS`, `LNC`, `CPT`, and `HCPCS` hierarchies are walked
   before any direct guarded SNOMED hierarchy walk.

### Independent Review

The reviewer manually inspects required edge cases and source query shapes. Any
failed edge case or hidden raw source logic returns this phase to development.

## Phase 7: Crosswalk, Optimize, ConceptMap, And Bulk

### Purpose

Move other workflows onto the same primitives.

### Implementation Tasks

1. Refactor exact mapping to use `mt4ds.crosswalk_edges`, with
   `mt4ds.same_cui_edges` retained as the source table and compatibility
   fallback. Engine-level prepared exact mapping should take the prepared path
   when either `crosswalk_edges` or `same_cui_edges` is available.
2. Refactor broader/narrower mapping to use `mt4ds.walk_edges`.
3. Refactor optimize to use normalized hierarchy edges.
4. Refactor ConceptMap export to consume stable `CodeMapping` and
   `FriendlyNameResult` rows.
5. Ensure bulk workflow streams inputs but calls the same services.
6. Ensure CLI/API/MCP use the same service functions.

### Tests

1. Mapping unit tests.
2. Broader/narrower crosswalk tests.
3. Optimize tests for valueset include/exclude behavior.
4. ConceptMap CSV/JSON/FHIR tests.
5. Bulk resume/checkpoint smoke.
6. MCP compact output smoke.

### Acceptance Criteria

1. No workflow has a separate terminology algorithm.
2. ConceptMap provenance preserves match type/depth/path.
3. Optimize behavior is source-aware and uses normalized walk edges.
4. Bulk and single-code results match for the same inputs.
5. Reviewer confirms no duplicated source logic in bulk/CLI/API/MCP.

### Independent Review

The reviewer traces one example through Python, CLI, API, MCP, and bulk paths.
Any divergent logic returns this phase to development.

## Phase 8: Data Setup And Release-Aware Builds

### Purpose

Make database preparation reproducible for 2025AB parity and current release
production use.

### Implementation Tasks

1. Update build command to support:
   - download latest Metathesaurus Full Subset
   - pin release version
   - build raw `umls` schema
   - prepare `mt4ds` schema
   - verify schema
   - write to named DB roles instead of ambiguous `umls_local.duckdb`
2. Add option to build from existing archive path:

```bash
python3 scripts/download_umls_release.py \
  --archive /mnt/d/medterm/data/umls-2025AB-metathesaurus-full.zip \
  --build \
  --output-db data/umls_2025ab.duckdb \
  --replace
```

3. Stage builds on Linux filesystem when needed, then copy finished DB to
   `/mnt/d` to avoid WSL mount finalization stalls.
4. Save raw release data under `data/umls`.
5. Record UMLS release metadata in `mt4ds.prepare_manifest`.
6. Add explicit DB role flags or metadata:

```bash
python3 scripts/download_umls_release.py \
  --release-version 2026AA \
  --build \
  --db-role current_candidate \
  --output-db data/umls_current.duckdb
```

7. Ensure reports and benchmark scripts require or infer DB role and include it
   in output metadata.
8. Build scripts must refuse `--build` when the UMLS release cannot be
   determined from `--release-version` or the archive name.
9. Build scripts must refuse ambiguous `umls_local.duckdb` output names. Use a
   role/release-specific path such as `data/umls_current.duckdb` or
   `data/umls_2025ab.duckdb`.
10. Download/build scripts must refuse missing explicit `--archive` paths before
    extraction or build work starts.
11. Download/build script JSON payloads must include release type, effective
    release version when known, archive-inferred release version when known,
    current filter, output directory, and extraction state.
12. Build scripts must refuse mismatches between an explicit `--release-version`
    and an archive-inferred release version when both are known.
13. Build scripts must refuse an existing output DB before extraction/build
    work starts unless `--replace` is explicitly set.
14. Materialization, build, parity, benchmark comparison, bulk validation,
    mapping quality, acceptance, smoke, notebook, patient-friendly review, and
    prepared-resolution benchmark reports that call `verify_mt4ds_schema` must
    include structured prepared-table metadata and a `missing_prepared_tables`
    list, plus raw `schema_errors` from verification.
15. Report scripts should use the shared
    `medterm4ds.services.schema_reporting` helpers rather than duplicating
    prepared-schema metadata extraction.
16. Reports should capture schema metadata after any workflow-level cache,
    preparation, or notebook execution step that can change the database state,
    so `prepared_tables`, `missing_prepared_tables`, and `schema_errors`
    describe the final state used by the report.

### Tests

1. Synthetic build from flat RRF.
2. Synthetic build from `.RRF.gz`.
3. Synthetic build from `.nlm` archive.
4. Existing archive build path.
5. Verify raw and prepared schemas.
6. Build script refuses to overwrite a named fixture unless `--replace` and
   role metadata are provided.
7. Benchmark script metadata includes DB role.

### Acceptance Criteria

1. 2025AB can be built or explicitly skipped because existing medterm DB is used
   as fixed parity fixture.
2. Current release can be downloaded and built from Metathesaurus Full Subset.
3. Raw data path is clear.
4. Prepared schema version, UMLS release, manifest DB role, and source archive
   are recorded when available.
5. Output DB names clearly indicate role/release purpose.
6. Reviewer confirms data setup does not hide release differences.

### Independent Review

The reviewer validates build provenance and release metadata. Any ambiguous data
origin returns this phase to development.

## Phase 9: Performance Baselines

### Purpose

Prove the normalized approach is fast enough for one code, many codes, and bulk
exports.

### Benchmarks

Run against prepared medterm4ds DB:

1. Lookup:
   - 1 code
   - 100 codes
   - 10,000 codes
2. Walk:
   - ICD10CM ancestors
   - SNOMED ancestors with guard
   - RxNorm TTY target paths
3. Crosswalk:
   - same-CUI
   - broader/narrower fallback
4. Patient-friendly:
   - focused edge cases
   - 100 per source
   - 1000 per source
   - full benchmark CSV
   - whole-source export for at least one small source and one large source
5. Bulk:
   - stream selected sources
   - memory profile
   - output throughput

Every benchmark report must include `db_path`, `db_role`, `db_role_source`,
`umls_release`, `prepared_schema_version`, `manifest_db_role`,
`source_archive`, `threads`, `memory_limit`, and `query_chunk_size`. Reports
that run schema verification must also include structured `prepared_tables`,
`missing_prepared_tables`, and `schema_errors` metadata so a report can
distinguish requested/report role from manifest provenance and prepared-table
completeness.

### Performance Targets

Targets can be adjusted only by explicit review.

1. Single-code patient-friendly returns in under 1 second for normal warm DB
   usage.
2. Runtime patient-friendly over a prepared resolution table is seconds-scale
   for the 5,285-row benchmark CSV.
3. RxNorm and SNOMED 1000-code patient-friendly lookup from prepared
   resolutions completes in seconds, not minutes.
4. Full benchmark CSV completes in minutes only if candidate/resolution
   materialization is included; lookup-only benchmark should be seconds-scale.
5. No benchmark consumes near-total system memory unexpectedly.
6. Full-source exports stream and do not require loading all outputs into
   Python memory.

### Tests

1. Add benchmark scripts with machine-readable JSON output.
2. Add threshold assertions for smoke-sized tests.
3. Keep full benchmarks as manual/release-gate scripts, not normal unit tests.

### Acceptance Criteria

1. Benchmark JSON files exist under `reports/performance/`.
2. Targets are met or a reviewed exception is documented.
3. Query plans do not include unbounded raw recursive joins.
4. Reviewer confirms performance results are from prepared schema, not a mixed
   old/new DB.
5. Reviewer confirms benchmark metadata identifies the DB role.

### Independent Review

The reviewer reruns a representative subset and inspects benchmark metadata.
Any missing metadata or unexplained slow path returns this phase to development.

## Phase 10: Parity And Quality Review

### Purpose

Confirm semantic parity where expected and document intentional differences.

### Test Matrix

Run against:

1. Legacy 2025AB medterm DB:
   - `/mnt/d/medterm/data/umls_local.duckdb`
2. Prepared medterm4ds current release DB.
3. Optional prepared medterm4ds 2025AB DB if build path is fixed.

### Required Reports

1. Focused edge-case report.
2. Source-sampled parity report.
3. Full patient-friendly benchmark report.
4. Mapping quality report.
5. Lookup/walk/crosswalk smoke report.
6. Performance report.
7. Intentional-differences allowlist.

Every report must include DB role metadata. Reports missing DB role metadata are
invalid and must be regenerated.

### Acceptance Criteria

1. Every focused edge case passes.
2. Every mismatch in sampled/full reports is classified as:
   - algorithm bug
   - release drift
   - loader/data issue
   - intentional fix
   - acceptable display-only difference
3. Algorithm bugs return to development.
4. Release drift is documented with DB/release metadata.
5. Full reports are CSV-reviewable.
6. Reviewer confirms reports are understandable without reading code.
7. Reviewer confirms no report compares two different UMLS releases without
   labeling the release difference.

### Independent Review

The reviewer manually samples mismatch CSVs and verifies classifications. Any
unclassified mismatch returns to development.

## Phase 11: API, CLI, MCP, Notebook, And Docs

### Purpose

Expose the normalized architecture without changing user-facing behavior unless
documented.

### Implementation Tasks

1. Update docs:
   - architecture
   - data setup
   - patient-friendly
   - hierarchy
   - mapping
   - optimize
   - NDC/RxNorm
2. Update API reference for stable models and services.
3. Update notebooks to prefer Python examples.
4. Update CLI help text where new data setup/prepared schema commands exist.
5. Ensure MCP compact output remains low-noise.

### Tests

1. CLI acceptance tests.
2. API import and endpoint smoke tests.
3. MCP import and tool smoke tests.
4. Notebook smoke tests.
5. Docusaurus build.

### Acceptance Criteria

1. Public examples use the normalized APIs.
2. CLI/API/MCP behavior remains consistent.
3. Docs explain UMLS license/data requirements.
4. Docs do not overemphasize internal engine names.
5. Reviewer confirms docs match implementation.

### Independent Review

The reviewer follows the docs on a fresh environment where practical. Any
example that does not run or contradicts implementation returns this phase to
development.

## Phase 12: Release Gate

### Purpose

Decide whether the package is publishable.

### Required Checks

1. Unit tests:

```bash
pytest -q
```

2. Focused real-data smoke:

```bash
python3 scripts/run_real_data_smoke.py \
  --db data/umls_current.duckdb \
  --db-role current_candidate
```

3. Patient-friendly focused cases:

```bash
python3 scripts/run_patient_friendly_focused_cases.py \
  --db data/umls_current.duckdb \
  --db-role current_candidate
```

4. Patient-friendly benchmark:

```bash
python3 scripts/compare_patient_friendly_benchmark.py \
  --db data/umls_current.duckdb \
  --db-role current_candidate \
  --benchmark /mnt/d/medterm/data/patient_friendly_benchmark.csv \
  --output-prefix reports/quality/patient_friendly_release
```

5. CLI acceptance:

```bash
python3 scripts/run_cli_acceptance.py \
  --db data/umls_current.duckdb \
  --db-role current_candidate
```

6. Package build:

```bash
hatch build
```

7. Docs build:

```bash
cd web/website
npm run build
```

### Acceptance Criteria

1. All required checks pass.
2. All independent review phases are `pass`.
3. No unresolved algorithm bugs.
4. No unexplained performance regressions.
5. License metadata is correct.
6. README and docs identify UMLS data/license requirements.
7. Version metadata is correct for the planned release.

### Independent Review

The release reviewer verifies all phase review logs. If any phase is missing,
failed, or ambiguous, release returns to development.

## Risk Register

| Risk | Mitigation |
| --- | --- |
| UMLS release drift looks like algorithm regression | Always record DB path, release, and prepared schema version in reports. |
| RxNorm TTY query still slow | Use bounded path-step CTE first; switch to generated `UNION ALL` paths if profiling proves it faster. |
| Prepared traversal is still too slow for patient-friendly | Materialize `mt4ds.patient_friendly_candidates` and `mt4ds.patient_friendly_resolutions`; runtime should join resolutions. |
| Prepared tables become too large | Track row counts and indexes; materialize only runtime-critical reductions. |
| Source rules duplicated in services | Review service code for raw source constants and return to dev if found. |
| SNOMED fallback gets too broad | Enforce `mt4ds.snomed_top_level_depth >= 4` and block expansion through levels 1-3. |
| Obsolete/suppressed handling hides active results | Rank active final targets first and preserve resolution provenance. |
| Full benchmarks waste time during debugging | Use focused cases first; full benchmark only after source-specific tests pass. |
| Build finalization stalls on `/mnt/d` | Stage large DuckDB builds on Linux filesystem, then copy complete DB. |

## Definition Of Done

The refactor is complete only when all of the following are true:

1. Confusing pre-refactor artifacts are archived or explicitly documented.
2. Database roles are documented and every report identifies DB role/release.
3. The `mt4ds` prepared schema exists and is verified.
4. Source-specific graph mechanics are encoded in prepared tables/strategy rows.
5. Lookup, walk, crosswalk, and select primitives use normalized tables.
6. Patient-friendly is workflow composition.
7. Patient-friendly candidate and resolution tables are materialized,
   versioned, and traceable.
8. RxNorm TTY traversal is bounded, fast, and parity-reviewed.
9. SNOMED guard applies broadly.
10. ICD10CM/LNC/CPT/HCPCS/SNOMED/CVX patient-friendly edge cases pass.
11. One-code, list, and full-source patient-friendly calls use the same
   resolution semantics.
12. Optimize, mapping, ConceptMap, CLI, API, MCP, and notebooks use shared
   services.
13. Performance baselines are recorded.
14. Parity reports are generated and reviewed.
15. Every phase has an independent `pass` review.
16. Release checklist passes.
