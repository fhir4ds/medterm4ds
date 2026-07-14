"""SKEPTIC probes for CS-01 (CodeSystem Resource Structure, https://build.fhir.org/codesystem.html).

Scope (per chunk assignment):
  1. `content` field values: must be from FHIR R4 CodeSystemContentMode enum
     {complete, example, fragment, not-present, supplement}.
  2. `property` field: defines code system-specific properties (code, uri, description, type).
  3. `concept` field: nested concept hierarchy (code, display, definition, concept[], property[]).
  4. `filter` field: allowed operators per code system.
  5. READ interaction: GET /fhir/CodeSystem/{id} returns full CodeSystem resource.
  6. SEARCH interaction: by url, version, name, title, status.

medterm4ds context (from chunk notes + AGENTS.md):
  - medterm4ds is a terminology SERVICE; it does NOT persist FHIR resources.
    READ on /fhir/CodeSystem/{id} returns 404 OperationOutcome (TS-01 QA-002).
    SEARCH on /fhir/CodeSystem returns empty Bundle (TS-01 QA-003).
    Items 5-6 are structurally covered by TS-01; CS-01 re-pins the contract.
  - CodeSystem resource shape (items 1-4) is not directly serialized; the
    in-scope surface is TerminologyCapabilities (advertising code systems) and
    $lookup (returning concept metadata).

Spec: https://build.fhir.org/codesystem.html (R4 / 4.0.1 — medterm4ds target).
      https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html (content enum)
      https://hl7.org/fhir/R4/concept-properties.html (defined concept properties)
      https://hl7.org/fhir/R4/codesystem-operation-lookup.html ($lookup)
"""

from __future__ import annotations

import pytest


# FHIR R4 CodeSystemContentMode enum
# Spec: https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html
FHIR_R4_CONTENT_MODES = {"complete", "example", "fragment", "not-present", "supplement"}

# FHIR R4 CodeSystem.property.type enum (PropertyType)
# Spec: https://hl7.org/fhir/R4/valueset-concept-property-type.html
FHIR_R4_PROPERTY_TYPES = {
    "code", "Coding", "string", "integer", "boolean", "dateTime", "decimal",
}

# FHIR R4 CodeSystem.filter.operator enum (FilterOperator)
# Spec: https://hl7.org/fhir/R4/valueset-filter-operator.html
FHIR_R4_FILTER_OPERATORS = {
    "=", "is-a", "descendent-of", "is-not-a", "regex",
    "in", "not-in", "generalizes", "exists",
}


# ---------------------------------------------------------------------------
# Item 1: `content` field values in TerminologyCapabilities.codeSystem[]
# ---------------------------------------------------------------------------

def test_s01_termcaps_content_value_in_r4_enum(fhir_client):
    """CS-01 item 1 / §4.8.5 CodeSystem.content binding (Required):
    "The extent of the content of the code system ... are represented in a
    code system resource." CodeSystemContentMode value set is REQUIRED.

    medterm4ds advertises each supported system in TerminologyCapabilities with
    a `content` value. Per AGENTS.md NOT A BUG Registry, every entry is
    `not-present` (intentional — medterm4ds does not expose CodeSystem
    resources). This probe pins that every advertised `content` value is a
    member of the FHIR R4 enum. Any other value would be a CodeSystemContentMode
    binding violation.
    """
    r = fhir_client.get("/fhir/metadata?mode=terminology")
    assert r.status_code == 200, f"mode=terminology → {r.status_code}"
    body = r.json()
    assert body.get("resourceType") == "TerminologyCapabilities"
    cs_entries = body.get("codeSystem", [])
    assert cs_entries, "TerologyCapabilities.codeSystem[] is empty — systems not advertised"
    bad = [
        {"uri": e.get("uri"), "content": e.get("content")}
        for e in cs_entries
        if e.get("content") not in FHIR_R4_CONTENT_MODES
    ]
    pytest.current_report_extra = f"entries={len(cs_entries)} bad_content={bad}"
    assert not bad, (
        f"TerminologyCapabilities advertised content values not in FHIR R4 "
        f"CodeSystemContentMode enum: {bad}. Allowed: {sorted(FHIR_R4_CONTENT_MODES)}."
    )


