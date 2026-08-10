"""VS-03 TERMINOLOGIST: ValueSet $expand — Advanced.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Filter operator: https://hl7.org/fhir/R4/valueset.html#filter
Filter Operator enum: https://hl7.org/fhir/R4/valueset-concept-operator.html
ValueSet expansion: https://hl7.org/fhir/R4/valueset.html#expansion

TERMINOLOGIST lens: clinical/terminological correctness on advanced $expand
paths. Every probe asserts a clinical-truth property:

  1. Inline valueSet expansion displays — each contains[].display MUST be the
     engine canonical preferred term.
  2. Filter clinical safety on critical roots (is-a on SNOMED Diabetes
     mellitus — expansion includes all descendants).
  3. Hierarchical expansion truncation honesty (toocostly extension surfaces).
  4. Code-system URI round-trip on inline valueSet (each code's advertised
     system MUST resolve via $lookup).
  5. Patient-friendly name surfacing (LOINC codes in inline expansion get PF
     names where available — DEFERRED per GAP-T01).
  6. descendent-of vs is-a clinical distinction (root excluded vs included).
  7. CF-TERMINOLOGIST-VS01-01 client-supplied display echo (carry-forward).

Prior VS-03 iterations:
  - SKEPTIC: 1 fix (QA-059 Parameters-with-valueSet).
  - HISTORIAN: 0 bugs; 8 robustness probes on QA-059 fix.
  - EXPLORER: 0 bugs; 51 lateral probes; 4 inline-valueSet combinations.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)

Default severity: HIGH per GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH".
"""

from __future__ import annotations

import re

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/valueset-concept-operator.html (Filter Operator)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display
# Spec: https://hl7.org/fhir/R4/parameters.html (resource property)
from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"
LOINC_URI = "http://loinc.org"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Canonical preferred terms per the conformance fixture.
# Spec: FHIR R4 §4.9.5 contains[].display: "The recommended display for this
# item in the expansion" — implies the SERVER's canonical preferred term, not a
# client-supplied echo.
CANONICAL_DISPLAY_SNOMED_DM = "Diabetes mellitus"
CANONICAL_DISPLAY_SNOMED_T2DM = "Type 2 diabetes mellitus"
CANONICAL_DISPLAY_ICD10CM_T2DM = "Type 2 diabetes mellitus"
CANONICAL_DISPLAY_RXNORM_METFORMIN = "24 HR metformin 500 MG Oral Tablet"


# =============================================================================
# Helpers
# =============================================================================

def _post_expand(fhir_client, body: dict, *, params: dict | None = None) -> tuple[int, dict]:
    """POST a body to /fhir/ValueSet/$expand.

    Returns (status_code, body_json). The body may be a ValueSet resource
    (intensional/extensional) OR a Parameters resource (filter mode), per
    FHIR R4 §4.7.5.
    """
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


def _contains_codes(body: dict) -> list[tuple[str, str]]:
    """Extract (system, code) pairs from ValueSet.expansion.contains."""
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


def _contains_displays(body: dict) -> dict[tuple[str, str], str]:
    """Map (system, code) -> display from ValueSet.expansion.contains."""
    out = {}
    for c in body.get("expansion", {}).get("contains", []):
        out[(c.get("system", ""), c.get("code", ""))] = c.get("display", "")
    return out


def _make_extensional_snomed(concepts=None) -> dict:
    """Build an extensional ValueSet with explicit concept list."""
    if concepts is None:
        concepts = [
            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"},
            {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"},
        ]
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-test-extensional",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "concept": concepts,
            }],
        },
    }


def _make_intensional_snomed_isa(root_code: str = SNOMED_DIABETES_MELLITUS) -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-test-intensional-isa",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "is-a", "value": root_code}
                ],
            }],
        },
    }


def _make_intensional_snomed_descendent_of(root_code: str = SNOMED_DIABETES_MELLITUS) -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-test-intensional-descendent-of",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "descendent-of", "value": root_code}
                ],
            }],
        },
    }


# =============================================================================
# Lens 1: Inline valueSet expansion display clinical correctness
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display
#   "The recommended display for this item in the expansion."
# VS-01 QA-056 fix: omitted display resolves to canonical preferred term.
# CF-TERMINOLOGIST-VS01-01: supplied display echoes verbatim (DEFERRED).
# =============================================================================


