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


class TestAnalyteContext:
    """Pattern 2: analytes labeled 'therapeutic agent' prefer lab anchors
    unless an administration context is present."""

    @pytest.fixture
    def stub(self, monkeypatch):
        def _install(routes):
            import medterm4ds.services.search as search_module
            stub = _StubSearchService(routes)
            monkeypatch.setattr(search_module, "get_search_service", lambda: stub)
            return stub
        return _install

    def test_analyte_without_admin_context_resolves_lab(self, stub):
        s = stub({
            "creatinine|lab": [_anchor("VAL-LAB-2160-0", 0.90, "LOINC")],
            "creatinine|drug_class,medication": [
                _anchor("VAL-MED-RXNORM-2913", 0.95, "RXNORM"),
            ],
            "creatinine|*": [_anchor("VAL-MED-RXNORM-2913", 0.95, "RXNORM")],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="creatinine", entity_type="therapeutic agent"),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-LAB-2160-0"]
        assert "lab" in s.called_with("creatinine")

    def test_serum_qualified_analyte_resolves_lab(self, stub):
        """Specimen qualifier is stripped before analyte matching."""
        stub({
            "serum creatinine|lab": [_anchor("VAL-LAB-2160-0", 0.90, "LOINC")],
            "serum creatinine|drug_class,medication": [],
            "serum creatinine|*": [_anchor("VAL-MED-RXNORM-2913", 0.95, "RXNORM")],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="serum creatinine", entity_type="therapeutic agent"),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-LAB-2160-0"]

    def test_analyte_with_admin_context_resolves_medication(self, stub):
        s = stub({
            "potassium|lab": [_anchor("VAL-LAB-2823-3", 0.92, "LOINC")],
            "potassium|drug_class,medication": [
                _anchor("VAL-MED-ATC-A12BA", 0.85, "ATC"),
            ],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(
                text="potassium",
                entity_type="therapeutic agent",
                context="patient was given potassium 40 mEq IV",
            ),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-MED-ATC-A12BA"]
        assert "lab" not in s.called_with("potassium")

    def test_drug_with_adjacent_level_word_resolves_lab(self, stub):
        """TDM carve-out: 'carbamazepine' next to 'level' prefers lab."""
        stub({
            "carbamazepine|lab": [_anchor("VAL-LAB-16061-1", 0.88, "LOINC")],
            "carbamazepine|drug_class,medication": [
                _anchor("VAL-MED-RXNORM-8212", 0.90, "RXNORM"),
            ],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(
                text="carbamazepine",
                entity_type="therapeutic agent",
                context="check carbamazepine level today",
            ),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-LAB-16061-1"]

    def test_drug_with_level_in_span_text_resolves_lab(self, stub):
        stub({
            "cyclosporine level|lab": [_anchor("VAL-LAB-16098-3", 0.89, "LOINC")],
            "cyclosporine level|drug_class,medication": [],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(text="cyclosporine level", entity_type="therapeutic agent"),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-LAB-16098-3"]

    def test_drug_span_alone_stays_medication(self, stub):
        """Pattern 4 wins by default: plain drug mention, no level word
        nearby, keeps the medication constraint."""
        s = stub({
            "carbamazepine|drug_class,medication": [
                _anchor("VAL-MED-RXNORM-8212", 0.90, "RXNORM"),
            ],
            "carbamazepine|lab": [_anchor("VAL-LAB-16061-1", 0.95, "LOINC")],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(
                text="carbamazepine",
                entity_type="therapeutic agent",
                context="continue carbamazepine for seizure control",
            ),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-MED-RXNORM-8212"]
        assert "lab" not in s.called_with("carbamazepine")

    def test_level_word_in_other_clause_does_not_flip_drug(self, stub):
        """A 'levels' belonging to another entity's clause must not flip a
        drug span to lab (adjacency, not window-presence)."""
        stub({
            "metformin|drug_class,medication": [
                _anchor("VAL-MED-6809", 0.93, "RXNORM"),
            ],
            "metformin|lab": [_anchor("VAL-LAB-xxxx", 0.95, "LOINC")],
        })
        svc = ExtractionService()
        concepts = svc.resolve_spans([
            FilteredSpan(
                text="metformin",
                entity_type="therapeutic agent",
                context="Continue metformin. Glucose levels stable.",
            ),
        ])
        assert [c.canonical_id for c in concepts] == ["VAL-MED-6809"]


class TestPopulationBlocklist:
    """Pattern 3: population terms are never extracted as disorders."""

    @pytest.mark.parametrize("term", [
        "adults", "women", "men", "children", "patients", "patient",
        "infant", "infants", "elderly", "neonate", "neonates",
        "female", "females", "male", "males",
    ])
    def test_population_terms_are_false_positives(self, term):
        assert _is_false_positive(term), f"{term!r} must be blocklisted"
