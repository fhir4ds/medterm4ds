"""EXPLORER iteration TS-02 — lateral probes for spec-compliance corners.

Source: https://build.fhir.org/terminology-service.html#summary §4.7.1.2

EXPLORER lens (per assignment):
1. Instance-level POST routes (ARCH-003 carry-forward from SKEPTIC).
2. Multi-valued parameters (repeating `property` in $lookup).
3. Mixed input encodings (multiple alternatives supplied at once).
4. Pagination corners (large/zero/negative count+offset).
5. Property parameter edge cases.
6. Filter edge cases (very long, regex chars, empty).
7. $subsumes both directions (equivalent vs not-subsumed).
8. $translate reverse mode.
9. CapabilityStatement operation reachability.
10. Cross-resource pollution (GET /fhir/ValueSet/$lookup etc.).
11. Uncaught exceptions (CPU-waste / DoS surfaces).
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Pattern: instance-level POST routes — ARCH-003 carry-forward.
#
# SKEPTIC QA-014 added instance-level GET for ValueSet/$expand. QA-015 added
# instance-level GET and POST for ConceptMap/$translate. The instance-level
# POST for ValueSet/$expand and ValueSet/$validate-code was NOT added —
# SKEPTIC's engineer handoff flagged it as deferred ("The instance-level
# $expand and $validate-code POST routes are not added; the spec lists
# instance-level invocation as GET-only for these ops").
#
# FHIR R4 §3.1.0.1.1 (https://hl7.org/fhir/R4/http.html#ops): "Operations
# MAY be invoked using either GET or POST." This is permissive: GET OR POST
# SHALL be supported by the server for operation invocation. A server that
# only supports GET on an instance-level route returns 405 on POST — and
# Starlette's default 405 body is {"detail":"Method Not Allowed"} with
# Content-Type: application/json, NOT a FHIR OperationOutcome. So the gap
# has two layers: missing POST route + non-FHIR 405 response shape.
# ---------------------------------------------------------------------------


def test_e01_valueset_expand_instance_post_returns_fhir_response(fhir_client):
    """EXPLORER: POST /fhir/ValueSet/{id}/$expand MUST not return a 405
    with Starlette's default body. Either accept the request, return a
    route-specific 404 OperationOutcome (instance-level pattern from
    QA-014), or return some FHIR-shaped response.

    Spec: https://hl7.org/fhir/R4/http.html#ops — "Operations MAY be
    invoked using either GET or POST."
    """
    r = fhir_client.post(
        "/fhir/ValueSet/some-id/$expand",
        json={"resourceType": "Parameters", "parameter": []},
    )
    # Reject Starlette default 405 body outright.
    assert not (r.status_code == 405 and r.headers.get("content-type") == "application/json"), (
        f"Instance-level POST $expand returns Starlette default 405 (non-FHIR). "
        f"Status={r.status_code}, body={r.text!r}"
    )
    # Whatever the response is, it must be FHIR-shaped.
    assert r.headers.get("content-type", "").startswith("application/fhir+json"), (
        f"Expected FHIR JSON response; got Content-Type={r.headers.get('content-type')!r}"
    )


def test_e02_valueset_validate_code_instance_post_returns_fhir_response(fhir_client):
    """EXPLORER: same as test_e01 for ValueSet/$validate-code instance POST."""
    r = fhir_client.post(
        "/fhir/ValueSet/some-id/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "73211009"},
            ],
        },
    )
    assert not (r.status_code == 405 and r.headers.get("content-type") == "application/json"), (
        f"Instance-level POST $validate-code returns Starlette default 405 (non-FHIR). "
        f"Status={r.status_code}, body={r.text!r}"
    )
    assert r.headers.get("content-type", "").startswith("application/fhir+json"), (
        f"Expected FHIR JSON response; got Content-Type={r.headers.get('content-type')!r}"
    )


def test_e03_conceptmap_translate_instance_post_is_fhir(fhir_client):
    """EXPLORER sanity check: ConceptMap/{id}/$translate POST WAS wired by
    SKEPTIC QA-015. Verify it stays FHIR-shaped (regression guard)."""
    r = fhir_client.post(
        "/fhir/ConceptMap/some-id/$translate",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert r.status_code != 405, "ConceptMap instance POST should be registered"
    assert r.headers.get("content-type", "").startswith("application/fhir+json"), (
        f"Expected FHIR JSON; got {r.headers.get('content-type')!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


# ---------------------------------------------------------------------------
# Pattern: codeableConcept on $validate-code — same shape as QA-022/QA-023.
#
# HISTORIAN wired `coding` (valueCoding) as an alternative to system+code on
# $lookup and $validate-code. The spec also lists `codeableConcept`
# (valueCodeableConcept) as a third alternative. A CodeableConcept wraps a
# list of Coding; the server picks the first coding with both system and
# code. HISTORIAN's engineer handoff explicitly noted this as deferred for
# EXPLORER.
# ---------------------------------------------------------------------------


def test_e04_validate_code_post_with_codeable_concept_accepted(fhir_client):
    """EXPLORER: POST $validate-code with codeableConcept parameter MUST be
    accepted as the spec-allowed alternative to system+code / coding.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html —
    "When invoking this operation, a client SHALL provide one (and only one)
    of the parameters (code+system, coding, or codeableConcept)."
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": "http://snomed.info/sct", "code": "73211009"},
                        ],
                    },
                },
            ],
        },
    )
    # Must NOT be 400 "system and code required." — the codeableConcept
    # supplies both. Allow 200 (validated) or a route-specific error (e.g.
    # 400 if display mismatch), but not the silent-reject message.
    assert not (
        r.status_code == 400
        and "system and code are required" in r.json().get("issue", [{}])[0].get("diagnostics", "")
    ), (
        f"codeableConcept parameter silently rejected. Status={r.status_code}, body={r.text!r}"
    )


