"""Tests for services.loinc_parts over prepared mt4ds tables and raw mrrel.

Phase 1a of canonical_anchors improvement plan.
"""
from __future__ import annotations

import duckdb
import pytest

from medterm4ds.core.models import CodeRef, CodeRelation
from medterm4ds.services.loinc_parts import (
    get_class_of,
    get_component_tests,
    get_lp_ancestors,
    get_lp_children,
    get_lp_descendants,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _build_db(con: duckdb.DuckDBPyConnection) -> None:
    """Build a minimal DB mirroring the layout used by services.loinc_parts.

    Includes:
    - mt4ds.walk_edges with LP↔LP isa edges (LP14161-1 Cortisol has children
      LP29041-8 Cortisol Free and LP286151-8 HbA1c-orphan; grandchild
      LP99999-0 under LP29041-8).
    - mrconso + mrrel for component_of and class_of (synthetic).
    """
    con.execute("CREATE SCHEMA IF NOT EXISTS mt4ds")

    # mt4ds.walk_edges — ISA LP-LP sub-hierarchy + LNC test/MTHU isa
    con.execute(
        """
        CREATE TABLE mt4ds.walk_edges (
            source VARCHAR, from_code VARCHAR, from_aui VARCHAR, from_cui VARCHAR,
            from_tty VARCHAR, to_code VARCHAR, to_aui VARCHAR, to_cui VARCHAR,
            to_tty VARCHAR, relationship VARCHAR, direction VARCHAR, edge_source VARCHAR
        )
        """
    )
    # walk_edges orientation: from=child, to=parent.
    walk_rows = [
        # LP-LP sub-hierarchy: Cortisol Free (LP29041-8) is child of Cortisol (LP14161-1)
        ("LNC", "LP29041-8", "AUI_LP29041", "C_CORT_FREE", "PT",
         "LP14161-1", "AUI_LP14161", "C_CORTISOL", "PT",
         "isa", "parent", "umls_mrrel"),
        # Grandchild: LP99999-0 (Free Cortisol AM) child of LP29041-8
        ("LNC", "LP99999-0", "AUI_LP99999", "C_CORT_FREE_AM", "PT",
         "LP29041-8", "AUI_LP29041", "C_CORT_FREE", "PT",
         "isa", "parent", "umls_mrrel"),
        # Another Cortisol child: LP16352-3 (Cortisol Urine)
        ("LNC", "LP16352-3", "AUI_LP16352", "C_CORT_URINE", "PT",
         "LP14161-1", "AUI_LP14161", "C_CORTISOL", "PT",
         "isa", "parent", "umls_mrrel"),
        # Unrelated LNC test/MTHU row (should not appear in LP walks)
        ("LNC", "2160-0", "AUI_2160", "C_CREAT", "LN",
         "MTHU000226", "AUI_MTHU", "C_CREAT_MTHU", "HC",
         "isa", "parent", "umls_mrrel"),
    ]
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        walk_rows,
    )

    # mrconso — atoms needed for component_of / class_of tests
    con.execute(
        """
        CREATE TABLE mrconso (
            CUI VARCHAR, LAT VARCHAR, TS VARCHAR, LUI VARCHAR, STT VARCHAR,
            SUI VARCHAR, ISPREF VARCHAR, AUI VARCHAR, SAUI VARCHAR, SCUI VARCHAR,
            SDUI VARCHAR, SAB VARCHAR, TTY VARCHAR, CODE VARCHAR, STR VARCHAR,
            SRL VARCHAR, SUPPRESS VARCHAR, CVF VARCHAR
        )
        """
    )
    mrconso_rows = [
        # LP14161-1 (Cortisol component)
        ("C_CORTISOL", "ENG", "P", "L0", "PF", "S0", "Y", "AUI_LP14161", None, None, None,
         "LNC", "PT", "LP14161-1", "Cortisol", "0", "N", "200"),
        # LP14319-6 (Creatinine component)
        ("C_CREAT_COMP", "ENG", "P", "L0", "PF", "S0", "Y", "AUI_LP14319", None, None, None,
         "LNC", "PT", "LP14319-6", "Creatinine", "0", "N", "200"),
        # 2160-0 (Creatinine test)
        ("C_CREAT", "ENG", "P", "L0", "PF", "S0", "Y", "AUI_2160", None, None, None,
         "LNC", "FT", "2160-0", "Creatinine [Mass/Vol]", "0", "N", "200"),
        # 3094-0 (BUN test)
        ("C_BUN", "ENG", "P", "L0", "PF", "S0", "Y", "AUI_3094", None, None, None,
         "LNC", "FT", "3094-0", "BUN [Mass/Vol]", "0", "N", "200"),
        # LP7780-3 (Chemistry class)
        ("C_CHEM_CLASS", "ENG", "P", "L0", "PF", "S0", "Y", "AUI_LP7780", None, None, None,
         "LNC", "PT", "LP7780-3", "Chemistry", "0", "N", "200"),
        # LP99665-7 (Hematology class)
        ("C_HEM_CLASS", "ENG", "P", "L0", "PF", "S0", "Y", "AUI_LP99665", None, None, None,
         "LNC", "PT", "LP99665-7", "Hematology", "0", "N", "200"),
    ]
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        mrconso_rows,
    )

    # mrrel — component_of and class_of edges (synthetic).
    # Note: real umls_local.duckdb has many more columns; for tests we use
    # the slim schema (AUI1, AUI2, REL, RELA) that matches production.
    con.execute(
        """
        CREATE TABLE mrrel (
            AUI1 VARCHAR, AUI2 VARCHAR, REL VARCHAR, RELA VARCHAR
        )
        """
    )
    mrrel_rows = [
        # component_of: AUI1=test, AUI2=LP component
        ("AUI_2160", "AUI_LP14319", "RB", "component_of"),
        # class_of: AUI1=test, AUI2=class LP
        ("AUI_2160", "AUI_LP7780", "RB", "class_of"),
        ("AUI_3094", "AUI_LP99665", "RB", "class_of"),
    ]
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        mrrel_rows,
    )


