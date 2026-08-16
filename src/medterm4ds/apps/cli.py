"""Command line interface for medterm4ds."""

from __future__ import annotations

import argparse
import functools
import os
import sys
import time
from pathlib import Path

from medterm4ds.core.config import (
    LOCAL_DUCKDB_MEMORY_PROFILES,
    local_duckdb_config,
    validate_memory_limit,
)
from medterm4ds.core.env import env_int, env_str
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.outputs import (
    OutputPosition,
    default_checkpoint_path,
    read_output_position,
    render_table,
    render_tree,
    write_checkpointed_rows,
    write_csv,
    write_fhir_concept_map,
    write_jsonl,
)
from medterm4ds.services.bulk import (
    iter_hierarchy_bulk,
    iter_lookup_bulk,
    iter_mapping_bulk,
    iter_patient_friendly_bulk,
)
from medterm4ds.services.conceptmap import iter_concept_map, iter_mapping_concept_map
from medterm4ds.services.data_setup import (
    DEFAULT_UMLS_RELEASE_TYPE,
    DEFAULT_UMLS_VERIFY_SOURCES,
    annotate_umls_duckdb,
    build_duckdb_from_rrf,
    download_release,
    prepare_umls_duckdb,
    verify_duckdb,
)
from medterm4ds.services.discovery import (
    get_code_ttys,
    get_source_stats,
    sample_source_codes,
    search_names,
)
from medterm4ds.services.hierarchy import get_code_relations
from medterm4ds.services.inventory import (
    DEFAULT_INVENTORY_SOURCES,
    count_source_codes,
    iter_source_codes,
    normalize_sources,
)
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.mapping import get_code_mappings
from medterm4ds.services.optimize import optimize_codes
from medterm4ds.services.resolution import ALLOWED_RESOLVE_MODES, resolve_codes
from medterm4ds.services.search import SEARCH_CATEGORIES

_HIERARCHY_DIRECTIONS = ("parents", "children", "ancestors", "descendants")


def _positive_int(value: str) -> int:
    """argparse type: reject zero/negative/non-integer limits (QC-125).

    ``--limit -1`` previously hit Python slice semantics in the search
    service and silently returned N-1 results.
    """
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer, got {value!r}") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {parsed}")
    return parsed


# QC-380 (MEDIUM): DuckDB size-string format for --memory-limit. Pre-fix,
# ``--memory-limit nonsense`` opened the connection first and died with a raw
# ``duckdb.ParserException: Memory must have a number (e.g. 1GB)`` traceback.
# QC-464/EC-21: the canonical pattern now lives in core.config
# (validate_memory_limit) so the MEDTERM4DS_MEMORY_LIMIT env fallback below
# enforces the same shape; this argparse type stays a thin wrapper.
def _memory_limit_string(value: str) -> str:
    """argparse type: reject malformed --memory-limit values (QC-380)."""
    try:
        return validate_memory_limit(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expects a DuckDB size string like 4GB or 512MB, got {value!r}"
        ) from None


def _env_memory_limit() -> str | None:
    """--memory-limit default from MEDTERM4DS_MEMORY_LIMIT, validated (QC-464)."""
    value = env_str("MEDTERM4DS_MEMORY_LIMIT")
    if value is None:
        return None
    return _memory_limit_string(value)


def main(argv: list[str] | None = None) -> int:
    # CR-043 (review-5 finding 5): argparse never applies type= to a DEFAULT,
    # so a garbage env default (MEDTERM4DS_THREADS=abc via env_int, or
    # MEDTERM4DS_MEMORY_LIMIT=nonsense via _env_memory_limit's
    # ArgumentTypeError) escaped build_parser() as a raw traceback instead
    # of the CLI's clean-error envelope. Catch both and exit one-liner.
    try:
        parser = build_parser()
    except (ValueError, argparse.ArgumentTypeError) as exc:
        raise SystemExit(f"Error: {exc}") from None
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="medterm4ds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    conceptmap = subparsers.add_parser("conceptmap", help="Generate ConceptMap outputs.")
    conceptmap_subparsers = conceptmap.add_subparsers(dest="target", required=True)
    patient_friendly = conceptmap_subparsers.add_parser(
        "patient-friendly",
        help="Generate a patient-friendly ConceptMap from source terminology codes.",
    )
    _add_patient_friendly_args(patient_friendly)
    patient_friendly.set_defaults(func=run_patient_friendly_conceptmap)
    mapping_conceptmap = conceptmap_subparsers.add_parser(
        "mapping",
        help="Generate a source-to-target ConceptMap from terminology mappings.",
    )
    _add_mapping_conceptmap_args(mapping_conceptmap)
    mapping_conceptmap.set_defaults(func=run_mapping_conceptmap)

    bulk = subparsers.add_parser("bulk", help="Run bulk terminology exports.")
    bulk_subparsers = bulk.add_subparsers(dest="workflow", required=True)
    bulk_lookup = bulk_subparsers.add_parser("lookup", help="Bulk exact code lookup.")
    _add_bulk_lookup_args(bulk_lookup)
    bulk_lookup.set_defaults(func=run_bulk_lookup)
    bulk_map = bulk_subparsers.add_parser("map", help="Bulk source-to-target mapping.")
    _add_bulk_mapping_args(bulk_map)
    bulk_map.set_defaults(func=run_bulk_mapping)
    bulk_hierarchy = bulk_subparsers.add_parser("hierarchy", help="Bulk hierarchy traversal.")
    _add_bulk_hierarchy_args(bulk_hierarchy)
    bulk_hierarchy.set_defaults(func=run_bulk_hierarchy)
    bulk_patient_friendly = bulk_subparsers.add_parser(
        "patient-friendly",
        help="Bulk patient-friendly name resolution.",
    )
    _add_bulk_patient_friendly_args(bulk_patient_friendly)
    bulk_patient_friendly.set_defaults(func=run_bulk_patient_friendly)

    lookup = subparsers.add_parser("lookup", help="Look up exact terminology codes.")
    _add_lookup_args(lookup)
    lookup.set_defaults(func=run_lookup)

    mapping = subparsers.add_parser("map", help="Map codes to target vocabularies.")
    _add_mapping_args(mapping)
    mapping.set_defaults(func=run_mapping)

    resolve = subparsers.add_parser("resolve", help="Resolve active, obsolete, and NDC inputs.")
    _add_resolve_args(resolve)
    resolve.set_defaults(func=run_resolve)

    optimize = subparsers.add_parser("optimize", aliases=["opt", "optimise"], help="Optimize valueset codes.")
    _add_optimize_args(optimize)
    optimize.set_defaults(func=run_optimize)

    sources = subparsers.add_parser("sources", help="List terminology source statistics.")
    _add_sources_args(sources)
    sources.set_defaults(func=run_source_stats)

    source_stats = subparsers.add_parser(
        "source-stats",
        help="List terminology source statistics.",
    )
    _add_sources_args(source_stats)
    source_stats.set_defaults(func=run_source_stats)

    sample_codes = subparsers.add_parser("sample-codes", help="Sample active source codes.")
    _add_sample_codes_args(sample_codes)
    sample_codes.set_defaults(func=run_sample_codes)

    code_ttys = subparsers.add_parser("code-ttys", help="Inspect active atoms and TTYs for codes.")
    _add_code_ttys_args(code_ttys)
    code_ttys.set_defaults(func=run_code_ttys)

    search = subparsers.add_parser("search-names", help="Search active terminology names.")
    _add_search_names_args(search)
    search.set_defaults(func=run_search_names)

    # Intelligent text-to-code search (BM25 + SapBERT)
    text_search = subparsers.add_parser(
        "search",
        help="Text-to-code search (lexical/semantic/hybrid via BM25 + SapBERT).",
    )
    text_search.add_argument("query", help="Free text to search for.")
    text_search.add_argument(
        "--mode", default="lexical", choices=["lexical", "semantic", "hybrid", "canonical"],
        help="Search mode (default: lexical).",
    )
    text_search.add_argument(
        "--sources", nargs="*", default=None,
        help="Restrict to source systems (e.g., SNOMEDCT_US RXNORM).",
    )
    text_search.add_argument("--limit", type=_positive_int, default=20, help="Max results (default: 20).")
    text_search.add_argument(
        "--result-types", nargs="*", default=None, dest="result_types",
        # QC-129 (CR-002): build the help text from SEARCH_CATEGORIES — the
        # old hardcoded list advertised drug_class/vital/symptom, which are
        # valid canonical result_types but NOT BM25 categories, so the CLI
        # client-side filter silently dropped every result.
        help=f"Filter lexical/semantic/hybrid results by category ({', '.join(SEARCH_CATEGORIES)}). "
        "Values outside this set only match canonical-mode result_types.",
    )
    # QC-382 (MEDIUM): the ``search`` parser previously accepted a dead
    # ``--db`` flag that was never opened — a user pointing at the wrong
    # (or a nonexistent) DB got correct-looking results from the search
    # index with no signal. Removed rather than wired: search reads BM25 /
    # SapBERT indexes, not DuckDB, so any --db value is semantically
    # meaningless here. (The missing --output/--format flags are deferred
    # as a feature decision; run_text_search still routes stdout JSON via
    # _write_record_results' getattr defaults.)
    text_search.set_defaults(func=run_text_search)

    # Text extraction (NER + ConText + code resolution)
    extract = subparsers.add_parser(
        "extract",
        help="Extract medical concepts from free text (NER + ConText + search).",
    )
    extract.add_argument("text", help="Free text to extract concepts from.")
    extract.add_argument(
        "--format", default="codes", choices=["codes", "terms", "annotated"],
        help="Output format: 'codes' (resolved), 'terms' (spans only), or 'annotated' (inline [entity|type] markup). Default: codes.",
    )
    # QC-169: these used nargs="*" — 'medterm4ds extract --result-types
    # condition "<text>"' swallowed the positional text as a second
    # --result-types value and errored with 'the following arguments are
    # required: text'. Comma-separated single values keep multiple selection
    # possible without consuming positionals.
    extract.add_argument(
        "--ner-labels", default=None, dest="ner_labels", metavar="L1,L2,...",
        help="GLiNER NER labels to detect, comma-separated (default: lab test,vital sign,panel,therapeutic agent,therapeutic class,immunization,medical intervention,disorder,symptom).",
    )
    extract.add_argument(
        "--result-types", default=None, dest="result_types", metavar="T1,T2,...",
        help="Filter resolved concepts by result type, comma-separated (condition, medication, drug_class, lab, vital, procedure, vaccine, symptom).",
    )
    # QC-155/QC-167: expose the service's full mode/grade vocabulary
    # (canonical is the service DEFAULT; 'exact'/'broader' are canonical-mode
    # grades) and default to the env-configurable service defaults rather
    # than hardcoded hybrid/certain.
    from medterm4ds.services.extraction import (
        DEFAULT_MIN_GRADE as _EXTRACT_DEFAULT_MIN_GRADE,
        DEFAULT_SEARCH_MODE as _EXTRACT_DEFAULT_MODE,
    )
    extract.add_argument(
        "--mode", default=_EXTRACT_DEFAULT_MODE,
        choices=["lexical", "semantic", "hybrid", "canonical"],
        help="Search mode used to resolve extracted spans to codes (default: canonical).",
    )
    extract.add_argument(
        "--min-grade", default=_EXTRACT_DEFAULT_MIN_GRADE,
        choices=["certain", "exact", "probable", "possible", "broader"],
        help="Minimum ConText certainty grade to keep (default: certain).",
    )
    extract.add_argument("--include-negated", action="store_true", help="Include negated mentions.")
    # QC-166: these existed only on the Python API — historical mentions
    # ('History of MI in 2019') were unreachable from CLI/FHIR/MCP.
    extract.add_argument("--include-uncertain", action="store_true", help="Include uncertain mentions.")
    extract.add_argument("--include-historical", action="store_true", help="Include historical mentions.")
    extract.add_argument("--include-family", action="store_true", help="Include family-history mentions (relatives' conditions).")
    extract.add_argument("--output", default=None)
    # QC-354 (MEDIUM): default None (not 'json') so _write_record_results can
    # infer the format from the --output extension (.csv → csv, .jsonl → jsonl)
    # — mirrors run_optimize's QC-204 fix; this extract sibling was missed.
    extract.add_argument("--format-output", dest="format_output", default=None, choices=["json", "csv"])
    extract.set_defaults(func=run_extract)

    hierarchy = subparsers.add_parser("hierarchy", help="Traverse code hierarchies.")
    hierarchy_subparsers = hierarchy.add_subparsers(dest="direction", required=True)
    for direction in _HIERARCHY_DIRECTIONS:
        hierarchy_command = hierarchy_subparsers.add_parser(
            direction,
            help=f"Return {direction} for terminology codes.",
        )
        _add_hierarchy_args(hierarchy_command)
        hierarchy_command.set_defaults(func=run_hierarchy)

    data = subparsers.add_parser("data", help="Download and build terminology data.")
    data_subparsers = data.add_subparsers(dest="data_command", required=True)
    data_download = data_subparsers.add_parser("download", help="Download a UTS release zip.")
    _add_data_download_args(data_download)
    data_download.set_defaults(func=run_data_download)
    data_build = data_subparsers.add_parser("build-duckdb", help="Build the local DuckDB database from RRF files.")
    _add_data_build_args(data_build)
    data_build.set_defaults(func=run_data_build_duckdb)
    data_prepare = data_subparsers.add_parser("prepare-derived", help="Create derived local DuckDB guardrail tables.")
    _add_data_prepare_derived_args(data_prepare)
    data_prepare.set_defaults(func=run_data_prepare_derived)
    data_verify = data_subparsers.add_parser("verify", help="Verify a local DuckDB database.")
    _add_data_verify_args(data_verify)
    data_verify.set_defaults(func=run_data_verify)

    return parser


