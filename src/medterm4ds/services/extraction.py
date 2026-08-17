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
import re
from dataclasses import dataclass, field
from typing import Any

from medterm4ds.core.models import CodeRef
from medterm4ds.core.normalize import source_label

logger = logging.getLogger(__name__)

DEFAULT_NER_MODEL = os.getenv("MEDTERM4DS_NER_MODEL", "knowledgator/gliner-bi-small-v2.0")
# Pinned revision (commit SHA) of DEFAULT_NER_MODEL so Hugging Face weight
# drift can't silently change extraction recall — drift observed 2026-08-14.
# Override (or disable with an empty value) via MEDTERM4DS_NER_MODEL_REVISION.
DEFAULT_NER_MODEL_REVISION = os.getenv("MEDTERM4DS_NER_MODEL_REVISION", "3d74c1bf459b8b1c0be1ecbddd679416ce005418") or None
# Canonical mode is the default — it searches canonical anchor names directly
# via the FAISS concept index, with a 0.70 confidence floor that filters weak
# matches. Hybrid/lexical modes still available for callers who want raw UMLS.
DEFAULT_SEARCH_MODE = os.getenv("MEDTERM4DS_EXTRACTION_MODE", "canonical")
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
# These are the default labels; callers can override via ExtractionService(labels=...)
# or per-call via find_terms(ner_labels=...) / extract(ner_labels=...).
# Label set calibrated by the model team against a 185-entity golden test set
# (Aug 2026) with knowledgator/gliner-bi-small-v2.0 at threshold 0.15.
DEFAULT_LABELS = [
    "lab test", "vital sign", "panel",
    "therapeutic agent", "therapeutic class", "immunization",
    "medical intervention", "disorder", "symptom",
]

# NER confidence threshold. Lower = more recall, more false positives.
# 0.15 calibrated by the model team for the knowledgator model + 9-label set.
DEFAULT_THRESHOLD = float(os.getenv("MEDTERM4DS_NER_THRESHOLD", "0.15"))

# NLP label → canonical search result types (passed to SearchService.canonical
# as result_types=...). Values are the label's canonical anchor categories.
#
# Constrain-then-fallback (QC-182 follow-up, 2026-08-16): an external QC
# report found 10,433 wrong-type extractions — diseases resolving to lab
# anchors (diabetes → LOINC LP128793-9), lab analytes resolving to drug
# anchors (creatinine → RxNorm 2913), drugs resolving to their drug-level
# LOINC (carbamazepine → LP16061-1). GLiNER's label is usually RIGHT even
# when SapBERT's top unfiltered hit is the wrong category, so resolve_spans
# first searches with the label's categories; if nothing clears the grade
# floor it retries UNFILTERED — a constraint can never reduce recall vs. the
# old all-None behavior (QC-182's original concern, e.g. "serum creatinine"
# mislabeled "vital sign", is covered by that fallback). Explicit caller
# result_types still wins and stays a hard filter (QC-153).
#
# Adjacent clinical types are paired (condition+symptom, medication+
# drug_class) so near-miss labels resolve without burning the fallback.
_LABEL_TO_SEARCH_CATEGORIES: dict[str, str | list[str] | None] = {
    "lab test": "lab",
    "panel": "lab",
    # Vitals share a measurement namespace with labs, and GLiNER often tags
    # plain labs as "vital sign" ("serum creatinine") — allow both.
    "vital sign": ["vital", "lab"],
    "therapeutic agent": ["medication", "drug_class"],
    "therapeutic class": ["drug_class", "medication"],
    "immunization": "vaccine",
    "medical intervention": "procedure",
    "disorder": ["condition", "symptom"],
    "symptom": ["symptom", "condition"],
    # Back-compat labels
    "disease": ["condition", "symptom"],
    "medication": ["medication", "drug_class"],
    "procedure": "procedure",
    "vital": ["vital", "lab"],
    # No body-structure anchors exist in the canonical vocabulary — no
    # constraint is possible (the fallback search handles these spans).
    "body structure": None,
}

# ConText tie-breaker cue lexicons (QC pattern 2 fix, 2026-08-16). GLiNER's
# label cannot distinguish "creatinine" the measurement from "creatinine"
# the RxNorm chemical — the biggest wrong-type pattern (1,046 lab analytes
# typed "therapeutic agent"). Two custom medspaCy ConText categories read
# the SENTENCE context instead: MEASUREMENT cues (span is being
# measured/monitored → lab anchor) and ADMINISTRATION cues (span is being
# given/administered → medication anchor). Evaluated at 100% accuracy on
# decided items over a 60-item corpus (docs/.ai_loop/qc_comp/
# lexicon_tiebreaker_results.md); undecided items conservatively keep the
# GLiNER label mapping.
#
# Cues are single literals phrase-matched case-insensitively; directions
# are BIDIRECTIONAL everywhere (cues legitimately precede "level of
# vancomycin" and follow "carbamazepine level"; sentence-bounded default
# scopes + same-category scope limiting do the cue-to-entity assignment).
_MEASUREMENT_CUES = (
    "level", "levels", "measured", "resulted", "resulting", "screening",
    "screened", "monitor", "monitored", "monitoring", "drawn", "panel",
    "CBC", "BMP", "CMP", "serum", "plasma", "urine", "lab", "labs",
    "laboratory", "quantitative", "checked", "checking", "pending",
    "elevated", "low", "high", "normal", "abnormal", "trough", "peak",
    "reference", "range", "reference range", "obtained", "collected",
    "specimen", "fasting", "random", "spot", "ordered", "supplemented",
    "corrected",
)

# Administration cues. Note vs. the eval spec: "repleted", "infused" and
# "drip" live HERE, not with the measurement cues — the corpus gold for
# "calcium gluconate infused" / "sodium bicarbonate drip" / "magnesium
# repleted" is medication (they are the eval's named cue gaps to close).
# "replaced" is administration-only despite appearing in both spec lists:
# medspaCy prunes overlapping same-span modifiers, so a literal registered
# in two categories fires as only one — administration matches the corpus
# gold ("potassium replaced" = medication).
_ADMINISTRATION_CUES = (
    "given", "gave", "administered", "administration", "started", "stopped",
    "switched", "IV", "intravenously", "PO", "orally", "daily", "BID", "TID",
    "QID", "nightly", "weekly", "dose", "dosing", "dosage", "prescribed",
    "infusion", "injected", "injection", "push", "pushed", "supplement",
    "supplementation", "tablet", "capsule", "cream", "ointment", "patch",
    "mEq", "mg", "mcg", "units", "held", "hold", "resumed", "increased",
    "decreased", "titrated", "tolerated", "replaced", "taken",
    "repleted", "infused", "drip",
)

# Slashed result units ("250 mg/dL") as MEASUREMENT regex patterns. The
# medspaCy tokenizer splits "mg/dL" into tokens, so the bare "mg"
# ADMINISTRATION cue above would fire on plain lab-value sentences and
# flip analytes to medications ("calcium 8.9 mg/dL"). These regex rules
# (matched over the raw doc text) fire MEASUREMENT on the same sentence,
# keeping analyte-with-unit spans on the lab side.
_LAB_RESULT_UNIT_REGEXES = (
    "mg/dl", "mcg/dl", "ng/ml", "ng/dl", "pg/ml", "g/dl",
    "mmol/l", "meq/l", "u/ml", "iu/l",
)


