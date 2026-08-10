"""TS-01 / SKEPTIC resweep — Terminology Service RESTful API Conformance (§4.7.2.1).

This is a FRESH resweep for the 2026-08-08 full-sweep 0.0.1 release run.
The baseline `test_ts01_skeptic.py` (99 probes across 4 personalities) is the
prior run's coverage — these probes are NEW hostile inputs that look for
regressions and previously-undetected bugs.

Spec source: https://build.fhir.org/terminology-service.html
Relevant section: §4.7.2.1 "Maneuver Set / RESTful API"

Verbatim spec mandates (from §4.7.2.1):
  "A FHIR terminology service SHALL support:
   - the XML and JSON FHIR formats
   - the READ and SEARCH interactions for CodeSystem, ValueSet and ConceptMap
   - the following elements as search parameters for CodeSystem, ValueSet and
     ConceptMap: url, version, name, title, status
   - the capabilities interaction with an absent mode parameter or a mode
     parameter with a value of full, returning a CapabilityStatement resource
     which includes the following elements: url, version, name, title, status,
     date, description, kind with a fixed value of instance, and fhirVersion
   - the capabilities interaction with a mode parameter with a value of
     terminology returning a TerminologyCapabilities resource which includes
     the following elements: url, name, title, status, date, kind with a fixed
     value of instance, and a codeSystem data element containing each of the
     following sub elements for each code system supported for terminology
     services: codeSystem.uri, codeSystem.version (for code systems with a
     version), codeSystem.version.code (for each version), codeSystem.content"

Each probe is a SKEPTIC-style adversarial test:
- Probe an edge of one SHALL item from §4.7.2.1.
- Capture the actual behavior (status, body, headers).
- A probe "fails" (reveals a bug) when the actual behavior violates the spec.
"""

from __future__ import annotations

import pytest


# =============================================================================
# Item 1: XML and JSON FHIR formats supported
# =============================================================================

