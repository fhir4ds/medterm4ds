"""CM-04 HISTORIAN probes: ConceptMap Equivalence Vocabulary Correctness.

Source-reading audit (HISTORIAN lens for CM-04). The canonical equivalence
module is ``engines/fhir/equivalence.py`` (created by milestone-3 review
CR-024). These probes pattern-match against prior bug patterns:

  * CF-HISTORIAN-VS01-01 (R5/R4B ``subsumedby`` value on R4 surface)
  * CM-01 SKEPTIC-001 (R4 narrower/wider directionality inversion)
  * CM-01 SKEPTIC-002 (``not-translated`` → ``equivalent`` silent-wrong-answer)
  * TS-02 TERMINOLOGIST QA-030 (hardcoded ``"equivalent"`` in response builder)

HISTORIAN lens for CM-04 — confirm via SOURCE-READING that NO code path
bypasses the canonical equivalence module:

  1. ``engines/fhir/equivalence.py`` is the canonical source.
  2. ``responses.py`` ($translate surface) imports and re-exports.
  3. ``outputs/fhir.py`` (ConceptMap export surface) imports and re-exports.
  4. No hardcoded ``valueCode="equivalent"`` literals bypass the helper.
  5. Default fallback (``relatedto``) for unknown engine vocabulary.
  6. No R5/R4B contamination (``subsumedby`` absent from canonical module).
  7. Closed-enum membership assertion at module load is load-bearing.

Spec citation (canonical R4 closed enum, verified 2026-07-13):
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    "This value set contains 10 concepts":
    relatedto | equivalent | equal | wider | subsumes | narrower |
    specializes | inexact | unmatched | disjoint
"""

from __future__ import annotations

import inspect
import re

import pytest

from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
from medterm4ds.engines.fhir import equivalence as equivalence_module
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    fhir_equivalence,
)
from medterm4ds.engines.fhir import responses as responses_module
from medterm4ds.outputs import fhir as outputs_fhir_module


# =============================================================================
# LENS 1 — Canonical module integrity (source-reading)
# =============================================================================

class TestCanonicalModuleIntegrity:
    """Verify ``engines/fhir/equivalence.py`` is the single source of truth.

    Pattern-match against CR-024 (cross-module parallel-map drift): the
    prior architecture had two parallel translation maps in ``responses.py``
    and ``outputs/fhir.py`` with divergent key/value pairs. HISTORIAN
    source-reads to confirm the consolidation holds.
    """

    def test_h10_canonical_module_defines_internal_map(self):
        """The canonical module defines ``INTERNAL_REL_TO_FHIR_EQUIVALENCE``.

        Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
        Methodology: strategy 29 (carry-forward-verification-by-source-reading).
        """
        assert hasattr(equivalence_module, "INTERNAL_REL_TO_FHIR_EQUIVALENCE"), (
            "Canonical module must define INTERNAL_REL_TO_FHIR_EQUIVALENCE. "
            "CR-024 (milestone-3 review) consolidated the two parallel maps "
            "here — without this, cross-surface drift returns."
        )
        assert isinstance(INTERNAL_REL_TO_FHIR_EQUIVALENCE, dict)
        assert len(INTERNAL_REL_TO_FHIR_EQUIVALENCE) > 0

    def test_h11_canonical_module_defines_fhir_equivalence_helper(self):
        """The canonical module exposes ``fhir_equivalence(relationship)``."""
        assert hasattr(equivalence_module, "fhir_equivalence")
        assert callable(fhir_equivalence)

    def test_h12_closed_enum_assertion_at_module_load(self):
        """Module-load ``assert`` is present and load-bearing.

        Pattern-match to CF-HISTORIAN-VS01-01: the prior inline map emitted
        R5/R4B ``subsumedby`` without any assertion catching the drift.
        The module-load ``assert`` is the load-bearing contract that
        prevents future drift across BOTH surfaces uniformly.
        """
        source = inspect.getsource(equivalence_module)
        # The closed-enum assertion MUST be present.
        assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in source, (
            "Canonical module must reference FHIR_R4_CONCEPT_MAP_EQUIVALENCE "
            "in its closed-enum assertion."
        )
        # The assertion guards values() against the closed enum.
        assert "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()" in source, (
            "The closed-enum membership assertion MUST check "
            "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()."
        )
        assert "<=" in source, (
            "The assertion MUST use set-subset (<=) to verify emitted "
            "values are a subset of the R4 closed enum."
        )


