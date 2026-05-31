"""MCP server for medterm4ds."""

from __future__ import annotations

import os
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from medterm4ds.core.config import MemoryProfile, local_lite_config
from medterm4ds.core.models import CodeRef
from medterm4ds.domains import evidence as evidence_domain
from medterm4ds.domains import terminology as terminology_domain
from medterm4ds.engines.duckdb import LocalLiteEngine
from medterm4ds.services.conceptmap import get_concept_map
from medterm4ds.services.discovery import (
    get_code_ttys,
    get_source_stats,
    sample_source_codes,
    search_names,
)
from medterm4ds.services.hierarchy import get_code_relations
from medterm4ds.services.inventory import DEFAULT_INVENTORY_SOURCES, normalize_sources
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.mapping import get_code_mappings
from medterm4ds.services.patient_friendly import get_patient_friendly_names

try:
    import duckdb
    from fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised only without mcp extras.
    raise ImportError("Install medterm4ds[mcp] to use medterm4ds.apps.mcp.") from exc


@dataclass(frozen=True)
class McpSettings:
    """Single-database MCP process settings."""

    db_path: Path
    sources: tuple[str, ...] = DEFAULT_INVENTORY_SOURCES
    memory_profile: MemoryProfile = "balanced"
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
            memory_profile=os.getenv("MEDTERM4DS_MEMORY_PROFILE", "balanced"),  # type: ignore[arg-type]
            memory_limit=os.getenv("MEDTERM4DS_MEMORY_LIMIT") or None,
            temp_directory=os.getenv("MEDTERM4DS_TEMP_DIR") or None,
            threads=_env_int("MEDTERM4DS_THREADS"),
            query_chunk_size=_env_int("MEDTERM4DS_QUERY_CHUNK_SIZE"),
            prepare_cache=_env_bool("MEDTERM4DS_PREPARE_CACHE", True),
            cache_indexes=_env_bool("MEDTERM4DS_CACHE_INDEXES", False),
        )


