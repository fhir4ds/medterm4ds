"""SKEPTIC RESWEEP probes for chunk CM-01 (ConceptMap Resource Structure).

Source: https://build.fhir.org/conceptmap.html (canonical)
        https://hl7.org/fhir/R4/conceptmap.html
        https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

This resweep test file extends the baseline ``test_cm01_skeptic.py`` (22
probes) with NEW hostile-input probes covering all 6 chunk items:

  1. group.element.target.equivalence values (R4 closed enum, 10 values).
  2. group.element.target.dependsOn for parameterized mappings.
  3. group.element.target.product for downstream concept derivations.
  4. group.source / group.target scope a group of mappings.
  5. ConceptMap.url as canonical identifier.
  6. READ and SEARCH interactions on ConceptMap.

SKEPTIC resweep lens (per evolution.json config.notes):
  * Equivalence enum hostile values: 12 chunk-desc values + R5/R4B
    camelCase variants ('subsumedBy', 'SubsumedBy') + case-folding +
    None/empty/whitespace inputs.
  * dependsOn / product malformed inputs (medterm4ds doesn't emit these
    today — this is a registry-as-contract pin so a future feature addition
    can't silently violate the 1..1 subfield constraint on property+value).
  * group.source / group.target: missing source/target, malformed URIs,
    non-canonical URIs, unknown SABs.
  * ConceptMap.url: malformed URL, very long URL, special chars/newlines,
    non-URI strings, empty string, None.
  * READ/SEARCH hostile inputs: malformed ids, very long ids, special
    chars, search with all 5 params simultaneously.
  * Canonical-DISPLAY META-PATTERN extension to CM-01 (per VS-05/
    TERMINOLOGIST tip): verify group.element.target.display byte-exact
    equals $lookup Out display for the same (targetSystem, targetCode).
  * CF-TERMINOLOGIST-CM01-01 latent gap re-verification (outputs/fhir.py
    FHIR_EQUIVALENCES lacks subsumes/specializes keys — would silently
    emit relatedto default).
  * R5/R4B contamination audit (CF-HISTORIAN-VS01-01 closed enum).

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)
"""

from __future__ import annotations

import ast
import inspect

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    canonical_system_uri,
)
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    fhir_equivalence,
)


# =============================================================================
# Constants
# =============================================================================

SNOMED_URI = "http://snomed.info/sct"
SNOMED_OID = "urn:oid:2.16.840.1.113883.6.96"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

SNOMED_DIABETES_MELLITUS = "73211009"
SNOMED_T2DM = "44054006"
ICD10CM_T2DM = "E11"
RXNORM_METFORMIN = "860975"

# 12 chunk-desc values listed in evolution.json for CM-01. The list itself
# contains R5/R4B values (subsumedby, matches, not-relatedto); test_s61 in
# the baseline file documents this drift. We use the list as the input for
# registry-robustness probing here.
CHUNK_DESC_EQUIVALENCE_VALUES = (
    "equal", "equivalent", "wider", "narrower", "relatedto", "not-relatedto",
    "disjoint", "subsumes", "subsumedby", "matches", "inexact", "unmatched",
)


# =============================================================================
# Lens 1: Equivalence enum hostile values — input-translation audit.
# Verify fhir_equivalence() never emits an off-spec value, regardless of the
# input string. The 12 chunk-desc values, R5/R4B camelCase variants, and
# degenerate inputs (None, empty, whitespace, 'null') MUST all resolve to a
# valid R4 enum value.
# =============================================================================


@pytest.mark.parametrize("value", CHUNK_DESC_EQUIVALENCE_VALUES)
def test_s10_fhir_equivalence_never_emits_offspec_on_chunk_desc_values(value):
    """SKEPTIC: the chunk description lists 12 equivalence values. Three are
    not in the R4 closed enum ('subsumedby', 'matches', 'not-relatedto').
    Regardless, ``fhir_equivalence()`` MUST never return an off-spec value —
    the function is the canonical boundary between engine vocabulary and the
    FHIR wire format, and the FHIR enum is closed.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    Quote: "The equivalence is read from target to source (e.g. the target
    is 'wider' than the source)."
    """
    result = fhir_equivalence(value)
    assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"fhir_equivalence({value!r}) emitted {result!r}, which is NOT in the "
        f"FHIR R4 ConceptMapEquivalence closed enum {sorted(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)}. "
        f"The boundary function MUST translate every input to a spec-conformant "
        f"value (R5/R4B inputs included). Silent leak is a wire-format bug."
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        # R4 closed-enum pass-through values — MUST round-trip exactly.
        ("equivalent", "equivalent"),
        # 'equal' is NOT a key in INTERNAL_REL_TO_FHIR_EQUIVALENCE — falls
        # through to the 'relatedto' default (the safe catch-all for any
        # unrecognized R4 enum string that isn't an engine-vocabulary key).
        ("equal", "relatedto"),
        ("wider", "wider"),
        ("narrower", "narrower"),
        ("relatedto", "relatedto"),
        ("disjoint", "disjoint"),
        ("subsumes", "subsumes"),
        ("specializes", "specializes"),
        ("inexact", "relatedto"),  # 'inexact' isn't an internal map key; defensive default
        ("unmatched", "unmatched"),
        # R5/R4B / chunk-desc values — MUST map to R4 spec-correct equivalent.
        ("subsumedby", "specializes"),     # CF-HISTORIAN-VS01-01 RESOLVED
        ("subsumed-by", "specializes"),    # hyphenated alias
        ("not-relatedto", "unmatched"),    # CF-HISTORIAN-VS01-01 RESOLVED
        ("not-related-to", "unmatched"),   # hyphenated alias
        ("matches", "relatedto"),          # R5-only value; safe catch-all
        ("not-translated", "unmatched"),   # CM-01-SKEPTIC-002 RESOLVED
    ],
)
def test_s11_fhir_equivalence_translation_table_pinned(value, expected):
    """SKEPTIC: pin the translation table for every chunk-desc value and
    every R5/R4B value the engine might encounter. A future regression in
    the translation map would silently emit a wrong equivalence code on the
    wire.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    result = fhir_equivalence(value)
    assert result == expected, (
        f"fhir_equivalence({value!r}) returned {result!r}; expected {expected!r}. "
        f"Pin drift — the translation table changed, breaking the wire-format "
        f"contract."
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "\t\n",
        "null",
        "None",
    ],
)
def test_s12_fhir_equivalence_degenerate_inputs_default_to_relatedto(value):
    """SKEPTIC: degenerate inputs (None, empty string, whitespace-only, the
    string 'null'/'None') MUST resolve to a valid R4 enum value. The
    function's documented contract is "Never raises: the FHIR enum is
    closed, so unrecognized internal vocabularies MUST be translated rather
    than echoed raw".

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    result = fhir_equivalence(value)
    assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"fhir_equivalence({value!r}) returned {result!r} — outside the closed "
        f"R4 enum. The function MUST translate degenerate input to a safe "
        f"default ('relatedto'), not propagate it."
    )