def _add_common_engine_args(parser: argparse.ArgumentParser, *, db_required: bool = True) -> None:
    parser.add_argument(
        "--db",
        required=db_required,
        default=None if db_required else os.getenv("MEDTERM4DS_DB"),
        help=(
            "Path to the UMLS DuckDB database."
            if db_required
            else "Path to the UMLS DuckDB database (default: $MEDTERM4DS_DB)."
        ),
    )
    # QC-464 (MEDIUM): the documented engine env vars are now honored as
    # FALLBACK DEFAULTS — an explicit flag always wins (argparse replaces
    # the default), but the same operator environment no longer produces a
    # different engine budget on the CLI vs the three servers. Pre-fix all
    # five MEDTERM4DS_* engine vars were silently ignored here, so
    # MEDTERM4DS_THREADS=-1 with a correct --threads flag still exited 0
    # with the flag silently dropped.
    parser.add_argument(
        "--memory-profile",
        choices=tuple(sorted(LOCAL_DUCKDB_MEMORY_PROFILES)),
        default=env_str("MEDTERM4DS_MEMORY_PROFILE", "balanced"),
        help=(
            "Named local DuckDB memory profile "
            "(default: $MEDTERM4DS_MEMORY_PROFILE or balanced)."
        ),
    )
    parser.add_argument(
        "--memory-limit",
        type=_memory_limit_string,
        default=_env_memory_limit(),
        help=(
            "Override DuckDB memory limit (DuckDB size string, e.g. 4GB, 512MB; "
            "default: $MEDTERM4DS_MEMORY_LIMIT)."
        ),
    )
    parser.add_argument(
        "--temp-dir",
        default=env_str("MEDTERM4DS_TEMP_DIR"),
        help="DuckDB temporary directory (default: $MEDTERM4DS_TEMP_DIR).",
    )
    # QC-466 (MEDIUM): --threads/--query-chunk-size now validate like
    # --memory-limit (QC-380). Pre-fix, ``--threads -1`` died with a raw
    # duckdb.SyntaxException traceback, threads=0 was silently dropped, and
    # ``--query-chunk-size -5`` was silently clamped to 1 — three sibling
    # knobs, three different behaviors.
    parser.add_argument(
        "--threads",
        type=_positive_int,
        default=env_int("MEDTERM4DS_THREADS", minimum=1),
        help="Override DuckDB thread count (default: $MEDTERM4DS_THREADS).",
    )
    parser.add_argument(
        "--query-chunk-size",
        type=_positive_int,
        default=env_int("MEDTERM4DS_QUERY_CHUNK_SIZE", minimum=1),
        help=(
            "Override local DuckDB internal query chunk size "
            "(default: $MEDTERM4DS_QUERY_CHUNK_SIZE)."
        ),
    )


def _add_patient_friendly_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_INVENTORY_SOURCES),
        help="Comma-separated source vocabularies.",
    )
    parser.add_argument("--output", required=True, help="Output file path.")
    parser.add_argument(
        "--format",
        choices=("jsonl", "csv", "fhir-json"),
        default=None,
        help="Output format. Defaults to the output file extension.",
    )
    parser.add_argument("--batch-size", type=int, default=5000, help="Codes per resolution batch (default: 5000).")
    parser.add_argument("--fetch-size", type=int, default=10_000, help="Rows fetched per source-code scan (default: 10000).")
    parser.add_argument("--max-depth", type=int, default=5, help="Broader-walk depth for patient-friendly fallback (default: 5; 0 = exact/same-CUI matches only).")
    parser.add_argument("--limit", type=_positive_int, default=None, help="Optional total code limit (must be >= 1).")
    parser.add_argument(
        "--no-prepare-cache",
        action="store_true",
        help="Skip local DuckDB temp cache preparation.",
    )
    parser.add_argument(
        "--cache-indexes",
        action="store_true",
        help="Create temp cache indexes. Usually slower for this workflow.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Print progress and throughput to stderr.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append to an existing output and resume after its last completed source/code.",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Checkpoint sidecar path. Defaults to <output>.checkpoint.json.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=1000,
        help="Write checkpoint state every N output rows.",
    )


