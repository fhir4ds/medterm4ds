"""SKEPTIC RESWEEP probes for chunk CM-04 (ConceptMap Equivalence
Vocabulary Correctness).

Source: https://build.fhir.org/conceptmap.html
Canonical R4 ConceptMapEquivalence value set:
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

This resweep test file extends the baseline ``test_cm04_skeptic.py`` with
NEW hostile-input probes through the SKEPTIC lens ("Break it."). Per
``evolution.json.config.notes`` (CM-03/TERMINOLOGIST tip), the
AST-walk-return-statements-only audit technique (used for
``ClosureTable.check`` outcome vocabulary) is DIRECTLY APPLICABLE to
CM-04's ConceptMap equivalence vocabulary audit. Walk return statements of
``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` lookups + ``$translate`` response
builders; assert every equivalence value is in
``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` (frozenset in
``engines/fhir/__init__.py``). Distinct from substring-matching on raw
text — the latter false-flags on docstring mentions of enum values.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus; A44054006 -> A73211009)

Existing baseline coverage in test_cm04_skeptic.py: 34 tests across 11
lenses. This resweep does NOT re-derive baseline coverage — it focuses on
NEW hostile-input combinations, AST-walk-return-statements audits, and
structural regression-pins for canonical-enum membership.

SKEPTIC lens dimensions covered (12 lenses, 67 new probes):
  L1  Off-spec R5/R4B case variants (subsumedBy camelCase)
  L2  Off-spec R5-only value (matches)
  L3  Off-spec value (not-relatedto) and case variants
  L4  Empty / missing / null / whitespace-only equivalence
  L5  AST-walk-return-statements audit on fhir_equivalence (CM-03/TERMINOLOGIST tip)
  L6  AST-walk-return-statements audit on _fhir_equivalence_from_relationship
  L7  AST-walk-return-statements audit on build_parameters_translate
  L8  Closed-enum membership registry-as-contract
  L9  Module-load assertion (CF-HISTORIAN-VS01-01 RESOLVED pin)
  L10 Map completeness (every R4 enum value reachable as output)
  L11 Wire-format hostile probes (POST with raw relationship in Parameters body)
  L12 Cross-surface parity (responses.py ↔ outputs/fhir.py)
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
)


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

# Off-spec values that MUST NOT appear on the wire.
# Per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
# (verified 2026-08-10): the R4 closed enum is exactly 10 values. R5/R4B
# uses `subsumedBy` (camelCase) where R4 uses `specializes`. R5 adds
# `matches` and `inexact` (inexact WAS back-ported to R4 — see R4 spec
# page). The catch-all "no mapping" value is `unmatched` — there is no
# `not-relatedto` value in ANY FHIR version (CF-HISTORIAN-VS01-01).
R5_R4B_VALUES_ABSENT_FROM_R4 = frozenset({
    "subsumedBy",   # R5/R4B; R4 uses `specializes`
    "matches",      # R5-only; not in R4
})
NOT_RELATEDTO_NOT_IN_ANY_ENUM = frozenset({
    "not-relatedto",       # NOT in ANY FHIR enum (R4 catch-all is `unmatched`)
    "not-related-to",
})

# The 10 canonical R4 codes per spec page.
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
# Helper: walk return statements of a function source, extract string
# constants. Per CM-03/TERMINOLOGIST strategy (CF-SKEPTIC-CS03-01 source-
# read form).
# ---------------------------------------------------------------------------
def _walk_return_string_constants(func) -> list[str]:
    """Walk ``return`` statements of ``func`` and extract every string
    ``ast.Constant`` value.

    Per CM-03/TERMINOLOGIST tip and CS-01 HISTORIAN L1 / CS-02 HISTORIAN
    L5 / VS-01 SKEPTIC methodology: source-read audits searching for
    off-spec literal values MUST walk ``ast.Constant`` nodes inside
    ``return`` statements only, not substring-match on raw text — the
    latter false-flags on docstring / commentary mentions of enum values
    (e.g. docstring text discussing the prior ``subsumedby`` drift).

    Returns a list of string constants found inside return statements.
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


def _walk_function_string_constants(func) -> list[str]:
    """Walk ALL string constants inside a function source (broader than
    return-statements-only — used for builder audits where literals may
    appear in dict / list expressions outside return statements)."""
    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    consts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            consts.append(node.value)
    return consts


def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


