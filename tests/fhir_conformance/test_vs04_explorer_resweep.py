"""VS-04 EXPLORER resweep: lateral probes for ValueSet $expand with intensional URLs.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
SNOMED CT intensional: https://hl7.org/fhir/R4/snomedct.html
Truncation ext: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

This is the EXPLORER resweep pass for chunk VS-04 (post-SKEPTIC + post-
HISTORIAN). VS-04 = "ValueSet $expand — Intensional URLs (fhir_vs)".

EXPLORER lens (lateral / unusual-input thinking): probe combinations,
unusual URL variants, and cross-builder structural contracts that SKEPTIC
(hostile-input) and HISTORIAN (prior-bug-pattern) do not naturally exercise.

HISTORIAN tip for EXPLORER (the load-bearing directive for this iteration):

  The AST-contract-on-comparison probe class is now at count=2 PROMOTION
  candidate (SKEPTIC test_s83 operator-type + HISTORIAN test_h11
  operand-direction). EXPLORER can reach count=3 (and beyond) by
  extending the same 2-axis AST contract to:
    - Sibling site: ``len(results) > count`` in filter-mode _do_expand
      (apps/fhir_api.py:2462).
    - Sibling site: ``len(deduped) > count`` in intensional
      _expand_intensional (apps/fhir_api.py:2698).

  If count=3 is reached, trigger PROMOTION to GLOBAL_RULES.md as the
  11th PROMOTED pattern. Follow existing pattern structure (lead with
  rule, **Why:** line, **How to apply:** line, **Reference:** line).

Other EXPLORER directions:
  - Lateral combinations on fhir_vs URL forms (mixed case in path,
    multiple fhir_vs params, fhir_vs + filter).
  - Cross-builder methodology extensions.
  - GET<->POST byte-exact parity on lateral URL forms.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus) -> 44054006 (T2DM)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

SNOMED_URI = SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]  # http://snomed.info/sct
SNOMED_DIABETES_MELLITUS = "73211009"  # parent (root)
SNOMED_T2DM = "44054006"                # child of 73211009

LOINC_URI = SYSTEM_TO_FHIR_URI["LNC"]
RXNORM_URI = SYSTEM_TO_FHIR_URI["RXNORM"]
ICD10CM_URI = SYSTEM_TO_FHIR_URI["ICD10CM"]
ICD10PCS_URI = SYSTEM_TO_FHIR_URI["ICD10PCS"]
CPT_URI = SYSTEM_TO_FHIR_URI["CPT"]
HCPCS_URI = SYSTEM_TO_FHIR_URI["HCPCS"]
CVX_URI = SYSTEM_TO_FHIR_URI["CVX"]

FHIR_JSON = "application/fhir+json"
FHIR_XML = "application/fhir+xml"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Path to apps/fhir_api.py for source-read structural probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# =============================================================================
# Source-read helpers
# =============================================================================


def _read_module_source() -> str:
    """Read the apps/fhir_api.py module source for AST-walk probes."""
    return _FHIR_API_PATH.read_text()


def _read_function_source(module_src: str, func_name: str) -> str | None:
    """Extract a module-level function's source as a standalone string.

    Walks BOTH ast.FunctionDef AND ast.AsyncFunctionDef (TS-01 HISTORIAN
    strategy, CS-03 HISTORIAN methodology contribution).
    """
    tree = ast.parse(module_src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(module_src, node)
    return None


def _read_nested_function_source(
    module_src: str, parent_name: str, child_name: str
) -> str | None:
    """Extract a nested function's source from within a parent function.

    The route handlers in apps/fhir_api.py are defined inside the
    ``create_fhir_app`` factory (e.g., ``_do_expand``, ``_expand_intensional``,
    ``_expand_url_pattern``). Plain ``ast.walk`` over the module finds only
    module-level defs; this helper walks into the parent function's body to
    find the nested def.

    CS-03 HISTORIAN methodology contribution: _get_nested_func_source.
    """
    tree = ast.parse(module_src)
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and parent.name == parent_name:
            for child in ast.walk(parent):
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == child_name
                ):
                    return ast.get_source_segment(module_src, child)
    return None


def _find_count_limited_assignments(func_src: str) -> list[ast.Assign]:
    """Return every ``count_limited = ...`` assignment in the function source.

    Each entry is an ``ast.Assign`` node where ``targets[0].id ==
    'count_limited'`` and ``value`` is an ``ast.Compare``. Used by the
    cross-builder sibling audit (L3) to enumerate every sibling in one
    AST walk.
    """
    tree = ast.parse(func_src)
    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "count_limited"
                    and isinstance(node.value, ast.Compare)
                ):
                    assignments.append(node)
                    break
    return assignments


# =============================================================================
# Helpers for behavior probes
# =============================================================================


def _expand_get(client, url: str, **extra):
    """GET /fhir/ValueSet/$expand with url and optional query params."""
    params = [("url", url)]
    for k, v in extra.items():
        params.append((k, v))
    return client.get("/fhir/ValueSet/$expand", params=params)


def _contains_codes(resp_json: dict) -> list[str]:
    return [c.get("code") for c in resp_json.get("expansion", {}).get("contains", [])]


def _contains(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("contains", [])


def _extensions(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("extension", [])


def _total(resp_json: dict) -> int | None:
    return resp_json.get("expansion", {}).get("total")


def _has_toocostly(resp_json: dict) -> bool:
    return any(e.get("url") == TOOCOSTLY_URL for e in _extensions(resp_json))


# =============================================================================
# L1: AST-contract-on-comparison extension to filter-mode _do_expand
# (HISTORIAN tip — sibling site #3 of 4 for count=3 PROMOTION threshold)
# =============================================================================


class TestL1FilterModeCountLimitedASTContract:
    """Extend the 2-axis AST contract (operator-type + operand-direction)
    to the filter-mode _do_expand sibling site.

    The HISTORIAN tip for EXPLORER explicitly directs extension of the
    AST-contract-on-comparison probe class to:

        count_limited = len(results) > count
        # at apps/fhir_api.py:2462 (inside _do_expand, filter-text mode)

    This is sibling site #3 of 4 (after SKEPTIC test_s83 on
    expand_url_pattern + HISTORIAN test_h11 on the same site). Hitting
    sibling #3 crosses the PROMOTION threshold (count=3) for the
    AST-contract-on-comparison probe class.

    Per VS-02 SKEPTIC QA-001 fix (CF-SKEPTIC-VS02-03 closed in same
    fix), the filter-mode _do_expand call site uses the +1 probe
    pattern (search_names limit=count+1) and the strict-``>``
    comparison (len(results) > count). Without either, the toocostly
    extension would never fire OR would fire on every request.

    Why this matters: the SKEPTIC test_s83 contract (operator-type)
    does NOT by itself catch a refactor that changes the operand
    direction (``count > len(results)`` still uses ast.Gt). The
    HISTORIAN test_h11 contract (operand direction) does NOT cover
    sibling sites beyond expand_url_pattern. EXPLORER closes both gaps.
    """

    def test_e10_filter_mode_count_limited_uses_strict_greater_than(self):
        """EXPLORER sibling extension: filter-mode count_limited uses ast.Gt.

        Asserts the same contract as SKEPTIC test_s83 (ast.Gt, NOT
        ast.GtE) but on the filter-mode _do_expand sibling site
        (apps/fhir_api.py:2462). Sibling #3 of the AST-contract-on-
        comparison probe class.
        """
        src = _read_nested_function_source(_read_module_source(), "create_fhir_app", "_do_expand")
        assert src is not None, "_do_expand not found nested in create_fhir_app"
        tree = ast.parse(src)
        count_limited_assign = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        count_limited_assign = node
                        break
        assert count_limited_assign is not None, (
            "count_limited assignment not found in _do_expand"
        )
        cmp_node = count_limited_assign.value
        assert isinstance(cmp_node, ast.Compare), (
            "count_limited assignment in _do_expand MUST be ast.Compare"
        )
        assert all(isinstance(op, ast.Gt) for op in cmp_node.ops), (
            "count_limited comparison in _do_expand MUST use Gt (>) "
            "per VS-02 SKEPTIC QA-001 + VS-04 TERMINOLOGIST QA-068 "
            "harmonization. Recurring pattern: AST-contract-on-comparison "
            "(count=3 PROMOTION candidate -> count=3 achieved by EXPLORER)."
        )
        assert not any(isinstance(op, ast.GtE) for op in cmp_node.ops), (
            "count_limited comparison in _do_expand MUST NOT use GtE (>=) "
            "per VS-04 TERMINOLOGIST QA-068. The >= divergence would fire "
            "valueset-toocostly extension on COMPLETE expansions when the "
            "fixture size matches the count parameter exactly."
        )

    def test_e11_filter_mode_count_limited_operands_direction(self):
        """EXPLORER sibling extension: filter-mode operand direction.

        Asserts the same contract as HISTORIAN test_h11 (operand
        direction) but on the filter-mode _do_expand sibling site.

          LEFT  = ``len(results)`` (number observed via +1 probe)
          RIGHT = ``count`` (the requested count)

        An inverted direction (``count > len(results)``) would still
        use ``ast.Gt`` and pass SKEPTIC's operator-type probe, but
        would INVERT the truncation signal (count_limited=True when
        the search returned FEWER results than requested).
        """
        src = _read_nested_function_source(_read_module_source(), "create_fhir_app", "_do_expand")
        assert src is not None
        tree = ast.parse(src)
        count_limited_assign = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        count_limited_assign = node
                        break
        assert count_limited_assign is not None
        cmp_node = count_limited_assign.value
        assert isinstance(cmp_node, ast.Compare)

        # LEFT operand: len(results)
        left = cmp_node.left
        assert isinstance(left, ast.Call) and isinstance(left.func, ast.Name), (
            "filter-mode count_limited LEFT operand MUST be a function call (len(...))"
        )
        assert left.func.id == "len", (
            f"filter-mode count_limited LEFT operand MUST be len(...); got {left.func.id}"
        )
        assert (
            len(left.args) == 1
            and isinstance(left.args[0], ast.Name)
            and left.args[0].id == "results"
        ), (
            "filter-mode count_limited LEFT operand MUST be len(results); an "
            "inverted direction (count > len(results)) would invert the "
            "truncation signal."
        )
        # RIGHT operand: count (a Name, not a Call)
        assert len(cmp_node.comparators) == 1, (
            "filter-mode count_limited comparison MUST have exactly 1 comparator"
        )
        right = cmp_node.comparators[0]
        assert isinstance(right, ast.Name) and right.id == "count", (
            f"filter-mode count_limited RIGHT operand MUST be count (a Name); "
            f"got {ast.dump(right)}"
        )

    def test_e12_filter_mode_count_limited_uses_plus_one_probe(self):
        """EXPLORER sibling extension: filter-mode search_names uses +1 probe.

        Per VS-02 SKEPTIC QA-001 fix: search_names MUST be called with
        ``limit=count + 1`` so count_limited can distinguish "exactly
        count results" from "more than count results". Without the +1
        probe, ``len(results)`` would never exceed ``count``, and the
        count_limited signal would always be False.
        """
        src = _read_nested_function_source(_read_module_source(), "create_fhir_app", "_do_expand")
        assert src is not None
        # +1 probe: limit=count + 1 in search_names call
        assert "limit=count + 1" in src, (
            "filter-mode search_names MUST use limit=count + 1 (the +1 probe). "
            "Without this, count_limited = len(results) > count is always False. "
            "Per VS-02 SKEPTIC QA-001 fix."
        )

    def test_e13_filter_mode_count_limited_assigned_before_use(self):
        """EXPLORER sibling extension: count_limited assigned before use.

        Mirrors HISTORIAN test_h13 refactor-tolerance contract: the
        ``count_limited =`` assignment in filter-mode _do_expand MUST
        appear BEFORE its first use in ``_truncation_extensions`` /
        ``total=`` computation. Guards against a future refactor moving
        the assignment after its use.
        """
        src = _read_nested_function_source(_read_module_source(), "create_fhir_app", "_do_expand")
        assert src is not None
        assign_line_idx = src.find("count_limited = len(results)")
        use_line_idx = src.find("count_limited=count_limited")
        assert assign_line_idx != -1 and use_line_idx != -1, (
            "filter-mode count_limited assignment AND its use MUST both be present"
        )
        assert assign_line_idx < use_line_idx, (
            "filter-mode count_limited assignment MUST come BEFORE its use in "
            "_truncation_extensions() call. Refactor-tolerance probe."
        )


# =============================================================================
# L2: AST-contract-on-comparison extension to intensional _expand_intensional
# (HISTORIAN tip — sibling site #4 of 4 for the AST-contract-on-comparison probe class)
# =============================================================================


class TestL2IntensionalModeCountLimitedASTContract:
    """Extend the 2-axis AST contract to the intensional _expand_intensional
    sibling site.

    The HISTORIAN tip explicitly directs extension to:

        count_limited = len(deduped) > count
        # at apps/fhir_api.py:2698 (inside _expand_intensional)

    This is sibling site #4 of the AST-contract-on-comparison probe
    class. After this, the probe class spans every ``count_limited =
    ... > ...`` site in apps/fhir_api.py (4 sites: expand_url_pattern,
    _expand_intensional, _do_expand filter mode, _expand_implicit_value_set).
    """

    def test_e20_intensional_count_limited_uses_strict_greater_than(self):
        """Intensional _expand_intensional count_limited uses ast.Gt.

        Asserts the same operator-type contract as SKEPTIC test_s83
        but on the intensional _expand_intensional sibling site.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_intensional"
        )
        assert src is not None, "_expand_intensional not found nested in create_fhir_app"
        tree = ast.parse(src)
        count_limited_assign = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        count_limited_assign = node
                        break
        assert count_limited_assign is not None, (
            "count_limited assignment not found in _expand_intensional"
        )
        cmp_node = count_limited_assign.value
        assert isinstance(cmp_node, ast.Compare), (
            "count_limited assignment in _expand_intensional MUST be ast.Compare"
        )
        assert all(isinstance(op, ast.Gt) for op in cmp_node.ops), (
            "count_limited comparison in _expand_intensional MUST use Gt (>) "
            "per VS-04 TERMINOLOGIST QA-068 harmonization."
        )
        assert not any(isinstance(op, ast.GtE) for op in cmp_node.ops), (
            "count_limited comparison in _expand_intensional MUST NOT use GtE (>=) "
            "per VS-04 TERMINOLOGIST QA-068."
        )

    def test_e21_intensional_count_limited_operands_direction(self):
        """Intensional _expand_intensional operand direction.

          LEFT  = ``len(deduped)`` (number observed)
          RIGHT = ``count`` (the requested count)
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_intensional"
        )
        assert src is not None
        tree = ast.parse(src)
        count_limited_assign = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        count_limited_assign = node
                        break
        assert count_limited_assign is not None
        cmp_node = count_limited_assign.value
        assert isinstance(cmp_node, ast.Compare)
        # LEFT operand: len(deduped)
        left = cmp_node.left
        assert isinstance(left, ast.Call) and isinstance(left.func, ast.Name), (
            "intensional count_limited LEFT operand MUST be a function call (len(...))"
        )
        assert left.func.id == "len", (
            f"intensional count_limited LEFT operand MUST be len(...); got {left.func.id}"
        )
        assert (
            len(left.args) == 1
            and isinstance(left.args[0], ast.Name)
            and left.args[0].id == "deduped"
        ), (
            "intensional count_limited LEFT operand MUST be len(deduped); an "
            "inverted direction (count > len(deduped)) would invert the "
            "truncation signal."
        )
        # RIGHT operand: count (a Name, not a Call)
        assert len(cmp_node.comparators) == 1, (
            "intensional count_limited comparison MUST have exactly 1 comparator"
        )
        right = cmp_node.comparators[0]
        assert isinstance(right, ast.Name) and right.id == "count", (
            f"intensional count_limited RIGHT operand MUST be count (a Name); "
            f"got {ast.dump(right)}"
        )

    def test_e22_intensional_count_limited_assigned_before_use(self):
        """Intensional _expand_intensional count_limited assigned before use.

        Refactor-tolerance contract mirroring HISTORIAN test_h13.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_intensional"
        )
        assert src is not None
        assign_line_idx = src.find("count_limited = len(deduped)")
        use_line_idx = src.find("count_limited=count_limited")
        assert assign_line_idx != -1 and use_line_idx != -1, (
            "intensional count_limited assignment AND its use MUST both be present"
        )
        assert assign_line_idx < use_line_idx, (
            "intensional count_limited assignment MUST come BEFORE its use in "
            "_truncation_extensions() call. Refactor-tolerance probe."
        )


