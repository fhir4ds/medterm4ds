#!/usr/bin/env python3
"""Build targeted patient-friendly semantic review reports from a compare CSV."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_COMPARE = "reports/quality/patient_friendly_benchmark_shared_primitives_2026-06-08_compare.csv"
DEFAULT_OUTPUT_PREFIX = "reports/quality/patient_friendly_targeted_review_2026-06-08"

TRACKED_CODES = {
    ("ICD10CM", "S43"): "regression_s43_no_head_injury_jump",
    ("ICD10CM", "F14.959"): "regression_combo_chv_guard",
    ("CPT", "50580"): "regression_cpt_50580_nephroscopy",
    ("CPT", "11644"): "regression_cpt_generic_operation_guard",
    ("CPT", "24075"): "regression_cpt_generic_operation_guard",
    ("CPT", "80167"): "regression_cpt_generic_cpt4_guard",
    ("SNOMEDCT_US", "769135007"): "regression_snomed_drug_to_rxnorm",
    ("SNOMEDCT_US", "779280002"): "regression_snomed_drug_to_rxnorm",
    ("RXNORM", "235991"): "regression_rxnorm_pin_to_in",
    ("RXNORM", "1489922"): "regression_rxnorm_pin_to_in",
    ("CVX", "102"): "regression_cvx_combo_group",
    ("CVX", "104"): "regression_cvx_combo_group",
    ("CVX", "120"): "regression_cvx_combo_group",
}

CPT_GENERIC_TERMS = {
    "biochemical test",
    "cpt4",
    "current procedural terminology",
    "current procedural terminology (cpt)",
    "current procedural terminology concept",
    "operation",
    "operations",
    "operative procedure",
    "procedure",
    "surgery",
    "surgical procedure",
}

SNOMED_BROAD_CHV_TERMS = {
    "body part",
    "clinical finding",
    "disease",
    "disorder",
    "finding",
    "findings",
    "health problem",
    "medical condition",
    "procedure",
    "symptom",
}

DRUG_TEXT_MARKERS = (
    "clinical drug",
    "drug",
    "inhalant",
    "medicinal product",
    "oral",
    "pharmaceutical",
    "product containing",
    "suspension",
    "tablet",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-csv", default=DEFAULT_COMPARE)
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--max-per-focus", type=int, default=75)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    compare_path = Path(args.compare_csv)
    if not compare_path.exists():
        raise SystemExit(f"compare CSV not found: {compare_path}")

    with compare_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    review_rows = _build_review_rows(rows, max_per_focus=args.max_per_focus)
    output_prefix = Path(args.output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_prefix.with_suffix(".csv")
    json_path = output_prefix.with_suffix(".json")

    fieldnames = [
        "focus_area",
        "review_reason",
        "review_priority",
        *list(rows[0].keys() if rows else []),
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_rows)

    focus_counts = Counter(row["focus_area"] for row in review_rows)
    status_counts = Counter(row.get("status", "") for row in review_rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compare_csv": str(compare_path),
        "output_csv": str(csv_path),
        "total_compare_rows": len(rows),
        "review_rows": len(review_rows),
        "focus_counts": dict(sorted(focus_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "tracked_codes_present": {
            f"{source}:{code}": any(
                row.get("source") == source and row.get("code") == code
                for row in rows
            )
            for source, code in sorted(TRACKED_CODES)
        },
    }
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _build_review_rows(rows: list[dict[str, str]], *, max_per_focus: int) -> list[dict[str, str]]:
    by_focus: dict[str, list[dict[str, str]]] = defaultdict(list)
    tracked: list[dict[str, str]] = []

    for row in rows:
        for focus_area, reason, priority in _classify(row):
            out = {
                "focus_area": focus_area,
                "review_reason": reason,
                "review_priority": str(priority),
                **row,
            }
            if focus_area == "tracked_regression_examples":
                tracked.append(out)
            else:
                by_focus[focus_area].append(out)

    review_rows: list[dict[str, str]] = []
    for focus_area in sorted(by_focus):
        focus_rows = sorted(
            by_focus[focus_area],
            key=lambda row: (
                int(row["review_priority"]),
                row.get("status") != "mismatch",
                row.get("source", ""),
                row.get("code", ""),
            ),
        )
        review_rows.extend(focus_rows[:max_per_focus])

    seen = {(row["focus_area"], row.get("source"), row.get("code")) for row in review_rows}
    for row in sorted(tracked, key=lambda item: (item.get("source", ""), item.get("code", ""))):
        key = (row["focus_area"], row.get("source"), row.get("code"))
        if key not in seen:
            review_rows.append(row)
            seen.add(key)

    return review_rows


def _classify(row: dict[str, str]) -> list[tuple[str, str, int]]:
    source = row.get("source", "")
    code = row.get("code", "")
    status = row.get("status", "")
    match_type = row.get("medterm4ds_match_type", "")
    friendly_source = row.get("medterm4ds_friendly_source", "")
    name = row.get("medterm4ds_name", "")
    technical = row.get("medterm4ds_technical_name", "")
    match_depth = _int(row.get("medterm4ds_match_depth", ""))
    lowered_name = name.lower()
    lowered_technical = technical.lower()

    items: list[tuple[str, str, int]] = []
    tracked_reason = TRACKED_CODES.get((source, code))
    if tracked_reason:
        items.append(("tracked_regression_examples", tracked_reason, 0))

    if source == "CPT":
        if lowered_name in CPT_GENERIC_TERMS:
            items.append(("cpt_generic_or_too_broad", f"generic CPT name: {name}", 1))
        elif status == "mismatch" and match_type in {"broader", "snomed_fallback"}:
            items.append(("cpt_generic_or_too_broad", f"CPT mismatch via {match_type}", 2))
        elif match_depth >= 4 and match_type == "broader":
            items.append(("cpt_generic_or_too_broad", f"deep CPT broader match depth {match_depth}", 3))

    if source == "SNOMEDCT_US" and friendly_source == "CHV":
        if lowered_name in SNOMED_BROAD_CHV_TERMS:
            items.append(("snomed_broad_chv", f"broad CHV label: {name}", 1))
        elif match_depth >= 3 or "fallback" in match_type or match_type == "broader":
            items.append(("snomed_broad_chv", f"SNOMED CHV {match_type} depth {match_depth}", 2))

    if source == "LNC":
        if match_type in {"original", "first_axis"}:
            items.append(("lnc_original_first_axis", f"LNC {match_type}", 2))
        elif status == "mismatch":
            items.append(("lnc_original_first_axis", "LNC benchmark mismatch", 3))

    if source == "SNOMEDCT_US":
        is_drug_text = any(marker in lowered_technical for marker in DRUG_TEXT_MARKERS)
        if friendly_source == "RXNORM":
            items.append(("snomed_drug_rxnorm_routes", f"SNOMED routed to RxNorm via {match_type}", 1))
        elif is_drug_text:
            items.append(("snomed_drug_rxnorm_routes", "drug/product text did not route to RxNorm", 2))

    return items


def _int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
