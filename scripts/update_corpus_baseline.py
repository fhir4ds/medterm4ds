#!/usr/bin/env python3
"""Regenerate tests/corpora/known_misses.json from the current pipeline.

The corpus test is a ratchet: it fails only on NEW misses. After a fix lands,
run this script and commit the shrunken baseline with the fix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CORPORA = ROOT / "tests" / "corpora"
OUT = CORPORA / "known_gaps.json"
MINIMAL_PAIRS = CORPORA / "minimal_pairs.json"
COMMENT = (
    "Baseline of corpus expectations not yet met with the shipped vocabulary/"
    "anchors: unresolved/mismatched mentions, furniture that still resolves, "
    "and qualifier pairs that still collide. tests/test_corpora_ner.py fails "
    "only on NEW gaps; regenerate with scripts/update_corpus_baseline.py when "
    "a fix lands (the diff is the fix's evidence)."
)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def collect(service) -> list[str]:
    misses: list[str] = []
    for path in sorted(CORPORA.glob("*_corpus.json")):
        for case in json.loads(path.read_text()).get("cases", []):
            mentions = case.get("mentions") or []
            if not mentions:
                continue
            open_statuses = bool(case.get("include_non_affirmed"))
            spans = service.find_terms(
                case["text"],
                include_negated=open_statuses,
                include_uncertain=open_statuses,
                include_historical=open_statuses,
                include_family=open_statuses,
            )
            concepts = service.resolve_spans(spans)
            span_texts = {_norm(s.text) for s in spans}
            for mention in mentions:
                expected = [t.lower() for t in mention.get("expect", [])]
                if not expected:
                    continue
                related = [
                    c for c in concepts
                    if (_norm(c.matched_text) in span_texts
                        or _norm(c.matched_text) in _norm(mention["text"]))
                    and (_norm(c.matched_text) in _norm(mention["text"])
                         or _norm(mention["text"]) in _norm(c.matched_text))
                ]
                key = f"{path.stem}:{case['id']}"
                if not related:
                    misses.append(f"{key}: {mention['text']!r} unresolved")
                    continue
                mode = mention.get("mode", "any")
                displays = [c.display.lower() for c in related]
                hit = any(
                    all(t in d for t in expected) if mode == "all"
                    else any(t in d for t in expected)
                    for d in displays
                )
                if not hit:
                    misses.append(f"{key}: {mention['text']!r} -> {displays[:2]}")
    return sorted(misses)


def collect_noise(service) -> list[str]:
    violations: list[str] = []
    for path in sorted(CORPORA.glob("*_corpus.json")):
        for case in json.loads(path.read_text()).get("cases", []):
            noise = case.get("noise") or []
            if not noise:
                continue
            open_statuses = bool(case.get("include_non_affirmed"))
            spans = service.find_terms(
                case["text"],
                include_negated=open_statuses,
                include_uncertain=open_statuses,
                include_historical=open_statuses,
                include_family=open_statuses,
            )
            resolved = {_norm(c.matched_text) for c in service.resolve_spans(spans)}
            for item in noise:
                if _norm(item) in resolved:
                    violations.append(f"{path.stem}:{case['id']}: {item!r}")
    return sorted(violations)


def collect_pair_collisions(service, limit: int = 60) -> list[str]:
    if not MINIMAL_PAIRS.is_file():
        return []
    pairs = json.loads(MINIMAL_PAIRS.read_text()).get("pairs", [])[:limit]
    collisions: list[str] = []
    for pair in pairs:
        resolved = {}
        for side in ("query_a", "query_b"):
            spans = service.find_terms(pair[side])
            resolved[side] = {c.canonical_id for c in service.resolve_spans(spans) if c.canonical_id}
        shared = resolved["query_a"] & resolved["query_b"]
        if shared:
            collisions.append(f"{pair['query_a']!r} vs {pair['query_b']!r} -> {sorted(shared)}")
    return sorted(collisions)


def main() -> int:
    from medterm4ds.services.extraction import ExtractionService

    service = ExtractionService(device="cpu")
    payload = {
        "_comment": COMMENT,
        "misses": collect(service),
        "noise": collect_noise(service),
        "pair_collisions": collect_pair_collisions(service),
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(
        f"wrote {len(payload['misses'])} misses, {len(payload['noise'])} noise, "
        f"{len(payload['pair_collisions'])} collisions -> {OUT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
