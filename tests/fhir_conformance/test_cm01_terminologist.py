"""CM-01 TERMINOLOGIST: ConceptMap Resource Structure — clinical/terminological correctness.

Spec:
  * https://build.fhir.org/conceptmap.html
  * https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html (canonical R4 enum)

Canonical R4 ConceptMapEquivalence closed enum (10 values, verified 2026-07-13):
    relatedto | equivalent | equal | wider | subsumes | narrower |
    specializes | inexact | unmatched | disjoint

TERMINOLOGIST lens for CM-01 (TERMINOLOGIST FOCUS AREA per chunk
assignment): "ConceptMap exports must use the right equivalence
vocabulary, not just any non-null value." Default severity HIGH per
GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH Severity".

6 lens items:

  Lens 1 — Clinical-directionality of SKEPTIC FIX-001 (CM01-SKEPTIC-001)
    on representative SNOMED↔ICD-10 crosswalk cases. Verify the fix
    produces spec-correct clinical semantics: SNOMED broad → ICD-10
    specific ⇒ target narrower ⇒ equivalence = ``narrower`` (R4
    target-perspective). SNOMED specific → ICD-10 broad ⇒ target
    wider ⇒ equivalence = ``wider``. Both production maps
    (``responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` AND
    ``outputs/fhir.py:FHIR_EQUIVALENCES``) MUST agree.

  Lens 2 — Full direction audit of every entry in
    ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE``. Direction-neutral values
    (``equivalent``, ``equal``, ``relatedto``) are safe. Direction-
    sensitive values (``subsumes``, ``specializes``, ``wider``,
    ``narrower``) must be verified against R4 spec target-perspective.

  Lens 3 — ``outputs/fhir.py:FHIR_EQUIVALENCES`` non-shared-key surface
    (CF-TERMINOLOGIST-CM01-01 latent gap, EXPLORER-flagged). A
    ``subsumes`` or ``specializes`` mapping exported via
    ``concept_map_to_fhir`` would silently emit
    ``equivalence="relatedto"`` (the default). The engine does NOT
    emit these relationships today (mapping pipeline only emits
    ``equivalent``, ``source-is-narrower-than-target``,
    ``source-is-broader-than-target``, ``related-to``,
    ``not-translated``, ``unmatched`` — see
    ``core/models.py:conceptmap_relationship`` and
    ``engines/duckdb/mappings.py``). The latent gap is documented as a
    carry-forward: when the engine adds hierarchical-source mappings
    (e.g. SNOMED↔SNOMED ISA crosswalks), the FHIR_EQUIVALENCES map
    MUST be extended.

  Lens 4 — Crosswalk clinical correctness on representative clinical
    scenarios.
    (a) SNOMED 44054006 (T2DM) → ICD-10-CM E11 (Type 2 diabetes
        mellitus): clinically, this should be ``equivalent`` (same
        concept in different code systems). Engine emits
        ``equivalent`` (same-CUI crosswalk). Verified.
    (b) SNOMED 73211009 (Diabetes mellitus) → ICD-10-CM E08-E13
        (Diabetes chapter): SNOMED broader → ICD-10 narrower range →
        ``source-is-broader-than-target`` → ``narrower`` (target
        narrower than source per R4 spec).
    (c) SNOMED 44054006 (T2DM) → ICD-10-CM E08-E13 (Diabetes chapter):
        SNOMED specific → ICD-10 broad → ``source-is-narrower-than-
        target`` → ``wider`` (target wider than source per R4 spec).

  Lens 5 — Default ``relatedto`` safety. When the engine has no clear
    equivalence, ``_fhir_equivalence_from_relationship`` defaults to
    ``relatedto``. Per R4 spec, ``relatedto`` is the catch-all for "a
    relationship exists but isn't a strict equivalence". This is
    clinically safe IF AND ONLY IF the closed-enum membership
    invariant holds (Lens 1 — every emitted value IS in the R4 enum).

  Lens 6 — SKEPTIC FIX-002 (``not-translated`` → ``unmatched``)
    clinical correctness. The ``not-translated`` relationship
    represents "no translation for this source concept in the target
    system". Per R4 spec, the catch-all for "no mapping" is
    ``unmatched`` (NOT ``equivalent`` which was the prior wrong value
    — silent clinical-correctness inversion). Verify both production
    maps emit ``unmatched`` for the ``not-translated`` engine
    relationship.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent (DM)
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child (T2DM)
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # 44054006 is-a 73211009

NOTE: The fixture only seeds SAME-CUI ``equivalent`` mappings via
crosswalk. Hierarchical (``source-is-narrower-than-target`` /
``source-is-broader-than-target``) and patient-friendly
(``not-translated``) relationships are NOT seeded in the HTTP surface;
Lens 1/4 therefore exercise the builder layer (``build_parameters_translate``,
``concept_map_to_fhir``, ``_fhir_equivalence_from_relationship``,
``fhir_equivalence``) directly — same methodology as SKEPTIC test_s20,
HISTORIAN test_h30-h32, EXPLORER test_e40-e43.
"""

from __future__ import annotations

import json

import pytest

