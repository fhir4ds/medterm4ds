"""VS-03 SKEPTIC: ValueSet $expand — Advanced.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Filter operator: https://hl7.org/fhir/R4/valueset.html#filter
Filter Operator enum: https://hl7.org/fhir/R4/valueset-concept-operator.html

5 spec items:

  1. ``valueSet`` parameter accepts inline ValueSet resource (POST).
  2. Explicit concept list expansion returns exactly those concepts.
  3. Filter with ``is-a`` operator on SNOMED returns all descendants + root.
  4. Filter with ``descendent-of`` operator returns descendants only (no root).
  5. ``date`` parameter evaluates expansion at a specific point in time.

SKEPTIC lens: adversarial bug hunting. Each probe exercises one spec-mandated
behavior; failures indicate silent-wrong-answer or non-conformant shape.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)

Carry-forwards relevant to VS-03:
  - CF-SKEPTIC-VS01-01: 7 of 9 filter operators silently dropped. VS-03
    probes each again to confirm carry-forward still applies.
  - CF-HISTORIAN-VS02-01: BFS cap on total still truncated (HIGH, deferred).
  - CF-HISTORIAN-VS02-02: implicit path doesn't use canonical_system_uri.
  - CF-EXPLORER-VS01: Parameters-with-valueSet silently dropped (deferred
    enhancement) — this chunk's Item 1 SHOULD close it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/valueset-concept-operator.html (Filter Operator)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
#
# FHIR R4 filter-operator enum — single source of truth per milestone-2 review
# (CR-014): import the canonical frozen-set rather than redefining it.
from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

ISO_8601_INSTANT = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


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
    """Extract (system, code) pairs from a ValueSet.expansion.contains."""
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
# Item 1: ``valueSet`` parameter accepts inline ValueSet resource (POST)
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html In Parameters
#   valueSet: 0..1 ValueSet — "The value set is provided directly as part of
#   the request. ..."
# =============================================================================


class TestItem1ValueSetInlineParameter:
    """Item 1: ``valueSet`` parameter accepts an inline ValueSet resource.

    Per https://hl7.org/fhir/R4/valueset-operation-expand.html In Parameters:

        valueSet: 0..1 ValueSet
        "The value set is provided directly as part of the request. Servers
         SHOULD expand the value set and SHOULD NOT use the value set cached
         on the server."

    Two valid POST body shapes:
      (a) Bare ValueSet resource (resourceType: ValueSet) — already supported.
      (b) Parameters resource with a ``valueSet`` parameter that contains a
          nested ValueSet (parameter.resource). Spec reference:
          https://hl7.org/fhir/R4/parameters.html — "A parameter can have a
          resource as a value (using the ``resource`` property rather than
          value[x])."

    Carry-forward CF-EXPLORER-VS01 (test_e13): the Parameters-with-valueSet
    shape is silently dropped by ``_parse_parameters`` (scalar-only). This
    chunk's Item 1 SHOULD close that gap; if the gap remains, document it
    as a confirmed open bug.
    """

    def test_s10_bare_valueset_body(self, fhir_client):
        """Sanity check: bare ValueSet body (resourceType: ValueSet) expands.

        This is the primary inline form and MUST work per FHIR R4 §4.7.5.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s11_parameters_with_valueset_param_expand(self, fhir_client):
        """POST a Parameters body containing a ``valueSet`` parameter that
        references an inline ValueSet via ``parameter.resource``.

        Per https://hl7.org/fhir/R4/parameters.html: a parameter MAY use the
        ``resource`` property (a full resource instead of value[x]). For
        ``$expand``, the spec In Parameters table explicitly lists ``valueSet``
        (0..1 ValueSet). The conformant body shape is:

            {
              "resourceType": "Parameters",
              "parameter": [
                {"name": "valueSet", "resource": {<ValueSet body>}}
              ]
            }

        Found by SKEPTIC iteration VS-03 (QA-059) — the prior implementation
        only honored the bare-ValueSet body shape and silently dropped the
        Parameters-with-valueSet form. Fixed by ``_extract_valueset_from_
        parameters`` helper wired into ``expand_post``.
        """
        nested_vs = _make_extensional_snomed()
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": nested_vs}
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, (
            f"valueSet inline param dropped: codes={codes}"
        )
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s12_parameters_with_valueset_via_part(self, fhir_client):
        """POST Parameters-with-valueSet using the spec-correct R4 shape:
        ``parameter.resource`` carrying the ValueSet directly, plus a count
        parameter to exercise truncation through the inline-ValueSet path.

        Found by SKEPTIC iteration VS-03 (QA-059). Same fix shape as test_s11.
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
        assert body["resourceType"] == "ValueSet"
        # Truncated to 1.
        assert len(body["expansion"]["contains"]) <= 1
        # total reflects un-truncated size (2 concepts).
        assert body["expansion"]["total"] == 2
        # toocostly present (truncation signal).
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"missing {TOOCOSTLY_URL} extension: {exts}"
        )

    def test_s13_parameters_with_missing_resource_returns_400(self, fhir_client):
        """POST Parameters-with-valueSet where the parameter has NO ``resource``
        subfield. The implementation MUST NOT crash (500); it MUST return a
        graceful 400 OperationOutcome (or silently drop and fall through to
        the no-url 400 path).
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet"}  # no resource, no value[x]
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        # MUST be 400 (no usable input), NOT 500.
        assert status in (400, 422), f"expected 400/422, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_s14_parameters_with_wrong_resourcetype(self, fhir_client):
        """POST Parameters-with-valueSet where the nested resource has
        resourceType != ValueSet. The implementation MUST NOT silently expand
        a non-ValueSet resource; it MUST 400 (or drop and fall through to the
        no-url 400 path).
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": {
                        "resourceType": "Patient",
                        "id": "not-a-valueset",
                    }
                }
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        # MUST be 400 (or 200 with empty expansion if silently dropped — not
        # preferred but acceptable). MUST NOT be 500.
        assert status < 500, (
            f"server crash on wrong-resourceType inline valueSet: {status} {body}"
        )

    def test_s15_deeply_nested_compose(self, fhir_client):
        """POST a bare ValueSet body with deeply nested compose (multiple
        include blocks, mixed concepts and filter). The implementation MUST
        expand all includes per FHIR R4 §4.9.4 (compose.include is 1..*).
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-deeply-nested",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [
                            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"}
                        ]
                    },
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                        ]
                    },
                ]
            }
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        codes = _contains_codes(body)
        # Both the explicit concept AND the is-a root AND the descendant appear.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes


# =============================================================================
# Item 2: Explicit concept list expansion returns exactly those concepts
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.include.concept
# =============================================================================


class TestItem2ExplicitConceptList:
    """Item 2: explicit concept list expansion returns exactly those concepts."""

    def test_s20_single_concept_list(self, fhir_client):
        """Single concept in the list — expansion MUST contain exactly that
        one code."""
        vs = _make_extensional_snomed(concepts=[
            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"}
        ])
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert codes == [(SNOMED_URI, SNOMED_DIABETES_MELLITUS)], (
            f"expected exactly 1 code, got {codes}"
        )

    def test_s21_two_concept_list(self, fhir_client):
        """Two concepts in the list — expansion MUST contain both."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert len(codes) == 2
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s22_cross_system_concept_list(self, fhir_client):
        """Concepts from multiple systems in the same ValueSet."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"}
                    ]},
                    {"system": ICD10CM_URI, "concept": [
                        {"code": ICD10CM_T2DM, "display": "T2DM"}
                    ]},
                ]
            }
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (ICD10CM_URI, ICD10CM_T2DM) in codes

    def test_s23_omitted_display_resolves_canonical(self, fhir_client):
        """When client OMITS display, the engine's canonical preferred term
        MUST be used (VS-01 TERMINOLOGIST QA-056 fix).

        Per https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display:
        "The recommended display for this item in the expansion."
        """
        vs = _make_extensional_snomed(concepts=[
            {"code": SNOMED_DIABETES_MELLITUS},  # no display
        ])
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        displays = _contains_displays(body)
        canonical = displays.get((SNOMED_URI, SNOMED_DIABETES_MELLITUS), "")
        # Canonical engine name from fixture: "Diabetes mellitus"
        assert canonical == "Diabetes mellitus", (
            f"expected canonical display, got {canonical!r}"
        )

    def test_s24_invalid_code_graceful(self, fhir_client):
        """Concept list with an invalid code (not in the code system). The
        implementation MUST handle gracefully — either include the code with
        empty display, OR exclude the code with a warning, OR return 400.
        MUST NOT crash (500).
        """
        vs = _make_extensional_snomed(concepts=[
            {"code": "NONEXISTENT_99999", "display": "fake"},
            {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
        ])
        status, body = _post_expand(fhir_client, vs)
        assert status < 500, (
            f"server crash on invalid code in concept list: {status} {body}"
        )
        if status == 200:
            codes = _contains_codes(body)
            # The valid code MUST be present.
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_s25_concept_list_with_count_truncates_with_toocostly(self, fhir_client):
        """Concept list (2 concepts) with count=1 MUST truncate AND emit
        the valueset-toocostly extension (VS-01 TERMINOLOGIST QA-055 +
        VS-02 SKEPTIC QA-057)."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        # Truncated to 1.
        assert len(body["expansion"]["contains"]) == 1
        # Total = un-truncated (2 concepts available).
        assert body["expansion"]["total"] == 2
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"missing {TOOCOSTLY_URL}: {exts}"
        )


# =============================================================================
# Item 3: ``is-a`` filter returns all descendants + root
# Spec: https://hl7.org/fhir/R4/valueset.html#filter
#   is-a: "The property value has the concept specified as the value as one
#   of its parents through transitive is-a relationships, OR the concept
#   specified as the value is the property value itself."
# =============================================================================


class TestItem3IsAFilter:
    """Item 3: ``is-a`` filter returns all descendants + the root code."""

    def test_s30_is_a_on_root_includes_root_and_descendant(self, fhir_client):
        """``is-a Diabetes mellitus`` MUST return Diabetes + T2DM (the child).

        Per FHIR R4 valueset.html#filter ``is-a``: "The property value has
        the concept specified as the value as one of its parents through
        transitive is-a relationships, OR the concept specified as the value
        is the property value itself." (Root is included.)
        """
        vs = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, (
            f"is-a root NOT included: codes={codes}"
        )
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"is-a descendant NOT included: codes={codes}"
        )

    def test_s31_is_a_on_leaf_returns_just_leaf(self, fhir_client):
        """``is-a`` on a leaf code (no descendants) MUST return just the leaf
        (the root is always included)."""
        vs = _make_intensional_snomed_isa(SNOMED_T2DM)
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes
        # No descendants of T2DM, so the only entry is T2DM itself.
        snomed_codes = [c for s, c in codes if s == SNOMED_URI]
        assert snomed_codes == [SNOMED_T2DM], (
            f"expected just leaf, got {snomed_codes}"
        )

    def test_s32_is_a_root_display_resolved(self, fhir_client):
        """When ``is-a`` filter is applied, the root code's display MUST be
        resolved from the engine (canonical preferred term).
        """
        vs = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        displays = _contains_displays(body)
        root_display = displays.get((SNOMED_URI, SNOMED_DIABETES_MELLITUS), "")
        assert root_display == "Diabetes mellitus", (
            f"root display not resolved: {root_display!r}"
        )

    def test_s33_is_a_descendant_display_resolved(self, fhir_client):
        """Descendants' displays MUST be resolved from the engine."""
        vs = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        displays = _contains_displays(body)
        desc_display = displays.get((SNOMED_URI, SNOMED_T2DM), "")
        assert desc_display == "Type 2 diabetes mellitus", (
            f"descendant display not resolved: {desc_display!r}"
        )

    def test_s34_is_a_total_reflects_untruncated_size(self, fhir_client):
        """``is-a`` with count=1 MUST truncate AND emit toocostly AND
        report un-truncated total (VS-02 SKEPTIC QA-057)."""
        vs = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        # contains truncated to 1.
        assert len(body["expansion"]["contains"]) == 1
        # total reflects un-truncated size (2 = root + descendant).
        # NOTE: per CF-HISTORIAN-VS02-01, when BFS-capped paths truncate,
        # total is computed from post-BFS relations. The fixture has
        # exactly 1 descendant matching BFS limit=1, so total == 2 by
        # fixture coincidence. This probe documents current behavior.
        assert body["expansion"]["total"] == 2, (
            f"total mismatch: {body['expansion']['total']}"
        )
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts)


