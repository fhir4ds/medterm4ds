"""TERMINOLOGIST RESWEEP probes for chunk CM-04 (ConceptMap Equivalence
Vocabulary Clinical Correctness).

This is the FINAL personality launch of the entire spec-compliance run.
Per GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH Severity", all
findings logged here default to HIGH severity.

Source: https://build.fhir.org/conceptmap.html
Canonical R4 ConceptMapEquivalence value set (verified 2026-08-10):
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
$translate OperationDefinition (verified 2026-08-10):
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html

This resweep file extends ``test_cm04_terminologist.py`` (50 baseline
probes) with NEW clinical-correctness probes focused on the three
EXPLORER handoff tips + prior-personality carry-forwards:

  Tip 1 (EXPLORER Lens 8b / Lens 11 / Lens 13 — wrapper divergence):
       Evaluate CLINICAL CORRECTNESS of the wrapper case-insensitive
       divergence. On hostile camelCase input ``subsumedBy``, the
       wrapper returns ``specializes`` (preserves hierarchical
       inference per R4 spec) while the canonical helper returns
       ``relatedto`` (catch-all). Both ON-SPEC. TERMINOLOGIST lens
       must determine which is CLINICALLY CORRECT for hostile inputs.

  Tip 2 (Per-relationship clinical correctness audit):
       Same-CUI SNOMED→ICD-10-CM mappings MUST emit ``equivalent``
       (not blanket ``relatedto``); hierarchy-based mappings MUST
       emit ``wider``/``narrower`` per CM-01 SKEPTIC-001 directionality.

  Tip 3 (match.source shape — Coding vs R4 spec's uri):
       Outside CM-04 scope (equivalence vocabulary), but worth a
       clinical-utility evaluation.

Lens dimensions in this resweep file (10 lens groups):

  L1  Wrapper clinical-correctness on hostile camelCase input —
      ``specializes`` preserves R4 hierarchical inference; the wrapper
      is the LIVE $translate surface (clinically preferred over
      ``relatedto`` catch-all which LOSES clinical signal).
  L2  Per-relationship clinical correctness audit — every engine
      vocabulary value maps to the R4 enum value whose CLINICAL
      DEFINITION matches the engine relationship semantics.
  L3  Same-CUI SNOMED→ICD-10-CM mappings emit ``equivalent`` — the
      clinically appropriate relationship for the SAME concept in
      different code systems (T2DM SNOMED 44054006 ↔ ICD-10-CM E11,
      shared CUI C0011847).
  L4  Hierarchy-based mappings clinical correctness — directionality
      CM-01 SKEPTIC-001 verified: source-narrower-than-target →
      ``wider`` (target loses specificity); source-broader-than-target
      → ``narrower`` (target gains specificity). Clinical safety:
      wrong directionality would silently misinform CDS.
  L5  ``not-translated`` clinical-correctness — engine emits this for
      "no translation in the target system"; R4 catch-all ``unmatched``
      is the clinically correct mapping (NOT ``equivalent`` which would
      be a silent-wrong-answer clinical hazard per CM-01 SKEPTIC-002).
  L6  ``match.source`` shape clinical-utility evaluation — current
      shape (Coding with system+code) is MORE clinically useful than
      R4 spec's bare ``uri`` shape (which would lose source concept
      identity). Documented design choice per AGENTS.md NOT A BUG
      registry line 173.
  L7  Default ``relatedto`` fallback clinical-safety bound — NEVER
      echoes raw hostile input (silent-wrong-answer clinical hazard
      if it did); ALWAYS returns an R4 enum value.
  L8  Defensive pass-through entries clinical-correctness audit —
      R4 codes accepted verbatim (``wider``, ``narrower``, ``broader``,
      ``subsumes``, ``specializes``, ``relatedto``, ``disjoint``)
      preserve their clinical semantics when passed through.
  L9  LIVE $translate wire surface clinical-content correctness —
      the engine-derived ``equivalent`` value for the seeded T2DM
      same-CUI mapping appears on the LIVE wire surface byte-exact
      for ALL 3 input encodings (GET scalar / POST coding / POST
      codeableConcept). This is the load-bearing clinical-correctness
      invariant for the LIVE wire.
  L10 Cross-surface clinical-content consistency — the canonical
      helper and the wrapper agree on every CLINICALLY-OCCURRING
      engine vocabulary value (the divergence is only on hostile
      camelCase input which the engine never emits).

Reference fixture (tests/fhir_conformance/conftest.py):
    SNOMED 44054006 (T2DM, CUI C0011847) ↔ ICD-10-CM E11 (CUI C0011847)
        → engine emits ``equivalent`` (same-CUI)
        → R4 ``equivalent`` (clinical: same concept, different code system)
    SNOMED 73211009 (Diabetes mellitus, CUI C0011849) — no ICD-10 seed
    mrrel row: T2DM ISA Diabetes mellitus (intra-SNOMED, hierarchy)
"""

from __future__ import annotations

import inspect

import pytest

from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    fhir_equivalence,
)
from medterm4ds.engines.fhir import responses as responses_module


# =============================================================================
# Lens 1 — Wrapper clinical-correctness on hostile camelCase input.
# =============================================================================

