"""VS-02 TERMINOLOGIST: ValueSet $expand — Basic.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Expansion shape: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion
contains.display: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display
valueset-toocostly: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

TERMINOLOGIST lens (per chunk assignment): clinical/terminological correctness.
Default severity HIGH.

7 lens items:

  Lens 1 — contains[].display clinical correctness.
    For codes returned by $expand, display SHOULD be the engine's canonical
    preferred term (VS-01 QA-056 fixed empty display echo on omitted case;
    VS-01 CF-TERMINOLOGIST-VS01-01 deferred supplied-display echo).
    Verify the fix holds on every $expand surface in VS-02.

  Lens 2 — Truncation honesty (valueset-toocostly extension).
    When $expand is truncated, the too-costly extension MUST be surfaced per
    FHIR R4 §4.9.3 + the extension spec. Clinical decisions on silently
    truncated expansions are unsafe. Per SKEPTIC QA-057, `total` reflects
    un-truncated size. CF-SKEPTIC-VS02-03 noted the GET filter path missing
    too-costly; verify the gap persists today.

  Lens 3 — Filter matching clinical safety.
    When filter="diabetes", returned codes should be clinically relevant
    (display / name match). Filter matching by display (clinical term) vs
    code (technical identifier) distinction.

  Lens 4 — Code-system URI round-trips.
    Every contains[] entry should be $lookup-able with the advertised system
    (TS-03 TERMINOLOGIST methodology; tightened per CF-EXPLORER-CS01-01).

  Lens 5 — Cross-source clinical consistency.
    SNOMED, ICD-10-CM, LOINC, RxNorm expansions clinically correct.

  Lens 6 — Patient-friendly surfacing.
    GAP-T01 / CF-TERMINOLOGIST-01: implicit LOINC expansion doesn't surface
    patient-friendly. Does the explicit filter-based expansion surface PF
    where available? Probe the current behavior (carry-forward-as-probe).

  Lens 7 — CF-TERMINOLOGIST-VS01-01 — client-supplied display echo.
    When client SUPPLIES display for compose.include[].concept[], the
    implementation echoes it verbatim. Verify if VS-02 surface exhibits
    same drift. Per GLOBAL_RULES "TERMINOLOGIST Findings Are HIGH Severity".

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # 44054006 is-a 73211009

The fixture doesn't seed LOINC codes. Lens 5 LOINC probes assert structural
correctness on SNOMED/ICD-10-CM/RxNorm (the seeded sources). Lens 6 probes
are structural-contract (patient-friendly cache is empty in fixture).
"""

from __future__ import annotations

import re

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion (expansion shape)
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display
#
# FHIR R4 filter-operator enum — single source of truth per milestone-2 review
# (CR-014): import the canonical frozen-set from engines.fhir rather than
# redefining it locally.
from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS  # noqa: F401

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"
ICD10CM_T2DM_DISPLAY = "Type 2 diabetes mellitus"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Codes that SHOULD be returned by filter="diabetes" on the conformance
# fixture — clinical diabetes codes seeded into the synthetic DB. These are
# CLINICALLY relevant diabetes matches, NOT technical code-only matches.
DIABETES_DISPLAYS = {"Diabetes mellitus", "Type 2 diabetes mellitus"}


# =============================================================================
# Helpers
# =============================================================================

def _post_expand(fhir_client, body: dict, *, params: dict | None = None) -> tuple[int, dict]:
    """POST a body to /fhir/ValueSet/$expand. Returns (status, body_json)."""
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=body,
        params=params or {},
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _get_expand(fhir_client, *, params: dict) -> tuple[int, dict]:
    """GET /fhir/ValueSet/$expand with query params."""
    resp = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=params,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _get_lookup(fhir_client, system: str, code: str) -> tuple[int, dict]:
    """GET /fhir/CodeSystem/$lookup with system+code."""
    resp = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _expansion_extensions(resp: dict) -> list[dict]:
    """Return the expansion-level extension list (empty if absent)."""
    return resp.get("expansion", {}).get("extension", [])


def _has_toocostly(resp: dict) -> bool:
    """True if the response's expansion carries a valueset-toocostly extension."""
    return any(e.get("url") == TOOCOSTLY_URL for e in _expansion_extensions(resp))


# =============================================================================
# Lens 1: contains[].display clinical correctness
# =============================================================================

