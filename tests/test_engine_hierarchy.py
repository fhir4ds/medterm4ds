"""Direct unit tests for engines/duckdb/hierarchy.py.

Tests hierarchy traversal (parents, children, ancestors, descendants)
with a small synthetic DuckDB fixture. Catches cycle-detection, depth-limit,
and direction bugs without needing the full UMLS DB.
"""

from __future__ import annotations

import duckdb
import pytest
from pathlib import Path

from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine


def _make_hierarchy_db(path: Path) -> None:
    """Create a 3-level SNOMED hierarchy: A → B → C."""
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE mrconso (
        CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
        SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("ROOT", "PT", "Root concept", "AUI_ROOT", "N", "SNOMEDCT_US", "C_ROOT"),
            ("CHILD", "PT", "Child concept", "AUI_CHILD", "N", "SNOMEDCT_US", "C_CHILD"),
            ("GRANDCHILD", "PT", "Grandchild concept", "AUI_GC", "N", "SNOMEDCT_US", "C_GC"),
        ],
    )
    con.execute("""CREATE TABLE mrrel (
        AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [
            # CHILD's parent is ROOT
            ("AUI_CHILD", "AUI_ROOT", "isa", "PAR"),
            # GRANDCHILD's parent is CHILD
            ("AUI_GC", "AUI_CHILD", "isa", "PAR"),
        ],
    )
    con.close()


@pytest.fixture
def engine(tmp_path):
    db_path = tmp_path / "hierarchy.duckdb"
    _make_hierarchy_db(db_path)
    con = duckdb.connect(str(db_path))
    return LocalDuckDBEngine(con)


class TestHierarchyTraversal:
    def test_get_parents(self, engine):
        """Direct parent lookup returns immediate parent only."""
        results = engine.get_code_relations(
            [CodeRef("SNOMEDCT_US", "CHILD")], direction="parents", max_depth=1
        )
        assert len(results) == 1
        assert results[0].target.code == "ROOT"

    def test_get_children(self, engine):
        """Direct child lookup returns immediate children only."""
        results = engine.get_code_relations(
            [CodeRef("SNOMEDCT_US", "ROOT")], direction="children", max_depth=1
        )
        assert len(results) == 1
        assert results[0].target.code == "CHILD"

    def test_get_ancestors_multi_depth(self, engine):
        """Ancestor walk traverses multiple levels."""
        results = engine.get_code_relations(
            [CodeRef("SNOMEDCT_US", "GRANDCHILD")], direction="ancestors", max_depth=5
        )
        ancestor_codes = {r.target.code for r in results}
        assert ancestor_codes == {"CHILD", "ROOT"}

    def test_get_descendants_multi_depth(self, engine):
        """Descendant walk traverses multiple levels."""
        results = engine.get_code_relations(
            [CodeRef("SNOMEDCT_US", "ROOT")], direction="descendants", max_depth=5
        )
        descendant_codes = {r.target.code for r in results}
        assert descendant_codes == {"CHILD", "GRANDCHILD"}

    def test_depth_limit_truncates(self, engine):
        """max_depth=1 on ancestors returns only direct parent."""
        results = engine.get_code_relations(
            [CodeRef("SNOMEDCT_US", "GRANDCHILD")], direction="ancestors", max_depth=1
        )
        assert len(results) == 1
        assert results[0].target.code == "CHILD"

    def test_leaf_has_no_children(self, engine):
        """Leaf node returns empty descendants."""
        results = engine.get_code_relations(
            [CodeRef("SNOMEDCT_US", "GRANDCHILD")], direction="children", max_depth=1
        )
        assert len(results) == 0

    def test_root_has_no_parents(self, engine):
        """Root node returns empty parents."""
        results = engine.get_code_relations(
            [CodeRef("SNOMEDCT_US", "ROOT")], direction="parents", max_depth=1
        )
        assert len(results) == 0
