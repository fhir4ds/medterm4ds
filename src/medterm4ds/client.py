"""Notebook-friendly terminology client."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from medterm4ds.core.config import MemoryProfile, local_duckdb_config
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.api import RemoteApiEngine
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
    in v0.0.2 to eliminate the silent source/code swap footgun.
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
        return to_dataframe(
            [
                row.to_dict() if row is not None else _missing_code_info(ref)
                for ref, row in zip(codes, rows, strict=True)
            ],
            backend=backend,
        )

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
        return to_dataframe(
            _as_dicts(
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
            ),
            backend=backend,
        )

    def hierarchy(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        direction: str,
        max_depth: int = 1,
    ):
        """Traverse parents, children, ancestors, or descendants."""
        return get_code_relations(
            _code_refs_from_args(source_or_codes, code)[0],
            engine=self.engine,
            direction=direction,
            max_depth=max_depth,
        )

    def hierarchy_df(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        direction: str,
        max_depth: int = 1,
        backend: str = "pandas",
    ):
        """Traverse hierarchy and return a DataFrame."""
        return to_dataframe(
            _as_dicts(
                self.hierarchy(
                    source_or_codes,
                    code,
                    direction=direction,
                    max_depth=max_depth,
                )
            ),
            backend=backend,
        )

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
    ):
        """Return descendant relationships."""
        return get_descendants(
            _code_refs_from_args(source_or_codes, code)[0],
            engine=self.engine,
            max_depth=max_depth,
        )

    def resolve(self, source_or_codes: str | CodeArg, code: CodeValueArg | None = None):
        """Resolve active, obsolete, historical, and NDC inputs."""
        codes, single = _code_refs_from_args(source_or_codes, code)
        rows = resolve_codes(codes, engine=self.engine)
        return rows[0] if single else rows

    def resolve_df(
        self,
        source_or_codes: str | CodeArg,
        code: CodeValueArg | None = None,
        *,
        backend: str = "pandas",
    ):
        """Resolve codes and return a DataFrame."""
        rows = self.resolve(source_or_codes, code)
        return to_dataframe(_as_dicts([rows] if not isinstance(rows, list) else rows), backend=backend)

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
            return search_service(query, mode=mode, sources=src_list, count=limit)
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
        return to_dataframe(
            _as_dicts(
                self.search(
                    query,
                    sources=sources,
                    tty_filters=tty_filters,
                    limit=limit,
                )
            ),
            backend=backend,
        )

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
        return to_dataframe(_as_dicts(self.code_ttys(source_or_codes, code)), backend=backend)

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
        return to_dataframe(
            _as_dicts(
                self.conceptmap(
                    source_or_codes,
                    code,
                    batch_size=batch_size,
                    max_depth=max_depth,
                    target_source=target_source,
                )
            ),
            backend=backend,
        )

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
        return to_dataframe(
            _as_dicts(
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
            ),
            backend=backend,
        )


def connect(
    db_path: str | Path | None = None,
    *,
    memory_profile: MemoryProfile = "balanced",
    memory_limit: str | None = None,
    temp_directory: str | Path | None = None,
    threads: int | None = None,
    query_chunk_size: int | None = None,
    read_only: bool = True,
    prepare_cache: bool = False,
    cache_sources: Sequence[str] | None = None,
    cache_indexes: bool = True,
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

    Args:
        db_path: Path to a pre-built UMLS DuckDB file. If None, auto-provisions.
        umls_api_key: NLM UTS API key (reads UMLS_API_KEY env var if not passed).
            Only needed on first run (to build lookup.duckdb from UMLS RRF).
        version: UMLS release tag (default ``2026AA``).
        cache_dir: Override cache root (default ``~/.medterm4ds/``).
        offline: Skip all network calls. Use existing cache only.
            Defaults to ``MEDTERM4DS_OFFLINE`` env var if set.

    Returns:
        A ``Terminology`` instance — the same facade used by CLI, MCP,
        and FHIR server.
    """
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB is required. Install medterm4ds[duckdb].") from exc

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

    con = duckdb.connect(str(db_path), read_only=read_only)
    config = local_duckdb_config(
        memory_profile,
        memory_limit=memory_limit,
        temp_directory=temp_directory,
        threads=threads,
        query_chunk_size=query_chunk_size,
    )
    engine = LocalDuckDBEngine(con, config=config)
    if prepare_cache:
        if cache_sources is None:
            engine.prepare_cache(create_indexes=cache_indexes)
        else:
            engine.prepare_cache(cache_sources, create_indexes=cache_indexes)
    return Terminology(engine, connection=con)


def connect_remote(
    base_url: str,
    *,
    timeout: float = 30.0,
    headers: Mapping[str, str] | None = None,
    transport=None,
) -> Terminology:
    """Connect to a medterm4ds API server with the same notebook facade."""
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
        return [CodeRef(source=source_or_codes, code=value) for value in code], False

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
        return CodeRef(source=str(source), code=str(code))
    raise TypeError


def _as_dicts(rows: Sequence[Any]) -> list[dict[str, Any]]:
    return [row.to_dict() if hasattr(row, "to_dict") else dict(row) for row in rows]


def _missing_code_info(ref: CodeRef) -> dict[str, Any]:
    return {
        "source": ref.source,
        "code": ref.code,
        "name": None,
        "cui": None,
        "aui": None,
        "tty": None,
        "suppress": None,
    }


__all__ = ["Terminology", "connect", "connect_remote"]
