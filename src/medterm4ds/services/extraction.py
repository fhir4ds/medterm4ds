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

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field, replace
from typing import Any

from medterm4ds.core.env import env_int
from medterm4ds.core.models import CodeRef
from medterm4ds.core.normalize import expand_source_aliases, source_label
from medterm4ds.services.extraction_cues import (
    ASSERTION_FRAGMENT_RE as _ASSERTION_FRAGMENT_RE,
)
from medterm4ds.services.extraction_cues import (
    CONCENTRATION_UNIT_RE as _CONCENTRATION_UNIT_RE,
)
from medterm4ds.services.extraction_cues import (
    DOSE_UNIT_RE as _DOSE_UNIT_RE,
)
from medterm4ds.services.extraction_cues import (
    FUSED_VALUE_TOKEN_RE as _FUSED_VALUE_TOKEN_RE,
)
from medterm4ds.services.extraction_cues import (
    Mt4dsCueAnnotator,
    absorb_sep_gap,
    compose_gap,
    list_gap,
    slash_gap,
    stitch_gap,
)
from medterm4ds.services.extraction_cues import (
    is_false_positive as _is_false_positive,
)

# Legacy mention-text fallbacks (design F6/F8): pipeline spans carry
# search_variants/conjunction_parts precomputed at NLP time from the doc's
# token slice; these pure-text helpers remain only for manually constructed
# spans (public resolve_spans API + tests). Relocated to extraction_cues.
from medterm4ds.services.extraction_cues import (
    mention_leading_qualifier_fallback as _leading_qualifier_fallback,
)
from medterm4ds.services.extraction_cues import (
    mention_split_on_conjunction as _split_on_conjunction,
)
from medterm4ds.services.extraction_cues import (
    mention_strip_trailing_values as _strip_trailing_values,
)
from medterm4ds.services.extraction_cues import (
    mention_trailing_form_fallback as _trailing_form_fallback,
)

logger = logging.getLogger(__name__)

DEFAULT_NER_MODEL = os.getenv("MEDTERM4DS_NER_MODEL", "knowledgator/gliner-bi-small-v2.0")
# Pinned revision (commit SHA) of DEFAULT_NER_MODEL so Hugging Face weight
# drift can't silently change extraction recall — drift observed 2026-08-14.
# Override (or disable with an empty value) via MEDTERM4DS_NER_MODEL_REVISION.
DEFAULT_NER_MODEL_REVISION = os.getenv("MEDTERM4DS_NER_MODEL_REVISION", "3d74c1bf459b8b1c0be1ecbddd679416ce005418") or None


def _calibration_id(labels: list[str], threshold: float) -> str:
    """Hash of the NER calibration (labels + threshold).

    The artifact-governance contract (docs/plans/artifact-governance-plan.md
    §3): the commit pin alone hides calibration drift — the labels/threshold
    pair is what the 185-entity golden set was calibrated against. The id
    ships in the extraction_ner manifest and is checked against the
    runtime's ACCEPTED_NER_CALIBRATIONS registry.
    """
    import hashlib

    payload = json.dumps(
        {"labels": list(labels), "threshold": threshold},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"cal_{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


# Calibration id of the shipped defaults (the calibrated pair). A loaded
# pipeline whose calibration differs from an accepted id must be explicitly
# overridden — and the override logs both ids on every load.
# Defined after DEFAULT_LABELS/DEFAULT_THRESHOLD below (module order).
DEFAULT_NER_CALIBRATION_ID: str = ""
# Canonical mode is the default — it searches canonical anchor names directly
# via the FAISS concept index, with a 0.70 confidence floor that filters weak
# matches. Hybrid/lexical modes still available for callers who want raw UMLS.
DEFAULT_SEARCH_MODE = os.getenv("MEDTERM4DS_EXTRACTION_MODE", "canonical")
DEFAULT_MIN_GRADE = os.getenv("MEDTERM4DS_EXTRACTION_MIN_GRADE", "certain")


def _env_min_grade_override() -> str | None:
    """MEDTERM4DS_EXTRACTION_MIN_GRADE when the operator set it.

    EV05-EXPLORER-1: read at CALL time (not import time) so the canonical
    default-floor resolution below honors operator overrides without a
    module reload, and stays testable.
    """
    return os.getenv("MEDTERM4DS_EXTRACTION_MIN_GRADE")

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

# Bind the default calibration id now that the calibrated pair is defined.
DEFAULT_NER_CALIBRATION_ID = _calibration_id(DEFAULT_LABELS, DEFAULT_THRESHOLD)

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
    # 2026-11-11: the phlebotomy phrase for analyte draws — "blood draw"
    # is the standard surface ("blood draw for lithium"); bare "draw"
    # stays out (verb senses: "draw blood", "draw up").
    "blood draw", "redraw",
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
    # "was/is N" — bare numeric result context ("Creatinine was 2.1",
    # "potassium is 5.2"). These sentences carry no explicit measurement cue
    # word or unit, so Signals 1-2 miss them; the copula+number pattern is
    # the remaining disambiguator (10 no-signal errors in the v2 corpus).
    # CR-052: "at" is deliberately excluded — it fired MEASUREMENT
    # sentence-wide on clock times and whole-number doses ("given at 8",
    # "was 500 mg"), degrading clean medication decisions to [lab,
    # medication] on parser-less installs. "was/is" keep both integer and
    # decimal results ("platelets was 150" stays a lab signal).
    rules += [ConTextRule(literal="copula-result", category="MEASUREMENT",
                          pattern=r"\b(?:was|is)\s+\d+(?:\.\d+)?\b")]
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

# Signal 2: number+unit immediately after the span — unit validators are
# mention-text regexes relocated to extraction_cues (AMUX-102 design F9).


def _head_noun_signal(span, parser_doc, parser_chunks=None) -> list[str] | None:
    """Signal 1: the head noun of the noun chunk containing the span.

    - chunk root lemma in a lexicon → that side decides
    - root uninformative (e.g. "phenytoin level subtherapeutic" roots at
      the mistagged adjective "subtherapeutic") → fall back to the
      rightmost noun/propn token in the chunk whose lemma is in a lexicon
    - span in no chunk → fall back to the syntactic head of the span's
      last token (fixes e.g. "metformin ... held" style attachments where
      the chunker fails); only noun/propn heads decide (a verb head like
      the misparsed "trough" in "Gentamicin trough 2.1" stays undecided)

    ``parser_chunks`` is a materialized ``list(parser_doc.noun_chunks)``
    computed once per doc by the caller. ``doc.noun_chunks`` is a property
    that regenerates the syntax iterator on EVERY access — iterating it
    per span made long docs O(spans x chunks) (measured 50% of batch
    runtime on drug-label workloads). None falls back to the property for
    single-call callers.
    """
    chunks = parser_chunks if parser_chunks is not None else parser_doc.noun_chunks
    for chunk in chunks:
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


def _arbitrate_lab_vs_med(span, parser_doc, context_ent, parser_chunks=None) -> list[str] | None:
    """Three-signal lab-vs-med disambiguation, in priority order.

    Signal 1: head noun of the noun phrase (100% precision in eval)
    Signal 2: unit type on the number after the span (100% precision)
    Signal 3: ConText MEASUREMENT/ADMINISTRATION cues (existing arbiter)

    No signal fired → None (GLiNER's label mapping stands). Higher-priority
    signals win by construction — later signals are only consulted on None.

    ``parser_chunks``: precomputed list(parser_doc.noun_chunks) shared
    across every span of the doc (see _head_noun_signal).
    """
    if parser_doc is not None and span is not None:
        result = _head_noun_signal(span, parser_doc, parser_chunks)
        if result is not None:
            return result
        result = _unit_type_signal(span, parser_doc)
        if result is not None:
            return result
    return _context_arbitrate_categories(context_ent)


def _span_search_categories(span: FilteredSpan) -> str | list[str] | None:
    """Effective canonical search categories for a span (constrain step).

    ConText arbitration first — but ONLY for spans GLiNER typed as a
    contested lab-vs-med label (CR-050). Without the gate, the arbiter
    overrides every label: "diabetes screening" fires MEASUREMENT on a
    disorder span and forces it to lab, "pain increased" fires
    ADMINISTRATION on a symptom span and forces it to medication. The
    arbiter exists to resolve lab-vs-med ambiguity, not to re-type
    disorders and symptoms.

    Contested labels: any label EXCEPT disorders, symptoms, procedures,
    and body structures — those are clearly not lab or medication and
    should never be re-typed by ConText.
    """
    _NON_LAB_MED_LABELS = frozenset({
        "disorder", "disease", "symptom", "sign", "procedure",
        "medical intervention", "body structure",
    })
    label_lower = span.entity_type.lower()
    if span.context_categories and label_lower not in _NON_LAB_MED_LABELS:
        return span.context_categories
    return _LABEL_TO_SEARCH_CATEGORIES.get(label_lower)


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

# Regex to strip trailing numeric values and units from span text before
# search — token-based since AMUX-102 stage 3 (design F6):
# Mt4dsCueAnnotator.strip_trailing_values / search_variant_ladder compute
# the fallback query ladder from the entity's token slice at NLP time
# (FilteredSpan.search_variants). The legacy mention-text regexes remain
# in extraction_cues as the fallback for manually constructed spans.
#
# Leading-qualifier strip (CR-301) and the trailing form-noun fallback
# (2026-11-11 addendum B) follow the same pattern; the conjunction split
# (design F8) is Mt4dsCueAnnotator.conjunction_split with the result
# precomputed onto FilteredSpan.conjunction_parts.


def _span_fallback_queries(span: FilteredSpan) -> list[str]:
    """Ordered fallback query variants after the full-mention query fails:
    value-stripped, form-noun-stripped, leading-qualifier-stripped (each
    form/qualifier strip applied on the value-stripped base — the order
    every resolve site uses today).

    Pipeline spans carry the ladder precomputed at NLP time
    (span.search_variants, token-based); manually constructed spans
    (fields None) fall back to the legacy mention-text helpers.
    """
    if span.search_variants is not None:
        return list(span.search_variants)
    out: list[str] = []
    stripped = _strip_trailing_values(span.text)
    for variant in (
        stripped,
        _trailing_form_fallback(span.text),
        _leading_qualifier_fallback(span.text),
    ):
        if variant and variant != span.text and variant not in out:
            out.append(variant)
    return out

# Common non-medical-word stoplist + false-positive validators are
# spaCy-era mention-text helpers in extraction_cues (AMUX-102 stage 2,
# design F7): is_false_positive is re-exported above for the hot path.

# Negation/uncertainty/historical INLINE triggers, post-entity negation
# tails, and the stitching/compose gap classifiers are token-based since
# AMUX-102 stage 2: see extraction_cues.Mt4dsCueAnnotator.inline_trigger
# (design F3) and the extraction_cues gap helpers (design F5).


# Drug-ish GLiNER labels whose fragments may be stitched (2026-11-07 NER
# battery, model team finding 3): GLiNER fragments compound drug mentions
# ('lisinopril/HCTZ' → 'lisinopril' + 'HCTZ'), and resolving the FULL
# mention is what returns the correct combination anchor (measured:
# lisinopril/HCTZ → 'hydrochlorothiazide / lisinopril' 0.917 rank 1).
# STITCH ONLY THESE — blind stitching merged 'Norco' with 'q6h'.
_DRUG_NER_LABELS = frozenset({
    "therapeutic agent", "therapeutic class", "medication", "drug",
})
# Battery finding #5: 'immunization' is GLiNER's dominant mislabel for
# oral-drug fragments ('lisinopril', 'HCTZ'). These may join a stitch
# ONLY across a bare '/' — 'flu and covid vaccine' must never merge.
_MISLABELED_DRUG_LABELS = frozenset({"immunization"})
# Gap classifiers (slash/stitch/list/compose) are token-based since
# AMUX-102 stage 2 — see extraction_cues.slash_gap/stitch_gap/list_gap/
# compose_gap (design F5): a bare '/' (slash gap) is the only separator
# that may join a mislabeled fragment; ',' and 'and' are LIST separators
# and are gated separately (see _stitch_gated_list_pairs).


def _strength_components(strength: str | None) -> int | None:
    """Number of dose components in a strength expression.

    '20/12.5 mg' -> 2, '5-500' -> 2, '500 mg' -> 1, '250/50/100' -> 3.
    None when unknown (no strength captured).
    """
    if not strength:
        return None
    nums = re.findall(r"\d+(?:\.\d+)?", strength)
    return len(nums) if nums else None




def _complete_strength(strength: str | None, text: str, at: int) -> str | None:
    """Extend a captured strength through slash/dash continuations.

    GLiNER splits '325/5' across spans ('325' absorbed, '/5' lost) — the
    raw text right after the captured strength completes the ratio.
    """
    if not strength:
        return strength
    m = re.match(
        r"\s*[./-]\s*\d+(?:\.\d+)?(?:\s*(?:mg|mcg|ml|%)?)?",
        text[at:at + 14],
        re.IGNORECASE,
    )
    if m:
        return f"{strength}{m.group(0).strip()}"
    return strength



def _is_value_like_span(span) -> bool:
    """A neutral fragment span usable as dose/strength context."""
    return (
        span.status == "affirmed"
        and _is_neutral_fragment(span.text)
        and bool(re.search(r"\d", span.text))
    )


def _absorb_trailing_strength(spans: list, text: str, doc) -> list:
    """Capture the dose/strength following a drug mention as metadata.

    'lisinopril/HCTZ' + '20' + '12.5 mg' (three GLiNER spans) -> one span
    with strength '20/12.5 mg'; the value spans stop resolving to junk.
    Only value-like neutral fragments DIRECTLY adjacent (whitespace/sep
    gap <= 4 chars) after a drug-ish span are absorbed — a dose that
    belongs to the next mention in a list is left alone ('aspirin 81,
    metformin 500': each drug keeps its own).
    """
    if not spans:
        return spans
    out: list = []
    i = 0
    drug_labels = _DRUG_NER_LABELS | _MISLABELED_DRUG_LABELS
    while i < len(spans):
        span = spans[i]
        if span.entity_type not in drug_labels:
            out.append(span)
            i += 1
            continue
        # in-span trailing strength ('metformin 500mg' as ONE span)
        if span.strength is None:
            m = re.search(
                r"\s+(?:"
                r"\d+(?:\s*[./-]\s*\d+)+\s*[a-z/%]*"
                r"|\d+(?:\.\d+)?\s*(?:mg|mcg|ug|g|ml|%)"
                r"|\d+(?:\.\d+)?(?=\s*$))",
                span.text,
                re.IGNORECASE,
            )
            if m and re.search(r"[a-z]", span.text[:m.start()], re.IGNORECASE):
                span.strength = m.group(0).strip()
        # absorb directly-following value-like spans
        end = span.span_end
        j = i + 1
        while j < len(spans):
            nxt = spans[j]
            if (
                _is_value_like_span(nxt)
                and 0 < nxt.span_start - end <= 4
                and absorb_sep_gap(doc, end, nxt.span_start)
            ):
                end = nxt.span_end
                j += 1
            else:
                break
        if j > i + 1:
            span.strength = text[span.span_end:end].strip()
            span.span_end = end
            span.strength = _complete_strength(span.strength, text, end)
            i = j
            out.append(span)
            continue
        # Raw-text fallback: GLiNER often emits NO value spans at all
        # ('lisinopril and HCTZ 20/12.5 mg' — only the two drug spans).
        # A value expression directly after the span (no intervening
        # words) is still this mention's strength — capture it without
        # moving span_end so a later real span is unaffected.
        if span.strength is None:
            m = re.match(
                r"\s{1,3}(\d+(?:\s*[./-]\s*\d+)+\s*(?:mg|mcg|ml|%)?"
                r"|\d+(?:\.\d+)?\s?(?:mg|mcg))(?=\W|$)",
                text[span.span_end:span.span_end + 24],
                re.IGNORECASE,
            )
            if m:
                span.strength = m.group(1).strip()
                span.strength = _complete_strength(
                    span.strength, text, span.span_end + m.end()
                )
        out.append(span)
        i += 1
    return out


def _stitch_gated_list_pairs(spans: list, text: str, doc) -> list:
    """Corroboration-gated join across ',' / 'and' (2026-11-09 design).

    A list separator may join two drug fragments ONLY when the merged
    mention carries a multi-component strength whose component count
    equals 2 (the fragment count). Anchor existence is enforced at
    resolve time — if no combination anchor covers every fragment, the
    composed attempt fails and BOTH siblings are emitted, which is the
    correct list behavior. No strength corroboration -> never joins.
    """
    if len(spans) < 2:
        return spans
    drug_labels = _DRUG_NER_LABELS | _MISLABELED_DRUG_LABELS
    out: list = []
    for span in spans:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.entity_type in drug_labels
            and span.entity_type in drug_labels
            and prev.status == "affirmed"
            and span.status == "affirmed"
            and prev.section == span.section
            and 0 < span.span_start - prev.span_end <= 8
            and list_gap(doc, prev.span_end, span.span_start)
            and _strength_components(span.strength) == 2
            and prev.strength is None
        ):
            prev.composed_from = [
                *(prev.composed_from or [prev.text]),
                span.text,
            ]
            prev.text = f"{prev.text}/{span.text}"
            prev.span_end = span.span_end
            prev.strength = span.strength
            prev.composed_strict = True
            prev.ner_confidence = max(prev.ner_confidence, span.ner_confidence)
            # NLP-time precomputed fields are stale for the merged text —
            # drop them so resolve falls back to the mention-text helpers.
            prev.search_variants = None
            prev.conjunction_parts = None
            continue
        out.append(span)
    return out


