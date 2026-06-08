"""Tests for non-RxNorm patient-friendly resolution using prepared tables.

Creates synthetic DuckDB databases with the required mt4ds prepared tables
and verifies source-specific workflows for ICD10CM, SNOMEDCT_US, CVX, LOINC,
and mixed-source batches.
"""

from __future__ import annotations

import duckdb
import pytest

from medterm4ds import get_patient_friendly_names
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.duckdb.prepared import (
    PATIENT_FRIENDLY_POLICY_VERSION,
    PREPARED_SCHEMA_VERSION,
)
from medterm4ds.services.patient_friendly_materialized import (
    materialize_patient_friendly_resolutions,
    materialize_patient_friendly_source,
)
from medterm4ds.services.patient_friendly_prepared import (
    get_non_rxnorm_patient_friendly,
)

# ---------------------------------------------------------------------------
# Helpers to create synthetic mt4ds tables
# ---------------------------------------------------------------------------


def _create_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS mt4ds")


def _create_best_atoms(con: duckdb.DuckDBPyConnection) -> None:
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


def _create_walk_edges(con: duckdb.DuckDBPyConnection) -> None:
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


def _create_friendly_atoms(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.friendly_atoms (
            cui VARCHAR,
            source VARCHAR,
            code VARCHAR,
            aui VARCHAR,
            tty VARCHAR,
            name VARCHAR,
            friendly_source VARCHAR,
            is_broad BOOLEAN,
            is_heading BOOLEAN
        )
        """
    )


def _create_same_cui_edges(con: duckdb.DuckDBPyConnection) -> None:
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


def _promote_same_cui_to_crosswalk_edges(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.crosswalk_edges AS
        SELECT
          source,
          code,
          cui,
          target_source,
          target_code,
          target_aui,
          target_cui,
          target_tty,
          'same_cui' AS relationship,
          'same_cui' AS match_type,
          0 AS match_depth,
          'same_cui_edges' AS edge_source,
          0 AS priority
        FROM mt4ds.same_cui_edges
        """
    )
    con.execute("DROP TABLE mt4ds.same_cui_edges")


def _create_snomed_top_level_depth(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.snomed_top_level_depth (
            code VARCHAR,
            min_top_depth INTEGER
        )
        """
    )


def _create_cvx_metadata(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.cvx_metadata (
            code VARCHAR,
            group_name VARCHAR,
            short_name VARCHAR
        )
        """
    )


def _create_patient_friendly_strategy(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.patient_friendly_strategy (
            source VARCHAR,
            phase VARCHAR,
            walk_kind VARCHAR,
            target_source VARCHAR,
            target_tty VARCHAR,
            match_type VARCHAR,
            priority INTEGER,
            max_depth INTEGER,
            stop_on_hit BOOLEAN,
            guard VARCHAR
        )
        """
    )


def _create_patient_friendly_resolutions(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE mt4ds.patient_friendly_resolutions (
            source VARCHAR,
            code VARCHAR,
            name VARCHAR,
            friendly_source VARCHAR,
            match_type VARCHAR,
            match_depth INTEGER,
            technical_name VARCHAR,
            selected_candidate_id BIGINT,
            policy_version VARCHAR,
            umls_release VARCHAR,
            prepared_schema_version VARCHAR,
            generated_at TIMESTAMP
        )
        """
    )


