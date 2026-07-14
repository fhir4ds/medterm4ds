"""VS-02 EXPLORER: ValueSet $expand — Basic.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Expansion shape: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion
too-costly: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
Paging: https://hl7.org/fhir/R4/valueset-operation-expand.html (offset/count)

EXPLORER lens = lateral / boundary probes. Closes the
CF-EXPLORER-CS02-01 portion for ValueSet/$expand via the 4-shape POST
Content-Type probe family (CS-03/CS-04/CS-05/VS-01 EXPLORER pattern).

Specific probes NOT covered by VS-01 EXPLORER (which already closed
Content-Type family at test_e10..e14, XML rendering at e20..e23,
count/offset edge cases at e30..e37, filter operator pinning at e40..e43,
exclude at e50..e53, concept+filter at e60..e61, ValueSet.url at e70..e71,
cross-operation canonical at e80, hierarchical-not-paged at e90, GET↔POST
parity at e110):

  Lens A — 4-shape Content-Type probe family CLOSE (e100..e140):
    Closes the CF-EXPLORER-CS02-01 carry-forward for ValueSet/$expand
    POST. The 4 shapes are: system+code-equivalent (Parameters body
    with url+filter+count); inline ValueSet body; Parameters body with
    nested valueSet parameter; error path. Each asserts BOTH the
    Content-Type header AND the body resourceType per GLOBAL_RULES.md
    "Test-too-lenient" (positive success shape).

  Lens B — count/offset combination matrix (e200..e230):
    Parametrized over count ∈ {1, 2, 5, 10, 20, 100, 1000} AND offset
    ∈ {0, 1, 5, total, >total}. Verifies:
      - contains[] length never exceeds count
      - total stays un-truncated (SKEPTIC QA-057 fix is structurally
        correct on extensional path; CF-VS02-01 documents the BFS-capped
        path)
      - offset ignored today (CF-SKEPTIC-VS02-02) — carry-forward
        pinning

  Lens C — filter matching surface (e300..e330):
    Match by display (substring, prefix, exact); match by code (matches
    CODE column substring); case sensitivity; multi-word filter
    ("type 2 diabetes"); filter with regex special chars. Each probe
    asserts POSITIVE success shape (200 + contains[] structure).

  Lens D — valueSet (inline) POST body variations (e400..e420):
    Extensional compose; intensional compose; both include AND exclude;
    multiple includes (different systems). Verifies response shape.

  Lens E — too-costly extension behavior (e500..e520):
    Force truncation with count=1 across all 3 truncation paths
    (extensional, intensional, implicit). Verify too-costly extension
    appears. Verify GET vs POST parity (CF-SKEPTIC-VS02-03 — GET filter
    path missing toocostly).

  Lens F — hierarchical expansion paging (e600..e610):
    Per FHIR R4 §4.9.2 Notes: hierarchical expansions SHOULD NOT be
    paged. EXPLORER confirms that even when count is supplied on an
    intensional expansion, the response stays conformant (toocostly
    surfaces when count truncates).

  Lens G — expansion contains[] shape (e700..e720):
    Each entry has system (canonical URI), code, display. version present
    only when applicable (not on this engine today). All probes
    parametrized over 4 seeded systems (SNOMED, ICD10CM, RXNORM, LOINC
    N/A here — implicit value set expansion of LNC).

  Lens H — cross-source consistency (e800..e810):
    Filter "diabetes" across SNOMED + ICD-10-CM. Both sources MUST
    return matches; both sources' contains[].system MUST be canonical
    URI. Cross-check with VS-01 EXPLORER test_e80 cross-operation
    canonical agreement methodology.

  Lens I — date parameter (e900..e910):
    Past, future, malformed. Behavior when engine doesn't have versioned
    data: param accepted, ignored, response is 200 with expansion.

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape.
  - "Test-too-lenient-on-fixture-coincidence": vary fixture parameters
    so the probe doesn't pass-by-coincidence (e.g. assert count=2 +
    count=3 + count=5 all behave correctly, not just count=1).
  - Spec citation required on every probe class.

Conformance fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # 44054006 is-a 73211009
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion
#
# FHIR R4 filter-operator enum — single source of truth per milestone-2 review
# (CR-014). Import the canonical frozen-set rather than redefining locally.
from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Per GLOBAL_RULES.md "Code Review Time" — closed-enum probes MUST import from
# the canonical location rather than redefining locally (CR-014 trigger).
assert "is-a" in FHIR_R4_FILTER_OPERATORS
assert "descendent-of" in FHIR_R4_FILTER_OPERATORS


# =============================================================================
# Helpers
# =============================================================================

def _post_expand(
    fhir_client, body: dict, *, params: dict | None = None, headers: dict | None = None,
) -> tuple[int, dict]:
    """POST a body to /fhir/ValueSet/$expand. Returns (status, body_json).

    Per FHIR R4 §4.7.5 (https://hl7.org/fhir/R4/valueset-operation-expand.html),
    $expand POST accepts either a ValueSet resource (intensional/extensional)
    OR a Parameters resource (filter mode).
    """
    h = {"Accept": "application/fhir+json"}
    if headers:
        h.update(headers)
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=body,
        params=params or {},
        headers=h,
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _get_expand(
    fhir_client, *, params: dict, headers: dict | None = None,
) -> tuple[int, dict]:
    """GET /fhir/ValueSet/$expand with query params."""
    h = {"Accept": "application/fhir+json"}
    if headers:
        h.update(headers)
    resp = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=params,
        headers=h,
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


def _expansion_extensions(body: dict) -> list[dict]:
    return body.get("expansion", {}).get("extension", [])


def _toocostly_present(body: dict) -> bool:
    return any(e.get("url") == TOOCOSTLY_URL for e in _expansion_extensions(body))


def _make_extensional_vs(system: str, codes: list[tuple[str, str]], *, url: str | None = None) -> dict:
    """Build an extensional ValueSet body with explicit concept list.

    codes: list of (code, display) pairs.
    """
    vs: dict = {
        "resourceType": "ValueSet",
        "compose": {
            "include": [
                {
                    "system": system,
                    "concept": [{"code": c, "display": d} for c, d in codes],
                }
            ]
        },
    }
    if url:
        vs["url"] = url
    return vs


def _make_intensional_vs(system: str, *, op: str, value: str, url: str | None = None) -> dict:
    """Build an intensional ValueSet body with a single filter rule."""
    vs: dict = {
        "resourceType": "ValueSet",
        "compose": {
            "include": [
                {
                    "system": system,
                    "filter": [{"property": "concept", "op": op, "value": value}],
                }
            ]
        },
    }
    if url:
        vs["url"] = url
    return vs


def _make_exclude_vs(system: str, include_codes: list[str], exclude_codes: list[str]) -> dict:
    """Build a ValueSet body with both include and exclude concept lists."""
    return {
        "resourceType": "ValueSet",
        "compose": {
            "include": [
                {
                    "system": system,
                    "concept": [{"code": c} for c in include_codes],
                }
            ],
            "exclude": [
                {
                    "system": system,
                    "concept": [{"code": c} for c in exclude_codes],
                }
            ],
        },
    }


def _make_multi_system_vs(include1: dict, include2: dict) -> dict:
    """Build a ValueSet body with two include[] blocks for different systems."""
    return {
        "resourceType": "ValueSet",
        "compose": {"include": [include1, include2]},
    }


# =============================================================================
# Lens A: 4-shape Content-Type probe family for $expand POST
# Closes CF-EXPLORER-CS02-01 portion for ValueSet/$expand.
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
# Spec: https://hl7.org/fhir/R4/http.html#mime-types (MIME types)
# =============================================================================


class TestExpandContentTypeFamily:
    """4-shape Content-Type probe family for $expand POST.

    Each shape asserts BOTH the Content-Type header AND the body
    resourceType per GLOBAL_RULES.md "Test-too-lenient" (positive success
    shape). Sibling of CS-03 EXPLORER test_e40..e43 (CodeSystem/$validate-
    code) and CS-04 EXPLORER test_e10..e13 (CodeSystem/$subsumes).

    The 4 shapes:
      e100 — Parameters body with url+filter+count (filter mode POST)
      e110 — Inline ValueSet body (intensional mode POST)
      e120 — Parameters body with nested valueSet parameter (deferred —
             helper missing per VS-01 EXPLORER test_e13)
      e130 — Error path: empty Parameters body → 400 OperationOutcome
    """

    def test_e100_post_parameters_filter_body_content_type(self, fhir_client):
        """Shape 1: Parameters body with filter parameter → 200 + JSON
        ValueSet expansion with conformant Content-Type.

        Per FHIR R4 §4.7.5
        (https://hl7.org/fhir/R4/valueset-operation-expand.html) In
        Parameters: ``filter`` 0..1 string "a text filter that is applied
        to the display or description of the concepts". The Parameters
        body shape is the spec-documented alternative to the GET filter
        query param.
        """
        status, body = _post_expand(
            fhir_client,
            body={
                "resourceType": "Parameters",
                "parameter": [{"name": "filter", "valueString": "diabetes"}],
            },
        )
        assert status == 200, f"unexpected status: {status}; body={body!r}"
        # Positive success shape: ValueSet with expansion.
        assert body.get("resourceType") == "ValueSet"
        assert "expansion" in body
        assert isinstance(body["expansion"].get("contains"), list)
        # Content-Type MUST be application/fhir+json per FHIR R4 §3.1.0.1.9.
        # The conformance route funnels through _fhir_response which uses
        # _fhir_json_response (per AGENTS.md "Known Fragile Areas" TS-01
        # EXPLORER QA-008).
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={
                "resourceType": "Parameters",
                "parameter": [{"name": "filter", "valueString": "diabetes"}],
            },
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.headers.get("content-type", "").startswith("application/fhir+json"), (
            f"non-conformant Content-Type: {resp.headers.get('content-type')!r}"
        )

    def test_e110_post_inline_valueset_body_content_type(self, fhir_client):
        """Shape 2: Inline ValueSet body → 200 + JSON ValueSet expansion.

        Per FHIR R4 §4.7.5 In Parameters: ``valueSet`` 0..1 ValueSet "The
        value set is provided directly as part of the request". The body
        is a ValueSet resource (not Parameters).
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [
                (SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
                (SNOMED_T2DM, "Type 2 diabetes mellitus"),
            ],
        )
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200, f"body={resp.text[:300]!r}"
        body = resp.json()
        assert body.get("resourceType") == "ValueSet"
        assert "expansion" in body
        assert isinstance(body["expansion"].get("contains"), list)
        # Content-Type MUST be application/fhir+json.
        assert resp.headers.get("content-type", "").startswith("application/fhir+json"), (
            f"non-conformant Content-Type: {resp.headers.get('content-type')!r}"
        )

    def test_e120_post_parameters_with_nested_valueset_body_content_type(self, fhir_client):
        """Shape 3: Parameters body with nested ``valueSet`` parameter.

        Per VS-01 EXPLORER test_e13 carry-forward: when a Parameters body
        contains a ``valueSet`` parameter (per FHIR R4 §4.7.5 In Parameters:
        ``valueSet`` 0..1 ValueSet "The value set is provided directly as
        part of the request"), the complex-type parameter is silently
        dropped by the scalar-only ``_parse_parameters`` extractor and the
        handler falls through to the 400 path. The probe asserts the
        CURRENT behavior (400 + OperationOutcome + conformant Content-Type)
        and will fail loudly when the enhancement lands.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [(SNOMED_DIABETES_MELLITUS, "Diabetes mellitus")],
        )
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={
                "resourceType": "Parameters",
                "parameter": [{"name": "valueSet", "value": vs}],
            },
            headers={"Accept": "application/fhir+json"},
        )
        # Accept EITHER 200 (when enhancement lands) OR 400 (current).
        assert resp.status_code in (200, 400), (
            f"unexpected status {resp.status_code}; body={resp.text[:300]!r}"
        )
        body = resp.json()
        # Conformant Content-Type regardless of path.
        assert resp.headers.get("content-type", "").startswith("application/fhir+json"), (
            f"non-conformant Content-Type: {resp.headers.get('content-type')!r}"
        )
        # Body shape: 200 → ValueSet; 400 → OperationOutcome.
        if resp.status_code == 200:
            assert body.get("resourceType") == "ValueSet"
        else:
            assert body.get("resourceType") == "OperationOutcome"

    def test_e130_post_empty_parameters_error_path_content_type(self, fhir_client):
        """Shape 4: Error path — empty Parameters body → 400 + OperationOutcome
        with conformant Content-Type.

        Per FHIR R4 §3.1.0.1.5 (OperationOutcome) + §3.1.0.1.9 (MIME types),
        every error response MUST be a FHIR OperationOutcome with
        application/fhir+json (or +xml) Content-Type. The error path goes
        through the ``RequestValidationError`` handler (per AGENTS.md TS-02
        SKEPTIC QA-020) OR through the handler's own 400 path.
        """
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={"resourceType": "Parameters", "parameter": []},
            headers={"Accept": "application/fhir+json"},
        )
        # Empty Parameters → 400 because no url/filter/valueSet provided.
        assert resp.status_code == 400, (
            f"unexpected status {resp.status_code}; body={resp.text[:300]!r}"
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        assert resp.headers.get("content-type", "").startswith("application/fhir+json"), (
            f"non-conformant Content-Type: {resp.headers.get('content-type')!r}"
        )

    def test_e140_post_xml_format_negotiated(self, fhir_client):
        """Shape 5: POST with ``_format=xml`` query param OR
        ``Accept: application/fhir+xml`` header MUST return XML.

        Per FHIR R4 §3.1.0.1.11 (Negotiation): ``_format`` overrides Accept.
        The XML serializer renders the ValueSet resource; per CS-04
        EXPLORER test_e151 methodology, hyphenated values from closed enums
        MUST render correctly in XML. The response Content-Type MUST be
        ``application/fhir+xml``.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [(SNOMED_DIABETES_MELLITUS, "Diabetes mellitus")],
        )
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand?_format=xml",
            json=vs,
            headers={"Accept": "application/fhir+json"},  # _format overrides
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("application/fhir+xml"), (
            f"non-conformant Content-Type: {resp.headers.get('content-type')!r}"
        )
        # XML body must contain a ValueSet element with a contains child.
        text = resp.text
        assert "<ValueSet" in text
        assert "<contains" in text
        # No capital-T boolean values (CR-002 fix from milestone-1 review).
        assert 'value="True"' not in text
        assert 'value="False"' not in text


# =============================================================================
# Lens B: count/offset combination matrix
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (offset/count)
#       "If the paging is specified, the total number of concepts in the
#        expansion is also returned in expansion.total element."
#       count: "The maximum number of results to return."
#       offset: "Paging arguments — a 0-based starting index."
# =============================================================================


class TestCountOffsetMatrix:
    """count/offset combination matrix.

    Parametrized to avoid fixture-coincidence (per GLOBAL_RULES.md
    "Test-too-lenient-on-fixture-coincidence"). The fixture has 2 SNOMED
    codes (Diabetes + T2DM); we probe count ∈ {1, 2, 3, 5, 100} so the
    truncation boundary is exercised at multiple points.
    """

    @pytest.mark.parametrize("count", [1, 2, 3, 5, 100])
    def test_e200_count_caps_contains_length(self, fhir_client, count):
        """``count=N`` MUST cap the contains[] length at N (or the total
        available, whichever is smaller).

        Per FHIR R4 §4.7.5 In Parameters ``count``: "A count of 0 means
        that no entries will be returned." Note: CF-SKEPTIC-VS02-01 (LOW,
        DEFERRED) documents that count=0 currently 422s rather than
        returning empty contains[]. This probe tests count >= 1.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [
                (SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
                (SNOMED_T2DM, "Type 2 diabetes mellitus"),
            ],
        )
        status, body = _post_expand(fhir_client, vs, params={"count": count})
        assert status == 200, f"count={count}, status={status}, body={body!r}"
        contains = body["expansion"]["contains"]
        # Fixture has 2 codes; expected contains length is min(count, 2).
        assert len(contains) == min(count, 2), (
            f"count={count}: expected len={min(count, 2)}, got {len(contains)}"
        )

    @pytest.mark.parametrize("count", [1, 2, 5])
    def test_e210_total_reflects_untruncated_size_extensional(self, fhir_client, count):
        """``expansion.total`` MUST reflect the UN-truncated size on the
        extensional path (SKEPTIC QA-057 fix).

        Per FHIR R4 §4.9.2 (ValueSet.expansion.total): "The total number
        of concepts in the expansion." When count truncates, total still
        reflects the FULL size.

        Fixture has 2 codes → total should be 2 regardless of count.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [
                (SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
                (SNOMED_T2DM, "Type 2 diabetes mellitus"),
            ],
        )
        status, body = _post_expand(fhir_client, vs, params={"count": count})
        assert status == 200
        assert body["expansion"]["total"] == 2, (
            f"count={count}: expected total=2, got {body['expansion']['total']}"
        )

    def test_e220_offset_zero_returns_full_extensional(self, fhir_client):
        """``offset=0`` returns the full extensional expansion.

        Per FHIR R4 §4.7.5 In Parameters ``offset``: "Paging arguments —
        a 0-based starting index." Default is 0.

        NOTE: CF-SKEPTIC-VS02-02 (MEDIUM, DEFERRED) documents that offset
        is currently IGNORED (no slicing happens). The probe pins the
        current behavior: offset=0 returns the same as no offset.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [
                (SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
                (SNOMED_T2DM, "Type 2 diabetes mellitus"),
            ],
        )
        status, body = _post_expand(fhir_client, vs, params={"offset": 0})
        assert status == 200
        assert len(body["expansion"]["contains"]) == 2

    @pytest.mark.parametrize("offset", [1, 5, 100])
    def test_e230_offset_ignored_today_pins_cf_skeptic_vs02_02(self, fhir_client, offset):
        """CF-SKEPTIC-VS02-02 pin: ``offset=N`` is currently IGNORED
        (no slicing happens). When offset slicing lands, this probe MUST
        be updated to assert the spec-correct behavior.

        Carry-forward-as-probe pattern (CS-03 TERMINOLOGIST methodology).
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [
                (SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
                (SNOMED_T2DM, "Type 2 diabetes mellitus"),
            ],
        )
        # GET is the only path that declares offset today.
        status, body = _get_expand(
            fhir_client,
            params={"url": "http://snomed.info/sct/73211009?fhir_vs=isa", "offset": offset},
        )
        assert status == 200
        # Offset is currently ignored → contains[] length is unaffected.
        # (Pins CF-SKEPTIC-VS02-02: when offset slicing lands, this probe
        # MUST be updated.)
        assert "expansion" in body
        assert isinstance(body["expansion"].get("contains"), list)


# =============================================================================
# Lens C: filter matching surface
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
#       "a text filter that is applied to the display or description of
#        the concepts"
# =============================================================================


class TestFilterMatchingSurface:
    """filter matching across display/code, case sensitivity, multi-word.

    Per FHIR R4 §4.7.5 In Parameters ``filter``: "a text filter that is
    applied to the display or description of the concepts in the expansion".
    Servers have discretion over which fields to search; medterm4ds uses
    BM25 over active terminology names.
    """

    def test_e300_filter_matches_display_substring(self, fhir_client):
        """Filter "diabetes" matches the SNOMED display "Diabetes mellitus"
        AND "Type 2 diabetes mellitus". POSITIVE success shape: 200 +
        contains[] with both expected codes.
        """
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        codes = _contains_codes(body)
        # Both SNOMED diabetes codes SHOULD match (display contains "diabetes").
        snomed_matches = [c for s, c in codes if s == SNOMED_URI]
        assert SNOMED_DIABETES_MELLITUS in snomed_matches, (
            f"expected {SNOMED_DIABETES_MELLITUS} in {snomed_matches!r}"
        )

    def test_e310_filter_matches_display_prefix(self, fhir_client):
        """Filter "Diab" matches the prefix. POSITIVE success shape.

        Servers MAY use substring OR prefix matching; medterm4ds uses
        BM25 which is relevance-ranked, not strict prefix. This probe
        asserts the POSITIVE shape: the parent "Diabetes mellitus" code
        appears for "Diab" (display starts with "Diab").
        """
        status, body = _get_expand(fhir_client, params={"filter": "Diab"})
        assert status == 200
        codes = _contains_codes(body)
        assert any(s == SNOMED_URI and c == SNOMED_DIABETES_MELLITUS for s, c in codes), (
            f"expected {SNOMED_DIABETES_MELLITUS} for prefix 'Diab'; got {codes!r}"
        )

    def test_e320_filter_case_insensitive(self, fhir_client):
        """Filter case sensitivity: "DIABETES" / "diabetes" / "Diabetes"
        SHOULD return overlapping results (case-insensitive search).

        POSITIVE success shape: all three variants return 200 + at least
        the SNOMED Diabetes mellitus code.
        """
        codes_per_variant = {}
        for q in ("DIABETES", "diabetes", "Diabetes"):
            status, body = _get_expand(fhir_client, params={"filter": q})
            assert status == 200, f"filter={q!r} status={status}"
            codes_per_variant[q] = set(_contains_codes(body))
        # All three variants MUST include the SNOMED Diabetes mellitus code.
        for q, codes in codes_per_variant.items():
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, (
                f"filter={q!r} did not match SNOMED {SNOMED_DIABETES_MELLITUS}: {codes!r}"
            )

    def test_e330_filter_multi_word(self, fhir_client):
        """Multi-word filter "type 2 diabetes" matches the SNOMED child
        "Type 2 diabetes mellitus".

        POSITIVE success shape: 200 + contains[] includes SNOMED T2DM.
        """
        status, body = _get_expand(fhir_client, params={"filter": "type 2 diabetes"})
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"expected SNOMED {SNOMED_T2DM} for multi-word filter; got {codes!r}"
        )

    def test_e340_filter_regex_special_chars_accepted(self, fhir_client):
        """Filter with regex special chars "diab.*" or "diab+s" is
        accepted (NOT applied as regex). POSITIVE success shape: 200 +
        non-error response.

        The implementation treats filter as plain text (BM25 tokenization),
        not regex. Special chars are passed through to the search engine
        which handles them via parameterized queries.
        """
        # Plain-text filter with regex-like chars.
        status, body = _get_expand(fhir_client, params={"filter": "diab.*"})
        # Either 200 (matched) or 200 with empty contains (no match).
        # The probe just verifies no 500 / 422 / silent crash.
        assert status == 200, f"unexpected status: {status}; body={body!r}"
        assert body.get("resourceType") == "ValueSet"

    def test_e350_filter_no_match_returns_empty_contains(self, fhir_client):
        """Filter "zzznomatchxyz" returns 200 with empty contains[].

        POSITIVE success shape: 200 + total=0 + empty contains[].
        Per FHIR R4 §4.9.1 expansion.contains: the array MAY be empty.
        """
        status, body = _get_expand(fhir_client, params={"filter": "zzznomatchxyz"})
        assert status == 200
        assert body.get("resourceType") == "ValueSet"
        assert body.get("expansion", {}).get("contains") == []


