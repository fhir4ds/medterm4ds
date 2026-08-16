"""Notebook-friendly terminology client."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from medterm4ds.core.config import MemoryProfile, local_duckdb_config
from medterm4ds.core.env import env_int, env_str
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.api import RemoteApiEngine
from medterm4ds.engines.api.engine import DEFAULT_REMOTE_TIMEOUT
from medterm4ds.engines.base import TerminologyEngine
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.outputs import to_dataframe
from medterm4ds.services.conceptmap import get_concept_map, get_mapping_concept_map
from medterm4ds.services.discovery import (
    get_code_ttys,
    get_source_stats,
    sample_source_codes,
    search_names,
)
from medterm4ds.services.hierarchy import (
    get_ancestors,
    get_children,
    get_code_relations,
    get_descendants,
    get_parents,
)
from medterm4ds.services.inventory import DEFAULT_INVENTORY_SOURCES
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.mapping import get_code_mappings
from medterm4ds.services.optimize import optimize_codes
from medterm4ds.services.patient_friendly import get_patient_friendly_names
from medterm4ds.services.resolution import resolve_codes

CodeInput = CodeRef | tuple[str, str]
CodeArg = CodeInput | Sequence[CodeInput]
CodeValueArg = str | Sequence[str]


class Terminology:
    """Convenience facade for notebook and application workflows.

    The facade delegates to the same service functions used by the CLI, API,
    and MCP interfaces. Tuple code inputs use the canonical ``(source, code)``
    order — same as ``CodeRef(source, code)`` constructor, same as FHIR
    Coding ``{system, code}``. ``CodeRef.from_pair`` and ``as_pair`` use the
    same order; the legacy ``(code, source)`` tuple convention was removed
    in v0.0.1 to eliminate the silent source/code swap footgun.

    Thread safety (QC-409): a Terminology instance is SINGLE-THREADED. The
    underlying DuckDB Python connection is not thread-safe under concurrent
    use — sharing one instance across threads can corrupt the DuckDB heap and
    abort the whole process (glibc ``malloc(): unsorted double linked list
    corrupted``), or return silently empty results. Concurrent access must
    go through per-thread connections (``mt.connect()`` per thread) or a
    single-worker executor, the pattern the bundled MCP and FHIR servers use
    (``apps/_asyncutil.run_db``).
    """

    def __init__(self, engine: TerminologyEngine, *, connection: Any | None = None):
        self.engine = engine
        self._connection = connection

    def close(self) -> None:
        """Close the underlying connection when this client owns one."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Terminology:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def lookup(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        resolve_mode: str = "active_only",
    ):
        """Look up one code or a batch of codes."""
        codes, single = _code_refs_from_args(source_or_codes, code)
        rows = get_code_infos(codes, engine=self.engine, resolve_mode=resolve_mode)
        return rows[0] if single else rows

    def lookup_df(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        resolve_mode: str = "active_only",
        backend: str = "pandas",
    ):
        """Look up codes and return a pandas or Polars DataFrame."""
        codes, _single = _code_refs_from_args(source_or_codes, code)
        rows = get_code_infos(codes, engine=self.engine, resolve_mode=resolve_mode)
        records = [
            row.to_dict() if row is not None else _missing_code_info(ref)
            for ref, row in zip(codes, rows, strict=True)
        ]
        if not records:
            # Empty input must still produce a DataFrame with the canonical
            # 7-column schema so downstream code (df['name'], df.name.notna(),
            # etc.) works uniformly regardless of batch size. Found by QC-004
            # (EDGE_CASE LOW). Single source of truth: CodeInfo.to_dict keys.
            return _empty_codeinfo_frame(backend)
        return to_dataframe(records, backend=backend)

    def patient_friendly(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        max_depth: int = 5,
        resolve_mode: str = "active_only",
    ):
        """Resolve patient-friendly names for one code or a batch of codes."""
        codes, single = _code_refs_from_args(source_or_codes, code)
        rows = get_patient_friendly_names(
            codes,
            engine=self.engine,
            max_depth=max_depth,
            resolve_mode=resolve_mode,
        )
        return rows[0] if single else rows

    def patient_friendly_df(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        max_depth: int = 5,
        resolve_mode: str = "active_only",
        backend: str = "pandas",
    ):
        """Resolve patient-friendly names and return a DataFrame."""
        rows = _as_dicts(
            get_patient_friendly_names(
                _code_refs_from_args(source_or_codes, code)[0],
                engine=self.engine,
                max_depth=max_depth,
                resolve_mode=resolve_mode,
            )
        )
        if not rows:
            return _empty_friendly_frame(backend)
        return to_dataframe(rows, backend=backend)

    def map(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        target_sources: Sequence[str],
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
        resolve_mode: str = "active_only",
    ):
        """Map codes to one or more target vocabularies."""
        return get_code_mappings(
            _code_refs_from_args(source_or_codes, code)[0],
            engine=self.engine,
            target_sources=target_sources,
            max_results_per_code=max_results_per_code,
            max_depth=max_depth,
            include_target_ancestors=include_target_ancestors,
            include_target_descendants=include_target_descendants,
            resolve_mode=resolve_mode,
        )

    def map_df(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        target_sources: Sequence[str],
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
        resolve_mode: str = "active_only",
        backend: str = "pandas",
    ):
        """Map codes and return a DataFrame."""
        records = _as_dicts(
            self.map(
                source_or_codes,
                code,
                target_sources=target_sources,
                max_results_per_code=max_results_per_code,
                max_depth=max_depth,
                include_target_ancestors=include_target_ancestors,
                include_target_descendants=include_target_descendants,
                resolve_mode=resolve_mode,
            )
        )
        if not records:
            return _empty_codemapping_frame(backend)
        return to_dataframe(records, backend=backend)

    def hierarchy(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        direction: str,
        # QC-426 (MEDIUM): was 1 — the facade returned 1/31 of the ancestors
        # every other surface (CLI --max-depth, MCP code_relations, and the
        # facade's own ancestors() convenience) returns for the same call.
        max_depth: int = 5,
        # QC-483/QC-494: optional row cap, mirroring the bounded walks the
        # CLI/MCP/FHIR surfaces already offer. Critical for the remote engine
        # where an unbounded expansion can exceed the 50MiB response cap.
        limit: int | None = None,
    ):
        """Traverse parents, children, ancestors, or descendants."""
        return get_code_relations(
            _code_refs_from_args(source_or_codes, code)[0],
            engine=self.engine,
            direction=direction,
            max_depth=max_depth,
            limit=limit,
        )

    def hierarchy_df(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        direction: str,
        max_depth: int = 1,
        limit: int | None = None,
        backend: str = "pandas",
    ):
        """Traverse hierarchy and return a DataFrame."""
        # QC-045: an empty result (no rows, e.g. bogus code) must still
        # produce a DataFrame with the canonical 15-column CodeRelation
        # schema so downstream ``df['target_code']`` doesn't KeyError.
        # Sibling of EC-01 FIX-007 / EC-02 FIX-006.
        records = _as_dicts(
            self.hierarchy(
                source_or_codes,
                code,
                direction=direction,
                max_depth=max_depth,
                limit=limit,
            )
        )
        if not records:
            return _empty_coderelation_frame(backend)
        return to_dataframe(records, backend=backend)

    def parents(self, source_or_codes: str | CodeArg, code: CodeValueArg | None = None):
        """Return direct parent relationships."""
        return get_parents(_code_refs_from_args(source_or_codes, code)[0], engine=self.engine)

    def children(self, source_or_codes: str | CodeArg, code: CodeValueArg | None = None):
        """Return direct child relationships."""
        return get_children(_code_refs_from_args(source_or_codes, code)[0], engine=self.engine)

    def ancestors(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        max_depth: int = 5,
    ):
        """Return ancestor relationships."""
        return get_ancestors(
            _code_refs_from_args(source_or_codes, code)[0],
            engine=self.engine,
            max_depth=max_depth,
        )

    def descendants(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        max_depth: int = 5,
        # QC-483 (LOW): the facade could not bound a descendant expansion
        # (TypeError: unexpected keyword argument 'limit') while CLI/MCP/FHIR
        # all offer bounded walks — QC-494 showed the unbounded form breaks
        # the remote engine's 50MiB response cap (SNOMED root depth 5 =
        # 75.4MiB).
        limit: int | None = None,
    ):
        """Return descendant relationships."""
        return get_descendants(
            _code_refs_from_args(source_or_codes, code)[0],
            engine=self.engine,
            max_depth=max_depth,
            limit=limit,
        )

    def resolve(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        resolve_mode: str = "historical",
    ):
        """Resolve active, obsolete, historical, and NDC inputs.

        ``resolve_mode`` mirrors the lookup surface and
        ``effective_code_refs`` semantics: ``active_only`` skips historical
        resolution for non-NDC inputs (fast path), ``historical`` returns the
        full resolution rows (input atom + replacement candidates),
        ``resolve_current`` returns the resolved active replacement. Default
        ``historical`` preserves prior single-call ``resolve()`` behavior.
        """
        from medterm4ds.services.resolution import effective_code_refs
        codes, single = _code_refs_from_args(source_or_codes, code)
        _effective, rows = effective_code_refs(
            codes, engine=self.engine, resolve_mode=resolve_mode
        )
        if rows is None:
            # active_only fast-path for non-NDC inputs: still return
            # CodeResolution objects so the surface stays shape-stable. For
            # active codes, resolve_codes returns active_exact without
            # invoking the replacement search; non-active codes fall through
            # to the historical path (consistent with the active-only
            # lookup behavior).
            rows = resolve_codes(_effective, engine=self.engine)
        return rows[0] if single else rows

    def expand_url(self, url: str, *, count: int = 1000) -> list[CodeRef]:
        """Expand a FHIR fhir_vs URL to a list of descendant codes.

        Supports SNOMED intensional URLs:
            ``http://snomed.info/sct/73211009?fhir_vs=isa``

        Returns the flat code list (root + descendants, up to ``count``).
        Same BFS + depth cap as the HTTP ``$expand`` endpoint.

        Example::

            terms = mt.connect()
            codes = terms.expand_url("http://snomed.info/sct/73211009?fhir_vs=isa")
            # → [CodeRef(source='SNOMEDCT_US', code='73211009'), ...]
        """
        from medterm4ds.apps.fhir_api import expand_url_pattern
        from medterm4ds.engines.fhir import fhir_uri_to_system

        payload = expand_url_pattern(self.engine, url, count=count)
        contains = payload.get("expansion", {}).get("contains", [])
        result: list[CodeRef] = []
        for c in contains:
            source = fhir_uri_to_system(c.get("system", ""))
            if source:
                result.append(CodeRef(source=source, code=str(c.get("code", ""))))
        return result

    def expand_intensional(
        self, value_set: dict, *, count: int = 1000,
    ) -> list[CodeRef]:
        """Expand a ValueSet with compose.include/exclude rules.

        Supports explicit concept lists and ``is-a`` / ``descendent-of``
        intensional filters (via BFS, bounded by ``FHIR_VS_MAX_DEPTH``).

        Example::

            terms = mt.connect()
            codes = terms.expand_intensional({
                "resourceType": "ValueSet",
                "compose": {
                    "include": [{
                        "system": "http://snomed.info/sct",
                        "filter": [{"property": "concept", "op": "is-a", "value": "73211009"}],
                    }],
                },
            })
        """
        from medterm4ds.apps.fhir_api import expand_intensional_value_set
        from medterm4ds.engines.fhir import fhir_uri_to_system

        contains, _ = expand_intensional_value_set(self.engine, value_set, count)
        contains = contains[:count]
        result: list[CodeRef] = []
        for c in contains:
            source = fhir_uri_to_system(c.get("system", ""))
            if source:
                result.append(CodeRef(source=source, code=str(c.get("code", ""))))
        return result

    def resolve_df(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        resolve_mode: str = "historical",
        backend: str = "pandas",
    ):
        """Resolve codes and return a DataFrame."""
        rows = self.resolve(source_or_codes, code, resolve_mode=resolve_mode)
        if not rows:
            return _empty_resolution_frame(backend)
        records = [rows] if not isinstance(rows, list) else rows
        return to_dataframe(_as_dicts(records), backend=backend)

    def optimize(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        source: str | None = None,
        relationship: str | None = None,
        output_format: str = "compact",
        include_codes: bool = False,
    ):
        """Compact code lists into hierarchy include/exclude rules."""
        return optimize_codes(
            _code_refs_from_args(source_or_codes, code)[0],
            engine=self.engine,
            source=source,
            relationship=relationship,
            output_format=output_format,
            include_codes=include_codes,
        )

    def search(
        self,
        query: str,
        *,
        sources: Sequence[str] | str | None = None,
        tty_filters: Sequence[str] | str | None = None,
        limit: int = 25,
        mode: str | None = None,
    ):
        """Search active terminology names.

        If ``mode`` is specified ('lexical', 'semantic', or 'hybrid'), uses
        the intelligent text-to-code search service (BM25 + SapBERT).
        If ``mode`` is None (default), uses the legacy LIKE-based search.
        """
        if mode:
            from medterm4ds.services.search import search as search_service
            src_list = [sources] if isinstance(sources, str) else list(sources) if sources else None
            # QC-400: pass the engine so result displays are canonicalized to
            # the engine preferred term — same convention FHIR $search emits.
            return search_service(query, mode=mode, sources=src_list, count=limit, engine=self.engine)
        return search_names(
            query,
            engine=self.engine,
            sources=sources,
            tty_filters=tty_filters,
            limit=limit,
        )

    def search_df(
        self,
        query: str,
        *,
        sources: Sequence[str] | str | None = None,
        tty_filters: Sequence[str] | str | None = None,
        limit: int = 25,
        backend: str = "pandas",
    ):
        """Search names and return a DataFrame."""
        rows = self.search(
            query,
            sources=sources,
            tty_filters=tty_filters,
            limit=limit,
        )
        if not rows:
            return _empty_name_search_frame(backend)
        return to_dataframe(_as_dicts(rows), backend=backend)

    def source_stats(
        self,
        sources: Sequence[str] | str | None = None,
    ):
        """Return vocabulary inventory counts."""
        return get_source_stats(engine=self.engine, sources=sources)

    def source_stats_df(
        self,
        sources: Sequence[str] | str | None = None,
        *,
        backend: str = "pandas",
    ):
        """Return vocabulary inventory counts as a DataFrame."""
        return to_dataframe(_as_dicts(self.source_stats(sources)), backend=backend)

    def sample_codes(
        self,
        sources: Sequence[str] | str | None = None,
        *,
        per_source: int = 10,
    ):
        """Sample active codes from one or more sources."""
        return sample_source_codes(engine=self.engine, sources=sources, per_source=per_source)

    def sample_codes_df(
        self,
        sources: Sequence[str] | str | None = None,
        *,
        per_source: int = 10,
        backend: str = "pandas",
    ):
        """Sample active codes as a DataFrame."""
        return to_dataframe(
            [{"source": row.source, "code": row.code} for row in self.sample_codes(sources, per_source=per_source)],
            backend=backend,
        )

    def code_ttys(self, source_or_codes: str | CodeArg, code: CodeValueArg | None = None):
        """Return active atoms and TTYs for codes."""
        return get_code_ttys(_code_refs_from_args(source_or_codes, code)[0], engine=self.engine)

    def code_ttys_df(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        backend: str = "pandas",
    ):
        """Return active atoms and TTYs as a DataFrame."""
        rows = self.code_ttys(source_or_codes, code)
        if not rows:
            return _empty_codeinfo_frame(backend)
        return to_dataframe(_as_dicts(rows), backend=backend)

    def conceptmap(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        batch_size: int = 5000,
        max_depth: int = 5,
        target_source: str = "PATIENT_FRIENDLY",
    ):
        """Build patient-friendly ConceptMap rows."""
        return get_concept_map(
            _code_refs_from_args(source_or_codes, code)[0],
            engine=self.engine,
            batch_size=batch_size,
            max_depth=max_depth,
            target_source=target_source,
        )

    def conceptmap_df(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        batch_size: int = 5000,
        max_depth: int = 5,
        target_source: str = "PATIENT_FRIENDLY",
        backend: str = "pandas",
    ):
        """Build patient-friendly ConceptMap rows as a DataFrame."""
        rows = _as_dicts(
            self.conceptmap(
                source_or_codes,
                code,
                batch_size=batch_size,
                max_depth=max_depth,
                target_source=target_source,
            )
        )
        if not rows:
            return _empty_conceptmap_frame(backend)
        return to_dataframe(rows, backend=backend)

    def mapping_conceptmap(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        target_sources: Sequence[str],
        batch_size: int = 5000,
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
    ):
        """Build source-to-target ConceptMap rows."""
        return get_mapping_concept_map(
            _code_refs_from_args(source_or_codes, code)[0],
            engine=self.engine,
            target_sources=tuple(target_sources),
            batch_size=batch_size,
            max_results_per_code=max_results_per_code,
            max_depth=max_depth,
            include_target_ancestors=include_target_ancestors,
            include_target_descendants=include_target_descendants,
        )

    def mapping_conceptmap_df(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        target_sources: Sequence[str],
        batch_size: int = 5000,
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
        backend: str = "pandas",
    ):
        """Build source-to-target ConceptMap rows as a DataFrame."""
        rows = _as_dicts(
            self.mapping_conceptmap(
                source_or_codes,
                code,
                target_sources=target_sources,
                batch_size=batch_size,
                max_results_per_code=max_results_per_code,
                max_depth=max_depth,
                include_target_ancestors=include_target_ancestors,
                include_target_descendants=include_target_descendants,
            )
        )
        if not rows:
            return _empty_conceptmap_frame(backend)
        return to_dataframe(rows, backend=backend)


