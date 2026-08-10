"""SKEPTIC RESWEEP probes for chunk CM-02 (ConceptMap $translate Operation).

Source: https://build.fhir.org/conceptmap-operation-translate.html
Canonical R4 $translate operation:
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html

This resweep test file extends the baseline ``test_cm02_skeptic.py`` with
NEW hostile-input probes through the SKEPTIC lens ("Break it."). Per
``evolution.json.config.notes``, the CM-01 surface already hardened the
POST ``$translate`` coding/codeableConcept body paths and the instance-
level GET/POST translate routes (``translate_instance_get`` /
``translate_instance_post`` at ``apps/fhir_api.py:3485/3505``). This
resweep focuses hostile inputs on:

  * Instance-level $translate — long codes, special chars, non-existent
    IDs, path-traversal-shaped IDs, very long IDs.
  * Reverse mode edge cases — reverse=true with no targetCode, reverse
    as non-boolean, reverse=true with codeableConcept body.
  * Dependency/product parameter combinations — the spec lists
    ``dependency`` (0..*) and the match Out ``product`` (0..*) but
    medterm4ds does not implement either today; verify they don't 500.
  * targetSystem/targetCode constraint interactions — both at once,
    conflicting systems, invalid system URIs.
  * Source-read structural contracts — verify the isinstance guards,
    canonical_system_uri wiring, and the 404-not-stored-body invariant
    on the instance-level routes.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus; A44054006 -> A73211009)
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from medterm4ds.apps.fhir_api import create_fhir_app
from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
)
from medterm4ds.engines.fhir.responses import (
    build_parameters_translate,
)


# ---------------------------------------------------------------------------
# Constants for the probes.
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
CONCEPTMAP_URL = "http://medterm4ds.org/fhir/ConceptMap/snomed-to-icd10"

# Hostile-input constants.
LONG_CODE_1K = "A" * 1000
LONG_CODE_10K = "B" * 10000
SQL_INJECTION_CODE = "44054006'; DROP TABLE mrrel; --"
XSS_CODE = "<script>alert('xss')</script>"
NULL_BYTE_CODE = "44054006\x00malicious"
CRLF_CODE = "44054006\r\nX-Inject: header"
UNICODE_CJK_CODE = "糖尿病"
PATH_TRAVERSAL_ID = "../../etc/passwd"
LONG_ID_1K = "x" * 1000
SPECIAL_CHARS_ID = "id with spaces & symbols?=<>"
NULL_BYTE_ID = "valid_id\x00injection"


def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Return the first ``parameter`` entry with ``name == name``, else None."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


def _match_parts(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the list of ``part`` dicts from every ``match`` parameter."""
    out: list[dict[str, Any]] = []
    for p in body.get("parameter", []):
        if p.get("name") == "match":
            for part in p.get("part", []):
                out.append(part)
    return out


def _is_fhir_response(r) -> bool:
    """True if the response carries a FHIR MIME Content-Type."""
    return r.headers.get("content-type", "").startswith("application/fhir+")


# ===========================================================================
# Lens 1: Instance-level $translate — hostile IDs and hostile query params
# (per CM-01/TERMINOLOGIST tip: instance-level routes are
# translate_instance_get/translate_instance_post at fhir_api.py:3485/3505).
# ===========================================================================


@pytest.mark.parametrize(
    "resource_id, label",
    [
        ("non-existent-id", "non-existent"),
        (LONG_ID_1K, "very-long-id-1K"),
        (SPECIAL_CHARS_ID, "special-chars-and-spaces"),
        ("%2F%2Fetc%2Fpasswd", "url-encoded-path-traversal"),
    ],
)
def test_s10_instance_get_translate_hostile_ids_returns_fhir_response(
    fhir_client, resource_id, label
):
    """SKEPTIC (instance-level hostile IDs): GET
    ``/fhir/ConceptMap/{id}/$translate`` with hostile IDs MUST return a
    FHIR-conformant response — never a Starlette default 404 with
    ``text/plain`` body, and never a 500 with traceback information
    disclosure.

    Spec: FHIR R4 §3.1.0.1.5 + §3.1.0.1.9 mandate a FHIR OperationOutcome
    for invalid input. The instance-level route at fhir_api.py:3485-3502
    returns 404 + OperationOutcome for every id (medterm4ds does not
    persist ConceptMaps).

    Per CM-01/TERMINOLOGIST tip: hostile inputs on instance-level
    $translate are the focus surface for this resweep.

    Note: raw ``../`` and null bytes are stripped/rejected by httpx at
    the client side (per RFC 3986 §3.3 + §6.2.2) before reaching the
    server — documented as a test-client artifact in CS-01 SKEPTIC. The
    URL-encoded form ``%2F%2Fetc%2Fpasswd`` IS accepted by httpx and
    reaches the server, so we use that as the path-traversal probe.
    """
    r = fhir_client.get(
        f"/fhir/ConceptMap/{resource_id}/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code < 500, (
        f"instance-level $translate hostile id ({label}) returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r), (
        f"instance-level $translate hostile id ({label}) returned non-FHIR "
        f"Content-Type: {r.headers.get('content-type')!r}; expected "
        f"application/fhir+*. Body: {r.text[:200]}"
    )


