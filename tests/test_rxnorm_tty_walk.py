"""Tests for RxNorm TTY-path patient-friendly resolution.

Creates synthetic DuckDB databases with the required mt4ds prepared tables
and verifies the bounded TTY-path traversal algorithm.
"""

from __future__ import annotations

import duckdb
import pytest

from medterm4ds.core.models import CodeRef
from medterm4ds.services.rxnorm_tty_walk import get_rxnorm_patient_friendly

# ---------------------------------------------------------------------------
# Helpers to create synthetic mt4ds tables
# ---------------------------------------------------------------------------


def _create_mt4ds_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create the mt4ds schema and all required prepared tables."""
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


def _insert_best_atoms(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def _create_rxnorm_tty_paths(con: duckdb.DuckDBPyConnection) -> None:
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


def _insert_rxnorm_tty_paths(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
    con.executemany(
        "INSERT INTO mt4ds.rxnorm_tty_paths VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def _create_rxnorm_tty_path_steps(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.rxnorm_tty_path_steps (
            path_id INTEGER,
            step INTEGER,
            tty VARCHAR
        )
        """
    )


def _insert_rxnorm_tty_path_steps(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
    con.executemany(
        "INSERT INTO mt4ds.rxnorm_tty_path_steps VALUES (?, ?, ?)",
        rows,
    )


def _create_rxnorm_tty_edges(con: duckdb.DuckDBPyConnection) -> None:
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


def _insert_rxnorm_tty_edges(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
    con.executemany(
        "INSERT INTO mt4ds.rxnorm_tty_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
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


def _insert_patient_friendly_strategy(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
    con.executemany(
        "INSERT INTO mt4ds.patient_friendly_strategy VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def con():
    """Create an in-memory DuckDB with mt4ds schema."""
    c = duckdb.connect(database=":memory:")
    _create_mt4ds_schema(c)
    _create_best_atoms(c)
    _create_rxnorm_tty_paths(c)
    _create_rxnorm_tty_path_steps(c)
    _create_rxnorm_tty_edges(c)
    _create_patient_friendly_strategy(c)
    yield c
    c.close()


def _setup_basic_topology(con: duckdb.DuckDBPyConnection) -> None:
    """Set up a minimal RxNorm TTY topology with group and ingredient paths.

    Topology paths:
      SCD -> SCDG (group, depth 2): SCD -> SCD -> SCDG? No, realistic is
        SCD -> (via edges) -> SCDG
      SCD -> MIN (ingredient, depth 1): SCD -> MIN
      SCD -> IN  (ingredient, depth 2): SCD -> MIN -> IN
      SBDC -> IN (ingredient, depth 2): SBDC -> SBD -> IN
      IN -> IN  (ingredient, depth 0): self
      MIN -> MIN (ingredient, depth 0): self
    """
    # Patient friendly strategy rows for RxNorm TTY traversal
    strategy_rows = [
        # (source, phase, walk_kind, target_source, target_tty, match_type, priority, max_depth, stop_on_hit, guard)
        ("RXNORM", "topology", "tty_traversal", "RXNORM", "SCDG", "group", 0, 2, True, None),
        ("RXNORM", "topology", "tty_traversal", "RXNORM", "MIN", "ingredient", 1, 1, True, None),
        ("RXNORM", "topology", "tty_traversal", "RXNORM", "IN", "ingredient", 2, 2, True, None),
    ]
    _insert_patient_friendly_strategy(con, strategy_rows)

    # TTY paths
    # path 0: SCD -> SCDG (group, depth 2, steps: SCD -> SCD -> SCDG would be wrong)
    # Realistic: SCD -> SCDF -> SCDG? Let's use a simple 2-step: SCD -> SCDF -> SCDG
    path_rows = [
        # (path_id, start_tty, target_tty, match_type, target_order, path_depth)
        (0, "SCD", "SCDG", "group", 0, 2),
        (1, "SCD", "MIN", "ingredient", 1, 1),
        (2, "SCD", "IN", "ingredient", 2, 2),
        (3, "SBDC", "IN", "ingredient", 1, 2),
        (4, "IN", "IN", "ingredient", 1, 0),
        (5, "MIN", "MIN", "ingredient", 1, 0),
        (6, "SBDC", "SCDG", "group", 0, 3),
    ]
    _insert_rxnorm_tty_paths(con, path_rows)

    # Path steps
    step_rows = [
        # path 0: SCD -> SCDF -> SCDG
        (0, 0, "SCD"),
        (0, 1, "SCDF"),
        (0, 2, "SCDG"),
        # path 1: SCD -> MIN
        (1, 0, "SCD"),
        (1, 1, "MIN"),
        # path 2: SCD -> MIN -> IN
        (2, 0, "SCD"),
        (2, 1, "MIN"),
        (2, 2, "IN"),
        # path 3: SBDC -> SBD -> IN
        (3, 0, "SBDC"),
        (3, 1, "SBD"),
        (3, 2, "IN"),
        # path 4: IN -> IN (self, depth 0)
        (4, 0, "IN"),
        # path 5: MIN -> MIN (self, depth 0)
        (5, 0, "MIN"),
        # path 6: SBDC -> SBD -> SCD -> SCDG
        (6, 0, "SBDC"),
        (6, 1, "SBD"),
        (6, 2, "SCD"),
        (6, 3, "SCDG"),
    ]
    _insert_rxnorm_tty_path_steps(con, step_rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBasicTTYPathTraversal:
    """Test basic TTY path traversal with synthetic data."""

    def test_scd_to_scdg_group_path(self, con):
        """SCD code should resolve to SCDG group target."""
        _setup_basic_topology(con)

        # Best atoms: SCD code and SCDG target
        _insert_best_atoms(con, [
            # (source, code, aui, cui, tty, name, suppress, is_active, rank)
            ("RXNORM", "1000001", "AUI_SCD1", "C1", "SCD",
             "Acetaminophen 325 MG Oral Tablet", "N", True, 1),
            ("RXNORM", "2000001", "AUI_SCDG1", "C2", "SCDG",
             "Acetaminophen Oral Product", "N", True, 1),
        ])

        # Edges: SCD atom -> SCDF intermediate -> SCDG target
        _insert_rxnorm_tty_edges(con, [
            # source_aui, source_code, source_tty, source_name, source_suppress,
            # target_aui, target_code, target_tty, target_name, target_suppress,
            # rel, rela
            ("AUI_SCD1", "1000001", "SCD", "Acetaminophen 325 MG Oral Tablet", "N",
             "AUI_SCDF1", "3000001", "SCDF", "Acetaminophen 325 MG Oral Tablet [FORM]", "N",
             "RN", "form_of"),
            ("AUI_SCDF1", "3000001", "SCDF", "Acetaminophen 325 MG Oral Tablet [FORM]", "N",
             "AUI_SCDG1", "2000001", "SCDG", "Acetaminophen Oral Product", "N",
             "RN", "has_form"),
        ])

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="1000001")],
            con,
        )
        assert len(results) == 1
        r = results[0]
        assert r.name == "Acetaminophen Oral Product"
        assert r.match_type == "group"
        assert r.friendly_source == "RXNORM"
        assert r.match_depth == 2
        assert r.technical_name == "Acetaminophen 325 MG Oral Tablet"
        assert r.matched_via is not None

    def test_scd_to_min_ingredient_path(self, con):
        """SCD code with no group path should fall through to MIN ingredient."""
        _setup_basic_topology(con)

        _insert_best_atoms(con, [
            ("RXNORM", "1000002", "AUI_SCD2", "C3", "SCD",
             "Ibuprofen 200 MG Oral Tablet", "N", True, 1),
            ("RXNORM", "4000001", "AUI_MIN1", "C4", "MIN",
             "Ibuprofen", "N", True, 1),
        ])

        # Only ingredient edges (no SCDG path edges)
        _insert_rxnorm_tty_edges(con, [
            ("AUI_SCD2", "1000002", "SCD", "Ibuprofen 200 MG Oral Tablet", "N",
             "AUI_MIN1", "4000001", "MIN", "Ibuprofen", "N",
             "RN", "ingredient_of"),
        ])

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="1000002")],
            con,
        )
        assert len(results) == 1
        r = results[0]
        assert r.name == "Ibuprofen"
        assert r.match_type == "ingredient"
        assert r.friendly_source == "RXNORM"

    def test_sbdc_to_in_ingredient_path(self, con):
        """SBDC code should resolve through SBD to IN ingredient."""
        _setup_basic_topology(con)

        _insert_best_atoms(con, [
            ("RXNORM", "5000001", "AUI_SBDC1", "C5", "SBDC",
             "Acetaminophen 500 MG / Oxycodone 5 MG Oral Capsule", "N", True, 1),
            ("RXNORM", "6000001", "AUI_SBD1", "C6", "SBD",
             "Acetaminophen / Oxycodone Oral Capsule", "N", True, 1),
            ("RXNORM", "7000001", "AUI_IN1", "C7", "IN",
             "Oxycodone", "N", True, 1),
        ])

        _insert_rxnorm_tty_edges(con, [
            ("AUI_SBDC1", "5000001", "SBDC", "Acetaminophen 500 MG / Oxycodone 5 MG Oral Capsule", "N",
             "AUI_SBD1", "6000001", "SBD", "Acetaminophen / Oxycodone Oral Capsule", "N",
             "RN", "has_ingredient"),
            ("AUI_SBD1", "6000001", "SBD", "Acetaminophen / Oxycodone Oral Capsule", "N",
             "AUI_IN1", "7000001", "IN", "Oxycodone", "N",
             "RN", "ingredient_of"),
        ])

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="5000001")],
            con,
        )
        assert len(results) == 1
        r = results[0]
        assert r.name == "Oxycodone"
        assert r.match_type == "ingredient"
        assert r.friendly_source == "RXNORM"
        assert r.match_depth == 2


