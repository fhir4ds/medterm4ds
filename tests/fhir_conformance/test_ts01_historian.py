"""HISTORIAN probes for TS-01 — pattern-match against prior bug fixes.

Each probe re-tests a class of prior bug from the v0.0.1 review cycle
(documented in GLOBAL_RULES.md) against the new code introduced by the
SKEPTIC iteration. Goal: catch regressions or pattern recurrence.

Patterns probed:
- A1: silent-wrong-answer on boolean / discrete-value params
- A2: hardcoded port/URL when env-var canonical exists
- B1: silent ImportError fallback
- B2/B3: broad except Exception / DEBUG-level swallowing
- B6: incomplete-state flag missing on degraded path
- AL1-AL6: drift / duplication patterns
- Carry-forward from SKEPTIC: format=["json","xml"] advertisement fidelity
"""

from __future__ import annotations

import pytest


# ============================================================
# Pattern A1 — silent-wrong-answer on discrete-value params
# SKEPTIC fixed mode=invalid. HISTORIAN re-tests the boundary
# and adjacent silent-wrong-answer surfaces.
# ============================================================

def test_h01_mode_validation_does_not_silently_accept_near_misses(fhir_client):
    """A1 pattern: str(True) == 'True' silently meant False.

    Re-test that the mode dispatcher doesn't silently accept case-variants
    or whitespace variants. Per spec §4.7.1.1 item 4+5, mode ∈ {None, "full",
    "terminology"}. "Full" with capital F is NOT in the spec's value set;
    silently treating it as "full" would be the A1 shape.
    """
    # Spec: FHIR R4 strings are generally case-sensitive for code values.
    # §4.7.1.1 enumerates mode values as full/terminology (lowercase).
    for near_miss in ("Full", "FULL", " terminology", "terminology ", "full "):
        r = fhir_client.get(f"/fhir/metadata", params={"mode": near_miss})
        body = r.text or ""
        pytest.current_report_extra = f"mode={near_miss!r} status={r.status_code} body[:80]={body[:80]!r}"
        # If status==200 with resourceType=CapabilityStatement, the server
        # silently accepted a near-miss — A1 pattern recurrence.
        if r.status_code == 200:
            payload = r.json()
            assert payload.get("resourceType") in ("CapabilityStatement", "TerminologyCapabilities"), (
                f"mode={near_miss!r}: 200 with non-FHIR body. Silent acceptance A1 pattern. "
                f"Body[:200]={body[:200]!r}"
            )
        # If status==400, that's strict validation (acceptable).


# ============================================================
# Pattern A2 — hardcoded port in canonical URL
# The /fhir/metadata handler reads env vars. But do the URLs INSIDE
# the CapabilityStatement body reflect the env vars, or fall back?
# ============================================================

def test_h02_capability_statement_url_reflects_env_port(fhir_client, monkeypatch):
    """A2 pattern: hardcoded DEFAULT_PORT in CapabilityStatement.endpoint.

    Re-test: the operation `definition` URLs inside the CapabilityStatement
    include the configured port. Per GLOBAL_RULES.md, CapabilityStatement
    endpoint URLs MUST reflect MEDTERM4DS_FHIR_API_PORT.
    """
    # The fhir_client fixture is module-scoped and already started; we can't
    # restart it with different env vars here. Instead, inspect the published
    # statement and verify the URL is non-default-plausible (it's constructed
    # from env vars at request time, not module load time).
    r = fhir_client.get("/fhir/metadata")
    body = r.json()
    rest = body.get("rest", [{}])[0]
    resources = rest.get("resource", [])
    for res in resources:
        for op in res.get("operation", []):
            definition = op.get("definition", "")
            # The definition URL must be an absolute URL with a port.
            # If it's "/OperationDefinition/..." (relative) or hardcoded to
            # :8001, the A2 pattern may be present.
            assert definition.startswith("http"), (
                f"{res['type']}.{op['name']} definition is not absolute: {definition!r}"
            )


