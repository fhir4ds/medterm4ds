#!/usr/bin/env python3
"""AMUX-46: hybrid search fusion benchmark (offline comparison).

Compares three hybrid strategies on golden families drawn from the
published terminology release (2026.10.31.1):

  cascade  — production today: BM25 candidate pool -> SapBERT re-rank
             (lexical recall is a hard gate; the semantic best row is
             invisible when BM25's tf-tie lottery drops it)
  rrf      — reciprocal-rank fusion over the UNION of per-system top-K:
             sum of 1/(k + rank) per system, k ~ 60; no score-scale
             reconciliation, one constant
  wsum     — weighted sum with per-query z-score normalisation inside
             the union: lambda*lex + (1-lambda)*sem

Golden families (query -> expected anchor, verified against the release's
own anchors_metadata/value_sets):
  brands, acronyms, classes, panels, dose-form, misspellings,
  abstention probes (must return nothing confident).

Metrics per family: hit@1, hit@5, false-positive@1 (confident top-1 on a
different anchor than truth — for abstention probes ANY confident top-1),
abstention@1 (no result at/above the 'probable' grade).

Usage: uv run python benchmarks/fusion_benchmark.py [--out FILE.md]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from medterm4ds.services import search as sm  # noqa: E402

TOP_K = sm.HYBRID_CANDIDATE_DEPTH  # production per-system candidate depth
RRF_KS = (sm.HYBRID_FUSION_K, 60, 100)  # production k first (QC1-A9)
WSUM_LAMBDAS = (0.3, 0.5, 0.7)

# --- Golden sets ------------------------------------------------------
# Atom-surface truth, hand-labeled against the release indexes
# (top-100 per system dumped and inspected; see AMUX-46 notes).
# A row hits when (source, code) in accept_codes OR its display surface-key
# is in accept_displays OR it matches an accept_contains substring.
# Combination products of the right ingredient ('Acetaminophen / Caffeine'
# for 'Tylenol') are deliberately NOT accepted — different drug, the
# cascade's current top-1 and a real clinical FP.
def _g(accept_displays=(), accept_codes=(), contains=()):
    return {
        "displays": set(accept_displays),
        "codes": set(accept_codes),
        "contains": tuple(contains),
    }


GOLDEN: dict[str, list[tuple[str, dict]]] = {
    "brands": [
        # Joel ruling (AMUX-22, 2026-11-04): brand/product rows are GOOD
        # answers for brand queries — each brand's own surface-key is
        # accepted alongside the ingredient truths.
        ("Tylenol", _g(
            ["tylenol", "acetaminophen", "acetaminophen oral product",
             "acetaminophen oral liquid product"])),
        ("Adbry", _g(["adbry", "tralokinumab", "tralokinumab injectable product"])),
        ("Motrin", _g(
            ["motrin", "ibuprofen", "ibuprofen oral product",
             "ibuprofen oral liquid product"])),
        ("Lipitor", _g(["lipitor", "atorvastatin", "atorvastatin oral product"])),
        ("Januvia", _g(["januvia", "sitagliptin", "sitagliptin oral product"])),
        ("Farxiga", _g(["farxiga", "dapagliflozin", "dapagliflozin oral product"])),
        ("Trulicity", _g(["trulicity", "dulaglutide", "dulaglutide injectable product"])),
    ],
    "acronyms": [
        ("MI", _g(["acute myocardial infarction mi"])),
        ("PE", _g(["pulmonary embolism"])),  # known-hard: both systems miss
        ("PT", _g(["prothrombin time", "prothrombin time pt test", "pt panel"])),
        ("HbA1c", _g(["hba1c", "a1c", "glycosylated hemoglobin", "hemoglobin a1c"])),
        ("CKD", _g(["chronic kidney disease"])),
        ("COPD", _g(["copd", "chronic obstructive pulmonary disease"])),
    ],
    "classes": [
        ("ACE inhibitors", _g(
            ["ace inhibitors plain"], contains=["angiotensin-converting enzyme"])),
        ("Biguanides", _g(["biguanide", "biguanides"])),
        ("Diabetes Medications", _g([], contains=["drugs used in diabetes", "antidiabetic"])),
        ("SGLT2", _g([], contains=["sglt2"])),
    ],
    "panels": [
        ("Lipid Panel", _g(["lipid panel", "lipids profile test", "lipids"])),
        ("Comprehensive Metabolic Panel", _g(
            ["comprehensive metabolic panel", "comprehensive metabolic 1998 panel",
             "comprehensive metabolic 2000 panel"])),
        ("Complete Blood Count", _g(
            ["blood count tests", "blood cell count", "hemogram"])),
        ("Plasma Metanephrines", _g([], contains=["metanephrine"])),
    ],
    "dose": [
        # the demo failure: the strength row must become visible. Truth by
        # code (all lisinopril rows display 'Lisinopril', so display truth
        # would be vacuous): the 5mg row, its SNOMED product twin, or the
        # ingredient.
        ("lisinopril 5mg", _g(
            accept_codes=["316156", "318858008", "311354", "29046"])),
        ("metformin 500 mg", _g(
            accept_codes=["860974", "861024", "861020", "6809"])),
    ],
    "misspellings": [
        ("acetaminophin", _g(
            ["acetaminophen", "acetaminophen oral product"])),
        ("lisinoprill", _g(["lisinopril"])),
        ("metformine", _g(["metformin"])),
        ("lipitir", _g(["atorvastatin", "atorvastatin oral product"])),  # known-hard
    ],
    "abstain": [
        ("zzzqqx wobble", None),
        ("flurbnocker", None),
        ("quantum granola syndrome", None),
        ("credit score", None),
        ("weather tomorrow", None),
    ],
}


def _surface(text: str) -> str:
    # Production surface equality (QC1-A5): reuse _surface_key — the
    # local copy lacked accent folding and drifted from the real exact
    # -surface decisions for accented queries.
    from medterm4ds.services.search import _surface_key

    return _surface_key(text)


def _is_hit(truth, code: str, display: str) -> bool:
    if truth is None:
        return False
    if code in truth["codes"]:
        return True
    key = _surface(display)
    if key in truth["displays"]:
        return True
    return any(c in display.lower() for c in truth["contains"])


class Candidates:
    """Per-system candidate rows for one query (the fusion input)."""

    def __init__(self, lex: list, sem: list):
        self.lex = lex   # [(source, code, display, score)]
        self.sem = sem


def collect(svc: sm.SearchService, query: str) -> Candidates:
    lex = [
        (r.source, r.code, r.display, r.score)
        for r in svc.lexical(query, count=TOP_K)
    ]
    engine = svc._ensure_semantic()
    sem = [
        (r.get("system", "").upper(), r["code"], r["display"], r["score"])
        for r in engine.search(query, top_k=TOP_K)
    ]
    return Candidates(lex, sem)


def _row_identity(row) -> tuple:
    return (row[0], row[1])


def fuse_rrf(c: Candidates, k: int) -> list[tuple]:
    """RRF over the union. Rows carry evidence meta: (identity, fused_score,
    display, meta-dict with lex_rank/sem_rank/lex_score/sem_cos/exact)."""
    scores: dict[tuple, float] = {}
    meta: dict[tuple, dict] = {}
    for name, system in (("lex", c.lex), ("sem", c.sem)):
        for rank, row in enumerate(system, start=1):
            ident = _row_identity(row)
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (k + rank)
            m = meta.setdefault(
                ident, {"display": row[2], "lex_rank": None, "sem_rank": None,
                        "lex_score": None, "sem_cos": None, "exact": False},
            )
            m[f"{name}_rank"] = rank
            m[f"{'lex_score' if name == 'lex' else 'sem_cos'}"] = row[3]
    q_key = None  # exactness computed by caller for the query
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    out = []
    for ident, sc in ranked:
        m = meta[ident]
        out.append((ident, sc, m["display"], m))
    return out, q_key


def fuse_rrf_guarded(c: Candidates, k: int, query: str) -> list[tuple]:
    """RRF ranking + AGREEMENT-AWARE grading (AMUX-46 candidate).

    The fused RRF score is a rank signal, not a confidence signal (measured:
    true/wrong top-1 medians overlap at k=60). Confidence instead requires
    SYSTEM CONCORDANCE + the underlying calibrated cosine:
      certain : present in BOTH systems' tops AND cosine >= 0.92
      probable: cosine >= 0.75, or lexical exact-surface (AMUX-18) at
                lexical rank <= 3
      possible: everything else (counts as abstain)
    Lexical-only rows can never be 'certain' (single-evidence ceiling) —
    the precision veto lexical had as a pool GATE returns as a GRADE gate.
    """
    rows, _ = fuse_rrf(c, k)
    from medterm4ds.services.search import (
        GRADE_CERTAIN_COSINE,
        GRADE_PROBABLE_COSINE,
        _surface_key,
    )
    q_key = _surface_key(query)
    out = []
    for ident, rrf_score, display, m in rows:
        both = m["lex_rank"] is not None and m["sem_rank"] is not None
        cos = m["sem_cos"] or 0.0
        exact = m["lex_rank"] is not None and m["lex_rank"] <= 3 and (
            _surface_key(display) == q_key
        )
        if both and cos >= GRADE_CERTAIN_COSINE:
            gscore = 0.95  # certain band (report-grade proxy)
        elif cos >= GRADE_PROBABLE_COSINE or exact:
            gscore = 0.80  # probable band
        else:
            gscore = 0.0   # possible -> abstain
        out.append((ident, gscore, display, {"rrf": rrf_score, **m}))
    # order by RRF (ranking), grade by guard (confidence)
    out.sort(key=lambda r: (-r[3]["rrf"], r[0]))
    return out


def _zscore(rows: list) -> dict[tuple, float]:
    if not rows:
        return {}
    vals = [r[3] for r in rows]
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var**0.5 or 1.0
    return {
        _row_identity(r): (r[3] - mean) / std for r in rows
    }


def fuse_wsum(c: Candidates, lam: float) -> list[tuple[tuple, float, str, str]]:
    zl = _zscore(c.lex)
    zs = _zscore(c.sem)
    union: dict[tuple, tuple] = {}
    for rows in (c.lex, c.sem):
        for row in rows:
            union[_row_identity(row)] = (row[2], row[3])
    fused = {
        ident: lam * zl.get(ident, 0.0) + (1 - lam) * zs.get(ident, 0.0)
        for ident in union
    }
    ranked = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(ident, s, union[ident][0], "union") for ident, s in ranked]


def cascade(svc: sm.SearchService, query: str) -> list[tuple[tuple, float, str, str]]:
    """The HISTORICAL v0.0.8 cascade, reimplemented here (QC1-A3).

    Production hybrid() became the RRF winner, so delegating the
    baseline to svc.hybrid made 'cascade' silently alias the winner —
    the baseline must be reproducible from the code under test's
    predecessor shape: BM25 pool (max(50, 3*count)) -> engine.rerank
    top_k, cosine grades from the engine."""
    engine = svc._ensure_semantic()
    pool = svc.lexical(query, count=min(TOP_K * 3, max(50, TOP_K)))
    if not pool:
        pool_sem = engine.search(query, top_k=TOP_K)
        return [
            ((r["system"].upper(), r["code"]), float(r["score"]), r["display"], "cascade")
            for r in pool_sem
        ]
    candidates = [
        {"code": r.code, "system": r.source, "display": r.display} for r in pool
    ]
    reranked = engine.rerank(query, candidates, top_k=TOP_K)
    return [
        ((r.get("system", "").upper(), r["code"]), float(r["score"]), r["display"], "cascade")
        for r in reranked
    ]


def grade(score: float, cutoffs: tuple[float, float]) -> str:
    certain, probable = cutoffs
    if score >= certain:
        return "certain"
    if score >= probable:
        return "probable"
    return "possible"


# Fused-score grades are NOT cosines; per-variant cutoffs are calibrated
# from the measured distributions below (see CALIBRATION in results).
VARIANT_CUTOFFS: dict[str, tuple[float, float]] = {
    "cascade": (0.92, 0.75),   # cosine grades (production today)
    "rrf(k=60)+guard": (0.90, 0.70),  # proxy bands from fuse_rrf_guarded
    "rrf(k=10)+guard": (0.90, 0.70),
    "rrf": (0.031, 0.023),     # calibrated: see benchmark output
    "wsum": (1.10, 0.55),      # calibrated: see benchmark output
}


def evaluate(
    ranker: Callable[[str], list[tuple[tuple, float, str, str]]],
    cutoffs: tuple[float, float],
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for family, cases in GOLDEN.items():
        h1 = h5 = fp = ab = 0
        n = len(cases)
        for query, truth in cases:
            rows = ranker(query)
            top = rows[0] if rows else None
            grade_top = grade(top[1], cutoffs) if top else "none"
            confident = grade_top in ("certain", "probable")
            if family == "abstain":
                if confident:
                    fp += 1
                else:
                    ab += 1
            else:
                hit5 = any(
                    _is_hit(truth, r[0][1], r[2]) for r in rows[:5]
                )
                hit1 = bool(top) and _is_hit(truth, top[0][1], top[2])
                h1 += hit1
                h5 += hit5
                if not confident:
                    ab += 1
                elif not hit1:
                    fp += 1
        out[family] = {
            "n": n, "hit@1": h1, "hit@5": h5, "FP@1": fp, "abstain": ab,
            "hit@1%": round(100 * h1 / n, 1),
            "hit@5%": round(100 * h5 / n, 1),
            "FP%": round(100 * fp / n, 1),
            "abstain%": round(100 * ab / n, 1),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    svc = sm.SearchService()
    svc.ensure_lexical()
    svc.ensure_semantic()

    cache: dict[str, Candidates] = {}

    def cands(q: str) -> Candidates:
        if q not in cache:
            cache[q] = collect(svc, q)
        return cache[q]

    variants: dict[str, Callable] = {
        "cascade": lambda q: cascade(svc, q),
        "rrf(k=60)+guard": lambda q: fuse_rrf_guarded(cands(q), 60, q),
        "rrf(k=10)+guard": lambda q: fuse_rrf_guarded(cands(q), 10, q),
    }
    for k in RRF_KS:
        variants[f"rrf(k={k})"] = (lambda q, _k=k: fuse_rrf(cands(q), _k)[0])
    for lam in WSUM_LAMBDAS:
        variants[f"wsum(l={lam})"] = (lambda q, _l=lam: fuse_wsum(cands(q), _l))

    results: dict[str, dict] = {}
    for name, ranker in variants.items():
        cutoffs = VARIANT_CUTOFFS.get(
            name,
            VARIANT_CUTOFFS["rrf" if name.startswith("rrf") else "wsum"],
        )
        results[name] = evaluate(ranker, cutoffs)

    # Render markdown table
    lines = ["# AMUX-46 fusion benchmark", "",
             f"release candidates: per-system top-{TOP_K}; "
             f"families: {', '.join(GOLDEN)}", ""]
    for name, fams in results.items():
        lines.append(f"## {name}")
        lines.append("| family | n | hit@1 | hit@5 | FP@1 | abstain |")
        lines.append("|---|---|---|---|---|---|")
        tot_h1 = tot_h5 = tot_fp = tot_ab = tot_n = 0
        for fam, m in fams.items():
            lines.append(
                f"| {fam} | {m['n']} | {m['hit@1%']}% | {m['hit@5%']}% "
                f"| {m['FP%']}% | {m['abstain%']}% |"
            )
            tot_h1 += m["hit@1"]
            tot_h5 += m["hit@5"]
            tot_fp += m["FP@1"]
            tot_ab += m["abstain"]
            tot_n += m["n"]
        lines.append(
            f"| **TOTAL** | {tot_n} | {round(100*tot_h1/tot_n,1)}% "
            f"| {round(100*tot_h5/tot_n,1)}% | {round(100*tot_fp/tot_n,1)}% "
            f"| {round(100*tot_ab/tot_n,1)}% |"
        )
        lines.append("")

    # Calibration evidence: score distributions for true top-1 vs FP top-1
    lines.append("## Calibration evidence (fused-score distributions)")
    lines.append("Scores of TRUE top-1 hits vs confident-but-wrong top-1s "
                 "per variant (median [min..max]):")
    lines.append("")
    for vname in ("cascade", "rrf(k=60)", "wsum(l=0.5)"):
        ranker = variants[vname]
        true_scores: list[float] = []
        wrong_scores: list[float] = []
        cutoffs = VARIANT_CUTOFFS.get(
            vname, VARIANT_CUTOFFS["rrf" if vname.startswith("rrf") else "wsum"]
        )
        for family, cases in GOLDEN.items():
            if family == "abstain":
                continue
            for query, truth in cases:
                rows = ranker(query)
                if not rows:
                    continue
                top = rows[0]
                if _is_hit(truth, top[0][1], top[2]):
                    true_scores.append(top[1])
                elif grade(top[1], cutoffs) in ("certain", "probable"):
                    wrong_scores.append(top[1])
        def med(xs):
            xs = sorted(xs)
            return f"{xs[len(xs)//2]:.4f} [{min(xs):.4f}..{max(xs):.4f}]" if xs else "n/a"
        lines.append(
            f"- {vname}: true top-1 = {med(true_scores)} (n={len(true_scores)}); "
            f"confident-wrong top-1 = {med(wrong_scores)} (n={len(wrong_scores)})"
        )

    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
        print(f"\nwritten: {args.out}")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
