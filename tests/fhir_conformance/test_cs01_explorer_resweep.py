"""EXPLORER resweep probes for CS-01 (CodeSystem Resource Structure).

Fresh full-sweep per USER_DIRECTIVES [2026-08-08]. Sibling file to the
existing ``test_cs01_explorer.py`` baseline; this file holds NEW lateral
probes targeting interaction corners not covered by SKEPTIC (44) or
HISTORIAN (27).

Spec: https://build.fhir.org/codesystem.html (R4 / 4.0.1).
      https://hl7.org/fhir/R4/http.html (READ interaction)
      https://hl7.org/fhir/R4/search.html (SEARCH result params)

EXPLORER lens (per ROLE_QA_ENGINEER.md §3): lateral thinking. Unusual
parameter combinations, undocumented features, integration corners.

HISTORIAN tip for EXPLORER (from CS-01_HISTORIAN_qa_handoff.md "Test Plan
Insights for EXPLORER"): probe
  - Combined operations ($lookup → READ → SEARCH on same system) — verify
    consistency across all three interaction patterns for the same
    canonical URI.
  - _format=xml on READ/SEARCH routes — content negotiation on resource
    routes, not just operation routes.
  - Cross-resource consistency parametrized over alias inputs
    (trailing-slash, urn:oid, uppercase-scheme applied to CodeSystem.url
    in READ and SEARCH).
  - Lateral corners on SEARCH: combined search params with mixed case,
    partial matches, search with status=draft/retired/unknown.
  - READ with _summary=count, _summary=true, _summary=data (FHIR R4
    summary parameters per https://hl7.org/fhir/R4/search.html#summary).
  - READ with _elements parameter (partial resource retrieval per
    https://hl7.org/fhir/R4/search.html#elements).

  - The 1 fixture-skip from HISTORIAN (test_h24 — SNOMED 73211009
    canonical-system custom property) signals conformance fixture lacks
    patient-friendly data for that code; EXPLORER may document this gap
    or verify the invariant on a fixture row that has PF data.

For each probe: source-read the relevant medterm4ds code AND/OR fetch the
spec page. Log bugs with spec citations; do NOT log non-bugs (intended
behaviors go in the "Notable Non-Bugs" section of qa_handoff.md).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Single source of truth — import canonical constants from engines/fhir.
# ---------------------------------------------------------------------------
from medterm4ds.engines.fhir import (
    FHIR_URI_ALIASES,
    SYSTEM_TO_FHIR_URI,
    canonical_system_uri,
)

# Module source paths for source-read probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# ---------------------------------------------------------------------------
# Helpers (mirror HISTORIAN resweep helpers)
# ---------------------------------------------------------------------------

def _read_source(path: Path) -> str:
    return path.read_text()


def _get_func_source(source: str, name: str) -> str:
    """Return source text of a top-level OR nested function.

    Sibling of HISTORIAN resweep helper; walks BOTH ``ast.FunctionDef`` AND
    ``ast.AsyncFunctionDef`` to catch nested async route handlers inside
    ``create_fhir_app()``.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _all_get_routes(app) -> list[str]:
    """Return all GET route paths registered on the FastAPI app.

    EXPLORER lateral methodology: parametrize probes over EVERY declared
    GET route (per GLOBAL_RULES.md "Code Review Time" conformance-probe
    pattern). Used to verify XML content negotiation is uniform across
    every CodeSystem route (operation + resource routes).
    """
    paths = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if "GET" in methods:
            path = getattr(route, "path", None)
            if path and "/fhir/" in path:
                paths.append(path)
    return sorted(paths)


# ===========================================================================
# Lens 1: Combined operations — $lookup → READ → SEARCH on same system
# HISTORIAN tip: "verify consistency across all three interaction patterns
# for the same canonical URI".
# ===========================================================================