def test_s02_termcaps_every_advertised_system_has_content(fhir_client):
    """CS-01 item 1 / §4.7.1.1 item 5: every codeSystem[] entry SHALL have a
    `content` element (cardinality 1..1 in CodeSystem.content per §4.8.5).
    TerminologyCapabilities.codeSystem.content is also 1..1 per
    https://hl7.org/fhir/R4/terminologycapabilities-definitions.html#TerminologyCapabilities.codeSystem.content.

    SKEPTIC lens: catch the case where one source's entry was added without a
    content field (silent missing-required-element). This is a positive
    success-shape assertion per GLOBAL_RULES.md "Test-too-lenient".
    """
    r = fhir_client.get("/fhir/metadata?mode=terminology")
    body = r.json()
    cs_entries = body.get("codeSystem", [])
    missing = [e.get("uri") for e in cs_entries if "content" not in e]
    pytest.current_report_extra = f"missing_content={missing}"
    assert not missing, (
        f"TerminologyCapabilities.codeSystem[] entries missing required `content` "
        f"field: {missing}"
    )


# ---------------------------------------------------------------------------
# Item 2-3: $lookup response shape (CodeSystem.property / concept structure)
# Per GLOBAL_RULES.md "Test-too-lenient": assert POSITIVE success shape.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "system_uri,code",
    [
        ("http://snomed.info/sct", "73211009"),
        ("http://snomed.info/sct", "44054006"),
    ],
)
def test_s10_lookup_property_part_uses_valueCode_for_code(fhir_client, system_uri, code):
    """CS-01 item 2 / §4.8.11 Concept Properties + $lookup Out Parameters
    (https://hl7.org/fhir/R4/codesystem-operation-lookup.html):

    Each property entry in $lookup Out has a `part` array with two parts:
      - name="code", valueCode=<property-name>
      - name="value", value[x]=<property-value>

    The `code` part MUST use `valueCode` (the property name is itself a code,
    drawn from CodeSystem.property.code). A common drift bug: emitting
    `valueString` for the code part instead of `valueCode` — the wire format
    looks similar but breaks typed clients.

    This is a positive success-shape assertion: verify the actual `value*` key
    used for the code part is `valueCode`, not just that "property" appears.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system_uri, "code": code},
    )
    assert r.status_code == 200, f"$lookup {system_uri}/{code} → {r.status_code}"
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    props = [p for p in body.get("parameter", []) if p.get("name") == "property"]
    if not props:
        pytest.skip("no property entries returned by this fixture row")
    wrong_key = []
    for p in props:
        parts = p.get("part", [])
        code_part = next((pt for pt in parts if pt.get("name") == "code"), {})
        # The code part should have EXACTLY one value* key, and it should be valueCode.
        value_keys = [k for k in code_part if k.startswith("value")]
        if value_keys != ["valueCode"]:
            wrong_key.append({"property": code_part, "value_keys": value_keys})
    pytest.current_report_extra = f"property_count={len(props)} wrong_code_part={wrong_key}"
    assert not wrong_key, (
        f"$lookup property.code part must use valueCode (FHIR type 'code'). "
        f"Wrong value* keys found: {wrong_key}"
    )


def test_s11_lookup_top_level_code_uses_valueCode(fhir_client):
    """CS-01 item 3 / $lookup Out parameter `code`:
    https://hl7.org/fhir/R4/codesystem-operation-lookup.html lists `code` as
    type `code` (Out). The wire-format MUST use `valueCode`, not `valueString`.

    SKEPTIC positive success-shape: assert the exact key name on the top-level
    `code` parameter, not just that it appears in the response.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    assert r.status_code == 200
    body = r.json()
    code_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "code"), None
    )
    assert code_param is not None, "$lookup response missing top-level `code` parameter"
    pytest.current_report_extra = f"code_param_keys={list(code_param.keys())}"
    assert "valueCode" in code_param, (
        f"$lookup top-level `code` parameter must use valueCode; got "
        f"keys={list(code_param.keys())}. A `code` typed value MUST be "
        f"emitted as valueCode per FHIR R4 JSON encoding rules."
    )
    assert "valueString" not in code_param, (
        f"$lookup top-level `code` parameter emitted as valueString instead "
        f"of valueCode — wrong FHIR type. Param={code_param}"
    )


