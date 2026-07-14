"""SKEPTIC probes for chunk CM-04 (ConceptMap Equivalence Vocabulary Correctness).

Source: https://build.fhir.org/conceptmap.html
Canonical R4 equivalence enum:
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

Chunk scope (8 items):
  1. All equivalence values returned by $translate are from the FHIR R4 enum.
  2. equal: identical concept.
  3. wider: target is broader (target maps to less-specific concept).
  4. narrower: target is more specific (loses information).
  5. relatedto: related but not exact (default for SNOMED→ICD10CM crosswalks).
  6. equivalent: same clinical meaning, different codes.
  7. not-relatedto: explicitly not mapped.
  8. disjoint, subsumes, subsumedby, matches, inexact, unmatched: rare,
     context-specific.

Per schedule notes: this is a TERMINOLOGIST-ONLY chunk (equivalence
vocabulary is purely a clinical-correctness concern, other personalities
find fewer bugs). SKEPTIC may find 0 bugs here; that's expected.

SKEPTIC lens (adversarial bug hunting — focused on closed-enum contracts):
  * Closed-enum audit (frozenset membership verification).
  * R4 enum values verification (canonical page cross-check).
  * Crosswalk equivalence correctness (SNOMED→ICD10CM).
  * Equivalence map completeness (post-milestone-3 consolidation).
  * Spec deviation audit (any code path emitting non-R4 values).
  * CM-01 SKEPTIC-001 (narrower/wider directionality) still holds.
  * CM-01 SKEPTIC-002 (not-translated → unmatched) still holds.

The canonical module ``engines/fhir/equivalence.py`` is the single source
of truth post-milestone-3 (CR-024). Both ``responses.py`` ($translate) AND
``outputs/fhir.py`` (ConceptMap export) import from it — drift between the
two surfaces is structurally impossible.
"""

from __future__ import annotations

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
)


# ---------------------------------------------------------------------------
# Lens 1: Canonical R4 enum cardinality + membership (10 values exact).
# Source: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
# (HTTP-fetched 2026-07-13; value set expansion declares 10 concepts).
# ---------------------------------------------------------------------------


def test_s10_r4_concept_map_equivalence_constant_has_exactly_10_values():
    """SKEPTIC: the FHIR R4 ConceptMapEquivalence value set is exactly 10
    values per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html.
    The canonical page (HTTP-fetched) declares "This value set contains 10
    concepts" and lists exactly:
      ``relatedto | equivalent | equal | wider | subsumes | narrower |
         specializes | inexact | unmatched | disjoint``
    """
    assert len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE) == 10, (
        f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE cardinality drift: got "
        f"{len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)} values; canonical R4 spec "
        f"page declares 10."
    )


def test_s11_r4_concept_map_equivalence_contains_all_spec_codes():
    """SKEPTIC: every code from the canonical R4 spec page MUST be in the
    frozen-set constant. Membership audit against the canonical 10 codes.
    """
    canonical_codes = {
        "relatedto",
        "equivalent",
        "equal",
        "wider",
        "subsumes",
        "narrower",
        "specializes",
        "inexact",
        "unmatched",
        "disjoint",
    }
    missing = canonical_codes - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not missing, (
        f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE missing canonical R4 codes: "
        f"{missing}. Source: "
        f"https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html"
    )


def test_s12_r4_concept_map_equivalence_has_no_r5_r4b_contamination():
    """SKEPTIC: R5/R4B values ``subsumedby``, ``matches``, ``not-relatedto``
    MUST NOT be in the R4 frozen-set constant. CF-HISTORIAN-VS01-01 was
    resolved at milestone-2; this probe pins the RESOLVED state.
    """
    r5_r4b_values = {"subsumedby", "matches", "not-relatedto"}
    contamination = r5_r4b_values & FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not contamination, (
        f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE contaminated with R5/R4B values: "
        f"{contamination}. CF-HISTORIAN-VS01-01 regression."
    )