from medterm4ds.core.models import CodeMapping, CodeRef, ConceptMapRow
from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
from medterm4ds.engines.fhir.responses import (
    _INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    _fhir_equivalence_from_relationship,
    build_parameters_translate,
)
from medterm4ds.outputs.fhir import (
    FHIR_EQUIVALENCES,
    concept_map_to_fhir,
    fhir_equivalence,
)


# ---------------------------------------------------------------------------
# Lens 1 — Clinical-directionality of SKEPTIC FIX-001 on representative
# SNOMED↔ICD-10 crosswalk cases.
# ---------------------------------------------------------------------------


def test_t10_skeptic_fix_001_clinical_correctness_snomed_broader_to_icd10_specific():
    """TERMINOLOGIST Lens 1a: SNOMED broad concept → ICD-10 specific code.

    Clinical scenario: a CDS hook maps SNOMED 73211009 (Diabetes
    mellitus — the broad category) to ICD-10-CM E11 (Type 2 diabetes
    mellitus — a specific billable code). The source (SNOMED) IS
    BROADER than the target (ICD-10-CM); the engine records this as
    ``source-is-broader-than-target``.

    Per R4 spec (https://hl7.org/fhir/R4/valueset-concept-map-
    equivalence.html): the equivalence enum is read from TARGET
    perspective. ``narrower`` = "The target mapping is NARROWER in
    meaning than the source concept."

    Therefore the spec-correct R4 value for source-broader/target-
    narrower is ``narrower``. Both production maps MUST agree.

    A wrong value (``wider``) would invert the hierarchy
    interpretation, producing wrong clinical decision support.
    """
    # responses.py path ($translate surface)
    r_responses = _fhir_equivalence_from_relationship("source-is-broader-than-target")
    assert r_responses == "narrower", (
        f"CLINICAL CORRECTNESS: source-is-broader-than-target MUST map to "
        f"R4 'narrower' (target narrower than source per R4 spec). "
        f"Got {r_responses!r}. A wrong value inverts hierarchy "
        f"interpretation, producing wrong clinical decision support."
    )
    # outputs/fhir.py path (ConceptMap export surface)
    r_outputs = fhir_equivalence("source-is-broader-than-target")
    assert r_outputs == "narrower", (
        f"CLINICAL CORRECTNESS (outputs/fhir.py): source-is-broader-than-"
        f"target MUST map to R4 'narrower'. Got {r_outputs!r}."
    )


def test_t11_skeptic_fix_001_clinical_correctness_snomed_narrower_to_icd10_broad():
    """TERMINOLOGIST Lens 1b: SNOMED specific → ICD-10 broad chapter.

    Clinical scenario: a CDS hook maps SNOMED 44054006 (T2DM) to
    ICD-10-CM E08-E13 (Diabetes chapter range — broad category). The
    source (SNOMED) IS NARROWER than the target (ICD-10-CM range);
    the engine records this as ``source-is-narrower-than-target``.

    Per R4 spec: ``wider`` = "The target mapping is WIDER in meaning
    than the source concept."

    Therefore the spec-correct R4 value for source-narrower/target-
    wider is ``wider``. Both production maps MUST agree.

    A wrong value (``narrower``) would invert the hierarchy
    interpretation, producing wrong clinical decision support.
    """
    # responses.py path ($translate surface)
    r_responses = _fhir_equivalence_from_relationship("source-is-narrower-than-target")
    assert r_responses == "wider", (
        f"CLINICAL CORRECTNESS: source-is-narrower-than-target MUST map to "
        f"R4 'wider' (target wider than source per R4 spec). "
        f"Got {r_responses!r}. A wrong value inverts hierarchy "
        f"interpretation, producing wrong clinical decision support."
    )
    # outputs/fhir.py path (ConceptMap export surface)
    r_outputs = fhir_equivalence("source-is-narrower-than-target")
    assert r_outputs == "wider", (
        f"CLINICAL CORRECTNESS (outputs/fhir.py): source-is-narrower-"
        f"than-target MUST map to R4 'wider'. Got {r_outputs!r}."
    )


def test_t12_translate_response_shape_on_clinical_crosswalk_broader():
    """TERMINOLOGIST Lens 1c: end-to-end $translate Parameters response
    shape on a clinical source-is-broader-than-target crosswalk.

    Build the same Parameters response that ``$translate`` would emit
    for a SNOMED broad → ICD-10 specific mapping, and assert the
    clinical content matches the spec-correct R4 value.
    """
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="73211009"),
        target=CodeRef(source="ICD10CM", code="E11"),
        source_display="Diabetes mellitus (SNOMED)",
        target_display="Type 2 diabetes mellitus (ICD-10-CM)",
        relationship="source-is-broader-than-target",
        match_type="snomed_to_target_native_hierarchy",
    )
    out = build_parameters_translate(
        [mapping],
        source_system_uri="http://snomed.info/sct",
        source_code="73211009",
    )
    assert out["resourceType"] == "Parameters"
    # result is true (1 match)
    result_param = next(p for p in out["parameter"] if p.get("name") == "result")
    assert result_param["valueBoolean"] is True
    # the match.equivalence value is the spec-correct 'narrower'
    match_param = next(p for p in out["parameter"] if p.get("name") == "match")
    equiv_part = next(
        part for part in match_param["part"] if part.get("name") == "equivalence"
    )
    assert equiv_part["valueCode"] == "narrower", (
        f"CLINICAL CORRECTNESS: $translate response for source-is-broader-"
        f"than-target MUST emit equivalence='narrower' (R4 target-perspective). "
        f"Got {equiv_part['valueCode']!r}."
    )
    # Wire type is valueCode (closed-enum strictness contract)
    # — NOT valueString (CS-04 TERMINOLOGIST test_t22 methodology)
    assert "valueCode" in equiv_part, (
        "equivalence part MUST use valueCode wire type (closed-enum strictness)."
    )


