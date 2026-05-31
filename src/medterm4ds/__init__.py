"""Batch-first medical terminology utilities."""

from .core.config import LOCAL_LITE_MEMORY_PROFILES, LocalLiteConfig, local_lite_config
from .core.models import (
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
from .core.schemas import (
    OUTPUT_SCHEMA_VERSION,
    OutputField,
    OutputSchema,
    get_output_schema,
    list_output_schemas,
)
from .ds import (
    code_ttys_dataframe,
    conceptmap_dataframe,
    hierarchy_dataframe,
    lookup_dataframe,
    map_dataframe,
    mapping_conceptmap_dataframe,
    patient_friendly_dataframe,
    sample_codes_dataframe,
    search_names_dataframe,
    source_stats_dataframe,
)
from .engines.api import RemoteApiEngine
from .services.conceptmap import (
    get_concept_map,
    get_mapping_concept_map,
    iter_concept_map,
    iter_mapping_concept_map,
)
from .services.discovery import get_code_ttys, get_source_stats, sample_source_codes, search_names
from .services.hierarchy import (
    get_ancestors,
    get_children,
    get_code_relations,
    get_descendants,
    get_parents,
)
from .services.lookup import get_code_info, get_code_infos
from .services.mapping import get_code_mappings
from .services.patient_friendly import get_patient_friendly_names

__all__ = [
    "CodeRef",
    "CodeInfo",
    "CodeMapping",
    "CodeRelation",
    "ConceptMapRow",
    "FriendlyNameResult",
    "NameSearchResult",
    "LOCAL_LITE_MEMORY_PROFILES",
    "LocalLiteConfig",
    "OUTPUT_SCHEMA_VERSION",
    "OutputField",
    "OutputSchema",
    "Provenance",
    "ProvenanceStep",
    "RemoteApiEngine",
    "SourceStats",
    "code_ttys_dataframe",
    "conceptmap_dataframe",
    "get_ancestors",
    "get_children",
    "get_code_relations",
    "get_concept_map",
    "get_code_ttys",
    "get_mapping_concept_map",
    "get_source_stats",
    "get_descendants",
    "get_code_info",
    "get_code_infos",
    "get_code_mappings",
    "get_output_schema",
    "get_parents",
    "get_patient_friendly_names",
    "hierarchy_dataframe",
    "sample_source_codes",
    "lookup_dataframe",
    "map_dataframe",
    "mapping_conceptmap_dataframe",
    "patient_friendly_dataframe",
    "sample_codes_dataframe",
    "search_names_dataframe",
    "search_names",
    "source_stats_dataframe",
    "iter_concept_map",
    "iter_mapping_concept_map",
    "list_output_schemas",
    "local_lite_config",
]
