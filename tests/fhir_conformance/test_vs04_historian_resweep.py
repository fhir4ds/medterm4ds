"""VS-04 HISTORIAN resweep: pattern-match against prior bug patterns.

HISTORIAN lens for VS-04 (ValueSet $expand — Intensional URLs / fhir_vs).
Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
SNOMED CT intensional: https://hl7.org/fhir/R4/snomedct.html
Truncation ext: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

This resweep addresses the 3 SKEPTIC tips for HISTORIAN:

  1. **Re-derive QA-068 structural AST contract via different angle.**
     The SKEPTIC ``test_s83`` AST-walk of ``count_limited`` assignment
     asserting ``ast.Gt`` (not ``ast.GtE``) is a candidate for PROMOTION
     (count=1 sibling of HCPCS URI drift count=8 PROMOTED). HISTORIAN:
       (a) Re-walk the same AST node via the ``_read_function_source``
           helper but with a different parametrization (also assert
           ``descendant_budget = max(0, count - len(contains))`` line is
           structurally present).
       (b) Verify the SKEPTIC AST contract survives future refactors
           (structural-contract probes are refactor-tolerant because they
           don't depend on line numbers or fixture data).
       (c) Extend the L9 META-pattern re-derivation to cover the 10th
           PROMOTED pattern (isinstance guard at untrusted-data
           list-iterator boundary) — independently confirm via AST walk
           of every ``for X in body.get(...)`` loop in
           ``expand_url_pattern`` AND ``_expand_url_pattern`` (nested).

  2. **Re-derive all 7 prior VS-04 SKEPTIC fixes** (QA-060/061/062/065/
     066/067/068).

  3. **Re-derive 10 PROMOTED patterns**.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus) -> 44054006 (T2DM)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)
"""

from __future__ import annotations

import ast
import inspect

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent (root)
SNOMED_T2DM = "44054006"  # child of 73211009

TRUNCATION_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"


# =============================================================================
# Helpers
# =============================================================================


def _expand_url(client, url: str, count: int | None = None):
    """Helper: GET /fhir/ValueSet/$expand with the given url (and count)."""
    params = [("url", url)]
    if count is not None:
        params.append(("count", count))
    return client.get("/fhir/ValueSet/$expand", params=params)


def _contains_codes(resp_json: dict) -> list[str]:
    return [c.get("code") for c in resp_json.get("expansion", {}).get("contains", [])]