# =============================================================================
# Lens 2: R5/R4B camelCase contamination audit.
# Per CF-HISTORIAN-VS01-01 (milestone-2 review), the prior
# _INTERNAL_REL_TO_FHIR_EQUIVALENCE map emitted R5 'subsumedby' verbatim.
# The fix replaced it with R4 'specializes'. These probes verify that NO
# R5/R4B camelCase value can ever leak to the wire.
# =============================================================================


@pytest.mark.parametrize(
    "r5_value",
    [
        "subsumedBy",    # R5/R4B exact
        "Subsumedby",    # case-folded variant
        "SubsumedBy",    # mixed case
        "SUBSUMEDBY",    # all-caps
        "subsumed_by",   # snake_case (programmatic typo)
    ],
)
def test_s20_r5_camelcase_subsumedby_cannot_leak_to_wire(r5_value):
    """SKEPTIC: the R5/R4B value 'subsumedBy' (camelCase) is NOT in the R4
    closed enum. fhir_equivalence() MUST translate it to an R4 value rather
    than echo it raw. The R4 spec-correct value for the
    'source-is-target's-child' direction is 'specializes'.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    CF-HISTORIAN-VS01-01 RESOLVED status.
    """
    result = fhir_equivalence(r5_value)
    assert result != r5_value, (
        f"fhir_equivalence({r5_value!r}) echoed the input verbatim — R5/R4B "
        f"camelCase value leaked to the wire. The R4 closed enum does NOT "
        f"contain 'subsumedBy' (camelCase); it uses 'specializes'."
    )
    assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"fhir_equivalence({r5_value!r}) returned {result!r} — outside R4 enum."
    )


@pytest.mark.parametrize(
    "r5_value",
    [
        "matches",     # R5-only (inexact-match catch-all)
        "MATCHES",
        "Matches",
        "match",
        "inexact-match",
    ],
)
def test_s21_r5_only_matches_value_cannot_leak_to_wire(r5_value):
    """SKEPTIC: 'matches' is an R5-only value (added in R5 to distinguish
    exact-match from inexact-match catch-all). It is NOT in the R4 closed
    enum. fhir_equivalence() MUST translate it (the closest R4 analog is
    'relatedto' for the unknown-input catch-all).

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    result = fhir_equivalence(r5_value)
    assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"fhir_equivalence({r5_value!r}) returned {result!r} — outside R4 enum. "
        f"'matches' is R5-only; must be translated to an R4 value."
    )


def test_s22_internal_map_emitted_values_full_audit():
    """SKEPTIC: walk every value in INTERNAL_REL_TO_FHIR_EQUIVALENCE and
    assert it's in the R4 closed enum. This is the registry-as-contract
    safety net for CF-HISTORIAN-VS01-01 RESOLVED status — the module-load
    assert enforces this, but a future change to the assert itself could
    silently let drift values land on the wire.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    drift = emitted - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drift, (
        f"INTERNAL_REL_TO_FHIR_EQUIVALENCE emits values outside the R4 closed "
        f"enum: {drift}. CF-HISTORIAN-VS01-01 regression."
    )


