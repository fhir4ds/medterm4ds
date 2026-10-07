"""TS-10 GET↔POST parameter-binding parity (2026-10-06).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261006b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: R4 TerminologyCapabilities advertises both GET and POST forms
for every terminology operation, and R4 operation statements declare
the SAME parameter set for both forms. This sweep compares parameter
BINDING between the two transports across all operations.

Survey (fresh, this run):
* Status + content-type + semantic payload parity on the happy path:
  lookup, validate-code (CS + VS), translate, subsumes, expand —
  GET and POST return semantically identical results (MATCH).
* POST body params are type-flexible (valueString-typed system/code
  bind identically to valueUri/valueCode) — R4 is silent on strict
  value[x] typing; accepted as conformant, pinned as control.
* expand count param binds on POST exactly as GET (total=2,
  contains=2 both paths).

M1 (MEDIUM, NEW) — GET rejects `coding`, POST accepts it.
    R4 $lookup declares `coding` (0..1 Coding) for BOTH forms: "the
    coding parameter allows a complete coding to be supplied rather
    than the separate system and code parameters." The POST handler
    implements this (lookup_post derives system/code from coding,
    fhir_api.py:2363-2371, TS-02 HISTORIAN QA-022). The GET handler
    declares only system/code/version Query params (lookup_get,
    fhir_api.py:2333-2347) — a `coding` query parameter is unknown to
    FastAPI and rejected 422 before reaching handler logic. Fresh
    probes: POST lookup coding → 200 full Parameters; GET lookup
    coding (JSON-encoded) → 422 processing error. A client porting
    between transports silently loses a spec-declared capability.
    Fix shape: accept coding on GET (parse JSON literal per QC-318's
    +-decoding precedent or repeated param form) OR document the
    GET-coding gap in TerminologyCapabilities (operation definition
    should then omit coding for GET only).

Controls re-verified fresh: empty-body POST with query params ignored
    (400 missing system/code — correct: POST reads body only),
    body+query duplication harmless, unknown POST body params
    accepted (200; R4 does not mandate rejection of unknown
    Parameters — matches GET's ignore behavior for unknown query
    params), canonical-typed POST binding, bogus-code miss encoding.
"""

from __future__ import annotations

import json

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"
VS_ISA_DM = "http://snomed.info/sct/73211009?fhir_vs=isa"
# N1 fix (maint/fix-conformance-20261007): url now resolves — the
# implicit crosswalk urn is the accepted value (the old loinc-to-snomed
# urn 400s on both transports, identically).
CM = "urn:medterm4ds:crosswalk"
LOINC_URI = "http://loinc.org"
HBA1C = "4548-4"


def _flat(j: dict) -> dict:
    return {
        q["name"]: next((v for k, v in q.items() if k.startswith("value")), None)
        for q in j.get("parameter", [])
    }


class TestM1GetCodingRejected:
    """M1 — `coding` binds on POST, 422s on GET."""

    def test_m10_post_coding_binds(self, fhir_client):
        """POST $lookup with coding parameter (no system/code) → 200
        with the full lookup payload (R4 alternative encoding)."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {
                        "name": "coding",
                        "valueCoding": {
                            "system": SNOMED_URI, "code": T2DM,
                        },
                    }
                ],
            },
        )
        assert r.status_code == 200, r.text
        flat = _flat(r.json())
        assert flat["code"] == T2DM
        assert flat["display"] == "Type 2 diabetes mellitus"

    def test_m11_get_coding_rejected(self, fhir_client):
        """GET $lookup with coding query param (JSON-encoded) → 422:
        FastAPI rejects the unknown query parameter before handler
        logic. R4 declares coding for both transports. WHEN the fix
        lands (GET accepts coding), flip to 200 + display assert."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"coding": json.dumps({"system": SNOMED_URI, "code": T2DM})},
        )
        assert r.status_code == 422, (
            f"GET now accepts coding ({r.status_code}) — flip this pin "
            "to parity with m10."
        )

    def test_m12_divergence_pinned(self, fhir_client):
        """THE FINDING: same logical request, different transport,
        different capability. POST=200/full-payload vs GET=422."""
        p = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {
                        "name": "coding",
                        "valueCoding": {
                            "system": SNOMED_URI, "code": T2DM,
                        },
                    }
                ],
            },
        )
        g = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"coding": json.dumps({"system": SNOMED_URI, "code": T2DM})},
        )
        assert p.status_code == 200 and g.status_code == 422, (
            "GET/POST coding capability converged — the parity fix "
            "landed; flip m11/m12 pins."
        )


