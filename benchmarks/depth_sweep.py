#!/usr/bin/env python3
"""AMUX-63: hybrid fusion candidate-depth sweep (latency vs quality).

Last release raised HYBRID_CANDIDATE_DEPTH to 100 (both legs), the
suspected source of the hybrid $search latency regression. This sweep
runs the PRODUCTION SearchService.hybrid() (real grading, real code
path — no reimplemented fusion) at several depths and reports per
query latency plus the golden-family quality metrics from
fusion_benchmark.py.

NOTE (AMUX-63 side-finding): this machine currently exhibits
nondeterministic native memory corruption in the medterm4ds lexical
path (stdlib-only crash frames, CPython refcount segfaults — see the
AMUX card for evidence). This script is therefore CHECKPOINTED: it
appends one JSON line per measured query and skips already-measured
cells, so a driver can retry until the grid is complete.

Method:
  - depth is varied via --depth (hybrid() reads the module constant
    HYBRID_CANDIDATE_DEPTH at call time; the driver patches it);
  - latency: median of --repeat timed calls after one warmup call
    (first-call index warmup excluded);
  - quality: hit@1 / hit@5 on returned rows, FP@1 (confident top-1
    that is not a hit), abstain@1 (top-1 below 'probable'), using
    production match_grade for confidence.

Usage:
  python benchmarks/depth_sweep.py --depth 25 --ckpt /tmp/sweep/25.jsonl
  python benchmarks/depth_sweep.py --report /tmp/sweep --json out.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from medterm4ds.services import search as sm  # noqa: E402
from fusion_benchmark import GOLDEN, _is_hit  # noqa: E402

DEPTHS = (10, 25, 50, 100)


def all_cases() -> list[tuple[str, str, object]]:
    return [
        (family, q, truth)
        for family, cases in GOLDEN.items()
        for q, truth in cases
    ]


def run_depth(depth: int, repeat: int, ckpt: Path) -> None:
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if ckpt.exists():
        for line in ckpt.read_text().splitlines():
            try:
                done.add(json.loads(line)["query"])
            except json.JSONDecodeError:
                continue  # truncated mid-crash line
    todo = [c for c in all_cases() if c[1] not in done]
    if not todo:
        return

    svc = sm.SearchService()
    svc.ensure_lexical()
    svc.ensure_semantic()
    sm.HYBRID_CANDIDATE_DEPTH = depth

    with ckpt.open("a") as fh:
        for family, q, truth in todo:
            svc.hybrid(q)  # warmup
            times = []
            for _ in range(repeat):
                t0 = time.perf_counter()
                rows = svc.hybrid(q)
                times.append((time.perf_counter() - t0) * 1000.0)
            top = rows[0] if rows else None
            grade_top = top.match_grade if top else "none"
            confident = grade_top in ("certain", "probable")
            if family == "abstain":
                hit1 = hit5 = False
            else:
                hit5 = any(
                    _is_hit(truth, r.code, r.display) for r in rows[:5]
                )
                hit1 = bool(top) and _is_hit(truth, top.code, top.display)
            rec = {
                "depth": depth,
                "family": family,
                "query": q,
                "ms": round(statistics.median(times), 1),
                "hit@1": hit1,
                "hit@5": hit5,
                "grade_top": grade_top,
                "confident_fp": bool(confident and family != "abstain" and not hit1)
                or bool(confident and family == "abstain"),
            }
            fh.write(json.dumps(rec) + "\n")
            fh.flush()


def report(ckpt_dir: str, out_json: str | None) -> None:
    records: list[dict] = []
    for p in sorted(Path(ckpt_dir).glob("*.jsonl")):
        for line in p.read_text().splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    by_depth: dict[int, list[dict]] = {}
    for r in records:
        by_depth.setdefault(r["depth"], []).append(r)
    summary: dict[int, dict] = {}
    for depth in sorted(by_depth):
        rows = by_depth[depth]
        n_all = len(all_cases())
        # dedupe by query (retries after partial corruption)
        seen: dict[str, dict] = {}
        for r in rows:
            seen.setdefault(r["query"], r)
        rows = list(seen.values())
        n = len(rows)
        lats = [r["ms"] for r in rows]
        lat_sorted = sorted(lats)
        hits = [r for r in rows if r["family"] != "abstain"]
        abs_rows = [r for r in rows if r["family"] == "abstain"]
        summary[depth] = {
            "measured_queries": n,
            "complete": n >= n_all,
            "hit@1%": round(100 * sum(r["hit@1"] for r in hits) / max(len(hits), 1), 1),
            "hit@5%": round(100 * sum(r["hit@5"] for r in hits) / max(len(hits), 1), 1),
            "FP%": round(
                100 * sum(r["confident_fp"] for r in rows) / max(len(rows), 1), 1
            ),
            "abstain%": round(
                100 * sum(
                    r["grade_top"] not in ("certain", "probable") for r in rows
                ) / max(n, 1),
                1,
            ),
            "latency_ms_median": round(statistics.median(lats), 1),
            "latency_ms_p90": round(lat_sorted[min(len(lat_sorted) - 1, int(0.9 * len(lat_sorted)))], 1),
        }
        s = summary[depth]
        print(
            f"depth={depth:>3} (n={s['measured_queries']}{'' if s['complete'] else ' INCOMPLETE'}): "
            f"hit@1 {s['hit@1%']}% hit@5 {s['hit@5%']}% FP {s['FP%']}% "
            f"abstain {s['abstain%']}% | median {s['latency_ms_median']}ms "
            f"p90 {s['latency_ms_p90']}ms"
        )
    if out_json:
        Path(out_json).write_text(json.dumps(summary, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=None)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    if args.depth is not None:
        assert args.ckpt, "--ckpt required with --depth"
        run_depth(args.depth, args.repeat, Path(args.ckpt))
        return 0
    if args.report:
        report(args.report, args.json)
        return 0
    ap.error("need --depth or --report")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
