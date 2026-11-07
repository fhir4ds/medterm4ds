"""Corpus-driven regression tests for the extraction pipeline.

The JSON corpora under tests/corpora/ are behavioural fixtures produced by the
cross-family NER battery (2026-11-07): mentions that must resolve with expected
content, note furniture that must NOT resolve, co-prescription lists that must
stay separate mentions, and minimal qualifier pairs that must not collide.

These need the GLiNER weights and the search index, so every test here is
``slow``-marked and excluded from the default gate (pyproject addopts). Run:

    pytest tests/test_corpora_ner.py -m slow -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("medspacy")
pytest.importorskip("transformers")

from medterm4ds.services.extraction import ExtractionService

CORPORA = Path(__file__).with_name("corpora")
CORPUS_FILES = sorted(
    p for p in CORPORA.glob("*_corpus.json")
)
MINIMAL_PAIRS = CORPORA / "minimal_pairs.json"
KNOWN_GAPS = CORPORA / "known_gaps.json"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def service():
    return ExtractionService(device="cpu")


def _new_gaps(kind: str, found: list[str]) -> list[str]:
    """Gaps that are not in the accepted baseline (ratchet semantics)."""
    known: set[str] = set()
    if KNOWN_GAPS.is_file():
        known = set(json.loads(KNOWN_GAPS.read_text()).get(kind, []))
    return sorted(set(found) - known)


def _cases(path: Path) -> list[dict]:
    return json.loads(path.read_text()).get("cases", [])


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _case_id(path: Path, case: dict) -> str:
    return f"{path.stem}:{case['id']}"


def _resolve_case(service, case: dict):
    open_statuses = bool(case.get("include_non_affirmed"))
    spans = service.find_terms(
        case["text"],
        include_negated=open_statuses,
        include_uncertain=open_statuses,
        include_historical=open_statuses,
        include_family=open_statuses,
    )
    concepts = service.resolve_spans(spans)
    return spans, concepts


@pytest.mark.parametrize("path", CORPUS_FILES, ids=lambda p: p.stem)
def test_corpus_noise_never_resolves(service, path):
    """Note furniture, codes and markup must not become clinical concepts."""
    failures = []
    for case in _cases(path):
        noise = case.get("noise") or []
        if not noise:
            continue
        _, concepts = _resolve_case(service, case)
        mapping = {_norm(c.matched_text): c.display for c in concepts}
        for item in noise:
            if _norm(item) in mapping:
                failures.append(f"{_case_id(path, case)}: {item!r}")
    new = _new_gaps("noise", failures)
    assert not new, (
        "NEW furniture resolving as concepts (not in known_gaps.json):\n"
        + "\n".join(new)
    )


@pytest.mark.parametrize("path", CORPUS_FILES, ids=lambda p: p.stem)
def test_corpus_lists_stay_separate(service, path):
    """Co-prescription lists must not collapse into one compound mention."""
    failures = []
    for case in _cases(path):
        for first, second in case.get("expect_separate") or []:
            spans, _ = _resolve_case(service, case)
            a, b = _norm(first), _norm(second)
            for span in spans:
                text = _norm(span.text)
                if a in text and b in text:
                    failures.append(f"{_case_id(path, case)}: merged into {span.text!r}")
    assert not failures, "co-prescription lists merged:\n" + "\n".join(failures)


@pytest.mark.parametrize("path", CORPUS_FILES, ids=lambda p: p.stem)
def test_corpus_mentions_resolve(service, path):
    """Mentions with declared expectations must resolve with the expected content.

    Reports every miss as one line so a corpus run is a work list, not a binary
    failure: vocabulary gaps are expected to appear here until they are fixed.
    """
    failures = []
    for case in _cases(path):
        mentions = case.get("mentions") or []
        if not mentions:
            continue
        spans, concepts = _resolve_case(service, case)
        span_texts = {_norm(s.text) for s in spans}
        for mention in mentions:
            expected = [t.lower() for t in mention.get("expect", [])]
            if not expected:
                continue
            related = [
                c for c in concepts
                if _norm(c.matched_text) in span_texts
                and (_norm(c.matched_text) in _norm(mention["text"])
                     or _norm(mention["text"]) in _norm(c.matched_text))
            ]
            if not related:
                failures.append(f"{_case_id(path, case)}: {mention['text']!r} unresolved")
                continue
            mode = mention.get("mode", "any")
            displays = [c.display.lower() for c in related]
            hit = any(
                all(t in d for t in expected) if mode == "all"
                else any(t in d for t in expected)
                for d in displays
            )
            if not hit:
                failures.append(
                    f"{_case_id(path, case)}: {mention['text']!r} -> {displays[:2]}"
                )
    new_misses = _new_gaps("misses", failures)
    assert not new_misses, (
        "NEW corpus misses (not in tests/corpora/known_gaps.json):\n"
        + "\n".join(new_misses)
        + "\n\nIf a change deliberately fixes a known miss, regenerate the baseline "
        "with scripts/update_corpus_baseline.py and commit the diff."
    )


@pytest.mark.skipif(
    not MINIMAL_PAIRS.is_file() or os.environ.get("MEDTERM4DS_CORPUS_PAIRS") != "1",
    reason="opt-in vocabulary-drift probe; FAISS tie-breaks make exact collision "
           "sets non-deterministic across runs",
)
def test_minimal_pairs_do_not_collide(service):
    """A polarity swap must not resolve both sides to the same canonical_id."""
    pairs = json.loads(MINIMAL_PAIRS.read_text()).get("pairs", [])[:60]
    assert pairs, "minimal_pairs.json is empty"
    collisions = []
    for pair in pairs:
        resolved = {}
        for side in ("query_a", "query_b"):
            spans = service.find_terms(pair[side])
            concepts = service.resolve_spans(spans)
            resolved[side] = {c.canonical_id for c in concepts if c.canonical_id}
        shared = resolved["query_a"] & resolved["query_b"]
        if shared:
            collisions.append(f"{pair['query_a']!r} vs {pair['query_b']!r} -> {sorted(shared)}")
    new = _new_gaps("pair_collisions", collisions)
    assert not new, "NEW qualifier-pair collisions (not in known_gaps.json):\n" + "\n".join(new)