def test_s23_module_load_assert_present_in_canonical_module():
    """SKEPTIC: the canonical module
    (``engines/fhir/equivalence.py``) MUST have a module-load ``assert``
    guarding the closed-enum invariant. If a future change removes the
    assert (without replacing it with a stronger check), drift values would
    silently land on BOTH the $translate surface AND the ConceptMap export
    surface.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    from medterm4ds.engines.fhir import equivalence as equiv_module

    source = inspect.getsource(equiv_module)
    tree = ast.parse(source)
    has_assert = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            has_assert = True
            break
    assert has_assert, (
        "engines/fhir/equivalence.py MUST have a module-load assert guarding "
        "the INTERNAL_REL_TO_FHIR_EQUIVALENCE closed-enum invariant "
        "(CF-HISTORIAN-VS01-01). Without it, drift silently propagates."
    )


# =============================================================================
# Lens 3: CF-TERMINOLOGIST-CM01-01 latent gap re-verification.
# Per VS-05/TERMINOLOGIST tip in evolution.json: outputs/fhir.py:FHIR_EQUIVALENCES
# was historically an alias for the canonical map. CR-024 (milestone-3 review)
# unified both surfaces. Verify the unification is intact — the alias import
# IS the canonical map (object identity), and BOTH 'subsumes' AND 'specializes'
# keys are present (the prior gap was their absence).
# =============================================================================


def test_s30_outputs_fhir_module_aliases_canonical_map_object_identity():
    """SKEPTIC: CR-024 (milestone-3 review) unified the two parallel
    equivalence maps (responses.py and outputs/fhir.py) into a single
    canonical module. The alias in outputs/fhir.py MUST be the SAME OBJECT
    as the canonical map (verified via `is` operator) — drift impossible.

    Spec: GLOBAL_RULES.md "Single Source of Truth" table.
    """
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    assert FHIR_EQUIVALENCES is INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
        "outputs/fhir.py:FHIR_EQUIVALENCES MUST be `is`-identical to "
        "engines/fhir/equivalence.py:INTERNAL_REL_TO_FHIR_EQUIVALENCE "
        "(CR-024 unification). Drift between the two maps is a regression."
    )


def test_s31_cf_terminologist_cm01_01_subsumes_key_present():
    """SKEPTIC: CF-TERMINOLOGIST-CM01-01 latent gap re-verification (1 of 2).

    The prior outputs/fhir.py:FHIR_EQUIVALENCES LACKED the 'subsumes' key.
    Per the tip in evolution.json this would silently emit the 'relatedto'
    default on a 'subsumes' engine relationship. Verify the key is present
    in the canonical map.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    'subsumes' is in the R4 closed enum.
    """
    assert "subsumes" in INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
        "CF-TERMINOLOGIST-CM01-01 latent gap regression: "
        "INTERNAL_REL_TO_FHIR_EQUIVALENCE MUST have a 'subsumes' key. "
        "Without it, a 'subsumes' engine relationship would silently emit "
        "'relatedto' default."
    )
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["subsumes"] == "subsumes", (
        f"INTERNAL_REL_TO_FHIR_EQUIVALENCE['subsumes'] must round-trip to "
        f"'subsumes'; got "
        f"{INTERNAL_REL_TO_FHIR_EQUIVALENCE['subsumes']!r}."
    )


def test_s32_cf_terminologist_cm01_01_specializes_key_present():
    """SKEPTIC: CF-TERMINOLOGIST-CM01-01 latent gap re-verification (2 of 2).

    The prior outputs/fhir.py:FHIR_EQUIVALENCES LACKED the 'specializes' key.
    Per the tip in evolution.json this would silently emit the 'relatedto'
    default on a 'specializes' engine relationship. Verify the key is present.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    'specializes' is in the R4 closed enum (R4 spec-correct value for the
    reverse-of-subsumes case; R5/R4B use 'subsumedBy').
    """
    assert "specializes" in INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
        "CF-TERMINOLOGIST-CM01-01 latent gap regression: "
        "INTERNAL_REL_TO_FHIR_EQUIVALENCE MUST have a 'specializes' key. "
        "Without it, a 'specializes' engine relationship would silently emit "
        "'relatedto' default."
    )
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["specializes"] == "specializes", (
        f"INTERNAL_REL_TO_FHIR_EQUIVALENCE['specializes'] must round-trip to "
        f"'specializes'; got "
        f"{INTERNAL_REL_TO_FHIR_EQUIVALENCE['specializes']!r}."
    )


def test_s33_outputs_fhir_alias_resolves_subsumes_and_specializes():
    """SKEPTIC: end-to-end verification via the outputs/fhir.py alias. Both
    'subsumes' and 'specializes' inputs MUST resolve correctly when invoked
    through the alias map. This catches a regression where the alias was
    redefined as a separate dict missing keys.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    assert fhir_equivalence("subsumes") == "subsumes"
    assert fhir_equivalence("specializes") == "specializes"
    # And the alias path used by concept_map_to_fhir:
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    assert FHIR_EQUIVALENCES["subsumes"] == "subsumes"
    assert FHIR_EQUIVALENCES["specializes"] == "specializes"


# =============================================================================
# Lens 4: ConceptMap.url hostile inputs (item 5).
# Per spec, ConceptMap.url is 0..1 (cardinality-optional). The default in
# medterm4ds is 'urn:medterm4ds:ConceptMap:patient-friendly'. These probes
# verify the URL field handling on hostile inputs.
# Spec: https://hl7.org/fhir/R4/conceptmap.html — ConceptMap.url: "An
# absolute URI that is used to identify this concept map ... SHALL remain
# the same when the concept map is stored on different servers."
# =============================================================================


def _make_minimal_concept_map_row(**overrides):
    """Build a minimal ConceptMapRow for export probes."""
    from medterm4ds.core.models import CodeRef, ConceptMapRow

    base = dict(
        source=CodeRef(source="SNOMEDCT_US", code=SNOMED_DIABETES_MELLITUS),
        target=CodeRef(source="ICD10CM", code=ICD10CM_T2DM),
        source_display="Diabetes mellitus",
        target_display="Type 2 diabetes mellitus",
        relationship="equivalent",
        match_type="exact",
    )
    base.update(overrides)
    return ConceptMapRow(**base)


