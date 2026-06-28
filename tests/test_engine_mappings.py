"""Direct unit tests for engines/duckdb/mappings.py.

Tests same-CUI crosswalk and source-to-target mapping with synthetic data.
"""

from __future__ import annotations

import duckdb
import pytest
from pathlib import Path

from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine


def _make_mapping_db(path: Path) -> None:
    """SNOMED and ICD10 share CUIs; RxNorm has a different one."""
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE mrconso (
        CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
        SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("SN001", "PT", "Flu", "AUI_SN", "N", "SNOMEDCT_US", "C_FLU"),
            ("J11.1", "HT", "Influenza", "AUI_ICD", "N", "ICD10CM", "C_FLU"),
            ("860975", "SCD", "metformin", "AUI_RX", "N", "RXNORM", "C_MET"),
        ],
    )
    con.close()


@pytest.fixture
def engine(tmp_path):
    db = tmp_path / "mapping.duckdb"
    _make_mapping_db(db)
    con = duckdb.connect(str(db))
    return LocalDuckDBEngine(con)


class TestCodeMappings:
    def test_same_cui_mapping(self, engine):
        """SNOMED → ICD10CM via shared CUI."""
        results = engine.get_code_mappings(
            [CodeRef("SNOMEDCT_US", "SN001")],
            target_sources=["ICD10CM"],
            max_results_per_code=10,
        )
        assert len(results) >= 1
        assert results[0].target.source == "ICD10CM"
        assert results[0].target.code == "J11.1"

    def test_no_mapping_different_cui(self, engine):
        """SNOMED → RXNORM where no CUI match exists."""
        results = engine.get_code_mappings(
            [CodeRef("SNOMEDCT_US", "SN001")],
            target_sources=["RXNORM"],
            max_results_per_code=10,
        )
        assert len(results) == 0

    def test_empty_codes(self, engine):
        """Empty input returns empty output."""
        results = engine.get_code_mappings(
            [],
            target_sources=["ICD10CM"],
        )
        assert results == []
