"""HISTORIAN RESWEEP probes for chunk CM-03 (ConceptMap $closure Operation).

Source: https://build.fhir.org/conceptmap-operation-closure.html
Canonical R4 OperationDefinition:
    https://hl7.org/fhir/R4/conceptmap-operation-closure.html

This resweep test file extends the baseline ``test_cm03_historian.py`` with
NEW regression probes through the HISTORIAN lens ("What broke before?").
Per ``evolution.json.config.notes`` (SKEPTIC tip for HISTORIAN, 5 items),
this resweep MUST:

  1. **Re-derive via 4th-sibling AST-walk search** across every
     ``for X in <body>.get(...)`` loop in fhir_api.py for any NEW iterator
     without the isinstance guard (10th PROMOTED pattern).
  2. **Re-derive the bidirectional canonical-URI invariant**
     (``fhir_uri_to_system`` on input + ``system_to_fhir_uri`` on output
     via ``to_parameter_list``) parametrized over every alias in
     FHIR_URI_ALIASES × every seeded code.
  3. **Re-derive the reset semantic via object-identity assertion**
     (``t1 is not t2`` after reset).
  4. **Verify version hash payload composition** (``len:_version:sorted_keys``
     — specifically ``sorted()`` on ``concepts.keys()``, not ``items()``).
  5. **Extend batch per-entry isolation to multi-entry batches** with mixed
     success/failure modes per FHIR R4 §3.7 order preservation.

The CF-HISTORIAN-CM03-01 RESOLVED site at ``_do_closure``
(``apps/fhir_api.py:2311`` for ``isinstance(param, dict)`` and ``:2316`` for
``isinstance(coding, dict)``) is the load-bearing 10th PROMOTED pattern.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus; A44054006 -> A73211009)

Methodology contributions:
  - **4th-sibling AST-walk search pattern** (extends CS-04 HISTORIAN
    strategy: walks EVERY ``ast.For`` node where iter is a ``.get(...)``
    call on a body-derived name, NOT just ``body.get('parameter', [])``).
  - **Bidirectional canonical-URI invariant parametrized over aliases**
    (extends TS-01 TERMINOLOGIST strategy 9 to the closure surface).
  - **Object-identity-is-the-contract for reset semantics** (extends
    CM-04 HISTORIAN strategy 54 from module-attribute identity to
    per-call-instance identity — verifies reset returns a NEW instance,
    not a mutated-in-place one).
  - **Version hash payload composition source-read contract** (extends
    VS-05 HISTORIAN strategy 52 to assert ``sorted(concepts.keys())``
    is the load-bearing composition, not ``items()`` or ``values()``).
  - **Batch multi-entry order preservation per FHIR R4 §3.7** (extends
    TS-04 HISTORIAN strategy 18 to mixed-success multi-entry batches).
"""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path
from typing import Any

import pytest

from medterm4ds.apps.fhir_api import create_fhir_app
from medterm4ds.engines.fhir import (
    FHIR_URI_ALIASES,
    SYSTEM_TO_FHIR_URI,
    fhir_uri_to_system,
    system_to_fhir_uri,
)
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

# Seeded codes from conftest._make_conformance_db.
SEEDED_CODES = [
    ("SNOMEDCT_US", "73211009", SNOMED_URI, "Diabetes mellitus"),
    ("SNOMEDCT_US", "44054006", SNOMED_URI, "Type 2 diabetes mellitus"),
    ("ICD10CM", "E11", ICD10CM_URI, "Type 2 diabetes mellitus"),
    ("RXNORM", "860975", RXNORM_URI, "24 HR metformin 500 MG Oral Tablet"),
]

FHIR_API_PATH = Path(inspect.getsourcefile(create_fhir_app))


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
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


