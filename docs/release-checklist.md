# medterm4ds Release Checklist

Use this checklist before publishing `medterm4ds` releases. Current as of
v0.0.2 (2026-08-16). The legacy `/mnt/d/medterm` parity comparison and its
scripts were removed in the Tier C refactor (2026-06-26); the fhir4px
regression suite (`tests/regression/`, gated by the `realdb` marker) is the
real-data quality gate.

## Version

- Confirm `pyproject.toml` has the intended version.
- Confirm `src/medterm4ds/__init__.py` `__version__` matches (servers and the
  FHIR CapabilityStatement are single-sourced from it).
- Confirm `hatch version` prints the same version.
- Update `CHANGELOG.md`: `[Unreleased]` → `[x.y.z] - YYYY-MM-DD`, fresh empty
  `[Unreleased]` on top.
- Confirm package metadata reports `License-Expression: GPL-3.0-only`.

## Local Quality Gates

```bash
make verify            # lint + test + compile
make fhir-conformance  # FHIR R4 conformance suite (~8 min)
cd web/website && npm run typecheck && npm run build
```

Known environmental failures that do NOT block a release (verify they are the
same tests, then note them in the release summary):

- `tests/test_fhir_conformance.py` — 4 pydantic
  `rest.0.url Extra inputs are not permitted` failures (`fhir.resources`
  version drift; pin the version to fix).
- `tests/test_extraction.py::TestFindTerms` — 3 GLiNER model-drift failures
  (HF weights re-download; pin via `MEDTERM4DS_NER_MODEL` to fix).

Release-engineer validation run (standard suite, both FHIR conformance trees
excluded):

```bash
.venv/bin/python -m pytest tests/ \
  --ignore=tests/test_fhir_conformance.py --ignore=tests/fhir_conformance \
  --tb=line -q
```

Real-data gate (licensed environment only): the `realdb`-marker regression
suite in `tests/regression/` runs against `umls_current.duckdb` (~12 min).

## Production Database Rebuild

**Required whenever the prepared schema version changes** (v0.0.2 bumps it
0.8 → 0.9). Servers refuse prepared patient-friendly lookups on a version
mismatch (HTTP 501 with remediation text) — loud by design.

```bash
medterm4ds data prepare-derived --db data/umls_current.duckdb
medterm4ds data verify --db data/umls_current.duckdb   # no version mismatch
```

Acceptance checks: `mt4ds.walk_edges` source coverage grows (RXNORM ~238 K
isa edges, MSH ~15 K, LNC `class_of` +~284 K), CPT 87143 displays the PT,
prepared patient-friendly latency returns to the ~1 s prepared path.

## Package Checks

- Inspect `dist/medterm4ds-*.whl`.
- Confirm `medterm4ds/client.py` is included.
- Confirm `notebooks/` is included in the sdist.
- Confirm generated website files, `node_modules`, and local DuckDB data are
  not included.
- Re-run the standard validation suite from a fresh wheel install before
  publishing.

## Documentation Checks

- CHANGELOG covers every breaking/behavior change with its workaround.
- Website API reference signatures match current defaults (e.g. remote
  `timeout=300.0`, `cache_indexes=False`).
- README Makefile target list matches the `Makefile`.
- Known issues carried into the release are documented in the CHANGELOG.

## Publish

```bash
make publish-test
```

Install from TestPyPI in a clean environment and rerun a small notebook smoke
before publishing to PyPI.

```bash
make publish
```