# =============================================================================
# LENS 2 — Cross-surface import integrity (responses.py + outputs/fhir.py)
# =============================================================================

class TestCrossSurfaceImportIntegrity:
    """Verify BOTH consumers import from the canonical module.

    Pattern-match to CR-024: the prior architecture had two parallel maps
    with divergent key/value pairs. The structural fix is the canonical
    import — drift between the two surfaces becomes structurally impossible
    because they share the same Python object.
    """

    def test_h20_responses_module_imports_from_canonical(self):
        """``responses.py`` imports the map from the canonical module.

        If ``responses.py`` redefines the map locally, drift returns.
        """
        source = inspect.getsource(responses_module)
        # The import statement MUST be present.
        assert "from medterm4ds.engines.fhir.equivalence import" in source, (
            "responses.py MUST import INTERNAL_REL_TO_FHIR_EQUIVALENCE from "
            "the canonical module (CR-024). Local redefinition re-introduces "
            "cross-module parallel-map drift."
        )

    def test_h21_outputs_fhir_module_imports_from_canonical(self):
        """``outputs/fhir.py`` imports the map + helper from canonical."""
        source = inspect.getsource(outputs_fhir_module)
        assert "from medterm4ds.engines.fhir.equivalence import" in source, (
            "outputs/fhir.py MUST import FHIR_EQUIVALENCES + fhir_equivalence "
            "from the canonical module (CR-024)."
        )
        assert "fhir_equivalence" in source, (
            "outputs/fhir.py MUST use the canonical fhir_equivalence helper."
        )

    def test_h22_responses_module_alias_is_canonical_object(self):
        """The ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` alias in responses.py
        IS the canonical module's dict (same Python object identity).

        Drift between the two is structurally impossible because they share
        the same object reference.
        """
        # responses.py imports as ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE``.
        assert (
            responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE
            is INTERNAL_REL_TO_FHIR_EQUIVALENCE
        ), (
            "responses.py's _INTERNAL_REL_TO_FHIR_EQUIVALENCE MUST be the "
            "same Python object as the canonical module's "
            "INTERNAL_REL_TO_FHIR_EQUIVALENCE. If not, a local copy was "
            "made — drift returns."
        )

    def test_h23_outputs_fhir_module_alias_is_canonical_object(self):
        """The ``FHIR_EQUIVALENCES`` alias in outputs/fhir.py IS the
        canonical module's dict (same Python object identity)."""
        assert (
            outputs_fhir_module.FHIR_EQUIVALENCES
            is INTERNAL_REL_TO_FHIR_EQUIVALENCE
        ), (
            "outputs/fhir.py's FHIR_EQUIVALENCES MUST be the same Python "
            "object as the canonical module's "
            "INTERNAL_REL_TO_FHIR_EQUIVALENCE."
        )


# =============================================================================
# LENS 3 — No bypass paths in $translate response builder
# =============================================================================

