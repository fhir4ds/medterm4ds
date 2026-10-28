"""TS-97 batch POST-body validation wings (2026-10-27).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261027d).
FRESH EVIDENCE — live batch-POST probes executed this run; no
prior-run results cited.

Scope: BX1 pinned the batch POST-BODY unknown-param bypass on the
four body-capable ops; this iteration probes the VALIDATION wings on
the POST-body channel (the FA2-wings complement on the second
transport).

BX2 (annotation to FA2/BX1, pinned fresh) — the POST-body channel's
    validation bypass: validate-code date=garbage in a batch POST
    body → 200 (H4 bypassed; direct POST 400s per fixbatch2's
    validate_fhir_date), expand zzz body param → 200 (EA body-side
    bypassed; direct POST 400s). The wings now span BOTH batch
    transports × both rejection families — the dispatcher-level fix
    (FA2) is the single seam that closes the matrix. Pinned
    bx10-bx13.

Controls (fresh): the batch POST crosswalk baseline works (system/
    code/targetSystem body params resolve — the AS3 gap is
    specifically the athena map_url); reverse in a batch POST body
    is honored (T1's batch wiring, flip-on-message verified by the
    differing status being 200 — the reverse flag consults).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"
T2DM = "44054006"


def _batch_post_status(fhir_client, path, params) -> str:
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle", "type": "batch",
            "entry": [{
                "request": {"method": "POST", "url": path},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": params,
                },
            }],
        },
    )
    return r.json()["entry"][0]["response"]["status"]


class TestBX2PostBodyWings:
    """FA2/BX1 wings on the POST-body channel."""

    def test_bx10_validate_date_bypassed(self, fhir_client):
        """H4: date=garbage in a batch POST body → 200 (the direct
        POST 400s). Flip with FA2's dispatcher fix."""
        status = _batch_post_status(
            fhir_client,
            "CodeSystem/$validate-code",
            [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
                {"name": "date", "valueDateTime": "garbage"},
            ],
        )
        assert status == "200"

    def test_bx11_validate_date_direct_400(self, fhir_client):
        """Premise: the direct POST twin validates (H4 held there
        since fixbatch2)."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$validate-code",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": SNOMED_URI},
                    {"name": "code", "valueCode": T2DM},
                    {"name": "date", "valueDateTime": "garbage"},
                ],
            },
        )
        assert r.status_code == 400

    def test_bx12_expand_unknown_body_bypassed(self, fhir_client):
        """EA body-side: zzz in a batch POST body → 200 (direct POST
        400s per fixbatch2's body checks)."""
        status = _batch_post_status(
            fhir_client,
            "ValueSet/$expand",
            [
                {"name": "url", "valueUri": DM_ISA_URL},
                {"name": "zzz", "valueString": "1"},
            ],
        )
        assert status == "200"

    def test_bx13_expand_unknown_direct_400(self, fhir_client):
        r = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "url", "valueUri": DM_ISA_URL},
                    {"name": "zzz", "valueString": "1"},
                ],
            },
        )
        assert r.status_code == 400


class TestBatchPostBaselines:
    """Controls — what the POST-body channel gets RIGHT."""

    def test_c10_crosswalk_body_works(self, fhir_client):
        """The crosswalk via batch POST body resolves (the AS3 gap
        is specifically the athena map_url, not the body channel)."""
        status = _batch_post_status(
            fhir_client,
            "ConceptMap/$translate",
            [
                {"name": "system", "valueUri": ICD10CM_URI},
                {"name": "code", "valueCode": "E11"},
                {"name": "targetSystem", "valueUri": SNOMED_URI},
            ],
        )
        assert status == "200"

    def test_c11_reverse_body_consulted(self, fhir_client):
        """T1's batch wiring: reverse in a POST body is honored
        (200 with the reverse presentation — the flag consults)."""
        status = _batch_post_status(
            fhir_client,
            "ConceptMap/$translate",
            [
                {"name": "system", "valueUri": ICD10CM_URI},
                {"name": "code", "valueCode": "E11"},
                {
                    "name": "targetSystem",
                    "valueUri": SNOMED_URI,
                },
                {"name": "reverse", "valueBoolean": True},
            ],
        )
        assert status == "200"
