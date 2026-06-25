# Regression Test Suite

Pre-refactor safety net for `medterm4ds`. Validates the four `fhir4px` deliverables
(patient-friendly names, embedding indices, condition associations, RxNorm
ingredients) against the canonical baseline at `reports/fhir4px/`.

See [`docs/regression-test-plan.md`](../../docs/regression-test-plan.md) for the
full design rationale.

## Running

### Default (CI, hermetic)
```bash
pytest -q
```
Existing 321 tests run. All regression tests are deselected (markers excluded
by default in `pyproject.toml`).

### Full regression (requires real DB + baseline)
```bash
pytest -m "realdb or fhir4px_smoke" -v
```
Runs against `/mnt/d/medterm4ds/data/umls_current.duckdb`. First run takes
~10 minutes (builds all four deliverables once via session-scoped fixture).

### Just the curated fixtures (fast, ~7s)
```bash
pytest tests/regression/test_patient_friendly_regression.py -v -m realdb
```

## Markers

| Marker | Meaning |
|---|---|
| `@pytest.mark.realdb` | Requires `umls_current.duckdb`. Set `MEDTERM4DS_REGRESSION_DB` to override. |
| `@pytest.mark.fhir4px_smoke` | Runs the full `build_fhir4px_all.py` pipeline (subprocess). |
| `@pytest.mark.slow` | Takes more than a few seconds. |

## Tiers

| Tier | File | What it validates |
|---|---|---|
| 1 | `test_patient_friendly_regression.py` | 15 hand-verified codes through the public service API. |
| 2 | `test_fhir4px_build_smoke.py` | Each build script runs cleanly; output counts match pinned values. |
| 2.5 | `test_cross_deliverable_consistency.py` | Drift between deliverables (embedding ↔ rxnorm ↔ associations). |
| 3 | `test_service_api_properties.py` | Per-record schema/type/range invariants over every record. |
| 4 | `test_golden_content_parity.py` | Full content parity: every field of every record matches baseline. |

## Findings addressed in Tier A (2026-06-25)

The suite caught real issues on first run. All have been fixed:

### 1. Medication embedding `atc.atc_name` non-determinism — FIXED

`scripts/build_fhir4px_embedding_index.py` line 306 had
`ROW_NUMBER() OVER (PARTITION BY code ORDER BY atc_code)` which left
non-deterministic ordering when multiple mrconso rows shared an ATC code
with different names. Fixed by adding `atc_name` to the ORDER BY:
`ORDER BY atc_code, atc_name`. Now the alphabetical-first name is picked
deterministically.

### 2. RxNorm ingredient scope drift — FIXED

13,797 RxNorm codes had `ingredient_codes` populated in
`embedding_index_medication.jsonl` but `[]` in `rxnorm-ingredients.json`.
Cause: `build_fhir4px_rxnorm_ingredients.py` filtered products to a narrower
TTY set (`SCDG/SCD/SBD/MIN/PIN/IN/BN`) than the embedding script
(`SCDG/SCD/SBD/SCDC/SBDC/SBDF/MIN/PIN/IN/BN`). Fixed by aligning the TTY
scope. All 13,797 SCDC codes now resolve correctly in both files.

### 3. SNOMED condition scope delta — PARTIALLY FIXED (rest is legitimate)

9,252 SNOMED codes appeared in `condition_associations.json` but not in
`embedding_index_condition.jsonl`. Cause: the embedding filter's
`_CONDITION_TUIS` list excluded T033 (Finding) and T184 (Symptom), two of the
most common SNOMED condition TUIs. Adding them reduced the delta from 9,252
to 3,360.

The remaining 3,360 are correctly NOT conditions — they're body parts
(T023/T018/T029/T030), organisms (T007/T204), procedures (T060), or
medications (T121/T109) that have `may_treat` edges via MSH but aren't
clinical conditions. 1,455 of them appear in `embedding_index_body_structure`,
198 in `embedding_index_procedure`, 131 in `embedding_index_medication`, and
38 in `embedding_index_lab`. Test pins this legitimate delta at 3,360 (±200).

### 4. Spec drift (still pinned, not fixed)

| Item | Spec | Built | Pinned reason |
|---|---|---|---|
| Medication embedding count | 117,544 | 124,540 | 6,996 ATC standalone records not in spec |
| `lab_associations` | 283 | 0 | `build_fhir4px_all.py` doesn't pass `--synthea-labs` |
| `_meta.sources.medications` doc | "0-4" | "0-5" | Stale doc string in output metadata |

These are surfaced in test names so they show up as triage items.

## Updating the baseline

When you intentionally change the build output:

1. Make the code change.
2. Rebuild: `PYTHONPATH=src python3 scripts/build_fhir4px_all.py`
3. Review the diff vs. the previous `reports/fhir4px/` outputs.
4. Regenerate pinned metadata:
   ```bash
   PYTHONPATH=tests python3 tests/regression/regenerate_pinned_meta.py
   ```
5. Re-run the suite:
   ```bash
   pytest -m "realdb or fhir4px_smoke" -v
   ```
6. If cross-deliverable pinned counts (Tier 2.5) drifted, update them in
   `test_cross_deliverable_consistency.py`.
7. Commit the code change, `tests/regression/fixtures/pinned_meta.json`, and
   any updated fixtures together.
