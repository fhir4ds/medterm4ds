#!/usr/bin/env python3
"""Run real-data bulk workflow trials and write a validation report."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import importlib.util
import json
import resource
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds.core.config import local_lite_config
from medterm4ds.engines.duckdb import LocalLiteEngine
from medterm4ds.outputs import default_checkpoint_path, write_checkpointed_rows
from medterm4ds.services.bulk import (
    iter_mapping_bulk,
    iter_patient_friendly_bulk,
)
from medterm4ds.services.inventory import count_source_codes, iter_source_codes, normalize_sources

DEFAULT_FRIENDLY_SOURCES = (
    "ICD10CM",
    "ICD10PCS",
    "SNOMEDCT_US",
    "RXNORM",
    "LNC",
    "CVX",
    "CPT",
    "HCPCS",
)
DEFAULT_MAPPING_WORKFLOWS = (
    ("icd10cm_to_snomed", ("ICD10CM",), ("SNOMEDCT_US",)),
    ("lnc_to_snomed", ("LNC",), ("SNOMEDCT_US",)),
    ("cpt_hcpcs_to_snomed", ("CPT", "HCPCS"), ("SNOMEDCT_US",)),
)


@dataclass(frozen=True)
class BulkTrial:
    name: str
    workflow: str
    sources: list[str]
    target_sources: list[str]
    input_codes_available: int
    input_codes_requested: int | None
    output_rows: int
    output_bytes: int
    elapsed_seconds: float
    rows_per_second: float
    max_rss_mb: float
    output_path: str
    status: str
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/mnt/d/medterm/data/umls_local.duckdb")
    parser.add_argument("--work-dir", default="validation_outputs")
    parser.add_argument("--output-json", default="bulk_validation_report.json")
    parser.add_argument("--limit", type=int, default=1000, help="Input code limit per workflow.")
    parser.add_argument("--full", action="store_true", help="Run full source inventories; ignores --limit.")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--fetch-size", type=int, default=10_000)
    parser.add_argument("--memory-profile", default="low")
    parser.add_argument("--memory-limit", default=None)
    parser.add_argument("--temp-dir", default=None)
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--query-chunk-size", type=int, default=None)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-results-per-code", type=int, default=50)
    parser.add_argument("--include-target-ancestors", action="store_true")
    parser.add_argument("--include-target-descendants", action="store_true")
    parser.add_argument("--prepare-cache", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--cache-indexes", action="store_true")
    parser.add_argument("--clean", action="store_true", help="Remove work-dir before running.")
    parser.add_argument("--progress", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    if importlib.util.find_spec("duckdb") is None:
        print("DuckDB is required. Install medterm4ds[duckdb].", file=sys.stderr)
        return 2

    work_dir = Path(args.work_dir)
    if args.clean:
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    trials = [
        _run_mapping_trial(db_path, work_dir, args, name, sources, targets)
        for name, sources, targets in DEFAULT_MAPPING_WORKFLOWS
    ]
    trials.append(_run_patient_friendly_trial(db_path, work_dir, args))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "full": bool(args.full),
        "limit": None if args.full else args.limit,
        "batch_size": args.batch_size,
        "max_depth": args.max_depth,
        "trials": [asdict(trial) for trial in trials],
    }
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({trial.name: trial.status for trial in trials}, sort_keys=True))
    return 0 if all(trial.status == "pass" for trial in trials) else 1


def _run_mapping_trial(db_path: Path, work_dir: Path, args, name, sources, targets) -> BulkTrial:
    import duckdb

    normalized_sources = normalize_sources(sources)
    normalized_targets = normalize_sources(targets)
    output_path = work_dir / f"{name}.jsonl"
    limit = None if args.full else args.limit
    start = time.perf_counter()
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        available = sum(count_source_codes(con, normalized_sources).values())
        config = local_lite_config(
            args.memory_profile,
            memory_limit=args.memory_limit,
            temp_directory=args.temp_dir,
            threads=args.threads,
            query_chunk_size=args.query_chunk_size,
        )
        engine = LocalLiteEngine(con, config=config, progress=print if args.progress else None)
        if args.prepare_cache:
            engine.prepare_cache(
                [*normalized_sources, *normalized_targets],
                create_indexes=args.cache_indexes,
            )
        codes = iter_source_codes(con, normalized_sources, fetch_size=args.fetch_size, limit=limit)
        rows = iter_mapping_bulk(
            codes,
            engine=engine,
            target_sources=normalized_targets,
            batch_size=args.batch_size,
            max_results_per_code=args.max_results_per_code,
            max_depth=args.max_depth,
            include_target_ancestors=args.include_target_ancestors,
            include_target_descendants=args.include_target_descendants,
        )
        position = write_checkpointed_rows(
            rows,
            output_path,
            output_format="jsonl",
            checkpoint_path=default_checkpoint_path(output_path),
            metadata={
                "command": "run_bulk_validation mapping",
                "db": str(db_path),
                "sources": list(normalized_sources),
                "target_sources": list(normalized_targets),
                "limit": limit,
            },
        )
        elapsed = time.perf_counter() - start
        return BulkTrial(
            name=name,
            workflow="mapping",
            sources=list(normalized_sources),
            target_sources=list(normalized_targets),
            input_codes_available=available,
            input_codes_requested=limit,
            output_rows=position.rows,
            output_bytes=_file_size(output_path),
            elapsed_seconds=elapsed,
            rows_per_second=(position.rows / elapsed) if elapsed else 0.0,
            max_rss_mb=_rss_mb(),
            output_path=str(output_path),
            status="pass",
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return BulkTrial(
            name=name,
            workflow="mapping",
            sources=list(normalized_sources),
            target_sources=list(normalized_targets),
            input_codes_available=0,
            input_codes_requested=limit,
            output_rows=0,
            output_bytes=_file_size(output_path),
            elapsed_seconds=elapsed,
            rows_per_second=0.0,
            max_rss_mb=_rss_mb(),
            output_path=str(output_path),
            status="fail",
            error=f"{exc.__class__.__name__}: {exc}",
        )
    finally:
        con.close()


def _run_patient_friendly_trial(db_path: Path, work_dir: Path, args) -> BulkTrial:
    import duckdb

    sources = normalize_sources(DEFAULT_FRIENDLY_SOURCES)
    output_path = work_dir / "patient_friendly_all_sources.jsonl"
    limit = None if args.full else args.limit
    start = time.perf_counter()
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        available = sum(count_source_codes(con, sources).values())
        config = local_lite_config(
            args.memory_profile,
            memory_limit=args.memory_limit,
            temp_directory=args.temp_dir,
            threads=args.threads,
            query_chunk_size=args.query_chunk_size,
        )
        engine = LocalLiteEngine(con, config=config, progress=print if args.progress else None)
        if args.prepare_cache:
            engine.prepare_cache(sources, create_indexes=args.cache_indexes)
        codes = iter_source_codes(con, sources, fetch_size=args.fetch_size, limit=limit)
        rows = iter_patient_friendly_bulk(
            codes,
            engine=engine,
            batch_size=args.batch_size,
            max_depth=5,
        )
        position = write_checkpointed_rows(
            rows,
            output_path,
            output_format="jsonl",
            checkpoint_path=default_checkpoint_path(output_path),
            metadata={
                "command": "run_bulk_validation patient-friendly",
                "db": str(db_path),
                "sources": list(sources),
                "limit": limit,
            },
        )
        elapsed = time.perf_counter() - start
        return BulkTrial(
            name="patient_friendly_all_sources",
            workflow="patient_friendly",
            sources=list(sources),
            target_sources=[],
            input_codes_available=available,
            input_codes_requested=limit,
            output_rows=position.rows,
            output_bytes=_file_size(output_path),
            elapsed_seconds=elapsed,
            rows_per_second=(position.rows / elapsed) if elapsed else 0.0,
            max_rss_mb=_rss_mb(),
            output_path=str(output_path),
            status="pass",
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return BulkTrial(
            name="patient_friendly_all_sources",
            workflow="patient_friendly",
            sources=list(sources),
            target_sources=[],
            input_codes_available=0,
            input_codes_requested=limit,
            output_rows=0,
            output_bytes=_file_size(output_path),
            elapsed_seconds=elapsed,
            rows_per_second=0.0,
            max_rss_mb=_rss_mb(),
            output_path=str(output_path),
            status="fail",
            error=f"{exc.__class__.__name__}: {exc}",
        )
    finally:
        con.close()


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


if __name__ == "__main__":
    raise SystemExit(main())
