#!/usr/bin/env python3
"""Roll condition and procedure codes up to MeSH descriptors.

Uses the medterm4ds prepared-table services:

  - services.crosswalk.get_same_cui_mappings  → direct same-CUI MSH hits
  - services.walk.get_ancestors_prepared      → source hierarchy walk
                                                   (then crosswalk each ancestor)

Three mapping paths in the output:
  1. direct   - source code's CUI has an MSH atom (concept-equivalent)
  2. ancestor - some ancestor (within --max-depth) has an MSH atom;
                walk_depth reports how many PAR edges up we had to go
  3. none     - no MeSH mapping within --max-depth

MSH descriptor metadata (tree number, top-level category, tree depth) comes
from mrsat.ATN='MN' joined through mrconso.CODE.

Run against the prepared DB (has both mt4ds schema and raw UMLS tables):

  PYTHONPATH=src python3 scripts/rollup_to_mesh.py
  PYTHONPATH=src python3 scripts/rollup_to_mesh.py --max-depth 3
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

from medterm4ds.core.models import CodeRef
from medterm4ds.services.crosswalk import get_same_cui_mappings
from medterm4ds.services.walk import get_ancestors_prepared

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_local.duckdb"
DEFAULT_MRSTY_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_OUTPUT = Path("reports/fhir4px/mesh_rollup.csv")
DEFAULT_MAX_DEPTH = 5

CONDITION_TUIS = (
    "T019", "T020", "T033", "T037",
    "T046", "T047", "T048", "T049",
    "T184", "T190", "T191",
)
PROCEDURE_TUIS = ("T058", "T060", "T061", "T062", "T063")

# Sources in mt4ds.walk_edges that we walk hierarchically.
WALKABLE_SOURCES = {"ICD10CM", "ICD10PCS", "SNOMEDCT_US", "CPT"}

TOP_CATEGORIES = {
    "A": "Anatomy",
    "B": "Organisms",
    "C": "Diseases",
    "D": "Chemicals and Drugs",
    "E": "Analytical, Diagnostic and Therapeutic Techniques and Equipment",
    "F": "Psychiatry and Psychology",
    "G": "Phenomena and Processes",
    "H": "Disciplines and Occupations",
    "I": "Anthropology, Education, Sociology and Social Phenomena",
    "J": "Technology, Industry, Agriculture",
    "K": "Humanities",
    "L": "Information Science",
    "M": "Named Groups",
    "N": "Health Care",
    "V": "Publication Characteristics",
    "Z": "Geographicals",
}

CSV_COLUMNS = [
    "scope", "source", "code", "source_cui", "source_name",
    "mapping_path", "walk_depth",
    "mesh_code", "mesh_name", "mesh_tree_number",
    "mesh_top_category", "mesh_top_category_name", "mesh_tree_depth",
]

logger = logging.getLogger(__name__)


def _tui_list(tuis: tuple[str, ...]) -> str:
    return ",".join(f"'{t}'" for t in tuis)


def load_aux_tables(con, mrsty_db_path: str) -> None:
    """Stage mrsty-derived CUI sets and MSH tree numbers as TEMP tables.

    The primary prepared DB (umls_local) doesn't carry mrsty or the MSH MN
    attribute. We pre-fetch them from the auxiliary DB in a separate
    connection and stage as session-temp tables so the primary connection
    can join them without an ATTACH (which would collide on the mt4ds
    schema name between the two DBs).
    """
    print(f"Loading mrsty + MSH MN from {mrsty_db_path}...", file=sys.stderr)
    aux = duckdb.connect(mrsty_db_path, read_only=True)

    cond_tuis = _tui_list(CONDITION_TUIS)
    proc_tuis = _tui_list(PROCEDURE_TUIS)

    cond_cuis = aux.execute(
        f"SELECT DISTINCT CUI FROM mrsty WHERE TUI IN ({cond_tuis})"
    ).fetchall()
    proc_cuis = aux.execute(
        f"SELECT DISTINCT CUI FROM mrsty WHERE TUI IN ({proc_tuis})"
    ).fetchall()
    mn_rows = aux.execute(
        "SELECT CODE, ATV FROM mrsat WHERE SAB='MSH' AND ATN='MN'"
    ).fetchall()
    aux.close()

    con.execute("CREATE TEMP TABLE _cond_cuis (cui VARCHAR)")
    con.executemany("INSERT INTO _cond_cuis VALUES (?)",
                    [(r[0],) for r in cond_cuis])
    con.execute("CREATE TEMP TABLE _proc_cuis (cui VARCHAR)")
    con.executemany("INSERT INTO _proc_cuis VALUES (?)",
                    [(r[0],) for r in proc_cuis])
    con.execute("CREATE TEMP TABLE _mesh_mn (code VARCHAR, tree_number VARCHAR)")
    con.executemany("INSERT INTO _mesh_mn VALUES (?, ?)", mn_rows)

    print(f"  staged {len(cond_cuis):,} condition CUIs, "
          f"{len(proc_cuis):,} procedure CUIs, "
          f"{len(mn_rows):,} MSH tree numbers", file=sys.stderr)


def build_seeds(con) -> dict[tuple[str, str], list[CodeRef]]:
    """Return {(scope, source): [CodeRef(...), ...]} for all seed codes.

    SNOMEDCT_US appears twice — once under condition scope (TUI-filtered),
    once under procedure scope (TUI-filtered). Other sources carry no TUI
    filter and go under their natural scope. Requires load_aux_tables()
    to have staged _cond_cuis / _proc_cuis.
    """
    sql = """
        WITH unioned AS (
            SELECT 'condition' AS scope, 'ICD10CM' AS source, CODE
            FROM mrconso WHERE SAB='ICD10CM' AND SUPPRESS='N' AND CODE IS NOT NULL AND CODE<>''
            UNION ALL
            SELECT 'condition', 'SNOMEDCT_US', CODE
            FROM mrconso
            WHERE SAB='SNOMEDCT_US' AND SUPPRESS='N' AND CODE IS NOT NULL AND CODE<>''
              AND CUI IN (SELECT cui FROM _cond_cuis)
            UNION ALL
            SELECT 'procedure', 'ICD10PCS', CODE
            FROM mrconso WHERE SAB='ICD10PCS' AND SUPPRESS='N' AND CODE IS NOT NULL AND CODE<>''
            UNION ALL
            SELECT 'procedure', 'SNOMEDCT_US', CODE
            FROM mrconso
            WHERE SAB='SNOMEDCT_US' AND SUPPRESS='N' AND CODE IS NOT NULL AND CODE<>''
              AND CUI IN (SELECT cui FROM _proc_cuis)
            UNION ALL
            SELECT 'procedure', 'CPT', CODE
            FROM mrconso WHERE SAB='CPT' AND SUPPRESS='N' AND CODE IS NOT NULL AND CODE<>''
            UNION ALL
            SELECT 'procedure', 'HCPCS', CODE
            FROM mrconso WHERE SAB='HCPCS' AND SUPPRESS='N' AND CODE IS NOT NULL AND CODE<>''
        )
        SELECT scope, source, CODE FROM unioned GROUP BY scope, source, CODE
    """
    rows = con.execute(sql).fetchall()
    grouped: dict[tuple[str, str], list[CodeRef]] = defaultdict(list)
    for scope, source, code in rows:
        grouped[(scope, source)].append(CodeRef(source=source, code=code))
    return grouped


def build_source_meta(con) -> dict[tuple[str, str], tuple[str, str]]:
    """{(source, code): (cui, name)} — preferred atom per source code."""
    rows = con.execute("""
        WITH ranked AS (
            SELECT SAB AS source, CODE, CUI, STR,
                   ROW_NUMBER() OVER (
                       PARTITION BY SAB, CODE
                       ORDER BY CASE TTY
                           WHEN 'PT' THEN 0 WHEN 'HT' THEN 0 WHEN 'LN' THEN 0
                           WHEN 'DP' THEN 1 WHEN 'LO' THEN 2
                           ELSE 3 END, LEN(STR)
                   ) AS rn
            FROM mrconso
            WHERE SUPPRESS='N' AND CODE IS NOT NULL AND CODE<>''
              AND SAB IN ('ICD10CM','ICD10PCS','SNOMEDCT_US','CPT','HCPCS')
        )
        SELECT source, CODE, CUI, STR FROM ranked WHERE rn = 1
    """).fetchall()
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


def build_mesh_meta(con) -> dict[str, tuple[str, str, str, str, int]]:
    """{mesh_cui: (mesh_code, mesh_name, tree_number, top_cat, tree_depth)}.

    Keyed by CUI (not by mesh_code) because the crosswalk may return any
    MSH atom TTY for a descriptor — NM, CE, etc. all share the descriptor's
    CODE but not always the same CUI. Keying by CUI makes the lookup work
    regardless of which atom TTY the crosswalk matched on. For each CUI we
    keep its deepest tree position (most specific).
    """
    rows = con.execute("""
        WITH ranked AS (
            SELECT
                c.CUI AS cui,
                c.CODE AS mesh_code, c.STR AS mesh_name,
                m.tree_number,
                REGEXP_REPLACE(m.tree_number, '^([A-Z]).*$', '\\1') AS top_cat,
                LENGTH(m.tree_number) - LENGTH(REPLACE(m.tree_number, '.', '')) + 1 AS tree_depth,
                ROW_NUMBER() OVER (
                    PARTITION BY c.CUI ORDER BY LENGTH(m.tree_number) DESC
                ) AS rn
            FROM mrconso c
            JOIN _mesh_mn m ON m.code = c.CODE
            WHERE c.SAB='MSH' AND c.SUPPRESS='N'
        )
        SELECT cui, mesh_code, mesh_name, tree_number, top_cat, tree_depth
        FROM ranked WHERE rn = 1
    """).fetchall()
    return {r[0]: (r[1], r[2], r[3], r[4], int(r[5])) for r in rows}


def pick_mesh_for_cui(
    cui: str | None,
    mesh_meta_by_cui: dict[str, tuple[str, str, str, str, int]],
) -> tuple[str | None, str | None, str | None, str | None, int | None]:
    """Resolve a CUI to its MSH descriptor fields, or all-None.

    Returns (mesh_code, mesh_name, tree_number, top_cat, tree_depth).
    """
    if cui is None:
        return None, None, None, None, None
    meta = mesh_meta_by_cui.get(cui)
    if meta is None:
        return None, None, None, None, None
    return meta


def run(db_path: str, mrsty_db_path: str, output: Path, max_depth: int) -> None:
    t0 = time.time()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    print(f"Opening {db_path} (read-only)...", file=sys.stderr)
    con = duckdb.connect(db_path, read_only=True)

    load_aux_tables(con, mrsty_db_path)

    print("Loading source + MSH metadata...", file=sys.stderr)
    t = time.time()
    seeds = build_seeds(con)
    n_seeds = sum(len(v) for v in seeds.values())
    source_meta = build_source_meta(con)
    mesh_meta = build_mesh_meta(con)
    print(f"  {n_seeds:,} seed codes, {len(mesh_meta):,} MSH descriptors "
          f"in {time.time()-t:.1f}s", file=sys.stderr)

    # mesh_meta is keyed by CUI; pick_mesh_for_cui looks up directly.

    # Per-seed-code mapping_path and walk_depth.
    # Result shape: {(scope, source, code): (mapping_path, walk_depth, mesh_fields...)}
    results: dict[tuple[str, str, str], dict] = {}

    # ---------- Pass 1: direct same-CUI ----------
    print("Pass 1: direct same-CUI mappings via services.crosswalk...", file=sys.stderr)
    t = time.time()
    all_codes = [c for group in seeds.values() for c in group]
    direct_mappings = get_same_cui_mappings(
        all_codes, con, target_sources=["MSH"],
    )
    # Map source-code → mesh_cui (deduped; first hit wins — they're all
    # same-CUI-equivalent so any is fine for the descriptor lookup).
    direct_cui: dict[tuple[str, str], str] = {}
    for m in direct_mappings:
        key = (m.source.source, m.source.code)
        if m.target_cui and key not in direct_cui:
            direct_cui[key] = m.target_cui
    print(f"  direct mappings: {len(direct_cui):,} source codes in "
          f"{time.time()-t:.1f}s", file=sys.stderr)

    for (scope, source), group in seeds.items():
        for code_ref in group:
            key = (source, code_ref.code)
            if key in direct_cui:
                mesh_fields = pick_mesh_for_cui(direct_cui[key], mesh_meta)
                if mesh_fields[0] is not None:
                    results[(scope, source, code_ref.code)] = {
                        "mapping_path": "direct",
                        "walk_depth": 0,
                        "mesh": mesh_fields,
                    }

    # ---------- Pass 2: ancestor walk + crosswalk ----------
    print(f"Pass 2: ancestor walk (max_depth={max_depth}) via services.walk...",
          file=sys.stderr)
    t = time.time()
    for (scope, source), group in seeds.items():
        if source not in WALKABLE_SOURCES:
            continue
        codes_needing_walk = [
            cr for cr in group
            if (scope, source, cr.code) not in results
        ]
        if not codes_needing_walk:
            continue

        print(f"  [{scope}/{source}] walking {len(codes_needing_walk):,} codes...",
              file=sys.stderr)
        t_src = time.time()
        ancestors = get_ancestors_prepared(codes_needing_walk, con, max_depth=max_depth)
        print(f"    got {len(ancestors):,} ancestor relations in {time.time()-t_src:.1f}s",
              file=sys.stderr)
        if not ancestors:
            continue

        # ancestor.target_code is still in `source` vocab. Collect unique ones.
        # Also track min depth per (seed_code, ancestor_code).
        min_depth_per_seed: dict[str, dict[str, int]] = defaultdict(dict)
        unique_ancestor_codes: set[str] = set()
        for a in ancestors:
            seed_code = a.source.code
            anc_code = a.target.code
            d = a.depth
            prev = min_depth_per_seed[seed_code].get(anc_code)
            if prev is None or d < prev:
                min_depth_per_seed[seed_code][anc_code] = d
            unique_ancestor_codes.add(anc_code)

        # Crosswalk all unique ancestor codes to MSH in one batched call.
        anc_refs = [CodeRef(source=source, code=c) for c in unique_ancestor_codes]
        t_xw = time.time()
        anc_mappings = get_same_cui_mappings(
            anc_refs, con, target_sources=["MSH"],
        )
        print(f"    crosswalked {len(unique_ancestor_codes):,} ancestor codes "
              f"-> {len(anc_mappings):,} MSH mappings in {time.time()-t_xw:.1f}s",
              file=sys.stderr)
        anc_cui_by_code: dict[str, str] = {}
        for m in anc_mappings:
            # m.source.code = the source-vocab ancestor code (e.g. SNOMED parent)
            # m.target_cui = the CUI of its same-CUI MSH atom
            if m.target_cui and m.source.code not in anc_cui_by_code:
                anc_cui_by_code[m.source.code] = m.target_cui

        # For each seed code, find the shallowest ancestor with an MSH hit.
        for seed_code, depths in min_depth_per_seed.items():
            best_depth = None
            best_mesh = None
            for anc_code, d in depths.items():
                anc_cui = anc_cui_by_code.get(anc_code)
                if not anc_cui:
                    continue
                mesh_fields = pick_mesh_for_cui(anc_cui, mesh_meta)
                if mesh_fields[0] is None:
                    continue
                if best_depth is None or d < best_depth:
                    best_depth = d
                    best_mesh = mesh_fields
            if best_mesh is not None:
                results[(scope, source, seed_code)] = {
                    "mapping_path": "ancestor",
                    "walk_depth": best_depth,
                    "mesh": best_mesh,
                }
    print(f"  ancestor walk + crosswalk done in {time.time()-t:.1f}s",
          file=sys.stderr)

    # ---------- Pass 3: assemble + write CSV ----------
    print(f"Writing {output}...", file=sys.stderr)
    output.parent.mkdir(parents=True, exist_ok=True)
    t = time.time()
    rows_out: list[dict] = []
    for (scope, source), group in seeds.items():
        for code_ref in group:
            code = code_ref.code
            key3 = (scope, source, code)
            src_cui, src_name = source_meta.get((source, code), (None, None))
            r = results.get(key3)
            if r is None:
                rows_out.append({
                    "scope": scope, "source": source, "code": code,
                    "source_cui": src_cui, "source_name": src_name,
                    "mapping_path": "none", "walk_depth": None,
                    "mesh_code": None, "mesh_name": None,
                    "mesh_tree_number": None,
                    "mesh_top_category": None,
                    "mesh_top_category_name": None,
                    "mesh_tree_depth": None,
                })
            else:
                mc, mn, tn, tc, td = r["mesh"]
                rows_out.append({
                    "scope": scope, "source": source, "code": code,
                    "source_cui": src_cui, "source_name": src_name,
                    "mapping_path": r["mapping_path"],
                    "walk_depth": r["walk_depth"],
                    "mesh_code": mc, "mesh_name": mn,
                    "mesh_tree_number": tn,
                    "mesh_top_category": tc,
                    "mesh_top_category_name": TOP_CATEGORIES.get(tc),
                    "mesh_tree_depth": td,
                })

    rows_out.sort(key=lambda r: (r["scope"], r["source"], r["code"]))
    with output.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for row in rows_out:
            w.writerow({k: row[k] for k in CSV_COLUMNS})
    print(f"  wrote {len(rows_out):,} rows in {time.time()-t:.1f}s",
          file=sys.stderr)

    print_summary(rows_out)
    print(f"\nTotal: {time.time()-t0:.1f}s", file=sys.stderr)


def print_summary(rows: list[dict]) -> None:
    from collections import Counter
    print("\n=== Coverage by source × mapping_path ===")
    cov: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for r in rows:
        cov[(r["scope"], r["source"])][r["mapping_path"]] += 1
    print(f"{'scope':<11}{'source':<15}{'codes':>9}{'direct':>9}{'ancestor':>10}{'none':>8}{'%mapped':>9}")
    for (scope, source), c in sorted(cov.items()):
        total = sum(c.values())
        mapped = c.get("direct", 0) + c.get("ancestor", 0)
        pct = 100.0 * mapped / total if total else 0.0
        print(f"{scope:<11}{source:<15}{total:>9,}{c.get('direct',0):>9,}"
              f"{c.get('ancestor',0):>10,}{c.get('none',0):>8,}{pct:>8.1f}%")

    print("\n=== Top-level MSH category distribution (mapped rows only) ===")
    cat_count: Counter = Counter()
    for r in rows:
        if r["mapping_path"] != "none" and r["mesh_top_category"]:
            cat_count[(r["mesh_top_category"], r["mesh_top_category_name"])] += 1
    print(f"{'cat':<4}{'name':<60}{'codes':>9}")
    for (cat, name), n in sorted(cat_count.items()):
        print(f"{cat:<4}{(name or '')[:58]:<60}{n:>9,}")

    print("\n=== Ancestor walk-depth histogram (ancestor rows only) ===")
    depth_count: Counter = Counter()
    for r in rows:
        if r["mapping_path"] == "ancestor" and r["walk_depth"] is not None:
            depth_count[r["walk_depth"]] += 1
    if depth_count:
        for d in sorted(depth_count):
            print(f"  depth {d}: {depth_count[d]:,}")
    else:
        print("  (no ancestor mappings)")

    print("\n=== MSH tree-depth distribution (mapped rows only) ===")
    td_count: Counter = Counter()
    for r in rows:
        if r["mapping_path"] != "none" and r["mesh_tree_depth"] is not None:
            td_count[r["mesh_tree_depth"]] += 1
    for d in sorted(td_count):
        print(f"  tree_depth {d}: {td_count[d]:,}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=DEFAULT_DB,
                   help=f"DuckDB path (default: {DEFAULT_DB})")
    p.add_argument("--mrsty-db", default=DEFAULT_MRSTY_DB,
                   help=f"Auxiliary DB with MRSTY (default: {DEFAULT_MRSTY_DB})")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                   help=f"Output CSV (default: {DEFAULT_OUTPUT})")
    p.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH,
                   help=f"Ancestor walk max depth (default: {DEFAULT_MAX_DEPTH})")
    args = p.parse_args()
    run(args.db, args.mrsty_db, args.output, args.max_depth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
