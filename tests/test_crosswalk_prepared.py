"""Tests for crosswalk_prepared service over prepared mt4ds tables."""
from __future__ import annotations

import duckdb
import pytest

from medterm4ds.core.models import CodeMapping, CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.crosswalk import get_same_cui_mappings
from medterm4ds.services.crosswalk_prepared import get_crosswalk_mappings

# ---------------------------------------------------------------------------
# Fixture: DuckDB with prepared mt4ds tables
# ---------------------------------------------------------------------------

def _build_prepared_db(con: duckdb.DuckDBPyConnection) -> None:
    """Create a minimal DuckDB database with mt4ds prepared tables for testing."""
    # Create schemas
    con.execute("CREATE SCHEMA IF NOT EXISTS mt4ds")

    # Build mt4ds.best_atoms
    con.execute(
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
    best_atoms_rows = [
        # ICD10CM codes
        ("ICD10CM", "E11.9", "AUI_E119", "C_DIAB", "PT", "Type 2 diabetes mellitus", "N", True, 1),
        ("ICD10CM", "A01.0", "AUI_A010", "C_TYPHOID", "PT", "Typhoid fever", "N", True, 1),
        ("ICD10CM", "A01", "AUI_A01", "C_TYPHOID_GRP", "PT", "Typhoid and paratyphoid fevers", "N", True, 1),
        # SNOMEDCT_US codes
        ("SNOMEDCT_US", "44054006", "AUI_SNO_DIAB", "C_DIAB", "PT", "Diabetes mellitus type 2", "N", True, 1),
        ("SNOMEDCT_US", "4834000", "AUI_SNO_TYPHOID", "C_TYPHOID", "PT", "Typhoid fever", "N", True, 1),
        ("SNOMEDCT_US", "4834001", "AUI_SNO_T2", "C_TYPHOID_GRP", "PT", "Typhoid and paratyphoid fevers", "N", True, 1),
        ("SNOMEDCT_US", "999001", "AUI_SNO_BROAD", "C_BROAD", "PT", "Broad infectious disease", "N", True, 1),
        # RXNORM codes
        ("RXNORM", "86097", "AUI_RX_MET", "C_METFORMIN", "PT", "Metformin", "N", True, 1),
        # CVX
        ("CVX", "208", "AUI_CVX_208", "C_CVX", "PT", "COVID-19 vaccine", "N", True, 1),
        ("SNOMEDCT_US", "840539006", "AUI_SNO_CVX", "C_CVX", "PT", "COVID-19 vaccine product", "N", True, 1),
    ]
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        best_atoms_rows,
    )

    # Build mt4ds.same_cui_edges
    con.execute(
        """
        CREATE TABLE mt4ds.same_cui_edges (
            source VARCHAR,
            code VARCHAR,
            cui VARCHAR,
            target_source VARCHAR,
            target_code VARCHAR,
            target_aui VARCHAR,
            target_cui VARCHAR,
            target_tty VARCHAR
        )
        """
    )
    same_cui_rows = [
        # ICD10CM <-> SNOMEDCT_US via C_DIAB
        ("ICD10CM", "E11.9", "C_DIAB", "SNOMEDCT_US", "44054006", "AUI_SNO_DIAB", "C_DIAB", "PT"),
        # ICD10CM <-> SNOMEDCT_US via C_TYPHOID
        ("ICD10CM", "A01.0", "C_TYPHOID", "SNOMEDCT_US", "4834000", "AUI_SNO_TYPHOID", "C_TYPHOID", "PT"),
        # ICD10CM <-> SNOMEDCT_US via C_TYPHOID_GRP (ancestor group)
        ("ICD10CM", "A01", "C_TYPHOID_GRP", "SNOMEDCT_US", "4834001", "AUI_SNO_T2", "C_TYPHOID_GRP", "PT"),
        # ICD10CM <-> RXNORM (no shared CUI - should not appear)
        # CVX <-> SNOMEDCT_US via C_CVX
        ("CVX", "208", "C_CVX", "SNOMEDCT_US", "840539006", "AUI_SNO_CVX", "C_CVX", "PT"),
        # SNOMEDCT_US <-> ICD10CM (reverse direction)
        ("SNOMEDCT_US", "44054006", "C_DIAB", "ICD10CM", "E11.9", "AUI_E119", "C_DIAB", "PT"),
        ("SNOMEDCT_US", "4834000", "C_TYPHOID", "ICD10CM", "A01.0", "AUI_A010", "C_TYPHOID", "PT"),
    ]
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        same_cui_rows,
    )

    # Build mt4ds.walk_edges (parent hierarchy)
    con.execute(
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
    walk_rows = [
        # ICD10CM: A01.0 -> A01 (parent)
        ("ICD10CM", "A01.0", "AUI_A010", "C_TYPHOID", "PT",
         "A01", "AUI_A01", "C_TYPHOID_GRP", "PT",
         "isa", "parent", "umls_mrrel"),
        # SNOMEDCT_US: 4834000 -> 999001 (parent)
        ("SNOMEDCT_US", "4834000", "AUI_SNO_TYPHOID", "C_TYPHOID", "PT",
         "999001", "AUI_SNO_BROAD", "C_BROAD", "PT",
         "isa", "parent", "umls_mrrel"),
    ]
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        walk_rows,
    )


