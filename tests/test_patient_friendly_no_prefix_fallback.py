"""Patient-friendly fallback must not infer hierarchy from code prefixes."""
from __future__ import annotations

import duckdb

from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.patient_friendly import get_patient_friendly_names


def _raw_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute(
        """
        CREATE TABLE mrconso (
            CUI VARCHAR,
            AUI VARCHAR,
            SAB VARCHAR,
            TTY VARCHAR,
            CODE VARCHAR,
            STR VARCHAR,
            SUPPRESS VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE mrrel (
            AUI1 VARCHAR,
            AUI2 VARCHAR,
            REL VARCHAR,
            RELA VARCHAR
        )
        """
    )
    con.execute("CREATE TABLE mrsat (CODE VARCHAR, SAB VARCHAR, ATN VARCHAR, ATV VARCHAR)")
    return con


def test_raw_patient_friendly_does_not_use_icd10_prefix_parent() -> None:
    con = _raw_con()
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "C_L7632",
                "A_L7632",
                "ICD10CM",
                "PT",
                "L76.32",
                "Postprocedural Hematoma of Skin and Subcutaneous Tissue",
                "N",
            ),
            (
                "C_L76",
                "A_L76",
                "ICD10CM",
                "HT",
                "L76",
                "Intraoperative and postprocedural complications of skin",
                "N",
            ),
            (
                "C_L76",
                "A_CHV_L76",
                "CHV",
                "PT",
                "CHV_L76",
                "postoperative skin problem",
                "N",
            ),
        ],
    )

    engine = LocalDuckDBEngine(con)
    result = get_patient_friendly_names(
        [CodeRef(source="ICD10CM", code="L76.32")],
        engine=engine,
    )[0]

    assert result.name == "Postprocedural Hematoma of Skin and Subcutaneous Tissue"
    assert result.friendly_source == "ICD10CM"
    assert result.match_type == "original"
    assert result.match_depth == 0

    con.close()
