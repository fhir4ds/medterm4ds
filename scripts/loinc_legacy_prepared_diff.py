#!/usr/bin/env python3
"""LOINC patient-friendly drift probe (historical).

This script measured the drift between the legacy raw-mrrel LOINC resolver and
the prepared-cache resolver. It was used to decide which path to keep before
deleting legacy. Findings:

  - 88% agreement on a mixed sample of 200 LOINC codes
  - All 23 disagreements were in one direction: legacy returned the technical
    'original' name where prepared returned a real patient-friendly name via
    'broader' or 'snomed_fallback' (CHV / MEDLINEPLUS)
  - Legacy was missing the native parent walk tier and was less aggressive at
    SNOMED fallback
  - Conclusion: prepared was strictly more correct; legacy was deleted

Post-deletion status: the `--strategy force_legacy` path now raises
NotImplementedError because the legacy _resolve_loinc function and its
dispatch branch have been removed. The 24 drift cases that this probe
surfaced are pinned in tests/regression/fixtures/patient_friendly_verified.jsonl
so future regressions on the prepared path are caught.

The script is kept for historical context. To re-run the audit against a
future second resolver implementation, replace run_path()'s patching target.

Usage:
    python scripts/loinc_legacy_prepared_diff.py [--db PATH] [--sample N]
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds import CodeRef, get_patient_friendly_names
from medterm4ds.core.config import local_duckdb_config
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.duckdb._mixins._PatientFriendlyOps import _PatientFriendlyOps


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="/mnt/d/medterm4ds/data/umls_current.duckdb")
    p.add_argument("--sample", type=int, default=200, help="LOINC codes to probe")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-depth", type=int, default=5)
    p.add_argument("--memory-profile", default="balanced")
    p.add_argument("--output-csv", default="reports/loinc_drift.csv")
    p.add_argument(
        "--strategy",
        default="mixed",
        choices=("random", "measured_by", "mixed"),
        help="Sample selection: random LOINC codes / codes with measured_by / half-and-half",
    )
    return p.parse_args()


def load_sample(con, sample_size: int, strategy: str, seed: int) -> list[CodeRef]:
    """Pick LOINC codes to probe.

    `measured_by` and `mixed` over-sample codes with a 'measured_by' RELA
    because that's the relationship legacy includes but prepared drops —
    highest-signal divergence.
    """
    rng = random.Random(seed)

    measured_by_codes: list[str] = []
    if strategy in ("measured_by", "mixed"):
        rows = con.execute(
            """
            SELECT DISTINCT c.CODE
            FROM mrconso c
            JOIN mrrel r ON r.AUI1 = c.AUI AND r.RELA = 'measured_by'
            WHERE c.SAB = 'LNC'
              AND c.SUPPRESS = 'N'
              AND c.CODE IS NOT NULL
              AND c.CODE != ''
            """
        ).fetchall()
        measured_by_codes = [r[0] for r in rows]
        rng.shuffle(measured_by_codes)

    all_codes: list[str] = []
    if strategy in ("random", "mixed"):
        rows = con.execute(
            """
            SELECT DISTINCT CODE FROM mrconso
            WHERE SAB = 'LNC'
              AND SUPPRESS = 'N'
              AND CODE IS NOT NULL
              AND CODE != ''
            """
        ).fetchall()
        all_codes = [r[0] for r in rows]
        rng.shuffle(all_codes)

    if strategy == "measured_by":
        picked = measured_by_codes[:sample_size]
    elif strategy == "random":
        picked = all_codes[:sample_size]
    else:  # mixed
        half = sample_size // 2
        picked = measured_by_codes[:half] + all_codes[: sample_size - half]

    picked = picked[:sample_size]
    return [CodeRef(source="LNC", code=code) for code in picked]


def run_path(
    codes: list[CodeRef],
    engine: LocalDuckDBEngine,
    max_depth: int,
    *,
    force_legacy: bool,
) -> list[Any]:
    """Run patient-friendly resolution, forcing legacy or prepared path.

    Post-deletion note: legacy LOINC was removed (see module docstring). The
    force_legacy path will raise NotImplementedError when LOINC codes are in
    the input. This function is kept so the script can still run against
    non-LOINC sources or a future second resolver.
    """
    if force_legacy:
        original = _PatientFriendlyOps._has_patient_friendly_prepared_tables
        _PatientFriendlyOps._has_patient_friendly_prepared_tables = (
            lambda self, sources: False
        )
        try:
            return get_patient_friendly_names(codes, engine=engine, max_depth=max_depth)
        finally:
            _PatientFriendlyOps._has_patient_friendly_prepared_tables = original
    return get_patient_friendly_names(codes, engine=engine, max_depth=max_depth)


def distribution(results: list[Any]) -> tuple[Counter, Counter]:
    """Return (match_type counts, friendly_source counts) for a result list."""
    mt = Counter(r.match_type for r in results)
    fs = Counter(r.friendly_source for r in results)
    return mt, fs


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to {db_path} (read_only=True)...", flush=True)
    con = duckdb.connect(str(db_path), read_only=True)
    config = local_duckdb_config(args.memory_profile)
    engine = LocalDuckDBEngine(con, config=config)

    print("Preparing cache for LNC (one-time, ~30s)...", flush=True)
    engine.prepare_cache(["LNC"], create_indexes=False)

    print(
        f"Loading sample (strategy={args.strategy}, n={args.sample})...",
        flush=True,
    )
    codes = load_sample(con, args.sample, args.strategy, args.seed)
    print(f"  picked {len(codes)} LOINC codes", flush=True)

    print("\n=== LEGACY PATH ===", flush=True)
    t0 = time.perf_counter()
    legacy_results = run_path(codes, engine, args.max_depth, force_legacy=True)
    legacy_elapsed = time.perf_counter() - t0
    legacy_mt, legacy_fs = distribution(legacy_results)
    print(f"  {len(legacy_results)} results in {legacy_elapsed:.1f}s", flush=True)
    print(f"  match_type:      {dict(legacy_mt.most_common())}", flush=True)
    print(f"  friendly_source: {dict(legacy_fs.most_common())}", flush=True)

    print("\n=== PREPARED PATH ===", flush=True)
    t0 = time.perf_counter()
    prepared_results = run_path(codes, engine, args.max_depth, force_legacy=False)
    prepared_elapsed = time.perf_counter() - t0
    prepared_mt, prepared_fs = distribution(prepared_results)
    print(f"  {len(prepared_results)} results in {prepared_elapsed:.1f}s", flush=True)
    print(f"  match_type:      {dict(prepared_mt.most_common())}", flush=True)
    print(f"  friendly_source: {dict(prepared_fs.most_common())}", flush=True)

    print("\n=== DIFF ===", flush=True)
    legacy_by = {r.code.code: r for r in legacy_results}
    prepared_by = {r.code.code: r for r in prepared_results}

    agreements = 0
    name_only_diff = 0
    field_diff = 0
    missing = 0
    disagreements: list[tuple[str, Any, Any, str]] = []
    for ref in codes:
        l = legacy_by.get(ref.code)
        p = prepared_by.get(ref.code)
        if l is None or p is None:
            missing += 1
            disagreements.append((ref.code, l, p, "missing"))
            continue
        l_key = (l.friendly_source, l.match_type)
        p_key = (p.friendly_source, p.match_type)
        if l.name == p.name and l_key == p_key:
            agreements += 1
        elif l_key == p_key and l.name != p.name:
            name_only_diff += 1
            disagreements.append((ref.code, l, p, "name_only"))
        else:
            field_diff += 1
            disagreements.append((ref.code, l, p, "field_diff"))

    total = len(codes)
    pct = 100 * agreements / total if total else 0
    print(f"agreements:        {agreements}/{total} ({pct:.1f}%)")
    print(f"name-only diffs:   {name_only_diff}/{total}")
    print(f"field diffs:       {field_diff}/{total}")
    print(f"missing:           {missing}/{total}")
    print(
        f"timing:            legacy {legacy_elapsed:.2f}s "
        f"({len(codes)/max(legacy_elapsed, 0.001):.1f} codes/s) | "
        f"prepared {prepared_elapsed:.2f}s "
        f"({len(codes)/max(prepared_elapsed, 0.001):.1f} codes/s) | "
        f"speedup {legacy_elapsed/max(prepared_elapsed, 0.001):.1f}x"
    )

    if disagreements:
        # Bucket disagreements by match_type transition
        transitions: Counter = Counter()
        for _code, l, p, kind in disagreements:
            if kind == "missing":
                transitions["(missing)"] += 1
            else:
                transitions[f"{l.match_type} -> {p.match_type}"] += 1
        print("\nMatch-type transitions (legacy -> prepared):")
        for transition, count in transitions.most_common():
            print(f"  {count:4d}  {transition}")

        fs_transitions: Counter = Counter()
        for _code, l, p, kind in disagreements:
            if kind == "missing":
                continue
            fs_transitions[f"{l.friendly_source} -> {p.friendly_source}"] += 1
        if any(k != "(missing)" for k in transitions):
            print("\nFriendly-source transitions (legacy -> prepared):")
            for transition, count in fs_transitions.most_common():
                print(f"  {count:4d}  {transition}")

        # Write all disagreements to CSV
        with open(args.output_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "code",
                "legacy_name", "legacy_friendly_source", "legacy_match_type",
                "prepared_name", "prepared_friendly_source", "prepared_match_type",
                "diff_kind",
            ])
            for code, l, p, kind in disagreements:
                if kind == "missing":
                    w.writerow([
                        code,
                        getattr(l, "name", ""), getattr(l, "friendly_source", ""), getattr(l, "match_type", ""),
                        getattr(p, "name", ""), getattr(p, "friendly_source", ""), getattr(p, "match_type", ""),
                        "missing",
                    ])
                    continue
                w.writerow([
                    code,
                    l.name, l.friendly_source, l.match_type,
                    p.name, p.friendly_source, p.match_type,
                    kind,
                ])
        print(f"\nFull disagreement list written to {args.output_csv}")

        # Print first 20 disagreements for inspection
        print("\nFirst 20 disagreements:")
        for code, l, p, kind in disagreements[:20]:
            print(f"  {code}  ({kind})")
            if kind == "missing":
                print(f"    legacy={l!r} prepared={p!r}")
                continue
            print(f"    legacy:    name={l.name!r} source={l.friendly_source} type={l.match_type}")
            print(f"    prepared:  name={p.name!r} source={p.friendly_source} type={p.match_type}")
    else:
        print("\nNo disagreements — paths produce identical output on this sample.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
