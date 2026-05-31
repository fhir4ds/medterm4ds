#!/usr/bin/env python3
"""Sample source mappings and flag rows that need clinical review."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds import get_code_mappings
from medterm4ds.core.config import local_lite_config
from medterm4ds.core.normalize import normalize_source
from medterm4ds.engines.duckdb import LocalLiteEngine
from medterm4ds.services.inventory import iter_source_codes

DEFAULT_PAIRS = "ICD10CM:SNOMEDCT_US,LNC:SNOMEDCT_US,CPT:SNOMEDCT_US,HCPCS:SNOMEDCT_US"
REVIEW_STOPWORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "in",
    "of",
    "or",
    "the",
    "to",
    "with",
    "without",
}
BROAD_TERMS = {
    "assessment",
    "clinical",
    "disease",
    "finding",
    "findings",
    "history",
    "measurement",
    "procedure",
    "screening",
    "test",
    "therapy",
}


@dataclass(frozen=True)
class PairReview:
    source: str
    target_source: str
    sampled_codes: int
    mapping_rows: int
    elapsed_seconds: float
    match_types: dict[str, int]
    relationships: dict[str, int]
    review_flags: dict[str, int]
    examples_by_match_type: dict[str, list[dict[str, Any]]]
    flagged_examples: list[dict[str, Any]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/mnt/d/medterm/data/umls_local.duckdb")
    parser.add_argument("--pairs", default=DEFAULT_PAIRS)
    parser.add_argument("--per-source", type=int, default=100)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-results-per-code", type=int, default=20)
    parser.add_argument("--include-target-ancestors", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-target-descendants", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--memory-profile", default="low")
    parser.add_argument("--output-json", default="mapping_quality_report.json")
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

    pairs = _parse_pairs(args.pairs)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalLiteEngine(con, config=local_lite_config(args.memory_profile))
        reviews = [
            _review_pair(con, engine, source, target, args)
            for source, target in pairs
        ]
    finally:
        con.close()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "per_source": args.per_source,
        "max_depth": args.max_depth,
        "include_target_ancestors": args.include_target_ancestors,
        "include_target_descendants": args.include_target_descendants,
        "reviews": [asdict(review) for review in reviews],
    }
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({f"{review.source}->{review.target_source}": review.mapping_rows for review in reviews}, sort_keys=True))
    return 0


def _review_pair(con, engine: LocalLiteEngine, source: str, target: str, args) -> PairReview:
    start = time.perf_counter()
    codes = list(iter_source_codes(con, [source], limit=args.per_source))
    rows = get_code_mappings(
        codes,
        engine=engine,
        target_sources=[target],
        max_results_per_code=args.max_results_per_code,
        max_depth=args.max_depth,
        include_target_ancestors=args.include_target_ancestors,
        include_target_descendants=args.include_target_descendants,
    )
    match_types = Counter(row.match_type for row in rows)
    relationships = Counter(row.relationship for row in rows)
    review_flags: Counter[str] = Counter()
    examples_by_match_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    flagged_examples: list[dict[str, Any]] = []
    targets_per_code = Counter((row.source.source, row.source.code) for row in rows)

    for row in rows:
        record = row.to_dict()
        match_bucket = examples_by_match_type[row.match_type]
        if len(match_bucket) < 5:
            match_bucket.append(record)
        flags = _review_flags(record, targets_per_code[(row.source.source, row.source.code)])
        for flag in flags:
            review_flags[flag] += 1
        if flags and len(flagged_examples) < 25:
            flagged_examples.append({"flags": flags, **record})

    return PairReview(
        source=source,
        target_source=target,
        sampled_codes=len(codes),
        mapping_rows=len(rows),
        elapsed_seconds=time.perf_counter() - start,
        match_types=dict(sorted(match_types.items())),
        relationships=dict(sorted(relationships.items())),
        review_flags=dict(sorted(review_flags.items())),
        examples_by_match_type=dict(sorted(examples_by_match_type.items())),
        flagged_examples=flagged_examples,
    )


def _review_flags(row: dict[str, Any], targets_for_code: int) -> list[str]:
    flags: list[str] = []
    if row.get("match_type") != "same_cui":
        flags.append("hierarchy_or_expansion_mapping")
    if targets_for_code > 10:
        flags.append("many_targets_for_source_code")
    target_display = str(row.get("target_display") or "")
    if _is_broad_display(target_display):
        flags.append("broad_target_display")
    source_display = str(row.get("source_display") or "")
    if source_display and target_display and _token_overlap(source_display, target_display) == 0:
        flags.append("low_name_overlap")
    return flags


def _is_broad_display(name: str) -> bool:
    tokens = _tokens(name)
    return bool(tokens & BROAD_TERMS) and len(tokens) <= 3


def _token_overlap(left: str, right: str) -> int:
    return len(_tokens(left) & _tokens(right))


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.sub(r"[^a-z0-9]+", " ", value.lower()).split()
        if len(token) > 2 and token not in REVIEW_STOPWORDS
    }


def _parse_pairs(raw_pairs: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw_pair in raw_pairs.split(","):
        if not raw_pair.strip():
            continue
        try:
            source, target = raw_pair.split(":", 1)
        except ValueError as exc:
            raise SystemExit(f"Invalid pair {raw_pair!r}; expected SOURCE:TARGET") from exc
        pairs.append((normalize_source(source.strip()), normalize_source(target.strip())))
    if not pairs:
        raise SystemExit("At least one SOURCE:TARGET pair is required.")
    return pairs


if __name__ == "__main__":
    raise SystemExit(main())
