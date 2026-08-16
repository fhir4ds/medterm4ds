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
        # Service contract always returns a FriendlyNameResult per input code
        # (never None), so the load-bearing assertion is match_type — original
        # means we fell back to the technical display name, none means no
        # resolver could place the code at all.
        assert results[0].match_type in ("original", "none")


# =============================================================================
# Regression: get_patient_friendly_names validates max_depth type/range.
# Found by QC-075/QC-076/QC-077/QC-082 (MEDIUM): pre-fix, max_depth='5'
# (string) silently coerced via int('5'); max_depth=None raised a raw
# TypeError from int(None); max_depth=0 silently skipped the broader walk
# with no signal. Sibling of EC-03 FIX-007 (hierarchy) and EC-02 FIX-005
# (mapping).
# =============================================================================


def test_get_patient_friendly_names_rejects_string_max_depth(engine):
    """QC-076: string max_depth raises TypeError, not silent coercion."""
    with pytest.raises(TypeError, match="max_depth must be int"):
        get_patient_friendly_names(
            [CodeRef("ICD10CM", "E11.9")], engine=engine, max_depth="5"  # type: ignore[arg-type]
        )


def test_get_patient_friendly_names_rejects_none_max_depth(engine):
    """QC-077: None max_depth raises clean TypeError, not raw int() leak."""
    with pytest.raises(TypeError, match="max_depth must be int"):
        get_patient_friendly_names(
            [CodeRef("ICD10CM", "E11.9")], engine=engine, max_depth=None  # type: ignore[arg-type]
        )


def test_get_patient_friendly_names_rejects_negative_max_depth(engine):
    """QC-075 sibling: negative max_depth raises ValueError."""
    with pytest.raises(ValueError, match="max_depth must be non-negative"):
        get_patient_friendly_names(
            [CodeRef("ICD10CM", "E11.9")], engine=engine, max_depth=-5
        )


def test_get_patient_friendly_names_rejects_bool_max_depth(engine):
    """Sibling defense: bool is rejected even though bool is a subclass of int."""
    with pytest.raises(TypeError, match="max_depth must be int"):
        get_patient_friendly_names(
            [CodeRef("ICD10CM", "E11.9")], engine=engine, max_depth=True  # type: ignore[arg-type]
        )
