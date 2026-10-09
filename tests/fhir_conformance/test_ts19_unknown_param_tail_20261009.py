"""TS-19 unknown-param coverage tail + T1 reverse no-op resharpened (2026-10-09).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261009).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: after three fix-batches, where does the EA-family unknown-param
rejection still NOT reach? Plus the R4 §3.2.0.1 JSON-tolerance and
content-type matrices (never swept systematically) as controls, and a
resharpened pin of T1 (reverse no-op, open since 20261003b).

W1-not/TRANSLATE (MEDIUM, NEW REGISTRATION of known residue) —
    $translate accepts arbitrary unknown params on BOTH transports
    (GET bogusParam=1 → 200; POST body parameter zzz → 200) and
    $closure POST accepts unknown parameters (zzz → 200; L1's typo-
    param WIPES closure is the destructive variant of this gap).
    fixbatch3 extended the rejection family to $lookup/
    $validate-code/$subsumes; translate/closure were never covered —
    U3's op-scoping persists at these two ops. Registered as W4 to
    keep the family tail visible.
    Fix shape: _TRANSLATE_KNOWN_PARAMS / _CLOSURE_KNOWN_PARAMS via
    reject_unknown_query_params + body-side param-name check. Flip
    pins w10-w13.

T1-RESHARPENED (MEDIUM, re-pinned) — reverse=true is a BYTE-IDENTICAL
    no-op: E11→SNOMED forward vs reverse responses are byte-equal; the
    reverse direction (T2DM system + ICD10CM target + reverse=true,
    which should consult the map as target-of) also returns TRUE 1
    match. Registered 20261003b; post-fixbatch3 (targetSystem now
    binding) the no-op is cleanly isolated: the param is accepted,
    changes nothing. Fix shape: wire reverse into get_code_mappings
    (swap direction) or 400 'reverse not supported'. Flip pins t10/t11.

Controls (fresh) — JSON tolerance per R4 §3.2.0.1 (unknown top-level
    fields, unknown resource fields ignored, 200s preserved); content-
    type matrix (garbage-as-json 422, json-as-text/plain 422,
    fhir+json;charset accepted); _format full-MIME accepted;
    cases.json live-contract check (34/34 real cases match; the one
    raw-loop mismatch is the runner's model-availability skip case).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
T2DM = "44054006"


def _translate_body(system, code, target):
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": system},
            {"name": "code", "valueCode": code},
            {"name": "targetSystem", "valueUri": target},
        ],
    }


class TestW4TranslateClosureUnknownParams:
    """W4 — EA-family rejection still absent at $translate/$closure."""

    def test_w10_translate_get_unknown_param(self, fhir_client):
        """GET bogusParam on $translate → 200 (U3's tail: fixbatch3
        covered lookup/validate-code/subsumes only). Flip when
        _TRANSLATE_KNOWN_PARAMS rejection lands."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetSystem": ICD10CM_URI, "bogusParam": "1",
            },
        )
        assert r.status_code == 200

    def test_w11_translate_post_unknown_param(self, fhir_client):
        """POST body parameter zzz → 200. Flip with the same known-set
        (body-side name check)."""
        body = _translate_body(SNOMED_URI, T2DM, ICD10CM_URI)
        body["parameter"].append(
            {"name": "zzz", "valueString": "x"}
        )
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code == 200

    def test_w12_closure_post_unknown_param(self, fhir_client):
        """$closure POST zzz → 200 (non-destructive variant of L1's
        typo-wipe). Flip when _CLOSURE_KNOWN_PARAMS lands."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "name", "valueString": "ts19-w12-probe"},
                {"name": "zzz", "valueString": "x"},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$closure", json=body)
        assert r.status_code == 200

    def test_w13_siblings_reject(self, fhir_client):
        """Premise control: the fixbatch3 ops DO reject (the family
        boundary is translate/closure, not everything)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "bogusParam": "1",
            },
        )
        assert r.status_code == 400


