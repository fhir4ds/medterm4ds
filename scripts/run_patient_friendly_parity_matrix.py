#!/usr/bin/env python3
"""Run patient-friendly parity checks one source at a time."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds.core.config import LOCAL_DUCKDB_MEMORY_PROFILES
from medterm4ds.engines.duckdb.prepared import verify_mt4ds_schema
from medterm4ds.services.inventory import DEFAULT_INVENTORY_SOURCES, normalize_sources
from medterm4ds.services.schema_reporting import (
    empty_schema_report_metadata,
    report_db_role_metadata,
    schema_report_metadata,
)


@dataclass(frozen=True)
class SourceParityRun:
    source: str
    status: str
    total: int = 0
    summary: dict[str, int] | None = None
    classification_summary: dict[str, int] | None = None
    elapsed_seconds: float = 0.0
    sample_mode: str = "first"
    per_source: int = 0
    per_tty: int = 0
    returncode: int | None = None
    report_json: str | None = None
    report_md: str | None = None
    report_csv: str | None = None
    stdout: str = ""
    stderr_tail: str = ""
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/mnt/d/medterm4ds/data/umls_current.duckdb")
    parser.add_argument("--db-role", default="unknown")
    parser.add_argument("--medterm-path", default="/mnt/d/medterm")
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_INVENTORY_SOURCES),
        help="Comma-separated source vocabularies.",
    )
    parser.add_argument("--work-dir", default="reports/quality/patient_friendly_parity")
    parser.add_argument("--per-source", type=int, default=5)
    parser.add_argument("--rxnorm-per-source", type=int, default=20)
    parser.add_argument(
        "--sample-mode",
        choices=("auto", "first", "tty"),
        default="auto",
        help="`auto` uses TTY-stratified samples for RXNORM and first-code samples otherwise.",
    )
    parser.add_argument("--per-tty", type=int, default=2)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--compare-batch-size", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--memory-profile",
        choices=tuple(sorted(LOCAL_DUCKDB_MEMORY_PROFILES)),
        default="low",
    )
    parser.add_argument("--memory-limit", default=None)
    parser.add_argument("--temp-dir", default=None)
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--query-chunk-size", type=int, default=None)
    parser.add_argument(
        "--prepare-cache",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Prepare local DuckDB temp cache in each source comparison.",
    )
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument(
        "--classification-csv",
        default=None,
        help="Optional mismatch classification CSV forwarded to each source comparison.",
    )
    return parser.parse_args()


def _schema_metadata(db_path: Path) -> dict[str, Any]:
    try:
        import duckdb

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            report = verify_mt4ds_schema(con)
        finally:
            con.close()
    except Exception:
        return empty_schema_report_metadata()
    return schema_report_metadata(report)


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    medterm_path = Path(args.medterm_path)
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    sources = normalize_sources(args.sources)
    db_metadata = _schema_metadata(db_path)

    runs: list[SourceParityRun] = []
    for source in sources:
        run = run_source(
            source,
            args=args,
            db_path=db_path,
            medterm_path=medterm_path,
            work_dir=work_dir,
        )
        runs.append(run)
        if args.progress:
            print(f"{source}: {run.status} {run.summary or {}}", flush=True)
        if args.stop_on_failure and run.status in {"failed", "timeout"}:
            break

    report = make_report(
        db_path=db_path,
        db_role=args.db_role,
        schema_metadata=db_metadata,
        medterm_path=medterm_path,
        work_dir=work_dir,
        sources=sources,
        runs=runs,
        timeout_seconds=args.timeout_seconds,
    )
    write_reports(work_dir, report, runs)
    print(json.dumps(report["summary"], sort_keys=True))
    return 1 if any(run.status in {"failed", "timeout"} for run in runs) else 0


def run_source(
    source: str,
    *,
    args: argparse.Namespace,
    db_path: Path,
    medterm_path: Path,
    work_dir: Path,
) -> SourceParityRun:
    sample_mode = effective_sample_mode(source, args.sample_mode)
    per_source = args.rxnorm_per_source if source == "RXNORM" else args.per_source
    slug = source_slug(source)
    report_json = work_dir / f"{slug}.json"
    report_md = work_dir / f"{slug}.md"
    report_csv = work_dir / f"{slug}.csv"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "compare_patient_friendly_parity.py"),
        "--db",
        str(db_path),
        "--db-role",
        args.db_role,
        "--medterm-path",
        str(medterm_path),
        "--sources",
        source,
        "--per-source",
        str(per_source),
        "--sample-mode",
        sample_mode,
        "--per-tty",
        str(args.per_tty),
        "--compare-batch-size",
        str(args.compare_batch_size),
        "--max-depth",
        str(args.max_depth),
        "--memory-profile",
        args.memory_profile,
        "--output-json",
        str(report_json),
        "--output-md",
        str(report_md),
        "--output-csv",
        str(report_csv),
    ]
    if args.classification_csv:
        command.extend(["--classification-csv", args.classification_csv])
    if args.memory_limit:
        command.extend(["--memory-limit", args.memory_limit])
    if args.temp_dir:
        command.extend(["--temp-dir", args.temp_dir])
    if args.threads:
        command.extend(["--threads", str(args.threads)])
    if args.query_chunk_size:
        command.extend(["--query-chunk-size", str(args.query_chunk_size)])
    command.append("--prepare-cache" if args.prepare_cache else "--no-prepare-cache")

    start = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=parity_env(medterm_path),
            text=True,
            capture_output=True,
            check=False,
            timeout=args.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return SourceParityRun(
            source=source,
            status="timeout",
            elapsed_seconds=time.perf_counter() - start,
            sample_mode=sample_mode,
            per_source=per_source,
            per_tty=args.per_tty,
            stdout=exc.stdout or "",
            stderr_tail=tail_text(exc.stderr or ""),
            error=f"timed out after {args.timeout_seconds}s",
        )

    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        return SourceParityRun(
            source=source,
            status="failed",
            elapsed_seconds=elapsed,
            sample_mode=sample_mode,
            per_source=per_source,
            per_tty=args.per_tty,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr_tail=tail_text(result.stderr),
            error="comparison command failed",
        )

    try:
        source_report = json.loads(report_json.read_text(encoding="utf-8"))
    except Exception as exc:
        return SourceParityRun(
            source=source,
            status="failed",
            elapsed_seconds=elapsed,
            sample_mode=sample_mode,
            per_source=per_source,
            per_tty=args.per_tty,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr_tail=tail_text(result.stderr),
            error=f"could not read source report: {type(exc).__name__}: {exc}",
        )

    summary = {str(key): int(value) for key, value in source_report.get("summary", {}).items()}
    classification_summary = {
        str(key): int(value)
        for key, value in source_report.get("classifications", {}).items()
    }
    return SourceParityRun(
        source=source,
        status=classify_summary(summary),
        total=int(source_report.get("total", 0)),
        summary=summary,
        classification_summary=classification_summary,
        elapsed_seconds=elapsed,
        sample_mode=sample_mode,
        per_source=per_source,
        per_tty=args.per_tty,
        returncode=result.returncode,
        report_json=str(report_json),
        report_md=str(report_md),
        report_csv=str(report_csv),
        stdout=result.stdout.strip(),
        stderr_tail=tail_text(result.stderr),
    )


def effective_sample_mode(source: str, sample_mode: str) -> str:
    if sample_mode != "auto":
        return sample_mode
    return "tty" if source == "RXNORM" else "first"


def classify_summary(summary: dict[str, int]) -> str:
    if summary.get("local_error") or summary.get("baseline_error"):
        return "failed"
    if summary.get("mismatch") or summary.get("baseline_error_known"):
        return "review"
    return "pass"


def make_report(
    *,
    db_path: Path,
    db_role: str,
    schema_metadata: dict[str, Any],
    medterm_path: Path,
    work_dir: Path,
    sources: tuple[str, ...],
    runs: list[SourceParityRun],
    timeout_seconds: int,
) -> dict[str, Any]:
    statuses = Counter(run.status for run in runs)
    classifications: Counter[str] = Counter()
    for run in runs:
        classifications.update(run.classification_summary or {})
    source_statuses = {run.source: run.status for run in runs}
    db_role_metadata = report_db_role_metadata(db_role, schema_metadata)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "db_role": db_role_metadata["db_role"],
        "db_role_source": db_role_metadata["db_role_source"],
        "manifest_db_role": schema_metadata.get("manifest_db_role"),
        "source_archive": schema_metadata.get("source_archive"),
        "umls_release": schema_metadata.get("umls_release"),
        "prepared_schema_version": schema_metadata.get("prepared_schema_version"),
        "patient_friendly_policy_version": schema_metadata.get("patient_friendly_policy_version"),
        "prepared_tables": schema_metadata.get("prepared_tables"),
        "missing_prepared_tables": schema_metadata.get("missing_prepared_tables"),
        "schema_errors": schema_metadata.get("schema_errors"),
        "medterm_path": str(medterm_path),
        "work_dir": str(work_dir),
        "sources": list(sources),
        "timeout_seconds": timeout_seconds,
        "summary": dict(sorted(statuses.items())),
        "classifications": dict(sorted(classifications.items())),
        "source_statuses": source_statuses,
        "runs": [asdict(run) for run in runs],
    }


def write_reports(work_dir: Path, report: dict[str, Any], runs: list[SourceParityRun]) -> None:
    (work_dir / "index.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (work_dir / "index.md").write_text(markdown_report(report), encoding="utf-8")
    write_index_csv(work_dir / "index.csv", runs)


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Patient-Friendly Parity Matrix",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- DB: `{report['db']}`",
        f"- DB role: `{report['db_role']}`",
        f"- UMLS release: `{report['umls_release']}`",
        f"- Prepared schema version: `{report['prepared_schema_version']}`",
        f"- Patient-friendly policy version: `{report['patient_friendly_policy_version']}`",
        f"- medterm path: `{report['medterm_path']}`",
        f"- Work dir: `{report['work_dir']}`",
        f"- Timeout seconds: `{report['timeout_seconds']}`",
        "",
    ]
    if report.get("classifications"):
        lines.extend([
            "## Classifications",
            "",
            "| Classification | Count |",
            "| --- | ---: |",
        ])
        for classification, count in report["classifications"].items():
            lines.append(f"| {classification} | {count} |")
        lines.append("")
    lines.extend([
        "| Source | Status | Total | Match | Mismatch | Known Baseline Error | Baseline Error | Local Error | Seconds | CSV |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    for run in report["runs"]:
        summary = run.get("summary") or {}
        csv_path = run.get("report_csv") or ""
        lines.append(
            f"| {run['source']} | {run['status']} | {run.get('total', 0)} | "
            f"{summary.get('match', 0)} | {summary.get('mismatch', 0)} | "
            f"{summary.get('baseline_error_known', 0)} | {summary.get('baseline_error', 0)} | "
            f"{summary.get('local_error', 0)} | {run.get('elapsed_seconds', 0):.2f} | "
            f"`{csv_path}` |"
        )
    lines.append("")
    return "\n".join(lines)


def write_index_csv(path: Path, runs: list[SourceParityRun]) -> None:
    fieldnames = [
        "source",
        "status",
        "total",
        "match",
        "mismatch",
        "baseline_error_known",
        "baseline_error",
        "local_error",
        "classifications",
        "elapsed_seconds",
        "sample_mode",
        "per_source",
        "report_csv",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for run in runs:
            summary = run.summary or {}
            writer.writerow(
                {
                    "source": run.source,
                    "status": run.status,
                    "total": run.total,
                    "match": summary.get("match", 0),
                    "mismatch": summary.get("mismatch", 0),
                    "baseline_error_known": summary.get("baseline_error_known", 0),
                    "baseline_error": summary.get("baseline_error", 0),
                    "local_error": summary.get("local_error", 0),
                    "classifications": json.dumps(
                        run.classification_summary or {},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "elapsed_seconds": f"{run.elapsed_seconds:.6f}",
                    "sample_mode": run.sample_mode,
                    "per_source": run.per_source,
                    "report_csv": run.report_csv or "",
                    "error": run.error or "",
                }
            )


def parity_env(medterm_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    prefix = f"{ROOT / 'src'}:{medterm_path / 'src'}"
    env["PYTHONPATH"] = f"{prefix}:{existing}" if existing else prefix
    return env


def source_slug(source: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")


def tail_text(text: str, max_chars: int = 2000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


if __name__ == "__main__":
    raise SystemExit(main())