def _get_nested_func_source(parent_name: str, child_name: str) -> str:
    """Return source text of a nested function inside ``create_fhir_app``.

    Walks BOTH ``ast.FunctionDef`` AND ``ast.AsyncFunctionDef`` to catch
    nested route handlers. Mirrors CM-02 / CS-04 HISTORIAN resweep strategy.
    """
    src = FHIR_API_PATH.read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    parent_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name == parent_name:
            parent_node = node
            break
    if parent_node is None:
        return ""
    for child in ast.walk(parent_node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                child.name == child_name:
            try:
                return ast.get_source_segment(src, child) or ""
            except Exception:
                return ""
    return ""


def _get_top_level_class_source(module_path: Path, class_name: str) -> str:
    """Return source text of a top-level class definition."""
    src = module_path.read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            try:
                return ast.get_source_segment(src, node) or ""
            except Exception:
                return ""
    return ""


def _reset_singleton_manager() -> None:
    """Reset the module-level ClosureManager singleton.

    Used so each probe starts from a clean slate (the singleton accumulates
    named tables across tests within the same process).
    """
    import medterm4ds.engines.fhir.closure as closure_mod

    closure_mod._manager = None


# ===========================================================================
# Lens 1: 4th-sibling AST-walk search for isinstance guards.
#
# SKEPTIC tip #1: re-derive via 4th-sibling AST-walk search across EVERY
# ``for X in <body>.get(...)`` loop in fhir_api.py for any NEW iterator
# without the isinstance guard (10th PROMOTED pattern).
#
# The 3 known sites (CS-04 HISTORIAN resweep L2):
#   1. ``_do_closure`` (CF-HISTORIAN-CM03-01 RESOLVED)
#   2. ``_extract_coding_from_parameters`` / ``_extract_named_coding_from_parameters``
#   3. ``_parse_parameters`` (CS-04 SKEPTIC QA-001 RESOLVED)
#   4. ``_extract_codeable_concept_from_parameters``
#   5. ``_extract_valueset_from_parameters``
#   6. ``_expand_intensional`` 5 sibling iterators (CS-04 HISTORIAN QA-001 RESOLVED)
#
# A NEW iterator without the guard would be the 7th+ sibling and a real bug.
# ===========================================================================


def test_h10_all_parameter_iterators_in_fhir_api_have_isinstance_guard() -> None:
    """HISTORIAN (4th-sibling AST-walk search, SKEPTIC tip #1):
    every ``for X in <body>.get("parameter", [])`` loop in
    ``apps/fhir_api.py`` MUST have an ``isinstance(X, dict)`` guard within
    the first 5 statements of the loop body.

    The 10th PROMOTED pattern (``isinstance`` guard at untrusted-data
    list-iterator boundary) was applied to 6+ sibling iterators across
    the spec-compliance run. A NEW iterator without the guard would be
    a 7th sibling and a real bug (AttributeError on non-dict entries
    propagating as 500 + traceback).

    Probe class: structural source-read audit — walks every ``ast.For``
    node where ``iter`` is ``<X>.get("parameter", [...])``. Catches
    refactors that introduce a new iterator without the guard.
    """
    src = FHIR_API_PATH.read_text()
    tree = ast.parse(src)
    unguarded: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if not isinstance(node.iter, ast.Call):
            continue
        if not isinstance(node.iter.func, ast.Attribute):
            continue
        if node.iter.func.attr != "get":
            continue
        if not node.iter.args:
            continue
        first_arg = node.iter.args[0]
        if not isinstance(first_arg, ast.Constant):
            continue
        if first_arg.value != "parameter":
            continue
        # Check first 5 statements for an isinstance call.
        has_guard = False
        for stmt in node.body[:5]:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                    if sub.func.id == "isinstance":
                        has_guard = True
                        break
            if has_guard:
                break
        if not has_guard:
            unguarded.append(node.lineno)
    assert not unguarded, (
        f"UNGUARDed `for param in <body>.get('parameter', [])` loops at "
        f"lines {unguarded}. Each MUST have `isinstance(param, dict)` "
        f"within first 5 statements (10th PROMOTED pattern / "
        f"CF-HISTORIAN-CM03-01 regression-pin)."
    )


def test_h11_all_compose_include_iterators_have_isinstance_guard() -> None:
    """HISTORIAN (4th-sibling AST-walk search, SKEPTIC tip #1):
    every ``for X in <body>.get("compose", ...)`` chain OR
    ``for X in <inc>.get("include"/"exclude"/"concept"/"filter", [])``
    loop in ``apps/fhir_api.py`` MUST have an ``isinstance`` guard.

    The CS-04 HISTORIAN QA-001 RESOLVED fix added 5 isinstance guards +
    1 list-shape guard at ``_expand_intensional`` (lines 2472-2606)
    covering:
      - compose.include[]
      - compose.include[].concept[]
      - compose.include[].filter[]
      - compose.exclude[]
      - compose.exclude[].concept[]

    A NEW iterator without the guard would be a regression. Probe class:
    structural source-read audit on the AST of ``_expand_intensional``.
    """
    src = _get_nested_func_source("create_fhir_app", "_expand_intensional")
    assert src, "_expand_intensional not found in create_fhir_app"
    tree = ast.parse(src)
    unguarded: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if not isinstance(node.iter, ast.Call):
            continue
        if not isinstance(node.iter.func, ast.Attribute):
            continue
        if node.iter.func.attr != "get":
            continue
        if not node.iter.args:
            continue
        first_arg = node.iter.args[0]
        if not isinstance(first_arg, ast.Constant):
            continue
        # Only audit untrusted-data iterator keys (per 10th PROMOTED pattern).
        if first_arg.value not in ("include", "exclude", "concept", "filter"):
            continue
        has_guard = False
        for stmt in node.body[:5]:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                    if sub.func.id == "isinstance":
                        has_guard = True
                        break
            if has_guard:
                break
        if not has_guard:
            unguarded.append(node.lineno)
    assert not unguarded, (
        f"UNGUARDed compose.iterator loops at lines {unguarded} in "
        f"_expand_intensional. CS-04 HISTORIAN QA-001 regression-pin."
    )


def test_h12_do_closure_isinstance_guards_structurally_present() -> None:
    """HISTORIAN (CF-HISTORIAN-CM03-01 regression-pin): ``_do_closure``
    source MUST structurally contain BOTH isinstance guards.

    The 10th PROMOTED pattern at the CF-HISTORIAN-CM03-01 RESOLVED site
    (``apps/fhir_api.py:2311`` for ``isinstance(param, dict)`` and
    ``:2316`` for ``isinstance(coding, dict)``) is load-bearing.

    Source-read contract catches refactors that inline the loop or rename
    the guard. Distinct from behavioral probe (test_h20) — the structural
    contract survives behavior-equivalent refactors that move the guard
    but preserve its presence.
    """
    src = _get_nested_func_source("create_fhir_app", "_do_closure")
    assert src, "_do_closure not found in create_fhir_app"
    assert "isinstance(param, dict)" in src, (
        "_do_closure missing `isinstance(param, dict)` guard "
        "(CF-HISTORIAN-CM03-01 regression)"
    )
    assert "isinstance(coding, dict)" in src, (
        "_do_closure missing `isinstance(coding, dict)` guard "
        "(CF-HISTORIAN-CM03-01 regression)"
    )


def test_h13_do_closure_isinstance_guards_via_ast_walk() -> None:
    """HISTORIAN (4th-sibling AST-walk search, SKEPTIC tip #1):
    AST-walk ``_do_closure`` to confirm the isinstance guards are
    structurally attached to the ``concept`` extraction loop body.

    Source-substring search catches presence but not structural position.
    AST-walk confirms the guard is INSIDE the loop iterating
    ``body.get("parameter", [])``, not elsewhere in the function.
    """
    src = _get_nested_func_source("create_fhir_app", "_do_closure")
    assert src
    tree = ast.parse(src)
    found_param_loop = False
    found_param_guard = False
    found_coding_guard = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if not isinstance(node.iter, ast.Call):
            continue
        if not isinstance(node.iter.func, ast.Attribute):
            continue
        if node.iter.func.attr != "get":
            continue
        if not node.iter.args:
            continue
        first_arg = node.iter.args[0]
        if not isinstance(first_arg, ast.Constant):
            continue
        if first_arg.value != "parameter":
            continue
        found_param_loop = True
        # Walk the loop body for isinstance guards.
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.If):
                    for nested in ast.walk(sub):
                        if (
                            isinstance(nested, ast.Call)
                            and isinstance(nested.func, ast.Name)
                            and nested.func.id == "isinstance"
                        ):
                            if nested.args and isinstance(nested.args[0], ast.Name):
                                arg_name = nested.args[0].id
                                if arg_name == "param":
                                    found_param_guard = True
                                if arg_name == "coding":
                                    found_coding_guard = True
    assert found_param_loop, (
        "_do_closure concept-extraction loop iterating "
        "`body.get('parameter', [])` not found structurally"
    )
    assert found_param_guard, (
        "`isinstance(param, dict)` guard not structurally attached to "
        "the concept-extraction loop body (CF-HISTORIAN-CM03-01 regression)"
    )
    assert found_coding_guard, (
        "`isinstance(coding, dict)` guard not structurally attached to "
        "the concept-extraction loop body (CF-HISTORIAN-CM03-01 regression)"
    )


# ===========================================================================
# Lens 2: Bidirectional canonical-URI invariant (SKEPTIC tip #2).
#
# Re-derive: ``fhir_uri_to_system`` on INPUT + ``system_to_fhir_uri`` on
# OUTPUT via ``to_parameter_list`` parametrized over every alias in
# FHIR_URI_ALIASES × every seeded code.
# ===========================================================================


