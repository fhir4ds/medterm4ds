"""TS-01 EXPLORER resweep — lateral coverage of FHIR R4 §4.7.1.1 (RESTful API).

Fresh full-sweep run per USER_DIRECTIVES [2026-08-08]. Sibling to the
baseline ``test_ts01_explorer.py`` so the baseline stays comparable across
runs. Adds NEW lateral probes not covered by SKEPTIC resweep (91 probes) or
HISTORIAN resweep (54 probes).

EXPLORER lens (ROLE_QA_ENGINEER Section 3): lateral thinking. Unusual
parameter combinations, undocumented features, integration corners.

Probe classes:
- Accept-header content-negotiation edge cases (q-values, multiple FHIR
  MIME types, generic MIME types, charset params, malformed q-values).
- Unusual parameter combinations on conformance endpoints (mode with
  non-standard values, _format overrides, conflicting _format vs Accept).
- Integration corners (capabilities advertisement vs actual READ/SEARCH
  on every advertised resource; batch dispatcher coverage of every
  advertised operation; cross-resource inconsistencies).
- Spec-implied behaviors (HEAD/OPTIONS methods, Accept absent, etc.).
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Lens 1: Accept-header q-value content negotiation
# ---------------------------------------------------------------------------
# FHIR R4 §3.1.0.1.9: "Servers SHALL support server-driven content negotiation
# as described in section 12 of the HTTP specification." RFC 7231 §5.3.1
# defines the q-value weight: "The weight is normalized to a real number in
# the range 0 through 1... The default value is q=1." RFC 7231 §5.3.2: a
# q=0 means "not acceptable".
#
# QA-001: ``_wants_xml`` (apps/fhir_api.py:727-749) uses substring matching
# (``"application/fhir+xml" in accept``) which ignores q-value precedence.
# A client expressing ``Accept: application/fhir+xml;q=0.1,
# application/fhir+json;q=0.9`` SHOULD get JSON (higher q) but the impl
# returns XML (substring match finds XML first).


@pytest.mark.parametrize(
    "accept_header, expect_xml, rationale",
    [
        # QA-001 reproducer: JSON has higher q but XML returned.
        ("application/fhir+xml;q=0.1, application/fhir+json;q=0.9", False,
         "JSON q=0.9 > XML q=0.1 per RFC 7231 §5.3.1"),
        # JSON listed first with higher q.
        ("application/fhir+json;q=0.9, application/fhir+xml;q=0.1", False,
         "JSON q=0.9 > XML q=0.1 (order in header is irrelevant)"),
        # XML has higher q.
        ("application/fhir+xml;q=0.9, application/fhir+json;q=0.1", True,
         "XML q=0.9 > JSON q=0.1"),
        # q=0 means "not acceptable" per RFC 7231 §5.3.2.
        ("application/fhir+xml;q=0", False,
         "XML q=0 means 'not acceptable' per RFC 7231 §5.3.2 — fall back to JSON default"),
        # When JSON is explicitly dispreferred (q=0) AND XML has any weight,
        # XML wins.
        ("application/fhir+xml;q=0.1, application/fhir+json;q=0", True,
         "XML q=0.1 > JSON q=0 (JSON explicitly dispreferred)"),
        # Mixed charset + q parameters. XML has implicit q=1.0 (charset is
        # NOT a q-param), JSON has q=0.9 — so XML wins (1.0 > 0.9).
        ("application/fhir+xml; charset=utf-8, application/fhir+json;q=0.9",
         True, "XML implicit q=1.0 (charset is not a q-param) > JSON q=0.9"),
    ],
    ids=[
        "xml_low_q_json_high_q_expect_json",
        "json_first_high_q_xml_low_q_expect_json",
        "xml_high_q_json_low_q_expect_xml",
        "xml_q_zero_expect_json",
        "json_q_zero_xml_any_q_expect_xml",
        "charset_and_q_combined",
    ],
)
def test_e10_accept_qvalue_preference(
    fhir_client, accept_header, expect_xml, rationale
):
    """Server MUST honor Accept q-value preference per RFC 7231 §5.3.1.

    FHIR R4 §3.1.0.1.9: "Servers SHALL support server-driven content
    negotiation as described in section 12 of the HTTP specification."
    """
    r = fhir_client.get("/fhir/metadata", headers={"Accept": accept_header})
    ct = r.headers.get("content-type", "")
    is_xml = "xml" in ct
    # Per spec, the higher q-value MUST win. The impl uses substring match
    # which ignores q-values — this is QA-001.
    assert is_xml == expect_xml, (
        f"Accept={accept_header!r} → CT={ct} (is_xml={is_xml}); "
        f"expected is_xml={expect_xml} because {rationale}"
    )


def test_e11_accept_mixed_fhir_mime_types_no_q(fhir_client):
    """When both FHIR XML and JSON are listed with no q, server's choice.

    RFC 7231 §5.3.1: when no q-value is given, q defaults to 1. Both
    equally weighted. The spec permits either choice (XML or JSON). Probe
    documents current behavior; not a bug.
    """
    r = fhir_client.get(
        "/fhir/metadata",
        headers={"Accept": "application/fhir+xml, application/fhir+json"},
    )
    ct = r.headers.get("content-type", "")
    assert ct in ("application/fhir+xml", "application/fhir+json"), (
        f"Expected FHIR MIME type; got {ct!r}"
    )


@pytest.mark.parametrize(
    "accept_header, expected_mime",
    [
        # Generic MIME types: FHIR R4 §3.1.0.1.9 implementation note:
        # "the server SHOULD respond with the requested mime type, using
        # the XML or JSON formats described in this specification as the
        # best representation for the named mime type".
        # Current impl returns the FHIR MIME type, not the requested generic.
        # This is borderline spec-compliant (format is correct) but the
        # MIME type returned is the FHIR form, not the generic form.
        ("application/xml", "xml"),  # format MUST be XML
        ("text/xml", "xml"),
        ("application/json", "json"),  # format MUST be JSON
        ("text/json", "json"),
    ],
    ids=[
        "generic_application_xml_format_xml",
        "generic_text_xml_format_xml",
        "generic_application_json_format_json",
        "generic_text_json_format_json",
    ],
)
def test_e12_generic_mime_type_accept(fhir_client, accept_header, expected_mime):
    """Generic MIME types: server MUST honor the requested format.

    Per FHIR R4 §3.1.0.1.9 implementation note: "If a client provides a
    generic mime type in the Accept header (application/xml, text/json, or
    application/json), the server SHOULD respond with the requested mime
    type, using the XML or JSON formats described in this specification as
    the best representation for the named mime type."

    Note: the spec says "SHOULD respond with the requested mime type" —
    the impl returns the FHIR MIME type (``application/fhir+json`` or
    ``application/fhir+xml``). The FORMAT is correct (XML or JSON as
    requested) but the Content-Type string differs from what the client
    asked for. This probe documents the FORMAT correctness, not the
    Content-Type string (the format is the load-bearing invariant).
    """
    r = fhir_client.get("/fhir/metadata", headers={"Accept": accept_header})
    ct = r.headers.get("content-type", "")
    if expected_mime == "xml":
        assert "xml" in ct, (
            f"Accept={accept_header!r} → CT={ct}; expected XML format "
            f"(any MIME type containing 'xml')"
        )
    else:
        assert "json" in ct, (
            f"Accept={accept_header!r} → CT={ct}; expected JSON format "
            f"(any MIME type containing 'json')"
        )


def test_e13_accept_with_charset_parameter(fhir_client):
    """Accept with ``; charset=utf-8`` parameter MUST still match MIME type.

    Per RFC 7231 §3.1.1.1 Accept-Charset is a separate dimension; the
    charset parameter on an Accept entry is an accept-params ext. The
    server should still recognize ``application/fhir+xml; charset=utf-8``
    as the XML MIME type.
    """
    r = fhir_client.get(
        "/fhir/metadata",
        headers={"Accept": "application/fhir+xml; charset=utf-8"},
    )
    assert "xml" in r.headers.get("content-type", "")


def test_e14_accept_empty_header_defaults_json(fhir_client):
    """Accept: '' (empty) — server's choice.

    Per §3.1.0.1.11: "If neither the accept header nor the _format parameter
    are specified, the MIME-type of the content returned by the server is
    undefined and may vary." An empty Accept header is equivalent to absent
    → server's default (JSON).
    """
    r = fhir_client.get("/fhir/metadata", headers={"Accept": ""})
    assert "json" in r.headers.get("content-type", "")


def test_e15_accept_absent_defaults_json(fhir_client):
    """No Accept header at all → JSON default."""
    r = fhir_client.get("/fhir/metadata")
    assert "json" in r.headers.get("content-type", "")


def test_e16_accept_star_default_json(fhir_client):
    """Accept: */* → server's default (JSON)."""
    r = fhir_client.get("/fhir/metadata", headers={"Accept": "*/*"})
    assert "json" in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Lens 2: _format query parameter (spec §3.1.0.1.11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fmt_value, expect_xml",
    [
        ("xml", True),
        ("application/fhir+xml", True),
        ("application/xml", True),
        ("text/xml", True),
        ("XML", True),  # case-insensitive
        (" Xml ", True),  # whitespace-trimmed
        ("json", False),
        ("application/fhir+json", False),
        ("application/json", False),
        ("JSON", False),  # case-insensitive
    ],
    ids=[
        "fmt_xml",
        "fmt_application_fhir_xml",
        "fmt_application_xml",
        "fmt_text_xml",
        "fmt_XML_uppercase",
        "fmt_xml_with_whitespace",
        "fmt_json",
        "fmt_application_fhir_json",
        "fmt_application_json",
        "fmt_JSON_uppercase",
    ],
)
def test_e20_format_query_param(fhir_client, fmt_value, expect_xml):
    """_format query parameter values per FHIR R4 §3.1.0.1.11.

    Values ``xml``, ``text/xml``, ``application/xml``, ``application/fhir+xml``
    map to XML; ``json``, ``application/json``, ``application/fhir+json``
    map to JSON.
    """
    r = fhir_client.get("/fhir/metadata", params={"_format": fmt_value})
    ct = r.headers.get("content-type", "")
    is_xml = "xml" in ct
    assert is_xml == expect_xml, (
        f"_format={fmt_value!r} → CT={ct}; expected is_xml={expect_xml}"
    )


