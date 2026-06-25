# Tier C: Engine and Architecture Refactor Plan

## Context

The deep-dive review (2026-06-25) surfaced four HIGH-severity architecture findings:

1. `engines/duckdb/engine.py` is **5362 lines** with parallel `_prepared` and non-`_prepared` code paths for hierarchy, mappings, and target hierarchy operations. It's a god-class that is the system's biggest maintenance risk.

2. The "prepared primitives" layer in `services/walk.py`, `services/selection.py`, `services/crosswalk_prepared.py` is largely **dead code internally** — only `selection.rank_candidates` is called from within `src/` (once). The engine re-implements the same SQL inline instead of composing primitives, contradicting `docs/architecture.md:148-149`.

3. `domains/terminology.py:260, 726` reaches past the engine protocol via `getattr(engine, "con", None)` to run raw UMLS SQL for `drugs_for_indication` and `_ndcs_for_rxcuis`. This contradicts the protocol-driven design (`docs/architecture.md:40-42, 258`).

4. Constants `_SNOMED_TARGET_PRIORITY` (`engine.py:53`) and `_BROAD_CHV_NAMES` (`engine.py:164`) duplicate the canonical versions in `sources/snomed.py:17` and `sources/base.py:11`. `services/patient_friendly_prepared.py` imports the canonical versions; the engine does not.

The regression suite (committed in `e4de89d`) provides the safety net: 72 tests across 5 tiers validate that any change to the engine, services, or build scripts is visible. All four findings can now be addressed with low risk.

**Intended outcome**: a codebase where the engine is split into focused modules, primitives are actually used, the domain layer composes services instead of running SQL, and constants have one canonical home. Each phase is independently shippable and reversible.

## Architecture decisions (locked in)

| Decision | Choice |
|---|---|
| Plan depth | Formal (this document) |
| Branching | Per-phase branches with PR at each gate |
| Performance gate | Yes — patient_friendly build must complete in <10 min (baseline 7.5 min) |

## Gate composition

Every phase must pass the **standard gate** before merge. The standard gate is:

```bash
# Fast sanity check (~7s) — run after every change during development
pytest tests/regression/test_patient_friendly_regression.py -v -m realdb

# Standard commit gate (~10 min) — required before merge
pytest -m "realdb or fhir4px_smoke" -v
```

Plus the **performance gate**:

```bash
# Measure patient_friendly build time; record to reports/perf/tier-c-baseline.json
PYTHONPATH=src python3 scripts/build_fhir4px_patient_friendly.py --output-dir /tmp/perf-check
# Must complete in <600s (10 min). Baseline: ~450s (7.5 min) on 2026AA.
```

The performance gate is enforced manually per phase (no automated fail; perf regressions are flagged in the PR for review). The regression suite itself takes ~10 min and effectively re-runs the build, so perf timing is observable in the suite output.

** xfails policy**: zero. After Tier A, the suite has 0 xfails. Any new xfail during Tier C requires explicit justification in the PR.

## Phase 0: Pre-flight (no code changes)

**Branch**: none (read-only verification)

**Steps**:
1. Verify regression suite is green: `pytest -m "realdb or fhir4px_smoke" -v` → expect 72 passed.
2. Capture baseline perf: time `scripts/build_fhir4px_patient_friendly.py` three times. Record median to a new file `reports/perf/tier-c-baseline.json` (this file is gitignored under `reports/`; keep it locally for comparison).
3. Confirm gate hygiene: `pytest --markers` lists `realdb`, `slow`, `fhir4px_smoke`.

**Gate**: this phase has no gate; it's measurement only.

## Phase 1: Consolidate duplicate constants

**Branch**: `tier-c-constants`

**Problem**: `engines/duckdb/engine.py:53, 164` redefine `_SNOMED_TARGET_PRIORITY` and `_BROAD_CHV_NAMES` that already exist canonically in `sources/snomed.py:17` and `sources/base.py:11`. The two copies can drift silently.

**Changes**:
- `engines/duckdb/engine.py`: delete the local `_SNOMED_TARGET_PRIORITY` and `_BROAD_CHV_NAMES` definitions; import from `sources.snomed` and `sources.base` instead. Update references at `engine.py:4499, 5336` to use the imported names.
- Audit other duplicated constants: `_BROAD_CHV_NAME_SQL` (`engine.py:182`) builds SQL at import time from the set; keep this derived form but reference the canonical set.
- Add a sanity test in `tests/test_source_strategies.py` (existing file) that asserts `LocalDuckDBEngine` uses the same objects as `sources.*` (identity check via `is`).

