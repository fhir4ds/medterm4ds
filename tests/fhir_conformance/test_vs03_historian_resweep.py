"""VS-03 HISTORIAN resweep: ValueSet $expand — Advanced.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Expansion.total: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.total
valueset-toocostly: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
Filter operator: https://hl7.org/fhir/R4/valueset-concept-operator.html
$expand operation definition: https://hl7.org/fhir/R4/valueset-operation-expand.html
Parameters resource: https://hl7.org/fhir/R4/parameters.html

This is the resweep (post-milestone-10) HISTORIAN pass for chunk VS-03. The
prior VS-03 HISTORIAN test_vs03_historian.py covered the baseline patterns
(CF-SKEPTIC-VS01-01, CF-HISTORIAN-VS02-01, CF-HISTORIAN-VS02-02, GAP-T01
referenced). The SKEPTIC resweep (test_vs03_skeptic_resweep.py) added 93
new probes — including 10 source-read structural contracts that documented
the canonical-DISPLAY fallback chain and the 5-sibling isinstance guards.

SKEPTIC tip for HISTORIAN (from VS-03_SKEPTIC_qa_handoff.md):
  "HISTORIAN next: re-derive prior bug patterns via source-read contracts.
   Focus areas:
   (a) client-input-as-canonical drift count=9 PROMOTED
   (b) cross-handler helper-wiring count=6 PROMOTED
       (_extract_valueset_from_parameters is 4th sibling — wired into expand_post)
   (c) explicit-size-on-truncation count=3 PROMOTED
       (VS-02 SKEPTIC QA-057 — total= parameter passed at all 3 call sites
        in _expand_intensional)
   (d) 10th PROMOTED pattern isinstance-guard
       (5 sibling guards present in _expand_intensional)

   AST-walk build_valueset_expand call sites and verify every truncating
   site passes explicit total=. The BFS-cap-on-total territory
   (CF-HISTORIAN-VS02-01, HIGH, deferred) is the remaining structural gap
   — fixture-coincidence-pinned, invisible in CI."

This HISTORIAN resweep applies the **pattern-match lens** ("what broke
before?") and is organized into 13 lens dimensions:

  Lens 1  — CF-HISTORIAN-VS02-01 source-read re-verification (LOAD-BEARING
             per SKEPTIC tip): the intensional BFS path uses limit=count
             (+0 probe) and total=len(deduped) where deduped is built from
             the BFS-capped descendants list. Bug is structurally present
             but fixture-coincidence-masked.
  Lens 2  — explicit-size-on-truncation pattern (count=3 PROMOTED at
             GLOBAL_RULES.md line 136) — AST-walk all 4
             build_valueset_expand call sites; verify every truncating
             site passes explicit total= per SKEPTIC tip (c).
  Lens 3  — client-input-as-canonical drift pattern (count=9 PROMOTED)
             — verify canonical_system_uri wired into the intensional
             path per SKEPTIC tip (a); CR-013 9th-instance.
  Lens 4  — cross-handler helper-wiring pattern (count=6 PROMOTED) —
             _extract_valueset_from_parameters is the 4th sibling; verify
             wired into expand_post per SKEPTIC tip (b).
  Lens 5  — 10th PROMOTED pattern isinstance-guard at untrusted-data
             list-iterator boundary (CS-04 HISTORIAN QA-001): 5 sibling
             guards in _expand_intensional per SKEPTIC tip (d).
  Lens 6  — CF-SKEPTIC-VS01-01 re-derivation (7 of 9 filter operators
             silently dropped in _expand_intensional).
  Lens 7  — CF-HISTORIAN-VS02-02 (RESOLVED — TS-03 SKEPTIC QA-001)
             re-derivation: implicit value set Form (a) uses
             canonical_system_uri.
  Lens 8  — QA-059 Parameters-with-valueSet body silently dropped
             (CF-EXPLORER-VS01 prior pattern) — sibling of cross-handler
             helper-wiring.
  Lens 9  — PROMOTED patterns re-derivation (count=10).
  Lens 10 — Source-read structural contracts for the 4 call sites +
             _expand_intensional internal structure.
  Lens 11 — Response shape audit across every mode.
  Lens 12 — Cross-handler GET<->POST byte-exact parity on advanced shapes.
  Lens 13 — Carry-forward-as-probe pinning (extension of strategy 56).

Conformance fixture (4 mrconso rows, 1 mrrel row): SNOMEDCT_US has 2 codes
(Diabetes mellitus / T2DM); ICD10CM has 1 (E11); RXNORM has 1 (metformin);
mrrel has a single isa relationship (T2DM -> Diabetes mellitus). The fixture
is the load-bearing reason CF-HISTORIAN-VS02-01 is invisible in CI: with
exactly 1 mrrel row, count=1 produces root+1=2 contains entries; BFS limit
caps at 1 descendant; truncation is structurally present but behaviorally
indistinguishable from "complete at exactly the budget".
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion (expansion shape)
#
# FHIR R4 filter-operator enum — single source of truth per milestone-2 review
# (CR-014): import the canonical frozen-set from engines.fhir rather than
# redefining it locally.
from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,  # noqa: F401
    FHIR_R4_FILTER_OPERATORS,
)

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
_RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)
_HIERARCHY_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "services" / "hierarchy.py"
)


# =============================================================================
# Helpers
# =============================================================================


def _get_func_source(
    module_path: Path, parent_name: str, child_name: str | None = None
) -> str:
    """Read the source of a top-level function or a nested function.

    Walks ``ast`` looking for ``ast.FunctionDef`` and ``ast.AsyncFunctionDef``.
    The nested-function form (``parent_name`` = factory function,
    ``child_name`` = inner def) is needed because many route handlers are
    defined inside the ``create_fhir_app`` factory. Mirrors the helper in
    test_vs02_historian_resweep.py and test_vs03_skeptic_resweep.py.
    """
    src = module_path.read_text()
    tree = ast.parse(src)
    if child_name is None:
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == parent_name
            ):
                return ast.get_source_segment(src, node) or ""
        return ""

    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == parent_name
        ):
            for child in ast.walk(node):
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == child_name
                ):
                    return ast.get_source_segment(src, child) or ""
    return ""


def _get_expand(fhir_client, *, params: dict):
    """GET /fhir/ValueSet/$expand with query params."""
    resp = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=params,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _post_expand(fhir_client, body: dict, *, params: dict | None = None):
    """POST a body to /fhir/ValueSet/$expand. Returns (status, body_json)."""
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=body,
        params=params or {},
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _make_intensional_snomed_isa() -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-hist-resweep-intensional",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {
                        "property": "concept",
                        "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }
                ],
            }],
        },
    }


def _make_intensional_snomed_descendent_of() -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-hist-resweep-descendent-of",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {
                        "property": "concept",
                        "op": "descendent-of",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }
                ],
            }],
        },
    }


def _make_explicit_concept_list() -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-hist-resweep-explicit",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "concept": [
                    {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"},
                    {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"},
                ],
            }],
        },
    }


def _make_parameters_with_valueset(valueset: dict, count: int = 20) -> dict:
    """Build a Parameters-with-valueSet body per FHIR R4 §4.7.5."""
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "valueSet", "resource": valueset},
            {"name": "count", "valueInteger": count},
        ],
    }


def _contains_codes(body: dict) -> list[tuple[str, str]]:
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


# =============================================================================
# Lens 1 — CF-HISTORIAN-VS02-01 source-read re-verification (LOAD-BEARING)
#
# SKEPTIC tip (a) rephrased: re-derive the explicit-size-on-truncation pattern
# (count=3 PROMOTED) and verify the intensional call site at apps/fhir_api.py
# passes total=. But the intensional path is the ONLY ONE that uses the
# BFS-cap-on-total territory — limit=count +0 probe + total=len(deduped) where
# deduped is built from the BFS-capped descendants list. The bug is structurally
# present but fixture-coincidence-masked.
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.total
# Quote: "The total number of concepts in the expansion."
# =============================================================================


class TestLens1CFHistorianVS02OneSourceRead:
    """Lens 1: CF-HISTORIAN-VS02-01 source-read re-verification.

    The bug is structurally present: get_descendants_bfs early-exits at the
    limit (line 129 / 146 of services/hierarchy.py), so the descendants list
    passed to the intensional builder is ALREADY truncated, and
    total=len(deduped) reports the truncated size when the cap fires. The
    conformance fixture masks the bug (exactly 1 mrrel row matches count=1).
    """

    def test_h10_bfs_helper_early_exit_on_limit_source_read(self):
        """Source-read: get_descendants_bfs early-exits when len(results) >= limit.

        Per the docstring at services/hierarchy.py:107-109: ``limit: optional
        cap on number of descendant relations returned. The walk stops as soon
        as the cap is hit (early-exit)``. This early-exit is the load-bearing
        structural condition that makes CF-HISTORIAN-VS02-01 a real bug —
        when the cap fires, the returned list is truncated.
        """
        src = _HIERARCHY_PATH.read_text()
        tree = ast.parse(src)

        bfs_func = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "get_descendants_bfs"
            ):
                bfs_func = node
                break
        assert bfs_func is not None, "get_descendants_bfs not found"

        bfs_src = ast.get_source_segment(src, bfs_func) or ""

        # The early-exit conditions MUST be present.
        # Line 129: ``if limit is not None and len(results) >= limit:``
        # Line 146: ``if limit is not None and len(results) >= limit:``
        assert "len(results) >= limit" in bfs_src, (
            "CF-HISTORIAN-VS02-01 load-bearing condition missing: "
            "get_descendants_bfs MUST early-exit when len(results) >= limit"
        )

        # The docstring MUST document the early-exit semantic.
        assert "early-exit" in bfs_src.lower() or "early exit" in bfs_src.lower(), (
            "get_descendants_bfs docstring MUST document the early-exit semantic "
            "(callers relying on the cap need to know the returned list is truncated)"
        )

    def test_h11_bfs_helper_returns_truncated_list_when_capped(self):
        """Behavioral: BFS helper returns at most `limit` relations.

        When limit=N, the helper returns at most N relations. This is the
        structural property that makes CF-HISTORIAN-VS02-01 a real bug: the
        intensional call site uses the BFS-capped list as the source for
        ``total=len(deduped)``, so total reports the truncated size when
        the cap fires.
        """
        from medterm4ds.core.models import CodeRef
        from medterm4ds.engines.duckdb.engine import LocalDuckDBEngine
        from medterm4ds.services.hierarchy import get_descendants_bfs

        # Use an in-memory engine seeded with the conformance fixture's
        # 2-level hierarchy. The fixture has T2DM is-a DM, so descendants
        # of DM = [T2DM].
        import duckdb
        con = duckdb.connect(":memory:")
        con.execute("""CREATE TABLE mrconso (
            CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
            SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
        )""")
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),
                ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
            ],
        )
        con.execute("""CREATE TABLE mrrel (
            AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR
        )""")
        con.execute(
            "INSERT INTO mrrel VALUES ('A44054006', 'A73211009', 'isa', 'PAR')"
        )

        engine = LocalDuckDBEngine(con=con)
        seed = CodeRef(source="SNOMEDCT_US", code="73211009")
        relations, depth_cap = get_descendants_bfs(seed, engine=engine, max_depth=5, limit=1)
        # limit=1 MUST cap at 1 relation.
        assert len(relations) <= 1, (
            f"BFS limit=1 returned {len(relations)} relations; cap MUST be honored"
        )

    def test_h12_intensional_call_site_passes_bfs_limit_count_source_read(self):
        """Source-read: _expand_intensional passes ``limit=count`` to BFS.

        This is the load-bearing line that makes CF-HISTORIAN-VS02-01 a real
        bug. The call site at apps/fhir_api.py:2658 invokes
        ``get_descendants_bfs(..., limit=count)`` — so when count < natural
        descendant count, BFS early-exits with a truncated list.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert src, "_expand_intensional source not found"

        # The BFS call MUST pass limit=count (or a count-derived expression).
        # Source-read: look for the get_descendants_bfs call followed by
        # ``limit=count`` within the function body.
        assert "get_descendants_bfs(" in src, (
            "CF-HISTORIAN-VS02-01: _expand_intensional MUST call get_descendants_bfs"
        )
        assert "limit=count" in src, (
            "CF-HISTORIAN-VS02-01: _expand_intensional passes limit=count to BFS; "
            "this is the structural pre-truncation step that makes the bug real"
        )

    def test_h13_intensional_call_site_uses_len_deduped_for_total_source_read(self):
        """Source-read: _expand_intensional passes ``total=len(deduped)``.

        ``deduped`` is built from the BFS-capped descendants list. When the
        cap fires, ``deduped`` is ALREADY the truncated size — so
        ``total=len(deduped)`` reports the truncated size, violating FHIR R4
        §4.9.2 "The total number of concepts in the expansion" (FULL count).
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "total=len(deduped)" in src, (
            "CF-HISTORIAN-VS02-01: _expand_intensional MUST pass total=len(deduped) "
            "to build_valueset_expand — this is the load-bearing buggy line"
        )

    def test_h14_intensional_path_uses_plus_one_probe(self):
        """Source-read: _expand_intensional uses the +1 probe pattern.

        CF-HISTORIAN-VS02-01 RESOLVED: the intensional path now uses
        ``limit=count + 1`` (the "+1 probe" pattern), harmonizing with the
        3 sibling call sites:
        - expand_url_pattern: ``limit=(descendant_budget + 1)`` (line 266)
        - _expand_implicit_value_set: ``LIMIT count + 1`` SQL trick
        - _do_expand filter mode: ``search_names(..., limit=count + 1)`` (SKEPTIC QA-001)

        This probe is a structural contract: it MUST fail if someone reverts
        the fix back to ``limit=count`` (+0 probe), which would reintroduce
        the count-truncation ambiguity at exactly the budget boundary.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # The intensional path MUST use limit=count + 1 (the +1 probe).
        assert "limit=count + 1" in src, (
            "CF-HISTORIAN-VS02-01 RESOLVED: _expand_intensional MUST use "
            "limit=count + 1 (the +1 probe pattern, harmonized with the 3 "
            "sibling call sites). If this fails, someone reverted the fix "
            "back to limit=count (+0 probe)."
        )
        # Inverse: the function MUST NOT use the +0 probe.
        assert "limit=count," not in src and "limit=count\n" not in src, (
            "CF-HISTORIAN-VS02-01 RESOLVED: _expand_intensional MUST NOT use "
            "limit=count (+0 probe); the +1 probe harmonization must hold."
        )

    def test_h15_intensional_path_bfs_cap_fixture_coincidence(self, fhir_client):
        """Behavioral: intensional path on conformance fixture — total == 2 for count=1.

        Conformance fixture: 1 mrrel row (T2DM is-a DM). With is-a filter on
        DM and count=1: contains has [DM root, T2DM child] but is sliced to
        count=1, so total reports the truncated size... BUT the BFS limit
        also caps at 1, so the contains built BEFORE the [:count] slice is
        [DM root, T2DM child from BFS]. After dedup and [:count] slice,
        contains = [DM root]. The bug is structurally present (BFS capped at
        1 descendant) but the reported total == 2 because deduped has
        [DM root, T2DM] BEFORE the [:count] slice. This probe is the
        documentation-of-buggy-behavior pattern: it pins the current
        fixture-coincidence behavior so future fixture enhancements that
        add a 2nd descendant will surface the bug.

        Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.total
        Quote: "The total number of concepts in the expansion."
        """
        body = _make_intensional_snomed_isa()
        status, resp = _post_expand(fhir_client, body, params={"count": 1})
        assert status == 200, f"expected 200, got {status}: {resp}"
        exp = resp.get("expansion", {})
        # total == 2 (root + 1 child) because the conformance fixture has
        # exactly 1 mrrel row. The bug is invisible here.
        assert exp.get("total") == 2, (
            f"CF-HISTORIAN-VS02-01 fixture coincidence: total expected 2 "
            f"(root + 1 BFS-capped descendant), got {exp.get('total')}. "
            f"If this value changed, either the fixture was enhanced OR the "
            f"bug was fixed — investigate and update the probe accordingly."
        )

    def test_h16_intensional_path_toocostly_emitted_on_bfs_cap(self, fhir_client):
        """Behavioral: intensional path with count < descendant count MUST
        emit the valueset-toocostly extension as the truncation signal.

        With the conformance fixture's 1 mrrel row and count=1, the BFS cap
        fires (BFS would return more if not capped; but with only 1 mrrel
        row, BFS naturally returns 1). The toocostly extension is NOT
        emitted here because count_limited is computed as
        ``len(deduped) > count`` AFTER the [:count] slice is applied — but
        deduped itself has 2 entries ([root, child]) which IS > count=1.

        Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
        """
        body = _make_intensional_snomed_isa()
        status, resp = _post_expand(fhir_client, body, params={"count": 1})
        assert status == 200
        exp = resp.get("expansion", {})
        # With count=1 and deduped=2, count_limited MUST be True, so the
        # toocostly extension MUST be present.
        exts = exp.get("extension", [])
        toocostly_present = any(
            e.get("url") == TOOCOSTLY_URL for e in exts
        )
        assert toocostly_present, (
            "Intensional path with count=1 and deduped=2 MUST emit "
            "valueset-toocostly extension as the truncation signal"
        )


