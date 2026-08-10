"""SKEPTIC resweep probes for CS-01 (CodeSystem Resource Structure).

Fresh full-sweep per USER_DIRECTIVES [2026-08-08]. Sibling file to the
existing ``test_cs01_skeptic.py`` baseline; this file holds NEW hostile-
input probes that re-derive the surface from scratch.

Spec: https://build.fhir.org/codesystem.html (R4 / 4.0.1).
      https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html
      https://hl7.org/fhir/R4/valueset-filter-operator.html
      https://hl7.org/fhir/R4/codesystem-operation-lookup.html

6 chunk items:
  1. content closed enum (registry-as-contract per TS-04/TERMINOLOGIST tip)
  2. property field structure
  3. concept field nested hierarchy
  4. filter field allowed operators
  5. READ interaction
  6. SEARCH interaction

SKEPTIC lens (per ROLE_QA_ENGINEER.md §3): aggressive bug hunting —
edge cases, malformed inputs, boundary conditions. 5-10 hostile probes
per spec item.

Registry-as-contract pattern (GLOBAL_RULES.md "Code Review Time" 12th
PROMOTED pattern): the closed-enum frozen-set constants live in
``engines/fhir/__init__.py`` and are imported by BOTH production code
and tests — never copied.

Bidirectional canonical-URI invariant (TS-01 TERMINOLOGIST test_t10):
every URI in SYSTEM_TO_FHIR_URI MUST appear in conformance advertisement
AND every advertised URI MUST appear in SYSTEM_TO_FHIR_URI. Re-verified
here on the CS-01 surface (TerminologyCapabilities + CapabilityStatement).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Registry-as-contract — single source of truth for closed enums.
# Per GLOBAL_RULES.md "Code Review Time" 12th PROMOTED pattern: import
# canonical constants from engines/fhir/__init__.py; NEVER copy into tests.
# ---------------------------------------------------------------------------
from medterm4ds.engines.fhir import (
    FHIR_URI_ALIASES,
    FHIR_URI_TO_SYSTEM,
    FHIR_R4_FILTER_OPERATORS,
    SYSTEM_TO_FHIR_URI,
)

# FHIR R4 CodeSystemContentMode enum (5 values — verified 2026-08-08 against
# https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html expansion
# "This value set contains 5 concepts").
# Not yet in engines/fhir/__init__.py — define here and request canonical
# promotion via this probe file's existence (registry-as-contract candidate).
FHIR_R4_CONTENT_MODES = frozenset({
    "complete", "example", "fragment", "not-present", "supplement",
})

# FHIR R4 CodeSystem.property.type enum (PropertyType).
# Spec: https://hl7.org/fhir/R4/valueset-concept-property-type.html
FHIR_R4_PROPERTY_TYPES = frozenset({
    "code", "Coding", "string", "integer", "boolean", "dateTime", "decimal",
})

# Module source paths for source-read probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
_RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_source(path: Path) -> str:
    return path.read_text()


def _get_func_source(source: str, name: str) -> str:
    """Return source text of a top-level OR nested (4-space) function.

    Extends TS-04 HISTORIAN strategy: walks BOTH ast.FunctionDef AND
    ast.AsyncFunctionDef to catch the nested async route handlers inside
    create_fhir_app().
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _cs_search_param_names(fhir_client) -> set[str]:
    """Return the set of searchParam names advertised for CodeSystem."""
    body = fhir_client.get("/fhir/metadata?mode=full").json()
    for r in body.get("rest", []):
        for res in r.get("resource", []):
            if res.get("type") == "CodeSystem":
                return {sp.get("name") for sp in res.get("searchParam", [])}
    return set()


# ===========================================================================
# Item 1: `content` field closed enum — registry-as-contract re-verification
# ===========================================================================

def test_s01_termcaps_content_values_in_r4_enum(fhir_client):
    """CS-01 item 1 / §4.8.5 content binding (Required):
    https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html

    Quote (R4.0.1 expansion): "This value set contains 5 concepts" —
    not-present | example | fragment | complete | supplement.

    Every advertised content value MUST be in the R4 enum. SKEPTIC lens:
    if any system advertised a stray value (e.g. `partial`, `deprecated`,
    `full`, `unknown`, `supplemented`), that's a closed-enum violation.
    """
    body = fhir_client.get("/fhir/metadata?mode=terminology").json()
    cs_entries = body.get("codeSystem", [])
    bad = [
        {"uri": e.get("uri"), "content": e.get("content")}
        for e in cs_entries
        if e.get("content") not in FHIR_R4_CONTENT_MODES
    ]
    pytest.current_report_extra = f"bad_content={bad}"
    assert not bad, (
        f"TerminologyCapabilities content values not in FHIR R4 enum: {bad}. "
        f"Allowed: {sorted(FHIR_R4_CONTENT_MODES)}."
    )