def _add_bulk_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", required=True, help="Output file path.")
    parser.add_argument(
        "--format",
        choices=("jsonl", "csv", "fhir-json"),
        default=None,
        help="Output format. Defaults to the output file extension.",
    )
    parser.add_argument("--batch-size", type=int, default=5000, help="Codes per resolution batch (default: 5000).")
    parser.add_argument("--fetch-size", type=int, default=10_000, help="Rows fetched per source-code scan (default: 10000).")
    parser.add_argument("--limit", type=_positive_int, default=None, help="Optional total code limit (must be >= 1).")
    parser.add_argument(
        "--no-prepare-cache",
        action="store_true",
        help="Skip local DuckDB temp cache preparation.",
    )
    parser.add_argument(
        "--cache-indexes",
        action="store_true",
        help="Create temp cache indexes. Usually slower for this workflow.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Print progress and throughput to stderr.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=1000,
        help="Write checkpoint state every N output rows for JSONL/CSV outputs.",
    )


def _add_bulk_record_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_INVENTORY_SOURCES),
        help="Comma-separated source vocabularies.",
    )
    parser.add_argument("--output", required=True, help="Output file path.")
    parser.add_argument(
        "--format",
        choices=("jsonl", "csv"),
        default=None,
        help="Output format. Defaults to the output file extension.",
    )
    parser.add_argument("--batch-size", type=int, default=5000, help="Codes per resolution batch (default: 5000).")
    parser.add_argument("--fetch-size", type=int, default=10_000, help="Rows fetched per source-code scan (default: 10000).")
    parser.add_argument("--limit", type=_positive_int, default=None, help="Optional total code limit (must be >= 1).")
    parser.add_argument(
        "--no-prepare-cache",
        action="store_true",
        help="Skip local DuckDB temp cache preparation.",
    )
    parser.add_argument(
        "--cache-indexes",
        action="store_true",
        help="Create temp cache indexes. Usually slower for this workflow.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Print progress and throughput to stderr.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append to an existing output and resume after its last completed source/code.",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Checkpoint sidecar path. Defaults to <output>.checkpoint.json.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=1000,
        help="Write checkpoint state every N output rows.",
    )


def _add_bulk_lookup_args(parser: argparse.ArgumentParser) -> None:
    _add_bulk_record_args(parser)
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip missing/suppressed lookup rows instead of writing null-valued records.",
    )


def _add_bulk_mapping_args(parser: argparse.ArgumentParser) -> None:
    _add_bulk_record_args(parser)
    parser.add_argument(
        "--target-sources",
        required=True,
        help="Comma-separated target vocabularies.",
    )
    parser.add_argument(
        "--max-results-per-code",
        type=int,
        default=50,
        help="Maximum target mappings per input code.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=0,
        help="Hierarchy fallback depth for broader/narrower mapping.",
    )
    parser.add_argument(
        "--include-target-ancestors",
        action="store_true",
        help="Also include broader target ancestors from exact same-CUI target mappings.",
    )
    parser.add_argument(
        "--include-target-descendants",
        action="store_true",
        help="Also include narrower target descendants from exact same-CUI target mappings.",
    )


def _add_bulk_hierarchy_args(parser: argparse.ArgumentParser) -> None:
    _add_bulk_record_args(parser)
    parser.add_argument(
        "--direction",
        choices=_HIERARCHY_DIRECTIONS,
        required=True,
        help="Hierarchy direction.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=5,
        help="Maximum depth for ancestors or descendants. Parents and children are direct only.",
    )


def _add_bulk_patient_friendly_args(parser: argparse.ArgumentParser) -> None:
    _add_bulk_record_args(parser)
    parser.add_argument("--max-depth", type=int, default=5, help="Broader-walk depth for patient-friendly fallback (default: 5; 0 = exact/same-CUI matches only).")


def _add_lookup_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument("--source", action="append", required=True, help="Source vocabulary.")
    parser.add_argument("--code", action="append", required=True, help="Code to look up.")
    parser.add_argument(
        "--resolve-mode",
        choices=tuple(sorted(ALLOWED_RESOLVE_MODES)),
        default="active_only",
        help="Whether obsolete/NDC inputs should resolve before lookup.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to stdout JSON.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "jsonl", "csv", "table", "tree"),
        default=None,
        help="Output format. Defaults to output extension, or JSON for stdout.",
    )


def _add_mapping_conceptmap_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_INVENTORY_SOURCES),
        help="Comma-separated source vocabularies.",
    )
    parser.add_argument(
        "--target-sources",
        required=True,
        help="Comma-separated target vocabularies.",
    )
    parser.add_argument(
        "--max-results-per-code",
        type=int,
        default=50,
        help="Maximum target mappings per input code.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=0,
        help="Hierarchy fallback depth for broader/narrower mapping. Defaults to exact same-CUI only.",
    )
    parser.add_argument(
        "--include-target-ancestors",
        action="store_true",
        help="Also include broader target ancestors from exact same-CUI target mappings.",
    )
    parser.add_argument(
        "--include-target-descendants",
        action="store_true",
        help="Also include narrower target descendants from exact same-CUI target mappings.",
    )
    _add_bulk_output_args(parser)


def _add_hierarchy_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument("--source", action="append", required=True, help="Source vocabulary.")
    parser.add_argument("--code", action="append", required=True, help="Code to traverse.")
    parser.add_argument(
        "--max-depth",
        type=int,
        default=5,
        help="Maximum depth for ancestors or descendants. Parents and children are direct only.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to stdout JSON.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "jsonl", "csv"),
        default=None,
        help="Output format. Defaults to output extension, or JSON for stdout.",
    )


def _add_mapping_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument("--source", action="append", required=True, help="Source vocabulary.")
    parser.add_argument("--code", action="append", required=True, help="Code to map.")
    parser.add_argument(
        "--target-source",
        action="append",
        required=True,
        help="Target vocabulary. Repeat for multiple targets.",
    )
    parser.add_argument(
        "--max-results-per-code",
        type=int,
        default=50,
        help="Maximum target mappings per input code.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=0,
        help="Hierarchy fallback depth for broader/narrower mapping. Defaults to exact same-CUI only.",
    )
    parser.add_argument(
        "--include-target-ancestors",
        action="store_true",
        help="Also include broader target ancestors from exact same-CUI target mappings.",
    )
    parser.add_argument(
        "--include-target-descendants",
        action="store_true",
        help="Also include narrower target descendants from exact same-CUI target mappings.",
    )
    parser.add_argument(
        "--resolve-mode",
        choices=tuple(sorted(ALLOWED_RESOLVE_MODES)),
        default="active_only",
        help="Whether obsolete/NDC inputs should resolve before mapping.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to stdout JSON.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "jsonl", "csv", "table", "tree"),
        default=None,
        help="Output format. Defaults to output extension, or JSON for stdout.",
    )


def _add_resolve_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument("--source", action="append", required=True, help="Source vocabulary.")
    parser.add_argument("--code", action="append", required=True, help="Code to resolve.")
    parser.add_argument(
        "--resolve-mode",
        choices=tuple(sorted(ALLOWED_RESOLVE_MODES)),
        default="historical",
        help="Resolution mode: active_only (skip historical for non-NDC), "
        "historical (return input atom + replacement candidates), "
        "resolve_current (return resolved active replacement).",
    )
    parser.add_argument("--output", default=None, help="Optional output path. Defaults to stdout JSON.")
    parser.add_argument(
        "--format",
        choices=("json", "jsonl", "csv", "table", "tree"),
        default=None,
        help="Output format. Defaults to output extension, or JSON for stdout.",
    )


def _add_optimize_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument("--source", required=True, help="Source vocabulary for all codes.")
    parser.add_argument("--code", action="append", required=True, help="Code to optimize.")
    parser.add_argument("--relationship", default=None, help="Hierarchy relationship override.")
    parser.add_argument(
        "--output-format",
        choices=("compact", "flat"),
        default="compact",
        help="Optimize rule format.",
    )
    parser.add_argument("--include-codes", action="store_true", help="Include the input code list in the output payload.")
    parser.add_argument("--output", default=None, help="Optional output path. Defaults to stdout JSON.")
    parser.add_argument(
        "--format",
        choices=("json", "jsonl", "csv", "table", "tree"),
        default=None,
        help="Output format. Defaults to output extension, or JSON for stdout.",
    )


def _add_sources_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument(
        "--sources",
        default=None,
        help="Optional comma-separated source vocabulary filter. Defaults to all active sources.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to stdout JSON.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "jsonl", "csv"),
        default=None,
        help="Output format. Defaults to output extension, or JSON for stdout.",
    )


def _add_sample_codes_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_INVENTORY_SOURCES),
        help="Comma-separated source vocabularies.",
    )
    parser.add_argument(
        "--per-source",
        type=int,
        default=10,
        help="Maximum codes to sample per source.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to stdout JSON.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "jsonl", "csv"),
        default=None,
        help="Output format. Defaults to output extension, or JSON for stdout.",
    )


def _add_code_ttys_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument("--source", action="append", required=True, help="Source vocabulary.")
    parser.add_argument("--code", action="append", required=True, help="Code to inspect.")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to stdout JSON.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "jsonl", "csv"),
        default=None,
        help="Output format. Defaults to output extension, or JSON for stdout.",
    )


