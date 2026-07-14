"""VS-03 EXPLORER: ValueSet $expand — Advanced (lateral thinking).

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Filter Operator enum: https://hl7.org/fhir/R4/valueset-concept-operator.html
Parameters resource: https://hl7.org/fhir/R4/parameters.html

EXPLORER lens: lateral thinking across the valueSet/$expand surface. The probes
do not repeat SKEPTIC's spec-citation-then-probe work; they probe adjacent
behaviors the spec leaves implicit OR that come from combining features the
prior personalities tested in isolation.

Carry-forwards reconfirmed (the load-bearing contracts):

  - CF-SKEPTIC-VS01-01: 7 of 9 filter operators silently dropped.
  - CF-HISTORIAN-VS02-01: BFS cap on total still truncated (HIGH, deferred).
  - CF-HISTORIAN-VS02-02: implicit path doesn't use canonical_system_uri.

Lenses per chunk assignment:

  - Inline valueSet POST combinations (bare / Parameters-with-valueSet / both
    valueSet AND url).
  - Filter operator combinations (multiple is-a, is-a + descendent-of, is-a +
    concept[]).
  - Date parameter variations (past, future, malformed, timezone).
  - Explicit concept list edge cases (large, duplicates, cross-system, special
    chars).
  - Cross-system is-a.
  - Hierarchical expansion paging (is-a + small count).
  - active filter.
  - Nested Parameters (adversarial).
  - Cross-resource POST routes (POST /fhir/ValueSet/{id}/$expand with Parameters
    body).
  - 4-shape Content-Type closure on $expand (verify VS-02 EXPLORER closure
    still holds).
  - GET↔POST parity on $expand.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"


# =============================================================================
# Helpers (mirror SKEPTIC file's helpers — same shape as test_vs03_skeptic.py)
# =============================================================================

def _post_expand(fhir_client, body: dict, *, params: dict | None = None,
                 headers: dict | None = None) -> tuple[int, dict, str]:
    """POST a body to /fhir/ValueSet/$expand.

    Returns (status_code, body_json, content_type).
    """
    merged_headers = {"Accept": "application/fhir+json"}
    if headers:
        merged_headers.update(headers)
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=body,
        params=params or {},
        headers=merged_headers,
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed, resp.headers.get("content-type", "")


def _get_expand(fhir_client, *, params: dict, headers: dict | None = None) -> tuple[int, dict, str]:
    """GET /fhir/ValueSet/$expand with query params."""
    merged_headers = {"Accept": "application/fhir+json"}
    if headers:
        merged_headers.update(headers)
    resp = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=params,
        headers=merged_headers,
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed, resp.headers.get("content-type", "")


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


def _wrap_in_parameters(value_set: dict, extra_params: list | None = None) -> dict:
    """Wrap an inline ValueSet in a Parameters resource per FHIR R4 §4.7.5."""
    parameter = [{"name": "valueSet", "resource": value_set}]
    if extra_params:
        parameter.extend(extra_params)
    return {"resourceType": "Parameters", "parameter": parameter}


# =============================================================================
# Lens 1: 4-shape Content-Type closure on $expand (CF-EXPLORER-CS02-01 partial)
# Verify VS-01 EXPLORER + VS-02 EXPLORER closure still holds AND the QA-059
# fix path emits conformant Content-Type.
# =============================================================================


class TestExpand4ShapeContentType:
    """4-shape Content-Type probe family for ValueSet/$expand.

    Per CF-EXPLORER-CS02-01: each chunk's EXPLORER iteration closes its own
    portion of the family. VS-02 EXPLORER closed ValueSet/$expand (test_e100..
    e140). VS-03 EXPLORER re-verifies the QA-059 fix path emits conformant
    Content-Type on every shape.
    """

    def test_e10_bare_valueset_body_content_type(self, fhir_client):
        """Shape 1: bare ValueSet body — Content-Type MUST be application/fhir+json."""
        vs = _make_extensional_snomed()
        status, body, ct = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        assert "application/fhir+json" in ct, f"Content-Type={ct!r}"
        assert body["resourceType"] == "ValueSet"

    def test_e11_parameters_with_valueset_content_type(self, fhir_client):
        """Shape 2: Parameters-with-valueSet body (QA-059 fix path) — Content-Type MUST be application/fhir+json."""
        params_body = _wrap_in_parameters(_make_extensional_snomed())
        status, body, ct = _post_expand(fhir_client, params_body)
        assert status == 200, f"status={status} body={body}"
        assert "application/fhir+json" in ct, f"Content-Type={ct!r}"
        assert body["resourceType"] == "ValueSet"

    def test_e12_parameters_with_count_and_valueset_content_type(self, fhir_client):
        """Shape 3: Parameters-with-valueSet + count inline — Content-Type MUST be application/fhir+json."""
        params_body = _wrap_in_parameters(
            _make_extensional_snomed(),
            extra_params=[{"name": "count", "valueInteger": 1}],
        )
        status, body, ct = _post_expand(fhir_client, params_body)
        assert status == 200, f"status={status} body={body}"
        assert "application/fhir+json" in ct, f"Content-Type={ct!r}"
        assert body["resourceType"] == "ValueSet"

    def test_e13_error_path_content_type(self, fhir_client):
        """Shape 4: error path (no usable input) — Content-Type MUST be application/fhir+json + OperationOutcome."""
        # Parameters body with no valueSet, no url, no filter → 400 path.
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "count", "valueInteger": 20}],
        }
        status, body, ct = _post_expand(fhir_client, params_body)
        assert status in (400, 422), f"status={status} body={body}"
        assert "application/fhir+json" in ct, f"Content-Type={ct!r}"
        assert body["resourceType"] == "OperationOutcome"


class TestExpand4ShapeContentTypeXML:
    """4-shape Content-Type probe family for ValueSet/$expand with _format=xml.

    Per CR-002 (XML serializer boolean special-case at xml.py:48-62): every
    wire-format serializer must be XML-probed per route. The $expand response
    includes booleans? No — but it does include integers (total, offset) and
    the response should still serialize cleanly to XML when negotiated.
    """

    def test_e20_bare_valueset_body_xml(self, fhir_client):
        """Shape 1 XML: bare ValueSet body — Content-Type MUST be application/fhir+xml."""
        vs = _make_extensional_snomed()
        status, body, ct = _post_expand(fhir_client, vs, headers={"Accept": "application/fhir+xml"})
        assert status == 200, f"status={status} body={body[:200]}"
        assert "application/fhir+xml" in ct, f"Content-Type={ct!r}"

    def test_e21_parameters_with_valueset_xml(self, fhir_client):
        """Shape 2 XML: Parameters-with-valueSet (QA-059 fix path) — Content-Type MUST be application/fhir+xml."""
        params_body = _wrap_in_parameters(_make_extensional_snomed())
        status, body, ct = _post_expand(fhir_client, params_body, headers={"Accept": "application/fhir+xml"})
        assert status == 200, f"status={status} body={body[:200]}"
        assert "application/fhir+xml" in ct, f"Content-Type={ct!r}"

    def test_e22_format_xml_param_precedence(self, fhir_client):
        """_format=xml overrides Accept: application/fhir+json."""
        vs = _make_extensional_snomed()
        # Send Accept=json but _format=xml — XML should win.
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            params={"_format": "xml"},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200, f"status={resp.status_code} body={resp.text[:200]}"
        assert "application/fhir+xml" in resp.headers.get("content-type", ""), (
            f"Content-Type={resp.headers.get('content-type', '')!r}"
        )

    def test_e23_error_path_xml(self, fhir_client):
        """Shape 4 XML: error path from ``_fhir_error_response`` (count validation).

        NOTE: this probe asserts XML-on-error for the count-validation path
        which goes through ``_fhir_error_response`` (the Accept-aware variant).
        The no-input 400 path inside ``_do_expand`` (called via ``_run_db``)
        uses the JSON-only ``_fhir_error`` helper — XML-on-error is out of
        scope there per the ``_fhir_error_response`` docstring disclaimer
        ("Sites without ``request`` ... keep using ``_fhir_error`` — XML-on-
        error is out of scope for those paths today and threading ``request``
        through them is significant churn"). The count-validation path runs
        in the handler scope (has ``request``) so it SHOULD honor XML.
        """
        # Use a bad count value to trigger the count-validation 400 path
        # which goes through _fhir_error_response (Accept-aware).
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "count", "valueInteger": 0},  # invalid (< 1)
            ],
        }
        status, body_text, ct = _post_expand(
            fhir_client, params_body, headers={"Accept": "application/fhir+xml"}
        )
        assert status == 400, f"status={status} body={body_text}"
        # The count-validation path goes through _fhir_error_response which
        # IS Accept-aware → XML should be honored.
        assert "application/fhir+xml" in ct, (
            f"Content-Type={ct!r} — _fhir_error_response SHOULD honor XML"
        )


# =============================================================================
# Lens 2: Inline valueSet POST combinations
# =============================================================================


class TestInlineValueSetPostCombinations:
    """Inline valueSet POST combinations.

    Per the chunk assignment:
      - POST body with Parameters wrapper containing valueSet (QA-059 fixed)
      - POST body with bare ValueSet (no Parameters wrapper) — already worked
      - POST body with BOTH valueSet AND url (conflicting — server behavior?)
    """

    def test_e30_bare_valueset_with_count_param(self, fhir_client):
        """Bare ValueSet body + count query param — count MUST be honored."""
        vs = _make_extensional_snomed()
        status, body, _ = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200, f"status={status} body={body}"
        assert len(body["expansion"]["contains"]) <= 1
        assert body["expansion"]["total"] == 2
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts)

    def test_e31_parameters_valueset_and_url_conflicting(self, fhir_client):
        """Parameters body with BOTH valueSet AND url — server behavior.

        Per FHIR R4 §4.7.5, both are In parameters. The spec does not say
        which takes precedence when both are present. The conformant path is
        to NOT crash; either honor valueSet OR honor url OR 400 (no specific
        requirement).
        """
        nested_vs = _make_extensional_snomed()
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": nested_vs},
                {"name": "url", "valueUri": "http://snomed.info/sct?fhir_vs"},
            ],
        }
        status, body, _ = _post_expand(fhir_client, params_body)
        # MUST NOT crash. Any of: 200 with inline VS expansion, 200 with url
        # expansion, 400 with OperationOutcome.
        assert status < 500, (
            f"server crash on conflicting valueSet+url: {status} {body}"
        )
        if status == 200:
            assert body["resourceType"] == "ValueSet"

    def test_e32_bare_valueset_and_url_param_conflicting(self, fhir_client):
        """Bare ValueSet body + url query param — same as test_e31 but query-param form."""
        vs = _make_extensional_snomed()
        status, body, _ = _post_expand(
            fhir_client, vs, params={"url": "http://snomed.info/sct?fhir_vs"}
        )
        # MUST NOT crash.
        assert status < 500, (
            f"server crash on bare VS + url: {status} {body}"
        )
        if status == 200:
            assert body["resourceType"] == "ValueSet"

    def test_e33_parameters_valueset_with_filter_param(self, fhir_client):
        """Parameters body with valueSet AND filter — server behavior.

        Per FHIR R4 §4.7.5: when both valueSet and filter are supplied, the
        server SHOULD expand the inline ValueSet AND apply the filter to the
        expansion. This is a valid combined use case.
        """
        nested_vs = _make_extensional_snomed()
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": nested_vs},
                {"name": "filter", "valueString": "diabetes"},
            ],
        }
        status, body, _ = _post_expand(fhir_client, params_body)
        # MUST NOT crash. Either honor valueSet (filter ignored) OR apply
        # filter to the inline VS expansion.
        assert status < 500, (
            f"server crash on valueSet+filter: {status} {body}"
        )
        if status == 200:
            assert body["resourceType"] == "ValueSet"


# =============================================================================
# Lens 3: Filter operator combinations (multiple filters, is-a + descendent-of)
# =============================================================================


class TestFilterOperatorCombinations:
    """Filter operator combinations per chunk assignment."""

    def test_e40_is_a_plus_descendent_of_same_root(self, fhir_client):
        """is-a + descendent-of on the SAME root — combined result.

        Per FHIR R4 §4.9.5: when multiple filters are applied to the same
        property in an include block, they are ANDed. is-a includes root;
        descendent-of excludes root. AND → descendent-of wins (root excluded
        because it's not in descendent-of).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS},
                        {"property": "concept", "op": "descendent-of", "value": SNOMED_DIABETES_MELLITUS},
                    ]
                }]
            }
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        # The implementation processes filters sequentially (is-a appends root
        # + descendants, descendent-of appends descendants only). The result
        # is the UNION of both filters, not strict AND. This is a known
        # implementation choice (no spec-mandated AND semantics in the
        # current implementation). Pinned as current behavior.
        # Spec-correct AND would exclude root. Current behavior INCLUDES root.
        # Documenting this as a load-bearing pin on the current behavior.
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"descendant missing from is-a+descendent-of: {codes}"
        )

    def test_e41_is_a_plus_extensional_concept_list(self, fhir_client):
        """is-a filter + concept[] in the same include block.

        Per FHIR R4 §4.9.4 + §4.9.5: when both concept[] and filter[] are
        present in the same include block, both contribute to the expansion
        (union, not intersection). The concept[] list is added verbatim; the
        filter[] expands its hierarchy walk.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"}
                    ],
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                    ],
                }]
            }
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        # Both the explicit concept AND the is-a expansion MUST appear.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"is-a descendant missing from combined include: {codes}"
        )

    def test_e42_multiple_includes_combined(self, fhir_client):
        """Two include blocks (different systems) — both contribute to expansion."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_DIABETES_MELLITUS, "display": "DM"}]
                    },
                    {
                        "system": ICD10CM_URI,
                        "concept": [{"code": ICD10CM_T2DM, "display": "T2DM"}]
                    },
                ]
            }
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (ICD10CM_URI, ICD10CM_T2DM) in codes

    def test_e43_is_a_on_rxnorm_root(self, fhir_client):
        """is-a filter on a code that has NO descendants in the fixture.

        RXNORM 860975 has no mrrel rows in the fixture. is-a MUST return
        just the root (root always included per spec).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": RXNORM_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": RXNORM_METFORMIN}
                    ],
                }]
            }
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        assert codes == [(RXNORM_URI, RXNORM_METFORMIN)], (
            f"is-a on leaf should return just leaf: {codes}"
        )


# =============================================================================
# Lens 4: Date parameter variations
# Per chunk assignment: past, future, malformed, timezone.
# =============================================================================


class TestDateParameterVariations:
    """Date parameter variations.

    Per FHIR R4 §4.7.5 In Parameters `date`: "The date for which the
    expansion is to be performed." medterm4ds is single-snapshot; the date
    is accepted but ignored (INTENDED — same shape as version/offset).
    """

    def test_e50_date_with_timezone(self, fhir_client):
        """``date`` with timezone offset (e.g. 2025-06-15T10:30:00+02:00)."""
        status, body, _ = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "2025-06-15T10:30:00+02:00"},
        )
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"

    def test_e51_date_year_only(self, fhir_client):
        """``date`` as year only (partial dateTime per FHIR R4 §3.4.1).

        Per FHIR R4 §3.4.1 dateTime regex: year, year-month, or full date
        are all valid (partial precision is allowed).
        """
        status, body, _ = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "2025"},
        )
        # Acceptable: 200 (accepted, ignored) OR 400 (rejected as too-coarse).
        # MUST NOT be 500.
        assert status < 500, (
            f"server crash on year-only date: {status} {body}"
        )

    def test_e52_date_with_z_timezone(self, fhir_client):
        """``date`` with Z (UTC) timezone."""
        status, body, _ = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "2025-06-15T10:30:00Z"},
        )
        assert status == 200, f"status={status} body={body}"

    def test_e53_date_in_parameters_body(self, fhir_client):
        """``date`` in POST Parameters body alongside valueSet."""
        params_body = _wrap_in_parameters(
            _make_extensional_snomed(),
            extra_params=[{"name": "date", "valueDateTime": "2020-01-01"}],
        )
        status, body, _ = _post_expand(fhir_client, params_body)
        # MUST NOT crash. Date should be accepted (ignored for evaluation).
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"

    def test_e54_date_far_future(self, fhir_client):
        """``date`` far in the future (year 2099)."""
        status, body, _ = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "2099-12-31T23:59:59Z"},
        )
        assert status == 200, f"status={status} body={body}"

    def test_e55_date_far_past(self, fhir_client):
        """``date`` far in the past (year 2000)."""
        status, body, _ = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "2000-01-01"},
        )
        assert status == 200, f"status={status} body={body}"


# =============================================================================
# Lens 5: Explicit concept list edge cases
# =============================================================================


class TestConceptListEdgeCases:
    """Explicit concept list edge cases per chunk assignment."""

    def test_e60_large_concept_list_100_plus(self, fhir_client):
        """Concept list with 100+ entries — MUST not crash, MUST dedupe."""
        # Generate 100 synthetic codes (all nonexistent — should be included
        # with empty display).
        concepts = [
            {"code": f"SYNTH-{i:04d}", "display": f"Synthetic concept {i}"}
            for i in range(100)
        ]
        # Add the real SNOMED codes for sanity.
        concepts.extend([
            {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
            {"code": SNOMED_T2DM, "display": "T2DM"},
        ])
        vs = _make_extensional_snomed(concepts=concepts)
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        # The 100+ entries plus the 2 real codes; but truncated at default
        # count=20. Verify total reflects un-truncated size (102).
        # NOTE: count default is 20; contains <= 20.
        assert len(body["expansion"]["contains"]) <= 20
        # Total reflects un-truncated size (102 concepts in list).
        # Per CF-HISTORIAN-VS02-01, BFS-cap doesn't apply here (no filter
        # walk), so the intensional path is the explicit list path that
        # computes total = len(deduped) directly.
        assert body["expansion"]["total"] == 102, (
            f"total mismatch: {body['expansion']['total']}"
        )

    def test_e61_duplicate_codes_in_list(self, fhir_client):
        """Duplicate codes in the concept list — MUST be deduplicated in expansion."""
        vs = _make_extensional_snomed(concepts=[
            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"},
            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus (duplicate)"},
            {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"},
        ])
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        # Deduplication is structural in _expand_intensional (seen set).
        unique = list(set(codes))
        assert len(codes) == len(unique), (
            f"duplicates NOT removed: {codes}"
        )

    def test_e62_codes_from_different_systems(self, fhir_client):
        """Codes from different systems in the SAME concept[] — spec violation."""
        # Per FHIR R4 §4.9.4: concept[] inside a single include block belongs
        # to the include[].system. Mixing systems is a client error.
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
                        # ICD-10-CM code in a SNOMED include block — wrong.
                        {"code": ICD10CM_T2DM, "display": "T2DM"},
                    ]
                }]
            }
        }
        status, body, _ = _post_expand(fhir_client, vs)
        # MUST NOT crash. The implementation may include the cross-system
        # code (with the SNOMED system URI — wrong but not crash) OR exclude
        # it OR 400. The spec doesn't mandate strict validation.
        assert status < 500, (
            f"server crash on cross-system concept[]: {status} {body}"
        )
        if status == 200:
            # The cross-system code's display will be empty (no SNOMED code
            # with value "E11"); the SNOMED code resolves normally.
            codes = _contains_codes(body)
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_e63_codes_with_special_characters(self, fhir_client):
        """Concept list with codes containing special characters."""
        # Codes with special characters (per chunk assignment: "codes with
        # special characters"). The implementation MUST handle them.
        concepts = [
            {"code": "code-with-dashes", "display": "Dash code"},
            {"code": "code.with.dots", "display": "Dot code"},
            {"code": "code/with/slashes", "display": "Slash code"},
            {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
        ]
        vs = _make_extensional_snomed(concepts=concepts)
        status, body, _ = _post_expand(fhir_client, vs)
        assert status < 500, (
            f"server crash on special-char codes: {status} {body}"
        )
        if status == 200:
            codes = _contains_codes(body)
            # All 4 should be in the expansion.
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
            assert (SNOMED_URI, "code-with-dashes") in codes


# =============================================================================
# Lens 6: Cross-system is-a filter
# =============================================================================


class TestCrossSystemIsA:
    """Cross-system is-a filter per chunk assignment.

    Per the chunk: "is-a filter on a code in one system, but compose.include
    has a different system". Per FHIR R4, this is a client error — the
    filter.value MUST be a code in the include[].system.
    """

    def test_e70_is_a_filter_value_from_different_system(self, fhir_client):
        """is-a filter with value from a DIFFERENT system than include.system.

        Per FHIR R4 §4.9.5: the filter.value SHOULD be a code in the
        include[].system. Cross-system is-a is a client error. The
        implementation MUST NOT crash; it MAY return an empty expansion
        (the root code is not found in the specified system).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    # Include is SNOMED but filter value is ICD-10-CM.
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": ICD10CM_T2DM}
                    ],
                }]
            }
        }
        status, body, _ = _post_expand(fhir_client, vs)
        # MUST NOT crash. The root code "E11" is not in SNOMED — the is-a
        # walk finds no root and no descendants. Returns empty contains
        # (or root included but root not found).
        assert status < 500, (
            f"server crash on cross-system is-a: {status} {body}"
        )
        if status == 200:
            # The implementation looks up the root code via get_code_infos
            # in the SNOMED source. E11 is not in SNOMED → root not added.
            # Descendants walk finds nothing. Result: empty contains.
            codes = _contains_codes(body)
            # The is-a root is NOT in the SNOMED source, so the BFS walk
            # finds no descendants. The result is empty contains (or just
            # the root, which is then excluded because it's not in SNOMED).
            # Pinned as current behavior.
            for s, c in codes:
                assert s == SNOMED_URI, (
                    f"unexpected system in cross-system is-a: {codes}"
                )


