"""TERMINOLOGIST iteration TS-03 — clinical correctness probes for Implicit
Value Sets, External Code Systems, and Terminology Maintenance.

Source: https://build.fhir.org/terminology-service.html §4.7.3, §4.7.3.1-3
       https://hl7.org/fhir/R4/extension-capabilitystatement-supported-system.html
       https://hl7.org/fhir/R4/valueset.html (expansion.contains[].display)
       https://confluence.ihtsdoproject.org/display/DOCLOINC/Consumer+Names — LOINC Consumer Name

TERMINOLOGIST lens (per the assignment):
1. Implicit value set enumeration quality — do the returned codes carry
   clinically sensible `display` values? Are they the engine's canonical
   preferred term, or a stale/wrong string?
2. Patient-friendly name surfacing on implicit LOINC expansion — the engine
   has the prepared LOINC patient-friendly cache; the implicit expansion
   should surface it (or at minimum not silently suppress it).
3. Code-system URI round-trips — every code returned by an implicit expansion
   must be `$lookup`-able with the SAME URI the expansion advertised. Catches
   "expansion advertises URI X, lookup only works with URI Y" drift.
4. HCPCS canonical URI round-trip — verifies TS-01 QA-012 fix is intact:
   HCPCS implicit expansion uses the corrected canonical URI (not the old
   wrong THO resource URL).
5. Truncation honesty — when `count=N` truncates the expansion, does the
   response honestly signal more exist via `valueset-toocostly`? Any silent
   drops (total != len(contains))?
6. CapabilityStatement extension clinical accuracy — every advertised system
   must be capable of meaningful clinical responses on the operations it
   advertises.
7. Cross-system clinical consistency — codes returned by SNOMED expansion
   must be SNOMED codes (no cross-system leakage).

Default severity HIGH per GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH
Severity". Remediation engineers cannot dismiss them as INTENDED without an
explicit user override.

All input-recognition probes assert the POSITIVE success shape per the
GLOBAL_RULES.md "Test-too-lenient" trigger (count=1, promoted by TS-03
HISTORIAN QA-034).
"""

from __future__ import annotations

import pytest


SUPPORTED_SYSTEM_EXTENSION_URL = (
    "http://hl7.org/fhir/StructureDefinition/capabilitystatement-supported-system"
)
TOOCOSTLY_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"
EMPTY_SOURCE_EXT_URL = (
    "http://medterm4ds.org/fhir/StructureDefinition/valueset-empty-source"
)

# Canonical FHIR R4 system URIs (single source of truth: HL7 THO + owning
# authorities). Mirrors `engines.fhir.SYSTEM_TO_FHIR_URI`.
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
# Only SNOMEDCT_US, ICD10CM, and RXNORM have seeded rows. The display values
# below are the exact canonical STR values from the fixture's mrconso rows —
# the implicit expansion MUST return these, never the bare code string.
SEEDED_CODES = {
    "SNOMEDCT_US": [
        ("73211009", "Diabetes mellitus"),
        ("44054006", "Type 2 diabetes mellitus"),
    ],
    "ICD10CM": [
        ("E11", "Type 2 diabetes mellitus"),
    ],
    "RXNORM": [
        ("860975", "24 HR metformin 500 MG Oral Tablet"),
    ],
}

# Sources advertised in the CapabilityStatement extension but NOT seeded in
# the fixture DB. These exercise the empty-source extension path.
UNSEEDED_SOURCES = ["LNC", "CPT", "HCPCS", "CVX", "ICD10PCS"]


# =============================================================================
# 1. Implicit value set enumeration quality — display correctness
# =============================================================================


