#!/usr/bin/env python3
"""Verify a lookup.duckdb built for the HF terminology-demo Space.

Gate checks (exit 1 on any failure):
  1. Tables mrconso, mrrel, mrsat exist with the expected columns.
  2. ZERO CPT keys: no SAB='CPT' rows in mrconso, and no mrrel edge whose
     AUI1/AUI2 belongs to a CPT atom (mrrel is built by joining mrconso, so
     this must hold by construction, but we assert it anyway).
  3. Per-source row counts and per-table row counts are printed for the
     build record.
  4. File size recorded.

Usage:
  python3 deploy/scripts/verify_lookup_db.py --db /path/to/lookup.duckdb [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb

_EXPECTED_COLUMNS = {
    "mrconso": {"CODE", "TTY", "STR", "AUI", "SUPPRESS", "SAB", "CUI"},
    "mrrel": {"AUI1", "AUI2", "RELA", "REL"},
    "mrsat": {"CODE", "SAB", "ATN", "ATV"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to lookup.duckdb")
    parser.add_argument("--json", default=None, help="Optional JSON report output path")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    con = duckdb.connect(str(db_path), read_only=True)
    report: dict = {"db": str(db_path), "size_bytes": db_path.stat().st_size}
    failures: list[str] = []

    try:
        tables = {
            r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
        }
        for table, expected_cols in _EXPECTED_COLUMNS.items():
            if table not in tables:
                failures.append(f"missing table: {table}")
                continue
            cols = {
                r[0] for r in con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='main' AND table_name=?", [table]
                ).fetchall()
            }
            if cols != expected_cols:
                failures.append(
                    f"table {table} columns {sorted(cols)} != expected {sorted(expected_cols)}"
                )
            report[f"rows_{table}"] = con.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

        # Gate: zero CPT keys.
        cpt_conso = con.execute(
            "SELECT COUNT(*) FROM mrconso WHERE SAB='CPT'"
        ).fetchone()[0]
        cpt_rel = con.execute("""
            SELECT COUNT(*) FROM mrrel r
            WHERE EXISTS (SELECT 1 FROM mrconso c WHERE c.AUI=r.AUI1 AND c.SAB='CPT')
               OR EXISTS (SELECT 1 FROM mrconso c WHERE c.AUI=r.AUI2 AND c.SAB='CPT')
        """).fetchone()[0]
        report["cpt_rows_mrconso"] = cpt_conso
        report["cpt_edges_mrrel"] = cpt_rel
        if cpt_conso or cpt_rel:
            failures.append(
                f"CPT CONTAMINATION: mrconso rows={cpt_conso}, mrrel edges={cpt_rel}"
            )

        report["rows_by_source"] = dict(
            con.execute(
                "SELECT SAB, COUNT(*) FROM mrconso GROUP BY SAB ORDER BY SAB"
            ).fetchall()
        )
    finally:
        con.close()

    print(json.dumps(report, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1
    print("PASS: schema ok, zero CPT keys.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