# =============================================================================
# Lens 7: Hierarchical expansion paging
# is-a with large result set + small count — paging behavior
# =============================================================================


class TestHierarchicalExpansionPaging:
    """Hierarchical expansion paging per chunk assignment."""

    def test_e80_is_a_with_count_1_truncates(self, fhir_client):
        """is-a with count=1 MUST truncate AND emit toocostly.

        Per VS-02 SKEPTIC QA-057: when the is-a expansion is truncated by
        count, the toocostly extension MUST be present.
        """
        vs = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        status, body, _ = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200, f"status={status} body={body}"
        # contains truncated to 1.
        assert len(body["expansion"]["contains"]) == 1
        # toocostly present.
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"missing toocostly extension: {exts}"
        )

    def test_e81_is_a_with_large_count(self, fhir_client):
        """is-a with count=1000 — MUST include all descendants without truncation."""
        vs = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        status, body, _ = _post_expand(fhir_client, vs, params={"count": 1000})
        assert status == 200, f"status={status} body={body}"
        # 2 results in fixture (root + 1 descendant).
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes
        # No truncation.
        exts = body["expansion"].get("extension", [])
        assert not any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"unexpected toocostly: {exts}"
        )

    def test_e82_offset_param_accepted_no_crash(self, fhir_client):
        """offset parameter MUST be accepted without crash (CF-SKEPTIC-VS02-02).

        Per AGENTS.md NOT A BUG registry: offset is accepted but ignored
        today (paging deferred). The probe pins the no-crash contract.
        """
        vs = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        status, body, _ = _post_expand(fhir_client, vs, params={"offset": 1})
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"


