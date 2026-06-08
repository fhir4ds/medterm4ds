"""Tests for non-RxNorm patient-friendly resolution using prepared tables.

Creates synthetic DuckDB databases with the required mt4ds prepared tables
and verifies source-specific workflows for ICD10CM, SNOMEDCT_US, CVX, LOINC,
and mixed-source batches.
"""

from __future__ import annotations

import duckdb
import pytest

from medterm4ds import get_patient_friendly_names
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.patient_friendly_prepared import (
    get_non_rxnorm_patient_friendly,
)

# ---------------------------------------------------------------------------
# Helpers to create synthetic mt4ds tables
# ---------------------------------------------------------------------------


def _create_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS mt4ds")


def _create_best_atoms(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.best_atoms (
            source VARCHAR,
            code VARCHAR,
            aui VARCHAR,
            cui VARCHAR,
            tty VARCHAR,
            name VARCHAR,
            suppress VARCHAR,
            is_active BOOLEAN,
            rank INTEGER
        )
        """
    )


def _create_walk_edges(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.walk_edges (
            source VARCHAR,
            from_code VARCHAR,
            from_aui VARCHAR,
            from_cui VARCHAR,
            from_tty VARCHAR,
            to_code VARCHAR,
            to_aui VARCHAR,
            to_cui VARCHAR,
            to_tty VARCHAR,
            relationship VARCHAR,
            direction VARCHAR,
            edge_source VARCHAR
        )
        """
    )


def _create_friendly_atoms(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.friendly_atoms (
            cui VARCHAR,
            source VARCHAR,
            code VARCHAR,
            aui VARCHAR,
            tty VARCHAR,
            name VARCHAR,
            friendly_source VARCHAR,
            is_broad BOOLEAN,
            is_heading BOOLEAN
        )
        """
    )


def _create_same_cui_edges(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.same_cui_edges (
            source VARCHAR,
            code VARCHAR,
            cui VARCHAR,
            target_source VARCHAR,
            target_code VARCHAR,
            target_aui VARCHAR,
            target_cui VARCHAR,
            target_tty VARCHAR
        )
        """
    )


def _promote_same_cui_to_crosswalk_edges(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.crosswalk_edges AS
        SELECT
          source,
          code,
          cui,
          target_source,
          target_code,
          target_aui,
          target_cui,
          target_tty,
          'same_cui' AS relationship,
          'same_cui' AS match_type,
          0 AS match_depth,
          'same_cui_edges' AS edge_source,
          0 AS priority
        FROM mt4ds.same_cui_edges
        """
    )
    con.execute("DROP TABLE mt4ds.same_cui_edges")


def _create_snomed_top_level_depth(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.snomed_top_level_depth (
            code VARCHAR,
            min_top_depth INTEGER
        )
        """
    )


def _create_cvx_metadata(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.cvx_metadata (
            code VARCHAR,
            group_name VARCHAR,
            short_name VARCHAR
        )
        """
    )


def _create_patient_friendly_strategy(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.patient_friendly_strategy (
            source VARCHAR,
            phase VARCHAR,
            walk_kind VARCHAR,
            target_source VARCHAR,
            target_tty VARCHAR,
            match_type VARCHAR,
            priority INTEGER,
            max_depth INTEGER,
            stop_on_hit BOOLEAN,
            guard VARCHAR
        )
        """
    )


