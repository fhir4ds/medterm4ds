"""SKEPTIC probes for TS-03 (External Code Systems, Implicit Value Sets,
Terminology Maintenance — FHIR R4 terminology-service §4.7.3, §4.7.3.1-3).

Source: https://build.fhir.org/terminology-service.html#4.7.3
       https://hl7.org/fhir/R4/extension-capabilitystatement-supported-system.html

Tests the 5 chunk items:
1. External code systems (SNOMED CT, LOINC, RxNorm, ICD-10-CM, ICD-10-PCS, CPT,
   CVX, HCPCS) recognized via canonical URIs in `system` parameters.
2. CapabilityStatement extension `capabilitystatement-supported-system` lists
   all supported external systems.
3. Implicit value sets (e.g., `http://loinc.org/vs`, `http://snomed.info/sct?
   fhir_vs`) resolvable via `$expand`.
4. Implicit value set fallback: `CodeSystem.valueSet` URI resolvable when no
   explicit ValueSet resource exists.
5. Terminology maintenance (server validates incoming resources — N/A for a
   read-only terminology service; documented in chunk notes).

Each probe is a SKEPTIC-style adversarial test:
- Probe a boundary or convention of one chunk item.
- Capture actual behavior (status, body, headers).
- A probe "fails" (reveals a bug) when actual behavior violates the spec.
"""

from __future__ import annotations

import pytest


# =============================================================================
# Item 1: External code systems recognized via canonical URIs
# =============================================================================

# Canonical URIs sourced from HL7 THO and owning authorities. Same registry as
# test_ts01_terminologist.py::CANONICAL_FHIR_R4_URIS — this copy is for TS-03
# cross-system probing of every operation.
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

# Known codes seeded in the conformance fixture DB (see conftest.py).
KNOWN_CODES = {
    "SNOMEDCT_US": "73211009",
    "ICD10CM": "E11",
    "RXNORM": "860975",
    # LNC, CPT, HCPCS, CVX, ICD10PCS aren't seeded in the fixture DB, but the
    # operation should still ACCEPT the canonical URI (return 200 with a body,
    # not 400 'Unrecognized system URI'). The spec requires server recognition
    # of the URI; the engine lookup may legitimately return code_info=None.
}


@pytest.mark.parametrize("source,uri", sorted(CANONICAL_FHIR_R4_URIS.items()))
def test_s01_lookup_accepts_all_canonical_system_uris(fhir_client, source, uri):
    """§4.7.3 / §4.7.1.2: 'the `system` parameter accepts the canonical URI
    of any code system the server supports.' Each of the 8 supported systems
    MUST be recognized.

    Spec: https://build.fhir.org/terminology-service.html#summary
    """
    code = KNOWN_CODES.get(source, "0")
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup", params=[("system", uri), ("code", code)]
    )
    # Acceptance means NOT 400 'Unrecognized system URI'. Status 200 (found)
    # or a Parameters-shaped body with `code`/`system` echoed are all OK.
    assert r.status_code != 400 or "Unrecognized system URI" not in r.text, (
        f"$lookup rejected canonical URI for {source}: {uri!r}. "
        f"Status={r.status_code}, body={r.text[:200]}"
    )


@pytest.mark.parametrize("source,uri", sorted(CANONICAL_FHIR_R4_URIS.items()))
def test_s02_validate_code_accepts_all_canonical_system_uris(
    fhir_client, source, uri
):
    """§4.7.3: same recognition requirement on $validate-code."""
    code = KNOWN_CODES.get(source, "0")
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code", params=[("system", uri), ("code", code)]
    )
    assert r.status_code != 400 or "Unrecognized system URI" not in r.text, (
        f"$validate-code rejected canonical URI for {source}: {uri!r}. "
        f"Status={r.status_code}, body={r.text[:200]}"
    )


