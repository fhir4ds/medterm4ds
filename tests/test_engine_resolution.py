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
            # QC-406 fixture: the active replacement for OLD_CODE.
            ("NEW_CODE", "PT", "Replacement term", "AUI_NEW", "N", "ICD10CM", "C_NEW"),
            # QC-401 fixture: the RXNORM drug the NDC attribute points at.
            ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "AUI_860975", "N", "RXNORM", "C0978484"),
        ],
    )
    con.execute("""CREATE TABLE mrrel (
        AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [
            # QC-406 fixture: obsolete -> active replacement edge.
            ("AUI_OLD", "AUI_NEW", "same_as", "RO"),
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

    def test_resolve_obsolete_code_finds_replacement(self, engine):
        """QC-398 baseline: the live-MRREL fallback resolves OLD_CODE to
        NEW_CODE when no prepared code_replacements table exists."""
        results = engine.resolve_codes([CodeRef("ICD10CM", "OLD_CODE")])
        assert results[0].status == "replaced"
        assert results[0].resolved.code == "NEW_CODE"
        assert results[0].resolved_display == "Replacement term"

    def test_prepare_cache_preserves_replacement_resolution_qc406(self, tmp_path):
        """QC-406 (HIGH): prepare_cache's active-only TEMP mrconso/mrrel shadow
        must not change obsolete-code resolution. The shadow hides the
        SUPPRESS='O' atoms (so code_auis found none) and drops the
        obsolete-to-active mrrel edges — pre-fix, the prepared engine
        degraded status='replaced' to 'historical' with resolved=None."""
        db = tmp_path / "resolution_qc406.duckdb"
        _make_resolution_db(db)
        con = duckdb.connect(str(db))
        try:
            prepared = LocalDuckDBEngine(con)
            prepared.prepare_cache(create_indexes=False)
            results = prepared.resolve_codes([CodeRef("ICD10CM", "OLD_CODE")])
        finally:
            con.close()
        assert results[0].status == "replaced", results[0].status
        assert results[0].resolved.code == "NEW_CODE"
        assert results[0].resolved_display == "Replacement term"

    def test_ndc_lookup_default_mode_returns_resolved_drug_qc401(self, tmp_path):
        """QC-401 (HIGH): active_only lookup of an NDC code must return the
        resolved RXNORM drug record. The historical fallthrough previously
        echoed the raw NDC string as the display while cui/aui/tty/suppress
        came from the resolved atom."""
        from medterm4ds.services.lookup import get_code_info

        db = tmp_path / "resolution_qc401.duckdb"
        _make_resolution_db(db)
        con = duckdb.connect(str(db))
        try:
            engine = LocalDuckDBEngine(con)
            info = get_code_info(
                ("NDC", "12345678901"), engine=engine, resolve_mode="active_only"
            )
        finally:
            con.close()
        assert info is not None
        assert (info.code.source, info.code.code) == ("RXNORM", "860975")
        assert info.name == "24 HR metformin 500 MG Oral Tablet"
        assert info.name != "12345678901"
