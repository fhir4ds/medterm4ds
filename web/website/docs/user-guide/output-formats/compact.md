---
title: Tables and Trees
---

Compact output is designed for humans and MCP responses.

Use `table` when rows matter:

```bash
medterm4ds map --db "$DB" --source ICD10CM --target-source SNOMEDCT_US --code E11.9 --format table
```

Use `tree` when relationships or rules matter:

```bash
medterm4ds optimize --db "$DB" --source ICD10CM --code E11.40 --code E11.41 --format tree
```

Use dictionary output when another process needs to parse the response.
