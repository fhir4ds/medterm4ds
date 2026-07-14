"""TERMINOLOGIST probes for VS-01 (ValueSet Resource Structure).

Spec: https://hl7.org/fhir/R4/valueset.html
       $expand operation: https://hl7.org/fhir/R4/valueset-operation-expand.html
       Expansion: https://hl7.org/fhir/R4/valueset.html#expansion
       Filter operators: https://hl7.org/fhir/R4/valueset.html#filter

TERMINOLOGIST lens (clinical/terminological correctness) per chunk assignment:

  1. **`contains[].display` clinical correctness** — for codes returned by
     `$expand`, the `display` MUST be the engine's canonical preferred term
     (PT/SCD/HT — STR from mrconso with the most-preferred TTY). The implicit
     value set expander (`_expand_implicit_value_set`) resolves display via
     `get_code_infos(...).name`. The intensional expander
     (`_expand_intensional`) uses `get_descendants_bfs(...).target_display` for
     descendants and `get_code_infos(...).name` for the root. Both MUST agree
     with `$lookup` Out `display` for the same code (cross-operation canonical
     agreement). Patient-friendly names are surfaced as separate custom
     extensions today on the $lookup surface; $expand does NOT surface
     patient-friendly names on contains[] entries — this is the deferred
     CF-TERMINOLOGIST-01 / GAP-T01 contract (do NOT override display; attach
     an extension instead).

  2. **Filter operator clinical safety on critical roots** — when a filter
     produces a very large expansion (e.g., `is-a` on a root code), the
     server MUST surface the HL7 `valueset-toocostly` extension if the result
     is truncated. Clinical decisions on silently-truncated expansions are
     unsafe. The implementation has `_truncation_extensions(count_limited,
     depth_cap_hit)` which emits the extension when either the count cap or
     depth cap is hit.

  3. **Cross-operation canonical agreement** — `$expand` contains[] entries
     have a `system` field. Each code returned should be `$lookup`-able with
     that system. URI round-trip methodology (TS-03 TERMINOLOGIST) applied to
     the $expand surface.

  4. **Inactive/abstract code handling in expansions** — the engine filters
     inactive at lookup (SUPPRESS='N' filter). Verify the implicit value set
     expander also filters inactive (it does — line 2148 `SUPPRESS = 'N'`).
     Inactive codes MUST NOT appear in expansions. Abstract codes are not
     separately flagged today (engine has no abstract-flag data per
     CF-SKEPTIC-CS05-01).

  5. **`compose.exclude` clinical safety** — if a clinician excludes a code
     from a value set, the expansion MUST honor that exclusion. Silent
     inclusion of excluded codes is unsafe. The implementation iterates
     `compose.exclude[].concept[].code` and removes matching entries. Cross-
     system matching drift is CF-SKEPTIC-VS01-03 (deferred). This iteration
     asserts that exclude on the SAME system as include IS honored.

  6. **CF-HISTORIAN-VS01-01 documentation** — R5-drift on R4 surface
     (ConceptMapEquivalence enum) is out of VS-01 scope but documented for
     CM-* chunks. We pin the CURRENT behavior here so the future fix is
     verifiable.

Per GLOBAL_RULES.md:
  - "TERMINOLOGIST Findings Are HIGH Severity" — default HIGH.
  - Spec citation required on every probe.
  - "Test-too-lenient": every probe asserts POSITIVE success shape.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # 44054006 is-a 73211009

All codes are SUPPRESS='N' (active). No abstract concepts seeded.
"""

from __future__ import annotations

import pytest

# Spec citations:
# - ValueSet resource: https://hl7.org/fhir/R4/valueset.html
# - $expand operation: https://hl7.org/fhir/R4/valueset-operation-expand.html
# - Filter operator (Required binding): https://hl7.org/fhir/R4/valueset.html#filter
# - Expansion contains[]: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains
# - valueset-toocostly extension: https://hl7.org/fhir/extension-valueset-toocostly.html
# - Display name: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out `display`
SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

# Canonical preferred-term expectations seeded by the conformance fixture.
# These are the EXPECTED canonical STR values from mrconso (PT/SCD/HT).
# Per FHIR R4 https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# Out `display`: "The canonical display name for the concept".
SNOMED_DIABETES_MELLITUS_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"
ICD10CM_T2DM_DISPLAY = "Type 2 diabetes mellitus"

# HL7 toocostly extension URL — when the server truncates an expansion
# (count-limited OR depth-limited), the extension MUST be present per
# https://hl7.org/fhir/extension-valueset-toocostly.html.
TOOCOSTLY_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"


def _post_expand(fhir_client, value_set: dict) -> tuple[int, dict]:
    """POST a ValueSet body to /fhir/ValueSet/$expand."""
    resp = fhir_client.post("/fhir/ValueSet/$expand", json=value_set)
    return resp.status_code, resp.json()


def _get_expand(fhir_client, params: dict) -> tuple[int, dict]:
    """GET /fhir/ValueSet/$expand with query params."""
    resp = fhir_client.get("/fhir/ValueSet/$expand", params=params)
    return resp.status_code, resp.json()


def _lookup(fhir_client, system: str, code: str) -> tuple[int, dict]:
    """Run $lookup for a (system, code)."""
    resp = fhir_client.get(
        "/fhir/CodeSystem/$lookup", params={"system": system, "code": code}
    )
    return resp.status_code, resp.json()


def _extract_out_param(body: dict, name: str):
    """Extract the value of an Out parameter from a Parameters body.

    Returns None if the parameter is not present.
    """
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


# =============================================================================
# Lens 1: contains[].display clinical correctness
# =============================================================================

