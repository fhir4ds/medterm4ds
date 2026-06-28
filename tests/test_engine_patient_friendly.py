"""Direct unit tests for engines/duckdb/patient_friendly.py.

Tests patient-friendly name resolution with synthetic data. Verifies
that the resolver produces correct names and provenance for each source.
"""

from __future__ import annotations

import duckdb
import pytest
from pathlib import Path

from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.patient_friendly import get_patient_friendly_names


def _make_pf_db(path: Path) -> None:
    """Minimal DB with MEDLINEPLUS friendly atoms for ICD10CM."""
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE mrconso (
        CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
        SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("E11", "PT", "Type 2 diabetes mellitus", "AUI_E11", "N", "ICD10CM", "C0011860"),
            ("C0011860", "MH", "Type 2 Diabetes Mellitus", "AUI_MLP", "N", "MEDLINEPLUS", "C0011860"),
        ],
    )
    con.execute("""CREATE TABLE mrrel (
        AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR
    )""")
    con.execute("INSERT INTO mrrel VALUES ('AUI_E11', 'AUI_MLP', 'isa', 'PAR')")
    con.close()


@pytest.fixture
def engine(tmp_path):
    db = tmp_path / "pf.duckdb"
    _make_pf_db(db)
    con = duckdb.connect(str(db))
    return LocalDuckDBEngine(con)


class TestPatientFriendly:
    def test_resolves_medlineplus_name(self, engine):
        """ICD10CM code resolves to MEDLINEPLUS patient-friendly name."""
        results = get_patient_friendly_names(
            [CodeRef("ICD10CM", "E11")], engine=engine, max_depth=5
        )
        assert len(results) == 1
        result = results[0]
        assert result is not None
        assert "Diabetes" in result.name
        assert result.friendly_source in ("MEDLINEPLUS", "ICD10CM")

    def test_empty_input(self, engine):
        results = get_patient_friendly_names([], engine=engine)
        assert results == []

    def test_nonexistent_code(self, engine):
        """Nonexistent code returns original/source display."""
        results = get_patient_friendly_names(
            [CodeRef("ICD10CM", "FAKE999")], engine=engine
        )
        assert len(results) == 1
        assert results[0] is not None
        assert results[0].match_type in ("original", "none")