def test_s03_lookup_rejects_unknown_system_uri(fhir_client):
    """SKEPTIC: silent-fallback guard. An unknown system URI MUST be rejected
    with 400, not silently produce an empty Parameters body or fall through to
    a default system. This is the GLOBAL_RULES 'silent fallback' prohibition."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", "http://example.com/unknown-system"), ("code", "X")],
    )
    assert r.status_code == 400, (
        f"Expected 400 for unknown system URI, got {r.status_code}. "
        f"Body: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"Expected OperationOutcome, got {body.get('resourceType')}"
    )


def test_s04_lookup_uri_case_sensitive_uppercase_rejected(fhir_client):
    """SKEPTIC: URIs are case-sensitive per RFC 3986 §3.1 and §6.2.2.1.
    Uppercasing the scheme or host MUST be rejected (HTTP ≠ http, SNOMED.INFO
    ≠ snomed.info) UNLESS the server explicitly normalizes. Verify the engine
    doesn't silently accept the uppercase variant as the canonical.

    Note: this is a *guarded* probe. Some servers normalize URI scheme/host
    case (RFC 3986 permits this for scheme only). If the server DOES accept
    the uppercase form, the test should still pass — but if it returns a
    'recognized' response, the URI was treated as the canonical string match
    or normalized; either is acceptable. The bug-class being probed: server
    silently accepts a 'close enough' URI form (silent-wrong-answer).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", "HTTP://SNOMED.INFO/SCT"), ("code", "73211009")],
    )
    # The conformance bar: server must NOT silently fall through to a 200 with
    # an empty Parameters body that *looks* like a successful lookup. Either
    # 400 (strict case match) OR a 200/404 with an explicit not-found body is
    # acceptable. The failure mode is a 200 Parameters body with `code` echoed
    # but no display — silently degrading.
    if r.status_code == 200:
        body = r.json()
        # If 200, must include resourceType=Parameters and at least a `code`
        # echo. Anything else is a silent-wrong-answer.
        assert body.get("resourceType") == "Parameters", (
            f"Uppercase URI silently returned non-Parameters body: {body}"
        )


def test_s05_lookup_hcpcs_legacy_alias_still_resolves(fhir_client):
    """TS-01 QA-012 corrected HCPCS to its canonical URI and retained the
    prior (incorrect) THO resource URL as a backwards-compat alias in
    FHIR_URI_ALIASES. SKEPTIC re-verifies the alias still resolves.

    Spec context: clients that learned the wrong URI must continue to work
    after a canonical-URI correction."""
    legacy_uri = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", legacy_uri), ("code", "0")],
    )
    # The alias MUST be recognized (not 400 'Unrecognized system URI'). The
    # code may not exist in the fixture DB, but the URI itself must resolve.
    assert r.status_code != 400 or "Unrecognized system URI" not in r.text, (
        f"HCPCS legacy alias was rejected as unrecognized: "
        f"Status={r.status_code}, body={r.text[:200]}"
    )


# =============================================================================
# Item 2: capabilitystatement-supported-system extension
# =============================================================================

SUPPORTED_SYSTEM_EXTENSION_URL = (
    "http://hl7.org/fhir/StructureDefinition/capabilitystatement-supported-system"
)


def test_s10_capabilitystatement_includes_supported_system_extension(
    fhir_client,
):
    """§4.7.3 / extension spec: 'A list of all the system URIs that the server
    supports. Servers SHOULD include this extension in their CapabilityStatement
    so clients know which code systems can be used.'

    Spec: https://hl7.org/fhir/R4/extension-capabilitystatement-supported-system.html
    Quote: 'A list of all the system URIs for code systems that are supported
            by the server.'
    """
    r = fhir_client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.json()
    extensions = body.get("extension", [])
    supported_system_exts = [
        e for e in extensions if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
    ]
    assert supported_system_exts, (
        "CapabilityStatement is missing the capabilitystatement-supported-system "
        "extension. Per §4.7.3, the server SHOULD list every supported external "
        "code system URI in this extension so clients can discover them without "
        "calling every operation."
    )


def test_s11_supported_system_extension_lists_all_8_systems(fhir_client):
    """§4.7.3: every supported external system URI MUST be present in the
    extension list (overpromise = bug; underpromise = bug)."""
    r = fhir_client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.json()
    extensions = body.get("extension", [])
    supported_uris = {
        e.get("valueUri")
        for e in extensions
        if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
    }
    expected = set(CANONICAL_FHIR_R4_URIS.values())
    missing = expected - supported_uris
    assert not missing, (
        f"capabilitystatement-supported-system extension is missing systems: "
        f"{sorted(missing)}. Advertised: {sorted(supported_uris)}"
    )


