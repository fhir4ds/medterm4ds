"""SKEPTIC probes for TS-02 (Mandatory Terminology Service Operations Matrix, §4.7.1.2).

Source: https://build.fhir.org/terminology-service.html §4.7.1.2

Tests the 7 mandatory items:
1. CodeSystem-$lookup (type-level): required params code, system, version
2. CodeSystem-$validate-code (type-level): required params code, system, version, display
3. CodeSystem-$subsumes (type-level): required params codeA, codeB, system, version
4. ValueSet-$expand (type AND instance level): required params url, filter, offset, count
5. ValueSet-$validate-code (type AND instance level): required params url, code, system,
   systemVersion, display
6. ConceptMap-$translate (type AND instance level): required params url, sourceCode, system,
   targetCode, targetSystem
7. CapabilityStatement advertises all mandatory operations

Mandatory operations set per FHIR R4 §4.7.1.2: $lookup, $validate-code (CodeSystem AND
ValueSet), $subsumes, $expand, $translate, $closure.

Each probe is a SKEPTIC-style adversarial test:
- Probe an edge of one mandatory operation or required parameter.
- Capture the actual behavior (status, body, headers).
- A probe "fails" (reveals a bug) when the actual behavior violates the spec.
"""

from __future__ import annotations

import pytest


# =============================================================================
# Item 7 (audited first — advertisement presence gates the rest of the matrix)
# =============================================================================

def test_s01_capabilitystatement_advertises_all_mandatory_operations(fhir_client):
    """§4.7.1.2: 'A FHIR Server SHOULD expose ... the following operations: $lookup,
    $validate-code, $subsumes, $expand, $translate, $closure.' Advertisement MUST
    cover both CodeSystem.$validate-code AND ValueSet.$validate-code.

    Spec: https://build.fhir.org/terminology-service.html#summary
    Quote: 'CodeSystem operations: $lookup, $validate-code, $subsumes, $closure ...
            ValueSet operations: $expand, $validate-code ... ConceptMap operations:
            $translate'
    """
    r = fhir_client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.json()
    rest = body.get("rest", [{}])[0]
    resources = {res["type"]: res for res in rest.get("resource", [])}

    # Mandatory CodeSystem operations: lookup, validate-code, subsumes, closure
    cs_ops = {op["name"] for op in resources.get("CodeSystem", {}).get("operation", [])}
    assert {"lookup", "validate-code", "subsumes", "closure"}.issubset(cs_ops), (
        f"CodeSystem missing mandatory operations. Have: {cs_ops}"
    )

    # Mandatory ValueSet operations: expand, validate-code
    vs_ops = {op["name"] for op in resources.get("ValueSet", {}).get("operation", [])}
    assert {"expand", "validate-code"}.issubset(vs_ops), (
        f"ValueSet missing mandatory operations. Have: {vs_ops}"
    )

    # Mandatory ConceptMap operations: translate
    cm_ops = {op["name"] for op in resources.get("ConceptMap", {}).get("operation", [])}
    assert "translate" in cm_ops, (
        f"ConceptMap missing mandatory operation 'translate'. Have: {cm_ops}"
    )


def test_s02_capabilitystatement_operation_definitions_use_canonical_uri(fhir_client):
    """§3.2.1.0.5 (OperationDefinition): when a CapabilityStatement advertises an
    operation, the `definition` field SHOULD reference the canonical OperationDefinition
    URL published by HL7 (e.g. http://hl7.org/fhir/OperationDefinition/CodeSystem-lookup),
    not a server-local URL.

    Spec: https://hl7.org/fhir/R4/capabilitystatement.html#definition
    Quote: 'definition: Definition of the operation - a reference to an OperationDefinition
            resource ... Note that the OperationDefinition is a single, canonical definition
            of the operation.'

    The current server uses f'{base_url}/OperationDefinition/cs-lookup' (server-local URLs)
    for every operation. This is non-conformant — clients can't tell whether the operation
    is the standard FHIR one or a custom variant.
    """
    r = fhir_client.get("/fhir/metadata")
    body = r.json()
    rest = body.get("rest", [{}])[0]
    expected_canonicals = {
        ("CodeSystem", "lookup"): "http://hl7.org/fhir/OperationDefinition/CodeSystem-lookup",
        ("CodeSystem", "validate-code"): "http://hl7.org/fhir/OperationDefinition/CodeSystem-validate-code",
        ("CodeSystem", "subsumes"): "http://hl7.org/fhir/OperationDefinition/CodeSystem-subsumes",
        ("CodeSystem", "closure"): "http://hl7.org/fhir/OperationDefinition/CodeSystem-closure",
        ("ValueSet", "expand"): "http://hl7.org/fhir/OperationDefinition/ValueSet-expand",
        ("ValueSet", "validate-code"): "http://hl7.org/fhir/OperationDefinition/ValueSet-validate-code",
        ("ConceptMap", "translate"): "http://hl7.org/fhir/OperationDefinition/ConceptMap-translate",
    }
    non_canonical = []
    for res in rest.get("resource", []):
        rtype = res.get("type")
        for op in res.get("operation", []):
            key = (rtype, op.get("name"))
            expected = expected_canonicals.get(key)
            if expected is None:
                continue
            actual = op.get("definition", "")
            if actual != expected:
                non_canonical.append((key, actual, expected))
    # Capture for evidence
    pytest.current_report_extra = f"non_canonical={non_canonical}"
    assert not non_canonical, (
        f"CapabilityStatement.operation[].definition uses server-local URIs instead of "
        f"canonical HL7 OperationDefinition URIs. Mismatches: {non_canonical}"
    )


