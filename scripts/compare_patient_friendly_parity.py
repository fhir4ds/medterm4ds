#!/usr/bin/env python3
"""Compare medterm4ds local DuckDB patient-friendly output with medterm."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds import CodeRef, FriendlyNameResult
from medterm4ds.core.config import LOCAL_DUCKDB_MEMORY_PROFILES, local_duckdb_config
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.duckdb.prepared import verify_mt4ds_schema
from medterm4ds.engines.medterm_baseline import MedtermBulkBaselineEngine
from medterm4ds.services.inventory import DEFAULT_INVENTORY_SOURCES, normalize_sources
from medterm4ds.services.patient_friendly import get_patient_friendly_names
from medterm4ds.services.schema_reporting import (
    empty_schema_report_metadata,
    report_db_role_metadata,
    schema_report_metadata,
)

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
    classification: str | None = None
    classification_reason: str | None = None
    classification_key: str | None = None
    elapsed_seconds: float = 0.0


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
    parser.add_argument("--per-source", type=int, default=10)
    parser.add_argument(
        "--sample-mode",
        choices=("first", "tty"),
        default="first",
        help="Sampling strategy. `tty` samples across term types within each source.",
    )
    parser.add_argument(
        "--per-tty",
        type=int,
        default=2,
        help="Maximum codes per TTY when --sample-mode=tty.",
    )
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument(
        "--compare-batch-size",
        type=int,
        default=1,
        help="Batch size for local/baseline comparison. Falls back to one-code batches on batch errors.",
    )
    parser.add_argument(
        "--memory-profile",
        choices=tuple(sorted(LOCAL_DUCKDB_MEMORY_PROFILES)),
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
        help="Prepare local DuckDB temp cache before comparison.",
    )
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-md", default=None)
    parser.add_argument("--output-csv", default=None)
    parser.add_argument(
        "--classification-csv",
        default=None,
        help=(
            "Optional CSV with source,code,classification,classification_reason "
            "columns used to classify non-match rows."
        ),
    )
    parser.add_argument("--progress", action="store_true")
    return parser.parse_args()


def _schema_metadata(con) -> dict[str, Any]:
    try:
        report = verify_mt4ds_schema(con)
    except Exception:
        return empty_schema_report_metadata()
    return schema_report_metadata(report)


def _classification_rows(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    output: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        source = str(row.get("source") or "").strip()
        code = str(row.get("code") or "").strip()
        if not source or not code or source == "*" or code == "*":
            continue
        output[(source, code)] = {
            "classification": str(row.get("classification") or "").strip(),
            "classification_reason": str(row.get("classification_reason") or "").strip(),
            "classification_key": str(row.get("classification_key") or f"{source}:{code}").strip(),
        }
    return output


def _classify_reports(
    reports: Sequence[CaseReport],
    classifications: dict[tuple[str, str], dict[str, str]],
) -> list[CaseReport]:
    classified: list[CaseReport] = []
    for report in reports:
        if report.status == "match":
            classified.append(report)
            continue
        entry = classifications.get((report.source, report.code)) or {
            "classification": "unclassified",
            "classification_reason": "",
            "classification_key": f"{report.source}:{report.code}",
        }
        classified.append(
            replace(
                report,
                classification=entry["classification"],
                classification_reason=entry["classification_reason"],
                classification_key=entry["classification_key"],
            )
        )
    return classified


def sample_codes(
    con,
    sources: Sequence[str],
    per_source: int,
    *,
    sample_mode: str = "first",
    per_tty: int = 2,
) -> list[CodeRef]:
    if per_source < 1:
        raise ValueError("per_source must be at least 1")
    if per_tty < 1:
        raise ValueError("per_tty must be at least 1")

    codes: list[CodeRef] = []
    for source in sources:
        if sample_mode == "tty":
            rows = con.execute(
                """
                WITH canonical AS (
                    SELECT CODE, COALESCE(upper(TTY), '') AS TTY,
                           ROW_NUMBER() OVER (
                               PARTITION BY CODE
                               ORDER BY CASE upper(TTY)
                                            WHEN 'PT' THEN 0
                                            WHEN 'MH' THEN 1
                                            WHEN 'LN' THEN 2
                                            ELSE 3
                                        END,
                                        AUI
                           ) AS code_rn
                    FROM mrconso
                    WHERE SUPPRESS = 'N'
                      AND CODE IS NOT NULL
                      AND CODE != ''
                      AND SAB = ?
                ),
                ranked AS (
                    SELECT CODE, TTY,
                           ROW_NUMBER() OVER (PARTITION BY TTY ORDER BY CODE) AS tty_rn
                    FROM canonical
                    WHERE code_rn = 1
                )
                SELECT CODE
                FROM ranked
                WHERE tty_rn <= ?
                ORDER BY TTY, CODE
                LIMIT ?
                """,
                [source, per_tty, per_source],
            ).fetchall()
        else:
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
    batch_size: int = 1,
    progress: bool = False,
) -> list[CaseReport]:
    reports: list[CaseReport] = []
    batch_size = max(1, int(batch_size))
    code_batches = list(_chunks(list(codes), batch_size))
    offset = 0
    for batch_index, code_batch in enumerate(code_batches, 1):
        start = time.perf_counter()
        if progress:
            print(
                f"[batch {batch_index}/{len(code_batches)}] "
                f"{len(code_batch)} code(s), starting {code_batch[0].source}:{code_batch[0].code}",
                flush=True,
            )
        try:
            local_rows = get_patient_friendly_names(code_batch, engine=local_engine, max_depth=max_depth)
        except Exception as exc:
            reports.extend(
                CaseReport(
                    source=code.source,
                    code=code.code,
                    status="local_error",
                    mismatch_fields=[],
                    error=_error_string(exc),
                    elapsed_seconds=(time.perf_counter() - start) / len(code_batch),
                )
                for code in code_batch
            )
            offset += len(code_batch)
            continue

        try:
            baseline_rows = get_patient_friendly_names(code_batch, engine=baseline_engine, max_depth=max_depth)
        except Exception as exc:
            if batch_size > 1 and len(code_batch) > 1:
                reports.extend(
                    compare_codes(
                        code_batch,
                        local_engine=local_engine,
                        baseline_engine=baseline_engine,
                        max_depth=max_depth,
                        batch_size=1,
                        progress=progress,
                    )
                )
                offset += len(code_batch)
                continue
            known_issue = _known_baseline_issue(code_batch[0], exc)
            reports.append(
                CaseReport(
                    source=code_batch[0].source,
                    code=code_batch[0].code,
                    status="baseline_error_known" if known_issue else "baseline_error",
                    mismatch_fields=[],
                    local=_result_dict(local_rows[0]) if local_rows else None,
                    error=_error_string(exc),
                    known_issue=known_issue,
                    elapsed_seconds=time.perf_counter() - start,
                )
            )
            offset += len(code_batch)
            continue

        elapsed_each = (time.perf_counter() - start) / max(1, len(code_batch))
        local_lookup = _result_lookup(local_rows)
        baseline_lookup = _result_lookup(baseline_rows)
        for index, code in enumerate(code_batch, offset + 1):
            if progress and batch_size == 1:
                print(f"[{index}/{len(codes)}] {code.source}:{code.code}", flush=True)
            local = local_lookup.get((code.source, code.code))
            baseline = baseline_lookup.get((code.source, code.code))
            if local is None or baseline is None:
                reports.append(
                    CaseReport(
                        source=code.source,
                        code=code.code,
                        status="local_error" if local is None else "baseline_error",
                        mismatch_fields=[],
                        local=_result_dict(local) if local else None,
                        baseline=_result_dict(baseline) if baseline else None,
                        error="result missing from batch response",
                        elapsed_seconds=elapsed_each,
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
                    elapsed_seconds=elapsed_each,
                )
            )
        offset += len(code_batch)
    return reports


def make_report(
    *,
    db_path: Path,
    db_role: str,
    schema_metadata: dict[str, Any],
    medterm_path: Path,
    sources: Sequence[str],
    per_source: int,
    sample_mode: str,
    per_tty: int,
    max_depth: int,
    compare_batch_size: int,
    reports: Sequence[CaseReport],
) -> dict[str, Any]:
    summary = Counter(report.status for report in reports)
    classifications = Counter(
        report.classification for report in reports if report.classification
    )
    by_source: dict[str, Counter[str]] = {}
    for report in reports:
        by_source.setdefault(report.source, Counter())[report.status] += 1

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
        "sources": list(sources),
        "per_source": per_source,
        "sample_mode": sample_mode,
        "per_tty": per_tty,
        "max_depth": max_depth,
        "compare_batch_size": compare_batch_size,
        "total": len(reports),
        "summary": dict(sorted(summary.items())),
        "classifications": dict(sorted(classifications.items())),
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
        f"- DB role: `{report['db_role']}`",
        f"- UMLS release: `{report['umls_release']}`",
        f"- Prepared schema version: `{report['prepared_schema_version']}`",
        f"- Patient-friendly policy version: `{report['patient_friendly_policy_version']}`",
        f"- medterm path: `{report['medterm_path']}`",
        f"- Sources: `{', '.join(report['sources'])}`",
        f"- Per source: `{report['per_source']}`",
        f"- Sample mode: `{report['sample_mode']}`",
        f"- Compare batch size: `{report['compare_batch_size']}`",
        f"- Total cases: `{report['total']}`",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status, count in report["summary"].items():
        lines.append(f"| {status} | {count} |")

    if report.get("classifications"):
        lines.extend([
            "",
            "## Classifications",
            "",
            "| Classification | Count |",
            "| --- | ---: |",
        ])
        for classification, count in report["classifications"].items():
            lines.append(f"| {classification} | {count} |")

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
        "| Source | Code | Status | Fields | Classification | Known Issue | Error |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    for case in notable[:100]:
        fields = ", ".join(case["mismatch_fields"])
        error = (case.get("error") or "").replace("|", "\\|")
        lines.append(
            f"| {case['source']} | `{case['code']}` | {case['status']} | "
            f"{fields} | {case.get('classification') or ''} | "
            f"{case.get('known_issue') or ''} | {error} |"
        )
    if len(notable) > 100:
        lines.append(f"| ... | ... | ... | ... | ... | ... | {len(notable) - 100} more omitted |")
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
    classifications = (
        _classification_rows(Path(args.classification_csv))
        if args.classification_csv else {}
    )
    config = local_duckdb_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        db_metadata = _schema_metadata(con)
        codes = sample_codes(
            con,
            sources,
            args.per_source,
            sample_mode=args.sample_mode,
            per_tty=args.per_tty,
        )
        local_engine = LocalDuckDBEngine(con, config=config)
        if args.prepare_cache:
            local_engine.prepare_cache(sources, create_indexes=False)
        baseline_engine = MedtermBulkBaselineEngine(con, medterm_path=medterm_path)
        reports = compare_codes(
            codes,
            local_engine=local_engine,
            baseline_engine=baseline_engine,
            max_depth=args.max_depth,
            batch_size=args.compare_batch_size,
            progress=args.progress,
        )
        reports = _classify_reports(reports, classifications)
    finally:
        con.close()

    report = make_report(
        db_path=db_path,
        db_role=args.db_role,
        schema_metadata=db_metadata,
        medterm_path=medterm_path,
        sources=sources,
        per_source=args.per_source,
        sample_mode=args.sample_mode,
        per_tty=args.per_tty,
        max_depth=args.max_depth,
        compare_batch_size=args.compare_batch_size,
        reports=reports,
    )
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.output_md:
        Path(args.output_md).write_text(markdown_report(report), encoding="utf-8")
    if args.output_csv:
        write_csv_report(Path(args.output_csv), reports)

    print(json.dumps(report["summary"], sort_keys=True))
    return 0


def write_csv_report(path: Path, reports: Sequence[CaseReport]) -> None:
    fieldnames = [
        "source",
        "code",
        "status",
        "mismatch_fields",
        "classification",
        "classification_reason",
        "classification_key",
        "known_issue",
        "error",
        "local_name",
        "baseline_name",
        "local_friendly_source",
        "baseline_friendly_source",
        "local_match_type",
        "baseline_match_type",
        "local_match_depth",
        "baseline_match_depth",
        "elapsed_seconds",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for report in reports:
            local = report.local or {}
            baseline = report.baseline or {}
            writer.writerow(
                {
                    "source": report.source,
                    "code": report.code,
                    "status": report.status,
                    "mismatch_fields": ",".join(report.mismatch_fields),
                    "classification": report.classification or "",
                    "classification_reason": report.classification_reason or "",
                    "classification_key": report.classification_key or "",
                    "known_issue": report.known_issue or "",
                    "error": report.error or "",
                    "local_name": local.get("name", ""),
                    "baseline_name": baseline.get("name", ""),
                    "local_friendly_source": local.get("friendly_source", ""),
                    "baseline_friendly_source": baseline.get("friendly_source", ""),
                    "local_match_type": local.get("match_type", ""),
                    "baseline_match_type": baseline.get("match_type", ""),
                    "local_match_depth": local.get("match_depth", ""),
                    "baseline_match_depth": baseline.get("match_depth", ""),
                    "elapsed_seconds": f"{report.elapsed_seconds:.6f}",
                }
            )


def _chunks(values: Sequence[CodeRef], size: int) -> list[list[CodeRef]]:
    return [list(values[index:index + size]) for index in range(0, len(values), size)]


def _result_lookup(results: Sequence[FriendlyNameResult]) -> dict[tuple[str, str], FriendlyNameResult]:
    return {
        (result.code.source, result.code.code): result
        for result in results
    }


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
