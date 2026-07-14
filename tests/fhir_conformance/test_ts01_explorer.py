"""EXPLORER probes for TS-01 (Terminology Service RESTful API Conformance, §4.7.1.1).

Source: https://build.fhir.org/terminology-service.html §4.7.1.1
        https://hl7.org/fhir/R4/http.html §3.1.0 (normative RESTful API rules)
Fixture: tests/fhir_conformance/conftest.py::fhir_client (synthetic DB)

EXPLORER lens: lateral thinking. Probes parameter combinations,
header negotiation corners, and cross-product behaviors that
SKEPTIC and HISTORIAN did not exercise.
"""

from __future__ import annotations

import pytest


# --- Probe E1/E2: Content-Type must be application/fhir+json (§3.1.0.1.9) ---
@pytest.mark.parametrize(
    "path,headers",
    [
        ("/fhir/metadata", {}),
        ("/fhir/metadata", {"Accept": "application/fhir+json"}),
        ("/fhir/metadata?mode=full", {}),
        ("/fhir/metadata?mode=terminology", {}),
        ("/fhir/CodeSystem", {}),
        ("/fhir/ValueSet", {}),
        ("/fhir/ConceptMap", {}),
        ("/fhir/CodeSystem/anything", {}),
    ],
)
def test_e01_json_content_type_is_fhir_json(fhir_client, path, headers):
    """§3.1.0.1.9: 'The formal MIME-type for FHIR resources is application/fhir+xml
    or application/fhir+json. The correct mime type SHALL be used by clients and servers.'

    EXPLORER finding: server returns Content-Type=application/json (Starlette default)
    instead of the FHIR-spec MIME type application/fhir+json. Clients that strictly
    validate the response Content-Type against the FHIR MIME type will reject these
    responses.
    """
    r = fhir_client.get(path, headers=headers)
    ct = r.headers.get("content-type", "")
    pytest.current_report_extra = f"path={path} ct={ct!r}"
    assert "application/fhir+json" in ct, (
        f"Content-Type for {path!r} is {ct!r}; spec mandates application/fhir+json "
        f"(https://hl7.org/fhir/R4/http.html#3.1.0.1.9). "
        f"Found via EXPLORER probe: JSONResponse default does not set the FHIR MIME type."
    )


# --- Probe E3: _format=xml query parameter override (§3.1.0.1.11) ---
@pytest.mark.parametrize(
    "fmt_value",
    ["xml", "text/xml", "application/xml", "application/fhir%2Bxml"],
)
def test_e03_format_param_xml(fhir_client, fmt_value):
    """§3.1.0.1.11: 'For the _format parameter, the values xml, text/xml, application/xml,
    and application/fhir+xml SHALL be interpreted to mean the XML format.'

    EXPLORER finding: the server ignores _format and only honors the Accept header.
    A client that cannot set the Accept header (e.g. some XSLT pipelines, browser
    fetch with limited header control) gets JSON even when requesting _format=xml.

    Note: application/fhir+xml is sent URL-encoded as application/fhir%2Bxml
    because '+' in a query string decodes to a space per RFC 3986.
    """
    r = fhir_client.get(f"/fhir/metadata?_format={fmt_value}")
    ct = r.headers.get("content-type", "")
    body = r.text or ""
    pytest.current_report_extra = f"_format={fmt_value} ct={ct!r} body[:60]={body[:60]!r}"
    is_xml = "xml" in ct or body.lstrip().startswith("<")
    assert is_xml, (
        f"_format={fmt_value!r} did not produce XML. ct={ct!r}, body[:120]={body[:120]!r}. "
        f"§3.1.0.1.11 mandates _format values SHALL be interpreted as XML format. "
        f"Server appears to ignore _format query parameter entirely."
    )


# --- Probe E4: _format=json query parameter is honored ---
def test_e04_format_param_json(fhir_client):
    """§3.1.0.1.11: '... the codes json, application/json and application/fhir+json
    SHALL be interpreted to mean the JSON format.'

    Counterpart to E3: _format=json should produce JSON even with an XML Accept header.
    Also tests the URL-encoded form application/fhir%2Bjson.
    """
    # Plain "json" overrides XML Accept.
    r = fhir_client.get(
        "/fhir/metadata?_format=json",
        headers={"Accept": "application/fhir+xml"},
    )
    ct = r.headers.get("content-type", "")
    pytest.current_report_extra = f"_format=json ct={ct!r}"
    assert "json" in ct, (
        f"_format=json with XML Accept header should still produce JSON; got ct={ct!r}"
    )

    # URL-encoded application/fhir+json also overrides XML Accept.
    r2 = fhir_client.get(
        "/fhir/metadata?_format=application/fhir%2Bjson",
        headers={"Accept": "application/fhir+xml"},
    )
    ct2 = r2.headers.get("content-type", "")
    assert "json" in ct2, (
        f"_format=application/fhir%2Bjson should produce JSON; got ct={ct2!r}"
    )