def open_duckdb_engine(
    db_path: str | Path,
    *,
    read_only: bool = True,
    config: Any | None = None,
    progress: Any | None = None,
) -> tuple[Any, Any]:
    """Open a DuckDB connection + LocalDuckDBEngine pair.

    Shared by ``connect()`` (and therefore mt.connect, the CLI, MCP, API,
    FHIR server) so the connection contract — read_only handling, config
    validation, future pool wrappers — lives in one place. Callers own the
    connection lifecycle (CLI uses try/finally; library users go through
    Terminology which owns the connection).

    Auto-provisioning is NOT done here — callers must pass an existing
    db_path. Use mt.connect() (which calls this helper internally) for
    auto-provisioning.

    Args:
        db_path: Path to a pre-built UMLS DuckDB file.
        read_only: Open the connection read-only (default True).
        config: A LocalDuckDBConfig from local_duckdb_config(). If None,
            uses the 'balanced' memory profile defaults.
        progress: Optional progress callback for prepare_cache reporting.

    Returns:
        (con, engine) — caller is responsible for con.close() on cleanup.
    """
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB is required. Install medterm4ds[duckdb].") from exc

    from medterm4ds.core.config import local_duckdb_config
    from medterm4ds.engines.duckdb import LocalDuckDBEngine

    # QC-468 (LOW): duckdb.connect() runs in create mode, so a typo'd
    # db_path silently materialized a 12KB junk DB and a URI-style suffix
    # ('?mode=ro') opened a LITERAL filename honoring none of the requested
    # semantics — the same fingerprint EC-20 catalogued on the data-setup
    # paths. connect() is a read/connect API; fail fast and never create
    # files from it (mirrors data_setup._require_existing_db /
    # _reject_connection_string_path).
    path = Path(db_path)
    if "?" in path.name:
        raise RuntimeError(
            f"db_path must be a plain filesystem path, not a DuckDB "
            f"connection string (found '?' in {str(path)!r}). Configure "
            f"read-only mode, threads, or memory via the connect() keyword "
            f"arguments or the MEDTERM4DS_* environment variables instead."
        )
    if not path.exists():
        raise RuntimeError(
            f"Database not found: {path}. mt.connect() opens an existing "
            f"database; build one with `medterm4ds data build-duckdb` or "
            f"call mt.connect() without a db_path to auto-provision."
        )

    if config is None:
        config = local_duckdb_config("balanced")
    con = duckdb.connect(str(path), read_only=read_only)
    engine = LocalDuckDBEngine(con, config=config, progress=progress)
    return con, engine


