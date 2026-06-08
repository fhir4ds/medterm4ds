#!/usr/bin/env python3
"""Materialize patient-friendly candidates and resolutions for prepared sources."""

from __future__ import annotations

import argparse
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
    prepare_mt4ds_schema,
    verify_mt4ds_schema,
)
from medterm4ds.services.patient_friendly_materialized import (  # noqa: E402
    materialize_patient_friendly_sources,
)
from medterm4ds.services.schema_reporting import (  # noqa: E402
    missing_prepared_tables,
    report_db_role_metadata,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="DuckDB database to update.")
    parser.add_argument(
        "--db-role",
        default="unknown",
        help="Database role label to include in the report.",
    )
    parser.add_argument(
        "--release-version",
        default=None,
        help="Optional UMLS release version to record when --prepare-schema is used.",
    )
    parser.add_argument(
        "--source-archive",
        default=None,
        help="Optional source archive path to record when --prepare-schema is used.",
    )
    parser.add_argument(
        "--sources",
        default=None,
        help="Comma-separated sources to materialize. Defaults to all prepared sources.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Materialize all sources from mt4ds.best_atoms. Equivalent to omitting --sources.",
    )
    parser.add_argument("--chunk-size", type=int, default=5000)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--policy-version", default=PATIENT_FRIENDLY_POLICY_VERSION)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--prepare-schema",
        action="store_true",
        help="Run prepare_mt4ds_schema before materialization.",
    )
    parser.add_argument(
        "--replace-schema",
        action="store_true",
        help="Use replace=True when --prepare-schema is set.",
    )
    parser.add_argument("--output-json", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    sources = _parse_sources(args.sources)
    if args.all:
        sources = None

    started = time.perf_counter()
    with duckdb.connect(str(db_path), read_only=False) as con:
        prepare_report = None
        if args.prepare_schema:
            prepare_report = prepare_mt4ds_schema(
                con,
                replace=bool(args.replace_schema),
                db_role=args.db_role,
                umls_release=args.release_version,
                source_archive=args.source_archive,
            )

        schema_report = verify_mt4ds_schema(con)
        if schema_report.get("errors"):
            report = _report(
                args=args,
                db_path=db_path,
                elapsed_seconds=time.perf_counter() - started,
                schema_report=schema_report,
                prepare_report=prepare_report,
                materialize_report=None,
                status="fail",
                error="; ".join(str(error) for error in schema_report["errors"]),
            )
            _write_report(report, args.output_json)
            print(json.dumps({"status": "fail", "error": report["error"]}, sort_keys=True))
            return 1

        try:
            materialize_report = materialize_patient_friendly_sources(
                sources,
                con,
                policy_version=args.policy_version,
                replace_existing=bool(args.replace),
                chunk_size=args.chunk_size,
                max_depth=args.max_depth,
            )
            schema_report = verify_mt4ds_schema(con)
        except Exception as exc:
            try:
                schema_report = verify_mt4ds_schema(con)
            except Exception:
                pass
            report = _report(
                args=args,
                db_path=db_path,
                elapsed_seconds=time.perf_counter() - started,
                schema_report=schema_report,
                prepare_report=prepare_report,
                materialize_report=None,
                status="fail",
                error=f"{exc.__class__.__name__}: {exc}",
            )
            _write_report(report, args.output_json)
            print(json.dumps({"status": "fail", "error": report["error"]}, sort_keys=True))
            return 1

    report = _report(
        args=args,
        db_path=db_path,
        elapsed_seconds=time.perf_counter() - started,
        schema_report=schema_report,
        prepare_report=prepare_report,
        materialize_report=materialize_report,
        status="pass",
        error=None,
    )
    _write_report(report, args.output_json)
    print(
        json.dumps(
            {
                "status": "pass",
                "source_count": materialize_report["source_count"],
                "inputs": materialize_report["inputs"],
                "resolutions": materialize_report["resolutions"],
                "friendly_resolutions": materialize_report["friendly_resolutions"],
                "original_fallbacks": materialize_report["original_fallbacks"],
                "missing_resolutions": materialize_report["missing_resolutions"],
                "resolution_coverage": materialize_report["resolution_coverage"],
                "elapsed_seconds": report["elapsed_seconds"],
            },
            sort_keys=True,
        )
    )
    return 0


def _parse_sources(value: str | None) -> list[str] | None:
    if value is None:
        return None
    sources = [item.strip() for item in value.split(",") if item.strip()]
    return sources or None


def _report(
    *,
    args: argparse.Namespace,
    db_path: Path,
    elapsed_seconds: float,
    schema_report: dict[str, object],
    prepare_report: dict[str, object] | None,
    materialize_report: dict[str, object] | None,
    status: str,
    error: str | None,
) -> dict[str, object]:
    db_role_metadata = report_db_role_metadata(args.db_role, schema_report)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "error": error,
        "db_path": str(db_path),
        "db_role": db_role_metadata["db_role"],
        "db_role_source": db_role_metadata["db_role_source"],
        "manifest_db_role": schema_report.get("db_role"),
        "source_archive": schema_report.get("source_archive"),
        "requested_umls_release": args.release_version,
        "requested_source_archive": args.source_archive,
        "prepared_schema_version": schema_report.get("prepared_schema_version"),
        "patient_friendly_policy_version": args.policy_version,
        "manifest_patient_friendly_policy_version": schema_report.get(
            "patient_friendly_policy_version"
        ),
        "umls_release": schema_report.get("umls_release"),
        "prepared_tables": schema_report.get("prepared_tables"),
        "missing_prepared_tables": missing_prepared_tables(schema_report),
        "schema_errors": schema_report.get("errors", []),
        "sources_requested": _parse_sources(args.sources),
        "all_sources": bool(args.all or not args.sources),
        "chunk_size": args.chunk_size,
        "max_depth": args.max_depth,
        "replace": bool(args.replace),
        "prepare_schema": bool(args.prepare_schema),
        "replace_schema": bool(args.replace_schema),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "prepare_report": prepare_report,
        "materialize_report": materialize_report,
    }


def _write_report(report: dict[str, object], output_json: str | None) -> None:
    if not output_json:
        return
    path = Path(output_json)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

if __name__ == "__main__":
    raise SystemExit(main())