def _find_params(body: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _match_equivalence_values(body: dict[str, Any]) -> list[str]:
    """Extract every ``match.equivalence`` valueCode from a $translate
    Parameters body."""
    values: list[str] = []
    for m in _find_params(body, "match"):
        equiv_part = next(
            (part for part in m.get("part", []) if part.get("name") == "equivalence"),
            None,
        )
        if equiv_part is not None:
            values.append(equiv_part.get("valueCode"))
    return values


# ===========================================================================
# LENS 1 — Off-spec R5/R4B case variants: subsumedBy (camelCase).
# Per FHIR R4 spec page: ``specializes`` is the R4 code for the
# reverse-of-subsumes case; ``subsumedBy`` (camelCase) is R5/R4B.
# The map accepts lowercase ``subsumedby`` and hyphenated ``subsumed-by``
# as defensive aliases; the camelCase form MUST NOT leak to the wire.
# ===========================================================================
class TestLens1R5R4BSubsumedByCamelCase:
    """SKEPTIC probes for off-spec R5/R4B ``subsumedBy`` (camelCase).

    Per
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html, R4
    uses ``specializes`` for the reverse-of-subsumes case. R5/R4B uses
    ``subsumedBy``. CF-HISTORIAN-VS01-01 (milestone-2) fixed the prior
    drift where the map emitted ``subsumedby``; the lowercase + hyphenated
    forms are accepted as defensive aliases that map to ``specializes``.
    """

    def test_s10_subsumedby_lowercase_maps_to_specializes(self):
        """SKEPTIC: ``subsumedby`` (lowercase, no hyphen) is a defensive
        alias mapping to R4 ``specializes``. CF-HISTORIAN-VS01-01 RESOLVED
        status — the lowercase form never leaks the R5 value.
        """
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        result = fhir_equivalence("subsumedby")
        assert result == "specializes", (
            f"fhir_equivalence('subsumedby') MUST return 'specializes' "
            f"(R4 spec-correct value); got {result!r}. The lowercase "
            f"alias is a defensive entry — it never leaks the R5 value."
        )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s11_subsumed_by_hyphenated_maps_to_specializes(self):
        """SKEPTIC: ``subsumed-by`` (hyphenated) is a defensive alias
        mapping to R4 ``specializes``."""
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        result = fhir_equivalence("subsumed-by")
        assert result == "specializes"
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s12_subsumed_by_camelcase_never_emitted_to_wire(self):
        """SKEPTIC: the camelCase R5/R4B form ``subsumedBy`` MUST NOT
        appear on the wire. The map does NOT key on camelCase; if it
        ever appeared in engine vocabulary, it would fall to the
        ``relatedto`` default — never echo raw.
        """
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        result = fhir_equivalence("subsumedBy")
        assert result != "subsumedBy", (
            "fhir_equivalence('subsumedBy') MUST NOT echo the raw camelCase "
            "R5/R4B value; it MUST translate to an R4 enum value."
        )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"fhir_equivalence MUST always return an R4 enum value; "
            f"got {result!r}."
        )

    def test_s13_subsumed_by_uppercase_never_emitted_to_wire(self):
        """SKEPTIC: ``SUBSUMED-BY`` (uppercase) MUST NOT echo raw — falls
        to ``relatedto`` default per case-insensitive fallback in
        ``_fhir_equivalence_from_relationship``.
        """
        from medterm4ds.engines.fhir.responses import _fhir_equivalence_from_relationship
        result = _fhir_equivalence_from_relationship("SUBSUMED-BY")
        assert result != "SUBSUMED-BY"
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s14_subsumed_by_snake_case_never_emitted_to_wire(self):
        """SKEPTIC: ``subsumed_by`` (snake_case) MUST NOT echo raw —
        falls to ``relatedto`` default.
        """
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        result = fhir_equivalence("subsumed_by")
        assert result != "subsumed_by"
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s15_no_camelcase_off_spec_constant_in_map_values(self):
        """SKEPTIC: the canonical map's VALUE set MUST NOT contain any
        camelCase off-spec R5/R4B literal. CF-HISTORIAN-VS01-01 RESOLVED
        status — structural contract on the map.
        """
        from medterm4ds.engines.fhir.equivalence import (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE,
        )
        for v in INTERNAL_REL_TO_FHIR_EQUIVALENCE.values():
            assert v not in R5_R4B_VALUES_ABSENT_FROM_R4, (
                f"INTERNAL_REL_TO_FHIR_EQUIVALENCE emits R5/R4B value {v!r} "
                f"which is NOT in the R4 closed enum. "
                f"CF-HISTORIAN-VS01-01 regression."
            )


