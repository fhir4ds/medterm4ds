"""Service-layer entry points."""

from .bulk import (
    iter_batches,
    iter_hierarchy_bulk,
    iter_lookup_bulk,
    iter_mapping_bulk,
    iter_patient_friendly_bulk,
)
from .conceptmap import (
    get_concept_map,
    get_mapping_concept_map,
    iter_concept_map,
    iter_mapping_concept_map,
)
from .crosswalk_prepared import get_crosswalk_mappings
from .data_setup import (
    DEFAULT_UMLS_RELEASE_TYPE,
    DEFAULT_UMLS_VERIFY_SOURCES,
    annotate_umls_duckdb,
    build_duckdb_from_rrf,
    build_umls_duckdb,
    download_release,
    download_umls_release,
    prepare_derived_tables,
    prepare_umls_duckdb,
    verify_duckdb,
    verify_umls_duckdb,
)
from .discovery import get_code_ttys, get_source_stats, sample_source_codes, search_names
from .hierarchy import (
    get_ancestors,
    get_children,
    get_code_relations,
    get_descendants,
    get_parents,
)
from .inventory import (
    DEFAULT_INVENTORY_SOURCES,
    count_source_codes,
    iter_source_codes,
    normalize_sources,
)
from .lookup import (
    get_code_info,
    get_code_info_prepared,
    get_code_infos,
    get_code_infos_prepared,
)
from .mapping import get_code_mappings
from .patient_friendly import get_patient_friendly_name, get_patient_friendly_names
from .schema_reporting import (
    empty_schema_report_metadata,
    missing_prepared_tables,
    report_db_role_metadata,
    schema_report_metadata,
)
from .selection import Candidate, rank_candidates, select_frontier
from .walk import (
    get_ancestors_prepared,
    get_children_prepared,
    get_descendants_prepared,
    get_parents_prepared,
)

__all__ = [
    "DEFAULT_INVENTORY_SOURCES",
    "DEFAULT_UMLS_RELEASE_TYPE",
    "DEFAULT_UMLS_VERIFY_SOURCES",
    "annotate_umls_duckdb",
    "build_duckdb_from_rrf",
    "build_umls_duckdb",
    "Candidate",
    "count_source_codes",
    "download_release",
    "download_umls_release",
    "empty_schema_report_metadata",
    "get_ancestors",
    "get_ancestors_prepared",
    "get_children",
    "get_children_prepared",
    "get_code_relations",
    "get_code_ttys",
    "get_code_info",
    "get_code_info_prepared",
    "get_code_infos",
    "get_code_infos_prepared",
    "get_code_mappings",
    "get_concept_map",
    "get_crosswalk_mappings",
    "get_mapping_concept_map",
    "get_descendants",
    "get_descendants_prepared",
    "get_parents",
    "get_parents_prepared",
    "get_patient_friendly_name",
    "get_patient_friendly_names",
    "get_source_stats",
    "iter_batches",
    "iter_concept_map",
    "iter_hierarchy_bulk",
    "iter_lookup_bulk",
    "iter_mapping_bulk",
    "iter_mapping_concept_map",
    "iter_patient_friendly_bulk",
    "iter_source_codes",
    "missing_prepared_tables",
    "normalize_sources",
    "prepare_derived_tables",
    "prepare_umls_duckdb",
    "rank_candidates",
    "report_db_role_metadata",
    "sample_source_codes",
    "schema_report_metadata",
    "search_names",
    "select_frontier",
    "verify_duckdb",
    "verify_umls_duckdb",
]