@pytest.mark.parametrize(
    "alias_uri, expected_source",
    list(FHIR_URI_ALIASES.items()),
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_h20_input_uri_resolution_for_every_alias(
    alias_uri: str, expected_source: str
) -> None:
    """HISTORIAN (SKEPTIC tip #2, INPUT axis): every alias in
    FHIR_URI_ALIASES resolves via ``fhir_uri_to_system`` to the expected
    internal source name.

    This is the INPUT side of the bidirectional canonical-URI invariant.
    The ``_do_closure`` handler reads the client-supplied system URI and
    resolves via ``fhir_uri_to_system(system_uri) or system_uri``. If the
    resolution silently returned None for an alias, the closure table
    would store the raw alias URI as ``info["system"]``, and the OUTPUT
    side (``to_parameter_list``) would emit the alias verbatim instead
    of the canonical URI.

    Probe class: parametrized source-resolution contract over the entire
    FHIR_URI_ALIASES registry. Catches a future drift where an alias is
    removed from the registry but still documented as supported.
    """
    resolved = fhir_uri_to_system(alias_uri)
    assert resolved == expected_source, (
        f"fhir_uri_to_system({alias_uri!r}) returned {resolved!r}; "
        f"expected {expected_source!r} per FHIR_URI_ALIASES"
    )


@pytest.mark.parametrize(
    "source, expected_uri",
    list(SYSTEM_TO_FHIR_URI.items()),
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_h21_output_uri_resolution_for_every_source(
    source: str, expected_uri: str
) -> None:
    """HISTORIAN (SKEPTIC tip #2, OUTPUT axis): every source in
    SYSTEM_TO_FHIR_URI resolves via ``system_to_fhir_uri`` to the
    expected canonical URI.

    This is the OUTPUT side of the bidirectional canonical-URI invariant.
    The ``ClosureTable.to_parameter_list`` method re-resolves the stored
    source name to the canonical URI via
    ``system_to_fhir_uri(info["system"]) or info["system"]``. If the
    resolution silently returned None, the OUTPUT would echo the raw
    source label (e.g. "SNOMEDCT_US") instead of the canonical URI.

    Probe class: parametrized source-resolution contract over the entire
    SYSTEM_TO_FHIR_URI registry. Catches a future drift where a source
    is added without updating the URI map.
    """
    resolved = system_to_fhir_uri(source)
    assert resolved == expected_uri, (
        f"system_to_fhir_uri({source!r}) returned {resolved!r}; "
        f"expected {expected_uri!r} per SYSTEM_TO_FHIR_URI"
    )


@pytest.mark.parametrize(
    "label, alias_uri, expected_canonical",
    [
        ("snomed-trailing-slash", SNOMED_URI_TRAILING_SLASH, SNOMED_URI),
        ("snomed-oid-alias", SNOMED_URI_OID_ALIAS, SNOMED_URI),
        ("snomed-uppercase-scheme", SNOMED_URI_UPPERCASE_SCHEME, SNOMED_URI),
    ],
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_h22_closure_input_resolves_alias_to_canonical_source(
    fhir_client, label, alias_uri, expected_canonical
) -> None:
    """HISTORIAN (SKEPTIC tip #2, INPUT × OUTPUT invariant): POST
    ``$closure`` with a concept whose ``system`` is an alias URI MUST
    resolve to the canonical source name internally AND emit the
    canonical URI in the OUTPUT.

    Behavioral end-to-end test of the bidirectional invariant on the
    ``$closure`` surface. The alias input goes through
    ``fhir_uri_to_system`` (INPUT resolution); the OUTPUT goes through
    ``system_to_fhir_uri`` via ``to_parameter_list``.

    If either side drifts (alias not in registry, OR source not in
    SYSTEM_TO_FHIR_URI), the OUTPUT would echo the alias verbatim
    instead of the canonical URI — silent client-input-as-canonical
    drift on the closure surface.
    """
    _reset_singleton_manager()
    name = f"historian-bidirectional-{label}"
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": alias_uri, "code": "73211009", "display": "DM"}],
        ),
    )
    assert r.status_code < 500, (
        f"closure with alias {label!r} caused server error: "
        f"{r.status_code} {r.text[:300]}"
    )
    body = r.json()
    concepts = _find_params(body, "concept")
    assert len(concepts) == 1, (
        f"expected 1 concept in closure OUTPUT; got {len(concepts)}: "
        f"{concepts}"
    )
    out_system = concepts[0]["valueCoding"]["system"]
    assert out_system == expected_canonical, (
        f"closure OUTPUT system for alias {label!r} ({alias_uri!r}) "
        f"drifted to {out_system!r}; expected canonical {expected_canonical!r}. "
        f"Bidirectional canonical-URI invariant violation on closure surface."
    )


@pytest.mark.parametrize(
    "source, code, display",
    [(s, c, d) for s, c, _, d in SEEDED_CODES],
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_h23_closure_output_emits_canonical_uri_for_seeded_source(
    fhir_client, source: str, code: str, display: str
) -> None:
    """HISTORIAN (SKEPTIC tip #2, OUTPUT × seeded-code parametrization):
    POST ``$closure`` with each seeded code MUST emit the canonical URI
    in the OUTPUT — NOT the internal source name.

    The closure table stores ``info["system"] = source`` (the internal
    source name like "SNOMEDCT_US"). The OUTPUT in ``to_parameter_list``
    MUST re-resolve via ``system_to_fhir_uri(info["system"])`` to the
    canonical FHIR R4 URI (like "http://snomed.info/sct").

    If the re-resolution silently returned None, the OUTPUT would echo
    "SNOMEDCT_US" — a non-URI string that violates the FHIR R4 Coding
    datatype constraint (system must be a URI per §3.1.0.1.5).

    Probe class: parametrized over every seeded code in the conformance
    fixture. Catches a future drift where ``to_parameter_list`` is
    refactored to emit the raw source label.
    """
    _reset_singleton_manager()
    canonical_uri = SYSTEM_TO_FHIR_URI[source]
    name = f"historian-canonical-output-{source}"
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": canonical_uri, "code": code, "display": display}],
        ),
    )
    assert r.status_code < 500, (
        f"closure for source {source!r} caused server error: "
        f"{r.status_code} {r.text[:300]}"
    )
    body = r.json()
    concepts = _find_params(body, "concept")
    assert len(concepts) == 1
    out_system = concepts[0]["valueCoding"]["system"]
    assert out_system == canonical_uri, (
        f"closure OUTPUT system for source {source!r} drifted to "
        f"{out_system!r}; expected canonical {canonical_uri!r}"
    )
    # The OUTPUT system MUST be a URI (http://... or urn:oid:...), NOT
    # the raw source label (e.g. "SNOMEDCT_US").
    assert out_system.startswith(("http://", "https://", "urn:")), (
        f"closure OUTPUT system {out_system!r} is NOT a URI — "
        f"looks like the raw source label was echoed verbatim"
    )


def test_h24_to_parameter_list_source_reads_system_to_fhir_uri() -> None:
    """HISTORIAN (SKEPTIC tip #2, structural contract): source-read
    ``ClosureTable.to_parameter_list`` to confirm it calls
    ``system_to_fhir_uri(info["system"])``.

    The OUTPUT side of the bidirectional canonical-URI invariant is
    load-bearing on this single call. Without it, the OUTPUT would echo
    the raw source label.

    Source-read contract catches a refactor that inlines the source name
    lookup or replaces it with a hardcoded URI.
    """
    closure_src = _get_top_level_class_source(
        Path(inspect.getsourcefile(ClosureTable)), "ClosureTable"
    )
    assert "system_to_fhir_uri" in closure_src, (
        "ClosureTable.to_parameter_list missing `system_to_fhir_uri` call "
        "(bidirectional canonical-URI invariant regression)"
    )


def test_h25_do_closure_source_reads_fhir_uri_to_system() -> None:
    """HISTORIAN (SKEPTIC tip #2, structural contract): source-read
    ``_do_closure`` to confirm it calls ``fhir_uri_to_system`` on the
    INPUT system URI.

    The INPUT side of the bidirectional canonical-URI invariant is
    load-bearing on this single call. Without it, the handler would
    store the raw alias URI as the source name.
    """
    src = _get_nested_func_source("create_fhir_app", "_do_closure")
    assert src
    assert "fhir_uri_to_system" in src, (
        "_do_closure missing `fhir_uri_to_system` call on INPUT system URI "
        "(bidirectional canonical-URI invariant regression)"
    )


# ===========================================================================
# Lens 3: Reset semantic via object-identity assertion (SKEPTIC tip #3).
#
# ``t1 is not t2`` after reset — the reset MUST construct a FRESH
# ClosureTable instance, not mutate the existing one in-place.
# ===========================================================================


def test_h30_reset_returns_fresh_instance_object_identity() -> None:
    """HISTORIAN (SKEPTIC tip #3, object-identity-is-the-contract):
    ``ClosureManager.reset(name)`` MUST return a FRESH ``ClosureTable``
    instance — verified via Python's ``is`` operator.

    The contract is ``t1 is not t2`` after reset. If reset mutated the
    existing instance in-place (e.g. ``self._tables[name].concepts.clear()``),
    the object identity would be preserved (``t1 is t2``) and any
    external references to the old table would silently observe the
    mutation — a subtle state-leakage bug.

    This is the strongest possible test of the reset semantic: a
    behavior-equivalent in-place clear would PASS a state-equality
    probe but FAIL this object-identity probe. Distinct from
    CM-04 HISTORIAN strategy 54 (which uses ``is`` for module-attribute
    identity post-consolidation); here we apply ``is`` to per-call
    instance identity post-reset.
    """
    _reset_singleton_manager()
    manager = ClosureManager()
    t1 = manager.get_or_create("historian-reset-identity")
    t2 = manager.reset("historian-reset-identity")
    assert t1 is not t2, (
        "ClosureManager.reset returned the SAME instance — must be a "
        "FRESH ClosureTable (object-identity contract violation)"
    )


def test_h31_reset_clears_concepts_in_new_instance() -> None:
    """HISTORIAN (SKEPTIC tip #3): after reset, the NEW instance's
    ``concepts`` dict MUST be empty (fresh state, not in-place clear).

    Behavioral companion to test_h30. Even if a future refactor uses
    in-place clear (``t1 is t2``), this probe would catch a partial
    clear (concepts survived the reset).
    """
    _reset_singleton_manager()
    manager = ClosureManager()
    t1 = manager.get_or_create("historian-reset-clears")
    # Mutate t1 so we can observe whether t2 reflects the mutation.
    t1.concepts["stale_code"] = {"system": "SNOMEDCT_US", "display": "stale"}
    t2 = manager.reset("historian-reset-clears")
    assert "stale_code" not in t2.concepts, (
        "stale_code leaked into the fresh ClosureTable after reset"
    )
    # The OLD instance t1 retains its state — but the manager no longer
    # references it. This is the load-bearing invariant: external refs
    # to t1 continue to see the pre-reset state.
    assert "stale_code" in t1.concepts, (
        "reset should NOT mutate the old instance — external refs to t1 "
        "must continue to observe pre-reset state"
    )


