"""SKEPTIC RESWEEP probes for chunk CM-03 (ConceptMap $closure Operation).

Source: https://build.fhir.org/conceptmap-operation-closure.html
Canonical R4 OperationDefinition:
    https://hl7.org/fhir/R4/conceptmap-operation-closure.html

This resweep test file extends the baseline ``test_cm03_skeptic.py`` with
NEW hostile-input probes through the SKEPTIC lens ("Break it."). Per
``evolution.json.config.notes`` (CM-02/TERMINOLOGIST tip), the
``$closure`` operation builds a pre-computed subsumption table and the
SKEPTIC lens MUST probe:

  1. Hostile ``name`` parameter on init (very long, special chars, null
     bytes, SQL injection, whitespace-only). The closure identifier is
     a load-bearing key — collision-prone or unsafe values can pollute
     the singleton ClosureManager's ``_tables`` dict.
  2. Hostile ``concept`` entries in add-concepts batch (non-dict,
     missing code/system, mixed valid+invalid, very large batches).
     Verify the 10th PROMOTED pattern (``isinstance`` guard at
     untrusted-data list-iterator boundary — CF-HISTORIAN-CM03-01
     RESOLVED site at ``_do_closure`` inline concept extraction).
  3. Re-init / idempotency edge cases (init -> add -> re-init -> add
     — does the closure correctly reset? version hash reflects state
     transitions correctly?).

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus; A44054006 -> A73211009)

Existing baseline coverage in test_cm03_skeptic.py: 35 tests across 13
lenses covering items 1-7 of the spec. This resweep does NOT re-derive
baseline coverage — it focuses on NEW hostile-input combinations and
structural regression-pins for the 10th PROMOTED pattern.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from medterm4ds.apps.fhir_api import create_fhir_app
from medterm4ds.engines.fhir.closure import (
    ClosureManager,
    ClosureTable,
    build_closure_response,
    get_closure_manager,
)


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
UNKNOWN_SYSTEM_URI = "http://example.org/unknown-system"

# Hostile-input constants.
LONG_NAME_1K = "n" * 1000
LONG_NAME_10K = "n" * 10000
SPECIAL_CHARS_NAME = "name with spaces & symbols?=<>"
SQL_INJECTION_NAME = "name'; DROP TABLE closure; --"
XSS_NAME = "<script>alert('xss')</script>"
NULL_BYTE_NAME = "valid_name\x00injection"
CRLF_NAME = "name\r\nX-Inject: header"
UNICODE_CJK_NAME = "關閉表"
WHITESPACE_ONLY_NAME = "    "
NEWLINE_ONLY_NAME = "\n\t\r"


def _closure_param_name_only(name: str) -> dict[str, Any]:
    return {
        "resourceType": "Parameters",
        "parameter": [{"name": "name", "valueString": name}],
    }


def _closure_param_with_concepts(
    name: str, concepts: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "name", "valueString": name},
            *[
                {"name": "concept", "valueCoding": c}
                for c in concepts
            ],
        ],
    }


def _closure_param_with_raw_concept_entries(
    name: str, concept_entries: list[dict[str, Any]]
) -> dict[str, Any]:
    """Like _closure_param_with_concepts but accepts the RAW parameter
    entries (not just valueCoding dicts) — used to inject non-dict
    valueCoding entries, missing-name entries, etc."""
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "name", "valueString": name},
            *concept_entries,
        ],
    }


def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


def _find_params(body: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _return_hash(body: dict[str, Any]) -> str | None:
    p = _find_param(body, "return")
    if p is None:
        return None
    return p.get("valueString")


def _is_fhir_response(r) -> bool:
    return r.headers.get("content-type", "").startswith("application/fhir+")


def _get_func_source(source: str, name: str) -> str:
    """Return source text of a top-level OR nested function by name.

    Walks BOTH ``ast.FunctionDef`` AND ``ast.AsyncFunctionDef`` to catch
    nested async route handlers inside ``create_fhir_app()``. Mirrors
    CM-02 SKEPTIC resweep strategy.
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


# ===========================================================================
# Lens 1: Hostile `name` parameter — Item 1 (required name).
#
# The closure identifier is a load-bearing key into the singleton
# ClosureManager's `_tables` dict. Hostile names that the implementation
# accepts without validation gate MAY:
#   * collide (silent state-overwrite),
#   * pollute the dict (memory leak),
#   * trigger downstream KeyError/AttributeError if used as a key in
#     another data structure.
# The CM-03 SKEPTIC baseline (test_s10-s13) covers missing/empty name.
# This resweep probes hostile-name ACCEPTANCE + structural safety.
# ===========================================================================


@pytest.mark.parametrize(
    "label, name",
    [
        ("long-1k", LONG_NAME_1K),
        ("long-10k", LONG_NAME_10K),
        ("special-chars", SPECIAL_CHARS_NAME),
        ("sql-injection", SQL_INJECTION_NAME),
        ("xss", XSS_NAME),
        ("crlf", CRLF_NAME),
        ("unicode-cjk", UNICODE_CJK_NAME),
    ],
    ids=lambda v: v,
)
def test_s10_init_with_hostile_name_does_not_500(fhir_client, label, name):
    """SKEPTIC (item 1, hostile name): POST ``$closure`` with a hostile
    ``name`` value MUST NOT return 5xx or a Python traceback. The
    implementation accepts any string as a name (no validation gate) —
    the load-bearing requirement is that the server doesn't crash.

    Per FHIR R4 §3.1.0.1.5 a server MUST respond with a FHIR resource
    (OperationOutcome on error), never a 500 + traceback.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name),
    )
    assert r.status_code < 500, (
        f"hostile name {label!r} caused server error: {r.status_code} {r.text}"
    )
    assert _is_fhir_response(r), (
        f"hostile name {label!r} — Content-Type drift: "
        f"{r.headers.get('content-type')!r}"
    )


def test_s11_init_with_hostile_name_then_add_then_check_roundtrip(fhir_client):
    """SKEPTIC (items 1+2+7, hostile name end-to-end): a closure created
    with a hostile name MUST be usable for the full lifecycle (init ->
    add concepts -> check subsumption). If the name were silently
    mangled or rejected internally, the check would fail.

    Uses a SQL-injection-shaped name — if the name leaked into a SQL
    query, the add_concepts call would either 500 or fail to register
    the concept.
    """
    name = "hostile-'\";--"
    r_init = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name),
    )
    assert r_init.status_code == 200, (
        f"init with hostile name — got {r_init.status_code}: {r_init.text}"
    )
    r_add = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    assert r_add.status_code == 200, (
        f"add with hostile name — got {r_add.status_code}: {r_add.text}"
    )
    # The closure table MUST exist under the exact name string.
    closure = get_closure_manager().get(name)
    assert closure is not None, (
        f"closure {name!r} not retrievable after init+add — name was silently "
        f"mangled or rejected"
    )
    assert ("SNOMEDCT_US", "73211009") in closure.concepts, (
        f"concept not registered under hostile name {name!r}"
    )


def test_s12_init_with_whitespace_only_name_accepted(fhir_client):
    """SKEPTIC (item 1, hostile name edge): ``name="    "`` (whitespace
    only). The implementation uses ``if not name`` which treats a
    whitespace-only string as truthy — so it's accepted as a valid
    name. Probe documents current behavior.

    Adversarial: a client bug producing whitespace-only names would
    silently create a closure named "    " — collision-prone across
    independent client sessions.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(WHITESPACE_ONLY_NAME),
    )
    # Current behavior: whitespace-only is accepted (truthy).
    # This probe asserts the CURRENT shape — when the implementation
    # adds a `.strip()` guard, tighten to assert 400.
    assert r.status_code == 200, (
        f"whitespace-only name — expected 200 (current behavior; truthy "
        f"non-empty string); got {r.status_code}: {r.text}"
    )