# =============================================================================
# Lens 8: active filter (SNOMED-specific property)
# =============================================================================


class TestActiveFilter:
    """``active`` filter — spec-listed but not implemented today.

    Per FHIR R4 §4.9.5: "CodeSystem-defined properties" can be filtered.
    SNOMED has an `inactive` property; some code systems have an `active`
    property. The implementation only honors `property="concept"` today
    (per AGENTS.md Known Fragile Areas). This probe documents the
    silent-drop behavior.
    """

    def test_e90_filter_property_active_silently_dropped(self, fhir_client):
        """Filter with property=active — silently dropped today."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "active", "op": "=", "value": "true"}
                    ],
                }]
            }
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        # property="active" is silently dropped (current behavior).
        assert codes == [], (
            f"property=active not silently dropped: {codes}"
        )

    def test_e91_filter_property_inactive_silently_dropped(self, fhir_client):
        """Filter with property=inactive — silently dropped today."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "inactive", "op": "=", "value": "false"}
                    ],
                }]
            }
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        # property="inactive" is silently dropped.
        assert codes == [], (
            f"property=inactive not silently dropped: {codes}"
        )


# =============================================================================
# Lens 9: Nested Parameters (adversarial)
# =============================================================================


class TestNestedParametersAdversarial:
    """Nested Parameters — adversarial shapes that MUST NOT crash.

    Per chunk assignment: "Parameters body with nested valueSet containing
    nested Parameters". This is an adversarial shape — the spec doesn't
    mandate it, but the server MUST NOT crash.
    """

    def test_e100_valueSet_with_parameters_resourcetype(self, fhir_client):
        """Nested resource has resourceType=Parameters (not ValueSet) — graceful."""
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": {
                        # Wrong resourceType — should be ValueSet.
                        "resourceType": "Parameters",
                        "parameter": [{"name": "nested", "valueString": "value"}],
                    }
                }
            ],
        }
        status, body, _ = _post_expand(fhir_client, params_body)
        # MUST be 400/422 (the helper returns None for wrong resourceType
        # and the caller falls through to no-url 400 path). MUST NOT be 500.
        assert status in (400, 422), f"status={status} body={body}"
        assert body["resourceType"] == "OperationOutcome"

    def test_e101_valueSet_with_no_resourcetype(self, fhir_client):
        """Nested resource has NO resourceType — graceful."""
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": {
                        # No resourceType.
                        "compose": {"include": [{"system": SNOMED_URI}]}
                    }
                }
            ],
        }
        status, body, _ = _post_expand(fhir_client, params_body)
        # MUST be 400/422. MUST NOT be 500.
        assert status in (400, 422), f"status={status} body={body}"
        assert body["resourceType"] == "OperationOutcome"

    def test_e102_valueSet_resource_is_string(self, fhir_client):
        """Nested resource is a STRING (not a dict) — graceful."""
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": "not-a-resource"}
            ],
        }
        status, body, _ = _post_expand(fhir_client, params_body)
        # MUST be 400/422. MUST NOT be 500.
        assert status in (400, 422), f"status={status} body={body}"
        assert body["resourceType"] == "OperationOutcome"

    def test_e103_valueSet_resource_is_list(self, fhir_client):
        """Nested resource is a LIST (not a dict) — graceful."""
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": [{"fake": "entry"}]}
            ],
        }
        status, body, _ = _post_expand(fhir_client, params_body)
        # MUST be 400/422. MUST NOT be 500.
        assert status in (400, 422), f"status={status} body={body}"
        assert body["resourceType"] == "OperationOutcome"

    def test_e104_valueSet_resource_is_null(self, fhir_client):
        """Nested resource is NULL — graceful."""
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": None}
            ],
        }
        status, body, _ = _post_expand(fhir_client, params_body)
        # MUST be 400/422. MUST NOT be 500.
        assert status in (400, 422), f"status={status} body={body}"
        assert body["resourceType"] == "OperationOutcome"

    def test_e105_valueSet_resource_is_integer(self, fhir_client):
        """Nested resource is an INTEGER — graceful."""
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": 42}
            ],
        }
        status, body, _ = _post_expand(fhir_client, params_body)
        # MUST be 400/422. MUST NOT be 500.
        assert status in (400, 422), f"status={status} body={body}"
        assert body["resourceType"] == "OperationOutcome"