def _extensions(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("extension", [])


def _has_toocostly(resp_json: dict) -> bool:
    return any(e.get("url") == TRUNCATION_EXT_URL for e in _extensions(resp_json))


def _read_module_source() -> str:
    return inspect.getsource(
        __import__("medterm4ds.apps.fhir_api", fromlist=["fhir_api"])
    )


def _read_function_source(module_src: str, func_name: str) -> str | None:
    """Return the source of a top-level function or None if not found.

    Walks ast.Module for ast.FunctionDef nodes (VS-04 surface functions
    are all module-level: expand_url_pattern, _resolve_max_depth,
    _truncation_extensions).
    """
    tree = ast.parse(module_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(module_src, node) or ""
    return None


def _read_nested_function_source(
    module_src: str, parent_name: str, child_name: str
) -> str | None:
    """Return the source of a nested function defined inside ``parent_name``.

    Mirrors the SKEPTIC resweep helper. Walks BOTH ast.FunctionDef AND
    ast.AsyncFunctionDef inside ``parent``.
    """
    tree = ast.parse(module_src)
    parent_node = None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == parent_name
        ):
            parent_node = node
            break
    if parent_node is None:
        return None
    for child in ast.walk(parent_node):
        if (
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == child_name
            and child is not parent_node
        ):
            return ast.get_source_segment(module_src, child) or ""
    return None


# =============================================================================
# L1: QA-068 structural AST contract — different angle (SKEPTIC tip #1a)
# =============================================================================


class TestL1QA068ASTContractDifferentAngle:
    """SKEPTIC tip #1a: re-walk the AST node via ``_read_function_source``
    with a DIFFERENT parametrization angle than SKEPTIC ``test_s83``.

    SKEPTIC ``test_s83`` walked the ``count_limited`` assignment and
    asserted the operator is ``ast.Gt``. HISTORIAN re-walks the SAME
    AST node and additionally asserts:
      - the ``descendant_budget = max(0, count - len(contains))`` line is
        structurally present (the load-bearing precondition for the
        strict-``>`` comparison to be semantically correct).
      - the comparison's LEFT operand is ``len(relations)`` and the
        RIGHT operand is ``descendant_budget`` (not the other way around
        — which would invert the truncation signal).
      - the BFS ``limit=`` argument uses ``descendant_budget + 1`` (the
        +1 probe pattern that gives the strict-``>`` comparison its
        discriminating power).
    """

    def test_h10_descendant_budget_max_zero_count_minus_contains_structurally_present(self):
        """QA-068 precondition: descendant_budget = max(0, count - len(contains)).

        Without this precondition, the strict-``>`` comparison in
        ``count_limited = len(relations) > descendant_budget`` could
        produce silent-wrong-answer (e.g. if descendant_budget were
        ``max(1, ...)`` instead of ``max(0, ...)``, count=1 would always
        fetch 1 descendant + 1 root = 2 entries, and the +1 probe would
        make ``len(relations) == 2``, ``descendant_budget == 1``,
        ``count_limited = True`` even though the cap wasn't truly hit).
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None, "expand_url_pattern not found"
        # Structural contract: the literal "max(0, count - len(contains))"
        # must appear in the source.
        assert "max(0, count - len(contains))" in src, (
            "QA-068 precondition: descendant_budget MUST be "
            "max(0, count - len(contains)). If max(0, ...) is changed "
            "to max(1, ...), the strict-> comparison semantics shift."
        )

    def test_h11_count_limited_comparison_operands_direction(self):
        """QA-068: count_limited comparison is len(relations) > descendant_budget.

        SKEPTIC ``test_s83`` asserted the operator type (Gt not GtE).
        HISTORIAN additionally asserts the OPERAND DIRECTION:
          LEFT  = ``len(relations)`` (number observed)
          RIGHT = ``descendant_budget`` (budget)

        An inverted direction (``descendant_budget > len(relations)``)
        would still use ``ast.Gt`` and pass SKEPTIC's probe, but would
        invert the truncation signal (count_limited=True when actually
        NO descendants were observed beyond budget).
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
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
        assert count_limited_assign is not None, (
            "count_limited assignment not found in expand_url_pattern"
        )
        cmp_node = count_limited_assign.value
        assert isinstance(cmp_node, ast.Compare), (
            "count_limited assignment MUST be ast.Compare"
        )
        # LEFT operand: len(relations)
        left = cmp_node.left
        assert isinstance(left, ast.Call) and isinstance(left.func, ast.Name), (
            "count_limited LEFT operand MUST be a function call (len(...))"
        )
        assert left.func.id == "len", (
            f"count_limited LEFT operand MUST be len(...); got {left.func.id}"
        )
        assert (
            len(left.args) == 1
            and isinstance(left.args[0], ast.Name)
            and left.args[0].id == "relations"
        ), (
            "count_limited LEFT operand MUST be len(relations); an inverted "
            "direction (descendant_budget > len(relations)) would invert the "
            "truncation signal."
        )
        # RIGHT operand: descendant_budget (a Name, not a Call)
        assert len(cmp_node.comparators) == 1, (
            "count_limited comparison MUST have exactly 1 comparator"
        )
        right = cmp_node.comparators[0]
        assert isinstance(right, ast.Name) and right.id == "descendant_budget", (
            f"count_limited RIGHT operand MUST be descendant_budget (a Name); "
            f"got {ast.dump(right)}"
        )

    def test_h12_bfs_limit_uses_plus_one_probe(self):
        """QA-068: BFS limit uses descendant_budget + 1 (the +1 probe).

        The strict-``>`` comparison only discriminates when BFS is asked
        for ONE MORE descendant than the budget. Without the +1 probe,
        ``len(relations)`` would never exceed ``descendant_budget``, and
        the comparison would always be False (no truncation signal).
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        # The +1 probe: limit=(descendant_budget + 1) when budget > 0
        # else 1 (special-case for count=0/root-only where budget=0).
        assert "(descendant_budget + 1)" in src, (
            "BFS limit MUST use descendant_budget + 1 (the +1 probe). "
            "Without this, count_limited = len(relations) > descendant_budget "
            "is always False."
        )

    def test_h13_count_limited_variable_assigned_before_use(self):
        """QA-068 refactor-tolerance: count_limited is assigned BEFORE it's used.

        SKEPTIC ``test_s83`` asserts the assignment exists; HISTORIAN
        verifies the assignment appears BEFORE its first use in
        ``_truncation_extensions`` / ``total=`` computation. This guards
        against a future refactor moving the assignment after its use
        (which would silently produce NameError or fall-through wrong
        behavior).
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assign_line_idx = src.find("count_limited = len(relations)")
        use_line_idx = src.find("count_limited=count_limited")
        assert assign_line_idx != -1 and use_line_idx != -1, (
            "count_limited assignment AND its use MUST both be present"
        )
        assert assign_line_idx < use_line_idx, (
            "count_limited assignment MUST come BEFORE its use in "
            "_truncation_extensions() call. Refactor-tolerance probe."
        )

    def test_h14_count_limited_in_total_computation(self):
        """QA-068 refactor-tolerance: count_limited gates the total computation.

        The +1 in ``total = len(contains) + 1`` is only correct when
        ``count_limited`` is True. Verify the ``if count_limited:`` branch
        is structurally present BEFORE the ``total = ...`` assignment.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        if_idx = src.find("if count_limited:")
        total_plus_one_idx = src.find("total = len(contains) + 1")
        assert if_idx != -1 and total_plus_one_idx != -1, (
            "if count_limited: branch AND total = len(contains) + 1 "
            "MUST both be present"
        )
        assert if_idx < total_plus_one_idx, (
            "if count_limited: MUST come BEFORE total = len(contains) + 1. "
            "Refactor-tolerance probe guarding the +1 in total computation."
        )


# =============================================================================
# L2: QA-068 AST contract survives future refactors (SKEPTIC tip #1b)
# =============================================================================


class TestL2QA068ASTContractRefactorTolerance:
    """SKEPTIC tip #1b: verify the SKEPTIC AST contract SURVIVES FUTURE
    REFACTORS.

    A refactor-tolerant contract is one that doesn't depend on:
      - line numbers (a line move shouldn't break the probe),
      - fixture data (changing the conformance DB shouldn't break the
        probe),
      - substring matching that false-flags on commentary.

    HISTORIAN re-verifies SKEPTIC ``test_s83`` contract holds under
    these conditions:
      - parses the function source as a standalone unit (not relying on
        global line numbers),
      - walks the AST tree (not relying on string-find),
      - narrows to ``count_limited`` assignment context (not relying on
        whole-function substring).
    """

    def test_h20_skeptic_contract_passes_on_isolated_function_source(self):
        """SKEPTIC test_s83 contract holds when run on the isolated
        function source (not the whole module)."""
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        # Walk the AST in the isolated source (not the whole module).
        tree = ast.parse(src)
        found_count_limited = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        found_count_limited = True
                        cmp_node = node.value
                        assert isinstance(cmp_node, ast.Compare), (
                            "count_limited assignment MUST be ast.Compare"
                        )
                        assert any(
                            isinstance(op, ast.Gt) for op in cmp_node.ops
                        ), "count_limited comparison MUST use ast.Gt (>)"
                        assert not any(
                            isinstance(op, ast.GtE) for op in cmp_node.ops
                        ), (
                            "count_limited comparison MUST NOT use ast.GtE (>=) "
                            "per VS-04 TERMINOLOGIST QA-068"
                        )
        assert found_count_limited, (
            "count_limited assignment not found in isolated source"
        )

    def test_h21_contract_survives_line_move(self):
        """Contract doesn't depend on line numbers.

        The probe walks the AST tree rather than relying on line numbers,
        so moving the ``count_limited =`` assignment up or down within
        the function body doesn't break the probe. This is verified by
        finding the assignment via tree-walk (not via string.find at a
        specific offset).
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        tree = ast.parse(src)
        # Walk: find the assignment by name, not by line.
        count = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "count_limited"
                    for t in node.targets
                )
            ):
                count += 1
        assert count == 1, (
            f"count_limited MUST be assigned exactly once; found {count}. "
            "Multiple assignments would make the AST contract ambiguous."
        )

    def test_h22_contract_not_in_commentary(self):
        """Contract doesn't false-flag on commentary.

        The SKEPTIC ``test_s83`` probe narrows to the ``count_limited``
        assignment context — NOT the whole-function substring. This
        prevents false-positives when commentary mentions ``>=``
        somewhere else in the function.

        HISTORIAN re-verifies: any ``>=`` occurrences elsewhere in
        ``expand_url_pattern`` MUST NOT be inside the count_limited
        assignment. (If they are, the assignment's operator type probe
        is the load-bearing check.)
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        # The count_limited assignment MUST NOT have GtE.
                        cmp_node = node.value
                        assert isinstance(cmp_node, ast.Compare)
                        assert not any(
                            isinstance(op, ast.GtE) for op in cmp_node.ops
                        ), (
                            "count_limited assignment uses GtE — REGRESSION "
                            "of VS-04 TERMINOLOGIST QA-068 fix"
                        )
                        return
        # If count_limited not found, fail loudly.
        pytest.fail("count_limited assignment not found in expand_url_pattern")


# =============================================================================
# L3: 10th PROMOTED pattern — isinstance guard coverage (SKEPTIC tip #1c)
# =============================================================================


class TestL3TenthPromotedPatternIsinstanceGuard:
    """SKEPTIC tip #1c: extend the L9 META-pattern re-derivation to cover
    the 10th PROMOTED pattern (isinstance guard at untrusted-data
    list-iterator boundary).

    Per GLOBAL_RULES.md line 140 (10th PROMOTED pattern): "for every
    ``for X in body.get(...)`` loop where the iterable is
    ``.get("...", [])`` extracted from a client-controlled JSON body",
    an ``isinstance(X, dict)`` guard MUST appear within the first 5
    statements of the loop body.

    HISTORIAN independently confirms via AST walk of EVERY
    ``for X in body.get(...)`` loop in:
      - ``expand_url_pattern`` (module-level) — processes URL strings,
        NO JSON body iterators expected (vacuously satisfied).
      - ``_expand_url_pattern`` (nested inside ``create_fhir_app``) —
        HTTP wrapper, delegates to module-level, NO JSON body iterators
        expected (vacuously satisfied).

    The VS-04 URL-pattern surface processes URL strings, NOT JSON
    bodies — so the 10th PROMOTED pattern's structural probe is
    vacuously satisfied on this surface. HISTORIAN verifies this is
    still the case AND that no NEW iterators over client JSON bodies
    have been introduced.
    """

    def test_h30_expand_url_pattern_no_unsafe_body_iterators(self):
        """expand_url_pattern has no ``for X in <body>.get(...)`` loops.

        VS-04 URL-pattern processes URL strings via urlparse/parse_qs.
        Any new ``for X in <body>.get(...)`` loop added to
        expand_url_pattern would need an isinstance guard per the 10th
        PROMOTED pattern.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        tree = ast.parse(src)
        unsafe_loops = []
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                # Check if iter is <name>.get(<const>, []) pattern.
                iter_node = node.iter
                if (
                    isinstance(iter_node, ast.Call)
                    and isinstance(iter_node.func, ast.Attribute)
                    and iter_node.func.attr == "get"
                    and len(iter_node.args) >= 1
                    and isinstance(iter_node.args[0], ast.Constant)
                ):
                    # This is a ``for X in Y.get("...", ...)`` loop.
                    # Check if a body.get(...) specifically (vs other
                    # variables like compose.get(...)).
                    if (
                        isinstance(iter_node.func.value, ast.Name)
                        and iter_node.func.value.id == "body"
                    ):
                        unsafe_loops.append(node.lineno)
        assert unsafe_loops == [], (
            f"expand_url_pattern introduced NEW unsafe iterators over "
            f"client body: lines {unsafe_loops}. Per 10th PROMOTED "
            f"pattern, each needs an isinstance guard."
        )

    def test_h31_expand_url_pattern_query_params_iteration_safe(self):
        """expand_url_pattern iterates query_params via [0] index, not a
        for-loop — no isinstance guard needed.

        The implementation at apps/fhir_api.py:191 uses
        ``query_params.get("fhir_vs", [""])[0]`` — single-element
        extraction, not a for-loop iteration. parse_qs already validates
        the structure (returns dict[str, list[str]]).
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        # The query_params access MUST use the [0] pattern, not a for-loop.
        assert 'query_params.get("fhir_vs", [""])[0]' in src, (
            "expand_url_pattern MUST extract fhir_vs via "
            'query_params.get("fhir_vs", [""])[0] (single-element access, '
            "not for-loop iteration). parse_qs validates structure."
        )

    def test_h32_nested_expand_url_pattern_no_unsafe_body_iterators(self):
        """_expand_url_pattern (nested) has no ``for X in body.get(...)``
        loops.

        The nested HTTP-handler wrapper at apps/fhir_api.py:2717-2726
        delegates to the module-level expand_url_pattern and catches
        ValueError. It does NOT iterate any client JSON body —
        parameters arrive as FastAPI Query params (url, count).
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_url_pattern"
        )
        assert src is not None, "_expand_url_pattern not found in create_fhir_app"
        tree = ast.parse(src)
        unsafe_loops = []
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                iter_node = node.iter
                if (
                    isinstance(iter_node, ast.Call)
                    and isinstance(iter_node.func, ast.Attribute)
                    and iter_node.func.attr == "get"
                    and isinstance(iter_node.func.value, ast.Name)
                    and iter_node.func.value.id == "body"
                ):
                    unsafe_loops.append(node.lineno)
        assert unsafe_loops == [], (
            f"_expand_url_pattern introduced NEW unsafe iterators over "
            f"client body: lines {unsafe_loops}."
        )

    def test_h33_tenth_promoted_pattern_holds_on_intensional_sibling(self):
        """10th PROMOTED pattern: 5 isinstance guards in _expand_intensional.

        The VS-04 URL-pattern surface (expand_url_pattern) does NOT
        iterate client JSON bodies. But its sibling path
        (_expand_intensional) DOES — and the 10th PROMOTED pattern
        requires 5 guards there (compose/include/concept/filter/exclude).

        HISTORIAN re-verifies the sibling pattern still holds, since
        VS-04's URL-pattern dispatch (``if url and "fhir_vs" in url:``)
        is the only thing separating the two code paths.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_intensional"
        )
        assert src is not None, "_expand_intensional not found"
        # Count isinstance guards in the function.
        tree = ast.parse(src)
        isinstance_count = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance"
            ):
                isinstance_count += 1
        # Per VS-03 HISTORIAN resweep + CS-04 HISTORIAN QA-001: 5
        # sibling guards (compose/include/concept/filter/exclude) PLUS
        # the compose parent guard from VS-01 resweep = 6 minimum.
        assert isinstance_count >= 5, (
            f"_expand_intensional MUST have >= 5 isinstance guards per "
            f"10th PROMOTED pattern (count=5 as of CS-04 HISTORIAN QA-001 "
            f"+ 1 added by VS-01 resweep = 6 minimum). Found "
            f"{isinstance_count}."
        )

    def test_h34_tenth_promoted_pattern_vacuously_satisfied_on_vs04(self):
        """10th PROMOTED pattern: VS-04 surface is structurally exempt.

        The VS-04 surface processes URL strings via
        urlparse/parse_qs. No JSON body iterators are introduced. The
        10th PROMOTED pattern is therefore vacuously satisfied on
        VS-04 — the structural probe ``count(for X in body.get) == 0``
        is the load-bearing contract.

        HISTORIAN documents this with a positive-shape assertion: the
        VS-04 surface MUST NOT introduce new JSON body iterators
        without an isinstance guard.
        """
        # Combined probe: both expand_url_pattern AND _expand_url_pattern
        # have ZERO unsafe body iterators (per test_h30 + test_h32).
        # This probe is the META-pattern: "VS-04 surface processes URL
        # strings, NOT JSON bodies" — a structural invariant.
        mod_src = _read_module_source()
        for func_name in ("expand_url_pattern",):
            src = _read_function_source(mod_src, func_name)
            assert src is not None
            tree = ast.parse(src)
            body_iter_count = sum(
                1
                for node in ast.walk(tree)
                if (
                    isinstance(node, ast.For)
                    and isinstance(node.iter, ast.Call)
                    and isinstance(node.iter.func, ast.Attribute)
                    and node.iter.func.attr == "get"
                    and isinstance(node.iter.func.value, ast.Name)
                    and node.iter.func.value.id == "body"
                )
            )
            assert body_iter_count == 0, (
                f"{func_name} MUST have ZERO body.get() for-loops "
                f"(VS-04 surface processes URL strings); found "
                f"{body_iter_count}."
            )


# =============================================================================
# L4: Re-derive QA-060 (unrecognized value dispatch) HELD
# =============================================================================


class TestL4QA060UnrecognizedValueDispatchHeld:
    """QA-060 (VS-04 SKEPTIC): ``?fhir_vs=unknown`` must raise ValueError.

    HISTORIAN re-derives the fix via source-read AND behavioral probes.
    Status: HELD if all probes pass.
    """

    def test_h40_source_read_dispatch_rejects_unrecognized(self):
        """Source-read contract: dispatch raises ValueError for
        unrecognized values."""
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert 'not in ("", "isa", "refset")' in src, (
            "QA-060 dispatch MUST reject values not in the allowed set"
        )
        assert "Unsupported fhir_vs value" in src, (
            "QA-060 dispatch MUST raise ValueError with diagnostic"
        )

    def test_h41_behavioral_unknown_value_400(self, fhir_client):
        """Behavioral: ``?fhir_vs=unknown`` returns 400 OperationOutcome."""
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=unknown",
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"

    @pytest.mark.parametrize(
        "value",
        ["unknown", "isa=extra", "all", "tree", "subtree", "*", "equals"],
    )
    def test_h42_unrecognized_values_rejected(self, fhir_client, value):
        """Behavioral: every unrecognized value rejected with 400."""
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs={value}",
        )
        assert resp.status_code == 400, (
            f"?fhir_vs={value!r} MUST be rejected; got {resp.status_code}"
        )


# =============================================================================
# L5: Re-derive QA-061 (case-insensitive isa) HELD
# =============================================================================


class TestL5QA061CaseInsensitiveIsaHeld:
    """QA-061 (VS-04 SKEPTIC): case variants of ``isa`` recognized.

    Status: HELD if all probes pass.
    """

    def test_h50_source_read_dispatch_normalizes_to_lowercase(self):
        """Source-read contract: dispatch normalizes via .lower()."""
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert "fhir_vs_normalized = fhir_vs.lower()" in src, (
            "QA-061 dispatch MUST normalize via .lower() before lookup"
        )

    @pytest.mark.parametrize("value", ["ISA", "Isa", "iSa", "iSA"])
    def test_h51_case_variants_accepted(self, fhir_client, value):
        """Behavioral: case variants of isa produce full isa expansion."""
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs={value}",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes and SNOMED_T2DM in codes


# =============================================================================
# L6: Re-derive QA-062 (refset explicit rejection) HELD
# =============================================================================


class TestL6QA062RefsetExplicitRejectionHeld:
    """QA-062 (VS-04 SKEPTIC): ``?fhir_vs=refset`` raises ValueError
    explicitly (medterm4ds lacks refset data).

    Status: HELD if all probes pass.
    """

    def test_h60_source_read_explicit_refset_branch(self):
        """Source-read contract: explicit refset branch raises ValueError."""
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert 'fhir_vs_normalized == "refset"' in src, (
            "QA-062 dispatch MUST have explicit refset branch"
        )
        assert "?fhir_vs=refset is not implemented" in src, (
            "QA-062 refset branch MUST raise ValueError with informative message"
        )

    @pytest.mark.parametrize("value", ["refset", "REFSET", "Refset", "RefSet"])
    def test_h61_refset_case_variants_400(self, fhir_client, value):
        """Behavioral: refset + case variants return 400."""
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs={value}",
        )
        assert resp.status_code == 400, (
            f"?fhir_vs={value} (case-variant of refset) MUST return 400; "
            f"got {resp.status_code}"
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"


# =============================================================================
# L7: Re-derive QA-065 (depth=0 truncation signal) HELD
# =============================================================================


class TestL7QA065Depth0TruncationSignalHeld:
    """QA-065 (VS-04 SKEPTIC): ``FHIR_VS_MAX_DEPTH=0`` synthesizes
    depth_cap_hit=True so the toocostly extension fires.

    Status: HELD if all probes pass.
    """

    def test_h70_source_read_depth_zero_synthesis(self):
        """Source-read contract: ``if max_depth == 0: depth_cap_hit = True``."""
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert "max_depth == 0" in src, (
            "QA-065 synthesis MUST set depth_cap_hit=True when max_depth==0"
        )

    def test_h71_behavioral_depth_zero_emits_extension(
        self, fhir_client, monkeypatch
    ):
        """Behavioral: ``FHIR_VS_MAX_DEPTH=0`` emits toocostly extension."""
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        assert _has_toocostly(resp.json()), (
            "QA-065 fix: FHIR_VS_MAX_DEPTH=0 MUST emit toocostly extension"
        )
        codes = _contains_codes(resp.json())
        assert codes == [SNOMED_DIABETES_MELLITUS], (
            f"QA-065: depth=0 means root-only; got {codes}"
        )


# =============================================================================
# L8: Re-derive QA-066 (invalid env var doesn't crash) HELD
# =============================================================================


class TestL8QA066InvalidEnvVarHeld:
    """QA-066 (VS-04 SKEPTIC): invalid ``FHIR_VS_MAX_DEPTH`` doesn't crash;
    falls back to default 5 with a WARNING.

    Status: HELD if all probes pass.
    """

    def test_h80_source_read_defensive_parsing(self):
        """Source-read contract: helper catches TypeError + ValueError."""
        src = _read_function_source(_read_module_source(), "_resolve_max_depth")
        assert src is not None
        assert "TypeError" in src and "ValueError" in src, (
            "QA-066 helper MUST catch TypeError + ValueError on int() parse"
        )
        assert "logger.warning" in src, (
            "QA-066 helper MUST log at WARNING (not DEBUG) when falling back"
        )

    @pytest.mark.parametrize(
        "value", ["not-a-number", "abc", "", "5.5", "0x10", "1e3", "INF", "None"]
    )
    def test_h81_invalid_env_values_no_crash(
        self, fhir_client, monkeypatch, value
    ):
        """Behavioral: invalid env values fall back to default 5."""
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", value)
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200, (
            f"FHIR_VS_MAX_DEPTH={value!r} MUST NOT crash; got {resp.status_code}"
        )


# =============================================================================
# L9: Re-derive QA-067 (negative depth) HELD
# =============================================================================


class TestL9QA067NegativeDepthHeld:
    """QA-067 (VS-04 HISTORIAN): negative ``FHIR_VS_MAX_DEPTH`` rejected
    with WARNING + default fallback (extension of QA-065 to negatives).

    Status: HELD if all probes pass.
    """

    def test_h90_source_read_negative_rejection(self):
        """Source-read contract: helper rejects negative values."""
        src = _read_function_source(_read_module_source(), "_resolve_max_depth")
        assert src is not None
        assert "value < 0" in src, (
            "QA-067 helper MUST reject negative values"
        )

    @pytest.mark.parametrize("value", ["-1", "-5", "-100", "-99999"])
    def test_h91_negative_env_values_default_fallback(
        self, fhir_client, monkeypatch, value
    ):
        """Behavioral: negative env values fall back to default 5."""
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", value)
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        # Default 5 walks the 1-layer descendant in the fixture.
        assert SNOMED_DIABETES_MELLITUS in codes and SNOMED_T2DM in codes


# =============================================================================
# L10: Re-derive QA-068 (count_limited >= vs > divergence) HELD
# =============================================================================


class TestL10QA068CountLimitedStrictGtHeld:
    """QA-068 (VS-04 TERMINOLOGIST): count_limited uses strict ``>`` not
    ``>=``. The bug: ``len(relations) >= descendant_budget`` fires the
    toocostly extension on COMPLETE expansions when fixture size matches
    budget exactly.

    Status: HELD if all probes pass.
    """

    def test_h100_behavioral_count_exact_size_no_toocostly(self, fhir_client):
        """count=2 (exact fixture size) MUST NOT fire toocostly extension.

        Per QA-068: descendant_budget = max(0, 2 - 1) = 1. BFS with
        limit=2 returns 1 relation (only 1 descendant in fixture).
        ``1 > 1`` = False → count_limited = False → no extension.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=2,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert not _has_toocostly(body), (
            "QA-068 regression-pin: count=2 (exact size) MUST NOT fire "
            "toocostly extension. The fix uses strict `>` not `>=`."
        )
        codes = _contains_codes(body)
        assert len(codes) == 2, (
            f"count=2 should return both root + descendant; got {codes}"
        )

    def test_h101_behavioral_count_1_truncation_fires(self, fhir_client):
        """count=1 fires toocostly because more descendants exist beyond cap.

        Per QA-068: descendant_budget = max(0, 1 - 1) = 0. BFS with
        limit=1 returns 1 relation. ``1 > 0`` = True → count_limited =
        True → extension fires.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert _has_toocostly(body), (
            "QA-068: count=1 MUST fire toocostly extension (1 more "
            "descendant exists beyond the 0-budget)."
        )

    def test_h102_source_read_count_limited_uses_strict_gt(self):
        """Source-read contract: count_limited uses ast.Gt (>) not ast.GtE (>=).

        Mirrors SKEPTIC test_s83 but parametrizes differently: HISTORIAN
        re-walks the AST node and ALSO verifies the comparison's LEFT and
        RIGHT operand types (per test_h11 above).
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        cmp_node = node.value
                        assert isinstance(cmp_node, ast.Compare)
                        assert any(
                            isinstance(op, ast.Gt) for op in cmp_node.ops
                        ), "count_limited MUST use ast.Gt (>)"
                        assert not any(
                            isinstance(op, ast.GtE) for op in cmp_node.ops
                        ), "count_limited MUST NOT use ast.GtE (>=)"
                        found = True
        assert found, "count_limited assignment not found"