def _add_search_names_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument("--query", required=True, help="Name text to search for.")
    parser.add_argument(
        "--sources",
        default=None,
        help="Optional comma-separated source vocabulary filter.",
    )
    parser.add_argument(
        "--tty",
        action="append",
        default=None,
        help="Optional TTY filter. Repeat for multiple TTYs.",
    )
    parser.add_argument("--limit", type=_positive_int, default=25, help="Maximum results to return (must be >= 1).")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to stdout JSON.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "jsonl", "csv"),
        default=None,
        help="Output format. Defaults to output extension, or JSON for stdout.",
    )


def _add_data_download_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key", default=None, help="UMLS/UTS API key. Defaults to UMLS_API_KEY.")
    parser.add_argument(
        "--release-type",
        default=DEFAULT_UMLS_RELEASE_TYPE,
        help="UTS releaseType. Defaults to umls-metathesaurus-full-subset.",
    )
    parser.add_argument(
        "--release-version",
        default=None,
        help="Optional release version such as 2025AB. When omitted, the first UTS result is used.",
    )
    parser.add_argument(
        "--current",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Filter UTS release metadata to current=true/false. Omitted by default.",
    )
    parser.add_argument("--output-dir", default="data/umls", help="Directory for downloaded raw release files.")
    parser.add_argument("--extract", action="store_true", help="Extract the downloaded zip.")


def _add_data_build_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--rrf-dir",
        required=True,
        help="Directory containing flat RRF files, RRF.gz shards, or extracted UMLS .nlm release archives.",
    )
    parser.add_argument("--output-db", required=True, help="DuckDB database path to create.")
    parser.add_argument("--db-role", default=None, help="DB role to record in mt4ds.prepare_manifest.")
    parser.add_argument("--release-version", default=None, help="Optional UMLS release version to record in mt4ds.prepare_manifest.")
    parser.add_argument("--source-archive", default=None, help="Optional source archive path to record in mt4ds.prepare_manifest.")
    parser.add_argument("--replace", action="store_true", help="Replace output database if it exists.")
    parser.add_argument("--batch-size", type=int, default=100_000, help="Deprecated; native DuckDB ingest ignores this.")