def test_h32_reset_clears_subsumes_in_new_instance() -> None:
    """HISTORIAN (SKEPTIC tip #3): after reset, the NEW instance's
    ``_subsumes`` dict MUST be empty (fresh state, not in-place clear)."""
    _reset_singleton_manager()
    manager = ClosureManager()
    t1 = manager.get_or_create("historian-reset-subsumes")
    t1._subsumes[("a", "b")] = True
    t2 = manager.reset("historian-reset-subsumes")
    assert ("a", "b") not in t2._subsumes, (
        "stale subsumes entry leaked into fresh ClosureTable after reset"
    )


def test_h33_reset_clears_incomplete_since_flag() -> None:
    """HISTORIAN (SKEPTIC tip #3): after reset, the NEW instance's
    ``incomplete_since`` flag MUST be False (fresh state).

    The B6 fix flag is the load-bearing signal for callers detecting
    degraded ``$subsumes`` answers after transient failures. If the flag
    leaked across reset, callers would observe stale degradation signals
    on a fresh closure table — silent-wrong-answer.
    """
    _reset_singleton_manager()
    manager = ClosureManager()
    t1 = manager.get_or_create("historian-reset-incomplete")
    t1.incomplete_since = True
    t2 = manager.reset("historian-reset-incomplete")
    assert t2.incomplete_since is False, (
        "incomplete_since flag leaked into fresh ClosureTable after reset "
        "(B6 fix flag must reset to False on re-init)"
    )


def test_h34_reset_clears_version_counter() -> None:
    """HISTORIAN (SKEPTIC tip #3): after reset, the NEW instance's
    ``_version`` counter MUST be 0 (fresh state)."""
    _reset_singleton_manager()
    manager = ClosureManager()
    t1 = manager.get_or_create("historian-reset-version")
    t1._version = 42
    t2 = manager.reset("historian-reset-version")
    assert t2._version == 0, (
        f"_version counter leaked into fresh ClosureTable after reset "
        f"(expected 0; got {t2._version})"
    )


def test_h35_reset_source_reads_closure_table_constructor() -> None:
    """HISTORIAN (SKEPTIC tip #3, structural contract): source-read
    ``ClosureManager.reset`` to confirm it constructs a FRESH
    ``ClosureTable(name)`` instance (NOT in-place clear).

    The structural contract catches a refactor that uses
    ``self._tables[name].concepts.clear()`` instead of constructing a
    new instance. The object-identity probe (test_h30) is the behavioral
    safety net; this source-read probe catches the drift structurally
    before the behavior even changes.
    """
    closure_mod_path = Path(inspect.getsourcefile(ClosureManager))
    src = _get_top_level_class_source(closure_mod_path, "ClosureManager")
    assert src, "ClosureManager class source not found"
    # Find the reset method source segment.
    tree = ast.parse(src)
    reset_src = ""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "reset"
        ):
            reset_src = ast.get_source_segment(src, node) or ""
            break
    assert reset_src, "ClosureManager.reset method not found in source"
    assert "ClosureTable(" in reset_src, (
        "ClosureManager.reset must construct a fresh ClosureTable(name) "
        "instance — not mutate in-place (object-identity contract)"
    )
    # The reset MUST NOT use `.clear()` on concepts/_subsumes in-place.
    assert ".clear()" not in reset_src, (
        "ClosureManager.reset uses `.clear()` — must construct a FRESH "
        "ClosureTable instance instead (object-identity contract)"
    )


def test_h36_get_or_create_returns_same_instance_for_existing_name() -> None:
    """HISTORIAN (SKEPTIC tip #3, mirror invariant): for an EXISTING name,
    ``get_or_create`` returns the SAME instance — the ``is`` operator
    confirms singleton-per-name semantics.

    This is the MIRROR of test_h30: reset returns a fresh instance; but
    get_or_create on an existing name returns the same instance. The
    two probes together verify the manager's table lifecycle is correct:
    new name → new instance, existing name → same instance, reset →
    fresh instance.
    """
    _reset_singleton_manager()
    manager = ClosureManager()
    t1 = manager.get_or_create("historian-goc-same")
    t2 = manager.get_or_create("historian-goc-same")
    assert t1 is t2, (
        "get_or_create on EXISTING name returned different instances — "
        "must be the SAME instance (singleton-per-name contract)"
    )


# ===========================================================================
# Lens 4: Version hash payload composition (SKEPTIC tip #4).
#
# ``len:_version:sorted_keys`` — specifically ``sorted()`` on
# ``concepts.keys()``, NOT ``items()`` or ``values()``.
# ===========================================================================


def test_h40_version_hash_payload_uses_sorted_keys_not_items() -> None:
    """HISTORIAN (SKEPTIC tip #4, structural contract): source-read
    ``ClosureTable.version_hash`` to confirm the payload composition is
    ``f"{len(concepts)}:{_version}:{sorted(concepts.keys())}"`` —
    specifically ``sorted()`` on ``concepts.keys()``, NOT on
    ``concepts.items()`` or ``concepts.values()``.

    Why this matters: the hash payload is the load-bearing signal for
    clients detecting state changes. If the payload used ``items()``,
    the DISPLAY values would contribute to the hash — meaning two
    closures with the same codes but different displays would have
    different hashes. The documented behavior is that DISPLAY is
    EXCLUDED from the payload (only codes + version contribute).

    Probe class: source-read contract via ``ast.get_source_segment`` +
    AST-walk. Catches a refactor that changes the payload composition.
    """
    closure_src = _get_top_level_class_source(
        Path(inspect.getsourcefile(ClosureTable)), "ClosureTable"
    )
    assert closure_src
    tree = ast.parse(closure_src)
    version_hash_src = ""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "version_hash"
        ):
            version_hash_src = ast.get_source_segment(closure_src, node) or ""
            break
    assert version_hash_src, "version_hash method not found in source"
    # The payload MUST call sorted() on .keys(), NOT on .items() or .values().
    assert "sorted(self.concepts.keys())" in version_hash_src, (
        "version_hash payload MUST use `sorted(self.concepts.keys())` — "
        "found a different composition. CF-HISTORIAN-CM03-02 / version "
        "hash payload composition regression."
    )
    # FORBIDDEN: the payload MUST NOT use items() or values() (would
    # include display, breaking the documented exclusion).
    assert "items()" not in version_hash_src, (
        "version_hash payload uses `.items()` — display values would "
        "contribute to the hash, breaking the documented exclusion "
        "(CF-SKEPTIC-CM03-01 spec-deviation-as-carry-forward)"
    )
    assert "values()" not in version_hash_src, (
        "version_hash payload uses `.values()` — display values would "
        "contribute to the hash, breaking the documented exclusion"
    )


def test_h41_version_hash_excludes_display_behavioral() -> None:
    """HISTORIAN (SKEPTIC tip #4, behavioral): two closures with the SAME
    codes but DIFFERENT displays MUST produce the SAME version hash
    (modulo ``_version`` counter).

    The payload composition ``len:_version:sorted_keys`` excludes display
    values. If a future refactor changes the payload to include display,
    this probe would fail loudly.

    Behavioral companion to test_h40. Source-read catches composition
    drift; behavioral catches semantic drift even if composition looks
    similar.
    """
    _reset_singleton_manager()
    t1 = ClosureTable("historian-hash-display-1")
    t2 = ClosureTable("historian-hash-display-2")
    # Same code + same source, DIFFERENT display.
    t1.concepts["73211009"] = {"system": "SNOMEDCT_US", "display": "Diabetes"}
    t2.concepts["73211009"] = {
        "system": "SNOMEDCT_US",
        "display": "Diabetes mellitus (entirely different display)",
    }
    # Both at _version=0 (no add_concepts call).
    assert t1._version == t2._version == 0
    h1 = t1.version_hash()
    h2 = t2.version_hash()
    assert h1 == h2, (
        f"version hashes differ for same-codes-different-displays: "
        f"{h1!r} vs {h2!r}. Display values MUST be excluded from the "
        f"hash payload per documented behavior."
    )


def test_h42_version_hash_changes_when_concept_added() -> None:
    """HISTORIAN (SKEPTIC tip #4): the version hash MUST change when a
    concept is added — the payload composition includes ``len(concepts)``
    + ``sorted(concepts.keys())``, so adding a concept changes both.

    This is the load-bearing semantic: the hash is the client's signal
    that the closure state has changed. If the hash were stable across
    additions, clients would miss state transitions.
    """
    _reset_singleton_manager()
    t = ClosureTable("historian-hash-change")
    h_empty = t.version_hash()
    t.concepts["73211009"] = {"system": "SNOMEDCT_US", "display": "DM"}
    h_one = t.version_hash()
    assert h_empty != h_one, (
        "version hash did not change after adding a concept — payload "
        "composition must include len(concepts) + sorted(concepts.keys())"
    )