def _setup_all_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create all required mt4ds prepared tables."""
    _create_schema(con)
    _create_best_atoms(con)
    _create_walk_edges(con)
    _create_friendly_atoms(con)
    _create_same_cui_edges(con)
    _create_snomed_top_level_depth(con)
    _create_cvx_metadata(con)
    _create_patient_friendly_strategy(con)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def con():
    """Create an in-memory DuckDB with all required mt4ds tables."""
    c = duckdb.connect(":memory:")
    _setup_all_tables(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Test 1: ICD10CM native hierarchy walk finds CHV candidate
# ---------------------------------------------------------------------------


def test_icd10cm_native_walk_finds_chv(con: duckdb.DuckDBPyConnection) -> None:
    """ICD10CM code walks native parents and finds a CHV friendly name."""
    # Seed best_atoms: child and parent ICD10CM codes
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
            ("ICD10CM", "E11", "A_E11", "C_DIAB_PARENT", "HT",
             "Type 2 diabetes mellitus", "N", True, 1),
        ],
    )
    # Walk edge: E11.9 -> E11
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "E11", "A_E11", "C_DIAB_PARENT", "HT",
             "isa", "parent", "test"),
        ],
    )
    # Friendly atom on parent CUI
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB_PARENT", "CHV", "CHV_DIAB", "A_CHV_DIAB", "PT",
             "diabetes", "CHV", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="E11.9")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "diabetes"
    assert r.friendly_source == "CHV"
    assert r.match_type == "broader"
    assert r.match_depth == 1
    assert r.technical_name == "Type 2 diabetes mellitus without complications"


def test_icd10cm_exact_same_cui_friendly_hit_is_exact(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Native depth-0 MEDLINEPLUS/CHV hits are exact same-CUI candidates."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB", "MEDLINEPLUS", "MP_DIAB", "A_MP_DIAB", "MH",
             "Diabetes", "MEDLINEPLUS", False, False),
        ],
    )

    result = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="E11.9")],
        con,
    )[0]

    assert result.name == "Diabetes"
    assert result.friendly_source == "MEDLINEPLUS"
    assert result.match_type == "exact"
    assert result.match_depth == 0
    assert result.matched_via is not None
    assert result.matched_via.steps[1].op == "exact_same_cui"


def test_local_engine_uses_prepared_patient_friendly_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Public LocalDuckDBEngine can resolve patient-friendly names without raw UMLS tables."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
            ("ICD10CM", "E11", "A_E11", "C_DIAB_PARENT", "HT",
             "Type 2 diabetes mellitus", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "E11", "A_E11", "C_DIAB_PARENT", "HT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB_PARENT", "CHV", "CHV_DIAB", "A_CHV_DIAB", "PT",
             "diabetes", "CHV", False, False),
        ],
    )

    result = get_patient_friendly_names(
        [CodeRef(source="ICD10CM", code="E11.9")],
        LocalDuckDBEngine(con),
    )[0]

    assert result.name == "diabetes"
    assert result.friendly_source == "CHV"
    assert result.match_type == "broader"


def test_icd10cm_returns_original_when_no_friendly(con: duckdb.DuckDBPyConnection) -> None:
    """ICD10CM code with no friendly atoms returns original display."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "Z99.9", "A_Z999", "C_UNKNOWN", "PT",
             "Dependence on enabling machines", "N", True, 1),
        ],
    )
    # No walk edges, no friendly atoms for this CUI

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="Z99.9")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "original"
    assert r.name == "Dependence on enabling machines"
    assert r.friendly_source == "ICD10CM"


# ---------------------------------------------------------------------------
# Test 3: SNOMED exact target routing to ICD10CM
# ---------------------------------------------------------------------------


def test_snomed_exact_target_routing_to_icd10cm(con: duckdb.DuckDBPyConnection) -> None:
    """SNOMED code crosswalks to ICD10CM and finds friendly name via target."""
    # SNOMED best atom
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "73211009", "A_SN_DIAB", "C_DIAB", "PT",
             "Diabetes mellitus type 2", "N", True, 1),
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
        ],
    )
    # Same-CUI crosswalk: SNOMED -> ICD10CM
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "73211009", "C_DIAB", "ICD10CM", "E11.9",
             "A_E119", "C_DIAB", "PT"),
        ],
    )
    _promote_same_cui_to_crosswalk_edges(con)
    # Friendly atom on the shared CUI
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB", "MEDLINEPLUS", "MP_DIAB", "A_MP_DIAB", "MH",
             "Type 2 Diabetes", "MEDLINEPLUS", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="SNOMEDCT_US", code="73211009")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "Type 2 Diabetes"
    assert r.friendly_source == "MEDLINEPLUS"
    assert r.match_type == "same_cui"


