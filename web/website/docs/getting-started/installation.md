---
title: Installation
---

medterm4ds is a Python package for terminology lookup, mapping, patient-friendly naming, value set operations, and MCP/API access.

```bash
pip install medterm4ds
```

For local DuckDB-backed workflows, install DuckDB support:

```bash
pip install "medterm4ds[duckdb]"
```

Typical notebook import:

```python
import medterm4ds as mt

terms = mt.connect("/mnt/d/medterm4ds/data/umls_current.duckdb")
```

For development from this repository:

```bash
cd /mnt/d/medterm4ds
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

Most examples assume a local UMLS DuckDB database at:

```bash
/mnt/d/medterm4ds/data/umls_current.duckdb
```

Set it once for API and MCP workflows:

```bash
export MEDTERM4DS_DB=/mnt/d/medterm4ds/data/umls_current.duckdb
```