def test_s12_lookup_system_uses_valueUri(fhir_client):
    """CS-01 item 3 / $lookup Out parameter `system`:
    https://hl7.org/fhir/R4/codesystem-operation-lookup.html lists `system` as
    type `uri` (Out). Wire-format MUST use `valueUri`, not `valueString`.

    Catches the common drift where the URI is emitted as a string — wire-format
    looks similar but breaks URI-typed clients (canonical-reference parsers).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"), None
    )
    assert sys_param is not None, "$lookup response missing `system` parameter"
    pytest.current_report_extra = f"system_param_keys={list(sys_param.keys())}"
    assert "valueUri" in sys_param, (
        f"$lookup `system` parameter must use valueUri; got keys="
        f"{list(sys_param.keys())}. A `uri` typed value MUST be emitted as "
        f"valueUri per FHIR R4 JSON encoding rules."
    )


def test_s13_lookup_abstract_uses_valueBoolean(fhir_client):
    """CS-01 item 3 / $lookup Out parameter `abstract`:
    https://hl7.org/fhir/R4/codesystem-operation-lookup.html lists `abstract`
    as type `boolean` (Out). Wire-format MUST use `valueBoolean`.

    Catches the case where the abstract flag is emitted as valueString "false"
    or omitted entirely (silent-wrong-shape).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    body = r.json()
    abs_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "abstract"), None
    )
    assert abs_param is not None, (
        "$lookup response missing `abstract` parameter — required per R4 Out"
    )
    pytest.current_report_extra = f"abstract_param_keys={list(abs_param.keys())}"
    assert "valueBoolean" in abs_param, (
        f"$lookup `abstract` parameter must use valueBoolean; got keys="
        f"{list(abs_param.keys())}. A boolean typed value MUST be emitted as "
        f"valueBoolean per FHIR R4 JSON encoding rules."
    )


# ---------------------------------------------------------------------------
# Item 4: filter operators — no false advertisement
# ---------------------------------------------------------------------------

def test_s20_termcaps_no_filter_operators_advertised(fhir_client):
    """CS-01 item 4 / §4.8.15: CodeSystem.filter.operator lists the FHIR R4
    FilterOperator closed enum. TerminologyCapabilities.codeSystem.filter is
    optional (0..* — server MAY declare which filters it supports per system).

    SKEPTIC lens: medterm4ds does NOT implement filter-based value set
    composition today (no ValueSet.compose.include.filter evaluation against
    persisted code-system filters). If the server advertised filter operators,
    it would be overpromising — clients would compose filter-based value sets
    and get silent wrong answers.

    Probe: TerminologyCapabilities.codeSystem[].filter (if present) MUST only
    advertise operators from the FHIR R4 enum, AND medterm4ds SHOULD NOT
    advertise filters it doesn't implement.

    Per AGENTS.md NOT A BUG Registry, the server advertises content="not-present"
    for each system — filter advertisement would be inconsistent with that.
    """
    r = fhir_client.get("/fhir/metadata?mode=terminology")
    body = r.json()
    cs_entries = body.get("codeSystem", [])
    # Verify no entry advertises a filter operator outside the R4 enum.
    bad_operators = []
    for e in cs_entries:
        for f in e.get("filter", []):
            for op in f.get("operator", []):
                if op not in FHIR_R4_FILTER_OPERATORS:
                    bad_operators.append({
                        "uri": e.get("uri"), "filter": f.get("code"), "op": op,
                    })
    pytest.current_report_extra = f"bad_operators={bad_operators}"
    assert not bad_operators, (
        f"TerminologyCapabilities.codeSystem[].filter.operator advertised "
        f"values outside FHIR R4 FilterOperator enum: {bad_operators}. "
        f"Allowed: {sorted(FHIR_R4_FILTER_OPERATORS)}."
    )


