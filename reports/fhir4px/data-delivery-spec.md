# fhir4px Data Delivery Specification

**Version:** 3.1 — As Built (Tier A refreshed)
**Spec date:** 2026-06-26
**Built:** 2026-06-26
**UMLS release:** 2026AA
**Pipeline:** `scripts/build_fhir4px_all.py`

---

## Principle

The model team provides source data assets. The app team builds runtime indexes (BM25, lookup tables) from those assets. All source data ships from one place on a schedule.

---

## How to rebuild (start here)

```bash
cd /mnt/d/medterm4ds

# Prerequisites (one-time, idempotent):
python3 scripts/load_mrsty.py           # ~12s — loads MRSTY table
python3 scripts/load_mrconso_lat.py     # ~10s — adds lat column to mrconso

# Full pipeline (~10 minutes):
PYTHONPATH=src python3 scripts/build_fhir4px_all.py

# Rebuild associations with Synthea labs:
PYTHONPATH=src python3 scripts/build_fhir4px_associations.py \
  --synthea-labs /mnt/d/fhir4px/public/terminology/synthea_condition_lab_codes.json
```

### Pipeline steps

| Step | Script | Output | Runtime |
|------|--------|--------|---------|
| 1 | `build_fhir4px_patient_friendly.py` | `patient_friendly_names.csv` + 8 per-system JSONs | ~7.5 min |
| 2 | `build_fhir4px_embedding_index.py` | 6 `embedding_index_*.jsonl` files | ~55s |
| 3 | `build_fhir4px_associations.py` | `condition_associations.json` | ~33s |
| 4 | `build_fhir4px_rxnorm_ingredients.py` | `rxnorm-ingredients.json` | ~3s |

---

## Deliverable 1: embedding_index_*.jsonl (6 categories)

One JSON record per addressable code. The app team builds BM25 inverted indexes from these files.

**Files produced:**

| File | Records | Size |
|------|---------|------|
| `embedding_index_condition.jsonl` | 245,425 | 162 MB |
| `embedding_index_lab.jsonl` | 116,498 | 91 MB |
| `embedding_index_medication.jsonl` | 124,540 | 99 MB |
| `embedding_index_procedure.jsonl` | 154,997 | 127 MB |
| `embedding_index_vaccine.jsonl` | 291 | 0.2 MB |
| `embedding_index_body_structure.jsonl` | 39,995 | 25 MB |

**Per-source filters:**

| Source | Filter | Codes |
|--------|--------|-------|
| ICD10CM | all | 98,506 |
| ICD10PCS | leaf-only (no PAR/RB children) | 79,512 |
| SNOMEDCT_US | TUI-filtered (condition/lab/procedure/medication/vaccine/body_structure); condition TUIs include T033 (Finding) and T184 (Symptom) | 285,845 |
| LNC | TTY=LN only | 104,334 |
| RXNORM | IN, MIN, SCDG, SCD, SBD, BN, PIN, SCDC, SBDC, SBDF, BPCK, GPCK | 83,112 |
| CPT | all | 15,468 |
| HCPCS | all | 7,685 |
| CVX | all | 288 |
| **Total** | | **681,746** |

**Record schema:**

```jsonc
{
  "category": "medication",           // condition | lab | medication | procedure | vaccine | body_structure
  "tty": "SCD",                       // source vocabulary term type
  "friendly_name": "Metformin Oral Product",
  "code": {
    "source": "RXNORM",
    "code": "860975",
    "tty": "SCD",
    "cui": "C0978484",
    "name": "24 HR metformin hydrochloride 500 MG Extended Release Oral Tablet"
  },
  "rule": "addressable",
  "semantic_types": ["T200"],
  "atc": {                             // RXNORM only — ATC levels 1-5
    "atc_code": "A10BA02", "atc_level1": "A", ...
  },
  "component": null,                   // LNC only — LOINC COMPONENT (first axis)
  "icd10_code": null,                  // conditions/body_structure only — shortest ICD-10 sharing CUI
  "ingredient_codes": ["6809"],        // RXNORM only — ingredient IN codes (SCD/SBD via multi-hop SCDC traversal)
  "vectors": {
    "technical": "24 HR metformin ...",  // preferred-term name (per-source TTY)
    "synonyms": ["amoxicillin", ...],    // K=8 English synonyms (LOINC COMPONENT, combo ingredients first)
    "friendly": "Metformin Oral Product",
    "hierarchy": ["A10 (A10BA parent)", "A10BA (A10BA02 parent)", "A10BA02 — metformin"]
  }
}
```

**Key fields for the app team's BM25 builder:**