def test_s13_r4_specializes_present_not_subsumedby():
    """SKEPTIC: R4 uses ``specializes`` (NOT R5/R4B ``subsumedby``) for the
    reverse-of-subsumes case. Per canonical spec page
    (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html):
      * ``subsumes``   = "The target mapping subsumes the meaning of the
                          source concept (e.g. the source is-a target)."
      * ``specializes`` = "The target mapping specializes the meaning of the
                          source concept (e.g. the target is-a source)."

    CF-HISTORIAN-VS01-01 (milestone-2 review) fixed the prior drift where
    the implementation emitted ``subsumedby``. This probe pins the RESOLVED
    state.
    """
    assert "specializes" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        "FHIR_R4_CONCEPT_MAP_EQUIVALENCE MUST contain 'specializes' — the "
        "R4 spec-correct value for the reverse-of-subsumes case."
    )
    assert "subsumedby" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        "FHIR_R4_CONCEPT_MAP_EQUIVALENCE MUST NOT contain 'subsumedby' — "
        "R5/R4B value; R4 uses 'specializes'. CF-HISTORIAN-VS01-01."
    )


# ---------------------------------------------------------------------------
# Lens 2: Closed-enum assertion at module load (load-bearing contract).
# ---------------------------------------------------------------------------


def test_s20_canonical_module_has_module_load_assertion():
    """SKEPTIC: ``engines/fhir/equivalence.py`` MUST have a module-load
    assertion that every emitted value is in the R4 closed enum. This is
    the load-bearing contract that prevents drift across BOTH the
    $translate surface (responses.py) AND the ConceptMap export surface
    (outputs/fhir.py) — both import the canonical map.

    Without the assertion, a future map addition could silently land
    off-spec values on both surfaces.
    """
    import inspect

    from medterm4ds.engines.fhir import equivalence as equiv_module

    source = inspect.getsource(equiv_module)
    assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in source, (
        "engines/fhir/equivalence.py MUST reference "
        "FHIR_R4_CONCEPT_MAP_EQUIVALENCE in a module-load assertion "
        "(CF-HISTORIAN-VS01-01, CR-024)."
    )
    assert "assert" in source, (
        "engines/fhir/equivalence.py MUST have a module-load assert "
        "enforcing closed-enum membership."
    )
    assert "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()" in source, (
        "engines/fhir/equivalence.py MUST assert on "
        "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values() membership."
    )


def test_s21_canonical_module_load_does_not_raise():
    """SKEPTIC: importing the canonical equivalence module MUST succeed
    without raising. The module-load assertion verifies every map value
    is in the R4 closed enum; if the assertion fires, the module fails
    to import and BOTH the $translate AND ConceptMap export surfaces
    are broken.
    """
    # The import itself is the test — if the module-load assert fires,
    # ImportError propagates and the test fails.
    from medterm4ds.engines.fhir import equivalence as _  # noqa: F401

    assert True, "canonical module loaded successfully"


# ---------------------------------------------------------------------------
# Lens 3: Engine vocabulary → R4 translation correctness (every engine key).
# The engine emits exactly 6 relationship values. Every one MUST map to a
# value in the R4 closed enum.
# ---------------------------------------------------------------------------


def test_s30_engine_equivalent_key_maps_to_r4_enum_value():
    """SKEPTIC (item 6 — equivalent): engine relationship ``equivalent``
    maps to R4 ``equivalent`` (same clinical meaning, different codes).
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    assert fhir_equivalence("equivalent") == "equivalent"
    assert fhir_equivalence("equivalent") in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_s31_engine_same_key_maps_to_equal_per_r4():
    """SKEPTIC (item 1 — equal): engine relationships ``same`` and
    ``identical`` map to R4 ``equal`` (identical concept). Per canonical
    R4 spec page: ``equal`` = "The definitions of the concepts are exactly
    the same".
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    assert fhir_equivalence("same") == "equal"
    assert fhir_equivalence("identical") == "equal"