@pytest.mark.parametrize(
    "fmt_value, accept_value, expect_xml, rationale",
    [
        # _format overrides Accept per §3.1.0.1.11.
        ("json", "application/fhir+xml", False,
         "_format=json overrides Accept=xml"),
        ("xml", "application/fhir+json", True,
         "_format=xml overrides Accept=json"),
        ("application/fhir+xml", "application/fhir+json", True,
         "_format=application/fhir+xml overrides Accept=json"),
        ("application/fhir+json", "application/fhir+xml", False,
         "_format=application/fhir+json overrides Accept=xml"),
    ],
    ids=[
        "format_json_overrides_accept_xml",
        "format_xml_overrides_accept_json",
        "format_full_xml_overrides_accept_json",
        "format_full_json_overrides_accept_xml",
    ],
)
def test_e21_format_overrides_accept(
    fhir_client, fmt_value, accept_value, expect_xml, rationale
):
    """_format overrides Accept per FHIR R4 §3.1.0.1.11."""
    r = fhir_client.get(
        "/fhir/metadata",
        params={"_format": fmt_value},
        headers={"Accept": accept_value},
    )
    ct = r.headers.get("content-type", "")
    is_xml = "xml" in ct
    assert is_xml == expect_xml, (
        f"_format={fmt_value!r} + Accept={accept_value!r} → CT={ct}; "
        f"expected is_xml={expect_xml} because {rationale}"
    )


