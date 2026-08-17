---
title: Quality Review
---

Terminology mappings need domain review.

The mapping quality report flags patterns such as:

- hierarchy fallback
- many target concepts for one source code
- broad target names
- low source/target name overlap
- unexpected source or target term types

Example:

```bash
python scripts/review_mapping_quality.py \
  --db /mnt/d/medterm4ds/data/umls_current.duckdb \
  --per-source 50 \
  --output-json reports/quality/mapping_quality_report.json \
  --output-csv reports/quality/mapping_review_cases.csv
```

The JSON report summarizes counts by source pair. The CSV contains one flagged
mapping per row with filterable flag columns and a blank `review_notes` column
for spreadsheet review.

Quality reports are triage aids. They are not clinical validation substitutes.
