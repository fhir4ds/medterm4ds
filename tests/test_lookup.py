from __future__ import annotations

import duckdb

from medterm4ds import CodeRef, get_code_info, get_code_infos
from medterm4ds.engines.duckdb import LocalDuckDBEngine


def _make_lookup_db(con: duckdb.DuckDBPyConnection) -> None:
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
            ("E11.9", "SY", "Diabetes synonym", "ICD_SY", "N", "ICD10CM", "C_DIAB"),
            ("E11.9", "PT", "Type 2 diabetes mellitus", "ICD_PT", "N", "ICD10CM", "C_DIAB"),
            ("E11.9", "PT", "Suppressed diabetes", "ICD_SUP", "Y", "ICD10CM", "C_DIAB_SUP"),
            ("S1", "PT", "Suppressed only", "ICD_SUP_ONLY", "Y", "ICD10CM", "C_SUP_ONLY"),
            ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX"),
            ("2345-7", "LN", "Glucose [Mass/volume] in Serum or Plasma", "LNC_GLU", "N", "LNC", "C_GLU"),
        ],
    )


def test_get_code_infos_preserves_order_and_missing_values():
    con = duckdb.connect(database=":memory:")
    try:
        _make_lookup_db(con)
        engine = LocalDuckDBEngine(con)

        infos = get_code_infos(
            [
                CodeRef("CVX", "208"),
                ("ICD10-CM", "E11.9"),
                CodeRef("ICD10CM", "NOPE"),
                CodeRef("ICD10CM", "S1"),
                ("LOINC", "2345-7"),
            ],
            engine=engine,
        )
    finally:
        con.close()

    assert infos[0].to_dict() == {
        "source": "CVX",
        "code": "208",
        "name": "COVID-19 vaccine",
        "cui": "C_CVX",
        "aui": "CVX_208",
        "tty": "PT",
        "suppress": "N",
    }
    assert infos[1].name == "Type 2 diabetes mellitus"
    assert infos[1].tty == "PT"
    assert infos[1].suppress == "N"
    assert infos[2] is None
    assert infos[3] is None
    assert infos[4].code == CodeRef("LNC", "2345-7")


def test_get_code_info_single_lookup():
    con = duckdb.connect(database=":memory:")
    try:
        _make_lookup_db(con)
        engine = LocalDuckDBEngine(con)
        info = get_code_info(CodeRef("ICD10CM", "E11.9"), engine=engine)
    finally:
        con.close()

    assert info.name == "Type 2 diabetes mellitus"
    assert info.cui == "C_DIAB"


def test_get_code_infos_uses_prepared_best_atoms_active_only():
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("CREATE SCHEMA mt4ds")
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
        con.executemany(
            "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ICD10CM", "E11.9", "ICD_PT", "C_DIAB", "PT", "Type 2 diabetes mellitus", "N", True, 1),
                ("ICD10CM", "S1", "ICD_SUP_ONLY", "C_SUP_ONLY", "PT", "Suppressed only", "Y", False, 1),
            ],
        )
        engine = LocalDuckDBEngine(con)
        infos = get_code_infos(
            [
                CodeRef("ICD10CM", "E11.9"),
                CodeRef("ICD10CM", "S1"),
            ],
            engine=engine,
        )
    finally:
        con.close()

    assert infos[0].name == "Type 2 diabetes mellitus"
    assert infos[1] is None


def test_discovery_uses_prepared_atoms_and_best_atoms():
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
        con.executemany(
            "INSERT INTO mt4ds.atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ICD10CM", "E11.9", "ICD_PT", "C_DIAB", "PT", "Type 2 diabetes mellitus", "N", True),
                ("ICD10CM", "E11.9", "ICD_SY", "C_DIAB", "SY", "Diabetes synonym", "N", True),
                ("ICD10CM", "S1", "ICD_SUP", "C_SUP", "PT", "Suppressed only", "Y", False),
                ("CVX", "208", "CVX_208", "C_CVX", "PT", "COVID-19 vaccine", "N", True),
            ],
        )
        con.executemany(
            "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ICD10CM", "E11.9", "ICD_PT", "C_DIAB", "PT", "Type 2 diabetes mellitus", "N", True, 1),
                ("ICD10CM", "S1", "ICD_SUP", "C_SUP", "PT", "Suppressed only", "Y", False, 1),
                ("CVX", "208", "CVX_208", "C_CVX", "PT", "COVID-19 vaccine", "N", True, 1),
            ],
        )
        engine = LocalDuckDBEngine(con)

        ttys = engine.get_code_ttys([CodeRef("ICD10CM", "E11.9"), CodeRef("ICD10CM", "S1")])
        stats = engine.get_source_stats(["ICD10CM", "CVX"])
        sample = engine.sample_source_codes(["ICD10CM", "CVX"], per_source=2)
    finally:
        con.close()

    assert [row.tty for row in ttys] == ["PT", "SY"]
    assert [(row.source, row.code_count, row.atom_count) for row in stats] == [
        ("CVX", 1, 1),
        ("ICD10CM", 1, 2),
    ]
    assert sample == [
        CodeRef("CVX", "208"),
        CodeRef("ICD10CM", "E11.9"),
    ]
