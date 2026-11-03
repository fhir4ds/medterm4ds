"""terminology-demo — FastAPI service for the fhir4ds HF Space.

Serves medterm4ds operations (search, extraction, mapping, lookup,
hierarchy, patient-friendly translation) over HTTP on port 7860, backed by:

  - the fhir4ds/terminology release 2026.10.31.1 mounted read-only under
    MEDTERM4DS_CACHE_DIR (canonical/, lexical/, semantic/, model/)
  - lookup.duckdb (CPT-excluded) mounted read-only at MEDTERM4DS_DB

Access control (Joel's "keyed" mechanism — no API keys in the browser):
  - ORIGIN ALLOW-LIST: requests carrying an Origin header are refused with
    403 unless the origin is allow-listed (DEMO_ALLOWED_ORIGINS). Requests
    without Origin (curl, server-side clients, HF health probes) pass.
  - PER-IP RATE LIMIT: sliding window, DEMO_RATE_LIMIT_PER_MINUTE requests
    per minute per client IP (default 30), 429 + Retry-After beyond.
    /health is exempt so platform probes are never throttled.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Literal

from medterm4ds import __version__
from medterm4ds.apps._asyncutil import run_db
from medterm4ds.core.config import local_duckdb_config
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.hierarchy import get_code_relations
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.mapping import get_code_mappings
from medterm4ds.services.patient_friendly import get_patient_friendly_names

import duckdb
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = os.getenv("MEDTERM4DS_DB", "/data/lookup.duckdb")
MEMORY_PROFILE = os.getenv("MEDTERM4DS_MEMORY_PROFILE", "low")
ALLOWED_ORIGINS = tuple(
    o.strip()
    for o in os.getenv(
        "DEMO_ALLOWED_ORIGINS",
        "https://terminology.fhir4ds.com,https://fhir4ds.com,https://www.fhir4ds.com,"
        "http://localhost:3000,http://127.0.0.1:3000,https://fhir4ds.github.io",
    ).split(",")
    if o.strip()
)
RATE_LIMIT_PER_MINUTE = int(os.getenv("DEMO_RATE_LIMIT_PER_MINUTE", "30"))
MAX_CODES_PER_REQUEST = 200          # demo service, not a batch API
MAX_QUERY_CHARS = 1000
MAX_TEXT_CHARS = 10_000
MAX_REQUEST_BODY_BYTES = 1_000_000

SEARCH_MODES = ("lexical", "semantic", "canonical", "hybrid")


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_search_service = None
_search_service_lock = threading.Lock()


def _get_search_service():
    """SearchService singleton; created on first use (loads lexical indexes
    eagerly, SapBERT + FAISS lazily on first semantic query)."""
    global _search_service
    with _search_service_lock:
        if _search_service is None:
            from medterm4ds.services.search import get_search_service
            _search_service = get_search_service()
    return _search_service


_extraction_service = None
_extraction_service_lock = threading.Lock()


def _get_extraction_service():
    """ExtractionService singleton; created on first use (loads GLiNER +
    medspaCy, ~1 GB of models) so /health and /search stay fast."""
    global _extraction_service
    with _extraction_service_lock:
        if _extraction_service is None:
            from medterm4ds.services.extraction import get_extraction_service
            _extraction_service = get_extraction_service()
    return _extraction_service


# ---------------------------------------------------------------------------
# Rate limiting (sliding window per client IP, in-memory)
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.window = 60.0
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.per_minute:
                retry_after = max(1, int(self.window - (now - hits[0])) + 1)
                return False, retry_after
            hits.append(now)
            return True, 0


_rate_limiter = RateLimiter(RATE_LIMIT_PER_MINUTE)


def _client_ip(request: Request) -> str:
    # HF Spaces proxies traffic; the real client IP arrives in x-forwarded-for.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class CodeInput(BaseModel):
    source: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=256)

    def to_ref(self) -> CodeRef:
        return CodeRef.from_pair((self.source, self.code))


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    mode: Literal["lexical", "semantic", "canonical", "hybrid"] = "lexical"
    sources: list[str] | None = None
    count: int = Field(default=20, ge=1, le=50)
    result_types: list[str] | None = None


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    mode: Literal["lexical", "semantic", "canonical", "hybrid"] | None = None
    min_grade: Literal["certain", "probable", "possible"] | None = None
    include_negated: bool = False
    include_uncertain: bool = False
    include_historical: bool = False
    include_family: bool = False


class MapRequest(BaseModel):
    codes: list[CodeInput] = Field(min_length=1, max_length=MAX_CODES_PER_REQUEST)
    target_sources: list[str] = Field(min_length=1, max_length=8)
    max_results_per_code: int = Field(default=50, ge=1, le=50)


class TranslateRequest(BaseModel):
    codes: list[CodeInput] = Field(min_length=1, max_length=MAX_CODES_PER_REQUEST)
    max_depth: int = Field(default=5, ge=0, le=10)


class LookupRequest(BaseModel):
    codes: list[CodeInput] = Field(min_length=1, max_length=MAX_CODES_PER_REQUEST)
    resolve_mode: Literal["active_only", "historical", "resolve_current"] = "active_only"


class HierarchyRequest(BaseModel):
    codes: list[CodeInput] = Field(min_length=1, max_length=MAX_CODES_PER_REQUEST)
    direction: Literal["parents", "children", "ancestors", "descendants"]
    max_depth: int = Field(default=1, ge=1, le=10)
    limit: int | None = Field(default=None, ge=0, le=1000)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.path.exists(DB_PATH):
        raise RuntimeError(f"Database not found: {DB_PATH} (mount the lookup.duckdb volume)")
    con = duckdb.connect(DB_PATH, read_only=True)
    config = local_duckdb_config(MEMORY_PROFILE, threads=int(os.getenv("DEMO_DB_THREADS", "2")))
    engine = LocalDuckDBEngine(con, config=config)
    db_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="demo-db")
    app.state.con = con
    app.state.engine = engine
    app.state.db_executor = db_executor
    app.state.db_ready = True
    app.state.search_ready = False

    def _warm_up() -> None:
        """Fully load the search service (lexical indexes, SapBERT, FAISS)
        and the extraction service (GLiNER + medspaCy) in the background so
        the first real query does not stall behind a model load. Both load
        their heavy models lazily on first USE, so the warm-up runs a tiny
        query through each rather than just constructing the objects."""
        try:
            search_service = _get_search_service()
            search_service.semantic("warmup", count=1)
            extraction_service = _get_extraction_service()
            # Full extract path (NER + section detection + resolution) —
            # more of the pipeline is lazy beyond find_terms alone.
            extraction_service.extract("Patient has diabetes.", format="codes")
        except Exception:
            logging.exception("terminology-demo warm-up failed; /health stays 'starting'")
            return
        app.state.search_ready = True

    threading.Thread(target=_warm_up, name="demo-warmup", daemon=True).start()
    try:
        yield
    finally:
        app.state.db_ready = False
        app.state.search_ready = False
        db_executor.shutdown(wait=True, cancel_futures=True)
        con.close()


app = FastAPI(
    title="medterm4ds terminology-demo",
    version=__version__,
    lifespan=lifespan,
)


@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(TypeError)
async def _type_error_handler(request: Request, exc: TypeError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.middleware("http")
async def origin_allowlist_and_rate_limit(request: Request, call_next):
    # 1. Request-body cap before parsing.
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_REQUEST_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Request body exceeds {MAX_REQUEST_BODY_BYTES} bytes."},
        )

    # 2. Origin allow-list: browsers always send Origin on cross-site calls.
    #    A present-but-unlisted Origin is refused with NO CORS headers;
    #    no Origin (curl, servers, HF probes) passes but is still
    #    rate-limited. NOTE: Origin is browser-supplied and trivially
    #    spoofed — this is a speed bump against other sites' browsers,
    #    NOT authentication.
    origin = request.headers.get("origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        return JSONResponse(
            status_code=403,
            content={"detail": f"Origin {origin!r} is not allowed to call this demo API."},
        )

    # 3. Per-IP rate limit (sliding window). /health stays open for probes.
    if request.url.path != "/health":
        allowed, retry_after = _rate_limiter.allow(_client_ip(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={"detail": f"Rate limit exceeded: {RATE_LIMIT_PER_MINUTE} requests/minute. Retry in {retry_after}s."},
            )

    # CORS headers only for allow-listed origins, so the browser page can read responses.
    response = await call_next(request)
    if origin is not None and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return response


@app.options("/{rest_of_path:path}")
async def preflight(rest_of_path: str, request: Request):
    origin = request.headers.get("origin")
    if origin is None or origin not in ALLOWED_ORIGINS:
        return JSONResponse(status_code=403, content={"detail": "Origin not allowed."})
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        },
    )


def _engine(request: Request) -> LocalDuckDBEngine:
    return request.app.state.engine


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    # Cheap by design: two in-memory booleans, no engine/model touch.
    db_ready = bool(getattr(request.app.state, "db_ready", False))
    search_ready = bool(getattr(request.app.state, "search_ready", False))
    ready = db_ready and search_ready
    return {
        "status": "ready" if ready else "starting",
        "ready": ready,
        "db_ready": db_ready,
        "search_ready": search_ready,
        "version": __version__,
        "service": "terminology-demo",
    }


@app.get("/capabilities")
async def capabilities(request: Request) -> dict[str, Any]:
    engine = _engine(request)
    db_ready = bool(getattr(request.app.state, "db_ready", False))
    search_modes = list(SEARCH_MODES)
    sources: list[str] = []
    if db_ready:
        sources = sorted({r[0] for r in engine.con.execute(
            "SELECT DISTINCT SAB FROM mrconso"
        ).fetchall()})
    return {
        "version": __version__,
        "search_warm": bool(getattr(request.app.state, "search_ready", False)),
        "endpoints": [
            "/health", "/capabilities", "/search", "/extract",
            "/map", "/translate", "/lookup", "/hierarchy",
        ],
        "search_modes": search_modes,
        "code_sources": sources,
        "limits": {
            "search_count_max": 50,
            "codes_per_request_max": MAX_CODES_PER_REQUEST,
            "text_chars_max": MAX_TEXT_CHARS,
            "rate_limit_per_minute_per_ip": RATE_LIMIT_PER_MINUTE,
        },
        "allowed_origins": list(ALLOWED_ORIGINS),
    }


def _require_search_warm(request: Request) -> JSONResponse | None:
    """503 (not blocking) if the search/extraction models are still loading."""
    if not getattr(request.app.state, "search_ready", False):
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "10"},
            content={
                "detail": "Search models are still warming up after wake; "
                "retry in a few seconds."
            },
        )
    return None


@app.post("/search")
async def search(payload: SearchRequest, request: Request) -> dict[str, Any]:
    warm_refusal = _require_search_warm(request)
    if warm_refusal is not None:
        return warm_refusal
    service = _get_search_service()
    results = await run_db(
        request.app.state.db_executor,
        service.search,
        payload.query,
        mode=payload.mode,
        sources=payload.sources,
        count=payload.count,
        result_types=payload.result_types,
    )
    return {"query": payload.query, "mode": payload.mode, "results": [r.to_dict() for r in results]}


@app.post("/extract")
async def extract(payload: ExtractRequest, request: Request) -> dict[str, Any]:
    warm_refusal = _require_search_warm(request)
    if warm_refusal is not None:
        return warm_refusal
    service = _get_extraction_service()
    result = await run_db(
        request.app.state.db_executor,
        service.extract,
        payload.text,
        format="annotated",
        mode=payload.mode,
        min_grade=payload.min_grade,
        include_negated=payload.include_negated,
        include_uncertain=payload.include_uncertain,
        include_historical=payload.include_historical,
        include_family=payload.include_family,
    )
    # format="annotated" returns a dict of primitives already
    return result if isinstance(result, dict) else {"concepts": result}


@app.post("/map")
async def map_codes(payload: MapRequest, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    results = await run_db(
        request.app.state.db_executor,
        get_code_mappings,
        [c.to_ref() for c in payload.codes],
        engine,
        target_sources=payload.target_sources,
        max_results_per_code=payload.max_results_per_code,
    )
    return {"results": [r.to_dict() for r in results]}


@app.post("/translate")
async def translate(payload: TranslateRequest, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    results = await run_db(
        request.app.state.db_executor,
        get_patient_friendly_names,
        [c.to_ref() for c in payload.codes],
        engine,
        max_depth=payload.max_depth,
    )
    return {"results": [r.to_dict() for r in results]}


@app.post("/lookup")
async def lookup(payload: LookupRequest, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    results = await run_db(
        request.app.state.db_executor,
        get_code_infos,
        [c.to_ref() for c in payload.codes],
        engine,
        resolve_mode=payload.resolve_mode,
    )
    return {"results": [None if r is None else r.to_dict() for r in results]}


@app.post("/hierarchy")
async def hierarchy(payload: HierarchyRequest, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    results = await run_db(
        request.app.state.db_executor,
        get_code_relations,
        [c.to_ref() for c in payload.codes],
        engine,
        direction=payload.direction,
        max_depth=payload.max_depth,
        limit=payload.limit,
    )
    return {"results": [r.to_dict() for r in results]}


if __name__ == "__main__":
    import uvicorn

    print(
        "terminology-demo starting — "
        f"allowed origins (explicit list, DEMO_ALLOWED_ORIGINS): {', '.join(ALLOWED_ORIGINS) or '(none)'}; "
        f"rate limit {RATE_LIMIT_PER_MINUTE}/min per IP"
    )
    uvicorn.run(
        app,
        host=os.getenv("DEMO_HOST", "0.0.0.0"),
        port=int(os.getenv("DEMO_PORT", "7860")),
    )
