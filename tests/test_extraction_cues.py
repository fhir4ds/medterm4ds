"""AMUX-102 stage 1 — spaCy-native cue/guard component parity tests.

Pins Mt4dsCueAnnotator (src/medterm4ds/services/extraction_cues.py) on
docs built through a plain medspaCy pipeline (no GLiNER — same shape as
the context_arbiter fixture in test_extraction.py): pre-span cue classes
with adjacency/window/sentence rules, post-span negation/uncertainty
including the parenthetical/comma-block/'now negative' rules, the F4
clause-support guard, and the pediatric-subject guard.
"""

import pytest

from medterm4ds.services.extraction_cues import Mt4dsCueAnnotator


@pytest.fixture(scope="module")
def cues():
    medspacy = pytest.importorskip("medspacy")
    nlp = medspacy.load(medspacy_disable=["medspacy_target_matcher"])
    return Mt4dsCueAnnotator(nlp)


class TestPreSpanCues:
    def test_all_four_classes(self, cues):
        def cue(text, target):
            return cues.pre_span_cue(cues._nlp(text), text.index(target))

        assert cue("Cannot rule out pneumonia.", "pneumonia") == "pseudo_negation"
        assert cue("We cannot exclude pulmonary embolism.", "pulmonary") == "pseudo_negation"
        assert cue("Unable to exclude malignancy.", "malignancy") == "pseudo_negation"
        assert cue("Suspected urinary tract infection.", "urinary") == "uncertainty"
        assert cue("Possibly gout.", "gout") == "uncertainty"
        assert cue("Neither pneumonia nor pulmonary embolism.", "pneumonia") == "negation"
        assert cue("Prior MI, now on dual antiplatelet therapy.", "MI") == "historical"
        assert cue("History of atrial fibrillation.", "atrial") == "historical"

    def test_article_and_hx_of_adjacency(self, cues):
        def cue(text, target):
            return cues.pre_span_cue(cues._nlp(text), text.index(target))

        # uncertainty allows ONE article between cue and span
        assert cue("Suspected a urinary tract infection.", "urinary") == "uncertainty"
        assert cue("Possible an outbreak.", "outbreak") == "uncertainty"
        # two interstitial tokens break the $-anchored adjacency
        assert cue("Suspected a very urinary thing.", "urinary") is None
        # historical: 'hx of' continuation + article
        assert cue("Past hx of the flu.", "flu") == "historical"
        assert cue("History of the flu.", "flu") == "historical"
        # pseudonegation allows NOTHING between cue and span — but the
        # 'cannot rule out' UNCERTAINTY cue allows one article, so the
        # class shifts (parity with the old regex alternation).
        assert cue("Cannot rule out the pneumonia.", "pneumonia") == "uncertainty"

    def test_whitespace_only_gap_keeps_binding(self, cues):
        # '\s*$' in the old regexes: a whitespace-only gap (PyRuSH keeps
        # the newline as a token) must not break cue adjacency.
        doc = cues._nlp("Pt denies\nchest pain noted.")
        assert cues.pre_span_cue(doc, doc.text.index("chest")) == "negation"

    def test_cross_clause_never_binds(self, cues):
        text = "We ruled out the flu. Pneumonia noted today."
        assert cues.pre_span_cue(cues._nlp(text), text.index("Pneumonia")) is None
        # cue directly adjacent in the same sentence still binds
        doc = cues._nlp("Denies chest pain.")
        assert cues.pre_span_cue(doc, doc.text.index("chest")) == "negation"

    def test_cant_variants(self, cues):
        assert cues.pre_span_cue(
            cues._nlp("Can't rule out pneumonia."), "Can't rule out pneumonia.".index("pneumonia")
        ) == "pseudo_negation"
        assert cues.pre_span_cue(
            cues._nlp("Could not exclude fracture."), "Could not exclude fracture.".index("fracture")
        ) == "pseudo_negation"