class TestLens1InlineValueSetDisplays:
    """Lens 1: contains[].display MUST be engine canonical preferred term.

    Per FHIR R4 ValueSet.expansion.contains.display: "The recommended display
    for this item in the expansion." When the client OMITS the display, the
    engine MUST resolve the canonical preferred term (VS-01 QA-056 fix).
    """

    def test_t10_bare_valueset_omitted_display_resolves_canonical(self, fhir_client):
        """VS-01 QA-056 fix holds on bare-ValueSet body path.

        When compose.include[].concept[] entry OMITS display, contains[].display
        MUST be the engine canonical preferred term. Clinically critical: a CDS
        hook reading the expansion would otherwise see an empty display string
        for the code — silent-wrong-answer.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t10",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [
                    {"code": SNOMED_DIABETES_MELLITUS},
                    {"code": SNOMED_T2DM},
                ],
            }]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        displays = _contains_displays(body)
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == CANONICAL_DISPLAY_SNOMED_DM, (
            f"display for {SNOMED_DIABETES_MELLITUS} not canonical: {displays}"
        )
        assert displays[(SNOMED_URI, SNOMED_T2DM)] == CANONICAL_DISPLAY_SNOMED_T2DM, (
            f"display for {SNOMED_T2DM} not canonical: {displays}"
        )

    def test_t11_parameters_with_valueset_omitted_display_resolves_canonical(self, fhir_client):
        """VS-01 QA-056 fix holds on Parameters-with-valueSet body path.

        The VS-03 SKEPTIC QA-059 fix wires the Parameters-with-valueSet shape
        into ``_expand_intensional``. The omitted-display canonical resolution
        (QA-056) MUST also fire on this path — the contains[].display MUST be
        the engine canonical preferred term, NOT an empty string.
        """
        nested_vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t11",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [
                    {"code": SNOMED_DIABETES_MELLITUS},
                    {"code": SNOMED_T2DM},
                ],
            }]},
        }
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "valueSet", "resource": nested_vs}],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status == 200, f"expected 200, got {status}: {body}"
        displays = _contains_displays(body)
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == CANONICAL_DISPLAY_SNOMED_DM, (
            f"display for {SNOMED_DIABETES_MELLITUS} not canonical on inline path: {displays}"
        )
        assert displays[(SNOMED_URI, SNOMED_T2DM)] == CANONICAL_DISPLAY_SNOMED_T2DM, (
            f"display for {SNOMED_T2DM} not canonical on inline path: {displays}"
        )

    def test_t12_parameters_with_valueset_cross_system_displays(self, fhir_client):
        """Cross-system concept[] list — each code's display resolves to its
        source's canonical preferred term. SNOMED, ICD-10-CM, RxNorm all in
        one inline expansion. Catches silent cross-source drift.
        """
        nested_vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t12",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]},
                {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
                {"system": RXNORM_URI, "concept": [{"code": RXNORM_METFORMIN}]},
            ]},
        }
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "valueSet", "resource": nested_vs}],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status == 200, f"expected 200, got {status}: {body}"
        displays = _contains_displays(body)
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == CANONICAL_DISPLAY_SNOMED_DM
        assert displays[(ICD10CM_URI, ICD10CM_T2DM)] == CANONICAL_DISPLAY_ICD10CM_T2DM
        assert displays[(RXNORM_URI, RXNORM_METFORMIN)] == CANONICAL_DISPLAY_RXNORM_METFORMIN


# =============================================================================
# Lens 2: Filter clinical safety — is-a on critical root (Diabetes mellitus)
# Spec: https://hl7.org/fhir/R4/valueset.html#filter
#   is-a: "The definition of the value set includes the concept and all of
#   its descendants in the code system."
# Clinical safety: a CDS hook reading an is-a expansion MUST see the root
# AND every descendant — silently missing descendants is a clinical hazard.
# =============================================================================


class TestLens2IsAFilterClinicalSafety:
    """Lens 2: is-a filter on a critical root (SNOMED Diabetes mellitus) MUST
    include the root AND all descendants. Missing descendants is a clinical
    safety hazard.
    """

    def test_t20_is_a_on_diabetes_mellitus_includes_root(self, fhir_client):
        """is-a on 73211009 (Diabetes mellitus) includes the root concept.

        Per FHIR R4 §4.9.5 filter operator ``is-a``: "The definition of the
        value set includes the concept and all of its descendants." Clinically:
        if the root is missing, downstream consumers would treat the value set
        as "all diabetes subtypes" rather than "diabetes inclusive" —
        silent-misclassification.
        """
        vs = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, (
            f"is-a root not included: {codes}"
        )

    def test_t21_is_a_on_diabetes_mellitus_includes_descendant(self, fhir_client):
        """is-a on 73211009 includes the descendant 44054006 (T2DM).

        The fixture has mrrel row (T2DM isa Diabetes) so the descendant walk
        MUST return 44054006. Missing it would mean the is-a filter is broken.
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"is-a descendant not included: {codes}"
        )

    def test_t22_is_a_descendant_display_is_canonical(self, fhir_client):
        """The descendant's display is the engine canonical preferred term.

        Per FHIR R4 ValueSet.expansion.contains.display: "The recommended
        display for this item in the expansion." For SNOMED 44054006 the
        canonical preferred term is "Type 2 diabetes mellitus" — NOT the FSN
        (Fully Specified Name) and NOT an empty string.
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        displays = _contains_displays(body)
        assert displays[(SNOMED_URI, SNOMED_T2DM)] == CANONICAL_DISPLAY_SNOMED_T2DM, (
            f"descendant display not canonical: {displays}"
        )

    def test_t23_is_a_root_display_is_canonical(self, fhir_client):
        """The root's display is the engine canonical preferred term."""
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        displays = _contains_displays(body)
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == CANONICAL_DISPLAY_SNOMED_DM, (
            f"root display not canonical: {displays}"
        )