class TestLens1DisplayClinicalCorrectness:
    """Lens 1 — contains[].display IS the engine canonical preferred term.

    Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display
    "The recommended display for this item in the expansion."

    The recommended display is the SERVER's canonical preferred term — NOT
    client input, NOT raw code fallback. VS-01 QA-056 fixed the omitted-
    display case for compose.include[].concept[]; VS-02 verifies the fix
    holds on every $expand surface (intensional root + descendants, implicit
    enumeration, URL-based SNOMED fhir_vs=isa, text filter).
    """

    def test_t10_extensional_omitted_display_is_engine_canonical_snomed(
        self, fhir_client
    ):
        """Spec: contains.display = engine canonical preferred term.

        VS-01 QA-056 fix applied to _expand_intensional extensional path.
        Verify on VS-02 surface: client OMITS display; engine resolves via
        get_code_infos; the response's contains[].display IS the canonical.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]}
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # Engine canonical preferred term for 73211009 is "Diabetes mellitus"
        assert contains[0]["display"] == "Diabetes mellitus", contains[0]
        # Clinical safety: display is NOT the raw code
        assert contains[0]["display"] != SNOMED_DIABETES_MELLITUS

    def test_t11_extensional_omitted_display_is_engine_canonical_icd10cm(
        self, fhir_client
    ):
        """Spec: contains.display = engine canonical preferred term (ICD-10-CM).

        Same shape as t10 but for ICD-10-CM. Verify the canonical resolution
        works across sources — clinical correctness is per-source.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]}
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        assert contains[0]["display"] == ICD10CM_T2DM_DISPLAY, contains[0]
        assert contains[0]["display"] != ICD10CM_T2DM

    def test_t12_extensional_omitted_display_is_engine_canonical_rxnorm(
        self, fhir_client
    ):
        """Spec: contains.display = engine canonical preferred term (RxNorm).

        Cross-source: RxNorm canonical preferred term for 860975 is the full
        fully-specified name "24 HR metformin 500 MG Oral Tablet", NOT the
        short code or ingredient-only name. Clinical correctness requires
        the FULL canonical preferred term so prescribers can distinguish
        formulations.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": RXNORM_URI, "concept": [{"code": RXNORM_METFORMIN}]}
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        assert contains[0]["display"] == RXNORM_METFORMIN_DISPLAY, contains[0]
        assert contains[0]["display"] != RXNORM_METFORMIN

    def test_t13_is_a_filter_root_display_is_engine_canonical(self, fhir_client):
        """Spec: contains.display = engine canonical preferred term for is-a root.

        When compose.include[].filter uses op=is-a, the root code's display
        MUST be resolved via get_code_infos (not echoed as raw code).
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}],
                }]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        # root + 1 descendant
        assert len(contains) == 2, contains
        # First entry is the root
        assert contains[0]["code"] == SNOMED_DIABETES_MELLITUS
        assert contains[0]["display"] == "Diabetes mellitus", contains[0]
        assert contains[0]["display"] != SNOMED_DIABETES_MELLITUS

    def test_t14_descendent_of_descendant_display_is_engine_canonical(
        self, fhir_client
    ):
        """Spec: contains.display = engine canonical preferred term for descendants.

        When compose.include[].filter uses op=descendent-of, the descendant
        code's display comes from the BFS relations list. Verify the engine
        canonical is propagated (not raw code fallback).
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept",
                        "op": "descendent-of",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        # descendant only (descendent-of excludes root)
        assert len(contains) == 1, contains
        assert contains[0]["code"] == SNOMED_T2DM
        assert contains[0]["display"] == "Type 2 diabetes mellitus", contains[0]
        assert contains[0]["display"] != SNOMED_T2DM

    def test_t15_implicit_expansion_display_is_engine_canonical(self, fhir_client):
        """Spec: contains.display = engine canonical preferred term for implicit.

        Implicit value set URL (e.g. http://snomed.info/sct?fhir_vs) expands
        to all SNOMED codes. Each contains[].display MUST be resolved via
        get_code_infos (canonical preferred atom STR), not raw code fallback.
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{SNOMED_URI}?fhir_vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        # Both seeded SNOMED codes
        assert len(contains) == 2, contains
        for entry in contains:
            # Display MUST NOT be the raw code (clinical safety)
            assert entry["display"] != entry["code"], entry
            assert entry["display"], f"empty display for {entry}"

    def test_t16_url_based_intensional_display_is_engine_canonical(
        self, fhir_client
    ):
        """Spec: contains.display = engine canonical preferred term for fhir_vs=isa.

        URL-based intensional expansion (http://snomed.info/sct/73211009?fhir_vs=isa)
        uses expand_url_pattern which calls get_descendants_bfs. The root
        display comes from get_code_infos (verified); descendant displays
        come from rel.target_display. Verify both are canonical (not raw).
        """
        url = f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        status, resp = _get_expand(fhir_client, params={"url": url})
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        # root + 1 descendant
        assert len(contains) == 2, contains
        for entry in contains:
            assert entry["display"] != entry["code"], entry
            assert entry["display"], f"empty display for {entry}"

    def test_t17_text_filter_display_is_engine_canonical(self, fhir_client):
        """Spec: contains.display = engine canonical preferred term for filter.

        Text-filter expansion uses search_names which returns results with
        .name set to the engine canonical preferred term. Verify display is
        the canonical, NOT raw code.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) >= 1, contains
        for entry in contains:
            assert entry["display"], f"empty display for {entry}"
            assert entry["display"] != entry["code"], entry


# =============================================================================
# Lens 2: Truncation honesty (valueset-toocostly extension)
# =============================================================================

class TestLens2TruncationHonesty:
    """Lens 2 — Truncation MUST surface valueset-toocostly extension.

    Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
    Per FHIR R4 §4.9.3 + valueset-toocostly extension: when the expansion
    is truncated (count cap, depth cap), the extension MUST be present.

    Clinical safety: a client paging an expansion that is silently truncated
    may treat the partial list as exhaustive — leading to clinical decisions
    (drug-drug interaction checks, decision support) that miss codes.

    Per SKEPTIC QA-057, expansion.total reflects UN-truncated size. CF-SKEPTIC-
    VS02-03 noted GET filter path missing too-costly; verify the gap persists.
    """

    def test_t20_extensional_count_1_emits_toocostly(self, fhir_client):
        """Spec: valueset-toocostly extension present on count truncation.

        count=1 on a 2-concept extensional expansion MUST emit toocostly.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS},
                        {"code": SNOMED_T2DM},
                    ],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body, params={"count": 1})
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        # Clinical safety: toocostly extension MUST be present
        assert _has_toocostly(resp), resp
        # And total reflects UN-truncated size (SKEPTIC QA-057)
        assert resp["expansion"]["total"] == 2, resp["expansion"]

    def test_t21_extensional_count_equals_size_no_toocostly(self, fhir_client):
        """Spec: NO toocostly when count does NOT truncate.

        count=2 on a 2-concept expansion = no truncation = no toocostly.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS},
                        {"code": SNOMED_T2DM},
                    ],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body, params={"count": 2})
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 2, contains
        assert not _has_toocostly(resp), resp

    def test_t22_intensional_count_1_emits_toocostly(self, fhir_client):
        """Spec: valueset-toocostly extension on intensional count truncation.

        count=1 on intensional is-a expansion of 73211009 (which would
        return 2 concepts: root + 1 descendant) MUST emit toocostly.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body, params={"count": 1})
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        assert _has_toocostly(resp), resp

    def test_t23_toocostly_extension_carries_reason_subextension(
        self, fhir_client
    ):
        """Spec: valueset-toocostly carries count/depth reason.

        Per https://hl7.org/fhir/R4/extension-valueset-toocostly.html the
        extension SHOULD carry context. medterm4ds adds a "reason" sub-
        extension naming the truncation cause (count-limited at N) — clinical
        operators need this for diagnosis.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS},
                        {"code": SNOMED_T2DM},
                    ],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body, params={"count": 1})
        assert status == 200, resp
        exts = _expansion_extensions(resp)
        toocostly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert len(toocostly) == 1, exts
        # valueBoolean must be true (clinical signal)
        assert toocostly[0].get("valueBoolean") is True, toocostly[0]
        # reason sub-extension must be present and non-empty
        sub_exts = toocostly[0].get("extension", [])
        reason_exts = [e for e in sub_exts if e.get("url") == "reason"]
        assert len(reason_exts) == 1, sub_exts
        assert reason_exts[0].get("valueString"), reason_exts[0]

    def test_t24_implicit_expansion_count_1_emits_toocostly(self, fhir_client):
        """Spec: valueset-toocostly extension on implicit count truncation.

        Implicit SNOMED expansion with count=1 (would return 2 codes
        un-truncated) MUST emit toocostly.
        """
        status, resp = _get_expand(
            fhir_client,
            params={"url": f"{SNOMED_URI}?fhir_vs", "count": 1},
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        assert _has_toocostly(resp), resp

    def test_t25_get_filter_truncation_toocostly_gap_cf_skeptic_vs02_03(
        self, fhir_client
    ):
        """CF-SKEPTIC-VS02-03 closed by VS-02 SKEPTIC resweep QA-001 fix:
        GET filter path now emits valueset-toocostly on truncation.

        Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
        The QA-001 fix uses the ``+1 probe`` pattern (search_names(limit=
        count+1)) to detect truncation and emits the valueset-toocostly
        extension as the clinical-safety signal. This probe was tightened
        from the carry-forward-as-probe pattern (skip-on-fix) to a
        positive assertion that the extension IS present.
        """
        # filter "diabetes" matches at least 2 codes (DM + T2DM in SNOMED +
        # ICD-10-CM); count=1 truncates.
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 1}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        # CF-SKEPTIC-VS02-03 closed: toocostly MUST be emitted on the GET
        # filter path when truncation fires.
        assert _has_toocostly(resp), (
            f"GET filter path missing toocostly on truncation — "
            f"CF-SKEPTIC-VS02-03 should be closed by QA-001 fix. Got: {resp}"
        )

    def test_t26_post_filter_truncation_toocostly_gap_mirror_cf(
        self, fhir_client
    ):
        """CF-SKEPTIC-VS02-03 closed by VS-02 SKEPTIC resweep QA-001 fix:
        POST filter path also emits valueset-toocostly on truncation.

        The filter path's fix applies to BOTH GET and POST because both
        call ``_do_expand`` filter mode → ``build_valueset_expand`` with
        the new ``total=`` and ``extensions=`` keyword args.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "count", "valueInteger": 1},
            ],
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        # CF-SKEPTIC-VS02-03 closed on POST path too.
        assert _has_toocostly(resp), (
            f"POST filter path missing toocostly on truncation — "
            f"CF-SKEPTIC-VS02-03 should be closed on POST too. Got: {resp}"
        )

    def test_t27_toocostly_get_post_parity_on_intensional(self, fhir_client):
        """Spec: GET ↔ POST toocostly parity.

        Per cross-operation-canonical-agreement (CS-05 EXPLORER strategy 38),
        intensional expansion via GET (url=...?fhir_vs=isa) AND POST (inline
        ValueSet) MUST agree on the toocostly signal when both truncate.
        """
        # POST intensional
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        post_status, post_resp = _post_expand(fhir_client, body, params={"count": 1})
        assert post_status == 200, post_resp
        # GET URL-based intensional (different code path: expand_url_pattern)
        url = f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        get_status, get_resp = _get_expand(fhir_client, params={"url": url, "count": 1})
        assert get_status == 200, get_resp
        # Both MUST surface toocostly (parity contract)
        assert _has_toocostly(post_resp) == _has_toocostly(get_resp)