class TestItem1XmlJsonFormats:
    """§4.7.2.1 item 1: 'the XML and JSON FHIR formats SHALL be supported.'"""

    def test_s10_metadata_xml_via_accept_header(self, fhir_client):
        """GET /fhir/metadata with Accept: application/fhir+xml MUST return
        application/fhir+xml Content-Type AND an XML body (not JSON)."""
        r = fhir_client.get("/fhir/metadata", headers={"Accept": "application/fhir+xml"})
        assert r.status_code == 200, f"status={r.status_code}"
        ct = r.headers.get("content-type", "")
        assert "application/fhir+xml" in ct, (
            f"Accept: application/fhir+xml → content-type={ct!r} "
            f"(spec mandates application/fhir+xml per §4.7.2.1 item 1)"
        )
        # Body must actually be XML, not JSON re-labeled as XML.
        body = r.text.lstrip()
        assert body.startswith("<"), (
            f"Accept: application/fhir+xml but body is not XML: body[:80]={body[:80]!r}"
        )

    def test_s11_metadata_xml_via_format_query_param(self, fhir_client):
        """GET /fhir/metadata?_format=xml MUST return XML per §3.1.0.1.11
        (the _format query parameter overrides the Accept header)."""
        r = fhir_client.get("/fhir/metadata?_format=xml")
        assert r.status_code == 200, f"status={r.status_code}"
        ct = r.headers.get("content-type", "")
        assert "application/fhir+xml" in ct, (
            f"_format=xml → content-type={ct!r} (spec mandates application/fhir+xml)"
        )
        assert r.text.lstrip().startswith("<"), (
            f"_format=xml but body is not XML: body[:80]={r.text[:80]!r}"
        )

    def test_s12_metadata_json_default(self, fhir_client):
        """GET /fhir/metadata (no Accept, no _format) MUST default to JSON
        with Content-Type application/fhir+json (not application/json)."""
        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"default Accept → content-type={ct!r} (Starlette's default "
            f"application/json would violate §3.1.0.1.9)"
        )

    def test_s13_metadata_json_explicit_accept(self, fhir_client):
        """GET /fhir/metadata with Accept: application/fhir+json MUST return
        application/fhir+json Content-Type."""
        r = fhir_client.get("/fhir/metadata", headers={"Accept": "application/fhir+json"})
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"Accept: application/fhir+json → content-type={ct!r}"
        )

    def test_s14_format_json_overrides_accept_xml(self, fhir_client):
        """§3.1.0.1.11: _format=json MUST override Accept: application/fhir+xml.
        Hostile: client sets Accept=xml but _format=json — server MUST return JSON."""
        r = fhir_client.get(
            "/fhir/metadata?_format=json",
            headers={"Accept": "application/fhir+xml"},
        )
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"_format=json should override Accept=xml, but content-type={ct!r}"
        )

    def test_s15_format_xml_overrides_accept_json(self, fhir_client):
        """§3.1.0.1.11 mirror: _format=xml MUST override Accept: application/fhir+json."""
        r = fhir_client.get(
            "/fhir/metadata?_format=xml",
            headers={"Accept": "application/fhir+json"},
        )
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/fhir+xml" in ct, (
            f"_format=xml should override Accept=json, but content-type={ct!r}"
        )

    def test_s16_xml_on_mode_terminology(self, fhir_client):
        """XML format MUST also be honored for mode=terminology path
        (item 1 applies uniformly to all conformance responses)."""
        r = fhir_client.get(
            "/fhir/metadata?mode=terminology&_format=xml",
        )
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/fhir+xml" in ct, (
            f"mode=terminology&_format=xml → content-type={ct!r} "
            f"(XML support is uniform per §4.7.2.1 item 1)"
        )
        assert r.text.lstrip().startswith("<"), (
            f"mode=terminology&_format=xml but body is not XML"
        )

    def test_s17_xml_on_invalid_mode_error_path(self, fhir_client):
        """Error path MUST honor XML negotiation (§3.1.0.1.9: error responses
        use the same MIME type). mode=invalid&_format=xml → OperationOutcome in XML."""
        r = fhir_client.get(
            "/fhir/metadata?mode=invalid&_format=xml",
        )
        assert r.status_code == 400
        ct = r.headers.get("content-type", "")
        assert "application/fhir+xml" in ct, (
            f"error path with _format=xml → content-type={ct!r} "
            f"(§3.1.0.1.9 mandates the same MIME on error path)"
        )
        assert r.text.lstrip().startswith("<"), "error path body should be XML"

    def test_s18_accept_star_star_defaults_json(self, fhir_client):
        """Accept: */* MUST default to JSON (not 406 Not Acceptable, not crash)."""
        r = fhir_client.get("/fhir/metadata", headers={"Accept": "*/*"})
        assert r.status_code == 200, f"*/* → {r.status_code}"
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"Accept: */* → content-type={ct!r} (default must be JSON)"
        )

    def test_s19_accept_unrecognized_format_defaults_json(self, fhir_client):
        """Hostile: Accept: application/foobar — server MUST NOT 500 or crash.
        Per §3.1.0.1.11 + FHIR general resilience, the server defaults to JSON
        for unrecognized formats rather than rejecting the request."""
        r = fhir_client.get("/fhir/metadata", headers={"Accept": "application/foobar"})
        assert r.status_code != 500, (
            f"Accept: application/foobar → 500 (unbounded error). "
            f"Body={r.text[:200]!r}"
        )
        # Either 200 with JSON (lenient default) or 406 Not Acceptable with
        # OperationOutcome is conformant. The spec leaves this open.
        ct = r.headers.get("content-type", "")
        assert "fhir+" in ct or "resourceType" in r.text, (
            f"Accept: application/foobar → non-FHIR response: status={r.status_code} "
            f"ct={ct!r} body[:120]={r.text[:120]!r}"
        )


# =============================================================================
# Item 2: READ and SEARCH interactions for CodeSystem, ValueSet, ConceptMap
# =============================================================================

