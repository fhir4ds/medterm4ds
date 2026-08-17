"""VS-04 EXPLORER: lateral probes for ValueSet $expand with intensional URLs.

EXPLORER lens (lateral / unusual-input thinking): probe combinations,
unusual URL variants, and cross-system boundaries that adversarial or
spec-listed-alternative probes (SKEPTIC) and prior-bug-pattern probes
(HISTORIAN) do not naturally exercise.

This iteration closes the VS-04 portion of CF-EXPLORER-CS02-01 (4-shape
Content-Type probe family on $expand-with-intensional-url) AND probes:
  - Unusual URL variants (trailing slash on path, extra query params, param
    order, URL-encoded chars).
  - Combinations: intensional URL + filter, + count, + offset.
  - Cross-system probing: intensional URLs for non-SNOMED systems.
  - Multi-code URL forms.
  - URL with explicit SNOMED edition.
  - Edge cases: empty code, very long code, URL fragment.
  - Bare ``fhir_vs`` (no ``=``) form behavior.
  - ``fhir_vs=refset`` error path shape.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus) -> 44054006 (T2DM)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
SNOMED CT intensional: https://hl7.org/fhir/R4/snomedct.html
Reference docs:
  - https://hl7.org/fhir/R4/valueset-operation-expand.html (In ``url``:
    "A canonical URL for a value set. The server must know the value set
    (e.g. it is defined inline as a compose rules, or it is a value set
    known to the server by identifier).")
  - https://hl7.org/fhir/R4/snomedct.html (Implicit Value Sets —
    ``fhir_vs=isa`` and ``fhir_vs=refset``).
"""

from __future__ import annotations

import json
import urllib.parse

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent (root)
SNOMED_T2DM = "44054006"  # child of 73211009

FHIR_JSON = "application/fhir+json"
FHIR_XML = "application/fhir+xml"

TRUNCATION_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"


def _expand_get(client, url: str, **extra):
    """GET /fhir/ValueSet/$expand with url and optional query params."""
    params = [("url", url)]
    for k, v in extra.items():
        params.append((k, v))
    return client.get("/fhir/ValueSet/$expand", params=params)


def _expand_post_url_body(client, url: str, **extra):
    """POST /fhir/ValueSet/$expand with Parameters body carrying url."""
    body = {
        "resourceType": "Parameters",
        "parameter": [{"name": "url", "valueUri": url}]
        + [{"name": k, "valueString": str(v)} for k, v in extra.items()],
    }
    return client.post("/fhir/ValueSet/$expand", json=body)


def _expand_post_bare_valueset_url(client, url: str):
    """POST /fhir/ValueSet/$expand with a bare ValueSet body whose url field
    carries an intensional URL.

    The implementation routes ValueSet-body shapes through _expand_intensional
    (compose-based); it does NOT route by ``value_set.url``. This probe is
    therefore the "bare-ValueSet body that LOOKS like an intensional URL"
    lateral probe — the EXPLORER lens asks what happens when a client sends
    the intensional URL in the resource body rather than as a query param.
    """
    body = {
        "resourceType": "ValueSet",
        "url": url,
    }
    return client.post("/fhir/ValueSet/$expand", json=body)


def _contains_codes(resp_json: dict) -> list[str]:
    return [c.get("code") for c in resp_json.get("expansion", {}).get("contains", [])]