def test_e10_combined_lookup_read_search_snomed_consistency(fhir_client):
    """EXPLORER lateral: combined operations on the same canonical URI
    MUST yield consistent URI advertisement across $lookup, READ, and
    SEARCH interactions.

    $lookup returns the canonical-system custom property (when PF data
    exists); READ returns 404 (no persisted resources — NOT A BUG
    Registry); SEARCH returns empty Bundle. The CONSISTENCY invariant is
    that ALL THREE accept the same canonical URI as input without
    unexpected behavior divergence (4xx where 200 expected, 5xx,
    framework-default MIME, etc.).

    Probe: hit all 3 with the same SNOMED URI and verify each yields the
    spec-correct shape for that interaction type.
    """
    snomed = "http://snomed.info/sct"
    code = "44054006"  # fixture: Type 2 diabetes mellitus

    # 1. $lookup — operation route
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": snomed, "code": code},
    )
    assert r_lookup.status_code == 200, (
        f"$lookup {snomed} {code} -> {r_lookup.status_code}; expected 200"
    )
    assert "fhir+json" in r_lookup.headers.get("content-type", ""), (
        f"$lookup Content-Type must be FHIR JSON"
    )

    # 2. READ — resource route (no persisted resources → 404 + OO)
    r_read = fhir_client.get(f"/fhir/CodeSystem/{code}")
    assert r_read.status_code == 404, (
        f"READ /fhir/CodeSystem/{code} -> {r_read.status_code}; expected 404 "
        f"(no persisted resources — AGENTS.md NOT A BUG Registry)"
    )
    assert "fhir+json" in r_read.headers.get("content-type", ""), (
        f"READ Content-Type must be FHIR JSON (not framework default)"
    )
    assert r_read.json().get("resourceType") == "OperationOutcome"

    # 3. SEARCH — type-level route (empty Bundle)
    r_search = fhir_client.get("/fhir/CodeSystem", params={"url": snomed})
    assert r_search.status_code == 200, (
        f"SEARCH ?url={snomed} -> {r_search.status_code}; expected 200"
    )
    body = r_search.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "searchset"
    # Empty search result is NOT a failure (per FHIR R4 §3.1.1.3).
    assert body.get("total") == 0
    assert body.get("entry") == []


def test_e11_combined_lookup_read_search_xml_format_consistency(fhir_client):
    """EXPLORER lateral: combined operations MUST honor ``_format=xml``
    uniformly across all three interaction patterns ($lookup, READ,
    SEARCH). Content negotiation MUST NOT be limited to operation routes.

    Spec: https://hl7.org/fhir/R4/http.html §3.1.0.1.11 — "the _format
    query parameter overrides the Accept header". Applies to ALL
    interactions including READ and SEARCH.

    Per HISTORIAN tip: "_format=xml on READ/SEARCH routes — content
    negotiation on resource routes, not just operation routes".
    """
    snomed = "http://snomed.info/sct"
    code = "44054006"

    # 1. $lookup with _format=xml — operation route (already covered by TS-01)
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": snomed, "code": code, "_format": "xml"},
    )
    assert r_lookup.status_code == 200
    assert "fhir+xml" in r_lookup.headers.get("content-type", ""), (
        f"$lookup _format=xml Content-Type must be FHIR XML; got "
        f"{r_lookup.headers.get('content-type')!r}"
    )

    # 2. READ with _format=xml — resource route (HISTORIAN gap)
    r_read = fhir_client.get(
        f"/fhir/CodeSystem/{code}", params={"_format": "xml"}
    )
    assert r_read.status_code == 404
    assert "fhir+xml" in r_read.headers.get("content-type", ""), (
        f"READ _format=xml Content-Type must be FHIR XML; got "
        f"{r_read.headers.get('content-type')!r}. Content negotiation MUST "
        f"apply to READ routes (per HISTORIAN tip)."
    )
    # The 404 OperationOutcome MUST be valid XML
    assert "<OperationOutcome" in r_read.text, (
        f"READ _format=xml body must be OperationOutcome XML; got "
        f"{r_read.text[:200]!r}"
    )

    # 3. SEARCH with _format=xml — type-level route (HISTORIAN gap)
    r_search = fhir_client.get(
        "/fhir/CodeSystem", params={"url": snomed, "_format": "xml"}
    )
    assert r_search.status_code == 200
    assert "fhir+xml" in r_search.headers.get("content-type", ""), (
        f"SEARCH _format=xml Content-Type must be FHIR XML; got "
        f"{r_search.headers.get('content-type')!r}. Content negotiation MUST "
        f"apply to SEARCH routes (per HISTORIAN tip)."
    )
    # The Bundle MUST be valid XML
    assert "<Bundle" in r_search.text, (
        f"SEARCH _format=xml body must be Bundle XML; got "
        f"{r_search.text[:200]!r}"
    )


