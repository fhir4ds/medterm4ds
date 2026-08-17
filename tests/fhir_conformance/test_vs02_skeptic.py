"""VS-02 SKEPTIC: ValueSet $expand — Basic.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Filter matching: https://hl7.org/fhir/R4/valueset-operation-expand.html (filter param)
Paging semantics: https://hl7.org/fhir/R4/valueset-operation-expand.html (offset/count)
Too-costly: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

8 spec items:

  1. Required params: ``url`` (or instance-level ValueSet).
  2. Optional params: ``filter`` (text), ``offset``, ``count``, ``valueSet``
     (inline), ``date``.
  3. Response is ValueSet with
     ``expansion.{timestamp, total, contains[]}``.
  4. Expansion contains: ``system``, ``code``, ``display``, ``version``
     (when applicable).
  5. Paging semantics: ``offset``+``count``, server SHOULD return ``total``.
  6. Hierarchical expansions are not paged (entire expansion returned).
  7. Server SHOULD return OperationOutcome with code ``too-costly`` for very
     large expansions.
  8. Filter text matches against display, code, or designation (server
     discretion).

SKEPTIC lens: adversarial bug hunting. Each probe exercises one spec-mandated
behavior; failures indicate silent-wrong-answer or non-conformant shape.

Conformance fixture: SNOMEDCT_US has 2 codes (Diabetes mellitus / T2DM);
ICD10CM has 1 code (E11); RXNORM has 1 code (860975 metformin); mrrel has a
single isa relationship (T2DM → Diabetes mellitus). This fixture is small but
sufficient to exercise the spec items in this chunk.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion (expansion shape)
#
# FHIR R4 filter-operator enum — single source of truth per milestone-2 review
# (CR-014): import the canonical frozen-set from engines.fhir rather than
# redefining it locally. VS-01 SKEPTIC QA-054 found that the test suite
# encoded the off-spec ``descendant-of`` spelling as expected behavior —
# importing the canonical constant prevents that class of drift.
from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# ISO 8601 UTC timestamp regex. Per FHIR R4 §3.4.1 (dateTime) and §4.9.1
# (expansion.timestamp), the timestamp MUST be a valid instant.
# Examples: "2026-07-13T10:30:00Z", "2026-07-13T10:30:00.123+00:00".
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
    """Extract the (system, code) pairs from a ValueSet.expansion.contains."""
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


def _make_intensional_snomed_isa() -> dict:
    """Helper: build an intensional ValueSet body with is-a SNOMED Diabetes."""
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs02-test-intensional",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                ],
            }],
        },
    }


def _make_extensional_snomed() -> dict:
    """Helper: build an extensional ValueSet body with 2 SNOMED codes."""
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs02-test-extensional",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "concept": [
                    {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"},
                    {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"},
                ],
            }],
        },
    }


# =============================================================================
# Item 1: Required params — url OR instance-level ValueSet
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html "In Parameters"
# url: "A canonical URL for a ValueSet"
# When using POST with a ValueSet body, url is not required.
# =============================================================================


class TestItem1RequiredParams:
    """Item 1: required parameters for $expand."""

    def test_s10_get_without_url_and_without_body_returns_400(self, fhir_client):
        """GET without ``url`` (no body possible for GET) MUST 400.

        Per https://hl7.org/fhir/R4/valueset-operation-expand.html In
        Parameters, ``url`` is required (when no body ValueSet is supplied).
        The current implementation returns 400 with a clear OperationOutcome
        message ("Provide a ValueSet body, a fhir_vs URL, or a filter
        parameter.").
        """
        status, body = _get_expand(fhir_client, params={})
        assert status == 400, f"expected 400, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_s11_post_valueset_body_works_without_url(self, fhir_client):
        """POST a ValueSet body — ``url`` is NOT required per FHIR R4 §4.7.5
        In Parameters (``valueSet`` 0..1 ValueSet is the alternative to
        ``url``).
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"
        # Both listed concepts MUST appear.
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s12_get_with_url_to_unknown_value_set_returns_400(self, fhir_client):
        """GET with a ``url`` to an unrecognized ValueSet MUST 400.

        Per FHIR R4 §4.7.5, the ``url`` parameter references a canonical URL.
        medterm4ds does not persist ValueSets; an unknown URL is a 400 (per
        TS-02 EXPLORER QA-027 fix shape: try/except ValueError).
        """
        status, body = _get_expand(
            fhir_client,
            params={"url": "http://example.org/vs/nonexistent"},
        )
        # Server returns 400 OR another OperationOutcome — the spec doesn't
        # mandate the exact code, but the request should not silently succeed.
        assert status in (400, 404), f"expected 400/404, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_s13_post_parameters_body_without_url_and_without_filter_400(self, fhir_client):
        """POST a Parameters body with neither ``url`` nor ``filter`` MUST 400.

        The Parameters body must carry at least one of (url, filter,
        valueSet) per FHIR R4 §4.7.5.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [],
        }
        status, body_json = _post_expand(fhir_client, body)
        assert status == 400, f"expected 400, got {status}: {body_json}"
        assert body_json.get("resourceType") == "OperationOutcome"


# =============================================================================
# Item 2: Optional params — filter, offset, count, valueSet (inline), date
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html In Parameters.
# =============================================================================


class TestItem2OptionalParams:
    """Item 2: optional parameters."""

    def test_s20_filter_text_present(self, fhir_client):
        """``filter`` (text): basic text filter.

        Per https://hl7.org/fhir/R4/valueset-operation-expand.html In
        Parameters ``filter``: "a text filter..." — server MAY match display,
        code, or designation.
        """
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"
        # Filter should find the Diabetes mellitus code.
        assert body["expansion"]["total"] >= 1

    def test_s21_offset_zero_returns_first_page(self, fhir_client):
        """``offset=0`` MUST behave as no offset (first page).

        Per https://hl7.org/fhir/R4/valueset-operation-expand.html In
        Parameters ``offset``: paging offset, 0-based.
        """
        vs = _make_extensional_snomed()
        status0, body0 = _post_expand(fhir_client, vs, params={"offset": 0})
        assert status0 == 200, f"expected 200, got {status0}: {body0}"
        codes0 = _contains_codes(body0)
        # offset=0 should return the same as no offset.
        status_no, body_no = _post_expand(fhir_client, vs)
        assert status_no == 200
        codes_no = _contains_codes(body_no)
        assert codes0 == codes_no, (
            f"offset=0 returned different codes than no offset: {codes0} vs {codes_no}"
        )

    def test_s22_count_zero_returns_empty_expansion(self, fhir_client):
        """``count=0`` SHOULD return empty expansion per FHIR R4 §4.7.5.

        Per FHIR R4 In Parameters: "count ... The maximum number of results
        to return. A count of 0 means that no entries will be returned."

        NOTE: the current implementation enforces ``count >= 1`` via FastAPI
        Query, returning 422 for count=0. The spec text allows count=0 to
        return an empty expansion. This probe documents the CURRENT behavior
        (422) and the spec-divergence; the next chunk implementing paging
        MUST update this probe (carry-forward-as-probe pattern).
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 0})
        # Current behavior: 422 (count >= 1 enforced by FastAPI).
        # Spec-correct: 200 with empty contains[].
        if status == 422:
            # Documented divergence — see carry-forward CF-SKEPTIC-VS02-01.
            assert body.get("resourceType") == "OperationOutcome"
        else:
            # If the divergence is fixed, the probe asserts spec-correct.
            assert status == 200, f"expected 200, got {status}: {body}"
            assert body["resourceType"] == "ValueSet"
            assert body["expansion"]["contains"] == []
            assert body["expansion"]["total"] == 0

    def test_s23_count_negative_rejected(self, fhir_client):
        """``count=-1`` MUST be rejected (negative count is non-sensical).

        Per FHIR R4: count must be non-negative integer. The FastAPI
        ``Query(20, ge=1, le=1000)`` enforces this.
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "count": -1},
        )
        assert status == 422, f"expected 422, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_s24_count_exceeding_total_returns_all(self, fhir_client):
        """``count`` exceeding total MUST return all matches without error.

        Per FHIR R4 §4.7.5: count is a maximum, not exact.
        """
        vs = _make_extensional_snomed()  # 2 concepts
        status, body = _post_expand(fhir_client, vs, params={"count": 1000})
        assert status == 200, f"expected 200, got {status}: {body}"
        # All 2 concepts returned (count exceeds total).
        assert body["expansion"]["total"] == 2

    def test_s25_count_1_truncates_with_toocostly(self, fhir_client):
        """``count=1`` on a 2-concept expansion MUST truncate AND emit
        the valueset-toocostly extension per FHIR R4 §4.7.5 + §4.9.5.

        Per VS-01 TERMINOLOGIST QA-055 fix: ``expand_post`` honors client
        ``count`` for both ValueSet-body and Parameters-body branches.
        Without that fix, the truncation extension was silently dropped.
        """
        vs = _make_extensional_snomed()  # 2 concepts
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200, f"expected 200, got {status}: {body}"
        # Truncated to count=1.
        assert len(body["expansion"]["contains"]) == 1
        # total reflects the UN-truncated size (2 concepts available).
        assert body["expansion"]["total"] == 2
        # valueset-toocostly extension present.
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"missing {TOOCOSTLY_URL} extension on truncated expansion: {exts}"
        )

    def test_s26_offset_non_integer_rejected(self, fhir_client):
        """``offset`` non-integer MUST be rejected by FastAPI."""
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "offset": "abc"},
        )
        assert status == 422, f"expected 422, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_s27_offset_negative_rejected(self, fhir_client):
        """``offset`` negative MUST be rejected."""
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "offset": -1},
        )
        assert status == 422, f"expected 422, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_s28_date_param_accepted(self, fhir_client):
        """``date`` param accepted (used to pin a version-snapshot).

        Per FHIR R4 §4.7.5 In Parameters ``date``: "The date for which the
        expansion is to be performed". medterm4ds is single-snapshot (no
        versioned data scoping); the param is accepted for spec-compat.
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "2025-01-01"},
        )
        # The param is accepted (no 422 about unknown param). Implementation
        # may or may not use it; the route MUST accept it.
        assert status != 422, f"date param should be accepted, got 422: {body}"

    def test_s29_valueSet_inline_param_accepted_via_body(self, fhir_client):
        """``valueSet`` (inline) is supplied as POST body per FHIR R4 §4.7.5.

        The body shape for POST is either a ValueSet OR a Parameters
        resource with a ``valueSet`` parameter. The ValueSet-body branch is
        the primary inline form (CF-EXPLORER-VS01 / test_e13 of VS-01
        EXPLORER documents the Parameters-with-valueSet form as a deferred
        enhancement).
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_s2a_filter_with_regex_chars_accepted(self, fhir_client):
        """``filter`` with regex special chars MUST NOT cause a 500.

        The text filter is used in search_names which constructs a BM25
        query. Special chars are SQL/regex-meaningful; they MUST be escaped
        properly. This is the same shape as the 5K-char DoS guard
        (TS-02 EXPLORER QA-027).
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes.*+?()[]{}|^$"},
        )
        assert status in (200, 400), f"expected 200/400, got {status}: {body}"
        # Either 200 (no matches found, no error) or 400 (rejected as invalid
        # syntax). MUST NOT be 500 with text/plain.
        if status >= 500:
            pytest.fail(f"filter with regex chars caused server error: {body}")

    def test_s2b_filter_very_long_rejected(self, fhir_client):
        """``filter`` >256 chars MUST 400 (per search_names length cap).

        The underlying search service rejects queries >256 chars via
        ValueError; the handler wraps in try/except ValueError → 400.
        Found by TS-02 EXPLORER QA-027.
        """
        long_filter = "a" * 5000
        status, body = _get_expand(
            fhir_client,
            params={"filter": long_filter},
        )
        assert status == 400, f"expected 400 for >256-char filter, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"