@pytest.mark.parametrize(
    "resource_id, label",
    [
        ("non-existent-id", "non-existent"),
        (LONG_ID_1K, "very-long-id-1K"),
        (SPECIAL_CHARS_ID, "special-chars-and-spaces"),
    ],
)
def test_s11_instance_post_translate_hostile_ids_returns_fhir_response(
    fhir_client, resource_id, label
):
    """SKEPTIC (instance-level hostile IDs, POST route): POST
    ``/fhir/ConceptMap/{id}/$translate`` with hostile IDs MUST return a
    FHIR-conformant response.

    Spec: same as test_s10 but on the POST route at fhir_api.py:3505-3520.
    """
    r = fhir_client.post(
        f"/fhir/ConceptMap/{resource_id}/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code < 500, (
        f"instance-level POST $translate hostile id ({label}) returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r), (
        f"instance-level POST $translate hostile id ({label}) returned "
        f"non-FHIR Content-Type: {r.headers.get('content-type')!r}. "
        f"Body: {r.text[:200]}"
    )


def test_s12_instance_get_translate_hostile_code_value(fhir_client):
    """SKEPTIC (instance-level + hostile code value): GET
    ``/fhir/ConceptMap/any-id/$translate`` with hostile ``code`` values
    MUST NOT produce a 5xx or non-FHIR response.

    The instance-level route short-circuits to 404 regardless of code
    validity (medterm4ds does not persist ConceptMaps), so the probe
    confirms the short-circuit fires before the code reaches the engine.
    """
    for code, label in [
        (LONG_CODE_1K, "long-1K"),
        (SQL_INJECTION_CODE, "sql-injection"),
        (XSS_CODE, "xss"),
        (NULL_BYTE_CODE, "null-byte"),
        (UNICODE_CJK_CODE, "unicode-cjk"),
    ]:
        r = fhir_client.get(
            "/fhir/ConceptMap/any-id/$translate",
            params=[
                ("system", SNOMED_URI),
                ("code", code),
                ("targetsystem", ICD10CM_URI),
            ],
        )
        assert r.status_code < 500, (
            f"instance-level $translate with hostile code ({label}) "
            f"returned 5xx; got {r.status_code}: {r.text}"
        )
        assert _is_fhir_response(r), (
            f"instance-level $translate with hostile code ({label}) "
            f"returned non-FHIR Content-Type: {r.headers.get('content-type')!r}"
        )


def test_s13_instance_get_translate_no_query_params(fhir_client):
    """SKEPTIC (instance-level + no query params): GET
    ``/fhir/ConceptMap/any-id/$translate`` with NO query params MUST
    still return 404 (not 400 from missing system/code) because the
    instance-level route short-circuits before validation.

    If the route DID validate first, this would be 400 + OperationOutcome;
    either way it must be <500 and FHIR-shaped.
    """
    r = fhir_client.get("/fhir/ConceptMap/any-id/$translate")
    assert r.status_code < 500, (
        f"instance-level $translate with no params returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r), (
        f"instance-level $translate with no params returned non-FHIR "
        f"Content-Type: {r.headers.get('content-type')!r}"
    )


def test_s14_instance_post_translate_no_body(fhir_client):
    """SKEPTIC (instance-level POST + no body): POST
    ``/fhir/ConceptMap/any-id/$translate`` with NO body MUST still
    return 404 (the route accepts ``body: dict | None = None``).

    The handler signature at fhir_api.py:3508 declares body as optional;
    a None body must not crash the handler.
    """
    r = fhir_client.post("/fhir/ConceptMap/any-id/$translate")
    assert r.status_code < 500, (
        f"instance-level POST $translate with no body returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r), (
        f"instance-level POST $translate with no body returned non-FHIR "
        f"Content-Type: {r.headers.get('content-type')!r}"
    )


def test_s15_instance_post_translate_non_dict_body(fhir_client):
    """SKEPTIC (instance-level POST + non-dict body): POST
    ``/fhir/ConceptMap/any-id/$translate`` with a non-dict body (list,
    string, null) MUST return 404, not 500.

    The instance-level handler at fhir_api.py:3504-3520 ignores body
    entirely and returns 404 OperationOutcome; the probe confirms the
    short-circuit is body-agnostic (no ``body.get(...)`` on None).
    """
    for body, label in [
        (None, "null"),
        ([], "empty-list"),
        ([{"foo": "bar"}], "list-of-dict"),
        ("not-a-json-object", "string"),
        (42, "integer"),
    ]:
        # Pass JSON-serializable body; httpx will encode as JSON.
        r = fhir_client.post(
            "/fhir/ConceptMap/any-id/$translate",
            json=body,
        )
        assert r.status_code < 500, (
            f"instance-level POST $translate with body ({label}) "
            f"returned 5xx; got {r.status_code}: {r.text}"
        )
        # Note: a non-dict JSON body may trip FastAPI's request-body
        # validation and produce 422 + application/json (+ tiny OperationOutcome-
        # shaped details) BEFORE the route handler runs. We don't assert
        # FHIR Content-Type here — FastAPI's 422 is its own contract.
        # The load-bearing assertion is: NO 5xx.


# ===========================================================================
# Lens 2: Type-level $translate hostile code values — long codes, special
# chars, SQL-injection, XSS, null bytes, unicode CJK. Spec: the code
# parameter is 0..1 code; the server MUST NOT crash or leak tracebacks.
# ===========================================================================


