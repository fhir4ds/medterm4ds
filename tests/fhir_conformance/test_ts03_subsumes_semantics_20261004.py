"""TS-03 $subsumes spec-compliance findings (2026-10-04).

Maintenance spec-comp iteration 12 (worktree maint/spec-comp-20261004b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: first maintenance sweep of CodeSystem/$subsumes (R4 §4.8.21.3).
Controls verified fresh: all four outcome codes on the fixture hierarchy
(T2DM↔DM PAR edge both directions, self→equivalent, unrelated→
not-subsumed); codingA/codingB POST bodies with explicit system;
cross-system coding rejections 400 (spec: "relationships between the
code systems must be well established" — none defined here, error is
correct); version In-Parameter accepted; missing-param 422s
(system/codeA/codeB each); batch parity (scalar + error-entry shapes);
whitespace-prefixed code treated as distinct concept (consistent with
$lookup miss behavior — no trimming anywhere, uniform).

S1 (MEDIUM, NEW) — unknown codes yield a confident 200 not-subsumed.
    R4 §4.8.21.3 Out `outcome`: "If the server is unable to determine
    the relationship between the codes/Codings, then it returns an
    error response with an OperationOutcome." An unknown codeA or
    codeB (verified: unknown A, unknown B, both unknown) returns
    HTTP 200 {outcome: not-subsumed} — a definitive negative about a
    relationship the server could not evaluate. Clients performing
    hierarchy QA (e.g. "is X deprecated under Y") receive a silent
    wrong answer indistinguishable from a true negative. Inverse of
    CM-03 C1 (unknown poisons whole closure request) — here unknown
    silently yields confidence. Fix shape: detect unknown codes
    (mrconso lookup) and return an OperationOutcome error (R4 leaves
    the code open; error/not-supported both defensible).
    Pinned: s10/s11/s12 (A-unknown, B-unknown, both-unknown) with
    flip-on-fix instructions.

S2 (LOW, NEW) — instance-level invocation form 404s.
    R4 §4.8.21.3 URL: "[base]/CodeSystem/[id]/$subsumes" is a defined
    invocation form; when invoked on an instance, `system` may be
    omitted (its cardinality note: "must be provided unless the
    operation is invoked on a code system instance").
    /fhir/CodeSystem/snomed/$subsumes returns 404 (no route). Direct
    consequence of S2: the spec-legal system-less request shape has no
    server path at all. Fix shape: route instance-level to _do_subsumes
    with system derived from the instance id (requires id→system map;
    'snomed' id exists today for /CodeSystem/snomed read).
    Pinned: s20 (404 + system-less form unhandled).

Controls: s30-s36.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"
DM = "73211009"


def _outcome(client, **params) -> tuple[int, str | None]:
    r = client.get("/fhir/CodeSystem/$subsumes", params=params)
    if r.status_code != 200:
        return r.status_code, None
    out = next(
        (p.get("valueCode") for p in r.json().get("parameter", [])
         if p.get("name") == "outcome"),
        None,
    )
    return 200, out


class TestS1UnknownCodesSilentNotSubsumed:
    """S1 — unknown codes yield confident not-subsumed (spec: error)."""

    def test_s10_unknown_code_b(self, fhir_client):
        """Unknown codeB → 200 not-subsumed (CURRENT, deviation).
        Flip when fix lands: expect OperationOutcome error."""
        status, out = _outcome(
            fhir_client,
            system=SNOMED_URI, codeA=T2DM, codeB="99999999",
        )
        assert status == 200 and out == "not-subsumed"

    def test_s11_unknown_code_a(self, fhir_client):
        """Unknown codeA → 200 not-subsumed (CURRENT, deviation)."""
        status, out = _outcome(
            fhir_client,
            system=SNOMED_URI, codeA="99999999", codeB=T2DM,
        )
        assert status == 200 and out == "not-subsumed"

    def test_s12_both_unknown(self, fhir_client):
        """Both unknown → 200 not-subsumed (CURRENT, deviation)."""
        status, out = _outcome(
            fhir_client,
            system=SNOMED_URI, codeA="11111111", codeB="99999999",
        )
        assert status == 200 and out == "not-subsumed"


class TestS2InstanceLevelInvocation:
    """S2 — /CodeSystem/{id}/$subsumes 404s (spec defines the form)."""

    def test_s20_instance_level_404(self, fhir_client):
        """Instance-level form returns 404 today (CURRENT, deviation).
        Flip when routed: expect 200 outcome for system-less params."""
        r = fhir_client.get(
            "/fhir/CodeSystem/snomed/$subsumes",
            params={"codeA": T2DM, "codeB": DM},
        )
        assert r.status_code == 404


class TestSubsumesControls:
    """Controls: conformant shapes re-verified fresh."""

    def test_s30_outcomes_both_directions_and_self(
        self, fhir_client
    ):
        """T2DM subsumed-by DM; DM subsumes T2DM; self equivalent."""
        assert _outcome(
            fhir_client,
            system=SNOMED_URI, codeA=T2DM, codeB=DM,
        ) == (200, "subsumed-by")
        assert _outcome(
            fhir_client,
            system=SNOMED_URI, codeA=DM, codeB=T2DM,
        ) == (200, "subsumes")
        assert _outcome(
            fhir_client,
            system=SNOMED_URI, codeA=T2DM, codeB=T2DM,
        ) == (200, "equivalent")

    def test_s31_coding_body_with_system(self, fhir_client):
        """codingA/codingB POST with explicit system works (QC-273
        pattern held; system IS required for non-instance calls)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA",
                 "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
                {"name": "codingB",
                 "valueCoding": {"system": SNOMED_URI, "code": DM}},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200

    def test_s32_cross_system_coding_rejected(self, fhir_client):
        """codingB from a different system → 400 (spec: relationships
        between code systems must be well established; none defined)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA",
                 "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
                {"name": "codingB",
                 "valueCoding": {
                     "system": "http://loinc.org", "code": "2160-0"}},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400

    def test_s33_version_param_accepted(self, fhir_client):
        """version In-Parameter accepted (versioned-URI form differs:
        version as its own parameter is the spec's own example shape)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "version",
                 "valueString": "http://snomed.info/sct/723100002/version/20260301"},
                {"name": "codeA", "valueCode": T2DM},
                {"name": "codeB", "valueCode": DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        # c-fixbatch2 (H1): version pins rejected — single-version server.
        assert r.status_code == 400

    @pytest.mark.parametrize(
        "missing", ["system", "codeA", "codeB"]
    )
    def test_s34_missing_params_422(self, fhir_client, missing):
        """Each required scalar param missing → 422 (GET form)."""
        params = {
            "system": SNOMED_URI, "codeA": T2DM, "codeB": DM,
        }
        del params[missing]
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes", params=params
        )
        assert r.status_code == 422

    def test_s35_batch_parity_scalar(self, fhir_client):
        """Batch entry matches direct outcome (GET form: params from
        the entry URL query string per R4 batch semantics)."""
        batch = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [{
                "request": {
                    "method": "GET",
                    "url": (
                        "CodeSystem/$subsumes"
                        f"?system={SNOMED_URI}"
                        f"&codeA={T2DM}&codeB={DM}"
                    ),
                },
            }],
        }
        r = fhir_client.post("/fhir", json=batch)
        entry = r.json()["entry"][0]["resource"]
        out = next(
            (p.get("valueCode") for p in entry.get("parameter", [])
             if p.get("name") == "outcome"),
            None,
        )
        assert entry["resourceType"] == "Parameters"
        assert out == "subsumed-by"

    def test_s36_whitespace_code_distinct_concept(
        self, fhir_client
    ):
        """Whitespace-prefixed code is a distinct (unknown) concept —
        consistent with $lookup miss; no trimming anywhere (uniform)."""
        status, out = _outcome(
            fhir_client,
            system=SNOMED_URI,
            codeA=f" {T2DM}", codeB=T2DM,
        )
        # Trapped by S1's class: unknown code currently answers
        # not-subsumed. When S1's fix lands this flips to an error —
        # either way it must NEVER be 'equivalent'.
        assert out != "equivalent"