def _stitch_drug_fragments(spans: list, text: str, doc) -> list:
    """Merge adjacent drug-label fragments separated only by separators.

    'lisinopril' + '/' + 'HCTZ' (two GLiNER spans) becomes one span
    'lisinopril/HCTZ' — the combination-mention text the resolver needs
    to find the combination anchor. Non-drug neighbours, statuses other
    than affirmed, and gaps containing any real token are never stitched.
    """
    if len(spans) < 2:
        return spans
    out: list = []
    for span in spans:
        prev = out[-1] if out else None
        if prev is None:
            out.append(span)
            continue
        prev_drug = prev.entity_type in _DRUG_NER_LABELS
        span_drug = span.entity_type in _DRUG_NER_LABELS
        prev_mis = prev.entity_type in _MISLABELED_DRUG_LABELS
        span_mis = span.entity_type in _MISLABELED_DRUG_LABELS
        eligible = (
            (prev_drug and span_drug)
            # a mislabeled fragment may only join through a bare '/'
            or (prev_drug and span_mis and slash_gap(doc, prev.span_end, span.span_start))
            or (prev_mis and span_drug and slash_gap(doc, prev.span_end, span.span_start))
            or (prev_mis and span_mis and slash_gap(doc, prev.span_end, span.span_start))
        )
        if (
            eligible
            and prev.status == "affirmed"
            and span.status == "affirmed"
            and prev.section == span.section
            and 0 < span.span_start - prev.span_end <= 8
            and stitch_gap(doc, prev.span_end, span.span_start)
        ):
            # CR-302 (focused review, now measured live): a stitched span
            # MUST carry composed_from — otherwise the resolver treats it
            # as an ordinary span (no fragment-completeness, unfiltered
            # fallback allowed), and on hybrid's stricter grade floor the
            # combination fails, the head fragment resolves alone, and the
            # sibling ('HCTZ') is silently dropped ('lisinopril/HCTZ' ->
            # 29046 only, live on the demo Space).
            prev.composed_from = [
                *(prev.composed_from or [prev.text]),
                span.text,
            ]
            prev.text = f"{prev.text}/{span.text}"
            prev.span_end = span.span_end
            prev.strength = prev.strength or span.strength
            prev.ner_confidence = max(prev.ner_confidence, span.ner_confidence)
            # NLP-time precomputed fields are stale for the merged text —
            # drop them so resolve falls back to the mention-text helpers.
            prev.search_variants = None
            prev.conjunction_parts = None
            continue
        out.append(span)
    return out


# Compound-span composition (2026-11-07 NER battery, model team findings 1/3).
# GLiNER fragments compound mentions ('CBC' + 'with differential',
# 'lisinopril' + 'HCTZ'); only the COMPOSED phrase carries the qualifier into
# search, and search on the head noun alone is what produced the inverted
# 'CBC' -> 'Hemogram ... WITHOUT Differential' answer.
#
# Linkers only: '/', '-', whitespace, parentheses, with/without/w-of/of
# — classified by extraction_cues.compose_gap since AMUX-102 stage 2.
# Comma and 'and' are NEVER linkers: 'metformin, glipizide, and insulin' is a
# co-prescription list and must stay three mentions (measured false merge).
_MAX_COMPOSE_GAP = 18  # chars between fragments; longer gaps are separate mentions

# Fragments that never compose: values, units, dosing, frequency, note verbs.
_NEUTRAL_FRAGMENT_TOKENS = frozenset({
    "mg", "mcg", "ug", "g", "kg", "ml", "cc", "l", "iu", "unit", "units",
    "meq", "tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule",
    "capsules", "pill", "pills", "drop", "drops", "puff", "puffs",
    "po", "iv", "im", "sq", "prn", "bid", "tid", "qid", "qd", "hs",
    "daily", "nightly", "weekly", "meals", "food", "today", "tomorrow",
    "ordered", "continue", "continue.", "start", "stop", "take", "takes",
    "given", "dose", "dosing", "mg/dl", "mmol/l", "mmhg",
    # dosing/taper verbs and single-letter units (2026-11-07 port):
    # 'Titrate lisinopril' and 'Temperature 38.2 C' must not compose.
    "titrate", "titration", "taper", "from", "to", "c", "f",
})
_COMPOSE_STOPWORDS = frozenset({"with", "without", "of", "the", "w", "o", "and"})

# Labels that describe the same kind of entity. GLiNER routinely splits one
# compound across families ('CBC' lab test + 'differential' vital sign), so
# composition treats these as compatible while keeping genuine cross-family
# pairs (lab + symptom) separate.
_COMPOSE_LABEL_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"lab test", "vital sign", "panel"}),
    frozenset({"therapeutic agent", "therapeutic class", "medication", "drug",
               "immunization"}),
    frozenset({"disorder", "disease", "symptom", "sign"}),
    frozenset({"medical intervention", "procedure"}),
)


def _labels_composable(left: str, right: str) -> bool:
    """True when two GLiNER labels describe the same entity family."""
    left, right = (left or "").lower(), (right or "").lower()
    if left == right:
        return True
    for family in _COMPOSE_LABEL_FAMILIES:
        if left in family and right in family:
            return True
    return False
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# The assertion-fragment and fused-value validators are mention-text
# regexes relocated to extraction_cues (AMUX-102 stage 2, design F5).


def _is_assertion_fragment(text: str) -> bool:
    """True when a fragment is an assertion cue, not a compound modifier.

    'Pneumonia' + 'ruled out' must never compose: the cue carries status,
    not meaning, and composing it turned a negated mention into an
    unresolvable span (2026-11-07 port regression).
    """
    return bool(_ASSERTION_FRAGMENT_RE.match(text.strip()))