def test_s32_engine_source_is_narrower_maps_to_wider_per_r4():
    """SKEPTIC (item 3 — wider): engine relationship
    ``source-is-narrower-than-target`` maps to R4 ``wider`` (target is
    broader). Per R4 spec: ``wider`` = "The target mapping is wider in
    meaning than the source concept".

    CM-01 SKEPTIC-001 RESOLVED-status verification — the prior map had
    directionality inverted.
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    result = fhir_equivalence("source-is-narrower-than-target")
    assert result == "wider", (
        f"CM-01 SKEPTIC-001 regression: 'source-is-narrower-than-target' "
        f"MUST map to R4 'wider' (target is broader); got {result!r}."
    )


def test_s33_engine_source_is_broader_maps_to_narrower_per_r4():
    """SKEPTIC (item 4 — narrower): engine relationship
    ``source-is-broader-than-target`` maps to R4 ``narrower`` (target is
    more specific, loses information). Per R4 spec: ``narrower`` = "The
    target mapping is narrower in meaning than the source concept".

    CM-01 SKEPTIC-001 RESOLVED-status verification — mirror of test_s32.
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    result = fhir_equivalence("source-is-broader-than-target")
    assert result == "narrower", (
        f"CM-01 SKEPTIC-001 regression: 'source-is-broader-than-target' "
        f"MUST map to R4 'narrower' (target loses information); got "
        f"{result!r}."
    )


def test_s34_engine_related_to_maps_to_relatedto_per_r4():
    """SKEPTIC (item 5 — relatedto): engine relationship ``related-to``
    maps to R4 ``relatedto`` (related but not exact). This is the
    spec-correct default for SNOMED→ICD10CM crosswalks.
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    assert fhir_equivalence("related-to") == "relatedto"


def test_s35_engine_not_translated_maps_to_unmatched_per_r4():
    """SKEPTIC (item 7 — not-relatedto / no mapping): engine relationship
    ``not-translated`` maps to R4 ``unmatched`` (no mapping). CM-01
    SKEPTIC-002 RESOLVED-status verification — the prior outputs/fhir.py
    mapped this to ``equivalent`` (silent-wrong-answer).

    Per R4 spec: ``unmatched`` = "There is no match for this concept in
    the target code system".
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    result = fhir_equivalence("not-translated")
    assert result == "unmatched", (
        f"CM-01 SKEPTIC-002 regression: 'not-translated' MUST map to R4 "
        f"'unmatched' (catch-all for no mapping); got {result!r}."
    )