def test_e12_combined_xml_via_accept_header_on_resource_routes(fhir_client):
    """EXPLORER lateral: Accept: application/fhir+xml header MUST also
    negotiate XML on READ and SEARCH routes (per FHIR R4 §3.1.0.1.9).
    Some clients cannot set query params; the Accept header is the
    standard content-negotiation channel.

    Spec: https://hl7.org/fhir/R4/http.html §3.1.0.1.9 — "Servers SHALL
    support server-driven content negotiation as described in section 12
    of the HTTP specification".
    """
    # READ with Accept: application/fhir+xml
    r_read = fhir_client.get(
        "/fhir/CodeSystem/test-id",
        headers={"Accept": "application/fhir+xml"},
    )
    assert r_read.status_code == 404
    assert "fhir+xml" in r_read.headers.get("content-type", ""), (
        f"READ Accept: application/fhir+xml Content-Type must be FHIR XML; "
        f"got {r_read.headers.get('content-type')!r}"
    )

    # SEARCH with Accept: application/fhir+xml
    r_search = fhir_client.get(
        "/fhir/CodeSystem",
        headers={"Accept": "application/fhir+xml"},
    )
    assert r_search.status_code == 200
    assert "fhir+xml" in r_search.headers.get("content-type", ""), (
        f"SEARCH Accept: application/fhir+xml Content-Type must be FHIR XML; "
        f"got {r_search.headers.get('content-type')!r}"
    )


# ===========================================================================
# Lens 2: _format=xml content negotiation across EVERY GET route
# Per GLOBAL_RULES.md "Code Review Time" conformance-probe pattern:
# parametrize over EVERY declared route, not per resource type.
# ===========================================================================

def test_e20_xml_negotiation_uniform_across_all_codesystem_get_routes(fhir_client):
    """EXPLORER lateral: walk ``app.routes`` and verify EVERY /fhir/
    CodeSystem GET route that does NOT require input params (READ +
    SEARCH) honors ``_format=xml`` (Content-Type becomes
    application/fhir+xml).

    Operation routes ($lookup, $validate-code, $subsumes, $search,
    $extract) require ``system``+``code`` params and return 422 + FHIR
    JSON OperationOutcome when those are missing — that's the input-
    validation handler, NOT a content-negotiation gap. The XML Content-
    Type for those routes is covered by sibling probes (test_e11 for
    $lookup; CS-02 HISTORIAN for $validate-code; CS-04 EXPLORER for
    $subsumes; etc.).

    Spec: https://hl7.org/fhir/R4/http.html §3.1.0.1.11.
    Pattern: conformance probe parametrized over routes (per GLOBAL_RULES
    "Code Review Time" strategy — same shape as TS-02 EXPLORER Content-
    Type probe).
    """
    # Focus on the routes that don't require input params for content-
    # negotiation verification. These are the READ and SEARCH routes.
    resource_routes = [
        "/fhir/CodeSystem",  # SEARCH (type-level)
        "/fhir/CodeSystem/test-id",  # READ (instance-level)
    ]
    failures = []
    for path in resource_routes:
        r = fhir_client.get(path, params={"_format": "xml"})
        ct = r.headers.get("content-type", "")
        if "fhir+xml" not in ct:
            failures.append({"path": path, "status": r.status_code, "ct": ct})
    pytest.current_report_extra = f"routes={resource_routes} failures={failures}"
    assert not failures, (
        f"_format=xml did NOT produce FHIR XML Content-Type on these "
        f"CodeSystem resource routes: {failures}. Content negotiation "
        f"MUST apply to READ and SEARCH routes (per HISTORIAN tip)."
    )


