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


def test_prepared_seed_uses_all_atoms_not_just_rank1() -> None:
    """QC-067/QC-070 (CRITICAL) regression: walk_edges edge attached to a
    non-rank-1 atom of the parent must be discoverable by descendants() /
    is_descendant() / $subsumes.

    Pre-fix, the seed step joined best_atoms with ``rank = 1``, so when a
    code had multiple atoms and an mrrel CHD/isa edge attached to a non-
    best atom, the edge was silently dropped. This fixture mirrors the
    production scenario: code PARENT has 3 atoms (rank 1/2/3); the edge
    to CHILD attaches to the rank-3 atom of PARENT. Without the fix,
    descendants(PARENT) would miss CHILD.
    """
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE SCHEMA mt4ds")
        # best_atoms mirrors the production schema (all atoms, with rank).
        # PARENT has 3 atoms; the edge attaches to AUI_PARENT_OAS (rank 3).
        conn.execute(
            """
            CREATE TABLE mt4ds.best_atoms (
                source VARCHAR, code VARCHAR, aui VARCHAR, cui VARCHAR,
                tty VARCHAR, name VARCHAR, suppress VARCHAR,
                is_active BOOLEAN, rank INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                # PARENT atoms: rank 1 (display), 2, 3 (where the edge attaches)
                ("SNOMEDCT_US", "PARENT", "AUI_PARENT_DISPLAY", "C_P", "OAS",
                 "Parent display", "N", True, 1),
                ("SNOMEDCT_US", "PARENT", "AUI_PARENT_OTHER", "C_P", "OAS",
                 "Parent other", "N", True, 2),
                ("SNOMEDCT_US", "PARENT", "AUI_PARENT_OAP", "C_P", "OAP",
                 "Parent OAP", "N", True, 3),
                # CHILD atom
                ("SNOMEDCT_US", "CHILD", "AUI_CHILD_PT", "C_C", "PT",
                 "Child PT", "N", True, 1),
            ],
        )
        # walk_edges: CHILD -> PARENT via the non-rank-1 atom of PARENT
        conn.execute(
            """
            CREATE TABLE mt4ds.walk_edges (
                source VARCHAR, from_code VARCHAR, from_aui VARCHAR,
                from_cui VARCHAR, from_tty VARCHAR, to_code VARCHAR,
                to_aui VARCHAR, to_cui VARCHAR, to_tty VARCHAR,
                relationship VARCHAR, direction VARCHAR, edge_source VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mt4ds.walk_edges VALUES (
                'SNOMEDCT_US', 'CHILD', 'AUI_CHILD_PT', 'C_C', 'PT',
                'PARENT', 'AUI_PARENT_OAP', 'C_P', 'OAP',
                'isa', 'parent', 'umls_mrrel'
            )
            """
        )
        engine = LocalDuckDBEngine(conn)
        # descendants(PARENT) must include CHILD even though the edge
        # attaches to PARENT's rank-3 atom (not rank-1).
        descendants = engine.get_code_relations(
            [CodeRef(source="SNOMEDCT_US", code="PARENT")],
            direction="descendants",
            max_depth=5,
        )
        target_codes = [row.target.code for row in descendants]
        assert "CHILD" in target_codes, (
            f"QC-070 regression: CHILD missing from descendants(PARENT); "
            f"got {target_codes}"
        )
    finally:
        conn.close()



def test_prepared_walk_excludes_retired_targets_qc238() -> None:
    """QC-238 (HIGH) regression: the prepared walk_edges path had no
    is_active check on walked targets (the raw-mrrel path enforces
    SUPPRESS='N' on every hop), leaking retired SNOMED concepts into
    descendants — 8,069 of 49,696 (16.2%) depth-3 descendants of
    404684003 in production 2026AA. Retired targets must be excluded,
    including concepts only reachable THROUGH a retired intermediate.
    """
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE SCHEMA mt4ds")
        conn.execute(
            """
            CREATE TABLE mt4ds.best_atoms (
                source VARCHAR, code VARCHAR, aui VARCHAR, cui VARCHAR,
                tty VARCHAR, name VARCHAR, suppress VARCHAR,
                is_active BOOLEAN, rank INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("SNOMEDCT_US", "ROOT", "AUI_ROOT", "C_R", "PT", "Root", "N", True, 1),
                ("SNOMEDCT_US", "ACTIVE_CHILD", "AUI_ACTIVE", "C_AC", "PT", "Active child", "N", True, 1),
                # Retired concept: suppressed atom (mrconso SUPPRESS='O').
                ("SNOMEDCT_US", "RETIRED_CHILD", "AUI_RETIRED", "C_RC", "PT", "Retired child", "O", False, 1),
                # Active concept whose ONLY parent edge is via RETIRED_CHILD.
                ("SNOMEDCT_US", "ONLY_VIA_RETIRED", "AUI_OVR", "C_OVR", "PT", "Only via retired", "N", True, 1),
            ],
        )
        conn.execute(
            """
            CREATE TABLE mt4ds.walk_edges (
                source VARCHAR, from_code VARCHAR, from_aui VARCHAR,
                from_cui VARCHAR, from_tty VARCHAR, to_code VARCHAR,
                to_aui VARCHAR, to_cui VARCHAR, to_tty VARCHAR,
                relationship VARCHAR, direction VARCHAR, edge_source VARCHAR
            )
            """
        )
        conn.executemany(
            "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("SNOMEDCT_US", "ACTIVE_CHILD", "AUI_ACTIVE", "C_AC", "PT",
                 "ROOT", "AUI_ROOT", "C_R", "PT", "isa", "parent", "synthetic"),
                ("SNOMEDCT_US", "RETIRED_CHILD", "AUI_RETIRED", "C_RC", "PT",
                 "ROOT", "AUI_ROOT", "C_R", "PT", "isa", "parent", "synthetic"),
                ("SNOMEDCT_US", "ONLY_VIA_RETIRED", "AUI_OVR", "C_OVR", "PT",
                 "RETIRED_CHILD", "AUI_RETIRED", "C_RC", "PT", "isa", "parent", "synthetic"),
            ],
        )
        engine = LocalDuckDBEngine(conn)
        descendants = engine.get_code_relations(
            [CodeRef(source="SNOMEDCT_US", code="ROOT")],
            direction="descendants",
            max_depth=3,
        )
        target_codes = [row.target.code for row in descendants]
        assert target_codes == ["ACTIVE_CHILD"], (
            f"QC-238 regression: retired targets leaked into descendants; "
            f"got {target_codes}"
        )
    finally:
        conn.close()


