"""terminology-demo — deployment wrapper around the medterm4ds FHIR API.

This Space surfaces the REAL medterm4ds FHIR R4 terminology server
(``medterm4ds.create_fhir_app``) unmodified: every route it registers
(/fhir/metadata, POST /fhir batch, CodeSystem/$lookup, $validate-code,
$search, $extract, $subsumes, $closure, ValueSet/$validate-code, $expand,
ConceptMap/$translate, resource-level reads, …) is served as-is.

The wrapper adds ONLY deployment concerns:

  - ORIGIN ALLOW-LIST: requests carrying an Origin header are refused with
    403 unless the origin is allow-listed (DEMO_ALLOWED_ORIGINS, explicit
    list — no wildcards). Requests without Origin (curl, servers, HF
    probes) pass but are rate-limited. NOTE: Origin is browser-supplied
    and trivially spoofed — this is a speed bump against other sites'
    browsers, NOT authentication.
  - PER-IP RATE LIMIT: DEMO_RATE_LIMIT_PER_MINUTE sliding window; /health
    is exempt.
  - SPLIT READINESS: /health reports db_ready (DuckDB open + prepare_cache
    done) and search_ready (lexical indexes, SapBERT+FAISS and the
    GLiNER/medspaCy pipeline warmed by a background task) separately.
    $search/$extract return 503 + Retry-Until search_ready so the first
    query never stalls behind a model load.
  - /capabilities: demo inventory (mounted release, DB sources, surfaced
    FHIR routes, limits, allow-list).

Route conflicts: the FHIR app registers its own GET /health; this wrapper
registers /health FIRST on the composed app, so the WRAPPER'S /health wins
(it carries the split readiness signal). The FHIR app's /docs, /redoc and
/openapi.json (authoritative API reference, generated from the real
routes) surface through the root mount because the wrapper disables its
own docs endpoints.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from medterm4ds import __version__, create_fhir_app
from medterm4ds.apps.fhir_api import FhirApiSettings
from medterm4ds.services import search as _search_module

# On the HF Space the data volumes may be attached (preferred) or fetched
# at startup from the private dataset / public terminology repo — see
# startup_data.py. Local/docker deployments with mounts are unaffected
# (ensure_data is a no-op when the files are already in place).
if os.getenv("DEMO_PROVISION_DATA", "false").lower() == "true":
    from startup_data import ensure_data

    ensure_data()

# Historical note (AMUX-44 fixed at origin): the FHIR app's default
# search_index_dir was once a PINNED developer /mnt/d path that disabled
# the library's terminology-release resolution (services/search.py uses
# explicitly-passed dirs "exactly as given"). The default is now unset
# (None) = UNPINNED, so SearchService resolves the mounted release under
# MEDTERM4DS_CACHE_DIR/terminology/<release>/ (lexical/, semantic/ and
# model/ families) — the library's intended fresh-install path; this
# explicit default keeps that behavior even if the env var is absent.
SEARCH_INDEX_DIR = os.getenv(
    "MEDTERM4DS_SEARCH_INDEX_DIR", _search_module.DEFAULT_SEARCH_INDEX_DIR
)
EMBEDDING_MODEL_DIR = os.getenv("MEDTERM4DS_EMBEDDING_MODEL_DIR")

logger = logging.getLogger("terminology-demo")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = os.getenv("MEDTERM4DS_DB", "/data/lookup.duckdb")
CACHE_DIR = os.getenv("MEDTERM4DS_CACHE_DIR", "/data/cache")
MEMORY_PROFILE = os.getenv("MEDTERM4DS_MEMORY_PROFILE", "low")
DB_THREADS = int(os.getenv("DEMO_DB_THREADS", "2"))
ALLOWED_ORIGINS = tuple(
    o.strip()
    for o in os.getenv(
        "DEMO_ALLOWED_ORIGINS",
        "https://terminology.fhir4ds.com,https://fhir4ds.com,https://www.fhir4ds.com,"
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3001,http://127.0.0.1:3001,https://fhir4ds.github.io",
    ).split(",")
    if o.strip()
)
RATE_LIMIT_PER_MINUTE = int(os.getenv("DEMO_RATE_LIMIT_PER_MINUTE", "30"))
MAX_REQUEST_BODY_BYTES = 1_000_000

# FHIR paths that need the warmed search/NER models. Gated with
# 503 + Retry-Until until search_ready, instead of blocking behind the
# model load on the first call.
_MODEL_GATED_PATHS = ("/fhir/CodeSystem/$search", "/fhir/CodeSystem/$extract")


def _env_str(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


# ---------------------------------------------------------------------------
# The real FHIR app (unmodified)
# ---------------------------------------------------------------------------

fhir_settings = FhirApiSettings(
    db_path=Path(DB_PATH),
    memory_profile=MEMORY_PROFILE,
    search_index_dir=SEARCH_INDEX_DIR,
    threads=DB_THREADS,
    temp_directory=_env_str("MEDTERM4DS_TEMP_DIR"),
    memory_limit=_env_str("MEDTERM4DS_MEMORY_LIMIT"),
    prepare_cache=os.getenv("MEDTERM4DS_PREPARE_CACHE", "true").lower() != "false",
    cache_indexes=os.getenv("MEDTERM4DS_CACHE_INDEXES", "false").lower() == "true",
)
fhir_app = create_fhir_app(fhir_settings)
# The FHIR lifespan pins the SearchService singleton to these same dirs
# (configure_search_service), and MEDTERM4DS_EMBEDDING_MODEL_DIR from the
# environment is honored there too — no further wiring needed.


# ---------------------------------------------------------------------------
# Rate limiter (per-IP sliding window)
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Sliding-window per-IP rate limiter (demo-scale, in-memory)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, ip: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[ip]
            while hits and now - hits[0] > 60.0:
                hits.popleft()
            if len(hits) >= self.per_minute:
                retry_after = int(60.0 - (now - hits[0])) + 1
                return False, retry_after
            hits.append(now)
            return True, 0


_rate_limiter = _RateLimiter(RATE_LIMIT_PER_MINUTE)


def _client_ip(request: Request) -> str:
    """Client IP behind the HF Spaces proxy (first x-forwarded-for hop)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Warm-up + readiness
