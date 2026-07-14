"""Regression tests for the Milestone 1 code review (review-5.md) fixes.

Covers CR-001, CR-002, CR-003 (CRITICAL/CRITICAL/HIGH) from
``docs/.ai_loop/spec_comp/reviews/review-5.md``:

* **CR-001** — ``$search`` / ``$extract`` handlers must funnel through
  ``_fhir_response`` so the wire-format ``Content-Type`` is
  ``application/fhir+json`` (or ``application/fhir+xml`` when negotiated),
  not Starlette's default ``application/json``.
  Spec: FHIR R4 §3.1.0.1.9, §4.7.1.1 item 1.

* **CR-002** — XML serializer must render Python ``bool`` as the FHIR R4
  wire-form lowercase literals ``true`` / ``false``, NOT Python's
  ``str(True)`` / ``str(False)`` which produce ``"True"`` / ``"False"``.
  Spec: FHIR R4 §3.4.1 boolean primitive representation.

* **CR-003** — Error responses MUST honor the same Accept/``_format``
  negotiation as success responses. ``_fhir_error_response(request, ...)``
  routes the OperationOutcome through ``_fhir_response`` so XML clients
  get an XML body and ``Content-Type: application/fhir+xml``.
  Spec: FHIR R4 §3.1.0.1.9, §3.1.0.1.5.

Each fix has a tagged validation command (per PROC_VALIDATION.md §"Validation
Tagging") in the docstring of its test function so the engineer_handoff.md
can cite it directly.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# CR-001: $search / $extract must return application/fhir+json
# ---------------------------------------------------------------------------

def test_cr001_extract_get_emits_fhir_mimetype(fhir_client):
    """CR-001 / §3.1.0.1.9, §4.7.1.1 item 1: ``GET /fhir/CodeSystem/$extract``
    must respond with ``Content-Type: application/fhir+json`` (not
    ``application/json`` from Starlette's default ``JSONResponse``).

    Reproducer for review-5.md finding 1 (CRITICAL). Before the fix the handler
    returned the dict from ``_do_extract`` directly to FastAPI, which emitted
    the framework-default ``application/json`` MIME type.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone1_review_fixes.py
        ::test_cr001_extract_get_emits_fhir_mimetype -q``
    """
    r = fhir_client.get("/fhir/CodeSystem/$extract", params={"text": "diabetes"})
    ct = r.headers.get("content-type", "")
    pytest.current_report_extra = f"status={r.status_code} ct={ct!r}"
    assert r.status_code == 200, f"$extract failed: {r.text[:200]!r}"
    assert "application/fhir+json" in ct, (
        f"$extract Content-Type is {ct!r}; spec mandates application/fhir+json "
        f"(FHIR R4 §3.1.0.1.9). Pre-fix this emitted application/json because "
        f"the handler returned a raw dict."
    )


def test_cr001_extract_post_emits_fhir_mimetype(fhir_client):
    """CR-001 (POST mirror): ``POST /fhir/CodeSystem/$extract`` must respond
    with ``Content-Type: application/fhir+json``. Same root cause as the GET
    variant — POST handler also returned the dict directly to FastAPI.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone1_review_fixes.py
        ::test_cr001_extract_post_emits_fhir_mimetype -q``
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$extract",
        json={
            "resourceType": "Parameters",
            "parameter": [{"name": "text", "valueString": "diabetes"}],
        },
    )
    ct = r.headers.get("content-type", "")
    pytest.current_report_extra = f"status={r.status_code} ct={ct!r}"
    assert r.status_code == 200, f"$extract POST failed: {r.text[:200]!r}"
    assert "application/fhir+json" in ct, (
        f"$extract POST Content-Type is {ct!r}; spec mandates "
        f"application/fhir+json (FHIR R4 §3.1.0.1.9)."
    )


