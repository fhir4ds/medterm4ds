# Regression Test Suite for `medterm4ds` — Pre-Refactor Safety Net

## Context

`medterm4ds` is a UMLS-backed medical terminology package whose `scripts/build_fhir4px_*.py` pipeline produces four classes of deliverables (~1GB total) consumed by downstream teams:

- **Patient-friendly names** for 1.13M codes (per-source JSONs + combined CSV)
- **Embedding indices** for 630K codes across 6 categories (condition/lab/medication/procedure/vaccine/body_structure)
- **Condition associations** (102K conditions → 2.86M medication associations)
- **RxNorm ingredient decomposition** (63K products)

A separate deep-dive review surfaced ~40 findings (3 bugs in uncommitted `drugs_for_indication` diff, 9 HIGH-severity architecture violations including protocol leakage, zip-slip, no API auth, and a 5146-line god-class engine with parallel code paths). Before any of those fixes land, we need a regression suite so changes are visible. The user has confirmed: real DB at `umls_current.duckdb`, all four deliverables, **full content pinning of every record**, plan-first workflow.

The existing test suite (35 files, ~290 tests) is hermetic — every test uses synthetic in-memory data. **Zero tests touch `umls_current.duckdb`**; zero cover any `build_fhir4px_*.py` script. There is no `pytest` marker system, no coverage config, no golden-file precedent. Importantly: `reports/` is gitignored — the 1GB+ of outputs lives on disk only, with just README, spec, and `crosswalk_examples.json` committed.

## Scope

**In scope (this plan):**
- Tiers 1, 2, 2.5, 3 — structural / count / invariant coverage of every record in every output.
- **Tier 4 — full content golden pinning** of every record in every fhir4px output.
- UMLS release pinning (exact counts + release tag, no tolerance).
- `realdb`/`slow`/`fhir4px_smoke` markers wired into CI as opt-in.

**Deferred:**
- Parity test between uncommitted `drugs_for_indication` SQL and `build_fhir4px_associations.py` inline SQL — needs curated synthetic may_treat graph; tracked separately.
- Fixing the three known spec-vs-built discrepancies (we pin current behavior, fix in dedicated PRs).

## Architecture

### Directory layout

```
tests/
├── conftest.py                           (extend — umls_db_path, baseline_path fixtures)
├── regression/                           (NEW)
│   ├── __init__.py
│   ├── conftest.py                       (regression-specific fixtures + normalization helpers)
│   ├── golden/                           (NEW — comparison helpers, NOT fixture data)
│   │   ├── __init__.py
│   │   ├── normalize.py                  (per-deliverable canonicalization)
│   │   ├── compare.py                    (set/diff logic)
│   │   └── report.py                     (structured diff formatting)
│   ├── fixtures/                         (NEW — small curated fixtures only)
│   │   ├── patient_friendly_verified.jsonl   (~30 hand-verified codes)
│   │   ├── embedding_index_verified.jsonl
│   │   ├── associations_verified.jsonl
│   │   ├── rxnorm_ingredients_verified.jsonl
│   │   └── pinned_meta.json              (exact counts + UMLS release + file hashes)
│   ├── test_patient_friendly_regression.py
│   ├── test_embedding_index_regression.py
│   ├── test_associations_regression.py
│   ├── test_rxnorm_ingredients_regression.py
│   ├── test_fhir4px_build_smoke.py
│   ├── test_cross_deliverable_consistency.py
│   ├── test_service_api_properties.py
│   └── test_golden_content_parity.py     (NEW — Tier 4)
```

### Storage strategy: use on-disk `reports/fhir4px/` as the baseline

The 1GB+ of canonical outputs already lives at `/mnt/d/medterm4ds/reports/fhir4px/` (gitignored). Rather than duplicating this data into git (which would require Git LFS and balloon the repo), the regression suite treats the on-disk baseline as the golden reference.