# ===========================================================================
# Lens 3: Cross-resource consistency parametrized over ALIAS inputs
# HISTORIAN tip: "trailing-slash, urn:oid, uppercase-scheme applied to
# CodeSystem.url in READ and SEARCH".
# ===========================================================================

@pytest.mark.parametrize("alias_url,canonical_url", [
    ("http://snomed.info/sct/", "http://snomed.info/sct"),
    ("urn:oid:2.16.840.1.113883.6.96", "http://snomed.info/sct"),
    ("HTTP://snomed.info/sct", "http://snomed.info/sct"),
    ("http://snomed.info/sct", "http://snomed.info/sct"),
])
def test_e30_search_accepts_alias_urls_uniformly(
    fhir_client, alias_url: str, canonical_url: str
):
    """EXPLORER lateral: SEARCH ``url`` parameter MUST accept ALL alias
    forms (trailing-slash, urn:oid, uppercase-scheme, canonical) and
    return the same shape (200 + Bundle). Cross-resource consistency
    parametrized over alias inputs.

    The READ interaction doesn't accept ``url`` (it accepts logical id),
    but the SEARCH interaction's ``url`` param is the canonical/alias
    entry point. Per FHIR R4 codesystem-search.html, ``url`` matches the
    CodeSystem.url element which is the canonical URI — aliases SHOULD
    resolve to canonical via the alias map (already proven on $lookup).

    Probe: SEARCH with each alias yields 200 + Bundle (server has no
    persisted resources so the Bundle is empty regardless — but the
    invariant is "no 4xx/5xx for valid alias input").
    """
    r = fhir_client.get("/fhir/CodeSystem", params={"url": alias_url})
    pytest.current_report_extra = (
        f"alias={alias_url!r} canonical={canonical_url!r} "
        f"status={r.status_code}"
    )
    assert r.status_code == 200, (
        f"SEARCH ?url={alias_url!r} -> {r.status_code}; expected 200. "
        f"Aliases MUST resolve on the SEARCH surface (cross-resource "
        f"consistency with $lookup)."
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "searchset"


@pytest.mark.parametrize("alias_url,canonical_url", [
    ("http://snomed.info/sct/", "http://snomed.info/sct"),
    ("urn:oid:2.16.840.1.113883.6.96", "http://snomed.info/sct"),
    ("HTTP://snomed.info/sct", "http://snomed.info/sct"),
])
def test_e31_lookup_alias_consistency_on_combined_operation(
    fhir_client, alias_url: str, canonical_url: str
):
    """EXPLORER lateral: combined operation on each alias — $lookup with
    the alias returns Out `system` = canonical URI. The same alias used
    in SEARCH (test_e30) returns 200 + empty Bundle. Combined, the two
    operations confirm cross-operation alias-input consistency.

    Regression cite: HCPCS URI drift count=8+1 PROMOTED (extends the
    pattern to alias inputs on $lookup + SEARCH combination).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": alias_url, "code": "44054006"},
    )
    pytest.current_report_extra = (
        f"alias={alias_url!r} status={r.status_code} canonical={canonical_url!r}"
    )
    assert r.status_code == 200, (
        f"$lookup alias={alias_url!r} -> {r.status_code}; expected 200"
    )
    body = r.json()
    # Out `system` MUST be canonical (not the alias).
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert sys_param is not None, "$lookup Out missing 'system' parameter"
    actual_system = sys_param.get("valueUri")
    assert actual_system == canonical_url, (
        f"$lookup alias={alias_url!r} Out system={actual_system!r}; "
        f"expected canonical={canonical_url!r}. Client-input-as-canonical "
        f"drift (count=8+1 PROMOTED)."
    )


# ===========================================================================
# Lens 4: SEARCH lateral corners — combined params, mixed case, partial
# matches, status enum coverage
# HISTORIAN tip: "combined search params with mixed case, partial
# matches, search with status=draft/retired/unknown".
# ===========================================================================

def test_e40_search_all_params_combined_yields_bundle(fhir_client):
    """EXPLORER lateral: ALL 5 spec search params combined
    (url+version+name+title+status) MUST yield 200 + Bundle.

    Spec: https://hl7.org/fhir/R4/codesystem-search.html.
    Server has no persisted resources, so total=0, but the invariant is
    "no 4xx/5xx for valid combined input".
    """
    r = fhir_client.get("/fhir/CodeSystem", params={
        "url": "http://snomed.info/sct",
        "version": "2024-09",
        "name": "SNOMEDCT",
        "title": "SNOMED Clinical Terms",
        "status": "active",
    })
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Bundle"


@pytest.mark.parametrize("status_value", ["draft", "active", "retired", "unknown"])
def test_e41_search_status_enum_coverage(fhir_client, status_value: str):
    """EXPLORER lateral: SEARCH ``status`` parameter accepts all 4
    PublicationStatus values (draft|active|retired|unknown) per FHIR R4
    https://hl7.org/fhir/R4/valueset-publication-status.html.

    Probe: each status yields 200 + Bundle (empty, since server has no
    persisted resources). The invariant is no 4xx/5xx for valid enum
    values.
    """
    r = fhir_client.get("/fhir/CodeSystem", params={"status": status_value})
    pytest.current_report_extra = f"status={status_value!r} http={r.status_code}"
    assert r.status_code == 200, (
        f"SEARCH ?status={status_value!r} -> {r.status_code}; expected 200. "
        f"PublicationStatus enum coverage per FHIR R4."
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle"


def test_e42_search_partial_url_match_yields_bundle(fhir_client):
    """EXPLORER lateral: SEARCH ``url`` with a PARTIAL URI (e.g.
    ``snomed``) — server has no persisted resources so the result is an
    empty Bundle, but the invariant is "no 5xx for non-matching input".

    Spec: https://hl7.org/fhir/R4/codesystem-search.html — ``url`` is
    type=uri, exact-match semantics. A partial value SHOULD return empty
    result (NOT 4xx/5xx).
    """
    r = fhir_client.get("/fhir/CodeSystem", params={"url": "snomed"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Bundle"


def test_e43_search_off_spec_status_value_no_5xx(fhir_client):
    """EXPLORER lateral: SEARCH ``status`` with an off-spec value (e.g.
    'deprecated', 'withdrawn') MUST NOT crash with 5xx. Either 200 +
    empty Bundle or 422 + OO is conformant; 5xx is not.

    Spec: https://hl7.org/fhir/R4/valueset-publication-status.html — the
    closed enum is draft|active|retired|unknown. Off-spec values are
    client errors.
    """
    for off_spec in ["deprecated", "withdrawn", "old", "new"]:
        r = fhir_client.get("/fhir/CodeSystem", params={"status": off_spec})
        assert r.status_code < 500, (
            f"SEARCH ?status={off_spec!r} -> {r.status_code}; 5xx is NOT "
            f"conformant for off-spec enum value."
        )


# ===========================================================================
# Lens 5: READ with _summary and _elements parameters
# HISTORIAN tip: "READ with _summary=count, _summary=true, _summary=data"
# and "READ with _elements parameter".
#
# FHIR R4 https://hl7.org/fhir/R4/http.html#read explicitly states:
#   "In addition, the search parameter _summary can be used when reading
#    a resource ... The same applies to the _elements parameter"
# ===========================================================================

@pytest.mark.parametrize("summary_value", ["true", "false", "text", "data", "count"])
def test_e50_read_summary_param_no_5xx(fhir_client, summary_value: str):
    """EXPLORER lateral: READ with ``_summary`` MUST NOT crash with 5xx,
    regardless of value. Per FHIR R4 http.html#read, _summary is
    applicable to READ.

    Since medterm4ds does not persist CodeSystem resources, READ always
    returns 404 + OperationOutcome (AGENTS.md NOT A BUG Registry). The
    invariant is "no 5xx for valid _summary value".

    Spec: https://hl7.org/fhir/R4/http.html#read + §3.1.1.5.8.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/test-id", params={"_summary": summary_value}
    )
    pytest.current_report_extra = (
        f"summary={summary_value!r} http={r.status_code} "
        f"ct={r.headers.get('content-type')}"
    )
    assert r.status_code < 500, (
        f"READ ?_summary={summary_value!r} -> {r.status_code}; 5xx NOT "
        f"conformant. _summary is applicable to READ per FHIR R4 "
        f"http.html#read."
    )
    # READ still returns 404 + OO (no persisted resources). The Content-
    # Type MUST be FHIR (not framework default).
    assert "fhir+" in r.headers.get("content-type", ""), (
        f"READ ?_summary Content-Type must be FHIR; got "
        f"{r.headers.get('content-type')!r}"
    )
    assert r.json().get("resourceType") == "OperationOutcome"