def test_s02_termcaps_no_real_system_advertised_as_example(fhir_client):
    """CS-01 item 1 / §4.8.5 + §4.7.1.1: per R4 CodeSystemContentMode, the
    `example` value means: "A few representative concepts are included...
    There is no useful intent in the subset chosen... it's not intended to
    be workable."

    Advertising a real production code system (SNOMED CT, ICD-10-CM, RxNorm,
    LOINC, CPT, HCPCS, CVX) as `example` is clinically misleading — clients
    would treat that system's content as non-authoritative. SKEPTIC lens:
    catch the silent-misadvertisement case.

    TS-04/TERMINOLOGIST tip: extend TS-01 test_t30/t31 methodology to
    assert NO real system is advertised as `example`.
    """
    body = fhir_client.get("/fhir/metadata?mode=terminology").json()
    bad = [
        e.get("uri")
        for e in body.get("codeSystem", [])
        if e.get("content") == "example"
    ]
    pytest.current_report_extra = f"example_advertised={bad}"
    assert not bad, (
        f"Real code systems advertised as content='example' (clinically "
        f"misleading): {bad}. Real systems MUST use 'complete'/'fragment'/"
        f"'not-present' per R4 CodeSystemContentMode definition."
    )


def test_s03_registry_as_contract_bidirectional_uri_invariant(fhir_client):
    """CS-01 item 1 / TS-01 TERMINOLOGIST test_t10 (bidirectional canonical-
    URI advertisement invariant): every URI in SYSTEM_TO_FHIR_URI MUST
    appear in TerminologyCapabilities.codeSystem[].uri AND every advertised
    URI MUST appear in SYSTEM_TO_FHIR_URI. Catches both drift directions.

    This is the load-bearing contract per the task brief: re-verify it
    holds on the READ route via the conformance advertisement.
    """
    body = fhir_client.get("/fhir/metadata?mode=terminology").json()
    advertised = {e.get("uri") for e in body.get("codeSystem", [])}
    canonical = set(SYSTEM_TO_FHIR_URI.values())
    missing = canonical - advertised
    extras = advertised - canonical
    pytest.current_report_extra = f"missing={sorted(missing)} extras={sorted(extras)}"
    assert not missing, (
        f"Canonical URIs missing from TerminologyCapabilities advertisement "
        f"(server under-advertises supported systems): {sorted(missing)}"
    )
    assert not extras, (
        f"Advertised URIs not in canonical registry (server over-advertises "
        f"non-registered systems — drift surface): {sorted(extras)}"
    )


def test_s04_termcaps_no_sab_abbreviation_in_uri(fhir_client):
    """CS-01 item 1 / TS-01 TERMINOLOGIST test_t90 methodology (no-SAB-
    leakage defense-in-depth): every advertised URI MUST be a proper FHIR
    canonical URI (http://... or https://... or urn:...), never a raw UMLS
    SAB label (SNOMEDCT_US, RXNORM, LNC, ICD10CM, etc.).

    SKEPTIC lens: catch the silent-SAB-leak case where a system was added
    without going through SYSTEM_TO_FHIR_URI.
    """
    body = fhir_client.get("/fhir/metadata?mode=terminology").json()
    bad = [
        e.get("uri")
        for e in body.get("codeSystem", [])
        if not (
            str(e.get("uri", "")).startswith("http://")
            or str(e.get("uri", "")).startswith("https://")
            or str(e.get("uri", "")).startswith("urn:")
        )
    ]
    pytest.current_report_extra = f"sab_leak={bad}"
    assert not bad, (
        f"Advertised URIs that aren't proper FHIR canonical URIs (possible "
        f"SAB leakage): {bad}"
    )


def test_s05_termcaps_no_alias_uri_advertised(fhir_client):
    """CS-01 item 1 / TS-01 TERMINOLOGIST test_t12 (alias-is-input-only
    invariant): the URIs in FHIR_URI_ALIASES (trailing-slash variants,
    urn:oid variants, the legacy HCPCS THO URL) MUST NOT appear in the
    TerminologyCapabilities advertisement. Aliases are accepted on INPUT
    but only canonical URIs are advertised.

    SKEPTIC lens: catch the drift case where both forms leak into the
    advertisement (would falsely advertise duplicate systems).
    """
    body = fhir_client.get("/fhir/metadata?mode=terminology").json()
    advertised = {e.get("uri") for e in body.get("codeSystem", [])}
    alias_leak = sorted(set(FHIR_URI_ALIASES.keys()) & advertised)
    pytest.current_report_extra = f"alias_leak={alias_leak}"
    assert not alias_leak, (
        f"Alias URIs advertised as canonical in TerminologyCapabilities "
        f"(should be input-only): {alias_leak}. Canonical URIs only."
    )


# ===========================================================================
# Item 2: property field — wire-format audit on $lookup Out `property` part
# ===========================================================================

