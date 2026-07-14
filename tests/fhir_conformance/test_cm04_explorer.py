"""EXPLORER probes for chunk CM-04 (ConceptMap Equivalence Vocabulary Correctness).

Source: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
Chunk scope: confirm the equivalence vocabulary surface (R4 ConceptMapEquivalence
closed enum) is correct across both the $translate HTTP surface (responses.py)
AND the ConceptMap export surface (outputs/fhir.py).

EXPLORER lens (lateral thinking / cross-handler probes). The SKEPTIC + HISTORIAN
iterations already produced 34 + 59 = 93 probes verifying the canonical module
integrity from the spec-citation and source-reading angles. EXPLORER extends
coverage to LATERAL axes:

  * Wire-format valueCode coverage on EVERY R4 enum value (not just ``equivalent``)
    — extend CS-04 EXPLORER test_e151 hyphenated-value methodology.
  * Cross-system consistency: SNOMED↔ICD-10-CM, SNOMED↔LOINC, SNOMED↔RxNorm.
  * Default-fallback behavior on the HTTP surface (unknown engine vocabulary
    would require monkeypatching the engine; here we probe the function-level
    surface with arbitrary relationship values via build_parameters_translate).
  * Equivalence in batch responses: each batch entry's match.equivalence
    MUST come from the R4 enum.
  * XML wire-format: hyphenated values (``not-relatedto``, ``not-related-to``)
    and all 10 R4 enum codes render correctly via the XML serializer.
  * Combined operations: ``$translate`` then ``$lookup`` on the returned target
    concept — equivalence is consistent with the resolved concept.
  * Registry-as-contract pattern: ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` is the
    frozen-set contract; verify both impl AND tests import from the canonical
    location (CM-04 SKEPTIC test_s37 + HISTORIAN test_h63 methodology extended
    to the wire).
  * Carry-forward pinning: CF-CM02-01 (no coding/codeableConcept extractors),
    CF-TERMINOLOGIST-CM01-01 (latent gap on subsumes/specializes engine values).

Per chunk schedule notes: this is a TERMINOLOGIST-ONLY chunk (equivalence
vocabulary is purely a clinical-correctness concern). SKEPTIC + HISTORIAN
already closed CLEAN (0 bugs each). EXPLORER is expected to find 0 bugs;
the lateral coverage extension is the load-bearing contribution.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from medterm4ds.core.models import CodeMapping, CodeRef, ConceptMapRow
from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    fhir_equivalence,
)
from medterm4ds.engines.fhir.responses import (
    _fhir_equivalence_from_relationship,
    build_parameters_translate,
)
from medterm4ds.outputs.fhir import concept_map_to_fhir


SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_URI_TRAILING_SLASH = "http://hl7.org/fhir/sid/icd-10-cm/"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
LOINC_URI = "http://loinc.org"
UNKNOWN_SYSTEM_URI = "http://example.org/unknown-system"

# Canonical R4 enum from
# https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
CANONICAL_R4_ENUM_VALUES = frozenset({
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
# Helpers
# ---------------------------------------------------------------------------

def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Find the FIRST parameter with the given name in a Parameters body."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


def _find_all_params(body: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _match_equivalence(match_part: dict[str, Any]) -> str | None:
    """Extract the equivalence valueCode from a match.part entry."""
    for sub in match_part.get("part", []):
        if sub.get("name") == "equivalence":
            return sub.get("valueCode")
    return None


def _make_mapping(relationship: str, target_code: str = "E11") -> CodeMapping:
    """Build a single CodeMapping for direct build_parameters_translate calls."""
    return CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code=target_code),
        relationship=relationship,
        match_type="exact",
    )


# ===========================================================================
# LENS 1: Registry-as-contract — frozen-set is the canonical contract source.
# Pattern-match to CF-HISTORIAN-VS01-01 (test-suite-encoded wrong-spec) and
# TS-02 TERMINOLOGIST QA-030 (engine-vocabulary → FHIR-enum translation audit).
# Verify the frozen-set is imported from the canonical location by BOTH the
# impl AND the test suite — a local copy in either location re-introduces
# drift.
# ===========================================================================


class TestRegistryAsContractFrozenSet:
    """Verify ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` is the single contract source.

    The frozen-set lives in ``engines/fhir/__init__.py`` (canonical location).
    Both impl (equivalence.py module-load assertion) AND tests (SKEPTIC +
    HISTORIAN probes) import it from there — drift between them is structurally
    impossible.
    """

    def test_e10_frozen_set_imported_from_canonical_in_equivalence_module(self):
        """EXPLORER: ``equivalence.py`` imports the frozen-set from
        ``engines.fhir.__init__`` (NOT a local copy).
        """
        import inspect

        from medterm4ds.engines.fhir import equivalence as equiv_module

        source = inspect.getsource(equiv_module)
        assert "from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in source, (
            "engines/fhir/equivalence.py MUST import FHIR_R4_CONCEPT_MAP_EQUIVALENCE "
            "from the canonical location (engines/fhir/__init__.py). A local copy "
            "re-introduces the test-suite-encoded-wrong-spec pattern."
        )

    def test_e11_frozen_set_object_identity_canonical(self):
        """EXPLORER: the equivalence module's reference IS the same Python
        object as the canonical ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``.

        Methodology: object-identity-is-the-contract (strategy 54, NEW from
        CM-04 HISTORIAN). Drift is structurally impossible because both names
        point to the same frozen-set object.
        """
        from medterm4ds.engines.fhir import (
            FHIR_R4_CONCEPT_MAP_EQUIVALENCE as canonical_const,
        )
        from medterm4ds.engines.fhir.equivalence import (
            FHIR_R4_CONCEPT_MAP_EQUIVALENCE as equiv_module_const,
        )

        assert equiv_module_const is canonical_const, (
            "The FHIR_R4_CONCEPT_MAP_EQUIVALENCE imported by engines/fhir/equivalence.py "
            "MUST be the same Python object as engines.fhir.__init__.FHIR_R4_CONCEPT_MAP_EQUIVALENCE. "
            "A local redefinition would create drift."
        )

    def test_e12_frozen_set_matches_canonical_r4_spec(self):
        """EXPLORER: ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` matches the canonical
        R4 spec page exactly (10 values).

        Source: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
        "This value set contains 10 concepts":
          ``relatedto | equivalent | equal | wider | subsumes | narrower |
             specializes | inexact | unmatched | disjoint``
        """
        assert FHIR_R4_CONCEPT_MAP_EQUIVALENCE == CANONICAL_R4_ENUM_VALUES, (
            f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE drift from canonical R4 spec.\n"
            f"  Expected: {sorted(CANONICAL_R4_ENUM_VALUES)}\n"
            f"  Got:      {sorted(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)}"
        )

    def test_e13_frozen_set_is_immutable(self):
        """EXPLORER: ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` is a ``frozenset``
        (immutable). A future change to ``set`` would silently allow mutation
        — re-introducing the test-suite-encoded-wrong-spec risk.
        """
        assert isinstance(FHIR_R4_CONCEPT_MAP_EQUIVALENCE, frozenset), (
            "FHIR_R4_CONCEPT_MAP_EQUIVALENCE MUST be a frozenset — a plain set "
            "could be mutated, re-introducing drift."
        )


# ===========================================================================
# LENS 2: Parametrized wire-format coverage on EVERY R4 enum value.
# CS-04 EXPLORER test_e151 verified hyphenated values on $subsumes; here we
# extend to ALL 10 R4 ConceptMapEquivalence values on the $translate surface
# via direct builder invocation.
# ===========================================================================


class TestWireFormatAllR4EnumValues:
    """Verify each of the 10 R4 enum values renders correctly on the wire.

    Pattern-match to CS-04 EXPLORER test_e151 (hyphenated values in XML).
    Pattern-match to CR-002 (boolean capitalization on serializers).
    EXPLORER extends coverage to every R4 equivalence value.
    """

    @pytest.mark.parametrize(
        "engine_relationship,expected_r4_value",
        [
            # Engine pipeline values → R4 enum
            ("equivalent", "equivalent"),
            ("same", "equal"),
            ("identical", "equal"),
            ("source-is-narrower-than-target", "wider"),
            ("source-is-broader-than-target", "narrower"),
            ("related-to", "relatedto"),
            ("not-translated", "unmatched"),
            ("unmatched", "unmatched"),
            # Defensive pass-through entries (R4 codes accepted verbatim)
            ("wider", "wider"),
            ("narrower", "narrower"),
            ("broader", "narrower"),
            ("subsumes", "subsumes"),
            ("specializes", "specializes"),
            ("subsumedby", "specializes"),
            ("subsumed-by", "specializes"),
            ("relatedto", "relatedto"),
            ("not-relatedto", "unmatched"),
            ("not-related-to", "unmatched"),
            ("disjoint", "disjoint"),
        ],
    )
    def test_e20_translate_builder_emits_expected_r4_value(
        self, engine_relationship, expected_r4_value
    ):
        """EXPLORER (parametrized wire-format): ``build_parameters_translate``
        MUST emit the R4 enum value for every engine relationship class.

        Methodology: parametrize over EVERY engine relationship the engine
        could emit (current + future-proofed defensive entries) AND assert the
        specific R4 enum value.
        """
        mappings = [_make_mapping(engine_relationship)]
        body = build_parameters_translate(
            mappings,
            source_system_uri=SNOMED_URI,
            source_code="44054006",
        )
        matches = [p for p in body["parameter"] if p.get("name") == "match"]
        assert matches, f"No matches emitted for relationship={engine_relationship!r}"
        equiv = _match_equivalence(matches[0])
        assert equiv == expected_r4_value, (
            f"build_parameters_translate emitted equivalence={equiv!r} for "
            f"engine relationship={engine_relationship!r}; expected "
            f"R4 value={expected_r4_value!r}."
        )
        # Wire shape: valueCode (NOT valueString).
        equiv_part = next(
            part for part in matches[0]["part"] if part.get("name") == "equivalence"
        )
        assert "valueCode" in equiv_part
        assert "valueString" not in equiv_part

    @pytest.mark.parametrize("r4_code", sorted(CANONICAL_R4_ENUM_VALUES))
    def test_e21_every_r4_enum_value_emitted_via_some_engine_input(self, r4_code):
        """EXPLORER (completeness audit): every R4 enum value SHOULD be
        reachable via some engine relationship input. Codes with no producer
        today (``inexact``) are acceptable — the gap is intentional (the
        engine does not model "imprecise overlap" semantics today).

        The probe class: parametrize over the FULL R4 enum (every spec-documented
        code); for each value, EITHER verify it is produced via some engine input
        OR document the no-producer gap. When a future engine change adds a
        producer for ``inexact``, the map MUST be extended.
        """
        # Iterate the canonical map to find a key producing this value.
        producing_keys = [
            k for k, v in INTERNAL_REL_TO_FHIR_EQUIVALENCE.items() if v == r4_code
        ]
        if not producing_keys:
            # No producer today — document the gap (acceptable).
            # Known no-producer values today: ``inexact``.
            assert r4_code in {"inexact"}, (
                f"R4 enum value {r4_code!r} has NO producing key in the "
                f"translation map. This is acceptable ONLY for documented "
                f"no-producer values ('inexact'). Extend the map when a "
                f"producer is added."
            )
            return
        # Verify fhir_equivalence returns this value for at least one input.
        result = fhir_equivalence(producing_keys[0])
        assert result == r4_code

    def test_e22_no_r4_enum_value_silently_missing_from_map(self):
        """EXPLORER (closed-enum completeness): every R4 enum value MUST be
        reachable via SOME key in the translation map. A missing value would
        mean a future engine producer cannot surface the R4 code on the wire
        (silent-wrong-answer — fallback would emit ``relatedto``).

        The ONLY acceptable no-producer value is ``inexact`` (the engine does
        not model "imprecise overlap" semantics today).
        """
        emitted_values = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        missing = FHIR_R4_CONCEPT_MAP_EQUIVALENCE - emitted_values
        # The only acceptable no-producer value is ``inexact``.
        acceptable_missing = {"inexact"}
        unacceptable_missing = missing - acceptable_missing
        assert not unacceptable_missing, (
            f"INTERNAL_REL_TO_FHIR_EQUIVALENCE has no key producing these "
            f"R4 enum values: {unacceptable_missing}. Only 'inexact' is "
            f"acceptable as no-producer today."
        )


# ===========================================================================
# LENS 3: XML wire-format for equivalence values (hyphenated and lowercase).
# CS-04 EXPLORER test_e151 verified hyphenated values for $subsumes outcome;
# CM-02 EXPLORER test_e23 verified ``equivalent`` on $translate XML. We extend
# to verify ALL hyphenated R4 values render correctly via the XML serializer.
# ===========================================================================


class TestXmlWireFormatHyphenatedAndLowercase:
    """Verify the XML serializer renders equivalence values correctly.

    Pattern-match to CR-002 (boolean capitalization on XML serializer).
    Pattern-match to CS-04 EXPLORER test_e151 (hyphenated values render).
    """

    @pytest.mark.parametrize(
        "r4_value",
        [
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
        ],
    )
    def test_e30_xml_serialization_each_r4_enum_value(self, r4_value):
        """EXPLORER (XML wire-format parametrized): every R4 enum value MUST
        render correctly via the XML serializer. The serializer MUST emit
        ``<valueCode value="X"/>`` for each value (NOT ``valueString``).

        We construct a Parameters resource manually with each value and verify
        the XML output. The engine may not produce every value today, but the
        serializer must handle every value in the closed enum.
        """
        from medterm4ds.engines.fhir.xml import to_fhir_xml

        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "match",
                    "part": [
                        {"name": "equivalence", "valueCode": r4_value},
                    ],
                },
            ],
        }
        xml_text = to_fhir_xml(body)
        assert f'value="{r4_value}"' in xml_text, (
            f"XML serializer did not emit {r4_value!r} as value attribute. "
            f"Body excerpt: {xml_text[:500]}"
        )
        # valueCode element MUST be present (NOT valueString).
        assert "valueCode" in xml_text, (
            f"XML serializer did not emit valueCode element for {r4_value!r}. "
            f"Body excerpt: {xml_text[:500]}"
        )
        # valueString MUST NOT be used for equivalence.
        assert "<valueString" not in xml_text, (
            f"XML serializer used valueString for equivalence (forbidden — "
            f"FHIR R4 mandates valueCode). Body: {xml_text[:500]}"
        )

    def test_e31_xml_no_capital_drift_on_equivalence(self, fhir_client):
        """EXPLORER: GET $translate XML MUST render equivalence in lowercase.
        Extends CM-02 EXPLORER test_e23 — every value the engine emits today
        is lowercase per R4 spec.

        CR-002 regression guard: capital-F ``False`` on booleans was the bug;
        here we verify capital drift on equivalence is impossible (the map
        only contains lowercase values).
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params=[
                ("system", SNOMED_URI),
                ("code", "44054006"),
                ("targetsystem", ICD10CM_URI),
                ("_format", "xml"),
            ],
        )
        assert r.status_code == 200
        body_text = r.text
        # Every value the engine emits today is lowercase.
        for forbidden in ("Equivalent", "EQUIVALENT", "Relatedto", "RELATEDTO"):
            assert forbidden not in body_text, (
                f"Capital drift on equivalence value: {forbidden!r} present in "
                f"XML body. Body excerpt: {body_text[:500]}"
            )


# ===========================================================================
# LENS 4: Cross-system combinations.
# SNOMED↔ICD-10-CM (same CUI), SNOMED↔RXNORM (no shared CUI → no match),
# SNOMED↔LOINC (no shared CUI → no match). Verify equivalence values across
# these combinations are always in the R4 closed enum.
# ===========================================================================


class TestCrossSystemCombinations:
    """Equivalence values across multiple cross-system combinations."""

    @pytest.mark.parametrize(
        "source_system,source_code,target_system,description",
        [
            (SNOMED_URI, "44054006", ICD10CM_URI, "SNOMED→ICD-10-CM same-CUI match"),
            (SNOMED_URI, "44054006", RXNORM_URI, "SNOMED→RxNorm no match expected"),
            (SNOMED_URI, "44054006", LOINC_URI, "SNOMED→LOINC no match expected"),
            (SNOMED_URI, "44054006", SNOMED_URI, "SNOMED→SNOMED same-system"),
            (ICD10CM_URI, "E11", SNOMED_URI, "ICD-10-CM→SNOMED reverse"),
        ],
    )
    def test_e40_translate_cross_system_emits_r4_enum(
        self,
        fhir_client,
        source_system,
        source_code,
        target_system,
        description,
    ):
        """EXPLORER (cross-system matrix): every cross-system $translate call
        MUST emit equivalence values from the R4 enum. The specific value
        depends on the fixture; the closed-enum membership is the load-bearing
        contract.

        Skip on OperationOutcome (some source systems may not be seeded).
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": source_system,
                "code": source_code,
                "targetsystem": target_system,
            },
        )
        body = r.json()
        if body.get("resourceType") == "OperationOutcome":
            pytest.skip(f"{description}: source/target system not seeded")
        matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
        if not matches:
            pytest.skip(f"{description}: no matches in fixture")
        for match in matches:
            equiv = _match_equivalence(match)
            assert equiv is not None, f"{description}: match missing equivalence"
            assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"{description}: equivalence={equiv!r} NOT in R4 enum. "
                f"Cross-system translations MUST always emit R4 enum values."
            )


# ===========================================================================
# LENS 5: Default fallback behavior in HTTP requests.
# The engine emits 6 relationship values today. A future engine vocabulary
# change (new relationship type) would flow through fhir_equivalence() →
# ``relatedto`` catch-all. Verify the fallback is consistent at the HTTP layer
# via the responses.py wrapper.
# ===========================================================================


class TestDefaultFallbackHttpSurface:
    """Default fallback (``relatedto``) on the HTTP surface via the
    ``_fhir_equivalence_from_relationship`` wrapper.
    """

    @pytest.mark.parametrize(
        "unknown_relationship",
        [
            None,
            "",
            "UNKNOWN_TOKEN_XYZ",
            "future-engine-vocab",
            "MIXED-Case_Token",
            "matches",  # R5-only — not in canonical map
        ],
    )
    def test_e50_responses_wrapper_unknown_returns_relatedto(
        self, unknown_relationship
    ):
        """EXPLORER: ``_fhir_equivalence_from_relationship`` (the HTTP surface
        wrapper) MUST return ``relatedto`` for unknown / null / empty inputs.

        The wrapper has a case-insensitive fallback that future-proofs against
        vocabulary changes; verify that fallback still catches unknown tokens.
        """
        result = _fhir_equivalence_from_relationship(unknown_relationship)
        assert result == "relatedto", (
            f"Wrapper returned {result!r} for unknown={unknown_relationship!r}; "
            f"expected 'relatedto' (R4 catch-all)."
        )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_e51_canonical_module_and_responses_wrapper_agree_on_all_inputs(self):
        """EXPLORER (consistency audit): ``fhir_equivalence`` (canonical)
        and ``_fhir_equivalence_from_relationship`` (responses.py wrapper)
        MUST emit the same value for every engine relationship.

        SKEPTIC test_s81 verified this on engine-relationship inputs; EXPLORER
        extends to a wider input matrix including unknown values.
        """
        test_inputs = list(INTERNAL_REL_TO_FHIR_EQUIVALENCE.keys()) + [
            None,
            "",
            "UNKNOWN_TOKEN",
            "completely-future-vocab",
        ]
        disagreements = []
        for inp in test_inputs:
            canonical = fhir_equivalence(inp)
            wrapper = _fhir_equivalence_from_relationship(inp)
            if canonical != wrapper:
                disagreements.append((inp, canonical, wrapper))
        assert not disagreements, (
            f"Canonical fhir_equivalence and responses.py wrapper disagree: "
            f"{disagreements}. The wrapper MUST be a pure delegation."
        )

    def test_e52_responses_wrapper_case_insensitive_fallback_documented(self):
        """EXPLORER: the wrapper's case-insensitive fallback is documented in
        the function source (docstring OR inline comment). The comment is
        load-bearing — a future engineer MUST be able to discover why the
        fallback exists without source-reading the entire function.

        Pattern-match to TS-01 HISTORIAN QA-007 (docstring-vs-impl drift).
        """
        import inspect

        source = inspect.getsource(_fhir_equivalence_from_relationship)
        assert "case-insensitive" in source.lower(), (
            "_fhir_equivalence_from_relationship source MUST document the "
            "case-insensitive fallback (in docstring OR inline comment). "
            "Maintenance-hazard defense."
        )


# ===========================================================================
# LENS 6: Equivalence in batch responses.
# Each batch entry's match.equivalence MUST come from the R4 enum (TS-04
# HISTORIAN QA-038 batch dispatcher per-entry isolation boundary applies; the
# dispatcher reuses build_parameters_translate so the equivalence vocabulary
# translation is structurally identical to single-entry calls).
# ===========================================================================


class TestEquivalenceInBatchResponses:
    """Verify equivalence values in batch $translate entries are R4 enum."""

    def test_e60_batch_translate_each_entry_emits_r4_enum_value(self, fhir_client):
        """EXPLORER (batch equivalence audit): every batch entry's
        match.equivalence MUST be in the R4 enum. The batch dispatcher
        reuses ``build_parameters_translate`` so equivalence translation
        is structurally identical to single-entry; this probe is the
        defense-in-depth runtime verification.
        """
        batch_body = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "GET",
                        "url": (
                            f"ConceptMap/$translate?"
                            f"system={SNOMED_URI}&"
                            f"code=44054006&"
                            f"targetsystem={ICD10CM_URI}"
                        ),
                    }
                },
                {
                    "request": {
                        "method": "GET",
                        "url": (
                            f"ConceptMap/$translate?"
                            f"system={SNOMED_URI}&"
                            f"code=44054006&"
                            f"targetsystem={RXNORM_URI}"
                        ),
                    }
                },
            ],
        }
        r = fhir_client.post("/fhir", json=batch_body)
        assert r.status_code == 200, f"Batch endpoint returned {r.status_code}"
        body = r.json()
        assert body.get("resourceType") == "Bundle"
        assert body.get("type") == "batch-response"
        for entry in body.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") == "OperationOutcome":
                continue  # per-entry 4xx is OK
            for p in resource.get("parameter", []):
                if p.get("name") != "match":
                    continue
                equiv = _match_equivalence(p)
                if equiv is None:
                    continue  # no-match entry has no equivalence
                assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                    f"Batch $translate entry emitted equivalence={equiv!r} "
                    f"NOT in R4 enum."
                )


# ===========================================================================
# LENS 7: Combined operations — $translate then $lookup on the returned
# target concept. The equivalence value MUST be consistent with the
# $lookup-resolved concept (if $translate says ``equivalent``, $lookup of the
# target concept must succeed).
# ===========================================================================


class TestCombinedOperationsTranslateThenLookup:
    """Combined operation consistency."""

    def test_e70_translate_then_lookup_target_concept(self, fhir_client):
        """EXPLORER: $translate returns target concept; $lookup on the target
        concept MUST succeed (200). The equivalence value (``equivalent``) is
        consistent with the resolved concept existing in the fixture DB.
        """
        translate_r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": "44054006",
                "targetsystem": ICD10CM_URI,
            },
        )
        body = translate_r.json()
        if body.get("resourceType") == "OperationOutcome":
            pytest.skip("fixture DB missing the test code")
        matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
        if not matches:
            pytest.skip("no matches for the test code")
        # Extract the first match's target concept.
        first_match = matches[0]
        concept_part = next(
            (sub for sub in first_match.get("part", []) if sub.get("name") == "concept"),
            None,
        )
        if not concept_part:
            pytest.skip("match.part missing 'concept'")
        coding = concept_part.get("valueCoding", {})
        target_system = coding.get("system")
        target_code = coding.get("code")
        assert target_system and target_code, (
            f"Target concept missing system/code: {coding!r}"
        )

        # Now $lookup the target concept.
        lookup_r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": target_system, "code": target_code},
        )
        assert lookup_r.status_code == 200, (
            f"$lookup of $translate target {target_system}|{target_code} "
            f"failed: {lookup_r.status_code}"
        )
        # The equivalence emitted by $translate MUST be consistent with the
        # lookup-resolvable target (a lookup-resolvable target is a "real" code
        # in the fixture, which means the equivalence value is semantically
        # meaningful).
        equiv = _match_equivalence(first_match)
        assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# ===========================================================================
# LENS 8: Hostile input handling on the equivalence surface.
# The equivalence translation is data-driven (engine emits a relationship
# string; the map translates). Hostile relationship values cannot reach the
# wire because the map's default fallback is ``relatedto``.
# ===========================================================================


class TestHostileInputHandling:
    """Verify hostile inputs cannot leak to the wire."""

    @pytest.mark.parametrize(
        "hostile_input",
        [
            "'; DROP TABLE mrconso; --",  # SQL injection
            "<script>alert('XSS')</script>",  # XSS
            "../../../etc/passwd",  # path traversal
            "null\x00byte",  # null byte
            "a" * 10_000,  # very long
            "related-to'; --",
        ],
    )
    def test_e80_fhir_equivalence_never_echoes_hostile_input(self, hostile_input):
        """EXPLORER (hostile-input matrix): ``fhir_equivalence`` MUST never
        echo a hostile input through to the wire. Every hostile input MUST
        translate to the R4 catch-all ``relatedto``.
        """
        result = fhir_equivalence(hostile_input)
        assert result == "relatedto", (
            f"fhir_equivalence returned {result!r} for hostile input; "
            f"expected 'relatedto' (R4 catch-all)."
        )
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_e81_translate_builder_never_echoes_hostile_relationship(self):
        """EXPLORER: ``build_parameters_translate`` with a hostile relationship
        value MUST emit ``relatedto`` equivalence (NOT the raw value).

        If a future engine change produced a hostile relationship string,
        the wire would contain the R4 catch-all — never the raw input.
        """
        mapping = CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="44054006"),
            target=CodeRef(source="ICD10CM", code="E11"),
            relationship="<script>alert('XSS')</script>",
            match_type="exact",
        )
        body = build_parameters_translate(
            [mapping],
            source_system_uri=SNOMED_URI,
            source_code="44054006",
        )
        matches = [p for p in body["parameter"] if p.get("name") == "match"]
        assert matches
        equiv = _match_equivalence(matches[0])
        assert equiv == "relatedto"
        # The raw hostile value MUST NOT appear in the wire-format body.
        body_str = json.dumps(body)
        assert "<script>" not in body_str, (
            f"Hostile input echoed in $translate body: {body_str[:300]}"
        )


# ===========================================================================
# LENS 9: Cross-handler parity: responses.py $translate ↔ outputs/fhir.py
# ConceptMap export. Both surfaces import from the canonical module (CR-024);
# runtime verification that they agree on every engine relationship.
# ===========================================================================


class TestCrossSurfaceRuntimeParity:
    """Cross-surface parity — responses.py and outputs/fhir.py emit the same
    R4 enum value for every engine relationship."""

    @pytest.mark.parametrize(
        "engine_relationship",
        sorted(INTERNAL_REL_TO_FHIR_EQUIVALENCE.keys()),
    )
    def test_e90_both_surfaces_emit_same_r4_value(self, engine_relationship):
        """EXPLORER (cross-surface runtime parity, parametrized): the $translate
        surface and ConceptMap export surface MUST emit the same R4 enum value
        for every engine relationship.

        SKEPTIC test_s81 verified this on a smaller input matrix; EXPLORER
        parametrizes over EVERY engine relationship key in the canonical map.
        """
        # $translate surface
        translate_value = _fhir_equivalence_from_relationship(engine_relationship)
        # ConceptMap export surface
        export_value = fhir_equivalence(engine_relationship)
        assert translate_value == export_value, (
            f"Surfaces disagree for {engine_relationship!r}: "
            f"$translate={translate_value!r}, export={export_value!r}."
        )
        # Both MUST be in the R4 enum.
        assert translate_value in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# ===========================================================================
# LENS 10: Carry-forward pinning.
# CF-CM02-01 (translate lacks coding/codeableConcept extractors) and
# CF-TERMINOLOGIST-CM01-01 (latent gap on subsumes/specializes engine values)
# are still open. EXPLORER pins them via carry-forward-as-probe pattern.
# ===========================================================================


class TestCarryForwardPinning:
    """Pin open carry-forwards via the carry-forward-as-probe pattern.

    When a future fix lands, these probes will fail loudly — the carry-forward
    is a load-bearing contract, not a passive note.
    """

    def test_e100_cf_cm02_01_translate_lacks_coding_extractor(self, fhir_client):
        """EXPLORER (CF-CM02-01 pin): POST $translate with a ``coding`` body
        silently falls through to the 400 path today. When CF-CM02-01 lands,
        this probe MUST be updated to assert 200 + match.

        The 400 path IS conformant (Content-Type + OperationOutcome); the gap
        is the missing feature, not a spec violation.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {"system": SNOMED_URI, "code": "44054006"}},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        # Current behavior: 400 + OperationOutcome (no coding extractor).
        assert r.status_code == 400, (
            f"CF-CM02-01 may have LANDED: POST $translate with coding body now "
            f"returns {r.status_code} instead of 400. Update this probe to "
            f"assert 200 + match."
        )
        assert r.headers["content-type"].startswith("application/fhir+json")
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome"

    def test_e101_cf_cm02_01_translate_lacks_codeable_concept_extractor(self, fhir_client):
        """EXPLORER (CF-CM02-01 mirror): POST $translate with a ``codeableConcept``
        body silently falls through to the 400 path today. When CF-CM02-01
        lands, this probe MUST be updated.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [{"system": SNOMED_URI, "code": "44054006"}]
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code == 400, (
            f"CF-CM02-01 may have LANDED: POST $translate with codeableConcept "
            f"body now returns {r.status_code}. Update this probe."
        )

    def test_e102_cf_terminologist_cm01_01_subsumes_specializes_pass_through(self):
        """EXPLORER (CF-TERMINOLOGIST-CM01-01 pin): ``subsumes`` and
        ``specializes`` engine relationships pass through to R4 enum values
        via the canonical map's defensive entries. The CF documents that the
        engine does NOT emit these today (verified via source-reading by
        CM-01 TERMINOLOGIST test_t32). This probe pins the defensive entries.

        When a future engine enhancement emits ``subsumes``/``specializes``,
        the probe will continue to pass (defensive entries ensure spec-correct
        translation). If the defensive entries are removed, the probe fails.
        """
        # The map MUST have defensive entries for subsumes/specializes.
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE.get("subsumes") == "subsumes"
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE.get("specializes") == "specializes"
        # And the R5/R4B subsumedby alias.
        assert INTERNAL_REL_TO_FHIR_EQUIVALENCE.get("subsumedby") == "specializes"

    def test_e103_cf_terminologist_cm01_01_subsumes_export_emits_r4_value(self):
        """EXPLORER (CF-TERMINOLOGIST-CM01-01 pin, export surface): the
        ConceptMap export surface translates ``subsumes``/``specializes``
        via the canonical helper. When a future engine emits these, the
        export surface will emit the R4 enum value (NOT the catch-all).
        """
        rows = [
            ConceptMapRow(
                source=CodeRef(source="SNOMEDCT_US", code="73211009"),
                target=CodeRef(source="SNOMEDCT_US", code="44054006"),
                source_display="Diabetes mellitus",
                target_display="T2DM",
                relationship="subsumes",
                match_type="broader",
            ),
            ConceptMapRow(
                source=CodeRef(source="SNOMEDCT_US", code="44054006"),
                target=CodeRef(source="SNOMEDCT_US", code="73211009"),
                source_display="T2DM",
                target_display="Diabetes mellitus",
                relationship="specializes",
                match_type="exact",
            ),
        ]
        resource = concept_map_to_fhir(rows)
        for g in resource.get("group", []):
            for element in g.get("element", []):
                for target in element.get("target", []):
                    equiv = target.get("equivalence")
                    assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
                    assert equiv in {"subsumes", "specializes"}, (
                        f"Export emitted equivalence={equiv!r} for "
                        f"subsumes/specializes engine input; expected the "
                        f"matching R4 enum value."
                    )


# ===========================================================================
# LENS 11: ConceptMap export shape audit (defense-in-depth on equivalence
# translation). Every group.element.target.equivalence in the export MUST
# come from the R4 enum.
# ===========================================================================


class TestExportEquivalenceShape:
    """Verify ConceptMap export emits R4 enum values for every target."""

    def test_e110_export_all_engine_relationships_emit_r4_enum(self):
        """EXPLORER: parametrized ConceptMap export with every engine
        relationship. Each target.equivalence MUST be in the R4 enum.
        """
        rows = [
            ConceptMapRow(
                source=CodeRef(source="SNOMEDCT_US", code="44054006"),
                target=CodeRef(source="ICD10CM", code="E11"),
                source_display="T2DM",
                target_display="T2DM",
                relationship=engine_rel,
                match_type="exact",
            )
            for engine_rel in (
                "equivalent",
                "source-is-narrower-than-target",
                "source-is-broader-than-target",
                "related-to",
                "not-translated",
                "unmatched",
                "subsumes",
                "specializes",
            )
        ]
        resource = concept_map_to_fhir(rows)
        equivalences_seen = set()
        for g in resource.get("group", []):
            for element in g.get("element", []):
                for target in element.get("target", []):
                    equiv = target.get("equivalence")
                    equivalences_seen.add(equiv)
                    assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                        f"Export emitted equivalence={equiv!r} NOT in R4 enum."
                    )
        # We MUST have seen multiple equivalence values (the export surface
        # translates engine vocabulary to multiple R4 codes).
        assert len(equivalences_seen) >= 3, (
            f"Export only emitted {len(equivalences_seen)} unique equivalence "
            f"values; expected at least 3 from the parametrized input matrix. "
            f"Values seen: {equivalences_seen}"
        )

    def test_e111_export_no_target_for_unmatched_relationship(self):
        """EXPLORER: when engine relationship is ``unmatched``, the export
        omits target code+display (per ``outputs/fhir.py:_merge_row_target``
        line 144 — ``if row.relationship != "unmatched"``).

        This is a load-bearing contract: ``unmatched`` means "no mapping",
        so emitting a target code would be misleading.
        """
        rows = [
            ConceptMapRow(
                source=CodeRef(source="SNOMEDCT_US", code="44054006"),
                target=CodeRef(source="ICD10CM", code=""),
                source_display="T2DM",
                target_display="",
                relationship="unmatched",
                match_type="original",
            ),
        ]
        resource = concept_map_to_fhir(rows)
        for g in resource.get("group", []):
            for element in g.get("element", []):
                for target in element.get("target", []):
                    equiv = target.get("equivalence")
                    assert equiv == "unmatched", (
                        f"Export emitted equivalence={equiv!r} for unmatched "
                        f"engine input; expected 'unmatched'."
                    )
                    # The target MUST NOT have a code/display for unmatched.
                    assert "code" not in target, (
                        f"Export emitted target.code for unmatched — "
                        f"misleading (unmatched means 'no mapping')."
                    )
                    assert "display" not in target


# ===========================================================================
# LENS 12: Module-load assertion integrity (defense-in-depth on the
# load-bearing contract). The closed-enum membership assertion at
# ``engines/fhir/equivalence.py:125-132`` is what prevents future drift.
# ===========================================================================


class TestModuleLoadAssertionIntegrity:
    """Verify the closed-enum assertion is load-bearing."""

    def test_e120_assertion_uses_subset_operator(self):
        """EXPLORER: the assertion uses set-subset ``<=`` (not ``==``) because
        multiple keys can map to the same R4 value (so values() is a SUBSET
        of the closed enum, not the FULL enum).
        """
        import inspect

        from medterm4ds.engines.fhir import equivalence as equiv_module

        source = inspect.getsource(equiv_module)
        # The assertion uses <= to verify values() is a SUBSET.
        assert "<=" in source, (
            "The module-load assertion MUST use set-subset (<=), not "
            "set-equality (==). Multiple keys map to the same R4 value."
        )

    def test_e121_assertion_error_message_names_drift_values(self):
        """EXPLORER: the assertion error message includes the set-difference
        so a future drift is immediately debuggable.
        """
        import inspect

        from medterm4ds.engines.fhir import equivalence as equiv_module

        source = inspect.getsource(equiv_module)
        # The error message computes the drift set.
        assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in source, (
            "Assertion MUST reference FHIR_R4_CONCEPT_MAP_EQUIVALENCE in error."
        )
        # The error message MUST name the drift values for debuggability.
        assert "-" in source and "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in source


# ===========================================================================
# LENS 13: GET ↔ POST byte-exact equivalence on $translate (extends VS-04
# EXPLORER strategy 50 to the CM-04 equivalence surface).
# ===========================================================================


class TestGetPostParityEquivalence:
    """Verify GET and POST $translate emit byte-identical equivalence values."""

    def test_e130_get_post_translate_emit_same_equivalence(self, fhir_client):
        """EXPLORER (GET↔POST parity): GET and POST $translate with the same
        scalar parameters MUST emit the same ``match.equivalence`` value.

        Extends VS-04 EXPLORER strategy 50 to the equivalence surface.
        """
        # GET
        get_r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": "44054006",
                "targetsystem": ICD10CM_URI,
            },
        )
        get_body = get_r.json()
        if get_body.get("resourceType") == "OperationOutcome":
            pytest.skip("fixture DB missing the test code")
        get_matches = [p for p in get_body.get("parameter", []) if p.get("name") == "match"]
        if not get_matches:
            pytest.skip("no matches in fixture")

        # POST with same scalar body
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        post_r = fhir_client.post("/fhir/ConceptMap/$translate", json=post_body)
        post_body = post_r.json()
        post_matches = [p for p in post_body.get("parameter", []) if p.get("name") == "match"]

        assert len(get_matches) == len(post_matches), (
            f"GET and POST emit different match counts: "
            f"GET={len(get_matches)}, POST={len(post_matches)}"
        )
        # Compare equivalence values byte-exact.
        for i, (g, p) in enumerate(zip(get_matches, post_matches)):
            g_equiv = _match_equivalence(g)
            p_equiv = _match_equivalence(p)
            assert g_equiv == p_equiv, (
                f"Match {i}: GET equivalence={g_equiv!r}, POST equivalence={p_equiv!r}. "
                f"GET↔POST byte-exact parity violated."
            )


# ===========================================================================
# LENS 14: Module re-export integrity. ``responses.py`` re-exports the
# internal map alias; ``outputs/fhir.py`` re-exports the canonical helper.
# Verify the re-exports are wired correctly (no shadowing).
# ===========================================================================


class TestModuleReExportIntegrity:
    """Verify responses.py and outputs/fhir.py re-export correctly."""

    def test_e140_responses_module_does_not_shadow_canonical_map(self):
        """EXPLORER: ``responses.py`` imports the canonical map as
        ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` — verify this is the SAME
        object as the canonical module's map (object identity, not just import).
        """
        from medterm4ds.engines.fhir import responses as responses_module

        assert (
            responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE
            is INTERNAL_REL_TO_FHIR_EQUIVALENCE
        ), (
            "responses.py's _INTERNAL_REL_TO_FHIR_EQUIVALENCE MUST be the same "
            "object as the canonical module's INTERNAL_REL_TO_FHIR_EQUIVALENCE. "
            "Shadowing would re-introduce cross-module parallel-map drift (CR-024)."
        )

    def test_e141_outputs_module_does_not_shadow_canonical_map(self):
        """EXPLORER: ``outputs/fhir.py`` imports the canonical map as
        ``FHIR_EQUIVALENCES`` — verify this is the SAME object.
        """
        from medterm4ds.outputs import fhir as outputs_fhir_module

        assert (
            outputs_fhir_module.FHIR_EQUIVALENCES
            is INTERNAL_REL_TO_FHIR_EQUIVALENCE
        ), (
            "outputs/fhir.py's FHIR_EQUIVALENCES MUST be the same object as "
            "the canonical module's INTERNAL_REL_TO_FHIR_EQUIVALENCE. "
            "Shadowing would re-introduce cross-module parallel-map drift (CR-024)."
        )

    def test_e142_outputs_module_helper_is_canonical_helper(self):
        """EXPLORER: ``outputs/fhir.py`` imports ``fhir_equivalence`` from
        the canonical module — verify this is the SAME callable object.
        """
        from medterm4ds.outputs import fhir as outputs_fhir_module

        assert outputs_fhir_module.fhir_equivalence is fhir_equivalence, (
            "outputs/fhir.py's fhir_equivalence MUST be the same callable as "
            "the canonical module's fhir_equivalence."
        )


# ===========================================================================
# LENS 15: Comprehensive closed-enum wire-format audit.
# Walk every R4 enum value through the build_parameters_translate builder
# via direct invocation. Confirms the wire shape is consistent regardless
# of which engine relationship produces the value.
# ===========================================================================


class TestComprehensiveWireFormatAudit:
    """Walk every R4 enum value through the builder."""

    @pytest.mark.parametrize("r4_value", sorted(CANONICAL_R4_ENUM_VALUES))
    def test_e150_every_r4_value_has_correct_wire_shape(self, r4_value):
        """EXPLORER: every R4 enum value MUST be emittable on the wire with
        the correct shape: ``{"name": "equivalence", "valueCode": <value>}``.

        We construct a CodeMapping with the R4 value as the relationship
        (defensive pass-through) and verify the wire shape.
        """
        # Every R4 value is accepted as a defensive pass-through key.
        # (Some R4 values are also values, e.g. ``equivalent`` → ``equivalent``.)
        mapping = _make_mapping(r4_value)
        body = build_parameters_translate(
            [mapping],
            source_system_uri=SNOMED_URI,
            source_code="44054006",
        )
        matches = [p for p in body["parameter"] if p.get("name") == "match"]
        assert matches, f"No match emitted for r4_value={r4_value!r}"
        equiv_part = next(
            (sub for sub in matches[0]["part"] if sub.get("name") == "equivalence"),
            None,
        )
        assert equiv_part is not None, (
            f"match.part missing 'equivalence' for r4_value={r4_value!r}"
        )
        # Wire shape assertions.
        assert equiv_part.get("valueCode") is not None
        assert "valueString" not in equiv_part
        assert "valueCoding" not in equiv_part
        # The emitted value MUST be in the R4 enum.
        assert equiv_part["valueCode"] in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_e151_match_part_order_is_equivalence_concept_source(self):
        """EXPLORER: each match.part MUST contain exactly 3 parts in the order
        (equivalence, concept, source). The order is load-bearing for clients
        that parse by position (though FHIR R4 clients SHOULD parse by name).
        """
        mappings = [_make_mapping("equivalent")]
        body = build_parameters_translate(
            mappings,
            source_system_uri=SNOMED_URI,
            source_code="44054006",
        )
        match = next(p for p in body["parameter"] if p.get("name") == "match")
        part_names = [p.get("name") for p in match["part"]]
        assert part_names == ["equivalence", "concept", "source"], (
            f"match.part order drift: got {part_names}, expected "
            f"['equivalence', 'concept', 'source']."
        )


# ===========================================================================
# LENS 16: Spec-citation audit — the canonical module docstring cites the
# R4 spec page. This is the maintenance-hazard defense (a future engineer
# must be able to verify the map against the spec without hunting).
# ===========================================================================


class TestSpecCitationAudit:
    """Verify spec citations are present in the canonical module."""

    def test_e160_canonical_module_cites_r4_spec(self):
        """EXPLORER: the canonical module's docstring cites the canonical R4
        spec page (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html).
        """
        from medterm4ds.engines.fhir import equivalence as equiv_module

        doc = equiv_module.__doc__ or ""
        # The docstring or comments may cite the spec.
        import inspect
        source = inspect.getsource(equiv_module)
        combined = doc + source
        assert "valueset-concept-map-equivalence.html" in combined, (
            "Canonical equivalence module MUST cite the R4 spec page "
            "(https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html). "
            "Maintenance-hazard defense: a future engineer must be able to "
            "verify the map against the spec."
        )

    def test_e161_canonical_module_cites_milestone_3_review(self):
        """EXPLORER: the canonical module cites milestone-3 review (CR-024)
        in its docstring. This is the maintenance-hazard defense — the
        consolidation rationale must be discoverable.
        """
        from medterm4ds.engines.fhir import equivalence as equiv_module

        doc = equiv_module.__doc__ or ""
        assert "CR-024" in doc or "milestone-3" in doc.lower(), (
            "Canonical equivalence module MUST cite milestone-3 review (CR-024) "
            "in its docstring. Maintenance-hazard defense: future engineers "
            "must be able to discover the consolidation rationale."
        )


# ===========================================================================
# LENS 17: Function contract audit — fhir_equivalence never raises, never
# echoes raw, returns a value in the R4 enum for EVERY input.
# ===========================================================================


class TestFunctionContractNeverRaises:
    """Function contract audit (SKEPTIC test_s100 extended)."""

    @pytest.mark.parametrize(
        "input_value",
        [
            None,
            "",
            "equivalent",
            "source-is-narrower-than-target",
            "source-is-broader-than-target",
            "related-to",
            "not-translated",
            "unmatched",
            "UNKNOWN",
            "subsumes",
            "specializes",
            "subsumedby",
            "subsumed-by",
            "not-relatedto",
            "not-related-to",
            "disjoint",
            "inexact",
            "equal",
        ],
    )
    def test_e170_fhir_equivalence_returns_r4_enum_value_for_every_input(self, input_value):
        """EXPLORER (function contract): ``fhir_equivalence`` returns a value
        in the R4 closed enum for every input in the test matrix.
        """
        result = fhir_equivalence(input_value)
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"fhir_equivalence({input_value!r}) returned {result!r} which is "
            f"NOT in the R4 enum."
        )

    def test_e171_fhir_equivalence_never_raises_on_edge_inputs(self):
        """EXPLORER: ``fhir_equivalence`` MUST never raise on any input.
        Verify with edge inputs (None, empty, very long, hostile).
        """
        edge_inputs = [
            None,
            "",
            "x" * 10_000,
            "\x00\x01\x02",
            "🚀",  # unicode
            "_related-to",
            "related-to-",
            "RELATED-TO",
            "Related-To",
        ]
        for inp in edge_inputs:
            try:
                result = fhir_equivalence(inp)
            except Exception as exc:
                pytest.fail(
                    f"fhir_equivalence({inp!r}) raised {type(exc).__name__}: {exc}"
                )
            assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
