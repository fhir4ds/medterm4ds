# Terminology Architecture Requirements

This document defines the target architecture for terminology operations in
`medterm4ds`. It is intentionally detailed because the same logic must support
single-code notebook use, bulk exports, CLI/API/MCP tools, and parity review
against legacy `medterm`.

The implementation plan for this architecture is
[`plans/terminology-normalization-implementation-plan.md`](plans/terminology-normalization-implementation-plan.md).

The key design principle is:

> Patient-friendly naming, mapping, optimize, discovery, and MCP tools should
> compose the same lookup, walk, crosswalk, and selection primitives.

## Goals

1. Keep one terminology implementation shared by Python, CLI, API, MCP, and
   bulk workflows.
2. Make source-specific behavior explicit and testable.
3. Support one code, a small list of codes, or full-source bulk runs without
   changing semantics.
4. Query prepared terminology tables at runtime rather than repeatedly joining
   raw UMLS tables.
5. Normalize code-system-specific graph mechanics into same-shaped tables and
   views so runtime workflows can compose lookup, walk, crosswalk, and select
   without knowing raw source quirks.
6. Preserve provenance for every non-trivial result:
   - input code
   - lookup atom
   - hierarchy walk path
   - crosswalk path
   - candidate selected
   - match type/depth
7. Preserve parity with `/mnt/d/medterm` unless a difference is explicitly
   documented as a bug fix or intentional behavior change.
8. Support patient-friendly resolution for one code, an arbitrary list of
   codes, or an entire prepared code system with the same semantics and
   reasonable runtime performance.

## Non-Goals

1. Do not create a separate bulk transform system with different terminology
   rules.
2. Do not embed raw SQL fragments independently in CLI/API/MCP adapters.
3. Do not rely on one very large recursive query over raw `mrrel` and
   `mrconso`.
4. Do not make patient-friendly naming responsible for source-specific graph
   mechanics.
5. Do not infer patient-friendly hierarchy from code prefixes or other
   non-UMLS clinical shortcuts. Derived edges must come from UMLS relationships
   or an explicit source-owned topology such as RxNorm TTY adjacency.

## Target Module Directory Structure

The code should make the normalized-table architecture visible. A proposed
layout:

```text
src/medterm4ds/
  core/
    models.py              # Stable output records and provenance.
    schemas.py             # Versioned output schemas.
    config.py              # Engine/runtime configuration.

  engines/
    base.py                # Protocols for lookup/walk/crosswalk/select.
    duckdb/
      engine.py            # LocalDuckDBEngine orchestration over services.
      prepared.py          # Build/verify prepared mt4ds schema objects.
      queries/
        lookup.sql
        walk.sql
        crosswalk.sql
        patient_friendly.sql
        rxnorm_tty_walk.sql

  sources/
    base.py                # SourceStrategy protocol and default rules.
    generic.py             # Generic UMLS source rules.
    rxnorm.py              # RxNorm TTY topology and NDC/RxCUI rules.
    cvx.py                 # CVX lookup/enrichment rules.
    snomed.py              # SNOMED isa/top-level guard rules.
    icd.py                 # ICD10CM/ICD10PCS PAR hierarchy rules.
    loinc.py               # LOINC component/common-name rules.
    cpt_hcpcs.py           # CPT/HCPCS hierarchy/display rules.

  services/
    lookup.py              # Public lookup service over normalized tables.
    walk.py                # Public hierarchy/graph walk service.
    crosswalk.py           # Public mapping/crosswalk service.
    selection.py           # Candidate ranking/frontier selection.
    patient_friendly.py    # Workflow composition only.
    optimize.py            # Valueset composition over walk/crosswalk.
    bulk.py                # Streaming wrappers over public services.

  outputs/
  domains/
  apps/
```

`sources/` should not become another service layer. Source modules define how to
prepare normalized rows and source policy metadata. Public workflows still live
in `services/`.

## Database Schemas

Use schemas to distinguish raw UMLS inputs from medterm4ds runtime objects.

