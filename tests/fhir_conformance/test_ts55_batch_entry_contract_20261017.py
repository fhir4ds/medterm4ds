"""TS-55 $batch per-entry contract matrix (2026-10-17).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261017).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the per-entry contract of Bundle/$batch — request.url forms,
entry-shape handling, response-shape fidelity, ordering, duplicates.
TS-04 covered bundle-level transaction/batch semantics (B1/B2); the
per-entry matrix was never swept.

BE1 (LOW, NEW, revised mid-run) — foreign-base absolute URLs 404 with
    a MISLEADING diagnostic. R4 §3.7.2 allows absolute request.urls;
    own-base absolutes resolve correctly (verified control), but a
    foreign-host absolute ('http://elsewhere.org/fhir/...') is
    answered '404 No stored CodeSystem resource with id snomed' —
    the client is told the RESOURCE is missing when the actual issue
    is the foreign base. First-draft BE1 (own-base absolute rejected)
    was a FALSE ALARM: my survey probed 'localhost' against a
    testserver-base client — retraction recorded in evolution.json.
    Fix shape: name the url/base mismatch in the diagnostic (or
    resolve the path component when the base is foreign — pick one
    documented rule). Flip pins be10/be11.

BE2 (LOW, NEW) — error entries carry the OperationOutcome TWICE.
    Failing entries populate BOTH entry.resource AND
    entry.response.outcome with identical OperationOutcome bodies.
    R4 §3.7.4: errors live in response.outcome; entry.resource is
    the success-response home. A consumer iterating resources sees
    the same error twice; a consumer checking only response.outcome
    works, but the redundant twin invites double-counting and
    divergent readers.
    Fix shape: emit response.outcome only for errors (drop
    entry.resource on failure), or document the belt-and-braces
    duplication. Flip pin be20.

Controls (fresh): entry ORDER preserved across mixed statuses;
    duplicates handled independently (3 identical GETs → 3×200);
    malformed entries fail per-entry (empty url 400 with both-method-
    and-url diagnostic; missing request 400; PATCH 405); empty bundle
    → 200 empty batch-response; success entries carry resource +
    status '200' (string form); operationOutcome embedded on error
    entries.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"
LOOKUP_OK = f"CodeSystem/$lookup?system={SNOMED_URI}&code={T2DM}"


def _batch(client, entries, btype="batch"):
    return client.post(
        "/fhir",
        json={"resourceType": "Bundle", "type": btype, "entry": entries},
    )


class TestBE1UrlForms:
    """BE1 — absolute rejected, nested stripped."""

    def test_be10_foreign_base_misleading_404(self, fhir_client):
        """Foreign-host absolute 404s with a STORAGE diagnostic
        ('No stored CodeSystem resource') — the foreign base is never
        named. Flip when the diagnostic names the url/base mismatch."""
        r = _batch(fhir_client, [
            {
                "request": {
                    "method": "GET",
                    "url": "http://elsewhere.org/fhir/CodeSystem/snomed",
                }
            }
        ])
        entry = r.json()["entry"][0]
        assert entry["response"]["status"] == "404"
        oc = entry.get("resource") or entry["response"]["outcome"]
        assert "CodeSystem resource" in oc["issue"][0]["diagnostics"]
        assert "elsewhere.org" not in oc["issue"][0]["diagnostics"]

    def test_be11_own_base_absolute_resolves(self, fhir_client):
        """CONTROL (false-alarm retraction): own-base absolute urls
        resolve (R4 §3.7.2 absolute-or-relative both honored)."""
        r = _batch(fhir_client, [
            {
                "request": {
                    "method": "GET",
                    "url": f"http://testserver/fhir/{LOOKUP_OK}",
                }
            }
        ])
        entry = r.json()["entry"][0]
        assert entry["response"]["status"] == "200"

    def test_be12_garbage_forms_404(self, fhir_client):
        """Non-spec forms ('//x', './x', 'fhir/x') 404 cleanly —
        control establishing the boundary is form-based, not
        everything-goes."""
        for url in (
            "//CodeSystem/snomed",
            "./CodeSystem/snomed",
            "fhir/CodeSystem/snomed",
        ):
            r = _batch(fhir_client, [
                {"request": {"method": "GET", "url": url}}
            ])
            assert r.json()["entry"][0]["response"]["status"] == "404", url


class TestBE2DoubleOutcome:
    """BE2 — error entries carry OperationOutcome twice."""

    def test_be20_resource_and_outcome_identical(self, fhir_client):
        """Failing entry: entry.resource AND response.outcome carry
        the same OperationOutcome. Flip when errors live in
        response.outcome only (or the duplication is documented)."""
        r = _batch(fhir_client, [
            {
                "request": {
                    "method": "GET",
                    "url": "CodeSystem/$lookup?system=x&code=1",
                }
            }
        ])
        entry = r.json()["entry"][0]
        assert entry["response"]["status"] == "400"
        assert entry.get("resource", {}).get("resourceType") == "OperationOutcome"
        assert (
            entry["response"].get("outcome", {}).get("resourceType")
            == "OperationOutcome"
        )
        assert entry["resource"] == entry["response"]["outcome"]

    def test_be21_success_entries_no_outcome(self, fhir_client):
        """Success entries carry resource + status only — the twin is
        error-specific (control isolating BE2)."""
        r = _batch(fhir_client, [
            {"request": {"method": "GET", "url": LOOKUP_OK}}
        ])
        entry = r.json()["entry"][0]
        assert entry["response"]["status"] == "200"
        assert "outcome" not in entry["response"]
        assert entry["resource"]["resourceType"] == "Parameters"


class TestTS55Controls:
    """Controls — per-entry contract, fresh."""

    def test_c10_order_preserved_mixed(self, fhir_client):
        r = _batch(fhir_client, [
            {"request": {"method": "GET", "url": LOOKUP_OK}},
            {
                "request": {
                    "method": "GET",
                    "url": "CodeSystem/$lookup?system=x&code=1",
                }
            },
            {
                "request": {
                    "method": "GET",
                    "url": f"CodeSystem/$lookup?system={SNOMED_URI}&code=99999999",
                }
            },
        ])
        statuses = [e["response"]["status"] for e in r.json()["entry"]]
        assert statuses == ["200", "400", "200"]

    def test_c11_duplicates_independent(self, fhir_client):
        r = _batch(fhir_client, [
            {"request": {"method": "GET", "url": LOOKUP_OK}} for _ in range(3)
        ])
        statuses = [e["response"]["status"] for e in r.json()["entry"]]
        assert statuses == ["200", "200", "200"]

    def test_c12_malformed_entries_fail_per_entry(self, fhir_client):
        r = _batch(fhir_client, [
            {"request": {"method": "GET", "url": ""}},
            {"resource": {"resourceType": "Parameters"}},
            {"request": {"method": "PATCH", "url": "CodeSystem/snomed"}},
        ])
        statuses = [e["response"]["status"] for e in r.json()["entry"]]
        assert statuses == ["400", "400", "405"]

    def test_c13_empty_bundle(self, fhir_client):
        r = fhir_client.post(
            "/fhir", json={"resourceType": "Bundle", "type": "batch"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "batch-response"
        assert body["entry"] == []

    def test_c14_post_entry_with_resource(self, fhir_client):
        """POST entries take the Parameters body from entry.resource
        (sibling of request) and echo the result resource."""
        r = _batch(fhir_client, [
            {
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": SNOMED_URI},
                        {"name": "code", "valueCode": T2DM},
                    ],
                },
                "request": {
                    "method": "POST",
                    "url": "CodeSystem/$lookup",
                },
            }
        ])
        entry = r.json()["entry"][0]
        assert entry["response"]["status"] == "200"
        assert entry["resource"]["resourceType"] == "Parameters"
