"""TS-65 conditional-request / caching machinery (2026-10-19).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261019c).
FRESH EVIDENCE — live probes executed this run; spec read live
(R4 http.html §3.1.0.1.3: Version Id ↔ weak ETag W/"…", Last-Updated
↔ Last-Modified); no prior-run results cited.

Scope: HTTP conditional-request machinery across the surface —
ETag / Last-Modified / Cache-Control / Content-Location emission and
If-None-Match handling. Never swept (the caching dimension of R4
HTTP binding).

CH1 (LOW, NEW) — zero conditional-request machinery.
    No cache-related header is emitted on ANY response (metadata,
    $lookup, $validate-code, $expand, search-type, 404 read) and
    conditional GETs (If-None-Match: *) are ignored (200 full body,
    never 304). R4's strong ETag requirements attach to the
    versioned-resource interaction set (read/vread/update) which
    this server does not implement (P3: read 404s) — so this is
    advertisement/perf family, not a hard conformance break. The
    practical pain: metadata is byte-stable per build (verified:
    two calls byte-identical) and $expand responses are
    deterministic ex-timestamp — both perfect 304 candidates — and
    canonical $search calls run 60-100s of model time per request
    with no cacheability signal at all. Fix shape: emit
    ETag: W/"<content-hash>" on metadata + static expansions and
    honor If-None-Match → 304; or register the no-caching posture
    in the TerminologyCapabilities narrative (Y1-adjacent).
    Pinned ch10-ch13.

Controls (fresh): responses byte-stable where determinism matters —
    metadata byte-identical across calls; $expand byte-identical
    including its timestamp within the same second and identical
    ex-timestamp after popping it (the deterministic-content
    PREMISE for 304 support); standard headers (content-type)
    present on all six surfaces probed.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"
CACHE_HEADERS = (
    "etag",
    "last-modified",
    "cache-control",
    "content-location",
    "location",
    "vary",
    "expires",
)


class TestCH1NoConditionalMachinery:
    """CH1 — no cache headers, conditional GETs ignored."""

    @pytest.mark.parametrize(
        "path,params",
        [
            ("/fhir/metadata", {}),
            ("/fhir/CodeSystem/$lookup",
             {"system": SNOMED_URI, "code": "44054006"}),
            ("/fhir/CodeSystem/$validate-code",
             {"system": SNOMED_URI, "code": "44054006"}),
            ("/fhir/ValueSet/$expand", {"url": DM_ISA_URL}),
            ("/fhir/CodeSystem", {"url": SNOMED_URI}),
            ("/fhir/CodeSystem/snomed", {}),
        ],
    )
    def test_ch10_no_cache_headers(self, fhir_client, path, params):
        """No cache-related header on any surface (200s and the 404
        read alike). Flip when ETag/Last-Modified emission lands."""
        r = fhir_client.get(path, params=params)
        present = {
            k for k in CACHE_HEADERS if k in r.headers
        }
        assert not present

    def test_ch11_conditional_get_ignored(self, fhir_client):
        """If-None-Match: * → 200 full body (304 machinery absent).
        Flip when conditional handling lands."""
        r = fhir_client.get(
            "/fhir/metadata", headers={"If-None-Match": "*"}
        )
        assert r.status_code == 200
        assert r.json().get("resourceType") == "CapabilityStatement"

    def test_ch12_metadata_byte_stable(self, fhir_client):
        """PREMISE control: metadata is byte-identical across calls —
        the deterministic-content basis for 304 support."""
        a = fhir_client.get("/fhir/metadata").content
        b = fhir_client.get("/fhir/metadata").content
        assert a == b

    def test_ch13_expand_deterministic_ex_timestamp(
        self, fhir_client
    ):
        """PREMISE control: $expand responses are identical after
        popping expansion.timestamp — deterministic content (another
        304 candidate)."""
        a = fhir_client.get(
            "/fhir/ValueSet/$expand", params={"url": DM_ISA_URL}
        ).json()
        b = fhir_client.get(
            "/fhir/ValueSet/$expand", params={"url": DM_ISA_URL}
        ).json()
        a["expansion"].pop("timestamp")
        b["expansion"].pop("timestamp")
        assert a == b


class TestTS65Controls:
    """Controls — the surfaces answer normally (baseline)."""

    def test_c10_content_type_present(self, fhir_client):
        for path, params in [
            ("/fhir/metadata", {}),
            ("/fhir/CodeSystem/$lookup",
             {"system": SNOMED_URI, "code": "44054006"}),
        ]:
            r = fhir_client.get(path, params=params)
            assert r.status_code == 200
            assert "fhir+json" in r.headers["content-type"]