def test_snomed_walks_target_hierarchy_before_direct_snomed(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """SNOMED routes through target source hierarchy before direct SNOMED walk."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "111", "A_SN_111", "C_SN", "PT",
             "Specific SNOMED concept", "N", True, 1),
            ("ICD10CM", "A01.1", "A_A011", "C_TARGET", "PT",
             "Specific ICD target", "N", True, 1),
            ("ICD10CM", "A01", "A_A01", "C_TARGET_PARENT", "HT",
             "ICD target parent", "N", True, 1),
            ("SNOMEDCT_US", "222", "A_SN_222", "C_SN_PARENT", "PT",
             "Direct SNOMED parent", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "111", "C_SN", "ICD10CM", "A01.1",
             "A_A011", "C_TARGET", "PT"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A01.1", "A_A011", "C_TARGET", "PT",
             "A01", "A_A01", "C_TARGET_PARENT", "HT",
             "isa", "parent", "test"),
            ("SNOMEDCT_US", "111", "A_SN_111", "C_SN", "PT",
             "222", "A_SN_222", "C_SN_PARENT", "PT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [("111", 6), ("222", 5)],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_TARGET_PARENT", "MEDLINEPLUS", "MP_TARGET", "A_MP_TARGET", "MH",
             "Target Hierarchy Friendly", "MEDLINEPLUS", False, False),
            ("C_SN_PARENT", "MEDLINEPLUS", "MP_SNOMED", "A_MP_SNOMED", "MH",
             "Direct SNOMED Friendly", "MEDLINEPLUS", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="SNOMEDCT_US", code="111")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "Target Hierarchy Friendly"
    assert r.friendly_source == "MEDLINEPLUS"
    assert r.match_type == "snomed_to_target_native_hierarchy"
    assert r.match_depth == 1


# ---------------------------------------------------------------------------
# Test 4: SNOMED guard blocks overly broad results
# ---------------------------------------------------------------------------


def test_snomed_guard_blocks_overly_broad(con: duckdb.DuckDBPyConnection) -> None:
    """SNOMED walk rejects ancestors with min_top_depth <= guard depth."""
    # SNOMED code with no crosswalk targets
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "12345", "A_SN_12345", "C_SPECIFIC", "PT",
             "Specific disorder", "N", True, 1),
            ("SNOMEDCT_US", "99999", "A_SN_99999", "C_BROAD", "PT",
             "Clinical finding", "N", True, 1),
        ],
    )
    # Walk edge from specific to broad SNOMED code
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "12345", "A_SN_12345", "C_SPECIFIC", "PT",
             "99999", "A_SN_99999", "C_BROAD", "PT",
             "isa", "parent", "test"),
        ],
    )
    # Broad ancestor has min_top_depth = 1 (too shallow, should be blocked)
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [("99999", 1)],
    )
    # Friendly atom on the broad CUI -- should NOT be used due to guard
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_BROAD", "CHV", "CHV_BROAD", "A_CHV_BROAD", "PT",
             "clinical finding", "CHV", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="SNOMEDCT_US", code="12345")], con
    )
    assert len(results) == 1
    r = results[0]
    # Guard should have blocked the broad result
    assert r.match_type == "original"
    assert r.name == "Specific disorder"


# ---------------------------------------------------------------------------
# Test 5: CVX returns original display
# ---------------------------------------------------------------------------


def test_cvx_returns_original_display(con: duckdb.DuckDBPyConnection) -> None:
    """CVX code without group metadata returns original display."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("CVX", "207", "A_CVX_207", "C_CVX_207", "PT",
             "COVID-19 vaccine, mRNA", "N", True, 1),
        ],
    )
    # No cvx_metadata rows for this code

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="CVX", code="207")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "original"
    assert r.name == "COVID-19 vaccine, mRNA"
    assert r.friendly_source == "CVX"