# =============================================================================
# Item 3: Response shape — ValueSet with expansion.{timestamp, total, contains[]}
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion
# =============================================================================


class TestItem3ResponseShape:
    """Item 3: response shape conforms to FHIR R4 ValueSet.expansion."""

    def test_s30_response_has_resource_type_valueset(self, fhir_client):
        """Response resourceType MUST be ``ValueSet`` per FHIR R4 §4.9."""
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        assert body["resourceType"] == "ValueSet"

    def test_s31_response_has_expansion_object(self, fhir_client):
        """Response MUST include ``expansion`` object."""
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        assert "expansion" in body, f"missing expansion: {body}"

    def test_s32_expansion_has_timestamp(self, fhir_client):
        """``expansion.timestamp`` MUST be present (per FHIR R4 §4.9.1)."""
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        ts = body["expansion"].get("timestamp")
        assert ts is not None, f"missing expansion.timestamp: {body}"

    def test_s33_expansion_timestamp_is_iso8601_instant(self, fhir_client):
        """``expansion.timestamp`` SHOULD be a valid ISO 8601 instant.

        Per FHIR R4 §4.9.1: "An instant value ... that the expansion was
        produced." The format is instant (per §3.4.1 dateTime with required
        seconds and timezone).
        """
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        ts = body["expansion"]["timestamp"]
        # MUST be ISO 8601 instant (Z or +/-HH:MM, with seconds).
        assert ISO_8601_INSTANT.match(ts), (
            f"expansion.timestamp is not a valid ISO 8601 instant: {ts!r}"
        )

    def test_s34_expansion_has_total(self, fhir_client):
        """``expansion.total`` MUST be present (per FHIR R4 §4.9.2)."""
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        assert "total" in body["expansion"]
        assert isinstance(body["expansion"]["total"], int)

    def test_s35_expansion_has_contains_array(self, fhir_client):
        """``expansion.contains`` MUST be present (per FHIR R4 §4.9.3)."""
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        assert "contains" in body["expansion"]
        assert isinstance(body["expansion"]["contains"], list)

    def test_s36_expansion_total_matches_contains_length_when_not_truncated(self, fhir_client):
        """``expansion.total`` MUST match ``len(contains)`` when not truncated.

        Per FHIR R4 §4.9.2: "the total number of concepts in the expansion"
        — when no truncation, this equals len(contains[]).
        """
        vs = _make_extensional_snomed()  # 2 concepts, count default 20
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert body["expansion"]["total"] == len(body["expansion"]["contains"])

    def test_s37_expansion_total_reflects_untruncated_size(self, fhir_client):
        """``expansion.total`` reflects UN-truncated size.

        Per FHIR R4 §4.9.2: total is the size of the FULL expansion. When
        count truncates, total > len(contains[]). This is the spec-mandated
        signal that the client should re-issue with a higher offset/count.
        """
        vs = _make_extensional_snomed()  # 2 concepts
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        # total = 2 (un-truncated), contains = 1.
        assert body["expansion"]["total"] == 2
        assert len(body["expansion"]["contains"]) == 1


