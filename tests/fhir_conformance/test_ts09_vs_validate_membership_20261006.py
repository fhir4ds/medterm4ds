"""TS-09 ValueSet $validate-code membership sweep (2026-10-06).

Maintenance spec-comp iteration 18 (worktree maint/spec-comp-20261006).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: first maintenance sweep of ValueSet/$validate-code's MEMBERSHIP
contract (R4 §4.9.3): url-scoped validation, coding/codeableConcept
forms, inline valueSet resources, display enforcement, and error
paths. The handler documents its url limitation in-source
(fhir_api.py:2781-2783: "The url param is accepted for
spec-compatibility but not used to restrict the membership check
today") — but the runtime SEVERITY of that documented gap has never
been pinned: out-of-valueSet codes validate TRUE.

V1 (HIGH, documented-gap escalated) — url-scoping absent: membership
    reduces to code-system existence. RxNorm 860975 (metformin)
    validates TRUE against http://snomed.info/sct/73211009?fhir_vs=isa
    (a SNOMED-only expansion of {73211009, 44054006}); the value set
    expansion demonstrably does NOT contain the code. A client using
    $validate-code to enforce value-set constraints (order codes,
    contraindication lists, formularies) receives silent TRUEs for
    codes outside the set. Same-system unknown codes DO return False
    (the existence check is sound; scoping is the gap). Fix shape:
    expand the url (implicit forms + compose parsing) and test
    membership against expansion.contains, or reject url+code with
    not-supported until membership evaluation exists. Pinned:
    v10 (cross-system TRUE — the headline), v11 (same-system unknown
    False — control doubling as contrast), v12 (inline valueSet
    resource accepted — same gap).

V2 (LOW) — inline valueSet In-param accepted but ignored for
    membership: a POSTed ValueSet resource whose compose includes only
    44054006 validates 860975 (not in the compose) TRUE — the resource
    is parsed for shape then the same existence check runs. Pinned
    with v12 (same probe); distinct registry row because the fix
    surface (compose evaluation) differs from url-form expansion.

Controls verified fresh (v20-v27): in-VS valid TRUE; in-VS parent
TRUE; display mismatch FALSE + message ('The display "wrong" is
incorrect'); display match TRUE; coding In-param POST TRUE;
codeableConcept multi-coding TRUE (VS-05 QA-069 any-coding contract);
missing url+system 400; unknown system 400 (route-level); batch of
error paths FHIR-shaped.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
VS_ISA_DM = f"{SNOMED_URI}/73211009?fhir_vs=isa"
T2DM = "44054006"
DM = "73211009"
METFORMIN = "860975"
ENDPOINT = "/fhir/ValueSet/$validate-code"


def _result(client, **params) -> tuple[int, bool | None]:
    r = client.get(ENDPOINT, params=params)
    if r.status_code != 200 or r.json().get("resourceType") != "Parameters":
        return r.status_code, None
    out = next(
        (p.get("valueBoolean")
         for p in r.json()["parameter"]
         if p.get("name") == "result"),
        None,
    )
    return 200, out


class TestV1MembershipNotUrlScoped:
    """V1 — url accepted but membership unscoped (documented gap,
    severity escalated: silent TRUE for out-of-set codes)."""

    def test_v10_cross_system_out_of_vs_true(self, fhir_client):
        """RxNorm metformin validates TRUE against the SNOMED
        isa-Diabetes value set (CURRENT, documented gap at
        fhir_api.py:2781). The expansion contains only SNOMED
        {73211009, 44054006}. Flip when membership evaluation lands:
        expect False (+ message)."""
        status, out = _result(
            fhir_client,
            url=VS_ISA_DM, code=METFORMIN, system=RXNORM_URI,
        )
        assert status == 200 and out is True

    def test_v11_same_system_unknown_false(self, fhir_client):
        """CONTRAST: same-system unknown code returns False — the
        existence check is sound; scoping is the gap (also the
        eventual flip-control for v10)."""
        status, out = _result(
            fhir_client,
            url=VS_ISA_DM, code="99999999", system=SNOMED_URI,
        )
        assert status == 200 and out is False

    def test_v12_inline_valueset_ignored(self, fhir_client):
        """POSTed valueSet resource (compose includes ONLY T2DM)
        validates metformin TRUE — the resource is accepted but
        membership is still the bare existence check."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": {
                    "resourceType": "ValueSet",
                    "url": "http://example.org/vs",
                    "compose": {"include": [{
                        "system": SNOMED_URI,
                        "concept": [{"code": T2DM}],
                    }]},
                }},
                {"name": "code", "valueCode": METFORMIN},
                {"name": "system", "valueUri": RXNORM_URI},
            ],
        }
        r = fhir_client.post(ENDPOINT, json=body)
        assert r.status_code == 200
        out = next(
            (p.get("valueBoolean")
             for p in r.json()["parameter"]
             if p.get("name") == "result"),
            None,
        )
        assert out is True


