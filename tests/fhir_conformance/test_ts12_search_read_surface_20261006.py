"""TS-12 search/read interaction surface (2026-10-06).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261006d).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the type-level search and instance-level read interactions,
which the CapabilityStatement advertises for CodeSystem, ValueSet and
ConceptMap. Iter-13 (B2) covered batch-entry reachability; this sweep
covers the direct HTTP surface + the Bundle paging contract.

Context (documented intent, not re-litigated): the server has no
persisted resources — read 404s and search returns an empty searchset
BY DESIGN (search_resource handler, fhir_api.py:4854-4868; QC-330
shape notes). Findings below are about the CONTRACT AROUND that
design, not the design itself.

Survey (fresh, this run):
* search-type on all three types: 200 searchset Bundle, total=0, no
  entry key (QC-330 omission), spec search params (url/version/name/
  title/status) accepted structurally.
* read on all three types: 404 + OperationOutcome not-found with an
  actionable diagnostic (terminology-ops-only message).
* _summary (true/bogus) and _count on search: accepted, no behavioral
  effect — MAY-support per R4 §2.1.0.11 general parameters; pinned as
  controls (silently-ignored, not rejected).

R1 (MEDIUM, NEW) — searchset Bundle lacks the `self` link.
    R4 §3.1.0.14 (Paging): the searchset Bundle example annotates the
    self link "All searches SHALL return this value." The stub
    searchset omits `link` entirely (keys: resourceType/type/total
    only). A conformant paging client cannot anchor page traversal;
    the omission also makes future real-search adoption silently
    non-conformant. Fix: emit link:[{relation: self, url: <request
    url>}] in search_resource's Bundle.

R2 (LOW-MED, NEW) — POST _search endpoint 405s.
    R4 §3.1.0.13 search: "Servers SHALL support this endpoint" for
    POST [base]/[type]/_search (form-encoded or Parameters body).
    Both encodings 405 (Method Not Allowed, OperationOutcome-wrapped).
    Fix: alias the search handler at POST /fhir/{type}/_search or
    declare the GET-only restriction in the CapabilityStatement.

R3 (LOW, NEW) — HEAD on read 405s.
    R4 §3.1.0.2 read: "A HEAD request can also be used." HEAD
    /fhir/CodeSystem/snomed → 405. Rides with the stub-read design;
    fix when/iff read gains real semantics (or route HEAD→GET).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
VS_URL = "http://snomed.info/sct/73211009?fhir_vs=isa"
CM_URL = "urn:medterm4ds:crosswalk:loinc-to-snomed"

ALL_TYPES = [
    ("CodeSystem", {"url": SNOMED_URI}),
    ("ValueSet", {"url": VS_URL}),
    ("ConceptMap", {"url": CM_URL}),
]


class TestR1SelfLinkMissing:
    """R1 — searchset Bundle SHALL carry a self link (§3.1.0.14)."""

    @pytest.mark.parametrize("rtype,params", ALL_TYPES)
    def test_r10_searchset_lacks_self_link(self, fhir_client, rtype, params):
        """Every search-type response omits Bundle.link. WHEN the fix
        lands (self link emitted), flip to relation=self presence +
        url echoing the request."""
        r = fhir_client.get(f"/fhir/{rtype}", params=params)
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"
        assert bundle.get("link") is None, (
            f"{rtype} searchset now carries link={bundle['link']} — "
            "flip this pin to require relation=self."
        )

    def test_r11_self_link_shape_contract(self, fhir_client):
        """Pins the FIX shape: when self lands it MUST be
        link[0].relation='self' with a url carrying the effective
        search parameters. Currently asserts absence (see r10); the
        flip instruction lives in r10's message."""
        r = fhir_client.get("/fhir/CodeSystem", params={"url": SNOMED_URI})
        assert r.status_code == 200
        assert "link" not in r.json()


class TestR2PostSearchEndpoint:
    """R2 — POST _search SHALL be supported (§3.1.0.13)."""

    def test_r20_form_encoded_search(self, fhir_client):
        """POST /fhir/CodeSystem/_search with form body → currently
        405. WHEN the endpoint lands, flip to 200 searchset."""
        r = fhir_client.post(
            "/fhir/CodeSystem/_search", data={"url": SNOMED_URI}
        )
        assert r.status_code == 405, (
            f"POST _search now {r.status_code} — flip this pin to a "
            "200 searchset expectation."
        )

    def test_r21_parameters_body_search(self, fhir_client):
        """Parameters-body variant of the same endpoint."""
        r = fhir_client.post(
            "/fhir/CodeSystem/_search",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "url", "valueUri": SNOMED_URI}
                ],
            },
        )
        assert r.status_code == 405

    def test_r22_get_search_control(self, fhir_client):
        """Control: GET type-level search works — the 405 is
        POST-specific, not a broken search surface."""
        r = fhir_client.get(
            "/fhir/CodeSystem", params={"url": SNOMED_URI}
        )
        assert r.status_code == 200
        assert r.json()["type"] == "searchset"


class TestR3HeadRead:
    """R3 — HEAD on read 405s (spec allows HEAD)."""

    def test_r30_head_read_405(self, fhir_client):
        r = fhir_client.head("/fhir/CodeSystem/snomed")
        assert r.status_code == 405, (
            f"HEAD read now {r.status_code} — flip this pin (expect "
            "GET-equivalent status, empty body)."
        )

    def test_r31_get_read_control(self, fhir_client):
        """Control: GET read reaches the stub's documented 404
        not-found (terminology-ops-only design)."""
        r = fhir_client.get("/fhir/CodeSystem/snomed")
        assert r.status_code == 404
        assert r.json()["resourceType"] == "OperationOutcome"
        assert r.json()["issue"][0]["code"] == "not-found"


class TestSearchSurfaceControls:
    """Controls: documented stub behavior, re-verified fresh."""

    @pytest.mark.parametrize("rtype,params", ALL_TYPES)
    def test_s10_search_params_accepted(self, fhir_client, rtype, params):
        """Spec search params accepted structurally; 200 searchset
        total=0; no entry key (QC-330 valueless-omission)."""
        r = fhir_client.get(f"/fhir/{rtype}", params=params)
        assert r.status_code == 200
        j = r.json()
        assert j["type"] == "searchset"
        assert j["total"] == 0
        assert "entry" not in j

    def test_s11_summary_ignored_not_rejected(self, fhir_client):
        """_summary (true and bogus) silently ignored — MAY-support
        per §2.1.0.11; consistent GET-ignore behavior (mirrors
        unknown-param tolerance pinned in TS-10)."""
        for value in ("true", "bogus"):
            r = fhir_client.get(
                "/fhir/CodeSystem",
                params={"url": SNOMED_URI, "_summary": value},
            )
            assert r.status_code == 200
            assert r.json()["total"] == 0

    def test_s12_count_ignored(self, fhir_client):
        """_count accepted, no effect (MAY-support)."""
        r = fhir_client.get(
            "/fhir/CodeSystem",
            params={"url": SNOMED_URI, "_count": "1"},
        )
        assert r.status_code == 200
        assert "entry" not in r.json()

    def test_s13_read_404_actionable_diagnostic(self, fhir_client):
        """The stub-read 404 explains WHY (terminology-ops-only) —
        actionable for integrators."""
        r = fhir_client.get("/fhir/ValueSet/some-vs")
        assert r.status_code == 404
        diag = r.json()["issue"][0]["diagnostics"]
        assert "terminology" in diag.lower()