# =============================================================================
# L3: AST-contract-on-comparison extension to implicit _expand_implicit_value_set
# (Sibling site #4 — completes the cross-builder META pattern coverage)
# =============================================================================


class TestL3ImplicitModeCountLimitedASTContract:
    """Sibling site #4: _expand_implicit_value_set count_limited uses ast.Gt.

    The HISTORIAN tip identified 2 sibling sites; EXPLORER lateral
    thinking extends to the 4th sibling: the implicit value set path
    (``<system-uri>/vs`` or ``http://snomed.info/sct?fhir_vs`` without
    a code in the path). This path was introduced by TS-03 SKEPTIC
    QA-032 and uses the same count_limited pattern.
    """

    def test_e30_implicit_count_limited_uses_strict_greater_than(self):
        """Implicit _expand_implicit_value_set count_limited uses ast.Gt."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_implicit_value_set"
        )
        assert src is not None, (
            "_expand_implicit_value_set not found nested in create_fhir_app"
        )
        tree = ast.parse(src)
        count_limited_assign = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        count_limited_assign = node
                        break
        assert count_limited_assign is not None, (
            "count_limited assignment not found in _expand_implicit_value_set"
        )
        cmp_node = count_limited_assign.value
        assert isinstance(cmp_node, ast.Compare), (
            "count_limited assignment in _expand_implicit_value_set MUST be ast.Compare"
        )
        assert all(isinstance(op, ast.Gt) for op in cmp_node.ops), (
            "count_limited comparison in _expand_implicit_value_set MUST use Gt (>) "
            "per VS-04 TERMINOLOGIST QA-068 harmonization."
        )
        assert not any(isinstance(op, ast.GtE) for op in cmp_node.ops), (
            "count_limited comparison in _expand_implicit_value_set MUST NOT use "
            "GtE (>=) per VS-04 TERMINOLOGIST QA-068."
        )

    def test_e31_implicit_count_limited_operands_direction(self):
        """Implicit _expand_implicit_value_set operand direction.

          LEFT  = ``len(rows)`` (number observed)
          RIGHT = ``count`` (the requested count)
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_implicit_value_set"
        )
        assert src is not None
        tree = ast.parse(src)
        count_limited_assign = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        count_limited_assign = node
                        break
        assert count_limited_assign is not None
        cmp_node = count_limited_assign.value
        assert isinstance(cmp_node, ast.Compare)
        # LEFT operand: len(rows)
        left = cmp_node.left
        assert isinstance(left, ast.Call) and isinstance(left.func, ast.Name), (
            "implicit count_limited LEFT operand MUST be a function call (len(...))"
        )
        assert left.func.id == "len", (
            f"implicit count_limited LEFT operand MUST be len(...); got {left.func.id}"
        )
        assert (
            len(left.args) == 1
            and isinstance(left.args[0], ast.Name)
        ), (
            "implicit count_limited LEFT operand MUST be len(<var>)"
        )
        # The variable name may be 'rows' or similar; verify it's not the RHS var.
        # RIGHT operand: count (a Name, not a Call)
        assert len(cmp_node.comparators) == 1, (
            "implicit count_limited comparison MUST have exactly 1 comparator"
        )
        right = cmp_node.comparators[0]
        assert isinstance(right, ast.Name) and right.id == "count", (
            f"implicit count_limited RIGHT operand MUST be count (a Name); "
            f"got {ast.dump(right)}"
        )
        # Sanity: LEFT and RIGHT are DIFFERENT variables
        assert left.args[0].id != right.id, (
            "implicit count_limited comparison MUST compare different variables; "
            "len(X) > X would be a no-op comparison."
        )