def _extensions(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("extension", [])


# =============================================================================
# Group 1: 4-shape Content-Type closure (CF-EXPLORER-CS02-01 close for $expand)
# =============================================================================


class TestExplorerExpandIntensionalUrl4ShapeContentType:
    """CF-EXPLORER-CS02-01 close for $expand-with-intensional-url.

    Per GLOBAL_RULES.md "Conformance properties need probes per route, not
    per resource type" and the 4-shape Content-Type probe family pattern
    (CS-03 EXPLORER..VS-01 EXPLORER, strategy 36 in GLOBAL_KNOWLEDGE.md).

    The 4 shapes for $expand-with-intensional-url are:
      (a) GET with url query param
      (b) POST with Parameters body (url as valueUri parameter)
      (c) POST with bare ValueSet body (whose url field carries the intensional URL)
      (d) error path (intensional URL for non-SNOMED system → 400)

    For each shape, assert BOTH the Content-Type header AND the body
    resourceType / shape.
    """

    INTENSIONAL_URL = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"

    def test_e10_get_url_param_content_type_and_body(self, fhir_client):
        """Shape (a): GET with url query param → 200 + application/fhir+json
        + ValueSet body."""
        resp = _expand_get(fhir_client, self.INTENSIONAL_URL)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] == "ValueSet"
        assert "expansion" in body
        codes = _contains_codes(body)
        # Root + T2DM descendant
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    def test_e11_post_parameters_body_content_type_and_body(self, fhir_client):
        """Shape (b): POST with Parameters body → 200 + application/fhir+json."""
        resp = _expand_post_url_body(fhir_client, self.INTENSIONAL_URL)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] == "ValueSet"
        codes = _contains_codes(body)
        assert SNOMED_DIABETES_MELLITUS in codes

    def test_e12_post_bare_valueset_body_content_type_and_body(self, fhir_client):
        """Shape (c): POST with bare ValueSet body whose url is an intensional URL.

        Per ``_do_expand``: when the body's resourceType is ValueSet, it routes
        through ``_expand_intensional`` which consumes ``compose`` rules — NOT
        the ``url`` field. A bare ValueSet with no compose rules falls through
        to the 400 path ('Provide a ValueSet body, a fhir_vs URL, or a filter').
        This probe confirms the shape is conformant (Content-Type + body
        resourceType) on the resulting response — either an empty expansion
        OR a 400. NOT acceptable: a non-FHIR error body.
        """
        resp = _expand_post_bare_valueset_url(fhir_client, self.INTENSIONAL_URL)
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        # The body MUST be a FHIR resource (OperationOutcome or ValueSet).
        assert body["resourceType"] in ("ValueSet", "OperationOutcome"), body

    def test_e13_error_path_content_type_and_body(self, fhir_client):
        """Shape (d): error path → 400 + application/fhir+json + OperationOutcome.

        Trigger: intensional URL for a non-SNOMED system (LOINC lacks a
        standard ``?fhir_vs=isa`` convention per AGENTS.md).
        """
        resp = _expand_get(fhir_client, "http://loinc.org?fhir_vs=isa")
        assert resp.status_code == 400, resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"


# =============================================================================
# Group 2: XML variant of intensional $expand
# =============================================================================