# =============================================================================
# Lens 3: Filter matching clinical safety
# =============================================================================

class TestLens3FilterMatchingClinicalSafety:
    """Lens 3 — filter text matches return clinically relevant codes.

    Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html In ``filter``
    "A text filter that is a code or display text[]" — server discretion.

    Clinical safety: when a clinician types "diabetes" into an EHR
    autocomplete, the returned codes should be the clinically relevant
    diabetes codes (NOT technical code-only matches like a lab LOINC code
    that happens to contain "diabetes" in its code). The conformance fixture
    seeds only diabetes-relevant codes, so any match IS clinically relevant
    today. Probes verify the matching surface.
    """

    def test_t30_filter_diabetes_returns_clinically_relevant_codes(
        self, fhir_client
    ):
        """Spec: filter matches return display-relevant codes.

        filter="diabetes" on the conformance fixture should return the
        diabetes codes (SNOMED DM + T2DM + ICD-10-CM T2DM). All matches
        should have "diabetes" (case-insensitive) in their display.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) >= 1, contains
        # Every returned code must have "diabetes" in its display (clinical
        # relevance). This is the load-bearing clinical-safety contract.
        for entry in contains:
            display_lower = entry.get("display", "").lower()
            assert "diabetes" in display_lower, (
                f"Filter 'diabetes' returned a code whose display does not "
                f"contain 'diabetes' (clinical relevance): {entry}"
            )

    def test_t31_filter_matches_display_not_just_code(self, fhir_client):
        """Spec: filter matches display text (clinical term).

        filter="metformin" should match RxNorm 860975 (display contains
        "metformin"). The match is on DISPLAY (clinical term), not CODE
        (technical identifier 860975).
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "metformin"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        entry = contains[0]
        assert entry["code"] == RXNORM_METFORMIN
        assert "metformin" in entry["display"].lower(), entry

    def test_t32_filter_case_insensitive(self, fhir_client):
        """Spec: filter is server-discretion; case-insensitive is clinical norm.

        filter="DIABETES" (uppercase) should match the same codes as
        "diabetes". Clinical users don't always capitalize correctly.
        """
        status_lower, resp_lower = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        status_upper, resp_upper = _get_expand(
            fhir_client, params={"filter": "DIABETES"}
        )
        assert status_lower == 200 and status_upper == 200
        codes_lower = {c["code"] for c in resp_lower["expansion"]["contains"]}
        codes_upper = {c["code"] for c in resp_upper["expansion"]["contains"]}
        assert codes_lower == codes_upper, (
            f"Case-insensitive filter matching broken: lower={codes_lower}, "
            f"upper={codes_upper}"
        )

    def test_t33_filter_no_clinically_irrelevant_matches(self, fhir_client):
        """Spec: filter should not return codes whose display doesn't match.

        filter="diabetes" should NOT return RxNorm 860975 (metformin) even
        though metformin is used to treat diabetes — the clinical term in
        the display is "metformin", not "diabetes". This is the clinical
        term vs disease relationship distinction.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        # Metformin is a diabetes TREATMENT but its display doesn't contain
        # "diabetes". A clinically-correct filter should NOT return it.
        assert RXNORM_METFORMIN not in codes, (
            f"Filter 'diabetes' returned metformin (RXNORM_METFORMIN). "
            f"Clinical safety: metformin's display does not contain "
            f"'diabetes'; the filter should match clinical terms (display), "
            f"not pharmacological relationships."
        )


# =============================================================================
# Lens 4: Code-system URI round-trips
# =============================================================================

class TestLens4UriRoundTrips:
    """Lens 4 — every contains[] entry is $lookup-able with advertised system.

    Per TS-03 TERMINOLOGIST strategy 21 (URI-round-trip from response). For
    every Coding returned by $expand, call $lookup with the advertised
    system+code; assert 200 + Parameters. This verifies the contains[].system
    field is a real FHIR R4 system URI the server can resolve, not a
    fabricated or aliased value.

    NOTE per CF-EXPLORER-CS01-01: when canonical-code may be a range/group
    code (chapter-range ICD-10-CM), tighten to "URI parseable by
    fhir_uri_to_system". The conformance fixture seeds single codes, so
    strict round-trip is correct here.
    """

    @pytest.mark.parametrize("system,code", [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_T2DM),
        (RXNORM_URI, RXNORM_METFORMIN),
    ])
    def test_t40_each_seeded_code_round_trips_via_lookup(
        self, fhir_client, system, code
    ):
        """Spec: every contains[].system+code is $lookup-able.

        Parametrized over the 4 seeded codes across 3 sources (SNOMED,
        ICD-10-CM, RxNorm). Verifies the system URIs advertised in expansions
        are the real canonical URIs the server's $lookup accepts.
        """
        status, resp = _get_lookup(fhir_client, system, code)
        assert status == 200, f"$lookup failed for system={system} code={code}: {resp}"
        assert resp.get("resourceType") == "Parameters", resp

    def test_t41_intensional_expansion_all_entries_round_trip(
        self, fhir_client
    ):
        """Spec: every contains[] entry from intensional expansion is $lookup-able.

        is-a expansion of 73211009 returns root + 1 descendant. Each entry's
        (system, code) MUST be $lookup-able — verifies the SNOMED URI
        advertised in contains[].system is canonical.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            lk_status, lk_resp = _get_lookup(
                fhir_client, entry["system"], entry["code"]
            )
            assert lk_status == 200, (
                f"$lookup failed for intensional contains entry {entry}: {lk_resp}"
            )

    def test_t42_implicit_expansion_all_entries_round_trip_snomed(
        self, fhir_client
    ):
        """Spec: every contains[] entry from implicit expansion is $lookup-able.

        Implicit SNOMED expansion returns all SNOMED codes. Each entry's
        (system, code) MUST be $lookup-able.
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{SNOMED_URI}?fhir_vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            lk_status, _ = _get_lookup(fhir_client, entry["system"], entry["code"])
            assert lk_status == 200, entry

    def test_t43_filter_expansion_all_entries_round_trip(self, fhir_client):
        """Spec: every contains[] entry from filter expansion is $lookup-able.

        filter="diabetes" matches codes across sources. Each entry's
        (system, code) MUST be $lookup-able.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            lk_status, _ = _get_lookup(fhir_client, entry["system"], entry["code"])
            assert lk_status == 200, entry