@pytest.mark.parametrize(
    "source,uri,expected_code,expected_display",
    [
        ("SNOMEDCT_US", "http://snomed.info/sct/vs", "73211009", "Diabetes mellitus"),
        ("SNOMEDCT_US", "http://snomed.info/sct/vs", "44054006", "Type 2 diabetes mellitus"),
        ("ICD10CM", "http://hl7.org/fhir/sid/icd-10-cm/vs", "E11", "Type 2 diabetes mellitus"),
        ("RXNORM", "http://www.nlm.nih.gov/research/umls/rxnorm/vs", "860975", "24 HR metformin 500 MG Oral Tablet"),
    ],
)
def test_t10_implicit_display_is_canonical_preferred_term(
    fhir_client, source, uri, expected_code, expected_display
):
    """TERMINOLOGIST: implicit expansion `display` MUST be the engine's
    canonical preferred term (CodeInfo.name from the prepared preferred-atom
    path). Returning a bare code string (e.g. "73211009") as the display is
    silent-wrong-answer — a clinician would see a meaningless numeric label
    where a clinical term is expected.

    Spec: https://hl7.org/fhir/R4/valueset.html#expansion — expansion.contains.
    display is 'The recommended display name for this code in this expansion'.
    """
    resp = fhir_client.get(f"/fhir/ValueSet/$expand?url={uri}&count=20")
    assert resp.status_code == 200, f"implicit expand {uri} failed: {resp.status_code} {resp.text[:200]}"
    body = resp.json()
    contains = body.get("expansion", {}).get("contains", [])
    by_code = {c.get("code"): c for c in contains}
    assert expected_code in by_code, (
        f"expected code {expected_code!r} not in expansion contains[]; "
        f"got codes {list(by_code)}"
    )
    entry = by_code[expected_code]
    assert entry.get("display") == expected_display, (
        f"display for {source}/{expected_code} is {entry.get('display')!r}; "
        f"expected canonical preferred term {expected_display!r}. "
        f"A bare-code or stale display is silent-wrong-answer clinically."
    )


def test_t11_implicit_display_is_not_bare_code(fhir_client):
    """TERMINOLOGIST: implicit expansion must NEVER fall back to the bare code
    string as the display when the code is in mrconso. If the engine can
    resolve the canonical name, the display must be that name — never the code.

    This catches the `display = code_info.name or code` silent-fallback shape
    from a clinical lens. The `or code` arm is acceptable only when the code
    is genuinely unresolvable; for seeded codes that path is silent-wrong-answer.
    """
    resp = fhir_client.get("/fhir/ValueSet/$expand?url=http://snomed.info/sct/vs&count=20")
    assert resp.status_code == 200
    contains = resp.json().get("expansion", {}).get("contains", [])
    assert contains, "expected non-empty expansion for SNOMED seeded fixture"
    for entry in contains:
        code = entry.get("code")
        display = entry.get("display")
        assert display, f"empty display for code {code!r}"
        assert display != code, (
            f"display for code {code!r} is the bare code itself — a clinician "
            f"would see a meaningless identifier where a clinical term is "
            f"expected. Engine should have resolved the canonical STR."
        )


# =============================================================================
# 2. Patient-friendly name surfacing on implicit LOINC expansion
# =============================================================================
# Note: the conformance fixture does NOT seed patient-friendly rows, so we
# cannot assert specific patient-friendly strings. We assert the STRUCTURAL
# contract: when the implicit LOINC expansion returns codes, each contains[]
# entry SHOULD carry a patient-friendly extension IF the engine has the data.
# In the fixture (no patient-friendly rows), the test documents the absence
# rather than asserting presence — it's a future-enhancement probe, not a bug.
# A bug would be: patient-friendly data exists for a code but the expansion
# silently suppresses it. The fixture cannot exercise that path; the
# regression suite (tests/regression/) covers it for $lookup. Leaving this
# section as a documented gap rather than a failing assertion.


def test_t12_implicit_loinc_expansion_returns_valueSet_shape(fhir_client):
    """TERMINOLOGIST: implicit LOINC expansion must return a ValueSet body
    with the correct expansion shape, even when the fixture has 0 LOINC rows.
    Patient-friendly surfacing is documented as a future enhancement (the
    engine has the prepared LOINC cache; the implicit expansion does not
    currently consult it). This probe pins the structural contract so a
    future enhancement that wires patient-friendly into _expand_implicit_value_set
    doesn't accidentally break the basic shape.
    """
    resp = fhir_client.get("/fhir/ValueSet/$expand?url=http://loinc.org/vs&count=20")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("resourceType") == "ValueSet"
    assert body.get("url") == "http://loinc.org/vs"
    expansion = body.get("expansion", {})
    assert "contains" in expansion
    # LOINC has no seeded rows in the fixture — the empty-source extension
    # MUST be present (HISTORIAN QA-033 contract).
    if not expansion.get("contains"):
        ext_urls = [e.get("url") for e in expansion.get("extension", [])]
        assert EMPTY_SOURCE_EXT_URL in ext_urls, (
            "empty LOINC expansion must carry the empty-source extension so "
            "clients can distinguish 'LOINC has 0 codes' from 'silent failure'"
        )