**Flow:**
1. Tier 4 test reads expected records from `reports/fhir4px/<file>` (path from `MEDTERM4DS_FHIR4PX_BASELINE` env var, default `/mnt/d/medterm4ds/reports/fhir4px`).
2. Test re-runs the build into `tmp_path`.
3. Test loads actual + expected, normalizes both, compares record-by-record keyed by primary key.
4. On mismatch: structured diff (added / removed / changed records with field-level detail) is printed and the test fails.

**Blessing workflow** (when intentional changes happen):
1. Make the code change.
2. Run `scripts/build_fhir4px_all.py` to regenerate `reports/fhir4px/`.
3. Re-run `pytest -m realdb tests/regression/test_golden_content_parity.py` — it will fail because actual (now == baseline) is fine but the test is comparing against the *baseline before rebuild*. So the workflow is actually: rebuild baseline first, then run tests — tests should pass.
4. Review `git diff` of `reports/fhir4px/` (note: gitignored, so review via `du` / file timestamps / manually curated release notes).
5. Commit code change. The baseline on disk is the new truth.

For **CI on self-hosted runner**: the runner has both `umls_current.duckdb` and `reports/fhir4px/` available. Tests compare freshly-built outputs against baseline. **GitHub-hosted runners skip Tier 4** (no DB, no baseline) — handled by the `realdb` marker.

For **auditability** of baseline changes (since `reports/` is gitignored), Tier 4 also writes a `tests/regression/fixtures/pinned_meta.json` containing SHA256 hashes of each canonical baseline file. This file IS committed. If the baseline drifts without a code change, the hash check surfaces it.

### Markers + CI strategy

Register in `pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
markers = [
    "realdb: requires umls_current.duckdb + reports/fhir4px baseline",
    "slow: tests that take >5s",
    "fhir4px_smoke: runs the full fhir4px build pipeline",
]
addopts = "-m 'not realdb and not slow and not fhir4px_smoke'"
```

- **`.github/workflows/ci.yml`**: unchanged (hermetic, all 3 markers excluded).
- **NEW `.github/workflows/regression.yml`**: nightly + `workflow_dispatch`. Runs on self-hosted runner with DB + baseline. Executes `pytest -m "realdb or fhir4px_smoke"`. Fails loudly on any drift.

### Conftest changes

Extend `tests/conftest.py`:
- `umls_db_path()` fixture: reads `MEDTERM4DS_REGRESSION_DB`, `pytest.skip` if absent.
- `fhir4px_baseline_dir()` fixture: reads `MEDTERM4DS_FHIR4PX_BASELINE` (default `/mnt/d/medterm4ds/reports/fhir4px`), `pytest.skip` if absent.
- `umls_release_tag()` fixture: opens DB once, returns release string for pin assertion.

`tests/regression/conftest.py` adds:
- `run_build_script(name, tmp_path, umls_db_path)` helper — wraps subprocess.run with timeout=900, env injection, returncode assertion. Used by Tier 2 and Tier 4.
- `load_canonical_records(path, kind)` helper — dispatches to `golden/normalize.py` per file extension.

## Tier 1 — Curated clinical fixtures (~30 codes, hand-verified)

The Plan agent's discipline: don't trust examples — cross-verify each one against the actual output AND a direct service call. **Target: 30 verified codes total**, distributed:

| Source | Count | Coverage goal |
|---|---|---|
| ICD10CM | 6 | common + obsolete + high-specificity |
| ICD10PCS | 4 | leaf + branch |
| SNOMEDCT_US | 5 | condition + lab + medication + body_structure TUIs |
| RXNORM | 6 | one per TTY (IN/MIN/SCD/SBD/SCDG/BN) |
| LNC | 4 | LN TTY only |
| CPT | 2 | |
| HCPCS | 2 | |
| CVX | 1 | |

Each entry in `patient_friendly_verified.jsonl`:
```json
{"source": "ICD10CM", "code": "E11.9", "expected_name": "Type 2 diabetes mellitus", "expected_friendly_source": "MEDLINEPLUS", "expected_match_type": "exact", "verified_against_release": "2026AA"}
```