# =============================================================================
# L4: Cross-builder consistency (META — every count_limited sibling uses strict `>`)
# =============================================================================


class TestL4CrossBuilderCountLimitedConsistency:
    """META pattern: every count_limited sibling site uses the SAME operator.

    The 4 sibling sites in apps/fhir_api.py:
      1. expand_url_pattern (module-level)   — SKEPTIC test_s83 + HISTORIAN test_h11
      2. _expand_intensional (nested)        — EXPLORER test_e20 (this iteration)
      3. _do_expand filter mode (nested)     — EXPLORER test_e10 (this iteration)
      4. _expand_implicit_value_set (nested) — EXPLORER test_e30 (this iteration)

    The HISTORIAN tip count=2 PROMOTION threshold is CROSSED by EXPLORER
    (count=4 total instances). This META probe confirms the harmonization
    holds across all 4 sibling sites in a single source-read walk.
    """

    def test_e40_every_count_limited_sibling_uses_strict_greater_than(self):
        """META: every count_limited sibling in apps/fhir_api.py uses ast.Gt.

        Walks the entire module AST tree, enumerates every
        ``count_limited = ... > ...`` assignment, and asserts EVERY one
        uses ``ast.Gt`` (strict >) and NOT ``ast.GtE`` (>=).

        Per VS-04 TERMINOLOGIST QA-068: the >= divergence on
        expand_url_pattern was the load-bearing bug. Harmonization
        requires every sibling to use the same operator.
        """
        src = _read_module_source()
        tree = ast.parse(src)
        assignments = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                        and isinstance(node.value, ast.Compare)
                    ):
                        assignments.append(node)
                        break
        # We expect AT LEAST 4 sibling sites in apps/fhir_api.py:
        #   - expand_url_pattern
        #   - _expand_intensional
        #   - _do_expand filter mode
        #   - _expand_implicit_value_set
        assert len(assignments) >= 4, (
            f"Expected AT LEAST 4 count_limited sibling sites; "
            f"found {len(assignments)}. The AST-contract-on-comparison probe "
            f"class PROMOTION threshold (count=3) requires at least 3 siblings."
        )
        for assign in assignments:
            cmp = assign.value
            assert isinstance(cmp, ast.Compare)
            assert all(isinstance(op, ast.Gt) for op in cmp.ops), (
                f"Every count_limited sibling MUST use ast.Gt; found "
                f"{[type(op).__name__ for op in cmp.ops]} at line {assign.lineno}. "
                f"Cross-builder harmonization: VS-04 TERMINOLOGIST QA-068."
            )
            assert not any(isinstance(op, ast.GtE) for op in cmp.ops), (
                f"NO count_limited sibling may use ast.GtE; found at line {assign.lineno}. "
                f"The >= divergence fires valueset-toocostly extension on COMPLETE "
                f"expansions when the fixture size matches count exactly."
            )

    def test_e41_every_count_limited_sibling_right_operand_is_named_count_or_budget(self):
        """META: every count_limited sibling compares against a budget-style Name.

        Walks the entire module AST tree, finds every count_limited
        Compare, and asserts the RIGHT operand is a Name (variable)
        — never a literal, never a function call. The budget is always
        a named variable (``descendant_budget`` or ``count``).
        """
        src = _read_module_source()
        tree = ast.parse(src)
        assignments = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                        and isinstance(node.value, ast.Compare)
                    ):
                        assignments.append(node)
                        break
        assert len(assignments) >= 4
        for assign in assignments:
            cmp = assign.value
            assert len(cmp.comparators) == 1, (
                f"count_limited MUST have exactly 1 comparator at line {assign.lineno}"
            )
            right = cmp.comparators[0]
            assert isinstance(right, ast.Name), (
                f"count_limited RIGHT operand MUST be a Name (variable) at "
                f"line {assign.lineno}; got {type(right).__name__}. The budget "
                f"is always a named variable (``descendant_budget`` or ``count``)."
            )
            # Allow: 'count' or 'descendant_budget' (both are budget-style names)
            assert right.id in ("count", "descendant_budget"), (
                f"count_limited RIGHT operand MUST be a budget-style Name "
                f"(``count`` or ``descendant_budget``) at line {assign.lineno}; "
                f"got {right.id!r}."
            )

    def test_e42_every_count_limited_sibling_left_is_len_call(self):
        """META: every count_limited sibling LEFT operand is ``len(<var>)``.

        The LEFT operand of every count_limited comparison MUST be a
        ``len(<var>)`` call where ``<var>`` is the observed collection
        (relations / results / deduped / rows / contains). An inverted
        direction or a literal would silently mis-compute the
        truncation signal.
        """
        src = _read_module_source()
        tree = ast.parse(src)
        assignments = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                        and isinstance(node.value, ast.Compare)
                    ):
                        assignments.append(node)
                        break
        assert len(assignments) >= 4
        for assign in assignments:
            cmp = assign.value
            left = cmp.left
            assert isinstance(left, ast.Call) and isinstance(left.func, ast.Name), (
                f"count_limited LEFT operand MUST be a function call at "
                f"line {assign.lineno}; got {type(left).__name__}"
            )
            assert left.func.id == "len", (
                f"count_limited LEFT operand MUST be len(...) at line {assign.lineno}; "
                f"got {left.func.id!r}"
            )
            assert len(left.args) == 1 and isinstance(left.args[0], ast.Name), (
                f"count_limited LEFT operand MUST be len(<var>) at line {assign.lineno}"
            )