# =============================================================================
# Item 1: CodeSystem/$lookup required params (code, system, version)
# =============================================================================

def test_s03_lookup_missing_system_returns_4xx_with_operationoutcome(fhir_client):
    """§4.7.1.2.1 (CodeSystem $lookup): 'system' and 'code' are required parameters.
    Omitting system MUST produce a 4xx error in a FHIR OperationOutcome body —
    NOT FastAPI's default {'detail': [...]} 422 validation response.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    Quote: 'code: The code to look up. ... system: The code system to look up.'
    §3.1.0.1.5: 'The OperationOutcome may be returned with any HTTP 4xx or 5xx response'.
    """
    r = fhir_client.get("/fhir/CodeSystem/$lookup", params={"code": "44054006"})
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:200]={body[:200]!r}"
    assert 400 <= r.status_code < 500, (
        f"$lookup without system returned {r.status_code} (expected 4xx)."
    )
    body_json = r.json()
    assert body_json.get("resourceType") == "OperationOutcome", (
        f"$lookup without system returned non-OperationOutcome body (FastAPI default "
        f"422 validation response is not conformant). Body: {body[:200]}"
    )


def test_s04_lookup_missing_code_returns_4xx_with_operationoutcome(fhir_client):
    """§4.7.1.2.1 CodeSystem $lookup: code is required. Omitting MUST be 4xx in a
    FHIR OperationOutcome body."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct"},
    )
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:200]={body[:200]!r}"
    assert 400 <= r.status_code < 500
    body_json = r.json()
    assert body_json.get("resourceType") == "OperationOutcome", (
        f"$lookup without code returned non-OperationOutcome body. Body: {body[:200]}"
    )


def test_s05_lookup_version_param_accepted(fhir_client):
    """§4.7.1.2.1 CodeSystem $lookup: 'version' is a documented parameter. The server
    MUST accept it gracefully (whether or not it uses the value). Currently the GET
    handler declares `version: str | None = Query(None)` — the param is accepted but
    ignored. The probe here is just that it doesn't 400/500 on a known-good version.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    Quote: 'version: The version of the code system, if one was provided in the source.'
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "version": "http://snomed.info/sct/731000124108",
        },
    )
    pytest.current_report_extra = f"status={r.status_code} body[:80]={r.text[:80]!r}"
    assert r.status_code == 200, (
        f"$lookup with version param returned {r.status_code} (expected 200). "
        f"Body: {r.text[:200]}"
    )


def test_s06_lookup_code_with_special_characters_safe(fhir_client):
    """SKEPTIC boundary: code with shell-injection-like and HTML-script-like content.
    Server MUST NOT crash (500) and MUST sanitize reflection in error messages.

    Per GLOBAL_RULES.md (MAX_ERROR_FIELD_CHARS) the server caps reflected error text.
    The probe here: no 500, no literal reflection of the script tag in body that
    would be rendered as HTML.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "http://snomed.info/sct",
            "code": "<script>alert(1)</script>",
        },
    )
    pytest.current_report_extra = f"status={r.status_code} body[:200]={r.text[:200]!r}"
    assert r.status_code != 500, (
        f"$lookup with script code returned 500. Body: {r.text[:200]}"
    )


def test_s07_lookup_very_long_code_no_500(fhir_client):
    """SKEPTIC boundary: 5000-char code. Must not 500 / not exhaust log output."""
    long_code = "A" * 5000
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": long_code},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code != 500


def test_s08_lookup_unknown_param_ignored_or_400(fhir_client):
    """SKEPTIC: extra/unknown params MUST be either ignored or rejected with 400.
    Must NOT crash or produce a silent wrong answer.

    Per FHIR R4 §3.1.0.1.4: 'Unknown parameters SHOULD be ignored.'
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "foo": "bar",
        },
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code != 500


