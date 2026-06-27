> **Historical document.** This doc references the legacy `/mnt/d/medterm` project
> and the parity comparison that was removed in the Tier C refactor (2026-06-26).
> The `engines/medterm_baseline/` adapter and all parity scripts have been deleted.
> The fhir4px regression suite (`tests/regression/`) is the current quality gate.
> Content below is preserved for historical context.

# medterm4ds Release Checklist

Use this checklist before publishing `medterm4ds` releases.

## Version

- Confirm `pyproject.toml` has the intended version.
- Confirm `hatch version` prints the same version.
- Update `CHANGELOG.md`.
- Confirm package metadata reports `License-Expression: GPL-3.0-only`.

## Local Quality Gates

```bash
make verify
make notebook-smoke
make parity-smoke
make parity-source-smoke
make acceptance-smoke
make real-data-smoke
make bulk-validation-smoke
make mapping-quality-smoke
make api-smoke
make mcp-smoke
cd web/website && npm run typecheck
make website-build
python3 scripts/build_package.py --skip-verify
python3 scripts/test_wheel_install.py
```

`real-data-smoke` requires a local UMLS DuckDB database and should not run in
public CI unless the data is available in a licensed private environment.

## Package Checks

- Inspect `dist/medterm4ds-*.whl`.
- Confirm `medterm4ds/client.py` is included.
- Confirm `notebooks/` is included in the sdist.
- Confirm generated website files, `node_modules`, and local DuckDB data are not included.
- Run the fresh-venv wheel install smoke.

## Parity Checks

- Run the source-by-source patient-friendly matrix against `/mnt/d/medterm`.
- Review any `review` sources in the generated matrix.
- Accept or fix every mismatch category before publishing.
- Keep accepted differences documented in `docs/parity-matrix.md`.

## Documentation Checks

- Quickstart should begin with Python notebook usage.
- CLI docs should be framed as bulk/export/automation.
- UMLS licensing docs should link users to official UMLS license instructions.
- Release notes should be about medterm4ds releases, not UMLS releases.

## Publish

```bash
make publish-test
```

Install from TestPyPI in a clean environment and rerun a small notebook smoke
before publishing to PyPI.

```bash
make publish
```