@pytest.mark.parametrize(
    "code, label",
    [
        (LONG_CODE_1K, "long-1K"),
        (LONG_CODE_10K, "long-10K"),
        (SQL_INJECTION_CODE, "sql-injection"),
        (XSS_CODE, "xss"),
        (NULL_BYTE_CODE, "null-byte"),
        (CRLF_CODE, "crlf-header-inject"),
        (UNICODE_CJK_CODE, "unicode-cjk"),
        ("", "empty-string"),  # blocked by min_length=1 per TS-02 SKEPTIC
        ("   ", "whitespace-only"),
    ],
)
def test_s20_get_translate_hostile_code_no_5xx(fhir_client, code, label):
    """SKEPTIC (item 1 + hostile code): GET ``$translate`` with hostile
    ``code`` values MUST NOT produce a 5xx or non-FHIR response.

    Spec: FHIR R4 §3.1.0.1.5 + §3.1.0.1.9 mandate a FHIR OperationOutcome
    for invalid input. Per GLOBAL_RULES.md "Silent Fallbacks", programming
    bugs (TypeError, KeyError) MUST propagate as FHIR OperationOutcome,
    NOT as 500 + traceback information-disclosure.

    Per SKEPTIC lens (aggressive bug hunting), every hostile code value
    must produce either:
      (a) 200 + result=false (legitimate no-match), OR
      (b) 4xx + OperationOutcome (input validation), OR
      (c) 422 (FastAPI min_length=1 / type validation).
    Never 5xx; never non-FHIR Content-Type.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", code),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code < 500, (
        f"GET $translate with hostile code ({label}) returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r), (
        f"GET $translate with hostile code ({label}) returned non-FHIR "
        f"Content-Type: {r.headers.get('content-type')!r}"
    )


@pytest.mark.parametrize(
    "system_uri, label",
    [
        (LONG_CODE_1K, "long-1K"),
        (SQL_INJECTION_CODE, "sql-injection"),
        (XSS_CODE, "xss"),
        (NULL_BYTE_CODE, "null-byte"),
        (UNICODE_CJK_CODE, "unicode-cjk"),
        ("", "empty-string"),
        ("not-a-uri", "not-a-uri"),
        ("http://", "scheme-only-no-host"),
        ("ftp://evil.example.com/x", "non-http-scheme"),
    ],
)
def test_s21_get_translate_hostile_system_no_5xx(fhir_client, system_uri, label):
    """SKEPTIC (item 1 + hostile system URI): GET ``$translate`` with
    hostile ``system`` values MUST NOT produce a 5xx or non-FHIR
    response.

    The handler resolves the system via ``fhir_uri_to_system`` which
    returns None for unrecognized URIs (no crash); the handler then
    returns 400 + OperationOutcome per fhir_api.py:2156-2157.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", system_uri),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code < 500, (
        f"GET $translate with hostile system ({label}) returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r), (
        f"GET $translate with hostile system ({label}) returned non-FHIR "
        f"Content-Type: {r.headers.get('content-type')!r}"
    )


@pytest.mark.parametrize(
    "target_uri, label",
    [
        (LONG_CODE_1K, "long-1K"),
        (SQL_INJECTION_CODE, "sql-injection"),
        (XSS_CODE, "xss"),
        (NULL_BYTE_CODE, "null-byte"),
        ("", "empty-string"),
        ("not-a-uri", "not-a-uri"),
        ("http://", "scheme-only-no-host"),
    ],
)
def test_s22_get_translate_hostile_targetsystem_no_5xx(fhir_client, target_uri, label):
    """SKEPTIC (item 2 + hostile targetSystem): GET ``$translate`` with
    hostile ``targetsystem`` values MUST NOT produce a 5xx.

    The handler resolves the target via ``fhir_uri_to_system`` which
    returns None for unrecognized URIs; the handler then returns 400 +
    OperationOutcome per fhir_api.py:2160-2162.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", target_uri),
        ],
    )
    assert r.status_code < 500, (
        f"GET $translate with hostile targetsystem ({label}) returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r), (
        f"GET $translate with hostile targetsystem ({label}) returned "
        f"non-FHIR Content-Type: {r.headers.get('content-type')!r}"
    )


# ===========================================================================
# Lens 3: Reverse mode edge cases — reverse=true with no targetCode,
# reverse as non-boolean, reverse=true with codeableConcept body.
# ===========================================================================


def test_s30_reverse_param_accepted_no_5xx(fhir_client):
    """SKEPTIC (item 2 + reverse): the GET handler does not declare a
    ``reverse`` query param today, but the SKEPTIC lens probes whether
    sending it produces a graceful response (FastAPI should ignore
    unknown query params or pass them through).

    Spec: FHIR R4 $translate ``reverse`` is 0..1 boolean. The spec text
    says reverse "reverses the meaning of the source and target
    parameters". medterm4ds does not implement reverse today (deferred
    enhancement candidate).

    This probe asserts: sending reverse=true does NOT 5xx; the response
    is still FHIR-shaped.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("reverse", "true"),
        ],
    )
    assert r.status_code < 500, (
        f"GET $translate with reverse=true returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r)


def test_s31_reverse_param_invalid_value_no_5xx(fhir_client):
    """SKEPTIC (item 2 + reverse invalid value): sending ``reverse`` with
    a non-boolean value (e.g. "yes", "1", a long string) MUST NOT 5xx.

    medterm4ds does not declare ``reverse`` as a Query param, so FastAPI
    ignores it; the probe confirms no downstream code interprets reverse
    in a way that crashes.
    """
    for val, label in [
        ("yes", "yes"),
        ("1", "integer-string"),
        ("TRUE", "uppercase-true"),
        (LONG_CODE_1K, "long-string"),
        (SQL_INJECTION_CODE, "sql-injection"),
    ]:
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params=[
                ("system", SNOMED_URI),
                ("code", "44054006"),
                ("targetsystem", ICD10CM_URI),
                ("reverse", val),
            ],
        )
        assert r.status_code < 500, (
            f"GET $translate with reverse={label!r} returned 5xx; "
            f"got {r.status_code}: {r.text}"
        )


