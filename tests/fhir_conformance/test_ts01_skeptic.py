"""SKEPTIC probes for TS-01 (Terminology Service RESTful API Conformance, §4.7.1.1).

Source: https://build.fhir.org/terminology-service.html §4.7.1.1
Fixture: tests/fhir_conformance/conftest.py::fhir_client (synthetic DB)

Each probe is a SKEPTIC-style adversarial test:
- Probe an edge of one SHALL item from §4.7.1.1.
- Capture the actual behavior (status, body, headers).
- A probe "fails" (reveals a bug) when the actual behavior violates the spec.
"""

from __future__ import annotations

import pytest


# --- Probe 1: XML format support (item 1) ---
def test_probe_01_xml_format_sUPPORTED(fhir_client):
    """§4.7.1.1 item 1: 'the XML and JSON FHIR formats.' SHALL be supported."""
    r = fhir_client.get("/fhir/metadata", headers={"Accept": "application/fhir+xml"})
    content_type = r.headers.get("content-type", "")
    body = r.text or ""
    is_xml = "xml" in content_type or body.lstrip().startswith("<")
    # Capture details for evidence whether pass or fail.
    pytest.current_report_extra = f"status={r.status_code} ct={content_type!r} body[:80]={body[:80]!r}"
    assert is_xml, (
        f"XML format not supported: status={r.status_code} content-type={content_type!r} "
        f"body[:120]={body[:120]!r}"
    )


# --- Probe 2: READ interaction on CodeSystem (item 2) ---
@pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
def test_probe_02_read_interaction(fhir_client, resource_type):
    """§4.7.1.1 item 2: 'the READ and SEARCH interactions for CodeSystem, ValueSet and ConceptMap'.

    READ = GET /{ResourceType}/{id}. Server SHALL support this interaction.
    """
    r = fhir_client.get(f"/fhir/{resource_type}/anything")
    # Acceptable: 200 with the resource, 404 with OperationOutcome. NOT acceptable: 404 with empty/HTML body.
    body = r.text or ""
    pytest.current_report_extra = (
        f"resource={resource_type} status={r.status_code} body[:80]={body[:80]!r}"
    )
    # SKEPTIC: server should return a structured FHIR response, not a bare 404 from FastAPI.
    if r.status_code == 404:
        assert "OperationOutcome" in body or "resourceType" in body, (
            f"{resource_type} READ returned 404 without OperationOutcome body: {body[:200]!r}"
        )
    else:
        # If status was 200 / 4xx other than 404, the body should still be FHIR JSON.
        assert r.headers.get("content-type", "").startswith(
            "application/fhir+json"
        ) or "resourceType" in body, (
            f"{resource_type} READ returned non-FHIR response: status={r.status_code} body={body[:200]!r}"
        )


# --- Probe 3: SEARCH interaction with required search params (items 2 + 3) ---
@pytest.mark.parametrize(
    "resource_type,param",
    [
        ("CodeSystem", "url"),
        ("CodeSystem", "version"),
        ("CodeSystem", "name"),
        ("CodeSystem", "title"),
        ("CodeSystem", "status"),
        ("ValueSet", "url"),
        ("ValueSet", "version"),
        ("ValueSet", "name"),
        ("ValueSet", "title"),
        ("ValueSet", "status"),
        ("ConceptMap", "url"),
        ("ConceptMap", "version"),
        ("ConceptMap", "name"),
        ("ConceptMap", "title"),
        ("ConceptMap", "status"),
    ],
)
def test_probe_03_search_params(fhir_client, resource_type, param):
    """§4.7.1.1 item 3: search parameters url, version, name, title, status SHALL be supported
    for CodeSystem, ValueSet, and ConceptMap."""
    r = fhir_client.get(f"/fhir/{resource_type}?{param}=test")
    body = r.text or ""
    pytest.current_report_extra = (
        f"{resource_type}?{param}=test status={r.status_code} body[:80]={body[:80]!r}"
    )
    # Acceptable: 200 with Bundle. NOT acceptable: 404 (route missing), 405 (method not allowed), 500.
    assert r.status_code != 404, (
        f"{resource_type}?{param}= → 404 (SEARCH interaction not implemented). "
        f"Spec requires SEARCH for {resource_type} with param '{param}'."
    )
    assert r.status_code != 405, (
        f"{resource_type}?{param}= → 405 (GET not allowed). SEARCH must be supported."
    )


