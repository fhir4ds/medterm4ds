#!/usr/bin/env python3
"""Build a JSONL embedding index from canonical_codes.csv.

Each canonical code produces one JSON record with 4 vector texts plus metadata:
  - vector_technical: preferred-term name in the source vocabulary
  - vector_synonyms: up to K=8 synonyms sharing the CUI, prioritized by source
  - vector_friendly: the patient-friendly name from Table 1
  - vector_hierarchy: 3-level hierarchy text in the source vocabulary

Plus metadata for re-ranking/filtering: candidate_count, semantic_types,
canonical_tty, rule, ATC levels for medications.

Output: reports/fhir4px/embedding_index.jsonl (one JSON record per line).

Usage:
  python3 scripts/build_embedding_index.py
  python3 scripts/build_embedding_index.py --input reports/fhir4px/canonical_codes.csv
                                            --output reports/fhir4px/embedding_index.jsonl
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

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_INPUT = Path("reports/fhir4px/canonical_codes.csv")
DEFAULT_OUTPUT = Path("reports/fhir4px/embedding_index.jsonl")

# Synonym source priority (lower = higher priority).
_SYNONYM_SOURCE_PRIORITY = {
    "MSH": 0,        # MeSH entry terms are usually high-quality consumer/clinical synonyms
    "MEDLINEPLUS": 1,
    "CHV": 2,        # Consumer Health Vocabulary — lay synonyms
    "SNOMEDCT_US": 3,
    "ICD10CM": 4,
    "RXNORM": 5,
    "LNC": 6,
    "CPT": 7,
    "HCPCS": 7,
    "CVX": 7,
    "MTH": 8,        # Metathesaurus-native — usually mappable
    "ATC": 9,
}
_SYNONYM_K = 8


def _synonyms_sql() -> str:
    """For each (source, code, cui) in the canonical list, get all atoms sharing
    the CUI. Filter to English (LAT='ENG') so non-English sources (MSHCZE,
    MSHRUS, LNC-ES-MX, SCTSPA, ...) don't pollute the synonym vectors."""
    return """
        WITH canon AS (
            SELECT
                CAST(canonical_source AS VARCHAR) AS source,
                CAST(canonical_code AS VARCHAR) AS code,
                CAST(canonical_cui AS VARCHAR) AS cui
            FROM read_csv_auto(?, HEADER=true)
            WHERE canonical_cui IS NOT NULL AND canonical_cui != ''
        ),
        synonyms AS (
            SELECT DISTINCT
                c.source,
                c.code,
                m.STR AS synonym,
                m.SAB AS sab,
                m.TTY AS tty
            FROM canon c
            JOIN mrconso m ON m.CUI = c.cui
            WHERE m.SUPPRESS = 'N'
              AND m.lat = 'ENG'
              AND m.STR IS NOT NULL AND m.STR != ''
              AND m.SAB IS NOT NULL
        )
        SELECT source, code, synonym, sab, tty FROM synonyms
    """


def _technical_name_sql() -> str:
    """Get the preferred technical name per canonical (source, code)."""
    return """
        WITH canon AS (
            SELECT DISTINCT
                CAST(canonical_source AS VARCHAR) AS source,
                CAST(canonical_code AS VARCHAR) AS code
            FROM read_csv_auto(?, HEADER=true)
        ),
        ranked AS (
            SELECT
                c.source,
                c.code,
                m.STR,
                ROW_NUMBER() OVER (
                    PARTITION BY c.source, c.code
                    ORDER BY
                        CASE WHEN m.SUPPRESS = 'N' THEN 0 ELSE 1 END,
                        CASE
                            WHEN m.SAB = 'ICD10CM' AND m.TTY = 'HT' THEN 0
                            WHEN m.SAB IN ('ICD10PCS','SNOMEDCT_US','CVX','HCPCS','CPT') AND m.TTY = 'PT' THEN 0
                            WHEN m.SAB = 'LNC' AND m.TTY IN ('LN','LPN','LA') THEN 0
                            WHEN m.SAB = 'RXNORM' AND m.TTY IN ('IN','MIN','SCDG','SCD') THEN 0
                            WHEN m.SAB = 'MSH' AND m.TTY = 'MH' THEN 0
                            ELSE 1
                        END,
                        m.AUI
                ) AS rn
            FROM canon c
            JOIN mrconso m ON m.SAB = c.source AND m.CODE = c.code
        )
        SELECT source, code, STR FROM ranked WHERE rn = 1
    """