def connect(
    db_path: str | Path | None = None,
    *,
    memory_profile: MemoryProfile | None = None,
    memory_limit: str | None = None,
    temp_directory: str | Path | None = None,
    threads: int | None = None,
    query_chunk_size: int | None = None,
    read_only: bool = True,
    prepare_cache: bool = False,
    cache_sources: Sequence[str] | None = None,
    cache_indexes: bool = False,
    # Auto-provisioning (only used when db_path is None):
    umls_api_key: str | None = None,
    version: str = "2026AA",
    cache_dir: str | Path | None = None,
    offline: bool | None = None,
) -> Terminology:
    """Connect to a local medterm4ds DuckDB database.

    Two modes:

    **Direct mode** (existing): pass a ``db_path`` to open a specific
    DuckDB file.

        terms = mt.connect("/path/to/umls.duckdb")

    **Auto-provisioning mode** (new): omit ``db_path`` to trigger
    one-time setup. Builds ``lookup.duckdb`` from the user's UMLS RRF
    download (~8 min first run), downloads derived search artifacts
    from Hugging Face (~2 min), caches in ``~/.medterm4ds/``, and
    returns a working Terminology instance.

        terms = mt.connect()  # auto-provisions on first call

    The cache is shared across all Python projects on the machine.
    Subsequent ``connect()`` calls are instant (cache hit).

    Engine configuration (QC-464): unset engine knobs fall back to the
    documented ``MEDTERM4DS_MEMORY_PROFILE`` / ``MEDTERM4DS_MEMORY_LIMIT`` /
    ``MEDTERM4DS_TEMP_DIR`` / ``MEDTERM4DS_THREADS`` /
    ``MEDTERM4DS_QUERY_CHUNK_SIZE`` environment variables — the same
    contract the api/mcp/fhir servers honor — and finally to the 'fast'
    profile defaults. Explicit arguments always win over the environment.

    Args:
        db_path: Path to a pre-built UMLS DuckDB file. If None, auto-provisions.
        umls_api_key: NLM UTS API key (reads UMLS_API_KEY env var if not passed).
            Only needed on first run (to build lookup.duckdb from UMLS RRF).
        version: UMLS release tag (default ``2026AA``).
        cache_dir: Override cache root (default ``~/.medterm4ds/``).
        offline: Skip all network calls. Use existing cache only.
            Defaults to ``MEDTERM4DS_OFFLINE`` env var if set.
        prepare_cache: Prepare the low-memory temp tables at connect time.
            The prepared scope is ``DEFAULT_INVENTORY_SOURCES`` (9 sources,
            including ATC) — the same scope the CLI/api/mcp/fhir servers
            use — so a prepared Python engine answers ATC lookups the same
            way every other surface does (QC-469).
        cache_indexes: Create temp indexes during prepare_cache. Defaults
            to False, matching the server surfaces; index creation adds
            ~28s and ~0.8GB to a production prepare for little gain on
            read-mostly workloads (QC-470).

    Returns:
        A ``Terminology`` instance — the same facade used by CLI, MCP,
        and FHIR server.
    """
    try:
        import duckdb  # noqa: F401 — used to surface install error early
    except ImportError as exc:
        raise RuntimeError("DuckDB is required. Install medterm4ds[duckdb].") from exc

    # QC-464 (MEDIUM): honor the documented engine env vars as fallback
    # defaults when the corresponding argument was not explicitly passed
    # (pre-fix the Python surface silently ignored all of them while the
    # three servers honored them — one operator environment produced four
    # different engine budgets). ``is not None`` guards keep explicitly
    # passed falsy values (e.g. memory_limit='') loud: local_duckdb_config
    # rejects them (QC-465).
    if memory_profile is None:
        memory_profile = env_str("MEDTERM4DS_MEMORY_PROFILE", "fast")
    if memory_limit is None:
        memory_limit = env_str("MEDTERM4DS_MEMORY_LIMIT")
    if temp_directory is None:
        temp_directory = env_str("MEDTERM4DS_TEMP_DIR")
    if threads is None:
        threads = env_int("MEDTERM4DS_THREADS", minimum=1)
    if query_chunk_size is None:
        query_chunk_size = env_int("MEDTERM4DS_QUERY_CHUNK_SIZE", minimum=1)

    # Auto-provisioning: build/download if no db_path given.
    if db_path is None:
        from medterm4ds.core.provision import provision

        resolved_offline = offline if offline is not None else bool(
            os.getenv("MEDTERM4DS_OFFLINE")
        )
        db_path = provision(
            version=version,
            umls_api_key=umls_api_key,
            cache_home=Path(cache_dir) if cache_dir else None,
            memory_profile=memory_profile,
            offline=resolved_offline,
        )

    config = local_duckdb_config(
        memory_profile,
        memory_limit=memory_limit,
        temp_directory=temp_directory,
        threads=threads,
        query_chunk_size=query_chunk_size,
    )
    con, engine = open_duckdb_engine(db_path, read_only=read_only, config=config)
    if prepare_cache:
        # QC-469 (HIGH): use the 9-source DEFAULT_INVENTORY_SOURCES (with
        # ATC), not the engine's 8-source default. Pre-fix,
        # connect(prepare_cache=True) shadowed mrconso without ATC, so
        # t.lookup('ATC', ...) silently returned None on the ONLY surface
        # that prepares by explicit opt-in while CLI (unprepared), MCP, api,
        # and FHIR all answered it.
        sources = DEFAULT_INVENTORY_SOURCES if cache_sources is None else cache_sources
        engine.prepare_cache(sources, create_indexes=cache_indexes)
    return Terminology(engine, connection=con)


