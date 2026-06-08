---
title: Releases
---

This page tracks Medical Terminology for Data Science package releases.

## 0.0.1

Initial release candidate for the refactored medterm4ds package.

Compatibility:

- Python 3.10 and newer
- UMLS DuckDB builds from flat RRF files, compressed RRF files, or `.nlm` release archives
- Release-pinned UMLS builds are supported with `--release-version`

Public surfaces:

- Notebook-first `Terminology` client from `medterm4ds.connect(...)`
- Local DuckDB engine and `RemoteApiEngine`
- Lookup, resolve, patient-friendly names, mapping, hierarchy, optimize, discovery, and ConceptMap helpers
- CLI, API, and MCP adapters over the same service layer
- pandas and Polars DataFrame helpers
- JSON, JSONL, CSV, compact text, ASCII tree, and FHIR R4 ConceptMap outputs

Release checks:

- unit, lint, and compile verification
- real-data lookup, mapping, hierarchy, discovery smoke tests
- patient-friendly parity matrix against `/mnt/d/medterm`
- CLI acceptance smoke for JSONL resume, CSV, FHIR R4, lookup, map, and hierarchy
- notebook smoke for included examples
- bulk validation and mapping-quality CSV generation
- package build, metadata check, and fresh-venv wheel install smoke
- Docusaurus typecheck and production build

Known 0.0.1 parity decisions:

- CPT patient-friendly display intentionally uses deterministic source-specific term ordering instead of legacy order-dependent atom selection.
- SNOMED original display uses the preferred term and keeps the fully specified name as `technical_name`.
- Legacy RxNav approximate drug spelling/class workflows are represented by UMLS-backed compatibility wrappers; richer RxClass/RxNav behavior is deferred.

UMLS data release details live in [Terminology > UMLS Release Info](../terminology/umls-release-info.md).