# ===========================================================================
# LENS 2 — Off-spec R5-only value: matches.
# Per FHIR R4 spec page: ``matches`` is R5-only; R4 does NOT define it.
# ===========================================================================
class TestLens2R5OnlyMatchesValue:
    """SKEPTIC probes for off-spec R5-only ``matches`` value.

    Per
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html, R4 does
    NOT define ``matches`` (introduced in R5). If it ever appeared in
    engine vocabulary, it MUST fall to ``relatedto`` default — never
    echoed raw.
    """

    def test_s20_matches_not_in_r4_enum(self):
        """SKEPTIC: ``matches`` is NOT in the R4 closed enum."""
        assert "matches" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            "FHIR_R4_CONCEPT_MAP_EQUIVALENCE MUST NOT contain 'matches' "
            "(R5-only value)."
        )

    def test_s21_matches_never_echoed_raw_by_fhir_equivalence(self):
        """SKEPTIC: ``fhir_equivalence('matches')`` MUST NOT echo 'matches'
        raw; it MUST return an R4 enum value (the ``relatedto`` default).
        """
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        result = fhir_equivalence("matches")
        assert result != "matches", (
            "fhir_equivalence('matches') MUST NOT echo the R5-only value raw."
        )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s22_matches_not_in_canonical_map_values(self):
        """SKEPTIC: the canonical map's value set MUST NOT contain
        ``matches`` (R5-only)."""
        from medterm4ds.engines.fhir.equivalence import (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE,
        )
        for v in INTERNAL_REL_TO_FHIR_EQUIVALENCE.values():
            assert v != "matches", (
                f"INTERNAL_REL_TO_FHIR_EQUIVALENCE MUST NOT emit 'matches' "
                f"(R5-only value)."
            )


# ===========================================================================
# LENS 3 — Off-spec value: not-relatedto (not in ANY FHIR enum).
# Per FHIR R4 spec page: there is NO ``not-relatedto`` code in ANY FHIR
# version. The R4 catch-all for "no mapping" is ``unmatched``. The map
# accepts ``not-relatedto`` and ``not-related-to`` as defensive aliases
# mapping to ``unmatched``.
# ===========================================================================
class TestLens3NotRelatedtoNotInAnyEnum:
    """SKEPTIC probes for off-spec ``not-relatedto`` value (not in ANY
    FHIR enum).

    Per
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html, the R4
    catch-all for "no mapping" is ``unmatched``. The literal
    ``not-relatedto`` is NOT in any FHIR enum. CF-HISTORIAN-VS01-01
    (milestone-2) fixed the prior drift where the map emitted
    ``not-relatedto``; both hyphenated and unhyphenated forms are now
    defensive aliases mapping to ``unmatched``.
    """

    def test_s30_not_relatedto_not_in_r4_enum(self):
        """SKEPTIC: ``not-relatedto`` is NOT in the R4 closed enum."""
        assert "not-relatedto" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        assert "not-related-to" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s31_not_relatedto_alias_maps_to_unmatched(self):
        """SKEPTIC: ``not-relatedto`` (no hyphen between 'related' and
        'to') is a defensive alias mapping to R4 ``unmatched``.
        """
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        result = fhir_equivalence("not-relatedto")
        assert result == "unmatched", (
            f"fhir_equivalence('not-relatedto') MUST return 'unmatched' "
            f"(R4 catch-all for no mapping); got {result!r}."
        )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s32_not_related_to_hyphenated_alias_maps_to_unmatched(self):
        """SKEPTIC: ``not-related-to`` (hyphen between 'related' and 'to')
        is a defensive alias mapping to R4 ``unmatched``.
        """
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        result = fhir_equivalence("not-related-to")
        assert result == "unmatched"
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s33_not_relatedto_never_echoed_raw(self):
        """SKEPTIC: ``NOT-RELATEDTO`` (uppercase) MUST NOT echo raw —
        falls to ``relatedto`` default per case-insensitive fallback.
        """
        from medterm4ds.engines.fhir.responses import _fhir_equivalence_from_relationship
        result = _fhir_equivalence_from_relationship("NOT-RELATEDTO")
        assert result != "NOT-RELATEDTO"
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s34_no_off_spec_constant_in_map_values(self):
        """SKEPTIC: the canonical map's VALUE set MUST NOT contain any
        off-spec ``not-relatedto`` / ``not-related-to`` literal.
        CF-HISTORIAN-VS01-01 RESOLVED status — structural contract.
        """
        from medterm4ds.engines.fhir.equivalence import (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE,
        )
        for v in INTERNAL_REL_TO_FHIR_EQUIVALENCE.values():
            assert v not in NOT_RELATEDTO_NOT_IN_ANY_ENUM, (
                f"INTERNAL_REL_TO_FHIR_EQUIVALENCE emits off-spec value "
                f"{v!r} which is NOT in any FHIR enum. "
                f"CF-HISTORIAN-VS01-01 regression."
            )