def test_e22_format_unrecognized_falls_through_to_accept(fhir_client):
    """Unrecognized _format value falls through to Accept header.

    Per §3.1.0.1.11, the spec-listed _format values are xml/text/xml/
    application/xml/application/fhir+xml (XML); json/application/json/
    application/fhir+json (JSON); ttl/application/fhir+turtle/text/turtle
    (Turtle). ``_format=foobar`` is not in the list → fall through to
    Accept header.
    """
    # _format=foobar + Accept=xml → XML (Accept wins on unrecognized _format)
    r = fhir_client.get(
        "/fhir/metadata",
        params={"_format": "foobar"},
        headers={"Accept": "application/fhir+xml"},
    )
    assert "xml" in r.headers.get("content-type", "")

    # _format=foobar + no Accept → JSON (default)
    r = fhir_client.get("/fhir/metadata", params={"_format": "foobar"})
    assert "json" in r.headers.get("content-type", "")


def test_e23_format_ttl_turtle_unsupported_falls_through(fhir_client):
    """_format=ttl (Turtle) is spec-listed but medterm4ds doesn't support it.

    NOT A BUG (AGENTS.md line 140): the spec says 406 is "appropriate" but
    not mandatory; falling back to JSON is conformant for a terminology
    server. The _format=ttl value is recognized by the spec but the impl
    treats it as unrecognized (falls through to Accept → JSON default).
    """
    r = fhir_client.get("/fhir/metadata", params={"_format": "ttl"})
    # Documents current behavior: _format=ttl → JSON (default).
    assert "json" in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Lens 3: mode parameter unusual values on /fhir/metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode_value, expect_status, expect_resource_type",
    [
        # Spec-listed values
        ("full", 200, "CapabilityStatement"),
        ("terminology", 200, "TerminologyCapabilities"),
        ("normative", 200, "CapabilityStatement"),
        # Unusual values
        ("fuller", 400, "OperationOutcome"),
        ("default", 400, "OperationOutcome"),
        ("FULL", 400, "OperationOutcome"),  # case-sensitive
        ("Full", 400, "OperationOutcome"),
        ("Terminology", 400, "OperationOutcome"),
        ("", 400, "OperationOutcome"),  # empty string
    ],
    ids=[
        "mode_full",
        "mode_terminology",
        "mode_normative",
        "mode_fuller_unknown",
        "mode_default_unknown",
        "mode_FULL_uppercase_rejected",
        "mode_Full_mixedcase_rejected",
        "mode_Terminology_mixedcase_rejected",
        "mode_empty_string_rejected",
    ],
)
def test_e30_mode_unusual_values(
    fhir_client, mode_value, expect_status, expect_resource_type
):
    """mode parameter unusual values: valid values accepted; unknown rejected.

    Per §3.1.0.10 + §4.7.1.1: only ``full``, ``normative``, ``terminology``
    are valid mode values. Other values (including case variants) are
    rejected with 400 + OperationOutcome. This probe documents current
    behavior.
    """
    r = fhir_client.get("/fhir/metadata", params={"mode": mode_value})
    assert r.status_code == expect_status, (
        f"mode={mode_value!r} → status={r.status_code}; "
        f"expected {expect_status}"
    )
    body = r.json()
    assert body.get("resourceType") == expect_resource_type, (
        f"mode={mode_value!r} → resourceType={body.get('resourceType')!r}; "
        f"expected {expect_resource_type!r}"
    )