def connect_remote(
    base_url: str,
    *,
    timeout: float = DEFAULT_REMOTE_TIMEOUT,
    headers: Mapping[str, str] | None = None,
    transport=None,
) -> Terminology:
    """Connect to a medterm4ds API server with the same notebook facade.

    ``base_url`` must be an http(s) URL string and ``timeout`` a positive
    number of seconds; both are validated at construction (invalid values
    raise ``ValueError`` immediately instead of failing at the first call).

    Timeout guidance (QC-485): the timeout is a per-request read timeout and
    ALSO counts time queued behind other requests on the server's
    single-worker DB executor. The 300s default covers typical workloads
    (``optimize``/``map`` over SNOMED measured 55-82s); a batch at the
    server's documented 10,000-code cap on ``patient_friendly``/``map``
    measured ~415s cold — pass ``timeout=600`` for bulk batches.

    Remote-only request caps (QC-480, not enforced by the local engine):
    batches are capped at 10,000 codes, 256 chars per code, 64 chars per
    source, and a 10MB request body; responses are capped client-side at
    50MiB (bound wide hierarchy walks with ``descendants(..., limit=)``).
    Exceeding a cap raises ``RuntimeError`` remotely where the local engine
    would succeed.

    Search note (QC-491): ``search(mode='lexical'|'semantic'|'hybrid')`` runs
    in the CLIENT process regardless of engine — BM25/SapBERT artifacts are
    downloaded into the local cache; only display canonicalization
    round-trips to the server.
    """
    return Terminology(
        RemoteApiEngine(
            base_url,
            timeout=timeout,
            headers=headers,
            transport=transport,
        )
    )