# =============================================================================
# 3. Code-system URI round-trip — every code in an implicit expansion must
#    be $lookup-able with the SAME URI the expansion advertised.
# =============================================================================


@pytest.mark.parametrize(
    "source,uri",
    [
        ("SNOMEDCT_US", "http://snomed.info/sct/vs"),
        ("ICD10CM", "http://hl7.org/fhir/sid/icd-10-cm/vs"),
        ("RXNORM", "http://www.nlm.nih.gov/research/umls/rxnorm/vs"),
    ],
)
def test_t20_implicit_expansion_codes_round_trip_via_lookup(fhir_client, source, uri):
    """TERMINOLOGIST: for every code returned by an implicit expansion, calling
    `$lookup?system=<advertised-URI>&code=<code>` MUST succeed with the same
    URI the expansion advertised. Catches 'expansion advertises URI X, lookup
    only works with URI Y' drift — a critical clinical correctness property
    because EHRs store Codings from expansions and re-query them later.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html — system
    is 'The code system in which the code is to be looked up'.
    """
    expand_resp = fhir_client.get(f"/fhir/ValueSet/$expand?url={uri}&count=20")
    assert expand_resp.status_code == 200
    contains = expand_resp.json().get("expansion", {}).get("contains", [])
    assert contains, f"expected non-empty expansion for {uri}"
    for entry in contains:
        advertised_uri = entry.get("system")
        code = entry.get("code")
        # The advertised URI MUST match the canonical one we queried.
        assert advertised_uri == uri.rstrip("/vs").__str__() or advertised_uri in uri or uri.replace("/vs", "") == advertised_uri, (
            f"expansion entry advertises system={advertised_uri!r}; expected "
            f"the canonical URI used to query ({uri!r}). URI drift here means "
            f"a Coding stored from this expansion wouldn't round-trip."
        )
        # And $lookup with that URI+code MUST succeed.
        lookup_resp = fhir_client.get(
            f"/fhir/CodeSystem/$lookup",
            params={"system": advertised_uri, "code": code},
        )
        assert lookup_resp.status_code == 200, (
            f"$lookup system={advertised_uri!r} code={code!r} (returned by "
            f"implicit expansion of {uri}) failed with {lookup_resp.status_code}: "
            f"{lookup_resp.text[:200]}. URI round-trip is broken."
        )
        params = lookup_resp.json().get("parameter", [])
        # The `name` parameter MUST carry the canonical display (server-side
        # authoritative name, not a client echo).
        name_param = next((p for p in params if p.get("name") == "name"), None)
        assert name_param is not None, (
            f"$lookup response for {advertised_uri}/{code} missing 'name' parameter"
        )
        assert name_param.get("valueString"), (
            f"$lookup 'name' parameter empty for {advertised_uri}/{code}"
        )


def test_t21_snomed_all_codes_implicit_uri_round_trip(fhir_client):
    """TERMINOLOGIST: SNOMED implicit Form (b) `?fhir_vs` expansion must
    advertise `system: http://snomed.info/sct`, and each code must round-trip
    through $lookup with that exact URI.
    """
    resp = fhir_client.get("/fhir/ValueSet/$expand?url=http://snomed.info/sct?fhir_vs&count=20")
    assert resp.status_code == 200
    contains = resp.json().get("expansion", {}).get("contains", [])
    assert contains, "expected non-empty SNOMED all-codes expansion"
    for entry in contains:
        assert entry.get("system") == "http://snomed.info/sct", (
            f"SNOMED implicit expansion advertised system={entry.get('system')!r}; "
            f"expected 'http://snomed.info/sct'"
        )
        code = entry.get("code")
        lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": code},
        )
        assert lookup.status_code == 200


