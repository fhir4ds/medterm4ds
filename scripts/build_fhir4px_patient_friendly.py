#!/usr/bin/env python3
"""Step 1: Resolve patient-friendly names for all active UMLS codes.

Produces:
  reports/fhir4px/patient_friendly_names.csv  (enriched with CUI/AUI/TTY/semantic_types)
  reports/fhir4px/patient_friendly_{source}.json  (per-system, code → {name, ...})

Usage:
  PYTHONPATH=src python3 scripts/build_fhir4px_patient_friendly.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import duckdb

from medterm4ds.core.config import local_duckdb_config
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.patient_friendly import get_patient_friendly_names

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_OUTPUT_DIR = Path("reports/fhir4px")

SOURCES = ("ICD10CM", "ICD10PCS", "SNOMEDCT_US", "RXNORM", "LNC", "CPT", "HCPCS", "CVX")


def _duckdb_config(profile: str) -> dict[str, object]:
    cfg = local_duckdb_config(profile)
    d: dict[str, object] = {"preserve_insertion_order": cfg.preserve_insertion_order}
    if cfg.memory_limit:
        d["memory_limit"] = cfg.memory_limit
    if cfg.temp_directory is not None:
        d["temp_directory"] = str(cfg.temp_directory)
    if cfg.threads is not None:
        d["threads"] = cfg.threads
    return d


def _load_codes(con, source: str, limit: int = 0) -> list[CodeRef]:
    sql = """
        SELECT CODE FROM mrconso
        WHERE SUPPRESS = 'N' AND CODE IS NOT NULL AND CODE != '' AND SAB = ?
        GROUP BY CODE ORDER BY CODE
    """
    params: list[object] = [source]
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    rows = con.execute(sql, params).fetchall()
    return [CodeRef(source=source, code=row[0]) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--memory-profile", default="fast")
    parser.add_argument("--max-depth", type=int, default=5)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "patient_friendly_names.csv"

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    db_config = local_duckdb_config(args.memory_profile)
    duckdb_config = _duckdb_config(args.memory_profile)
    con = duckdb.connect(str(db_path), read_only=True, config=duckdb_config)

    all_rows: list[dict] = []
    engine = LocalDuckDBEngine(con, config=db_config)

    try:
        engine.prepare_cache(SOURCES, create_indexes=True)

        for source in SOURCES:
            codes = _load_codes(con, source)
            if not codes:
                continue
            print(f"  {source}: resolving {len(codes):,} codes...", flush=True)
            start = time.perf_counter()
            results = get_patient_friendly_names(codes, engine=engine, max_depth=args.max_depth)
            elapsed = time.perf_counter() - start
            print(f"    {len(results):,} resolved in {elapsed:.1f}s")

            for r in results:
                last_step = r.matched_via.steps[-1] if r.matched_via and r.matched_via.steps else None
                all_rows.append({
                    "source": r.code.source,
                    "code": r.code.code,
                    "name": r.name,
                    "friendly_source": r.friendly_source,
                    "match_type": r.match_type,
                    "match_depth": r.match_depth,
                    "technical_name": r.technical_name,
                    "source_tty": last_step.tty if last_step else None,
                    "cui": last_step.cui if last_step else None,
                    "aui": last_step.aui if last_step else None,
                    "semantic_types": None,  # populated below via mrsty JOIN
                })
    finally:
        con.close()

    # Enrich with preferred atom info (CUI/AUI/TTY) via mrconso JOIN, then
    # add semantic_types via mrsty.  This overwrites the resolver-provided
    # values which were often blank.
    print("  Enriching with CUI/AUI/TTY/semantic_types via mrconso + mrsty...")
    con2 = duckdb.connect(str(db_path), read_only=True)
    try:
        con2.execute(
            f"""
            COPY (
                WITH pf AS (
                    SELECT * FROM (VALUES {','.join(['(?,?,?,?,?,?,?,?,?,?,?)'] * len(all_rows))}) AS t(
                        source, code, name, friendly_source, match_type, match_depth,
                        technical_name, source_tty, cui, aui, semantic_types)
                ),
                ranked_atoms AS (
                    SELECT SAB AS source, CODE AS code, CUI, AUI, TTY,
                           ROW_NUMBER() OVER (
                               PARTITION BY SAB, CODE ORDER BY
                                   CASE WHEN SUPPRESS='N' THEN 0 ELSE 1 END,
                                   CASE
                                       WHEN SAB='ICD10CM' AND TTY='HT' THEN 0
                                       WHEN SAB IN ('ICD10PCS','SNOMEDCT_US','CVX','HCPCS','CPT') AND TTY='PT' THEN 0
                                       WHEN SAB='LNC' AND TTY IN ('LN','LPN','LA') THEN 0
                                       WHEN SAB='MSH' AND TTY='MH' THEN 0
                                       WHEN SAB='RXNORM' AND TTY='SCDG' THEN 0
                                       WHEN SAB='RXNORM' AND TTY='SBDG' THEN 1
                                       WHEN SAB='RXNORM' AND TTY='SCD' THEN 2
                                       WHEN SAB='RXNORM' AND TTY='SBD' THEN 3
                                       WHEN SAB='RXNORM' AND TTY='SCDC' THEN 4
                                       WHEN SAB='RXNORM' AND TTY='SBDC' THEN 5
                                       WHEN SAB='RXNORM' AND TTY='SCDF' THEN 6
                                       WHEN SAB='RXNORM' AND TTY='SBDF' THEN 7
                                       WHEN SAB='RXNORM' AND TTY='GPCK' THEN 8
                                       WHEN SAB='RXNORM' AND TTY='BPCK' THEN 9
                                       WHEN SAB='RXNORM' AND TTY='MIN' THEN 10
                                       WHEN SAB='RXNORM' AND TTY='IN' THEN 11
                                       WHEN SAB='RXNORM' AND TTY='PIN' THEN 12
                                       WHEN SAB='RXNORM' AND TTY='BN' THEN 13
                                       WHEN SAB='RXNORM' AND TTY='DF' THEN 14
                                       WHEN SAB='RXNORM' AND TTY='DFG' THEN 15
                                       WHEN SAB='RXNORM' THEN 99
                                       ELSE 1
                                   END,
                                   AUI
                           ) AS rn
                    FROM mrconso WHERE CODE IS NOT NULL AND CODE != ''
                ),
                sem AS (
                    SELECT cui, string_agg(DISTINCT tui, ',' ORDER BY tui) AS stys
                    FROM mrsty GROUP BY cui
                )
                SELECT pf.source, pf.code, pf.name, pf.friendly_source,
                       CAST(pf.match_type AS VARCHAR) AS match_type,
                       CAST(pf.match_depth AS INTEGER) AS match_depth,
                       CAST(pf.technical_name AS VARCHAR) AS technical_name,
                       a.TTY AS source_tty,
                       a.CUI AS cui,
                       a.AUI AS aui,
                       sem.stys AS semantic_types
                FROM pf
                LEFT JOIN ranked_atoms a ON a.source = pf.source AND a.code = pf.code AND a.rn = 1
                LEFT JOIN sem ON sem.cui = a.CUI
                ORDER BY pf.source, pf.code
            ) TO '{csv_path.as_posix()}'
            (HEADER, DELIMITER ',', QUOTE '"', FORMAT CSV)
            """
        , [v for row in all_rows for v in (row["source"], row["code"], row["name"],
            row["friendly_source"], row["match_type"], row["match_depth"],
            row["technical_name"], row["source_tty"], row["cui"], row["aui"],
            row["semantic_types"])])
    finally:
        con2.close()

    n = sum(1 for _ in csv_path.open())
    size_mb = csv_path.stat().st_size / (1024 * 1024)
    print(f"  Wrote {n:,} rows to {csv_path} ({size_mb:.1f} MB)")

    # Per-system JSON files: { "code": { "name": ..., "friendly_source": ..., "cui": ... } }
    # RxNorm entries also include "tty" (the source-vocabulary term type) so
    # downstream code-selection can prefer more specific TTYs (SCD > SCDG > IN)
    # when a FHIR MedicationRequest carries multiple RxNorm codes. Other
    # sources omit "tty" because their TTY semantics don't follow the same
    # priority table; add them per-source if a similar need arises.
    by_source: dict[str, dict] = defaultdict(dict)
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            entry = {
                "name": row["name"],
                "friendly_source": row["friendly_source"],
                "match_type": row["match_type"],
                "cui": row.get("cui") or None,
            }
            if row["source"] == "RXNORM":
                entry["tty"] = row.get("source_tty") or None
            by_source[row["source"]][row["code"]] = entry

    # Canonical code lookup: for SNOMED conditions, find the shortest ICD-10
    # code sharing the same CUI (for condition_associations.json lookup).
    # All codes get canonical_code/canonical_system; SNOMED codes without an
    # ICD-10 equivalent default to themselves.
    _CANONICAL_SYSTEM = {
        "ICD10CM": "icd10", "ICD10PCS": "icd10pcs", "SNOMEDCT_US": "snomedct_us",
        "RXNORM": "rxnorm", "LNC": "lnc", "CPT": "cpt", "HCPCS": "hcpcs", "CVX": "cvx",
    }
    snomed_canonicals: dict[str, str] = {}
    if "SNOMEDCT_US" in by_source:
        con3 = duckdb.connect(str(db_path), read_only=True)
        try:
            snomed_codes = list(by_source["SNOMEDCT_US"].keys())
            chunk_size = 5000
            for i in range(0, len(snomed_codes), chunk_size):
                chunk = snomed_codes[i:i+chunk_size]
                placeholders = ",".join(["?"] * len(chunk))
                rows = con3.execute(
                    f"""
                    SELECT s.CODE, MIN(i.CODE) AS icd10
                    FROM mrconso s
                    JOIN mrconso i ON i.CUI = s.CUI
                      AND i.SAB = 'ICD10CM' AND i.SUPPRESS = 'N'
                      AND i.CODE IS NOT NULL AND i.CODE != ''
                    WHERE s.SAB = 'SNOMEDCT_US' AND s.SUPPRESS = 'N'
                      AND s.CODE IN ({placeholders})
                    GROUP BY s.CODE
                    """,
                    chunk
                ).fetchall()
                for code, icd10 in rows:
                    if icd10:
                        snomed_canonicals[code] = str(icd10)
        finally:
            con3.close()
    print(f"  SNOMED → ICD-10 canonicals: {len(snomed_canonicals):,}")

    # Apply canonical_code/canonical_system to all entries.
    from medterm4ds.core.normalize import normalize_icd10_to_category
    for source, entries in by_source.items():
        self_system = _CANONICAL_SYSTEM.get(source, source.lower())
        for code, entry in entries.items():
            if source == "SNOMEDCT_US" and code in snomed_canonicals:
                entry["canonical_code"] = snomed_canonicals[code]
                entry["canonical_system"] = "icd10"
            elif source == "ICD10CM":
                entry["canonical_code"] = normalize_icd10_to_category(code)
                entry["canonical_system"] = "icd10"
            else:
                entry["canonical_code"] = code
                entry["canonical_system"] = self_system

    for source, entries in by_source.items():
        json_path = output_dir / f"patient_friendly_{source.lower()}.json"
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)
        print(f"  Wrote {len(entries):,} entries to {json_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