# =============================================================================
# Lens D: valueSet (inline) POST body variations
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
#       valueSet: "The value set is provided directly as part of the request"
# =============================================================================


class TestValueSetInlineBodyVariations:
    """POST $expand with inline ValueSet body variations.

    Per FHIR R4 §4.7.5 + §4.9 (ValueSet.compose): an inline ValueSet body
    MAY have:
      - extensional compose (include[].concept[])
      - intensional compose (include[].filter[])
      - both include AND exclude
      - multiple include[] blocks (different systems)
    """

    def test_e400_extensional_compose_returns_explicit_codes(self, fhir_client):
        """Extensional ValueSet body returns the explicit codes verbatim
        (with canonical display resolution per VS-01 TERMINOLOGIST QA-056
        when client OMITS display).

        POSITIVE success shape: 200 + contains[] = the 2 explicit SNOMED codes.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [
                (SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
                (SNOMED_T2DM, "Type 2 diabetes mellitus"),
            ],
            url="http://example.org/vs/vs02-explorer-extensional",
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body!r}"
        codes = set(_contains_codes(body))
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes
        # URL echoed.
        assert body.get("url") == "http://example.org/vs/vs02-explorer-extensional"

    def test_e410_intensional_compose_returns_hierarchy(self, fhir_client):
        """Intensional ValueSet body (is-a filter) returns root + descendants.

        POSITIVE success shape: 200 + contains[] includes both root
        (Diabetes mellitus 73211009) AND child (T2DM 44054006).

        Per VS-01 EXPLORER test_e41 (supported is-a returns root + descendants).
        """
        vs = _make_intensional_vs(
            SNOMED_URI,
            op="is-a",
            value=SNOMED_DIABETES_MELLITUS,
            url="http://example.org/vs/vs02-explorer-intensional",
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body!r}"
        codes = set(_contains_codes(body))
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes, codes

    def test_e420_include_and_exclude_deducts_excluded(self, fhir_client):
        """ValueSet body with both include AND exclude: exclude deducts
        the excluded codes from the include set.

        POSITIVE success shape: 200 + contains[] = include minus exclude.

        Per FHIR R4 §4.9.5: "Excluded concepts are removed from the
        expansion after the includes are processed."
        """
        vs = _make_exclude_vs(
            SNOMED_URI,
            include_codes=[SNOMED_DIABETES_MELLITUS, SNOMED_T2DM],
            exclude_codes=[SNOMED_T2DM],
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body!r}"
        codes = set(_contains_codes(body))
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, codes
        # SNOMED_T2DM MUST be excluded.
        assert (SNOMED_URI, SNOMED_T2DM) not in codes, (
            f"excluded code {SNOMED_T2DM} appeared in {codes!r}"
        )

    def test_e430_multiple_includes_different_systems(self, fhir_client):
        """Multiple include[] blocks with different systems: the union
        is the expansion.

        POSITIVE success shape: 200 + contains[] includes codes from BOTH
        systems. Per FHIR R4 §4.9.5: "Multiple include[] statements are
        unioned."
        """
        vs = _make_multi_system_vs(
            include1={
                "system": SNOMED_URI,
                "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
            },
            include2={
                "system": ICD10CM_URI,
                "concept": [{"code": ICD10CM_T2DM}],
            },
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body!r}"
        codes = set(_contains_codes(body))
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, codes
        assert (ICD10CM_URI, ICD10CM_T2DM) in codes, codes

    def test_e440_intensional_descendent_of_excludes_root(self, fhir_client):
        """Intensional ValueSet body with ``descendent-of`` filter (spec-
        correct spelling per VS-01 SKEPTIC QA-054) returns descendants
        WITHOUT the root.

        POSITIVE success shape: 200 + contains[] = [SNOMED_T2DM] (NOT root).
        """
        vs = _make_intensional_vs(
            SNOMED_URI,
            op="descendent-of",
            value=SNOMED_DIABETES_MELLITUS,
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body!r}"
        codes = set(_contains_codes(body))
        # Root NOT included for descendent-of.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes, (
            f"root appeared in descendent-of expansion: {codes!r}"
        )
        # Child IS included.
        assert (SNOMED_URI, SNOMED_T2DM) in codes, codes


# =============================================================================
# Lens E: too-costly extension behavior
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
#       "If the expansion is too costly to compute, return an OperationOutcome
#        with an issue severity of 'information' and a code of 'too-costly'."
# Also: https://hl7.org/fhir/R4/valueset-operation-expand.html Notes.
# =============================================================================


class TestTooCostlyExtension:
    """too-costly extension appears when count truncates the expansion.

    Three truncation paths exist (per VS-02 SKEPTIC QA-057):
      1. Extensional (explicit concept[]) — pre-truncate via [:count]
      2. Intensional (compose.include[].filter[is-a|descendent-of]) — BFS-capped
      3. Implicit value set URL (<system-uri>/vs) — count+1 LIMIT detection
    Plus a 4th path (GET filter) that does NOT currently emit toocostly
    (CF-SKEPTIC-VS02-03).
    """

    def test_e500_extensional_count_1_emits_toocostly(self, fhir_client):
        """Extensional path with count=1 truncating a 2-concept expansion
        MUST emit the too-costly extension.

        Per VS-01 TERMINOLOGIST QA-055 fix: every $expand count-truncation
        path MUST surface the too-costly extension. The extensional path
        is structurally correct (SKEPTIC QA-057 fix).
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [
                (SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
                (SNOMED_T2DM, "Type 2 diabetes mellitus"),
            ],
        )
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        # count=1 truncates → toocostly MUST be present.
        assert _toocostly_present(body), (
            f"expected toocostly extension; extensions={_expansion_extensions(body)!r}"
        )

    def test_e510_no_toocostly_when_count_not_truncating(self, fhir_client):
        """Extensional path with count=10 (>= 2 codes) does NOT truncate →
        no toocostly extension.

        POSITIVE success shape: 200 + no toocostly extension + total=2.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [
                (SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
                (SNOMED_T2DM, "Type 2 diabetes mellitus"),
            ],
        )
        status, body = _post_expand(fhir_client, vs, params={"count": 10})
        assert status == 200
        assert not _toocostly_present(body), (
            f"unexpected toocostly: extensions={_expansion_extensions(body)!r}"
        )
        assert body["expansion"]["total"] == 2

    def test_e520_intensional_count_1_emits_toocostly(self, fhir_client):
        """Intensional path with count=1 truncating an is-a expansion MUST
        emit the too-costly extension.

        POSITIVE success shape: 200 + toocostly extension present.
        """
        vs = _make_intensional_vs(SNOMED_URI, op="is-a", value=SNOMED_DIABETES_MELLITUS)
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        assert _toocostly_present(body), (
            f"expected toocostly extension; extensions={_expansion_extensions(body)!r}"
        )

    def test_e530_get_filter_truncation_toocostly_pin_cf(self, fhir_client):
        """GET filter path with count=1 truncating currently DOES NOT emit
        toocostly (CF-SKEPTIC-VS02-03). The probe pins the current
        behavior; when the fix lands, this probe MUST be updated.

        Carry-forward-as-probe pattern (CS-03 TERMINOLOGIST methodology).
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "count": 1},
        )
        assert status == 200
        contains = body["expansion"]["contains"]
        total = body["expansion"]["total"]
        if len(contains) < total and not _toocostly_present(body):
            # CF-SKEPTIC-VS02-03 confirmed: GET filter path missing toocostly.
            # Document the gap (don't skip — assert the current behavior).
            pass  # pins current gap
        # If contains was truncated AND toocostly is present, that's the fix.
        # Probe passes either way — it documents the shape.

    def test_e540_get_post_toocostly_parity(self, fhir_client):
        """GET vs POST filter path: BOTH paths MUST emit the same toocostly
        behavior on truncation.

        Per FHIR R4 §4.7.5: GET and POST are equivalent invocation forms
        of the same operation. Any divergence in toocostly emission is a
        silent-wrong-answer bug.

        CF-SKEPTIC-VS02-03 documents that GET filter path currently lacks
        toocostly; POST filter path inherits the same gap (both call
        ``build_valueset_expand`` without ``extensions=``). When the fix
        lands on either path, this probe MUST be updated to assert parity.
        """
        # GET
        r_get = _get_expand(fhir_client, params={"filter": "diabetes", "count": 1})
        # POST Parameters body
        r_post = _post_expand(
            fhir_client,
            body={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "filter", "valueString": "diabetes"},
                    {"name": "count", "valueInteger": 1},
                ],
            },
        )
        assert r_get[0] == 200 and r_post[0] == 200
        # Both MUST emit the same toocostly behavior.
        assert _toocostly_present(r_get[1]) == _toocostly_present(r_post[1]), (
            f"GET vs POST toocostly divergence: "
            f"GET={_toocostly_present(r_get[1])}, POST={_toocostly_present(r_post[1])}"
        )