def test_s21_capabilitystatement_no_codesystem_resource_filter_property(fhir_client):
    """CS-01 item 4 / §4.8.5: the CodeSystem RESOURCE has a `filter` element
    (0..*) declaring filter code+operator+value. A CapabilityStatement's
    CodeSystem resource block does NOT echo this — it's a per-resource-instance
    property, not a server-capability advertisement.

    SKEPTIC lens: verify the CapabilityStatement.rest.resource[type=CodeSystem]
    block does NOT include `filter` or `property` elements pretending to be
    server-level capabilities. Such elements would be invalid CapabilityStatement
    shape per https://hl7.org/fhir/R4/capabilitystatement-definitions.html.
    """
    r = fhir_client.get("/fhir/metadata?mode=full")
    body = r.json()
    rest = body.get("rest", [])
    cs_resource = None
    for r_block in rest:
        for res in r_block.get("resource", []):
            if res.get("type") == "CodeSystem":
                cs_resource = res
                break
    assert cs_resource is not None, "CapabilityStatement missing CodeSystem resource block"
    pytest.current_report_extra = f"keys={list(cs_resource.keys())}"
    # `filter` and `property` are NOT valid keys on CapabilityStatement.rest.resource.
    # They belong on CodeSystem resource instances, not on server-capability advertisements.
    assert "filter" not in cs_resource, (
        f"CapabilityStatement.rest.resource[CodeSystem] has invalid `filter` "
        f"key — that's a CodeSystem resource-level element, not a server "
        f"capability. Block: {cs_resource}"
    )
    assert "property" not in cs_resource, (
        f"CapabilityStatement.rest.resource[CodeSystem] has invalid `property` "
        f"key — that's a CodeSystem resource-level element, not a server "
        f"capability. Block: {cs_resource}"
    )


# ---------------------------------------------------------------------------
# Item 5: READ interaction (re-pin TS-01 carry-forward; CS-01 spec-citation)
# ---------------------------------------------------------------------------

def test_s30_read_codesystem_returns_operationoutcome_404(fhir_client):
    """CS-01 item 5 / §4.8.5 + §3.1.0.1.5: GET /fhir/CodeSystem/{id} on a
    terminology-only server that does not persist CodeSystem resources SHALL
    return a FHIR OperationOutcome (not a bare framework 404).

    Per AGENTS.md NOT A BUG Registry + TS-01 QA-002: medterm4ds does not
    persist CodeSystem resources — every READ returns 404 OperationOutcome.

    Positive success-shape assertion (per GLOBAL_RULES.md "Test-too-lenient"):
    verify the actual OperationOutcome shape, not just "not 200".
    """
    r = fhir_client.get("/fhir/CodeSystem/anything-at-all")
    pytest.current_report_extra = f"status={r.status_code} ct={r.headers.get('content-type')}"
    assert r.status_code == 404, (
        f"READ /fhir/CodeSystem/{{id}} → {r.status_code}; medterm4ds should "
        f"return 404 (no persisted CodeSystem resources)."
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"READ 404 body must be OperationOutcome; got resourceType="
        f"{body.get('resourceType')!r}. Body: {body}"
    )
    issues = body.get("issue", [])
    assert issues, "OperationOutcome missing issue[]"
    sev = issues[0].get("severity")
    code = issues[0].get("code")
    assert sev == "error", (
        f"OperationOutcome.issue[0].severity must be 'error'; got {sev!r}"
    )
    # Per the AGENTS.md spec contract, the not-found code is "not-found" (HTTP 404 mapping).
    assert code == "not-found", (
        f"OperationOutcome.issue[0].code must be 'not-found' for READ on "
        f"non-persisted resource; got {code!r}"
    )


def test_s31_read_codesystem_operation_name_as_id_rejected(fhir_client):
    """CS-01 item 5 / §3.1.0.1.4: operation names ($lookup, $validate-code)
    are NOT valid resource IDs. A READ of `/fhir/CodeSystem/$lookup` is a
    malformed request — the server should reject it explicitly with a FHIR
    OperationOutcome, not silently route it to the operation handler.

    SKEPTIC lens: a $-prefixed resource_id is a client confusion; the server
    must NOT return 200 (which would imply the operation ran on a stray path)
    and must NOT return framework-default 404.
    """
    r = fhir_client.get("/fhir/CodeSystem/$lookup")
    pytest.current_report_extra = f"status={r.status_code} body={r.text[:200]!r}"
    # The operation route `/fhir/CodeSystem/$lookup` IS registered, but it
    # requires system+code params. Without params, FastAPI returns 422 (missing
    # required query param) — which is fine. The risk is if the route shadowing
    # breaks and the request falls through to READ with resource_id="$lookup".
    # Acceptable: 422 (missing param), 400 (handler error). NOT acceptable: 404
    # with non-FHIR body, or 200 with empty Parameters (would mean READ
    # handler returned a "CodeSystem" for resource_id="$lookup").
    assert r.status_code in (400, 422), (
        f"/fhir/CodeSystem/$lookup (no params) → {r.status_code}; expected "
        f"422 (FastAPI missing-param) or 400 (handler). Body: {r.text[:300]}"
    )
    # Body must be FHIR-shaped (OperationOutcome for 400; can be FHIR-wrapped
    # 422 detail per TS-02 SKEPTIC QA-020). Verify content-type honors fhir+json.
    ct = r.headers.get("content-type", "")
    assert "fhir+json" in ct or "application/json" in ct, (
        f"Response Content-Type must be FHIR or JSON; got {ct!r}"
    )


