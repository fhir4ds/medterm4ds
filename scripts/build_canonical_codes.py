#!/usr/bin/env python3
"""Enrich patient_friendly_names.csv and build canonical_codes.csv.

Two outputs:
  1. Overwrites reports/fhir4px/patient_friendly_names.csv with CUI/AUI/source_tty
     populated by JOINing against mrconso on (source, code).
  2. New reports/fhir4px/canonical_codes.csv with one row per
     (category, friendly_name), picking a canonical code per the rules
     documented in the module docstring of build_canonical_table().

Usage:
  python3 scripts/build_canonical_codes.py
  python3 scripts/build_canonical_codes.py --input reports/fhir4px/patient_friendly_names.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_INPUT = Path("reports/fhir4px/patient_friendly_names.csv")
DEFAULT_OUTPUT_DIR = Path("reports/fhir4px")


# Step 1 enrichment: JOIN each (source, code) in patient_friendly_names.csv
# against mrconso to recover CUI, AUI, and a preferred TTY.
#
# For codes with multiple atoms, pick the preferred one by:
#   1. Non-suppressed (SUPPRESS='N')
#   2. Preferred-term TTY per source (PT for most; MH for MSH; LN for LNC; IN for RXNORM)
#   3. Shortest STR as final tiebreaker
ENRICH_SQL = """
WITH pf AS (
    SELECT
        CAST(source AS VARCHAR) AS source,
        CAST(code AS VARCHAR) AS code,
        CAST(name AS VARCHAR) AS name,
        CAST(friendly_source AS VARCHAR) AS friendly_source,
        CAST(match_type AS VARCHAR) AS match_type,
        CAST(match_depth AS VARCHAR) AS match_depth,
        CAST(technical_name AS VARCHAR) AS technical_name
    FROM read_csv_auto(?, HEADER=true)
),
ranked_atoms AS (
    SELECT
        SAB AS source,
        CODE AS code,
        CUI,
        AUI,
        TTY,
        STR AS atom_name,
        ROW_NUMBER() OVER (
            PARTITION BY SAB, CODE
            ORDER BY
                CASE WHEN SUPPRESS = 'N' THEN 0 ELSE 1 END,
                CASE
                    -- Per-source preferred-term conventions.
                    WHEN SAB = 'ICD10CM' AND TTY = 'HT' THEN 0
                    WHEN SAB = 'ICD10PCS' AND TTY = 'PT' THEN 0
                    WHEN SAB = 'SNOMEDCT_US' AND TTY = 'PT' THEN 0
                    WHEN SAB = 'CVX' AND TTY = 'PT' THEN 0
                    WHEN SAB = 'HCPCS' AND TTY = 'PT' THEN 0
                    WHEN SAB = 'CPT' AND TTY = 'PT' THEN 0
                    WHEN SAB = 'LNC' AND TTY IN ('LN', 'LPN', 'LA') THEN 0
                    WHEN SAB = 'RXNORM' AND TTY IN ('IN', 'MIN', 'SCDG', 'SCD') THEN 0
                    WHEN SAB = 'MSH' AND TTY = 'MH' THEN 0
                    ELSE 1
                END,
                AUI
        ) AS rn
    FROM mrconso
    WHERE CODE IS NOT NULL AND CODE != ''
),
pf_cui AS (
    SELECT pf.*, a.CUI
    FROM pf
    LEFT JOIN ranked_atoms a ON a.source = pf.source AND a.code = pf.code AND a.rn = 1
),
semantic_types AS (
    -- Comma-separated list of TUIs per CUI (only when mrsty table exists).
    SELECT
        cui,
        string_agg(DISTINCT tui, ',' ORDER BY tui) AS semantic_types
    FROM mrsty
    GROUP BY cui
)
SELECT
    pf.source,
    pf.code,
    pf.name,
    pf.friendly_source,
    pf.match_type,
    CAST(pf.match_depth AS INTEGER) AS match_depth,
    pf.technical_name,
    a.TTY AS source_tty,
    pf.CUI AS cui,
    a.AUI AS aui,
    st.semantic_types