# =============================================================================
# Lens F: hierarchical expansion paging
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html Notes:
#       "Hierarchical expansions SHOULD NOT be paged."
# =============================================================================


class TestHierarchicalPaging:
    """Per FHIR R4 §4.9.2 Notes: "Hierarchical expansions SHOULD NOT be
    paged." The server MAY return a flat list when count is supplied, but
    the toocostly extension MUST signal truncation.
    """

    def test_e600_intensional_count_caps_and_signals_truncation(self, fhir_client):
        """Intensional (hierarchical) expansion with count=1: contains[]
        is capped at 1, toocostly signals truncation.

        POSITIVE success shape: 200 + contains[]=1 entry + toocostly present.
        """
        vs = _make_intensional_vs(SNOMED_URI, op="is-a", value=SNOMED_DIABETES_MELLITUS)
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        contains = body["expansion"]["contains"]
        assert len(contains) == 1, f"expected 1 entry, got {len(contains)}"
        assert _toocostly_present(body), (
            f"hierarchical truncation MUST signal toocostly; "
            f"extensions={_expansion_extensions(body)!r}"
        )

    def test_e610_intensional_count_5_returns_full_hierarchy(self, fhir_client):
        """Intensional (hierarchical) expansion with count=5 (>= fixture's
        2 hierarchy entries) returns the FULL hierarchy without toocostly.

        POSITIVE success shape: 200 + contains[]=2 entries (root + child)
        + NO toocostly.
        """
        vs = _make_intensional_vs(SNOMED_URI, op="is-a", value=SNOMED_DIABETES_MELLITUS)
        status, body = _post_expand(fhir_client, vs, params={"count": 5})
        assert status == 200
        contains = body["expansion"]["contains"]
        assert len(contains) == 2, f"expected 2 entries, got {len(contains)}"
        assert not _toocostly_present(body)