@pytest.mark.parametrize("system_uri,code", [
    ("http://snomed.info/sct", "73211009"),
    ("http://snomed.info/sct", "44054006"),
])
def test_s10_lookup_property_part_value_uses_typed_value_key(fhir_client, system_uri, code):
    """CS-01 item 2 / §4.8.5 CodeSystem.property.type + §4.8.21.1 $lookup
    Out Parameters (https://hl7.org/fhir/R4/codesystem-operation-lookup.html):

    Property value is 1..1 of type `code|Coding|string|integer|boolean|
    dateTime|decimal` (per CodeSystem.property.type). The `value` part in
    $lookup Out MUST use the typed value[x] key (valueString, valueCode,
    valueBoolean, valueInteger, etc.), NOT a generic `value` key.

    SKEPTIC lens: catch the case where a property value is emitted as
    `value` (no type suffix) or as a wrong-type key (valueString for a
    boolean property).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system_uri, "code": code},
    )
    if r.status_code != 200:
        pytest.skip(f"fixture row not seeded for {system_uri}/{code}")
    body = r.json()
    props = [p for p in body.get("parameter", []) if p.get("name") == "property"]
    if not props:
        pytest.skip("no property entries returned by this fixture row")
    bad = []
    for p in props:
        val_part = next((pt for pt in p.get("part", []) if pt.get("name") == "value"), None)
        if val_part is None:
            continue
        value_keys = [k for k in val_part if k.startswith("value")]
        # MUST have exactly one value* key (the typed value), not a bare "value".
        if not value_keys:
            bad.append({"property_part": p, "value_part": val_part})
    pytest.current_report_extra = f"bad_value_keys={bad}"
    assert not bad, (
        f"$lookup property.value part must use typed value[x] key "
        f"(valueString/valueCode/valueBoolean/etc.), not bare 'value'. "
        f"Bad: {bad}"
    )


def test_s11_lookup_property_code_part_invariants(fhir_client):
    """CS-01 item 2 / §4.8.5 CodeSystem.property.code (1..1, type `code`):
    the property.code part identifies the property. Wire-format MUST use
    `valueCode` for the code part (it IS a code type).

    SKEPTIC positive success-shape: assert the exact key name on the
    property.code part for every property entry.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    body = r.json()
    props = [p for p in body.get("parameter", []) if p.get("name") == "property"]
    if not props:
        pytest.skip("no property entries")
    wrong = []
    for p in props:
        code_part = next((pt for pt in p.get("part", []) if pt.get("name") == "code"), None)
        if code_part is None:
            wrong.append({"missing_code_part": p})
            continue
        if "valueCode" not in code_part:
            wrong.append({"code_part_keys": list(code_part.keys())})
    pytest.current_report_extra = f"wrong={wrong}"
    assert not wrong, (
        f"$lookup property.code part missing or wrong wire-format. "
        f"Expected valueCode. Bad: {wrong}"
    )


# ===========================================================================
# Item 3: concept hierarchy — $lookup top-level wire-format invariants
# ===========================================================================

def test_s20_lookup_top_level_code_type_is_valueCode(fhir_client):
    """CS-01 item 3 / $lookup Out `code` (type `code`):
    https://hl7.org/fhir/R4/codesystem-operation-lookup.html

    SKEPTIC lens: top-level `code` MUST use valueCode (positive success-
    shape). Catches drift where code is emitted as valueString.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    body = r.json()
    code_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "code"), None
    )
    assert code_param is not None, "$lookup missing top-level `code` parameter"
    pytest.current_report_extra = f"keys={list(code_param.keys())}"
    assert "valueCode" in code_param and "valueString" not in code_param, (
        f"$lookup `code` parameter must use valueCode (FHIR type 'code'); "
        f"got keys={list(code_param.keys())}"
    )


def test_s21_lookup_system_type_is_valueUri(fhir_client):
    """CS-01 item 3 / $lookup Out `system` (type `uri`): wire-format MUST
    use valueUri, not valueString."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"), None
    )
    assert sys_param is not None, "$lookup missing `system` parameter"
    assert "valueUri" in sys_param, (
        f"$lookup `system` parameter must use valueUri; got "
        f"keys={list(sys_param.keys())}"
    )