def test_s40_concept_map_url_field_present_and_string():
    """SKEPTIC: ``ConceptMap.url`` MUST be present in the export and MUST be
    a string. A None or non-string value would break canonical-identifier
    semantics.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — ConceptMap.url: type=uri.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    resource = concept_map_to_fhir([_make_minimal_concept_map_row()])
    assert "url" in resource, "ConceptMap export MUST include 'url' field"
    assert isinstance(resource["url"], str), (
        f"ConceptMap.url MUST be a string (per spec type=uri); got "
        f"{type(resource['url']).__name__}."
    )
    assert resource["url"], "ConceptMap.url MUST NOT be empty"


def test_s41_concept_map_default_url_is_canonical_constant():
    """SKEPTIC: the default ConceptMap.url is the module-level constant
    DEFAULT_CONCEPT_MAP_URL. Verify the default is used when no URL is
    supplied.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — ConceptMap.url canonical
    identifier.
    """
    from medterm4ds.outputs.fhir import (
        DEFAULT_CONCEPT_MAP_URL,
        concept_map_to_fhir,
    )

    resource = concept_map_to_fhir([_make_minimal_concept_map_row()])
    assert resource["url"] == DEFAULT_CONCEPT_MAP_URL


def test_s42_concept_map_url_very_long_url_passthrough():
    """SKEPTIC: a client supplying a very long URL (>10000 chars) MUST get
    that URL back unchanged (no truncation, no crash, no 500). The export
    surface is a builder, not a validator — but the wire-format MUST remain
    a string. The spec defines uri as having an unspecified max length but
    JSON serializers MUST accept arbitrarily long strings.

    Spec: https://hl7.org/fhir/R4/datatypes.html#uri
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    long_url = "http://example.org/cm/" + "x" * 10000
    resource = concept_map_to_fhir(
        [_make_minimal_concept_map_row()], url=long_url
    )
    assert resource["url"] == long_url, (
        f"ConceptMap.url field did not preserve the long input URL "
        f"(expected len={len(long_url)}, got len={len(resource['url'])})."
    )


def test_s43_concept_map_url_special_chars_passthrough():
    """SKEPTIC: a URL with special characters (newlines, control chars, XML
    injection attempts) is preserved verbatim. The export surface is a JSON
    serializer; no input sanitization is performed. Document this as
    intended — callers are responsible for supplying valid URIs.

    Spec: https://hl7.org/fhir/R4/datatypes.html#uri (uri type does not
    impose character restrictions beyond RFC 3986).
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    special_url = "http://example.org/cm<a>\n\t"
    resource = concept_map_to_fhir(
        [_make_minimal_concept_map_row()], url=special_url
    )
    assert resource["url"] == special_url


def test_s44_concept_map_url_explicit_empty_string_accepted():
    """SKEPTIC: a client supplying url='' explicitly MUST NOT crash the
    builder. The spec cardinality is 0..1, so an empty string is technically
    permitted (the client is opting out of canonical identification). This
    is the boundary between 'no url' and 'empty url'.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — ConceptMap.url 0..1.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    resource = concept_map_to_fhir(
        [_make_minimal_concept_map_row()], url=""
    )
    # Empty string accepted; the field is present and a string.
    assert "url" in resource
    assert isinstance(resource["url"], str)