**Files touched**: `engines/duckdb/engine.py`, `tests/test_source_strategies.py`.

**Risk**: low. Constants are pure data; if values match, behavior is identical.

**Gate**: standard gate + perf gate.

## Phase 2: Extract hierarchy subsystem

**Branch**: `tier-c-engine-hierarchy`

**Problem**: `engine.py` has both `_get_source_code_relations` (non-prepared, lines 1894-2029) and `_get_source_code_relations_prepared` (2029-2155), plus `_source_display_lookup` (2155-2198). These ~300 lines form a coherent hierarchy subsystem.

**Changes**:
- New file: `engines/duckdb/hierarchy.py` — move the three methods plus any hierarchy-only helpers (`_source_hierarchy_family`, `_source_hierarchy_join_sql` at module level if not used elsewhere).
- The functions take `con` (DuckDBPyConnection) and required parameters explicitly; no longer methods on `LocalDuckDBEngine`.
- `engine.py::get_code_relations` becomes a thin delegation: picks prepared vs non-prepared based on cache state, calls the new module.
- Re-export from `engines/duckdb/__init__.py` if downstream code expects it (probably not — these are private methods).

**Files touched**: `engines/duckdb/engine.py` (shrink by ~300 lines), new `engines/duckdb/hierarchy.py`.

**Risk**: medium. The methods are private but tightly coupled to engine state (`_active_source_code_set`, `_snomed_top_level_depth` cache). Need to pass these explicitly or expose read accessors.

**Mitigation**: keep the methods stateless; pass needed state as arguments. If a method needs `_active_source_code_set(source)`, call it on the engine and pass the resulting set.

**Gate**: standard gate + perf gate.

## Phase 3: Extract mapping subsystem

**Branch**: `tier-c-engine-mappings`

**Problem**: The mapping methods are the largest parallel-path block:
- `_get_source_code_mappings` (2198) + `_get_source_code_mappings_prepared` (2353)
- `_get_source_ancestor_mappings` (2384) + `_get_source_ancestor_mappings_prepared` (2620)
- `_get_target_hierarchy_mappings` (2652) + `_get_target_hierarchy_mappings_prepared` (2901)
- `_filter_snomed_top_level_mappings` (1220)
- `_map_cpt_targets` (4278), `_map_snomed_codes` (4373), `_map_snomed_broader` (4508)

Total ~1500 lines.

**Changes**:
- New file: `engines/duckdb/mappings.py` — move all mapping methods.
- Engine's `get_code_mappings` becomes a dispatcher.
- Same pattern as Phase 2: methods become stateless functions taking `con` + explicit state.

**Files touched**: `engines/duckdb/engine.py` (shrink by ~1500 lines), new `engines/duckdb/mappings.py`.

**Risk**: medium-high. Larger surface area, more coupling points. May discover implicit dependencies on engine private state.

**Mitigation**: do this in two sub-PRs if needed (source-side mappings first, then target-hierarchy mappings).

**Gate**: standard gate + perf gate.

## Phase 4: Extract resolution subsystem

**Branch**: `tier-c-engine-resolution`

**Problem**: Code resolution (active/historical/obsolete/NDC) is its own subsystem:
- `_resolve_code` (1248), `_resolve_ndc` (1421), `_lookup_any_code` (1557)
- `_replacement_candidates` (1630), `_active_source_code_set` (1402)
- `_ReplacementCandidate` class (267)

Total ~600 lines.

**Changes**:
- New file: `engines/duckdb/resolution.py` — move the resolution logic and the `_ReplacementCandidate` dataclass.
- Engine's `resolve_codes` delegates.

**Files touched**: `engines/duckdb/engine.py` (shrink by ~600 lines), new `engines/duckdb/resolution.py`.

**Risk**: medium. `_replacement_candidates` may have hidden coupling to engine caches.

**Gate**: standard gate + perf gate.

## Phase 5: Extract patient-friendly resolvers (the big one)

**Branch**: `tier-c-engine-patient-friendly`

**Problem**: The patient-friendly resolver is the largest subsystem (~3000 lines):
- `_resolve_source` (3113), `_resolve_default` (3126), `_apply_snomed_fallback` (3265), `_resolve_default_via_snomed` (3294)
- `_resolve_rxnorm` (3645), `_resolve_loinc` (3980), `_resolve_cpt` (4109), `_resolve_cvx` (4304), `_resolve_snomed` (4635)
- `_display_name` (4711), `_technical_name` (4737)
- The `_Row` dataclass (244)

