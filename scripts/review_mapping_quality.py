#!/usr/bin/env python3
"""Sample source mappings and flag rows that need clinical review."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import csv
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
from medterm4ds.core.config import local_duckdb_config
from medterm4ds.core.normalize import normalize_source
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.duckdb.prepared import verify_mt4ds_schema
from medterm4ds.services.inventory import iter_source_codes
from medterm4ds.services.schema_reporting import (
    empty_schema_report_metadata,
    report_db_role_metadata,
    schema_report_metadata,
)

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
REVIEW_FLAG_COLUMNS = (
    "hierarchy_or_expansion_mapping",
    "many_targets_for_source_code",
    "broad_target_display",
    "low_name_overlap",
)
REVIEW_CSV_COLUMNS = (
    "review_source",
    "review_target_source",
    "flags",
    "flag_hierarchy_or_expansion_mapping",
    "flag_many_targets_for_source_code",
    "flag_broad_target_display",
    "flag_low_name_overlap",
    "source",
    "code",
    "source_display",
    "target_source",
    "target_code",
    "target_display",
    "relationship",
    "match_type",
    "match_depth",
    "source_cui",
    "target_cui",
    "source_aui",
    "target_aui",
    "target_tty",
    "matched_via_strategy",
    "matched_via_json",
    "review_notes",
)


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
    parser.add_argument("--db", default="/mnt/d/medterm4ds/data/umls_current.duckdb")
    parser.add_argument("--db-role", default="unknown")
    parser.add_argument("--pairs", default=DEFAULT_PAIRS)
    parser.add_argument("--per-source", type=int, default=100)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-results-per-code", type=int, default=20)
    parser.add_argument("--include-target-ancestors", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-target-descendants", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--memory-profile", default="low")
    parser.add_argument("--output-json", default="mapping_quality_report.json")
    parser.add_argument(
        "--output-csv",
        default="mapping_review_cases.csv",
        help="Write one flagged review case per row. Use an empty value to skip CSV output.",
    )
    parser.add_argument(
        "--max-json-flagged-examples",
        type=int,
        default=25,
        help="Maximum flagged examples to embed per source pair in the JSON summary.",
    )
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
        db_metadata = _schema_metadata(con)
        engine = LocalDuckDBEngine(con, config=local_duckdb_config(args.memory_profile))
        results = [
            _review_pair(con, engine, source, target, args)
            for source, target in pairs
        ]
        reviews = [review for review, _flagged_cases in results]
        flagged_cases = [
            case
            for _review, pair_flagged_cases in results
            for case in pair_flagged_cases
        ]
    finally:
        con.close()

    db_role_metadata = report_db_role_metadata(args.db_role, db_metadata)
    report = {
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
        "include_target_ancestors": args.include_target_ancestors,
        "include_target_descendants": args.include_target_descendants,
        "reviews": [asdict(review) for review in reviews],
    }
    if args.output_json:
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.output_csv:
        _write_review_csv(flagged_cases, Path(args.output_csv))
    print(json.dumps({f"{review.source}->{review.target_source}": review.mapping_rows for review in reviews}, sort_keys=True))
    return 0


def _schema_metadata(con) -> dict[str, object]:
    try:
        report = verify_mt4ds_schema(con)
    except Exception:
        return empty_schema_report_metadata()
    return schema_report_metadata(report)


def _review_pair(
    con,
    engine: LocalDuckDBEngine,
    source: str,
    target: str,
    args,
) -> tuple[PairReview, list[dict[str, Any]]]:
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
    flagged_cases: list[dict[str, Any]] = []
    targets_per_code = Counter((row.source.source, row.source.code) for row in rows)
    json_example_limit = max(0, int(args.max_json_flagged_examples))

    for row in rows:
        record = row.to_dict()
        match_bucket = examples_by_match_type[row.match_type]
        if len(match_bucket) < 5:
            match_bucket.append(record)
        flags = _review_flags(record, targets_per_code[(row.source.source, row.source.code)])
        for flag in flags:
            review_flags[flag] += 1
        if flags and len(flagged_examples) < json_example_limit:
            flagged_examples.append({"flags": flags, **record})
        if flags:
            flagged_cases.append(
                {
                    "review_source": source,
                    "review_target_source": target,
                    "flags": flags,
                    **record,
                }
            )

    return (
        PairReview(
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
        ),
        flagged_cases,
    )


def _write_review_csv(cases: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(REVIEW_CSV_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for case in cases:
            writer.writerow(_review_csv_row(case))


def _review_csv_row(case: dict[str, Any]) -> dict[str, Any]:
    flags = [str(flag) for flag in case.get("flags", [])]
    matched_via = case.get("matched_via")
    row = {
        key: case.get(key)
        for key in REVIEW_CSV_COLUMNS
        if key not in {"flags", "matched_via_strategy", "matched_via_json", "review_notes"}
        and not key.startswith("flag_")
    }
    row["flags"] = "|".join(flags)
    for flag in REVIEW_FLAG_COLUMNS:
        row[f"flag_{flag}"] = flag in flags
    if isinstance(matched_via, dict):
        row["matched_via_strategy"] = matched_via.get("strategy")
        row["matched_via_json"] = json.dumps(matched_via, sort_keys=True, separators=(",", ":"))
    elif matched_via is not None:
        row["matched_via_json"] = json.dumps(matched_via, sort_keys=True, separators=(",", ":"))
    row["review_notes"] = ""
    return row


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