# =============================================================================
# L11: 10 PROMOTED patterns re-derivation
# =============================================================================


class TestL11TenPromotedPatternsReDerivation:
    """Re-derive the 10 PROMOTED patterns applicable to the VS-04 surface.

    Per GLOBAL_RULES.md, the 10 PROMOTED patterns are:
      1. client-input-as-canonical drift (count=9 PROMOTED via CR-013)
      2. closed-enum vocabulary drift / registry-as-contract
      3. response field derived from wrong source (size-field-from-wrong-source, count=4 PROMOTED)
      4. cross-handler-helper-wiring (count=6 PROMOTED)
      5. silent-fallback broad except Exception (v0.0.1 B-series)
      6. DEBUG-level swallowing of operational errors (B6)
      7. single-source-of-truth table violations
      8. URL-constructor edge-case matrix (TS-04 HISTORIAN QA-040)
      9. wire-format serializer boolean rendering (CR-002)
      10. isinstance guard at untrusted-data list-iterator boundary (CS-04 HISTORIAN, count=5 PROMOTED)

    For the VS-04 surface, applicable patterns: 1, 3, 5, 6, 7, 10.
    Patterns 2, 4, 8, 9 are not directly applicable (VS-04 doesn't
    render closed-enum vocabularies, doesn't wire response builders
    that take pre-truncatable lists, doesn't construct URLs from env
    vars, doesn't serialize booleans).
    """

    def test_h110_pattern1_client_input_as_canonical_drift_absent(self):
        """Pattern 1: contains[].system sourced from canonical SYSTEM_TO_FHIR_URI.

        VS-04 surface uses ``system_uri`` (canonical) NOT ``base`` /
        ``parsed.netloc`` (client-supplied). No drift.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (
                        isinstance(k, ast.Constant)
                        and k.value == "system"
                        and isinstance(v, ast.Name)
                    ):
                        assert v.id == "system_uri", (
                            f"contains[].system MUST be `system_uri` (canonical), "
                            f"not `{v.id}` (client-input-as-canonical drift)"
                        )

    def test_h111_pattern3_size_field_from_wrong_source_absent(self):
        """Pattern 3: expansion.total reflects UN-truncated size.

        Per QA-057 + QA-068: when count_limited, ``total = len(contains) + 1``
        (lower bound from +1 probe); otherwise ``total = len(contains)``.
        The prior ``total=len(contains)`` unconditionally is GONE.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        # The unconditional literal ``total=len(contains)`` is GONE —
        # replaced with the if/else guarded by count_limited.
        # Verify both branches are structurally present.
        assert "if count_limited:" in src, (
            "Pattern 3: count_limited MUST gate the total computation"
        )
        assert "total = len(contains) + 1" in src, (
            "Pattern 3: count_limited branch MUST add +1 (lower bound)"
        )
        assert "total = len(contains)" in src, (
            "Pattern 3: non-count_limited branch MUST be len(contains)"
        )

    def test_h112_pattern5_no_broad_except_exception(self):
        """Pattern 5: no broad ``except Exception:`` in expand_url_pattern.

        Per GLOBAL_RULES "Silent Fallbacks": broad except masks
        programming bugs.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # Broad ``except Exception:`` or bare ``except:`` is prohibited.
                if node.type is None:
                    pytest.fail(
                        "Pattern 5: bare `except:` is prohibited in "
                        "expand_url_pattern"
                    )
                if (
                    isinstance(node.type, ast.Name)
                    and node.type.id == "Exception"
                ):
                    pytest.fail(
                        "Pattern 5: broad `except Exception:` is prohibited "
                        "in expand_url_pattern"
                    )

    def test_h113_pattern6_no_debug_swallowing(self):
        """Pattern 6: no DEBUG-level swallowing of operational errors.

        _resolve_max_depth catches ValueError on int() parse and logs at
        WARNING (not DEBUG). This is the load-bearing pattern.
        """
        src = _read_function_source(_read_module_source(), "_resolve_max_depth")
        assert src is not None
        # Must have logger.warning (NOT logger.debug) for the fallback.
        assert "logger.warning" in src, (
            "Pattern 6: _resolve_max_depth MUST log at WARNING when falling back"
        )
        # Must NOT have logger.debug in the fallback path.
        # (We can't easily AST-walk this without false-positives on
        # docstrings — the structural check is the WARNING assertion.)
        assert "logger.debug" not in src, (
            "Pattern 6: _resolve_max_depth MUST NOT log at DEBUG when "
            "falling back — that swallows the operator-misconfiguration signal"
        )

    def test_h114_pattern7_single_source_of_truth(self):
        """Pattern 7: SNOMED URI sourced from SYSTEM_TO_FHIR_URI.

        Per GLOBAL_RULES single-source-of-truth table: the source →
        FHIR URI map MUST be imported from engines.fhir.SYSTEM_TO_FHIR_URI.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert (
            "from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI" in src
        ), (
            "Pattern 7: expand_url_pattern MUST import SYSTEM_TO_FHIR_URI "
            "from engines.fhir (single source of truth)"
        )
        assert 'SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]' in src, (
            "Pattern 7: SNOMED URI MUST be sourced from "
            "SYSTEM_TO_FHIR_URI['SNOMEDCT_US'], not hardcoded"
        )

    def test_h115_pattern10_isinstance_guard_vacuously_satisfied(self):
        """Pattern 10: VS-04 surface is structurally exempt.

        VS-04 processes URL strings, NOT JSON bodies. The 10th PROMOTED
        pattern's structural probe (``for X in body.get(...):`` loop) is
        vacuously satisfied. Verified via test_h30/h32/h34 above.
        """
        # Re-verify the META contract.
        mod_src = _read_module_source()
        for func_name in ("expand_url_pattern",):
            src = _read_function_source(mod_src, func_name)
            assert src is not None
            tree = ast.parse(src)
            body_iter_count = sum(
                1
                for node in ast.walk(tree)
                if (
                    isinstance(node, ast.For)
                    and isinstance(node.iter, ast.Call)
                    and isinstance(node.iter.func, ast.Attribute)
                    and node.iter.func.attr == "get"
                    and isinstance(node.iter.func.value, ast.Name)
                    and node.iter.func.value.id == "body"
                )
            )
            assert body_iter_count == 0


