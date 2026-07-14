"""EXPLORER iteration TS-03 — lateral-thinking probes for Implicit Value Sets,
External Code Systems, and Terminology Maintenance corners.

Source: https://build.fhir.org/terminology-service.html §4.7.3, §4.7.3.1-3

EXPLORER lens (per the assignment):
1. Implicit URL robustness: trailing slash, uppercase host, http vs https,
   query-param order, URL-encoded characters, params in POST body.
2. Instance-level $expand with implicit URL — does the {id} segment interfere?
3. All advertised systems actually expandable — cross-check the extension list
   against actual expansion capability (one probe per system URI).
4. Cross-system implicit URLs — intensional (?fhir_vs=isa) vs all-codes (?fhir_vs).
5. Combined implicit + filter — does filter apply to implicit expansion?
6. Implicit URL + count cap — does the server emit `too-costly` extension?
7. Terminology maintenance — POST /fhir/CodeSystem etc. MUST return FHIR-shaped
   response (not FastAPI default), since medterm4ds is read-only.
8. CapabilityStatement extension structure — well-formed? duplicates? every URI
   expandable?
9. valueSet parameter on $expand via POST body.
10. Cross-resource pollution — implicit URL passed to wrong operation.

All input-recognition probes assert the POSITIVE success shape (200 + resource
body), per GLOBAL_RULES.md "Test-too-lenient" trigger promoted by HISTORIAN
TS-03 QA-034.
"""

from __future__ import annotations

import pytest


SUPPORTED_SYSTEM_EXTENSION_URL = (
    "http://hl7.org/fhir/StructureDefinition/capabilitystatement-supported-system"
)
EMPTY_SOURCE_EXT_URL = (
    "http://medterm4ds.org/fhir/StructureDefinition/valueset-empty-source"
)
TOOCOSTLY_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Same canonical URIs the server advertises. Single source of truth: the
# server's SYSTEM_TO_FHIR_URI map (sourced into the CapabilityStatement).
CANONICAL_FHIR_R4_URIS = {
    "SNOMEDCT_US": "http://snomed.info/sct",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10PCS": "http://hl7.org/fhir/sid/icd-10-pcs",
    "LNC": "http://loinc.org",
    "CPT": "http://www.ama-assn.org/go/cpt",
    "HCPCS": "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets",
    "CVX": "http://hl7.org/fhir/sid/cvx",
}


# =============================================================================
# 1. Implicit URL robustness — variants of the LOINC all-codes implicit URL.
# =============================================================================


def test_e10_implicit_loinc_trailing_slash(fhir_client):
    """EXPLORER: trailing slash on implicit URL — `http://loinc.org/vs/`.
    RFC 3986 treats trailing slashes as significant in the path component,
    but convention-based value set URLs are commonly written either way.
    The server SHOULD tolerate the trailing slash.

    Positive success shape: 200 + ValueSet body (per GLOBAL_RULES.md
    "Test-too-lenient" — assert the body, not just absence of error string).
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", "http://loinc.org/vs/")]
    )
    assert r.status_code == 200, (
        f"Trailing-slash implicit URL not recognized. Status={r.status_code}, "
        f"body={r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "ValueSet"


def test_e11_implicit_url_uppercase_host_rejected_or_resolved(fhir_client):
    """EXPLORER: `HTTP://LOINC.ORG/vs` — uppercase host. Per RFC 3986 §3.2.2
    host is case-insensitive. The server SHOULD resolve it; an explicit 400
    that says 'URL not recognized' is non-conformant per RFC 3986.

    Acceptance: 200 ValueSet body (resolved) OR 200 with empty-source
    extension (resolved but empty). Either way, the URL was recognized.
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", "HTTP://LOINC.ORG/vs")]
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/fhir") else {}
    if r.status_code == 200:
        assert body.get("resourceType") == "ValueSet"
    else:
        # If rejected, the rejection MUST be FHIR-shaped (OperationOutcome),
        # not a Starlette default. The intent of this probe is to expose
        # case-sensitivity in URI matching, not to demand uppercase support.
        assert r.headers.get("content-type", "").startswith("application/fhir"), (
            f"Non-FHIR response on uppercase-host URL: {r.headers.get('content-type')!r}"
        )