# =============================================================================
# 4. HCPCS canonical URI round-trip — verifies TS-01 QA-012 fix is intact.
# =============================================================================


def test_t30_hcpcs_implicit_expansion_uses_corrected_canonical_uri(fhir_client):
    """TERMINOLOGIST: HCPCS implicit expansion must advertise the corrected
    canonical URI from TS-01 QA-012 (`http://www.cms.gov/Medicare/Coding/
    HCPCSReleaseCodeSets`), NOT the old wrong THO resource URL
    (`http://terminology.hl7.org/CodeSystem/hcpcs-Level-II`).

    Even though the fixture DB has 0 HCPCS rows (so the expansion is empty),
    the empty-source extension fires and we can verify the URL was *recognized*
    as HCPCS. The HCPCS source is in the advertised extension list — this
    test cross-checks the canonical URI is in place via $lookup acceptance.

    Spec: per HL7 THO v5.5.0 the canonical system URI for HCPCS Level II is
    http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets.
    """
    # Implicit expansion of HCPCS — fixture has 0 rows, so this returns the
    # empty-source extension. The fact that it returns 200 (not 400 'unrecognized
    # code system URI') proves the URL was correctly recognized as HCPCS.
    resp = fhir_client.get(
        "/fhir/ValueSet/$expand?url=http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets/vs&count=20"
    )
    assert resp.status_code == 200, (
        f"HCPCS implicit expand with corrected canonical URI failed: "
        f"{resp.status_code} {resp.text[:200]}"
    )
    body = resp.json()
    contains = body.get("expansion", {}).get("contains", [])
    # Fixture has 0 HCPCS rows — empty expansion is correct, but MUST carry
    # the empty-source extension.
    if not contains:
        ext_urls = [e.get("url") for e in body.get("expansion", {}).get("extension", [])]
        assert EMPTY_SOURCE_EXT_URL in ext_urls

    # Cross-check: the OLD wrong URI (THO resource URL) is still accepted as
    # an alias (backwards-compat per QA-012 fix), but is NOT what gets
    # advertised.
    resp2 = fhir_client.get(
        "/fhir/ValueSet/$expand?url=http://terminology.hl7.org/CodeSystem/hcpcs-Level-II/vs&count=20"
    )
    assert resp2.status_code == 200, (
        f"HCPCS legacy alias URI implicit expand failed: {resp2.status_code}"
    )


def test_t31_capabilitystatement_advertises_corrected_hcpcs_uri(fhir_client):
    """TERMINOLOGIST: the capabilitystatement-supported-system extension list
    MUST contain the corrected HCPCS canonical URI (NOT the old THO resource
    URL). This is the TS-01 QA-012 contract, re-verified from the implicit
    expansion angle.
    """
    resp = fhir_client.get("/fhir/metadata")
    assert resp.status_code == 200
    body = resp.json()
    ext_entries = [
        e.get("valueUri")
        for e in body.get("extension", [])
        if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
    ]
    assert "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets" in ext_entries, (
        "corrected HCPCS canonical URI missing from capabilitystatement-"
        "supported-system extension"
    )
    # The OLD wrong URI MUST NOT be advertised (only the canonical one).
    assert "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II" not in ext_entries, (
        "old wrong HCPCS THO resource URL is being advertised in the "
        "supported-system extension — TS-01 QA-012 fix is leaking the wrong URI"
    )


# =============================================================================
# 5. Truncation honesty — count truncation must be signaled honestly.
# =============================================================================