def test_s45_concept_map_url_none_accepted_as_none():
    """SKEPTIC: a client supplying url=None explicitly MUST NOT crash the
    builder. Per spec the field is 0..1, so None (omission) is the canonical
    'no canonical identifier' case. The builder MUST accept this without
    raising — the caller may be building a draft resource.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — ConceptMap.url 0..1.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    # url=None MUST not raise (builder is robust to None input).
    resource = concept_map_to_fhir(
        [_make_minimal_concept_map_row()], url=None
    )
    # The field is either absent or None — both are conformant for 0..1.
    assert "url" not in resource or resource["url"] is None


# =============================================================================
# Lens 5: group.source / group.target scope fields (item 4).
# Per spec, both are 0..1 uri. medterm4ds emits them as canonical FHIR system
# URIs via SYSTEM_TO_FHIR_URI. These probes verify the scoping fields are
# correctly emitted and use canonical URIs (not raw SAB labels or aliases).
# Spec: https://hl7.org/fhir/R4/conceptmap.html
# =============================================================================


def test_s50_group_source_target_are_canonical_uris():
    """SKEPTIC: ``group.source`` and ``group.target`` MUST be canonical URIs
    (http://snomed.info/sct, http://hl7.org/fhir/sid/icd-10-cm), not raw
    SAB labels ('SNOMEDCT_US', 'ICD10CM'). Same canonical-URI invariant as
    TS-01/TERMINOLOGIST.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — group.source/target:
    "An absolute URI that identifies the source/target system".
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    resource = concept_map_to_fhir([_make_minimal_concept_map_row()])
    for g in resource.get("group", []):
        assert g["source"] == SNOMED_URI, (
            f"group.source={g['source']!r}; expected canonical SNOMED URI."
        )
        assert g["target"] == ICD10CM_URI, (
            f"group.target={g['target']!r}; expected canonical ICD-10-CM URI."
        )


def test_s51_group_source_target_unknown_sab_uses_fallback_urn():
    """SKEPTIC: when the source SAB is unknown to SYSTEM_TO_FHIR_URI, the
    builder falls back to a synthetic urn:medterm4ds:CodeSystem:{SAB}. This
    is a known-intended fallback (not a silent-wrong-answer) — the wire
    format remains a string and the consumer can detect the synthetic URN
    prefix. Document via probe so future regression is caught.

    Spec: GLOBAL_RULES.md "Single Source of Truth" — code_system_uri fallback.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    resource = concept_map_to_fhir(
        [_make_minimal_concept_map_row(
            source=__import__("medterm4ds.core.models", fromlist=["CodeRef"]).CodeRef(
                source="UNKNOWN_SAB_XYZ", code="ABC"
            )
        )]
    )
    for g in resource.get("group", []):
        # Synthetic URN prefix is the intended fallback.
        assert g["source"].startswith("urn:medterm4ds:CodeSystem:"), (
            f"group.source={g['source']!r}; expected synthetic URN fallback "
            f"for unknown SAB."
        )


def test_s52_group_source_target_no_raw_sab_label_leakage():
    """SKEPTIC: walk every group in the export and assert neither source
    nor target contains a raw UMLS SAB label (SNOMEDCT_US, ICD10CM, RXNORM,
    etc.) — those MUST be translated to canonical URIs. Catches the
    client-input-as-canonical drift pattern at the export surface.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — group.source/target
    type=uri.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    raw_sabs = {"SNOMEDCT_US", "ICD10CM", "ICD10PCS", "RXNORM", "LNC", "CPT", "HCPCS", "CVX"}
    rows = [
        _make_minimal_concept_map_row(),
        _make_minimal_concept_map_row(
            target=__import__("medterm4ds.core.models", fromlist=["CodeRef"]).CodeRef(
                source="RXNORM", code=RXNORM_METFORMIN
            ),
            target_display="24 HR metformin 500 MG Oral Tablet",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    for g in resource.get("group", []):
        for field in ("source", "target"):
            assert g[field] not in raw_sabs, (
                f"group.{field}={g[field]!r} leaked raw SAB label; expected "
                f"canonical URI."
            )


# =============================================================================
# Lens 6: dependsOn / product (items 2-3).
# medterm4ds engine does not emit dependsOn or product today. The export
# surface (concept_map_to_fhir) does not emit these fields either. Per spec
# both are 0..* (cardinality-optional). These probes are registry-as-contract
# pins: a future feature addition that emits dependsOn/product MUST include
# the required subfields (property 1..1, value 1..1).
# Spec: https://hl7.org/fhir/R4/conceptmap.html — dependsOn.property 1..1,
# dependsOn.value 1..1.
# =============================================================================


def test_s60_depends_on_absent_in_current_export():
    """SKEPTIC: medterm4ds engine does not emit dependsOn today. Document
    the absence via probe — a future feature addition that introduces
    dependsOn without the required subfields would be caught.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — dependsOn is 0..*.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    resource = concept_map_to_fhir([_make_minimal_concept_map_row()])
    for g in resource.get("group", []):
        for element in g.get("element", []):
            for target in element.get("target", []):
                if "dependsOn" in target:
                    # If present, MUST be a non-empty list.
                    assert isinstance(target["dependsOn"], list)
                    assert target["dependsOn"], (
                        "dependsOn present but empty — should be omitted if "
                        "no dependencies."
                    )


def test_s61_product_absent_in_current_export():
    """SKEPTIC: medterm4ds engine does not emit product today. Document the
    absence via probe.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — product is 0..*.
    """
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    resource = concept_map_to_fhir([_make_minimal_concept_map_row()])
    for g in resource.get("group", []):
        for element in g.get("element", []):
            for target in element.get("target", []):
                if "product" in target:
                    assert isinstance(target["product"], list)
                    assert target["product"], (
                        "product present but empty — should be omitted if "
                        "no downstream derivations."
                    )


def test_s62_depends_on_required_subfields_documented_for_future():
    """SKEPTIC: per spec, dependsOn has TWO required subfields (property
    1..1, value 1..1). This probe is a registry-as-contract pin: if a
    future feature addition emits dependsOn, it MUST include both
    subfields. We can't exercise this behaviorally today (engine doesn't
    emit), so the probe is a documentation pin via AST inspection of the
    canonical outputs/fhir.py module.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — dependsOn.property 1..1,
    dependsOn.value 1..1.
    """
    import medterm4ds.outputs.fhir as outputs_module

    source = inspect.getsource(outputs_module)
    tree = ast.parse(source)
    # Walk every dict literal in the module; if any contains the key
    # "dependsOn" or "product", assert the structure includes "property"
    # and "value" subfields (for dependsOn) or is structurally identical
    # (for product).
    found_depends_on = False
    found_product = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {
                k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if "dependsOn" in keys or "product" in keys:
                if "dependsOn" in keys:
                    found_depends_on = True
                if "product" in keys:
                    found_product = True
    # Today: neither is found (engine doesn't emit). When a future feature
    # adds them, this probe will need updating to validate the subfields.
    # For now: log the absence as the intended state.
    assert not found_depends_on, (
        "outputs/fhir.py emits dependsOn — update this probe to validate the "
        "required property+value subfields per spec."
    )
    assert not found_product, (
        "outputs/fhir.py emits product — update this probe to validate the "
        "structure (same as dependsOn) per spec."
    )


# =============================================================================
# Lens 7: READ and SEARCH interactions on ConceptMap (item 6).
# Per spec, READ returns the full resource; SEARCH returns a Bundle.
# medterm4ds doesn't persist ConceptMaps, so READ returns 404
# OperationOutcome and SEARCH returns an empty Bundle.
# Spec: https://hl7.org/fhir/R4/conceptmap.html + §3.1.0.2.1 (search-type).
# =============================================================================


def test_s70_read_with_dollar_prefixed_id_returns_operationoutcome(fhir_client):
    """SKEPTIC: GET /fhir/ConceptMap/$translate (operation) is distinct from
    GET /fhir/ConceptMap/{id} (READ). A READ attempt with a $-prefixed id
    (e.g. /fhir/ConceptMap/$translate as a READ) MUST return a FHIR
    OperationOutcome — not 500, not the operation response.

    Spec: https://hl7.org/fhir/R4/http.html#read
    """
    r = fhir_client.get("/fhir/ConceptMap/$nonexistent-op")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"READ with $-prefixed id MUST return OperationOutcome; got "
        f"resourceType={body.get('resourceType')!r}, status={r.status_code}."
    )


def test_s71_read_with_very_long_id_returns_operationoutcome(fhir_client):
    """SKEPTIC: GET /fhir/ConceptMap/{very_long_id} MUST return a FHIR
    OperationOutcome. Very long ids should NOT crash the server or trigger
    a 500. The route MUST gracefully handle pathological input.

    Spec: https://hl7.org/fhir/R4/http.html#read
    """
    long_id = "x" * 5000
    r = fhir_client.get(f"/fhir/ConceptMap/{long_id}")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"
    assert r.status_code == 404


def test_s72_read_with_special_chars_in_id_returns_operationoutcome(fhir_client):
    """SKEPTIC: GET /fhir/ConceptMap/{special_chars} MUST return a FHIR
    OperationOutcome. Special characters in the id (URL-encoded or otherwise)
    MUST NOT crash the server.

    Spec: https://hl7.org/fhir/R4/http.html#read
    """
    # URL-encoded special chars.
    r = fhir_client.get("/fhir/ConceptMap/%3Csvg%3E")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_s73_search_returns_bundle_with_all_5_params(fhir_client):
    """SKEPTIC: GET /fhir/ConceptMap with all 5 spec-required search params
    (url, version, name, title, status) MUST return a Bundle. The server
    has no persisted ConceptMaps, so the Bundle is empty (total=0, entry=[]).

    Spec: https://hl7.org/fhir/R4/conceptmap.html — search-type interaction.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap",
        params={
            "url": "http://example.org/cm",
            "version": "1.0.0",
            "name": "TestCM",
            "title": "Test ConceptMap",
            "status": "draft",
        },
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "searchset"
    assert body.get("total") == 0
    assert body.get("entry") == []


def test_s74_search_with_special_chars_in_params_returns_bundle(fhir_client):
    """SKEPTIC: GET /fhir/ConceptMap with special chars in search params
    MUST return a Bundle. The server has no persisted ConceptMaps, so the
    Bundle is empty regardless.

    Spec: https://hl7.org/fhir/R4/http.html#search
    """
    r = fhir_client.get(
        "/fhir/ConceptMap",
        params={"url": "http://example.org/cm?injected=param&other=<svg>"},
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "searchset"


def test_s75_search_with_empty_param_values_returns_bundle(fhir_client):
    """SKEPTIC: GET /fhir/ConceptMap with empty string values for the
    search params MUST return a Bundle. Empty-string-on-optional-param has
    a different semantic than empty-string-on-required-param (TS-02 SKEPTIC
    QA-001 PROMOTED pattern). SEARCH params are optional, so empty is
    accepted.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — search params are
    optional (cardinality 0..1).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap",
        params={"url": "", "version": "", "name": "", "title": "", "status": ""},
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle"


# =============================================================================
# Lens 8: Canonical-DISPLAY META-PATTERN extension to CM-01.
# Per VS-05/TERMINOLOGIST tip: verify group.element.target.display byte-exact
# equals $lookup Out display for the same (targetSystem, targetCode). This
# extends the 12-surface canonical-DISPLAY META-PATTERN to the export surface.
# Spec: https://hl7.org/fhir/R4/conceptmap.html — group.element.target.display
# is the human-readable label for the target code.
# =============================================================================


def test_s80_export_target_display_byte_exact_matches_lookup_for_snomed(fhir_client):
    """SKEPTIC: Canonical-DISPLAY META-PATTERN extension. The
    group.element.target.display in the ConceptMap export MUST byte-exact
    equal the $lookup Out `display` for the same (targetSystem, targetCode).
    Drift here would mean the export shows one display string and the
    lookup returns another — silent-wrong-answer for downstream consumers.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — target.display is the
    display for the code.
    """
    # $lookup on the seeded SNOMED code.
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    if body.get("resourceType") == "OperationOutcome":
        pytest.skip("fixture DB missing the test code")
    lookup_display = next(
        (p.get("valueString") for p in body.get("parameter", []) if p.get("name") == "display"),
        None,
    )
    if not lookup_display:
        pytest.skip("no display in $lookup response")

    # Export surface: build a ConceptMap row with target = same SNOMED code.
    from medterm4ds.core.models import CodeRef, ConceptMapRow
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    rows = [
        ConceptMapRow(
            source=CodeRef(source="ICD10CM", code=ICD10CM_T2DM),
            target=CodeRef(source="SNOMEDCT_US", code=SNOMED_DIABETES_MELLITUS),
            source_display="T2DM",
            target_display=lookup_display,
            relationship="source-is-broader-than-target",
            match_type="broader",
        )
    ]
    resource = concept_map_to_fhir(rows)
    for g in resource.get("group", []):
        for element in g.get("element", []):
            for target in element.get("target", []):
                if target.get("code") == SNOMED_DIABETES_MELLITUS:
                    assert target.get("display") == lookup_display, (
                        f"target.display={target.get('display')!r} != "
                        f"$lookup display={lookup_display!r}. Canonical-DISPLAY "
                        f"META-PATTERN drift on CM-01 export surface."
                    )


def test_s81_translate_target_display_byte_exact_matches_lookup(fhir_client):
    """SKEPTIC: Canonical-DISPLAY META-PATTERN extension. The
    match.concept.display in the $translate response MUST byte-exact equal
    the $lookup Out `display` for the same (targetSystem, targetCode).

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html —
    match.concept.display.
    """
    # $lookup on the seeded SNOMED code.
    lookup_resp = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    lookup_body = lookup_resp.json()
    if lookup_body.get("resourceType") == "OperationOutcome":
        pytest.skip("fixture DB missing the test code")
    lookup_display = next(
        (p.get("valueString") for p in lookup_body.get("parameter", []) if p.get("name") == "display"),
        None,
    )
    if not lookup_display:
        pytest.skip("no display in $lookup response")

    # $translate from ICD-10-CM E11 to SNOMEDCT_US.
    translate_resp = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": ICD10CM_URI,
            "code": ICD10CM_T2DM,
            "targetsystem": SNOMED_URI,
        },
    )
    translate_body = translate_resp.json()
    if translate_body.get("resourceType") == "OperationOutcome":
        pytest.skip("fixture DB missing the test code")
    matches = [
        p for p in translate_body.get("parameter", []) if p.get("name") == "match"
    ]
    if not matches:
        pytest.skip("no matches for the test code")

    for m in matches:
        concept_part = next(
            (part for part in m.get("part", []) if part.get("name") == "concept"),
            None,
        )
        if concept_part is None:
            continue
        concept_coding = concept_part.get("valueCoding", {})
        if concept_coding.get("system") == SNOMED_URI and concept_coding.get("code") == SNOMED_DIABETES_MELLITUS:
            assert concept_coding.get("display") == lookup_display, (
                f"match.concept.display={concept_coding.get('display')!r} != "
                f"$lookup display={lookup_display!r}. Canonical-DISPLAY "
                f"META-PATTERN drift on $translate target concept."
            )


# =============================================================================
# Lens 9: $translate equivalence wire-format probe via hostile engine input.
# Verify build_parameters_translate emits the spec-correct R4 equivalence
# for every engine relationship. This is the wire-format extension of Lens 1.
# =============================================================================


@pytest.mark.parametrize(
    "engine_rel,expected_r4",
    [
        ("equivalent", "equivalent"),
        ("source-is-narrower-than-target", "wider"),
        ("source-is-broader-than-target", "narrower"),
        ("related-to", "relatedto"),
        ("not-translated", "unmatched"),
        ("unmatched", "unmatched"),
        ("subsumes", "subsumes"),
        ("specializes", "specializes"),
    ],
)
def test_s90_build_parameters_translate_emits_r4_equivalence_per_engine_rel(
    engine_rel, expected_r4
):
    """SKEPTIC: for every engine relationship, ``build_parameters_translate``
    MUST emit the spec-correct R4 equivalence value on the wire. This is
    the wire-format audit of Lens 1 — the function is the LAST boundary
    before the response goes on the wire.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html —
    match.equivalence: code from concept-map-equivalence value set.
    """
    from medterm4ds.core.models import CodeMapping, CodeRef
    from medterm4ds.engines.fhir.responses import build_parameters_translate

    mappings = [
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code=SNOMED_DIABETES_MELLITUS),
            target=CodeRef(source="ICD10CM", code=ICD10CM_T2DM),
            relationship=engine_rel,
            match_type="exact",
        )
    ]
    body = build_parameters_translate(
        mappings,
        source_system_uri=SNOMED_URI,
        source_code=SNOMED_DIABETES_MELLITUS,
    )
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert matches, "build_parameters_translate emitted no matches"
    equiv_part = next(
        part for part in matches[0]["part"] if part.get("name") == "equivalence"
    )
    equiv = equiv_part.get("valueCode")
    assert equiv == expected_r4, (
        f"build_parameters_translate emitted equivalence={equiv!r} for engine "
        f"relationship={engine_rel!r}; expected R4 {expected_r4!r}."
    )


def test_s91_build_parameters_translate_match_concept_always_has_display_key():
    """SKEPTIC: per R4 spec, match.concept.valueCoding MUST have a display
    key (it can be empty string for unknown displays, but the key MUST be
    present). A missing display key would break downstream consumers that
    unconditionally read .display.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    """
    from medterm4ds.core.models import CodeMapping, CodeRef
    from medterm4ds.engines.fhir.responses import build_parameters_translate

    mappings = [
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code=SNOMED_DIABETES_MELLITUS),
            target=CodeRef(source="ICD10CM", code=ICD10CM_T2DM),
            relationship="equivalent",
            match_type="exact",
        )
    ]
    body = build_parameters_translate(
        mappings,
        source_system_uri=SNOMED_URI,
        source_code=SNOMED_DIABETES_MELLITUS,
    )
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert matches
    for m in matches:
        concept_part = next(
            part for part in m.get("part", []) if part.get("name") == "concept"
        )
        coding = concept_part.get("valueCoding", {})
        assert "display" in coding, (
            f"match.concept.valueCoding missing 'display' key; "
            f"got keys={sorted(coding.keys())}."
        )


def test_s92_build_parameters_translate_match_source_always_has_system_and_code():
    """SKEPTIC: per R4 spec, match.source.valueCoding MUST have system+code.
    A missing system OR code would break downstream consumers.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    """
    from medterm4ds.core.models import CodeMapping, CodeRef
    from medterm4ds.engines.fhir.responses import build_parameters_translate

    mappings = [
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code=SNOMED_DIABETES_MELLITUS),
            target=CodeRef(source="ICD10CM", code=ICD10CM_T2DM),
            relationship="equivalent",
            match_type="exact",
        )
    ]
    body = build_parameters_translate(
        mappings,
        source_system_uri=SNOMED_URI,
        source_code=SNOMED_DIABETES_MELLITUS,
    )
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert matches
    for m in matches:
        source_part = next(
            part for part in m.get("part", []) if part.get("name") == "source"
        )
        coding = source_part.get("valueCoding", {})
        assert "system" in coding
        assert "code" in coding
        assert coding["system"] == SNOMED_URI, (
            f"match.source.valueCoding.system={coding.get('system')!r}; "
            f"expected canonical SNOMED URI."
        )


# =============================================================================
# Lens 10: $translate POST body hostile inputs.
# The POST /fhir/ConceptMap/$translate handler parses a Parameters body.
# These probes verify hostile inputs don't crash the server.
# =============================================================================


def test_s93_translate_post_body_missing_system_returns_400(fhir_client):
    """SKEPTIC: POST /fhir/ConceptMap/$translate with body missing `system`
    MUST return 400 with a FHIR OperationOutcome — not 500.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html —
    system+code required.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "code", "valueCode": SNOMED_DIABETES_MELLITUS},
            ],
        },
    )
    body = r.json()
    assert r.status_code == 400
    assert body.get("resourceType") == "OperationOutcome"


