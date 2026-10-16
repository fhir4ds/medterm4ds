"""TS-49 HTTP method/layer matrix (2026-10-16).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261016b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the HTTP layer beneath the operations — OPTIONS/PUT/PATCH/
DELETE handling, the Allow header, route-form tolerance (trailing
slash, case), and the /fhir root. Error-payload completeness
(diagnostics on every OperationOutcome issue) surveyed FIRST and
found COMPLETE (11-path matrix, controls pinned).

HM1 (LOW-MED, NEW) — Allow header lies about POST-capable routes.
    OPTIONS (and every 405) on /fhir/CodeSystem/$lookup and
    /fhir/ValueSet/$expand returns 'Allow: GET' while POST succeeds
    (200) on the same routes. RFC 9110 §10.2.1: the Allow header
    'MUST list the set of methods supported by' the resource; R4
    §3.2.0 clients use it to discover operation transports (the
    TS-10 GET/POST parity surface exists BECAUSE both transports
    work). A client reading Allow concludes POST is unsupported.
    Root cause shape: FastAPI's default Allow for unmatched methods
    lists only the FIRST method's route or the router's 405 default;
    operations registered as sibling GET+POST routes don't merge.
    Fix shape: an OPTIONS handler or middleware that unions the
    route-table methods per path. Flip pins m10-m12.

HM2 — RETRACTED pre-commit (first-draft premise error: GET /fhir
    405s; POST is the only method and Allow: POST is accurate).
    Pinned as the contrast control that isolates HM1.

HM3 (LOW, informational) — route-form strictness undocumented:
    trailing slash ($lookup/), lowercase resource segment
    (/codesystem/), and uppercase op ($LOOKUP) all 404. Per R4
    §3.1.0.4 URLs are case-sensitive and the spec does not require
    slash tolerance — strictness is CORRECT but undocumented in the
    CapabilityStatement/docs (a client-porting note, not a bug).

Controls (fresh): PUT/PATCH/DELETE 405 with Allow present on op and
    instance routes; HEAD 405 (R3 held); POST succeeds on $lookup/
    $expand (the HM1 premise); metadata is GET-only (POST 405 — its
    Allow 'GET' is accurate, a contrast control); error payloads
    complete across 11 paths (severity+code+diagnostics everywhere).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"


class TestHM1AllowHeaderPost:
    """HM1 — Allow omits POST on POST-capable op routes."""

    def test_m10_lookup_allow_get_only(self, fhir_client):
        """OPTIONS $lookup → 405 'Allow: GET' though POST works (the
        TS-10 parity surface). Flip when Allow unions the route
        methods ('GET, POST' or 'OPTIONS, GET, POST')."""
        r = fhir_client.options("/fhir/CodeSystem/$lookup")
        assert r.status_code == 405
        assert r.headers.get("allow") == "GET"

    def test_m11_lookup_post_succeeds_premise(self, fhir_client):
        """Premise control: POST on the same route 200s."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
            ],
        }
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup", json=body
        )
        assert r.status_code == 200

    def test_m12_expand_allow_get_only(self, fhir_client):
        """$expand shows the same lie (Allow: GET, POST works)."""
        r = fhir_client.options("/fhir/ValueSet/$expand")
        assert r.status_code == 405
        assert r.headers.get("allow") == "GET"

    def test_m13_metadata_get_only_accurate(self, fhir_client):
        """Contrast control: metadata IS GET-only (POST 405) — its
        Allow: GET is accurate; the bug is route-specific, not a
        blanket default."""
        r = fhir_client.options("/fhir/metadata")
        assert r.status_code == 405
        assert r.headers.get("allow") == "GET"
        rp = fhir_client.post("/fhir/metadata")
        assert rp.status_code == 405


