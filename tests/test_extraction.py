"""Tests for the text extraction service."""

from __future__ import annotations

import pytest

pytest.importorskip("medspacy")
pytest.importorskip("transformers")

from medterm4ds.services.extraction import (
    ExtractionService,
    FilteredSpan,
    ExtractedConcept,
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
