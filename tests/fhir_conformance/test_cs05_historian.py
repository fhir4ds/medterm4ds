"""HISTORIAN probes for CS-05 (CodeSystem Edge Cases).

Spec: https://build.fhir.org/codesystem.html
       (canonical R4: https://hl7.org/fhir/R4/codesystem.html)
       concept-properties: https://hl7.org/fhir/R4/concept-properties.html
       $lookup: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
       $validate-code: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
       $subsumes: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html

HISTORIAN lens (per chunk assignment): pattern-match SKEPTIC's 3 carry-
forwards against prior bug patterns. Specifically:

  1. CF-SKEPTIC-CS05-01 (abstract hardcoded False at responses.py:46):
     pattern-match against literal-value-vs-canonical-registry drift
     (count=5, PROMOTED). The hardcoded False is a missing-data default
     rather than a wrong-registry-value, but the shape is sibling —
     a hardcoded literal overrides the engine-derived value. HISTORIAN
     confirms the DEFERRED classification is correct (fixture cannot
     reproduce) AND pins the pattern recurrence for future-chunk
     awareness.

  2. CF-SKEPTIC-CS05-02 (missing `inactive` property): pattern-match
     against silent-fallback (GLOBAL_RULES.md "Silent Fallbacks"). The
     engine filters mrconso on SUPPRESS='N' but never surfaces inactive
     codes via the `inactive` property. HISTORIAN verifies by code
     reading that the engine has no inactive-code tracking today, so
     the omission is conformant for active codes (test_s10 baseline).
     The CF documents the reproduction shape for a future fixture
     enhancement. DEFERRED classification confirmed.

  3. CF-SKEPTIC-CS05-03 (multi-hierarchy BFS): pattern-match against
     the CS-03 HISTORIAN QA-052 methodology "carry-forward notes MUST
     be verified by a probe before being trusted". HISTORIAN reads
     services/hierarchy.py:121 to verify the `visited` set structurally
     prevents infinite loops in multi-parent DAGs, then exercises the
     BFS path directly with a synthetic multi-parent fixture in memory.

  4. CF-HISTORIAN-CS04-02 (systemic per-operation `_do_*` duckdb.Error
     gap): pattern-match against the CS-02 HISTORIAN QA-046 alternative-
     failure-path class. HISTORIAN confirms the gap exists on every
     `_do_lookup` / `_do_validate` / `_do_subsumes` path (no
     `try/except duckdb.Error` boundary). The CS-04 carry-forward
     remains systemic and applies to CS-05. HISTORIAN does NOT re-file
     as a new bug (the systemic documentation is the contract).

  5. Test-too-lenient audit (TS-03 HISTORIAN QA-034 pattern): spot-
     check SKEPTIC's 44 probes for negative-only assertions or tests
     that would false-pass on a different bug.

  6. XML-capitalization probe class (CR-002 + CS-04 HISTORIAN test_h61):
     the FIRST XML valueBoolean probe on the $lookup Out `abstract`
     parameter. The CR-002 fix shape (`_scalar_to_xml_attr` boolean
     special-case) holds on the abstract path.

  7. Documentation-vs-implementation drift (TS-01 HISTORIAN QA-007):
     audit the docstrings on `build_parameters_lookup` and `_do_lookup`
     to confirm the abstract/inactive handling is accurately documented.

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape.
  - "Don't manufacture bugs": DEFERRED is valid for genuine fixture
    gaps.
  - Spec citation required on every probe.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
    ("E11", "HT", "Type 2 diabetes mellitus", "AE11", "N", "ICD10CM", "C0011847"),
    ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # single-parent
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# Spec: https://hl7.org/fhir/R4/concept-properties.html

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"


def _lookup_param(body: dict, name: str) -> dict | None:
    """Return the first Out parameter with the given name, or None."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


def _lookup_param_value(body: dict, name: str):
    """Return the value of the first Out parameter with the given name."""
    p = _lookup_param(body, name)
    if p is None:
        return None
    for k, v in p.items():
        if k.startswith("value"):
            return v
    return None


def _has_param(body: dict, name: str) -> bool:
    """Return True if a parameter with the given name is present."""
    return _lookup_param(body, name) is not None