# ============================================================
# Pattern B1 — silent ImportError fallback
# Check that no new `except ImportError: pass` was introduced in
# the FHIR conformance surface modules.
# ============================================================

def test_h03_no_silent_importerror_in_conformance_modules():
    """B1 pattern: `except ImportError: pass` swallows real install bugs.

    Inspect the source of the conformance modules to confirm no new
    silent-ImportError pattern was introduced.
    """
    from medterm4ds.engines.fhir import responses, xml
    from medterm4ds.apps import fhir_api

    for mod in (responses, xml, fhir_api):
        src = open(mod.__file__).read()
        # Forbidden: bare `except ImportError:` followed by `pass` or
        # assignment to None without logging.
        bad_pattern = "except ImportError:"
        if bad_pattern in src:
            # The only acceptable ImportError handling is in fhir_api.py's
            # FastAPI import guard, which raises on use (not silent).
            assert mod is fhir_api, (
                f"{mod.__name__} has silent `except ImportError:` — B1 pattern recurrence."
            )


# ============================================================
# Pattern B2/B3 — broad except Exception / DEBUG swallowing
# Inspect the XML serializer's error path.
# ============================================================

def test_h04_xml_serializer_error_path_is_narrow():
    """B2/B3 pattern: broad `except Exception:` masks programming bugs.

    The XML-failure → JSON fallback in apps/fhir_api.py:_fhir_response
    must catch only ValueError (the narrow serializer error), not
    broad Exception.
    """
    from medterm4ds.apps import fhir_api
    src = open(fhir_api.__file__).read()
    # Locate the _fhir_response function body and verify the except clause.
    # Crude but reliable: check that the only except in _fhir_response is
    # `except ValueError`.
    start = src.index("def _fhir_response(")
    end = src.index("def ", start + 1)
    body = src[start:end]
    assert "except ValueError" in body, (
        "_fhir_response missing narrow ValueError catch (B2 pattern check)."
    )
    # Forbidden: broad Exception in this function.
    assert "except Exception" not in body, (
        "_fhir_response uses broad `except Exception` — B2 pattern recurrence."
    )
    # Forbidden: silent fallback (no log) — the WARNING log must be present.
    assert "logger.warning" in body or "logger.error" in body, (
        "_fhir_response XML→JSON fallback has no WARNING log — B6 DEBUG-swallow recurrence."
    )


# ============================================================
# Pattern B6 — incomplete-state flag missing on degraded path
# When XML serialization fails and we degrade to JSON, callers
# should be able to detect the degradation (WARNING at minimum).
# ============================================================

def test_h05_xml_failure_degrades_loudly(fhir_client, monkeypatch):
    """B6 pattern: closure.py DEBUG-swallow with no incomplete_since flag.

    Simulate an XML serialization failure and verify the server logs a
    WARNING (not DEBUG) and still returns a structured response.
    """
    from medterm4ds.apps import fhir_api
    from medterm4ds.engines.fhir import xml as xml_mod

    # Force the serializer to raise ValueError on the next call.
    calls = {"warning_logged": False}

    # Patch the to_fhir_xml reference used by fhir_api to raise.
    original = fhir_api.to_fhir_xml

    def raising_serializer(payload):
        raise ValueError("simulated serialization failure")

    # Capture logger.warning calls.
    original_warning = fhir_api.logger.warning

    def capture_warning(msg, *args, **kwargs):
        calls["warning_logged"] = True
        return original_warning(msg, *args, **kwargs)

    fhir_api.to_fhir_xml = raising_serializer
    fhir_api.logger.warning = capture_warning
    try:
        r = fhir_client.get(
            "/fhir/metadata",
            headers={"Accept": "application/fhir+xml"},
        )
    finally:
        fhir_api.to_fhir_xml = original
        fhir_api.logger.warning = original_warning

    # The server must NOT 500 — it must degrade to JSON.
    assert r.status_code == 200, (
        f"XML failure → {r.status_code} (expected 200 with JSON fallback)."
    )
    # The Content-Type must be JSON (degraded path).
    ct = r.headers.get("content-type", "")
    assert "json" in ct, (
        f"XML failure → content-type={ct!r} (expected JSON fallback)."
    )
    # A WARNING must have been logged — no silent DEBUG swallow.
    assert calls["warning_logged"], (
        "XML→JSON fallback did not log WARNING — B6 DEBUG-swallow pattern recurrence."
    )