def _icd10cm_hierarchy_sql() -> str:
    """For each ICD10CM code, get up to 2 ancestor codes/names via PAR/RB walk."""
    return """
        WITH RECURSIVE canon AS (
            SELECT CAST(canonical_code AS VARCHAR) AS code
            FROM read_csv_auto(?, HEADER=true)
            WHERE canonical_source = 'ICD10CM'
        ),
        seed AS (
            SELECT DISTINCT c.code, m.AUI, 0 AS depth
            FROM canon c
            JOIN mrconso m ON m.SAB='ICD10CM' AND m.CODE = c.code AND m.SUPPRESS='N'
        ),
        walk AS (
            SELECT code, AUI, depth FROM seed
            UNION ALL
            SELECT w.code, parent.AUI, w.depth + 1
            FROM walk w
            JOIN mrrel r ON r.AUI1 = w.AUI AND r.REL IN ('PAR','RB')
            JOIN mrconso parent ON parent.AUI = r.AUI2
                              AND parent.SAB = 'ICD10CM'
                              AND parent.SUPPRESS = 'N'
            WHERE w.depth < 3
              AND position(' -> ' || parent.AUI || ' -> ' IN ' -> ' || (
                  SELECT string_agg(AUI, ' -> ') FROM walk w2 WHERE w2.code = w.code
              ) || ' -> ') = 0
        ),
        ranked AS (
            SELECT
                w.code,
                w.depth,
                m.STR,
                m.CODE AS ancestor_code,
                ROW_NUMBER() OVER (PARTITION BY w.code, w.depth ORDER BY m.AUI) AS rn
            FROM walk w
            JOIN mrconso m ON m.AUI = w.AUI AND m.SAB = 'ICD10CM'
            WHERE w.depth > 0 AND w.depth <= 2
        )
        SELECT code, depth, ancestor_code, STR FROM ranked WHERE rn = 1
        ORDER BY code, depth
    """


def _snomed_hierarchy_sql() -> str:
    """For each SNOMED code, get up to 2 ancestors."""
    return """
        WITH RECURSIVE canon AS (
            SELECT CAST(canonical_code AS VARCHAR) AS code
            FROM read_csv_auto(?, HEADER=true)
            WHERE canonical_source = 'SNOMEDCT_US'
        ),
        seed AS (
            SELECT DISTINCT c.code, m.AUI, 0 AS depth
            FROM canon c
            JOIN mrconso m ON m.SAB='SNOMEDCT_US' AND m.CODE = c.code AND m.SUPPRESS='N'
        ),
        walk AS (
            SELECT code, AUI, depth FROM seed
            UNION ALL
            SELECT w.code, parent.AUI, w.depth + 1
            FROM walk w
            JOIN mrrel r ON r.AUI1 = w.AUI AND r.REL IN ('PAR','RB')
            JOIN mrconso parent ON parent.AUI = r.AUI2
                              AND parent.SAB = 'SNOMEDCT_US'
                              AND parent.SUPPRESS = 'N'
            WHERE w.depth < 3
              AND position(' -> ' || parent.AUI || ' -> ' IN ' -> ' || (
                  SELECT string_agg(AUI, ' -> ') FROM walk w2 WHERE w2.code = w.code
              ) || ' -> ') = 0
        ),
        ranked AS (
            SELECT
                w.code,
                w.depth,
                m.STR,
                ROW_NUMBER() OVER (PARTITION BY w.code, w.depth ORDER BY m.AUI) AS rn
            FROM walk w
            JOIN mrconso m ON m.AUI = w.AUI AND m.SAB = 'SNOMEDCT_US'
            WHERE w.depth > 0 AND w.depth <= 2
        )
        SELECT code, depth, STR FROM ranked WHERE rn = 1
        ORDER BY code, depth
    """


def _lnc_hierarchy_sql() -> str:
    """For each LNC code, get the LOINC CLASS (ATN='LCL')."""
    return """
        WITH canon AS (
            SELECT DISTINCT CAST(canonical_code AS VARCHAR) AS code
            FROM read_csv_auto(?, HEADER=true)
            WHERE canonical_source = 'LNC'
        )
        SELECT c.code, s.ATV AS class
        FROM canon c
        LEFT JOIN mrsat s ON s.SAB = 'LNC' AND s.CODE = c.code AND s.ATN = 'LCL'
    """