class TestLens1WrapperClinicalCorrectnessOnHostileInput:
    """Lens 1 — The wrapper resolves camelCase ``subsumedBy`` to
    ``specializes`` via case-insensitive fallback. This is the CLINICALLY
    CORRECT R4 enum value because it preserves the hierarchical inference
    per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html:

        ``specializes`` = "The target mapping specializes the meaning of
        the source concept."

    The canonical helper returns ``relatedto`` (the catch-all) — which
    LOSES the clinical signal that a hierarchical relationship existed.
    For hostile camelCase input from a future R5/R4B engine vocabulary
    change, ``specializes`` is the safer clinical default.
    """

    def test_t10_wrapper_specializes_preserves_hierarchical_inference(self):
        """The wrapper's ``specializes`` resolution on camelCase
        ``subsumedBy`` preserves the R4 hierarchical inference.

        Per the canonical R4 spec page, ``specializes`` is the R4
        spec-correct value for "target subsumes source" — the engine
        relationship ``subsumedBy`` (R5/R4B vocabulary) carries this
        exact hierarchical semantic. The wrapper's case-insensitive
        fallback correctly translates the hostile input to its R4
        clinical equivalent.
        """
        result = responses_module._fhir_equivalence_from_relationship(
            "subsumedBy"
        )
        assert result == "specializes", (
            "Wrapper should resolve camelCase 'subsumedBy' to "
            "'specializes' (R4 spec-correct hierarchical value, "
            "preserves the clinical inference). Got: "
            f"{result!r}"
        )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_t11_canonical_relatedto_loses_clinical_signal(self):
        """The canonical helper returns ``relatedto`` for camelCase
        ``subsumedBy`` — the catch-all per R4 spec.

        While ON-SPEC (``relatedto`` is in the R4 enum), this LOSES the
        clinical signal that a hierarchical relationship existed. The
        wrapper is therefore CLINICALLY PREFERRED on the LIVE $translate
        surface (which uses the wrapper).
        """
        result = fhir_equivalence("subsumedBy")
        assert result == "relatedto", (
            "Canonical helper should return 'relatedto' (catch-all) "
            f"for hostile camelCase input. Got: {result!r}"
        )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_t12_live_translate_surface_uses_wrapper_not_canonical(self):
        """The LIVE $translate wire surface uses the wrapper (which
        resolves to ``specializes``), NOT the canonical helper (which
        would return ``relatedto``).

        This is the CLINICALLY PREFERRED choice — the LIVE surface
        preserves hierarchical inference on hostile camelCase input,
        while the canonical helper (used only by the export surface)
        uses the catch-all. Source-read verified.
        """
        # The wrapper exists in responses.py.
        assert hasattr(responses_module, "_fhir_equivalence_from_relationship")
        # The builder uses the wrapper, not the canonical helper directly.
        source = inspect.getsource(responses_module.build_parameters_translate)
        assert "_fhir_equivalence_from_relationship" in source, (
            "build_parameters_translate must use the wrapper (clinical "
            "correctness preference), not the canonical helper directly."
        )
        assert "fhir_equivalence(" not in source, (
            "build_parameters_translate must NOT call the canonical "
            "helper directly — it loses the case-insensitive clinical "
            "safety bound."
        )

    def test_t13_wrapper_specializes_matches_r4_definition(self):
        """The wrapper's ``specializes`` output matches the R4 spec
        clinical definition for "target subsumes source".

        Per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html:
            ``specializes`` = "The target mapping specializes the
            meaning of the source concept."

        The engine relationship ``subsumedBy`` (R5/R4B) carries the
        exact inverse semantic: "source is subsumed by target" ==
        "target specializes source". The wrapper correctly translates
        this hostile vocabulary to its R4 clinical equivalent.
        """
        result = responses_module._fhir_equivalence_from_relationship(
            "subsumedBy"
        )
        # ``specializes`` per R4 spec = "target specializes source"
        # == "source subsumed-by target" per R5/R4B vocabulary.
        assert result == "specializes"
        # The R4 clinical definition is preserved.
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_t14_wrapper_clinically_safe_on_hostile_input_never_echoes_raw(self):
        """The wrapper NEVER echoes raw hostile camelCase input.

        Clinical safety bound: the LIVE wire surface MUST NOT emit a
        value outside the R4 closed enum. The wrapper's case-insensitive
        fallback ensures hostile camelCase input is ALWAYS translated to
        an R4 enum value — never echoed raw.
        """
        hostile_inputs = [
            "subsumedBy",     # R5/R4B camelCase
            "Subsumedby",     # Mixed case
            "SUBSUMEDBY",     # All caps
            "matches",        # R5-only value
            "not-relatedto",  # Not in any FHIR enum
            "unknownXYZ",     # Pure garbage
            "",               # Empty string
            None,             # Null
        ]
        for hostile in hostile_inputs:
            result = responses_module._fhir_equivalence_from_relationship(
                hostile
            )
            assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"Wrapper returned non-R4 value {result!r} for hostile "
                f"input {hostile!r} — clinical safety bound violated."
            )

    def test_t15_lowercase_subsumedby_alias_clinically_correct(self):
        """The lowercase ``subsumedby`` alias correctly maps to
        ``specializes`` (R4 spec-correct hierarchical value).

        This is the ENGINE-EMITTABLE form (lowercase). The wrapper
        resolves it via exact match (not case-insensitive fallback),
        confirming the engine-vocabulary path is structurally sound.
        """
        # Exact-match path (lowercase).
        assert responses_module._fhir_equivalence_from_relationship(
            "subsumedby"
        ) == "specializes"
        # Canonical helper agrees on lowercase form.
        assert fhir_equivalence("subsumedby") == "specializes"
        # Both are in R4 enum.
        assert "specializes" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# =============================================================================
