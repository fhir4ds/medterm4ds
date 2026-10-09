"""TS-17 $subsumes transport forms + fixbatch2 cross-op uniformity (2026-10-08).

U1-U4 RESOLVED (c-fixbatch3): coding-only POST derives system from the
codings; scalar cross-system errors like the coding form (code-in-system
validation); unknown-param rejection extended to lookup/validate-code/
subsumes; systemVersion joins the version-rejection family uniformly.
Pins flipped.

Maintenance spec-comp iteration (worktree maint/spec-comp-20261008b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: $subsumes input FORMS (R4 §4.8.21.2 declares scalar
system/codeA/codeB AND self-identifying codingA/codingB) plus the
uniformity of the fixbatch2 rejection family (EA unknown params, H1
version params) ACROSS operations — fixbatch2 landed them per-op and
this suite checks the seams.

U1 (MEDIUM, NEW) — coding-only POST rejected.
    codingA/codingB with embedded systems (no scalar system) → 400
    'system, codeA, and codeB are required.' R4 §4.8.21.2: the coding
    forms are self-identifying; system is 0..1 when codings carry it.
    The coding form WITH an explicit system works (s31 control held).
    Same family as M1 (GET $lookup coding). Fix shape: derive
    system/codeA/codeB from the codings (reject only when the two
    codings disagree on system). Flip pin u10/u11.

U2 (LOW-MED, NEW) — cross-system verdicts differ by encoding.
    codingB from another system → 400 (s32: 'relationships between
    code systems must be well established'); the SCALAR form of the
    same request (system=SNOMED, codeB=E11 which exists only in
    ICD10CM) returns a confident 200 not-subsumed. Identical clinical
    question, opposite verdicts by encoding. S1 (unknown-code
    confidence) compounds: E11 IS known to the server, just not in
    the queried system. Fix shape: scalar form should verify both
    codes exist in the queried system (unknown-in-system → error per
    §4.8.21.3, same as S1's fix). Flip pin u20.

U3 (MEDIUM, NEW) — EA unknown-param rejection is $expand-only.
    fixbatch2's reject_unknown_query_params landed on $expand; GET
    $lookup/$validate-code/$subsumes still return 200 for arbitrary
    unknown params (bogusParam=1). The EA registry row is RESOLVED
    but the family is op-scoped. Fix shape: route the same helper at
    each op's known-param set. Flip pins u30/u31.

U4 (LOW-MED, NEW) — H1 systemVersion coverage inconsistent.
    $validate-code rejects version AND systemVersion (400);
    $lookup/$subsumes reject only `version` — systemVersion=9.9
    returns 200 ignored. Same family, same op group, divergent
    coverage: a cross-op consistency seam at the NEW contract
    (the cs05 e40 invariant family). Fix shape: include systemVersion
    in reject_unsupported_version_params everywhere it runs. Flip
    pins u40/u41.

Controls (fresh): all four outcome codes (subsumes/subsumed-by/
    equivalent/not-subsumed); coding+system POST 200 (s31 shape);
    version=9.9 400 on all three ops (H1 core held); translate
    conceptMapVersion 400 (N2 held); fixbatch2 $expand contract
    spot-verified live (EA/EB 400s).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
DM = "73211009"
T2DM = "44054006"
ICD10CM_ONLY = "E11"  # exists in fixture, but only in ICD10CM


def _outcome(params_json: dict):
    for p in params_json.get("parameter", []):
        if p.get("name") == "outcome":
            return p.get("valueCode")
    return None


class TestU1CodingOnlyPost:
    """U1 — self-identifying coding form rejected."""

    def test_u10_coding_only_post_rejected(self, fhir_client):
        """codingA/codingB without scalar system → 400. Flip when the
        coding form is self-identifying (200 + outcome)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI, "code": DM,
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": T2DM,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        # U1 FIXED: the coding form is self-identifying (R4 §4.8.21.2) —
        # system derived from the codings; outcome subsumes (DM subsumes
        # T2DM).
        assert r.status_code == 200
        assert _outcome(r.json()) == "subsumes"

    def test_u11_error_names_scalars(self, fhir_client):
        """With NO codings and no system the 400 still names the
        required params (coding-only no longer hits this path)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeA", "valueCode": DM},
                {"name": "codeB", "valueCode": T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        assert "system" in r.json()["issue"][0]["diagnostics"]


class TestU2CrossSystemByEncoding:
    """U2 — same question, opposite verdicts by encoding."""

    def test_u20_scalar_cross_system_confident(self, fhir_client):
        """Scalar form: SNOMED system + codeB that exists only in
        ICD10CM → confident 200 not-subsumed (vs coding form's 400).
        Flip when scalar form validates code-in-system (error) —
        matches S1's fix shape."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": DM, "codeB": ICD10CM_ONLY,
            },
        )
        # U2 FIXED: the scalar form now validates code-in-system and errors
        # like the coding form (was: confident 200 not-subsumed for a code
        # the queried system does not carry).
        assert r.status_code == 400