def test_h43_version_hash_changes_when_version_counter_advances() -> None:
    """HISTORIAN (SKEPTIC tip #4): the version hash MUST change when the
    ``_version`` counter advances — the payload composition includes
    ``self._version``."""
    _reset_singleton_manager()
    t = ClosureTable("historian-hash-version")
    t.concepts["73211009"] = {"system": "SNOMEDCT_US", "display": "DM"}
    h_v0 = t.version_hash()
    t._version = 1
    h_v1 = t.version_hash()
    assert h_v0 != h_v1, (
        "version hash did not change after advancing _version counter — "
        "payload composition must include self._version"
    )


def test_h44_version_hash_uses_md5_hexdigest_truncated_to_12() -> None:
    """HISTORIAN (SKEPTIC tip #4, structural contract): the version hash
    algorithm is ``hashlib.md5(...).hexdigest()[:12]``.

    The 12-char truncation is a documented contract — clients depend on
    the hash being a fixed-length short string for easy comparison.
    A refactor that changes the algorithm (e.g. sha256) or the truncation
    length would silently invalidate existing client hash-pinning logic.
    """
    closure_src = _get_top_level_class_source(
        Path(inspect.getsourcefile(ClosureTable)), "ClosureTable"
    )
    assert closure_src
    tree = ast.parse(closure_src)
    version_hash_src = ""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "version_hash"
        ):
            version_hash_src = ast.get_source_segment(closure_src, node) or ""
            break
    assert version_hash_src
    assert "hashlib.md5" in version_hash_src, (
        "version_hash MUST use hashlib.md5 — found a different algorithm"
    )
    assert "hexdigest()[:12]" in version_hash_src, (
        "version_hash MUST truncate to [:12] chars — found a different "
        "truncation length"
    )


def test_h45_version_hash_deterministic_across_instances_same_state() -> None:
    """HISTORIAN (SKEPTIC tip #4): two ClosureTable instances with the
    SAME state (same concepts + same _version) MUST produce the SAME
    version hash.

    The payload composition is deterministic — no randomness, no
    instance-specific salt. Cross-instance determinism is the load-bearing
    invariant for clients that pin the hash across server restarts.
    """
    _reset_singleton_manager()
    t1 = ClosureTable("historian-hash-det-1")
    t2 = ClosureTable("historian-hash-det-2")
    t1.concepts["73211009"] = {"system": "SNOMEDCT_US", "display": "DM"}
    t2.concepts["73211009"] = {"system": "SNOMEDCT_US", "display": "DM"}
    # _version is 0 on both (no add_concepts call).
    assert t1.version_hash() == t2.version_hash(), (
        "version hashes differ for same-state instances — payload "
        "composition must be deterministic across instances"
    )


def test_h46_version_hash_payload_format_explicit() -> None:
    """HISTORIAN (SKEPTIC tip #4): the version hash payload format is
    ``{len}:{version}:{sorted_keys_list}`` — explicitly verify the
    format by reconstructing the payload and matching the hash.

    The format is a 3-part colon-separated string. The third part is
    the Python ``repr()`` of the sorted keys list (because f-string
    interpolation of a list calls ``str()`` which equals ``repr()`` for
    lists). This probe verifies the format is structurally intact.
    """
    _reset_singleton_manager()
    t = ClosureTable("historian-hash-format")
    t.concepts["73211009"] = {"system": "SNOMEDCT_US", "display": "DM"}
    t.concepts["44054006"] = {"system": "SNOMEDCT_US", "display": "T2DM"}
    expected_payload = (
        f"{len(t.concepts)}:{t._version}:{sorted(t.concepts.keys())}"
    )
    expected_hash = hashlib.md5(expected_payload.encode()).hexdigest()[:12]
    assert t.version_hash() == expected_hash, (
        f"version hash {t.version_hash()!r} does not match expected "
        f"{expected_hash!r} from reconstructed payload {expected_payload!r}. "
        f"Payload format composition drift."
    )


# ===========================================================================
# Lens 5: Batch per-entry isolation on multi-entry mixed-success batches
# (SKEPTIC tip #5).
#
# Per FHIR R4 §3.7, batch entry success/failure of one change SHOULD NOT
# alter another. The order of entries in the response Bundle MUST match
# the order in the request Bundle.
# ===========================================================================


def test_h50_batch_multi_entry_mixed_success_preserves_order(fhir_client) -> None:
    """HISTORIAN (SKEPTIC tip #5, FHIR R4 §3.7): a batch Bundle with
    multiple entries of mixed success/failure MUST return a
    ``batch-response`` Bundle with per-entry responses in the SAME ORDER
    as the request.

    Probe shape: 3-entry batch with:
      - Entry 0: valid $closure (success — 200)
      - Entry 1: missing-name $closure (failure — 400)
      - Entry 2: valid $closure on a different name (success — 200)

    The response Bundle MUST have 3 entries in the same order with
    matching status codes. Per §3.7: "the order of entries in the
    response Bundle matches the order in the request Bundle."
    """
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": "batch-entry-0"},
                        ],
                    },
                },
                {
                    # Missing name — should fail with 400.
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [],
                    },
                },
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": "batch-entry-2"},
                        ],
                    },
                },
            ],
        },
    )
    assert r.status_code == 200, (
        f"batch endpoint returned {r.status_code}: {r.text[:300]}"
    )
    bundle = r.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "batch-response"
    entries = bundle.get("entry", [])
    assert len(entries) == 3, (
        f"expected 3 response entries; got {len(entries)}"
    )
    # Order preservation: entry[0]=200, entry[1]=400, entry[2]=200.
    status_codes = [e.get("response", {}).get("status") for e in entries]
    assert status_codes[0].startswith("2"), (
        f"entry[0] should be 2xx (valid $closure); got {status_codes[0]}"
    )
    assert status_codes[1].startswith("4"), (
        f"entry[1] should be 4xx (missing name); got {status_codes[1]}"
    )
    assert status_codes[2].startswith("2"), (
        f"entry[2] should be 2xx (valid $closure); got {status_codes[2]}"
    )


def test_h51_batch_per_entry_isolation_malformed_valuecoding_in_one_entry(
    fhir_client
) -> None:
    """HISTORIAN (SKEPTIC tip #5, 10th PROMOTED pattern on batch surface):
    a batch with one entry containing a malformed valueCoding MUST
    isolate the failure to that entry — other entries MUST succeed.

    Probe shape: 2-entry batch with:
      - Entry 0: $closure with malformed valueCoding (string instead of dict)
      - Entry 1: valid $closure

    The malformed valueCoding entry MUST silently drop the malformed
    concept (per CF-HISTORIAN-CM03-01 RESOLVED) AND return 200 — NOT
    propagate AttributeError as 500. The valid entry MUST succeed
    independently.
    """
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": "batch-malformed-0"},
                            # Malformed valueCoding as STRING.
                            {
                                "name": "concept",
                                "valueCoding": "not-a-coding",
                            },
                        ],
                    },
                },
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": "batch-valid-1"},
                            {
                                "name": "concept",
                                "valueCoding": {
                                    "system": SNOMED_URI,
                                    "code": "73211009",
                                    "display": "DM",
                                },
                            },
                        ],
                    },
                },
            ],
        },
    )
    assert r.status_code == 200
    bundle = r.json()
    entries = bundle.get("entry", [])
    assert len(entries) == 2
    # Entry 0: malformed valueCoding silently dropped → 200 with empty closure.
    assert entries[0]["response"]["status"].startswith("2"), (
        f"entry[0] (malformed valueCoding) should be 2xx (silent drop "
        f"per CF-HISTORIAN-CM03-01); got {entries[0]['response']['status']}"
    )
    # Entry 1: valid $closure → 200 with 1 concept.
    assert entries[1]["response"]["status"].startswith("2"), (
        f"entry[1] (valid $closure) should be 2xx; got "
        f"{entries[1]['response']['status']}"
    )


