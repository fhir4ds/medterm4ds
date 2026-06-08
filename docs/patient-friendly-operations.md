# Patient-friendly operations runbook

This runbook captures the current operational workflow for validating patient-friendly naming semantics and performance.

## Semantic regression gate

Use the benchmark comparison as the row-level regression gate before and after patient-friendly logic changes.

```bash
PYTHONPATH=src python3 scripts/compare_patient_friendly_benchmark.py \
  --benchmark /mnt/d/medterm/data/patient_friendly_benchmark.csv \
  --db data/umls_current.duckdb \
  --db-role current_candidate \
  --output-prefix reports/quality/patient_friendly_benchmark_<label> \
  --chunk-size 250 \
  --query-chunk-size 5000 \
  --threads 32 \
  --memory-limit 64GB \
  --temp-dir /tmp \
  --progress
```

Compare the new `*_compare.csv` against the latest blessed compare CSV. A logic-preserving change should have `0` unexpected row-level changes.

Current blessed baseline for cleanup/refactor work:

```text
reports/quality/patient_friendly_benchmark_cleanup_regression_2026-06-08_compare.csv
```

Observed cleanup/refactor gate on 2026-06-08:

```text
rows: 5285
changed_rows_vs_previous_blessed: 0
match_rate: 0.5313
elapsed_seconds: 25.336
```

## Targeted semantic review

Build a focused review file from the benchmark compare output.

```bash
PYTHONPATH=src python3 scripts/build_patient_friendly_targeted_review.py \
  --compare-csv reports/quality/patient_friendly_benchmark_cleanup_regression_2026-06-08_compare.csv \
  --output-prefix reports/quality/patient_friendly_targeted_review_2026-06-08 \
  --max-per-focus 100
```

Current review output:

```text
reports/quality/patient_friendly_targeted_review_2026-06-08.csv
```

Focus areas:

- CPT useful vs too generic mappings.
- SNOMEDCT_US broad CHV matches.
- SNOMEDCT_US drug/product routes through RxNorm.
- RxNorm PIN to IN ingredient behavior.
- LNC original and first-axis/component behavior.
- CVX combination vaccine behavior.
- Tracked regressions such as `ICD10CM:S43`, `CPT:50580`, and old synthetic-edge jumps.

## All-code materialization performance

Use materialization to test full-system batch generation, not request-time lookup.

Reviewed systems command:

```bash
/usr/bin/time -v env PYTHONPATH=src python3 scripts/materialize_patient_friendly.py \
  --db data/umls_current.duckdb \
  --db-role current_candidate \
  --sources ICD10CM,ICD10PCS,LNC,CPT,HCPCS,SNOMEDCT_US,RXNORM,CVX \
  --replace \
  --chunk-size 5000 \
  --output-json reports/performance/patient_friendly_all_reviewed_systems_2026-06-08_materialize.json \
  2>&1 | tee reports/performance/patient_friendly_all_reviewed_systems_2026-06-08_materialize.log
```

A broader `--all` run is not the preferred iteration command because it includes every prepared source in the database, not just the reviewed production code systems.

2026-06-08 performance finding:

- Broad `--all` materialization was terminated after `40:56.51` without completing.
- Scoped reviewed-system materialization was terminated after `1:01:50` without completing.
- Both logs are saved under `reports/performance/` for review.
- This means final-resolution materialization should not be treated as solved until the materialization path has source-level progress/timing and the slow phase is identified.

## Operational recommendation

Keep using the runtime prepared primitives plus benchmark regression gate for semantic iteration. Defer final patient-friendly resolution materialization until there is a concrete serving or repeated batch requirement, and first add source-level materialization timing so performance regressions can be localized.
