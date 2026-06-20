# fhir4px Data Deliverable

UMLS-derived tables for the fhir4px model. Produced by `medterm4ds` from a local UMLS Metathesaurus DuckDB build.

- **Produced**: 2026-06-20
- **medterm4ds version**: 0.0.1
- **Source data**: `/mnt/d/medterm4ds/data/umls_current.duckdb` → `umls_2026aa.duckdb` (UMLS 2026AA release)
- **Last commit**: `b69f24a` (Add embedding index builder for canonical codes)

## Files

| File | Rows | Size | Last regenerated | Purpose |
|------|------|------|------------------|---------|
| `patient_friendly_names.csv` | 1,127,094 | 165 MB | 2026-06-20 | Patient-friendly name for every active code across 8 source vocabularies |
| `rxnorm_ingredient_decomposition.csv` | 125,894 | 15 MB | 2026-06-15 | RxNorm product → ingredient(s) with ATC levels 1–5 |
| `condition_medication_ingredient.csv` | 2,984,437 | 218 MB | 2026-06-15 | Condition → medication ingredient (may_treat / may_prevent) |
| `canonical_codes.csv` | 196,509 | 27 MB | 2026-06-20 | One canonical code per (category, friendly_name); categories: condition, lab, medication, vaccine |
| `embedding_index.jsonl` | 196,509 | 134 MB | 2026-06-20 | Embedding-ready documents — one JSON record per canonical with 4 vector texts plus metadata |

CSV format: UTF-8, comma-delimited, double-quote text qualifier, header row.
JSONL format: UTF-8, one JSON object per line.

**Regeneration history**:

- **2026-06-15**: initial build of Tables 1, 2, 3.
- **2026-06-18**: added `canonical_codes.csv`; enriched Table 1 with `source_tty`/`cui`/`aui`.
- **2026-06-20**: loaded MRSTY into DuckDB; re-routed SNOMED via TUI filter; added `semantic_types` column to Table 1; re-introduced SNOMED canonical candidates per category with TUI guard; added `category=vaccine` for CVX; regenerated Table 1 and canonical_codes.csv; added `embedding_index.jsonl`.

Tables 2 and 3 still reflect the 2026-06-15 build. They are independent of MRSTY routing and canonical_codes changes; rerun `scripts/build_clinical_relationship_tables.py --tables 2 3` only if a UMLS refresh requires it.

---

## Prerequisites

- UMLS DuckDB at `/mnt/d/medterm4ds/data/umls_current.duckdb` (UMLS Metathesaurus with `mrconso`, `mrrel`, and `mrsty` tables)
- Python 3.10+
- `medterm4ds` repo at `/mnt/d/medterm4ds`
- Python dependencies installed (`pip install -e .[mcp]` or equivalent)

The `mrsty` table must be loaded before rebuild. One-time setup (~12 seconds):

```bash
python3 scripts/load_mrsty.py
```

---

## Full Rebuild

Total wall time: ~7.5 minutes on the reference machine (WSL2, fast memory profile).

```bash
cd /mnt/d/medterm4ds

# 0. One-time: load MRSTY into the DuckDB (~12 seconds)
python3 scripts/load_mrsty.py

# 1. Build Tables 1, 2, 3 (~5 minutes)
mkdir -p reports/fhir4px
PYTHONPATH=src python3 scripts/build_clinical_relationship_tables.py \
  --memory-profile fast \
  --output-dir reports/fhir4px

# 2. Enrich Table 1 with CUI/AUI/source_tty/semantic_types and build canonical_codes.csv (~2 minutes)
PYTHONPATH=src python3 scripts/build_canonical_codes.py

# 3. Build embedding_index.jsonl from canonical_codes.csv (~20 seconds)
PYTHONPATH=src python3 scripts/build_embedding_index.py
```

### Selective rebuild

```bash
# Only Tables 2 and 3 (skip Table 1)
PYTHONPATH=src python3 scripts/build_clinical_relationship_tables.py \
  --memory-profile fast \
  --output-dir reports/fhir4px \
  --tables 2 3

# Only rebuild canonical_codes.csv (keep existing Table 1)
PYTHONPATH=src python3 scripts/build_canonical_codes.py --skip-enrich
```

### Scripts

| Script | Produces | Notes |
|--------|----------|-------|
| `scripts/load_mrsty.py` | `mrsty` table in DuckDB | One-time. Loads UMLS MRSTY.RRF (3.9M rows, ~12s). |
| `scripts/build_clinical_relationship_tables.py` | Tables 1, 2, 3 | Delegates Table 1 to `scripts/run_patient_friendly_review.py` |
| `scripts/build_canonical_codes.py` | Enriched Table 1, canonical_codes.csv | Joins Table 1 against `mrconso` to populate CUI/AUI/source_tty, then groups by friendly name |
| `scripts/build_embedding_index.py` | embedding_index.jsonl | Reads canonical_codes.csv and emits one JSON record per canonical with 4 vector texts plus metadata |

