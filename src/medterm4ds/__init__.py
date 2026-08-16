"""Medical Terminology for Data Science.

# Recommended API (start here)

    import medterm4ds as mt

    # Connect to a UMLS DuckDB file (built via mt.build_umls_duckdb or the
    # medterm4ds-build package).
    terms = mt.connect("/path/to/umls.duckdb")

    # Lookup, hierarchy, mapping, patient-friendly names.
    info = terms.lookup("SNOMEDCT_US", "44054006")
    parents = terms.parents([("SNOMEDCT_US", "44054006")])
    friendly = terms.patient_friendly("SNOMEDCT_US", "44054006")

    # Text-to-code search (BM25 + SapBERT).
    results = mt.search("diabetes", mode="hybrid")

    # Text extraction (GLiNER + medspaCy + search).
    concepts = mt.extract("Patient has T2DM. No CKD.", format="codes")

For a remote HTTP backend instead of a local DuckDB:

    terms = mt.connect_remote("http://localhost:8000")

# Surfaces

Same engine, four deliverables:

- **Python library** (this module)
- **CLI**: `pip install medterm4ds[api]` then `medterm4ds --help`
- **MCP server**: `pip install medterm4ds[mcp]` then `medterm4ds-mcp`
- **FHIR R4 server**: `pip install medterm4ds[fhir]` then `python -m medterm4ds.apps.fhir_api`

# Module layout

- `medterm4ds` — recommended API (this module)
- `medterm4ds.services.*` — service-layer functions (lookup, hierarchy, mapping,
  search, extraction, etc.). Accept an `engine=` arg.
- `medterm4ds.domains.*` — domain-specific helpers (diagnosis_codes, lab_codes,
  drugs_for_indication). Wrap services with source-list presets.
- `medterm4ds.engines.*` — engine implementations (LocalDuckDBEngine,
  LocalLiteEngine, RemoteApiEngine).
- `medterm4ds.apps.*` — CLI, FastAPI, MCP, FHIR server entry points.
- `medterm4ds.ds` — dataframe helpers for pandas/polars workflows.
"""

__version__ = "0.0.2"

# ============================================================================
# Primary API — what most users need
# ============================================================================

from .client import Terminology, connect, connect_remote

# Intelligent text-to-code search (optional — requires BM25/SapBERT indexes)
# Log ImportError at WARNING so real bugs (typos, broken transitive imports)
# surface instead of silently producing AttributeError at the user's call site.
# Optional-deps users who expectedly lack BM25/SapBERT see one warning per
# missing surface, which they can silence via logging config if desired.
import logging as _logging

_logger = _logging.getLogger(__name__)

try:
    from .services.search import search as _search_func

    def search(query, *, mode="lexical", sources=None, count=20):
        """Text-to-code search (lexical/semantic/hybrid).

        Requires BM25 indexes at MEDTERM4DS_SEARCH_INDEX_DIR and/or SapBERT
        model at MEDTERM4DS_EMBEDDING_MODEL_DIR.
        """
        return _search_func(query, mode=mode, sources=sources, count=count)
except ImportError as _exc:
    _logger.warning("medterm4ds.search unavailable (install medterm4ds[search]?): %s", _exc)

# Text extraction (optional — requires medterm4ds[extraction])
try:
    from .services.extraction import extract as _extract_func
    from .services.extraction import find_terms as _find_terms_func
    from .services.extraction import resolve_spans as _resolve_spans_func

    def extract(text, *, format="codes", **kwargs):
        """Extract medical concepts from free text.

        Requires medterm4ds[extraction] (medspaCy + transformers).
        """
        return _extract_func(text, format=format, **kwargs)

    def find_terms(text, **kwargs):
        """Extract medical terms from text (NLP only, no code resolution)."""
        return _find_terms_func(text, **kwargs)

    def resolve_spans(spans, **kwargs):
        """Resolve filtered spans to coded concepts via search."""
        return _resolve_spans_func(spans, **kwargs)
except ImportError as _exc:
    _logger.warning("medterm4ds.extract unavailable (install medterm4ds[extraction]?): %s", _exc)

# FHIR server (optional — requires medterm4ds[fhir])
try:
    from .apps.fhir_api import create_fhir_app
except ImportError as _exc:
    _logger.warning("medterm4ds.create_fhir_app unavailable (install medterm4ds[fhir]?): %s", _exc)