def _is_neutral_fragment(text: str) -> bool:
    """True when a fragment is only values/units/dosing (never composed)."""
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return True
    return all(
        t.isdigit()
        or t in _NEUTRAL_FRAGMENT_TOKENS
        or _FUSED_VALUE_TOKEN_RE.match(t)
        for t in tokens
    )


def _compose_compound_spans(spans: list, text: str, doc) -> list:
    """Merge adjacent same-label spans separated by linker-only gaps.

    'CBC' + 'with differential' -> 'CBC with differential' (the phrase that
    carries the qualifier into search). Only affirmed spans in the same
    section, same GLiNER label, and neither side numeric/unit/dosing.
    The merged span keeps the head's offsets start and records every merged
    fragment in ``composed_from`` so resolution can rank candidates by
    fragment completeness.
    """
    if len(spans) < 2:
        return spans
    out: list = []
    for span in spans:
        prev = out[-1] if out else None
        if prev is None:
            out.append(span)
            continue
        if not (
            prev.status == "affirmed"
            and span.status == "affirmed"
            and prev.section == span.section
            and _labels_composable(prev.entity_type, span.entity_type)
            and 0 < span.span_start - prev.span_end <= _MAX_COMPOSE_GAP
            and compose_gap(doc, prev.span_end, span.span_start)
            and not _is_neutral_fragment(prev.text)
            and not _is_neutral_fragment(span.text)
            and not _is_assertion_fragment(prev.text)
            and not _is_assertion_fragment(span.text)
        ):
            out.append(span)
            continue
        fragments = list(prev.composed_from or [prev.text])
        fragments.append(span.text)
        prev.text = " ".join(text[prev.span_start:span.span_end].split())
        prev.span_end = span.span_end
        prev.ner_confidence = max(prev.ner_confidence, span.ner_confidence)
        prev.composed_from = fragments
        # NLP-time precomputed fields are stale for the merged text —
        # drop them so resolve falls back to the mention-text helpers.
        prev.search_variants = None
        prev.conjunction_parts = None
        continue
    return out


def _fragment_completeness(span, result) -> bool:
    """True when a candidate represents every fragment of a composed span.

    A combination anchor whose combination_members cover the fragment count
    is complete by construction ('lisinopril/HCTZ' -> 'hydrochlorothiazide /
    lisinopril'); otherwise each fragment must contribute a content token to
    the display ('CBC with differential' -> 'CBC with Manual Differential').
    """
    fragments = getattr(span, "composed_from", None) or []
    if len(fragments) < 2:
        return True
    members = getattr(result, "combination_members", None) or []
    if len(members) >= len(fragments):
        if getattr(span, "composed_strict", False):
            # List-separator joins (',' / 'and') are the risky class: a
            # member COUNT match is not enough — every fragment must be
            # textually covered by the candidate's member displays, so
            # 'aspirin and clopidogrel' cannot land on 'aspirin / caffeine'
            # (measured DAPT regression, 2026-11-09). Alias-fragment joins
            # ('/'-stitched, e.g. HCTZ) keep the count shortcut.
            member_tokens = set()
            for mem in members:
                for tok in (
                    getattr(mem, "display", None)
                    or (mem.get("display", "") if isinstance(mem, dict) else "")
                    or ""
                ).lower().split():
                    member_tokens.update(_TOKEN_RE.findall(tok))
            for fragment in fragments:
                ftoks = [
                    t for t in _TOKEN_RE.findall(fragment.lower())
                    if not t.isdigit() and t not in _COMPOSE_STOPWORDS
                ]
                if ftoks and not all(t in member_tokens for t in ftoks):
                    return False
        return True
    display_tokens = set(_TOKEN_RE.findall((result.display or "").lower()))
    for fragment in fragments:
        tokens = [
            t for t in _TOKEN_RE.findall(fragment.lower())
            if not t.isdigit() and t not in _COMPOSE_STOPWORDS
        ]
        if tokens and not all(
            _token_covered(t, display_tokens) for t in tokens
        ):
            return False
    return True


# Initialism expansions for fragment coverage: 'GI' abbreviates
# 'Gastro-Intestinal' by initials, so a prefix rule cannot cover it
# ('Upper GI bleed' -> 'Upper Gastrointestinal Bleeding',
# amux-canonical follow-up 3, 2026-11-11). Keep curated and minimal.
_ABBREV_INITIALISM_EXPANSIONS = {
    "gi": "gastrointestinal",
    "gu": "genitourinary",
    "ugib": "gastrointestinal",
}


def _token_covered(token: str, display_tokens: set[str]) -> bool:
    """Exact token coverage, with abbreviation tolerance.

    Two narrow tolerances: a length-floored prefix match ('bleed' ->
    'bleeding') and the curated initialism map above ('gi' ->
    'gastrointestinal'). A composed candidate is not incomplete just
    because the display spells an abbreviation out.
    """
    if token in display_tokens:
        return True
    expansion = _ABBREV_INITIALISM_EXPANSIONS.get(token)
    if expansion and expansion in display_tokens:
        return True
    if len(token) < 2:
        return False
    return any(
        d.startswith(token) and len(d) > len(token)
        for d in display_tokens
    )