def _register_context_arbiter(nlp) -> bool:
    """Register the MEASUREMENT/ADMINISTRATION ConText rules on a medspaCy
    pipeline (call once per pipeline; NlpPipeline._ensure_loaded and tests
    that build a medspaCy pipeline without GLiNER both route through here).

    Returns True when the rules were registered, False when the pipeline
    has no medspacy_context pipe (nothing to arbitrate with).
    """
    if "medspacy_context" not in getattr(nlp, "pipe_names", ()):
        return False
    from medspacy.context import ConTextRule
    from spacy.tokens import Span

    ctx = nlp.get_pipe("medspacy_context")
    # Span attributes set on targets modified by each category. Copy the
    # mapping first — assigning into it in place would mutate medspaCy's
    # module-level DEFAULT_ATTRIBUTES for every ConText instance.
    Span.set_extension("is_measurement", default=False, force=True)
    Span.set_extension("is_administration", default=False, force=True)
    attrs = dict(ctx.context_attributes_mapping or {})
    attrs["MEASUREMENT"] = {"is_measurement": True}
    attrs["ADMINISTRATION"] = {"is_administration": True}
    ctx.context_attributes_mapping = attrs

    def _unit_pattern(unit: str) -> str:
        # "mg/dl" → \bmg\s*/\s*dl\b — tolerates "mg / dl" spacing variants.
        return r"\b" + re.escape(unit).replace("/", r"\s*/\s*") + r"\b"

    rules = [ConTextRule(literal=c, category="MEASUREMENT")
             for c in _MEASUREMENT_CUES]
    rules += [ConTextRule(literal=u, category="MEASUREMENT",
                          pattern=_unit_pattern(u))
              for u in _LAB_RESULT_UNIT_REGEXES]
    # "was/is/at N" — bare numeric result context ("Creatinine was 2.1",
    # "potassium is 5.2"). These sentences carry no explicit measurement cue
    # word or unit, so Signals 1-2 miss them; the copula+number pattern is
    # the remaining disambiguator (10 no-signal errors in the v2 corpus).
    rules += [ConTextRule(literal="copula-result", category="MEASUREMENT",
                          pattern=r"\b(?:was|is|at)\s+\d+(?:\.\d+)?\b")]
    rules += [ConTextRule(literal=c, category="ADMINISTRATION")
              for c in _ADMINISTRATION_CUES]
    ctx.add(rules)
    return True


def _context_arbitrate_categories(ent) -> list[str] | None:
    """Map a spaCy entity's ConText MEASUREMENT/ADMINISTRATION flags to
    canonical search categories for lab-vs-med disambiguation.

    - MEASUREMENT only → ["lab"]
    - ADMINISTRATION only → ["medication"]
    - both fire → ["lab", "medication"] (search both, anchor ranking picks)
    - neither → None (no override — GLiNER's label mapping stands)
    """
    measurement = bool(getattr(ent._, "is_measurement", False))
    administration = bool(getattr(ent._, "is_administration", False))
    if measurement and administration:
        return ["lab", "medication"]
    if measurement:
        return ["lab"]
    if administration:
        return ["medication"]
    return None


# --- Three-signal lab-vs-med disambiguation -------------------------------
# Signals 1 (head noun) and 2 (unit type) run BEFORE the ConText arbiter
# (Signal 3). Evaluation on the 212-item v2 corpus (docs/.ai_loop/qc_comp/
# three_signal_results.md): signals 1-2 fired 62/62 correct (100% each);
# wiring them ahead of ConText raises overall in-scope accuracy 78.3% ->
# 84.4% (TDM group E: 65% -> 94%). The parser is en_core_web_sm (medspaCy
# ships no dependency parser) — loaded lazily by NlpPipeline, absent here.

# Signal 1 lexicons: head noun of the noun phrase containing the span.
_LAB_HEAD_NOUNS = frozenset({
    "level", "trough", "peak", "concentration", "result", "value",
    "range", "clearance", "panel", "ratio", "index", "count",
    "fraction", "rate", "score", "measurement", "reading",
})

_MED_HEAD_NOUNS = frozenset({
    "dose", "dosage", "regimen", "replacement", "supplementation",
    "infusion", "injection", "tablet", "capsule", "cream",
    "patch", "solution", "suspension", "syrup", "elixir",
})

# Signal 2 regexes: number+unit immediately after the span.
# Concentration units (mass or amount per volume) → lab result values.
# The separator accepts "/" and the word "per" (with optional spaces —
# the parser tokenizer splits "mg/dL" into mg / dL and "mg per dL" into
# three tokens; combined matching is done by the caller).
_CONCENTRATION_UNIT_RE = re.compile(
    r'^(?:\d+\.?\d*\s*)?'
    r'(mg|mcg|µg|ug|g|mmol|µmol|umol|meq|iu|u|ng)'
    r'\s*(?:/|\s*per\s*|\s*per)'
    r'\s*(dl|l|ml)\s*$',
    re.IGNORECASE,
)
# Bare dose units (no denominator) → administered amounts.
_DOSE_UNIT_RE = re.compile(
    r'^(?:\d+\.?\d*\s*)?'
    r'(?:mg|mcg|g|meq|units?|tablets?|tabs?|capsules?|caps?|'
    r'pills?|drops?|puffs?|ml|cc)\s*$',
    re.IGNORECASE,
)


def _head_noun_signal(span, parser_doc) -> list[str] | None:
    """Signal 1: the head noun of the noun chunk containing the span.

    - chunk root lemma in a lexicon → that side decides
    - root uninformative (e.g. "phenytoin level subtherapeutic" roots at
      the mistagged adjective "subtherapeutic") → fall back to the
      rightmost noun/propn token in the chunk whose lemma is in a lexicon
    - span in no chunk → fall back to the syntactic head of the span's
      last token (fixes e.g. "metformin ... held" style attachments where
      the chunker fails); only noun/propn heads decide (a verb head like
      the misparsed "trough" in "Gentamicin trough 2.1" stays undecided)
    """
    for chunk in parser_doc.noun_chunks:
        if chunk.start <= span.start and span.end <= chunk.end:
            root = chunk.root
            lemma = root.lemma_.lower()
            if lemma in _LAB_HEAD_NOUNS:
                return ["lab"]
            if lemma in _MED_HEAD_NOUNS:
                return ["medication"]
            # Adjective/mistagged root: scan the chunk right-to-left for a
            # real noun carrying a lexicon lemma.
            for token in reversed(chunk):
                lemma = token.lemma_.lower()
                if token.pos_ in ("NOUN", "PROPN"):
                    if lemma in _LAB_HEAD_NOUNS:
                        return ["lab"]
                    if lemma in _MED_HEAD_NOUNS:
                        return ["medication"]
                    return None
            return None
    # No containing chunk: syntactic head of the span's last token.
    head = span[-1].head
    if head not in span and head.pos_ in ("NOUN", "PROPN"):
        lemma = head.lemma_.lower()
        if lemma in _LAB_HEAD_NOUNS:
            return ["lab"]
        if lemma in _MED_HEAD_NOUNS:
            return ["medication"]
    return None