# Cache management — inspect and clean the ~/.medterm4ds/ cache
from .core.provision import cache_clear, cache_info, cache_versions

# ============================================================================
# Types — what most call sites need
# ============================================================================

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

# ============================================================================
# Advanced — engine implementations, service functions, domain helpers
#
# These are re-exported at the top level for legacy callers and for users who
# need direct access without going through the Terminology facade. Most new
# code should import from the submodule directly (e.g.
# `from medterm4ds.services.hierarchy import get_descendants`) for clarity.
# ============================================================================

from .core.config import (
    LOCAL_DUCKDB_MEMORY_PROFILES,
    LOCAL_LITE_MEMORY_PROFILES,
    LocalDuckDBConfig,
    LocalLiteConfig,
    local_duckdb_config,
    local_lite_config,
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
    get_descendants_bfs,
    get_parents,
    is_descendant,
)
from .services.lookup import get_code_info, get_code_infos
from .services.mapping import get_code_mappings
from .services.optimize import optimize_codes
from .services.patient_friendly import get_patient_friendly_names
from .services.resolution import resolve_codes

__all__ = [
    "__version__",
    # Primary API
    "Terminology",
    "connect",
    "connect_remote",
    "search",
    "extract",
    "find_terms",
    "resolve_spans",
    "create_fhir_app",
    "cache_clear",
    "cache_info",
    "cache_versions",
    # Types
    "CodeRef",
    "CodeInfo",
    "CodeMapping",
    "CodeRelation",
    "CodeResolution",
    "ConceptMapRow",
    "FriendlyNameResult",
    "NameSearchResult",
    "OptimizeResult",
    "OptimizeRule",
    "Provenance",
    "ProvenanceStep",
    "SourceStats",
    # Advanced — engines
    "LocalDuckDBEngine",
    "LocalLiteEngine",
    "RemoteApiEngine",
    "LocalDuckDBConfig",
    "LocalLiteConfig",
    "LOCAL_DUCKDB_MEMORY_PROFILES",
    "LOCAL_LITE_MEMORY_PROFILES",
    "local_duckdb_config",
    "local_lite_config",
    # Advanced — service functions
    "get_ancestors",
    "get_children",
    "get_code_relations",
    "get_descendants",
    "get_descendants_bfs",
    "get_parents",
    "is_descendant",
    "get_code_info",
    "get_code_infos",
    "get_code_mappings",
    "get_code_ttys",
    "get_source_stats",
    "get_patient_friendly_names",
    "optimize_codes",
    "resolve_codes",
    "sample_source_codes",
    "search_names",
    "get_concept_map",
    "get_mapping_concept_map",
    "iter_concept_map",
    "iter_mapping_concept_map",
    "iter_batches",
    "iter_hierarchy_bulk",
    "iter_lookup_bulk",
    "iter_mapping_bulk",
    "iter_patient_friendly_bulk",
    # Advanced — domain helpers
    "cross_reference",
    "diagnosis_codes",
    "discover",
    "drugs_by_class",
    "drugs_for_indication",
    "fda_label_by_rxcui",
    "guideline_fulltext",
    "guideline_recommendations",
    "guideline_search",
    "guidelines_for_code",
    "hcpcs_drugs",
    "indication_search",
    "lab_codes",
    "lab_value_codes",
    "procedure_codes",
    "search_drug",
    "vaccine_codes",
    # Advanced — dataframe helpers
    "code_ttys_dataframe",
    "conceptmap_dataframe",
    "hierarchy_dataframe",
    "lookup_dataframe",
    "map_dataframe",
    "mapping_conceptmap_dataframe",
    "patient_friendly_dataframe",
    "sample_codes_dataframe",
    "search_names_dataframe",
    "source_stats_dataframe",
    # Advanced — data setup
    "DEFAULT_UMLS_RELEASE_TYPE",
    "DEFAULT_UMLS_VERIFY_SOURCES",
    "annotate_umls_duckdb",
    "build_umls_duckdb",
    "download_umls_release",
    "prepare_umls_duckdb",
    "verify_umls_duckdb",
    # Advanced — schemas
    "OUTPUT_SCHEMA_VERSION",
    "OutputField",
    "OutputSchema",
    "get_output_schema",
    "list_output_schemas",
]