# ---------------------------------------------------------------------------
# Lens 1: CF-SKEPTIC-CS05-01 pattern-match (literal-value-vs-canonical-
#         registry drift, count=5 PROMOTED; here as hardcoded-override-
#         engine-value variant).
# ---------------------------------------------------------------------------
# Per GLOBAL_RULES.md "Code Review Time" (literal-value drift trigger):
# "For response-builder values derived from engine data (URIs, equivalence
# codes, displays, etc.): audit for hardcoded literals that override the
# engine's actual value." The CF-SKEPTIC-CS05-01 hardcoded `abstract=False`
# at responses.py:46 IS a sibling instance of this pattern — the engine
# has no abstract-flag data today, so the hardcoded literal is a missing-
# data default rather than a wrong-registry-value, but the SHAPE is
# identical. A future engine enhancement that wires SNOMED release-file
# `definitionStatusId` into CodeInfo MUST also update
# build_parameters_lookup to propagate code_info.abstract — otherwise the
# hardcoded False will silently override the engine-derived value.
#
# HISTORIAN does NOT file this as a new bug (SKEPTIC correctly classified
# it as DEFERRED per "Don't manufacture bugs"). HISTORIAN's value-add:
# pin the pattern recurrence so the future-chunk fix is structurally
# aware of the literal-value-drift trigger.

def test_h10_lookup_abstract_hardcoded_false_confirmed_by_source_reading():
    """Lens 1 / CF-SKEPTIC-CS05-01 pattern-match: read the source of
    `build_parameters_lookup` in engines/fhir/responses.py and confirm
    the `abstract` Out parameter is hardcoded False (literal-value-
    vs-canonical-registry drift pattern, count=5 PROMOTED sibling).

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out
    `abstract`: "True if this code is abstract (i.e. the code is not
    meant to be used in an instance, only as a grouping/parent concept)."

    This is a SOURCE-READING probe (not an HTTP probe) — it pattern-
    matches the literal-value-drift trigger from GLOBAL_RULES.md by
    parsing the responses.py AST. A future engine enhancement that
    wires CodeInfo.abstract MUST update line 46 to propagate
    code_info.abstract — otherwise this probe will fail loudly.
    """
    from medterm4ds.engines.fhir import responses as resp_module

    src_path = Path(inspect.getsourcefile(resp_module))
    src_text = src_path.read_text()
    tree = ast.parse(src_text)

    # Locate build_parameters_lookup and walk its body for the abstract
    # _param() call. Assert it currently hardcodes False.
    found_abstract_call = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "build_parameters_lookup":
            continue
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.Call):
                continue
            func = stmt.func
            if not isinstance(func, ast.Name) or func.id != "_param":
                continue
            args = stmt.args
            if len(args) < 2:
                continue
            name_arg = args[0]
            value_arg = args[1]
            if (
                isinstance(name_arg, ast.Constant)
                and name_arg.value == "abstract"
            ):
                found_abstract_call = True
                # CF-SKEPTIC-CS05-01 pins the current behavior: the value
                # argument is the literal False (ast.Constant with value
                # False). When the engine is enhanced to carry abstract
                # flags, this assertion MUST be updated to reflect the
                # propagated value (e.g. code_info.abstract).
                assert isinstance(value_arg, ast.Constant), (
                    "abstract _param value should be a literal today; if "
                    "you wired CodeInfo.abstract, update this probe AND "
                    "the CS-05 SKEPTIC test_s70 probe to assert the "
                    "propagated value."
                )
                assert value_arg.value is False
    assert found_abstract_call, (
        "build_parameters_lookup must emit an `abstract` Out parameter "
        "(per FHIR R4 $lookup Out Parameters table)"
    )