Test calls `get_patient_friendly_names([CodeRef(...)], engine=engine)` directly against the real DB; asserts the full result matches. This validates the **public service API** end-to-end (separate from the build script output validation in Tier 4).

## Tier 2 — fhir4px build smoke (subprocess, real DB)

Plan agent's call: subprocess, not in-process. We are about to refactor; testing the real shipped entry point catches CLI/moving-module regressions.

Pattern (mimics `tests/test_hardening_scripts.py:95-150`):
```python
@pytest.mark.fhir4px_smoke
def test_build_patient_friendly_smoke(tmp_path, umls_db_path, fhir4px_baseline_dir):
    out_dir = tmp_path / "fhir4px"
    out_dir.mkdir()
    result = run_build_script("build_fhir4px_patient_friendly.py", out_dir, umls_db_path)
    assert result.returncode == 0, result.stderr.decode()
    _assert_patient_friendly_outputs(out_dir, umls_release="2026AA")
```

One test per script (4 tests) + one orchestration test (`build_fhir4px_all.py` end-to-end). Each:
1. Asserts return code 0 and no stderr.
2. Asserts each expected output file exists and is non-zero.
3. Validates `_meta.stats` counts against `pinned_meta.json` (exact, no tolerance).
4. Spot-checks 2–3 Tier 1 fixture codes per deliverable.

## Tier 2.5 — Cross-deliverable consistency

Highest-signal test for drift between the parallel implementations. `test_cross_deliverable_consistency.py`:

- **Embedding ↔ rxnorm-ingredients**: for every RXNORM code in `embedding_index_medication.jsonl`, `set(ingredient_codes)` must equal `set(rxnorm-ingredients[code].c)`. Pin mismatch ceiling at 50 (legitimate scoping differences). Force inspection above that.
- **Associations ↔ embedding_index**: every condition key in `condition_associations.json` is either a bare ICD10CM/SNOMED code OR appears in `embedding_index_condition.jsonl`.
- **Associations ↔ rxnorm-ingredients**: every `ingredient_code` referenced in `condition_associations.json` medications exists as an `IN` RxNorm code in `embedding_index_medication.jsonl`.

## Tier 3 — Property/contract invariants

Validates **every record** in each output against structural invariants from `data-delivery-spec.md`. Catches schema breakage.

Per-deliverable invariants:
- **patient_friendly**: every entry `{name, friendly_source, match_type, cui}`; `name` non-empty; `match_type ∈ {exact, original, broader, narrower, ...}`.
- **embedding_index**: `category`, `code.{source,code,tty,cui,name}`, `vectors.{technical,synonyms,friendly,hierarchy}` all present; `ingredient_codes` is `list | null`; `atc` is `null` for non-RxNorm.
- **associations**: every med entry `{code, strength, relationship, depth}`; `depth ∈ [0,5]`; `relationship ∈ {treats, prevents}`; `strength` derived from depth (`≤1→strong`, `==2→moderate`, `≥3→weak`).
- **rxnorm-ingredients**: every value is `list[{c,n}]` (may be empty for BN/PIN); `c` is numeric string.

## Tier 4 — Full content golden parity (NEW)

The user-requested scope expansion. Pins the **exact field-level content** of every record in every fhir4px output. Catches any drift anywhere — synonym reordering, friendly_name case changes, ATC level renames, etc.

### Per-deliverable normalization rules

Different deliverables have different non-determinism sources. `tests/regression/golden/normalize.py` canonicalizes each:

| Deliverable | Primary key | Sort unordered fields | Strip volatile fields |
|---|---|---|---|
| `patient_friendly_{src}.json` | `(source, code)` derived from filename | n/a (single record per code) | none |
| `patient_friendly_names.csv` | `(source, code)` | n/a | none |
| `embedding_index_{cat}.jsonl` | `(code.source, code.code)` | `vectors.synonyms` sorted lexicographically; `semantic_types` sorted | none |
| `condition_associations.json` | condition code (top-level key) | `medications` list sorted by `(code, relationship, depth)`; `labs` list sorted by `code` | `_meta.generated_at` |
| `rxnorm-ingredients.json` | rxnorm_code | per-code ingredient list sorted by `c` | `_meta.generated_at` |

### Comparison and diff

`tests/regression/golden/compare.py`:
```python
def compare_records(expected: dict, actual: dict, key_name: str) -> DiffReport:
    expected_keys = set(expected)
    actual_keys = set(actual)
    added = actual_keys - expected_keys
    removed = expected_keys - actual_keys
    changed = []
    for k in expected_keys & actual_keys:
        if expected[k] != actual[k]:
            changed.append((k, _field_diff(expected[k], actual[k])))
    return DiffReport(added=sorted(added), removed=sorted(removed), changed=changed)
```

`tests/regression/golden/report.py` formats a readable diff: per-key "REMOVED", "ADDED", "CHANGED (field: expected → actual)". Truncates at 50 records per category to avoid log explosion.

### Test pattern

```python
@pytest.mark.realdb
@pytest.mark.parametrize("deliverable", list(DELIVERABLES))
def test_golden_content_parity(deliverable, tmp_path, umls_db_path, fhir4px_baseline_dir):
    # Re-run the build for this deliverable into tmp_path
    run_build_script(deliverable.script, tmp_path / "actual", umls_db_path)

    # Load + canonicalize both
    expected = load_canonical_records(fhir4px_baseline_dir / deliverable.filename, deliverable.kind)
    actual = load_canonical_records(tmp_path / "actual" / deliverable.filename, deliverable.kind)

    diff = compare_records(expected, actual, deliverable.key)
    assert not diff, format_diff_report(deliverable.name, diff)
```

Where `DELIVERABLES` enumerates:
- 9 patient-friendly outputs (8 per-source JSONs + 1 combined CSV)
- 6 embedding JSONLs
- 1 associations JSON
- 1 rxnorm-ingredients JSON

Total: 17 deliverable comparisons.

### Pinned hashes for audit trail

`tests/regression/fixtures/pinned_meta.json` records SHA256 of each canonical baseline file:
```json
{
  "umls_release": "2026AA",
  "patient_friendly": {"icd10cm": {"count": 98506, "sha256": "..."}, ...},
  "embedding_index": {"medication": {"count": 124540, "sha256": "..."}, ...},
  ...
}
```

This file is committed. Test asserts:
1. UMLS release tag matches.
2. Each output's canonical SHA256 matches pinned value.
3. Each output's record count matches pinned value.

If the baseline on disk drifts without `pinned_meta.json` being updated, the test fails — surfaces unauthorized baseline changes.

## UMLS release pinning discipline

Drop `±2%` tolerance. Pin exact counts AND release tag.

On `pytest -m realdb`:
1. Read DB release tag. Assert `release == pinned_meta["umls_release"]`.
2. If mismatch: hard fail with `"UMLS release changed (2026AA → 2026AB). Re-run scripts/build_fhir4px_all.py, review diff, update tests/regression/fixtures/pinned_meta.json."` No silent tolerance.
3. All count + SHA assertions use exact values from `pinned_meta.json`.

## Known spec-vs-built discrepancies (pin, don't fix)

| Item | Spec | Built | Test name suffix |
|---|---|---|---|
| Medication embedding count | 117,544 | 124,540 | `_pinned_at_124540_atc_standalone_not_in_spec` |
| `lab_associations` in shipped file | 283 | 0 | `_pinned_at_0_orchestrator_missing_synthea_labs_flag` |
| `_meta.sources.medications` doc string | "0-4" | "0-5" | `_stale_meta_doc_string` |

Each pinned-with-discrepancy test name surfaces the issue. They become triage items post-suite, not blockers.

