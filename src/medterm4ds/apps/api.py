"""FastAPI application for medterm4ds."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from medterm4ds.core.config import MemoryProfile, local_duckdb_config
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
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
from medterm4ds.services.optimize import optimize_codes
from medterm4ds.services.patient_friendly import get_patient_friendly_names
from medterm4ds.services.resolution import resolve_codes

try:
    import duckdb
    from fastapi import FastAPI, HTTPException, Request
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - exercised only without api extras.
    raise ImportError("Install medterm4ds[api] to use medterm4ds.apps.api.") from exc


@dataclass(frozen=True)
class ApiSettings:
    """Single-database API process settings."""

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
    def from_env(cls) -> ApiSettings:
        db_path = os.getenv("MEDTERM4DS_DB")
        if not db_path:
            raise RuntimeError("MEDTERM4DS_DB is required for the API app.")
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


class CodeInput(BaseModel):
    source: str
    code: str

    def to_ref(self) -> CodeRef:
        return CodeRef(source=self.source, code=self.code)


# Cap the number of codes per batch request so a single misbehaving local
# client cannot lock the read-only DuckDB connection with a 100k-code POST.
# 10k is generous; downstream notebooks typically batch in chunks of 100-1000.
MAX_CODES_PER_REQUEST = 10_000


class PatientFriendlyRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)
    max_depth: int = Field(default=5, ge=0)


class LookupRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)
    resolve_mode: str = "active_only"


class SourceStatsRequest(BaseModel):
    sources: list[str] | None = None


class SampleCodesRequest(BaseModel):
    sources: list[str] | None = None
    per_source: int = Field(default=10, ge=1)


class CodeTtysRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)


class SearchNamesRequest(BaseModel):
    query: str = Field(min_length=1, max_length=256)
    sources: list[str] | None = None
    tty_filters: list[str] | None = None
    limit: int = Field(default=25, ge=1)


class HierarchyRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)
    direction: Literal["parents", "children", "ancestors", "descendants"]
    max_depth: int = Field(default=5, ge=1)


class MappingRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)
    target_sources: list[str] = Field(default_factory=list, min_length=1)
    max_results_per_code: int = Field(default=50, ge=1)
    max_depth: int = Field(default=0, ge=0)
    include_target_ancestors: bool = False
    include_target_descendants: bool = False
    resolve_mode: str = "active_only"


class ConceptMapRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)
    max_depth: int = Field(default=5, ge=0)
    batch_size: int = Field(default=5000, ge=1)
    target_source: str = "PATIENT_FRIENDLY"


class ResolveRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)


class OptimizeRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)
    relationship: str | None = None
    output_format: Literal["compact", "flat"] = "compact"
    include_codes: bool = False


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Create a single-database FastAPI app."""
    app_settings = settings or ApiSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not app_settings.db_path.exists():
            raise RuntimeError(f"Database not found: {app_settings.db_path}")

        con = duckdb.connect(str(app_settings.db_path), read_only=True)
        config = local_duckdb_config(
            app_settings.memory_profile,
            memory_limit=app_settings.memory_limit,
            temp_directory=app_settings.temp_directory,
            threads=app_settings.threads,
            query_chunk_size=app_settings.query_chunk_size,
        )
        engine = LocalDuckDBEngine(con, config=config)
        if app_settings.prepare_cache:
            engine.prepare_cache(app_settings.sources, create_indexes=app_settings.cache_indexes)

        app.state.con = con
        app.state.engine = engine
        app.state.settings = app_settings
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            con.close()

    app = FastAPI(
        title="medterm4ds",
        version="0.0.1",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        """Readiness check. Sanitized: does NOT leak DB filesystem path.

        Local processes needing the path can read MEDTERM4DS_DB from the env
        block they passed to the server. External probes (which shouldn't
        reach this server -- it binds to localhost only) get only readiness.
        """
        ready = bool(getattr(request.app.state, "ready", False))
        return {
            "status": "ok" if ready else "starting",
            "ready": ready,
            "sources": list(app_settings.sources),
            "memory_profile": app_settings.memory_profile,
            "cache_prepared": bool(getattr(_engine(request), "cache_prepared", False)) if ready else False,
        }

    @app.post("/patient-friendly")
    async def patient_friendly(
        payload: PatientFriendlyRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        results = get_patient_friendly_names(
            [code.to_ref() for code in payload.codes],
            engine=engine,
            max_depth=payload.max_depth,
            resolve_mode="resolve_current",
        )
        return {"results": [result.to_dict() for result in results]}

    @app.post("/lookup")
    async def lookup(
        payload: LookupRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        results = get_code_infos(
            [code.to_ref() for code in payload.codes],
            engine=engine,
            resolve_mode=payload.resolve_mode,
        )
        return {"results": [result.to_dict() if result else None for result in results]}

    @app.post("/resolve")
    async def resolve(
        payload: ResolveRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        results = resolve_codes([code.to_ref() for code in payload.codes], engine=engine)
        return {"results": [result.to_dict() for result in results]}

    @app.post("/sources")
    async def sources(
        payload: SourceStatsRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        stats = get_source_stats(engine=engine, sources=payload.sources)
        return {"results": [stat.to_dict() for stat in stats]}

    @app.post("/source-stats")
    async def source_stats(
        payload: SourceStatsRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        stats = get_source_stats(engine=engine, sources=payload.sources)
        return {"results": [stat.to_dict() for stat in stats]}

    @app.post("/sample-codes")
    async def sample_codes(
        payload: SampleCodesRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        codes = sample_source_codes(
            engine=engine,
            sources=payload.sources,
            per_source=payload.per_source,
        )
        return {"results": [{"source": code.source, "code": code.code} for code in codes]}

    @app.post("/code-ttys")
    async def code_ttys(
        payload: CodeTtysRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        infos = get_code_ttys([code.to_ref() for code in payload.codes], engine=engine)
        return {"results": [info.to_dict() for info in infos]}

    @app.post("/search-names")
    async def search(
        payload: SearchNamesRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        results = search_names(
            payload.query,
            engine=engine,
            sources=payload.sources,
            tty_filters=payload.tty_filters,
            limit=payload.limit,
        )
        return {"results": [result.to_dict() for result in results]}

    @app.post("/hierarchy")
    async def hierarchy(
        payload: HierarchyRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        results = get_code_relations(
            [code.to_ref() for code in payload.codes],
            engine=engine,
            direction=payload.direction,
            max_depth=payload.max_depth,
        )
        return {"results": [result.to_dict() for result in results]}

    @app.post("/map")
    async def map_codes(
        payload: MappingRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        results = get_code_mappings(
            [code.to_ref() for code in payload.codes],
            engine=engine,
            target_sources=payload.target_sources,
            max_results_per_code=payload.max_results_per_code,
            max_depth=payload.max_depth,
            include_target_ancestors=payload.include_target_ancestors,
            include_target_descendants=payload.include_target_descendants,
            resolve_mode=payload.resolve_mode,
        )
        return {"results": [result.to_dict() for result in results]}

    @app.post("/optimize")
    async def optimize(
        payload: OptimizeRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        result = optimize_codes(
            [code.to_ref() for code in payload.codes],
            engine=engine,
            relationship=payload.relationship,
            output_format=payload.output_format,
            include_codes=payload.include_codes,
        )
        return {"result": result.to_dict(include_codes=payload.include_codes)}

    @app.post("/conceptmap/patient-friendly")
    async def conceptmap_patient_friendly(
        payload: ConceptMapRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        rows = get_concept_map(
            [code.to_ref() for code in payload.codes],
            engine=engine,
            batch_size=payload.batch_size,
            max_depth=payload.max_depth,
            target_source=payload.target_source,
        )
        return {"results": [row.to_dict() for row in rows]}

    return app


def _engine(request: Request) -> LocalDuckDBEngine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Terminology engine is not ready.")
    return engine


def _env_int(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value else None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    """Run the API server, bound to localhost by default.

    Binds to 127.0.0.1 so the server is reachable from any process on the
    same host (notebooks, scripts, MCP server, etc.) but NOT from external
    networks. This is the documented exposure model (see SECURITY.md):
    local-only multi-process sidecar. Do NOT pass --host 0.0.0.0 to uvicorn
    unless you've configured an authenticating reverse proxy in front.

    Override with MEDTERM4DS_API_HOST (e.g., for container-internal use
    behind a proxy) -- the process will log a warning on startup.
    """
    import logging

    import uvicorn

    host = os.getenv("MEDTERM4DS_API_HOST", "127.0.0.1")
    port = int(os.getenv("MEDTERM4DS_API_PORT", "8000"))
    if host not in {"127.0.0.1", "::1", "localhost"}:
        logging.getLogger("medterm4ds.apps.api").warning(
            "Binding to %s -- this exposes the API to external networks. "
            "Ensure an authenticating reverse proxy is in front, or set "
            "MEDTERM4DS_API_HOST=127.0.0.1 for local-only access.", host
        )
    uvicorn.run(
        "medterm4ds.apps.api:create_app",
        factory=True,
        host=host,
        port=port,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
