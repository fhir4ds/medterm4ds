"""Tests for services.crosswalk.find_via_walk (Phase 1b).

Tests cover:
- direction="self" (direct same-CUI crosswalk, replaces get_same_cui_mappings)
- direction="up" (walk ancestors, crosswalk at each depth)
- direction="down" (walk descendants, crosswalk at each depth)
- direction="both"
- relationship="isa" (uses mt4ds.walk_edges)
- relationship="has_active_ingredient" (uses raw mrrel fallback)
- depth annotation and dedup
- input validation
"""
from __future__ import annotations

import duckdb
import pytest

from medterm4ds.core.models import CodeMapping, CodeRef
from medterm4ds.services.crosswalk import find_via_walk, get_same_cui_mappings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _build_db(con: duckdb.DuckDBPyConnection) -> None:
    """Build a minimal DB with ISA walk_edges + same_cui_edges + mrrel/mrconso."""
    con.execute("CREATE SCHEMA IF NOT EXISTS mt4ds")

    # best_atoms for display names (used by get_same_cui_mappings indirectly
    # via crosswalk_prepared, but we set rank=1 anyway).
    con.execute(
        """
        CREATE TABLE mt4ds.best_atoms (
            source VARCHAR, code VARCHAR, aui VARCHAR, cui VARCHAR, tty VARCHAR,
            name VARCHAR, suppress VARCHAR, is_active BOOLEAN, rank INTEGER
        )
        """
    )
    best_atoms_rows = [
        # SNOMEDCT_US leaf + ancestor
        ("SNOMEDCT_US", "44054006", "AUI_SNO_T2DM", "C_T2DM", "PT",
         "DM2", "N", True, 1),
        ("SNOMEDCT_US", "73211009", "AUI_SNO_DM", "C_DM", "PT",
         "Diabetes mellitus", "N", True, 1),
        # ICD10CM exact + parent
        ("ICD10CM", "E11.9", "AUI_E119", "C_T2DM", "PT",
         "T2DM", "N", True, 1),
        ("ICD10CM", "E11", "AUI_E11", "C_DM", "PT",
         "DM", "N", True, 1),
        # SNOMEDCT_US Metformin substance + products
        ("SNOMEDCT_US", "372567009", "AUI_MET_SUB", "C_MET_SUB", "PT",
         "Metformin", "N", True, 1),
        ("SNOMEDCT_US", "438340003", "AUI_MET_PROD1", "C_MET_PROD1", "PT",
         "Metformin+repaglinide product", "N", True, 1),
        ("SNOMEDCT_US", "1299109003", "AUI_MET_PROD2", "C_MET_PROD2", "PT",
         "Empagliflozin+metformin product", "N", True, 1),
        # RXNORM Metformin substance (same CUI as SNOMEDCT substance via same-CUI)
        ("RXNORM", "86097", "AUI_RX_MET", "C_MET_SUB", "PT",
         "Metformin", "N", True, 1),
    ]
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        best_atoms_rows,
    )

    # mt4ds.same_cui_edges for direct crosswalk
    con.execute(
        """
        CREATE TABLE mt4ds.same_cui_edges (
            source VARCHAR, code VARCHAR, cui VARCHAR,
            target_source VARCHAR, target_code VARCHAR, target_aui VARCHAR,
            target_cui VARCHAR, target_tty VARCHAR
        )
        """
    )
    same_cui_rows = [
        # SNOMEDCT_US 44054006 (C_T2DM) <-> ICD10CM E11.9
        ("SNOMEDCT_US", "44054006", "C_T2DM", "ICD10CM", "E11.9",
         "AUI_E119", "C_T2DM", "PT"),
        # SNOMEDCT_US 73211009 (C_DM broader) <-> ICD10CM E11
        ("SNOMEDCT_US", "73211009", "C_DM", "ICD10CM", "E11",
         "AUI_E11", "C_DM", "PT"),
        # RXNORM 86097 (Metformin) <-> SNOMEDCT_US 372567009 via C_MET_SUB
        ("RXNORM", "86097", "C_MET_SUB", "SNOMEDCT_US", "372567009",
         "AUI_MET_SUB", "C_MET_SUB", "PT"),
        # SNOMEDCT products <-> SNOMEDCT substance via same-CUI? They have
        # distinct CUIs in our synthetic data; crosswalk only via same-CUI
        # won't link them. Use mrrel has_active_ingredient for that.
    ]
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        same_cui_rows,
    )

    # mt4ds.walk_edges — SNOMED ISA: 44054006 (child) → 73211009 (parent)
    con.execute(
        """
        CREATE TABLE mt4ds.walk_edges (
            source VARCHAR, from_code VARCHAR, from_aui VARCHAR, from_cui VARCHAR,
            from_tty VARCHAR, to_code VARCHAR, to_aui VARCHAR, to_cui VARCHAR,
            to_tty VARCHAR, relationship VARCHAR, direction VARCHAR, edge_source VARCHAR
        )
        """
    )
    walk_rows = [
        ("SNOMEDCT_US", "44054006", "AUI_SNO_T2DM", "C_T2DM", "PT",
         "73211009", "AUI_SNO_DM", "C_DM", "PT",
         "isa", "parent", "umls_mrrel"),
    ]
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        walk_rows,
    )

    # mrconso + mrrel for has_active_ingredient
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
        ("C_MET_SUB", "ENG", "P", "L0", "PF", "S0", "Y", "AUI_MET_SUB", None, None, None,
         "SNOMEDCT_US", "PT", "372567009", "Metformin", "0", "N", "200"),
        ("C_MET_PROD1", "ENG", "P", "L0", "PF", "S0", "Y", "AUI_MET_PROD1", None, None, None,
         "SNOMEDCT_US", "PT", "438340003", "Metformin+repaglinide product", "0", "N", "200"),
        ("C_MET_PROD2", "ENG", "P", "L0", "PF", "S0", "Y", "AUI_MET_PROD2", None, None, None,
         "SNOMEDCT_US", "PT", "1299109003", "Empagliflozin+metformin product", "0", "N", "200"),
    ]
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        mrconso_rows,
    )

    con.execute(
        """
        CREATE TABLE mrrel (
            AUI1 VARCHAR, AUI2 VARCHAR, REL VARCHAR, RELA VARCHAR
        )
        """
    )
    # has_active_ingredient: AUI1=substance, AUI2=product (matches real UMLS)
    mrrel_rows = [
        ("AUI_MET_SUB", "AUI_MET_PROD1", "RB", "has_active_ingredient"),
        ("AUI_MET_SUB", "AUI_MET_PROD2", "RB", "has_active_ingredient"),
    ]
    con.executemany("INSERT INTO mrrel VALUES (?, ?, ?, ?)", mrrel_rows)


