"""spaCy-native cue/guard component (AMUX-102 stages 1-2).

Stage 1 replaced the raw-text regex pre/post-span cue checks and the
clause/pediatric guards; stage 2 added the inline-trigger strip
(``Mt4dsCueAnnotator.inline_trigger``, design F3), the token-based
stitching/compose gap classifiers (design F5), and the relocated
mention-text validators — false-positive stoplists (F7), unit validators
(F9) and the assertion/fused-value fragment regexes. Cues are matched
with a spaCy ``Matcher`` over token LOWER patterns at NLP time
(``Mt4dsCueAnnotator.__call__``); per-entity decisions then reduce to
token-indexed annotation lookups — the ConText tie-rule pattern.

Parity notes (the 244-test battery in tests/test_extraction.py is the
spec; behavior must match the deleted regexes exactly):
- Case-insensitivity comes from matching on token LOWER (the regexes
  used re.IGNORECASE on raw text).
- The regexes' ``$``-anchored adjacency (cue ends at the span, allowing
  'a|an|the' and, for the historical class, 'hx of' in between) is
  replicated with token adjacency checks.
- The regexes' ``^``-anchored post-span starts (whitespace-only gap
  plus at most one opening bracket/quote token) are replicated by
  anchoring at the first token at/after the span end; a comma or
  semicolon token there blocks binding.
- The 48/32-char windows stay CHAR-based (computed from token.idx),
  as do the sentence bounds (doc.sents with the same try/except
  fallback to whole-doc windows the old helpers had).
- Multi-variant cues: 'can't' may tokenize as one token or two, and
  'cannot' as 'can' + 'not' (PyRuSH) — every variant gets a pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # spacy stays a lazy, extraction-extra-only import (see
    from spacy.matcher import Matcher  # _register_doc_extensions below)
    from spacy.tokens import Doc

# F6 — trailing-value run vocabulary (token equivalents of the legacy
# _TRAILING_VALUE_RE; see Mt4dsCueAnnotator.strip_trailing_values).
# Numbers are digit tokens optionally joined by UNSPACED '.'/'/' tokens
# ('20', '12.5', '20/12.5'); the unit suffix is an UNSPACED chain of
# [%°a-zA-Z/]-shaped tokens ('7.2%', '500mg', '100mg/dL') — QC-188: a
# SPACED unit ('7.2 %') never strips, exactly like the regex.
_NUMBER_TOKEN_RE = re.compile(r"\d+(?:[./]\d+)*")
_NUMBER_WITH_UNIT_RE = re.compile(r"\d+(?:[./]\d+)*(?:[%°a-zA-Z/]+)?")
_UNIT_ONLY_RE = re.compile(r"[%°a-zA-Z/]+")
# Comparator words — exact-case (the legacy regex had NO IGNORECASE, so
# 'glucose HIGH' never stripped and must keep not stripping).
_COMPARATOR_WORDS = frozenset({"less", "greater", "high", "low", "normal", "abnormal"})

# F6 — trailing form nouns (legacy _TRAILING_FORM_NOUN_RE, IGNORECASE).
_FORM_NOUNS = frozenset({
    "bolus", "drip", "push", "infusion", "injection", "tablet", "tablets",
    "capsule", "capsules", "solution", "suspension",
})

# F6 — leading qualifier vocabulary (legacy _LEADING_QUALIFIER_RE).
_LEADING_QUALIFIER_SINGLES = frozenset({
    "left", "right", "bilateral", "unilateral", "upper", "lower",
})
_LEADING_QUALIFIER_PHRASES = frozenset({
    ("history", "of"), ("hx", "of"), ("status", "post"),
})

# F8 — conjunction-split numeric-parts guard (legacy E:614 rule: a pure
# number is a value, not a standalone query).
_PURE_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# Registered lazily (component may be constructed per pipeline). The
# spacy import must stay OUT of module scope: this module's constants are
# re-exported by services/extraction.py, which apps/cli.py (build_parser)
# and apps/fhir_api.py import at module level — a module-level spacy
# import here crashed the base install's `medterm4ds --version` and the
# `[fhir]`-only install's create_fhir_app (EV09-EXPLORER-1). spacy is an
# extraction-extra dependency (via medspacy), not a base dependency.
def _register_doc_extensions() -> None:
    from spacy.tokens import Doc

    if not Doc.has_extension("mt4ds_cues"):
        Doc.set_extension("mt4ds_cues", default=None)

# Cue class labels (match the strings the old helpers returned).
PSEUDO_NEGATION = "pseudo_negation"
UNCERTAINTY = "uncertainty"
NEGATION = "negation"
HISTORICAL = "historical"

# How far before the span a pre-span cue may sit (chars, same-sentence).
_PRE_SPAN_WINDOW = 48
# How far past the span end a post-span cue may sit and still bind.
_POST_SPAN_CUE_WINDOW = 32

# Opening bracket/quote tokens allowed between a span end and a
# parenthetical post-span cue ('Sepsis (possible)').
_POST_SPAN_OPENERS = ("(", "[", '"', "\u201c")

# Article tokens allowed between a pre-span cue and the span.
_ARTICLES = frozenset({"a", "an", "the"})


@dataclass
class CueAnnotations:
    """Token-indexed cue annotations for one doc (doc._.mt4ds_cues)."""

    # (class, start_token, end_token_exclusive) per pre-span cue match.
    pre_cues: list[tuple[str, int, int]] = field(default_factory=list)
    # token index where a pre-span cue phrase ends -> class (design F1).
    pre_cue_ends: dict[int, str] = field(default_factory=dict)
    # token index where a post-span cue phrase STARTS -> (class, end).
    post_cue_starts: dict[int, list[tuple[str, int]]] = field(default_factory=dict)
    # Token indices of strong clause boundary tokens (';' ':' '—' 'but').
    clause_boundary_tokens: set[int] = field(default_factory=set)
    # Token indices witnessing negation support inside their clause.
    negation_support_tokens: set[int] = field(default_factory=set)
    # Token indices of pediatric descriptors governing 'with'.
    pediatric_subject_tokens: set[int] = field(default_factory=set)


# 'can't' / 'cannot' tokenize differently per tokenizer — include every
# variant. Each entry is a LOWER-token sequence.
_CANT = [["can't"], ["ca", "n't"]]
_CANNOT = [["cannot"], ["can", "not"]]
_COULD_NOT = [["could", "not"]]
_UNABLE_TO = [["unable", "to"]]
_NOT_ABLE_TO = [["not", "able", "to"]]
_PSEUDO_PREFIXES = _CANT + _CANNOT + _COULD_NOT + _UNABLE_TO + _NOT_ABLE_TO
_PSEUDO_CORES = [["rule", "out"], ["exclude"], ["be", "ruled", "out"], ["be", "excluded"]]

_PRE_UNCERTAINTY = [
    ["suspected"], ["suspect"], ["possibly"], ["possible"],
    ["concerning", "for"], ["likely"], ["question", "of"],
    *([p + ["rule", "out"] for p in _CANT + _CANNOT]),
    *([p + ["exclude"] for p in _CANNOT]),
    ["unable", "to", "exclude"],
]
_PRE_NEGATION = [
    ["neither"], ["nor"], ["denies"], ["denied"],
    ["negative", "for"], ["free", "of"],
]
_PRE_HISTORICAL = [
    ["history", "of"], ["hx", "of"], ["hx"], ["prior"], ["previous"], ["past"],
]

# Post-span negation: optional prefix verb phrase (at most ONE, matching
# the regex alternation), then a cue core; or prefix + bare 'negative'.
_POST_PREFIXES = [["was"], ["is"], ["are"], ["has", "been"], ["have", "been"], ["now"]]
_POST_NEG_CORES = [
    ["ruled", "out"], ["rules", "out"], ["excluded"], ["not", "seen"],
    ["not", "appreciated"], ["negative", "for"], ["free", "of"],
    ["denied"], ["denies"],
]
_POST_UNCERTAINTY = [
    ["possible"], ["possibly"], ["suspected"], ["suspect"], ["likely"],
    ["concerning", "for"],
    *([p + ["rule", "out"] for p in _CANT + _CANNOT]),
    *([p + ["exclude"] for p in _CANNOT]),
]

# F4 — clause guard vocabulary.
_CLAUSE_BOUNDARY_TEXTS = frozenset({";", ":", "\u2014"})
_CLAUSE_SUPPORT_SINGLES = frozenset({
    "no", "not", "denies", "denied", "deny", "negative", "without",
    "absent", "free", "neither", "nor",
})
_CLAUSE_SUPPORT_BIGRAMS = [("ruled", "out"), ("rules", "out"), ("negative", "for")]
_PEDIATRIC_DESCRIPTORS = frozenset({
    "child", "infant", "neonate", "newborn", "toddler",
    "adolescent", "teenager",
})


def _phrase_patterns(phrases: list[list[str]]) -> list[list[dict]]:
    return [[{"LOWER": w} for w in phrase] for phrase in phrases]


def _optional_openers() -> list[list[dict]]:
    return [[], [{"TEXT": {"IN": list(_POST_SPAN_OPENERS)}}]]


class Mt4dsCueAnnotator:
    """spaCy cue/guard component (AMUX-102).

    Not added via ``nlp.add_pipe`` (GLiNER spans exist only after
    per-sentence GLiNER runs): NlpPipeline instantiates it once at
    ``_ensure_loaded`` and calls it on each doc in ``_finalize_doc``
    right after the ConText re-run — the established medspaCy pattern
    in this codebase.
    """

    def __init__(self, nlp):
        self._nlp = nlp
        self._matcher = None
        self._pattern_count = 0

    def _ensure_matcher(self) -> Matcher:
        """Build the Matcher on first use (the pipeline object may be a
        stand-in while NlpPipeline._ensure_loaded is still mocking loads)."""
        from spacy.matcher import Matcher

        if self._matcher is None:
            self._matcher = Matcher(self._nlp.vocab)
            self._add_patterns()
        return self._matcher

    # ------------------------------------------------------------------
    # Pattern construction

    def _add(self, label: str, patterns: list[list[dict]]) -> None:
        self._matcher.add(label, patterns)
        self._pattern_count += len(patterns)

    def _add_patterns(self) -> None:
        # F1 — pre-span cues (matched anywhere; adjacency is checked at
        # decision time because the allowed interstitials differ per
        # class, exactly as the $-anchored regexes did).
        self._add("PRE_PSEUDONEGATION", _phrase_patterns(
            [p + c for p in _PSEUDO_PREFIXES for c in _PSEUDO_CORES]))
        self._add("PRE_UNCERTAINTY", _phrase_patterns(_PRE_UNCERTAINTY))
        self._add("PRE_NEGATION", _phrase_patterns(_PRE_NEGATION))
        self._add("PRE_HISTORICAL", _phrase_patterns(_PRE_HISTORICAL))
        # F2 — post-span cues. The optional opener/prefix tokens are
        # part of the pattern so a match's START token is the anchor
        # the decision methods check against the span end.
        opener = _optional_openers()
        post_neg: list[list[dict]] = []
        for o in opener:
            for prefix in [[]] + _POST_PREFIXES:
                for core in _POST_NEG_CORES:
                    post_neg.append(o + _phrase_patterns([prefix + core])[0])
            # Bare 'negative' predicate requires the explicit
            # copula/'now' prefix (unprefixed 'HIV negative.' stays
            # affirmed; the 'now'-before-span rule is separate below).
            for prefix in _POST_PREFIXES:
                post_neg.append(o + _phrase_patterns([prefix + ["negative"]])[0])
        self._add("POST_NEGATION", post_neg)
        post_unc: list[list[dict]] = []
        for o in opener:
            for phrase in _POST_UNCERTAINTY:
                post_unc.append(o + _phrase_patterns([phrase])[0])
        self._add("POST_UNCERTAINTY", post_unc)

    # ------------------------------------------------------------------
    # Annotation

    def __call__(self, doc: Doc) -> Doc:
        _register_doc_extensions()
        cues = CueAnnotations()
        matcher = self._ensure_matcher()
        for match_id, start, end in matcher(doc):
            label = self._nlp.vocab.strings[match_id]
            if label.startswith("PRE_"):
                cls = {
                    "PRE_PSEUDONEGATION": PSEUDO_NEGATION,
                    "PRE_UNCERTAINTY": UNCERTAINTY,
                    "PRE_NEGATION": NEGATION,
                    "PRE_HISTORICAL": HISTORICAL,
                }[label]
                cues.pre_cues.append((cls, start, end))
                # Same end token may carry several classes (e.g. 'cannot
                # rule out' is both pseudo-negation and uncertainty) —
                # keep the highest-precedence class for the dict view.
                prev = cues.pre_cue_ends.get(end)
                if prev is None or _CLASS_ORDER.index(cls) < _CLASS_ORDER.index(prev):
                    cues.pre_cue_ends[end] = cls
            elif label in ("POST_NEGATION", "POST_UNCERTAINTY"):
                cls = NEGATION if label == "POST_NEGATION" else UNCERTAINTY
                cues.post_cue_starts.setdefault(start, []).append((cls, end))
        self._annotate_guard_tokens(doc, cues)
        doc._.mt4ds_cues = cues
        return doc

    @staticmethod
    def _annotate_guard_tokens(doc: Doc, cues: CueAnnotations) -> None:
        tokens = list(doc)
        for i, tok in enumerate(tokens):
            if tok.text in _CLAUSE_BOUNDARY_TEXTS or tok.lower_ == "but":
                cues.clause_boundary_tokens.add(i)
            low = tok.lower_
            if low in _CLAUSE_SUPPORT_SINGLES or low.startswith("non-"):
                cues.negation_support_tokens.add(i)
            elif i + 1 < len(tokens):
                nxt = tokens[i + 1].lower_
                if any(
                    low == a and nxt == b for a, b in _CLAUSE_SUPPORT_BIGRAMS
                ):
                    cues.negation_support_tokens.add(i)
                if low in _PEDIATRIC_DESCRIPTORS and nxt == "with":
                    cues.pediatric_subject_tokens.add(i)

    def _cues_for(self, doc: Doc) -> CueAnnotations:
        _register_doc_extensions()
        if doc._.mt4ds_cues is None:
            self(doc)
        return doc._.mt4ds_cues

    # ------------------------------------------------------------------
    # Sentence bounds (same try/except fallback the old helpers had)

    @staticmethod
    def _sentence_bounds(doc: Doc, char: int, *, want_start: bool):
        try:
            for sent in doc.sents:
                if sent.start_char <= char < sent.end_char:
                    return sent.start_char if want_start else sent.end_char
        except Exception:  # noqa: BLE001 — no sentence iterator → window only
            pass
        return None

    # ------------------------------------------------------------------
    # F1 — pre-span cues

    def pre_span_cue(self, doc: Doc, span_start_char: int) -> str | None:
        """Cue immediately before a span, in its sentence.

        Returns 'pseudo_negation' | 'uncertainty' | 'negation' |
        'historical' | None. Class precedence mirrors the sequential
        regex checks; a cue from a previous clause never binds.
        """
        cues = self._cues_for(doc)
        sent_start = self._sentence_bounds(doc, span_start_char, want_start=True)
        begin = max(span_start_char - _PRE_SPAN_WINDOW, sent_start or 0)
        tokens = list(doc)
        best: str | None = None
        for cls, start, end in cues.pre_cues:
            if doc[start].idx < begin:
                continue  # cue starts outside the window/sentence head
            if tokens[end - 1].idx + len(tokens[end - 1]) > span_start_char:
                continue  # cue overlaps the span
            if not self._pre_cue_adjacent(
                tokens, end, span_start_char, cls
            ):
                continue
            if best is None or _CLASS_ORDER.index(cls) < _CLASS_ORDER.index(best):
                best = cls
        return best

    @staticmethod
    def _pre_cue_adjacent(tokens, end: int, span_start: int, cls: str) -> bool:
        """Tokens between cue end and span start — the $-anchor rules."""
        # Whitespace-only tokens (\s*$ in the old regexes) never break
        # adjacency — PyRuSH emits them between sentences/lines.
        inter = [
            t for t in tokens[end:]
            if t.idx < span_start and not t.is_space
        ]
        if cls in (PSEUDO_NEGATION, NEGATION):
            return not inter
        if cls == UNCERTAINTY:
            # (?:a|an|the)? — at most one article
            return len(inter) <= 1 and all(t.lower_ in _ARTICLES for t in inter)
        # historical: (?:hx of)? (?:a|an|the)?
        n = len(inter)
        if n > 3:
            return False
        i = 0
        if n >= 2 and inter[0].lower_ == "hx" and inter[1].lower_ == "of":
            i = 2
        return all(t.lower_ in _ARTICLES for t in inter[i:])

    # ------------------------------------------------------------------
    # F2 — post-span cues

    def _first_token_at_or_after(self, doc: Doc, char: int):
        # Whitespace-only tokens ('\s*' in the old regexes) are skipped:
        # the anchor is the first CONTENT token at/after the span end.
        for tok in doc:
            if tok.idx >= char and not tok.is_space:
                return tok
        return None

    def _post_limit(self, doc: Doc, span_end: int) -> int:
        sent_end = self._sentence_bounds(doc, span_end, want_start=False)
        return min(span_end + _POST_SPAN_CUE_WINDOW, sent_end or len(doc.text))

    def _post_cue_at_anchor(
        self, doc: Doc, anchor, cls: str, span_end: int
    ) -> bool:
        cues = self._cues_for(doc)
        limit = self._post_limit(doc, span_end)
        for hit_cls, end in cues.post_cue_starts.get(anchor.i, []):
            if hit_cls != cls:
                continue
            end_tok = doc[end - 1]
            # The whole cue phrase must fit inside the sentence window
            # (the regex matched a tail truncated at the same limit).
            if end_tok.idx + len(end_tok) <= limit:
                return True
        return False

    def post_span_negation_cue(
        self, doc: Doc, span_end_char: int, span_start_char: int | None = None
    ) -> bool:
        """True when a negation cue sits just after the span, in its
        sentence, across a whitespace-only gap (optional opening
        bracket/quote). A ',' or ';' between span and cue blocks.
        """
        anchor = self._first_token_at_or_after(doc, span_end_char)
        if anchor is None:
            return False
        if self._post_cue_at_anchor(doc, anchor, NEGATION, span_end_char):
            return True
        # Bare 'negative' predicate binds ONLY when the span is
        # directly preceded by 'now' ('now COVID-19 negative'); an
        # unmarked 'HIV negative.' stays affirmed (q-hiv-02).
        if span_start_char is None or anchor.lower_ != "negative":
            return False
        limit = self._post_limit(doc, span_end_char)
        if anchor.idx + len(anchor) > limit:
            return False
        head = [t for t in doc if t.idx + len(t) <= span_start_char and not t.is_space]
        return bool(head) and head[-1].lower_ == "now"

    def post_span_uncertainty_cue(self, doc: Doc, span_end_char: int) -> bool:
        """True when an uncertainty cue sits just after the span
        ('X (possible)')."""
        anchor = self._first_token_at_or_after(doc, span_end_char)
        if anchor is None:
            return False
        return self._post_cue_at_anchor(doc, anchor, UNCERTAINTY, span_end_char)

    # ------------------------------------------------------------------
    # F4 — clause guard + pediatric subject

    def _clause_boundary_char(self, doc: Doc, span_start: int) -> int:
        cues = self._cues_for(doc)
        boundary = 0
        for i in sorted(cues.clause_boundary_tokens):
            tok = doc[i]
            if tok.idx + len(tok) <= span_start:
                boundary = max(boundary, tok.idx + len(tok))
        return boundary

    def clause_negation_supported(
        self, doc: Doc, span_start_char: int, span_end_char: int
    ) -> bool:
        """True when the span's own clause (strong boundaries only —
        ',' is deliberately NOT one) carries a negation cue."""
        cues = self._cues_for(doc)
        boundary = self._clause_boundary_char(doc, span_start_char)
        for i in cues.negation_support_tokens:
            tok = doc[i]
            if tok.idx < boundary or tok.idx + len(tok) > span_end_char:
                continue
            low = tok.lower_
            if low in _CLAUSE_SUPPORT_SINGLES or low.startswith("non-"):
                return True
            nxt = doc[i + 1] if i + 1 < len(doc) else None
            if nxt is not None and any(
                low == a and nxt.lower_ == b for a, b in _CLAUSE_SUPPORT_BIGRAMS
            ):
                if nxt.idx + len(nxt) <= span_end_char:
                    return True
        return False

    def pediatric_subject(
        self, doc: Doc, span_start_char: int, span_end_char: int
    ) -> bool:
        """True when the span's clause has a pediatric PATIENT
        descriptor governing 'with' ('Child with asthma')."""
        cues = self._cues_for(doc)
        boundary = self._clause_boundary_char(doc, span_start_char)
        for i in cues.pediatric_subject_tokens:
            tok = doc[i]
            nxt = doc[i + 1]
            if (
                tok.idx >= boundary
                and nxt.idx + len(nxt) <= span_end_char
            ):
                return True
        return False

    # ------------------------------------------------------------------
    # F3 — inline trigger strip (design F3; replaces the raw-slice regexes
    # _NEGATION_TRIGGERS / _POST_ENTITY_NEGATION_RE)

    def inline_trigger(
        self, doc: Doc, start_char: int, end_char: int
    ) -> tuple[str, str]:
        """(cleaned_text, status) for a RAW entity slice [start_char,
        end_char), matching anchored trigger VOCAB token-sequences on the
        doc tokens instead of regex on the slice text.

        Runs on the raw char offsets (the old helper ran on the raw slice
        BEFORE overlap filtering). Prefix triggers anchor at the span
        start (head token must be fully inside — the regexes matched the
        slice from its first character); the QC-188 post-entity variant
        anchors at the span tail. Cleaned text is computed from token
        boundaries then stripped; the min-length-2 rule and the
        affirmed-fallback are preserved exactly.
        """
        text = doc.text
        toks = [
            t for t in doc
            if not t.is_space
            and t.idx < end_char
            and t.idx + len(t.text) > start_char
        ]
        if toks and toks[0].idx >= start_char:
            for status, sequences in _INLINE_TRIGGER_FAMILIES:
                for seq in sequences:
                    # The regexes required \s+ AFTER the trigger: a
                    # trigger consuming every token never matched.
                    if len(seq) >= len(toks):
                        continue
                    # strict=False on purpose: only the seq-length prefix
                    # of the span tokens is compared.
                    if all(t.lower_ == w for t, w in zip(toks, seq, strict=False)):
                        cut = toks[len(seq) - 1]
                        cleaned = text[cut.idx + len(cut.text):end_char].strip()
                        if len(cleaned) >= 2:
                            return cleaned, status
        for seq in _POST_ENTITY_TAILS:
            k = len(seq)
            if k >= len(toks):
                continue  # '\s+' before the trigger: a head must remain
            tail = toks[-k:]
            if (
                tail[0].idx < start_char
                or tail[-1].idx + len(tail[-1].text) > end_char
            ):
                continue
            if all(t.lower_ == w for t, w in zip(tail, seq, strict=True)):
                cleaned = text[start_char:tail[0].idx].strip()
                if len(cleaned) >= 2:
                    return cleaned, NEGATED_STATUS
        return text[start_char:end_char], AFFIRMED_STATUS

    # ------------------------------------------------------------------
    # F6/F8 — mention-only decisions, PRECOMPUTED at NLP time (AMUX-102
    # stage 3). These operate on the entity's TOKEN SLICE (a list of spaCy
    # tokens; leading/trailing space tokens are skipped) and replicate the
    # legacy mention-text regexes exactly — the resolve fallback chain and
    # the conjunction split consult the precomputed FilteredSpan fields
    # instead of re-deriving them per query.

    @staticmethod
    def _nonspace(tokens) -> list:
        return [t for t in tokens if not t.is_space]

    @staticmethod
    def _joined(tokens) -> str:
        return "".join(t.text_with_ws for t in tokens).strip()

    @staticmethod
    def _fused(a, b) -> bool:
        """True when token b directly follows a with no whitespace between
        (b.i == a.i + 1 and a.whitespace_ == '') — the unspaced-fusion the
        legacy regexes saw inside single tokens ('500mg', '20/12.5')."""
        return b.i == a.i + 1 and a.whitespace_ == ""

    @classmethod
    def _consume_number_item(cls, ns: list, i: int) -> int | None:
        """Consume a NUMBER item (digit tokens joined by unspaced '.'/'/'
        plus an optional unspaced unit chain) ENDING at ns[i-1].

        Returns the item's start index in ns, or None. Mirrors the
        ``\\d+(?:[./]\\d+)*(?:[%°a-zA-Z/]+)?`` alternation of the legacy
        _TRAILING_VALUE_RE: units attach UNSPACED only ('7.2%' strips,
        '7.2 %' does not).
        """
        if i <= 0:
            return None
        # optional attached unit chain: unit-shaped tokens ('/' is
        # unit-shaped too — 'mg/dL'), fused pairwise, fused to the number.
        if _UNIT_ONLY_RE.fullmatch(ns[i - 1].text):
            k = i
            while (
                k >= 1
                and _UNIT_ONLY_RE.fullmatch(ns[k - 1].text)
                and (k == i or cls._fused(ns[k - 1], ns[k]))
            ):
                k -= 1
            if (
                k >= 1
                and k < i
                and cls._fused(ns[k - 1], ns[k])
                and _NUMBER_WITH_UNIT_RE.fullmatch(ns[k - 1].text)
            ):
                i = k
        if not _NUMBER_WITH_UNIT_RE.fullmatch(ns[i - 1].text):
            return None
        i -= 1
        # extend over unspaced '/'/'.' joiners between digit tokens
        while (
            i >= 2
            and ns[i - 1].text in ("/", ".")
            and _NUMBER_TOKEN_RE.fullmatch(ns[i - 2].text)
            and cls._fused(ns[i - 2], ns[i - 1])
            and cls._fused(ns[i - 1], ns[i])
        ):
            i -= 2
        return i

    @classmethod
    def _trailing_value_run(cls, ns: list) -> tuple[list, str | None]:
        """(kept_token_list, stripped_text | None) for a trailing value run.

        Replicates ``\\s+ VALUE (?:[,;]?\\s* VALUE)* \\s*$`` anchored at the
        span END: the run's head (leftmost element) may be a number, a
        comparator word or a bare '+'/'-'; every following element is a
        number item, each optionally preceded by one ','/';' token. QC-188
        ('type 2 diabetes' must NOT strip) holds because the digit token
        '2' is not followed by run-valid tokens to the span end. Never
        empties the text (the regex's leading ``\\s+`` needs a kept token).
        """
        n = len(ns)
        if n == 0:
            return ns, None
        run_start: int | None = None
        i = cls._consume_number_item(ns, n)
        if i is not None:
            while i > 0:
                j = i
                if ns[j - 1].text in (",", ";"):
                    j -= 1
                k = cls._consume_number_item(ns, j)
                if k is None or k == 0:
                    break
                i = k
            # optional comparator / bare '+-' head left of the chain
            j = i
            if j > 0 and ns[j - 1].text in (",", ";"):
                j -= 1
            if j > 0 and (
                ns[j - 1].text in ("+", "-")
                or ns[j - 1].text in _COMPARATOR_WORDS
            ):
                i = j - 1
            run_start = i
        elif ns[-1].text in ("+", "-") or ns[-1].text in _COMPARATOR_WORDS:
            run_start = n - 1  # a lone comparator/'+'-'-' head
        if run_start is None or run_start <= 0:
            return ns, None
        prev, first = ns[run_start - 1], ns[run_start]
        if cls._fused(prev, first):
            return ns, None  # regex needs \s+ before the run
        kept = ns[:run_start]
        return kept, cls._joined(kept)

    @classmethod
    def strip_trailing_values(cls, tokens) -> str:
        """Token-slice _TRAILING_VALUE_RE: 'heart rate 80' -> 'heart rate',
        'BP 120/80' -> 'BP', 'HbA1c 7.2%' -> 'HbA1c'. Returns the stripped
        text, or '' when nothing (valid) strips."""
        _, stripped = cls._trailing_value_run(cls._nonspace(tokens))
        return stripped or ""

    @classmethod
    def strip_trailing_form_noun(cls, tokens) -> str:
        """Token-slice _TRAILING_FORM_NOUN_RE: 'Normal saline bolus' ->
        'Normal saline'. Returns the stripped text, or '' when nothing
        strips."""
        ns = cls._nonspace(tokens)
        if len(ns) < 2 or ns[-1].lower_ not in _FORM_NOUNS:
            return ""
        if cls._fused(ns[-2], ns[-1]):
            return ""  # regex needs \s+ before the noun
        return cls._joined(ns[:-1])

    @classmethod
    def strip_leading_qualifiers(cls, tokens) -> str:
        """Token-slice _LEADING_QUALIFIER_RE (repeated): leading
        {left,right,bilateral,unilateral,upper,lower} or the phrases
        {history of, hx of, status post, s/p}. Returns the stripped text,
        or '' when nothing strips (or stripping would empty the text)."""
        ns = cls._nonspace(tokens)
        i = 0
        while i < len(ns):
            low = ns[i].lower_
            nxt = ns[i + 1].lower_ if i + 1 < len(ns) else None
            if low in _LEADING_QUALIFIER_SINGLES:
                step = 1
            elif nxt is not None and (low, nxt) in _LEADING_QUALIFIER_PHRASES:
                step = 2
            elif low == "s/p":
                step = 1
            elif (
                low == "s"
                and nxt == "/"
                and i + 2 < len(ns)
                and ns[i + 2].lower_ == "p"
                and cls._fused(ns[i], ns[i + 1])
                and cls._fused(ns[i + 1], ns[i + 2])
            ):
                step = 3  # 's/p' split into three unspaced tokens
            else:
                break
            if i + step >= len(ns):
                break  # regex guard: never strip everything
            i += step
        if i == 0:
            return ""
        return cls._joined(ns[i:])

    @classmethod
    def conjunction_split(cls, tokens) -> list[str]:
        """Token-slice _split_on_conjunction (design F8): split on
        'and/or'/'or/and' compounds (fused token or 'and / or' token run),
        standalone 'and'/'or', ',' and '/' tokens. The numeric-parts guard
        is preserved (all parts pure numbers -> no split). Returns [] when
        no valid split (pipeline spans store []; manual spans carry None)."""
        ns = cls._nonspace(tokens)
        # spaCy fuses a leading '/' into the following word after a space
        # ('CBC /CMP' -> ['CBC', '/CMP']); split it back so the separator
        # is seen — the legacy \\s*/\\s* mention regex split these shapes.
        units: list[tuple[str, str]] = []  # (LOWER, join text)
        for t in ns:
            low = t.lower_
            if len(low) > 1 and low[0] == "/":
                units.append(("/", ""))
                low = low[1:]
                units.append((low, t.text[1:] + t.whitespace_))
            else:
                units.append((low, t.text_with_ws))
        lows = [u[0] for u in units]
        n = len(units)

        def _sep_len(i: int) -> int:
            # longest-first: the 3-token compound ('and / or') beats the
            # single 'and'.
            if (
                i + 2 < n
                and lows[i] in ("and", "or")
                and lows[i + 1] == "/"
                and lows[i + 2] in ("and", "or")
                and lows[i] != lows[i + 2]
            ):
                return 3
            if lows[i] in ("and", "or", ",", "/", "and/or", "or/and"):
                return 1
            return 0

        def _join(a: int, b: int) -> str:
            return "".join(u[1] for u in units[a:b]).strip()

        parts: list[str] = []
        seg_start = 0
        i = 0
        while i < n:
            length = _sep_len(i)
            if length and seg_start < i:
                parts.append(_join(seg_start, i))
                seg_start = i + length
            elif length:
                seg_start = i + length  # leading separator: empty segment
            i += length if length else 1
        if seg_start < n:
            parts.append(_join(seg_start, n))
        if len(parts) <= 1:
            return []
        if all(_PURE_NUMBER_RE.fullmatch(p) for p in parts):
            return []
        return parts

    @classmethod
    def search_variant_ladder(cls, tokens) -> list[str]:
        """Ordered fallback query ladder (design F6) — the EXACT order the
        resolve fallback chain uses today: value-stripped, then
        form-noun-stripped (on the value-stripped base), then
        leading-qualifier-stripped (same base). Non-empty, != the full
        text, deduped. The FULL mention is NOT in the list — resolve paths
        query span.text first, then these variants."""
        ns = cls._nonspace(tokens)
        if not ns:
            return []
        full = cls._joined(ns)
        kept, v0 = cls._trailing_value_run(ns)
        base = kept if v0 else ns
        variants: list[str] = []
        if v0 and v0 != full:
            variants.append(v0)
        form = cls.strip_trailing_form_noun(base)
        if form and form != full and form not in variants:
            variants.append(form)
        qual = cls.strip_leading_qualifiers(base)
        if qual and qual != full and qual not in variants:
            variants.append(qual)
        return variants


_CLASS_ORDER = [PSEUDO_NEGATION, UNCERTAINTY, NEGATION, HISTORICAL]

# F3 — inline trigger vocabulary (the _NEGATION_TRIGGERS regex
# alternations as LOWER-token sequences; families in regex order,
# longest-first within a family so 'no evidence of' beats 'no' and
# 'hx of' beats 'hx', exactly as the alternations did).
_INLINE_TRIGGER_FAMILIES: list[tuple[str, list[list[str]]]] = [
    ("negated", [
        ["no", "evidence", "of"], ["no", "sign", "of"],
        ["no", "signs", "of"], ["no", "history", "of"],
    ]),
    ("negated", [
        ["negative", "for"], ["denies"], ["deny"], ["denied"],
        ["ruled", "out"], ["rule", "out"], ["rules", "out"],
        ["free", "of"], ["no"], ["without"], ["absent"],
    ]),
    ("uncertain", [
        ["possible"], ["possibly"], ["may", "have"], ["might", "have"],
        ["could", "be"], ["suspected"], ["suspect"],
        ["suggestive", "of"], ["concerning", "for"], ["likely"],
    ]),
    ("historical", [
        ["previously", "had"], ["history", "of"], ["hx", "of"],
        ["hx"], ["past"], ["prior"], ["remote"],
    ]),
]
for _status, _seqs in _INLINE_TRIGGER_FAMILIES:
    _seqs.sort(key=len, reverse=True)

# QC-188 post-entity negation tails ('breast cancer ruled out'),
# longest-first so 'was ruled out' is taken whole.
_POST_ENTITY_TAILS: list[list[str]] = [
    ["is", "ruled", "out"], ["was", "ruled", "out"],
    ["ruled", "out"], ["rules", "out"],
]

# Inline-trigger statuses (the strings _finalize_doc assigns).
NEGATED_STATUS = "negated"
AFFIRMED_STATUS = "affirmed"

# ---------------------------------------------------------------------------
# F5 — stitching/compose gap classification (design F5). Token-based
# replacements for _STITCH_SLASH_GAP_RE / _STITCH_GAP_RE / _LIST_GAP_RE /
# _COMPOSE_GAP_RE (and the absorb separator gap). Consumers live in
# extraction.py (span-list post-passes) and pass the doc through.

_SLASH_GAP_SEP = ("/", "+")
_STITCH_GAP_SEP = ("/", "\\", "-", "+")
_COMPOSE_LINKERS = frozenset({"with", "without", "w/", "w/o", "of"})
_COMPOSE_SEP = frozenset({"/", "-", "(", ")"})
_ABSORB_SEP = frozenset({".", "/", "-"})


def gap_tokens(doc: Doc, prev_end_char: int, start_char: int) -> list:
    """Non-whitespace tokens FULLY inside the (prev_end, start) char gap.

    A token's trailing whitespace belongs to the following span's region,
    so containment is ``idx >= prev_end`` and ``idx + len <= start``
    (the ``^\\s*...\\s*$`` edges of the old regexes).
    """
    return [
        t for t in doc
        if not t.is_space
        and t.idx >= prev_end_char
        and t.idx + len(t.text) <= start_char
    ]


def _single_token_gap(toks, sep_texts: tuple[str, ...], words: frozenset) -> bool:
    return len(toks) == 1 and (
        toks[0].text in sep_texts or toks[0].lower_ in words
    )


def slash_gap(doc: Doc, prev_end_char: int, start_char: int) -> bool:
    """``^\\s*(?:/|\\+|plus)\\s*$`` — the mislabel-safe bare separator."""
    return _single_token_gap(
        gap_tokens(doc, prev_end_char, start_char), _SLASH_GAP_SEP, frozenset({"plus"})
    )


def stitch_gap(doc: Doc, prev_end_char: int, start_char: int) -> bool:
    """``^\\s*(?:/|\\\\|-|\\+|plus)\\s*$`` — slash/hyphen/plus stitch gap."""
    return _single_token_gap(
        gap_tokens(doc, prev_end_char, start_char), _STITCH_GAP_SEP, frozenset({"plus"})
    )


def list_gap(doc: Doc, prev_end_char: int, start_char: int) -> bool:
    """``^\\s*(?:,|\\band\\b)\\s*$`` — a LIST separator (never a linker)."""
    toks = gap_tokens(doc, prev_end_char, start_char)
    return len(toks) == 1 and (toks[0].text == "," or toks[0].lower_ == "and")


def compose_gap(doc: Doc, prev_end_char: int, start_char: int) -> bool:
    """``^\\s*(?:(?:with|without|w/|w/o|of)\\s*|[/\\-()]\\s*|\\s+)*$``.

    Only with/without/w-of/of words or [/()-] chars (any mix); ',' and
    'and' are never linkers. An empty/whitespace-only gap matches (no
    tokens), as the regex did — consumers gate gap width separately.
    'w/o' may tokenize as w / o and 'w/' as w /: rejoin before matching.
    """
    lows = [t.lower_ for t in gap_tokens(doc, prev_end_char, start_char)]
    merged: list[str] = []
    i = 0
    while i < len(lows):
        if lows[i] == "w" and i + 1 < len(lows) and lows[i + 1] == "/":
            if i + 2 < len(lows) and lows[i + 2] == "o":
                merged.append("w/o")
                i += 3
            else:
                merged.append("w/")
                i += 2
            continue
        merged.append(lows[i])
        i += 1
    return all(
        low in _COMPOSE_LINKERS or low in _COMPOSE_SEP for low in merged
    )


def absorb_sep_gap(doc: Doc, prev_end_char: int, start_char: int) -> bool:
    """``re.fullmatch(r"[\\s./-]*", gap)`` — the trailing-strength absorb
    separator check (empty and whitespace-only gaps pass)."""
    return all(t.text in _ABSORB_SEP for t in gap_tokens(doc, prev_end_char, start_char))


def gap_class(doc: Doc, prev_end_char: int, start_char: int) -> str:
    """Single finest label for a gap: 'adjacent' (whitespace-only/empty),
    'slash', 'stitch', 'list', 'compose', else 'linker-none'. Note the
    boolean predicates overlap by design ('/' is a slash AND compose
    gap) — consumers must use slash_gap/stitch_gap/list_gap/compose_gap
    for exact per-regex parity; this is the classification view."""
    toks = gap_tokens(doc, prev_end_char, start_char)
    if not toks:
        return "adjacent"
    if _single_token_gap(toks, _SLASH_GAP_SEP, frozenset({"plus"})):
        return "slash"
    if _single_token_gap(toks, _STITCH_GAP_SEP, frozenset({"plus"})):
        return "stitch"
    if len(toks) == 1 and (toks[0].text == "," or toks[0].lower_ == "and"):
        return "list"
    lows = [t.lower_ for t in toks]
    merged: list[str] = []
    i = 0
    while i < len(lows):
        if lows[i] == "w" and i + 1 < len(lows) and lows[i + 1] == "/":
            if i + 2 < len(lows) and lows[i + 2] == "o":
                merged.append("w/o")
                i += 3
                continue
            merged.append("w/")
            i += 2
            continue
        merged.append(lows[i])
        i += 1
    if all(low in _COMPOSE_LINKERS or low in _COMPOSE_SEP for low in merged):
        return "compose"
    return "linker-none"


# ---------------------------------------------------------------------------
# F7 — stoplists + mention-text validators (design F7). These operate on
# MENTION text (set membership + three small fullmatch/search regexes:
# stage-roman, code-pattern, markup) — same behavior as the extraction.py
# helpers they replace, relocated so the hot path has no regex.

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
    # 2026-11-11 batteries: bare section-header/boilerplate words that
    # resolved confidently wrong ('exam' -> Assessment lab, 'risks' ->
    # Injury risk, 'Follow-up' -> Follow-Up Encounter, 'Objective' ->
    # Goals).
    "exam", "risks", "follow-up", "objective",
})

_ABBREVIATION_STOPLIST = frozenset({
    "npo", "nkda", "nka", "md", "hedis", "cms", "fda", "cdc",
    "icd", "icd-10", "icd10", "cpt", "loinc", "ndc", "drg", "snomed",
    "umls", "rxnorm", "hcpcs",
    # Bare units/dosing/frequency tokens (2026-11-07 verification): a
    # lone 'mg' resolved to Myasthenia Gravis in 'lisinopril/HCTZ
    # 20/12.5 mg daily'. A unit is never a concept — drop it.
    "mg", "mcg", "ug", "ng", "g", "kg", "ml", "cc", "dl", "l",
    "iu", "meq", "unit", "units", "mmol", "mol", "percent", "%",
    "tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule",
    "capsules", "pill", "pills", "drop", "drops", "puff", "puffs",
    "po", "iv", "im", "sq", "prn", "bid", "tid", "qid", "qd", "qhs",
    "hs", "q6h", "q8h", "q12h", "q24h", "daily", "nightly", "weekly",
})

# Clinical boilerplate phrases (2026-11-11 assertion-scope battery,
# as-status-01/02): 'no acute distress' is the exam-finding template —
# the phrase 'acute distress' in a span of its own is boilerplate, not a
# finding ('in acute distress' phrasing is rare and the measured battery
# noise is 2/2).
_BOILERPLATE_PHRASES = frozenset({"acute distress"})

# Bare billing/terminology codes that leak as spans ('99213' CPT,
# 'E11.9' ICD, '4548-4' LOINC) carry no clinical surface text to
# resolve. Deliberately NOT matching short alnum clinical tokens:
# 'b12'/'a1c' stay extractable (the alnum ICD form requires a decimal).
_CODE_PATTERN_RE = re.compile(
    r"(?:[a-z]\d{2}\.\d+|\d{4,5}-\d|\d{5}|\d{1,3}\.\d+)",
    re.IGNORECASE,
)
# QC-160: staging fragments ('stage 2', 'stage 3') and markup remnants
# ('<b>') — mention-only validators, same semantics as the inline
# fullmatch/search the old helper performed.
_STAGE_ROMAN_RE = re.compile(r"stage\s+\d+(?:[a-d])?")
_MARKUP_RE = re.compile(r"[<>{}]|&[a-z]+;")


def is_false_positive(text: str) -> bool:
    """Check if an entity mention text is a common non-medical word."""
    lower = text.lower().strip()
    if lower in _FALSE_POSITIVE_WORDS:
        return True
    # QC-160: 'Pt' (patient abbreviation) and staging fragments ('stage 2',
    # 'stage 3') leak into affirmed output and resolve to LOINC codes.
    if lower == "pt":
        return True
    if _STAGE_ROMAN_RE.fullmatch(lower):
        return True
    # Filter single-character spans (bullet chars, punctuation, etc.)
    if len(lower) <= 1:
        return True
    # Abbreviation/code/markup stoplist (2026-11-07 adversarial battery):
    # non-clinical shorthand resolved to confident wrong answers
    # ('NPO' -> Narcissistic Personality Disorder, 'HEDIS' -> hypermobile
    # EDS, 'MD' -> Multicore Myopathy, '<b>' -> 'B').
    if lower in _ABBREVIATION_STOPLIST:
        return True
    if lower in _BOILERPLATE_PHRASES:
        return True
    if _CODE_PATTERN_RE.fullmatch(lower):
        return True
    if _MARKUP_RE.search(lower):
        return True
    return False


# ---------------------------------------------------------------------------
# Relocated mention-text validators (design F5/F9 notes): they operate on
# fragment/mention strings, not docs — kept as compiled regexes here so
# extraction.py's hot path carries none.

# F9 — arbiter unit validators (used by _unit_type_signal on 3-token
# rejoined windows). Concentration units (mass or amount per volume) →
# lab result values; bare dose units (no denominator) → administered
# amounts. The separator accepts "/" and the word "per" (the parser
# tokenizer splits "mg/dL" into mg / dL; combined matching is done by
# the caller).
CONCENTRATION_UNIT_RE = re.compile(
    r'^(?:\d+\.?\d*\s*)?'
    r'(mg|mcg|µg|ug|g|mmol|µmol|umol|meq|iu|u|ng)'
    r'\s*(?:/|\s*per\s*|\s*per)'
    r'\s*(dl|l|ml)\s*$',
    re.IGNORECASE,
)
DOSE_UNIT_RE = re.compile(
    r'^(?:\d+\.?\d*\s*)?'
    r'(?:mg|mcg|g|meq|units?|tablets?|tabs?|capsules?|caps?|'
    r'pills?|drops?|puffs?|ml|cc)\s*$',
    re.IGNORECASE,
)

# Assertion-cue fragment validator ('Pneumonia' + 'ruled out' must never
# compose: the cue carries status, not meaning).
ASSERTION_FRAGMENT_RE = re.compile(
    r"^(?:ruled?\s+out|rules?\s+out|excluded?|not\s+seen|not\s+appreciated"
    r"|negative(?:\s+for)?|denies?|denied|free\s+of|no\s+evidence(?:\s+of)?"
    r"|cannot|suspect(?:ed)?|possible|possibly|likely)\b",
    re.IGNORECASE,
)
# Fused dose/value tokens ('500mg', '7.2%', '120/80') survive _TOKEN_RE
# as ONE alnum token, so isdigit()/set membership never classify them —
# measured regression (ts99): 'metformin' + '500mg' composed into
# 'metformin 500mg' and resolved to the glyburide/metformin combination
# anchor instead of plain metformin.
FUSED_VALUE_TOKEN_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:[a-z]+(?:/[a-z]+)?|%)$", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# F6/F8 — legacy mention-text fallbacks (AMUX-102 stage 3). Pipeline spans
# carry search_variants/conjunction_parts precomputed at NLP time (token
# slice); these pure-text helpers remain ONLY as the fallback for manually
# constructed FilteredSpans (public resolve_spans API + tests), where no
# doc tokens exist. Relocated from extraction.py, behavior unchanged.

_TRAILING_VALUE_RE = re.compile(
    r'\s+'
    r'(?:'
        r'\d+(?:[./]\d+)*'
        r'(?:[%°a-zA-Z/]+)?'
        r'|[+\-]'
        r'|less|greater|high|low|normal|abnormal'
    r')'
    r'(?:[,;]?\s*\d+(?:[./]\d+)*(?:[%°a-zA-Z/]+)?)*'
    r'\s*$'
)


def mention_strip_trailing_values(text: str) -> str:
    """Legacy mention-text fallback for manually constructed spans —
    pipeline spans carry precomputed search_variants.

    Parity with the token stripper (Mt4dsCueAnnotator.strip_trailing_values):
    a comparator-headed all-value span ('high 80') stays whole — the
    comparator word belongs to the value run, so the never-empty guard
    fires. The bare regex would otherwise match only the trailing chain
    (' 80') and leave the bare comparator 'high' as a junk query."""
    stripped = _TRAILING_VALUE_RE.sub("", text).strip()
    if stripped in _COMPARATOR_WORDS or stripped in ("+", "-"):
        return text
    return stripped


_LEADING_QUALIFIER_RE = re.compile(
    r"^(?:left|right|bilateral|unilateral|upper|lower|"
    r"history\s+of|hx\s+of|status\s+post|s/p)\s+",
    re.IGNORECASE,
)


def mention_strip_leading_qualifiers(text: str) -> str:
    while True:
        m = _LEADING_QUALIFIER_RE.match(text)
        if not m:
            return text
        rest = text[m.end():].strip()
        if not rest:
            return text
        text = rest


def mention_leading_qualifier_fallback(text: str) -> str | None:
    base = mention_strip_trailing_values(text)
    stripped = mention_strip_leading_qualifiers(base)
    return stripped if stripped and stripped != base else None


_TRAILING_FORM_NOUN_RE = re.compile(
    r"\s+(?:bolus|drip|push|infusion|injection|tablet|tablets|capsule|"
    r"capsules|solution|suspension)\s*$",
    re.IGNORECASE,
)


def mention_trailing_form_fallback(text: str) -> str | None:
    base = mention_strip_trailing_values(text)
    stripped = _TRAILING_FORM_NOUN_RE.sub("", base).strip()
    return stripped if stripped and stripped != base else None


_CONJUNCTION_RE = re.compile(
    r'\s*(?:and\s*/\s*or|or\s*/\s*and)\s*'
    r'|\s+(?:or|and)\s+'
    r'|\s*/\s*'
    r'|\s*,\s+',
    re.IGNORECASE,
)


def mention_split_on_conjunction(text: str) -> list[str] | None:
    parts = _CONJUNCTION_RE.split(text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        return None
    if all(re.fullmatch(r'\d+(?:\.\d+)?', p) for p in parts):
        return None
    return parts
