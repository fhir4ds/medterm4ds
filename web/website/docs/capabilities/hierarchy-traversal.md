---
title: Hierarchy Traversal
---

# Hierarchy Traversal

Walk parent/child/ancestor/descendant relationships within a code system.

## Quick example

```python
import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb")

# Direct parents
parents = terms.parents("SNOMEDCT_US", "44054006")

# All ancestors (multi-level)
ancestors = terms.ancestors("SNOMEDCT_US", "44054006", max_depth=5)

# Direct children
children = terms.children("SNOMEDCT_US", "73211009")

# All descendants
descendants = terms.descendants("SNOMEDCT_US", "73211009", max_depth=5)
```

## Directions

| Direction | Description | Depth |
|---|---|---|
| `parents` | Direct parent codes | 1 level |
| `children` | Direct child codes | 1 level |
| `ancestors` | All ancestors up the hierarchy | Configurable (`max_depth`) |
| `descendants` | All descendants down the hierarchy | Configurable (`max_depth`) |

## How it works

medterm4ds walks the source-native hierarchy:
- **SNOMED CT**: `isa` relationships
- **ICD-10-CM/PCS**: `PAR`/`CHD` (parent/child)
- **LOINC**: multi-axial hierarchy
- **RxNorm**: `has_ingredient`, `has_part`, `consists_of` (via TTY topology)

The engine uses prepared `mt4ds.walk_edges` tables for fast traversal when available, falling back to recursive CTEs on raw `mrrel` for unprepared databases.

For wide SNOMED subtrees (e.g., descendants of Diabetes Mellitus), the recursive CTE path enumerates all paths via a path-string column and explodes. FHIR `$subsumes` and `$expand?fhir_vs=isa` use a layer-by-layer BFS that visits each node once (O(nodes) not O(paths)) — `$subsumes` on Diabetes Mellitus (was timing out at 60s+) now returns in ~750ms.

## Cycle detection

All hierarchy walks use either delimited path tracking (`>code>`) on the recursive CTE path or a visited set on the BFS path. Safe on any hierarchy structure.

## FHIR $subsumes

The FHIR R4 terminology server exposes hierarchy relationships via `$subsumes`:

```bash
curl "http://127.0.0.1:8001/fhir/CodeSystem/\$subsumes?system=http://snomed.info/sct&codeA=73211009&codeB=44054006"
# → outcome: subsumes
```

Outcomes: `equivalent`, `subsumes`, `subsumed-by`, `not-subsumed`. The walk
uses BFS with early-exit (`stop_at=candidate`) so the typical 1-hop case is
a single SQL query.