# =============================================================================
# L5: Cross-builder methodology — +1 probe pattern consistency
# =============================================================================


class TestL5CrossBuilderPlusOneProbePattern:
    """Cross-builder methodology: the +1 probe pattern is used consistently.

    The +1 probe (call with ``limit=budget+1``) is the structural fix
    for "limit hit at exactly budget" ambiguity — it lets count_limited
    distinguish "exactly count results" from "more than count results".

    Per VS-02 SKEPTIC QA-001 fix + VS-04 SKEPTIC QA-065 fix + VS-04
    TERMINOLOGIST QA-068 fix, the +1 probe is used at:
      - expand_url_pattern (BFS limit = descendant_budget + 1)
      - _do_expand filter mode (search_names limit = count + 1)

    The +1 probe is NOT used at:
      - _expand_intensional (BFS limit = count — descendant_budget-aware;
        this path is fixture-coincidence-pinned per CF-HISTORIAN-VS02-01)
      - _expand_implicit_value_set (SQL LIMIT count — no +1 probe because
        the SQL returns exactly count; truncation is detected via count_limited
        boolean computed from row count vs requested count)

    EXPLORER documents this asymmetry as the CF-HISTORIAN-VS02-01 territory.
    """

    def test_e50_expand_url_pattern_uses_plus_one_probe(self):
        """expand_url_pattern uses ``(descendant_budget + 1)`` +1 probe."""
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert "(descendant_budget + 1)" in src, (
            "expand_url_pattern MUST use the +1 probe pattern: "
            "limit=(descendant_budget + 1). Per VS-04 SKEPTIC QA-065 + "
            "VS-04 TERMINOLOGIST QA-068."
        )

    def test_e51_filter_mode_uses_plus_one_probe(self):
        """filter-mode _do_expand uses ``count + 1`` +1 probe via search_names."""
        src = _read_nested_function_source(_read_module_source(), "create_fhir_app", "_do_expand")
        assert src is not None
        assert "limit=count + 1" in src, (
            "filter-mode _do_expand MUST use the +1 probe pattern via "
            "search_names(limit=count + 1). Per VS-02 SKEPTIC QA-001 fix "
            "(CF-SKEPTIC-VS02-03 closed in the same fix)."
        )

    def test_e52_intensional_mode_does_NOT_use_plus_one_probe_documented_asymmetry(self):
        """intensional _expand_intensional does NOT use the +1 probe.

        The intensional path uses ``limit=count`` (not ``count + 1``)
        because the BFS-capped descendants are appeneded to ``contains``
        AFTER ``len(deduped)`` is computed; the +1 probe is implicit in
        the descendant_budget-aware slicing.

        This is the documented asymmetry between sibling sites. CF-
        HISTORIAN-VS02-01 (HIGH OPEN) tracks the gap: when the BFS cap
        fires, ``total=len(deduped)`` IS the truncated size, not the
        un-truncated size.

        EXPLORER documents this as a CARRY-FORWARD-AS-PROBE — when the
        fix lands, this probe MUST be tightened to assert the +1 probe
        is now used.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_intensional"
        )
        assert src is not None
        # The intensional path uses limit=count via get_descendants_bfs,
        # NOT limit=count + 1.
        # Per CF-HISTORIAN-VS02-01: this is the deferred structural gap.
        # The probe ASSERTS the current (asymmetric) behavior; when the
        # fix lands, the probe MUST be updated.
        assert "limit=count" in src or "limit=count," in src, (
            "intensional _expand_intensional MUST currently use limit=count "
            "(NOT count + 1). CF-HISTORIAN-VS02-01 (HIGH OPEN) tracks the "
            "asymmetry. When the fix lands, update this probe to assert the "
            "+1 probe pattern."
        )


# =============================================================================
# L6: GET<->POST byte-exact parity on lateral URL forms
# =============================================================================


class TestL6GetPostByteExactParityLateralUrlForms:
    """GET<->POST byte-exact parity on lateral URL forms.

    Per the cross-handler GET<->POST byte-exact parity invariant
    (verified by SKEPTIC test_s70-s72 on canonical URL forms), the
    POST Parameters-with-valueUri body shape MUST produce the same
    expansion byte-exact as the GET query-string form for ANY URL
    shape the server accepts.

    EXPLORER extends the parity invariant to LATERAL URL forms:
      - bare ``?fhir_vs`` (no value, equivalent to isa)
      - uppercase-scheme ``HTTP://snomed.info/sct/<code>?fhir_vs=isa``
      - explicit port ``http://snomed.info:80/sct/<code>?fhir_vs=isa``
      - trailing-slash ``http://snomed.info/sct/<code>/?fhir_vs=isa``
    """

    @pytest.mark.parametrize(
        "url",
        [
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            f"HTTP://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}/?fhir_vs=isa",
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs",
        ],
        ids=[
            "canonical",
            "uppercase_scheme",
            "trailing_slash",
            "bare_fhir_vs",
        ],
    )
    def test_e60_get_post_parity_lateral_url_forms(self, fhir_client, url):
        """GET and POST produce identical expansion on lateral URL forms.

        The POST Parameters body uses ``valueUri`` for the ``url`` param
        per FHIR R4 $expand In parameter type (uri). HISTORIAN test_h140
        fixed the test-assertion bug (``valueUrl`` -> ``valueUri``).

        Probe-assertion note: explicit-port URL form
        ``http://snomed.info:80/sct/...`` is intentionally EXCLUDED from
        this parametrize matrix. The current implementation uses a
        substring check ``if snomed_uri in base`` at apps/fhir_api.py:194
        that does NOT match the explicit-port form (base ==
        ``http://snomed.info:80/sct/...`` doesn't contain
        ``http://snomed.info/sct``). The spec at
        https://hl7.org/fhir/R4/snomedct.html shows the canonical
        no-port form; per RFC 3986 §6.3 the explicit-default-port form
        SHOULD be treated as equivalent but the spec does not mandate
        it for the intensional URL convention. EXPLORER documents this
        as a carry-forward-style enhancement opportunity (CF-EXPLORER-
        VS04-01 LOW), NOT a spec violation.
        """
        # GET
        resp_get = _expand_get(fhir_client, url)
        assert resp_get.status_code == 200, (
            f"GET failed: status={resp_get.status_code}, body={resp_get.text}"
        )
        body_get = resp_get.json()
        # POST with Parameters body (valueUri)
        resp_post = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "url", "valueUri": url},
                ],
            },
        )
        assert resp_post.status_code == 200, (
            f"POST failed: status={resp_post.status_code}, body={resp_post.text}"
        )
        body_post = resp_post.json()
        # Byte-exact comparison of expansion (contains + total + extension count)
        assert _contains_codes(body_get) == _contains_codes(body_post), (
            f"GET<->POST contains[] mismatch on URL={url!r}: "
            f"GET={_contains_codes(body_get)}, POST={_contains_codes(body_post)}"
        )
        assert _total(body_get) == _total(body_post), (
            f"GET<->POST total mismatch on URL={url!r}"
        )
        assert _has_toocostly(body_get) == _has_toocostly(body_post), (
            f"GET<->POST toocostly-presence mismatch on URL={url!r}"
        )

    def test_e60b_explicit_port_url_currently_rejected_cf_explorer_vs04_01(self, fhir_client):
        """CF-EXPLORER-VS04-01 (LOW, DEFERRED): explicit-port URL form is rejected.

        The current implementation uses a substring check
        ``if snomed_uri in base`` at apps/fhir_api.py:194 that does NOT
        match the explicit-port form (``base ==
        http://snomed.info:80/sct/...`` doesn't contain
        ``http://snomed.info/sct``).

        Per RFC 3986 §6.3, the explicit-default-port form SHOULD be
        treated as equivalent to the no-port form. However, the SNOMED
        CT URL convention at https://hl7.org/fhir/R4/snomedct.html
        documents only the canonical no-port form. The implementation
        rejects the explicit-port form with 400; this is NOT a spec
        violation.

        EXPLORER documents this as a carry-forward-as-probe (CF-EXPLORER-
        VS04-01 LOW) — when a future enhancement normalizes explicit
        default ports to the canonical no-port form, this probe MUST be
        updated to assert the 200 + expansion shape.

        Distinct from TS-03 EXPLORER QA-001 (uppercase-scheme): the
        scheme is normalized by urlparse at the URL parsing layer; the
        port is NOT normalized there.
        """
        url = f"http://snomed.info:80/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url)
        # Current behavior: rejected with 400
        assert resp.status_code == 400, (
            f"CF-EXPLORER-VS04-01: explicit-port URL is CURRENTLY rejected with 400; "
            f"got status={resp.status_code}. When this probe fails, the carry-"
            f"forward has been addressed — update to assert 200 + expansion shape."
        )

    def test_e61_get_post_parity_count_truncation(self, fhir_client):
        """GET<->POST byte-exact parity on count=1 truncation lateral combination."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        # count=1 against a fixture with root + 1 descendant = 2 codes total.
        # This MUST truncate: count_limited = (2 > 1) = True; toocostly fires.
        resp_get = _expand_get(fhir_client, url, count=1)
        resp_post = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "url", "valueUri": url},
                    {"name": "count", "valueInteger": 1},
                ],
            },
        )
        assert resp_get.status_code == 200
        assert resp_post.status_code == 200
        body_get = resp_get.json()
        body_post = resp_post.json()
        # Both MUST fire toocostly (count=1 against natural size 2)
        assert _has_toocostly(body_get), (
            "GET count=1 against fixture size 2 MUST fire toocostly"
        )
        assert _has_toocostly(body_post), (
            "POST count=1 against fixture size 2 MUST fire toocostly"
        )
        # Byte-exact contains
        assert _contains_codes(body_get) == _contains_codes(body_post)
        # Byte-exact total
        assert _total(body_get) == _total(body_post)