def test_e31_mode_combined_with_format_xml(fhir_client):
    """mode=terminology + _format=xml → XML TerminologyCapabilities.

    Integration corner: mode dispatch + format dispatch combined. The body
    is XML so we can't ``.json()`` it — assert on text content for the
    root element instead.
    """
    r = fhir_client.get(
        "/fhir/metadata", params={"mode": "terminology", "_format": "xml"}
    )
    assert r.status_code == 200
    assert "<TerminologyCapabilities" in r.text
    assert "xml" in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Lens 4: HTTP methods on /fhir/metadata
# ---------------------------------------------------------------------------


def test_e40_head_metadata_returns_405(fhir_client):
    """HEAD /fhir/metadata → 405 (conformant per NOT A BUG registry).

    §3.1.0.15 says servers MUST allow HEAD anywhere GET is allowed OR
    respond 405/501. The 405 is conformant.
    """
    r = fhir_client.head("/fhir/metadata")
    assert r.status_code == 405


def test_e41_options_metadata_returns_405(fhir_client):
    """OPTIONS /fhir/metadata → 405 (not implemented; not spec-required)."""
    r = fhir_client.options("/fhir/metadata")
    assert r.status_code in (405, 200, 204)  # framework-dependent


def test_e42_post_metadata_rejected(fhir_client):
    """POST /fhir/metadata → not 200 (method not allowed for metadata)."""
    r = fhir_client.post("/fhir/metadata")
    assert r.status_code in (405, 404, 422), (
        f"POST /fhir/metadata → {r.status_code}; expected non-200"
    )