@pytest.fixture()
def prepared_db():
    """Return a DuckDB connection with prepared mt4ds tables."""
    con = duckdb.connect(":memory:")
    _build_prepared_db(con)
    yield con
    con.close()


def _promote_same_cui_to_crosswalk_edges(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.crosswalk_edges AS
        SELECT source, code, cui, target_source, target_code, target_aui,
               target_cui, target_tty, 'same_cui' AS relationship,
               'same_cui' AS match_type, 0 AS match_depth,
               'same_cui_edges' AS edge_source, 0 AS priority
        FROM mt4ds.same_cui_edges
        """
    )
    con.execute("DROP TABLE mt4ds.same_cui_edges")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExactSameCUIMapping:
    """Test 1: Exact same-CUI mapping (max_depth=0)."""

    def test_finds_cross_source_mapping(self, prepared_db):
        codes = [CodeRef(source="ICD10CM", code="E11.9")]
        results = get_crosswalk_mappings(codes, prepared_db)
        assert len(results) == 1
        m = results[0]
        assert isinstance(m, CodeMapping)
        assert m.source == CodeRef(source="ICD10CM", code="E11.9")
        assert m.target == CodeRef(source="SNOMEDCT_US", code="44054006")
        assert m.match_type == "same_cui"
        assert m.relationship == "equivalent"
        assert m.match_depth == 0

    def test_exact_mapping_has_display_names(self, prepared_db):
        codes = [CodeRef(source="ICD10CM", code="E11.9")]
        results = get_crosswalk_mappings(codes, prepared_db)
        assert len(results) == 1
        m = results[0]
        assert m.source_display == "Type 2 diabetes mellitus"
        assert m.target_display == "Diabetes mellitus type 2"

    def test_exact_mapping_has_provenance(self, prepared_db):
        codes = [CodeRef(source="ICD10CM", code="E11.9")]
        results = get_crosswalk_mappings(codes, prepared_db)
        assert len(results) == 1
        m = results[0]
        assert m.matched_via is not None
        assert m.matched_via.strategy == "same_cui"
        ops = [step.op for step in m.matched_via.steps]
        assert "input_atom" in ops
        assert "same_cui" in ops

    def test_exact_mapping_uses_crosswalk_edges_when_available(self, prepared_db):
        _promote_same_cui_to_crosswalk_edges(prepared_db)

        codes = [CodeRef(source="ICD10CM", code="E11.9")]
        results = get_crosswalk_mappings(codes, prepared_db)

        assert len(results) == 1
        assert results[0].target == CodeRef(source="SNOMEDCT_US", code="44054006")
        assert results[0].match_type == "same_cui"


class TestTargetSourceFilter:
    """Test 2: Mapping with target source filter."""

    def test_filter_to_single_target(self, prepared_db):
        codes = [CodeRef(source="ICD10CM", code="E11.9")]
        results = get_crosswalk_mappings(
            codes, prepared_db, target_sources=["RXNORM"]
        )
        # E11.9 shares CUI only with SNOMEDCT_US, not RXNORM
        assert len(results) == 0

    def test_filter_to_matching_target(self, prepared_db):
        codes = [CodeRef(source="ICD10CM", code="E11.9")]
        results = get_crosswalk_mappings(
            codes, prepared_db, target_sources=["SNOMEDCT_US"]
        )
        assert len(results) == 1
        assert results[0].target.source == "SNOMEDCT_US"

    def test_filter_to_multiple_targets(self, prepared_db):
        codes = [CodeRef(source="CVX", code="208")]
        results = get_crosswalk_mappings(
            codes, prepared_db, target_sources=["SNOMEDCT_US", "RXNORM"]
        )
        assert len(results) == 1
        assert results[0].target.source == "SNOMEDCT_US"


class TestSameCUICrosswalkPrimitive:
    """Direct same-CUI primitive uses canonical crosswalk semantics."""

    def test_same_cui_primitive_uses_crosswalk_edges_and_labels_same_cui(self, prepared_db):
        _promote_same_cui_to_crosswalk_edges(prepared_db)

        results = get_same_cui_mappings(
            [CodeRef(source="ICD10CM", code="E11.9")],
            prepared_db,
            target_sources=["SNOMEDCT_US"],
        )

        assert len(results) == 1
        assert results[0].target == CodeRef(source="SNOMEDCT_US", code="44054006")
        assert results[0].relationship == "equivalent"
        assert results[0].match_type == "same_cui"


class TestBroaderMapping:
    """Test 3: Broader mapping with depth > 0."""

    def test_broader_mapping_finds_ancestor_targets(self, prepared_db):
        # A01.0 (Typhoid fever) has parent A01 (Typhoid group)
        # A01 has same-CUI edge to SNOMED 4834000
        codes = [CodeRef(source="ICD10CM", code="A01.0")]
        results = get_crosswalk_mappings(codes, prepared_db, max_depth=1)
        # Should have exact same-CUI mapping for A01.0
        # AND broader mapping via A01 ancestor
        exact_matches = [m for m in results if m.match_type == "same_cui"]
        broader_matches = [m for m in results if m.match_type == "source_ancestor_same_cui"]
        assert len(exact_matches) == 1
        assert len(broader_matches) >= 1

        broader = broader_matches[0]
        assert broader.match_depth == 1
        assert broader.relationship == "source-is-narrower-than-target"
        assert broader.matched_via is not None
        assert broader.matched_via.strategy == "source_ancestor_same_cui"

    def test_no_broader_when_depth_zero(self, prepared_db):
        codes = [CodeRef(source="ICD10CM", code="A01.0")]
        results = get_crosswalk_mappings(codes, prepared_db, max_depth=0)
        broader_matches = [m for m in results if m.match_type == "source_ancestor_same_cui"]
        assert len(broader_matches) == 0

    def test_broader_dedupes_with_exact(self, prepared_db):
        # A01.0 has exact same-CUI mapping to SNOMED 4834000
        # A01 (parent) also has same-CUI mapping to SNOMED 4834000
        # Both would map to same target - should be deduped
        codes = [CodeRef(source="ICD10CM", code="A01.0")]
        results = get_crosswalk_mappings(codes, prepared_db, max_depth=1)
        target_keys = [(m.target.source, m.target.code) for m in results]
        # Check no duplicate (SNOMEDCT_US, 4834000) entries
        assert target_keys.count(("SNOMEDCT_US", "4834000")) == 1

    def test_broader_mapping_uses_crosswalk_edges_without_same_cui_edges(self, prepared_db):
        _promote_same_cui_to_crosswalk_edges(prepared_db)

        codes = [CodeRef(source="ICD10CM", code="A01.0")]
        results = get_crosswalk_mappings(codes, prepared_db, max_depth=1)

        assert [m.match_type for m in results] == [
            "same_cui",
            "source_ancestor_same_cui",
        ]

    def test_broader_mapping_falls_back_when_closure_lacks_source_cr031(self, prepared_db):
        """CR-031 (HIGH): a walk_closure_limited table with zero rows for the
        source (pre-fix build excluded RXNORM/ATC/MSH; here ICD10CM) must NOT
        dispatch ancestor lookups through it — the recursive walk_edges CTE
        serves the broader mapping instead of silently returning []."""
        # Stale closure table: exists, but has no ICD10CM rows (pre-CR-031
        # whitelist shape; walk_edges above carries the A01.0 -> A01 edge).
        prepared_db.execute(
            """
            CREATE TABLE mt4ds.walk_closure_limited (
                source VARCHAR, from_code VARCHAR, from_aui VARCHAR,
                from_cui VARCHAR, from_tty VARCHAR, to_code VARCHAR,
                to_aui VARCHAR, to_cui VARCHAR, to_tty VARCHAR, depth INTEGER
            )
            """
        )
        prepared_db.execute(
            "INSERT INTO mt4ds.walk_closure_limited VALUES ("
            "'SNOMEDCT_US', '4834000', 'AUI_SNO_TYPHOID', 'C_TYPHOID', 'PT', "
            "'999001', 'AUI_SNO_BROAD', 'C_BROAD', 'PT', 1)"
        )

        codes = [CodeRef(source="ICD10CM", code="A01.0")]
        results = get_crosswalk_mappings(codes, prepared_db, max_depth=1)

        broader = [m for m in results if m.match_type == "source_ancestor_same_cui"]
        assert len(broader) >= 1, (
            "CR-031 regression: broader mapping returned [] through an "
            f"uncovered closure table; got {[m.match_type for m in results]}"
        )
        assert broader[0].match_depth == 1


class TestCodeNotFound:
    """Test 4: Code not found returns empty."""

    def test_unknown_code_returns_empty(self, prepared_db):
        codes = [CodeRef(source="ICD10CM", code="ZZZ999")]
        results = get_crosswalk_mappings(codes, prepared_db)
        assert results == []

    def test_unknown_source_returns_empty(self, prepared_db):
        codes = [CodeRef(source="FAKESOURCE", code="123")]
        results = get_crosswalk_mappings(codes, prepared_db)
        assert results == []

    def test_empty_input_returns_empty(self, prepared_db):
        results = get_crosswalk_mappings([], prepared_db)
        assert results == []


class TestBatchProcessing:
    """Test 5: Batch processing preserves order."""

    def test_batch_preserves_order(self, prepared_db):
        codes = [
            CodeRef(source="CVX", code="208"),
            CodeRef(source="ICD10CM", code="E11.9"),
            CodeRef(source="ICD10CM", code="A01.0"),
        ]
        results = get_crosswalk_mappings(codes, prepared_db)
        # CVX 208 -> SNOMED 840539006
        assert results[0].source == CodeRef(source="CVX", code="208")
        assert results[0].target == CodeRef(source="SNOMEDCT_US", code="840539006")
        # ICD10CM E11.9 -> SNOMED 44054006
        assert results[1].source == CodeRef(source="ICD10CM", code="E11.9")
        assert results[1].target == CodeRef(source="SNOMEDCT_US", code="44054006")
        # ICD10CM A01.0 -> SNOMED 4834000
        assert results[2].source == CodeRef(source="ICD10CM", code="A01.0")
        assert results[2].target == CodeRef(source="SNOMEDCT_US", code="4834000")

    def test_multiple_codes_same_source(self, prepared_db):
        codes = [
            CodeRef(source="ICD10CM", code="E11.9"),
            CodeRef(source="ICD10CM", code="A01.0"),
        ]
        results = get_crosswalk_mappings(codes, prepared_db)
        assert len(results) == 2
        sources = [m.source for m in results]
        assert sources[0] == CodeRef(source="ICD10CM", code="E11.9")
        assert sources[1] == CodeRef(source="ICD10CM", code="A01.0")

    def test_reverse_crosswalk(self, prepared_db):
        """SNOMED -> ICD10CM should also work via same_cui_edges."""
        codes = [CodeRef(source="SNOMEDCT_US", code="44054006")]
        results = get_crosswalk_mappings(codes, prepared_db)
        assert len(results) == 1
        assert results[0].target == CodeRef(source="ICD10CM", code="E11.9")


class TestValidation:
    """Input validation tests."""

    def test_negative_max_depth_raises(self, prepared_db):
        codes = [CodeRef(source="ICD10CM", code="E11.9")]
        with pytest.raises(ValueError, match="max_depth must be non-negative"):
            get_crosswalk_mappings(codes, prepared_db, max_depth=-1)


class TestLocalEnginePreparedCrosswalk:
    """Engine mapping uses prepared crosswalk tables for common mapping paths."""

    def test_engine_uses_prepared_exact_mapping(self, prepared_db):
        engine = LocalDuckDBEngine(prepared_db)

        results = engine.get_code_mappings(
            [CodeRef(source="ICD10CM", code="E11.9")],
            target_sources=["SNOMEDCT_US"],
        )

        assert len(results) == 1
        assert results[0].source == CodeRef(source="ICD10CM", code="E11.9")
        assert results[0].target == CodeRef(source="SNOMEDCT_US", code="44054006")
        assert results[0].match_type == "same_cui"

    def test_engine_uses_crosswalk_edges_without_same_cui_edges(self, prepared_db):
        _promote_same_cui_to_crosswalk_edges(prepared_db)
        engine = LocalDuckDBEngine(prepared_db)

        results = engine.get_code_mappings(
            [CodeRef(source="ICD10CM", code="E11.9")],
            target_sources=["SNOMEDCT_US"],
        )

        assert len(results) == 1
        assert results[0].target == CodeRef(source="SNOMEDCT_US", code="44054006")
        assert results[0].match_type == "same_cui"

    def test_engine_uses_crosswalk_edges_without_same_cui_edges_for_source_ancestor(self, prepared_db):
        _promote_same_cui_to_crosswalk_edges(prepared_db)
        engine = LocalDuckDBEngine(prepared_db)

        results = engine.get_code_mappings(
            [CodeRef(source="ICD10CM", code="A01.0")],
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
        )

        assert [mapping.match_type for mapping in results] == [
            "same_cui",
            "source_ancestor_same_cui",
        ]

    def test_engine_uses_prepared_source_ancestor_mapping(self, prepared_db):
        engine = LocalDuckDBEngine(prepared_db)

        results = engine.get_code_mappings(
            [CodeRef(source="ICD10CM", code="A01.0")],
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
        )

        assert [mapping.match_type for mapping in results] == [
            "same_cui",
            "source_ancestor_same_cui",
        ]

    def test_engine_prepared_mapping_caps_each_input(self, prepared_db):
        engine = LocalDuckDBEngine(prepared_db)

        results = engine.get_code_mappings(
            [CodeRef(source="ICD10CM", code="A01.0")],
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
            max_results_per_code=1,
        )

        assert len(results) == 1
        assert results[0].match_type == "same_cui"

    def test_engine_prepared_mapping_preserves_duplicate_inputs(self, prepared_db):
        engine = LocalDuckDBEngine(prepared_db)

        results = engine.get_code_mappings(
            [
                CodeRef(source="ICD10CM", code="E11.9"),
                CodeRef(source="ICD10CM", code="E11.9"),
            ],
            target_sources=["SNOMEDCT_US"],
        )

        assert [mapping.target.code for mapping in results] == ["44054006", "44054006"]

    def test_engine_uses_prepared_target_ancestor_mapping(self, prepared_db):
        engine = LocalDuckDBEngine(prepared_db)

        results = engine.get_code_mappings(
            [CodeRef(source="ICD10CM", code="A01.0")],
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
            include_target_ancestors=True,
        )

        target_ancestors = [
            mapping for mapping in results
            if mapping.match_type == "target_ancestor"
        ]
        assert len(target_ancestors) == 1
        assert target_ancestors[0].target == CodeRef(source="SNOMEDCT_US", code="999001")
        assert target_ancestors[0].relationship == "source-is-narrower-than-target"
        assert target_ancestors[0].match_depth == 1

    def test_engine_uses_crosswalk_edges_without_same_cui_edges_for_target_ancestor(self, prepared_db):
        _promote_same_cui_to_crosswalk_edges(prepared_db)
        engine = LocalDuckDBEngine(prepared_db)

        results = engine.get_code_mappings(
            [CodeRef(source="ICD10CM", code="A01.0")],
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
            include_target_ancestors=True,
        )

        target_ancestors = [
            mapping for mapping in results
            if mapping.match_type == "target_ancestor"
        ]
        assert len(target_ancestors) == 1
        assert target_ancestors[0].target == CodeRef(source="SNOMEDCT_US", code="999001")