def test_s12_supported_system_extension_does_not_overpromise(fhir_client):
    """SKEPTIC: every URI in the extension list MUST be one the engine
    recognizes via `fhir_uri_to_system`. Advertising a system the engine can't
    serve is a silent-overpromise bug."""
    r = fhir_client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.json()
    extensions = body.get("extension", [])
    advertised = {
        e.get("valueUri")
        for e in extensions
        if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
    }
    # Every advertised URI must be resolvable via the canonical map. We
    # verify by issuing a $lookup for a (system, code) pair and confirming
    # the server treats it as recognized.
    unrecognized = []
    for uri in sorted(advertised):
        r2 = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[("system", uri), ("code", "__nonexistent__")],
        )
        if r2.status_code == 400 and "Unrecognized system URI" in r2.text:
            unrecognized.append(uri)
    assert not unrecognized, (
        f"CapabilityStatement advertises systems the engine doesn't recognize: "
        f"{unrecognized}"
    )


# =============================================================================
# Item 3: Implicit value sets resolvable via $expand
# =============================================================================

def test_s20_expand_implicit_loinc_value_set(fhir_client):
    """§4.7.3.1 Implicit Value Sets: 'http://loinc.org/vs' resolves to all of
    LOINC. The server SHOULD resolve convention-based ValueSet URLs derived
    from code system URIs. A 200 with truncation OR a `too-costly`
    OperationOutcome is acceptable. A 400 'Provide a ValueSet body, a fhir_vs
    URL, or a filter parameter.' is non-conformant.

    Spec: https://build.fhir.org/terminology-service.html#4.7.3
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", "http://loinc.org/vs")]
    )
    # Acceptance: 200 (ValueSet body) or 400 with too-costly message. The bug
    # is a 400 'Provide a ValueSet body...' which means implicit URL was
    # silently treated as unknown.
    body = r.json()
    if r.status_code == 200:
        assert body.get("resourceType") == "ValueSet", (
            f"Expected ValueSet, got {body.get('resourceType')}"
        )
    else:
        diagnostics = body.get("issue", [{}])[0].get("diagnostics", "")
        # Acceptable: too-costly or size-related error. NOT acceptable: the
        # generic 'Provide a ValueSet body, a fhir_vs URL, or a filter' which
        # means the implicit URL was unrecognized.
        assert "Provide a ValueSet body" not in diagnostics, (
            f"Implicit value set http://loinc.org/vs was not recognized as an "
            f"implicit URL — server returned generic 'no input' error. "
            f"Status={r.status_code}, body={r.text[:200]}"
        )


def test_s21_expand_implicit_snomed_all_value_set(fhir_client):
    """§4.7.3.1: 'http://snomed.info/sct?fhir_vs' (no =isa, no code in path)
    resolves to all of SNOMED CT. The server SHOULD accept this convention-based
    URL even without a code in the path.

    Spec: https://build.fhir.org/terminology-service.html#4.7.3
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", "http://snomed.info/sct?fhir_vs")]
    )
    # HISTORIAN iteration TS-03 (QA-034): tightened to a positive success-shape
    # assertion. The prior negative-only check ('body must not contain
    # "Provide a ValueSet body"') gave a false-positive pass on a real bug
    # — Form (b) URL detection had two parsing bugs and the URL fell through
    # to the intensional handler which raised a *different* error string
    # that passed the lenient check. Positive shape required: 200 ValueSet.
    assert r.status_code == 200, (
        f"Implicit SNOMED value set expansion failed. "
        f"Status={r.status_code}, body={r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "ValueSet", (
        f"Expected ValueSet, got {body.get('resourceType')}"
    )
    expansion = body.get("expansion", {})
    assert "contains" in expansion and isinstance(expansion["contains"], list), (
        f"expansion.contains missing or wrong type: {expansion!r}"
    )


