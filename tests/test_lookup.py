from __future__ import annotations

import duckdb

from medterm4ds import CodeRef, get_code_info, get_code_infos
from medterm4ds.engines.duckdb import LocalLiteEngine


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
        engine = LocalLiteEngine(con)

        infos = get_code_infos(
            [
                CodeRef("CVX", "208"),
                ("E11.9", "ICD10-CM"),
                CodeRef("ICD10CM", "NOPE"),
                CodeRef("ICD10CM", "S1"),
                ("2345-7", "LOINC"),
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
        engine = LocalLiteEngine(con)
        info = get_code_info(CodeRef("ICD10CM", "E11.9"), engine=engine)
    finally:
        con.close()

    assert info.name == "Type 2 diabetes mellitus"
    assert info.cui == "C_DIAB"
