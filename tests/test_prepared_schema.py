"""Tests for prepared schema management (Phase 1)."""
from __future__ import annotations

import duckdb

from medterm4ds.engines.duckdb.prepared import (
    PATIENT_FRIENDLY_POLICY_VERSION,
    PREPARED_SCHEMA_VERSION,
    prepare_mt4ds_schema,
    verify_mt4ds_schema,
)


def _create_raw_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create synthetic main.* UMLS tables for testing."""
    con.execute(
        """CREATE TABLE main.mrconso (
            CUI VARCHAR, AUI VARCHAR, SAB VARCHAR, TTY VARCHAR,
            CODE VARCHAR, STR VARCHAR, SUPPRESS VARCHAR
        )"""
    )
    con.execute(
        """CREATE TABLE main.mrrel (
            AUI1 VARCHAR, AUI2 VARCHAR, REL VARCHAR, RELA VARCHAR
        )"""
    )
    con.execute(
        """CREATE TABLE main.mrsat (
            CODE VARCHAR, SAB VARCHAR, ATN VARCHAR, ATV VARCHAR
        )"""
    )
    con.execute(
        "INSERT INTO main.mrconso VALUES ('C001', 'A001', 'ICD10CM', 'PT', 'E11.9', 'Type 2 diabetes', 'N')"
    )
    con.execute(
        "INSERT INTO main.mrrel VALUES ('A001', 'A002', 'PAR', 'isa')"
    )
    con.execute(
        "INSERT INTO main.mrsat VALUES ('12345', 'RXNORM', 'NDC', '00002082101')"
    )


def _create_umls_schema_raw_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create synthetic umls.* raw UMLS tables for replacement-safety tests."""
    con.execute("CREATE SCHEMA umls")
    con.execute(
        """CREATE TABLE umls.mrconso (
            CUI VARCHAR, AUI VARCHAR, SAB VARCHAR, TTY VARCHAR,
            CODE VARCHAR, STR VARCHAR, SUPPRESS VARCHAR
        )"""
    )
    con.execute(
        """CREATE TABLE umls.mrrel (
            AUI1 VARCHAR, AUI2 VARCHAR, REL VARCHAR, RELA VARCHAR
        )"""
    )
    con.execute(
        """CREATE TABLE umls.mrsat (
            CODE VARCHAR, SAB VARCHAR, ATN VARCHAR, ATV VARCHAR
        )"""
    )
    con.execute(
        "INSERT INTO umls.mrconso VALUES ('C001', 'A001', 'ICD10CM', 'PT', 'E11.9', 'Type 2 diabetes', 'N')"
    )
    con.execute("INSERT INTO umls.mrrel VALUES ('A001', 'A002', 'PAR', 'isa')")
    con.execute("INSERT INTO umls.mrsat VALUES ('META', 'MTH', 'RELEASE', '2026AA')")


