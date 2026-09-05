"""Shared fixtures for FHIR conformance tests."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

CASES_FILE = Path(__file__).parent / "cases.json"


def load_cases() -> list[dict]:
    """Load declarative test cases from cases.json."""
    with CASES_FILE.open() as f:
        data = json.load(f)
    return data["cases"]


def _make_conformance_db(path: Path) -> None:
    """Create a synthetic DuckDB with mrconso + mrrel for conformance tests."""
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE mrconso (
        CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
        SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),
            ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
            ("E11", "HT", "Type 2 diabetes mellitus", "AE11", "N", "ICD10CM", "C0011847"),
            ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
            # Rows for every remaining advertised system (SYSTEM_TO_FHIR_URI
            # minus pseudo-sources) so implicit `<uri>/vs` expansions and the
            # supported-system advertisement are honest against this fixture.
            ("0DT00ZZ", "PT", "Resection of upper arm skin", "A0DT00ZZ", "N", "ICD10PCS", "C1261207"),
            ("88165", "PT", "Microscopic examination of slide", "A88165", "N", "CPT", "C0057696"),
            ("J0570", "HCPCS", "Counseling, methadone", "AJ0570", "N", "HCPCS", "C0025635"),
            ("2160-0", "PT", "Creatinine [Mass/volume] in Serum", "A21600", "N", "LNC", "C0033756"),
            ("140", "PT", "Influenza, seasonal, injectable, preservative free", "A140", "N", "CVX", "C2816439"),
        ],
    )
    con.execute("""CREATE TABLE mrrel (
        AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [("A44054006", "A73211009", "isa", "PAR")],
    )
    con.close()


@pytest.fixture(scope="module")
def fhir_client(tmp_path_factory):
    """Start the FHIR facade with a synthetic DB and yield a TestClient."""
    fastapi = pytest.importorskip("fastapi")
    from starlette.testclient import TestClient
    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

    db_path = tmp_path_factory.mktemp("fhir_conf") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(tmp_path_factory.mktemp("no_index")),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app) as client:
        yield client