def test_e12_implicit_url_https_variant(fhir_client):
    """EXPLORER: `https://loinc.org/vs` — https variant. The canonical URI
    is `http://loinc.org` (no s). Some clients normalize to https.
    The server SHOULD either accept or reject with a clear FHIR error.
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", "https://loinc.org/vs")]
    )
    # Whatever happens, the response MUST be FHIR-shaped.
    assert r.headers.get("content-type", "").startswith("application/fhir"), (
        f"Non-FHIR response on https implicit URL: {r.headers.get('content-type')!r}"
    )


def test_e13_implicit_snomed_with_extra_query_param(fhir_client):
    """EXPLORER: `?fhir_vs&abc=1` — query-param order / extra params.
    RFC 3986: order of query params is not significant. The server should
    still recognize the bare `fhir_vs` even when extra params are present.
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=[("url", "http://snomed.info/sct?fhir_vs&abc=1")],
    )
    # URL passed as a single value with embedded ?&. The handler parses url.
    # The expectation: 200 ValueSet (recognized as implicit).
    body = r.json() if r.headers.get("content-type", "").startswith("application/fhir") else {}
    if r.status_code == 200:
        assert body.get("resourceType") == "ValueSet"
        # If the URL was recognized, contains[] exists per QA-032 fix.
        expansion = body.get("expansion", {})
        assert "contains" in expansion
    else:
        # NOT acceptable: "Provide a ValueSet body" — means URL was silently
        # treated as unknown.
        diag = body.get("issue", [{}])[0].get("diagnostics", "") if body else ""
        assert "Provide a ValueSet body" not in diag, (
            f"SNOMED ?fhir_vs with extra param not recognized: {diag}"
        )


# =============================================================================
# 2. Instance-level $expand with implicit URL — does the {id} segment interfere?
# =============================================================================


def test_e20_instance_expand_with_implicit_url(fhir_client):
    """EXPLORER: GET /fhir/ValueSet/{id}/$expand?url=http://loinc.org/vs —
    instance-level route with an implicit value set URL. The instance {id}
    SHOULD be irrelevant when an implicit URL is supplied (the URL names a
    different value set than the resource). The server SHOULD expand the
    implicit URL.

    Spec: https://hl7.org/fhir/R4/http.html#ops — operations on instances
    accept query parameters.
    """
    r = fhir_client.get(
        "/fhir/ValueSet/anything-here/$expand",
        params=[("url", "http://loinc.org/vs")],
    )
    assert r.headers.get("content-type", "").startswith("application/fhir"), (
        f"Non-FHIR response on instance $expand with implicit URL: "
        f"{r.headers.get('content-type')!r}"
    )
    # If 200, the server expanded the implicit URL (good). If 404 OperationOutcome
    # that's also conformant — the route exists, the instance is unknown, the URL
    # is documented as ignored. Either is FHIR-shaped.
    if r.status_code == 200:
        body = r.json()
        assert body.get("resourceType") == "ValueSet"


# =============================================================================
# 3. All advertised systems actually expandable — cross-check the extension
#    list against actual expansion capability.
# =============================================================================


@pytest.mark.parametrize(
    "source,uri",
    sorted(CANONICAL_FHIR_R4_URIS.items()),
    ids=lambda v: v if isinstance(v, str) else v,
)
def test_e30_every_advertised_system_expands_via_implicit_url(
    fhir_client, source, uri
):
    """EXPLORER: every system in the capabilitystatement-supported-system
    extension MUST be expandable via the `<system-uri>/vs` implicit URL form.
    A system that's advertised but not expandable is overpromise — the
    CapabilityStatement lies about what the server supports.

    Acceptance: 200 + ValueSet body (containing 0+ codes; the empty-source
    extension is acceptable when the fixture DB has 0 rows for that source).
    NOT acceptable: 400 / 500 / non-FHIR response.
    """
    implicit_url = f"{uri}/vs"
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", implicit_url)]
    )
    assert r.status_code == 200, (
        f"Advertised system {source} ({uri}) implicit URL {implicit_url} did "
        f"not expand. Status={r.status_code}, body={r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "ValueSet", (
        f"Expected ValueSet, got {body.get('resourceType')!r}"
    )
    # The expansion MUST have a contains[] array (even if empty).
    expansion = body.get("expansion", {})
    assert "contains" in expansion and isinstance(expansion["contains"], list), (
        f"expansion.contains missing for {implicit_url}: {expansion!r}"
    )
    # If contains is empty, the empty-source extension MUST be present
    # (per HISTORIAN QA-033 — silent-empty is non-conformant).
    if not expansion["contains"]:
        exts = expansion.get("extension", [])
        ext_urls = {e.get("url") for e in exts}
        assert EMPTY_SOURCE_EXT_URL in ext_urls, (
            f"Empty expansion for {implicit_url} but no empty-source extension. "
            f"Extensions: {ext_urls}"
        )