def _unit_type_signal(span, parser_doc) -> list[str] | None:
    """Signal 2: a number+unit immediately after the span.

    Concentration units (mg/dL, mmol/L, ...) → lab; bare dose units
    (mg, mEq, tablets, ...) → medication. Checks the 3-token window
    ("mg" "/" "dL" → "mg/dL") BEFORE the single-token dose match so
    "potassium 5.2 mEq/L" is not flipped to medication by its "mEq"
    numerator token.
    """
    for i in range(span.end, min(span.end + 4, len(parser_doc))):
        token = parser_doc[i]
        text = token.text.lower()
        # Combined tokens first: "mg" "/" "dL" or "mg" "per" "dL".
        if i + 2 < len(parser_doc):
            combined = (parser_doc[i].text + parser_doc[i + 1].text
                        + parser_doc[i + 2].text).lower().replace(" ", "")
            if _CONCENTRATION_UNIT_RE.match(combined):
                return ["lab"]
        if _CONCENTRATION_UNIT_RE.match(text):
            return ["lab"]
        if _DOSE_UNIT_RE.match(text):
            return ["medication"]
        if token.like_num or token.text in {".", ",", "/"}:
            continue
        break  # first non-numeric, non-unit token → stop
    return None


def _arbitrate_lab_vs_med(span, parser_doc, context_ent) -> list[str] | None:
    """Three-signal lab-vs-med disambiguation, in priority order.

    Signal 1: head noun of the noun phrase (100% precision in eval)
    Signal 2: unit type on the number after the span (100% precision)
    Signal 3: ConText MEASUREMENT/ADMINISTRATION cues (existing arbiter)

    No signal fired → None (GLiNER's label mapping stands). Higher-priority
    signals win by construction — later signals are only consulted on None.
    """
    if parser_doc is not None and span is not None:
        result = _head_noun_signal(span, parser_doc)
        if result is not None:
            return result
        result = _unit_type_signal(span, parser_doc)
        if result is not None:
            return result
    return _context_arbitrate_categories(context_ent)


def _span_search_categories(span: "FilteredSpan") -> str | list[str] | None:
    """Effective canonical search categories for a span (constrain step).

    ConText arbitration first: when the MEASUREMENT/ADMINISTRATION cues
    around the span produced a decision (captured on
    FilteredSpan.context_categories at find_terms time), it takes precedence
    over the GLiNER label mapping — ConText decided 45/45 contested
    lab-vs-med items correctly where the label is exactly what's wrong.
    No decision (None) falls through to the label mapping as before.
    """
    if span.context_categories:
        return span.context_categories
    return _LABEL_TO_SEARCH_CATEGORIES.get(span.entity_type.lower())


def _categories_to_prefixes(
    categories: str | list[str] | None,
) -> set[str] | None:
    """Map canonical search categories to canonical_id prefixes.

    None (or an empty mapping) → None = no constraint. Raises ValueError for
    unknown categories (delegates to search._result_types_to_prefixes).
    """
    if categories is None:
        return None
    from medterm4ds.services.search import _result_types_to_prefixes
    prefixes = _result_types_to_prefixes(categories)
    return prefixes or None

# result_type → display label for annotation output.
# Maps our internal result_type to the NER label vocabulary the team uses.
# Used in annotation when a span resolves (replaces the sometimes-wrong NER
# label with the correct label from the resolved anchor type).
_RESULT_TYPE_TO_LABEL: dict[str, str] = {
    "lab": "lab test",
    "vital": "vital sign",
    "medication": "therapeutic agent",
    "drug_class": "therapeutic class",
    "vaccine": "immunization",
    "procedure": "medical intervention",
    "condition": "disorder",
    "symptom": "symptom",
}

# NLP label -> source systems. All None — don't restrict search by NER label.
# GLiNER mislabels are common (e.g., "Creatine kinase" tagged as "immunization"
# would block the LOINC lab anchor if we filtered to ["CVX"]). Let SapBERT
# find the best match across all code systems.
_LABEL_TO_SOURCES: dict[str, list[str] | None] = {
    "lab test": None,
    "panel": None,
    "vital sign": None,
    "therapeutic agent": None,
    "therapeutic class": None,
    "immunization": None,
    "medical intervention": None,
    "disorder": None,
    "symptom": None,
    "disease": None,
    "medication": None,
    "procedure": None,
    "vital": None,
    "body structure": None,
}

# Match-grade ordering. Both "certain" (legacy modes) and "exact" (canonical
# mode) are the top grade — canonical renamed the grade but they mean the
# same thing (high-confidence match). "broader" (ancestor fallback in
# canonical mode) ranks below "possible" — extraction callers usually don't
# want lossy broader matches by default. Pass min_grade="broader" to include.
_GRADE_ORDER = {"certain": 0, "exact": 0, "probable": 1, "possible": 2, "broader": 3}

# Regex to strip trailing numeric values and units from span text before search.
# GLiNER often includes the value in the span ("heart rate 80", "BP 120/80",
# "HbA1c 7.2%"). SapBERT embeds "heart rate 80" differently from "heart rate"
# and fails to match the Heart Rate anchor. Stripping values before search
# (while preserving the original span in `matched_text`) fixes this.
# Matches trailing tokens like: "80", "120/80", "7.2%", "37C", "98%", "+", "-".
# QC-188: the unit part must be a single unspaced token — the previous
# `(?:\s*[%°a-zA-Z/]+)?` let " 2 diabetes" match as number+unit, mangling
# "type 2 diabetes" → "type" before search.
_TRAILING_VALUE_RE = re.compile(
    r'\s+'                              # whitespace before value
    r'(?:'
        r'\d+(?:[./]\d+)*'              # number: 80, 120/80, 7.2
        r'(?:[%°a-zA-Z/]+)?'            # optional unspaced unit: %, C, mmHg, mg/dL
        r'|[+\-]'                       # bare + or -
        r'|less|greater|high|low|normal|abnormal'  # common qualifiers
    r')'
    r'(?:[,;]?\s*\d+(?:[./]\d+)*(?:[%°a-zA-Z/]+)?)*'  # additional values
    r'\s*$'
)


def _strip_trailing_values(text: str) -> str:
    """Strip trailing numeric values and units from a span for search.

    "heart rate 80" → "heart rate"
    "BP 120/80" → "BP"
    "HbA1c 7.2%" → "HbA1c"
    "temperature" → "temperature" (unchanged — no trailing value)
    """
    return _TRAILING_VALUE_RE.sub('', text).strip()


# Conjunction patterns for splitting compound spans.
# GLiNER sometimes returns "ALT or AST" or "ALT/AST" as one span. We try the
# full span first; if it doesn't resolve, we split on conjunctions and search
# each part. Handles both spaced ("ALT or AST") and unspaced ("ALT/AST") forms.
_CONJUNCTION_RE = re.compile(
    r'\s+(?:or|and)\s+'       # " or ", " and " (require spaces)
    r'|\s*/\s*'                # "/" with optional surrounding spaces (ALT/AST)
    r'|\s*,\s+',              # ", " (comma separator)
    re.IGNORECASE,
)


