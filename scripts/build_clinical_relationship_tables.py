#!/usr/bin/env python3
"""Build the 3 CSV tables requested by fhir4px.

Outputs (under --output-dir, default reports/fhir4px/):
  Table 1: patient_friendly_names.csv
  Table 2: rxnorm_ingredient_decomposition.csv
  Table 3: condition_medication_ingredient.csv

Tables 2 and 3 issue direct DuckDB SQL against the local UMLS database.
Table 1 delegates to scripts/run_patient_friendly_review.py.

Usage:
  python3 scripts/build_clinical_relationship_tables.py --tables 1 2 3

Run times (rough): Table 1 ~3-5 hours, Table 2 ~minutes, Table 3 ~1-3 hours.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

import duckdb

from medterm4ds.core.config import local_duckdb_config

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_OUTPUT_DIR = Path("reports/fhir4px")


def _write_csv(path: Path, header: list[str], rows) -> None:
    """Write rows to a UTF-8 CSV. Uses stdout-friendly progress reporting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        writer.writerows(rows)


def build_patient_friendly_names(args: argparse.Namespace) -> None:
    """Table 1: invoke run_patient_friendly_review.py for full bulk extraction."""
    output_csv = args.output_dir / "patient_friendly_names.csv"
    output_json = args.output_dir / "patient_friendly_names_timing.json"
    cmd = [
        sys.executable,
        str(Path(__file__).parent / "run_patient_friendly_review.py"),
        "--db",
        args.db,
        "--per-source",
        "0",
        "--max-depth",
        str(args.max_depth),
        "--memory-profile",
        args.memory_profile,
        "--output-csv",
        str(output_csv),
        "--output-json",
        str(output_json),
        "--progress",
    ]
    print(f"  Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


# Table 2 SQL: RxNorm product -> ingredient decomposition with ATC levels 1-5.
#
# Data convention in this DuckDB (verified):
#   - has_ingredient: AUI1=IN, AUI2=product-type (SCDC/SCDG/SCDF)
#   - has_tradename: AUI1=SBD, AUI2=SCD (so SBD looks up SCD's ingredients)
#   - consists_of: AUI1=SCDC, AUI2=SCD (so SCD looks up SCDC, then IN)
#   - has_part: AUI1=IN, AUI2=MIN (so MIN looks up its IN parts)
#   - IN maps to ATC via shared CUI; ATC codes have lengths 1, 3, 4, 5, 7.
#
# Outputs one row per (rxnorm_code, ingredient_rxnorm_code, atc_code).
# Products without an ingredient lookup (BN, PIN) emit a null-ingredient row.
# ATC may be null when no ATC atom shares the ingredient's CUI.
TABLE2_SQL = """
WITH RECURSIVE
product_atoms AS (
    SELECT DISTINCT CODE, TTY, STR, AUI, CUI
    FROM mrconso
    WHERE SAB = 'RXNORM'
      AND SUPPRESS = 'N'
      AND TTY IN ('SCDG', 'SCD', 'SBD', 'MIN', 'PIN', 'IN', 'BN')
),
-- (a) IN is its own ingredient.
pairs_self AS (
    SELECT p.CODE AS rxnorm_code, p.TTY AS rxnorm_tty, p.STR AS rxnorm_name,
           p.CODE AS ingredient_rxnorm_code, p.STR AS ingredient_name, p.CUI AS ingredient_cui
    FROM product_atoms p
    WHERE p.TTY = 'IN'
),
-- (b) SCDG/SCDC/SCDF have direct IN via has_ingredient (IN -> product).
pairs_direct AS (
    SELECT p.CODE AS rxnorm_code, p.TTY AS rxnorm_tty, p.STR AS rxnorm_name,
           ing.CODE AS ingredient_rxnorm_code, ing.STR AS ingredient_name, ing.CUI AS ingredient_cui
    FROM product_atoms p
    JOIN mrrel r ON r.AUI2 = p.AUI AND r.RELA = 'has_ingredient'
    JOIN mrconso ing ON ing.AUI = r.AUI1
                    AND ing.SAB = 'RXNORM' AND ing.SUPPRESS = 'N' AND ing.TTY = 'IN'
    WHERE p.TTY IN ('SCDG', 'SCD', 'SBD', 'MIN')
),
-- (c) SCD via SCDC: SCD consists_of SCDC, SCDC has_ingredient IN.
--     Data: SCDC consists_of SCD (AUI1=SCDC, AUI2=SCD).
pairs_scd_via_scdc AS (
    SELECT p.CODE AS rxnorm_code, p.TTY AS rxnorm_tty, p.STR AS rxnorm_name,
           ing.CODE AS ingredient_rxnorm_code, ing.STR AS ingredient_name, ing.CUI AS ingredient_cui
    FROM product_atoms p
    JOIN mrrel r1 ON r1.AUI2 = p.AUI AND r1.RELA = 'consists_of'
    JOIN mrconso scdc ON scdc.AUI = r1.AUI1
                     AND scdc.SAB = 'RXNORM' AND scdc.SUPPRESS = 'N' AND scdc.TTY = 'SCDC'
    JOIN mrrel r2 ON r2.AUI2 = scdc.AUI AND r2.RELA = 'has_ingredient'
    JOIN mrconso ing ON ing.AUI = r2.AUI1
                    AND ing.SAB = 'RXNORM' AND ing.SUPPRESS = 'N' AND ing.TTY = 'IN'
    WHERE p.TTY = 'SCD'
),
-- (d) SBD via SCD: SBD has_tradename SCD, then SCD via SCDC.
--     Data: SBD has_tradename SCD (AUI1=SBD, AUI2=SCD).
pairs_sbd_via_scd AS (
    SELECT p.CODE AS rxnorm_code, p.TTY AS rxnorm_tty, p.STR AS rxnorm_name,
           ing.CODE AS ingredient_rxnorm_code, ing.STR AS ingredient_name, ing.CUI AS ingredient_cui
    FROM product_atoms p
    JOIN mrrel r1 ON r1.AUI1 = p.AUI AND r1.RELA = 'has_tradename'
    JOIN mrconso scd ON scd.AUI = r1.AUI2
                    AND scd.SAB = 'RXNORM' AND scd.SUPPRESS = 'N' AND scd.TTY = 'SCD'
    JOIN mrrel r2 ON r2.AUI2 = scd.AUI AND r2.RELA = 'consists_of'
    JOIN mrconso scdc ON scdc.AUI = r2.AUI1
                     AND scdc.SAB = 'RXNORM' AND scdc.SUPPRESS = 'N' AND scdc.TTY = 'SCDC'
    JOIN mrrel r3 ON r3.AUI2 = scdc.AUI AND r3.RELA = 'has_ingredient'
    JOIN mrconso ing ON ing.AUI = r3.AUI1
                    AND ing.SAB = 'RXNORM' AND ing.SUPPRESS = 'N' AND ing.TTY = 'IN'
    WHERE p.TTY = 'SBD'
),
-- (e) MIN via has_part: IN has_part MIN.
pairs_min_via_part AS (
    SELECT p.CODE AS rxnorm_code, p.TTY AS rxnorm_tty, p.STR AS rxnorm_name,
           ing.CODE AS ingredient_rxnorm_code, ing.STR AS ingredient_name, ing.CUI AS ingredient_cui
    FROM product_atoms p
    JOIN mrrel r ON r.AUI2 = p.AUI AND r.RELA = 'has_part'
    JOIN mrconso ing ON ing.AUI = r.AUI1
                    AND ing.SAB = 'RXNORM' AND ing.SUPPRESS = 'N' AND ing.TTY = 'IN'
    WHERE p.TTY = 'MIN'
),
all_pairs AS (
    SELECT * FROM pairs_self
    UNION SELECT * FROM pairs_direct
    UNION SELECT * FROM pairs_scd_via_scdc
    UNION SELECT * FROM pairs_sbd_via_scd
    UNION SELECT * FROM pairs_min_via_part
),
-- Products that produced no ingredient row get a null row.
products_with_pairs AS (
    SELECT DISTINCT rxnorm_code FROM all_pairs
),
pairs_null AS (
    SELECT p.CODE AS rxnorm_code, p.TTY AS rxnorm_tty, p.STR AS rxnorm_name,
           CAST(NULL AS VARCHAR) AS ingredient_rxnorm_code,
           CAST(NULL AS VARCHAR) AS ingredient_name,
           CAST(NULL AS VARCHAR) AS ingredient_cui
    FROM product_atoms p
    WHERE p.TTY IN ('SCDG', 'SCD', 'SBD', 'MIN', 'PIN', 'BN')
      AND p.CODE NOT IN (SELECT rxnorm_code FROM products_with_pairs)
),
pairs_complete AS (
    SELECT * FROM all_pairs
    UNION SELECT * FROM pairs_null
),
atc_lookup AS (
    SELECT DISTINCT
        m_rxn.CUI AS ingredient_cui,
        m_atc.CODE AS atc_code
    FROM mrconso m_atc
    JOIN mrconso m_rxn ON m_atc.CUI = m_rxn.CUI
                     AND m_rxn.SAB = 'RXNORM' AND m_rxn.TTY = 'IN' AND m_rxn.SUPPRESS = 'N'
    WHERE m_atc.SAB = 'ATC' AND m_atc.SUPPRESS = 'N' AND length(m_atc.CODE) = 7
)
SELECT
    p.rxnorm_code,
    p.rxnorm_tty,
    p.rxnorm_name,
    p.ingredient_rxnorm_code,
    p.ingredient_name,
    atc.atc_code,
    substr(atc.atc_code, 1, 1) AS atc_level1,
    substr(atc.atc_code, 1, 3) AS atc_level2,
    substr(atc.atc_code, 1, 4) AS atc_level3,
    substr(atc.atc_code, 1, 5) AS atc_level4,
    atc.atc_code AS atc_level5
FROM pairs_complete p
LEFT JOIN atc_lookup atc ON atc.ingredient_cui = p.ingredient_cui
ORDER BY p.rxnorm_code, p.ingredient_rxnorm_code, atc.atc_code
"""

TABLE2_COLUMNS = [
    "rxnorm_code",
    "rxnorm_tty",
    "rxnorm_name",
    "ingredient_rxnorm_code",
    "ingredient_name",
    "atc_code",
    "atc_level1",
    "atc_level2",
    "atc_level3",
    "atc_level4",
    "atc_level5",
]


def build_rxnorm_ingredient_decomposition(con, output_path: Path) -> None:
    """Table 2: RxNorm product -> ingredient decomposition with ATC levels 1-5."""
    print("  Querying...")
    rows = con.execute(TABLE2_SQL).fetchall()
    print(f"  Writing {len(rows):,} rows to {output_path}")
    _write_csv(output_path, TABLE2_COLUMNS, rows)


# Table 3 SQL: condition -> medication ingredient (may_treat / may_prevent, IN only).
# Walks ICD10CM/SNOMEDCT_US -> MSH MH (shared CUI, hierarchy walk) -> may_treat/may_prevent -> RXNORM IN.
# Deduplicates by keeping the shallowest match_depth per (condition, ingredient, relationship).
TABLE3_SQL = """
WITH RECURSIVE
input_conditions AS (
    SELECT DISTINCT SAB, CODE
    FROM mrconso
    WHERE SAB IN ('ICD10CM', 'SNOMEDCT_US')
      AND SUPPRESS = 'N'
      AND CODE IS NOT NULL AND CODE != ''
),
seed AS (
    SELECT
        ic.SAB AS condition_source,
        ic.CODE AS condition_code,
        atom.AUI,
        atom.CUI,
        atom.STR AS condition_name,
        0 AS match_depth,
        CAST(atom.AUI AS VARCHAR) AS path_auis
    FROM input_conditions ic
    JOIN mrconso atom
      ON atom.SAB = ic.SAB AND atom.CODE = ic.CODE AND atom.SUPPRESS = 'N'
),
source_walk AS (
    SELECT * FROM seed
    UNION ALL
    SELECT
        walk.condition_source,
        walk.condition_code,
        parent.AUI,
        parent.CUI,
        parent.STR AS condition_name,
        walk.match_depth + 1,
        walk.path_auis || ' -> ' || parent.AUI
    FROM source_walk walk
    JOIN mrrel rel ON rel.AUI1 = walk.AUI AND rel.REL IN ('PAR', 'RB')
    JOIN mrconso parent
      ON parent.AUI = rel.AUI2
     AND parent.SAB = walk.condition_source
     AND parent.SUPPRESS = 'N'
    WHERE walk.match_depth < ?
      AND position(' -> ' || parent.AUI || ' -> ' IN ' -> ' || walk.path_auis || ' -> ') = 0
),
msh_nodes AS (
    SELECT DISTINCT
        walk.condition_source,
        walk.condition_code,
        walk.condition_name,
        walk.match_depth,
        mesh.AUI AS mesh_aui
    FROM source_walk walk
    JOIN mrconso mesh
      ON mesh.CUI = walk.CUI AND mesh.SAB = 'MSH' AND mesh.TTY = 'MH' AND mesh.SUPPRESS = 'N'
),
rel_edges AS (
    SELECT
        msh.condition_source,
        msh.condition_code,
        msh.condition_name,
        msh.match_depth,
        lower(rel.RELA) AS relationship_type,
        rx.CODE AS medication_rxnorm_code,
        rx.STR AS medication_name
    FROM msh_nodes msh
    JOIN mrrel rel
      ON rel.AUI1 = msh.mesh_aui
     AND lower(rel.RELA) IN ('may_treat', 'may_prevent')
    JOIN mrconso rx
      ON rx.AUI = rel.AUI2 AND rx.SAB = 'RXNORM' AND rx.SUPPRESS = 'N' AND rx.TTY = 'IN'
),
nearest_depth AS (
    SELECT condition_source, condition_code, medication_rxnorm_code, relationship_type,
           MIN(match_depth) AS match_depth
    FROM rel_edges
    GROUP BY 1, 2, 3, 4
)
SELECT
    nd.condition_source,
    nd.condition_code,
    e.condition_name,
    nd.match_depth,
    nd.medication_rxnorm_code,
    e.medication_name,
    nd.relationship_type
FROM nearest_depth nd
JOIN rel_edges e
  ON e.condition_source = nd.condition_source
 AND e.condition_code = nd.condition_code
 AND e.medication_rxnorm_code = nd.medication_rxnorm_code
 AND e.relationship_type = nd.relationship_type
 AND e.match_depth = nd.match_depth
ORDER BY nd.condition_source, nd.condition_code, nd.relationship_type, nd.medication_rxnorm_code
"""

TABLE3_COLUMNS = [
    "condition_source",
    "condition_code",
    "condition_name",
    "match_depth",
    "medication_rxnorm_code",
    "medication_name",
    "relationship_type",
]


def build_condition_medication_ingredient(con, output_path: Path, *, max_depth: int) -> None:
    """Table 3: condition -> medication ingredient (may_treat / may_prevent, IN only)."""
    print(f"  Querying (max_depth={max_depth})...")
    rows = con.execute(TABLE3_SQL, [max(0, min(int(max_depth), 8))]).fetchall()
    print(f"  Writing {len(rows):,} rows to {output_path}")
    _write_csv(output_path, TABLE3_COLUMNS, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--memory-profile", default="balanced")
    parser.add_argument(
        "--tables",
        nargs="+",
        default=["1", "2", "3"],
        choices=["1", "2", "3"],
        help="Which tables to build.",
    )
    args = parser.parse_args()

    args.output_dir = Path(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Output directory: {args.output_dir.resolve()}")
    print(f"Database: {args.db}")
    print(f"Tables: {', '.join(args.tables)}")
    print()

    if "1" in args.tables:
        print("[Table 1] patient_friendly_names.csv")
        start = time.perf_counter()
        build_patient_friendly_names(args)
        print(f"  Done in {time.perf_counter() - start:.1f}s")
        print()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    if "2" in args.tables or "3" in args.tables:
        config = local_duckdb_config(args.memory_profile)
        # Apply DuckDB memory/config settings on the connection.
        con = duckdb.connect(str(db_path), read_only=True, config=config)
        try:
            if "2" in args.tables:
                print("[Table 2] rxnorm_ingredient_decomposition.csv")
                start = time.perf_counter()
                build_rxnorm_ingredient_decomposition(
                    con, args.output_dir / "rxnorm_ingredient_decomposition.csv"
                )
                print(f"  Done in {time.perf_counter() - start:.1f}s")
                print()

            if "3" in args.tables:
                print("[Table 3] condition_medication_ingredient.csv")
                start = time.perf_counter()
                build_condition_medication_ingredient(
                    con,
                    args.output_dir / "condition_medication_ingredient.csv",
                    max_depth=args.max_depth,
                )
                print(f"  Done in {time.perf_counter() - start:.1f}s")
                print()
        finally:
            con.close()

    print("All requested tables complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