class TestPostSpanCues:
    def test_negation_binds_directly_after(self, cues):
        def negated(text, span_text, start=None):
            doc = cues._nlp(text)
            return cues.post_span_negation_cue(
                doc, text.index(span_text) + len(span_text),
                text.index(span_text) if start is None else start,
            )

        assert negated("Breast cancer ruled out.", "Breast cancer")
        assert negated("Chest pain was ruled out today.", "Chest pain")
        assert negated("Melanoma excluded.", "Melanoma")
        assert negated("Syncope denied.", "Syncope")

    def test_comma_and_semicolon_block(self, cues):
        def negated(text, span_text):
            return cues.post_span_negation_cue(
                cues._nlp(text), text.index(span_text) + len(span_text)
            )

        assert not negated("Hypertension, negative for proteinuria.", "Hypertension")
        assert not negated("Asthma; free of symptoms today.", "Asthma")
        assert not negated("Positive for fever and cough; negative for dyspnea.", "cough")

    def test_parenthetical_cues(self, cues):
        doc = cues._nlp("Pneumonia (ruled out).")
        assert cues.post_span_negation_cue(doc, doc.text.index("(") - 1)
        doc2 = cues._nlp("Sepsis (possible).")
        assert cues.post_span_uncertainty_cue(doc2, doc2.text.index("(") - 1)
        assert not cues.post_span_negation_cue(doc2, doc2.text.index("(") - 1)

    def test_now_negative_rule(self, cues):
        text = "Previously had COVID-19, now COVID-19 negative."
        doc = cues._nlp(text)
        end = text.index("negative")
        start = text.rindex("COVID-19")
        assert cues.post_span_negation_cue(doc, end, start)
        # the FIRST, unmarked COVID-19 span does not get the binding
        assert not cues.post_span_negation_cue(doc, end, text.index("COVID-19"))
        # unmarked 'HIV negative.' stays affirmed
        hiv = cues._nlp("HIV negative.")
        assert not cues.post_span_negation_cue(hiv, hiv.text.index("negative"), 0)

    def test_window_32_chars(self, cues):
        filler = " ".join(["word"] * 10)  # ~50 chars before the cue
        text = f"Cancer then {filler} ruled out."
        assert not cues.post_span_negation_cue(cues._nlp(text), len("Cancer"))

    def test_uncertainty_forms(self, cues):
        def uncertain(text, span_text):
            return cues.post_span_uncertainty_cue(
                cues._nlp(text), text.index(span_text) + len(span_text)
            )

        assert uncertain("Glioma possible.", "Glioma")
        assert uncertain("Fracture suspected.", "Fracture")
        assert uncertain("Mass concerning for malignancy.", "Mass")
        assert not uncertain("PE cannot be excluded here.", "PE")  # 'cannot be excluded' not post-vocab
        assert not uncertain("Mass concerning.", "Mass")

    def test_next_sentence_does_not_bind(self, cues):
        text = "Breast cancer is present. It was ruled out later."
        assert not cues.post_span_negation_cue(cues._nlp(text), 13)


class TestClauseGuard:
    def test_strong_boundaries_and_support(self, cues):
        def supported(text, target):
            doc = cues._nlp(text)
            i = text.index(target)
            return cues.clause_negation_supported(doc, i, i + len(target))

        # ';' separates — cue stays in the first clause
        assert not supported(
            "No evidence of pneumonia; started ceftriaxone for sepsis.", "sepsis"
        )
        # ':' and ' but ' are boundaries too
        assert not supported("Denies chest pain but fever persists.", "fever")
        # cue in the same clause -> supported
        assert supported("Denies chest pain at rest.", "chest pain")
        # comma is NOT a boundary — list items keep their support
        assert supported("Denies chest pain, arm pain now.", "arm pain")
        # multi-word support cue
        assert supported("Ruled out pneumonia today.", "pneumonia")


class TestPediatricGuard:
    def test_descriptors_with(self, cues):
        def pediatric(text, target):
            doc = cues._nlp(text)
            i = text.index(target)
            return cues.pediatric_subject(doc, i, i + len(target))

        assert pediatric("Child with asthma.", "asthma")
        assert pediatric("Neonate with jaundice.", "jaundice")
        assert pediatric("Toddler with otitis media.", "otitis")
        # non-pediatric subjects
        assert not pediatric("Father had colon cancer.", "colon cancer")
        assert not pediatric("Family history of CAD.", "CAD")
        # descriptor outside the span's clause
        assert not pediatric("Child with asthma; father had diabetes.", "diabetes")