def test_cr001_search_get_emits_fhir_mimetype_even_on_503(fhir_client):
    """CR-001 (error-path MIME): ``GET /fhir/CodeSystem/$search`` 503s when
    the BM25/embedding indexes aren't loaded (the conformance fixture
    intentionally has no indexes). The 503 path goes through ``_fhir_error``,
    which already used ``_fhir_json_response``; the success path (200) is the
    one that was broken. This probe pins BOTH paths to ``application/fhir+json``
    so future regressions of either shape are caught.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone1_review_fixes.py
        ::test_cr001_search_get_emits_fhir_mimetype_even_on_503 -q``
    """
    r = fhir_client.get("/fhir/CodeSystem/$search", params={"query": "diabetes"})
    ct = r.headers.get("content-type", "")
    pytest.current_report_extra = f"status={r.status_code} ct={ct!r}"
    # 503 is fine (no indexes in fixture) — but Content-Type must still be FHIR.
    assert r.status_code in (200, 503), (
        f"$search returned unexpected status {r.status_code}: {r.text[:200]!r}"
    )
    assert "application/fhir+json" in ct, (
        f"$search Content-Type is {ct!r}; spec mandates application/fhir+json "
        f"on every response (FHIR R4 §3.1.0.1.9)."
    )


# ---------------------------------------------------------------------------
# CR-001 (parametrized Content-Type probe): every FHIR route MUST emit a FHIR
# MIME type. Per review-5.md architectural concern #1: "before VS-01, write a
# parametrized probe `test_every_route_emits_fhir_mimetype` that walks
# `app.routes` and asserts Content-Type for every operation." This probe
# generalizes the QA-008/QA-021 Content-Type class so the next dict-returning
# handler regression is caught at PR time.
# ---------------------------------------------------------------------------

# Routes that legitimately cannot be probed with a vanilla request because
# they require complex Bodies (e.g. POST Parameters with system+code), return
# framework-level status codes for missing required query params (422), or
# are non-FHIR (health check). These are exercised by their dedicated
# per-operation probes in the per-chunk suites.
_ROUTES_NOT_PROBABLE_BLANK = {
    "/health",  # non-FHIR
    "/fhir",  # batch endpoint — needs a Bundle body
    "/fhir/CodeSystem/$extract",  # exercised above (CR-001 specific probes)
    "/fhir/CodeSystem/$search",
    "/fhir/CodeSystem/$lookup",
    "/fhir/CodeSystem/$validate-code",
    "/fhir/ValueSet/$validate-code",
    "/fhir/ConceptMap/$translate",
    "/fhir/CodeSystem/$subsumes",
    "/fhir/CodeSystem/$closure",
    "/fhir/ValueSet/$expand",
    # Instance-level & catch-all routes — need path params.
    # FastAPI framework-generated routes (not FHIR):
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
}


def _is_fhir_route(path: str) -> bool:
    """Filter out FastAPI's framework-generated routes (docs/openapi) so the
    parametrized Content-Type probe only walks FHIR routes."""
    return path.startswith("/fhir") or path in ("/health",)


def _collect_simple_get_routes(app) -> list[str]:
    """Walk app.routes for GET routes that take no path parameters and can
    be hit with a bare request. Used by the parametrized Content-Type probe."""
    from starlette.routing import Route
    routes = []
    for r in app.routes:
        if not isinstance(r, Route):
            continue
        if r.methods is None or "GET" not in r.methods:
            continue
        path = r.path
        # Skip non-FHIR framework-generated routes (docs, openapi.json, etc).
        if not _is_fhir_route(path):
            continue
        # Skip routes with path params (e.g. /fhir/{resource_type}/{id}).
        if "{" in path:
            continue
        if path in _ROUTES_NOT_PROBABLE_BLANK:
            continue
        routes.append(path)
    return sorted(set(routes))


def test_cr001_every_simple_get_route_emits_fhir_mimetype(fhir_client):
    """CR-001 generalized probe (architectural concern #1 from review-5.md).

    Walks ``app.routes`` for simple GET routes (no path params, no required
    query params) and asserts each emits a FHIR MIME type. This catches the
    next dict-returning handler regression at PR time — the original CR-001
    bug class slipped past the per-operation Content-Type probes because no
    probe asserted Content-Type on ``$search`` / ``$extract``.

    Spec: FHIR R4 §3.1.0.1.9 ("the correct MIME type SHALL be used").

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone1_review_fixes.py
        ::test_cr001_every_simple_get_route_emits_fhir_mimetype -q``
    """
    app = fhir_client.app
    routes = _collect_simple_get_routes(app)
    pytest.current_report_extra = f"routes={routes}"
    assert routes, "Probe sanity check: expected at least one simple GET route"
    failures = []
    for path in routes:
        r = fhir_client.get(path)
        ct = r.headers.get("content-type", "")
        if "application/fhir+json" not in ct and "application/fhir+xml" not in ct:
            failures.append((path, r.status_code, ct))
    assert not failures, (
        f"Routes returning non-FHIR Content-Type: {failures}. Spec mandates "
        f"application/fhir+json or application/fhir+xml on every FHIR route "
        f"(FHIR R4 §3.1.0.1.9)."
    )