- `code.source` + `code.code` → `rid_to_code` / `rid_to_system`
- `friendly_name` → `rid_to_friendly_name`
- `icd10_code` (conditions) → `rid_to_canonical_code` / `rid_to_canonical_system`
- `ingredient_codes` (medications) → `rid_to_ingredient_codes`
- `vectors.technical` + `vectors.synonyms` → BM25 search text per record

---

## Deliverable 2: condition_associations.json

Condition → lab + medication associations at ingredient level.

**Built from:** UMLS `may_treat` + `may_prevent` (all depths 0–5, both ICD-10 and SNOMED) + Synthea condition-lab baseline (when `--synthea-labs` is passed to the build).

**Stats:**

| Metric | Value |
|--------|-------|
| Conditions | 102,317 |
| Medication associations | 2,864,440 |
| Lab associations | 0 (Synthea baseline not passed in default build; see "Known issues" below) |
| File size | 220 MB |

**Structure:**

```json
{
  "_meta": {
    "schema_version": "1.0",
    "generated_at": "2026-06-26T...",
    "sources": {
      "labs": "Synthea modules (if provided) + UMLS monitoring",
      "medications": "UMLS may_treat + may_prevent (all depths 0-5, ingredient-level)"
    },
    "stats": { "conditions": 102317, "medication_associations": 2864440, "lab_associations": 0 }
  },
  "E11": {
    "labs": [
      {"code": "4548-4", "strength": "strong"},
      {"code": "2339-0", "strength": "strong"}
    ],
    "medications": [
      {"code": "6809", "strength": "strong", "relationship": "treats", "depth": 0},
      {"code": "1191", "strength": "moderate", "relationship": "treats", "depth": 2},
      {"code": "854899", "strength": "weak", "relationship": "prevents", "depth": 4}
    ]
  }
}
```

**Field rules:**

| Field | Labs | Medications |
|-------|------|-------------|
| `code` | Bare LOINC code | Bare RxNorm ingredient code (TTY=IN) |
| `strength` | `"strong"` / `"moderate"` / `"weak"` | Same |
| `relationship` | Not needed (implicit: monitors) | `"treats"` or `"prevents"` |
| `depth` | Not included (all strong from Synthea) | Integer 0–5 (hierarchy walk depth to find may_treat/may_prevent edge) |

**Strength from depth:**

| depth | strength | Meaning |
|-------|----------|---------|
| 0–1 | strong | Direct or parent — clinically definitive |
| 2 | moderate | Grandparent — reasonable, less specific |
| 3–5 | weak | Distant ancestor — possible but noisy |

The app can filter by strength or depth at runtime. All depths are included so the app controls the confidence threshold.

**Keys:** bare condition codes. ICD-10 codes (start with letter) and SNOMED codes (pure numeric) coexist without prefix.

---

## Deliverable 3: rxnorm-ingredients.json

Product code → ingredient codes mapping for runtime decomposition.

**Stats:** 92,654 product entries, 4.7 MB.

```json
{
  "_meta": { "schema_version": "1.0", "count": 92654 },
  "860975": [{"c": "6809", "n": "metformin"}],
  "1000000": [{"c": "17767", "n": "amlodipine"}, {"c": "321064", "n": "olmesartan"}],
  "1000086": []
}
```

Includes all RxNorm TTYs: IN, MIN, SCDG, SCD, SBD, SCDC, SBDC, SBDF, BN, PIN. Products without ingredient edges (BN, PIN) have empty arrays `[]`.

**Source:** UMLS MRCONSO/MRREL via multi-hop traversal (SCDC for SCD, has_tradename for SBD, has_part for MIN).

---

## Deliverable 4: patient_friendly_names (per-system JSON files)

Tier 1 deterministic lookup: code → friendly name.

| File | Entries |
|------|---------|
| `patient_friendly_icd10cm.json` | 98,506 |
| `patient_friendly_icd10pcs.json` | 192,560 |
| `patient_friendly_snomedct_us.json` | 386,110 |
| `patient_friendly_rxnorm.json` | 124,919 |
| `patient_friendly_lnc.json` | 301,558 |
| `patient_friendly_cpt.json` | 15,468 |
| `patient_friendly_hcpcs.json` | 7,685 |
| `patient_friendly_cvx.json` | 288 |

Each entry: `{ "code": { "name": "...", "friendly_source": "...", "match_type": "...", "cui": "..." } }`

Also available as a single CSV: `patient_friendly_names.csv` (1,127,095 rows, enriched with CUI/AUI/TTY/semantic_types).

---

## Runtime resolution flow

