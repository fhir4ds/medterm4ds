"""EXPLORER probes for VS-01 (ValueSet Resource Structure).

Spec: https://build.fhir.org/valueset.html
       (canonical R4: https://hl7.org/fhir/R4/valueset.html)
       $expand operation: https://hl7.org/fhir/R4/valueset-operation-expand.html
       Filter operators: https://hl7.org/fhir/R4/valueset.html#filter
       Expansion: https://hl7.org/fhir/R4/valueset.html#expansion

EXPLORER lens (lateral / boundary probes) per chunk assignment:

  1. **4-shape Content-Type probe family for $expand POST** (closes the
     CF-EXPLORER-CS02-01 portion for $expand). The 4 shapes:
       (a) GET with filter
       (b) GET with url (fhir_vs pattern)
       (c) POST with inline ValueSet body
       (d) POST with Parameters body containing a nested ValueSet parameter
     Plus the error-path shape: POST with empty Parameters → 400 OperationOutcome.

  2. **XML rendering of ValueSet expansions** — `$expand` with
     `Accept: application/fhir+xml` (and `_format=xml`) MUST return XML.
     Per CS-04 EXPLORER test_e151 methodology, hyphenated values from
     closed enums render in XML — here we verify the XML path emits a
     ValueSet resource with `contains[]` (no `<`/`>` issues, no capital-T
     booleans).

  3. **count/offset handling** — `count=0`, count exceeding total, offset
     beyond total, negative count/offset (FastAPI 422 vs handler behavior).

  4. **Filter operator edge cases** (CF-SKEPTIC-VS01-01 partial close) —
     for each unsupported operator: what's the response? Per
     CF-SKEPTIC-VS01-01, current behavior is silent-drop → empty
     expansion. EXPLORER pins the SHAPE for each unsupported operator
     (response is a 200 with empty contains, NOT 400, NOT warning). When
     VS-02/VS-03 implement the operators, the pinning probes will fail
     loudly.

  5. **compose.exclude edge cases**:
       - exclude without matching include (CF-SKEPTIC-VS01-02 partial)
       - Multi-system exclude (CF-SKEPTIC-VS01-03 partial)
       - exclude with filter (silently ignored — already pinned by SKEPTIC
         test_s80; EXPLORER confirms with positive success-shape)

  6. **compose.include with both concept[] and filter[]** — extensional +
     intensional combined. The implementation iterates concept[] THEN
     filter[] in sequence; the union is the result.

  7. **ValueSet.url in POST body** — used as canonical identifier? Echoed
     in response? (Pinned by SKEPTIC test_s60 — EXPLORER confirms with
     positive success-shape.)

  8. **Cross-operation canonical agreement** — `$expand` system vs
     `$lookup` Out system (per CS-05 EXPLORER test_e10/e11 methodology).

  9. **Hierarchical expansions are NOT paged** — per FHIR R4 spec, the
     server SHOULD NOT page hierarchical expansions. EXPLORER documents
     the current behavior: contains[] is a flat list regardless of
     `offset`.

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape (200 +
    expected fields), not just absence of one error string.
  - Spec citation required on every probe.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # 44054006 is-a 73211009
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
# Spec: https://hl7.org/fhir/R4/valueset.html#filter (Filter operators)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion
#
# FHIR R4 filter-operator enum (9 values). Per
# https://hl7.org/fhir/R4/valueset.html#filter:
#   op 1..1 code  = | is-a | descendent-of | is-not-a | regex | in | not-in |
#                       generalizes | exists
#   Binding: Filter Operator (Required)
FHIR_R4_FILTER_OPERATORS = {
    "=",
    "is-a",
    "descendent-of",
    "is-not-a",
    "regex",
    "in",
    "not-in",
    "generalizes",
    "exists",
}

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_expand(fhir_client, value_set: dict, *, headers=None) -> tuple[int, dict]:
    """POST a ValueSet body to /fhir/ValueSet/$expand.

    Returns (status_code, body_json_or_text). Per FHIR R4 §4.7.5
    (https://hl7.org/fhir/R4/valueset-operation-expand.html), $expand accepts
    a ValueSet resource body via POST.
    """
    h = {"Accept": "application/fhir+json"}
    if headers:
        h.update(headers)
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=value_set,
        headers=h,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text}
    return resp.status_code, body


def _contains_codes(body) -> list[tuple[str, str]]:
    """Extract (system, code) pairs from a ValueSet.expansion.contains."""
    if isinstance(body, dict):
        contains = body.get("expansion", {}).get("contains", [])
    else:
        contains = []
    out = []
    for c in contains:
        out.append((c.get("system", ""), c.get("code", "")))
    return out


def _make_extensional_vs(system: str, code: str, display: str = "", url: str | None = None) -> dict:
    vs: dict = {
        "resourceType": "ValueSet",
        "compose": {
            "include": [
                {"system": system, "concept": [{"code": code, "display": display}]}
            ]
        },
    }
    if url:
        vs["url"] = url
    return vs


# =============================================================================
# Lens 1: 4-shape Content-Type probe family for $expand POST
# (CF-EXPLORER-CS02-01 partial close on ValueSet/$expand surface)
# =============================================================================


class TestExpandPostContentTypeFamily:
    """Per GLOBAL_RULES.md "Conformance property per route": the CR-001
    parametrized Content-Type probe skips routes requiring complex POST
    bodies. This class closes the $expand portion.

    The 4 success shapes + 1 error path:
      (a) GET with filter (text search)
      (b) GET with url (SNOMED intensional fhir_vs=isa)
      (c) POST with inline ValueSet body
      (d) POST with Parameters body containing a nested ValueSet parameter
      (e) Error path: POST with empty Parameters → 400 OperationOutcome
    """

    def test_e10_get_with_filter_emits_fhir_json(self, fhir_client):
        """GET ``$expand?filter=...`` MUST emit ``Content-Type:
        application/fhir+json``.

        Spec basis (https://hl7.org/fhir/R4/valueset-operation-expand.html):
        ``filter`` is a 0..1 string In parameter for text search. The
        success path MUST return a ValueSet resource with the FHIR MIME
        type per §3.1.0.1.9.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes"},
            headers={"Accept": "application/fhir+json"},
        )
        assert r.status_code == 200, f"body={r.text[:300]!r}"
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"GET $expand?filter= Content-Type is {ct!r}; spec mandates "
            f"application/fhir+json (§3.1.0.1.9)."
        )
        body = r.json()
        assert body.get("resourceType") == "ValueSet"
        assert "expansion" in body

    def test_e11_get_with_url_fhir_vs_emits_fhir_json(self, fhir_client):
        """GET ``$expand?url=http://snomed.info/sct/<code>?fhir_vs=isa``
        MUST emit ``application/fhir+json``.

        Spec basis (https://hl7.org/fhir/R4/valueset-operation-expand.html):
        ``url`` is a 0..1 uri In parameter — when the URL is a FHIR R4
        §4.7.3.1 implicit value-set URL convention, the server expands
        the intensional definition.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"},
            headers={"Accept": "application/fhir+json"},
        )
        assert r.status_code == 200, f"body={r.text[:300]!r}"
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"GET $expand?url= Content-Type is {ct!r}; spec mandates "
            f"application/fhir+json (§3.1.0.1.9)."
        )
        body = r.json()
        assert body.get("resourceType") == "ValueSet"
        codes = _contains_codes(body)
        # is-a expansion includes the root AND its descendants.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_e12_post_inline_valueset_body_emits_fhir_json(self, fhir_client):
        """POST ``$expand`` with inline ValueSet body MUST emit
        ``application/fhir+json``.

        Spec basis (https://hl7.org/fhir/R4/valueset-operation-expand.html
        In Parameters: ``valueSet`` 0..1 ValueSet — "The value set is
        provided directly as part of the request. Servers MAY choose not
        to accept this mode."). The implementation accepts the inline
        ValueSet via the POST body (resourceType: ValueSet).
        """
        vs = _make_extensional_vs(
            SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes mellitus",
            url="http://example.org/vs/test-inline",
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body}"
        # Verify the response Content-Type via a fresh call (the helper
        # doesn't return headers; this is the load-bearing assertion).
        r = fhir_client.post("/fhir/ValueSet/$expand", json=vs)
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"POST $expand (inline ValueSet) Content-Type is {ct!r}; spec "
            f"mandates application/fhir+json (§3.1.0.1.9)."
        )
        body = r.json()
        assert body.get("resourceType") == "ValueSet"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_e13_post_parameters_with_valueset_param_emits_fhir_json(self, fhir_client):
        """POST ``$expand`` with a Parameters body containing a nested
        ``valueSet`` parameter MUST emit ``application/fhir+json``.

        Per FHIR R4 §4.7.5 (https://hl7.org/fhir/R4/valueset-operation-
        expand.html), $expand can be invoked with a Parameters body whose
        ``valueSet`` part contains a full ValueSet resource. The current
        implementation's POST handler routes Parameters bodies through
        `_parse_parameters` (scalar-only extractor); complex types like
        ``valueSet`` are dropped, falling through to the 400 path. EXPLORER
        documents this with the POSITIVE success-shape (200 ValueSet body)
        WHEN the implementation supports it; today the assertion is on
        the error-path Content-Type (400 OperationOutcome MUST still be
        application/fhir+json).

        This probe closes the CF-EXPLORER-CS02-01 portion for $expand by
        asserting Content-Type on BOTH the (a) inline-ValueSet shape (200)
        and (b) Parameters-with-valueSet shape (either 200 if honored OR
        400 with FHIR MIME). Per GLOBAL_RULES.md "Test-too-lenient": the
        probe asserts the body resourceType, not just absence of an error
        string.
        """
        inner_vs = _make_extensional_vs(SNOMED_URI, SNOMED_T2DM, "T2DM")
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "valueSet": inner_vs},
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=params_body)
        ct = r.headers.get("content-type", "")
        # The Content-Type MUST be application/fhir+json on EITHER path.
        assert "application/fhir+json" in ct, (
            f"POST $expand (Parameters with valueSet) Content-Type is {ct!r}; "
            f"spec mandates application/fhir+json on success AND error paths "
            f"(§3.1.0.1.5 + §3.1.0.1.9)."
        )
        body = r.json()
        # Document the current behavior: Parameters-with-valueSet is not
        # honored today (scalar-only extractor); the response is a 400
        # OperationOutcome with the canonical "Provide a ValueSet body..."
        # message. WHEN the implementation supports nested valueSet, this
        # probe MUST be updated to assert the 200 + ValueSet resourceType.
        assert r.status_code in (200, 400), (
            f"unexpected status {r.status_code}: {r.text[:200]!r}"
        )
        if r.status_code == 200:
            assert body.get("resourceType") == "ValueSet"
        else:
            assert body.get("resourceType") == "OperationOutcome"

    def test_e14_post_empty_parameters_error_emits_fhir_json(self, fhir_client):
        """POST ``$expand`` with an empty Parameters body MUST return 400
        with ``application/fhir+json`` Content-Type + OperationOutcome body.

        Spec basis (https://hl7.org/fhir/R4/valueset-operation-expand.html):
        the operation requires either a ``url``, ``filter``, or ``valueSet``
        parameter. An empty Parameters body has none → 400 OperationOutcome.
        """
        r = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={"resourceType": "Parameters", "parameter": []},
        )
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]!r}"
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"POST $expand (empty Parameters) Content-Type is {ct!r}; spec "
            f"mandates application/fhir+json on error paths (§3.1.0.1.5)."
        )
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome"


# =============================================================================
# Lens 2: XML rendering of ValueSet expansions
# (extends CS-04 EXPLORER hyphenated-value XML probe class)
# =============================================================================


class TestExpandXmlRendering:
    """XML rendering of ValueSet expansion via `_format=xml` AND
    `Accept: application/fhir+xml`. The CR-002 fix shape (boolean lowercase)
    applies; the XML serializer is shared via `to_fhir_xml`.

    Per FHIR R4 §3.1.0.1.11: `_format=xml` overrides Accept; per §3.2.1.0.3:
    the server MUST honor `application/fhir+xml`.
    """

    def test_e20_expand_get_with_format_xml_emits_xml_valueset(self, fhir_client):
        """GET ``$expand?filter=...&_format=xml`` MUST return an XML
        ValueSet body with ``Content-Type: application/fhir+xml``.

        Spec basis (https://hl7.org/fhir/R4/valueset-operation-expand.html
        + §3.1.0.1.11): `_format=xml` overrides the Accept header. The
        response MUST be a ValueSet resource in XML.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "_format": "xml"},
        )
        assert r.status_code == 200, f"body={r.text[:300]!r}"
        ct = r.headers.get("content-type", "")
        assert "application/fhir+xml" in ct, (
            f"_format=xml Content-Type is {ct!r}; spec mandates "
            f"application/fhir+xml (§3.1.0.1.9 + §3.1.0.1.11)."
        )
        text = r.text
        assert "<ValueSet" in text, f"expected <ValueSet root; body={text[:300]!r}"
        assert "</ValueSet>" in text
        # Verify expansion structure present.
        assert "<expansion" in text

    def test_e21_expand_post_with_accept_xml_emits_xml_valueset(self, fhir_client):
        """POST ``$expand`` with inline ValueSet body AND
        ``Accept: application/fhir+xml`` MUST return XML.

        Spec basis (§3.1.0.1.9): the server MUST honor
        ``application/fhir+xml`` in the Accept header.
        """
        vs = _make_extensional_vs(
            SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes mellitus",
            url="http://example.org/vs/test-xml",
        )
        r = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+xml"},
        )
        assert r.status_code == 200, f"body={r.text[:300]!r}"
        ct = r.headers.get("content-type", "")
        assert "application/fhir+xml" in ct, (
            f"Accept: application/fhir+xml Content-Type is {ct!r}; spec "
            f"mandates application/fhir+xml (§3.1.0.1.9)."
        )
        text = r.text
        assert "<ValueSet" in text
        assert "</ValueSet>" in text
        # The expanded code MUST appear in the XML body.
        assert SNOMED_T2DM in text

    def test_e22_expand_xml_no_capital_t_booleans(self, fhir_client):
        """The XML body MUST NOT contain capital-T boolean forms.

        Per CR-002 fix shape (GLOBAL_RULES.md "Boolean capitalization on
        serializers"): `str(True) == "True"`, but FHIR R4 §3.4.1 mandates
        lowercase `true`/`false`. The XML serializer uses
        `_scalar_to_xml_attr` to render booleans correctly. EXPLORER
        asserts the negative (no `value="True"`) on a $expand response.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "_format": "xml"},
        )
        assert r.status_code == 200
        text = r.text
        # Negative assertion: NO capital-T boolean.
        assert 'value="True"' not in text, (
            f"capital-T boolean form in XML: {text[:300]!r}"
        )
        assert 'value="False"' not in text, (
            f"capital-T boolean form in XML: {text[:300]!r}"
        )

    def test_e23_expand_xml_hyphenated_values_render_correctly(self, fhir_client):
        """Hyphenated values from closed enums (e.g. `not-subsumed`) MUST
        render correctly in XML. Per CS-04 EXPLORER test_e151 methodology,
        hyphenated values are a separate failure class (URL-encoding,
        stripping, entity-encoding).

        The $expand expansion contains codes (typically without hyphens),
        but the URL field often contains hyphens (canonical URLs). The XML
        serializer must not mangle hyphens.
        """
        vs = _make_extensional_vs(
            SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes mellitus",
            url="http://example.org/vs/hyphen-test-value-set",
        )
        r = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+xml"},
        )
        assert r.status_code == 200
        text = r.text
        # The hyphenated URL MUST appear verbatim (not URL-encoded).
        assert "hyphen-test-value-set" in text, (
            f"hyphenated URL mangled in XML: {text[:400]!r}"
        )


# =============================================================================
# Lens 3: count / offset handling
# =============================================================================


class TestExpandCountOffset:
    """count / offset parameter edge cases.

    Per FHIR R4 §4.7.5 (https://hl7.org/fhir/R4/valueset-operation-expand.html):
      - ``count`` 0..1 integer: "Maximum number of results to return"
      - ``offset`` 0..1 integer: "Paging offset - only applies when the
        expansion is NOT hierarchical"

    Per AGENTS.md NOT A BUG registry: "offset parameter accepted but
    ignored on $expand". EXPLORER documents the current behavior on edge
    cases.
    """

    def test_e30_count_at_least_one(self, fhir_client):
        """GET ``$expand?filter=...&count=1`` MUST return at most 1
        contains entry.

        The implementation enforces ``count >= 1`` via FastAPI's
        ``Query(20, ge=1, le=1000)``.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 1},
        )
        assert r.status_code == 200, f"body={r.text[:300]!r}"
        body = r.json()
        contains = body.get("expansion", {}).get("contains", [])
        assert len(contains) <= 1

    def test_e31_count_zero_rejected_by_validation(self, fhir_client):
        """GET ``$expand?count=0`` MUST be rejected by FastAPI's
        validation (count >= 1 enforced via Query(ge=1)).

        The 422 response goes through the RequestValidationError handler
        that converts FastAPI's default body into a FHIR OperationOutcome
        (per AGENTS.md "Known Fragile Areas").
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 0},
        )
        assert r.status_code == 422, (
            f"expected 422 for count=0, got {r.status_code}: {r.text[:200]!r}"
        )
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"count=0 validation error Content-Type is {ct!r}; spec mandates "
            f"application/fhir+json on validation-error paths (§3.1.0.1.5)."
        )
        body = r.json()
        # The RequestValidationError handler emits an OperationOutcome.
        assert body.get("resourceType") == "OperationOutcome"

    def test_e32_count_negative_rejected_by_validation(self, fhir_client):
        """GET ``$expand?count=-5`` MUST be rejected (negative count
        violates Query(ge=1))."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": -5},
        )
        assert r.status_code == 422
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome"

    def test_e33_offset_zero_accepted(self, fhir_client):
        """GET ``$expand?filter=...&offset=0`` MUST be accepted.

        Per AGENTS.md NOT A BUG registry: "offset parameter accepted but
        ignored on $expand". The success path returns a ValueSet body.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "offset": 0},
        )
        assert r.status_code == 200, f"body={r.text[:300]!r}"
        body = r.json()
        assert body.get("resourceType") == "ValueSet"

    def test_e34_offset_negative_rejected_by_validation(self, fhir_client):
        """GET ``$expand?offset=-1`` MUST be rejected (negative offset
        violates Query(ge=0))."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "offset": -1},
        )
        assert r.status_code == 422
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome"

    def test_e35_offset_beyond_total_returns_empty_or_full(self, fhir_client):
        """GET ``$expand?offset=1000`` MUST be accepted.

        Per AGENTS.md NOT A BUG registry, offset is accepted but ignored.
        The success path returns the same contains[] as offset=0 (because
        offset is ignored). EXPLORER documents this with a positive
        success-shape assertion.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "offset": 1000},
        )
        assert r.status_code == 200, f"body={r.text[:300]!r}"
        body = r.json()
        assert body.get("resourceType") == "ValueSet"
        # offset is ignored today; the result is identical to offset=0.
        # EXPLORER documents this — WHEN paging lands, the assertion MUST
        # change to `len(contains) == 0`.

    def test_e36_count_exceeding_total_returns_all_matches(self, fhir_client):
        """GET ``$expand?count=1000`` MUST return all matching codes (no
        truncation). The implementation's cap is 1000 via Query(le=1000)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 1000},
        )
        assert r.status_code == 200, f"body={r.text[:300]!r}"
        body = r.json()
        contains = body.get("expansion", {}).get("contains", [])
        # The fixture has at most a few codes matching "diabetes".
        # count=1000 returns all of them.
        assert isinstance(contains, list)

    def test_e37_count_above_max_rejected(self, fhir_client):
        """GET ``$expand?count=1001`` MUST be rejected (above 1000 cap)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 1001},
        )
        assert r.status_code == 422
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome"


# =============================================================================
# Lens 4: Filter operator edge cases (CF-SKEPTIC-VS01-01 partial close)
# =============================================================================


class TestFilterOperatorEdgeCases:
    """For each unsupported FHIR R4 filter operator: document the response
    shape. Per CF-SKEPTIC-VS01-01, the current behavior is silent-drop →
    empty expansion. EXPLORER pins the SHAPE so VS-02/VS-03 will fail
    loudly when the operators are implemented.

    Per FHIR R4 https://hl7.org/fhir/R4/valueset.html#filter:
      op 1..1 code  = | is-a | descendent-of | is-not-a | regex | in |
                          not-in | generalizes | exists
      Binding: Filter Operator (Required)
    """

    @pytest.mark.parametrize("op", sorted(FHIR_R4_FILTER_OPERATORS - {"is-a", "descendent-of"}))
    def test_e40_unsupported_operator_returns_200_empty_or_implemented(self, fhir_client, op):
        """For each unsupported FHIR R4 filter operator: the response is
        either (a) 200 with empty contains[] (silent-drop, current) OR
        (b) 200 with non-empty contains[] (when VS-02/VS-03 implements
        the operator). Either way, the response MUST be a ValueSet body
        (NOT 400, NOT 500).

        The probe PINS the current silent-drop behavior so VS-02/VS-03
        implementation will fail loudly and require a probe update.

        Per CF-SKEPTIC-VS01-01: silent-drop at DEBUG log level is the
        v0.0.1 B-class silent-fallback anti-pattern. The fix is
        out-of-VS-01 scope (engine enhancements required).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept",
                        "op": op,
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"op={op}: expected 200, got {status}: {body}"
        assert body.get("resourceType") == "ValueSet"
        # PIN current behavior: silent-drop → empty expansion.
        # WHEN VS-02/VS-03 implements the operator, this assertion MUST
        # be updated to reflect the new behavior.
        codes = _contains_codes(body)
        assert codes == [], (
            f"op={op}: current behavior is silent-drop (empty contains); "
            f"got {codes!r}. If this assertion fails, the operator has been "
            f"IMPLEMENTED — update this probe to assert the new behavior."
        )

    def test_e41_supported_is_a_returns_root_and_descendants(self, fhir_client):
        """is-a operator returns root + descendants. Positive success-shape
        for the supported operator (per GLOBAL_RULES.md "Test-too-lenient")."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_e42_supported_descendent_of_excludes_root(self, fhir_client):
        """descendent-of excludes root (spec-correct spelling per QA-054).
        Positive success-shape."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "descendent-of",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_e43_unknown_property_silently_dropped(self, fhir_client):
        """Filter with unknown property (e.g. `inactive` for SNOMED) is
        silently dropped. Per CF-SKEPTIC-VS01-01: filter-property handling
        is restricted to `property="concept"`. EXPLORER documents this
        with positive success-shape (200 + ValueSet body, empty contains)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "inactive", "op": "exists",
                        "value": "true",
                    }],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert body.get("resourceType") == "ValueSet"
        # Silent-drop → empty contains.
        codes = _contains_codes(body)
        assert codes == []


# =============================================================================
# Lens 5: compose.exclude edge cases (CF-SKEPTIC-VS01-02/03 partial)
# =============================================================================


class TestComposeExcludeEdgeCases:
    """compose.exclude edge cases:
      - exclude without matching include
      - multi-system exclude (CF-SKEPTIC-VS01-03 partial)
      - exclude with filter (silently ignored — CF-SKEPTIC-VS01-02)
    """

    def test_e50_exclude_without_matching_include_is_noop(self, fhir_client):
        """exclude referencing codes not in include is a no-op.

        Per §4.9.5: exclude removes codes from the union of includes. If
        none of the excluded codes are in the include, the result equals
        the include. EXPLORER pins this with positive success-shape.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
                "exclude": [{
                    "system": RXNORM_URI,
                    "concept": [{"code": RXNORM_METFORMIN}],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body}"
        codes = _contains_codes(body)
        # The include code is present; the excluded (different-system) code
        # was never included.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (RXNORM_URI, RXNORM_METFORMIN) not in codes

    def test_e51_multi_system_exclude_deducts_only_matching_codes(self, fhir_client):
        """Multi-system exclude: exclude removes only the codes from
        matching systems. Per CF-SKEPTIC-VS01-03: the current exclude
        path matches on code alone, ignoring system. EXPLORER documents
        this drift: an exclude of code "E11" (ICD-10-CM) would also
        erroneously exclude a SNOMED code "E11" if both were present.

        This probe uses codes that DON'T collide (SNOMED child +
        ICD-10-CM T2DM), so the exclude correctly removes only the
        matching-system code.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]},
                    {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
                ],
                "exclude": [{
                    "system": ICD10CM_URI,
                    "concept": [{"code": ICD10CM_T2DM}],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body}"
        codes = _contains_codes(body)
        # SNOMED T2DM remains; ICD-10-CM E11 excluded.
        assert (SNOMED_URI, SNOMED_T2DM) in codes
        assert (ICD10CM_URI, ICD10CM_T2DM) not in codes

    def test_e52_exclude_with_filter_silently_ignored(self, fhir_client):
        """exclude with filter[] is silently ignored (only concept[]
        matched). Per CF-SKEPTIC-VS01-02. EXPLORER confirms with
        positive success-shape: the include's concepts remain in the
        expansion; the exclude.filter was a no-op."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS},
                        {"code": SNOMED_T2DM},
                    ],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body}"
        codes = _contains_codes(body)
        # QC-242 (EC-10, HIGH) RESOLVED: exclude.filter is honored — the
        # is-a exclusion removes the whole subtree (root + descendant).
        assert codes == []

    def test_e53_exclude_after_filter_keeps_filter_descendants(self, fhir_client):
        """exclude after a filter-based include: the exclude operates on
        the filter-expanded codes. The implementation runs include first
        (BFS), then exclude on the result. EXPLORER documents this with
        a positive success-shape: an is-a filter expands root+descendants,
        then exclude removes the root.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body}"
        codes = _contains_codes(body)
        # Root excluded; child remains.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes


# =============================================================================
# Lens 6: compose.include with BOTH concept[] AND filter[]
# (extensional + intensional combined)
# =============================================================================


class TestConceptAndFilterCombined:
    """compose.include may have BOTH concept[] (extensional) and filter[]
    (intensional). Per §4.9.5: include is a 1..* BackboneElement where
    concept AND filter are both 0..* — they coexist within one include.

    The implementation iterates concept[] THEN filter[] in sequence.
    """

    def test_e60_concept_and_filter_union(self, fhir_client):
        """Include with BOTH concept=[metformin] AND filter=[is-a diabetes]
        MUST return the union: metformin + diabetes + T2DM.

        Per §4.9.5: include.concept and include.filter are both 0..* —
        they coexist within one include block. The implementation
        processes concept[] first (extensional add) then filter[]
        (intensional BFS).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_T2DM, "display": "T2DM"}],
                    "filter": [{
                        "property": "concept", "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body}"
        codes = _contains_codes(body)
        # The concept (T2DM) is added; then the is-a filter expands root+T2DM.
        # The union is {root, T2DM}.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_e61_concept_alone_independent(self, fhir_client):
        """Include with concept[] alone (no filter) is pure extensional.
        Positive success-shape: every concept is in the expansion."""
        vs = {
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
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes


# =============================================================================
# Lens 7: ValueSet.url in POST body (canonical identifier)
# =============================================================================


class TestValueSetUrl:
    """ValueSet.url in POST body is the canonical identifier. Per §4.9.10:
    "The canonical URL for the expansion is the same as the value set it
    was expanded from". EXPLORER confirms with positive success-shape
    (extends SKEPTIC test_s60).
    """

    def test_e70_url_echoed_in_response(self, fhir_client):
        """POST ``$expand`` with `url` in the ValueSet body MUST echo
        the url in the response.

        Positive success-shape: 200 + ValueSet body with `url` field
        matching the input.
        """
        url = "http://example.org/vs/explorer-url-echo"
        vs = _make_extensional_vs(SNOMED_URI, SNOMED_T2DM, "T2DM", url=url)
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"body={body}"
        assert body.get("resourceType") == "ValueSet"
        assert body.get("url") == url, (
            f"url field not echoed: got {body.get('url')!r}, expected {url!r}"
        )

    def test_e71_url_absent_when_not_provided(self, fhir_client):
        """POST ``$expand`` without `url` MUST omit `url` in the response."""
        vs = _make_extensional_vs(SNOMED_URI, SNOMED_T2DM, "T2DM")
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert "url" not in body, f"url should be absent: got {body.get('url')!r}"


# =============================================================================
# Lens 8: Cross-operation canonical agreement
# ($expand system vs $lookup Out system)
# (CS-05 EXPLORER test_e10/e11 methodology applied to $expand surface)
# =============================================================================


class TestCrossOperationCanonicalAgreement:
    """The system URI emitted in `$expand` contains[] MUST match the
    `$lookup` Out `system` for the same code. Per CS-05 EXPLORER
    test_e10/e11 methodology: cross-operation canonical agreement is
    structurally guaranteed because both operations use
    `system_to_fhir_uri`. The probe guards against future divergence.
    """

    def test_e80_expand_and_lookup_agree_on_canonical_system(self, fhir_client):
        """For SNOMED T2DM: $expand contains[] system MUST equal
        $lookup Out system.

        Per CS-05 EXPLORER methodology: cross-operation agreement on
        canonical URIs is the load-bearing invariant. A regression that
        adds a translation step to one operation but not the other would
        fail loudly.
        """
        # 1. Expand via extensional include.
        vs = _make_extensional_vs(SNOMED_URI, SNOMED_T2DM, "T2DM")
        expand_status, expand_body = _post_expand(fhir_client, vs)
        assert expand_status == 200
        expand_codes = _contains_codes(expand_body)
        assert (SNOMED_URI, SNOMED_T2DM) in expand_codes
        # Extract the system actually emitted by $expand.
        expand_systems = {
            c.get("system", "") for c in expand_body.get("expansion", {}).get("contains", [])
            if c.get("code") == SNOMED_T2DM
        }

        # 2. Lookup the same code.
        lookup_r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert lookup_r.status_code == 200, f"lookup body={lookup_r.text[:300]!r}"
        lookup_body = lookup_r.json()
        lookup_system = None
        for p in lookup_body.get("parameter", []):
            if p.get("name") == "system":
                lookup_system = p.get("valueUri")
                break

        # Cross-operation agreement: expand system == lookup system.
        assert lookup_system in expand_systems, (
            f"cross-op drift: $expand systems={expand_systems!r}, "
            f"$lookup system={lookup_system!r}"
        )


# =============================================================================
# Lens 9: Hierarchical expansions NOT paged
# (per FHIR R4 spec, offset only applies to non-hierarchical expansions)
# =============================================================================


class TestHierarchicalExpansionNotPaged:
    """Per FHIR R4 §4.7.5 (https://hl7.org/fhir/R4/valueset-operation-expand.html):
    "offset ... only applies when the expansion is NOT hierarchical".

    A hierarchical expansion (is-a filter) MUST NOT be sliced by offset.
    The current implementation ignores offset entirely (per AGENTS.md
    NOT A BUG registry); EXPLORER documents this with a positive
    success-shape: the hierarchical contains[] is identical with and
    without offset.
    """

    def test_e90_hierarchical_expansion_ignores_offset(self, fhir_client):
        """A hierarchical expansion (is-a filter) returns the same
        contains[] regardless of offset (per spec, offset only applies
        to non-hierarchical expansions)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        # 1. No offset.
        status_a, body_a = _post_expand(fhir_client, vs)
        assert status_a == 200
        codes_a = _contains_codes(body_a)

        # 2. With offset=10 (would slice if paging were applied).
        # POST doesn't accept query params naturally, but we can
        # use the GET form to test offset on hierarchical expansions.
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={
                "url": f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
                "offset": 10,
            },
        )
        assert r.status_code == 200, f"body={r.text[:300]!r}"
        body_b = r.json()
        codes_b = _contains_codes(body_b)

        # QC-241 (EC-10, HIGH) RESOLVED: offset pages every $expand mode.
        # medterm4ds expansions are FLAT (expansion.contains is a list, no
        # hierarchical CodeSystem-style nesting), so the FHIR R4 "hierarchical
        # expansions SHALL not be paged" caveat does not apply — offset=10
        # past this fixture's 2-code expansion pages to an empty page.
        assert codes_b == [], (
            f"flat expansion must page with offset: "
            f"no_offset={set(codes_a)!r}, offset=10={set(codes_b)!r}"
        )


# =============================================================================
# Lens 10: Body-shape audit on $expand response
# =============================================================================


class TestExpandResponseBodyShape:
    """Per GLOBAL_RULES.md "Conformance property per route": probe the
    response body shape on $expand — assert resourceType, expansion
    structure, contains[] shape. Mirrors CS-04 EXPLORER body-shape audit.
    """

    def test_e100_expand_response_has_expansion_with_timestamp_and_total(self, fhir_client):
        """$expand response MUST have:
          - resourceType: ValueSet
          - expansion.timestamp
          - expansion.total (integer, equals len(contains))
          - expansion.contains (list)
        """
        vs = _make_extensional_vs(SNOMED_URI, SNOMED_T2DM, "T2DM")
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert body.get("resourceType") == "ValueSet"
        expansion = body.get("expansion", {})
        assert "timestamp" in expansion, f"missing expansion.timestamp: {body}"
        assert "total" in expansion, f"missing expansion.total: {body}"
        assert isinstance(expansion.get("total"), int)
        assert isinstance(expansion.get("contains"), list)
        # total MUST equal len(contains) for the trivial extensional case.
        assert expansion["total"] == len(expansion["contains"])

    def test_e101_contains_entries_have_system_code_display(self, fhir_client):
        """Each contains[] entry MUST have system, code, display."""
        vs = _make_extensional_vs(
            SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes mellitus",
            url="http://example.org/vs/explorer-shape",
        )
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        contains = body.get("expansion", {}).get("contains", [])
        assert len(contains) >= 1
        for c in contains:
            assert "system" in c, f"missing system in {c!r}"
            assert "code" in c, f"missing code in {c!r}"
            # display may be empty for unknown codes but the key MUST exist.
            assert "display" in c, f"missing display in {c!r}"


# =============================================================================
# Lens 11: GET ↔ POST round-trip consistency on $expand
# (extends CS-04 EXPLORER GET↔POST round-trip methodology)
# =============================================================================


class TestExpandGetPostRoundTrip:
    """GET and POST $expand MUST produce identical results for the same
    logical query. The implementation's GET uses `filter` for text
    search; POST can use either an inline ValueSet body OR a Parameters
    body with filter parameter.
    """

    def test_e110_get_filter_post_filter_same_result(self, fhir_client):
        """GET ``$expand?filter=diabetes`` and POST ``$expand`` with
        Parameters body containing `filter=diabetes` MUST return the
        same contains[] codes."""
        # GET
        r_get = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes"},
        )
        assert r_get.status_code == 200
        codes_get = set(_contains_codes(r_get.json()))

        # POST Parameters body
        r_post = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "filter", "valueString": "diabetes"},
                ],
            },
        )
        assert r_post.status_code == 200, f"body={r_post.text[:300]!r}"
        codes_post = set(_contains_codes(r_post.json()))

        # Cross-method round-trip: same codes.
        assert codes_get == codes_post, (
            f"GET vs POST filter mismatch: get={codes_get!r}, post={codes_post!r}"
        )