# --- Probe 4: capabilities?mode=full required elements (item 4) ---
def test_probe_04_mode_full_required_elements(fhir_client):
    """§4.7.1.1 item 4: capabilities?mode=full returns CapabilityStatement with:
    url, version, name, title, status, date, description, kind=='instance', fhirVersion."""
    r = fhir_client.get("/fhir/metadata?mode=full")
    assert r.status_code == 200, f"mode=full → {r.status_code}"
    body = r.json()
    required = {
        "url": None,
        "version": None,
        "name": None,
        "title": None,
        "status": None,
        "date": None,
        "description": None,
        "kind": "instance",
        "fhirVersion": None,
    }
    missing = []
    wrong_value = []
    for key, expected in required.items():
        if key not in body:
            missing.append(key)
        elif expected is not None and body.get(key) != expected:
            wrong_value.append(f"{key}={body.get(key)!r} (expected {expected!r})")
    pytest.current_report_extra = f"missing={missing} wrong_value={wrong_value}"
    assert not missing, f"CapabilityStatement (mode=full) missing required elements: {missing}"
    assert not wrong_value, f"CapabilityStatement (mode=full) wrong values: {wrong_value}"


# --- Probe 5: capabilities?mode=terminology (item 5) ---
def test_probe_05_mode_terminology(fhir_client):
    """§4.7.1.1 item 5: capabilities?mode=terminology returns TerminologyCapabilities with:
    url, name, title, status, date, kind=='instance', codeSystem block with sub-elements."""
    r = fhir_client.get("/fhir/metadata?mode=terminology")
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:200]={body[:200]!r}"
    assert r.status_code == 200, f"mode=terminology → {r.status_code} (expected 200)"
    payload = r.json()
    assert payload.get("resourceType") == "TerminologyCapabilities", (
        f"mode=terminology returned resourceType={payload.get('resourceType')!r} "
        f"(expected 'TerminologyCapabilities'). Silent wrong-answer."
    )
    required_top = ["url", "name", "title", "status", "date", "kind", "fhirVersion"]
    missing_top = [k for k in required_top if k not in payload]
    assert not missing_top, f"TerminologyCapabilities missing top-level: {missing_top}"
    assert payload.get("kind") == "instance", (
        f"TerminologyCapabilities.kind={payload.get('kind')!r} (expected 'instance')"
    )


# --- Probe 6: default /fhir/metadata is treated as mode=full (item 4) ---
def test_probe_06_default_metadata_treated_as_full(fhir_client):
    """§4.7.1.1 item 4: 'absent mode parameter ... returning a CapabilityStatement'.
    The default /fhir/metadata (no mode) MUST also satisfy the mode=full requirements."""
    r = fhir_client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.json()
    required = ["url", "version", "name", "title", "status", "date", "description", "kind", "fhirVersion"]
    missing = [k for k in required if k not in body]
    pytest.current_report_extra = f"missing={missing} present={list(body.keys())}"
    assert not missing, (
        f"Default /fhir/metadata missing required elements (spec treats absent mode as mode=full): {missing}"
    )
    assert body.get("kind") == "instance", (
        f"Default metadata kind={body.get('kind')!r} (expected 'instance')"
    )


# --- Probe 7: malformed mode value ---
def test_probe_07_malformed_mode(fhir_client):
    """Adversarial: mode=invalid. Should NOT crash; should return either 400 OperationOutcome
    OR be treated as 'full' (per spec, only absent/full/terminology are valid). Either is
    acceptable as long as the response is FHIR-structured."""
    r = fhir_client.get("/fhir/metadata?mode=invalid")
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:120]={body[:120]!r}"
    # Must be a FHIR response, not a bare FastAPI error page.
    assert "resourceType" in body or r.status_code == 400, (
        f"mode=invalid → status={r.status_code} body={body[:200]!r} (not FHIR-structured)"
    )


# --- Probe 8: very long mode value (boundary) ---
def test_probe_08_long_mode_value(fhir_client):
    """Adversarial boundary: 10K-char mode value. Must not 500."""
    long_mode = "x" * 10000
    r = fhir_client.get(f"/fhir/metadata?mode={long_mode}")
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body_len={len(body)}"
    assert r.status_code != 500, (
        f"mode=<10K chars> → 500 (unbounded error). Body[:200]={body[:200]!r}"
    )
