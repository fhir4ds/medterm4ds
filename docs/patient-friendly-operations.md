# Patient-friendly operations runbook

This runbook captures the current operational workflow for validating patient-friendly naming semantics and performance.

## Canonical runtime path

The supported implementation is the runtime resolver used by `get_patient_friendly_names` and `scripts/run_patient_friendly_review.py`. It uses prepared lookup, walk, crosswalk, RxNorm, CVX, and source-specific patient-friendly policy primitives.

For full production-code-system runs, use the `fast` memory profile. The `balanced` profile caps DuckDB around 1GB and can OOM on full ICD10/SNOMED/LNC batches.

```bash
/usr/bin/time -v env PYTHONPATH=src python3 scripts/run_patient_friendly_review.py \
  --db data/umls_current.duckdb \
  --db-role current_candidate \
  --per-source 0 \
  --max-depth 5 \
  --memory-profile fast \
  --output-csv reports/performance/patient_friendly_runtime_all_sources_fast_2026-06-08.csv \
  --output-json reports/performance/patient_friendly_runtime_all_sources_fast_2026-06-08.json \
  --progress \
  2>&1 | tee reports/performance/patient_friendly_runtime_all_sources_fast_2026-06-08.log
```

Observed 2026-06-08 runtime result:

```text
total_codes: 1,127,094
source_resolution_seconds: 200.27
wall_time: 3:45.57
max_rss: about 10.6GB
```

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

A logic-preserving change should have `0` unexpected row-level changes against the latest blessed compare CSV.

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

## Archived materialized path

The old final-resolution materialization path has been archived under `archive/legacy/patient_friendly_materialization/`. It is not an active or recommended path.

Reason:

- It was not validated against the current runtime patient-friendly policy.
- The scoped reviewed-system materialization attempt did not complete after `1:01:50`.
- Runtime resolution already handles 1.1M+ reviewed production codes in under four minutes with `--memory-profile fast`.

Future materialization should reuse runtime resolver output directly, or first refactor the runtime resolver into shared SQL relation builders so runtime and materialized semantics cannot drift.