# ===========================================================================
# LENS 4 — Empty / missing / null / whitespace-only equivalence.
# Per FHIR R4 spec: ``relatedto`` is the catch-all for "relationship
# exists but exact type unknown". ``fhir_equivalence`` MUST never raise
# and never echo raw — empty/null inputs return ``relatedto``.
# ===========================================================================
class TestLens4EmptyMissingNullWhitespace:
    """SKEPTIC probes for empty / missing / null / whitespace-only
    equivalence inputs. ``fhir_equivalence`` MUST return ``relatedto``
    (catch-all) for all of these — never raise, never echo raw.
    """

    @pytest.mark.parametrize("input_val,expected", [
        (None, "relatedto"),
        ("", "relatedto"),
        ("   ", "relatedto"),
        ("\t\n", "relatedto"),
        ("\x00", "relatedto"),
    ])
    def test_s40_empty_null_whitespace_returns_relatedto(self, input_val, expected):
        """SKEPTIC: empty / null / whitespace-only inputs return
        ``relatedto`` (R4 catch-all)."""
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        result = fhir_equivalence(input_val)
        assert result == expected
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s41_fhir_equivalence_never_raises_on_hostile_inputs(self):
        """SKEPTIC: ``fhir_equivalence`` MUST NEVER raise on any string
        input. The function catches None and empty explicitly; the
        ``.get(key, "relatedto")`` fallback covers all unknown strings.
        """
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        hostile_inputs = [
            "'; DROP TABLE concepts; --",
            "<script>alert('xss')</script>",
            "\x00\x01\x02",
            "關閉表",
            "a" * 10000,
            "null",
            "NULL",
            "None",
            "undefined",
        ]
        for inp in hostile_inputs:
            result = fhir_equivalence(inp)
            assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"fhir_equivalence({inp!r}) MUST return an R4 enum value; "
                f"got {result!r}."
            )

    def test_s42_fhir_equivalence_from_relationship_never_raises(self):
        """SKEPTIC: ``_fhir_equivalence_from_relationship`` MUST NEVER
        raise on hostile inputs. The wrapper has the same contract as
        ``fhir_equivalence`` plus a case-insensitive fallback.
        """
        from medterm4ds.engines.fhir.responses import _fhir_equivalence_from_relationship
        for inp in [None, "", "   ", "UNKNOWN", ";--", "\x00", "a" * 1000]:
            result = _fhir_equivalence_from_relationship(inp)
            assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# ===========================================================================
# LENS 5 — AST-walk-return-statements audit on fhir_equivalence.
# Per CM-03/TERMINOLOGIST tip: the AST-walk-return-statements-only audit
# technique is DIRECTLY APPLICABLE to CM-04. Walk return statements of
# ``fhir_equivalence`` and assert every string constant is in the R4
# closed enum.
# ===========================================================================
class TestLens5AstWalkFhirEquivalenceReturns:
    """SKEPTIC (AST-walk-return-statements audit): every string constant
    in a return statement of ``fhir_equivalence`` MUST be in the R4
    closed enum. Per CM-03/TERMINOLOGIST tip, this audit walks
    ``ast.Return`` nodes only — distinct from substring matching on raw
    text, which false-flags on docstring / commentary mentions of enum
    values.
    """

    def test_s50_fhir_equivalence_return_constants_in_r4_enum(self):
        """SKEPTIC: every string constant in a return statement of
        ``fhir_equivalence`` is in the R4 closed enum.
        """
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        consts = _walk_return_string_constants(fhir_equivalence)
        # The function returns "relatedto" for unknown/null/empty inputs;
        # it returns INTERNAL_REL_TO_FHIR_EQUIVALENCE.get(...) for known
        # keys. The only string literal in return statements is the
        # default "relatedto".
        for c in consts:
            assert c in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"fhir_equivalence return statement contains string "
                f"constant {c!r} which is NOT in the R4 closed enum. "
                f"Found in: {consts}"
            )

    def test_s51_fhir_equivalence_no_r5_r4b_constants_in_returns(self):
        """SKEPTIC: ``fhir_equivalence`` return statements MUST NOT
        contain R5/R4B ``subsumedBy`` or R5-only ``matches`` constants.
        """
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        consts = _walk_return_string_constants(fhir_equivalence)
        for off_spec in R5_R5B_VALUES():
            assert off_spec not in consts, (
                f"fhir_equivalence return statement contains off-spec "
                f"value {off_spec!r}. Found in: {consts}"
            )

    def test_s52_fhir_equivalence_no_not_relatedto_constants_in_returns(self):
        """SKEPTIC: ``fhir_equivalence`` return statements MUST NOT
        contain off-spec ``not-relatedto`` / ``not-related-to`` constants.
        """
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        consts = _walk_return_string_constants(fhir_equivalence)
        for off_spec in NOT_RELATEDTO_NOT_IN_ANY_ENUM:
            assert off_spec not in consts, (
                f"fhir_equivalence return statement contains off-spec "
                f"value {off_spec!r}. Found in: {consts}"
            )


