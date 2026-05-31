from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

pytest.importorskip("fastmcp")

from medterm4ds.apps.mcp import McpRuntime, McpSettings, _code_refs, create_mcp_server


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
                ("D_DIAB", "MH", "Diabetes", "MP_DIAB", "N", "MEDLINEPLUS", "C_DIAB"),
                ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX"),
            ],
        )
    finally:
        con.close()


def _settings(db_path: Path, *, prepare_cache: bool = True) -> McpSettings:
    return McpSettings(
        db_path=db_path,
        sources=("ICD10CM", "CVX"),
        memory_profile="low",
        prepare_cache=prepare_cache,
    )


def test_mcp_runtime_patient_friendly_tools(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    runtime = McpRuntime(_settings(db_path, prepare_cache=True))

    runtime.open()
    try:
        assert runtime.health()["cache_prepared"] is True

        single = runtime.patient_friendly_name(code="E11.9", source="ICD10CM")
        assert single["name"] == "Diabetes"
        assert single["match_type"] == "exact"

        batch = runtime.patient_friendly_names(
            codes=["E11.9", "208"],
            sources=["ICD10CM", "CVX"],
        )
        assert [row["name"] for row in batch["results"]] == ["Diabetes", "COVID-19 vaccine"]
    finally:
        runtime.close()

    assert runtime.health()["ready"] is False


def test_mcp_runtime_concept_map_tool(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    runtime = McpRuntime(_settings(db_path, prepare_cache=False))

    runtime.open()
    try:
        rows = runtime.patient_friendly_concept_map(
            codes=["E11.9"],
            sources=["ICD10CM"],
        )["results"]
    finally:
        runtime.close()

    assert rows[0]["source"] == "ICD10CM"
    assert rows[0]["code"] == "E11.9"
    assert rows[0]["target_display"] == "Diabetes"
    assert rows[0]["relationship"] == "equivalent"


def test_mcp_server_registers_expected_tools(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    server = create_mcp_server(_settings(db_path, prepare_cache=False))

    tool_names = {tool.name for tool in server._tool_manager.list_tools()}

    assert {
        "health",
        "patient_friendly_name",
        "patient_friendly_names",
        "patient_friendly_concept_map",
    }.issubset(tool_names)


def test_code_refs_accepts_one_source_for_many_codes():
    refs = _code_refs(["E11.9", "E10.9"], ["ICD10CM"])

    assert [(ref.source, ref.code) for ref in refs] == [
        ("ICD10CM", "E11.9"),
        ("ICD10CM", "E10.9"),
    ]


def test_code_refs_validates_lengths():
    with pytest.raises(ValueError, match="sources must contain"):
        _code_refs(["E11.9", "208"], ["ICD10CM", "CVX", "RXNORM"])