```text
umls.mrconso
umls.mrrel
umls.mrsat

mt4ds.atoms
mt4ds.best_atoms
mt4ds.hierarchy_edges
mt4ds.walk_edges
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

The raw `umls` schema is the source of truth for loaded UMLS data. The `mt4ds`
schema is the runtime terminology model. Existing `main.mrconso`,
`main.mrrel`, and `main.mrsat` tables or views can remain temporarily for
backward compatibility, but new runtime logic should query `mt4ds`.

## Normalized Table Principle

Code-system-specific behavior should be handled while building normalized
tables/views, not by scattering branches through every workflow.

Examples:

- ICD10CM `REL='PAR'` hierarchy becomes rows in `mt4ds.walk_edges`.
- SNOMED `isa` hierarchy and top-level guard metadata become rows in
  `mt4ds.walk_edges` plus `mt4ds.snomed_top_level_depth`.
- RxNorm TTY topology becomes rows in `mt4ds.rxnorm_tty_edges` and
  `mt4ds.rxnorm_tty_paths`.
- CVX group metadata becomes rows in `mt4ds.cvx_metadata` and lookup display
  ranking fields.
- MEDLINEPLUS/CHV candidates become rows in `mt4ds.friendly_atoms` with
  `is_broad` already computed.

After preparation, runtime workflows should mostly compose same-shaped tables:

```text
lookup input -> mt4ds.best_atoms
walk graph -> mt4ds.walk_edges or mt4ds.rxnorm_tty_edges
crosswalk -> mt4ds.crosswalk_edges
friendly candidates -> mt4ds.friendly_atoms
select -> shared ranking/frontier policy
```

Some runtime policy can still vary by source, but that policy should be
represented as data where practical, for example
`mt4ds.patient_friendly_strategy`.

Patient-friendly is the main exception to a pure live-query runtime. The
candidate-generation pipeline still composes lookup, walk, crosswalk, and
selection primitives, but production patient-friendly lookup should read a
materialized resolution table keyed by source/code/release/policy version. This
keeps one-code notebook calls and whole-source exports on the same semantic
path while avoiding live traversal for every request.

Runtime services may expose a debug/trace mode that regenerates candidates for
a small input set, but that path is not the normal high-volume execution mode.

## Core Primitives

### Lookup

Lookup resolves source/code inputs to canonical terminology metadata.

Required output fields:

- `source`
- `code`
- `cui`
- `aui`
- `tty`
- `name`
- `suppress`
- `is_active`
- `technical_name`, when different from display

Lookup is batch-first. Single-code lookup calls the same batch path with one
input.

Source-specific lookup rules:

| Source | Lookup behavior |
| --- | --- |
| `RXNORM` | Resolve RxCUI atoms, normalize NDC inputs to 11-digit NDC, map NDC through RxNorm `MRSAT`, preserve suppressed/obsolete candidates for historical workflows. |
| `CVX` | Resolve CVX display and optionally enrich with CDC vaccine group metadata. This is a lookup/enrichment strategy, not a separate workflow. |
| `SNOMEDCT_US` | Prefer active preferred terms for display and preserve fully specified name as `technical_name` when available. |
| `ICD10CM`, `ICD10PCS`, `HCPCS`, `LNC`, `CPT`, `ATC`, `MSH` | Use source-specific preferred atom ranking. Heading/range atoms must be retained where they are needed for hierarchy traversal. |

### Walk

Walk traverses a source-specific graph. RxNorm TTY traversal is a walk strategy,
even though it is not an `isa` tree.

Required output fields:

- `input_source`
- `input_code`
- `walk_source`
- `walk_code`
- `walk_aui`
- `walk_tty`
- `depth`
- `relationship`
- `path`

Walk requirements:

1. Must support one or many start codes.
2. Must support bounded depth.
3. Must preserve a frontier per depth so selection can apply "first acceptable
   frontier wins" rules.
4. Must expose direction:
   - parents/ancestors
   - children/descendants
   - source-specific target traversal, such as RxNorm target TTY.
5. Must not expand into known unsafe broad SNOMED levels.

### Crosswalk

Crosswalk moves from one source to another source.

Required output fields:

- `source`
- `code`
- `target_source`
- `target_code`
- `target_cui`
- `target_aui`
- `match_type`
- `match_depth`
- `matched_via`

Crosswalk modes:

| Mode | Meaning |
| --- | --- |
| `same_cui` | Same UMLS CUI, no hierarchy traversal. |
| `source_ancestor_same_cui` | Walk source ancestors, then same-CUI target match. |
| `target_ancestor` | Same-CUI target match, then walk target hierarchy. |
| `snomed_fallback` | Crosswalk into SNOMED, then walk guarded SNOMED hierarchy. |
| `best_available` | Exact first, then bounded fallback with explicit provenance. |

### Select

Selection chooses the best result from a candidate frontier.

Selection requirements:

1. Rank only after candidate generation is complete for the current frontier.
2. Preserve "closest acceptable hierarchy frontier wins" for patient-friendly
   names. A frontier is the set of candidate nodes reached at the same walk
   depth for the current route. Select the nearest depth that contains any
   acceptable non-broad `MEDLINEPLUS` or `CHV` candidate.
3. Prefer `MEDLINEPLUS` over `CHV` only within that same nearest acceptable
   frontier.
4. Do not choose a farther ancestor because it has a better-looking label if
   the current frontier has an acceptable non-broad candidate. A farther
   `MEDLINEPLUS` label must not override a closer acceptable `CHV` label.
5. Filter known over-broad friendly names.
6. Prefer active/current codes over suppressed/obsolete codes, but do not drop
   suppressed candidates before a historical workflow has a chance to resolve
   them.
7. Use deterministic tie-breakers:
   - source priority
   - match depth
   - active rank
   - numeric code sort when applicable
   - lexical name
   - AUI

## Source Hierarchy Rules

Hierarchy rules are source-specific. Runtime walk queries should use prepared
edge tables that already encode these rules.

| Source | Relationship | Raw UMLS rule | Direction to normalize |
| --- | --- | --- | --- |
| `SNOMEDCT_US` | `isa` | `RELA = 'isa'`; include `REL='PAR'`/`REL='CHD'` forms where UMLS stores `isa` direction that way | normalize to `child_code -> parent_code` |
| `ICD10CM` | `isa` | `REL = 'PAR'`, `RELA IS NULL`; reverse `REL='CHD'` when present | normalize to `child_code -> parent_code` |
| `ICD10PCS` | `isa` | `REL = 'PAR'`, `RELA IS NULL`; reverse `REL='CHD'` when present | normalize to `child_code -> parent_code` |
| `HCPCS` | `isa` | `REL = 'PAR'`, `RELA IS NULL`; reverse `REL='CHD'` when present | normalize to `child_code -> parent_code` |
| `LNC` | `isa` | `REL = 'PAR'`, `RELA IS NULL`; reverse `REL='CHD'` when present | normalize to `child_code -> parent_code` |
| `CPT` | `isa` | `RELA = 'isa'`; include reverse where UMLS relation direction requires it | normalize to `child_code -> parent_code` |
| `ATC` | `isa` | `RELA = 'isa'` | normalize to `child_code -> parent_code` |
| `MSH` | `isa` | `RELA = 'isa'` | normalize to `child_code -> parent_code` |
| `RXNORM` | TTY topology | source-specific TTY graph over RxNorm atoms | normalize to `source_aui -> target_aui` for allowed TTY hops |

### SNOMED Top-Level Guard

SNOMED hierarchy traversal must prevent overly broad patient-friendly fallback.

Requirements:

1. Maintain `mt4ds.snomed_top_level_depth(code, min_top_depth)`.
2. A SNOMED node is acceptable for patient-friendly fallback only when
   `min_top_depth >= 4`.
3. Traversal must not expand through levels 1-3 while searching for broader
   friendly candidates.
4. Exact same-CUI matches may bypass the top-level guard when they do not
   require broader SNOMED expansion.
5. The guard applies to:
   - direct SNOMED patient-friendly fallback
   - crosswalk-to-SNOMED fallback from ICD10CM, ICD10PCS, LNC, CPT, HCPCS
   - any MCP/API/domain tool that requests broader SNOMED context.

## RxNorm TTY Topology

RxNorm patient-friendly naming is source-native hierarchy traversal over a TTY
graph. It should be implemented through the generic `walk` primitive with a
RxNorm source strategy.

### Allowed TTY Hops

The topology is based on the legacy `medterm` RxNorm traversal and RxNav
default paths, with the `SBDC` edge included.

| Start TTY | Allowed next TTYs |
| --- | --- |
| `SCD` | `SBD`, `SCDC`, `SCDF`, `SCDG`, `GPCK`, `DF`, `MIN` |
| `SBD` | `BN`, `SCD`, `SBDF`, `SBDG`, `SBDC`, `BPCK`, `SCDC` |
| `SCDC` | `SCD`, `SBD`, `IN`, `PIN` |
| `SCDF` | `SCD`, `SBDF` |
| `SBDC` | `SBD`, `SBDF`, `IN` |
| `SBDF` | `SBD`, `SCDF` |
| `SCDG` | `SCD`, `SBDG`, `DFG` |
| `SBDG` | `SBD`, `SCDG` |
| `GPCK` | `SCD`, `BPCK` |
| `BPCK` | `SBD`, `GPCK` |
| `MIN` | `SCD`, `IN` |
| `IN` | `SCDC`, `MIN`, `BN` |
| `PIN` | `SCDC` |
| `BN` | `SBD`, `IN` |
| `DF` | `SCD` |
| `DFG` | `SCDG` |

### RxNorm Target Strategy

Patient-friendly RxNorm strategy:

1. For group-target TTYs, try target `SCDG`.
   - Group-target TTYs are explicitly:
     `SCD`, `SBD`, `SBDC`, `SCDF`, `SBDF`, `GPCK`, `BPCK`,
     `SBDG`, `SCDG`, `DFG`.
2. Then try ingredient targets:
   - `PIN`: `IN`, then `MIN`
   - `SCDC`: `IN`, then `MIN`
   - `IN`: keep self unless an exact self result is returned
   - `MIN`: keep self
   - all other TTYs: `MIN`, then `IN`
3. If no group or ingredient target is found:
   - `IN` and `MIN` remain themselves with match type `ingredient`
   - all other TTYs use original RxNorm display with match type `original`

Expected examples:

| Input | Expected walk |
| --- | --- |
| `1149364` (`SBD`) | `SBD -> SCD -> SCDG`, resolving through `1046770 -> 1165278` |
| `1604333` (`SBDC`) | `SBDC -> BN -> IN`, resolving through `1604332 -> 6922` |
| `1658659` (`SCD`) | `SCD -> SCDG`, resolving to `1856274` |

### RxNorm Suppression Policy

RxNorm traversal must support historical data without making every runtime query
explode.

Requirements:

1. Traversal edges may include suppressed atoms as intermediates when they are
   needed to reach a valid target.
2. Final target selection ranks active (`SUPPRESS = 'N'`) targets before
   suppressed targets.
3. Suppressed targets are allowed only when no active target is reachable for
   the requested target TTY/path.
4. Reports must expose whether the selected target was active or suppressed.
5. The prepared RxNorm edge table must include `source_suppress` and
   `target_suppress`.

### Building RxNorm TTY Edges

Do not perform recursive joins over raw `mrrel` and `mrconso` at runtime.
RxNorm source-specific logic should mainly live in this preparation step.
After preparation, runtime code sees the same concept as any other walk:
`from atom -> to atom`, with a `walk_kind` and optional target TTY.

Prepare a materialized table:

```sql
CREATE TABLE mt4ds.rxnorm_tty_edges AS
SELECT DISTINCT
  s.AUI AS source_aui,
  s.CODE AS source_code,
  upper(s.TTY) AS source_tty,
  s.STR AS source_name,
  s.SUPPRESS AS source_suppress,
  t.AUI AS target_aui,
  t.CODE AS target_code,
  upper(t.TTY) AS target_tty,
  t.STR AS target_name,
  t.SUPPRESS AS target_suppress,
  r.REL,
  r.RELA