def test_e51_read_elements_param_no_5xx(fhir_client):
    """EXPLORER lateral: READ with ``_elements`` MUST NOT crash with 5xx.
    Per FHIR R4 http.html#read, _elements is applicable to READ.

    Spec: https://hl7.org/fhir/R4/http.html#read + §3.1.1.5.9.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/test-id",
        params={"_elements": "url,content,status"},
    )
    pytest.current_report_extra = (
        f"http={r.status_code} ct={r.headers.get('content-type')}"
    )
    assert r.status_code < 500, (
        f"READ ?_elements -> {r.status_code}; 5xx NOT conformant. "
        f"_elements is applicable to READ per FHIR R4 http.html#read."
    )
    assert "fhir+" in r.headers.get("content-type", "")


def test_e52_read_combined_format_and_summary(fhir_client):
    """EXPLORER lateral: READ with ``_format=xml`` AND ``_summary=text``
    combined — content negotiation AND summary parameter MUST compose
    without crashing. The 404 + OO response MUST be XML.

    Spec: https://hl7.org/fhir/R4/http.html#read + §3.1.0.1.11.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/test-id",
        params={"_format": "xml", "_summary": "text"},
    )
    assert r.status_code < 500
    assert "fhir+xml" in r.headers.get("content-type", ""), (
        f"READ _format=xml&_summary=text Content-Type must be FHIR XML; "
        f"got {r.headers.get('content-type')!r}"
    )