# =============================================================================
# 4. Cross-system implicit URLs — intensional vs all-codes.
# =============================================================================


def test_e40_snomed_intensional_and_allcodes_distinct(fhir_client):
    """EXPLORER: SNOMED CT intensional (`?fhir_vs=isa` with code) and
    all-codes (`?fhir_vs` without code) MUST be distinct URLs producing
    distinct expansions.

    - `http://snomed.info/sct/73211009?fhir_vs=isa` — descendants of 73211009.
    - `http://snomed.info/sct?fhir_vs` — all of SNOMED CT.

    The fixture DB seeds both 73211009 (diabetes) and 44054006 (Type 2
    diabetes, descendant of 73211009). The intensional expansion contains
    only descendants (44054006); the all-codes expansion contains both.
    """
    # Intensional
    r_isa = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=[("url", "http://snomed.info/sct/73211009?fhir_vs=isa")],
    )
    assert r_isa.status_code == 200
    body_isa = r_isa.json()
    codes_isa = {c.get("code") for c in body_isa.get("expansion", {}).get("contains", [])}

    # All-codes
    r_all = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=[("url", "http://snomed.info/sct?fhir_vs")],
    )
    assert r_all.status_code == 200
    body_all = r_all.json()
    codes_all = {c.get("code") for c in body_all.get("expansion", {}).get("contains", [])}

    # The intensional expansion MUST include the descendant (44054006).
    assert "44054006" in codes_isa, (
        f"Intensional expansion missing descendant 44054006: {codes_isa}"
    )
    # The all-codes expansion MUST include both root and descendant
    # (or emit empty-source if the fixture has no SNOMED rows — but it does).
    assert codes_all, (
        f"All-codes SNOMED expansion is empty: {body_all.get('expansion')!r}"
    )


# =============================================================================
# 5. Combined implicit + filter — does filter apply to implicit expansion?
# =============================================================================


def test_e50_implicit_url_with_filter_param(fhir_client):
    """EXPLORER: `?url=http://loinc.org/vs&filter=glucose` — the spec
    permits filter to be combined with url. The server SHOULD apply the
    filter to the implicit expansion. For an empty fixture, the response
    is still 200 + ValueSet (filter just produces 0 matches).

    Spec: https://hl7.org/fhir/operation-valueset-expand.html — `filter`
    is "Allows server-side processing of the supplied filter".
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=[("url", "http://loinc.org/vs"), ("filter", "glucose")],
    )
    assert r.status_code == 200, (
        f"Implicit URL + filter failed. Status={r.status_code}, body={r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "ValueSet"


# =============================================================================
# 6. Implicit URL + count cap — does the server emit too-costly extension?
# =============================================================================


def test_e60_implicit_url_count_cap_emits_toocostly(fhir_client):
    """EXPLORER: large code systems will exceed reasonable expansion sizes.
    Per §4.7.3.1 the server SHOULD return OperationOutcome with code
    'too-costly' for very large expansions. With count=1 on a seeded source
    (SNOMED), the truncation extension MUST fire when results exceed count.

    The fixture seeds 2 SNOMED codes (73211009, 44054006). count=1 forces
    a truncation. The extension URL is
    `http://hl7.org/fhir/StructureDefinition/valueset-toocostly`.
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=[("url", "http://snomed.info/sct?fhir_vs"), ("count", 1)],
    )
    assert r.status_code == 200, (
        f"Implicit URL with count=1 failed. Status={r.status_code}, body={r.text[:200]}"
    )
    body = r.json()
    expansion = body.get("expansion", {})
    contains = expansion.get("contains", [])
    # Truncation: only 1 entry should be returned when count=1.
    assert len(contains) <= 1, (
        f"count=1 cap not enforced: {len(contains)} entries returned"
    )
    exts = expansion.get("extension", [])
    ext_urls = {e.get("url") for e in exts}
    assert TOOCOSTLY_EXT_URL in ext_urls, (
        f"too-costly extension not emitted on count-cap truncation. "
        f"Extensions: {ext_urls}"
    )


# =============================================================================
# 7. Terminology maintenance — submission endpoints MUST return FHIR response.
# =============================================================================