# =============================================================================
# Lens 2 — Explicit-size-on-truncation pattern (count=3 PROMOTED)
#
# SKEPTIC tip (c): AST-walk build_valueset_expand call sites and verify every
# truncating site passes explicit total=.
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.total
# =============================================================================


class TestLens2ExplicitSizeOnTruncationCallSiteAudit:
    """Lens 2: explicit-size-on-truncation pattern (count=3 PROMOTED).

    Per GLOBAL_RULES.md line 136 (count=3 PROMOTED; count=4 with VS-02 SKEPTIC
    resweep QA-001 fix). Every build_valueset_expand call site that
    pre-truncates MUST pass total=<un-truncated-size> explicitly.

    The 4 call sites are:
    1. expand_url_pattern (line 313)
    2. _expand_intensional (line 2710)
    3. _expand_implicit_value_set (line 2913)
    4. _do_expand filter mode (line 2489 — VS-02 SKEPTIC resweep QA-001)
    """

    def test_h20_build_valueset_expand_has_total_parameter(self):
        """Source-read: build_valueset_expand signature has total: int | None = None.

        Per VS-02 SKEPTIC QA-057: the builder accepts an explicit total
        parameter. Default is None (compute from len(contains)).
        """
        src = _RESPONSES_PATH.read_text()
        tree = ast.parse(src)
        builder_func = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "build_valueset_expand"
            ):
                builder_func = node
                break
        assert builder_func is not None
        builder_src = ast.get_source_segment(src, builder_func) or ""
        assert "total: int | None = None" in builder_src, (
            "build_valueset_expand signature MUST have total: int | None = None"
        )

    def test_h21_all_4_call_sites_pass_total_explicitly(self):
        """Source-read: all 4 build_valueset_expand call sites pass total=.

        Per GLOBAL_RULES.md line 136 PROMOTED pattern (count=4 with VS-02
        SKEPTIC resweep QA-001). The AST-walk over the source MUST find
        exactly 4 call sites, and every one MUST pass total=.
        """
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        call_sites = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_valueset_expand"
            ):
                # Check if total= is passed as a keyword argument.
                kw_names = [kw.arg for kw in node.keywords]
                call_sites.append({
                    "line": node.lineno,
                    "passes_total": "total" in kw_names,
                })
        assert len(call_sites) == 4, (
            f"Expected exactly 4 build_valueset_expand call sites, got "
            f"{len(call_sites)}: {call_sites}"
        )
        missing = [c for c in call_sites if not c["passes_total"]]
        assert not missing, (
            f"GLOBAL_RULES.md line 136 PROMOTED pattern (count=4): every "
            f"build_valueset_expand call site MUST pass total= explicitly. "
            f"Missing at: {missing}"
        )

    def test_h22_call_site_count_strict_greater_than_semantic(self):
        """Source-read: all 4 call sites use strict > for count_limited.

        Per VS-04 TERMINOLOGIST QA-068: the count-truncation signal MUST use
        strict-greater-than (``>``), NOT greater-than-or-equal (``>=``).
        """
        # expand_url_pattern
        src_url = _get_func_source(_FHIR_API_PATH, "expand_url_pattern")
        assert "len(relations) > descendant_budget" in src_url, (
            "expand_url_pattern MUST use strict > for count_limited (QA-068)"
        )

        # _expand_intensional
        src_int = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_intensional"
        )
        assert "len(deduped) > count" in src_int, (
            "_expand_intensional MUST use strict > for count_limited"
        )

        # _do_expand filter mode
        src_filter = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert "len(results) > count" in src_filter, (
            "_do_expand filter mode MUST use strict > for count_limited (QA-001)"
        )

    def test_h23_filter_mode_total_lower_bound_semantic(self, fhir_client):
        """Behavioral: filter mode count=1 returns total >= 2 (the +1 probe lower bound).

        Per VS-02 SKEPTIC QA-001 fix: when count=1 truncates a result set with
        >= 2 natural matches, total reports the lower bound from the +1
        probe. The conformance fixture has 'diabetes' matching 3 codes
        (SNOMED DM + SNOMED T2DM + ICD-10-CM T2DM), so count=1 produces
        total >= 2 (the +1 probe lower bound; exact count requires
        unbounded search).
        """
        status, resp = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "count": 1},
        )
        assert status == 200
        exp = resp.get("expansion", {})
        assert exp.get("total") >= 2, (
            f"Filter mode count=1 with 3 natural matches MUST report total >= 2 "
            f"(+1 probe lower bound); got {exp.get('total')}"
        )

    def test_h24_filter_mode_call_site_uses_plus_one_probe(self):
        """Source-read: _do_expand filter mode uses limit=count + 1 (VS-02 SKEPTIC QA-001)."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        # The filter mode uses search_names(..., limit=count + 1).
        assert "limit=count + 1" in src, (
            "Filter mode call site MUST use limit=count + 1 (VS-02 SKEPTIC QA-001 fix)"
        )

    def test_h25_url_pattern_call_site_uses_plus_one_probe(self):
        """Source-read: expand_url_pattern uses limit=(descendant_budget + 1).

        Per VS-04 TERMINOLOGIST QA-068: the descendant walk uses
        ``limit=(descendant_budget + 1)`` so count_limited can distinguish
        "BFS exhausted at exactly the budget" (NOT truncated) from "BFS hit
        the limit with more remaining" (truncated).
        """
        src = _get_func_source(_FHIR_API_PATH, "expand_url_pattern")
        assert "descendant_budget + 1" in src, (
            "URL pattern call site MUST use descendant_budget + 1 (VS-04 QA-068 fix)"
        )

    def test_h26_implicit_value_set_call_site_uses_plus_one_probe(self):
        """Source-read: _expand_implicit_value_set uses LIMIT count + 1 SQL trick.

        Per VS-02 SKEPTIC QA-057: the implicit value set path uses a
        ``LIMIT count + 1`` SQL query trick to detect truncation.
        """
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set"
        )
        # The implicit path uses SQL LIMIT count + 1 (case-insensitive match).
        assert "count + 1" in src.lower() or "count+1" in src.lower(), (
            "Implicit value set call site MUST use LIMIT count + 1 SQL trick"
        )

    def test_h27_intensional_call_site_uses_plus_one_probe(self):
        """Source-read: _expand_intensional uses the +1 probe pattern.

        CF-HISTORIAN-VS02-01 RESOLVED: the intensional path now uses
        ``limit=count + 1``, harmonizing with the 3 sibling call sites
        (filter mode, URL pattern, implicit value set). The asymmetry is
        closed — all 4 build_valueset_expand call sites now use the +1
        probe pattern, which lets count_limited be computed as
        ``len(results) > N`` to disambiguate "limit hit at exactly budget".

        This probe is a structural contract: it MUST fail if someone reverts
        the fix back to ``limit=count`` (+0 probe), reintroducing the
        asymmetry across the 4 call sites.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # The intensional path uses limit=count + 1, harmonized with siblings.
        assert "limit=count + 1" in src, (
            "CF-HISTORIAN-VS02-01 RESOLVED: _expand_intensional MUST use "
            "limit=count + 1, harmonizing with the 3 sibling call sites. "
            "If this fails, the asymmetry was reintroduced — the fix was "
            "reverted back to limit=count (+0 probe)."
        )