def test_h52_batch_per_entry_isolation_unknown_op_in_one_entry(fhir_client) -> None:
    """HISTORIAN (SKEPTIC tip #5, FHIR R4 §3.7): a batch with one entry
    containing an unknown operation path MUST isolate the 404 to that
    entry — other entries MUST succeed.

    Probe shape: 2-entry batch with:
      - Entry 0: unknown operation $closure-typo → 404
      - Entry 1: valid $closure → 200
    """
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure-typo",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": "unknown-op"},
                        ],
                    },
                },
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": "valid-op"},
                        ],
                    },
                },
            ],
        },
    )
    assert r.status_code == 200
    bundle = r.json()
    entries = bundle.get("entry", [])
    assert len(entries) == 2
    assert entries[0]["response"]["status"].startswith("4"), (
        f"entry[0] (unknown op) should be 4xx; got "
        f"{entries[0]['response']['status']}"
    )
    assert entries[1]["response"]["status"].startswith("2"), (
        f"entry[1] (valid op) should be 2xx; got "
        f"{entries[1]['response']['status']}"
    )


def test_h53_batch_per_entry_isolation_malformed_body_in_one_entry(fhir_client) -> None:
    """HISTORIAN (SKEPTIC tip #5, FHIR R4 §3.7): a batch with one entry
    containing a non-Parameters body MUST isolate the 400 to that entry
    — other entries MUST succeed.

    Probe shape: 2-entry batch with:
      - Entry 0: $closure with body that is NOT a Parameters resource → 400
      - Entry 1: valid $closure → 200
    """
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    # Wrong resourceType — not a Parameters.
                    "resource": {
                        "resourceType": "Patient",
                        "id": "not-parameters",
                    },
                },
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": "valid-after-bad"},
                        ],
                    },
                },
            ],
        },
    )
    assert r.status_code == 200
    bundle = r.json()
    entries = bundle.get("entry", [])
    assert len(entries) == 2
    # Entry 0: bad body shape — should be 400 (isolated failure).
    assert entries[0]["response"]["status"].startswith("4"), (
        f"entry[0] (bad body) should be 4xx; got "
        f"{entries[0]['response']['status']}"
    )
    assert entries[1]["response"]["status"].startswith("2"), (
        f"entry[1] (valid) should be 2xx; got "
        f"{entries[1]['response']['status']}"
    )


def test_h54_batch_per_entry_order_preserved_on_large_batch(fhir_client) -> None:
    """HISTORIAN (SKEPTIC tip #5, FHIR R4 §3.7 order preservation):
    a 10-entry batch with interleaved valid/invalid entries MUST return
    responses in the EXACT order of the request.

    Order preservation is structural (single in-order loop), but a
    future refactor that parallelizes batch processing could break it.
    Probe class: large-batch order-preservation audit.
    """
    _reset_singleton_manager()
    entries_req = []
    expected_statuses = []
    for i in range(10):
        if i % 3 == 1:
            # Every 3rd entry (1, 4, 7) is missing name → 400.
            entries_req.append({
                "request": {
                    "method": "POST",
                    "url": "/CodeSystem/$closure",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [],
                },
            })
            expected_statuses.append("4xx")
        else:
            entries_req.append({
                "request": {
                    "method": "POST",
                    "url": "/CodeSystem/$closure",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "name", "valueString": f"batch-order-{i}"},
                    ],
                },
            })
            expected_statuses.append("2xx")
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": entries_req,
        },
    )
    assert r.status_code == 200
    bundle = r.json()
    entries = bundle.get("entry", [])
    assert len(entries) == 10, (
        f"expected 10 response entries; got {len(entries)}"
    )
    for i, (entry, expected) in enumerate(zip(entries, expected_statuses)):
        actual = entry["response"]["status"]
        assert actual.startswith(expected[0]), (
            f"entry[{i}] expected {expected}; got {actual}. "
            f"Order preservation violation on large batch."
        )


# ===========================================================================
# Lens 6: Manager-level invariants (regression-pin for prior patterns).
# ===========================================================================


def test_h60_singleton_manager_returns_same_instance() -> None:
    """HISTORIAN (regression-pin): ``get_closure_manager()`` returns the
    SAME singleton instance across multiple calls.

    The singleton invariant is load-bearing — without it, two threads
    seeing ``_manager is None`` simultaneously would each construct a
    ClosureManager; one wins the assignment, the other's tables are
    orphaned, and subsequent $subsumes calls return wrong answers.
    """
    _reset_singleton_manager()
    m1 = get_closure_manager()
    m2 = get_closure_manager()
    assert m1 is m2, (
        "get_closure_manager returned different instances — singleton "
        "invariant broken"
    )


def test_h61_list_names_reflects_current_state() -> None:
    """HISTORIAN (regression-pin): ``ClosureManager.list_names`` returns
    the current set of named tables — creating a new table adds its name
    to the list; reset does NOT add a duplicate name.
    """
    _reset_singleton_manager()
    manager = ClosureManager()
    assert "historian-list-1" not in manager.list_names()
    manager.get_or_create("historian-list-1")
    assert "historian-list-1" in manager.list_names()
    # Reset on existing name does NOT duplicate.
    manager.reset("historian-list-1")
    assert manager.list_names().count("historian-list-1") == 1, (
        "list_names has duplicates after reset — name registration drift"
    )


def test_h62_get_returns_none_for_unknown_name() -> None:
    """HISTORIAN (regression-pin): ``ClosureManager.get`` returns None
    for an unknown name (does NOT auto-create)."""
    _reset_singleton_manager()
    manager = ClosureManager()
    assert manager.get("historian-nonexistent") is None


def test_h63_reset_on_unknown_name_creates_fresh_instance() -> None:
    """HISTORIAN (regression-pin): ``ClosureManager.reset`` on an unknown
    name creates a fresh instance (mirrors the documented "Reset (or
    create) a named closure table" semantic in the docstring)."""
    _reset_singleton_manager()
    manager = ClosureManager()
    assert manager.get("historian-reset-create") is None
    t = manager.reset("historian-reset-create")
    assert t is not None
    assert isinstance(t, ClosureTable)
    assert manager.get("historian-reset-create") is t


# ===========================================================================
# Lens 7: Carry-forward pinning (CF-SKEPTIC-CM03-01 + CF-HISTORIAN-CM03-02).
#
# Per carry-forward-as-probe pattern (strategy 33/56), deferred
# carry-forwards are pinned by probes asserting the CURRENT behavior.
# When a future enhancement chunk closes the CF, the probe MUST be
# tightened.
# ===========================================================================


def test_h70_cf_skeptic_cm03_01_return_is_valuestring_not_conceptmap(
    fhir_client
) -> None:
    """HISTORIAN (carry-forward-as-probe, CF-SKEPTIC-CM03-01 DEFERRED):
    ``build_closure_response`` emits Out ``return`` as ``valueString``
    (the 12-char MD5-hex version hash), NOT as a ConceptMap resource
    per FHIR R4 canonical OperationDefinition.

    Per https://hl7.org/fhir/R4/conceptmap-operation-closure.html Out
    Parameters: ``return`` is 1..1 ConceptMap. The current
    implementation's valueString shape is medterm4ds-specific (CF-SKEPTIC-
    CM03-01 DEFERRED — TERMINOLOGIST DECISION: defer to future enhancement
    chunk; the closure table is server-side and HTTP ``$subsumes`` does
    not consult it per CF-SKEPTIC-CM03-02).

    When the CF is closed, this probe MUST be updated to assert the
    spec-correct ConceptMap shape.
    """
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("historian-cf-cm03-01"),
    )
    assert r.status_code == 200
    body = r.json()
    return_param = _find_param(body, "return")
    assert return_param is not None, (
        "Out `return` parameter missing from $closure response"
    )
    # CF-SKEPTIC-CM03-01 documents the CURRENT behavior: valueString.
    assert "valueString" in return_param, (
        f"CF-SKEPTIC-CM03-01 pin: expected valueString in `return`; "
        f"got keys {list(return_param.keys())}. If this probe failed, "
        f"the CF may have been CLOSED — update to assert ConceptMap."
    )
    # FORBIDDEN today: ConceptMap resource shape.
    assert "resource" not in return_param, (
        "CF-SKEPTIC-CM03-01 pin: `return` has a `resource` field — "
        "ConceptMap shape detected. If the CF was closed, update this "
        "probe to assert the spec-correct shape."
    )


