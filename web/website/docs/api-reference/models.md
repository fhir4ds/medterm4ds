---
title: Models
---

medterm4ds public result objects are frozen dataclasses. Their `to_dict()`
methods define the stable serialized output shape for `0.0.1`.

## CodeRef

```python
mt.CodeRef(source: str, code: str)
```

Represents one terminology code. `source` is normalized on construction and
`code` is stored as a string.

```python
ref = mt.CodeRef("ICD10-CM", "E11.9")
ref.source  # "ICD10CM"
ref.code    # "E11.9"
```

`CodeRef.from_pair((source, code))` and `ref.as_pair()` both use the canonical
`(source, code)` order — same as the dataclass field order, same as the
Terminology facade, same as FHIR Coding `{system, code}`. (Earlier 0.0.x
releases used a legacy `(code, source)` order in some helpers; that ambiguity
was removed because it caused silent source/code swaps when refactoring
between tuple and CodeRef forms.)

## CodeInfo

```python
mt.CodeInfo(
    code: CodeRef,
    name: str | None = None,
    cui: str | None = None,
    aui: str | None = None,
    tty: str | None = None,
    suppress: str | None = None,
)
```

`to_dict()` fields:

```python
source, code, name, cui, aui, tty, suppress
```

## CodeResolution

```python
mt.CodeResolution(
    input: CodeRef,
    resolved: CodeRef | None,
    status: str,
    match_type: str,
    input_display: str | None = None,
    resolved_display: str | None = None,
    input_cui: str | None = None,
    resolved_cui: str | None = None,
    input_aui: str | None = None,
    resolved_aui: str | None = None,
    input_suppress: str | None = None,
    resolved_suppress: str | None = None,
    replacement_relationship: str | None = None,
    normalized_code: str | None = None,
    candidates: tuple[CodeRef, ...] = (),
    matched_via: Provenance | None = None,
)
```

Used for active, obsolete, historical, missing, ambiguous, and NDC-to-RxCUI
inputs. `is_resolved` is true when `resolved` is present and `status` is not
`not_found` or `ambiguous`.

`to_dict()` fields:

```python
source, code, resolved_source, resolved_code, status, match_type,
input_display, resolved_display, input_cui, resolved_cui, input_aui,
resolved_aui, input_suppress, resolved_suppress, replacement_relationship,
normalized_code, candidates, matched_via
```

## CodeMapping

```python
mt.CodeMapping(
    source: CodeRef,
    target: CodeRef,
    relationship: str,
    match_type: str,
    match_depth: int = 0,
    source_display: str | None = None,
    target_display: str | None = None,
    source_cui: str | None = None,
    target_cui: str | None = None,
    source_aui: str | None = None,
    target_aui: str | None = None,
    target_tty: str | None = None,
    matched_via: Provenance | None = None,
)
```

`match_type`, `match_depth`, and `matched_via` explain how the mapping was
found, including exact same-CUI and hierarchy fallback paths.

`to_dict()` fields:

```python
source, code, source_display, target_source, target_code, target_display,
relationship, match_type, match_depth, source_cui, target_cui, source_aui,
target_aui, target_tty, matched_via
```

## CodeRelation

```python
mt.CodeRelation(
    source: CodeRef,
    target: CodeRef,
    relationship: str,
    depth: int = 1,
    source_display: str | None = None,
    target_display: str | None = None,
    rel: str | None = None,
    rela: str | None = None,
    source_cui: str | None = None,
    target_cui: str | None = None,
    source_aui: str | None = None,
    target_aui: str | None = None,
)
```

`relationship` is the normalized traversal direction, such as `parent`,
`child`, `ancestor`, or `descendant`.

## FriendlyNameResult

```python
mt.FriendlyNameResult(
    code: CodeRef,
    name: str,
    friendly_source: str,
    match_type: str,
    match_depth: int = 0,
    technical_name: str | None = None,
    matched_via: Provenance | None = None,
)
```

`name` is the patient-friendly display. `technical_name` is the original
source display when available.

`to_dict()` fields:

```python
code, source, name, friendly_source, match_type, match_depth,
technical_name, matched_via
```

## ConceptMapRow

```python
mt.ConceptMapRow(
    source: CodeRef,
    target: CodeRef,
    target_display: str,
    relationship: str,
    source_display: str | None = None,
    friendly_source: str | None = None,
    match_type: str | None = None,
    match_depth: int = 0,
    matched_via: Provenance | None = None,
)
```

Constructors:

```python
mt.ConceptMapRow.from_friendly_result(result)
mt.ConceptMapRow.from_mapping(mapping)
```

`to_dict()` fields:

```python
source, code, source_display, target_source, target_code, target_display,
relationship, friendly_source, match_type, match_depth, matched_via
```

## Optimize Models

```python
mt.OptimizeRule(
    include: CodeRef,
    exclude: tuple[CodeRef, ...] = (),
    covered_codes: tuple[CodeRef, ...] = (),
    excluded_codes: tuple[CodeRef, ...] = (),
)

mt.OptimizeResult(
    source: str,
    relationship: str,
    rules: tuple[OptimizeRule, ...],
    original_count: int,
    optimized_count: int,
    reduction: float,
    strategy: str = "greedy_hierarchy",
)
```

Use `result.to_dict(include_codes=True)` when review output should include the
codes covered or excluded by each rule.

## Discovery Models

```python
mt.SourceStats(source: str, code_count: int, atom_count: int)

mt.NameSearchResult(
    code: CodeRef,
    name: str,
    cui: str | None = None,
    aui: str | None = None,
    tty: str | None = None,
    match_type: str = "contains",
)
```

## Provenance

```python
mt.Provenance(strategy: str, steps: tuple[ProvenanceStep, ...] = ())

mt.ProvenanceStep(
    op: str,
    source: str | None = None,
    code: str | None = None,
    target_source: str | None = None,
    target_code: str | None = None,
    cui: str | None = None,
    aui: str | None = None,
    tty: str | None = None,
    depth: int | None = None,
    mode: str | None = None,
    name: str | None = None,
    metadata: Mapping[str, Any] = {},
)
```

`matched_via` serializes to:

```python
{
    "strategy": "...",
    "steps": [
        {"op": "input", "source": "ICD10CM", "code": "E11.9"},
        ...
    ],
}
```