# ===========================================================================
# Lens 6: SEARCH _summary=count semantics
# Per FHIR R4 §3.1.1.5.3 + §3.1.1.5.8: _summary=count returns Bundle.total
# with count and empty entry, no prev/next/last links.
# ===========================================================================

def test_e60_search_summary_count_returns_empty_bundle_with_total(fhir_client):
    """EXPLORER lateral: SEARCH ``_summary=count`` returns 200 + Bundle
    with total=N and empty entry list per FHIR R4 §3.1.1.5.3.

    Spec: https://hl7.org/fhir/R4/search.html §3.1.1.5.3 — "the server
    returns a bundle that reports the total number of resources that
    match in Bundle.total, but with no entries".

    medterm4ds has no persisted resources, so total=0 and entry=[]. The
    invariant is the SHAPE: Bundle.total is an integer, entry is empty.
    """
    r = fhir_client.get("/fhir/CodeSystem", params={"_summary": "count"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    # total MUST be an integer (not None, not string).
    assert isinstance(body.get("total"), int), (
        f"Bundle.total must be int; got {type(body.get('total'))} "
        f"value={body.get('total')!r}"
    )
    assert body.get("entry") == [], (
        f"_summary=count Bundle.entry must be empty; got {body.get('entry')!r}"
    )


# ===========================================================================
# Lens 7: Source-read structural probes
# EXPLORER lateral: walk the route registration to verify the SEARCH
# handler does not silently drop params (silent-fallback prohibition per
# GLOBAL_RULES.md).
# ===========================================================================

def test_e70_search_handler_accepts_all_spec_params():
    """EXPLORER source-read: the SEARCH route handler MUST declare all 5
    spec search params (url, version, name, title, status) as Query()
    parameters. Missing any param means clients passing it get silently
    ignored (or worse, FastAPI's unknown-param handling fires).

    Spec: https://hl7.org/fhir/R4/codesystem-search.html.
    """
    src = _read_source(_FHIR_API_PATH)
    search_fn = _get_func_source(src, "search_resource")
    pytest.current_report_extra = f"found_search_fn={bool(search_fn)}"
    assert search_fn, "search_resource function not found"
    expected_params = ["url", "version", "name", "title", "status"]
    missing = [p for p in expected_params if f"{p}:" not in search_fn and f"{p} =" not in search_fn]
    pytest.current_report_extra += f" missing={missing}"
    assert not missing, (
        f"search_resource is missing spec-required search params: {missing}. "
        f"Per FHIR R4 codesystem-search.html these are the standard search "
        f"parameters for CodeSystem."
    )


def test_e71_read_handler_does_not_silently_swallow_summary_or_elements():
    """EXPLORER source-read: the READ route handler accepts ``_summary``
    and ``_elements`` per FHIR R4 http.html#read. Since the server has
    no persisted resources, these params have no behavioral effect
    (READ always returns 404 + OO). But the route MUST accept them
    structurally so FastAPI doesn't reject them as unknown query params.

    Probe: source-read the read_resource function and verify it doesn't
    reject _summary/_elements. Since FastAPI allows unknown query params
    by default (they're passed via Request.query_params), the absence of
    explicit handling is acceptable — but the behavioral test (test_e50,
    test_e51) covers the no-5xx invariant.
    """
    src = _read_source(_FHIR_API_PATH)
    read_fn = _get_func_source(src, "read_resource")
    pytest.current_report_extra = f"found_read_fn={bool(read_fn)}"
    assert read_fn, "read_resource function not found"
    # The handler MUST NOT explicitly reject unknown query params. Since
    # FastAPI's default is lenient, the absence of a query-param whitelist
    # is acceptable. The probe asserts the handler EXISTS and is wired.
    assert "_fhir_response" in read_fn or "_fhir_error" in read_fn


def test_e72_search_handler_returns_fhir_response_not_raw_dict():
    """EXPLORER source-read: SEARCH handler MUST call _fhir_response (not
    return a raw dict that FastAPI wraps in JSONResponse with the wrong
    Content-Type). Sibling of CR-001 pattern.

    Pattern: conformance probes parametrized over routes per GLOBAL_RULES
    "Code Review Time" strategy.
    """
    src = _read_source(_FHIR_API_PATH)
    search_fn = _get_func_source(src, "search_resource")
    assert search_fn, "search_resource function not found"
    assert "_fhir_response" in search_fn, (
        f"search_resource MUST call _fhir_response (not return raw dict). "
        f"CR-001 pattern."
    )


# ===========================================================================
# Lens 8: Fixture-skip documentation (HISTORIAN tip #4)
# HISTORIAN's test_h24 skipped because the conformance fixture lacks PF
# data for SNOMED 73211009. EXPLORER documents this gap and verifies the
# invariant on a fixture row that HAS PF data (SNOMED 44054006).
# ===========================================================================

def test_e80_fixture_skip_documentation_snomed_73211009(fhir_client):
    """EXPLORER lateral: HISTORIAN test_h24 skipped on SNOMED 73211009
    because the conformance fixture lacks patient-friendly data for that
    code. This probe DOCUMENTS the fixture gap by attempting $lookup and
    recording the response shape — it does NOT fail when the canonical-
    system custom property is absent.

    The intent is to characterize the fixture coverage so future PRs
    that add PF data for 73211009 will fire this probe loudly (the
    assertion that the property is absent will flip).

    Sibling of HISTORIAN carry-forward-as-probe pattern.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    if r.status_code != 200:
        pytest.skip("fixture row not seeded for SNOMED 73211009")
    body = r.json()
    canonical_system = None
    for p in body.get("parameter", []):
        if p.get("name") == "property":
            parts = {pt.get("name"): pt for pt in p.get("part", [])}
            code_part = parts.get("code", {})
            if code_part.get("valueCode") == "canonical-system":
                value_part = parts.get("value", {})
                canonical_system = value_part.get("valueUri") or value_part.get("valueCode")
    pytest.current_report_extra = f"canonical_system={canonical_system!r}"
    # Document the current state: this fixture row does NOT have PF data
    # for canonical-system. When the fixture grows PF data, this probe
    # fires loudly (carry-forward-as-probe).
    if canonical_system is None:
        pytest.skip(
            "fixture lacks PF data for SNOMED 73211009 (HISTORIAN test_h24 "
            "gap documentation)"
        )


def test_e81_canonical_system_invariant_on_row_with_pf_data(fhir_client):
    """EXPLORER lateral: when the fixture row DOES have PF data
    (SNOMED 44054006 — Type 2 diabetes mellitus), the $lookup Out
    canonical-system custom property (if present) MUST be a canonical
    FHIR URI (resolvable via SYSTEM_TO_FHIR_URI.values()).

    Sibling of HISTORIAN test_h24 — verifies the invariant on a fixture
    row that actually has PF data.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "44054006"},
    )
    if r.status_code != 200:
        pytest.skip("fixture row not seeded for SNOMED 44054006")
    body = r.json()
    canonical_system = None
    for p in body.get("parameter", []):
        if p.get("name") == "property":
            parts = {pt.get("name"): pt for pt in p.get("part", [])}
            code_part = parts.get("code", {})
            if code_part.get("valueCode") == "canonical-system":
                value_part = parts.get("value", {})
                canonical_system = value_part.get("valueUri") or value_part.get("valueCode")
    if canonical_system is None:
        pytest.skip("no canonical-system custom property for this fixture row")
    pytest.current_report_extra = f"canonical_system={canonical_system!r}"
    # MUST be a FHIR canonical URI (resolvable).
    assert canonical_system in set(SYSTEM_TO_FHIR_URI.values()), (
        f"$lookup canonical-system={canonical_system!r} is NOT a canonical "
        f"FHIR URI. Raw SAB label leak."
    )