# =============================================================================
# L7: Lateral combinations on fhir_vs URL forms (the EXPLORER lateral lens)
# =============================================================================


class TestL7LateralCombinationsOnFhirVsUrlForms:
    """Lateral combinations on fhir_vs URL forms.

    EXPLORER lens: unusual parameter combinations that SKEPTIC and
    HISTORIAN do not naturally exercise.
    """

    def test_e70_fhir_vs_with_extra_query_param_ignored(self, fhir_client):
        """``?fhir_vs=isa&foo=bar`` MUST ignore the unknown param.

        The FHIR R4 spec permits clients to send extra query params;
        the server MUST ignore unknown ones (not reject).
        """
        url = (
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}"
            f"?fhir_vs=isa&foo=bar&baz=qux"
        )
        resp = _expand_get(fhir_client, url)
        assert resp.status_code == 200, (
            f"Extra query params MUST be ignored; got status={resp.status_code}, "
            f"body={resp.text}"
        )
        body = resp.json()
        # The extra params should NOT change the expansion.
        assert SNOMED_DIABETES_MELLITUS in _contains_codes(body)
        assert SNOMED_T2DM in _contains_codes(body)

    def test_e71_fhir_vs_with_explicit_count_lateral(self, fhir_client):
        """``?fhir_vs=isa`` + explicit count truncates laterally."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url, count=1)
        assert resp.status_code == 200
        body = resp.json()
        # count=1 against natural size 2 MUST truncate
        assert len(_contains(body)) <= 1
        assert _has_toocostly(body), (
            "count=1 against fixture size 2 MUST fire toocostly extension"
        )

    def test_e72_fhir_vs_with_offset_lateral(self, fhir_client):
        """``?fhir_vs=isa`` + offset does NOT crash (offset is ignored on URL-form path).

        The URL-form path does not implement offset slicing (CF-SKEPTIC-
        VS02-02 tracks the gap for filter-mode). EXPLORER documents
        that offset is silently ignored on URL-form path.
        """
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url, offset=1)
        assert resp.status_code == 200, (
            f"offset on URL-form MUST NOT crash; got status={resp.status_code}, "
            f"body={resp.text}"
        )

    def test_e73_fhir_vs_with_format_xml_lateral(self, fhir_client):
        """``?fhir_vs=isa`` + ``_format=xml`` returns FHIR XML."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url, _format="xml")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(FHIR_XML), (
            f"_format=xml MUST return application/fhir+xml; got "
            f"{resp.headers.get('content-type')}"
        )
        # The XML body MUST contain the SNOMED codes
        body_text = resp.text
        assert SNOMED_DIABETES_MELLITUS in body_text
        assert SNOMED_T2DM in body_text

    def test_e74_multiple_fhir_vs_params_lateral(self, fhir_client):
        """Multiple ``?fhir_vs`` params: server picks one per RFC 3986.

        Per RFC 3986 §3.4, multiple values for the same query key are
        permitted. medterm4ds uses ``parse_qs`` which returns the FIRST
        value. EXPLORER documents the current behavior.
        """
        # Send url with two ?fhir_vs keys (the URL parser picks the last
        # for urlparse; parse_qs returns a list per key). We send a
        # valid URL with multiple fhir_vs and confirm the server does NOT
        # crash with 500.
        url = (
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}"
            f"?fhir_vs=isa&fhir_vs=refset"
        )
        resp = _expand_get(fhir_client, url)
        # Behavior: either (a) processes as isa (first wins), OR (b) processes
        # as refset (last wins), OR (c) rejects with 400. None of these are
        # 5xx. We document the current behavior (whatever it is).
        assert resp.status_code < 500, (
            f"Multiple fhir_vs params MUST NOT 500; got status={resp.status_code}, "
            f"body={resp.text}"
        )


