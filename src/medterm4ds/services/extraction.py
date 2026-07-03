"""Text extraction service: free text → medical concepts.

Decomposed into two independent steps:

  find_terms(text) → list[FilteredSpan]
    Clinical NLP pipeline (medspaCy + NER). Returns text spans with
    ConText annotations (negation, uncertainty, temporality) and section
    detection. No code resolution. Does not require SapBERT/BM25.

  resolve_spans(spans) → list[ExtractedConcept]
    Takes filtered spans and resolves each to a code via SearchService
    (BM25 + SapBERT). Does not require medspaCy.

  extract(text, format=...) → list[FilteredSpan] | list[ExtractedConcept]
    Convenience: calls find_terms + (optionally) resolve_spans.
    format="codes" (default): full pipeline.
    format="terms": NLP only, skips SapBERT.

Requires [medterm4ds,extraction] extra: pip install medterm4ds[extraction]
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from medterm4ds.core.models import CodeRef

logger = logging.getLogger(__name__)

DEFAULT_NER_MODEL = os.getenv("MEDTERM4DS_NER_MODEL", "d4data/biomedical-ner-all")
DEFAULT_SEARCH_MODE = os.getenv("MEDTERM4DS_EXTRACTION_MODE", "hybrid")
DEFAULT_MIN_GRADE = os.getenv("MEDTERM4DS_EXTRACTION_MIN_GRADE", "certain")
DEFAULT_SECTIONS = tuple(
    s.strip()
    for s in os.getenv(
        "MEDTERM4DS_SECTION_ALLOWLIST",
        "Assessment,Assessment and Plan,Past Medical History,Problem List,Diagnosis,Diagnoses",
    ).split(",")
    if s.strip()
)

# NER entity type → search category
DEFAULT_CATEGORY_MAP: dict[str, str] = {
    "DISEASE": "condition",
    "DISORDER": "condition",
    "DISEASE_DISORDER": "condition",
    "SYMPTOM": "condition",
    "SIGN_SYMPTOM": "condition",
    "DIAGNOSTIC_PROCEDURE": "condition",
    "MEDICATION": "medication",
    "CHEMICAL": "medication",
    "DRUG": "medication",
    "PHARMACOLOGIC_SUBSTANCE": "medication",
    "CLINICAL_DRUG": "medication",
    "LAB": "lab",
    "LABORATORY_PROCEDURE": "lab",
    "LABORATORY_TEST_RESULT": "lab",
    "PROCEDURE": "procedure",
    "THERAPEUTIC_PROCEDURE": "procedure",
    "BIOLOGICAL_STRUCTURE": "body_structure",
    "BODY_PART_ORGAN_ORGAN_COMPONENT": "body_structure",
}

_GRADE_ORDER = {"certain": 0, "probable": 1, "possible": 2}


@dataclass
class FilteredSpan:
    """A medical term found in free text, after NLP filtering."""

    text: str
    entity_type: str
    status: str  # "affirmed" | "negated" | "uncertain" | "historical"
    section: str | None = None
    span_start: int = 0
    span_end: int = 0
    ner_confidence: float = 1.0

    @property
    def category(self) -> str:
        return DEFAULT_CATEGORY_MAP.get(self.entity_type.upper(), "condition")

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "entity_type": self.entity_type,
            "category": self.category,
            "status": self.status,
            "section": self.section,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "ner_confidence": self.ner_confidence,
        }


@dataclass
class ExtractedConcept:
    """A medical concept extracted from text and resolved to a code."""

    code: str
    source: str
    display: str
    matched_text: str
    status: str
    section: str | None = None
    confidence: float = 0.0
    match_grade: str = "possible"
    category: str = ""
    span_start: int = 0
    span_end: int = 0

    @property
    def system_label(self) -> str:
        labels = {
            "SNOMEDCT_US": "snomedct_us", "RXNORM": "rxnorm",
            "ICD10CM": "icd10", "ICD10PCS": "icd10pcs",
            "LNC": "lnc", "CPT": "cpt", "HCPCS": "hcpcs", "CVX": "cvx",
        }
        return labels.get(self.source, self.source.lower())

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source": self.source,
            "system": self.system_label,
            "display": self.display,
            "matched_text": self.matched_text,
            "status": self.status,
            "section": self.section,
            "confidence": self.confidence,
            "match_grade": self.match_grade,
            "category": self.category,
            "span_start": self.span_start,
            "span_end": self.span_end,
        }

    def to_coderef(self) -> CodeRef:
        return CodeRef(source=self.source, code=self.code)


class NlpPipeline:
    """medspaCy pipeline with NER and ConText.

    Lazy-loaded on first use. Combines:
    - medspaCy sentence segmentation (PyRuSH)
    - HuggingFace NER model for entity extraction
    - medspaCy ConText for negation/uncertainty/historical detection
    """

    def __init__(self, *, ner_model: str = DEFAULT_NER_MODEL):
        self._ner_model_name = ner_model
        self._nlp = None
        self._ner_pipeline = None

    def _ensure_loaded(self):
        if self._nlp is not None:
            return

        import logging as _logging
        _logging.getLogger("PyRuSH").setLevel(_logging.WARNING)

        import medspacy
        from medspacy.target_matcher import TargetMatcher, TargetRule

        self._nlp = medspacy.load(disable=["medspacy_target_matcher"])

        # Load HuggingFace NER model
        from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline as hf_pipeline

        tokenizer = AutoTokenizer.from_pretrained(self._ner_model_name)
        model = AutoModelForTokenClassification.from_pretrained(self._ner_model_name)
        self._ner_pipeline = hf_pipeline(
            "ner", model=model, tokenizer=tokenizer, aggregation_strategy="simple",
        )

        logger.info("NLP pipeline loaded (medspaCy + %s)", self._ner_model_name)

    def process(self, text: str) -> list[FilteredSpan]:
        """Process text and return filtered entity spans."""
        self._ensure_loaded()

        # Step 1: Run HuggingFace NER
        raw_entities = self._ner_pipeline(text)

        # Step 2: Post-process NER output (merge fragmented tokens)
        entities = self._merge_entities(raw_entities, text)

        # Step 3: Run through medspaCy Doc for ConText annotation
        doc = self._nlp(text)

        # Step 4: Add HuggingFace entities as spaCy spans so ConText can annotate them
        from spacy.tokens import Span

        spacy_spans = []
        for ent_text, ent_type, start, end, score in entities:
            span = doc.char_span(start, end, label=ent_type, alignment_mode="expand")
            if span is not None:
                spacy_spans.append(span)

        # Set doc.ents so ConText can process them
        if spacy_spans:
            try:
                doc.set_ents(spacy_spans)
            except Exception:
                # Overlapping spans — filter to non-overlapping
                filtered = []
                last_end = -1
                for s in sorted(spacy_spans, key=lambda x: x.start):
                    if s.start >= last_end:
                        filtered.append(s)
                        last_end = s.end
                doc.set_ents(filtered)

            # Re-run ConText on the updated entities
            if "medspacy_context" in self._nlp.pipe_names:
                context = self._nlp.get_pipe("medspacy_context")
                context(doc)

        # Step 5: Build FilteredSpan results with ConText annotations
        spans: list[FilteredSpan] = []
        for i, (ent_text, ent_type, start, end, score) in enumerate(entities):
            status = "affirmed"
            # Check if a matching spaCy entity has ConText annotations
            for ent in doc.ents:
                if ent.start_char <= start < ent.end_char or (start <= ent.start_char < end):
                    negated = getattr(ent._, "is_negated", False)
                    uncertain = getattr(ent._, "is_uncertain", False)
                    historical = getattr(ent._, "is_historical", False)
                    if negated:
                        status = "negated"
                    elif uncertain:
                        status = "uncertain"
                    elif historical:
                        status = "historical"
                    break

            spans.append(FilteredSpan(
                text=ent_text,
                entity_type=ent_type,
                status=status,
                span_start=start,
                span_end=end,
                ner_confidence=score,
            ))

        return spans

    def _merge_entities(self, raw_entities: list[dict], text: str) -> list[tuple[str, str, int, int, float]]:
        """Merge fragmented NER tokens into clean entity spans.

        The HuggingFace NER model sometimes splits words (e.g., "metformin" →
        "met" + "formin"). This method merges adjacent entities of the same
        type and extracts the original text from character offsets.
        """
        if not raw_entities:
            return []

        merged: list[tuple[str, str, int, int, float]] = []
        current = None

        for ent in raw_entities:
            entity_type = ent["entity_group"]
            start = ent["start"]
            end = ent["end"]
            score = ent["score"]

            if current and entity_type == current[1] and start <= current[3] + 1:
                # Merge with previous
                current = (
                    text[current[2]:max(end, current[3])],
                    current[1],
                    current[2],
                    max(end, current[3]),
                    max(score, current[4]),
                )
            else:
                if current:
                    merged.append(current)
                current = (text[start:end], entity_type, start, end, score)

        if current:
            merged.append(current)

        # Filter out very short or low-confidence entities
        return [(t, ty, s, e, sc) for t, ty, s, e, sc in merged if len(t) >= 2 and sc >= 0.3]


class ExtractionService:
    """Unified text extraction service.

    find_terms(): NLP only (medspaCy + NER). No SapBERT needed.
    resolve_spans(): code resolution via SearchService.
    extract(): convenience wrapper calling both.
    """

    def __init__(
        self,
        *,
        ner_model: str = DEFAULT_NER_MODEL,
        search_mode: str = DEFAULT_SEARCH_MODE,
        min_grade: str = DEFAULT_MIN_GRADE,
        section_allowlist: tuple[str, ...] = DEFAULT_SECTIONS,
        category_mapping: dict[str, str] | None = None,
    ):
        self._nlp = NlpPipeline(ner_model=ner_model)
        self._search_mode = search_mode
        self._min_grade = min_grade
        self._section_allowlist = section_allowlist
        self._category_map = category_mapping or DEFAULT_CATEGORY_MAP

    def find_terms(
        self,
        text: str,
        *,
        categories: list[str] | None = None,
        include_negated: bool = False,
        include_uncertain: bool = False,
        include_historical: bool = False,
        section_allowlist: tuple[str, ...] | None = None,
    ) -> list[FilteredSpan]:
        """Extract medical terms from free text.

        Returns FilteredSpan objects with text, entity type, ConText status,
        and character offsets. No code resolution — use resolve_spans() or
        extract(format='codes') for that.

        Does NOT require SapBERT/BM25 indexes.
        """
        spans = self._nlp.process(text)

        # Filter by ConText status
        allowed_statuses = {"affirmed"}
        if include_negated:
            allowed_statuses.add("negated")
        if include_uncertain:
            allowed_statuses.add("uncertain")
        if include_historical:
            allowed_statuses.add("historical")

        spans = [s for s in spans if s.status in allowed_statuses]

        # Filter by category
        if categories:
            cat_set = set(categories)
            spans = [s for s in spans if self._category_map.get(s.entity_type.upper(), "condition") in cat_set]

        # Deduplicate by text (keep highest confidence)
        seen: dict[str, FilteredSpan] = {}
        for s in spans:
            key = s.text.lower()
            if key not in seen or s.ner_confidence > seen[key].ner_confidence:
                seen[key] = s

        return sorted(seen.values(), key=lambda s: s.span_start)

    def resolve_spans(
        self,
        spans: list[FilteredSpan],
        *,
        mode: str | None = None,
        min_grade: str | None = None,
        count: int = 1,
    ) -> list[ExtractedConcept]:
        """Resolve filtered spans to coded concepts via search.

        Takes FilteredSpan objects and searches each span text against
        the terminology index (BM25 + SapBERT). Returns ExtractedConcept
        objects with code, display, confidence.

        Does NOT require medspaCy/NER model.
        """
        from medterm4ds.services.search import get_search_service

        search = get_search_service()
        search_mode = mode or self._search_mode
        grade_threshold = min_grade or self._min_grade

        concepts: list[ExtractedConcept] = []
        for span in spans:
            category = self._category_map.get(span.entity_type.upper(), "condition")
            results = search.search(
                span.text,
                mode=search_mode,
                count=count,
            )
            # Filter by category and min_grade
            for r in results:
                if r.category and r.category != category:
                    continue
                if _GRADE_ORDER.get(r.match_grade, 2) > _GRADE_ORDER.get(grade_threshold, 0):
                    continue
                concepts.append(ExtractedConcept(
                    code=r.code,
                    source=r.source,
                    display=r.display,
                    matched_text=span.text,
                    status=span.status,
                    section=span.section,
                    confidence=r.score,
                    match_grade=r.match_grade,
                    category=category,
                    span_start=span.span_start,
                    span_end=span.span_end,
                ))
                break  # Only take top result per span

        # Deduplicate by code (keep highest confidence)
        seen: dict[str, ExtractedConcept] = {}
        for c in concepts:
            key = f"{c.source}:{c.code}"
            if key not in seen or c.confidence > seen[key].confidence:
                seen[key] = c

        return sorted(seen.values(), key=lambda c: c.confidence, reverse=True)

    def extract(
        self,
        text: str,
        *,
        format: str = "codes",
        categories: list[str] | None = None,
        mode: str | None = None,
        min_grade: str | None = None,
        include_negated: bool = False,
        include_uncertain: bool = False,
        include_historical: bool = False,
    ) -> list[FilteredSpan] | list[ExtractedConcept]:
        """Extract medical concepts from free text.

        Parameters
        ----------
        text : Free clinical text.
        format : "codes" (default) returns ExtractedConcept with resolved codes.
                 "terms" returns FilteredSpan (text spans only, no SapBERT).
        categories : Restrict to search categories (e.g., ["condition", "medication"]).
        mode : Search mode for code resolution ("lexical", "semantic", "hybrid").
        min_grade : Minimum match grade ("certain", "probable", "possible").
        include_negated / include_uncertain / include_historical : Include
            filtered-out ConText statuses.
        """
        spans = self.find_terms(
            text,
            categories=categories,
            include_negated=include_negated,
            include_uncertain=include_uncertain,
            include_historical=include_historical,
        )

        if format == "terms":
            return spans

        return self.resolve_spans(spans, mode=mode, min_grade=min_grade)


# Singleton
_service: ExtractionService | None = None


def get_extraction_service() -> ExtractionService:
    global _service
    if _service is None:
        _service = ExtractionService()
    return _service


def find_terms(text: str, **kwargs) -> list[FilteredSpan]:
    """Extract medical terms from free text (NLP only, no code resolution)."""
    return get_extraction_service().find_terms(text, **kwargs)


def resolve_spans(spans: list[FilteredSpan], **kwargs) -> list[ExtractedConcept]:
    """Resolve filtered spans to coded concepts via search."""
    return get_extraction_service().resolve_spans(spans, **kwargs)


def extract(text: str, *, format: str = "codes", **kwargs) -> list[FilteredSpan] | list[ExtractedConcept]:
    """Extract medical concepts from free text.

    format="codes" (default): returns ExtractedConcept with resolved codes.
    format="terms": returns FilteredSpan (text spans only).
    """
    return get_extraction_service().extract(text, format=format, **kwargs)