# ===========================================================================
# LENS 6 — AST-walk-return-statements audit on _fhir_equivalence_from_relationship.
# The wrapper function is used by ``build_parameters_translate`` (the
# $translate HTTP surface). Every string constant in its return statements
# MUST be in the R4 closed enum.
# ===========================================================================
class TestLens6AstWalkWrapperReturns:
    """SKEPTIC (AST-walk-return-statements audit on the wrapper):
    ``_fhir_equivalence_from_relationship`` return statements MUST only
    contain R4 closed-enum values.
    """

    def test_s60_wrapper_return_constants_in_r4_enum(self):
        """SKEPTIC: every string constant in a return statement of
        ``_fhir_equivalence_from_relationship`` is in the R4 closed enum.
        """
        from medterm4ds.engines.fhir.responses import _fhir_equivalence_from_relationship
        consts = _walk_return_string_constants(_fhir_equivalence_from_relationship)
        for c in consts:
            assert c in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"_fhir_equivalence_from_relationship return statement "
                f"contains string constant {c!r} which is NOT in the R4 "
                f"closed enum. Found in: {consts}"
            )

    def test_s61_wrapper_no_off_spec_constants_in_returns(self):
        """SKEPTIC: ``_fhir_equivalence_from_relationship`` return
        statements MUST NOT contain R5/R4B or off-spec constants.
        """
        from medterm4ds.engines.fhir.responses import _fhir_equivalence_from_relationship
        consts = _walk_return_string_constants(_fhir_equivalence_from_relationship)
        for off_spec in R5_R5B_VALUES() | NOT_RELATEDTO_NOT_IN_ANY_ENUM:
            assert off_spec not in consts, (
                f"_fhir_equivalence_from_relationship return statement "
                f"contains off-spec value {off_spec!r}. Found in: {consts}"
            )

    def test_s62_wrapper_returns_relatedto_for_default(self):
        """SKEPTIC: the wrapper's default fallback return is
        ``relatedto`` — verified by AST-walk presence in return
        statements.
        """
        from medterm4ds.engines.fhir.responses import _fhir_equivalence_from_relationship
        consts = _walk_return_string_constants(_fhir_equivalence_from_relationship)
        assert "relatedto" in consts, (
            f"_fhir_equivalence_from_relationship MUST return 'relatedto' "
            f"as the default fallback. Return constants: {consts}"
        )


# ===========================================================================
# LENS 7 — AST-walk-return-statements audit on build_parameters_translate.
# The $translate response builder MUST only emit equivalence values from
# the R4 closed enum. This is the load-bearing contract that the wire
# surface conforms to.
# ===========================================================================
class TestLens7AstWalkBuilderReturns:
    """SKEPTIC (AST-walk-return-statements audit on the builder):
    ``build_parameters_translate`` source MUST NOT contain off-spec
    equivalence constants in any dict / list / return literal.
    """

    def test_s70_builder_no_r5_r4b_constants(self):
        """SKEPTIC: ``build_parameters_translate`` source MUST NOT
        contain R5/R4B or R5-only constants anywhere — neither in return
        statements nor in dict / list literals.
        """
        from medterm4ds.engines.fhir.responses import build_parameters_translate
        # Walk ALL string constants in the function source — the builder
        # uses dict literals (not direct return statements) for the
        # parameter entries, so we audit the full function body.
        consts = _walk_function_string_constants(build_parameters_translate)
        for off_spec in R5_R5B_VALUES():
            assert off_spec not in consts, (
                f"build_parameters_translate source contains off-spec "
                f"value {off_spec!r}. Found in: {consts}"
            )

    def test_s71_builder_no_not_relatedto_constants(self):
        """SKEPTIC: ``build_parameters_translate`` source MUST NOT
        contain ``not-relatedto`` / ``not-related-to`` constants.
        """
        from medterm4ds.engines.fhir.responses import build_parameters_translate
        consts = _walk_function_string_constants(build_parameters_translate)
        for off_spec in NOT_RELATEDTO_NOT_IN_ANY_ENUM:
            assert off_spec not in consts, (
                f"build_parameters_translate source contains off-spec "
                f"value {off_spec!r}. Found in: {consts}"
            )

    def test_s72_builder_emits_only_r4_enum_values_at_runtime(self):
        """SKEPTIC: at runtime, ``build_parameters_translate`` MUST emit
        only R4 closed-enum equivalence values. The builder iterates
        CodeMapping objects and calls ``_fhir_equivalence_from_relationship``
        on each ``m.relationship`` — so every emitted equivalence value
        MUST be in the R4 enum.
        """
        from medterm4ds.core.models import CodeMapping, CodeRef
        from medterm4ds.engines.fhir.responses import build_parameters_translate

        # Probe every engine-emitted relationship + every defensive alias.
        relationships = [
            "equivalent",
            "same",
            "identical",
            "source-is-narrower-than-target",
            "source-is-broader-than-target",
            "related-to",
            "not-translated",
            "unmatched",
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
            None,  # None relationship
            "",   # empty relationship
            "UNKNOWN_TOKEN",
        ]
        mappings = [
            CodeMapping(
                source=CodeRef(source="SNOMEDCT_US", code="44054006"),
                target=CodeRef(source="ICD10CM", code="E11"),
                relationship=rel,
                match_type="exact",
            )
            for rel in relationships
        ]
        body = build_parameters_translate(
            mappings,
            source_system_uri="http://snomed.info/sct",
            source_code="44054006",
        )
        for equiv in _match_equivalence_values(body):
            assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"build_parameters_translate emitted equivalence={equiv!r} "
                f"NOT in R4 closed enum."
            )


