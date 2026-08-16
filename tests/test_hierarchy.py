from __future__ import annotations

import duckdb
import pytest

from medterm4ds import CodeRef, get_ancestors, get_children, get_descendants, get_parents
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.hierarchy import get_code_relations


def _make_hierarchy_db(con: duckdb.DuckDBPyConnection) -> None:
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
            ("E11.9", "PT", "Type 2 diabetes mellitus", "ICD_E119", "N", "ICD10CM", "C_E119"),
            ("E11", "PT", "Type 2 diabetes mellitus", "ICD_E11", "N", "ICD10CM", "C_E11"),
            ("E00-E89", "PT", "Endocrine diseases", "ICD_E00", "N", "ICD10CM", "C_E00"),
            ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX"),
            ("200", "PT", "Vaccine group", "CVX_200", "N", "CVX", "C_CVX_PARENT"),
            ("S37.06", "HT", "Major laceration of kidney", "ICD_S3706", "N", "ICD10CM", "C_S3706"),
            ("S37.0", "HT", "Injury of kidney", "ICD_S370", "N", "ICD10CM", "C_S370"),
            ("0010U", "PT", "Specific CPT procedure", "CPT_0010U", "N", "CPT", "C_CPT_CHILD"),
            ("0010", "PT", "CPT procedure parent", "CPT_0010", "N", "CPT", "C_CPT_PARENT"),
            ("E11.9", "PT", "Suppressed duplicate", "ICD_SUP", "Y", "ICD10CM", "C_SUP"),
        ],
    )
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [
            ("ICD_E119", "ICD_E11", "isa", "PAR"),
            ("ICD_E11", "ICD_E00", "isa", "PAR"),
            ("CVX_208", "CVX_200", "isa", "PAR"),
            ("ICD_S3706", "ICD_S370", "isa", "PAR"),
            # Real UMLS CPT edges are REL='PAR' RELA='inverse_isa' (child in
            # AUI1, parent in AUI2) mirrored by REL='CHD' RELA='isa'. The old
            # fixture row carried a fabricated REL='AUI' which does not exist
            # in mrrel; updated per the QC-349/350 REL-authoritative contract.
            ("CPT_0010U", "CPT_0010", "inverse_isa", "PAR"),
        ],
    )


def test_direct_parents_and_children():
    con = duckdb.connect(database=":memory:")
    try:
        _make_hierarchy_db(con)
        engine = LocalDuckDBEngine(con)

        parents = get_parents([("ICD10CM", "E11.9")], engine=engine)
        children = get_children([CodeRef("ICD10CM", "E11")], engine=engine)
    finally:
        con.close()

    assert [row.to_dict() for row in parents] == [
        {
            "source": "ICD10CM",
            "code": "E11.9",
            "source_display": "Type 2 diabetes mellitus",
            "target_source": "ICD10CM",
            "target_code": "E11",
            "target_display": "Type 2 diabetes mellitus",
            "relationship": "parent",
            "depth": 1,
            "rel": "PAR",
            "rela": "isa",
            "source_cui": "C_E119",
            "target_cui": "C_E11",
            "source_aui": "ICD_E119",
            "target_aui": "ICD_E11",
        }
    ]
    assert [(row.relationship, row.source.code, row.target.code, row.depth) for row in children] == [
        ("child", "E11", "E11.9", 1)
    ]


def test_ancestors_descendants_and_missing_values():
    con = duckdb.connect(database=":memory:")
    try:
        _make_hierarchy_db(con)
        engine = LocalDuckDBEngine(con)

        ancestors = get_ancestors([CodeRef("ICD10CM", "E11.9")], engine=engine, max_depth=2)
        descendants = get_descendants([CodeRef("ICD10CM", "E00-E89")], engine=engine, max_depth=2)
        missing = get_code_relations(
            [CodeRef("CVX", "NOPE"), CodeRef("ICD10CM", "NOPE")],
            engine=engine,
            direction="ancestor",
            max_depth=2,
        )
    finally:
        con.close()

    assert [(row.target.code, row.relationship, row.depth) for row in ancestors] == [
        ("E11", "ancestor", 1),
        ("E00-E89", "ancestor", 2),
    ]
    assert [(row.target.code, row.relationship, row.depth) for row in descendants] == [
        ("E11", "descendant", 1),
        ("E11.9", "descendant", 2),
    ]
    assert missing == []


def test_relations_preserve_mixed_source_input_order():
    con = duckdb.connect(database=":memory:")
    try:
        _make_hierarchy_db(con)
        engine = LocalDuckDBEngine(con)

        parents = get_parents(
            [
                CodeRef("ICD10CM", "E11.9"),
                CodeRef("CVX", "208"),
                CodeRef("ICD10CM", "E11"),
            ],
            engine=engine,
        )
    finally:
        con.close()

    assert [(row.source.source, row.source.code, row.target.code) for row in parents] == [
        ("ICD10CM", "E11.9", "E11"),
        ("CVX", "208", "200"),
        ("ICD10CM", "E11", "E00-E89"),
    ]


def test_hierarchy_uses_source_specific_relationship_rules_and_icd_umls_parent_relation():
    con = duckdb.connect(database=":memory:")
    try:
        _make_hierarchy_db(con)
        engine = LocalDuckDBEngine(con)

        icd_parent = get_parents([CodeRef("ICD10CM", "S37.06")], engine=engine)
        cpt_parent = get_parents([CodeRef("CPT", "0010U")], engine=engine)
    finally:
        con.close()

    assert [(row.target.code, row.rel, row.rela, row.depth) for row in icd_parent] == [
        ("S37.0", "PAR", "isa", 1)
    ]
    assert [(row.target.code, row.rel, row.rela, row.depth) for row in cpt_parent] == [
        ("0010", "PAR", "inverse_isa", 1)
    ]


def test_get_code_relations_rejects_non_string_direction():
    """QC-048 (MEDIUM): direction=None / direction=int must raise TypeError,
    not leak AttributeError("'NoneType' object has no attribute 'strip'")."""
    con = duckdb.connect(database=":memory:")
    try:
        _make_hierarchy_db(con)
        engine = LocalDuckDBEngine(con)
        with pytest.raises(TypeError):
            get_code_relations(
                [CodeRef("ICD10CM", "S37.06")],
                engine=engine,
                direction=None,  # type: ignore[arg-type]
                max_depth=1,
            )
        with pytest.raises(TypeError):
            get_code_relations(
                [CodeRef("ICD10CM", "S37.06")],
                engine=engine,
                direction=42,  # type: ignore[arg-type]
                max_depth=1,
            )
    finally:
        con.close()


def test_get_code_relations_rejects_string_max_depth_and_negative_limit():
    """QC-051 (MEDIUM): max_depth='5' must raise TypeError.
    QC-053 (LOW): limit=-1 must raise ValueError at service boundary
    (not duckdb.BinderException leaking from SQL)."""
    con = duckdb.connect(database=":memory:")
    try:
        _make_hierarchy_db(con)
        engine = LocalDuckDBEngine(con)
        # QC-051: string max_depth
        with pytest.raises(TypeError):
            get_code_relations(
                [CodeRef("ICD10CM", "S37.06")],
                engine=engine,
                direction="children",
                max_depth="5",  # type: ignore[arg-type]
            )
        # QC-053: negative limit
        with pytest.raises(ValueError):
            get_code_relations(
                [CodeRef("ICD10CM", "S37.06")],
                engine=engine,
                direction="children",
                max_depth=1,
                limit=-1,
            )
    finally:
        con.close()