def _setup_all_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create all required mt4ds prepared tables."""
    _create_schema(con)
    _create_best_atoms(con)
    _create_walk_edges(con)
    _create_friendly_atoms(con)
    _create_same_cui_edges(con)
    _create_snomed_top_level_depth(con)
    _create_cvx_metadata(con)
    _create_patient_friendly_strategy(con)
    _create_patient_friendly_resolutions(con)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def con():
    """Create an in-memory DuckDB with all required mt4ds tables."""
    c = duckdb.connect(":memory:")
    _setup_all_tables(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Test 1: ICD10CM native hierarchy walk finds CHV candidate
# ---------------------------------------------------------------------------


def test_icd10cm_native_walk_finds_chv(con: duckdb.DuckDBPyConnection) -> None:
    """ICD10CM code walks native parents and finds a CHV friendly name."""
    # Seed best_atoms: child and parent ICD10CM codes
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
            ("ICD10CM", "E11", "A_E11", "C_DIAB_PARENT", "HT",
             "Type 2 diabetes mellitus", "N", True, 1),
        ],
    )
    # Walk edge: E11.9 -> E11
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "E11", "A_E11", "C_DIAB_PARENT", "HT",
             "isa", "parent", "test"),
        ],
    )
    # Friendly atom on parent CUI
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB_PARENT", "CHV", "CHV_DIAB", "A_CHV_DIAB", "PT",
             "diabetes", "CHV", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="E11.9")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "diabetes"
    assert r.friendly_source == "CHV"
    assert r.match_type == "broader"
    assert r.match_depth == 1
    assert r.technical_name == "Type 2 diabetes mellitus without complications"


def test_icd10cm_exact_same_cui_friendly_hit_is_exact(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Native depth-0 MEDLINEPLUS/CHV hits are exact same-CUI candidates."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB", "MEDLINEPLUS", "MP_DIAB", "A_MP_DIAB", "MH",
             "Diabetes", "MEDLINEPLUS", False, False),
        ],
    )

    result = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="E11.9")],
        con,
    )[0]

    assert result.name == "Diabetes"
    assert result.friendly_source == "MEDLINEPLUS"
    assert result.match_type == "exact"
    assert result.match_depth == 0
    assert result.matched_via is not None
    assert result.matched_via.steps[1].op == "exact_same_cui"


def test_local_engine_uses_prepared_patient_friendly_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Public LocalDuckDBEngine can resolve patient-friendly names without raw UMLS tables."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
            ("ICD10CM", "E11", "A_E11", "C_DIAB_PARENT", "HT",
             "Type 2 diabetes mellitus", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "E11", "A_E11", "C_DIAB_PARENT", "HT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB_PARENT", "CHV", "CHV_DIAB", "A_CHV_DIAB", "PT",
             "diabetes", "CHV", False, False),
        ],
    )

    result = get_patient_friendly_names(
        [CodeRef(source="ICD10CM", code="E11.9")],
        LocalDuckDBEngine(con),
    )[0]

    assert result.name == "diabetes"
    assert result.friendly_source == "CHV"
    assert result.match_type == "broader"


def test_local_engine_prefers_materialized_patient_friendly_resolutions(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Public engine reads complete materialized resolution rows before live traversal."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
        ],
    )
    con.executemany(
        """
        INSERT INTO mt4ds.patient_friendly_resolutions
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
        """,
        [
            (
                "ICD10CM",
                "E11.9",
                "Diabetes",
                "MEDLINEPLUS",
                "broader",
                1,
                "Type 2 diabetes mellitus without complications",
                1001,
                PATIENT_FRIENDLY_POLICY_VERSION,
                "synthetic",
                PREPARED_SCHEMA_VERSION,
            ),
        ],
    )

    result = get_patient_friendly_names(
        [CodeRef(source="ICD10CM", code="E11.9")],
        LocalDuckDBEngine(con),
    )[0]

    assert result.name == "Diabetes"
    assert result.friendly_source == "MEDLINEPLUS"
    assert result.match_type == "broader"
    assert result.matched_via is not None
    assert result.matched_via.strategy == "patient_friendly_resolutions"

    strict_result = get_patient_friendly_names(
        [CodeRef(source="ICD10CM", code="E11.9")],
        LocalDuckDBEngine(con, require_patient_friendly_resolutions=True),
    )[0]
    assert strict_result.name == "Diabetes"
    assert strict_result.matched_via is not None
    assert strict_result.matched_via.strategy == "patient_friendly_resolutions"


def test_local_engine_dedupes_materialized_patient_friendly_resolution_rows(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Duplicate materialized rows should not force fallback to live traversal."""
    con.executemany(
        """
        INSERT INTO mt4ds.patient_friendly_resolutions
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
        """,
        [
            (
                "ICD10CM",
                "E11.9",
                "Older Diabetes",
                "MEDLINEPLUS",
                "broader",
                1,
                "Type 2 diabetes mellitus without complications",
                1001,
                PATIENT_FRIENDLY_POLICY_VERSION,
                "synthetic",
                PREPARED_SCHEMA_VERSION,
            ),
            (
                "ICD10CM",
                "E11.9",
                "Newer Diabetes",
                "MEDLINEPLUS",
                "broader",
                1,
                "Type 2 diabetes mellitus without complications",
                1002,
                PATIENT_FRIENDLY_POLICY_VERSION,
                "synthetic",
                PREPARED_SCHEMA_VERSION,
            ),
        ],
    )

    result = get_patient_friendly_names(
        [CodeRef(source="ICD10CM", code="E11.9")],
        LocalDuckDBEngine(con, require_patient_friendly_resolutions=True),
    )[0]

    assert result.name == "Newer Diabetes"
    assert result.matched_via is not None
    assert result.matched_via.strategy == "patient_friendly_resolutions"


def test_local_engine_strict_resolution_mode_fails_closed(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Strict runtime mode refuses live traversal when materialized rows miss."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB", "MEDLINEPLUS", "MP_DIAB", "A_MP_DIAB", "MH",
             "Diabetes", "MEDLINEPLUS", False, False),
        ],
    )

    with pytest.raises(RuntimeError, match="patient_friendly_resolutions"):
        get_patient_friendly_names(
            [CodeRef(source="ICD10CM", code="E11.9")],
            LocalDuckDBEngine(con, require_patient_friendly_resolutions=True),
        )


