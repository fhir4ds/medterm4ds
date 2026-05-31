# Changelog

## 0.1.0 - Unreleased

- Added a DuckDB LocalLite terminology engine for low-memory local execution.
- Added exact lookup, hierarchy traversal, same-CUI mapping, and bounded broader/narrower mapping.
- Added patient-friendly name resolution for ICD-10, SNOMED CT, RxNorm, LOINC, CVX, CPT, and HCPCS.
- Added JSONL, CSV, and FHIR R4 ConceptMap outputs.
- Added source inventory, TTY inspection, and name search discovery tools.
- Added CLI, FastAPI, MCP, remote API engine, shared bulk exports, and DataFrame helpers.
- Added versioned public output schemas and real-data smoke scripts.
- Added MCP-compatible terminology, drug, and external evidence tool names. UMLS-backed tools return terminology results; external evidence tools return structured unavailable responses until data adapters are configured.