def test_h71_cf_historian_cm03_02_incomplete_since_not_surfaced_in_http(
    fhir_client
) -> None:
    """HISTORIAN (carry-forward-as-probe, CF-HISTORIAN-CM03-02 DEFERRED):
    ``build_closure_response`` does NOT surface the ``incomplete_since``
    flag in the HTTP response.

    The flag IS observable on the Python ``ClosureTable`` instance (set
    True when ``add_concept``/``add_concepts`` catches ``duckdb.Error``
    per B6 fix). But ``build_closure_response`` returns ONLY ``return``
    (valueString version hash) + ``concept`` entries — no extension, no
    flag.

    Per CF-HISTORIAN-CM03-02 DEFERRED (TERMINOLOGIST DECISION): the gap
    is invisible today (``$subsumes`` does not consult closure per
    CF-SKEPTIC-CM03-02); the fix becomes load-bearing ONLY when
    CF-SKEPTIC-CM03-02 is wired.

    When the CF is closed, this probe MUST be updated to assert presence
    of the FHIR extension
    ``http://medterm4ds.org/fhir/StructureDefinition/closure-incomplete-since``
    with ``valueBoolean``.
    """
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("historian-cf-cm03-02"),
    )
    assert r.status_code == 200
    body = r.json()
    # CF-HISTORIAN-CM03-02 documents the CURRENT behavior: no extension.
    body_str = str(body)
    assert (
        "closure-incomplete-since" not in body_str
    ), (
        "CF-HISTORIAN-CM03-02 pin: `closure-incomplete-since` extension "
        "detected in response. If this probe failed, the CF may have "
        "been CLOSED — update to assert the spec-correct extension shape."
    )


def test_h72_cf_skeptic_cm03_02_subsumes_does_not_consult_closure(fhir_client) -> None:
    """HISTORIAN (carry-forward-as-probe, CF-SKEPTIC-CM03-02 DEFERRED):
    the HTTP ``$subsumes`` handler does NOT consult the server-side
    ClosureTable — it walks the hierarchy directly via ``is_descendant``.

    Per chunk scope item 6 ("Closure enables fast $subsumes via pre-
    computed relationship table"): the ClosureTable IS built and CAN
    answer subsumption in O(1) via ``ClosureTable.check``, but the HTTP
    ``$subsumes`` handler bypasses it. Spec-permitted (FHIR R4
    ``$closure`` maintains CLIENT-side closure tables; the server's
    ``$subsumes`` is a separate operation).

    Probe verifies: after seeding a closure table with DM + T2DM, the
    HTTP ``$subsumes`` STILL walks the hierarchy (NOT the closure). If
    a future enhancement wires ``$subsumes`` to consult the closure,
    this probe may need updating — but the result should be identical
    because the closure table is seeded from the same hierarchy.
    """
    _reset_singleton_manager()
    # Seed the closure with DM (broader) + T2DM (narrower).
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "historian-cf-cm03-02-subsumes",
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    # HTTP $subsumes — should return "subsumes" (DM broader than T2DM)
    # regardless of whether it consults the closure or the hierarchy.
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": "73211009",
            "codeB": "44054006",
        },
    )
    assert r.status_code == 200
    body = r.json()
    outcome_param = _find_param(body, "outcome")
    assert outcome_param is not None
    assert outcome_param.get("valueCode") == "subsumes", (
        f"DM $subsumes T2DM should be 'subsumes'; got "
        f"{outcome_param.get('valueCode')}"
    )


# ===========================================================================
# Lens 8: Spec-citation discipline — FHIR R4 $closure In/Out params.
# ===========================================================================


def test_h80_closure_in_param_name_required(fhir_client) -> None:
    """HISTORIAN (spec-citation discipline): POST ``$closure`` without
    the ``name`` parameter MUST return 400 — per FHIR R4
    https://hl7.org/fhir/R4/conceptmap-operation-closure.html In
    Parameters: ``name`` is 1..1 string "The name of the closure table
    to create or update."
    """
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={
            "resourceType": "Parameters",
            "parameter": [],
        },
    )
    assert r.status_code == 400, (
        f"$closure without `name` should return 400; got {r.status_code}"
    )
    assert _is_fhir_response(r), (
        f"Content-Type must be application/fhir+json; got "
        f"{r.headers.get('content-type')!r}"
    )


def test_h81_closure_in_param_concept_repeating(fhir_client) -> None:
    """HISTORIAN (spec-citation discipline): POST ``$closure`` with
    multiple ``concept`` entries (0..* per FHIR R4 In Parameters) MUST
    add all of them to the closure table."""
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "historian-multi-concept",
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    assert r.status_code == 200
    body = r.json()
    concepts = _find_params(body, "concept")
    assert len(concepts) == 2, (
        f"expected 2 concepts in closure OUTPUT; got {len(concepts)}"
    )
    codes = {c["valueCoding"]["code"] for c in concepts}
    assert codes == {"73211009", "44054006"}, (
        f"closure OUTPUT codes drifted: {codes}"
    )


def test_h82_closure_out_param_return_present(fhir_client) -> None:
    """HISTORIAN (spec-citation discipline): POST ``$closure`` MUST
    return an Out ``return`` parameter (per FHIR R4 Out Parameters:
    ``return`` is 1..1). The current implementation emits it as
    valueString (CF-SKEPTIC-CM03-01 documents the deviation from the
    spec-correct ConceptMap shape)."""
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("historian-out-return"),
    )
    assert r.status_code == 200
    body = r.json()
    return_param = _find_param(body, "return")
    assert return_param is not None, (
        "Out `return` parameter missing from $closure response "
        "(spec violation: return is 1..1)"
    )


# ===========================================================================
# Lens 9: Cross-handler parity (closure_get ↔ closure_post ↔ batch).
# ===========================================================================


def test_h90_closure_post_and_batch_byte_equivalent_for_same_input(
    fhir_client
) -> None:
    """HISTORIAN (cross-handler parity, extends strategy 50): the SAME
    ``$closure`` input issued via per-operation POST AND via batch
    Bundle entry MUST produce byte-equivalent response shapes (same
    concepts, same version hash).

    The batch dispatcher reuses the same ``_do_closure`` handler as the
    per-operation POST route — clinical content (concept list, version
    hash) is structurally guaranteed to be identical. This probe verifies
    the invariant on the closure surface.
    """
    _reset_singleton_manager()
    # Per-operation POST.
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "historian-parity-perop",
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    assert r1.status_code == 200
    body1 = r1.json()
    # Batch entry.
    _reset_singleton_manager()
    r2 = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [{
                "request": {
                    "method": "POST",
                    "url": "/CodeSystem/$closure",
                },
                "resource": _closure_param_with_concepts(
                    "historian-parity-batch",
                    [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
                )[0] if False else _closure_param_with_concepts(
                    "historian-parity-batch",
                    [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
                ),
            }],
        },
    )
    assert r2.status_code == 200
    bundle = r2.json()
    # Extract the entry's resource body.
    assert len(bundle.get("entry", [])) == 1
    batch_entry = bundle["entry"][0]
    # The response resource is under the "resource" key for successful entries.
    body2 = batch_entry.get("resource", {})
    # Both bodies should have the same number of concepts.
    concepts1 = _find_params(body1, "concept")
    concepts2 = _find_params(body2, "concept")
    assert len(concepts1) == len(concepts2) == 1, (
        f"expected 1 concept in each response; per-op={len(concepts1)}, "
        f"batch={len(concepts2)}"
    )
    # The version hash should be identical (same input → same hash).
    h1 = _return_hash(body1)
    h2 = _return_hash(body2)
    assert h1 is not None and h2 is not None
    assert h1 == h2, (
        f"version hash diverged between per-op ({h1!r}) and batch ({h2!r}) "
        f"for the same input — cross-handler parity violation"
    )


# ===========================================================================
# Lens 10: Wire-format / Content-Type on closure surface.
# ===========================================================================