# ---------------------------------------------------------------------------
# Test 6: LOINC returns original when no tiers match
# ---------------------------------------------------------------------------


def test_loinc_returns_original_when_no_tiers(con: duckdb.DuckDBPyConnection) -> None:
    """LOINC code with no friendly candidates returns original display."""
    con.execute("CREATE SCHEMA IF NOT EXISTS main")
    con.execute("CREATE TABLE IF NOT EXISTS main.mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR)")
    con.execute("CREATE TABLE IF NOT EXISTS mt4ds.atoms (source VARCHAR, code VARCHAR, aui VARCHAR, cui VARCHAR, tty VARCHAR, name VARCHAR, suppress VARCHAR, is_active BOOLEAN)")
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("LNC", "12345-6", "A_LNC_12345", "C_LNC_12345", "LN",
             "Some obscure lab test", "N", True, 1),
        ],
    )
    # No friendly atoms, no walk edges, no SNOMED crosswalk

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="LNC", code="12345-6")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "original"
    assert r.name == "Some obscure lab test"


# ---------------------------------------------------------------------------
# Test 7: MEDLINEPLUS preferred over CHV at same depth
# ---------------------------------------------------------------------------


def test_medlineplus_preferred_over_chv_same_depth(con: duckdb.DuckDBPyConnection) -> None:
    """When MEDLINEPLUS and CHV are at the same depth, MEDLINEPLUS wins."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "J00", "A_J00", "C_COLD", "PT",
             "Acute nasopharyngitis", "N", True, 1),
            ("ICD10CM", "J06", "A_J06", "C_URTI", "HT",
             "Upper respiratory infection", "N", True, 1),
        ],
    )
    # Walk edge: J00 -> J06
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "J00", "A_J00", "C_COLD", "PT",
             "J06", "A_J06", "C_URTI", "HT",
             "isa", "parent", "test"),
        ],
    )
    # Both MEDLINEPLUS and CHV on the same parent CUI
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_URTI", "MEDLINEPLUS", "MP_URTI", "A_MP_URTI", "MH",
             "Upper Respiratory Infections", "MEDLINEPLUS", False, False),
            ("C_URTI", "CHV", "CHV_URTI", "A_CHV_URTI", "PT",
             "cold and flu", "CHV", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="J00")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "Upper Respiratory Infections"
    assert r.friendly_source == "MEDLINEPLUS"


def test_closest_frontier_beats_farther_medlineplus(con: duckdb.DuckDBPyConnection) -> None:
    """A closer acceptable CHV hit wins over a farther MEDLINEPLUS ancestor."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "J00", "A_J00", "C_COLD", "PT",
             "Acute nasopharyngitis", "N", True, 1),
            ("ICD10CM", "J06", "A_J06", "C_URTI", "HT",
             "Upper respiratory infection", "N", True, 1),
            ("ICD10CM", "J00-J99", "A_J00J99", "C_RESP", "HT",
             "Diseases of the respiratory system", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "J00", "A_J00", "C_COLD", "PT",
             "J06", "A_J06", "C_URTI", "HT",
             "isa", "parent", "test"),
            ("ICD10CM", "J06", "A_J06", "C_URTI", "HT",
             "J00-J99", "A_J00J99", "C_RESP", "HT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_URTI", "CHV", "CHV_URTI", "A_CHV_URTI", "PT",
             "cold and flu", "CHV", False, False),
            ("C_RESP", "MEDLINEPLUS", "MP_RESP", "A_MP_RESP", "MH",
             "Respiratory Diseases", "MEDLINEPLUS", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="J00")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "cold and flu"
    assert r.friendly_source == "CHV"
    assert r.match_depth == 1