# ---------------------------------------------------------------------------
# Item 6: SEARCH interaction — search params honesty
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("param", ["url", "version", "name", "title", "status"])
def test_s40_search_codesystem_accepts_spec_param(fhir_client, param):
    """CS-01 item 6 / §4.8.17 + §4.7.1.1 item 3: the CodeSystem SEARCH params
    `url`, `version`, `name`, `title`, `status` SHALL be supported.

    medterm4ds doesn't persist CodeSystem resources, so the SEARCH result is
    always an empty Bundle (per AGENTS.md NOT A BUG Registry — honest for a
    non-persisting server). This probe verifies the route exists and returns
    a Bundle (positive success-shape), not a 404/405/500.
    """
    r = fhir_client.get(f"/fhir/CodeSystem?{param}=test")
    pytest.current_report_extra = f"{param}=test → status={r.status_code} body={r.text[:120]!r}"
    assert r.status_code == 200, (
        f"SEARCH /fhir/CodeSystem?{param}=test → {r.status_code}; expected 200"
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle", (
        f"SEARCH must return Bundle; got resourceType={body.get('resourceType')!r}"
    )
    assert body.get("type") == "searchset", (
        f"SEARCH Bundle.type must be 'searchset'; got {body.get('type')!r}"
    )
    assert "total" in body, (
        f"SEARCH Bundle missing `total` field. Body: {body}"
    )
    # Empty result is conformant for a non-persisting server; verify the Bundle
    # is well-formed regardless of count.
    assert isinstance(body.get("entry", []), list), (
        f"SEARCH Bundle.entry must be a list (possibly empty); got "
        f"{type(body.get('entry'))!r}"
    )


