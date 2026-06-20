#!/usr/bin/env python3
"""Load UMLS MRSTY.RRF into the local DuckDB as table `mrsty`.

The MRSTY file maps each CUI to one or more semantic types (TUI/STY).
3.9M rows, ~30s to load. Idempotent — drops and recreates the table.

Usage:
  python3 scripts/load_mrsty.py
  python3 scripts/load_mrsty.py --db /path/to/umls.duckdb
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_MGSTY = "/mnt/d/medterm4ds/data/umls/umls-2026AA-metathesaurus-full/2026AA/META/MRSTY.RRF"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--mrsty", default=DEFAULT_MGSTY)
    args = parser.parse_args()

    db_path = Path(args.db)
    mrsty_path = Path(args.mrsty)

    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    if not mrsty_path.exists():
        print(f"MRSTY.RRF not found: {mrsty_path}", file=sys.stderr)
        return 2

    print(f"Loading MRSTY from {mrsty_path}")
    print(f"Into database: {db_path}")
    start = time.perf_counter()

    # Open READ_WRITE to add the table.
    con = duckdb.connect(str(db_path), read_only=False)
    try:
        # Drop if exists
        con.execute("DROP TABLE IF EXISTS mrsty")

        # Create and load. MRSTY.RRF is pipe-delimited with a trailing pipe
        # (so 7 fields per row, last is empty).
        con.execute(f"""
            CREATE TABLE mrsty AS
            SELECT
                column00 AS cui,
                column01 AS tui,
                column03 AS sty
            FROM read_csv(
                '{mrsty_path.as_posix()}',
                HEADER=false,
                DELIM='|',
                QUOTE='',
                COLUMNS={{
                    'column00': 'VARCHAR',
                    'column01': 'VARCHAR',
                    'column02': 'VARCHAR',
                    'column03': 'VARCHAR',
                    'column04': 'VARCHAR',
                    'column05': 'VARCHAR',
                    'column06': 'VARCHAR'
                }},
                NULL_PADDING=true,
                IGNORE_ERRORS=true
            )
            WHERE column00 IS NOT NULL AND column00 != ''
        """)

        # Index for the common lookup pattern
        con.execute("CREATE INDEX IF NOT EXISTS mrsty_cui_idx ON mrsty(cui)")

        n = con.execute("SELECT COUNT(*) FROM mrsty").fetchone()[0]
        distinct_cuis = con.execute("SELECT COUNT(DISTINCT cui) FROM mrsty").fetchone()[0]
        distinct_tuis = con.execute("SELECT COUNT(DISTINCT tui) FROM mrsty").fetchone()[0]
    finally:
        con.close()

    elapsed = time.perf_counter() - start
    print(f"Loaded {n:,} MRSTY rows ({distinct_cuis:,} distinct CUIs, {distinct_tuis} distinct TUIs) in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
