"""FastAPI application for medterm4ds."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from medterm4ds import __version__
from medterm4ds.apps._asyncutil import run_db
from medterm4ds.core.config import MemoryProfile, local_duckdb_config
from medterm4ds.core.env import env_bool, env_int, env_str
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
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - exercised only without api extras.
    raise ImportError("Install medterm4ds[api] to use medterm4ds.apps.api.") from exc


@dataclass(frozen=True)
class ApiSettings:
    """Single-database API process settings."""

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
    def from_env(cls) -> ApiSettings:
        db_path = os.getenv("MEDTERM4DS_DB")
        if not db_path:
            raise RuntimeError("MEDTERM4DS_DB is required for the API app.")
        return cls(
            db_path=Path(db_path),
            sources=normalize_sources(os.getenv("MEDTERM4DS_SOURCES")),
            memory_profile=os.getenv("MEDTERM4DS_MEMORY_PROFILE", "fast"),  # type: ignore[arg-type]
            # CR-044 (review-5 finding 6): env_str treats whitespace-only as
            # unset like the facade/CLI; the old ``or None`` idiom passed
            # " " through to validate_memory_limit and crashed at startup.
            memory_limit=env_str("MEDTERM4DS_MEMORY_LIMIT"),
            temp_directory=env_str("MEDTERM4DS_TEMP_DIR"),
            # QC-473 (LOW): minimum=1 rejects MEDTERM4DS_THREADS=-1 /
            # QUERY_CHUNK_SIZE=-5 at settings-parse time naming the env var
            # (pre-fix all three servers crashed in lifespan with an
            # anonymous duckdb SyntaxException).
            threads=env_int("MEDTERM4DS_THREADS", minimum=1),
            query_chunk_size=env_int("MEDTERM4DS_QUERY_CHUNK_SIZE", minimum=1),
            prepare_cache=env_bool("MEDTERM4DS_PREPARE_CACHE", True),
            cache_indexes=env_bool("MEDTERM4DS_CACHE_INDEXES", False),
        )


# QC-474 (MEDIUM): per-item length caps. The batch cap below counts ITEMS,
# not bytes — a 10,000-code POST with 100KB codes was a 1GB body that
# passed validation, cost +1.8GB RSS, and still returned HTTP 200.
_MAX_SOURCE_LENGTH = 64
_MAX_CODE_LENGTH = 256

# QC-489 (MEDIUM): min_length=1 — the PROMOTED empty-string-as-present
# pattern swept onto the api models. Pre-fix, '' passed validation (only
# max_length was set), reached the service layer, and came back as an
# opaque 500 while the same input got a clean 422 on the FHIR surface and a
# ValueError on the Python facade.
SourceStr = Annotated[str, Field(min_length=1, max_length=_MAX_SOURCE_LENGTH)]
CodeStr = Annotated[str, Field(min_length=1, max_length=_MAX_CODE_LENGTH)]
# QC-474 sibling: the free-form filter lists (sources / tty_filters /
# target_sources) carried no per-item caps — a single multi-megabyte string
# per item scales pydantic + service cost with attacker bytes while staying
# under the item-count caps.
# CR-041 (review-5 finding 1, MEDIUM): the LIST itself was unbounded — the
# codes fields cap at MAX_CODES_PER_REQUEST but sources/ttys/target_sources
# accepted any N, so a chunked-encoding body (no Content-Length → the
# MAX_REQUEST_BODY_BYTES middleware gate is skipped) could materialize a
# 100M-item list. Belt-and-braces item-count cap independent of transport.
_MAX_SOURCES_PER_REQUEST = 1_000
SourceList = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=_MAX_SOURCE_LENGTH)]],
    Field(max_length=_MAX_SOURCES_PER_REQUEST),
]


class CodeInput(BaseModel):
    source: SourceStr
    code: CodeStr

    def to_ref(self) -> CodeRef:
        return CodeRef(source=self.source, code=self.code)


# Cap the number of codes per batch request so a single misbehaving local
# client cannot lock the read-only DuckDB connection with a 100k-code POST.
# 10k is generous; downstream notebooks typically batch in chunks of 100-1000.
MAX_CODES_PER_REQUEST = 10_000

# QC-474 (MEDIUM): request-body byte cap enforced on Content-Length before
# parsing. 10k codes at the 256-char per-code cap plus JSON overhead is
# ~3.7MB; 10MB leaves generous headroom for lists of sources/ttys while
# stopping transport+pydantic cost from scaling with attacker-supplied
# bytes (uvicorn/h11 impose no default limit).
MAX_REQUEST_BODY_BYTES = 10_000_000


class PatientFriendlyRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)
    max_depth: int = Field(default=5, ge=0)
    # QC-495 (HIGH): pre-fix the handler hardcoded resolve_mode=
    # 'resolve_current', silently discarding the caller's mode and diverging
    # from the local facade (same call, same DB: local returned match_type=
    # 'none' for an obsolete code, remote returned the resolved
    # replacement's friendly name). Default matches the facade/service
    # default ('active_only') so both legs agree when the field is absent.
    resolve_mode: str = "active_only"


class LookupRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)
    resolve_mode: str = "active_only"


class SourceStatsRequest(BaseModel):
    sources: SourceList | None = None


class SampleCodesRequest(BaseModel):
    sources: SourceList | None = None
    per_source: int = Field(default=10, ge=1)


class CodeTtysRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)


class SearchNamesRequest(BaseModel):
    query: str = Field(min_length=1, max_length=256)
    sources: SourceList | None = None
    tty_filters: SourceList | None = None
    limit: int = Field(default=25, ge=1)


class HierarchyRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)
    direction: Literal["parents", "children", "ancestors", "descendants"]
    max_depth: int = Field(default=5, ge=1)
    # QC-494: ge=0 — the service contract treats limit=0 as "no rows"
    # (validated non-negative in services.hierarchy.get_code_relations);
    # ge=1 made the remote surface 422 on a value the local engine accepts.
    limit: int | None = Field(default=None, ge=0)
    # include_retired: include retired/editorial-suppressed concepts as walk
    # targets (default active-only, per the QC-238 pruning).
    include_retired: bool = False


class MappingRequest(BaseModel):
    codes: list[CodeInput] = Field(default_factory=list, max_length=MAX_CODES_PER_REQUEST)
    target_sources: SourceList = Field(default_factory=list, min_length=1)
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

        # QC-471 (MEDIUM): per-app single-worker executor — the same pattern
        # the MCP and FHIR servers use (apps/_asyncutil.run_db). Pre-fix every
        # handler ran its DuckDB work directly on the asyncio event loop, so
        # one slow batch POST froze ALL endpoints (measured: /health
        # completion gap == full batch duration; k8s liveness probes would
        # kill the pod ~30s into a 60s production batch).
        db_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="api-db")
        app.state.con = con
        app.state.engine = engine
        app.state.db_executor = db_executor
        app.state.settings = app_settings
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            # wait=True so any in-flight DuckDB call finishes before
            # con.close() runs (mirror of McpRuntime.close ordering).
            db_executor.shutdown(wait=True, cancel_futures=True)
            con.close()

    app = FastAPI(
        title="medterm4ds",
        # QC-487 (MEDIUM): single-sourced from the package — the literal
        # '0.0.1' had to be manually synced with pyproject and gave clients
        # no way to detect client/server version skew.
        version=__version__,
        lifespan=lifespan,
    )

    # QC-478 (MEDIUM) / QC-497 (MEDIUM): service-layer input-validation
    # errors previously escaped as opaque 500s ("Internal Server Error"),
    # destroying the diagnostic the local facade surfaces (e.g. "source(s)
    # not found in this database: 'BANANA'") — and for LOINC the one error
    # whose text is operator-actionable (the prepared-schema rebuild
    # instruction) was exactly the one remote callers could not see. This
    # is the apps/fhir_api GLOBAL_RULES 'service-delegation' wrapper,
    # applied once at the app level instead of per-handler. Not a silent
    # fallback: the message is preserved verbatim in the response body.
    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(NotImplementedError)
    async def _not_implemented_handler(request: Request, exc: NotImplementedError) -> JSONResponse:
        return JSONResponse(status_code=501, content={"detail": str(exc)})

    @app.middleware("http")
    async def cap_request_body(request: Request, call_next):
        # QC-474 (MEDIUM): reject oversized bodies on Content-Length BEFORE
        # parsing. The per-request code-count cap alone measured bytes only
        # after pydantic had materialized them (a 1GB count-legal POST cost
        # +1.8GB RSS and still returned 200).
        length = request.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > MAX_REQUEST_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={
                    "detail": (
                        f"Request body exceeds {MAX_REQUEST_BODY_BYTES} bytes. "
                        f"Batch fewer codes or shorten values."
                    ),
                },
            )
        return await call_next(request)

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
            # QC-487 (MEDIUM): version contract — /health is the endpoint
            # RemoteApiEngine.health() already surfaces to clients; without
            # it, a server from an older wheel answers indistinguishably
            # from a current one (skew only surfaced as raw wrong-shape
            # errors). Single-sourced from medterm4ds.__version__.
            "version": __version__,
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
        executor = request.app.state.db_executor
        results = await run_db(
            executor,
            get_patient_friendly_names,
            [code.to_ref() for code in payload.codes],
            engine=engine,
            max_depth=payload.max_depth,
            resolve_mode=payload.resolve_mode,
        )
        return {"results": [result.to_dict() for result in results]}

    @app.post("/lookup")
    async def lookup(
        payload: LookupRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        executor = request.app.state.db_executor
        results = await run_db(
            executor,
            get_code_infos,
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
        executor = request.app.state.db_executor
        results = await run_db(
            executor,
            resolve_codes,
            [code.to_ref() for code in payload.codes],
            engine=engine,
        )
        return {"results": [result.to_dict() for result in results]}

    @app.post("/sources")
    async def sources(
        payload: SourceStatsRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        executor = request.app.state.db_executor
        stats = await run_db(
            executor, get_source_stats, engine=engine, sources=payload.sources
        )
        return {"results": [stat.to_dict() for stat in stats]}

    # QC-488 (LOW): /source-stats was a byte-identical copy-paste of the
    # /sources handler — two implementations that could drift independently
    # (e.g. a future fix applied to one but not the other). One handler, two
    # routes: /source-stats stays as a backward-compatible alias.
    app.add_api_route("/source-stats", sources, methods=["POST"])

    @app.post("/sample-codes")
    async def sample_codes(
        payload: SampleCodesRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        executor = request.app.state.db_executor
        codes = await run_db(
            executor,
            sample_source_codes,
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
        executor = request.app.state.db_executor
        infos = await run_db(
            executor,
            get_code_ttys,
            [code.to_ref() for code in payload.codes],
            engine=engine,
        )
        return {"results": [info.to_dict() for info in infos]}

    @app.post("/search-names")
    async def search(
        payload: SearchNamesRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        executor = request.app.state.db_executor
        results = await run_db(
            executor,
            search_names,
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
        executor = request.app.state.db_executor
        results = await run_db(
            executor,
            get_code_relations,
            [code.to_ref() for code in payload.codes],
            engine=engine,
            direction=payload.direction,
            max_depth=payload.max_depth,
            limit=payload.limit,
            include_retired=payload.include_retired,
        )
        return {"results": [result.to_dict() for result in results]}

    @app.post("/map")
    async def map_codes(
        payload: MappingRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        executor = request.app.state.db_executor
        results = await run_db(
            executor,
            get_code_mappings,
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
        executor = request.app.state.db_executor
        result = await run_db(
            executor,
            optimize_codes,
            [code.to_ref() for code in payload.codes],
            engine=engine,
            relationship=payload.relationship,
            output_format=payload.output_format,
            include_codes=payload.include_codes,
        )
        # QC-490 (LOW): /optimize was the only one of 13 data endpoints
        # using the singular 'result' envelope key — two envelope conventions
        # on one wire protocol forced generic clients to special-case it.
        # RemoteApiEngine still accepts the legacy 'result' key.
        return {"results": [result.to_dict(include_codes=payload.include_codes)]}

    @app.post("/conceptmap/patient-friendly")
    async def conceptmap_patient_friendly(
        payload: ConceptMapRequest,
        request: Request,
    ) -> dict[str, Any]:
        engine = _engine(request)
        executor = request.app.state.db_executor
        rows = await run_db(
            executor,
            get_concept_map,
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
    # QC-344 (EC-15 LOW): MEDTERM4DS_API_HOST may carry a scheme/port for
    # advertisement purposes (see fhir_api._deployment_base_url); uvicorn's
    # bind address must be a bare host. Strip both before binding.
    if "://" in host:
        host = host.split("://", 1)[1]
    if host.startswith("["):
        host = host.split("]", 1)[0] + "]"
    elif ":" in host:
        host = host.split(":", 1)[0]
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