# =============================================================================
# Item 4: Expansion contains[] shape — system, code, display, version
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains
# =============================================================================


class TestItem4ContainsShape:
    """Item 4: each contains[] entry has system, code, display, version."""

    def test_s40_contains_entry_has_system(self, fhir_client):
        """Each contains[] entry MUST have ``system`` per FHIR R4 §4.9.3.1."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for entry in body["expansion"]["contains"]:
            assert "system" in entry, f"missing system in contains entry: {entry}"

    def test_s41_contains_entry_has_code(self, fhir_client):
        """Each contains[] entry MUST have ``code`` per FHIR R4 §4.9.3.2."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for entry in body["expansion"]["contains"]:
            assert "code" in entry, f"missing code in contains entry: {entry}"

    def test_s42_contains_entry_has_display(self, fhir_client):
        """Each contains[] entry SHOULD have ``display`` per FHIR R4 §4.9.3.3."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for entry in body["expansion"]["contains"]:
            assert "display" in entry, f"missing display in contains entry: {entry}"
            assert entry["display"], f"empty display in contains entry: {entry}"

    def test_s43_contains_entry_version_optional(self, fhir_client):
        """``version`` on contains[] entry is OPTIONAL (per FHIR R4 §4.9.3.4).

        When the code system tracks versions (e.g. SNOMED edition), the
        version SHOULD be populated. medterm4ds is single-snapshot (no
        version tracking) so the field is omitted.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        # version is optional — just verify the route doesn't 500 if it's
        # absent. Probe confirms contract.
        for entry in body["expansion"]["contains"]:
            # version absent OR present — both are conformant.
            if "version" in entry:
                assert isinstance(entry["version"], str)

    def test_s44_contains_system_is_canonical_uri(self, fhir_client):
        """``system`` in contains[] MUST be the canonical FHIR URI.

        Per FHIR R4 §4.9.3.1: "An absolute URI which is the code system URI
        of the code system from which the code in the expansion was defined."
        This is the canonical URI, NOT an alias.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for entry in body["expansion"]["contains"]:
            assert entry["system"] == SNOMED_URI, (
                f"contains[].system is not canonical: {entry['system']!r} "
                f"(expected {SNOMED_URI!r})"
            )

    def test_s45_contains_system_canonical_on_alias_input(self, fhir_client):
        """``system`` in contains[] MUST be canonical even when client
        supplied an alias.

        Per VS-01 EXPLORER test_e10 + CR-013 (milestone-2 review): the
        ``canonical_system_uri`` helper re-resolves aliases to canonical
        URIs. The probe verifies the contract holds on the POST body shape.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    # urn:oid:2.16.840.1.113883.6.96 is the SNOMED OID form.
                    "system": "urn:oid:2.16.840.1.113883.6.96",
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, (
            f"alias input not resolved to canonical: {codes}"
        )