FROM umls.mrrel r
JOIN umls.mrconso s ON s.AUI = r.AUI1
JOIN umls.mrconso t ON t.AUI = r.AUI2
JOIN mt4ds.rxnorm_allowed_tty_edges e
  ON e.source_tty = upper(s.TTY)
 AND e.target_tty = upper(t.TTY)
WHERE s.SAB = 'RXNORM'
  AND t.SAB = 'RXNORM'
  AND s.CODE IS NOT NULL
  AND t.CODE IS NOT NULL;
```

Indexes:

```sql
CREATE INDEX idx_rxnorm_tty_edges_source
ON mt4ds.rxnorm_tty_edges(source_aui, target_tty);

CREATE INDEX idx_rxnorm_tty_edges_code
ON mt4ds.rxnorm_tty_edges(source_code, source_tty);

CREATE INDEX idx_rxnorm_tty_edges_target
ON mt4ds.rxnorm_tty_edges(target_aui);
```

Open direction question:

- Legacy bulk traversal used raw `MRREL` direction `AUI1 -> AUI2` and then
  restricted traversal by target TTY.
- The topology table contains conceptual TTY adjacency that can be symmetric in
  places.
- The initial implementation should preserve legacy raw direction unless a
  reviewed test case proves that a specific reverse edge is required.

### RxNorm Runtime Query Shape

The runtime query should join prepared tables, not raw UMLS.

Option A: path-step recursive CTE over prepared edges:

```sql
WITH RECURSIVE
input_codes(code) AS (...),
base AS (
  SELECT a.*
  FROM mt4ds.atoms a
  JOIN input_codes i ON i.code = a.code
  WHERE a.source = 'RXNORM'
),
target_paths AS (
  SELECT * FROM mt4ds.rxnorm_tty_paths
  WHERE start_tty IN (SELECT tty FROM base)
),
walk(input_code, target_tty, path_id, step, aui, tty) AS (
  SELECT b.code, p.target_tty, p.path_id, 0, b.aui, b.tty
  FROM base b
  JOIN target_paths p ON p.start_tty = b.tty
  UNION ALL
  SELECT w.input_code, w.target_tty, w.path_id, w.step + 1,
         e.target_aui, e.target_tty
  FROM walk w
  JOIN mt4ds.rxnorm_tty_path_steps ps
    ON ps.path_id = w.path_id
   AND ps.step = w.step + 1
  JOIN mt4ds.rxnorm_tty_edges e
    ON e.source_aui = w.aui
   AND e.target_tty = ps.tty
)
SELECT ...
```

Option B: generated `UNION ALL` paths:

```sql
-- One SELECT per known path length/target, generated from topology.
SELECT ... FROM base b
JOIN mt4ds.rxnorm_tty_edges e1
  ON e1.source_aui = b.aui AND e1.target_tty = 'SCD'