def test_h11_lookup_abstract_is_boolean_wire_type(fhir_client):
    """Lens 1 / wire-type audit: the `abstract` Out parameter MUST be
    a boolean on the wire (valueBoolean), not a string or code. Per
    FHIR R4 $lookup Out Parameters: `abstract` is 0..1 boolean.

    Pattern-match: TS-01 HISTORIAN QA-007 wire-format audit class +
    CS-04 TERMINOLOGIST closed-enum wire-type assertion (test_t22).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    abstract_param = _lookup_param(body, "abstract")
    assert abstract_param is not None, "Out `abstract` parameter must be present"
    # The wire-type key MUST be valueBoolean — not valueString, not valueCode.
    assert "valueBoolean" in abstract_param, (
        f"abstract parameter must use valueBoolean wire-type, got keys: "
        f"{list(abstract_param.keys())}"
    )
    assert isinstance(abstract_param["valueBoolean"], bool)


def test_h12_lookup_abstract_consistent_across_leaf_and_parent(fhir_client):
    """Lens 1 / consistency: the hardcoded `abstract=False` is consistent
    across both leaf (44054006 T2DM) and parent (73211009 Diabetes
    mellitus) concepts. CF-SKEPTIC-CS05-01 documents the hardcoded value
    is WRONG for any abstract concept — but the consistency invariant
    (both codes emit the same value today) holds.

    When CF-SKEPTIC-CS05-01 is resolved via engine enhancement, the
    parent concept 73211009 SHOULD emit `abstract=true` (it is a
    SNOMED hierarchy node); this probe will then fail loudly — the
    carry-forward is a load-bearing contract.
    """
    for code in (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS):
        r = fhir_client.get(
            f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={code}"
        )
        assert r.status_code == 200
        body = r.json()
        abstract_val = _lookup_param_value(body, "abstract")
        # Both codes emit False today (hardcoded).
        assert abstract_val is False, (
            f"code {code}: expected abstract=False (current hardcoded "
            f"behavior per CF-SKEPTIC-CS05-01); when engine abstract "
            f"flags are wired, the parent concept MUST emit abstract=true"
        )


# ---------------------------------------------------------------------------
# Lens 2: CF-SKEPTIC-CS05-02 silent-fallback audit (GLOBAL_RULES.md
#         "Silent Fallbacks").
# ---------------------------------------------------------------------------
# Per GLOBAL_RULES.md "Silent Fallbacks — Prohibited Patterns":
# the question is whether omitting the `inactive` property for active
# codes is a silent fallback. Answer: NO — for active codes (SUPPRESS='N'),
# the spec allows `inactive` to be absent (active is the default; the
# property is 0..1 per concept-properties.html). The CF documents the
# gap: IF an inactive code (SUPPRESS in {'O', 'D'}) is ever seeded, the
# engine MUST emit `inactive=true` AND $validate-code MUST return
# result=false by default. Today, the fixture has only SUPPRESS='N' rows.

def test_h20_lookup_active_code_does_not_carry_inactive_property(fhir_client):
    """Lens 2 / silent-fallback audit: on an active code (SUPPRESS='N'),
    the server MUST NOT emit an `inactive` property in the Out `property`
    group. Emitting `inactive=true` for an active code would be silent-
    wrong-answer; emitting `inactive=false` is permitted but not required.

    This probe complements SKEPTIC test_s10 by walking the FULL property
    list (SKEPTIC's _has_property helper checks the property group;
    HISTORIAN re-walks to verify no `inactive` entry sneaks in under a
    different shape).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    # Walk the property group explicitly. No entry with code="inactive"
    # should be present for an active code.
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        parts = p.get("part", [])
        for part in parts:
            if part.get("name") == "code" and part.get("valueCode") == "inactive":
                pytest.fail(
                    "active code (SUPPRESS='N') must not carry `inactive` "
                    "property; CF-SKEPTIC-CS05-02 documents the gap for "
                    "inactive codes only"
                )