def test_local_engine_strict_resolution_mode_rejects_stale_schema_rows(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Strict runtime mode rejects resolution rows from old prepared schemas."""
    con.executemany(
        """
        INSERT INTO mt4ds.patient_friendly_resolutions
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
        """,
        [
            (
                "ICD10CM",
                "E11.9",
                "Diabetes",
                "MEDLINEPLUS",
                "broader",
                1,
                "Type 2 diabetes mellitus without complications",
                1001,
                PATIENT_FRIENDLY_POLICY_VERSION,
                "synthetic",
                "0.0",
            ),
        ],
    )

    with pytest.raises(RuntimeError, match="patient_friendly_resolutions"):
        get_patient_friendly_names(
            [CodeRef(source="ICD10CM", code="E11.9")],
            LocalDuckDBEngine(con, require_patient_friendly_resolutions=True),
        )


def test_materialize_patient_friendly_resolutions_populates_tables(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Build/review materialization writes candidate, path, and resolution rows."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
            ("ICD10CM", "E11", "A_E11", "C_DIAB_PARENT", "HT",
             "Type 2 diabetes mellitus", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "E11", "A_E11", "C_DIAB_PARENT", "HT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB_PARENT", "MEDLINEPLUS", "MP_DIAB", "A_MP_DIAB", "MH",
             "Diabetes", "MEDLINEPLUS", False, False),
        ],
    )

    summary = materialize_patient_friendly_resolutions(
        [CodeRef(source="ICD10CM", code="E11.9")],
        con,
    )

    assert summary["inputs"] == 1
    assert summary["candidates"] == 1
    assert summary["resolutions"] == 1
    assert summary["paths"] >= 1
    assert summary["missing_resolutions"] == 0
    assert summary["friendly_resolutions"] == 1
    assert summary["original_fallbacks"] == 0
    assert summary["match_types"] == {"broader": 1}
    assert summary["resolution_coverage"] == 1.0

    resolution = con.execute(
        """
        SELECT name, friendly_source, match_type, policy_version
        FROM mt4ds.patient_friendly_resolutions
        WHERE source = 'ICD10CM' AND code = 'E11.9'
        """
    ).fetchone()
    assert resolution == (
        "Diabetes",
        "MEDLINEPLUS",
        "broader",
        PATIENT_FRIENDLY_POLICY_VERSION,
    )

    candidate = con.execute(
        """
        SELECT candidate_origin, candidate_name, rank_features
        FROM mt4ds.patient_friendly_candidates
        WHERE source = 'ICD10CM' AND code = 'E11.9'
        """
    ).fetchone()
    assert candidate == (
        "native_hierarchy",
        "Diabetes",
        "match_depth=1;frontier_depth=1;friendly_source=MEDLINEPLUS;"
        "friendly_source_priority=0;match_type=broader",
    )


def test_materialize_patient_friendly_resolutions_tags_exact_same_cui(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Build/review materialization records exact same-CUI candidate origin."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB", "MEDLINEPLUS", "MP_DIAB", "A_MP_DIAB", "MH",
             "Diabetes", "MEDLINEPLUS", False, False),
        ],
    )

    summary = materialize_patient_friendly_resolutions(
        [CodeRef(source="ICD10CM", code="E11.9")],
        con,
    )

    assert summary["resolutions"] == 1
    assert summary["candidates"] == 1
    candidate = con.execute(
        """
        SELECT candidate_origin, candidate_name, match_type, match_depth,
               rank_features
        FROM mt4ds.patient_friendly_candidates
        WHERE source = 'ICD10CM' AND code = 'E11.9'
        """
    ).fetchone()
    assert candidate == (
        "exact_same_cui",
        "Diabetes",
        "exact",
        0,
        "match_depth=0;frontier_depth=0;friendly_source=MEDLINEPLUS;"
        "friendly_source_priority=0;match_type=exact",
    )


def test_materialize_patient_friendly_resolutions_keeps_unselected_frontier_candidates(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Build/review materialization stores non-selected native frontier candidates."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "J00", "A_J00", "C_COLD", "PT",
             "Acute nasopharyngitis", "N", True, 1),
            ("ICD10CM", "J06", "A_J06", "C_URTI", "HT",
             "Upper respiratory infection", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "J00", "A_J00", "C_COLD", "PT",
             "J06", "A_J06", "C_URTI", "HT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_URTI", "MEDLINEPLUS", "MP_URTI", "A_MP_URTI", "MH",
             "Upper Respiratory Infections", "MEDLINEPLUS", False, False),
            ("C_URTI", "CHV", "CHV_URTI", "A_CHV_URTI", "PT",
             "cold and flu", "CHV", False, False),
        ],
    )

    summary = materialize_patient_friendly_resolutions(
        [CodeRef(source="ICD10CM", code="J00")],
        con,
    )

    assert summary["resolutions"] == 1
    assert summary["candidates"] == 2

    candidates = con.execute(
        """
        SELECT candidate_name, candidate_source, candidate_origin, match_depth,
               rank_features
        FROM mt4ds.patient_friendly_candidates
        WHERE source = 'ICD10CM' AND code = 'J00'
        ORDER BY candidate_name
        """
    ).fetchall()
    assert candidates == [
        (
            "Upper Respiratory Infections",
            "MEDLINEPLUS",
            "native_hierarchy",
            1,
            "match_depth=1;frontier_depth=1;friendly_source=MEDLINEPLUS;"
            "friendly_source_priority=0;match_type=broader",
        ),
        (
            "cold and flu",
            "CHV",
            "native_hierarchy",
            1,
            "match_depth=1;frontier_depth=1;friendly_source=CHV;"
            "friendly_source_priority=1;match_type=broader",
        ),
    ]


def test_materialize_patient_friendly_resolutions_keeps_unselected_snomed_fallback_candidates(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Build/review materialization stores non-selected guarded SNOMED candidates."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A00.0", "A_A00", "C_CHOLERA", "PT",
             "Cholera due to Vibrio cholerae", "N", True, 1),
            ("SNOMEDCT_US", "63650001", "A_SN_CHOLERA", "C_CHOLERA", "PT",
             "Cholera", "N", True, 1),
            ("SNOMEDCT_US", "40930007", "A_SN_INFECT", "C_INFECT", "PT",
             "Intestinal infectious disease", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A00.0", "C_CHOLERA", "SNOMEDCT_US", "63650001",
             "A_SN_CHOLERA", "C_CHOLERA", "PT"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "63650001", "A_SN_CHOLERA", "C_CHOLERA", "PT",
             "40930007", "A_SN_INFECT", "C_INFECT", "PT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [("63650001", 6), ("40930007", 5)],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_INFECT", "MEDLINEPLUS", "MP_INFECT", "A_MP_INFECT", "MH",
             "Intestinal Infections", "MEDLINEPLUS", False, False),
            ("C_INFECT", "CHV", "CHV_INFECT", "A_CHV_INFECT", "PT",
             "intestinal infection", "CHV", False, False),
        ],
    )

    summary = materialize_patient_friendly_resolutions(
        [CodeRef(source="ICD10CM", code="A00.0")],
        con,
    )

    assert summary["resolutions"] == 1
    assert summary["candidates"] == 2

    candidates = con.execute(
        """
        SELECT candidate_name, candidate_source, candidate_origin, match_depth,
               rank_features
        FROM mt4ds.patient_friendly_candidates
        WHERE source = 'ICD10CM' AND code = 'A00.0'
        ORDER BY candidate_name
        """
    ).fetchall()
    assert candidates == [
        (
            "Intestinal Infections",
            "MEDLINEPLUS",
            "snomed_fallback",
            1,
            "match_depth=1;frontier_depth=1;friendly_source=MEDLINEPLUS;"
            "friendly_source_priority=0;match_type=snomed_fallback",
        ),
        (
            "intestinal infection",
            "CHV",
            "snomed_fallback",
            1,
            "match_depth=1;frontier_depth=1;friendly_source=CHV;"
            "friendly_source_priority=1;match_type=snomed_fallback",
        ),
    ]


def test_materialize_patient_friendly_resolutions_tags_direct_snomed_guarded_walk(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Direct SNOMED hierarchy fallback has explicit origin and no duplicate selected row."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "12345", "A_SN_12345", "C_SPECIFIC", "PT",
             "Specific disorder", "N", True, 1),
            ("SNOMEDCT_US", "67890", "A_SN_67890", "C_PARENT", "PT",
             "Specific parent disorder", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "12345", "A_SN_12345", "C_SPECIFIC", "PT",
             "67890", "A_SN_67890", "C_PARENT", "PT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [("12345", 6), ("67890", 5)],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_PARENT", "MEDLINEPLUS", "MP_PARENT", "A_MP_PARENT", "MH",
             "Specific Parent", "MEDLINEPLUS", False, False),
        ],
    )

    summary = materialize_patient_friendly_resolutions(
        [CodeRef(source="SNOMEDCT_US", code="12345")],
        con,
    )

    assert summary["resolutions"] == 1
    assert summary["candidates"] == 1
    candidates = con.execute(
        """
        SELECT candidate_name, candidate_source, candidate_origin, match_type,
               match_depth
        FROM mt4ds.patient_friendly_candidates
        WHERE source = 'SNOMEDCT_US' AND code = '12345'
        """
    ).fetchall()
    assert candidates == [
        ("Specific Parent", "MEDLINEPLUS", "direct_snomed_guarded_walk", "broader", 1),
    ]


def test_materialize_patient_friendly_source_processes_chunks(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Source-wide materialization streams source inventory through chunks."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A01", "A_A01", "C_A01", "PT", "A01 display", "N", True, 1),
            ("ICD10CM", "A01.1", "A_A011", "C_A011", "PT", "A01.1 display", "N", True, 1),
            ("ICD10CM", "B01", "A_B01", "C_B01", "PT", "B01 display", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_A01", "CHV", "CHV_A01", "A_CHV_A01", "PT", "a one", "CHV", False, False),
            ("C_A011", "CHV", "CHV_A011", "A_CHV_A011", "PT", "a one one", "CHV", False, False),
            ("C_B01", "CHV", "CHV_B01", "A_CHV_B01", "PT", "b one", "CHV", False, False),
        ],
    )

    summary = materialize_patient_friendly_source(
        "ICD10CM",
        con,
        chunk_size=2,
        replace_existing=True,
    )

    assert summary["source"] == "ICD10CM"
    assert summary["chunks"] == 2
    assert summary["inputs"] == 3
    assert summary["resolutions"] == 3
    assert summary["missing_resolutions"] == 0
    assert summary["friendly_resolutions"] == 3
    assert summary["original_fallbacks"] == 0
    assert summary["match_types"] == {"exact": 3}
    assert summary["resolution_coverage"] == 1.0

    count = con.execute(
        """
        SELECT COUNT(*)
        FROM mt4ds.patient_friendly_resolutions
        WHERE source = 'ICD10CM'
        """
    ).fetchone()[0]
    assert count == 3


# ---------------------------------------------------------------------------
# Test 2: ICD10CM returns original when no friendly candidate found
# ---------------------------------------------------------------------------


def test_icd10cm_returns_original_when_no_friendly(con: duckdb.DuckDBPyConnection) -> None:
    """ICD10CM code with no friendly atoms returns original display."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "Z99.9", "A_Z999", "C_UNKNOWN", "PT",
             "Dependence on enabling machines", "N", True, 1),
        ],
    )
    # No walk edges, no friendly atoms for this CUI

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="Z99.9")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "original"
    assert r.name == "Dependence on enabling machines"
    assert r.friendly_source == "ICD10CM"


