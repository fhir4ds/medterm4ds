"""TS-18 $translate targetSystem + error-shape matrix (2026-10-08).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261008c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: error-response matrix across all operations (status selection +
OperationOutcome shape) and, surfaced by it, the $translate
targetSystem contract. Distinct from TS-15 (map SELECTION: which
ConceptMap, url/version semantics — happy path there used
ICD10→SNOMED where targetSystem coincidentally matched) and TS-02
(reverse/one-of). This suite pins the TARGET dimension.

V2 (MEDIUM-HIGH, NEW) — targetSystem silently ignored.
    Requesting targetSystem=LOINC while translating SNOMED 44054006
    returns the ICD-10-CM E11 match: result=true, '1 matches found',
    match.concept.system=icd-10-cm — under a LOINC-targeted request.
    targetSystem=http://nowhere.org behaves identically (result=true,
    same match). GET and POST body alike. The client's declared
    translation TARGET filters nothing: any crosswalk the engine
    finds is returned regardless of direction/system asked. TS-15's
    happy path (E11 + targetSystem=SNOMED → SNOMED match) passed by
    coincidence — the fixture's only cross-SAB pair points that way.
    Fix shape: filter matches to the requested target system (0
    matches → result=false, not an error, per §4.9.13.3 semantics).
    Flip pins v10-v13.

V3 (MEDIUM, NEW) — required targetSystem unenforced.
    R4 §4.9.13.1 declares targetSystem 1..1 (REQUIRED). Omitting it
    entirely returns 200 with matches to ANY system — while the
    sibling required params on the SAME op (system, code) 422/400
    loudly, and every other op enforces its requireds. A client
    forgetting the target gets an unfiltered cross-system answer it
    may treat as its intended target. Fix shape: 400/422 when
    targetSystem absent (mirror the system/code enforcement). Flip
    pins v20/v21.

Controls (fresh) — error-shape matrix across ops: OperationOutcome
    severity=error on all 4xx; not-found code on 404s vs processing
    on 400/422s; POST body-shape 400s (Foo); lookup/subsumes missing
    params 422 (FastAPI typed) vs validate/expand/closure 400
    (handler checks) — the documented enforcement-channel split.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
LOINC_URI = "http://loinc.org"
T2DM = "44054006"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"


def _match_concept(params_json: dict):
    for p in params_json.get("parameter", []):
        if p.get("name") == "match":
            for part in p.get("part", []):
                if part.get("name") == "concept":
                    return part.get("valueCoding")
    return None


def _result(params_json: dict):
    for p in params_json.get("parameter", []):
        if p.get("name") == "result":
            return p.get("valueBoolean")
    return None


class TestV2TargetSystemIgnored:
    """V2 — the declared translation target filters nothing."""

    def test_v10_loinc_target_returns_icd10cm(self, fhir_client):
        """targetSystem=LOINC + SNOMED T2DM → ICD-10-CM E11 match at
        200 'result=true'. Flip when matches are target-filtered
        (result=false + zero matches for an unmapped target)."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetSystem": LOINC_URI,
            },
        )
        assert r.status_code == 200
        assert _result(r.json()) is True
        assert _match_concept(r.json())["system"] == ICD10CM_URI

    def test_v11_unknown_target_same_match(self, fhir_client):
        """targetSystem=http://nowhere.org — identical TRUE + ICD-10-CM
        match. Flip with V2's filter."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetSystem": "http://nowhere.org",
            },
        )
        assert r.status_code == 200
        assert _result(r.json()) is True
        assert _match_concept(r.json())["system"] == ICD10CM_URI

    def test_v12_post_body_target_ignored(self, fhir_client):
        """POST Parameters targetSystem=LOINC — same ignore. Flip with
        V2's filter."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
                {"name": "targetSystem", "valueUri": LOINC_URI},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code == 200
        assert _match_concept(r.json())["system"] == ICD10CM_URI

    def test_v13_coincidental_happy_path_still_true(self, fhir_client):
        """Control-within-finding: the TS-15 happy path (targetSystem
        SNOMED + ICD10CM E11) still returns the SNOMED match — proof
        the ignore is invisible when target and available crosswalk
        happen to agree."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI,
            },
        )
        assert r.status_code == 200
        assert _result(r.json()) is True
        assert _match_concept(r.json())["system"] == SNOMED_URI


class TestV3RequiredTargetSystem:
    """V3 — R4 §4.9.13.1 targetSystem 1..1 unenforced."""

    def test_v20_get_missing_target_200(self, fhir_client):
        """No targetSystem at all → 200 with cross-system matches.
        Flip when required-enforcement lands (422/400)."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={"system": SNOMED_URI, "code": T2DM},
        )
        assert r.status_code == 200
        assert _result(r.json()) is True

    def test_v21_post_missing_target_200(self, fhir_client):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code == 200
        assert _result(r.json()) is True

    def test_v22_sibling_requireds_enforced(self, fhir_client):
        """Premise control: system and code ARE enforced on the same
        op — the targetSystem gap is inconsistent with its siblings."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate", params={"code": T2DM}
        )
        assert r.status_code == 422
        r2 = fhir_client.get(
            "/fhir/ConceptMap/$translate", params={"system": SNOMED_URI}
        )
        assert r2.status_code == 422


class TestErrorShapeControls:
    """Controls — the cross-op error matrix, fresh."""

    @pytest.mark.parametrize(
        "method,path,kw,status",
        [
            ("get", "/fhir/CodeSystem/$lookup",
             {"params": {"code": T2DM}}, 422),
            ("get", "/fhir/CodeSystem/$lookup",
             {"params": {"system": "http://x.org", "code": "1"}}, 400),
            ("get", "/fhir/CodeSystem/$validate-code",
             {"params": {"code": T2DM}}, 400),
            ("get", "/fhir/CodeSystem/$subsumes",
             {"params": {"system": SNOMED_URI, "codeA": "73211009"}}, 422),
            ("get", "/fhir/ValueSet/$expand", {}, 400),
            ("get", "/fhir/ValueSet/$expand",
             {"params": {"url": "http://x.org/vs?x=1"}}, 400),
            ("get", "/fhir/ValueSet/$expand",
             {"params": {"url": DM_ISA_URL, "count": 0}}, 422),
            ("post", "/fhir/CodeSystem/$closure",
             {"json": {"resourceType": "Parameters", "parameter": [
                 {"name": "concept", "valueCoding": {
                     "system": SNOMED_URI, "code": T2DM}}]}}, 400),
            ("post", "/fhir/ValueSet/$expand",
             {"json": {"resourceType": "Foo"}}, 400),
            ("post", "/fhir/ConceptMap/$translate",
             {"json": {"resourceType": "Foo"}}, 400),
            ("get", "/fhir/CodeSystem/zzz", {}, 404),
            ("get", "/fhir/Bogus", {}, 404),
        ],
    )
    def test_c10_error_matrix(
        self, fhir_client, method, path, kw, status
    ):
        """Status-code selection per op+cause (documented
        enforcement-channel split: typed 422s vs handler 400s vs
        router 404s)."""
        r = getattr(fhir_client, method)(path, **kw)
        assert r.status_code == status

    @pytest.mark.parametrize(
        "method,path,kw,issue_code",
        [
            ("get", "/fhir/CodeSystem/zzz", {}, "not-found"),
            ("get", "/fhir/Bogus", {}, "not-found"),
            ("get", "/fhir/CodeSystem/$lookup",
             {"params": {"system": "http://x.org", "code": "1"}},
             "processing"),
        ],
    )
    def test_c11_operationoutcome_codes(
        self, fhir_client, method, path, kw, issue_code
    ):
        """404s carry not-found; 400/422s carry processing; severity
        always error."""
        r = getattr(fhir_client, method)(path, **kw)
        body = r.json()
        assert body["resourceType"] == "OperationOutcome"
        issue = body["issue"][0]
        assert issue["severity"] == "error"
        assert issue["code"] == issue_code

    def test_c12_error_path_fhir_mime_xml(self, fhir_client):
        """Errors honor Accept (TS-01 contract re-verified at the
        matrix level)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/zzz", headers={"Accept": "application/fhir+xml"}
        )
        assert r.status_code == 404
        assert "fhir+xml" in r.headers["content-type"]
        assert r.text.startswith("<?xml")