@pytest.fixture()
def prepared_db():
    con = duckdb.connect(":memory:")
    _build_db(con)
    yield con
    con.close()


# ---------------------------------------------------------------------------
# LP-LP sub-hierarchy (uses mt4ds.walk_edges)
# ---------------------------------------------------------------------------

class TestGetLpChildren:
    def test_returns_direct_children(self, prepared_db):
        results = get_lp_children([CodeRef(source="LNC", code="LP14161-1")], prepared_db)
        # Cortisol has two children: Cortisol Free + Cortisol Urine
        child_codes = {r.target.code for r in results}
        assert child_codes == {"LP29041-8", "LP16352-3"}
        # All should be depth=1, rel='isa', relationship='child'
        for r in results:
            assert isinstance(r, CodeRelation)
            assert r.depth == 1
            assert r.rel == "isa"
            assert r.relationship == "child"
            assert r.source.source == "LNC"
            assert r.target.source == "LNC"

    def test_no_children_returns_empty(self, prepared_db):
        # LP99999-0 is a leaf with no children
        results = get_lp_children([CodeRef(source="LNC", code="LP99999-0")], prepared_db)
        assert results == []

    def test_empty_input_returns_empty(self, prepared_db):
        assert get_lp_children([], prepared_db) == []

    def test_non_lnc_input_ignored(self, prepared_db):
        # SNOMED input is silently dropped
        results = get_lp_children([CodeRef(source="SNOMEDCT_US", code="X")], prepared_db)
        assert results == []

    def test_batch_multiple_lps(self, prepared_db):
        results = get_lp_children(
            [CodeRef(source="LNC", code="LP14161-1"),
             CodeRef(source="LNC", code="LP29041-8")],
            prepared_db,
        )
        # LP14161-1 has 2 children + LP29041-8 has 1 child (LP99999-0)
        child_codes = {r.target.code for r in results}
        assert child_codes == {"LP29041-8", "LP16352-3", "LP99999-0"}