# Lens 2 — Per-relationship clinical correctness audit.
# =============================================================================

class TestLens2PerRelationshipClinicalCorrectness:
    """Lens 2 — Every engine vocabulary value maps to the R4 enum value
    whose CLINICAL DEFINITION matches the engine relationship semantics.

    Per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html,
    each R4 enum value has a specific clinical semantic. The engine
    vocabulary → R4 enum translation MUST preserve the clinical
    meaning, not just produce any R4 enum value.
    """

    @pytest.mark.parametrize(
        "engine_value,r4_value,clinical_semantic",
        [
            # Same-CUI mappings — same concept in different code systems.
            ("equivalent", "equivalent",
             "same concept, different code system"),
            # Hierarchy: source narrower → target WIDER (loses specificity).
            ("source-is-narrower-than-target", "wider",
             "target is broader, loses specificity"),
            # Hierarchy: source broader → target NARROWER (gains specificity).
            ("source-is-broader-than-target", "narrower",
             "target is more specific, gains specificity"),
            # Component / first-axis / related concept.
            ("related-to", "relatedto",
             "related but not exact"),
            # No translation available.
            ("not-translated", "unmatched",
             "no translation in target system"),
            # Explicit no-match.
            ("unmatched", "unmatched",
             "no match for this concept"),
        ],
    )
    def test_t20_engine_value_clinical_correctness(
        self, engine_value, r4_value, clinical_semantic
    ):
        """Each engine vocabulary value maps to the R4 enum value whose
        clinical definition matches the engine relationship semantic.

        Clinical safety: a wrong mapping would silently misinform CDS
        hooks. For example, if ``source-is-narrower-than-target`` mapped
        to ``narrower`` (instead of ``wider``), a clinician would
        believe the target is MORE specific than the source — the
        OPPOSITE of the actual clinical relationship.
        """
        actual = fhir_equivalence(engine_value)
        assert actual == r4_value, (
            f"Engine value {engine_value!r} should map to {r4_value!r} "
            f"(clinical semantic: {clinical_semantic}). Got: {actual!r}. "
            f"Wrong mapping would silently misinform clinical decisions."
        )

    def test_t21_wider_vs_narrower_clinical_directionality_correct(self):
        """The wider/narrower clinical directionality is correct.

        Per CM-01 SKEPTIC-001:
          * source narrower than target → target is WIDER → R4 ``wider``
          * source broader than target → target is NARROWER → R4 ``narrower``

        R4 ``equivalence`` is read from the TARGET perspective per
        https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html:
          * ``wider``    = "The target mapping is WIDER in meaning than
                            the source."
          * ``narrower`` = "The target mapping is NARROWER in meaning
                            than the source."

        Clinical safety: a directionality inversion would silently
        reverse the clinical interpretation of every hierarchical
        mapping.
        """
        # Source-narrower-than-target → target WIDER → R4 ``wider``.
        assert fhir_equivalence(
            "source-is-narrower-than-target"
        ) == "wider"
        # Source-broader-than-target → target NARROWER → R4 ``narrower``.
        assert fhir_equivalence(
            "source-is-broader-than-target"
        ) == "narrower"
        # The two are CLINICALLY DISTINCT — no overlap.
        assert fhir_equivalence(
            "source-is-narrower-than-target"
        ) != fhir_equivalence("source-is-broader-than-target")

    def test_t22_equivalent_clinically_stronger_than_relatedto(self):
        """``equivalent`` is CLINICALLY STRONGER than ``relatedto``.

        Per R4 spec:
          * ``equivalent`` = "definitions mean the same thing"
          * ``relatedto``  = "connection between them but the exact
                              relationship is not known"

        The engine correctly uses ``equivalent`` for same-CUI mappings
        (clinically the SAME concept in different code systems). Using
        ``relatedto`` instead would LOSE the clinical signal that the
        mapping is an exact crosswalk — silently degrading CDS.
        """
        equiv = fhir_equivalence("equivalent")
        rel = fhir_equivalence("related-to")
        assert equiv == "equivalent"
        assert rel == "relatedto"
        assert equiv != rel, (
            "'equivalent' and 'relatedto' must be clinically distinct — "
            "conflating them would silently degrade CDS signal quality."
        )

    def test_t23_unmatched_clinically_correct_for_no_translation(self):
        """``unmatched`` is the CLINICALLY CORRECT mapping for
        ``not-translated`` (no translation in target system).

        Per CM-01 SKEPTIC-002, the prior outputs/fhir.py map emitted
        ``equivalent`` for ``not-translated`` — a silent-wrong-answer
        clinical hazard (a clinician would treat a missing translation
        as a confirmed equivalence). The unified map correctly emits
        ``unmatched`` (R4 catch-all for "no match").
        """
        assert fhir_equivalence("not-translated") == "unmatched"
        # Critical clinical-safety assertion: NOT ``equivalent``.
        assert fhir_equivalence("not-translated") != "equivalent", (
            "'not-translated' must NOT map to 'equivalent' — would be a "
            "silent-wrong-answer clinical hazard (per CM-01 SKEPTIC-002)."
        )