---

## Schema Details

### Table 1: `patient_friendly_names.csv`

Map of every active code to its patient-friendly name. The primary join key for downstream tables.

| Column | Type | Description |
|--------|------|-------------|
| `source` | string | Source vocabulary (`RXNORM`, `ICD10CM`, `ICD10PCS`, `SNOMEDCT_US`, `LNC`, `CPT`, `HCPCS`, `CVX`) |
| `code` | string | Code in the source vocabulary |
| `name` | string | Patient-friendly name (Title Case) |
| `friendly_source` | string | How the name was derived (`MEDLINEPLUS`, `CHV`, `LNC`, `RXNORM`, `ICD10CM`, `SNOMEDCT_US`, `CPT`, `HCPCS`, `CVX`, `ICD10PCS`) |
| `match_type` | string | Match strategy (`exact`, `original`, `broader`, `group`, `ingredient`, `first_axis`, `same_cui`, `snomed_fallback`, etc.) |
| `match_depth` | integer | Hierarchy depth used to resolve the name (0 = exact concept) |
| `technical_name` | string | Original clinical name from the source vocabulary |
| `source_tty` | string or null | Term type of the source-vocabulary atom (populated by JOIN against `mrconso`) |
| `cui` | string or null | UMLS CUI for the source code (populated by JOIN against `mrconso`) |
| `aui` | string or null | UMLS AUI for the preferred atom (populated by JOIN against `mrconso`) |

**Coverage**: 1,127,094 codes across 8 source vocabularies. Generated by the runtime patient-friendly resolver (`get_patient_friendly_names`) and then enriched via `mrconso` JOIN for the `source_tty`/`cui`/`aui` columns.

**Per-source preferred-atom TTY used for `source_tty`/`cui`/`aui`**:

| Source | Preferred TTY |
|--------|---------------|
| ICD10CM | `HT` (Hierarchical Term) |
| ICD10PCS, SNOMEDCT_US, CVX, HCPCS, CPT | `PT` (Preferred Term) |
| LNC | `LN`, `LPN`, `LA` (whichever is present) |
| RXNORM | `IN`, `MIN`, `SCDG`, `SCD` |
| MSH | `MH` |

---

### Table 2: `rxnorm_ingredient_decomposition.csv`

Map of RxNorm product codes to their active ingredient(s), with ATC class levels 1–5.

| Column | Type | Description |
|--------|------|-------------|
| `rxnorm_code` | string | RxNorm code at the product/ingredient level |
| `rxnorm_tty` | string | Term type of `rxnorm_code` (`SCDG`, `SCD`, `SBD`, `MIN`, `PIN`, `IN`, `BN`) |
| `rxnorm_name` | string | RxNorm preferred name at the source code level |
| `ingredient_rxnorm_code` | string or null | RxNorm ingredient (`IN`) code |
| `ingredient_name` | string or null | Ingredient name |
| `atc_code` | string or null | Full ATC code (level 5) for the ingredient, if a crosswalk exists |
| `atc_level1` | string or null | ATC level 1 (anatomical main group, 1 char — e.g., `A`) |
| `atc_level2` | string or null | ATC level 2 (therapeutic main group, 3 chars — e.g., `A10`) |
| `atc_level3` | string or null | ATC level 3 (pharmacological subgroup, 4 chars — e.g., `A10B`) |
| `atc_level4` | string or null | ATC level 4 (chemical subgroup, 5 chars — e.g., `A10BA`) |
| `atc_level5` | string or null | ATC level 5 (chemical substance, 7 chars — e.g., `A10BA02`) |

**Coverage by `rxnorm_tty`** (rows / ATC coverage):

| TTY | Rows | With ATC |
|-----|------|----------|
| SCD | 40,119 | 83% |
| SBD | 20,854 | 84% |
| SCDG | 21,997 | 84% |
| MIN | 18,914 | 83% |
| IN | 15,225 | 22% (expected — IN-level ATC is sparse in UMLS) |
| BN | 5,161 | 0% (null ingredient rows, as specified) |
| PIN | 3,624 | 0% (null ingredient rows) |

