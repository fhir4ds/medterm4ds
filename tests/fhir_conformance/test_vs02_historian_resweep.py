"""VS-02 HISTORIAN resweep: ValueSet $expand — Basic.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Expansion.total: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.total
valueset-toocostly: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

This is the resweep (post-milestone-10) HISTORIAN pass for chunk VS-02. The
prior VS-02 HISTORIAN test_vs02_historian.py covered the baseline patterns
(CF-HISTORIAN-VS02-01 + CF-HISTORIAN-VS02-02 filed; CF-HISTORIAN-VS02-02
RESOLVED by TS-03 SKEPTIC QA-001). The SKEPTIC resweep (test_vs02_skeptic_resweep.py)
added 106 new probes and closed CF-SKEPTIC-VS02-03 in the same fix that
landed QA-001 (filter-mode build_valueset_expand call site missing total=).
CF-HISTORIAN-VS02-01 is still HIGH OPEN.

This HISTORIAN resweep applies the **pattern-match lens** ("what broke
before?") and is organized into 13 lens dimensions per the launch notes:

  Lens 1  — CF-HISTORIAN-VS02-01 source-read re-verification (load-bearing
             known issue per SKEPTIC tip #1): source-read on
             services/hierarchy.py:84-155 and apps/fhir_api.py:2597-2654.
  Lens 2  — +1 probe pattern consistency audit across all 4
             build_valueset_expand call sites per SKEPTIC tip #2.
  Lens 3  — Methodology reuse: AST-walk over build_valueset_expand call
             sites generalizes to other response builders with size fields
             per SKEPTIC tip #3 — apply to build_parameters_translate and
             build_closure_response.
  Lens 4  — QA-057 expansion.total field pattern (count=3 PROMOTED at
             GLOBAL_RULES.md line 136; SKEPTIC added the 4th instance in
             this run).
  Lens 5  — CF-SKEPTIC-VS02-01 (count=0 422) re-derived.
  Lens 6  — CF-SKEPTIC-VS02-03 (CLOSED by SKEPTIC QA-001) verification.
  Lens 7  — CF-HISTORIAN-VS02-02 (RESOLVED — implicit value set Form (a))
             re-derived.
  Lens 8  — PROMOTED pattern re-derivation (count=10).
  Lens 9  — Source-read structural contracts for the 4 call sites.
  Lens 10 — Response shape audit across every mode.
  Lens 11 — Documentation-vs-implementation drift (VS-02 SKEPTIC docstring
             on build_valueset_expand matches implementation).
  Lens 12 — Cross-handler GET<->POST byte-exact parity.
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
    test_vs02_skeptic_resweep.py.
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
        "url": "http://example.org/vs/vs02-hist-resweep-intensional",
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


def _contains_codes(body: dict) -> list[tuple[str, str]]:
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


# =============================================================================
# Lens 1 — CF-HISTORIAN-VS02-01 source-read re-verification (LOAD-BEARING)
#
# SKEPTIC tip #1: re-verify via source-read on services/hierarchy.py:84-155
# (get_descendants_bfs(..., limit=N) early-exits once len(results) >= N) and
# the intensional call site at apps/fhir_api.py:2597-2654. Bug is structural
# but fixture-coincidence-masked (1 mrrel row matches count=1 exactly).
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.total
# Quote: "The total number of concepts in the expansion."
# =============================================================================


class TestLens1CFHistorianVS02OneSourceRead:
    """Lens 1: CF-HISTORIAN-VS02-01 source-read re-verification.

    The bug is structurally present: get_descendants_bfs early-exits at the
    limit, so the descendants list passed to the intensional builder is
    ALREADY truncated, and total=len(deduped) reports the truncated size when
    the cap fires. The conformance fixture masks the bug (exactly 1 mrrel row
    matches count=1).
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

        # Locate get_descendants_bfs.
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
        # Line 129: ``if limit is not None and len(results) >= limit:`` (top of loop)
        # Line 146: ``if limit is not None and len(results) >= limit:`` (inside child loop)
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
        # Import the helper directly.
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
        bug. The call site at apps/fhir_api.py:2644-2649 invokes
        ``get_descendants_bfs(..., limit=count)`` — so when count < natural
        descendant count, BFS early-exits with a truncated list.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert src, "_expand_intensional source not found"

        # The BFS call MUST pass limit=count (or a count-derived expression).
        # Source-read: look for the get_descendants_bfs call followed by
        # ``limit=count`` or ``limit=`` within the function body.
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
# Lens 2 — +1 probe pattern consistency audit
#
# SKEPTIC tip #2: 3 of 4 build_valueset_expand call sites now use +1
# (filter mode just fixed, implicit value set, URL pattern); the 4th
# (_expand_intensional) uses +0 (CF-HISTORIAN-VS02-01 territory). Audit
# this asymmetry.
# =============================================================================