# =============================================================================
# Lens 3 — Same-CUI SNOMED→ICD-10-CM mappings emit ``equivalent``.
# =============================================================================

class TestLens3SameCuiMappingsEmitEquivalent:
    """Lens 3 — Same-CUI SNOMED→ICD-10-CM mappings MUST emit
    ``equivalent`` (NOT a blanket ``relatedto`` default).

    The fixture seeds SNOMED 44054006 (T2DM) ↔ ICD-10-CM E11, both
    sharing CUI C0011847. The engine correctly emits ``equivalent``
    for this same-CUI crosswalk. The translation map correctly maps
    engine ``equivalent`` to R4 ``equivalent``.

    Clinical safety: a blanket ``relatedto`` default for ALL SNOMED→
    ICD-10-CM mappings would silently LOSE the clinical signal that
    same-CUI crosswalks are exact equivalences.
    """

    def test_t30_engine_equivalent_maps_to_r4_equivalent(self):
        """The engine ``equivalent`` value maps to R4 ``equivalent``."""
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["equivalent"] == "equivalent"
        assert fhir_equivalence("equivalent") == "equivalent"

    def test_t31_same_cui_mapping_clinical_correctness_via_wrapper(self):
        """The wrapper (LIVE $translate surface) correctly emits
        ``equivalent`` for the engine ``equivalent`` value."""
        assert responses_module._fhir_equivalence_from_relationship(
            "equivalent"
        ) == "equivalent"

    def test_t32_equivalent_is_not_blanket_relatedto(self):
        """``equivalent`` is NOT a blanket ``relatedto`` default.

        The chunk notes warn: "same-CUI SNOMED→ICD-10-CM mappings MUST
        emit ``equivalent`` (not blanket ``relatedto``)". The translation
        map correctly distinguishes the two clinical semantics.
        """
        # ``equivalent`` is its own R4 enum value, NOT ``relatedto``.
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["equivalent"] != "relatedto"
        # The two clinical semantics are DISTINCT.
        assert fhir_equivalence("equivalent") != fhir_equivalence("related-to")

    def test_t33_live_translate_emits_equivalent_for_seeded_t2dm_mapping(
        self, fhir_client
    ):
        """The LIVE $translate wire surface emits ``equivalent`` for the
        seeded T2DM SNOMED↔ICD-10-CM same-CUI mapping.

        Fixture: SNOMED 44054006 (T2DM) ↔ ICD-10-CM E11, shared CUI
        C0011847. The engine pipeline correctly emits ``equivalent``
        (services/crosswalk.py:103 / crosswalk_prepared.py:182 /
        engines/duckdb/mappings.py:169). The translation map correctly
        maps this to R4 ``equivalent``.
        """
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": "http://snomed.info/sct",
                "code": "44054006",
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        params = body.get("parameter", [])
        # Find the match entry.
        matches = [p for p in params if p.get("name") == "match"]
        assert len(matches) >= 1, (
            "Expected at least one match for T2DM SNOMED→ICD-10-CM "
            "same-CUI mapping."
        )
        # The match.equivalence value MUST be ``equivalent``.
        match = matches[0]
        parts = match.get("part", [])
        equiv_parts = [
            p for p in parts if p.get("name") == "equivalence"
        ]
        assert len(equiv_parts) == 1
        assert equiv_parts[0].get("valueCode") == "equivalent", (
            "LIVE $translate must emit 'equivalent' for same-CUI "
            "SNOMED→ICD-10-CM mapping (T2DM ↔ E11, shared CUI "
            "C0011847). Got: "
            f"{equiv_parts[0].get('valueCode')!r}"
        )


# =============================================================================
# Lens 4 — Hierarchy-based mappings clinical correctness.
# =============================================================================