# =============================================================================
# Lens 10: Cross-resource POST routes
# POST /fhir/ValueSet/{id}/$expand with Parameters body
# =============================================================================


class TestCrossResourcePostRoutes:
    """Cross-resource POST routes per chunk assignment.

    Per FHIR R4 §3.1.0.1.1: operations MAY be invoked via POST on either the
    type or a resource instance. POST /fhir/ValueSet/{id}/$expand with a
    Parameters body is a valid invocation form.
    """

    def test_e110_post_instance_expand_returns_404_with_fhir_content_type(self, fhir_client):
        """POST /fhir/ValueSet/{id}/$expand with Parameters body — 404 FHIR OperationOutcome.

        Per TS-02 EXPLORER QA-024: instance-level POST routes MUST exist so
        the framework default 405 doesn't shadow them. The route returns 404
        (no persisted ValueSet) with a FHIR OperationOutcome body.
        """
        params_body = _wrap_in_parameters(_make_extensional_snomed())
        resp = fhir_client.post(
            "/fhir/ValueSet/some-id/$expand",
            json=params_body,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 404, f"status={resp.status_code}"
        assert "application/fhir+json" in resp.headers.get("content-type", "")
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "not-found"

    def test_e111_post_instance_expand_with_bare_valueset(self, fhir_client):
        """POST /fhir/ValueSet/{id}/$expand with bare ValueSet — 404 FHIR OperationOutcome."""
        vs = _make_extensional_snomed()
        resp = fhir_client.post(
            "/fhir/ValueSet/some-id/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 404
        assert "application/fhir+json" in resp.headers.get("content-type", "")
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_e112_post_instance_expand_xml_content_type(self, fhir_client):
        """POST /fhir/ValueSet/{id}/$expand with _format=xml — 404 in XML."""
        params_body = _wrap_in_parameters(_make_extensional_snomed())
        resp = fhir_client.post(
            "/fhir/ValueSet/some-id/$expand",
            json=params_body,
            params={"_format": "xml"},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 404
        assert "application/fhir+xml" in resp.headers.get("content-type", "")


# =============================================================================
# Lens 11: GET↔POST parity on $expand
# =============================================================================


class TestGetPostParity:
    """GET↔POST parity on $expand.

    Per FHIR R4 §3.1.0.1.1: the same operation SHOULD produce the same
    result whether invoked via GET (params in query) or POST (params in
    body). The $expand filter mode is testable both ways.
    """

    def test_e120_filter_get_vs_post_parity(self, fhir_client):
        """GET ?filter=diabetes vs POST Parameters filter=diabetes — same codes."""
        # GET
        status_get, body_get, _ = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status_get == 200
        # POST
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "filter", "valueString": "diabetes"}],
        }
        status_post, body_post, _ = _post_expand(fhir_client, params_body)
        assert status_post == 200
        # Same codes (same underlying search_names call).
        codes_get = set(_contains_codes(body_get))
        codes_post = set(_contains_codes(body_post))
        assert codes_get == codes_post, (
            f"GET vs POST filter mismatch: get={codes_get} post={codes_post}"
        )

    def test_e121_count_get_vs_post_parity(self, fhir_client):
        """count=1 on GET vs POST — same truncation behavior.

        NOTE: per CF-SKEPTIC-VS02-03 (GET filter mode missing toocostly
        extension on truncation — DEFERRED), the GET filter path with
        count=1 truncates WITHOUT emitting the valueset-toocostly extension.
        The probe documents the current behavior: both paths truncate, but
        neither emits toocostly on the filter mode (the gap is shared by
        both invocation paths because they share the same ``_do_expand``
        filter-mode code path).
        """
        # GET
        status_get, body_get, _ = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 1}
        )
        assert status_get == 200
        # POST with count in body
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "count", "valueInteger": 1},
            ],
        }
        status_post, body_post, _ = _post_expand(fhir_client, params_body)
        assert status_post == 200
        # Both truncated to <= 1.
        assert len(body_get["expansion"]["contains"]) <= 1
        assert len(body_post["expansion"]["contains"]) <= 1
        # CF-SKEPTIC-VS02-03: filter mode does NOT emit toocostly today
        # (the gap is shared by GET and POST because they share the same
        # _do_expand filter-mode code path).
        for label, body in [("GET", body_get), ("POST", body_post)]:
            exts = body["expansion"].get("extension", [])
            # Pinning current CF-SKEPTIC-VS02-03 behavior: no toocostly on
            # filter mode truncation. When the CF is closed, this assertion
            # MUST be updated to assert toocostly IS present.
            assert not any(e.get("url") == TOOCOSTLY_URL for e in exts), (
                f"{label} unexpectedly has toocostly — CF-SKEPTIC-VS02-03 "
                f"may be closed: update the probe to assert presence."
            )