# ---------------------------------------------------------------------------
# Test 3: SNOMED exact target routing to ICD10CM
# ---------------------------------------------------------------------------


def test_snomed_exact_target_routing_to_icd10cm(con: duckdb.DuckDBPyConnection) -> None:
    """SNOMED code crosswalks to ICD10CM and finds friendly name via target."""
    # SNOMED best atom
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "73211009", "A_SN_DIAB", "C_DIAB", "PT",
             "Diabetes mellitus type 2", "N", True, 1),
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
        ],
    )
    # Same-CUI crosswalk: SNOMED -> ICD10CM
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "73211009", "C_DIAB", "ICD10CM", "E11.9",
             "A_E119", "C_DIAB", "PT"),
        ],
    )
    _promote_same_cui_to_crosswalk_edges(con)
    # Friendly atom on the shared CUI
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB", "MEDLINEPLUS", "MP_DIAB", "A_MP_DIAB", "MH",
             "Type 2 Diabetes", "MEDLINEPLUS", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="SNOMEDCT_US", code="73211009")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "Type 2 Diabetes"
    assert r.friendly_source == "MEDLINEPLUS"
    assert r.match_type == "same_cui"


def test_materialize_patient_friendly_resolutions_tags_snomed_same_cui_target_route(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """SNOMED target-source exact routes materialize as same-CUI crosswalks."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "73211009", "A_SN_DIAB", "C_DIAB", "PT",
             "Diabetes mellitus type 2", "N", True, 1),
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus without complications", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "73211009", "C_DIAB", "ICD10CM", "E11.9",
             "A_E119", "C_DIAB", "PT"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB", "MEDLINEPLUS", "MP_DIAB", "A_MP_DIAB", "MH",
             "Type 2 Diabetes", "MEDLINEPLUS", False, False),
        ],
    )

    summary = materialize_patient_friendly_resolutions(
        [CodeRef(source="SNOMEDCT_US", code="73211009")],
        con,
    )

    assert summary["resolutions"] == 1
    assert summary["candidates"] == 1
    selected = con.execute(
        """
        SELECT c.candidate_origin, c.candidate_name, c.match_type,
               c.match_depth, c.rank_features
        FROM mt4ds.patient_friendly_resolutions r
        JOIN mt4ds.patient_friendly_candidates c
          ON c.candidate_id = r.selected_candidate_id
        WHERE r.source = 'SNOMEDCT_US' AND r.code = '73211009'
        """
    ).fetchone()
    assert selected == (
        "same_cui_crosswalk",
        "Type 2 Diabetes",
        "same_cui",
        0,
        "match_depth=0;frontier_depth=0;friendly_source=MEDLINEPLUS;"
        "friendly_source_priority=0;match_type=same_cui",
    )


def test_snomed_walks_target_hierarchy_before_direct_snomed(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """SNOMED routes through target source hierarchy before direct SNOMED walk."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "111", "A_SN_111", "C_SN", "PT",
             "Specific SNOMED concept", "N", True, 1),
            ("ICD10CM", "A01.1", "A_A011", "C_TARGET", "PT",
             "Specific ICD target", "N", True, 1),
            ("ICD10CM", "A01", "A_A01", "C_TARGET_PARENT", "HT",
             "ICD target parent", "N", True, 1),
            ("SNOMEDCT_US", "222", "A_SN_222", "C_SN_PARENT", "PT",
             "Direct SNOMED parent", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "111", "C_SN", "ICD10CM", "A01.1",
             "A_A011", "C_TARGET", "PT"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A01.1", "A_A011", "C_TARGET", "PT",
             "A01", "A_A01", "C_TARGET_PARENT", "HT",
             "isa", "parent", "test"),
            ("SNOMEDCT_US", "111", "A_SN_111", "C_SN", "PT",
             "222", "A_SN_222", "C_SN_PARENT", "PT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [("111", 6), ("222", 5)],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_TARGET_PARENT", "MEDLINEPLUS", "MP_TARGET", "A_MP_TARGET", "MH",
             "Target Hierarchy Friendly", "MEDLINEPLUS", False, False),
            ("C_SN_PARENT", "MEDLINEPLUS", "MP_SNOMED", "A_MP_SNOMED", "MH",
             "Direct SNOMED Friendly", "MEDLINEPLUS", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="SNOMEDCT_US", code="111")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "Target Hierarchy Friendly"
    assert r.friendly_source == "MEDLINEPLUS"
    assert r.match_type == "snomed_to_target_native_hierarchy"
    assert r.match_depth == 1