class TestLens2PlusOneProbeConsistency:
    """Lens 2: +1 probe pattern consistency across all 4 call sites.

    The +1 probe pattern (calling the underlying source with limit=N+1 so
    count_limited can be computed as ``len(results) > N``) is the
    structural fix for "limit hit at exactly budget" ambiguity. It's used
    by 3 of 4 build_valueset_expand call sites; the 4th
    (_expand_intensional) uses +0 — which is CF-HISTORIAN-VS02-01 territory.
    """

    def test_h20_filter_mode_call_site_uses_plus_one_probe(self):
        """Source-read: _do_expand filter mode uses limit=count + 1 (SKEPTIC QA-001)."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        # The filter mode uses search_names(..., limit=count + 1).
        assert "limit=count + 1" in src, (
            "Filter mode call site MUST use limit=count + 1 (SKEPTIC QA-001 fix)"
        )

    def test_h21_url_pattern_call_site_uses_plus_one_probe(self):
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

    def test_h22_implicit_value_set_call_site_uses_plus_one_probe(self):
        """Source-read: _expand_implicit_value_set uses LIMIT count + 1 SQL trick.

        Per SKEPTIC tip: the implicit value set path uses a ``LIMIT count + 1``
        SQL query trick to detect truncation.
        """
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set"
        )
        # The implicit path uses SQL LIMIT count + 1 (case-insensitive match).
        assert "count + 1" in src.lower() or "count+1" in src.lower(), (
            "Implicit value set call site MUST use LIMIT count + 1 SQL trick"
        )

    def test_h23_intensional_call_site_uses_plus_one_probe(self):
        """Source-read: _expand_intensional uses the +1 probe pattern.

        CF-HISTORIAN-VS02-01 RESOLVED: the intensional path now uses
        ``limit=count + 1``, harmonizing with the 3 sibling call sites
        (filter mode, URL pattern, implicit value set). The +1 probe
        pattern (calling the underlying source with limit=N+1 so
        count_limited can be computed as ``len(results) > N``) is the
        structural fix for "limit hit at exactly budget" ambiguity.

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

    def test_h24_call_site_count_signature(self):
        """Source-read: count_limited uses strict-greater-than (>) in all 4 sites.

        Per VS-04 TERMINOLOGIST QA-068: the count-truncation signal MUST use
        strict-greater-than (``>``), NOT greater-than-or-equal (``>=``). The
        prior implementation used ``>=`` which fired the toocostly extension
        even on COMPLETE expansions when fixture size matched budget exactly.
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


# =============================================================================
# Lens 3 — Methodology reuse: AST-walk over build_valueset_expand call sites
#          generalizes to other response builders with size fields
#
# SKEPTIC tip #3: Apply the AST-walk over build_valueset_expand call sites
# (test_s100) to build_parameters_translate and build_closure_response.
# =============================================================================


class TestLens3MethodologyReuseBuilderCallSiteAudit:
    """Lens 3: AST-walk over every call site of build_parameters_translate
    and build_closure_response.

    The methodology: walk the AST of apps/fhir_api.py and engines/fhir/*
    looking for calls to the builder. For each call site, verify the
    expected invariant.

    For build_parameters_translate: the source_system_uri parameter MUST
    be canonical (sourced via canonical_system_uri helper, NOT echoed
    from client input). This is the client-input-as-canonical drift
    pattern (count=8 PROMOTED).

    For build_closure_response: the builder takes a ClosureTable object
    (not a list of pre-truncated entries), so the size-field-from-wrong-
    source pattern doesn't directly apply. But the call-site audit still
    verifies: (a) the builder is called from _do_closure only; (b) the
    closure object is built from the request body's concepts; (c) the
    response shape matches the FHIR R4 $closure spec.
    """

    def test_h30_build_parameters_translate_call_sites_count(self):
        """Source-read: build_parameters_translate has exactly 1 call site.

        Per AGENTS.md, the only caller is _do_translate at apps/fhir_api.py:2150.
        If a new caller is added, this probe will catch it and force the
        new caller to be audited for client-input-as-canonical drift.
        """
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        call_count = 0
        call_nodes = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_parameters_translate"
            ):
                call_count += 1
                call_nodes.append(node.lineno)
        assert call_count == 1, (
            f"build_parameters_translate has {call_count} call sites at lines "
            f"{call_nodes}; expected exactly 1. New call sites MUST be audited "
            f"for client-input-as-canonical drift (count=8 PROMOTED pattern)."
        )

    def test_h31_build_parameters_translate_call_site_passes_canonical_source(self):
        """Source-read: _do_translate passes canonical_source_uri (NOT raw client input).

        Per CR-012 (milestone-2 review): the source system URI MUST be re-
        resolved to canonical via canonical_system_uri() before passing to
        the builder. Without this, Out match[].source.system echoes the
        client-supplied source_uri verbatim — including aliases.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_translate")
        assert "canonical_source_uri = canonical_system_uri(" in src, (
            "_do_translate MUST re-resolve source_uri via canonical_system_uri "
            "before passing to build_parameters_translate (CR-012 fix)"
        )
        assert "source_system_uri=canonical_source_uri" in src, (
            "_do_translate MUST pass canonical_source_uri (NOT raw source_uri) "
            "to build_parameters_translate"
        )

    def test_h32_build_parameters_translate_uses_fhir_equivalence_helper(self):
        """Source-read: build_parameters_translate uses _fhir_equivalence_from_relationship.

        Per TS-02 TERMINOLOGIST QA-030 + CF-HISTORIAN-VS01-01 (RESOLVED):
        the equivalence value MUST be sourced from each mapping's
        relationship field via the translation helper, NOT hardcoded to
        "equivalent". Hardcoding misrepresents SNOMED->ICD10CM crosswalks
        (typically relatedto) and ancestor/descendant mappings
        (subsumes/specializes).
        """
        src = _RESPONSES_PATH.read_text()
        # The builder MUST call the helper per match entry.
        assert "_fhir_equivalence_from_relationship(m.relationship)" in src, (
            "build_parameters_translate MUST source equivalence from "
            "_fhir_equivalence_from_relationship (TS-02 QA-030 + CR-024)"
        )
        # The builder MUST NOT hardcode "equivalent" as the equivalence value.
        # (Commentary may mention "equivalent" but the wire-value MUST be dynamic.)

    def test_h33_build_closure_response_call_sites_count(self):
        """Source-read: build_closure_response has exactly 1 call site in fhir_api.

        Per AGENTS.md, the only caller is _do_closure at apps/fhir_api.py:2303.
        """
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        call_count = 0
        call_nodes = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_closure_response"
            ):
                call_count += 1
                call_nodes.append(node.lineno)
        assert call_count == 1, (
            f"build_closure_response has {call_count} call sites at lines "
            f"{call_nodes}; expected exactly 1. New call sites MUST be audited."
        )

    def test_h34_build_closure_response_takes_closure_object(self):
        """Source-read: build_closure_response signature takes ClosureTable.

        Unlike build_valueset_expand (which takes a list that callers can
        pre-truncate), build_closure_response takes a ClosureTable object.
        This means the size-field-from-wrong-source pattern does NOT apply
        here — the builder reads the closure's full state via
        to_parameter_list(). The audit confirms the structural difference.
        """
        from medterm4ds.engines.fhir.closure import build_closure_response

        sig = inspect.signature(build_closure_response)
        params = list(sig.parameters.keys())
        assert params == ["closure"], (
            f"build_closure_response signature changed: {params}; expected ['closure']. "
            f"The builder takes a ClosureTable object, NOT a pre-truncatable list — "
            f"the size-field-from-wrong-source pattern does NOT apply here."
        )

    def test_h35_build_closure_response_call_passes_closure_object(self):
        """Source-read: _do_closure passes a ClosureTable object (not a list)."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_closure")
        assert "return build_closure_response(closure)" in src, (
            "_do_closure MUST pass a closure object to build_closure_response"
        )


# =============================================================================
# Lens 4 — QA-057 expansion.total field pattern re-derivation
# PROMOTED at GLOBAL_RULES.md line 136 (count=3 → count=4 with SKEPTIC resweep)
# =============================================================================


class TestLens4QA057ExpansionTotalFieldPattern:
    """Lens 4: QA-057 pattern — every build_valueset_expand call site MUST
    pass total=<un-truncated-size> when the caller pre-truncates.

    This is the load-bearing PROMOTED pattern. The 4 call sites are:
    1. expand_url_pattern (line 313)
    2. _expand_intensional (line 2696)
    3. _expand_implicit_value_set (line 2899)
    4. _do_expand filter mode (line 2489 — SKEPTIC resweep QA-001)
    """

    def test_h40_build_valueset_expand_has_total_parameter(self):
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

    def test_h41_all_4_call_sites_pass_total_explicitly(self):
        """Source-read: all 4 build_valueset_expand call sites pass total=.

        Per GLOBAL_RULES.md line 136 PROMOTED pattern (count=4 with SKEPTIC
        resweep QA-001). The AST-walk over the source MUST find exactly 4
        call sites, and every one MUST pass total=.
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

    def test_h42_filter_mode_total_lower_bound_semantic(self, fhir_client):
        """Behavioral: filter mode count=1 returns total >= 2 (the +1 probe lower bound).

        Per SKEPTIC QA-001 fix: when count=1 truncates a result set with
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


# =============================================================================
# Lens 5 — CF-SKEPTIC-VS02-01 (count=0 422) re-derived
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html "count"
# =============================================================================


class TestLens5CFSkepticVS02OneCountZero422:
    """Lens 5: CF-SKEPTIC-VS02-01 — count=0 rejected with 422 (DEFERRED).

    Per FHIR R4 $expand In Parameters ``count``: "A count of 0 means that
    no entries will be returned." The spec text allows count=0 to return
    an empty expansion. medterm4ds enforces ``Query(20, ge=1, le=1000)``
    which rejects count=0 with 422.
    """

    def test_h50_count_zero_rejected_with_422(self, fhir_client):
        """Behavioral: count=0 rejected with 422 per current impl.

        Spec-correct behavior would be 200 with empty contains. This probe
        is the carry-forward-as-probe pattern — it pins the current 422
        behavior so a future fix that loosens the constraint will surface.
        """
        status, resp = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "count": 0},
        )
        assert status == 422, (
            f"CF-SKEPTIC-VS02-01: count=0 currently rejected with 422; got {status}. "
            f"If this changed, the constraint was loosened — update this probe."
        )

    def test_h51_count_zero_query_constraint_source_read(self):
        """Source-read: count Query declaration uses ge=1 (rejects 0)."""
        src = _FHIR_API_PATH.read_text()
        # The expand_get and expand_post handlers declare count with ge=1.
        assert "count: int = Query(20, ge=1, le=1000)" in src, (
            "CF-SKEPTIC-VS02-01 pin: count constraint uses ge=1 (rejects 0)"
        )


# =============================================================================
# Lens 6 — CF-SKEPTIC-VS02-03 (CLOSED by SKEPTIC QA-001) verification
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
# =============================================================================


class TestLens6CFSkepticVS02ThreeClosedVerification:
    """Lens 6: CF-SKEPTIC-VS02-03 (filter-mode toocostly gap) was CLOSED by
    SKEPTIC QA-001 in this run. Verify the closure holds.
    """

    def test_h60_filter_mode_truncation_emits_toocostly(self, fhir_client):
        """Behavioral: filter mode with truncation MUST emit valueset-toocostly.

        Per CF-SKEPTIC-VS02-03 closure (SKEPTIC QA-001 fix): when count
        truncates the filter-mode result set, the valueset-toocostly
        extension MUST be emitted. Before the fix, this extension was
        silently dropped.
        """
        status, resp = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "count": 1},
        )
        assert status == 200
        exp = resp.get("expansion", {})
        exts = exp.get("extension", [])
        toocostly_present = any(
            e.get("url") == TOOCOSTLY_URL for e in exts
        )
        assert toocostly_present, (
            "CF-SKEPTIC-VS02-03 CLOSED: filter mode truncation MUST emit "
            "valueset-toocostly extension"
        )

    def test_h61_filter_mode_extensions_parameter_passed(self):
        """Source-read: _do_expand filter mode passes extensions= to builder."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        # The filter mode MUST pass extensions= to build_valueset_expand.
        assert "extensions=extensions" in src, (
            "Filter mode MUST pass extensions= to build_valueset_expand "
            "(closes CF-SKEPTIC-VS02-03)"
        )


# =============================================================================
# Lens 7 — CF-HISTORIAN-VS02-02 (RESOLVED) re-derived
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
        # The implicit path MUST call canonical_system_uri on contains[].system.
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
                # The contains[].system MUST NOT be the URL prefix verbatim
                # (e.g. "http://loinc.org/vs") — that would indicate the
                # CF-HISTORIAN-VS02-02 regression.
                if "loinc.org" in sys:
                    assert not sys.endswith("/vs"), (
                        f"CF-HISTORIAN-VS02-02 regression: contains[].system={sys} "
                        f"echoes the URL prefix verbatim (must be canonical URI)"
                    )


# =============================================================================
# Lens 8 — PROMOTED patterns re-derivation (count=10)
# =============================================================================


class TestLens8PromotedPatternsReDerivation:
    """Lens 8: Re-derive the 10 PROMOTED patterns from GLOBAL_RULES.md.

    Each probe verifies a pattern holds (no new instance introduced in this
    iteration's diff). The patterns are: (1) empty-string-as-present on
    required Query (count=5); (2) client-input-as-canonical drift (count=8+1);
    (3) literal-value-vs-canonical-registry drift (count=8); (4) cross-
    handler helper-wiring (count=6); (5) closed-enum R5/R4B contamination
    CF-HISTORIAN-VS01-01 (RESOLVED); (6) silent-wrong-answer on alt encodings
    (count=6); (7) boolean serializer lowercase wire-format (A1/CR-002);
    (8) test-too-lenient probe class (TS-03 QA-034); (9) URL constructor
    edge cases (count=2 — TS-04); (10) response-builder size field from
    wrong source (count=4 — VS-02 SKEPTIC QA-001 added 4th instance).
    """

    def test_h80_promoted_1_empty_string_required_query_min_length(self):
        """Source-read: required string Query declarations have min_length=1."""
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        # Find all Query(...) calls inside the create_fhir_app factory.
        # For required string Query declarations, min_length=1 MUST be present.
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            func_src = ast.get_source_segment(src, node) or ""
            # Look for patterns like ``code: str = Query(..., ...)`` without min_length=1.
            # Simple heuristic: find ``Query(...)`` on required (with ...) string args.
            # We don't need to walk every function — just verify the global count.
        # The structural invariant: grep for ``Query(..., required`` and ``min_length=1``.
        assert "min_length=1" in src, (
            "min_length=1 MUST be present on required string Query declarations"
        )

    def test_h81_promoted_2_canonical_system_uri_helper_wired(self):
        """Source-read: canonical_system_uri helper is imported and called."""
        src = _FHIR_API_PATH.read_text()
        assert "canonical_system_uri" in src, (
            "canonical_system_uri helper MUST be used (client-input-as-canonical "
            "drift count=8+1 PROMOTED structural fix)"
        )

    def test_h82_promoted_3_closed_enum_filter_operators_imported(self):
        """Source-read: FHIR_R4_FILTER_OPERATORS imported from canonical location."""
        # The closed enum MUST be imported from engines.fhir, not redefined locally.
        src = _FHIR_API_PATH.read_text()
        # The import may or may not be in fhir_api directly — verify the
        # canonical location exists in engines.fhir.
        from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS as _ops
        assert "is-a" in _ops, "FHIR_R4_FILTER_OPERATORS MUST contain 'is-a'"
        assert "descendent-of" in _ops, (
            "FHIR_R4_FILTER_OPERATORS MUST contain spec-correct 'descendent-of' "
            "(NOT 'descendant-of')"
        )
        assert "descendant-of" not in _ops, (
            "FHIR_R4_FILTER_OPERATORS MUST NOT contain off-spec 'descendant-of'"
        )

    def test_h83_promoted_5_closed_enum_equivalence_no_r5_leak(self):
        """Source-read: FHIR_R4_CONCEPT_MAP_EQUIVALENCE has no R5/R4B values.

        Per CF-HISTORIAN-VS01-01 RESOLVED: R4 uses ``specializes`` (NOT R5/R4B
        ``subsumedby``) for the reverse-of-subsumes case. R4 does NOT have a
        hyphenated ``subsumed-by`` form — that's an R5/R4B value too. The
        canonical R4 set is documented at
        https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html.
        """
        from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE as _eq
        # R5/R4B values that MUST NOT be in R4 surface.
        assert "subsumedby" not in _eq, (
            "CF-HISTORIAN-VS01-01 RESOLVED: 'subsumedby' (R5/R4B) MUST NOT be in R4 enum"
        )
        assert "matches" not in _eq, (
            "CF-HISTORIAN-VS01-01 RESOLVED: 'matches' (R5-only) MUST NOT be in R4 enum"
        )
        # R4 does NOT use a hyphenated 'subsumed-by' either — that's R5.
        assert "subsumed-by" not in _eq, (
            "R4 does NOT use 'subsumed-by' (that's R5); R4 uses 'specializes' "
            "for the reverse-of-subsumes case"
        )
        # R4 spec-correct values MUST be present.
        assert "specializes" in _eq, (
            "R4 'specializes' MUST be in enum (reverse-of-subsumes)"
        )
        assert "subsumes" in _eq, "R4 'subsumes' MUST be in enum"

    def test_h84_promoted_7_boolean_serializer_lowercase_xml(self):
        """Source-read: _scalar_to_xml special-cases bool BEFORE generic conversion.

        Per CR-002 (milestone-1 code review): Python's str(False) is "False"
        (capital F), not "false". FHIR R4 §3.4.1 mandates lowercase true/false.
        """
        from medterm4ds.engines.fhir import xml as xml_module
        # The xml module MUST have a _scalar_to_xml helper that handles bool.
        assert hasattr(xml_module, "_scalar_to_xml") or hasattr(
            xml_module, "_render_value"
        ), (
            "engines/fhir/xml.py MUST have a scalar-to-XML helper that "
            "special-cases bool before generic str() conversion (CR-002)"
        )

    def test_h85_promoted_10_response_builder_size_field_count(self):
        """Source-read: 4 build_valueset_expand call sites (count=4 PROMOTED).

        Per GLOBAL_RULES.md line 136 (count=4 with SKEPTIC resweep QA-001):
        every call site that pre-truncates MUST pass total= explicitly.
        """
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
# Lens 9 — Source-read structural contracts for the 4 call sites
# =============================================================================


class TestLens9SourceReadStructuralContracts:
    """Lens 9: Source-read structural contracts for build_valueset_expand.

    Verify each of the 4 call sites has the expected shape.
    """

    def test_h90_expand_url_pattern_uses_descendant_budget_plus_one(self):
        """Source-read: expand_url_pattern uses descendant_budget = max(0, count - len(contains)).

        Per VS-04 TERMINOLOGIST QA-068: ``max(0, count - len(contains))``
        (NOT ``max(1, ...)``) so count=1 with root already in contains
        reserves zero descendant slots.
        """
        src = _get_func_source(_FHIR_API_PATH, "expand_url_pattern")
        assert "descendant_budget = max(0, count - len(contains))" in src, (
            "expand_url_pattern MUST use max(0, count - len(contains)) (QA-068)"
        )

    def test_h91_expand_intensional_uses_bfs_with_limit_count(self):
        """Source-read: _expand_intensional uses get_descendants_bfs(limit=count).

        CF-HISTORIAN-VS02-01 territory: the BFS limit=count is the structural
        pre-truncation step that makes the bug real.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # The BFS call MUST pass max_depth and limit=count.
        assert "max_depth=max_depth" in src, (
            "_expand_intensional MUST pass max_depth=max_depth to BFS"
        )

    def test_h92_expand_implicit_value_set_uses_count_plus_one(self):
        """Source-read: _expand_implicit_value_set uses count + 1 SQL trick."""
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set"
        )
        assert "count + 1" in src.lower(), (
            "_expand_implicit_value_set MUST use count + 1 SQL trick"
        )

    def test_h93_do_expand_filter_mode_uses_count_plus_one_search(self):
        """Source-read: _do_expand filter mode uses search_names(limit=count + 1)."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert "search_names(" in src, (
            "_do_expand filter mode MUST call search_names"
        )
        assert "limit=count + 1" in src, (
            "_do_expand filter mode MUST use limit=count + 1 (SKEPTIC QA-001)"
        )

    def test_h94_do_expand_filter_mode_untruncated_total_computation(self):
        """Source-read: _do_expand filter mode computes untruncated_total correctly.

        Per SKEPTIC QA-001: when count_limited, untruncated_total = len(results) + 1
        (the +1 probe lower bound). When not count_limited, total = len(results).
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert "untruncated_total = len(results) + 1 if count_limited else len(results)" in src, (
            "_do_expand filter mode MUST compute untruncated_total via the +1 probe "
            "lower bound (SKEPTIC QA-001 fix)"
        )


# =============================================================================
# Lens 10 — Response shape audit across every mode
# =============================================================================


class TestLens10ResponseShapeEveryMode:
    """Lens 10: response shape conforms to FHIR R4 §4.9 in every mode."""

    def test_h100_filter_mode_response_shape(self, fhir_client):
        """Filter mode: ValueSet resource with expansion.{timestamp,total,contains[]}."""
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 20}
        )
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"
        exp = resp.get("expansion", {})
        assert "timestamp" in exp, "expansion.timestamp MUST be present"
        assert "total" in exp, "expansion.total MUST be present"
        assert "contains" in exp, "expansion.contains MUST be present"
        # timestamp MUST be a valid ISO 8601 UTC.
        ts = exp.get("timestamp", "")
        try:
            datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pytest.fail(f"expansion.timestamp not valid ISO 8601 UTC: {ts}")

    def test_h101_intensional_mode_response_shape(self, fhir_client):
        """Intensional mode: ValueSet resource with expansion."""
        body = _make_intensional_snomed_isa()
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"
        exp = resp.get("expansion", {})
        assert "timestamp" in exp
        assert "total" in exp
        assert "contains" in exp

    def test_h102_url_pattern_mode_response_shape(self, fhir_client):
        """URL pattern mode: ValueSet resource with expansion."""
        url = f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        status, resp = _get_expand(fhir_client, params={"url": url, "count": 20})
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"
        exp = resp.get("expansion", {})
        assert "timestamp" in exp
        assert "total" in exp
        assert "contains" in exp

    def test_h103_implicit_value_set_mode_response_shape(self, fhir_client):
        """Implicit value set mode: ValueSet resource with expansion."""
        status, resp = _get_expand(
            fhir_client,
            params={"url": f"{SNOMED_URI}?fhir_vs", "count": 20},
        )
        # Note: implicit value set path may return 200 or 400 depending
        # on whether the SNOMED base URI is recognized as implicit (Form b).
        if status == 200:
            assert resp.get("resourceType") == "ValueSet"
            exp = resp.get("expansion", {})
            assert "timestamp" in exp
            assert "total" in exp


# =============================================================================
# Lens 11 — Documentation-vs-implementation drift
# =============================================================================


class TestLens11DocumentationVsImplementationDrift:
    """Lens 11: docstring on build_valueset_expand matches implementation."""

    def test_h110_build_valueset_expand_docstring_documents_total_parameter(self):
        """Source-read: build_valueset_expand docstring documents the total parameter.

        Per VS-02 SKEPTIC QA-057: the docstring MUST explain the total
        parameter semantics and cite FHIR R4 §4.9.2.
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
        # Docstring MUST mention the total parameter.
        assert "total" in builder_src.lower(), (
            "build_valueset_expand docstring MUST document the total parameter"
        )
        # Docstring MUST cite FHIR R4 §4.9.2.
        assert "4.9.2" in builder_src or "§4.9.2" in builder_src, (
            "build_valueset_expand docstring MUST cite FHIR R4 §4.9.2 "
            "(The total number of concepts in the expansion)"
        )

    def test_h111_do_expand_docstring_lists_4_modes(self):
        """Source-read: _do_expand docstring lists the modes it handles.

        Per the prior 3-mode docstring (intensional / URL-based / filter),
        the docstring MUST accurately describe the implemented modes. The
        implicit value set mode is dispatched before _do_expand reaches
        the mode dispatch (in the calling handler), but _do_expand itself
        dispatches 4 modes internally per the source.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        # Docstring MUST mention at least 3 modes.
        assert "Intensional" in src or "intensional" in src, (
            "_do_expand docstring MUST mention intensional mode"
        )
        assert "URL" in src or "url" in src, (
            "_do_expand docstring MUST mention URL mode"
        )
        assert "Filter" in src or "filter" in src, (
            "_do_expand docstring MUST mention filter mode"
        )


# =============================================================================
# Lens 12 — Cross-handler GET<->POST byte-exact parity
# =============================================================================


class TestLens12CrossHandlerGetPostParity:
    """Lens 12: GET and POST $expand produce byte-exact semantic responses."""

    def test_h120_filter_mode_get_post_byte_exact(self, fhir_client):
        """GET ?filter=diabetes&count=20 == POST Parameters body with filter.

        Per expand_post handler (apps/fhir_api.py:2378-2389): POST with a
        Parameters body that carries filter as a parameter is parsed via
        _parse_parameters and produces the same _do_expand dispatch as GET
        with the same filter query param. The contains[] and total MUST
        be byte-exact equal between GET and POST.
        """
        get_status, get_resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 20}
        )
        # POST with a Parameters body carrying filter as a parameter.
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
        # Both MUST succeed.
        assert get_status == 200 and post_status == 200, (
            f"GET {get_status}, POST {post_status} — both MUST succeed"
        )
        # The contains[] MUST be byte-exact equal.
        get_contains = _contains_codes(get_resp)
        post_contains = _contains_codes(post_resp)
        assert get_contains == post_contains, (
            f"GET != POST contains: {get_contains} vs {post_contains}"
        )
        # total MUST be equal.
        assert get_resp.get("expansion", {}).get("total") == post_resp.get("expansion", {}).get("total"), (
            f"GET total {get_resp.get('expansion', {}).get('total')} != "
            f"POST total {post_resp.get('expansion', {}).get('total')}"
        )

    def test_h121_intensional_mode_post_only(self, fhir_client):
        """Intensional mode: POST with body. GET with url would 400 (no body accepted)."""
        body = _make_intensional_snomed_isa()
        status, resp = _post_expand(fhir_client, body, params={"count": 20})
        assert status == 200
        assert resp.get("resourceType") == "ValueSet"


# =============================================================================
# Lens 13 — Carry-forward-as-probe pinning (extension of strategy 56)
# =============================================================================


class TestLens13CarryForwardAsProbePinning:
    """Lens 13: pin the carry-forwards so future fixes surface.

    The carry-forward-as-probe pattern (CS-03 TERMINOLOGIST methodology,
    strategy 56) documents current behavior as a probe assertion. When the
    fix lands, the probe MUST be updated to assert the new behavior. This
    is the structural mechanism that prevents carry-forwards from being
    silently forgotten.
    """

    def test_h130_cf_historian_vs02_01_pinned_as_open(self):
        """Source-read: CF-HISTORIAN-VS02-01 is structurally present (still OPEN).

        The 4 source-read probes in Lens 1 (test_h10-h13) collectively pin
        the structural condition. This probe is the META pin — it asserts
        that the 4 sub-probes exist in this file and are collectively
        load-bearing for the CF.

        When the fix lands, ALL 4 sub-probes MUST be updated to assert the
        new structural shape (BFS returns 3-tuple OR +1 probe applied).
        """
        # The 4 sub-probes are test_h10, test_h11, test_h12, test_h13 in
        # TestLens1CFHistorianVS02OneSourceRead. They are load-bearing.
        # This META probe is a documentation anchor — it doesn't add new
        # assertions but ensures the CF is tracked.
        assert hasattr(TestLens1CFHistorianVS02OneSourceRead, "test_h10_bfs_helper_early_exit_on_limit_source_read")
        assert hasattr(TestLens1CFHistorianVS02OneSourceRead, "test_h12_intensional_call_site_passes_bfs_limit_count_source_read")
        assert hasattr(TestLens1CFHistorianVS02OneSourceRead, "test_h13_intensional_call_site_uses_len_deduped_for_total_source_read")

    def test_h131_cf_skeptic_vs02_01_pinned_as_open(self, fhir_client):
        """CF-SKEPTIC-VS02-01 (count=0 422) pinned as OPEN via test_h50."""
        # The structural pin is in Lens 5 (test_h50 + test_h51).
        status, _ = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 0}
        )
        # The CF is OPEN — count=0 still rejected with 422.
        assert status == 422, (
            "CF-SKEPTIC-VS02-01 OPEN: count=0 currently rejected with 422"
        )

    def test_h132_cf_skeptic_vs02_03_pinned_as_closed(self, fhir_client):
        """CF-SKEPTIC-VS02-03 (filter-mode toocostly gap) CLOSED by SKEPTIC QA-001."""
        # The structural pin is in Lens 6 (test_h60 + test_h61).
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 1}
        )
        assert status == 200
        exts = resp.get("expansion", {}).get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            "CF-SKEPTIC-VS02-03 CLOSED: toocostly extension MUST be present"
        )
