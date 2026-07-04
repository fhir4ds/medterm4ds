---
title: CLI
---

The CLI is the fastest way to inspect terminology behavior and run shell workflows.

```bash
medterm4ds lookup --db "$DB" --source ICD10CM --code E11.9
medterm4ds map --db "$DB" --source ICD10CM --target-source SNOMEDCT_US --code E11.9
medterm4ds hierarchy parents --db "$DB" --source SNOMEDCT_US --code 44054006
medterm4ds resolve --db "$DB" --source NDC --code 0002-0821-01
medterm4ds optimize --db "$DB" --source ICD10CM --code E11.40 --code E11.41
```

Use structured output for pipelines and compact output for review.
