#!/usr/bin/env python3
"""Run patient-friendly resolution per source code system, output CSV with timing."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds import CodeRef, get_patient_friendly_names
from medterm4ds.core.config import local_duckdb_config
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.duckdb.prepared import verify_mt4ds_schema
from medterm4ds.services.schema_reporting import (
    empty_schema_report_metadata,
    report_db_role_metadata,
    schema_report_metadata,
)

SOURCES = (
    "ICD10CM",
    "ICD10PCS",
    "SNOMEDCT_US",
    "RXNORM",
    "LNC",
    "CPT",
    "HCPCS",
    "CVX",
)


@dataclass
class SourceTiming:
    source: str
    code_count: int
    elapsed_seconds: float
    codes_per_second: float
    match_types: dict[str, int]
    friendly_sources: dict[str, int]


CSV_COLUMNS = (
    "source",
    "code",
    "name",
    "friendly_source",
    "match_type",
    "match_depth",
    "technical_name",
    "source_tty",
    "cui",
    "aui",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/mnt/d/medterm4ds/data/umls_current.duckdb")
    parser.add_argument("--db-role", default="unknown")
    parser.add_argument("--per-source", type=int, default=500, help="Max codes per source. 0 = all.")
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--memory-profile", default="balanced")
    parser.add_argument("--output-csv", default="patient_friendly_review.csv")
    parser.add_argument("--output-json", default="patient_friendly_review_timing.json")
    parser.add_argument("--progress", action="store_true")
    return parser.parse_args()


def _schema_metadata(con) -> dict[str, object]:
    try:
        report = verify_mt4ds_schema(con)
    except Exception:
        return empty_schema_report_metadata()
    return schema_report_metadata(report)


def load_codes(con, source: str, limit: int) -> list[CodeRef]:
    sql = """
        SELECT CODE
        FROM mrconso
        WHERE SUPPRESS = 'N'
          AND CODE IS NOT NULL
          AND CODE != ''
          AND SAB = ?
        GROUP BY CODE
        ORDER BY CODE
    """
    params: list[object] = [source]
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    rows = con.execute(sql, params).fetchall()
    return [CodeRef(source=source, code=row[0]) for row in rows]


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    config = local_duckdb_config(args.memory_profile)

    def print_progress(message: str) -> None:
        if args.progress:
            print(f"  {message}", flush=True)

    con = duckdb.connect(str(db_path), read_only=True)
    timings: list[SourceTiming] = []
    all_rows: list[dict] = []

    try:
        db_metadata = _schema_metadata(con)
        engine = LocalDuckDBEngine(con, config=config, progress=print_progress)
        engine.prepare_cache(SOURCES, create_indexes=True)

        aggregate_start = time.perf_counter()

        for source in SOURCES:
            codes = load_codes(con, source, args.per_source)
            if not codes:
                print(f"{source}: no codes found, skipping")
                continue

            print(f"{source}: resolving {len(codes):,} codes...", flush=True)
            start = time.perf_counter()
            results = get_patient_friendly_names(codes, engine=engine, max_depth=args.max_depth)
            elapsed = time.perf_counter() - start

            match_types = Counter(r.match_type for r in results)
            friendly_sources = Counter(r.friendly_source for r in results)
            rate = len(results) / elapsed if elapsed > 0 else 0

            timing = SourceTiming(
                source=source,
                code_count=len(results),
                elapsed_seconds=round(elapsed, 3),
                codes_per_second=round(rate, 1),
                match_types=dict(sorted(match_types.items())),
                friendly_sources=dict(sorted(friendly_sources.items())),
            )
            timings.append(timing)

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
                })

            print(
                f"  {len(results):>8,} codes | {elapsed:>8.2f}s | {rate:>8.1f} codes/s | "
                f"match_types={dict(match_types)}"
            )

        aggregate_elapsed = time.perf_counter() - aggregate_start

    finally:
        con.close()

    # Write CSV
    csv_path = Path(args.output_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\nwrote {csv_path} ({len(all_rows):,} rows)")

    # Write timing JSON
    total_codes = sum(t.code_count for t in timings)
    total_elapsed = sum(t.elapsed_seconds for t in timings)
    aggregate_rate = total_codes / aggregate_elapsed if aggregate_elapsed > 0 else 0

    db_role_metadata = report_db_role_metadata(args.db_role, db_metadata)
    timing_report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "db_role": db_role_metadata["db_role"],
        "db_role_source": db_role_metadata["db_role_source"],
        "manifest_db_role": db_metadata.get("manifest_db_role"),
        "source_archive": db_metadata.get("source_archive"),
        "umls_release": db_metadata.get("umls_release"),
        "prepared_schema_version": db_metadata.get("prepared_schema_version"),
        "patient_friendly_policy_version": db_metadata.get("patient_friendly_policy_version"),
        "prepared_tables": db_metadata.get("prepared_tables"),
        "missing_prepared_tables": db_metadata.get("missing_prepared_tables"),
        "schema_errors": db_metadata.get("schema_errors"),
        "per_source": args.per_source,
        "max_depth": args.max_depth,
        "memory_profile": args.memory_profile,
        "sources": [asdict(t) for t in timings],
        "aggregate": {
            "total_codes": total_codes,
            "total_elapsed_seconds": round(total_elapsed, 3),
            "aggregate_elapsed_seconds": round(aggregate_elapsed, 3),
            "aggregate_codes_per_second": round(aggregate_rate, 1),
        },
    }
    json_path = Path(args.output_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(timing_report, indent=2), encoding="utf-8")
    print(f"wrote {json_path}")

    # Print summary
    print(f"\n{'='*60}")
    print(f"{'Source':<15} {'Codes':>10} {'Time':>10} {'Rate':>12}")
    print(f"{'-'*15} {'-'*10} {'-'*10} {'-'*12}")
    for t in timings:
        print(f"{t.source:<15} {t.code_count:>10,} {t.elapsed_seconds:>9.2f}s {t.codes_per_second:>10.1f}/s")
    print(f"{'-'*15} {'-'*10} {'-'*10} {'-'*12}")
    print(f"{'TOTAL':<15} {total_codes:>10,} {total_elapsed:>9.2f}s {aggregate_rate:>10.1f}/s")
    print(f"{'='*60}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