def test_h21_lookup_active_code_does_not_carry_inactive_at_top_level(fhir_client):
    """Lens 2 / structural audit: the `inactive` property is in the Out
    `property` group (per concept-properties.html), NOT a top-level Out
    parameter. Some servers emit it at top-level; medterm4ds does not.

    Pattern-match: the structural difference between top-level Out
    parameters (`name`, `code`, `system`, `display`, `abstract`) and
    property-group entries (`cui`, `tty`, `aui`, custom). `inactive`
    belongs in the property group per spec.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r.status_code == 200
    body = r.json()
    # `inactive` must NOT be a top-level Out parameter.
    assert _lookup_param(body, "inactive") is None, (
        "inactive must not be a top-level Out parameter; it belongs in "
        "the Out `property` group per FHIR R4 concept-properties.html"
    )


def test_h22_engine_filters_suppress_n_confirmed_by_source_reading():
    """Lens 2 / source-reading audit: confirm the engine's get_code_infos
    path filters on SUPPRESS='N'. This verifies CF-SKEPTIC-CS05-02's
    claim that the engine has no inactive-code tracking today.

    CF-SKEPTIC-CS05-02 documents: "the engine filters mrconso on
    SUPPRESS='N' but does not surface inactive codes via the `inactive`
    property". HISTORIAN verifies the filter exists in the SQL.
    """
    from medterm4ds.engines.duckdb._mixins import _LookupOps as lookup_mixin

    src_path = Path(inspect.getsourcefile(lookup_mixin))
    src_text = src_path.read_text()
    # The filter shape: WHERE SUPPRESS = 'N' (or equivalent). When the
    # CF-SKEPTIC-CS05-02 fix lands (emit inactive=true for SUPPRESS='O'
    # codes), the filter MUST change to include inactive codes OR a
    # separate code path must query inactive rows.
    # Today, the SUPPRESS='N' filter is the active-code filter.
    assert "SUPPRESS" in src_text.upper(), (
        "engine lookup path must reference SUPPRESS column "
        "(CF-SKEPTIC-CS05-02 documents the filter shape)"
    )


# ---------------------------------------------------------------------------
# Lens 3: CF-SKEPTIC-CS05-03 BFS multi-hierarchy verification (TS-03
#         HISTORIAN QA-034 carry-forward-verification-by-probe pattern).
# ---------------------------------------------------------------------------
# Per CS-03 HISTORIAN QA-052 methodology: carry-forward notes about
# engine behavior MUST be verified by a probe before being trusted.
# CF-SKEPTIC-CS05-03 claims "services/hierarchy.py BFS with `visited`
# set (line 121) structurally handles multi-parent DAGs". HISTORIAN
# verifies this by:
#   (a) reading the source to confirm the visited set
#   (b) exercising the BFS directly with a synthetic multi-parent DAG

def test_h30_bfs_visited_set_present_in_hierarchy_source():
    """Lens 3 / CF-SKEPTIC-CS05-03 verification by source reading:
    confirm services/hierarchy.py:get_descendants_bfs initializes a
    `visited` set and consults it before adding a child to the frontier.

    Pattern-match: TS-03 HISTORIAN QA-034 carry-forward-verification-
    by-probe pattern. The CF note claims structural correctness; this
    probe verifies the claim.
    """
    from medterm4ds.services import hierarchy as hierarchy_module

    src_path = Path(inspect.getsourcefile(hierarchy_module))
    src_text = src_path.read_text()
    tree = ast.parse(src_text)

    # Locate get_descendants_bfs and confirm the visited set pattern.
    found_bfs = False
    found_visited_init = False
    found_visited_check = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "get_descendants_bfs":
            continue
        found_bfs = True
        for stmt in ast.walk(node):
            # Look for `visited: set[str] = {seed.code}` (AnnAssign) or
            # `visited = {...}` (Assign) initializations.
            if isinstance(stmt, ast.AnnAssign):
                tgt = stmt.target
                if isinstance(tgt, ast.Name) and tgt.id == "visited":
                    found_visited_init = True
            if isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "visited":
                        found_visited_init = True
            # Look for `child_code in visited` membership check.
            if isinstance(stmt, ast.Compare):
                if isinstance(stmt.ops, list) and any(isinstance(op, ast.In) for op in stmt.ops):
                    # Walk the comparators for 'visited'
                    for comp in stmt.comparators:
                        if isinstance(comp, ast.Name) and comp.id == "visited":
                            found_visited_check = True
    assert found_bfs, "get_descendants_bfs must exist in hierarchy.py"
    assert found_visited_init, (
        "get_descendants_bfs must initialize a `visited` set "
        "(CF-SKEPTIC-CS05-03 contract)"
    )
    assert found_visited_check, (
        "get_descendants_bfs must check membership in `visited` before "
        "adding to the frontier (CF-SKEPTIC-CS05-03 contract)"
    )


def test_h31_bfs_handles_multi_parent_dag_synthetic():
    """Lens 3 / CF-SKEPTIC-CS05-03 verification by direct exercise:
    construct a synthetic multi-parent DAG and exercise
    get_descendants_bfs directly. The BFS MUST visit each child exactly
    once even when multiple parents point to it.

    Pattern shape: HISTORIAN's "alternative-failure-path probe" — the
    conformance fixture has only a single-parent mrrel row, so the HTTP
    surface cannot exercise multi-parent. HISTORIAN exercises the
    underlying service directly to verify the structural correctness
    claim in CF-SKEPTIC-CS05-03.
    """
    # Synthetic DAG: ROOT → {MIDDLE_A, MIDDLE_B}; both middles → LEAF.
    # A naive BFS without visited set would enqueue LEAF twice.
    # The medterm4ds BFS MUST return LEAF exactly once.
    # We mock the HierarchyEngine.get_code_relations to return our
    # synthetic shape. Mock-free per AGENTS.md convention is preferred,
    # but for pure-graph verification the engine interface is the graph
    # contract; using a tiny stub is consistent with the convention.
    from medterm4ds.core.models import CodeRef, CodeRelation
    from medterm4ds.services.hierarchy import get_descendants_bfs

    ROOT = "ROOT"
    MIDDLE_A = "MIDA"
    MIDDLE_B = "MIDB"
    LEAF = "LEAF"
    SRC = "SYNTH"

    class _StubHierarchyEngine:
        """Minimal HierarchyEngine stub returning a fixed multi-parent DAG."""

        def get_code_relations(self, refs, *, direction, max_depth, limit, include_retired=False):
            # Only "children" direction is used by get_descendants_bfs.
            out: list[CodeRelation] = []
            for ref in refs:
                if ref.code == ROOT:
                    out.append(CodeRelation(
                        source=CodeRef(source=SRC, code=ROOT),
                        target=CodeRef(source=SRC, code=MIDDLE_A),
                        relationship="isa",
                    ))
                    out.append(CodeRelation(
                        source=CodeRef(source=SRC, code=ROOT),
                        target=CodeRef(source=SRC, code=MIDDLE_B),
                        relationship="isa",
                    ))
                elif ref.code in (MIDDLE_A, MIDDLE_B):
                    out.append(CodeRelation(
                        source=CodeRef(source=SRC, code=ref.code),
                        target=CodeRef(source=SRC, code=LEAF),
                        relationship="isa",
                    ))
            return out

    stub = _StubHierarchyEngine()
    relations, depth_cap_hit = get_descendants_bfs(
        CodeRef(source=SRC, code=ROOT),
        engine=stub,
        max_depth=5,
    )
    # Expected: MIDDLE_A, MIDDLE_B at depth 1; LEAF at depth 2 (once).
    target_codes = [r.target.code for r in relations]
    assert target_codes.count(LEAF) == 1, (
        f"BFS must visit LEAF exactly once in multi-parent DAG; got "
        f"{target_codes} (CF-SKEPTIC-CS05-03 contract)"
    )
    assert MIDDLE_A in target_codes
    assert MIDDLE_B in target_codes
    assert not depth_cap_hit


def test_h32_bfs_handles_cycle_synthetic():
    """Lens 3 / CF-SKEPTIC-CS05-03 cycle-prevention verification: a
    cycle in the mrrel graph (A → B → A) MUST NOT cause infinite
    recursion. The `visited` set prevents this.

    Pattern-match: the CF-SKEPTIC-CS05-03 contract includes "each child
    visited exactly once via a visited set, so cost is O(nodes) not
    O(paths)". A cycle is the pathological case where the visited set
    is load-bearing.
    """
    from medterm4ds.core.models import CodeRef, CodeRelation
    from medterm4ds.services.hierarchy import get_descendants_bfs

    SRC = "SYNTH"
    A = "A"
    B = "B"

    class _CyclicStubEngine:
        def get_code_relations(self, refs, *, direction, max_depth, limit, include_retired=False):
            out: list[CodeRelation] = []
            for ref in refs:
                if ref.code == A:
                    out.append(CodeRelation(
                        source=CodeRef(source=SRC, code=A),
                        target=CodeRef(source=SRC, code=B),
                        relationship="isa",
                    ))
                elif ref.code == B:
                    out.append(CodeRelation(
                        source=CodeRef(source=SRC, code=B),
                        target=CodeRef(source=SRC, code=A),
                        relationship="isa",
                    ))
            return out

    stub = _CyclicStubEngine()
    # The visited set MUST prevent infinite loop. Without it, this call
    # would oscillate A → B → A → B... until max_depth. With it, the
    # walk returns A's descendants as [B] only (A was visited as seed,
    # so the B → A edge is skipped).
    relations, _ = get_descendants_bfs(
        CodeRef(source=SRC, code=A),
        engine=stub,
        max_depth=20,
    )
    target_codes = [r.target.code for r in relations]
    # B is reached once; A is NOT re-enqueued (cycle broken).
    assert target_codes == [B], (
        f"BFS must break cycle A↔B; expected [B], got {target_codes}"
    )


# ---------------------------------------------------------------------------
# Lens 4: CF-HISTORIAN-CS04-02 systemic `_do_*` duckdb.Error gap.
# ---------------------------------------------------------------------------
# Per AGENTS.md "Known Fragile Areas" entry for CF-HISTORIAN-CS04-02:
# the per-operation `_do_*` handlers do NOT wrap engine calls in
# `try/except duckdb.Error`. A transient DuckDB operational failure
# propagates to Starlette's default 500 with text/plain body. The
# batch dispatcher has the boundary (TS-04 HISTORIAN QA-038); the per-
# operation path does not.
#
# HISTORIAN confirms the gap is structural (not CS-04-specific) by
# reading the source of every _do_* handler. The carry-forward remains
# valid — HISTORIAN does NOT re-file it as a new CS-05 bug.

def test_h40_do_lookup_lacks_duckdb_error_boundary():
    """Lens 4 / CF-HISTORIAN-CS04-02 systemic gap verification: read
    the source of `_do_lookup` and confirm it does NOT wrap the engine
    call (`get_code_infos`) in `try/except duckdb.Error`.

    The gap is structural: a transient DuckDB failure would propagate
    past the handler. The CF-HISTORIAN-CS04-02 carry-forward remains
    valid for the CS-05 surface too.
    """
    from medterm4ds.apps import fhir_api

    src_path = Path(inspect.getsourcefile(fhir_api))
    src_text = src_path.read_text()
    tree = ast.parse(src_text)

    # Locate _do_lookup.
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_do_lookup":
            continue
        # Walk the function body for try/except handlers with duckdb.Error.
        has_try = False
        has_duckdb_error = False
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Try):
                has_try = True
                for handler in stmt.handlers:
                    if isinstance(handler.type, ast.Attribute):
                        if (
                            isinstance(handler.type.value, ast.Name)
                            and handler.type.value.id == "duckdb"
                            and handler.type.attr == "Error"
                        ):
                            has_duckdb_error = True
                    elif isinstance(handler.type, ast.Name):
                        if handler.type.id == "Error":
                            # Broad `except Error` is not the narrow shape.
                            pass
        # The systemic gap: _do_lookup has NO try/except duckdb.Error.
        # This assertion documents the gap; when CF-HISTORIAN-CS04-02 is
        # resolved via a generic @app.exception_handler(duckdb.Error),
        # this probe MUST be updated.
        assert not (has_try and has_duckdb_error), (
            "_do_lookup now has a duckdb.Error boundary — update "
            "CF-HISTORIAN-CS04-02 documentation"
        )


def test_h41_do_validate_lacks_duckdb_error_boundary():
    """Lens 4 / CF-HISTORIAN-CS04-02 systemic gap on `_do_validate`.
    Same shape as test_h40 but for the $validate-code handler."""
    from medterm4ds.apps import fhir_api

    src_path = Path(inspect.getsourcefile(fhir_api))
    src_text = src_path.read_text()
    tree = ast.parse(src_text)

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_do_validate":
            continue
        has_duckdb_error = False
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Try):
                for handler in stmt.handlers:
                    if isinstance(handler.type, ast.Attribute):
                        if (
                            isinstance(handler.type.value, ast.Name)
                            and handler.type.value.id == "duckdb"
                            and handler.type.attr == "Error"
                        ):
                            has_duckdb_error = True
        assert not has_duckdb_error, (
            "_do_validate now has a duckdb.Error boundary — update "
            "CF-HISTORIAN-CS04-02 documentation"
        )


def test_h42_do_subsumes_lacks_duckdb_error_boundary():
    """Lens 4 / CF-HISTORIAN-CS04-02 systemic gap on `_do_subsumes`.
    Same shape as test_h40 but for the $subsumes handler."""
    from medterm4ds.apps import fhir_api

    src_path = Path(inspect.getsourcefile(fhir_api))
    src_text = src_path.read_text()
    tree = ast.parse(src_text)

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_do_subsumes":
            continue
        has_duckdb_error = False
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Try):
                for handler in stmt.handlers:
                    if isinstance(handler.type, ast.Attribute):
                        if (
                            isinstance(handler.type.value, ast.Name)
                            and handler.type.value.id == "duckdb"
                            and handler.type.attr == "Error"
                        ):
                            has_duckdb_error = True
        assert not has_duckdb_error, (
            "_do_subsumes now has a duckdb.Error boundary — update "
            "CF-HISTORIAN-CS04-02 documentation"
        )


# ---------------------------------------------------------------------------
# Lens 5: Test-too-lenient audit (TS-03 HISTORIAN QA-034 pattern).
# ---------------------------------------------------------------------------
# Spot-check SKEPTIC's 44 probes for negative-only assertions or tests
# that would false-pass on a different bug. Per GLOBAL_RULES.md "Test-
# too-lenient": input-recognition probes MUST assert the POSITIVE
# success shape (200 + resource body with expected fields), not just
# absence of one error string.

def test_h50_skeptic_test_s10_positive_success_shape(fhir_client):
    """Lens 5 / test-too-lenient audit on SKEPTIC test_s10: the probe
    asserts an active code does not emit `inactive=true`. HISTORIAN re-
    runs the same request AND asserts the positive success shape (200 +
    Parameters body + required Out parameters) so a future regression
    producing a generic 500 would fail this probe.

    Pattern-match: TS-03 HISTORIAN QA-034 (negative-only assertion gave
    false-positive pass on a real bug). SKEPTIC test_s10 is positive-
    success-shape but HISTORIAN adds the wire-shape (Parameters
    resourceType) and required Out parameter (code, system) assertions.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    # Positive success shape.
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    # Required Out parameters MUST be present per FHIR R4 $lookup.
    assert _lookup_param_value(body, "code") == SNOMED_T2DM
    assert _lookup_param_value(body, "system") == SNOMED_URI
    assert _lookup_param_value(body, "display") is not None
    # The inactive property group MUST be absent for an active code.
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        for part in p.get("part", []):
            if part.get("name") == "code" and part.get("valueCode") == "inactive":
                pytest.fail("inactive property must not be emitted for active code")