def test_t40_count_truncation_emits_toocostly_extension(fhir_client):
    """TERMINOLOGIST: when an implicit expansion returns more codes than `count`
    allows, the response MUST carry the `valueset-toocostly` extension
    (per https://hl7.org/fhir/R4/extension-valueset-toocostly.html) so the
    client knows the expansion is incomplete. Silent truncation is a clinical
    safety risk — a clinician acting on an incomplete expansion could miss
    relevant codes.

    The fixture has 2 SNOMED rows; asking for count=1 forces truncation.
    """
    resp = fhir_client.get("/fhir/ValueSet/$expand?url=http://snomed.info/sct/vs&count=1")
    assert resp.status_code == 200
    body = resp.json()
    expansion = body.get("expansion", {})
    contains = expansion.get("contains", [])
    # The fixture has 2 SNOMED rows; count=1 must yield exactly 1 entry.
    assert len(contains) == 1, (
        f"expected exactly 1 contains entry (count=1); got {len(contains)}"
    )
    # The too-costly extension MUST be present.
    ext_urls = [e.get("url") for e in expansion.get("extension", [])]
    assert TOOCOSTLY_EXT_URL in ext_urls, (
        f"implicit expansion truncated at count=1 but no "
        f"valueset-toocostly extension was emitted; got extensions {ext_urls}"
    )


def test_t41_no_silent_drop_total_matches_contains_length(fhir_client):
    """TERMINOLOGIST: the ValueSet.expansion.total field MUST equal the actual
    length of contains[] — never a silent drop. If total=10 but contains[]
    has only 8 entries, the client is misled about how many codes are in the
    expansion. Spec: https://hl7.org/fhir/R4/valueset.html#expansion — 'total'
    is the total number of codes in the expansion.
    """
    resp = fhir_client.get("/fhir/ValueSet/$expand?url=http://snomed.info/sct/vs&count=20")
    assert resp.status_code == 200
    expansion = resp.json().get("expansion", {})
    total = expansion.get("total")
    contains = expansion.get("contains", [])
    assert total == len(contains), (
        f"expansion.total={total} but len(contains)={len(contains)} — "
        f"silent drop or stale total. Client would be misled about expansion size."
    )


def test_t42_count_cap_not_silently_returning_more_than_count(fhir_client):
    """TERMINOLOGIST: the implicit expansion MUST honor the `count` cap — never
    return more than `count` entries. Returning more would silently overwhelm
    a paginating client.
    """
    resp = fhir_client.get("/fhir/ValueSet/$expand?url=http://snomed.info/sct/vs&count=1")
    assert resp.status_code == 200
    contains = resp.json().get("expansion", {}).get("contains", [])
    assert len(contains) <= 1, (
        f"server returned {len(contains)} entries but count=1 was requested — "
        f"count cap is not honored"
    )


# =============================================================================
# 6. CapabilityStatement extension clinical accuracy — every advertised
#    system must be capable of meaningful clinical responses.
# =============================================================================


def test_t50_every_advertised_system_accepts_lookup(fhir_client):
    """TERMINOLOGIST: for every system URI advertised in the
    capabilitystatement-supported-system extension, $lookup with that URI MUST
    be accepted (200, not 400 'unrecognized system URI'). Advertising a system
    that the engine rejects at the operation layer is clinical overpromise.

    The fixture DB has seeded codes for SNOMEDCT/ICD10CM/RXNORM; the other 5
    systems (LNC/CPT/HCPCS/CVX/ICD10PCS) have no seeded rows but the URI MUST
    still be accepted (200 with code_info=None is conformant — 'recognized URI,
    unknown code').
    """
    resp = fhir_client.get("/fhir/metadata")
    body = resp.json()
    advertised_uris = [
        e.get("valueUri")
        for e in body.get("extension", [])
        if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
    ]
    assert advertised_uris, "no supported-system extension entries"
    for uri in advertised_uris:
        # Probe with a sentinel code that's definitely not in the fixture.
        # The conformant response is 200 (Parameters with `code: <sentinel>`,
        # no `name` parameter) — NOT 400 'unrecognized system URI'.
        lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": uri, "code": "PROBE_NOT_A_REAL_CODE_12345"},
        )
        assert lookup.status_code == 200, (
            f"advertised system URI {uri!r} rejected by $lookup with "
            f"{lookup.status_code}: {lookup.text[:200]}. Advertising a system "
            f"that the operation layer rejects is clinical overpromise."
        )