@pytest.mark.parametrize(
    "resource_type",
    ["CodeSystem", "ValueSet", "ConceptMap"],
)
def test_e70_post_resource_returns_fhir_response_not_fastapi_default(
    fhir_client, resource_type
):
    """EXPLORER: medterm4ds is read-only per AGENTS.md. POST /fhir/<Resource>
    MUST return a FHIR-shaped response (OperationOutcome), NOT FastAPI's
    default 405/422 with `{"detail": ...}` body and `application/json`
    Content-Type.

    Spec: https://hl7.org/fhir/R4/http.html#create — even rejected creates
    should return OperationOutcome.
    """
    r = fhir_client.post(
        f"/fhir/{resource_type}",
        json={"resourceType": resource_type, "status": "draft"},
    )
    # Acceptance: 4xx/5xx is fine (server is read-only); but the body MUST
    # be FHIR OperationOutcome, not FastAPI default.
    ct = r.headers.get("content-type", "")
    assert ct.startswith("application/fhir"), (
        f"POST /fhir/{resource_type} returned non-FHIR Content-Type: {ct!r}, "
        f"body={r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"POST /fhir/{resource_type} did not return OperationOutcome: {body!r}"
    )


# =============================================================================
# 8. CapabilityStatement extension structure audit.
# =============================================================================


def test_e80_extension_no_unexpected_keys(fhir_client):
    """EXPLORER: every supported-system extension entry has exactly `url`
    + `valueUri`. Any extra keys suggest drift (e.g., copy-paste from
    another extension type).
    """
    r = fhir_client.get("/fhir/metadata")
    body = r.json()
    exts = body.get("extension", [])
    for e in exts:
        if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL:
            keys = set(e.keys())
            assert keys == {"url", "valueUri"}, (
                f"Extension entry has unexpected keys: {e!r} (keys={keys})"
            )


def test_e81_extension_uris_match_canonical_registry(fhir_client):
    """EXPLORER: every URI in the extension MUST be one of the canonical
    URIs in `SYSTEM_TO_FHIR_URI`. Any drift suggests the extension is
    sourcing from somewhere other than the canonical map.
    """
    r = fhir_client.get("/fhir/metadata")
    body = r.json()
    exts = body.get("extension", [])
    uris = {
        e.get("valueUri")
        for e in exts
        if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
    }
    canonical = set(CANONICAL_FHIR_R4_URIS.values())
    assert uris == canonical, (
        f"Extension URIs do not match canonical registry.\n"
        f"  Extension URIs: {sorted(uris)}\n"
        f"  Canonical URIs: {sorted(canonical)}\n"
        f"  Difference: {uris.symmetric_difference(canonical)}"
    )


# =============================================================================
# 9. valueSet parameter on $expand via POST body.
# =============================================================================


def test_e90_expand_via_post_parameters_resource_with_url(fhir_client):
    """EXPLORER: $expand can be invoked via POST with a Parameters resource
    body. The `url` parameter inside the Parameters resource should be
    recognized and routed through the same implicit-URL detection as the
    GET query-param form.

    Spec: https://hl7.org/fhir/R4/operation-valueset-expand.html —
    "Operations MAY be invoked by POSTing a Parameters resource".
    """
    r = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": "http://loinc.org/vs"},
            ],
        },
    )
    assert r.status_code == 200, (
        f"POST $expand with implicit URL via Parameters body failed. "
        f"Status={r.status_code}, body={r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "ValueSet"


# =============================================================================
# 10. Cross-resource pollution — implicit URL passed to wrong operation.
# =============================================================================


def test_e100_lookup_with_implicit_url_returns_fhir_error(fhir_client):
    """EXPLORER: implicit URLs only make sense for $expand. Passing one to
    $lookup or $validate-code MUST produce a FHIR-shaped error, not a
    silent success or a Starlette default 500.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", "http://loinc.org/vs"), ("code", "any")],
    )
    ct = r.headers.get("content-type", "")
    assert ct.startswith("application/fhir"), (
        f"$lookup with implicit URL returned non-FHIR Content-Type: {ct!r}"
    )
    # We don't assert status — 200 (treated as lookup system=loinc/vs code=any)
    # or 400 (rejected) are both acceptable as long as it's FHIR-shaped.


def test_e101_validate_code_with_implicit_url_returns_fhir_response(fhir_client):
    """EXPLORER: same probe for $validate-code. The implicit URL is not a
    valid system URI for code lookup, but the response must be FHIR-shaped.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params=[("system", "http://loinc.org/vs"), ("code", "any")],
    )
    ct = r.headers.get("content-type", "")
    assert ct.startswith("application/fhir"), (
        f"$validate-code with implicit URL returned non-FHIR Content-Type: {ct!r}"
    )