# --- Probe E5: mode=normative is a valid FHIR value (§3.1.0.10) ---
def test_e05_mode_normative_is_valid(fhir_client):
    """§3.1.0.10 capabilities interaction mode parameter table:
        | full (or mode not present) | A Capability Statement ... |
        | normative                  | As above, but only the normative portions ... |
        | terminology                | A TerminologyCapabilities resource ... |

    EXPLORER finding: server rejects mode=normative with 400 'Invalid mode parameter'.
    The FHIR R4 spec lists normative as a third valid value (alongside full/terminology).
    Note: 'Servers MAY ignore the mode parameter' — so returning the full CapabilityStatement
    is acceptable. Rejecting normative as if it were an unknown value is non-conformant.
    """
    r = fhir_client.get("/fhir/metadata?mode=normative")
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:120]={body[:120]!r}"
    # mode=normative must NOT 400. Acceptable: 200 with CapabilityStatement (treated as full)
    # OR 200 with normative-only CapabilityStatement.
    assert r.status_code != 400, (
        f"mode=normative → 400 (rejected as invalid). Per §3.1.0.10, normative is a valid "
        f"value for the mode parameter. Body={body[:200]!r}"
    )


# --- Probe E6: Accept */* defaults to JSON (boundary, not a bug) ---
def test_e06_accept_star_defaults_json(fhir_client):
    """§3.1.0.1.9: 'Servers SHALL support server-driven content negotiation.'

    Boundary probe: Accept: */* should default to JSON (the server's primary format).
    This is the documented carry-forward behavior; recording as a non-bug.
    """
    r = fhir_client.get("/fhir/metadata", headers={"Accept": "*/*"})
    ct = r.headers.get("content-type", "")
    pytest.current_report_extra = f"ct={ct!r}"
    assert r.status_code == 200
    assert "json" in ct, f"Accept: */* should default to JSON; got ct={ct!r}"


# --- Probe E7: Accept: application/json (generic) returns JSON format ---
def test_e07_accept_generic_json(fhir_client):
    """§3.1.0.1.9: 'If a client provides a generic mime type in the Accept header
    (application/xml, text/json, or application/json), the server SHOULD respond with
    the requested mime type, using the XML or JSON formats described in this specification.'

    Generic application/json SHOULD return JSON-formatted body. (Content-Type still must
    be application/fhir+json per E1, but the BODY must be JSON-shaped.)
    """
    r = fhir_client.get("/fhir/metadata", headers={"Accept": "application/json"})
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:60]={body[:60]!r}"
    assert r.status_code == 200
    assert body.lstrip().startswith("{"), (
        f"Accept: application/json should return JSON body; got body[:120]={body[:120]!r}"
    )


# --- Probe E8: Accept: application/xml (generic) returns XML format ---
def test_e08_accept_generic_xml(fhir_client):
    """§3.1.0.1.9: 'If a client provides a generic mime type in the Accept header
    (application/xml, text/json, or application/json), the server SHOULD respond with
    the requested mime type, using the XML or JSON formats described in this specification.'

    Generic application/xml SHOULD return XML-formatted body.
    """
    r = fhir_client.get("/fhir/metadata", headers={"Accept": "application/xml"})
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:60]={body[:60]!r}"
    assert r.status_code == 200
    assert body.lstrip().startswith("<"), (
        f"Accept: application/xml should return XML body; got body[:120]={body[:120]!r}"
    )


# --- Probe E9: Unknown resource type returns FHIR OperationOutcome ---
def test_e09_unknown_resource_type_returns_operation_outcome(fhir_client):
    """§3.1.0.1.5: 'FHIR defines an OperationOutcome resource that can be used to convey
    specific detailed processable error information ... The OperationOutcome may be returned
    with any HTTP 4xx or 5xx response.'

    §4.7.1.1 lists CodeSystem/ValueSet/ConceptMap. An unknown resource type (Patient)
    is technically out of TS-01 scope, but the response should still be FHIR-structured
    when the server is a FHIR server (any 4xx/5xx SHOULD be OperationOutcome-capable).

    EXPLORER finding: server returns FastAPI default {'detail': 'Not Found'} 404 with
    no resourceType field. This is the same shape as the QA-002/QA-003 issue SKEPTIC
    found for CodeSystem/ValueSet/ConceptMap — the catch-all only covers those 3 types.
    """
    r = fhir_client.get("/fhir/Patient/anything")
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body={body[:120]!r}"
    assert r.status_code == 404
    # Acceptable: OperationOutcome in body. NOT acceptable: bare FastAPI {'detail':'Not Found'}.
    assert "OperationOutcome" in body or "resourceType" in body, (
        f"Unknown resource type returned non-FHIR 404 body: {body[:200]!r}. "
        f"Expected FHIR OperationOutcome per §3.1.0.1.5."
    )