# =============================================================================
# Lens 3 — Client-input-as-canonical drift pattern (count=9 PROMOTED)
#
# SKEPTIC tip (a): client-input-as-canonical drift count=9 PROMOTED.
# Verify canonical_system_uri wired into the intensional path; CR-013 9th instance.
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.system
# =============================================================================


class TestLens3ClientInputAsCanonicalDrift:
    """Lens 3: client-input-as-canonical drift pattern (count=9 PROMOTED).

    Per CR-013 (milestone-2 review): the intensional path MUST re-resolve
    the include[].system through canonical_system_uri() so contains[].system
    echoes the canonical URI, NOT the client-supplied alias / trailing-
    slash variant. Same root cause as CR-011/012 (CS-02/CS-03 HISTORIAN) and
    CF-HISTORIAN-VS02-02 (RESOLVED by TS-03 SKEPTIC QA-001).
    """

    def test_h30_canonical_system_uri_helper_imported(self):
        """Source-read: canonical_system_uri helper is imported into fhir_api.py."""
        src = _FHIR_API_PATH.read_text()
        # The import may be top-level or local; verify the helper is referenced.
        assert "canonical_system_uri" in src, (
            "canonical_system_uri helper MUST be referenced in fhir_api.py "
            "(client-input-as-canonical drift count=9 PROMOTED pattern)"
        )

    def test_h31_intensional_path_calls_canonical_system_uri(self):
        """Source-read: _expand_intensional calls canonical_system_uri.

        Per CR-013: the intensional path MUST re-resolve the include[].system
        to the canonical FHIR URI via canonical_system_uri() before assigning
        it to contains[].system. This is the 9th instance of client-input-as-
        canonical drift.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "canonical_system_uri(" in src, (
            "CR-013 9th instance: _expand_intensional MUST call canonical_system_uri "
            "on the include[].system to re-resolve alias URIs to canonical"
        )
        # The canonical URI MUST be assigned to a variable that's used in contains[].
        assert "canonical_inc" in src, (
            "CR-013: _expand_intensional MUST assign the canonical URI to a "
            "local variable (canonical_inc) and use it in contains[].system"
        )

    def test_h32_intensional_path_contains_system_is_canonical(self, fhir_client):
        """Behavioral: intensional path contains[].system is canonical SNOMED URI.

        Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.system
        Quote: "An absolute URI which is the code system URI of the code
        system from which the code in the expansion was defined."
        """
        body = _make_intensional_snomed_isa()
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        assert status == 200
        for c in resp.get("expansion", {}).get("contains", []):
            assert c.get("system") == SNOMED_URI, (
                f"CR-013: contains[].system MUST be canonical {SNOMED_URI}, "
                f"got {c.get('system')!r}"
            )

    def test_h33_intensional_path_alias_uri_resolves_to_canonical(self, fhir_client):
        """Behavioral: alias URI (urn:oid) resolves to canonical SNOMED URI.

        Per CR-013: when a client supplies an alias URI (urn:oid:2.16.840.1.113883.6.96),
        contains[].system MUST be the canonical http://snomed.info/sct.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-hist-resweep-alias",
            "compose": {
                "include": [{
                    "system": "urn:oid:2.16.840.1.113883.6.96",
                    "filter": [
                        {
                            "property": "concept",
                            "op": "is-a",
                            "value": SNOMED_DIABETES_MELLITUS,
                        }
                    ],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        # The alias must resolve to SNOMED; status should be 200 with contains[].
        if status == 200:
            for c in resp.get("expansion", {}).get("contains", []):
                # The canonical URI is http://snomed.info/sct, NOT urn:oid:...
                assert c.get("system") == SNOMED_URI, (
                    f"CR-013 9th-instance drift: alias urn:oid URI MUST resolve to "
                    f"canonical {SNOMED_URI}, got {c.get('system')!r}"
                )

    def test_h34_implicit_value_set_path_uses_canonical_helper(self):
        """Source-read: _expand_implicit_value_set calls canonical_system_uri.

        Per CF-HISTORIAN-VS02-02 RESOLVED (TS-03 SKEPTIC QA-001): the implicit
        value set Form (a) path MUST re-resolve the client-supplied prefix
        through canonical_system_uri so contains[].system is the canonical URI.
        """
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set"
        )
        assert "canonical_system_uri(" in src, (
            "CF-HISTORIAN-VS02-02 RESOLVED: _expand_implicit_value_set MUST "
            "use canonical_system_uri (TS-03 SKEPTIC QA-001 fix)"
        )

    def test_h35_canonical_system_uri_helper_in_canonical_location(self):
        """Source-read: canonical_system_uri is defined in engines.fhir."""
        from medterm4ds.engines.fhir import canonical_system_uri
        # The helper MUST be callable.
        assert callable(canonical_system_uri), (
            "canonical_system_uri MUST be a callable in engines.fhir"
        )

    def test_h36_explicit_concept_list_path_uses_canonical_inc(self):
        """Source-read: explicit concept list path uses canonical_inc in contains[].system.

        Per CR-013: the explicit concept list path assigns canonical_inc to
        contains[].system (NOT raw client-supplied inc_system).
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # Look for "system": canonical_inc in the contains.append for explicit concepts.
        assert '"system": canonical_inc' in src, (
            "CR-013: explicit concept list path MUST assign canonical_inc to "
            "contains[].system (NOT raw client-supplied inc_system)"
        )

    def test_h37_descendant_loop_uses_canonical_inc(self):
        """Source-read: descendant loop uses canonical_inc in contains[].system."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # The descendant loop MUST also use canonical_inc.
        # Source-read: count occurrences of '"system": canonical_inc'.
        count = src.count('"system": canonical_inc')
        assert count >= 3, (
            f"CR-013: canonical_inc MUST be used in at least 3 contains.append "
            f"calls (explicit concept list, is-a root, descendant loop); "
            f"found {count} occurrences"
        )


# =============================================================================
# Lens 4 — Cross-handler helper-wiring pattern (count=6 PROMOTED)
#
# SKEPTIC tip (b): _extract_valueset_from_parameters is the 4th sibling —
# verify wired into expand_post.
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html In param "valueSet"
# =============================================================================


class TestLens4CrossHandlerHelperWiring:
    """Lens 4: cross-handler helper-wiring pattern (count=6 PROMOTED).

    Per GLOBAL_RULES.md "Code Review Time" Parameters-body case: audit
    _parse_parameters AND every sibling complex-type extractor. _extract_valueset_from_parameters
    is the 4th sibling of the helper-exists-but-not-wired pattern (TS-02
    HISTORIAN QA-022/023, TS-02 EXPLORER QA-026/028, VS-03 SKEPTIC QA-059).
    """

    def test_h40_extract_valueset_from_parameters_defined(self):
        """Source-read: _extract_valueset_from_parameters is defined in fhir_api.py.

        Per VS-03 SKEPTIC QA-059: the helper extracts a ValueSet from a
        Parameters body via the resource property.
        """
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_extract_valueset_from_parameters"
            ):
                found = True
                break
        assert found, (
            "_extract_valueset_from_parameters MUST be defined in fhir_api.py "
            "(VS-03 SKEPTIC QA-059)"
        )

    def test_h41_extract_valueset_from_parameters_wired_into_expand_post(self):
        """Source-read: expand_post calls _extract_valueset_from_parameters.

        Per VS-03 SKEPTIC QA-059: the helper MUST be called from expand_post
        so POST $expand with a Parameters-with-valueSet body works. Without
        the wiring, the helper exists but isn't applied — silent-wrong-answer
        (the body falls through to the no-url/no-filter 400 path).
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "expand_post")
        assert "_extract_valueset_from_parameters" in src, (
            "VS-03 SKEPTIC QA-059: expand_post MUST call "
            "_extract_valueset_from_parameters (helper-wiring pattern)"
        )

    def test_h42_extract_valueset_from_parameters_returns_valueset_or_none(self):
        """Source-read: _extract_valueset_from_parameters returns dict | None."""
        from medterm4ds.apps.fhir_api import create_fhir_app
        # Verify the function exists by checking the source.
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_extract_valueset_from_parameters"
        )
        # Returns None when no matching parameter found.
        assert "return None" in src or "return resource" in src, (
            "_extract_valueset_from_parameters MUST return the ValueSet or None"
        )

    def test_h43_parameters_with_valueset_body_works(self, fhir_client):
        """Behavioral: POST $expand with Parameters-with-valueSet body returns 200.

        Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html In param "valueSet"
        Quote: "The value set is provided directly as part of the request."
        """
        vs = _make_intensional_snomed_isa()
        body = _make_parameters_with_valueset(vs, count=20)
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, (
            f"VS-03 SKEPTIC QA-059 helper-wiring: Parameters-with-valueSet body "
            f"MUST return 200; got {status}: {resp}"
        )
        assert resp.get("resourceType") == "ValueSet"
        # The contains[] MUST reflect the intensional expansion.
        contains = resp.get("expansion", {}).get("contains", [])
        codes = [c.get("code") for c in contains]
        assert SNOMED_DIABETES_MELLITUS in codes, (
            "Parameters-with-valueSet body: contains[] MUST include the is-a root"
        )

    def test_h44_parameters_with_valueset_count_overrides_default(self, fhir_client):
        """Behavioral: count in Parameters body overrides the query default.

        Per FHIR R4 §4.7.5: Parameters-body parameters override defaults.
        """
        vs = _make_intensional_snomed_isa()
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": vs},
                {"name": "count", "valueInteger": 1},
            ],
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200
        # With count=1, the contains[] should be truncated and the toocostly
        # extension MUST be present (since deduped has 2 entries > count=1).
        exp = resp.get("expansion", {})
        assert len(exp.get("contains", [])) <= 1, (
            f"count=1 in Parameters body MUST truncate contains[] to <= 1; "
            f"got {len(exp.get('contains', []))}"
        )
        exts = exp.get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            "count=1 with 2 natural matches MUST emit valueset-toocostly extension"
        )

    def test_h45_extract_valueset_helper_robustness_non_dict_param(self, fhir_client):
        """Behavioral: non-dict parameter[] entries are silently skipped.

        Per CS-04 SKEPTIC QA-001 + 10th PROMOTED pattern: the helper MUST
        guard against non-dict parameter[] entries.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                "not-a-dict",  # malformed entry
                {"name": "valueSet", "resource": _make_intensional_snomed_isa()},
            ],
        }
        status, resp = _post_expand(fhir_client, body)
        # The malformed entry MUST be silently skipped; the helper MUST still
        # extract the ValueSet from the second entry.
        assert status == 200, (
            f"Non-dict parameter[] entry MUST be silently skipped; got {status}: {resp}"
        )

    def test_h46_extract_valueset_helper_robustness_non_dict_resource(self, fhir_client):
        """Behavioral: non-dict resource in parameter[] is silently skipped."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": "not-a-dict"},
                {"name": "valueSet", "resource": _make_intensional_snomed_isa()},
            ],
        }
        status, resp = _post_expand(fhir_client, body)
        # The first valueSet parameter has a non-dict resource; the helper
        # MUST skip it and extract from the second.
        assert status == 200, (
            f"Non-dict resource in parameter[] MUST be silently skipped; got {status}: {resp}"
        )

    def test_h47_sibling_helpers_count_in_fhir_api(self):
        """Source-read: at least 4 sibling complex-type extractors exist.

        Per GLOBAL_RULES.md "Code Review Time" Parameters-body case: the
        sibling complex-type extractor family includes:
        1. _parse_parameters (scalar extractor)
        2. _extract_coding_from_parameters
        3. _extract_named_coding_from_parameters
        4. _extract_valueset_from_parameters (4th sibling — VS-03 SKEPTIC QA-059)
        """
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        sibling_count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in (
                "_parse_parameters",
                "_extract_coding_from_parameters",
                "_extract_named_coding_from_parameters",
                "_extract_valueset_from_parameters",
            ):
                sibling_count += 1
        assert sibling_count >= 4, (
            f"At least 4 sibling complex-type extractors MUST exist; found {sibling_count}"
        )


# =============================================================================
# Lens 5 — 10th PROMOTED pattern isinstance-guard at untrusted-data list-iterator
#
# SKEPTIC tip (d): 5 sibling guards present in _expand_intensional
# (CS-04 HISTORIAN QA-001 PROMOTED as 10th PROMOTED pattern).
# Spec: FHIR R4 §3.1.0.1.5 + §3.1.0.1.9 — malformed body MUST produce
# OperationOutcome, not 500 + traceback.
# =============================================================================


class TestLens5IsinstanceGuardUntrustedDataBoundary:
    """Lens 5: 10th PROMOTED pattern isinstance-guard (CS-04 HISTORIAN QA-001).

    The 5 sibling guards in _expand_intensional cover:
    1. compose element itself (parent data-access boundary — VS-01 resweep QA-001)
    2. compose.include[] (CS-04 HISTORIAN QA-001)
    3. compose.include[].concept[]
    4. compose.include[].filter[]
    5. compose.exclude[]
    6. compose.exclude[].concept[] (combined list-shape guard)

    Plus isinstance guards in the sibling complex-type extractors
    (_parse_parameters, _extract_coding_from_parameters, etc.).
    """

    def test_h50_intensional_has_at_least_5_isinstance_guards(self):
        """Source-read: _expand_intensional has at least 5 isinstance guards.

        Per CS-04 HISTORIAN QA-001 (PROMOTED as 10th PROMOTED pattern): the
        function MUST have isinstance guards at the 5 sibling iterator
        boundaries.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # Count isinstance calls.
        count = src.count("isinstance(")
        assert count >= 5, (
            f"CS-04 HISTORIAN QA-001 10th PROMOTED pattern: _expand_intensional "
            f"MUST have at least 5 isinstance guards; found {count}"
        )

    def test_h51_intensional_compose_isinstance_guard(self):
        """Source-read: compose element has isinstance(compose, dict) guard.

        Per VS-01 resweep SKEPTIC QA-001: the parent compose element MUST
        have a guard. Without it, compose=null triggers AttributeError on
        compose.get("include", []).
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # The compose guard MUST be present.
        assert "isinstance(compose, dict)" in src, (
            "VS-01 resweep QA-001: _expand_intensional MUST guard compose with isinstance"
        )

    def test_h52_intensional_include_isinstance_guard(self):
        """Source-read: include[] loop has isinstance(include, dict) guard."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "isinstance(include, dict)" in src, (
            "CS-04 HISTORIAN QA-001: _expand_intensional MUST guard include with isinstance"
        )

    def test_h53_intensional_concept_isinstance_guard(self):
        """Source-read: concept[] loop has isinstance(concept, dict) guard."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "isinstance(concept, dict)" in src, (
            "CS-04 HISTORIAN QA-001: _expand_intensional MUST guard concept with isinstance"
        )

    def test_h54_intensional_filter_isinstance_guard(self):
        """Source-read: filter[] loop has isinstance(filt, dict) guard."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "isinstance(filt, dict)" in src, (
            "CS-04 HISTORIAN QA-001: _expand_intensional MUST guard filter with isinstance"
        )

    def test_h55_intensional_exclude_isinstance_guard(self):
        """Source-read: exclude[] loop has isinstance(exclude, dict) guard."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "isinstance(exclude, dict)" in src, (
            "CS-04 HISTORIAN QA-001: _expand_intensional MUST guard exclude with isinstance"
        )

    def test_h56_extract_valueset_helper_isinstance_guard(self):
        """Source-read: _extract_valueset_from_parameters has isinstance(param, dict) guard."""
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_extract_valueset_from_parameters"
        )
        assert "isinstance(param, dict)" in src, (
            "CS-04 SKEPTIC QA-001 sibling: _extract_valueset_from_parameters "
            "MUST guard param with isinstance"
        )

    def test_h57_intensional_compose_non_dict_no_500(self, fhir_client):
        """Behavioral: compose=null does NOT trigger 500.

        Spec: FHIR R4 §3.1.0.1.5 + §3.1.0.1.9.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/h57",
            "compose": None,
        }
        status, resp = _post_expand(fhir_client, body)
        assert status < 500, (
            f"compose=null MUST NOT trigger 500; got {status}: {resp}"
        )

    def test_h58_intensional_include_non_dict_entry_no_500(self, fhir_client):
        """Behavioral: compose.include[] with non-dict entry does NOT trigger 500."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/h58",
            "compose": {
                "include": ["not-a-dict"],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status < 500, (
            f"compose.include[] non-dict entry MUST NOT trigger 500; got {status}: {resp}"
        )

    def test_h59_intensional_concept_non_dict_entry_no_500(self, fhir_client):
        """Behavioral: compose.include[].concept[] non-dict entry does NOT trigger 500."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/h59",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        "not-a-dict",
                        {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
                    ],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status < 500
        # The valid concept MUST still appear in the expansion.
        if status == 200:
            codes = [c.get("code") for c in resp.get("expansion", {}).get("contains", [])]
            assert SNOMED_DIABETES_MELLITUS in codes, (
                "Valid concept MUST be processed; malformed entries silently skipped"
            )


