"""CM-03 $closure mixed-batch semantics findings (2026-10-03).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261003d).
FRESH EVIDENCE — live probes executed this run against the shared
conformance fixture; no prior-run results cited.

Context: the $closure surface's known deviations are pinned (Out
`return` valueString token vs R4's 1..1 ConceptMap — CF-SKEPTIC-CM03-01;
unknown-SYSTEM rejection at 400 — QC-271, deliberate). This suite pins
the adjacent UNPROBED shapes:

C1 (MEDIUM) — one unknown CODE poisons the entire closure request.
    R4 $closure Out `return` is a ConceptMap of new entries whose ONLY
    allowed equivalences are "equal, specializes, subsumes and
    unmatched" (operation-conceptmap-closure.html). The existence of
    `unmatched` in the Out vocabulary means unknown/unmatched concepts
    are expected to be REPORTABLE per-concept, not fatal to the call.
    Current: a request carrying one valid concept (73211009) AND one
    unknown code (99999999) returns 400 for the WHOLE request — the
    valid concept's closure entries are silently lost. A client
    maintaining a closure table over user-supplied codings (e.g. free
    text coding in documents) must re-send concepts one-by-one to
    discover which single code killed the batch.
    Pinned: current whole-request-400 with flip-on-fix instructions.
    Fix shape: per-concept isolation (add known concepts to the
    closure; report unknown ones as `unmatched` entries in the Out
    ConceptMap, or at minimum an OperationOutcome listing ALL unknown
    codes while still serving the valid ones — the batch-isolation
    pattern TS-04 established for POST /fhir batch entries).

C2 (controls) — conformant behaviors re-verified fresh: happy-path
    token + incomplete=false shape, no-concepts 200 (In concept is
    0..*), empty-name 400, wrong-typed concept 400, GET 404
    (POST-only operation), unknown-system 400 (QC-271 held).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
DM = "73211009"
UNKNOWN_CODE = "99999999"


def _closure_body(name: str, concepts: list[dict]) -> dict:
    return {
        "resourceType": "Parameters",
        "parameter": (
            [{"name": "name", "valueString": name}]
            + [
                {"name": "concept", "valueCoding": c} for c in concepts
            ]
        ),
    }


class TestC1MixedBatchPoisoning:
    """C1 — one unknown code fails the whole closure request."""

    def test_c10_mixed_valid_plus_unknown_whole_request_400(
        self, fhir_client
    ):
        """Valid DM + unknown code in ONE request: whole request 400,
        valid concept's entries lost.

        WHEN THE FIX LANDS (per-concept isolation: serve the valid
        concepts AND report unknowns — as unmatched entries in the
        Out ConceptMap per the spec's equivalence vocabulary, or an
        OperationOutcome enumerating every unknown code alongside a
        successful partial closure), flip this probe to assert the
        partial-success shape.
        """
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_body(
                "mixed-c10",
                [
                    {"system": SNOMED_URI, "code": DM},
                    {"system": SNOMED_URI, "code": UNKNOWN_CODE},
                ],
            ),
        )
        assert r.status_code == 400, (
            "mixed batch no longer hard-fails — per-concept isolation "
            "landed; flip this pin to assert the partial shape."
        )
        body = r.json()
        assert body["resourceType"] == "OperationOutcome"
        # The diagnostic names the offending code (client can at least
        # identify the poison — but only the FIRST one).
        assert UNKNOWN_CODE in body["issue"][0]["diagnostics"]

    def test_c11_valid_only_still_succeeds(self, fhir_client):
        """Control: the identical request minus the unknown code
        succeeds — the 400 in c10 is caused solely by the unknown
        code, not the valid concept or request shape."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_body(
                "mixed-c11", [{"system": SNOMED_URI, "code": DM}]
            ),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["resourceType"] == "Parameters"

    def test_c12_two_unknowns_report_first_only(self, fhir_client):
        """Two unknown codes in one request: the diagnostic names only
        the first — a client cannot learn the full bad set without
        binary-search re-sends (strengthens the C1 fix rationale)."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_body(
                "mixed-c12",
                [
                    {"system": SNOMED_URI, "code": "11111111"},
                    {"system": SNOMED_URI, "code": "22222222"},
                ],
            ),
        )
        assert r.status_code == 400
        diag = r.json()["issue"][0]["diagnostics"]
        first_named = "11111111" in diag
        second_named = "22222222" in diag
        assert first_named, "first unknown code must be named"
        # Pinned: only the first unknown is named (current behavior).
        # When the fix enumerates ALL unknown codes, flip this to
        # assert second_named too.
        assert not second_named, (
            "both unknown codes now named — full enumeration landed; "
            "tighten this pin."
        )


class TestC2ClosureControls:
    """Controls: conformant/known-pinned shapes re-verified fresh."""

    def test_c20_happy_path_token_and_incomplete(self, fhir_client):
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_body(
                "ctl-happy",
                [{"system": SNOMED_URI, "code": DM}],
            ),
        )
        assert r.status_code == 200
        params = r.json()["parameter"]
        names = [p["name"] for p in params]
        assert "return" in names and "incomplete" in names
        ret = next(p for p in params if p["name"] == "return")
        # CF-SKEPTIC-CM03-01 pin: return is a string token today, NOT
        # the spec's 1..1 ConceptMap (already registry-pinned; this
        # control just re-verifies the current shape).
        assert isinstance(ret.get("valueString"), str)

    def test_c21_no_concepts_200(self, fhir_client):
        """In `concept` is 0..* — a name-only request is valid and
        returns the closure token (fresh re-verification)."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json={
                "resourceType": "Parameters",
                "parameter": [{"name": "name", "valueString": "ctl-empty"}],
            },
        )
        assert r.status_code == 200

    def test_c22_empty_name_400(self, fhir_client):
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_body("", [{"system": SNOMED_URI, "code": DM}]),
        )
        assert r.status_code == 400
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_c23_concept_wrong_type_400(self, fhir_client):
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "ctl-type"},
                    {"name": "concept", "valueString": "not-a-coding"},
                ],
            },
        )
        assert r.status_code == 400

    def test_c24_get_closure_404(self, fhir_client):
        """$closure is POST-only; GET must not route (404, not 405/500)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$closure", params={"name": "x"}
        )
        assert r.status_code == 404

    def test_c25_unknown_system_400_held(self, fhir_client):
        """QC-271 re-verification: unknown SYSTEM URI rejected at 400
        (deliberate, pinned by test_s32 — this control confirms it
        still holds after all subsequent work)."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_body(
                "ctl-qc271",
                [{"system": "http://example.org/nope", "code": "X1"}],
            ),
        )
        assert r.status_code == 400