# =============================================================================
# Item 4: ``descendent-of`` filter returns descendants only (no root)
# Spec: https://hl7.org/fhir/R4/valueset.html#filter
#   descendent-of: "The property value has the concept specified as the value
#   as one of its parents through transitive is-a relationships (the concept
#   specified as the value is NOT included in the expansion)."
# =============================================================================


class TestItem4DescendentOfFilter:
    """Item 4: ``descendent-of`` filter returns descendants only (no root).

    NOTE: per VS-01 SKEPTIC QA-054, the spec-correct spelling is
    ``descendent-of`` (Latin-derived), NOT ``descendant-of``. The
    ``descendant-of`` form is silently dropped today.
    """

    def test_s40_descendent_of_on_root_excludes_root(self, fhir_client):
        """``descendent-of Diabetes mellitus`` MUST return ONLY the descendant
        (T2DM), NOT Diabetes mellitus itself."""
        vs = _make_intensional_snomed_descendent_of(SNOMED_DIABETES_MELLITUS)
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"descendent-of descendant NOT included: {codes}"
        )
        # ROOT MUST NOT BE PRESENT.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes, (
            f"descendent-of root WAS included (should be excluded): {codes}"
        )

    def test_s41_descendent_of_on_leaf_returns_empty(self, fhir_client):
        """``descendent-of`` on a leaf code MUST return an empty expansion."""
        vs = _make_intensional_snomed_descendent_of(SNOMED_T2DM)
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert codes == [], (
            f"descendent-of on leaf should return empty, got {codes}"
        )

    def test_s42_descendent_of_distinct_from_is_a(self, fhir_client):
        """``descendent-of`` MUST produce a strict subset of ``is-a`` (root
        excluded). Distinct from is-a which includes the root.

        Per spec text: "the concept specified as the value is NOT included
        in the expansion" — distinct from ``is-a`` which DOES include it.
        """
        vs_isa = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        vs_desc = _make_intensional_snomed_descendent_of(SNOMED_DIABETES_MELLITUS)
        status1, body_isa = _post_expand(fhir_client, vs_isa)
        status2, body_desc = _post_expand(fhir_client, vs_desc)
        assert status1 == 200 and status2 == 200
        isa_codes = set(_contains_codes(body_isa))
        desc_codes = set(_contains_codes(body_desc))
        # descendent-of MUST be a strict subset of is-a.
        assert desc_codes.issubset(isa_codes)
        # is-a MUST include the root; descendent-of MUST NOT.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in isa_codes
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in desc_codes

    def test_s43_descendant_of_offspec_silently_dropped(self, fhir_client):
        """Off-spec spelling ``descendant-of`` (common English) MUST be
        silently dropped or rejected (per VS-01 SKEPTIC QA-054). The
        spec-correct spelling is ``descendent-of``."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "descendant-of", "value": SNOMED_DIABETES_MELLITUS}
                    ]
                }]
            }
        }
        status, body = _post_expand(fhir_client, vs)
        # MUST NOT honor off-spec spelling (would silently wrong-answer).
        assert status == 200  # the call doesn't crash
        codes = _contains_codes(body)
        # Off-spec spelling is silently dropped → empty contains (root NOT
        # included either, since descendant-of is not recognized).
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes


# =============================================================================
# Item 5: ``date`` parameter evaluates expansion at a specific point in time
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html In Parameters
#   date: 0..1 dateTime — "The date for which the expansion is to be
#   performed."
# =============================================================================


class TestItem5DateParameter:
    """Item 5: ``date`` parameter evaluates expansion at a point in time.

    Per https://hl7.org/fhir/R4/valueset-operation-expand.html In Parameters:

        date: 0..1 dateTime
        "The date for which the expansion is to be performed. ... Usually this
         is the current date, but there are situations where the server should
         expand the value set for a date in the past or future."

    medterm4ds is single-snapshot (no versioned data scoping per AGENTS.md NOT
    A BUG registry — `version` and `offset` are accepted but ignored). The
    `date` parameter follows the same pattern: MUST be accepted without
    crashing, MAY be ignored for actual evaluation. This is conformant per
    spec ("Servers MAY ignore the date parameter and expand for the current
    date").
    """

    def test_s50_past_date_accepted_on_get(self, fhir_client):
        """``date`` (past) on GET MUST be accepted (no 422/500)."""
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "2020-01-01"},
        )
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"

    def test_s51_future_date_accepted_on_get(self, fhir_client):
        """``date`` (future) on GET MUST be accepted."""
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "2099-12-31"},
        )
        assert status == 200, f"expected 200, got {status}: {body}"

    def test_s52_full_dateTime_accepted(self, fhir_client):
        """``date`` as a full ISO 8601 dateTime (with time component) MUST
        be accepted per FHIR R4 §3.4.1 dateTime."""
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "2025-06-15T10:30:00Z"},
        )
        assert status == 200, f"expected 200, got {status}: {body}"

    def test_s53_date_param_in_post_parameters_body(self, fhir_client):
        """``date`` in a POST Parameters body MUST be accepted (no crash).

        Per spec, ``date`` is a top-level In parameter; the POST handler
        should accept it via the Parameters body.
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "date", "valueDateTime": "2025-01-01"},
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"

    def test_s54_malformed_date_does_not_crash(self, fhir_client):
        """``date`` malformed MUST NOT crash the server (no 500).

        The implementation may accept (ignore) or reject (400). MUST NOT
        crash. Per FHIR R4 §3.4.1, malformed dateTime is non-conformant, so
        400 is acceptable.
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "not-a-date"},
        )
        # Acceptable: 200 (ignored) OR 400 (rejected). MUST NOT be 500.
        assert status < 500, (
            f"server crash on malformed date: {status} {body}"
        )


# =============================================================================
# CF-SKEPTIC-VS01-01: 7 missing filter operators
# Per the chunk assignment: probe each. If silently dropped, log as bug.
# If returned 400 with clear message, INTENDED.
# Source: https://hl7.org/fhir/R4/valueset-concept-operator.html
# =============================================================================


class TestCFSkepticVS01_01_SevenFilterOperators:
    """Re-probe the 7 missing filter operators from CF-SKEPTIC-VS01-01.

    Per the chunk assignment: probe each operator and verify behavior. If
    silently dropped, log as bug (silent-wrong-answer pattern). If returned
    400 with clear message, INTENDED.

    Current behavior (per VS-01 SKEPTIC + VS-02 HISTORIAN test_h20): all 7
    operators are silently dropped at DEBUG log level. These probes pin the
    current behavior so that when a future chunk implements them, the probes
    fail loudly and MUST be updated.
    """

    @pytest.mark.parametrize("op", sorted([
        "=", "is-not-a", "regex", "in", "not-in", "generalizes", "exists"
    ]))
    def test_s60_operator_silently_dropped_today(self, fhir_client, op):
        """Each of the 7 missing operators is silently dropped today.

        The probe documents the silent-drop behavior. When the operator is
        implemented, the probe MUST be updated to assert the spec-correct
        behavior.
        """
        # `exists` uses boolean value; others use code/regex/value.
        value = "true" if op == "exists" else SNOMED_DIABETES_MELLITUS
        prop = "inactive" if op == "exists" else "concept"
        if op == "regex":
            prop = "display"
            value = "[Dd]iabetes"
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": prop, "op": op, "value": value}
                    ]
                }]
            }
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200 for op={op}, got {status}: {body}"
        codes = _contains_codes(body)
        # Pinning current silent-drop behavior.
        assert codes == [], (
            f"op={op} not silently dropped — codes={codes}. If this fails, "
            f"the operator is now honored — update the probe to assert "
            f"spec-correct behavior."
        )


# =============================================================================
# Combined filters: AND semantics
# Spec: https://hl7.org/fhir/R4/valueset.html#filter
# "When multiple filters are applied, they are ANDed together."
# =============================================================================


class TestCombinedFilters:
    """Multiple filters in compose.include[].filter[] have AND semantics."""

    def test_s70_is_a_plus_nonexistent_filter(self, fhir_client):
        """``is-a`` + ``regex`` (silently dropped). The is-a filter MUST
        produce its expected result; the regex drop MUST NOT affect the
        is-a expansion (silent drop at DEBUG level)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS},
                        {"property": "display", "op": "regex", "value": "[Dd]iabetes.*"},
                    ]
                }]
            }
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # is-a honored → root + descendant present.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s71_is_a_plus_is_a_same_root(self, fhir_client):
        """Two ``is-a`` filters on the same root — MUST produce the same
        result as a single ``is-a`` filter (idempotent / deduplication)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS},
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS},
                    ]
                }]
            }
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Deduplication: each (system, code) appears exactly once.
        assert len(codes) == len(set(codes)), f"duplicates: {codes}"

    def test_s72_filter_with_invalid_property_silently_dropped(self, fhir_client):
        """Filter with an invalid property (e.g., "nonexistent") and op=is-a
        MUST be silently dropped (current behavior, per AGENTS.md "Known
        Fragile Areas" line about filter-property handling)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "nonexistent", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                    ]
                }]
            }
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Invalid property → silently dropped → empty expansion.
        assert codes == [], (
            f"invalid-property filter not silently dropped: {codes}"
        )