class TestItem2ReadSearch:
    """§4.7.2.1 item 2: 'the READ and SEARCH interactions for CodeSystem,
    ValueSet and ConceptMap.'"""

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s20_read_returns_fhir_content_type(self, fhir_client, resource_type):
        """READ response MUST use application/fhir+json Content-Type, not
        application/json (Starlette default)."""
        r = fhir_client.get(f"/fhir/{resource_type}/anything")
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"READ {resource_type}/anything → content-type={ct!r} "
            f"(must be application/fhir+json per §3.1.0.1.9)"
        )

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s21_read_returns_operationoutcome_on_404(self, fhir_client, resource_type):
        """READ of a non-existent id MUST return a FHIR OperationOutcome body
        (resourceType=OperationOutcome), not Starlette's {"detail":"Not Found"}."""
        r = fhir_client.get(f"/fhir/{resource_type}/nonexistent-id-12345")
        assert r.status_code == 404
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"READ of unknown {resource_type} id → resourceType="
            f"{body.get('resourceType')!r} (must be OperationOutcome)"
        )

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s22_read_dollar_prefixed_non_operation_id_rejected(self, fhir_client, resource_type):
        """Hostile: GET /fhir/{Resource}/$notanoperation — the $-prefix with
        an unrecognized operation name MUST return 404 OperationOutcome (not
        500, not fall through to a wrong route). Note: $lookup/$expand/etc.
        on the appropriate resource DO match real operation routes and are
        correct behavior; this probe uses a deliberately non-operation $-id."""
        for hostile_id in ["$notanoperation", "$$bad", "$DROP TABLE"]:
            r = fhir_client.get(f"/fhir/{resource_type}/{hostile_id}")
            assert r.status_code != 500, (
                f"$-prefix id={hostile_id!r} → 500. Body={r.text[:200]!r}"
            )
            body = r.json()
            assert body.get("resourceType") == "OperationOutcome", (
                f"$-prefix id={hostile_id!r} → resourceType="
                f"{body.get('resourceType')!r} (must be OperationOutcome)"
            )

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s23_read_very_long_id_no_500(self, fhir_client, resource_type):
        """Hostile boundary: 10K-char id. Must not 500 (no unbounded error)."""
        long_id = "x" * 10000
        r = fhir_client.get(f"/fhir/{resource_type}/{long_id}")
        assert r.status_code != 500, (
            f"10K-char id → 500 (unbounded error). Body={r.text[:200]!r}"
        )

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s24_read_id_with_special_chars(self, fhir_client, resource_type):
        """Hostile: id with special chars (path traversal, SQL injection attempt).
        Must not 500, must not leak a traceback."""
        for hostile_id in ["..%2F..%2Fetc%2Fpasswd", "'; DROP TABLE--", "<script>"]:
            r = fhir_client.get(f"/fhir/{resource_type}/{hostile_id}")
            assert r.status_code != 500, (
                f"hostile id={hostile_id!r} → 500 (information disclosure). "
                f"Body={r.text[:200]!r}"
            )

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s25_search_returns_bundle(self, fhir_client, resource_type):
        """SEARCH (GET /fhir/{Resource} with no params) MUST return a Bundle
        (resourceType=Bundle, type=searchset) with FHIR Content-Type."""
        r = fhir_client.get(f"/fhir/{resource_type}")
        assert r.status_code == 200, f"SEARCH {resource_type} → {r.status_code}"
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"SEARCH {resource_type} → content-type={ct!r}"
        )
        body = r.json()
        assert body.get("resourceType") == "Bundle", (
            f"SEARCH {resource_type} → resourceType={body.get('resourceType')!r}"
        )
        assert body.get("type") == "searchset", (
            f"SEARCH {resource_type} → Bundle.type={body.get('type')!r}"
        )
        # entry must be a list (even if empty) — clients iterate it.
        assert isinstance(body.get("entry"), list), (
            f"SEARCH {resource_type} → entry is not a list: {type(body.get('entry'))}"
        )

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s26_search_unknown_resource_type_returns_operationoutcome(self, fhir_client, resource_type):
        """SEARCH for an unsupported resource type (Patient, Observation) MUST
        return a FHIR OperationOutcome, not Starlette's {"detail":"Not Found"}."""
        for unknown in ["Patient", "Observation", "MedicationStatement"]:
            r = fhir_client.get(f"/fhir/{unknown}")
            assert r.status_code in (404,), (
                f"SEARCH unknown {unknown} → status={r.status_code} (must be 404)"
            )
            body = r.json()
            assert body.get("resourceType") == "OperationOutcome", (
                f"SEARCH unknown {unknown} → resourceType="
                f"{body.get('resourceType')!r} (must be OperationOutcome)"
            )
            ct = r.headers.get("content-type", "")
            assert "application/fhir+json" in ct

    @pytest.mark.parametrize(
        "resource_type,param",
        [
            (rt, p)
            for rt in ["CodeSystem", "ValueSet", "ConceptMap"]
            for p in ["url", "version", "name", "title", "status"]
        ],
    )
    def test_s27_search_required_param_accepted(self, fhir_client, resource_type, param):
        """SEARCH with each spec-required param MUST return 200 Bundle, not 400
        (param must be accepted — FastAPI must not reject it as unknown)."""
        r = fhir_client.get(f"/fhir/{resource_type}?{param}=test-value")
        assert r.status_code == 200, (
            f"{resource_type}?{param}=test-value → {r.status_code} "
            f"(spec-required param rejected). Body={r.text[:200]!r}"
        )
        body = r.json()
        assert body.get("resourceType") == "Bundle"