```
FHIR Condition (code: snomed:44054006)
  │
  ├─ Tier 1: patient_friendly_snomedct_us.json lookup by "44054006"
  │   → { name: "Diabetes Type 2", code: "44054006", system: "snomed" }
  │   → canonical_code from condition BM25: "E11" (from icd10_code field)
  │
  └─ Tier 2: BM25 search against condition_bm25.json
      → { friendly_name, code, system, canonical_code, canonical_system, score }

FHIR MedicationRequest (code: rxnorm:860975)
  │
  ├─ Tier 1: patient_friendly_rxnorm.json lookup by "860975"
  │   → { name: "Metformin Oral Product", code: "860975", system: "rxnorm" }
  │   → ingredient_codes from medication BM25: ["6809"]
  │
  └─ Tier 2: BM25 search against medication_bm25.json
      → { friendly_name, code, system, ingredient_codes, score }

Downstream:
  condition_associations["E11"] → labs + medications
  ingredient "6809" → scan associations → conditions listing "6809" → E11
  gbd_weights["E11"] → 0.63 (ICD-10 canonical only)
  reference_ranges[loinc_code] → { low, high, unit }
```

---

## Canonical code computation (conditions only)

At BM25 build time, for each condition entry:
1. If `icd10_code` is present → `canonical_code = icd10_code`, `canonical_system = "icd10"`
2. If `icd10_code` is null but the entry has associations data → `canonical_code = own SNOMED code`, `canonical_system = "snomed"`
3. No associations data → `canonical_code = null`

**Coverage:** 111,256 of 285,420 condition/body_structure entries (38%) have a non-null `icd10_code`.

---

## Naming rules

- **Friendly name picking:** MEDLINEPLUS source preferred → fall back to shortest → keep others as aliases/search texts
- **Disambiguation:** When multiple CUIs share a friendly_name, append LOINC system axis for labs (e.g., "Creatinine, Serum" vs "Creatinine, Urine")
- **Short name:** `friendly_name` = shortest patient-comprehensible form, no technical qualifiers

---

## App team provides as inputs

| File | Content | Status |
|------|---------|--------|
| `gbd_disability_weights.json` | 5,111 ICD-10 codes → DW | Provided |
| `reference_ranges.json` | 35 ACP labs with ranges | Provided |
| `synthea_condition_lab_codes.json` | 34 conditions → LOINC codes | Provided — merged into `condition_associations.json` |

---

## What gets removed after migration

| Asset | Why |
|-------|-----|
| `canonical-codes/` | BM25 index carries canonical_code via `icd10_code` field |
| `condition_lab_relationships.json` | Replaced by `condition_associations.json` labs arrays |
| `condition_medication_relationships.json` | Replaced by `condition_associations.json` medications arrays |
| `rxnorm_ingredient_decomposition.csv` | Replaced by `rxnorm-ingredients.json` |

---

## Build cadence

| Source data | Update frequency |
|-------------|-----------------|
| `embedding_index_*.jsonl` | Per UMLS release (~2× yearly) |
| `condition_associations.json` | UMLS release + Synthea curated updates |
| `rxnorm-ingredients.json` | Per UMLS release |
| `patient_friendly_*.json` | Per UMLS release |
| `reference_ranges.json` | Ad-hoc (curated) |
| `gbd_disability_weights.json` | Annual (IHME) |

---

## Deviations from original spec (v3.0)

| Item | Original spec | As built | Reason |
|------|---------------|----------|--------|
| Association depths | 0–4 (exclude 5) | 0–5 (all included) | User requested same depth coverage as v1, with depth field for app-side filtering |
| Association entries | `strength` only | `strength` + `depth` | App needs raw depth to display confidence level |
| RxNorm ingredients TTYs | IN, MIN, SCD, SBD, SCDG | + BN, PIN, SCDC, SBDC, SBDF | Aligning scope with `embedding_index_medication` TTY filter (Tier A fix, 2026-06-26). Adds 29,182 products vs original spec |
| Category count | 5 (condition, lab, medication, procedure, vaccine) | 6 (+ body_structure) | Added body_structure for SNOMED anatomy TUIs (T023–T031) |
| File size (associations) | ~50MB estimated | 220MB actual | Estimate was low; full ICD-10 + SNOMED at all depths is larger |
| Condition embedding count (v3.0) | 201,447 | 245,425 | Tier A fix: added T033 (Finding) and T184 (Symptom) to `_CONDITION_TUIS` (2026-06-26). +43,978 conditions including the SNOMED "Clinical finding" hierarchy |
| Medication embedding count (v3.0) | 117,544 | 124,540 | ATC standalone records added (6,996) — not in original spec table |
| `atc.atc_name` determinism (v3.0) | non-deterministic | deterministic | Tier A fix: added `atc_name` to ROW_NUMBER ORDER BY (2026-06-26). 214 records previously picked names randomly from a multiset |
| Lab associations (v3.0) | 283 (Synthea) | 0 | `build_fhir4px_all.py` orchestrator does not pass `--synthea-labs`. KNOWN ISSUE — re-run with `--synthea-labs path/to/synthea.json` to restore |