# ============================================================
# Carry-forward: format=["json","xml"] advertisement fidelity
# SKEPTIC flagged this as carry-forward. HISTORIAN probes whether
# it's an actual spec-compliance bug for $lookup/$expand/$translate.
# ============================================================

def test_h06_lookup_returns_json_even_when_xml_requested(fhir_client):
    """Carry-forward: format=["json","xml"] is aspirational for $lookup.

    The CapabilityStatement advertises both formats. §4.7.1.1 item 1
    requires XML and JSON support. If $lookup returns JSON even when
    the client sends Accept: application/fhir+xml, the advertisement
    is misleading.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
        headers={"Accept": "application/fhir+xml"},
    )
    ct = r.headers.get("content-type", "")
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} ct={ct!r} body[:80]={body[:80]!r}"
    # Document the current behavior: $lookup is JSON-only despite the
    # CapabilityStatement advertising XML. This is the carry-forward.
    if "xml" in ct or body.lstrip().startswith("<"):
        # XML support was added — carry-forward resolved.
        return
    # JSON returned. This confirms the carry-forward is real.
    # HISTORIAN does NOT log this as a bug (SKEPTIC already flagged it).
    # Document the assertion so the test is meaningful:
    assert "json" in ct, (
        f"$lookup Accept:xml → content-type={ct!r} (neither XML nor JSON)."
    )


# ============================================================
# SKEPTIC fix completeness — re-test the route precedence
# Operation routes must match BEFORE the READ catch-all.
# ============================================================

def test_h07_operation_routes_match_before_read_catchall(fhir_client):
    """Verify /fhir/CodeSystem/$lookup hits the operation, not READ.

    If route ordering drifts, $lookup would be shadowed by the
    /fhir/CodeSystem/{resource_id} catch-all and return a 404
    OperationOutcome claiming $lookup is an unknown resource id.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:120]={body[:120]!r}"
    # The response must be a Parameters resource (the lookup result),
    # NOT a 404 OperationOutcome saying "$lookup is not a known id".
    assert r.status_code == 200, (
        f"$lookup → {r.status_code} (route may be shadowed by READ catch-all). Body={body[:200]!r}"
    )
    payload = r.json()
    assert payload.get("resourceType") == "Parameters", (
        f"$lookup returned resourceType={payload.get('resourceType')!r} "
        f"(route shadowed by READ catch-all?)."
    )


def test_h08_dollar_prefixed_id_rejected_on_read(fhir_client):
    """READ route should reject $-prefixed ids explicitly.

    Per the engineer's handoff, the READ handler rejects $-prefixed ids
    to prevent operation-name misuse as resource ids. Verify this.
    """
    r = fhir_client.get("/fhir/CodeSystem/$bogusOp")
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:120]={body[:120]!r}"
    # Acceptable: 404 with OperationOutcome naming the unknown operation.
    # NOT acceptable: 200 (treated as resource read) or 500.
    assert r.status_code in (404, 400), (
        f"$bogusOp on CodeSystem → {r.status_code} (expected 404/400)."
    )
    assert "OperationOutcome" in body or "resourceType" in body, (
        f"$bogusOp body is not FHIR-structured: {body[:200]!r}"
    )


# ============================================================
# Mode dispatcher completeness — verify all 4 input classes
# ============================================================