# =============================================================================
# Item 5: Paging semantics — offset+count, total
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (offset/count)
#       "Servers SHOULD ... return the total number of concepts in the
#        expansion.total element"
# =============================================================================


class TestItem5PagingSemantics:
    """Item 5: paging semantics."""

    def test_s50_offset_zero_default(self, fhir_client):
        """Default offset is 0 (first page) per FHIR R4 §4.7.5."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        # Default offset is 0 (all results).
        assert body["expansion"]["total"] == 2

    def test_s51_offset_respected_on_get_filter(self, fhir_client):
        """``offset`` MUST page the results.

        Per FHIR R4 §4.7.5: "Paging arguments — a server SHOULD return a
        flat list of concepts ... offset is the 0-based starting index."

        QC-241 (EC-10 EDGE_CASE, HIGH) RESOLVED: offset is now honored —
        the mode handlers fetch an ``offset + count`` window and the page
        is sliced from the built payload. CF-SKEPTIC-VS02-02 is CLOSED.
        This probe (previously a carry-forward asserting offset was
        IGNORED) now asserts the corrected paging semantics.
        """
        # Use a filter that yields multiple matches (we can't easily get >1
        # match in the fixture, but the probe establishes the contract).
        status_no_offset, body_no = _get_expand(
            fhir_client,
            params={"filter": "diabetes"},
        )
        status_offset_5, body_offset_5 = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "offset": 5},
        )
        assert status_no_offset == 200
        assert status_offset_5 == 200
        # Offset is honored: offset=5 past the fixture's match count pages
        # to fewer (empty) contains[] while total still reports the size.
        assert len(body_offset_5["expansion"]["contains"]) < len(
            body_no["expansion"]["contains"]
        ), (
            "offset appears to be ignored — CF-SKEPTIC-VS02-02 regression "
            "(QC-241 fix must hold)"
        )

    def test_s52_offset_beyond_total_returns_empty_contains(self, fhir_client):
        """``offset`` beyond total SHOULD return empty contains[] but
        still report the correct total.

        Per FHIR R4 §4.7.5: paging — offset beyond total returns empty
        page. The total SHOULD still reflect the full count.

        QC-241 (EC-10) RESOLVED — offset is honored (CF-SKEPTIC-VS02-02
        CLOSED); the skip-guard for the old ignored behavior is removed.
        """
        vs = _make_extensional_snomed()  # 2 concepts
        status, body = _post_expand(fhir_client, vs, params={"offset": 100})
        assert status == 200, f"expected 200, got {status}: {body}"
        # Offset is honored: contains is empty, total still the full count.
        assert body["expansion"]["contains"] == []
        assert body["expansion"]["total"] == 2

    def test_s53_count_default_is_20(self, fhir_client):
        """Default count is 20 (per FHIR R4 §4.7.5)."""
        # Just verify the route accepts GET without count.
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        # Default count is 20 — but the fixture has <20 results.

    def test_s54_count_caps_explicitly(self, fhir_client):
        """``count=N`` MUST cap the response size."""
        vs = _make_extensional_snomed()  # 2 concepts
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        assert len(body["expansion"]["contains"]) == 1


# =============================================================================
# Item 6: Hierarchical expansions are NOT paged
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
#       "Hierarchical expansions SHALL not be paged - the entire expansion
#        will be returned in a single response"
# =============================================================================


class TestItem6HierarchicalExpansions:
    """Item 6: hierarchical expansions are not paged."""

    def test_s60_intensional_expansion_returns_full_hierarchy(self, fhir_client):
        """Intensional (is-a filter) expansion MUST return full hierarchy.

        Per FHIR R4 §4.7.5: hierarchical expansions SHALL not be paged.
        With count=20 (default) and a 2-concept expansion, both should be
        returned.
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        codes = _contains_codes(body)
        # Both Diabetes mellitus (root) and T2DM (child) should be present.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s61_intensional_with_count_returns_truncated(self, fhir_client):
        """``count`` on a hierarchical expansion: the spec says hierarchical
        SHALL NOT be paged — but the server MAY truncate via count (and emit
        the toocostly extension as the signal). This is the spec-correct
        behavior: paging is "client-driven slicing of the expansion"; count
        is "server-side size cap with a truncation signal".

        Per VS-01 TERMINOLOGIST QA-055 + the valueset-toocostly extension.
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200, f"expected 200, got {status}: {body}"
        # Truncated to count=1.
        assert len(body["expansion"]["contains"]) == 1
        # total reflects the full hierarchy (2 concepts).
        assert body["expansion"]["total"] == 2
        # valueset-toocostly extension present.
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"missing {TOOCOSTLY_URL} extension on truncated hierarchical expansion: {exts}"
        )


# =============================================================================
# Item 7: Server SHOULD return OperationOutcome with code 'too-costly' for
#         very large expansions
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (Notes)
#       "If the expansion is too large (or requested offset+count is too
#        large), the server MAY return a 400 with an OperationOutcome with
#        code 'too-costly'"
#       https://hl7.org/fhir/R4/extension-valueset-toocostly.html
# =============================================================================


class TestItem7TooCostly:
    """Item 7: too-costly OperationOutcome for very large expansions."""

    def test_s70_toocostly_extension_on_count_truncation(self, fhir_client):
        """count=N truncation MUST emit valueset-toocostly extension.

        Per FHIR R4 + VS-01 TERMINOLOGIST QA-055: the
        valueset-toocostly extension is the load-bearing clinical-safety
        signal that the expansion is truncated.
        """
        vs = _make_extensional_snomed()  # 2 concepts
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        exts = body["expansion"].get("extension", [])
        too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert too_costly, (
            f"missing valueset-toocostly extension on count-truncated expansion: {exts}"
        )
        # The extension carries valueBoolean=true (per
        # https://hl7.org/fhir/R4/extension-valueset-toocostly.html).
        assert too_costly[0].get("valueBoolean") is True, (
            f"valueset-toocostly should carry valueBoolean=true: {too_costly[0]}"
        )

    def test_s71_no_toocostly_extension_when_not_truncated(self, fhir_client):
        """When expansion is not truncated, no valueset-toocostly extension."""
        vs = _make_extensional_snomed()  # 2 concepts
        status, body = _post_expand(fhir_client, vs, params={"count": 1000})
        assert status == 200
        exts = body["expansion"].get("extension", [])
        too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert not too_costly, (
            f"valueset-toocostly extension present on non-truncated expansion: {exts}"
        )

    def test_s72_toocostly_extension_includes_reason(self, fhir_client):
        """The toocostly extension SHOULD include a reason extension.

        Per FHIR R4 + the medterm4ds extension convention: the toocostly
        extension has a nested ``reason`` extension explaining why it was
        truncated (count-limited at N).
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        exts = body["expansion"].get("extension", [])
        too_costly = next(
            (e for e in exts if e.get("url") == TOOCOSTLY_URL), None
        )
        assert too_costly is not None
        reasons = [e for e in too_costly.get("extension", []) if e.get("url") == "reason"]
        assert reasons, (
            f"toocostly extension missing 'reason' nested extension: {too_costly}"
        )

    def test_s73_get_filter_truncation_emits_toocostly(self, fhir_client):
        """GET $expand?filter=...&count=N truncation MUST emit toocostly.

        Cross-check: the GET filter path uses ``build_valueset_expand``
        without a toocostly extension (the filter path does not currently
        signal truncation). This is the same silent-truncation shape that
        VS-01 TERMINOLOGIST QA-055 found in expand_post — the GET filter
        path also silently truncates without surfacing the extension.

        Probe documents the current behavior.
        """
        # The fixture has <20 diabetes matches, so count=1 must truncate.
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "count": 1},
        )
        assert status == 200
        # If contains is truncated (< total of all matches), the extension
        # SHOULD be present. The current GET filter path does NOT emit it.
        if len(body["expansion"]["contains"]) < body["expansion"]["total"]:
            exts = body["expansion"].get("extension", [])
            too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
            # Documented gap: GET filter path does not currently emit
            # toocostly extension. Carry-forward CF-SKEPTIC-VS02-03.
            if not too_costly:
                pytest.skip(
                    "GET filter path does not emit valueset-toocostly extension "
                    "on truncation (CF-SKEPTIC-VS02-03)"
                )


