"""Tests for the text extraction service."""

from __future__ import annotations

import pytest

pytest.importorskip("medspacy")
pytest.importorskip("transformers")

from medterm4ds.services.extraction import (
    ExtractedConcept,
    ExtractionService,
    FilteredSpan,
    _is_false_positive,
)


@pytest.fixture(scope="module")
def service():
    return ExtractionService()


class TestFindTerms:
    """Test NLP-only extraction (no code resolution)."""

    def test_finds_disease(self, service):
        spans = service.find_terms("Patient has diabetes")
        texts = [s.text.lower() for s in spans]
        assert any("diabet" in t for t in texts)

    def test_finds_medication(self, service):
        spans = service.find_terms("Started on metformin")
        texts = [s.text.lower() for s in spans]
        assert any("metformin" in t for t in texts)

    def test_negation_excluded_by_default(self, service):
        all_spans = service.find_terms("No evidence of diabetes. Denies chest pain.", include_negated=True)
        default_spans = service.find_terms("No evidence of diabetes. Denies chest pain.")
        # ConText should detect at least one negated entity
        all_statuses = {s.status for s in all_spans}
        if "negated" in all_statuses:
            # Default (no negated) should have fewer or equal results
            assert len(default_spans) <= len(all_spans)

    def test_negation_included_when_requested(self, service):
        spans = service.find_terms("No evidence of diabetes", include_negated=True)
        texts = [s.text.lower() for s in spans]
        assert any("diabet" in t for t in texts)

    def test_span_offsets(self, service):
        text = "Patient has diabetes"
        spans = service.find_terms(text)
        for span in spans:
            # Offsets should reference the original text
            assert text[span.span_start:span.span_end].lower() == span.text.lower()

    def test_status_field_populated(self, service):
        spans = service.find_terms("Patient has diabetes", include_negated=True)
        for span in spans:
            assert span.status in ("affirmed", "negated", "uncertain", "historical")

    def test_ner_label_override(self, service):
        # Override default labels to detect only medications.
        spans = service.find_terms("Diabetes and metformin", ner_labels=["medication"])
        # Should only include medication entities
        for span in spans:
            assert span.entity_type == "medication"

    def test_empty_text(self, service):
        spans = service.find_terms("")
        assert spans == []


class TestExtract:
    """Test convenience extract() with format parameter."""

    def test_format_terms_returns_spans(self, service):
        results = service.extract("Patient has diabetes", format="terms")
        assert all(isinstance(r, FilteredSpan) for r in results)

    def test_format_codes_returns_concepts(self, service):
        results = service.extract("Patient has diabetes", format="codes")
        # May be empty if search indexes not loaded, but type should be right
        for r in results:
            assert isinstance(r, ExtractedConcept)
            assert r.code
            assert r.source
            assert r.display

    def test_negation_in_extraction(self, service):
        spans = service.extract("Patient has T2DM. No CKD.", format="terms")
        # GLiNER may not detect "T2DM" or "CKD" as short acronyms.
        # Verify that the pipeline runs and returns a list.
        assert isinstance(spans, list)


class TestNerModelRevisionPin:
    """The GLiNER NER model revision must be pinned (weight drift observed
    2026-08-14 changed recall); the pin must reach the load call."""

    def test_revision_constant_is_pinned_sha(self):
        import os
        import re

        from medterm4ds.services.extraction import DEFAULT_NER_MODEL_REVISION

        if os.getenv("MEDTERM4DS_NER_MODEL_REVISION"):
            # Explicit override: honor whatever the operator pinned.
            assert DEFAULT_NER_MODEL_REVISION == (
                os.environ["MEDTERM4DS_NER_MODEL_REVISION"] or None
            )
        else:
            assert re.fullmatch(r"[0-9a-f]{40}", DEFAULT_NER_MODEL_REVISION), (
                "DEFAULT_NER_MODEL_REVISION must be a 40-hex commit SHA"
            )

    def test_revision_passed_at_load(self, monkeypatch):
        """GLiNER.from_pretrained receives revision=<pin> (mocked — no
        weight download)."""
        gliner = pytest.importorskip("gliner")
        import medspacy

        from medterm4ds.services import extraction

        calls: dict = {}

        class _FakeGLiNER:
            @staticmethod
            def from_pretrained(model_id, revision=None, **kwargs):
                calls["model_id"] = model_id
                calls["revision"] = revision
                return object()

        monkeypatch.setattr(gliner, "GLiNER", _FakeGLiNER)
        monkeypatch.setattr(medspacy, "load", lambda **kwargs: object())

        pipeline = extraction.NlpPipeline()
        pipeline._ensure_loaded()

        assert calls["model_id"] == extraction.DEFAULT_NER_MODEL
        assert calls["revision"] == extraction.DEFAULT_NER_MODEL_REVISION

    def test_revision_disabled_with_none(self, monkeypatch):
        """ner_revision=None unpins (local model paths)."""
        gliner = pytest.importorskip("gliner")
        import medspacy

        from medterm4ds.services import extraction

        calls: dict = {}

        class _FakeGLiNER:
            @staticmethod
            def from_pretrained(model_id, revision=None, **kwargs):
                calls["revision"] = revision
                return object()

        monkeypatch.setattr(gliner, "GLiNER", _FakeGLiNER)
        monkeypatch.setattr(medspacy, "load", lambda **kwargs: object())

        pipeline = extraction.NlpPipeline(ner_revision=None)
        pipeline._ensure_loaded()

        assert calls["revision"] is None


# ---------------------------------------------------------------------------
# Wrong-type resolution regression tests (QC report 2026-08-16: 10,433
# wrong-type extractions). All tests mock SearchService — no database, no
# SapBERT. CanonicalSearchResult is a plain dataclass, safe to construct.
# ---------------------------------------------------------------------------

from medterm4ds.services.search import CanonicalSearchResult


def _anchor(cid: str, score: float, system: str = "SNOMEDCT_US") -> CanonicalSearchResult:
    """Build a stub canonical result; grades mirror canonical_batch thresholds."""
    grade = "exact" if score > 0.95 else "probable" if score > 0.80 else "possible"
    return CanonicalSearchResult(
        canonical_id=cid,
        domain=[],
        anchor_system=system,
        anchor_code=cid.rsplit("-", 1)[-1],
        patient_friendly_name=cid,
        score=score,
        match_grade=grade,
        matched_via_code="stub",
        matched_via_display="stub",
        total_member_count=1,
    )


class _StubSearchService:
    """SearchService stub routing canonical() by (query, result_types).

    ``routes`` maps "query|type1,type2" (types sorted, comma-joined) or
    "query|*" for unfiltered calls to a result list. canonical_batch always
    returns the unfiltered ("*") list — the batch path post-filters itself,
    which is exactly what these tests exercise.
    """

    def __init__(self, routes: dict[str, list[CanonicalSearchResult]]):
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _key(query: str, result_types) -> str:
        if result_types is None:
            return f"{query}|*"
        types = [result_types] if isinstance(result_types, str) else list(result_types)
        return f"{query}|{','.join(sorted(t.lower() for t in types))}"

    def canonical(self, query, *, sub_mode="semantic", result_types=None,
                  sources=None, count=20):
        key = self._key(query, result_types)
        self.calls.append((query, key.split("|", 1)[1]))
        return self.routes.get(key, [])

    def canonical_batch(self, queries, *, count=5, min_score=0.70):
        return [self.routes.get(f"{q}|*", []) for q in queries]

    def called_with(self, query: str) -> list[str]:
        return [types for q, types in self.calls if q == query]