# Inline-trigger detection (_detect_inline_trigger) is token-based since
# AMUX-102 stage 2: Mt4dsCueAnnotator.inline_trigger (design F3). The
# false-positive stoplists and validators (_is_false_positive) live in
# extraction_cues (design F7) and are re-exported above.


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
    # Fragments merged into this span by _compose_compound_spans, head first.
    # None for ordinary spans. Internal only — omitted from to_dict.
    composed_from: list[str] | None = None
    # Trailing dose/strength expression captured for this mention
    # ('20/12.5 mg', '500 mg', '5-500') — dose context, exposed in output
    # and used for combination corroboration (component count must match
    # the composed fragment count) and multi-ingredient inference.
    # Internal only — omitted from to_dict (use to_dict_with_strength).
    strength: str | None = None
    # True when this composed span was joined across a LIST separator
    # (',' / 'and') under strength corroboration — the riskier join class.
    # Members-coverage is then checked STRICTLY (see _fragment_completeness)
    # so 'aspirin and clopidogrel' can never land on aspirin/caffeine just
    # because it also has two members.
    composed_strict: bool = False
    # Fallback query ladder computed at NLP time from the entity's token
    # slice (design F6): [value-stripped, form-noun-stripped,
    # leading-qualifier-stripped], non-empty and != the full text. The
    # FULL mention is NOT in the list — resolve paths query span.text
    # first, then these variants. None on manually constructed spans
    # (resolve then uses the legacy mention-text fallbacks). Internal
    # only — omitted from to_dict.
    search_variants: list[str] | None = None
    # Conjunction-split parts computed at NLP time from the entity's
    # token slice (design F8; [] = no valid split). None on manually
    # constructed spans. Internal only — omitted from to_dict.
    conjunction_parts: list[str] | None = None

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
    # Qualifier phrases carried by the mention but not by the anchor name
    # (2026-11-11 addendum A — qualifier handling is resolver-side): the
    # vocabulary does not carry qualifier strings, so 'Fasting glucose'
    # resolves to the Glucose anchor and records qualifier 'fasting'; same
    # for laterality, numbered stage/type/grade, contrast.
    qualifiers: list[str] = field(default_factory=list)
    # R4 abstention flags (qualifier-guard port, 2026-11-11): a candidate
    # chosen while a qualifier-variant runner-up sits within the ambiguity
    # margin carries 'qualifier_ambiguous' — the pick is a coin flip
    # between clinical variants ('CBC with/without Differential'), and
    # consumers deserve to know instead of a silent choice.
    flags: list[str] = field(default_factory=list)
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
            "qualifiers": self.qualifiers,
            "flags": self.flags,
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
        device: str | None = None,
    ):
        self._ner_model_name = ner_model
        # Pinned HF revision so weight drift can't silently change recall
        # (drift observed 2026-08-14). None disables pinning (e.g. local
        # model paths, where huggingface_hub has no revision concept).
        self._ner_model_revision = ner_revision
        self._labels = labels or DEFAULT_LABELS
        self._threshold = threshold
        self._device_param = device
        self._nlp = None
        self._ner_model = None
        # en_core_web_sm for dependency parsing (Signals 1-2 of the
        # three-signal lab-vs-med arbiter). Loaded lazily alongside the
        # rest of the pipeline; None when the model is not installed
        # (ConText-only arbitration still works).
        self._parser_nlp = None
        self._parser_loaded = False
        # spaCy-native cue/guard component (AMUX-102): token-indexed
        # pre/post-span cue annotations, built once the medspaCy
        # pipeline exists (_ensure_loaded).
        self._cue_annotator = None

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

        # Artifact-governance calibration gate (plan §5): the loaded
        # pipeline's calibration (labels + threshold) must be an accepted
        # one. MEDTERM4DS_NER_ALLOW_UNCALIBRATED=1 loads anyway but logs
        # the accepted-vs-served ids on EVERY load (never refuse silently,
        # never override silently). Empty registry = migration mode (the
        # Phase 2 dual-publish populates ACCEPTED_NER_CALIBRATIONS).
        from medterm4ds.core.artifact_manifest import ACCEPTED_NER_CALIBRATIONS

        loaded_calibration = _calibration_id(self._labels, self._threshold)
        if ACCEPTED_NER_CALIBRATIONS and loaded_calibration not in ACCEPTED_NER_CALIBRATIONS:
            if os.getenv("MEDTERM4DS_NER_ALLOW_UNCALIBRATED", "") == "1":
                logger.warning(
                    "NER calibration %s is NOT in the accepted set %s — "
                    "loading anyway (MEDTERM4DS_NER_ALLOW_UNCALIBRATED=1). "
                    "Label set / threshold were not calibrated against the "
                    "golden set; extraction quality is unverified.",
                    loaded_calibration, sorted(ACCEPTED_NER_CALIBRATIONS),
                )
            else:
                raise RuntimeError(
                    f"NER calibration {loaded_calibration} (labels+threshold "
                    f"hash) is not accepted by this medterm4ds release "
                    f"(accepted: {sorted(ACCEPTED_NER_CALIBRATIONS)}). "
                    "Recalibrate against the golden set, or set "
                    "MEDTERM4DS_NER_ALLOW_UNCALIBRATED=1 to load anyway "
                    "(ids are logged on every load)."
                )

        # Load GLiNER. revision= pins the HF repo commit (see
        # DEFAULT_NER_MODEL_REVISION) so weight drift can't silently change
        # extraction recall; None (explicit constructor override or empty
        # env var) loads the repo head / local path unpinned.
        from gliner import GLiNER
        # FHIR4-51: pin native thread pools before GLiNER drags in torch.
        from medterm4ds.core.runtime import apply_thread_pinning

        apply_thread_pinning()
        self._ner_model = GLiNER.from_pretrained(
            self._ner_model_name, revision=self._ner_model_revision,
        )
        # FHIR4-51: re-check after the full GLiNER stack is loaded. The
        # pin-time check cannot see sklearn's libgomp (loaded lazily at
        # `from gliner import GLiNER` and beyond), so the real extraction
        # workload can run THREE GOMP runtimes while the first warning
        # reported two.
        from medterm4ds.core.runtime import warn_duplicate_omp_runtimes

        warn_duplicate_omp_runtimes()
        # GPU/MPS placement (MEDTERM4DS_DEVICE, default auto-detect). GLiNER
        # inference is the bulk of extract() cost; everything else in this
        # pipeline (medspaCy, PyRuSH, en_core_web_sm parser) is CPU-native.
        from medterm4ds.core.env import resolve_device
        self._device = resolve_device(self._device_param)
        if self._device != "cpu":
            self._ner_model = self._ner_model.to(self._device)

        # Load medspaCy for ConText (disable target_matcher — we use GLiNER
        # instead). medspacy_disable is the medspaCy 1.3.x kwarg; the plain
        # spaCy `disable=` kwarg is silently swallowed by **model_kwargs when
        # the base model is blank-English, so the matcher used to run anyway.
        import medspacy
        self._nlp = medspacy.load(medspacy_disable=["medspacy_target_matcher"])
        _register_context_arbiter(self._nlp)
        self._cue_annotator = Mt4dsCueAnnotator(self._nlp)

        # Parser for the three-signal arbiter (Signals 1-2: head noun,
        # unit type). Separate en_core_web_sm model, not a component of
        # the medspaCy pipeline: adding a parser pipe to it would require
        # re-running the full pipeline per doc, and the medspaCy doc is
        # tokenized by PyRuSH-resolved components we don't want to
        # disturb. Both run on the same text; alignment is by char_span.
        self._ensure_parser_loaded()

        logger.info("NLP pipeline loaded (GLiNER %s + medspaCy ConText, device=%s)",
                    self._ner_model_name, self._device)

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

        # CR-054: parser pass runs only when entities exist — notes with
        # zero medical entities were paying the full tagger+parser cost on
        # the whole text for nothing.
        parser_doc = self._parser_nlp(text) if self._parser_nlp is not None else None

        return self._finalize_doc(text, doc, parser_doc, raw_entities)

    def process_batch(
        self,
        texts: list[str],
        labels: list[str] | None = None,
    ) -> list[list[FilteredSpan]]:
        """Batched counterpart of ``process`` for many texts at once.

        Sentences from every input are pooled into single GLiNER inference
        calls (``inference(batch_size=MEDTERM4DS_EXTRACT_BATCH_SIZE)``,
        default 32), so N texts cost roughly 1/N the forward passes of
        looping ``process``. medspaCy and the parser run through
        ``nlp.pipe`` (same per-doc components, doc order preserved).
        Per-text output has the same shape and semantics as ``process`` —
        the batch-parity tests pin this.

        GLiNER also supports token-budget packing via
        ``InferencePackingConfig`` (gliner/infer_packing.py); sentence
        pooling with count-based batches is the default because its
        numerical parity with the single-text path is test-enforced.
        """
        self._ensure_loaded()
        labels = labels if labels is not None else self._labels

        if not texts:
            return []

        docs = list(self._nlp.pipe(texts, batch_size=8))

        # Pool sentences across all texts, remembering (doc index, sentence
        # start offset) so batched results scatter back exactly like
        # process() maps its per-sentence predictions.
        owners: list[tuple[int, int]] = []
        sent_texts: list[str] = []
        for doc_idx, doc in enumerate(docs):
            for sent in doc.sents:
                sent_text = sent.text.strip()
                if sent_text:
                    owners.append((doc_idx, sent.start_char))
                    sent_texts.append(sent_text)

        raw_per_doc: list[list[dict]] = [[] for _ in texts]
        if sent_texts:
            batch_size = env_int("MEDTERM4DS_EXTRACT_BATCH_SIZE", minimum=1) or 32
            batched = self._ner_model.inference(
                sent_texts, labels,
                flat_ner=True, threshold=self._threshold, multi_label=False,
                batch_size=batch_size,
            )
            for (doc_idx, sent_offset), sent_ents in zip(owners, batched, strict=False):
                for ent in sent_ents:
                    raw_per_doc[doc_idx].append({
                        "start": sent_offset + ent["start"],
                        "end": sent_offset + ent["end"],
                        "label": ent["label"],
                        "score": ent["score"],
                        "text": ent["text"],
                    })

        # CR-054: the en_core_web_sm parse runs only for texts that HAVE
        # entities — blanket-parsing every input paid tagger+parser cost on
        # entity-free texts for nothing, batch-wide.
        parser_docs: list[Any] = [None] * len(texts)
        if self._parser_nlp is not None:
            needy = [i for i, raw in enumerate(raw_per_doc) if raw]
            if needy:
                parsed = self._parser_nlp.pipe(
                    (texts[i] for i in needy), batch_size=16
                )
                for i, parser_doc in zip(needy, parsed, strict=False):
                    parser_docs[i] = parser_doc

        return [
            self._finalize_doc(texts[i], docs[i], parser_docs[i], raw_per_doc[i])
            for i in range(len(texts))
        ]

    def _finalize_doc(
        self,
        text: str,
        doc: Any,
        parser_doc: Any,
        raw_entities: list[dict],
    ) -> list[FilteredSpan]:
        """Register GLiNER entities on the spaCy doc, re-run ConText, and
        build FilteredSpans. Shared tail of process() and process_batch()."""
        if not raw_entities:
            return []

        # Step 3: Add GLiNER entities as spaCy spans so ConText can annotate them

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

        # Cue/guard annotations (AMUX-102): token-indexed pre/post-span
        # cue matches + clause/pediatric guard tokens, computed once per
        # doc right after the ConText re-run.
        self._cue_annotator(doc)

        # Step 4: Build FilteredSpan results with ConText annotations.
        # Materialize noun chunks ONCE per doc: doc.noun_chunks regenerates
        # the syntax iterator on every access, so per-span iteration made
        # long docs O(spans x chunks) — measured 50% of batch runtime on
        # drug-label workloads (structured-team instrumented profile).
        parser_chunks = list(parser_doc.noun_chunks) if parser_doc is not None else None
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
            # (token-based since AMUX-102 stage 2; still computed from the
            # RAW char offsets, before any overlap filtering, like the old
            # raw-slice helper).
            cleaned_text, inline_status = self._cue_annotator.inline_trigger(doc, start, end)
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
                        parser_chunks,
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
                        # Pediatric-subject guard (2026-11-11): 'Child with
                        # asthma' is the PATIENT, not a relative — ConText's
                        # kinship cue is wrong when an age descriptor
                        # governs the finding; the span stays affirmed.
                        context_status = (
                            None if self._cue_annotator.pediatric_subject(doc, start, end) else "family"
                        )
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
                # ConText clause-scope guard (adversarial corpus as-scope-03,
                # 2026-11-07): ConText's token window can cross a ';'/':'
                # boundary — a 'negated' verdict is only trusted when the
                # span's own clause carries a negation cue.
                if (
                    status == "negated"
                    and not self._cue_annotator.clause_negation_supported(doc, start, end)
                ):
                    status = "affirmed"
                # Pre-span cues (2026-11-07 edge corpus). PSEUDONEGATION
                # overrides even a ConText negation ('cannot rule out X' is
                # uncertain, never negated); the weaker cues only lift an
                # otherwise-affirmed span.
                pre_cue = self._cue_annotator.pre_span_cue(doc, start)
                if pre_cue == "pseudo_negation" and status in ("affirmed", "negated"):
                    status = "uncertain"
                elif status == "affirmed" and pre_cue:
                    status = {"uncertainty": "uncertain", "negation": "negated", "historical": "historical"}[pre_cue]
                # Sentence-local post-span cues (2026-11-07): negation and
                # uncertainty may follow the span, parenthetical or bare.
                if status == "affirmed":
                    if self._cue_annotator.post_span_negation_cue(doc, end, start):
                        status = "negated"
                    elif self._cue_annotator.post_span_uncertainty_cue(doc, end):
                        status = "uncertain"
                display_text = ent_text

            # AMUX-102 stage 3 (design F6/F8): precompute the mention-only
            # resolve decisions at NLP time, from the doc's token slice for
            # the DISPLAY text (an inline trigger may have cleaned the raw
            # slice; the cleaned text is a contiguous sub-slice).
            cstart, cend = start, end
            if display_text != ent_text:
                off = (
                    ent_text.rfind(display_text)
                    if ent_text.endswith(display_text)
                    else ent_text.find(display_text)
                )
                if off >= 0:
                    cstart, cend = start + off, start + off + len(display_text)
            ent_tokens = [
                t for t in doc
                if not t.is_space
                and t.idx >= cstart
                and t.idx + len(t.text) <= cend
            ]
            if ent_tokens:
                search_variants = self._cue_annotator.search_variant_ladder(ent_tokens)
                conjunction_parts = self._cue_annotator.conjunction_split(ent_tokens)
            else:
                # token alignment failed — leave unset so resolve falls
                # back to the legacy mention-text helpers (exact parity).
                search_variants = None
                conjunction_parts = None

            spans.append(FilteredSpan(
                text=display_text,
                entity_type=ent_type,
                status=status,
                span_start=start,
                span_end=end,
                ner_confidence=score,
                context_categories=context_categories,
                search_variants=search_variants,
                conjunction_parts=conjunction_parts,
            ))

        spans = _absorb_trailing_strength(spans, text, doc)
        spans = _stitch_gated_list_pairs(spans, text, doc)
        return _compose_compound_spans(
            _stitch_drug_fragments(spans, text, doc), text, doc
        )


# Inline-marker field vocabulary for format="annotated". source_code is the
# raw vocabulary id (RXNORM:6809); canonical_id is the internal anchor id.
_ANNOTATION_FIELDS = (
    "text", "name", "type", "source_code", "canonical_id", "status",
)
_DEFAULT_ANNOTATION_FIELDS = ("text", "type")
# Placeholder for unavailable fields so marker positions stay stable (a bare
# "?" would collide with clinical uncertainty shorthand in notes).
_ANNOTATION_UNKNOWN = "UNKNOWN"


def _normalize_annotation_fields(value: str | list[str] | None) -> list[str]:
    """Normalize the annotation_fields argument to a validated list.

    Accepts a list or a comma-separated string (house style, matching
    result_types). None/blank -> the default [text, type]; unknown field
    names raise a ValueError naming the valid set.
    """
    if value is None:
        return list(_DEFAULT_ANNOTATION_FIELDS)
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not value:
        return list(_DEFAULT_ANNOTATION_FIELDS)
    unknown = [f for f in value if f not in _ANNOTATION_FIELDS]
    if unknown:
        raise ValueError(
            f"Unknown annotation_fields: {unknown}. "
            f"Valid fields: {list(_ANNOTATION_FIELDS)}."
        )
    return list(value)


def _validate_min_grade(grade: str) -> None:
    """QC-161/QA-004: garbage min_grade was silently treated as the strictest
    grade (0) because _GRADE_ORDER.get(grade_threshold, 0) defaults unknown
    keys to 0. Shared by the eager extract() boundary and both resolve paths."""
    if grade not in _GRADE_ORDER:
        valid = ", ".join(sorted(_GRADE_ORDER))
        raise ValueError(f"Unknown min_grade: {grade!r}. Valid: {valid}.")


def _annotation_marker_values(
    fields: list[str],
    *,
    entity_text: str,
    label: str,
    span: FilteredSpan,
    concept: ExtractedConcept | None,
) -> list[str]:
    """Render the configured marker fields for one span.

    Resolution-dependent fields (name, source_code, canonical_id) fall back
    to UNKNOWN when the span did not resolve to a code, keeping field
    positions stable across all markers in the same output.
    """
    values: list[str] = []
    for f in fields:
        if f == "text":
            values.append(entity_text)
        elif f == "type":
            values.append(label)
        elif f == "status":
            values.append(span.status)
        elif f == "name":
            values.append(concept.display if concept and concept.display else _ANNOTATION_UNKNOWN)
        elif f == "source_code":
            if concept and concept.code:
                values.append(f"{concept.source}:{concept.code}")
            else:
                values.append(_ANNOTATION_UNKNOWN)
        elif f == "canonical_id":
            values.append(
                concept.canonical_id if concept and concept.canonical_id
                else _ANNOTATION_UNKNOWN
            )
    return values