class TestAnnotations:
    def test_call_populates_doc_extension(self, cues):
        doc = cues._nlp("Cannot rule out pneumonia. Breast cancer ruled out.")
        assert doc._.mt4ds_cues is None  # not yet annotated
        cues(doc)
        ann = doc._.mt4ds_cues
        assert ann is not None
        assert "pseudo_negation" in ann.pre_cue_ends.values()
        # post negation cue anchored at the '(' / verb region after 'cancer'
        all_post = [cls for hits in ann.post_cue_starts.values() for cls, _ in hits]
        assert "negation" in all_post
        # guard token annotations present
        assert ann.clause_boundary_tokens or ann.negation_support_tokens

    def test_lazy_annotation_on_first_use(self, cues):
        doc = cues._nlp("Denies chest pain.")
        # methods annotate on demand when __call__ was not invoked
        assert cues.clause_negation_supported(doc, 7, 17)
        assert doc._.mt4ds_cues is not None

    def test_pattern_count_built(self, cues):
        assert cues._pattern_count > 0


# ---------------------------------------------------------------------------
# AMUX-102 stage 2 — F3 inline triggers + F5 gap classification pins.

class TestInlineTrigger:
    """F3: Mt4dsCueAnnotator.inline_trigger on real docs (parity with the
    deleted _detect_inline_trigger regexes)."""

    def trigger(self, cues, text, target):
        doc = cues._nlp(text)
        s = text.index(target)
        return cues.inline_trigger(doc, s, s + len(target))

    def test_prefix_families(self, cues):
        t = self.trigger
        assert t(cues, "no evidence of pneumonia", "no evidence of pneumonia") == ("pneumonia", "negated")
        assert t(cues, "No signs of stroke", "No signs of stroke") == ("stroke", "negated")
        assert t(cues, "negative for influenza", "negative for influenza") == ("influenza", "negated")
        assert t(cues, "Denies chest pain", "Denies chest pain") == ("chest pain", "negated")
        assert t(cues, "Possible gout", "Possible gout") == ("gout", "uncertain")
        assert t(cues, "concerning for malignancy", "concerning for malignancy") == ("malignancy", "uncertain")
        assert t(cues, "history of afib", "history of afib") == ("afib", "historical")
        assert t(cues, "hx of diabetes", "hx of diabetes") == ("diabetes", "historical")
        # 'hx' alone strips too
        assert t(cues, "hx diabetes", "hx diabetes") == ("diabetes", "historical")

    def test_longest_trigger_wins(self, cues):
        # 'no history of' (family 1) beats the bare 'no' (family 2)
        assert self.trigger(cues, "no history of CAD", "no history of CAD") == ("CAD", "negated")

    def test_post_entity_tail(self, cues):
        t = self.trigger
        assert t(cues, "breast cancer ruled out", "breast cancer ruled out") == ("breast cancer", "negated")
        assert t(cues, "melanoma was ruled out", "melanoma was ruled out") == ("melanoma", "negated")
        assert t(cues, "chest pain is ruled out", "chest pain is ruled out") == ("chest pain", "negated")

    def test_no_trigger_and_short_remainder(self, cues):
        t = self.trigger
        # plain mention stays affirmed with the original text
        assert t(cues, "pneumonia noted", "pneumonia") == ("pneumonia", "affirmed")
        # trigger consuming everything / leaving < 2 chars never matched
        assert t(cues, "no", "no") == ("no", "affirmed")
        assert t(cues, "hx", "hx") == ("hx", "affirmed")
        assert t(cues, "ruled out", "ruled out") == ("ruled out", "affirmed")


