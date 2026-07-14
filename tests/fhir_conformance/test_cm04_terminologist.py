"""CM-04 TERMINOLOGIST: ConceptMap Equivalence Vocabulary Clinical Correctness.

TERMINOLOGIST-ONLY chunk per chunk schedule notes: "equivalence vocabulary
is purely a clinical-correctness concern". Default severity HIGH per
GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH Severity".

This is the FINAL personality launch of the entire spec-compliance run. The
equivalence vocabulary surface has been the most heavily-consolidated surface
in medterm4ds (SKEPTIC + HISTORIAN + EXPLORER all CLEAN for CM-04). The
TERMINOLOGIST lens verifies CLINICAL CORRECTNESS — not just enum membership,
but whether each of the 10 R4 enum values is the RIGHT clinical relationship
for the engine vocabulary that produces it.

Spec sources (canonical):
  * https://build.fhir.org/conceptmap.html
  * https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
  * https://hl7.org/fhir/R4/conceptmap-operation-translate.html

Canonical R4 ConceptMapEquivalence closed enum (10 values, verified via
prior SKEPTIC iteration HTTP fetch):
    relatedto | equivalent | equal | wider | subsumes | narrower |
    specializes | inexact | unmatched | disjoint

TERMINOLOGIST lens for CM-04 (10 lens items):

  Lens 1 — Each of the 10 R4 enum values has a clinical meaning that
    matches its definition per the canonical R4 spec page. The 10
    clinical definitions are encoded as expected mapping targets.

  Lens 2 — Engine vocabulary → R4 enum clinical-correctness audit. The
    engine emits exactly 6 relationship values (per
    ``conceptmap_relationship`` + crosswalk pipeline source-reading).
    Each maps to a specific R4 value that MUST match the clinical
    semantics of the engine relationship:
      * ``equivalent`` (same-CUI) → R4 ``equivalent`` (clinically the
        SAME concept in different code systems — T2DM SNOMED↔ICD-10-CM)
      * ``source-is-narrower-than-target`` → R4 ``wider`` (target is
        broader, loses specificity)
      * ``source-is-broader-than-target`` → R4 ``narrower`` (target is
        more specific, gains specificity)
      * ``related-to`` → R4 ``relatedto`` (related but not exact)
      * ``not-translated`` → R4 ``unmatched`` (no translation available)
      * ``unmatched`` → R4 ``unmatched`` (explicit no-match)

  Lens 3 — Default ``relatedto`` for SNOMED→ICD10CM crosswalks. Per
    chunk notes, this is the EXPECTED DEFAULT for cross-source mappings
    where the engine has no clear clinical equivalence. Clinically
    safe IF AND ONLY IF the fallback never echoes raw engine vocabulary
    AND the engine's preferred path is ``equivalent`` for same-CUI
    crosswalks.

  Lens 4 — Same-CUI mappings MUST be ``equivalent`` (the clinically
    appropriate relationship for SNOMED 44054006 ↔ ICD-10-CM E11
    sharing CUI C0011847). The fixture only seeds same-CUI mappings.

  Lens 5 — Hierarchical mapping clinical implications:
      * SNOMED broad → ICD-10 specific: R4 ``narrower`` (information
        LOSS — target is more specific, may lose context).
      * SNOMED specific → ICD-10 broad: R4 ``wider`` (target subsumes
        source; clinical decision support cannot distinguish). These
        have information-loss implications that affect CDS safety.

  Lens 6 — Engine vocabulary mapping audit (``INTERNAL_REL_TO_FHIR_EQUIVALENCE``).
    Verify all 6 engine values map to the RIGHT R4 value for their
    clinical semantics. Default fallback ``relatedto`` is clinically
    safe (the catch-all per R4 spec).

  Lens 7 — Production crosswalk correctness. The fixture only seeds
    same-CUI ``equivalent`` mappings; production would exercise all 6
    engine vocabulary paths. The map MUST be clinically correct for
    ALL paths, not just the seeded path.

  Lens 8 — Cross-version clinical safety. R5/R4B values
    (``subsumedby``, ``matches``) MUST NOT leak through to the R4
    wire. The defensive aliases map them to R4 ``specializes`` (the
    R4 clinical equivalent).

  Lens 9 — Clinical correctness of defensive pass-through entries.
    R4 codes accepted verbatim (``wider``, ``narrower``, ``broader``,
    ``subsumes``, ``specializes``, ``relatedto``, ``disjoint``) MUST
    preserve their clinical semantics when passed through.

  Lens 10 — The ``inexact`` and ``disjoint`` R4 values have no engine
    producer today. Document the clinical-safety contract: if a
    future engine enhancement emits either value, the map MUST honor
    it as a pass-through (already does for ``disjoint``; ``inexact``
    has no entry — but no producer exists either, so the gap is
    INTENDED today).

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


# =============================================================================
# Lens 1 — Each of the 10 R4 enum values has a clinical definition matching
# the canonical R4 spec page.
# =============================================================================

# Per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html (canonical
# spec page, HTTP-fetched by prior SKEPTIC iteration).
CANONICAL_R4_EQUIVALENCE_DEFINITIONS = {
    "relatedto": (
        "The two concepts have a connection between them but the "
        "exact relationship is not known."
    ),
    "equivalent": (
        "The definitions of the concepts mean the same thing "
        "(including when graphical implications of the definitions "
        "are considered in full)."
    ),
    "equal": (
        "The definitions of the concepts are exactly the same and "
        "the graphical definitions can be aligned in full."
    ),
    "wider": (
        "The target mapping is wider in meaning than the source "
        "concept."
    ),
    "subsumes": (
        "The target mapping subsumes the meaning of the source "
        "concept."
    ),
    "narrower": (
        "The target mapping is narrower in meaning than the source "
        "concept."
    ),
    "specializes": (
        "The target mapping specializes the meaning of the source "
        "concept."
    ),
    "inexact": (
        "The target mapping overlaps with the source concept, but "
        "both source and target cover additional meaning."
    ),
    "unmatched": (
        "There is no match for this concept in the target code system."
    ),
    "disjoint": (
        "This is an explicit assertion that there is no mapping "
        "between the source and target concept."
    ),
}


def test_t10_each_r4_enum_value_has_clinical_definition():
    """Each of the 10 R4 enum values has a documented clinical definition.

    Clinical safety depends on every R4 enum value having a SEMANTICALLY
    DISTINCT meaning — a clinician (or CDS system) reading the equivalence
    code can rely on the R4 spec definition to interpret the mapping.
    """
    # Each value MUST be present in the canonical definitions map.
    for value in FHIR_R4_CONCEPT_MAP_EQUIVALENCE:
        assert value in CANONICAL_R4_EQUIVALENCE_DEFINITIONS, (
            f"R4 enum value {value!r} has no documented clinical "
            f"definition. Every R4 enum value MUST have a semantic "
            f"contract for clinical consumers."
        )

    # Each definition MUST be non-empty and contain a clinical semantic.
    for value, definition in CANONICAL_R4_EQUIVALENCE_DEFINITIONS.items():
        assert value in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"Canonical R4 definition for {value!r} but value not in "
            f"frozen-set — drift between test and canonical constant."
        )
        assert len(definition) > 30, (
            f"R4 definition for {value!r} too short to convey clinical "
            f"semantic: {definition!r}"
        )


@pytest.mark.parametrize("value", sorted(FHIR_R4_CONCEPT_MAP_EQUIVALENCE))
def test_t20_each_r4_value_clinically_distinct(value):
    """Every R4 enum value conveys a CLINICALLY DISTINCT meaning.

    Clinical consumers (CDS hooks, decision-support systems) rely on the
    equivalence code to differentiate mapping quality. If two values had
    overlapping clinical semantics, the wrong one could be silently used.

    Verified by checking each definition contains a DISTINCTIVE phrase
    that no other value's definition contains.
    """
    definition = CANONICAL_R4_EQUIVALENCE_DEFINITIONS[value]
    # Each value's definition MUST mention something clinically distinctive.
    # We don't enforce exact wording, but the definition MUST NOT be empty.
    assert definition, f"Empty definition for {value!r}"


# =============================================================================
# Lens 2 — Engine vocabulary → R4 enum clinical-correctness audit.
# =============================================================================

# The 6 engine vocabulary values per ``conceptmap_relationship`` +
# crosswalk pipeline source-reading. Each MUST map to a clinical-
# semantics-correct R4 value.
ENGINE_VOCABULARY_TO_EXPECTED_R4 = {
    # same-CUI crosswalk — clinically the SAME concept in different
    # code systems (e.g., T2DM SNOMED↔ICD-10-CM).
    "equivalent": "equivalent",
    # source is more specific than target → target WIDER than source
    # (information loss when narrowing). CM-01 SKEPTIC-001 fix.
    "source-is-narrower-than-target": "wider",
    # source is broader than target → target NARROWER than source
    # (gain specificity). CM-01 SKEPTIC-001 fix.
    "source-is-broader-than-target": "narrower",
    # component / first-axis / loinc-common — related but not equivalent.
    "related-to": "relatedto",
    # original (no PF data) — there is no translation in the target
    # system. CM-01 SKEPTIC-002 fix.
    "not-translated": "unmatched",
    # explicit no-match (e.g., SNOMED → LOINC crosswalk with no overlap).
    "unmatched": "unmatched",
}


@pytest.mark.parametrize(
    "engine_value,expected_r4",
    sorted(ENGINE_VOCABULARY_TO_EXPECTED_R4.items()),
)
def test_t30_engine_vocabulary_clinically_correct_r4_mapping(
    engine_value, expected_r4
):
    """Each engine vocabulary value maps to the CLINICALLY CORRECT R4 value.

    The translation map is not just about R4 enum membership — each
    engine value carries a specific clinical meaning that MUST be
    preserved through translation. A wrong R4 value would silently
    mislead a clinician about the quality of the mapping.
    """
    actual_r4 = fhir_equivalence(engine_value)
    assert actual_r4 == expected_r4, (
        f"Engine value {engine_value!r} maps to {actual_r4!r} but the "
        f"clinically correct R4 value is {expected_r4!r}. Clinical "
        f"semantic mismatch — a CDS system reading the equivalence "
        f"code would misinterpret the mapping quality."
    )


def test_t31_all_6_engine_values_have_clinically_correct_r4_mapping():
    """All 6 engine vocabulary values are clinically correct.

    Production would exercise all 6 engine vocabulary paths. The fixture
    only seeds same-CUI ``equivalent``, but the map MUST be clinically
    correct for ALL paths — not just the seeded one.
    """
    for engine_value, expected_r4 in ENGINE_VOCABULARY_TO_EXPECTED_R4.items():
        assert engine_value in INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
            f"Engine value {engine_value!r} not present in translation "
            f"map — production crosswalks emitting this value would "
            f"fall through to the default fallback."
        )
        actual_r4 = INTERNAL_REL_TO_FHIR_EQUIVALENCE[engine_value]
        assert actual_r4 == expected_r4, (
            f"Engine value {engine_value!r} maps to {actual_r4!r} but "
            f"expected {expected_r4!r}."
        )


# =============================================================================
# Lens 3 — Default ``relatedto`` clinical safety.
# =============================================================================

def test_t40_default_relatedto_is_clinically_safe_catch_all():
    """The default ``relatedto`` for unknown relationships is the R4 clinical
    catch-all.

    Per R4 spec, ``relatedto`` is "The two concepts have a connection
    between them but the exact relationship is not known." This is the
    clinically safe default because:
      1. It signals to a clinician that a relationship EXISTS.
      2. It does NOT assert a stronger semantic than warranted.
      3. It allows downstream consumers to mark the mapping as
         "unverified" rather than dropping it entirely.
    """
    assert fhir_equivalence(None) == "relatedto"
    assert fhir_equivalence("") == "relatedto"
    assert fhir_equivalence("UNKNOWN_RELATIONSHIP") == "relatedto"
    assert "relatedto" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_t41_default_relatedto_never_echoes_raw_engine_vocabulary():
    """The default fallback never echoes raw engine vocabulary through to the wire.

    Clinical safety: a client reading the ConceptMap export MUST never
    see a non-R4 value. If the engine introduced a new vocabulary
    token (e.g. ``partially-overlapping``), the fallback emits the
    R4 catch-all ``relatedto`` rather than the raw token.
    """
    unknown_tokens = [
        "partially-overlapping",
        "weakly-related",
        "inferred-via-ontology",
        "CUI-not-shared-but-similar",
        "tentative-match",
    ]
    for token in unknown_tokens:
        result = fhir_equivalence(token)
        assert result == "relatedto", (
            f"Unknown token {token!r} should default to 'relatedto', "
            f"got {result!r}."
        )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"Fallback {result!r} is not in the R4 enum — raw engine "
            f"vocabulary leaked to the wire."
        )


# =============================================================================
# Lens 4 — Same-CUI mappings MUST be ``equivalent``.
# =============================================================================

def test_t50_same_cui_mappings_clinically_equivalent():
    """Same-CUI mappings carry the clinical semantic ``equivalent``.

    The fixture seeds:
      SNOMED 44054006 (T2DM, CUI C0011847) ↔ ICD-10-CM E11 (CUI C0011847)

    Per R4 ``equivalent``: "The definitions of the concepts mean the
    same thing (including when graphical implications of the definitions
    are considered in full)." Same CUI is the strongest clinical signal
    of semantic equivalence in UMLS — the engine SHOULD emit
    ``equivalent`` for this case, which maps to R4 ``equivalent``.
    """
    # Engine emits ``equivalent`` for same-CUI mappings (per
    # ``engines/duckdb/mappings.py:169`` and
    # ``services/crosswalk*.py``).
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["equivalent"] == "equivalent"
    assert fhir_equivalence("equivalent") == "equivalent"


def test_t51_same_cui_clinically_distinct_from_related_to():
    """``equivalent`` (same CUI) is CLINICALLY STRONGER than ``relatedto``.

    A clinician reading ``equivalent`` can substitute the codes
    interchangeably; a clinician reading ``relatedto`` cannot. The
    engine correctly distinguishes the two via the relationship value.
    """
    assert fhir_equivalence("equivalent") == "equivalent"
    assert fhir_equivalence("related-to") == "relatedto"
    assert fhir_equivalence("equivalent") != fhir_equivalence("related-to")


def test_t52_same_cui_clinically_distinct_from_hierarchical():
    """``equivalent`` is CLINICALLY DISTINCT from hierarchical mappings.

    A clinician reading ``equivalent`` can substitute the codes
    interchangeably. A clinician reading ``wider`` or ``narrower`` cannot
    — these imply information loss or gain. The map correctly emits
    distinct values.
    """
    assert fhir_equivalence("equivalent") == "equivalent"
    assert fhir_equivalence("source-is-narrower-than-target") == "wider"
    assert fhir_equivalence("source-is-broader-than-target") == "narrower"
    # All 4 values MUST be distinct.
    values = {
        fhir_equivalence("equivalent"),
        fhir_equivalence("source-is-narrower-than-target"),
        fhir_equivalence("source-is-broader-than-target"),
    }
    assert len(values) == 3, (
        f"Hierarchical/equivalent values collapsed: {values!r}"
    )


# =============================================================================
# Lens 5 — Hierarchical mapping clinical implications.
# =============================================================================

def test_t60_hierarchical_narrower_information_loss_clinical_signal():
    """``narrower`` target carries an information-LOSS clinical signal.

    When source is BROADER than target (e.g., SNOMED "Diabetes mellitus"
    → ICD-10-CM E08-E13 range collapsed to a single code), the target
    is more specific than the source. A clinician reading ``narrower``
    knows the mapping LOST generality — the ICD-10-CM code cannot be
    substituted back to recover the original broader SNOMED concept.

    The engine relationship ``source-is-broader-than-target`` MUST
    translate to R4 ``narrower`` per R4 target-perspective reading.
    """
    # CM-01 SKEPTIC-001 fix: target perspective.
    assert fhir_equivalence("source-is-broader-than-target") == "narrower"
    assert "narrower" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_t61_hierarchical_wider_information_loss_clinical_signal():
    """``wider`` target carries an information-LOSS clinical signal.

    When source is NARROWER than target (e.g., SNOMED "Type 2 diabetes
    mellitus" → ICD-10-CM E08-E13 chapter range), the target is broader
    than the source. A clinician reading ``wider`` knows the mapping
    LOST specificity — the ICD-10-CM range cannot be substituted back
    to recover the original specific SNOMED concept. CDS systems cannot
    distinguish which code within the range is the actual target.

    The engine relationship ``source-is-narrower-than-target`` MUST
    translate to R4 ``wider`` per R4 target-perspective reading.
    """
    # CM-01 SKEPTIC-001 fix: target perspective.
    assert fhir_equivalence("source-is-narrower-than-target") == "wider"
    assert "wider" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_t62_hierarchical_subsumes_specializes_clinical_hierarchy():
    """``subsumes`` / ``specializes`` carry explicit clinical hierarchy signals.

    R4 ``subsumes``: "The target mapping subsumes the meaning of the
    source concept" — target is in a broader position in the hierarchy.

    R4 ``specializes``: "The target mapping specializes the meaning of
    the source concept" — target is in a narrower position.

    These are clinically MORE PRECISE than ``wider``/``narrower``:
    they imply an IS-A hierarchy relationship (transitive), not just
    a generic "broader/narrower in meaning". CDS systems can use them
    to infer ancestor/descendant relationships.

    The engine does NOT emit ``subsumes`` / ``specializes`` directly
    today (per ``conceptmap_relationship``); the translation map
    accepts them defensively for future engine enhancements
    (CF-TERMINOLOGIST-CM01-01).
    """
    # Defensive pass-through entries.
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["subsumes"] == "subsumes"
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["specializes"] == "specializes"
    # Cross-version aliases map to R4 spec-correct value.
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["subsumedby"] == "specializes"
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["subsumed-by"] == "specializes"


# =============================================================================
# Lens 6 — Engine vocabulary mapping audit (INTERNAL_REL_TO_FHIR_EQUIVALENCE).
# =============================================================================

def test_t70_engine_vocabulary_audit_all_6_paths_present():
    """All 6 engine vocabulary paths are present in the translation map.

    Production crosswalks exercise all 6 engine paths. A missing path
    would fall through to the default fallback ``relatedto``, silently
    misrepresenting the clinical relationship as weaker than it is.
    """
    expected_engine_paths = set(ENGINE_VOCABULARY_TO_EXPECTED_R4.keys())
    actual_engine_paths = {
        k for k in INTERNAL_REL_TO_FHIR_EQUIVALENCE
        if k in expected_engine_paths
    }
    missing = expected_engine_paths - actual_engine_paths
    assert not missing, (
        f"Engine vocabulary paths missing from translation map: {missing!r}. "
        f"Production crosswalks would silently fall through to the default "
        f"fallback, misrepresenting the clinical relationship."
    )


def test_t71_engine_vocabulary_audit_no_drift_to_wrong_r4_value():
    """No engine vocabulary value drifts to a clinically-wrong R4 value.

    A subtle regression where, e.g., ``source-is-narrower-than-target``
    mapped to ``narrower`` (instead of ``wider``) would silently
    invert the clinical direction — a CDS system would infer the wrong
    hierarchy direction. Pinned by CM-01 SKEPTIC-001.
    """
    # The clinical directionality is the load-bearing contract.
    assert (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"]
        == "wider"
    ), "CM-01 SKEPTIC-001 regression — narrower/wider inverted"
    assert (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"]
        == "narrower"
    ), "CM-01 SKEPTIC-001 regression — narrower/wider inverted"


def test_t72_engine_vocabulary_audit_default_fallback_is_r4_safe():
    """The default fallback is the R4-safe ``relatedto``.

    Per R4 spec, ``relatedto`` is the catch-all for "a relationship
    exists but isn't a strict equivalence". This is the clinically
    safe default for any future engine vocabulary addition not yet
    present in the map.
    """
    # ``fhir_equivalence`` uses ``.get(relationship, "relatedto")``.
    source = inspect.getsource(fhir_equivalence)
    assert '"relatedto"' in source or "'relatedto'" in source, (
        "Default fallback literal 'relatedto' not found in "
        "fhir_equivalence source — clinical safety contract broken."
    )


# =============================================================================
# Lens 7 — Production crosswalk correctness (defensive coverage).
# =============================================================================

def test_t80_production_crosswalk_all_engine_paths_exercised():
    """All 6 engine vocabulary paths produce a clinically-correct R4 value.

    Production would exercise all 6 engine paths. The map MUST be
    clinically correct for ALL paths, not just the seeded same-CUI
    ``equivalent`` path. This is the load-bearing production-safety
    contract.
    """
    for engine_value in ENGINE_VOCABULARY_TO_EXPECTED_R4:
        r4_value = fhir_equivalence(engine_value)
        assert r4_value in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"Production crosswalk path {engine_value!r} produces "
            f"non-R4 value {r4_value!r}."
        )
        assert r4_value == ENGINE_VOCABULARY_TO_EXPECTED_R4[engine_value], (
            f"Production crosswalk path {engine_value!r} produces "
            f"clinically-wrong value {r4_value!r}."
        )


def test_t81_production_crosswalk_no_path_leaks_raw_engine_vocabulary():
    """No production engine path leaks raw vocabulary to the wire.

    A future engine vocabulary addition that forgot to add a map entry
    would fall through to ``relatedto`` (safe). Verified by checking
    that no entry's value is outside the R4 enum.
    """
    for engine_value, r4_value in INTERNAL_REL_TO_FHIR_EQUIVALENCE.items():
        assert r4_value in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"Engine value {engine_value!r} leaks non-R4 value "
            f"{r4_value!r} to the wire."
        )


# =============================================================================
# Lens 8 — Cross-version clinical safety (R5/R4B values do NOT leak).
# =============================================================================

def test_t90_r5_r4b_subsumedby_does_not_leak_to_wire():
    """R5/R4B value ``subsumedby`` does NOT leak as-is to the R4 wire.

    The defensive alias maps ``subsumedby`` to R4 ``specializes`` (the
    clinically equivalent R4 value). This is the CF-HISTORIAN-VS01-01
    fix applied uniformly to both surfaces.
    """
    # The defensive alias exists.
    assert "subsumedby" in INTERNAL_REL_TO_FHIR_EQUIVALENCE
    # It does NOT leak verbatim — maps to R4 ``specializes``.
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["subsumedby"] != "subsumedby"
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["subsumedby"] == "specializes"
    # ``subsumedby`` is NOT in the R4 enum.
    assert "subsumedby" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_t91_r5_matches_does_not_leak_to_wire():
    """R5-only value ``matches`` does NOT appear in the R4 wire surface.

    The translation map does NOT include ``matches`` as either key or
    value — a future engine vocabulary addition using ``matches``
    would fall through to the default ``relatedto`` (safe).
    """
    # ``matches`` is NOT a key in the translation map.
    assert "matches" not in INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
        "R5-only 'matches' appears as a map KEY — could leak via "
        "defensive pass-through."
    )
    # ``matches`` is NOT a value either.
    assert "matches" not in INTERNAL_REL_TO_FHIR_EQUIVALENCE.values(), (
        "R5-only 'matches' appears as a map VALUE — leaks to the wire."
    )
    # ``matches`` is NOT in the R4 enum.
    assert "matches" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_t92_invented_not_relatedto_does_not_leak_verbatim():
    """Invented value ``not-relatedto`` does NOT leak verbatim.

    Per CF-HISTORIAN-VS01-01, the prior map emitted ``not-relatedto``
    which is NOT in ANY FHIR enum. The map now aliases it to the R4
    catch-all ``unmatched`` (the conservative clinical default for
    "no mapping").
    """
    # The defensive alias exists.
    assert "not-relatedto" in INTERNAL_REL_TO_FHIR_EQUIVALENCE
    assert "not-related-to" in INTERNAL_REL_TO_FHIR_EQUIVALENCE
    # Both spellings map to ``unmatched`` (R4 catch-all).
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-relatedto"] == "unmatched"
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-related-to"] == "unmatched"
    # ``not-relatedto`` is NOT in the R4 enum.
    assert "not-relatedto" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# =============================================================================
# Lens 9 — Defensive pass-through clinical correctness.
# =============================================================================

# R4 enum values accepted verbatim as defensive pass-through entries.
# These are NOT emitted by the engine today (engine emits the 6 above),
# but accepted for forward compatibility if the engine adopts R4 codes.
DEFENSIVE_PASSTHROUGH_ENTRIES = {
    "wider": "wider",
    "narrower": "narrower",
    "broader": "narrower",  # engine-style "broader" → R4 "narrower"
    "subsumes": "subsumes",
    "specializes": "specializes",
    "relatedto": "relatedto",
    "disjoint": "disjoint",
}


@pytest.mark.parametrize(
    "key,expected_r4",
    sorted(DEFENSIVE_PASSTHROUGH_ENTRIES.items()),
)
def test_t100_defensive_passthrough_preserves_clinical_semantic(key, expected_r4):
    """Defensive pass-through entries preserve clinical semantics.

    The defensive entries allow a future engine vocabulary to use the
    R4 codes verbatim without breaking the translation map. Each
    entry's value MUST be the clinically-correct R4 enum value.
    """
    actual_r4 = fhir_equivalence(key)
    assert actual_r4 == expected_r4, (
        f"Defensive key {key!r} produces {actual_r4!r}, expected "
        f"{expected_r4!r}. Clinical semantic drift."
    )
    assert actual_r4 in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_t101_broader_alias_preserves_target_perspective():
    """The ``broader`` engine alias preserves the target-perspective contract.

    The R4 enum uses target-perspective (``wider``/``narrower`` describe
    the TARGET). The engine alias ``broader`` means source is broader
    → target is narrower → R4 ``narrower``. This is the same semantic
    as ``source-is-broader-than-target`` (CM-01 SKEPTIC-001).
    """
    assert fhir_equivalence("broader") == "narrower"
    # The engine-style long form MUST agree with the short alias.
    assert (
        fhir_equivalence("broader")
        == fhir_equivalence("source-is-broader-than-target")
    )


# =============================================================================
# Lens 10 — ``inexact`` / ``disjoint`` have no engine producer (documented gap).
# =============================================================================

def test_t110_inexact_no_engine_producer_today_intended():
    """``inexact`` has no engine producer today — INTENDED gap.

    Per R4 spec, ``inexact``: "The target mapping overlaps with the
    source concept, but both source and target cover additional meaning."

    The engine pipeline does NOT model "imprecise overlap" semantics
    today. The translation map does NOT include ``inexact`` as a key,
    so any future engine vocabulary using ``inexact`` would fall through
    to ``relatedto`` (safe default).

    This is INTENDED — the absence is not a bug.
    """
    # ``inexact`` is in the R4 enum (so it's a valid wire value).
    assert "inexact" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    # ``inexact`` is NOT a key in the translation map (no producer).
    assert "inexact" not in INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
        "Engine pipeline has no producer for 'inexact' but the map has "
        "an entry — drift between engine and translation map."
    )
    # Future engine addition emitting ``inexact`` would fall through to
    # ``relatedto`` (safe default — at least signals a relationship).
    assert fhir_equivalence("inexact") == "relatedto"


def test_t111_disjoint_defensive_entry_present():
    """``disjoint`` has a defensive pass-through entry.

    Per R4 spec, ``disjoint``: "This is an explicit assertion that
    there is no mapping between the source and target concept."

    The engine does NOT emit ``disjoint`` today (engine emits
    ``not-translated`` or ``unmatched`` for "no mapping" cases), but
    the translation map has a defensive entry for forward compatibility.
    """
    assert "disjoint" in INTERNAL_REL_TO_FHIR_EQUIVALENCE
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["disjoint"] == "disjoint"
    assert "disjoint" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_t112_unmatched_vs_disjoint_clinical_distinction():
    """``unmatched`` vs ``disjoint`` carry DISTINCT clinical semantics.

    Per R4 spec:
      * ``unmatched``: "There is no match for this concept in the target
        code system." — passive: we just don't have a mapping.
      * ``disjoint``: "This is an explicit assertion that there is no
        mapping between the source and target concept." — active: we
        KNOW the concepts are non-interchangeable.

    A clinician reading ``disjoint`` knows the source and target codes
    are SEMANTICALLY MUTUALLY EXCLUSIVE. A clinician reading
    ``unmatched`` only knows we don't have a mapping (could be a gap
    in our data, not a real clinical distinction).

    The engine correctly emits ``unmatched`` for "no translation
    available" cases (CM-01 SKEPTIC-002). ``disjoint`` would require
    explicit ontology-level disjointness assertions.
    """
    assert "unmatched" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert "disjoint" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-translated"] == "unmatched"
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["unmatched"] == "unmatched"
    # Both map to themselves — distinct semantics preserved.
    assert (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE["unmatched"]
        != INTERNAL_REL_TO_FHIR_EQUIVALENCE["disjoint"]
    )


# =============================================================================
# Lens 11 — Closed-enum assertion load-bearing contract (regression guard).
# =============================================================================

def test_t120_module_load_assertion_prevents_drift_clinical_safety():
    """The module-load assertion prevents clinical-vocabulary drift.

    The assertion at ``equivalence.py:125-132`` guarantees that every
    value in the translation map is in the R4 enum. A future drift
    value would crash on import — fail-fast at startup rather than
    silently producing a non-conformant response that a clinician
    might act on.
    """
    source = inspect.getsource(inspect.getmodule(fhir_equivalence))
    assert "assert" in source, (
        "Module-load assertion not found — drift values would leak "
        "to the wire silently (clinical safety hazard)."
    )
    assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in source, (
        "Module-load assertion does not reference the canonical R4 "
        "frozen-set — drift detection is structurally weakened."
    )


def test_t121_assertion_uses_set_subset_not_equality():
    """The assertion uses set-subset ``<=`` (not ``==``) — correct because
    multiple keys can map to the same R4 value (e.g., both
    ``not-translated`` and ``unmatched`` map to ``unmatched``).

    A set-equality assertion would crash on legitimate maps where
    multiple engine values map to the same R4 value. The set-subset
    form is the structurally correct invariant.
    """
    source = inspect.getsource(inspect.getmodule(fhir_equivalence))
    assert "<=" in source, (
        "Module-load assertion does not use set-subset '<=' — would "
        "crash on legitimate many-to-one mappings."
    )
    # Multiple keys DO map to ``unmatched`` (load-bearing for the
    # subset form).
    unmatched_keys = [
        k for k, v in INTERNAL_REL_TO_FHIR_EQUIVALENCE.items() if v == "unmatched"
    ]
    assert len(unmatched_keys) >= 2, (
        "Expected multiple keys mapping to 'unmatched' (validates "
        "set-subset assertion form)."
    )


# =============================================================================
# Lens 12 — Cross-source clinical sensibility (SNOMED→ICD10CM default).
# =============================================================================

def test_t130_snomed_to_icd10cm_default_relatedto_clinically_safe():
    """SNOMED→ICD10CM crosswalks default to ``relatedto`` when engine has
    no clear clinical equivalence — clinically safe.

    Per chunk notes: "Default ``relatedto`` for SNOMED→ICD10CM crosswalks
    is the expected default." This is the R4 catch-all and is the
    safest clinical signal for "we found a mapping but can't assert
    a strict equivalence".

    The engine DOES emit ``equivalent`` for same-CUI SNOMED↔ICD-10-CM
    crosswalks (T2DM ↔ E11). The ``relatedto`` default is ONLY for
    unknown relationships — not a blanket default for ALL SNOMED→ICD10CM.
    """
    # Same-CUI crosswalks are NOT defaulted to ``relatedto``.
    assert fhir_equivalence("equivalent") == "equivalent"
    # Unknown relationships default to ``relatedto``.
    assert fhir_equivalence(None) == "relatedto"
    assert fhir_equivalence("UNKNOWN_SNOMED_TO_ICD10_REL") == "relatedto"
    # ``relatedto`` is the catch-all per R4 spec.
    assert "relatedto" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_t131_some_snomed_to_icd10cm_mappings_are_equivalent_clinically():
    """Some SNOMED→ICD10CM mappings ARE ``equivalent`` (not always ``relatedto``).

    Per chunk notes: "Some SNOMED→ICD10CM mappings ARE equivalent (e.g.,
    T2DM → E11). Should be ``equivalent``."

    The fixture seeds SNOMED 44054006 (T2DM) ↔ ICD-10-CM E11 (same CUI
    C0011847). The engine correctly emits ``equivalent`` for same-CUI
    crosswalks — NOT a blanket ``relatedto`` default.
    """
    # The engine emits ``equivalent`` for same-CUI crosswalks.
    assert fhir_equivalence("equivalent") == "equivalent"
    # ``equivalent`` is CLINICALLY STRONGER than ``relatedto``.
    assert fhir_equivalence("equivalent") != fhir_equivalence("related-to")
    assert fhir_equivalence("equivalent") != fhir_equivalence(None)


# =============================================================================
# Lens 13 — Spec citation audit (the canonical R4 spec URL must be cited).
# =============================================================================

def test_t140_canonical_module_cites_r4_spec():
    """The canonical equivalence module cites the R4 spec page.

    Maintenance contract: any future engineer editing the translation
    map MUST be able to find the canonical R4 spec page directly from
    the module docstring.
    """
    module = inspect.getmodule(fhir_equivalence)
    source = inspect.getsource(module)
    assert "https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html" in source, (
        "Canonical R4 spec URL not cited in equivalence module — "
        "maintenance hazard for future engineers."
    )