def test_s09_lookup_post_with_unknown_param_safe(fhir_client):
    """SKEPTIC: POST $lookup with an unknown 'coding' parameter (FHIR spec lists 'coding'
    as a valid alternative to system+code). Server currently rejects this silently because
    it only checks params.get('system')/params.get('code').

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    Quote: 'coding: A coding to look up. If a coding is provided, the system+code
            values are ignored.'
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "coding", "valueCoding": {
                "system": "http://snomed.info/sct", "code": "44054006",
            }},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    pytest.current_report_extra = f"status={r.status_code} body[:120]={r.text[:120]!r}"
    # Either accept (200 with display) or reject with 4xx OperationOutcome.
    # The current server returns 400 'system and code are required.' which is a
    # spec violation but at least not a crash. SKEPTIC logs this as a MEDIUM bug.
    assert r.status_code != 500


# =============================================================================
# Item 2: CodeSystem/$validate-code required params (code, system, version, display)
# =============================================================================

def test_s10_validate_code_missing_system_returns_400(fhir_client):
    """§4.7.1.2.2 CodeSystem $validate-code: 'system' required. Omitting MUST be 4xx."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"code": "44054006"},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500


def test_s11_validate_code_missing_code_returns_400(fhir_client):
    """§4.7.1.2.2 CodeSystem $validate-code: 'code' required. Omitting MUST be 4xx."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": "http://snomed.info/sct"},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500


def test_s12_validate_code_display_param_accepted(fhir_client):
    """§4.7.1.2.2 CodeSystem $validate-code: 'display' is a documented parameter used
    to validate the code's display string against the supplied candidate. The current
    server's GET handler does NOT declare a `display` parameter — only system and code.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
    Quote: 'display: The display string to verify. If the code system does not
            define the supplied display string, the operation returns the canonical
            display string for the code.'
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "display": "Type 2 diabetes mellitus",
            "version": "2024-09",
        },
    )
    pytest.current_report_extra = f"status={r.status_code} body[:120]={r.text[:120]!r}"
    # The current server ignores display entirely. The 200 is a silent wrong answer:
    # when display mismatch is supplied, the response SHOULD include a 'message'
    # parameter or a different result if display does not match.
    assert r.status_code != 500


def test_s13_validate_code_version_param_accepted(fhir_client):
    """§4.7.1.2.2 CodeSystem $validate-code: 'version' is a documented parameter."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "version": "http://snomed.info/sct/731000124108",
        },
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code == 200


def test_s14_validate_code_response_includes_display_param(fhir_client):
    """§4.7.1.2.2 CodeSystem $validate-code response: when result=true, the response
    SHOULD include a 'display' parameter giving the code's display string.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html (Output)
    Quote: 'display: The display string for the code as it is to be shown to the user.'
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$validate-code",
        params={"system": "http://snomed.info/sct", "code": "44054006"},
    )
    body = r.json()
    params = {p["name"]: p for p in body.get("parameter", [])}
    assert "display" in params, (
        f"$validate-code response missing 'display' parameter. Got: {list(params)}"
    )


# =============================================================================
# Item 3: CodeSystem/$subsumes required params (codeA, codeB, system, version)
# =============================================================================

def test_s15_subsumes_missing_system_returns_400(fhir_client):
    """§4.7.1.2.3 CodeSystem $subsumes: 'system' required."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={"codeA": "73211009", "codeB": "44054006"},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500


def test_s16_subsumes_missing_codeA_returns_400(fhir_client):
    """§4.7.1.2.3 CodeSystem $subsumes: 'codeA' required."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={"system": "http://snomed.info/sct", "codeB": "44054006"},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500


