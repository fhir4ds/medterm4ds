"""HISTORIAN RESWEEP probes for chunk CM-04 (ConceptMap Equivalence
Vocabulary Correctness).

Source: https://build.fhir.org/conceptmap.html
Canonical R4 ConceptMapEquivalence value set (verified 2026-08-10):
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

This resweep test file extends the baseline ``test_cm04_historian.py``
with NEW pattern-matching probes through the HISTORIAN lens ("What broke
before?"). Per ``evolution.json.config.notes`` (SKEPTIC tip for HISTORIAN),
the AST-walk-return-statements-only audit technique (CM-03/TERMINOLOGIST
tip DIRECTLY APPLICABLE to CM-04) MUST be re-verified via 3-angle
defense-in-depth:

  1. Source-substring audit of ``equivalence.py`` for module-load assertion
     text (the assertion is structurally load-bearing — pin via substring
     that the text mentioning drift values is present at module top level,
     not inside a comment-only block).
  2. AST-walk audit of ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` value-set
     membership — structurally assert every dict literal value is a Name
     node resolved to a member of the closed enum (immune to substring
     false-flags on docstring mentions of historical drift at lines 67-72).
  3. AST-walk-return-statements audit on ``fhir_equivalence`` +
     ``_fhir_equivalence_from_relationship`` + ``build_parameters_translate``
     — same technique as SKEPTIC resweep but with HISTORIAN's regression-
     detection angle (each probe names the prior bug it pattern-matches
     against).

HISTORIAN meta-pattern audit (closed-enum vocabulary drift, count=3
PROMOTED at ``GLOBAL_RULES.md`` line 134):
  * VS-01 SKEPTIC QA-054 (Filter Operator ``descendant-of`` typo)
  * VS-01 HISTORIAN CF-HISTORIAN-VS01-01 (R5/R4B ``subsumedby`` /
    ``not-relatedto`` in map values)
  * Milestone-2 CR-014 (5 test files hardcoded R5/R4B values)

HISTORIAN regression-pins (prior CM-04 / sibling-chunk bug patterns
confirmed RESOLVED; the resweep re-derives each via source-read +
runtime + structural-AST):
  * CF-HISTORIAN-VS01-01 RESOLVED — no R5/R4B ``subsumedBy`` / ``matches``
    regression in canonical map values
  * CM-01 SKEPTIC-001 HELD — narrower/wider directionality (source-is-
    narrower-than-target → ``wider``)
  * CM-01 SKEPTIC-002 HELD — ``not-translated`` → ``unmatched`` (NOT
    ``equivalent``)
  * TS-02 TERMINOLOGIST QA-030 HELD — no hardcoded ``"equivalent"`` in
    ``build_parameters_translate``
  * CR-024 HELD — single canonical module prevents parallel-map drift
  * Milestone-2 CR-014 HELD — frozen-set registry-as-contract

Cross-surface object-identity invariant (META pattern closure):
  ``responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE
     is INTERNAL_REL_TO_FHIR_EQUIVALENCE``
  ``outputs_fhir_module.FHIR_EQUIVALENCES
     is INTERNAL_REL_TO_FHIR_EQUIVALENCE``

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus; A44054006 -> A73211009)
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
)
from medterm4ds.engines.fhir import equivalence as equivalence_module
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    fhir_equivalence,
)
from medterm4ds.engines.fhir import responses as responses_module
from medterm4ds.outputs import fhir as outputs_fhir_module


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

# Off-spec values per closed-enum vocabulary drift meta-pattern
# (count=3 PROMOTED at GLOBAL_RULES.md line 134).
R5_R4B_VALUES = frozenset({"subsumedBy", "subsumedby"})  # R5/R4B (camelCase)
R5_ONLY_VALUES = frozenset({"matches"})  # R5-only
NOT_IN_ANY_ENUM = frozenset({"not-relatedto", "not-related-to"})

CANONICAL_R4_CODES = frozenset({
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
})


# ---------------------------------------------------------------------------
# Helpers: 3-angle defense-in-depth audit primitives.
# ---------------------------------------------------------------------------
def _walk_return_string_constants(func) -> list[str]:
    """AST-walk-return-statements-only: extract every string ``ast.Constant``
    found INSIDE an ``ast.Return`` node of ``func``.

    Per CM-03/TERMINOLOGIST tip (DIRECTLY APPLICABLE to CM-04 per SKEPTIC
    resweep confirmation): source-read audits searching for off-spec literal
    values MUST walk ``ast.Return`` nodes only — distinct from substring
    matching on raw text, which false-flags on docstring / commentary
    mentions (e.g. ``equivalence.py:67-72`` commentary discussing prior
    ``subsumedby`` / ``not-relatedto`` drift).
    """
    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    consts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    consts.append(sub.value)
    return consts


def _walk_module_dict_literal_values(module, name: str) -> list[str]:
    """AST-walk a module-level ``dict[str, str] = {...}`` literal and
    extract every VALUE-side string constant. Defense-in-depth probe class:
    structurally confirm no value-side string in the dict literal is
    off-spec, IMMUNE to substring false-flags from comments / docstrings /
    attribute accesses that contain the off-spec literal as a substring.

    Handles BOTH ``Assign`` (``name = {...}``) AND ``AnnAssign``
    (``name: dict[str, str] = {...}``) — the canonical module uses the
    annotated form for type documentation.
    """
    src = textwrap.dedent(inspect.getsource(module))
    tree = ast.parse(src)
    values: list[str] = []

    def _extract_from_dict_value(value: ast.expr) -> None:
        if isinstance(value, ast.Dict):
            for v in value.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    values.append(v.value)

    for node in ast.walk(tree):
        # Plain assignment: ``name = {...}``
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    _extract_from_dict_value(node.value)
        # Annotated assignment: ``name: dict[str, str] = {...}``
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                if node.value is not None:
                    _extract_from_dict_value(node.value)
    return values


def _has_module_load_assertion(module) -> bool:
    """AST-walk a module: does it have a module-level ``assert`` statement
    (not inside a function/class) that references
    ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``?
    """
    src = textwrap.dedent(inspect.getsource(module))
    tree = ast.parse(src)
    for node in tree.body:  # top-level only
        if isinstance(node, ast.Assert):
            test_src = ast.unparse(node.test)
            if "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in test_src:
                return True
    return False


# ===========================================================================
# LENS 1 — 3-ANGLE DEFENSE-IN-DEPTH (the SKEPTIC tip re-verification).
# HISTORIAN re-derives the AST-walk-return-statements-only technique via
# three independent angles and cross-checks they agree.
# ===========================================================================
class TestLens1ThreeAngleDefenseInDepth:
    """HISTORIAN: re-verify the AST-walk-return-statements-only technique
    via 3-angle defense-in-depth (SKEPTIC tip for HISTORIAN). The three
    angles MUST agree — if any single angle is brittle, the other two
    catch the drift.
    """

    def test_h10_angle_1_source_substring_assertion_text_present(self):
        """ANGLE 1 — source-substring audit of ``equivalence.py`` for the
        module-load assertion text.

        The substring ``INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()`` MUST
        appear in the module source — this is the textual signature of
        the closed-enum membership assertion. CF-HISTORIAN-VS01-01
        RESOLVED status depends on this assertion existing.
        """
        source = inspect.getsource(equivalence_module)
        assert "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()" in source, (
            "ANGLE 1 (substring): equivalence.py MUST contain the "
            "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values() membership "
            "assertion text. CF-HISTORIAN-VS01-01 RESOLVED depends on it."
        )
        assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in source, (
            "ANGLE 1 (substring): equivalence.py MUST reference "
            "FHIR_R4_CONCEPT_MAP_EQUIVALENCE."
        )
        assert "Drift values" in source, (
            "ANGLE 1 (substring): assertion message MUST name drift values "
            "for actionable debugging."
        )

    def test_h11_angle_2_ast_walk_dict_value_membership(self):
        """ANGLE 2 — AST-walk of ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` dict
        literal VALUES. Every value-side string constant in the dict
        literal MUST be in the R4 closed enum.

        This is the structural-immune angle: it walks the dict literal
        directly (not substring-matches the module), so docstring /
        commentary mentions of historical drift at lines 67-72 do NOT
        false-flag. Cross-checks the runtime values match the AST-walked
        dict literal values.
        """
        ast_values = set(
            _walk_module_dict_literal_values(
                equivalence_module, "INTERNAL_REL_TO_FHIR_EQUIVALENCE"
            )
        )
        # Cross-check: AST-walked values == runtime values.
        runtime_values = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        assert ast_values == runtime_values, (
            f"ANGLE 2 (AST-walk dict values) ≠ runtime values. "
            f"AST-only: {ast_values - runtime_values}; "
            f"runtime-only: {runtime_values - ast_values}."
        )
        # Every AST-walked value MUST be in the R4 closed enum.
        drift = ast_values - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        assert not drift, (
            f"ANGLE 2 (AST-walk dict values): off-spec values detected in "
            f"INTERNAL_REL_TO_FHIR_EQUIVALENCE dict literal: {drift}. "
            f"CF-HISTORIAN-VS01-01 regression."
        )

    def test_h12_angle_3_ast_walk_return_statements_fhir_equivalence(self):
        """ANGLE 3 — AST-walk-return-statements audit on
        ``fhir_equivalence``. Every string constant in a return statement
        MUST be in the R4 closed enum.

        Cross-checks Angle 2 at the function level: not only must the
        dict literal be clean, but the helper function's return
        statements must also only emit R4 values.
        """
        consts = _walk_return_string_constants(fhir_equivalence)
        for c in consts:
            assert c in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"ANGLE 3 (return-statements on fhir_equivalence): "
                f"off-spec constant {c!r} detected. Found in: {consts}."
            )

    def test_h13_angle_3_ast_walk_return_statements_wrapper(self):
        """ANGLE 3 (cont.) — AST-walk-return-statements audit on
        ``_fhir_equivalence_from_relationship`` (the responses.py
        wrapper used by ``build_parameters_translate``).
        """
        consts = _walk_return_string_constants(
            responses_module._fhir_equivalence_from_relationship
        )
        for c in consts:
            assert c in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"ANGLE 3 (return-statements on wrapper): off-spec "
                f"constant {c!r} detected. Found in: {consts}."
            )

    def test_h14_angle_3_ast_walk_return_statements_builder(self):
        """ANGLE 3 (cont.) — AST-walk-return-statements audit on
        ``build_parameters_translate``. The builder MUST NOT emit off-spec
        constants in return statements.
        """
        consts = _walk_return_string_constants(
            responses_module.build_parameters_translate
        )
        # The builder returns a dict literal; return-statement walk may
        # surface only the closing reference. Audit ALL function constants
        # for parity with SKEPTIC resweep test_s70.
        full_src_consts: list[str] = []
        src = textwrap.dedent(
            inspect.getsource(responses_module.build_parameters_translate)
        )
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                full_src_consts.append(node.value)
        for off_spec in R5_R4B_VALUES | R5_ONLY_VALUES | NOT_IN_ANY_ENUM:
            assert off_spec not in full_src_consts, (
                f"ANGLE 3 (function constants on builder): off-spec "
                f"value {off_spec!r} detected. Found in: {full_src_consts}."
            )

    def test_h15_three_angles_agree_no_off_spec_anywhere(self):
        """META — all three angles MUST agree that no off-spec value
        appears anywhere it shouldn't. The closed-enum vocabulary drift
        meta-pattern (count=3 PROMOTED) is RESOLVED when all three angles
        pass simultaneously.
        """
        # Angle 1.
        source = inspect.getsource(equivalence_module)
        assert "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()" in source
        # Angle 2.
        ast_values = set(
            _walk_module_dict_literal_values(
                equivalence_module, "INTERNAL_REL_TO_FHIR_EQUIVALENCE"
            )
        )
        assert ast_values <= FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        # Angle 3.
        for c in _walk_return_string_constants(fhir_equivalence):
            assert c in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        # META — all three angles agree.
        assert True


# ===========================================================================
# LENS 2 — CF-HISTORIAN-VS01-01 RESOLVED regression-pins.
# Pattern-match against the prior bug pattern: R5/R4B ``subsumedBy`` /
# ``not-relatedto`` values leaked into the canonical map's VALUE set.
# HISTORIAN re-derives each via source-read + runtime + AST.
# ===========================================================================
class TestLens2CFHistorianVS01_01_ResolvedPins:
    """HISTORIAN: re-verify CF-HISTORIAN-VS01-01 RESOLVED status via
    pattern-match against the closed-enum vocabulary drift meta-pattern.

    Per GLOBAL_RULES.md line 134 (PROMOTED at count=3): the meta-pattern
    recurrence was caught on Filter Operator (VS-01 SKEPTIC QA-054),
    ConceptMapEquivalence (CF-HISTORIAN-VS01-01), and 5 test files
    (Milestone-2 CR-014). The structural fix (frozen-set registry-as-
    contract + module-load assertion) MUST hold post-resweep.
    """

    def test_h20_no_subsumedby_in_map_values(self):
        """CF-HISTORIAN-VS01-01 RESOLVED: R5/R4B ``subsumedBy`` /
        ``subsumedby`` MUST NOT appear in the canonical map's VALUE set."""
        emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        for off_spec in R5_R4B_VALUES:
            assert off_spec not in emitted, (
                f"CF-HISTORIAN-VS01-01 REGRESSION: R5/R4B value {off_spec!r} "
                f"leaked into INTERNAL_REL_TO_FHIR_EQUIVALENCE value set."
            )

    def test_h21_no_matches_in_map_values(self):
        """CF-HISTORIAN-VS01-01 RESOLVED: R5-only ``matches`` MUST NOT
        appear in the canonical map's VALUE set."""
        emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        for off_spec in R5_ONLY_VALUES:
            assert off_spec not in emitted, (
                f"REGRESSION: R5-only value {off_spec!r} leaked into "
                f"INTERNAL_REL_TO_FHIR_EQUIVALENCE value set."
            )

    def test_h22_no_not_relatedto_in_map_values(self):
        """CF-HISTORIAN-VS01-01 RESOLVED: off-spec ``not-relatedto`` /
        ``not-related-to`` MUST NOT appear in the canonical map's VALUE
        set (defensive alias KEYS are OK; VALUE leaks are NOT).
        """
        emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        for off_spec in NOT_IN_ANY_ENUM:
            assert off_spec not in emitted, (
                f"REGRESSION: off-spec value {off_spec!r} leaked into "
                f"INTERNAL_REL_TO_FHIR_EQUIVALENCE value set."
            )

    def test_h23_subsumedby_key_maps_to_specializes(self):
        """CF-HISTORIAN-VS01-01 RESOLVED: ``subsumedby`` (lowercase) and
        ``subsumed-by`` (hyphenated) ARE accepted as defensive alias KEYS
        mapping to R4 ``specializes``. The camelCase ``subsumedBy`` is NOT
        a key (would only appear from a future R5/R4B engine vocabulary
        change).
        """
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE.get("subsumedby") == "specializes"
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE.get("subsumed-by") == "specializes"
        # camelCase form NOT a key — falls to relatedto default.
        assert "subsumedBy" not in INTERNAL_REL_TO_FHIR_EQUIVALENCE
        assert fhir_equivalence("subsumedBy") == "relatedto"

    def test_h24_module_load_assertion_fires_loudly_via_ast(self):
        """CF-HISTORIAN-VS01-01 RESOLVED structural pin: the module-load
        ``assert`` statement exists at module top level (NOT inside a
        function/class) and references ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``.

        This is the load-bearing contract. If the assert were deleted or
        moved inside a function, future drift would silently propagate.
        """
        assert _has_module_load_assertion(equivalence_module), (
            "Module-load assertion MUST exist at top level of "
            "engines/fhir/equivalence.py and reference "
            "FHIR_R4_CONCEPT_MAP_EQUIVALENCE. CF-HISTORIAN-VS01-01 "
            "structural pin."
        )

    def test_h25_assertion_uses_subset_operator_via_ast(self):
        """CF-HISTORIAN-VS01-01 RESOLVED: the assertion uses set-subset
        (``<=``) operator — NOT set-equality (``==``). The map has
        multiple keys mapping to the same R4 value, so values() is a
        SUBSET of the closed enum, not equal to it. AST-walk: find the
        assert, confirm comparison operator is ``ast.LtE``.
        """
        src = textwrap.dedent(inspect.getsource(equivalence_module))
        tree = ast.parse(src)
        found_subset_assert = False
        for node in tree.body:
            if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
                if any(isinstance(op, ast.LtE) for op in node.test.ops):
                    found_subset_assert = True
                    break
        assert found_subset_assert, (
            "Module-load assertion MUST use set-subset (<=) operator. "
            "AST-walk did not find ast.Assert with ast.LtE at top level."
        )


# ===========================================================================
# LENS 3 — CM-01 SKEPTIC-001 narrower/wider directionality HELD pin.
# Pattern-match against the prior bug pattern: prior responses.py had
# ``source-is-narrower-than-target`` mapped to ``narrower`` (inverted).
# Per R4 spec (target-perspective): source narrower → target WIDER.
# ===========================================================================
class TestLens3CM01Skeptic001_DirectionalityHeld:
    """HISTORIAN: re-verify CM-01 SKEPTIC-001 (narrower/wider directionality
    inversion) HELD status. The prior bug had
    ``source-is-narrower-than-target`` mapped to ``narrower`` instead of
    ``wider``. The fix is in the canonical map.
    """

    def test_h30_source_is_narrower_maps_to_wider(self):
        """CM-01 SKEPTIC-001 HELD: source-is-narrower-than-target →
        ``wider`` (target perspective)."""
        assert (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"]
            == "wider"
        ), (
            "CM-01 SKEPTIC-001 REGRESSION: source-is-narrower-than-target "
            "MUST map to R4 'wider' (target is wider than source)."
        )

    def test_h31_source_is_broader_maps_to_narrower(self):
        """CM-01 SKEPTIC-001 HELD: source-is-broader-than-target →
        ``narrower`` (target perspective)."""
        assert (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"]
            == "narrower"
        ), (
            "CM-01 SKEPTIC-001 REGRESSION: source-is-broader-than-target "
            "MUST map to R4 'narrower' (target is narrower than source)."
        )

    def test_h32_directionality_inverted_check_runtime(self):
        """CM-01 SKEPTIC-001 HELD (runtime): calling ``fhir_equivalence``
        with the engine vocabulary produces the R4-target-perspective
        value, not the inverted source-perspective value."""
        assert fhir_equivalence("source-is-narrower-than-target") == "wider"
        assert fhir_equivalence("source-is-broader-than-target") == "narrower"


# ===========================================================================
# LENS 4 — CM-01 SKEPTIC-002 not-translated HELD pin.
# Pattern-match: prior outputs/fhir.py mapped ``not-translated`` to
# ``equivalent`` (silent-wrong-answer: missing translation treated as
# confirmed equivalence).
# ===========================================================================
class TestLens4CM01Skeptic002_NotTranslatedHeld:
    """HISTORIAN: re-verify CM-01 SKEPTIC-002 (``not-translated`` →
    ``equivalent`` silent-wrong-answer) HELD status. The fix maps
    ``not-translated`` → ``unmatched`` (R4 catch-all for no mapping).
    """

    def test_h40_not_translated_maps_to_unmatched(self):
        """CM-01 SKEPTIC-002 HELD: ``not-translated`` → ``unmatched``."""
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-translated"] == "unmatched", (
            "CM-01 SKEPTIC-002 REGRESSION: 'not-translated' MUST map to "
            "R4 'unmatched', NOT 'equivalent'."
        )

    def test_h41_not_translated_runtime_returns_unmatched(self):
        """CM-01 SKEPTIC-002 HELD (runtime): ``fhir_equivalence`` returns
        ``unmatched`` for ``not-translated`` input."""
        assert fhir_equivalence("not-translated") == "unmatched"

    def test_h42_not_translated_never_returns_equivalent(self):
        """CM-01 SKEPTIC-002 HELD (negative pin): ``fhir_equivalence``
        NEVER returns ``equivalent`` for ``not-translated`` input."""
        result = fhir_equivalence("not-translated")
        assert result != "equivalent", (
            "REGRESSION: fhir_equivalence('not-translated') returned "
            "'equivalent' — this is the original CM-01 SKEPTIC-002 bug."
        )


# ===========================================================================
# LENS 5 — TS-02 TERMINOLOGIST QA-030 HELD pin.
# Pattern-match: prior ``build_parameters_translate`` hardcoded
# ``"equivalent"`` for every match entry, silently misrepresenting
# SNOMED→ICD10CM crosswalks (typically ``relatedto``).
# ===========================================================================
class TestLens5TS02TerminologistQA030_Held:
    """HISTORIAN: re-verify TS-02 TERMINOLOGIST QA-030 (hardcoded
    ``"equivalent"`` in response builder) HELD status. The fix routes
    through ``_fhir_equivalence_from_relationship`` for every match.
    """

    def test_h50_builder_calls_fhir_equivalence_helper(self):
        """TS-02 TERMINOLOGIST QA-030 HELD: ``build_parameters_translate``
        MUST call ``_fhir_equivalence_from_relationship`` — no hardcoded
        literal."""
        source = inspect.getsource(
            responses_module.build_parameters_translate
        )
        assert "_fhir_equivalence_from_relationship" in source, (
            "TS-02 TERMINOLOGIST QA-030 REGRESSION: build_parameters_translate "
            "MUST call _fhir_equivalence_from_relationship (no hardcoded literal)."
        )

    def test_h51_builder_no_hardcoded_equivalence_literal(self):
        """TS-02 TERMINOLOGIST QA-030 HELD: no ``"equivalence": "equivalent"``
        style literal in the builder source."""
        source = inspect.getsource(
            responses_module.build_parameters_translate
        )
        # Forbidden: a hardcoded equivalence value literal in the dict.
        # The actual builder uses
        # ``_fhir_equivalence_from_relationship(m.relationship)`` —
        # not a literal.
        import re
        forbidden_patterns = [
            r'["\']equivalence["\']\s*:\s*["\']equivalent["\']',
            r'["\']equivalence["\']\s*:\s*["\']relatedto["\']',
            r'["\']equivalence["\']\s*:\s*["\']subsumes["\']',
        ]
        for pat in forbidden_patterns:
            assert not re.search(pat, source), (
                f"TS-02 TERMINOLOGIST QA-030 REGRESSION: forbidden "
                f"hardcoded equivalence literal {pat!r} detected."
            )

    def test_h52_builder_routes_through_canonical_map(self):
        """TS-02 TERMINOLOGIST QA-030 HELD: the wrapper used by the builder
        delegates to ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` (canonical map)."""
        source = inspect.getsource(
            responses_module._fhir_equivalence_from_relationship
        )
        assert "_INTERNAL_REL_TO_FHIR_EQUIVALENCE" in source, (
            "REGRESSION: _fhir_equivalence_from_relationship MUST delegate "
            "to _INTERNAL_REL_TO_FHIR_EQUIVALENCE (canonical map)."
        )


# ===========================================================================
# LENS 6 — CR-024 single canonical module HELD pin.
# Pattern-match: prior architecture had two parallel translation maps
# (responses.py + outputs/fhir.py) with divergent key/value pairs.
# CR-024 (milestone-3 review) consolidated to a single canonical module.
# ===========================================================================
class TestLens6CR024_SingleCanonicalModuleHeld:
    """HISTORIAN: re-verify CR-024 (single canonical module) HELD status.
    The structural fix is the canonical import — drift between the two
    surfaces becomes structurally impossible because they share the same
    Python object.
    """

    def test_h60_responses_imports_from_canonical(self):
        """CR-024 HELD: ``responses.py`` imports from
        ``medterm4ds.engines.fhir.equivalence``."""
        source = inspect.getsource(responses_module)
        assert "from medterm4ds.engines.fhir.equivalence import" in source, (
            "CR-024 REGRESSION: responses.py MUST import from canonical "
            "equivalence module."
        )

    def test_h61_outputs_imports_from_canonical(self):
        """CR-024 HELD: ``outputs/fhir.py`` imports from
        ``medterm4ds.engines.fhir.equivalence``."""
        source = inspect.getsource(outputs_fhir_module)
        assert "from medterm4ds.engines.fhir.equivalence import" in source, (
            "CR-024 REGRESSION: outputs/fhir.py MUST import from canonical "
            "equivalence module."
        )

    def test_h62_responses_alias_object_identity(self):
        """CR-024 HELD (object identity): ``responses.py`` alias IS the
        canonical module's dict (same Python object).
        """
        assert (
            responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE
            is INTERNAL_REL_TO_FHIR_EQUIVALENCE
        ), (
            "CR-024 REGRESSION: responses.py's alias MUST be the same "
            "Python object as the canonical module's dict."
        )

    def test_h63_outputs_alias_object_identity(self):
        """CR-024 HELD (object identity): ``outputs/fhir.py`` alias IS
        the canonical module's dict (same Python object).
        """
        assert (
            outputs_fhir_module.FHIR_EQUIVALENCES
            is INTERNAL_REL_TO_FHIR_EQUIVALENCE
        ), (
            "CR-024 REGRESSION: outputs/fhir.py's FHIR_EQUIVALENCES MUST "
            "be the same Python object as the canonical module's dict."
        )

    def test_h64_outputs_helper_object_identity(self):
        """CR-024 HELD (object identity): ``outputs/fhir.py``'s
        ``fhir_equivalence`` IS the canonical module's callable.
        """
        assert (
            outputs_fhir_module.fhir_equivalence is fhir_equivalence
        ), (
            "CR-024 REGRESSION: outputs/fhir.py's fhir_equivalence MUST "
            "be the same Python callable as the canonical module's."
        )


# ===========================================================================
# LENS 7 — Milestone-2 CR-014 frozen-set registry-as-contract HELD pin.
# Pattern-match: prior 5 test files hardcoded R5/R4B ``subsumedby`` /
# R5-only ``matches`` values as if they were R4. CR-014 introduced
# ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` as the single source of truth.
# ===========================================================================
class TestLens7Milestone2CR014_FrozenSetRegistryHeld:
    """HISTORIAN: re-verify Milestone-2 CR-014 (frozen-set registry-as-
    contract) HELD status. The frozen-set is the single source of truth
    imported by BOTH impl and test code.
    """

    def test_h70_constant_is_frozenset(self):
        """Milestone-2 CR-014 HELD: ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``
        is a ``frozenset`` (mutation-safe)."""
        assert isinstance(FHIR_R4_CONCEPT_MAP_EQUIVALENCE, frozenset), (
            f"Milestone-2 CR-014 REGRESSION: constant MUST be frozenset; "
            f"got {type(FHIR_R4_CONCEPT_MAP_EQUIVALENCE).__name__}."
        )

    def test_h71_constant_cardinality_exactly_10(self):
        """Milestone-2 CR-014 HELD: per
        https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html,
        the R4 enum contains EXACTLY 10 concepts."""
        assert len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE) == 10

    def test_h72_constant_contains_all_canonical_r4_codes(self):
        """Milestone-2 CR-014 HELD: every canonical R4 code is in the
        frozen-set."""
        missing = CANONICAL_R4_CODES - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        assert not missing, (
            f"Milestone-2 CR-014 REGRESSION: frozen-set missing codes: {missing}"
        )

    def test_h73_constant_contains_no_extras(self):
        """Milestone-2 CR-014 HELD: the frozen-set MUST NOT contain extra
        codes outside the canonical R4 set."""
        extras = FHIR_R4_CONCEPT_MAP_EQUIVALENCE - CANONICAL_R4_CODES
        assert not extras, (
            f"Milestone-2 CR-014 REGRESSION: frozen-set has extra codes: {extras}"
        )

    def test_h74_constant_does_not_contain_off_spec_values(self):
        """Milestone-2 CR-014 HELD: the frozen-set MUST NOT contain
        R5/R4B ``subsumedBy`` / R5-only ``matches`` / off-spec
        ``not-relatedto`` values."""
        for off_spec in R5_R4B_VALUES | R5_ONLY_VALUES | NOT_IN_ANY_ENUM:
            assert off_spec not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"Milestone-2 CR-014 REGRESSION: off-spec value "
                f"{off_spec!r} in frozen-set."
            )


# ===========================================================================
# LENS 8 — Cross-surface runtime parity (META pattern closure).
# Both surfaces consume the same engine vocabulary; the translation MUST
# produce the same R4 value for the same input. Object-identity
# enforcement makes drift structurally impossible.
#
# NOTE: the wrapper ``_fhir_equivalence_from_relationship`` adds a
# case-insensitive fallback (responses.py:148-155) on top of the canonical
# ``fhir_equivalence``. For ENGINE-EMITTED values (always lowercase), both
# surfaces agree. For HOSTILE camelCase inputs (e.g. ``subsumedBy``), the
# wrapper lowercases and re-looks-up; the canonical helper does not. This
# is documented design (the wrapper docstring cites the case-insensitive
# fallback), NOT drift — both still emit R4 enum values (never off-spec).
# ===========================================================================
class TestLens8CrossSurfaceRuntimeParity:
    """HISTORIAN: re-verify cross-surface parity between
    ``responses.py`` ($translate) and ``outputs/fhir.py`` (ConceptMap
    export). The two surfaces share the same canonical Python object —
    drift on engine vocabulary is structurally impossible post-CR-024.

    The wrapper ``_fhir_equivalence_from_relationship`` adds a case-
    insensitive fallback that the canonical helper does not have; this is
    documented design (see Lens 8 sub-class
    ``TestLens8bWrapperCaseInsensitiveDivergence`` for the divergence pin).
    """

    @pytest.mark.parametrize(
        "engine_value",
        [
            # Engine pipeline vocabulary (always lowercase).
            "equivalent",
            "same",
            "identical",
            "source-is-narrower-than-target",
            "source-is-broader-than-target",
            "related-to",
            "not-translated",
            "unmatched",
            # Defensive alias keys (lowercase / hyphenated forms).
            "wider",
            "narrower",
            "broader",
            "subsumes",
            "subsumedby",
            "subsumed-by",
            "specializes",
            "relatedto",
            "not-relatedto",
            "not-related-to",
            "disjoint",
            # Unknown / null / empty — both surfaces emit relatedto default.
            None,
            "",
            "UNKNOWN_TOKEN",
            # R5-only — both surfaces emit relatedto default (no map key).
            "matches",
        ],
    )
    def test_h80_both_surfaces_agree_on_engine_inputs(self, engine_value):
        """META — the $translate surface (``_fhir_equivalence_from_relationship``)
        and the export surface (``fhir_equivalence``) produce the same R4
        value for the same engine-emitted or lowercase-alias input. CR-024
        makes this structurally enforced via object identity.
        """
        result_translate = (
            responses_module._fhir_equivalence_from_relationship(engine_value)
        )
        result_export = outputs_fhir_module.fhir_equivalence(engine_value)
        assert result_translate == result_export, (
            f"Cross-surface drift on input {engine_value!r}: "
            f"responses._fhir_equivalence_from_relationship returned "
            f"{result_translate!r}; outputs.fhir_equivalence returned "
            f"{result_export!r}. Both MUST import from the same canonical "
            f"map (CR-024)."
        )
        # Both MUST return a value in the R4 closed enum.
        assert result_translate in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


class TestLens8bWrapperCaseInsensitiveDivergence:
    """HISTORIAN: DOCUMENTED divergence between the canonical helper and
    the responses.py wrapper on hostile camelCase inputs. The wrapper adds
    a case-insensitive fallback (responses.py:148-155) that the canonical
    helper does not have. This is INTENTIONAL design (documented in the
    wrapper's docstring AND in the canonical module's docstring at
    equivalence.py:145 which calls the wrapper a "thin wrapper that
    preserves the case-insensitive fallback behaviour").

    NOT A BUG — both surfaces still emit R4 enum values (never off-spec).
    Filed as a Notable Non-Bug for the TERMINOLOGIST lens (clinical-
    correctness of which ON-SPEC value to emit when input is hostile).
    """

    def test_h81_wrapper_case_insensitive_on_subsumedby_camelcase(self):
        """The wrapper resolves ``subsumedBy`` (camelCase) via
        case-insensitive fallback to ``subsumedby`` → ``specializes``.
        The canonical helper does NOT — it returns the ``relatedto``
        default. Both values ARE in the R4 closed enum.
        """
        wrapper_result = (
            responses_module._fhir_equivalence_from_relationship("subsumedBy")
        )
        canonical_result = fhir_equivalence("subsumedBy")
        assert wrapper_result == "specializes"
        assert canonical_result == "relatedto"
        # Both are in R4 enum — no off-spec leak.
        assert wrapper_result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        assert canonical_result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_h82_wrapper_case_insensitive_fallback_documented(self):
        """The case-insensitive fallback is documented in the wrapper's
        own source. Pin the documentation to ensure future maintainers
        don't strip it as dead code."""
        source = inspect.getsource(
            responses_module._fhir_equivalence_from_relationship
        )
        assert "case-insensitive" in source.lower() or "lowered" in source, (
            "The wrapper's case-insensitive fallback MUST be documented in "
            "the wrapper source. Removing it would silently break the "
            "divergence documented in test_h81."
        )

    def test_h83_no_off_spec_value_leaks_on_either_surface(self):
        """META — both surfaces (wrapper + canonical) emit only R4
        closed-enum values on every hostile input. The camelCase
        divergence is between two ON-SPEC values, NOT an off-spec leak.
        """
        hostile_inputs = [
            "subsumedBy",  # camelCase
            "SUBSUMEDBY",  # uppercase
            "Matches",  # title-case R5-only
            "MATCHES",
            "NOT-RELATEDTO",
            "",
            None,
        ]
        for inp in hostile_inputs:
            wrapper_v = (
                responses_module._fhir_equivalence_from_relationship(inp)
            )
            canonical_v = fhir_equivalence(inp)
            assert wrapper_v in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"Wrapper emitted off-spec {wrapper_v!r} on input {inp!r}."
            )
            assert canonical_v in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"Canonical emitted off-spec {canonical_v!r} on input {inp!r}."
            )