# ---------------------------------------------------------------------------
# Test 4: SNOMED guard blocks overly broad results
# ---------------------------------------------------------------------------


def test_snomed_guard_blocks_overly_broad(con: duckdb.DuckDBPyConnection) -> None:
    """SNOMED walk rejects ancestors with min_top_depth <= guard depth."""
    # SNOMED code with no crosswalk targets
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "12345", "A_SN_12345", "C_SPECIFIC", "PT",
             "Specific disorder", "N", True, 1),
            ("SNOMEDCT_US", "99999", "A_SN_99999", "C_BROAD", "PT",
             "Clinical finding", "N", True, 1),
        ],
    )
    # Walk edge from specific to broad SNOMED code
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "12345", "A_SN_12345", "C_SPECIFIC", "PT",
             "99999", "A_SN_99999", "C_BROAD", "PT",
             "isa", "parent", "test"),
        ],
    )
    # Broad ancestor has min_top_depth = 1 (too shallow, should be blocked)
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [("99999", 1)],
    )
    # Friendly atom on the broad CUI -- should NOT be used due to guard
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_BROAD", "CHV", "CHV_BROAD", "A_CHV_BROAD", "PT",
             "clinical finding", "CHV", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="SNOMEDCT_US", code="12345")], con
    )
    assert len(results) == 1
    r = results[0]
    # Guard should have blocked the broad result
    assert r.match_type == "original"
    assert r.name == "Specific disorder"