@pytest.fixture()
def prepared_db():
    con = duckdb.connect(":memory:")
    _build_db(con)
    yield con
    con.close()


# ---------------------------------------------------------------------------
# direction="self"
# ---------------------------------------------------------------------------

class TestDirectionSelf:
    def test_returns_direct_same_cui_mapping(self, prepared_db):
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["ICD10CM"], direction="self",
        )
        assert len(results) == 1
        m = results[0]
        assert isinstance(m, CodeMapping)
        assert m.source == CodeRef(source="SNOMEDCT_US", code="44054006")
        assert m.target == CodeRef(source="ICD10CM", code="E11.9")
        assert m.relationship == "equivalent"
        assert m.match_type == "same_cui"
        assert m.match_depth == 0

    def test_target_filter_applied(self, prepared_db):
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        # Asking for RXNORM crosswalk — no match
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["RXNORM"], direction="self",
        )
        assert results == []

    def test_equivalent_to_get_same_cui_mappings(self, prepared_db):
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        via_walk = find_via_walk(
            codes, prepared_db,
            target_sources=["ICD10CM"], direction="self",
        )
        via_primitive = get_same_cui_mappings(
            codes, prepared_db, target_sources=["ICD10CM"],
        )
        assert len(via_walk) == len(via_primitive) == 1
        assert via_walk[0].target == via_primitive[0].target
        assert via_walk[0].match_type == via_primitive[0].match_type


# ---------------------------------------------------------------------------
# direction="up" (ISA)
# ---------------------------------------------------------------------------

class TestDirectionUp:
    def test_walks_ancestor_crosswalk(self, prepared_db):
        # 44054006 (T2DM) → walk up to 73211009 (DM) → crosswalk to E11
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["ICD10CM"], direction="up", max_depth=1,
        )
        # Expect: depth=0 direct (E11.9) + depth=1 ancestor (E11)
        by_depth = {(m.target.code, m.match_depth): m for m in results}
        assert ("E11.9", 0) in by_depth
        assert ("E11", 1) in by_depth
        m1 = by_depth[("E11", 1)]
        assert m1.relationship == "source-is-narrower-than-target"
        assert m1.match_type == "source_ancestor_same_cui"

    def test_no_walk_when_max_depth_zero(self, prepared_db):
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["ICD10CM"], direction="up", max_depth=0,
        )
        # Only direct (depth=0) should appear
        depths = {m.match_depth for m in results}
        assert depths == {0}

    def test_dedupes_when_ancestor_has_same_cui_as_seed(self, prepared_db):
        # If both seed and ancestor crosswalk to the same target, the
        # shallowest (depth=0) wins.
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["ICD10CM"], direction="up", max_depth=1,
        )
        e119_results = [m for m in results if m.target.code == "E11.9"]
        assert len(e119_results) == 1
        assert e119_results[0].match_depth == 0


