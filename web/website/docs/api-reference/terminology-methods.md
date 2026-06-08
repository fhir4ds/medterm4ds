---
title: Terminology Methods
---

This page documents methods on the high-level `Terminology` client returned by
`mt.connect(...)` and `mt.connect_remote(...)`.

Single-code methods return one model object. Batch inputs return lists. `_df`
methods return pandas by default or Polars with `backend="polars"`.

## Lookup

```python
terms.lookup(source_or_codes, code=None, *, resolve_mode="active_only")
terms.lookup_df(source_or_codes, code=None, *, resolve_mode="active_only", backend="pandas")
```

Returns `CodeInfo | None` for single inputs, or `list[CodeInfo | None]` for
batches. Missing codes are represented as null rows in `lookup_df`.

```python
terms.lookup("ICD10CM", "E11.9")
terms.lookup_df("ICD10CM", ["E11.9", "E11.40"])
```

## Patient-Friendly Names

```python
terms.patient_friendly(source_or_codes, code=None, *, max_depth=5, resolve_mode="active_only")
terms.patient_friendly_df(source_or_codes, code=None, *, max_depth=5, resolve_mode="active_only", backend="pandas")
```

Returns `FriendlyNameResult` records with `match_type`, `match_depth`, and
`matched_via` provenance.

```python
df = terms.patient_friendly_df("ICD10CM", ["E11.9", "E11.40"])
df[["source", "code", "name", "match_type", "match_depth", "matched_via"]]
```

## Mapping

```python
terms.map(
    source_or_codes,
    code=None,
    *,
    target_sources,
    max_results_per_code=50,
    max_depth=0,
    include_target_ancestors=False,
    include_target_descendants=False,
    resolve_mode="active_only",
)

terms.map_df(..., backend="pandas")
```

Returns `list[CodeMapping]`. `max_depth=0` limits mapping to exact/same-CUI
style mappings. Higher depths enable bounded hierarchy fallback where the
engine supports it.

```python
terms.map_df(
    "ICD10CM",
    ["E11.9"],
    target_sources=["SNOMEDCT_US"],
    max_depth=2,
)
```

## Resolution

```python
terms.resolve(source_or_codes, code=None)
terms.resolve_df(source_or_codes, code=None, *, backend="pandas")
```

Returns `CodeResolution` records for active, obsolete, historical, missing,
ambiguous, and NDC-to-RxCUI inputs.

```python
terms.resolve("NDC", "0002-0821-01")
terms.resolve_df([mt.CodeRef("NDC", "0002-0821-01"), mt.CodeRef("ICD10CM", "OLD")])
```

## Hierarchy

```python
terms.hierarchy(source_or_codes, code=None, *, direction, max_depth=1)
terms.hierarchy_df(source_or_codes, code=None, *, direction, max_depth=1, backend="pandas")
terms.parents(source_or_codes, code=None)
terms.children(source_or_codes, code=None)
terms.ancestors(source_or_codes, code=None, *, max_depth=5)
terms.descendants(source_or_codes, code=None, *, max_depth=5)
```

`direction` is `parents`, `children`, `ancestors`, or `descendants`.

```python
terms.ancestors("SNOMEDCT_US", "44054006", max_depth=3)
```

## Optimize

```python
terms.optimize(
    source_or_codes,
    code=None,
    *,
    source=None,
    relationship=None,
    output_format="compact",
    include_codes=False,
)
```

Returns an `OptimizeResult` with include/exclude rules for compact value set
maintenance.

```python
result = terms.optimize("ICD10CM", ["E11.40", "E11.41", "E11.42"])
result.to_dict()
```

## Discovery

```python
terms.search(query, *, sources=None, tty_filters=None, limit=25)
terms.search_df(query, *, sources=None, tty_filters=None, limit=25, backend="pandas")

terms.source_stats(sources=None)
terms.source_stats_df(sources=None, *, backend="pandas")

terms.sample_codes(sources=None, *, per_source=10)
terms.sample_codes_df(sources=None, *, per_source=10, backend="pandas")

terms.code_ttys(source_or_codes, code=None)
terms.code_ttys_df(source_or_codes, code=None, *, backend="pandas")
```

Discovery helps inspect a UMLS build before broader mapping or export work.

```python
terms.source_stats_df(["ICD10CM", "SNOMEDCT_US", "RXNORM"])
terms.search_df("metformin", sources=["RXNORM"], limit=20)
terms.code_ttys_df("RXNORM", "12345")
```

## ConceptMap

```python
terms.conceptmap(
    source_or_codes,
    code=None,
    *,
    batch_size=5000,
    max_depth=5,
    target_source="PATIENT_FRIENDLY",
)

terms.conceptmap_df(..., backend="pandas")

terms.mapping_conceptmap(
    source_or_codes,
    code=None,
    *,
    target_sources,
    batch_size=5000,
    max_results_per_code=50,
    max_depth=0,
    include_target_ancestors=False,
    include_target_descendants=False,
)

terms.mapping_conceptmap_df(..., backend="pandas")
```

Patient-friendly ConceptMap rows come from `FriendlyNameResult` records. Mapping
ConceptMap rows come from `CodeMapping` records.

```python
terms.conceptmap_df("ICD10CM", ["E11.9"])
terms.mapping_conceptmap_df("ICD10CM", ["E11.9"], target_sources=["SNOMEDCT_US"])
```

## Connection

```python
terms.close()
```

Closes a client-owned local DuckDB connection. Remote clients do not own a
local DuckDB connection.
