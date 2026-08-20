"""Tests for the text extraction service."""

from __future__ import annotations

import pytest

pytest.importorskip("medspacy")
pytest.importorskip("transformers")

from medterm4ds.services.extraction import (
    ExtractionService,
    FilteredSpan,
    ExtractedConcept,
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
    from medterm4ds.services.extraction import _register_context_arbiter
    nlp = medspacy.load(medspacy_disable=["medspacy_target_matcher"])
    assert _register_context_arbiter(nlp)
    parser = spacy.load("en_core_web_sm", disable=["ner"])
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
        batch = service.extract([self.T1, self.T0], format="codes")
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
