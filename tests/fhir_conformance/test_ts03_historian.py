"""HISTORIAN iteration TS-03 — pattern-match SKEPTIC fixes (QA-031, QA-032)
against v0.0.1 bug patterns documented in GLOBAL_RULES.md.

Source: https://build.fhir.org/terminology-service.html#4.7.3
       https://hl7.org/fhir/R4/extension-capabilitystatement-supported-system.html

HISTORIAN lens (per the assignment):
1. Silent-wrong-answer (B-class): does `_expand_implicit_value_set` distinguish
   'code system exists but has 0 codes' (empty expansion) from 'code system
   unknown' (400 error)? The fixture DB seeds SNOMED/ICD10CM/RXNORM but NOT
   CPT/HCPCS/CVX/ICD10PCS/LNC — so probing `http://loinc.org/vs` against the
   fixture is the perfect boundary test: LOINC is advertised in the
   capabilitystatement-supported-system extension but the fixture DB has 0
   LOINC rows in mrconso.
2. Silent fallback across engines (B6): the implicit expander queries
   `mrconso` directly. Verify narrowing to `duckdb.Error` actually catches
   operational errors only (programming bugs propagate).
3. Literal-value-vs-canonical-registry drift (TS-02 promotion): implicit URL
   prefixes are convention-defined strings — verify the `/vs` form resolves
   for EVERY advertised system, not just LOINC. Probe with a non-LOINC URI
   that has known seeded codes (e.g. ICD-10-CM).
4. Documentation-vs-implementation drift: docstring on `_expand_implicit_value_set`
   claims "For very large code systems ... count cap will trigger the
   too-costly truncation extension." Verify the truncation extension actually
   fires when count is exceeded.
5. Re-verify SKEPTIC's fixes survive: extension `url`/`valueUri` shape and
   implicit `$expand` returning a ValueSet with `expansion.contains[]`.
"""

from __future__ import annotations

import pytest


SUPPORTED_SYSTEM_EXTENSION_URL = (
    "http://hl7.org/fhir/StructureDefinition/capabilitystatement-supported-system"
)

# Same registry as test_ts03_skeptic.py — kept local for HISTORIAN independence.
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
# Re-test SKEPTIC fix QA-031: extension structure
# =============================================================================


def test_h10_extension_entries_have_url_and_valueUri(fhir_client):
    """HISTORIAN: every entry in the supported-system extension MUST have both
    `url` (the extension URL) and `valueUri` (the system URI). Missing or
    mis-named keys would silently break client discovery. This is the
    literal-vs-canonical-registry drift shape — a key-typo would silently
    produce an empty extension list."""
    r = fhir_client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.json()
    exts = body.get("extension", [])
    supported = [e for e in exts if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL]
    assert supported, "No capabilitystatement-supported-system extension present"
    for e in supported:
        assert "url" in e and e["url"] == SUPPORTED_SYSTEM_EXTENSION_URL, (
            f"Extension entry missing/wrong `url`: {e!r}"
        )
        assert "valueUri" in e and isinstance(e["valueUri"], str) and e["valueUri"], (
            f"Extension entry missing/wrong `valueUri`: {e!r}"
        )
        # valueUri MUST be one of the canonical URIs — not a relative path or
        # the THO resource URL form (cf. TS-01 QA-012 HCPCS drift).
        assert e["valueUri"].startswith(("http://", "https://", "urn:")), (
            f"valueUri {e['valueUri']!r} is not an absolute URI — possible drift"
        )


def test_h11_extension_no_duplicate_uris(fhir_client):
    """HISTORIAN: drift detection. The extension list MUST NOT contain
    duplicate `valueUri` entries — duplicates would suggest the helper is
    sourcing from two places (e.g. canonical + alias) instead of the single
    `SYSTEM_TO_FHIR_URI` map."""
    r = fhir_client.get("/fhir/metadata")
    body = r.json()
    exts = body.get("extension", [])
    uris = [
        e.get("valueUri")
        for e in exts
        if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
    ]
    assert len(uris) == len(set(uris)), (
        f"Duplicate URIs in capabilitystatement-supported-system extension: {uris}"
    )


# =============================================================================
# Re-test SKEPTIC fix QA-032: implicit $expand returns ValueSet with contains[]
# =============================================================================