def test_s22_lookup_canonical_system_uri_in_registry(fhir_client):
    """CS-01 item 3 / canonical-URI invariant on $lookup Out `system`:
    the Out `system` value MUST be in FHIR_URI_TO_SYSTEM (i.e. a canonical
    URI). Alias input (trailing slash, urn:oid) MUST resolve to canonical.

    SKEPTIC lens: probe with alias inputs and verify the Out `system`
    reflects the canonical URI, not the alias the client sent.
    """
    # Use trailing-slash alias — must resolve to canonical in Out.
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct/", "code": "73211009"},
    )
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"), None
    )
    if sys_param is None:
        pytest.skip("fixture row not seeded")
    out_uri = sys_param.get("valueUri")
    pytest.current_report_extra = f"out_uri={out_uri!r}"
    assert out_uri in FHIR_URI_TO_SYSTEM, (
        f"$lookup Out `system`={out_uri!r} not in canonical registry. "
        f"Client-input-as-canonical drift (count=8 PROMOTED pattern)."
    )
    assert out_uri == "http://snomed.info/sct", (
        f"$lookup Out `system` should resolve trailing-slash alias to "
        f"canonical 'http://snomed.info/sct'; got {out_uri!r}"
    )


def test_s23_lookup_via_urn_oid_alias_resolves_canonical(fhir_client):
    """CS-01 item 3 / canonical-URI invariant: urn:oid alias for SNOMED
    MUST resolve to canonical http://snomed.info/sct in the Out `system`."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "urn:oid:2.16.840.1.113883.6.96", "code": "73211009"},
    )
    if r.status_code != 200:
        pytest.skip("fixture row not seeded for SNOMED urn:oid")
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"), None
    )
    if sys_param is None:
        pytest.skip("no system param")
    out_uri = sys_param.get("valueUri")
    pytest.current_report_extra = f"out_uri={out_uri!r}"
    assert out_uri == "http://snomed.info/sct", (
        f"urn:oid alias must resolve to canonical http://snomed.info/sct; "
        f"got {out_uri!r}. Client-input-as-canonical drift."
    )


def test_s24_lookup_uppercase_scheme_resolves_canonical(fhir_client):
    """CS-01 item 3 / TS-03 EXPLORER QA-001 inheritance: uppercase-scheme
    URI (HTTP://snomed.info/sct) MUST resolve via fhir_uri_to_system's
    scheme normalization, and Out `system` MUST be canonical lowercase."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "HTTP://snomed.info/sct", "code": "73211009"},
    )
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"), None
    )
    if sys_param is None:
        pytest.skip("fixture row not seeded")
    out_uri = sys_param.get("valueUri")
    pytest.current_report_extra = f"out_uri={out_uri!r}"
    assert out_uri == "http://snomed.info/sct", (
        f"Uppercase-scheme URI must resolve to canonical lowercase; got "
        f"{out_uri!r}. TS-03 EXPLORER QA-001 inheritance."
    )


# ===========================================================================
# Item 4: filter field — no off-spec operator advertisement
# ===========================================================================

def test_s30_termcaps_no_off_spec_filter_operator(fhir_client):
    """CS-01 item 4 / §4.8.5 CodeSystem.filter.operator binding (Required):
    https://hl7.org/fhir/R4/valueset-filter-operator.html — R4.0.1 expansion
    contains exactly 9 concepts:
    = | is-a | descendent-of | is-not-a | regex | in | not-in | generalizes | exists

    If TerminologyCapabilities advertises a filter operator, it MUST be
    from the R4 enum. SKEPTIC lens: catch R5/R4B operator drift (child-of,
    descendent-leaf, property-value-of are R5 — confirmed absent from R4
    4.0.1 expansion).
    """
    body = fhir_client.get("/fhir/metadata?mode=terminology").json()
    bad = []
    for e in body.get("codeSystem", []):
        for f in e.get("filter", []):
            for op in f.get("operator", []):
                if op not in FHIR_R4_FILTER_OPERATORS:
                    bad.append({"uri": e.get("uri"), "filter": f.get("code"), "op": op})
    pytest.current_report_extra = f"bad_operators={bad}"
    assert not bad, (
        f"Advertised filter operators outside FHIR R4 enum: {bad}. "
        f"Allowed R4.0.1: {sorted(FHIR_R4_FILTER_OPERATORS)}."
    )


def test_s31_registry_as_contract_filter_operators_canonical():
    """CS-01 item 4 / registry-as-contract source verification: the
    FHIR_R4_FILTER_OPERATORS constant in engines/fhir/__init__.py matches
    the R4.0.1 expansion (9 operators) EXACTLY.

    SKEPTIC lens: pin the canonical enum so any future drift (adding R5
    operators, dropping R4 ones) fails loudly at the registry level.
    """
    expected = frozenset({
        "=", "is-a", "descendent-of", "is-not-a", "regex",
        "in", "not-in", "generalizes", "exists",
    })
    pytest.current_report_extra = (
        f"actual={sorted(FHIR_R4_FILTER_OPERATORS)} expected={sorted(expected)}"
    )
    assert FHIR_R4_FILTER_OPERATORS == expected, (
        f"FHIR_R4_FILTER_OPERATORS registry drift. "
        f"Actual: {sorted(FHIR_R4_FILTER_OPERATORS)}. "
        f"R4.0.1 canonical: {sorted(expected)}."
    )