class TestHM2RootAllowAccurate:
    """HM2 RETRACTED pre-commit: /fhir root Allow: POST is ACCURATE.

    First-draft premise assumed GET /fhir was the batch entry; the
    live survey shows GET /fhir 405s — POST is the only method and
    the Allow header is correct. Pinned as an accuracy control (the
    contrast that isolates HM1 as route-specific)."""

    def test_m20_root_allow_post_accurate(self, fhir_client):
        r = fhir_client.options("/fhir")
        assert r.status_code == 405
        assert r.headers.get("allow") == "POST"

    def test_m21_root_get_405_premise(self, fhir_client):
        r = fhir_client.get("/fhir")
        assert r.status_code == 405


class TestHM3RouteForms:
    """HM3 — strict route forms (correct, undocumented)."""

    @pytest.mark.parametrize(
        "path",
        [
            "/fhir/CodeSystem/$lookup/",
            "/fhir/codesystem/$lookup",
            "/fhir/CodeSystem/$LOOKUP",
        ],
        ids=["trailing-slash", "lowercase-segment", "uppercase-op"],
    )
    def test_m30_strict_forms_404(self, fhir_client, path):
        """All non-canonical forms 404 (case-sensitive per R4
        §3.1.0.4; slash strictness is the framework default).
        Informational pin: strictness correct, tolerance absent —
        document in the porting notes."""
        r = fhir_client.get(
            path,
            params={
                "system": SNOMED_URI, "code": "44054006",
            },
        )
        assert r.status_code == 404


class TestMethodControls:
    """Controls — 405 shapes with Allow present."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("put", "/fhir/CodeSystem/$lookup"),
            ("patch", "/fhir/CodeSystem/$lookup"),
            ("delete", "/fhir/CodeSystem/$lookup"),
            ("put", "/fhir/CodeSystem/snomed"),
            ("head", "/fhir/metadata"),
        ],
        ids=["put-op", "patch-op", "delete-op", "put-instance", "head-meta"],
    )
    def test_c10_405_with_allow(
        self, fhir_client, method, path
    ):
        r = getattr(fhir_client, method)(path)
        assert r.status_code == 405
        assert r.headers.get("allow")


class TestErrorPayloadControls:
    """Controls — OperationOutcome completeness across 11 paths."""

    @pytest.mark.parametrize(
        "method,path,kw",
        [
            ("get", "/fhir/CodeSystem/$lookup",
             {"params": {"code": "44054006"}}),
            ("get", "/fhir/CodeSystem/$lookup",
             {"params": {"system": "http://x.org", "code": "1"}}),
            ("get", "/fhir/CodeSystem/$validate-code",
             {"params": {"code": "44054006"}}),
            ("get", "/fhir/ValueSet/$expand", {}),
            ("get", "/fhir/ValueSet/$expand",
             {"params": {"url": "http://x.org/vs"}}),
            ("get", "/fhir/ValueSet/$expand",
             {"params": {"url": DM_ISA_URL, "count": "abc"}}),
            ("get", "/fhir/CodeSystem/$subsumes",
             {"params": {
                 "system": SNOMED_URI, "codeA": "73211009"}}),
            ("get", "/fhir/ConceptMap/$translate",
             {"params": {
                 "system": SNOMED_URI, "code": "44054006"}}),
            ("post", "/fhir/CodeSystem/$closure",
             {"json": {
                 "resourceType": "Parameters", "parameter": []}}),
            ("get", "/fhir/CodeSystem/zzz", {}),
            ("get", "/fhir/metadata", {"params": {"mode": "bogus"}}),
        ],
        ids=[
            "lookup-missing-system", "lookup-unknown-system",
            "validate-missing-system", "expand-no-params",
            "expand-unknown-url", "expand-bogus-count",
            "subsumes-missing-b", "translate-no-target",
            "closure-no-name", "read-404", "bad-mode",
        ],
    )
    def test_c20_issue_completeness(
        self, fhir_client, method, path, kw
    ):
        """Every error path: severity + code + diagnostics on every
        issue (OperationOutcome R4 constraints + the repo's own
        diagnostics convention)."""
        r = getattr(fhir_client, method)(path, **kw)
        assert 400 <= r.status_code < 500
        issues = r.json()["issue"]
        assert issues
        for issue in issues:
            assert issue.get("severity") == "error"
            assert issue.get("code")
            assert issue.get("diagnostics")