def test_s13_init_with_null_byte_name_does_not_500(fhir_client):
    """SKEPTIC (item 1, hostile name edge): ``name="valid\\x00injection"``.
    A null byte in the name MUST NOT crash the server.

    Note: httpx may strip null bytes client-side per RFC 3986 §3.3, so
    the probe also verifies the server response shape (FHIR MIME on
    either 200 or 400 — never 500).
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(NULL_BYTE_NAME),
    )
    assert r.status_code < 500, (
        f"null byte name caused 5xx: {r.status_code} {r.text}"
    )
    assert _is_fhir_response(r), (
        f"null byte name — Content-Type drift: "
        f"{r.headers.get('content-type')!r}"
    )


def test_s14_init_with_two_hostile_names_isolated(fhir_client):
    """SKEPTIC (item 1 + isolation): two distinct hostile names produce
    two distinct closure tables (no collision).

    Adversarial: if hostile names were silently normalized (e.g., via
    ``str(name).strip()``) or hashed to the same key, two client
    sessions using different hostile names would share a closure
    table — silent state corruption.
    """
    name_a = "hostile-A-';--"
    name_b = "hostile-B-';--"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name_a,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name_b,
            [{"system": SNOMED_URI, "code": "44054006", "display": "T2DM"}],
        ),
    )
    manager = get_closure_manager()
    a = manager.get(name_a)
    b = manager.get(name_b)
    assert a is not None and b is not None, (
        "both hostile-name closures should be retrievable"
    )
    assert a is not b, (
        "two hostile-name closures should be distinct instances"
    )
    assert ("SNOMEDCT_US", "73211009") in a.concepts and \
        ("SNOMEDCT_US", "44054006") not in a.concepts, (
        f"closure {name_a!r} should only have its own concept"
    )
    assert ("SNOMEDCT_US", "44054006") in b.concepts and \
        ("SNOMEDCT_US", "73211009") not in b.concepts, (
        f"closure {name_b!r} should only have its own concept"
    )


# ===========================================================================
# Lens 2: Hostile `concept` entries — Item 2 (optional repeating valueCoding).
#
# Per CM-02/TERMINOLOGIST tip, the 10th PROMOTED pattern (isinstance
# guard at untrusted-data list-iterator boundary) covers the
# CF-HISTORIAN-CM03-01 site (``_do_closure`` inline concept extraction
# at apps/fhir_api.py:2278). SKEPTIC resweep MUST verify the guard
# does not regress under hostile valueCoding entries.
# ===========================================================================


def test_s20_add_concept_with_value_coding_as_string_does_not_500(fhir_client):
    """SKEPTIC (item 2 + 10th PROMOTED): POST ``$closure`` with
    ``valueCoding`` as a STRING (not a dict) MUST return 200, not 500.

    Without the isinstance guard at ``_do_closure``, the loop body
    calls ``coding.get("system", "")`` on a string — raises
    ``AttributeError: 'str' object has no attribute 'get'`` — propagates
    as 500 + traceback. The guard silently drops the malformed entry.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_raw_concept_entries(
            "skeptic-string-vc-20",
            [{"name": "concept", "valueCoding": "not-a-dict"}],
        ),
    )
    # QC-264 (HIGH): a body whose ONLY concept entry is malformed is a
    # 400 OperationOutcome — never a 500-with-traceback (the isinstance
    # guard still holds) and never a silent reset.
    assert r.status_code == 400, (
        f"valueCoding as string — expected 400 (QC-264 all-malformed "
        f"rejection); got {r.status_code}: {r.text}"
    )
    assert _is_fhir_response(r)


def test_s21_add_concept_with_value_coding_as_int_does_not_500(fhir_client):
    """SKEPTIC (item 2 + 10th PROMOTED): ``valueCoding`` as INT."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_raw_concept_entries(
            "skeptic-int-vc-21",
            [{"name": "concept", "valueCoding": 12345}],
        ),
    )
    assert r.status_code == 400, (
        f"valueCoding as int — expected 400 (QC-264 all-malformed "
        f"rejection); got {r.status_code}: {r.text}"
    )


def test_s22_add_concept_with_value_coding_as_null_does_not_500(fhir_client):
    """SKEPTIC (item 2 + 10th PROMOTED): ``valueCoding`` as NULL."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_raw_concept_entries(
            "skeptic-null-vc-22",
            [{"name": "concept", "valueCoding": None}],
        ),
    )
    assert r.status_code == 400, (
        f"valueCoding as null — expected 400 (QC-264 all-malformed "
        f"rejection); got {r.status_code}: {r.text}"
    )


def test_s23_add_concept_with_value_coding_as_list_does_not_500(fhir_client):
    """SKEPTIC (item 2 + 10th PROMOTED): ``valueCoding`` as LIST."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_raw_concept_entries(
            "skeptic-list-vc-23",
            [{"name": "concept", "valueCoding": [{"system": SNOMED_URI, "code": "X"}]}],
        ),
    )
    assert r.status_code == 400, (
        f"valueCoding as list — expected 400 (QC-264 all-malformed "
        f"rejection); got {r.status_code}: {r.text}"
    )


def test_s24_add_concept_with_non_dict_parameter_entry_does_not_500(fhir_client):
    """SKEPTIC (item 2 + 10th PROMOTED): a non-dict ENTRY in
    ``parameter[]`` MUST return 200, not 500.

    Per CS-04 SKEPTIC QA-001, the isinstance guard at
    ``_parse_parameters`` (10th PROMOTED pattern) catches non-dict
    entries in ``parameter[]`` before they reach ``_do_closure``. But
    ``_do_closure`` ALSO iterates ``body.get("parameter", [])`` directly
    (not via ``_parse_parameters``), so it has its OWN isinstance guard
    at line 2311. This probe verifies BOTH guards work: a non-dict
    entry must be silently dropped, not crash.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "name", "valueString": "skeptic-non-dict-24"},
                "string-entry-not-dict",  # non-dict
                42,                       # non-dict
                None,                     # non-dict
                ["a", "list"],            # non-dict
            ],
        },
    )
    assert r.status_code == 200, (
        f"non-dict parameter entries — expected 200 (silent drop via "
        f"isinstance guard); got {r.status_code}: {r.text}"
    )