class McpRuntime:
    """Owns the configured DuckDB connection and LocalLite engine."""

    def __init__(self, settings: McpSettings):
        self.settings = settings
        self.con = None
        self.engine: LocalLiteEngine | None = None

    @property
    def ready(self) -> bool:
        return self.engine is not None

    def open(self) -> None:
        if self.ready:
            return
        if not self.settings.db_path.exists():
            raise RuntimeError(f"Database not found: {self.settings.db_path}")
        self.con = duckdb.connect(str(self.settings.db_path), read_only=True)
        config = local_lite_config(
            self.settings.memory_profile,
            memory_limit=self.settings.memory_limit,
            temp_directory=self.settings.temp_directory,
            threads=self.settings.threads,
            query_chunk_size=self.settings.query_chunk_size,
        )
        self.engine = LocalLiteEngine(self.con, config=config)
        if self.settings.prepare_cache:
            self.engine.prepare_cache(self.settings.sources, create_indexes=self.settings.cache_indexes)

    def close(self) -> None:
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
    ) -> dict[str, Any]:
        result = get_patient_friendly_names(
            [CodeRef(source=source, code=code)],
            engine=self._engine(),
            max_depth=max_depth,
        )[0]
        return result.to_dict()

    def lookup_code(
        self,
        *,
        code: str,
        source: str,
    ) -> dict[str, Any] | None:
        return self.lookup_codes(codes=[code], sources=[source])["results"][0]

    def lookup_codes(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
    ) -> dict[str, Any]:
        refs = _code_refs(codes, sources)
        infos = get_code_infos(refs, engine=self._engine())
        return {"results": [info.to_dict() if info else None for info in infos]}

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
        refs = _code_refs(codes, sources)
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
    ) -> dict[str, Any]:
        return terminology_domain.discover(
            source_terminology,
            engine=self._engine(),
            code=code,
            depth=depth,
            include_ancestors=include_ancestors,
            limit=limit,
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
        return terminology_domain.cross_reference(
            code,
            from_source,
            engine=self._engine(),
            to_sources=to_sources,
            mode=mode,
            max_depth=max_depth,
            max_results_per_code=max_results_per_code,
        )

    def diagnosis_codes(self, *, condition: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.diagnosis_codes(condition, engine=self._engine(), limit=limit)

    def lab_codes(self, *, lab_test: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.lab_codes(lab_test, engine=self._engine(), limit=limit)

    def lab_value_codes(self, *, clinical_value: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.lab_value_codes(clinical_value, engine=self._engine(), limit=limit)

    def procedure_codes(self, *, procedure: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.procedure_codes(procedure, engine=self._engine(), limit=limit)

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
    ) -> dict[str, Any]:
        return terminology_domain.search_drug(
            drug_name,
            engine=self._engine(),
            limit=limit,
            tty_filters=tty_filters,
        )

    def drugs_by_class(self, *, class_id: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.drugs_by_class(class_id, engine=self._engine(), limit=limit)

    def drugs_for_indication(self, *, condition: str, limit: int = 20) -> dict[str, Any]:
        return terminology_domain.drugs_for_indication(condition, engine=self._engine(), limit=limit)

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
        return evidence_domain.guidelines_for_code(code, source, limit=limit)

    def code_relations(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        direction: str,
        max_depth: int = 5,
    ) -> dict[str, Any]:
        refs = _code_refs(codes, sources)
        relations = get_code_relations(
            refs,
            engine=self._engine(),
            direction=direction,
            max_depth=max_depth,
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
    ) -> dict[str, Any]:
        refs = _code_refs(codes, sources)
        mappings = get_code_mappings(
            refs,
            engine=self._engine(),
            target_sources=target_sources,
            max_results_per_code=max_results_per_code,
            max_depth=max_depth,
            include_target_ancestors=include_target_ancestors,
            include_target_descendants=include_target_descendants,
        )
        return {"results": [mapping.to_dict() for mapping in mappings]}

    def parents(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
    ) -> dict[str, Any]:
        return self.code_relations(codes=codes, sources=sources, direction="parents", max_depth=1)

    def children(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
    ) -> dict[str, Any]:
        return self.code_relations(codes=codes, sources=sources, direction="children", max_depth=1)

    def ancestors(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        max_depth: int = 5,
    ) -> dict[str, Any]:
        return self.code_relations(
            codes=codes,
            sources=sources,
            direction="ancestors",
            max_depth=max_depth,
        )

    def descendants(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        max_depth: int = 5,
    ) -> dict[str, Any]:
        return self.code_relations(
            codes=codes,
            sources=sources,
            direction="descendants",
            max_depth=max_depth,
        )

    def patient_friendly_names(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        max_depth: int = 5,
    ) -> dict[str, Any]:
        refs = _code_refs(codes, sources)
        results = get_patient_friendly_names(refs, engine=self._engine(), max_depth=max_depth)
        return {"results": [result.to_dict() for result in results]}

    def patient_friendly_concept_map(
        self,
        *,
        codes: Sequence[str],
        sources: Sequence[str],
        max_depth: int = 5,
        batch_size: int = 5000,
        target_source: str = "PATIENT_FRIENDLY",
    ) -> dict[str, Any]:
        refs = _code_refs(codes, sources)
        rows = get_concept_map(
            refs,
            engine=self._engine(),
            max_depth=max_depth,
            batch_size=batch_size,
            target_source=target_source,
        )
        return {"results": [row.to_dict() for row in rows]}

    def _engine(self) -> LocalLiteEngine:
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

    @asynccontextmanager
    async def lifespan(_mcp: FastMCP):
        server_runtime.open()
        try:
            yield
        finally:
            server_runtime.close()

    mcp = FastMCP(
        "medterm4ds",
        instructions="Batch-first medical terminology tools backed by medterm4ds LocalLite.",
        lifespan=lifespan,
    )

    @mcp.tool()
    async def health() -> dict[str, Any]:
        """Return MCP terminology engine readiness and configuration."""
        return server_runtime.health()

    @mcp.tool()
    async def patient_friendly_name(
        code: str,
        source: str,
        max_depth: int = 5,
    ) -> dict[str, Any]:
        """Resolve one clinical code to a patient-friendly name."""
        return server_runtime.patient_friendly_name(
            code=code,
            source=source,
            max_depth=max_depth,
        )

    @mcp.tool()
    async def lookup_code(
        code: str,
        source: str,
    ) -> dict[str, Any] | None:
        """Look up canonical atom information for one clinical code."""
        return server_runtime.lookup_code(code=code, source=source)

    @mcp.tool()
    async def lookup_codes(
        codes: list[str],
        sources: list[str],
    ) -> dict[str, Any]:
        """Look up canonical atom information for clinical codes.

        `sources` can contain one source for all codes, or one source per code.
        """
        return server_runtime.lookup_codes(codes=codes, sources=sources)

    @mcp.tool()
    async def sources(
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return active code and atom counts by source."""
        return server_runtime.source_stats(sources=sources)

    @mcp.tool()
    async def source_stats(
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return active code and atom counts by source."""
        return server_runtime.source_stats(sources=sources)

    @mcp.tool()
    async def sample_codes(
        sources: list[str] | None = None,
        per_source: int = 10,
    ) -> dict[str, Any]:
        """Return sample active codes by source."""
        return server_runtime.sample_codes(sources=sources, per_source=per_source)

    @mcp.tool()
    async def code_ttys(
        codes: list[str],
        sources: list[str],
    ) -> dict[str, Any]:
        """Return active UMLS atoms and term types for clinical codes."""
        return server_runtime.code_ttys(codes=codes, sources=sources)

    @mcp.tool()
    async def search_names(
        query: str,
        sources: list[str] | None = None,
        tty_filters: list[str] | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search active terminology names."""
        return server_runtime.search_names(
            query=query,
            sources=sources,
            tty_filters=tty_filters,
            limit=limit,
        )

    @mcp.tool()
    async def discover(
        source_terminology: str,
        code: str | None = None,
        depth: int = 1,
        include_ancestors: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Browse a source or a code's local hierarchy."""
        return server_runtime.discover(
            source_terminology=source_terminology,
            code=code,
            depth=depth,
            include_ancestors=include_ancestors,
            limit=limit,
        )

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
        return server_runtime.cross_reference(
            code=code,
            from_source=from_source,
            to_sources=to_sources,
            mode=mode,
            max_depth=max_depth,
            max_results_per_code=max_results_per_code,
        )

    @mcp.tool()
    async def diagnosis_codes(condition: str, limit: int = 20) -> dict[str, Any]:
        """Search diagnosis-oriented ICD-10-CM and SNOMED CT codes."""
        return server_runtime.diagnosis_codes(condition=condition, limit=limit)

    @mcp.tool()
    async def lab_codes(lab_test: str, limit: int = 20) -> dict[str, Any]:
        """Search lab test terminology sources."""
        return server_runtime.lab_codes(lab_test=lab_test, limit=limit)

    @mcp.tool()
    async def lab_value_codes(clinical_value: str, limit: int = 20) -> dict[str, Any]:
        """Search lab value or clinical finding terminology sources."""
        return server_runtime.lab_value_codes(clinical_value=clinical_value, limit=limit)

    @mcp.tool()
    async def procedure_codes(procedure: str, limit: int = 20) -> dict[str, Any]:
        """Search procedure terminology sources."""
        return server_runtime.procedure_codes(procedure=procedure, limit=limit)

    @mcp.tool()
    async def hcpcs_drugs(drug_name: str, limit: int = 20) -> dict[str, Any]:
        """Search HCPCS drug/device codes."""
        return server_runtime.hcpcs_drugs(drug_name=drug_name, limit=limit)

    @mcp.tool()
    async def vaccine_codes(vaccine: str, limit: int = 20) -> dict[str, Any]:
        """Search vaccine-oriented CVX, RxNorm, and HCPCS codes."""
        return server_runtime.vaccine_codes(vaccine=vaccine, limit=limit)

    @mcp.tool()
    async def search_drug(
        drug_name: str,
        limit: int = 20,
        tty_filters: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search RxNorm drug names."""
        return server_runtime.search_drug(
            drug_name=drug_name,
            limit=limit,
            tty_filters=tty_filters,
        )

    @mcp.tool()
    async def drugs_by_class(class_id: str, limit: int = 20) -> dict[str, Any]:
        """Search ATC/RxNorm class names or class identifiers."""
        return server_runtime.drugs_by_class(class_id=class_id, limit=limit)

    @mcp.tool()
    async def drugs_for_indication(condition: str, limit: int = 20) -> dict[str, Any]:
        """Return UMLS-backed indication context for drug workflows."""
        return server_runtime.drugs_for_indication(condition=condition, limit=limit)

    @mcp.tool()
    async def indication_search(indication: str, limit: int = 20) -> dict[str, Any]:
        """Search indication evidence when an external evidence adapter is available."""
        return server_runtime.indication_search(indication=indication, limit=limit)

    @mcp.tool()
    async def fda_label_by_rxcui(rxcui: str) -> dict[str, Any]:
        """Fetch FDA label evidence when an external evidence adapter is available."""
        return server_runtime.fda_label_by_rxcui(rxcui=rxcui)

    @mcp.tool()
    async def guideline_search(query: str, limit: int = 20) -> dict[str, Any]:
        """Search guideline evidence when an external evidence adapter is available."""
        return server_runtime.guideline_search(query=query, limit=limit)

    @mcp.tool()
    async def guideline_recommendations(topic: str, limit: int = 20) -> dict[str, Any]:
        """Fetch guideline recommendations when an external evidence adapter is available."""
        return server_runtime.guideline_recommendations(topic=topic, limit=limit)

    @mcp.tool()
    async def guideline_fulltext(guideline_id: str) -> dict[str, Any]:
        """Fetch guideline full text when an external evidence adapter is available."""
        return server_runtime.guideline_fulltext(guideline_id=guideline_id)

    @mcp.tool()
    async def guidelines_for_code(code: str, source: str, limit: int = 20) -> dict[str, Any]:
        """Fetch guideline evidence for a code when an external adapter is available."""
        return server_runtime.guidelines_for_code(code=code, source=source, limit=limit)

    @mcp.tool()
    async def code_relations(
        codes: list[str],
        sources: list[str],
        direction: str,
        max_depth: int = 5,
    ) -> dict[str, Any]:
        """Return parent, child, ancestor, or descendant relationships."""
        return server_runtime.code_relations(
            codes=codes,
            sources=sources,
            direction=direction,
            max_depth=max_depth,
        )

    @mcp.tool()
    async def map_codes(
        codes: list[str],
        sources: list[str],
        target_sources: list[str],
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
    ) -> dict[str, Any]:
        """Map clinical codes to target vocabularies using active same-CUI atoms."""
        return server_runtime.map_codes(
            codes=codes,
            sources=sources,
            target_sources=target_sources,
            max_results_per_code=max_results_per_code,
            max_depth=max_depth,
            include_target_ancestors=include_target_ancestors,
            include_target_descendants=include_target_descendants,
        )

    @mcp.tool()
    async def get_parents(
        codes: list[str],
        sources: list[str],
    ) -> dict[str, Any]:
        """Return direct parent relationships for clinical codes."""
        return server_runtime.parents(codes=codes, sources=sources)

    @mcp.tool()
    async def get_children(
        codes: list[str],
        sources: list[str],
    ) -> dict[str, Any]:
        """Return direct child relationships for clinical codes."""
        return server_runtime.children(codes=codes, sources=sources)

    @mcp.tool()
    async def get_ancestors(
        codes: list[str],
        sources: list[str],
        max_depth: int = 5,
    ) -> dict[str, Any]:
        """Return ancestor relationships for clinical codes."""
        return server_runtime.ancestors(codes=codes, sources=sources, max_depth=max_depth)

    @mcp.tool()
    async def get_descendants(
        codes: list[str],
        sources: list[str],
        max_depth: int = 5,
    ) -> dict[str, Any]:
        """Return descendant relationships for clinical codes."""
        return server_runtime.descendants(codes=codes, sources=sources, max_depth=max_depth)

    @mcp.tool()
    async def patient_friendly_names(
        codes: list[str],
        sources: list[str],
        max_depth: int = 5,
    ) -> dict[str, Any]:
        """Resolve multiple clinical codes to patient-friendly names.

        `sources` can contain one source for all codes, or one source per code.
        """
        return server_runtime.patient_friendly_names(
            codes=codes,
            sources=sources,
            max_depth=max_depth,
        )

    @mcp.tool()
    async def patient_friendly_concept_map(
        codes: list[str],
        sources: list[str],
        max_depth: int = 5,
        batch_size: int = 5000,
        target_source: str = "PATIENT_FRIENDLY",
    ) -> dict[str, Any]:
        """Generate patient-friendly ConceptMap rows for clinical codes."""
        return server_runtime.patient_friendly_concept_map(
            codes=codes,
            sources=sources,
            max_depth=max_depth,
            batch_size=batch_size,
            target_source=target_source,
        )

    return mcp


def main() -> None:
    """Run the MCP server over stdio."""
    create_mcp_server().run()


def _code_refs(codes: Sequence[str], sources: Sequence[str]) -> list[CodeRef]:
    if not sources:
        raise ValueError("sources must not be empty")
    if len(sources) == 1:
        sources = list(sources) * len(codes)
    if len(codes) != len(sources):
        raise ValueError("sources must contain either one source or one source per code")
    return [
        CodeRef(source=source, code=code)
        for code, source in zip(codes, sources, strict=True)
    ]


def _env_int(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value else None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    main()