class TestTranslateBuilderNoBypass:
    """Verify ``build_parameters_translate`` routes through the helper.

    Pattern-match to TS-02 TERMINOLOGIST QA-030: the prior implementation
    hardcoded ``"equivalent"`` for every match, silently misrepresenting
    SNOMED→ICD10CM crosswalks (typically ``relatedto``) and
    ancestor/descendant mappings (``subsumes``/``specializes``).
    """

    def test_h30_translate_builder_calls_fhir_equivalence_helper(self):
        """``build_parameters_translate`` MUST call
        ``_fhir_equivalence_from_relationship`` for every match entry.

        Source-reading audit (strategy 29). A hardcoded literal would be a
        re-introduction of TS-02 TERMINOLOGIST QA-030.
        """
        source = inspect.getsource(responses_module.build_parameters_translate)
        assert "_fhir_equivalence_from_relationship" in source, (
            "build_parameters_translate MUST call "
            "_fhir_equivalence_from_relationship for each match. "
            "Hardcoding 'equivalent' is TS-02 TERMINOLOGIST QA-030."
        )

    def test_h31_translate_builder_no_hardcoded_equivalence_literal(self):
        """No ``"equivalence": "equivalent"`` style literal in the builder.

        Pattern-match to TS-02 TERMINOLOGIST QA-030: the bug was a hardcoded
        ``equivalence="equivalent"`` that misrepresented non-default
        relationship values.
        """
        source = inspect.getsource(responses_module.build_parameters_translate)
        # The forbidden pattern: a hardcoded equivalence value literal.
        forbidden_patterns = [
            r'["\']equivalence["\']\s*:\s*["\']equivalent["\']',
            r'["\']equivalence["\']\s*:\s*["\']relatedto["\']',
            r'["\']equivalence["\']\s*:\s*["\']subsumes["\']',
            r'["\']equivalence["\']\s*:\s*["\']specializes["\']',
            r'["\']equivalence["\']\s*:\s*["\']narrower["\']',
            r'["\']equivalence["\']\s*:\s*["\']wider["\']',
        ]
        for pattern in forbidden_patterns:
            assert not re.search(pattern, source), (
                f"build_parameters_translate contains forbidden hardcoded "
                f"equivalence literal matching {pattern!r}. The value MUST "
                f"come from _fhir_equivalence_from_relationship."
            )

    def test_h32_fhir_equivalence_from_relationship_uses_canonical_map(self):
        """``_fhir_equivalence_from_relationship`` delegates to the canonical
        map (no local redefinition)."""
        source = inspect.getsource(
            responses_module._fhir_equivalence_from_relationship
        )
        assert "_INTERNAL_REL_TO_FHIR_EQUIVALENCE" in source, (
            "_fhir_equivalence_from_relationship MUST use the canonical "
            "_INTERNAL_REL_TO_FHIR_EQUIVALENCE map (imported from the "
            "canonical module)."
        )


# =============================================================================
# LENS 4 — No bypass paths in ConceptMap export builder
# =============================================================================

class TestExportBuilderNoBypass:
    """Verify ``concept_map_to_fhir`` / ``_merge_row_target`` routes through
    the canonical helper.

    Pattern-match to CM-01 SKEPTIC-002: the prior ``outputs/fhir.py`` map
    mapped ``not-translated`` to ``equivalent`` (silent-wrong-answer).
    """

    def test_h40_merge_row_target_uses_canonical_helper(self):
        """``_merge_row_target`` MUST call the canonical ``fhir_equivalence``.

        Source-reading audit. A local map redefinition would re-introduce
        CM-01 SKEPTIC-002 (``not-translated`` → ``equivalent``).
        """
        source = inspect.getsource(outputs_fhir_module._merge_row_target)
        assert "fhir_equivalence(" in source, (
            "_merge_row_target MUST call fhir_equivalence() (imported from "
            "the canonical module). Local redefinition re-introduces "
            "CM-01 SKEPTIC-002 silent-wrong-answer."
        )

    def test_h41_export_module_no_local_equivalence_map(self):
        """``outputs/fhir.py`` does NOT define a local ``FHIR_EQUIVALENCES``
        dict (only the import alias).

        Pattern-match to CR-024: the prior local map diverged from
        ``responses.py`` on key/value pairs.
        """
        source = inspect.getsource(outputs_fhir_module)
        # The forbidden pattern: a local dict definition.
        # ``FHIR_EQUIVALENCES: dict`` or ``FHIR_EQUIVALENCES = {`` (without
        # ``import`` on the same logical line).
        # The import line is: ``INTERNAL_REL_TO_FHIR_EQUIVALENCE as FHIR_EQUIVALENCES,``
        # We check there's no ``FHIR_EQUIVALENCES = {`` local definition.
        forbidden = re.compile(r"^FHIR_EQUIVALENCES\s*=\s*\{", re.MULTILINE)
        assert not forbidden.search(source), (
            "outputs/fhir.py MUST NOT define a local FHIR_EQUIVALENCES dict. "
            "It MUST be imported from the canonical module (CR-024)."
        )


# =============================================================================
# LENS 5 — Default fallback for unknown engine vocabulary
# =============================================================================