class TestGapClassification:
    """F5: token-based gap helpers (parity with the deleted
    _STITCH_*_RE / _LIST_GAP_RE / _COMPOSE_GAP_RE regexes)."""

    @staticmethod
    def gap(cues, text, left, right, fn):
        doc = cues._nlp(text)
        prev_end = text.index(left) + len(left)
        start = text.index(right, prev_end)
        return fn(doc, prev_end, start)

    def test_slash_and_plus(self, cues):
        from medterm4ds.services.extraction_cues import gap_class, slash_gap
        assert self.gap(cues, "lisinopril/HCTZ", "lisinopril", "HCTZ", slash_gap)
        assert self.gap(cues, "lisinopril + HCTZ", "lisinopril", "HCTZ", slash_gap)
        assert self.gap(cues, "lisinopril plus HCTZ", "lisinopril", "HCTZ", slash_gap)
        assert self.gap(cues, "lisinopril/HCTZ", "lisinopril", "HCTZ",
                        lambda d, e, s: gap_class(d, e, s) == "slash")
        # hyphen is a stitch gap but NOT a slash gap
        assert self.gap(cues, "lisinopril-HCTZ", "lisinopril", "HCTZ", slash_gap) is False

    def test_stitch_gaps(self, cues):
        from medterm4ds.services.extraction_cues import stitch_gap
        for text in ("lisinopril/HCTZ", "lisinopril-HCTZ", "lisinopril + HCTZ",
                     "lisinopril plus HCTZ", "lisinopril\\HCTZ"):
            assert self.gap(cues, text, "lisinopril", "HCTZ", stitch_gap), text

    def test_list_gap(self, cues):
        from medterm4ds.services.extraction_cues import list_gap
        assert self.gap(cues, "metformin, glipizide", "metformin", "glipizide", list_gap)
        assert self.gap(cues, "metformin and glipizide", "metformin", "glipizide", list_gap)
        # ', and' is NOT a single list separator (regex parity)
        assert self.gap(cues, "metformin, and glipizide", "metformin", "glipizide", list_gap) is False

    def test_compose_gaps(self, cues):
        from medterm4ds.services.extraction_cues import compose_gap
        assert self.gap(cues, "CBC with differential", "CBC", "differential", compose_gap)
        assert self.gap(cues, "CBC without differential", "CBC", "differential", compose_gap)
        assert self.gap(cues, "panel of errors", "panel", "errors", compose_gap)
        assert self.gap(cues, "CBC w/ differential", "CBC", "differential", compose_gap)
        # whitespace-only gap matches (regex \s+ arm)
        assert self.gap(cues, "CBC differential", "CBC", "differential", compose_gap)
        # ',' and 'and' are never linkers
        assert self.gap(cues, "metformin, glipizide", "metformin", "glipizide", compose_gap) is False
        assert self.gap(cues, "metformin and glipizide", "metformin", "glipizide", compose_gap) is False

    def test_adjacent_and_none(self, cues):
        from medterm4ds.services.extraction_cues import gap_class
        assert self.gap(cues, "Norco q6h", "Norco", "q6h",
                        lambda d, e, s: gap_class(d, e, s) == "adjacent")
        assert self.gap(cues, "Norco then q6h", "Norco", "q6h",
                        lambda d, e, s: gap_class(d, e, s) == "linker-none")