# =============================================================================
# Item 3: Search parameters url, version, name, title, status (all 3 resources)
# =============================================================================

class TestItem3SearchParams:
    """§4.7.2.1 item 3: 'url, version, name, title, status as search parameters
    for CodeSystem, ValueSet and ConceptMap.'"""

    @pytest.mark.parametrize(
        "resource_type,param",
        [
            (rt, p)
            for rt in ["CodeSystem", "ValueSet", "ConceptMap"]
            for p in ["url", "version", "name", "title", "status"]
        ],
    )
    def test_s30_search_param_with_hostile_value(self, fhir_client, resource_type, param):
        """Hostile: each spec-required param with SQL injection / very long value.
        Must not 500; must return a FHIR Bundle or OperationOutcome."""
        hostile_values = [
            "'; DROP TABLE--",
            "x" * 5000,
            "%3Cscript%3Ealert(1)%3C%2Fscript%3E",
            "http://evil.com/x?y=1&z=2",
        ]
        for val in hostile_values:
            r = fhir_client.get(f"/fhir/{resource_type}?{param}={val}")
            assert r.status_code != 500, (
                f"{resource_type}?{param}={val[:60]!r} → 500 (information disclosure). "
                f"Body={r.text[:200]!r}"
            )
            ct = r.headers.get("content-type", "")
            assert "fhir+" in ct or "resourceType" in r.text, (
                f"non-FHIR response: ct={ct!r}"
            )

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s31_search_all_params_combined(self, fhir_client, resource_type):
        """SEARCH with all 5 required params in one request MUST return 200
        Bundle (FastAPI must not reject the combination)."""
        r = fhir_client.get(
            f"/fhir/{resource_type}?url=http://x&version=1&name=Foo&title=Foo&status=active"
        )
        assert r.status_code == 200, (
            f"{resource_type} all-params → {r.status_code}. Body={r.text[:200]!r}"
        )
        body = r.json()
        assert body.get("resourceType") == "Bundle"

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s32_search_param_empty_value(self, fhir_client, resource_type):
        """Hostile: empty value for each required param (?url=). Must not 500."""
        for param in ["url", "version", "name", "title", "status"]:
            r = fhir_client.get(f"/fhir/{resource_type}?{param}=")
            assert r.status_code != 500, (
                f"{resource_type}?{param}= (empty) → 500. Body={r.text[:200]!r}"
            )

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s33_search_unrelated_param_accepted(self, fhir_client, resource_type):
        """Hostile: SEARCH with an unrelated param (?foo=bar). FastAPI must not
        reject it as an unknown query param — FHIR servers SHOULD ignore unknown
        params. Must return 200 Bundle, not 400/422."""
        r = fhir_client.get(f"/fhir/{resource_type}?foo=bar&unrelated=1")
        # FHIR clients frequently send params the server doesn't recognize
        # (e.g., _count, _summary). Server SHOULD accept gracefully.
        assert r.status_code != 422, (
            f"{resource_type}?foo=bar → 422 (FastAPI rejecting unknown param). "
            f"Body={r.text[:200]!r}"
        )
        # If status is 200, body must be Bundle.
        if r.status_code == 200:
            assert r.json().get("resourceType") == "Bundle"

    @pytest.mark.parametrize("resource_type", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_s34_search_xml_format(self, fhir_client, resource_type):
        """SEARCH with _format=xml MUST return XML Bundle (item 1 applies
        uniformly)."""
        r = fhir_client.get(f"/fhir/{resource_type}?_format=xml")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/fhir+xml" in ct, (
            f"SEARCH {resource_type}?_format=xml → content-type={ct!r}"
        )
        assert r.text.lstrip().startswith("<"), (
            f"SEARCH {resource_type}?_format=xml but body is not XML"
        )


