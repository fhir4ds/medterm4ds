#!/usr/bin/env python3
"""Rebuild snomed_top_level_depth in-place against a UMLS DuckDB.

The current 2026AA DB has a stale 77K-row / depth-1-5 version of this table
from an older loader. The current loader code caps at depth 64 and produces
~386K rows / depth-1-18 (SNOMED CT has one root: 138875005). This script
rebuilds the table without touching any other derived table.

Run with the FHIR server STOPPED (the DB is opened read-write).

Usage::

  python3 scripts/rebuild_snomed_top_level_depth.py \\
      --db /mnt/d/medterm4ds/data/umls_current.duckdb
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict, deque
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, help="Path to UMLS DuckDB (will be opened read-write)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    import duckdb
    con = duckdb.connect(str(db_path))

    try:
        # Before stats
        before = con.execute(
            "SELECT COUNT(*), COALESCE(MAX(min_top_depth), 0) FROM snomed_top_level_depth"
        ).fetchone()
        print(f"Before: {before[0]:,} rows, max depth {before[1]}", file=sys.stderr)

        t0 = time.perf_counter()

        # Mirror the loader's exact edge filter so the rebuild matches what
        # services/data_setup.py would produce on a fresh build.
        print("Loading active SNOMED atoms...", file=sys.stderr)
        active = con.execute(
            """
            SELECT DISTINCT CODE, AUI
            FROM mrconso
            WHERE SAB = 'SNOMEDCT_US'
              AND SUPPRESS = 'N'
              AND CODE IS NOT NULL AND CODE != ''
              AND AUI IS NOT NULL AND AUI != ''
            """
        ).fetchall()
        active_codes = {r[0] for r in active}
        active_aui_to_code = {r[1]: r[0] for r in active}
        print(f"  {len(active_codes):,} active codes", file=sys.stderr)

        print("Loading SNOMED IS-A edges...", file=sys.stderr)
        # Mirror services/data_setup.py exactly: PAR rows have child=AUI1,
        # parent=AUI2; CHD rows have parent=AUI1, child=AUI2 — UNION both with
        # a consistent (child, parent) column order.
        edge_rows = con.execute(
            """
            SELECT DISTINCT c.CODE AS child_code, p.CODE AS parent_code
            FROM mrrel r
            JOIN mrconso c ON c.AUI = r.AUI1 AND c.SAB = 'SNOMEDCT_US' AND c.SUPPRESS = 'N'
            JOIN mrconso p ON p.AUI = r.AUI2 AND p.SAB = 'SNOMEDCT_US' AND p.SUPPRESS = 'N'
            WHERE r.REL = 'PAR'
              AND COALESCE(r.RELA, 'isa') IN ('isa', 'inverse_isa')
              AND c.CODE != p.CODE
            UNION
            SELECT DISTINCT c.CODE AS child_code, p.CODE AS parent_code
            FROM mrrel r
            JOIN mrconso p ON p.AUI = r.AUI1 AND p.SAB = 'SNOMEDCT_US' AND p.SUPPRESS = 'N'
            JOIN mrconso c ON c.AUI = r.AUI2 AND c.SAB = 'SNOMEDCT_US' AND c.SUPPRESS = 'N'
            WHERE r.REL = 'CHD'
              AND COALESCE(r.RELA, 'isa') IN ('isa', 'inverse_isa')
              AND c.CODE != p.CODE
            """
        ).fetchall()
        print(f"  {len(edge_rows):,} child-parent edges", file=sys.stderr)

        parent_to_children: dict[str, set[str]] = defaultdict(set)
        child_codes: set[str] = set()
        for child_code, parent_code in edge_rows:
            if not child_code or not parent_code or child_code == parent_code:
                continue
            parent_to_children[parent_code].add(child_code)
            child_codes.add(child_code)

        roots = active_codes - child_codes
        print(
            f"  {len(parent_to_children):,} parents, {len(child_codes):,} children, "
            f"{len(roots):,} roots",
            file=sys.stderr,
        )

        print("Running BFS (cap=64)...", file=sys.stderr)
        queue = deque((c, 1) for c in roots)
        code_depths: dict[str, int] = {}
        while queue:
            code, depth = queue.popleft()
            cur = code_depths.get(code)
            if cur is not None and cur <= depth:
                continue
            code_depths[code] = depth
            if depth >= 64:
                continue
            for child_code in parent_to_children.get(code, ()):
                queue.append((child_code, depth + 1))
        print(f"  visited {len(code_depths):,} codes", file=sys.stderr)

        print("Replacing table...", file=sys.stderr)
        con.execute("DROP TABLE IF EXISTS snomed_top_level_depth")
        con.execute(
            "CREATE TABLE snomed_top_level_depth (code VARCHAR, min_top_depth INTEGER)"
        )
        if code_depths:
            con.executemany(
                "INSERT INTO snomed_top_level_depth VALUES (?, ?)",
                list(code_depths.items()),
            )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_snomed_top_level_depth_code ON snomed_top_level_depth(code)"
        )

        after = con.execute(
            "SELECT COUNT(*), MAX(min_top_depth) FROM snomed_top_level_depth"
        ).fetchone()
        elapsed = time.perf_counter() - t0

        # Depth distribution
        print("\nAfter:", file=sys.stderr)
        print(f"  {after[0]:,} rows, max depth {after[1]}", file=sys.stderr)
        print(f"  elapsed: {elapsed:.1f}s", file=sys.stderr)
        print("\nDepth distribution:", file=sys.stderr)
        for r in con.execute(
            "SELECT min_top_depth, count(*) FROM snomed_top_level_depth GROUP BY 1 ORDER BY 1"
        ).fetchall():
            print(f"  depth {r[0]:>2}: {r[1]:>8,}", file=sys.stderr)

    finally:
        con.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