# --- Probe E10: SEARCH Bundle structure conforms to §3.1.0.9 ---
@pytest.mark.parametrize("rtype", ["CodeSystem", "ValueSet", "ConceptMap"])
def test_e10_search_bundle_shape(fhir_client, rtype):
    """§3.1.0.9: 'If the search succeeds, the server SHALL return a 200 OK HTTP status code
    and the return content SHALL be a Bundle with type = searchset containing the results.'

    Verify the Bundle shape on SEARCH (not just status 200).
    """
    r = fhir_client.get(f"/fhir/{rtype}?url=http://example.org/test")
    body = r.json()
    pytest.current_report_extra = f"rtype={rtype} body={body!r}"
    assert r.status_code == 200
    assert body.get("resourceType") == "Bundle", (
        f"{rtype} SEARCH should return Bundle resourceType; got {body.get('resourceType')!r}"
    )
    assert body.get("type") == "searchset", (
        f"{rtype} SEARCH Bundle.type should be 'searchset'; got {body.get('type')!r}"
    )
    assert "total" in body, f"{rtype} SEARCH Bundle must include 'total'"
    assert isinstance(body.get("entry"), list), (
        f"{rtype} SEARCH Bundle.entry must be a list (possibly empty)"
    )


# --- Probe E11: mode=full&_format=xml cross-product ---
def test_e11_mode_full_format_xml_crossproduct(fhir_client):
    """Cross-product of two valid parameters: mode=full AND _format=xml.
    Both should be honored independently.
    """
    r = fhir_client.get("/fhir/metadata?mode=full&_format=xml")
    body = r.text or ""
    ct = r.headers.get("content-type", "")
    pytest.current_report_extra = f"ct={ct!r} body[:60]={body[:60]!r}"
    is_xml = "xml" in ct or body.lstrip().startswith("<")
    assert is_xml, (
        f"mode=full&_format=xml should produce XML (mode and format are independent); "
        f"ct={ct!r} body[:120]={body[:120]!r}"
    )
    assert "CapabilityStatement" in body, (
        f"mode=full should still return CapabilityStatement resourceType"
    )


# --- Probe E12: READ id with $-prefix collision protection ---
def test_e12_read_id_with_dollar_prefix(fhir_client):
    """An id starting with '$' would collide with operation names. Verify the server
    doesn't accidentally try to dispatch /fhir/CodeSystem/$bogus as an operation.

    SKEPTIC H08 covers the operation-prefix case; this is the inverse — probing from
    the READ side. Already covered as PASS per HISTORIAN re-test matrix.
    """
    r = fhir_client.get("/fhir/CodeSystem/$bogus")
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:100]={body[:100]!r}"
    # Acceptable: 404 with OperationOutcome (explicit $-prefix rejection).
    assert r.status_code == 404
    assert "OperationOutcome" in body or "resourceType" in body


# --- Probe E13: deeply-nested search with all 5 params present ---
def test_e13_search_all_params_present(fhir_client):
    """EXPLORER: combined query — all 5 spec-required search params on one request.
    Server should accept and return a Bundle (not 400, not 500).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem"
        "?url=http://example.org/x"
        "&version=1.0"
        "&name=Foo"
        "&title=Foo"
        "&status=active"
    )
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:80]={body[:80]!r}"
    assert r.status_code == 200
    assert "Bundle" in body


# --- Probe E14: very long id does not 500 (boundary safety) ---
def test_e14_long_id_no_500(fhir_client):
    """Boundary: 2000-char id should not crash."""
    r = fhir_client.get(f"/fhir/CodeSystem/{'x' * 2000}")
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body_len={len(body)}"
    assert r.status_code != 500, (
        f"Long id → 500 (unbounded error). Body[:200]={body[:200]!r}"
    )


# --- Probe E15: Accept with q-values picks XML (q=1.0 implicit on first) ---
def test_e15_accept_q_values_xml_preferred(fhir_client):
    """Accept: application/fhir+xml, application/fhir+json;q=0.9 — first entry has
    implicit q=1.0, so XML should win.
    """
    r = fhir_client.get(
        "/fhir/metadata",
        headers={"Accept": "application/fhir+xml, application/fhir+json;q=0.9"},
    )
    body = r.text or ""
    ct = r.headers.get("content-type", "")
    pytest.current_report_extra = f"ct={ct!r} body[:60]={body[:60]!r}"
    is_xml = "xml" in ct or body.lstrip().startswith("<")
    assert is_xml, (
        f"q-value precedence should pick XML (implicit q=1.0); ct={ct!r} body[:120]={body[:120]!r}"
    )


# --- Probe E16: missing Accept header defaults to JSON ---
def test_e16_no_accept_header_defaults_json(fhir_client):
    """When Accept is entirely absent, server should default to JSON (its primary format).
    """
    # Note: TestClient sets a default Accept; clear it explicitly.
    r = fhir_client.get("/fhir/metadata", headers={"Accept": ""})
    body = r.text or ""
    pytest.current_report_extra = f"body[:60]={body[:60]!r}"
    # Empty Accept is treated as */* by most clients; JSON expected.
    assert r.status_code == 200
    assert body.lstrip().startswith("{"), (
        f"Empty/missing Accept should default to JSON; body[:120]={body[:120]!r}"
    )