# ---------------------------------------------------------------------------
# Test 5: CVX returns original display
# ---------------------------------------------------------------------------


def test_cvx_returns_original_display(con: duckdb.DuckDBPyConnection) -> None:
    """CVX code without group metadata returns original display."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("CVX", "207", "A_CVX_207", "C_CVX_207", "PT",
             "COVID-19 vaccine, mRNA", "N", True, 1),
        ],
    )
    # No cvx_metadata rows for this code

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="CVX", code="207")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "original"
    assert r.name == "COVID-19 vaccine, mRNA"
    assert r.friendly_source == "CVX"


# ---------------------------------------------------------------------------
# Test 6: LOINC returns original when no tiers match
# ---------------------------------------------------------------------------


def test_loinc_returns_original_when_no_tiers(con: duckdb.DuckDBPyConnection) -> None:
    """LOINC code with no friendly candidates returns original display."""
    con.execute("CREATE SCHEMA IF NOT EXISTS main")
    con.execute("CREATE TABLE IF NOT EXISTS main.mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR)")
    con.execute("CREATE TABLE IF NOT EXISTS mt4ds.atoms (source VARCHAR, code VARCHAR, aui VARCHAR, cui VARCHAR, tty VARCHAR, name VARCHAR, suppress VARCHAR, is_active BOOLEAN)")
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("LNC", "12345-6", "A_LNC_12345", "C_LNC_12345", "LN",
             "Some obscure lab test", "N", True, 1),
        ],
    )
    # No friendly atoms, no walk edges, no SNOMED crosswalk

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="LNC", code="12345-6")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "original"
    assert r.name == "Some obscure lab test"


# ---------------------------------------------------------------------------
# Test 7: MEDLINEPLUS preferred over CHV at same depth
# ---------------------------------------------------------------------------


def test_medlineplus_preferred_over_chv_same_depth(con: duckdb.DuckDBPyConnection) -> None:
    """When MEDLINEPLUS and CHV are at the same depth, MEDLINEPLUS wins."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "J00", "A_J00", "C_COLD", "PT",
             "Acute nasopharyngitis", "N", True, 1),
            ("ICD10CM", "J06", "A_J06", "C_URTI", "HT",
             "Upper respiratory infection", "N", True, 1),
        ],
    )
    # Walk edge: J00 -> J06
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "J00", "A_J00", "C_COLD", "PT",
             "J06", "A_J06", "C_URTI", "HT",
             "isa", "parent", "test"),
        ],
    )
    # Both MEDLINEPLUS and CHV on the same parent CUI
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_URTI", "MEDLINEPLUS", "MP_URTI", "A_MP_URTI", "MH",
             "Upper Respiratory Infections", "MEDLINEPLUS", False, False),
            ("C_URTI", "CHV", "CHV_URTI", "A_CHV_URTI", "PT",
             "cold and flu", "CHV", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="J00")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "Upper Respiratory Infections"
    assert r.friendly_source == "MEDLINEPLUS"