def _qualifier_inverted(query: str, display: str) -> bool:
    """True when a result's with/without qualifier inverts the query's.

    2026-11-07 NER battery, model team finding 2 ('CBC with differential'
    resolving to 'Hemogram and Platelets WITHOUT Differential'): right
    family, inverted meaning — a wrong answer, not a near-miss. Only a
    with↔without flip on the SAME qualifier token counts; other tokens
    are irrelevant.
    """
    # EV01-QA-01: display-side qualifiers are lowercased before the set
    # compare — LOINC preferred names render Title Case ('Without'), and
    # {'with', 'Without'} silently passed the inversion guard.
    # EV01-QA-03: the 'w/'/'w/o' abbreviations fold to with/without the
    # same way _extract_qualifiers folds them (line ~2516) — the inversion
    # guard must know the family's full qualifier vocabulary.
    qual_re = re.compile(r"\b(without|w/o|with|w/)\s+([a-z]{3,})\b", re.IGNORECASE)
    canon = {"with": "with", "w/": "with", "without": "without", "w/o": "without"}
    q_quals = {tok.lower(): canon[q.lower()] for q, tok in qual_re.findall(query)}
    if not q_quals:
        return False
    for q, tok in qual_re.findall(display):
        q_tok = q_quals.get(tok.lower())
        if q_tok is not None and {q_tok, canon[q.lower()]} == {"with", "without"}:
            return True
    return False


def _result_is_lab(r) -> bool:
    """True when a search result is a canonical lab anchor (VAL-LAB-*)."""
    return (getattr(r, "canonical_id", "") or "").startswith("VAL-LAB-")


def _scores_tied(a: float, b: float) -> bool:
    """Epsilon-based tie detection (EV01-QA-06).

    Shares ONE epsilon definition with the canonical layer
    (SearchService.CANONICAL_TIE_EPSILON) — exact-float tie compares are
    defeated by 1-ULP noise between the batch and single embed paths.
    """
    from medterm4ds.services.search import SearchService
    return abs(a - b) < SearchService.CANONICAL_TIE_EPSILON


def _result_is_medication(r) -> bool:
    """True when a search result is a canonical medication anchor (VAL-MED-*)."""
    return (getattr(r, "canonical_id", "") or "").startswith("VAL-MED-")


def _context_decided_lab(span) -> bool:
    """True when the ConText arbiter EXCLUSIVELY decided lab for this span.

    The medspaCy MEASUREMENT/ADMINISTRATION arbitration (level, trough,
    drawn, blood draw, serum, ...) records its verdict on every span.
    ['lab'] alone is an exclusive measurement decision — ['lab',
    'medication'] means both cue families fired and carries no decision.
    Consulted by the cross-type tie rule so measurement context outranks
    the bare-mention drug preference.
    """
    return (getattr(span, "context_categories", None) or []) == ["lab"]


# ---- R4 abstention (qualifier-guard port, 2026-11-11) ----------------------
# Prototype: fhir4px-model scripts/experiments/qualifier_guard.py (R4), where
# the battery scored the guard net-neutral over production — R1-R3 were
# already absorbed piecewise. R4 (silent picks between near-tied qualifier
# variants) had no production equivalent; that is what lands here.

# Exclusive qualifier groups: candidates differing ONLY across one group's
# members are clinical variants of each other ('CBC with Differential' vs
# 'CBC without Differential') — a near-tied pick between them is a coin
# flip, not a resolution.
_QUALIFIER_EXCLUSIVE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"with", "without"}),
    frozenset({"positive", "negative"}),
    frozenset({"left", "right"}),
    frozenset({"acute", "chronic"}),
    frozenset({"pre", "post"}),
    frozenset({"primary", "secondary"}),
    frozenset({"malignant", "benign"}),
    frozenset({"systolic", "diastolic"}),
    frozenset({"unilateral", "bilateral"}),
    frozenset({"increased", "decreased"}),
    frozenset({"high", "low"}),
    frozenset({"normal", "abnormal"}),
    frozenset({"present", "absent"}),
)
# Score margin under which two qualifier variants count as a coin flip
# (prototype default, kept).
_QUALIFIER_AMBIGUITY_MARGIN = 0.03

_VARIANT_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _qualifier_variants(display_a: str, display_b: str) -> bool:
    """True when two candidate displays are qualifier variants of one concept.

    Either they differ across an exclusive group ('with' vs 'without'
    differential) or their token sets differ by at most two tokens — the
    narrow difference class of laterality/qualifier swaps. Identical
    token sets are the same candidate, not variants.
    """
    ta = set(_VARIANT_TOKEN_RE.findall(display_a.lower()))
    tb = set(_VARIANT_TOKEN_RE.findall(display_b.lower()))
    if ta == tb:
        return False
    if len(ta ^ tb) > 2:
        return False
    for group in _QUALIFIER_EXCLUSIVE_GROUPS:
        if (ta & group) and (tb & group) and (ta & group) != (tb & group):
            return True
    return len(ta ^ tb) == 2


def _qualifier_ambiguous(chosen, runner_up) -> bool:
    """R4: the chosen candidate wins by less than the ambiguity margin over
    a qualifier-variant runner-up — flag instead of picking silently."""
    if chosen is None or runner_up is None:
        return False
    return (
        abs(runner_up.score - chosen.score) < _QUALIFIER_AMBIGUITY_MARGIN
        and _qualifier_variants(chosen.display, runner_up.display)
    )


def _first_matching_concept(
    span: FilteredSpan,
    results: list,
    ss: tuple[str, ...] | None,
    *,
    result_type_prefixes: set[str] | None,
    grade_threshold: str,
    composed: bool = False,
) -> ExtractedConcept | None:
    """First search result passing the span's source/category/grade filters.

    Constrain-then-fallback (QC-182 follow-up): try the label's categories
    first; if no result clears the floor, retry unfiltered so the
    constraint never reduces recall. An explicit caller result_types stays
    a hard filter (QC-153) and disables the label constraint entirely.
    Shared by the single-text and pooled-batch canonical paths.
    """
    source_set = None
    # GLiNER's 'immunization' mislabel for oral drugs: the label's source
    # constraint (CVX) is wrong for a drug mention — bypass it so the
    # RxNorm combination anchor is reachable; completeness still gates.
    if ss and not (
        getattr(span, "composed_from", None)
        and span.entity_type.lower() in _MISLABELED_DRUG_LABELS
    ):
        source_set = set()
        for s in ss:
            source_set.add(s.upper())
            # Pairwise SAB aliasing via the canonical map (core.normalize) —
            # single source of truth shared with search's _ancestor_fallback.
            source_set.update(expand_source_aliases([s]))

    label_prefixes = (
        None if result_type_prefixes
        else _categories_to_prefixes(_span_search_categories(span))
    )
    # Composed spans never take the unfiltered fallback: a composed phrase may
    # only resolve within its label's categories (same result_type — iteration
    # (c) lesson: label-only constraints reintroduced type drift on mislabeled
    # spans when the fallback was allowed).
    if composed and label_prefixes:
        # GLiNER's 'immunization' mislabel for oral drugs makes the label
        # constraint wrong exactly when it matters ('Acetaminophen/
        # hydrocodone' typed immunization must still reach the RxNorm
        # combination anchor) — the mislabel-prone label keeps the
        # unfiltered fallback; completeness still gates every candidate.
        mislabel_prone = span.entity_type.lower() in _MISLABELED_DRUG_LABELS
        prefix_attempts = [label_prefixes, None] if mislabel_prone else [label_prefixes]
    else:
        prefix_attempts = [label_prefixes, None] if label_prefixes else [None]
    for label_pf in prefix_attempts:
        def _eligible(r, _label_pf=label_pf) -> bool:
            """Per-pass candidate filter — single source for both the
            selection scan and the R4 runner-up scan (``_label_pf`` bound
            per pass, B023)."""
            # Qualifier-inversion guard (2026-11-07 NER battery): a
            # with/without flip on the same token is a wrong answer, not
            # a lower-grade match — skip it and let the next candidate
            # (or abstention) win.
            if _qualifier_inverted(span.text, r.display):
                return False
            # Source filter
            if source_set and r.anchor_system not in source_set:
                return False
            result_canonical_id = getattr(r, "canonical_id", "") or ""
            # Result-type filter (QC-153: previously the batch path
            # ignored result_types entirely — FHIR resultTypes=condition
            # still returned medications)
            if result_type_prefixes and result_canonical_id and not any(
                result_canonical_id.startswith(p) for p in result_type_prefixes
            ):
                return False
            # Label-derived category constraint (constrain pass;
            # the fallback pass re-runs with _label_pf=None)
            if _label_pf and result_canonical_id and not any(
                result_canonical_id.startswith(p) for p in _label_pf
            ):
                return False
            # Grade filter
            if _GRADE_ORDER.get(r.match_grade, 2) > _GRADE_ORDER.get(grade_threshold, 0):
                return False
            # Composed spans: candidate must represent every fragment, and
            # clear the 'probable' floor regardless of the caller's
            # min_grade.
            if composed:
                if _GRADE_ORDER.get(r.match_grade, 2) > _GRADE_ORDER["probable"]:
                    return False
                if not _fragment_completeness(span, r):
                    return False
            return True

        chosen = None
        for r in results:
            if not _eligible(r):
                continue
            if chosen is None:
                chosen = r
                # Small resolution rule (Joel, 2026-11-10 pressure test,
                # twin_regate.json cross-type tie class): a bare analyte
                # mention ('dexamethasone', 'tacrolimus') with no label
                # constraint tying a LOINC lab anchor and a medication
                # anchor on score prefers the medication — bare-phrase
                # mentions in notes are overwhelmingly drug-use mentions,
                # and real level queries ('tacrolimus level') outscore the
                # drug anchor on the level-specific lab surfaces, so only
                # an exact tie ever flips. Never applies inside a
                # constrained pass — a label/type constraint is evidence.
                #
                # ConText override (2026-11-11): when the medspaCy ConText
                # arbiter EXCLUSIVELY decided lab (MEASUREMENT cues —
                # level/trough/drawn/blood draw/serum...), the measurement
                # context is evidence the tie rule must respect: the lab
                # anchor stays and a tied medication cannot steal it.
                if (
                    label_pf is None
                    and _result_is_lab(r)
                    and not _context_decided_lab(span)
                ):
                    continue
                # mirror: a medication chosen under a lab decision scans
                # for a tied lab anchor before settling
                if (
                    label_pf is None
                    and _result_is_medication(r)
                    and _context_decided_lab(span)
                ):
                    continue
                break
            # EV01-QA-06: tie detection is score-epsilon based (the canonical
            # layer's doctrine, 2026-11-12 owner ruling) — exact-float compare
            # loses to 1-ULP twin noise between the batch and single embed
            # paths. One epsilon definition, shared with services.search.
            if (
                label_pf is None
                and _scores_tied(r.score, chosen.score)
            ):
                # default tie direction: medication wins a cross-type tie
                if (
                    _result_is_medication(r)
                    and not (_result_is_lab(chosen) and _context_decided_lab(span))
                ):
                    chosen = r
                    break
                # ConText mirror: a tied lab anchor beats the medication
                # when the arbiter exclusively decided lab ('tacrolimus
                # level', 'blood draw for lithium').
                if (
                    _result_is_lab(r)
                    and _result_is_medication(chosen)
                    and _context_decided_lab(span)
                ):
                    chosen = r
                    break
        if chosen is None:
            continue
        # R4 abstention (qualifier-guard port, 2026-11-11): the first
        # eligible candidate after the chosen one is the runner-up — a
        # qualifier variant within the ambiguity margin flags the pick as
        # a coin flip instead of choosing silently.
        flags: list[str] = []
        chosen_idx = next(
            (i for i, x in enumerate(results) if x is chosen), None,
        )
        if chosen_idx is not None:
            runner = next(
                (r for r in results[chosen_idx + 1:] if _eligible(r)), None,
            )
            if _qualifier_ambiguous(chosen, runner):
                flags = ["qualifier_ambiguous"]
        canonical_id = getattr(chosen, "canonical_id", "") or ""
        combination_members = getattr(chosen, "combination_members", None) or []
        return ExtractedConcept(
            code=chosen.code, source=chosen.source, display=chosen.display,
            matched_text=span.text, status=span.status,
            section=span.section, confidence=chosen.score,
            match_grade=chosen.match_grade, ner_label=span.entity_type,
            span_start=span.span_start, span_end=span.span_end,
            canonical_id=canonical_id,
            combination_members=combination_members,
            flags=flags,
        )
    return None