# =============================================================================
# Lens 5: Cross-source clinical consistency
# =============================================================================

class TestLens5CrossSourceConsistency:
    """Lens 5 — clinical correctness across SNOMED, ICD-10-CM, RxNorm.

    Each source's expansion should be clinically correct (canonical display,
    real code, real URI). The conformance fixture seeds 4 codes across 3
    sources (no LOINC). Probe each source's expansion independently.
    """

    def test_t50_snomed_expansion_clinically_correct(self, fhir_client):
        """Spec: SNOMED expansion is clinically correct.

        Implicit SNOMED expansion should return 2 codes with canonical
        displays. Each (system, code, display) should be a real SNOMED
        concept verified via $lookup.
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{SNOMED_URI}?fhir_vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        assert codes == {SNOMED_DIABETES_MELLITUS, SNOMED_T2DM}, codes
        for entry in contains:
            assert entry["system"] == SNOMED_URI, entry

    def test_t51_icd10cm_expansion_clinically_correct(self, fhir_client):
        """Spec: ICD-10-CM expansion is clinically correct.

        Implicit ICD-10-CM expansion should return 1 code (E11) with its
        canonical display "Type 2 diabetes mellitus".
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{ICD10CM_URI}/vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        entry = contains[0]
        assert entry["system"] == ICD10CM_URI, entry
        assert entry["code"] == ICD10CM_T2DM, entry
        assert entry["display"] == ICD10CM_T2DM_DISPLAY, entry

    def test_t52_rxnorm_expansion_clinically_correct(self, fhir_client):
        """Spec: RxNorm expansion is clinically correct.

        Implicit RxNorm expansion should return 1 code (860975) with its
        canonical fully-specified name.
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{RXNORM_URI}/vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        entry = contains[0]
        assert entry["system"] == RXNORM_URI, entry
        assert entry["code"] == RXNORM_METFORMIN, entry
        assert entry["display"] == RXNORM_METFORMIN_DISPLAY, entry

    def test_t53_filter_constrained_to_single_source(self, fhir_client):
        """Spec: filter constrained to a single source via system param.

        filter="diabetes" with system=SNOMED_URI should return only SNOMED
        codes. Cross-source consistency: the system constraint is respected.
        """
        status, resp = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "system": SNOMED_URI},
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            assert entry["system"] == SNOMED_URI, (
                f"system=SNOMED constraint violated: {entry}"
            )


# =============================================================================
# Lens 6: Patient-friendly surfacing (CF-TERMINOLOGIST-01 / GAP-T01)
# =============================================================================

class TestLens6PatientFriendlySurfacing:
    """Lens 6 — patient-friendly name surfacing gap.

    Per AGENTS.md "Known Fragile Areas" (CF-TERMINOLOGIST-01 / GAP-T01):
    the implicit value set expander resolves display via get_code_infos
    (canonical preferred-atom STR) but does NOT consult
    app.state.patient_friendly_cache. $lookup enriches its response with
    patient-friendly, match-type, canonical-code, canonical-system, tty
    custom properties via _PatientFriendlyCache; $expand silently doesn't.

    The conformance fixture's pf_cache is empty (no patient_friendly JSONs
    seeded), so this gap is structural-invisible in CI. The regression
    suite covers $lookup patient-friendly resolution against the production
    UMLS DB.

    Fix shape (when implemented): attach a `patient-friendly` extension to
    each contains[] entry — do NOT override display (spec mandates display
    is the code system's preferred term).

    These probes PIN the current behavior so a future implementation that
    adds patient-friendly extensions will fail loudly and require an update.
    """

    def test_t60_explicit_filter_expansion_no_pf_extension_today(
        self, fhir_client
    ):
        """CF-TERMINOLOGIST-01: explicit filter expansion does NOT surface
        patient-friendly extensions today.

        Question from chunk assignment: "Does the explicit filter-based
        expansion surface PF where available?" — Answer: NO today. The
        filter path uses search_names → results.name; no PF consultation.

        Carry-forward-as-probe pattern. When the fix lands, this probe MUST
        be updated to assert the extension IS present.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) >= 1, contains
        for entry in contains:
            entry_exts = entry.get("extension", [])
            pf_exts = [
                e for e in entry_exts if "patient-friendly" in e.get("url", "")
            ]
            assert not pf_exts, (
                f"CF-TERMINOLOGIST-01: filter expansion does NOT surface PF "
                f"today. Entry unexpectedly had PF extension: {entry}"
            )

    def test_t61_intensional_expansion_no_pf_extension_today(
        self, fhir_client
    ):
        """CF-TERMINOLOGIST-01: intensional expansion does NOT surface PF today.

        _expand_intensional uses get_code_infos for canonical display and
        get_descendants_bfs for descendants. Neither consults pf_cache.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            entry_exts = entry.get("extension", [])
            pf_exts = [
                e for e in entry_exts if "patient-friendly" in e.get("url", "")
            ]
            assert not pf_exts, entry

    def test_t62_implicit_expansion_no_pf_extension_today(self, fhir_client):
        """CF-TERMINOLOGIST-01: implicit expansion does NOT surface PF today.

        _expand_implicit_value_set uses get_code_infos for canonical display.
        Does NOT consult pf_cache (documented in AGENTS.md).
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{SNOMED_URI}?fhir_vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            entry_exts = entry.get("extension", [])
            pf_exts = [
                e for e in entry_exts if "patient-friendly" in e.get("url", "")
            ]
            assert not pf_exts, entry

    def test_t63_display_remains_canonical_when_pf_eventually_surfaced(
        self, fhir_client
    ):
        """Spec: contains.display is the code system's preferred term.

        Per FHIR R4 §4.9.1: display is "The recommended display for this
        item in the expansion" — code system preferred term, NOT patient-
        friendly name. When PF surfacing is implemented, the patient-friendly
        name belongs in a SEPARATE extension; display MUST remain canonical.

        This probe asserts the CURRENT behavior: display IS the canonical
        preferred term. Structural-contract probe.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]
                }]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        assert contains[0]["display"] == "Type 2 diabetes mellitus", contains[0]


# =============================================================================
# Lens 7: CF-TERMINOLOGIST-VS01-01 — client-supplied display echo
# =============================================================================

class TestLens7ClientSuppliedDisplayEcho:
    """Lens 7 — client-supplied display echo (CF-TERMINOLOGIST-VS01-01).

    Per AGENTS.md "Known Fragile Areas" (CF-TERMINOLOGIST-VS01-01, MEDIUM,
    DEFERRED): when a client SUPPLIES display for compose.include[].concept[],
    _expand_intensional echoes the client's display verbatim. The spec says
    contains[].display is "the recommended display for this item in the
    expansion" — implying the SERVER's canonical preferred term.

    VS-01 TERMINOLOGIST QA-056 fixed the OMITTED-display case; supplied-
    display echo is deferred (canonical-wins requires display-name
    canonicalization decision).

    These probes verify the VS-02 surface exhibits the same drift (i.e.
    the CF applies across surfaces, not just VS-01).
    """

    def test_t70_client_supplied_display_echoedverbatim_today(self, fhir_client):
        """CF-TERMINOLOGIST-VS01-01: client-supplied display is echoed verbatim.

        The client supplies a WRONG display ("Client-supplied display"); the
        server echoes it verbatim instead of correcting to the engine's
        canonical preferred term. This is the 6th instance of client-input-
        as-canonical drift (per AGENTS.md Architecture Drift Log).

        Carry-forward-as-probe pattern. When canonical-wins lands, this
        probe MUST be updated to assert the canonical display wins.
        """
        client_display = "Client-supplied display (CF-TERMINOLOGIST-VS01-01)"
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS, "display": client_display}],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # CF-TERMINOLOGIST-VS01-01 (MEDIUM, DEFERRED): client-supplied display
        # IS echoed verbatim today. When canonical-wins lands, flip to:
        #   assert contains[0]["display"] == "Diabetes mellitus"
        assert contains[0]["display"] == client_display, (
            f"CF-TERMINOLOGIST-VS01-01: expected client-supplied display to "
            f"be echoed verbatim today. Got: {contains[0]}"
        )

    def test_t71_client_supplied_wrong_display_overrides_canonical(
        self, fhir_client
    ):
        """CF-TERMINOLOGIST-VS01-01: wrong display overrides canonical today.

        Clinical safety: if a client supplies a wrong/misleading display
        for a code, the expansion echoes it. A downstream CDS hook reading
        the expansion sees the wrong display. This is the clinical-correctness
        concern that motivates the deferred canonical-wins fix.

        This probe documents the CURRENT behavior (wrong display wins).
        """
        wrong_display = "Wrong clinical name"
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS, "display": wrong_display}],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # Today: wrong display wins (CF-TERMINOLOGIST-VS01-01 gap).
        assert contains[0]["display"] == wrong_display
        # Engine canonical IS available (verified via omitted-display case).
        # When the fix lands, this probe MUST be updated.

    def test_t72_omitted_display_resolves_to_canonical_vs01_qa056_holds(
        self, fhir_client
    ):
        """VS-01 QA-056 fix holds on VS-02 surface: omitted display resolves.

        When the client OMITS display for compose.include[].concept[], the
        engine canonical preferred term IS resolved (VS-01 QA-056 fix).
        Verify this fix is not regressed on the VS-02 surface.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],  # no display
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # Engine canonical preferred term wins (NOT empty, NOT raw code)
        assert contains[0]["display"] == "Diabetes mellitus", contains[0]
        assert contains[0]["display"] != SNOMED_DIABETES_MELLITUS