**Relationship traversal**: The data's `has_ingredient` direction is `AUI1=ingredient, AUI2=product` (verified). Different product TTYs resolve via different paths:
- SCDG/SCD/SBD/MIN: direct `has_ingredient` from IN
- SCD: also traverses `SCDC` via `consists_of`
- SBD: also traverses `SCD` via `has_tradename`, then `SCDC`
- MIN: via `IN has_part MIN`
- IN: maps to itself
- PIN, BN: no ingredient edge in UMLS — null row emitted

ATC levels are extracted from the 7-char ATC code via `substr` (lengths 1, 3, 4, 5, 7).

---

### Table 3: `condition_medication_ingredient.csv`

Map of condition codes to medication ingredients that treat or prevent them. Targets `may_treat` and `may_prevent` UMLS relationships.

| Column | Type | Description |
|--------|------|-------------|
| `condition_source` | string | `ICD10CM` or `SNOMEDCT_US` |
| `condition_code` | string | Condition code in the source vocabulary |
| `condition_name` | string | Condition name at this code |
| `match_depth` | integer | Hierarchy depth walked from input to the MSH MH that carries the may_treat/may_prevent edge (0 = direct CUI match to MSH MH) |
| `medication_rxnorm_code` | string | RxNorm ingredient (`IN`) code |
| `medication_name` | string | Ingredient name |
| `relationship_type` | string | `may_treat` or `may_prevent` |

**Coverage by (source, relationship_type)**:

| Source | may_treat | may_prevent |
|--------|-----------|-------------|
| ICD10CM | 72,704 | 15,227 |
| SNOMEDCT_US | 2,103,787 | 792,719 |

**Filter**: Target TTY = `IN` (ingredient only). No SCDG/MIN/SCD/product-level targets — those are decomposed separately in Table 2.

**Deduplication**: One row per `(condition_source, condition_code, medication_rxnorm_code, relationship_type)`. If a pair appears at multiple depths, the shallowest is kept (`match_depth` = min).

**Hierarchy walk**: From the input condition, walks `PAR`/`RB` edges up to `max_depth=5`. Cycle detection uses delimited membership (`position(' -> ' || parent.AUI || ' -> ' IN ' -> ' || walk.path_auis || ' -> ') = 0`) so AUI prefix collisions don't cause false cycles.

**Other relationship types out of scope**: `may_diagnose`, `contraindicated_with_disease` are not included in this deliverable.

---

### `canonical_codes.csv`

One canonical code per `(category, friendly_name)`. Use this to map any patient-friendly name to a representative code in the target system.

| Column | Type | Description |
|--------|------|-------------|
| `category` | string | `condition`, `lab`, `medication`, or `vaccine` |
| `friendly_name` | string | The patient-friendly name from Table 1 |
| `canonical_source` | string | `ICD10CM` (condition), `LNC` (lab), `RXNORM` (medication), or `CVX` (vaccine). SNOMEDCT_US may appear as a fallback source per category. |
| `canonical_code` | string | The canonical code in the target system |
| `canonical_tty` | string or null | TTY of the canonical code in its source vocabulary |
| `canonical_cui` | string or null | UMLS CUI of the canonical code |
| `canonical_name` | string | Preferred name of the canonical code in its source vocabulary (for QA) |
| `rule` | string | How the canonical was chosen (see rules below) |
| `candidate_count` | integer | How many input codes in Table 1 shared this `(category, friendly_name)` |

**Rows by (category, rule)**:

| Category | Rule | Rows |
|----------|------|------|
| condition | `icd10cm_shortest` | 27,577 |
| condition | `snomedct_condition_fallback` | 10,268 |
| lab | `lnc_shortest` | 113,670 |
| lab | `snomedct_lab_fallback` | 1,454 |
| medication | `rxnorm_in` | 14,617 |
| medication | `rxnorm_min` | 3,827 |
| medication | `rxnorm_scdg` | 8,909 |
| medication | `rxnorm_other` | 14,503 |
| medication | `snomedct_medication_fallback` | 1,575 |
| vaccine | `cvx_shortest` | 108 |
| vaccine | `snomedct_vaccine_fallback` | 1 |

**Canonical rules**:

- **condition**: prefer ICD10CM (`icd10cm_shortest` — shortest ICD10CM code). Fall back to SNOMED (`snomedct_condition_fallback`) when no ICD10CM candidate exists, only if the SNOMED concept's TUI is disease/finding-like (T019, T020, T037, T046, T047, T048, T049, T190, T191).
- **lab**: prefer LNC (`lnc_shortest`). Fall back to SNOMED (`snomedct_lab_fallback`) when TUI is T034 or T059.
- **medication**: prefer RXNORM; rank TTY=IN > MIN > SCDG > other (`rxnorm_in`/`rxnorm_min`/`rxnorm_scdg`/`rxnorm_other`). Fall back to SNOMED (`snomedct_medication_fallback`) when TUI is T121, T123, or T200.
- **vaccine**: prefer CVX (`cvx_shortest`). Fall back to SNOMED (`snomedct_vaccine_fallback`) when the SNOMED concept shares a CUI with any CVX atom.

