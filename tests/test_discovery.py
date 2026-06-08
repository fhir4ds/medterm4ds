from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.discovery import (
    get_code_ttys,
    get_source_stats,
    sample_source_codes,
    search_names,
)


def _make_duckdb(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE mrconso (
                CODE VARCHAR,
                TTY VARCHAR,
                STR VARCHAR,
                AUI VARCHAR,
                SUPPRESS VARCHAR,
                SAB VARCHAR,
                CUI VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mrrel (
                AUI1 VARCHAR,
                AUI2 VARCHAR,
                RELA VARCHAR,
                REL VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("E11.9", "PT", "Type 2 diabetes mellitus", "ICD_E119", "N", "ICD10CM", "C_DIAB"),
                ("E11.9", "HT", "Diabetes mellitus type 2", "ICD_E119_HT", "N", "ICD10CM", "C_DIAB"),
                ("E10.9", "PT", "Type 1 diabetes mellitus", "ICD_E109", "N", "ICD10CM", "C_DIAB1"),
                ("44054006", "PT", "Diabetes mellitus type 2", "SNOMED_DIAB", "N", "SNOMEDCT_US", "C_DIAB"),
                ("D_DIAB", "MH", "Diabetes", "MP_DIAB", "N", "MEDLINEPLUS", "C_DIAB"),
                ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX"),
                ("999", "PT", "Suppressed code", "CVX_999", "Y", "CVX", "C_SUPP"),
            ],
        )
    finally:
        con.close()


@pytest.fixture()
def engine(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        yield LocalDuckDBEngine(con)
    finally:
        con.close()


def test_source_stats_counts_active_distinct_codes(engine):
    stats = get_source_stats(engine=engine, sources=["ICD10-CM", "CVX"])

    assert [stat.to_dict() for stat in stats] == [
        {"source": "CVX", "code_count": 1, "atom_count": 1},
        {"source": "ICD10CM", "code_count": 2, "atom_count": 3},
    ]


def test_sample_source_codes_returns_per_source_limit(engine):
    codes = sample_source_codes(engine=engine, sources=["ICD10CM", "CVX"], per_source=1)

    assert [(code.source, code.code) for code in codes] == [
        ("CVX", "208"),
        ("ICD10CM", "E10.9"),
    ]


def test_code_ttys_returns_all_active_atoms_for_codes(engine):
    infos = get_code_ttys([CodeRef("ICD10CM", "E11.9")], engine=engine)

    assert [(info.code.source, info.code.code, info.tty, info.name) for info in infos] == [
        ("ICD10CM", "E11.9", "PT", "Type 2 diabetes mellitus"),
        ("ICD10CM", "E11.9", "HT", "Diabetes mellitus type 2"),
    ]


def test_search_names_ranks_and_filters_active_atoms(engine):
    results = search_names(
        "diabetes",
        engine=engine,
        sources=["ICD10CM", "MEDLINEPLUS"],
        tty_filters=["MH", "PT"],
        limit=3,
    )

    assert [(row.code.source, row.code.code, row.match_type, row.tty) for row in results] == [
        ("MEDLINEPLUS", "D_DIAB", "exact", "MH"),
        ("ICD10CM", "E10.9", "contains", "PT"),
        ("ICD10CM", "E11.9", "contains", "PT"),
    ]


def test_search_names_uses_prepared_atoms_active_only():
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("CREATE SCHEMA mt4ds")
        con.execute(
            """
            CREATE TABLE mt4ds.atoms (
                source VARCHAR,
                code VARCHAR,
                aui VARCHAR,
                cui VARCHAR,
                tty VARCHAR,
                name VARCHAR,
                suppress VARCHAR,
                is_active BOOLEAN
            )
            """
        )
        con.executemany(
            "INSERT INTO mt4ds.atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ICD10CM", "E11.9", "ICD_E119", "C_DIAB", "PT", "Type 2 diabetes mellitus", "N", True),
                ("ICD10CM", "E11.9", "ICD_E119_HT", "C_DIAB", "HT", "Diabetes heading", "N", True),
                ("MEDLINEPLUS", "D_DIAB", "MP_DIAB", "C_DIAB", "MH", "Diabetes", "N", True),
                ("CVX", "999", "CVX_999", "C_SUPP", "PT", "Diabetes suppressed", "Y", False),
            ],
        )
        engine = LocalDuckDBEngine(con)

        results = search_names(
            "diabetes",
            engine=engine,
            sources=["ICD10CM", "MEDLINEPLUS", "CVX"],
            tty_filters=["MH", "PT"],
            limit=5,
        )
    finally:
        con.close()

    assert [(row.code.source, row.code.code, row.match_type, row.tty) for row in results] == [
        ("MEDLINEPLUS", "D_DIAB", "exact", "MH"),
        ("ICD10CM", "E11.9", "contains", "PT"),
    ]