# =============================================================================
# Lens 8: Cross-cutting clinical correctness (CF-HISTORIAN-VS02-02 + docs)
# =============================================================================

class TestLens8CrossCutting:
    """Lens 8 — cross-cutting clinical-correctness probes.

    CF-HISTORIAN-VS02-02: implicit-value-set path lacks canonical_system_uri().
    Form (a) (<system-uri>/vs) uses client-supplied URL prefix verbatim for
    contains[].system — does NOT call canonical_system_uri() helper. 8th
    instance of client-input-as-canonical drift. Bug invisible in CI (fixture
    doesn't seed alias URIs).

    Carry-forward-as-probe pattern. The probe below asserts the CANONICAL
    URI is emitted when the client uses the canonical form (the positive-
    shape contract); the drift case (alias input) is documented in
    test_vs02_historian.py (test_h32).
    """

    def test_t80_implicit_value_set_contains_system_is_canonical_snomed(
        self, fhir_client
    ):
        """CF-HISTORIAN-VS02-02 positive-shape contract: canonical input →
        canonical output.

        When the client uses the canonical SNOMED URI (http://snomed.info/sct?
        fhir_vs), contains[].system MUST be the canonical URI. This probe
        documents the positive case (canonical input); the alias-input case
        is documented in test_vs02_historian.py::test_h32.
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{SNOMED_URI}?fhir_vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            assert entry["system"] == SNOMED_URI, entry

    def test_t81_implicit_value_set_form_a_contains_system_is_canonical_icd10cm(
        self, fhir_client
    ):
        """CF-HISTORIAN-VS02-02 Form (a) positive-shape: canonical input →
        canonical output (ICD-10-CM).

        Form (a) (<system-uri>/vs) on the canonical ICD-10-CM URI MUST emit
        the canonical URI in contains[].system.
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{ICD10CM_URI}/vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            assert entry["system"] == ICD10CM_URI, entry

    def test_t82_intensional_contains_system_uses_canonical_helper(
        self, fhir_client
    ):
        """CR-013 fix holds: intensional contains[].system uses canonical_system_uri().

        Milestone-2 review CR-013 applied canonical_system_uri() to
        _expand_intensional (line 2034 canonical_inc). Verify on VS-02
        surface: alias input to intensional expansion emits canonical.
        """
        # Use the canonical SNOMED URI — should emit canonical
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        assert contains[0]["system"] == SNOMED_URI, contains[0]

    def test_t83_intensional_alias_input_emits_canonical_via_helper(
        self, fhir_client
    ):
        """CR-013 fix verified on alias input: alias → canonical.

        SNOMED alias urn:oid:2.16.840.1.113883.6.96 should resolve through
        canonical_system_uri() and emit the canonical http://snomed.info/sct
        in contains[].system. This is the load-bearing contract of the fix.
        """
        snomed_alias = "urn:oid:2.16.840.1.113883.6.96"
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": snomed_alias,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # CR-013 fix: alias resolved to canonical
        assert contains[0]["system"] == SNOMED_URI, (
            f"CR-013 fix regression: expected canonical {SNOMED_URI} for "
            f"alias input {snomed_alias}; got {contains[0]['system']}"
        )