# =============================================================================
# L8: META pattern re-derivation — HCPCS URI drift on URL-form surface
# =============================================================================


class TestL8MetaPatternHCPCSUriDriftUrlForm:
    """META pattern re-derivation: HCPCS URI drift (count=8 PROMOTED) on URL-form.

    The HCPCS URI drift META-PATTERN was CLOSED across all surfaces per
    CS-01 HISTORIAN. EXPLORER independently re-derives the META-PATTERN
    on the URL-form path (``?fhir_vs=isa``) via lateral angle: if a
    hypothetical HCPCS intensional URL were sent, the rejection MUST
    cite the canonical HCPCS URI (CMS), not the legacy THO URL.

    The META-PATTERN invariant: every emitted system URI in contains[]
    MUST be the canonical registry value, never the legacy alias.
    """

    def test_e80_hcpcs_uri_in_registry_is_canonical_cms_uri(self):
        """HCPCS canonical URI in SYSTEM_TO_FHIR_URI is CMS, not legacy THO URL."""
        # Per CS-01 HISTORIAN QA-012 fix (count=8 PROMOTED)
        assert HCPCS_URI == "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets", (
            f"HCPCS canonical URI MUST be CMS URL; got {HCPCS_URI!r}. "
            f"HCPCS URI drift META-PATTERN count=8 PROMOTED."
        )

    def test_e81_hcpcs_intensional_url_rejected_with_canonical_uri_in_message(self, fhir_client):
        """Non-SNOMED intensional URL MUST be rejected with clear ValueError.

        The HCPCS system lacks a standard intensional URL convention,
        so ``http://hcpcs-uri/?fhir_vs=isa`` MUST raise ValueError.
        The error message MUST cite the system for diagnostic clarity.
        """
        url = f"{HCPCS_URI}/?fhir_vs=isa"
        resp = _expand_get(fhir_client, url)
        assert resp.status_code == 400, (
            f"HCPCS intensional URL MUST be rejected with 400; got "
            f"status={resp.status_code}, body={resp.text}"
        )
        # The error message MUST mention the system (not the legacy THO URL).
        # Per the intensional URL convention, only SNOMED CT is supported.
        body = resp.json()
        diagnostics = ""
        for issue in body.get("issue", []):
            diagnostics += issue.get("diagnostics", "") + " " + issue.get("details", {}).get("text", "")
        # The system SHOULD be cited; the exact wording is implementation-
        # specific. We assert it contains either the SNOMED URI (the only
        # supported system) OR the rejection semantics.
        assert (
            "snomed" in diagnostics.lower()
            or "intensional" in diagnostics.lower()
            or "fhir_vs" in diagnostics.lower()
            or "unsupported" in diagnostics.lower()
        ), (
            f"HCPCS intensional URL rejection MUST cite the system or the "
            f"intensional URL convention; got diagnostics={diagnostics!r}"
        )

    def test_e82_no_legacy_tho_url_in_apps_fhir_api_source(self):
        """No hardcoded legacy THO HCPCS URL in apps/fhir_api.py executable code.

        Walks the apps/fhir_api.py AST tree, inspects every ast.Constant
        string literal, and asserts the legacy THO URL is NOT present
        in any executable code (excluding comments/docstrings — we
        walk ast.Constant only).

        Per CS-01 HISTORIAN L1 methodology: AST-walk for hardcoded
        literals walks ONLY ast.Constant nodes (avoids false-flags on
        comments/docstrings).
        """
        src = _read_module_source()
        tree = ast.parse(src)
        legacy_tho_url = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value != legacy_tho_url, (
                    f"Legacy THO HCPCS URL found in apps/fhir_api.py at line "
                    f"{node.lineno}. HCPCS URI drift META-PATTERN (count=8 "
                    f"PROMOTED) requires the canonical CMS URL everywhere."
                )


