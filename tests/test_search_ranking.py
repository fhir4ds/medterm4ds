"""Lexical search ranking fixes: AMUX-18 + AMUX-20 (2026-11-02).

AMUX-18 — exact-surface-first tie-breaking. Many rows tie at tf=1 for a
token (38 contain 'tylenol'), so top-5 was decided by document-length
normalisation and the row literally named 'Tylenol' lost to longer
combination-product rows. The lexical rank key is now
(exact_match, bm25_score, code): deterministic, query-time only, no index
rebuild, lexical stays model-free (semantic distance stays in the hybrid
re-rank).

AMUX-20 — tokenizer coverage. The index-build tokenizer was ASCII
``[a-zA-Z]{3,}``, so alphanumeric tokens (A1c, HbA1c, B12) and 2-char
acronyms (MI, PE, PT) have no posting keys and were unsearchable. The
query tokenizer now preserves them; posting lookup falls back to their
>= 3-letter runs ('hba1c' -> 'hba'), and tokens with no posting
candidates are recovered by a surface-name pass (full query-token
coverage over names + friendly displays, acronym-casing corroboration).

All tests run against synthetic BM25 index JSON files — no model, no
network, no downloaded artifacts.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import pytest

from medterm4ds.services.search import (
    SearchService,
    _posting_candidates,
    _query_tokens,
    _surface_key,
)

# Build-side tokenizer (mirrors build_bm25_v2.tokenize): ASCII letter
# runs of >= 3 chars, lowercased — digits and 2-char acronyms drop out.
_BUILD_TOKEN_RE = re.compile(r"[a-zA-Z]{3,}")


def _build_index(category: str, docs: list[dict], *, avg_doc_length: float | None = None) -> dict:
    """Build one minimal v2 BM25 index dict from {code, system, name, friendly?} docs."""
    postings: dict[str, list[list]] = {}
    names, friendly, codes, systems, doc_lengths = [], [], [], [], []
    for rid, d in enumerate(docs):
        names.append(d["name"])
        codes.append(d["code"])
        systems.append(d["system"])
        friendly.append(d.get("friendly") or d["name"])
        toks = [t.lower() for t in _BUILD_TOKEN_RE.findall(d["name"])]
        doc_lengths.append(max(len(toks), 1))
        for tok, tf in Counter(toks).items():
            postings.setdefault(tok, []).append([rid, float(tf)])
    n = len(docs)
    idf = {t: math.log(1.0 + n / len(pl)) for t, pl in postings.items()}
    return {
        "version": 2,
        "category": category,
        "num_records": n,
        "avg_doc_length": avg_doc_length or (sum(doc_lengths) / max(n, 1)),
        "names": names,
        "postings": postings,
        "idf": idf,
        "doc_lengths": doc_lengths,
        "rid_to_friendly_name": friendly,
        "rid_to_code": codes,
        "rid_to_system": systems,
        "rid_to_canonical_code": list(codes),
        "rid_to_canonical_system": list(systems),
        "rid_to_ingredient_codes": [[] for _ in docs],
    }


@pytest.fixture()
def search_svc(tmp_path: Path) -> SearchService:
    def write(category: str, docs: list[dict], **kw) -> None:
        idx = _build_index(category, docs, **kw)
        (tmp_path / f"{category}_bm25.json").write_text(json.dumps(idx))

    # AMUX-18 scenario: the row literally named 'Tylenol' ties at tf=1
    # with combination products; pre-fix, tf/doc-length put the exact row
    # behind 'Tylenol PM Extra Strength Tylenol' (tf=2) and shorter docs.
    write(
        "medication",
        [
            {"code": "202433", "system": "rxnorm", "name": "Tylenol", "friendly": "Acetaminophen"},
            {"code": "1001", "system": "rxnorm", "name": "Tylenol PM Extra Strength Tylenol"},
            {"code": "1002", "system": "rxnorm", "name": "Tylenol Gelcap Extra Strength"},
            {
                "code": "1003",
                "system": "rxnorm",
                "name": "Acetaminophen Oral Product",
                "friendly": "Acetaminophen",
            },
        ],
        avg_doc_length=20.0,
    )

    # AMUX-20 scenarios: 2-char acronyms and alphanumeric tokens that the
    # build tokenizer dropped from the postings entirely.
    write(
        "condition",
        [
            {"code": "I21", "system": "icd10cm", "name": "Acute Myocardial Infarction (MI)"},
            {"code": "C1", "system": "snomed", "name": "Mi-2 Ab Binding"},
        ],
        avg_doc_length=20.0,
    )
    write(
        "lab",
        [
            {
                "code": "L1",
                "system": "lnc",
                "name": "Hemoglobin A1c/Hemoglobin.Total",
                "friendly": "A1C",
            },
            {"code": "L2", "system": "lnc", "name": "HbA1c measurement"},
            {"code": "L3", "system": "lnc", "name": "Hemoglobin B"},
            {"code": "L4", "system": "lnc", "name": "Prothrombin Time (PT) Test"},
        ],
        avg_doc_length=20.0,
    )

    return SearchService(
        search_index_dir=str(tmp_path), embedding_model_dir=str(tmp_path / "no_model")
    )


# --- Tokenizer + posting candidates (AMUX-20) -------------------------


class TestQueryTokens:
    def test_preserves_alphanumeric_tokens(self):
        assert "a1c" in _query_tokens("Hemoglobin A1c")
        assert "hba1c" in _query_tokens("HbA1c")
        assert "b12" in _query_tokens("Vitamin B12")

    def test_preserves_two_char_acronyms(self):
        assert _query_tokens("MI") == ["mi"]
        assert _query_tokens("PE") == ["pe"]
        assert _query_tokens("PT") == ["pt"]

    def test_single_chars_still_dropped(self):
        assert _query_tokens("a I x") == []


class TestPostingCandidates:
    def test_ordinary_token_maps_to_itself(self):
        postings = {"tylenol": [[0, 1.0]]}
        assert _posting_candidates("tylenol", postings) == ["tylenol"]

    def test_alphanumeric_falls_back_to_letter_runs(self):
        postings = {"hba": [[0, 1.0]]}
        assert _posting_candidates("hba1c", postings) == ["hba"]

    def test_two_char_acronym_has_no_candidates(self):
        assert _posting_candidates("mi", {"acute": []}) == []

    def test_digit_only_letter_runs_have_no_candidates(self):
        # 'a1c' -> letter runs 'a', 'c' are both < 3 chars
        assert _posting_candidates("a1c", {"acute": []}) == []


class TestSurfaceKey:
    def test_case_and_punctuation_insensitive(self):
        assert _surface_key("Tylenol") == _surface_key("  TYLENOL ")
        assert _surface_key("A1C") == "a1c"


# --- AMUX-18: exact-surface-first tie-breaking -------------------------


class TestExactSurfaceFirst:
    def test_exact_name_row_wins_despite_lower_bm25(self, search_svc):
        # 'Tylenol PM Extra Strength Tylenol' has tf=2 (higher BM25) but
        # the row literally named 'Tylenol' must rank first.
        results = search_svc.lexical("Tylenol", count=5)
        assert results, "lexical('Tylenol') must return rows"
        assert results[0].code == "202433"
        assert results[0]._exact_surface is True

    def test_exact_friendly_display_also_wins(self, search_svc):
        # Both acetaminophen rows tie on tf=1; the one whose FRIENDLY
        # display equals the query wins over the longer oral-product row.
        results = search_svc.lexical("Acetaminophen", count=5)
        assert results[0].code == "1003"  # friendly display match
        assert results[0]._exact_surface is True

    def test_ordering_is_deterministic(self, search_svc):
        runs = [[r.code for r in search_svc.lexical("Tylenol", count=5)] for _ in range(3)]
        assert runs[0] == runs[1] == runs[2]

    def test_exact_flag_not_leaked_into_to_dict(self, search_svc):
        r = search_svc.lexical("Tylenol", count=1)[0]
        assert "_exact_surface" not in r.to_dict()
        assert set(r.to_dict()) == {
            "code",
            "source",
            "system",
            "display",
            "score",
            "match_grade",
            "category",
        }


# --- AMUX-20: acronym / alphanumeric searchability ---------------------


class TestAcronymSearchability:
    def test_two_char_acronym_returns_acronym_rows(self, search_svc):
        results = search_svc.lexical("MI", count=5)
        assert results, "MI was unsearchable pre-fix"
        displays = [r.display for r in results]
        assert any("Myocardial Infarction" in d for d in displays)

    def test_cased_acronym_outranks_plain_word_hit(self, search_svc):
        # '(MI)' carries the acronym the user typed; 'Mi-2 Ab Binding' is
        # a plain-word collision and must rank below every cased hit.
        results = search_svc.lexical("MI", count=5)
        cased = [r for r in results if "Myocardial" in r.display]
        plain = [r for r in results if "Mi-2" in r.display]
        assert cased and plain
        assert min(results.index(r) for r in cased) < min(results.index(r) for r in plain)

    def test_alphanumeric_token_searchable(self, search_svc):
        results = search_svc.lexical("A1c", count=5)
        assert results, "A1c was unsearchable pre-fix"
        # The row whose friendly display is literally 'A1C' wins.
        assert results[0].display == "A1C"
        assert results[0]._exact_surface is True
        assert all("Hemoglobin" in r.display or "A1C" == r.display for r in results)

    def test_hba1c_matches_via_letter_run_posting(self, search_svc):
        results = search_svc.lexical("HbA1c", count=5)
        assert any(r.display == "HbA1c measurement" for r in results)

    def test_pt_acronym_searchable(self, search_svc):
        results = search_svc.lexical("PT", count=5)
        assert any("Prothrombin Time" in r.display for r in results)

    def test_surface_pass_requires_full_query_coverage(self, search_svc):
        # 'MI panel' must not surface rows that contain only 'mi'.
        results = search_svc.lexical("MI panel", count=5)
        assert results == []


# --- Ordinary-query non-regression ------------------------------------


class TestOrdinaryQueriesUnchanged:
    def test_rows_without_token_never_match(self, search_svc):
        results = search_svc.lexical("aspirin", count=5)
        assert results == []

    def test_plain_query_ranks_by_bm25_with_exact_first(self, search_svc):
        results = search_svc.lexical("tylenol gelcap", count=5)
        assert results, "multi-token query must still hit postings"
        # 'Tylenol Gelcap Extra Strength' is the only doc with both tokens
        assert results[0].code == "1002"
        assert 0.0 < results[0].score <= 1.0

    def test_lowercase_query_still_matches_cased_names(self, search_svc):
        results = search_svc.lexical("tylenol", count=5)
        assert results[0].code == "202433"


# --- Unified surface-distance tie-break (lexical + semantic) ----------


class TestSurfaceDistance:
    def test_zero_for_exact_and_basic_edits(self):
        from medterm4ds.services.search import _surface_distance as d

        assert d("tylenol", "Tylenol") == 0
        # normalized by the longer surface: 3 edits over 10 chars
        # ('tylenol' vs 'tylenol pm' — the space is part of the key)
        assert abs(d("tylenol", "Tylenol PM") - 3 / 10) < 1e-9
        assert abs(d("abcd", "abdc") - 1 / 4) < 1e-9  # transposition once
        assert d("ab", "") == 1.0 and d("", "ab") == 1.0

    def test_containment_beats_short_unrelated(self):
        from medterm4ds.services.search import _surface_distance as d

        # normalization makes surfaces CONTAINING the query closer than
        # short unrelated ones (raw edit distance is length-biased).
        assert d("tylenol pm", "Tylenol PM Extra Strength Tylenol") < d(
            "tylenol pm", "Acetaminophen"
        )

    def test_closer_surface_ranks_lower_distance(self):
        from medterm4ds.services.search import _surface_distance as d

        assert d("tylenol", "Tylenol PM") < d("tylenol", "Tylenol Cold Multi Symptom")


class TestLexicalDistanceTiebreak:
    def test_query_containing_surface_ranks_first(self, search_svc):
        """For 'Tylenol PM' the row containing the query surface ranks
        first (higher tf AND closest surface agree here); the
        equal-score distance arbitration itself is pinned precisely by
        the semantic stub test below (the key is shared code)."""
        res = search_svc.lexical("Tylenol PM", count=3)
        names = [r.display for r in res]
        assert names, "fixture unexpectedly empty"
        assert "PM" in names[0], names


class TestSemanticUnifiedTiebreak:
    def test_semantic_ties_resolve_by_surface_distance_then_code(self, search_svc, monkeypatch):
        """Equal-cosine rows must order (exact, score, distance, code) —
        the SAME key as lexical — instead of FAISS iteration order."""

        class StubEngine:
            def search(self, query, *, categories=None, top_k=20):
                return [
                    {
                        "code": "9999",
                        "system": "lnc",
                        "display": "zzz unrelated far",
                        "score": 0.90,
                        "match_grade": "probable",
                        "category": "lab",
                    },
                    {
                        "code": "0001",
                        "system": "lnc",
                        "display": "Hemoglobin A1c Panel",
                        "score": 0.90,
                        "match_grade": "probable",
                        "category": "lab",
                    },
                    {
                        "code": "0002",
                        "system": "lnc",
                        "display": "Hemoglobin A1c",
                        "score": 0.90,
                        "match_grade": "probable",
                        "category": "lab",
                    },
                    # exact surface with a LOWER cosine must still win (AMUX-18
                    # semantics preserved on the semantic path).
                    {
                        "code": "5555",
                        "system": "lnc",
                        "display": "Hemoglobin A1c measurement",
                        "score": 0.85,
                        "match_grade": "probable",
                        "category": "lab",
                    },
                ]

        monkeypatch.setattr(search_svc, "_ensure_semantic", lambda: StubEngine())
        res = search_svc.semantic("Hemoglobin A1c", count=4)
        codes = [r.code for r in res]
        assert codes == ["0002", "0001", "9999", "5555"], codes


class TestOrphanPassGradeCap:
    """QC1-T3 (v0.0.9 QC): rows scored ONLY by the AMUX-20 orphan surface
    pass carry substring-level evidence ('MS' matches 'LC/MS/MS' mass-spec
    method notation — Tacrolimus/Metanephrines lab rows, not Multiple
    Sclerosis). The max-idf proxy plus the short-doc tf term inflates the
    normalized score past the certain cutoff; the grade must cap at
    'probable' unless the row's surface IS the query (exact match)."""

    @pytest.fixture()
    def ms_svc(self, tmp_path: Path) -> SearchService:
        idx = _build_index(
            "lab",
            [
                {
                    "code": "74097-7",
                    "system": "lnc",
                    "name": "Tacrolimus:MCnc:Pt:Bld:Qn:LC/MS/MS",
                    "friendly": "Tacrolimus",
                },
                {
                    "code": "104630-9",
                    "system": "lnc",
                    "name": "Metanephrines:MRat:24H:Urine:Qn:LC/MS/MS",
                    "friendly": "Metanephrines",
                },
                {"code": "E1", "system": "snomed", "name": "Multiple Sclerosis", "friendly": "MS"},
            ],
            avg_doc_length=20.0,
        )
        (tmp_path / "lab_bm25.json").write_text(json.dumps(idx))
        return SearchService(
            search_index_dir=str(tmp_path), embedding_model_dir=str(tmp_path / "no_model")
        )

    def test_orphan_only_rows_never_certain(self, ms_svc):
        results = ms_svc.lexical("MS", count=10)
        by_code = {r.code: r for r in results}
        # The LC/MS/MS rows surface (token coverage is real evidence)…
        assert "74097-7" in by_code and "104630-9" in by_code
        # …but their normalized score (~1.0 via the max-idf proxy + short
        # doc-length term) must NOT buy 'certain' — substring coincidence
        # is single weak evidence.
        assert by_code["74097-7"].match_grade == "probable"
        assert by_code["104630-9"].match_grade == "probable"

    def test_exact_surface_orphan_row_stays_certain(self, ms_svc):
        results = ms_svc.lexical("MS", count=10)
        by_code = {r.code: r for r in results}
        # Exact-surface rows are exempt from the cap: 'MS' IS the query.
        assert by_code["E1"].match_grade == "certain"