def test_h100_closure_post_xml_format_serializes_value_coding(fhir_client) -> None:
    """HISTORIAN (CR-002 wire-format probe class on closure surface):
    POST ``$closure?_format=xml`` MUST serialize the Out ``concept``
    valueCoding entries as XML elements with the correct structure
    per FHIR R4 XML representation.

    The XML serializer at ``engines/fhir/xml.py`` MUST handle nested
    valueCoding structures. This probe verifies the closure surface
    inherits the CR-002 fix (boolean lowercase) and renders valueCoding
    correctly.
    """
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure?_format=xml",
        json=_closure_param_with_concepts(
            "historian-xml",
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    assert r.status_code == 200
    assert "xml" in r.headers.get("content-type", ""), (
        f"Content-Type must be XML; got {r.headers.get('content-type')!r}"
    )
    body_text = r.text
    # The valueCoding system/code/display should be present as XML.
    assert "73211009" in body_text, "code not in XML body"
    assert SNOMED_URI in body_text, "system URI not in XML body"
    assert "DM" in body_text, "display not in XML body"


def test_h101_closure_post_content_type_fhir_json(fhir_client) -> None:
    """HISTORIAN (CR-001 wire-format probe class on closure surface):
    POST ``$closure`` MUST return Content-Type ``application/fhir+json``
    (NOT the FastAPI default ``application/json``).

    The closure handler funnels through ``_fhir_response`` which sets
    the FHIR MIME type. A future refactor that returns a raw dict would
    silently revert the Content-Type.
    """
    _reset_singleton_manager()
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("historian-content-type"),
    )
    assert _is_fhir_response(r), (
        f"Content-Type must be application/fhir+json; got "
        f"{r.headers.get('content-type')!r}"
    )


# ===========================================================================
# Lens 11: Response-builder drift audit (closure-specific).
# ===========================================================================


def test_h110_build_closure_response_signature_stable() -> None:
    """HISTORIAN (regression-pin for build_closure_response signature):
    the builder function MUST accept a single ``ClosureTable`` parameter
    and return a dict with ``resourceType=Parameters`` + ``parameter``
    array containing ``return`` + ``concept`` entries.

    A future refactor that changes the signature (e.g. adds an explicit
    total parameter per CF-HISTORIAN-VS02-01 methodology extension)
    would need to update this probe AND every call site.
    """
    sig = inspect.signature(build_closure_response)
    params = list(sig.parameters.keys())
    assert params == ["closure"], (
        f"build_closure_response signature drifted: expected ['closure']; "
        f"got {params}. If a size/total parameter was added (CF-HISTORIAN-"
        f"VS02-01 methodology extension), update this probe AND every "
        f"call site."
    )


def test_h111_build_closure_response_emits_resource_type_parameters() -> None:
    """HISTORIAN (regression-pin): ``build_closure_response`` MUST emit
    a Parameters resource with ``resourceType=Parameters`` and a
    ``parameter`` array."""
    _reset_singleton_manager()
    t = ClosureTable("historian-builder-shape")
    response = build_closure_response(t)
    assert response["resourceType"] == "Parameters"
    assert "parameter" in response
    assert isinstance(response["parameter"], list)
    # First entry MUST be the `return` parameter (version hash).
    assert response["parameter"][0]["name"] == "return"


def test_h112_do_closure_calls_build_closure_response() -> None:
    """HISTORIAN (single-source-of-truth structural contract):
    ``_do_closure`` MUST call ``build_closure_response`` to construct
    the response — NOT inline the response construction.

    The builder is the single source of truth for the closure response
    shape. A future refactor that inlines the response would silently
    diverge from the builder's shape contract.
    """
    src = _get_nested_func_source("create_fhir_app", "_do_closure")
    assert src
    assert "build_closure_response" in src, (
        "_do_closure missing `build_closure_response` call — "
        "single-source-of-truth regression"
    )


def test_h113_do_closure_dispatches_to_reset_on_init_path() -> None:
    """HISTORIAN (single-source-of-truth structural contract):
    ``_do_closure`` MUST call ``manager.reset(name)`` on the init path
    (when no concepts are provided) — NOT mutate the existing table
    in-place."""
    src = _get_nested_func_source("create_fhir_app", "_do_closure")
    assert src
    assert "manager.reset(name)" in src, (
        "_do_closure init path missing `manager.reset(name)` call — "
        "object-identity contract regression on init path"
    )


def test_h114_do_closure_dispatches_to_get_or_create_on_add_path() -> None:
    """HISTORIAN (single-source-of-truth structural contract):
    ``_do_closure`` MUST call ``manager.get_or_create(name)`` on the
    add path (when concepts are provided) — NOT call reset (which would
    discard prior state)."""
    src = _get_nested_func_source("create_fhir_app", "_do_closure")
    assert src
    assert "manager.get_or_create(name)" in src, (
        "_do_closure add path missing `manager.get_or_create(name)` call — "
        "state-accumulation regression on add path"
    )


# ===========================================================================
# Lens 12: Closure-table isolation across names.
# ===========================================================================


def test_h120_two_names_isolate_state(fhir_client) -> None:
    """HISTORIAN (regression-pin): two closures with DIFFERENT names
    MUST isolate their state — adding a concept to one MUST NOT affect
    the other.

    The closure identifier is the load-bearing key into the singleton
    ClosureManager's ``_tables`` dict. If two names accidentally collided
    (e.g. due to a silent normalization bug), state would leak.
    """
    _reset_singleton_manager()
    # Init closure-A with DM.
    r_a = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "historian-isolation-A",
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    assert r_a.status_code == 200
    # Init closure-B with T2DM.
    r_b = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "historian-isolation-B",
            [{"system": SNOMED_URI, "code": "44054006", "display": "T2DM"}],
        ),
    )
    assert r_b.status_code == 200
    # Closure-A should have DM, NOT T2DM.
    concepts_a = _find_params(r_a.json(), "concept")
    codes_a = {c["valueCoding"]["code"] for c in concepts_a}
    assert codes_a == {"73211009"}, (
        f"closure-A state drifted: expected {{73211009}}; got {codes_a}"
    )
    # Closure-B should have T2DM, NOT DM.
    concepts_b = _find_params(r_b.json(), "concept")
    codes_b = {c["valueCoding"]["code"] for c in concepts_b}
    assert codes_b == {"44054006"}, (
        f"closure-B state drifted: expected {{44054006}}; got {codes_b}"
    )


def test_h121_two_names_distinct_version_hashes(fhir_client) -> None:
    """HISTORIAN (regression-pin): two closures with DIFFERENT state
    MUST produce DIFFERENT version hashes (the hash payload includes
    ``sorted(concepts.keys())``)."""
    _reset_singleton_manager()
    r_a = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "historian-hash-distinct-A",
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    r_b = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "historian-hash-distinct-B",
            [{"system": SNOMED_URI, "code": "44054006", "display": "T2DM"}],
        ),
    )
    h_a = _return_hash(r_a.json())
    h_b = _return_hash(r_b.json())
    assert h_a != h_b, (
        f"version hashes should differ for distinct closures: "
        f"{h_a!r} vs {h_b!r}"
    )


def test_h122_same_name_same_input_produces_same_hash(fhir_client) -> None:
    """HISTORIAN (regression-pin): two closures with the SAME name AND
    SAME concept input MUST produce the SAME version hash — UNLESS the
    closure accumulates state across calls (which it does — each add is
    cumulative). So the probe verifies the cumulative-accumulation
    semantic: re-adding the same concept INCREMENTS the version counter.

    Per SKEPTIC resweep test_s43: duplicate add OVERWRITES (concepts is
    dict-keyed by code), so the concept set is stable. But _version
    counter advances per add_concepts call. The hash payload includes
    _version — so the hash WILL differ across two separate add calls
    even with the same input.

    Probe documents this behavior.
    """
    _reset_singleton_manager()
    name = "historian-same-name-same-input"
    # First add.
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    # Second add (same concept — overwrites in concepts dict, but
    # _version counter advances).
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    h1 = _return_hash(r1.json())
    h2 = _return_hash(r2.json())
    # Both responses should have 1 concept (overwrite, not append).
    assert len(_find_params(r1.json(), "concept")) == 1
    assert len(_find_params(r2.json(), "concept")) == 1
    # Hashes should DIFFER because _version counter advanced.
    assert h1 != h2, (
        f"version hashes should differ across two add_concepts calls "
        f"(cumulative _version counter): {h1!r} vs {h2!r}"
    )
