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

# The public lookup universe (must stay derived from the FULL export
# list minus the license exclusion — QC3-A3, single source of truth).
_CPT_EXCLUDED = {"CPT"}
PUBLIC_LOOKUP_SOURCES = tuple(
    s for s in (
        "SNOMEDCT_US", "ICD10CM", "ICD10PCS", "RXNORM",
        "LNC", "CPT", "HCPCS", "CVX",
    )
    if s not in _CPT_EXCLUDED
)

# Per-source row floors (QC3-E8): ~40% of the UMLS 2026AA active-atom
# counts (SNOMED 1.04M, LNC 0.75M, ICD10PCS 0.27M, RXNORM 0.20M,
# ICD10CM 0.11M, HCPCS 7.7k, CVX 1.0k measured on the build box). A
# catastrophically truncated extract (partial RRF, wrong drop, ENOSPC
# mid-build) fails LOUD instead of passing a vacuous "zero CPT keys".
_MIN_ROWS_PER_SOURCE = {
    "SNOMEDCT_US": 400_000,
    "LNC": 300_000,
    "ICD10PCS": 100_000,
    "RXNORM": 80_000,
    "ICD10CM": 40_000,
    "HCPCS": 3_000,
    "CVX": 400,
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

        # QC3-E8: unexpected extra tables fail — a stray backup/partial
        # table must not ride along in a shipped artifact.
        extra = tables - set(_EXPECTED_COLUMNS)
        if extra:
            failures.append(f"unexpected extra tables: {sorted(extra)}")

        # Gate: zero CPT keys.
        cpt_conso = con.execute(
            "SELECT COUNT(*) FROM mrconso WHERE SAB='CPT'"
        ).fetchone()[0]
        cpt_rel = con.execute("""
            SELECT COUNT(*) FROM mrrel r
            WHERE EXISTS (SELECT 1 FROM mrconso c WHERE c.AUI=r.AUI1 AND c.SAB='CPT')
               OR EXISTS (SELECT 1 FROM mrconso c WHERE c.AUI=r.AUI2 AND c.SAB='CPT')
        """).fetchone()[0]
        # QC3-S10: the SAB universe must be exactly the public source
        # list — CPT under ANY label (HCPT, NUCCHCPT, case variants) or
        # any unlisted vocabulary fails, not just the literal 'CPT'.
        sab_rows = con.execute(
            "SELECT DISTINCT SAB FROM mrconso"
        ).fetchall()
        sabs = {r[0] for r in sab_rows}
        illegal = sabs - set(PUBLIC_LOOKUP_SOURCES)
        # QC3-S10: mrsat must be CPT-clean too.
        cpt_sat = con.execute(
            "SELECT COUNT(*) FROM mrsat WHERE SAB='CPT'"
        ).fetchone()[0]
        # QC3-S2 residue: CPT Level I codes are 5-digit numerics; under
        # HCPCS they would be the AMA-carrier shape. Empirically zero in
        # UMLS 2026AA (HCPCS carries Level II alpha codes + 2-char
        # modifiers only) — assert it stays that way.
        cpt_shaped = con.execute(
            "SELECT COUNT(*) FROM mrconso WHERE SAB='HCPCS' AND CODE ~ '^[0-9]{5}$'"
        ).fetchone()[0]
        report["cpt_rows_mrconso"] = cpt_conso
        report["cpt_edges_mrrel"] = cpt_rel
        report["cpt_rows_mrsat"] = cpt_sat
        report["cpt_shaped_hcpcs_codes"] = cpt_shaped
        report["illegal_sabs"] = sorted(illegal)
        if illegal:
            failures.append(
                f"SAB universe {sorted(sabs)} exceeds public source list "
                f"{list(PUBLIC_LOOKUP_SOURCES)} (CPT under another label?)"
            )
        if cpt_sat:
            failures.append(f"CPT CONTAMINATION: mrsat rows={cpt_sat}")
        if cpt_shaped:
            failures.append(
                f"CPT-SHAPED CODES under HCPCS: {cpt_shaped} (Level I carriers?)"
            )
        if cpt_conso or cpt_rel:
            failures.append(
                f"CPT CONTAMINATION: mrconso rows={cpt_conso}, mrrel edges={cpt_rel}"
            )

        report["rows_by_source"] = dict(
            con.execute(
                "SELECT SAB, COUNT(*) FROM mrconso GROUP BY SAB ORDER BY SAB"
            ).fetchall()
        )
        # QC3-E8: per-source floors — a truncated extract fails loud.
        for source, floor in _MIN_ROWS_PER_SOURCE.items():
            got = report["rows_by_source"].get(source, 0)
            if got < floor:
                failures.append(
                    f"truncated extract: {source} has {got:,} rows, floor {floor:,}"
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