class TestTransportParityControls:
    """Controls: happy-path GET↔POST semantic parity everywhere."""

    @pytest.mark.parametrize(
        "path,get_params,post_params",
        [
            (
                "/fhir/CodeSystem/$lookup",
                {"system": SNOMED_URI, "code": T2DM},
                [
                    {"name": "system", "valueUri": SNOMED_URI},
                    {"name": "code", "valueCode": T2DM},
                ],
            ),
            (
                "/fhir/CodeSystem/$validate-code",
                {"system": SNOMED_URI, "code": T2DM},
                [
                    {"name": "system", "valueUri": SNOMED_URI},
                    {"name": "code", "valueCode": T2DM},
                ],
            ),
            (
                "/fhir/ValueSet/$validate-code",
                {"url": VS_ISA_DM, "system": SNOMED_URI, "code": T2DM},
                [
                    {"name": "url", "valueUri": VS_ISA_DM},
                    {"name": "system", "valueUri": SNOMED_URI},
                    {"name": "code", "valueCode": T2DM},
                ],
            ),
            (
                "/fhir/ConceptMap/$translate",
                {
                    "url": CM, "system": LOINC_URI,
                    "code": HBA1C, "targetSystem": SNOMED_URI,
                },
                [
                    {"name": "url", "valueUri": CM},
                    {"name": "system", "valueUri": LOINC_URI},
                    {"name": "code", "valueCode": HBA1C},
                    {"name": "targetSystem", "valueUri": SNOMED_URI},
                ],
            ),
            (
                "/fhir/CodeSystem/$subsumes",
                {"system": SNOMED_URI, "codeA": "73211009", "codeB": T2DM},
                [
                    {"name": "system", "valueUri": SNOMED_URI},
                    {"name": "codeA", "valueCode": "73211009"},
                    {"name": "codeB", "valueCode": T2DM},
                ],
            ),
            (
                "/fhir/ValueSet/$expand",
                {"url": VS_ISA_DM, "count": 2},
                [
                    {"name": "url", "valueUri": VS_ISA_DM},
                    {"name": "count", "valueInteger": 2},
                ],
            ),
        ],
    )
    def test_p10_semantic_parity(
        self, fhir_client, path, get_params, post_params
    ):
        """GET and POST return semantically identical responses for
        the same logical request (status + body equality)."""
        g = fhir_client.get(path, params=get_params)
        p = fhir_client.post(
            path,
            json={"resourceType": "Parameters", "parameter": post_params},
        )
        assert g.status_code == p.status_code == 200
        assert g.json() == p.json(), (
            f"GET/POST bodies diverged for {path}"
        )

    def test_p11_post_type_flexibility(self, fhir_client):
        """valueString-typed system/code bind identically to the
        canonical valueUri/valueCode forms (documented lenient
        binding; R4 is silent on value[x] strictness)."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueString": SNOMED_URI},
                    {"name": "code", "valueString": T2DM},
                ],
            },
        )
        assert r.status_code == 200
        assert _flat(r.json())["display"] == "Type 2 diabetes mellitus"

    def test_p12_post_reads_body_only(self, fhir_client):
        """POST with valid query params but EMPTY body → 400: POST
        binds body parameters only, never falls back to the query
        string (no cross-transport leakage)."""
        r = fhir_client.post(
            f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={T2DM}",
            json={"resourceType": "Parameters", "parameter": []},
        )
        assert r.status_code == 400
        assert "system and code are required" in r.text

    def test_p13_unknown_post_param_accepted(self, fhir_client):
        """Unknown body parameter (bogus) is accepted silently —
        mirrors GET's ignore behavior for unknown query params; R4
        does not mandate rejection. Pinned as the current contract."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": SNOMED_URI},
                    {"name": "code", "valueCode": T2DM},
                    {"name": "bogus", "valueString": "x"},
                ],
            },
        )
        assert r.status_code == 200
        assert _flat(r.json())["display"] == "Type 2 diabetes mellitus"

    def test_p14_post_bogus_code_miss_encoding(self, fhir_client):
        """valueString-typed bogus code binds (no silent skip) and
        encodes as 200 + not-found OperationOutcome — consistent with
        the CS-05 miss-encoding contract."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueString": SNOMED_URI},
                    {"name": "code", "valueString": "99999999"},
                ],
            },
        )
        assert r.status_code == 200
        assert r.json()["resourceType"] == "OperationOutcome"
        assert any(
            i.get("code") == "not-found"
            for i in r.json()["issue"]
        )