## Determinism handling

- `embedding_index` JSONL line order is non-deterministic. All comparisons are **set-based** keyed by `(source, code)` after canonicalization.
- `condition_associations.json` medication list order within a condition follows SQL insertion. Comparisons sort before deep-equal.
- `generated_at` timestamps stripped during normalization.
- `synonyms` and `semantic_types` lists sorted before comparison.

## Verification

End-to-end verification after implementation:

1. **Default run (CI-equivalent)**:
   ```bash
   pytest -q
   ```
   Expected: existing 290 tests pass + new tests skipped (markers excluded by default).

2. **Real-DB run (self-hosted runner)**:
   ```bash
   pytest -m "realdb or fhir4px_smoke" -v
   ```
   Expected: Tier 1 (~30 tests) passes; Tier 2 (5 tests) passes after ~10 min; Tier 2.5 (3 tests) passes; Tier 3 (~10 tests) passes; Tier 4 (17 tests) passes against current baseline.

3. **Marker hygiene**:
   ```bash
   pytest --markers | grep -E "realdb|slow|fhir4px_smoke"
   ```
   All three listed without warnings.

4. **Hash pin verification**:
   ```bash
   pytest tests/regression/test_golden_content_parity.py -k hash -v
   ```
   All SHA256 pins match current baseline.

5. **CI integration**: `.github/workflows/ci.yml` unchanged; new `regression.yml` visible in Actions tab, scheduled nightly.

## Files to create/modify

**Modify:**
- `tests/conftest.py` — add `umls_db_path`, `fhir4px_baseline_dir`, `umls_release_tag` fixtures
- `pyproject.toml` — register 3 markers + `addopts` default

**Create:**
- `tests/regression/__init__.py`
- `tests/regression/conftest.py` — `run_build_script`, `load_canonical_records` helpers
- `tests/regression/golden/__init__.py`
- `tests/regression/golden/normalize.py` — per-deliverable canonicalization
- `tests/regression/golden/compare.py` — set/diff logic
- `tests/regression/golden/report.py` — structured diff formatting
- `tests/regression/fixtures/pinned_meta.json` — exact counts + SHA256 + UMLS release
- `tests/regression/fixtures/patient_friendly_verified.jsonl` — 30 hand-verified codes
- `tests/regression/fixtures/embedding_index_verified.jsonl`
- `tests/regression/fixtures/associations_verified.jsonl`
- `tests/regression/fixtures/rxnorm_ingredients_verified.jsonl`
- `tests/regression/test_patient_friendly_regression.py`
- `tests/regression/test_embedding_index_regression.py`
- `tests/regression/test_associations_regression.py`
- `tests/regression/test_rxnorm_ingredients_regression.py`
- `tests/regression/test_fhir4px_build_smoke.py`
- `tests/regression/test_cross_deliverable_consistency.py`
- `tests/regression/test_service_api_properties.py`
- `tests/regression/test_golden_content_parity.py`
- `.github/workflows/regression.yml` — nightly + manual trigger

## Time estimate

| Phase | Hours |
|---|---|
| Conftest + markers + CI workflow | 1.5 |
| `golden/` helpers (normalize, compare, report) | 3.0 |
| `pinned_meta.json` extraction (counts + SHA256) | 1.0 |
| Tier 2 build smoke (5 tests) | 2.0 |
| Tier 2.5 cross-deliverable (3 tests) | 1.5 |
| Tier 3 invariants (4 test files) | 2.5 |
| Tier 4 golden parity (17 parametrized tests) | 3.5 |
| Tier 1 curation (30 codes, manual verify) | 3.0 |
| Documentation + verification | 1.5 |
| **Total** | **~19.5 hours** |

A 1-day MVP is still achievable by landing Tier 2 + 2.5 + 3 + `pinned_meta.json` first (~7 hours) — Tier 4 and Tier 1 grow incrementally as follow-ups. But the full plan as written delivers the complete regression contract.