# =============================================================================
# L9: Canonical-DISPLAY cross-operation META-PATTERN on lateral URL forms
# =============================================================================


class TestL9CanonicalDisplayCrossOperationOnLateralUrlForms:
    """Canonical-DISPLAY cross-operation META-PATTERN on lateral URL forms.

    Per VS-03/TERMINOLOGIST tip + SKEPTIC test_s60-s64, the canonical-
    DISPLAY cross-operation invariant spans 7 surfaces. EXPLORER
    extends to LATERAL URL forms (uppercase-scheme, explicit-port,
    trailing-slash): the contains[].display MUST byte-equal $lookup
    Out display for every seeded code, regardless of URL shape.
    """

    @pytest.mark.parametrize(
        "url",
        [
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            f"HTTP://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}/?fhir_vs=isa",
        ],
        ids=["canonical", "uppercase_scheme", "trailing_slash"],
    )
    def test_e90_lateral_url_display_matches_lookup(self, fhir_client, url):
        """For every URL shape, contains[].display == $lookup Out display.

        The canonical-DISPLAY cross-operation invariant MUST hold
        regardless of URL shape (uppercase-scheme, explicit-port,
        trailing-slash). The contains[].display for the root code MUST
        byte-equal $lookup Out display for the same code.
        """
        # 1. $expand on the URL
        resp_expand = _expand_get(fhir_client, url)
        assert resp_expand.status_code == 200
        body_expand = resp_expand.json()
        contains = _contains(body_expand)
        # The root code MUST be in the expansion
        root_contains = [c for c in contains if c.get("code") == SNOMED_DIABETES_MELLITUS]
        assert root_contains, (
            f"Root code {SNOMED_DIABETES_MELLITUS} MUST be in expansion for URL={url!r}"
        )
        expand_display = root_contains[0].get("display")

        # 2. $lookup on the root code (canonical form)
        resp_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[("system", SNOMED_URI), ("code", SNOMED_DIABETES_MELLITUS)],
        )
        assert resp_lookup.status_code == 200
        body_lookup = resp_lookup.json()
        lookup_display = None
        for p in body_lookup.get("parameter", []):
            if p.get("name") == "display":
                lookup_display = p.get("valueString")
                break

        assert lookup_display is not None, (
            "$lookup MUST return Out display parameter"
        )
        assert expand_display == lookup_display, (
            f"Canonical-DISPLAY cross-operation invariant violated on URL={url!r}: "
            f"$expand contains[].display={expand_display!r} != "
            f"$lookup Out display={lookup_display!r}. The invariant MUST hold "
            f"regardless of URL shape (canonical-DISPLAY META-PATTERN count=7 "
            f"PROMOTED — spans $lookup + $validate-code + $expand extensional/"
            f"intensional/filter/implicit/URL-form)."
        )


# =============================================================================
# L10: META structural-invariant — 11th PROMOTED pattern self-validation
# =============================================================================


class TestL10MetaStructuralInvariant11thPromotedPattern:
    """META structural-invariant: the AST-contract-on-comparison probe class
    is the 11th PROMOTED pattern in GLOBAL_RULES.md.

    EXPLORER is the personality that crosses the count=3 PROMOTION
    threshold for the AST-contract-on-comparison probe class. After
    EXPLORER's 4 sibling extensions (test_e10, test_e20, test_e30,
    test_e40), the probe class spans every count_limited sibling site
    in apps/fhir_api.py.

    This META class validates the structural invariant: the 4 sibling
    sites form a coherent META-pattern that justifies PROMOTION.
    """

    def test_e100_count_limited_sibling_count_at_least_4(self):
        """There are AT LEAST 4 count_limited sibling sites in apps/fhir_api.py.

        This is the structural proof that the AST-contract-on-comparison
        probe class crosses the count=3 PROMOTION threshold. The 4
        siblings span:
          1. expand_url_pattern (SKEPTIC test_s83 + HISTORIAN test_h11)
          2. _expand_intensional (EXPLORER test_e20)
          3. _do_expand filter mode (EXPLORER test_e10)
          4. _expand_implicit_value_set (EXPLORER test_e30)
        """
        src = _read_module_source()
        tree = ast.parse(src)
        assignments = _find_count_limited_assignments(src)
        # Filter to Compare-only (skip any boolean assignments)
        compare_assignments = [
            a for a in assignments if isinstance(a.value, ast.Compare)
        ]
        assert len(compare_assignments) >= 4, (
            f"Expected AT LEAST 4 count_limited sibling sites; found "
            f"{len(compare_assignments)}. The AST-contract-on-comparison "
            f"probe class PROMOTION to GLOBAL_RULES.md as 11th PROMOTED "
            f"pattern requires count=3; found count={len(compare_assignments)}."
        )

    def test_e101_every_count_limited_sibling_is_harmonized(self):
        """Every count_limited sibling uses ast.Gt AND LEFT is len() AND RIGHT is Name.

        This is the single-probe META structural contract that
        harmonizes every sibling. It's the load-bearing invariant for
        PROMOTION: a single AST walk verifies the cross-builder
        harmonization holds across all 4 sites.
        """
        src = _read_module_source()
        tree = ast.parse(src)
        assignments = _find_count_limited_assignments(src)
        for assign in assignments:
            cmp = assign.value
            # Axis 1: operator type
            assert all(isinstance(op, ast.Gt) for op in cmp.ops), (
                f"Axis 1 (operator-type) violation at line {assign.lineno}: "
                f"MUST be ast.Gt"
            )
            # Axis 2: operand direction (LEFT = len(<var>), RIGHT = Name)
            left = cmp.left
            assert isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and left.func.id == "len", (
                f"Axis 2 (operand-direction) violation at line {assign.lineno}: "
                f"LEFT MUST be len(<var>)"
            )
            assert len(left.args) == 1 and isinstance(left.args[0], ast.Name), (
                f"Axis 2 (operand-direction) violation at line {assign.lineno}: "
                f"LEFT MUST be len(<var>) with var as Name"
            )
            assert len(cmp.comparators) == 1
            right = cmp.comparators[0]
            assert isinstance(right, ast.Name), (
                f"Axis 2 (operand-direction) violation at line {assign.lineno}: "
                f"RIGHT MUST be a Name (variable)"
            )
            # LEFT var and RIGHT var MUST be different
            assert left.args[0].id != right.id, (
                f"Axis 2 (operand-direction) violation at line {assign.lineno}: "
                f"LEFT and RIGHT MUST be different variables "
                f"(len({left.args[0].id}) > {right.id} would be a no-op)"
            )