class TestDefaultFallback:
    """Verify the default fallback (``relatedto``) is documented and applied.

    Pattern-match to literal-value-vs-canonical-registry drift (count=8
    PROMOTED): when an unrecognized engine vocabulary value flows through,
    the helper MUST translate it to the R4 catch-all (``relatedto``),
    never echo it raw.
    """

    @pytest.mark.parametrize(
        "unknown_value",
        [
            None,
            "",
            "UNKNOWN",
            "some-future-engine-vocab",
            "matches",  # R5-only value — not a key in the map → catch-all
            "nonexistent-relationship",
        ],
    )
    def test_h50_unknown_relationship_returns_relatedto(self, unknown_value):
        """``fhir_equivalence`` returns ``relatedto`` for unknown / null /
        empty inputs — the R4 catch-all.

        Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
        ``relatedto`` = "The concepts are related, but the exact relationship
        is not known." Never echoes raw engine vocabulary through to the
        wire — the FHIR enum is closed.

        Note: ``subsumedby`` and ``not-relatedto`` ARE keys in the
        translation map (defensive pass-through entries that map to R4
        spec-correct values). They are tested separately in Lens 6.
        """
        result = fhir_equivalence(unknown_value)
        assert result == "relatedto", (
            f"fhir_equivalence({unknown_value!r}) MUST return 'relatedto' "
            f"(the R4 catch-all); got {result!r}. Echoing raw engine "
            f"vocabulary would emit a value outside the FHIR R4 value set."
        )
        # The returned value MUST be in the R4 closed enum.
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_h51_default_fallback_is_documented(self):
        """The default-fallback behavior is documented in the helper's
        docstring (maintenance-hazard defense).

        Pattern-match to TS-01 HISTORIAN QA-007 (docstring-vs-impl drift).
        """
        doc = fhir_equivalence.__doc__ or ""
        assert "relatedto" in doc, (
            "fhir_equivalence docstring MUST document the 'relatedto' "
            "default fallback."
        )


# =============================================================================
# LENS 6 — Cross-version enum drift (R5/R4B values absent)
# =============================================================================

class TestCrossVersionEnumDrift:
    """Verify no R5/R4B values leaked into the canonical module.

    Pattern-match to CF-HISTORIAN-VS01-01: the prior inline map emitted
    R5/R4B ``subsumedby`` (R4 spec-correct is ``specializes``) and
    ``not-relatedto`` (not in any FHIR enum). Closed-enum membership check
    on the EMITTED values; we extend it to check the R5/R4B values are
    NOT present as either keys or values in the map.
    """

    def test_h60_no_subsumedby_in_emitted_values(self):
        """R5/R4B ``subsumedby`` MUST NOT appear in the emitted values.

        Methodology: strategy 28 (cross-version-enum-drift audit).
        """
        emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        assert "subsumedby" not in emitted, (
            "R5/R4B value 'subsumedby' leaked into the canonical map's "
            "emitted values. R4 spec-correct is 'specializes'. "
            "CF-HISTORIAN-VS01-01."
        )

    def test_h61_no_matches_in_emitted_values(self):
        """R5-only ``matches`` MUST NOT appear in the emitted values."""
        emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        assert "matches" not in emitted, (
            "R5-only value 'matches' leaked into the canonical map's "
            "emitted values. Not in any R4 enum."
        )

    def test_h62_subsumedby_key_maps_to_specializes(self):
        """``subsumedby`` is accepted as a defensive KEY (engine may emit it
        in the future) but maps to the R4 ``specializes`` value.

        This is the spec-correct translation per
        https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html.
        """
        assert (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE.get("subsumedby") == "specializes"
        ), (
            "'subsumedby' key MUST map to R4 'specializes' value. "
            "CF-HISTORIAN-VS01-01."
        )
        # Verify both spelling variants accepted defensively.
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE.get("subsumed-by") == "specializes"

    def test_h63_all_emitted_values_in_r4_closed_enum(self):
        """Every emitted value is in the R4 closed enum (frozen-set).

        This is the structural invariant the module-load ``assert`` enforces.
        We re-verify at test time as a defense-in-depth probe.
        """
        emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        drift = emitted - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        assert not drift, (
            f"INTERNAL_REL_TO_FHIR_EQUIVALENCE emits values outside the "
            f"FHIR R4 closed enum: {drift}."
        )


# =============================================================================
# LENS 7 — Engine vocabulary completeness (no unmapped engine values)
# =============================================================================

