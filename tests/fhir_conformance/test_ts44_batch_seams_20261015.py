"""TS-44 fixbatch-era batch-dispatcher seams (2026-10-15).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261015).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: four fix-batches moved five operations to fail-loud contracts
(EA unknown-param 400s, H1 version 400s, W4 translate/closure, V2/V3
targetSystem, T1 reverse). The $batch dispatcher was progressively
wired for SOME of these (V2/V3/T1/H1 landed per fixbatch3/4) — this
suite maps which fail-loud contracts STILL bypass when the identical
request rides in a $batch entry, plus one inert-declared-param seam on
the direct route itself.

FA1 (MEDIUM, NEW) — targetCode declared but inert.
    R4 §4.9.13.1 declares targetCode (0..1, "used with reverse=true
    to look up a concept by its target code"). The param is IN
    _TRANSLATE_KNOWN_PARAMS (passes the W4 gate) but never consulted:
    targetCode=99999999 (nonexistent) returns the same 200 TRUE match
    as targetCode=44054006 (the actual match). Accepted-then-inert
    INSIDE the fail-loud era — worse than the EA family it belongs
    to: it survived the gate by being declared.
    Fix shape: filter matches to the requested targetCode (no match →
    result=false), or reject with 400 'not implemented' per §4.9.2.
    Flip pins f10/f11.

FA2 (MEDIUM, NEW) — the batch dispatcher bypasses the W4/U3 family.
    The identical GET that 400s direct (bogusParam on $lookup,
    $validate-code, $subsumes, $expand, $translate) returns 200
    entry-status inside a $batch bundle. The dispatcher's per-op
    branches carry the H1/V2/V3/T1 checks but never the
    reject_unknown_query_params calls — a batch client gets the
    silent-accept era for exactly the family the fix-batches retired.
    Cross-handler-helper-wiring class (count > 5 instances: fixbatch3
    precedent wired batch for url resolution, fixbatch4 for reverse).
    Fix shape: run the known-set rejection inside the dispatcher's
    GET branches (the params dict is already parsed). Flip pins
    f20-f24.

FA3 (re-confirmation) — XA1 still open at $extract/$search
    (engine=bogus 200s; iter-35 registration, op tail #3).

Controls (fresh): H1 version=9.9 IS enforced in batch (400) — the
    dispatcher's version wiring held (fixbatch2/3 era); direct-route
    W4 400s on all five ops re-verified; T1 reverse honored via batch
    (fixbatch4); P1/P2/P3 CapabilityStatement rows re-pinned (still
    open, unchanged).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
T2DM = "44054006"
DM = "73211009"
DM_ISA_URL = f"{SNOMED_URI}/{DM}?fhir_vs=isa"


def _result_of(params_json: dict):
    for p in params_json.get("parameter", []):
        if p.get("name") == "result":
            return p.get("valueBoolean")
    return None


def _batch_entry_status(fhir_client, url: str) -> tuple[int, str]:
    batch = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [{"request": {"method": "GET", "url": url}}],
    }
    r = fhir_client.post("/fhir", json=batch)
    return r.status_code, (
        r.json().get("entry", [{}])[0]
        .get("response", {})
        .get("status", "")
    )


class TestFA1TargetCodeInert:
    """FA1 — targetCode declared, passes the gate, changes nothing."""

    def test_f10_matching_targetcode_true(self, fhir_client):
        """targetCode equal to the actual match: TRUE (correct by
        coincidence — see f11)."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI,
                "targetCode": T2DM,
            },
        )
        assert r.status_code == 200
        assert _result_of(r.json()) is True

    def test_f11_nonexistent_targetcode_also_true(self, fhir_client):
        """THE FINDING: targetCode=99999999 (no such concept) returns
        the same TRUE match — the declared filter is never consulted.
        Flip when targetCode filters (result=false) or 400s."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI,
                "targetCode": "99999999",
            },
        )
        assert r.status_code == 200
        assert _result_of(r.json()) is True


class TestFA2BatchBypassesW4:
    """FA2 — identical request, opposite verdicts by transport."""

    @pytest.mark.parametrize(
        "url",
        [
            f"CodeSystem/$lookup?system={SNOMED_URI}&code={T2DM}&bogusParam=1",
            f"CodeSystem/$validate-code?system={SNOMED_URI}&code={T2DM}&bogusParam=1",
            f"CodeSystem/$subsumes?system={SNOMED_URI}&codeA={DM}&codeB={T2DM}&bogusParam=1",
            f"ValueSet/$expand?url={SNOMED_URI}%2F{DM}%3Ffhir_vs%3Disa&bogusParam=1",
            f"ConceptMap/$translate?system={SNOMED_URI}&code={T2DM}&targetSystem={ICD10CM_URI}&bogusParam=1",
        ],
        ids=["lookup", "validate", "subsumes", "expand", "translate"],
    )
    def test_f20_batch_unknown_param_200(self, fhir_client, url):
        """Direct route 400s bogusParam on all five ops; the batch
        entry 200s — the W4/U3 family never reached the dispatcher.
        Flip when the dispatcher runs the known-set rejection."""
        _, entry_status = _batch_entry_status(fhir_client, url)
        assert entry_status == "200"

    def test_f21_direct_rejects_control(self, fhir_client):
        """Premise control: the direct routes DO 400 (fixbatch2-4
        contract held outside $batch)."""
        for path, params in [
            ("/fhir/CodeSystem/$lookup",
             {"system": SNOMED_URI, "code": T2DM, "bogusParam": "1"}),
            ("/fhir/CodeSystem/$validate-code",
             {"system": SNOMED_URI, "code": T2DM, "bogusParam": "1"}),
        ]:
            r = fhir_client.get(path, params=params)
            assert r.status_code == 400, path

    def test_f22_batch_version_still_enforced(self, fhir_client):
        """Control: the H1 family DID land in the dispatcher (fixbatch
        era wiring) — version=9.9 400s via batch. The seam is
        op-check-specific, not a general batch laxness."""
        _, entry_status = _batch_entry_status(
            fhir_client,
            f"CodeSystem/$validate-code?system={SNOMED_URI}"
            f"&code={T2DM}&version=9.9",
        )
        assert entry_status == "400"

    def test_f23_batch_reverse_honored_control(self, fhir_client):
        """Control: T1 reverse rides batch (fixbatch4 wiring) — the
        reverse message appears in the batch entry."""
        batch = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "GET",
                        "url": (
                            f"ConceptMap/$translate?system={ICD10CM_URI}"
                            f"&code=E11&targetSystem={SNOMED_URI}"
                            f"&reverse=true"
                        ),
                    }
                }
            ],
        }
        r = fhir_client.post("/fhir", json=batch)
        body = r.json()["entry"][0]["resource"]
        msg = next(
            (
                p.get("valueString")
                for p in body.get("parameter", [])
                if p.get("name") == "message"
            ),
            "",
        )
        assert "reverse" in msg


class TestFA3XA1Reconfirmed:
    """FA3 — XA1 re-confirmation (engine param at $extract)."""

    def test_f30_extract_unknown_engine_200(self, fhir_client):
        """XA1 (iter 35, MEDIUM) still open: engine=bogus runs the
        default pipeline at 200. Registered; pinned here as the
        fixbatch-era tail marker."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": "patient has diabetes", "engine": "bogus"},
        )
        assert r.status_code == 200


class TestCSRowsRecheck:
    """Controls — the MD-01 rows unchanged after 4 fix-batches."""

    def test_c10_p1_searchparams_inert(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem", params={"url": SNOMED_URI}
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0  # P1 still open

    def test_c11_p2_no_self_link(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem", params={"url": SNOMED_URI}
        )
        assert r.json().get("link") is None  # P2 still open

    def test_c12_p3_read_404(self, fhir_client):
        r = fhir_client.get("/fhir/CodeSystem/snomed")
        assert r.status_code == 404  # P3 still open