---

### `embedding_index.jsonl`

Embedding-ready documents — one JSON record per canonical code. Each record carries 4 vector texts (for multi-vector retrieval) plus metadata for re-ranking and filtering. The model team embeds each vector field independently.

**Schema** (per JSON record):

| Field | Type | Description |
|-------|------|-------------|
| `category` | string | `condition`, `lab`, `medication`, or `vaccine` |
| `friendly_name` | string | The patient-friendly name from Table 1 (join key) |
| `canonical.source` | string | Source vocabulary (`ICD10CM`, `LNC`, `RXNORM`, `CVX`, `SNOMEDCT_US`) |
| `canonical.code` | string | The canonical code in the source vocabulary |
| `canonical.tty` | string or null | TTY of the canonical atom in its source vocabulary |
| `canonical.cui` | string or null | UMLS CUI of the canonical atom |
| `canonical.name` | string or null | Preferred name of the canonical code in its source vocabulary |
| `rule` | string | Canonical-selection rule (see canonical_codes.csv rules) |
| `candidate_count` | integer | How many input codes in Table 1 shared this `(category, friendly_name)` |
| `semantic_types` | array of string | UMLS TUIs (e.g., `["T047"]`) looked up via `mrsty` on the canonical CUI |
| `atc` | object or null | (RXNORM only) ATC levels 1–5 with name; null when no ATC crosswalk exists |
| `vectors.technical` | string | Preferred-term name in the source vocabulary (per-source preferred TTY: ICD10CM HT, SNOMED/CVX/CPT/HCPCS/ICD10PCS PT, LNC LN/LPN, RXNORM IN/MIN/SCDG/SCD) |
| `vectors.synonyms` | array of string | Up to K=8 synonyms sharing the CUI, prioritized: MSH > MEDLINEPLUS > CHV > SNOMEDCT_US > ICD10CM > RXNORM > LNC > CPT/HCPCS/CVX > MTH > others. Excludes the technical name itself. |
| `vectors.friendly` | string | The patient-friendly name (same as `friendly_name`) |
| `vectors.hierarchy` | array of string | Source-specific 3-level ancestor chain (broadest first). ICD10CM: chapter + ancestors; SNOMEDCT_US: PAR/RB ancestors; LNC: LOINC CLASS; RXNORM: ATC level 2 → level 4 → level 5 with names. Empty array when no hierarchy exists. |

**Coverage from the current build**:

| Field | Coverage |
|-------|----------|
| `vectors.technical` | 100% |
| `vectors.friendly` | 100% |
| `vectors.synonyms` | 60.5% (codes that share a CUI with another atom) |
| `vectors.hierarchy` | 48.6% (codes with a meaningful class/ancestor — LNC LA codes lack CLASS, etc.) |
| `atc` | 6.7% of medication rows (RXNORM IN-level ATC coverage; SCDG/MIN rarely have direct ATC) |
| `semantic_types` | 100% |

**Sample record** (medication):

```json
{
  "category": "medication",
  "friendly_name": "Phenylephrine",
  "canonical": {
    "source": "RXNORM", "code": "8163", "tty": "IN",
    "cui": "C0031469", "name": "phenylephrine"
  },
  "rule": "rxnorm_in",
  "candidate_count": 58,
  "semantic_types": ["T109", "T121"],
  "atc": {
    "atc_code": "R01AA04",
    "atc_level1": "R", "atc_level2": "R01", "atc_level3": "R01A",
    "atc_level4": "R01AA", "atc_level5": "R01AA04",
    "atc_name": "phenylephrine"
  },
  "vectors": {
    "technical": "phenylephrine",
    "synonyms": [
      "(R)-3-Hydroxy-alpha-((methylamino)methyl)benzenemethanol",
      "Phenylephrine (substance)",
      "Product containing phenylephrine (medicinal product)",
      "Phenylephrine-containing product",
      "..."
    ],
    "friendly": "Phenylephrine",
    "hierarchy": [
      "R01 (R01AA parent)",
      "R01AA (R01AA04 parent)",
      "R01AA04 — phenylephrine"
    ]
  }
}
```