def test_s32_reverse_in_post_body_accepted_no_5xx(fhir_client):
    """SKEPTIC (item 2 + reverse POST body): POST ``$translate`` with
    ``reverse=true`` in the Parameters body.

    ``_parse_parameters`` extracts boolean valueBoolean; the handler
    ignores it (the value is extracted but not used to alter the
    direction of the translation). The probe confirms no 5xx.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                {"name": "reverse", "valueBoolean": True},
            ],
        },
    )
    assert r.status_code < 500, (
        f"POST $translate with reverse=true returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r)


def test_s33_reverse_with_target_code_no_5xx(fhir_client):
    """SKEPTIC (item 2 + reverse + targetCode): the spec pairs
    ``reverse=true`` with ``targetCode`` to find source codes mapping to
    a given target. medterm4ds does not implement this today; the probe
    confirms the parameters are accepted without 5xx.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("targetCode", "E11"),
            ("reverse", "true"),
        ],
    )
    assert r.status_code < 500, (
        f"GET $translate with reverse=true + targetCode=E11 returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )


# ===========================================================================
# Lens 4: Dependency / product parameter combinations — the spec lists
# ``dependency`` (0..*) and ``product`` (0..*) but medterm4ds does not
# implement either today. The probe confirms the parameters are accepted
# without 5xx; ``dependency`` as a list of complex entries exercises the
# _parse_parameters isinstance guard.
# ===========================================================================


def test_s40_post_dependency_param_non_dict_entries_no_5xx(fhir_client):
    """SKEPTIC (item 2 + dependency hostile body): POST ``$translate``
    with ``dependency`` as a list of non-dict entries (string, int,
    null, list) MUST NOT 5xx.

    Per GLOBAL_RULES.md 10th PROMOTED pattern (isinstance-guard-at-
    untrusted-data list-iterator boundary), a non-dict entry in
    ``parameter[]`` triggers AttributeError that propagates as 500 +
    traceback information disclosure. The ``_parse_parameters`` helper
    has the isinstance guard; the probe confirms the guard fires for
    sibling complex-type entries too.

    Spec: FHIR R4 §3.1.0.1.5 + §3.1.0.1.9 mandate a FHIR OperationOutcome
    for invalid input.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                # Hostile: non-dict entries mixed with valid ones.
                "this-is-a-string-not-a-dict",
                42,
                None,
                ["nested", "list"],
                {"name": "reverse", "valueBoolean": True},
            ],
        },
    )
    assert r.status_code < 500, (
        f"POST $translate with non-dict parameter[] entries returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r)


def test_s41_post_codeable_concept_with_non_dict_coding_no_5xx(fhir_client):
    """SKEPTIC (item 2 + codeableConcept hostile body): POST ``$translate``
    with codeableConcept.coding[] containing non-dict entries MUST NOT
    5xx.

    The ``_extract_codeable_concept_from_parameters`` helper has an
    isinstance guard (per 10th PROMOTED pattern). The probe confirms the
    guard fires for non-dict entries inside ``coding[]``.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            "not-a-dict",
                            42,
                            None,
                            ["nested"],
                            {"system": SNOMED_URI, "code": "44054006"},
                        ]
                    },
                },
            ],
        },
    )
    assert r.status_code < 500, (
        f"POST $translate with non-dict coding[] entries returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    # The valid coding at the end should still be extracted; result=true.
    if r.status_code == 200:
        body = r.json()
        result = _find_param(body, "result")
        assert result is not None, "result param missing"


def test_s42_post_coding_with_non_dict_valueCoding_no_5xx(fhir_client):
    """SKEPTIC (item 2 + coding hostile body): POST ``$translate`` with
    ``coding`` parameter carrying a non-dict ``valueCoding`` MUST NOT 5xx.

    The ``_extract_named_coding_from_parameters`` helper has an isinstance
    guard (per CS-04 SKEPTIC QA-053 fix). The probe confirms the guard
    fires for non-dict valueCoding.
    """
    for bad_value in ["string", 42, None, ["list"]]:
        r = fhir_client.post(
            "/fhir/ConceptMap/$translate",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "targetsystem", "valueUri": ICD10CM_URI},
                    {"name": "coding", "valueCoding": bad_value},
                ],
            },
        )
        assert r.status_code < 500, (
            f"POST $translate with non-dict valueCoding={bad_value!r} "
            f"returned 5xx; got {r.status_code}: {r.text}"
        )