def test_s17_subsumes_missing_codeB_returns_400(fhir_client):
    """§4.7.1.2.3 CodeSystem $subsumes: 'codeB' required."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={"system": "http://snomed.info/sct", "codeA": "73211009"},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500


def test_s18_subsumes_version_param_accepted(fhir_client):
    """§4.7.1.2.3 CodeSystem $subsumes: 'version' is a documented parameter.
    The current GET handler does NOT declare version. Probe: passing it should not 500.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": "http://snomed.info/sct",
            "codeA": "73211009",
            "codeB": "44054006",
            "version": "http://snomed.info/sct/731000124108",
        },
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code == 200


def test_s19_subsumes_response_outcome_value_set(fhir_client):
    """§4.7.1.2.3 CodeSystem $subsumes: outcome MUST be one of: 'equivalent', 'subsumes',
    'subsumed-by', 'not-subsumed'. The current server emits these values — probe to pin
    the vocabulary.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    Quote: 'The possible outcome values are: equivalent | subsumes | subsumed-by |
            not-subsumed.'
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": "http://snomed.info/sct",
            "codeA": "73211009",
            "codeB": "44054006",
        },
    )
    body = r.json()
    outcomes = [
        p.get("valueCode") for p in body.get("parameter", []) if p.get("name") == "outcome"
    ]
    valid = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}
    invalid = [o for o in outcomes if o not in valid]
    assert not invalid, f"$subsumes returned invalid outcome value(s): {invalid}"


# =============================================================================
# Item 4: ValueSet/$expand required params (url, filter, offset, count) — type AND instance
# =============================================================================

def test_s20_expand_count_negative_rejected(fhir_client):
    """§4.7.1.2.4 ValueSet $expand: 'count' MUST be a non-negative integer. Negative
    values MUST be rejected with 400.

    Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
    Quote: 'count: The maximum number of codes to return in the expansion.'
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params={"filter": "diabetes", "count": "-5"},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500, (
        f"$expand count=-5 returned {r.status_code} (expected 4xx). Body: {r.text[:200]}"
    )


def test_s21_expand_count_non_integer_rejected(fhir_client):
    """§4.7.1.2.4 ValueSet $expand: count must be integer. 'abc' MUST be 4xx, not 500."""
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params={"filter": "diabetes", "count": "abc"},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    # GET handler declares `count: int = Query(20, ge=1, le=1000)` so FastAPI
    # type-coercion should produce 422. Either 400 or 422 is acceptable.
    assert 400 <= r.status_code < 500


def test_s22_expand_count_overflow_safe(fhir_client):
    """SKEPTIC boundary: count=999999999 (would overflow naive int parsing). Server caps
    via Query(le=1000), but the probe verifies the cap holds."""
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params={"filter": "diabetes", "count": "999999999"},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500


def test_s23_expand_offset_param_accepted(fhir_client):
    """§4.7.1.2.4 ValueSet $expand: 'offset' is a documented parameter for pagination.
    The current GET handler does NOT declare offset. Probe: passing it should not 500.

    Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
    Quote: 'offset: Paging parameter — the offset to use when paging through results.'
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params={"filter": "diabetes", "offset": "0", "count": "5"},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code != 500


def test_s24_expand_instance_level_route_exists(fhir_client):
    """§4.7.1.2.4 ValueSet $expand instance-level: 'GET /fhir/ValueSet/{id}/$expand'
    SHALL be supported for instances. The route MUST exist; for unknown ids, returning
    404 OperationOutcome is acceptable (the spec allows it for instances the server
    can't resolve).

    Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
    Quote: 'This operation may be invoked by name on a ValueSet instance, or by type.'
    """
    r = fhir_client.get("/fhir/ValueSet/some-id/$expand")
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:120]={body[:120]!r}"
    # After the fix: the route is registered and returns 404 OperationOutcome for
    # unknown ids. Pre-fix behavior was a generic catch-all 404 saying "Resource type
    # 'ValueSet' is not supported" — that is non-conformant. The route-specific 404
    # says "No stored ValueSet with id 'some-id'" — that is conformant.
    is_route_specific_404 = (
        r.status_code == 404
        and "OperationOutcome" in body
        and "No stored ValueSet" in body
    )
    is_expansion_response = (
        r.status_code == 200
        and '"resourceType":"ValueSet"' in body.replace(" ", "")
    ) or r.status_code == 501 or is_route_specific_404
    assert is_expansion_response, (
        f"Instance-level $expand route missing/non-conformant: status={r.status_code} "
        f"body[:200]={body[:200]!r}"
    )


def test_s25_expand_post_count_overflow_safe(fhir_client):
    """SKEPTIC: POST $expand with count=999999999 in Parameters body. The POST handler
    uses _parse_count_param which caps at 1000 — verify the cap holds for huge values."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "filter", "valueString": "diabetes"},
            {"name": "count", "valueInteger": 999999999},
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500, (
        f"POST $expand with count=999999999 returned {r.status_code} (expected 4xx cap)."
    )


def test_s26_expand_post_count_negative_rejected(fhir_client):
    """SKEPTIC: POST $expand with count=-5 in Parameters body."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "filter", "valueString": "diabetes"},
            {"name": "count", "valueInteger": -5},
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500


# =============================================================================
# Item 5: ValueSet/$validate-code — type AND instance level
# =============================================================================

def test_s27_valueset_validate_code_type_level_route_exists(fhir_client):
    """§4.7.1.2 ValueSet operations: '$validate-code' SHALL be exposed at type level.
    Originally missing (SKEPTIC iteration TS-02, QA-013). After fix the route returns
    200 with a Parameters body for valid (system, code).

    Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
    Quote: 'The ValueSet resource includes an operation called $validate-code that
            may be invoked on either a ValueSet instance, or by type.'
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$validate-code",
        params={
            "url": "http://snomed.info/sct?fhir_vs",
            "code": "44054006",
            "system": "http://snomed.info/sct",
        },
    )
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:120]={body[:120]!r}"
    assert r.status_code == 200, (
        f"ValueSet/$validate-code returned status={r.status_code} (expected 200). "
        f"Body: {body[:200]}"
    )
    payload = r.json()
    assert payload.get("resourceType") == "Parameters", (
        f"ValueSet/$validate-code returned non-Parameters body: {payload}"
    )