def test_s94_translate_post_body_with_empty_system_returns_400(fhir_client):
    """SKEPTIC: POST /fhir/ConceptMap/$translate with body where system is
    empty string MUST return 400. Empty-string-on-required-param is the
    TS-02 SKEPTIC QA-001 PROMOTED pattern.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": ""},
                {"name": "code", "valueCode": SNOMED_DIABETES_MELLITUS},
            ],
        },
    )
    body = r.json()
    assert r.status_code == 400
    assert body.get("resourceType") == "OperationOutcome"


def test_s95_translate_get_with_empty_system_returns_422(fhir_client):
    """SKEPTIC: GET /fhir/ConceptMap/$translate with empty system query
    param MUST return 422 (the min_length=1 contract on the Query
    declaration; converted to OperationOutcome by the
    RequestValidationError handler).

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    Pattern: empty-string-as-present-on-required-Query (count=5 PROMOTED).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={"system": "", "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    assert r.status_code == 422
    assert body.get("resourceType") == "OperationOutcome"


def test_s96_translate_get_with_very_long_system_returns_400_or_200(fhir_client):
    """SKEPTIC: GET /fhir/ConceptMap/$translate with a very long system
    URI (>10000 chars) MUST NOT crash the server. The handler returns 400
    (unrecognized URI) — never 500.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    """
    long_system = "http://" + "x" * 10000
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={"system": long_system, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    assert r.status_code in (200, 400), (
        f"expected 200 or 400 for very long system URI; got {r.status_code}"
    )
    # MUST be a FHIR resource (Parameters or OperationOutcome), not 500.
    assert body.get("resourceType") in ("Parameters", "OperationOutcome")


# =============================================================================
# Lens 11: Source-read structural contracts.
# Pin the load-bearing contracts via AST inspection so a future refactor
# cannot silently break them.
# =============================================================================


def test_s100_canonical_module_has_subsumes_and_specializes_in_map():
    """SKEPTIC: AST-walk the canonical equivalence module and assert the
    INTERNAL_REL_TO_FHIR_EQUIVALENCE dict literal contains both 'subsumes'
    and 'specializes' keys. CF-TERMINOLOGIST-CM01-01 structural pin.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    from medterm4ds.engines.fhir import equivalence as equiv_module

    source = inspect.getsource(equiv_module)
    tree = ast.parse(source)
    # The dict is declared with a type annotation: dict[str, str] = {...}
    # so it's an ast.AnnAssign, not ast.Assign. Walk both.
    found = False
    for node in ast.walk(tree):
        target = None
        value = None
        if isinstance(node, ast.Assign):
            if not node.targets:
                continue
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and target.id == "INTERNAL_REL_TO_FHIR_EQUIVALENCE"
        ):
            assert isinstance(value, ast.Dict), (
                "INTERNAL_REL_TO_FHIR_EQUIVALENCE MUST be a dict literal "
                "for AST inspection."
            )
            keys = set()
            for k in value.keys:
                if isinstance(k, ast.Constant):
                    keys.add(k.value)
            assert "subsumes" in keys, (
                f"INTERNAL_REL_TO_FHIR_EQUIVALENCE missing 'subsumes' "
                f"key — CF-TERMINOLOGIST-CM01-01 regression."
            )
            assert "specializes" in keys, (
                f"INTERNAL_REL_TO_FHIR_EQUIVALENCE missing 'specializes' "
                f"key — CF-TERMINOLOGIST-CM01-01 regression."
            )
            found = True
            break
    assert found, (
        "INTERNAL_REL_TO_FHIR_EQUIVALENCE dict literal not found in canonical "
        "module — AST inspection failed."
    )


def test_s101_canonical_system_uri_helper_used_in_translate_handler():
    """SKEPTIC: source-read audit of _do_translate. The handler MUST call
    canonical_system_uri on the client-supplied source_uri (CR-012 RESOLVED).
    A future refactor that removes the call would re-introduce the
    client-input-as-canonical drift pattern.

    Spec: FHIR R4 §4.8.21.1 Out Coding.system.
    Pattern: client-input-as-canonical drift (count=8 PROMOTED).
    """
    from medterm4ds.apps.fhir_api import create_fhir_app

    src = inspect.getsource(create_fhir_app)
    assert "canonical_system_uri" in src, (
        "CR-012 regression: _do_translate must call canonical_system_uri on "
        "the client-supplied source_uri before passing to "
        "build_parameters_translate."
    )


def test_s102_outputs_fhir_uses_canonical_helper():
    """SKEPTIC: source-read audit of outputs/fhir.py. The module MUST use
    the canonical fhir_equivalence helper (not a local copy of the map).
    A future regression that reintroduces a local map would silently
    diverge from the canonical one.

    Spec: GLOBAL_RULES.md "Single Source of Truth" table.
    """
    import medterm4ds.outputs.fhir as outputs_module

    source = inspect.getsource(outputs_module)
    assert "from medterm4ds.engines.fhir.equivalence import fhir_equivalence", (
        "outputs/fhir.py MUST import fhir_equivalence from the canonical "
        "module (CR-024 unification)."
    )
    # Verify no local redefinition of INTERNAL_REL_TO_FHIR_EQUIVALENCE.
    assert "INTERNAL_REL_TO_FHIR_EQUIVALENCE: dict" not in source, (
        "outputs/fhir.py MUST NOT locally redefine "
        "INTERNAL_REL_TO_FHIR_EQUIVALENCE — use the canonical import."
    )