def test_closest_frontier_beats_farther_medlineplus(con: duckdb.DuckDBPyConnection) -> None:
    """A closer acceptable CHV hit wins over a farther MEDLINEPLUS ancestor."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "J00", "A_J00", "C_COLD", "PT",
             "Acute nasopharyngitis", "N", True, 1),
            ("ICD10CM", "J06", "A_J06", "C_URTI", "HT",
             "Upper respiratory infection", "N", True, 1),
            ("ICD10CM", "J00-J99", "A_J00J99", "C_RESP", "HT",
             "Diseases of the respiratory system", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "J00", "A_J00", "C_COLD", "PT",
             "J06", "A_J06", "C_URTI", "HT",
             "isa", "parent", "test"),
            ("ICD10CM", "J06", "A_J06", "C_URTI", "HT",
             "J00-J99", "A_J00J99", "C_RESP", "HT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_URTI", "CHV", "CHV_URTI", "A_CHV_URTI", "PT",
             "cold and flu", "CHV", False, False),
            ("C_RESP", "MEDLINEPLUS", "MP_RESP", "A_MP_RESP", "MH",
             "Respiratory Diseases", "MEDLINEPLUS", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="J00")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "cold and flu"
    assert r.friendly_source == "CHV"
    assert r.match_depth == 1


# ---------------------------------------------------------------------------
# Test 8: Batch processing preserves input order
# ---------------------------------------------------------------------------


def test_batch_preserves_input_order(con: duckdb.DuckDBPyConnection) -> None:
    """Multiple codes in one batch preserve input order in results."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A01", "A_A01", "C_TYPH", "PT",
             "Typhoid fever", "N", True, 1),
            ("ICD10CM", "B01", "A_B01", "C_POX", "PT",
             "Chickenpox", "N", True, 1),
            ("ICD10CM", "C01", "A_C01", "C_TUMOR", "PT",
             "Malignant neoplasm", "N", True, 1),
        ],
    )

    codes = [
        CodeRef(source="ICD10CM", code="C01"),
        CodeRef(source="ICD10CM", code="A01"),
        CodeRef(source="ICD10CM", code="B01"),
    ]
    results = get_non_rxnorm_patient_friendly(codes, con)

    assert len(results) == 3
    assert results[0].code.code == "C01"
    assert results[1].code.code == "A01"
    assert results[2].code.code == "B01"


# ---------------------------------------------------------------------------
# Test 9: Mixed sources in one batch
# ---------------------------------------------------------------------------


