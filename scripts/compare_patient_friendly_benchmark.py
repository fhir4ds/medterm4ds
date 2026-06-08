#!/usr/bin/env python3
"""Compare medterm4ds patient-friendly output against a benchmark CSV."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import duckdb  # noqa: E402

from medterm4ds import CodeRef  # noqa: E402
from medterm4ds.core.config import local_duckdb_config  # noqa: E402
from medterm4ds.engines.duckdb import LocalDuckDBEngine  # noqa: E402
from medterm4ds.engines.duckdb.prepared import verify_mt4ds_schema  # noqa: E402
from medterm4ds.services.lookup import get_code_infos  # noqa: E402
from medterm4ds.services.patient_friendly import get_patient_friendly_names  # noqa: E402
from medterm4ds.services.schema_reporting import (  # noqa: E402
    empty_schema_report_metadata,
    report_db_role_metadata,
    schema_report_metadata,
)

FIELDNAMES = [
    "source",
    "code",
    "status",
    "mismatch_fields",
    "benchmark_original_name",
    "benchmark_original_umls_name",
    "benchmark_name",
    "benchmark_friendly_source",
    "benchmark_match_type",
    "medterm4ds_name",
    "medterm4ds_friendly_source",
    "medterm4ds_match_type",
    "medterm4ds_match_depth",
    "medterm4ds_source_name",
    "medterm4ds_technical_name",
    "classification",
    "classification_reason",
    "classification_key",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default="/mnt/d/medterm/data/patient_friendly_benchmark.csv")
    parser.add_argument("--db", default="/mnt/d/medterm/data/umls_local.duckdb")
    parser.add_argument("--db-role", default="unknown")
    parser.add_argument("--output-prefix", default="reports/quality/patient_friendly_benchmark_2025ab")
    parser.add_argument("--sources", default=None, help="Comma-separated sources. Defaults to benchmark order.")
    parser.add_argument("--chunk-size", type=int, default=250)
    parser.add_argument(
        "--query-chunk-size",
        type=int,
        default=5000,
        help="DuckDB engine query chunk size; --chunk-size controls report input batches.",
    )
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--threads", type=int, default=32)
    parser.add_argument("--memory-limit", default="64GB")
    parser.add_argument("--temp-dir", default="/tmp")
    parser.add_argument(
        "--classification-csv",
        default=None,
        help=(
            "Optional CSV with source,code,classification,classification_reason "
            "columns used to classify mismatch rows."
        ),
    )
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    benchmark_path = Path(args.benchmark)
    db_path = Path(args.db)
    output_prefix = Path(args.output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)

    rows = _benchmark_rows(benchmark_path)
    classifications = _classification_rows(Path(args.classification_csv)) if args.classification_csv else {}
    source_order = _source_order(rows)
    if args.sources:
        wanted = {source.strip() for source in args.sources.split(",") if source.strip()}
        source_order = [source for source in source_order if source in wanted]
        rows = [row for row in rows if row["source"] in wanted]

    full_csv = output_prefix.with_name(output_prefix.name + "_compare.csv")
    diff_csv = output_prefix.with_name(output_prefix.name + "_differences.csv")
    summary_csv = output_prefix.with_name(output_prefix.name + "_differences_summary.csv")
    summary_json = output_prefix.with_name(output_prefix.name + "_compare_summary.json")

    config = local_duckdb_config(
        "fast",
        memory_limit=args.memory_limit,
        threads=args.threads,
        temp_directory=args.temp_dir,
        query_chunk_size=args.query_chunk_size,
    )

    started = time.perf_counter()
    summary = _EmptySummary()
    source_elapsed: dict[str, float] = defaultdict(float)

    with (
        duckdb.connect(str(db_path), read_only=True) as con,
        full_csv.open("w", encoding="utf-8", newline="") as full_handle,
        diff_csv.open("w", encoding="utf-8", newline="") as diff_handle,
    ):
        schema_metadata = _schema_metadata(con)
        engine = LocalDuckDBEngine(con, config=config)
        full_writer = csv.DictWriter(full_handle, fieldnames=FIELDNAMES)
        diff_writer = csv.DictWriter(diff_handle, fieldnames=FIELDNAMES)
        full_writer.writeheader()
        diff_writer.writeheader()

        for source in source_order:
            source_rows = [row for row in rows if row["source"] == source]
            source_started = time.perf_counter()
            for offset in range(0, len(source_rows), args.chunk_size):
                chunk_rows = source_rows[offset:offset + args.chunk_size]
                result_rows = _compare_chunk(
                    chunk_rows,
                    engine=engine,
                    max_depth=args.max_depth,
                    classifications=classifications,
                )
                for result_row in result_rows:
                    full_writer.writerow(result_row)
                    if result_row["status"] != "match":
                        diff_writer.writerow(result_row)
                    summary.add(result_row)
                full_handle.flush()
                diff_handle.flush()
                if args.progress:
                    done = min(offset + args.chunk_size, len(source_rows))
                    print(f"{source}: {done}/{len(source_rows)}", flush=True)
            source_elapsed[source] = time.perf_counter() - source_started
            if args.progress:
                print(f"{source}: {len(source_rows)} rows in {source_elapsed[source]:.3f}s", flush=True)

    summary_rows = summary.source_rows(source_elapsed=source_elapsed)
    _write_summary_csv(summary_csv, summary_rows)
    db_role_metadata = report_db_role_metadata(args.db_role, schema_metadata)
    summary_doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_path": str(benchmark_path),
        "db_path": str(db_path),
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
        "prepared_cache": False,
        "threads": args.threads,
        "memory_limit": args.memory_limit,
        "chunk_size": args.chunk_size,
        "query_chunk_size": args.query_chunk_size,
        "max_depth": args.max_depth,
        "compared_fields": ["name", "friendly_source", "match_type"],
        "total": summary.total,
        "matches": summary.matches,
        "mismatches": summary.mismatches,
        "missing_local": summary.missing_local,
        "classifications": dict(sorted(summary.classifications.items())),
        "match_rate": round(summary.matches / summary.total, 4) if summary.total else 0,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "sources": summary_rows,
        "outputs": {
            "full_csv": str(full_csv),
            "differences_csv": str(diff_csv),
            "summary_csv": str(summary_csv),
        },
    }
    summary_json.write_text(json.dumps(summary_doc, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: summary_doc[key]
                for key in (
                    "total",
                    "matches",
                    "mismatches",
                    "missing_local",
                    "classifications",
                    "match_rate",
                    "elapsed_seconds",
                )
            },
            indent=2,
        )
    )
    return 0


def _schema_metadata(con) -> dict[str, object]:
    try:
        report = verify_mt4ds_schema(con)
    except Exception:
        return empty_schema_report_metadata()
    return schema_report_metadata(report)


def _benchmark_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _classification_rows(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    output: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        source = str(row.get("source") or "").strip()
        code = str(row.get("code") or "").strip()
        if not source or not code:
            continue
        output[(source, code)] = {
            "classification": str(row.get("classification") or "").strip(),
            "classification_reason": str(row.get("classification_reason") or "").strip(),
            "classification_key": str(row.get("classification_key") or f"{source}:{code}").strip(),
        }
    return output


def _source_order(rows: list[dict[str, str]]) -> list[str]:
    order: list[str] = []
    for row in rows:
        source = row["source"]
        if source not in order:
            order.append(source)
    return order


def _compare_chunk(
    rows: list[dict[str, str]],
    *,
    engine: LocalDuckDBEngine,
    max_depth: int,
    classifications: dict[tuple[str, str], dict[str, str]],
) -> list[dict[str, str]]:
    codes = [CodeRef(source=row["source"], code=row["code"]) for row in rows]
    try:
        results = get_patient_friendly_names(codes, engine=engine, max_depth=max_depth)
        info_rows = get_code_infos(codes, engine=engine, resolve_mode="active_only")
    except Exception:
        if len(rows) == 1:
            return [_report_row(rows[0], None, None, classifications=classifications)]
        midpoint = len(rows) // 2
        return [
            *_compare_chunk(
                rows[:midpoint],
                engine=engine,
                max_depth=max_depth,
                classifications=classifications,
            ),
            *_compare_chunk(
                rows[midpoint:],
                engine=engine,
                max_depth=max_depth,
                classifications=classifications,
            ),
        ]
    lookup = {(result.code.source, result.code.code): result for result in results}
    info_lookup = {(row.code.source, row.code.code): row.name for row in info_rows if row is not None}
    return [
        _report_row(
            row,
            lookup.get((row["source"], row["code"])),
            info_lookup.get((row["source"], row["code"]), ""),
            classifications=classifications,
        )
        for row in rows
    ]


def _report_row(
    row: dict[str, str],
    result,
    original_name: str,
    *,
    classifications: dict[tuple[str, str], dict[str, str]],
) -> dict[str, str]:
    local = result.to_dict() if result is not None else {}
    if result is None:
        status = "missing_local"
        mismatch_fields = ["missing_local"]
    else:
        comparisons = {
            "name": (row["friendly_name"], local.get("name")),
            "friendly_source": (row["friendly_source"], local.get("friendly_source")),
            "match_type": (row["match_type"], local.get("match_type")),
        }
        mismatch_fields = [field for field, (expected, actual) in comparisons.items() if expected != actual]
        status = "match" if not mismatch_fields else "mismatch"
    classification = {"classification": "", "classification_reason": "", "classification_key": ""}
    if status != "match":
        classification = classifications.get((row["source"], row["code"])) or {
            "classification": "unclassified",
            "classification_reason": "",
            "classification_key": f"{row['source']}:{row['code']}",
        }
    return {
        "source": row["source"],
        "code": row["code"],
        "status": status,
        "mismatch_fields": ",".join(mismatch_fields),
        "benchmark_original_name": row.get("original_name", ""),
        "benchmark_original_umls_name": str(original_name),
        "benchmark_name": row.get("friendly_name", ""),
        "benchmark_friendly_source": row.get("friendly_source", ""),
        "benchmark_match_type": row.get("match_type", ""),
        "medterm4ds_name": str(local.get("name", "")),
        "medterm4ds_friendly_source": str(local.get("friendly_source", "")),
        "medterm4ds_match_type": str(local.get("match_type", "")),
        "medterm4ds_match_depth": str(local.get("match_depth", "")),
        "medterm4ds_source_name": str(local.get("technical_name", "")),
        "medterm4ds_technical_name": str(local.get("technical_name", "")),
        "classification": classification["classification"],
        "classification_reason": classification["classification_reason"],
        "classification_key": classification["classification_key"],
    }


class _EmptySummary:
    def __init__(self) -> None:
        self.total = 0
        self.matches = 0
        self.mismatches = 0
        self.missing_local = 0
        self.by_source: dict[str, Counter[str]] = defaultdict(Counter)
        self.pairs: dict[str, Counter[str]] = defaultdict(Counter)
        self.classifications: Counter[str] = Counter()

    def add(self, row: dict[str, str]) -> None:
        source = row["source"]
        status = row["status"]
        self.total += 1
        if status == "match":
            self.matches += 1
        elif status == "missing_local":
            self.missing_local += 1
        else:
            self.mismatches += 1
        if row.get("classification"):
            self.classifications[row["classification"]] += 1
        self.by_source[source]["total"] += 1
        self.by_source[source][status] += 1
        for field in row["mismatch_fields"].split(","):
            if field:
                self.by_source[source][field + "_differences"] += 1
        pair = f"{row['benchmark_friendly_source']}->{row['medterm4ds_friendly_source']}"
        self.pairs[source][pair] += 1

    def source_rows(self, *, source_elapsed: dict[str, float]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for source in sorted(self.by_source):
            counts = self.by_source[source]
            total = int(counts["total"])
            matches = int(counts["match"])
            rows.append(
                {
                    "source": source,
                    "total": total,
                    "matches": matches,
                    "mismatches": int(counts["mismatch"]),
                    "match_rate": round(matches / total, 4) if total else 0,
                    "name_differences": int(counts["name_differences"]),
                    "friendly_source_differences": int(counts["friendly_source_differences"]),
                    "match_type_differences": int(counts["match_type_differences"]),
                    "missing_local": int(counts["missing_local"]),
                    "top_friendly_source_pairs": "; ".join(
                        f"{pair}: {count}" for pair, count in self.pairs[source].most_common(20)
                    ),
                    "elapsed_seconds": round(source_elapsed.get(source, 0), 3),
                }
            )
        return rows


def _write_summary_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
