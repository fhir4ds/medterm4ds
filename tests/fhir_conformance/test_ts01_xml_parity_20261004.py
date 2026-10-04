"""TS-01 XML surface + cross-format content parity findings (2026-10-04).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261004).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the XML serialization surface (engines/fhir/xml.py via
_fhir_response) had never been maintenance-swept. Survey covered all
major operations in XML (metadata, lookup, search, read, validate) +
error paths — all conformant (correct MIME, well-formed, escaped).

X1 (LOW, NEW) — cross-format CONTENT divergence on control characters.
    The XML path strips XML-1.0-illegal control chars (QC-300's
    documented well-formedness sanitizer, _ILLEGAL_XML_CHARS_RE); the
    JSON path does not. Consequence: the SAME request can yield
    DIFFERENT clinical message content per requested format. Fresh
    probe: $validate-code display="w\\x08rong" → JSON message
    'The display "w\\x08rong" is incorrect' vs XML message
    'The display "wrong" is incorrect'. The mismatch diagnostic quotes
    the client display verbatim per CS-03 QA-048 — verbatim-quoting is
    load-bearing (the client matches the message against its own
    input); a client cross-checking JSON vs XML transcripts sees
    silently different evidence. NB: neither side is spec-ILLEGAL
    (JSON may carry \\x08 escaped; XML must not carry it raw) — the
    finding is the undocumented ASYMMETRY, not a malformation.
    Pinned: both formats' current values + the divergence itself,
    with options-on-fix instructions.
    Fix shape (pick ONE): (a) strip control chars at the MESSAGE
    BUILDING layer so both formats carry identical sanitized content;
    (b) document the asymmetry in AGENTS.md as intended (XML-only
    sanitizer) and pin it — the current registry treats it as an
    accident of layering.

Controls re-verified fresh: XML on metadata/lookup/search/read/
    validate + 404 + miss + bad-mode + unicode-filter (all
    application/fhir+xml, well-formed); _xml_escape entity + quote
    escaping (& < > " '); XML-1.0 sanitizer drops illegal chars
    (QC-300 held); boolean lowercase wire form on the XML path.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"
NS = {"f": "http://hl7.org/fhir"}


def _xml_message(client) -> str | None:
    r = client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": SNOMED_URI, "code": T2DM,
            "display": "w\x08rong", "_format": "xml",
        },
    )
    assert r.status_code == 200, r.text
    assert "fhir+xml" in r.headers["content-type"]
    root = ET.fromstring(r.text)  # well-formedness gate
    # FHIR XML Parameters shape: <parameter><name value="message"/>
    # <valueString value="..."/></parameter> — name/valueString are CHILD
    # ELEMENTS of parameter, not attributes on it.
    for p in root.findall("f:parameter", NS):
        name_el = p.find("f:name", NS)
        if name_el is not None and name_el.get("value") == "message":
            vs = p.find("f:valueString", NS)
            if vs is not None:
                return vs.get("value")
    return None


class TestX1CrossFormatDivergence:
    """X1 — same request, different message content per format."""

    def test_x10_json_carries_control_char(self, fhir_client):
        """JSON message quotes the client display VERBATIM including
        the \\x08 (CS-03 QA-048 contract on the JSON path)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "display": "w\x08rong", "_format": "json",
            },
        )
        assert r.status_code == 200
        msg = next(
            p.get("valueString")
            for p in r.json()["parameter"]
            if p.get("name") == "message"
        )
        assert msg == 'The display "w\x08rong" is incorrect'

    def test_x11_xml_strips_control_char(self, fhir_client):
        """XML message has the \\x08 stripped (QC-300 sanitizer) —
        well-formedness preserved, content altered."""
        msg = _xml_message(fhir_client)
        assert msg == 'The display "wrong" is incorrect'

    def test_x12_divergence_pinned(self, fhir_client):
        """THE FINDING: identical request, different message content
        per format. WHEN the fix lands (message-layer sanitization so
        both formats agree, or a documented-intended asymmetry), flip:
        assert the sanitized-equality (option a) or keep this pin with
        an INTENDED annotation (option b)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "display": "w\x08rong", "_format": "json",
            },
        )
        jmsg = next(
            p.get("valueString")
            for p in r.json()["parameter"]
            if p.get("name") == "message"
        )
        xmsg = _xml_message(fhir_client)
        assert jmsg != xmsg, (
            "JSON and XML messages now agree — the cross-format "
            "sanitization fix landed; flip this pin to equality."
        )


class TestXMLSurfaceControls:
    """Controls: XML surface conformant shapes, re-verified fresh."""

    @pytest.mark.parametrize(
        "path,params",
        [
            ("/fhir/metadata", {}),
            ("/fhir/CodeSystem/$lookup",
             {"system": SNOMED_URI, "code": T2DM}),
            ("/fhir/CodeSystem", {"url": SNOMED_URI}),
            ("/fhir/CodeSystem/snomed", {}),
            ("/fhir/CodeSystem/$validate-code",
             {"system": SNOMED_URI, "code": T2DM}),
        ],
    )
    def test_v10_xml_mime_and_wellformed(
        self, fhir_client, path, params
    ):
        r = fhir_client.get(
            path, params={**params, "_format": "xml"}
        )
        assert "fhir+xml" in r.headers["content-type"]
        ET.fromstring(r.text)  # raises if malformed

    def test_v11_error_paths_xml(self, fhir_client):
        """OperationOutcome errors in XML: unknown code (200 miss) and
        bad mode (400) both render application/fhir+xml well-formed."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": SNOMED_URI, "code": "nope", "_format": "xml",
            },
        )
        assert "fhir+xml" in r.headers["content-type"]
        root = ET.fromstring(r.text)
        assert root.tag.endswith("OperationOutcome")
        r2 = fhir_client.get(
            "/fhir/metadata", params={"mode": "bogus", "_format": "xml"}
        )
        assert r2.status_code == 400
        assert "fhir+xml" in r2.headers["content-type"]

    def test_v12_entity_escaping(self, fhir_client):
        """Display with markup/amp/quote entities survives round-trip
        via XML attribute escaping (QC-300-adjacent escaping contract).
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "display": "<b>AT&T \"x\"</b>", "_format": "xml",
            },
        )
        root = ET.fromstring(r.text)
        msg = None
        for p in root.findall("f:parameter", NS):
            name_el = p.find("f:name", NS)
            if name_el is not None and name_el.get("value") == "message":
                vs = p.find("f:valueString", NS)
                if vs is not None:
                    msg = vs.get("value")
        assert msg is not None and "<b>" in msg and "AT&T" in msg

    def test_v13_boolean_lowercase_wire_form(self, fhir_client):
        """FHIR R4 §3.4.1: booleans render lowercase true/false on the
        XML path (_scalar_to_xml_attr contract)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM, "_format": "xml",
            },
        )
        assert 'valueBoolean value="true"' in r.text

    def test_v14_unicode_display_xml(self, fhir_client):
        """Non-ASCII display content serializes cleanly (no encoding
        degradation)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "display": "diabète 🩸", "_format": "xml",
            },
        )
        assert "fhir+xml" in r.headers["content-type"]
        root = ET.fromstring(r.text)
        msgs = []
        for p in root.findall("f:parameter", NS):
            name_el = p.find("f:name", NS)
            if name_el is not None and name_el.get("value") == "message":
                vs = p.find("f:valueString", NS)
                if vs is not None:
                    msgs.append(vs.get("value"))
        assert msgs and "diabète" in msgs[0]
