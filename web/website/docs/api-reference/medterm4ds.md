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

The Terminology client and all lower-level service functions use the canonical
`(source, code)` tuple order — same as `CodeRef(source=, code=)`, same as
FHIR Coding `{system, code}`:

```python
terms.lookup(("ICD10CM", "E11.9"))
# Same thing, more explicit:
mt.CodeRef("ICD10CM", "E11.9")
```

(Earlier 0.0.x releases had a `(code, source)` convention in some helpers
that caused silent source/code swaps when refactoring between tuple and
CodeRef forms; that was unified in v0.0.2.)

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