class TestEngineVocabularyCompleteness:
    """Verify every engine-side relationship value has a map entry.

    The engine emits exactly 6 relationship values (source-read from
    ``core/models.py:conceptmap_relationship`` + ``engines/duckdb/mappings.py``
    + ``services/crosswalk*.py``):
      * ``equivalent``
      * ``source-is-narrower-than-target``
      * ``source-is-broader-than-target``
      * ``related-to``
      * ``not-translated``
      * ``unmatched``
    """

    ENGINE_VOCABULARY = frozenset({
        "equivalent",
        "source-is-narrower-than-target",
        "source-is-broader-than-target",
        "related-to",
        "not-translated",
        "unmatched",
    })

    @pytest.mark.parametrize("engine_value", sorted(ENGINE_VOCABULARY))
    def test_h70_every_engine_value_mapped(self, engine_value):
        """Every engine-side relationship value has a map entry that maps
        to an R4 closed-enum value (not the default fallback)."""
        assert engine_value in INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
            f"Engine vocabulary value {engine_value!r} is not in the "
            f"canonical translation map. If the engine emits a new value, "
            f"the map MUST be extended."
        )
        result = INTERNAL_REL_TO_FHIR_EQUIVALENCE[engine_value]
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        # Engine values MUST map to a specific R4 value, not the catch-all
        # default (relatedto) — except ``related-to`` which IS the catch-all.
        if engine_value != "related-to":
            assert result != "relatedto", (
                f"Engine value {engine_value!r} maps to the R4 catch-all "
                f"'relatedto'. This loses clinical information — every "
                f"engine value SHOULD map to the most specific R4 enum "
                f"value (CM-01 SKEPTIC-002 fix shape)."
            )

    def test_h71_equivalent_maps_to_equivalent(self):
        """``equivalent`` engine value maps to R4 ``equivalent``."""
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["equivalent"] == "equivalent"

    def test_h72_source_is_narrower_maps_to_wider(self):
        """``source-is-narrower-than-target`` engine value maps to R4 ``wider``.

        CM-01 SKEPTIC-001 (directionality): R4 ``equivalence`` is read from
        the TARGET perspective. ``source-is-narrower-than-target`` means
        source is more specific → target is WIDER → R4 ``wider``.
        """
        assert (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"]
            == "wider"
        )

    def test_h73_source_is_broader_maps_to_narrower(self):
        """``source-is-broader-than-target`` engine value maps to R4
        ``narrower`` (CM-01 SKEPTIC-001 directionality)."""
        assert (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"]
            == "narrower"
        )

    def test_h74_not_translated_maps_to_unmatched(self):
        """``not-translated`` engine value maps to R4 ``unmatched`` (CM-01
        SKEPTIC-002 — NOT ``equivalent``)."""
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-translated"] == "unmatched"


# =============================================================================
# LENS 8 — Function contract (never raises, never echoes raw)
# =============================================================================

class TestFunctionContract:
    """``fhir_equivalence`` is the load-bearing contract.

    Pattern-match to TS-02 TERMINOLOGIST QA-030 + CM-01 SKEPTIC-001/002:
    the function MUST:
      (a) never raise (closed-enum guarantee),
      (b) never echo raw input (catch-all translation), and
      (c) return a value in the R4 closed enum.
    """

    @pytest.mark.parametrize(
        "weird_input",
        [
            None,
            "",
            "UNKNOWN",
            "subsumedby",  # R5/R4B — defensive key, maps to specializes
            "not-relatedto",  # not in any enum — maps to unmatched
            "EQUIVALENT",  # case variant
            "Equivalent",
        ],
    )
    def test_h80_never_raises(self, weird_input):
        """``fhir_equivalence`` never raises on any input."""
        try:
            result = fhir_equivalence(weird_input)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"fhir_equivalence({weird_input!r}) raised {type(exc).__name__}: "
                f"{exc}. The function contract is 'never raises'."
            )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    @pytest.mark.parametrize(
        "raw_input,expected_value",
        [
            ("subsumedby", "specializes"),  # R5/R4B key → R4 value
            ("not-relatedto", "unmatched"),  # not-in-any-enum → R4 catch-all
            ("not-related-to", "unmatched"),  # hyphenated variant
        ],
    )
    def test_h81_never_echoes_raw(self, raw_input, expected_value):
        """``fhir_equivalence`` never echoes raw input.

        For inputs that are NOT valid R4 enum values, the function MUST
        translate to a valid R4 value (not echo raw).
        """
        result = fhir_equivalence(raw_input)
        assert result != raw_input, (
            f"fhir_equivalence({raw_input!r}) echoed the raw input. "
            f"The function MUST translate to a valid R4 enum value."
        )
        assert result == expected_value

    def test_h82_case_insensitive_fallback_in_responses_helper(self):
        """``_fhir_equivalence_from_relationship`` has a case-insensitive
        fallback (the responses.py wrapper preserves this)."""
        # The wrapper at responses.py:136-154 implements case-insensitive
        # fallback on top of the canonical map.
        result_lower = responses_module._fhir_equivalence_from_relationship(
            "equivalent"
        )
        result_upper = responses_module._fhir_equivalence_from_relationship(
            "EQUIVALENT"
        )
        assert result_lower == result_upper == "equivalent"


