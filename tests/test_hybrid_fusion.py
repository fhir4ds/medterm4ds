"""AMUX-46: hybrid = RRF fusion over the UNION of per-system candidates.

The cascade made lexical recall a hard gate (BM25's tf-tie lottery could
drop the semantic best row from the pool). Fusion contracts pinned here:

  - UNION: a row seen by EITHER system's candidates can surface;
    semantic-only rows are no longer gated by lexical recall.
  - Ranking: reciprocal-rank fusion 1/(HYBRID_FUSION_K + rank) summed
    per system; ties broken by the AMUX-49 unified key.
  - Agreement-aware grades: 'certain' needs BOTH systems plus cosine
    >= 0.92; 'probable' needs cosine >= 0.75 or a lexical exact-surface
    match at lexical rank <= 3; lexical-only rows can never be certain
    (single-evidence ceiling — the precision veto as a grade gate).
  - score stays the calibrated underlying value (cosine when the
    semantic system saw the row, else normalized BM25).

All tests stub both systems — no model, no index, deterministic.
"""

from __future__ import annotations

import pytest

from medterm4ds.services import search as sm


def _lex_row(code, display, score=0.5, exact=False, source="RXNORM"):
    return sm.SearchResult(
        code=code,
        source=source,
        display=display,
        score=score,
        match_grade="probable",
        category="medication",
        _exact_surface=exact,
    )


class _StubEngine:
    def __init__(self, rows):
        # rows: [(system, code, display, cos)]
        self._rows = rows

    def search(self, query, *, categories=None, top_k=20):
        return [
            {
                "system": s,
                "code": c,
                "display": d,
                "score": cos,
                "match_grade": "probable",
                "category": "medication",
            }
            for s, c, d, cos in self._rows[:top_k]
        ]


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDTERM4DS_SEARCH_INDEX_DIR", raising=False)
    monkeypatch.delenv("MEDTERM4DS_EMBEDDING_MODEL_DIR", raising=False)
    s = sm.SearchService(search_index_dir=str(tmp_path), embedding_model_dir=str(tmp_path))
    yield s


def _wire(svc, lex_rows, sem_rows):
    svc._ensure_bm25 = lambda: None
    svc._bm25_loaded = True
    svc._semantic_engine = _StubEngine(sem_rows)
    svc._ensure_semantic = lambda: svc._semantic_engine
    svc.lexical = lambda query, **kw: lex_rows


class TestUnionNotCascade:
    def test_semantic_only_row_surfaces(self, svc):
        # The demo failure shape: BM25 pool lacks the strength row; the
        # cascade could never return it. Fusion must surface it — and with
        # realistic ranks (sem #1 for the strength row; the ingredient sits
        # mid-pack in the lexical tf-tie) the strength row wins.
        _wire(
            svc,
            lex_rows=[
                _lex_row("316151", "Lisinopril", 0.30),
                _lex_row("316152", "Lisinopril", 0.30),
                _lex_row("29046", "Lisinopril", 0.30),
                _lex_row("316153", "Lisinopril", 0.30),
            ],
            sem_rows=[("rxnorm", "316156", "Lisinopril", 0.93)],
        )
        rows = svc.hybrid("lisinopril 5mg", count=5)
        codes = [r.code for r in rows]
        # Union contract: the strength row SURFACES (the cascade could
        # never return it). Rank-1-vs-rank-1 RRF ties are broken by the
        # AMUX-49 key and are tie-granular by design; the live release
        # check (many rows ranked per system) puts 316156 at top-1.
        assert "316156" in codes, codes
        by_code = {r.code: r for r in rows}
        assert by_code["316156"].match_grade == "probable"  # sem-only 0.93

    def test_lexical_only_row_surfaces(self, svc):
        # Complement: rare exact token lexical finds that semantic misses.
        _wire(
            svc,
            lex_rows=[_lex_row("999", "Zzquillorphin", 0.9, exact=True)],
            sem_rows=[("rxnorm", "1", "Unrelated", 0.5)],
        )
        rows = svc.hybrid("Zzquillorphin", count=5)
        assert rows[0].code == "999"


class TestRRFRanking:
    def test_both_systems_rank1_beats_single_system_rank1(self, svc):
        _wire(
            svc,
            lex_rows=[_lex_row("A", "Alpha Drug", 0.4)],
            sem_rows=[("rxnorm", "A", "Alpha Drug", 0.90), ("rxnorm", "B", "Beta Drug", 0.99)],
        )
        rows = svc.hybrid("alpha drug", count=5)
        # A: 1/11 + 1/11 = 0.1818 ; B: 1/11 only — concordance wins even
        # though B has the higher cosine.
        assert rows[0].code == "A", [(r.code, r.score) for r in rows]