**Known limitation — exact-code recall**: This index is at the canonical grain (one vector set per friendly_name + category). A query like "T2DM with neuropathy" will match the canonical E11 (Diabetes Type 2) — it will not surface E11.40 (the specific subcode). If exact-code recall becomes the objective, expand the index to the clinically-addressable grain (~583K codes per the design discussion).

---

## Caveats

### SNOMED canonicals are gated by MRSTY semantic types

SNOMEDCT_US spans clinical findings, substances, and products in UMLS. We re-include SNOMED as a *fallback* canonical per category, gated by MRSTY TUI: a SNOMED concept only qualifies as a condition canonical if its TUI is disease/finding-like, only as a medication canonical if its TUI is pharmacologic-substance/clinical-drug, etc. This preserves SNOMED coverage for legitimate concepts (e.g., rare SNOMED findings with no ICD10 equivalent) while excluding the noise that previously put *Phenylephrine* in the condition bucket.

Vaccines are detected via crosswalk existence (shared CUI with a CVX atom) rather than TUI — vaccines share generic substance TUIs and aren't TUI-distinguishable.

### SNOMED → target routing in Table 1 is TUI-driven

When MRSTY is loaded, the SNOMED → target vocabulary step uses semantic-type filtering. A SNOMED Pharmacologic Substance (T121) routes to RXNORM rather than LNC, a Disease (T047) routes to ICD10CM, a Lab Procedure (T059) routes to LNC, a Therapeutic Procedure (T061) routes to CPT/ICD10PCS. CVX is preferred when a shared-CUI crosswalk exists. This means SNOMED inputs that previously resolved via LNC (e.g., Phenylephrine the substance) now resolve via RXNORM in `patient_friendly_names.csv`.

When MRSTY is absent, the engine falls back to legacy priority-only routing (CVX/RXNORM targets are not considered).

### Multi-category friendly names

14,815 distinct names appear in more than one category (typically substances like *Glucose* that are both a lab measurement and a medication ingredient). Join on `category` to disambiguate:

```sql
SELECT canonical_code
FROM canonical_codes
WHERE friendly_name = 'Glucose' AND category = 'medication';
```

### `patient_friendly_names.csv` `source_tty`/`cui`/`aui` are populated by JOIN

The runtime resolver does not populate these fields in `ProvenanceStep`. The enrichment step (`scripts/build_canonical_codes.py` Step 1) JOINs each `(source, code)` against `mrconso` using per-source preferred-term conventions to fill them in. If you regenerate Table 1 via `build_clinical_relationship_tables.py` only, those columns will be blank — run `build_canonical_codes.py` (without `--skip-enrich`) to populate them.

### LOINC `first_axis` match type

LOINC codes often resolve their friendly name via the LOINC component (the "first axis" of the LOINC multi-axial name). Multiple LOINC codes measuring the same component share the same friendly name; the canonical rule (`lnc_shortest`) picks the shortest LOINC code as representative, which often — but not always — is the most commonly ordered version.

### ATC coverage is ingredient-grain

ATC codes are looked up via shared CUI between the ingredient (RXNORM `IN`) and any ATC atom. Not every ingredient has an ATC code in UMLS — 22% coverage at IN level, 83–84% at product levels (because products inherit their ingredients' ATCs). An ingredient with multiple ATCs (different therapeutic uses) appears in multiple rows.

### UMLS data direction quirk

In this DuckDB build, `has_ingredient` is encoded as `AUI1=ingredient, AUI2=product` (the opposite of standard RxNorm documentation). The extraction SQL matches the data's actual direction. If you regenerate against a UMLS build with the standard direction, the SQL JOINs in `scripts/build_clinical_relationship_tables.py` (Table 2) and `src/medterm4ds/domains/terminology.py` (`ingredient_counts` and `expanded_edges` CTEs) will need to be flipped.

---

## Source Code References

- `scripts/build_clinical_relationship_tables.py` — Tables 1, 2, 3 builder
- `scripts/build_canonical_codes.py` — Table 1 enrichment + canonical_codes builder
- `scripts/run_patient_friendly_review.py` — Table 1 resolver (called by `build_clinical_relationship_tables.py`)
- `src/medterm4ds/services/patient_friendly.py` — Patient-friendly resolver
- `src/medterm4ds/domains/terminology.py` — Underlying terminology SQL patterns

## Git History

```
b779322 Exclude SNOMEDCT_US from canonical_codes candidates
b9a6886 Add canonical codes builder and Table 1 enrichment
ce2e545 Fix build_clinical_relationship_tables DuckDB config
d6b13a9 Add fhir4px clinical relationship table builder
```

To reproduce the exact state at delivery time:

```bash
git checkout b779322
```
