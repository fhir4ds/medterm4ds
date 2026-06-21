#!/usr/bin/env python3
"""Build a JSONL embedding index at the clinically-addressable grain.

Reads reports/fhir4px/patient_friendly_names.csv (Table 1) and emits one
JSON record per addressable code with 4 vector texts (technical, synonyms,
friendly, hierarchy) plus metadata.

This is the larger companion to scripts/build_embedding_index.py (which
produces a canonical-only index at the friendly-name grain). Use this when
exact-code recall matters — e.g., "T2DM with neuropathy" should match
E11.40, not just E11.

Per-source filters (see reports/fhir4px/README.md for the rationale):

  ICD10CM      all 98K (no filter — every code is potentially a target)
  ICD10PCS     leaf-only ~50K (codes with no PAR/RB children — drop
               intermediate hierarchy nodes that are rarely the target)
  SNOMEDCT_US  TUI-filtered ~250K (clinical findings, diseases, procedures,
               labs, observables, substances, vaccines — exclude body parts,
               bacteria, devices, pure chemicals/proteins)
  LNC          TTY='LN' only ~113K (exclude parts, answers, display names)
  RXNORM       TTY in {IN, MIN, SCDG, SCD, SBD} ~50K (exclude PIN, BN, DF,
               and component/form atoms SCDC/SBDC/SCDF/SBDF)
  CPT, HCPCS, CVX  all (small already)

Output: reports/fhir4px/embedding_index_full.jsonl

Usage:
  PYTHONPATH=src python3 scripts/build_embedding_index_full.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import duckdb

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_INPUT = Path("reports/fhir4px/patient_friendly_names.csv")
DEFAULT_OUTPUT = Path("reports/fhir4px/embedding_index_full.jsonl")

# TUI sets used to both filter and categorize SNOMED codes. Mirrors the
# routing logic in src/medterm4ds/engines/duckdb/engine.py (_SNOMED_TUI_TARGETS)
# plus the canonical_codes categorization rules. Priority order matters:
# condition > lab > procedure > medication > vaccine.
_SNOMED_CONDITION_TUIS = ("T019", "T020", "T037", "T046", "T047", "T048", "T049", "T190", "T191")
_SNOMED_LAB_TUIS = ("T034", "T059")
_SNOMED_PROCEDURE_TUIS = ("T060", "T061", "T062", "T063")
_SNOMED_MEDICATION_TUIS = ("T121", "T123", "T200")
# Body structure TUIs (anatomy). Surfaced as their own category per the
# fhir4px spec — useful for clinical NLP queries about anatomical sites.
_SNOMED_BODY_STRUCTURE_TUIS = (
    "T023",  # Body Part, Organ, or Organ Component
    "T024",  # Tissue
    "T025",  # Cell
    "T026",  # Cell Component
    "T029",  # Body Location or Region
    "T030",  # Body Space or Junction
    "T031",  # Body Substance
)

# Source priority for synonyms — LNC_COMPONENT and RXNORM_ING are surfaced
# first (highest priority, negative) since they are clean clinical signals
# that BM25 struggles to extract from the full noisy strings.
_SYNONYM_SOURCE_PRIORITY = {
    "LNC_COMPONENT": -1,
    "RXNORM_ING": -1,
    "MSH": 0, "MEDLINEPLUS": 1, "CHV": 2, "SNOMEDCT_US": 3, "ICD10CM": 4,
    "RXNORM": 5, "LNC": 6, "CPT": 7, "HCPCS": 7, "CVX": 7, "MTH": 8, "ATC": 9,
}
_SYNONYM_K = 8


def _codes_sql() -> str:
    """Select addressable codes from Table 1 with per-source filters, and
    derive a category per code (with SNOMED TUI-driven categorization)."""
    # TUI membership for SNOMED is determined via correlated EXISTS subqueries
    # on mrsty. The CVX-crosswalk check for SNOMED uses a shared-CUI JOIN.
    return """
        WITH pf AS (
            SELECT
                CAST(source AS VARCHAR) AS source,
                CAST(code AS VARCHAR) AS code,
                CAST(name AS VARCHAR) AS name,
                CAST(friendly_source AS VARCHAR) AS friendly_source,
                CAST(technical_name AS VARCHAR) AS technical_name,
                CAST(source_tty AS VARCHAR) AS source_tty,
                CAST(cui AS VARCHAR) AS cui,
                CAST(aui AS VARCHAR) AS aui
            FROM read_csv_auto(?, HEADER=true)
        ),
        target AS (
            SELECT pf.*
            FROM pf
            WHERE
                -- ICD10CM: all codes.
                pf.source = 'ICD10CM'
                -- ICD10PCS: leaf-only (no PAR/RB children).
                OR (
                    pf.source = 'ICD10PCS'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM mrrel r2
                        JOIN mrconso child ON child.AUI = r2.AUI1
                                       AND child.SAB = 'ICD10PCS'
                                       AND child.SUPPRESS = 'N'
                        WHERE r2.AUI2 = pf.aui
                          AND r2.REL IN ('PAR', 'RB')
                    )
                )
                -- SNOMEDCT_US: TUI-filtered (any condition/lab/procedure/
                -- medication/body_structure TUI, OR a CVX crosswalk).
                -- Bacteria, devices, pure chemicals/proteins, occupations are
                -- excluded.
                OR (
                    pf.source = 'SNOMEDCT_US'
                    AND (
                        EXISTS (
                            SELECT 1 FROM mrsty m WHERE m.cui = pf.cui
                              AND m.tui IN ('T019','T020','T037','T046','T047','T048','T049','T190','T191',
                                            'T034','T059',
                                            'T060','T061','T062','T063',
                                            'T121','T123','T200',
                                            'T023','T024','T025','T026','T029','T030','T031')
                        )
                        OR EXISTS (
                            SELECT 1
                            FROM mrconso cvx
                            WHERE cvx.CUI = pf.cui AND cvx.SAB = 'CVX' AND cvx.SUPPRESS = 'N'
                        )
                    )
                )
                -- LNC: TTY='LN' (actual lab tests; exclude parts/answers).
                OR (pf.source = 'LNC' AND pf.source_tty = 'LN')
                -- RXNORM: IN, MIN, SCDG, SCD, SBD, plus brand/component/pack codes.
                OR (pf.source = 'RXNORM' AND pf.source_tty IN (
                    'IN','MIN','SCDG','SCD','SBD',
                    'BN','PIN','SCDC','SBDC','SBDF','BPCK','GPCK'
                ))
                -- CPT, HCPCS, CVX: all.
                OR pf.source IN ('CPT', 'HCPCS', 'CVX')
        )
        SELECT
            t.*,
            CASE
                WHEN t.source = 'ICD10CM' THEN 'condition'
                WHEN t.source = 'LNC' THEN 'lab'
                WHEN t.source = 'RXNORM' THEN 'medication'
                WHEN t.source = 'CVX' THEN 'vaccine'
                WHEN t.source IN ('ICD10PCS', 'CPT', 'HCPCS') THEN 'procedure'
                WHEN t.source = 'SNOMEDCT_US' THEN
                    CASE
                        WHEN EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = t.cui AND m.tui IN ('T019','T020','T037','T046','T047','T048','T049','T190','T191'))
                            THEN 'condition'
                        WHEN EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = t.cui AND m.tui IN ('T034','T059'))
                            THEN 'lab'
                        WHEN EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = t.cui AND m.tui IN ('T060','T061','T062','T063'))
                            THEN 'procedure'
                        WHEN EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = t.cui AND m.tui IN ('T121','T123','T200'))
                            THEN 'medication'
                        WHEN EXISTS (SELECT 1 FROM mrconso cvx WHERE cvx.CUI = t.cui AND cvx.SAB = 'CVX' AND cvx.SUPPRESS = 'N')
                            THEN 'vaccine'
                        WHEN EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = t.cui AND m.tui IN ('T023','T024','T025','T026','T029','T030','T031'))
                            THEN 'body_structure'
                    END
            END AS category
        FROM target t
    """


def _synonyms_sql() -> str:
    """English-only synonyms sharing the CUI, prioritized by source.

    Three categories of synonyms are unioned together; the Python
    aggregator dedupes and applies source priority + the K=8 cap.

    1. CUI-shared atoms (the standard source of synonyms).
    2. LOINC COMPONENT (from mrsat ATN='LOINC_COMPONENT') — the clinically
       meaningful first axis of the LOINC long name, surfaced as a clean
       synonym so BM25 doesn't have to parse the 6-axis format.
    3. For combination RxNorm codes (MIN, SCD, SBD, SCDG, etc.) — the
       individual ingredients from Table 2 decomposition, so a query
       mentioning only one ingredient ("Amoxicillin") can still match the
       combination product.
    """
    return (
        """
        WITH target AS (
            SELECT source, code, cui, source_tty FROM ("""
        + _codes_sql()
        + """) AS t
            WHERE cui IS NOT NULL AND cui != ''
        ),
        -- (1) Standard CUI-shared synonyms.
        cui_synonyms AS (
            SELECT DISTINCT
                t.source, t.code,
                m.STR AS synonym,
                m.SAB AS sab,
                m.TTY AS tty
            FROM target t
            JOIN mrconso m ON m.CUI = t.cui
            WHERE m.SUPPRESS = 'N'
              AND m.lat = 'ENG'
              AND m.STR IS NOT NULL AND m.STR != ''
              AND m.SAB IS NOT NULL
        ),
        -- (2) LOINC COMPONENT as a clean first-axis synonym.
        lnc_components AS (
            SELECT DISTINCT
                t.source, t.code,
                s.ATV AS synonym,
                'LNC_COMPONENT' AS sab,
                'COMPONENT' AS tty
            FROM target t
            JOIN mrsat s ON s.SAB = 'LNC' AND s.CODE = t.code AND s.ATN = 'LOINC_COMPONENT'
            WHERE t.source = 'LNC'
              AND s.ATV IS NOT NULL AND s.ATV != ''
        ),
        -- (3) Individual ingredients for combination RxNorm products.
        combination_ingredients AS (
            SELECT DISTINCT
                'RXNORM' AS source,
                t.code,
                d.ingredient_name AS synonym,
                'RXNORM_ING' AS sab,
                'IN' AS tty
            FROM target t
            JOIN read_csv_auto('"""
        + str(Path("reports/fhir4px/rxnorm_ingredient_decomposition.csv"))
        + """', HEADER=true) d
              ON d.rxnorm_code = t.code
            WHERE t.source = 'RXNORM'
              AND t.source_tty IN ('MIN','SCD','SBD','SCDG','SCDC','SBDC','SBDF','BPCK','GPCK')
              AND d.ingredient_name IS NOT NULL AND d.ingredient_name != ''
              AND d.ingredient_rxnorm_code IS NOT NULL
        ),
        all_synonyms AS (
            SELECT source, code, synonym, sab, tty FROM cui_synonyms
            UNION ALL
            SELECT source, code, synonym, sab, tty FROM lnc_components
            UNION ALL
            SELECT source, code, synonym, sab, tty FROM combination_ingredients
        )
        SELECT source, code, synonym, sab, tty FROM all_synonyms
    """
    )


def _hierarchy_icd10cm_sql() -> str:
    return """
        WITH RECURSIVE target AS (
            SELECT code, aui FROM (""" + _codes_sql() + """) AS t
            WHERE source = 'ICD10CM'
        ),
        walk AS (
            SELECT code, aui, 0 AS depth FROM target
            UNION ALL
            SELECT w.code, parent.AUI, w.depth + 1
            FROM walk w
            JOIN mrrel r ON r.AUI1 = w.aui AND r.REL IN ('PAR','RB')
            JOIN mrconso parent ON parent.AUI = r.AUI2
                             AND parent.SAB = 'ICD10CM'
                             AND parent.SUPPRESS = 'N'
            WHERE w.depth < 2
        ),
        ranked AS (
            SELECT w.code, w.depth, m.CODE AS ancestor_code, m.STR,
                   ROW_NUMBER() OVER (PARTITION BY w.code, w.depth ORDER BY m.AUI) AS rn
            FROM walk w
            JOIN mrconso m ON m.AUI = w.aui AND m.SAB = 'ICD10CM'
            WHERE w.depth > 0
        )
        SELECT code, depth, ancestor_code, STR FROM ranked WHERE rn = 1
    """


def _hierarchy_icd10pcs_sql() -> str:
    return """
        WITH RECURSIVE target AS (
            SELECT code, aui FROM (""" + _codes_sql() + """) AS t
            WHERE source = 'ICD10PCS'
        ),
        walk AS (
            SELECT code, aui, 0 AS depth FROM target
            UNION ALL
            SELECT w.code, parent.AUI, w.depth + 1
            FROM walk w
            JOIN mrrel r ON r.AUI1 = w.aui AND r.REL IN ('PAR','RB')
            JOIN mrconso parent ON parent.AUI = r.AUI2
                             AND parent.SAB = 'ICD10PCS'
                             AND parent.SUPPRESS = 'N'
            WHERE w.depth < 2
        ),
        ranked AS (
            SELECT w.code, w.depth, m.CODE AS ancestor_code, m.STR,
                   ROW_NUMBER() OVER (PARTITION BY w.code, w.depth ORDER BY m.AUI) AS rn
            FROM walk w
            JOIN mrconso m ON m.AUI = w.aui AND m.SAB = 'ICD10PCS'
            WHERE w.depth > 0
        )
        SELECT code, depth, ancestor_code, STR FROM ranked WHERE rn = 1
    """


def _hierarchy_snomed_sql() -> str:
    return """
        WITH RECURSIVE target AS (
            SELECT code, aui FROM (""" + _codes_sql() + """) AS t
            WHERE source = 'SNOMEDCT_US'
        ),
        walk AS (
            SELECT code, aui, 0 AS depth FROM target
            UNION ALL
            SELECT w.code, parent.AUI, w.depth + 1
            FROM walk w
            JOIN mrrel r ON r.AUI1 = w.aui AND r.REL IN ('PAR','RB')
            JOIN mrconso parent ON parent.AUI = r.AUI2
                             AND parent.SAB = 'SNOMEDCT_US'
                             AND parent.SUPPRESS = 'N'
            WHERE w.depth < 2
        ),
        ranked AS (
            SELECT w.code, w.depth, m.STR,
                   ROW_NUMBER() OVER (PARTITION BY w.code, w.depth ORDER BY m.AUI) AS rn
            FROM walk w
            JOIN mrconso m ON m.AUI = w.aui AND m.SAB = 'SNOMEDCT_US'
            WHERE w.depth > 0
        )
        SELECT code, depth, STR FROM ranked WHERE rn = 1
    """


def _hierarchy_lnc_sql() -> str:
    """For LNC, use the LOINC CLASS field from mrsat ATN='LCL'."""
    return """
        WITH target AS (
            SELECT code FROM (""" + _codes_sql() + """) AS t
            WHERE source = 'LNC'
        )
        SELECT t.code, s.ATV AS class
        FROM target t
        LEFT JOIN mrsat s ON s.SAB = 'LNC' AND s.CODE = t.code AND s.ATN = 'LCL'
    """


def _lnc_parents_sql() -> str:
    """For each LNC code, walk PAR edges to find parent LNC codes and their
    preferred names. These are LOINC group/panel concepts that may match a
    query's broad bucket term (e.g., a parent panel like 'Acylcarnitine'
    when the input is 'Octanoylcarnitine')."""
    return """
        WITH RECURSIVE target AS (
            SELECT code, aui FROM (""" + _codes_sql() + """) AS t
            WHERE source = 'LNC'
        ),
        walk AS (
            SELECT code, aui, 0 AS depth FROM target
            UNION ALL
            SELECT w.code, parent.AUI, w.depth + 1
            FROM walk w
            JOIN mrrel r ON r.AUI1 = w.aui AND r.REL = 'PAR'
            JOIN mrconso parent ON parent.AUI = r.AUI2
                             AND parent.SAB = 'LNC'
                             AND parent.SUPPRESS = 'N'
            WHERE w.depth < 2
        ),
        ranked AS (
            SELECT w.code, w.depth, m.CODE AS parent_code, m.STR,
                   ROW_NUMBER() OVER (PARTITION BY w.code, w.depth ORDER BY m.AUI) AS rn
            FROM walk w
            JOIN mrconso m ON m.AUI = w.aui AND m.SAB = 'LNC' AND m.TTY = 'LC'
            WHERE w.depth > 0
        )
        SELECT code, depth, parent_code, STR FROM ranked WHERE rn = 1
    """


# LOINC CLASS abbreviation -> human-readable name. Used to surface readable
# class names as hierarchy synonyms for LNC records. Built from the
# top-30 CLASS values in the current build plus a curated handful of
# less-common but clinically meaningful ones.
_LNC_CLASS_READABLE: dict[str, str] = {
    "MICRO": "Microbiology",
    "CHEM": "Chemistry",
    "DRUG/TOX": "Drug and Toxicology",
    "RAD": "Radiology",
    "ALLERGY": "Allergy",
    "CHAL": "Challenge Tests",
    "DOC.ONTOLOGY": "Document Ontology",
    "PHENX": "PHENX Surveys",
    "SERO": "Serology",
    "LABORDERS.ONTOLOGY": "Lab Order Ontology",
    "SURVEY.PROMIS": "PROMIS Survey",
    "HEM/BC": "Hematology",
    "ABXBACT": "Antibacterial Susceptibility",
    "CELLMARK": "Cell Marker",
    "SURVEY.GNHLTH": "General Health Survey",
    "MOLPATH.MUT": "Molecular Pathology Mutations",
    "BLDBK": "Blood Bank",
    "COAG": "Coagulation",
    "SURVEY.CMS": "CMS Survey",
    "H&P.HX": "History and Physical",
    "PULM": "Pulmonary",
    "PANEL.SURVEY.CMS": "CMS Survey Panel",
    "SURVEY.MDS": "MDS Survey",
    "CARD.US": "Cardiac Ultrasound",
    "OB.US": "Obstetric Ultrasound",
    "PATH": "Pathology",
    "PANEL.PHENX": "PHENX Panel",
    "MOLPATH": "Molecular Pathology",
    "UA": "Urinalysis",
    "HLA": "HLA Typing",
    "BD": "Blood Gas",
    "BP": "Blood Pressure",
    "BDYWGT.ATOM": "Body Weight",
    "BDYTMP.ATOM": "Body Temperature",
    "HRTRATE.ATOM": "Heart Rate",
    "RESP.ATOM": "Respiratory Rate",
    "BDYOBS.ATOM": "Body Observation",
    "PAIN.ATOM": "Pain",
    "VISION.ATOM": "Vision",
}


def _lnc_class_readable(cls: str | None) -> str | None:
    """Return a human-readable LOINC CLASS name, or the original CLASS if
    no mapping is known."""
    if not cls:
        return None
    return _LNC_CLASS_READABLE.get(cls, cls)


# Section header prefixes that appear as the first axis of ICD10PCS long
# names. These are surfaced as clean synonyms (they ARE the patient-friendly
# bucket names: "Imaging", "Radiation Therapy", "Medical and Surgical").
_ICD10PCS_ROOT_SECTIONS = (
    "Medical and Surgical",
    "Medical",
    "Surgical",
    "Imaging",
    "Mental Health",
    "Radiation Therapy",
    "Nuclear Medicine",
    "Physical Rehabilitation and Diagnostic Audiology",
    "Chiropractic",
    "Administration of Medicine",
    "Measurement",
    "Extracorporeal Assistance and Performance",
    "Osteopathic",
    "Other Procedures",
)


def _clean_icd10pcs_template(text: str | None) -> tuple[list[str], str | None]:
    """Flatten an ICD10PCS `@`-delimited template into clean segments.

    Returns (segments, root_section). `segments` is the list of meaningful
    nodes (drops empty/None placeholders). `root_section` is the first
    segment if it matches a known ICD10PCS root section name (e.g.,
    "Imaging", "Radiation Therapy"), else None.

    Example:
      'Imaging @ Veins @ Computerized Tomography (CT Scan) @ Superior Vena Cava @ High Osmolar @ None @ None'
      -> (['Imaging', 'Veins', 'Computerized Tomography (CT Scan)',
           'Superior Vena Cava', 'High Osmolar'],
          'Imaging')
    """
    if not text or "@" not in text:
        return [], None
    raw_segments = [s.strip() for s in text.split("@")]
    segments = [s for s in raw_segments if s and s != "None"]
    root = segments[0] if segments and segments[0] in _ICD10PCS_ROOT_SECTIONS else None
    return segments, root


def _hierarchy_rxnorm_sql(decomposition_csv: str) -> str:
    """For RXNORM, look up ATC two ways:
    - IN-level (and any code sharing a CUI with an ATC atom): direct via mrconso.
    - SCD/SBD/SCDG/MIN: via Table 2 (rxnorm_ingredient_decomposition.csv),
      which already has the product -> ingredient traversal and ATC levels.
    """
    return f"""
        WITH target AS (
            SELECT code, cui, source_tty FROM (_TARGET_PLACEHOLDER_) AS t
            WHERE source = 'RXNORM'
        ),
        -- Direct ATC via shared CUI (mostly IN-level).
        atc_direct AS (
            SELECT DISTINCT
                t.code,
                atc.CODE AS atc_code,
                atc.STR AS atc_name
            FROM target t
            JOIN mrconso atc ON atc.CUI = t.cui
                           AND atc.SAB = 'ATC'
                           AND atc.SUPPRESS = 'N'
                           AND length(atc.CODE) = 7
        ),
        -- ATC via Table 2 decomposition (product -> ingredient -> ATC).
        atc_via_decomp AS (
            SELECT DISTINCT
                t.code,
                d.atc_code,
                d.atc_level5
            FROM target t
            JOIN read_csv_auto('{decomposition_csv}', HEADER=true) d
              ON d.rxnorm_code = t.code
            WHERE d.atc_code IS NOT NULL AND d.atc_code != ''
              AND t.source_tty IN ('SCD','SBD','SCDG','MIN')
        ),
        all_atc AS (
            SELECT code, atc_code, atc_name FROM atc_direct
            UNION
            SELECT code, atc_level5 AS atc_code, CAST(NULL AS VARCHAR) AS atc_name
            FROM atc_via_decomp
        ),
        with_names AS (
            SELECT
                a.code,
                a.atc_code,
                COALESCE(a.atc_name, m.STR) AS atc_name
            FROM all_atc a
            LEFT JOIN mrconso m ON m.SAB = 'ATC'
                              AND m.CODE = a.atc_code
                              AND m.SUPPRESS = 'N'
                              AND length(m.CODE) = 7
        )
        SELECT
            code,
            atc_code,
            substr(atc_code, 1, 1) AS atc_level1,
            substr(atc_code, 1, 3) AS atc_level2,
            substr(atc_code, 1, 4) AS atc_level3,
            substr(atc_code, 1, 5) AS atc_level4,
            atc_code AS atc_level5,
            atc_name,
            ROW_NUMBER() OVER (PARTITION BY code ORDER BY atc_code) AS rn
        FROM with_names
    """.replace("_TARGET_PLACEHOLDER_", _codes_sql())


def _cvx_hierarchy_sql() -> str:
    """CVX hierarchy would require the external VG group file; skip for v1
    (returns nothing — handled in Python as empty hierarchy)."""
    return "SELECT NULL WHERE FALSE"


def _semantic_types_sql() -> str:
    return """
        WITH target AS (
            SELECT source, code, cui FROM (""" + _codes_sql() + """) AS t
            WHERE cui IS NOT NULL AND cui != ''
        )
        SELECT t.source, t.code, string_agg(DISTINCT m.tui, ',' ORDER BY m.tui) AS tuis
        FROM target t
        JOIN mrsty m ON m.cui = t.cui
        GROUP BY t.source, t.code
    """


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 2
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Reading codes from {input_path}")
    print(f"Writing embedding index to {output_path}")
    print(f"Database: {db_path}")
    print()

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        t0 = time.perf_counter()
        print("[1/5] Loading addressable codes with per-source filters...")
        # Pass input path twice — each subquery that calls _codes_sql() opens
        # its own read_csv_auto, so the parameter is replicated.
        target_rows = con.execute(_codes_sql(), [str(input_path)]).fetchall()
        print(f"  {len(target_rows):,} addressable codes")

        # Index by (source, code) for fast lookup.
        target_by_key: dict[tuple[str, str], dict] = {}
        for r in target_rows:
            (source, code, name, friendly_source, technical_name, source_tty,
             cui, aui, category) = r
            target_by_key[(source, code)] = {
                "source": source, "code": code, "name": name,
                "friendly_source": friendly_source,
                "technical_name": technical_name,
                "source_tty": source_tty, "cui": cui, "aui": aui,
                "category": category,
            }

        from collections import Counter
        by_source = Counter(r[0] for r in target_rows)
        by_category = Counter(r[8] for r in target_rows if r[8])
        print(f"  By source: {dict(sorted(by_source.items()))}")
        print(f"  By category: {dict(sorted(by_category.items()))}")
        n_no_category = sum(1 for r in target_rows if r[8] is None)
        print(f"  Codes with no category (excluded from output): {n_no_category:,}")

        print("[2/5] Loading English synonyms per code (top-K=8 by source priority)...")
        syn_rows = con.execute(_synonyms_sql(), [str(input_path)]).fetchall()
        syn_by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
        syn_seen: dict[tuple[str, str], set[str]] = defaultdict(set)
        syn_rows.sort(
            key=lambda r: (
                r[0], r[1],
                _SYNONYM_SOURCE_PRIORITY.get(r[3] or "", 99),
                r[4] or "",
                r[2],
            )
        )
        for source, code, synonym, _sab, _tty in syn_rows:
            key = (source, code)
            tech = target_by_key.get(key, {}).get("technical_name")
            if tech and synonym.lower() == tech.lower():
                continue
            norm = synonym.lower().strip()
            if not norm or norm in syn_seen[key]:
                continue
            syn_seen[key].add(norm)
            if len(syn_by_key[key]) < _SYNONYM_K:
                syn_by_key[key].append(synonym)
        print(f"  {len(syn_by_key):,} codes with at least one synonym")

        print("[3/5] Loading source-specific hierarchies...")
        hier_icd = defaultdict(list)
        for code, depth, ancestor_code, name in con.execute(
            _hierarchy_icd10cm_sql(), [str(input_path)]
        ).fetchall():
            hier_icd[code].append({"depth": depth, "code": ancestor_code, "name": name})
        print(f"  ICD10CM: {len(hier_icd):,} codes with hierarchy")

        hier_icdpcs = defaultdict(list)
        for code, depth, ancestor_code, name in con.execute(
            _hierarchy_icd10pcs_sql(), [str(input_path)]
        ).fetchall():
            hier_icdpcs[code].append({"depth": depth, "code": ancestor_code, "name": name})
        print(f"  ICD10PCS: {len(hier_icdpcs):,} codes with hierarchy")

        hier_snomed = defaultdict(list)
        for code, depth, name in con.execute(
            _hierarchy_snomed_sql(), [str(input_path)]
        ).fetchall():
            hier_snomed[code].append({"depth": depth, "name": name})
        print(f"  SNOMEDCT_US: {len(hier_snomed):,} codes with hierarchy")

        hier_lnc = {}
        for code, cls in con.execute(_hierarchy_lnc_sql(), [str(input_path)]).fetchall():
            if cls:
                hier_lnc[code] = cls
        print(f"  LNC: {len(hier_lnc):,} codes with CLASS")
        # Note: LOINC group/panel concepts (e.g., "Acylcarnitines" as a parent
        # of specific acylcarnitine tests) are NOT available in UMLS mrrel.
        # Walking PAR from a LNC code yields Metathesaurus part concepts
        # (MTHU atoms like "Chemistry"), which duplicate the CLASS info we
        # already surface. Implementing spec change #3 fully would require
        # loading the LOINC source files (Group.csv / MultiAxialGroup.csv)
        # into the DuckDB.

        # RXNORM ATC: pick one row per code (first by ATC code).
        atc_by_code: dict[str, dict[str, str]] = {}
        decomposition_csv = str(input_path.parent / "rxnorm_ingredient_decomposition.csv")
        atc_rows = con.execute(
            _hierarchy_rxnorm_sql(decomposition_csv), [str(input_path)]
        ).fetchall()
        for (code, atc_code, l1, l2, l3, l4, l5, atc_name, rn) in atc_rows:
            if rn == 1 and code not in atc_by_code:
                atc_by_code[code] = {
                    "atc_code": atc_code,
                    "atc_level1": l1, "atc_level2": l2, "atc_level3": l3,
                    "atc_level4": l4, "atc_level5": l5,
                    "atc_name": atc_name,
                }
        print(f"  RXNORM ATC: {len(atc_by_code):,} codes with ATC")

        print("[4/5] Loading semantic types via mrsty...")
        sem_by_key: dict[tuple[str, str], list[str]] = {}
        for source, code, tuis in con.execute(
            _semantic_types_sql(), [str(input_path)]
        ).fetchall():
            sem_by_key[(source, code)] = [t for t in tuis.split(",") if t]
        print(f"  {len(sem_by_key):,} codes with semantic types")

        print("[5/5] Building hierarchy vectors and writing JSONL...")
        def build_hierarchy(t: dict) -> list[str]:
            source = t["source"]
            code = t["code"]
            if source == "ICD10CM":
                levels = sorted(hier_icd.get(code, []), key=lambda x: x["depth"])
                return [f"{lv['name']} ({lv['code']})" for lv in reversed(levels)]
            if source == "ICD10PCS":
                # ICD10PCS ancestors carry the @ template format. Clean each
                # segment, drop None placeholders. If a hierarchy entry
                # doesn't have @, keep it as-is.
                levels = sorted(hier_icdpcs.get(code, []), key=lambda x: x["depth"])
                out: list[str] = []
                for lv in reversed(levels):
                    name = lv["name"]
                    if "@" in name:
                        segments, _root = _clean_icd10pcs_template(name)
                        if segments:
                            # Drop the trailing "(CODE)" since the segments
                            # are the meaningful part. The ICD10PCS code is
                            # already in the index — no need to repeat.
                            out.append(" - ".join(segments))
                    else:
                        out.append(f"{name} ({lv['code']})")
                return out
            if source == "SNOMEDCT_US":
                levels = sorted(hier_snomed.get(code, []), key=lambda x: x["depth"])
                return [lv["name"] for lv in reversed(levels)]
            if source == "LNC":
                # LNC hierarchy: just the readable CLASS name. Parent panels/
                # groups are not in UMLS mrrel (would need LOINC source files).
                cls = hier_lnc.get(code)
                if not cls:
                    return []
                readable = _lnc_class_readable(cls)
                return [f"{readable} ({cls})" if readable != cls else cls]
            if source == "RXNORM":
                atc = atc_by_code.get(code)
                if not atc:
                    return []
                name = atc.get("atc_name") or ""
                return [
                    f"{atc['atc_level2']} ({atc['atc_level4']} parent)",
                    f"{atc['atc_level4']} ({atc['atc_level5']} parent)",
                    f"{atc['atc_level5']} — {name}",
                ]
            return []

        n_written = 0
        n_skipped = 0
        with output_path.open("w", encoding="utf-8") as f:
            for key, t in target_by_key.items():
                if not t["category"]:
                    n_skipped += 1
                    continue
                source = t["source"]
                code = t["code"]
                tech = t["technical_name"] or t["name"]
                syns = syn_by_key.get(key, [])
                friendly = t["name"]
                hierarchy = build_hierarchy(t)
                atc = atc_by_code.get(code) if source == "RXNORM" else None
                sem_types = sem_by_key.get(key, [])

                # Spec follow-up: surface readable LOINC CLASS name and
                # ICD10PCS root section as priority synonyms (prepended,
                # before the standard CUI-shared set). These are the
                # patient-friendly "bucket" names that BM25 struggles to
                # extract from full noisy strings.
                priority_synonyms: list[str] = []
                if source == "LNC":
                    cls = hier_lnc.get(code)
                    if cls:
                        readable = _lnc_class_readable(cls)
                        if readable and readable != cls:
                            priority_synonyms.append(readable)
                if source == "ICD10PCS" and hierarchy:
                    # The first hierarchy entry after build_hierarchy is the
                    # broadest ancestor. Extract the root section from it.
                    first_hier = hierarchy[0]
                    # build_hierarchy joins @ segments with " - ", so the
                    # root section is the first segment.
                    root_segment = first_hier.split(" - ")[0].strip()
                    if root_segment in _ICD10PCS_ROOT_SECTIONS:
                        priority_synonyms.append(root_segment)

                # Clean @-template synonyms from ICD10PCS HX atoms. The HX
                # atom shares a CUI with the PT atom and gets pulled into
                # the synonym list, but its 'A @ B @ None @ C' format is
                # noise for BM25. Replace each @ synonym with its cleaned
                # segment-joined version (dedupe against the technical
                # name and other synonyms to avoid repetition).
                if source == "ICD10PCS":
                    cleaned_syns: list[str] = []
                    for s in syns:
                        if "@" in s:
                            segments, _root = _clean_icd10pcs_template(s)
                            if segments:
                                cleaned = " ".join(segments)
                                if (cleaned.lower() not in
                                        {c.lower() for c in cleaned_syns}
                                    and cleaned.lower() != (tech or "").lower()):
                                    cleaned_syns.append(cleaned)
                        else:
                            cleaned_syns.append(s)
                    syns = cleaned_syns

                # Dedupe priority synonyms against the existing set, then
                # prepend (so they appear before the standard CUI-shared
                # synonyms in the K=8 list).
                existing_lower = {s.lower() for s in syns}
                existing_lower.add(tech.lower() if tech else "")
                extra = [s for s in priority_synonyms if s.lower() not in existing_lower]
                syns = extra + syns
                # Re-apply the K=8 cap.
                if len(syns) > _SYNONYM_K:
                    syns = syns[:_SYNONYM_K]

                # For LOINC, also surface the COMPONENT as a top-level field.
                # Parse it from technical_name (the LN long name) — the
                # first segment before the first ':'.
                component = None
                if source == "LNC" and tech and ":" in tech:
                    component = tech.split(":")[0].strip() or None

                record = {
                    "category": t["category"],
                    "tty": t["source_tty"],
                    "friendly_name": friendly,
                    "code": {
                        "source": source,
                        "code": code,
                        "tty": t["source_tty"],
                        "cui": t["cui"],
                        "name": tech,
                    },
                    "rule": "addressable",
                    "semantic_types": sem_types,
                    "atc": atc,
                    "component": component,
                    "vectors": {
                        "technical": tech,
                        "synonyms": syns,
                        "friendly": friendly,
                        "hierarchy": hierarchy,
                    },
                }
                f.write(json.dumps(record, ensure_ascii=False))
                f.write("\n")
                n_written += 1

        elapsed = time.perf_counter() - t0
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print()
        print(f"Wrote {n_written:,} records to {output_path} ({size_mb:.1f} MB) in {elapsed:.1f}s")
        if n_skipped:
            print(f"Skipped {n_skipped:,} codes with no category (TUI didn't match any)")

        # Split the index by category for the fhir4px app. The app always
        # knows the category from the FHIR resourceType (MedicationRequest ->
        # medication, Observation -> lab, etc.), so per-category files let it
        # load only what it needs and tune BM25 per category.
        print()
        print("Splitting index by category...")
        category_files: dict[str, object] = {
            cat: (output_path.parent / f"embedding_index_{cat}.jsonl").open(
                "w", encoding="utf-8"
            )
            for cat in ("condition", "lab", "medication", "procedure", "vaccine", "body_structure")
        }
        try:
            counts: dict[str, int] = dict.fromkeys(category_files, 0)
            with output_path.open("r", encoding="utf-8") as src:
                for line in src:
                    r = json.loads(line)
                    cat = r.get("category")
                    if cat in category_files:
                        category_files[cat].write(line)
                        counts[cat] += 1
            for cat, fh in category_files.items():
                fh.close()
                p = output_path.parent / f"embedding_index_{cat}.jsonl"
                size_mb = p.stat().st_size / (1024 * 1024)
                print(f"  embedding_index_{cat}.jsonl: {counts[cat]:,} records ({size_mb:.1f} MB)")
        finally:
            for fh in category_files.values():
                if not fh.closed:
                    fh.close()
    finally:
        con.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