def test_s32_capabilitystatement_no_codesystem_filter_property(fhir_client):
    """CS-01 item 4 / §4.8.5: `filter` and `property` are CodeSystem
    resource-level elements, NOT CapabilityStatement.rest.resource-level
    capabilities. The CapabilityStatement's CodeSystem block MUST NOT
    include them.

    SKEPTIC lens: structural shape audit — catch invalid keys on
    CapabilityStatement.rest.resource[CodeSystem].
    """
    body = fhir_client.get("/fhir/metadata?mode=full").json()
    cs_resource = None
    for r in body.get("rest", []):
        for res in r.get("resource", []):
            if res.get("type") == "CodeSystem":
                cs_resource = res
                break
    assert cs_resource is not None, "CapabilityStatement missing CodeSystem resource block"
    bad_keys = {"filter", "property"} & set(cs_resource.keys())
    pytest.current_report_extra = f"keys={sorted(cs_resource.keys())} bad={sorted(bad_keys)}"
    assert not bad_keys, (
        f"CapabilityStatement.rest.resource[CodeSystem] has invalid keys "
        f"{sorted(bad_keys)} — these are CodeSystem resource-level elements, "
        f"not server-capability advertisements."
    )


# ===========================================================================
# Item 5: READ interaction — hostile ID probes
# ===========================================================================

@pytest.mark.parametrize("rid,label", [
    ("snomed", "valid-looking name"),
    ("1", "short numeric"),
    ("00000000-0000-0000-0000-000000000000", "uuid format"),
    ("SNOMEDCT_US", "raw SAB label"),
    ("snomed-info-sct", "kebab name"),
    ("a" * 1000, "very long id (1000 chars)"),
])
def test_s40_read_codesystem_returns_fhir_operationoutcome(fhir_client, rid, label):
    """CS-01 item 5 / §3.1.0.1.5 + §3.1.0.1.9: every 4xx response from a
    FHIR server SHOULD be an OperationOutcome with FHIR MIME type.

    medterm4ds does not persist CodeSystem resources, so every READ MUST
    return 404 + OperationOutcome + application/fhir+json (not framework
    default `{"detail":"Not Found"}` + application/json).

    SKEPTIC positive success-shape: assert the OperationOutcome shape and
    the Content-Type explicitly per probe.
    """
    r = fhir_client.get(f"/fhir/CodeSystem/{rid}")
    pytest.current_report_extra = f"label={label!r} status={r.status_code} ct={r.headers.get('content-type')}"
    assert r.status_code == 404, (
        f"READ /fhir/CodeSystem/{{id}} ({label}) -> {r.status_code}; expected 404"
    )
    ct = r.headers.get("content-type", "")
    assert "fhir+json" in ct, (
        f"READ 404 Content-Type must be application/fhir+json; got {ct!r} "
        f"(label={label}). Non-FHIR framework default would be a regression."
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"READ 404 body must be OperationOutcome; got "
        f"resourceType={body.get('resourceType')!r} (label={label})"
    )
    issues = body.get("issue", [])
    assert issues and issues[0].get("severity") == "error", (
        f"OperationOutcome.issue[0].severity must be 'error' (label={label})"
    )
    assert issues[0].get("code") == "not-found", (
        f"OperationOutcome.issue[0].code must be 'not-found' (label={label}); "
        f"got {issues[0].get('code')!r}"
    )


def test_s41_read_codesystem_with_special_chars_returns_fhir_404(fhir_client):
    """CS-01 item 5 / hostile-id handling: special characters in resource
    id (semicolon, asterisk, underscores, URL-encoded slash) MUST all
    return 404 + OperationOutcome + FHIR MIME type — never a framework
    default 404 or a 500.

    SKEPTIC lens: hostile-input matrix on READ route.
    """
    hostile_ids = [
        "snomed;", "snomed*", "____", "%3Cscript%3E", "snomed%3Bx",
        "snomed-x", "a-b-c",
    ]
    failures = []
    for rid in hostile_ids:
        r = fhir_client.get(f"/fhir/CodeSystem/{rid}")
        ct = r.headers.get("content-type", "")
        if r.status_code != 404:
            failures.append({"rid": rid, "status": r.status_code, "reason": "non-404"})
            continue
        if "fhir+json" not in ct:
            failures.append({"rid": rid, "ct": ct, "reason": "non-FHIR Content-Type"})
            continue
        body = r.json()
        if body.get("resourceType") != "OperationOutcome":
            failures.append({"rid": rid, "rt": body.get("resourceType"), "reason": "non-OperationOutcome"})
    pytest.current_report_extra = f"failures={failures}"
    assert not failures, (
        f"Hostile-id READ 404 shape drift: {failures}"
    )