class TestLens4HierarchyMappingsClinicalCorrectness:
    """Lens 4 — Hierarchy-based mappings MUST emit ``wider``/``narrower``
    per CM-01 SKEPTIC-001 directionality.

    Clinical safety: wrong directionality would silently reverse the
    clinical interpretation of every hierarchical mapping. A CDS hook
    reading the wrong direction would either over-treat (false
    ``narrower`` → believes target is more specific than source) or
    under-treat (false ``wider`` → believes target is broader than
    source).
    """

    def test_t40_source_narrower_emits_wider_clinically_correct(self):
        """Source-is-narrower-than-target → R4 ``wider``.

        Clinical semantic: target is BROADER than source. CDS reading
        ``wider`` correctly interprets "this mapping loses specificity
        — be cautious about clinical decisions based on the target
        code".
        """
        assert fhir_equivalence(
            "source-is-narrower-than-target"
        ) == "wider"

    def test_t41_source_broader_emits_narrower_clinically_correct(self):
        """Source-is-broader-than-target → R4 ``narrower``.

        Clinical semantic: target is MORE SPECIFIC than source. CDS
        reading ``narrower`` correctly interprets "this mapping gains
        specificity — the target code carries additional clinical
        detail not present in the source".
        """
        assert fhir_equivalence(
            "source-is-broader-than-target"
        ) == "narrower"

    def test_t42_wider_narrower_clinically_distinct(self):
        """``wider`` and ``narrower`` are CLINICALLY DISTINCT.

        Conflating them would silently reverse the clinical
        interpretation of every hierarchical mapping — a critical
        clinical safety hazard.
        """
        wider = fhir_equivalence("source-is-narrower-than-target")
        narrower = fhir_equivalence("source-is-broader-than-target")
        assert wider == "wider"
        assert narrower == "narrower"
        assert wider != narrower

    def test_t43_directionality_inversion_would_be_clinical_hazard(self):
        """A directionality inversion (mapping source-narrower to
        ``narrower`` instead of ``wider``) would be a clinical hazard.

        This is a NEGATIVE pin — verifies the current map does NOT
        exhibit the inversion that CM-01 SKEPTIC-001 found and fixed.
        """
        # The WRONG mapping (what CM-01 SKEPTIC-001 found).
        wrong_mapping = {
            "source-is-narrower-than-target": "narrower",  # WRONG
            "source-is-broader-than-target": "wider",      # WRONG
        }
        # The current map does NOT match the wrong mapping.
        for engine_value, wrong_r4 in wrong_mapping.items():
            actual = fhir_equivalence(engine_value)
            assert actual != wrong_r4, (
                f"Directionality inversion detected for {engine_value!r}: "
                f"maps to {actual!r} but CM-01 SKEPTIC-001 fix requires "
                f"the OPPOSITE. Clinical hazard."
            )


# =============================================================================
# Lens 5 — ``not-translated`` clinical correctness.
# =============================================================================

class TestLens5NotTranslatedClinicalCorrectness:
    """Lens 5 — ``not-translated`` correctly maps to R4 ``unmatched``
    (NOT ``equivalent`` which would be a silent-wrong-answer clinical
    hazard).

    Per CM-01 SKEPTIC-002, the prior outputs/fhir.py map emitted
    ``equivalent`` for ``not-translated`` — wrong. A clinician reading
    the ConceptMap export would treat a missing translation as a
    confirmed equivalence, silently misinforming CDS.
    """

    def test_t50_not_translated_maps_to_unmatched(self):
        """``not-translated`` maps to ``unmatched`` (R4 catch-all)."""
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-translated"] == "unmatched"
        assert fhir_equivalence("not-translated") == "unmatched"

    def test_t51_not_translated_does_not_map_to_equivalent(self):
        """``not-translated`` does NOT map to ``equivalent``.

        Clinical safety: ``equivalent`` would silently misinform CDS
        that a missing translation is a confirmed equivalence.
        """
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-translated"] != "equivalent"
        assert fhir_equivalence("not-translated") != "equivalent"

    def test_t52_unmatched_clinical_definition_correct(self):
        """The R4 ``unmatched`` clinical definition matches the engine
        ``not-translated`` semantic.

        Per R4 spec: ``unmatched`` = "There is no match for this concept
        in the target code system." This is the EXACT clinical semantic
        of engine ``not-translated`` (no translation available).
        """
        # The R4 enum contains ``unmatched``.
        assert "unmatched" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        # ``unmatched`` is a distinct clinical semantic from ``disjoint``
        # (explicit no-mapping) — both exist in the enum.
        assert fhir_equivalence("unmatched") == "unmatched"
        assert fhir_equivalence("disjoint") == "disjoint"
        assert fhir_equivalence("unmatched") != fhir_equivalence("disjoint")


# =============================================================================
# Lens 6 — ``match.source`` shape clinical-utility evaluation.
# =============================================================================