def test_mixed_sources_in_one_batch(con: duckdb.DuckDBPyConnection) -> None:
    """Batch with ICD10CM, CVX, and LOINC codes processes each correctly."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # ICD10CM with friendly
            ("ICD10CM", "E11.9", "A_E119", "C_DIAB", "PT",
             "Type 2 diabetes mellitus", "N", True, 1),
            # CVX
            ("CVX", "207", "A_CVX_207", "C_CVX_207", "PT",
             "COVID-19 vaccine, mRNA", "N", True, 1),
            # LOINC
            ("LNC", "2345-7", "A_LNC_2345", "C_LNC_2345", "LN",
             "Glucose lab test", "N", True, 1),
        ],
    )
    # Friendly for ICD10CM (same CUI)
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_DIAB", "MEDLINEPLUS", "MP_DIAB", "A_MP_DIAB", "MH",
             "Diabetes", "MEDLINEPLUS", False, False),
        ],
    )

    codes = [
        CodeRef(source="CVX", code="207"),
        CodeRef(source="ICD10CM", code="E11.9"),
        CodeRef(source="LNC", code="2345-7"),
    ]
    results = get_non_rxnorm_patient_friendly(codes, con)

    assert len(results) == 3
    # CVX -> original
    assert results[0].code.source == "CVX"
    assert results[0].match_type == "original"
    # ICD10CM -> found friendly
    assert results[1].code.source == "ICD10CM"
    assert results[1].name == "Diabetes"
    assert results[1].friendly_source == "MEDLINEPLUS"
    # LOINC -> original (no friendly atoms)
    assert results[2].code.source == "LNC"
    assert results[2].match_type == "original"


# ---------------------------------------------------------------------------
# Test 10: SNOMED fallback from ICD10CM
# ---------------------------------------------------------------------------


def test_snomed_fallback_from_icd10cm(con: duckdb.DuckDBPyConnection) -> None:
    """ICD10CM code with no native friendly candidate falls back via SNOMED."""
    # ICD10CM code with no friendly in native hierarchy
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A00.0", "A_A00", "C_CHOLERA", "PT",
             "Cholera due to Vibrio cholerae", "N", True, 1),
            ("SNOMEDCT_US", "63650001", "A_SN_CHOLERA", "C_CHOLERA", "PT",
             "Cholera", "N", True, 1),
            ("SNOMEDCT_US", "40930007", "A_SN_INFECT", "C_INFECT", "PT",
             "Intestinal infectious disease", "N", True, 1),
        ],
    )
    # Crosswalk: ICD10CM A00.0 -> SNOMED 63650001 (same CUI)
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "A00.0", "C_CHOLERA", "SNOMEDCT_US", "63650001",
             "A_SN_CHOLERA", "C_CHOLERA", "PT"),
        ],
    )
    _promote_same_cui_to_crosswalk_edges(con)
    # SNOMED walk: 63650001 -> 40930007
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "63650001", "A_SN_CHOLERA", "C_CHOLERA", "PT",
             "40930007", "A_SN_INFECT", "C_INFECT", "PT",
             "isa", "parent", "test"),
        ],
    )
    # SNOMED ancestor is deep enough (min_top_depth > 3)
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [
            ("63650001", 6),
            ("40930007", 5),
        ],
    )
    # Friendly atom on the SNOMED ancestor CUI
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_INFECT", "MEDLINEPLUS", "MP_INFECT", "A_MP_INFECT", "MH",
             "Intestinal Infections", "MEDLINEPLUS", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="A00.0")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "Intestinal Infections"
    assert r.friendly_source == "MEDLINEPLUS"
    assert r.match_type == "snomed_fallback"
    assert r.technical_name == "Cholera due to Vibrio cholerae"


def test_snomed_fallback_from_source_ancestor(con: duckdb.DuckDBPyConnection) -> None:
    """ICD10CM fallback can crosswalk via a parent source code, not just direct code."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "K00.0", "A_K00_0", "C_K00_ORIGINAL", "PT",
             "K00.0 child concept", "N", True, 1),
            ("ICD10CM", "K00", "A_K00", "C_K00_PARENT", "HT",
             "K00 parent concept", "N", True, 1),
            ("SNOMEDCT_US", "63650001", "A_SN_CHOLERA", "C_CHOLERA", "PT",
             "Cholera", "N", True, 1),
            ("SNOMEDCT_US", "40930007", "A_SN_INFECT", "C_INFECT", "PT",
             "Intestinal infectious disease", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "K00.0", "A_K00_0", "C_K00_ORIGINAL", "PT",
             "K00", "A_K00", "C_K00_PARENT", "HT",
             "isa", "parent", "test"),
            ("SNOMEDCT_US", "63650001", "A_SN_CHOLERA", "C_CHOLERA", "PT",
             "40930007", "A_SN_INFECT", "C_INFECT", "PT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "K00", "C_K00_PARENT", "SNOMEDCT_US", "63650001",
             "A_SN_CHOLERA", "C_CHOLERA", "PT"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [("63650001", 6), ("40930007", 5)],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_INFECT", "MEDLINEPLUS", "MP_INFECT", "A_MP_INFECT", "MH",
             "Intestinal Infections", "MEDLINEPLUS", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="K00.0")], con
    )
    assert len(results) == 1
    r = results[0]
    assert r.name == "Intestinal Infections"
    assert r.friendly_source == "MEDLINEPLUS"
    assert r.match_type == "snomed_fallback"
    # Source parent depth (1) + SNOMED depth (1) to the friendly node
    assert r.match_depth == 2


def test_snomed_fallback_skips_unrelated_combo_chv_candidate(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Combination source names should not accept unrelated CHV fallback labels."""
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "F14.959", "A_F14959", "C_SOURCE_COMBO", "PT",
             "Cocaine use with cocaine-induced psychotic disorder", "N", True, 1),
            ("SNOMEDCT_US", "100001", "A_SN_BAD", "C_SN_BAD", "PT",
             "Unrelated SNOMED combo seed", "N", True, 1),
            ("SNOMEDCT_US", "100002", "A_SN_GOOD", "C_SN_GOOD", "PT",
             "Substance-related disorder", "N", True, 1),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.same_cui_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "F14.959", "C_SOURCE_COMBO", "SNOMEDCT_US", "100001",
             "A_SN_BAD", "C_SN_BAD", "PT"),
        ],
    )
    _promote_same_cui_to_crosswalk_edges(con)
    con.executemany(
        "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SNOMEDCT_US", "100001", "A_SN_BAD", "C_SN_BAD", "PT",
             "100002", "A_SN_GOOD", "C_SN_GOOD", "PT",
             "isa", "parent", "test"),
        ],
    )
    con.executemany(
        "INSERT INTO mt4ds.snomed_top_level_depth VALUES (?, ?)",
        [("100001", 6), ("100002", 5)],
    )
    con.executemany(
        "INSERT INTO mt4ds.friendly_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("C_SN_BAD", "CHV", "CHV_BAD", "A_CHV_BAD", "PT",
             "brain findings", "CHV", False, False),
            ("C_SN_GOOD", "CHV", "CHV_GOOD", "A_CHV_GOOD", "PT",
             "cocaine related disorders", "CHV", False, False),
        ],
    )

    results = get_non_rxnorm_patient_friendly(
        [CodeRef(source="ICD10CM", code="F14.959")],
        con,
    )

    assert len(results) == 1
    assert results[0].name == "cocaine related disorders"
    assert results[0].match_type == "snomed_fallback"
    assert results[0].match_depth == 1