def _con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPrepareMt4dsSchema:
    """prepare_mt4ds_schema creates schemas, views, and manifest."""

    def test_creates_schemas_and_manifest(self):
        con = _con()
        _create_raw_tables(con)

        report = prepare_mt4ds_schema(con)

        assert "umls" in report["schemas_created"]
        assert "mt4ds" in report["schemas_created"]
        assert set(report["views_created"]) == {"mrconso", "mrrel", "mrsat"}
        assert report["manifest_ready"] is True
        assert report["prepared_schema_version"] == PREPARED_SCHEMA_VERSION

        # Source counts recorded
        counts = report["source_counts"]
        assert counts["mrconso"] == 1
        assert counts["mrrel"] == 1
        assert counts["mrsat"] == 1

        # Manifest rows exist
        rows = con.execute(
            "SELECT key FROM mt4ds.prepare_manifest ORDER BY key"
        ).fetchall()
        keys = [r[0] for r in rows]
        assert "prepared_schema_version" in keys
        assert "package_version" in keys
        assert "source_count.mrconso" in keys

        con.close()

    def test_idempotent_no_replace(self):
        con = _con()
        _create_raw_tables(con)

        prepare_mt4ds_schema(con, replace=False)
        # Insert extra metadata to verify it survives
        con.execute(
            "INSERT INTO mt4ds.prepare_manifest (key, value) VALUES ('test_marker', 'survives')"
        )

        report2 = prepare_mt4ds_schema(con, replace=False)

        # Second call should not report schemas created (they already exist)
        assert report2["schemas_created"] == []

        # Marker row should still be present
        (val,) = con.execute(
            "SELECT value FROM mt4ds.prepare_manifest WHERE key = 'test_marker'"
        ).fetchone()
        assert val == "survives"

        con.close()

    def test_replace_true_drops_and_recreates(self):
        con = _con()
        _create_raw_tables(con)

        prepare_mt4ds_schema(con, replace=False)
        # Insert a marker row
        con.execute(
            "INSERT INTO mt4ds.prepare_manifest (key, value) VALUES ('test_marker', 'gone')"

        )

        report = prepare_mt4ds_schema(con, replace=True)

        # Marker should be gone
        rows = con.execute(
            "SELECT value FROM mt4ds.prepare_manifest WHERE key = 'test_marker'"
        ).fetchall()
        assert len(rows) == 0

        # Schemas recreated
        assert report["manifest_ready"] is True

        con.close()

    def test_replace_true_preserves_existing_provenance_metadata(self):
        con = _con()
        _create_raw_tables(con)

        prepare_mt4ds_schema(
            con,
            db_role="current_candidate",
            umls_release="2026AA",
            source_archive="/data/umls/umls-2026AA.zip",
        )
        prepare_mt4ds_schema(con, replace=True)

        result = verify_mt4ds_schema(con)

        assert result["db_role"] == "current_candidate"
        assert result["umls_release"] == "2026AA"
        assert result["source_archive"] == "/data/umls/umls-2026AA.zip"

        con.close()

    def test_replace_true_discovers_release_before_preserved_release(self):
        con = _con()
        _create_raw_tables(con)

        prepare_mt4ds_schema(con, umls_release="2025AB")
        con.execute(
            "INSERT INTO main.mrsat VALUES ('META', 'MTH', 'RELEASE', '2026AA')"
        )
        prepare_mt4ds_schema(con, replace=True)

        result = verify_mt4ds_schema(con)

        assert result["umls_release"] == "2026AA"

        con.close()

    def test_replace_true_preserves_raw_umls_schema_tables(self):
        con = _con()
        _create_umls_schema_raw_tables(con)

        prepare_mt4ds_schema(con, replace=True)

        count = con.execute("SELECT COUNT(*) FROM umls.mrconso").fetchone()[0]
        result = verify_mt4ds_schema(con)

        assert count == 1
        assert result["source_tables"]["mrconso"]["location"] == "umls"
        assert result["umls_release"] == "2026AA"

        con.close()


class TestVerifyMt4dsSchema:
    """verify_mt4ds_schema returns accurate metadata."""

    def test_verify_after_prepare(self):
        con = _con()
        _create_raw_tables(con)
        prepare_mt4ds_schema(con)

        result = verify_mt4ds_schema(con)

        assert result["umls_schema_exists"] is True
        assert result["mt4ds_schema_exists"] is True
        assert result["manifest_exists"] is True
        assert result["prepared_schema_version"] == PREPARED_SCHEMA_VERSION
        assert result["patient_friendly_policy_version"] == PATIENT_FRIENDLY_POLICY_VERSION
        assert result["package_version"] is not None
        assert result["errors"] == []

        # Source tables have correct metadata.
        # After prepare, umls views exist so verify reports location as "umls".
        st = result["source_tables"]
        assert st["mrconso"]["location"] == "umls"
        assert st["mrconso"]["row_count"] == 1
        assert st["mrrel"]["row_count"] == 1
        assert st["mrsat"]["row_count"] == 1

        prepared = result["prepared_tables"]
        assert prepared["atoms"]["exists"] is True
        assert prepared["crosswalk_edges"]["exists"] is True
        assert prepared["atoms"]["row_count"] == 1

        con.close()

    def test_verify_reports_missing_prepared_table(self):
        con = _con()
        _create_raw_tables(con)
        prepare_mt4ds_schema(con)
        con.execute("DROP TABLE mt4ds.crosswalk_edges")

        result = verify_mt4ds_schema(con)

        assert result["prepared_tables"]["crosswalk_edges"]["exists"] is False
        assert result["prepared_tables"]["crosswalk_edges"]["row_count"] is None
        assert any(
            "missing prepared tables: crosswalk_edges" in str(error)
            for error in result["errors"]
        )

        con.close()

    def test_verify_reports_manifest_provenance_metadata(self):
        con = _con()
        _create_raw_tables(con)
        report = prepare_mt4ds_schema(
            con,
            db_role="current_candidate",
            umls_release="2026AA",
            source_archive="/data/umls/umls-2026AA.zip",
        )

        result = verify_mt4ds_schema(con)

        assert report["db_role"] == "current_candidate"
        assert report["source_archive"] == "/data/umls/umls-2026AA.zip"
        assert report["umls_release"] == "2026AA"
        assert result["db_role"] == "current_candidate"
        assert result["source_archive"] == "/data/umls/umls-2026AA.zip"
        assert result["umls_release"] == "2026AA"

        con.close()

    def test_verify_reports_prepared_schema_version_mismatch(self):
        con = _con()
        _create_raw_tables(con)
        prepare_mt4ds_schema(con)
        con.execute(
            """
            UPDATE mt4ds.prepare_manifest
            SET value = 'stale'
            WHERE key = 'prepared_schema_version'
            """
        )

        result = verify_mt4ds_schema(con)

        assert any(
            "prepared schema version mismatch" in str(error)
            for error in result["errors"]
        )

        con.close()

    def test_verify_reports_patient_friendly_policy_version_mismatch(self):
        con = _con()
        _create_raw_tables(con)
        prepare_mt4ds_schema(con)
        con.execute(
            """
            UPDATE mt4ds.prepare_manifest
            SET value = 'stale'
            WHERE key = 'patient_friendly_policy_version'
            """
        )

        result = verify_mt4ds_schema(con)

        assert any(
            "patient-friendly policy version mismatch" in str(error)
            for error in result["errors"]
        )

        con.close()

    def test_verify_before_prepare(self):
        con = _con()
        _create_raw_tables(con)

        result = verify_mt4ds_schema(con)

        assert result["umls_schema_exists"] is False
        assert result["mt4ds_schema_exists"] is False
        assert result["manifest_exists"] is False
        assert result["prepared_schema_version"] is None

        con.close()