# =============================================================================
# Lens 12: Carry-forward reconfirmations (the load-bearing contracts)
# =============================================================================


class TestCarryForwards:
    """Carry-forward reconfirmations per the chunk assignment.

    These probes verify that the deferred carry-forwards from prior
    personalities are still in their documented state. If a future chunk
    closes a CF, the probe MUST be updated.
    """

    def test_e130_cf_skeptic_vs01_01_seven_operators_silently_dropped(self, fhir_client):
        """CF-SKEPTIC-VS01-01: 7 of 9 filter operators still silently dropped.

        Pinned as load-bearing — when VS-02/VS-03 (or a future chunk)
        implements them, this probe MUST fail loudly.
        """
        for op in ["=", "is-not-a", "regex", "in", "not-in", "generalizes", "exists"]:
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
                        "filter": [{"property": prop, "op": op, "value": value}]
                    }]
                }
            }
            status, body, _ = _post_expand(fhir_client, vs)
            assert status == 200, f"op={op} status={status} body={body}"
            codes = _contains_codes(body)
            assert codes == [], (
                f"op={op} NOT silently dropped — codes={codes}. If this fails, "
                f"the operator is now honored — update the probe."
            )

    def test_e131_cf_historian_vs02_02_implicit_path_uses_client_prefix(self, fhir_client):
        """CF-HISTORIAN-VS02-02: implicit path uses client-supplied prefix verbatim.

        Per the carry-forward: `_expand_implicit_value_set` Form (a) does
        NOT call `canonical_system_uri()`. Bug is invisible because the
        fixture doesn't seed alias URIs. The probe documents the current
        behavior (canonical prefix is echoed as-is, which is fine when the
        client already uses the canonical URI).
        """
        # Use the canonical LOINC URI (one of the seeded systems in the
        # implicit-value-set path test would need LOINC rows, which the
        # fixture DOESN'T seed — the implicit expander will return 0 codes
        # with the empty-source extension).
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": "http://loinc.org/vs"},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Implicit value set for LOINC with 0 rows → empty-source extension.
        exts = body.get("expansion", {}).get("extension", [])
        empty_source_ext = next(
            (e for e in exts if "valueset-empty-source" in e.get("url", "")), None
        )
        assert empty_source_ext is not None, (
            f"missing empty-source extension: {exts}"
        )

    def test_e132_cf_historian_vs02_01_bfs_cap_fixture_coincidence(self, fhir_client):
        """CF-HISTORIAN-VS02-01: BFS cap on total — fixture coincidence reconfirmed.

        Per the carry-forward: intensional is-a with count=1 returns
        total=2 by fixture coincidence (the only descendant matches BFS
        limit=1). The probe documents the current behavior.
        """
        vs = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        status, body, _ = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        # contains truncated to 1.
        assert len(body["expansion"]["contains"]) == 1
        # total = 2 (root + 1 descendant) by fixture coincidence.
        assert body["expansion"]["total"] == 2