# ---------------------------------------------------------------------------
# Pattern: uncaught ValueError on $expand with very long filter.
#
# services/discovery.py:search_names raises ValueError for queries >256
# chars (CPU-waste guard). _do_expand calls search_names without catching
# the exception → propagates as HTTP 500 with a non-FHIR body.
# ---------------------------------------------------------------------------


def test_e05_expand_long_filter_returns_fhir_error_not_500(fhir_client):
    """EXPLORER: GET $expand?filter=<5K chars> MUST return a FHIR
    OperationOutcome (e.g. 400), not an uncaught 500.

    Spec: https://hl7.org/fhir/R4/http.html — "The OperationOutcome may be
    returned with any HTTP 4xx or 5xx response." An uncaught ValueError
    surfaces as a generic 500 with a non-FHIR body in production.
    """
    r = fhir_client.get("/fhir/ValueSet/$expand?filter=" + "x" * 5000)
    # The 500 status itself may be acceptable (server-side limit), but the
    # response MUST be a FHIR OperationOutcome, not Starlette's default.
    assert r.headers.get("content-type", "").startswith("application/fhir+json"), (
        f"Long filter produced non-FHIR response. ct={r.headers.get('content-type')!r}, "
        f"status={r.status_code}, body={r.text[:200]!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"Long filter did not return OperationOutcome. body={body}"
    )


def test_e06_expand_filter_with_regex_chars_no_500(fhir_client):
    """EXPLORER: filter with regex chars must not 500. (Boundary probe.)"""
    r = fhir_client.get("/fhir/ValueSet/$expand?filter=" + "diab" + "%5B%5C%5D%3F")
    # Should be 200 with empty contains, or 400 with OperationOutcome — not 500.
    assert r.status_code != 500, f"Regex chars in filter caused 500: {r.text!r}"
    assert r.headers.get("content-type", "").startswith("application/fhir"), (
        f"Non-FHIR response: {r.headers.get('content-type')!r}"
    )


