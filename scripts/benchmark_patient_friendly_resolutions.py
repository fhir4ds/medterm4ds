#!/usr/bin/env python3
"""Benchmark patient-friendly lookup over materialized resolution rows."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import duckdb  # noqa: E402

from medterm4ds.engines.duckdb.prepared import (  # noqa: E402
    PATIENT_FRIENDLY_POLICY_VERSION,
    PREPARED_SCHEMA_VERSION,
    verify_mt4ds_schema,
)
from medterm4ds.services.schema_reporting import (  # noqa: E402
    missing_prepared_tables,
    report_db_role_metadata,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--db-role", default="unknown")
    parser.add_argument(
        "--benchmark",
        default=None,
        help="Optional CSV with source/code columns. Defaults to resolution rows.",
    )
    parser.add_argument(
        "--sources",
        default=None,
        help="Comma-separated sources. Applies to DB sampling and benchmark CSV input.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--chunk-size", "--query-chunk-size", dest="chunk_size", type=int, default=5000)
    parser.add_argument("--policy-version", default=PATIENT_FRIENDLY_POLICY_VERSION)
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--memory-limit", default=None)
    parser.add_argument("--output-json", default="reports/performance/patient_friendly_resolutions.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    sources = _parse_sources(args.sources)
    chunk_size = max(1, int(args.chunk_size))
    started = time.perf_counter()

    with duckdb.connect(str(db_path), read_only=True) as con:
        if args.threads is not None:
            con.execute(f"PRAGMA threads={int(args.threads)}")
        if args.memory_limit:
            con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")

        metadata = _metadata(con)
        schema_report = verify_mt4ds_schema(con)
        if args.benchmark:
            input_rows = _benchmark_inputs(Path(args.benchmark), sources, args.limit)
            summary = _benchmark_input_rows(
                con,
                input_rows,
                policy_version=args.policy_version,
                chunk_size=chunk_size,
            )
            input_mode = "benchmark_csv"
        else:
            summary = _benchmark_resolution_inventory(
                con,
                sources,
                policy_version=args.policy_version,
                limit=args.limit,
                chunk_size=chunk_size,
            )
            input_mode = "resolution_inventory"

    elapsed = time.perf_counter() - started
    db_role_metadata = report_db_role_metadata(args.db_role, schema_report)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "db_role": db_role_metadata["db_role"],
        "db_role_source": db_role_metadata["db_role_source"],
        "manifest_db_role": schema_report.get("db_role"),
        "source_archive": schema_report.get("source_archive"),
        "umls_release": metadata.get("umls_release"),
        "prepared_schema_version": metadata.get("prepared_schema_version"),
        "expected_prepared_schema_version": PREPARED_SCHEMA_VERSION,
        "patient_friendly_policy_version": args.policy_version,
        "manifest_patient_friendly_policy_version": metadata.get(
            "patient_friendly_policy_version"
        ),
        "prepared_tables": schema_report.get("prepared_tables"),
        "missing_prepared_tables": missing_prepared_tables(schema_report),
        "schema_errors": schema_report.get("errors", []),
        "input_mode": input_mode,
        "benchmark_path": args.benchmark,
        "sources": sources,
        "limit": args.limit,
        "chunk_size": chunk_size,
        "query_chunk_size": chunk_size,
        "threads": args.threads,
        "memory_limit": args.memory_limit,
        "elapsed_seconds": round(elapsed, 6),
        "rows_per_second": round((summary["inputs"] / elapsed) if elapsed else 0.0, 3),
        "summary": summary,
    }
    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "inputs": summary["inputs"],
                "hits": summary["hits"],
                "missing": summary["missing"],
                "elapsed_seconds": report["elapsed_seconds"],
                "rows_per_second": report["rows_per_second"],
            },
            sort_keys=True,
        )
    )
    return 0


def _parse_sources(value: str | None) -> list[str] | None:
    if not value:
        return None
    sources = [item.strip() for item in value.split(",") if item.strip()]
    return sources or None


def _metadata(con) -> dict[str, str | None]:
    keys = (
        "umls_release",
        "prepared_schema_version",
        "patient_friendly_policy_version",
    )
    result: dict[str, str | None] = {key: None for key in keys}
    try:
        rows = con.execute(
            """
            SELECT key, value
            FROM mt4ds.prepare_manifest
            WHERE key IN ('umls_release', 'prepared_schema_version', 'patient_friendly_policy_version')
            """
        ).fetchall()
    except Exception:
        return result
    for key, value in rows:
        if key in result and value is not None:
            result[str(key)] = str(value)
    return result

def _benchmark_inputs(
    path: Path,
    sources: list[str] | None,
    limit: int | None,
) -> list[tuple[str, str]]:
    wanted = set(sources or [])
    rows: list[tuple[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            source = str(row["source"])
            code = str(row["code"])
            if wanted and source not in wanted:
                continue
            rows.append((source, code))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def _benchmark_input_rows(
    con,
    input_rows: list[tuple[str, str]],
    *,
    policy_version: str,
    chunk_size: int,
) -> dict[str, int]:
    inputs = 0
    hits = 0
    missing = 0
    for offset in range(0, len(input_rows), chunk_size):
        chunk = input_rows[offset:offset + chunk_size]
        inputs += len(chunk)
        chunk_hits = _lookup_chunk(con, chunk, policy_version=policy_version)
        hits += chunk_hits
        missing += len(chunk) - chunk_hits
    return {"inputs": inputs, "hits": hits, "missing": missing}


def _benchmark_resolution_inventory(
    con,
    sources: list[str] | None,
    *,
    policy_version: str,
    limit: int | None,
    chunk_size: int,
) -> dict[str, int]:
    inputs = 0
    hits = 0
    missing = 0
    last_key: tuple[str, str] | None = None
    while limit is None or inputs < limit:
        remaining = chunk_size if limit is None else min(chunk_size, limit - inputs)
        if remaining <= 0:
            break
        rows = _resolution_inventory_chunk(
            con,
            sources,
            policy_version=policy_version,
            last_key=last_key,
            limit=remaining,
        )
        if not rows:
            break
        chunk = [(str(source), str(code)) for source, code in rows]
        inputs += len(chunk)
        chunk_hits = _lookup_chunk(con, chunk, policy_version=policy_version)
        hits += chunk_hits
        missing += len(chunk) - chunk_hits
        last_key = chunk[-1]
    return {"inputs": inputs, "hits": hits, "missing": missing}


def _resolution_inventory_chunk(
    con,
    sources: list[str] | None,
    *,
    policy_version: str,
    last_key: tuple[str, str] | None,
    limit: int,
) -> list[tuple[str, str]]:
    source_filter = ""
    params: list[object] = [policy_version]
    if sources:
        source_filter = "AND source IN (SELECT unnest(?))"
        params.append(list(sources))
    key_filter = ""
    if last_key is not None:
        key_filter = "AND (source > ? OR (source = ? AND code > ?))"
        params.extend([last_key[0], last_key[0], last_key[1]])
    params.append(limit)
    return con.execute(
        f"""
        SELECT source, code
        FROM mt4ds.patient_friendly_resolutions
        WHERE policy_version = ?
          {source_filter}
          {key_filter}
        ORDER BY source, code
        LIMIT ?
        """,
        params,
    ).fetchall()


def _lookup_chunk(
    con,
    chunk: list[tuple[str, str]],
    *,
    policy_version: str,
) -> int:
    if not chunk:
        return 0
    con.execute("CREATE TEMP TABLE _mt4ds_pf_benchmark_input (source VARCHAR, code VARCHAR)")
    try:
        con.executemany("INSERT INTO _mt4ds_pf_benchmark_input VALUES (?, ?)", chunk)
        row = con.execute(
            """
            SELECT COUNT(*)
            FROM _mt4ds_pf_benchmark_input i
            JOIN mt4ds.patient_friendly_resolutions r
              ON r.source = i.source
             AND r.code = i.code
             AND r.policy_version = ?
             AND r.prepared_schema_version = ?
            """,
            [policy_version, PREPARED_SCHEMA_VERSION],
        ).fetchone()
        return int(row[0] or 0)
    finally:
        con.execute("DROP TABLE IF EXISTS _mt4ds_pf_benchmark_input")


if __name__ == "__main__":
    raise SystemExit(main())
