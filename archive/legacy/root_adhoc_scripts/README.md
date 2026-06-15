# Archived root ad-hoc scripts

These measurement and benchmark scripts previously lived in the repository root. They were exploratory tooling used during patient-friendly and SNOMED/LOINC/RxNorm development and are not part of the supported package surface.

They are archived here because:

- They hardcode `data/umls_current.duckdb` paths and assume a local UMLS DuckDB build.
- Several depend on `medterm4ds.services.patient_friendly_materialized`, which itself is archived under `archive/legacy/patient_friendly_materialization/`.
- The `test_*.py` files are not pytest tests; they are ad-hoc measurement scripts with misleading names.

The canonical scripts for benchmarking, parity, and review live under `scripts/`. Re-run support for the workflows these scripts fed should be rebuilt there if needed, rather than reviving these files.