def _code_refs_from_args(
    source_or_codes: str | CodeArg,
    code: CodeValueArg | None = None,
) -> tuple[list[CodeRef], bool]:
    if code is not None:
        if not isinstance(source_or_codes, str):
            raise TypeError("When code is provided, the first argument must be a source string.")
        if isinstance(code, str):
            return [CodeRef(source=source_or_codes, code=code)], True
        # Per GLOBAL_RULES "Silent Fallbacks": programming bugs MUST propagate
        # with a helpful message. Pre-fix, an int code (e.g. 44054006) hit the
        # list-comprehension branch and raised the unhelpful
        # "'int' object is not iterable". Found by QC-005 (EDGE_CASE LOW).
        # CR-045 (review-5 finding 7): the isinstance(code, Sequence) guard
        # over-narrowed the domain — sets/frozensets/generators are valid
        # iterables the facade accepted pre-QC-005. Guard on "iterable, not
        # a string" instead; the int/float case still fails the per-item
        # string check below.
        if isinstance(code, Iterable) and not isinstance(code, str):
            return [CodeRef(source=source_or_codes, code=value) for value in code], False
        raise TypeError(
            f"code must be a string or a sequence of strings, got "
            f"{type(code).__name__} ({code!r}); pass code=\"{code}\" instead"
        )

    if isinstance(source_or_codes, str):
        raise TypeError("Pass both source and code, or pass CodeRef/list inputs.")

    if _is_single_code_input(source_or_codes):
        return [_coerce_code_input(source_or_codes)], True

    try:
        codes = [_coerce_code_input(item) for item in source_or_codes]
    except TypeError as exc:
        raise TypeError(
            "Code inputs must be CodeRef objects or (source, code) tuples."
        ) from exc
    return codes, False