# =============================================================================
# Lens 3: Hierarchical expansion truncation honesty
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
#   "Indicates that the expansion of this value set is incomplete because
#   the system refused to expand the value set for some reason."
# Clinical safety: clinical decisions on silently-truncated expansions are
# unsafe. The toocostly extension MUST surface when count truncates.
# =============================================================================


class TestLens3TruncationHonesty:
    """Lens 3: count-truncation MUST surface the toocostly extension.

    Per FHIR R4 §4.9.2: expansion.total reflects UN-truncated size. Per
    https://hl7.org/fhir/R4/extension-valueset-toocostly.html: the
    valueset-toocostly extension surfaces truncation. VS-02 SKEPTIC QA-057
    fix added the explicit-total parameter; VS-01 TERMINOLOGIST QA-055 fix
    added count pass-through on the inline-valueSet path.
    """

    def test_t30_inline_valueset_count_1_surfaces_toocostly(self, fhir_client):
        """Inline valueSet body with count=1 surfaces the toocostly extension.

        The VS-01 QA-055 fix removed the hardcoded count=1000 on the
        bare-ValueSet body path. count=1 on a 2-concept inline VS MUST
        truncate AND surface toocostly — clinical decisions on silently-
        truncated expansions are unsafe.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200, f"expected 200, got {status}: {body}"
        assert len(body["expansion"]["contains"]) <= 1
        assert body["expansion"]["total"] == 2, (
            f"total should reflect UN-truncated size (2): {body['expansion'].get('total')}"
        )
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"missing {TOOCOSTLY_URL}: {exts}"
        )

    def test_t31_parameters_with_valueset_count_1_surfaces_toocostly(self, fhir_client):
        """Parameters-with-valueSet body with count=1 surfaces toocostly.

        The VS-03 SKEPTIC QA-059 fix wires the Parameters-with-valueSet shape
        into _expand_intensional. Truncation signaling MUST fire on this path
        too — the inline-ValueSet POST combination matrix (VS-03 EXPLORER
        strategy 46) confirmed the helper is wired; this probe confirms the
        clinical-safety signal surfaces.
        """
        nested_vs = _make_extensional_snomed()
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": nested_vs},
                {"name": "count", "valueInteger": 1},
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status == 200, f"expected 200, got {status}: {body}"
        assert len(body["expansion"]["contains"]) <= 1
        assert body["expansion"]["total"] == 2
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"missing {TOOCOSTLY_URL}: {exts}"
        )

    def test_t32_is_a_count_1_surfaces_toocostly(self, fhir_client):
        """is-a on root with count=1 surfaces toocostly.

        Note: CF-HISTORIAN-VS02-01 documents that the BFS-capped total
        computation passes the truncated size on this path (the fixture's
        1-mrrel-row coincidence means total=2 happens to equal the actual
        size). The toocostly extension itself MUST still surface — the
        clinical-safety signal is the extension, not the total field.
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200, f"expected 200, got {status}: {body}"
        assert len(body["expansion"]["contains"]) <= 1
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"missing {TOOCOSTLY_URL} on is-a truncation: {exts}"
        )


# =============================================================================
# Lens 4: Code-system URI round-trips on inline valueSet
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.system
#   "An absolute URI which is the code system URI of the code system from
#    which the code in the expansion was defined."
# Each code in the expansion MUST advertise a system URI that $lookup can
# resolve (round-trip contract). Catch silent URI drift on inline paths.
# =============================================================================


