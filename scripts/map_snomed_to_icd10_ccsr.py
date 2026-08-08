#!/usr/bin/env python3
"""Map SNOMED CT (disease + finding) codes → ICD-10-CM → CCSR category.

Two legs:

  1. SNOMED → ICD-10-CM via medterm4ds's ``get_code_mappings`` with
     ``max_depth=5``. Direct (mapped_from + same-CUI) matches are tried
     first; on miss the engine walks up the SNOMED hierarchy (IS-A / PAR /
     CHD edges per ``_source_hierarchy_join_sql``) trying each ancestor.
     ``match_depth`` records how far up the walk found a match
     (0 = direct; 1-5 = Nth ancestor).

  2. ICD-10-CM → CCSR via a single set-based ``mrrel`` query against
     ``RELA = 'default_inpatient_classification_of'``. CCSR categories
     ship in UMLS as ``SAB = 'CCSR_ICD10CM'`` (554 categories in 2025AB);
     AHRQ designates one inpatient default per ICD-10-CM code when the
     code maps to multiple categories.

Input scope: every active ``SNOMEDCT_US`` atom joined to ``mrsty`` whose
TUI is in the disease/finding family (T019, T020, T037, T046-T049, T184,
T190, T191). Override with ``--snomed-codes`` (file, one code per line).

Output: per-tuple CSV with one row per (SNOMED, ICD-10-CM, CCSR). Columns:

  snomed_code, snomed_display, match_type, match_depth,
  icd10_code, icd10_display, ccsr_category, ccsr_category_display

Usage::

  PYTHONPATH=src python3 scripts/map_snomed_to_icd10_ccsr.py \\
      --db /mnt/d/medterm4ds/data/umls_current.duckdb \\
      --output reports/fhir4px/snomed_to_icd10_ccsr.csv \\
      --memory-profile balanced --progress

Smoke test::

  PYTHONPATH=src python3 scripts/map_snomed_to_icd10_ccsr.py \\
      --db /mnt/d/medterm4ds/data/umls_current.duckdb \\
      --output /tmp/smoke.csv --limit 100 --progress
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

from medterm4ds.core.config import local_duckdb_config
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.mapping import get_code_mappings

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_OUTPUT = Path("reports/fhir4px/snomed_to_icd10_ccsr.csv")

# UMLS semantic-network TUIs for diseases / findings. AHRQ CCSR is diagnosis-
# side, so we restrict to clinically-relevant SNOMED concepts. Add or remove
# via --tui-include (comma-separated) if a different slice is needed.
DEFAULT_DISEASE_FINDING_TUIS = (
    "T019",  # Congenital Abnormality
    "T020",  # Acquired Abnormality
    "T037",  # Injury or Poisoning
    "T046",  # Pathologic Function
    "T047",  # Disease or Syndrome
    "T048",  # Mental or Behavioral Dysfunction
    "T049",  # Cell or Molecular Dysfunction
    "T184",  # Sign or Symptom
    "T190",  # Anatomical Abnormality
    "T191",  # Neoplastic Process
)

CSV_COLUMNS = (
    "snomed_code",
    "snomed_display",
    "match_type",
    "match_depth",
    "matched_ancestor_code",
    "matched_ancestor_display",
    "matched_ancestor_depth_from_root",
    "icd10_code",
    "icd10_display",
    "ccsr_category",
    "ccsr_category_display",
)


def _load_snomed_codes(
    con,
    *,
    tuis: tuple[str, ...],
    limit: int,
    snomed_codes_file: Path | None,
) -> list[str]:
    """Active SNOMEDCT_US atom codes filtered to disease/finding TUIs.

    Caller-supplied file overrides TUI filtering (one code per line).
    """
    if snomed_codes_file is not None:
        text = snomed_codes_file.read_text(encoding="utf-8")
        codes = [line.strip() for line in text.splitlines() if line.strip()]
        # Deduplicate while preserving file order.
        seen: set[str] = set()
        unique: list[str] = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        return unique

    placeholders = ", ".join("?" for _ in tuis)
    sql = f"""
        SELECT DISTINCT c.CODE
        FROM mrconso c
        JOIN mrsty m ON m.CUI = c.CUI
        WHERE c.SAB = 'SNOMEDCT_US'
          AND c.SUPPRESS = 'N'
          AND c.CODE IS NOT NULL
          AND c.CODE != ''
          AND m.TUI IN ({placeholders})
        ORDER BY c.CODE
    """
    params: list[object] = list(tuis)
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    rows = con.execute(sql, params).fetchall()
    return [r[0] for r in rows]


def _build_icd10_to_ccsr_map(
    con,
    icd10_codes: list[str],
) -> dict[str, tuple[str, str]]:
    """One-shot SQL: map every ICD-10-CM code to its inpatient-default CCSR.

    Returns ``{icd10_code: (ccsr_category, ccsr_category_display)}``. Codes
    without a default CCSR are absent from the dict.

    Uses the ``umls.`` schema explicitly because ``LocalDuckDBEngine.prepare_cache``
    shadows ``main.mrrel`` with a filtered copy that retains only the edge
    families medterm4ds walks internally (hierarchy + same-CUI + selected
    crosswalks). The CCSR ``default_inpatient_classification_of`` edges live
    in the original ``umls.mrrel`` and are dropped from the ``main.mrrel``
    projection.
    """
    if not icd10_codes:
        return {}
    # Chunk to avoid SQLite-style parameter limits (DuckDB handles large IN
    # lists but keeping chunks bounded keeps the query plan stable).
    out: dict[str, tuple[str, str]] = {}
    chunk = 1000
    for i in range(0, len(icd10_codes), chunk):
        batch = icd10_codes[i : i + chunk]
        placeholders = ", ".join("?" for _ in batch)
        rows = con.execute(
            f"""
            SELECT icd.CODE AS icd10_code,
                   ccsr.CODE AS ccsr_category,
                   ccsr.STR AS ccsr_category_display
            FROM umls.mrconso icd
            JOIN umls.mrrel r
              ON r.AUI1 = icd.AUI
             AND r.RELA = 'default_inpatient_classification_of'
            JOIN umls.mrconso ccsr
              ON ccsr.AUI = r.AUI2
             AND ccsr.SAB = 'CCSR_ICD10CM'
            WHERE icd.SAB = 'ICD10CM'
              AND icd.SUPPRESS = 'N'
              AND icd.CODE IN ({placeholders})
            """,
            list(batch),
        ).fetchall()
        for icd10, ccsr_cat, ccsr_disp in rows:
            # AHRQ designates one default per code; first row per code wins
            # if multiple rows ever appear (shouldn't, defensive).
            out.setdefault(icd10, (ccsr_cat, ccsr_disp))
    return out


def _chunk(seq: list[str], size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DEFAULT_DB, help=f"DuckDB UMLS path (default: {DEFAULT_DB})")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV path (default: {DEFAULT_OUTPUT})")
    p.add_argument("--memory-profile", default="balanced", choices=("fast", "balanced", "low"))
    p.add_argument(
        "--memory-limit",
        default=None,
        help="Override profile memory limit (e.g. '32GB'). Bypasses --memory-profile when set.",
    )
    p.add_argument(
        "--threads",
        type=int,
        default=None,
        help="Override profile thread count. Defaults to all visible CPUs when --memory-limit is set.",
    )
    p.add_argument(
        "--target-tty",
        default="PT",
        help=(
            "Comma-separated ICD-10-CM atom TTYs to keep on the mapping target "
            "(default: 'PT' = billable diagnosis codes only). 'PT' filters out "
            "HT (hierarchical term) and ET (entry term) category headers that "
            "appear as same-CUI matches but have no CCSR mapping. Pass '' or "
            "'all' to disable filtering."
        ),
    )
    p.add_argument(
        "--min-ancestor-depth-from-root",
        type=int,
        default=3,
        help=(
            "Drop mappings whose matched SNOMED ancestor is too close to the "
            "root. SNOMED depth 1 = universal root (138875005), depth 2 = "
            "top-level branches (Disease, Clinical finding, Procedure, Body "
            "structure — 22 codes). Mappings sourced from these are "
            "chapter-level noise (e.g. Diabetes → K92.9 'Disease of digestive "
            "system, unspecified'). Default 3 drops only depth-1 and depth-2 "
            "ancestors; pass 1 to keep all mappings. Direct match_depth=0 "
            "mappings are always kept regardless of this setting."
        ),
    )
    p.add_argument("--max-depth", type=int, default=5, help="SNOMED hierarchy fallback depth (default: 5)")
    p.add_argument(
        "--max-results-per-code",
        type=int,
        default=20,
        help="Cap on ICD-10-CM mappings per SNOMED code (default: 20)",
    )
    p.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help="Codes per get_code_mappings batch (default: 1000)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Cap on SNOMED codes processed (0 = no cap; useful for smoke tests)",
    )
    p.add_argument(
        "--snomed-codes",
        type=Path,
        default=None,
        help="Optional file with one SNOMED code per line. Overrides TUI filter.",
    )
    p.add_argument(
        "--tui-include",
        default=",".join(DEFAULT_DISEASE_FINDING_TUIS),
        help=f"Comma-separated UMLS TUIs to include (default: disease+finding family)",
    )
    p.add_argument("--progress", action="store_true", help="Log progress per chunk")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tuis = tuple(t.strip() for t in args.tui_include.split(",") if t.strip())
    if not tuis and args.snomed_codes is None:
        print("Either --tui-include or --snomed-codes must be non-empty.", file=sys.stderr)
        return 2

    config = local_duckdb_config(args.memory_profile)
    if args.memory_limit is not None:
        # User override — bypass the profile. Default threads to all visible
        # CPUs when the user opts into custom memory; this is the "use my
        # machine's capabilities" path.
        threads = args.threads if args.threads is not None else os.cpu_count()
        engine_kwargs = {
            "memory_limit": args.memory_limit,
            "threads": threads,
            "query_chunk_size": config.query_chunk_size,
            "preserve_insertion_order": config.preserve_insertion_order,
        }
        profile_label = f"custom (memory={args.memory_limit}, threads={threads})"
    else:
        engine_kwargs = {"config": config}
        threads = config.threads if config.threads is not None else os.cpu_count()
        profile_label = f"{args.memory_profile} profile (memory={config.memory_limit}, threads={threads})"
    print(f"DuckDB: {profile_label}", file=sys.stderr)

    import duckdb
    con = duckdb.connect(str(db_path), read_only=True)
    engine = LocalDuckDBEngine(con, **engine_kwargs)
    # Patient-friendly cache isn't needed for mapping; skip prepare_cache to
    # keep startup fast.
    engine.prepare_cache(["SNOMEDCT_US", "ICD10CM"], create_indexes=False)

    # Resolve target TTY filter
    if args.target_tty.strip().lower() in ("", "all"):
        target_ttys: set[str] | None = None
    else:
        target_ttys = {t.strip().upper() for t in args.target_tty.split(",") if t.strip()}
    if target_ttys is not None:
        print(f"Filtering ICD-10-CM targets to TTY in {sorted(target_ttys)}", file=sys.stderr)

    # Ancestor-depth-from-root cap (option B): look up each matched ancestor's
    # depth in snomed_top_level_depth (rebuilt 2026-07-21 to cover all 386K
    # active SNOMED codes, depths 1-18).
    min_anc_root_depth = args.min_ancestor_depth_from_root
    print(
        f"Cap: drop mappings whose matched SNOMED ancestor depth-from-root < {min_anc_root_depth} "
        f"(depth 1=SNOMED root, 2=top-level branches like Disease/Clinical finding)",
        file=sys.stderr,
    )
    ancestor_depth_cache: dict[str, int | None] = {}

    def lookup_ancestor_depth(code: str) -> int | None:
        if not code:
            return None
        if code not in ancestor_depth_cache:
            r = con.execute(
                "SELECT min_top_depth FROM snomed_top_level_depth WHERE code=?",
                [code],
            ).fetchone()
            ancestor_depth_cache[code] = r[0] if r else None
        return ancestor_depth_cache[code]

    tuis_label = "caller-supplied file" if args.snomed_codes else f"{len(tuis)} disease/finding TUIs"
    print(f"Loading SNOMED codes ({tuis_label})...", file=sys.stderr)
    t0 = time.perf_counter()
    snomed_codes = _load_snomed_codes(
        con,
        tuis=tuis if args.snomed_codes is None else DEFAULT_DISEASE_FINDING_TUIS,
        limit=args.limit,
        snomed_codes_file=args.snomed_codes,
    )
    print(
        f"  {len(snomed_codes):,} codes in {time.perf_counter() - t0:.1f}s",
        file=sys.stderr,
    )
    if not snomed_codes:
        print("No SNOMED codes selected; nothing to do.", file=sys.stderr)
        return 1

    total_rows = 0
    codes_with_match = 0
    codes_without_match = 0
    t_start = time.perf_counter()

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        all_icd10_seen: set[str] = set()
        # Two-pass: stream mappings out, but defer CCSR enrichment until the
        # full ICD-10 set is known (one batched SQL beats N per-tuple queries).
        # Buffer the per-chunk mappings, do CCSR lookup at end of chunk.
        # Memory bounded by chunk_size * max_results_per_code rows.
        for chunk_idx, chunk in enumerate(_chunk(snomed_codes, args.chunk_size)):
            t_chunk = time.perf_counter()
            refs = [CodeRef(source="SNOMEDCT_US", code=c) for c in chunk]
            mappings = get_code_mappings(
                refs,
                engine=engine,
                target_sources=["ICD10CM"],
                max_results_per_code=args.max_results_per_code,
                max_depth=args.max_depth,
            )

            # Drop mappings whose target atom isn't a billable diagnosis code.
            # medterm4ds returns target atoms of any TTY (PT preferred, HT
            # hierarchical, ET entry). HT/ET atoms are ICD-10-CM tabular
            # category headers (e.g. "H93.9 Unspecified disorder of ear" looks
            # like a real code but is a parent-class node); AHRQ CCSR doesn't
            # map them. Filtering here keeps the output clinically meaningful.
            if target_ttys is not None:
                mappings = [m for m in mappings if (m.target_tty or "").upper() in target_ttys]

            # Ancestor-depth-from-root cap. The engine already exposes the
            # matched SNOMED ancestor in matched_via.steps[op="source_ancestor"].
            # Direct match_depth=0 mappings have no source_ancestor step — they're
            # the most specific match and always kept.
            kept_mappings = []
            cap_dropped = 0
            for m in mappings:
                anc_step = None
                if m.matched_via:
                    anc_step = next(
                        (s for s in m.matched_via.steps if s.op == "source_ancestor"),
                        None,
                    )
                if anc_step is None:
                    # Direct same-CUI match — no ancestor walked. Keep.
                    kept_mappings.append((m, "", "", ""))
                    continue
                anc_code = anc_step.code or ""
                anc_name = anc_step.name or ""
                anc_root_depth = lookup_ancestor_depth(anc_code)
                if anc_root_depth is None or anc_root_depth >= min_anc_root_depth:
                    kept_mappings.append(
                        (m, anc_code, anc_name, anc_root_depth if anc_root_depth is not None else "")
                    )
                else:
                    cap_dropped += 1
            mappings_for_chunk = kept_mappings

            # Group by source code so we can count codes-without-match.
            mappings_by_source: dict[str, list] = {}
            for m, _, _, _ in mappings_for_chunk:
                mappings_by_source.setdefault(m.source.code, []).append(m)
            codes_with_match += len(mappings_by_source)
            codes_without_match += len(chunk) - len(mappings_by_source)

            icd10_codes = sorted({m.target.code for m, _, _, _ in mappings_for_chunk if m.target.code})
            all_icd10_seen.update(icd10_codes)
            ccsr_map = _build_icd10_to_ccsr_map(con, icd10_codes)

            for m, anc_code, anc_name, anc_root_depth in mappings_for_chunk:
                icd10 = m.target.code
                ccsr_cat, ccsr_disp = ccsr_map.get(icd10, ("", ""))
                writer.writerow({
                    "snomed_code": m.source.code,
                    "snomed_display": m.source_display or "",
                    "match_type": m.match_type,
                    "match_depth": m.match_depth,
                    "matched_ancestor_code": anc_code,
                    "matched_ancestor_display": anc_name,
                    "matched_ancestor_depth_from_root": anc_root_depth,
                    "icd10_code": icd10,
                    "icd10_display": m.target_display or "",
                    "ccsr_category": ccsr_cat,
                    "ccsr_category_display": ccsr_disp,
                })
                total_rows += 1

            f.flush()
            if args.progress:
                elapsed = time.perf_counter() - t_start
                done = (chunk_idx + 1) * args.chunk_size
                pct = min(100.0, 100.0 * done / len(snomed_codes))
                rate = len(chunk) / max(1e-6, time.perf_counter() - t_chunk)
                print(
                    f"  chunk {chunk_idx + 1}: {len(mappings):,} mappings "
                    f"({cap_dropped:,} dropped by ancestor-depth cap), "
                    f"{len(icd10_codes):,} ICD-10 codes, "
                    f"{done:,}/{len(snomed_codes):,} SNOMED ({pct:.1f}%) "
                    f"[{rate:.0f} codes/s, {elapsed:.0f}s elapsed]",
                    file=sys.stderr,
                )

    elapsed_total = time.perf_counter() - t_start
    print(
        f"\nDone in {elapsed_total:.1f}s — "
        f"{total_rows:,} rows, {codes_with_match:,} SNOMED codes matched, "
        f"{codes_without_match:,} unmatched, {len(all_icd10_seen):,} unique ICD-10 codes.\n"
        f"Output: {output_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