class TestT1ReverseNoOp:
    """T1 resharpened — reverse=true changes nothing, byte-level."""

    def test_t10_forward_reverse_identical(self, fhir_client):
        """E11→SNOMED: forward vs reverse=true responses byte-equal —
        the param is accepted and ignored. Flip when reverse is wired
        (direction swapped) or rejected (400)."""
        fwd = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI,
            },
        )
        rev = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI, "reverse": "true",
            },
        )
        assert fwd.status_code == rev.status_code == 200
        assert fwd.content == rev.content

    def test_t11_reverse_direction_also_true(self, fhir_client):
        """T2DM system + ICD10CM target + reverse=true (the
        consult-as-target direction) → TRUE 1 match: identical to its
        forward form; a true reverse would still match here via the
        symmetric pair — this pin guards that SOME semantics arrive
        (flip expectations when reverse is wired)."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetSystem": ICD10CM_URI, "reverse": "true",
            },
        )
        assert r.status_code == 200
        result = next(
            p.get("valueBoolean")
            for p in r.json()["parameter"]
            if p.get("name") == "result"
        )
        assert result is True


class TestJSONToleranceControls:
    """Controls — R4 §3.2.0.1 unknown-element tolerance, fresh."""

    def test_c10_unknown_top_level_field(self, fhir_client):
        body = _translate_body(SNOMED_URI, T2DM, ICD10CM_URI)
        body["unknownTopLevel"] = 42
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code == 200

    def test_c11_unknown_resource_field(self, fhir_client):
        body = {
            "resourceType": "Parameters",
            "unknownResourceField": True,
            "parameter": _translate_body(
                SNOMED_URI, T2DM, ICD10CM_URI
            )["parameter"],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code == 200

    def test_c12_content_type_matrix(self, fhir_client):
        """garbage-as-json 422; json-as-text/plain 422; fhir+json
        with charset accepted (documented channel behavior)."""
        r1 = fhir_client.post(
            "/fhir/ConceptMap/$translate",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert r1.status_code == 422
        r2 = fhir_client.post(
            "/fhir/ConceptMap/$translate",
            content=b'{"resourceType":"Parameters"}',
            headers={"Content-Type": "text/plain"},
        )
        assert r2.status_code == 422
        r3 = fhir_client.post(
            "/fhir/ConceptMap/$translate",
            json=_translate_body(SNOMED_URI, T2DM, ICD10CM_URI),
            headers={
                "Content-Type": "application/fhir+json; charset=utf-8"
            },
        )
        assert r3.status_code == 200

    def test_c13_format_full_mime(self, fhir_client):
        """_format accepts the full MIME form, not just the short
        code."""
        r = fhir_client.get(
            "/fhir/metadata",
            params={"_format": "application/fhir+json"},
        )
        assert r.status_code == 200
        assert "fhir+json" in r.headers["content-type"]

    def test_c14_cases_json_live_contract(self, fhir_client):
        """cases.json (the runner's driver) matches the live contract:
        every non-skipped case's expected status/resource-type holds.
        Fresh-run equivalent of the runner's own assertions (the one
        model-availability case excluded here as the runner skips
        it)."""
        import json
        from pathlib import Path

        cases = json.loads(
            (
                Path(__file__).parent / "cases.json"
            ).read_text()
        )["cases"]
        checked = 0
        for case in cases:
            if case.get("skip"):
                continue
            if case.get("skip_if_model_available"):
                continue  # runner applies availability logic
            checked += 1
            method = case.get("method", "GET").lower()
            kw = {}
            if case.get("params"):
                kw["params"] = case["params"]
            if method == "post" and case.get("body"):
                kw["json"] = case["body"]
            r = getattr(fhir_client, method)(case["path"], **kw)
            exp = case.get("expected_status")
            exp_rt = case.get("expected_resource_type")
            if exp is not None:
                assert r.status_code == exp, case["id"]
            if exp_rt is not None:
                rt = r.json().get("resourceType")
                assert rt == exp_rt, case["id"]
        assert checked >= 30  # the driver is substantial, not gutted