# =============================================================================
# Item 4: capabilities?mode=full returns CapabilityStatement with required elements
# =============================================================================

class TestItem4ModeFull:
    """§4.7.2.1 item 4: capabilities with mode absent OR mode=full MUST return
    a CapabilityStatement with: url, version, name, title, status, date,
    description, kind=instance, fhirVersion."""

    def test_s40_mode_full_has_all_required_elements(self, fhir_client):
        """mode=full response MUST include every required element verbatim
        per §4.7.2.1 item 4."""
        r = fhir_client.get("/fhir/metadata?mode=full")
        assert r.status_code == 200
        body = r.json()
        assert body.get("resourceType") == "CapabilityStatement"
        required = ["url", "version", "name", "title", "status", "date",
                    "description", "kind", "fhirVersion"]
        missing = [k for k in required if k not in body]
        assert not missing, (
            f"mode=full CapabilityStatement missing required elements: {missing}"
        )
        assert body["kind"] == "instance", (
            f"kind={body['kind']!r} (spec mandates fixed value 'instance')"
        )

    def test_s41_mode_absent_treated_as_full(self, fhir_client):
        """§4.7.2.1 item 4: 'absent mode parameter ... returning a
        CapabilityStatement'. Default metadata MUST be CapabilityStatement,
        not TerminologyCapabilities."""
        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200
        body = r.json()
        assert body.get("resourceType") == "CapabilityStatement", (
            f"absent mode → resourceType={body.get('resourceType')!r} "
            f"(spec treats absent mode as mode=full)"
        )

    def test_s42_capabilitystatement_advertises_rest_resources(self, fhir_client):
        """The CapabilityStatement.rest[].resource[] MUST advertise CodeSystem,
        ValueSet, ConceptMap with read + search-type interactions (since the
        spec mandates READ and SEARCH support in item 2)."""
        r = fhir_client.get("/fhir/metadata?mode=full")
        body = r.json()
        rest = body.get("rest", [])
        assert rest, "CapabilityStatement has no rest[] block"
        resources = {res["type"]: res for res in rest[0].get("resource", [])}
        for expected in ["CodeSystem", "ValueSet", "ConceptMap"]:
            assert expected in resources, (
                f"CapabilityStatement.rest[].resource missing {expected}"
            )
            interactions = {i["code"] for i in resources[expected].get("interaction", [])}
            assert "read" in interactions, (
                f"{expected} missing 'read' interaction advertisement"
            )
            assert "search-type" in interactions, (
                f"{expected} missing 'search-type' interaction advertisement"
            )

    def test_s43_capabilitystatement_advertises_search_params(self, fhir_client):
        """Per §4.7.2.1 item 3, the CapabilityStatement.rest[].resource[]
        MUST advertise url/version/name/title/status as searchParam for each
        of the 3 resources. (CapabilityStatement discovery is the spec-mandated
        way clients learn which params the server supports.)"""
        r = fhir_client.get("/fhir/metadata?mode=full")
        body = r.json()
        rest = body.get("rest", [{}])[0]
        for resource in rest.get("resource", []):
            rtype = resource["type"]
            params = {p["name"] for p in resource.get("searchParam", [])}
            for required_param in ["url", "version", "name", "title", "status"]:
                assert required_param in params, (
                    f"{rtype} CapabilityStatement.searchParam missing "
                    f"'{required_param}' (spec §4.7.2.1 item 3)"
                )

    def test_s44_capabilitystatement_has_format_xml_json(self, fhir_client):
        """The CapabilityStatement.format array MUST include both 'json' and
        'xml' (item 1 conformance advertisement)."""
        r = fhir_client.get("/fhir/metadata?mode=full")
        body = r.json()
        formats = body.get("format", [])
        assert "json" in formats, (
            f"CapabilityStatement.format={formats!r} missing 'json'"
        )
        assert "xml" in formats, (
            f"CapabilityStatement.format={formats!r} missing 'xml'"
        )

    def test_s45_status_value_is_active_or_draft(self, fhir_client):
        """The CapabilityStatement.status MUST be a valid PublicationStatus
        (active | draft | retired | unknown). Hostile: catch a future bug
        where status is set to an off-spec string."""
        r = fhir_client.get("/fhir/metadata?mode=full")
        body = r.json()
        status = body.get("status")
        assert status in {"active", "draft", "retired", "unknown"}, (
            f"CapabilityStatement.status={status!r} is not a valid PublicationStatus"
        )

    def test_s46_fhirversion_is_4_0_1(self, fhir_client):
        """The CapabilityStatement.fhirVersion MUST be 4.0.1 (medterm4ds
        targets R4). Hostile: catch drift to 4.x or 5.0."""
        r = fhir_client.get("/fhir/metadata?mode=full")
        body = r.json()
        fv = body.get("fhirVersion")
        assert fv == "4.0.1", (
            f"fhirVersion={fv!r} (expected '4.0.1' for FHIR R4)"
        )

    def test_s47_mode_normative_returns_capabilitystatement(self, fhir_client):
        """Hostile: mode=normative — spec doesn't mention it but the server
        accepts it. Must NOT 500 and must return a FHIR resource."""
        r = fhir_client.get("/fhir/metadata?mode=normative")
        assert r.status_code != 500, f"mode=normative → 500. Body={r.text[:200]!r}"
        ct = r.headers.get("content-type", "")
        assert "fhir+" in ct, f"mode=normative → non-FHIR ct={ct!r}"
        body = r.json()
        assert body.get("resourceType") in {
            "CapabilityStatement", "TerminologyCapabilities"
        }, f"mode=normative → unexpected resourceType={body.get('resourceType')!r}"

    def test_s48_mode_full_xml_format(self, fhir_client):
        """mode=full with _format=xml MUST return CapabilityStatement as XML."""
        r = fhir_client.get("/fhir/metadata?mode=full&_format=xml")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/fhir+xml" in ct
        body = r.text.lstrip()
        assert body.startswith("<"), f"mode=full&_format=xml but body not XML"
        # XML body must contain CapabilityStatement root element.
        assert "CapabilityStatement" in body[:200]

    def test_s49_mode_value_case_sensitive(self, fhir_client):
        """Hostile: mode=FULL (uppercase) — per FHIR general practice,
        enum values are case-sensitive. Server MUST NOT silently accept
        an uppercase variant and return a CapabilityStatement (that would
        be a silent-wrong-answer on case-fold). Either 400 (strict) or
        treat as full (lenient) is acceptable; the probe catches a future
        regression where uppercase silently partial-matches."""
        r = fhir_client.get("/fhir/metadata?mode=FULL")
        # Acceptable outcomes:
        # (a) 400 OperationOutcome (strict: FULL is not in {None, full, normative, terminology})
        # (b) 200 CapabilityStatement (lenient case-fold to 'full')
        # NOT acceptable: 500, or 200 TerminologyCapabilities (silent wrong answer).
        assert r.status_code != 500, f"mode=FULL → 500. Body={r.text[:200]!r}"
        if r.status_code == 200:
            body = r.json()
            assert body.get("resourceType") == "CapabilityStatement", (
                f"mode=FULL → resourceType={body.get('resourceType')!r} "
                f"(silent wrong answer on case)"
            )