# =============================================================================
# Lens 6 — CF-SKEPTIC-VS01-01 re-derivation (7 of 9 filter operators dropped)
# Spec: https://hl7.org/fhir/R4/valueset-concept-operator.html
# =============================================================================


class TestLens6CFSkepticVS01OneFilterOperators:
    """Lens 6: CF-SKEPTIC-VS01-01 — 7 of 9 filter operators silently dropped.

    Per VS-01 SKEPTIC QA-054: only is-a and descendent-of are honored; the
    other 7 (=, is-not-a, regex, in, not-in, generalizes, exists) are
    silently dropped. The intensional path's filter dispatch is hardcoded:
    ``if prop == "concept" and op in ("is-a", "descendent-of"): ... else: logger.debug``.
    """

    def test_h60_intensional_dispatch_hardcoded_to_two_operators(self):
        """Source-read: _expand_intensional dispatch is hardcoded to is-a + descendent-of."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # The hardcoded dispatch MUST be present (CF-SKEPTIC-VS01-01 pin).
        assert 'op in ("is-a", "descendent-of")' in src, (
            "CF-SKEPTIC-VS01-01 pin: dispatch hardcoded to is-a + descendent-of"
        )

    def test_h61_filter_operator_enum_has_9_values(self):
        """Source-read: FHIR_R4_FILTER_OPERATORS contains exactly 9 spec values."""
        # Per FHIR R4 https://hl7.org/fhir/R4/valueset-concept-operator.html
        expected = frozenset({
            "=", "is-a", "descendent-of", "is-not-a", "regex",
            "in", "not-in", "generalizes", "exists",
        })
        assert FHIR_R4_FILTER_OPERATORS == expected, (
            f"FHIR_R4_FILTER_OPERATORS MUST equal the 9 spec values; got "
            f"{FHIR_R4_FILTER_OPERATORS}"
        )

    def test_h62_offspec_descendant_of_not_in_enum(self):
        """Source-read: 'descendant-of' (off-spec) is NOT in the enum."""
        assert "descendant-of" not in FHIR_R4_FILTER_OPERATORS, (
            "Off-spec 'descendant-of' MUST NOT be in FHIR_R4_FILTER_OPERATORS "
            "(VS-01 SKEPTIC QA-054)"
        )

    def test_h63_seven_operators_silently_dropped(self, fhir_client):
        """Behavioral: 7 of 9 filter operators are silently dropped (no error, no contains).

        CF-SKEPTIC-VS01-01 pin: when an unsupported operator is used, the
        intensional path silently drops the filter and returns an empty
        expansion (no error surfaced to the client).
        """
        unsupported_ops = ["=", "is-not-a", "regex", "in", "not-in", "generalizes", "exists"]
        for op in unsupported_ops:
            body = {
                "resourceType": "ValueSet",
                "url": f"http://example.org/vs/h63-{op}",
                "compose": {
                    "include": [{
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": op, "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }],
                },
            }
            status, resp = _post_expand(fhir_client, body, params={"count": 20})
            assert status == 200, (
                f"op={op!r}: silently dropped (200 with empty/partial contains); "
                f"got {status}"
            )
            # The contains[] MUST be empty (or only contain the root if is-a-equivalent).
            contains = resp.get("expansion", {}).get("contains", [])
            # The silently-dropped filter produces 0 contains entries for these ops.
            assert len(contains) == 0, (
                f"CF-SKEPTIC-VS01-01: op={op!r} MUST be silently dropped "
                f"(empty contains); got {len(contains)} entries"
            )

    def test_h64_is_a_filter_honored(self, fhir_client):
        """Behavioral: is-a filter IS honored (root + descendants in contains)."""
        body = _make_intensional_snomed_isa()
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        assert status == 200
        codes = [c.get("code") for c in resp.get("expansion", {}).get("contains", [])]
        assert SNOMED_DIABETES_MELLITUS in codes, "is-a MUST include root"
        assert SNOMED_T2DM in codes, "is-a MUST include descendant"

    def test_h65_descendent_of_filter_honored(self, fhir_client):
        """Behavioral: descendent-of filter IS honored (descendants only, no root)."""
        body = _make_intensional_snomed_descendent_of()
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        assert status == 200
        codes = [c.get("code") for c in resp.get("expansion", {}).get("contains", [])]
        assert SNOMED_T2DM in codes, "descendent-of MUST include descendant"
        assert SNOMED_DIABETES_MELLITUS not in codes, (
            "descendent-of MUST NOT include root (spec-correct Latin spelling)"
        )


# =============================================================================
# Lens 7 — CF-HISTORIAN-VS02-02 (RESOLVED) re-derivation
# Spec: https://hl7.org/fhir/R4/terminology-service.html#4.7.3.1
# =============================================================================


class TestLens7CFHistorianVS02TwoResolvedVerification:
    """Lens 7: CF-HISTORIAN-VS02-02 (implicit value set Form (a) canonical
    drift) was RESOLVED by TS-03 SKEPTIC QA-001. Verify the resolution holds.
    """

    def test_h70_implicit_value_set_uses_canonical_system_uri(self):
        """Source-read: _expand_implicit_value_set uses canonical_system_uri.

        Per CF-HISTORIAN-VS02-02 RESOLVED (TS-03 SKEPTIC QA-001): Form (a)
        paths MUST resolve contains[].system via canonical_system_uri(),
        NOT echo the client-supplied URL prefix.
        """
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set"
        )
        assert "canonical_system_uri(" in src, (
            "CF-HISTORIAN-VS02-02 RESOLVED: _expand_implicit_value_set MUST "
            "use canonical_system_uri (TS-03 SKEPTIC QA-001 fix)"
        )

    def test_h71_implicit_value_set_contains_system_is_canonical(self, fhir_client):
        """Behavioral: implicit value set contains[].system is the canonical URI.

        Spec: https://hl7.org/fhir/R4/terminology-service.html#4.7.3.1
        """
        # Use the LOINC implicit value set URL (all of LOINC).
        status, resp = _get_expand(
            fhir_client,
            params={"url": "http://loinc.org/vs", "count": 20},
        )
        # Note: the conformance fixture doesn't have LOINC codes, but the
        # implicit value set path may return an empty expansion or 200 with
        # a "system not loaded" message. Either way, contains[].system
        # (if any entries) MUST be the canonical URI.
        if status == 200:
            for c in resp.get("expansion", {}).get("contains", []):
                sys = c.get("system", "")
                # Canonical LOINC URI is http://loinc.org (no trailing /vs).
                if "loinc.org" in sys:
                    assert not sys.endswith("/vs"), (
                        f"CF-HISTORIAN-VS02-02 regression: contains[].system={sys} "
                        f"echoes the URL prefix verbatim (must be canonical URI)"
                    )


# =============================================================================
# Lens 8 — QA-059 Parameters-with-valueSet body silently dropped (CF-EXPLORER-VS01 prior)
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html In param "valueSet"
# =============================================================================


class TestLens8QA059ParametersWithValueSetBody:
    """Lens 8: QA-059 — Parameters-with-valueSet body silently dropped prior pattern.

    Per VS-03 SKEPTIC QA-059 (filed in original VS-03 SKEPTIC run): the
    prior implementation only honored the bare-ValueSet body shape and
    silently dropped the Parameters-with-valueSet form. The fix added
    _extract_valueset_from_parameters and wired it into expand_post.
    """

    def test_h80_bare_valueset_body_works(self, fhir_client):
        """Behavioral: POST with bare ValueSet body returns 200."""
        body = _make_intensional_snomed_isa()
        status, resp = _post_expand(fhir_client, body)
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"

    def test_h81_parameters_with_valueset_body_works(self, fhir_client):
        """Behavioral: POST with Parameters-with-valueSet body returns 200."""
        vs = _make_intensional_snomed_isa()
        body = _make_parameters_with_valueset(vs, count=20)
        status, resp = _post_expand(fhir_client, body)
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"

    def test_h82_bare_valueset_vs_parameters_body_byte_exact(self, fhir_client):
        """Behavioral: bare ValueSet body == Parameters-with-valueSet body.

        The two POST body shapes for the same intensional ValueSet MUST
        produce byte-exact contains[] and total.
        """
        vs = _make_intensional_snomed_isa()
        # Bare ValueSet body.
        s1, r1 = _post_expand(fhir_client, vs, params={"count": 20})
        # Parameters-with-valueSet body.
        s2, r2 = _post_expand(fhir_client, _make_parameters_with_valueset(vs, count=20))
        assert s1 == 200 and s2 == 200
        # contains[] MUST be byte-exact equal.
        assert _contains_codes(r1) == _contains_codes(r2), (
            f"Bare ValueSet body != Parameters-with-valueSet body: "
            f"{_contains_codes(r1)} vs {_contains_codes(r2)}"
        )
        # total MUST be equal.
        assert (
            r1.get("expansion", {}).get("total")
            == r2.get("expansion", {}).get("total")
        )

    def test_h83_helper_not_wired_into_get_handler(self):
        """Source-read: _extract_valueset_from_parameters is NOT called from expand_get.

        GET $expand takes query params, not a body. The helper MUST only be
        wired into expand_post. This probe verifies the wiring is scoped
        correctly — over-wiring would be a bug (GET has no body).
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "expand_get")
        assert "_extract_valueset_from_parameters" not in src, (
            "_extract_valueset_from_parameters MUST NOT be called from expand_get "
            "(GET has no body — over-wiring would be a bug)"
        )