This phase is large enough to warrant sub-phases. **Recommended split**:

**5a** — non-RxNorm resolvers (`_resolve_default`, `_resolve_loinc`, `_resolve_cpt`, `_resolve_cvx`, `_resolve_default_via_snomed`, `_apply_snomed_fallback`).
**5b** — RxNorm resolver (`_resolve_rxnorm`).
**5c** — SNOMED resolver (`_resolve_snomed`, `_map_snomed_codes`, `_map_snomed_broader`).

Each sub-phase is its own branch + PR.

**Changes**:
- New file: `engines/duckdb/patient_friendly.py` — move resolver methods.
- `_Row` dataclass moves with them.
- Engine's `get_patient_friendly_names` and `_get_patient_friendly_names_prepared` become dispatchers.

**Files touched**: `engines/duckdb/engine.py` (final shrink — should end up ~1000-1500 lines), new `engines/duckdb/patient_friendly.py` (~3000 lines, but focused).

**Risk**: high. This subsystem has the most coupling (uses hierarchy, mapping, and resolution methods). Likely to surface hidden dependencies.

**Mitigation**: sub-phases (5a/5b/5c). Each sub-phase has its own gate. Stop and reassess after 5a if coupling is worse than expected.

**Gate**: standard gate + perf gate, per sub-phase.

## Phase 6: Wire up prepared primitives

**Branch**: `tier-c-primitives-wiring`

**Problem**: After Phase 5, the engine is smaller and the prepared paths are easier to see. The primitives in `services/walk.py` (`get_parents_prepared`, `get_ancestors_prepared`, `get_children_prepared`, `get_descendants_prepared`) and `services/selection.py` (`rank_candidates`, `select_frontier`) are exported but unused outside `__init__.py` re-exports (except `selection.rank_candidates` which is called once internally).

**Changes**:
- In `engines/duckdb/hierarchy.py` (created in Phase 2): replace inline `walk_edges` SQL with calls to `services.walk.get_parents_prepared` / `get_ancestors_prepared`.
- In `engines/duckdb/patient_friendly.py` (created in Phase 5): replace candidate ranking logic with `services.selection.rank_candidates` / `select_frontier` where appropriate.
- If the primitives' signatures don't match what the engine needs, extend them rather than duplicating logic.

**Files touched**: `engines/duckdb/hierarchy.py`, `engines/duckdb/patient_friendly.py`, possibly `services/walk.py` and `services/selection.py` (signature extensions).

**Risk**: medium. The primitives may have subtle differences from the inline SQL (e.g., different default max_depth, different ordering). The regression suite + golden parity test will surface any behavioral drift.

**Mitigation**: replace one call site at a time. Run the full gate after each replacement.

**Gate**: standard gate + perf gate. Special attention to Tier 4 (golden content parity) — any field-level drift is a real semantic change.

**Decision point**: if a primitive can't be made to match the inline SQL without extensive changes, document the divergence and either (a) keep both code paths with a comment explaining why, or (b) delete the unused primitive. Don't leave the dead code in place.

## Phase 7: Move `drugs_for_indication` SQL out of the domain layer

**Branch**: `tier-c-domain-sql-move`

**Problem**: `domains/terminology.py:260, 726` reach past the engine protocol via `getattr(engine, "con", None)` to run raw UMLS SQL. The `drugs_for_indication` function builds a 200+ line recursive CTE inline in the domain layer.

**Changes**:
- Add a new method to the engine protocol (`engines/base.py::TerminologyEngine`): `get_drugs_for_indication(condition, source, code, relationships, max_depth, include_product_groups) -> list[ConditionMedication]` (or similar typed result).
- Implement in `LocalDuckDBEngine` (or in a new `engines/duckdb/indications.py` module if Phase 5 created a precedent for sub-modules).
- Add a corresponding result model in `core/models.py` if the dict[str, Any] return is too loose.
- `domains/terminology.py::drugs_for_indication` becomes a thin wrapper that calls `engine.get_drugs_for_indication(...)` and formats the result.
- Same pattern for `_ndcs_for_rxcuis` (terminology.py:726).

**Files touched**: `engines/base.py`, `engines/duckdb/engine.py` (or new module), `domains/terminology.py`, possibly `core/models.py`.

**Risk**: low-medium. The SQL itself doesn't change; only its location. The domain layer becomes properly thin.

**Mitigation**: Tier 1 clinical fixtures validate the public API end-to-end. If the move changes the result format, tests fail.

**Gate**: standard gate + perf gate. May need to add new Tier 1 fixtures specifically for `drugs_for_indication` if not already covered (currently only the MCP tool registration is tested).