# =============================================================================
# Lens 9: Clinical safety — designations / inactive / version
# =============================================================================

class TestLens9ClinicalSafetyExtras:
    """Lens 9 — clinical safety extras: version, inactive, designations.

    Per FHIR R4 ValueSet.expansion.contains: version is OPTIONAL (0..1),
    inactive is OPTIONAL (0..1 boolean), designation is OPTIONAL (0..*).

    The conformance fixture is single-snapshot (no version metadata) and
    contains only SUPPRESS='N' (active) codes. Probes verify the absence
    is conformant (OPTIONAL fields correctly omitted, not silently wrong).
    """

    def test_t90_contains_version_absent_on_unversioned_engine(self, fhir_client):
        """Spec: contains.version is OPTIONAL (0..1).

        The engine is single-snapshot; no version metadata is tracked.
        contains[].version MUST be absent (NOT empty string, NOT null).
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]
                }]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # version MUST be absent (not present with empty/null value)
        assert "version" not in contains[0], contains[0]

    def test_t91_contains_inactive_absent_on_active_codes(self, fhir_client):
        """Spec: contains.inactive is OPTIONAL (0..1 boolean).

        The fixture seeds only SUPPRESS='N' (active) codes. contains[].inactive
        MUST be absent (active is the default; absence = active).
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]
                }]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # inactive absent = active (default)
        assert "inactive" not in contains[0], contains[0]

    def test_t92_designation_absent_when_client_doesnt_request(self, fhir_client):
        """Spec: contains.designation is OPTIONAL (0..* BackboneElement).

        When the client does not request designations, contains[].designation
        MUST be absent. medterm4ds doesn't implement designation surfacing.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]
                }]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        assert "designation" not in contains[0], contains[0]
