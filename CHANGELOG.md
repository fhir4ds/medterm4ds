# Changelog

## Unreleased

- Stabilized patient-friendly naming around UMLS-only hierarchy traversal and removed synthetic hierarchy edge generation.
- Archived the old final-resolution materialization path; the live prepared runtime resolver is now the canonical patient-friendly path.
- Added smart title casing for patient-friendly `name` output while preserving `technical_name` source casing, clinical units, acronyms, mixed-case terms, and systematic chemical names.
- Added `--ignore-name-case` to the patient-friendly benchmark comparison so semantic regressions can be separated from display-case differences.
- Recorded the current all-source patient-friendly runtime baseline: 1,127,094 reviewed production codes in 3:47.17 wall time with `--memory-profile fast`.
- Refreshed CI compatibility by updating stale display expectations and lint issues.

## 0.0.1 - 2026-06-01

- Added Medical Terminology for Data Science package identity and GPL-3.0-only license metadata.
- Added a notebook-first `Terminology` facade with `connect(...)`, `connect_remote(...)`, typed result methods, and DataFrame helpers.
- Added a local DuckDB terminology engine for low-memory local execution. `LocalLite*` names remain compatibility aliases for early pre-release users.
- Added exact lookup, hierarchy traversal, same-CUI mapping, and bounded broader/narrower mapping.
- Added patient-friendly name resolution for ICD-10, SNOMED CT, RxNorm, LOINC, CVX, CPT, and HCPCS.
- Added JSONL, CSV, and FHIR R4 ConceptMap outputs.
- Added source inventory, TTY inspection, and name search discovery tools.
- Added CLI, FastAPI, MCP, remote API engine, shared bulk exports, and DataFrame helpers.
- Added versioned public output schemas and real-data smoke scripts.
- Added executable notebook examples and notebook smoke testing against a synthetic DuckDB fixture.
- Added Hatch build/publish workflow and fresh-venv wheel install smoke testing.
- Added Docusaurus documentation with Python notebook quickstarts, terminology notes, UMLS licensing guidance, and release docs.
- Added MCP-compatible terminology, drug, and external evidence tool names. UMLS-backed tools return terminology results; external evidence tools return structured unavailable responses until data adapters are configured.