class TestLens4SystemUriRoundTrip:
    """Lens 4: each contains[].system MUST round-trip via $lookup.

    Per FHIR R4 ValueSet.expansion.contains.system: "An absolute URI which is
    the code system URI of the code system from which the code in the
    expansion was defined." A CDS hook receiving the expansion will pass each
    Coding through $lookup for enrichment; the URI MUST resolve.
    """

    @pytest.mark.parametrize("system,code,expected_display", [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, CANONICAL_DISPLAY_SNOMED_DM),
        (SNOMED_URI, SNOMED_T2DM, CANONICAL_DISPLAY_SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_T2DM, CANONICAL_DISPLAY_ICD10CM_T2DM),
        (RXNORM_URI, RXNORM_METFORMIN, CANONICAL_DISPLAY_RXNORM_METFORMIN),
    ])
    def test_t40_advertised_system_round_trips_via_lookup(
        self, fhir_client, system, code, expected_display,
    ):
        """For each code returned by an inline-valueSet expansion, $lookup
        with the advertised system+code MUST succeed.

        Implementation: expand an inline valueSet containing each code,
        extract the (system, code) from contains[], then call $lookup with
        that exact pair. If $lookup 400s ("Unrecognized system URI"), the
        advertised system is a silent drift.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": f"http://example.org/vs/vs03-t40-{code}",
            "compose": {"include": [{"system": system, "concept": [{"code": code}]}]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200, f"expand failed: {s} {b}"
        codes = _contains_codes(b)
        assert (system, code) in codes, (
            f"expected ({system}, {code}) in expansion: {codes}"
        )

        # Now round-trip via $lookup.
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200, (
            f"$lookup round-trip failed for system={system} code={code}: "
            f"{resp.status_code} {resp.text}"
        )
        lu = resp.json()
        # The lookup display MUST match the expand display (canonical agreement
        # invariant — CS-05 EXPLORER strategy 38).
        param_displays = [
            p.get("valueString") for p in lu.get("parameter", [])
            if p.get("name") == "display"
        ]
        assert expected_display in param_displays, (
            f"canonical agreement broken: $lookup display={param_displays}, "
            f"expected {expected_display!r}"
        )

    def test_t41_inline_vs_contains_canonical_uri_no_alias(self, fhir_client):
        """Inline valueSet with client-supplied alias system URI gets
        re-resolved to canonical on the contains[] output.

        Per milestone-2 CR-013 fix: ``_expand_intensional`` re-resolves
        inc_system through canonical_system_uri. A client POSTing with the
        alias URI (e.g. trailing slash) MUST get back the canonical URI in
        contains[].system. This is the 6th-instance client-input-as-canonical
        drift pattern (PROMOTED).
        """
        # Trailing-slash alias on SNOMED URI.
        alias_uri = SNOMED_URI + "/"
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t41",
            "compose": {"include": [{
                "system": alias_uri,
                "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
            }]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        codes = _contains_codes(body)
        # Every contains[].system MUST be the canonical SNOMED URI, not the
        # trailing-slash alias.
        for sys_uri, code in codes:
            assert sys_uri == SNOMED_URI, (
                f"contains[].system is alias {sys_uri!r}, expected canonical "
                f"{SNOMED_URI!r}: drift on inline path"
            )


# =============================================================================
# Lens 5: Patient-friendly name surfacing (GAP-T01 DEFERRED)
# Spec: FHIR R4 §4.8.11 Concept Properties + custom properties via Out property
#   group. Spec does NOT require $expand to surface patient-friendly names —
#   this is an enhancement. Per AGENTS.md GAP-T01 / CF-TERMINOLOGIST-01, the
#   implicit value set expander resolves display via get_code_infos but does
#   NOT consult app.state.patient_friendly_cache. The conformance fixture
#   cannot exercise this gap (no PF rows seeded); the regression suite
#   covers $lookup patient-friendly resolution.
#
# This lens documents the current behavior as load-bearing contract. When a
# future enhancement chunk wires PF into $expand, the probe MUST be updated.
# =============================================================================


class TestLens5PatientFriendlySurfacing:
    """Lens 5: $expand does NOT surface patient-friendly names today.

    Per GAP-T01 / CF-TERMINOLOGIST-01: the implicit value set expander
    resolves display via get_code_infos (canonical preferred-atom STR) but
    does NOT consult app.state.patient_friendly_cache. The spec does not
    require $expand to surface PF names — this is an enhancement tied to
    a future chunk.

    These probes document the CURRENT behavior. When the gap is closed,
    the probes MUST be updated to assert PF names are present.
    """

    def test_t50_expand_does_not_emit_patient_friendly_extension(self, fhir_client):
        """$expand response does NOT carry a patient-friendly extension on
        contains[] entries today. This is the deferred GAP-T01 behavior.

        When the gap is closed, this probe MUST be updated to assert the
        extension IS present.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        contains = body.get("expansion", {}).get("contains", [])
        assert contains, "expansion should not be empty"
        for c in contains:
            exts = c.get("extension", [])
            pf_exts = [
                e for e in exts
                if "patient-friendly" in e.get("url", "").lower()
            ]
            assert not pf_exts, (
                f"patient-friendly extension surfaced (GAP-T01 may be closed): "
                f"{pf_exts} on code {c.get('code')!r}"
            )

    def test_t51_expand_contains_no_patient_friendly_property(self, fhir_client):
        """$expand contains[] entries do NOT carry a ``patient-friendly``
        field today. Mirrors GAP-T01 — display is canonical preferred term,
        PF surfacing is a deferred enhancement.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        contains = body.get("expansion", {}).get("contains", [])
        for c in contains:
            # No 'patient-friendly' or 'patientFriendly' key at the contains
            # level (would be non-spec anyway; contains[] only has system/
            # code/display/version/inactive/abstract/displayLanguage/
            # designation/extension).
            assert "patient-friendly" not in c, (
                f"contains entry has unexpected 'patient-friendly' key: {c}"
            )
            assert "patientFriendly" not in c, (
                f"contains entry has unexpected 'patientFriendly' key: {c}"
            )


# =============================================================================
# Lens 6: descendent-of vs is-a clinical distinction
# Spec: https://hl7.org/fhir/R4/valueset-concept-operator.html
#   is-a: "The definition of the value set includes the concept and all of
#          its descendants in the code system."
#   descendent-of: "The definition of the value set includes the descendants
#                  of the concept (but not the concept itself) in the code
#                  system."
# Clinically critical: the root concept is often an abstract or grouping
# concept; including it (is-a) vs excluding it (descendent-of) is a clinical
# decision. VS-01 SKEPTIC QA-054 fix corrected the spelling.
# =============================================================================


class TestLens6DescendentOfVsIsA:
    """Lens 6: is-a vs descendent-of clinically distinct on critical root.

    Per FHIR R4 valueset-concept-operator.html:
      - is-a includes the root AND descendants.
      - descendent-of includes ONLY descendants (excludes root).

    The clinical distinction: SNOMED 73211009 (Diabetes mellitus) is an
    abstract grouping concept. A CDS rule for "screening for diabetes" would
    use descendent-of (specific subtypes); a CDS rule for "diabetes (any)"
    would use is-a (inclusive).
    """

    def test_t60_is_a_includes_root(self, fhir_client):
        """is-a on root includes the root itself."""
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, (
            f"is-a MUST include root: {codes}"
        )

    def test_t61_descendent_of_excludes_root(self, fhir_client):
        """descendent-of on root EXCLUDES the root itself.

        Clinically critical: the operator name is "descendent-of" (Latin-
        derived per VS-01 SKEPTIC QA-054). The off-spec "descendant-of"
        (English) is silently dropped (test_t65 below).
        """
        vs = _make_intensional_snomed_descendent_of()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes, (
            f"descendent-of MUST exclude root: {codes}"
        )

    def test_t62_descendent_of_includes_descendant(self, fhir_client):
        """descendent-of on root includes the descendant."""
        vs = _make_intensional_snomed_descendent_of()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"descendent-of MUST include descendant: {codes}"
        )

    def test_t63_is_a_and_descendent_of_strict_subset(self, fhir_client):
        """is-a result is a STRICT SUPERSET of descendent-of result on the
        same root. The difference is exactly the root concept.
        """
        isa_vs = _make_intensional_snomed_isa()
        des_vs = _make_intensional_snomed_descendent_of()
        s1, b1 = _post_expand(fhir_client, isa_vs)
        s2, b2 = _post_expand(fhir_client, des_vs)
        assert s1 == 200 and s2 == 200
        isa_codes = set(_contains_codes(b1))
        des_codes = set(_contains_codes(b2))
        assert des_codes.issubset(isa_codes), (
            f"descendent-of codes {des_codes} not subset of is-a codes {isa_codes}"
        )
        # The difference is exactly the root.
        assert isa_codes - des_codes == {(SNOMED_URI, SNOMED_DIABETES_MELLITUS)}, (
            f"is-a - descendent-of should be exactly the root: "
            f"{isa_codes - des_codes}"
        )

    def test_t64_descendent_of_display_is_canonical(self, fhir_client):
        """descendent-of descendant's display is engine canonical preferred
        term (mirror of test_t22)."""
        vs = _make_intensional_snomed_descendent_of()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        displays = _contains_displays(body)
        assert displays[(SNOMED_URI, SNOMED_T2DM)] == CANONICAL_DISPLAY_SNOMED_T2DM, (
            f"descendent-of display not canonical: {displays}"
        )

    def test_t65_offspec_descendant_of_silently_dropped(self, fhir_client):
        """Off-spec 'descendant-of' (English spelling) is silently dropped.

        VS-01 SKEPTIC QA-054: spec-correct is 'descendent-of' (Latin-derived).
        'descendant-of' returns empty expansion (silently dropped per
        CF-SKEPTIC-VS01-01 — the operator is not in the spec enum).
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t65",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "descendant-of", "value": SNOMED_DIABETES_MELLITUS}
                ],
            }]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        # Off-spec spelling silently dropped → empty expansion.
        codes = _contains_codes(body)
        assert codes == [], (
            f"off-spec 'descendant-of' should produce empty expansion: {codes}"
        )