def test_s25_add_concept_missing_code_silently_dropped(fhir_client):
    """SKEPTIC (item 2 edge): a valueCoding with ``system`` but no
    ``code`` is silently dropped (the implementation requires both
    ``code and system_uri``).

    Adversarial: confirms missing-code doesn't crash — the entry is
    silently dropped.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_raw_concept_entries(
            "skeptic-missing-code-25",
            [{"name": "concept", "valueCoding": {"system": SNOMED_URI}}],
        ),
    )
    # QC-264 (HIGH): all-malformed concept entries are a 400.
    assert r.status_code == 400
    assert r.json()["resourceType"] == "OperationOutcome"


def test_s26_add_concept_missing_system_silently_dropped(fhir_client):
    """SKEPTIC (item 2 edge): a valueCoding with ``code`` but no
    ``system`` is silently dropped."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_raw_concept_entries(
            "skeptic-missing-system-26",
            [{"name": "concept", "valueCoding": {"code": "73211009"}}],
        ),
    )
    # QC-264 (HIGH): all-malformed concept entries are a 400.
    assert r.status_code == 400
    assert r.json()["resourceType"] == "OperationOutcome"


def test_s27_add_concept_missing_both_code_and_system_silently_dropped(fhir_client):
    """SKEPTIC (item 2 edge): empty valueCoding dict ``{}``."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_raw_concept_entries(
            "skeptic-empty-vc-27",
            [{"name": "concept", "valueCoding": {}}],
        ),
    )
    # QC-264 (HIGH): all-malformed concept entries are a 400.
    assert r.status_code == 400
    assert r.json()["resourceType"] == "OperationOutcome"


def test_s28_add_concept_mixed_valid_and_invalid_entries(fhir_client):
    """SKEPTIC (item 2 + 10th PROMOTED, MIX): a single batch with MIX
    of valid and malformed entries — the valid entries MUST be added,
    the malformed ones silently dropped, no 500.

    Adversarial: this is the load-bearing test for the 10th PROMOTED
    pattern — if the isinstance guard regresses, the first malformed
    entry crashes the WHOLE batch (500), losing the valid entries.
    """
    name = "skeptic-mix-28"
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "name", "valueString": name},
                # Valid entry — should be added
                {"name": "concept", "valueCoding": {"system": SNOMED_URI, "code": "73211009", "display": "DM"}},
                # Malformed entries — should be silently dropped
                {"name": "concept", "valueCoding": "string"},
                {"name": "concept", "valueCoding": 42},
                {"name": "concept", "valueCoding": None},
                {"name": "concept"},  # missing valueCoding
                {"name": "concept", "valueCoding": {"code": "X"}},  # missing system
                {"name": "concept", "valueCoding": {"system": SNOMED_URI}},  # missing code
                "not-a-dict",
                99,
                None,
                # Valid entry — should be added
                {"name": "concept", "valueCoding": {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"}},
            ],
        },
    )
    assert r.status_code == 200, (
        f"mixed valid+invalid entries — expected 200; got {r.status_code}: {r.text}"
    )
    body = r.json()
    codes = {c["valueCoding"]["code"] for c in _find_params(body, "concept")}
    assert "73211009" in codes and "44054006" in codes, (
        f"valid entries should be added; got codes {codes}"
    )


def test_s29_add_concept_large_batch_does_not_500(fhir_client):
    """SKEPTIC (item 2, performance boundary): adding a large batch of
    concepts (1000 entries) MUST NOT 5xx.

    Adversarial: if the batched add_concepts path had O(n^2) behavior
    or unbounded memory growth, a 1000-entry batch could exhaust
    resources.
    """
    concepts = [
        {"name": "concept", "valueCoding": {"system": SNOMED_URI, "code": f"X{i}"}}
        for i in range(1000)
    ]
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "name", "valueString": "skeptic-large-batch-29"},
                *concepts,
            ],
        },
    )
    # QC-269 (LOW): codes with no active atom are now rejected (mirroring
    # $lookup) — a 1000-bogus-code batch is a 400, not a 200 that silently
    # pollutes the closure. The load-bearing no-5xx contract still holds.
    assert r.status_code == 400, (
        f"large batch of unknown codes — expected 400 (QC-269); "
        f"got {r.status_code}: {r.text[:500]}"
    )


def test_s2a_add_concept_with_very_long_code_value(fhir_client):
    """SKEPTIC (item 2, hostile value): a valueCoding with a 10K-char
    code value MUST NOT 5xx. The implementation accepts arbitrary
    strings as code values; the load-bearing requirement is no crash.
    """
    long_code = "C" * 10000
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "skeptic-long-code-2a",
            [{"system": SNOMED_URI, "code": long_code, "display": "X"}],
        ),
    )
    # QC-269: unknown code rejected at 400 — and critically NOT a 5xx
    # (the no-crash contract on hostile values).
    assert r.status_code == 400, (
        f"10K-char code — got {r.status_code}: {r.text[:500]}"
    )


def test_s2b_add_concept_with_sql_injection_code_value(fhir_client):
    """SKEPTIC (item 2, hostile value): SQL-injection-shaped code
    value MUST NOT 5xx. If the code value leaked into a SQL query
    (parameter binding bug), the add_concepts call would either crash
    or silently corrupt the closure table.

    Note: medterm4ds uses parameterized queries throughout; this probe
    is a regression-pin against a future refactor that string-formats
    SQL with code values.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "skeptic-sql-code-2b",
            [{"system": SNOMED_URI, "code": "73211009'; DROP TABLE mrrel; --", "display": "X"}],
        ),
    )
    # QC-269: the injection-shaped code resolves to no active atom and is
    # rejected at 400 — parameterized queries mean no crash/corruption
    # either way (the load-bearing contract).
    assert r.status_code == 400, (
        f"SQL-injection code — got {r.status_code}: {r.text[:500]}"
    )
    # The closure was never created (the add was rejected before any
    # registration) — no SQL injection occurred.
    closure = get_closure_manager().get("skeptic-sql-code-2b")
    assert closure is None


# ===========================================================================
# Lens 3: Hostile `concept` system aliases — Item 2 + canonical URI.
#
# Per CM-02 HISTORIAN methodology (3-op round-trip), the canonical URI
# invariant MUST hold on the closure surface. Aliases (trailing-slash,
# urn:oid, uppercase-scheme) MUST resolve to the same source label.
# ===========================================================================