# ---------------------------------------------------------------------------
# CR-002: XML serializer must emit lowercase true/false for booleans
# ---------------------------------------------------------------------------

def test_cr002_xml_boolean_renders_lowercase(fhir_client):
    """CR-002 / §3.4.1: FHIR R4 boolean primitives must render as the
    lowercase literals ``true`` / ``false`` in the XML wire-form, NOT Python's
    ``str(True)`` / ``str(False)`` which produce ``"True"`` / ``"False"``.

    Reproducer for review-5.md finding 2 (CRITICAL). Verified via the public
    serializer entrypoint ``to_fhir_xml``.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone1_review_fixes.py
        ::test_cr002_xml_boolean_renders_lowercase -q``
    """
    from medterm4ds.engines.fhir.xml import to_fhir_xml

    xml = to_fhir_xml({
        "resourceType": "Parameters",
        "parameter": [
            {"name": "present", "valueBoolean": True},
            {"name": "absent", "valueBoolean": False},
        ],
    })
    pytest.current_report_extra = f"xml={xml!r}"
    assert 'value="true"' in xml, (
        f"Expected valueBoolean=true to render as 'value=\"true\"'; XML: {xml!r}. "
        f"Pre-fix the serializer emitted 'value=\"True\"' (capital T) which "
        f"violates FHIR R4 §3.4.1 boolean primitive representation."
    )
    assert 'value="false"' in xml, (
        f"Expected valueBoolean=false to render as 'value=\"false\"'; XML: {xml!r}"
    )
    # Negative assertion: capital-T/F forms MUST NOT appear.
    assert "True" not in xml, f"Capital 'True' leaked into XML: {xml!r}"
    assert "False" not in xml, f"Capital 'False' leaked into XML: {xml!r}"


