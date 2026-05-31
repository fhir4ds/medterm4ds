"""Command line interface for medterm4ds."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from medterm4ds.core.config import LOCAL_LITE_MEMORY_PROFILES, local_lite_config
from medterm4ds.engines.duckdb import LocalLiteEngine
from medterm4ds.outputs import (
    OutputPosition,
    default_checkpoint_path,
    read_output_position,
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

_HIERARCHY_DIRECTIONS = ("parents", "children", "ancestors", "descendants")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
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

    hierarchy = subparsers.add_parser("hierarchy", help="Traverse code hierarchies.")
    hierarchy_subparsers = hierarchy.add_subparsers(dest="direction", required=True)
    for direction in _HIERARCHY_DIRECTIONS:
        hierarchy_command = hierarchy_subparsers.add_parser(
            direction,
            help=f"Return {direction} for terminology codes.",
        )
        _add_hierarchy_args(hierarchy_command)
        hierarchy_command.set_defaults(func=run_hierarchy)

    return parser


def _add_common_engine_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", required=True, help="Path to the UMLS DuckDB database.")
    parser.add_argument(
        "--memory-profile",
        choices=tuple(sorted(LOCAL_LITE_MEMORY_PROFILES)),
        default="balanced",
        help="Named LocalLite memory profile.",
    )
    parser.add_argument("--memory-limit", default=None, help="Override DuckDB memory limit.")
    parser.add_argument("--temp-dir", default=None, help="DuckDB temporary directory.")
    parser.add_argument("--threads", type=int, default=None, help="Override DuckDB thread count.")
    parser.add_argument(
        "--query-chunk-size",
        type=int,
        default=None,
        help="Override LocalLite internal query chunk size.",
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
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--fetch-size", type=int, default=10_000)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="Optional total code limit.")
    parser.add_argument(
        "--no-prepare-cache",
        action="store_true",
        help="Skip LocalLite temp cache preparation.",
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
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--fetch-size", type=int, default=10_000)
    parser.add_argument("--limit", type=int, default=None, help="Optional total code limit.")
    parser.add_argument(
        "--no-prepare-cache",
        action="store_true",
        help="Skip LocalLite temp cache preparation.",
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
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--fetch-size", type=int, default=10_000)
    parser.add_argument("--limit", type=int, default=None, help="Optional total code limit.")
    parser.add_argument(
        "--no-prepare-cache",
        action="store_true",
        help="Skip LocalLite temp cache preparation.",
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
    parser.add_argument("--max-depth", type=int, default=5)


def _add_lookup_args(parser: argparse.ArgumentParser) -> None:
    _add_common_engine_args(parser)
    parser.add_argument("--source", action="append", required=True, help="Source vocabulary.")
    parser.add_argument("--code", action="append", required=True, help="Code to look up.")
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
    parser.add_argument("--limit", type=int, default=25, help="Maximum results to return.")
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


def run_patient_friendly_conceptmap(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    sources = normalize_sources(args.sources)
    output_path = Path(args.output)
    output_format = args.format or _format_from_path(output_path)
    if args.resume and output_format == "fhir-json":
        raise SystemExit("FHIR JSON output is not resumable. Use JSONL or CSV for resumable bulk exports.")
    checkpoint_path = Path(args.checkpoint) if args.checkpoint else default_checkpoint_path(output_path)
    config = local_lite_config(
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
    con = duckdb.connect(str(db_path), read_only=True)
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

        engine = LocalLiteEngine(
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

    sources = normalize_sources(args.sources)
    target_sources = normalize_sources(args.target_sources)
    output_path = Path(args.output)
    output_format = args.format or _format_from_path(output_path)
    checkpoint_path = default_checkpoint_path(output_path)
    config = local_lite_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )

    progress = _Progress(enabled=args.progress)
    con = duckdb.connect(str(db_path), read_only=True)
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

        engine = LocalLiteEngine(
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
    sources = normalize_sources(args.sources)
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
    sources = normalize_sources(args.sources)
    target_sources = normalize_sources(args.target_sources)
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
    sources = normalize_sources(args.sources)
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
    sources = normalize_sources(args.sources)
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
        metadata={"max_depth": args.max_depth},
    )


def run_lookup(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    config = local_lite_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )
    refs = _code_source_pairs(args.code, args.source)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalLiteEngine(con, config=config)
        infos = get_code_infos(refs, engine=engine)
    finally:
        con.close()

    rows = [
        info.to_dict() if info else _missing_code_info(code=code, source=source)
        for info, (code, source) in zip(infos, refs, strict=True)
    ]
    _write_record_results(rows, output=args.output, output_format=args.format)
    return 0


def run_hierarchy(args: argparse.Namespace) -> int:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    config = local_lite_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )
    refs = _code_source_pairs(args.code, args.source)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalLiteEngine(con, config=config)
        relations = get_code_relations(
            refs,
            engine=engine,
            direction=args.direction,
            max_depth=args.max_depth,
        )
    finally:
        con.close()

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

    config = local_lite_config(
        args.memory_profile,
        memory_limit=args.memory_limit,
        temp_directory=args.temp_dir,
        threads=args.threads,
        query_chunk_size=args.query_chunk_size,
    )
    refs = _code_source_pairs(args.code, args.source)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalLiteEngine(con, config=config)
        mappings = get_code_mappings(
            refs,
            engine=engine,
            target_sources=args.target_source,
            max_results_per_code=args.max_results_per_code,
            max_depth=args.max_depth,
            include_target_ancestors=args.include_target_ancestors,
            include_target_descendants=args.include_target_descendants,
        )
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

    config = _local_lite_config_from_args(args)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalLiteEngine(con, config=config)
        stats = get_source_stats(engine=engine, sources=args.sources)
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

    config = _local_lite_config_from_args(args)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalLiteEngine(con, config=config)
        codes = sample_source_codes(
            engine=engine,
            sources=args.sources,
            per_source=args.per_source,
        )
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

    config = _local_lite_config_from_args(args)
    refs = _code_source_pairs(args.code, args.source)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalLiteEngine(con, config=config)
        infos = get_code_ttys(refs, engine=engine)
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

    config = _local_lite_config_from_args(args)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalLiteEngine(con, config=config)
        results = search_names(
            args.query,
            engine=engine,
            sources=args.sources,
            tty_filters=args.tty,
            limit=args.limit,
        )
    finally:
        con.close()

    _write_record_results(
        [result.to_dict() for result in results],
        output=args.output,
        output_format=args.format,
    )
    return 0


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

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    output_path = Path(args.output)
    output_format = args.format or _bulk_record_format_from_path(output_path)
    checkpoint_path = Path(args.checkpoint) if args.checkpoint else default_checkpoint_path(output_path)
    config = _local_lite_config_from_args(args)

    resume_position = (
        read_output_position(output_path, output_format)
        if args.resume
        else OutputPosition()
    )
    remaining_limit = None
    if args.limit is not None:
        remaining_limit = max(args.limit - resume_position.rows, 0)

    progress = _Progress(enabled=args.progress, initial_rows=resume_position.rows)
    con = duckdb.connect(str(db_path), read_only=True)
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

        engine = LocalLiteEngine(
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


def _local_lite_config_from_args(args: argparse.Namespace):
    return local_lite_config(
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


def _record_format_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".csv":
        return "csv"
    raise SystemExit("Could not infer output format. Use --format json, jsonl, or csv.")


def _bulk_record_format_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".csv":
        return "csv"
    raise SystemExit("Could not infer output format. Use --format jsonl or csv.")


def _code_source_pairs(codes: list[str], sources: list[str]) -> list[tuple[str, str]]:
    if len(sources) == 1 and len(codes) > 1:
        sources = sources * len(codes)
    if len(sources) != len(codes):
        raise SystemExit("--source must be provided once for all codes or once per --code")
    return list(zip(codes, sources, strict=True))


def _write_record_results(
    rows: list[dict[str, object]],
    *,
    output: str | None,
    output_format: str | None,
) -> None:
    resolved_format = output_format or (_record_format_from_path(Path(output)) if output else "json")
    if output:
        output_path = Path(output)
        if resolved_format == "json":
            output_path.write_text(_json_dumps({"results": rows}), encoding="utf-8")
        elif resolved_format == "jsonl":
            write_jsonl(rows, output_path)
        elif resolved_format == "csv":
            write_csv(rows, output_path)
        else:
            raise SystemExit(f"Unsupported output format: {resolved_format}")
    else:
        print(_json_dumps({"results": rows}), file=sys.stdout)


def _missing_code_info(*, code: str, source: str) -> dict[str, object]:
    from medterm4ds.core.models import CodeRef

    ref = CodeRef(source=source, code=code)
    return {
        "source": ref.source,
        "code": ref.code,
        "name": None,
        "cui": None,
        "aui": None,
        "tty": None,
        "suppress": None,
    }


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