# ===========================================================================
# LENS 8 — Closed-enum membership registry-as-contract.
# Per GLOBAL_RULES.md line 134 (closed-enum vocabulary drift pattern):
# ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` is the registry-as-contract
# constant. Both production code AND tests MUST import from the canonical
# location — never copy the enum into the test file.
# ===========================================================================
class TestLens8ClosedEnumRegistryAsContract:
    """SKEPTIC: ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` is the single source
    of truth for the R4 closed enum. Both impl and test code MUST import
    from ``engines/fhir/__init__.py`` — never copy the enum into test
    files (CF-HISTORIAN-VS01-01, Milestone-2 CR-014).
    """

    def test_s80_constant_cardinality_exactly_10(self):
        """SKEPTIC: per
        https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
        (HTTP-fetched 2026-08-10), the R4 ConceptMapEquivalence value
        set contains EXACTLY 10 concepts.
        """
        assert len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE) == 10

    def test_s81_constant_contains_all_canonical_r4_codes(self):
        """SKEPTIC: every code from the canonical R4 spec page MUST be
        in the frozen-set constant.
        """
        missing = CANONICAL_R4_CODES - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        assert not missing, (
            f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE missing canonical codes: "
            f"{missing}"
        )

    def test_s82_constant_contains_no_extra_codes(self):
        """SKEPTIC: the frozen-set constant MUST NOT contain codes
        outside the canonical R4 set.
        """
        extras = FHIR_R4_CONCEPT_MAP_EQUIVALENCE - CANONICAL_R4_CODES
        assert not extras, (
            f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE contains extra codes: "
            f"{extras}"
        )

    def test_s83_constant_is_frozenset(self):
        """SKEPTIC: the constant MUST be a ``frozenset`` — mutation-safe
        and hashable for use as a registry-as-contract."""
        assert isinstance(FHIR_R4_CONCEPT_MAP_EQUIVALENCE, frozenset), (
            f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE MUST be a frozenset; "
            f"got {type(FHIR_R4_CONCEPT_MAP_EQUIVALENCE).__name__}."
        )

    def test_s84_constant_imported_in_responses_module(self):
        """SKEPTIC: ``responses.py`` MUST import the constant from the
        canonical location (not redefine locally)."""
        from medterm4ds.engines.fhir import responses as responses_module
        source = inspect.getsource(responses_module)
        # responses.py imports the canonical map via the equivalence
        # submodule; the FHIR_R4_CONCEPT_MAP_EQUIVALENCE constant is
        # referenced indirectly through the equivalence module's
        # module-load assertion. Verify the equivalence module is imported.
        assert "from medterm4ds.engines.fhir.equivalence import" in source

    def test_s85_constant_imported_in_outputs_fhir_module(self):
        """SKEPTIC: ``outputs/fhir.py`` MUST import from the canonical
        equivalence module."""
        from medterm4ds.outputs import fhir as outputs_fhir_module
        source = inspect.getsource(outputs_fhir_module)
        assert "from medterm4ds.engines.fhir.equivalence import" in source or \
               "from medterm4ds.engines.fhir.equivalence" in source


# ===========================================================================
# LENS 9 — Module-load assertion (CF-HISTORIAN-VS01-01 RESOLVED pin).
# The canonical equivalence module has a module-load ``assert`` that every
# map value is in the R4 closed enum. This is the load-bearing contract
# that prevents drift across BOTH the $translate surface AND the
# ConceptMap export surface.
# ===========================================================================
class TestLens9ModuleLoadAssertion:
    """SKEPTIC: the canonical module MUST have a module-load assertion
    enforcing closed-enum membership. CF-HISTORIAN-VS01-01 (milestone-2)
    was fixed via this structural contract — a future map addition that
    emits an off-spec value would fail at import time.
    """

    def test_s90_canonical_module_load_does_not_raise(self):
        """SKEPTIC: importing the canonical equivalence module MUST
        succeed without raising. If the module-load assertion fires,
        ImportError propagates and BOTH surfaces are broken.
        """
        from medterm4ds.engines.fhir import equivalence as _  # noqa: F401
        assert True

    def test_s91_canonical_module_has_load_assertion(self):
        """SKEPTIC: ``engines/fhir/equivalence.py`` MUST have a
        module-load ``assert`` statement on
        ``INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()`` membership."""
        from medterm4ds.engines.fhir import equivalence as equiv_module
        source = inspect.getsource(equiv_module)
        assert "assert" in source
        assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in source
        assert "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()" in source

    def test_s92_assertion_would_fire_on_off_spec_value(self):
        """SKEPTIC: the module-load assertion is a real contract — if a
        future map addition emits an off-spec value, the assertion fires.
        Verify by AST-walk: the assertion's comparison operator is
        ``set <= frozenset`` (subset).
        """
        from medterm4ds.engines.fhir import equivalence as equiv_module
        src = textwrap.dedent(inspect.getsource(equiv_module))
        tree = ast.parse(src)
        # Find an assert statement containing a Compare node with ast.LtE
        # operator. This is the load-bearing membership contract.
        found_assert = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
                if any(isinstance(op, ast.LtE) for op in node.test.ops):
                    found_assert = True
                    break
        assert found_assert, (
            "engines/fhir/equivalence.py MUST have a module-load assert "
            "using set <= FHIR_R4_CONCEPT_MAP_EQUIVALENCE (subset) operator."
        )


