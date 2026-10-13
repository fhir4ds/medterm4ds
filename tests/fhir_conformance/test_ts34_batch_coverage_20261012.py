"""TS-34 $batch op-coverage matrix (2026-10-12).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261012d).
FRESH EVIDENCE — live batch probes executed this run; no prior-run
results cited.

Scope: which advertised operations dispatch from $batch entries —
the coverage matrix (B1/B2 pinned bundle SEMANTICS and metadata
reachability; the op-coverage matrix was never enumerated).

BC1 (LOW-MED, NEW) — custom ops unreachable from $batch, with a
    false diagnostic.
    $search and $extract (both ADVERTISED under CodeSystem in the
    CapabilityStatement) return per-entry 404 from $batch — and the
    outcome diagnostic says "Unknown operation '$search'. See
    /fhir/metadata for the list of supported operations" — which is
    FALSE: /fhir/metadata advertises exactly this operation. The
    batch dispatcher's op table simply lacks the two custom ops.
    Batch clients composing search+lookup workflows (the natural
    terminology batch: search for codes, then validate them) cannot
    reach half the server's surface.
    Fix shape: route $search/$extract in _dispatch_batch_operation
    (both are GET+POST ops; the direct handlers exist), or — if
    deliberately excluded — correct the diagnostic and declare the
    batch scope. Flip pins b10-b12.

Controls (fresh — the coverage matrix, first full enumeration):
    The 6 terminology ops dispatch from batch GET entries (lookup,
    validate-code, subsumes, translate, expand, vs-validate-code all
    200); $closure dispatches via its POST-entry form (Parameters
    resource in the entry — the batch side of its direct-POST-only
    contract); metadata / read / search-type per-entry 404s
    re-pinned (B2's finding at matrix level).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
DM = "73211009"
T2DM = "44054006"


def _batch(client, entries):
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {"request": {"method": m, "url": u}, **({"resource": r} if r else {})}
            for m, u, r in entries
        ],
    }
    r = client.post("/fhir", json=bundle)
    assert r.status_code == 200
    return [
        e.get("response", {}).get("status")
        for e in r.json().get("entry", [])
    ]


class TestBC1CustomOpsUnreachable:
    """BC1 — $search/$extract 404 from batch w/ false diagnostic."""

    @pytest.mark.parametrize(
        "url",
        [
            "CodeSystem/$search?query=diabetes"
            "&searchMode=canonical&count=1",
            "CodeSystem/$extract?text=diabetes",
        ],
    )
    def test_b10_custom_op_404(self, fhir_client, url):
        """Both advertised custom ops 404 per-entry from batch.
        Flip when dispatched (200) or the exclusion is declared."""
        (status,) = _batch(fhir_client, [("GET", url, None)])
        assert status == "404"

    def test_b11_diagnostic_claims_unknown(self, fhir_client):
        """The 404 outcome says 'Unknown operation' — false: the CS
        advertises it. Flip with the dispatch fix (or a truthful
        exclusion diagnostic)."""
        bundle = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [{"request": {
                "method": "GET",
                "url": "CodeSystem/$search?query=x&searchMode=canonical",
            }}],
        }
        r = fhir_client.post("/fhir", json=bundle)
        outcome = r.json()["entry"][0]["response"]["outcome"]
        diag = outcome["issue"][0]["diagnostics"]
        assert "Unknown operation" in diag

    def test_b12_metadata_advertises_both(self, fhir_client):
        """Premise control: /fhir/metadata advertises $search and
        $extract under CodeSystem — the diagnostic is provably
        false."""
        m = fhir_client.get("/fhir/metadata").json()
        ops = {
            o["name"]
            for rest in m["rest"]
            for res in rest["resource"]
            if res["type"] == "CodeSystem"
            for o in res.get("operation", [])
        }
        assert {"search", "extract"} <= ops


class TestBatchCoverageControls:
    """Controls — the op-coverage matrix, fresh."""

    def test_c10_six_term_ops_dispatch(self, fhir_client):
        entries = [
            ("GET", f"CodeSystem/$lookup?system={SNOMED_URI}&code={T2DM}", None),
            ("GET", f"CodeSystem/$validate-code?system={SNOMED_URI}&code={T2DM}", None),
            ("GET", f"CodeSystem/$subsumes?system={SNOMED_URI}&codeA={DM}&codeB={T2DM}", None),
            ("GET", f"ConceptMap/$translate?system={ICD10CM_URI}&code=E11&targetSystem={SNOMED_URI}", None),
            ("GET", f"ValueSet/$expand?url={SNOMED_URI}/{DM}?fhir_vs=isa", None),
            ("GET", f"ValueSet/$validate-code?url={SNOMED_URI}/{DM}?fhir_vs=isa&system={SNOMED_URI}&code={T2DM}", None),
        ]
        statuses = _batch(fhir_client, entries)
        assert statuses == ["200"] * 6

    def test_c11_closure_post_entry(self, fhir_client):
        """$closure dispatches via its POST-entry form (Parameters
        resource in the entry)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "name", "valueString": "ts34-c11"},
                {"name": "concept", "valueCoding": {
                    "system": SNOMED_URI, "code": T2DM,
                }},
            ],
        }
        (status,) = _batch(
            fhir_client, [("POST", "CodeSystem/$closure", body)]
        )
        assert status == "200"

    @pytest.mark.parametrize(
        "url",
        ["metadata", "CodeSystem/snomed", "CodeSystem"],
    )
    def test_c12_reachability_404s(self, fhir_client, url):
        """B2 re-pinned at matrix level: metadata / read / search
        unreachable from batch entries."""
        (status,) = _batch(fhir_client, [("GET", url, None)])
        assert status == "404"