# =============================================================================
# Item 5: capabilities?mode=terminology returns TerminologyCapabilities
# =============================================================================

class TestItem5ModeTerminology:
    """§4.7.2.1 item 5: mode=terminology MUST return a TerminologyCapabilities
    with: url, name, title, status, date, kind=instance, and a codeSystem block
    with sub-elements uri, version (for code systems with a version),
    version.code (for each version), content for each supported code system."""

    def test_s50_mode_terminology_has_all_required_top_elements(self, fhir_client):
        """mode=terminology response MUST include url, name, title, status,
        date, kind per §4.7.2.1 item 5."""
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        assert r.status_code == 200
        body = r.json()
        assert body.get("resourceType") == "TerminologyCapabilities", (
            f"mode=terminology → resourceType={body.get('resourceType')!r}"
        )
        required = ["url", "name", "title", "status", "date", "kind"]
        missing = [k for k in required if k not in body]
        assert not missing, (
            f"TerminologyCapabilities missing required top-level: {missing}"
        )
        assert body["kind"] == "instance"

    def test_s51_mode_terminology_has_codesystem_block(self, fhir_client):
        """mode=terminology MUST include a codeSystem block (non-empty list)."""
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        body = r.json()
        cs = body.get("codeSystem")
        assert cs is not None, (
            "TerminologyCapabilities missing codeSystem block "
            "(spec mandates it for each supported code system)"
        )
        assert isinstance(cs, list), f"codeSystem is not a list: {type(cs)}"
        assert len(cs) > 0, "codeSystem list is empty (no systems advertised)"

    def test_s52_codesystem_entries_have_uri_and_content(self, fhir_client):
        """Each codeSystem entry MUST have at least uri and content sub-elements
        per §4.7.2.1 item 5 (content is required; version is conditional)."""
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        body = r.json()
        for i, entry in enumerate(body.get("codeSystem", [])):
            assert "uri" in entry, (
                f"codeSystem[{i}] missing 'uri' sub-element: {entry!r}"
            )
            assert "content" in entry, (
                f"codeSystem[{i}] missing 'content' sub-element: {entry!r}"
            )
            # content must be a valid CodeSystemContentMode value.
            assert entry["content"] in {
                "not-present", "example", "fragment", "complete", "supplement"
            }, (
                f"codeSystem[{i}].content={entry['content']!r} is not a valid "
                f"CodeSystemContentMode"
            )

    def test_s53_codesystem_uris_match_canonical_registry(self, fhir_client):
        """Each codeSystem.uri MUST be one of the canonical URIs in
        SYSTEM_TO_FHIR_URI (single source of truth). Hostile: catches drift
        where a URI is hardcoded wrong (HCPCS QA-012 regression class)."""
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI
        canonical_uris = set(SYSTEM_TO_FHIR_URI.values())
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        body = r.json()
        advertised_uris = {entry["uri"] for entry in body.get("codeSystem", [])}
        # Every advertised URI must be in the canonical registry.
        extra = advertised_uris - canonical_uris
        assert not extra, (
            f"TerminologyCapabilities.codeSystem advertised URIs not in "
            f"SYSTEM_TO_FHIR_URI canonical registry: {extra}"
        )
        # Every canonical URI must be advertised (no silent drops).
        missing = canonical_uris - advertised_uris
        assert not missing, (
            f"TerminologyCapabilities.codeSystem missing canonical URIs from "
            f"SYSTEM_TO_FHIR_URI: {missing}"
        )

    def test_s54_mode_terminology_xml_format(self, fhir_client):
        """mode=terminology with _format=xml MUST return TerminologyCapabilities
        as XML (item 1 applies uniformly)."""
        r = fhir_client.get("/fhir/metadata?mode=terminology&_format=xml")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/fhir+xml" in ct
        body = r.text.lstrip()
        assert body.startswith("<")
        assert "TerminologyCapabilities" in body[:300]

    def test_s55_mode_terminology_not_capabilitystatement(self, fhir_client):
        """Hostile catch: mode=terminology MUST NOT return a CapabilityStatement
        (catches a future bug where the dispatcher falls through to the
        default branch)."""
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        body = r.json()
        assert body.get("resourceType") != "CapabilityStatement", (
            "mode=terminology returned a CapabilityStatement (silent wrong answer)"
        )

    def test_s56_mode_terminology_status_valid(self, fhir_client):
        """TerminologyCapabilities.status MUST be a valid PublicationStatus."""
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        body = r.json()
        status = body.get("status")
        assert status in {"active", "draft", "retired", "unknown"}, (
            f"TerminologyCapabilities.status={status!r}"
        )

    def test_s57_mode_terminology_kind_instance(self, fhir_client):
        """TerminologyCapabilities.kind MUST be 'instance' (spec fixed value)."""
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        body = r.json()
        assert body.get("kind") == "instance", (
            f"TerminologyCapabilities.kind={body.get('kind')!r} "
            f"(spec mandates fixed value 'instance')"
        )