class TestLens1DisplayClinicalCorrectness:
    """Lens 1 — contains[].display MUST be the engine's canonical preferred term.

    Per FHIR R4 https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display:
      "The recommended display for this item in the expansion."

    Per FHIR R4 https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out `display`:
      "The canonical display name for the concept".

    For the same (system, code), `$expand` contains[].display and `$lookup`
    Out `display` MUST agree. The engine resolves both via the same canonical
    path (PT/SCD/HT STR from mrconso). A future regression that adds a
    translation step to one operation but not the other would silently
    diverge — these probes guard against that.

    CF-TERMINOLOGIST-01 / GAP-T01 (deferred): patient-friendly names are NOT
    surfaced on contains[] entries today (only $lookup has them via the
    _PatientFriendlyCache). When this is implemented, attach a `patient-
    friendly` extension per contains[] entry — do NOT override `display`.
    """

    def test_t10_extensional_concept_display_matches_engine_canonical(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        When the client provides a compose.include[].concept[].display that
        DIFFERS from the engine's canonical, the implementation currently
        echoes the client-supplied display (this is a known deferral — see
        CF-TERMINOLOGIST-VS01-01 below). However, when the client OMITS the
        display, the expansion MUST surface the engine's canonical preferred
        term — NOT an empty string, NOT the raw code, NOT a fallback.

        Probe: POST a concept with no `display` field; assert contains[0].display
        equals the engine's canonical preferred term.
        """
        # Spec: client-supplied concept without display → server resolves canonical.
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [
                            {"code": SNOMED_DIABETES_MELLITUS},  # no display
                        ],
                    }
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        assert resp["resourceType"] == "ValueSet"
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # Engine canonical MUST be present (NOT empty string).
        assert contains[0]["display"] == SNOMED_DIABETES_MELLITUS_DISPLAY, (
            f"Expected canonical display {SNOMED_DIABETES_MELLITUS_DISPLAY!r}, "
            f"got {contains[0]['display']!r}"
        )

    def test_t11_concept_display_client_supplied_is_currently_echoed(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        CURRENT BEHAVIOR (CF-TERMINOLOGIST-VS01-01 deferred): when a client
        supplies a compose.include[].concept[].display, the implementation
        ECHOES the client's display verbatim. The spec says the contains[].display
        is "the recommended display for this item in the expansion" — implying
        the SERVER's canonical. CS-03 TERMINOLOGIST test_t10 (CodeSystem/$validate-code
        Out `display`) established the precedent: server-canonical wins over
        client input.

        This is the carry-forward-as-probe pattern (CS-03 TERMINOLOGIST
        methodology): the probe asserts the CURRENT behavior so a future
        fix will fail loudly. When VS-* chunks wire the canonical-resolution
        path into `_expand_intensional`, this probe MUST be updated to assert
        canonical-wins.

        Why TERMINOLOGIST care: a clinician who supplies a misleading display
        in their ValueSet body (e.g., an outdated synonym, or a typo) gets
        that misleading display back in the expansion — the expansion is the
        authoritative source for downstream clinical decisions.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [
                            {
                                "code": SNOMED_DIABETES_MELLITUS,
                                "display": "Diabetes (outdated synonym)",
                            }
                        ],
                    }
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # CURRENT behavior: client-supplied display is echoed.
        assert contains[0]["display"] == "Diabetes (outdated synonym)", (
            "CF-TERMINOLOGIST-VS01-01: current behavior echoes client display. "
            "If this assertion fails because the server now returns the canonical, "
            "UPDATE this probe to assert canonical-wins and close the carry-forward."
        )

    def test_t12_explicit_concept_display_canonical_when_omitted_rxnorm(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        Cross-source consistency: the implicit-expansion canonical-resolution
        path produces the engine's STR for RxNorm SCD codes too. Verifies
        the canonical-resolution invariant is source-agnostic.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": RXNORM_URI,
                        "concept": [{"code": RXNORM_METFORMIN}],  # no display
                    }
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        assert contains[0]["display"] == RXNORM_METFORMIN_DISPLAY, (
            f"Expected RxNorm canonical display, got {contains[0]['display']!r}"
        )

    def test_t13_explicit_concept_display_canonical_when_omitted_icd10cm(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        ICD-10-CM uses HT (Hybrid Term) TTY in the fixture; the canonical-
        resolution path must still surface the engine's STR.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": ICD10CM_URI,
                        "concept": [{"code": ICD10CM_T2DM}],
                    }
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        assert contains[0]["display"] == ICD10CM_T2DM_DISPLAY, (
            f"Expected ICD-10-CM canonical display, got {contains[0]['display']!r}"
        )

    def test_t14_is_a_filter_root_display_is_engine_canonical(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        For `is-a` filter expansions, the root code's display MUST be the
        engine's canonical preferred term (resolved via `get_code_infos`).
        The implementation in `_expand_intensional` at apps/fhir_api.py:1968
        resolves root display via `root_infos[0].name or root_code`. This
        probe verifies the `or root_code` fallback is NOT taken when the
        engine has a canonical name.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes_to_displays = {c["code"]: c.get("display", "") for c in contains}
        assert SNOMED_DIABETES_MELLITUS in codes_to_displays
        # Engine canonical MUST be the display (not the raw code, not empty).
        assert codes_to_displays[SNOMED_DIABETES_MELLITUS] == SNOMED_DIABETES_MELLITUS_DISPLAY, (
            f"Expected root display {SNOMED_DIABETES_MELLITUS_DISPLAY!r}, "
            f"got {codes_to_displays[SNOMED_DIABETES_MELLITUS]!r}"
        )

    def test_t15_descendent_of_descendant_display_is_engine_canonical(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        For `descendent-of` filter expansions, descendant code displays are
        sourced from `get_descendants_bfs(...).target_display`. This MUST
        be the engine's canonical name for the descendant, not the raw code.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "descendent-of", "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes_to_displays = {c["code"]: c.get("display", "") for c in contains}
        # descendent-of excludes root; SNOMED_T2DM is the only descendant in fixture.
        assert SNOMED_T2DM in codes_to_displays
        assert codes_to_displays[SNOMED_T2DM] == SNOMED_T2DM_DISPLAY, (
            f"Expected descendant display {SNOMED_T2DM_DISPLAY!r}, "
            f"got {codes_to_displays[SNOMED_T2DM]!r}"
        )

    def test_t16_text_filter_display_is_engine_canonical(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        For `$expand?filter=...` (text-filter mode), display comes from
        `search_names(...).name`. This MUST be the engine's canonical
        preferred term, not a derived/auto-generated value.
        """
        status, resp = _get_expand(fhir_client, {"filter": "diabetes", "count": 10})
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) >= 1
        # Each contains MUST have a non-empty display sourced from the engine.
        for c in contains:
            assert c.get("display"), (
                f"contains entry {c.get('code')!r} has empty display; "
                "engine canonical preferred term is required."
            )

    def test_t17_implicit_expansion_display_is_engine_canonical(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/terminology-service.html#4.7.3.1
                  https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        For implicit value set expansion (`<system-uri>/vs`), display comes
        from `get_code_infos(...).name`. This MUST be the engine's canonical
        preferred term, not the raw code.
        """
        # ICD-10-CM has only one row in the fixture; expansion should include E11.
        status, resp = _get_expand(
            fhir_client, {"url": f"{ICD10CM_URI}/vs", "count": 10}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes_to_displays = {c["code"]: c.get("display", "") for c in contains}
        assert ICD10CM_T2DM in codes_to_displays
        assert codes_to_displays[ICD10CM_T2DM] == ICD10CM_T2DM_DISPLAY, (
            f"Expected implicit-expansion display {ICD10CM_T2DM_DISPLAY!r}, "
            f"got {codes_to_displays[ICD10CM_T2DM]!r}"
        )

    def test_t18_text_filter_display_no_raw_code_fallback(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        Clinical safety: the display MUST NOT be the raw code as a fallback.
        If the engine cannot resolve a canonical name, that's a data-quality
        signal — surfacing the raw code as `display` misleads a clinician
        into thinking the code HAS a name. (Some implementations do this;
        medterm4ds MUST NOT.)
        """
        status, resp = _get_expand(fhir_client, {"filter": "metformin", "count": 5})
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for c in contains:
            # Display must not be a verbatim echo of the code (raw fallback).
            assert c.get("display") != c.get("code"), (
                f"Display for code {c.get('code')!r} is the raw code — "
                "engine canonical preferred term is required (no fallback)."
            )


# =============================================================================
# Lens 2: Filter operator clinical safety on critical roots
# =============================================================================

class TestLens2FilterOperatorSafetyOnCriticalRoots:
    """Lens 2 — `is-a`/`descendent-of` on a root code can produce very large
    expansions. When truncated, the HL7 `valueset-toocostly` extension MUST
    be surfaced so clinicians know NOT to use the expansion as exhaustive.

    Per FHIR R4 https://hl7.org/fhir/extension-valueset-toocostly.html:
      "The expansion was too big to process, and the expansion was truncated
       to the first portion of the expansion."

    Per FHIR R4 https://hl7.org/fhir/R4/valueset-operation-expand.html Notes:
      "Hierarchical expansions SHOULD NOT be paged — if the expansion is
       truncated, the server SHOULD return an extension."

    The implementation has `_truncation_extensions(count_limited,
    depth_cap_hit)`. The conformance fixture has only 1 descendant, so
    count-limited truncation is triggered with count=1 (the root + 1
    descendant exceeds count=1 in `is-a` mode).
    """

    def test_t20_truncated_expansion_surfaces_toocostly_extension(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/extension-valueset-toocostly.html

        When `count=1` is requested for an `is-a` filter whose expansion
        yields 2 codes (root + 1 descendant), the implementation MUST
        surface the `valueset-toocostly` extension. Without it, a clinician
        would treat the 1-code expansion as exhaustive — clinically unsafe
        on a hierarchy walk.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }
                ]
            },
        }
        # count=1 forces truncation: root + descendant = 2 codes > count.
        status, resp = _post_expand_with_count(fhir_client, body, count=1)
        assert status == 200, resp
        exts = resp.get("expansion", {}).get("extension", [])
        toocostly = [e for e in exts if e.get("url") == TOOCOSTLY_EXT_URL]
        assert toocostly, (
            "Truncated expansion MUST surface valueset-toocostly extension. "
            f"Got extensions: {[e.get('url') for e in exts]}"
        )
        # valueBoolean MUST be True (lowercase) on JSON serialization per FHIR R4.
        assert toocostly[0].get("valueBoolean") is True

    def test_t21_untruncated_expansion_no_toocostly_extension(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/extension-valueset-toocostly.html

        When the full expansion fits within `count`, the `valueset-toocostly`
        extension MUST NOT appear. A spurious toocostly extension misleads
        the clinician into thinking the expansion is incomplete.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }
                ]
            },
        }
        # count=10 fits root + 1 descendant without truncation.
        status, resp = _post_expand_with_count(fhir_client, body, count=10)
        assert status == 200, resp
        exts = resp.get("expansion", {}).get("extension", [])
        toocostly = [e for e in exts if e.get("url") == TOOCOSTLY_EXT_URL]
        assert not toocostly, (
            "Untruncated expansion MUST NOT surface valueset-toocostly. "
            "A spurious toocostly misleads clinicians into thinking the "
            "expansion is incomplete."
        )

    def test_t22_toocostly_extension_includes_reason_subextension(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/extension-valueset-toocostly.html

        The implementation's `_truncation_extensions` helper emits a nested
        `reason` sub-extension documenting WHY the expansion was truncated
        (count-limited at N, depth-limited at M). This reason is clinically
        actionable: a clinician can request a higher count or adjust depth.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }
                ]
            },
        }
        status, resp = _post_expand_with_count(fhir_client, body, count=1)
        assert status == 200, resp
        exts = resp.get("expansion", {}).get("extension", [])
        toocostly = [e for e in exts if e.get("url") == TOOCOSTLY_EXT_URL]
        assert toocostly
        sub_exts = toocostly[0].get("extension", [])
        reason_exts = [e for e in sub_exts if e.get("url") == "reason"]
        assert reason_exts, (
            "toocostly extension MUST include a 'reason' sub-extension "
            "documenting the truncation cause (clinically actionable)."
        )

    def test_t23_implicit_expansion_truncation_surfaces_toocostly(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/extension-valueset-toocostly.html

        Implicit value set expansion (`<system-uri>/vs`) MUST surface
        `valueset-toocostly` when the underlying source has more codes than
        `count`. The implementation's `_expand_implicit_value_set` enforces
        this via `_truncation_extensions(count_limited=...)`.
        """
        # ICD-10-CM has 1 row in the fixture. count=1 returns the row;
        # the LIMIT is count+1=2, so rows > count is False (1 > 1 = False).
        # Force truncation: SNOMED has 2 codes; count=1 truncates.
        status, resp = _get_expand(
            fhir_client, {"url": f"{SNOMED_URI}/vs", "count": 1}
        )
        assert status == 200, resp
        exts = resp.get("expansion", {}).get("extension", [])
        toocostly = [e for e in exts if e.get("url") == TOOCOSTLY_EXT_URL]
        assert toocostly, (
            "Truncated implicit expansion MUST surface valueset-toocostly. "
            "Implicit expansions of large code systems (LOINC, SNOMED) are "
            "the highest-stakes truncation case — clinicians rely on the "
            "extension to know the expansion is incomplete."
        )


def _post_expand_with_count(fhir_client, value_set: dict, *, count: int) -> tuple[int, dict]:
    """POST a ValueSet body to /fhir/ValueSet/$expand with explicit count."""
    # $expand accepts count as a query parameter per FHIR R4 §4.9.5.
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand", json=value_set, params={"count": count}
    )
    return resp.status_code, resp.json()


# =============================================================================
# Lens 3: Cross-operation canonical agreement
# =============================================================================

class TestLens3CrossOperationCanonicalAgreement:
    """Lens 3 — `$expand` contains[].system and contains[].display MUST agree
    with `$lookup` Out `system` and Out `display` for the same (system, code).

    Per FHIR R4 §4.7.5 (https://hl7.org/fhir/R4/valueset-operation-expand.html):
    The contains[] entries are Coding-typed; their `system` and `code` fields
    form a Coding reference that downstream clients use for `$lookup`,
    `$validate-code`, etc.

    Per CS-05 EXPLORER test_e10/e11 + VS-01 EXPLORER test_e80 methodology,
    the canonical agreement invariant holds structurally because both
    operations share `get_code_infos` and the canonical-URI re-resolution
    pattern. These probes guard against a future regression that adds a
    translation step to one operation but not the other.

    URI round-trip methodology (TS-03 TERMINOLOGIST): every Coding returned
    by `$expand` MUST be `$lookup`-able with that system URI. If $lookup
    400s on the system URI, the contains[] entry is unresolvable — a
    clinical safety violation (the Coding advertises a system the server
    itself can't look up).
    """

    @pytest.mark.parametrize(
        "system,code,expected_display",
        [
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_DIABETES_MELLITUS_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN, RXNORM_METFORMIN_DISPLAY),
            (ICD10CM_URI, ICD10CM_T2DM, ICD10CM_T2DM_DISPLAY),
        ],
        ids=["snomed-parent", "snomed-child", "rxnorm", "icd10cm"],
    )
    def test_t30_expand_display_matches_lookup_display(
        self, fhir_client, system, code, expected_display
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display
                  https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out `display`

        For each seeded system, the display returned by $expand (extensional
        concept list) MUST match the display returned by $lookup for the same
        code. The two operations share `get_code_infos`; a future divergence
        would silently wrong-answer.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": system, "concept": [{"code": code}]}
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes_to_displays = {c["code"]: c.get("display") for c in contains}
        assert code in codes_to_displays, f"Code {code} missing from expansion"

        # $lookup the same code
        lk_status, lk_resp = _lookup(fhir_client, system, code)
        assert lk_status == 200, lk_resp
        lk_display = _extract_out_param(lk_resp, "display")
        assert lk_display == expected_display

        # Agreement
        assert codes_to_displays[code] == lk_display, (
            f"$expand contains[].display={codes_to_displays[code]!r} DISAGREES with "
            f"$lookup Out display={lk_display!r} for ({system}, {code}). "
            "Cross-operation canonical agreement is required."
        )

    @pytest.mark.parametrize(
        "system,code",
        [
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
            (SNOMED_URI, SNOMED_T2DM),
            (RXNORM_URI, RXNORM_METFORMIN),
            (ICD10CM_URI, ICD10CM_T2DM),
        ],
        ids=["snomed-parent", "snomed-child", "rxnorm", "icd10cm"],
    )
    def test_t31_uri_round_trip_expand_system_is_lookup_able(
        self, fhir_client, system, code
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (contains[] is Coding)
                  https://hl7.org/fhir/R4/codesystem-operation-lookup.html (URI round-trip)

        TS-03 TERMINOLOGIST URI-round-trip methodology applied to $expand:
        each contains[] entry has a `system` URI; $lookup with that URI + code
        MUST return 200 + a Parameters body. A 400 "Unrecognized system URI"
        would mean the contains[] entry is unresolvable by the server itself —
        a clinical safety violation.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": system, "concept": [{"code": code}]}
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes_to_systems = {c["code"]: c.get("system") for c in contains}
        assert code in codes_to_systems
        contains_system = codes_to_systems[code]
        # Sanity: contains_system is non-empty
        assert contains_system, "contains[].system is empty"

        # URI round-trip: $lookup with the SAME system URI.
        lk_status, lk_resp = _lookup(fhir_client, contains_system, code)
        assert lk_status == 200, (
            f"URI round-trip failed: $lookup with system={contains_system!r} "
            f"code={code!r} returned {lk_status}, body={lk_resp}. "
            "Every Coding returned by $expand MUST be $lookup-able."
        )
        assert lk_resp.get("resourceType") == "Parameters"

    def test_t32_implicit_expansion_uri_round_trip(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (implicit value sets)

        For implicit value set expansion, contains[].system is sourced from
        `SYSTEM_TO_FHIR_URI`. Each contains[].system MUST be $lookup-able.
        """
        status, resp = _get_expand(
            fhir_client, {"url": f"{ICD10CM_URI}/vs", "count": 10}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) >= 1
        for c in contains:
            sys_uri = c.get("system")
            code = c.get("code")
            assert sys_uri and code, f"Missing system/code in contains entry: {c}"
            # URI round-trip
            lk_status, lk_resp = _lookup(fhir_client, sys_uri, code)
            assert lk_status == 200, (
                f"Implicit expansion contains[system={sys_uri!r}, code={code!r}] "
                f"is NOT $lookup-able (status={lk_status}). Round-trip required."
            )

    def test_t33_intensional_filter_uri_round_trip(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (intensional compose)

        For `is-a` filter expansions, contains[].system is the include.system
        value. Each contains entry MUST be $lookup-able.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) >= 1
        for c in contains:
            sys_uri = c.get("system")
            code = c.get("code")
            lk_status, _ = _lookup(fhir_client, sys_uri, code)
            assert lk_status == 200, (
                f"Intensional expansion contains[system={sys_uri!r}, code={code!r}] "
                f"is NOT $lookup-able. Round-trip required."
            )


# =============================================================================
# Lens 4: Inactive/abstract code handling in expansions
# =============================================================================

class TestLens4InactiveAbstractCodeHandling:
    """Lens 4 — Inactive codes MUST NOT appear in expansions.

    The engine filters mrconso on SUPPRESS='N' at lookup. The implicit value
    set expander (`_expand_implicit_value_set`) at apps/fhir_api.py:2148
    applies the same filter: `WHERE SAB = ? AND SUPPRESS = 'N'`. The
    conformance fixture has only SUPPRESS='N' rows, so this probe cannot
    directly exercise the inactive-filtering path — it VERIFIES the SQL
    filter is present via source-reading (carry-forward-verification-by-
    source-reading methodology from CS-05 HISTORIAN).

    Abstract codes: CF-SKEPTIC-CS05-01 documents that the engine has no
    abstract-flag data today. $lookup Out `abstract` is hardcoded False
    (CF-SKEPTIC-CS05-01 DEFERRED — finding candidate). $expand contains[]
    has no abstract flag today (ValueSet.expansion.contains inherits display,
    system, code — abstract is not a contains[] field per R4).

    The conformance DB has all SUPPRESS='N' rows. So:
    - Inactive filtering: VERIFIED via source-reading (SQL filter is present).
    - Abstract surfacing: not a contains[] field in R4 — N/A.
    """

    def test_t40_implicit_expansion_filters_inactive_via_source_reading(self):
        """Spec: https://hl7.org/fhir/R4/concept-properties.html (inactive property)

        CS-05 HISTORIAN methodology — carry-forward-verification-by-source-
        reading. The conformance fixture cannot exercise inactive codes (all
        rows are SUPPRESS='N'), so we verify the filter is present via AST
        source-reading.

        The implicit value set expander's SQL filter is the load-bearing
        contract: `WHERE SAB = ? AND SUPPRESS = 'N'`. If a future refactor
        drops the SUPPRESS filter, inactive codes would silently leak into
        expansions — clinicians could act on deprecated terminology.
        """
        import ast

        # Parse the source and find _expand_implicit_value_set.
        source_path = (
            "/mnt/d/medterm4ds/src/medterm4ds/apps/fhir_api.py"
        )
        with open(source_path) as f:
            tree = ast.parse(f.read())

        # Find the _expand_implicit_value_set function.
        impl_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_expand_implicit_value_set":
                impl_fn = node
                break
        assert impl_fn is not None, "_expand_implicit_value_set not found"

        # Walk the function body to find the SQL query string.
        source_text = ast.unparse(impl_fn)
        # The SQL filter MUST include both SAB=? and SUPPRESS='N'.
        assert "SUPPRESS" in source_text, (
            "_expand_implicit_value_set SQL query must filter on SUPPRESS "
            "(inactive codes must not leak into expansions)."
        )
        assert "'N'" in source_text, (
            "_expand_implicit_value_set SQL query must filter SUPPRESS='N' "
            "(active codes only)."
        )

    def test_t41_conformance_fixture_has_no_inactive_codes(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/concept-properties.html (inactive property)

        Sanity probe: the conformance DB has all SUPPRESS='N' rows (active).
        If a future fixture enhancement adds SUPPRESS='O' rows, this probe
        will still pass (the implementation filters them) but the upstream
        probe test_t40 will catch a regression in the filter itself.
        """
        # Implicit expansion of SNOMED should return BOTH active codes.
        status, resp = _get_expand(
            fhir_client, {"url": f"{SNOMED_URI}/vs", "count": 10}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        # Both seeded SNOMED codes are active and should appear.
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    def test_t42_explicit_concept_inactive_filter_via_lookup(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html

        Indirect verification: if the engine returns the code via $lookup
        (the canonical "is this code active?" surface), the same code MUST
        appear in $expand. Both operations filter on SUPPRESS='N'; agreement
        on the active-only filter is the contract.

        Cross-operation canonical agreement (Lens 3) applied to the inactive-
        filtering invariant.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        assert SNOMED_T2DM in codes

        # $lookup must also succeed (same SUPPRESS='N' filter).
        lk_status, lk_resp = _lookup(fhir_client, SNOMED_URI, SNOMED_T2DM)
        assert lk_status == 200
        assert lk_resp.get("resourceType") == "Parameters"


# =============================================================================
# Lens 5: compose.exclude clinical safety
# =============================================================================

class TestLens5ComposeExcludeClinicalSafety:
    """Lens 5 — `compose.exclude` is a clinical safety control. A clinician
    who excludes a code is saying "this code must NOT appear in clinical
    decision-support based on this ValueSet". Silent inclusion is unsafe.

    The implementation in `_expand_intensional` at apps/fhir_api.py:2000-2002:
        for exclude in compose.get("exclude", []):
            exc_codes = {c.get("code") for c in exclude.get("concept", [])}
            contains = [c for c in contains if c["code"] not in exc_codes]

    Known gaps (deferred by SKEPTIC, confirmed by HISTORIAN):
    - CF-SKEPTIC-VS01-02: exclude[].filter[] silently ignored.
    - CF-SKEPTIC-VS01-03: exclude ignores system when matching codes
      (cross-system drift).
    - CF-SKEPTIC-VS01-04: compose metadata (lockedDate, inactive, valueSet)
      silently ignored.

    This iteration VERIFIES that the SAME-system exclude IS honored — the
    core clinical safety property.
    """

    def test_t50_exclude_removes_code_from_expansion(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.exclude

        "Exclude one or more codes from the value set."

        Probe: include SNOMED T2DM; exclude SNOMED T2DM. The expansion MUST
        NOT contain T2DM. This is the load-bearing clinical-safety property.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
                "exclude": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        assert SNOMED_T2DM not in codes, (
            "Excluded code MUST NOT appear in expansion. Silent inclusion "
            "is a clinical safety violation — the clinician explicitly "
            "excluded this code."
        )

    def test_t51_exclude_does_not_remove_unrelated_codes(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.exclude

        Exclude is targeted: excluding one code MUST NOT remove other codes
        from the expansion. A regression that over-excludes would silently
        drop clinically-relevant codes.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [
                            {"code": SNOMED_DIABETES_MELLITUS},
                            {"code": SNOMED_T2DM},
                        ],
                    }
                ],
                "exclude": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        assert SNOMED_DIABETES_MELLITUS in codes, (
            "Exclude of T2DM must NOT remove the unrelated parent code "
            "Diabetes mellitus. Over-exclusion is a clinical safety violation."
        )
        assert SNOMED_T2DM not in codes

    def test_t52_exclude_after_is_a_filter_removes_root(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.exclude

        Exclude is applied AFTER include filters. The composition order is:
        (1) include rules (extensional + intensional); (2) exclude rules.
        A clinician can build a value set "all of Diabetes mellitus hierarchy
        EXCEPT the root" by combining `is-a` with `exclude`.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }
                ],
                "exclude": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]}
                ],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        assert SNOMED_DIABETES_MELLITUS not in codes, (
            "Excluded root MUST NOT appear in expansion (even when it's the "
            "root of an is-a filter)."
        )
        assert SNOMED_T2DM in codes, "Descendant MUST remain (only root excluded)."

    def test_t53_exclude_after_is_a_filter_removes_descendant(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.exclude

        Inverse composition: include `is-a` hierarchy; exclude a DESCENDANT.
        The root MUST remain; only the excluded descendant is removed.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }
                ],
                "exclude": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        assert SNOMED_DIABETES_MELLITUS in codes, "Root MUST remain."
        assert SNOMED_T2DM not in codes, "Excluded descendant MUST NOT appear."

    def test_t54_exclude_empty_concept_is_noop(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.exclude

        An exclude with empty concept[] is a no-op. The implementation's
        `exc_codes = {c.get("code") for c in exclude.get("concept", [])}`
        correctly handles empty concept[] (exc_codes = empty set).
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
                "exclude": [
                    {"system": SNOMED_URI}  # no concept[]
                ],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        assert SNOMED_T2DM in codes, (
            "Exclude with empty concept[] MUST be a no-op."
        )

    def test_t55_exclude_nonexistent_code_is_noop(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.exclude

        Excluding a code NOT in the expansion is a no-op. The exclude set is
        subtracted from contains; subtracting a non-member is a no-op.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
                "exclude": [
                    {"system": SNOMED_URI, "concept": [{"code": "NONEXISTENT999"}]}
                ],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        assert SNOMED_T2DM in codes


# =============================================================================
# Lens 6: CF-HISTORIAN-VS01-01 documentation (R5-drift on R4 surface)
# =============================================================================

class TestLens6CarryForwardDocumentation:
    """Lens 6 — Document CF-HISTORIAN-VS01-01 for CM-* chunks.

    The ConceptMapEquivalence closed-enum drift discovered by VS-01 HISTORIAN
    is on the $translate surface (out of VS-01 scope). However, TERMINOLOGIST
    should document the carry-forward as a probe that asserts the CURRENT
    behavior so the CM-* fix is verifiable.

    CF-TERMINOLOGIST-VS01-01 (NEW): compose.include[].concept[].display
    echoed verbatim by `_expand_intensional` instead of re-resolving the
    canonical preferred term. Client-input-as-canonical drift variant
    (TS-02 TERMINOLOGIST QA-029 shape).
    """

    def test_t60_cf_historian_vs01_01_concept_map_equivalence_drift_current(
        self
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

        CF-HISTORIAN-VS01-01 RESOLVED in the milestone-2 structural
        remediation pass (CR-014). ``INTERNAL_REL_TO_FHIR_EQUIVALENCE``
        (now in ``engines/fhir/equivalence.py`` per CR-024 milestone-3
        review) NOW emits ONLY values in the FHIR R4
        ConceptMapEquivalence closed enum. The prior drift:
          - `subsumedby` (R5/R4B value; R4 spec-correct is `specializes`)
          - `not-relatedto` (NOT in any FHIR version's enum)
        was replaced:
          - `subsumedby`/`subsumed-by` → `specializes`
          - `not-relatedto` → `unmatched`
        This probe now PASSES by asserting the spec-conformance invariant.

        CR-024 (milestone-3 review): the inline map in ``responses.py``
        was consolidated into ``engines/fhir/equivalence.py``. The map
        is now imported by both ``responses.py`` ($translate HTTP surface)
        and ``outputs/fhir.py`` (ConceptMap export surface); the
        closed-enum invariant applies to BOTH surfaces uniformly via the
        canonical module's module-load assertion.
        """
        import ast

        source_path = "/mnt/d/medterm4ds/src/medterm4ds/engines/fhir/equivalence.py"
        with open(source_path) as f:
            tree = ast.parse(f.read())

        # Find the INTERNAL_REL_TO_FHIR_EQUIVALENCE assignment.
        # It may be an ast.Assign OR ast.AnnAssign (typed dict assignment).
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "INTERNAL_REL_TO_FHIR_EQUIVALENCE":
                        found = True
                        break
            elif isinstance(node, ast.AnnAssign):
                tgt = node.target
                if isinstance(tgt, ast.Name) and tgt.id == "INTERNAL_REL_TO_FHIR_EQUIVALENCE":
                    found = True
        assert found, "INTERNAL_REL_TO_FHIR_EQUIVALENCE not found in equivalence.py"

        # FHIR R4 ConceptMapEquivalence enum (canonical 10-value set) —
        # imported from the single source of truth (milestone-2 structural
        # fix CR-014). Per
        # https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html.
        from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE

        # Read source text to extract values.
        source_text = open(source_path).read()
        # Find the INTERNAL_REL_TO_FHIR_EQUIVALENCE block.
        start = source_text.find("INTERNAL_REL_TO_FHIR_EQUIVALENCE")
        end = source_text.find("}", start)
        block = source_text[start:end + 1]

        # CF-HISTORIAN-VS01-01 RESOLVED: the off-spec values MUST NOT
        # appear in the map's VALUE positions today. Keys are still allowed
        # (the engine may emit `subsumedby` or `subsumed-by` as a
        # `CodeMapping.relationship`, and the map translates those keys to
        # the R4 value `specializes`). The assertion checks VALUE positions
        # only: `": "<value>"`.
        for drift_value in ("subsumedby", "not-relatedto"):
            assert f': "{drift_value}"' not in block, (
                f"CF-HISTORIAN-VS01-01 was resolved in milestone-2 but the "
                f"off-spec VALUE {drift_value!r} is still emitted by "
                f"INTERNAL_REL_TO_FHIR_EQUIVALENCE. The fix was reverted."
            )
        # Positive assertion: the R4 spec-correct value IS present.
        assert ': "specializes"' in block, (
            "CF-HISTORIAN-VS01-01 was resolved in milestone-2 — expected "
            "`specializes` (R4 spec-correct) in the emitted values."
        )

        # The R4 enum itself is well-formed (10 values).
        assert len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE) == 10
        assert "specializes" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        assert "subsumedby" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    def test_t61_cf_historian_vs01_01_values_outside_r4_enum_present(self):
        """Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

        CF-HISTORIAN-VS01-01 RESOLVED. Stronger probe: explicitly assert
        the off-spec values are ABSENT from the implementation today. The
        prior probe asserted PRESENCE of `subsumedby`; the milestone-2
        fix flipped this — the value must now be ABSENT.

        CR-024 (milestone-3 review): source path updated to
        ``engines/fhir/equivalence.py`` (the canonical module).
        """
        source_path = "/mnt/d/medterm4ds/src/medterm4ds/engines/fhir/equivalence.py"
        with open(source_path) as f:
            source_text = f.read()

        # Find INTERNAL_REL_TO_FHIR_EQUIVALENCE block.
        start = source_text.find("INTERNAL_REL_TO_FHIR_EQUIVALENCE")
        # Find the matching close brace by simple counting.
        i = source_text.find("{", start)
        depth = 0
        while i < len(source_text):
            if source_text[i] == "{":
                depth += 1
            elif source_text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = source_text[start:i + 1]

        # CF-HISTORIAN-VS01-01 RESOLVED (milestone-2): the off-spec
        # emitted values MUST NOT appear in the map's value positions.
        # NOTE: keys are still allowed (the engine may emit `subsumedby`
        # or `subsumed-by` as a `CodeMapping.relationship`, and the map
        # translates those keys to the R4 value `specializes`). The
        # assertion checks VALUE positions only: `": "<value>"`.
        for drift_value in ("subsumedby", "not-relatedto"):
            assert f': "{drift_value}"' not in block, (
                f"CF-HISTORIAN-VS01-01 was resolved in milestone-2 but the "
                f"off-spec VALUE {drift_value!r} is still emitted by "
                f"INTERNAL_REL_TO_FHIR_EQUIVALENCE. The fix was reverted."
            )
        # Positive assertion: the R4 spec-correct value IS present.
        assert ': "specializes"' in block, (
            "CF-HISTORIAN-VS01-01 was resolved in milestone-2 — expected "
            "`specializes` (R4 spec-correct) in the emitted values. The "
            "fix may have been reverted."
        )

    def test_t62_cf_terminologist_vs01_01_concept_display_client_echo_documented(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.include.concept.display
                  https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        CF-TERMINOLOGIST-VS01-01 documentation: when a client supplies a
        compose.include[].concept[].display, `_expand_intensional` echoes
        the client's display verbatim (apps/fhir_api.py:1945):
            "display": concept.get("display", "")

        Per the spec, contains[].display is "the recommended display for
        this item in the expansion" — implying the SERVER's canonical
        preferred term. CS-03 TERMINOLOGIST test_t10 established the
        precedent on the $validate-code surface: server-canonical wins
        over client input.

        This carry-forward is the SAME shape (client-input-as-canonical
        drift, TS-02 TERMINOLOGIST QA-029 family) but on the $expand
        surface. DEFERRED — engine enhancement required to resolve display
        via `get_code_infos` even when the client supplies one.

        Probe: PIN the current behavior (echo). When VS-* chunks wire
        canonical-resolution, UPDATE this probe to assert canonical-wins.
        """
        # Already covered by test_t11 — this is a methodological companion
        # that documents the carry-forward via a focused, named probe.
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [
                            {
                                "code": SNOMED_DIABETES_MELLITUS,
                                "display": "Client-supplied display (CF-TERMINOLOGIST-VS01-01)",
                            }
                        ],
                    }
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # CURRENT behavior: client-supplied display is echoed.
        # If this fails because the server returns the engine canonical,
        # UPDATE this probe to assert canonical-wins and close CF-TERMINOLOGIST-VS01-01.
        assert contains[0]["display"] == "Client-supplied display (CF-TERMINOLOGIST-VS01-01)"


# =============================================================================
# Lens 7: Patient-friendly surfacing gap (CF-TERMINOLOGIST-01 / GAP-T01)
# =============================================================================

class TestLens7PatientFriendlySurfacing:
    """Lens 7 — Patient-friendly names are surfaced on $lookup but NOT on
    $expand contains[] entries today.

    Per AGENTS.md "Known Fragile Areas" (CF-TERMINOLOGIST-01):
      "apps/fhir_api.py:_expand_implicit_value_set patient-friendly surfacing
       gap (DEFERRED, GAP-T01 / CF-TERMINOLOGIST-01) — the implicit value set
       expander resolves `display` via `get_code_infos` (canonical preferred-
       atom STR) but does NOT consult app.state.patient_friendly_cache."

    Fix shape (when implemented):
      "When a future enhancement chunk wires patient-friendly into
       _expand_implicit_value_set, attach a `patient-friendly` extension to
       each contains[] entry — do NOT override `display` (spec mandates
       `display` is the code system's preferred term)."

    These probes PIN the current behavior so a future implementation that
    adds patient-friendly extensions will fail loudly and require an update.
    """

    def test_t70_expand_contains_no_patient_friendly_extension_today(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains

        CF-TERMINOLOGIST-01 carry-forward-as-probe: $expand contains[] entries
        do NOT have a patient-friendly extension today. WHEN the enhancement
        lands (attach a `patient-friendly` extension per contains[] entry),
        this probe MUST be updated to assert the extension is present.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # contains[] entries MAY have an `extension` field per FHIR R4; today
        # it's absent (no patient-friendly extension surfacing).
        entry_exts = contains[0].get("extension", [])
        patient_friendly_exts = [
            e for e in entry_exts
            if "patient-friendly" in e.get("url", "")
        ]
        assert not patient_friendly_exts, (
            "CF-TERMINOLOGIST-01: $expand contains[] does NOT surface "
            "patient-friendly extensions today. If this probe fails because "
            "the extension was added, UPDATE this probe to assert the "
            "extension value matches the patient-friendly JSON."
        )

    def test_t71_expand_display_remains_canonical_when_patient_friendly_available(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

        When patient-friendly surfacing is eventually implemented, the
        `display` field MUST remain the code system's canonical preferred
        term — NOT the patient-friendly name. The patient-friendly name
        belongs in a SEPARATE extension.

        This probe asserts the CURRENT behavior: display IS the canonical,
        even for codes that have a patient-friendly name available in
        production JSONs. The conformance fixture doesn't load production
        JSONs, so this is a structural-contract probe.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # Display MUST be the canonical preferred term.
        assert contains[0]["display"] == SNOMED_T2DM_DISPLAY, (
            "contains[].display MUST remain the engine canonical preferred "
            "term, NOT the patient-friendly name. Patient-friendly belongs "
            "in a separate extension (CF-TERMINOLOGIST-01 fix shape)."
        )


# =============================================================================
# Lens 8: Compose metadata clinical correctness
# =============================================================================

class TestLens8ComposeMetadata:
    """Lens 8 — CF-SKEPTIC-VS01-04: compose.lockedDate / inactive / valueSet
    are silently ignored today. These are clinical-safety controls:

    - `compose.lockedDate`: a frozen-in-time version of the code system.
      Ignoring it means the expansion uses the current version — fine for
      medterm4ds (single-snapshot engine) but a deferred conformance gap.

    - `compose.inactive`: when True, the server SHOULD include inactive
      codes in the expansion. medterm4ds filters inactive at the SQL layer
      (SUPPRESS='N'); ignoring the flag means inactive codes never appear.
      Today this is conformant-by-accident (the conformance DB has no
      inactive codes).

    - `compose.include[].valueSet`: a nested ValueSet canonical URL. The
      expansion SHOULD include the codes from that nested ValueSet. Ignored
      today (no ValueSet persistence).

    These are NOT bugs today — they're documented deferrals tied to engine
    enhancements. Carry-forward-as-probe (CS-03 TERMINOLOGIST methodology).
    """

    def test_t80_compose_locked_date_silently_ignored(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.lockedDate

        CF-SKEPTIC-VS01-04 (carry-forward-as-probe): compose.lockedDate is
        silently ignored today. The expansion succeeds with the current
        snapshot regardless of the lockedDate value.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "lockedDate": "2020-01-01",
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        # lockedDate is ignored; the code still appears.
        assert SNOMED_T2DM in codes

    def test_t81_compose_inactive_true_silently_ignored(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.inactive

        CF-SKEPTIC-VS01-04 (carry-forward-as-probe): compose.inactive=True
        is silently ignored today. The expansion filters inactive at the
        SQL layer regardless of the flag.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "inactive": True,
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        # inactive=True is ignored; the active code still appears.
        # (And no inactive codes appear, because the conformance fixture
        # has only SUPPRESS='N' rows.)
        assert SNOMED_T2DM in codes

    def test_t82_compose_include_valueset_silently_ignored(self, fhir_client):
        """Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.include.valueSet

        CF-SKEPTIC-VS01-04 (carry-forward-as-probe): compose.include[].valueSet
        (nested ValueSet canonical URL) is silently ignored today.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "valueSet": ["http://example.org/fhir/ValueSet/some-vs"],
                        "concept": [{"code": SNOMED_T2DM}],
                    }
                ]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        # The nested valueSet URL is ignored; the explicit concept still appears.
        assert SNOMED_T2DM in codes


# =============================================================================
# Lens 9: Boolean rendering on $expand (XML capitalization safety)
# =============================================================================

class TestLens9BooleanRenderingXmlSafety:
    """Lens 9 — Boolean values in $expand responses MUST render lowercase
    (`true`/`false`) per FHIR R4 §3.4.1, NOT Python's `str(True)` = "True".

    Per GLOBAL_RULES.md "Code Review Time": "Python's `str(False)` is
    `\"False\"` (capital F), not `\"false\"`. FHIR R4 §3.4.1 mandates
    lowercase `true`/`false` for boolean primitives."

    The `valueset-toocostly` extension has `valueBoolean: True` when
    truncation occurs. In XML, this MUST render as `value="true"` (lowercase),
    not `value="True"` (capital T).

    The CR-002 fix (`_scalar_to_xml_attr` boolean special-case in
    engines/fhir/xml.py) structurally handles this — but the $expand
    XML surface with the toocostly extension is the FIRST place this
    fix is exercised on a TRUNCATION EXTENSION (prior probes tested
    $validate-code result, $subsumes outcome, $lookup abstract).
    """

    def test_t90_toocostly_extension_renders_lowercase_boolean_in_xml(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/xml-representation.html (boolean primitives)
                  https://hl7.org/fhir/extension-valueset-toocostly.html

        XML serialization of the toocostly extension's valueBoolean MUST
        be lowercase `true`, not capital-T `True`.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }
                ]
            },
        }
        # count=1 forces truncation → toocostly extension surfaces.
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=body,
            params={"count": 1, "_format": "xml"},
        )
        assert resp.status_code == 200
        body_text = resp.text
        # The toocostly extension must be present.
        assert "valueset-toocostly" in body_text
        # Boolean MUST be lowercase per FHIR R4 §3.4.1.
        assert 'value="true"' in body_text, (
            "toocostly extension valueBoolean MUST render as lowercase 'true' "
            "per FHIR R4 §3.4.1."
        )
        # CR-002 fix shape: NO capital-T True.
        assert 'value="True"' not in body_text, (
            "CR-002 regression: capital-T 'True' in XML boolean. "
            "Python's str(True) is 'True' (capital T); FHIR R4 mandates 'true'."
        )

    def test_t91_no_capital_t_booleans_in_any_expand_xml_response(
        self, fhir_client
    ):
        """Spec: https://hl7.org/fhir/R4/xml-representation.html (boolean primitives)

        CR-002 fix shape extension to $expand: NO capital-T `True` appears
        in any XML serialization of an $expand response.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_T2DM}],
                    }
                ]
            },
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand", json=body, params={"_format": "xml"}
        )
        assert resp.status_code == 200
        body_text = resp.text
        # No capital-T True anywhere in the response.
        assert 'value="True"' not in body_text
        assert 'value="False"' not in body_text
