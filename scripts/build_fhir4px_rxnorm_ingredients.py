#!/usr/bin/env python3
"""Step 4: Build rxnorm-ingredients.json from UMLS MRCONSO/MRREL.

Queries UMLS directly for product → ingredient decomposition across
SCDG, SCD, SBD, MIN, PIN, IN, BN TTYs. Output is a JSON map keyed by
product code with abbreviated ingredient entries.

Output: reports/fhir4px/rxnorm-ingredients.json

Usage:
  PYTHONPATH=src python3 scripts/build_fhir4px_rxnorm_ingredients.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_OUTPUT = Path("reports/fhir4px/rxnorm-ingredients.json")

# Product → ingredient decomposition SQL. Data convention in this UMLS build:
#   has_ingredient: AUI1=ingredient, AUI2=product (reversed from RxNorm standard)
#   has_tradename: AUI1=SBD, AUI2=SCD
#   consists_of: AUI1=SCDC, AUI2=SCD
#   has_part: AUI1=IN, AUI2=MIN
# Multiple paths cover different TTY granularities.
SQL = """
WITH RECURSIVE
products AS (
    SELECT DISTINCT CODE, TTY, STR, AUI, CUI
    FROM mrconso
    WHERE SAB = 'RXNORM' AND SUPPRESS = 'N'
      AND TTY IN ('SCDG', 'SCD', 'SBD', 'MIN', 'PIN', 'IN', 'BN')
),
-- (a) IN is its own ingredient
pairs_self AS (
    SELECT p.CODE AS rxnorm_code, p.CODE AS ing_code, p.STR AS ing_name
    FROM products p WHERE p.TTY = 'IN'
),
-- (b) SCDG/SCDC/SCDF/SBD/SBDG: direct has_ingredient from IN
pairs_direct AS (
    SELECT p.CODE AS rxnorm_code, ing.CODE AS ing_code, ing.STR AS ing_name
    FROM products p
    JOIN mrrel r ON r.AUI2 = p.AUI AND r.RELA = 'has_ingredient'
    JOIN mrconso ing ON ing.AUI = r.AUI1 AND ing.SAB = 'RXNORM'
                   AND ing.SUPPRESS = 'N' AND ing.TTY = 'IN'
    WHERE p.TTY IN ('SCDG', 'SCD', 'SBD', 'MIN')
),
-- (c) SCD via SCDC: SCDC consists_of SCD, then SCDC has_ingredient IN
pairs_scd_via_scdc AS (
    SELECT p.CODE AS rxnorm_code, ing.CODE AS ing_code, ing.STR AS ing_name
    FROM products p
    JOIN mrrel r1 ON r1.AUI2 = p.AUI AND r1.RELA = 'consists_of'
    JOIN mrconso scdc ON scdc.AUI = r1.AUI1 AND scdc.SAB = 'RXNORM'
                    AND scdc.SUPPRESS = 'N' AND scdc.TTY = 'SCDC'
    JOIN mrrel r2 ON r2.AUI2 = scdc.AUI AND r2.RELA = 'has_ingredient'
    JOIN mrconso ing ON ing.AUI = r2.AUI1 AND ing.SAB = 'RXNORM'
                   AND ing.SUPPRESS = 'N' AND ing.TTY = 'IN'
    WHERE p.TTY = 'SCD'
),
-- (d) SBD via SCD: SBD has_tradename SCD → SCDC → IN
pairs_sbd_via_scd AS (
    SELECT p.CODE AS rxnorm_code, ing.CODE AS ing_code, ing.STR AS ing_name
    FROM products p
    JOIN mrrel r1 ON r1.AUI1 = p.AUI AND r1.RELA = 'has_tradename'
    JOIN mrconso scd ON scd.AUI = r1.AUI2 AND scd.SAB = 'RXNORM'
                   AND scd.SUPPRESS = 'N' AND scd.TTY = 'SCD'
    JOIN mrrel r2 ON r2.AUI2 = scd.AUI AND r2.RELA = 'consists_of'
    JOIN mrconso scdc ON scdc.AUI = r2.AUI1 AND scdc.SAB = 'RXNORM'
                    AND scdc.SUPPRESS = 'N' AND scdc.TTY = 'SCDC'
    JOIN mrrel r3 ON r3.AUI2 = scdc.AUI AND r3.RELA = 'has_ingredient'
    JOIN mrconso ing ON ing.AUI = r3.AUI1 AND ing.SAB = 'RXNORM'
                   AND ing.SUPPRESS = 'N' AND ing.TTY = 'IN'
    WHERE p.TTY = 'SBD'
),
-- (e) MIN via has_part: IN has_part MIN
pairs_min_via_part AS (
    SELECT p.CODE AS rxnorm_code, ing.CODE AS ing_code, ing.STR AS ing_name
    FROM products p
    JOIN mrrel r ON r.AUI2 = p.AUI AND r.RELA = 'has_part'
    JOIN mrconso ing ON ing.AUI = r.AUI1 AND ing.SAB = 'RXNORM'
                   AND ing.SUPPRESS = 'N' AND ing.TTY = 'IN'
    WHERE p.TTY = 'MIN'
),
all_pairs AS (
    SELECT * FROM pairs_self
    UNION SELECT * FROM pairs_direct
    UNION SELECT * FROM pairs_scd_via_scdc
    UNION SELECT * FROM pairs_sbd_via_scd
    UNION SELECT * FROM pairs_min_via_part
)
SELECT rxnorm_code, ing_code, ing_name FROM all_pairs
ORDER BY rxnorm_code, ing_code
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    db_path = Path(args.db)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        print("Querying UMLS for product → ingredient decomposition...")
        start = time.perf_counter()
        rows = con.execute(SQL).fetchall()
        print(f"  {len(rows):,} pairs in {time.perf_counter()-start:.1f}s")

        # Get ALL product codes (including BN/PIN with no ingredients)
        all_products = con.execute("""
            SELECT DISTINCT CODE FROM mrconso
            WHERE SAB = 'RXNORM' AND SUPPRESS = 'N'
              AND TTY IN ('SCDG', 'SCD', 'SBD', 'MIN', 'PIN', 'IN', 'BN')
              AND CODE IS NOT NULL AND CODE != ''
        """).fetchall()
        all_product_codes = {r[0] for r in all_products}
        print(f"  {len(all_product_codes):,} total product codes (including BN/PIN with no ingredients)")
    finally:
        con.close()

    result: dict[str, list] = {}
    # Initialize all products with empty arrays (BN/PIN stay empty)
    for code in all_product_codes:
        result[code] = []
    for rxnorm_code, ing_code, ing_name in rows:
        result.setdefault(rxnorm_code, []).append({"c": ing_code, "n": ing_name})

    output = {
        "_meta": {
            "schema_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(result),
        },
    }
    output.update(result)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Wrote {len(result):,} product entries to {output_path} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