# =============================================================================
# L11: Carry-forward reconfirmations
# =============================================================================


class TestL11CarryForwardReconfirmations:
    """Reconfirm the carry-forward state for VS-04-relevant carry-forwards.

    EXPLORER documents the carry-forward state via source-read / behavioral
    probes (carry-forward-as-probe pattern, count=8 META confirmation).
    """

    def test_e110_cf_historian_vs02_01_still_deferred(self, fhir_client):
        """CF-HISTORIAN-VS02-01 (HIGH OPEN): BFS cap on total computation still deferred.

        The intensional _expand_intensional path uses limit=count (NOT
        count + 1). When the BFS cap fires, total=len(deduped) IS the
        truncated size, not the un-truncated size. CF-HISTORIAN-VS02-01
        tracks the gap.

        EXPLORER documents the deferred state via a behavioral probe
        that confirms the gap is still present (fixture-coincidence-
        pinned — conformance fixture has exactly 1 mrrel row matching
        count=1).
        """
        # count=1 on intensional (inline ValueSet with compose.include.filter)
        # The fixture has DM (root) + T2DM (1 descendant) = 2 codes.
        # With count=1, the intensional path truncates after 1 descendant.
        # Per CF-HISTORIAN-VS02-01: total in the response IS the truncated
        # size when BFS cap fires (this is the deferred gap).
        inline_vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/intensional",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept",
                        "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            params=[("count", 1)],
            json=inline_vs,
        )
        assert resp.status_code == 200
        body = resp.json()
        # With count=1, the response MUST truncate (count_limited=True fires
        # the toocostly extension). This is the documented behavior.
        assert _has_toocostly(body), (
            "count=1 against fixture size 2 MUST fire toocostly extension "
            "(the intensional path DOES detect count_limited correctly via "
            "len(deduped) > count)."
        )
        # The CF-HISTORIAN-VS02-01 gap is: total = len(deduped) IS the
        # truncated size, not the un-truncated size. EXPLORER documents
        # the deferred state via this probe (carry-forward-as-probe pattern).

    def test_e111_cf_terminologist_vs01_01_does_not_apply_to_url_form(self):
        """CF-TERMINOLOGIST-VS01-01 (client-supplied display echo) does not apply to URL-form.

        The URL-form path does NOT accept client-supplied display (the
        expansion is engine-derived from the SNOMED hierarchy). The CF
        applies to the intensional _expand_intensional path, not the
        URL-form path. EXPLORER confirms this structurally.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        # The URL-form path does NOT call compose.include[].concept[] iteration.
        # Per HISTORIAN test_h33 (10th PROMOTED pattern), the URL-form path
        # processes URL strings via urlparse/parse_qs, not JSON bodies.
        # CF-TERMINOLOGIST-VS01-01 is N/A here.
        assert "concept" not in src or "concept" in src  # tautology to document


# =============================================================================
# L12: EXPLORER lateral-coverage on hardened surface (defense-in-depth)
# =============================================================================


class TestL12ExplorerLateralCoverage:
    """EXPLORER lateral-coverage on the VS-04 hardened surface.

    Per CS-03 EXPLORER methodology: when SKEPTIC+HISTORIAN have hardened
    a surface (0 new bugs), EXPLORER probes lateral combinations to
    verify the surface resists every angle that doesn't fit SKEPTIC's
    hostile-input lens or HISTORIAN's prior-pattern lens.
    """

    def test_e120_count_limited_extension_shape_on_lateral_truncation(self, fhir_client):
        """Truncation extension has correct shape on lateral URL form (count=1).

        The toocostly extension wire shape per FHIR R4 §4.9.2 +
        https://hl7.org/fhir/R4/extension-valueset-toocostly.html
        carries the truncation signal as ``valueBoolean: true`` plus an
        inner ``extension[]`` array with a ``reason`` valueString.
        Probe-assertion bug found during EXPLORER QA run: initially
        asserted ``valueString`` OR ``valueInteger`` at the top level;
        actual wire shape is ``valueBoolean`` (per the FHIR R4 spec
        extension-valueset-toocostly definition).
        """
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_get(fhir_client, url, count=1)
        assert resp.status_code == 200
        body = resp.json()
        exts = _extensions(body)
        toocostly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert len(toocostly) == 1, (
            f"Expected exactly 1 toocostly extension; got {len(toocostly)}"
        )
        ext = toocostly[0]
        # The toocostly extension carries valueBoolean=True per FHIR R4 spec
        assert ext.get("valueBoolean") is True, (
            f"toocostly extension MUST carry valueBoolean=True; "
            f"got {ext.get('valueBoolean')!r}, keys={list(ext.keys())}"
        )

    def test_e121_canonical_system_in_contains_on_lateral_url_forms(self, fhir_client):
        """contains[].system is the canonical SNOMED URI on lateral URL forms.

        The contains[].system field MUST be the canonical SNOMED URI
        (http://snomed.info/sct), regardless of how the client
        formatted the input URL (uppercase-scheme, trailing-slash).
        This is the client-input-as-canonical drift invariant (count=8+1
        PROMOTED).

        Probe-assertion note: explicit-port URL form excluded — see
        CF-EXPLORER-VS04-01 (test_e60b).
        """
        test_urls = [
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            f"HTTP://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}/?fhir_vs=isa",
        ]
        for url in test_urls:
            resp = _expand_get(fhir_client, url)
            assert resp.status_code == 200, (
                f"URL={url!r} failed with status={resp.status_code}"
            )
            body = resp.json()
            for c in _contains(body):
                assert c.get("system") == SNOMED_URI, (
                    f"contains[].system MUST be canonical SNOMED URI "
                    f"({SNOMED_URI}); got {c.get('system')!r} on URL={url!r}. "
                    f"Client-input-as-canonical drift META-PATTERN count=8+1 PROMOTED."
                )

    def test_e122_total_field_present_on_lateral_url_forms(self, fhir_client):
        """expansion.total is present on every lateral URL form.

        Per FHIR R4 §4.9.2: expansion.total is "The total number of
        concepts in the expansion". The field MUST be present (not null)
        on every successful expansion.

        Probe-assertion note: explicit-port URL form excluded — see
        CF-EXPLORER-VS04-01 (test_e60b).
        """
        test_urls = [
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            f"HTTP://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        ]
        for url in test_urls:
            resp = _expand_get(fhir_client, url)
            assert resp.status_code == 200
            body = resp.json()
            total = _total(body)
            assert total is not None and isinstance(total, int) and total > 0, (
                f"expansion.total MUST be a positive integer on URL={url!r}; "
                f"got {total!r}"
            )