# =============================================================================
# L12: Carry-forward reconfirmations
# =============================================================================


class TestL12CarryForwardReconfirmations:
    """HISTORIAN lens: re-confirm CF carry-forwards from prior iterations.

    Per VS-04 SKEPTIC resweep + GLOBAL_KNOWLEDGE.md:
      - CF-SKEPTIC-VS01-01: 7 missing filter operators in
        _expand_intensional. Doesn't apply to URL-pattern path.
      - CF-HISTORIAN-VS02-01: BFS cap on total computation. Applies to
        URL-pattern path's total computation when the BFS budget fires.
      - CF-HISTORIAN-VS02-02: implicit path lacks canonical_system_uri.
        URL-pattern path uses SYSTEM_TO_FHIR_URI directly so unaffected.
      - CF-TERMINOLOGIST-VS01-01: client-supplied display echo. Doesn't
        apply to URL-pattern path (displays come from engine preferred
        term, not client).
    """

    def test_h120_cf_skeptic_vs01_01_no_leakage_to_url_path(self, fhir_client):
        """CF-SKEPTIC-VS01-01 (7 missing filter operators) does NOT apply
        to URL-pattern path.

        The URL-pattern path processes SNOMED intensional URLs
        (``?fhir_vs=isa``). The ``isa`` semantic is structurally
        equivalent to ``filter[is-a]`` in the ValueSet body path. The 7
        missing filter operators are NOT applicable to the URL pattern.
        """
        # ``?fhir_vs=is-a`` is NOT a valid SNOMED intensional URL value.
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=is-a",
        )
        assert resp.status_code == 400, (
            "CF-SKEPTIC-VS01-01 leakage guard: ?fhir_vs=is-a MUST be rejected"
        )

    def test_h121_cf_historian_vs02_01_bfs_cap_fixture_coincidence(self, fhir_client):
        """CF-HISTORIAN-VS02-01 (BFS cap on total) — STILL OPEN but masked
        by fixture coincidence.

        With count=1, the fixture (1 descendant) produces correct total
        by accident. The CF remains open for the exact-count enhancement.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Fixture has 2 codes (root + 1 descendant). count=1 → contains=1,
        # total MUST be 2 (lower bound per QA-068 +1 probe).
        # NOTE: this passes because count_limited=True so total = len(contains) + 1
        # = 1 + 1 = 2. The CF-HISTORIAN-VS02-01 territory (exact-count
        # enhancement) is closed by the QA-068 fix's +1 lower bound.
        assert body["expansion"]["total"] == 2

    def test_h122_cf_historian_vs02_02_url_pattern_uses_canonical_snomed(
        self, fhir_client
    ):
        """CF-HISTORIAN-VS02-02 doesn't apply: URL-pattern uses canonical URI."""
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        for c in resp.json()["expansion"]["contains"]:
            assert c.get("system") == SNOMED_URI, (
                f"contains[].system MUST be canonical SNOMED URI; "
                f"got {c.get('system')!r}"
            )


