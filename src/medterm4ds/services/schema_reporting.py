"""Helpers for turning schema verification output into report metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_UNKNOWN_DB_ROLE_VALUES = {"", "unknown", "none", "null"}


def _known_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _UNKNOWN_DB_ROLE_VALUES:
        return None
    return text


def missing_prepared_tables(schema_report: Mapping[str, Any]) -> list[str]:
    """Return sorted required prepared tables missing from a verification report."""
    prepared_tables = schema_report.get("prepared_tables")
    if not isinstance(prepared_tables, Mapping):
        return []
    missing: list[str] = []
    for table_name, table_info in prepared_tables.items():
        if isinstance(table_info, Mapping) and not table_info.get("exists"):
            missing.append(str(table_name))
    return sorted(missing)


def report_db_role_metadata(
    explicit_db_role: str | None,
    schema_report: Mapping[str, Any],
) -> dict[str, str]:
    """Resolve report DB role from CLI metadata, then manifest provenance.

    Explicit report metadata wins. If the caller passes an ambiguous value such
    as ``unknown``, a manifest ``db_role`` from schema verification is used
    instead. Reports can then include ``db_role_source`` to make ambiguity
    reviewable.
    """
    explicit = _known_value(explicit_db_role)
    if explicit:
        return {"db_role": explicit, "db_role_source": "argument"}

    manifest = _known_value(
        schema_report.get("db_role") or schema_report.get("manifest_db_role")
    )
    if manifest:
        return {"db_role": manifest, "db_role_source": "manifest"}

    return {"db_role": "unknown", "db_role_source": "unknown"}


def schema_report_metadata(schema_report: Mapping[str, Any]) -> dict[str, Any]:
    """Extract stable report fields from ``verify_mt4ds_schema`` output."""
    return {
        "umls_release": schema_report.get("umls_release"),
        "prepared_schema_version": schema_report.get("prepared_schema_version"),
        "patient_friendly_policy_version": schema_report.get(
            "patient_friendly_policy_version"
        ),
        "manifest_db_role": schema_report.get("db_role"),
        "source_archive": schema_report.get("source_archive"),
        "prepared_tables": schema_report.get("prepared_tables") or {},
        "missing_prepared_tables": missing_prepared_tables(schema_report),
        "schema_errors": list(schema_report.get("errors") or []),
    }


def empty_schema_report_metadata() -> dict[str, Any]:
    """Return report metadata used when schema verification cannot run."""
    return schema_report_metadata({})
