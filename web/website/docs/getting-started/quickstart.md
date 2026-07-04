---
title: Quickstart
---

Install medterm4ds, connect to a UMLS DuckDB, and use the five core capabilities.

## Install

```bash
pip install medterm4ds              # core: lookup, hierarchy, mapping
pip install medterm4ds[fhir]        # + FHIR server + intelligent search
pip install medterm4ds[extraction]  # + text extraction (NER + ConText)
pip install medterm4ds[all]         # everything
```

## Connect

```python
import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb", memory_profile="low")
```

## 1. Look up a code

```python
info = terms.lookup("SNOMEDCT_US", "44054006")
print(info.name)  # "Type 2 diabetes mellitus"

friendly = terms.patient_friendly("SNOMEDCT_US", "44054006")
print(friendly.name)  # "Diabetes Type 2"
```

## 2. Walk the hierarchy

```python
parents = terms.parents("SNOMEDCT_US", "44054006")
ancestors = terms.ancestors("SNOMEDCT_US", "44054006", max_depth=5)
children = terms.children("SNOMEDCT_US", "73211009")
```

## 3. Map between code systems

```python
mappings = terms.map("SNOMEDCT_US", "44054006", target_sources=["ICD10CM"])
# → ICD-10-CM E11 (Type 2 diabetes mellitus)
```

## 4. Search by text

```python
# Lexical: fast BM25 token matching (~1ms)
results = mt.search("diabetes", mode="lexical")

# Semantic: catches novel phrasings (~100ms)
results = mt.search("high blood sugar", mode="semantic")
# → Hyperglycemia (0.80, probable)

# Hybrid: best accuracy (~110ms)
results = mt.search("metformin pill", mode="hybrid")
# → Metformin Pill (1.00, certain)
```

## 5. Extract concepts from text

```python
# Full pipeline: free text → coded concepts
concepts = mt.extract(
    "65yo M with T2DM on metformin. No CKD.",
    format="codes",
    categories=["condition", "medication"],
)
for c in concepts:
    print(f"  {c.code:12s} {c.display:35s} matched='{c.matched_text}' status={c.status}")
# → 44054006     Type 2 diabetes mellitus            matched='T2DM'      status=affirmed
# → 860975       Metformin Oral Product              matched='metformin' status=affirmed
# CKD excluded — negated by ConText
```

## Next steps

- [Capabilities](../capabilities/code-lookup.md) — detailed guides for each capability
- [Interfaces](../interfaces/python.md) — Python, CLI, MCP, FHIR server
- [Demo notebook](https://github.com/fhir4ds/medterm4ds/blob/main/notebooks/fhir_terminology_server_demo.ipynb)