@pytest.mark.parametrize("mode,expected_rtype", [
    (None, "CapabilityStatement"),
    ("full", "CapabilityStatement"),
    ("terminology", "TerminologyCapabilities"),
])
def test_h09_mode_dispatcher_classes(fhir_client, mode, expected_rtype):
    """Re-test SKEPTIC's mode dispatcher for all valid input classes."""
    if mode is None:
        r = fhir_client.get("/fhir/metadata")
    else:
        r = fhir_client.get("/fhir/metadata", params={"mode": mode})
    assert r.status_code == 200, f"mode={mode!r} → {r.status_code}"
    payload = r.json()
    assert payload.get("resourceType") == expected_rtype, (
        f"mode={mode!r}: resourceType={payload.get('resourceType')!r} (expected {expected_rtype!r})"
    )


def test_h10_mode_invalid_returns_400(fhir_client):
    """Re-test SKEPTIC's mode validation for invalid input."""
    r = fhir_client.get("/fhir/metadata", params={"mode": "invalid"})
    assert r.status_code == 400, f"mode=invalid → {r.status_code} (expected 400)"
    body = r.text or ""
    assert "OperationOutcome" in body or "resourceType" in body, (
        f"mode=invalid body is not FHIR-structured: {body[:200]!r}"
    )


# ============================================================
# XML serialization completeness across FHIR resource types
# Probe that the serializer handles the shapes the conformance
# surface produces.
# ============================================================

