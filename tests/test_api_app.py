from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from medterm4ds.apps.api import ApiSettings, create_app


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
                ("E11", "PT", "Type 2 diabetes mellitus", "ICD_E11", "N", "ICD10CM", "C_E11"),
                ("D_DIAB", "MH", "Diabetes", "MP_DIAB", "N", "MEDLINEPLUS", "C_DIAB"),
                ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX"),
            ],
        )
        con.executemany(
            "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
            [
                ("ICD_E119", "ICD_E11", "isa", "PAR"),
            ],
        )
    finally:
        con.close()


def _settings(db_path: Path, *, prepare_cache: bool = True) -> ApiSettings:
    return ApiSettings(
        db_path=db_path,
        sources=("ICD10CM", "CVX"),
        memory_profile="low",
        prepare_cache=prepare_cache,
    )


def test_api_health_uses_single_configured_database(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path))
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "ready": True,
        "database": str(db_path),
        "sources": ["ICD10CM", "CVX"],
        "memory_profile": "low",
        "cache_prepared": True,
    }


def test_api_patient_friendly_endpoint(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/patient-friendly",
            json={
                "codes": [
                    {"source": "ICD10CM", "code": "E11.9"},
                    {"source": "CVX", "code": "208"},
                ]
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert [row["name"] for row in payload["results"]] == ["Diabetes", "COVID-19 vaccine"]
    assert [row["match_type"] for row in payload["results"]] == ["exact", "original"]


def test_api_lookup_endpoint(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/lookup",
            json={
                "codes": [
                    {"source": "ICD10-CM", "code": "E11.9"},
                    {"source": "CVX", "code": "NOPE"},
                ]
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0] == {
        "source": "ICD10CM",
        "code": "E11.9",
        "name": "Type 2 diabetes mellitus",
        "cui": "C_DIAB",
        "aui": "ICD_E119",
        "tty": "PT",
        "suppress": "N",
    }
    assert payload["results"][1] is None


def test_api_hierarchy_endpoint(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/hierarchy",
            json={
                "codes": [
                    {"source": "ICD10CM", "code": "E11.9"},
                ],
                "direction": "parents",
            },
        )

    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["source"] == "ICD10CM"
    assert row["code"] == "E11.9"
    assert row["target_code"] == "E11"
    assert row["relationship"] == "parent"
    assert row["depth"] == 1


def test_api_conceptmap_endpoint(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/conceptmap/patient-friendly",
            json={
                "codes": [
                    {"source": "ICD10CM", "code": "E11.9"},
                ],
                "target_source": "PATIENT_FRIENDLY",
            },
        )

    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["source"] == "ICD10CM"
    assert row["code"] == "E11.9"
    assert row["target_source"] == "PATIENT_FRIENDLY"
    assert row["target_display"] == "Diabetes"
    assert row["relationship"] == "equivalent"


def test_api_validates_request_body(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/patient-friendly",
            json={"codes": [{"source": "ICD10CM"}]},
        )

    assert response.status_code == 422
