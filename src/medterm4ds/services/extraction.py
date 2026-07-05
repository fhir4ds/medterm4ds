"""Text extraction service: free text → medical concepts.

Decomposed into two independent steps:

  find_terms(text) → list[FilteredSpan]
    Clinical NLP pipeline (medspaCy + GLiNER NER). Returns text spans with
    ConText annotations (negation, uncertainty, temporality). No code
    resolution. Does not require SapBERT/BM25.

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
from dataclasses import dataclass
from typing import Any

from medterm4ds.core.models import CodeRef
from medterm4ds.core.normalize import source_label

logger = logging.getLogger(__name__)

DEFAULT_NER_MODEL = os.getenv("MEDTERM4DS_NER_MODEL", "E3-JSI/gliner-multi-med-ner-synthetic-v1")
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

# GLiNER labels → search category
# GLiNER is zero-shot: labels are passed at query time, not baked into the model.
# These are the default labels; callers can override via ExtractionService(labels=...).
DEFAULT_LABELS = ["disease", "medication", "symptom", "procedure", "lab test", "body structure"]

_LABEL_TO_CATEGORY: dict[str, str] = {
    "disease": "condition",
    "medication": "medication",
    "symptom": "condition",
    "procedure": "procedure",
    "lab test": "lab",
    "body structure": "body_structure",
}

_GRADE_ORDER = {"certain": 0, "probable": 1, "possible": 2}

# Common non-medical words that GLiNER may wrongly classify at threshold 0.3
_FALSE_POSITIVE_WORDS = frozenset({
    "patient", "patients", "male", "female", "man", "woman", "child",
    "year", "years", "old", "age", "date", "time", "day", "days", "week",
    "hospital", "clinic", "center", "department", "service",
    "doctor", "nurse", "physician", "provider",
    "family", "mother", "father", "brother", "sister",
    "plan", "assessment", "note", "notes", "report",
    "presents", "presenting", "admitted", "discharged",
    "normal", "stable", "unremarkable", "well", "good",
    "left", "right", "bilateral", "upper", "lower",
    "yes", "no", "not", "and", "or", "with", "without",
})

# Negation/uncertainty/historical trigger patterns that GLiNER may include
# in the entity span itself. When detected, the trigger is stripped and the
# status is set accordingly.
import re as _re

_NEGATION_TRIGGERS = [
    (r"^(no\s+evidence\s+of\s+|no\s+sign\s+of\s+|no\s+signs\s+of\s+|no\s+history\s+of\s+)", "negated"),
    (r"^(no\s+|without\s+|absent\s+|negative\s+for\s+|denies?\s+|denied\s+|rules?\s+out\s+|ruled\s+out\s+|free\s+of\s+)", "negated"),
    (r"^(possible\s+|possibly\s+|may\s+have\s+|might\s+have\s+|could\s+be\s+|suspected\s+|suspect\s+|suggestive\s+of\s+|concerning\s+for\s+|likely\s+)", "uncertain"),
    (r"^(history\s+of\s+|hx\s+of\s+|hx\s+|past\s+|previously\s+had\s+|prior\s+|remote\s+)", "historical"),
]


def _detect_inline_trigger(entity_text: str) -> tuple[str, str]:
    """Check if entity text starts with a negation/uncertainty/historical trigger.

    Returns (cleaned_text, status). If no trigger found, returns (text, "affirmed").
    """
    lower = entity_text.lower().strip()
    for pattern, status in _NEGATION_TRIGGERS:
        match = _re.match(pattern, lower)
        if match:
            # Strip the trigger from the entity text
            cleaned = entity_text[match.end():].strip()
            if cleaned and len(cleaned) >= 2:
                return cleaned, status
    return entity_text, "affirmed"


def _is_false_positive(text: str) -> bool:
    """Check if an entity text is a common non-medical word."""
    lower = text.lower().strip()
    if lower in _FALSE_POSITIVE_WORDS:
        return True
    # Single short word that's not a medical term
    if len(lower) <= 3 and not any(c.isdigit() for c in lower):
        return True
    return False


@dataclass
class FilteredSpan:
    """A medical term found in free text, after NLP filtering."""

    text: str
    entity_type: str  # GLiNER label (e.g., "disease", "medication")
    status: str  # "affirmed" | "negated" | "uncertain" | "historical"
    section: str | None = None
    span_start: int = 0
    span_end: int = 0
    ner_confidence: float = 1.0

    @property
    def category(self) -> str:
        return _LABEL_TO_CATEGORY.get(self.entity_type.lower(), "condition")

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
        # Delegates to core.normalize.source_label — single source of truth
        # shared with SearchResult.system_label. Previously had an inline
        # dict that could drift from search.py's _SOURCE_LABELS.
        return source_label(self.source)

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
    """medspaCy pipeline with GLiNER zero-shot NER.

    Combines:
    - GLiNER zero-shot NER for entity extraction (configurable labels)
    - medspaCy ConText for negation/uncertainty/historical detection
    - medspaCy sentence segmentation

    Lazy-loaded on first use.
    """

    def __init__(
        self,
        *,
        ner_model: str = DEFAULT_NER_MODEL,
        labels: list[str] | None = None,
        threshold: float = 0.3,
    ):
        self._ner_model_name = ner_model
        self._labels = labels or DEFAULT_LABELS
        self._threshold = threshold
        self._nlp = None
        self._ner_model = None

    def _ensure_loaded(self):
        if self._nlp is not None:
            return

        import logging as _logging
        _logging.getLogger("PyRuSH").setLevel(_logging.WARNING)

        # Load GLiNER
        from gliner import GLiNER
        self._ner_model = GLiNER.from_pretrained(self._ner_model_name)

        # Load medspaCy for ConText (disable target_matcher — we use GLiNER instead)
        import medspacy
        self._nlp = medspacy.load(disable=["medspacy_target_matcher"])

        logger.info("NLP pipeline loaded (GLiNER %s + medspaCy ConText)", self._ner_model_name)

    def process(self, text: str) -> list[FilteredSpan]:
        """Process text and return filtered entity spans."""
        self._ensure_loaded()

        # Step 1: GLiNER zero-shot NER
        raw_entities = self._ner_model.predict_entities(
            text, self._labels, threshold=self._threshold
        )

        if not raw_entities:
            return []

        # Step 2: Run through medspaCy Doc for ConText annotation
        doc = self._nlp(text)

        # Step 3: Add GLiNER entities as spaCy spans so ConText can annotate them
        from spacy.tokens import Span

        spacy_spans = []
        for ent in raw_entities:
            span = doc.char_span(
                ent["start"], ent["end"],
                label=ent["label"],
                alignment_mode="expand",
            )
            if span is not None:
                spacy_spans.append(span)

        if spacy_spans:
            try:
                doc.set_ents(spacy_spans)
            except Exception:
                # Handle overlapping spans — keep non-overlapping subset
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

        # Step 4: Build FilteredSpan results with ConText annotations
        spans: list[FilteredSpan] = []
        for ent in raw_entities:
            start = ent["start"]
            end = ent["end"]
            ent_type = ent["label"]
            ent_text = text[start:end]
            score = ent["score"]

            # Filter false positives (non-medical words GLiNER may return)
            if _is_false_positive(ent_text):
                continue

            # Check for negation/uncertainty/historical triggers embedded in the entity text
            cleaned_text, inline_status = _detect_inline_trigger(ent_text)
            if inline_status != "affirmed":
                # The entity text started with a trigger like "No evidence of CKD"
                # Use the cleaned text and the detected status
                status = inline_status
                display_text = cleaned_text
            else:
                # Check ConText status by finding the matching spaCy entity
                status = "affirmed"
                display_text = ent_text
                for spacy_ent in doc.ents:
                    if (spacy_ent.start_char <= start < spacy_ent.end_char or
                        start <= spacy_ent.start_char < end):
                        negated = getattr(spacy_ent._, "is_negated", False)
                        uncertain = getattr(spacy_ent._, "is_uncertain", False)
                        historical = getattr(spacy_ent._, "is_historical", False)
                        if negated:
                            status = "negated"
                        elif uncertain:
                            status = "uncertain"
                        elif historical:
                            status = "historical"
                        break

            spans.append(FilteredSpan(
                text=display_text,
                entity_type=ent_type,
                status=status,
                span_start=start,
                span_end=end,
                ner_confidence=score,
            ))

        return spans


class ExtractionService:
    """Unified text extraction service."""

    def __init__(
        self,
        *,
        ner_model: str = DEFAULT_NER_MODEL,
        labels: list[str] | None = None,
        threshold: float = 0.3,
        search_mode: str = DEFAULT_SEARCH_MODE,
        min_grade: str = DEFAULT_MIN_GRADE,
        section_allowlist: tuple[str, ...] = DEFAULT_SECTIONS,
    ):
        self._nlp = NlpPipeline(
            ner_model=ner_model,
            labels=labels or DEFAULT_LABELS,
            threshold=threshold,
        )
        self._search_mode = search_mode
        self._min_grade = min_grade
        self._section_allowlist = section_allowlist

    def find_terms(
        self,
        text: str,
        *,
        categories: list[str] | None = None,
        include_negated: bool = False,
        include_uncertain: bool = False,
        include_historical: bool = False,
    ) -> list[FilteredSpan]:
        """Extract medical terms from free text.

        Returns FilteredSpan objects. No code resolution.
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
            spans = [s for s in spans if s.category in cat_set]

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
    ) -> list[ExtractedConcept]:
        """Resolve filtered spans to coded concepts via search."""
        from medterm4ds.services.search import get_search_service

        search = get_search_service()
        search_mode = mode or self._search_mode
        grade_threshold = min_grade or self._min_grade

        concepts: list[ExtractedConcept] = []
        for span in spans:
            results = search.search(
                span.text,
                mode=search_mode,
                count=1,
            )
            for r in results:
                if r.category and r.category != span.category:
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
                    category=span.category,
                    span_start=span.span_start,
                    span_end=span.span_end,
                ))
                break

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
        """Extract medical concepts from free text."""
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
    """Extract medical concepts from free text."""
    return get_extraction_service().extract(text, format=format, **kwargs)