# ---------------------------------------------------------------------------
# Confirmed-conformant behaviors — regression guards.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/fhir/ValueSet/$lookup?system=http://snomed.info/sct&code=73211009",
        "/fhir/CodeSystem/$translate?system=http://snomed.info/sct&code=73211009",
        "/fhir/CodeSystem/$expand?filter=diab",
        "/fhir/ConceptMap/$lookup?system=http://snomed.info/sct&code=73211009",
    ],
)
def test_e07_cross_resource_pollution_returns_fhir_404(url, fhir_client):
    """EXPLORER: GET $op on the wrong resource type MUST return a FHIR
    OperationOutcome (not Starlette default 404). The catch-all routes
    added in TS-01 EXPLORER (QA-011) handle this; verify it stays working
    for the operation URLs."""
    r = fhir_client.get(url)
    assert r.headers.get("content-type", "").startswith("application/fhir"), (
        f"Non-FHIR response for cross-resource pollution: {r.headers.get('content-type')!r}"
    )
    # 404 is acceptable (operation not defined for this resource type).
    assert r.status_code in (404, 400, 422), f"Unexpected status {r.status_code} for {url}"


@pytest.mark.parametrize(
    "url",
    [
        "/fhir/ValueSet/$expand?filter=diab&count=-1",
        "/fhir/ValueSet/$expand?filter=diab&count=0",
        "/fhir/ValueSet/$expand?filter=diab&count=99999",
        "/fhir/ValueSet/$expand?filter=diab&offset=-1",
    ],
)
def test_e08_expand_invalid_count_offset_returns_4xx_fhir(url, fhir_client):
    """EXPLORER: negative / zero / over-cap count and negative offset MUST
    return a 4xx FHIR OperationOutcome (not 500 or empty)."""
    r = fhir_client.get(url)
    assert r.status_code in (400, 422), f"Status {r.status_code} for {url}"
    assert r.headers.get("content-type", "").startswith("application/fhir"), (
        f"Non-FHIR response for {url}: {r.headers.get('content-type')!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


@pytest.mark.parametrize("count_value", [-1, 0, 99999])
def test_e09_expand_post_invalid_count(count_value, fhir_client):
    """EXPLORER: POST $expand with count outside [1, 1000] MUST return 400
    OperationOutcome. Regression guard on _parse_count_param."""
    r = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diab"},
                {"name": "count", "valueInteger": count_value},
            ],
        },
    )
    assert r.status_code == 400, f"count={count_value} should give 400; got {r.status_code}"
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_e10_subsumes_both_directions(fhir_client):
    """EXPLORER: $subsumes outcome MUST be direction-sensitive. A=parent
    (73211009), B=child (44054006) per the conformance DB."""
    # A subsumes B
    r1 = fhir_client.get(
        "/fhir/CodeSystem/$subsumes?system=http://snomed.info/sct&codeA=73211009&codeB=44054006"
    )
    p1 = [p for p in r1.json()["parameter"] if p.get("name") == "outcome"][0]
    assert p1.get("valueCode") == "subsumes", f"A→B should be 'subsumes'; got {p1}"

    # B subsumed-by A
    r2 = fhir_client.get(
        "/fhir/CodeSystem/$subsumes?system=http://snomed.info/sct&codeA=44054006&codeB=73211009"
    )
    p2 = [p for p in r2.json()["parameter"] if p.get("name") == "outcome"][0]
    assert p2.get("valueCode") == "subsumed-by", f"B→A should be 'subsumed-by'; got {p2}"