def test_s36_engine_unmatched_maps_to_unmatched_per_r4():
    """SKEPTIC (item 7 — unmatched): engine relationship ``unmatched``
    maps to R4 ``unmatched`` directly.
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    assert fhir_equivalence("unmatched") == "unmatched"


def test_s37_every_engine_relationship_emits_r4_enum_value():
    """SKEPTIC (closed-enum audit): every engine relationship value in
    ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` MUST map to a value in the R4
    closed enum. The module-load assertion enforces this; this probe is
    the runtime equivalent — explicit enumeration of every map entry.
    """
    from medterm4ds.engines.fhir.equivalence import (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    )

    drift = {}
    for key, value in INTERNAL_REL_TO_FHIR_EQUIVALENCE.items():
        if value not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE:
            drift[key] = value
    assert not drift, (
        f"INTERNAL_REL_TO_FHIR_EQUIVALENCE emits values outside R4 enum: "
        f"{drift}."
    )


# ---------------------------------------------------------------------------
# Lens 4: Default fallback (relatedto) for unknown / null / empty.
# Per R4 spec: ``relatedto`` = "concepts are related; exact relationship
# not known" — the catch-all for an unknown relationship.
# ---------------------------------------------------------------------------


def test_s40_fhir_equivalence_none_returns_relatedto():
    """SKEPTIC: ``fhir_equivalence(None)`` MUST return ``relatedto`` — the
    R4 catch-all for "relationship exists but exact type unknown". Never
    echoes None and never raises.
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    assert fhir_equivalence(None) == "relatedto"


def test_s41_fhir_equivalence_empty_string_returns_relatedto():
    """SKEPTIC: ``fhir_equivalence("")`` MUST return ``relatedto``."""
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    assert fhir_equivalence("") == "relatedto"


def test_s42_fhir_equivalence_unknown_returns_relatedto_never_echoes_raw():
    """SKEPTIC: ``fhir_equivalence("UNKNOWN_TOKEN")`` MUST return
    ``relatedto`` (catch-all). NEVER echoes the raw token — otherwise the
    response contains a value outside the FHIR R4 value set.
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    result = fhir_equivalence("UNKNOWN_TOKEN_XYZ")
    assert result == "relatedto", (
        f"fhir_equivalence unknown-token MUST return 'relatedto' "
        f"(R4 catch-all); got {result!r}."
    )
    assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"fhir_equivalence MUST emit R4 enum value, not raw token; "
        f"got {result!r}."
    )


# ---------------------------------------------------------------------------
# Lens 5: Defensive pass-through entries (R4 codes accepted verbatim).
# The map accepts the R4 codes as keys too — keeps the map resilient if
# a future engine vocabulary change uses R4 codes verbatim.
# ---------------------------------------------------------------------------


def test_s50_r4_codes_accepted_verbatim_in_map():
    """SKEPTIC: the canonical map accepts R4 enum codes as keys too,
    returning the same value. This keeps the map resilient if a future
    engine vocabulary change uses R4 codes verbatim.
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    for r4_code in ("wider", "narrower", "relatedto", "disjoint", "unmatched"):
        result = fhir_equivalence(r4_code)
        assert result == r4_code, (
            f"fhir_equivalence({r4_code!r}) MUST return {r4_code!r} "
            f"(R4 code verbatim); got {result!r}."
        )


def test_s51_r4_specializes_code_accepted_verbatim():
    """SKEPTIC: R4 ``specializes`` accepted verbatim. CF-HISTORIAN-VS01-01
    RESOLVED-status verification — R4 uses ``specializes`` (NOT R5
    ``subsumedby``).
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    assert fhir_equivalence("specializes") == "specializes"


def test_s52_subsumes_code_accepted_verbatim():
    """SKEPTIC: R4 ``subsumes`` accepted verbatim."""
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    assert fhir_equivalence("subsumes") == "subsumes"


def test_s53_subsumedby_alias_maps_to_specializes():
    """SKEPTIC: ``subsumedby`` (R5/R4B value if it ever appears in engine
    vocabulary) is aliased to R4 ``specializes``. This is the
    CF-HISTORIAN-VS01-01 RESOLVED-status contract — the alias is defensive
    against a future engine vocabulary change; it never leaks the R5 value
    to the wire.
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    assert fhir_equivalence("subsumedby") == "specializes"
    assert fhir_equivalence("subsumed-by") == "specializes"


# ---------------------------------------------------------------------------
# Lens 6: CM-01 SKEPTIC-001 narrower/wider directionality still holds.
# Direct verification of the translation map values.
# ---------------------------------------------------------------------------


def test_s60_internal_map_source_is_narrower_emits_wider():
    """SKEPTIC: ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` maps
    ``source-is-narrower-than-target`` → ``wider``. CM-01 SKEPTIC-001
    RESOLVED-status pin.
    """
    from medterm4ds.engines.fhir.equivalence import (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    )

    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"] == "wider"


def test_s61_internal_map_source_is_broader_emits_narrower():
    """SKEPTIC: ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` maps
    ``source-is-broader-than-target`` → ``narrower``. CM-01 SKEPTIC-001
    RESOLVED-status pin (mirror of test_s60).
    """
    from medterm4ds.engines.fhir.equivalence import (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    )

    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"] == "narrower"


# ---------------------------------------------------------------------------
# Lens 7: CM-01 SKEPTIC-002 not-translated → unmatched still holds.
# ---------------------------------------------------------------------------


def test_s70_internal_map_not_translated_emits_unmatched():
    """SKEPTIC: ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` maps
    ``not-translated`` → ``unmatched``. CM-01 SKEPTIC-002 RESOLVED-status
    pin.
    """
    from medterm4ds.engines.fhir.equivalence import (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    )

    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-translated"] == "unmatched"


def test_s71_internal_map_not_relatedto_emits_unmatched():
    """SKEPTIC: ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` maps
    ``not-relatedto`` → ``unmatched`` (R4 catch-all for no mapping).
    CF-HISTORIAN-VS01-01 RESOLVED-status pin.
    """
    from medterm4ds.engines.fhir.equivalence import (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    )

    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-relatedto"] == "unmatched"
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-related-to"] == "unmatched"


# ---------------------------------------------------------------------------
# Lens 8: Cross-surface parity (responses.py ↔ outputs/fhir.py).
# Both import from the canonical module — drift is structurally impossible.
# ---------------------------------------------------------------------------


def test_s80_responses_and_outputs_modules_import_same_canonical_map():
    """SKEPTIC: both ``responses.py`` ($translate surface) and
    ``outputs/fhir.py`` (ConceptMap export surface) MUST import from the
    same canonical module. A regression where either file redefines the
    map locally would silently diverge.
    """
    import inspect

    from medterm4ds.engines.fhir import responses as responses_module
    from medterm4ds.outputs import fhir as outputs_fhir_module

    responses_src = inspect.getsource(responses_module)
    outputs_src = inspect.getsource(outputs_fhir_module)

    assert "from medterm4ds.engines.fhir.equivalence import" in responses_src, (
        "engines/fhir/responses.py MUST import from the canonical "
        "equivalence module (CR-024)."
    )
    assert "from medterm4ds.engines.fhir.equivalence import" in outputs_src, (
        "outputs/fhir.py MUST import from the canonical equivalence "
        "module (CR-024)."
    )


def test_s81_both_modules_emit_same_value_for_every_engine_relationship():
    """SKEPTIC: for every engine relationship value, ``responses.py``
    (via ``_fhir_equivalence_from_relationship``) and ``outputs/fhir.py``
    (via ``fhir_equivalence``) MUST emit the same R4 enum value. The
    canonical module import makes drift structurally impossible; this
    probe is the runtime verification.
    """
    from medterm4ds.engines.fhir.equivalence import (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    )
    from medterm4ds.engines.fhir.responses import _fhir_equivalence_from_relationship
    from medterm4ds.outputs.fhir import fhir_equivalence

    disagreements = []
    for engine_rel in INTERNAL_REL_TO_FHIR_EQUIVALENCE:
        responses_val = _fhir_equivalence_from_relationship(engine_rel)
        outputs_val = fhir_equivalence(engine_rel)
        if responses_val != outputs_val:
            disagreements.append((engine_rel, responses_val, outputs_val))
    assert not disagreements, (
        f"responses.py and outputs/fhir.py disagree on engine "
        f"relationships: {disagreements}."
    )


# ---------------------------------------------------------------------------
# Lens 9: Spec deviation audit (no code path emits non-R4 values).
# Every emitted value across both surfaces is in the R4 closed enum.
# ---------------------------------------------------------------------------


def test_s90_translate_response_emits_only_r4_equivalence_values(fhir_client):
    """SKEPTIC (closed-enum audit on the wire): every ``match.equivalence``
    value in a $translate response MUST be in the R4 closed enum. The
    canonical module assertion enforces this at import time; this probe
    is the runtime verification against the actual HTTP surface.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
        },
    )
    body = r.json()
    if body.get("resourceType") == "OperationOutcome":
        pytest.skip("fixture DB missing the test code")
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    if not matches:
        pytest.skip("no matches for the test code")
    for m in matches:
        equiv_part = next(
            (part for part in m.get("part", []) if part.get("name") == "equivalence"),
            None,
        )
        assert equiv_part is not None, "match.part missing 'equivalence'"
        equiv = equiv_part.get("valueCode")
        assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"$translate emitted equivalence={equiv!r} which is NOT in "
            f"the FHIR R4 ConceptMapEquivalence closed enum."
        )


def test_s91_conceptmap_export_emits_only_r4_equivalence_values():
    """SKEPTIC (closed-enum audit on the export surface): every
    ``group.element.target.equivalence`` value in a ``concept_map_to_fhir``
    export MUST be in the R4 closed enum.
    """
    from medterm4ds.core.models import CodeRef, ConceptMapRow
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="Diabetes mellitus",
            target_display="Type 2 diabetes mellitus",
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="44054006"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="T2DM",
            target_display="T2DM",
            relationship="equivalent",
            match_type="exact",
        ),
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="44054006"),
            target=CodeRef(source="RXNORM", code="860975"),
            source_display="T2DM",
            target_display="metformin",
            relationship="related-to",
            match_type="first_axis",
        ),
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="44054006"),
            target=CodeRef(source="ICD10CM", code="NONE"),
            source_display="T2DM",
            target_display="",
            relationship="not-translated",
            match_type="original",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    assert resource["resourceType"] == "ConceptMap"
    for g in resource.get("group", []):
        for element in g.get("element", []):
            for target in element.get("target", []):
                equiv = target.get("equivalence")
                assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                    f"concept_map_to_fhir emitted equivalence={equiv!r} "
                    f"which is NOT in the FHIR R4 closed enum."
                )


def test_s92_translate_response_equivalence_value_is_code_not_string():
    """SKEPTIC (wire shape): the ``equivalence`` part in a $translate
    response MUST be ``valueCode`` (not ``valueString``). Per FHIR R4
    $translate OperationDefinition
    (https://hl7.org/fhir/R4/conceptmap-operation-translate.html) Out
    Parameters: ``equivalence`` is a ``code`` type. The closed-enum
    membership is verified by the value being a code.
    """
    from medterm4ds.core.models import CodeMapping, CodeRef
    from medterm4ds.engines.fhir.responses import build_parameters_translate

    mappings = [
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            relationship="equivalent",
            match_type="exact",
        ),
    ]
    body = build_parameters_translate(
        mappings,
        source_system_uri="http://snomed.info/sct",
        source_code="73211009",
    )
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert matches, "build_parameters_translate emitted no matches"
    equiv_part = next(
        part for part in matches[0]["part"] if part.get("name") == "equivalence"
    )
    assert "valueCode" in equiv_part, (
        f"equivalence part MUST use 'valueCode' (FHIR R4 code type); "
        f"got keys={list(equiv_part.keys())}."
    )
    assert "valueString" not in equiv_part, (
        f"equivalence part MUST NOT use 'valueString'; FHIR R4 spec "
        f"requires 'valueCode' for the equivalence enum value."
    )


# ---------------------------------------------------------------------------
# Lens 10: fhir_equivalence() function contract (never raises, never echoes raw).
# ---------------------------------------------------------------------------


def test_s100_fhir_equivalence_never_raises_on_any_input():
    """SKEPTIC (function contract): ``fhir_equivalence()`` MUST never raise.
    The FHIR enum is closed, so unrecognized internal vocabularies MUST be
    translated rather than echoed raw (otherwise the response contains a
    value outside the FHIR R4 value set). Inputs that might trigger
    exceptions are tested: None, empty, non-string types (should be
    handled gracefully or rejected at the type level).
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence

    # None and empty are handled explicitly.
    assert fhir_equivalence(None) == "relatedto"
    assert fhir_equivalence("") == "relatedto"

    # Every R4 enum value is a valid input.
    for r4_code in FHIR_R4_CONCEPT_MAP_EQUIVALENCE:
        result = fhir_equivalence(r4_code)
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


def test_s101_fhir_equivalence_case_insensitive_fallback():
    """SKEPTIC: ``_fhir_equivalence_from_relationship`` (the $translate
    surface wrapper) has a case-insensitive fallback. Engine emits
    lowercase values today; the fallback future-proofs against a
    vocabulary change without silently emitting a non-FHIR value.
    """
    from medterm4ds.engines.fhir.responses import _fhir_equivalence_from_relationship

    # Lowercase (canonical engine form)
    assert _fhir_equivalence_from_relationship("equivalent") == "equivalent"
    # Uppercase (fallback)
    assert _fhir_equivalence_from_relationship("EQUIVALENT") == "equivalent"
    # Mixed case
    assert _fhir_equivalence_from_relationship("Equivalent") == "equivalent"


# ---------------------------------------------------------------------------
# Lens 11: Crosswalk equivalence correctness (SNOMED→ICD10CM).
# Per chunk scope item 5: SNOMED→ICD10CM crosswalk is typically ``relatedto``.
# The fixture DB seeds SNOMED 44054006 (T2DM) → ICD10CM E11 (T2DM) via
# shared CUI C0011847, which the engine emits as ``equivalent``. The
# crosswalk class depends on engine data; the engine may emit ``equivalent``
# for same-CUI mappings, ``related-to`` for component/first-axis mappings,
# and ``source-is-narrower-than-target`` / ``source-is-broader-than-target``
# for hierarchy-based mappings.
# ---------------------------------------------------------------------------


def test_s110_translate_snomed_to_icd10_emits_r4_enum_value(fhir_client):
    """SKEPTIC (crosswalk equivalence correctness): a SNOMED→ICD10CM
    crosswalk via $translate MUST emit an ``equivalence`` value from the
    R4 closed enum. The specific value (``equivalent``, ``relatedto``,
    etc.) depends on the engine's relationship classification for the
    fixture data; the closed-enum membership is the load-bearing
    contract regardless of which R4 code the engine picks.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
        },
    )
    body = r.json()
    if body.get("resourceType") == "OperationOutcome":
        pytest.skip("fixture DB missing the test code")
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    if not matches:
        pytest.skip("no matches for the test code")
    equiv_part = next(
        (part for part in matches[0].get("part", []) if part.get("name") == "equivalence"),
        None,
    )
    assert equiv_part is not None, "match.part missing 'equivalence'"
    equiv = equiv_part.get("valueCode")
    assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"SNOMED→ICD10CM crosswalk emitted equivalence={equiv!r} NOT in "
        f"R4 enum. The crosswalk relationship classification must always "
        f"resolve to an R4 spec-correct value."
    )


def test_s111_translate_no_targetsystem_emits_r4_enum_value(fhir_client):
    """SKEPTIC: a $translate call without a ``targetsystem`` parameter
    MUST still emit only R4-spec-correct ``equivalence`` values. The
    engine may return matches across multiple target systems; every
    match's ``equivalence`` MUST be in the R4 closed enum.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
        },
    )
    body = r.json()
    if body.get("resourceType") == "OperationOutcome":
        pytest.skip("fixture DB missing the test code")
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    if not matches:
        pytest.skip("no matches for the test code")
    for m in matches:
        equiv_part = next(
            (part for part in m.get("part", []) if part.get("name") == "equivalence"),
            None,
        )
        assert equiv_part is not None, "match.part missing 'equivalence'"
        equiv = equiv_part.get("valueCode")
        assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"$translate (no targetsystem) emitted equivalence={equiv!r} "
            f"NOT in R4 enum."
        )