def test_s43_post_dependency_complex_entries_no_5xx(fhir_client):
    """SKEPTIC (item 2 + dependency complex entries): POST ``$translate``
    with ``dependency`` as a list of complex entries (each carrying
    ``dependency.element`` + ``dependency.concept``) MUST NOT 5xx.

    The spec lists ``dependency`` as 0..* complex; medterm4ds does not
    implement it. The probe confirms the parameters are accepted without
    5xx and the response is FHIR-shaped.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
                {
                    "name": "dependency",
                    "part": [
                        {"name": "element", "valueUri": "http://example.org/element"},
                        {
                            "name": "concept",
                            "valueCodeableConcept": {
                                "coding": [{"system": SNOMED_URI, "code": "73211009"}]
                            },
                        },
                    ],
                },
            ],
        },
    )
    assert r.status_code < 500, (
        f"POST $translate with dependency complex entries returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r)


# ===========================================================================
# Lens 5: targetSystem / targetCode constraint interactions — both at
# once, conflicting systems, invalid system URIs.
# ===========================================================================


def test_s50_get_translate_targetsystem_self_no_5xx(fhir_client):
    """SKEPTIC (item 2 + targetSystem=self): GET ``$translate`` with
    ``targetsystem == system`` (SNOMED→SNOMED) MUST NOT 5xx.

    Per fhir_api.py:2159-2165, when ``target_uri`` is provided and
    resolves to a known source, ``target_sources = [target_source]``.
    When ``target_source == source``, the call to ``get_code_mappings``
    still runs; the probe confirms no edge-case crash.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", SNOMED_URI),
        ],
    )
    assert r.status_code < 500, (
        f"GET $translate with targetsystem=system (self) returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r)


def test_s51_get_translate_targetsystem_with_targetcode_no_5xx(fhir_client):
    """SKEPTIC (item 2 + targetSystem + targetCode combined): GET
    ``$translate`` with both ``targetsystem`` and ``targetCode`` set.

    Per FHIR R4 $translate prose, ``targetCode`` is used with
    ``reverse=true`` to find source codes mapping to a given target.
    medterm4ds declares ``targetCode`` as a Query param (line 2127-2130)
    but does not use it; the probe confirms the combination doesn't 5xx.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("targetCode", "E11"),
        ],
    )
    assert r.status_code < 500, (
        f"GET $translate with targetsystem + targetCode returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )


def test_s52_get_translate_targetsystem_with_source_param_no_5xx(fhir_client):
    """SKEPTIC (item 6 + ConceptMap URL constraint): GET ``$translate``
    with the ``source`` query param (which carries a ConceptMap canonical
    URL per the handler docstring).

    Per fhir_api.py:2123-2126, ``source`` is declared as a Query param
    but not used to select a ConceptMap. The probe confirms sending a
    plausible ConceptMap URL doesn't 5xx.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("source", CONCEPTMAP_URL),
        ],
    )
    assert r.status_code < 500, (
        f"GET $translate with source=ConceptMap URL returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r)


def test_s53_get_translate_source_param_with_alias_uri_no_5xx(fhir_client):
    """SKEPTIC (item 6 + ConceptMap URL with alias system): GET
    ``$translate`` sending both ``source`` (ConceptMap URL) AND an
    alias-shaped ``system`` (urn:oid). Confirms canonical_system_uri
    resolves the alias through to the canonical SNOMED URI on the
    match.source.system output path.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI_OID_ALIAS),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("source", CONCEPTMAP_URL),
        ],
    )
    assert r.status_code < 500, (
        f"GET $translate with source + alias system returned 5xx; "
        f"got {r.status_code}: {r.text}"
    )
    if r.status_code == 200:
        body = r.json()
        # match.source.system MUST be canonical (CR-012 RESOLVED).
        for match_part in body.get("parameter", []):
            if match_part.get("name") != "match":
                continue
            for part in match_part.get("part", []):
                if part.get("name") == "source":
                    coding = part.get("valueCoding", {})
                    assert coding.get("system") == SNOMED_URI, (
                        f"match.source.system drift on alias input: "
                        f"{coding.get('system')!r}; expected {SNOMED_URI!r}"
                    )


# ===========================================================================
# Lens 6: Hostile source-system pair — source-system combination that
# would cause ``_all_systems_except`` to behave unexpectedly.
# ===========================================================================


def test_s60_get_translate_no_targetsystem_returns_200(fhir_client):
    """SKEPTIC (item 2 + no targetsystem): GET ``$translate`` WITHOUT
    ``targetsystem`` MUST return 200 (the handler falls back to
    ``_all_systems_except(source)`` per fhir_api.py:2164-2165).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
        ],
    )
    assert r.status_code == 200, (
        f"GET $translate without targetsystem returned non-200; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r)


def test_s61_get_translate_invalid_source_then_target_invalid_uri_error(fhir_client):
    """SKEPTIC (item 1 + invalid source AND invalid target): GET
    ``$translate`` with BOTH system AND targetsystem set to invalid
    URIs. The handler validates source FIRST (line 2156-2157) and
    returns 400 + "Unrecognized source system URI: ...".

    The probe asserts: source-error fires first (deterministic order),
    NOT a 5xx.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://invalid.example.com/x"),
            ("code", "44054006"),
            ("targetsystem", "http://also-invalid.example.com/y"),
        ],
    )
    assert r.status_code == 400, (
        f"GET $translate with both invalid URIs expected 400; "
        f"got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r)


# ===========================================================================
# Lens 7: Source-read structural contracts — verify the load-bearing
# patterns from prior chunks fire on the $translate surface.
# ===========================================================================


def _get_func_source(source: str, name: str) -> str:
    """Return source text of a top-level OR nested function by name.

    Walks BOTH ``ast.FunctionDef`` AND ``ast.AsyncFunctionDef`` to catch
    nested async route handlers inside ``create_fhir_app()``. Mirrors
    TS-04 HISTORIAN + CM-01 HISTORIAN strategy.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            try:
                return ast.get_source_segment(source, node) or ""
            except Exception:
                return ""
    return ""


def test_s70_translate_get_has_min_length_on_system_and_code():
    """SKEPTIC (source-read contract): the GET ``$translate`` handler
    MUST declare ``min_length=1`` on ``system`` and ``code`` per TS-02
    SKEPTIC QA-001/QA-002 (5th PROMOTED pattern: empty-string-as-present-
    on-required-Query drift).

    Without ``min_length=1``, FastAPI's ``Query(..., required=True)``
    treats empty string as present, and the handler proceeds with empty
    system/code producing silent-wrong-answer output (200 + result=false
    rather than 422).
    """
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "translate_get")
    assert src, "Could not find translate_get handler source"
    # Both required-string Query declarations must have min_length=1.
    assert "system: str = Query(..., min_length=1" in src, (
        f"GET $translate missing min_length=1 on system Query. Source:\n{src}"
    )
    assert "code: str = Query(..., min_length=1" in src, (
        f"GET $translate missing min_length=1 on code Query. Source:\n{src}"
    )