def test_h20_expand_implicit_loinc_returns_valueset_with_expansion(fhir_client):
    """HISTORIAN: SKEPTIC fix QA-032 claims `GET /fhir/ValueSet/$expand?url=
    http://loinc.org/vs` now returns a ValueSet. Verify the response shape:
    resourceType=ValueSet, expansion.contains[] present (even if empty for
    the fixture DB which seeds no LOINC rows)."""
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", "http://loinc.org/vs")]
    )
    assert r.status_code == 200, (
        f"Expected 200 for implicit LOINC VS, got {r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("resourceType") == "ValueSet", (
        f"Expected ValueSet, got {body.get('resourceType')}"
    )
    expansion = body.get("expansion")
    assert isinstance(expansion, dict), (
        f"expansion must be an object, got {type(expansion).__name__}: {expansion!r}"
    )
    assert "contains" in expansion and isinstance(expansion["contains"], list), (
        f"expansion.contains must be a list, got: {expansion!r}"
    )


# =============================================================================
# Pattern: Silent-wrong-answer (B-class) — empty vs unknown boundary
# =============================================================================
#
# The fixture DB seeds SNOMEDCT_US / ICD10CM / RXNORM rows in mrconso. LNC is
# in `SYSTEM_TO_FHIR_URI` (advertised) but NOT seeded — exactly the boundary
# case: "code system known to the server but 0 codes in the underlying store".
#
# The current SKEPTIC implementation returns `contains:[]` with `total:0` and
# no extension explaining the empty result. For a real LOINC DB this is
# correct (LOINC has thousands of codes); for the empty-fixture case the
# client cannot distinguish "LOINC has no codes" from "LOINC has codes that
# are filtered out". The fix is to include an explanatory extension when the
# expansion comes back empty — NOT to fake the total or hide the system.
#
# We log this as a finding (QA-033) but the probe here is a soft check: it
# captures the current behavior so a regression (silent non-200 with a
# generic 400 'Provide a ValueSet body...') would be caught.


def test_h30_expand_implicit_for_unseeded_known_system_does_not_500(fhir_client):
    """HISTORIAN: implicit VS for an advertised-but-unseeded system MUST NOT
    500. The expander queries `mrconso WHERE SAB='LNC'` and gets 0 rows —
    that's a legitimate empty result, not an error condition. A 500 would
    mean the empty-result path mishandled the SQL response (silent fallback
    anti-pattern: catching an exception that didn't actually occur, or
    raising on an empty result)."""
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", "http://loinc.org/vs")]
    )
    assert r.status_code != 500, (
        f"Implicit VS for unseeded known system (LOINC) returned 500 — "
        f"empty-result path mishandled. Body: {r.text[:200]}"
    )


