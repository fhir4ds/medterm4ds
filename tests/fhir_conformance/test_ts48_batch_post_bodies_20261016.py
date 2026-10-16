"""TS-48 batch POST-entry body seams (2026-10-16).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261016).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the unknown-param rejection family now has three mapped
seams — FA2 (batch GET entries skip query-side rejection), FA4
(direct POST bodies at lookup/validate/subsumes), FA5 (POST routes
skip query-side rejection). This iteration completes the matrix:
the $batch POST ENTRY's Parameters BODY.

BX1 (MEDIUM, NEW) — batch POST entries bypass body-side unknown-
    param rejection on ALL FOUR body-capable ops. A Parameters body
    carrying zzz returns entry-status 200 inside a batch for
    $lookup, $validate-code, $translate AND $expand — the last two
    400 the identical body on their direct POST routes (verified as
    the premise control this run). The dispatcher's POST branch
    never invokes the _parameter_names_present checks its direct
    handlers run. With BX1 the family matrix is complete:
      transport          direct-route    batch-entry
      GET query params   rejected        BYPASSED (FA2)
      POST query params  BYPASSED (FA5)  (query on POST entries — unused shape)
      POST body params   3 ops missing   BYPASSED (BX1, all 4 ops)
                         (FA4)
    Fix shape: run the body name-set check in the dispatcher's POST
    branches (the helper + known-sets all exist). Flip pins
    b10-b13.

BC1-ALIVE (re-verified, iter 45) — the custom ops ($search/
    $extract) still 404 from batch entries entirely.

Q1-ALIVE (re-verified, iter 20) — XML request bodies still 422
    ('Parameter unknown: Input should be a valid dictionary' — the
    JSON-parser artifact Q2 registered) on POST ops.

Controls (fresh): direct POST twins 400 on translate/expand (the
    premise); batch POST entries with CLEAN bodies succeed (200)
    — the bypass is param-specific, not a broken dispatch.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
T2DM = "44054006"
DM = "73211009"
DM_ISA_URL = f"{SNOMED_URI}/{DM}?fhir_vs=isa"


def _batch_post_entry(fhir_client, path: str, params: list[dict]):
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": path},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": params,
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    return (
        r.json().get("entry", [{}])[0]
        .get("response", {})
        .get("status", "")
    )


_ZZZ_BODIES = {
    "lookup": (
        "CodeSystem/$lookup",
        [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": T2DM},
            {"name": "zzz", "valueString": "x"},
        ],
    ),
    "validate": (
        "CodeSystem/$validate-code",
        [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": T2DM},
            {"name": "zzz", "valueString": "x"},
        ],
    ),
    "translate": (
        "ConceptMap/$translate",
        [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": T2DM},
            {"name": "targetSystem", "valueUri": ICD10CM_URI},
            {"name": "zzz", "valueString": "x"},
        ],
    ),
    "expand": (
        "ValueSet/$expand",
        [
            {"name": "url", "valueUri": DM_ISA_URL},
            {"name": "zzz", "valueString": "x"},
        ],
    ),
}


class TestBX1BatchPostBodyBypass:
    """BX1 — batch POST entries skip body unknown-param checks."""

    @pytest.mark.parametrize("op", list(_ZZZ_BODIES))
    def test_b10_batch_post_zzz_200(self, fhir_client, op):
        """Entry-status 200 for the zzz body in a batch POST entry
        (all four body-capable ops). Flip when the dispatcher's POST
        branches run the name-set check."""
        path, params = _ZZZ_BODIES[op]
        status = _batch_post_entry(fhir_client, path, params)
        assert status == "200", op

    def test_b11_direct_twins_reject_control(self, fhir_client):
        """Premise control: the wired ops 400 the identical body on
        their direct POST routes (fixbatch2/4 wiring held)."""
        for path, params in (
            _ZZZ_BODIES["translate"],
            _ZZZ_BODIES["expand"],
        ):
            r = fhir_client.post(
                f"/fhir/{path}",
                json={
                    "resourceType": "Parameters",
                    "parameter": params,
                },
            )
            assert r.status_code == 400, path

    def test_b12_clean_bodies_succeed_control(self, fhir_client):
        """Control: clean bodies dispatch fine via batch POST — the
        bypass is param-specific, not a broken dispatch."""
        path, params = _ZZZ_BODIES["translate"]
        clean = [p for p in params if p["name"] != "zzz"]
        status = _batch_post_entry(fhir_client, path, clean)
        assert status == "200"


class TestBC1Q1Alive:
    """BC1 + Q1 re-verifications."""

    def test_b20_custom_ops_404_from_batch(self, fhir_client):
        """BC1 (iter 45) alive: $search/$extract unreachable from
        batch entries (404 + false diagnostic)."""
        for url in (
            "CodeSystem/$search?query=diabetes",
            "CodeSystem/$extract?text=diabetes",
        ):
            bundle = {
                "resourceType": "Bundle",
                "type": "batch",
                "entry": [
                    {
                        "request": {
                            "method": "GET", "url": url,
                        }
                    }
                ],
            }
            r = fhir_client.post("/fhir", json=bundle)
            status = (
                r.json().get("entry", [{}])[0]
                .get("response", {})
                .get("status", "")
            )
            assert status == "404", url

    def test_b21_xml_request_body_422(self, fhir_client):
        """Q1 (iter 20) alive: XML Parameters body → 422 with the
        JSON-parser artifact diagnostic (Q2)."""
        xml = (
            '<?xml version="1.0"?>'
            '<Parameters xmlns="http://hl7.org/fhir">'
            '<parameter><name value="system"/>'
            '<valueUri value="http://snomed.info/sct"/></parameter>'
            '<parameter><name value="code"/>'
            '<valueCode value="44054006"/></parameter>'
            "</Parameters>"
        )
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            content=xml.encode(),
            headers={"Content-Type": "application/fhir+xml"},
        )
        assert r.status_code == 422