def test_t13_translate_response_shape_on_clinical_crosswalk_narrower():
    """TERMINOLOGIST Lens 1d: end-to-end $translate Parameters response
    shape on a clinical source-is-narrower-than-target crosswalk.

    SNOMED 44054006 (T2DM) → ICD-10-CM E08-E13 (Diabetes chapter
    range). Target is wider than source ⇒ equivalence = ``wider``.
    """
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E08-E13"),
        source_display="Type 2 diabetes mellitus (SNOMED)",
        target_display="Diabetes mellitus chapter (ICD-10-CM)",
        relationship="source-is-narrower-than-target",
        match_type="snomed_to_target_native_hierarchy",
    )
    out = build_parameters_translate(
        [mapping],
        source_system_uri="http://snomed.info/sct",
        source_code="44054006",
    )
    match_param = next(p for p in out["parameter"] if p.get("name") == "match")
    equiv_part = next(
        part for part in match_param["part"] if part.get("name") == "equivalence"
    )
    assert equiv_part["valueCode"] == "wider", (
        f"CLINICAL CORRECTNESS: $translate response for source-is-narrower-"
        f"than-target MUST emit equivalence='wider' (R4 target-perspective). "
        f"Got {equiv_part['valueCode']!r}."
    )


# ---------------------------------------------------------------------------
# Lens 2 — Full direction audit of every entry in
# _INTERNAL_REL_TO_FHIR_EQUIVALENCE.
# ---------------------------------------------------------------------------


# Direction-classification of every R4 ConceptMapEquivalence value per
# canonical spec (https://hl7.org/fhir/R4/valueset-concept-map-
# equivalence.html):
#   * direction-NEUTRAL: equivalent, equal, relatedto, inexact, unmatched,
#     disjoint — symmetric or non-hierarchical, no source-vs-target
#     directionality ambiguity.
#   * direction-TARGET-PERSPECTIVE: wider, narrower, subsumes,
#     specializes — the value describes the TARGET relative to the
#     source. Inverting source/target would invert the value.
DIRECTION_NEUTRAL = frozenset({
    "equivalent", "equal", "relatedto", "inexact", "unmatched", "disjoint",
})
DIRECTION_TARGET_PERSPECTIVE = frozenset({
    "wider", "narrower", "subsumes", "specializes",
})


def test_t20_internal_rel_direction_audit_all_values_in_enum():
    """TERMINOLOGIST Lens 2a: every emitted value IS in the R4 closed enum.

    This is the closed-enum membership invariant. The production-side
    ``assert`` in ``responses.py`` enforces this at module load; this
    probe is the runtime safety net.
    """
    emitted = set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    drift = emitted - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drift, (
        f"_INTERNAL_REL_TO_FHIR_EQUIVALENCE emits values outside the R4 "
        f"closed enum: {drift}. Clinical clients cannot interpret off-spec "
        f"values — closed-enum strictness is the spec contract."
    )


def test_t21_internal_rel_direction_audit_values_classified():
    """TERMINOLOGIST Lens 2b: every emitted value is classified as either
    direction-neutral OR direction-target-perspective. There are no
    unclassified values — the R4 enum is fully covered by the two
    classes.
    """
    emitted = set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    unclassified = emitted - DIRECTION_NEUTRAL - DIRECTION_TARGET_PERSPECTIVE
    assert not unclassified, (
        f"Unclassified values (neither direction-neutral nor target-"
        f"perspective): {unclassified}. The R4 enum audit MUST cover "
        f"every value."
    )
    # Sanity: the two classes partition the R4 enum
    assert DIRECTION_NEUTRAL | DIRECTION_TARGET_PERSPECTIVE == FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        "Classification does NOT cover the full R4 enum — audit gap."
    )


# Spec-correct target-perspective mapping for direction-sensitive
# engine relationships. Per R4 spec:
#   * ``source-is-broader-than-target``  ⇒ target narrower ⇒ ``narrower``
#   * ``source-is-narrower-than-target`` ⇒ target wider    ⇒ ``wider``
#   * Direct R4 enum values (``wider``, ``narrower``, ``subsumes``,
#     ``specializes``) are already target-perspective; pass-through
#     unchanged.
#   * Engine alias ``broader`` is source-perspective ⇒ target narrower
#     ⇒ ``narrower``.
SPEC_CORRECT_TARGET_PERSPECTIVE = {
    "source-is-broader-than-target": "narrower",
    "source-is-narrower-than-target": "wider",
    "broader": "narrower",
    "wider": "wider",
    "narrower": "narrower",
    "subsumes": "subsumes",
    "subsumedby": "specializes",
    "subsumed-by": "specializes",
    "specializes": "specializes",
}