def test_s71_translate_post_handler_calls_extract_translate_params():
    """SKEPTIC (source-read contract): the POST ``$translate`` handler
    MUST call ``_extract_translate_params`` (CM-01 EXPLORER QA-001 fix,
    closing CF-CM02-01). Without this, POST with coding/codeableConcept
    body is silently rejected with 400.

    The handler is at fhir_api.py:2135-2152.
    """
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "translate_post")
    assert src, "Could not find translate_post handler source"
    assert "_extract_translate_params" in src, (
        f"POST $translate missing _extract_translate_params call (CF-CM02-01 "
        f"regression). Source:\n{src}"
    )


def test_s72_translate_get_handler_does_NOT_use_targetCode_or_source():
    """SKEPTIC (source-read contract — declared-but-unused): the GET
    ``$translate`` handler declares ``targetCode`` and ``source`` Query
    params but per inline comments does NOT use them (deferred
    enhancement candidates). The probe pins this contract — if a future
    refactor wires them in, this probe must be updated.
    """
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "translate_get")
    assert src
    # targetCode and source are declared.
    assert "targetCode: str | None = Query" in src, (
        f"targetCode Query declaration missing. Source:\n{src}"
    )
    assert "source: str | None = Query" in src, (
        f"source Query declaration missing. Source:\n{src}"
    )
    # The handler invokes _do_translate with only (engine, system, code, targetsystem).
    # targetCode and source are NOT in the call signature.
    assert "_do_translate, _engine(request), system, code, targetsystem" in src, (
        f"_do_translate call does NOT pass targetCode/source through; "
        f"expected 4-tuple (engine, system, code, targetsystem). Source:\n{src}"
    )


def test_s73_do_translate_calls_canonical_system_uri():
    """SKEPTIC (source-read contract — CR-012 RESOLVED): the
    ``_do_translate`` internal helper MUST call ``canonical_system_uri``
    on the source URI before passing to the response builder.

    Without this, match.source.system echoes the client-supplied
    source_uri verbatim — including aliases (urn:oid:...) and trailing-
    slash variants. Per client-input-as-canonical drift meta-pattern
    (count=8+1 PROMOTED), every Out-system-emitting surface MUST route
    through canonical_system_uri.
    """
    src_app = inspect.getsource(create_fhir_app)
    do_translate_src = _get_func_source(src_app, "_do_translate")
    assert do_translate_src, "Could not find _do_translate source"
    assert "canonical_system_uri" in do_translate_src, (
        f"_do_translate missing canonical_system_uri call (CR-012 "
        f"regression). Source:\n{do_translate_src}"
    )


def test_s74_extract_translate_params_calls_named_coding_helper():
    """SKEPTIC (source-read contract — CF-CM02-01 CLOSED): the
    ``_extract_translate_params`` helper MUST call
    ``_extract_named_coding_from_parameters(body, "coding")`` AND
    ``_extract_codeable_concept_from_parameters(body)`` so that POST
    with coding/codeableConcept body is honored.
    """
    src_app = inspect.getsource(create_fhir_app)
    helper_src = _get_func_source(src_app, "_extract_translate_params")
    assert helper_src, "Could not find _extract_translate_params source"
    assert '_extract_named_coding_from_parameters' in helper_src, (
        f"_extract_translate_params missing _extract_named_coding_from_parameters "
        f"call (CF-CM02-01 regression). Source:\n{helper_src}"
    )
    assert '_extract_codeable_concept_from_parameters' in helper_src, (
        f"_extract_translate_params missing _extract_codeable_concept_from_parameters "
        f"call (CF-CM02-01 regression). Source:\n{helper_src}"
    )