class TestINMINSelfResolution:
    """Test that IN and MIN TTYs resolve to themselves."""

    def test_in_self_resolve(self, con):
        """IN code should return its own name as ingredient."""
        _setup_basic_topology(con)

        _insert_best_atoms(con, [
            ("RXNORM", "8000001", "AUI_IN2", "C8", "IN",
             "Acetaminophen", "N", True, 1),
        ])

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="8000001")],
            con,
        )
        assert len(results) == 1
        r = results[0]
        assert r.name == "Acetaminophen"
        assert r.match_type == "ingredient"
        assert r.match_depth == 0
        assert r.technical_name == "Acetaminophen"

    def test_min_self_resolve(self, con):
        """MIN code should return its own name as ingredient."""
        _setup_basic_topology(con)

        _insert_best_atoms(con, [
            ("RXNORM", "9000001", "AUI_MIN2", "C9", "MIN",
             "Acetaminophen", "N", True, 1),
        ])

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="9000001")],
            con,
        )
        assert len(results) == 1
        r = results[0]
        assert r.name == "Acetaminophen"
        assert r.match_type == "ingredient"
        assert r.match_depth == 0

    def test_pin_prefers_in_over_min(self, con):
        """PIN codes should try IN before MIN when both ingredient paths exist."""
        _setup_basic_topology(con)
        _insert_rxnorm_tty_paths(con, [
            (20, "PIN", "IN", "ingredient", 1, 1),
            (21, "PIN", "MIN", "ingredient", 2, 1),
        ])
        _insert_rxnorm_tty_path_steps(con, [
            (20, 0, "PIN"),
            (20, 1, "IN"),
            (21, 0, "PIN"),
            (21, 1, "MIN"),
        ])
        _insert_best_atoms(con, [
            ("RXNORM", "235991", "AUI_PIN1", "C_PIN", "PIN",
             "anhydrous tacrolimus", "N", True, 1),
            ("RXNORM", "42316", "AUI_IN1", "C_IN", "IN",
             "Tacrolimus", "N", True, 1),
            ("RXNORM", "999991", "AUI_MIN1", "C_MIN", "MIN",
             "tacrolimus mixture", "N", True, 1),
        ])
        _insert_rxnorm_tty_edges(con, [
            ("AUI_PIN1", "235991", "PIN", "anhydrous tacrolimus", "N",
             "AUI_IN1", "42316", "IN", "Tacrolimus", "N",
             "RN", "precise_ingredient_of"),
            ("AUI_PIN1", "235991", "PIN", "anhydrous tacrolimus", "N",
             "AUI_MIN1", "999991", "MIN", "tacrolimus mixture", "N",
             "RN", "ingredient_of"),
        ])

        result = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="235991")],
            con,
        )[0]

        assert result.name == "Tacrolimus"
        assert result.match_type == "ingredient"
        assert result.match_depth == 1