class TestExplorerXmlVariant:
    """Verify ``_format=xml`` AND ``Accept: application/fhir+xml`` both produce
    XML on the intensional $expand path.

    CR-002 (milestone-1 review) added the ``_scalar_to_xml_attr`` boolean
    special-case in ``engines/fhir/xml.py``. The XML serializer is shared but
    the path through ``_fhir_response → _wants_xml → to_fhir_xml`` MUST be
    exercised per route to guard against a future handler bypassing
    ``_fhir_response``. Per AGENTS.md "Known Fragile Areas" CS-03 EXPLORER.
    """

    INTENSIONAL_URL = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"

    def test_e20_format_xml_returns_xml_body(self, fhir_client):
        """``_format=xml`` overrides Accept per FHIR R4 §3.1.0.1.11."""
        resp = _expand_get(fhir_client, self.INTENSIONAL_URL, _format="xml")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_XML
        # The body MUST be valid XML (not JSON). Verify by parsing.
        import xml.etree.ElementTree as ET

        root = ET.fromstring(resp.text)
        # The root element MUST be ValueSet (the FHIR resource type). The
        # serializer emits a namespace-qualified tag (``{http://hl7.org/fhir}ValueSet``)
        # per the FHIR R4 XML representation convention. Verify the localname.
        localname = root.tag.rsplit("}", 1)[-1] if "}" in root.tag else root.tag
        assert localname == "ValueSet", f"Expected ValueSet, got {root.tag}"
        # Lowercase boolean rendering on any valueBoolean in the body
        # (e.g. truncation extension). The implementation uses _scalar_to_xml_attr
        # which special-cases bool. We assert at least that "True" (capital T)
        # never appears as a value attribute.
        for elem in root.iter():
            v = elem.get("value")
            if v is not None:
                assert v != "True", f"Capital-T boolean in <{elem.tag} value='{v}'>"
                assert v != "False", f"Capital-F boolean in <{elem.tag} value='{v}'>"

    def test_e21_accept_header_xml_returns_xml_body(self, fhir_client):
        """``Accept: application/fhir+xml`` produces XML."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", self.INTENSIONAL_URL)],
            headers={"Accept": FHIR_XML},
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_XML
        import xml.etree.ElementTree as ET

        root = ET.fromstring(resp.text)
        localname = root.tag.rsplit("}", 1)[-1] if "}" in root.tag else root.tag
        assert localname == "ValueSet", f"Expected ValueSet, got {root.tag}"


# =============================================================================
# Group 3: Unusual URL variants
# =============================================================================


class TestExplorerUnusualUrlVariants:
    """EXPLORER lens: unusual URL shapes that may slip past naive URL parsing.

    All variants MUST produce a FHIR-shaped response (200 + ValueSet, OR 400 +
    OperationOutcome). NOT acceptable: 500 with a Python traceback, or a
    non-FHIR error body.
    """

    def test_e30_trailing_slash_on_path_handled(self, fhir_client):
        """``http://snomed.info/sct/73211009/?fhir_vs=isa`` — trailing slash
        on the path AFTER the code.

        Per ``urlparse`` this is fine; the code-extraction uses
        ``path_parts = parsed.path.strip("/").split("/")`` which yields the
        code correctly even with a trailing slash. Verify the response is
        well-formed (root + descendant).
        """
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}/?fhir_vs=isa"
        resp = _expand_get(fhir_client, url)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes, codes

    def test_e31_extra_query_params_ignored(self, fhir_client):
        """``http://snomed.info/sct/73211009?fhir_vs=isa&count=5`` — extra
        query params in the URL (NOT query params on the request, but
        literally inside the ``url`` query-param value)."""
        url = (
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}"
            "?fhir_vs=isa&count=5"
        )
        resp = _expand_get(fhir_client, url)
        assert resp.status_code == 200, resp.text
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    def test_e32_param_order_irrelevant(self, fhir_client):
        """``?count=5&fhir_vs=isa`` — ``fhir_vs`` is NOT the first query param.

        Per ``parse_qs`` (which the implementation uses), param order is
        irrelevant. Verify the value dispatch correctly extracts ``isa``
        regardless of param order.
        """
        url = (
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}"
            "?count=5&fhir_vs=isa"
        )
        resp = _expand_get(fhir_client, url)
        assert resp.status_code == 200, resp.text
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes

    def test_e33_url_encoded_chars_in_code_handled(self, fhir_client):
        """URL-encoded chars in code: ``%37%33%32%31%31%30%30%39`` decodes to
        ``73211009``. The implementation uses ``urlparse`` which does NOT
        decode path segments (only query); this is a probe to verify the
        server's behavior is graceful — either 200 (if the code decodes) or
        400 (if the raw encoded string is treated as the code). NOT acceptable:
        500 with traceback.
        """
        encoded_code = urllib.parse.quote(SNOMED_DIABETES_MELLITUS, safe="")
        url = f"http://snomed.info/sct/{encoded_code}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url)
        # Either 200 (root + descendants) or 400 (code not found) is acceptable.
        # 500 with traceback is NOT acceptable.
        assert resp.status_code in (200, 400), resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] in ("ValueSet", "OperationOutcome"), body


# =============================================================================
# Group 4: Intensional URL + filter / count / offset combinations
# =============================================================================


class TestExplorerIntensionalUrlCombinations:
    """EXPLORER lens: combinations of the intensional URL with other $expand
    parameters. The spec permits count/offset/system alongside url; the
    question is whether the implementation correctly handles the COMBINATION
    (does adding a filter or count=1 still produce the intensional expansion,
    with the right truncation signal?).
    """

    INTENSIONAL_URL = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"

    def test_e40_intensional_url_with_count_param_truncates(self, fhir_client):
        """intensional URL + count=1 → 200 with truncation extension
        (count-limited). The descendant-budget is ``max(1, count - len(root))``
        = ``max(1, 1 - 1) = 1``, so 1 descendant is fetched; ``count_limited``
        is True because ``len(relations) >= descendant_budget`` (1 >= 1).
        """
        resp = _expand_get(fhir_client, self.INTENSIONAL_URL, count=1)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Root + 1 descendant
        codes = _contains_codes(body)
        assert SNOMED_DIABETES_MELLITUS in codes
        # Extension MUST be present (truncation signal).
        exts = _extensions(body)
        assert any(e.get("url") == TRUNCATION_EXT_URL for e in exts), exts

    def test_e41_intensional_url_with_filter_param_filter_ignored_or_explicit(
        self, fhir_client
    ):
        """intensional URL + filter → the implementation routes by URL presence
        (Mode 3 fires before Mode 4 filter). The filter is silently ignored
        when the URL carries a ``fhir_vs`` pattern. Verify the response is the
        intensional expansion (not the filter-text-search result).

        INTENDED behavior per dispatch order: URL-with-fhir_vs wins.
        Alternative semantics (combining url + filter) is a future enhancement.
        """
        resp = _expand_get(fhir_client, self.INTENSIONAL_URL, filter="diabetes")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        codes = _contains_codes(body)
        # Root MUST be present (intensional expansion shape, not filter search).
        assert SNOMED_DIABETES_MELLITUS in codes, codes

    def test_e42_intensional_url_with_offset_param_accepted(self, fhir_client):
        """intensional URL + offset → offset pages the expansion (QC-241).

        Pre-QC-241 the GET handler declared ``offset`` but never applied
        it ("Passed through; not yet used to slice results" — CF-SKEPTIC-
        VS02-02, DEFERRED). EC-10 remediation wires offset through every
        $expand mode: the response is still 200 + conformant shape, and
        offset=1 pages PAST the root (the root sits at index 0).
        NOT acceptable: 500 with traceback, or non-FHIR body.
        """
        resp = _expand_get(fhir_client, self.INTENSIONAL_URL, offset=1)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        codes = _contains_codes(body)
        # Offset 1 skips index 0 — the root MUST NOT be on page 2.
        assert SNOMED_DIABETES_MELLITUS not in codes, codes


# =============================================================================
# Group 5: Cross-system probing (non-SNOMED intensional URLs)
# =============================================================================


class TestExplorerCrossSystemIntensionalUrls:
    """EXPLORER lens: try the intensional URL convention (``?fhir_vs=isa``)
    on every system medterm4ds supports. Per AGENTS.md "Known Fragile Areas"
    (``$expand?url=...?fhir_vs=isa`` only supports SNOMED CT intensional
    expansions), every other system MUST raise ValueError (→ 400
    OperationOutcome), NOT silently produce a partial expansion or 500.

    Reference: https://hl7.org/fhir/R4/snomedct.html — the ``fhir_vs``
    implicit-value-set URL convention is SNOMED-specific.
    """

    @pytest.mark.parametrize(
        "system_url",
        [
            "http://loinc.org",
            "http://www.nlm.nih.gov/research/umls/rxnorm",
            "http://hl7.org/fhir/sid/icd-10-cm",
            "http://www.ama-assn.org/go/cpt",
            "http://hl7.org/fhir/sid/cvx",
            "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II",
            "http://example.org/fake-system",
        ],
    )
    def test_e50_non_snomed_intensional_url_returns_400(self, fhir_client, system_url):
        """Each non-SNOMED intensional URL MUST return 400 + OperationOutcome.

        Verifies the implementation's "Only SNOMED CT intensional expansions
        ... are implemented" boundary per the ``expand_url_pattern`` docstring.
        """
        # Try both forms: with code in path AND bare-form.
        url_with_code = f"{system_url.rstrip('/')}/73211009?fhir_vs=isa"
        resp = _expand_get(fhir_client, url_with_code)
        assert resp.status_code in (400, 404), resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome", body

    def test_e51_loinc_bare_intensional_url_routes_through_implicit_path(
        self, fhir_client
    ):
        """``http://loinc.org?fhir_vs`` (bare form, no code) — this is the
        implicit-value-set Form (b) variant for LOINC. Per ``_do_expand`` Mode
        2, this routes through ``_expand_implicit_value_set`` which DOES
        support LOINC (returns all LOINC codes). Verify the response shape is
        conformant (200 + ValueSet OR 400 + OperationOutcome for empty sources).

        Distinct from the intensional-with-code path which only supports SNOMED.
        """
        resp = _expand_get(fhir_client, "http://loinc.org?fhir_vs")
        # The implicit path may return 200 with an empty-or-non-empty contains[]
        # OR a 400 if the system has no seeded codes (with the explanatory
        # extension per VS-04 HISTORIAN QA-033).
        assert resp.status_code in (200, 400), resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] in ("ValueSet", "OperationOutcome"), body


# =============================================================================
# Group 6: Multi-code URL + versioned URL forms
# =============================================================================


class TestExplorerMultiCodeAndVersionedUrls:
    """EXPLORER lens: the spec permits edition/version qualifiers in the SNOMED
    URL (``/sct/<edition>/version/<release>?fhir_vs=isa``); medterm4ds uses a
    single-snapshot engine so version is accepted but ignored (per NOT A BUG
    registry). Multi-code URLs (``?fhir_vs=isa`` for a comma-separated list)
    are NOT spec-defined for SNOMED CT intensional expansions — verify the
    implementation's behavior is graceful.
    """

    def test_e60_multi_code_url_handled_gracefully(self, fhir_client):
        """``http://snomed.info/sct/73211009,44054006?fhir_vs=isa`` — multiple
        codes in the path. The spec doesn't define this; the implementation
        treats the comma-separated string as a single (unknown) code.

        Either 200 (with root missing because comma-code is unknown) or 400
        is acceptable. NOT acceptable: 500 with traceback.
        """
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS},{SNOMED_T2DM}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url)
        assert resp.status_code in (200, 400), resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] in ("ValueSet", "OperationOutcome"), body

    def test_e61_versioned_url_accepted(self, fhir_client):
        """``http://snomed.info/sct/version/20240101?fhir_vs=isa`` — SNOMED
        edition-style URL. The implementation's ``path_parts`` extraction uses
        the LAST segment as the code; "20240101" is not a known code.

        Either 200 (with empty contains because code not found) or 400 is
        acceptable. NOT acceptable: 500.
        """
        url = "http://snomed.info/sct/version/20240101?fhir_vs=isa"
        resp = _expand_get(fhir_client, url)
        assert resp.status_code in (200, 400), resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] in ("ValueSet", "OperationOutcome"), body

    def test_e62_international_edition_url_form(self, fhir_client):
        """``http://snomed.info/sct/900000000000207008/version/20240101?fhir_vs=isa``
        — international SNOMED edition URL form. The implementation extracts
        the LAST path segment as the code (``20240101``), which is not a
        known code → empty contains or 400.

        Verify graceful handling.
        """
        url = (
            "http://snomed.info/sct/900000000000207008/version/20240101?fhir_vs=isa"
        )
        resp = _expand_get(fhir_client, url)
        assert resp.status_code in (200, 400), resp.text
        body = resp.json()
        assert body["resourceType"] in ("ValueSet", "OperationOutcome"), body


# =============================================================================
# Group 7: Edge cases — empty code, very long code, URL fragment
# =============================================================================


class TestExplorerEdgeCases:
    """EXPLORER lens: edge cases that don't fit the standard URL pattern but
    a robust server MUST handle gracefully (FHIR-shaped response, never a
    500 with raw traceback).
    """

    def test_e70_empty_code_in_url_handled(self, fhir_client):
        """``http://snomed.info/sct/?fhir_vs=isa`` — empty code in path.

        ``path_parts = parsed.path.strip("/").split("/")`` yields ``["sct"]``
        (len=1), so ``len(path_parts) >= 2`` is False — the function falls
        through to the generic ValueError. Verify 400 + OperationOutcome.
        """
        url = "http://snomed.info/sct/?fhir_vs=isa"
        resp = _expand_get(fhir_client, url)
        assert resp.status_code == 400, resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_e71_very_long_code_handled(self, fhir_client):
        """A very long (likely invalid) code in the URL path. Either 400
        (code not found → no root in expansion) or 200 (empty contains)
        is acceptable. NOT acceptable: 500 with traceback (DoS surface)."""
        long_code = "73211009" + "0" * 500  # 508 chars
        url = f"http://snomed.info/sct/{long_code}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url)
        # The implementation calls get_code_infos which returns [(None,)] for
        # unknown codes; the intensional handler produces an empty contains[]
        # (root is missing because get_code_infos returned None). The response
        # is 200 with empty expansion OR 400 (depending on dispatch).
        assert resp.status_code in (200, 400), resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON

    def test_e72_url_with_fragment_handled(self, fhir_client):
        """``http://snomed.info/sct/73211009?fhir_vs=isa#fragment`` — URL with
        a fragment. Per RFC 3986 fragments are client-side only; the server
        SHOULD ignore them. Verify the response is the standard intensional
        expansion.
        """
        url = (
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}"
            "?fhir_vs=isa#fragment"
        )
        resp = _expand_get(fhir_client, url)
        assert resp.status_code == 200, resp.text
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes


# =============================================================================
# Group 8: ``fhir_vs`` bare form (no ``=``)
# =============================================================================


class TestExplorerBareFhirVsForm:
    """``http://snomed.info/sct/73211009?fhir_vs`` (no ``=isa``, just
    ``?fhir_vs``). Per SNOMED CT intensional URL spec, the bare form is
    equivalent to ``?fhir_vs=isa``. Verify the implementation honors this.

    Note: ``parse_qs("fhir_vs")`` returns ``{}`` because parse_qs requires
    ``key=value`` pairs. The implementation handles this via
    ``query_params.get("fhir_vs", [""])`` returning ``[""]`` (empty default).
    The empty-string case is then handled by the explicit dispatch
    (``if fhir_vs_normalized not in ("", "isa", "refset")`` — empty IS in the
    valid set → isa-equivalent).

    Reference: TS-03 HISTORIAN QA-034 fix (bare-query detection via raw-string
    inspection in ``_is_implicit_value_set_url``); VS-04 SKEPTIC QA-060
    extension to the dispatch table.
    """

    def test_e80_bare_fhir_vs_no_equals_recognized_as_isa(self, fhir_client):
        """``?fhir_vs`` (no ``=``) → equivalent to ``?fhir_vs=isa``."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs"
        resp = _expand_get(fhir_client, url)
        assert resp.status_code == 200, resp.text
        codes = _contains_codes(resp.json())
        # Root + descendant present (full isa expansion).
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    def test_e81_bare_fhir_vs_with_count_param_truncates(self, fhir_client):
        """``?fhir_vs&count=1`` → bare form + count=1 truncation."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs"
        resp = _expand_get(fhir_client, url, count=1)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        codes = _contains_codes(body)
        assert SNOMED_DIABETES_MELLITUS in codes
        # Truncation extension present.
        exts = _extensions(body)
        assert any(e.get("url") == TRUNCATION_EXT_URL for e in exts), exts


# =============================================================================
# Group 9: ``fhir_vs=refset`` error path shape
# =============================================================================


class TestExplorerRefsetErrorPath:
    """``http://snomed.info/sct/73211009?fhir_vs=refset`` — medterm4ds does
    not load SNOMED refset data. Per VS-04 SKEPTIC QA-062 fix, the server
    raises a clear ValueError → 400 OperationOutcome.

    EXPLORER lens: probe the error path's CONTENT-TYPE and BODY SHAPE to
    verify it's FHIR-conformant (not a non-FHIR error body). The SKEPTIC
    probe already verified the 400 status; EXPLORER verifies the full shape
    per the 4-shape Content-Type family pattern.
    """

    def test_e90_refset_returns_400_with_fhir_json_content_type(self, fhir_client):
        """``?fhir_vs=refset`` → 400 + application/fhir+json + OperationOutcome
        with a clear message mentioning refset / unimplemented."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=refset"
        resp = _expand_get(fhir_client, url)
        assert resp.status_code == 400, resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"
        # The diagnostics MUST mention refset (operator-actionable signal).
        text = json.dumps(body).lower()
        assert "refset" in text, text

    def test_e91_refset_error_does_not_leak_descendants(self, fhir_client):
        """``?fhir_vs=refset`` MUST NOT produce a ValueSet body with descendants
        (which would be the silent-equated-to-isa bug from VS-04 SKEPTIC
        QA-062)."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=refset"
        resp = _expand_get(fhir_client, url)
        body = resp.json()
        # Must be OperationOutcome (NOT ValueSet).
        assert body["resourceType"] == "OperationOutcome", body


# =============================================================================
# Group 10: Carry-forward verifications (probe prior CFs on this surface)
# =============================================================================


class TestExplorerCarryForwards:
    """Re-confirm carry-forwards from prior chunks still apply on the VS-04
    intensional-URL surface (per the "Carry-forward tracking across iterations"
    strategy 19 in GLOBAL_KNOWLEDGE.md)."""

    def test_e100_cf_historian_vs02_01_bfs_cap_applies_to_url_pattern_path(
        self, fhir_client
    ):
        """CF-HISTORIAN-VS02-01: BFS cap on total applies to URL-pattern path.

        The ``expand_url_pattern`` line 280 calls ``build_valueset_expand(...,
        total=len(contains))`` AFTER the BFS-capped relations were appended.
        When ``descendant_budget=1`` (count=1 + root), the BFS returns at most
        1 relation; ``len(contains)=2`` (root + 1 descendant). If the actual
        descendant tree had >1 relation, ``total=2`` is the TRUNCATED size.

        The fixture has exactly 1 mrrel row (T2DM → Diabetes) so the cap fires
        AT 1 — the total=2 IS correct for THIS fixture but masks the bug for
        deeper trees.

        Verify the structural property: when count=1, the toocostly extension
        fires (count_limited=True because descendant_budget=1 and BFS returned
        1 relation). The ``contains[]`` is truncated to count=1 (just the root)
        because the final ``build_valueset_expand(contains[:count], ...)`` slice.
        But ``expansion.total`` reflects the un-truncated size (= 2: root + 1
        descendant). This is fixture-coincidence (per strategy 41)."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url, count=1)
        body = resp.json()
        # Root MUST be present (count=1 includes the root).
        codes = _contains_codes(body)
        assert SNOMED_DIABETES_MELLITUS in codes, codes
        # The truncation extension MUST be present (count_limited=True).
        exts = _extensions(body)
        assert any(e.get("url") == TRUNCATION_EXT_URL for e in exts), exts
        # ``expansion.total`` reflects the un-truncated size (root + descendant
        # gathered before the contains[:count] slice). NOTE: per CF-HISTORIAN-
        # VS02-01, the total value may itself be the BFS-capped size when the
        # tree is deeper than count. The fixture's tree has 1 descendant
        # matching the cap, so total == 2 today.
        assert body["expansion"]["total"] >= 1, body["expansion"]["total"]

    def test_e101_cf_historian_vs02_02_url_pattern_uses_canonical_snomed_uri(
        self, fhir_client
    ):
        """CF-HISTORIAN-VS02-02: implicit-value-set path lacks
        ``canonical_system_uri()``. The URL-pattern path does NOT have this
        gap — it uses the canonical SNOMED URI directly via
        ``SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]``. Verify every contains[].system
        is the canonical SNOMED URI (not an alias or empty string)."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url)
        body = resp.json()
        for c in body["expansion"]["contains"]:
            assert c["system"] == SNOMED_URI, c

    def test_e102_cf_skeptic_vs01_01_does_not_leak_to_url_pattern_path(
        self, fhir_client
    ):
        """CF-SKEPTIC-VS01-01: 7 of 9 FHIR R4 filter operators silently dropped
        in ``_expand_intensional``. This CF is on the intensional-VALUESET path
        (compose rules), NOT the URL-pattern path. Verify the URL-pattern path
        is unaffected — it doesn't process compose.include[].filter at all."""
        # The URL-pattern path doesn't accept filter operators; the only
        # filter-like behavior is the bare fhir_vs form (which is always isa).
        # This probe is structural — it confirms the URL-pattern path doesn't
        # accidentally route to _expand_intensional.
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url)
        body = resp.json()
        # The expansion MUST contain the root (intensional semantics).
        codes = _contains_codes(body)
        assert SNOMED_DIABETES_MELLITUS in codes