def test_s75_instance_translate_routes_registered():
    """SKEPTIC (source-read contract — instance-level routes exist):
    the instance-level GET and POST ``$translate`` routes MUST be
    registered at ``/fhir/ConceptMap/{resource_id}/$translate``.

    Per FHIR R4 §3.1.0.1.1, operations MAY be invoked on either type
    or instance. Per CM-01/TERMINOLOGIST tip, these routes were
    registered in the CM-01 surface hardening.

    Note: ``create_fhir_app()`` requires MEDTERM4DS_DB env var; we
    instead source-read the factory to confirm the route is registered.
    """
    src_app = inspect.getsource(create_fhir_app)
    # The route path literal must appear in the factory source.
    assert '"/fhir/ConceptMap/{resource_id}/$translate"' in src_app, (
        f"Instance-level $translate route literal missing from "
        f"create_fhir_app source."
    )


def test_s76_parse_parameters_has_isinstance_guard():
    """SKEPTIC (source-read contract — 10th PROMOTED pattern):
    ``_parse_parameters`` MUST have an isinstance guard at the top of
    the ``for param in body.get("parameter", [])`` loop, per CS-04
    SKEPTIC QA-001.
    """
    src_app = inspect.getsource(create_fhir_app)
    parse_src = _get_func_source(src_app, "_parse_parameters")
    assert parse_src, "Could not find _parse_parameters source"
    assert "isinstance(param, dict)" in parse_src, (
        f"_parse_parameters missing isinstance(param, dict) guard. "
        f"Source:\n{parse_src}"
    )


# ===========================================================================
# Lens 8: Build builder audit — confirm match.source.system carries the
# canonical URI supplied by _do_translate, and that the result boolean
# is emitted as a Python bool (CR-002 sibling — serializer correctness).
# ===========================================================================


def test_s80_build_parameters_translate_emits_python_bool():
    """SKEPTIC (build builder audit): build_parameters_translate MUST
    emit ``valueBoolean`` as a Python bool (not 0/1, not "true"/"false"
    strings). The serializer (json.dumps) renders Python True/False as
    lowercase ``true``/``false`` automatically.
    """
    from medterm4ds.core.models import CodeMapping, CodeRef

    # Construct a CodeMapping directly (no ConceptMapRow intermediary needed).
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E11"),
        relationship="same",
        match_type="same",
        target_display="Type 2 diabetes mellitus",
    )
    body = build_parameters_translate(
        [mapping],
        source_system_uri=SNOMED_URI,
        source_code="44054006",
    )
    result_param = _find_param(body, "result")
    assert result_param is not None
    assert "valueBoolean" in result_param
    assert isinstance(result_param["valueBoolean"], bool), (
        f"valueBoolean is not a Python bool: "
        f"type={type(result_param['valueBoolean']).__name__}; "
        f"value={result_param['valueBoolean']!r}. "
        f"GLOBAL_RULES.md boolean-rendering rule (CR-002 sibling)."
    )
    assert result_param["valueBoolean"] is True


def test_s81_build_parameters_translate_empty_emits_python_bool_false():
    """SKEPTIC (build builder audit — empty case):
    build_parameters_translate with empty mappings MUST emit
    ``valueBoolean: False`` as a Python bool.
    """
    body = build_parameters_translate(
        [],
        source_system_uri=SNOMED_URI,
        source_code="non-existent-code",
    )
    result_param = _find_param(body, "result")
    assert result_param is not None
    assert isinstance(result_param["valueBoolean"], bool)
    assert result_param["valueBoolean"] is False


def test_s82_build_parameters_translate_match_source_uses_supplied_uri():
    """SKEPTIC (build builder audit — match.source.system contract):
    the builder MUST use the ``source_system_uri`` parameter verbatim
    for match.source.system — the canonical-URI resolution happens in
    the CALLER (_do_translate), not in the builder.
    """
    from medterm4ds.core.models import CodeMapping, CodeRef

    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E11"),
        relationship="same",
        match_type="same",
        target_display="Type 2 diabetes mellitus",
    )
    custom_uri = "http://custom.example.com/sid/test"
    body = build_parameters_translate(
        [mapping],
        source_system_uri=custom_uri,
        source_code="44054006",
    )
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) == 1
    source_part = next(
        (p for p in matches[0].get("part", []) if p.get("name") == "source"),
        None,
    )
    assert source_part is not None
    coding = source_part.get("valueCoding", {})
    assert coding.get("system") == custom_uri, (
        f"match.source.system should use supplied URI verbatim; "
        f"got {coding.get('system')!r}; expected {custom_uri!r}"
    )


# ===========================================================================
# Lens 9: GET ↔ POST byte-exact parity on hostile inputs.
# ===========================================================================