JOIN mt4ds.rxnorm_tty_edges e2
  ON e2.source_aui = e1.target_aui AND e2.target_tty = 'SCDG'
UNION ALL
SELECT ...
```

Recommendation:

- Use Option A first because it keeps SQL smaller and source-agnostic.
- Keep recursion bounded by static path steps, not by arbitrary graph depth.
- Use Option B only if profiling shows the bounded recursive CTE is still too
  slow.

## Prepared Tables and Views

Raw UMLS tables are build inputs. Runtime services should query prepared tables.
The goal is not to create one table per workflow, but to create normalized
tables with stable shapes that make workflow queries composable.

Required materialized tables:

| Table | Purpose |
| --- | --- |
| `mt4ds.atoms` | Canonical source/code/AUI/CUI/TTY/name/suppress table with source-specific display ranking fields. |
| `mt4ds.best_atoms` | One or more ranked display atoms per source/code. |
| `mt4ds.hierarchy_edges` | Normalized source-specific hierarchy edges as `child -> parent`. |
| `mt4ds.walk_edges` | Shared runtime walk shape. Includes normal hierarchy edges and source-specific graph edges where practical. |
| `mt4ds.walk_closure_limited` | Optional bounded parent-walk closure derived only from `mt4ds.walk_edges` for high-volume lookup/walk/crosswalk/patient-friendly acceleration. |
| `mt4ds.same_cui_edges` | Same-CUI cross-source candidate mappings used as build input and compatibility fallback. |
| `mt4ds.crosswalk_edges` | Canonical runtime crosswalk table. Initially materializes exact same-CUI rows, and can later hold pre-ranked fallback mappings where build-time materialization is worthwhile. |
| `mt4ds.friendly_atoms` | MEDLINEPLUS/CHV candidate atoms with broad-name flags and TTY flags. |
| `mt4ds.rxnorm_allowed_tty_edges` | Static RxNorm topology adjacency table. |
| `mt4ds.rxnorm_tty_paths` | Static shortest paths from start TTY to target TTY with target order and match type. |
| `mt4ds.rxnorm_tty_path_steps` | Static per-step TTY rows for each path. |
| `mt4ds.rxnorm_tty_edges` | Materialized RxNorm AUI edges filtered to allowed TTY adjacency. |
| `mt4ds.cvx_metadata` | CVX display/group metadata when available. |
| `mt4ds.code_replacements` | Current-code replacement candidates for obsolete/historical codes. |
| `mt4ds.snomed_top_level_depth` | SNOMED broadness guard. |
| `mt4ds.patient_friendly_strategy` | Source policy rows: phases, targets, priority, stop-on-hit behavior. |

Useful views:

| View | Purpose |
| --- | --- |
| `mt4ds.v_active_atoms` | Active atoms only. |
| `mt4ds.v_lookup_atoms` | Lookup-ready display-ranked atoms. |
| `mt4ds.v_friendly_candidates` | Non-broad MEDLINEPLUS/CHV candidates. |
| `mt4ds.v_parent_edges` | Parent direction over `mt4ds.hierarchy_edges`. |
| `mt4ds.v_child_edges` | Child direction over `mt4ds.hierarchy_edges`. |
| `mt4ds.v_walk_edges` | Common runtime shape over source-specific walk edges. |

Views should not hide expensive raw UMLS joins. If a view is expensive and used
at runtime, make it a materialized table.

## Patient-Friendly Composition Requirements

Patient-friendly naming is a workflow over primitives.
It should not contain raw source graph mechanics. Source-specific graph rules
must already be encoded in `mt4ds.walk_edges`, `mt4ds.rxnorm_tty_edges`,
`mt4ds.friendly_atoms`, and source policy rows.

Patient-friendly resolution has one canonical execution mode today: live
prepared runtime resolution over prepared primitive tables. It may use
`mt4ds.walk_closure_limited` for bounded parent walks, but it must not change
candidate semantics.

The former candidate/path/final-resolution materialization implementation has
been archived under `archive/legacy/patient_friendly_materialization/`. Future
materialization must either consume runtime resolver output directly or share
the same SQL relation builders as runtime resolution.

Current stabilization decision: use live prepared runtime resolution for batch
and export. On 2026-06-08 it resolved 1,127,094 reviewed production codes in
3:45.57 wall time with `memory-profile fast`. On the current prepared DB, the
benchmark gate still runs in about 25 seconds.
seconds with no row-level regression against the reviewed baseline, and
processed 1,186,645 codes across ICD10CM, RXNORM, LNC, CVX, CPT, and
SNOMEDCT_US in about 7.1 minutes of query time. Building
`mt4ds.walk_closure_limited` took about 2.1 minutes, for about 9.2 minutes
setup-plus-run.

Runtime patient-friendly uses a common shape:

```text
input_codes
  -> lookup rows from mt4ds.best_atoms
  -> source strategy phases from mt4ds.patient_friendly_strategy
  -> walk frontiers from mt4ds.walk_edges, mt4ds.walk_closure_limited, or mt4ds.rxnorm_tty_edges
  -> crosswalk candidates from mt4ds.crosswalk_edges
  -> candidate names from mt4ds.friendly_atoms or source-native target atoms
  -> shared frontier/ranking selection