# ---------------------------------------------------------------------------
# Test 8: Batch processing preserves input order
# ---------------------------------------------------------------------------


def test_batch_preserves_input_order(con: duckdb.DuckDBPyConnection) -> None:
    """Multiple codes in one batch preserve input order in results."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A01", "A_A01", "C_TYPH", "PT",
             "Typhoid fever", "N", True, 1),
            ("ICD10CM", "B01", "A_B01", "C_POX", "PT",
             "Chickenpox", "N", True, 1),
            ("ICD10CM", "C01", "A_C01", "C_TUMOR", "PT",
             "Malignant neoplasm", "N", True, 1),
        ],
    )

    codes = [
        CodeRef(source="ICD10CM", code="C01"),
        CodeRef(source="ICD10CM", code="A01"),
        CodeRef(source="ICD10CM", code="B01"),
    ]
    results = get_non_rxnorm_patient_friendly(codes, con)

    assert len(results) == 3
    assert results[0].code.code == "C01"
    assert results[1].code.code == "A01"
    assert results[2].code.code == "B01"


# ---------------------------------------------------------------------------
# Test 9: Mixed sources in one batch
# ---------------------------------------------------------------------------


def test_mixed_sources_in_one_batch(con: duckdb.DuckDBPyConnection) -> None:
    """Batch with ICD10CM, CVX, and LOINC codes processes each correctly."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # ICD10CM with friendly
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus", "N", True, 1),
            # CVX
            ("CVX", "207", "A_CVX_207", "C_CVX_207", "PT",
             "COVID-19 vaccine, mRNA", "N", True, 1),
            # LOINC
            ("LNC", "2345-7", "A_LNC_2345", "C_LNC_2345", "LN",
             "Glucose lab test", "N", True, 1),
        ],
    )
    # Friendly for ICD10CM (same CUI)
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB", "MEDLINEPLUS", "MP_DIAB", "A_MP_DIAB", "MH",
             "Diabetes", "MEDLINEPLUS", False, False),
        ],
    )

    codes = [
        CodeRef(source="CVX", code="207"),
        CodeRef(source="ICD10CM", code="E11.9"),
        CodeRef(source="LNC", code="2345-7"),
    ]
    results = get_non_rxnorm_patient_friendly(codes, con)

    assert len(results) == 3
    # CVX -> original
    assert results[0].code.source == "CVX"
    assert results[0].match_type == "original"
    # ICD10CM -> found friendly
    assert results[1].code.source == "ICD10CM"
    assert results[1].name == "Diabetes"
    assert results[1].friendly_source == "MEDLINEPLUS"
    # LOINC -> original (no friendly atoms)
    assert results[2].code.source == "LNC"
    assert results[2].match_type == "original"


# ---------------------------------------------------------------------------
# Test 10: SNOMED fallback from ICD10CM
# ---------------------------------------------------------------------------


def test_snomed_fallback_from_icd10cm(con: duckdb.DuckDBPyConnection) -> None:
    """ICD10CM code with no native friendly candidate falls back via SNOMED."""
    # ICD10CM code with no friendly in native hierarchy
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A00.0", "A_A00", "C_CHOLERA", "PT",
             "Cholera due to Vibrio cholerae", "N", True, 1),
            ("SNOMEDCT_US", "63650001", "A_SN_CHOLERA", "C_CHOLERA", "PT",
             "Cholera", "N", True, 1),
            ("SNOMEDCT_US", "40930007", "A_SN_INFECT", "C_INFECT", "PT",
             "Intestinal infectious disease", "N", True, 1),
        ],
    )
    # Crosswalk: ICD10CM A00.0 -> SNOMED 63650001 (same CUI)
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A00.0", "C_CHOLERA", "SNOMEDCT_US", "63650001",
             "A_SN_CHOLERA", "C_CHOLERA", "PT"),
        ],
    )
    _promote_same_cui_to_crosswalk_edges(con)
    # SNOMED walk: 63650001 -> 40930007
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "63650001", "A_SN_CHOLERA", "C_CHOLERA", "PT",
             "40930007", "A_SN_INFECT", "C_INFECT", "PT",
             "isa", "parent", "test"),
        ],
    )
    # SNOMED ancestor is deep enough (min_top_depth > 3)
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [
            ("63650001", 6),
            ("40930007", 5),
        ],
    )
    # Friendly atom on the SNOMED ancestor CUI
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_INFECT", "MEDLINEPLUS", "MP_INFECT", "A_MP_INFECT", "MH",
             "Intestinal Infections", "MEDLINEPLUS", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="A00.0")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "Intestinal Infections"
    assert r.friendly_source == "MEDLINEPLUS"
    assert r.match_type == "snomed_fallback"
    assert r.technical_name == "Cholera due to Vibrio cholerae"