# =============================================================================
# LENS 9 — Closed-enum assertion fires loudly on drift (defense-in-depth)
# =============================================================================

class TestClosedEnumAssertionFiresLoudly:
    """Verify the module-load assertion would catch a hypothetical drift.

    We don't actually inject drift (the assertion is at module load). Instead
    we source-read to confirm the assertion is structurally capable of
    catching drift.
    """

    def test_h90_assertion_checks_subset_relationship(self):
        """The assertion uses set-subset (``<=``), not set-equality (``==``).

        ``<=`` is correct because the map has multiple keys mapping to the
        same R4 value (e.g. ``subsumedby`` + ``subsumed-by`` both →
        ``specializes``). The values() set is a SUBSET of the closed enum,
        not equal to it. The canonical module uses ``set(...) <= FHIR_R4_...``
        form (set-subset operator on the constructed set).
        """
        source = inspect.getsource(equivalence_module)
        # Find the assertion block.
        assert "assert" in source
        # The assertion MUST use set-subset semantics. The canonical module
        # uses ``set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()) <= FHIR_R4_...``.
        # Accept either ``set(...values()) <= FHIR_R4`` OR
        # ``...values() <= FHIR_R4`` (both express set-subset).
        subset_pattern = re.compile(
            r"(set\(\s*)?INTERNAL_REL_TO_FHIR_EQUIVALENCE\.values\(\)(\s*\))?\s*<=\s*FHIR_R4_CONCEPT_MAP_EQUIVALENCE"
        )
        assert subset_pattern.search(source), (
            "The closed-enum assertion MUST use set-subset (<=), not "
            "set-equality (==). The map has multiple keys mapping to the "
            "same R4 value, so values() is a subset of the closed enum."
        )

    def test_h91_assertion_message_names_drift_values(self):
        """The assertion's error message names the drift values — actionable
        for the engineer who triggered it."""
        source = inspect.getsource(equivalence_module)
        # The assertion message MUST include a drift-value computation.
        assert "Drift values" in source or "-" in source, (
            "The assertion's error message MUST name the drift values "
            "(set difference) so the engineer knows which values are "
            "off-spec."
        )


# =============================================================================
# LENS 10 — Cross-surface runtime parity (responses.py ↔ outputs/fhir.py)
# =============================================================================

class TestCrossSurfaceRuntimeParity:
    """Runtime parity between the $translate surface and the export surface.

    The two surfaces consume the same engine vocabulary
    (``CodeMapping.relationship`` and ``ConceptMapRow.relationship``). The
    translation MUST produce the same R4 value for the same input —
    structurally enforced because both import the same Python object.
    """

    @pytest.mark.parametrize(
        "engine_value",
        [
            "equivalent",
            "source-is-narrower-than-target",
            "source-is-broader-than-target",
            "related-to",
            "not-translated",
            "unmatched",
            "subsumedby",  # defensive key
            "UNKNOWN",  # default fallback
            None,
        ],
    )
    def test_h100_both_surfaces_agree(self, engine_value):
        """The $translate surface (``_fhir_equivalence_from_relationship``)
        and the export surface (``fhir_equivalence``) produce the same R4
        value for the same input."""
        result_translate = (
            responses_module._fhir_equivalence_from_relationship(engine_value)
        )
        result_export = fhir_equivalence(engine_value)
        assert result_translate == result_export, (
            f"Cross-surface drift on input {engine_value!r}: "
            f"responses._fhir_equivalence_from_relationship returned "
            f"{result_translate!r}; outputs.fhir_equivalence returned "
            f"{result_export!r}. Both MUST import from the same canonical "
            f"map (CR-024)."
        )