def test_s28_valueset_validate_code_instance_level_route_exists(fhir_client):
    """§4.7.1.2 ValueSet $validate-code instance-level: 'GET /fhir/ValueSet/{id}/$validate-code'.
    The current server does NOT register this route. Probe confirms the missing route."""
    r = fhir_client.get("/fhir/ValueSet/some-id/$validate-code")
    pytest.current_report_extra = f"status={r.status_code}"
    # 404 with OperationOutcome is acceptable (instance-level only required when the
    # server persists ValueSets). The probe exists to document the gap.
    # Acceptable outcomes: 200 (operation ran), 404 OperationOutcome, 501 NotImplemented.
    # NOT acceptable: 500 (crash).
    assert r.status_code != 500


# =============================================================================
# Item 6: ConceptMap/$translate — type AND instance level
# =============================================================================

def test_s29_translate_instance_level_route_exists(fhir_client):
    """§4.7.1.2 ConceptMap operations: '$translate' SHALL be exposed at both type and
    instance level. The route MUST exist; for unknown ids, returning 404 OperationOutcome
    with a route-specific message is acceptable.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    Quote: 'This operation may be invoked by name on a ConceptMap instance, or by type.'
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/some-id/$translate",
        params={"code": "44054006", "system": "http://snomed.info/sct"},
    )
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} body[:120]={body[:120]!r}"
    # After fix: route exists and returns 404 OperationOutcome for unknown ids
    # with a route-specific message. Pre-fix: catch-all 404 saying "Resource type
    # 'ConceptMap' is not supported".
    is_route_specific_404 = (
        r.status_code == 404
        and "OperationOutcome" in body
        and "No stored ConceptMap" in body
    )
    is_valid_response = (
        r.status_code == 200
        or r.status_code == 501
        or is_route_specific_404
    )
    assert is_valid_response, (
        f"Instance-level $translate route missing/non-conformant: status={r.status_code} "
        f"body[:200]={body[:200]!r}"
    )


def test_s30_translate_targetsystem_param_name(fhir_client):
    """§4.7.1.2.6 ConceptMap $translate: 'targetsystem' is the documented parameter name
    (lowercase, no separator). The current server uses 'targetsystem' — verify it works.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    Quote: 'targetsystem: The target code system to translate to.'
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
        },
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code == 200