# =============================================================================
# Lens 13: Active filter flag on $expand (spec-listed but not implemented)
# =============================================================================


class TestExpandActiveFlag:
    """``active`` flag on $expand — spec-listed but not implemented today.

    Per FHIR R4 §4.7.5 In Parameters `active`: "Controls whether the active
    or inactive concepts are included in the expansion." This is a 0..1
    boolean In parameter. The implementation accepts but ignores it (same
    shape as `version`, `offset`, `date`).
    """

    def test_e140_active_true_accepted_on_get(self, fhir_client):
        """``active=true`` on GET — accepted without crash."""
        status, body, _ = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "active": "true"},
        )
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"

    def test_e141_active_false_accepted_on_get(self, fhir_client):
        """``active=false`` on GET — accepted without crash."""
        status, body, _ = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "active": "false"},
        )
        assert status == 200, f"status={status} body={body}"

    def test_e142_active_in_post_parameters_body(self, fhir_client):
        """``active`` in POST Parameters body — accepted without crash."""
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "active", "valueBoolean": True},
            ],
        }
        status, body, _ = _post_expand(fhir_client, params_body)
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"


# =============================================================================
# Lens 14: Cross-handler helper-wiring-scope audit (helper-over-application)
# VS-03 HISTORIAN strategy 45 — verify the helper ISN'T wired into handlers
# that SHOULDN'T use it.
# =============================================================================