def _resolve_composed_multi(
    span: FilteredSpan,
    fetch,
    ss: tuple[str, ...] | None,
    *,
    result_type_prefixes: set[str] | None,
    grade_threshold: str,
) -> list[ExtractedConcept]:
    """Resolve a span, preferring the composed phrase, never losing siblings.

    A composed span that fails completeness/type/grade falls back to EVERY
    fragment: composition may add specificity, but it may never drop a mention
    ('Pneumonia with sepsis' must still yield both concepts when no single
    anchor covers the pair — 2026-11-07 review blocker).
    """
    composed = bool(span.composed_from)
    strength_parts = _strength_components(span.strength)
    non_neutral_frags = None
    if composed:
        non_neutral_frags = [
            f for f in (span.composed_from or [])
            if not (_is_neutral_fragment(f) or _is_assertion_fragment(f))
        ]
    # Corroboration gate (2026-11-09 design): a captured strength's
    # component count must match the composed fragment count — a mismatch
    # means the strength belongs to something else, and a combination
    # guess would be wrong ('aspirin 81 and warfarin 5 mg' must not fuse).
    corroboration_ok = (
        strength_parts is None
        or non_neutral_frags is None
        or strength_parts == len(non_neutral_frags)
    )
    if composed and corroboration_ok:
        # A composed query is already gated by fragment completeness, so it is
        # never held to a floor stricter than 'probable' — hybrid mode's default
        # min_grade is 'certain', which filtered every composed candidate before
        # the completeness logic could rank it (review blocker 2).
        composed_threshold = (
            "probable"
            if _GRADE_ORDER.get(grade_threshold, 0) < _GRADE_ORDER["probable"]
            else grade_threshold
        )
        # Strength-inclusive primary query (2026-11-09): canonical aliases
        # and lexical product rows carry strengths, so the full mention is
        # the better query; the names-only form retries on failure.
        queries = [span.text]
        if span.strength and span.strength not in span.text:
            queries.insert(0, f"{span.text} {span.strength}")
        for q in queries:
            matched = _first_matching_concept(
                span, fetch(q), ss,
                result_type_prefixes=result_type_prefixes,
                grade_threshold=composed_threshold,
                composed=True,
            )
            if matched is not None:
                return [matched]

    # Multi-ingredient inference (2026-11-09 design): a slash-strength on
    # a slash-less single name ('Ziac 2.5/6.25', 'Janumet 50/1000') is
    # itself evidence of a combination product — attempt the combination
    # anchor for name+strength, gated by combination_members count.
    if (
        not composed
        and span.entity_type in (_DRUG_NER_LABELS | _MISLABELED_DRUG_LABELS)
        and (strength_parts or 0) >= 2
    ):
        composed_threshold = (
            "probable"
            if _GRADE_ORDER.get(grade_threshold, 0) < _GRADE_ORDER["probable"]
            else grade_threshold
        )
        results = fetch(f"{span.text} {span.strength}")
        for r in results:
            if (
                _GRADE_ORDER.get(r.match_grade, 2) > _GRADE_ORDER[composed_threshold]
            ):
                continue
            members = getattr(r, "combination_members", None) or []
            if len(members) < (strength_parts or 0):
                continue
            if result_type_prefixes and not any(
                r.canonical_id.startswith(x) for x in result_type_prefixes
            ):
                continue
            canonical_id = getattr(r, "canonical_id", "") or ""
            return [ExtractedConcept(
                code=r.code, source=r.source, display=r.display,
                matched_text=span.text, status=span.status,
                section=span.section, confidence=r.score,
                match_grade=r.match_grade, ner_label=span.entity_type,
                span_start=span.span_start, span_end=span.span_end,
                canonical_id=canonical_id,
                combination_members=members,
            )]

    out: list[ExtractedConcept] = []
    seen: set[str] = set()
    # Corroborated-but-declined joins (strict members rejected an
    # alias-fragment combination) still emit every sibling — and because
    # the shared strength marked these as ONE mention's fragments, the
    # siblings resolve at the composed 'probable' floor, not the caller's
    # stricter default (otherwise 'lisinopril and HCTZ 20/12.5' would
    # silently lose the HCTZ sibling on hybrid's 'certain' floor).
    sibling_threshold = (
        "probable"
        if (
            getattr(span, "composed_strict", False)
            and _GRADE_ORDER.get(grade_threshold, 0) < _GRADE_ORDER["probable"]
        )
        else grade_threshold
    )
    for fragment in (span.composed_from or [span.text]):
        # Values/units and assertion cues are never mentions of their own.
        if composed and (_is_neutral_fragment(fragment) or _is_assertion_fragment(fragment)):
            continue
        fragment_span = replace(
            span, text=fragment, composed_from=None,
            span_end=span.span_start + len(fragment),
        )
        matched = _first_matching_concept(
            fragment_span, fetch(fragment), ss,
            result_type_prefixes=result_type_prefixes,
            grade_threshold=sibling_threshold,
            composed=False,
        )
        if matched is None:
            # CR-301 sibling parity: a fragment may need the leading-qualifier
            # fallback too ('Bilateral pneumonia' must still resolve when the
            # composed pair does not — measured loss of the pneumonia mention).
            variant = _leading_qualifier_fallback(fragment)
            if variant:
                matched = _first_matching_concept(
                    fragment_span, fetch(variant), ss,
                    result_type_prefixes=result_type_prefixes,
                    grade_threshold=grade_threshold,
                    composed=False,
                )
        if matched is None:
            continue
        key = matched.canonical_id or f"{matched.source}:{matched.code}"
        if key in seen:
            continue
        seen.add(key)
        out.append(matched)
    return out


def _dedup_concepts(concepts: list[ExtractedConcept]) -> list[ExtractedConcept]:
    """Deduplicate by canonical_id (preferred) or source:code (legacy).

    QC-183: the key includes status — without it a negated mention could
    REPLACE the affirmed mention of the same condition (the patient's
    active condition silently disappearing from output when a "no evidence
    of X" sentence scores higher).
    """
    seen: dict[tuple[str, str], ExtractedConcept] = {}
    for c in concepts:
        # Qualifier attributes (2026-11-11 addendum A): derived once at the
        # resolution funnel — phrases the mention carried that the anchor
        # name does not. Applied only when empty so an explicit caller
        # value is never overwritten.
        if not c.qualifiers:
            c.qualifiers = _extract_qualifiers(c.matched_text, c.display)
        key = (c.canonical_id or f"{c.source}:{c.code}", c.status)
        if key not in seen or c.confidence > seen[key].confidence:
            seen[key] = c
    return sorted(seen.values(), key=lambda c: c.confidence, reverse=True)


# Qualifier vocabulary (2026-11-11 addendum A): modifiers the mention may
# carry that the anchor name does not — the vocabulary will not carry
# qualifier strings, so the resolver records them as attributes instead of
# dropping them silently. A token qualifies only when it is in the mention
# and NOT in the resolved display.
_QUALIFIER_WORDS = frozenset({
    "fasting", "nonfasting", "random", "spot",
    "left", "right", "bilateral", "unilateral", "upper", "lower",
    "acute", "chronic", "primary", "secondary",
    "systolic", "diastolic", "peak", "trough",
    "contrast", "with", "without", "w", "wo",
})
_QUALIFIER_HEAD_RE = re.compile(
    r"\b(type|stage|grade|class|phase)\s+(\d+[a-d]?|[ivxIVX]+)\b",
    re.IGNORECASE,
)
_QUALIFIER_WORD_RE = re.compile(r"[a-z0-9]+")


def _extract_qualifiers(matched_text: str, display: str) -> list[str]:
    """Qualifier phrases present in the mention but absent from the display.

    'Fasting glucose' -> display 'Glucose' -> ['fasting'];
    'left arm pain' -> display 'Arm pain' -> ['left'];
    'Stage 2 pressure ulcer' -> display 'Pressure ulcer' -> ['stage 2'];
    'CBC with differential' -> display 'CBC W Differential' -> [] (the
    display carries the qualifier — nothing was dropped).
    """
    if not matched_text or not display:
        return []
    display_tokens = set(_QUALIFIER_WORD_RE.findall(display.lower()))
    # Display abbreviations that still carry the qualifier: 'W' = with,
    # 'W/O'/'WO' = without ('CBC W Differential' did not drop 'with').
    # Case-insensitive scan — displays may carry 'W/O' uppercase.
    display_lower = display.lower()
    if "w" in display_tokens:
        display_tokens.add("with")
    if "wo" in display_tokens or "w/o" in display_lower or "without" in display_tokens:
        display_tokens.add("without")
    qualifiers: list[str] = []
    # Numbered qualifiers first, with their spans so the word pass does not
    # double-report the head/number tokens.
    numbered_spans: list[tuple[int, int]] = []
    for m in _QUALIFIER_HEAD_RE.finditer(matched_text):
        phrase = " ".join(m.group(0).lower().split())
        if not any(t in display_tokens for t in _QUALIFIER_WORD_RE.findall(phrase)):
            qualifiers.append(phrase)
            numbered_spans.append((m.start(), m.end()))
    for m in _QUALIFIER_WORD_RE.finditer(matched_text.lower()):
        if any(s <= m.start() < e for s, e in numbered_spans):
            continue
        tok = m.group(0)
        if tok in _QUALIFIER_WORDS and tok not in display_tokens:
            qualifiers.append(tok)
    return qualifiers