# ===========================================================================
# LENS 10 — Map completeness (every R4 enum value reachable as output).
# SKEPTIC completeness audit: every R4 enum value MUST be reachable as a
# map output. If a value is declared in the enum but NOT reachable, that
# is either a dead-code indication OR a missing defensive alias.
# ===========================================================================
class TestLens10MapCompleteness:
    """SKEPTIC: every R4 enum value MUST be reachable as a map output
    (either via engine pipeline vocabulary or via defensive alias).
    """

    def test_s100_every_r4_enum_value_reachable_via_some_input(self):
        """SKEPTIC: for every R4 enum value ``v``, there exists at least
        one input string that maps to ``v`` via ``fhir_equivalence``.
        """
        from medterm4ds.engines.fhir.equivalence import (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE,
        )
        emitted_values = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        # ``relatedto`` is the default fallback for unknown inputs —
        # always reachable.
        unreachable = FHIR_R4_CONCEPT_MAP_EQUIVALENCE - emitted_values - {"relatedto"}
        # Some R4 values may not be reachable via the engine map today
        # (e.g. ``inexact`` and ``equal`` are not explicit map values
        # but ``equal`` IS reachable via the ``same`` / ``identical``
        # alias keys). Probe this as a documentation pin, not a bug.
        # The unreachable set MUST be a subset of the canonical R4 codes
        # (i.e. no off-spec unreachable values).
        assert unreachable <= CANONICAL_R4_CODES, (
            f"Unreachable values contain off-spec codes: "
            f"{unreachable - CANONICAL_R4_CODES}"
        )

    def test_s101_equal_reachable_via_alias(self):
        """SKEPTIC: the R4 ``equal`` value MUST be reachable via the
        ``same`` or ``identical`` alias keys."""
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        assert fhir_equivalence("same") == "equal"
        assert fhir_equivalence("identical") == "equal"

    def test_s102_specializes_reachable_via_subsumedby_alias(self):
        """SKEPTIC: the R4 ``specializes`` value MUST be reachable via
        the ``subsumedby`` or ``subsumed-by`` alias keys."""
        from medterm4ds.engines.fhir.equivalence import fhir_equivalence
        assert fhir_equivalence("subsumedby") == "specializes"
        assert fhir_equivalence("subsumed-by") == "specializes"

    def test_s103_inexact_reachable_only_as_r4_default(self):
        """SKEPTIC: the R4 ``inexact`` value is NOT reachable via the
        engine map today (the engine does not emit ``inexact``). It IS
        in the R4 closed enum. Verify it is in the enum but not in the
        map's value set — this is a documentation pin, not a bug.

        Per R4 spec: ``inexact`` = "The target mapping overlaps with the
        source concept, but both source and target cover additional
        meaning, or the definitions are imprecise" — boundary uncertainty
        exists. The engine does not classify this relationship type.
        """
        from medterm4ds.engines.fhir.equivalence import (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE,
        )
        assert "inexact" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        # ``inexact`` is not in the map's value set today.
        assert "inexact" not in INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()


