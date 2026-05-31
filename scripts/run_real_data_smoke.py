#!/usr/bin/env python3
"""Smoke-test lookup, mapping, and hierarchy against a real UMLS DuckDB file."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds import CodeRef, get_code_infos, get_code_mappings
from medterm4ds.core.config import local_lite_config
from medterm4ds.engines.duckdb import LocalLiteEngine
from medterm4ds.services.hierarchy import get_code_relations
from medterm4ds.services.inventory import normalize_sources


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    status: str
    elapsed_seconds: float
    details: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/mnt/d/medterm/data/umls_local.duckdb")
    parser.add_argument("--source", default="ICD10CM")
    parser.add_argument("--target-source", default="SNOMEDCT_US")
    parser.add_argument("--memory-profile", default="low")
    parser.add_argument("--output-json", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    try:
        import duckdb
    except ImportError as exc:
        print("DuckDB is required. Install medterm4ds[duckdb].", file=sys.stderr)
        raise SystemExit(2) from exc

    source = normalize_sources(args.source)[0]
    target_source = normalize_sources(args.target_source)[0]
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalLiteEngine(con, config=local_lite_config(args.memory_profile))
        checks = [
            _check_lookup(con, engine, source),
            _check_mapping(con, engine, source, target_source),
            _check_hierarchy(con, engine, source),
        ]
    finally:
        con.close()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "source": source,
        "target_source": target_source,
        "checks": [asdict(check) for check in checks],
    }
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({check.name: check.status for check in checks}, sort_keys=True))
    return 0 if all(check.status == "pass" for check in checks) else 1


def _check_lookup(con, engine: LocalLiteEngine, source: str) -> SmokeCheck:
    start = time.perf_counter()
    code = _first_active_code(con, source)
    if not code:
        return SmokeCheck("lookup", "skip", time.perf_counter() - start, {"reason": "no active code"})
    result = get_code_infos([CodeRef(source, code)], engine=engine)[0]
    status = "pass" if result and result.code.code == code and result.name else "fail"
    return SmokeCheck(
        "lookup",
        status,
        time.perf_counter() - start,
        {"source": source, "code": code, "name": result.name if result else None},
    )


def _check_mapping(
    con,
    engine: LocalLiteEngine,
    source: str,
    target_source: str,
) -> SmokeCheck:
    start = time.perf_counter()
    code = _first_same_cui_mapping_code(con, source, target_source)
    if not code:
        return SmokeCheck(
            "mapping",
            "skip",
            time.perf_counter() - start,
            {"reason": "no same-CUI source/target pair"},
        )
    rows = get_code_mappings(
        [CodeRef(source, code)],
        engine=engine,
        target_sources=[target_source],
        max_results_per_code=10,
    )
    status = "pass" if rows else "fail"
    return SmokeCheck(
        "mapping",
        status,
        time.perf_counter() - start,
        {
            "source": source,
            "code": code,
            "target_source": target_source,
            "rows": len(rows),
            "first_target": rows[0].target.code if rows else None,
        },
    )


def _check_hierarchy(con, engine: LocalLiteEngine, source: str) -> SmokeCheck:
    start = time.perf_counter()
    code = _first_child_code(con, source)
    if not code:
        return SmokeCheck("hierarchy", "skip", time.perf_counter() - start, {"reason": "no parent edge"})
    rows = get_code_relations(
        [CodeRef(source, code)],
        engine=engine,
        direction="parents",
        max_depth=1,
    )
    status = "pass" if rows else "fail"
    return SmokeCheck(
        "hierarchy",
        status,
        time.perf_counter() - start,
        {"source": source, "code": code, "rows": len(rows), "first_parent": rows[0].target.code if rows else None},
    )


def _first_active_code(con, source: str) -> str | None:
    row = con.execute(
        """
        SELECT CODE
        FROM mrconso
        WHERE SAB = ?
          AND SUPPRESS = 'N'
          AND CODE IS NOT NULL
          AND CODE != ''
        GROUP BY CODE
        ORDER BY CODE
        LIMIT 1
        """,
        [source],
    ).fetchone()
    return str(row[0]) if row else None


def _first_same_cui_mapping_code(con, source: str, target_source: str) -> str | None:
    row = con.execute(
        """
        SELECT s.CODE
        FROM mrconso s
        JOIN mrconso t ON t.CUI = s.CUI
        WHERE s.SAB = ?
          AND t.SAB = ?
          AND s.SUPPRESS = 'N'
          AND t.SUPPRESS = 'N'
          AND s.CODE IS NOT NULL
          AND s.CODE != ''
          AND t.CODE IS NOT NULL
          AND t.CODE != ''
        GROUP BY s.CODE
        ORDER BY s.CODE
        LIMIT 1
        """,
        [source, target_source],
    ).fetchone()
    return str(row[0]) if row else None


def _first_child_code(con, source: str) -> str | None:
    row = con.execute(
        """
        SELECT c.CODE
        FROM mrconso c
        JOIN mrrel r ON r.AUI1 = c.AUI AND r.REL = 'PAR'
        JOIN mrconso p ON p.AUI = r.AUI2
        WHERE c.SAB = ?
          AND p.SAB = ?
          AND c.SUPPRESS = 'N'
          AND p.SUPPRESS = 'N'
          AND c.CODE IS NOT NULL
          AND c.CODE != ''
        GROUP BY c.CODE
        ORDER BY c.CODE
        LIMIT 1
        """,
        [source, source],
    ).fetchone()
    return str(row[0]) if row else None


if __name__ == "__main__":
    raise SystemExit(main())