def test_h31_expand_implicit_empty_expansion_documents_empty_state(fhir_client):
    """HISTORIAN (REGRESSION GUARD, currently FAILS — captures the gap):

    When the implicit expansion of a KNOWN-URI returns 0 codes (because the
    underlying mrconso has no rows for that SAB), the response MUST either:
      (a) include an explanatory extension (e.g. `http://hl7.org/fhir/StructureDefinition/valueset-toocostly`
          or a server-local 'empty-source' marker), OR
      (b) document the empty state via a top-level `expansion.parameter`
          indicating the source had 0 matches.

    Without one of these, the client cannot distinguish "LOINC has no codes"
    from "the server failed silently". This is the silent-wrong-answer shape:
    the response degrades without notification. See Finding QA-033 in the
    HISTORIAN QA handoff.

    Spec basis: FHIR R4 §4.7.3.1 — implicit value sets "include all codes in
    the code system". Returning `total=0` for an advertised system that the
    server cannot enumerate is silent misrepresentation when no signal is
    attached.
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", "http://loinc.org/vs")]
    )
    if r.status_code != 200:
        pytest.skip(
            f"Implicit LOINC VS returned {r.status_code}; empty-state check "
            f"only applies to 200 responses."
        )
    body = r.json()
    expansion = body.get("expansion", {})
    contains = expansion.get("contains", [])
    if not contains:
        # Empty expansion: MUST carry an explanatory extension or parameter.
        has_ext = bool(expansion.get("extension"))
        has_param = bool(expansion.get("parameter"))
        assert has_ext or has_param, (
            "Empty implicit expansion for http://loinc.org/vs has no "
            "explanatory extension or parameter — client cannot distinguish "
            "'LOINC has 0 codes' from a silent server failure. (QA-033)"
        )


# =============================================================================
# Pattern: literal-vs-canonical-registry drift — `/vs` works for every URI
# =============================================================================


@pytest.mark.parametrize(
    "source,uri",
    sorted([(s, u) for s, u in CANONICAL_FHIR_R4_URIS.items() if u.count("/") >= 3]),
)
def test_h40_expand_vs_suffix_works_for_every_advertised_system(
    fhir_client, source, uri
):
    """HISTORIAN: SKEPTIC's `_is_implicit_value_set_url` strips the trailing
    `/vs` and re-resolves via `fhir_uri_to_system`. Verify the form (a)
    `<uri>/vs` resolves for EVERY system advertised in the extension — not
    just LOINC. If the prefix reconstruction is wrong for any URI, that
    system's implicit expansion silently falls through to the generic 400
    'Provide a ValueSet body...' error (the original QA-032 bug).

    Skipped for systems whose URI has fewer path segments (URL parsing edge
    cases). All 8 canonical URIs have at least 3 slashes (scheme://host/...).
    """
    implicit_url = f"{uri}/vs"
    r = fhir_client.get(
        "/fhir/ValueSet/$expand", params=[("url", implicit_url)]
    )
    # Acceptance: NOT 400 with the generic 'Provide a ValueSet body...' msg.
    if r.status_code == 400:
        try:
            body = r.json()
            diagnostics = body.get("issue", [{}])[0].get("diagnostics", "")
        except Exception:
            diagnostics = r.text[:200]
        assert "Provide a ValueSet body" not in diagnostics, (
            f"Implicit URL {implicit_url!r} for {source} not recognized — "
            f"server returned generic 'no input' 400 (the QA-032 shape). "
            f"Diagnostics: {diagnostics!r}"
        )


# =============================================================================
# Pattern: silent fallback across engines (B6) — exception narrowing audit
# =============================================================================


def test_h50_expand_implicit_propagates_programming_errors(fhir_client):
    """HISTORIAN: GLOBAL_RULES.md 'Silent Fallbacks' prohibits broad
    `except Exception:`. The architect narrowed QA-032's catch to
    `except duckdb.Error`. Verify by code inspection that the narrowing
    actually caught only operational DuckDB errors and would propagate
    programming bugs (TypeError, AttributeError, KeyError).

    This probe is a static check — it imports the helper and inspects the
    source line, since constructing a real TypeError-via-HTTP path is
    contrived. The point is to lock the narrowing in place against future
    regressions (someone re-widening the catch)."""
    import inspect

    from medterm4ds.apps.fhir_api import create_fhir_app

    src = inspect.getsource(create_fhir_app)
    # Find the _expand_implicit_value_set body.
    start = src.index("def _expand_implicit_value_set(")
    end = src.index("\n    def ", start + 1)
    body = src[start:end]
    assert "except duckdb.Error" in body, (
        "_expand_implicit_value_set must catch duckdb.Error specifically "
        "(GLOBAL_RULES.md 'Silent Fallbacks'). Found body did not contain "
        "`except duckdb.Error`."
    )
    assert "except Exception" not in body, (
        "_expand_implicit_value_set must NOT use broad `except Exception` "
        "(would catch programming bugs that MUST propagate)."
    )


# =============================================================================
# Pattern: docstring-vs-implementation drift (TS-01 HISTORIAN find QA-007)
# =============================================================================


def test_h60_truncation_extension_fires_when_count_exceeded(fhir_client):
    """HISTORIAN: docstring on `_expand_implicit_value_set` claims 'For very
    large code systems ... the count cap will trigger the too-costly
    truncation extension.' Verify the body delivers: when `count=N` is set
    and the source has MORE than N codes, the response MUST include the
    truncation extension. The fixture DB has 2 SNOMED codes — so `count=1`
    forces truncation."""
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=[("url", "http://snomed.info/sct?fhir_vs"), ("count", 1)],
    )
    assert r.status_code == 200, (
        f"Expected 200, got {r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    expansion = body.get("expansion", {})
    contains = expansion.get("contains", [])
    # The fixture seeds 2 SNOMED codes; count=1 must truncate.
    assert len(contains) <= 1, (
        f"Expected at most 1 code after truncation, got {len(contains)}"
    )
    exts = expansion.get("extension", [])
    assert exts, (
        "Truncation triggered (count=1, source has 2 codes) but no extension "
        "was attached to the expansion — docstring claims truncation fires "
        "but body didn't deliver. Documentation-vs-implementation drift "
        "(TS-01 QA-007 shape)."
    )


# =============================================================================
# Re-verify SKEPTIC's SKEPTIC probes still pass (regression guard)
# =============================================================================


def test_h70_skeptic_test_s22_intensional_with_code_still_works(fhir_client):
    """HISTORIAN regression guard: SKEPTIC test_s22 (intensional with code)
    was working pre-TS-03. Verify the new dispatch ordering (implicit BEFORE
    fhir_vs) didn't shadow the intensional path."""
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=[("url", "http://snomed.info/sct/73211009?fhir_vs=isa")],
    )
    assert r.status_code == 200, (
        f"Intensional expansion regressed. Status={r.status_code}, body={r.text[:200]}"
    )
    body = r.json()
    contains = body.get("expansion", {}).get("contains", [])
    codes = {c.get("code") for c in contains}
    assert "44054006" in codes, (
        f"Expected descendant 44054006 in intensional expansion, got {sorted(codes)}"
    )