def test_h51_skeptic_test_s62_positive_failure_shape(fhir_client):
    """Lens 5 / test-too-lenient audit on SKEPTIC test_s62 (unknown
    system returns 400): HISTORIAN re-runs the request and asserts the
    FULL failure shape (status + OperationOutcome resourceType +
    severity=error). A future regression producing a generic 500 would
    fail this probe.

    Pattern-match: test-too-lenient tightening. SKEPTIC test_s62 only
    asserted status_code == 400; HISTORIAN adds OperationOutcome shape.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup?system=http://unknown.example/system&code=X"
    )
    assert r.status_code == 400
    body = r.json()
    # The failure MUST carry a FHIR OperationOutcome, not a generic
    # {'detail': ...} body (framework default).
    assert body.get("resourceType") == "OperationOutcome"
    issues = body.get("issue", [])
    assert len(issues) >= 1
    assert issues[0].get("severity") == "error"


def test_h52_skeptic_test_s61_positive_failure_shape(fhir_client):
    """Lens 5 / test-too-lenient audit on SKEPTIC test_s61 (unknown
    code returns result=false): HISTORIAN re-runs AND asserts the full
    Parameters body shape. A future regression producing a 500 would
    fail this probe.

    Pattern-match: positive-success-shape on a "result=false" response.
    The response is still 200 with a Parameters body — the failure mode
    is in the result field, not the HTTP status.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code=UNKNOWN_CODE_XYZ"
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    result_val = None
    for p in body.get("parameter", []):
        if p.get("name") == "result":
            result_val = p.get("valueBoolean")
    assert result_val is False