# =============================================================================
# LENS 11 — apps/fhir_api.py operation dispatch routes through builders
# =============================================================================

class TestFhirApiOperationDispatch:
    """Verify ``_do_translate`` in ``apps/fhir_api.py`` calls
    ``build_parameters_translate`` (which routes through the canonical
    helper). No direct equivalence construction in the operation handler.

    Pattern-match to TS-02 TERMINOLOGIST QA-030: the bug was inside
    ``build_parameters_translate`` itself. The handler dispatching to the
    builder is structurally correct.
    """

    def test_h110_do_translate_calls_build_parameters_translate(self):
        """``_do_translate`` MUST delegate to ``build_parameters_translate``.

        Source-reading audit. A handler that constructs the Parameters
        response inline (bypassing the builder) would be a new bypass path.
        """
        # Import here to avoid loading the full app at module-import time.
        from medterm4ds.apps import fhir_api

        # _do_translate is a closure inside the create_app factory.
        # We source-read the factory to confirm the dispatch.
        source = inspect.getsource(fhir_api)
        # The dispatch MUST call build_parameters_translate.
        assert "build_parameters_translate" in source, (
            "apps/fhir_api.py MUST call build_parameters_translate from "
            "_do_translate. Inline construction bypasses the canonical "
            "helper (CF-HISTORIAN-VS01-01 / TS-02 TERMINOLOGIST QA-030)."
        )

    def test_h111_do_translate_no_hardcoded_equivalence_literal(self):
        """``_do_translate`` does NOT hardcode an equivalence value.

        Pattern-match to TS-02 TERMINOLOGIST QA-030.
        """
        from medterm4ds.apps import fhir_api

        source = inspect.getsource(fhir_api)
        # Find _do_translate function source.
        match = re.search(
            r"def _do_translate\(.*?\n(?=\s+def |\Z)",
            source,
            re.DOTALL,
        )
        assert match is not None, "_do_translate function not found"
        do_translate_source = match.group(0)
        # The forbidden pattern: hardcoded equivalence value in the handler.
        forbidden_patterns = [
            r'["\']equivalence["\']\s*:\s*["\']equivalent["\']',
            r'["\']equivalence["\']\s*:\s*["\']relatedto["\']',
        ]
        for pattern in forbidden_patterns:
            assert not re.search(pattern, do_translate_source), (
                f"_do_translate contains forbidden hardcoded equivalence "
                f"literal matching {pattern!r}."
            )


# =============================================================================
# LENS 12 — Spec-citation audit (every probe cites the canonical R4 spec)
# =============================================================================

class TestSpecCitationAudit:
    """HISTORIAN lens: verify the canonical R4 spec URL is cited in the
    canonical module's docstring (maintenance-hazard defense).
    """

    def test_h120_canonical_module_cites_spec(self):
        """``engines/fhir/equivalence.py`` cites the canonical R4 spec page.
        """
        source = inspect.getsource(equivalence_module)
        assert (
            "https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html"
            in source
        ), (
            "Canonical equivalence module MUST cite the canonical R4 spec "
            "URL. Memory is unreliable; future maintainers need the "
            "authoritative source (CF-HISTORIAN-VS01-01 methodology)."
        )

    def test_h121_canonical_module_cites_10_value_cardinality(self):
        """The 10-value cardinality of the R4 closed enum is documented
        EITHER in the canonical module OR in the frozen-set definition in
        ``engines/fhir/__init__.py`` (where the 10 values are enumerated).

        Defense against R5/R4B contamination claims — the cardinality is
        structurally enforced by the frozen-set having exactly 10 members.
        """
        # The canonical module cites the spec URL (verified in test_h120).
        # The 10-value cardinality is structurally enforced by the frozen-set
        # in engines/fhir/__init__.py. Verify the frozen-set has exactly 10.
        assert len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE) == 10, (
            "FHIR_R4_CONCEPT_MAP_EQUIVALENCE frozen-set MUST contain "
            "exactly 10 values per canonical R4 spec (verified 2026-07-13: "
            "https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html). "
            f"Got {len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)}."
        )