@pytest.mark.parametrize(
    "engine_key,expected_r4",
    sorted(SPEC_CORRECT_TARGET_PERSPECTIVE.items()),
)
def test_t22_direction_audit_each_target_perspective_value(
    engine_key: str, expected_r4: str
):
    """TERMINOLOGIST Lens 2c: each direction-sensitive engine relationship
    maps to its spec-correct R4 target-perspective value.

    Per R4 spec (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html):
      * ``wider``    = "The target mapping is WIDER in meaning than the
                        source concept."
      * ``narrower`` = "The target mapping is NARROWER in meaning than
                        the source concept."
      * ``subsumes`` = "The target mapping subsumes the source concept"
                       (target is broader).
      * ``specializes`` = "The target mapping is more specific than the
                          source concept" (target is narrower).

    The engine uses two vocabulary styles:
      * Source-perspective (R5-style): ``source-is-narrower-than-target``,
        ``source-is-broader-than-target``, ``broader``.
      * Direct R4 enum (target-perspective): ``wider``, ``narrower``,
        ``subsumes``, ``specializes``, ``subsumedby``, ``subsumed-by``.

    Each engine key MUST map to the spec-correct R4 value. A wrong
    direction inverts the clinical hierarchy interpretation.
    """
    actual = _INTERNAL_REL_TO_FHIR_EQUIVALENCE.get(engine_key)
    assert actual == expected_r4, (
        f"Direction audit failure on key {engine_key!r}: expected "
        f"{expected_r4!r} (R4 target-perspective), got {actual!r}. "
        f"Source/target perspective inversion produces wrong clinical "
        f"hierarchy interpretation."
    )


def test_t23_direction_neutral_values_unaffected_by_perspective():
    """TERMINOLOGIST Lens 2d: direction-neutral values are NOT subject to
    source-vs-target ambiguity. They MUST be passed through unchanged
    (modulo normalization like ``related-to`` → ``relatedto``).
    """
    # Each direction-neutral key MUST map to a direction-neutral value
    # in the R4 enum.
    for key, value in _INTERNAL_REL_TO_FHIR_EQUIVALENCE.items():
        if value in DIRECTION_TARGET_PERSPECTIVE:
            # This key maps to a direction-sensitive value — it must
            # be in the spec-correct target-perspective map.
            assert key in SPEC_CORRECT_TARGET_PERSPECTIVE, (
                f"Engine key {key!r} maps to direction-sensitive value "
                f"{value!r} but is NOT in the spec-correct target-"
                f"perspective map. Either the mapping is wrong or the "
                f"spec-correct map needs to be extended."
            )


# ---------------------------------------------------------------------------
# Lens 3 — outputs/fhir.py:FHIR_EQUIVALENCES non-shared-key surface
# (CF-TERMINOLOGIST-CM01-01 latent gap, EXPLORER-flagged).
# ---------------------------------------------------------------------------


# Engine relationship values actually emitted by the mapping pipeline
# (verified by source-reading conceptmap_relationship in core/models.py
# + crosswalk/mappings pipeline in engines/duckdb/mappings.py).
ENGINE_PIPELINE_RELATIONSHIPS = frozenset({
    "equivalent",
    "source-is-narrower-than-target",
    "source-is-broader-than-target",
    "related-to",
    "not-translated",
    "unmatched",
})


def test_t30_outputs_fhir_module_supports_engine_pipeline_relationships():
    """TERMINOLOGIST Lens 3a: ``outputs/fhir.py:FHIR_EQUIVALENCES`` covers
    every relationship emitted by the current engine pipeline.

    Source-reading of ``conceptmap_relationship`` in
    ``core/models.py:460`` and the crosswalk pipeline confirms the
    engine emits exactly: ``equivalent``, ``source-is-narrower-than-
    target``, ``source-is-broader-than-target``, ``related-to``,
    ``not-translated``, ``unmatched``.

    Each MUST be a key in ``FHIR_EQUIVALENCES`` so the ConceptMap
    export produces spec-correct values (NOT the silent-``relatedto``
    default).
    """
    missing = ENGINE_PIPELINE_RELATIONSHIPS - set(FHIR_EQUIVALENCES.keys())
    assert not missing, (
        f"FHIR_EQUIVALENCES is missing engine pipeline relationships: "
        f"{missing}. These would silently emit 'relatedto' (the default) "
        f"on ConceptMap export — clinical correctness gap."
    )