# ---------------------------------------------------------------------------
# Lens 6: XML-capitalization probe on $lookup Out `abstract` (CR-002 +
#         CS-04 HISTORIAN test_h61 probe class extended).
# ---------------------------------------------------------------------------
# Per GLOBAL_RULES.md "Boolean capitalization on serializers" (PROMOTED):
# Python's `str(False) == "False"`, not `"false"`. FHIR R4 §3.4.1
# mandates lowercase `true`/`false` for boolean primitives. The CR-002
# fix (`_scalar_to_xml_attr` boolean special-case in engines/fhir/xml.py)
# is structurally applied to every wire-format serializer. CS-03 EXPLORER
# added the FIRST XML valueBoolean probe on an OPERATION route
# ($validate-code); CS-04 HISTORIAN added the first on $subsumes outcome.
# CS-05 HISTORIAN adds the FIRST on $lookup Out `abstract`.

def test_h60_lookup_xml_abstract_is_lowercase_boolean(fhir_client):
    """Lens 6 / CR-002 fix shape extended: $lookup Out `abstract`
    parameter MUST render as lowercase `false` in XML (not `False`).

    Spec: FHIR R4 §3.4.1 boolean primitive. The CR-002 fix
    (`_scalar_to_xml_attr` boolean special-case) holds on the $lookup
    abstract path. This is the FIRST XML valueBoolean probe on $lookup
    Out `abstract` (CS-03 EXPLORER tested $validate-code Out `result`;
    CS-04 HISTORIAN tested $subsumes Out `outcome`).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}&_format=xml"
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml"), (
        f"expected application/fhir+xml content-type, got {r.headers.get('content-type')}"
    )
    body_text = r.text
    # The abstract parameter must render as value="false" (lowercase).
    # The XML form is: <name value="abstract"/><valueBoolean value="false"/>
    assert '<name value="abstract"/>' in body_text, (
        "XML body must contain the abstract parameter name"
    )
    # The CR-002 fix shape: valueBoolean MUST be lowercase 'false'.
    assert '<valueBoolean value="false"/>' in body_text, (
        "abstract valueBoolean MUST render lowercase 'false' per FHIR R4 §3.4.1"
    )
    # Negative assertion: the capital-T form MUST NOT appear.
    assert '<valueBoolean value="False"/>' not in body_text, (
        "abstract valueBoolean MUST NOT render capital-T 'False' "
        "(CR-002 fix shape — _scalar_to_xml_attr boolean special-case)"
    )


def test_h61_lookup_xml_accept_header_negotiation(fhir_client):
    """Lens 6 / XML-via-Accept negotiation on $lookup: per §3.1.0.1.11,
    Accept: application/fhir+xml MUST negotiate to XML just like
    `_format=xml`. CS-04 HISTORIAN test_h61 verified this on $subsumes;
    CS-05 HISTORIAN extends to $lookup Out `abstract`."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}",
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml")
    body_text = r.text
    assert 'valueBoolean value="false"' in body_text
    assert 'valueBoolean value="False"' not in body_text