def _split_on_conjunction(text: str) -> list[str] | None:
    """Split a span on conjunctions into individual entity candidates.

    "ALT or AST" → ["ALT", "AST"]
    "HDL/LDL"    → ["HDL", "LDL"]
    "ALT, AST"   → ["ALT", "AST"]
    "metformin"  → None (no conjunction)

    Returns None if no split happened (single entity).
    Filters out parts that look like pure numbers (e.g., "120/80" → keep as-is).
    """
    parts = _CONJUNCTION_RE.split(text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        return None
    # Don't split if all parts are numeric (e.g., "120/80" is one value, not two entities)
    if all(re.fullmatch(r'\d+(?:\.\d+)?', p) for p in parts):
        return None
    return parts

# Common non-medical words that GLiNER may wrongly classify at threshold 0.3
_FALSE_POSITIVE_WORDS = frozenset({
    "patient", "patients", "male", "female", "man", "woman", "child",
    # QC report (2026-08-16) pattern 3: populations extracted as disorders
    # (adults 140x, women 73x, patients, ...). Complete the population set —
    # the singular forms were already filtered but plurals/inflections and
    # age-group nouns leaked through and resolved to condition anchors.
    "adults", "men", "women", "children", "infant", "infants",
    "neonate", "neonates", "elderly", "males", "females",
    "year", "years", "old", "age", "date", "time", "day", "days", "week",
    "hospital", "clinic", "center", "department", "service",
    "doctor", "nurse", "physician", "provider",
    "family", "mother", "father", "brother", "sister",
    # QC-189: complete the kinship set — aunt/uncle/cousin/daughter/son/
    # maternal/paternal/grandmother (etc.) leaked through as GLiNER spans
    # in family-history sentences while father/mother were filtered.
    "aunt", "uncle", "cousin", "daughter", "son",
    "maternal", "paternal", "grandmother", "grandfather",
    "grandparent", "grandparents", "sibling", "siblings",
    "nephew", "niece", "relative", "relatives",
    "plan", "assessment", "note", "notes", "report",
    "presents", "presenting", "admitted", "discharged",
    "normal", "stable", "unremarkable", "well", "good",
    "left", "right", "bilateral", "upper", "lower",
    "yes", "no", "not", "and", "or", "with", "without",
    # QC-160: note furniture GLiNER wrongly affirmed at low threshold.
    "evidence", "history", "social history", "family history",
    "past medical history", "review of systems", "physical exam",
    "stage",
})

# Negation/uncertainty/historical trigger patterns that GLiNER may include
# in the entity span itself. When detected, the trigger is stripped and the
# status is set accordingly.

_NEGATION_TRIGGERS = [
    (r"^(no\s+evidence\s+of\s+|no\s+sign\s+of\s+|no\s+signs\s+of\s+|no\s+history\s+of\s+)", "negated"),
    (r"^(no\s+|without\s+|absent\s+|negative\s+for\s+|denies?\s+|denied\s+|rules?\s+out\s+|ruled\s+out\s+|free\s+of\s+)", "negated"),
    (r"^(possible\s+|possibly\s+|may\s+have\s+|might\s+have\s+|could\s+be\s+|suspected\s+|suspect\s+|suggestive\s+of\s+|concerning\s+for\s+|likely\s+)", "uncertain"),
    (r"^(history\s+of\s+|hx\s+of\s+|hx\s+|past\s+|previously\s+had\s+|prior\s+|remote\s+)", "historical"),
]

# Post-entity negation triggers (QC-188): "breast cancer ruled out" carries
# the trigger AFTER the entity. medspaCy ConText missed these in GLiNER
# spans (it annotates its own cue window, not inline span text).
_POST_ENTITY_NEGATION_RE = re.compile(
    r"\s+(?:ruled\s+out|rules\s+out|is\s+ruled\s+out|was\s+ruled\s+out)\s*$",
    re.IGNORECASE,
)


def _detect_inline_trigger(entity_text: str) -> tuple[str, str]:
    """Check if entity text starts with a negation/uncertainty/historical trigger.

    Returns (cleaned_text, status). If no trigger found, returns (text, "affirmed").
    """
    lower = entity_text.lower().strip()
    for pattern, status in _NEGATION_TRIGGERS:
        match = re.match(pattern, lower)
        if match:
            # Strip the trigger from the entity text
            cleaned = entity_text[match.end():].strip()
            if cleaned and len(cleaned) >= 2:
                return cleaned, status
    # QC-188: post-entity negation ("breast cancer ruled out") — GLiNER
    # includes the trailing trigger in the span.
    post = _POST_ENTITY_NEGATION_RE.search(lower)
    if post:
        cleaned = entity_text[:post.start()].strip()
        if cleaned and len(cleaned) >= 2:
            return cleaned, "negated"
    return entity_text, "affirmed"


def _is_false_positive(text: str) -> bool:
    """Check if an entity text is a common non-medical word."""
    lower = text.lower().strip()
    if lower in _FALSE_POSITIVE_WORDS:
        return True
    # QC-160: 'Pt' (patient abbreviation) and staging fragments ('stage 2',
    # 'stage 3') leak into affirmed output and resolve to LOINC codes.
    if lower == "pt":
        return True
    if re.fullmatch(r"stage\s+\d+(?:[a-d])?", lower):
        return True
    # Filter single-character spans (bullet chars, punctuation, etc.)
    if len(lower) <= 1:
        return True
    return False


@dataclass
class FilteredSpan:
    """A medical term found in free text, after NLP filtering."""

    text: str
    entity_type: str
    status: str = "affirmed"
    section: str | None = None
    span_start: int = 0
    span_end: int = 0
    ner_confidence: float = 0.0
    # ConText MEASUREMENT/ADMINISTRATION arbitration result for this span
    # (see _context_arbitrate_categories): ["lab"], ["medication"],
    # ["lab", "medication"], or None (no cue decision — the GLiNER label
    # mapping stands). Captured at find_terms time because resolve_spans
    # runs without medspaCy loaded. Internal only — omitted from to_dict.
    context_categories: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "ner_label": self.entity_type,
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
    # Raw GLiNER label that detected the span (e.g., "medication", "disease",
    # "vital"). Useful for debugging; most callers want `result_type` instead.
    ner_label: str = ""
    # Populated when resolved via canonical mode (the default). Empty in legacy
    # modes (hybrid/lexical/semantic) where CanonicalSearchResult isn't used.
    canonical_id: str = ""
    # For combination-drug anchors (VAL-MED-RXNORM-MIN), lists the constituent
    # ingredient canonical_ids so callers can resolve all components. Empty for
    # non-combination anchors and legacy mode results.
    combination_members: list[dict[str, Any]] = field(default_factory=list)
    span_start: int = 0
    span_end: int = 0

    @property
    def system_label(self) -> str:
        # Delegates to core.normalize.source_label — single source of truth
        # shared with SearchResult.system_label. Previously had an inline
        # dict that could drift from search.py's _SOURCE_LABELS.
        return source_label(self.source)

    @property
    def result_type(self) -> str:
        """Resolved anchor type derived from canonical_id prefix.

        Single source of truth for "what kind of anchor did this resolve to?".
        A "statins" mention (ner_label="medication") resolves to a class
        anchor (result_type="drug_class"); a "Lipitor" mention resolves to
        an ingredient anchor (result_type="medication"). Differentiates
        within the same NLP label without parsing canonical_id.

        Empty when canonical_id is empty (legacy non-canonical modes).
        """
        if not self.canonical_id:
            return ""
        # Inline the prefix map to avoid circular import with search.py.
        # Keep in sync with search._RESULT_TYPE_TO_PREFIXES / _PREFIX_TO_RESULT_TYPE.
        for prefix, rt in (
            ("VAL-COND-", "condition"),
            ("VAL-SYMP-", "symptom"),
            ("VAL-LAB-", "lab"),
            ("VAL-VIT-", "vital"),
            ("VAL-MED-", "medication"),
            ("VAL-DRUGCLASS-", "drug_class"),
            ("VAL-PROC-", "procedure"),
            ("VAL-VAX-", "vaccine"),
        ):
            if self.canonical_id.startswith(prefix):
                return rt
        return ""

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
            "ner_label": self.ner_label,
            "result_type": self.result_type,
            "canonical_id": self.canonical_id,
            "combination_members": self.combination_members,
            "span_start": self.span_start,
            "span_end": self.span_end,
        }

    def to_coderef(self) -> CodeRef:
        return CodeRef(source=self.source, code=self.code)