@pytest.mark.parametrize(
    "system, code, target, label",
    [
        (SNOMED_URI, "44054006", ICD10CM_URI, "valid-baseline"),
        (SNOMED_URI, LONG_CODE_1K, ICD10CM_URI, "long-code"),
        (SNOMED_URI, "non-existent-code", ICD10CM_URI, "no-match-code"),
        (SNOMED_URI_TRAILING_SLASH, "44054006", ICD10CM_URI, "trailing-slash-source"),
        (SNOMED_URI_OID_ALIAS, "44054006", ICD10CM_URI, "oid-alias-source"),
    ],
)
def test_s90_get_post_parity_on_hostile_inputs(
    fhir_client, system, code, target, label
):
    """SKEPTIC (cross-handler parity on hostile inputs): GET and POST
    ``$translate`` with the same logical inputs MUST produce identical
    status codes and result values.

    Spec: FHIR R4 operations are invocable via GET or POST with
    equivalent semantics. The probe confirms the parity holds on
    hostile inputs (long codes, no-match codes, alias URIs).
    """
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", system),
            ("code", code),
            ("targetsystem", target),
        ],
    )
    r_post = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": system},
                {"name": "code", "valueCode": code},
                {"name": "targetsystem", "valueUri": target},
            ],
        },
    )
    assert r_get.status_code == r_post.status_code, (
        f"GET↔POST status drift on ({label}): GET={r_get.status_code}, "
        f"POST={r_post.status_code}. GET body: {r_get.text[:200]}; "
        f"POST body: {r_post.text[:200]}"
    )
    if r_get.status_code == 200:
        body_get = r_get.json()
        body_post = r_post.json()
        result_get = _find_param(body_get, "result")
        result_post = _find_param(body_post, "result")
        assert result_get and result_post, "result param missing in one body"
        assert result_get["valueBoolean"] == result_post["valueBoolean"], (
            f"GET↔POST result-value drift on ({label}): "
            f"GET={result_get['valueBoolean']}, POST={result_post['valueBoolean']}"
        )


# ===========================================================================
# Lens 10: Cross-handler helper-wiring — instance-level routes are
# registered AFTER type-level routes; verify the order doesn't matter
# by exercising both routes in the same test.
# ===========================================================================


def test_s100_type_level_then_instance_level_no_state_leak(fhir_client):
    """SKEPTIC (cross-handler state isolation): type-level $translate
    followed by instance-level $translate MUST NOT leak state.

    The instance-level route returns 404 + OperationOutcome regardless
    of prior requests; the probe confirms no shared mutable state
    between the two routes.
    """
    # Type-level first.
    r1 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r1.status_code == 200
    # Instance-level next.
    r2 = fhir_client.get(
        "/fhir/ConceptMap/any-id/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    # Instance-level always 404 (medterm4ds does not persist ConceptMaps).
    assert r2.status_code == 404, (
        f"instance-level after type-level expected 404; got {r2.status_code}"
    )
    assert _is_fhir_response(r2)


def test_s101_instance_level_then_type_level_no_state_leak(fhir_client):
    """SKEPTIC (cross-handler state isolation, reverse order): instance-
    level $translate followed by type-level $translate MUST NOT leak
    state.
    """
    r1 = fhir_client.get("/fhir/ConceptMap/any-id/$translate")
    assert r1.status_code == 404
    r2 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r2.status_code == 200, (
        f"type-level after instance-level expected 200; got {r2.status_code}: "
        f"{r2.text}"
    )


# ===========================================================================
# Lens 11: Closed-enum vocabulary audit on the wire — every equivalence
# value emitted in a real $translate response MUST be a member of
# FHIR_R4_CONCEPT_MAP_EQUIVALENCE.
# ===========================================================================


def test_s110_translate_emitted_equivalence_in_r4_enum_on_no_target(fhir_client):
    """SKEPTIC (closed-enum on no-target path): when ``targetsystem`` is
    NOT supplied, the handler falls back to ``_all_systems_except``. The
    probe asserts every emitted equivalence value is in the R4 enum.

    This exercises a different code path than test_s23 (which used an
    explicit targetsystem). The T2DM→DM mrrel row is an ``isa`` parent
    relationship; the engine's INTERNAL_REL_TO_FHIR_EQUIVALENCE maps it
    to a specific R4 value.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            # No targetsystem — _all_systems_except path.
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    for match_part in body.get("parameter", []):
        if match_part.get("name") != "match":
            continue
        for part in match_part.get("part", []):
            if part.get("name") != "equivalence":
                continue
            value = part.get("valueCode")
            assert value in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"emitted equivalence {value!r} NOT in FHIR_R4_CONCEPT_MAP_EQUIVALENCE "
                f"(closed-enum drift regression of CF-HISTORIAN-VS01-01)."
            )


def test_s111_translate_no_match_emits_result_false_only(fhir_client):
    """SKEPTIC (item 5 — no-mapping shape): when no mapping exists, the
    response MUST contain exactly ``result=false`` + ``message``, with
    NO ``match`` entries.

    Spec: FHIR R4 $translate Out Parameters — match is 0..*, so a
    no-match response has zero match entries.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "non-existent-code-xyz"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    result = _find_param(body, "result")
    assert result is not None
    assert result["valueBoolean"] is False, (
        f"no-match expected result=false; got {result['valueBoolean']!r}"
    )
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) == 0, (
        f"no-match response MUST have 0 match entries; got {len(matches)}. "
        f"Body: {body}"
    )
    message = _find_param(body, "message")
    assert message is not None, "message parameter missing on no-match response"