FROM pf_cui pf
LEFT JOIN ranked_atoms a ON a.source = pf.source AND a.code = pf.code AND a.rn = 1
LEFT JOIN semantic_types st ON st.cui = pf.CUI
ORDER BY pf.source, pf.code
"""

ENRICH_COLUMNS = [
    "source", "code", "name", "friendly_source",
    "match_type", "match_depth", "technical_name",
    "source_tty", "cui", "aui", "semantic_types",
]


def enrich_patient_friendly(con, input_path: Path, output_path: Path) -> int:
    """JOIN patient_friendly_names.csv against mrconso and overwrite."""
    print(f"  Reading {input_path}, JOINing mrconso, writing {output_path}")
    con.execute(
        f"""
        COPY (
            {ENRICH_SQL}
        ) TO '{output_path.as_posix()}'
        (HEADER, DELIMITER ',', QUOTE '"', FORMAT CSV)
        """,
        [str(input_path)],
    )
    count = con.execute(
        f"SELECT COUNT(*) FROM read_csv_auto('{output_path.as_posix()}')"
    ).fetchone()[0]
    return count


# Step 2: build canonical_codes.csv.
#
# Target systems: ICD10CM (condition), LNC (lab), RXNORM (medication), CVX (vaccine).
# SNOMEDCT_US is included as a fallback per category, gated by MRSTY semantic
# types — a SNOMED concept is only eligible as a condition canonical if its
# TUI is disease/finding-like, only eligible as a medication canonical if its
# TUI is pharmacologic-substance/clinical-drug, etc. This avoids the prior
# problem of substances (Phenylephrine) appearing as conditions via SNOMED
# product concepts.
#
# Rules per category:
#   condition (ICD10CM preferred; SNOMED fallback if TUI is disease/finding-like)
#     Pick the shortest code from the preferred source. SNOMED fallback only
#     considered when no ICD10CM candidate exists.
#   lab (LNC preferred; SNOMED fallback if TUI is lab-procedure/result)
#     Same pattern.
#   medication (RXNORM preferred; SNOMED fallback if TUI is pharmacologic)
#     Prefer TTY=IN, then MIN, then SCDG, then other. Among same TTY, shortest code.
#   vaccine (CVX preferred; SNOMED fallback if crosswalk exists)
#     SNOMED concepts with a shared-CUI CVX atom count as vaccine candidates.
CANONICAL_SQL = """
WITH pf AS (
    SELECT
        CAST(source AS VARCHAR) AS source,
        CAST(code AS VARCHAR) AS code,
        CAST(name AS VARCHAR) AS name,
        CAST(source_tty AS VARCHAR) AS source_tty,
        CAST(cui AS VARCHAR) AS cui
    FROM read_csv_auto(?, HEADER=true)
),
-- SNOMED TUIs per code, looked up via mrsty through the code's CUI.
snomed_tuis AS (
    SELECT DISTINCT pf.source, pf.code, pf.name, m.tui
    FROM pf
    JOIN mrconso c ON c.SAB = 'SNOMEDCT_US' AND c.CODE = pf.code AND c.SUPPRESS = 'N'
    JOIN mrsty m ON m.cui = c.CUI
    WHERE pf.source = 'SNOMEDCT_US'
),
-- SNOMED codes with a shared-CUI CVX atom (vaccine candidates).
snomed_cvx AS (
    SELECT DISTINCT pf.source, pf.code, pf.name
    FROM pf
    JOIN mrconso c ON c.SAB = 'SNOMEDCT_US' AND c.CODE = pf.code AND c.SUPPRESS = 'N'
    JOIN mrconso cvx ON cvx.CUI = c.CUI AND cvx.SAB = 'CVX' AND cvx.SUPPRESS = 'N'
    WHERE pf.source = 'SNOMEDCT_US'
),
categorized AS (
    SELECT
        pf.*,
        CASE
            WHEN pf.source = 'ICD10CM' THEN 'condition'
            WHEN pf.source = 'LNC' THEN 'lab'
            WHEN pf.source = 'RXNORM' THEN 'medication'
            WHEN pf.source = 'CVX' THEN 'vaccine'
            WHEN pf.source = 'SNOMEDCT_US' AND EXISTS (
                SELECT 1 FROM snomed_tuis st
                WHERE st.source = pf.source AND st.code = pf.code
                  AND st.tui IN ('T019','T020','T037','T046','T047','T048','T049','T190','T191')
            ) THEN 'condition'
            WHEN pf.source = 'SNOMEDCT_US' AND EXISTS (
                SELECT 1 FROM snomed_tuis st
                WHERE st.source = pf.source AND st.code = pf.code
                  AND st.tui IN ('T034','T059')
            ) THEN 'lab'
            WHEN pf.source = 'SNOMEDCT_US' AND EXISTS (
                SELECT 1 FROM snomed_tuis st
                WHERE st.source = pf.source AND st.code = pf.code
                  AND st.tui IN ('T121','T123','T200')
            ) THEN 'medication'
            WHEN pf.source = 'SNOMEDCT_US' AND EXISTS (
                SELECT 1 FROM snomed_cvx sc
                WHERE sc.source = pf.source AND sc.code = pf.code
            ) THEN 'vaccine'
        END AS category
    FROM pf
    WHERE pf.source IN ('ICD10CM', 'LNC', 'RXNORM', 'CVX', 'SNOMEDCT_US')
),
candidates AS (
    SELECT
        category,
        name,
        COUNT(*) OVER (PARTITION BY category, name) AS candidate_count,
        source,
        code,
        source_tty,
        cui
    FROM categorized
    WHERE category IS NOT NULL
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY category, name
            ORDER BY
                -- Prefer primary target source over SNOMED fallback.
                CASE
                    WHEN category = 'condition' AND source = 'ICD10CM' THEN 0
                    WHEN category = 'condition' AND source = 'SNOMEDCT_US' THEN 1
                    WHEN category = 'lab' AND source = 'LNC' THEN 0
                    WHEN category = 'lab' AND source = 'SNOMEDCT_US' THEN 1
                    WHEN category = 'medication' AND source = 'RXNORM' THEN 0
                    WHEN category = 'medication' AND source = 'SNOMEDCT_US' THEN 1
                    WHEN category = 'vaccine' AND source = 'CVX' THEN 0
                    WHEN category = 'vaccine' AND source = 'SNOMEDCT_US' THEN 1
                    ELSE 0
                END,
                -- medication: prefer TTY=IN, then MIN, then SCDG, then anything else
                CASE
                    WHEN category = 'medication' AND source_tty = 'IN' THEN 0
                    WHEN category = 'medication' AND source_tty = 'MIN' THEN 1
                    WHEN category = 'medication' AND source_tty = 'SCDG' THEN 2
                    WHEN category = 'medication' THEN 3
                    ELSE 0
                END,
                -- tiebreak: shorter code = more general / canonical
                length(code),
                code
        ) AS canonical_rn
    FROM candidates
),
winners AS (
    SELECT * FROM ranked WHERE canonical_rn = 1
)
SELECT
    w.category,
    w.name AS friendly_name,
    w.source AS canonical_source,
    w.code AS canonical_code,
    w.source_tty AS canonical_tty,
    w.cui AS canonical_cui,
    m.STR AS canonical_name,
    CASE
        WHEN w.category = 'condition' AND w.source = 'ICD10CM' THEN 'icd10cm_shortest'
        WHEN w.category = 'condition' AND w.source = 'SNOMEDCT_US' THEN 'snomedct_condition_fallback'
        WHEN w.category = 'lab' AND w.source = 'LNC' THEN 'lnc_shortest'
        WHEN w.category = 'lab' AND w.source = 'SNOMEDCT_US' THEN 'snomedct_lab_fallback'
        WHEN w.category = 'medication' AND w.source = 'RXNORM' AND w.source_tty = 'IN' THEN 'rxnorm_in'
        WHEN w.category = 'medication' AND w.source = 'RXNORM' AND w.source_tty = 'MIN' THEN 'rxnorm_min'
        WHEN w.category = 'medication' AND w.source = 'RXNORM' AND w.source_tty = 'SCDG' THEN 'rxnorm_scdg'
        WHEN w.category = 'medication' AND w.source = 'RXNORM' THEN 'rxnorm_other'
        WHEN w.category = 'medication' AND w.source = 'SNOMEDCT_US' THEN 'snomedct_medication_fallback'
        WHEN w.category = 'vaccine' AND w.source = 'CVX' THEN 'cvx_shortest'
        WHEN w.category = 'vaccine' AND w.source = 'SNOMEDCT_US' THEN 'snomedct_vaccine_fallback'
    END AS rule,
    w.candidate_count