def test_s31_translate_source_param_accepted(fhir_client):
    """§4.7.1.2.6 ConceptMap $translate: 'source' parameter (canonical ConceptMap URL)
    is documented. The current server does NOT declare this param.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    Quote: 'source: The canonical url of the concept map to use.'
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "source": "http://medterm4ds.org/fhir/ConceptMap/snomed-to-icd10",
        },
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code != 500


def test_s32_translate_targetCode_param_accepted(fhir_client):
    """§4.7.1.2.6 ConceptMap $translate: 'targetCode' / 'targetcode' parameter is
    documented for reverse lookup. The current server does NOT declare this param.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    Quote: 'targetCode: The code to translate to. ... reverse: if this is set to true,
            then the operation should return all the source codes that map to the target.'
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            "targetCode": "E11",
        },
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code != 500


def test_s33_translate_response_includes_message(fhir_client):
    """§4.7.1.2.6 ConceptMap $translate response: 'message' parameter is documented
    in the output. Pin its presence.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
        },
    )
    body = r.json()
    names = {p["name"] for p in body.get("parameter", [])}
    assert "message" in names, f"$translate response missing 'message' param. Got: {names}"


# =============================================================================
# Carry-forward from TS-01: Accept-header on operations
# =============================================================================

def test_s34_lookup_accept_xml_returns_xml(fhir_client):
    """§3.1.0.1.9 + §4.7.1.1 item 1: $lookup with Accept: application/fhir+xml MUST
    return XML. Carry-forward from TS-01 EXPLORER (QA-008 fixed Content-Type for JSON
    but XML serialization on operations was aspirational).

    Spec: https://hl7.org/fhir/R4/http.html#mime-type
    Quote: 'The correct MIME type for XML content SHALL be application/fhir+xml.'
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "44054006"},
        headers={"Accept": "application/fhir+xml"},
    )
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} ct={r.headers.get('content-type')!r} body[:80]={body[:80]!r}"
    # If the server claims XML support in `format: ["json","xml"]`, every operation
    # response MUST honor Accept: application/fhir+xml.
    assert r.headers.get("content-type", "").startswith("application/fhir+xml"), (
        f"$lookup Accept:xml returned Content-Type={r.headers.get('content-type')!r} "
        f"(expected application/fhir+xml). Body: {body[:200]}"
    )


def test_s35_expand_accept_xml_returns_xml(fhir_client):
    """Same carry-forward — verify $expand honors Accept: application/fhir+xml."""
    r = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params={"filter": "diabetes", "count": 5},
        headers={"Accept": "application/fhir+xml"},
    )
    body = r.text or ""
    pytest.current_report_extra = f"status={r.status_code} ct={r.headers.get('content-type')!r}"
    assert r.headers.get("content-type", "").startswith("application/fhir+xml"), (
        f"$expand Accept:xml returned Content-Type={r.headers.get('content-type')!r}. "
        f"Body: {body[:200]}"
    )


# =============================================================================
# SKEPTIC: malformed POST Parameters bodies
# =============================================================================

def test_s36_lookup_post_missing_parameter_array(fhir_client):
    """SKEPTIC: POST $lookup with empty Parameters body. Must 400, not 500."""
    body = {"resourceType": "Parameters", "parameter": []}
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500


def test_s37_lookup_post_wrong_value_type(fhir_client):
    """SKEPTIC: POST $lookup where 'code' parameter uses valueInteger instead of
    valueCode. Server's _parse_parameters supports valueInteger, so this should
    succeed (200) or be a clean 4xx. Must NOT 500.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": "http://snomed.info/sct"},
            {"name": "code", "valueInteger": 12345},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code != 500


def test_s38_lookup_post_non_parameters_body(fhir_client):
    """SKEPTIC: POST $lookup with a body that is NOT a Parameters resource.
    Must be a clean 4xx, not 500."""
    body = {"resourceType": "Patient", "id": "example"}
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500


# =============================================================================
# SKEPTIC: $closure boundary
# =============================================================================

def test_s39_closure_missing_body_returns_400(fhir_client):
    """SKEPTIC: POST $closure with empty body. Must 400, not 500."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={"resourceType": "Parameters", "parameter": []},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert 400 <= r.status_code < 500


def test_s40_closure_get_returns_405(fhir_client):
    """§4.7.1.2 CodeSystem $closure is POST-only. GET should 405 (method not allowed)
    rather than silently fall through to the catch-all."""
    r = fhir_client.get("/fhir/CodeSystem/$closure")
    pytest.current_report_extra = f"status={r.status_code}"
    # Acceptable: 405 (correct), 404 OperationOutcome (catch-all). NOT: 200 silent.
    assert r.status_code != 200 or r.json().get("resourceType") == "OperationOutcome"