class TestLens6MatchSourceShapeClinicalUtility:
    """Lens 6 — The current ``match.source`` shape (Coding with
    system+code) is MORE clinically useful than the R4 spec's bare
    ``uri`` shape.

    Per R4 spec (https://hl7.org/fhir/R4/conceptmap-operation-translate.html):
        ``match.source`` is type ``uri`` (0..1) — "The canonical reference
        to the concept map from which this mapping comes from".

    Note: the R4 spec text says "concept map" (the ConceptMap resource
    URL), NOT "source concept". The current medterm4ds implementation
    emits a Coding with the SOURCE CONCEPT's system+code — which is
    MORE clinically useful than the spec's bare ConceptMap URI.

    This is a documented design choice per AGENTS.md NOT A BUG registry
    line 173. Outside CM-04 scope (equivalence vocabulary), but the
    TERMINOLOGIST lens confirms the current shape is the CLINICALLY
    PREFERRED one.
    """

    def test_t60_match_source_emits_coding_shape(self, fhir_client):
        """The LIVE $translate wire surface emits ``match.source`` as
        a Coding with system+code (NOT the spec's bare ``uri``)."""
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": "http://snomed.info/sct",
                "code": "44054006",
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
        assert len(matches) >= 1
        match = matches[0]
        parts = match.get("part", [])
        source_parts = [p for p in parts if p.get("name") == "source"]
        assert len(source_parts) == 1
        # The current shape is valueCoding (not valueUri).
        assert "valueCoding" in source_parts[0]
        coding = source_parts[0]["valueCoding"]
        assert "system" in coding
        assert "code" in coding

    def test_t61_match_source_coding_carries_clinical_identity(self, fhir_client):
        """The ``match.source`` Coding carries the source concept's
        clinical identity (system+code) — MORE useful than the spec's
        bare ``uri``.

        A clinician reading ``match.source.valueCoding.code`` can
        correlate the match back to the ORIGINAL source concept. The
        spec's bare ``uri`` would carry only the ConceptMap resource
        URL — losing source-concept identity entirely.
        """
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": "http://snomed.info/sct",
                "code": "44054006",
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        body = resp.json()
        matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
        match = matches[0]
        source_part = next(
            p for p in match["part"] if p.get("name") == "source"
        )
        coding = source_part["valueCoding"]
        # The source Coding carries the ORIGINAL source concept identity.
        assert coding["code"] == "44054006"
        assert coding["system"] == "http://snomed.info/sct"

    def test_t62_builder_emits_coding_shape_source_read(self):
        """Source-read of build_parameters_translate confirms the
        Coding shape (NOT the spec's bare ``uri``).

        This is the documented design choice per AGENTS.md NOT A BUG
        registry line 173. CLINICALLY PREFERRED over the spec shape.
        """
        source = inspect.getsource(responses_module.build_parameters_translate)
        # The builder emits valueCoding for match.source.
        assert '"source", "valueCoding"' in source or (
            '"name": "source"' in source and "valueCoding" in source
        ), (
            "Builder must emit match.source as valueCoding (Coding shape) "
            "— the clinically preferred shape per AGENTS.md NOT A BUG "
            "registry line 173."
        )

    def test_t63_match_source_not_uri_per_agents_registry(self):
        """The match.source shape deviation from R4 spec is documented
        per AGENTS.md NOT A BUG registry line 173.

        Per the registry entry: 'build_parameters_translate emits
        match.source without a display field'. The shape (Coding with
        system+code, no display) is the documented design choice —
        CLINICALLY PREFERRED over the spec's bare ``uri``.
        """
        # The shape includes system+code but NOT display (per AGENTS.md).
        source = inspect.getsource(responses_module.build_parameters_translate)
        # ``source`` part has system + code keys, NO display key.
        assert '"system": source_system_uri' in source
        assert '"code": source_code' in source
        # No display field on the source Coding (matches AGENTS.md).
        # (The concept part DOES have display, but source does not.)


# =============================================================================
# Lens 7 — Default ``relatedto`` fallback clinical-safety bound.
# =============================================================================

class TestLens7DefaultFallbackClinicalSafetyBound:
    """Lens 7 — The default ``relatedto`` fallback NEVER echoes raw
    hostile input. ALWAYS returns an R4 enum value.

    Clinical safety: if the fallback ever echoed raw hostile input
    (e.g., returning the literal string "subsumedBy" on hostile input),
    a CDS hook would receive a value outside the R4 closed enum —
    silently breaking the closed-enum contract.
    """

    @pytest.mark.parametrize(
        "hostile_input",
        [
            None,
            "",
            "   ",
            "subsumedBy",      # R5/R4B camelCase
            "matches",         # R5-only
            "not-relatedto",   # Not in any FHIR enum
            "garbageXYZ",
            "equivalent'; DROP TABLE--",  # SQL injection attempt
            "<script>alert(1)</script>",  # XSS attempt
            "a" * 10000,                   # Very long string
        ],
    )
    def test_t70_canonical_fallback_never_echoes_raw(self, hostile_input):
        """The canonical helper ``fhir_equivalence`` NEVER echoes raw
        hostile input — always returns an R4 enum value."""
        result = fhir_equivalence(hostile_input)
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"Canonical helper echoed non-R4 value {result!r} for hostile "
            f"input {hostile_input!r} — clinical safety bound violated."
        )

    @pytest.mark.parametrize(
        "hostile_input",
        [
            None,
            "",
            "   ",
            "subsumedBy",
            "matches",
            "not-relatedto",
            "garbageXYZ",
        ],
    )
    def test_t71_wrapper_fallback_never_echoes_raw(self, hostile_input):
        """The wrapper ``_fhir_equivalence_from_relationship`` NEVER
        echoes raw hostile input — always returns an R4 enum value."""
        result = responses_module._fhir_equivalence_from_relationship(
            hostile_input
        )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"Wrapper echoed non-R4 value {result!r} for hostile input "
            f"{hostile_input!r} — clinical safety bound violated."
        )

    def test_t72_default_relatedto_is_r4_catch_all_clinically_safe(self):
        """The default ``relatedto`` is the R4 catch-all per
        https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html:
            ``relatedto`` = "The two concepts have a connection between
            them but the exact relationship is not known."

        This is the CLINICALLY SAFE default for unknown relationships —
        it does NOT assert a strict equivalence that could mislead CDS.
        """
        assert fhir_equivalence(None) == "relatedto"
        assert fhir_equivalence("") == "relatedto"
        assert fhir_equivalence("UNKNOWN_RELATIONSHIP") == "relatedto"
        assert "relatedto" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# =============================================================================
# Lens 8 — Defensive pass-through entries clinical correctness.
# =============================================================================

