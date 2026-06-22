# fhir4px v1 — Archived Deliverables

These scripts and reports were the first iteration of the fhir4px data
deliverable. They have been superseded by the v2 pipeline that builds
directly from UMLS per the final data-delivery-spec.md (v3.0).

## What's here

**Scripts** (in `scripts/`):
- `build_clinical_relationship_tables.py` — Tables 1, 2, 3 builder
- `build_canonical_codes.py` — canonical codes + Table 1 enrichment
- `build_embedding_index.py` — canonical-grain embedding index
- `build_embedding_index_full.py` — clinically-addressable-grain embedding index
- `filter_embedding_index.py` — per-ValueSet index filter

**Reports** (in `reports/`):
- `patient_friendly_names.csv` — Table 1 (1.1M rows)
- `rxnorm_ingredient_decomposition.csv` — Table 2 (126K rows)
- `condition_medication_ingredient.csv` — Table 3 (3M rows)
- `canonical_codes.csv` — canonical code mapping (197K rows)
- `embedding_index*.jsonl` — embedding indices (canonical + full + splits)
- `valueset_*` — Encounter Type ValueSet lookup + index
- `README.md` — deliverable documentation
- `build.log` — build log

## Why archived

The v1 pipeline evolved incrementally through multiple spec iterations.
The v2 pipeline rebuilds from UMLS source as a single clean, repeatable
process per the final spec.