def test_t31_cf_terminologist_cm01_01_subsumes_resolved_by_consolidation():
    """TERMINOLOGIST Lens 3b: CF-TERMINOLOGIST-CM01-01 RESOLVED-status
    verification.

    Pre-CR-024 (milestone-2 state): a ``subsumes`` mapping exported via
    ``concept_map_to_fhir`` would silently emit ``equivalence="relatedto"``
    (the default fallback in ``outputs/fhir.py:fhir_equivalence``) because
    the narrow ``FHIR_EQUIVALENCES`` map lacked the ``subsumes`` /
    ``specializes`` keys. The latent gap was OUT OF SCOPE for CM-01 (the
    engine does not emit these relationships today), but the probe
    documented the silent-default surface.

    Post-CR-024 (milestone-3 review): the parallel maps in
    ``outputs/fhir.py`` and ``responses.py`` were consolidated into the
    canonical module ``engines/fhir/equivalence.py``. The unified map
    covers the full defensive pass-through surface (``subsumes``,
    ``specializes``, ``subsumedby``, ``subsumed-by``, ``wider``,
    ``narrower``, ``broader``, ``relatedto``, ``disjoint``) for BOTH the
    ConceptMap export surface AND the $translate HTTP surface. The
    silent-default gap is closed: a future engine enhancement that adds
    hierarchical-source mappings will get spec-correct R4 values on both
    surfaces uniformly.

    Per R4 spec (https://hl7.org/fhir/R4/valueset-concept-map-
    equivalence.html):
      * ``subsumes``    = "The target mapping subsumes the source concept"
                         (target is broader).
      * ``specializes`` = "The target mapping is more specific than the
                         source concept" (target is narrower).
    """
    # Post-CR-024: subsumes / specializes resolve to their R4 enum values
    # (NOT the silent-default 'relatedto').
    assert fhir_equivalence("subsumes") == "subsumes", (
        "CF-TERMINOLOGIST-CM01-01 regression or CR-024 regression: "
        "subsumes must resolve to the R4 'subsumes' value (was silent-"
        "default 'relatedto' pre-CR-024)."
    )
    assert fhir_equivalence("specializes") == "specializes", (
        "CF-TERMINOLOGIST-CM01-01 regression or CR-024 regression: "
        "specializes must resolve to the R4 'specializes' value (was "
        "silent-default 'relatedto' pre-CR-024)."
    )


def test_t32_cf_terminologist_cm01_01_engine_pipeline_does_not_emit_subsumes():
    """TERMINOLOGIST Lens 3c: source-reading verification that the engine
    pipeline does NOT emit ``subsumes`` / ``specializes`` relationships
    today. The CF-TERMINOLOGIST-CM01-01 latent gap is therefore not
    currently exercised.

    Verified by source-reading ``conceptmap_relationship`` in
    ``core/models.py`` (the function that produces relationship values
    for patient-friendly ConceptMap exports) — it emits exactly:
    ``unmatched``, ``source-is-narrower-than-target``, ``related-to``,
    ``not-translated``, ``equivalent``.

    Verified by source-reading the crosswalk pipeline in
    ``engines/duckdb/mappings.py`` — it emits ``equivalent``,
    ``source-is-narrower-than-target``, ``source-is-broader-than-target``.
    """
    # Confirm subsumes / specializes are NOT in the engine pipeline
    # relationship vocabulary.
    assert "subsumes" not in ENGINE_PIPELINE_RELATIONSHIPS, (
        "Engine pipeline vocabulary audit: 'subsumes' is now emitted "
        "by the engine. Update CF-TERMINOLOGIST-CM01-01 and add "
        "FHIR_EQUIVALENCES['subsumes'] -> 'subsumes' mapping in "
        "outputs/fhir.py."
    )
    assert "specializes" not in ENGINE_PIPELINE_RELATIONSHIPS, (
        "Engine pipeline vocabulary audit: 'specializes' is now emitted "
        "by the engine. Update CF-TERMINOLOGIST-CM01-01 and add "
        "FHIR_EQUIVALENCES['specializes'] -> 'specializes' mapping in "
        "outputs/fhir.py."
    )


