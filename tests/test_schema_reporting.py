"""Tests for report metadata helpers."""
from __future__ import annotations

from medterm4ds.services.schema_reporting import report_db_role_metadata


def test_report_db_role_metadata_is_exported_from_services() -> None:
    from medterm4ds.services import report_db_role_metadata as exported

    assert exported is report_db_role_metadata


def test_report_db_role_metadata_uses_explicit_role_first() -> None:
    metadata = report_db_role_metadata(
        "benchmark_fixture",
        {"db_role": "current_candidate"},
    )

    assert metadata == {
        "db_role": "benchmark_fixture",
        "db_role_source": "argument",
    }


def test_report_db_role_metadata_falls_back_to_manifest_role() -> None:
    metadata = report_db_role_metadata(
        "unknown",
        {"db_role": "current_candidate"},
    )

    assert metadata == {
        "db_role": "current_candidate",
        "db_role_source": "manifest",
    }


def test_report_db_role_metadata_marks_missing_role_unknown() -> None:
    metadata = report_db_role_metadata(None, {})

    assert metadata == {
        "db_role": "unknown",
        "db_role_source": "unknown",
    }
