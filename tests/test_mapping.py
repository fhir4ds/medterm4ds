from __future__ import annotations

import duckdb
import pytest

from medterm4ds import CodeRef, get_code_mappings
from medterm4ds.engines.duckdb import LocalLiteEngine


def _make_mapping_db(con: duckdb.DuckDBPyConnection) -> None:
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
            ("E11.9", "PT", "Suppressed source", "ICD_SUP", "Y", "ICD10CM", "C_SUP"),
            ("A1", "PT", "Specific source concept", "ICD_A1", "N", "ICD10CM", "C_CHILD"),
            ("A0", "PT", "Broader source concept", "ICD_A0", "N", "ICD10CM", "C_PARENT"),
            ("44054006", "SY", "Diabetes type 2 synonym", "SNOMED_SY", "N", "SNOMEDCT_US", "C_DIAB"),
            ("44054006", "PT", "Diabetes mellitus type 2", "SNOMED_PT", "N", "SNOMEDCT_US", "C_DIAB"),
            ("73211009", "PT", "Diabetes mellitus", "SNOMED_DM", "N", "SNOMEDCT_US", "C_DIAB"),
            ("999000", "PT", "Broader target concept", "SNOMED_A0", "N", "SNOMEDCT_US", "C_PARENT"),
            ("111111", "PT", "Target parent", "SNOMED_PARENT", "N", "SNOMEDCT_US", "C_PARENT_TARGET"),
            ("222222", "PT", "Target child", "SNOMED_CHILD", "N", "SNOMEDCT_US", "C_CHILD_TARGET"),
            ("999999", "PT", "Suppressed target", "SNOMED_SUP", "Y", "SNOMEDCT_US", "C_DIAB"),
            ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX"),
            ("840539006", "PT", "COVID-19 vaccine product", "SNOMED_CVX", "N", "SNOMEDCT_US", "C_CVX"),
            ("2345-7", "LN", "Glucose [Mass/volume] in Serum or Plasma", "LNC_GLU", "N", "LNC", "C_GLU"),
            ("S1", "PT", "Suppressed only", "ICD_SUP_ONLY", "Y", "ICD10CM", "C_ONLY_SUP"),
        ],
    )
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [
            ("ICD_A1", "ICD_A0", "isa", "PAR"),
            ("SNOMED_PT", "SNOMED_PARENT", "isa", "PAR"),
            ("SNOMED_CHILD", "SNOMED_PT", "isa", "PAR"),
        ],
    )


def test_get_code_mappings_returns_same_cui_active_targets_in_input_order():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        engine = LocalLiteEngine(con)

        rows = get_code_mappings(
            [
                CodeRef("CVX", "208"),
                ("E11.9", "ICD10-CM"),
                CodeRef("ICD10CM", "NOPE"),
                CodeRef("ICD10CM", "S1"),
            ],
            engine=engine,
            target_sources=["SNOMED"],
        )
    finally:
        con.close()

    assert [(row.source.source, row.source.code, row.target.code) for row in rows] == [
        ("CVX", "208", "840539006"),
        ("ICD10CM", "E11.9", "44054006"),
        ("ICD10CM", "E11.9", "73211009"),
    ]
    assert rows[1].to_dict() == {
        "source": "ICD10CM",
        "code": "E11.9",
        "source_display": "Type 2 diabetes mellitus",
        "target_source": "SNOMEDCT_US",
        "target_code": "44054006",
        "target_display": "Diabetes mellitus type 2",
        "relationship": "equivalent",
        "match_type": "same_cui",
        "match_depth": 0,
        "source_cui": "C_DIAB",
        "target_cui": "C_DIAB",
        "source_aui": "ICD_E119",
        "target_aui": "SNOMED_PT",
        "target_tty": "PT",
        "matched_via": {
            "strategy": "same_cui",
            "steps": [
                {
                    "op": "input_atom",
                    "source": "ICD10CM",
                    "code": "E11.9",
                    "cui": "C_DIAB",
                    "aui": "ICD_E119",
                    "name": "Type 2 diabetes mellitus",
                },
                {
                    "op": "same_cui",
                    "source": "ICD10CM",
                    "code": "E11.9",
                    "target_source": "SNOMEDCT_US",
                    "target_code": "44054006",
                    "cui": "C_DIAB",
                },
                {
                    "op": "target_atom",
                    "source": "SNOMEDCT_US",
                    "code": "44054006",
                    "cui": "C_DIAB",
                    "aui": "SNOMED_PT",
                    "tty": "PT",
                    "name": "Diabetes mellitus type 2",
                },
            ],
        },
    }


def test_get_code_mappings_caps_results_and_validates_args():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        engine = LocalLiteEngine(con)

        rows = get_code_mappings(
            [CodeRef("ICD10CM", "E11.9")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_results_per_code=1,
        )
    finally:
        con.close()

    assert [(row.target.code, row.target_display) for row in rows] == [
        ("44054006", "Diabetes mellitus type 2")
    ]

    with pytest.raises(ValueError, match="target_sources"):
        get_code_mappings([CodeRef("ICD10CM", "E11.9")], engine=engine, target_sources=[])
    with pytest.raises(ValueError, match="max_results_per_code"):
        get_code_mappings(
            [CodeRef("ICD10CM", "E11.9")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_results_per_code=0,
        )
    with pytest.raises(ValueError, match="max_depth"):
        get_code_mappings(
            [CodeRef("ICD10CM", "E11.9")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_depth=-1,
        )


def test_get_code_mappings_can_use_source_ancestor_fallback():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        engine = LocalLiteEngine(con)

        rows = get_code_mappings(
            [CodeRef("ICD10CM", "A1")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
        )
    finally:
        con.close()

    assert [(row.target.code, row.relationship, row.match_type, row.match_depth) for row in rows] == [
        ("999000", "source-is-narrower-than-target", "source_ancestor_same_cui", 1)
    ]
    assert rows[0].matched_via.strategy == "source_ancestor_same_cui"
    assert [step.op for step in rows[0].matched_via.steps] == [
        "input_atom",
        "source_ancestor",
        "same_cui",
        "target_atom",
    ]


def test_get_code_mappings_can_include_target_hierarchy():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        engine = LocalLiteEngine(con)

        rows = get_code_mappings(
            [CodeRef("ICD10CM", "E11.9")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
            include_target_ancestors=True,
            include_target_descendants=True,
        )
    finally:
        con.close()

    selected = {
        (row.target.code, row.relationship, row.match_type, row.match_depth)
        for row in rows
    }
    assert ("44054006", "equivalent", "same_cui", 0) in selected
    assert ("111111", "source-is-narrower-than-target", "target_ancestor", 1) in selected
    assert ("222222", "source-is-broader-than-target", "target_descendant", 1) in selected
