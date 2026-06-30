"""Build lookup.duckdb directly from UMLS RRF files (no full DB needed).

Creates a filtered DuckDB (~287 MB) containing only the 8 clinical sources
needed by the FHIR terminology server. Reads RRF pipe-delimited files directly
via DuckDB's read_csv — no intermediate full-DB build step.

This avoids needing 56 GB of disk space to build a 287 MB filtered DB.
Total disk needed: ~2 GB (RRF files) + 287 MB (output) = ~2.3 GB.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb

_MRCONSO_COLUMNS = ("CUI", "LAT", "TS", "LUI", "STT", "SUI", "ISPREF", "AUI", "SAUI", "SCUI", "SDUI", "SAB", "TTY", "CODE", "STR", "SRL", "SUPPRESS", "CVF")
_MRREL_COLUMNS = ("CUI1", "AUI1", "STYPE1", "REL", "CUI2", "AUI2", "STYPE2", "RELA", "RUI", "SRUI", "SAB", "SL", "RG", "DIR", "SUPPRESS", "CVF")
_MRSAT_COLUMNS = ("CUI", "LUI", "SUI", "METAUI", "STYPE", "CODE", "ATUI", "SATUI", "ATN", "SAB", "ATV", "SUPPRESS", "CVF")

_LOOKUP_SOURCES = (
    "SNOMEDCT_US", "ICD10CM", "ICD10PCS", "RXNORM",
    "LNC", "CPT", "HCPCS", "CVX",
)

_PATH_ALLOWED = re.compile(r"^[A-Za-z0-9._/\-:\\]+$")


def _duckdb_columns(columns: tuple[str, ...]) -> str:
    return "{" + ", ".join(f"{c}: VARCHAR" for c in columns) + "}"


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_path_list(paths: list[str]) -> str:
    parts = []
    for path_str in paths:
        if not _PATH_ALLOWED.match(path_str):
            raise RuntimeError(f"Refusing SQL path with disallowed characters: {path_str!r}")
        parts.append(_sql_string(path_str))
    return "[" + ", ".join(parts) + "]"


def _find_rrf_files(rrf_root: Path, name: str) -> list[str]:
    """Find RRF files by name, handling .gz and nested directories."""
    root = Path(rrf_root)
    direct = root / name
    if direct.exists():
        return [str(direct)]
    matches = sorted(str(p) for p in root.rglob(name) if p.is_file())
    if matches:
        return matches
    gz_matches = sorted(str(p) for p in root.rglob(f"{name}*") if p.is_file() and str(p).endswith(".gz"))
    if gz_matches:
        return gz_matches
    raise FileNotFoundError(f"Could not find {name} under {root}")


def build_lookup_from_rrf(
    rrf_dir: str | Path,
    output_db: str | Path,
) -> Path:
    """Build a filtered lookup DuckDB directly from UMLS RRF files.

    Reads pipe-delimited RRF files via DuckDB's read_csv with WHERE filters,
    creating only the 3 tables the FHIR server needs:

      mrconso: 8 clinical sources, active atoms only (~2.45M rows)
      mrrel:   hierarchy edges only (PAR, CHD, RB) (~14.6M rows)
      mrsat:   RxNorm NDC attributes (~252K rows)

    Total: ~287 MB. No full 56 GB intermediate DB needed.
    """
    rrf_root = Path(rrf_dir)
    output = Path(output_db)

    if output.exists():
        output.unlink()

    sources_sql = ", ".join(f"'{s}'" for s in _LOOKUP_SOURCES)
    con = duckdb.connect(str(output))

    try:
        print("[1/3] Building mrconso (8 sources, active only)...")
        mrconso_files = _find_rrf_files(rrf_root, "MRCONSO.RRF")
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
        count = con.execute("SELECT COUNT(*) FROM mrconso").fetchone()[0]
        print(f"  {count:,} rows")

        print("[2/3] Building mrrel (hierarchy edges)...")
        mrrel_files = _find_rrf_files(rrf_root, "MRREL.RRF")
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
        count = con.execute("SELECT COUNT(*) FROM mrrel").fetchone()[0]
        print(f"  {count:,} rows")

        print("[3/3] Building mrsat (RxNorm NDCs)...")
        mrsat_files = _find_rrf_files(rrf_root, "MRSAT.RRF")
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
        count = con.execute("SELECT COUNT(*) FROM mrsat").fetchone()[0]
        print(f"  {count:,} rows")

    finally:
        con.close()

    size_mb = output.stat().st_size / 1e6
    print(f"\nDone: {output} ({size_mb:.1f} MB)")
    return output