class TestVsValidateControls:
    """Controls: conformant shapes re-verified fresh."""

    def test_v20_in_vs_true(self, fhir_client):
        status, out = _result(
            fhir_client,
            url=VS_ISA_DM, code=T2DM, system=SNOMED_URI,
        )
        assert status == 200 and out is True

    def test_v21_parent_in_vs_true(self, fhir_client):
        status, out = _result(
            fhir_client,
            url=VS_ISA_DM, code=DM, system=SNOMED_URI,
        )
        assert status == 200 and out is True

    def test_v22_display_mismatch_false(self, fhir_client):
        r = fhir_client.get(
            ENDPOINT,
            params={
                "url": VS_ISA_DM, "code": T2DM,
                "system": SNOMED_URI, "display": "wrong",
            },
        )
        out = next(
            (p.get("valueBoolean")
             for p in r.json()["parameter"]
             if p.get("name") == "result"),
            None,
        )
        msg = next(
            (p.get("valueString")
             for p in r.json()["parameter"]
             if p.get("name") == "message"),
            None,
        )
        assert out is False
        assert msg is not None and "wrong" in msg

    def test_v23_display_match_true(self, fhir_client):
        status, out = _result(
            fhir_client,
            url=VS_ISA_DM, code=T2DM, system=SNOMED_URI,
            display="Type 2 diabetes mellitus",
        )
        assert status == 200 and out is True

    def test_v24_coding_form_post(self, fhir_client):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": VS_ISA_DM},
                {"name": "coding", "valueCoding": {
                    "system": SNOMED_URI, "code": T2DM}},
            ],
        }
        r = fhir_client.post(ENDPOINT, json=body)
        assert r.status_code == 200
        out = next(
            (p.get("valueBoolean")
             for p in r.json()["parameter"]
             if p.get("name") == "result"),
            None,
        )
        assert out is True

    def test_v25_codeable_concept_any_coding(self, fhir_client):
        """VS-05 QA-069: TRUE if ANY coding matches — the second
        coding (T2DM) satisfies even though the first is unknown."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": VS_ISA_DM},
                {"name": "codeableConcept", "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "99999999"},
                        {"system": SNOMED_URI, "code": T2DM},
                    ]}},
            ],
        }
        r = fhir_client.post(ENDPOINT, json=body)
        assert r.status_code == 200
        out = next(
            (p.get("valueBoolean")
             for p in r.json()["parameter"]
             if p.get("name") == "result"),
            None,
        )
        assert out is True

    def test_v26_missing_url_and_system_400(self, fhir_client):
        r = fhir_client.get(
            ENDPOINT, params={"code": T2DM}
        )
        assert r.status_code == 400

    def test_v27_unknown_system_400(self, fhir_client):
        r = fhir_client.get(
            ENDPOINT,
            params={
                "url": VS_ISA_DM, "code": T2DM,
                "system": "http://example.org/sys",
            },
        )
        assert r.status_code == 400
        assert (
            r.json().get("resourceType") == "OperationOutcome"
        )
