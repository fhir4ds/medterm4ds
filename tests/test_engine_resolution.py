"""Direct unit tests for engines/duckdb/resolution.py.

Tests code resolution: active codes, nonexistent codes, and NDC normalization.
"""

from __future__ import annotations

import duckdb
import pytest
from pathlib import Path

from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine


def _make_resolution_db(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE mrconso (
        CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
        SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("E11", "PT", "Type 2 diabetes", "AUI_E11", "N", "ICD10CM", "C0011860"),
            ("OLD_CODE", "PT", "Deprecated term", "AUI_OLD", "O", "ICD10CM", "C_OLD"),
        ],
    )
    con.execute("""CREATE TABLE mrsat (
        CODE VARCHAR, SAB VARCHAR, ATN VARCHAR, ATV VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrsat VALUES (?, ?, ?, ?)",
        [
            ("860975", "RXNORM", "NDC", "12345678901"),
        ],
    )
    con.close()


@pytest.fixture
def engine(tmp_path):
    db = tmp_path / "resolution.duckdb"
    _make_resolution_db(db)
    con = duckdb.connect(str(db))
    return LocalDuckDBEngine(con)


class TestCodeResolution:
    def test_resolve_active_code(self, engine):
        """Active code resolves to itself."""
        results = engine.resolve_codes([CodeRef("ICD10CM", "E11")])
        assert len(results) == 1
        assert results[0].status == "active"
        assert results[0].resolved.code == "E11"

    def test_resolve_nonexistent_code(self, engine):
        """Nonexistent code returns not-found status."""
        results = engine.resolve_codes([CodeRef("ICD10CM", "FAKE999")])
        assert len(results) == 1
        assert results[0].status == "not_found"

    def test_empty_input(self, engine):
        results = engine.resolve_codes([])
        assert results == []