class TestMentionStrippers:
    """AMUX-102 stage 3 (design F6): token-slice strippers — parity pins
    with the legacy mention regexes (extraction_cues.mention_* remain the
    fallback for manually constructed spans)."""

    @staticmethod
    def toks(cues, text):
        return [t for t in cues._nlp(text) if not t.is_space]

    def test_strip_trailing_values(self, cues):
        strip = Mt4dsCueAnnotator.strip_trailing_values
        assert strip(self.toks(cues, "heart rate 80")) == "heart rate"
        assert strip(self.toks(cues, "BP 120/80")) == "BP"
        assert strip(self.toks(cues, "HbA1c 7.2%")) == "HbA1c"
        assert strip(self.toks(cues, "lisinopril 20/12.5")) == "lisinopril"
        assert strip(self.toks(cues, "potassium 3.5")) == "potassium"
        assert strip(self.toks(cues, "glucose 100mg/dL")) == "glucose"
        assert strip(self.toks(cues, "glucose less")) == "glucose"

    def test_strip_trailing_values_qc188(self, cues):
        # QC-188: 'type 2 diabetes' must NOT strip — the digit token '2' is
        # followed by 'diabetes', and the run anchors at the span END.
        assert Mt4dsCueAnnotator.strip_trailing_values(
            self.toks(cues, "type 2 diabetes")) == ""

    def test_strip_trailing_values_never_strips(self, cues):
        strip = Mt4dsCueAnnotator.strip_trailing_values
        assert strip(self.toks(cues, "temperature")) == ""
        # spaced units never strip (regex parity: the unit must be unspaced)
        assert strip(self.toks(cues, "glucose 7.2 %")) == ""
        # comparator words are exact-case (the legacy regex had no IGNORECASE)
        assert strip(self.toks(cues, "glucose HIGH")) == ""
        # never empties the text: an all-value span stays whole
        assert strip(self.toks(cues, "high 80")) == ""

    def test_strip_trailing_form_noun(self, cues):
        strip = Mt4dsCueAnnotator.strip_trailing_form_noun
        assert strip(self.toks(cues, "Normal saline bolus")) == "Normal saline"
        assert strip(self.toks(cues, "ceftriaxone 1 g injection")) == "ceftriaxone 1 g"
        assert strip(self.toks(cues, "metformin")) == ""

    def test_strip_leading_qualifiers(self, cues):
        strip = Mt4dsCueAnnotator.strip_leading_qualifiers
        assert strip(self.toks(cues, "Bilateral pneumonia")) == "pneumonia"
        assert strip(self.toks(cues, "Upper GI bleed")) == "GI bleed"
        assert strip(self.toks(cues, "s/p CABG")) == "CABG"
        assert strip(self.toks(cues, "history of MI")) == "MI"
        assert strip(self.toks(cues, "status post CABG")) == "CABG"
        assert strip(self.toks(cues, "left left arm")) == "arm"
        assert strip(self.toks(cues, "metformin")) == ""
        # never empties the text
        assert strip(self.toks(cues, "bilateral")) == ""

    def test_search_variant_ladder(self, cues):
        ladder = Mt4dsCueAnnotator.search_variant_ladder
        # resolve fallback-chain order: value-stripped, form-noun,
        # leading-qualifier; the FULL mention is never in the ladder
        assert ladder(self.toks(cues, "lisinopril 20/12.5")) == ["lisinopril"]
        assert ladder(self.toks(cues, "Normal saline bolus")) == ["Normal saline"]
        assert ladder(self.toks(cues, "Bilateral pneumonia")) == ["pneumonia"]
        assert ladder(self.toks(cues, "metformin")) == []
        # value strip first, then the qualifier strip on the stripped base
        assert ladder(self.toks(cues, "Bilateral pneumonia 3.5")) == [
            "Bilateral pneumonia", "pneumonia",
        ]
        # QC-188: 'type 2 diabetes' keeps its full form as primary and no
        # value-stripped variant
        assert ladder(self.toks(cues, "type 2 diabetes")) == []


class TestConjunctionSplit:
    """AMUX-102 stage 3 (design F8): token-slice conjunction split — parity
    pins with the legacy _split_on_conjunction mention regex."""

    @staticmethod
    def parts(cues, text):
        return Mt4dsCueAnnotator.conjunction_split(
            [t for t in cues._nlp(text) if not t.is_space])

    def test_compound_and_or(self, cues):
        assert self.parts(cues, "CBC and/or CMP") == ["CBC", "CMP"]
        assert self.parts(cues, "CBC or/and CMP") == ["CBC", "CMP"]
        # spaced compound form (EV01-QA-02 pin)
        assert self.parts(cues, "CBC and / or CMP") == ["CBC", "CMP"]

    def test_plain_separators(self, cues):
        assert self.parts(cues, "ALT or AST") == ["ALT", "AST"]
        assert self.parts(cues, "ALT, AST") == ["ALT", "AST"]
        assert self.parts(cues, "HDL/LDL") == ["HDL", "LDL"]
        assert self.parts(cues, "ALT/AST, GGT") == ["ALT", "AST", "GGT"]

    def test_no_split(self, cues):
        assert self.parts(cues, "metformin") == []
        assert self.parts(cues, "CBC and") == []  # single remaining part

    def test_numeric_guard(self, cues):
        # pure-number parts are a value, not two entities
        assert self.parts(cues, "20/12.5") == []
        # not ALL parts numeric -> the split stands (regex parity)
        assert self.parts(cues, "BP 120/80") == ["BP 120", "80"]