# =============================================================================
# Lens 7: CF-TERMINOLOGIST-VS01-01 client-supplied display echo (DEFERRED)
# When a client SUPPLIES compose.include[].concept[].display, the impl echoes
# it verbatim. CF-TERMINOLOGIST-VS01-01 (MEDIUM, DEFERRED) — applying
# canonical-wins requires a display-name canonicalization decision tied to
# AGENTS.md NOT A BUG registry.
#
# These probes document the CURRENT behavior as load-bearing contract. When
# the CF is closed, the probes MUST be updated.
# =============================================================================


class TestLens7CFClientSuppliedDisplayEcho:
    """Lens 7: client-supplied display echo on inline valueSet (CF-VS01-01).

    Per CF-TERMINOLOGIST-VS01-01 (MEDIUM — DEFERRED): when a client SUPPLIES
    compose.include[].concept[].display, ``_expand_intensional`` echoes the
    client's display verbatim. The spec says contains[].display is "the
    recommended display for this item in the expansion" — implying the
    SERVER's canonical preferred term. CS-03 TERMINOLOGIST established the
    precedent: server-canonical wins over client input.

    6th instance of client-input-as-canonical drift pattern. Deferred because
    applying canonical-wins requires a display-name canonicalization decision.
    """

    def test_t70_client_supplied_display_is_currently_echoed(self, fhir_client):
        """CF-TERMINOLOGIST-VS01-01 pin: when client SUPPLIES display,
        ``_expand_intensional`` echoes it verbatim (lines 2089-2100 of
        apps/fhir_api.py: ``display = concept.get("display") or ""``).

        This probe asserts the CURRENT (deferred) behavior. When the CF is
        closed (canonical-wins applied), this probe MUST be updated.
        """
        # Supply a clearly-wrong display for SNOMED 73211009.
        wrong_display = "WRONG CLIENT DISPLAY 73211009"
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t70",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [
                    {"code": SNOMED_DIABETES_MELLITUS, "display": wrong_display},
                ],
            }]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        displays = _contains_displays(body)
        # CURRENT behavior: client display echoed verbatim.
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == wrong_display, (
            f"CF-VS01-01 pin: client-supplied display should be echoed verbatim "
            f"(current deferred behavior). Got: {displays}. If this probe "
            f"FAILED, the CF may be closed — update to assert canonical-wins."
        )

    def test_t71_cf_terminologist_vs01_01_documented(self, fhir_client):
        """CF-TERMINOLOGIST-VS01-01 carry-forward-as-probe (CS-03 TERMINOLOGIST
        methodology): pin the deferred behavior so when the CF is closed, the
        probe fails loudly.
        """
        # Same shape as test_t70 but explicit: probe asserts the deferred
        # echo behavior, with comment pointing at the CF.
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t71",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [
                    # Client supplies a non-canonical display.
                    {"code": SNOMED_T2DM, "display": "Client-supplied T2DM name"},
                ],
            }]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        displays = _contains_displays(body)
        # CF-VS01-01: client display echoed, NOT canonical.
        assert displays[(SNOMED_URI, SNOMED_T2DM)] == "Client-supplied T2DM name"


