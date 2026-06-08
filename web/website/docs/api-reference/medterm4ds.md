---
title: Overview
---

This reference documents the public Python contract for `0.0.1`. Private
DuckDB SQL helpers, ranking functions, CLI parser functions, and
underscore-prefixed members are implementation details.

The public API has three layers:

- **Terminology Client**: the high-level `Terminology` object returned by
  `mt.connect(...)` and `mt.connect_remote(...)`.
- **Service Functions**: lower-level batch functions for applications that need
  direct engine control.
- **Models and Outputs**: typed records, serialization helpers, DataFrame
  conversion, compact renderers, and FHIR ConceptMap helpers.

Most notebook and data science workflows should start here:

```python
import medterm4ds as mt

terms = mt.connect("data/umls_current.duckdb", memory_profile="low")

friendly = terms.patient_friendly_df("ICD10CM", ["E11.9", "E11.40"])
mapping = terms.map_df("ICD10CM", ["E11.9"], target_sources=["SNOMEDCT_US"])
```

## Input Conventions

The Terminology client uses `(source, code)` tuples:

```python
terms.lookup(("ICD10CM", "E11.9"))
```

Lower-level service functions preserve medterm's historical `(code, source)`
tuple convention. Use `CodeRef` when passing code inputs across layers:

```python
mt.CodeRef("ICD10CM", "E11.9")
```

All source names are normalized by `CodeRef`, so aliases such as `LOINC` and
`ICD10-CM` normalize to the internal source names used by UMLS.

## Reference Pages

- [Terminology Client](./terminology-client.md): `connect`, `connect_remote`,
  connection lifecycle, and code inputs.
- [Terminology Methods](./terminology-methods.md): `lookup`,
  `patient_friendly`, `map`, `resolve`, hierarchy, optimize, discovery, and
  ConceptMap methods.
- [Models](./models.md): dataclass fields and stable `to_dict()` output
  shapes.
- [Service Functions](./service-functions.md): lower-level batch functions and
  engine-level signatures.
- [Data Setup](./data-setup.md): UMLS download/build/verify helpers.
- [Engines and Outputs](./engines-outputs.md): local/remote engines,
  DataFrame/record helpers, compact renderers, and FHIR ConceptMap output.
