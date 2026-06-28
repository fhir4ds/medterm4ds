#!/usr/bin/env python3
"""Export a lightweight lookup DuckDB from the full UMLS DB.

Produces a ~1 GB DuckDB containing only the tables and rows the FHIR
facade needs for runtime operations:

  mrconso: active atoms for 8 clinical sources (SNOMEDCT_US, ICD10CM,
           ICD10PCS, RXNORM, LNC, CPT, HCPCS, CVX)
  mrrel:   hierarchy edges only (PAR, CHD, RB) connecting active atoms
  mrsat:   RxNorm NDC attributes

Usage:
  python3 deploy/scripts/export_lookup_db.py
  python3 deploy/scripts/export_lookup_db.py --db /mnt/d/medterm4ds/data/umls_current.duckdb --output /tmp/lookup.duckdb
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

SOURCES = (
    "SNOMEDCT_US", "ICD10CM", "ICD10PCS", "RXNORM",
    "LNC", "CPT", "HCPCS", "CVX",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/mnt/d/medterm4ds/data/umls_current.duckdb")
    parser.add_argument("--output", default="lookup.duckdb")
    args = parser.parse_args()

    source_db = Path(args.db)
    output_db = Path(args.output)

    if not source_db.exists():
        print(f"ERROR: source DB not found: {source_db}", file=sys.stderr)
        return 1

    if output_db.exists():
        output_db.unlink()

    print(f"Exporting from {source_db} -> {output_db}")
    print(f"Sources: {', '.join(SOURCES)}")

    out = duckdb.connect(str(output_db))
    out.execute(f"ATTACH '{source_db}' AS src (READ_ONLY)")

    total_start = time.perf_counter()

    # [1] mrconso: active atoms for our sources
    print("[1/3] mrconso (active atoms, 8 sources)...")
    start = time.perf_counter()
    out.execute(f"""
        CREATE TABLE mrconso AS
        SELECT CODE, TTY, STR, AUI, SUPPRESS, SAB, CUI
        FROM src.mrconso
        WHERE SAB IN ({','.join(f"'{s}'" for s in SOURCES)})
          AND SUPPRESS = 'N'
          AND CODE IS NOT NULL AND CODE != ''
    """)
    count = out.execute("SELECT COUNT(*) FROM mrconso").fetchone()[0]
    print(f"  {count:,} rows in {time.perf_counter() - start:.1f}s")

    # [2] mrrel: hierarchy edges connecting active atoms in our sources
    print("[2/3] mrrel (hierarchy edges for active atoms)...")
    start = time.perf_counter()
    out.execute("""
        CREATE TABLE mrrel AS
        SELECT r.AUI1, r.AUI2, r.RELA, r.REL
        FROM src.mrrel r
        JOIN src.mrconso c1 ON c1.AUI = r.AUI1 AND c1.SUPPRESS = 'N'
        JOIN src.mrconso c2 ON c2.AUI = r.AUI2 AND c2.SUPPRESS = 'N'
        WHERE r.REL IN ('PAR', 'CHD', 'RB')
    """)
    count = out.execute("SELECT COUNT(*) FROM mrrel").fetchone()[0]
    print(f"  {count:,} rows in {time.perf_counter() - start:.1f}s")

    # [3] mrsat: RxNorm NDC attributes
    print("[3/3] mrsat (RxNorm NDC attributes)...")
    start = time.perf_counter()
    out.execute("""
        CREATE TABLE mrsat AS
        SELECT CODE, SAB, ATN, ATV
        FROM src.mrsat
        WHERE SAB = 'RXNORM' AND ATN = 'NDC'
    """)
    count = out.execute("SELECT COUNT(*) FROM mrsat").fetchone()[0]
    print(f"  {count:,} rows in {time.perf_counter() - start:.1f}s")

    out.execute("DETACH src")
    out.close()

    size_gb = output_db.stat().st_size / 1e9
    print(f"\nDone in {time.perf_counter() - total_start:.1f}s")
    print(f"Output: {output_db} ({size_gb:.2f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
