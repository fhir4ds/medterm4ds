from __future__ import annotations

import duckdb
import pytest

from medterm4ds import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.walk import (
    get_ancestors_prepared,
    get_children_prepared,
    get_descendants_prepared,
    get_parents_prepared,
)


@pytest.fixture()
def con() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE SCHEMA mt4ds")
        conn.execute(
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
        conn.execute(
            """
            CREATE TABLE mt4ds.walk_edges (
                source VARCHAR,
                from_code VARCHAR,
                from_aui VARCHAR,
                from_cui VARCHAR,
                from_tty VARCHAR,
                to_code VARCHAR,
                to_aui VARCHAR,
                to_cui VARCHAR,
                to_tty VARCHAR,
                relationship VARCHAR,
                direction VARCHAR,
                edge_source VARCHAR
            )
            """
        )
        conn.executemany(
            "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ICD10CM", "A.1", "A_CHILD", "C_A1", "PT", "A child", "N", True, 1),
                ("ICD10CM", "A", "A_PARENT", "C_A", "PT", "A parent", "N", True, 1),
                ("ICD10CM", "ROOT", "A_ROOT", "C_ROOT", "PT", "A root", "N", True, 1),
                ("HCPCS", "H1", "H_CHILD", "C_H1", "PT", "H child", "N", True, 1),
                ("HCPCS", "H0", "H_PARENT", "C_H0", "PT", "H parent", "N", True, 1),
            ],
        )
        conn.executemany(
            "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "ICD10CM",
                    "A.1",
                    "A_CHILD",
                    "C_A1",
                    "PT",
                    "A",
                    "A_PARENT",
                    "C_A",
                    "PT",
                    "isa",
                    "parent",
                    "synthetic",
                ),
                (
                    "ICD10CM",
                    "A",
                    "A_PARENT",
                    "C_A",
                    "PT",
                    "ROOT",
                    "A_ROOT",
                    "C_ROOT",
                    "PT",
                    "isa",
                    "parent",
                    "synthetic",
                ),
                (
                    "HCPCS",
                    "H1",
                    "H_CHILD",
                    "C_H1",
                    "PT",
                    "H0",
                    "H_PARENT",
                    "C_H0",
                    "PT",
                    "isa",
                    "parent",
                    "synthetic",
                ),
            ],
        )
        yield conn
    finally:
        conn.close()


def test_walk_primitives_cover_all_directions_and_group_by_source(
    con: duckdb.DuckDBPyConnection,
) -> None:
    parents = get_parents_prepared(
        [
            CodeRef(source="ICD10CM", code="A.1"),
            CodeRef(source="HCPCS", code="H1"),
        ],
        con,
    )
    assert [(row.source.source, row.source.code, row.target.code) for row in parents] == [
        ("ICD10CM", "A.1", "A"),
        ("HCPCS", "H1", "H0"),
    ]

    children = get_children_prepared(
        [
            CodeRef(source="ICD10CM", code="A"),
            CodeRef(source="HCPCS", code="H0"),
        ],
        con,
    )
    assert [(row.source.source, row.source.code, row.target.code) for row in children] == [
        ("ICD10CM", "A", "A.1"),
        ("HCPCS", "H0", "H1"),
    ]

    ancestors = get_ancestors_prepared(
        [CodeRef(source="ICD10CM", code="A.1")],
        con,
        max_depth=2,
    )
    assert [(row.source.code, row.target.code, row.depth) for row in ancestors] == [
        ("A.1", "A", 1),
        ("A", "ROOT", 2),
    ]

    descendants = get_descendants_prepared(
        [CodeRef(source="ICD10CM", code="ROOT")],
        con,
        max_depth=2,
    )
    assert [(row.source.code, row.target.code, row.depth) for row in descendants] == [
        ("ROOT", "A", 1),
        ("A", "A.1", 2),
    ]


def test_local_engine_hierarchy_uses_prepared_walk_edges(
    con: duckdb.DuckDBPyConnection,
) -> None:
    engine = LocalDuckDBEngine(con)

    ancestors = engine.get_code_relations(
        [CodeRef(source="ICD10CM", code="A.1")],
        direction="ancestors",
        max_depth=2,
    )
    assert [(row.source.code, row.target.code, row.depth) for row in ancestors] == [
        ("A.1", "A", 1),
        ("A.1", "ROOT", 2),
    ]
    assert ancestors[0].source_display == "A child"
    assert ancestors[0].target_display == "A parent"
    assert ancestors[0].rel == "isa"
    assert ancestors[0].rela is None

    descendants = engine.get_code_relations(
        [CodeRef(source="ICD10CM", code="ROOT")],
        direction="descendants",
        max_depth=2,
    )
    assert [(row.source.code, row.target.code, row.depth) for row in descendants] == [
        ("ROOT", "A", 1),
        ("ROOT", "A.1", 2),
    ]