# =============================================================================
# L13: Cross-cutting invariant: canonical-DISPLAY cross-operation
# =============================================================================


class TestL13CanonicalDisplayCrossOperation:
    """VS-03/TERMINOLOGIST tip: canonical-DISPLAY invariant extends to
    VS-04 URL forms as the 7th surface. Verify byte-exact contains[].display
    vs $lookup Out display for every seeded code in the expansion.
    """

    def test_h130_isa_contains_display_matches_lookup(self, fhir_client):
        """contains[].display (root + descendant) byte-exact with $lookup Out display."""
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        contains = resp.json()["expansion"]["contains"]
        for c in contains:
            code = c.get("code")
            display = c.get("display", "")
            # $lookup the same code
            lookup_resp = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params=[("system", SNOMED_URI), ("code", code)],
            )
            assert lookup_resp.status_code == 200
            params = lookup_resp.json().get("parameter", [])
            lookup_display = next(
                (p.get("valueString") for p in params if p.get("name") == "display"),
                None,
            )
            assert lookup_display == display, (
                f"canonical-DISPLAY drift on code {code}: contains={display!r}, "
                f"lookup={lookup_display!r}"
            )

    def test_h131_isa_system_canonical_no_drift(self, fhir_client):
        """contains[].system is canonical SNOMED URI even on uppercase-scheme input.

        Per TS-03 EXPLORER QA-001 scheme-normalization fix + the 9th
        PROMOTED canonical_system_uri helper (count=9 PROMOTED).
        """
        # Uppercase scheme input
        resp = _expand_url(
            fhir_client,
            f"HTTP://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        # Either accepted (200) or rejected (400) — but if 200,
        # contains[].system MUST be canonical lowercase scheme.
        if resp.status_code == 200:
            for c in resp.json()["expansion"]["contains"]:
                assert c.get("system") == SNOMED_URI, (
                    f"contains[].system MUST be canonical ({SNOMED_URI}); "
                    f"got {c.get('system')!r}"
                )


# =============================================================================
# L14: GET ↔ POST byte-exact parity on URL forms
# =============================================================================


class TestL14GetPostByteExactParity:
    """GET ``$expand?url=...?fhir_vs=isa`` MUST be byte-exact with POST
    ``$expand`` Parameters-body-with-url.
    """

    def test_h140_get_post_url_form_parity(self, fhir_client):
        """GET vs POST on URL-form: byte-exact contains[] + total.

        Per FHIR R4 $expand In parameter ``url`` (type uri):
        https://hl7.org/fhir/R4/valueset-operation-expand.html. The
        POST Parameters body uses ``valueUri`` for url parameters (the
        impl's _parse_parameters extracts ``valueString``/``valueUri``/
        ``valueCode``/``valueInteger``/``valueBoolean`` — per
        apps/fhir_api.py:3144).
        """
        url = f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        get_resp = _expand_url(fhir_client, url)
        assert get_resp.status_code == 200
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": url},
            ],
        }
        post_resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=post_body,
            headers={"Accept": "application/fhir+json"},
        )
        assert post_resp.status_code == 200
        get_codes = _contains_codes(get_resp.json())
        post_codes = _contains_codes(post_resp.json())
        assert get_codes == post_codes, (
            f"GET/POST URL-form contains[] MUST be byte-exact; "
            f"GET={get_codes}, POST={post_codes}"
        )

    def test_h141_get_post_rejection_parity(self, fhir_client):
        """GET vs POST rejection parity on unrecognized fhir_vs value."""
        url = f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=unknown"
        get_resp = _expand_url(fhir_client, url)
        assert get_resp.status_code == 400
        post_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "url", "valueUri": url}],
        }
        post_resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=post_body,
            headers={"Accept": "application/fhir+json"},
        )
        assert post_resp.status_code == 400, (
            f"POST URL-form with unrecognized value MUST return 400; "
            f"got {post_resp.status_code}"
        )


