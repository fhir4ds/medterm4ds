#!/usr/bin/env python3
"""Compare medterm4ds LocalLite patient-friendly output with medterm."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds import CodeRef, FriendlyNameResult
from medterm4ds.core.config import LOCAL_LITE_MEMORY_PROFILES, local_lite_config
from medterm4ds.engines.duckdb import LocalLiteEngine
from medterm4ds.engines.medterm_baseline import MedtermBulkBaselineEngine
from medterm4ds.services.inventory import DEFAULT_INVENTORY_SOURCES, normalize_sources
from medterm4ds.services.patient_friendly import get_patient_friendly_names

COMPARE_FIELDS = ("name", "friendly_source", "match_type", "match_depth")
KNOWN_OLD_MEDTERM_CPT_BUG = "medterm_cpt_hcpcs_friendly_name_keyerror"


@dataclass(frozen=True)
class CaseReport:
    source: str
    code: str
    status: str
    mismatch_fields: list[str]
    local: dict[str, Any] | None = None
    baseline: dict[str, Any] | None = None
    error: str | None = None
    known_issue: str | None = None
    elapsed_seconds: float = 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/mnt/d/medterm/data/umls_local.duckdb")
    parser.add_argument("--medterm-path", default="/mnt/d/medterm")
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_INVENTORY_SOURCES),
        help="Comma-separated source vocabularies.",
    )
    parser.add_argument("--per-source", type=int, default=10)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument(
        "--memory-profile",
        choices=tuple(sorted(LOCAL_LITE_MEMORY_PROFILES)),
        default="balanced",
    )
    parser.add_argument("--memory-limit", default=None)
    parser.add_argument("--temp-dir", default=None)
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--query-chunk-size", type=int, default=None)
    parser.add_argument(
        "--prepare-cache",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Prepare LocalLite temp cache before comparison.",
    )
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-md", default=None)
    parser.add_argument("--progress", action="store_true")
    return parser.parse_args()


def sample_codes(con, sources: Sequence[str], per_source: int) -> list[CodeRef]:
    if per_source < 1:
        raise ValueError("per_source must be at least 1")

    codes: list[CodeRef] = []
    for source in sources:
        rows = con.execute(
            """
            SELECT CODE
            FROM mrconso
            WHERE SUPPRESS = 'N'
              AND CODE IS NOT NULL
              AND CODE != ''
              AND SAB = ?
            GROUP BY CODE
            ORDER BY CODE
            LIMIT ?
            """,
            [source, per_source],
        ).fetchall()
        codes.extend(CodeRef(source=source, code=row[0]) for row in rows)
    return codes


def compare_codes(
    codes: Sequence[CodeRef],
    *,
    local_engine,
    baseline_engine,
    max_depth: int,
    progress: bool = False,
) -> list[CaseReport]:
    reports: list[CaseReport] = []
    for index, code in enumerate(codes, 1):
        start = time.perf_counter()
        if progress:
            print(f"[{index}/{len(codes)}] {code.source}:{code.code}", flush=True)
        try:
            local = get_patient_friendly_names([code], engine=local_engine, max_depth=max_depth)[0]
        except Exception as exc:
            reports.append(
                CaseReport(
                    source=code.source,
                    code=code.code,
                    status="local_error",
                    mismatch_fields=[],
                    error=_error_string(exc),
                    elapsed_seconds=time.perf_counter() - start,
                )
            )
            continue

        try:
            baseline = get_patient_friendly_names([code], engine=baseline_engine, max_depth=max_depth)[0]
        except Exception as exc:
            known_issue = _known_baseline_issue(code, exc)
            reports.append(
                CaseReport(
                    source=code.source,
                    code=code.code,
                    status="baseline_error_known" if known_issue else "baseline_error",
                    mismatch_fields=[],
                    local=_result_dict(local),
                    error=_error_string(exc),
                    known_issue=known_issue,
                    elapsed_seconds=time.perf_counter() - start,
                )
            )
            continue

        mismatch_fields = [
            field
            for field in COMPARE_FIELDS
            if getattr(local, field) != getattr(baseline, field)
        ]
        reports.append(
            CaseReport(
                source=code.source,
                code=code.code,
                status="match" if not mismatch_fields else "mismatch",
                mismatch_fields=mismatch_fields,
                local=_result_dict(local),
                baseline=_result_dict(baseline),
                elapsed_seconds=time.perf_counter() - start,
            )
        )
    return reports


def make_report(
    *,
    db_path: Path,
    medterm_path: Path,
    sources: Sequence[str],
    per_source: int,
    max_depth: int,
    reports: Sequence[CaseReport],
) -> dict[str, Any]:
    summary = Counter(report.status for report in reports)
    by_source: dict[str, Counter[str]] = {}
    for report in reports:
        by_source.setdefault(report.source, Counter())[report.status] += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "medterm_path": str(medterm_path),
        "sources": list(sources),
        "per_source": per_source,
        "max_depth": max_depth,
        "total": len(reports),
        "summary": dict(sorted(summary.items())),
        "by_source": {
            source: dict(sorted(counter.items()))
            for source, counter in sorted(by_source.items())
        },
        "cases": [asdict(report) for report in reports],
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# medterm4ds Patient-Friendly Parity Report",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- DB: `{report['db']}`",
        f"- medterm path: `{report['medterm_path']}`",
        f"- Sources: `{', '.join(report['sources'])}`",
        f"- Per source: `{report['per_source']}`",
        f"- Total cases: `{report['total']}`",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status, count in report["summary"].items():
        lines.append(f"| {status} | {count} |")

    lines.extend([
        "",
        "## By Source",
        "",
        "| Source | Status | Count |",
        "| --- | --- | ---: |",
    ])
    for source, counts in report["by_source"].items():
        for status, count in counts.items():
            lines.append(f"| {source} | {status} | {count} |")

    notable = [
        case for case in report["cases"]
        if case["status"] != "match"
    ]
    lines.extend([
        "",
        "## Non-Matches",
        "",
        "| Source | Code | Status | Fields | Known Issue | Error |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for case in notable[:100]:
        fields = ", ".join(case["mismatch_fields"])
        error = (case.get("error") or "").replace("|", "\\|")
        lines.append(
            f"| {case['source']} | `{case['code']}` | {case['status']} | "
            f"{fields} | {case.get('known_issue') or ''} | {error} |"
        )
    if len(notable) > 100:
        lines.append(f"| ... | ... | ... | ... | ... | {len(notable) - 100} more omitted |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    medterm_path = Path(args.medterm_path)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    if not medterm_path.exists():
        print(f"medterm path not found: {medterm_path}", file=sys.stderr)
        return 2

    try:
        import duckdb
    except ImportError as exc:
        print("DuckDB is required. Install medterm4ds[duckdb].", file=sys.stderr)
        raise SystemExit(2) from exc

    sources = normalize_sources(args.sources)
    config = local_lite_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        codes = sample_codes(con, sources, args.per_source)
        local_engine = LocalLiteEngine(con, config=config)
        if args.prepare_cache:
            local_engine.prepare_cache(sources, create_indexes=False)
        baseline_engine = MedtermBulkBaselineEngine(con, medterm_path=medterm_path)
        reports = compare_codes(
            codes,
            local_engine=local_engine,
            baseline_engine=baseline_engine,
            max_depth=args.max_depth,
            progress=args.progress,
        )
    finally:
        con.close()

    report = make_report(
        db_path=db_path,
        medterm_path=medterm_path,
        sources=sources,
        per_source=args.per_source,
        max_depth=args.max_depth,
        reports=reports,
    )
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.output_md:
        Path(args.output_md).write_text(markdown_report(report), encoding="utf-8")

    print(json.dumps(report["summary"], sort_keys=True))
    return 0


def _result_dict(result: FriendlyNameResult) -> dict[str, Any]:
    data = result.to_dict()
    data.pop("matched_via", None)
    return data


def _known_baseline_issue(code: CodeRef, exc: Exception) -> str | None:
    if code.source == "CPT" and "friendly_name" in _error_string(exc):
        return KNOWN_OLD_MEDTERM_CPT_BUG
    return None


def _error_string(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


if __name__ == "__main__":
    raise SystemExit(main())