def _semantic_types_sql() -> str:
    """For each (canonical_source, canonical_code), look up TUIs via mrsty
    using the canonical's CUI."""
    return """
        WITH canon AS (
            SELECT DISTINCT
                CAST(canonical_source AS VARCHAR) AS source,
                CAST(canonical_code AS VARCHAR) AS code,
                CAST(canonical_cui AS VARCHAR) AS cui
            FROM read_csv_auto(?, HEADER=true)
            WHERE canonical_cui IS NOT NULL AND canonical_cui != ''
        )
        SELECT c.source, c.code, string_agg(DISTINCT m.tui, ',' ORDER BY m.tui) AS tuis
        FROM canon c
        JOIN mrsty m ON m.cui = c.cui
        GROUP BY c.source, c.code
    """


def _atc_for_rxnorm_sql() -> str:
    """For each RXNORM canonical code, look up ATC via shared CUI with ATC atom.
    Levels extracted from the 7-char ATC code via substr."""
    return """
        WITH canon AS (
            SELECT
                CAST(canonical_code AS VARCHAR) AS code,
                CAST(canonical_cui AS VARCHAR) AS cui
            FROM read_csv_auto(?, HEADER=true)
            WHERE canonical_source = 'RXNORM' AND canonical_cui IS NOT NULL
        ),
        atc AS (
            SELECT DISTINCT
                c.code,
                atc.CODE AS atc_code,
                atc.STR AS atc_name
            FROM canon c
            JOIN mrconso atc ON atc.CUI = c.cui
                           AND atc.SAB = 'ATC'
                           AND atc.SUPPRESS = 'N'
                           AND length(atc.CODE) = 7
        )
        SELECT
            code,
            atc_code,
            substr(atc_code, 1, 1) AS atc_level1,
            substr(atc_code, 1, 3) AS atc_level2,
            substr(atc_code, 1, 4) AS atc_level3,
            substr(atc_code, 1, 5) AS atc_level4,
            atc_code AS atc_level5,
            atc_name
        FROM atc
    """


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 2
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Reading canonicals from {input_path}")
    print(f"Writing embedding index to {output_path}")
    print(f"Database: {db_path}")
    print()

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        # ---- Gather all data in batched SQL ----
        t0 = time.perf_counter()
        print("[1/5] Loading canonical codes...")
        canonicals = list(csv.DictReader(input_path.open()))
        print(f"  {len(canonicals):,} canonical rows")
        # Re-write as a temp relation DuckDB can query directly via read_csv_auto.
        csv_arg = str(input_path)

        print("[2/5] Loading technical names per code...")
        tech_rows = con.execute(_technical_name_sql(), [csv_arg]).fetchall()
        tech_by_key = {(r[0], r[1]): r[2] for r in tech_rows}
        print(f"  {len(tech_by_key):,} technical names")

        print("[3/5] Loading synonyms per code (top-K by source priority)...")
        syn_rows = con.execute(_synonyms_sql(), [csv_arg]).fetchall()
        syn_by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
        syn_seen: dict[tuple[str, str], set[str]] = defaultdict(set)
        # Sort within each (source, code) by SAB priority then dedupe.
        syn_rows.sort(
            key=lambda r: (
                r[0], r[1],
                _SYNONYM_SOURCE_PRIORITY.get(r[3] or "", 99),
                r[4] or "",
                r[2],
            )
        )
        # Exclude the technical name itself (we already have it).
        for source, code, synonym, _sab, _tty in syn_rows:
            key = (source, code)
            tech = tech_by_key.get(key)
            if tech and synonym.lower() == tech.lower():
                continue
            norm = synonym.lower().strip()
            if not norm or norm in syn_seen[key]:
                continue
            syn_seen[key].add(norm)
            if len(syn_by_key[key]) < _SYNONYM_K:
                syn_by_key[key].append(synonym)
        print(f"  {len(syn_by_key):,} codes with at least one synonym")

        print("[4/5] Loading source-specific hierarchies...")
        # ICD10CM: ancestors by depth
        hier_icd = defaultdict(list)
        for code, depth, ancestor_code, name in con.execute(_icd10cm_hierarchy_sql(), [csv_arg]).fetchall():
            hier_icd[code].append({"depth": depth, "code": ancestor_code, "name": name})
        print(f"  ICD10CM: {len(hier_icd):,} codes with hierarchy")

        # SNOMED: ancestors by depth
        hier_snomed = defaultdict(list)
        for code, depth, name in con.execute(_snomed_hierarchy_sql(), [csv_arg]).fetchall():
            hier_snomed[code].append({"depth": depth, "name": name})
        print(f"  SNOMEDCT_US: {len(hier_snomed):,} codes with hierarchy")

        # LNC: LOINC CLASS
        hier_lnc = {}
        for code, cls in con.execute(_lnc_hierarchy_sql(), [csv_arg]).fetchall():
            if cls:
                hier_lnc[code] = cls
        print(f"  LNC: {len(hier_lnc):,} codes with CLASS")

        # RXNORM ATC
        atc_by_code: dict[str, dict[str, str]] = {}
        for row in con.execute(_atc_for_rxnorm_sql(), [csv_arg]).fetchall():
            atc_by_code[row[0]] = {
                "atc_code": row[1],
                "atc_level1": row[2],
                "atc_level2": row[3],
                "atc_level3": row[4],
                "atc_level4": row[5],
                "atc_level5": row[6],
                "atc_name": row[7],
            }
        print(f"  RXNORM ATC: {len(atc_by_code):,} codes with ATC")

        # Semantic types via mrsty
        sem_by_key: dict[tuple[str, str], list[str]] = {}
        for source, code, tuis in con.execute(_semantic_types_sql(), [csv_arg]).fetchall():
            sem_by_key[(source, code)] = [t for t in tuis.split(",") if t]
        print(f"  Semantic types via mrsty: {len(sem_by_key):,} codes")

        # ---- Build per-code hierarchy vector (list of strings) ----
        print("[5/5] Building hierarchy vectors...")
        def build_hierarchy(canonical_source: str, canonical_code: str) -> list[str]:
            if canonical_source == "ICD10CM":
                levels = sorted(hier_icd.get(canonical_code, []), key=lambda x: x["depth"])
                # Depth 2 is grandparent, depth 1 is parent. Order from broad to specific.
                out = []
                for lv in reversed(levels):  # broadest first
                    out.append(f"{lv['name']} ({lv['code']})")
                return out
            if canonical_source == "SNOMEDCT_US":
                levels = sorted(hier_snomed.get(canonical_code, []), key=lambda x: x["depth"])
                out = []
                for lv in reversed(levels):
                    out.append(lv["name"])
                return out
            if canonical_source == "LNC":
                cls = hier_lnc.get(canonical_code)
                return [cls] if cls else []
            if canonical_source == "RXNORM":
                atc = atc_by_code.get(canonical_code)
                if not atc:
                    return []
                # Broadest to narrowest meaningful ATC level.
                name = atc.get("atc_name") or ""
                return [
                    f"{atc['atc_level2']} ({atc['atc_level4']} parent)",
                    f"{atc['atc_level4']} ({atc['atc_level5']} parent)",
                    f"{atc['atc_level5']} — {name}",
                ]
            if canonical_source == "CVX":
                # CVX group hierarchy is loaded externally; skip for v1.
                return []
            return []

        # ---- Write JSONL ----
        n_written = 0
        with output_path.open("w", encoding="utf-8") as f:
            for row in canonicals:
                source = row["canonical_source"]
                code = row["canonical_code"]
                key = (source, code)

                tech = tech_by_key.get(key)
                syns = syn_by_key.get(key, [])
                friendly = row["friendly_name"]
                hierarchy = build_hierarchy(source, code)

                atc = atc_by_code.get(code) if source == "RXNORM" else None
                sem_types = sem_by_key.get(key, [])

                record = {
                    "category": row["category"],
                    "friendly_name": friendly,
                    "canonical": {
                        "source": source,
                        "code": code,
                        "tty": row.get("canonical_tty") or None,
                        "cui": row.get("canonical_cui") or None,
                        "name": row.get("canonical_name") or None,
                    },
                    "rule": row["rule"],
                    "candidate_count": int(row["candidate_count"]),
                    "semantic_types": sem_types,
                    "atc": atc,
                    "vectors": {
                        "technical": tech,
                        "synonyms": syns,
                        "friendly": friendly,
                        "hierarchy": hierarchy,
                    },
                }
                f.write(json.dumps(record, ensure_ascii=False))
                f.write("\n")
                n_written += 1

        elapsed = time.perf_counter() - t0
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print()
        print(f"Wrote {n_written:,} records to {output_path} ({size_mb:.1f} MB) in {elapsed:.1f}s")
    finally:
        con.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