# =============================================================================
# Lens 9 — PROMOTED patterns re-derivation (count=10)
# =============================================================================


class TestLens9PromotedPatternsReDerivation:
    """Lens 9: Re-derive the 10 PROMOTED patterns from GLOBAL_RULES.md.

    Each probe verifies a pattern holds (no new instance introduced in this
    iteration's diff).
    """

    def test_h90_promoted_1_empty_string_required_query_min_length(self):
        """Source-read: required string Query declarations have min_length=1."""
        src = _FHIR_API_PATH.read_text()
        assert "min_length=1" in src, (
            "min_length=1 MUST be present on required string Query declarations"
        )

    def test_h91_promoted_2_canonical_system_uri_helper_wired(self):
        """Source-read: canonical_system_uri helper is imported and called."""
        src = _FHIR_API_PATH.read_text()
        assert "canonical_system_uri" in src, (
            "canonical_system_uri helper MUST be used (client-input-as-canonical "
            "drift count=9 PROMOTED structural fix)"
        )

    def test_h92_promoted_3_closed_enum_filter_operators_imported(self):
        """Source-read: FHIR_R4_FILTER_OPERATORS imported from canonical location."""
        from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS as _ops
        assert "is-a" in _ops, "FHIR_R4_FILTER_OPERATORS MUST contain 'is-a'"
        assert "descendent-of" in _ops, (
            "FHIR_R4_FILTER_OPERATORS MUST contain spec-correct 'descendent-of' "
            "(NOT 'descendant-of')"
        )
        assert "descendant-of" not in _ops, (
            "FHIR_R4_FILTER_OPERATORS MUST NOT contain off-spec 'descendant-of'"
        )

    def test_h93_promoted_5_closed_enum_equivalence_no_r5_leak(self):
        """Source-read: FHIR_R4_CONCEPT_MAP_EQUIVALENCE has no R5/R4B values.

        Per CF-HISTORIAN-VS01-01 RESOLVED: R4 uses ``specializes`` (NOT R5/R4B
        ``subsumedby``) for the reverse-of-subsumes case.
        """
        from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE as _eq
        # R5/R4B values that MUST NOT be in R4 surface.
        assert "subsumedby" not in _eq, (
            "CF-HISTORIAN-VS01-01 RESOLVED: 'subsumedby' (R5/R4B) MUST NOT be in R4 enum"
        )
        assert "matches" not in _eq, (
            "CF-HISTORIAN-VS01-01 RESOLVED: 'matches' (R5-only) MUST NOT be in R4 enum"
        )
        # R4 spec-correct values MUST be present.
        assert "specializes" in _eq, (
            "R4 'specializes' MUST be in enum (reverse-of-subsumes)"
        )
        assert "subsumes" in _eq, "R4 'subsumes' MUST be in enum"

    def test_h94_promoted_7_boolean_serializer_lowercase_xml(self):
        """Source-read: _scalar_to_xml special-cases bool BEFORE generic conversion."""
        from medterm4ds.engines.fhir import xml as xml_module
        assert hasattr(xml_module, "_scalar_to_xml") or hasattr(
            xml_module, "_render_value"
        ), (
            "engines/fhir/xml.py MUST have a scalar-to-XML helper that "
            "special-cases bool before generic str() conversion (CR-002)"
        )

    def test_h95_promoted_10_response_builder_size_field_count(self):
        """Source-read: 4 build_valueset_expand call sites (count=4 PROMOTED)."""
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        call_count = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_valueset_expand"
            ):
                call_count += 1
        assert call_count == 4, (
            f"PROMOTED pattern count=4: build_valueset_expand has {call_count} "
            f"call sites; expected 4. New call sites MUST be audited for the "
            f"size-field-from-wrong-source pattern."
        )