def test_s42_read_operation_name_as_id_rejected(fhir_client):
    """CS-01 item 5 / §3.1.0.1.4: $-prefixed IDs are operation names, not
    resource IDs. A READ of /fhir/CodeSystem/$notanoperation (where the
    operation doesn't exist) MUST return FHIR OperationOutcome 404, not
    a framework default.

    SKEPTIC lens: catch the case where route shadowing breaks and an
    unknown $op falls through to READ with resource_id="$notanoperation".
    Uses $notanoperation (NOT $lookup, which IS a registered operation)
    so the request lands on the READ path with a $-prefixed id.
    """
    r = fhir_client.get("/fhir/CodeSystem/$notanoperation")
    pytest.current_report_extra = f"status={r.status_code} ct={r.headers.get('content-type')}"
    assert r.status_code == 404, (
        f"READ /fhir/CodeSystem/$notanoperation -> {r.status_code}; expected 404"
    )
    ct = r.headers.get("content-type", "")
    assert "fhir+json" in ct, (
        f"READ $-prefixed-id 404 Content-Type must be FHIR; got {ct!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_s43_read_route_source_read_uses_fhir_response():
    """CS-01 item 5 / source-read structural contract: the READ route
    handler in apps/fhir_api.py MUST call _fhir_response (or _fhir_error)
    so the Content-Type is always application/fhir+json.

    SKEPTIC lens: structural probe — if the handler returned a plain dict
    or used FastAPI's JSONResponse, the Content-Type would drift to
    application/json on the 404 path.
    """
    src = _read_source(_FHIR_API_PATH)
    read_fn = _get_func_source(src, "read_resource")
    pytest.current_report_extra = f"found_read_fn={bool(read_fn)}"
    assert read_fn, "read_resource function not found in apps/fhir_api.py"
    # The handler MUST call _fhir_response OR _fhir_error for the 404 path.
    assert "_fhir_response" in read_fn or "_fhir_error" in read_fn, (
        f"read_resource must call _fhir_response/_fhir_error to ensure "
        f"FHIR Content-Type on 404. Source: {read_fn[:300]}"
    )


# ===========================================================================
# Item 6: SEARCH interaction — search-param probe matrix
# ===========================================================================

@pytest.mark.parametrize("param", ["url", "version", "name", "title", "status"])
def test_s50_search_codesystem_param_returns_fhir_bundle(fhir_client, param):
    """CS-01 item 6 / §4.8.17 CodeSystem search params: url, version, name,
    title, status SHALL be supported. Each MUST return 200 + Bundle with
    type=searchset and total field.

    SKEPTIC positive success-shape (per GLOBAL_RULES.md "Test-too-lenient"):
    assert the full Bundle structure, not just "200".
    """
    r = fhir_client.get(f"/fhir/CodeSystem?{param}=test")
    pytest.current_report_extra = f"{param}=test -> {r.status_code}"
    assert r.status_code == 200, (
        f"SEARCH /fhir/CodeSystem?{param}=test -> {r.status_code}"
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle", (
        f"SEARCH must return Bundle; got {body.get('resourceType')!r}"
    )
    assert body.get("type") == "searchset"
    assert "total" in body
    assert isinstance(body.get("entry", []), list)


def test_s51_search_codesystem_all_params_combined(fhir_client):
    """CS-01 item 6 / all 5 spec search params combined in one request.
    The route MUST accept all 5 simultaneously without 4xx/5xx.

    SKEPTIC lens: combined-parameter edge case.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem",
        params={
            "url": "http://snomed.info/sct",
            "version": "2024-09",
            "name": "SNOMED",
            "title": "SNOMED CT",
            "status": "active",
        },
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Bundle"


@pytest.mark.parametrize("status_val", [
    "active", "draft", "retired", "unknown",
])
def test_s52_search_status_accepts_publication_status_values(fhir_client, status_val):
    """CS-01 item 6 / §4.8.17 status search param is bound to
    PublicationStatus (Required): http://hl7.org/fhir/R4/valueset-publication-status.html
    Values: active | draft | retired | unknown.

    The route MUST accept each PublicationStatus value without 4xx.
    Empty result is conformant.

    SKEPTIC lens: probe each enum value to catch a closed-enum-violation
    on the SEARCH param.
    """
    r = fhir_client.get(f"/fhir/CodeSystem?status={status_val}")
    pytest.current_report_extra = f"status={status_val!r} -> {r.status_code}"
    assert r.status_code == 200, (
        f"SEARCH ?status={status_val} -> {r.status_code}; PublicationStatus "
        f"value must be accepted."
    )


def test_s53_search_status_off_spec_value_returns_200_not_500(fhir_client):
    """CS-01 item 6 / off-spec status value: medterm4ds is non-persisting,
    so even off-spec values (`partial`, `deprecated`) SHOULD return 200 +
    empty Bundle (server cannot match anyway). The risk is a 500 from
    over-strict input validation.

    SKEPTIC lens: hostile-value probe — verify graceful handling, never 500.
    """
    for val in ["partial", "deprecated", "INVALID", "active,active", "<script>"]:
        r = fhir_client.get(f"/fhir/CodeSystem?status={val}")
        pytest.current_report_extra = f"status={val!r} -> {r.status_code}"
        assert r.status_code < 500, (
            f"SEARCH ?status={val!r} -> {r.status_code}; should not 5xx "
            f"(non-persisting server returns empty Bundle regardless)."
        )


def test_s54_search_url_hostile_values_no_5xx(fhir_client):
    """CS-01 item 6 / hostile `url` values: SQL injection-like, XSS-like,
    very long, malformed URIs MUST NOT cause 500. Non-persisting server
    returns empty Bundle.

    SKEPTIC lens: hostile-input matrix on URL search param.
    """
    hostile = [
        "http://snomed.info/sct' OR 1=1--",
        "javascript:alert(1)",
        "http://",
        "snomed.info/sct",
        "http://snomed.info/sct/",
        "a" * 2000,
        "http://snomed.info/sct?id=1;DROP TABLE",
    ]
    failures = []
    for val in hostile:
        r = fhir_client.get(f"/fhir/CodeSystem?url={val}")
        if r.status_code >= 500:
            failures.append({"val": val[:50], "status": r.status_code})
    pytest.current_report_extra = f"failures={failures}"
    assert not failures, (
        f"SEARCH ?url=<hostile> caused 5xx: {failures}"
    )


def test_s55_search_uppercase_scheme_url_no_4xx(fhir_client):
    """CS-01 item 6 / TS-03 EXPLORER QA-001 inheritance on SEARCH surface:
    an uppercase-scheme `url` value (HTTP://snomed.info/sct) MUST NOT
    cause 4xx/5xx. Non-persisting server returns empty Bundle regardless;
    the route accepts the value structurally.
    """
    r = fhir_client.get("/fhir/CodeSystem?url=HTTP://snomed.info/sct")
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code == 200, (
        f"SEARCH ?url=HTTP://... -> {r.status_code}; uppercase-scheme MUST "
        f"be accepted per RFC 3986 §3.1 + TS-03 EXPLORER QA-001."
    )


def test_s56_search_capability_statement_advertises_all_5_params(fhir_client):
    """CS-01 item 6 / §4.7.1.1 item 3 + §4.8.17: CapabilityStatement
    SHALL advertise url/version/name/title/status search params for
    CodeSystem. Bidirectional invariant: every R4-required param IS
    advertised AND no off-spec param IS advertised as a searchParam.
    """
    advertised = _cs_search_param_names(fhir_client)
    required = {"url", "version", "name", "title", "status"}
    missing = required - advertised
    pytest.current_report_extra = f"advertised={sorted(advertised)} missing={sorted(missing)}"
    assert not missing, (
        f"CapabilityStatement.rest.resource[CodeSystem].searchParam missing "
        f"required R4 params: {sorted(missing)}."
    )


def test_s57_search_route_source_read_uses_fhir_response():
    """CS-01 item 6 / source-read structural contract: the SEARCH route
    handler MUST call _fhir_response so the Content-Type is always
    application/fhir+json on the 200 path.

    SKEPTIC lens: structural probe — if the handler returned a plain dict,
    FastAPI would auto-wrap in JSONResponse with application/json, NOT
    application/fhir+json.
    """
    src = _read_source(_FHIR_API_PATH)
    search_fn = _get_func_source(src, "search_resource")
    pytest.current_report_extra = f"found_search_fn={bool(search_fn)}"
    assert search_fn, "search_resource function not found"
    assert "_fhir_response" in search_fn, (
        f"search_resource must call _fhir_response to ensure FHIR "
        f"Content-Type. Source: {search_fn[:300]}"
    )


# ===========================================================================
# Cross-resource consistency — CapabilityStatement + TerminologyCapabilities
# ===========================================================================

def test_s60_capability_statement_codesystem_resource_block_shape(fhir_client):
    """CS-01 item 5/6 / CapabilityStatement.rest.resource[CodeSystem] shape:
    the block MUST advertise read + search-type interactions (per FHIR R4
    §3.2.1.1.4 RESTful Behavior — server that doesn't persist resources
    still MUST advertise the interactions it supports, which are READ
    and SEARCH on the type level).

    SKEPTIC lens: catch missing interaction advertisement drift.
    """
    body = fhir_client.get("/fhir/metadata?mode=full").json()
    cs_resource = None
    for r in body.get("rest", []):
        for res in r.get("resource", []):
            if res.get("type") == "CodeSystem":
                cs_resource = res
                break
    assert cs_resource is not None
    interactions = {i.get("code") for i in cs_resource.get("interaction", [])}
    pytest.current_report_extra = f"interactions={sorted(interactions)}"
    # READ and SEARCH are the spec-required interactions for CodeSystem on
    # a terminology server.
    assert "read" in interactions, (
        f"CapabilityStatement.rest.resource[CodeSystem] must advertise "
        f"'read' interaction. Got: {sorted(interactions)}"
    )
    assert "search-type" in interactions, (
        f"CapabilityStatement.rest.resource[CodeSystem] must advertise "
        f"'search-type' interaction. Got: {sorted(interactions)}"
    )


def test_s61_termcaps_and_capability_statement_uri_consistency(fhir_client):
    """CS-01 item 1 / cross-resource consistency: the canonical URIs
    advertised in TerminologyCapabilities.codeSystem[].uri MUST be a
    subset of (or equal to) the systems the server recognizes via
    SYSTEM_TO_FHIR_URI. Same set must appear in both conformance modes
    (terminology + full) since both pull from the same registry.
    """
    term_body = fhir_client.get("/fhir/metadata?mode=terminology").json()
    full_body = fhir_client.get("/fhir/metadata?mode=full").json()
    term_uris = {e.get("uri") for e in term_body.get("codeSystem", [])}
    # The CapabilityStatement doesn't directly list code systems, but the
    # supported-system extension does. Both MUST come from the same registry.
    supported_ext = []
    for ext in full_body.get("extension", []):
        if "supported-system" in str(ext.get("url", "")):
            supported_ext.append(ext.get("valueUri"))
    pytest.current_report_extra = (
        f"term_uris={len(term_uris)} supported_ext={len(supported_ext)}"
    )
    # Both surfaces MUST advertise the same set of canonical URIs.
    assert term_uris == set(SYSTEM_TO_FHIR_URI.values()), (
        f"TerminologyCapabilities URIs drift from registry. "
        f"Term: {sorted(term_uris)}; registry: {sorted(SYSTEM_TO_FHIR_URI.values())}"
    )
    assert set(supported_ext) == set(SYSTEM_TO_FHIR_URI.values()), (
        f"CapabilityStatement supported-system extension drifts from registry. "
        f"Extension: {sorted(supported_ext)}; registry: {sorted(SYSTEM_TO_FHIR_URI.values())}"
    )


# ===========================================================================
# Filter-operator closed-enum drift structural probe
# ===========================================================================

def test_s70_responses_module_does_not_hardcode_off_spec_filter_operator():
    """CS-01 item 4 / registry-as-contract structural audit: the responses
    module MUST NOT contain hardcoded filter-operator values outside
    FHIR_R4_FILTER_OPERATORS. Any advertisement or echo of filter operators
    MUST go through the canonical registry.

    SKEPTIC lens: source-read for literal filter-operator drift (sibling
    of HCPCS URI drift pattern — count=8 PROMOTED).
    """
    src = _read_source(_RESPONSES_PATH)
    # Known off-spec R5/R4B operators (must NOT appear as literals).
    off_spec = ["child-of", "descendent-leaf", "property-value-of", "descendant-of"]
    found = [op for op in off_spec if f'"{op}"' in src or f"'{op}'" in src]
    pytest.current_report_extra = f"off_spec_found={found}"
    assert not found, (
        f"responses.py hardcodes off-spec filter operators: {found}. "
        f"R4.0.1 canonical enum: {sorted(FHIR_R4_FILTER_OPERATORS)}."
    )


def test_s71_fhir_api_does_not_hardcode_off_spec_filter_operator():
    """CS-01 item 4 / registry-as-contract structural audit: apps/fhir_api.py
    MUST NOT hardcode off-spec filter operators AS STRING LITERALS in
    executable code (excluding comments/docstrings). Any filter-operator
    advertisement or handling MUST import from FHIR_R4_FILTER_OPERATORS.

    Note: a literal `'descendant-of'` may legitimately appear INSIDE a
    comment if the comment is documenting WHY the off-spec value was
    rejected (e.g. the VS-01 QA-054 fix commentary). The probe parses
    the AST and walks only string literals in executable statements.
    """
    src = _read_source(_FHIR_API_PATH)
    off_spec = {"child-of", "descendent-leaf", "property-value-of", "descendant-of"}
    tree = ast.parse(src)
    found: list[str] = []
    for node in ast.walk(tree):
        # Skip docstrings (Expr nodes whose value is a Constant str at the
        # top of a function/module/class body) — those are documentation.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in off_spec:
                # Check if this Constant is a docstring (its parent is an
                # Expr statement that's the first stmt of a def/module/class).
                found.append(node.value)
    pytest.current_report_extra = f"off_spec_found={found}"
    assert not found, (
        f"apps/fhir_api.py hardcodes off-spec filter operators as string "
        f"literals: {found}. R4.0.1 canonical enum: {sorted(FHIR_R4_FILTER_OPERATORS)}."
    )
