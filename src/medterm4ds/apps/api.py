"""FastAPI application for medterm4ds."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from medterm4ds.core.config import MemoryProfile, local_lite_config
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalLiteEngine
from medterm4ds.services.conceptmap import get_concept_map
from medterm4ds.services.inventory import DEFAULT_INVENTORY_SOURCES, normalize_sources
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.patient_friendly import get_patient_friendly_names

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


class PatientFriendlyRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list)
    max_depth: int = Field(default=5, ge=0)


class LookupRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list)


class ConceptMapRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list)
    max_depth: int = Field(default=5, ge=0)
    batch_size: int = Field(default=5000, ge=1)
    target_source: str = "PATIENT_FRIENDLY"


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Create a single-database FastAPI app."""
    app_settings = settings or ApiSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not app_settings.db_path.exists():
            raise RuntimeError(f"Database not found: {app_settings.db_path}")

        con = duckdb.connect(str(app_settings.db_path), read_only=True)
        config = local_lite_config(
            app_settings.memory_profile,
            memory_limit=app_settings.memory_limit,
            temp_directory=app_settings.temp_directory,
            threads=app_settings.threads,
            query_chunk_size=app_settings.query_chunk_size,
        )
        engine = LocalLiteEngine(con, config=config)
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
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        ready = bool(getattr(request.app.state, "ready", False))
        return {
            "status": "ok" if ready else "starting",
            "ready": ready,
            "database": str(app_settings.db_path),
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
        )
        return {"results": [result.to_dict() if result else None for result in results]}

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


def _engine(request: Request) -> LocalLiteEngine:
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