class TestU3UnknownParamsBeyondExpand:
    """U3 — EA unknown-param rejection scoped to $expand."""

    @pytest.mark.parametrize(
        "op,extra",
        [
            ("$lookup", {"code": T2DM}),
            ("$validate-code", {"code": T2DM}),
            ("$subsumes", {"codeA": DM, "codeB": T2DM}),
        ],
    )
    def test_u30_unknown_param_200(self, fhir_client, op, extra):
        """bogusParam still 200 on the non-expand ops. Flip when the
        reject_unknown_query_params helper routes at these ops."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/{op}",
            params={"system": SNOMED_URI, **extra, "bogusParam": "1"},
        )
        # U3 FIXED: unknown-param rejection extended beyond $expand.
        assert r.status_code == 400

    def test_u31_expand_rejects(self, fhir_client):
        """Control: $expand DOES reject (fixbatch2 contract held —
        the premise for U3's op-scoping)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={
                "url": f"{SNOMED_URI}/{DM}?fhir_vs=isa",
                "bogusParam": "1",
            },
        )
        assert r.status_code == 400


class TestU4SystemVersionCoverage:
    """U4 — systemVersion divergent across ops."""

    @pytest.mark.parametrize(
        "op,extra,expected",
        [
            ("$validate-code", {"code": T2DM}, 400),
            ("$lookup", {"code": T2DM}, 400),
            ("$subsumes", {"codeA": DM, "codeB": T2DM}, 400),
        ],
    )
    def test_u40_system_version_divergent(
        self, fhir_client, op, extra, expected
    ):
        """systemVersion=9.9: 400 on $validate-code, 200-ignored on
        $lookup/$subsumes. Flip the 200s when systemVersion joins the
        rejection family uniformly."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/{op}",
            params={
                "system": SNOMED_URI, **extra,
                "systemVersion": "9.9",
            },
        )
        assert r.status_code == expected

    def test_u41_version_rejected_everywhere(self, fhir_client):
        """Control: `version` itself is uniformly 400 (H1 core held on
        all three ops)."""
        for op, extra in [
            ("$validate-code", {"code": T2DM}),
            ("$lookup", {"code": T2DM}),
            ("$subsumes", {"codeA": DM, "codeB": T2DM}),
        ]:
            r = fhir_client.get(
                f"/fhir/CodeSystem/{op}",
                params={
                    "system": SNOMED_URI, **extra, "version": "9.9",
                },
            )
            assert r.status_code == 400, op


class TestTS17Controls:
    """Controls — outcome codes + held contracts, fresh."""

    @pytest.mark.parametrize(
        "a,b,expected",
        [
            (DM, T2DM, "subsumes"),
            (T2DM, DM, "subsumed-by"),
            (T2DM, T2DM, "equivalent"),
        ],
    )
    def test_c10_outcomes(self, fhir_client, a, b, expected):
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={"system": SNOMED_URI, "codeA": a, "codeB": b},
        )
        assert r.status_code == 200
        assert _outcome(r.json()) == expected

    def test_c11_coding_with_system_200(self, fhir_client):
        """s31 shape: coding form + explicit system works."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI, "code": DM,
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": T2DM,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200
        assert _outcome(r.json()) == "subsumes"

    def test_c12_translate_version_400(self, fhir_client):
        """N2 held: conceptMapVersion rejected."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetSystem": SNOMED_URI,
                "conceptMapVersion": "9.9",
            },
        )
        assert r.status_code == 400