def test_snomed_fallback_from_source_ancestor(con: duckdb.DuckDBPyConnection) -> None:
    """ICD10CM fallback can crosswalk via a parent source code, not just direct code."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "K00.0", "A_K00_0", "C_K00_ORIGINAL", "PT",
             "K00.0 child concept", "N", True, 1),
            ("ICD10CM", "K00", "A_K00", "C_K00_PARENT", "HT",
             "K00 parent concept", "N", True, 1),
            ("SNOMEDCT_US", "63650001", "A_SN_CHOLERA", "C_CHOLERA", "PT",
             "Cholera", "N", True, 1),
            ("SNOMEDCT_US", "40930007", "A_SN_INFECT", "C_INFECT", "PT",
             "Intestinal infectious disease", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "K00.0", "A_K00_0", "C_K00_ORIGINAL", "PT",
             "K00", "A_K00", "C_K00_PARENT", "HT",
             "isa", "parent", "test"),
            ("SNOMEDCT_US", "63650001", "A_SN_CHOLERA", "C_CHOLERA", "PT",
             "40930007", "A_SN_INFECT", "C_INFECT", "PT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "K00", "C_K00_PARENT", "SNOMEDCT_US", "63650001",
             "A_SN_CHOLERA", "C_CHOLERA", "PT"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [("63650001", 6), ("40930007", 5)],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_INFECT", "MEDLINEPLUS", "MP_INFECT", "A_MP_INFECT", "MH",
             "Intestinal Infections", "MEDLINEPLUS", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="K00.0")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "Intestinal Infections"
    assert r.friendly_source == "MEDLINEPLUS"
    assert r.match_type == "snomed_fallback"
    # Source parent depth (1) + SNOMED depth (1) to the friendly node
    assert r.match_depth == 2


def test_snomed_fallback_skips_unrelated_combo_chv_candidate(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Combination source names should not accept unrelated CHV fallback labels."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "F14.959", "A_F14959", "C_SOURCE_COMBO", "PT",
             "Cocaine use with cocaine-induced psychotic disorder", "N", True, 1),
            ("SNOMEDCT_US", "100001", "A_SN_BAD", "C_SN_BAD", "PT",
             "Unrelated SNOMED combo seed", "N", True, 1),
            ("SNOMEDCT_US", "100002", "A_SN_GOOD", "C_SN_GOOD", "PT",
             "Substance-related disorder", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "F14.959", "C_SOURCE_COMBO", "SNOMEDCT_US", "100001",
             "A_SN_BAD", "C_SN_BAD", "PT"),
        ],
    )
    _promote_same_cui_to_crosswalk_edges(con)
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "100001", "A_SN_BAD", "C_SN_BAD", "PT",
             "100002", "A_SN_GOOD", "C_SN_GOOD", "PT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [("100001", 6), ("100002", 5)],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_SN_BAD", "CHV", "CHV_BAD", "A_CHV_BAD", "PT",
             "brain findings", "CHV", False, False),
            ("C_SN_GOOD", "CHV", "CHV_GOOD", "A_CHV_GOOD", "PT",
             "cocaine related disorders", "CHV", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="F14.959")],
        con,
    )

    assert len(results) == 1
    assert results[0].name == "cocaine related disorders"
    assert results[0].match_type == "snomed_fallback"
    assert results[0].match_depth == 1


def test_cvx_combination_metadata_aggregates_components(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """CVX combination vaccines aggregate component group rows."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("CVX", "102", "A_CVX_102", "C_CVX_102", "PT",
             "DTaP-Hib-Hep B vaccine", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.cvx_metadata VALUES (?, ?, ?)",
        [
            ("102", "DTAP", "DTAP"),
            ("102", "HIB", "HIB"),
            ("102", "HepB", "HepB"),
        ],
    )

    result = get_non_rxnorm_patient_friendly(
        [CodeRef(source="CVX", code="102")],
        con,
    )[0]

    assert result.name == "DTAP / HIB / HepB"
    assert result.friendly_source == "CVX"
    assert result.match_type == "cvx_group"


