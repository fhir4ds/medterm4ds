#!/usr/bin/env python3
"""Step 3: Build condition_associations.json from UMLS may_treat + may_prevent.

Queries UMLS directly (no intermediate CSVs). Walks ICD-10 and SNOMED condition
hierarchies to find may_treat/may_prevent edges to RxNorm ingredient (IN) targets.
Merges Synthea condition-lab baseline if available.

Output: reports/fhir4px/condition_associations.json

Usage:
  PYTHONPATH=src python3 scripts/build_fhir4px_associations.py
  PYTHONPATH=src python3 scripts/build_fhir4px_associations.py --synthea-labs path/to/synthea.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_OUTPUT = Path("reports/fhir4px/condition_associations.json")
DEFAULT_MAX_DEPTH = 4

ASSOCIATIONS_SQL = """
WITH RECURSIVE
input_conditions AS (
    SELECT DISTINCT SAB, CODE
    FROM mrconso
    WHERE SAB IN ('ICD10CM', 'SNOMEDCT_US')
      AND SUPPRESS = 'N' AND CODE IS NOT NULL AND CODE != ''
),
seed AS (
    SELECT ic.SAB AS condition_source, ic.CODE AS condition_code,
           atom.AUI, atom.CUI, atom.STR AS condition_name,
           0 AS match_depth,
           CAST(atom.AUI AS VARCHAR) AS path_auis
    FROM input_conditions ic
    JOIN mrconso atom
      ON atom.SAB = ic.SAB AND atom.CODE = ic.CODE AND atom.SUPPRESS = 'N'
),
source_walk AS (
    SELECT * FROM seed
    UNION ALL
    SELECT walk.condition_source, walk.condition_code,
           parent.AUI, parent.CUI, parent.STR AS condition_name,
           walk.match_depth + 1,
           walk.path_auis || ' -> ' || parent.AUI
    FROM source_walk walk
    JOIN mrrel rel ON rel.AUI1 = walk.AUI AND rel.REL IN ('PAR', 'RB')
    JOIN mrconso parent
      ON parent.AUI = rel.AUI2 AND parent.SAB = walk.condition_source
     AND parent.SUPPRESS = 'N'
    WHERE walk.match_depth < ?
      AND position(' -> ' || parent.AUI || ' -> ' IN ' -> ' || walk.path_auis || ' -> ') = 0
),
msh_nodes AS (
    SELECT DISTINCT walk.condition_source, walk.condition_code,
           walk.condition_name, walk.match_depth, mesh.AUI AS mesh_aui
    FROM source_walk walk
    JOIN mrconso mesh
      ON mesh.CUI = walk.CUI AND mesh.SAB = 'MSH' AND mesh.TTY = 'MH'
     AND mesh.SUPPRESS = 'N'
),
rel_edges AS (
    SELECT msh.condition_source, msh.condition_code, msh.condition_name,
           msh.match_depth,
           lower(rel.RELA) AS relationship_type,
           rx.CODE AS medication_code, rx.STR AS medication_name
    FROM msh_nodes msh
    JOIN mrrel rel ON rel.AUI1 = msh.mesh_aui
                  AND lower(rel.RELA) IN ('may_treat', 'may_prevent')
    JOIN mrconso rx ON rx.AUI = rel.AUI2 AND rx.SAB = 'RXNORM'
                   AND rx.SUPPRESS = 'N' AND rx.TTY = 'IN'
),
nearest AS (
    SELECT condition_source, condition_code, medication_code, relationship_type,
           MIN(match_depth) AS match_depth
    FROM rel_edges
    GROUP BY 1, 2, 3, 4
)
SELECT ne.condition_source, ne.condition_code, ne.relationship_type,
       ne.medication_code, ne.match_depth
FROM nearest ne
JOIN rel_edges e
  ON e.condition_source = ne.condition_source
 AND e.condition_code = ne.condition_code
 AND e.medication_code = ne.medication_code
 AND e.relationship_type = ne.relationship_type
 AND e.match_depth = ne.match_depth
"""


def _depth_to_strength(depth: int) -> str | None:
    if depth <= 1:
        return "strong"
    if depth == 2:
        return "moderate"
    if depth <= 4:
        return "weak"
    return None  # depth 5+ excluded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--synthea-labs", default=None,
                        help="Path to Synthea condition-lab JSON (code-keyed)")
    args = parser.parse_args()

    db_path = Path(args.db)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        print(f"Querying UMLS may_treat + may_prevent (max_depth={args.max_depth})...")
        start = time.perf_counter()
        rows = con.execute(ASSOCIATIONS_SQL, [args.max_depth]).fetchall()
        print(f"  {len(rows):,} raw association rows in {time.perf_counter()-start:.1f}s")

        # Group by condition code, deduplicate (condition, medication, relationship)
        associations: dict[str, dict] = defaultdict(lambda: {"labs": [], "medications": []})
        seen_meds: dict[str, set] = defaultdict(set)

        for _cond_source, cond_code, rel_type, med_code, depth in rows:
            strength = _depth_to_strength(depth)
            if strength is None:
                continue
            key = f"{cond_code}"
            med_key = (med_code, rel_type)
            if med_key not in seen_meds[key]:
                seen_meds[key].add(med_key)
                rel_label = "treats" if rel_type == "may_treat" else "prevents"
                associations[key]["medications"].append({
                    "code": med_code,
                    "strength": strength,
                    "relationship": rel_label,
                })
    finally:
        con.close()

    # Merge Synthea condition-lab if provided
    if args.synthea_labs:
        synthea_path = Path(args.synthea_labs)
        if synthea_path.exists():
            print(f"Merging Synthea condition-lab from {synthea_path}...")
            with synthea_path.open() as f:
                synthea = json.load(f)
            for cond_code, labs in synthea.items():
                if cond_code == "_meta":
                    continue
                for lab_code in labs:
                    associations[cond_code]["labs"].append({
                        "code": lab_code,
                        "strength": "strong",
                    })
            print(f"  Merged {len(synthea)} conditions with lab associations")
        else:
            print(f"  Synthea file not found: {synthea_path}, skipping")

    # Build output
    n_conditions = len(associations)
    n_meds = sum(len(v["medications"]) for v in associations.values())
    n_labs = sum(len(v["labs"]) for v in associations.values())

    output = {
        "_meta": {
            "schema_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": {
                "labs": "Synthea modules (if provided) + UMLS monitoring",
                "medications": "UMLS may_treat + may_prevent (depths 0-4, ingredient-level)",
            },
            "stats": {
                "conditions": n_conditions,
                "medication_associations": n_meds,
                "lab_associations": n_labs,
            },
        },
    }
    output.update(dict(sorted(associations.items())))

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Wrote {n_conditions:,} conditions ({n_meds:,} med associations, "
          f"{n_labs:,} lab associations) to {output_path} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