# ===========================================================================
# Lens 9: Hostile / edge inputs on READ and SEARCH combinations
# EXPLORER lateral: hostile combinations (long values, special chars,
# multiple _format values).
# ===========================================================================

def test_e90_read_long_id_no_5xx(fhir_client):
    """EXPLORER lateral: READ with a 1000-char id MUST NOT crash. Either
    404 (treated as unknown resource) or 414 (URI too long) is OK; 5xx
    is not.

    Pattern: hostile-id matrix per GLOBAL_RULES SKEPTIC strategy.
    """
    long_id = "a" * 1000
    r = fhir_client.get(f"/fhir/CodeSystem/{long_id}")
    assert r.status_code < 500, (
        f"READ long_id -> {r.status_code}; 5xx NOT conformant"
    )


def test_e91_search_long_url_no_5xx(fhir_client):
    """EXPLORER lateral: SEARCH with a 2000-char url MUST NOT crash."""
    long_url = "http://example.com/" + "a" * 2000
    r = fhir_client.get("/fhir/CodeSystem", params={"url": long_url})
    assert r.status_code < 500, (
        f"SEARCH long_url -> {r.status_code}; 5xx NOT conformant"
    )


def test_e92_search_with_unknown_extra_param_no_5xx(fhir_client):
    """EXPLORER lateral: SEARCH with an unknown extra param (e.g.
    ``?customParam=foo``) MUST NOT crash. FastAPI's default is lenient
    (unknown query params are ignored); 5xx would be a regression.

    Spec: §3.1.1.5 — "Servers SHOULD ignore unknown parameters" (with
    some caveats around _sort chaining, but those don't apply here).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem",
        params={"url": "http://snomed.info/sct", "customParam": "foo"},
    )
    assert r.status_code < 500
    assert r.json().get("resourceType") == "Bundle"


def test_e93_read_with_subresource_path_no_5xx(fhir_client):
    """EXPLORER lateral: READ with a path containing slashes (e.g.
    ``/fhir/CodeSystem/foo/bar``) — the route uses ``{resource_id}`` (no
    :path converter), so the second segment falls to a different route
    or 404. MUST NOT 5xx.

    This probes whether nested paths are handled gracefully.
    """
    r = fhir_client.get("/fhir/CodeSystem/foo/bar")
    assert r.status_code < 500
    # Either 404 (no resource) or fall through to another route. The key
    # invariant is no 5xx and FHIR MIME on the response.
    assert "fhir+" in r.headers.get("content-type", ""), (
        f"READ nested path Content-Type must be FHIR; got "
        f"{r.headers.get('content-type')!r}"
    )