```

The source-specific part is primarily the prepared data and strategy rows. For
example, RxNorm should not require patient-friendly runtime code to know how to
walk `SBD -> SCD -> SCDG`; it should request the configured RxNorm phase
`target_tty='SCDG'` and walk prepared RxNorm TTY edges.

Candidate rows should include at least:

```text
source, code, candidate_name, candidate_source, match_type, match_depth,
candidate_origin, walk_source, walk_code, walk_depth, target_source,
target_code, rank_features, policy_version
```

For source-native MEDLINEPLUS/CHV hits on the input code's own CUI, use
`match_type='exact'`, `match_depth=0`, and
`candidate_origin='exact_same_cui'`. Reserve `broader` for accepted candidates
found after walking at least one hierarchy edge.
For cross-source same-CUI target routes, use
`candidate_origin='same_cui_crosswalk'`; do not label them as native hierarchy
unless a hierarchy edge was actually traversed.

Resolution rows should include at least:

```text
source, code, name, friendly_source, match_type, match_depth,
technical_name, selected_candidate_id, policy_version, generated_at
```

### ICD10CM, ICD10PCS, HCPCS, CPT

Workflow:

1. `lookup(input)`
2. `walk(source, parents/ancestors)`
3. At each depth frontier:
   - find MEDLINEPLUS/CHV candidates
   - reject broad names
   - choose from the closest frontier with any acceptable MEDLINEPLUS/CHV
     candidate
   - prefer MEDLINEPLUS over CHV only within that same frontier
   - stop at the first acceptable frontier
4. If source-native walk misses:
   - `crosswalk(source -> SNOMEDCT_US)`
   - `walk(SNOMEDCT_US, ancestors)` with top-level guard
   - apply same friendly candidate selection
5. If still miss, return original display.

ICD and CPT/HCPCS hierarchy must use UMLS-derived edges only. Prefix-derived
or range-inferred edges are not valid patient-friendly fallback edges unless the
UMLS source contains the relationship and preparation normalizes it.

This is the required ordering for ICD10CM, ICD10PCS, CPT, and HCPCS: walk the
source hierarchy first, then fallback crosswalk to SNOMEDCT_US and walk SNOMED
only under the guarded SNOMED policy. The SNOMED fallback must not replace a
valid source-native candidate with a broader or less relevant SNOMED candidate.

### LOINC

LOINC keeps its source-specific patient-friendly tiers:

1. component/axis/common-name logic
2. source-native hierarchy where available
3. SNOMED fallback when source-native tiers miss
4. original display

The LOINC rules should still call the same lookup, walk, crosswalk, and select
primitives where possible. After the LOINC-specific tiers, LNC follows the same
ordering principle as ICD/CPT/HCPCS: walk LNC first, then fallback crosswalk to
SNOMEDCT_US and walk SNOMED only under the guarded SNOMED policy.

### SNOMEDCT_US

Workflow:

1. Crosswalk SNOMED to target sources in priority order:
   - `RXNORM` for explicit same-CUI drug/product target routes
   - `ICD10CM`
   - `ICD10PCS`
   - `LNC`
   - `CPT`
   - `HCPCS`
2. For an RxNorm target, apply the RxNorm patient-friendly TTY strategy.
3. For each non-RxNorm target source candidate, walk the target source hierarchy first and
   apply the same MEDLINEPLUS/CHV frontier selection used for native target
   source inputs.
4. If a target-source route does not produce an acceptable friendly candidate,
   fallback crosswalk from that target route to SNOMED and walk SNOMED under the
   guarded SNOMED policy.
5. If no target-source route produces an acceptable friendly candidate, perform
   a direct guarded SNOMED walk for MEDLINEPLUS/CHV.
6. If still miss, return SNOMED preferred display.

This is not recursive patient-friendly composition. It is bounded candidate
generation with explicit route origins:
`snomed_to_target_native_hierarchy`, `snomed_to_target_snomed_fallback`,
`direct_snomed_guarded_walk`, and `original`. Each route may enter guarded
SNOMED fallback at most once.

### RXNORM

Workflow:

1. `lookup(RXNORM)`
2. `walk(RXNORM, target_tty='SCDG')` for the explicit group-target TTY set
3. `walk(RXNORM, target_tty='MIN'/'IN')` according to source TTY
4. `select(best RxNorm target)`
5. return original display if no target applies

No MEDLINEPLUS/CHV lookup is used for direct RxNorm patient-friendly output.
The RxNorm runtime workflow should still use the common strategy-table shape:

| Source | Phase | Walk kind | Target | Match type | Priority |
| --- | --- | --- | --- | --- | --- |
| `RXNORM` | group | `tty_path` | `SCDG` | `group` | 0 |
| `RXNORM` | ingredient_pin_scdc | `tty_path` | `IN` | `ingredient` | 1 |
| `RXNORM` | ingredient_pin_scdc | `tty_path` | `MIN` | `ingredient` | 2 |
| `RXNORM` | ingredient_default | `tty_path` | `MIN` | `ingredient` | 1 |
| `RXNORM` | ingredient_default | `tty_path` | `IN` | `ingredient` | 2 |

### CVX

Workflow:

1. `lookup(CVX)` with group enrichment when available
2. select CVX group/display
3. return original CVX display if no group metadata is available

CVX is a source-specific lookup/enrichment workflow, not a hierarchy fallback
workflow.
Its source-specific behavior should be encoded in lookup/display preparation:
`mt4ds.cvx_metadata` and display ranking in `mt4ds.best_atoms`.

### Source-Specific Policy To Preserve

The materialized pipeline must preserve the useful behavior already identified
in legacy `medterm` and current `medterm4ds` work:

| Source | Required policy |
| --- | --- |
| `ICD10CM`, `ICD10PCS` | Native UMLS parent walk first; MEDLINEPLUS/CHV candidate selection by closest frontier; guarded SNOMED fallback only after native miss; no prefix-inferred hierarchy. |
| `HCPCS`, `CPT` | Source hierarchy and display ranking remain deterministic; walk CPT/HCPCS hierarchy first, then use explicit guarded SNOMED fallback only after native miss. |
| `LNC` | Component/axis/common-name tiers are preserved, then LNC hierarchy is walked before guarded SNOMED fallback. |
| `SNOMEDCT_US` | Crosswalk to `ICD10CM`, `ICD10PCS`, `LNC`, `CPT`, and `HCPCS` first; walk each target source hierarchy before any SNOMED fallback; only then use target-to-SNOMED guarded fallback or direct guarded SNOMED walk. |
| `RXNORM` | TTY topology and target strategy are explicit; direct output is RxNorm source-native, not MEDLINEPLUS/CHV. |
| `CVX` | Vaccine group/display enrichment is lookup metadata, with original display fallback. |
| `MEDLINEPLUS`, `CHV` | Heading and broad-name flags are computed during preparation and used during selection; broad rows are retained for audit. |

## Batch Query Composition

For performance and maintainability, primitive workflows and
patient-friendly candidate generation should run as batch queries over prepared
tables. Runtime patient-friendly lookup uses the live prepared resolver. Future
materialized lookup must be generated from runtime-equivalent semantics and
should not introduce a second resolver implementation.

Candidate-generation shape:

```text
Python service:
  normalize inputs
  group by source
  call one source workflow query per source
  merge results in input order
