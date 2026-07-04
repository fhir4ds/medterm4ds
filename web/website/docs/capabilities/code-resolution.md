---
title: Code Resolution
---

# Code Resolution

Resolve active, historical, obsolete, and NDC inputs to their current canonical codes.

## Quick example

```python
import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb")

# Active code
result = terms.resolve("ICD10CM", "E11")
print(result.status)      # "active"
print(result.resolved.code)  # "E11"

# NDC (drug package code)
result = terms.resolve("NDC", "0002-0821-01")
print(result.resolved.source)  # "RXNORM"
print(result.resolved.code)    # "860975"

# Obsolete code → replacement
result = terms.resolve("ICD10CM", "OLD_CODE")
print(result.status)  # "obsolete"
print(result.replacements)  # [CodeRef(...)]
```

## Resolution statuses

| Status | Meaning |
|---|---|
| `active` | Code is current and valid |
| `historical` | Code was valid but has been superseded |
| `obsolete` | Code is no longer in use; replacements may be available |
| `not_found` | Code does not exist in the source |

## NDC resolution

NDC (National Drug Code) inputs are automatically resolved to RxNorm codes. The resolver:
1. Normalizes the NDC format (11-digit, hyphenated, etc.)
2. Looks up in `mrsat` (UMLS attributes)
3. Returns the corresponding RxNorm code

## Provenance

Every resolution carries `matched_via` provenance — the exact steps the resolver took, including match type and depth. Useful for audit trails and debugging.

```python
print(result.matched_via.to_dict())
# → {"strategy": "ndc_lookup", "steps": [...]}
```
