"""TS-11 request-body content negotiation (2026-10-06).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261006c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: FHIR R4 §3.1.0 declares JSON and XML as the two mandatory
exchange formats across the RESTful surface — including request
bodies for POST operations. The server's RESPONSE-side XML support is
complete (TS-01 iteration verified all response surfaces). This sweep
covers the REQUEST side: XML bodies into POST operations and $batch.

Survey (fresh, this run):
* XML response negotiation fully functional: POST with JSON body +
  Accept: application/fhir+xml → 200 application/fhir+xml.
* Error responses honor Accept regardless of request Content-Type
  (422s render fhir+xml when asked).
* Malformed XML body → 422 OperationOutcome (not 500) — safe failure.
* Empty JSON body / form-encoded body → 422 OperationOutcome.
* JSON $batch → 200 batch-response (control).

Q1 (MEDIUM, NEW) — XML request bodies rejected everywhere while
    CapabilityStatement advertises 'xml'.
    The CapabilityStatement format list is ['json', 'xml'] — R4
    §2.1.0.7: format codes declare support "for the RESTful surface",
    which includes POST request bodies. But EVERY POST operation
    rejects an application/fhir+xml body with 422 "Parameter
    'unknown': Input should be a valid dictionary" (FastAPI's JSON
    body parser receiving a string): probed $lookup,
    $validate-code, $expand, and POST /fhir $batch — all 422, all
    with JSON-equivalent bodies returning 200. A spec-conformant
    client (XML-only stack) cannot use ANY POST operation.
    Fix shape: parse application/fhir+xml bodies (xml.etree → dict
    via a from_fhir_xml inverse of to_fhir_xml) before the body
    validator, or drop 'xml' from CapabilityStatement.format (less
    correct: response-side XML IS supported).

Q2 (LOW, NEW) — the 422 diagnostic for XML bodies is misleading.
    "Parameter 'unknown': Input should be a valid dictionary" names
    no actionable cause (it is the pydantic-fastapi wrapper failing
    to JSON-decode, not a parameter problem). A client sends
    well-formed FHIR XML and receives a parameter error. Fix rides
    with Q1: detect Content-Type fhir+xml and either parse or 415.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"
VS_ISA_DM = "http://snomed.info/sct/73211009?fhir_vs=isa"

XML_LOOKUP = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Parameters xmlns="http://hl7.org/fhir">'
    f'<parameter><name value="system"/><valueUri value="{SNOMED_URI}"/></parameter>'
    f'<parameter><name value="code"/><valueCode value="{T2DM}"/></parameter>'
    "</Parameters>"
)

XML_EXPAND = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Parameters xmlns="http://hl7.org/fhir">'
    f'<parameter><name value="url"/><valueUri value="{VS_ISA_DM}"/></parameter>'
    "</Parameters>"
)

XML_BATCH = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Bundle xmlns="http://hl7.org/fhir"><type value="batch"/>'
    "<entry><resource>"
    '<Parameters xmlns="http://hl7.org/fhir">'
    f'<parameter><name value="system"/><valueUri value="{SNOMED_URI}"/></parameter>'
    f'<parameter><name value="code"/><valueCode value="{T2DM}"/></parameter>'
    "</Parameters></resource>"
    '<request><method value="GET"/><url value="CodeSystem/$lookup"/></request>'
    "</entry></Bundle>"
)

JSON_LOOKUP = {
    "resourceType": "Parameters",
    "parameter": [
        {"name": "system", "valueUri": SNOMED_URI},
        {"name": "code", "valueCode": T2DM},
    ],
}

JSON_BATCH = {
    "resourceType": "Bundle",
    "type": "batch",
    "entry": [
        {
            "resource": JSON_LOOKUP,
            "request": {"method": "GET", "url": "CodeSystem/$lookup"},
        }
    ],
}

XML_HEADERS = {"Content-Type": "application/fhir+xml"}


class TestQ1XmlRequestBodiesRejected:
    """Q1 — advertised 'xml' format not honored for request bodies."""

    def test_q10_capability_statement_advertises_xml(self, fhir_client):
        """CapabilityStatement.format lists xml — the advertised
        contract this finding measures against (R4 §2.1.0.7)."""
        r = fhir_client.get("/fhir/metadata")
        assert "xml" in r.json().get("format", [])

    @pytest.mark.parametrize(
        "path,body",
        [
            ("/fhir/CodeSystem/$lookup", XML_LOOKUP),
            ("/fhir/CodeSystem/$validate-code", XML_LOOKUP),
            ("/fhir/ValueSet/$expand", XML_EXPAND),
            ("/fhir", XML_BATCH),
        ],
    )
    def test_q11_xml_body_rejected(self, fhir_client, path, body):
        """Every POST surface rejects application/fhir+xml bodies
        with 422. WHEN the fix lands (XML body parsing or correct
        415), flip to the negotiated behavior."""
        r = fhir_client.post(path, content=body, headers=XML_HEADERS)
        assert r.status_code == 422, (
            f"XML body now accepted on {path} ({r.status_code}) — "
            "flip this pin (and q13)."
        )

    def test_q12_json_equivalents_all_200(self, fhir_client):
        """Controls: the same logical requests as JSON bodies all
        succeed — the rejection is format-specific, not
        request-specific."""
        r1 = fhir_client.post("/fhir/CodeSystem/$lookup", json=JSON_LOOKUP)
        assert r1.status_code == 200
        assert any(
            p["name"] == "display"
            and p.get("valueString") == "Type 2 diabetes mellitus"
            for p in r1.json()["parameter"]
        )
        r2 = fhir_client.post("/fhir", json=JSON_BATCH)
        assert r2.status_code == 200
        assert r2.json()["type"] == "batch-response"

    def test_q13_divergence_pinned(self, fhir_client):
        """THE FINDING: format advertised + response-side supported +
        request-side rejected. Same logical lookup: JSON 200 vs XML
        422."""
        j = fhir_client.post("/fhir/CodeSystem/$lookup", json=JSON_LOOKUP)
        x = fhir_client.post(
            "/fhir/CodeSystem/$lookup", content=XML_LOOKUP, headers=XML_HEADERS
        )
        assert j.status_code == 200 and x.status_code == 422, (
            "XML request bodies now honored — flip q11/q13 pins."
        )


class TestRequestBodyNegotiationControls:
    """Controls: response-side negotiation + safe failure modes."""

    def test_r10_json_body_xml_accept(self, fhir_client):
        """POST JSON body + Accept fhir+xml → 200 fhir+xml (response
        negotiation orthogonal to request encoding)."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json=JSON_LOOKUP,
            headers={"Accept": "application/fhir+xml"},
        )
        assert r.status_code == 200
        assert "fhir+xml" in r.headers["content-type"]
        assert r.text.startswith("<?xml")

    def test_r11_errors_honor_accept(self, fhir_client):
        """422 responses render in the Accept-negotiated format even
        when the request Content-Type was XML."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            content=XML_LOOKUP,
            headers={**XML_HEADERS, "Accept": "application/fhir+xml"},
        )
        assert r.status_code == 422
        assert "fhir+xml" in r.headers["content-type"]
        assert r.text.startswith("<?xml")

    def test_r12_malformed_xml_safe_failure(self, fhir_client):
        """Malformed XML body → 422 OperationOutcome, never a 500 or
        unhandled parser traceback."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            content="<Parameters><broken",
            headers=XML_HEADERS,
        )
        assert r.status_code == 422
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_r13_empty_and_form_bodies(self, fhir_client):
        """Empty body and form-encoded body → 422 OperationOutcome
        (not 500); consistent safe rejection of unsupported
        encodings."""
        r1 = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            content=b"",
            headers={"Content-Type": "application/fhir+json"},
        )
        assert r1.status_code == 422
        assert r1.json()["resourceType"] == "OperationOutcome"
        r2 = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            content="system=X&code=Y",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r2.status_code == 422
        assert r2.json()["resourceType"] == "OperationOutcome"


# Q2 is observed inside q11 responses; explicit pin:
class TestQ2MisleadingDiagnostic:
    def test_q20_xml_rejection_diagnostic_shape(self, fhir_client):
        """The 422 for well-formed XML names 'Parameter unknown' — a
        JSON-parser artifact, not a parameter problem. Rides with
        Q1's fix (parse or 415 with an accurate reason)."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            content=XML_LOOKUP,
            headers=XML_HEADERS,
        )
        assert r.status_code == 422
        text = r.text
        # current misleading diagnostic present (pin)
        assert "Input should be a valid dictionary" in text or (
            "Parameter" in text
        )