```

Source query shape:

```sql
WITH
input_codes AS (...),
lookup AS (...),
walk_frontier AS (...),
crosswalk_hits AS (...),
friendly_candidates AS (...),
ranked AS (...)
SELECT ...
```

Patient-friendly candidate-generation skeleton:

```sql
WITH RECURSIVE
input_codes(source, code, input_order) AS (...),
lookup AS (
  SELECT i.input_order, i.source, i.code,
         a.aui, a.cui, a.tty, a.name AS technical_name
  FROM input_codes i
  LEFT JOIN mt4ds.best_atoms a
    ON a.source = i.source
   AND a.code = i.code
   AND a.rank = 1
),
strategy AS (
  SELECT *
  FROM mt4ds.patient_friendly_strategy
  WHERE source IN (SELECT DISTINCT source FROM input_codes)
),
source_walk(input_order, phase, walk_source, walk_code, walk_aui, walk_cui, depth) AS (
  SELECT l.input_order, s.phase, l.source, l.code, l.aui, l.cui, 0
  FROM lookup l
  JOIN strategy s
    ON s.source = l.source
   AND s.walk_kind IN ('self', 'parents')
  UNION ALL
  SELECT w.input_order, w.phase, e.source, e.to_code, e.to_aui, e.to_cui, w.depth + 1
  FROM source_walk w
  JOIN mt4ds.walk_edges e
    ON e.source = w.walk_source
   AND e.from_aui = w.walk_aui
   AND e.direction = 'parent'
  JOIN strategy s
    ON s.phase = w.phase
   AND s.source = e.source
  WHERE w.depth < s.max_depth
),
friendly_hits AS (
  SELECT w.input_order, w.phase, w.depth,
         f.name, f.friendly_source, f.tty,
         'broader' AS match_type
  FROM source_walk w
  JOIN mt4ds.friendly_atoms f
    ON f.cui = w.walk_cui
  WHERE f.is_broad = false
),
ranked AS (
  SELECT *,
         row_number() OVER (
           PARTITION BY input_order
           ORDER BY depth,
                    CASE friendly_source WHEN 'MEDLINEPLUS' THEN 0 ELSE 1 END,
                    name
         ) AS rn
  FROM friendly_hits
)
SELECT ...
FROM lookup l
LEFT JOIN ranked r ON r.input_order = l.input_order AND r.rn = 1;
```

This skeleton is not intended to be the exact final SQL. It defines the desired
construction pattern:

- source inputs are batched
- source-specific hierarchy rules are already in `mt4ds.walk_edges`
- source strategy rows control phases and target behavior
- candidate selection is shared
- source-specific SQL is limited to special prepared edge families, such as
  RxNorm TTY path walking

RxNorm uses the same composition principle, but the walk CTE reads
`mt4ds.rxnorm_tty_paths`, `mt4ds.rxnorm_tty_path_steps`, and
`mt4ds.rxnorm_tty_edges` instead of `mt4ds.walk_edges`.

This is preferable to:

- one Python query per code
- one huge all-source query with every rule embedded
- recursive queries over raw `mrrel`

## Performance Requirements

1. All public primitives must support batch input.
2. Single-code use must call the same batch implementation.
3. Patient-friendly runtime lookup must use the live prepared resolver unless
   an explicitly validated runtime-equivalent materialized table is added later.
4. Runtime queries must use prepared tables where expensive raw UMLS joins would
   otherwise be repeated.
5. Recursive traversal must be bounded by:
   - max depth
   - source
   - relationship type
   - static path steps for RxNorm TTY traversal
6. Full source exports should stream inputs and write output incrementally.
7. Batch workflows should checkpoint/report progress by source and chunk.
8. Query plans must be testable against real UMLS data for:
   - ICD10CM hierarchy fallback
   - LOINC source-native fallback
   - RxNorm topology traversal
   - SNOMED top-level guard
   - CPT/HCPCS mappings
9. Build-time preparation may be expensive, but runtime patient-friendly,
   lookup, walk, and crosswalk queries should avoid raw full-table recursive
   joins.
10. A 5,000-row patient-friendly benchmark against an already prepared
    resolution table should be a seconds-scale join/reporting task. Against the
    live prepared resolver with `walk_closure_limited`, it should stay near the
    reviewed 21-second runtime unless semantic policy changes require review.

## Edge Cases

Required edge cases:

1. ICD10CM heading/range atoms:
   - Example: `L30.1 -> L30 -> L20-L30 -> CHV`
   - Heading/range atoms must remain in hierarchy edges.
2. ICD10CM to SNOMED fallback:
   - Example: `M99.75 -> SNOMED 203715007 -> 88230002 -> CHV`
3. ICD10CM pregnancy fallback:
   - Example: `O26.7 -> SNOMED 199308008 -> 263038009 -> 263012009 -> CHV`
4. SNOMED guard:
   - Do not walk up into top-level depths 1-3 for fallback expansion.
   - Example: `ICD10CM:S43` must not jump to unrelated `Head Injuries` unless
     UMLS provides an explicit defensible route.
5. RxNorm group:
   - `1149364` should resolve through `SBD -> SCD -> SCDG`.
6. RxNorm ingredient:
   - `1604333` should resolve through `SBDC -> BN -> IN`.
7. RxNorm direct group:
   - `1658659` should resolve through `SCD -> SCDG`.
8. RxNorm `IN` and `MIN`:
   - `IN` stays itself.
   - `MIN` stays itself.
   - `PIN` tries `IN`, then `MIN`.
9. SNOMED drug/product routes:
   - Explicit SNOMED drug/product crosswalks should use the RxNorm strategy
     before falling back to broad CHV/SNOMED labels.
10. CVX combination vaccines:
   - Multiple CVX group metadata rows for one vaccine should aggregate into a
     combination label such as `DTAP / HIB / HepB`, not pick one component.
11. CPT generic guards:
   - Generic labels such as `Operation`, `current procedural terminology`, and
     `biochemical test` should not replace a useful CPT display.
   - Example: `CPT:50580` should still reach `CHV:0000019534` / `nephroscopy`
     through the UMLS CPT hierarchy.
12. NDC:
   - normalize to 11 digits
   - preserve leading zeros
   - support dashed and undashed inputs
   - resolve obsolete/historical NDCs where data supports it
13. Obsolete/historical codes:
   - support active-only and resolve-current modes
   - expose resolution provenance
14. Broad friendly names:
   - reject broad MEDLINEPLUS/CHV labels such as top-level disease/anatomy terms
   - do not reject deeper acceptable MEDLINEPLUS labels simply because a
     shallower CHV candidate exists

## Testing Requirements

### Unit Tests

1. Source hierarchy edge extraction for each supported source.
2. RxNorm topology path generation.
3. RxNorm TTY edge materialization over synthetic data.
4. SNOMED top-level guard behavior.
5. Friendly candidate frontier selection.
6. Crosswalk provenance.
7. Obsolete/NDC resolution.

### Real-Data Smoke Tests

Run against both:

- legacy 2025AB DB in `/mnt/d/medterm/data/umls_local.duckdb`
- current medterm4ds DB built from the selected UMLS release

Smoke tests:

1. `lookup`
2. `walk`
3. `crosswalk`
4. `patient_friendly`
5. `optimize`
6. `ConceptMap` export

### Parity Tests

Parity tests should distinguish:

1. algorithm differences
2. UMLS release differences
3. loader/build differences
4. intentional bug fixes

Do not use a single full benchmark result as the only signal. Use focused
source-specific cases first, then broader sampled/full reports after the
primitive is stable.

## Existing Assets To Reuse

Several pieces of the current work remain useful and should not be discarded.

Reusable medterm4ds assets:

1. Public models:
   - `CodeRef`
   - `CodeInfo`
   - `CodeMapping`
   - `CodeRelation`
   - `CodeResolution`
   - `FriendlyNameResult`
   - `ConceptMapRow`
   - `Provenance`
2. Public service/API/CLI/MCP surfaces, as adapters over the future normalized
   primitives.
3. Data setup/download scaffolding, with the corrected Metathesaurus Full
   Subset release type and raw-data storage under `data/`.
4. Output serializers:
   - CSV/JSON/JSONL
   - compact renderers
   - FHIR R4 ConceptMap export
5. Existing edge-case tests and parity scripts, after they are updated to target
   normalized primitives.

Reusable legacy medterm assets:

1. `/mnt/d/medterm/src/medterm/bulk/transforms/patient_friendly_refactored.py`
   as the behavioral template for source-specific patient-friendly logic.
2. RxNorm TTY topology and deterministic traversal behavior.
3. LOINC component/common-name tiers.
4. SNOMED routing priority to `ICD10CM`, `ICD10PCS`, `LNC`, `CPT`, and
   `HCPCS`, with target source hierarchies walked before guarded SNOMED
   fallback.
5. MEDLINEPLUS/CHV broad-name guard concepts.
6. Combo-name guard concepts that prevent combination drugs or procedures from
   selecting unrelated CHV terms with no meaningful token overlap.
7. Source-specific original/display fallback behavior for codes without a
   defensible friendly candidate.

Internals to replace:

1. Broad recursive runtime SQL over raw `mrrel`/`mrconso`.
2. Patient-friendly source-specific graph mechanics embedded directly in one
   engine method.
3. Separate bulk-only transform logic that diverges from public services.
4. Full benchmark comparisons as the first debugging step for a source-specific
   issue.

## Open Questions

1. Should RxNorm edge materialization include raw reverse `MRREL` direction, or
   only legacy `AUI1 -> AUI2` direction?
2. Which suppressed RxNorm atoms are allowed as final targets versus only
   intermediates?
3. Should `mt4ds.rxnorm_tty_edges` filter by specific RxNorm `REL`/`RELA`
   values, or by allowed TTY adjacency only?
4. Should the 2025AB parity DB be rebuilt by medterm4ds, or should we treat
   `/mnt/d/medterm/data/umls_local.duckdb` as the fixed parity fixture?
5. Which prepared tables are required for 0.0.1 versus later releases?
6. If static serving later requires materialization, should it bulk-insert
   runtime resolver output or first expose shared SQL relation builders?
7. What policy version and build metadata should be included in any future
   materialized resolution row?

These questions should be answered with small real-data examples before the
full patient-friendly benchmark is rerun.