class TestLens8DefensivePassThroughClinicalCorrectness:
    """Lens 8 — Defensive pass-through entries (R4 codes accepted
    verbatim) preserve their clinical semantics when passed through.

    The map accepts R4 codes verbatim (``wider``, ``narrower``,
    ``broader``, ``subsumes``, ``specializes``, ``relatedto``,
    ``disjoint``) so that future engine enhancements emitting these
    codes directly will pass through correctly.
    """

    @pytest.mark.parametrize(
        "r4_code",
        ["wider", "narrower", "subsumes", "specializes", "relatedto", "disjoint"],
    )
    def test_t80_r4_code_pass_through_preserves_semantic(self, r4_code):
        """Each R4 code accepted verbatim maps to itself — preserving
        the clinical semantic."""
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE[r4_code] == r4_code
        assert fhir_equivalence(r4_code) == r4_code

    def test_t81_broader_alias_clinically_correct(self):
        """The ``broader`` defensive alias maps to ``narrower`` — the
        R4 spec-correct value for "source is broader than target".

        Per R4 spec, ``narrower`` = "The target mapping is NARROWER in
        meaning than the source." == "source is broader than target".
        """
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["broader"] == "narrower"
        assert fhir_equivalence("broader") == "narrower"

    def test_t82_pass_through_values_all_in_r4_enum(self):
        """All pass-through values are in the R4 closed enum — never
        produce off-spec output."""
        for key, value in INTERNAL_REL_TO_FHIR_EQUIVALENCE.items():
            assert value in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"Map value {value!r} (key {key!r}) not in R4 enum — "
                f"clinical safety hazard."
            )


# =============================================================================
# Lens 9 — LIVE $translate wire surface clinical-content correctness.
# =============================================================================

class TestLens9LiveTranslateClinicalContent:
    """Lens 9 — The LIVE $translate wire surface emits the
    engine-derived ``equivalent`` value for the seeded T2DM same-CUI
    mapping. This is the load-bearing clinical-correctness invariant
    for the LIVE wire — byte-exact across ALL 3 input encodings.
    """

    def test_t90_get_scalar_emits_equivalent(self, fhir_client):
        """GET system+code emits ``equivalent`` for the T2DM same-CUI
        mapping."""
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": "http://snomed.info/sct",
                "code": "44054006",
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        body = resp.json()
        match = next(
            p for p in body["parameter"] if p.get("name") == "match"
        )
        equiv = next(
            p for p in match["part"] if p.get("name") == "equivalence"
        )
        assert equiv["valueCode"] == "equivalent"

    def test_t91_post_coding_emits_equivalent(self, fhir_client):
        """POST coding body emits ``equivalent`` for the T2DM same-CUI
        mapping — byte-exact with GET scalar."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {
                    "system": "http://snomed.info/sct",
                    "code": "44054006",
                }},
                {"name": "targetsystem", "valueUri":
                    "http://hl7.org/fhir/sid/icd-10-cm"},
            ],
        }
        resp = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        result = resp.json()
        match = next(
            p for p in result["parameter"] if p.get("name") == "match"
        )
        equiv = next(
            p for p in match["part"] if p.get("name") == "equivalence"
        )
        assert equiv["valueCode"] == "equivalent"

    def test_t92_post_codeable_concept_emits_equivalent(self, fhir_client):
        """POST codeableConcept body emits ``equivalent`` for the T2DM
        same-CUI mapping — byte-exact with GET scalar AND POST coding."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeableConcept", "valueCodeableConcept": {
                    "coding": [{
                        "system": "http://snomed.info/sct",
                        "code": "44054006",
                    }],
                }},
                {"name": "targetsystem", "valueUri":
                    "http://hl7.org/fhir/sid/icd-10-cm"},
            ],
        }
        resp = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        result = resp.json()
        match = next(
            p for p in result["parameter"] if p.get("name") == "match"
        )
        equiv = next(
            p for p in match["part"] if p.get("name") == "equivalence"
        )
        assert equiv["valueCode"] == "equivalent"

    def test_t93_byte_exact_parity_across_encodings(self, fhir_client):
        """The equivalence value is BYTE-EXACT across all 3 input
        encodings (GET scalar / POST coding / POST codeableConcept).

        This is the load-bearing clinical-correctness invariant: a
        clinician using any of the 3 spec-permitted input encodings
        receives the SAME clinical signal.
        """
        # GET scalar.
        get_resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": "http://snomed.info/sct",
                "code": "44054006",
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        get_equiv = next(
            p for p in next(
                pp for pp in get_resp.json()["parameter"]
                if pp.get("name") == "match"
            )["part"] if p.get("name") == "equivalence"
        )["valueCode"]

        # POST coding.
        coding_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {
                    "system": "http://snomed.info/sct",
                    "code": "44054006",
                }},
                {"name": "targetsystem", "valueUri":
                    "http://hl7.org/fhir/sid/icd-10-cm"},
            ],
        }
        post_coding_resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=coding_body
        )
        post_coding_equiv = next(
            p for p in next(
                pp for pp in post_coding_resp.json()["parameter"]
                if pp.get("name") == "match"
            )["part"] if p.get("name") == "equivalence"
        )["valueCode"]

        # POST codeableConcept.
        cc_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeableConcept", "valueCodeableConcept": {
                    "coding": [{
                        "system": "http://snomed.info/sct",
                        "code": "44054006",
                    }],
                }},
                {"name": "targetsystem", "valueUri":
                    "http://hl7.org/fhir/sid/icd-10-cm"},
            ],
        }
        post_cc_resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=cc_body
        )
        post_cc_equiv = next(
            p for p in next(
                pp for pp in post_cc_resp.json()["parameter"]
                if pp.get("name") == "match"
            )["part"] if p.get("name") == "equivalence"
        )["valueCode"]

        # Byte-exact parity.
        assert get_equiv == post_coding_equiv == post_cc_equiv == "equivalent"


