"""Medical Terminology for Data Science."""

__version__ = "0.0.1"

# FHIR facade (optional — requires medterm4ds[fhir])
try:
    from .apps.fhir_api import create_fhir_app
except ImportError:
    pass

from .client import Terminology, connect, connect_remote

# Intelligent text-to-code search (optional — requires BM25/SapBERT indexes)
try:
    from .services.search import search as _search_func

    def search(query, *, mode="lexical", sources=None, count=20):
        """Text-to-code search (lexical/semantic/hybrid).

        Requires BM25 indexes at MEDTERM4DS_SEARCH_INDEX_DIR and/or SapBERT
        model at MEDTERM4DS_EMBEDDING_MODEL_DIR.
        """
        return _search_func(query, mode=mode, sources=sources, count=count)
except ImportError:
    pass
from .core.config import (
    LOCAL_DUCKDB_MEMORY_PROFILES,
    LOCAL_LITE_MEMORY_PROFILES,
    LocalDuckDBConfig,
    LocalLiteConfig,
    local_duckdb_config,
    local_lite_config,
)
from .core.models import (
    CodeInfo,
    CodeMapping,
    CodeRef,
    CodeRelation,
    CodeResolution,
    ConceptMapRow,
    FriendlyNameResult,
    NameSearchResult,
    OptimizeResult,
    OptimizeRule,
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
from .domains import (
    cross_reference,
    diagnosis_codes,
    discover,
    drugs_by_class,
    drugs_for_indication,
    fda_label_by_rxcui,
    guideline_fulltext,
    guideline_recommendations,
    guideline_search,
    guidelines_for_code,
    hcpcs_drugs,
    indication_search,
    lab_codes,
    lab_value_codes,
    procedure_codes,
    search_drug,
    vaccine_codes,
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
from .engines.duckdb import LocalDuckDBEngine, LocalLiteEngine
from .services.bulk import (
    iter_batches,
    iter_hierarchy_bulk,
    iter_lookup_bulk,
    iter_mapping_bulk,
    iter_patient_friendly_bulk,
)
from .services.conceptmap import (
    get_concept_map,
    get_mapping_concept_map,
    iter_concept_map,
    iter_mapping_concept_map,
)
from .services.data_setup import (
    DEFAULT_UMLS_RELEASE_TYPE,
    DEFAULT_UMLS_VERIFY_SOURCES,
    annotate_umls_duckdb,
    build_umls_duckdb,
    download_umls_release,
    prepare_umls_duckdb,
    verify_umls_duckdb,
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
from .services.optimize import optimize_codes
from .services.patient_friendly import get_patient_friendly_names
from .services.resolution import resolve_codes

__all__ = [
    "__version__",
    "CodeRef",
    "CodeInfo",
    "CodeMapping",
    "CodeRelation",
    "CodeResolution",
    "ConceptMapRow",
    "DEFAULT_UMLS_VERIFY_SOURCES",
    "FriendlyNameResult",
    "NameSearchResult",
    "OptimizeResult",
    "OptimizeRule",
    "LOCAL_DUCKDB_MEMORY_PROFILES",
    "LOCAL_LITE_MEMORY_PROFILES",
    "LocalDuckDBConfig",
    "LocalDuckDBEngine",
    "LocalLiteConfig",
    "LocalLiteEngine",
    "OUTPUT_SCHEMA_VERSION",
    "OutputField",
    "OutputSchema",
    "Provenance",
    "ProvenanceStep",
    "RemoteApiEngine",
    "DEFAULT_UMLS_RELEASE_TYPE",
    "SourceStats",
    "Terminology",
    "annotate_umls_duckdb",
    "build_umls_duckdb",
    "code_ttys_dataframe",
    "conceptmap_dataframe",
    "connect",
    "connect_remote",
    "cross_reference",
    "diagnosis_codes",
    "discover",
    "download_umls_release",
    "drugs_by_class",
    "drugs_for_indication",
    "fda_label_by_rxcui",
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
    "optimize_codes",
    "get_output_schema",
    "get_parents",
    "get_patient_friendly_names",
    "guideline_fulltext",
    "guideline_recommendations",
    "guideline_search",
    "guidelines_for_code",
    "hcpcs_drugs",
    "hierarchy_dataframe",
    "indication_search",
    "lab_codes",
    "lab_value_codes",
    "sample_source_codes",
    "lookup_dataframe",
    "map_dataframe",
    "mapping_conceptmap_dataframe",
    "patient_friendly_dataframe",
    "procedure_codes",
    "prepare_umls_duckdb",
    "sample_codes_dataframe",
    "search_drug",
    "search_names_dataframe",
    "search_names",
    "source_stats_dataframe",
    "vaccine_codes",
    "verify_umls_duckdb",
    "iter_batches",
    "iter_concept_map",
    "iter_hierarchy_bulk",
    "iter_lookup_bulk",
    "iter_mapping_bulk",
    "iter_mapping_concept_map",
    "iter_patient_friendly_bulk",
    "list_output_schemas",
    "local_duckdb_config",
    "local_lite_config",
    "resolve_codes",
]