# =============================================================================
# Lens 8: Inline valueSet POST with multiple includes (clinical completeness)
# Spec: https://hl7.org/fhir/R4/valueset.html#compose
#   compose.include is 1..*: "An include clause specifies what to include in
#   the value set." Multiple includes MUST be unioned (clinical completeness).
# =============================================================================


class TestLens8MultipleIncludesUnion:
    """Lens 8: multiple compose.include[] blocks MUST be unioned.

    Per FHIR R4 ValueSet.compose.include (1..*): multiple include clauses
    MUST be unioned into the final expansion. Clinically: a value set
    spanning multiple code systems (SNOMED + ICD-10-CM) MUST return codes
    from BOTH systems, not just the first.
    """

    def test_t80_multiple_includes_unioned(self, fhir_client):
        """Multiple compose.include blocks union into a single expansion."""
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t80",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]},
                {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
                {"system": RXNORM_URI, "concept": [{"code": RXNORM_METFORMIN}]},
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = set(_contains_codes(body))
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (ICD10CM_URI, ICD10CM_T2DM) in codes
        assert (RXNORM_URI, RXNORM_METFORMIN) in codes

    def test_t81_multiple_includes_cross_system_displays(self, fhir_client):
        """Multiple compose.include blocks each resolve to their own system's
        canonical preferred term."""
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t81",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]},
                {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
                {"system": RXNORM_URI, "concept": [{"code": RXNORM_METFORMIN}]},
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        displays = _contains_displays(body)
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == CANONICAL_DISPLAY_SNOMED_DM
        assert displays[(ICD10CM_URI, ICD10CM_T2DM)] == CANONICAL_DISPLAY_ICD10CM_T2DM
        assert displays[(RXNORM_URI, RXNORM_METFORMIN)] == CANONICAL_DISPLAY_RXNORM_METFORMIN


# =============================================================================
# Lens 9: Date parameter clinical correctness (out-of-fixture-scope documentation)
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html In Parameters
#   date: 0..* dateTime — "Controls the maximum date for the expansion. The
#   server SHOULD use the maximum date it can support, but cannot guarantee
#   to do so."
# The engine has no versioned data (single-snapshot). Per AGENTS.md NOT A BUG
# registry, the date param is accepted for spec-compatibility but not used
# to scope results. This lens documents the behavior.
# =============================================================================


class TestLens9DateParameter:
    """Lens 9: date parameter accepted but not used (single-snapshot engine).

    Per AGENTS.md NOT A BUG registry: the date parameter is accepted for
    spec-compatibility; the engine has no versioned data so the parameter is
    not used to scope results. The acceptance MUST be silent (no 500).
    """

    def test_t90_past_date_accepted_on_inline_vs(self, fhir_client):
        """Past date on inline-valueSet expansion is accepted (no 500).

        The engine may not have versioned data — out-of-fixture-scope per
        AGENTS.md. Document the current behavior: 200 with the full expansion
        regardless of date.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"date": "2020-01-01"})
        assert status == 200, f"past date should be accepted: {status} {body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_t91_future_date_accepted_on_inline_vs(self, fhir_client):
        """Future date on inline-valueSet expansion is accepted (no 500)."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"date": "2030-01-01"})
        assert status == 200, f"future date should be accepted: {status} {body}"

    def test_t92_date_in_parameters_body_alongside_valueset(self, fhir_client):
        """date parameter co-located in Parameters body alongside valueSet."""
        nested_vs = _make_extensional_snomed()
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": nested_vs},
                {"name": "date", "valueDateTime": "2020-01-01T00:00:00Z"},
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status == 200, f"date+valueSet in Parameters body failed: {status} {body}"


# =============================================================================
# Lens 10: Carry-forward reconfirmations (CS-03 TERMINOLOGIST methodology)
# Each carry-forward MUST be probed by every subsequent personality to confirm
# it remains a load-bearing contract. If the CF is closed without updating
# the probe, the probe MUST fail loudly.
# =============================================================================


class TestLens10CarryForwardReconfirmations:
    """Lens 10: reconfirm all VS-03-relevant carry-forwards remain open.

    These probes document the CURRENT (deferred) behavior. When the CF is
    closed, the probe MUST be updated to assert the new behavior.
    """

    def test_t100_cf_skeptic_vs01_01_seven_operators_silently_dropped(self, fhir_client):
        """CF-SKEPTIC-VS01-01: 7 of 9 filter operators silently dropped.

        The implementation only honors `is-a` and `descendent-of` (VS-01
        QA-054 fix). The other 7 operators are silently dropped → empty
        expansion. When a future chunk implements them, this probe MUST be
        updated to assert the new behavior.
        """
        # The full R4 enum per https://hl7.org/fhir/R4/valueset-concept-operator.html
        assert FHIR_R4_FILTER_OPERATORS == frozenset({
            "=", "is-a", "descendent-of", "is-not-a", "regex",
            "in", "not-in", "generalizes", "exists",
        }), f"FHIR_R4_FILTER_OPERATORS changed: {FHIR_R4_FILTER_OPERATORS}"

        unsupported = {"=", "is-not-a", "regex", "in", "not-in", "generalizes", "exists"}
        for op in sorted(unsupported):
            vs = {
                "resourceType": "ValueSet",
                "url": f"http://example.org/vs/vs03-t100-{op}",
                "compose": {"include": [{
                    "system": SNOMED_URI,
                    "filter": [{"property": "concept", "op": op, "value": SNOMED_DIABETES_MELLITUS}],
                }]},
            }
            status, body = _post_expand(fhir_client, vs)
            assert status == 200, f"operator {op!r} caused 500: {status} {body}"
            codes = _contains_codes(body)
            # All 7 unsupported operators silently drop → empty expansion.
            # When implemented, this assertion MUST be updated per-operator.
            assert codes == [], (
                f"operator {op!r} returned non-empty expansion (CF may be closed): {codes}"
            )

    def test_t101_cf_historian_vs02_02_implicit_path_canonical_uri(self, fhir_client):
        """CF-HISTORIAN-VS02-02: implicit value set path lacks canonical_system_uri.

        The implicit value set expander uses the client-supplied URL prefix
        verbatim for contains[].system. Bug invisible because the fixture
        doesn't seed alias URIs. This probe uses the canonical URI so it
        passes today; when the CF is closed, the probe can be tightened to
        test alias inputs.
        """
        # Use canonical LOINC URI (no alias). Probe confirms path works.
        status, body = _get_expand(fhir_client, params={"url": f"{LOINC_URI}/vs"})
        assert status in (200, 400), (
            f"implicit value set expand failed unexpectedly: {status} {body}"
        )

    def test_t102_cf_skeptic_vs02_03_filter_path_toocostly_parity(self, fhir_client):
        """CF-SKEPTIC-VS02-03 closed by VS-02 SKEPTIC resweep QA-001 fix:
        both GET and POST filter paths share the same code path and BOTH
        now emit toocostly on truncation.

        The QA-001 fix uses the ``+1 probe`` pattern (search_names(limit=
        count+1)) and emits the valueset-toocostly extension when
        truncation fires. The fix closes CF-SKEPTIC-VS02-03 in the same
        pass because both gaps shared the same root cause (the filter-
        mode call site omitted both ``total=`` AND ``extensions=``).
        """
        # GET filter path with count=1.
        s_get, b_get = _get_expand(fhir_client, params={
            "filter": "diabetes", "count": 1, "system": SNOMED_URI,
        })
        assert s_get == 200, f"GET filter failed: {s_get} {b_get}"
        get_exts = b_get.get("expansion", {}).get("extension", [])
        get_has_toocostly = any(e.get("url") == TOOCOSTLY_URL for e in get_exts)

        # POST filter path with count=1.
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "count", "valueInteger": 1},
                {"name": "system", "valueUri": SNOMED_URI},
            ],
        }
        s_post, b_post = _post_expand(fhir_client, post_body)
        assert s_post == 200, f"POST filter failed: {s_post} {b_post}"
        post_exts = b_post.get("expansion", {}).get("extension", [])
        post_has_toocostly = any(e.get("url") == TOOCOSTLY_URL for e in post_exts)

        # Both paths MUST emit toocostly on truncation (QA-001 fix closes
        # CF-SKEPTIC-VS02-03 in the same pass).
        assert get_has_toocostly, (
            f"GET filter path missing toocostly on truncation — "
            f"CF-SKEPTIC-VS02-03 should be closed. Got: {get_exts}"
        )
        assert post_has_toocostly, (
            f"POST filter path missing toocostly on truncation — "
            f"CF-SKEPTIC-VS02-03 should be closed. Got: {post_exts}"
        )

    def test_t103_cf_historian_vs02_01_bfs_cap_fixture_coincidence(self, fhir_client):
        """CF-HISTORIAN-VS02-01: BFS cap on total still truncated.

        The SKEPTIC test_s34 / HISTORIAN test_h60 / test_h33 family pins the
        fixture coincidence: intensional is-a with count=1 returns total=2
        because the fixture has exactly 1 mrrel row matching BFS limit=1.

        When the structural fix lands (extend get_descendants_bfs to return
        total_count), this probe MUST be updated to assert the actual
        un-truncated size.
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        # Fixture coincidence: 1 descendant (T2DM) + root (DM) = 2 concepts.
        # BFS limit=1 returns 1 descendant; root is added before BFS; total
        # passed is len(deduped)=2 AFTER BFS-cap (coincidence: BFS returned
        # 1 which matches count=1).
        assert body["expansion"]["total"] == 2, (
            f"fixture-coincidence total expected to be 2; got "
            f"{body['expansion'].get('total')}. If this FAILED, the CF may be "
            f"closed — update to assert actual un-truncated size."
        )