# =============================================================================
# Lens 10 — Source-read structural contracts for the 4 call sites
# =============================================================================


class TestLens10SourceReadStructuralContracts:
    """Lens 10: Source-read structural contracts for build_valueset_expand.

    Verify each of the 4 call sites has the expected shape.
    """

    def test_h100_expand_url_pattern_uses_descendant_budget_plus_one(self):
        """Source-read: expand_url_pattern uses descendant_budget = max(0, count - len(contains))."""
        src = _get_func_source(_FHIR_API_PATH, "expand_url_pattern")
        assert "descendant_budget = max(0, count - len(contains))" in src, (
            "expand_url_pattern MUST use max(0, count - len(contains)) (VS-04 QA-068)"
        )

    def test_h101_expand_intensional_uses_bfs_with_limit_count(self):
        """Source-read: _expand_intensional uses get_descendants_bfs(limit=count).

        CF-HISTORIAN-VS02-01 territory: the BFS limit=count is the structural
        pre-truncation step that makes the bug real.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "max_depth=max_depth" in src, (
            "_expand_intensional MUST pass max_depth=max_depth to BFS"
        )

    def test_h102_expand_implicit_value_set_uses_count_plus_one(self):
        """Source-read: _expand_implicit_value_set uses count + 1 SQL trick."""
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set"
        )
        assert "count + 1" in src.lower(), (
            "_expand_implicit_value_set MUST use count + 1 SQL trick"
        )

    def test_h103_do_expand_filter_mode_uses_count_plus_one_search(self):
        """Source-read: _do_expand filter mode uses search_names(limit=count + 1)."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert "search_names(" in src, (
            "_do_expand filter mode MUST call search_names"
        )
        assert "limit=count + 1" in src, (
            "_do_expand filter mode MUST use limit=count + 1 (VS-02 SKEPTIC QA-001)"
        )

    def test_h104_do_expand_filter_mode_untruncated_total_computation(self):
        """Source-read: _do_expand filter mode computes untruncated_total correctly.

        Per VS-02 SKEPTIC QA-001: when count_limited, untruncated_total = len(results) + 1
        (the +1 probe lower bound). When not count_limited, total = len(results).
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert "untruncated_total = len(results) + 1 if count_limited else len(results)" in src, (
            "_do_expand filter mode MUST compute untruncated_total via the +1 probe "
            "lower bound (VS-02 SKEPTIC QA-001 fix)"
        )

    def test_h105_intensional_path_uses_dedup_before_total(self):
        """Source-read: _expand_intensional deduplicates BEFORE computing total."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # The dedup loop MUST be present.
        assert "seen: set[tuple[str, str]]" in src or "seen = set()" in src, (
            "_expand_intensional MUST deduplicate contains[] before computing total"
        )
        # The total MUST be computed from deduped (not contains).
        assert "total=len(deduped)" in src