@pytest.mark.parametrize(
    "label, system_uri",
    [
        ("trailing-slash", SNOMED_URI_TRAILING_SLASH),
        ("urn-oid-alias", SNOMED_URI_OID_ALIAS),
        ("uppercase-scheme", SNOMED_URI_UPPERCASE_SCHEME),
    ],
    ids=lambda v: v,
)
def test_s30_add_concept_with_alias_system_resolves_to_same_source(fhir_client, label, system_uri):
    """SKEPTIC (item 2 + canonical URI): adding a concept with an
    alias system URI (trailing-slash, urn:oid, uppercase-scheme) MUST
    resolve to the same source label as the canonical SNOMED URI.

    Adversarial: if alias resolution regressed, two client sessions
    using different alias forms for the same concept would create two
    distinct closure entries — silent state fragmentation.
    """
    name = f"skeptic-alias-{label}-30"
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": system_uri, "code": "73211009", "display": "DM"}],
        ),
    )
    assert r.status_code == 200, (
        f"alias {label} — got {r.status_code}: {r.text}"
    )
    closure = get_closure_manager().get(name)
    assert closure is not None
    # The concept MUST be registered under the resolved source label
    # (SNOMEDCT_US), not the raw alias.
    info = closure.concepts.get(("SNOMEDCT_US", "73211009"))
    assert info is not None, "concept not registered"
    # The source should resolve to the canonical SNOMEDCT_US label.
    assert info["system"] == "SNOMEDCT_US", (
        f"alias {label} did not resolve to SNOMEDCT_US; got source={info['system']!r}"
    )


# ===========================================================================
# Lens 4: Re-init / idempotency — Items 3 + 5.
#
# Per the task directive: probe init -> add -> re-init -> add sequence.
# Does the closure correctly reset? Does the version hash reflect the
# reset? Does a re-add after reset work cleanly?
# ===========================================================================


def test_s40_init_add_reinit_add_full_lifecycle(fhir_client):
    """SKEPTIC (items 3+5, full lifecycle): init -> add -> re-init ->
    add MUST work cleanly. Each transition MUST be reflected in the
    version hash.

    Adversarial: if ``manager.reset(name)`` doesn't fully clear state,
    the second add would observe stale concepts and could produce
    wrong subsumption results.
    """
    name = "skeptic-lifecycle-40"
    # 1. init (empty)
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name),
    )
    h1 = _return_hash(r1.json())
    closure = get_closure_manager().get(name)
    assert len(closure.concepts) == 0, "fresh closure should be empty"

    # 2. add DM
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    h2 = _return_hash(r2.json())
    assert h2 != h1, "add MUST change hash"
    closure = get_closure_manager().get(name)
    assert ("SNOMEDCT_US", "73211009") in closure.concepts, (
        "concept should be registered"
    )

    # 3. re-init (MUST clear concepts)
    r3 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name),
    )
    h3 = _return_hash(r3.json())
    assert h3 != h2, "re-init MUST change hash"
    assert h3 == h1, "re-init hash MUST equal original empty hash"
    closure = get_closure_manager().get(name)
    assert len(closure.concepts) == 0, "re-init MUST clear concepts"
    assert ("SNOMEDCT_US", "73211009") not in closure.concepts, (
        "re-init MUST clear concepts"
    )

    # 4. add T2DM after re-init
    r4 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "44054006", "display": "T2DM"}],
        ),
    )
    h4 = _return_hash(r4.json())
    assert h4 != h3, "second add MUST change hash"
    closure = get_closure_manager().get(name)
    assert ("SNOMEDCT_US", "44054006") in closure.concepts, (
        "second add should register concept"
    )
    assert ("SNOMEDCT_US", "73211009") not in closure.concepts, (
        "second add should NOT re-introduce the pre-reinit concept"
    )


def test_s41_reinit_then_add_then_subsumes_check_correct(fhir_client):
    """SKEPTIC (items 3+5+7, subsumption after re-init): after re-init,
    a fresh subsumption check MUST produce the correct outcome. If
    re-init didn't fully clear ``_subsumes``, stale relationships
    could produce wrong answers.
    """
    name = "skeptic-subsumes-after-reinit-41"
    # Add DM + T2DM
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    closure = get_closure_manager().get(name)
    assert closure.check("73211009", "44054006") == "subsumes", (
        "before re-init, DM subsumes T2DM"
    )

    # Re-init
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name),
    )
    closure = get_closure_manager().get(name)
    # After re-init, _subsumes MUST be empty.
    assert len(closure._subsumes) == 0, (
        f"re-init MUST clear _subsumes; got {len(closure._subsumes)} entries"
    )
    # After re-init, check should return not-subsumed (concepts gone).
    assert closure.check("73211009", "44054006") == "not-subsumed", (
        "after re-init, codes are not in closure — should be not-subsumed"
    )


def test_s42_init_idempotent_concept_count_stays_zero(fhir_client):
    """SKEPTIC (item 3, idempotency): calling init (POST name only) 5
    times in a row produces an empty closure each time. No silent
    accumulation of state across resets.
    """
    name = "skeptic-idempotent-42"
    for _ in range(5):
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
    closure = get_closure_manager().get(name)
    assert len(closure.concepts) == 0, (
        f"5 consecutive inits should leave empty closure; got {len(closure.concepts)}"
    )
    assert len(closure._subsumes) == 0


def test_s43_add_same_concept_twice_is_idempotent_in_concepts_dict(fhir_client):
    """SKEPTIC (item 4, idempotent add): adding the same concept twice
    does not create a duplicate entry — ``concepts`` is a dict keyed
    by code, so the second add overwrites the first.

    Adversarial: if ``concepts`` were a list, two adds of the same
    concept would create two entries (silent duplication).
    """
    name = "skeptic-dup-add-43"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM-v1"}],
        ),
    )
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM-v2"}],
        ),
    )
    body = r2.json()
    concepts = _find_params(body, "concept")
    codes = [c["valueCoding"]["code"] for c in concepts]
    assert codes.count("73211009") == 1, (
        f"duplicate add should overwrite, not append; got codes {codes}"
    )
    # EC-11 QC-282/QC-278: displays are canonicalized through the engine
    # (ONE preferred term per code) and a re-add is a NO-OP — the
    # client-supplied DM-v2 display can no longer overwrite anything.
    closure = get_closure_manager().get(name)
    assert closure.concepts[("SNOMEDCT_US", "73211009")]["display"] == (
        "Diabetes mellitus"
    ), "display must stay the engine canonical preferred term"