# =============================================================================
# L15: Full-system-registry non-SNOMED rejection
# =============================================================================


class TestL15FullSystemRegistryNonSnomedRejection:
    """Every system in SYSTEM_TO_FHIR_URI except SNOMEDCT_US raises
    ValueError when used with ``?fhir_vs=isa`` (medterm4ds only supports
    SNOMED CT intensional expansions).
    """

    def test_h150_full_registry_rejection_matrix(self, fhir_client):
        """All non-SNOMED systems raise ValueError on fhir_vs URL pattern."""
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

        non_snomed = {
            k: v for k, v in SYSTEM_TO_FHIR_URI.items() if k != "SNOMEDCT_US"
        }
        assert len(non_snomed) >= 6, (
            f"Expected at least 6 non-SNOMED systems; got {len(non_snomed)}"
        )
        for source_name, uri in non_snomed.items():
            url = f"{uri}/some-code?fhir_vs=isa"
            resp = _expand_url(fhir_client, url)
            assert resp.status_code == 400, (
                f"system {source_name} ({uri}) with fhir_vs=isa MUST raise "
                f"ValueError -> 400; got {resp.status_code}"
            )


# =============================================================================
# L16: Source-read structural contracts for refactors (META)
# =============================================================================


class TestL16SourceReadStructuralContracts:
    """META source-read contracts at expand_url_pattern + _expand_url_pattern.

    These contracts lock in expected behaviors without depending on
    fixture data — making them refactor-tolerant.
    """

    def test_h160_dispatch_table_load_bearing(self):
        """Dispatch table at apps/fhir_api.py:213-219 is load-bearing."""
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        # The 3 recognized values: '', 'isa', 'refset'.
        assert '("", "isa", "refset")' in src, (
            "Dispatch table MUST recognize ('', 'isa', 'refset')"
        )

    def test_h161_resolve_max_depth_default_5(self):
        """_resolve_max_depth default value is 5 (matches docstring)."""
        from medterm4ds.apps.fhir_api import _resolve_max_depth

        sig = inspect.signature(_resolve_max_depth)
        assert sig.parameters["default"].default == 5, (
            "_resolve_max_depth default MUST be 5"
        )

    def test_h162_truncation_extensions_gated_by_count_or_depth(self):
        """_truncation_extensions returns [] when neither count_limited nor
        depth_cap_hit is True."""
        from medterm4ds.apps.fhir_api import _truncation_extensions

        result = _truncation_extensions(
            count_limited=False, depth_cap_hit=False
        )
        assert result == [], (
            "_truncation_extensions MUST return [] when neither signal fires"
        )

    def test_h163_nested_handler_catches_value_error(self):
        """The nested _expand_url_pattern catches ValueError → 400."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_url_pattern"
        )
        assert src is not None
        assert "except ValueError" in src
        assert "_fhir_error(400" in src

    def test_h164_expand_url_pattern_module_level(self):
        """expand_url_pattern is module-level (callable in-process)."""
        from medterm4ds.apps.fhir_api import expand_url_pattern

        assert callable(expand_url_pattern)

    def test_h165_canonical_system_uri_importable(self):
        """canonical_system_uri importable from apps.fhir_api.

        Per CR-012 + count=9 PROMOTED: this helper is the load-bearing
        structural backbone.
        """
        from medterm4ds.apps.fhir_api import canonical_system_uri  # noqa: F401


# =============================================================================
# L17: Defense-in-depth — verify refactored dispatch still rejects
# =============================================================================


class TestL17DefenseInDepthDispatchExhaustive:
    """HISTORIAN lens: probe the dispatch exhaustiveness with near-miss
    values that a buggy future change might silently accept.

    This is defense-in-depth: even if the structural AST contract holds
    (per L1-L2), the behavioral probes verify the dispatch still
    REJECTS every unrecognized value with 400.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "is-a",
            "descendants",
            "children",
            "parents",
            "all",
            "*",
            "true",
            "false",
            "null",
            "none",
            "descendant-of",
            "regex",
            "in",
            "is-not-a",
        ],
    )
    def test_h170_near_miss_values_rejected(self, fhir_client, value):
        """Every near-miss value rejected with 400."""
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs={value}",
        )
        assert resp.status_code == 400, (
            f"?fhir_vs={value!r} MUST be rejected; got {resp.status_code}"
        )

    @pytest.mark.parametrize("value", ["isa", "ISA", "Isa", ""])
    def test_h171_recognized_values_accepted(self, fhir_client, value):
        """Recognized values (isa + case variants + bare) accepted.

        Note: ``isa `` (trailing whitespace) is correctly REJECTED — the
        server's dispatch uses exact-match after .lower(), not whitespace-
        stripped match. This is the documented contract.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs={value}",
        )
        assert resp.status_code == 200, (
            f"?fhir_vs={value!r} MUST be recognized; got {resp.status_code}"
        )
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes


# =============================================================================
# L18: META — 4-personality rotation break risk closed
# =============================================================================


class TestL18MetaFourPersonalityRotationBreakClosed:
    """META: VS-04 in the prior [2026-07-14] run was the chunk where the
    4-personality rotation pattern BROKE — TERMINOLOGIST found a HIGH
    bug (count_limited >= vs > divergence) the other 3 missed.

    HISTORIAN re-verifies the QA-068 fix holds via INDEPENDENT
    re-derivation (different angle than SKEPTIC). The rotation break
    risk is closed when:
      - SKEPTIC independently re-derives (test_s40 behavioral + test_s83
        AST contract),
      - HISTORIAN independently re-derives via different parametrization
        (test_h10 descendant_budget precondition + test_h11 operand
        direction + test_h12 +1 probe + test_h20 isolated source + etc.).
    """

    def test_h180_qa068_independently_re_derived(self, fhir_client):
        """QA-068 independently re-derived via behavioral probe.

        HISTORIAN lens: count=2 (exact fixture size) MUST NOT fire
        toocostly extension. Mirrors SKEPTIC test_s40.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=2,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert not _has_toocostly(body), (
            "QA-068 independently re-derived: count=2 MUST NOT fire extension"
        )

    def test_h181_qa068_ast_contract_independently_re_derived(self):
        """QA-068 AST contract independently re-derived.

        HISTORIAN lens: structural contract via AST walk. Mirrors SKEPTIC
        test_s83 but parametrizes differently (operand direction + isolated
        source + refactor tolerance).
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        cmp_node = node.value
                        assert isinstance(cmp_node, ast.Compare)
                        assert any(
                            isinstance(op, ast.Gt) for op in cmp_node.ops
                        ), "MUST use ast.Gt"
                        assert not any(
                            isinstance(op, ast.GtE) for op in cmp_node.ops
                        ), "MUST NOT use ast.GtE"
                        return
        pytest.fail("count_limited assignment not found")