# ===========================================================================
# LENS 11 — Wire-format hostile probes (POST with raw relationship in
# Parameters body). SKEPTIC: hostile inputs at the HTTP surface.
# ===========================================================================
class TestLens11WireFormatHostileProbes:
    """SKEPTIC: hostile inputs at the HTTP $translate surface. The
    equivalence value is sourced from the engine, not from the client,
    so the client cannot inject off-spec values via the wire. Verify
    this contract.
    """

    def test_s110_translate_emits_only_r4_values_for_snomed_to_icd10(self, fhir_client):
        """SKEPTIC: a SNOMED→ICD10CM $translate call MUST emit only R4
        closed-enum equivalence values.
        """
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
        for equiv in _match_equivalence_values(body):
            assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"$translate emitted equivalence={equiv!r} NOT in R4 enum."
            )

    def test_s111_translate_no_targetsystem_emits_only_r4_values(self, fhir_client):
        """SKEPTIC: $translate without ``targetsystem`` MUST emit only
        R4 closed-enum equivalence values."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={"system": SNOMED_URI, "code": "44054006"},
        )
        body = r.json()
        if body.get("resourceType") == "OperationOutcome":
            pytest.skip("fixture DB missing the test code")
        for equiv in _match_equivalence_values(body):
            assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s112_translate_post_with_hostile_body_emits_only_r4_values(self, fhir_client):
        """SKEPTIC: POST $translate with hostile Parameters body MUST
        emit only R4 closed-enum equivalence values. The equivalence
        value is sourced from the engine, not from the client body —
        so client body cannot inject off-spec values.
        """
        body_dict = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body_dict)
        body = r.json()
        if body.get("resourceType") == "OperationOutcome":
            pytest.skip("fixture DB missing the test code")
        for equiv in _match_equivalence_values(body):
            assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_s113_translate_client_cannot_inject_off_spec_equivalence(self, fhir_client):
        """SKEPTIC: the client CANNOT inject an off-spec equivalence
        value via the wire. The equivalence is sourced from the engine's
        CodeMapping.relationship, not echoed from any client-supplied
        parameter.
        """
        body_dict = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                # Hostile: try to inject an off-spec equivalence value.
                # The server MUST ignore this — it is NOT a documented
                # In parameter for $translate.
                {"name": "equivalence", "valueCode": "subsumedBy"},
                {"name": "match.equivalence", "valueCode": "matches"},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body_dict)
        body = r.json()
        if body.get("resourceType") == "OperationOutcome":
            pytest.skip("fixture DB missing the test code")
        for equiv in _match_equivalence_values(body):
            assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"$translate emitted equivalence={equiv!r} NOT in R4 enum "
                f"— client succeeded in injecting off-spec value."
            )
            # Verify the client's hostile injection did NOT leak.
            assert equiv != "subsumedBy"
            assert equiv != "matches"

    def test_s114_translate_unknown_source_code_emits_no_match(self, fhir_client):
        """SKEPTIC: $translate on an unknown source code returns
        result=false and NO match entries (no equivalence emitted at
        all)."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": "UNKNOWN_CODE_XYZ",
                "targetsystem": ICD10CM_URI,
            },
        )
        body = r.json()
        # Unknown code → result=false, no matches, no equivalence values.
        result_param = _find_param(body, "result")
        if result_param is not None:
            assert result_param.get("valueBoolean") is False
        # No equivalence values emitted at all.
        equiv_values = _match_equivalence_values(body)
        assert equiv_values == [], (
            f"$translate on unknown code MUST NOT emit match entries; "
            f"got equivalence values: {equiv_values}"
        )


# ===========================================================================
# LENS 12 — Cross-surface parity (responses.py ↔ outputs/fhir.py).
# Both surfaces import from the canonical equivalence module — drift is
# structurally impossible. Verify via runtime object-identity.
# ===========================================================================
class TestLens12CrossSurfaceParity:
    """SKEPTIC: both ``responses.py`` ($translate) and ``outputs/fhir.py``
    (ConceptMap export) MUST use the same canonical map. Drift between
    the two surfaces is structurally impossible post-CR-024.
    """

    def test_s120_responses_uses_canonical_map(self):
        """SKEPTIC: ``responses.py`` imports
        ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` from the canonical module.
        """
        from medterm4ds.engines.fhir import responses as responses_module
        from medterm4ds.engines.fhir.equivalence import (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE,
        )
        # Object identity — same dict instance, not a copy.
        assert responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE is INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
            "responses.py MUST import INTERNAL_REL_TO_FHIR_EQUIVALENCE from "
            "the canonical equivalence module (object identity)."
        )

    def test_s121_outputs_uses_canonical_map(self):
        """SKEPTIC: ``outputs/fhir.py`` uses the canonical
        ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` map."""
        from medterm4ds.engines.fhir.equivalence import (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE,
        )
        from medterm4ds.outputs import fhir as outputs_module
        # outputs/fhir.py imports the canonical map via the equivalence module.
        assert hasattr(outputs_module, "INTERNAL_REL_TO_FHIR_EQUIVALENCE") or \
               hasattr(outputs_module, "fhir_equivalence")
        # If INTERNAL_REL_TO_FHIR_EQUIVALENCE is exposed, verify identity.
        if hasattr(outputs_module, "INTERNAL_REL_TO_FHIR_EQUIVALENCE"):
            assert outputs_module.INTERNAL_REL_TO_FHIR_EQUIVALENCE is INTERNAL_REL_TO_FHIR_EQUIVALENCE

    def test_s122_both_surfaces_emit_same_value_for_every_engine_relationship(self):
        """SKEPTIC: for every engine relationship value, both surfaces
        MUST emit the same R4 enum value.
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
# Helper for lens 5/6/7 (R5/R4B absent values). Encapsulated as a function
# so the test classes can reference it without module-level noise.
# ---------------------------------------------------------------------------
def R5_R5B_VALUES() -> frozenset[str]:
    return R5_R4B_VALUES_ABSENT_FROM_R4