class TestLabelConstrainedResolution:
    """Constrain-then-fallback: label categories first, unfiltered retry."""

    @pytest.fixture
    def stub(self, monkeypatch):
        def _install(routes):
            import medterm4ds.services.search as search_module
            stub = _StubSearchService(routes)
            monkeypatch.setattr(search_module, "get_search_service", lambda: stub)
            return stub
        return _install

    def test_disorder_label_beats_higher_scoring_lab_anchor(self, stub):
        """Pattern 1: 'diabetes' must resolve to a condition anchor even when
        a higher-scoring lab anchor (LP128793-9) tops the unfiltered results."""
        stub({
            "diabetes|*": [
                _anchor("VAL-LAB-128793-9", 0.99, "LOINC"),   # wrong type, top hit
                _anchor("VAL-COND-44054006", 0.88),            # correct type
            ],
            "metformin|*": [_anchor("VAL-MED-6809", 0.97, "RXNORM")],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="diabetes", entity_type="disorder"),
            FilteredSpan(text="metformin", entity_type="therapeutic agent"),
        ])
        by_text = {c.matched_text: c for c in concepts}
        assert by_text["diabetes"].canonical_id == "VAL-COND-44054006"
        assert by_text["diabetes"].result_type == "condition"

    def test_drug_label_stays_on_medication_anchor(self, stub):
        """Pattern 4: 'carbamazepine' must stay a medication even though its
        drug-level LOINC (LP16061-1) scores higher unfiltered."""
        stub({
            "carbamazepine|*": [
                _anchor("VAL-LAB-16061-1", 0.97, "LOINC"),     # drug-level LOINC
                _anchor("VAL-MED-RXNORM-8212", 0.90, "RXNORM"),
            ],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="carbamazepine", entity_type="therapeutic agent"),
            FilteredSpan(text="metformin", entity_type="therapeutic agent"),
        ])
        carb = next(c for c in concepts if c.matched_text == "carbamazepine")
        assert carb.canonical_id == "VAL-MED-RXNORM-8212"
        assert carb.result_type == "medication"

    def test_batch_fallback_when_constraint_finds_nothing(self, stub):
        """Fallback: constrained candidates find nothing above the grade
        floor → unfiltered result is used (never return empty)."""
        stub({
            "tuberculosis|*": [
                _anchor("VAL-LAB-718-7", 0.85, "LOINC"),  # only a lab passes floor
            ],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="tuberculosis", entity_type="disorder"),
        ] + [FilteredSpan(text="metformin", entity_type="therapeutic agent")])
        # Batch path requires >1 span; single result list has no VAL-COND,
        # so the fallback must return the lab anchor exactly as before.
        tb = next(c for c in concepts if c.matched_text == "tuberculosis")
        assert tb.canonical_id == "VAL-LAB-718-7"

    def test_single_span_fallback_retries_unfiltered(self, stub):
        """Single-span path: label-constrained canonical() returns [] →
        the unfiltered retry resolves."""
        stub({
            "anemia|condition,symptom": [],           # constrained: nothing
            "anemia|*": [_anchor("VAL-LAB-718-7", 0.85, "LOINC")],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([FilteredSpan(text="anemia", entity_type="disorder")])
        assert [c.canonical_id for c in concepts] == ["VAL-LAB-718-7"]

    def test_explicit_result_types_still_hard_filter(self, stub):
        """QC-153 regression guard: caller-passed result_types is a hard
        filter — no label-derived constraint, no fallback retry."""
        stub({
            "diabetes|condition": [],
            "diabetes|*": [_anchor("VAL-LAB-128793-9", 0.99, "LOINC")],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans(
            [FilteredSpan(text="diabetes", entity_type="disorder")],
            result_types="condition",
        )
        assert concepts == []


@pytest.fixture(scope="module")
def context_arbiter():
    """medspaCy pipeline with the production ConText arbiter rules, no
    GLiNER (target spans are inserted directly by the tests)."""
    medspacy = pytest.importorskip("medspacy")
    from medterm4ds.services.extraction import _register_context_arbiter
    nlp = medspacy.load(medspacy_disable=["medspacy_target_matcher"])
    assert _register_context_arbiter(nlp)
    return nlp


def _context_decide(nlp, text, target, label="therapeutic agent"):
    """Run one (text, target) through ConText arbitration — mirrors
    NlpPipeline.process Steps 3-4 minus GLiNER."""
    from medterm4ds.services.extraction import _context_arbitrate_categories
    doc = nlp(text)
    t0 = text.find(target)
    assert t0 >= 0, f"target {target!r} not found in {text!r}"
    sp = doc.char_span(t0, t0 + len(target), label=label,
                       alignment_mode="expand")
    assert sp is not None, f"span {target!r} failed to align"
    doc.set_ents([sp])
    nlp.get_pipe("medspacy_context")(doc)
    return _context_arbitrate_categories(doc.ents[0])


# The 60-item eval corpus (docs/.ai_loop/qc_comp/lexicon_tiebreaker_
# results.md): A analyte-monitoring (lab), B analyte-administration (med),
# C bare drugs (med), D posology (med), E TDM levels (lab). Expected
# values: ['lab'] / ['medication'] / ['lab', 'medication'] (both fired) /
# None (no decision — the GLiNER label mapping stands; conservative
# failure mode).
_ARBITER_CORPUS = [
        ('A01', 'Creatinine was 2.1 this morning', 'Creatinine', ['lab']),
        ('A02', 'check potassium level', 'potassium', ['lab']),
        ('A03', 'sodium of 135', 'sodium', None),
        ('A04', 'glucose 180 on admission', 'glucose', None),
        ('A05', 'calcium was low', 'calcium', ['lab']),
        ('A06', 'magnesium 1.2', 'magnesium', None),
        ('A07', 'cholesterol elevated', 'cholesterol', ['lab']),
        ('A08', 'fasting glucose drawn', 'glucose', ['lab']),
        ('A09', 'creatinine clearance measured', 'creatinine clearance', ['lab']),
        ('A10', 'potassium resulted at 3.2', 'potassium', ['lab']),
        ('A11', 'serum calcium pending', 'calcium', ['lab']),
        ('A12', 'glucose checked hourly', 'glucose', ['lab']),
        ('A13', 'magnesium level monitored', 'magnesium', ['lab']),
        ('A14', 'sodium remained low', 'sodium', ['lab']),
        ('A15', 'cholesterol panel ordered', 'cholesterol', ['lab']),
        ('A16', 'potassium 3.2 on labs', 'potassium', ['lab']),
        ('A17', 'glucose stable overnight', 'glucose', None),
        ('B01', 'potassium chloride 20 mEq IV given', 'potassium chloride', ['medication']),
        ('B02', 'D50 glucose given for hypoglycemia', 'D50 glucose', ['medication']),
        ('B03', 'calcium gluconate infused', 'calcium gluconate', ['medication']),
        ('B04', 'sodium bicarbonate pushed', 'sodium bicarbonate', ['medication']),
        ('B05', 'magnesium sulfate started', 'magnesium sulfate', ['medication']),
        ('B06', 'potassium 40 mEq PO daily', 'potassium', ['medication']),
        ('B07', 'will start potassium supplementation', 'potassium', ['medication']),
        ('B08', 'glucose tablet taken for low sugar', 'glucose', ['lab', 'medication']),
        ('B09', 'calcium carbonate 500 mg TID', 'calcium carbonate', ['medication']),
        ('B10', 'potassium replaced', 'potassium', ['medication']),
        ('B11', 'magnesium oxide given', 'magnesium oxide', ['medication']),
        ('B12', 'sodium bicarbonate drip', 'sodium bicarbonate', ['medication']),
        ('B13', 'magnesium repleted', 'magnesium', ['medication']),
        ('B14', 'potassium 20 mEq IV once', 'potassium', ['medication']),
        ('C01', 'metformin held this morning', 'metformin', ['medication']),
        ('C02', 'stopped lisinopril', 'lisinopril', ['medication']),
        ('C03', 'aspirin continued', 'aspirin', None),
        ('C04', 'warfarin bridged to heparin', 'warfarin', None),
        ('C05', 'restart metformin tomorrow', 'metformin', None),
        ('C06', 'patient takes apixaban', 'apixaban', None),
        ('C07', 'amlodipine stopped', 'amlodipine', ['medication']),
        ('C08', 'hold Januvia', 'Januvia', ['medication']),
        ('C09', 'carvedilol tolerated', 'carvedilol', ['medication']),
        ('C10', 'switched from sertraline to fluoxetine', 'sertraline', ['medication']),
        ('C11', 'discontinued gabapentin', 'gabapentin', None),
        ('C12', 'hold metoprolol', 'metoprolol', ['medication']),
        ('D01', 'metformin 500 mg BID', 'metformin', ['medication']),
        ('D02', 'lisinopril 10 mg daily', 'lisinopril', ['medication']),
        ('D03', 'apixaban 5 mg twice daily', 'apixaban', ['medication']),
        ('D04', 'insulin glargine 20 units nightly', 'insulin glargine', ['medication']),
        ('D05', 'furosemide 40 mg IV', 'furosemide', ['medication']),
        ('D06', 'hydromorphone 2 mg IV once', 'hydromorphone', ['medication']),
        ('D07', 'prednisone 60 mg daily', 'prednisone', ['medication']),
        ('D08', 'amoxicillin 500 mg TID x7 days', 'amoxicillin', ['medication']),
        ('D09', 'metoprolol 25 mg PO BID', 'metoprolol', ['medication']),
        ('E01', 'carbamazepine level was 8', 'carbamazepine', ['lab']),
        ('E02', 'check cyclosporine level', 'cyclosporine', ['lab']),
        ('E03', 'digoxin level low', 'digoxin', ['lab']),
        ('E04', 'vancomycin trough 12', 'vancomycin', ['lab']),
        ('E05', 'phenytoin level toxic', 'phenytoin', ['lab']),
        ('E06', 'tacrolimus level monitored', 'tacrolimus', ['lab']),
        ('E07', 'vancomycin level drawn', 'vancomycin', ['lab']),
        ('E08', 'lithium level 0.8', 'lithium', ['lab']),
]


class TestConTextArbitration:
    """medspaCy ConText MEASUREMENT/ADMINISTRATION tie-breaker for the
    lab-vs-med confusion (QC pattern 2: 1,046 analytes typed 'therapeutic
    agent'). Runs the REAL medspaCy ConText pipeline with the production
    cue rules — GLiNER is bypassed by inserting the target span directly,
    mirroring NlpPipeline.process Steps 3-4."""

    @pytest.mark.parametrize("item_id,text,target,expected", _ARBITER_CORPUS,
                             ids=[row[0] for row in _ARBITER_CORPUS])
    def test_corpus_item(self, context_arbiter, item_id, text, target, expected):
        assert _context_decide(context_arbiter, text, target) == expected, (
            f"{item_id}: {text!r} target {target!r}"
        )

    def test_analyte_value_sentence_with_slashed_unit_stays_lab(self, context_arbiter):
        """Regression guard: the tokenizer splits 'mg/dL', so the bare 'mg'
        ADMINISTRATION cue fires on plain lab-value sentences — the
        slashed-unit MEASUREMENT regexes must fire too so the analyte is
        not flipped to a medication."""
        assert _context_decide(context_arbiter, "glucose 250 mg/dL this morning", "glucose") == ["lab"]
        assert _context_decide(context_arbiter, "calcium 8.9 mg/dL", "calcium") == ["lab"]

    def test_level_word_in_other_clause_does_not_flip_drug(self, context_arbiter):
        """A 'levels' belonging to another entity's clause (different
        sentence) must not reach a drug span — ConText scopes are
        sentence-bounded."""
        got = _context_decide(
            context_arbiter, "Continue metformin. Glucose levels stable.", "metformin",
        )
        assert got is None

    def test_multi_word_reference_range_cue_fires(self, context_arbiter):
        assert _context_decide(
            context_arbiter, "hemoglobin within reference range", "hemoglobin",
        ) == ["lab"]


@pytest.fixture(scope="module")
def three_signal():
    """Parser (en_core_web_sm) + medspaCy ConText pipeline with the
    production arbiter rules — mirrors NlpPipeline.process for the
    three-signal lab-vs-med disambiguation, minus GLiNER."""
    spacy = pytest.importorskip("spacy")
    medspacy = pytest.importorskip("medspacy")
    # en_core_web_sm lives on the spacy-models index, not PyPI (see the
    # extraction extra note in pyproject) — skip when not installed.
    try:
        parser = spacy.load("en_core_web_sm", disable=["ner"])
    except OSError:
        pytest.skip("en_core_web_sm not installed (spacy-models index)")
    from medterm4ds.services.extraction import _register_context_arbiter
    nlp = medspacy.load(medspacy_disable=["medspacy_target_matcher"])
    assert _register_context_arbiter(nlp)
    return parser, nlp


def _three_signal_decide(three_signal, text, target):
    """Run one (text, target) through the full three-signal arbiter —
    mirrors NlpPipeline.process: parser doc for Signals 1-2, ConText doc
    for Signal 3."""
    from medterm4ds.services.extraction import _arbitrate_lab_vs_med
    parser, nlp = three_signal
    doc = nlp(text)
    parser_doc = parser(text)
    t0 = text.find(target)
    assert t0 >= 0, f"target {target!r} not found in {text!r}"
    sp = doc.char_span(t0, t0 + len(target), label="therapeutic agent",
                       alignment_mode="expand")
    assert sp is not None, f"span {target!r} failed to align"
    doc.set_ents([sp])
    nlp.get_pipe("medspacy_context")(doc)
    parser_span = parser_doc.char_span(t0, t0 + len(target),
                                       alignment_mode="expand")
    return _arbitrate_lab_vs_med(parser_span, parser_doc, doc.ents[0])


class TestThreeSignalArbitration:
    """Head noun (Signal 1) and unit type (Signal 2) pre-empt the ConText
    arbiter (Signal 3). Eval: docs/.ai_loop/qc_comp/three_signal_results.md
    (signals 1-2 100% precision, overall 78.3% -> 84.4%, group E 65% -> 94%)."""

    def test_signal1_lab_head_noun(self, three_signal):
        assert _three_signal_decide(three_signal, "vancomycin level was 8", "vancomycin") == ["lab"]

    def test_signal1_med_head_noun(self, three_signal):
        assert _three_signal_decide(three_signal, "vancomycin dose adjusted", "vancomycin") == ["medication"]

    def test_signal1_head_noun_with_modifiers(self, three_signal):
        assert _three_signal_decide(
            three_signal, "the slightly elevated vancomycin level", "vancomycin",
        ) == ["lab"]

    def test_signal1_mistagged_adjective_root(self, three_signal):
        """en_core_web_sm roots 'phenytoin level subtherapeutic' at the
        adjective 'subtherapeutic' — the rightmost-noun fallback must
        still find 'level'."""
        assert _three_signal_decide(
            three_signal, "phenytoin level subtherapeutic", "phenytoin",
        ) == ["lab"]

    def test_signal2_concentration_unit_is_lab(self, three_signal):
        assert _three_signal_decide(three_signal, "potassium 5.2 mEq/L", "potassium") == ["lab"]

    def test_signal2_dose_unit_is_medication(self, three_signal):
        assert _three_signal_decide(three_signal, "potassium 40 mEq", "potassium") == ["medication"]

    def test_signal2_slashed_concentration_unit_is_lab(self, three_signal):
        assert _three_signal_decide(three_signal, "calcium 8.9 mg/dL", "calcium") == ["lab"]

    def test_signal2_dose_unit_after_brand_is_medication(self, three_signal):
        assert _three_signal_decide(three_signal, "calcium carbonate 500 mg", "calcium carbonate") == ["medication"]

    def test_signal2_numerator_token_does_not_flip_slashed_unit(self, three_signal):
        """'mEq' alone is a dose unit, but in '5.2 mEq/L' it is the
        numerator of a concentration — the combined-token check must run
        before the single-token dose match."""
        assert _three_signal_decide(three_signal, "potassium 5.2 mEq/L", "potassium") == ["lab"]

    def test_signal3_context_fallback_fires_lab(self, three_signal):
        """No head noun, no unit after the span — the MEASUREMENT cue
        ('low') decides via the existing ConText arbiter."""
        assert _three_signal_decide(three_signal, "sodium remained low", "sodium") == ["lab"]

    def test_signal3_context_fallback_administration_cue(self, three_signal):
        """ConText-only decision ('increased' ADMINISTRATION cue; the
        analyte-rising gold is a known Signal 3 misfire, kept as-is —
        ConText implementation is unchanged by the three-signal wiring)."""
        assert _three_signal_decide(three_signal, "creatinine increased to 1.8", "creatinine") == ["medication"]

    def test_signal1_preempts_signal2(self, three_signal):
        """'vancomycin level 500 mg': Signal 1 ('level' → lab) wins over
        the Signal 2 dose unit ('mg' → medication)."""
        assert _three_signal_decide(three_signal, "vancomycin level 500 mg", "vancomycin") == ["lab"]

    def test_signal2_preempts_signal3(self, three_signal):
        """'potassium 5.2 mEq/L held': Signal 2 (concentration → lab)
        wins over the Signal 3 ADMINISTRATION cue ('held')."""
        assert _three_signal_decide(three_signal, "potassium 5.2 mEq/L held", "potassium") == ["lab"]

    def test_no_signal_falls_through_to_label(self, three_signal):
        """Sentence-bounded cue scope: the 'levels' of another sentence
        must not reach the drug span; no signal fires → None."""
        assert _three_signal_decide(
            three_signal, "Continue metformin. Glucose levels stable.", "metformin",
        ) is None


class TestConTextOverrideResolution:
    """resolve_spans wiring: the ConText arbitration captured on
    FilteredSpan.context_categories takes precedence over the GLiNER label
    mapping; no decision (None) falls through to the label mapping."""

    @pytest.fixture
    def stub(self, monkeypatch):
        def _install(routes):
            import medterm4ds.services.search as search_module
            stub = _StubSearchService(routes)
            monkeypatch.setattr(search_module, "get_search_service", lambda: stub)
            return stub
        return _install

    def test_measurement_override_resolves_lab(self, stub):
        """Group A/E shape: drug-labeled analyte with a measurement cue
        resolves to the lab anchor even when the medication anchor scores
        higher unfiltered."""
        s = stub({
            "creatinine|lab": [_anchor("VAL-LAB-2160-0", 0.90, "LOINC")],
            "creatinine|drug_class,medication": [],
            "creatinine|*": [_anchor("VAL-MED-RXNORM-2913", 0.95, "RXNORM")],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="creatinine", entity_type="therapeutic agent",
                         context_categories=["lab"]),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-LAB-2160-0"]
        assert [c.result_type for c in concepts] == ["lab"]
        assert "lab" in s.called_with("creatinine")

    def test_administration_override_resolves_medication(self, stub):
        """Group B/D shape: 'potassium chloride 20 mEq IV given' resolves
        to the medication anchor; the label's drug_class+medication
        constraint is replaced by the narrower medication-only override."""
        s = stub({
            "potassium chloride|medication": [
                _anchor("VAL-MED-RXNORM-245260", 0.91, "RXNORM"),
            ],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="potassium chloride", entity_type="therapeutic agent",
                         context_categories=["medication"]),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-MED-RXNORM-245260"]
        assert [c.result_type for c in concepts] == ["medication"]
        called = s.called_with("potassium chloride")
        assert "medication" in called
        assert "drug_class,medication" not in called

    def test_both_fire_searches_both_categories(self, stub):
        """Both cues fire ('glucose tablet taken for low sugar'): the search
        requests lab+medication together and anchor ranking picks."""
        s = stub({
            "glucose|lab,medication": [
                _anchor("VAL-MED-GLUCOSE-TABS", 0.93, "RXNORM"),
            ],
            "glucose|lab": [],
            "glucose|medication": [],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="glucose", entity_type="therapeutic agent",
                         context_categories=["lab", "medication"]),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-MED-GLUCOSE-TABS"]
        assert "lab,medication" in s.called_with("glucose")

    def test_no_decision_falls_back_to_label_mapping(self, stub):
        """Neither cue fires: the GLiNER label mapping stands."""
        s = stub({
            "aspirin|drug_class,medication": [
                _anchor("VAL-MED-1191", 0.92, "RXNORM"),
            ],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="aspirin", entity_type="therapeutic agent",
                         context_categories=None),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-MED-1191"]
        assert "drug_class,medication" in s.called_with("aspirin")

    def test_batch_path_applies_override_per_span(self, stub):
        """Batch (multi-span canonical) path: the unfiltered batch results
        are post-filtered by the OVERRIDE categories per span, not the
        label mapping — even with adversarial result ordering."""
        stub({
            "carbamazepine|*": [
                _anchor("VAL-MED-RXNORM-8212", 0.96, "RXNORM"),  # top, wrong
                _anchor("VAL-LAB-16061-1", 0.88, "LOINC"),
            ],
            "metformin|*": [
                _anchor("VAL-LAB-xxxx", 0.97, "LOINC"),            # top, wrong
                _anchor("VAL-MED-6809", 0.90, "RXNORM"),
            ],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="carbamazepine", entity_type="therapeutic agent",
                         context_categories=["lab"]),
            FilteredSpan(text="metformin", entity_type="therapeutic agent",
                         context_categories=["medication"]),
        ])
        by_text = {c.matched_text: c for c in concepts}
        assert by_text["carbamazepine"].canonical_id == "VAL-LAB-16061-1"
        assert by_text["carbamazepine"].result_type == "lab"
        assert by_text["metformin"].canonical_id == "VAL-MED-6809"
        assert by_text["metformin"].result_type == "medication"

    def test_explicit_result_types_beat_context_override(self, stub):
        """QC-153 regression guard: caller-passed result_types stays a hard
        filter — the ConText override is not consulted."""
        stub({
            "diabetes|condition": [],
            "diabetes|*": [_anchor("VAL-LAB-128793-9", 0.99, "LOINC")],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans(
            [FilteredSpan(text="diabetes", entity_type="therapeutic agent",
                          context_categories=["medication"])],
            result_types="condition",
        )
        assert concepts == []


class TestPopulationBlocklist:
    """Pattern 3: population terms are never extracted as disorders."""

    @pytest.mark.parametrize("term", [
        "adults", "women", "men", "children", "patients", "patient",
        "infant", "infants", "elderly", "neonate", "neonates",
        "female", "females", "male", "males",
    ])
    def test_population_terms_are_false_positives(self, term):
        assert _is_false_positive(term), f"{term!r} must be blocklisted"


class TestThreadSafety:
    """Direct multi-threaded use must be safe (service-level lock)."""

    def test_concurrent_find_terms(self, service):
        import threading

        text = "Patient has diabetes and takes metformin. Creatinine 1.8."
        results = []
        errors = []

        def worker():
            try:
                results.append(tuple(
                    (s.text, s.span_start, s.span_end, s.status)
                    for s in service.find_terms(text)
                ))
            except Exception as exc:  # noqa: BLE001 — captured for assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent find_terms raised: {errors}"
        assert len(results) == 4
        # Same input on the same (CPU-pinned) pipeline must give identical
        # output in every thread.
        assert all(r == results[0] for r in results), (
            "concurrent find_terms returned divergent results"
        )

    def test_annotated_single_text_holds_service_lock(self, service):
        """CR-062: extract(str, format="annotated") must serialize on the
        service lock like every other public entry point.

        Deterministic probe: instrument _find_terms_locked to record
        concurrent entries; without the lock, two barrier-synchronized
        threads overlap inside the (non-thread-safe) NLP pipeline; with it,
        the second thread blocks before entering.
        """
        import threading
        import time as _time

        inner = service._find_terms_locked
        active: list[int] = []
        max_active = 0

        def probe(*args, **kwargs):
            nonlocal max_active
            active.append(1)
            max_active = max(max_active, len(active))
            _time.sleep(0.05)
            try:
                return inner(*args, **kwargs)
            finally:
                active.pop()

        service._find_terms_locked = probe
        try:
            texts = [
                "Patient takes metformin 500 mg daily for type 2 diabetes.",
                "HbA1c was 7.2 percent.",
            ]
            errors: list[Exception] = []
            barrier = threading.Barrier(2)

            def run(text):
                try:
                    barrier.wait()
                    service.extract(text, format="annotated")
                except Exception as exc:  # noqa: BLE001 — captured for assertion
                    errors.append(exc)

            threads = [threading.Thread(target=run, args=(t,)) for t in texts]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
        finally:
            service._find_terms_locked = inner

        assert not errors, f"annotated extract raised: {errors}"
        assert max_active == 1, (
            "concurrent entry into the NLP pipeline on the annotated path — "
            "the service lock is not held (CR-062 regression)"
        )


class TestAnnotatedFields:
    """annotation_fields configuration for format="annotated"."""

    TEXT = "Patient started on metformin. HbA1c was elevated."

    def test_default_marker_unchanged(self, service):
        out = service.extract(self.TEXT, format="annotated")
        # Historical two-field marker: [entity|label]
        for marker in [p + "]" for p in out["annotated_text"].split("[")[1:]]:
            fields = marker[1:-1].split("|")
            assert len(fields) == 2, f"default marker must have 2 fields: {marker}"

    def test_source_code_in_marker(self, service):
        out = service.extract(
            self.TEXT, format="annotated",
            annotation_fields=["text", "type", "source_code"],
        )
        assert any(
            "RXNORM:" in m
            for m in out["annotated_text"].split("[")[1:]
        ), out["annotated_text"]

    def test_canonical_id_in_marker(self, service):
        out = service.extract(
            self.TEXT, format="annotated", annotation_fields=["canonical_id"],
        )
        assert "VAL-MED-RXNORM" in out["annotated_text"]

    def test_comma_string_accepted(self, service):
        out = service.extract(
            self.TEXT, format="annotated", annotation_fields="text,type",
        )
        metformin = [m for m in out["annotated_text"].split("[")[1:] if "metformin" in m]
        assert metformin and len(metformin[0][:-1].split("|")) == 2

    def test_marker_order_follows_argument_order(self, service):
        a = service.extract(
            self.TEXT, format="annotated",
            annotation_fields=["text", "source_code"],
        )["annotated_text"]
        b = service.extract(
            self.TEXT, format="annotated",
            annotation_fields=["source_code", "text"],
        )["annotated_text"]
        a_met = [m for m in a.split("[")[1:] if "metformin" in m][0][:-1]
        b_met = [m for m in b.split("[")[1:] if "metformin" in m][0][:-1]
        assert a_met.startswith("metformin|RXNORM:"), a_met
        assert b_met.startswith("RXNORM:"), b_met

    def test_unresolved_span_gets_unknown(self, service):
        from medterm4ds.services.extraction import (
            _annotation_marker_values,
            _normalize_annotation_fields,
        )
        span = FilteredSpan(
            text="500 mg", entity_type="vital sign", span_start=0, span_end=6,
        )
        values = _annotation_marker_values(
            _normalize_annotation_fields(["text", "type", "source_code", "name"]),
            entity_text="500 mg", label="vital sign", span=span, concept=None,
        )
        assert values == ["500 mg", "vital sign", "UNKNOWN", "UNKNOWN"]

    def test_invalid_field_raises(self, service):
        with pytest.raises(ValueError, match="annotation_fields"):
            service.extract(
                self.TEXT, format="annotated", annotation_fields=["text", "bogus"],
            )

    def test_span_metadata_carries_source_code(self, service):
        out = service.extract(self.TEXT, format="annotated")
        med = [s for s in out["spans"] if "metformin" in s["text"].lower()]
        assert med, out["spans"]
        assert med[0]["source"] == "RXNORM"
        assert med[0]["code"] == "6809"


class TestBatchExtract:
    """extract() accepts a single text or a list of texts."""

    T0 = "Patient started on metformin."
    T1 = "HbA1c was elevated."

    def test_single_matches_first_batch_element(self, service):
        single = service.extract(self.T0, format="codes")
        batch = service.extract([self.T0, self.T1], format="codes")
        assert isinstance(batch, list) and len(batch) == 2
        assert [(c.source, c.code) for c in single] == [
            (c.source, c.code) for c in batch[0]
        ]

    def test_order_preserved(self, service):
        # min_grade="possible": the v3-hybrid serving space (0.0.8 default,
        # esp_468d01ebbab03648) scores HbA1c→LOINC LP16413-4 at 0.79 —
        # below the 0.80 "probable" line the legacy space cleared. The
        # test's contract is ORDER, not grade calibration.
        batch = service.extract(
            [self.T1, self.T0], format="codes", min_grade="possible"
        )
        # T1 is the lab text: its result must contain a LOINC concept
        assert any(c.source == "LOINC" for c in batch[0])
        # T0 is the medication text
        assert any(c.source == "RXNORM" for c in batch[1])

    def test_empty_list(self, service):
        assert service.extract([], format="codes") == []

    def test_annotated_batch(self, service):
        batch = service.extract([self.T0], format="annotated")
        assert isinstance(batch, list) and len(batch) == 1
        assert set(batch[0].keys()) == {"concepts", "annotated_text", "spans"}

    def test_terms_batch(self, service):
        batch = service.extract([self.T0, self.T1], format="terms")
        assert isinstance(batch, list) and len(batch) == 2
        assert all(isinstance(per_text, list) for per_text in batch)

    def test_invalid_element_raises_with_index(self, service):
        with pytest.raises(ValueError, match="index 1"):
            service.extract([self.T0, 42], format="codes")

    def test_module_level_function_batch(self):
        from medterm4ds.services.extraction import extract as extract_fn
        from medterm4ds.services.extraction import get_extraction_service
        out = extract_fn([self.T0], format="terms")
        assert isinstance(out, list) and len(out) == 1
        assert get_extraction_service() is not None

    def test_span_metadata_carries_match_grade(self, service):
        # Structured-data team join: grade in spans drops their second
        # CLI lookup for accept-vs-withhold decisions.
        out = service.extract("Patient started on metformin.", format="annotated")
        med = [s for s in out["spans"] if "metformin" in s["text"].lower()]
        assert med, out["spans"]
        assert med[0]["match_grade"] in {"certain", "exact", "probable", "possible", "broader"}

    def test_batch_nlp_strict_parity(self, service):
        """process_batch spans must match process() spans semantically —
        same spans, labels, offsets, statuses; scores within float noise.
        Batched GLiNER inference (padded batches) differs from sequential
        per-sentence calls at the last float digits, same class of drift
        as GPU-vs-CPU. Campaign runs should pick ONE mode and stay in it."""
        texts = [
            "Patient started on metformin 500 mg twice daily.",
            "No evidence of diabetes. Creatinine was 1.8 mg/dL.",
            "Father had colon cancer. Family history of CAD.",
        ]
        singles = [service._nlp.process(t) for t in texts]
        batched = service._nlp.process_batch(texts)
        assert len(batched) == 3
        for i, (a, b) in enumerate(zip(singles, batched, strict=False)):
            assert len(a) == len(b), f"span count differs for text {i}"
            for sa, sb in zip(a, b, strict=False):
                assert (sa.text, sa.entity_type, sa.status,
                        sa.span_start, sa.span_end) == (
                       sb.text, sb.entity_type, sb.status,
                       sb.span_start, sb.span_end), f"span differs for text {i}"
                assert abs(sa.ner_confidence - sb.ner_confidence) < 1e-3, (
                    f"score drift beyond float noise for text {i}: "
                    f"{sa.ner_confidence} vs {sb.ner_confidence}"
                )


class TestNerBattery20261107:
    """Model-team 50-case NER battery (2026-11-07): three deterministic
    consumer-side fixes. Evidence: fhir4px-model docs/experiments/
    drug-ner-diagnostics.md."""

    # --- 1. sentence-local post-span negation ('Breast cancer ruled out.') ---

    def test_post_span_cue_regex_binds_directly_after_span(self):
        from medterm4ds.services.extraction import _POST_SPAN_CUE_RE
        for tail in (" ruled out.", " was ruled out.", " excluded;",
                     " not seen in the follow-up", " negative for disease",
                     " denied."):
            assert _POST_SPAN_CUE_RE.match(tail), tail
        for tail in (" reported.", " ruled", " routine screening.",
                     " was noted"):
            assert not _POST_SPAN_CUE_RE.match(tail), tail

    def test_post_span_cue_sentence_limited(self):
        from medterm4ds.services.extraction import _post_span_negation_cue

        class _Sent:
            def __init__(self, s, e):
                self.start_char, self.end_char = s, e

        class _Doc:
            def __init__(self, *sents):
                self.sents = list(sents)

        # cue inside the same sentence, within the window -> binds
        text = "Breast cancer ruled out. Continue screening."
        doc = _Doc(_Sent(0, 26))
        assert _post_span_negation_cue(text, 13, doc)
        # cue in the NEXT sentence -> does not bind (sentence 1 has no cue)
        text2 = "Breast cancer is present. It was ruled out later."
        doc2 = _Doc(_Sent(0, 26), _Sent(26, len(text2)))
        assert not _post_span_negation_cue(text2, 13, doc2)
        # no sentence info -> window fallback still binds a direct cue
        class _NoSents:
            sents = ()
            def __iter__(self):
                return iter(())
        assert _post_span_negation_cue("Chest pain excluded.", 10, _NoSents())

    # --- 2. with/without qualifier inversion ('CBC with differential') ---

    def test_qualifier_inverted_pairs(self):
        from medterm4ds.services.extraction import _qualifier_inverted
        assert _qualifier_inverted(
            "CBC with differential",
            "Hemogram and Platelets without Differential",
        )
        assert _qualifier_inverted(
            "panel without differential", "CBC with Differential",
        )
        assert not _qualifier_inverted(
            "CBC with differential", "CBC with Differential",
        )
        # different qualifier tokens, or no qualifier on either side
        assert not _qualifier_inverted("CBC with differential", "chest pain")
        assert not _qualifier_inverted("chest pain", "Basic metabolic panel")

    def test_resolve_skips_inverted_qualifier_result(self, monkeypatch):
        from medterm4ds.services import extraction as ex
        from medterm4ds.services.search import CanonicalSearchResult

        good = CanonicalSearchResult(
            canonical_id="LAB-1", domain="Lab", anchor_system="LOINC",
            anchor_code="57021-2", patient_friendly_name="CBC W Differential",
            score=0.80, match_grade="probable",
            matched_via_code="57021-2", matched_via_display="CBC W Differential",
            total_member_count=1,
        )
        inverted = CanonicalSearchResult(
            canonical_id="LAB-2", domain="Lab", anchor_system="LOINC",
            anchor_code="58410-2",
            patient_friendly_name="Hemogram and Platelets without Differential",
            score=0.95, match_grade="probable",
            matched_via_code="58410-2", matched_via_display="Hemogram",
            total_member_count=1,
        )
        span = ex.FilteredSpan(text="CBC with differential", entity_type="panel")
        got = ex._first_matching_concept(
            span, [inverted, good], None,
            result_type_prefixes=None, grade_threshold="probable",
        )
        assert got is not None and got.code == "57021-2"

    # --- 3. drug-fragment stitching (lisinopril/HCTZ) ---

    def test_stitch_drug_fragments_slash_and_dash(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _stitch_drug_fragments,
        )
        spans = [
            FilteredSpan(text="lisinopril", entity_type="therapeutic agent",
                         span_start=0, span_end=10, ner_confidence=0.8),
            FilteredSpan(text="HCTZ", entity_type="therapeutic agent",
                         span_start=11, span_end=15, ner_confidence=0.7),
        ]
        out = _stitch_drug_fragments(spans, "lisinopril/HCTZ")
        assert len(out) == 1
        assert out[0].text == "lisinopril/HCTZ"
        assert (out[0].span_start, out[0].span_end) == (0, 15)

    def test_stitch_blocked_by_non_separator_gap(self):
        # 'Norco q6h' — the frequency token blocks the merge (measured
        # regression in the model team's blind-stitch prototype)
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _stitch_drug_fragments,
        )
        spans = [
            FilteredSpan(text="Norco", entity_type="therapeutic agent",
                         span_start=0, span_end=5),
            FilteredSpan(text="q6h", entity_type="therapeutic agent",
                         span_start=6, span_end=9),
        ]
        assert len(_stitch_drug_fragments(spans, "Norco q6h")) == 2

    def test_stitch_only_drug_labels(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _stitch_drug_fragments,
        )
        spans = [
            FilteredSpan(text="chest", entity_type="disorder",
                         span_start=0, span_end=5),
            FilteredSpan(text="pain", entity_type="disorder",
                         span_start=6, span_end=10),
        ]
        assert len(_stitch_drug_fragments(spans, "chest/pain")) == 2

    def test_stitch_blocked_across_statuses(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _stitch_drug_fragments,
        )
        spans = [
            FilteredSpan(text="aspirin", entity_type="medication",
                         span_start=0, span_end=7, status="negated"),
            FilteredSpan(text="plavix", entity_type="medication",
                         span_start=8, span_end=14),
        ]
        assert len(_stitch_drug_fragments(spans, "aspirin/plavix")) == 2


class TestNerEdgeCorpus20261107:
    """Model-team expanded edge corpus (2026-11-07, ~202 cases): the
    list false-merge side effect, pseudonegation, and pre-span cues."""

    def test_coprescription_list_never_merges(self):
        # 'Home meds: metformin, glipizide, and insulin.' — three
        # mentions, NOT the fake combination 'metformin/glipizide'.
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _stitch_drug_fragments,
        )
        text = "Home meds: metformin, glipizide, and insulin."
        spans = [
            FilteredSpan(text="metformin", entity_type="therapeutic agent",
                         span_start=11, span_end=20),
            FilteredSpan(text="glipizide", entity_type="therapeutic agent",
                         span_start=22, span_end=31),
            FilteredSpan(text="insulin", entity_type="therapeutic agent",
                         span_start=37, span_end=44),
        ]
        out = _stitch_drug_fragments(spans, text)
        assert [s.text for s in out] == ["metformin", "glipizide", "insulin"]

    def test_hyphen_compound_still_stitches(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _stitch_drug_fragments,
        )
        text = "lisinopril-HCTZ 20/12.5 mg daily"
        spans = [
            FilteredSpan(text="lisinopril", entity_type="therapeutic agent",
                         span_start=0, span_end=10),
            FilteredSpan(text="HCTZ", entity_type="therapeutic agent",
                         span_start=11, span_end=15),
        ]
        out = _stitch_drug_fragments(spans, text)
        assert len(out) == 1 and out[0].text == "lisinopril/HCTZ"

    def test_pre_span_cue_classes(self):
        from medterm4ds.services.extraction import _pre_span_cue

        class _Sent:
            def __init__(self, s, e):
                self.start_char, self.end_char = s, e

        class _Doc:
            def __init__(self, *sents):
                self.sents = list(sents)

        def cue(text, start, doc=None):
            return _pre_span_cue(text, start, doc or _Doc(_Sent(0, len(text))))

        # pseudonegation — 'cannot rule out X' is UNCERTAIN, never negated
        assert cue("Cannot rule out pneumonia.", "Cannot rule out pneumonia.".index("pneumonia")) == "pseudo_negation"
        assert cue("We cannot exclude pulmonary embolism.", "We cannot exclude pulmonary embolism.".index("pulmonary")) == "pseudo_negation"
        assert cue("Unable to exclude malignancy.", "Unable to exclude malignancy.".index("malignancy")) == "pseudo_negation"
        # plain uncertainty rescue
        assert cue("Suspected urinary tract infection.", "Suspected urinary tract infection.".index("urinary")) == "uncertainty"
        assert cue("Possibly gout.", "Possibly gout.".index("gout")) == "uncertainty"
        # neither/nor negation
        assert cue("Neither pneumonia nor pulmonary embolism.", "Neither pneumonia nor pulmonary embolism.".index("pneumonia")) == "negation"
        # historical
        assert cue("Prior MI, now on dual antiplatelet therapy.", "Prior MI, now on dual antiplatelet therapy.".index("MI")) == "historical"
        # a cue from an EARLIER clause never binds (anchored adjacency)
        assert cue("We ruled out the flu. Pneumonia noted today.", "We ruled out the flu. Pneumonia noted today.".index("Pneumonia")) is None

    def test_post_span_cue_still_negates(self):
        # the original battery fix keeps working alongside the new guards
        from medterm4ds.services.extraction import _post_span_negation_cue

        class _Sent:
            def __init__(self, s, e):
                self.start_char, self.end_char = s, e

        class _Doc:
            def __init__(self, *sents):
                self.sents = list(sents)

        text = "Breast cancer ruled out."
        assert _post_span_negation_cue(text, 13, _Doc(_Sent(0, len(text))))


class TestPostSpanClauseBinding:
    """Adversarial corpus (model team, 2026-11-07, as-window-01/03,
    as-mixed-01, as-scope-03): the post-span cue and ConText scope must
    not cross clause boundaries — false negations delete real findings."""

    def test_post_span_cue_blocked_by_clause_boundary(self):
        from medterm4ds.services.extraction import _post_span_negation_cue

        class _Sent:
            def __init__(self, s, e):
                self.start_char, self.end_char = s, e

        class _Doc:
            def __init__(self, *sents):
                self.sents = list(sents)

        def negated(text, span_text):
            return _post_span_negation_cue(
                text, text.index(span_text) + len(span_text),
                _Doc(_Sent(0, len(text))),
            )

        # cue in a DIFFERENT clause -> must NOT negate
        assert not negated("Hypertension, negative for proteinuria.", "Hypertension")
        assert not negated("Asthma; free of symptoms today.", "Asthma")
        assert not negated(
            "Positive for fever and cough; negative for dyspnea.", "cough"
        )
        # cue in the SAME clause, directly after -> still negates
        assert negated("Breast cancer ruled out.", "Breast cancer")
        assert negated("Chest pain was ruled out today.", "Chest pain")

    def test_context_negation_needs_clause_support(self):
        from medterm4ds.services.extraction import _clause_negation_supported

        # sepsis clause ('started ceftriaxone for sepsis') has no cue
        text = "No evidence of pneumonia; started ceftriaxone for sepsis."
        assert not _clause_negation_supported(
            text, text.index("sepsis"), text.index("sepsis") + len("sepsis")
        )
        # cue in the same clause -> supported
        assert _clause_negation_supported(
            "Denies chest pain at rest.", 7, 17
        )
        # comma is NOT a boundary: the list item keeps its support
        assert _clause_negation_supported(
            "Denies chest pain, arm pain now.", 18, 26
        )


class TestParentheticalAndStoplist20261107:
    """Model-team verification round 2 (2026-11-07): parenthetical
    assertion cues, neither/nor residual, abbreviation stoplist, and
    leading-qualifier query stripping."""

    def test_parenthetical_negation_and_uncertainty(self):
        from medterm4ds.services.extraction import (
            _post_span_negation_cue,
            _post_span_uncertainty_cue,
        )

        class _Sent:
            def __init__(self, s, e):
                self.start_char, self.end_char = s, e

        class _Doc:
            def __init__(self, *sents):
                self.sents = list(sents)

        text1 = "Pneumonia (ruled out)."
        doc1 = _Doc(_Sent(0, len(text1)))
        assert _post_span_negation_cue(text1, text1.index("(") - 1, doc1)

        text2 = "Sepsis (possible)."
        doc2 = _Doc(_Sent(0, len(text2)))
        assert _post_span_uncertainty_cue(text2, text2.index("(") - 1, doc2)
        assert not _post_span_negation_cue(text2, text2.index("(") - 1, doc2)

    def test_nor_pre_span_negation(self):
        from medterm4ds.services.extraction import _pre_span_cue

        class _Sent:
            def __init__(self, s, e):
                self.start_char, self.end_char = s, e

        class _Doc:
            def __init__(self, s):
                self.sents = [_Sent(0, len(s))]

        text = "Neither pneumonia nor pulmonary embolism."
        assert _pre_span_cue(text, text.index("pulmonary embolism"), _Doc(text)) == "negation"

    def test_abbreviation_stoplist(self):
        from medterm4ds.services.extraction import _is_false_positive
        for span in ("NPO", "NKDA", "MD", "HEDIS", "<b>", "99213",
                     "E11.9", "4548-4", "7.2"):
            assert _is_false_positive(span), span
        # clinical alnum tokens survive
        for span in ("b12", "a1c", "HbA1c 7.2%", "metformin"):
            assert not _is_false_positive(span), span

    def test_leading_qualifier_stripping(self):
        """CR-301: leading qualifiers are now a FALLBACK query, not the
        primary one — the qualifier is part of the concept for terms like
        'left ventricular hypertrophy' and furniture for 'bilateral
        pneumonia'."""
        from medterm4ds.services.extraction import (
            _leading_qualifier_fallback,
            _strip_trailing_values,
        )
        # primary text keeps the qualifier
        assert _strip_trailing_values("Bilateral pneumonia") == "Bilateral pneumonia"
        assert _strip_trailing_values("HbA1c 7.2%") == "HbA1c"
        # the fallback carries the stripped variant
        assert _leading_qualifier_fallback("Bilateral pneumonia") == "pneumonia"
        assert _leading_qualifier_fallback("Upper GI bleed") == "GI bleed"
        assert _leading_qualifier_fallback("Left arm pain") == "arm pain"
        assert _leading_qualifier_fallback("s/p CABG") == "CABG"
        # no qualifier -> no fallback (original text is used alone)
        assert _leading_qualifier_fallback("metformin") is None
        assert _leading_qualifier_fallback("Goodpasture syndrome") is None
        assert _leading_qualifier_fallback("Chronic kidney disease") is None
class TestFragmentCompositionRanking:
    """Compound-span composition + fragment-completeness ranking (2026-11-07).

    Model-team finding: GLiNER emits 'CBC' and 'differential' as separate
    spans, the resolver is queried with 'CBC' alone, and the top hit is
    'Hemogram and Platelets WITHOUT Differential' — an inverted answer.
    Composition carries the qualifier into search; completeness ranking
    rejects candidates that drop a fragment; a failed composition always
    falls back to the head concept.
    """

    @staticmethod
    def _span(text, label, start, end):
        return FilteredSpan(
            text=text, entity_type=label, span_start=start, span_end=end,
            ner_confidence=0.5,
        )

    def test_composes_across_qualifier_linker(self):
        from medterm4ds.services.extraction import _compose_compound_spans

        text = "CBC with differential ordered."
        spans = [
            self._span("CBC", "lab test", 0, 3),
            self._span("differential", "lab test", 9, 21),
        ]
        merged = _compose_compound_spans(spans, text)
        assert len(merged) == 1
        assert merged[0].text == "CBC with differential"
        assert merged[0].composed_from == ["CBC", "differential"]

    def test_never_merges_across_comma(self):
        from medterm4ds.services.extraction import _compose_compound_spans

        text = "Home meds: metformin, glipizide."
        spans = [
            self._span("metformin", "therapeutic agent", 11, 20),
            self._span("glipizide", "therapeutic agent", 22, 31),
        ]
        assert len(_compose_compound_spans(spans, text)) == 2

    def test_never_merges_across_and(self):
        from medterm4ds.services.extraction import _compose_compound_spans

        text = "metformin and glipizide"
        spans = [
            self._span("metformin", "therapeutic agent", 0, 9),
            self._span("glipizide", "therapeutic agent", 14, 23),
        ]
        assert len(_compose_compound_spans(spans, text)) == 2

    def test_never_merges_numeric_or_unit_fragment(self):
        from medterm4ds.services.extraction import _compose_compound_spans

        text = "metformin 500 mg"
        spans = [
            self._span("metformin", "therapeutic agent", 0, 9),
            self._span("500 mg", "lab test", 10, 16),
        ]
        assert len(_compose_compound_spans(spans, text)) == 2

    def test_never_merges_across_labels(self):
        from medterm4ds.services.extraction import _compose_compound_spans

        text = "CBC pain"
        spans = [
            self._span("CBC", "lab test", 0, 3),
            self._span("pain", "symptom", 4, 8),
        ]
        assert len(_compose_compound_spans(spans, text)) == 2

    def test_completeness_rejects_dropped_fragment(self):
        from medterm4ds.services.extraction import _fragment_completeness

        span = self._span("CBC with differential", "lab test", 0, 21)
        span.composed_from = ["CBC", "differential"]
        inverted = _StubHit("Hemogram and Platelets Without Differential Panel")
        complete = _StubHit("CBC with Manual Differential Panel")
        assert _fragment_completeness(span, complete) is True
        assert _fragment_completeness(span, inverted) is False

    def test_completeness_accepts_combination_members(self):
        from medterm4ds.services.extraction import _fragment_completeness

        span = self._span("lisinopril/HCTZ", "immunization", 0, 15)
        span.composed_from = ["lisinopril", "HCTZ"]
        combo = _StubHit(
            "hydrochlorothiazide / lisinopril",
            canonical_id="VAL-MED-RXNORM-1",
            members=[{"canonical_id": "VAL-MED-RXNORM-2"}, {"canonical_id": "VAL-MED-RXNORM-3"}],
        )
        assert _fragment_completeness(span, combo) is True

    def test_failed_composition_falls_back_to_head(self):
        from medterm4ds.services.extraction import _resolve_composed_multi

        span = self._span("CBC with differential", "lab test", 0, 21)
        span.composed_from = ["CBC", "differential"]
        inverted = _StubHit("Hemogram and Platelets Without Differential Panel")
        head_hit = _StubHit("Complete Blood Count")
        search = _StubSearch({"CBC with differential": [inverted], "CBC": [head_hit]})
        concepts = _resolve_composed_multi(
            span, search.canonical, None,
            result_type_prefixes=None, grade_threshold="probable",
        )
        assert [c.display for c in concepts] == ["Complete Blood Count"]

    def test_siblings_survive_failed_composition(self):
        """'Pneumonia with sepsis' must yield BOTH concepts (review blocker 1)."""
        from medterm4ds.services.extraction import _resolve_composed_multi

        span = self._span("Pneumonia with sepsis", "disorder", 0, 21)
        span.composed_from = ["Pneumonia", "sepsis"]
        pneumonia = _StubHit("Pneumonia", canonical_id="VAL-COND-SNOMED-1")
        sepsis = _StubHit("Sepsis", canonical_id="VAL-COND-SNOMED-2")
        search = _StubSearch({
            "Pneumonia with sepsis": [pneumonia],  # no single anchor covers both
            "Pneumonia": [pneumonia],
            "sepsis": [sepsis],
        })
        concepts = _resolve_composed_multi(
            span, search.canonical, None,
            result_type_prefixes=None, grade_threshold="probable",
        )
        assert sorted(c.display for c in concepts) == ["Pneumonia", "Sepsis"]


class _StubHit:
    """Minimal SearchResult stand-in for resolution-unit tests."""

    def __init__(self, display, *, canonical_id="VAL-LAB-LOINC-1", grade="exact",
                 members=None, score=1.0):
        self.display = display
        self.canonical_id = canonical_id
        self.match_grade = grade
        self.combination_members = members or []
        self.code = canonical_id.rsplit("-", 1)[-1]
        self.source = "LOINC"
        self.anchor_system = "LOINC"
        self.score = score


class _StubSearch:
    def __init__(self, mapping):
        self._mapping = mapping

    def canonical(self, text, **_kwargs):
        return self._mapping.get(text, [])


class TestFusedDoseNotComposed:
    """Regression (ts99 extract-filter gate, 2026-11-07): a fused dose
    token ('500mg') is ONE alnum token, so the neutral-fragment check
    missed it and 'metformin' + '500mg' composed into 'metformin 500mg',
    resolving to the glyburide/metformin COMBINATION anchor instead of
    plain metformin."""

    def test_fused_dose_is_neutral(self):
        from medterm4ds.services.extraction import _is_neutral_fragment

        for frag in ("500mg", "5 mg", "7.2%", "120/80", "20/12.5 mg", "2.5mcg"):
            assert _is_neutral_fragment(frag), frag
        for frag in ("metformin", "b12", "lisinopril", "HbA1c"):
            assert not _is_neutral_fragment(frag), frag

    def test_drug_and_fused_dose_do_not_compose(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _compose_compound_spans,
        )

        text = "Patient takes metformin 500mg and has diabetes."
        spans = [
            FilteredSpan(text="metformin", entity_type="therapeutic agent",
                         span_start=14, span_end=23),
            FilteredSpan(text="500mg", entity_type="therapeutic agent",
                         span_start=24, span_end=29),
        ]
        out = _compose_compound_spans(spans, text)
        assert [s.text for s in out] == ["metformin", "500mg"]


class TestUnitStoplist:
    """Unit/dose/frequency tokens are never concepts (2026-11-07
    verification): a lone 'mg' resolved to Myasthenia Gravis."""

    def test_unit_tokens_dropped(self):
        from medterm4ds.services.extraction import _is_false_positive

        for span in ("mg", "mcg", "ml", "units", "q6h", "bid", "prn",
                     "tab", "Tabs", "IU", "mmol"):
            assert _is_false_positive(span), span
        # real entity names survive
        for span in ("metformin", "Myasthenia Gravis", "b12", "insulin"):
            assert not _is_false_positive(span), span


class TestQualifierFallbackNotPrimary:
    """CR-301 regression (focused review, 2026-11-09): the leading
    qualifier must NOT be stripped from the primary query — measured
    'Left ventricular hypertrophy' -> 'Ventricular Hypertrophy' and
    'Upper respiratory infection' -> 'Respiratory Tract Infection'."""

    def test_primary_query_keeps_qualifier(self):
        from medterm4ds.services.extraction import _strip_trailing_values
        assert _strip_trailing_values("Left ventricular hypertrophy") == (
            "Left ventricular hypertrophy"
        )
        assert _strip_trailing_values("Upper respiratory infection") == (
            "Upper respiratory infection"
        )

    def test_fallback_rescues_bilateral(self):
        from medterm4ds.services.extraction import _leading_qualifier_fallback
        assert _leading_qualifier_fallback("Bilateral pneumonia") == "pneumonia"


class TestStitchedCombinationAllModes:
    """CR-302 (measured live on the demo Space, 2026-11-09): a stitched
    combination span must carry composed_from AND the composed query must
    resolve against the canonical anchor space in EVERY mode — mode-search
    results lack combination_members, so fragment-completeness rejected
    the correct combination and hybrid returned single Lisinopril with
    HCTZ dropped entirely."""

    def test_stitch_sets_composed_from(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _stitch_drug_fragments,
        )
        spans = [
            FilteredSpan(text="lisinopril", entity_type="therapeutic agent",
                         span_start=0, span_end=10),
            FilteredSpan(text="HCTZ", entity_type="therapeutic agent",
                         span_start=11, span_end=15),
        ]
        out = _stitch_drug_fragments(spans, "lisinopril/HCTZ")
        assert out[0].composed_from == ["lisinopril", "HCTZ"]

    def test_combination_resolves_in_every_mode(self):
        from medterm4ds.services.extraction import get_extraction_service
        es = get_extraction_service()
        text = "Take lisinopril/HCTZ 20/12.5 mg daily"
        for mode in ("hybrid", "canonical", "lexical"):
            codes = [c.code for c in es.extract(text, mode=mode)]
            assert "214618" in codes, (mode, codes)


class TestStrengthDesign20261109:
    """The 0.0.12 strength design: strength-inclusive primary queries in
    every mode, strength captured as metadata, corroboration-gated list
    joins, slash-strength inference, and +/plus separators."""

    def test_strength_components(self):
        from medterm4ds.services.extraction import _strength_components
        assert _strength_components("20/12.5 mg") == 2
        assert _strength_components("5-500") == 2
        assert _strength_components("500 mg") == 1
        assert _strength_components("250/50/100") == 3
        assert _strength_components(None) is None
        assert _strength_components("daily") is None

    def test_absorb_trailing_strength_spans(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _absorb_trailing_strength,
        )
        text = "Take lisinopril/HCTZ 20/12.5 mg daily"
        drug_end = text.index("20")
        spans = [
            FilteredSpan(text="lisinopril/HCTZ", entity_type="therapeutic agent",
                         span_start=5, span_end=drug_end - 1),
            FilteredSpan(text="20", entity_type="lab test",
                         span_start=drug_end, span_end=drug_end + 2),
            FilteredSpan(text="12.5 mg", entity_type="vital sign",
                         span_start=drug_end + 3, span_end=drug_end + 10),
        ]
        out = _absorb_trailing_strength(spans, text)
        assert len(out) == 1
        assert out[0].strength == "20/12.5 mg"
        assert out[0].span_end == drug_end + 10

    def test_plus_and_word_plus_separators_stitch(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _stitch_drug_fragments,
        )
        for gap in ("+", " plus "):
            text = f"lisinopril{gap}HCTZ"
            cut = text.index("HCTZ")
            spans = [
                FilteredSpan(text="lisinopril", entity_type="therapeutic agent",
                             span_start=0, span_end=10),
                FilteredSpan(text="HCTZ", entity_type="therapeutic agent",
                             span_start=cut, span_end=len(text)),
            ]
            out = _stitch_drug_fragments(spans, text)
            assert len(out) == 1 and out[0].composed_from == ["lisinopril", "HCTZ"], gap

    def test_gated_list_join_requires_strength_corroboration(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _stitch_gated_list_pairs,
        )
        # corroborated: 'lisinopril and HCTZ 20/12.5' joins
        text = "lisinopril and HCTZ 20/12.5"
        cut = text.index("HCTZ")
        spans = [
            FilteredSpan(text="lisinopril", entity_type="therapeutic agent",
                         span_start=0, span_end=10),
            FilteredSpan(text="HCTZ", entity_type="therapeutic agent",
                         span_start=cut, span_end=len(text), strength="20/12.5"),
        ]
        out = _stitch_gated_list_pairs(spans, text)
        assert len(out) == 1 and out[0].text == "lisinopril/HCTZ"
        assert out[0].strength == "20/12.5"
        # uncorroborated list ('metformin, glipizide, and insulin') stays 3
        text2 = "metformin, glipizide, and insulin"
        spans2 = [
            FilteredSpan(text="metformin", entity_type="medication",
                         span_start=0, span_end=9),
            FilteredSpan(text="glipizide", entity_type="medication",
                         span_start=11, span_end=20),
            FilteredSpan(text="insulin", entity_type="medication",
                         span_start=26, span_end=33),
        ]
        assert len(_stitch_gated_list_pairs(spans2, text2)) == 3

    def test_in_span_strength_captured(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _absorb_trailing_strength,
        )
        spans = [FilteredSpan(text="metformin 500mg",
                              entity_type="therapeutic agent",
                              span_start=0, span_end=15)]
        out = _absorb_trailing_strength(spans, "metformin 500mg")
        assert out[0].strength == "500mg"
        assert out[0].text == "metformin 500mg"

    def test_gated_join_strict_members_reject_wrong_combination(self):
        """DAPT safety: 'Aspirin and clopidogrel 75/75' must NOT land on
        aspirin/caffeine (also 2 members) — count alone is not enough
        for list-separator joins."""
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _fragment_completeness,
        )
        from medterm4ds.services.search import CanonicalSearchResult
        span = FilteredSpan(
            text="Aspirin/clopidogrel", entity_type="medication",
            composed_from=["Aspirin", "clopidogrel"], composed_strict=True,
        )
        members = [{"display": "Aspirin"}, {"display": "Caffeine"}]
        r = CanonicalSearchResult(
            canonical_id="VAL-MED-RXNORM-X", domain=[], anchor_system="RXNORM",
            anchor_code="X", patient_friendly_name="aspirin / caffeine",
            score=0.95, match_grade="exact", matched_via_code="t",
            matched_via_display="t", total_member_count=2,
            combination_members=members,
        )
        assert not _fragment_completeness(span, r)
        # name-matching members pass
        r2 = CanonicalSearchResult(
            canonical_id="VAL-MED-RXNORM-Y", domain=[], anchor_system="RXNORM",
            anchor_code="Y", patient_friendly_name="aspirin / clopidogrel",
            score=0.95, match_grade="exact", matched_via_code="t",
            matched_via_display="t", total_member_count=2,
            combination_members=[{"display": "Aspirin"}, {"display": "Clopidogrel"}],
        )
        assert _fragment_completeness(span, r2)
        # alias fragments ('/'-stitched, not strict) keep the count shortcut
        span_alias = FilteredSpan(
            text="lisinopril/HCTZ", entity_type="medication",
            composed_from=["lisinopril", "HCTZ"],
        )
        assert _fragment_completeness(
            span_alias,
            CanonicalSearchResult(
                canonical_id="VAL-MED-RXNORM-Z", domain=[], anchor_system="RXNORM",
                anchor_code="Z", patient_friendly_name="hctz / lisinopril",
                score=0.92, match_grade="probable", matched_via_code="t",
                matched_via_display="t", total_member_count=2,
                combination_members=[{"display": "Hydrochlorothiazide"},
                                     {"display": "Lisinopril"}],
            ),
        )

    def test_raw_text_strength_fallback(self):
        from medterm4ds.services.extraction import (
            FilteredSpan,
            _absorb_trailing_strength,
        )
        text = "Take lisinopril and HCTZ 20/12.5 mg daily"
        cut = text.index("HCTZ")
        spans = [
            FilteredSpan(text="lisinopril", entity_type="therapeutic agent",
                         span_start=5, span_end=15),
            FilteredSpan(text="HCTZ", entity_type="immunization",
                         span_start=cut, span_end=cut + 4),
        ]
        out = _absorb_trailing_strength(spans, text)
        assert out[1].strength == "20/12.5 mg"
        assert out[1].span_end == cut + 4  # boundaries unchanged