def _is_single_code_input(value: Any) -> bool:
    return isinstance(value, CodeRef) or (
        isinstance(value, tuple)
        and len(value) == 2
        and not isinstance(value[0], CodeRef)
    )


def _coerce_code_input(value: CodeInput) -> CodeRef:
    if isinstance(value, CodeRef):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        source, code = value
        # QC-054 (LOW): pre-fix, ``str(code)`` silently coerced an int
        # (e.g. ``parents([('SNOMEDCT_US', 44054006)])``) to the string
        # '44054006' and accepted it as valid. Per GLOBAL_RULES "Silent
        # Fallbacks" — programming bugs MUST propagate. ``bool`` is
        # excluded because ``isinstance(True, int)`` is True in Python.
        if not isinstance(source, str):
            raise TypeError(
                f"source must be a string, got {type(source).__name__} "
                f"({source!r}); pass source=\"{source}\" instead"
            )
        if not isinstance(code, str):
            raise TypeError(
                f"code must be a string, got {type(code).__name__} "
                f"({code!r}); pass code=\"{code}\" instead"
            )
        return CodeRef(source=source, code=code)
    raise TypeError


def _as_dicts(rows: Sequence[Any]) -> list[dict[str, Any]]:
    return [row.to_dict() if hasattr(row, "to_dict") else dict(row) for row in rows]