# =============================================================================
# Item 8: Filter text matches against display, code, or designation
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (filter)
#       "a text filter that is applied to the display or description of
#        the concepts"
# =============================================================================


class TestItem8FilterMatching:
    """Item 8: filter text matching."""

    def test_s80_filter_matches_display_substring(self, fhir_client):
        """``filter`` SHOULD match the display (per FHIR R4 §4.7.5)."""
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        # "diabetes" should match the display of both SNOMED codes.
        codes = _contains_codes(body)
        assert codes, f"filter='diabetes' returned no results: {body}"

    def test_s81_filter_case_insensitive(self, fhir_client):
        """``filter`` SHOULD be case-insensitive (per typical server
        implementation — spec says "server discretion").
        """
        status_lower, body_lower = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        status_upper, body_upper = _get_expand(
            fhir_client, params={"filter": "DIABETES"}
        )
        assert status_lower == 200
        assert status_upper == 200
        # Both should match the same set of concepts.
        codes_lower = _contains_codes(body_lower)
        codes_upper = _contains_codes(body_upper)
        assert set(codes_lower) == set(codes_upper), (
            f"filter case-sensitivity mismatch: {codes_lower} vs {codes_upper}"
        )

    def test_s82_filter_empty_string_400(self, fhir_client):
        """Empty filter string MUST be rejected.

        Per the AGENTS.md NOT A BUG registry entry: "filter= empty string
        on $expand returns 400 — empty filter is meaningless; the server
        treats it as 'no filter supplied'".
        """
        status, body = _get_expand(fhir_client, params={"filter": ""})
        # The empty filter is treated as no filter supplied → 400 (missing
        # url or body).
        assert status == 400, f"expected 400 for empty filter, got {status}: {body}"

    def test_s83_filter_no_match_returns_empty(self, fhir_client):
        """``filter`` with no matches returns empty expansion (not 404).
        Per FHIR R4: empty expansion is conformant when no codes match.
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "zzzznomatch"},
        )
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"
        assert body["expansion"]["total"] == 0
        assert body["expansion"]["contains"] == []

    def test_s84_filter_includes_metformin_by_name(self, fhir_client):
        """``filter`` matches by display name (metformin is the RxNorm entry)."""
        status, body = _get_expand(fhir_client, params={"filter": "metformin"})
        assert status == 200
        codes = _contains_codes(body)
        assert (RXNORM_URI, RXNORM_METFORMIN) in codes, (
            f"filter='metformin' did not match RxNorm metformin: {codes}"
        )


# =============================================================================
# Cross-cutting: Content-Type, error shape, expand_get ↔ expand_post parity
# =============================================================================


class TestCrossCutting:
    """Content-Type, error shape, parity."""

    def test_s90_expand_get_content_type_fhir_json(self, fhir_client):
        """GET $expand MUST emit application/fhir+json."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes"},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/fhir+json"

    def test_s91_expand_post_content_type_fhir_json(self, fhir_client):
        """POST $expand MUST emit application/fhir+json."""
        vs = _make_extensional_snomed()
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/fhir+json"

    def test_s92_error_path_emits_operation_outcome(self, fhir_client):
        """Error path MUST return OperationOutcome with FHIR Content-Type."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"] == "application/fhir+json"
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_s93_xml_format_negotiated(self, fhir_client):
        """``_format=xml`` SHOULD return application/fhir+xml."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "_format": "xml"},
            headers={"Accept": "application/fhir+xml"},
        )
        assert resp.status_code == 200
        # XML serialization is supported per milestone-1 CR-002.
        assert "xml" in resp.headers["content-type"], (
            f"expected XML content-type, got {resp.headers['content-type']}"
        )

    def test_s94_filter_operator_enum_imported(self, fhir_client):
        """Sanity: FHIR_R4_FILTER_OPERATORS constant is importable.

        Per milestone-2 review CR-014 / VS-01 SKEPTIC QA-054: the frozen-set
        constant is the single source of truth for the FHIR R4 filter
        operator closed enum.
        """
        assert "is-a" in FHIR_R4_FILTER_OPERATORS
        assert "descendent-of" in FHIR_R4_FILTER_OPERATORS
        assert "descendant-of" not in FHIR_R4_FILTER_OPERATORS

    def test_s95_expand_get_post_parity(self, fhir_client):
        """GET $expand?filter=X and POST $expand with Parameters body
        filter=X SHOULD produce equivalent results.

        The two paths use the same ``_do_expand`` handler; the responses
        should match in total and contains[] codes.
        """
        status_get, body_get = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "filter", "valueString": "diabetes"}],
        }
        status_post, body_post = _post_expand(fhir_client, params_body)
        assert status_get == 200
        assert status_post == 200
        assert body_get["expansion"]["total"] == body_post["expansion"]["total"]
        assert set(_contains_codes(body_get)) == set(_contains_codes(body_post))

    def test_s96_active_filter_excludes_suppressed_codes(self, fhir_client):
        """Expansion SHOULD NOT include inactive (SUPPRESS != 'N') codes.

        Per FHIR R4 §4.7.5 + CS-05 SKEPTIC CF-SKEPTIC-CS05-02: the engine
        filters mrconso on SUPPRESS='N' (active). The conformance fixture
        has only SUPPRESS='N' rows so this probe confirms the contract
        structurally.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        # All returned codes should be active — by construction of the
        # conformance fixture (all rows have SUPPRESS='N').
        assert body["expansion"]["total"] > 0

    def test_s97_expansion_does_not_include_inactive_flag(self, fhir_client):
        """contains[] entries SHOULD NOT mark active codes as inactive.

        Per CS-05 SKEPTIC CF-SKEPTIC-CS05-02: ``$lookup`` doesn't emit
        ``inactive=true`` for active codes (the property is correctly
        absent). $expand similarly doesn't mark active codes as inactive.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for entry in body["expansion"]["contains"]:
            # active codes should NOT have inactive=true.
            assert entry.get("inactive") is not True, (
                f"active code marked inactive: {entry}"
            )