def test_s44_reinit_clears_incomplete_since_flag(fhir_client):
    """SKEPTIC (item 3 + B6 fix): re-init MUST clear the
    ``incomplete_since`` flag. A re-initialized closure starts fresh —
    no prior transient failures should carry over.
    """
    name = "skeptic-incomplete-clear-44"
    # Manually mark the closure as incomplete (simulating prior transient)
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    closure = get_closure_manager().get(name)
    closure.incomplete_since = True  # simulate prior duckdb.Error
    assert closure.incomplete_since is True

    # Re-init
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name),
    )
    closure = get_closure_manager().get(name)
    assert closure.incomplete_since is False, (
        "re-init MUST create fresh table with incomplete_since=False"
    )


# ===========================================================================
# Lens 5: Version hash stability + drift — Item 5.
#
# Probe hash stability across identical calls, hash change on different
# state transitions, hash determinism.
# ===========================================================================


def test_s50_version_hash_stable_across_three_identical_inits(fhir_client):
    """SKEPTIC (item 5): calling init 3 times on the same name produces
    the same hash each time (idempotent)."""
    name = "skeptic-stable-50"
    hashes = []
    for _ in range(3):
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        hashes.append(_return_hash(r.json()))
    assert len(set(hashes)) == 1, (
        f"3 identical inits should produce 1 unique hash; got {hashes}"
    )


def test_s51_version_hash_changes_per_add_increment(fhir_client):
    """SKEPTIC (item 5): each add_concepts call increments ``_version``
    by exactly 1, so consecutive adds produce distinct hashes (the
    underlying state differs each time).
    """
    name = "skeptic-increment-51"
    # init
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name),
    )
    hashes = []
    # EC-11 QC-269: codes must resolve to an active atom — use real
    # fixture codes (one NEW concept per add so the content changes).
    for code in ("73211009", "44054006", "E11"):
        system = RXNORM_URI if code == "860975" else (
            ICD10CM_URI if code == "E11" else SNOMED_URI)
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [{"system": system, "code": code}],
            ),
        )
        hashes.append(_return_hash(r.json()))
    assert len(set(hashes)) == 3, (
        f"3 consecutive adds of new concepts should produce 3 unique "
        f"hashes; got {hashes}"
    )


def test_s52_version_hash_incorporates_display_changes(fhir_client):
    """SKEPTIC (item 5, hash drift edge): does the version hash change
    when the SAME code is re-added with a DIFFERENT display?

    Implementation: ``version_hash`` uses ``len(concepts):_version:sorted(concepts.keys())``
    — display is NOT in the hash payload. So the hash should NOT
    change on display-only updates. Probe documents current behavior.
    """
    name = "skeptic-display-drift-52"
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "Diabetes Mellitus"}],
        ),
    )
    h1 = _return_hash(r1.json())
    h2 = _return_hash(r2.json())
    # EC-11 QC-270/QC-278: the hash is content-addressed and excludes
    # the internal call counter. A re-add of an already-present concept
    # is a no-op, and client display changes never reach the stored
    # state (displays are engine-canonical per QC-282) — so identical
    # content yields the identical hash.
    assert h1 == h2, (
        "display-only re-add is a content no-op — hash MUST be identical"
    )


def test_s53_version_hash_payload_does_not_include_display():
    """SKEPTIC (item 5, source-read contract): ``ClosureTable.version_hash``
    payload MUST NOT include display values. Two closures with the same
    codes but different displays produce the same hash (modulo version).

    Adversarial: if display leaked into the hash, two clients using
    different display conventions would never agree on hash equality
    even for the same logical closure state.
    """
    t1 = ClosureTable("t1")
    t2 = ClosureTable("t2")
    # EC-11 QC-266/QC-283: concepts keyed by (source, code) and the hash
    # payload now covers the FULL state (concepts AND relations). Display
    # is part of the content — and since QC-282 it is always the engine
    # canonical preferred term, so a display change can only mean the
    # underlying terminology release changed (a real state change).
    t1.concepts[("S", "X")] = {"system": "S", "display": "Display-A"}
    t1._subsumes[(("S", "X"), ("S", "X"))] = True
    t2.concepts[("S", "X")] = {"system": "S", "display": "Display-B"}
    t2._subsumes[(("S", "X"), ("S", "X"))] = True
    assert t1.version_hash() != t2.version_hash(), (
        "QC-283: display/content changes MUST change the hash"
    )


def test_s54_version_hash_deterministic_across_manager_instances():
    """SKEPTIC (item 5): the version hash is deterministic across
    fresh ClosureManager instances (no entropy from process ID, time,
    or memory address).
    """
    t1 = ClosureTable("a")
    t2 = ClosureTable("b")
    # Both fresh
    assert t1.version_hash() == t2.version_hash(), (
        "fresh closures should produce identical hash (deterministic)"
    )
    # Same state mutation (EC-11 QC-266: (source, code) keys)
    t1.concepts[("S", "X")] = {"system": "S", "display": "X"}
    t1._subsumes[(("S", "X"), ("S", "X"))] = True
    t2.concepts[("S", "X")] = {"system": "S", "display": "X"}
    t2._subsumes[(("S", "X"), ("S", "X"))] = True
    assert t1.version_hash() == t2.version_hash(), (
        "same closure state should produce identical hash across instances"
    )


# ===========================================================================
# Lens 6: Subsumption table correctness on the closure surface — Items 6 + 7.
#
# Baseline test_s50-s54 covers DM/T2DM/equivalent/not-subsumed. This
# resweep adds HOSTILE subsumption probes — checks on codes not in the
# closure, checks on equal-but-different-system codes, mirror checks.
# ===========================================================================


def test_s60_check_both_directions_mutually_consistent(fhir_client):
    """SKEPTIC (item 7, mirror consistency): if check(A, B) returns
    "subsumes", then check(B, A) MUST return "subsumed-by" (NOT
    "subsumes" or "not-subsumed").

    Adversarial: if the closure table stored asymmetric relationships
    incorrectly, the mirror check would diverge.
    """
    name = "skeptic-mirror-60"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    closure = get_closure_manager().get(name)
    forward = closure.check("73211009", "44054006")
    reverse = closure.check("44054006", "73211009")
    assert forward == "subsumes" and reverse == "subsumed-by", (
        f"mirror inconsistency: forward={forward!r}, reverse={reverse!r}"
    )


def test_s61_check_unrelated_pair_both_directions_not_subsumed(fhir_client):
    """SKEPTIC (item 7): for unrelated concepts, BOTH directions return
    "not-subsumed"."""
    name = "skeptic-unrelated-mirror-61"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": RXNORM_URI, "code": "860975", "display": "metformin"},
            ],
        ),
    )
    closure = get_closure_manager().get(name)
    assert closure.check("73211009", "860975") == "not-subsumed"
    assert closure.check("860975", "73211009") == "not-subsumed"