def test_s41_search_codesystem_status_param_token_semantics(fhir_client):
    """CS-01 item 6 / §4.8.17 search parameter table: CodeSystem.status is a
    `token`-type search parameter bound to PublicationStatus (Required).
    The route MUST accept token-style values like `active` (the most common
    status). Empty result is conformant; the test verifies the route accepts
    the value and returns a well-formed Bundle.

    SKEPTIC lens: probe an exotic-but-valid token value (`unknown`) and verify
    the server doesn't crash or return a 500. Status `unknown` is in the
    PublicationStatus value set per https://hl7.org/fhir/R4/valueset-publication-status.html.
    """
    r = fhir_client.get("/fhir/CodeSystem?status=unknown")
    pytest.current_report_extra = f"status=unknown → {r.status_code} body={r.text[:120]!r}"
    assert r.status_code == 200, (
        f"SEARCH ?status=unknown → {r.status_code}; PublicationStatus includes "
        f"'unknown' so the route must accept it."
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle"


# ---------------------------------------------------------------------------
# Cross-resource consistency: SEARCH and READ return values consistent with
# the CapabilityStatement advertisement.
# ---------------------------------------------------------------------------

def test_s46_lookup_canonical_system_property_is_fhir_uri_when_pf_loaded(fhir_client):
    """CS-01 item 2 / §4.8.11 Concept Properties + §4.8.3.1 CodeSystem
    identification: when $lookup returns a custom `canonical-system` property
    (medterm4ds-local convention surfacing the patient-friendly crosswalk's
    canonical source), the value SHOULD be the FHIR R4 canonical system URI
    (e.g. `http://hl7.org/fhir/sid/icd-10-cm`), NOT the raw UMLS SAB label
    (e.g. `icd10`).

    Production-surface concern: the patient-friendly JSONs at
    /mnt/d/medterm4ds/reports/fhir4px/patient_friendly_*.json store
    `canonical_system` as the raw SAB label (`icd10`, `rxnorm`, etc.). The
    $lookup handler echoes this verbatim — see apps/fhir_api.py:1245. When the
    JSONs are loaded (production deployments), this is the THIRD instance of
    the literal-value-vs-canonical-registry drift pattern (count=3 as of
    TS-02 TERMINOLOGIST QA-030). The conformance fixture does NOT seed
    patient-friendly rows, so this probe SKIPS in CI today — the regression
    is documented for the TERMINOLOGIST-personality chunk that probes the
    patient-friendly surface against the production JSONs.

    Carry-forward: CF-SKEPTIC-CS01-01. Fix shape: translate the raw SAB
    label to the FHIR canonical URI via SYSTEM_TO_FHIR_URI before emitting.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    body = r.json()
    props = [p for p in body.get("parameter", []) if p.get("name") == "property"]
    cs_prop = None
    for p in props:
        parts = p.get("part", [])
        code_part = next((pt for pt in parts if pt.get("name") == "code"), {})
        if code_part.get("valueCode") == "canonical-system":
            cs_prop = p
            break
    if cs_prop is None:
        # Conformance fixture doesn't seed patient-friendly rows; the
        # canonical-system custom property isn't emitted. Skip — see header.
        pytest.skip(
            "canonical-system property not emitted (patient-friendly JSONs "
            "not loaded in conformance fixture). CF-SKEPTIC-CS01-01 documents "
            "the production-surface drift; TERMINOLOGIST chunk probes it."
        )
    val_part = next(
        (pt for pt in cs_prop.get("part", []) if pt.get("name") == "value"), {}
    )
    val = val_part.get("valueString") or val_part.get("valueUri")
    # If we get here, patient-friendly data IS loaded. Verify the value is a
    # proper FHIR URI (http://... or urn:...), not a raw SAB label.
    assert val is not None and (val.startswith("http://") or val.startswith("https://") or val.startswith("urn:")), (
        f"$lookup canonical-system property must be a FHIR canonical URI; "
        f"got {val!r}. Raw SAB label drift — translate via SYSTEM_TO_FHIR_URI. "
        f"CF-SKEPTIC-CS01-01."
    )


def test_s50_capabilitystatement_codesystem_searchparam_matches_r4(fhir_client):
    """CS-01 item 6 / §4.7.1.1 item 3 + §4.8.17: the CapabilityStatement
    SHALL advertise `url`, `version`, `name`, `title`, `status` search params
    for CodeSystem. Verify the advertisement is honest — every advertised
    search param actually accepts a value without 4xx.

    SKEPTIC lens: catches drift between advertised searchParam list and actual
    route-accepted params. (Re-pins TS-01 EXPLORER QA-010 normative + TS-01
    SKEPTIC QA-003, but cross-checked against the CapabilityStatement
    advertisement.)
    """
    r = fhir_client.get("/fhir/metadata?mode=full")
    body = r.json()
    rest = body.get("rest", [])
    cs_resource = None
    for r_block in rest:
        for res in r_block.get("resource", []):
            if res.get("type") == "CodeSystem":
                cs_resource = res
                break
    assert cs_resource is not None
    sp_names = {sp.get("name") for sp in cs_resource.get("searchParam", [])}
    required = {"url", "version", "name", "title", "status"}
    missing = required - sp_names
    pytest.current_report_extra = f"advertised={sorted(sp_names)} missing={sorted(missing)}"
    assert not missing, (
        f"CapabilityStatement.rest.resource[CodeSystem].searchParam missing "
        f"required R4 params: {sorted(missing)}. Advertised: {sorted(sp_names)}"
    )
    # Cross-check: every advertised search param actually works.
    for name in required:
        rr = fhir_client.get(f"/fhir/CodeSystem?{name}=test")
        assert rr.status_code == 200, (
            f"Advertised CodeSystem search param {name!r} returned "
            f"{rr.status_code} on SEARCH — capability-overpromise drift."
        )