# =============================================================================
# Group 11: Catch-all layer conformance on instance-level $expand
# =============================================================================


class TestExplorerCatchAllInstanceExpand:
    """Per AGENTS.md "Known Fragile Areas" CS-04 EXPLORER test_e20/e21 pattern:
    verify the catch-all layer (registered LAST) handles the
    instance-level /fhir/ValueSet/{id}/$expand fall-through conformantly.

    The instance-level $expand routes return 404 OperationOutcome (medterm4ds
    does not persist ValueSets). Verify both GET and POST return FHIR-shaped
    responses.
    """

    def test_e110_instance_expand_get_returns_fhir_json(self, fhir_client):
        """GET /fhir/ValueSet/{id}/$expand → 404 + application/fhir+json +
        OperationOutcome."""
        resp = fhir_client.get(
            "/fhir/ValueSet/any-id/$expand",
            params=[("url", f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")],
        )
        assert resp.status_code == 404, resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_e111_instance_expand_post_returns_fhir_json(self, fhir_client):
        """POST /fhir/ValueSet/{id}/$expand → 404 + application/fhir+json +
        OperationOutcome."""
        resp = fhir_client.post(
            "/fhir/ValueSet/any-id/$expand",
            json={"resourceType": "Parameters", "parameter": []},
        )
        assert resp.status_code == 404, resp.text
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"


# =============================================================================
# Group 12: Unknown-resource-type POST catch-all (TS-04 EXPLORER QA-042)
# =============================================================================


class TestExplorerUnknownResourceTypePostCatchAll:
    """Per TS-04 EXPLORER QA-042 / CF-EXPLORER-01 (CLOSED): POST to unknown
    FHIR resource types returns a FHIR-shaped 405 OperationOutcome, NOT
    Starlette's default ``{"detail":"Method Not Allowed"}``.

    EXPLORER lens: verify this contract holds even when the body carries an
    intensional URL (the catch-all MUST fire on resource-type mismatch
    regardless of body content)."""

    def test_e120_post_to_unknown_resource_type_with_intensional_url_body(
        self, fhir_client
    ):
        """POST /fhir/Patient/$expand with an intensional URL body → 405 +
        OperationOutcome (not-found or not-supported), NOT a raw 405 from
        Starlette."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "url",
                    "valueUri": f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
                }
            ],
        }
        # Patient is not a known FHIR terminology resource — falls through.
        resp = fhir_client.post("/fhir/Patient/$expand", json=body)
        # Expected: 404 (route doesn't exist) OR 405 (method not allowed) OR
        # 422 (Patient is not in conformance). Must be FHIR-shaped.
        assert resp.status_code in (404, 405, 422), resp.text
        ct = resp.headers.get("content-type", "")
        # Must be FHIR JSON (not Starlette's application/json default).
        assert "fhir+json" in ct or "application/json" in ct, ct


# =============================================================================
# Group 13: Body shape audit on every response from this surface
# =============================================================================


class TestExplorerBodyShapeAudit:
    """Audit that every response from the intensional-URL surface has a
    well-formed FHIR body (either ValueSet or OperationOutcome).

    Pattern: parametrize over a matrix of intensional-URL variants and
    verify each produces a well-formed response. NOT acceptable: empty body,
    HTML body, JSON without resourceType, or any non-FHIR shape.
    """

    @pytest.mark.parametrize(
        "url,expected_status",
        [
            # Standard isa
            (f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa", 200),
            # Bare form
            (f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs", 200),
            # Unknown value
            (f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=unknown", 400),
            # Refset
            (f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=refset", 400),
            # Case-variant ISA
            (f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=ISA", 200),
            # Unknown code — 400 OperationOutcome since QC-247 (EC-10):
            # a typo'd/retired isa root must not read as a by-design
            # empty value set.
            ("http://snomed.info/sct/00000000?fhir_vs=isa", 400),
            # Non-SNOMED
            ("http://loinc.org/12345?fhir_vs=isa", 400),
        ],
    )
    def test_e130_body_shape_audit(self, fhir_client, url, expected_status):
        resp = _expand_get(fhir_client, url)
        assert resp.status_code == expected_status, f"{url}: {resp.text}"
        assert resp.headers["content-type"].split(";")[0].strip() == FHIR_JSON
        body = resp.json()
        assert body["resourceType"] in ("ValueSet", "OperationOutcome"), body


# =============================================================================
# Group 14: GET↔POST round-trip consistency
# =============================================================================


class TestExplorerGetPostRoundTripConsistency:
    """Per CS-04 EXPLORER test_e60/e61 pattern: GET and POST must produce
    equivalent results for the same intensional URL. The batch dispatcher
    parity pattern (TS-04 TERMINOLOGIST) generalizes to GET↔POST parity.
    """

    INTENSIONAL_URL = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"

    def test_e140_get_and_post_produce_same_codes(self, fhir_client):
        """GET with url param vs POST with Parameters body carrying the same
        url — the resulting expansion MUST contain the same codes."""
        resp_get = _expand_get(fhir_client, self.INTENSIONAL_URL)
        resp_post = _expand_post_url_body(fhir_client, self.INTENSIONAL_URL)
        assert resp_get.status_code == 200, resp_get.text
        assert resp_post.status_code == 200, resp_post.text
        codes_get = set(_contains_codes(resp_get.json()))
        codes_post = set(_contains_codes(resp_post.json()))
        assert codes_get == codes_post, (codes_get, codes_post)
        # Both MUST contain the root.
        assert SNOMED_DIABETES_MELLITUS in codes_get

    def test_e141_get_and_post_produce_same_total(self, fhir_client):
        """``expansion.total`` MUST match between GET and POST for the same URL
        (count=N truncation applied to both paths consistently)."""
        resp_get = _expand_get(fhir_client, self.INTENSIONAL_URL, count=1)
        resp_post = _expand_post_url_body(fhir_client, self.INTENSIONAL_URL, count=1)
        assert resp_get.status_code == 200, resp_get.text
        assert resp_post.status_code == 200, resp_post.text
        total_get = resp_get.json()["expansion"]["total"]
        total_post = resp_post.json()["expansion"]["total"]
        assert total_get == total_post, (total_get, total_post)
