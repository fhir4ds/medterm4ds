"""Versioned public output schema contracts."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

OUTPUT_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class OutputField:
    """One stable output field."""

    name: str
    type: str
    nullable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
        }


@dataclass(frozen=True)
class OutputSchema:
    """Versioned schema for one public result model."""

    name: str
    version: str
    fields: tuple[OutputField, ...]

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "fields": [field.to_dict() for field in self.fields],
        }


def _schema(name: str, fields: tuple[tuple[str, str, bool], ...]) -> OutputSchema:
    return OutputSchema(
        name=name,
        version=OUTPUT_SCHEMA_VERSION,
        fields=tuple(OutputField(*field) for field in fields),
    )


OUTPUT_SCHEMAS = MappingProxyType(
    {
        "CodeInfo": _schema(
            "CodeInfo",
            (
                ("source", "string", False),
                ("code", "string", False),
                ("name", "string", True),
                ("cui", "string", True),
                ("aui", "string", True),
                ("tty", "string", True),
                ("suppress", "string", True),
            ),
        ),
        "SourceStats": _schema(
            "SourceStats",
            (
                ("source", "string", False),
                ("code_count", "integer", False),
                ("atom_count", "integer", False),
            ),
        ),
        "CodeResolution": _schema(
            "CodeResolution",
            (
                ("source", "string", False),
                ("code", "string", False),
                ("resolved_source", "string", True),
                ("resolved_code", "string", True),
                ("status", "string", False),
                ("match_type", "string", False),
                ("input_display", "string", True),
                ("resolved_display", "string", True),
                ("input_cui", "string", True),
                ("resolved_cui", "string", True),
                ("input_aui", "string", True),
                ("resolved_aui", "string", True),
                ("input_suppress", "string", True),
                ("resolved_suppress", "string", True),
                ("replacement_relationship", "string", True),
                ("normalized_code", "string", True),
                ("candidates", "array", False),
                ("matched_via", "object", True),
            ),
        ),
        "NameSearchResult": _schema(
            "NameSearchResult",
            (
                ("source", "string", False),
                ("code", "string", False),
                ("name", "string", False),
                ("cui", "string", True),
                ("aui", "string", True),
                ("tty", "string", True),
                ("match_type", "string", False),
            ),
        ),
        "CodeMapping": _schema(
            "CodeMapping",
            (
                ("source", "string", False),
                ("code", "string", False),
                ("source_display", "string", True),
                ("target_source", "string", False),
                ("target_code", "string", False),
                ("target_display", "string", True),
                ("relationship", "string", False),
                ("match_type", "string", False),
                ("match_depth", "integer", False),
                ("source_cui", "string", True),
                ("target_cui", "string", True),
                ("source_aui", "string", True),
                ("target_aui", "string", True),
                ("target_tty", "string", True),
                ("matched_via", "object", True),
            ),
        ),
        "CodeRelation": _schema(
            "CodeRelation",
            (
                ("source", "string", False),
                ("code", "string", False),
                ("source_display", "string", True),
                ("target_source", "string", False),
                ("target_code", "string", False),
                ("target_display", "string", True),
                ("relationship", "string", False),
                ("depth", "integer", False),
                ("rel", "string", True),
                ("rela", "string", True),
                ("source_cui", "string", True),
                ("target_cui", "string", True),
                ("source_aui", "string", True),
                ("target_aui", "string", True),
            ),
        ),
        "FriendlyNameResult": _schema(
            "FriendlyNameResult",
            (
                ("code", "string", False),
                ("source", "string", False),
                ("name", "string", False),
                ("friendly_source", "string", False),
                ("match_type", "string", False),
                ("match_depth", "integer", False),
                ("technical_name", "string", True),
                ("matched_via", "object", True),
            ),
        ),
        "ConceptMapRow": _schema(
            "ConceptMapRow",
            (
                ("source", "string", False),
                ("code", "string", False),
                ("source_display", "string", True),
                ("target_source", "string", False),
                ("target_code", "string", False),
                ("target_display", "string", False),
                ("relationship", "string", False),
                ("friendly_source", "string", True),
                ("match_type", "string", True),
                ("match_depth", "integer", False),
                ("matched_via", "object", True),
            ),
        ),
        "OptimizeResult": _schema(
            "OptimizeResult",
            (
                ("source", "string", False),
                ("relationship", "string", False),
                ("strategy", "string", False),
                ("original_count", "integer", False),
                ("optimized_count", "integer", False),
                ("reduction", "number", False),
                ("rules", "array", False),
            ),
        ),
    }
)


def list_output_schemas() -> tuple[str, ...]:
    """Return public output schema names."""
    return tuple(OUTPUT_SCHEMAS)


def get_output_schema(schema: str | type | object) -> OutputSchema:
    """Return a public output schema by model name, class, or instance."""
    name = _schema_name(schema)
    try:
        return OUTPUT_SCHEMAS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown output schema: {name}") from exc


def _schema_name(schema: str | type | object) -> str:
    if isinstance(schema, str):
        raw_name = schema
    elif isinstance(schema, type):
        raw_name = schema.__name__
    else:
        raw_name = schema.__class__.__name__
    normalized = raw_name.replace("_", "").replace("-", "").lower()
    for name in OUTPUT_SCHEMAS:
        if normalized == name.lower():
            return name
    return raw_name