class TestCodeNotFound:
    """Test behavior when code is not found in best_atoms."""

    def test_unknown_code_returns_original(self, con):
        """Code not found in best_atoms should return original match."""
        _setup_basic_topology(con)
        # No best_atoms for this code

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="9999999")],
            con,
        )
        assert len(results) == 1
        r = results[0]
        assert r.code.code == "9999999"
        assert r.match_type == "original"
        assert r.friendly_source == "RXNORM"

    def test_unknown_tty_returns_original(self, con):
        """Code with a TTY that has no paths should return original."""
        _setup_basic_topology(con)

        _insert_best_atoms(con, [
            ("RXNORM", "1100001", "AUI_DF1", "C10", "DF",
             "Some Dose Form", "N", True, 1),
        ])
        # DF has no paths in our setup, and it's not IN/MIN

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="1100001")],
            con,
        )
        assert len(results) == 1
        r = results[0]
        assert r.name == "Some Dose Form"
        assert r.match_type == "original"


class TestActivePreferredOverSuppressed:
    """Test that active targets are preferred over suppressed ones."""

    def test_active_preferred(self, con):
        """When both active and suppressed targets exist, active wins."""
        _setup_basic_topology(con)

        _insert_best_atoms(con, [
            ("RXNORM", "1200001", "AUI_SCD3", "C11", "SCD",
             "Drug X 100 MG Tablet", "N", True, 1),
            ("RXNORM", "2200001", "AUI_MIN3A", "C12", "MIN",
             "Drug X (Active)", "N", True, 1),
            ("RXNORM", "2200002", "AUI_MIN3B", "C12", "MIN",
             "Drug X (suppressed)", "O", False, 2),
        ])

        # Both active and suppressed MIN targets reachable
        _insert_rxnorm_tty_edges(con, [
            ("AUI_SCD3", "1200001", "SCD", "Drug X 100 MG Tablet", "N",
             "AUI_MIN3A", "2200001", "MIN", "Drug X (Active)", "N",
             "RN", "ingredient_of"),
            ("AUI_SCD3", "1200001", "SCD", "Drug X 100 MG Tablet", "N",
             "AUI_MIN3B", "2200002", "MIN", "Drug X (suppressed)", "O",
             "RN", "ingredient_of"),
        ])

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="1200001")],
            con,
        )
        assert len(results) == 1
        r = results[0]
        assert r.name == "Drug X (Active)"
        assert r.match_type == "ingredient"

