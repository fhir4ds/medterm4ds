"""Regression tests for the Milestone 2 code review (review-10.md) fixes.

Covers CR-005, CR-011, CR-012, CR-013, CR-014, CR-019, CF-HISTORIAN-VS01-01
from ``docs/.ai_loop/spec_comp/reviews/review-10.md``.

Each fix has a tagged validation command (per PROC_VALIDATION.md §"Validation
Tagging") in the docstring of its test function so the engineer_handoff.md
can cite it directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


# =============================================================================
# CR-005: _check_ready must return-don't-raise (Response is not BaseException)
# =============================================================================

def test_cr005_check_ready_returns_response_not_raises():
    """CR-005 (milestone-1 review, still open at milestone-2): ``_check_ready``
    used to ``raise _fhir_error(503, ...)`` — but ``_fhir_error`` returns a
    Starlette ``Response`` which is NOT a ``BaseException``. The ``raise``
    was a latent TypeError that fired on any in-flight request during
    shutdown/startup transient states, producing a Python traceback body
    with ``text/plain`` Content-Type — exactly the failure shape the
    surrounding defensive code was meant to prevent.

    Fix shape: ``_check_ready`` now returns ``Response | None``; callers
    check ``if not_ready is not None: return not_ready``. This probe
    verifies the source-code contract via AST reading.

    Spec: FHIR R4 §3.1.0.1.5 (OperationOutcome on 4xx/5xx) + §3.1.0.1.9
    (correct MIME type).

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr005_check_ready_returns_response_not_raises -q``
    """
    src_path = (
        Path(__file__).resolve().parents[2]
        / "src" / "medterm4ds" / "apps" / "fhir_api.py"
    )
    src = src_path.read_text()
    # Locate the _check_ready function body and assert no `raise` inside it.
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_check_ready":
            fn = node
            break
    assert fn is not None, "_check_ready function not found"
    for node in ast.walk(fn):
        assert not isinstance(node, ast.Raise), (
            f"_check_ready contains a `raise` statement at line "
            f"{node.lineno}: CR-005 regression. The function MUST return "
            f"the Response (or None), not raise it (Response is not a "
            f"BaseException; raising causes TypeError)."
        )


def test_cr005_search_get_returns_503_via_check_ready(fhir_client):
    """CR-005 behavioral: when ``app.state.ready=False`` is simulated by
    the route returning a 503 (the ``$search`` 503-no-index path is the
    closest reachable 503 in the fixture), the response MUST be a FHIR
    OperationOutcome with the correct Content-Type — NOT a Python
    traceback from a TypeError. The fixture's $search 503s because the
    BM25 indexes are absent; this is the closest behavioral proxy for
    the shutdown-transient failure shape.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr005_search_get_returns_503_via_check_ready -q``
    """
    r = fhir_client.get("/fhir/CodeSystem/$search", params={"query": "diabetes"})
    # Either 503 (no indexes) or 200 (if some env has indexes). Both are
    # FHIR-shaped; the failure mode CR-005 prevents is a 500 with
    # text/plain traceback body.
    assert r.status_code in (200, 503), (
        f"Expected 200 or 503; got {r.status_code}: {r.text[:200]!r}. "
        f"CR-005 regression would produce 500 + text/plain traceback."
    )
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"Expected application/fhir+json Content-Type; got {ct!r}. "
        f"CR-005 regression would produce text/plain."
    )
    if r.status_code == 503:
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"Expected OperationOutcome body; got {body!r}"
        )


# =============================================================================
# CR-011: _do_vs_validate must re-resolve the canonical system URI
# =============================================================================

def test_cr011_vs_validate_canonicalizes_alias_system(fhir_client):
    """CR-011: ``POST /fhir/ValueSet/$validate-code`` with ``system`` set
    to a valid alias (``urn:oid:2.16.840.1.113883.6.96`` for SNOMED CT)
    MUST return the canonical URI (``http://snomed.info/sct``) in the
    Out ``system`` parameter — not echo the alias verbatim. Same drift
    pattern as CS-02 HISTORIAN QA-047 (_do_lookup) and CS-03 HISTORIAN
    QA-051 (_do_validate); the ValueSet/$validate-code handler was
    missed (CR-007 partial regression). Structural fix: shared
    ``canonical_system_uri`` helper.

    Spec: FHIR R4 §4.8.21.1 Out `system`.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr011_vs_validate_canonicalizes_alias_system -q``
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$validate-code",
        params={
            "system": "urn:oid:2.16.840.1.113883.6.96",  # SNOMED CT OID alias
            "code": "73211009",
        },
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    system_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert system_param is not None, "Out `system` parameter missing"
    emitted = system_param.get("valueUri")
    assert emitted == "http://snomed.info/sct", (
        f"Expected canonical 'http://snomed.info/sct'; got {emitted!r}. "
        f"CR-011 regression: alias-echo in _do_vs_validate."
    )


def test_cr011_vs_validate_canonicalizes_trailing_slash(fhir_client):
    """CR-011 trailing-slash variant: ``http://snomed.info/sct/`` (with
    trailing slash) MUST also re-resolve to the canonical
    ``http://snomed.info/sct`` (no trailing slash) in the Out ``system``.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr011_vs_validate_canonicalizes_trailing_slash -q``
    """
    r = fhir_client.get(
        "/fhir/ValueSet/$validate-code",
        params={
            "system": "http://snomed.info/sct/",  # trailing-slash variant
            "code": "73211009",
        },
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    system_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert system_param is not None
    emitted = system_param.get("valueUri")
    assert emitted == "http://snomed.info/sct", (
        f"Expected canonical 'http://snomed.info/sct' (no trailing slash); "
        f"got {emitted!r}."
    )


# =============================================================================
# CR-012: _do_translate source.system must re-resolve the canonical URI
# =============================================================================

def test_cr012_translate_source_system_canonicalizes_alias(fhir_client):
    """CR-012: ``GET /fhir/ConceptMap/$translate?system=urn:oid:...`` MUST
    emit the canonical URI in ``match[].source.system`` — not echo the
    alias. The ``target.system`` field was already canonical; this fix
    brings the source side to parity.

    Spec: FHIR R4 §4.8.21.1 Out Coding.system.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr012_translate_source_system_canonicalizes_alias -q``
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "urn:oid:2.16.840.1.113883.6.96",  # SNOMED CT OID alias
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
        },
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    if not matches:
        pytest.skip("fixture DB has no SNOMED→ICD10CM mappings for code 44054006")
    for m in matches:
        source_part = next(
            (pt for pt in m.get("part", []) if pt.get("name") == "source"),
            None,
        )
        assert source_part is not None, "match.part missing 'source'"
        source_coding = source_part.get("valueCoding", {})
        emitted = source_coding.get("system")
        assert emitted == "http://snomed.info/sct", (
            f"Expected canonical 'http://snomed.info/sct' in match.source.system; "
            f"got {emitted!r}. CR-012 regression: alias-echo in _do_translate."
        )


# =============================================================================
# CR-013: _expand_intensional contains[].system must re-resolve the canonical URI
# =============================================================================

def test_cr013_expand_contains_system_canonicalizes_alias(fhir_client):
    """CR-013: ``POST /fhir/ValueSet/$expand`` with a ValueSet body
    containing ``compose.include[].system = urn:oid:2.16.840.1.113883.6.96``
    MUST emit ``contains[].system = http://snomed.info/sct`` (canonical)
    — not echo the alias verbatim. Same drift pattern applies to the
    is-a filter path AND the descendants loop.

    Spec: FHIR R4 §4.7.5 Out ``contains[].system``.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr013_expand_contains_system_canonicalizes_alias -q``
    """
    body = {
        "resourceType": "ValueSet",
        "compose": {
            "include": [{
                # SNOMED CT OID alias — clients may use this verbatim.
                "system": "urn:oid:2.16.840.1.113883.6.96",
                "concept": [{"code": "73211009"}],
            }],
        },
    }
    r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
    assert r.status_code == 200, r.text[:200]
    contains = r.json().get("expansion", {}).get("contains", [])
    assert len(contains) >= 1
    emitted = contains[0].get("system")
    assert emitted == "http://snomed.info/sct", (
        f"Expected canonical 'http://snomed.info/sct' in contains[0].system; "
        f"got {emitted!r}. CR-013 regression: alias-echo in _expand_intensional."
    )


def test_cr013_expand_contains_system_canonicalizes_trailing_slash(fhir_client):
    """CR-013 trailing-slash variant: ``http://snomed.info/sct/`` MUST
    re-resolve to ``http://snomed.info/sct`` (no trailing slash).

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr013_expand_contains_system_canonicalizes_trailing_slash -q``
    """
    body = {
        "resourceType": "ValueSet",
        "compose": {
            "include": [{
                "system": "http://snomed.info/sct/",  # trailing slash
                "concept": [{"code": "73211009"}],
            }],
        },
    }
    r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
    assert r.status_code == 200, r.text[:200]
    contains = r.json().get("expansion", {}).get("contains", [])
    assert len(contains) >= 1
    emitted = contains[0].get("system")
    assert emitted == "http://snomed.info/sct", (
        f"Expected canonical 'http://snomed.info/sct' (no trailing slash); "
        f"got {emitted!r}."
    )


def test_cr013_expand_is_a_filter_canonicalizes_alias(fhir_client):
    """CR-013 is-a filter path: when the include uses an is-a filter
    (rather than an explicit concept list), the descendant
    ``contains[].system`` MUST still be canonical.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr013_expand_is_a_filter_canonicalizes_alias -q``
    """
    body = {
        "resourceType": "ValueSet",
        "compose": {
            "include": [{
                "system": "urn:oid:2.16.840.1.113883.6.96",  # alias
                "filter": [
                    {"property": "concept", "op": "is-a", "value": "73211009"},
                ],
            }],
        },
    }
    r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
    assert r.status_code == 200, r.text[:200]
    contains = r.json().get("expansion", {}).get("contains", [])
    assert len(contains) >= 1, "Expected at least the is-a root in expansion"
    for c in contains:
        emitted = c.get("system")
        assert emitted == "http://snomed.info/sct", (
            f"Expected canonical 'http://snomed.info/sct' for ALL contains[]; "
            f"got {emitted!r} for code {c.get('code')!r}."
        )


# =============================================================================
# CR-014: Per-enum frozen-set constants (single source of truth)
# =============================================================================

def test_cr014_frozen_set_constants_exist_and_correct():
    """CR-014: ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` (10 values) and
    ``FHIR_R4_FILTER_OPERATORS`` (9 values) MUST be defined as
    ``frozenset[str]`` constants in ``medterm4ds.engines.fhir``,
    containing the spec-correct R4 values. The R5/R4B ``subsumedby`` and
    the R5-only ``matches`` MUST NOT be in the ConceptMapEquivalence set;
    the R4 ``specializes`` MUST be. The frozen-set form prevents test-
    suite-encoded-wrong-spec drift (pattern count=3 at milestone 2).

    Spec:
      https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
      https://hl7.org/fhir/R4/valueset.html#filter

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr014_frozen_set_constants_exist_and_correct -q``
    """
    from medterm4ds.engines.fhir import (
        FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
        FHIR_R4_FILTER_OPERATORS,
    )

    # ConceptMapEquivalence: 10 values per R4 spec.
    assert isinstance(FHIR_R4_CONCEPT_MAP_EQUIVALENCE, frozenset)
    assert len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE) == 10
    assert "specializes" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        "R4 `specializes` MUST be in the enum "
        "(https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html)."
    )
    # R5/R4B / R5-only values MUST NOT be in the R4 enum.
    for off_spec in ("subsumedby", "matches", "not-relatedto"):
        assert off_spec not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"Off-spec value {off_spec!r} MUST NOT be in the R4 enum."
        )

    # Filter Operator: 9 values per R4 spec.
    assert isinstance(FHIR_R4_FILTER_OPERATORS, frozenset)
    assert len(FHIR_R4_FILTER_OPERATORS) == 9
    # Spec spelling is `descendent-of` (Latin), NOT `descendant-of`.
    assert "descendent-of" in FHIR_R4_FILTER_OPERATORS
    assert "descendant-of" not in FHIR_R4_FILTER_OPERATORS


def test_cr014_internal_rel_map_emits_only_r4_values():
    """CR-014 / CF-HISTORIAN-VS01-01: the ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE``
    map in ``engines/fhir/responses.py`` MUST emit only values in
    ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``. The map USED TO emit ``subsumedby``
    (R5/R4B) and ``not-relatedto`` (not in any FHIR enum); the milestone-2
    fix translated them to ``specializes`` and ``unmatched`` respectively.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr014_internal_rel_map_emits_only_r4_values -q``
    """
    from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE

    emitted = set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    drifted = emitted - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drifted, (
        f"_INTERNAL_REL_TO_FHIR_EQUIVALENCE emits values outside the R4 enum: "
        f"{drifted}. CF-HISTORIAN-VS01-01 was resolved in milestone-2; if "
        f"these values reappear the fix was reverted."
    )
    # Positive assertion: the R4 spec-correct value is the one emitted for
    # the reverse-of-subsumes case (subsumedby/subsumed-by engine vocab).
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["subsumedby"] == "specializes"
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["subsumed-by"] == "specializes"
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-relatedto"] == "unmatched"


def test_cr014_canonical_system_uri_helper_in_engines_fhir():
    """CR-014/Structural Fix 1: the shared ``canonical_system_uri``
    helper MUST be importable from ``medterm4ds.engines.fhir``. It
    re-resolves (alias / trailing-slash) client-supplied system URIs
    to their canonical FHIR R4 URIs, with WARNING-level logging on
    fallback per GLOBAL_RULES.md "Silent Fallbacks".

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr014_canonical_system_uri_helper_in_engines_fhir -q``
    """
    from medterm4ds.engines.fhir import canonical_system_uri

    # Alias resolution.
    assert canonical_system_uri("urn:oid:2.16.840.1.113883.6.96") == "http://snomed.info/sct"
    # Trailing slash.
    assert canonical_system_uri("http://snomed.info/sct/") == "http://snomed.info/sct"
    # Canonical passthrough.
    assert canonical_system_uri("http://snomed.info/sct") == "http://snomed.info/sct"
    # Unknown system — fall back to client input (with WARNING).
    assert canonical_system_uri("http://example.org/unknown") == "http://example.org/unknown"
    # Explicit source hint skips the fhir_uri_to_system step.
    assert canonical_system_uri("urn:oid:2.16.840.1.113883.6.96", source="SNOMEDCT_US") == "http://snomed.info/sct"


# =============================================================================
# CR-019: App-level duckdb.Error handler returns 503 OperationOutcome
# =============================================================================

def test_cr019_duckdb_error_handler_registered():
    """CR-019 / CF-HISTORIAN-CS04-02: the FastAPI app MUST register an
    ``@app.exception_handler(duckdb.Error)`` handler that returns a FHIR
    OperationOutcome with status 503 (Service Unavailable) — appropriate
    for transient DB issues. Without this registration, every per-operation
    ``_do_*`` handler inherits the systemic gap: a transient DuckDB
    operational failure propagates past the handler to Starlette's default
    500 with ``text/plain`` body — non-conformant per FHIR R4 §3.1.0.1.5
    + §3.1.0.1.9.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr019_duckdb_error_handler_registered -q``
    """
    src_path = (
        Path(__file__).resolve().parents[2]
        / "src" / "medterm4ds" / "apps" / "fhir_api.py"
    )
    src = src_path.read_text()
    # The exception handler registration must exist.
    assert "@app.exception_handler(duckdb.Error)" in src, (
        "CR-019 regression: @app.exception_handler(duckdb.Error) is not "
        "registered. CF-HISTORIAN-CS04-02 systemic gap re-introduced."
    )
    # The handler MUST emit 503, not 500.
    tree = ast.parse(src)
    handler_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and "duckdb" in node.name.lower():
            handler_found = True
            # Walk for the status code passed to _fhir_error_response.
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    for arg in sub.args:
                        if isinstance(arg, ast.Constant) and arg.value == 503:
                            return
            pytest.fail(
                "duckdb.Error handler found but no 503 status code in its body. "
                "CR-019 specifies 503 (Service Unavailable) for transient DB issues."
            )
    assert handler_found, "duckdb.Error handler function not found"


def test_cr019_duckdb_error_returns_fhir_operation_outcome(fhir_client, monkeypatch):
    """CR-019 behavioral: simulate a transient DuckDB failure during a
    ``$lookup`` request and assert the response is a FHIR OperationOutcome
    with status 503 and the correct Content-Type — NOT a 500 with
    ``text/plain`` Python traceback body.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone2_review_fixes.py
        ::test_cr019_duckdb_error_returns_fhir_operation_outcome -q``
    """
    import duckdb
    from medterm4ds.apps import fhir_api as fhir_api_mod

    # Monkeypatch get_code_infos (as bound in fhir_api) to raise
    # duckdb.Error — simulates a transient connection issue / lock
    # contention / OOM. CR-019 specifies the narrow exception type
    # ``duckdb.Error`` (NOT ``Exception``) so programming bugs propagate.
    def boom(*args, **kwargs):
        raise duckdb.Error("simulated transient DB failure (CR-019 probe)")

    monkeypatch.setattr(fhir_api_mod, "get_code_infos", boom)
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    assert r.status_code == 503, (
        f"Expected 503 for duckdb.Error; got {r.status_code}: {r.text[:200]!r}. "
        f"CR-019 regression: 500 with text/plain traceback would indicate the "
        f"exception handler is not registered."
    )
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct, (
        f"Expected application/fhir+json Content-Type; got {ct!r}."
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"
    issue = body.get("issue", [{}])[0]
    assert issue.get("severity") == "error"