def test_icd10_s43_uses_explicit_umls_parent_not_unrelated_snomed(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """S43 should not jump to unrelated Head Injuries when native UMLS parent hits."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "S43", "A_S43", "C_S43", "HT",
             "Dislocation and sprain of joints and ligaments of shoulder girdle", "N", True, 1),
            ("ICD10CM", "S40-S49", "A_S40S49", "C_INJURY_BLOCK", "HT",
             "Injuries to the shoulder and upper arm", "N", True, 1),
            ("SNOMEDCT_US", "HEAD", "A_HEAD", "C_HEAD", "PT",
             "Head injury", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "S43", "A_S43", "C_S43", "HT",
             "S40-S49", "A_S40S49", "C_INJURY_BLOCK", "HT",
             "isa", "parent", "umls_mrrel"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_INJURY_BLOCK", "MEDLINEPLUS", "MP_INJURY", "A_MP_INJURY", "MH",
             "Injuries", "MEDLINEPLUS", False, False),
            ("C_HEAD", "MEDLINEPLUS", "MP_HEAD", "A_MP_HEAD", "MH",
             "Head Injuries", "MEDLINEPLUS", False, False),
        ],
    )

    result = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="S43")],
        con,
    )[0]

    assert result.name == "Injuries"
    assert result.name != "Head Injuries"
    assert result.match_type == "broader"
    assert result.match_depth == 1


def test_cpt_generic_operation_candidate_is_blocked(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """CPT generic operation/surgery labels should not replace useful display."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("CPT", "11644", "A_CPT_11644", "C0038894", "PT",
             "Removal of cancer skin growth of face, 3.1-4.0 cm", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C0038894", "MEDLINEPLUS", "MP_OPERATION", "A_MP_OPERATION", "MH",
             "Operation", "MEDLINEPLUS", False, False),
        ],
    )

    result = get_non_rxnorm_patient_friendly(
        [CodeRef(source="CPT", code="11644")],
        con,
    )[0]

    assert result.name == "Removal of cancer skin growth of face, 3.1-4.0 cm"
    assert result.friendly_source == "CPT"
    assert result.match_type == "original"