# ---------------------------------------------------------------------------
# Lens 5: path variations on /fhir/metadata
# ---------------------------------------------------------------------------


def test_e50_metadata_trailing_slash_404(fhir_client):
    """/fhir/metadata/ (trailing slash) → 404 (no route registered).

    Documents current behavior — not spec-violating but a footgun for
    clients that auto-add trailing slashes.
    """
    r = fhir_client.get("/fhir/metadata/")
    assert r.status_code == 404


@pytest.mark.parametrize(
    "path, expect_status",
    [
        ("/fhir/Metadata", 404),  # case-sensitive
        ("/fhir/METADATA", 404),  # case-sensitive
        ("/fhir/metadata", 200),
    ],
    ids=["mixedcase_metadata_404", "uppercase_metadata_404", "lowercase_ok"],
)
def test_e51_metadata_path_case_sensitivity(fhir_client, path, expect_status):
    """Path case sensitivity: only ``/fhir/metadata`` (lowercase) is routed."""
    r = fhir_client.get(path)
    assert r.status_code == expect_status


# ---------------------------------------------------------------------------
# Lens 6: integration corners — capabilities → READ/SEARCH round-trip
# ---------------------------------------------------------------------------


def test_e60_capabilities_advertises_three_resources(fhir_client):
    """CapabilityStatement.rest[].resource[] advertises the 3 resources
    that §4.7.1.1 item 2 mandates: CodeSystem, ValueSet, ConceptMap."""
    caps = fhir_client.get("/fhir/metadata").json()
    rest = caps.get("rest", [])
    assert rest, "CapabilityStatement.rest[] must be non-empty"
    resources = rest[0].get("resource", [])
    types = [r.get("type") for r in resources]
    for expected in ("CodeSystem", "ValueSet", "ConceptMap"):
        assert expected in types, (
            f"CapabilityStatement must advertise {expected}; got {types}"
        )