def _missing_code_info(ref: CodeRef) -> dict[str, Any]:
    # Delegates to CodeInfo(code=ref).to_dict() — CodeInfo's defaults are all
    # None for the optional fields, so a freshly-constructed CodeInfo produces
    # exactly the missing-info shape. Single source of truth lives in
    # core.models.CodeInfo; previously duplicated across client/ds/bulk/cli
    # with drifting field sets.
    from medterm4ds.core.models import CodeInfo
    return CodeInfo(code=ref).to_dict()


# Canonical CodeInfo column order — single source of truth for empty-DataFrame
# schemas in lookup_df/patient_friendly_df/etc. Found by QC-004 (EDGE_CASE
# LOW): pre-fix, ``pd.DataFrame([], dtype=object)`` produced a 0-column frame
# because there were no records to infer schema from.
_CODEINFO_COLUMNS: tuple[str, ...] = (
    "source", "code", "name", "cui", "aui", "tty", "suppress",
)


def _empty_codeinfo_frame(backend: str):
    """Return an empty DataFrame with the canonical 7-column CodeInfo schema."""
    if backend == "pandas":
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError("Install pandas to use to_pandas() or to_dataframe().") from exc
        return pd.DataFrame({col: pd.Series(dtype=object) for col in _CODEINFO_COLUMNS})
    if backend == "polars":
        try:
            import polars as pl
        except ImportError as exc:
            raise ImportError("Install polars to use to_dataframe(backend='polars').") from exc
        return pl.DataFrame(schema={col: pl.Utf8 for col in _CODEINFO_COLUMNS})
    raise ValueError("backend must be 'pandas' or 'polars'")


# Canonical 16-column CodeMapping schema for empty-result DataFrames. Mirrors
# CodeMapping.to_dict() key order. Found by QC-024 (EDGE_CASE MEDIUM):
# pre-fix, map_df on an empty result produced a 0x0 frame because there were
# no records to infer schema from, causing downstream df['source'] to KeyError.
_CODEMAPPING_COLUMNS: tuple[str, ...] = (
    "source", "code", "source_display",
    "target_source", "target_code", "target_display",
    "relationship", "match_type", "match_depth",
    "source_cui", "target_cui",
    "source_aui", "target_aui", "target_tty",
    "matched_via",
)


def _empty_codemapping_frame(backend: str):
    """Return an empty DataFrame with the canonical 16-column CodeMapping schema."""
    if backend == "pandas":
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError("Install pandas to use to_pandas() or to_dataframe().") from exc
        return pd.DataFrame({col: pd.Series(dtype=object) for col in _CODEMAPPING_COLUMNS})
    if backend == "polars":
        try:
            import polars as pl
        except ImportError as exc:
            raise ImportError("Install polars to use to_dataframe(backend='polars').") from exc
        return pl.DataFrame(schema={col: pl.Utf8 for col in _CODEMAPPING_COLUMNS})
    raise ValueError("backend must be 'pandas' or 'polars'")


# Canonical 14-column CodeRelation schema for empty-result DataFrames.
# Mirrors CodeRelation.to_dict() key order. Found by QC-045 (EDGE_CASE
# HIGH): pre-fix, hierarchy_df on an empty result produced a 0x0 frame
# because there were no records to infer schema from, causing downstream
# df['target_code'] to KeyError. Sibling of EC-01 FIX-007 / EC-02 FIX-006.
_CODERELATION_COLUMNS: tuple[str, ...] = (
    "source", "code", "source_display",
    "target_source", "target_code", "target_display",
    "relationship", "depth",
    "rel", "rela",
    "source_cui", "target_cui",
    "source_aui", "target_aui",
)


def _empty_coderelation_frame(backend: str):
    """Return an empty DataFrame with the canonical 14-column CodeRelation schema."""
    if backend == "pandas":
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError("Install pandas to use to_pandas() or to_dataframe().") from exc
        return pd.DataFrame({col: pd.Series(dtype=object) for col in _CODERELATION_COLUMNS})
    if backend == "polars":
        try:
            import polars as pl
        except ImportError as exc:
            raise ImportError("Install polars to use to_dataframe(backend='polars').") from exc
        return pl.DataFrame(schema={col: pl.Utf8 for col in _CODERELATION_COLUMNS})
    raise ValueError("backend must be 'pandas' or 'polars'")


# Canonical 8-column FriendlyNameResult schema for empty-result DataFrames.
# Mirrors FriendlyNameResult.to_dict() key order. Found by QC-072 (EDGE_CASE
# HIGH): pre-fix, patient_friendly_df on an empty result produced a 0x0
# frame because there were no records to infer schema from, causing
# downstream df['name'] to KeyError. Sibling of EC-01 FIX-007 / EC-02
# FIX-006 / EC-03 FIX-002.
_FRIENDLY_NAME_RESULT_COLUMNS: tuple[str, ...] = (
    "code", "source", "name",
    "friendly_source", "match_type", "match_depth",
    "technical_name", "matched_via",
)