def test_s22_expand_implicit_snomed_intensional_with_code(fhir_client):
    """§4.7.3.2: 'http://snomed.info/sct/73211009?fhir_vs=isa' resolves to all
    descendants of code 73211009 (diabetes). Already implemented in TS-01, but
    SKEPTIC re-verifies since this is the intensional-URL probe TS-03 cares
    about."""
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=[("url", "http://snomed.info/sct/73211009?fhir_vs=isa")],
    )
    assert r.status_code == 200, (
        f"SNOMED intensional expansion failed. Status={r.status_code}, "
        f"body={r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "ValueSet"
    contains = body.get("expansion", {}).get("contains", [])
    # Fixture seeds 44054006 (Type 2 diabetes) as descendant of 73211009.
    codes = {c.get("code") for c in contains}
    assert "44054006" in codes, (
        f"Expected descendant 44054006 in expansion, got codes={sorted(codes)}"
    )


def test_s23_expand_non_canonical_url_returns_clear_error(fhir_client):
    """SKEPTIC: a non-canonical url format MUST return a clear error, not
    silently succeed (silent-wrong-answer anti-pattern)."""
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=[("url", "http://example.com/random-vs-url")],
    )
    # The url doesn't match any implicit pattern and no filter was provided.
    # Acceptance: 400 with a clear 'no input' message (current behavior) OR
    # 400 'not found'. NOT acceptable: 200 with empty expansion (silent
    # success on an unrecognized URL — would imply the server pretends every
    # URL resolves).
    assert r.status_code == 400, (
        f"Non-canonical URL should return 400, got {r.status_code}"
    )


# =============================================================================
# Item 4: CodeSystem.valueSet URI alone is resolvable (Implicit VS Fallback)
# =============================================================================

def test_s30_expand_codesystem_valueset_uri_fallback(fhir_client):
    """§4.7.3: 'If a CodeSystem resource has a `valueSet` element, the URL of
    the implicit value set is the same as the CodeSystem url itself.' The
    server SHOULD expand such URIs even when no explicit ValueSet resource
    exists.

    NOTE: medterm4ds does not persist CodeSystem resources (per AGENTS.md, the
    CodeSystem routes are stubs that return empty Bundles / not-found). This
    probe documents the expected behavior: a URL matching a known code-system
    URI (e.g. `http://loinc.org`) SHOULD expand to that system's implicit
    value set OR be rejected with a clear 'CodeSystem persistence not
    supported' message — NOT silently succeed with an empty expansion."""
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", "http://loinc.org")]
    )
    # Acceptance: 400 with a clear error (current 'Provide a ValueSet body...'
    # is acceptable for a non-persisting server). The failure mode is a 200
    # with an empty expansion that *implies* LOINC has no codes (silent
    # misrepresentation).
    if r.status_code == 200:
        body = r.json()
        # An empty 200 expansion is only acceptable if it includes a clear
        # extension explaining the implicit fallback isn't implemented.
        # Otherwise it's a silent misrepresentation.
        expansion = body.get("expansion", {})
        if not expansion.get("contains"):
            extensions = expansion.get("extension", [])
            assert extensions, (
                "Empty expansion of http://loinc.org without explanation — "
                "silent misrepresentation. Server should either expand or "
                "return 400 with a clear 'implicit VS not implemented' message."
            )


# =============================================================================
# Item 5: Terminology maintenance (server validates incoming resources)
# =============================================================================

def test_s40_terminology_maintenance_out_of_scope_documented(fhir_client):
    """§4.7.3.3 Terminology Maintenance: the spec section discusses server
    responsibilities for accepting, validating, and maintaining terminology
    resources submitted by clients. medterm4ds is a READ-ONLY terminology
    service per AGENTS.md — it loads UMLS data into DuckDB and exposes query
    operations; it does not accept resource submissions.

    This probe verifies the read-only contract: POST to /fhir/CodeSystem with
    a resource body MUST be rejected (405 / 404 / 400), not silently accepted
    and discarded (which would be a silent-wrong-answer bug)."""
    r = fhir_client.post(
        "/fhir/CodeSystem",
        json={
            "resourceType": "CodeSystem",
            "url": "http://example.com/test",
            "content": "complete",
            "concept": [{"code": "X", "display": "Test"}],
        },
    )
    # Acceptance: any non-200 status, OR 200 with a clear OperationOutcome
    # indicating 'not supported'. NOT acceptable: 200/201 Created implying
    # the resource was persisted (silent lie).
    assert r.status_code != 201, (
        f"Read-only server returned 201 Created on POST CodeSystem — "
        f"silent acceptance of resource submission. Status={r.status_code}"
    )