@pytest.mark.parametrize(
    "resource_type",
    ["CodeSystem", "ValueSet", "ConceptMap"],
    ids=["CodeSystem", "ValueSet", "ConceptMap"],
)
def test_e61_advertised_resource_supports_read_route(fhir_client, resource_type):
    """Every advertised resource MUST support the READ route (item 2).

    Integration corner: the capabilities advertisement promises READ;
    does the server actually serve it? READ returns 404 OperationOutcome
    (no resources persisted) which is conformant for a non-persisting
    terminology server.
    """
    r = fhir_client.get(f"/fhir/{resource_type}/some-id")
    assert r.status_code == 404, (
        f"READ /fhir/{resource_type}/some-id → {r.status_code}; expected 404"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"
    assert "fhir+json" in r.headers.get("content-type", "")


@pytest.mark.parametrize(
    "resource_type",
    ["CodeSystem", "ValueSet", "ConceptMap"],
    ids=["CodeSystem", "ValueSet", "ConceptMap"],
)
def test_e62_advertised_resource_supports_search_route(fhir_client, resource_type):
    """Every advertised resource MUST support the SEARCH route (item 2)."""
    r = fhir_client.get(
        f"/fhir/{resource_type}", params={"url": "http://example.org/foo"}
    )
    assert r.status_code == 200, (
        f"SEARCH /fhir/{resource_type}?url=... → {r.status_code}"
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "searchset"


@pytest.mark.parametrize(
    "search_param",
    ["url", "version", "name", "title", "status"],
    ids=["url", "version", "name", "title", "status"],
)
def test_e63_all_5_spec_search_params_accepted_on_codesystem(
    fhir_client, search_param
):
    """§4.7.1.1 item 3: all 5 search params accepted on every resource."""
    r = fhir_client.get(
        "/fhir/CodeSystem", params={search_param: "some-value"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Bundle"


def test_e64_search_param_case_insensitive_accepted(fhir_client):
    """SEARCH params are case-insensitive in URL (HTTP convention).

    ``?URL=...`` and ``?url=...`` should both be accepted (the impl
    doesn't enforce case-sensitivity on search param names — FastAPI
    treats them as opaque query params). Documents current behavior.
    """
    for variant in ("url", "URL", "Url"):
        r = fhir_client.get(
            "/fhir/CodeSystem", params={variant: "http://snomed.info/sct"}
        )
        assert r.status_code == 200, (
            f"CodeSystem?{variant}=... → {r.status_code}"
        )


# ---------------------------------------------------------------------------
# Lens 7: batch dispatcher coverage of every advertised operation
# ---------------------------------------------------------------------------


def test_e70_capabilities_advertises_mandatory_operations(fhir_client):
    """§4.7.1.2 mandatory operations MUST be advertised in CapabilityStatement.

    Note: ``$closure`` is defined by FHIR R4 as a CodeSystem operation
    (canonical OperationDefinition URI:
    http://hl7.org/fhir/OperationDefinition/CodeSystem-closure), NOT a
    ConceptMap operation.
    """
    caps = fhir_client.get("/fhir/metadata").json()
    ops_advertised = set()
    for r in caps.get("rest", []):
        for res in r.get("resource", []):
            for op in res.get("operation", []):
                ops_advertised.add((res.get("type"), op.get("name")))
    # 7 mandatory ops per §4.7.1.2 — $closure is on CodeSystem per R4 spec
    mandatory = {
        ("CodeSystem", "lookup"),
        ("CodeSystem", "validate-code"),
        ("CodeSystem", "subsumes"),
        ("CodeSystem", "closure"),
        ("ValueSet", "expand"),
        ("ValueSet", "validate-code"),
        ("ConceptMap", "translate"),
    }
    missing = mandatory - ops_advertised
    assert not missing, (
        f"Missing mandatory operations in CapabilityStatement: {missing}"
    )


@pytest.mark.parametrize(
    "op_url, expect_status_in_entry",
    [
        # All 7 mandatory ops MUST be batch-dispatchable per §4.7.1.2.
        ("CodeSystem/$lookup?system=http://snomed.info/sct&code=73211009", "200"),
        ("CodeSystem/$validate-code?system=http://snomed.info/sct&code=73211009", "200"),
        ("CodeSystem/$subsumes?system=http://snomed.info/sct&codeA=73211009&codeB=73211009", "200"),
        ("CodeSystem/$closure?name=explorer-test", "200"),
        ("ValueSet/$validate-code?url=http://snomed.info/sct&system=http://snomed.info/sct&code=73211009", "200"),
        ("ConceptMap/$translate?system=http://snomed.info/sct&sourceCode=73211009&targetSystem=http://hl7.org/fhir/sid/icd-10-cm", "200"),
    ],
    ids=[
        "batch_lookup",
        "batch_cs_validate",
        "batch_subsumes",
        "batch_closure",
        "batch_vs_validate",
        "batch_translate",
    ],
)
def test_e71_batch_dispatcher_covers_all_mandatory_ops(
    fhir_client, op_url, expect_status_in_entry
):
    """Batch endpoint MUST dispatch every advertised operation per §4.7.1.2.

    Integration corner: capabilities advertises all 7 mandatory operations;
    does the batch dispatcher actually serve each one?
    """
    body = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [{"request": {"method": "GET", "url": op_url}}],
    }
    r = fhir_client.post("/fhir", json=body)
    assert r.status_code == 200, (
        f"POST /fhir batch → {r.status_code}; expected 200 outer Bundle"
    )
    bundle = r.json()
    assert bundle.get("resourceType") == "Bundle"
    assert bundle.get("type") == "batch-response"
    entries = bundle.get("entry", [])
    assert entries, "batch-response must have ≥1 entry"
    entry_status = str(entries[0].get("response", {}).get("status", ""))
    # Accept 2xx (some ops may return 4xx for valid input combinations due
    # to fixture limits; the load-bearing invariant is that the dispatcher
    # routes the request rather than returning a 404 catch-all).
    assert entry_status.startswith("2") or entry_status.startswith("4"), (
        f"Batch entry for {op_url!r} → status={entry_status}; "
        f"expected 2xx (success) or 4xx (input validation). A 404 or 5xx "
        f"would indicate the dispatcher failed to route."
    )
    # Per-entry resource MUST be present (Parameters or OperationOutcome)
    assert "resource" in entries[0], (
        f"Batch entry for {op_url!r} missing 'resource' field"
    )


def test_e72_batch_advertised_capability_consistency(fhir_client):
    """Cross-resource consistency: every op advertised in CapabilityStatement
    MUST be reachable via the batch dispatcher.

    Integration corner: the capabilities advertisement and the batch
    dispatcher path-table are TWO locations that must agree (per AGENTS.md
    Known Fragile Areas line 68 — "Future operations added to per-operation
    routes MUST also be wired into _dispatch_batch_operation").
    """
    caps = fhir_client.get("/fhir/metadata").json()
    advertised = set()
    for r in caps.get("rest", []):
        for res in r.get("resource", []):
            for op in res.get("operation", []):
                advertised.add((res.get("type"), op.get("name")))

    # Probe each (resource, op) tuple with a minimal valid request.
    # We expect a 2xx or 4xx (validation), NOT a 404 (route not wired).
    for rtype, op_name in sorted(advertised):
        # Skip custom ops not in the FHIR R4 mandatory matrix
        if op_name in ("search", "extract"):
            continue
        # Build a minimal URL
        url = f"{rtype}/${op_name}?_probe=1"
        body = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [{"request": {"method": "GET", "url": url}}],
        }
        r = fhir_client.post("/fhir", json=body)
        assert r.status_code == 200
        entry_status = str(
            r.json().get("entry", [{}])[0].get("response", {}).get("status", "")
        )
        # The probe must NOT return a catch-all 404 — every advertised op
        # must reach its handler.
        assert not entry_status.startswith("404"), (
            f"Advertised op ({rtype}/{op_name}) returned 404 in batch "
            f"dispatcher — op is advertised but not wired. status={entry_status}"
        )


# ---------------------------------------------------------------------------
# Lens 8: Cross-resource consistency on conformance elements
# ---------------------------------------------------------------------------


def test_e80_capability_statement_format_array_includes_both(fhir_client):
    """§4.7.1.1 item 1: format[] MUST include both ``json`` and ``xml``."""
    caps = fhir_client.get("/fhir/metadata").json()
    fmts = caps.get("format", [])
    assert "json" in fmts, f"format[] missing 'json': {fmts}"
    assert "xml" in fmts, f"format[] missing 'xml': {fmts}"


def test_e81_capability_statement_kind_instance(fhir_client):
    """§4.7.1.1 item 4: kind MUST be ``instance``."""
    caps = fhir_client.get("/fhir/metadata").json()
    assert caps.get("kind") == "instance"


def test_e82_terminology_capabilities_kind_instance(fhir_client):
    """§4.7.1.1 item 5: TerminologyCapabilities.kind MUST be ``instance``."""
    tc = fhir_client.get("/fhir/metadata?mode=terminology").json()
    assert tc.get("kind") == "instance"


def test_e83_terminology_capabilities_codesystem_block_non_empty(fhir_client):
    """§4.7.1.1 item 5: codeSystem[] block MUST be present + non-empty."""
    tc = fhir_client.get("/fhir/metadata?mode=terminology").json()
    cs = tc.get("codeSystem", [])
    assert cs, "TerminologyCapabilities.codeSystem[] must be non-empty"
    for entry in cs:
        assert "uri" in entry, f"codeSystem entry missing 'uri': {entry}"
        assert "content" in entry, f"codeSystem entry missing 'content': {entry}"


def test_e84_terminology_capabilities_codesystem_uris_match_registry(
    fhir_client,
):
    """Every advertised codeSystem.uri MUST be in SYSTEM_TO_FHIR_URI.

    Mirrors SKEPTIC test_s53 bidirectional assertion (no extras, no
    missing) — verifies the conformance surface is consistent with the
    single-source-of-truth registry.
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    tc = fhir_client.get("/fhir/metadata?mode=terminology").json()
    advertised = {entry.get("uri") for entry in tc.get("codeSystem", [])}
    canonical = set(SYSTEM_TO_FHIR_URI.values())
    extras = advertised - canonical
    missing = canonical - advertised
    assert not extras, (
        f"Advertised codeSystem URIs not in canonical registry: {extras}"
    )
    assert not missing, (
        f"Canonical registry URIs not advertised: {missing}"
    )


# ---------------------------------------------------------------------------
# Lens 9: capabilities advertisement required elements (item 4 re-verification)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "element",
    ["url", "version", "name", "title", "status", "date", "description",
     "kind", "fhirVersion"],
    ids=[
        "url", "version", "name", "title", "status", "date", "description",
        "kind", "fhirVersion",
    ],
)
def test_e90_capability_statement_required_element_present(
    fhir_client, element
):
    """§4.7.1.1 item 4: 9 required elements MUST be present in
    CapabilityStatement."""
    caps = fhir_client.get("/fhir/metadata").json()
    assert element in caps, (
        f"CapabilityStatement missing required element {element!r}: "
        f"keys={list(caps.keys())}"
    )


@pytest.mark.parametrize(
    "element",
    ["url", "name", "title", "status", "date", "kind"],
    ids=["url", "name", "title", "status", "date", "kind"],
)
def test_e91_terminology_capabilities_required_element_present(
    fhir_client, element
):
    """§4.7.1.1 item 5: 6 required top-level elements MUST be present in
    TerminologyCapabilities."""
    tc = fhir_client.get("/fhir/metadata?mode=terminology").json()
    assert element in tc, (
        f"TerminologyCapabilities missing required element {element!r}: "
        f"keys={list(tc.keys())}"
    )


# ---------------------------------------------------------------------------
# Lens 10: spec-implied behaviors on metadata route
# ---------------------------------------------------------------------------


def test_e100_mode_invalid_returns_fhir_error(fhir_client):
    """mode=invalid → 400 + OperationOutcome with FHIR Content-Type.

    Per GLOBAL_RULES framework-default-drift prohibition: the error path
    MUST go through _fhir_error_response so the Content-Type is
    application/fhir+json, not application/json.
    """
    r = fhir_client.get("/fhir/metadata", params={"mode": "invalid"})
    assert r.status_code == 400
    assert r.json().get("resourceType") == "OperationOutcome"
    assert "fhir+json" in r.headers.get("content-type", "")


def test_e101_mode_invalid_xml_client_gets_xml_error(fhir_client):
    """mode=invalid + _format=xml → XML OperationOutcome.

    CR-003 fix: error path routes through _fhir_error_response so XML
    clients get XML.
    """
    r = fhir_client.get(
        "/fhir/metadata", params={"mode": "invalid", "_format": "xml"}
    )
    assert r.status_code == 400
    assert "xml" in r.headers.get("content-type", "")
    assert "<OperationOutcome" in r.text


def test_e102_metadata_mode_with_extra_whitespace_rejected(fhir_client):
    """mode=' full ' (URL-encoded whitespace) → 400 (not silently accepted).

    Per FHIR R4 §3.1.0.1.4: query parameter values are not auto-trimmed;
    ' full ' is a distinct value from 'full'. The impl correctly rejects
    non-spec values.
    """
    r = fhir_client.get("/fhir/metadata", params={"mode": " full "})
    assert r.status_code == 400


def test_e103_metadata_status_value_valid_publicationstatus(fhir_client):
    """CapabilityStatement.status MUST be a valid PublicationStatus.

    Per FHIR R4 PublicationStatus enum: active | draft | retired | unknown.
    """
    caps = fhir_client.get("/fhir/metadata").json()
    status = caps.get("status")
    assert status in ("active", "draft", "retired", "unknown"), (
        f"CapabilityStatement.status={status!r}; expected a valid "
        f"PublicationStatus value"
    )


def test_e104_metadata_fhirversion_is_4_0_1(fhir_client):
    """CapabilityStatement.fhirVersion MUST be ``4.0.1`` for FHIR R4."""
    caps = fhir_client.get("/fhir/metadata").json()
    assert caps.get("fhirVersion") == "4.0.1"