# ---------------------------------------------------------------------------

def _warm_up(app: FastAPI) -> None:
    """Fully load the search service (BM25, SapBERT, FAISS) and the
    extraction pipeline (GLiNER + medspaCy). Both load their heavy models
    lazily on first USE, so the warm-up runs a tiny query through each
    rather than just constructing the objects."""
    try:
        from medterm4ds.services.extraction import get_extraction_service
        from medterm4ds.services.search import get_search_service

        # Both index families are lazy per mode: BM25 loads on the first
        # lexical call, SapBERT+FAISS on the first semantic call. Warm both.
        search_service = get_search_service()
        search_service.lexical("warmup", count=1)
        search_service.semantic("warmup", count=1)
        # Full extract path (NER + section detection + resolution) —
        # more of the pipeline is lazy beyond find_terms alone.
        get_extraction_service().extract("Patient has diabetes.", format="codes")
    except Exception as exc:
        logger.exception("terminology-demo warm-up failed; /health stays 'starting'")
        app.state.warm_error = f"{type(exc).__name__}: {exc}"
        return
    app.state.search_ready = True
    logger.info("terminology-demo warm-up complete: search_ready=True")


def _mounted_release() -> str | None:
    """Release id of the mounted terminology release, from its manifest."""
    releases_dir = Path(CACHE_DIR) / "terminology"
    try:
        for release_dir in sorted(releases_dir.iterdir()):
            manifest = release_dir / "manifest.json"
            if manifest.is_file():
                with manifest.open() as f:
                    return str(json.load(f).get("release", release_dir.name))
    except OSError:
        pass
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run the FHIR app's own lifespan (opens DuckDB, prepare_cache,
    # executors, SearchService configuration, startup banner). Mounted
    # sub-app lifespans do not run automatically, so drive it explicitly.
    async with fhir_app.router.lifespan_context(fhir_app):
        app.state.db_ready = True
        app.state.search_ready = False
        app.state.warm_error = None
        threading.Thread(
            target=_warm_up, args=(app,), name="demo-warmup", daemon=True
        ).start()
        try:
            yield
        finally:
            app.state.db_ready = False
            app.state.search_ready = False
            app.state.warm_error = None