def _filter_spans_by_status(
    spans: list[FilteredSpan],
    *,
    include_negated: bool = False,
    include_uncertain: bool = False,
    include_historical: bool = False,
    include_family: bool = False,
) -> list[FilteredSpan]:
    """Filter spans by ConText status, preserving position order.

    Don't deduplicate by text — return ALL spans (including repeats at
    different positions). Dedup happens at the concept level in
    resolve_spans (by canonical_id). For annotation mode, every mention
    should appear at its own position.
    """
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

    return sorted(
        (s for s in spans if s.status in allowed_statuses),
        key=lambda s: s.span_start,
    )


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
        device: str | None = None,
    ):
        self._nlp = NlpPipeline(
            ner_model=ner_model,
            labels=labels or DEFAULT_LABELS,
            threshold=threshold,
            device=device,
        )
        self._search_mode = search_mode
        self._min_grade = min_grade
        self._section_allowlist = section_allowlist
        # medspaCy pipelines and spaCy models are not thread-safe, and the
        # DuckDB-backed resolution shares one engine connection. Public
        # methods serialize on this lock so direct multi-threaded use of
        # mt.extract()/find_terms() is safe (single-lane, mirroring the
        # FHIR server's dedicated NER executor). RLock because extract()
        # calls find_terms()/resolve_spans() internally.
        self._lock = threading.RLock()

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
        Thread-safe: serializes on the service lock (see __init__).

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
        with self._lock:
            return self._find_terms_locked(
                text,
                ner_labels=ner_labels,
                include_negated=include_negated,
                include_uncertain=include_uncertain,
                include_historical=include_historical,
                include_family=include_family,
            )

    def _find_terms_locked(
        self,
        text: str,
        *,
        ner_labels: list[str] | None = None,
        include_negated: bool = False,
        include_uncertain: bool = False,
        include_historical: bool = False,
        include_family: bool = False,
    ) -> list[FilteredSpan]:
        spans = self._nlp.process(text, labels=ner_labels)
        return _filter_spans_by_status(
            spans,
            include_negated=include_negated,
            include_uncertain=include_uncertain,
            include_historical=include_historical,
            include_family=include_family,
        )

    def resolve_spans(
        self,
        spans: list[FilteredSpan],
        *,
        mode: str | None = None,
        result_types: str | list[str] | None = None,
        min_grade: str | None = None,
    ) -> list[ExtractedConcept]:
        """Resolve filtered spans to coded concepts via search.

        Thread-safe: serializes on the service lock (see __init__).

        See ``_resolve_spans_locked`` for parameters.
        """
        with self._lock:
            return self._resolve_spans_locked(
                spans, mode=mode, result_types=result_types, min_grade=min_grade
            )

    def _resolve_spans_locked(
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
        # EV05-EXPLORER-1: the canonical-mode default floor is 'probable'
        # (v0.0.3 doctrine — anchors grade exact/probable at best, so
        # 'certain' returns exact-only) and the operator env override
        # MEDTERM4DS_EXTRACTION_MIN_GRADE wins (QC-165), read at call
        # time. Legacy modes keep the service-configured self._min_grade.
        if search_mode == "canonical":
            default_threshold = _env_min_grade_override() or "probable"
        else:
            default_threshold = self._min_grade
        grade_threshold = min_grade or default_threshold
        # QC-153: validate result_types EAGERLY (raises ValueError for bogus
        # values, which the FHIR layer maps to a 400 per the service-
        # delegation pattern) and reduce to canonical_id prefixes for the
        # post-filtering below.
        result_type_prefixes = _result_types_to_prefixes(result_types)
        # QC-161: validate at the service boundary so wire surfaces that
        # forward min_grade unconstrained (MCP) get a clean ValueError.
        _validate_min_grade(grade_threshold)

        # --- Batch path for canonical mode ---
        # Embed ALL search texts in one SapBERT forward pass, then batch-search
        # FAISS. Turns N×100ms into ~100ms total for the embedding step.
        # Post-filters (source, grade) applied per-span after batch search.
        if search_mode == "canonical" and len(spans) > 1:
            # Prepare search texts (strip trailing values)
            # 2026-11-09: full mention (strength included) is the primary
            # query; the stripped form retries per-span on failure below.
            search_texts = [s.text.strip() or s.text for s in spans]

            # Batch search — returns list of result-lists, one per query
            batch_results = search.canonical_batch(search_texts, count=10)

            concepts: list[ExtractedConcept] = []
            part_cache: dict[tuple, list] = {}

            def _cached_part_search(part, part_rt, ss):
                rt_key = (
                    part_rt if isinstance(part_rt, str) or part_rt is None
                    else tuple(part_rt)
                )
                key = (part, rt_key, ss)
                if key not in part_cache:
                    part_cache[key] = search.canonical(
                        part, result_types=part_rt, sources=ss, count=5,
                    )
                return part_cache[key]

            for span, results in zip(spans, batch_results, strict=False):
                ss = _LABEL_TO_SOURCES.get(span.entity_type.lower())
                if span.composed_from:
                    concepts.extend(_resolve_composed_multi(
                        span,
                        lambda text, _results=results, _span_text=span.text: (
                            _results if text == _span_text
                            else search.canonical(_strip_trailing_values(text), count=10)
                        ),
                        ss,
                        result_type_prefixes=result_type_prefixes,
                        grade_threshold=grade_threshold,
                    ))
                    continue
                matched = _first_matching_concept(
                    span, results, ss,
                    result_type_prefixes=result_type_prefixes,
                    grade_threshold=grade_threshold,
                )
                if matched is not None:
                    concepts.append(matched)
                    continue
                # 2026-11-09: strength-stripped fallback, then qualifier
                # variant — only after the primary (full mention) failed.
                # AMUX-102 stage 3: the ladder is precomputed at NLP time
                # for pipeline spans (span.search_variants).
                for fallback_query in _span_fallback_queries(span):
                    if not fallback_query or fallback_query == span.text:
                        continue
                    matched = _first_matching_concept(
                        span, search.canonical(fallback_query, count=10), ss,
                        result_type_prefixes=result_type_prefixes,
                        grade_threshold=grade_threshold,
                    )
                    if matched is not None:
                        concepts.append(matched)
                        break
                if matched is not None:
                    continue

                # Conjunction split on failure (individual search for split parts).
                # This site splits the VALUE-STRIPPED text (pre-migration
                # semantics). The value-stripped base is computed with the
                # legacy mention helper on this cold path — NOT from
                # search_variants[0], which is the value-stripped variant
                # only when the value-strip fired; when the strip is a no-op
                # the first ladder entry is the form/qualifier variant
                # (EV02-HISTORIAN-1: 'history of CBC and CMP' lost its
                # qualifier part). The precomputed conjunction_parts (full
                # token slice) are consulted when the strip is a no-op.
                search_text = _strip_trailing_values(span.text)
                if span.conjunction_parts is not None and search_text == span.text:
                    parts = span.conjunction_parts
                else:
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
                            part_results = _cached_part_search(part, part_rt, ss)
                            for r in part_results:
                                # EV01-QA-05 fix: the qualifier-inversion guard
                                # must gate conjunction-split parts too — a
                                # 'with differential' part must never resolve
                                # to the WITHOUT-qualifier anchor (10th
                                # helper-not-wired occurrence).
                                if _qualifier_inverted(part, r.display):
                                    continue
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

            return _dedup_concepts(concepts)

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

        # Composition guard (2026-11-07 port, mode-agnostic): composed spans
        # resolve under completeness/type/grade rules in EVERY mode — the
        # shipped default (hybrid) had the inverted 'CBC' answer because this
        # guard was canonical-only (review blocker 2). Non-composed spans keep
        # the pre-existing per-span path untouched.
        if any(span.composed_from for span in spans):
            for span in spans:
                if not span.composed_from:
                    continue
                ss = _LABEL_TO_SOURCES.get(span.entity_type.lower())
                ss_key = ",".join(ss) if ss else ""
                rt_probe = (
                    result_types if result_types is not None
                    else _span_search_categories(span)
                )

                def _fetch(text, _ss=ss, _ss_key=ss_key, _rt=rt_probe,
                           _span=span, _mode=search_mode):
                    # CR-302 followup (measured live on the demo Space):
                    # the COMPOSED query resolves against the canonical
                    # anchor space even in non-canonical modes —
                    # combination_members (and the alias expansions
                    # fragment-completeness needs) exist only on canonical
                    # results; mode-search results made every combination
                    # fail completeness ('lisinopril/HCTZ' -> single
                    # Lisinopril, HCTZ dropped). Sibling fragments keep
                    # the mode search below.
                    if text == _span.text and _mode != "canonical":
                        canon = search.canonical(text, count=10)
                        if canon:
                            return canon
                    probe = _strip_trailing_values(text)
                    attempts = [_rt, None] if result_types is None else [result_types]
                    for attempt in attempts:
                        found = _search_span_texts(probe, _ss_key, _ss, attempt)
                        if found:
                            return found
                    return []

                concepts.extend(_resolve_composed_multi(
                    span, _fetch, ss,
                    result_type_prefixes=result_type_prefixes,
                    grade_threshold=grade_threshold,
                ))

        for span in spans:
            if span.composed_from:
                continue  # resolved by the composed pre-pass above
            # 2026-11-09 design: the FULL mention text (strength included)
            # is the primary query in every mode — lexical product rows and
            # canonical/semantic strength aliases key on it; the stripped
            # form is a fallback, as is the leading-qualifier variant.
            search_texts = [span.text.strip()]
            # AMUX-102 stage 3: fallback ladder precomputed at NLP time
            # for pipeline spans (span.search_variants) — value-stripped,
            # form-noun, leading-qualifier variants in resolve order.
            for variant in _span_fallback_queries(span):
                if variant and variant not in search_texts:
                    search_texts.append(variant)
            search_text = search_texts[0]
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

            # search_texts already carries [full, stripped, qualifier
            # variant] (2026-11-09 strength-inclusive design + CR-301).

            resolved = False
            for candidate_text in search_texts:
                if resolved:
                    break
                for rt in rt_attempts:
                    if resolved:
                        break
                    # EV01-QA-06 fix (2026-11-11 evolution): route the
                    # candidate selection through _first_matching_concept so
                    # the cross-type tie rule, ConText lab override,
                    # qualifier-inversion guard and R4 abstention flag apply
                    # to single spans exactly as they do in the batch path
                    # (the prior inline grade-only selection skipped them).
                    # The rt-constrained query contract is preserved — the
                    # constrain-then-fallback semantics now run client-side
                    # on each attempt's result list.
                    matched = _first_matching_concept(
                        span, _search_span_texts(candidate_text, ss_key, ss, rt), ss,
                        result_type_prefixes=result_type_prefixes,
                        grade_threshold=grade_threshold,
                    )
                    if matched is not None:
                        concepts.append(matched)
                        resolved = True
                        break
                if resolved:
                    break

            if not resolved:
                # This site splits the FULL mention text — the precomputed
                # conjunction_parts (full token slice) apply directly;
                # manual spans fall back to the mention-text helper.
                parts = (
                    span.conjunction_parts
                    if span.conjunction_parts is not None
                    else _split_on_conjunction(search_text)
                )
                if parts:
                    for part in parts:
                        part_resolved = False
                        for part_rt in rt_attempts:
                            part_results = _search_span_texts(part, ss_key, ss, part_rt)
                            for r in part_results:
                                # EV01-QA-05 fix (legacy path): qualifier-
                                # inversion guard on conjunction-split parts.
                                if _qualifier_inverted(part, r.display):
                                    continue
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

        # CR-063: shared with the batch path — a local copy here forked
        # silently if the QC-183 status-inclusive key ever changed.
        return _dedup_concepts(concepts)

    def _resolve_spans_batch_locked(
        self,
        spans_lists: list[list[FilteredSpan]],
        *,
        mode: str | None = None,
        result_types: str | list[str] | None = None,
        min_grade: str | None = None,
    ) -> list[list[ExtractedConcept]]:
        """Pooled canonical resolution for batch extract().

        Deduplicates search texts across EVERY input before embedding —
        drug-label corpora repeat entity texts heavily, so the corpus-wide
        unique set is far smaller than the total span count — then runs
        one canonical_batch and applies the same per-span post-filtering
        (including the conjunction fallback) as _resolve_spans_locked.
        Per-text output is identical. Non-canonical modes fall back to
        per-text resolution.
        """
        search_mode = mode or self._search_mode
        if search_mode != "canonical":
            return [
                self._resolve_spans_locked(
                    s, mode=mode, result_types=result_types, min_grade=min_grade,
                )
                for s in spans_lists
            ]

        from medterm4ds.services.search import (
            _result_types_to_prefixes,
            get_search_service,
        )
        search = get_search_service()
        grade_threshold = min_grade or "probable"
        result_type_prefixes = _result_types_to_prefixes(result_types)
        _validate_min_grade(grade_threshold)

        # Global dedup: each unique entity text is embedded exactly once
        # for the whole batch.
        unique_texts: dict[str, int] = {}
        per_span_texts: list[list[str]] = []
        for spans in spans_lists:
            per_span_texts.append([])
            for span in spans:
                t = (span.text.strip() or span.text)
                per_span_texts[-1].append(t)
                if t not in unique_texts:
                    unique_texts[t] = len(unique_texts)

        results_by_text: dict[str, list] = {}
        if unique_texts:
            batch = search.canonical_batch(list(unique_texts), count=10)
            for t, i in unique_texts.items():
                results_by_text[t] = batch[i]

        # Conjunction-part searches memoized across the WHOLE batch: failed
        # parts repeat heavily across drug labels (combo products share
        # ingredients), and each uncached search.canonical call pays a full
        # embed + FAISS + possible ancestor-fallback hierarchy walk. Exact
        # same per-part semantics — first occurrence computes, repeats hit
        # the cache.
        part_cache: dict[tuple, list] = {}

        def _cached_part_search(part, part_rt, ss):
            rt_key = (
                part_rt if isinstance(part_rt, str) or part_rt is None
                else tuple(part_rt)
            )
            key = (part, rt_key, ss)
            if key not in part_cache:
                part_cache[key] = search.canonical(
                    part, result_types=part_rt, sources=ss, count=5,
                )
            return part_cache[key]

        out: list[list[ExtractedConcept]] = []
        for spans, texts in zip(spans_lists, per_span_texts, strict=False):
            concepts: list[ExtractedConcept] = []
            for span, t in zip(spans, texts, strict=False):
                ss = _LABEL_TO_SOURCES.get(span.entity_type.lower())
                if span.composed_from:
                    concepts.extend(_resolve_composed_multi(
                        span,
                        lambda text, _by_text=results_by_text: _by_text.get(
                            _strip_trailing_values(text),
                            search.canonical(_strip_trailing_values(text), count=10),
                        ),
                        ss,
                        result_type_prefixes=result_type_prefixes,
                        grade_threshold=grade_threshold,
                    ))
                    continue
                matched = _first_matching_concept(
                    span, results_by_text.get(t, []), ss,
                    result_type_prefixes=result_type_prefixes,
                    grade_threshold=grade_threshold,
                )
                if matched is not None:
                    concepts.append(matched)
                    continue
                # 2026-11-09: strength-stripped fallback, then qualifier
                # variant — only after the primary (full mention) failed.
                # AMUX-102 stage 3: the ladder is precomputed at NLP time
                # for pipeline spans (span.search_variants).
                for fallback_query in _span_fallback_queries(span):
                    if not fallback_query or fallback_query == t:
                        continue
                    matched = _first_matching_concept(
                        span, search.canonical(fallback_query, count=10), ss,
                        result_type_prefixes=result_type_prefixes,
                        grade_threshold=grade_threshold,
                    )
                    if matched is not None:
                        concepts.append(matched)
                        break
                if matched is not None:
                    continue
                # Full-mention conjunction split — the precomputed
                # conjunction_parts (full token slice) apply directly;
                # manual spans fall back to the mention-text helper.
                parts = (
                    span.conjunction_parts
                    if span.conjunction_parts is not None
                    else _split_on_conjunction(t)
                )
                if parts:
                    rt_attempts: list[str | list[str] | None]
                    if result_types is not None:
                        rt_attempts = [result_types]
                    else:
                        rt = _span_search_categories(span)
                        rt_attempts = [rt, None] if rt is not None else [None]
                    for part in parts:
                        part_resolved = False
                        for part_rt in rt_attempts:
                            part_results = _cached_part_search(part, part_rt, ss)
                            for r in part_results:
                                # EV01-QA-05 fix: the qualifier-inversion guard
                                # must gate conjunction-split parts too — a
                                # 'with differential' part must never resolve
                                # to the WITHOUT-qualifier anchor (10th
                                # helper-not-wired occurrence).
                                if _qualifier_inverted(part, r.display):
                                    continue
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
            out.append(_dedup_concepts(concepts))
        return out

    def extract(
        self,
        text: str | list[str],
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
        annotation_fields: str | list[str] | None = None,
    ) -> list[FilteredSpan] | list[ExtractedConcept] | dict[str, Any] | list[Any]:
        """Extract medical concepts from free text.

        Parameters
        ----------
        text : str | list[str]
            A single text, or a list of texts. A list returns one result
            per text, in input order — each element has exactly the shape
            a single-text call returns for the chosen ``format``. Batch
            inputs are processed under a single lock acquisition (no
            per-text interleaving with other threads); an empty list
            returns ``[]``.
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
        annotation_fields : str | list[str] | None
            Fields rendered in each ``annotated`` inline marker (``format``
            must be ``"annotated"``; ignored otherwise). A comma-separated
            string or a list. One of:

            - ``"text"``: matched span text (always available)
            - ``"name"``: resolved preferred display name
            - ``"type"``: corrected entity label (from result_type; raw NER
              label when unresolved)
            - ``"source_code"``: vocabulary code as ``SOURCE:code`` (e.g.
              ``RXNORM:6809``)
            - ``"canonical_id"``: internal canonical anchor id
            - ``"status"``: ConText status (affirmed, negated, ...)

            Unavailable fields (e.g. codes for unresolved spans) render as
            ``UNKNOWN`` so field positions stay stable across markers.
            Default ``["text", "type"]`` reproduces the historical
            ``[entity|label]`` marker exactly.
        """
        # QA-004 (QC-163 parity): fail on garbage BEFORE the NER/resolve
        # burn — validation used to happen deep inside the resolve paths,
        # so a bogus argument paid the full extraction cost (measured 2.3s
        # on a 2.5K-char text) before raising. Only validates what the
        # chosen format will actually consume, so ignored-for-this-format
        # arguments keep their lenient pass-through semantics.
        # QC07-001: ``format`` itself is validated FIRST — an unknown value
        # used to fall through the == "annotated"/== "terms" branches and
        # silently return codes-shaped results (CLI/FHIR reject the same
        # input; the Python surface was the lenient one).
        if format not in ("codes", "terms", "annotated"):
            raise ValueError(
                f"Unknown format: {format!r}. "
                "Valid: codes, terms, annotated."
            )
        if format == "annotated":
            _normalize_annotation_fields(annotation_fields)
        if format != "terms":
            from medterm4ds.services.search import _result_types_to_prefixes
            _result_types_to_prefixes(result_types)
            if min_grade is not None:
                _validate_min_grade(min_grade)

        if isinstance(text, (list, tuple)):
            texts = list(text)
            for i, t in enumerate(texts):
                if not isinstance(t, str):
                    raise ValueError(
                        f"extract() list inputs must contain only strings "
                        f"(index {i} is {type(t).__name__})"
                    )
            # One lock acquisition for the whole batch: batch inputs stay
            # on one lane instead of interleaving with other threads per
            # text. Both heavy stages are pooled cross-text: NLP (one GLiNER
            # inference over all sentences) and canonical resolution (one
            # canonical_batch over deduped entity texts from every text).
            with self._lock:
                all_spans = self._nlp.process_batch(texts, ner_labels)
                if format == "terms":
                    return [
                        _filter_spans_by_status(
                            all_spans[i],
                            include_negated=include_negated,
                            include_uncertain=include_uncertain,
                            include_historical=include_historical,
                            include_family=include_family,
                        )
                        for i in range(len(texts))
                    ]
                if format == "annotated":
                    concepts_lists = self._resolve_spans_batch_locked(
                        all_spans, mode=mode,
                        result_types=result_types, min_grade=min_grade,
                    )
                    return [
                        self._build_annotated(
                            texts[i], all_spans[i], concepts_lists[i],
                            annotation_fields,
                        )
                        for i in range(len(texts))
                    ]
                # codes: filter statuses, then pooled resolution
                filtered_lists = [
                    _filter_spans_by_status(
                        all_spans[i],
                        include_negated=include_negated,
                        include_uncertain=include_uncertain,
                        include_historical=include_historical,
                        include_family=include_family,
                    )
                    for i in range(len(texts))
                ]
                return self._resolve_spans_batch_locked(
                    filtered_lists, mode=mode,
                    result_types=result_types, min_grade=min_grade,
                )

        if format == "annotated":
            return self._extract_annotated(
                text,
                ner_labels=ner_labels,
                result_types=result_types,
                mode=mode,
                min_grade=min_grade,
                annotation_fields=annotation_fields,
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
        annotation_fields: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """Extract concepts + return inline entity annotations.

        Returns a dict with:
        - ``concepts``: list[ExtractedConcept] (resolved codes)
        - ``annotated_text``: str with inline markers built from
          ``annotation_fields`` (default ``[text|type]``)
        - ``spans``: list of span metadata dicts

        All spans are included regardless of ConText status (negated,
        uncertain, historical). Each span's ``status`` field lets callers
        filter downstream.
        """
        # CR-062: every public entry point must serialize on the service
        # lock. The single-text annotated path used to reach the _locked
        # helpers without holding it (v0.0.2 called the public find_terms/
        # resolve_spans here, which lock) — unsynchronized medspaCy/GLiNER
        # access under direct multi-threaded use. RLock: no re-entrancy
        # concern with the _locked calls below.
        with self._lock:
            all_spans = self._find_terms_locked(
                text,
                ner_labels=ner_labels,
                include_negated=True,
                include_uncertain=True,
                include_historical=True,
                include_family=True,
            )
            return self._annotated_from_spans(
                text, all_spans,
                result_types=result_types, mode=mode, min_grade=min_grade,
                annotation_fields=annotation_fields,
            )

    def _annotated_from_spans(
        self,
        text: str,
        all_spans: list[FilteredSpan],
        *,
        result_types: str | list[str] | None = None,
        mode: str | None = None,
        min_grade: str | None = None,
        annotation_fields: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve spans and assemble the annotated-format output (single text).

        Assumes the caller holds the service lock.
        """
        concepts = self._resolve_spans_locked(
            all_spans, mode=mode, result_types=result_types, min_grade=min_grade,
        )
        return self._build_annotated(text, all_spans, concepts, annotation_fields)

    def _build_annotated(
        self,
        text: str,
        all_spans: list[FilteredSpan],
        concepts: list[ExtractedConcept],
        annotation_fields: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """Assemble annotated output from precomputed spans AND concepts.

        Resolution is done by the caller so the batch path can pool it
        across texts (see _resolve_spans_batch_locked).
        """
        fields = _normalize_annotation_fields(annotation_fields)

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

            # Look up resolved concept (may also match conjunction-split parts).
            # The lookup base is the VALUE-STRIPPED text via the legacy
            # mention helper — NOT search_variants[0] (EV02-HISTORIAN-1
            # sibling: when the value-strip is a no-op the first ladder
            # entry is the form/qualifier variant, and a concept keyed by
            # that variant (e.g. resolved for a different span) would leak
            # into this span's marker).
            concept = concept_by_text.get(span.text.lower()) or concept_by_text.get(
                _strip_trailing_values(span.text).lower()
            )

            # Determine the annotation label: prefer result_type (corrected),
            # fall back to ner_label if unresolved.
            if concept and concept.result_type:
                label = _RESULT_TYPE_TO_LABEL.get(concept.result_type, span.entity_type)
            else:
                label = span.entity_type

            result_parts.append("[" + "|".join(
                _annotation_marker_values(
                    fields, entity_text=entity_text, label=label,
                    span=span, concept=concept,
                )
            ) + "]")
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
                "source": concept.source if concept else "",
                "code": concept.code if concept else "",
                "match_grade": concept.match_grade if concept else "",
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


def extract(
    text: str | list[str], *, format: str = "codes", **kwargs
) -> list[FilteredSpan] | list[ExtractedConcept] | dict[str, Any] | list[Any]:
    """Extract medical concepts from free text.

    Accepts a single text or a list of texts (one result per text, in
    input order). See ``ExtractionService.extract`` for parameters.
    """
    return get_extraction_service().extract(text, format=format, **kwargs)