def _empty_friendly_frame(backend: str):
    """Return an empty DataFrame with the canonical 8-column FriendlyNameResult schema."""
    if backend == "pandas":
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError("Install pandas to use to_pandas() or to_dataframe().") from exc
        return pd.DataFrame({col: pd.Series(dtype=object) for col in _FRIENDLY_NAME_RESULT_COLUMNS})
    if backend == "polars":
        try:
            import polars as pl
        except ImportError as exc:
            raise ImportError("Install polars to use to_dataframe(backend='polars').") from exc
        return pl.DataFrame(schema={col: pl.Utf8 for col in _FRIENDLY_NAME_RESULT_COLUMNS})
    raise ValueError("backend must be 'pandas' or 'polars'")


# Canonical 11-column ConceptMapRow schema for empty-result DataFrames.
# Mirrors ConceptMapRow.to_dict() key order. Found by QC-073 / QC-080
# (EDGE_CASE HIGH + CROSS_SURFACE HIGH): pre-fix, conceptmap_df and
# mapping_conceptmap_df on an empty result produced a 0x0 frame because
# there were no records to infer schema from, causing downstream
# df['target_display'] / df['relationship'] to KeyError. Sibling of
# EC-01 FIX-007 / EC-02 FIX-006 / EC-03 FIX-002.
_CONCEPTMAP_ROW_COLUMNS: tuple[str, ...] = (
    "source", "code", "source_display",
    "target_source", "target_code", "target_display",
    "relationship", "friendly_source",
    "match_type", "match_depth", "matched_via",
)


def _empty_conceptmap_frame(backend: str):
    """Return an empty DataFrame with the canonical 11-column ConceptMapRow schema."""
    if backend == "pandas":
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError("Install pandas to use to_pandas() or to_dataframe().") from exc
        return pd.DataFrame({col: pd.Series(dtype=object) for col in _CONCEPTMAP_ROW_COLUMNS})
    if backend == "polars":
        try:
            import polars as pl
        except ImportError as exc:
            raise ImportError("Install polars to use to_dataframe(backend='polars').") from exc
        return pl.DataFrame(schema={col: pl.Utf8 for col in _CONCEPTMAP_ROW_COLUMNS})
    raise ValueError("backend must be 'pandas' or 'polars'")


# Canonical 7-column NameSearchResult schema for empty-result DataFrames.
# Mirrors NameSearchResult.to_dict() key order. Found by QC-105 (CROSS_SURFACE
# HIGH): pre-fix, search_df on a no-match query produced a 0x0 frame because
# there were no records to infer schema from, causing downstream df['source']
# to KeyError. Sibling of EC-01 FIX-007 / EC-02 FIX-006 / EC-03 FIX-002 /
# EC-04 FIX-002.
_NAME_SEARCH_RESULT_COLUMNS: tuple[str, ...] = (
    "source", "code", "name", "cui", "aui", "tty", "match_type",
)


def _empty_name_search_frame(backend: str):
    """Return an empty DataFrame with the canonical 7-column NameSearchResult schema."""
    if backend == "pandas":
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError("Install pandas to use to_pandas() or to_dataframe().") from exc
        return pd.DataFrame({col: pd.Series(dtype=object) for col in _NAME_SEARCH_RESULT_COLUMNS})
    if backend == "polars":
        try:
            import polars as pl
        except ImportError as exc:
            raise ImportError("Install polars to use to_dataframe(backend='polars').") from exc
        return pl.DataFrame(schema={col: pl.Utf8 for col in _NAME_SEARCH_RESULT_COLUMNS})
    raise ValueError("backend must be 'pandas' or 'polars'")


# Canonical 18-column CodeResolution schema for empty-result DataFrames.
# Mirrors CodeResolution.to_dict() key order. Found by QC-100 (EDGE_CASE
# MEDIUM): pre-fix, resolve_df([]) produced a 0x0 frame because there were no
# records to infer schema from, causing downstream df['status'] to KeyError.
# Sibling of EC-01 FIX-007 / EC-02 FIX-006 / EC-03 FIX-002 / EC-04 FIX-002.
_RESOLUTION_COLUMNS: tuple[str, ...] = (
    "source", "code",
    "resolved_source", "resolved_code",
    "status", "match_type",
    "input_display", "resolved_display",
    "input_cui", "resolved_cui",
    "input_aui", "resolved_aui",
    "input_suppress", "resolved_suppress",
    "replacement_relationship", "normalized_code",
    "candidates", "matched_via",
)


def _empty_resolution_frame(backend: str):
    """Return an empty DataFrame with the canonical 18-column CodeResolution schema."""
    if backend == "pandas":
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError("Install pandas to use to_pandas() or to_dataframe().") from exc
        return pd.DataFrame({col: pd.Series(dtype=object) for col in _RESOLUTION_COLUMNS})
    if backend == "polars":
        try:
            import polars as pl
        except ImportError as exc:
            raise ImportError("Install polars to use to_dataframe(backend='polars').") from exc
        return pl.DataFrame(schema={col: pl.Utf8 for col in _RESOLUTION_COLUMNS})
    raise ValueError("backend must be 'pandas' or 'polars'")


__all__ = ["Terminology", "connect", "connect_remote"]
