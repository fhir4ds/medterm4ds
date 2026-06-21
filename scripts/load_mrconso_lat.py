#!/usr/bin/env python3
"""Add the LAT (Language) column to the existing mrconso table.

UMLS MRCONSO.RRF column 2 is LAT (e.g., ENG, SPA, FRE, CZE). This script
adds `lat` as a column on mrconso directly (no sidecar table) and populates
it from the source RRF. Idempotent: if the column already exists, it just
re-runs the UPDATE.

Why: synonyms pulled via shared-CUI crosswalks include many non-English
sources (MSHCZE, MSHRUS, MSHFRE, LNC-ES-MX, SCTSPA, ...). The `lat`
column lets callers filter to English atoms cleanly.

Usage:
  python3 scripts/load_mrconso_lat.py
  python3 scripts/load_mrconso_lat.py --db /path/to/umls.duckdb
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_MRCONSO = "/mnt/d/medterm4ds/data/umls/umls-2026AA-metathesaurus-full/2026AA/META/MRCONSO.RRF"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--mrconso", default=DEFAULT_MRCONSO)
    args = parser.parse_args()

    db_path = Path(args.db)
    mrconso_path = Path(args.mrconso)

    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    if not mrconso_path.exists():
        print(f"MRCONSO.RRF not found: {mrconso_path}", file=sys.stderr)
        return 2

    print(f"Loading LAT column from {mrconso_path}")
    print(f"Into database: {db_path}")
    start = time.perf_counter()

    con = duckdb.connect(str(db_path), read_only=False)
    try:
        # Add the column if missing.
        # Note: PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
        col_names = {row[1] for row in con.execute("PRAGMA table_info('mrconso')").fetchall()}
        if "lat" not in col_names:
            print("  Adding `lat` column to mrconso...")
            con.execute("ALTER TABLE mrconso ADD COLUMN lat VARCHAR")
        else:
            print("  `lat` column already exists; refreshing values.")

        # Build a temp table mapping AUI -> LAT, then UPDATE mrconso via JOIN.
        # MRCONSO.RRF column order (0-indexed in DuckDB auto-naming):
        #   column00=CUI, column01=LAT, column02=TS, column03=LUI, column04=STT,
        #   column05=SUI, column06=ISPREF, column07=AUI, column08=SAUI,
        #   column09=SCUI, column10=SDUI, column11=SAB, column12=TTY,
        #   column13=CODE, column14=STR, column15=SRL, column16=SUPPRESS,
        #   column17=CVF. Trailing pipe adds an empty 19th field.
        print("  Loading AUI -> LAT from MRCONSO.RRF...")
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE _lat_updates AS
            SELECT column07 AS aui, column01 AS lat
            FROM read_csv(
                '{mrconso_path.as_posix()}',
                HEADER=false,
                DELIM='|',
                QUOTE='',
                COLUMNS={{
                    'column00':'VARCHAR','column01':'VARCHAR','column02':'VARCHAR',
                    'column03':'VARCHAR','column04':'VARCHAR','column05':'VARCHAR',
                    'column06':'VARCHAR','column07':'VARCHAR','column08':'VARCHAR',
                    'column09':'VARCHAR','column10':'VARCHAR','column11':'VARCHAR',
                    'column12':'VARCHAR','column13':'VARCHAR','column14':'VARCHAR',
                    'column15':'VARCHAR','column16':'VARCHAR','column17':'VARCHAR',
                    'column18':'VARCHAR'
                }},
                NULL_PADDING=true,
                IGNORE_ERRORS=true
            )
            WHERE column07 IS NOT NULL AND column07 != ''
              AND column01 IS NOT NULL AND column01 != ''
            """
        )
        n_updates_loaded = con.execute("SELECT COUNT(*) FROM _lat_updates").fetchone()[0]
        print(f"  {n_updates_loaded:,} (AUI, LAT) pairs staged")

        # Update mrconso.lat from the temp table.
        print("  Updating mrconso.lat...")
        con.execute(
            """
            UPDATE mrconso
            SET lat = (SELECT lu.lat FROM _lat_updates lu WHERE lu.aui = mrconso.AUI)
            """
        )

        # Verify.
        n_with_lat = con.execute("SELECT COUNT(*) FROM mrconso WHERE lat IS NOT NULL").fetchone()[0]
        n_eng = con.execute("SELECT COUNT(*) FROM mrconso WHERE lat = 'ENG'").fetchone()[0]
        n_total = con.execute("SELECT COUNT(*) FROM mrconso").fetchone()[0]
        print(f"  Result: {n_with_lat:,} of {n_total:,} rows have LAT populated")
        print(f"  English (ENG) rows: {n_eng:,} ({100*n_eng/n_total:.1f}%)")

        # Report top non-English languages.
        rows = con.execute("""
            SELECT lat, COUNT(*) AS n FROM mrconso
            WHERE lat IS NOT NULL AND lat != 'ENG'
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """).fetchall()
        if rows:
            print("  Top non-English languages:")
            for r in rows:
                print(f"    {r[0]:5s} {r[1]:,}")
    finally:
        con.close()

    elapsed = time.perf_counter() - start
    print(f"Done in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
