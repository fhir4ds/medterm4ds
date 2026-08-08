#!/usr/bin/env python3
"""Roll condition and procedure codes up to OMOP standard concepts via the
OMOP Vocabulary (concept / concept_relationship / concept_ancestor).

For each source code:
  1. Find its OMOP concept_id via (vocabulary_id, concept_code).
  2. Compute effective standard concepts:
       - If the source concept is itself standard, use it.
       - Else follow concept_relationship.relationship_id='Maps to'.
  3. Look for a direct target hit (target domain + class).
  4. If no direct hit, walk concept_ancestor upward for a target ancestor.
  5. Else mark 'mapped_other' (mapped to a different domain) or 'none'.

Rollup targets:
  - Conditions  -> domain_id='Condition' AND concept_class_id='Disorder'
  - Procedures  -> domain_id='Procedure' (any class)

Run against the OMOP DuckDB built from the v5 vocabulary download:

  PYTHONPATH=src python3 scripts/rollup_to_omop.py
  PYTHONPATH=src python3 scripts/rollup_to_omop.py --max-depth 3
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

import duckdb

DEFAULT_DB = "/mnt/d/medterm4ds/data/omop/omop_vocab.duckdb"
DEFAULT_OUTPUT = Path("reports/fhir4px/omop_rollup.csv")
DEFAULT_MAX_DEPTH = 20  # concept_ancestor closure is bounded anyway

# OMOP vocabulary_id mapping for the source vocabularies we seed from.
SEED_VOCABS = {
    "ICD10CM":    {"scope": "condition", "domain_filter": None},
    "SNOMED":     {"scope": "condition", "domain_filter": "Condition"},  # split below
    "ICD10PCS":   {"scope": "procedure", "domain_filter": None},
    "CPT4":       {"scope": "procedure", "domain_filter": "Procedure"},
    "HCPCS":      {"scope": "procedure", "domain_filter": "Procedure"},
}

# OMOP SNOMED concept_id for "Clinical finding" root — used for depth reporting.
CLINICAL_FINDING_ROOT = 441840  # SNOMED 404684003

CSV_COLUMNS = [
    "scope", "source_vocab", "source_code", "source_concept_id",
    "source_name",
    "mapping_path", "walk_depth",
    "omop_target_concept_id", "omop_target_concept_code",
    "omop_target_concept_name", "omop_target_vocabulary",
    "omop_target_class", "omop_target_domain",
    "omop_depth_from_clinical_finding",
]

logger = logging.getLogger(__name__)


def build_seeds(con) -> list[dict]:
    """Build seed list — one row per source code with scope/source_vocab/code.

    SNOMED is split between condition (domain_id='Condition') and procedure
    (domain_id='Procedure') scopes via OMOP's own domain assignment, so no
    TUI filtering is needed (unlike the MeSH rollup).
    """
    rows = con.execute("""
        WITH s AS (
            SELECT 'condition' AS scope, 'ICD10CM' AS source_vocab,
                   concept_id, concept_code, concept_name
            FROM concept
            WHERE vocabulary_id='ICD10CM' AND invalid_reason IS NULL
              AND concept_code IS NOT NULL AND concept_code <> ''
            UNION ALL
            SELECT 'condition', 'SNOMED', concept_id, concept_code, concept_name
            FROM concept
            WHERE vocabulary_id='SNOMED' AND domain_id='Condition'
              AND invalid_reason IS NULL AND concept_code IS NOT NULL AND concept_code<>''
            UNION ALL
            SELECT 'procedure', 'ICD10PCS', concept_id, concept_code, concept_name
            FROM concept
            WHERE vocabulary_id='ICD10PCS' AND invalid_reason IS NULL
              AND concept_code IS NOT NULL AND concept_code<>''
            UNION ALL
            SELECT 'procedure', 'SNOMED', concept_id, concept_code, concept_name
            FROM concept
            WHERE vocabulary_id='SNOMED' AND domain_id='Procedure'
              AND invalid_reason IS NULL AND concept_code IS NOT NULL AND concept_code<>''
            UNION ALL
            SELECT 'procedure', 'CPT4', concept_id, concept_code, concept_name
            FROM concept
            WHERE vocabulary_id='CPT4' AND domain_id='Procedure'
              AND invalid_reason IS NULL AND concept_code IS NOT NULL AND concept_code<>''
            UNION ALL
            SELECT 'procedure', 'HCPCS', concept_id, concept_code, concept_name
            FROM concept
            WHERE vocabulary_id='HCPCS' AND domain_id='Procedure'
              AND invalid_reason IS NULL AND concept_code IS NOT NULL AND concept_code<>''
        )
        SELECT scope, source_vocab, concept_id, concept_code, concept_name
        FROM s GROUP BY scope, source_vocab, concept_id, concept_code, concept_name
    """).fetchall()
    return [
        {"scope": r[0], "source_vocab": r[1], "source_concept_id": r[2],
         "source_code": r[3], "source_name": r[4]}
        for r in rows
    ]


def run(db_path: str, output: Path, max_depth: int) -> None:
    t0 = time.time()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    print(f"Opening {db_path} (read-only)...", file=sys.stderr)
    con = duckdb.connect(db_path, read_only=True)

    print("Loading seed codes...", file=sys.stderr)
    t = time.time()
    seeds = build_seeds(con)
    n_total = len(seeds)
    by_scope_source: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for s in seeds:
        by_scope_source[(s["scope"], s["source_vocab"])].append(s)
    print(f"  {n_total:,} seed codes in {time.time()-t:.1f}s", file=sys.stderr)

    # Stage seed concept_ids in a temp table so subsequent joins are fast.
    con.execute(
        "CREATE TEMP TABLE _seeds "
        "(rid BIGINT, scope VARCHAR, source_vocab VARCHAR, "
        " source_concept_id BIGINT, source_code VARCHAR, source_name VARCHAR)"
    )
    con.executemany(
        "INSERT INTO _seeds VALUES (?, ?, ?, ?, ?, ?)",
        [(i, s["scope"], s["source_vocab"], s["source_concept_id"],
          s["source_code"], s["source_name"])
         for i, s in enumerate(seeds)],
    )

    # ---- Pass 1: effective standard concepts per seed ----
    # A seed's effective standard concepts = itself if standard, else 'Maps to' targets.
    print("Pass 1: effective standard concepts (self or Maps to)...", file=sys.stderr)
    t = time.time()
    con.execute("""
        CREATE TEMP TABLE _eff AS
        -- Seed is itself standard
        SELECT s.rid, c.concept_id AS eff_concept_id
        FROM _seeds s JOIN concept c ON c.concept_id = s.source_concept_id
        WHERE c.standard_concept = 'S'
        UNION
        -- Maps to standard
        SELECT s.rid, r.concept_id_2 AS eff_concept_id
        FROM _seeds s
        JOIN concept_relationship r ON r.concept_id_1 = s.source_concept_id
        WHERE r.relationship_id = 'Maps to' AND r.invalid_reason IS NULL
          AND r.concept_id_2 IN (
            SELECT concept_id FROM concept WHERE standard_concept='S'
          )
    """)
    n_eff = con.execute("SELECT COUNT(*) FROM _eff").fetchone()[0]
    n_eff_rids = con.execute("SELECT COUNT(DISTINCT rid) FROM _eff").fetchone()[0]
    print(f"  {n_eff:,} effective-standard rows covering {n_eff_rids:,} of "
          f"{n_total:,} seeds in {time.time()-t:.1f}s", file=sys.stderr)

    # ---- Pass 2: direct target hits ----
    # Seed's effective standard concept matches target domain+class.
    print("Pass 2: direct target hits...", file=sys.stderr)
    t = time.time()
    con.execute("""
        CREATE TEMP TABLE _direct AS
        SELECT e.rid, e.eff_concept_id, 0 AS walk_depth
        FROM _eff e
        JOIN concept c ON c.concept_id = e.eff_concept_id
        JOIN _seeds s ON s.rid = e.rid
        WHERE (s.scope = 'condition' AND c.domain_id='Condition' AND c.concept_class_id='Disorder')
           OR (s.scope = 'procedure' AND c.domain_id='Procedure')
    """)
    n_direct = con.execute("SELECT COUNT(DISTINCT rid) FROM _direct").fetchone()[0]
    print(f"  direct: {n_direct:,} seeds in {time.time()-t:.1f}s", file=sys.stderr)

    # ---- Pass 3: ancestor target hits ----
    # For seeds not in _direct, walk concept_ancestor upward from eff_concept_id.
    print(f"Pass 3: ancestor walk (max_depth={max_depth})...", file=sys.stderr)
    t = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _ancestor AS
        SELECT s.rid, a.ancestor_concept_id AS eff_concept_id,
               a.min_levels_of_separation AS walk_depth
        FROM _eff e
        JOIN _seeds s ON s.rid = e.rid
        JOIN concept_ancestor a ON a.descendant_concept_id = e.eff_concept_id
        JOIN concept c ON c.concept_id = a.ancestor_concept_id
        WHERE a.min_levels_of_separation BETWEEN 1 AND {max_depth}
          AND ( (s.scope = 'condition' AND c.domain_id='Condition' AND c.concept_class_id='Disorder')
             OR (s.scope = 'procedure'  AND c.domain_id='Procedure') )
    """)
    n_anc = con.execute("SELECT COUNT(DISTINCT rid) FROM _ancestor").fetchone()[0]
    print(f"  ancestor (before best-pick): {n_anc:,} seeds in {time.time()-t:.1f}s",
          file=sys.stderr)

    # ---- Pick best hit per seed (direct wins; else shallowest ancestor) ----
    print("Picking best target per seed...", file=sys.stderr)
    t = time.time()
    con.execute("""
        CREATE TEMP TABLE _best AS
        WITH ranked AS (
            SELECT rid, eff_concept_id, walk_depth, 0 AS pass_order FROM _direct
            UNION ALL
            SELECT rid, eff_concept_id, walk_depth, 1 AS pass_order FROM _ancestor
        ),
        picked AS (
            SELECT rid, eff_concept_id, walk_depth, pass_order,
                   ROW_NUMBER() OVER (
                       PARTITION BY rid
                       ORDER BY pass_order, walk_depth
                   ) AS rn
            FROM ranked
        )
        SELECT rid, eff_concept_id, walk_depth,
               CASE WHEN pass_order = 0 THEN 'direct' ELSE 'ancestor' END AS mapping_path
        FROM picked WHERE rn = 1
    """)
    n_best = con.execute("SELECT COUNT(*) FROM _best").fetchone()[0]
    print(f"  best: {n_best:,} seeds mapped in {time.time()-t:.1f}s",
          file=sys.stderr)

    # ---- Final: join back to seeds, attach target metadata, classify unmapped ----
    print("Assembling final rows...", file=sys.stderr)
    t = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _final AS
        WITH has_eff AS (
            SELECT DISTINCT rid FROM _eff
        )
        SELECT
            s.scope, s.source_vocab, s.source_code, s.source_concept_id,
            s.source_name,
            CASE
                WHEN b.rid IS NOT NULL THEN b.mapping_path
                WHEN h.rid IS NOT NULL THEN 'mapped_other'
                ELSE 'none'
            END AS mapping_path,
            b.walk_depth,
            c.concept_id AS omop_target_concept_id,
            c.concept_code AS omop_target_concept_code,
            c.concept_name AS omop_target_concept_name,
            c.vocabulary_id AS omop_target_vocabulary,
            c.concept_class_id AS omop_target_class,
            c.domain_id AS omop_target_domain,
            cf.depth AS omop_depth_from_clinical_finding
        FROM _seeds s
        LEFT JOIN _best b ON b.rid = s.rid
        LEFT JOIN has_eff h ON h.rid = s.rid
        LEFT JOIN concept c ON c.concept_id = b.eff_concept_id
        LEFT JOIN (
            SELECT descendant_concept_id, min_levels_of_separation AS depth
            FROM concept_ancestor
            WHERE ancestor_concept_id = {CLINICAL_FINDING_ROOT}
        ) cf ON cf.descendant_concept_id = c.concept_id
    """)
    n_final = con.execute("SELECT COUNT(*) FROM _final").fetchone()[0]
    print(f"  final: {n_final:,} rows in {time.time()-t:.1f}s", file=sys.stderr)

    # ---- Write CSV ----
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Writing {output}...", file=sys.stderr)
    t = time.time()
    rows = con.execute(
        f"SELECT {','.join(CSV_COLUMNS)} FROM _final "
        f"ORDER BY scope, source_vocab, source_code"
    ).fetchall()
    with output.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        w.writerows(rows)
    print(f"  wrote {len(rows):,} rows in {time.time()-t:.1f}s", file=sys.stderr)

    print_summary(con)
    print(f"\nTotal: {time.time()-t0:.1f}s", file=sys.stderr)


def print_summary(con) -> None:
    print("\n=== Coverage by source × mapping_path ===")
    df = con.execute("""
        SELECT scope, source_vocab,
               COUNT(*) AS codes,
               SUM(CASE WHEN mapping_path='direct' THEN 1 ELSE 0 END) AS direct,
               SUM(CASE WHEN mapping_path='ancestor' THEN 1 ELSE 0 END) AS ancestor,
               SUM(CASE WHEN mapping_path='mapped_other' THEN 1 ELSE 0 END) AS mapped_other,
               SUM(CASE WHEN mapping_path='none' THEN 1 ELSE 0 END) AS none,
               ROUND(100.0 * SUM(CASE WHEN mapping_path IN ('direct','ancestor')
                                      THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_mapped
        FROM _final
        GROUP BY scope, source_vocab
        ORDER BY scope, source_vocab
    """).fetchdf()
    print(df.to_string(index=False))

    print("\n=== Target class distribution (conditions: mapped rows only) ===")
    df = con.execute("""
        SELECT omop_target_class, COUNT(*) AS n
        FROM _final
        WHERE scope='condition' AND mapping_path IN ('direct','ancestor')
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchdf()
    print(df.to_string(index=False))

    print("\n=== Target vocabulary distribution (mapped rows only) ===")
    df = con.execute("""
        SELECT scope, omop_target_vocabulary, COUNT(*) AS n
        FROM _final
        WHERE mapping_path IN ('direct','ancestor')
        GROUP BY 1, 2 ORDER BY 1, 3 DESC
    """).fetchdf()
    print(df.to_string(index=False))

    print("\n=== Ancestor walk-depth histogram (ancestor rows only) ===")
    df = con.execute("""
        SELECT scope, walk_depth, COUNT(*) AS n
        FROM _final
        WHERE mapping_path='ancestor' AND walk_depth IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchdf()
    if df.empty:
        print("  (no ancestor mappings)")
    else:
        print(df.to_string(index=False))

    print("\n=== Depth-from-Clinical-finding histogram (conditions, mapped only) ===")
    df = con.execute("""
        SELECT omop_depth_from_clinical_finding AS depth, COUNT(*) AS n
        FROM _final
        WHERE scope='condition' AND mapping_path IN ('direct','ancestor')
          AND omop_depth_from_clinical_finding IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).fetchdf()
    print(df.to_string(index=False))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=DEFAULT_DB,
                   help=f"OMOP DuckDB path (default: {DEFAULT_DB})")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                   help=f"Output CSV (default: {DEFAULT_OUTPUT})")
    p.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH,
                   help=f"Ancestor walk max depth (default: {DEFAULT_MAX_DEPTH})")
    args = p.parse_args()
    run(args.db, args.output, args.max_depth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
