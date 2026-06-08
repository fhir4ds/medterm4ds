"""Core domain models for medterm4ds."""

from .config import (
    LOCAL_DUCKDB_MEMORY_PROFILES,
    LOCAL_LITE_MEMORY_PROFILES,
    LocalDuckDBConfig,
    LocalLiteConfig,
    local_duckdb_config,
    local_lite_config,
)
from .models import (
    CodeInfo,
    CodeMapping,
    CodeRef,
    CodeRelation,
    ConceptMapRow,
    FriendlyNameResult,
    NameSearchResult,
    Provenance,
    ProvenanceStep,
    SourceStats,
)
from .normalize import normalize_source
from .schemas import (
    OUTPUT_SCHEMA_VERSION,
    OutputField,
    OutputSchema,
    get_output_schema,
    list_output_schemas,
)

__all__ = [
    "CodeRef",
    "CodeInfo",
    "CodeMapping",
    "CodeRelation",
    "ConceptMapRow",
    "FriendlyNameResult",
    "NameSearchResult",
    "LOCAL_DUCKDB_MEMORY_PROFILES",
    "LOCAL_LITE_MEMORY_PROFILES",
    "LocalDuckDBConfig",
    "LocalLiteConfig",
    "OUTPUT_SCHEMA_VERSION",
    "OutputField",
    "OutputSchema",
    "Provenance",
    "ProvenanceStep",
    "SourceStats",
    "get_output_schema",
    "list_output_schemas",
    "local_duckdb_config",
    "local_lite_config",
    "normalize_source",
]