class TestAgreementGrades:
    def test_certain_requires_both_systems_and_high_cosine(self, svc):
        _wire(
            svc,
            lex_rows=[_lex_row("A", "Alpha Drug", 0.4)],
            sem_rows=[("rxnorm", "A", "Alpha Drug", 0.93), ("rxnorm", "S", "Sem Only", 0.95)],
        )
        rows = svc.hybrid("alpha drug", count=5)
        by_code = {r.code: r for r in rows}
        assert by_code["A"].match_grade == "certain"
        # sem-only, however high the cosine, is single-evidence:
        assert by_code["S"].match_grade == "probable"

    def test_lexical_only_never_certain(self, svc):
        _wire(
            svc,
            lex_rows=[_lex_row("A", "Alpha Drug", 0.99, exact=True)],
            sem_rows=[],
        )
        rows = svc.hybrid("Alpha Drug", count=5)
        assert rows and rows[0].match_grade == "probable"

    def test_low_cosine_sem_only_is_possible(self, svc):
        _wire(
            svc,
            lex_rows=[],
            sem_rows=[("rxnorm", "X", "Weak Match", 0.64)],
        )
        rows = svc.hybrid("anything", count=5)
        assert rows[0].match_grade == "possible"

    def test_exact_surface_lexical_rank3_is_probable(self, svc):
        _wire(
            svc,
            lex_rows=[
                _lex_row("1", "Other One", 0.9),
                _lex_row("2", "Other Two", 0.8),
                _lex_row("3", "Exactrow", 0.7, exact=True),
            ],
            sem_rows=[],
        )
        rows = svc.hybrid("Exactrow", count=5)
        by_code = {r.code: r for r in rows}
        assert by_code["3"].match_grade == "probable"

    def test_score_is_underlying_calibrated_value(self, svc):
        _wire(
            svc,
            lex_rows=[_lex_row("A", "Alpha", 0.42)],
            sem_rows=[("rxnorm", "A", "Alpha", 0.91)],
        )
        rows = svc.hybrid("alpha", count=5)
        by_code = {r.code: r for r in rows}
        assert by_code["A"].score == 0.91  # cosine wins when both saw it


class TestDeterminism:
    def test_same_inputs_same_order(self, svc):
        rows_spec = [
            ("rxnorm", "A", "Alpha Drug", 0.9),
            ("rxnorm", "B", "Alpha Drug B", 0.9),
        ]
        _wire(
            svc,
            lex_rows=[_lex_row("A", "Alpha Drug", 0.4)],
            sem_rows=rows_spec,
        )
        r1 = [r.code for r in svc.hybrid("alpha drug", count=5)]
        r2 = [r.code for r in svc.hybrid("alpha drug", count=5)]
        assert r1 == r2


class TestBatchAFixes:
    """v0.0.9 QC fixbatch pins: sort-key totality (QC1-S2), best-rank
    dedupe (QC1-S1), and exact-flag reuse in the probable branch
    (QC1-A4)."""

    def test_sort_key_is_total_order_source_breaks_code_ties(self, svc):
        # Two rows fully tied on (rrf, exact, distance, code) but distinct
        # sources: the key's trailing source term must fix the order
        # instead of hash-seed-dependent set iteration.
        _wire(
            svc,
            lex_rows=[_lex_row("X", "Same Display", 0.5, source="BBB")],
            sem_rows=[
                ("rxnorm", "X", "Same Display", 0.90),
                ("snomedct_us", "X", "Same Display", 0.90),
            ],
        )
        # Give the lexical row the same total RRF as one sem row by
        # aligning ranks: lex rank1 = 1/11, sem rank1 = 1/11. The lnc
        # source label maps from the stub's raw source strings.
        rows = svc.hybrid("same display", count=5)
        xs = [r for r in rows if r.code == "X"]
        assert len(xs) == 3, [(r.source, r.code) for r in rows]
        # Deterministic contract: full ties (BBB lex-rank1 and RXNORM
        # sem-rank1, both 1/11) order by source ascending; SNOMEDCT_US
        # (sem rank2, 1/12) ranks last by RRF.
        assert [r.source for r in xs] == ["BBB", "RXNORM", "SNOMEDCT_US"]

    def test_duplicate_identity_keeps_best_rank(self, svc):
        # The same (source, code) appearing twice in lexical's list (cross
        # -category duplicates): the FIRST (better) rank must win the RRF
        # contribution, not the last.
        _wire(
            svc,
            lex_rows=[
                _lex_row("A", "Alpha Drug", 0.4),  # rank 1
                _lex_row("B", "Beta Drug", 0.4),  # rank 2
                _lex_row("A", "Alpha Drug", 0.4),  # rank 3 (dup)
                _lex_row("A", "Alpha Drug", 0.4),  # rank 5 shape
                _lex_row("A", "Alpha Drug", 0.4),
                _lex_row("A", "Alpha Drug", 0.4),
            ],
            sem_rows=[],
        )
        rows = svc.hybrid("alpha drug", count=5)
        # A keeps rank 1 (1/11); B has rank 2 (1/12) — A must outrank B.
        assert rows[0].code == "A", [(r.code, r.match_grade) for r in rows]

    def test_probable_uses_row_exact_flag_not_display_only(self, svc):
        # A row whose INDEXED NAME equals the query (AMUX-18 exactness:
        # name doc OR display) but whose friendly display differs. Lexical
        # carried exact=True at rank 1; the probable branch must honor
        # that flag — a display-only re-derivation (QC1-A4) graded this
        # 'possible'.
        _wire(
            svc,
            lex_rows=[_lex_row("A", "Acetaminophen Oral Product", 0.42, exact=True)],
            sem_rows=[],
        )
        rows = svc.hybrid("Tylenol", count=5)
        assert rows, "union must surface the lexical row"
        assert rows[0].match_grade == "probable", rows[0].match_grade