def test_cpt_50580_reaches_nephroscopy_through_cpt_hierarchy(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """CPT 50580 should keep useful CPT hierarchy route to CHV nephroscopy."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("CPT", "50580", "A_CPT_50580", "C_50580", "PT",
             "Removal of foreign body or stone in kidney using an endoscope", "N", True, 1),
            ("CPT", "1007000", "A_CPT_1007000", "C_PARENT", "HT",
             "Endoscopy Procedures on the Urinary System", "N", True, 1),
            ("CPT", "1008152", "A_CPT_1008152", "C0194135", "HT",
             "Endoscopy Procedures on the Kidney", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("CPT", "50580", "A_CPT_50580", "C_50580", "PT",
             "1007000", "A_CPT_1007000", "C_PARENT", "HT",
             "isa", "parent", "umls_mrrel"),
            ("CPT", "1007000", "A_CPT_1007000", "C_PARENT", "HT",
             "1008152", "A_CPT_1008152", "C0194135", "HT",
             "isa", "parent", "umls_mrrel"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C0194135", "CHV", "0000019534", "A_CHV_NEPHROSCOPY", "PT",
             "nephroscopy", "CHV", False, False),
        ],
    )

    result = get_non_rxnorm_patient_friendly(
        [CodeRef(source="CPT", code="50580")],
        con,
    )[0]

    assert result.name == "nephroscopy"
    assert result.friendly_source == "CHV"
    assert result.match_type == "broader"
    assert result.match_depth == 2


def test_snomed_drug_product_routes_to_rxnorm_strategy(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """SNOMED drug/product concepts with RxNorm routes should use RxNorm strategy."""
    con.execute(
        """
        CREATE TABLE mt4ds.rxnorm_tty_paths (
            path_id INTEGER,
            start_tty VARCHAR,
            target_tty VARCHAR,
            match_type VARCHAR,
            target_order INTEGER,
            path_depth INTEGER
        )
        """
    )
    con.execute(
        """
        CREATE TABLE mt4ds.rxnorm_tty_path_steps (
            path_id INTEGER,
            step INTEGER,
            tty VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE mt4ds.rxnorm_tty_edges (
            source_aui VARCHAR,
            source_code VARCHAR,
            source_tty VARCHAR,
            source_name VARCHAR,
            source_suppress VARCHAR,
            target_aui VARCHAR,
            target_code VARCHAR,
            target_tty VARCHAR,
            target_name VARCHAR,
            target_suppress VARCHAR,
            rel VARCHAR,
            rela VARCHAR
        )
        """
    )
    con.executemany(
        "INSERT INTO mt4ds.patient_friendly_strategy VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("RXNORM", "topology", "tty_traversal", "RXNORM", "SCDG", "group", 0, 1, True, None),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.rxnorm_tty_paths VALUES (?, ?, ?, ?, ?, ?)",
        [(1, "SCD", "SCDG", "group", 0, 1)],
    )
    con.executemany(
        "INSERT INTO mt4ds.rxnorm_tty_path_steps VALUES (?, ?, ?)",
        [(1, 0, "SCD"), (1, 1, "SCDG")],
    )
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "769135007", "A_SN_DRUG", "C_SN_DRUG", "PT",
             "Product containing precisely rifampicin oral suspension (clinical drug)", "N", True, 1),
            ("RXNORM", "12345", "A_RX_SCD", "C_RX_SCD", "SCD",
             "Rifampin 20 mg/mL Oral Suspension", "N", True, 1),
            ("RXNORM", "67890", "A_RX_SCDG", "C_RX_SCDG", "SCDG",
             "rifampin Oral Liquid Product", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "769135007", "C_SN_DRUG", "RXNORM", "12345",
             "A_RX_SCD", "C_RX_SCD", "SCD"),
        ],
    )
    _promote_same_cui_to_crosswalk_edges(con)
    con.executemany(
        "INSERT INTO mt4ds.rxnorm_tty_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("A_RX_SCD", "12345", "SCD", "Rifampin 20 mg/mL Oral Suspension", "N",
             "A_RX_SCDG", "67890", "SCDG", "rifampin Oral Liquid Product", "N",
             "RN", "has_tradename"),
        ],
    )

    result = get_non_rxnorm_patient_friendly(
        [CodeRef(source="SNOMEDCT_US", code="769135007")],
        con,
    )[0]

    assert result.name == "rifampin Oral Liquid Product"
    assert result.friendly_source == "RXNORM"
    assert result.match_type == "group"
    assert result.match_depth == 1
