"""TS-45 POST-side rejections + body-side tail (2026-10-15).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261015b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the EA/H1/W4 fail-loud contracts were built GET-first. This
suite maps the POST side of the same matrix: (a) unknown PARAMETERS
BODY entries on ops whose GET route rejects unknown query params;
(b) unknown/version QUERY params riding a POST request (per R4
§3.1.0.1.1 parameters MAY come from the query string on POST — the
QC-306 precedent the closure op itself implements for ``name``).

FA4 (MEDIUM, NEW) — body-side unknown-param rejection covers only
    half the ops. lookup/validate-code/subsumes POST bodies accept
    arbitrary parameter entries (zzz → 200) while expand/closure/
    translate bodies reject them (400, fixbatch2/4 wiring). The
    body-side _parameter_names_present check exists as a module
    helper and is two lines per op — the family simply stopped at
    three ops. Fix shape: run the same name-set check in
    lookup_post/validate_post/subsumes_post against their known sets
    (already defined for the GET routes). Flip pins f40/f41.

FA5 (MEDIUM, NEW) — POST routes skip QUERY-side rejections entirely.
    The identical query that 400s on GET (bogusParam; version=9.9;
    systemVersion=9.9; includeDesignations) returns 200 when the
    request method is POST — on EVERY op tested, including the ops
    whose GET rejections are the fix-batches' flagship contracts.
    R4 §3.1.0.1.1 makes query-string POST params first-class (the
    server's own closure op reads query ``name`` as a fallback), so
    the POST route is a complete bypass of the unknown/version
    rejection family — transport-dependent verdicts for identical
    parameter sets (FA2's mirror on the POST side).
    Fix shape: invoke reject_unknown_query_params + the version
    rejection helpers on the POST handlers exactly as their GET
    twins do. Flip pins f50-f54.

Controls (fresh): expand/closure/translate POST BODY unknown-param
    400s re-verified (the wired half of the contract); GET-side
    rejections re-verified on lookup (bogusParam/version/systemVersion
    all 400); closure has no GET route (404 — POST-only op, W4's
    GET-side set is batch-scoped only).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
T2DM = "44054006"
DM = "73211009"
DM_ISA_URL = f"{SNOMED_URI}/{DM}?fhir_vs=isa"


def _lookup_body():
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": T2DM},
        ],
    }


class TestFA4PostBodyTail:
    """FA4 — body-side unknown-param rejection at half the ops."""

    @pytest.mark.parametrize(
        "path,body",
        [
            ("/fhir/CodeSystem/$lookup", _lookup_body()),
            (
                "/fhir/CodeSystem/$validate-code",
                _lookup_body(),
            ),
            (
                "/fhir/CodeSystem/$subsumes",
                {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": SNOMED_URI},
                        {"name": "codeA", "valueCode": DM},
                        {"name": "codeB", "valueCode": T2DM},
                    ],
                },
            ),
        ],
        ids=["lookup", "validate", "subsumes"],
    )
    def test_f40_post_body_unknown_param(
        self, fhir_client, path, body
    ):
        """zzz parameter entry in the POST body → 200 (the body-side
        family never reached these ops). Flip when the name-set check
        lands in their POST handlers."""
        body["parameter"].append(
            {"name": "zzz", "valueString": "x"}
        )
        r = fhir_client.post(path, json=body)
        assert r.status_code == 200

    @pytest.mark.parametrize(
        "path",
        [
            "/fhir/ValueSet/$expand",
            "/fhir/CodeSystem/$closure",
            "/fhir/ConceptMap/$translate",
        ],
        ids=["expand", "closure", "translate"],
    )
    def test_f41_wired_ops_reject_control(self, fhir_client, path):
        """Control: the wired half rejects (fixbatch2/4 body checks
        held) — the family boundary is the three unwired ops."""
        bodies = {
            "/fhir/ValueSet/$expand": [
                {"name": "url", "valueUri": DM_ISA_URL},
                {"name": "zzz", "valueString": "x"},
            ],
            "/fhir/CodeSystem/$closure": [
                {"name": "name", "valueString": "f41-control"},
                {"name": "zzz", "valueString": "x"},
            ],
            "/fhir/ConceptMap/$translate": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
                {"name": "targetSystem", "valueUri": ICD10CM_URI},
                {"name": "zzz", "valueString": "x"},
            ],
        }
        r = fhir_client.post(
            path,
            json={
                "resourceType": "Parameters",
                "parameter": bodies[path],
            },
        )
        assert r.status_code == 400


class TestFA5PostRoutesSkipQueryRejections:
    """FA5 — query-side rejections are GET-only."""

    def test_f50_bogus_query_on_post(self, fhir_client):
        """bogusParam in the QUERY STRING of a POST lookup → 200 (the
        GET twin 400s). Flip when POST handlers run the known-set
        rejection."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json=_lookup_body(),
            params={"bogusParam": "1"},
        )
        assert r.status_code == 200

    def test_f51_version_query_on_post(self, fhir_client):
        """version=9.9 via POST query → 200 (GET twin 400s — H1).
        Flip with FA5's fix."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json=_lookup_body(),
            params={"version": "9.9"},
        )
        assert r.status_code == 200

    def test_f52_systemversion_query_on_post(self, fhir_client):
        """systemVersion=9.9 via POST query → 200 (GET twin 400s —
        U4). Flip with FA5's fix."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json=_lookup_body(),
            params={"systemVersion": "9.9"},
        )
        assert r.status_code == 200

    def test_f53_expand_post_query_unsupported_param(self, fhir_client):
        """includeDesignations via POST query on $expand → 200 (GET
        twin 400s — EA). Flip with FA5's fix."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": DM_ISA_URL},
            ],
        }
        r = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=body,
            params={"includeDesignations": "true"},
        )
        assert r.status_code == 200

    def test_f54_get_twins_reject_control(self, fhir_client):
        """Premise control: the GET twins of f50-f53 all 400 (the
        fix-batch contracts held on the GET surface)."""
        for params in (
            {"system": SNOMED_URI, "code": T2DM, "bogusParam": "1"},
            {"system": SNOMED_URI, "code": T2DM, "version": "9.9"},
            {"system": SNOMED_URI, "code": T2DM,
             "systemVersion": "9.9"},
        ):
            r = fhir_client.get(
                "/fhir/CodeSystem/$lookup", params=params
            )
            assert r.status_code == 400
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL,
                    "includeDesignations": "true"},
        )
        assert r.status_code == 400


class TestControls:
    """Controls — closure route shape."""

    def test_c10_closure_get_route_absent(self, fhir_client):
        """$closure is POST-only (404 on GET) — its W4 GET-side
        known-set is reachable only via batch entries."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$closure", params={"name": "x"}
        )
        assert r.status_code == 404