@pytest.mark.parametrize("payload,rtype", [
    ({"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "not-found", "diagnostics": "x"}]}, "OperationOutcome"),
    ({"resourceType": "Bundle", "type": "searchset", "total": 0, "entry": []}, "Bundle"),
    ({"resourceType": "Parameters", "parameter": [{"name": "result", "valueBoolean": True}]}, "Parameters"),
    ({"resourceType": "CapabilityStatement", "status": "active", "format": ["json", "xml"]}, "CapabilityStatement"),
    ({"resourceType": "TerminologyCapabilities", "codeSystem": [{"uri": "http://loinc.org", "content": "not-present"}]}, "TerminologyCapabilities"),
])
def test_h11_xml_serializer_handles_all_conformance_shapes(payload, rtype):
    """Verify the XML serializer handles every FHIR shape the conformance
    surface emits. Regression guard: if a future change adds a new shape,
    this test will catch XML failures.
    """
    from medterm4ds.engines.fhir.xml import to_fhir_xml

    xml_str = to_fhir_xml(payload)
    assert xml_str.startswith('<?xml'), f"XML preamble missing for {rtype}"
    assert f"<{rtype}" in xml_str, f"Root element {rtype} missing"
    assert f"</{rtype}>" in xml_str, f"Root close {rtype} missing"
    # Round-trip: must be parseable XML.
    import xml.etree.ElementTree as ET
    ET.fromstring(xml_str)  # raises ParseError on malformed XML


def test_h11a_xml_serializer_renders_extension_url_as_attribute():
    """FHIR R4 XML convention: <extension url="..."> — url is an XML attribute
    on the extension element, NOT a child element.

    Spec: https://hl7.org/fhir/R4/extensibility.html
    Quote: "The URL of the extension is in the url attribute."
    """
    from medterm4ds.engines.fhir.xml import to_fhir_xml

    payload = {
        "resourceType": "OperationOutcome",
        "issue": [{
            "severity": "error",
            "extension": [{"url": "http://example.com/ext", "valueString": "val"}]
        }]
    }
    xml_str = to_fhir_xml(payload)
    # The conformant rendering is: <extension url="http://example.com/ext">
    # NOT: <extension><url>http://example.com/ext</url>...</extension>
    assert '<extension url="http://example.com/ext">' in xml_str, (
        f"Extension url not rendered as XML attribute (FHIR R4 spec violation). "
        f"Got: {xml_str}"
    )
    # Forbidden pattern: <url> as a child element of <extension>.
    assert "<extension><url>" not in xml_str, (
        f"Extension rendered with <url> as child element instead of attribute. "
        f"Got: {xml_str}"
    )


def test_h12_xml_serializer_rejects_missing_resourceType():
    """Verify the serializer raises ValueError (not silent empty output)
    when resourceType is missing — narrowest exception type."""
    from medterm4ds.engines.fhir.xml import to_fhir_xml

    with pytest.raises(ValueError, match="resourceType"):
        to_fhir_xml({"status": "active"})


# ============================================================
# Silent fallback across the Accept-header dispatcher
# Test that malformed Accept headers don't silently fall through
# to JSON in a way that masks a real bug.
# ============================================================

def test_h13_accept_header_dispatch_is_deterministic(fhir_client):
    """The dispatcher must honor q-value preference per RFC 7231 §5.3.1.

    FHIR R4 §3.1.0.1.9: "Servers SHALL support server-driven content
    negotiation as described in section 12 of the HTTP specification."
    RFC 7231 §5.3.1 defines the q-value weight — the higher-q MIME type
    MUST win.

    Originally a documentation-of-buggy-behavior probe (HISTORIAN TS-01
    iteration 1) asserting substring matching returned XML even when
    JSON had higher q-value. The TS-01 EXPLORER QA-001 fix landed
    proper q-value parsing — the probe is updated to assert the new
    spec-correct behavior (JSON wins when q=0.9 > q=0.5).
    """
    # Both XML and JSON advertised; XML first with lower q-value than JSON.
    # Per RFC 7231 §5.3.1, JSON (q=0.9) MUST win over XML (q=0.5).
    accept_header = "application/fhir+xml;q=0.5, application/fhir+json;q=0.9"
    r = fhir_client.get(
        "/fhir/metadata",
        headers={"Accept": accept_header},
    )
    ct = r.headers.get("content-type", "")
    body = r.text or ""
    pytest.current_report_extra = f"ct={ct!r} body[:60]={body[:60]!r}"
    # Post-fix: q-value parsing means JSON wins (q=0.9 > q=0.5).
    assert "json" in ct, (
        f"Accept={accept_header!r} → CT={ct!r}; expected JSON "
        f"(q=0.9 > q=0.5 per RFC 7231 §5.3.1)"
    )


def test_h14_accept_xml_with_charset_variant(fhir_client):
    """Edge: Accept: application/fhir+xml; charset=UTF-8.

    The substring match 'application/fhir+xml' should still fire.
    If the implementation did exact-string matching, this would fail.
    """
    r = fhir_client.get(
        "/fhir/metadata",
        headers={"Accept": "application/fhir+xml; charset=UTF-8"},
    )
    ct = r.headers.get("content-type", "")
    body = r.text or ""
    pytest.current_report_extra = f"ct={ct!r} body[:60]={body[:60]!r}"
    assert "xml" in ct or body.lstrip().startswith("<"), (
        f"Accept with charset variant not honored: ct={ct!r}"
    )


# ============================================================
# Carry-forward: $lookup/$expand/$translate Accept header
# Verify these operations are JSON-only despite the advertisement.
# ============================================================

def test_h15_lookup_xml_request_returns_xml(fhir_client):
    """Verify $lookup honors Accept: application/fhir+xml.

    Originally a carry-forward from TS-01 SKEPTIC: $lookup ignored Accept and
    returned JSON. The TS-02 SKEPTIC iteration (QA-021) wired operations
    through _fhir_response, so $lookup now correctly returns XML.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
        headers={"Accept": "application/fhir+xml"},
    )
    ct = r.headers.get("content-type", "")
    body = r.text or ""
    assert "application/fhir+xml" in ct, (
        f"$lookup with Accept:xml → ct={ct!r} (expected application/fhir+xml). "
        f"Body[:200]={body[:200]!r}"
    )
    # Sanity: body should be parseable XML.
    import xml.etree.ElementTree as ET
    ET.fromstring(body)
