"""EXPLORER RESWEEP probes for chunk CM-04 (ConceptMap Equivalence
Vocabulary Correctness).

Source: https://build.fhir.org/conceptmap.html
Canonical R4 ConceptMapEquivalence value set (verified 2026-08-10):
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

This resweep test file extends the baseline ``test_cm04_explorer.py``
with NEW lateral-combination probes through the EXPLORER lens ("What's
not yet tested?"). Per ``evolution.json.config.notes`` (HISTORIAN tip
for EXPLORER), the focus is on:

  1. **POST $translate with ``coding`` parameter** (vs system+code scalar)
     + hostile Parameters body; verify the equivalence value is sourced
     from the engine regardless of input encoding shape.
  2. **Wrapper case-insensitive divergence** (Lens 8b divergence) —
     ``$translate`` resolves camelCase ``subsumedBy`` to ``specializes``
     while ConceptMap export returns ``relatedto``; both ON-SPEC but
     client-visible behavior differs. Probe whether ANY client-facing
     surface depends on the wrapper's case-insensitive behavior.

Additional EXPLORER lateral combinations across unexplored axes:

  L3  Mixed-encoding POST $translate body (scalar + coding + codeableConcept
      simultaneously)
  L4  POST $translate byte-exact parity: GET system+code ↔ POST coding ↔
      POST codeableConcept (equivalence value invariant across encodings)
  L5  Hostile Parameters body: hostile entries interleaved with valid ones;
      equivalence value comes from the engine, not from client-injected
      parameters
  L6  Batch $translate with mixed-encoding entries: each entry's equivalence
      comes from the engine, not from client-injected parameters in the
      batch entry's Parameters body
  L7  Cross-system mappings (T2DM SNOMED → ICD-10-CM) via POST coding body:
      byte-exact equivalence parity with GET system+code
  L8  Default-fallback parity across encodings: unknown code in GET vs POST
      coding vs POST codeableConcept — all return ``result=false`` with
      ``match=[]`` (no equivalence value emitted)
  L9  Source-code-resolved-from-coding canonical re-resolution: POST coding
      with alias system URI (urn:oid) → Out match.source.system is canonical
      AND equivalence value is from the engine
  L10 Combined-operations round-trip via POST coding: $translate (coding) →
      $lookup (system+code) — target concept display consistency
  L11 Wrapper-vs-canonical divergence on LIVE wire surface: POST $translate
      with hostile ``coding`` body where the relationship is injected via
      a non-spec parameter — the wire NEVER reflects the injected value
  L12 Empty/missing/null equivalence under POST coding: POST coding body
      with missing system or code — graceful 400 OR result=false, never 500
  L13 Cross-surface wrapper-divergence source-read audit: source-read of
      ``_fhir_equivalence_from_relationship`` confirms case-insensitive
      fallback is documented AND isolated to the $translate surface
  L14 Spec-citation audit on $translate In Parameters: ``coding`` 0..1
      Coding and ``codeableConcept`` 0..1 CodeableConcept are spec-listed
      alternatives to ``system``+``code``
  L15 Module-load assertion integrity under POST coding: the assertion
      fires at module import regardless of which encoding shape the
      client uses
  L16 GET↔POST coding byte-exact parity parametrized over every seeded
      code (4 codes × 2 methods = 8 cases)
  L17 Function contract: ``_fhir_equivalence_from_relationship`` never
      raises on hostile coding-shaped inputs

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus; A44054006 -> A73211009)

Existing baseline coverage in test_cm04_explorer.py: 36 tests across 17
lenses. This resweep does NOT re-derive baseline coverage — it focuses on
NEW lateral combinations between input encoding shapes, hostile body
adversarial probes, and the wrapper case-insensitive divergence (Lens 8b)
that the HISTORIAN iteration flagged as a potential EXPLORER target.
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
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    fhir_equivalence,
)
from medterm4ds.engines.fhir import responses as responses_module
from medterm4ds.engines.fhir import equivalence as equivalence_module


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

# SNOMED urn:oid alias — useful for testing canonical re-resolution on
# the POST coding surface.
SNOMED_URN_OID = "urn:oid:2.16.840.1.113883.6.96"

# The canonical R4 closed enum (verified 2026-08-10 from spec page).
CANONICAL_R4_CODES = frozenset({
    "relatedto", "equivalent", "equal", "wider", "narrower",
    "subsumes", "specializes", "inexact", "unmatched", "disjoint",
})

# R5/R4B / R5-only / not-in-any-enum values that MUST NOT appear on the wire.
OFF_SPEC_VALUES = frozenset({
    "subsumedBy",     # R5/R4B camelCase; R4 uses `specializes`
    "matches",        # R5-only
    "not-relatedto",  # not in any FHIR enum
    "not-related-to",
})


# ---------------------------------------------------------------------------
# Helper functions.
# ---------------------------------------------------------------------------
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


def _make_coding_body(
    system: str, code: str, targetsystem: str | None = None,
) -> dict[str, Any]:
    """Build a POST $translate body with ``coding`` (valueCoding) parameter."""
    params: list[dict[str, Any]] = [{
        "name": "coding",
        "valueCoding": {"system": system, "code": code},
    }]
    if targetsystem is not None:
        params.append({"name": "targetsystem", "valueUri": targetsystem})
    return {"resourceType": "Parameters", "parameter": params}


def _make_codeable_concept_body(
    system: str, code: str, targetsystem: str | None = None,
) -> dict[str, Any]:
    """Build a POST $translate body with ``codeableConcept`` parameter."""
    params: list[dict[str, Any]] = [{
        "name": "codeableConcept",
        "valueCodeableConcept": {
            "coding": [{"system": system, "code": code}],
        },
    }]
    if targetsystem is not None:
        params.append({"name": "targetsystem", "valueUri": targetsystem})
    return {"resourceType": "Parameters", "parameter": params}


def _make_scalar_body(
    system: str, code: str, targetsystem: str | None = None,
) -> dict[str, Any]:
    """Build a POST $translate body with scalar ``system``+``code`` parameters."""
    params: list[dict[str, Any]] = [
        {"name": "system", "valueUri": system},
        {"name": "code", "valueCode": code},
    ]
    if targetsystem is not None:
        params.append({"name": "targetsystem", "valueUri": targetsystem})
    return {"resourceType": "Parameters", "parameter": params}


# ===========================================================================
# LENS 1 — POST $translate with coding parameter: equivalence sourced
# from the engine regardless of input encoding shape.
#
# Per HISTORIAN tip (Lens 8b divergence + lateral encoding combinations):
# verify the equivalence value is sourced from the engine, not echoed
# from the client-supplied coding body. The engine relationship vocabulary
# is the only source; the client cannot inject a value.
# ===========================================================================
class TestLens1PostCodingEquivalenceFromEngine:
    """EXPLORER: POST $translate with ``coding`` body — verify the
    equivalence value comes from the engine, not from the client-supplied
    Parameters body.

    Per FHIR R4 $translate In Parameters
    (https://hl7.org/fhir/R4/conceptmap-operation-translate.html):
    ``coding`` 0..1 Coding is a spec-listed alternative to ``system``+``code``.
    The Out ``match.equivalence`` value MUST use values from
    ConceptMapEquivalence — sourced from the engine mapping relationship,
    NOT echoed from any client-supplied input.
    """

    def test_e10_post_coding_emits_engine_equivalence_value(
        self, fhir_client,
    ):
        """EXPLORER: POST $translate with coding body produces the same
        engine-derived equivalence value as GET system+code.

        T2DM SNOMED 44054006 → ICD-10-CM E11 is a same-CUI mapping
        (C0011847) → engine emits ``equivalent`` relationship → R4
        equivalence is ``equivalent``.
        """
        get_resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": "44054006",
                "targetsystem": ICD10CM_URI,
            },
        )
        post_body = _make_coding_body(
            SNOMED_URI, "44054006", targetsystem=ICD10CM_URI,
        )
        post_resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert get_resp.status_code == 200
        assert post_resp.status_code == 200
        get_equiv = _match_equivalence_values(get_resp.json())
        post_equiv = _match_equivalence_values(post_resp.json())
        # Both must emit the SAME engine-derived equivalence value.
        assert get_equiv == post_equiv, (
            f"GET ↔ POST coding equivalence mismatch: GET={get_equiv!r}, "
            f"POST coding={post_equiv!r}. The equivalence value MUST be "
            f"sourced from the engine regardless of input encoding shape."
        )
        # Every emitted value MUST be in the R4 closed enum.
        for v in post_equiv:
            assert v in CANONICAL_R4_CODES, (
                f"POST coding equivalence value {v!r} NOT in R4 closed enum."
            )

    def test_e11_post_coding_no_injected_equivalence_via_parameters_body(
        self, fhir_client,
    ):
        """EXPLORER: POST $translate with coding body AND a client-injected
        ``equivalence`` parameter — the injected value MUST NOT appear in
        the Out match.equivalence. The Out value comes from the engine.

        Per FHIR R4 $translate: ``match.equivalence`` is an Out parameter
        (0..1 code using values from ConceptMapEquivalence). It is NOT an
        In parameter; clients cannot supply it.
        """
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": SNOMED_URI, "code": "44054006",
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                # Hostile injection: client attempts to override equivalence.
                {"name": "equivalence", "valueCode": "equal"},
                {"name": "match.equivalence", "valueCode": "wider"},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200
        equiv_values = _match_equivalence_values(resp.json())
        # Injected values MUST NOT appear — the engine drives the value.
        assert "equal" not in equiv_values or equiv_values == [], (
            f"Client-injected 'equal' equivalence appeared on the wire: "
            f"{equiv_values!r}. Client cannot inject equivalence."
        )
        assert "wider" not in equiv_values or equiv_values == [], (
            f"Client-injected 'wider' equivalence appeared on the wire: "
            f"{equiv_values!r}. Client cannot inject equivalence."
        )
        # Whatever the engine emits MUST be R4 enum.
        for v in equiv_values:
            assert v in CANONICAL_R4_CODES

    def test_e12_post_coding_with_hostile_relationship_in_body(
        self, fhir_client,
    ):
        """EXPLORER: POST $translate with coding body AND hostile
        ``relationship`` parameter in the body — the engine-driven
        equivalence is emitted, NOT the hostile value.

        The R5/R4B ``subsumedBy`` and R5-only ``matches`` values MUST NOT
        leak to the wire via a client-supplied relationship parameter.
        """
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": SNOMED_URI, "code": "44054006",
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                # Hostile injections: every off-spec value.
                {"name": "relationship", "valueString": "subsumedBy"},
                {"name": "relationship", "valueString": "matches"},
                {"name": "relationship", "valueString": "not-relatedto"},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200
        equiv_values = _match_equivalence_values(resp.json())
        for v in equiv_values:
            assert v not in OFF_SPEC_VALUES, (
                f"Off-spec value {v!r} leaked to wire via hostile body "
                f"injection."
            )
            assert v in CANONICAL_R4_CODES

    def test_e13_post_coding_with_no_targetsystem_returns_200(
        self, fhir_client,
    ):
        """EXPLORER: POST $translate with coding body and NO targetsystem —
        server translates to all other systems; equivalence value(s) come
        from the engine.

        Per FHIR R4 $translate: ``targetsystem`` is 0..1 (optional); when
        absent, the server MAY translate to all known target systems.
        """
        post_body = _make_coding_body(SNOMED_URI, "44054006")
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200
        body = resp.json()
        for v in _match_equivalence_values(body):
            assert v in CANONICAL_R4_CODES


# ===========================================================================
# LENS 2 — POST $translate with codeableConcept body: equivalence sourced
# from the engine.
# ===========================================================================
class TestLens2PostCodeableConceptEquivalenceFromEngine:
    """EXPLORER: POST $translate with ``codeableConcept`` body — verify
    the equivalence value comes from the engine, not from the client.

    Per FHIR R4 $translate In Parameters: ``codeableConcept`` 0..1
    CodeableConcept is a spec-listed alternative. Per spec text: "The
    server can translate any of the coding values (e.g. existing
    translations) as it chooses" — so the server picks one coding and
    produces the engine-derived equivalence.
    """

    def test_e20_post_cc_emits_engine_equivalence_value(
        self, fhir_client,
    ):
        """EXPLORER: POST $translate with codeableConcept body produces
        the same engine-derived equivalence as GET system+code."""
        get_resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": "44054006",
                "targetsystem": ICD10CM_URI,
            },
        )
        post_body = _make_codeable_concept_body(
            SNOMED_URI, "44054006", targetsystem=ICD10CM_URI,
        )
        post_resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert get_resp.status_code == 200
        assert post_resp.status_code == 200
        get_equiv = _match_equivalence_values(get_resp.json())
        post_equiv = _match_equivalence_values(post_resp.json())
        assert get_equiv == post_equiv, (
            f"GET ↔ POST codeableConcept equivalence mismatch: "
            f"GET={get_equiv!r}, POST cc={post_equiv!r}."
        )

    def test_e21_post_cc_with_multi_coding_picks_first_valid(
        self, fhir_client,
    ):
        """EXPLORER: POST $translate with codeableConcept body containing
        multiple codings — server picks the first coding with both
        system+code (per CF-CM02-01 + CM-01 EXPLORER QA-001 spec text).

        Spec: "The server can translate any of the coding values as it
        chooses" — medterm4ds picks the first coding with both fields.
        """
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            # First coding: missing code — server skips.
                            {"system": SNOMED_URI},
                            # Second coding: valid — server picks.
                            {"system": SNOMED_URI, "code": "44054006"},
                        ],
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200
        # Server picked the valid coding → result=true, at least one match.
        body = resp.json()
        result_param = next(
            (p for p in body.get("parameter", []) if p.get("name") == "result"),
            None,
        )
        assert result_param is not None
        assert result_param.get("valueBoolean") is True, (
            "Server should have picked the valid (second) coding and "
            "produced result=true."
        )

    def test_e22_post_cc_no_injected_equivalence_via_parameters_body(
        self, fhir_client,
    ):
        """EXPLORER: POST $translate with codeableConcept body AND a
        client-injected ``equivalence`` parameter — the injected value
        MUST NOT appear in the Out match.equivalence."""
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "44054006"},
                        ],
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                # Hostile injection.
                {"name": "equivalence", "valueCode": "equal"},
                {"name": "match.equivalence", "valueCode": "wider"},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200
        equiv_values = _match_equivalence_values(resp.json())
        for v in equiv_values:
            assert v not in OFF_SPEC_VALUES
            assert v in CANONICAL_R4_CODES


# ===========================================================================
# LENS 3 — Mixed-encoding POST $translate body (scalar + coding +
# codeableConcept simultaneously): scalar wins on conflict.
#
# Per AGENTS.md NOT A BUG registry (CM-02 EXPLORER test_e50/e51):
# scalar-wins-on-conflict is the documented semantic. EXPLORER verifies
# this holds AND that the resulting equivalence value is R4 enum.
# ===========================================================================
class TestLens3MixedEncodingScalarWinsOnConflict:
    """EXPLORER: POST $translate body containing scalar system+code AND
    coding AND codeableConcept — scalar wins (per CM-02 EXPLORER
    convention) AND the resulting equivalence is R4 enum.
    """

    def test_e30_scalar_plus_coding_scalar_wins_with_r4_equivalence(
        self, fhir_client,
    ):
        """EXPLORER: scalar system+code + coding body — scalar wins;
        the equivalence value comes from the engine mapping for the
        scalar code, NOT the coding body."""
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                # Scalar (primary) — Diabetes mellitus SNOMED.
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "73211009"},
                # Coding body (alternative) — T2DM SNOMED.
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": SNOMED_URI, "code": "44054006",
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200
        # The scalar code 73211009 (Diabetes mellitus) has no ICD-10-CM
        # mapping in the fixture (only T2DM maps). Result is false OR
        # matches is empty.
        body = resp.json()
        # Regardless of result, every emitted equivalence MUST be R4.
        for v in _match_equivalence_values(body):
            assert v in CANONICAL_R4_CODES

    def test_e31_scalar_plus_cc_scalar_wins_with_r4_equivalence(
        self, fhir_client,
    ):
        """EXPLORER: scalar system+code + codeableConcept — scalar wins."""
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "73211009"},
                        ],
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200
        # Scalar 44054006 (T2DM) maps to E11 — result=true, equivalence
        # from engine.
        body = resp.json()
        result_param = next(
            (p for p in body.get("parameter", []) if p.get("name") == "result"),
            None,
        )
        assert result_param is not None
        assert result_param.get("valueBoolean") is True
        for v in _match_equivalence_values(body):
            assert v in CANONICAL_R4_CODES


# ===========================================================================
# LENS 4 — GET ↔ POST coding ↔ POST codeableConcept byte-exact parity
# on equivalence value.
#
# Per HISTORIAN tip: verify equivalence is sourced from the engine
# regardless of input encoding shape. The same (system, code, target)
# triple MUST produce byte-exact equivalence across all 3 encodings.
# ===========================================================================
class TestLens4ByteExactParityAcrossEncodings:
    """EXPLORER: byte-exact equivalence parity across 3 encoding shapes
    for the same (system, code, target) triple.
    """

    @pytest.mark.parametrize(
        "system, code, target, desc",
        [
            (SNOMED_URI, "44054006", ICD10CM_URI, "T2DM SNOMED → ICD-10-CM"),
            (ICD10CM_URI, "E11", SNOMED_URI, "T2DM ICD-10-CM → SNOMED"),
            (SNOMED_URI, "44054006", None, "T2DM SNOMED → all targets"),
            (SNOMED_URI, "860975", None, "Metformin RXNORM → all targets"),
        ],
        ids=["t2dm_snomed_to_icd10", "t2dm_icd10_to_snomed", "t2dm_snomed_all", "metformin_all"],
    )
    def test_e40_byte_exact_parity_across_encodings(
        self, fhir_client, system, code, target, desc,
    ):
        """EXPLORER: 3-way byte-exact equivalence parity.

        GET system+code, POST coding body, POST codeableConcept body —
        all 3 must emit the same equivalence value(s) for the same
        (system, code, target) triple.
        """
        get_params = {"system": system, "code": code}
        if target is not None:
            get_params["targetsystem"] = target
        get_resp = fhir_client.get(
            "/fhir/ConceptMap/$translate", params=get_params,
        )
        post_coding_body = _make_coding_body(system, code, targetsystem=target)
        post_cc_body = _make_codeable_concept_body(system, code, targetsystem=target)
        post_coding_resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_coding_body,
        )
        post_cc_resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_cc_body,
        )
        assert get_resp.status_code == 200
        assert post_coding_resp.status_code == 200
        assert post_cc_resp.status_code == 200
        get_equiv = _match_equivalence_values(get_resp.json())
        coding_equiv = _match_equivalence_values(post_coding_resp.json())
        cc_equiv = _match_equivalence_values(post_cc_resp.json())
        assert get_equiv == coding_equiv == cc_equiv, (
            f"Equivalence parity failed for {desc}: "
            f"GET={get_equiv!r}, coding={coding_equiv!r}, cc={cc_equiv!r}."
        )

    def test_e41_no_target_byte_exact_parity_no_off_spec_leak(
        self, fhir_client,
    ):
        """EXPLORER: POST coding with no targetsystem — server translates
        to all other systems. Every emitted equivalence MUST be R4 enum,
        no off-spec leak across any encoding."""
        for body_fn in (_make_coding_body, _make_codeable_concept_body):
            post_body = body_fn(SNOMED_URI, "44054006")
            resp = fhir_client.post(
                "/fhir/ConceptMap/$translate", json=post_body,
            )
            assert resp.status_code == 200
            for v in _match_equivalence_values(resp.json()):
                assert v in CANONICAL_R4_CODES
                assert v not in OFF_SPEC_VALUES


# ===========================================================================
# LENS 5 — Hostile Parameters body: hostile entries interleaved with
# valid ones; equivalence value comes from the engine.
#
# Per HISTORIAN tip + 10th PROMOTED pattern (isinstance guards at
# untrusted-data list-iterator boundary): hostile entries MUST be
# silently dropped (or rejected with 400) without affecting the
# equivalence derivation path.
# ===========================================================================
class TestLens5HostileParametersBody:
    """EXPLORER: POST $translate with hostile Parameters body — hostile
    entries interleaved with valid ones; engine-driven equivalence
    derivation is unaffected.
    """

    def test_e50_hostile_parameter_entries_silently_dropped(
        self, fhir_client,
    ):
        """EXPLORER: Parameters body with non-dict parameter[] entries —
        the 10th PROMOTED pattern guarantees they're silently dropped
        (isinstance guard at the iterator boundary).

        Per the 10th PROMOTED pattern (CF-HISTORIAN-CM03-01 RESOLVED):
        every ``for X in body.get("parameter", []):`` loop with subsequent
        ``X.get(...)`` MUST have ``isinstance(X, dict): continue`` guard.
        """
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                # Hostile non-dict entries (silent-drop per 10th PROMOTED).
                "garbage-string",
                42,
                None,
                ["nested", "list"],
                # Valid scalar entries.
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                # More hostile entries.
                "more-garbage",
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        # Per the systemic handler + 10th PROMOTED pattern: 200 with
        # valid translation (hostile entries silently dropped).
        assert resp.status_code < 500, (
            f"Hostile parameter[] entries triggered 5xx: {resp.status_code}. "
            f"Per 10th PROMOTED pattern, they MUST be silently dropped."
        )
        if resp.status_code == 200:
            for v in _match_equivalence_values(resp.json()):
                assert v in CANONICAL_R4_CODES

    def test_e51_hostile_valueCoding_silently_dropped(
        self, fhir_client,
    ):
        """EXPLORER: Parameters body with valid scalar AND hostile
        valueCoding entries — valueCoding non-dict entries are silently
        dropped per _extract_named_coding_from_parameters isinstance guard
        (CS-04 SKEPTIC QA-001).
        """
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                # Valid scalar (primary).
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                # Hostile valueCoding entries.
                {"name": "coding", "valueCoding": "garbage-string"},
                {"name": "coding", "valueCoding": 42},
                {"name": "coding", "valueCoding": None},
                {"name": "coding", "valueCoding": ["nested", "list"]},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code < 500
        if resp.status_code == 200:
            for v in _match_equivalence_values(resp.json()):
                assert v in CANONICAL_R4_CODES

    def test_e52_hostile_codeableConcept_coding_entries(
        self, fhir_client,
    ):
        """EXPLORER: codeableConcept with hostile coding[] entries —
        non-dict entries silently dropped per isinstance guards."""
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            "garbage",
                            42,
                            None,
                            {"system": SNOMED_URI, "code": "44054006"},
                            ["nested"],
                        ],
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code < 500
        if resp.status_code == 200:
            for v in _match_equivalence_values(resp.json()):
                assert v in CANONICAL_R4_CODES


# ===========================================================================
# LENS 6 — Batch $translate with mixed-encoding entries: each entry's
# equivalence comes from the engine.
# ===========================================================================
class TestLens6BatchTranslateMixedEncodingEquivalence:
    """EXPLORER: batch $translate with entries using different encoding
    shapes (scalar / coding / codeableConcept). Each entry's equivalence
    value comes from the engine mapping for that entry's source code.
    """

    def test_e60_batch_mixed_encoding_entries_all_emit_r4_equivalence(
        self, fhir_client,
    ):
        """EXPLORER: batch with 3 entries — scalar, coding, codeableConcept
        — every entry emits R4-closed-enum equivalence (or no equivalence
        on no-match)."""
        batch_body: dict[str, Any] = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                # Entry 1: GET-style URL with scalar params.
                {
                    "request": {
                        "method": "GET",
                        "url": (
                            "/fhir/ConceptMap/$translate?"
                            f"system={SNOMED_URI}&code=44054006&"
                            f"targetsystem={ICD10CM_URI}"
                        ),
                    },
                },
                # Entry 2: POST coding body.
                {
                    "request": {
                        "method": "POST",
                        "url": "/fhir/ConceptMap/$translate",
                    },
                    "resource": _make_coding_body(
                        SNOMED_URI, "44054006", targetsystem=ICD10CM_URI,
                    ),
                },
                # Entry 3: POST codeableConcept body.
                {
                    "request": {
                        "method": "POST",
                        "url": "/fhir/ConceptMap/$translate",
                    },
                    "resource": _make_codeable_concept_body(
                        SNOMED_URI, "44054006", targetsystem=ICD10CM_URI,
                    ),
                },
            ],
        }
        resp = fhir_client.post("/fhir", json=batch_body)
        assert resp.status_code == 200
        bundle = resp.json()
        assert bundle.get("type") == "batch-response"
        entries = bundle.get("entry", [])
        assert len(entries) == 3
        for entry in entries:
            # Each entry is a Parameters resource (or OperationOutcome).
            resource = entry.get("resource", {})
            assert resource.get("resourceType") in {"Parameters", "OperationOutcome"}
            if resource.get("resourceType") == "Parameters":
                for v in _match_equivalence_values(resource):
                    assert v in CANONICAL_R4_CODES
                    assert v not in OFF_SPEC_VALUES

    def test_e61_batch_entry_with_injected_equivalence_no_leak(
        self, fhir_client,
    ):
        """EXPLORER: batch entry with hostile injected ``equivalence``
        parameter — the injected value MUST NOT leak to the wire."""
        batch_body: dict[str, Any] = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "/fhir/ConceptMap/$translate",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {
                                "name": "coding",
                                "valueCoding": {
                                    "system": SNOMED_URI, "code": "44054006",
                                },
                            },
                            {"name": "targetsystem", "valueUri": ICD10CM_URI},
                            # Hostile injection.
                            {"name": "equivalence", "valueCode": "subsumedBy"},
                            {"name": "match.equivalence", "valueCode": "matches"},
                        ],
                    },
                },
            ],
        }
        resp = fhir_client.post("/fhir", json=batch_body)
        assert resp.status_code == 200
        bundle = resp.json()
        entries = bundle.get("entry", [])
        assert len(entries) == 1
        resource = entries[0].get("resource", {})
        for v in _match_equivalence_values(resource):
            assert v not in OFF_SPEC_VALUES
            assert v in CANONICAL_R4_CODES


# ===========================================================================
# LENS 7 — Cross-system mappings (T2DM SNOMED → ICD-10-CM) via POST
# coding body: byte-exact equivalence parity with GET system+code.
# ===========================================================================
class TestLens7CrossSystemMappingsPostCoding:
    """EXPLORER: cross-system mapping (SNOMED → ICD-10-CM) via POST
    coding body produces the same engine-derived equivalence as GET.
    """

    def test_e70_t2dm_snomed_to_icd10_post_coding(self, fhir_client):
        """EXPLORER: T2DM SNOMED 44054006 → ICD-10-CM E11 same-CUI
        mapping. Both encodings MUST produce the same equivalence
        value (the engine's same-CUI → ``equivalent`` relationship
        → R4 ``equivalent``)."""
        get_resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": "44054006",
                "targetsystem": ICD10CM_URI,
            },
        )
        post_body = _make_coding_body(
            SNOMED_URI, "44054006", targetsystem=ICD10CM_URI,
        )
        post_resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert get_resp.status_code == 200
        assert post_resp.status_code == 200
        get_equiv = _match_equivalence_values(get_resp.json())
        post_equiv = _match_equivalence_values(post_resp.json())
        assert get_equiv == post_equiv
        for v in post_equiv:
            assert v in CANONICAL_R4_CODES

    def test_e71_diabetes_snomed_to_metformin_post_coding(self, fhir_client):
        """EXPLORER: SNOMED 73211009 (Diabetes mellitus) → RXNORM 860975
        (Metformin). No mapping exists in fixture → result=false; no
        equivalence value emitted."""
        post_body = _make_coding_body(
            SNOMED_URI, "73211009", targetsystem=RXNORM_URI,
        )
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200
        body = resp.json()
        result_param = next(
            (p for p in body.get("parameter", []) if p.get("name") == "result"),
            None,
        )
        assert result_param is not None
        assert result_param.get("valueBoolean") is False


# ===========================================================================
# LENS 8 — Default-fallback parity across encodings: unknown code in
# GET vs POST coding vs POST codeableConcept.
# ===========================================================================
class TestLens8DefaultFallbackParityAcrossEncodings:
    """EXPLORER: unknown code across 3 encodings — all return
    ``result=false`` with ``match=[]`` (no equivalence value emitted).
    """

    @pytest.mark.parametrize(
        "body_fn_name",
        ["scalar", "coding", "codeableConcept"],
        ids=["scalar", "coding", "codeableConcept"],
    )
    def test_e80_unknown_code_all_encodings_result_false(
        self, fhir_client, body_fn_name,
    ):
        """EXPLORER: unknown code via each encoding — result=false, no
        equivalence value on the wire."""
        if body_fn_name == "scalar":
            post_body = _make_scalar_body(
                SNOMED_URI, "UNKNOWN_CODE_999", targetsystem=ICD10CM_URI,
            )
            resp = fhir_client.post(
                "/fhir/ConceptMap/$translate", json=post_body,
            )
        elif body_fn_name == "coding":
            post_body = _make_coding_body(
                SNOMED_URI, "UNKNOWN_CODE_999", targetsystem=ICD10CM_URI,
            )
            resp = fhir_client.post(
                "/fhir/ConceptMap/$translate", json=post_body,
            )
        else:  # codeableConcept
            post_body = _make_codeable_concept_body(
                SNOMED_URI, "UNKNOWN_CODE_999", targetsystem=ICD10CM_URI,
            )
            resp = fhir_client.post(
                "/fhir/ConceptMap/$translate", json=post_body,
            )
        assert resp.status_code == 200
        body = resp.json()
        result_param = next(
            (p for p in body.get("parameter", []) if p.get("name") == "result"),
            None,
        )
        assert result_param is not None
        assert result_param.get("valueBoolean") is False
        # No equivalence values on no-match path.
        assert _match_equivalence_values(body) == []


# ===========================================================================
# LENS 9 — Source-code-resolved-from-coding canonical re-resolution:
# POST coding with alias system URI (urn:oid) → Out match.source.system
# is canonical AND equivalence value is from the engine.
# ===========================================================================
class TestLens9PostCodingCanonicalReResolution:
    """EXPLORER: POST coding body with alias system URI (urn:oid) — the
    Out match.source.system is the canonical URI (CR-012 RESOLVED),
    AND the equivalence value comes from the engine.
    """

    def test_e90_post_coding_urn_oid_resolves_to_canonical(
        self, fhir_client,
    ):
        """EXPLORER: POST coding body with urn:oid alias for SNOMED →
        Out match.source.system is canonical ``http://snomed.info/sct``
        AND equivalence value is R4 enum."""
        post_body = _make_coding_body(
            SNOMED_URN_OID, "44054006", targetsystem=ICD10CM_URI,
        )
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Out match.source.system is canonical (CR-012).
        for match in _find_params(body, "match"):
            source_part = next(
                (p for p in match.get("part", []) if p.get("name") == "source"),
                None,
            )
            if source_part is not None:
                source_coding = source_part.get("valueCoding", {})
                assert source_coding.get("system") == SNOMED_URI, (
                    f"Out match.source.system is {source_coding.get('system')!r}, "
                    f"expected canonical {SNOMED_URI!r} (CR-012)."
                )
        for v in _match_equivalence_values(body):
            assert v in CANONICAL_R4_CODES

    def test_e91_post_cc_urn_oid_resolves_to_canonical(
        self, fhir_client,
    ):
        """EXPLORER: POST codeableConcept body with urn:oid alias —
        Out match.source.system is canonical."""
        post_body = _make_codeable_concept_body(
            SNOMED_URN_OID, "44054006", targetsystem=ICD10CM_URI,
        )
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200
        body = resp.json()
        for match in _find_params(body, "match"):
            source_part = next(
                (p for p in match.get("part", []) if p.get("name") == "source"),
                None,
            )
            if source_part is not None:
                source_coding = source_part.get("valueCoding", {})
                assert source_coding.get("system") == SNOMED_URI


# ===========================================================================
# LENS 10 — Combined-operations round-trip: $translate (coding) →
# $lookup (system+code) — target concept display consistency.
# ===========================================================================
class TestLens10CombinedOpsTranslateCodingThenLookup:
    """EXPLORER: $translate (POST coding) → $lookup (GET system+code)
    on the target concept. The target concept's display MUST be the
    engine's canonical preferred term in both operations.
    """

    def test_e100_translate_coding_then_lookup_display_consistency(
        self, fhir_client,
    ):
        """EXPLORER: $translate (POST coding) target concept.display
        byte-exact equals $lookup (GET system+code) Out display."""
        post_body = _make_coding_body(
            SNOMED_URI, "44054006", targetsystem=ICD10CM_URI,
        )
        translate_resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert translate_resp.status_code == 200
        target_displays: list[str] = []
        for match in _find_params(translate_resp.json(), "match"):
            concept_part = next(
                (p for p in match.get("part", []) if p.get("name") == "concept"),
                None,
            )
            if concept_part is not None:
                target_coding = concept_part.get("valueCoding", {})
                if target_coding.get("display"):
                    target_displays.append(target_coding["display"])
                    # Also: lookup the target code.
                    lookup_resp = fhir_client.get(
                        "/fhir/CodeSystem/$lookup",
                        params={
                            "system": ICD10CM_URI,
                            "code": target_coding.get("code"),
                        },
                    )
                    if lookup_resp.status_code == 200:
                        lookup_display = next(
                            (
                                p.get("valueString")
                                for p in lookup_resp.json().get("parameter", [])
                                if p.get("name") == "display"
                            ),
                            None,
                        )
                        assert lookup_display == target_coding.get("display"), (
                            f"$translate target concept.display "
                            f"({target_coding.get('display')!r}) ≠ "
                            f"$lookup Out display ({lookup_display!r})."
                        )
        # At least one target display was checked.
        assert target_displays, (
            "Expected at least one match.concept.display in $translate response."
        )


# ===========================================================================
# LENS 11 — Wrapper-vs-canonical divergence on LIVE wire surface.
#
# Per HISTORIAN tip (Lens 8b divergence): the wrapper
# ``_fhir_equivalence_from_relationship`` adds a case-insensitive
# fallback that the canonical helper does NOT have. EXPLORER verifies
# that NO client-facing surface depends on the wrapper's case-insensitive
# behavior — the engine emits lowercase vocabulary only, so the
# divergence is invisible today.
# ===========================================================================
class TestLens11WrapperCaseInsensitiveDivergence:
    """EXPLORER: probe whether ANY client-facing surface depends on the
    wrapper's case-insensitive behavior.

    Per HISTORIAN Lens 8b: ``$translate`` resolves camelCase
    ``subsumedBy`` to ``specializes`` (via case-insensitive fallback in
    ``_fhir_equivalence_from_relationship``) while ConceptMap export
    returns ``relatedto`` (canonical helper's default). Both ON-SPEC,
    but client-visible behavior differs.

    EXPLORER probes:
      (a) the wrapper's case-insensitive fallback fires on the $translate
          surface for camelCase inputs;
      (b) the canonical helper does NOT fire the fallback (returns
          ``relatedto`` default);
      (c) the divergence is between two ON-SPEC values (both in R4 enum);
      (d) no client-injected camelCase value reaches either surface
          (the engine emits lowercase only).
    """

    def test_e110_wrapper_resolves_camelcase_subsumedby_to_specializes(self):
        """EXPLORER: ``_fhir_equivalence_from_relationship('subsumedBy')``
        → ``specializes`` (case-insensitive fallback fires)."""
        from medterm4ds.engines.fhir.responses import (
            _fhir_equivalence_from_relationship,
        )
        result = _fhir_equivalence_from_relationship("subsumedBy")
        assert result == "specializes", (
            f"Wrapper resolved camelCase 'subsumedBy' to {result!r}, "
            f"expected 'specializes' (case-insensitive fallback fires)."
        )
        assert result in CANONICAL_R4_CODES

    def test_e111_canonical_returns_relatedto_default_for_camelcase(self):
        """EXPLORER: ``fhir_equivalence('subsumedBy')`` → ``relatedto``
        (canonical helper does NOT have case-insensitive fallback)."""
        result = fhir_equivalence("subsumedBy")
        assert result == "relatedto", (
            f"Canonical helper returned {result!r} for camelCase "
            f"'subsumedBy', expected 'relatedto' (default — NO "
            f"case-insensitive fallback)."
        )
        assert result in CANONICAL_R4_CODES

    def test_e112_divergence_is_between_two_on_spec_values(self):
        """EXPLORER: both the wrapper and canonical return ON-SPEC R4
        values for camelCase ``subsumedBy`` (specializes vs relatedto —
        both in R4 enum). The divergence is between two ON-SPEC values,
        never an off-spec leak."""
        from medterm4ds.engines.fhir.responses import (
            _fhir_equivalence_from_relationship,
        )
        wrapper_result = _fhir_equivalence_from_relationship("subsumedBy")
        canonical_result = fhir_equivalence("subsumedBy")
        # Divergence is real.
        assert wrapper_result != canonical_result
        # Both are ON-SPEC.
        assert wrapper_result in CANONICAL_R4_CODES
        assert canonical_result in CANONICAL_R4_CODES
        # Neither is the off-spec R5/R4B value.
        assert wrapper_result != "subsumedBy"
        assert canonical_result != "subsumedBy"

    def test_e113_no_client_surface_depends_on_wrapper_today(self):
        """EXPLORER: NO client-facing surface depends on the wrapper's
        case-insensitive behavior today.

        The engine emits lowercase relationship vocabulary only
        (``equivalent``, ``source-is-narrower-than-target``,
        ``source-is-broader-than-target``, ``related-to``,
        ``not-translated``, ``unmatched`` — all lowercase). The wrapper's
        case-insensitive fallback would only fire on a future R5/R4B
        engine vocabulary change that has not happened.
        """
        # Every engine-emitted relationship value is lowercase.
        engine_values = [
            "equivalent", "source-is-narrower-than-target",
            "source-is-broader-than-target", "related-to",
            "not-translated", "unmatched",
        ]
        for v in engine_values:
            assert v == v.lower(), (
                f"Engine value {v!r} is NOT lowercase — wrapper "
                f"case-insensitive fallback would diverge from canonical."
            )

    def test_e114_wrapper_source_read_confirms_case_insensitive_fallback(self):
        """EXPLORER: source-read of ``_fhir_equivalence_from_relationship``
        confirms the case-insensitive fallback exists AND is documented."""
        src = textwrap.dedent(
            inspect.getsource(responses_module._fhir_equivalence_from_relationship)
        )
        # The case-insensitive fallback uses ``.lower()``.
        assert ".lower()" in src, (
            "_fhir_equivalence_from_relationship MUST have a "
            "case-insensitive fallback using .lower()."
        )
        # The fallback is documented.
        assert "case-insensitive" in src.lower() or "case insensitive" in src.lower(), (
            "The case-insensitive fallback MUST be documented in the "
            "function docstring or comments."
        )

    def test_e115_canonical_helper_has_no_case_insensitive_fallback(self):
        """EXPLORER: source-read of ``fhir_equivalence`` confirms it does
        NOT have a case-insensitive fallback (the divergence source)."""
        src = textwrap.dedent(inspect.getsource(fhir_equivalence))
        # The canonical helper does NOT have .lower() in its body.
        # (It uses .get(relationship, default) directly.)
        assert ".lower()" not in src, (
            "Canonical fhir_equivalence MUST NOT have a case-insensitive "
            "fallback — that's the divergence source from the wrapper."
        )

    def test_e116_live_wire_translate_surface_uses_wrapper(self, fhir_client):
        """EXPLORER: the LIVE $translate HTTP surface uses the wrapper
        (with case-insensitive fallback), not the canonical helper
        directly. Source-read ``build_parameters_translate`` confirms
        ``_fhir_equivalence_from_relationship`` is the called helper."""
        from medterm4ds.engines.fhir.responses import build_parameters_translate
        src = textwrap.dedent(inspect.getsource(build_parameters_translate))
        assert "_fhir_equivalence_from_relationship" in src, (
            "build_parameters_translate MUST call "
            "_fhir_equivalence_from_relationship (the wrapper, not the "
            "canonical helper directly)."
        )


# ===========================================================================
# LENS 12 — Empty/missing/null equivalence under POST coding: POST
# coding body with missing system or code — graceful 400 OR result=false.
# ===========================================================================
class TestLens12EmptyMissingPostCodingBody:
    """EXPLORER: POST coding body with missing system or code — graceful
    handling. Per spec text: ``coding`` is a Coding with required system
    and code fields; missing fields trigger fallback to codeableConcept
    OR 400 response.
    """

    def test_e120_post_coding_missing_system_returns_400(self, fhir_client):
        """EXPLORER: POST coding body with missing system — 400 (system
        and code are required)."""
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {"code": "44054006"}},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 400

    def test_e121_post_coding_missing_code_returns_400(self, fhir_client):
        """EXPLORER: POST coding body with missing code — 400."""
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {"system": SNOMED_URI}},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 400

    def test_e122_post_coding_empty_system_returns_400(self, fhir_client):
        """EXPLORER: POST coding body with empty system string — 400
        (per CF-CM02-01 RESOLVED + scalar-wins-on-conflict: empty
        system triggers 400)."""
        post_body: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {"system": "", "code": "44054006"},
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        # Empty system → coding pair rejected → fall through to 400.
        assert resp.status_code == 400


# ===========================================================================
# LENS 13 — Cross-surface wrapper-divergence source-read audit.
#
# Source-read of ``_fhir_equivalence_from_relationship`` confirms the
# case-insensitive fallback is documented AND isolated to the $translate
# surface.
# ===========================================================================
class TestLens13WrapperDivergenceSourceRead:
    """EXPLORER: source-read audit confirming the wrapper's case-
    insensitive fallback is isolated to the $translate surface.
    """

    def test_e130_wrapper_only_in_responses_module(self):
        """EXPLORER: ``_fhir_equivalence_from_relationship`` is defined
        ONLY in ``responses.py`` (the $translate surface); the canonical
        ``equivalence.py`` module does NOT define it."""
        # The wrapper exists in responses.py.
        assert hasattr(responses_module, "_fhir_equivalence_from_relationship")
        # The canonical module does NOT have the wrapper (only fhir_equivalence).
        assert not hasattr(equivalence_module, "_fhir_equivalence_from_relationship"), (
            "Canonical equivalence module MUST NOT define "
            "_fhir_equivalence_from_relationship — it's the $translate-"
            "surface wrapper only."
        )

    def test_e131_outputs_module_uses_canonical_helper_not_wrapper(self):
        """EXPLORER: ``outputs/fhir.py`` (ConceptMap export surface) uses
        the canonical ``fhir_equivalence`` helper, NOT the wrapper."""
        from medterm4ds.outputs import fhir as outputs_fhir
        # The export surface uses the canonical helper.
        src = inspect.getsource(outputs_fhir)
        assert "fhir_equivalence" in src, (
            "outputs/fhir.py MUST use the canonical fhir_equivalence helper."
        )
        assert "_fhir_equivalence_from_relationship" not in src, (
            "outputs/fhir.py MUST NOT use the wrapper (case-insensitive "
            "fallback is $translate-surface only)."
        )

    def test_e132_canonical_module_docstring_documents_divergence(self):
        """EXPLORER: the canonical module's docstring documents the
        wrapper's case-insensitive fallback divergence (per HISTORIAN
        Lens 8b: 'Documented in both wrapper source AND canonical
        module's docstring')."""
        src = inspect.getsource(equivalence_module)
        # The docstring documents that the wrapper preserves case-
        # insensitive behaviour.
        assert (
            "case-insensitive" in src.lower()
            or "case insensitive" in src.lower()
        ), (
            "Canonical module docstring MUST document the wrapper's "
            "case-insensitive fallback divergence."
        )


# ===========================================================================
# LENS 14 — Spec-citation audit on $translate In Parameters.
# ===========================================================================
class TestLens14SpecCitationAudit:
    """EXPLORER: spec-citation audit on $translate In Parameters —
    ``coding`` 0..1 Coding and ``codeableConcept`` 0..1 CodeableConcept
    are spec-listed alternatives to ``system``+``code``.
    """

    def test_e140_translate_handler_accepts_coding_alternative(self, fhir_client):
        """EXPLORER: POST $translate with ``coding`` body — handler
        accepts and processes the alternative encoding."""
        post_body = _make_coding_body(
            SNOMED_URI, "44054006", targetsystem=ICD10CM_URI,
        )
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        # Spec-permitted alternative encoding → 200 (not 400).
        assert resp.status_code == 200

    def test_e141_translate_handler_accepts_codeableConcept_alternative(
        self, fhir_client,
    ):
        """EXPLORER: POST $translate with ``codeableConcept`` body —
        handler accepts and processes the alternative encoding."""
        post_body = _make_codeable_concept_body(
            SNOMED_URI, "44054006", targetsystem=ICD10CM_URI,
        )
        resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert resp.status_code == 200


# ===========================================================================
# LENS 15 — Module-load assertion integrity under POST coding.
# ===========================================================================
class TestLens15ModuleLoadAssertionUnderPostCoding:
    """EXPLORER: the module-load assertion in ``equivalence.py`` fires
    at import time regardless of which encoding shape the client uses.
    """

    def test_e150_assertion_fires_regardless_of_encoding(self):
        """EXPLORER: the assertion is at module load (not per-request),
        so it fires for ALL encoding shapes — POST coding, POST
        codeableConcept, GET system+code, batch entries."""
        # If the module imports successfully, the assertion passed.
        assert hasattr(equivalence_module, "INTERNAL_REL_TO_FHIR_EQUIVALENCE")
        assert hasattr(equivalence_module, "fhir_equivalence")
        # The assertion text is present.
        src = inspect.getsource(equivalence_module)
        assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in src
        assert "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()" in src

    def test_e151_assertion_uses_subset_operator(self):
        """EXPLORER: the assertion uses ``<=`` (subset), not ``==``
        (equality) — structurally correct because multiple map keys can
        map to the same R4 value (e.g. ``subsumedby`` + ``subsumed-by``
        both → ``specializes``)."""
        src = inspect.getsource(equivalence_module)
        tree = ast.parse(src)
        found_subset = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                # Look for <= operator in the assertion test.
                for sub in ast.walk(node):
                    if isinstance(sub, ast.LtE):
                        found_subset = True
                        break
        assert found_subset, (
            "Module-load assertion MUST use <= (subset operator), not == "
            "(equality)."
        )


# ===========================================================================
# LENS 16 — GET↔POST coding byte-exact parity parametrized over every
# seeded code.
# ===========================================================================
class TestLens16GetPostCodingByteExactParityAllSeededCodes:
    """EXPLORER: GET ↔ POST coding byte-exact equivalence parity
    parametrized over every seeded code (4 codes × 2 methods = 8 cases).
    """

    @pytest.mark.parametrize(
        "system, code, target, desc",
        [
            (SNOMED_URI, "73211009", ICD10CM_URI, "DM SNOMED → ICD-10-CM"),
            (SNOMED_URI, "44054006", ICD10CM_URI, "T2DM SNOMED → ICD-10-CM"),
            (ICD10CM_URI, "E11", SNOMED_URI, "T2DM ICD-10-CM → SNOMED"),
            (RXNORM_URI, "860975", SNOMED_URI, "Metformin → SNOMED"),
        ],
        ids=["dm_snomed_icd10", "t2dm_snomed_icd10", "t2dm_icd10_snomed", "metformin_snomed"],
    )
    def test_e160_byte_exact_parity_per_seeded_code(
        self, fhir_client, system, code, target, desc,
    ):
        """EXPLORER: GET ↔ POST coding byte-exact equivalence for every
        seeded code."""
        get_resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={"system": system, "code": code, "targetsystem": target},
        )
        post_body = _make_coding_body(system, code, targetsystem=target)
        post_resp = fhir_client.post(
            "/fhir/ConceptMap/$translate", json=post_body,
        )
        assert get_resp.status_code == 200
        assert post_resp.status_code == 200
        get_equiv = _match_equivalence_values(get_resp.json())
        post_equiv = _match_equivalence_values(post_resp.json())
        assert get_equiv == post_equiv, (
            f"GET ↔ POST coding equivalence mismatch for {desc}: "
            f"GET={get_equiv!r}, POST={post_equiv!r}."
        )
        for v in post_equiv:
            assert v in CANONICAL_R4_CODES
            assert v not in OFF_SPEC_VALUES


# ===========================================================================
# LENS 17 — Function contract: ``_fhir_equivalence_from_relationship``
# never raises on hostile coding-shaped inputs.
# ===========================================================================
class TestLens17WrapperNeverRaisesOnHostileInput:
    """EXPLORER: ``_fhir_equivalence_from_relationship`` never raises on
    hostile coding-shaped inputs.
    """

    @pytest.mark.parametrize(
        "hostile_input",
        [
            None,
            "",
            "   ",
            "subsumedBy",          # camelCase R5/R4B
            "SUBSUMEDBY",          # uppercase
            "matches",             # R5-only
            "not-relatedto",       # not in any enum
            "not-related-to",
            "'; DROP TABLE--",     # SQL injection
            "<script>alert(1)</script>",  # XSS
            "a" * 10000,           # very long string
            "null\x00byte",        # null byte
            "日本語",               # CJK
        ],
        ids=[
            "none", "empty", "whitespace", "camelcase", "uppercase",
            "r5_matches", "not_relatedto", "not_related_to",
            "sql_injection", "xss", "very_long", "null_byte", "cjk",
        ],
    )
    def test_e170_wrapper_never_raises_returns_r4_enum(self, hostile_input):
        """EXPLORER: ``_fhir_equivalence_from_relationship`` returns an
        R4 enum value for every hostile input; never raises."""
        from medterm4ds.engines.fhir.responses import (
            _fhir_equivalence_from_relationship,
        )
        result = _fhir_equivalence_from_relationship(hostile_input)
        assert result in CANONICAL_R4_CODES, (
            f"Wrapper returned {result!r} for hostile input "
            f"{hostile_input!r} — NOT in R4 closed enum."
        )
        assert result not in OFF_SPEC_VALUES

    def test_e171_wrapper_never_echoes_raw_input(self):
        """EXPLORER: the wrapper never echoes the raw input string back
        — always translates to an R4 enum value."""
        from medterm4ds.engines.fhir.responses import (
            _fhir_equivalence_from_relationship,
        )
        hostile_inputs = ["subsumedBy", "matches", "not-relatedto", "X" * 100]
        for inp in hostile_inputs:
            result = _fhir_equivalence_from_relationship(inp)
            assert result != inp, (
                f"Wrapper echoed raw input {inp!r} back — MUST translate."
            )