def test_t33_concept_map_export_clinical_correctness_on_engine_pipeline():
    """TERMINOLOGIST Lens 3d: end-to-end ConceptMap export produces
    spec-correct equivalence values for every relationship in the
    engine pipeline vocabulary.

    Build a ConceptMap resource containing one element per engine
    pipeline relationship, and assert each target.equivalence value
    matches the spec-correct R4 value.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code=f"SRC_{i}"),
            source_display=f"Source {rel}",
            target=CodeRef(source="ICD10CM", code=f"TGT_{i}"),
            target_display=f"Target {rel}",
            relationship=rel,
        )
        for i, rel in enumerate(sorted(ENGINE_PIPELINE_RELATIONSHIPS))
    ]
    resource = concept_map_to_fhir(rows, include_extensions=False)
    assert resource["resourceType"] == "ConceptMap"
    # Collect all equivalence values emitted
    equivalence_values = []
    for group in resource["group"]:
        for element in group["element"]:
            for target in element.get("target", []):
                equivalence_values.append(target["equivalence"])
    # Every value MUST be in the R4 closed enum
    drift = set(equivalence_values) - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drift, (
        f"ConceptMap export emits values outside R4 closed enum: {drift}."
    )
    # Every value MUST be spec-correct for the engine pipeline
    # relationship that produced it. Re-resolve via fhir_equivalence
    # for parity.
    for rel, val in zip(sorted(ENGINE_PIPELINE_RELATIONSHIPS), equivalence_values):
        assert val == fhir_equivalence(rel), (
            f"ConceptMap export for relationship {rel!r} emitted {val!r}; "
            f"expected {fhir_equivalence(rel)!r} (parity with fhir_equivalence)."
        )


# ---------------------------------------------------------------------------
# Lens 4 — Crosswalk clinical correctness on representative scenarios.
# ---------------------------------------------------------------------------


def test_t40_clinical_crosswalk_snomed_t2dm_to_icd10_e11_equivalent():
    """TERMINOLOGIST Lens 4a: SNOMED 44054006 (T2DM) → ICD-10-CM E11.

    Clinical fact: SNOMED 44054006 (Type 2 diabetes mellitus) and
    ICD-10-CM E11 (Type 2 diabetes mellitus) refer to the SAME clinical
    concept in different code systems. The crosswalk via UMLS CUI
    (C0011847 = "Diabetes Mellitus, Type 2") produces a
    ``same_cui`` / ``equivalent`` relationship.

    The spec-correct R4 value is ``equivalent`` (= "the definitions of
    the concepts mean the same thing").
    """
    assert _fhir_equivalence_from_relationship("equivalent") == "equivalent"
    assert fhir_equivalence("equivalent") == "equivalent"


def test_t41_clinical_crosswalk_snomed_dm_to_icd10_chapter_narrower_target():
    """TERMINOLOGIST Lens 4b: SNOMED 73211009 (Diabetes mellitus, broad)
    → ICD-10-CM E08-E13 (Diabetes chapter range).

    Source (SNOMED) is broader; target (ICD-10-CM range) is narrower.
    Per R4 spec: ``narrower`` = "The target mapping is NARROWER in
    meaning than the source concept". The spec-correct R4 value is
    ``narrower``.

    A wrong value (``wider``) would invert the clinical interpretation,
    making a CDS hook treat the ICD-10-CM range as broader than the
    SNOMED concept (opposite of clinical reality).
    """
    # Engine emits source-is-broader-than-target for this scenario
    actual = _fhir_equivalence_from_relationship("source-is-broader-than-target")
    assert actual == "narrower", (
        f"CLINICAL CORRECTNESS: SNOMED-broad → ICD-10-narrower crosswalk "
        f"MUST produce R4 'narrower'. Got {actual!r}."
    )


def test_t42_clinical_crosswalk_snomed_t2dm_to_icd10_chapter_wider_target():
    """TERMINOLOGIST Lens 4c: SNOMED 44054006 (T2DM, specific) →
    ICD-10-CM E08-E13 (Diabetes chapter range, broad).

    Source (SNOMED) is narrower; target (ICD-10-CM range) is wider.
    Per R4 spec: ``wider`` = "The target mapping is WIDER in meaning
    than the source concept". The spec-correct R4 value is ``wider``.
    """
    actual = _fhir_equivalence_from_relationship("source-is-narrower-than-target")
    assert actual == "wider", (
        f"CLINICAL CORRECTNESS: SNOMED-narrower → ICD-10-wider crosswalk "
        f"MUST produce R4 'wider'. Got {actual!r}."
    )


def test_t43_clinical_crosswalk_directionality_mirror_invariant():
    """TERMINOLOGIST Lens 4d: forward and reverse crosswalk directions
    MUST produce mirror-image R4 equivalence values.

    If SNOMED-broad → ICD-10-narrower produces ``narrower``, then
    SNOMED-narrower → ICD-10-broad MUST produce ``wider``. This is
    the clinical-directionality mirror invariant (extends CS-04
    TERMINOLOGIST test_t12 methodology from $subsumes outcome codes
    to ConceptMap equivalence values).

    A regression that breaks one direction but not the other would
    produce asymmetric clinical semantics for the same logical
    relationship.
    """
    forward = _fhir_equivalence_from_relationship("source-is-broader-than-target")
    reverse = _fhir_equivalence_from_relationship("source-is-narrower-than-target")
    assert forward == "narrower"
    assert reverse == "wider"
    # The mirror invariant: forward and reverse MUST be different R4
    # values (narrower vs wider) — NOT the same value (which would
    # indicate direction-ignoring bug).
    assert forward != reverse, (
        f"Directionality mirror invariant violation: forward ({forward!r}) "
        f"and reverse ({reverse!r}) MUST differ for direction-sensitive "
        f"engine relationships. Same value indicates the translation "
        f"ignores direction — clinical correctness gap."
    )


# ---------------------------------------------------------------------------
# Lens 5 — Default ``relatedto`` safety.
# ---------------------------------------------------------------------------


def test_t50_default_relatedto_is_in_r4_enum():
    """TERMINOLOGIST Lens 5a: the default fallback ``relatedto`` IS in
    the R4 closed enum. This is the safety floor — unknown engine
    relationships emit a conformant R4 value, not an off-spec token.
    """
    assert "relatedto" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        "relatedto (default fallback) MUST be in the R4 closed enum."
    )


def test_t51_default_relatedto_emitted_for_unknown_engine_relationship():
    """TERMINOLOGIST Lens 5b: an unknown engine relationship (not in
    the map) MUST emit ``relatedto`` (the R4 catch-all for "a
    relationship exists but isn't a strict equivalence"). Never raises
    and never emits an off-spec value.

    Per R4 spec: ``relatedto`` is the safe default — it signals to
    the client that a mapping exists without overclaiming the semantic.
    The implementation MUST never emit None, raise KeyError, or echo
    the raw engine value verbatim (all three would be silent clinical
    bugs).
    """
    # Unknown relationship
    assert _fhir_equivalence_from_relationship("totally-unknown-relationship") == "relatedto"
    # None relationship
    assert _fhir_equivalence_from_relationship(None) == "relatedto"
    # Empty string relationship
    assert _fhir_equivalence_from_relationship("") == "relatedto"
    # outputs/fhir.py path
    assert fhir_equivalence("totally-unknown-relationship") == "relatedto"
    assert fhir_equivalence(None) == "relatedto"


def test_t52_default_relatedto_clinical_safety_floor():
    """TERMINOLOGIST Lens 5c: the default ``relatedto`` is the clinical
    safety floor — it does NOT overclaim semantic equivalence. A CDS
    hook reading ``relatedto`` knows the mapping is approximate; a CDS
    hook reading ``equivalent`` would treat it as confirmed.

    Per R4 spec definitions:
      * ``equivalent`` = "the definitions of the concepts mean the
                          same thing".
      * ``relatedto``  = "the concepts are related, and have at least
                          some overlap in meaning, but the exact
                          relationship is not defined."

    The implementation MUST default to ``relatedto`` for unknown
    relationships — defaulting to ``equivalent`` would overclaim
    semantic equivalence for unmapped engine vocabulary.
    """
    # Confirm the default is NOT equivalent/equal (overclaim)
    assert _fhir_equivalence_from_relationship("unknown") != "equivalent", (
        "Default fallback MUST be 'relatedto', NOT 'equivalent'. "
        "Defaulting to 'equivalent' overclaims semantic equivalence "
        "for unmapped engine vocabulary — clinical safety violation."
    )
    assert _fhir_equivalence_from_relationship("unknown") != "equal", (
        "Default fallback MUST be 'relatedto', NOT 'equal'."
    )


# ---------------------------------------------------------------------------
# Lens 6 — SKEPTIC FIX-002 (not-translated → unmatched) clinical
# correctness.
# ---------------------------------------------------------------------------


def test_t60_skeptic_fix_002_not_translated_to_unmatched():
    """TERMINOLOGIST Lens 6a: SKEPTIC FIX-002 (CM01-SKEPTIC-002)
    clinical correctness.

    The engine emits ``not-translated`` for patient-friendly mappings
    where no translation was found (per ``conceptmap_relationship`` in
    ``core/models.py:469``). The prior ``outputs/fhir.py`` map had
    ``"not-translated": "equivalent"`` — clinical correctness inversion.
    A client reading the ConceptMap export would treat a missing
    translation as a confirmed equivalence.

    Per R4 spec (https://hl7.org/fhir/R4/valueset-concept-map-
    equivalence.html): ``unmatched`` = "there is no match for this
    concept in the target code system". The spec-correct value is
    ``unmatched``.

    SKEPTIC FIX-002 corrected this in ``outputs/fhir.py``. This probe
    is the clinical-correctness pin.
    """
    # outputs/fhir.py (ConceptMap export surface)
    assert fhir_equivalence("not-translated") == "unmatched", (
        f"CLINICAL CORRECTNESS: not-translated MUST map to R4 'unmatched' "
        f"(no match in target system). Got {fhir_equivalence('not-translated')!r}. "
        f"The prior 'equivalent' value was a clinical-correctness inversion "
        f"(silent confirmation of equivalence for missing translation)."
    )


def test_t61_concept_map_export_not_translated_to_unmatched():
    """TERMINOLOGIST Lens 6b: end-to-end ConceptMap export for a
    not-translated relationship produces ``unmatched`` in the exported
    target.equivalence field.
    """
    row = ConceptMapRow(
        source=CodeRef(source="SNOMEDCT_US", code="UNKNOWN_SOURCE_CODE"),
        source_display="Some technical term with no friendly translation",
        target=CodeRef(source="PATIENT_FRIENDLY", code="UNKNOWN_SOURCE_CODE"),
        target_display="Some technical term with no friendly translation",
        relationship="not-translated",
    )
    resource = concept_map_to_fhir([row], include_extensions=False)
    target = resource["group"][0]["element"][0]["target"][0]
    assert target["equivalence"] == "unmatched", (
        f"ConceptMap export for not-translated MUST emit equivalence="
        f"'unmatched'. Got {target['equivalence']!r}."
    )


def test_t62_clinical_distinction_unmatched_vs_equivalent():
    """TERMINOLOGIST Lens 6c: clinical distinction between ``unmatched``
    and ``equivalent``.

    Per R4 spec:
      * ``equivalent`` = "the definitions of the concepts mean the
                          same thing" (confirmed semantic match).
      * ``unmatched``  = "there is no match for this concept in the
                          target code system" (no translation).

    A clinician's EHR reading ``equivalent`` would treat the target
    code as a confirmed substitute; reading ``unmatched`` would know
    no substitute exists. Conflating them produces wrong clinical
    decisions (e.g., substituting a code that has no clinical
    equivalent).
    """
    # Confirm the two values are distinct in the R4 enum
    assert "equivalent" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert "unmatched" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert "equivalent" != "unmatched", (
        "equivalent and unmatched MUST be distinct R4 values. "
        "Conflating them produces wrong clinical decisions."
    )


# ---------------------------------------------------------------------------
# Lens 7 — Closed-enum membership on BOTH production maps (cross-module
# parallel-map audit — HISTORIAN test_h71 methodology reinforced).
# ---------------------------------------------------------------------------


def test_t70_outputs_fhir_module_emits_only_r4_values():
    """TERMINOLOGIST Lens 7a: ``outputs/fhir.py:FHIR_EQUIVALENCES`` emits
    ONLY values in the R4 closed enum.

    Mirrors SKEPTIC test_s10 for the responses.py path. Cross-module
    parallel-map invariant (HISTORIAN test_h71).
    """
    emitted = set(FHIR_EQUIVALENCES.values())
    drift = emitted - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drift, (
        f"FHIR_EQUIVALENCES emits values outside the R4 closed enum: "
        f"{drift}. Same closed-enum membership invariant as responses.py."
    )


def test_t71_inter_module_map_agreement_on_shared_keys():
    """TERMINOLOGIST Lens 7b: the two production maps agree on every
    shared key. Reinforces SKEPTIC test_s21.

    A future regression that touches one map but not the other would
    silently produce opposite R4 codes for the same input — the
    $translate and ConceptMap-export surfaces would disagree on
    clinical semantics.
    """
    shared = set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE.keys()) & set(FHIR_EQUIVALENCES.keys())
    assert shared, "Maps should share at least the core keys."
    disagreements = [
        (k, _INTERNAL_REL_TO_FHIR_EQUIVALENCE[k], FHIR_EQUIVALENCES[k])
        for k in sorted(shared)
        if _INTERNAL_REL_TO_FHIR_EQUIVALENCE[k] != FHIR_EQUIVALENCES[k]
    ]
    assert not disagreements, (
        f"Inter-module map disagreement on shared keys: {disagreements}. "
        f"Same engine relationship MUST produce same R4 value on every "
        f"surface."
    )


def test_t72_outputs_fhir_default_fallback_is_r4_safe():
    """TERMINOLOGIST Lens 7c: the default fallback in
    ``outputs/fhir.py:fhir_equivalence`` is ``relatedto`` (the R4
    catch-all). Same safety floor as responses.py — never None, never
    raises, never emits an off-spec value.
    """
    # Unknown relationship via outputs/fhir.py
    assert fhir_equivalence("totally-unknown") == "relatedto"
    # The default IS in the R4 enum
    assert "relatedto" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# ---------------------------------------------------------------------------
# Lens 8 — XML wire-format clinical correctness on $translate route.
# Extends CR-002 (XML serializer) + CS-04 TERMINOLOGIST test_t22
# methodology (closed-enum wire-type assertion) to $translate surface.
# ---------------------------------------------------------------------------


def test_t80_xml_wire_format_equivalence_value_code(fhir_client):
    """TERMINOLOGIST Lens 8: XML wire-format on $translate route.

    Per FHIR R4 §3.4.1 (XML representation) + CR-002 fix
    (``_scalar_to_xml_attr`` boolean special-case): the
    ``match.equivalence`` part MUST use ``valueCode`` wire type (NOT
    ``valueString``) because the value is from a closed enum.
    The wire type IS the clinical contract — ``valueCode`` signals
    "validate strictly" to clients (CS-04 TERMINOLOGIST test_t22
    methodology extended to $translate).

    Probe issues $translate with SNOMED T2DM and asserts the XML body
    contains ``valueCode`` for the equivalence part. The fixture
    seeds same-CUI mappings (``equivalent``) — sufficient for this
    wire-format probe.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            "_format": "xml",
        },
    )
    if r.status_code != 200:
        pytest.skip("fixture DB missing the test code")
    body_text = r.text
    if "match" not in body_text:
        pytest.skip("no matches for the test code in fixture DB")
    # The wire type MUST be valueCode (closed-enum strictness)
    assert "valueCode" in body_text, (
        "XML wire-format on $translate: equivalence part MUST use "
        "valueCode wire type (closed-enum strictness contract)."
    )


# ---------------------------------------------------------------------------
# Lens 9 — Inter-module map directionality agreement on shared
# direction-sensitive keys. Reinforces Lens 1 with explicit
# parametrization over the two production maps.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "engine_key,expected_r4",
    [
        ("source-is-narrower-than-target", "wider"),
        ("source-is-broader-than-target", "narrower"),
    ],
)
def test_t90_inter_module_directionality_agreement_on_direction_sensitive_keys(
    engine_key: str, expected_r4: str
):
    """TERMINOLOGIST Lens 9: BOTH production maps emit the spec-correct
    R4 value for the two direction-sensitive engine relationships.

    Parametrized form of Lens 1 over (engine_key, expected_r4) pairs.
    """
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE[engine_key] == expected_r4, (
        f"responses.py directionality regression on {engine_key!r}."
    )
    assert FHIR_EQUIVALENCES[engine_key] == expected_r4, (
        f"outputs/fhir.py directionality regression on {engine_key!r}."
    )