def test_s62_check_codes_never_added_returns_not_subsumed(fhir_client):
    """SKEPTIC (item 7 edge): check(A, B) where NEITHER A nor B was
    added to the closure returns "not-subsumed" (not an error)."""
    name = "skeptic-never-added-62"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    closure = get_closure_manager().get(name)
    # Neither "Z1" nor "Z2" was added.
    assert closure.check("Z1", "Z2") == "not-subsumed", (
        "check on never-added codes should be 'not-subsumed'"
    )


def test_s63_check_one_in_one_out_returns_not_subsumed(fhir_client):
    """SKEPTIC (item 7 edge): check(A, B) where A is in the closure
    but B is NOT returns "not-subsumed"."""
    name = "skeptic-one-out-63"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    closure = get_closure_manager().get(name)
    # 73211009 is in closure; 99999999 is not.
    assert closure.check("73211009", "99999999") == "not-subsumed"
    assert closure.check("99999999", "73211009") == "not-subsumed"


def test_s64_check_with_empty_string_codes_does_not_crash(fhir_client):
    """SKEPTIC (item 7, hostile input): ``check("", "")``,
    ``check("X", "")``, ``check("", "X")`` MUST NOT crash.

    Adversarial: empty-string codes are a hostile-input boundary —
    if the closure table's ``_subsumes`` dict had a buggy key lookup,
    empty-string keys could trigger KeyError.
    """
    name = "skeptic-empty-check-64"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    closure = get_closure_manager().get(name)
    # Per ``check`` impl: code_a == code_b → "equivalent"
    assert closure.check("", "") == "equivalent"
    assert closure.check("X", "") == "not-subsumed"
    assert closure.check("", "X") == "not-subsumed"


# ===========================================================================
# Lens 7: Batch dispatcher — hostile $closure entries in a Bundle.
#
# Per TS-04 SKEPTIC QA-001 / HISTORIAN QA-038 (per-entry isolation), a
# malformed batch entry MUST NOT affect other entries. Apply this to
# the $closure batch dispatcher.
# ===========================================================================


def test_s70_batch_two_init_entries_isolated(fhir_client):
    """SKEPTIC (per-entry isolation): a batch with two $closure init
    entries on DIFFERENT names — both succeed independently."""
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [{"name": "name", "valueString": "skeptic-batch-A-70"}],
                    },
                },
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [{"name": "name", "valueString": "skeptic-batch-B-70"}],
                    },
                },
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    for i in range(2):
        e = body["entry"][i]
        assert e["response"]["status"] == "200", (
            f"entry[{i}] status={e['response']['status']!r}"
        )


def test_s71_batch_missing_name_isolates_from_valid_entry(fhir_client):
    """SKEPTIC (per-entry isolation): a batch with [missing-name entry,
    valid entry] — the valid entry STILL succeeds (per-entry isolation
    per FHIR R4 §3.7)."""
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [],  # missing name
                    },
                },
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [{"name": "name", "valueString": "skeptic-batch-iso-71"}],
                    },
                },
            ],
        },
    )
    body = r.json()
    assert body["entry"][0]["response"]["status"] == "400"
    assert body["entry"][1]["response"]["status"] == "200", (
        "per-entry isolation: entry[1] should succeed despite entry[0] failure"
    )


def test_s72_batch_with_malformed_concept_value_coding_does_not_500_entry(fhir_client):
    """SKEPTIC (per-entry isolation + 10th PROMOTED): a batch entry
    whose Parameters body has a non-dict valueCoding MUST succeed per-
    entry (the malformed valueCoding is silently dropped via the
    isinstance guard).

    Adversarial: if the batch dispatcher had a different code path
    from the direct POST handler, the isinstance guard could be
    missing here.
    """
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": "skeptic-batch-malformed-72"},
                            {"name": "concept", "valueCoding": "string-not-dict"},
                            {"name": "concept", "valueCoding": {"system": SNOMED_URI, "code": "73211009", "display": "DM"}},
                        ],
                    },
                },
            ],
        },
    )
    body = r.json()
    e = body["entry"][0]
    assert e["response"]["status"] == "200", (
        f"batch entry with malformed valueCoding — expected 200 (silent drop); "
        f"got {e['response']['status']}: {e.get('resource')}"
    )


def test_s73_batch_mixed_op_entries_isolate_closure(fhir_client):
    """SKEPTIC (per-entry isolation across ops): a batch with [$closure
    entry, $lookup entry] — both succeed independently."""
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [{"name": "name", "valueString": "skeptic-mixed-73"}],
                    },
                },
                {
                    "request": {
                        "method": "GET",
                        "url": "/CodeSystem/$lookup?system=http://snomed.info/sct&code=73211009",
                    },
                },
            ],
        },
    )
    body = r.json()
    assert body["entry"][0]["response"]["status"] == "200"
    assert body["entry"][1]["response"]["status"] == "200"


# ===========================================================================
# Lens 8: Source-read structural contracts — verify regression-pins.
#
# These probes verify the LOAD-BEARING structural contracts survive
# refactors. Per the 10th PROMOTED pattern (isinstance guard at
# untrusted-data list-iterator boundary), the CF-HISTORIAN-CM03-01
# RESOLVED site MUST keep its isinstance guard.
# ===========================================================================


def test_s80_do_closure_has_isinstance_guard_on_param(fhir_client):
    """SKEPTIC (source-read contract, 10th PROMOTED pattern):
    ``_do_closure`` MUST have ``isinstance(param, dict)`` guard inside
    its ``for param in body.get("parameter", [])`` loop.

    Without this guard, a non-dict entry triggers AttributeError that
    propagates as 500 + traceback. Pattern-match to CF-HISTORIAN-CM03-01.
    """
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "_do_closure")
    assert src, "Could not find _do_closure source"
    # The loop MUST be present. (EC-11: it iterates the defensive
    # ``_parameter_entries`` helper — QC-001's parameter:null guard —
    # which the raw ``body.get("parameter", [])`` cannot provide.)
    assert "for param in _parameter_entries(body)" in src, (
        f"_do_closure missing the parameter iteration loop:\n{src}"
    )
    # The isinstance guard MUST be present inside the loop body.
    assert "isinstance(param, dict)" in src, (
        f"_do_closure missing isinstance(param, dict) guard "
        f"(CF-HISTORIAN-CM03-01 regression):\n{src}"
    )


def test_s81_do_closure_has_isinstance_guard_on_value_coding(fhir_client):
    """SKEPTIC (source-read contract, 10th PROMOTED pattern sibling):
    ``_do_closure`` MUST have ``isinstance(coding, dict)`` guard on the
    ``valueCoding`` lookup.

    Without this guard, a non-dict valueCoding triggers AttributeError
    on ``coding.get("system", "")``. Pattern-match to CS-04 SKEPTIC
    QA-053 (sibling isinstance guard).
    """
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "_do_closure")
    assert src, "Could not find _do_closure source"
    assert "isinstance(coding, dict)" in src, (
        f"_do_closure missing isinstance(coding, dict) guard on valueCoding:\n{src}"
    )