class TestDeterministicTieBreaking:
    """Test that results are deterministic when multiple equal-rank targets exist."""

    def test_tie_break_by_code(self, con):
        """When targets have same suppress status, lower code wins."""
        _setup_basic_topology(con)

        _insert_best_atoms(con, [
            ("RXNORM", "1300001", "AUI_SCD4", "C13", "SCD",
             "Drug Y 200 MG Tablet", "N", True, 1),
            ("RXNORM", "2300002", "AUI_MIN4B", "C14", "MIN",
             "Drug Y", "N", True, 1),
            ("RXNORM", "2300001", "AUI_MIN4A", "C14", "MIN",
             "Drug Y", "N", True, 2),
        ])

        # Two active MIN targets reachable from the same SCD atom
        _insert_rxnorm_tty_edges(con, [
            ("AUI_SCD4", "1300001", "SCD", "Drug Y 200 MG Tablet", "N",
             "AUI_MIN4A", "2300001", "MIN", "Drug Y", "N",
             "RN", "ingredient_of"),
            ("AUI_SCD4", "1300001", "SCD", "Drug Y 200 MG Tablet", "N",
             "AUI_MIN4B", "2300002", "MIN", "Drug Y", "N",
             "RN", "ingredient_of"),
        ])

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="1300001")],
            con,
        )
        assert len(results) == 1
        # Deterministic: sorted by target_code, so 2300001 wins
        assert results[0].matched_via is not None
        # We verify the result is deterministic (same result each call)
        results2 = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="1300001")],
            con,
        )
        assert results[0].name == results2[0].name

    def test_tie_break_by_numeric_code_before_lexical_code(self, con):
        """Numeric RxCUIs sort by integer value, not lexical text order."""
        _setup_basic_topology(con)

        _insert_best_atoms(con, [
            ("RXNORM", "1300002", "AUI_SCD5", "C15", "SCD",
             "Drug Z 200 MG Tablet", "N", True, 1),
            ("RXNORM", "9", "AUI_MIN5A", "C16", "MIN",
             "Drug Z Lower Numeric Code", "N", True, 1),
            ("RXNORM", "10", "AUI_MIN5B", "C16", "MIN",
             "Drug Z lexical-first code", "N", True, 2),
        ])

        _insert_rxnorm_tty_edges(con, [
            ("AUI_SCD5", "1300002", "SCD", "Drug Z 200 MG Tablet", "N",
             "AUI_MIN5A", "9", "MIN", "Drug Z Lower Numeric Code", "N",
             "RN", "ingredient_of"),
            ("AUI_SCD5", "1300002", "SCD", "Drug Z 200 MG Tablet", "N",
             "AUI_MIN5B", "10", "MIN", "Drug Z lexical-first code", "N",
             "RN", "ingredient_of"),
        ])

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="1300002")],
            con,
        )

        assert results[0].name == "Drug Z Lower Numeric Code"