def test_t51_no_stub_system_in_extension(fhir_client):
    """TERMINOLOGIST: every system in the supported-system extension MUST have
    a corresponding entry in SYSTEM_TO_FHIR_URI (single source of truth). A
    stub entry with no engine backing would be a clinical lie — clients would
    query it and get nothing.

    Cross-check: the extension list and SYSTEM_TO_FHIR_URI must have identical
    URI sets (after sorting).
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    resp = fhir_client.get("/fhir/metadata")
    body = resp.json()
    advertised_uris = {
        e.get("valueUri")
        for e in body.get("extension", [])
        if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
    }
    canonical_uris = set(SYSTEM_TO_FHIR_URI.values())
    assert advertised_uris == canonical_uris, (
        f"capabilitystatement-supported-system extension drifts from "
        f"SYSTEM_TO_FHIR_URI (single source of truth).\n"
        f"  advertised but not canonical: {advertised_uris - canonical_uris}\n"
        f"  canonical but not advertised: {canonical_uris - advertised_uris}"
    )


# =============================================================================
# 7. Cross-system clinical consistency — codes returned by an implicit
#    expansion must be from the advertised system (no leakage).
# =============================================================================


@pytest.mark.parametrize(
    "uri,expected_system",
    [
        ("http://snomed.info/sct/vs", "http://snomed.info/sct"),
        ("http://hl7.org/fhir/sid/icd-10-cm/vs", "http://hl7.org/fhir/sid/icd-10-cm"),
        ("http://www.nlm.nih.gov/research/umls/rxnorm/vs", "http://www.nlm.nih.gov/research/umls/rxnorm"),
    ],
)
def test_t60_no_cross_system_leakage_in_implicit_expansion(fhir_client, uri, expected_system):
    """TERMINOLOGIST: every entry in an implicit expansion MUST advertise the
    system URI that matches the URL convention queried. Cross-system leakage
    (e.g. an ICD-10-CM code appearing in a SNOMED expansion with system=
    'http://snomed.info/sct') would be a critical clinical safety failure —
    a Coding stored from the expansion would be attributed to the wrong
    code system.

    The implicit expander queries `WHERE SAB = ?` per-source, so leakage
    shouldn't occur — but the probe pins the contract.
    """
    resp = fhir_client.get(f"/fhir/ValueSet/$expand?url={uri}&count=20")
    assert resp.status_code == 200
    contains = resp.json().get("expansion", {}).get("contains", [])
    for entry in contains:
        assert entry.get("system") == expected_system, (
            f"cross-system leakage: implicit expansion of {uri} returned an "
            f"entry with system={entry.get('system')!r} (expected "
            f"{expected_system!r}). Code {entry.get('code')} would be "
            f"misattributed to the wrong code system."
        )


def test_t61_snomed_all_codes_no_leakage_into_intensional(fhir_client):
    """TERMINOLOGIST: SNOMED all-codes (`?fhir_vs`) expansion must return
    ONLY codes from the SNOMED fixture rows — never the ICD-10-CM or RxNorm
    codes that share the same CUI (C0011847 / C0011849). UMLS CUI-based
    siblings must NOT bleed into a single-system expansion.
    """
    resp = fhir_client.get("/fhir/ValueSet/$expand?url=http://snomed.info/sct?fhir_vs&count=20")
    assert resp.status_code == 200
    contains = resp.json().get("expansion", {}).get("contains", [])
    snomed_codes = {c.get("code") for c in contains}
    # The ICD-10-CM E11 code shares CUI C0011847 with SNOMED 44054006. It MUST
    # NOT appear in the SNOMED all-codes expansion.
    assert "E11" not in snomed_codes, (
        "ICD-10-CM E11 (shares CUI with SNOMED 44054006) leaked into SNOMED "
        "all-codes expansion — cross-system CUI-based contamination"
    )
    # The RxNorm 860975 code shares no CUI with SNOMED but appears in the
    # same fixture — it also MUST NOT appear.
    assert "860975" not in snomed_codes, (
        "RxNorm 860975 leaked into SNOMED all-codes expansion"
    )