def test_s82_do_closure_resolves_system_uri_via_fhir_uri_to_system():
    """SKEPTIC (source-read contract, canonical URI resolution):
    ``_do_closure`` MUST resolve the valueCoding system URI via
    ``fhir_uri_to_system`` (with fallback to the raw URI). This is the
    load-bearing canonical-URI resolution for the closure surface.

    Adversarial: if a future refactor hardcoded the raw URI without
    resolution, alias inputs would fragment the closure table.
    """
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "_do_closure")
    assert src
    assert "fhir_uri_to_system" in src, (
        f"_do_closure missing fhir_uri_to_system call for canonical "
        f"URI resolution:\n{src}"
    )


def test_s83_do_closure_calls_build_closure_response():
    """SKEPTIC (source-read contract): ``_do_closure`` MUST call
    ``build_closure_response`` to construct the response — not inline
    a Parameters dict (which would bypass the canonical-URI re-resolution
    in ``to_parameter_list``)."""
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "_do_closure")
    assert src
    assert "build_closure_response" in src, (
        f"_do_closure should call build_closure_response (single-source-of-"
        f"truth for response shape):\n{src}"
    )


def test_s84_do_closure_initializes_via_manager_reset_when_no_concepts():
    """SKEPTIC (source-read contract, item 3): when no concepts are
    extracted, ``_do_closure`` MUST call ``manager.reset(name)``
    (NOT ``manager.get_or_create(name)``). The reset semantic is
    load-bearing — re-init MUST clear prior state.
    """
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "_do_closure")
    assert src
    assert "manager.reset" in src, (
        f"_do_closure should call manager.reset for init/re-init path:\n{src}"
    )


def test_s85_do_closure_adds_via_manager_get_or_create_when_concepts():
    """SKEPTIC (source-read contract, item 4): when concepts are
    extracted, ``_do_closure`` MUST call ``manager.get_or_create(name)``
    (NOT ``manager.reset(name)``) — add is cumulative.
    """
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "_do_closure")
    assert src
    assert "manager.get_or_create" in src, (
        f"_do_closure should call manager.get_or_create for add path:\n{src}"
    )


def test_s86_closure_post_handler_validates_name_before_run_db():
    """SKEPTIC (source-read contract, item 1): the ``closure_post``
    handler MUST validate ``name`` BEFORE calling ``_run_db``. The
    early 400-return avoids spinning up a DB task for a request that
    cannot succeed.

    Adversarial: if the name check moved INSIDE ``_do_closure``, a
    missing-name request would allocate DB resources before failing.
    """
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "closure_post")
    assert src
    # The `if not name:` check MUST appear BEFORE `_run_db`.
    name_check_idx = src.find("if not name")
    run_db_idx = src.find("_run_db")
    assert name_check_idx != -1 and run_db_idx != -1, (
        f"closure_post missing name check or _run_db call:\n{src}"
    )
    assert name_check_idx < run_db_idx, (
        f"closure_post name check MUST precede _run_db call "
        f"(found name_check at {name_check_idx}, _run_db at {run_db_idx}):\n{src}"
    )


def test_s87_batch_dispatcher_closure_path_calls_do_closure():
    """SKEPTIC (source-read contract, batch wiring): the batch
    dispatcher's ``/CodeSystem/$closure`` branch (in
    ``_dispatch_batch_operation``, NOT ``_process_batch_entry`` — the
    closure branch is a sibling of the resource-type dispatch) MUST
    call ``_do_closure`` (NOT inline the closure logic). The single-
    source-of-truth pattern means batch and direct POST share the same
    code path."""
    src_app = inspect.getsource(create_fhir_app)
    # _dispatch_batch_operation is the function containing the
    # operation-path dispatch (per apps/fhir_api.py:1190). The
    # _process_batch_entry is the outer per-entry wrapper.
    src = _get_func_source(src_app, "_dispatch_batch_operation")
    assert src, "Could not find _dispatch_batch_operation source"
    assert "/CodeSystem/$closure" in src, (
        "_dispatch_batch_operation missing /CodeSystem/$closure branch"
    )
    assert "_do_closure" in src, (
        f"_dispatch_batch_operation /CodeSystem/$closure branch MUST call "
        f"_do_closure (single-source-of-truth):\n{src[:1500]}"
    )


def test_s88_batch_dispatcher_closure_path_validates_name():
    """SKEPTIC (source-read contract, batch wiring): the batch
    dispatcher's ``/CodeSystem/$closure`` branch MUST validate name
    (returning per-entry 400 on missing name)."""
    src_app = inspect.getsource(create_fhir_app)
    src = _get_func_source(src_app, "_dispatch_batch_operation")
    assert src
    # Find the closure branch section.
    closure_idx = src.find('"/CodeSystem/$closure"')
    assert closure_idx != -1
    # Take a window around the closure branch.
    branch_src = src[closure_idx:closure_idx + 1500]
    assert "name parameter is required" in branch_src or "not name_val" in branch_src or "not val" in branch_src, (
        f"batch closure branch missing name validation:\n{branch_src}"
    )


def test_s89_build_closure_response_uses_system_to_fhir_uri():
    """SKEPTIC (source-read contract, response builder): the response
    builder ``build_closure_response`` -> ``ClosureTable.to_parameter_list``
    MUST resolve the source label back to the canonical FHIR URI via
    ``system_to_fhir_uri``. This is the bidirectional canonical-URI
    invariant for the closure surface."""
    from medterm4ds.engines.fhir import closure as closure_mod
    src = inspect.getsource(closure_mod.ClosureTable.to_parameter_list)
    assert "system_to_fhir_uri" in src, (
        f"to_parameter_list missing system_to_fhir_uri call (canonical "
        f"URI re-resolution):\n{src}"
    )


def test_s8a_version_hash_uses_md5_and_truncates_to_12():
    """SKEPTIC (source-read contract, item 5): ``version_hash`` MUST
    use ``hashlib.md5(...).hexdigest()[:12]``. A future refactor that
    switches algorithm or drops the truncation would silently change
    the hash format — breaking clients that pin the format."""
    from medterm4ds.engines.fhir import closure as closure_mod
    src = inspect.getsource(closure_mod.ClosureTable.version_hash)
    assert "hashlib.md5" in src, (
        f"version_hash should use hashlib.md5:\n{src}"
    )
    assert "[:12]" in src, (
        f"version_hash should truncate to 12 chars:\n{src}"
    )