def test_e11_subsumes_same_code_is_equivalent(fhir_client):
    """EXPLORER: $subsumes with identical codeA=codeB MUST return 'equivalent'
    per the spec outcome vocabulary."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes?system=http://snomed.info/sct&codeA=73211009&codeB=73211009"
    )
    p = [p for p in r.json()["parameter"] if p.get("name") == "outcome"][0]
    assert p.get("valueCode") == "equivalent", f"Same code should be 'equivalent'; got {p}"


def test_e12_lookup_property_repeat_param_accepted(fhir_client):
    """EXPLORER: $lookup with repeating `property` parameter (0..* per spec)
    MUST be accepted (the server returns its full property set anyway, but
    the param must not 4xx/5xx). Regression guard."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "73211009"},
                {"name": "property", "valueCode": "display"},
                {"name": "property", "valueCode": "name"},
            ],
        },
    )
    assert r.status_code == 200, f"property repeat should succeed; got {r.status_code}: {r.text!r}"


def test_e13_translate_reverse_param_accepted(fhir_client):
    """EXPLORER: $translate?reverse=true MUST be accepted (deferred-processing
    per spec, but the param is declared). Regression guard."""
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate?system=http://snomed.info/sct&code=73211009&reverse=true"
    )
    assert r.status_code != 500, f"reverse=true caused 500: {r.text!r}"
    assert r.headers.get("content-type", "").startswith("application/fhir")


def test_e14_advertised_operations_reachable(fhir_client):
    """EXPLORER: every operation advertised in CapabilityStatement.rest[].resource[].operation[]
    MUST be reachable at the advertised URL. Walk the capability statement
    and probe each operation's type-level GET route."""
    r = fhir_client.get("/fhir/metadata")
    rest = r.json()["rest"][0]["resource"]
    unreachable = []
    for entry in rest:
        rtype = entry["type"]
        for op in entry.get("operation", []):
            code = op.get("name", "").lstrip("$")
            if not code:
                continue
            url = f"/fhir/{rtype}/${code}"
            # Use a benign param set; most ops will return 400 (missing
            # required params) which still proves the route is wired.
            probe = fhir_client.get(url)
            # 405 means route registered for POST only (unusual);
            # 404 with non-FHIR body means route missing entirely.
            if probe.status_code == 404 and not probe.headers.get("content-type", "").startswith("application/fhir"):
                unreachable.append(f"{url} → {probe.status_code} ({probe.headers.get('content-type')})")
    assert not unreachable, f"Unreachable advertised operations: {unreachable}"


def test_e15_state_isolation_lookup_then_subsumes(fhir_client):
    """EXPLORER: chaining $lookup then $subsumes on the same code MUST not
    corrupt server state. Regression guard against shared mutable state."""
    # First call: $lookup
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup?system=http://snomed.info/sct&code=73211009"
    )
    assert r_lookup.status_code == 200

    # Second call: $subsumes
    r_sub = fhir_client.get(
        "/fhir/CodeSystem/$subsumes?system=http://snomed.info/sct&codeA=73211009&codeB=44054006"
    )
    assert r_sub.status_code == 200
    p = [p for p in r_sub.json()["parameter"] if p.get("name") == "outcome"][0]
    assert p.get("valueCode") == "subsumes"


def test_e16_valueset_validate_code_post_with_coding_accepted(fhir_client):
    """EXPLORER: ValueSet/$validate-code POST with `coding` parameter MUST
    be accepted as the spec-listed alternative. (Same shape as HISTORIAN's
    QA-022/QA-023 for CodeSystem, applied to ValueSet.) Regression guard
    confirming the valueCoding extraction is wired on the ValueSet handler
    too."""
    r = fhir_client.post(
        "/fhir/ValueSet/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": "http://snomed.info/sct",
                        "code": "73211009",
                    },
                },
            ],
        },
    )
    # Should NOT silently reject with "code and system are required for $validate-code."
    body = r.json()
    diag = body.get("issue", [{}])[0].get("diagnostics", "") if body.get("resourceType") == "OperationOutcome" else ""
    assert not (
        r.status_code == 400 and "code and system are required" in diag
    ), f"VS $validate-code POST with coding silently rejected: {r.text!r}"
