#!/usr/bin/env python3
"""Produce three views of CCSR → ICD-10-CM code mappings.

Outputs (all written from one run, JOINable on ``ccsr_category``):

1. **Compact** (``ccsr_canonical_icd10_compact.csv``) — ``optimize_codes``
   output: include + exclude rules. ~49K rows, 0.4% have excludes. Most
   concise; closest to FHIR ValueSet ``compose.include`` shape.

2. **Inclusions** (``ccsr_canonical_icd10.csv``, default) — same roll-up
   logic as compact, but rules with excludes are expanded to their covered
   leaf codes. ~62K rows, zero excludes. Pure inclusion list; simplest for
   downstream SQL.

3. **Flat** (``ccsr_icd10_flat.csv``) — every ICD-10-CM billable code
   classified under each CCSR. No rollup, no optimize step. ~86K rows.
   The raw AHRQ classification as it ships in UMLS.

Usage::

  PYTHONPATH=src python3 scripts/ccsr_canonical_icd10.py \\
      --db /mnt/d/medterm4ds/data/umls_current.duckdb

Disable any output by passing an empty path (``--output-flat ""``).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from medterm4ds.core.config import local_duckdb_config
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.optimize import optimize_codes

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_OUTPUT_FLAT = Path("reports/fhir4px/ccsr_icd10_flat.csv")
DEFAULT_OUTPUT_INCLUSIONS = Path("reports/fhir4px/ccsr_canonical_icd10.csv")
DEFAULT_OUTPUT_COMPACT = Path("reports/fhir4px/ccsr_canonical_icd10_compact.csv")

CSV_COLUMNS = (
    "ccsr_category",
    "ccsr_category_display",
    "icd10_code",
    "icd10_display",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument(
        "--output-flat",
        default=str(DEFAULT_OUTPUT_FLAT),
        help=f"Flat output (every ICD-10 code per CCSR, no rollup). Default: {DEFAULT_OUTPUT_FLAT}",
    )
    p.add_argument(
        "--output-inclusions",
        default=str(DEFAULT_OUTPUT_INCLUSIONS),
        help=f"Inclusions-only output (rolled up, no excludes). Default: {DEFAULT_OUTPUT_INCLUSIONS}",
    )
    p.add_argument(
        "--output-compact",
        default=str(DEFAULT_OUTPUT_COMPACT),
        help=f"Compact output (rolled up, with excludes). Default: {DEFAULT_OUTPUT_COMPACT}",
    )
    p.add_argument("--memory-profile", default="fast", choices=("fast", "balanced", "low"))
    p.add_argument(
        "--ccsr-codes",
        default="",
        help="Comma-separated subset of CCSR categories (default: all). For smoke testing.",
    )
    p.add_argument(
        "--flat-only",
        action="store_true",
        help="Skip optimize_codes entirely. Produces only the flat output in seconds.",
    )
    p.add_argument("--progress", action="store_true")
    return p.parse_args()


def load_ccsr_to_icd10(con) -> dict[str, tuple[str, list[str]]]:
    """Map every CCSR category to its list of billable ICD-10-CM codes.

    Uses ``RELA = 'classifies'`` in umls.mrrel — every AHRQ classification
    edge (1..N per ICD-10 code). Filters ICD-10-CM side to TTY='PT' so HT/ET
    tabular category headers don't pollute the roll-up.
    """
    rows = con.execute(
        """
        SELECT ccsr.CODE AS ccsr_code,
               ccsr.STR AS ccsr_display,
               icd.CODE AS icd10_code
        FROM umls.mrconso icd
        JOIN umls.mrrel r
          ON r.AUI1 = icd.AUI
         AND r.RELA = 'classifies'
        JOIN umls.mrconso ccsr
          ON ccsr.AUI = r.AUI2
         AND ccsr.SAB = 'CCSR_ICD10CM'
        WHERE icd.SAB = 'ICD10CM'
          AND icd.SUPPRESS = 'N'
          AND icd.TTY = 'PT'
          AND icd.CODE IS NOT NULL
          AND icd.CODE != ''
        ORDER BY ccsr.CODE, icd.CODE
        """
    ).fetchall()

    out: dict[str, tuple[str, list[str]]] = {}
    for ccsr_code, ccsr_display, icd10_code in rows:
        if ccsr_code not in out:
            out[ccsr_code] = (ccsr_display, [])
        out[ccsr_code][1].append(icd10_code)
    return out


def load_all_icd10_displays(con, codes: set[str]) -> dict[str, str]:
    """Bulk-load display strings for many ICD-10 codes at once.

    Prefers PT (billable), falls back to HT (hierarchical category like
    "C00 Malignant neoplasm of lip"), then any non-suppressed row. One
    SQL query instead of N.
    """
    if not codes:
        return {}
    out: dict[str, str] = {}
    chunk = 1000
    code_list = sorted(codes)
    for i in range(0, len(code_list), chunk):
        batch = code_list[i:i + chunk]
        placeholders = ", ".join("?" for _ in batch)
        rows = con.execute(
            f"""
            SELECT CODE, STR, TTY
            FROM umls.mrconso
            WHERE SAB='ICD10CM' AND SUPPRESS='N' AND CODE IN ({placeholders})
            """,
            batch,
        ).fetchall()
        for code, str_val, tty in rows:
            # First PT wins; else first HT; else any
            if code not in out:
                out[code] = str_val
            elif tty == "PT" and out.get(f"__tty__{code}") != "PT":
                out[code] = str_val
            # Track which TTY we kept (cheap dict prefix)
            # (Simpler: re-query is rare; just prefer PT in single pass.)
        # Simpler: do one pass per TTY preference
    # Refine: ensure PT wins where available
    pt_rows = con.execute(
        f"""
        SELECT CODE, STR FROM umls.mrconso
        WHERE SAB='ICD10CM' AND SUPPRESS='N' AND TTY='PT'
          AND CODE IN (SELECT unnest(?))
        """,
        [code_list],
    ).fetchall() if False else None  # placeholder; using simpler approach below
    # The above got messy; let's just do one clean query preferring PT.
    return _load_displays_prefer_pt(con, code_list)


def _load_displays_prefer_pt(con, code_list: list[str]) -> dict[str, str]:
    """One query: prefer PT, fall back to HT, then any."""
    out: dict[str, str] = {}
    chunk = 1000
    for i in range(0, len(code_list), chunk):
        batch = code_list[i:i + chunk]
        placeholders = ", ".join("?" for _ in batch)
        rows = con.execute(
            f"""
            SELECT CODE, STR, TTY FROM (
                SELECT CODE, STR, TTY,
                       ROW_NUMBER() OVER (
                           PARTITION BY CODE
                           ORDER BY CASE TTY WHEN 'PT' THEN 0 WHEN 'HT' THEN 1 ELSE 2 END
                       ) AS rn
                FROM umls.mrconso
                WHERE SAB='ICD10CM' AND SUPPRESS='N'
                  AND CODE IN ({placeholders})
            ) WHERE rn = 1
            """,
            batch,
        ).fetchall()
        for code, str_val, tty in rows:
            out[code] = str_val
    return out


class _NullWriter:
    """No-op CSV writer for disabled outputs."""
    def writerow(self, row): pass
    def writeheader(self): pass


_display_cache: dict[str, str] = {}


def _prime_display_cache(con, codes: set[str]) -> None:
    """Bulk-load displays for all codes at once (one query, not N).

    Prefers PT (billable), falls back to HT (hierarchical category), then
    any non-suppressed row.
    """
    if not codes:
        return
    code_list = sorted(codes)
    chunk = 1000
    for i in range(0, len(code_list), chunk):
        batch = code_list[i:i + chunk]
        placeholders = ", ".join("?" for _ in batch)
        rows = con.execute(
            f"""
            SELECT CODE, STR FROM (
                SELECT CODE, STR,
                       ROW_NUMBER() OVER (
                           PARTITION BY CODE
                           ORDER BY CASE TTY WHEN 'PT' THEN 0 WHEN 'HT' THEN 1 ELSE 2 END
                       ) AS rn
                FROM umls.mrconso
                WHERE SAB='ICD10CM' AND SUPPRESS='N'
                  AND CODE IN ({placeholders})
            ) WHERE rn = 1
            """,
            batch,
        ).fetchall()
        for code, str_val in rows:
            _display_cache[code] = str_val


def _lookup_display(con, code: str) -> str:
    """Return display for an ICD-10-CM code. Call _prime_display_cache first
    for bulk efficiency."""
    if code in _display_cache:
        return _display_cache[code]
    r = con.execute(
        "SELECT STR FROM umls.mrconso WHERE SAB='ICD10CM' AND CODE=? AND SUPPRESS='N' ORDER BY TTY LIMIT 1",
        [code],
    ).fetchone()
    _display_cache[code] = r[0] if r else ""
    return _display_cache[code]


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    output_flat = Path(args.output_flat) if args.output_flat else None
    output_inclusions = Path(args.output_inclusions) if args.output_inclusions and not args.flat_only else None
    output_compact = Path(args.output_compact) if args.output_compact and not args.flat_only else None
    for p in (output_flat, output_inclusions, output_compact):
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)

    config = local_duckdb_config(args.memory_profile)
    import duckdb
    con = duckdb.connect(str(db_path), read_only=True)

    print(f"Loading CCSR → ICD-10-CM (TTY='PT') classifications...", file=sys.stderr)
    t0 = time.perf_counter()
    ccsr_map = load_ccsr_to_icd10(con)
    print(
        f"  {len(ccsr_map):,} CCSR categories covering "
        f"{sum(len(c) for _, c in ccsr_map.values()):,} ICD-10 codes "
        f"in {time.perf_counter()-t0:.1f}s",
        file=sys.stderr,
    )

    if args.ccsr_codes:
        wanted = {c.strip() for c in args.ccsr_codes.split(",") if c.strip()}
        ccsr_map = {k: v for k, v in ccsr_map.items() if k in wanted}
        print(f"  filtered to {len(ccsr_map)} CCSR codes via --ccsr-codes", file=sys.stderr)

    # Bulk-load displays for every ICD-10 code we'll touch (much faster than
    # N individual queries).
    all_icd10_codes = {c for _, codes in ccsr_map.values() for c in codes}
    print(f"  bulk-loading displays for {len(all_icd10_codes):,} ICD-10 codes...", file=sys.stderr)
    t_disp = time.perf_counter()
    _prime_display_cache(con, all_icd10_codes)
    print(f"  done in {time.perf_counter()-t_disp:.1f}s", file=sys.stderr)

    # ---- 1. FLAT (every code per CCSR, no rollup) ----
    if output_flat:
        print(f"\nWriting flat output → {output_flat}", file=sys.stderr)
        t_flat = time.perf_counter()
        with output_flat.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for ccsr_code, (ccsr_display, icd10_codes) in sorted(ccsr_map.items()):
                for icd10_code in sorted(set(icd10_codes)):
                    writer.writerow({
                        "ccsr_category": ccsr_code,
                        "ccsr_category_display": ccsr_display,
                        "icd10_code": icd10_code,
                        "icd10_display": _lookup_display(con, icd10_code),
                    })
        print(f"  done in {time.perf_counter()-t_flat:.1f}s", file=sys.stderr)

    if args.flat_only:
        print(f"\n--flat-only set; skipping optimize step.", file=sys.stderr)
        return 0

    # ---- 2 + 3. COMPACT + INCLUSIONS (require optimize_codes) ----
    engine = LocalDuckDBEngine(con, config=config)
    engine.prepare_cache(["ICD10CM"], create_indexes=False)

    print(f"\nRunning optimize_codes per CCSR (writes compact + inclusions)...", file=sys.stderr)
    t_start = time.perf_counter()
    total_compact = 0
    total_inclusions = 0

    with output_compact.open("w", newline="", encoding="utf-8") as f_compact, \
         output_inclusions.open("w", newline="", encoding="utf-8") as f_inc:
        writer_compact = csv.DictWriter(f_compact, fieldnames=CSV_COLUMNS + ("excluded_count", "excluded_codes"))
        writer_compact.writeheader()
        writer_inc = csv.DictWriter(f_inc, fieldnames=CSV_COLUMNS)
        writer_inc.writeheader()

        for idx, (ccsr_code, (ccsr_display, icd10_codes)) in enumerate(sorted(ccsr_map.items()), start=1):
            unique_codes = sorted(set(icd10_codes))
            refs = [CodeRef(source="ICD10CM", code=c) for c in unique_codes]

            try:
                # include_codes=True populates rule.covered_codes; needed for
                # the inclusions output. Slight overhead but unavoidable.
                result = optimize_codes(refs, engine=engine, source="ICD10CM", include_codes=True)
            except Exception as exc:
                print(f"  FAIL {ccsr_code}: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue

            for rule in result.rules:
                excluded = sorted(code.code for code in rule.exclude)
                # Prime any new codes from excludes/canonical that weren't in the input
                new_codes = set(excluded) | {rule.include.code}
                if new_codes - set(_display_cache):
                    _prime_display_cache(con, new_codes - set(_display_cache))
                excl_displays = [_lookup_display(con, e) for e in excluded]
                canon_code = rule.include.code
                canon_display = _lookup_display(con, canon_code)

                # Compact: roll up to parent, emit excludes
                writer_compact.writerow({
                    "ccsr_category": ccsr_code,
                    "ccsr_category_display": ccsr_display,
                    "icd10_code": canon_code,
                    "icd10_display": canon_display,
                    "excluded_count": len(excluded),
                    "excluded_codes": "; ".join(f"{c} ({d})" for c, d in zip(excluded, excl_displays)),
                })
                total_compact += 1

                # Inclusions: expand partials to covered leaves
                if excluded:
                    for covered in rule.covered_codes:
                        writer_inc.writerow({
                            "ccsr_category": ccsr_code,
                            "ccsr_category_display": ccsr_display,
                            "icd10_code": covered.code,
                            "icd10_display": _lookup_display(con, covered.code),
                        })
                        total_inclusions += 1
                else:
                    writer_inc.writerow({
                        "ccsr_category": ccsr_code,
                        "ccsr_category_display": ccsr_display,
                        "icd10_code": canon_code,
                        "icd10_display": canon_display,
                    })
                    total_inclusions += 1

            if args.progress and (idx % 25 == 0 or idx == len(ccsr_map)):
                elapsed = time.perf_counter() - t_start
                rate = idx / max(1e-6, elapsed)
                remaining = (len(ccsr_map) - idx) / max(0.1, rate)
                print(
                    f"  {idx}/{len(ccsr_map)} CCSR done ({rate:.1f}/s, "
                    f"~{remaining:.0f}s remaining)",
                    file=sys.stderr,
                )

    elapsed = time.perf_counter() - t_start
    print(
        f"\nDone in {elapsed:.1f}s — compact={total_compact:,}, inclusions={total_inclusions:,}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