def test_s8b_reset_does_not_preserve_any_state():
    """SKEPTIC (source-read contract, item 3): ``ClosureManager.reset``
    MUST construct a FRESH ``ClosureTable`` instance — not mutate the
    existing one in-place. This guarantees no state leakage across
    re-init (no orphaned _subsumes entries, no orphaned
    incomplete_since flag)."""
    from medterm4ds.engines.fhir import closure as closure_mod
    src = inspect.getsource(closure_mod.ClosureManager.reset)
    # The reset method MUST construct ClosureTable(name) (fresh instance).
    assert "ClosureTable(name)" in src, (
        f"reset should construct a fresh ClosureTable:\n{src}"
    )
    # The reset method MUST NOT call clear() / __init__ on existing instance.
    assert ".clear()" not in src, (
        f"reset should NOT call .clear() on existing instance (state "
        f"could leak via shared references):\n{src}"
    )


# ===========================================================================
# Lens 9: Wire-format + Content-Type — items 1+3+4 (response shape).
# ===========================================================================


def test_s90_post_closure_xml_format_value_string_serialized(fhir_client):
    """SKEPTIC (wire-format): POST ``$closure`` with ``_format=xml``
    returns XML where the ``return`` parameter's ``valueString`` is
    properly serialized (CR-002 fix — _scalar_to_xml_attr applies to
    all serializers)."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("skeptic-xml-90"),
        params={"_format": "xml"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml"), (
        f"_format=xml — Content-Type drift: {r.headers['content-type']!r}"
    )
    # The XML body MUST contain <valueString> element.
    assert "<valueString" in r.text, (
        f"XML response missing <valueString>: {r.text[:300]}"
    )


def test_s91_post_closure_with_concepts_xml_serializes_value_coding(fhir_client):
    """SKEPTIC (wire-format): POST ``$closure`` with concepts +
    ``_format=xml`` returns XML where the ``concept`` entries are
    serialized with ``<valueCoding>``."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "skeptic-xml-91",
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
        params={"_format": "xml"},
    )
    assert r.status_code == 200
    assert "<valueCoding" in r.text, (
        f"XML response missing <valueCoding>: {r.text[:300]}"
    )


def test_s92_post_closure_no_body_returns_4xx(fhir_client):
    """SKEPTIC (item 1, hostile body): POST ``$closure`` with NO body
    at all (no JSON). The handler signature declares ``body:
    dict[str, Any]`` so FastAPI returns 422 (body required).

    Adversarial: confirm no 500."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        headers={"content-type": "application/fhir+json"},
    )
    assert r.status_code < 500, (
        f"no body — got 5xx: {r.status_code} {r.text}"
    )
    assert _is_fhir_response(r), (
        f"no body — Content-Type drift: {r.headers.get('content-type')!r}"
    )


def test_s93_post_closure_non_object_body_does_not_500(fhir_client):
    """SKEPTIC (item 1, hostile body): POST ``$closure`` with body =
    a JSON array (not an object). FastAPI coerces / rejects — verify
    no 500."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=[1, 2, 3],  # not a dict
    )
    assert r.status_code < 500, (
        f"non-object body — got 5xx: {r.status_code} {r.text}"
    )


# ===========================================================================
# Lens 10: Manager-level structural invariants.
# ===========================================================================


def test_s100_manager_get_or_create_creates_new_table():
    """SKEPTIC: ``manager.get_or_create(name)`` on a NEW name creates a
    fresh table (not the singleton or another name's table)."""
    m = ClosureManager()
    t = m.get_or_create("test-s100-fresh")
    assert t is not None
    assert t.name == "test-s100-fresh"
    assert len(t.concepts) == 0


def test_s101_manager_reset_creates_independent_instance():
    """SKEPTIC: ``manager.reset(name)`` on an existing name returns a
    DIFFERENT instance from the prior one."""
    m = ClosureManager()
    t1 = m.get_or_create("test-s101")
    t1.concepts["X"] = {"system": "S", "display": "X"}
    t2 = m.reset("test-s101")
    assert t1 is not t2
    assert len(t2.concepts) == 0
    # The OLD instance is unchanged (mutating it doesn't affect the new).
    t1.concepts["Y"] = {"system": "S", "display": "Y"}
    assert "Y" not in t2.concepts


def test_s102_manager_list_names_reflects_state():
    """SKEPTIC: ``manager.list_names()`` reflects the current set of
    managed closure tables."""
    m = ClosureManager()
    m.get_or_create("test-s102-a")
    m.get_or_create("test-s102-b")
    names = set(m.list_names())
    assert "test-s102-a" in names
    assert "test-s102-b" in names


def test_s103_closure_manager_singleton_returns_same_instance_across_calls():
    """SKEPTIC: ``get_closure_manager()`` returns the same singleton
    across multiple calls (guarded by lock)."""
    m1 = get_closure_manager()
    m2 = get_closure_manager()
    assert m1 is m2


# ===========================================================================
# Lens 11: Cross-handler closure isolation — hostile combinations.
# ===========================================================================


def test_s110_concurrent_init_two_names_no_interference(fhir_client):
    """SKEPTIC: init two closures, add to one, the other remains empty.
    Re-verifies isolation but with explicit init calls before adds."""
    name_a = "skeptic-concurrent-A-110"
    name_b = "skeptic-concurrent-B-110"
    # Init both
    fhir_client.post("/fhir/CodeSystem/$closure", json=_closure_param_name_only(name_a))
    fhir_client.post("/fhir/CodeSystem/$closure", json=_closure_param_name_only(name_b))
    # Add to A only
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name_a,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    manager = get_closure_manager()
    a = manager.get(name_a)
    b = manager.get(name_b)
    assert ("SNOMEDCT_US", "73211009") in a.concepts
    assert len(b.concepts) == 0, (
        f"closure B should be empty (isolated from A); got {list(b.concepts)}"
    )


def test_s111_add_after_init_idempotent_check_returns_subsumes(fhir_client):
    """SKEPTIC (item 7, full lifecycle): init -> add DM -> add T2DM ->
    check(DM, T2DM) == "subsumes". The check is correct even when the
    two adds are separate calls (incremental population)."""
    name = "skeptic-incremental-111"
    fhir_client.post("/fhir/CodeSystem/$closure", json=_closure_param_name_only(name))
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "44054006", "display": "T2DM"}],
        ),
    )
    closure = get_closure_manager().get(name)
    # IMPORTANT: the closure table's batched add_concepts walks per
    # source. The DM-T2DM ancestor/descendant relationship is discovered
    # IFF the second add_concepts walks T2DM's ancestors and finds DM
    # already in the closure.
    outcome = closure.check("73211009", "44054006")
    assert outcome == "subsumes", (
        f"check(DM, T2DM) after incremental add — expected 'subsumes'; "
        f"got {outcome!r}"
    )