**Dependency**: this phase moves SQL that's analogous to `build_fhir4px_associations.py` (the may_treat/may_prevent traversal). After this phase, consider a parity test between the two implementations as a follow-up (mentioned in the regression plan as deferred).

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Silent semantic change in extracted code | Medium | High | Tier 4 golden parity catches any field-level drift |
| Performance regression | Low-Medium | Medium | Perf gate; also observable in build smoke test timing |
| Hidden coupling surfaces mid-extraction | High | Medium | Sub-phases for patient-friendly; stop-and-reassess at each gate |
| Breaking downstream consumers (fhir4px build) | Low | High | Tier 2 build smoke tests the real build pipeline end-to-end |
| Primitive signature mismatch (Phase 6) | Medium | Low | Extend the primitive rather than duplicate; document if divergence is intentional |
| Branch drift during long-running refactor | Medium | Low | Rebase each phase branch on `main` before merge; keep phases small |

## Verification (end of Tier C)

1. **All phase gates green**: each phase's PR passed standard gate + perf gate.
2. **Final regression run**: `pytest -m "realdb or fhir4px_smoke" -v` → 72 passed, 0 failed, 0 xfailed.
3. **Engine size**: `wc -l src/medterm4ds/engines/duckdb/engine.py` → target <2000 lines (was 5362).
4. **No protocol leakage**: `grep -rn 'getattr(engine, "con"' src/medterm4ds/` → returns nothing.
5. **No duplicate constants**: `_SNOMED_TARGET_PRIORITY` and `_BROAD_CHV_NAMES` defined in exactly one place each.
6. **Primitives are used**: `grep -rn "get_parents_prepared\|rank_candidates" src/medterm4ds/engines/` → returns real call sites, not just re-exports.
7. **Perf**: patient_friendly build time within 10% of Phase 0 baseline.
8. **Update `docs/architecture.md`** to reflect the new engine structure (sub-modules + primitive usage).

## Sequencing summary

| Phase | Branch | Estimated effort | Risk |
|---|---|---|---|
| 0 | (none) | 30 min | none |
| 1 | `tier-c-constants` | 1-2 hours | low |
| 2 | `tier-c-engine-hierarchy` | 4-6 hours | medium |
| 3 | `tier-c-engine-mappings` | 6-8 hours | medium-high |
| 4 | `tier-c-engine-resolution` | 3-4 hours | medium |
| 5a | `tier-c-engine-pf-non-rxnorm` | 6-8 hours | high |
| 5b | `tier-c-engine-pf-rxnorm` | 4-6 hours | high |
| 5c | `tier-c-engine-pf-snomed` | 4-6 hours | high |
| 6 | `tier-c-primitives-wiring` | 4-6 hours | medium |
| 7 | `tier-c-domain-sql-move` | 3-4 hours | low-medium |
| **Total** | | **~40-50 hours** | |

This is roughly 1-2 weeks of focused work. Phases can be paused between any two without leaving the codebase in a broken state — each phase ends green.

## Files to create/modify

**New files** (created across phases):
- `engines/duckdb/hierarchy.py` (Phase 2)
- `engines/duckdb/mappings.py` (Phase 3)
- `engines/duckdb/resolution.py` (Phase 4)
- `engines/duckdb/patient_friendly.py` (Phase 5a-c)
- Possibly `engines/duckdb/indications.py` (Phase 7)
- Possibly `core/models.py` additions for `ConditionMedication` (Phase 7)

**Modified files**:
- `engines/duckdb/engine.py` (every phase; shrinks dramatically)
- `engines/base.py` (Phase 7 — protocol extension)
- `domains/terminology.py` (Phase 7 — becomes thin wrapper)
- `docs/architecture.md` (final phase — document new structure)
- `tests/test_source_strategies.py` (Phase 1 — add identity assertion)

**Reference files** (read-only, provide context):
- `services/walk.py`, `services/selection.py`, `services/crosswalk_prepared.py` (Phase 6 — primitives to wire)
- `sources/snomed.py`, `sources/base.py` (Phase 1 — canonical constant sources)
- `docs/regression-test-plan.md` (gate definitions and tier structure)

## After Tier C

The regression suite + Tier C refactor together leave the codebase in a maintainable state: focused modules, used primitives, no protocol leakage, no duplicate constants. The remaining review findings (security hardening, API auth, test gaps in `core/models.py` and `engines/duckdb/engine.py` direct tests) can be tackled as separate efforts with the same regression-gated workflow.