app = FastAPI(
    title="medterm4ds terminology-demo",
    version=__version__,
    lifespan=lifespan,
    # Disable the wrapper's own docs so the FHIR app's /docs, /redoc and
    # /openapi.json — generated from the real routes — surface instead.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# ---------------------------------------------------------------------------
# Middleware: body cap -> origin allow-list -> rate limit -> warm gate
# ---------------------------------------------------------------------------

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
        # Spec-correct refusal: 403 with NO access-control-allow-origin
        # (absence IS the refusal). NOTE (verified live): the hf.space
        # platform edge nonetheless injects an echoed ACAO onto this
        # response — it overwrites the header even when the app pins it —
        # so the browser sees the echo. Nothing the app sends can prevent
        # that; the protection is the 403 itself (no data returned).
        return JSONResponse(
            status_code=403,
            content={"detail": f"Origin {origin!r} is not allowed to call this demo API."},
        )

    # 3. Per-IP rate limit (sliding window). /health stays open for probes
    #    and for the docs page's readiness polling.
    if request.url.path != "/health":
        allowed, retry_after = _rate_limiter.allow(_client_ip(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "detail": (
                        f"Rate limit exceeded: {RATE_LIMIT_PER_MINUTE} requests/minute. "
                        f"Retry in {retry_after}s."
                    )
                },
            )

    # 4. Model-warm gate: the FHIR $search/$extract handlers lazily load
    #    SapBERT/FAISS/GLiNER on first use; refuse with 503 + Retry-After
    #    until the background warm-up has completed.
    if (
        request.url.path.rstrip("/") in _MODEL_GATED_PATHS
    ) and not getattr(request.app.state, "search_ready", False):
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "10"},
            content={
                "detail": "Search models are still warming up after wake; "
                "retry in a few seconds."
            },
        )

    # CORS headers only for allow-listed origins, so the browser page can
    # read responses.
    response = await call_next(request)
    if origin is not None and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return response


@app.options("/{rest_of_path:path}")
async def preflight(rest_of_path: str, request: Request):
    """Explicit CORS preflight. Cheap: no engine, model or DB touch."""
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


# ---------------------------------------------------------------------------
# Wrapper-only endpoints (everything else is the real FHIR app)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    # Cheap by design: two in-memory booleans, no engine/model touch.
    db_ready = bool(getattr(request.app.state, "db_ready", False))
    search_ready = bool(getattr(request.app.state, "search_ready", False))
    warm_error = getattr(request.app.state, "warm_error", None)
    ready = db_ready and search_ready
    return {
        "status": "ready" if ready else "starting",
        "ready": ready,
        "db_ready": db_ready,
        "search_ready": search_ready,
        # Why search_ready is stuck false, when warm-up failed — never
        # leave an operator staring at "starting" forever.
        "warm_error": warm_error,
        "version": __version__,
        "service": "terminology-demo-server",
    }


@app.get("/capabilities")
async def capabilities(request: Request) -> dict[str, Any]:
    db_ready = bool(getattr(request.app.state, "db_ready", False))
    engine = getattr(fhir_app.state, "engine", None)
    sources: list[str] = []
    if db_ready and engine is not None:
        sources = sorted(
            {r[0] for r in engine.con.execute("SELECT DISTINCT SAB FROM mrconso").fetchall()}
        )
    fhir_routes = sorted(
        {
            r.path
            for r in fhir_app.routes
            if getattr(r, "path", "").startswith("/fhir")
        }
    )
    return {
        "service": "terminology-demo-server",
        "version": __version__,
        "api": "native FHIR R4 (medterm4ds.apps.fhir_api, unmodified)",
        "mounted_terminology_release": _mounted_release(),
        "db_sources": sources,
        "search_warm": bool(getattr(request.app.state, "search_ready", False)),
        "fhir_routes": fhir_routes,
        "excluded_routes": [],
        "limits": {
            "request_body_bytes_max": MAX_REQUEST_BODY_BYTES,
            "rate_limit_per_minute_per_ip": RATE_LIMIT_PER_MINUTE,
        },
        "allowed_origins": list(ALLOWED_ORIGINS),
    }


# Mount the real FHIR app at the root. Routes registered above (/health,
# /capabilities, the OPTIONS preflight) take precedence; every /fhir/* path
# is served by the unmodified FHIR app.
app.mount("/", fhir_app)


if __name__ == "__main__":
    import uvicorn

    print(
        "terminology-demo starting — native FHIR R4 API (create_fhir_app) "
        f"+ demo wrapper; allowed origins (explicit list, DEMO_ALLOWED_ORIGINS): "
        f"{', '.join(ALLOWED_ORIGINS) or '(none)'}; "
        f"rate limit {RATE_LIMIT_PER_MINUTE}/min per IP"
    )
    uvicorn.run(
        app,
        host=os.getenv("DEMO_HOST", "0.0.0.0"),
        port=int(os.getenv("DEMO_PORT", "7860")),
    )