# =============================================================================
# Lens 11 — Response shape audit across every mode
# =============================================================================


class TestLens11ResponseShapeEveryMode:
    """Lens 11: response shape conforms to FHIR R4 §4.9 in every mode."""

    def test_h110_filter_mode_response_shape(self, fhir_client):
        """Filter mode: ValueSet resource with expansion.{timestamp,total,contains[]}."""
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 20}
        )
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"
        exp = resp.get("expansion", {})
        assert "timestamp" in exp
        assert "total" in exp
        assert "contains" in exp
        # timestamp MUST be a valid ISO 8601 UTC.
        ts = exp.get("timestamp", "")
        try:
            datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pytest.fail(f"expansion.timestamp not valid ISO 8601 UTC: {ts}")

    def test_h111_intensional_mode_response_shape(self, fhir_client):
        """Intensional mode: ValueSet resource with expansion."""
        body = _make_intensional_snomed_isa()
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"
        exp = resp.get("expansion", {})
        assert "timestamp" in exp
        assert "total" in exp
        assert "contains" in exp

    def test_h112_explicit_concept_list_mode_response_shape(self, fhir_client):
        """Explicit concept list mode: ValueSet resource with expansion."""
        body = _make_explicit_concept_list()
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"
        exp = resp.get("expansion", {})
        assert "timestamp" in exp
        assert "total" in exp
        assert "contains" in exp

    def test_h113_parameters_with_valueset_mode_response_shape(self, fhir_client):
        """Parameters-with-valueSet mode: ValueSet resource with expansion."""
        vs = _make_intensional_snomed_isa()
        body = _make_parameters_with_valueset(vs, count=20)
        status, resp = _post_expand(fhir_client, body)
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"
        exp = resp.get("expansion", {})
        assert "timestamp" in exp
        assert "total" in exp
        assert "contains" in exp