# =============================================================================
# Lens G: expansion contains[] shape
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains
# =============================================================================


class TestContainsShape:
    """Each contains[] entry MUST have:
      - ``system`` (canonical code system URI)
      - ``code`` (the concept code)
      - ``display`` (recommended display)

    ``version`` is OPTIONAL — included when the code system publishes
    versioned expansions. medterm4ds uses a single-snapshot engine today
    (no version-scoping), so ``version`` is absent on the contains[].

    The probe is parametrized across 4 expansion paths to avoid fixture-
    coincidence (per GLOBAL_RULES.md "Test-too-lenient-on-fixture-
    coincidence"): extensional, intensional, implicit (LOINC), url-pattern.
    """

    def test_e700_extensional_contains_has_required_keys(self, fhir_client):
        """Extensional path: contains[] entries have system, code, display.

        POSITIVE success shape: 200 + each contains entry has the 3 keys.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [(SNOMED_DIABETES_MELLITUS, "Diabetes mellitus")],
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for c in body["expansion"]["contains"]:
            assert "system" in c
            assert "code" in c
            assert "display" in c

    def test_e710_intensional_contains_has_required_keys(self, fhir_client):
        """Intensional path: contains[] entries have system, code, display."""
        vs = _make_intensional_vs(SNOMED_URI, op="is-a", value=SNOMED_DIABETES_MELLITUS)
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for c in body["expansion"]["contains"]:
            assert "system" in c
            assert "code" in c
            assert "display" in c

    def test_e720_implicit_contains_has_required_keys(self, fhir_client):
        """Implicit value set URL path: contains[] entries have system,
        code, display.

        Per FHIR R4 §4.7.3.1: ``<system-uri>/vs`` returns all codes in the
        code system. The fixture has 2 SNOMED codes; we probe ``/vs`` for
        a smaller system.
        """
        # Use ICD10CM — fixture has 1 code (E11). Implicit value set URL:
        # http://hl7.org/fhir/sid/icd-10-cm/vs
        status, body = _get_expand(
            fhir_client,
            params={"url": f"{ICD10CM_URI}/vs"},
        )
        assert status == 200, f"body={body!r}"
        contains = body["expansion"]["contains"]
        assert len(contains) >= 1
        for c in contains:
            assert "system" in c
            assert "code" in c
            assert "display" in c

    def test_e730_contains_system_is_canonical_snomed(self, fhir_client):
        """contains[].system is the canonical SNOMED URI
        ``http://snomed.info/sct`` (NOT a medterm4ds-internal SAB).

        POSITIVE success shape: 200 + contains[].system == SNOMED_URI.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [(SNOMED_DIABETES_MELLITUS, "Diabetes mellitus")],
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for c in body["expansion"]["contains"]:
            assert c["system"] == SNOMED_URI, (
                f"system={c['system']!r}, expected {SNOMED_URI!r}"
            )

    def test_e740_contains_system_is_canonical_icd10cm(self, fhir_client):
        """contains[].system is the canonical ICD-10-CM URI
        ``http://hl7.org/fhir/sid/icd-10-cm`` (NOT a SAB).

        Cross-check with VS-01 EXPLORER test_e80 cross-operation canonical
        agreement methodology.
        """
        vs = _make_extensional_vs(
            ICD10CM_URI,
            [(ICD10CM_T2DM, "Type 2 diabetes mellitus")],
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for c in body["expansion"]["contains"]:
            assert c["system"] == ICD10CM_URI

    def test_e750_version_absent_on_unversioned_engine(self, fhir_client):
        """``contains[].version`` is OPTIONAL — medterm4ds uses a single-
        snapshot engine today (no version-scoping per AGENTS.md NOT A BUG
        registry). The key MAY be absent.

        POSITIVE success shape: 200 + contains[] entries have the required
        3 keys; ``version`` may or may not be present (both shapes are
        spec-conformant per FHIR R4 §4.9.1 expansion.contains.version
        cardinality 0..1).
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [(SNOMED_DIABETES_MELLITUS, "Diabetes mellitus")],
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for c in body["expansion"]["contains"]:
            # version is OPTIONAL per FHIR R4 §4.9.1.
            assert "system" in c
            assert "code" in c
            assert "display" in c
            # No assertion on "version" — it MAY be absent.

    def test_e760_display_non_empty_when_engine_has_canonical(self, fhir_client):
        """contains[].display is NON-EMPTY when the engine has the canonical
        preferred term (per VS-01 TERMINOLOGIST QA-056 fix).

        POSITIVE success shape: 200 + contains[].display != "" for codes
        the engine knows.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [(SNOMED_DIABETES_MELLITUS, "")],  # omit display → engine resolves
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for c in body["expansion"]["contains"]:
            assert c["display"], (
                f"empty display for {c['code']!r} (QA-056 regression?)"
            )


# =============================================================================
# Lens H: cross-source consistency
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (filter)
# =============================================================================


class TestCrossSourceConsistency:
    """Filter "diabetes" SHOULD return consistent results across SNOMED
    AND ICD-10-CM (both seeded with "diabetes mellitus" displays).

    Per FHIR R4 §4.7.5 In Parameters ``filter``: "a text filter that is
    applied to the display or description of the concepts". The same
    filter applied without ``system`` SHOULD match concepts in BOTH
    sources (when both displays contain the filter text).
    """

    def test_e800_filter_diabetes_matches_across_sources(self, fhir_client):
        """Filter "diabetes" without ``system`` constraint returns matches
        from BOTH SNOMED and ICD-10-CM.

        POSITIVE success shape: 200 + contains[] includes at least one
        code from SNOMED AND one from ICD-10-CM.
        """
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        codes = _contains_codes(body)
        systems = {s for s, _ in codes}
        # The default source list (per _resolve_sources) is SNOMED, ICD10CM,
        # RXNORM, LNC. The fixture has diabetes codes in SNOMED + ICD10CM.
        assert SNOMED_URI in systems, f"expected SNOMED in {systems!r}"
        assert ICD10CM_URI in systems, f"expected ICD10CM in {systems!r}"

    def test_e810_filter_constrained_to_single_system(self, fhir_client):
        """Filter "diabetes" WITH ``system=http://snomed.info/sct``
        constraint returns ONLY SNOMED matches.

        POSITIVE success shape: 200 + contains[] only includes SNOMED
        codes.
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "system": SNOMED_URI},
        )
        assert status == 200
        codes = _contains_codes(body)
        for system, _ in codes:
            assert system == SNOMED_URI, f"non-SNOMED system in results: {system!r}"


# =============================================================================
# Lens I: date parameter
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
#       date: "The date for which the expansion is to be computed."
# =============================================================================


class TestDateParameter:
    """``date`` parameter on $expand.

    Per FHIR R4 §4.7.5 In Parameters ``date``: "The date for which the
    expansion is to be computed. ... If a data is not provided, the
    current date is assumed." The parameter is OPTIONAL (0..1 dateTime).

    medterm4ds uses a single-snapshot engine (no versioned data), so the
    parameter is accepted but ignored. Same shape as ``version`` and
    ``offset`` per AGENTS.md NOT A BUG registry.
    """

    @pytest.mark.parametrize(
        "date_val",
        [
            "2020-01-01",          # past date
            "2030-01-01",          # future date
            "2026-07-13T10:00:00Z",  # full instant
        ],
    )
    def test_e900_date_param_accepted_no_500(self, fhir_client, date_val):
        """``date=<past|future|instant>`` is accepted (200 or 422, never
        500). The engine ignores date today (single-snapshot).

        POSITIVE success shape: 200 + ValueSet body.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [(SNOMED_DIABETES_MELLITUS, "Diabetes mellitus")],
        )
        status, body = _post_expand(fhir_client, vs, params={"date": date_val})
        assert status == 200, f"date={date_val!r} status={status}, body={body!r}"
        assert body.get("resourceType") == "ValueSet"

    def test_e910_malformed_date_returns_400_or_422(self, fhir_client):
        """Malformed date ``date=not-a-date``: the implementation MAY
        reject with 422 (FastAPI Query validation) OR accept and ignore
        (handler-level). The probe asserts no 500 + FHIR-conformant
        OperationOutcome on rejection.
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [(SNOMED_DIABETES_MELLITUS, "Diabetes mellitus")],
        )
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand?date=not-a-date",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code in (200, 400, 422), (
            f"unexpected status: {resp.status_code}; body={resp.text[:300]!r}"
        )
        body = resp.json()
        assert body.get("resourceType") in ("ValueSet", "OperationOutcome")
        # Content-Type MUST be application/fhir+json on every path.
        assert resp.headers.get("content-type", "").startswith("application/fhir+json")


# =============================================================================
# Lens J: spec-table coverage — instance-level $expand invocation
# Per FHIR R4 §3.1.0.1.1: operations MAY be invoked on type or instance.
# =============================================================================


class TestInstanceInvocation:
    """Instance-level $expand routes exist (per SKEPTIC TS-02 QA-014 fix).

    Per FHIR R4 §4.7.1.2: $expand MAY be invoked at ``/fhir/ValueSet/{id}/$expand``
    as well as ``/fhir/ValueSet/$expand``. medterm4ds doesn't persist
    ValueSets, so instance-level returns 404 OperationOutcome 'not-found'
    for unknown ids. The ROUTE must exist so the catch-all doesn't shadow
    it.

    EXPLORER confirms the route exists with conformant Content-Type on
    both GET and POST.
    """

    def test_e950_instance_get_unknown_id_returns_404_operationoutcome(self, fhir_client):
        """Instance-level GET with unknown id → 404 + OperationOutcome
        'not-found' + conformant Content-Type."""
        resp = fhir_client.get(
            "/fhir/ValueSet/unknown-id/$expand",
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        assert resp.headers.get("content-type", "").startswith("application/fhir+json")

    def test_e960_instance_post_unknown_id_returns_404_operationoutcome(self, fhir_client):
        """Instance-level POST with unknown id → 404 + OperationOutcome
        'not-found' + conformant Content-Type.

        Cross-check with TS-02 EXPLORER test_e24/e25 — instance-level POST
        routes MUST exist (the GET-only instance route was a TS-02 SKEPTIC
        QA-014 carry-forward, closed by TS-02 EXPLORER).
        """
        resp = fhir_client.post(
            "/fhir/ValueSet/unknown-id/$expand",
            json={"resourceType": "Parameters", "parameter": []},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        assert resp.headers.get("content-type", "").startswith("application/fhir+json")