class TestHelperOverApplication:
    """Helper-over-application audit per VS-03 HISTORIAN strategy 45.

    Per spec, only ValueSet/$expand accepts the inline `valueSet` parameter.
    CodeSystem/$lookup, CodeSystem/$validate-code, ConceptMap/$translate,
    CodeSystem/$subsumes do NOT accept it. The probe verifies the helper
    doesn't leak into other handlers' call sites.
    """

    def test_e150_lookup_does_not_accept_valueset_param(self, fhir_client):
        """CodeSystem/$lookup POST with valueSet parameter — MUST NOT be processed.

        The implementation should IGNORE the valueSet parameter (it's not
        in the $lookup In Parameters table) and look for system+code.
        Without system+code, it should 400.
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": _make_extensional_snomed()},
                # No system+code.
            ],
        }
        resp = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json=params_body,
            headers={"Accept": "application/fhir+json"},
        )
        # MUST be 400 (no system+code). MUST NOT be 500.
        assert resp.status_code in (400, 422), (
            f"status={resp.status_code} body={resp.text[:200]}"
        )
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_e151_validate_code_does_not_accept_valueset_param(self, fhir_client):
        """CodeSystem/$validate-code POST with valueSet parameter — MUST NOT be processed."""
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": _make_extensional_snomed()},
            ],
        }
        resp = fhir_client.post(
            "/fhir/CodeSystem/$validate-code",
            json=params_body,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code in (400, 422), (
            f"status={resp.status_code} body={resp.text[:200]}"
        )
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"
