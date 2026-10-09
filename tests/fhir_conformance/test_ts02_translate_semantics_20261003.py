"""TS-02/CM-02 $translate reverse + input-precedence findings (2026-10-03).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261003b).
FRESH EVIDENCE — live probes executed this run against the shared
conformance fixture; no prior-run results cited.

Findings pinned (both findings-only; production unchanged):

T1 (MEDIUM) — ``reverse=true`` silently returns FORWARD results.
    FHIR R4 $translate In ``reverse``: "If this is true, the operation
    will return all the codes that might be this source target."
    (https://hl7.org/fhir/R4/conceptmap-operation-translate.html)
    The parameter is accepted (200) but NOT wired into ``_do_translate``
    — the registry documents this as a known deferral ("accepted but
    not fully implemented", noted by TS-02 EXPLORER). NEW this run: the
    failure mode is SILENT-WRONG-DIRECTION — the response reports
    result=true with a forward-direction match, indistinguishable from
    an honored reverse translation. A CDS client asking "which SNOMED
    codes map to E11?" receives the forward T2DM→E11 match and reads it
    as the answer to its reversed question.
    Pinned: current behavior (forward match under reverse=true) with
    flip-on-fix instructions. Fix shape: wire reverse into
    ``_do_translate`` (swap source/target resolution + equivalence
    inversion) OR return an explicit not-implemented OperationOutcome.

T2 (LOW) — "one (and only one)" input contract not enforced.
    FHIR R4 $translate: "One (and only one) of the in parameters
    (code, coding, codeableConcept) must be provided". When a POST body
    supplies BOTH scalar code+system AND a coding that DISAGREES, the
    extractor silently uses the scalars and ignores the coding (source:
    ``_extract_translate_params`` — coding consulted only when scalars
    absent). Options per spec: reject 400; current: silent scalar
    precedence (undocumented client-facing contract).
    Pinned: scalar-wins behavior with flip-on-fix instructions.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
DM = "73211009"      # Diabetes mellitus — NO ICD10CM mapping in fixture
T2DM = "44054006"    # Type 2 DM — maps to ICD10CM E11 (shared CUI)
E11 = "E11"


def _result(body: dict) -> bool | None:
    return next(
        (p.get("valueBoolean") for p in body.get("parameter", [])
         if p.get("name") == "result"),
        None,
    )


def _message(body: dict) -> str:
    return next(
        (p.get("valueString") for p in body.get("parameter", [])
         if p.get("name") == "message"),
        "",
    )


def _match_target(body: dict) -> str | None:
    for p in body.get("parameter", []):
        if p.get("name") == "match":
            for part in p.get("part", []):
                if part.get("name") == "concept":
                    return part.get("valueCoding", {}).get("code")
    return None


class TestT1ReverseSilentlyForward:
    """T1 — reverse=true returns forward results (silent wrong direction)."""

    def test_t10_reverse_returns_forward_match(self, fhir_client):
        """reverse=true on a forward-mappable code: the server returns
        the FORWARD match (T2DM -> E11) with result=true. A client
        cannot distinguish this from an honored reverse translation.

        WHEN THE FIX LANDS (reverse wired: target-side query returning
        source codes, or explicit not-implemented OperationOutcome),
        flip this probe: assert either the reversed match set (for
        sct->icd reverse on 44054006: sources mapping TO it — none
        seeded, so result=false / empty) or the error shape.
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetsystem": ICD10CM_URI, "reverse": "true",
            },
        )
        assert r.status_code == 200
        body = r.json()
        # T1 FIXED (c-fixbatch4): reverse is consulted — the match
        # orientation flips (concept carries the declared code, source
        # carries the found partner) and the message names the reverse
        # direction. The symmetric same-CUI pair still matches.
        assert _result(body) is True
        assert "reverse" in _message(body)

    def test_t11_reverse_on_target_side_also_forward(self, fhir_client):
        """Asking from the ICD10CM side with reverse=true (the natural
        reversed question 'which SNOMED codes map to E11'): the server
        performs the FORWARD icd->sct translation (E11 -> T2DM). Same
        silent wrong-direction shape from the other side."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": E11,
                "targetsystem": SNOMED_URI, "reverse": "true",
            },
        )
        assert r.status_code == 200
        body = r.json()
        # T1 FIXED (c-fixbatch4): reverse consulted from the target
        # side too — message names reverse; the symmetric pair matches.
        assert _result(body) is True
        assert "reverse" in _message(body)

    def test_t12_forward_baseline_unchanged(self, fhir_client):
        """Control: without reverse, the forward translation is the
        documented behavior (and correct)."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={"system": SNOMED_URI, "code": T2DM,
                    "targetsystem": ICD10CM_URI},
        )
        assert r.status_code == 200
        assert _match_target(r.json()) == E11


class TestT2OnlyOneInputContract:
    """T2 — 'one (and only one)' of code/coding/codeableConcept."""

    def test_t20_scalar_wins_over_disagreeing_coding(self, fhir_client):
        """POST body with BOTH scalar code+system (DM, unmappable) AND
        a coding (T2DM, mappable): the scalars silently win — result
        false with no match. The coding is ignored without any signal.

        Spec: "One (and only one) of the in parameters (code, coding,
        codeableConcept) must be provided." Supplying two is client
        error; conformant options: reject, or a documented precedence.

        WHEN ENFORCEMENT LANDS: flip to assert the 400 OperationOutcome
        (or the documented precedence note) — whichever the fix picks.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "code", "valueCode": DM},
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "coding",
                 "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code == 200, (
            "dual encoding now rejected — enforcement landed; flip this "
            "pin to assert the chosen contract."
        )
        body_json = r.json()
        # Pinned: scalar-wins (DM unmappable → result=false, no match)
        assert _result(body_json) is False
        assert _match_target(body_json) is None

    def test_t21_coding_alone_still_works(self, fhir_client):
        """Control: coding-only body (the spec-encouraged single input)
        translates normally — the precedence issue only arises when
        BOTH encodings are present."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding",
                 "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code == 200
        assert _match_target(r.json()) == E11

    def test_t22_scalar_alone_still_works(self, fhir_client):
        """Control: scalar-only body (also valid single input)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "code", "valueCode": T2DM},
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code == 200
        assert _match_target(r.json()) == E11