def test_discover_limit_caps_descendant_rows_qc216(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """QC-216 (HIGH) regression: discover() never passed ``limit`` to
    get_code_relations on the code branch — limit=5 on SNOMED 404684003
    (depth=3) returned all 49,696 descendants (~18MB payload) in production."""
    from medterm4ds.domains.terminology import discover

    engine = LocalDuckDBEngine(con)
    payload = discover("ICD10CM", engine=engine, code="ROOT", depth=2, limit=1)
    assert len(payload["descendants"]) == 1
    # Without a limit the same walk returns both levels.
    payload_all = discover("ICD10CM", engine=engine, code="ROOT", depth=2, limit=20)
    assert len(payload_all["descendants"]) == 2


# ---------------------------------------------------------------------------
# CR-031 (HIGH): walk_closure_limited per-source coverage gate
# ---------------------------------------------------------------------------


@pytest.fixture()
def stale_closure_con() -> duckdb.DuckDBPyConnection:
    """Prepared DB whose walk_closure_limited misses RXNORM (pre-CR-031 build).

    walk_edges has RXNORM + ICD10CM parent edges; the closure table was built
    by the OLD hardcoded whitelist (ICD10CM only, no RXNORM/ATC/MSH) — the
    exact production shape during the 0.9 rebuild transition.
    """
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE SCHEMA mt4ds")
        conn.execute(
            """
            CREATE TABLE mt4ds.walk_edges (
                source VARCHAR, from_code VARCHAR, from_aui VARCHAR,
                from_cui VARCHAR, from_tty VARCHAR, to_code VARCHAR,
                to_aui VARCHAR, to_cui VARCHAR, to_tty VARCHAR,
                relationship VARCHAR, direction VARCHAR, edge_source VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE mt4ds.walk_closure_limited (
                source VARCHAR, from_code VARCHAR, from_aui VARCHAR,
                from_cui VARCHAR, from_tty VARCHAR, to_code VARCHAR,
                to_aui VARCHAR, to_cui VARCHAR, to_tty VARCHAR, depth INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("RXNORM", "1161610", "A1161610", "C0978484", "SCD",
                 "1156948", "A1156948", "C0978484", "IN", "isa", "parent", "umls_mrrel"),
                ("ICD10CM", "A.1", "A_CHILD", "C_A1", "PT",
                 "A", "A_PARENT", "C_A", "PT", "isa", "parent", "synthetic"),
            ],
        )
        conn.execute(
            "INSERT INTO mt4ds.walk_closure_limited VALUES ("
            "'ICD10CM', 'A.1', 'A_CHILD', 'C_A1', 'PT', "
            "'A', 'A_PARENT', 'C_A', 'PT', 1)"
        )
        yield conn
    finally:
        conn.close()


class TestWalkClosureSourceGateCr031:
    def test_uncovered_source_falls_back_to_walk_edges_bfs(self, stale_closure_con):
        """CR-031: RXNORM has walk_edges rows but zero closure rows — the
        ancestor walk at closure-eligible depth must fall back to the BFS
        instead of silently returning [] through the closure table."""
        ancestors = get_ancestors_prepared(
            [CodeRef(source="RXNORM", code="1161610")],
            stale_closure_con,
            max_depth=5,
        )
        assert [(r.source.code, r.target.code, r.depth) for r in ancestors] == [
            ("1161610", "1156948", 1),
        ]

    def test_covered_source_still_uses_closure(self, stale_closure_con):
        from medterm4ds.services.prepared_primitives import walk_closure_table

        assert walk_closure_table(stale_closure_con, 5, "ICD10CM") == (
            "mt4ds.walk_closure_limited"
        )
        ancestors = get_ancestors_prepared(
            [CodeRef(source="ICD10CM", code="A.1")], stale_closure_con, max_depth=5
        )
        assert [(r.target.code, r.depth) for r in ancestors] == [("A", 1)]

    def test_gate_semantics(self, stale_closure_con):
        from medterm4ds.services.prepared_primitives import walk_closure_table

        # Uncovered source -> None regardless of table existence.
        assert walk_closure_table(stale_closure_con, 5, "RXNORM") is None
        # Multiple sources: one uncovered poisons the whole set.
        assert walk_closure_table(stale_closure_con, 5, {"ICD10CM", "RXNORM"}) is None
        assert walk_closure_table(stale_closure_con, 5, {"ICD10CM"}) is not None
        # Depth beyond the closure bound is still None (pre-CR-031 behavior).
        assert walk_closure_table(stale_closure_con, 6, "ICD10CM") is None
        # No source gate -> legacy table-existence behavior retained.
        assert walk_closure_table(stale_closure_con, 5) == "mt4ds.walk_closure_limited"

    def test_closure_seed_sources_derived_from_strategies(self):
        """The build whitelist must track SOURCE_STRATEGIES (any strategy with
        hierarchy_edge_sql), not the old hardcoded 6-source list."""
        from medterm4ds.engines.duckdb.prepared import _walk_closure_seed_sources

        seeds = _walk_closure_seed_sources()
        # The three sources the old list excluded must now be seeded.
        assert {"RXNORM", "ATC", "MSH"} <= set(seeds)
        # CVX declares no hierarchy — must stay out.
        assert "CVX" not in seeds
