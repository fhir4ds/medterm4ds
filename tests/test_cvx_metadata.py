"""CVX patient-friendly resolution over prepared metadata."""
from __future__ import annotations

import duckdb

from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.patient_friendly import get_patient_friendly_names


def test_raw_cvx_resolution_uses_prepared_metadata() -> None:
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
    con.execute("CREATE SCHEMA mt4ds")
    con.execute(
        """
        CREATE TABLE mt4ds.cvx_metadata (
            code VARCHAR,
            group_name VARCHAR,
            short_name VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO mrconso VALUES ('C_CVX', 'A_CVX', 'CVX', 'PT', '208', 'COVID-19 vaccine', 'N')"
    )
    con.execute("INSERT INTO mt4ds.cvx_metadata VALUES ('208', 'COVID-19', 'COVID vaccine')")

    engine = LocalDuckDBEngine(con, cvx_groups={})
    result = get_patient_friendly_names(
        [CodeRef(source="CVX", code="208")],
        engine=engine,
    )[0]

    assert result.name == "COVID-19"
    assert result.friendly_source == "CVX"
    assert result.match_type == "cvx_group"

    con.close()