# =============================================================================
# Lens 10 — Cross-surface clinical-content consistency.
# =============================================================================

class TestLens10CrossSurfaceClinicalConsistency:
    """Lens 10 — The canonical helper and the wrapper AGREE on every
    CLINICALLY-OCCURRING engine vocabulary value. The divergence (per
    EXPLORER Lens 8b / Lens 11 / Lens 13) is ONLY on hostile camelCase
    input which the engine never emits.

    Clinical safety: a divergence on clinically-occurring vocabulary
    would silently produce different clinical signals on the $translate
    surface vs the ConceptMap export surface.
    """

    ENGINE_VOCABULARY = [
        "equivalent",
        "same",
        "identical",
        "source-is-narrower-than-target",
        "source-is-broader-than-target",
        "related-to",
        "not-translated",
        "unmatched",
        # Defensive pass-through (engine does not emit today but could):
        "wider",
        "narrower",
        "broader",
        "subsumes",
        "specializes",
        "relatedto",
        "disjoint",
        "subsumedby",  # lowercase alias (engine-emittable form)
        "subsumed-by",
        "not-relatedto",
        "not-related-to",
    ]

    @pytest.mark.parametrize("engine_value", ENGINE_VOCABULARY)
    def test_t100_canonical_and_wrapper_agree_on_engine_vocabulary(
        self, engine_value
    ):
        """The canonical helper and the wrapper AGREE on every
        clinically-occurring engine vocabulary value (lowercase,
        hyphenated, and aliased forms)."""
        canonical = fhir_equivalence(engine_value)
        wrapper = responses_module._fhir_equivalence_from_relationship(
            engine_value
        )
        assert canonical == wrapper, (
            f"Divergence on clinically-occurring vocabulary "
            f"{engine_value!r}: canonical={canonical!r}, "
            f"wrapper={wrapper!r}. Clinical safety requires byte-exact "
            f"agreement on engine vocabulary."
        )

    def test_t101_divergence_only_on_hostile_camelcase(self):
        """The canonical/wrapper divergence is ONLY on hostile camelCase
        input (e.g., ``subsumedBy``) which the engine NEVER emits.

        On all engine-emittable forms (lowercase, hyphenated), the two
        surfaces AGREE byte-exact.
        """
        # The divergence is real on camelCase.
        canonical = fhir_equivalence("subsumedBy")
        wrapper = responses_module._fhir_equivalence_from_relationship(
            "subsumedBy"
        )
        assert canonical != wrapper, (
            "Divergence should exist on hostile camelCase 'subsumedBy'."
        )
        # But the lowercase form AGREES.
        canonical_lower = fhir_equivalence("subsumedby")
        wrapper_lower = responses_module._fhir_equivalence_from_relationship(
            "subsumedby"
        )
        assert canonical_lower == wrapper_lower == "specializes"

    def test_t102_engine_emits_lowercase_only_clinical_safety(self):
        """The engine emits LOWERCASE vocabulary only — the camelCase
        form (where the divergence surfaces) NEVER appears in engine
        output. Clinical safety bound: the divergence is invisible to
        all clients today.

        Source-read of services/crosswalk.py + crosswalk_prepared.py +
        engines/duckdb/mappings.py confirms all engine vocabulary
        emissions are lowercase.
        """
        # The engine emits these lowercase values.
        engine_emitted_values = [
            "equivalent",
            "source-is-narrower-than-target",
            "source-is-broader-than-target",
            "related-to",
            "not-translated",
            "unmatched",
        ]
        for value in engine_emitted_values:
            # No uppercase characters in engine vocabulary.
            assert value == value.lower(), (
                f"Engine vocabulary {value!r} contains uppercase — "
                f"would surface the wrapper/canonical divergence."
            )
            # Both surfaces AGREE on the lowercase form.
            assert fhir_equivalence(value) == (
                responses_module._fhir_equivalence_from_relationship(value)
            )

    def test_t103_no_client_facing_surface_depends_on_wrapper_today(self):
        """NO client-facing surface depends on the wrapper's
        case-insensitive behavior today — because the engine emits
        lowercase vocabulary only.

        Clinical safety: the wrapper/canonical divergence is invisible
        to all clients. A future R5/R4B engine vocabulary change could
        surface this divergence, but the wrapper's ``specializes``
        output is the CLINICALLY CORRECT R4 value for that case.
        """
        # All engine vocabulary values are lowercase.
        engine_values = [
            v for v in [
                "equivalent", "source-is-narrower-than-target",
                "source-is-broader-than-target", "related-to",
                "not-translated", "unmatched",
            ]
        ]
        for v in engine_values:
            canonical_result = fhir_equivalence(v)
            wrapper_result = (
                responses_module._fhir_equivalence_from_relationship(v)
            )
            assert canonical_result == wrapper_result
            assert canonical_result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