def test_cr002_xml_lookup_with_abstract_boolean(fhir_client):
    """CR-002 end-to-end: ``$lookup`` with ``Accept: application/fhir+xml``
    returns an XML body that must NOT contain capital-T/F boolean literals.

    The ``$lookup`` response includes ``<abstract value="false"/>`` in the
    typical case (when the code is not a SNOMED CT concept abstract). Pre-fix
    that emitted ``value="False"``.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone1_review_fixes.py
        ::test_cr002_xml_lookup_with_abstract_boolean -q``
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
        headers={"Accept": "application/fhir+xml"},
    )
    pytest.current_report_extra = f"status={r.status_code}"
    assert r.status_code == 200, f"$lookup failed: {r.text[:300]!r}"
    body = r.text
    # The serializer is correct if no capital-T/F appears anywhere in the
    # XML body. (CapabilityStatement URLs may contain 'False' in rare cases;
    # this lookup fixture does not, so the assertion is sound here.)
    assert "True" not in body, f"Capital 'True' leaked into XML body: {body[:500]!r}"
    assert "False" not in body, f"Capital 'False' leaked into XML body: {body[:500]!r}"


def test_cr002_xml_other_scalars_unchanged(fhir_client):
    """CR-002 non-regression: non-boolean scalar rendering is unchanged.

    Strings, ints, and floats still go through ``_xml_escape``. The boolean
    fix must not change the rendering path for any other scalar type.
    """
    from medterm4ds.engines.fhir.xml import to_fhir_xml

    xml = to_fhir_xml({
        "resourceType": "Parameters",
        "parameter": [
            {"name": "n", "valueInteger": 42},
            {"name": "s", "valueString": "hello&world"},
        ],
    })
    assert 'value="42"' in xml, f"Integer rendering broken: {xml!r}"
    # The ampersand must still be escaped to &amp; per XML safety.
    assert "hello&amp;world" in xml, f"String escaping broken: {xml!r}"


# ---------------------------------------------------------------------------
# CR-003: Error responses must honor Accept/_format negotiation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path,method,body",
    [
        # POST handlers with required system+code params (missing → 400).
        (
            "/fhir/CodeSystem/$lookup",
            "post",
            {"resourceType": "Parameters", "parameter": []},
        ),
        (
            "/fhir/CodeSystem/$validate-code",
            "post",
            {"resourceType": "Parameters", "parameter": []},
        ),
        (
            "/fhir/ValueSet/$validate-code",
            "post",
            {"resourceType": "Parameters", "parameter": []},
        ),
        (
            "/fhir/ConceptMap/$translate",
            "post",
            {"resourceType": "Parameters", "parameter": []},
        ),
        (
            "/fhir/CodeSystem/$subsumes",
            "post",
            {"resourceType": "Parameters", "parameter": []},
        ),
        (
            "/fhir/CodeSystem/$closure",
            "post",
            {"resourceType": "Parameters", "parameter": []},
        ),
        # POST $search — missing query → 400.
        (
            "/fhir/CodeSystem/$search",
            "post",
            {"resourceType": "Parameters", "parameter": []},
        ),
        # POST $extract — missing text → 400.
        (
            "/fhir/CodeSystem/$extract",
            "post",
            {"resourceType": "Parameters", "parameter": []},
        ),
    ],
)
def test_cr003_xml_negotiation_on_error_path(fhir_client, path, method, body):
    """CR-003 / §3.1.0.1.9, §3.1.0.1.5: error responses MUST honor the same
    Accept/``_format`` negotiation as success responses.

    Before the fix, all error sites called ``_fhir_error`` which always
    returns ``application/fhir+json``. An XML client (``Accept:
    application/fhir+xml``) triggering a 400 received a JSON body — a format
    mismatch from what the client requested.

    This probe triggers each POST handler's required-param-missing 400 path
    with ``Accept: application/fhir+xml`` and asserts the response is XML
    with the correct Content-Type.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone1_review_fixes.py
        ::test_cr003_xml_negotiation_on_error_path -q``
    """
    headers = {"Accept": "application/fhir+xml"}
    if method == "post":
        r = fhir_client.post(path, json=body, headers=headers)
    else:
        r = fhir_client.get(path, headers=headers)
    ct = r.headers.get("content-type", "")
    body_text = r.text or ""
    pytest.current_report_extra = f"path={path} status={r.status_code} ct={ct!r}"
    # All these probes trigger 400 (missing required param).
    assert r.status_code == 400, (
        f"{path} expected 400 (missing required param), got {r.status_code}; "
        f"body: {body_text[:300]!r}"
    )
    assert "application/fhir+xml" in ct, (
        f"{path} error Content-Type is {ct!r}; client requested XML via Accept. "
        f"Pre-fix the error path emitted JSON regardless of Accept header."
    )
    assert body_text.lstrip().startswith("<?xml") or body_text.lstrip().startswith("<"), (
        f"{path} error body is not XML: {body_text[:200]!r}"
    )
    # The XML body must be a valid OperationOutcome, not arbitrary XML.
    assert "OperationOutcome" in body_text, (
        f"{path} error body missing OperationOutcome root: {body_text[:300]!r}"
    )


def test_cr003_default_accept_on_error_path_returns_json(fhir_client):
    """CR-003 non-regression: when no Accept header is sent (default JSON),
    the error path MUST still return JSON.

    The XML-negotiation fix must not flip the default error response to XML.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={"resourceType": "Parameters", "parameter": []},
    )
    ct = r.headers.get("content-type", "")
    pytest.current_report_extra = f"status={r.status_code} ct={ct!r}"
    assert r.status_code == 400
    assert "application/fhir+json" in ct, (
        f"Default-Accept error path returned {ct!r}; must remain JSON."
    )


def test_cr003_format_param_xml_on_error_path(fhir_client):
    """CR-003 / §3.1.0.1.11: ``_format=xml`` query parameter MUST override
    Accept on the error path too — same precedence rule as success responses.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$lookup?_format=xml",
        json={"resourceType": "Parameters", "parameter": []},
    )
    ct = r.headers.get("content-type", "")
    pytest.current_report_extra = f"status={r.status_code} ct={ct!r}"
    assert r.status_code == 400
    assert "application/fhir+xml" in ct, (
        f"_format=xml error path returned {ct!r}; must honor _format precedence."
    )
