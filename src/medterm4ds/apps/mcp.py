"""MCP server for medterm4ds."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from medterm4ds.apps._asyncutil import run_db as _run_db_impl
from medterm4ds.core.config import MemoryProfile, local_duckdb_config
from medterm4ds.core.env import env_bool, env_int, env_str
from medterm4ds.core.models import CodeInfo, CodeRef
from medterm4ds.domains import evidence as evidence_domain
from medterm4ds.domains import terminology as terminology_domain
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.outputs import render_output
from medterm4ds.services.conceptmap import get_concept_map
from medterm4ds.services.discovery import (
    get_code_ttys,
    get_source_stats,
    sample_source_codes,
    search_names,
)
from medterm4ds.services.hierarchy import (
    get_code_relations,
    get_descendants_bfs,
    normalize_hierarchy_direction,
)
from medterm4ds.services.inventory import DEFAULT_INVENTORY_SOURCES, normalize_sources
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.mapping import get_code_mappings
from medterm4ds.services.optimize import optimize_codes
from medterm4ds.services.patient_friendly import get_patient_friendly_names
from medterm4ds.services.resolution import resolve_codes

try:
    import duckdb
    from fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised only without mcp extras.
    raise ImportError("Install medterm4ds[mcp] to use medterm4ds.apps.mcp.") from exc

# QC-432 (HIGH): default per-code result cap for MCP descendant expansion.
# Descendants of a top-level SNOMED concept exceed 100K relations at the
# default depth — the walk is bounded like FHIR $expand's descendant_budget.
DEFAULT_DESCENDANT_LIMIT = 1000


@dataclass(frozen=True)
class McpSettings:
    """Single-database MCP process settings."""

    db_path: Path
    sources: tuple[str, ...] = DEFAULT_INVENTORY_SOURCES
    memory_profile: MemoryProfile = "fast"
    memory_limit: str | None = None
    temp_directory: str | Path | None = None
    threads: int | None = None
    query_chunk_size: int | None = None
    prepare_cache: bool = True
    cache_indexes: bool = False

    @classmethod
    def from_env(cls) -> McpSettings:
        db_path = os.getenv("MEDTERM4DS_DB")
        if not db_path:
            raise RuntimeError("MEDTERM4DS_DB is required for the MCP server.")
        return cls(
            db_path=Path(db_path),
            sources=normalize_sources(os.getenv("MEDTERM4DS_SOURCES")),
            memory_profile=os.getenv("MEDTERM4DS_MEMORY_PROFILE", "fast"),  # type: ignore[arg-type]
            # CR-044 (review-5 finding 6): env_str treats whitespace-only as
            # unset like the facade/CLI; the old ``or None`` idiom passed
            # " " through to validate_memory_limit and crashed at startup.
            memory_limit=env_str("MEDTERM4DS_MEMORY_LIMIT"),
            temp_directory=env_str("MEDTERM4DS_TEMP_DIR"),
            threads=env_int("MEDTERM4DS_THREADS", minimum=1),
            query_chunk_size=env_int("MEDTERM4DS_QUERY_CHUNK_SIZE", minimum=1),
            prepare_cache=env_bool("MEDTERM4DS_PREPARE_CACHE", True),
            cache_indexes=env_bool("MEDTERM4DS_CACHE_INDEXES", False),
        )


class McpRuntime:
    """Owns the configured DuckDB connection and local DuckDB engine."""

    def __init__(self, settings: McpSettings):
        self.settings = settings
        self.con = None
        self.engine: LocalDuckDBEngine | None = None
        self.db_executor: ThreadPoolExecutor | None = None

    @property
    def ready(self) -> bool:
        return self.engine is not None

    def open(self) -> None:
        if self.ready:
            return
        if not self.settings.db_path.exists():
            raise RuntimeError(f"Database not found: {self.settings.db_path}")
        self.con = duckdb.connect(str(self.settings.db_path), read_only=True)
        config = local_duckdb_config(
            self.settings.memory_profile,
            memory_limit=self.settings.memory_limit,
            temp_directory=self.settings.temp_directory,
            threads=self.settings.threads,
            query_chunk_size=self.settings.query_chunk_size,
        )
        self.engine = LocalDuckDBEngine(self.con, config=config)
        self.db_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-db")
        if self.settings.prepare_cache:
            self.engine.prepare_cache(self.settings.sources, create_indexes=self.settings.cache_indexes)

    def close(self) -> None:
        # Shut down the executor BEFORE closing the connection so any in-flight
        # DuckDB call finishes (wait=True) before con.close() runs. Without this
        # ordering, the worker thread can segfault or hang on a closed connection.
        if self.db_executor is not None:
            self.db_executor.shutdown(wait=True, cancel_futures=True)
            self.db_executor = None
        if self.con is not None:
            self.con.close()
        self.con = None
        self.engine = None

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ready else "starting",
            "ready": self.ready,
            "database": str(self.settings.db_path),
            "sources": list(self.settings.sources),
            "memory_profile": self.settings.memory_profile,
            "cache_prepared": bool(self.engine.cache_prepared) if self.engine else False,
        }

    def patient_friendly_name(
        self,
        *,
        code: str,
        source: str,
        max_depth: int = 5,
        resolve_mode: str = "active_only",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        _validate_single_code_inputs(code=code, source=source)
        result = get_patient_friendly_names(
            [CodeRef(source=source, code=code)],
            engine=self._engine(),
            max_depth=max_depth,
            # QC-417 (MEDIUM): was hardcoded resolve_current — diverged from
            # the Python facade default (active_only) with no way to pick the
            # other mode. Same default as Python, overridable per call.
            resolve_mode=resolve_mode,
        )[0]
        return render_output(result.to_dict(), output_format=output_format, title=f"{source}:{code}")

    def lookup_code(
        self,
        *,
        code: str,
        source: str,
        resolve_mode: str = "active_only",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        payload = self.lookup_codes(
            codes=[code],
            sources=[source],
            resolve_mode=resolve_mode,
            output_format="dict",
        )
        result = payload["results"][0]
        # QC-323: result is always a CodeInfo dict now (null-field record for
        # unknown codes) — never None. Mirrors the CLI lookup shape.
        return render_output(result, output_format=output_format, title=f"{source}:{code}")

    def lookup_codes(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        resolve_mode: str = "active_only",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        refs = build_code_refs(codes, sources)
        infos = get_code_infos(refs, engine=self._engine(), resolve_mode=resolve_mode)
        # QC-323 (MEDIUM): emit the canonical missing-code shape
        # (CodeInfo(code=ref).to_dict()) instead of None — the null-field
        # record is the single source of truth per GLOBAL_RULES.md
        # (client.py, ds.py, services/bulk.py, apps/cli.py all use it); the
        # MCP surface silently diverged to {"results": [null]}.
        payload = {
            "results": [
                info.to_dict() if info else CodeInfo(code=ref).to_dict()
                for info, ref in zip(infos, refs, strict=True)
            ]
        }
        # QC-364 (MEDIUM): render ALL rows in table mode, matching the CLI
        # table for the same query (render_table direct call in the CLI is
        # untruncated) — mirrors the QC-203 optimize fix.
        return render_output(payload, output_format=output_format, max_rows=None)

    def resolve_codes(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        resolve_mode: str = "historical",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        from medterm4ds.services.resolution import effective_code_refs
        refs = build_code_refs(codes, sources)
        _effective, results = effective_code_refs(
            refs, engine=self._engine(), resolve_mode=resolve_mode
        )
        if results is None:
            # active_only fast-path for non-NDC inputs — still return
            # CodeResolution rows for shape-stability.
            results = resolve_codes(_effective, engine=self._engine())
        payload = {"results": [result.to_dict() for result in results]}
        # QC-364 (MEDIUM): render ALL rows in table mode, matching the CLI
        # table for the same query (render_table direct call in the CLI is
        # untruncated) — mirrors the QC-203 optimize fix.
        return render_output(payload, output_format=output_format, max_rows=None)

    def source_stats(
        self,
        *,
        sources: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        stats = get_source_stats(engine=self._engine(), sources=sources)
        return {"results": [stat.to_dict() for stat in stats]}

    def sample_codes(
        self,
        *,
        sources: Sequence[str] | None = None,
        per_source: int = 10,
    ) -> dict[str, Any]:
        codes = sample_source_codes(
            engine=self._engine(),
            sources=sources,
            per_source=per_source,
        )
        return {"results": [{"source": code.source, "code": code.code} for code in codes]}

    def code_ttys(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
    ) -> dict[str, Any]:
        refs = build_code_refs(codes, sources)
        infos = get_code_ttys(refs, engine=self._engine())
        return {"results": [info.to_dict() for info in infos]}

    def search_names(
        self,
        *,
        query: str,
        sources: Sequence[str] | None = None,
        tty_filters: Sequence[str] | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        results = search_names(
            query,
            engine=self._engine(),
            sources=sources,
            tty_filters=tty_filters,
            limit=limit,
        )
        return {"results": [result.to_dict() for result in results]}

    def discover(
        self,
        *,
        source_terminology: str,
        code: str | None = None,
        depth: int = 1,
        include_ancestors: bool = False,
        limit: int = 20,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        return terminology_domain.discover(
            source_terminology,
            engine=self._engine(),
            code=code,
            depth=depth,
            include_ancestors=include_ancestors,
            limit=limit,
            include_retired=include_retired,
        )

    def cross_reference(
        self,
        *,
        code: str,
        from_source: str,
        to_sources: Sequence[str] | None = None,
        mode: str = "exact",
        max_depth: int = 5,
        max_results_per_code: int = 50,
    ) -> dict[str, Any]:
        _validate_single_code_inputs(code=code, source=from_source)
        return terminology_domain.cross_reference(
            code,
            from_source,
            engine=self._engine(),
            to_sources=to_sources,
            mode=mode,
            max_depth=max_depth,
            max_results_per_code=max_results_per_code,
        )

    def diagnosis_codes(
        self,
        *,
        condition: str,
        limit: int = 20,
        descendant_depth: int | None = None,
        include_ancestors: bool | None = None,
    ) -> dict[str, Any]:
        return terminology_domain.diagnosis_codes(
            condition,
            engine=self._engine(),
            limit=limit,
            descendant_depth=descendant_depth,
            include_ancestors=include_ancestors,
        )

    def lab_codes(self, *, lab_test: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.lab_codes(lab_test, engine=self._engine(), limit=limit)

    def lab_value_codes(self, *, clinical_value: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.lab_value_codes(clinical_value, engine=self._engine(), limit=limit)

    def procedure_codes(
        self,
        *,
        procedure: str,
        limit: int = 20,
        descendant_depth: int | None = None,
        include_ancestors: bool | None = None,
    ) -> dict[str, Any]:
        return terminology_domain.procedure_codes(
            procedure,
            engine=self._engine(),
            limit=limit,
            descendant_depth=descendant_depth,
            include_ancestors=include_ancestors,
        )

    def hcpcs_drugs(self, *, drug_name: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.hcpcs_drugs(drug_name, engine=self._engine(), limit=limit)

    def vaccine_codes(self, *, vaccine: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.vaccine_codes(vaccine, engine=self._engine(), limit=limit)

    def search_drug(
        self,
        *,
        drug_name: str,
        limit: int = 20,
        tty_filters: Sequence[str] | None = None,
        include_equivalents: bool = True,
        include_ndc: bool = False,
    ) -> dict[str, Any]:
        return terminology_domain.search_drug(
            drug_name,
            engine=self._engine(),
            limit=limit,
            tty_filters=tty_filters,
            include_equivalents=include_equivalents,
            include_ndc=include_ndc,
        )

    def drugs_by_class(self, *, class_id: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.drugs_by_class(class_id, engine=self._engine(), limit=limit)

    def drugs_for_indication(
        self,
        *,
        condition: str,
        limit: int = 20,
        source: str | None = None,
        code: str | None = None,
        relationship_types: Sequence[str] | None = None,
        max_depth: int = 5,
        include_product_groups: bool = True,
    ) -> dict[str, Any]:
        return terminology_domain.drugs_for_indication(
            condition,
            engine=self._engine(),
            limit=limit,
            source=source,
            code=code,
            relationship_types=relationship_types,
            max_depth=max_depth,
            include_product_groups=include_product_groups,
        )

    def indication_search(self, *, indication: str, limit: int = 20) -> dict[str, Any]:
        return evidence_domain.indication_search(indication, limit=limit)

    def fda_label_by_rxcui(self, *, rxcui: str) -> dict[str, Any]:
        return evidence_domain.fda_label_by_rxcui(rxcui)

    def guideline_search(self, *, query: str, limit: int = 20) -> dict[str, Any]:
        return evidence_domain.guideline_search(query, limit=limit)

    def guideline_recommendations(self, *, topic: str, limit: int = 20) -> dict[str, Any]:
        return evidence_domain.guideline_recommendations(topic, limit=limit)

    def guideline_fulltext(self, *, guideline_id: str) -> dict[str, Any]:
        return evidence_domain.guideline_fulltext(guideline_id)

    def guidelines_for_code(self, *, code: str, source: str, limit: int = 20) -> dict[str, Any]:
        _validate_single_code_inputs(code=code, source=source)
        return evidence_domain.guidelines_for_code(code, source, engine=self._engine(), limit=limit)

    def code_relations(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        direction: str,
        max_depth: int = 5,
        limit: int = DEFAULT_DESCENDANT_LIMIT,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        refs = build_code_refs(codes, sources)
        # QC-432 (HIGH): the descendants direction must not run the
        # path-enumerating recursive CTE — for top-level SNOMED concepts it
        # never completes and blocks this server's single db_executor (and
        # therefore every DB-backed tool) indefinitely. Route through the
        # layer-bounded BFS (the same contract FHIR $expand uses) with a
        # result cap. Ancestors walk upward and stay small, so only the
        # descendants direction needs the rerouting.
        if normalize_hierarchy_direction(direction) == "descendants":
            return self._descendants_bounded(
                refs, max_depth=max_depth, limit=limit, include_retired=include_retired,
            )
        relations = get_code_relations(
            refs,
            engine=self._engine(),
            direction=direction,
            max_depth=max_depth,
            limit=limit,
            include_retired=include_retired,
        )
        return {"results": [relation.to_dict() for relation in relations]}

    def map_codes(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        target_sources: Sequence[str],
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
        resolve_mode: str = "active_only",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        refs = build_code_refs(codes, sources)
        mappings = get_code_mappings(
            refs,
            engine=self._engine(),
            target_sources=target_sources,
            max_results_per_code=max_results_per_code,
            max_depth=max_depth,
            include_target_ancestors=include_target_ancestors,
            include_target_descendants=include_target_descendants,
            resolve_mode=resolve_mode,
        )
        payload = {"results": [mapping.to_dict() for mapping in mappings]}
        # QC-364 (MEDIUM): render ALL rows in table mode, matching the CLI
        # table for the same query (render_table direct call in the CLI is
        # untruncated) — mirrors the QC-203 optimize fix.
        return render_output(payload, output_format=output_format, max_rows=None)

    def optimize(
        self,
        *,
        codes: Sequence[str],
        source: str,
        relationship: str | None = None,
        output_format: str = "dict",
        rule_format: str = "compact",
        include_codes: bool = False,
    ) -> dict[str, Any] | str:
        # QC-086 (MEDIUM): source=None crashed inside CodeRef.__post_init__
        # with a raw TypeError. codes=None crashed on iteration. Surface
        # clean TypeErrors before constructing CodeRefs. Sibling of EC-03
        # FIX-006.
        if not isinstance(source, str):
            raise TypeError(
                f"source must be a string, got {type(source).__name__}"
            )
        if codes is None or not isinstance(codes, Sequence):
            raise TypeError(
                f"codes must be a sequence, got {type(codes).__name__}"
            )
        for code in codes:
            if not isinstance(code, str):
                raise TypeError(
                    f"each code must be a string, got {type(code).__name__}"
                )
        # QC-430 (LOW): `output_format` means the RULE format on Python/CLI
        # (compact/flat) but the RENDER format on this surface (the rule
        # format is `rule_format`). Accept the documented Python vocabulary
        # as an alias so carrying Python usage onto MCP does the intended
        # thing instead of failing with a render-format error.
        if output_format in {"compact", "flat"}:
            rule_format = output_format
            output_format = "dict"
        # QC-198 (MEDIUM): validate rule_format at the MCP boundary so the
        # error names the parameter the client actually sent. Pre-fix a typo
        # surfaced the service message "output_format must be compact or
        # flat" — a parameter that does not exist on this surface.
        if rule_format not in {"compact", "flat"}:
            raise ValueError("rule_format must be compact or flat")
        result = optimize_codes(
            [CodeRef(source=source, code=code) for code in codes],
            engine=self._engine(),
            relationship=relationship,
            output_format=rule_format,
            include_codes=include_codes,
        )
        payload = result.to_dict(include_codes=include_codes)
        if output_format == "table":
            # QC-203 (HIGH-class divergence, MEDIUM severity): render ALL
            # rules, matching the CLI table (previously capped at 50 rows
            # with a bare "... N more rows" suffix — 14% data loss for a
            # 58-rule valueset). Every rule is load-bearing.
            return render_output(
                {"results": payload["rules"]}, output_format="table", max_rows=None
            )
        # QC-205 (LOW): no title — matches the CLI tree render for the same
        # valueset (the CLI emits no title line).
        return render_output(payload, output_format=output_format)

    def parents(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        include_retired: bool = False,
    ) -> dict[str, Any]:
        return self.code_relations(
            codes=codes, sources=sources, direction="parents", max_depth=1,
            include_retired=include_retired,
        )

    def children(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        include_retired: bool = False,
    ) -> dict[str, Any]:
        return self.code_relations(
            codes=codes, sources=sources, direction="children", max_depth=1,
            include_retired=include_retired,
        )

    def ancestors(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        max_depth: int = 5,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        return self.code_relations(
            codes=codes,
            sources=sources,
            direction="ancestors",
            max_depth=max_depth,
            include_retired=include_retired,
        )

    def descendants(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        max_depth: int = 5,
        limit: int = DEFAULT_DESCENDANT_LIMIT,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        refs = build_code_refs(codes, sources)
        return self._descendants_bounded(
            refs, max_depth=max_depth, limit=limit, include_retired=include_retired,
        )

    def _descendants_bounded(
        self,
        refs: Sequence[CodeRef],
        *,
        max_depth: int,
        limit: int,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        """Bounded descendant expansion (QC-432, HIGH).

        Per-seed budget via ``get_descendants_bfs`` — the layer-by-layer walk
        over direct-children queries that FHIR ``$expand`` already uses. The
        previous recursive-CTE route enumerated every distinct path through
        the subtree and never completed for top-level SNOMED concepts (e.g.
        404684003 at the default max_depth=5 was still running after a 240s
        watchdog kill), head-of-line blocking the single-worker executor.
        """
        # Mirror get_code_relations' limit validation (QC-053).
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TypeError(f"limit must be an integer, got {type(limit).__name__}")
        if limit < 0:
            raise ValueError("limit must be non-negative")
        results: list[dict[str, Any]] = []
        truncated = False
        for ref in refs:
            relations, depth_cap_hit = get_descendants_bfs(
                ref,
                engine=self._engine(),
                max_depth=max_depth,
                limit=limit,
                include_retired=include_retired,
            )
            results.extend(relation.to_dict() for relation in relations)
            if depth_cap_hit or (limit is not None and len(relations) >= limit):
                truncated = True
        return {"results": results, "truncated": truncated}

    def patient_friendly_names(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        max_depth: int = 5,
        resolve_mode: str = "active_only",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        refs = build_code_refs(codes, sources)
        results = get_patient_friendly_names(
            refs,
            engine=self._engine(),
            max_depth=max_depth,
            # QC-417 (MEDIUM): align default with the Python facade and make
            # the mode overridable (was hardcoded resolve_current).
            resolve_mode=resolve_mode,
        )
        payload = {"results": [result.to_dict() for result in results]}
        # QC-364 (MEDIUM): render ALL rows in table mode, matching the CLI
        # table for the same query (render_table direct call in the CLI is
        # untruncated) — mirrors the QC-203 optimize fix.
        return render_output(payload, output_format=output_format, max_rows=None)

    def patient_friendly_concept_map(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        max_depth: int = 5,
        batch_size: int = 5000,
        target_source: str = "PATIENT_FRIENDLY",
    ) -> dict[str, Any]:
        refs = build_code_refs(codes, sources)
        rows = get_concept_map(
            refs,
            engine=self._engine(),
            max_depth=max_depth,
            batch_size=batch_size,
            target_source=target_source,
        )
        return {"results": [row.to_dict() for row in rows]}

    def _engine(self) -> LocalDuckDBEngine:
        if self.engine is None:
            raise RuntimeError("Terminology engine is not ready.")
        return self.engine


def create_mcp_server(
    settings: McpSettings | None = None,
    *,
    runtime: McpRuntime | None = None,
) -> FastMCP:
    """Create a FastMCP server for one configured DuckDB database."""
    server_runtime = runtime or McpRuntime(settings or McpSettings.from_env())

    async def _run_db(func, *args, **kwargs):
        """Offload to this server's single-worker executor.

        Shadows the module-level helper so handlers don't need to pass the
        executor explicitly. server_runtime.db_executor is created in open()
        and torn down in close(); handlers run between those points.
        """
        return await _run_db_impl(server_runtime.db_executor, func, *args, **kwargs)

    @asynccontextmanager
    async def lifespan(_mcp: FastMCP):
        server_runtime.open()
        try:
            yield
        finally:
            server_runtime.close()

    mcp = FastMCP(
        "medterm4ds",
        instructions="Batch-first medical terminology tools backed by medterm4ds local DuckDB.",
        lifespan=lifespan,
    )

    @mcp.tool()
    async def health() -> dict[str, Any]:
        """Return MCP terminology engine readiness and configuration."""
        # QC-207 (HIGH): health() is pure dict assembly over Python
        # attributes (no DB access) — calling it through the single-worker
        # db_executor made readiness checks queue behind long-running tools
        # (measured 39.3s behind one LNC optimize). Invoke it directly.
        return server_runtime.health()

    @mcp.tool()
    async def patient_friendly_name(
        code: str,
        source: str,
        max_depth: int = 5,
        resolve_mode: str = "active_only",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        """Resolve one clinical code to a patient-friendly name.

        resolve_mode: 'active_only' (default; matches the Python client —
        obsolete codes return a no-match row), 'resolve_current' (follow the
        code's replacement to an active concept), or 'historical' (answer
        with the queried code's own historical atom).
        """
        return await _run_db(server_runtime.patient_friendly_name, code=code, source=source, max_depth=max_depth, resolve_mode=resolve_mode, output_format=output_format)

    @mcp.tool()
    async def lookup_code(
        code: str,
        source: str,
        resolve_mode: str = "active_only",
        output_format: str = "dict",
    ) -> dict[str, Any] | str | None:
        """Look up canonical atom information for one clinical code."""
        return await _run_db(server_runtime.lookup_code, code=code, source=source, resolve_mode=resolve_mode, output_format=output_format)

    @mcp.tool()
    async def lookup_codes(
        codes: list[str],
        sources: list[str],
        resolve_mode: str = "active_only",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        """Look up canonical atom information for clinical codes.

        `sources` can contain one source for all codes, or one source per code.
        """
        return await _run_db(server_runtime.lookup_codes, codes=codes, sources=sources, resolve_mode=resolve_mode, output_format=output_format)

    @mcp.tool()
    async def resolve_codes(
        codes: list[str],
        sources: list[str],
        resolve_mode: str = "historical",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        """Resolve active, historical, obsolete, and NDC code inputs.

        `resolve_mode` accepts 'active_only', 'historical', or 'resolve_current'
        and mirrors the lookup_codes / map_codes matrix.
        """
        return await _run_db(server_runtime.resolve_codes, codes=codes, sources=sources, resolve_mode=resolve_mode, output_format=output_format)

    @mcp.tool()
    async def sources(
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return active code and atom counts by source."""
        return await _run_db(server_runtime.source_stats, sources=sources)

    @mcp.tool()
    async def source_stats(
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return active code and atom counts by source."""
        return await _run_db(server_runtime.source_stats, sources=sources)

    @mcp.tool()
    async def sample_codes(
        sources: list[str] | None = None,
        per_source: int = 10,
    ) -> dict[str, Any]:
        """Return sample active codes by source."""
        return await _run_db(server_runtime.sample_codes, sources=sources, per_source=per_source)

    @mcp.tool()
    async def code_ttys(
        codes: list[str],
        sources: list[str],
    ) -> dict[str, Any]:
        """Return active UMLS atoms and term types for clinical codes."""
        return await _run_db(server_runtime.code_ttys, codes=codes, sources=sources)

    @mcp.tool()
    async def search_names(
        query: str,
        sources: list[str] | None = None,
        tty_filters: list[str] | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search active terminology names."""
        return await _run_db(server_runtime.search_names, query=query, sources=sources, tty_filters=tty_filters, limit=limit)

    @mcp.tool()
    async def search(
        query: str,
        mode: str = "lexical",
        sources: list[str] | None = None,
        count: int = 20,
        result_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Text-to-code search with BM25 (lexical), SapBERT (semantic), or both (hybrid).

        Modes:
        - 'lexical': BM25 token matching (~1ms). Best for known medical terms.
        - 'semantic': SapBERT embeddings + FAISS (~100ms). Catches novel phrasings
          like "high blood sugar" → Hyperglycemia.
        - 'hybrid': BM25 retrieve + SapBERT re-rank (~110ms). Best accuracy.

        result_types: restrict to result categories, e.g. ['condition',
        'medication'] (legacy modes) or canonical result types (canonical
        mode) — same filter as CLI ``search --result-types``.

        Defaults differ from the Python client's terms.search() (mode=None →
        legacy LIKE over all sources, limit=25): MCP defaults to
        mode='lexical' (BM25 canonical displays), count=20 (QC-418).

        Each result includes a match_grade: 'certain', 'probable', or 'possible'.
        """
        from medterm4ds.services.search import (
            CANONICAL_RESULT_TYPES,
            SEARCH_CATEGORIES,
            search as search_service,
        )
        warnings: list[str] = []
        # result_types is enforced SERVICE-SIDE in every mode (canonical
        # filters by canonical_id prefix; lexical/semantic/hybrid restrict the
        # category indexes searched), so `count` caps the FILTERED result set.
        # The former post-filter ran AFTER the service truncated to count and
        # silently discarded the non-matching truncated slots — the same
        # filter-then-limit bug the CLI had.
        requested = list(result_types or [])
        forwarded: list[str] | None = None
        if requested:
            if mode != "canonical":
                # QC-429: the category filter applies to the BM25 `category`
                # field, which only ever holds SEARCH_CATEGORIES values.
                unknown = [t for t in requested if t not in SEARCH_CATEGORIES]
                if unknown:
                    warnings.append(
                        f"result_types values {unknown} are not search "
                        f"categories ({', '.join(SEARCH_CATEGORIES)}); they "
                        "matched no results."
                    )
                forwarded = [t for t in requested if t in SEARCH_CATEGORIES]
            else:
                # canonical() raises ValueError on unknown types; forwarding
                # only the matchable subset keeps the empty-result contract.
                forwarded = [t for t in requested if t in CANONICAL_RESULT_TYPES]
            if not forwarded:
                # Nothing can match — skip the (expensive) service call.
                payload: dict[str, Any] = {"results": []}
                if warnings:
                    payload["warnings"] = warnings
                return payload
        # QC-400: pass the engine so result displays are canonicalized to the
        # engine preferred term — the same convention Python and FHIR $search
        # emit (runs on this server's single db_executor, like every tool).
        results = await _run_db(
            search_service, query, mode=mode, sources=sources, count=count,
            result_types=forwarded, engine=server_runtime.engine,
        )
        payload = {"results": [r.to_dict() for r in results]}
        if warnings:
            payload["warnings"] = warnings
        return payload

    @mcp.tool()
    async def extract(
        text: str,
        format: str = "codes",
        ner_labels: list[str] | None = None,
        result_types: list[str] | None = None,
        mode: str | None = None,
        min_grade: str | None = None,
        include_negated: bool = False,
        include_uncertain: bool = False,
        include_historical: bool = False,
        include_family: bool = False,
    ) -> dict[str, Any]:
        """Extract medical concepts from free text.

        Uses NER + clinical NLP (medspaCy ConText) + terminology search
        (BM25 + SapBERT) to find medical codes in clinical text.

        Parameters:
        - text: Free clinical text to extract from.
        - format: 'codes' (resolve to codes), 'terms' (text spans only), or 'annotated' (inline markup + spans).
        - ner_labels: Override default GLiNER labels (disease, medication, symptom, procedure, lab test, vital).
        - result_types: Filter resolved concepts by result type (condition, medication, drug_class, lab, vital, procedure, vaccine, symptom).
        - mode: Search mode for code resolution (lexical, semantic, hybrid, canonical). Default: canonical (env-configurable via MEDTERM4DS_EXTRACTION_MODE).
        - min_grade: Minimum match grade (certain, exact, probable, possible, broader). Default: certain (env-configurable via MEDTERM4DS_EXTRACTION_MIN_GRADE).
        - include_negated: Include negated mentions (default: excluded).
        - include_uncertain: Include uncertain mentions (default: excluded).
        - include_historical: Include historical mentions (default: excluded).
        - include_family: Include family-history mentions — relatives' conditions (default: excluded).
        """
        from medterm4ds.services.extraction import extract as extract_service
        results = await _run_db(
            extract_service,
            text,
            format=format,
            ner_labels=ner_labels,
            result_types=result_types,
            mode=mode,
            min_grade=min_grade,
            include_negated=include_negated,
            include_uncertain=include_uncertain,
            include_historical=include_historical,
            include_family=include_family,
        )
        # format="annotated" returns a dict (concepts/annotated_text/spans),
        # not a list. QC-164: calling r.to_dict() on the dict's values crashed
        # with "'str' object has no attribute 'to_dict'". Serialize the concept
        # dataclasses like the CLI/FHIR surfaces do.
        if isinstance(results, dict):
            return {
                "concepts": [
                    c.to_dict() if hasattr(c, "to_dict") else c
                    for c in results.get("concepts", [])
                ],
                "annotated_text": results.get("annotated_text", ""),
                "spans": results.get("spans", []),
            }
        return {"results": [r.to_dict() for r in results]}

    @mcp.tool()
    async def discover(
        source_terminology: str,
        code: str | None = None,
        depth: int = 1,
        include_ancestors: bool = False,
        limit: int = 20,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        """Browse a source or a code's local hierarchy.

        include_retired: include retired/editorial-suppressed concepts as
        walk targets on the code branch (default active-only)."""
        return await _run_db(server_runtime.discover, source_terminology=source_terminology, code=code, depth=depth, include_ancestors=include_ancestors, limit=limit, include_retired=include_retired)

    @mcp.tool()
    async def cross_reference(
        code: str,
        from_source: str,
        to_sources: list[str] | None = None,
        mode: str = "exact",
        max_depth: int = 5,
        max_results_per_code: int = 50,
    ) -> dict[str, Any]:
        """Map one code to target terminology sources."""
        return await _run_db(server_runtime.cross_reference, code=code, from_source=from_source, to_sources=to_sources, mode=mode, max_depth=max_depth, max_results_per_code=max_results_per_code)

    @mcp.tool()
    async def diagnosis_codes(
        condition: str,
        limit: int = 20,
        descendant_depth: int | None = None,
        include_ancestors: bool | None = None,
    ) -> dict[str, Any]:
        """Search diagnosis-oriented ICD-10-CM and SNOMED CT codes."""
        return await _run_db(server_runtime.diagnosis_codes, condition=condition, limit=limit, descendant_depth=descendant_depth, include_ancestors=include_ancestors)

    @mcp.tool()
    async def lab_codes(lab_test: str, limit: int = 20) -> dict[str, Any]:
        """Search lab test terminology sources."""
        return await _run_db(server_runtime.lab_codes, lab_test=lab_test, limit=limit)

    @mcp.tool()
    async def lab_value_codes(clinical_value: str, limit: int = 20) -> dict[str, Any]:
        """Search lab value or clinical finding terminology sources."""
        return await _run_db(server_runtime.lab_value_codes, clinical_value=clinical_value, limit=limit)

    @mcp.tool()
    async def procedure_codes(
        procedure: str,
        limit: int = 20,
        descendant_depth: int | None = None,
        include_ancestors: bool | None = None,
    ) -> dict[str, Any]:
        """Search procedure terminology sources."""
        return await _run_db(server_runtime.procedure_codes, procedure=procedure, limit=limit, descendant_depth=descendant_depth, include_ancestors=include_ancestors)

    @mcp.tool()
    async def hcpcs_drugs(drug_name: str, limit: int = 20) -> dict[str, Any]:
        """Search HCPCS drug/device codes."""
        return await _run_db(server_runtime.hcpcs_drugs, drug_name=drug_name, limit=limit)

    @mcp.tool()
    async def vaccine_codes(vaccine: str, limit: int = 20) -> dict[str, Any]:
        """Search vaccine-oriented CVX, RxNorm, and HCPCS codes."""
        return await _run_db(server_runtime.vaccine_codes, vaccine=vaccine, limit=limit)

    @mcp.tool()
    async def search_drug(
        drug_name: str,
        limit: int = 20,
        tty_filters: list[str] | None = None,
        include_equivalents: bool = True,
        include_ndc: bool = False,
    ) -> dict[str, Any]:
        """Search RxNorm drug names."""
        return await _run_db(server_runtime.search_drug, drug_name=drug_name, limit=limit, tty_filters=tty_filters, include_equivalents=include_equivalents, include_ndc=include_ndc)

    @mcp.tool()
    async def drugs_by_class(class_id: str, limit: int = 20) -> dict[str, Any]:
        """Search ATC/RxNorm class names or class identifiers."""
        return await _run_db(server_runtime.drugs_by_class, class_id=class_id, limit=limit)

    @mcp.tool()
    async def drugs_for_indication(
        condition: str,
        limit: int = 20,
        source: str | None = None,
        code: str | None = None,
        relationship_types: list[str] | None = None,
        max_depth: int = 5,
        include_product_groups: bool = True,
    ) -> dict[str, Any]:
        """Return UMLS relationship-backed medications for a condition."""
        return await _run_db(server_runtime.drugs_for_indication, condition=condition, limit=limit, source=source, code=code, relationship_types=relationship_types, max_depth=max_depth, include_product_groups=include_product_groups)

    @mcp.tool()
    async def indication_search(indication: str, limit: int = 20) -> dict[str, Any]:
        """Search indication evidence when an external evidence adapter is available."""
        return await _run_db(server_runtime.indication_search, indication=indication, limit=limit)

    @mcp.tool()
    async def fda_label_by_rxcui(rxcui: str) -> dict[str, Any]:
        """Fetch FDA label evidence when an external evidence adapter is available."""
        return await _run_db(server_runtime.fda_label_by_rxcui, rxcui=rxcui)

    @mcp.tool()
    async def guideline_search(query: str, limit: int = 20) -> dict[str, Any]:
        """Search guideline evidence when an external evidence adapter is available."""
        return await _run_db(server_runtime.guideline_search, query=query, limit=limit)

    @mcp.tool()
    async def guideline_recommendations(topic: str, limit: int = 20) -> dict[str, Any]:
        """Fetch guideline recommendations when an external evidence adapter is available."""
        return await _run_db(server_runtime.guideline_recommendations, topic=topic, limit=limit)

    @mcp.tool()
    async def guideline_fulltext(guideline_id: str) -> dict[str, Any]:
        """Fetch guideline full text when an external evidence adapter is available."""
        return await _run_db(server_runtime.guideline_fulltext, guideline_id=guideline_id)

    @mcp.tool()
    async def guidelines_for_code(code: str, source: str, limit: int = 20) -> dict[str, Any]:
        """Fetch guideline evidence for a code when an external adapter is available."""
        return await _run_db(server_runtime.guidelines_for_code, code=code, source=source, limit=limit)

    @mcp.tool()
    async def code_relations(
        codes: list[str],
        sources: list[str],
        direction: str,
        max_depth: int = 5,
        limit: int = DEFAULT_DESCENDANT_LIMIT,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        """Return parent, child, ancestor, or descendant relationships.

        Descendant requests are layer-bounded (BFS) with a per-code result
        cap (default 1000); the response carries "truncated": true when more
        descendants existed beyond the cap or the depth limit.

        include_retired: include retired/editorial-suppressed concepts as
        walk targets (default active-only)."""
        return await _run_db(server_runtime.code_relations, codes=codes, sources=sources, direction=direction, max_depth=max_depth, limit=limit, include_retired=include_retired)

    @mcp.tool()
    async def map_codes(
        codes: list[str],
        sources: list[str],
        target_sources: list[str],
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
        resolve_mode: str = "active_only",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        """Map clinical codes to target vocabularies using active same-CUI atoms."""
        return await _run_db(server_runtime.map_codes, codes=codes, sources=sources, target_sources=target_sources, max_results_per_code=max_results_per_code, max_depth=max_depth, include_target_ancestors=include_target_ancestors, include_target_descendants=include_target_descendants, resolve_mode=resolve_mode, output_format=output_format)

    @mcp.tool()
    async def optimize(
        codes: list[str],
        source: str,
        relationship: str | None = None,
        output_format: str = "dict",
        rule_format: str = "compact",
        include_codes: bool = False,
    ) -> dict[str, Any] | str:
        """Optimize a valueset into compact hierarchy include/exclude rules.

        Note: output_format here is the RENDER format (dict/table/tree); the
        rule format is rule_format (compact/flat). Passing
        output_format='compact'/'flat' (the Python/CLI rule-format
        vocabulary) is accepted as a rule_format alias."""
        return await _run_db(server_runtime.optimize, codes=codes, source=source, relationship=relationship, output_format=output_format, rule_format=rule_format, include_codes=include_codes)

    @mcp.tool()
    async def get_parents(
        codes: list[str],
        sources: list[str],
        include_retired: bool = False,
    ) -> dict[str, Any]:
        """Return direct parent relationships for clinical codes.

        include_retired: include retired/editorial-suppressed concepts as
        walk targets (default active-only)."""
        return await _run_db(server_runtime.parents, codes=codes, sources=sources, include_retired=include_retired)

    @mcp.tool()
    async def get_children(
        codes: list[str],
        sources: list[str],
        include_retired: bool = False,
    ) -> dict[str, Any]:
        """Return direct child relationships for clinical codes.

        include_retired: include retired/editorial-suppressed concepts as
        walk targets (default active-only)."""
        return await _run_db(server_runtime.children, codes=codes, sources=sources, include_retired=include_retired)

    @mcp.tool()
    async def get_ancestors(
        codes: list[str],
        sources: list[str],
        max_depth: int = 5,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        """Return ancestor relationships for clinical codes.

        include_retired: include retired/editorial-suppressed concepts as
        walk targets (default active-only)."""
        return await _run_db(server_runtime.ancestors, codes=codes, sources=sources, max_depth=max_depth, include_retired=include_retired)

    @mcp.tool()
    async def get_descendants(
        codes: list[str],
        sources: list[str],
        max_depth: int = 5,
        limit: int = DEFAULT_DESCENDANT_LIMIT,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        """Return descendant relationships for clinical codes.

        Expansion is layer-bounded (BFS) with a per-code result cap (default
        1000) so wide SNOMED subtrees answer in bounded time instead of
        enumerating every path; "truncated": true signals more descendants
        existed beyond the cap or the depth limit.

        include_retired: include retired/editorial-suppressed concepts as
        walk targets (default active-only)."""
        return await _run_db(server_runtime.descendants, codes=codes, sources=sources, max_depth=max_depth, limit=limit, include_retired=include_retired)

    @mcp.tool()
    async def patient_friendly_names(
        codes: list[str],
        sources: list[str],
        max_depth: int = 5,
        resolve_mode: str = "active_only",
        output_format: str = "dict",
    ) -> dict[str, Any] | str:
        """Resolve multiple clinical codes to patient-friendly names.

        `sources` can contain one source for all codes, or one source per code.

        resolve_mode: 'active_only' (default; matches the Python client —
        obsolete codes return no-match rows), 'resolve_current' (follow each
        code's replacement to an active concept), or 'historical'.
        """
        return await _run_db(server_runtime.patient_friendly_names, codes=codes, sources=sources, max_depth=max_depth, resolve_mode=resolve_mode, output_format=output_format)

    @mcp.tool()
    async def patient_friendly_concept_map(
        codes: list[str],
        sources: list[str],
        max_depth: int = 5,
        batch_size: int = 5000,
        target_source: str = "PATIENT_FRIENDLY",
    ) -> dict[str, Any]:
        """Generate patient-friendly ConceptMap rows for clinical codes."""
        return await _run_db(server_runtime.patient_friendly_concept_map, codes=codes, sources=sources, max_depth=max_depth, batch_size=batch_size, target_source=target_source)

    return mcp


def main() -> None:
    """Run the MCP server over stdio."""
    create_mcp_server().run()


def build_code_refs(codes: Sequence[str], sources: Sequence[str]) -> list[CodeRef]:
    # QC-050/QC-060 (MEDIUM): symmetric input validation. Pre-fix,
    # ``codes=None`` crashed with ``TypeError: object of type 'NoneType'
    # has no len()`` (raw leak) and ``codes=[]`` silently returned
    # ``{'results': []}`` while ``sources=[]`` raised ValueError —
    # asymmetric. Now both empty-codes and empty-sources raise clean
    # ValueErrors. Sibling of EC-02 FIX-003/004 (mapping validation).
    if codes is None or not isinstance(codes, Sequence):
        raise TypeError(f"codes must be a sequence, got {type(codes).__name__}")
    if not codes:
        raise ValueError("codes must not be empty")
    if not sources:
        raise ValueError("sources must not be empty")
    if len(sources) == 1:
        sources = list(sources) * len(codes)
    if len(codes) != len(sources):
        raise ValueError("sources must contain either one source or one source per code")
    # QC-052 (MEDIUM): per-code validation. Pre-fix, a single None entry in
    # codes crashed inside CodeRef.__post_init__ with a raw TypeError that
    # failed the whole batch. The bug note suggests per-code isolation
    # (skip None entries), but that's a silent-fallback anti-pattern per
    # GLOBAL_RULES.md. Instead, surface a clear TypeError pointing at the
    # bad entry so callers fix their input rather than getting silent
    # partial results.
    refs: list[CodeRef] = []
    for code, source in zip(codes, sources, strict=True):
        if not isinstance(code, str):
            raise TypeError(
                f"each code must be a string, got {type(code).__name__} "
                f"in entry {code!r}"
            )
        _validate_source_sab(source)
        # QC-324 (LOW): an empty code is never a valid invocation — reject
        # with a diagnostic instead of a success-shaped null-field record.
        if not code.strip():
            raise ValueError(
                f"code must be a non-empty string in entry {code!r}"
            )
        refs.append(CodeRef(source=source, code=code))
    return refs


def _validate_source_sab(source: Any) -> None:
    # QC-322 (MEDIUM): reject URI/OID-form source (e.g.
    # 'http://snomed.info/sct') with a clear, actionable error — mirrors the
    # CLI guard (cli.py:_code_source_pairs, QC-011/FIX-010). Pre-fix, an MCP
    # client reusing FHIR system URIs got a silent None/[] that was
    # indistinguishable from an unknown-code result, while the same input
    # succeeds on the FHIR surface and is rejected with a diagnostic on the
    # CLI surface.
    #
    # QC-389 (MEDIUM): reject empty/whitespace source. Pre-fix,
    # lookup_code(code=..., source='') returned a success-shaped null-field
    # record while the FHIR surface answered 422 on the same input. Guard
    # here covers build_code_refs (batch tools) and
    # _validate_single_code_inputs (single-code tools).
    if isinstance(source, str):
        if not source.strip():
            raise ValueError(
                "source must be a non-empty vocabulary name (UMLS SAB, "
                f"e.g. SNOMEDCT_US), got {source!r}."
            )
        if "://" in source or source.lower().startswith("urn:oid:"):
            raise ValueError(
                f"source expects a UMLS SAB string (e.g. SNOMEDCT_US), got "
                f"{source!r} (looks like a URI/OID). FHIR URIs are not accepted "
                f"here; use the SAB form."
            )


def _validate_single_code_inputs(*, code: Any, source: Any) -> None:
    # QC-078/QC-086 (MEDIUM): single-code MCP tools (patient_friendly_name,
    # cross_reference, optimize, guidelines_for_code) construct CodeRef
    # directly, bypassing build_code_refs validation. CodeRef.__post_init__
    # raises a raw TypeError whose message leaks the impl detail
    # ("CodeRef.code must be a string, got NoneType"). Surface a clean
    # TypeError with a clear message before the CodeRef construction.
    # Sibling of EC-03 FIX-006 (build_code_refs validation).
    if not isinstance(code, str):
        raise TypeError(
            f"code must be a string, got {type(code).__name__}"
        )
    if not isinstance(source, str):
        raise TypeError(
            f"source must be a string, got {type(source).__name__}"
        )
    _validate_source_sab(source)
    # QC-324 (LOW): empty code is never a valid invocation (7th PROMOTED
    # pattern rationale) — only the FHIR surface rejected it before.
    if not code.strip():
        raise ValueError("code must be a non-empty string")


def _env_int(name: str) -> int | None:
    # QC-467 (LOW): superseded by medterm4ds.core.env.env_int — kept as a
    # thin alias for any external callers of the private name.
    return env_int(name)


def _env_bool(name: str, default: bool) -> bool:
    # QC-467: superseded by medterm4ds.core.env.env_bool.
    return env_bool(name, default)


if __name__ == "__main__":
    main()