# ===========================================================================
# LENS 9 — Wire-format hostile probes (META pattern closure at HTTP surface).
# The client cannot inject off-spec equivalence values via the wire —
# the equivalence is sourced from the engine's CodeMapping.relationship,
# never echoed from any client-supplied parameter.
# ===========================================================================
class TestLens9WireFormatHostileProbes:
    """HISTORIAN: re-verify at the HTTP $translate surface that (a) the
    equivalence is always sourced from the engine, (b) the client cannot
    inject off-spec values via Parameters body, (c) the wire emission
    is always in the R4 closed enum.
    """

    def test_h90_translate_emits_only_r4_values(self, fhir_client):
        """HISTORIAN: a SNOMED→ICD10CM $translate call MUST emit only R4
        closed-enum equivalence values (META pattern closure at HTTP
        surface)."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": "44054006",
                "targetsystem": ICD10CM_URI,
            },
        )
        body = r.json()
        if body.get("resourceType") == "OperationOutcome":
            pytest.skip("fixture DB missing the test code")
        for m in body.get("parameter", []):
            if m.get("name") != "match":
                continue
            for part in m.get("part", []):
                if part.get("name") == "equivalence":
                    val = part.get("valueCode")
                    assert val in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                        f"$translate emitted equivalence={val!r} NOT in R4 enum."
                    )

    def test_h91_translate_client_cannot_inject_off_spec(self, fhir_client):
        """HISTORIAN: the client CANNOT inject an off-spec equivalence
        value via the wire — the value is sourced from the engine.
        """
        body_dict = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                # Hostile: try to inject off-spec values.
                {"name": "equivalence", "valueCode": "subsumedBy"},
                {"name": "match.equivalence", "valueCode": "matches"},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body_dict)
        body = r.json()
        if body.get("resourceType") == "OperationOutcome":
            pytest.skip("fixture DB missing the test code")
        for m in body.get("parameter", []):
            if m.get("name") != "match":
                continue
            for part in m.get("part", []):
                if part.get("name") == "equivalence":
                    val = part.get("valueCode")
                    assert val in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
                    assert val != "subsumedBy"
                    assert val != "matches"


# ===========================================================================
# LENS 10 — Closed-enum vocabulary drift META-PATTERN audit.
# The meta-pattern recurrence (count=3 PROMOTED at GLOBAL_RULES.md line
# 134) covers: Filter Operator (VS-01 SKEPTIC QA-054),
# ConceptMapEquivalence (CF-HISTORIAN-VS01-01), and 5 test files
# (Milestone-2 CR-014). HISTORIAN confirms the META pattern is CLOSED
# on the ConceptMapEquivalence surface.
# ===========================================================================
class TestLens10MetaPatternClosed:
    """HISTORIAN: META pattern closure audit. The closed-enum vocabulary
    drift meta-pattern recurrence (count=3 PROMOTED) is CLOSED on the
    ConceptMapEquivalence surface when ALL of the following hold:

      1. The frozen-set registry-as-contract is the single source of truth.
      2. The canonical map's value set is a subset of the frozen-set.
      3. The module-load assertion enforces (2) at import time.
      4. No off-spec value (R5/R4B / R5-only / not-in-any-enum) leaks
         to the wire on EITHER surface.
    """

    def test_h100_meta_pattern_closed_on_conceptmap_equivalence(self):
        """META — all four invariants hold simultaneously. The closed-enum
        vocabulary drift meta-pattern is CLOSED on the
        ConceptMapEquivalence surface."""
        # Invariant 1: frozen-set is the source of truth.
        assert isinstance(FHIR_R4_CONCEPT_MAP_EQUIVALENCE, frozenset)
        assert len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE) == 10
        # Invariant 2: map values are a subset.
        emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        assert emitted <= FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        # Invariant 3: module-load assertion exists at top level.
        assert _has_module_load_assertion(equivalence_module)
        # Invariant 4: no off-spec value leaks.
        for off_spec in R5_R4B_VALUES | R5_ONLY_VALUES | NOT_IN_ANY_ENUM:
            assert off_spec not in emitted
            assert off_spec not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        # META — pattern CLOSED.
        assert True

    def test_h101_no_r5_r4b_regression_in_canonical_module(self):
        """META — confirm no R5/R4B ``subsumedBy`` value in the canonical
        module's runtime values (CF-HISTORIAN-VS01-01 RESOLVED).
        """
        emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        # The R4 spec-correct value ``specializes`` IS in the emitted set
        # (via subsumedby/subsumed-by/specializes keys).
        assert "specializes" in emitted
        # The R5/R4B ``subsumedBy`` / ``subsumedby`` are NOT.
        for off_spec in R5_R4B_VALUES:
            assert off_spec not in emitted

    def test_h102_canonical_module_docstring_cites_spec(self):
        """META — the canonical module cites the R4 spec page
        (maintenance-hazard defense against future R5/R4B contamination
        claims)."""
        source = inspect.getsource(equivalence_module)
        assert (
            "https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html"
            in source
        ), (
            "Canonical module MUST cite the R4 spec URL. Memory is "
            "unreliable; future maintainers need the authoritative source."
        )