# ---------------------------------------------------------------------------
# direction="down" (ISA)
# ---------------------------------------------------------------------------

class TestDirectionDown:
    def test_walks_descendant_crosswalk(self, prepared_db):
        # 73211009 (DM, ancestor) → walk down to 44054006 (T2DM) → crosswalk to E11.9
        codes = [CodeRef(source="SNOMEDCT_US", code="73211009")]
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["ICD10CM"], direction="down", max_depth=1,
        )
        by_depth = {(m.target.code, m.match_depth): m for m in results}
        # 73211009 has no direct same-CUI to ICD10CM in our fixture besides E11
        # Wait: 73211009 has C_DM which crosswalks to E11 directly (depth=0).
        # And walking down to 44054006 gives E11.9 (depth=1).
        assert ("E11", 0) in by_depth
        assert ("E11.9", 1) in by_depth
        m1 = by_depth[("E11.9", 1)]
        assert m1.relationship == "source-is-broader-than-target"
        assert m1.match_type == "source_descendant_same_cui"


# ---------------------------------------------------------------------------
# direction="both"
# ---------------------------------------------------------------------------

class TestDirectionBoth:
    def test_combines_up_and_down(self, prepared_db):
        # 44054006 has parent 73211009. direction="both" walks both ways.
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["ICD10CM"], direction="both", max_depth=1,
        )
        # Should include direct (E11.9, depth=0) + ancestor (E11, depth=1)
        by_depth = {(m.target.code, m.match_depth): m for m in results}
        assert ("E11.9", 0) in by_depth
        assert ("E11", 1) in by_depth


# ---------------------------------------------------------------------------
# Non-ISA relationships (raw mrrel fallback)
# ---------------------------------------------------------------------------

class TestNonIsaRelationship:
    def test_has_active_ingredient_walk_down(self, prepared_db):
        # Walk DOWN from Metformin substance (372567009) to products
        # via has_active_ingredient.
        codes = [CodeRef(source="SNOMEDCT_US", code="372567009")]
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["SNOMEDCT_US"],
            direction="down",
            relationship="has_active_ingredient",
            max_depth=1,
        )
        # No direct same-CUI match within SNOMEDCT_US (substance vs products
        # have distinct CUIs in fixture). The walk should find the two products.
        walked = [m for m in results if m.match_depth >= 1]
        walked_codes = {m.target.code for m in walked}
        assert "438340003" in walked_codes
        assert "1299109003" in walked_codes
        # All walked should be depth=1 and use the broader relationship
        for m in walked:
            assert m.match_depth == 1
            assert m.relationship == "source-is-broader-than-target"

    def test_unknown_relationship_does_not_crash(self, prepared_db):
        codes = [CodeRef(source="SNOMEDCT_US", code="372567009")]
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["SNOMEDCT_US"],
            direction="down",
            relationship="nonexistent_rela",
            max_depth=1,
        )
        # Empty result (no matches in mrrel)
        assert results == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_negative_max_depth_raises(self, prepared_db):
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        with pytest.raises(ValueError, match="non-negative"):
            find_via_walk(
                codes, prepared_db,
                target_sources=["ICD10CM"], max_depth=-1,
            )

    def test_max_depth_above_2_raises(self, prepared_db):
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        with pytest.raises(ValueError, match="exceeds auto-return cap"):
            find_via_walk(
                codes, prepared_db,
                target_sources=["ICD10CM"], max_depth=3,
            )

    def test_invalid_direction_raises(self, prepared_db):
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        with pytest.raises(ValueError, match="direction"):
            find_via_walk(
                codes, prepared_db,
                target_sources=["ICD10CM"], direction="sideways",  # type: ignore[arg-type]
            )

    def test_empty_input_returns_empty(self, prepared_db):
        results = find_via_walk(
            [], prepared_db,
            target_sources=["ICD10CM"], direction="up",
        )
        assert results == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unknown_code_returns_only_direct_if_any(self, prepared_db):
        # Unknown SNOMED code: no walk, no direct.
        codes = [CodeRef(source="SNOMEDCT_US", code="UNKNOWN")]
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["ICD10CM"], direction="up", max_depth=1,
        )
        assert results == []

    def test_batch_multiple_codes(self, prepared_db):
        codes = [
            CodeRef(source="SNOMEDCT_US", code="44054006"),
            CodeRef(source="RXNORM", code="86097"),
        ]
        results = find_via_walk(
            codes, prepared_db,
            target_sources=["ICD10CM", "SNOMEDCT_US"], direction="self",
        )
        # 44054006 → E11.9 (ICD10CM); 86097 → 372567009 (SNOMEDCT_US)
        targets = {(m.source.code, m.target.code) for m in results}
        assert ("44054006", "E11.9") in targets
        assert ("86097", "372567009") in targets
