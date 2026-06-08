---
title: Service Functions
---

Service functions are lower-level batch APIs for applications that need direct
engine control. They accept `CodeRef` objects or medterm-compatible
`(code, source)` tuples.

Most notebook users should prefer the [Terminology Client](./terminology-client.md).

## Lookup

```python
mt.get_code_infos(
    codes,
    engine,
    *,
    resolve_mode="active_only",
) -> list[CodeInfo | None]

mt.get_code_info(
    code,
    engine,
    *,
    resolve_mode="active_only",
) -> CodeInfo | None
```

`resolve_mode` is passed through the shared resolution layer.

## Patient-Friendly Names

```python
mt.get_patient_friendly_names(
    codes,
    engine,
    max_depth=5,
    resolve_mode="active_only",
) -> list[FriendlyNameResult]
```

## Mapping

```python
mt.get_code_mappings(
    codes,
    engine,
    *,
    target_sources,
    max_results_per_code=50,
    max_depth=0,
    include_target_ancestors=False,
    include_target_descendants=False,
    resolve_mode="active_only",
) -> list[CodeMapping]
```

`max_depth=0` keeps mapping exact. Higher values enable bounded hierarchy
fallback where the engine supports it.

## Hierarchy

```python
mt.get_code_relations(
    codes,
    engine,
    *,
    direction,
    max_depth=1,
) -> list[CodeRelation]

mt.get_parents(codes, engine) -> list[CodeRelation]
mt.get_children(codes, engine) -> list[CodeRelation]
mt.get_ancestors(codes, engine, *, max_depth=5) -> list[CodeRelation]
mt.get_descendants(codes, engine, *, max_depth=5) -> list[CodeRelation]
```

`direction` accepts `parents`, `children`, `ancestors`, and `descendants`.

## Resolution

```python
mt.resolve_codes(codes, engine) -> list[CodeResolution]
```

Service functions that perform downstream work accept these resolution modes:

```python
"active_only"      # use inputs as-is unless NDC resolution is needed
"resolve_current" # resolve obsolete/historical/NDC inputs before work
"historical"      # preserve original input codes
```

## Optimize

```python
mt.optimize_codes(
    codes,
    *,
    engine,
    source=None,
    relationship=None,
    output_format="compact",
    include_codes=False,
) -> OptimizeResult
```

## Discovery

```python
mt.get_source_stats(engine, *, sources=None) -> list[SourceStats]

mt.sample_source_codes(
    engine,
    *,
    sources=None,
    per_source=10,
) -> list[CodeRef]

mt.get_code_ttys(codes, engine) -> list[CodeInfo]

mt.search_names(
    query,
    engine,
    *,
    sources=None,
    tty_filters=None,
    limit=25,
) -> list[NameSearchResult]
```

## ConceptMap

```python
mt.iter_concept_map(
    codes,
    engine,
    *,
    target="patient_friendly",
    batch_size=5000,
    max_depth=5,
    target_source="PATIENT_FRIENDLY",
) -> Iterator[ConceptMapRow]

mt.get_concept_map(...) -> list[ConceptMapRow]

mt.iter_mapping_concept_map(
    codes,
    engine,
    *,
    target_sources,
    batch_size=5000,
    max_results_per_code=50,
    max_depth=0,
    include_target_ancestors=False,
    include_target_descendants=False,
) -> Iterator[ConceptMapRow]

mt.get_mapping_concept_map(...) -> list[ConceptMapRow]
```

The iterator forms are intended for large exports and avoid holding every row
in memory.

## Bulk Iterators

```python
mt.iter_batches(values, size)

mt.iter_lookup_bulk(
    codes,
    engine,
    *,
    batch_size=5000,
    include_missing=True,
)

mt.iter_mapping_bulk(
    codes,
    engine,
    *,
    target_sources,
    batch_size=5000,
    max_results_per_code=50,
    max_depth=0,
    include_target_ancestors=False,
    include_target_descendants=False,
)

mt.iter_hierarchy_bulk(
    codes,
    engine,
    *,
    direction,
    batch_size=5000,
    max_depth=1,
)

mt.iter_patient_friendly_bulk(
    codes,
    engine,
    *,
    batch_size=5000,
    max_depth=5,
)
```
