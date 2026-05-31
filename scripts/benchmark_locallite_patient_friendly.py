#!/usr/bin/env python3
"""Benchmark LocalLite patient-friendly resolution on a real UMLS DuckDB file."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import resource
import statistics
import sys
import time
from typing import Sequence

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds import CodeRef, get_patient_friendly_names
from medterm4ds.engines.duckdb import LocalLiteEngine

DEFAULT_SOURCES = (
    "ICD10CM",
    "ICD10PCS",
    "HCPCS",
    "SNOMEDCT_US",
    "RXNORM",
    "LNC",
    "CVX",
    "CPT",
)


@dataclass(frozen=True)
class BenchmarkResult:
    size_requested: int
    size_actual: int
    elapsed_seconds: float
    codes_per_second: float
    max_rss_mb: float
    match_types: dict[str, int]
    friendly_sources: dict[str, int]
    sources: dict[str, int]
    sample_names: list[dict[str, str]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="/mnt/d/medterm/data/umls_local.duckdb",
        help="Path to UMLS DuckDB database.",
    )
    parser.add_argument(
        "--sizes",
        default="100,1000,10000",
        help="Comma-separated total sample sizes.",
    )
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCES),
        help="Comma-separated source vocabularies.",
    )
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument(
        "--memory-limit",
        default="4GB",
        help="DuckDB memory limit. Use empty string to leave unset.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="Optional DuckDB worker thread count.",
    )
    parser.add_argument(
        "--query-chunk-size",
        type=int,
        default=5000,
        help="Maximum code count per internal LocalLite query chunk.",
    )
    parser.add_argument("--temp-dir", default=None)
    parser.add_argument(
        "--sample-mode",
        choices=("proportional", "balanced"),
        default="proportional",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to write benchmark results as JSON.",
    )
    parser.add_argument(
        "--prepare-cache",
        action="store_true",
        help="Create temp active-atom and relationship cache tables before benchmarking.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Print source/chunk progress during LocalLite resolution.",
    )
    parser.add_argument(
        "--no-cache-indexes",
        action="store_true",
        help="Skip indexes when --prepare-cache is used.",
    )
    return parser.parse_args()


def rss_mb() -> float:
    # Linux returns kilobytes. macOS returns bytes; this environment is Linux.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def get_source_counts(con, sources: Sequence[str]) -> dict[str, int]:
    placeholders = ",".join(["?"] * len(sources))
    rows = con.execute(
        f"""
        SELECT SAB, COUNT(DISTINCT CODE)
        FROM mrconso
        WHERE SUPPRESS = 'N'
          AND CODE IS NOT NULL
          AND CODE != ''
          AND SAB IN ({placeholders})
        GROUP BY SAB
        ORDER BY SAB
        """,
        list(sources),
    ).fetchall()
    return {source: int(count) for source, count in rows}


def allocate_sample_size(
    total: int,
    source_counts: dict[str, int],
    mode: str,
) -> dict[str, int]:
    if not source_counts:
        return {}
    if mode == "balanced":
        base = max(1, total // len(source_counts))
        allocations = {source: min(count, base) for source, count in source_counts.items()}
    else:
        total_available = sum(source_counts.values())
        allocations = {
            source: min(count, max(1, round(total * count / total_available)))
            for source, count in source_counts.items()
        }

    # Correct rounding drift while respecting source counts.
    while sum(allocations.values()) < total:
        candidates = [
            source for source, count in source_counts.items()
            if allocations.get(source, 0) < count
        ]
        if not candidates:
            break
        candidates.sort(key=lambda source: source_counts[source] - allocations[source], reverse=True)
        allocations[candidates[0]] += 1
    while sum(allocations.values()) > total:
        candidates = [source for source, value in allocations.items() if value > 0]
        if not candidates:
            break
        candidates.sort(key=lambda source: allocations[source], reverse=True)
        allocations[candidates[0]] -= 1
    return {source: value for source, value in allocations.items() if value > 0}


def load_sample(con, allocations: dict[str, int]) -> list[CodeRef]:
    codes: list[CodeRef] = []
    for source, limit in allocations.items():
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
            [source, limit],
        ).fetchall()
        codes.extend(CodeRef(source=source, code=row[0]) for row in rows)
    return codes


def run_one(con, engine: LocalLiteEngine, size: int, sources: Sequence[str], args) -> BenchmarkResult:
    if args.sample_mode == "balanced":
        per_source = max(1, round(size / len(sources)))
        source_counts = {source: per_source for source in sources}
    else:
        source_counts = get_source_counts(con, sources)
    allocations = allocate_sample_size(size, source_counts, args.sample_mode)
    codes = load_sample(con, allocations)

    start_rss = rss_mb()
    start = time.perf_counter()
    results = get_patient_friendly_names(codes, engine=engine, max_depth=args.max_depth)
    elapsed = time.perf_counter() - start
    end_rss = rss_mb()

    match_types = Counter(result.match_type for result in results)
    friendly_sources = Counter(result.friendly_source for result in results)
    result_sources = Counter(result.code.source for result in results)
    sample_names = [
        {
            "source": result.code.source,
            "code": result.code.code,
            "name": result.name,
            "match_type": result.match_type,
            "friendly_source": result.friendly_source,
        }
        for result in results[:5]
    ]

    return BenchmarkResult(
        size_requested=size,
        size_actual=len(results),
        elapsed_seconds=elapsed,
        codes_per_second=(len(results) / elapsed) if elapsed else 0.0,
        max_rss_mb=max(start_rss, end_rss),
        match_types=dict(sorted(match_types.items())),
        friendly_sources=dict(sorted(friendly_sources.items())),
        sources=dict(sorted(result_sources.items())),
        sample_names=sample_names,
    )


def print_result(result: BenchmarkResult) -> None:
    print(
        f"{result.size_actual:>8,d} codes | "
        f"{result.elapsed_seconds:>8.2f}s | "
        f"{result.codes_per_second:>9.1f} codes/s | "
        f"max RSS {result.max_rss_mb:>8.1f} MB"
    )
    print(f"  sources: {result.sources}", flush=True)
    print(f"  match_types: {result.match_types}", flush=True)


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    sizes = [int(part.strip()) for part in args.sizes.split(",") if part.strip()]
    sources = tuple(part.strip().upper() for part in args.sources.split(",") if part.strip())
    results: list[BenchmarkResult] = []

    def print_progress(message: str) -> None:
        print(f"  progress: {message} | max RSS {rss_mb():.1f} MB", flush=True)

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalLiteEngine(
            con,
            memory_limit=args.memory_limit or None,
            temp_directory=args.temp_dir,
            threads=args.threads,
            query_chunk_size=args.query_chunk_size,
            progress=print_progress if args.progress else None,
        )
        print(f"DB: {db_path}", flush=True)
        print(f"sources: {', '.join(sources)}", flush=True)
        print(f"sizes: {sizes}", flush=True)
        print(flush=True)
        if args.prepare_cache:
            start = time.perf_counter()
            start_rss = rss_mb()
            engine.prepare_cache(sources, create_indexes=not args.no_cache_indexes)
            elapsed = time.perf_counter() - start
            print(
                f"prepared cache in {elapsed:.2f}s | max RSS {max(start_rss, rss_mb()):.1f} MB",
                flush=True,
            )
            print(flush=True)
        for size in sizes:
            result = run_one(con, engine, size, sources, args)
            results.append(result)
            print_result(result)
    finally:
        con.close()

    if len(results) >= 2:
        rates = [result.codes_per_second for result in results if result.codes_per_second]
        if rates:
            print()
            print(f"median throughput: {statistics.median(rates):.1f} codes/s")

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.write_text(
            json.dumps([asdict(result) for result in results], indent=2),
            encoding="utf-8",
        )
        print(f"wrote {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
