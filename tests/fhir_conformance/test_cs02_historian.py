"""HISTORIAN iteration CS-02 — pattern-match against prior bug registry.

Spec: https://build.fhir.org/codesystem-operation-lookup.html
       https://hl7.org/fhir/R4/codesystem-operation-lookup.html (canonical R4)

HISTORIAN lens for CS-02 ($lookup):

1. **Alternative-failure path inside ``_do_lookup``** (carry-forward from
   SKEPTIC CS-02; TS-04 HISTORIAN QA-038 "silent-wrong-answer at
   error-isolation boundary" class):
   - The ``_run_db`` wrapper offloads to a single-worker executor but
     does NOT catch exceptions. If a malformed ``pf_cache`` entry causes
     ``pf.get(...)`` to raise ``AttributeError`` (e.g. a per-code value
     that is a list, not a dict), the exception propagates to FastAPI
     unhandled → HTTP 500 with ``text/plain`` body — no OperationOutcome,
     no FHIR Content-Type, no per-request error isolation.

2. **Silent-fallback patterns** (v0.0.1 B-class + CS-01 HISTORIAN QA-044):
   - Re-audit ``_do_lookup`` for broad ``except Exception`` or
     DEBUG/INFO-level swallowing. The function has no try/except today,
     which means malformed ``pf_cache`` data leaks as raw 500 (issue 1).

3. **Documentation-vs-implementation drift** (TS-01 HISTORIAN QA-007):
   - The ``_do_lookup`` docstring (extended in CS-01 TERMINOLOGIST
     QA-045) names five custom properties: ``patient-friendly``,
     ``match-type``, ``canonical-code``, ``canonical-system``, ``tty``.
   - Verify the implementation actually emits all five when present in
     ``pf_cache`` data, and that the docstring's contract (server-local
     vocabulary for ``match-type``) is honored.

4. **Re-test SKEPTIC's "no bugs" claim** (Test-too-lenient pattern):
   - Re-audit ``test_s20`` (GET-vs-POST byte-exact parity) for whether
     it asserts enough — specifically, that the assertion is on body
     content (not just status code).
   - Re-audit ``test_s70`` (Out ``property`` shape has code+value parts)
     — verify the part types match what the impl emits today.

5. **CR-007 from review-5** (relevant to $lookup):
   - ``_do_*`` handlers echo client-supplied ``system_uri`` verbatim in
     the response even when the client passed an alias (``urn:oid:...``)
     or a trailing-slash variant. Verify the Out ``system`` parameter
     for $lookup.
   - This is the "client-input-as-canonical" drift pattern
     (TS-02 TERMINOLOGIST QA-029 shape — recurring count).

6. **Property parameter handling**:
   - When ``property=foo`` is requested for a property that doesn't
     exist, the server returns the full default set anyway (already
     documented in AGENTS.md NOT A BUG Registry).
   - When ``property=name`` is requested, the value is the code system's
     FHIR display name (URI-derived via ``_system_display_name``), not
     the raw SAB — verify.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"


# ---------------------------------------------------------------------------
# Local fixture: build an isolated FHIR app with a controlled PF cache.
# Avoids mutating the shared module-scoped `fhir_client` fixture state.
# ---------------------------------------------------------------------------

def _build_app_with_pf(pf_data: dict | None, tmp_path: Path):
    """Construct a FHIR app with a controlled patient-friendly baseline dir.

    Writes the given ``pf_data`` (keyed by source-lower) to JSON files in
    a fresh baseline directory and points the app at it via env var.
    Returns ``(app, baseline_path)`` so the caller can keep the env var
    set for the duration of the TestClient lifespan (the cache loads
    inside the lifespan, not at app construction).
    """
    import duckdb

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

    baseline = tmp_path / "pf_baseline"
    baseline.mkdir(parents=True, exist_ok=True)
    if pf_data:
        for source_lower, payload in pf_data.items():
            (baseline / f"patient_friendly_{source_lower}.json").write_text(
                json.dumps(payload)
            )

    db_path = tmp_path / "umls.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE mrconso (CODE VARCHAR, TTY VARCHAR, STR VARCHAR, "
        "AUI VARCHAR, SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR)"
    )
    con.execute(
        "INSERT INTO mrconso VALUES "
        "('73211009','PT','Diabetes mellitus','A1','N','SNOMEDCT_US','C0011849'), "
        "('44054006','PT','Type 2 diabetes mellitus','A2','N','SNOMEDCT_US','C0011847')"
    )
    con.execute("CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR)")
    con.execute("INSERT INTO mrrel VALUES ('A2','A1','isa','PAR')")
    con.close()

    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(tmp_path / "no_index"),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    return app, baseline


def _run_with_env(client_fn, baseline: Path):
    """Deprecated stub — kept to avoid breaking imports if referenced. Use
    the fixture pattern directly (yield inside the env-var scope)."""
    raise NotImplementedError("use the fixture pattern directly")


@pytest.fixture
def historian_client(tmp_path):
    """FHIR app with NO patient-friendly data loaded (clean baseline)."""
    app, baseline = _build_app_with_pf(None, tmp_path)
    old = os.environ.get("MEDTERM4DS_FHIR4PX_BASELINE")
    os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = str(baseline)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        if old is None:
            os.environ.pop("MEDTERM4DS_FHIR4PX_BASELINE", None)
        else:
            os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = old


@pytest.fixture
def malformed_pf_client(tmp_path):
    """FHIR app where the snomedct_us PF JSON has a malformed per-code value
    (a list instead of a dict). Reproduces the AttributeError-in-_do_lookup
    failure path."""
    app, baseline = _build_app_with_pf(
        {"snomedct_us": {SNOMED_DIABETES_MELLITUS: ["not", "a", "dict"]}},
        tmp_path,
    )
    old = os.environ.get("MEDTERM4DS_FHIR4PX_BASELINE")
    os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = str(baseline)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        if old is None:
            os.environ.pop("MEDTERM4DS_FHIR4PX_BASELINE", None)
        else:
            os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = old


@pytest.fixture
def valid_pf_client(tmp_path):
    """FHIR app with a valid, complete PF entry for the seeded SNOMED code."""
    app, baseline = _build_app_with_pf(
        {
            "snomedct_us": {
                SNOMED_DIABETES_MELLITUS: {
                    "name": "high blood sugar",
                    "match_type": "exact",
                    "canonical_code": "E11",
                    "canonical_system": "icd10",
                    "tty": "PT",
                }
            }
        },
        tmp_path,
    )
    old = os.environ.get("MEDTERM4DS_FHIR4PX_BASELINE")
    os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = str(baseline)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        if old is None:
            os.environ.pop("MEDTERM4DS_FHIR4PX_BASELINE", None)
        else:
            os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = old


# ---------------------------------------------------------------------------
# QA-046: CRITICAL — Malformed pf_cache entry raises AttributeError inside
# _do_lookup, surfacing as HTTP 500 with text/plain body. No OperationOutcome,
# no FHIR Content-Type, no per-request error isolation.
#
# Pattern class: TS-04 HISTORIAN QA-038 ("silent-wrong-answer at
# error-isolation boundary") + v0.0.1 review B-class silent-fallback. The
# _run_db wrapper is the boundary; exceptions inside _do_lookup MUST be
# translated to a per-request OperationOutcome, NOT leak as raw 500.
#
# Spec: FHIR R4 §3.1.0.1.5 (OperationOutcome MAY be returned with any
# 4xx/5xx) + §3.1.0.1.9 (correct MIME type SHALL be used).
# ---------------------------------------------------------------------------

def test_h01_malformed_pf_entry_returns_operationoutcome_not_500(malformed_pf_client):
    """QA-046 / CRITICAL (post-fix verification).

    A malformed patient-friendly cache entry (per-code value is a list,
    not a dict) MUST NOT cause the $lookup handler to leak an unhandled
    exception. Pre-fix: the impl raised ``AttributeError`` inside
    ``_do_lookup``, which propagated past the route handler (which only
    checks ``isinstance(payload, Response)``) to FastAPI's default 500
    with ``text/plain`` body.

    Post-fix: ``_do_lookup`` defensively guards with ``isinstance(pf,
    dict)`` and skips custom-property enrichment when the entry is
    malformed. The Out ``property`` group is 0..* per FHIR R4 §4.8.21.1,
    so absence is spec-conformant; the lookup still succeeds with the
    engine's canonical data. A WARNING log is emitted as the operator
    signal (per GLOBAL_RULES.md "Silent Fallbacks" — INFO/DEBUG would
    hide the data-quality issue).

    Acceptance criteria (post-fix):
      - status 200 (not 500) — the code itself is valid; only PF
        enrichment is skipped
      - Content-Type: application/fhir+json
      - body: Parameters resource with the engine's canonical display
        (NOT OperationOutcome — the malformed PF data does not block
        the core lookup)
      - no `patient-friendly` / `match-type` / `canonical-*` / `tty`
        custom properties emitted (the malformed entry was skipped)
    """
    r = malformed_pf_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code == 200, (
        f"Malformed pf_cache entry MUST NOT cause a 500 — the code is "
        f"valid; only PF enrichment is skipped. Got {r.status_code}. "
        f"Pre-fix: this returned 500 with text/plain body (CRITICAL bug)."
    )
    ct = r.headers.get("content-type", "")
    assert "fhir+json" in ct, (
        f"Response Content-Type MUST be application/fhir+json; got {ct!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"Body MUST be a Parameters resource (the lookup succeeded); "
        f"got resourceType={body.get('resourceType')!r}"
    )
    # Verify the malformed entry's custom properties were NOT emitted.
    prop_codes = {
        p["part"][0]["valueCode"]
        for p in body.get("parameter", [])
        if p.get("name") == "property"
    }
    pf_props = {"patient-friendly", "match-type", "canonical-code", "canonical-system"}
    assert not (prop_codes & pf_props), (
        f"Malformed pf_cache entry MUST NOT emit custom properties; "
        f"found {prop_codes & pf_props}. The entry should be skipped "
        f"with a WARNING log."
    )
    # Verify the canonical `display` (engine preferred term) is present.
    display_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "display"),
        None,
    )
    assert display_param is not None and display_param.get("valueString") == "Diabetes mellitus"


def test_h02_pf_entry_not_a_dict_does_not_crash_when_code_absent(historian_client, tmp_path):
    """QA-046 regression — confirm that when no pf_cache entry exists for the
    requested code, lookup works fine (negative control). The bug is
    specifically when a malformed entry IS present."""
    r = historian_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": "9999999999"},  # not in pf_cache
    )
    assert r.status_code == 200, (
        f"Lookup for code absent from pf_cache MUST return 200 (no error); "
        f"got {r.status_code}. Body: {r.text[:200]}"
    )


# ---------------------------------------------------------------------------
# QA-047: MEDIUM — CR-007 from review-5 applies to $lookup. The Out
# `system` parameter echoes the client-supplied URI verbatim when the
# client passes an alias (urn:oid:...) or a trailing-slash variant.
#
# Pattern class: TS-02 TERMINOLOGIST QA-029 "client-input-as-canonical"
# drift (recurring count=3 after this confirmation). The Out `system`
# has a server-canonical source (SYSTEM_TO_FHIR_URI registry) and SHOULD
# prefer the canonical value over the client input echo.
#
# Spec: FHIR R4 §4.8.21.1 Out `system` ("The requested system" —
# ambiguous, but the canonical-URI cross-check probe class from TS-01
# TERMINOLOGIST QA-012 establishes the registry-canonical contract).
# ---------------------------------------------------------------------------

def test_h10_lookup_system_out_is_canonical_not_alias(historian_client):
    """QA-047 / MEDIUM.

    The Out `system` parameter MUST be the canonical FHIR URI (from
    SYSTEM_TO_FHIR_URI registry), not the client-supplied alias. The
    implementation today passes ``system_uri`` (the raw client input)
    verbatim to ``build_parameters_lookup``.

    Pattern: client-input-as-canonical drift (TS-02 TERMINOLOGIST
    QA-029 shape). The spec says Out `system` is "The requested system"
    — but the canonical-URI cross-check contract (TS-01 TERMINOLOGIST
    QA-012) requires that a registry-canonical value MUST be emitted
    when one exists.

    Reproduction:
      - GET with system=urn:oid:2.16.840.1.113883.6.96 → Out `system`
        is the alias verbatim (BUG).
      - GET with system=http://snomed.info/sct/ (trailing slash) → Out
        `system` is the trailing-slash variant verbatim (BUG).
      - Spec-correct: Out `system` is always
        ``system_to_fhir_uri(fhir_uri_to_system(input))``.
    """
    r = historian_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "urn:oid:2.16.840.1.113883.6.96",  # SNOMED CT alias
            "code": SNOMED_DIABETES_MELLITUS,
        },
    )
    assert r.status_code == 200, (
        f"Lookup with urn:oid alias MUST resolve; got {r.status_code}"
    )
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert sys_param is not None, "Out `system` parameter MUST be present"
    # Spec-correct: canonical URI from SYSTEM_TO_FHIR_URI.
    assert sys_param.get("valueUri") == SNOMED_URI, (
        f"Out `system` MUST be the canonical FHIR URI ({SNOMED_URI}), not "
        f"the client-supplied alias. Got {sys_param.get('valueUri')!r}. "
        f"Pattern: client-input-as-canonical drift (TS-02 TERMINOLOGIST "
        f"QA-029 shape). Fix: in _do_lookup, re-resolve to canonical via "
        f"`system_to_fhir_uri(fhir_uri_to_system(system_uri)) or system_uri`."
    )


def test_h11_lookup_system_out_canonical_for_trailing_slash(historian_client):
    """QA-047 regression — trailing-slash variant of the canonical URI."""
    r = historian_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "http://snomed.info/sct/",  # trailing slash
            "code": SNOMED_DIABETES_MELLITUS,
        },
    )
    assert r.status_code == 200, (
        f"Lookup with trailing-slash URI MUST resolve; got {r.status_code}"
    )
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert sys_param is not None
    assert sys_param.get("valueUri") == SNOMED_URI, (
        f"Out `system` MUST be the canonical URI without trailing slash; "
        f"got {sys_param.get('valueUri')!r}"
    )


def test_h12_lookup_system_out_canonical_when_already_canonical(historian_client):
    """QA-047 negative control — when client passes the canonical URI, Out
    `system` is the same canonical URI (no drift). This confirms the
    fix does not double-translate canonical URIs."""
    r = historian_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert sys_param is not None
    assert sys_param.get("valueUri") == SNOMED_URI


# ---------------------------------------------------------------------------
# QA-048: LOW — _do_lookup docstring (CS-01 TERMINOLOGIST QA-045) lists
# five custom properties; verify the implementation emits ALL FIVE when
# present in pf_cache data. Pattern: TS-01 HISTORIAN QA-007
# docstring-vs-implementation drift.
# ---------------------------------------------------------------------------

def test_h20_do_lookup_emits_all_documented_custom_properties(valid_pf_client):
    """QA-048 / LOW.

    The ``_do_lookup`` docstring (extended in CS-01 TERMINOLOGIST
    QA-045) names five custom properties:
      - patient-friendly (string)
      - match-type (code, server-local vocabulary)
      - canonical-code (code)
      - canonical-system (uri, translated via sab_label_to_fhir_uri)
      - tty (code)

    Verify the implementation emits ALL FIVE when present in pf_cache.
    Pattern: TS-01 HISTORIAN QA-007 docstring-vs-implementation drift.
    """
    r = valid_pf_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    assert r.status_code == 200, f"Unexpected status: {r.status_code}"
    body = r.json()
    prop_codes = {
        p["part"][0]["valueCode"]
        for p in body.get("parameter", [])
        if p.get("name") == "property"
    }
    expected = {"patient-friendly", "match-type", "canonical-code", "canonical-system", "tty"}
    missing = expected - prop_codes
    assert not missing, (
        f"_do_lookup docstring documents these custom properties but the "
        f"implementation did not emit them: {missing}. "
        f"Pattern: TS-01 HISTORIAN QA-007 docstring drift."
    )


def test_h21_do_lookup_canonical_system_translated_from_sab(valid_pf_client):
    """QA-048 regression — canonical-system is the FHIR URI
    (http://hl7.org/fhir/sid/icd-10-cm), NOT the raw SAB label (icd10).
    Verifies CS-01 SKEPTIC QA-043 fix + HISTORIAN QA-044 WARNING path."""
    r = valid_pf_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    cs_param = next(
        (
            p
            for p in body.get("parameter", [])
            if p.get("name") == "property"
            and p.get("part", [{}])[0].get("valueCode") == "canonical-system"
        ),
        None,
    )
    assert cs_param is not None, "canonical-system property MUST be emitted"
    value_part = next(
        (pp for pp in cs_param["part"] if pp.get("name") == "value"), None
    )
    assert value_part is not None
    assert value_part.get("valueString") == "http://hl7.org/fhir/sid/icd-10-cm", (
        f"canonical-system MUST be translated FHIR URI, not raw SAB; "
        f"got {value_part.get('valueString')!r}"
    )


# ---------------------------------------------------------------------------
# QA-049: MEDIUM — `_property_param` always emits `valueString` and coerces
# via `str(value)`. For boolean/integer pf values (e.g. match_type=42),
# the impl silently emits `valueString: "42"`. The FHIR spec permits
# `value[x]` (any type), but mixing primitive types per property code is
# a serialization drift risk. Document the current behavior; not a bug
# per FHIR R4 §4.8.21.1 (property.value is 0..1 value[x]).
# ---------------------------------------------------------------------------

def test_h30_property_value_always_value_string_for_documented_types(valid_pf_client):
    """QA-049 / LOW (documented behavior).

    ``_property_param`` (engines/fhir/responses.py) always emits the value
    as ``valueString`` and coerces via ``str(value)``. FHIR R4 §4.8.21.1
    permits ``value[x]`` (any type) — valueString is conformant. This
    probe documents the behavior: even when the source data has an
    integer or boolean, the wire form is valueString.

    Not a bug — spec-permissive. Documented to catch future drift if a
    property type should be valueCode/valueBoolean/valueInteger.
    """
    r = valid_pf_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    body = r.json()
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        value_part = next(
            (pp for pp in p["part"] if pp.get("name") == "value"), None
        )
        assert value_part is not None
        # valueString is the only value[x] key present (current impl).
        value_keys = [k for k in value_part if k.startswith("value")]
        assert value_keys == ["valueString"], (
            f"_property_param always emits valueString; got keys {value_keys}. "
            f"If a future property needs valueCode/valueBoolean/etc., update "
            f"the builder (engines/fhir/responses.py:_property_param)."
        )


# ---------------------------------------------------------------------------
# QA-050: Test-too-lenient audit on test_s20 (GET-vs-POST byte-exact
# parity). The probe compares r_get.json() == r_post.json() — a positive
# body-equality assertion, not a status-code-only check. Confirm.
# ---------------------------------------------------------------------------

def test_h40_test_s20_asserts_body_equality_not_just_status(fhir_client):
    """QA-050 / PASS (test-too-lenient audit).

    Re-audit SKEPTIC's ``test_s20_get_and_post_lookup_with_system_code_produce_equal_body``
    per the Test-too-lenient pattern (TS-03 HISTORIAN QA-034). The probe
    asserts ``r_get.json() == r_post.json()`` — a positive body-equality
    check on the full Parameters resource. NOT a negative-only trap.

    This confirmation probe just re-runs the same check to ensure no
    regression has been introduced since SKEPTIC.
    """
    r_get = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
    )
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_DIABETES_MELLITUS},
            ],
        },
    )
    assert r_get.status_code == 200 and r_post.status_code == 200
    # POSITIVE success-shape: body equality (not just status code).
    assert r_get.json() == r_post.json()


# ---------------------------------------------------------------------------
# QA-051: SKEPTIC carry-forward — `_do_lookup` has no try/except today.
# Confirm the absence of broad except Exception / DEBUG-swallow by
# source-level AST audit. Pattern: v0.0.1 B-class silent-fallback
# (negative control — confirms _do_lookup does NOT swallow).
# ---------------------------------------------------------------------------

def test_h51_do_lookup_has_no_broad_except_or_debug_swallow():
    """QA-051 / PASS (negative control).

    Source-level audit: ``_do_lookup`` (apps/fhir_api.py) has NO
    try/except blocks at all today. This means:
      - No broad ``except Exception`` swallowing.
      - No DEBUG-level error swallowing.
      - But ALSO: no error isolation when pf_cache data is malformed
        (which is QA-046).

    Pattern: v0.0.1 B-class silent-fallback audit. The function does
    NOT swallow — but it also does not isolate, which is the opposite
    failure mode.
    """
    import ast

    src_path = Path(__file__).resolve().parents[2] / "src" / "medterm4ds" / "apps" / "fhir_api.py"
    tree = ast.parse(src_path.read_text())
    do_lookup = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_do_lookup":
            do_lookup = node
            break
    assert do_lookup is not None, "_do_lookup function not found"

    has_try = any(isinstance(n, ast.Try) for n in ast.walk(do_lookup))
    assert not has_try, (
        "_do_lookup contains a try/except block — re-audit for broad "
        "Exception catches or DEBUG-level swallowing per GLOBAL_RULES.md "
        "'Silent Fallbacks'. (None today — this assertion documents the "
        "absence.)"
    )


# ---------------------------------------------------------------------------
# QA-052: SKEPTIC carry-forward — confirm the _run_db wrapper boundary
# does NOT translate exceptions. The wrapper is the natural place to add
# error isolation (catch duckdb.Error and AttributeError-from-malformed-pf
# and translate to OperationOutcome).
# ---------------------------------------------------------------------------

def test_h52_run_db_does_not_translate_exceptions():
    """QA-052 / PASS (documents the boundary contract).

    The ``_run_db`` wrapper (apps/_asyncutil.py) offloads to a single-worker
    ThreadPoolExecutor via ``loop.run_in_executor``. It does NOT catch
    exceptions — the caller (the route handler) is responsible for
    translation.

    Today the route handler ``lookup_get`` / ``lookup_post`` does:
      ``return payload if isinstance(payload, Response) else _fhir_response(...)``
    which assumes ``payload`` is always a dict or a Response. When
    ``_do_lookup`` raises, the exception propagates past this line to
    FastAPI's default 500 handler — producing text/plain "Internal
    Server Error" (QA-046).

    Documents: the fix for QA-046 belongs either inside ``_do_lookup``
    (try/except around the pf_cache access) OR in a wrapper between
    ``_run_db`` and the route handler. This probe confirms the wrapper
    is a no-op today — the fix must be added.
    """
    import ast

    src_path = Path(__file__).resolve().parents[2] / "src" / "medterm4ds" / "apps" / "_asyncutil.py"
    tree = ast.parse(src_path.read_text())
    run_db = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_db":
            run_db = node
            break
    assert run_db is not None
    has_try = any(isinstance(n, ast.Try) for n in ast.walk(run_db))
    assert not has_try, (
        "run_db now has try/except — re-audit: the wrapper is the boundary "
        "between the executor thread and the FastAPI route. If it now "
        "catches, the catch MUST be narrow (duckdb.Error + AttributeError "
        "for malformed pf_cache) and translate to a Response carrying an "
        "OperationOutcome — NOT swallow."
    )