class TestPreparedTableData:
    """Prepared tables contain correct data from raw main.* tables."""

    def test_atoms_table_matches_raw_mrconso(self):
        con = _con()
        _create_raw_tables(con)
        prepare_mt4ds_schema(con)

        (main_count,) = con.execute("SELECT COUNT(*) FROM main.mrconso WHERE CODE IS NOT NULL AND CODE != '' AND AUI IS NOT NULL AND AUI != ''").fetchone()
        (atoms_count,) = con.execute("SELECT COUNT(*) FROM mt4ds.atoms").fetchone()
        assert main_count == atoms_count

        con.close()

    def test_best_atoms_has_ranked_rows(self):
        con = _con()
        _create_raw_tables(con)
        prepare_mt4ds_schema(con)

        rows = con.execute("SELECT COUNT(*) FROM mt4ds.best_atoms WHERE rank = 1").fetchone()
        assert rows[0] > 0

        con.close()

    def test_best_atoms_uses_rxnorm_topology_tty_rank(self):
        con = _con()
        _create_raw_tables(con)
        con.executemany(
            "INSERT INTO main.mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("C_RX", "RX_PSN", "RXNORM", "PSN", "1149364",
                 "OSCIMIN 0.375 MG 12HR Extended Release Oral Tablet", "N"),
                ("C_RX", "RX_SBD", "RXNORM", "SBD", "1149364",
                 "12 HR hyoscyamine sulfate 0.375 MG Extended Release Oral Tablet [Oscimin]", "N"),
            ],
        )

        prepare_mt4ds_schema(con)

        row = con.execute(
            """
            SELECT tty, name
            FROM mt4ds.best_atoms
            WHERE source = 'RXNORM' AND code = '1149364' AND rank = 1
            """
        ).fetchone()
        assert row == (
            "SBD",
            "12 HR hyoscyamine sulfate 0.375 MG Extended Release Oral Tablet [Oscimin]",
        )

        con.close()

    def test_rxnorm_tty_edges_include_reverse_allowed_orientation(self):
        con = _con()
        _create_raw_tables(con)
        con.executemany(
            "INSERT INTO main.mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("C_ALC_IN", "RX_ALC_IN", "RXNORM", "IN", "1000082", "alcaftadine", "N"),
                ("C_ALC_DOSE", "RX_ALC_SCDC", "RXNORM", "SCDC", "1000083", "alcaftadine 2.5 MG/ML", "N"),
            ],
        )
        con.execute(
            "INSERT INTO main.mrrel VALUES ('RX_ALC_IN', 'RX_ALC_SCDC', 'RO', 'has_ingredient')"
        )

        prepare_mt4ds_schema(con)

        row = con.execute(
            """
            SELECT source_code, source_tty, target_code, target_tty
            FROM mt4ds.rxnorm_tty_edges
            WHERE source_code = '1000083' AND target_code = '1000082'
            """
        ).fetchone()
        assert row == ("1000083", "SCDC", "1000082", "IN")

        con.close()

    def test_hierarchy_edges_do_not_include_icd_prefix_or_range_edges(self):
        con = _con()
        _create_raw_tables(con)
        con.executemany(
            "INSERT INTO main.mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("C_L301", "A_L301", "ICD10CM", "PT", "L30.1", "Dyshidrosis [pompholyx]", "N"),
                ("C_L30", "A_L30", "ICD10CM", "HT", "L30", "Other and unspecified dermatitis", "N"),
                ("C_L00L99", "A_L00L99", "ICD10CM", "HT", "L00-L99", "Diseases of the skin", "N"),
                ("C_L20L30", "A_L20L30", "ICD10CM", "HT", "L20-L30", "Dermatitis and eczema", "N"),
                ("C_S3706", "A_S3706", "ICD10CM", "HT", "S37.06", "Major laceration of kidney", "N"),
                ("C_S370", "A_S370", "ICD10CM", "HT", "S37.0", "Injury of kidney", "N"),
            ],
        )

        prepare_mt4ds_schema(con)

        edges = set(
            con.execute(
                """
                SELECT from_code, to_code, edge_source
                FROM mt4ds.hierarchy_edges
                WHERE source = 'ICD10CM'
                  AND edge_source IN ('prefix_code', 'prefix_range')
                """
            ).fetchall()
        )
        assert edges == set()

        con.close()

    def test_code_replacements_materialized_from_mrrel(self):
        con = _con()
        _create_raw_tables(con)
        con.executemany(
            "INSERT INTO main.mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("C_OLD", "A_OLD", "ICD10CM", "PT", "E11.8X", "Old diabetes code", "O"),
                ("C_NEW", "A_NEW", "ICD10CM", "PT", "E11.9", "Type 2 diabetes", "N"),
            ],
        )
        con.execute(
            "INSERT INTO main.mrrel VALUES ('A_OLD', 'A_NEW', 'RO', 'replaced_by')"
        )

        prepare_mt4ds_schema(con)

        rows = con.execute(
            """
            SELECT source, old_code, new_code, rela
            FROM mt4ds.code_replacements
            WHERE source = 'ICD10CM'
            ORDER BY old_code, new_code
            """
        ).fetchall()

        assert ("ICD10CM", "E11.8X", "E11.9", "replaced_by") in rows
        assert ("ICD10CM", "E11.9", "E11.8X", "replaced_by") not in rows

        con.close()

    def test_cvx_metadata_copied_from_main_table(self):
        con = _con()
        _create_raw_tables(con)
        con.execute(
            """
            CREATE TABLE main.cvx_metadata (
                code VARCHAR,
                group_name VARCHAR,
                short_name VARCHAR
            )
            """
        )
        con.execute(
            "INSERT INTO main.cvx_metadata VALUES ('208', 'COVID-19', 'COVID vaccine')"
        )

        prepare_mt4ds_schema(con)

        row = con.execute(
            """
            SELECT code, group_name, short_name
            FROM mt4ds.cvx_metadata
            WHERE code = '208'
            """
        ).fetchone()

        assert row == ("208", "COVID-19", "COVID vaccine")

        con.close()

    def test_manifest_records_source_counts(self):
        con = _con()
        _create_raw_tables(con)
        prepare_mt4ds_schema(con)

        (count,) = con.execute("SELECT value FROM mt4ds.prepare_manifest WHERE key = 'source_count.mrconso'").fetchone()
        assert int(count) == 1

        con.close()

    
class TestMissingRawTables:
    """Graceful handling when raw tables are absent."""

    def test_prepare_with_missing_raw_tables(self):
        con = _con()
        # Do NOT create raw tables

        report = prepare_mt4ds_schema(con)

        # Schemas should still be created
        assert "umls" in report["schemas_created"]
        assert "mt4ds" in report["schemas_created"]
        assert report["manifest_ready"] is True

        # Source counts should all be None
        for table in ("mrconso", "mrrel", "mrsat"):
            assert report["source_counts"][table] is None

        con.close()

    def test_verify_with_missing_raw_tables(self):
        con = _con()
        # Do NOT create raw tables

        result = verify_mt4ds_schema(con)

        # Should report missing tables in errors
        assert len(result["errors"]) > 0
        error_msg = ", ".join(str(e) for e in result["errors"])
        assert "missing raw tables" in error_msg

        # Source tables should show no location
        for table in ("mrconso", "mrrel", "mrsat"):
            assert result["source_tables"][table]["location"] is None

        con.close()