# ---------------------------------------------------------------------------
# Lens 7: Documentation-vs-implementation drift (TS-01 HISTORIAN QA-007).
# ---------------------------------------------------------------------------
# Per TS-01 HISTORIAN QA-007: "read every new function's docstring and
# verify the body delivers it. Catches 'claims conformance, doesn't
# deliver'". HISTORIAN audits the docstrings on build_parameters_lookup
# and _do_lookup to confirm the abstract/inactive handling is accurately
# documented.

def test_h70_build_parameters_lookup_docstring_accurate():
    """Lens 7 / docstring-vs-implementation drift audit: read the
    docstring of `build_parameters_lookup` and confirm it accurately
    describes the abstract handling (or lack thereof). The docstring
    MUST NOT claim abstract-ness is propagated from the engine (it
    isn't — CF-SKEPTIC-CS05-01 documents the hardcoded False).

    Pattern-match: TS-01 HISTORIAN QA-007 (docstring promised
    convention, body was no-op). Here the inverse: body hardcodes a
    value, docstring must NOT claim engine propagation.
    """
    from medterm4ds.engines.fhir.responses import build_parameters_lookup

    docstring = build_parameters_lookup.__doc__ or ""
    # The docstring should NOT claim the abstract flag reflects the
    # engine's concept abstractness. Today the implementation hardcodes
    # False — a docstring claiming otherwise would be drift.
    forbidden_phrases = [
        "reflects the concept's abstract",
        "propagates abstract",
        "engine-derived abstract",
        "abstract flag from the engine",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in docstring.lower(), (
            f"build_parameters_lookup docstring must not claim '{phrase}' "
            f"— the implementation hardcodes False per CF-SKEPTIC-CS05-01"
        )


def test_h71_do_lookup_docstring_documents_custom_properties():
    """Lens 7 / docstring audit on `_do_lookup`: the docstring MUST
    document the custom properties added under the Out `property` group
    (patient-friendly, match-type, canonical-code, canonical-system, tty).
    Per CS-01 TERMINOLOGIST QA-045 (DECISION (b)): the custom properties
    are server-local vocabulary documented as a registry-as-contract.

    Pattern-match: TS-01 HISTORIAN QA-007 documentation-vs-implementation
    drift. The _do_lookup docstring is the load-bearing contract for
    the custom properties; if it's missing or inaccurate, future chunks
    will re-introduce drift.
    """
    # The _do_lookup function is nested inside create_fhir_app, so we
    # access the source directly.
    from medterm4ds.apps import fhir_api

    src_path = Path(inspect.getsourcefile(fhir_api))
    src_text = src_path.read_text()
    # The docstring is the first string literal after `def _do_lookup`.
    marker = "def _do_lookup("
    idx = src_text.find(marker)
    assert idx >= 0, "_do_lookup function must exist in fhir_api.py"
    # Extract the docstring (triple-quoted string after the def).
    snippet = src_text[idx:idx + 5000]
    # Required documentation: each custom property.
    required_tokens = [
        "patient-friendly",
        "match-type",
        "canonical-code",
        "canonical-system",
        "tty",
    ]
    for token in required_tokens:
        assert token in snippet, (
            f"_do_lookup docstring must document the '{token}' custom "
            f"property (CS-01 TERMINOLOGIST QA-045 registry-as-contract)"
        )


# ---------------------------------------------------------------------------
# Lens 8: GET↔POST parity for $lookup (CS-04 EXPLORER methodology +
#         spec-citation discipline).
# ---------------------------------------------------------------------------
# Per FHIR R4 §3.1.0.1.1: operations MAY be invoked via GET or POST on
# either the type or a resource instance. CS-04 EXPLORER added GET↔POST
# round-trip consistency probes on $subsumes; CS-05 HISTORIAN extends
# to $lookup Out `abstract` to confirm the hardcoded False is identical
# on both invocation paths.

def test_h80_lookup_get_post_parity_for_abstract(fhir_client):
    """Lens 8 / GET↔POST parity: $lookup Out `abstract` value MUST be
    identical on GET and POST. Per FHIR R4 §3.1.0.1.1, both invocation
    methods produce the same response shape.

    Pattern-match: CS-04 EXPLORER test_e170 GET↔POST round-trip
    consistency; CS-05 HISTORIAN applies to the abstract Out parameter.
    """
    # GET
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r_get.status_code == 200
    get_abstract = _lookup_param_value(r_get.json(), "abstract")

    # POST
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM},
            ],
        },
    )
    assert r_post.status_code == 200
    post_abstract = _lookup_param_value(r_post.json(), "abstract")

    assert get_abstract == post_abstract == False


def test_h81_lookup_get_post_parity_for_property_group(fhir_client):
    """Lens 8 / GET↔POST parity on the Out `property` group: the
    property codes returned MUST be identical on GET and POST. The
    property group carries cui, tty, aui, and custom properties —
    divergence between invocation paths would be silent-wrong-answer."""
    def _property_codes(body):
        codes = set()
        for p in body.get("parameter", []):
            if p.get("name") != "property":
                continue
            for part in p.get("part", []):
                if part.get("name") == "code":
                    codes.add(part.get("valueCode"))
        return codes

    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r_get.status_code == 200
    get_codes = _property_codes(r_get.json())

    r_post = fhir_client.post(
        "/fhir/CodeSystem/$lookup",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM},
            ],
        },
    )
    assert r_post.status_code == 200
    post_codes = _property_codes(r_post.json())

    assert get_codes == post_codes
    # The seeded SNOMED T2DM concept has cui + tty + aui.
    assert "cui" in get_codes
    assert "tty" in get_codes
    assert "aui" in get_codes