class TestGetLpDescendants:
    def test_walks_transitively(self, prepared_db):
        # Cortisol → Cortisol Free → LP99999-0 (grandchild) + Cortisol Urine
        results = get_lp_descendants([CodeRef(source="LNC", code="LP14161-1")], prepared_db)
        descendants = {(r.target.code, r.depth) for r in results}
        # Depth 1: LP29041-8, LP16352-3
        # Depth 2: LP99999-0
        assert ("LP29041-8", 1) in descendants
        assert ("LP16352-3", 1) in descendants
        assert ("LP99999-0", 2) in descendants

    def test_respects_max_depth(self, prepared_db):
        # max_depth=1 should not return grandchild
        results = get_lp_descendants(
            [CodeRef(source="LNC", code="LP14161-1")], prepared_db, max_depth=1,
        )
        depths = {r.depth for r in results}
        assert depths == {1}

    def test_dedupes_paths(self, prepared_db):
        # If a code is reachable via two paths, it appears once at the
        # shallowest depth (visited-set semantics).
        results = get_lp_descendants(
            [CodeRef(source="LNC", code="LP14161-1"),
             CodeRef(source="LNC", code="LP29041-8")],
            prepared_db,
        )
        # LP99999-0 should appear once even though reachable from both seeds
        n_grandchild = sum(1 for r in results if r.target.code == "LP99999-0")
        assert n_grandchild == 1


class TestGetLpAncestors:
    def test_walks_up(self, prepared_db):
        # LP99999-0 has ancestor LP29041-8 (depth 1) and LP14161-1 (depth 2)
        results = get_lp_ancestors([CodeRef(source="LNC", code="LP99999-0")], prepared_db)
        ancestors = {(r.target.code, r.depth) for r in results}
        assert ("LP29041-8", 1) in ancestors
        assert ("LP14161-1", 2) in ancestors


# ---------------------------------------------------------------------------
# component_of / class_of (uses mrrel + mrconso)
# ---------------------------------------------------------------------------

class TestGetComponentTests:
    def test_finds_tests_for_component(self, prepared_db):
        # LP14319-6 (Creatinine) is the component of test 2160-0
        results = get_component_tests([CodeRef(source="LNC", code="LP14319-6")], prepared_db)
        assert len(results) == 1
        r = results[0]
        assert r.source.code == "LP14319-6"
        assert r.target.code == "2160-0"
        assert r.relationship == "component_test"
        assert r.rel == "component_of"
        assert r.rela == "component_of"
        assert r.depth == 1

    def test_lp_must_start_with_lp(self, prepared_db):
        # A test code (2160-0) passed as "component" should be ignored
        results = get_component_tests([CodeRef(source="LNC", code="2160-0")], prepared_db)
        assert results == []

    def test_unknown_component_returns_empty(self, prepared_db):
        results = get_component_tests([CodeRef(source="LNC", code="LPUNKNOWN")], prepared_db)
        assert results == []


class TestGetClassOf:
    def test_returns_class_for_test(self, prepared_db):
        result = get_class_of([CodeRef(source="LNC", code="2160-0")], prepared_db)
        assert "2160-0" in result
        class_code, class_display = result["2160-0"]
        assert class_code == "LP7780-3"
        assert class_display == "Chemistry"

    def test_no_class_returns_empty_dict(self, prepared_db):
        result = get_class_of([CodeRef(source="LNC", code="UNKNOWN")], prepared_db)
        assert result == {}

    def test_batch_multiple_tests(self, prepared_db):
        result = get_class_of(
            [CodeRef(source="LNC", code="2160-0"),
             CodeRef(source="LNC", code="3094-0")],
            prepared_db,
        )
        assert result["2160-0"] == ("LP7780-3", "Chemistry")
        assert result["3094-0"] == ("LP99665-7", "Hematology")

    def test_non_lnc_input_ignored(self, prepared_db):
        result = get_class_of([CodeRef(source="SNOMEDCT_US", code="X")], prepared_db)
        assert result == {}
