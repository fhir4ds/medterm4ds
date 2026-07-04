---
title: Installation
---

medterm4ds is a Python package for medical terminology lookup, mapping, hierarchy traversal, intelligent text search, and clinical text extraction.

## Install

```bash
# Core: lookup, mapping, hierarchy, patient-friendly names
pip install medterm4ds

# With DuckDB engine (required for local DB workflows)
pip install "medterm4ds[duckdb]"

# With FHIR R4 terminology server + intelligent search (BM25 + SapBERT)
pip install "medterm4ds[fhir]"

# With text extraction (NER + clinical NLP)
pip install "medterm4ds[extraction]"

# Everything
pip install "medterm4ds[all]"
```

## What each extra gives you

| Extra | Capabilities unlocked | Key dependencies |
|---|---|---|
| (none) | Core engine, CLI, MCP (basic) | duckdb |
| `[fhir]` | FHIR R4 server, `mt.search()` (BM25 + SapBERT) | fastapi, torch, faiss |
| `[extraction]` | `mt.extract()` (NER + ConText + search) | medspacy, transformers |
| `[api]` | REST API server | fastapi, uvicorn |
| `[mcp]` | MCP server (37+ tools) | fastmcp |
| `[dataframe]` | pandas/polars helpers | pandas, polars |

## Connecting to UMLS data

```python
import medterm4ds as mt

# Local DuckDB (requires UMLS DuckDB — see data setup guide)
terms = mt.connect("/path/to/umls.duckdb")
```

The DuckDB must contain UMLS Metathesaurus tables (`mrconso`, `mrrel`, `mrsat`). See [Data Setup](data-setup.md) for building one from UMLS RRF files.

## Development install

```bash
git clone https://github.com/fhir4ds/medterm4ds.git
cd medterm4ds
pip install -e ".[dev]"
```
