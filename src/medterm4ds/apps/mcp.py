"""MCP server for medterm4ds."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Sequence

from medterm4ds.core.config import MemoryProfile, local_lite_config
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalLiteEngine
from medterm4ds.services.conceptmap import get_concept_map
from medterm4ds.services.inventory import DEFAULT_INVENTORY_SOURCES, normalize_sources
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
    def from_env(cls) -> "McpSettings":
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
        for code, source in zip(codes, sources)
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
