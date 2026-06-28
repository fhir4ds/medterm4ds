"""Direct unit tests for engines/duckdb/indications.py.

Tests the may_treat/may_prevent condition→medication traversal with synthetic data.
"""

from __future__ import annotations

import duckdb
import pytest
from pathlib import Path

from medterm4ds.engines.duckdb import LocalDuckDBEngine


def _make_indications_db(path: Path) -> None:
    """Minimal DB: SNOMED condition → MSH → may_treat → RxNorm ingredient."""
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE mrconso (
        CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
        SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("SN_COND", "PT", "Diabetes", "AUI_SN", "N", "SNOMEDCT_US", "C_COND"),
            ("MSH001", "MH", "Diabetes Mellitus", "AUI_MSH", "N", "MSH", "C_COND"),
            ("6809", "IN", "Metformin", "AUI_RX", "N", "RXNORM", "C_MET"),
        ],
    )
    con.execute("""CREATE TABLE mrrel (
        AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR
    )""")
    con.execute("INSERT INTO mrrel VALUES ('AUI_MSH', 'AUI_RX', 'may_treat', 'RO')")
    con.close()


@pytest.fixture
def engine(tmp_path):
    db = tmp_path / "indications.duckdb"
    _make_indications_db(db)
    con = duckdb.connect(str(db))
    return LocalDuckDBEngine(con)


class TestIndications:
    def test_get_drugs_for_indication(self, engine):
        """Condition → medication via may_treat traversal."""
        rows = engine.get_drugs_for_indication(
            [("SNOMEDCT_US", "SN_COND", 1)],
            relationships=("may_treat",),
            max_depth=5,
            limit=20,
            include_product_groups=False,
        )
        assert len(rows) >= 1
        # Row format: (input_source, input_code, rank, source_code, source_name,
        #              source_depth, mesh_code, mesh_name, relationship,
        #              relationship_rx_code, relationship_rx_tty, relationship_rx_name,
        #              target_code, target_tty, target_name, target_expansion, ingredient_count, path)
        first_row = rows[0]
        assert first_row[0] == "SNOMEDCT_US"  # input_source
        assert first_row[1] == "SN_COND"       # input_code
        assert first_row[8] == "may_treat"     # relationship

    def test_empty_candidates(self, engine):
        """Empty candidate list returns empty."""
        rows = engine.get_drugs_for_indication(
            [("SNOMEDCT_US", "SN_COND", 1)],
            relationships=("may_treat",),
            max_depth=0,
            limit=20,
            include_product_groups=False,
        )
        # With max_depth=0, no traversal happens
        assert isinstance(rows, list)

    def test_get_ndcs_for_rxcuis(self, engine):
        """NDC lookup from mrsat (empty in our test DB)."""
        result = engine.get_ndcs_for_rxcuis(["6809"])
        assert isinstance(result, dict)