FROM winners w
LEFT JOIN (
    SELECT SAB, CODE, STR,
           ROW_NUMBER() OVER (
               PARTITION BY SAB, CODE
               ORDER BY
                   CASE WHEN SUPPRESS = 'N' THEN 0 ELSE 1 END,
                   CASE
                       WHEN SAB = 'ICD10CM' AND TTY = 'HT' THEN 0
                       WHEN SAB = 'ICD10PCS' AND TTY = 'PT' THEN 0
                       WHEN SAB = 'SNOMEDCT_US' AND TTY = 'PT' THEN 0
                       WHEN SAB = 'CVX' AND TTY = 'PT' THEN 0
                       WHEN SAB = 'HCPCS' AND TTY = 'PT' THEN 0
                       WHEN SAB = 'CPT' AND TTY = 'PT' THEN 0
                       WHEN SAB = 'LNC' AND TTY IN ('LN', 'LPN', 'LA') THEN 0
                       WHEN SAB = 'RXNORM' AND TTY IN ('IN', 'MIN', 'SCDG', 'SCD') THEN 0
                       WHEN SAB = 'MSH' AND TTY = 'MH' THEN 0
                       ELSE 1
                   END,
                   AUI
           ) AS rn
    FROM mrconso
) m ON m.SAB = w.source AND m.CODE = w.code AND m.rn = 1
ORDER BY w.category, w.name
"""

CANONICAL_COLUMNS = [
    "category", "friendly_name",
    "canonical_source", "canonical_code", "canonical_tty",
    "canonical_cui", "canonical_name",
    "rule", "candidate_count",
]


def build_canonical_table(con, input_path: Path, output_path: Path) -> int:
    """Apply canonical rules per (category, friendly_name) and write CSV."""
    print(f"  Reading {input_path}, building canonical table, writing {output_path}")
    con.execute(
        f"""
        COPY (
            {CANONICAL_SQL}
        ) TO '{output_path.as_posix()}'
        (HEADER, DELIMITER ',', QUOTE '"', FORMAT CSV)
        """,
        [str(input_path)],
    )
    count = con.execute(
        f"SELECT COUNT(*) FROM read_csv_auto('{output_path.as_posix()}')"
    ).fetchone()[0]
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--skip-enrich",
        action="store_true",
        help="Skip Table 1 enrichment; only build canonical_codes.csv from existing input.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 2

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        if not args.skip_enrich:
            print("[Step 1] Enrich patient_friendly_names.csv with CUI/AUI/source_tty")
            n = enrich_patient_friendly(con, input_path, input_path)
            print(f"  Wrote {n:,} enriched rows back to {input_path}")
            print()

        print("[Step 2] Build canonical_codes.csv")
        canonical_path = output_dir / "canonical_codes.csv"
        n = build_canonical_table(con, input_path, canonical_path)
        print(f"  Wrote {n:,} canonical rows to {canonical_path}")
    finally:
        con.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