def _add_data_prepare_derived_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", required=True, help="DuckDB database path to update.")
    parser.add_argument("--replace", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--db-role", default=None, help="Optional DB role to record in mt4ds.prepare_manifest.")
    parser.add_argument("--release-version", default=None, help="Optional UMLS release version to record in mt4ds.prepare_manifest.")
    parser.add_argument("--source-archive", default=None, help="Optional source archive path to record in mt4ds.prepare_manifest.")


def _add_data_verify_args(parser: argparse.ArgumentParser) -> None:
    # QC-452 (MEDIUM): MEDTERM4DS_DB is the documented shared operator
    # contract (apps/mcp.py, apps/fhir_api.py); data verify honors it as the
    # --db default instead of hard-requiring the flag.
    _add_common_engine_args(parser, db_required=False)
    parser.add_argument(
        "--sources",
        # QC-450 (MEDIUM): consume the canonical default instead of a
        # hardcoded 7-source list that silently diverged from the Python API
        # default (which includes ICD10PCS) — same operation, different
        # coverage depending on the surface used.
        default=",".join(DEFAULT_UMLS_VERIFY_SOURCES),
        help="Comma-separated source vocabularies to verify.",
    )


def _validate_cli_max_depth(args: argparse.Namespace, *, warn_on_zero: bool = False) -> None:
    # QC-079/QC-083/QC-084/QC-085 (MEDIUM): pre-fix, ``--max-depth -5`` on
    # ``conceptmap mapping`` raised a raw Python traceback (ValueError from
    # services/mapping.py:36), while ``conceptmap patient-friendly`` silently
    # clamped to 0 via ``max(0, int(max_depth))``. Two sibling CLI
    # subcommands never harmonized. Surface a clean SystemExit for both,
    # mirroring EC-03 FIX-005 (run_hierarchy wrapper).
    #
    # ``warn_on_zero=True`` is set by the patient_friendly surface, where
    # max_depth=0 silently skips the broader walk. The mapping surface uses
    # depth=0 as the canonical "no ancestor walk" default — no warning
    # needed.
    max_depth = getattr(args, "max_depth", None)
    if max_depth is None:
        return
    if not isinstance(max_depth, int) or isinstance(max_depth, bool):
        raise SystemExit(
            f"Error: --max-depth must be an integer, got {type(max_depth).__name__}"
        )
    if max_depth < 0:
        raise SystemExit("Error: --max-depth must be non-negative")
    if max_depth == 0 and warn_on_zero:
        # patient_friendly surface: max_depth=0 silently skips the broader
        # walk. The service now logs a WARNING, but for the CLI bulk path
        # the WARNING may be missed — surface it on stderr too so operators
        # building conceptmap exports see the degraded run.
        import sys
        print(
            "Warning: --max-depth 0 skips the broader walk; only "
            "exact/same_cui matches will be returned.",
            file=sys.stderr,
        )


def _normalize_sources_or_exit(raw: str | None) -> tuple[str, ...] | None:
    # QC-387/QC-395 (LOW): an explicitly-supplied --sources value that
    # contains no non-empty vocabulary names is a client/script bug
    # (``--sources ''`` — empty variable, shell quoting slip). Pre-fix, the
    # bulk surfaces silently exported a 0-row "successful" file while the
    # sources/sample-codes surfaces silently WIDENED scope to every source
    # in the database. Sibling of the QC-324/QC-378 empty-input guard
    # family: reject with a diagnostic; ``None`` still means "default/all".
    if raw is None:
        return None
    normalized = normalize_sources(raw)
    if not normalized:
        raise SystemExit(
            "--sources must contain at least one non-empty vocabulary name "
            "(e.g. SNOMEDCT_US), got no usable entries."
        )
    return normalized


def run_patient_friendly_conceptmap(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    # QC-079/QC-083: validate max_depth before opening the DB / running the
    # query so invalid values exit cleanly without partial output.
    _validate_cli_max_depth(args, warn_on_zero=True)

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    sources = _normalize_sources_or_exit(args.sources)
    output_path = Path(args.output)
    output_format = args.format or _format_from_path(output_path)
    if args.resume and output_format == "fhir-json":
        raise SystemExit("FHIR JSON output is not resumable. Use JSONL or CSV for resumable bulk exports.")
    checkpoint_path = Path(args.checkpoint) if args.checkpoint else default_checkpoint_path(output_path)
    config = local_duckdb_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )

    resume_position = (
        read_output_position(output_path, output_format)
        if args.resume
        else OutputPosition()
    )
    remaining_limit = None
    if args.limit is not None:
        remaining_limit = max(args.limit - resume_position.rows, 0)

    progress = _Progress(enabled=args.progress, initial_rows=resume_position.rows)
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        if args.progress:
            counts = count_source_codes(con, sources)
            total = sum(counts.values())
            if args.limit is not None:
                total = min(total, args.limit)
            remaining = max(total - resume_position.rows, 0)
            progress.print(f"sources={dict(sorted(counts.items()))} total={total:,} remaining={remaining:,}")
            if resume_position.has_rows:
                progress.print(
                    "resuming after "
                    f"{resume_position.last_code.source}:{resume_position.last_code.code} "
                    f"({resume_position.rows:,} existing rows)"
                )

        engine = LocalDuckDBEngine(
            con,
            config=config,
            progress=progress.print if args.progress else None,
        )
        if not args.no_prepare_cache:
            start = time.perf_counter()
            engine.prepare_cache(sources, create_indexes=args.cache_indexes)
            progress.print(f"prepared cache in {time.perf_counter() - start:.2f}s")

        codes = iter_source_codes(
            con,
            sources,
            fetch_size=args.fetch_size,
            limit=remaining_limit,
            resume_after=resume_position.last_code if resume_position.has_rows else None,
        )
        rows = iter_concept_map(
            codes,
            engine=engine,
            batch_size=args.batch_size,
            max_depth=args.max_depth,
        )
        metadata = {
            "command": "conceptmap patient-friendly",
            "db": str(db_path),
            "sources": list(sources),
            "memory_profile": args.memory_profile,
            "limit": args.limit,
        }
        if output_format == "fhir-json":
            write_fhir_concept_map(
                progress.count_rows(rows),
                output_path,
                id_="medterm4ds-patient-friendly",
                url="urn:medterm4ds:ConceptMap:patient-friendly",
                title="medterm4ds Patient Friendly ConceptMap",
            )
            final_position = OutputPosition(rows=progress.rows)
        else:
            final_position = write_checkpointed_rows(
                rows,
                output_path,
                output_format=output_format,
                checkpoint_path=checkpoint_path,
                append=args.resume and output_path.exists(),
                checkpoint_every=args.checkpoint_every,
                initial_position=resume_position,
                metadata=metadata,
                on_row=progress.record_row,
            )
    finally:
        con.close()

    progress.print(f"wrote {final_position.rows:,} rows to {output_path}")
    if output_format != "fhir-json":
        progress.print(f"checkpoint {checkpoint_path}")
    return 0


def run_mapping_conceptmap(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    # QC-084/QC-085: validate max_depth before opening the DB so invalid
    # values exit cleanly without a Python traceback. Sibling of EC-03
    # FIX-005 (run_hierarchy wrapper).
    _validate_cli_max_depth(args, warn_on_zero=False)

    sources = _normalize_sources_or_exit(args.sources)
    target_sources = _normalize_sources_or_exit(args.target_sources)
    output_path = Path(args.output)
    output_format = args.format or _format_from_path(output_path)
    checkpoint_path = default_checkpoint_path(output_path)
    config = local_duckdb_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )

    progress = _Progress(enabled=args.progress)
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        if args.progress:
            counts = count_source_codes(con, sources)
            total = sum(counts.values())
            if args.limit is not None:
                total = min(total, args.limit)
            progress.print(
                f"sources={dict(sorted(counts.items()))} "
                f"targets={list(target_sources)} total={total:,}"
            )

        engine = LocalDuckDBEngine(
            con,
            config=config,
            progress=progress.print if args.progress else None,
        )
        if not args.no_prepare_cache:
            start = time.perf_counter()
            engine.prepare_cache(
                [*sources, *target_sources],
                create_indexes=args.cache_indexes,
            )
            progress.print(f"prepared cache in {time.perf_counter() - start:.2f}s")

        codes = iter_source_codes(
            con,
            sources,
            fetch_size=args.fetch_size,
            limit=args.limit,
        )
        rows = iter_mapping_concept_map(
            codes,
            engine=engine,
            target_sources=target_sources,
            batch_size=args.batch_size,
            max_results_per_code=args.max_results_per_code,
            max_depth=args.max_depth,
            include_target_ancestors=args.include_target_ancestors,
            include_target_descendants=args.include_target_descendants,
        )
        metadata = {
            "command": "conceptmap mapping",
            "db": str(db_path),
            "sources": list(sources),
            "target_sources": list(target_sources),
            "memory_profile": args.memory_profile,
            "limit": args.limit,
            "max_depth": args.max_depth,
        }
        if output_format == "fhir-json":
            write_fhir_concept_map(
                progress.count_rows(rows),
                output_path,
                id_="medterm4ds-mapping",
                url="urn:medterm4ds:ConceptMap:mapping",
                name="Medterm4dsMappingConceptMap",
                title="medterm4ds Source Mapping ConceptMap",
            )
            final_position = OutputPosition(rows=progress.rows)
        else:
            final_position = write_checkpointed_rows(
                rows,
                output_path,
                output_format=output_format,
                checkpoint_path=checkpoint_path,
                append=False,
                checkpoint_every=args.checkpoint_every,
                initial_position=OutputPosition(),
                metadata=metadata,
                on_row=progress.record_row,
            )
    finally:
        con.close()

    progress.print(f"wrote {final_position.rows:,} mapping rows to {output_path}")
    if output_format != "fhir-json":
        progress.print(f"checkpoint {checkpoint_path}")
    return 0


def run_bulk_lookup(args: argparse.Namespace) -> int:
    sources = _normalize_sources_or_exit(args.sources)
    return _run_bulk_record_export(
        args,
        sources=sources,
        cache_sources=sources,
        command="bulk lookup",
        allow_resume=True,
        row_factory=lambda codes, engine: iter_lookup_bulk(
            codes,
            engine=engine,
            batch_size=args.batch_size,
            include_missing=not args.skip_missing,
        ),
    )


def run_bulk_mapping(args: argparse.Namespace) -> int:
    sources = _normalize_sources_or_exit(args.sources)
    target_sources = _normalize_sources_or_exit(args.target_sources)
    return _run_bulk_record_export(
        args,
        sources=sources,
        cache_sources=(*sources, *target_sources),
        command="bulk map",
        allow_resume=False,
        row_factory=lambda codes, engine: iter_mapping_bulk(
            codes,
            engine=engine,
            target_sources=target_sources,
            batch_size=args.batch_size,
            max_results_per_code=args.max_results_per_code,
            max_depth=args.max_depth,
            include_target_ancestors=args.include_target_ancestors,
            include_target_descendants=args.include_target_descendants,
        ),
        metadata={"target_sources": list(target_sources), "max_depth": args.max_depth},
    )


def run_bulk_hierarchy(args: argparse.Namespace) -> int:
    sources = _normalize_sources_or_exit(args.sources)
    return _run_bulk_record_export(
        args,
        sources=sources,
        cache_sources=sources,
        command="bulk hierarchy",
        allow_resume=False,
        row_factory=lambda codes, engine: iter_hierarchy_bulk(
            codes,
            engine=engine,
            direction=args.direction,
            batch_size=args.batch_size,
            max_depth=args.max_depth,
        ),
        metadata={"direction": args.direction, "max_depth": args.max_depth},
    )


def run_bulk_patient_friendly(args: argparse.Namespace) -> int:
    sources = _normalize_sources_or_exit(args.sources)
    return _run_bulk_record_export(
        args,
        sources=sources,
        cache_sources=sources,
        command="bulk patient-friendly",
        allow_resume=True,
        row_factory=lambda codes, engine: iter_patient_friendly_bulk(
            codes,
            engine=engine,
            batch_size=args.batch_size,
            max_depth=args.max_depth,
        ),
        metadata={
            "max_depth": args.max_depth,
        },
    )


def run_lookup(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    config = local_duckdb_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )
    refs = _code_source_pairs(args.code, args.source)
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        engine = LocalDuckDBEngine(con, config=config)
        infos = get_code_infos(refs, engine=engine, resolve_mode=args.resolve_mode)
    finally:
        con.close()

    rows = [
        info.to_dict() if info else _missing_code_info(source=source, code=code)
        for info, (source, code) in zip(infos, refs, strict=True)
    ]
    _write_record_results(rows, output=args.output, output_format=args.format)
    return 0


def run_resolve(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    refs = _code_source_pairs(args.code, args.source)
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        engine = LocalDuckDBEngine(con, config=_local_duckdb_config_from_args(args))
        from medterm4ds.services.resolution import effective_code_refs
        _effective, resolutions = effective_code_refs(
            refs, engine=engine, resolve_mode=args.resolve_mode
        )
        if resolutions is None:
            # active_only fast-path for non-NDC inputs — still return
            # CodeResolution rows for shape-stability.
            resolutions = resolve_codes(_effective, engine=engine)
        rows = [resolution.to_dict() for resolution in resolutions]
    finally:
        con.close()

    _write_record_results(rows, output=args.output, output_format=args.format)
    return 0


def run_optimize(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    refs = [CodeRef(source=args.source, code=code) for code in args.code]
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        engine = LocalDuckDBEngine(con, config=_local_duckdb_config_from_args(args))
        # QC-197 (MEDIUM): surface input-validation errors (unknown source,
        # empty code, prefix relationship, source override mismatch) as
        # clean CLI messages rather than raw Python tracebacks. Sibling of
        # the QC-049/QC-059 run_hierarchy wrapper and EC-02 FIX-008
        # (run_mapping wrapper).
        try:
            result = optimize_codes(
                refs,
                engine=engine,
                relationship=args.relationship,
                output_format=args.output_format,
                include_codes=args.include_codes,
            )
        except ValueError as exc:
            raise SystemExit(f"Error: {exc}") from exc
    finally:
        con.close()

    payload = result.to_dict(include_codes=args.include_codes)
    # QC-204 (MEDIUM): --output <file> without --format previously wrote
    # pretty JSON regardless of a .jsonl/.csv extension. Resolve the record
    # formats from the output path first, mirroring run_bulk's inference.
    resolved_format = args.format
    if resolved_format is None and args.output:
        resolved_format = _record_format_from_path(Path(args.output))
    if resolved_format in {"table", "csv", "jsonl"}:
        rows = payload["rules"]
        # QC-200 (LOW): csv/table cells previously rendered exclude as the
        # Python list repr '[]' and dropped covered_codes entirely. Flatten
        # list values to ';' joined code strings for record formats.
        if resolved_format in {"csv", "table"}:
            rows = [_flatten_optimize_rule_row(row) for row in rows]
        _write_record_results(rows, output=args.output, output_format=resolved_format)
    else:
        _write_payload(payload, output=args.output, output_format=args.format)
    return 0


def run_hierarchy(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    config = local_duckdb_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )
    refs = _code_source_pairs(args.code, args.source)
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        engine = LocalDuckDBEngine(con, config=config)
        # QC-049/QC-059 (MEDIUM): surface input-validation errors (invalid
        # direction, max_depth < 1, etc.) as clean CLI messages (exit 1 via
        # SystemExit) rather than raw Python tracebacks. Sibling of EC-02
        # FIX-008 (run_mapping wrapper).
        try:
            relations = get_code_relations(
                refs,
                engine=engine,
                direction=args.direction,
                max_depth=args.max_depth,
            )
        except (ValueError, TypeError) as exc:
            raise SystemExit(f"Error: {exc}") from exc
    finally:
        con.close()

    # QC-058 (HIGH): the engine forces ``max_depth=1`` for parents/children
    # (they are 1-hop by definition). If the user explicitly passed
    # ``--max-depth`` > 1 for parents/children, surface a warning so they
    # know the value was overridden (rather than silently ignored). The
    # --help text documents this, but a runtime warning closes the loop
    # for users who don't read --help.
    if args.direction in {"parents", "children"} and args.max_depth > 1:
        import sys
        print(
            f"Warning: --max-depth {args.max_depth} is ignored for "
            f"{args.direction} (parents/children are always direct). "
            "Use 'ancestors' or 'descendants' for multi-hop traversal.",
            file=sys.stderr,
        )

    _write_record_results(
        [relation.to_dict() for relation in relations],
        output=args.output,
        output_format=args.format,
    )
    return 0


def run_mapping(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    # QC-023: empty-string --target-source is a clear shell-scripting bug.
    # The service layer rejects '' (QC-021), but surface a clean CLI message
    # rather than a traceback.
    target_sources = list(args.target_source or [])
    empty_targets = [t for t in target_sources if not t or not t.strip()]
    if empty_targets:
        raise SystemExit(
            "--target-source must be a non-empty vocabulary name "
            f"(got {empty_targets!r}). Use a SAB like ICD10CM or SNOMEDCT_US."
        )
    # QC-032: reject URI/OID-form --target-source (sibling of QC-011/FIX-010
    # for --source). Pre-fix, ``--target-source http://hl7.org/fhir/sid/icd-10-cm``
    # was uppercased to 'HTTP://HL7.ORG/...' and silently returned no matches.
    for target in target_sources:
        if "://" in target or target.lower().startswith("urn:oid:"):
            raise SystemExit(
                f"--target-source expects a UMLS SAB string (e.g. ICD10CM), got "
                f"{target!r} (looks like a URI/OID). FHIR URIs are not accepted "
                f"here; use the SAB form."
            )

    config = local_duckdb_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )
    refs = _code_source_pairs(args.code, args.source)
    # QC-029, generalized by QC-360: shared helper disables the DuckDB
    # terminal progress bar on every stdout-printing command.
    con = _connect_read_only(db_path, output=args.output)
    try:
        engine = LocalDuckDBEngine(con, config=config)
        # QC-022/QC-027: surface input-validation errors as clean CLI messages
        # (exit 1 via SystemExit) rather than raw Python tracebacks.
        try:
            mappings = get_code_mappings(
                refs,
                engine=engine,
                target_sources=target_sources,
                max_results_per_code=args.max_results_per_code,
                max_depth=args.max_depth,
                include_target_ancestors=args.include_target_ancestors,
                include_target_descendants=args.include_target_descendants,
                resolve_mode=args.resolve_mode,
            )
        except (ValueError, TypeError) as exc:
            raise SystemExit(f"Error: {exc}") from exc
    finally:
        con.close()

    _write_record_results(
        [mapping.to_dict() for mapping in mappings],
        output=args.output,
        output_format=args.format,
    )
    return 0


def run_source_stats(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    config = _local_duckdb_config_from_args(args)
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        engine = LocalDuckDBEngine(con, config=config)
        # QC-135/QC-219 pattern: surface input-validation errors as clean
        # CLI messages instead of raw tracebacks.
        try:
            stats = get_source_stats(engine=engine, sources=_normalize_sources_or_exit(args.sources))
        except ValueError as exc:
            raise SystemExit(f"Error: {exc}") from exc
    finally:
        con.close()

    _write_record_results(
        [stat.to_dict() for stat in stats],
        output=args.output,
        output_format=args.format,
    )
    return 0


def run_sample_codes(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    config = _local_duckdb_config_from_args(args)
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        engine = LocalDuckDBEngine(con, config=config)
        # QC-219: run_sample_codes lacked the ValueError→SystemExit wrapper
        # that run_search_names (QC-135) and run_optimize (QC-197) have —
        # ``--per-source 0`` dumped a 12-frame traceback with source paths.
        try:
            codes = sample_source_codes(
                engine=engine,
                sources=_normalize_sources_or_exit(args.sources),
                per_source=args.per_source,
            )
        except ValueError as exc:
            raise SystemExit(f"Error: {exc}") from exc
    finally:
        con.close()

    _write_record_results(
        [{"source": code.source, "code": code.code} for code in codes],
        output=args.output,
        output_format=args.format,
    )
    return 0


def run_code_ttys(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    config = _local_duckdb_config_from_args(args)
    refs = _code_source_pairs(args.code, args.source)
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        engine = LocalDuckDBEngine(con, config=config)
        # QC-135/QC-219 pattern: get_code_ttys now validates empty codes /
        # URI-form sources (QC-222/QC-227), so this handler needs the clean
        # error wrapper too.
        try:
            infos = get_code_ttys(refs, engine=engine)
        except ValueError as exc:
            raise SystemExit(f"Error: {exc}") from exc
    finally:
        con.close()

    _write_record_results(
        [info.to_dict() for info in infos],
        output=args.output,
        output_format=args.format,
    )
    return 0


def run_search_names(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    config = _local_duckdb_config_from_args(args)
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        engine = LocalDuckDBEngine(con, config=config)
        # QC-135: surface input-validation errors (e.g. whitespace query →
        # ValueError from discovery.py) as clean CLI messages instead of raw
        # tracebacks — the QC-022/QC-027 fix pattern, missing on this surface.
        try:
            results = search_names(
                args.query,
                engine=engine,
                sources=args.sources,
                tty_filters=args.tty,
                limit=args.limit,
            )
        except (ValueError, TypeError) as exc:
            raise SystemExit(f"Error: {exc}") from exc
    finally:
        con.close()

    _write_record_results(
        [result.to_dict() for result in results],
        output=args.output,
        output_format=args.format,
    )
    return 0


def run_text_search(args: argparse.Namespace) -> int:
    """Run intelligent text-to-code search (BM25 + SapBERT)."""
    from medterm4ds.services.search import search as search_service

    # Clean CLI errors for input validation (whitespace/over-length query,
    # invalid mode/count) instead of raw tracebacks — QC-022/QC-027 pattern.
    try:
        results = search_service(
            args.query,
            mode=args.mode,
            sources=args.sources,
            count=args.limit,
        )
    except (ValueError, TypeError) as exc:
        raise SystemExit(f"Error: {exc}") from exc
    if getattr(args, "result_types", None):
        # QC-129: the category filter applies to the BM25 `category` field,
        # which only ever holds SEARCH_CATEGORIES values. Warn (not silently
        # filter to empty) when the user passes values outside that set —
        # they are valid canonical result_types but never match legacy modes.
        # CanonicalSearchResult has no `.category` (it has result_type), so
        # the filter only applies to legacy-mode results.
        if args.mode != "canonical":
            unknown = [t for t in args.result_types if t not in SEARCH_CATEGORIES]
            if unknown:
                print(
                    f"Warning: --result-types values {unknown} are not search "
                    f"categories ({', '.join(SEARCH_CATEGORIES)}); they will "
                    "match no results.",
                    file=sys.stderr,
                )
            results = [r for r in results if r.category in args.result_types]
        else:
            wanted = {t for t in args.result_types}
            results = [r for r in results if r.result_type in wanted]
    _write_record_results(
        [r.to_dict() for r in results],
        output=getattr(args, "output", None),
        output_format=getattr(args, "format", "json"),
    )
    return 0


def run_extract(args: argparse.Namespace) -> int:
    """Extract medical concepts from free text."""
    from medterm4ds.services.extraction import extract as extract_service

    # QC-169: --ner-labels/--result-types are single comma-separated strings
    # on the parser; the service accepts str | list — pass a list.
    ner_labels = args.ner_labels.split(",") if args.ner_labels else None
    result_types = args.result_types.split(",") if args.result_types else None
    results = extract_service(
        args.text,
        format=args.format,
        ner_labels=ner_labels,
        result_types=result_types,
        mode=args.mode,
        min_grade=args.min_grade,
        include_negated=args.include_negated,
        include_uncertain=args.include_uncertain,
        include_historical=args.include_historical,
        include_family=args.include_family,
    )
    # annotated format returns a dict with annotated_text + spans, not a list of records
    if args.format == "annotated":
        import json as _json
        if isinstance(results, dict):
            # Convert any ExtractedConcept objects in 'concepts' list to dicts
            output_obj = {}
            for k, v in results.items():
                if k == "concepts" and isinstance(v, list):
                    output_obj[k] = [c.to_dict() if hasattr(c, "to_dict") else c for c in v]
                elif isinstance(v, list):
                    output_obj[k] = [c.to_dict() if hasattr(c, "to_dict") else c for c in v]
                else:
                    output_obj[k] = v
        else:
            output_obj = {"annotated_text": results}
        if args.output:
            # QC-383 (LOW): QC-359's OSError guard was applied to
            # _write_record_results/_write_payload but not this annotated
            # branch — a nonexistent/unwritable --output directory dumped a
            # raw FileNotFoundError traceback AFTER the full NER/ConText
            # extraction had already run. OSError is the narrow type for
            # filesystem write failures.
            try:
                from pathlib import Path as _Path
                _Path(args.output).write_text(_json.dumps(output_obj, indent=2))
            except OSError as exc:
                raise SystemExit(f"Error: cannot write to {args.output}: {exc}") from exc
        else:
            print(_json.dumps(output_obj, indent=2))
        return 0
    _write_record_results(
        [r.to_dict() for r in results],
        output=args.output,
        output_format=args.format_output,
    )
    return 0


def _data_cli_errors(func):
    # QC-445 (MEDIUM): the data family was the only CLI surface with zero
    # error envelope — routine operational failures (missing MRREL, corrupt
    # or nonexistent DB, network down, DB locked by another process) dumped
    # 16-line Python tracebacks from args.func(args). Sibling runners raise
    # SystemExit with a one-line diagnostic. Narrow exception set per
    # GLOBAL_RULES (no except Exception): RuntimeError (service input
    # guards), OSError (URLError/network/disk), duckdb.Error (IO/catalog/
    # binder/lock — QC-458's lock conflicts now surface one line). Value/
    # Type/Attribute errors are programming bugs and propagate.
    @functools.wraps(func)
    def wrapper(args: argparse.Namespace) -> int:
        import duckdb

        try:
            return func(args)
        except (RuntimeError, OSError, duckdb.Error) as exc:
            raise SystemExit(f"Error: {exc}") from exc

    return wrapper


def _data_db_path_or_exit(args: argparse.Namespace) -> Path:
    # QC-452 (MEDIUM): MEDTERM4DS_DB is the documented shared operator
    # contract (honored by apps/mcp.py and apps/fhir_api.py); the data family
    # was the only surface ignoring it. QC-454 (LOW): adopt the same
    # exists() precheck + SystemExit envelope as the 15+ sibling --db
    # consumers instead of a raw duckdb.IOException traceback.
    raw = getattr(args, "db", None) or os.getenv("MEDTERM4DS_DB")
    if not raw:
        raise SystemExit("Error: --db is required (or set MEDTERM4DS_DB).")
    db_path = Path(raw)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")
    return db_path


@_data_cli_errors
def run_data_download(args: argparse.Namespace) -> int:
    path = download_release(
        output_dir=args.output_dir,
        api_key=args.api_key,
        release_type=args.release_type,
        release_version=args.release_version,
        current=args.current,
        extract=args.extract,
    )
    sys.stdout.write(_json_dumps({"downloaded": str(path)}))
    return 0


@_data_cli_errors
def run_data_build_duckdb(args: argparse.Namespace) -> int:
    if not args.db_role:
        print("--db-role is required when building a DuckDB database.", file=sys.stderr)
        return 2
    if Path(args.output_db).name == "umls_local.duckdb":
        print(
            "Refusing ambiguous output DB name 'umls_local.duckdb'. "
            "Use a role/release-specific path such as data/umls_current.duckdb "
            "or data/umls_2025ab.duckdb.",
            file=sys.stderr,
        )
        return 2
    path = build_duckdb_from_rrf(
        rrf_dir=args.rrf_dir,
        output_db=args.output_db,
        replace=args.replace,
        batch_size=args.batch_size,
        db_role=args.db_role,
        release_version=args.release_version,
        source_archive=args.source_archive,
    )
    annotations = annotate_umls_duckdb(
        path,
        db_role=args.db_role,
        release_version=args.release_version,
        source_archive=args.source_archive,
    )
    sys.stdout.write(_json_dumps({"db": str(path), "status": "ok", "annotations": annotations}))
    return 0


@_data_cli_errors
def run_data_prepare_derived(args: argparse.Namespace) -> int:
    db_path = _data_db_path_or_exit(args)
    report = prepare_umls_duckdb(
        db_path,
        replace=args.replace,
        db_role=args.db_role,
        release_version=args.release_version,
        source_archive=args.source_archive,
    )
    errors = (report.get("mt4ds") or {}).get("errors") or []
    sys.stdout.write(
        _json_dumps(
            {
                "db": str(db_path),
                "status": "ok" if not errors else "error",
                "derived": report,
            }
        )
    )
    # QC-459 (HIGH): replace-mode prepare on a DB with broken umls.* views
    # previously dropped the mt4ds schema, failed 10 of 14 builders, and
    # still exited 0 with a half-built DB. Builder errors now flip the exit
    # code so operators/CI don't deploy a destroyed DB.
    return 0 if not errors else 1


@_data_cli_errors
def run_data_verify(args: argparse.Namespace) -> int:
    db_path = _data_db_path_or_exit(args)
    # QC-452 (MEDIUM): verify previously declared and accepted all five
    # common engine args but ignored every one. Honor the profile-mapped
    # memory/threads/temp-dir knobs on the verification connection.
    # (--query-chunk-size has no effect here: verify runs no chunked
    # engine queries.)
    config = local_duckdb_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
    )
    report = verify_duckdb(
        db_path,
        sources=_normalize_sources_or_exit(args.sources),
        memory_limit=config.memory_limit,
        threads=config.threads,
        temp_directory=config.temp_directory,
    )
    sys.stdout.write(_json_dumps(report))
    # QC-451 (MEDIUM): machine-readable verdict + nonzero exit. Pre-fix,
    # verify always exited 0, so `data verify --db broken.duckdb && deploy`
    # was a no-op gate even for a DB missing required tables or with zero
    # codes in every requested source.
    return 0 if report.get("ok") else 1


def _run_bulk_record_export(
    args: argparse.Namespace,
    *,
    sources: tuple[str, ...],
    cache_sources: tuple[str, ...],
    command: str,
    row_factory,
    allow_resume: bool,
    metadata: dict[str, object] | None = None,
) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    if args.resume and not allow_resume:
        raise SystemExit(
            f"{command} can write multiple rows per code, so append/resume is not supported."
        )

    # QC-377 (MEDIUM, CR-015 gap): validate max_depth BEFORE opening the DB
    # or creating output artifacts. Pre-fix, ``bulk map|bulk hierarchy|bulk
    # patient-friendly --max-depth -5`` dumped a raw ValueError traceback
    # from the service layer and left a 0-byte output file plus checkpoint
    # sidecar behind. run_patient_friendly_conceptmap /
    # run_mapping_conceptmap already validated (cli.py:842/961); the four
    # run_bulk_* wrappers all route through here.
    _validate_cli_max_depth(args)

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    output_path = Path(args.output)
    output_format = args.format or _bulk_record_format_from_path(output_path)
    checkpoint_path = Path(args.checkpoint) if args.checkpoint else default_checkpoint_path(output_path)
    config = _local_duckdb_config_from_args(args)

    resume_position = (
        read_output_position(output_path, output_format)
        if args.resume
        else OutputPosition()
    )
    remaining_limit = None
    if args.limit is not None:
        remaining_limit = max(args.limit - resume_position.rows, 0)

    progress = _Progress(enabled=args.progress, initial_rows=resume_position.rows)
    con = _connect_read_only(db_path, output=getattr(args, "output", None))
    try:
        if args.progress:
            counts = count_source_codes(con, sources)
            total = sum(counts.values())
            if args.limit is not None:
                total = min(total, args.limit)
            remaining = max(total - resume_position.rows, 0)
            progress.print(f"sources={dict(sorted(counts.items()))} total={total:,} remaining={remaining:,}")
            if resume_position.has_rows:
                progress.print(
                    "resuming after "
                    f"{resume_position.last_code.source}:{resume_position.last_code.code} "
                    f"({resume_position.rows:,} existing rows)"
                )

        engine = LocalDuckDBEngine(
            con,
            config=config,
            progress=progress.print if args.progress else None,
        )
        if not args.no_prepare_cache:
            start = time.perf_counter()
            engine.prepare_cache(cache_sources, create_indexes=args.cache_indexes)
            progress.print(f"prepared cache in {time.perf_counter() - start:.2f}s")

        codes = iter_source_codes(
            con,
            sources,
            fetch_size=args.fetch_size,
            limit=remaining_limit,
            resume_after=resume_position.last_code if resume_position.has_rows else None,
        )
        rows = row_factory(codes, engine)
        checkpoint_metadata = {
            "command": command,
            "db": str(db_path),
            "sources": list(sources),
            "memory_profile": args.memory_profile,
            "limit": args.limit,
            **(metadata or {}),
        }
        final_position = write_checkpointed_rows(
            rows,
            output_path,
            output_format=output_format,
            checkpoint_path=checkpoint_path,
            append=args.resume and output_path.exists(),
            checkpoint_every=args.checkpoint_every,
            initial_position=resume_position,
            metadata=checkpoint_metadata,
            on_row=progress.record_row,
        )
    finally:
        con.close()

    progress.print(f"wrote {final_position.rows:,} rows to {output_path}")
    progress.print(f"checkpoint {checkpoint_path}")
    return 0


def _local_duckdb_config_from_args(args: argparse.Namespace):
    return local_duckdb_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )


def _format_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "fhir-json"
    raise SystemExit("Could not infer output format. Use --format jsonl, --format csv, or --format fhir-json.")


def _connect_read_only(db_path: Path, *, output: str | None = None):
    """Open a read-only DuckDB connection for a query command.

    QC-029/QC-360 (LOW): DuckDB 1.5+ auto-enables a progress bar for long
    queries that writes directly to the terminal fd (bypassing
    sys.stdout/sys.stderr), corrupting piped JSON when the caller pipes
    stdout to a parser. Every command that prints to stdout without
    --output disables it here; the file path is unaffected.
    """
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    if not output:
        con.execute("SET enable_progress_bar = false")
    return con


def _record_format_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".csv":
        return "csv"
    if suffix == ".txt":
        return "table"
    raise SystemExit("Could not infer output format. Use --format json, jsonl, or csv.")


def _bulk_record_format_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".csv":
        return "csv"
    raise SystemExit("Could not infer output format. Use --format jsonl or csv.")


def _code_source_pairs(codes: list[str], sources: list[str]) -> list[tuple[str, str]]:
    """Return ``(source, code)`` tuples matching CodeRef.from_pair convention.

    Was ``zip(codes, sources)`` (returning ``(code, source)``) under the
    legacy medterm tuple convention; flipped to ``(source, code)`` in
    v0.0.1 to match CodeRef's field order and the Terminology facade.
    """
    # Reject URI/OID-form source inputs early with a clear message. The CLI
    # uses UMLS SAB strings (e.g. SNOMEDCT_US), not FHIR URIs (e.g.
    # http://snomed.info/sct). Pre-fix, ``--source http://snomed.info/sct``
    # was uppercased to 'HTTP://SNOMED.INFO/SCT' and silently returned a
    # null-valued row. Found by QC-011 (CROSS_SURFACE MEDIUM).
    for source in sources:
        # QC-378 (MEDIUM): sibling of the QC-324 empty---code guard below.
        # Pre-fix, ``--source ''`` exited 0 with a null-valued record —
        # success-shaped output for a request that was never validly
        # invoked (7th PROMOTED pattern). The FHIR surface 422s on empty
        # system; the MCP surface rejects via _validate_source_sab (QC-389).
        if not source.strip():
            raise SystemExit(
                "--source must be a non-empty vocabulary name "
                f"(e.g. SNOMEDCT_US), got {source!r}."
            )
        if "://" in source or source.lower().startswith("urn:oid:"):
            raise SystemExit(
                f"--source expects a UMLS SAB string (e.g. SNOMEDCT_US), got "
                f"{source!r} (looks like a URI/OID). FHIR URIs are not accepted "
                f"here; use the SAB form."
            )
    # QC-324 (LOW): an empty --code is never a valid invocation (7th PROMOTED
    # pattern rationale; the FHIR surface rejects it with 422). Pre-fix the
    # CLI exited 0 with a null-field record — success-shaped output for a
    # request that was never validly invoked.
    empty_codes = [c for c in codes if not c or not c.strip()]
    if empty_codes:
        raise SystemExit(
            "--code must be a non-empty terminology code "
            f"(got {empty_codes!r})."
        )
    if len(sources) == 1 and len(codes) > 1:
        sources = sources * len(codes)
    if len(sources) != len(codes):
        raise SystemExit("--source must be provided once for all codes or once per --code")
    return list(zip(sources, codes, strict=True))


def _flatten_optimize_rule_row(row: dict[str, object]) -> dict[str, object]:
    """Flatten list-valued optimize rule fields for csv/table output.

    QC-200 (LOW): ``to_dict`` renders exclude/covered_codes as Python lists;
    the csv writer serializes those as the list repr ('[]' / JSON blobs) and
    the table renderer truncates them. Render code lists as ';'-joined
    strings so the optimization evidence survives in record formats.
    """
    flat = dict(row)
    exclude = flat.get("exclude")
    if isinstance(exclude, list):
        flat["exclude"] = ";".join(str(code) for code in exclude)
    for key in ("covered_codes", "excluded_codes"):
        values = flat.get(key)
        if isinstance(values, list):
            flat[key] = ";".join(
                item["code"] if isinstance(item, dict) else str(item)
                for item in values
            )
    return flat


def _write_record_results(
    rows: list[dict[str, object]],
    *,
    output: str | None,
    output_format: str | None,
) -> None:
    resolved_format = output_format or (_record_format_from_path(Path(output)) if output else "json")
    if output:
        output_path = Path(output)
        # QC-359 (LOW): a nonexistent/unwritable --output directory dumped a
        # raw FileNotFoundError traceback AFTER the query had run. Surface a
        # clean SystemExit instead (QC-022/QC-027 guard pattern). OSError is
        # the narrow type for filesystem write failures.
        try:
            if resolved_format == "json":
                output_path.write_text(_json_dumps({"results": rows}), encoding="utf-8")
            elif resolved_format == "jsonl":
                write_jsonl(rows, output_path)
            elif resolved_format == "csv":
                write_csv(rows, output_path)
            elif resolved_format == "table":
                output_path.write_text(render_table(rows) + "\n", encoding="utf-8")
            elif resolved_format == "tree":
                # QC-358/QC-369 (MEDIUM): file exports render the FULL tree —
                # the max_items=20/max_depth=4 defaults are for interactive
                # stdout readability, not for machine-consumed files.
                output_path.write_text(
                    render_tree({"results": rows}, max_items=None, max_depth=None) + "\n",
                    encoding="utf-8",
                )
            else:
                raise SystemExit(f"Unsupported output format: {resolved_format}")
        except OSError as exc:
            raise SystemExit(f"Error: cannot write to {output_path}: {exc}") from exc
    else:
        if resolved_format == "table":
            print(render_table(rows), file=sys.stdout)
        elif resolved_format == "tree":
            print(render_tree({"results": rows}), file=sys.stdout)
        elif resolved_format == "csv":
            # QC-170: --format-output csv without --output previously fell
            # through to JSON on stdout (the csv branch was file-only). Emit
            # CSV rows to stdout so the flag isn't silently ignored.
            _print_csv_records(rows)
        elif resolved_format == "jsonl":
            # Same gap as csv (QC-170 sibling).
            import json as _json_mod
            for row in rows:
                print(_json_mod.dumps(row, sort_keys=True), file=sys.stdout)
        else:
            # QC-357 (LOW): _json_dumps already appends a trailing newline;
            # print() added a second, so stdout ended '}\n\n' while the file
            # path ended '}\n'. Write directly for byte-identical output.
            sys.stdout.write(_json_dumps({"results": rows}))


def _print_csv_records(rows: list[dict[str, object]]) -> None:
    """Print records as CSV to stdout (QC-170 — mirrors write_csv's shape)."""
    import csv as _csv
    import io as _io

    from medterm4ds.outputs.records import _sanitize_csv_record, to_csv_record

    iterator = iter(rows)
    try:
        first = to_csv_record(next(iterator))
    except StopIteration:
        return
    buffer = _io.StringIO()
    writer = _csv.DictWriter(buffer, fieldnames=list(first.keys()), extrasaction="ignore")
    writer.writeheader()
    # QC-353 (MEDIUM): apply the same formula-injection sanitizer as
    # write_csv so stdout and file CSV output are byte-identical.
    writer.writerow(_sanitize_csv_record(first))
    for row in iterator:
        writer.writerow(_sanitize_csv_record(to_csv_record(row)))
    print(buffer.getvalue(), end="", file=sys.stdout)


def _write_payload(
    payload: dict[str, object],
    *,
    output: str | None,
    output_format: str | None,
) -> None:
    resolved_format = output_format or (_record_format_from_path(Path(output)) if output else "json")
    # QC-358: file-exported trees render the full structure (max_items/
    # max_depth None); stdout keeps the compact defaults.
    if resolved_format == "tree":
        text = render_tree(payload, max_items=None, max_depth=None) if output else render_tree(payload)
    elif resolved_format == "table" and isinstance(payload.get("rules"), list):
        text = render_table(payload["rules"])  # type: ignore[arg-type]
    else:
        text = _json_dumps(payload)
    if output:
        # QC-359: clean error on unwritable output paths (see above).
        try:
            Path(output).write_text(text if text.endswith("\n") else f"{text}\n", encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"Error: cannot write to {output}: {exc}") from exc
    else:
        # QC-357: single trailing newline on stdout, matching the file path.
        sys.stdout.write(text if text.endswith("\n") else f"{text}\n")


def _missing_code_info(*, code: str, source: str) -> dict[str, object]:
    # Delegates to CodeInfo — see client._missing_code_info for rationale.
    # This CLI variant takes kwargs instead of a CodeRef because some CLI
    # paths know the source/code before they have a CodeRef in hand.
    from medterm4ds.core.models import CodeInfo
    return CodeInfo(code=CodeRef(source=source, code=code)).to_dict()


def _json_dumps(data: object) -> str:
    import json

    return json.dumps(data, indent=2, sort_keys=True) + "\n"


class _Progress:
    def __init__(self, *, enabled: bool, initial_rows: int = 0):
        self.enabled = enabled
        self.rows = initial_rows
        self.started_at = time.perf_counter()

    def print(self, message: str) -> None:
        if self.enabled:
            print(f"progress: {message}", file=sys.stderr, flush=True)

    def record_row(self, _record: dict[str, object], total_rows: int) -> None:
        self.rows = total_rows
        if self.enabled and self.rows % 10_000 == 0:
            elapsed = max(time.perf_counter() - self.started_at, 0.001)
            rate = (self.rows) / elapsed
            self.print(f"wrote {self.rows:,} rows ({rate:,.1f} rows/s)")

    def count_rows(self, rows):
        for row in rows:
            self.record_row(row.to_dict(), self.rows + 1)
            yield row


if __name__ == "__main__":
    raise SystemExit(main())
