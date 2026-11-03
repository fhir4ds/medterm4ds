#!/usr/bin/env python3
"""Build the CPT-excluded lookup.duckdb for the HF terminology-demo Space.

Reads the LOCAL UMLS Metathesaurus RRF (either the unpacked directory or the
original .nlm zip archive) and produces a filtered DuckDB with the 3 tables
the terminology services need:

  mrconso: active atoms for 7 clinical sources (SNOMEDCT_US, ICD10CM,
           ICD10PCS, RXNORM, LNC, HCPCS, CVX) — CPT EXCLUDED
  mrrel:   hierarchy edges only (PAR, CHD, RB) connecting active atoms
  mrsat:   RxNorm NDC attributes

CPT is excluded entirely (rows and any hierarchy edges touching CPT atoms)
because the AMA license (category 3) forbids use of CPT content in publicly
accessible systems. The HF Space is public, therefore the shipped
lookup.duckdb must contain zero CPT keys.

Usage:
  python3 deploy/scripts/export_lookup_db.py \
      --rrf /path/to/umls-2025AB-metathesaurus-full.nlm \
      --output /path/to/lookup.duckdb \
      [--scratch /path/to/extract_dir]

The .nlm archive is a stored (uncompressed) zip; only MRCONSO.RRF, MRREL.RRF
and MRSAT.RRF are extracted, then deleted unless --keep-scratch is passed.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import duckdb

from medterm4ds.services.lookup_builder import (
    _MRCONSO_COLUMNS,
    _MRREL_COLUMNS,
    _MRSAT_COLUMNS,
    _duckdb_columns,
    _find_rrf_files,
    _sql_path_list,
)

# CPT EXCLUDED: AMA license forbids CPT in publicly accessible systems.
LOOKUP_SOURCES = (
    "SNOMEDCT_US", "ICD10CM", "ICD10PCS", "RXNORM",
    "LNC", "HCPCS", "CVX",
)

_ARCHIVE_MEMBERS = ("MRCONSO.RRF", "MRREL.RRF", "MRSAT.RRF")


def _extract_from_archive(archive: Path, scratch: Path) -> None:
    """Extract only the RRF members we need from the .nlm zip."""
    scratch.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        wanted = [
            info for info in zf.infolist()
            if Path(info.filename).name in _ARCHIVE_MEMBERS
        ]
        if not wanted:
            raise RuntimeError(
                f"No RRF members ({', '.join(_ARCHIVE_MEMBERS)}) found in {archive}"
            )
        for info in wanted:
            name = Path(info.filename).name
            target = scratch / name
            print(f"  extracting {info.filename} -> {target} "
                  f"({info.file_size / 1e9:.1f} GB)")
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, length=64 * 1024 * 1024)


def build(rrf_dir: Path, output_db: Path) -> dict[str, int]:
    """Run the filtered build; returns per-table row counts."""
    sources_sql = ", ".join(f"'{s}'" for s in LOOKUP_SOURCES)
    counts: dict[str, int] = {}
    con = duckdb.connect(str(output_db))
    try:
        print("[1/3] mrconso (active atoms, 7 sources, CPT excluded)...")
        start = time.perf_counter()
        mrconso_files = _find_rrf_files(rrf_dir, "MRCONSO.RRF")
        con.execute(f"""
            CREATE TABLE mrconso AS
            SELECT CODE, TTY, STR, AUI, SUPPRESS, SAB, CUI
            FROM read_csv(
                {_sql_path_list(mrconso_files)},
                delim='|', header=false,
                columns={_duckdb_columns(_MRCONSO_COLUMNS)},
                quote='', escape='', nullstr='',
                all_varchar=true, strict_mode=false,
                max_line_size=10000000
            )
            WHERE SAB IN ({sources_sql})
              AND SUPPRESS = 'N'
              AND CODE IS NOT NULL AND CODE != ''
        """)
        counts["mrconso"] = con.execute("SELECT COUNT(*) FROM mrconso").fetchone()[0]
        print(f"  {counts['mrconso']:,} rows in {time.perf_counter() - start:.1f}s")

        print("[2/3] mrrel (hierarchy edges for active atoms)...")
        start = time.perf_counter()
        mrrel_files = _find_rrf_files(rrf_dir, "MRREL.RRF")
        con.execute(f"""
            CREATE TABLE mrrel AS
            SELECT r.AUI1, r.AUI2, r.RELA, r.REL
            FROM read_csv(
                {_sql_path_list(mrrel_files)},
                delim='|', header=false,
                columns={_duckdb_columns(_MRREL_COLUMNS)},
                quote='', escape='', nullstr='',
                all_varchar=true, strict_mode=false,
                max_line_size=10000000
            ) r
            JOIN mrconso c1 ON c1.AUI = r.AUI1
            JOIN mrconso c2 ON c2.AUI = r.AUI2
            WHERE r.REL IN ('PAR', 'CHD', 'RB')
        """)
        counts["mrrel"] = con.execute("SELECT COUNT(*) FROM mrrel").fetchone()[0]
        print(f"  {counts['mrrel']:,} rows in {time.perf_counter() - start:.1f}s")

        print("[3/3] mrsat (RxNorm NDC attributes)...")
        start = time.perf_counter()
        mrsat_files = _find_rrf_files(rrf_dir, "MRSAT.RRF")
        con.execute(f"""
            CREATE TABLE mrsat AS
            SELECT CODE, SAB, ATN, ATV
            FROM read_csv(
                {_sql_path_list(mrsat_files)},
                delim='|', header=false,
                columns={_duckdb_columns(_MRSAT_COLUMNS)},
                quote='', escape='', nullstr='',
                all_varchar=true, strict_mode=false,
                max_line_size=10000000
            )
            WHERE SAB = 'RXNORM' AND ATN = 'NDC'
        """)
        counts["mrsat"] = con.execute("SELECT COUNT(*) FROM mrsat").fetchone()[0]
        print(f"  {counts['mrsat']:,} rows in {time.perf_counter() - start:.1f}s")
    finally:
        con.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build CPT-excluded lookup.duckdb from local UMLS RRF.",
        epilog="Never downloads anything; the RRF must already be on disk.",
    )
    parser.add_argument(
        "--rrf", required=True,
        help="RRF root directory, or the .nlm zip archive",
    )
    parser.add_argument(
        "--output", default="lookup.duckdb",
        help="Output DuckDB path (default: ./lookup.duckdb)",
    )
    parser.add_argument(
        "--scratch", default=None,
        help="Directory for archive extraction (default: a temp dir)",
    )
    parser.add_argument(
        "--keep-scratch", action="store_true",
        help="Keep extracted RRF files after the build",
    )
    args = parser.parse_args()

    rrf_path = Path(args.rrf)
    if not rrf_path.exists():
        print(f"ERROR: RRF path not found: {rrf_path}", file=sys.stderr)
        return 1

    output_db = Path(args.output)
    output_db.parent.mkdir(parents=True, exist_ok=True)
    if output_db.exists():
        output_db.unlink()

    total_start = time.perf_counter()

    if rrf_path.is_file():
        owned_scratch = args.scratch is None
        scratch = Path(args.scratch) if args.scratch else Path(
            tempfile.mkdtemp(prefix="umls_rrf_")
        )
        print(f"Extracting RRF members from {rrf_path} into {scratch}")
        _extract_from_archive(rrf_path, scratch)
        rrf_root: Path = scratch
    else:
        rrf_root = rrf_path

    try:
        counts = build(rrf_root, output_db)
    finally:
        if rrf_path.is_file() and not args.keep_scratch:
            owned = args.scratch is None
            if owned:
                shutil.rmtree(scratch, ignore_errors=True)
            else:
                for name in _ARCHIVE_MEMBERS:
                    p = scratch / name
                    if p.exists():
                        p.unlink()

    size_mb = output_db.stat().st_size / 1e6
    print(f"\nDone in {time.perf_counter() - total_start:.1f}s")
    print(f"Output: {output_db} ({size_mb:.1f} MB)")
    print("Row counts:", ", ".join(f"{k}={v:,}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