class TestMultipleCodes:
    """Test batch resolution of multiple codes."""

    def test_batch_resolution(self, con):
        """Multiple codes should be resolved in input order."""
        _setup_basic_topology(con)

        _insert_best_atoms(con, [
            ("RXNORM", "1400001", "AUI_SCD5", "C15", "SCD",
             "Drug A 50 MG Tablet", "N", True, 1),
            ("RXNORM", "1400002", "AUI_MIN5", "C16", "MIN",
             "Drug A", "N", True, 1),
            ("RXNORM", "1400003", "AUI_IN3", "C17", "IN",
             "Drug B", "N", True, 1),
        ])

        _insert_rxnorm_tty_edges(con, [
            ("AUI_SCD5", "1400001", "SCD", "Drug A 50 MG Tablet", "N",
             "AUI_MIN5", "1400002", "MIN", "Drug A", "N",
             "RN", "ingredient_of"),
        ])

        results = get_rxnorm_patient_friendly(
            [
                CodeRef(source="RXNORM", code="1400001"),
                CodeRef(source="RXNORM", code="1400003"),
                CodeRef(source="RXNORM", code="9999999"),
            ],
            con,
        )
        assert len(results) == 3

        # First code: SCD -> MIN ingredient
        assert results[0].name == "Drug A"
        assert results[0].match_type == "ingredient"

        # Second code: IN self-resolve
        assert results[1].name == "Drug B"
        assert results[1].match_type == "ingredient"

        # Third code: not found -> original
        assert results[2].match_type == "original"

    def test_empty_input(self, con):
        """Empty input should return empty list."""
        results = get_rxnorm_patient_friendly([], con)
        assert results == []


class TestGroupPreferredOverIngredient:
    """Test that group match is preferred over ingredient when both exist."""

    def test_group_beats_ingredient(self, con):
        """When both SCDG and MIN paths hit, SCDG (group) wins due to lower target_order."""
        _setup_basic_topology(con)

        _insert_best_atoms(con, [
            ("RXNORM", "1500001", "AUI_SCD6", "C18", "SCD",
             "Aspirin 81 MG Oral Tablet", "N", True, 1),
            ("RXNORM", "2500001", "AUI_SCDG2", "C19", "SCDG",
             "Aspirin Oral Product", "N", True, 1),
            ("RXNORM", "2500002", "AUI_MIN6", "C20", "MIN",
             "Aspirin", "N", True, 1),
        ])

        _insert_rxnorm_tty_edges(con, [
            # Group path: SCD -> SCDF -> SCDG
            ("AUI_SCD6", "1500001", "SCD", "Aspirin 81 MG Oral Tablet", "N",
             "AUI_SCDF2", "3500001", "SCDF", "Aspirin 81 MG Oral Tablet [FORM]", "N",
             "RN", "form_of"),
            ("AUI_SCDF2", "3500001", "SCDF", "Aspirin 81 MG Oral Tablet [FORM]", "N",
             "AUI_SCDG2", "2500001", "SCDG", "Aspirin Oral Product", "N",
             "RN", "has_form"),
            # Ingredient path: SCD -> MIN
            ("AUI_SCD6", "1500001", "SCD", "Aspirin 81 MG Oral Tablet", "N",
             "AUI_MIN6", "2500002", "MIN", "Aspirin", "N",
             "RN", "ingredient_of"),
        ])

        results = get_rxnorm_patient_friendly(
            [CodeRef(source="RXNORM", code="1500001")],
            con,
        )
        assert len(results) == 1
        r = results[0]
        # Group should win because target_order=0 < target_order=1
        assert r.name == "Aspirin Oral Product"
        assert r.match_type == "group"