class NlpPipeline:
    """Combines GLiNER for zero-shot NER with medspaCy for ConText annotations."""

    def __init__(
        self,
        *,
        ner_model: str = DEFAULT_NER_MODEL,
        ner_revision: str | None = DEFAULT_NER_MODEL_REVISION,
        labels: list[str] | None = None,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        self._ner_model_name = ner_model
        # Pinned HF revision so weight drift can't silently change recall
        # (drift observed 2026-08-14). None disables pinning (e.g. local
        # model paths, where huggingface_hub has no revision concept).
        self._ner_model_revision = ner_revision
        self._labels = labels or DEFAULT_LABELS
        self._threshold = threshold
        self._nlp = None
        self._ner_model = None
        # en_core_web_sm for dependency parsing (Signals 1-2 of the
        # three-signal lab-vs-med arbiter). Loaded lazily alongside the
        # rest of the pipeline; None when the model is not installed
        # (ConText-only arbitration still works).
        self._parser_nlp = None
        self._parser_loaded = False

    def _ensure_loaded(self):
        if self._nlp is not None:
            return

        # QC-178: PyRuSH's Cython module imports `from loguru import logger`
        # at MODULE level and emits per-token DEBUG lines containing the full
        # clinical note text (PHI) — ~1MB of log per 100K-char note. The
        # previous stdlib setLevel(WARNING) call was a no-op against loguru.
        # Remove loguru's default stderr sink so these lines never render.
        # (loguru.disable() on the package is the other option but sinks are
        # the supported removal path; if the host app configured its own
        # loguru sink it stays — we only remove the default one.)
        # If loguru isn't installed PyRuSH isn't either, so the import failing
        # means this pipeline can't load at all — let the ImportError propagate
        # (a missing dependency is a real error, per GLOBAL_RULES).
        from loguru import logger as _loguru_logger
        _loguru_logger.remove()

        # Load GLiNER. revision= pins the HF repo commit (see
        # DEFAULT_NER_MODEL_REVISION) so weight drift can't silently change
        # extraction recall; None (explicit constructor override or empty
        # env var) loads the repo head / local path unpinned.
        from gliner import GLiNER
        self._ner_model = GLiNER.from_pretrained(
            self._ner_model_name, revision=self._ner_model_revision,
        )

        # Load medspaCy for ConText (disable target_matcher — we use GLiNER
        # instead). medspacy_disable is the medspaCy 1.3.x kwarg; the plain
        # spaCy `disable=` kwarg is silently swallowed by **model_kwargs when
        # the base model is blank-English, so the matcher used to run anyway.
        import medspacy
        self._nlp = medspacy.load(medspacy_disable=["medspacy_target_matcher"])
        _register_context_arbiter(self._nlp)

        # Parser for the three-signal arbiter (Signals 1-2: head noun,
        # unit type). Separate en_core_web_sm model, not a component of
        # the medspaCy pipeline: adding a parser pipe to it would require
        # re-running the full pipeline per doc, and the medspaCy doc is
        # tokenized by PyRuSH-resolved components we don't want to
        # disturb. Both run on the same text; alignment is by char_span.
        self._ensure_parser_loaded()

        logger.info("NLP pipeline loaded (GLiNER %s + medspaCy ConText)", self._ner_model_name)

    def _ensure_parser_loaded(self):
        """Lazily load en_core_web_sm (disable NER — we only need the
        tagger+parser for noun chunks and dependency heads). Missing
        model degrades to ConText-only arbitration with a warning."""
        if self._parser_loaded:
            return
        self._parser_loaded = True
        try:
            import spacy
            self._parser_nlp = spacy.load("en_core_web_sm", disable=["ner"])
        except (ImportError, OSError) as exc:
            self._parser_nlp = None
            logger.warning(
                "en_core_web_sm unavailable (%s) — three-signal arbiter "
                "Signals 1-2 disabled, falling back to ConText-only. "
                "Install with: pip install en-core-web-sm", exc,
            )

    def process(self, text: str, labels: list[str] | None = None) -> list[FilteredSpan]:
        """Process text and return filtered entity spans using sentence-level NER attention.

        ``labels`` overrides the pipeline's default label set for this call
        only (GLiNER is zero-shot — labels are passed at query time, not baked
        into the model). Used by find_terms(ner_labels=...) so per-call
        overrides never mutate the shared pipeline (QC-154).
        """
        self._ensure_loaded()
        labels = labels if labels is not None else self._labels

        # Step 1: Run text through medspaCy sentencizer (PyRuSH)
        doc = self._nlp(text)

        # Parser pass (same text, separate model) for the three-signal
        # arbiter's Signals 1-2 (head noun, unit type). Aligned to spans
        # by character offsets below.
        parser_doc = self._parser_nlp(text) if self._parser_nlp is not None else None

        # Step 2: Execute GLiNER zero-shot NER per sentence/clause
        raw_entities = []
        for sent in doc.sents:
            sent_text = sent.text.strip()
            if not sent_text:
                continue
            sent_ents = self._ner_model.predict_entities(
                sent_text, labels, threshold=self._threshold
            )
            # Map sentence-relative character offsets back to doc character offsets
            sent_offset = sent.start_char
            for ent in sent_ents:
                raw_entities.append({
                    "start": sent_offset + ent["start"],
                    "end": sent_offset + ent["end"],
                    "label": ent["label"],
                    "score": ent["score"],
                    "text": ent["text"],
                })

        if not raw_entities:
            return []

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
            # ConText annotations from the overlapping spaCy entity: ConText
            # status (negation/uncertainty/...) plus the MEASUREMENT/
            # ADMINISTRATION arbitration result for lab-vs-med resolution.
            context_status = None
            context_categories = None
            for spacy_ent in doc.ents:
                if (spacy_ent.start_char <= start < spacy_ent.end_char or
                        start <= spacy_ent.start_char < end):
                    context_categories = _arbitrate_lab_vs_med(
                        parser_doc.char_span(start, end, alignment_mode="expand")
                        if parser_doc is not None else None,
                        parser_doc,
                        spacy_ent,
                    )
                    negated = getattr(spacy_ent._, "is_negated", False)
                    uncertain = getattr(spacy_ent._, "is_uncertain", False)
                    historical = getattr(spacy_ent._, "is_historical", False)
                    # QC-152: relatives' conditions are not the patient's —
                    # medspaCy ConText sets is_family for FAMILY_HISTORY
                    # items ("father had", "family history of"). Previously
                    # only negated/uncertain/historical were checked, so
                    # "Father had colon cancer" affirmed colon cancer for
                    # the patient. Excluded from the affirmed default
                    # (status "family" is not in allowed_statuses); callers
                    # who want it pass include_family=True.
                    family = getattr(spacy_ent._, "is_family", False)
                    # QC-187: conditional/hypothetical mentions ("if X were
                    # present, we would start Y") must not be affirmed.
                    hypothetical = getattr(spacy_ent._, "is_hypothetical", False)
                    if negated:
                        context_status = "negated"
                    elif family:
                        context_status = "family"
                    elif hypothetical:
                        context_status = "hypothetical"
                    elif uncertain:
                        context_status = "uncertain"
                    elif historical:
                        context_status = "historical"
                    break
            if inline_status != "affirmed":
                # An inline trigger found inside the span text wins over the
                # sentence-level ConText status.
                status = inline_status
                display_text = cleaned_text
            else:
                status = context_status or "affirmed"
                display_text = ent_text

            spans.append(FilteredSpan(
                text=display_text,
                entity_type=ent_type,
                status=status,
                span_start=start,
                span_end=end,
                ner_confidence=score,
                context_categories=context_categories,
            ))

        return spans


class ExtractionService:
    """Unified text extraction service."""

    def __init__(
        self,
        *,
        ner_model: str = DEFAULT_NER_MODEL,
        labels: list[str] | None = None,
        threshold: float = DEFAULT_THRESHOLD,
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
        ner_labels: list[str] | None = None,
        include_negated: bool = False,
        include_uncertain: bool = False,
        include_historical: bool = False,
        include_family: bool = False,
    ) -> list[FilteredSpan]:
        """Extract medical terms from free text.

        Returns FilteredSpan objects. No code resolution.
        Does NOT require SapBERT/BM25 indexes.

        Parameters
        ----------
        ner_labels : list[str] | None
            Override the default GLiNER labels (DEFAULT_LABELS). When set,
            GLiNER will only detect spans matching these labels. None uses
            the defaults (disease, medication, symptom, procedure, lab test, vital).

            Per-call override only — the shared pipeline (and its loaded
            models) are never mutated (QC-154: replacing self._nlp on the
            cached singleton leaked one client's labels into every later
            call; QC-175: each rebuild reloaded GLiNER+medspaCy, ~2.1s CPU
            and ~0.7GB transient RSS per flip).
        """
        spans = self._nlp.process(text, labels=ner_labels)

        # Filter by ConText status
        allowed_statuses = {"affirmed"}
        if include_negated:
            allowed_statuses.add("negated")
        if include_uncertain:
            allowed_statuses.add("uncertain")
        if include_historical:
            allowed_statuses.add("historical")
        # QC-152: family-history mentions default to excluded (a relative's
        # condition is not the patient's). Opt-in like the other statuses.
        if include_family:
            allowed_statuses.add("family")

        spans = [s for s in spans if s.status in allowed_statuses]

        # Don't deduplicate by text — return ALL spans (including repeats at
        # different positions). Dedup happens at the concept level in
        # resolve_spans (by canonical_id). For annotation mode, every mention
        # should appear at its own position.
        return sorted(spans, key=lambda s: s.span_start)

    def resolve_spans(
        self,
        spans: list[FilteredSpan],
        *,
        mode: str | None = None,
        result_types: str | list[str] | None = None,
        min_grade: str | None = None,
    ) -> list[ExtractedConcept]:
        """Resolve filtered spans to coded concepts via search.

        In canonical mode (the default), passes the NLP label's mapped
        result-type categories to SearchService.canonical(result_types=...)
        so the filter happens at the service level via canonical_id prefix
        matching. Broader matches (ancestor fallback) are filtered out by
        default — they're often clinically lossy. Callers who want them can
        pass min_grade='broader'.

        Parameters
        ----------
        result_types : str | list[str] | None
            Filter resolved concepts by anchor type. None uses the per-label
            default (e.g., medication label searches medication + drug_class).
            When set, overrides the per-label default.
        """
        from medterm4ds.services.search import (
            _result_types_to_prefixes,
            get_search_service,
        )

        search = get_search_service()
        search_mode = mode or self._search_mode
        default_threshold = "probable" if search_mode == "canonical" else self._min_grade
        grade_threshold = min_grade or default_threshold
        # QC-153: validate result_types EAGERLY (raises ValueError for bogus
        # values, which the FHIR layer maps to a 400 per the service-
        # delegation pattern) and reduce to canonical_id prefixes for the
        # post-filtering below.
        result_type_prefixes = _result_types_to_prefixes(result_types)
        # QC-161: garbage min_grade was silently treated as the strictest
        # grade (0) because _GRADE_ORDER.get(grade_threshold, 0) defaults
        # unknown keys to 0. Validate at the service boundary so wire surfaces
        # that forward it unconstrained (MCP) get a clean ValueError.
        if grade_threshold not in _GRADE_ORDER:
            valid = ", ".join(sorted(_GRADE_ORDER))
            raise ValueError(
                f"Unknown min_grade: {grade_threshold!r}. Valid: {valid}."
            )

        # --- Batch path for canonical mode ---
        # Embed ALL search texts in one SapBERT forward pass, then batch-search
        # FAISS. Turns N×100ms into ~100ms total for the embedding step.
        # Post-filters (source, grade) applied per-span after batch search.
        if search_mode == "canonical" and len(spans) > 1:
            # Prepare search texts (strip trailing values)
            search_texts = [_strip_trailing_values(s.text) for s in spans]

            # Batch search — returns list of result-lists, one per query
            batch_results = search.canonical_batch(search_texts, count=10)

            concepts: list[ExtractedConcept] = []
            for span, results in zip(spans, batch_results):
                # Apply per-span source filter (post-filter the batch results)
                ss = _LABEL_TO_SOURCES.get(span.entity_type.lower())
                source_set = None
                if ss:
                    source_set = set()
                    for s in ss:
                        su = s.upper()
                        source_set.add(su)
                        if su == "LNC":
                            source_set.add("LOINC")
                        elif su == "LOINC":
                            source_set.add("LNC")
                        elif su == "RXNORM":
                            source_set.add("ATC")
                        elif su == "ATC":
                            source_set.add("RXNORM")

                resolved = False
                # Constrain-then-fallback (QC-182 follow-up): try the label's
                # categories first; if no result clears the floor, retry
                # unfiltered so the constraint never reduces recall. An
                # explicit caller result_types stays a hard filter (QC-153)
                # and disables the label constraint entirely.
                label_prefixes = (
                    None if result_type_prefixes
                    else _categories_to_prefixes(_span_search_categories(span))
                )
                prefix_attempts: list[set[str] | None] = (
                    [label_prefixes, None] if label_prefixes else [None]
                )
                for label_pf in prefix_attempts:
                    for r in results:
                        # Source filter
                        if source_set and r.anchor_system not in source_set:
                            continue
                        # Result-type filter (QC-153: previously the batch path
                        # ignored result_types entirely — FHIR resultTypes=condition
                        # still returned medications)
                        if result_type_prefixes and not any(
                            r.canonical_id.startswith(p) for p in result_type_prefixes
                        ):
                            continue
                        # Label-derived category constraint (constrain pass;
                        # the fallback pass re-runs with label_pf=None)
                        if label_pf and not any(
                            r.canonical_id.startswith(p) for p in label_pf
                        ):
                            continue
                        # Grade filter
                        if _GRADE_ORDER.get(r.match_grade, 2) > _GRADE_ORDER.get(grade_threshold, 0):
                            continue
                        canonical_id = getattr(r, "canonical_id", "") or ""
                        combination_members = getattr(r, "combination_members", None) or []
                        concepts.append(ExtractedConcept(
                            code=r.code,
                            source=r.source,
                            display=r.display,
                            matched_text=span.text,
                            status=span.status,
                            section=span.section,
                            confidence=r.score,
                            match_grade=r.match_grade,
                            ner_label=span.entity_type,
                            span_start=span.span_start,
                            span_end=span.span_end,
                            canonical_id=canonical_id,
                            combination_members=combination_members,
                        ))
                        resolved = True
                        break
                    if resolved:
                        break

                # Conjunction split on failure (individual search for split parts)
                if not resolved:
                    search_text = _strip_trailing_values(span.text)
                    parts = _split_on_conjunction(search_text)
                    if parts:
                        # Same constrain-then-fallback per split part.
                        rt_attempts: list[str | list[str] | None]
                        if result_types is not None:
                            rt_attempts = [result_types]
                        else:
                            rt = _span_search_categories(span)
                            rt_attempts = [rt, None] if rt is not None else [None]
                        for part in parts:
                            part_resolved = False
                            for part_rt in rt_attempts:
                                part_results = search.canonical(
                                    part, result_types=part_rt, sources=ss, count=5,
                                )
                                for r in part_results:
                                    if _GRADE_ORDER.get(r.match_grade, 2) > _GRADE_ORDER.get(grade_threshold, 0):
                                        continue
                                    canonical_id = getattr(r, "canonical_id", "") or ""
                                    combination_members = getattr(r, "combination_members", None) or []
                                    concepts.append(ExtractedConcept(
                                        code=r.code, source=r.source, display=r.display,
                                        matched_text=part, status=span.status,
                                        section=span.section, confidence=r.score,
                                        match_grade=r.match_grade, ner_label=span.entity_type,
                                        span_start=span.span_start, span_end=span.span_end,
                                        canonical_id=canonical_id,
                                        combination_members=combination_members,
                                    ))
                                    part_resolved = True
                                    break
                                if part_resolved:
                                    break

            # Deduplicate by canonical_id (preferred) or source:code (legacy).
            # QC-183: the key includes status — without it a negated mention
            # could REPLACE the affirmed mention of the same condition (the
            # patient's active condition silently disappearing from output
            # when a "no evidence of X" sentence scores higher).
            seen: dict[tuple[str, str], ExtractedConcept] = {}
            for c in concepts:
                key = (c.canonical_id or f"{c.source}:{c.code}", c.status)
                if key not in seen or c.confidence > seen[key].confidence:
                    seen[key] = c
            return sorted(seen.values(), key=lambda c: c.confidence, reverse=True)

        # --- Legacy path: per-span search (single span or non-canonical mode) ---
        # QC-153: result_types must filter here too. Canonical mode forwards
        # to search.canonical (prefix filter). Legacy modes use BM25 category
        # (SEARCH_CATEGORIES vocabulary — a subset of result_type values);
        # categories that don't exist in the BM25 index can never match, so
        # filtering on the intersection preserves caller intent.
        from medterm4ds.services.search import SEARCH_CATEGORIES

        requested_types = (
            {result_types.lower().strip()}
            if isinstance(result_types, str)
            else {t.lower().strip() for t in result_types}
            if result_types
            else None
        )
        concepts = []
        # QC-172: legacy modes ran one full search PER SPAN with zero dedup —
        # a 10K-char note with 310 spans / 26 distinct texts did 11.9x
        # redundant SapBERT+BM25 work (108.6s where ~12s needed). Cache search
        # results by (search_text, sources-key) so repeated mentions of the
        # same entity text resolve once. Canonical multi-span path already
        # batches; this covers the per-span legacy/single-span route.
        # Cache key includes rt: two spans with the same text but different
        # labels (hence different label-derived categories) must not share
        # entries once the values are no longer uniformly None.
        _search_cache: dict[tuple[str, str, str], list[Any]] = {}

        def _search_span_texts(text: str, ss_key: str, ss, rt) -> list[Any]:
            rt_key = rt if isinstance(rt, str) or rt is None else ",".join(rt)
            cache_key = (text, ss_key, rt_key)
            if cache_key in _search_cache:
                return _search_cache[cache_key]
            if search_mode == "canonical":
                r = search.canonical(text, result_types=rt, sources=ss, count=5)
            else:
                r = search.search(text, mode=search_mode, sources=ss, count=5)
                if requested_types is not None:
                    cat_filter = requested_types.intersection(SEARCH_CATEGORIES)
                    if cat_filter:
                        r = [x for x in r if x.category in cat_filter]
            _search_cache[cache_key] = r
            return r

        for span in spans:
            search_text = _strip_trailing_values(span.text)
            ss = _LABEL_TO_SOURCES.get(span.entity_type.lower())
            ss_key = ",".join(ss) if ss else ""
            # Constrain-then-fallback (QC-182 follow-up): label-derived
            # categories first, then an unfiltered retry. Explicit caller
            # result_types is the only attempt (hard filter, QC-153).
            rt_attempts: list[str | list[str] | None]
            if result_types is not None:
                rt_attempts = [result_types]
            else:
                rt = _span_search_categories(span)
                rt_attempts = [rt, None] if rt is not None else [None]

            resolved = False
            for rt in rt_attempts:
                results = _search_span_texts(search_text, ss_key, ss, rt)
                for r in results:
                    if _GRADE_ORDER.get(r.match_grade, 2) > _GRADE_ORDER.get(grade_threshold, 0):
                        continue
                    canonical_id = getattr(r, "canonical_id", "") or ""
                    combination_members = getattr(r, "combination_members", None) or []
                    concepts.append(ExtractedConcept(
                        code=r.code, source=r.source, display=r.display,
                        matched_text=span.text, status=span.status,
                        section=span.section, confidence=r.score,
                        match_grade=r.match_grade, ner_label=span.entity_type,
                        span_start=span.span_start, span_end=span.span_end,
                        canonical_id=canonical_id,
                        combination_members=combination_members,
                    ))
                    resolved = True
                    break
                if resolved:
                    break

            if not resolved:
                parts = _split_on_conjunction(search_text)
                if parts:
                    for part in parts:
                        part_resolved = False
                        for part_rt in rt_attempts:
                            part_results = _search_span_texts(part, ss_key, ss, part_rt)
                            for r in part_results:
                                if _GRADE_ORDER.get(r.match_grade, 2) > _GRADE_ORDER.get(grade_threshold, 0):
                                    continue
                                canonical_id = getattr(r, "canonical_id", "") or ""
                                combination_members = getattr(r, "combination_members", None) or []
                                concepts.append(ExtractedConcept(
                                    code=r.code, source=r.source, display=r.display,
                                    matched_text=part, status=span.status,
                                    section=span.section, confidence=r.score,
                                    match_grade=r.match_grade, ner_label=span.entity_type,
                                    span_start=span.span_start, span_end=span.span_end,
                                    canonical_id=canonical_id,
                                    combination_members=combination_members,
                                ))
                                part_resolved = True
                                break
                            if part_resolved:
                                break

        seen: dict[tuple[str, str], ExtractedConcept] = {}
        for c in concepts:
            # QC-183: include status in the dedup key (see batch path above).
            key = (c.canonical_id or f"{c.source}:{c.code}", c.status)
            if key not in seen or c.confidence > seen[key].confidence:
                seen[key] = c
        return sorted(seen.values(), key=lambda c: c.confidence, reverse=True)

    def extract(
        self,
        text: str,
        *,
        format: str = "codes",
        ner_labels: list[str] | None = None,
        result_types: str | list[str] | None = None,
        mode: str | None = None,
        min_grade: str | None = None,
        include_negated: bool = False,
        include_uncertain: bool = False,
        include_historical: bool = False,
        include_family: bool = False,
    ) -> list[FilteredSpan] | list[ExtractedConcept] | dict[str, Any]:
        """Extract medical concepts from free text.

        Parameters
        ----------
        format : str
            - ``"codes"`` (default): resolve to canonical anchors.
              Returns ``list[ExtractedConcept]``.
            - ``"terms"``: NLP only, no code resolution.
              Returns ``list[FilteredSpan]``.
            - ``"annotated"``: resolve to codes AND return inline entity
              annotations with span metadata. Returns a dict with ``concepts``,
              ``annotated_text``, and ``spans``. Includes ALL spans regardless
              of ConText status (negated, uncertain, historical) for maximum
              flexibility — callers filter by ``status`` as needed.
        """
        if format == "annotated":
            return self._extract_annotated(
                text,
                ner_labels=ner_labels,
                result_types=result_types,
                mode=mode,
                min_grade=min_grade,
            )

        spans = self.find_terms(
            text,
            ner_labels=ner_labels,
            include_negated=include_negated,
            include_uncertain=include_uncertain,
            include_historical=include_historical,
            include_family=include_family,
        )

        if format == "terms":
            return spans

        return self.resolve_spans(
            spans, mode=mode, result_types=result_types, min_grade=min_grade
        )

    def _extract_annotated(
        self,
        text: str,
        *,
        ner_labels: list[str] | None = None,
        result_types: str | list[str] | None = None,
        mode: str | None = None,
        min_grade: str | None = None,
    ) -> dict[str, Any]:
        """Extract concepts + return inline entity annotations.

        Returns a dict with:
        - ``concepts``: list[ExtractedConcept] (resolved codes)
        - ``annotated_text``: str with inline ``[entity|label]`` markers
        - ``spans``: list of span metadata dicts

        All spans are included regardless of ConText status (negated,
        uncertain, historical). Each span's ``status`` field lets callers
        filter downstream.
        """        # Get ALL spans (don't filter by status — include everything)
        all_spans = self.find_terms(
            text,
            ner_labels=ner_labels,
            include_negated=True,
            include_uncertain=True,
            include_historical=True,
            include_family=True,
        )

        # Resolve ALL spans (including negated/uncertain/historical). With batch
        # embedding the cost is minimal. Status is preserved on each concept so
        # callers can filter. This fixes ALT/AST failing resolution when ConText
        # marks them as "uncertain" in conditional sentences ("if ALT is elevated...").
        concepts = self.resolve_spans(
            all_spans, mode=mode, result_types=result_types, min_grade=min_grade,
        )

        # Build a lookup from matched_text → concept for span metadata enrichment
        concept_by_text: dict[str, ExtractedConcept] = {}
        for c in concepts:
            concept_by_text[c.matched_text.lower()] = c

        # Select non-overlapping spans (greedy longest-first)
        sorted_by_len = sorted(all_spans, key=lambda s: s.span_end - s.span_start, reverse=True)
        selected: list[FilteredSpan] = []
        occupied: list[tuple[int, int]] = []
        for span in sorted_by_len:
            if not any(not (span.span_end <= start or span.span_start >= end)
                       for start, end in occupied):
                selected.append(span)
                occupied.append((span.span_start, span.span_end))
        selected.sort(key=lambda s: s.span_start)

        # Build annotated text with inline [entity|label] markers.
        # When a span resolves, use the label derived from result_type (corrected
        # label) instead of the raw NER label — GLiNER sometimes mislabels
        # (e.g., "serum creatinine" tagged as "vital sign" should show "lab test").
        # When unresolved, fall back to the NER label.
        result_parts: list[str] = []
        last_pos = 0
        span_metadata: list[dict[str, Any]] = []
        for span in selected:
            # Text before the entity
            result_parts.append(text[last_pos:span.span_start])
            entity_text = text[span.span_start:span.span_end]

            # Look up resolved concept (may also match conjunction-split parts)
            concept = (concept_by_text.get(span.text.lower())
                       or concept_by_text.get(_strip_trailing_values(span.text).lower()))

            # Determine the annotation label: prefer result_type (corrected),
            # fall back to ner_label if unresolved.
            if concept and concept.result_type:
                label = _RESULT_TYPE_TO_LABEL.get(concept.result_type, span.entity_type)
            else:
                label = span.entity_type

            result_parts.append(f"[{entity_text}|{label}]")
            last_pos = span.span_end

            span_metadata.append({
                "text": entity_text,
                "label": label,                  # corrected label (from result_type)
                "ner_label": span.entity_type,   # original GLiNER label (for debugging)
                "start": span.span_start,
                "end": span.span_end,
                "ner_score": span.ner_confidence,
                "status": span.status,
                "canonical_id": concept.canonical_id if concept else "",
                "result_type": concept.result_type if concept else "",
                "display": concept.display if concept else "",
            })

        # Trailing text after last entity
        result_parts.append(text[last_pos:])

        return {
            "concepts": concepts,
            "annotated_text": "".join(result_parts),
            "spans": span_metadata,
        }


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