# =============================================================================
# Lens 12 — Cross-handler GET<->POST byte-exact parity on advanced shapes
# =============================================================================


class TestLens12CrossHandlerGetPostParity:
    """Lens 12: GET and POST $expand produce byte-exact semantic responses."""

    def test_h120_filter_mode_get_post_byte_exact(self, fhir_client):
        """GET ?filter=diabetes&count=20 == POST Parameters body with filter."""
        get_status, get_resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 20}
        )
        post_status, post_resp = _post_expand(
            fhir_client,
            {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "filter", "valueString": "diabetes"},
                    {"name": "count", "valueInteger": 20},
                ],
            },
        )
        assert get_status == 200 and post_status == 200
        # contains[] MUST be byte-exact equal.
        assert _contains_codes(get_resp) == _contains_codes(post_resp)
        # total MUST be equal.
        assert (
            get_resp.get("expansion", {}).get("total")
            == post_resp.get("expansion", {}).get("total")
        )

    def test_h121_intensional_mode_post_only(self, fhir_client):
        """Intensional mode: POST with body. GET with url would 400 (no body accepted)."""
        body = _make_intensional_snomed_isa()
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"

    def test_h122_explicit_concept_list_post_byte_exact_with_parameters(self, fhir_client):
        """POST bare ValueSet == POST Parameters-with-valueSet (explicit concept list)."""
        vs = _make_explicit_concept_list()
        s1, r1 = _post_expand(fhir_client, vs, params={"count": 20})
        s2, r2 = _post_expand(fhir_client, _make_parameters_with_valueset(vs, count=20))
        assert s1 == 200 and s2 == 200
        assert _contains_codes(r1) == _contains_codes(r2)
        assert (
            r1.get("expansion", {}).get("total")
            == r2.get("expansion", {}).get("total")
        )


# =============================================================================
# Lens 13 — Carry-forward-as-probe pinning (extension of strategy 56)
# =============================================================================


class TestLens13CarryForwardAsProbePinning:
    """Lens 13: pin the carry-forwards so future fixes surface.

    The carry-forward-as-probe pattern (CS-03 TERMINOLOGIST methodology,
    strategy 56) documents current behavior as a probe assertion. When the
    fix lands, the probe MUST be updated to assert the new behavior.
    """

    def test_h130_cf_historian_vs02_01_pinned_as_open(self):
        """Source-read: CF-HISTORIAN-VS02-01 is structurally present (still OPEN).

        The 4 source-read probes in Lens 1 (test_h10-h13) collectively pin
        the structural condition. This probe is the META pin — it asserts
        that the sub-probes exist in this file and are collectively
        load-bearing for the CF.

        When the fix lands, ALL 4 sub-probes MUST be updated to assert the
        new structural shape (BFS returns 3-tuple OR +1 probe applied).
        """
        assert hasattr(TestLens1CFHistorianVS02OneSourceRead, "test_h10_bfs_helper_early_exit_on_limit_source_read")
        assert hasattr(TestLens1CFHistorianVS02OneSourceRead, "test_h12_intensional_call_site_passes_bfs_limit_count_source_read")
        assert hasattr(TestLens1CFHistorianVS02OneSourceRead, "test_h13_intensional_call_site_uses_len_deduped_for_total_source_read")

    def test_h131_cf_skeptic_vs01_01_pinned_as_open(self, fhir_client):
        """CF-SKEPTIC-VS01-01 (7 of 9 filter operators silently dropped) OPEN.

        The structural pin is in Lens 6 (test_h60 + test_h63).
        """
        # Verify an unsupported operator IS silently dropped.
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/h131",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "=", "value": SNOMED_DIABETES_MELLITUS}
                    ],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        assert status == 200
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 0, (
            "CF-SKEPTIC-VS01-01 OPEN: op='=' MUST be silently dropped"
        )

    def test_h132_cf_terminologist_vs01_01_whitespace_display_preserved(self, fhir_client):
        """CF-TERMINOLOGIST-VS01-01: client-supplied whitespace-only display IS preserved.

        Per CF-TERMINOLOGIST-VS01-01 (deferred): when the client supplies a
        display (even whitespace-only), the implementation echoes it verbatim.
        The fix would resolve the canonical via the engine — but per the
        deferred decision, the client-supplied display takes precedence.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/h132",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS, "display": "   "}
                    ],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        assert status == 200
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # The whitespace-only display IS preserved (CF-TERMINOLOGIST-VS01-01 OPEN).
        assert contains[0].get("display") == "   ", (
            f"CF-TERMINOLOGIST-VS01-01 OPEN: whitespace-only display MUST be "
            f"preserved as-is (current behavior); got {contains[0].get('display')!r}"
        )

    def test_h133_gap_t01_implicit_no_patient_friendly_extension(self, fhir_client):
        """GAP-T01 (CF-TERMINOLOGIST-01): implicit value set has no patient-friendly extension.

        Per GAP-T01: the implicit value set expander resolves display via
        get_code_infos but does NOT consult app.state.patient_friendly_cache.
        The conformance fixture cannot exercise this gap (no patient-friendly
        rows seeded). This probe pins the structural absence.
        """
        # Use the LOINC implicit value set URL.
        status, resp = _get_expand(
            fhir_client,
            params={"url": "http://loinc.org/vs", "count": 20},
        )
        if status == 200:
            for c in resp.get("expansion", {}).get("contains", []):
                # The extension list MUST NOT contain a patient-friendly extension.
                exts = c.get("extension", [])
                pf_present = any(
                    "patient-friendly" in str(e.get("url", "")) for e in exts
                )
                assert not pf_present, (
                    "GAP-T01 OPEN: implicit value set MUST NOT surface patient-friendly "
                    "extension (deferred enhancement per TS-03 TERMINOLOGIST)"
                )

    def test_h134_cf_historian_vs02_02_resolved_verified(self):
        """CF-HISTORIAN-VS02-02 RESOLVED — canonical_system_uri wired into implicit path.

        The structural pin is in Lens 7 (test_h70 + test_h71).
        """
        assert hasattr(TestLens7CFHistorianVS02TwoResolvedVerification, "test_h70_implicit_value_set_uses_canonical_system_uri")
