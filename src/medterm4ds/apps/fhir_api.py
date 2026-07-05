"""FHIR R4 terminology server facade for medterm4ds.

Exposes $lookup, $validate-code, $translate, and $search operations
over standard FHIR R4 HTTP endpoints. Binds to 127.0.0.1 by default
(localhost-only multi-process sidecar; see SECURITY.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from medterm4ds.apps._asyncutil import run_db as _run_db
from medterm4ds.core.config import local_duckdb_config
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.fhir import fhir_uri_to_system, system_to_fhir_uri
from medterm4ds.engines.fhir.responses import (
    MATCH_GRADE_EXTENSION_URL,
    build_bundle_search,
    build_capability_statement,
    build_operation_outcome,
    build_parameters_lookup,
    build_parameters_subsumes,
    build_parameters_translate,
    build_parameters_validate,
    build_valueset_expand,
)
from medterm4ds.services.discovery import search_names
from medterm4ds.services.hierarchy import get_descendants_bfs, is_descendant
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.mapping import get_code_mappings
from medterm4ds.services.patient_friendly import get_patient_friendly_names

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8001
# Cap on $extract input text length. medspaCy + GLiNER inference cost scales
# linearly with input length; without a cap, a single megabyte-text request
# can starve the single-worker NER executor for seconds. 100K chars is well
# above any reasonable clinical-note size (a 50-page chart note is ~25K).
MAX_EXTRACT_TEXT_CHARS = int(os.getenv("MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS", "100000"))
# Cap on rendered length of user-supplied strings in error messages. Defends
# against reflected XSS in EHR clients that render OperationOutcome.diagnostics
# as HTML, and against log-injection via control chars.
MAX_ERROR_FIELD_CHARS = 256
DEFAULT_SEARCH_INDEX_DIR = "/mnt/d/fhir4px-model/dist/naming_bm25"


TRUNCATION_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"


def _truncation_extensions(
    *,
    count_limited: bool,
    depth_cap_hit: bool,
    count: int | None = None,
    max_depth: int | None = None,
) -> list[dict[str, Any]]:
    """Build the HL7 valueset-toocostly extension for truncated expansions."""
    if not (count_limited or depth_cap_hit):
        return []
    reasons = []
    if count_limited and count is not None:
        reasons.append(f"count-limited at {count}")
    if depth_cap_hit and max_depth is not None:
        reasons.append(f"depth-limited at {max_depth}")
    suffix = "; set FHIR_VS_MAX_DEPTH to override" if max_depth is not None else ""
    return [{
        "url": TRUNCATION_EXT_URL,
        "valueBoolean": True,
        "extension": [{
            "url": "reason",
            "valueString": ", ".join(reasons) + suffix,
        }],
    }]


def expand_url_pattern(
    engine,
    url: str,
    *,
    count: int = 1000,
) -> dict[str, Any]:
    """Expand a FHIR fhir_vs URL pattern to a ValueSet expansion payload.

    Standalone version of the FHIR ``$expand?url=...`` logic, callable
    without starting the HTTP server. Used by in-process consumers (e.g.
    fhir4ds's InProcessTerminologyEndpoint) that need the same URL
    semantics as the HTTP sidecar.

    Supports SNOMED intensional URLs:
      ``http://snomed.info/sct/404684003?fhir_vs=isa``
      ``http://snomed.info/sct/404684003?fhir_vs``

    Other FHIR system URIs (ICD10CM, RXNORM, LNC, etc.) are recognized via
    ``SYSTEM_TO_FHIR_URI`` but only SNOMED has a standard intensional
    expansion URL convention (``fhir_vs=isa``) — other systems raise
    ValueError. The SNOMED URI itself is sourced from
    ``medterm4ds.engines.fhir.SYSTEM_TO_FHIR_URI`` so the expansion,
    ConceptMap export, and CapabilityStatement all reference the same URI.

    Performance: descendant walk uses layer-by-layer BFS (O(nodes) not
    O(paths)), capped at ``FHIR_VS_MAX_DEPTH`` (default 5). Diabetes
    Mellitus descendants return in <1s; was 5min+ with the old recursive CTE.

    Args:
        engine: A TerminologyEngine (typically LocalDuckDBEngine).
        url: The fhir_vs URL to expand.
        count: Max number of concepts in the expansion (default 1000).

    Returns:
        FHIR ValueSet expansion payload dict — same shape as the HTTP
        ``$expand`` response.

    Raises:
        ValueError: If the URL pattern is unsupported or the system URI
            is unrecognized.
    """
    from urllib.parse import parse_qs, urlparse

    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    path_parts = parsed.path.strip("/").split("/")
    query_params = parse_qs(parsed.query)
    fhir_vs = query_params.get("fhir_vs", [""])[0]

    snomed_uri = SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]
    if snomed_uri in base and len(path_parts) >= 2:
        code = path_parts[-1]
        source = "SNOMEDCT_US"
        system_uri = snomed_uri
        include_root = fhir_vs in ("", "isa", "refset")

        contains: list[dict[str, Any]] = []
        if include_root:
            root_infos = get_code_infos([CodeRef(source, code)], engine=engine)
            if root_infos and root_infos[0]:
                contains.append({
                    "system": system_uri,
                    "code": code,
                    "display": root_infos[0].name or code,
                })

        max_depth = int(os.getenv("FHIR_VS_MAX_DEPTH", "5"))
        descendant_budget = max(1, count - len(contains))
        relations, depth_cap_hit = get_descendants_bfs(
            CodeRef(source=source, code=code),
            engine=engine,
            max_depth=max_depth,
            limit=descendant_budget,
        )
        for rel in relations:
            contains.append({
                "system": system_uri,
                "code": rel.target.code,
                "display": rel.target_display or rel.target.code,
            })

        count_limited = len(relations) >= descendant_budget
        extensions = _truncation_extensions(
            count_limited=count_limited,
            depth_cap_hit=depth_cap_hit,
            count=count,
            max_depth=max_depth,
        )

        return build_valueset_expand(contains[:count], url=url, extensions=extensions)

    raise ValueError(
        f"Unsupported fhir_vs URL pattern: {url!r}. "
        f"Only SNOMED CT intensional expansions ({snomed_uri}/<code>?fhir_vs=isa) "
        "are implemented. Other FHIR system URIs are recognized by the server "
        "but lack a standard intensional expansion URL convention."
    )


@dataclass(frozen=True)
class FhirApiSettings:
    """FHIR facade settings."""
    db_path: Path
    memory_profile: str = "balanced"
    search_index_dir: str = DEFAULT_SEARCH_INDEX_DIR
    prepare_cache: bool = True
    sources: tuple[str, ...] = (
        "ICD10CM", "ICD10PCS", "SNOMEDCT_US", "RXNORM", "LNC", "CPT", "HCPCS", "CVX",
    )

    @classmethod
    def from_env(cls) -> FhirApiSettings:
        db_path = os.getenv("MEDTERM4DS_DB")
        if not db_path:
            raise RuntimeError("MEDTERM4DS_DB is required for the FHIR API.")
        return cls(
            db_path=Path(db_path),
            memory_profile=os.getenv("MEDTERM4DS_MEMORY_PROFILE", "balanced"),
            search_index_dir=os.getenv("MEDTERM4DS_SEARCH_INDEX_DIR", DEFAULT_SEARCH_INDEX_DIR),
        )


def _load_bm25_indexes(search_dir: str) -> dict[str, Any]:
    """Probe the BM25 search index directory and report what's available.

    Does NOT load indexes into a module global — services.search.SearchService
    owns the indexes now (single source of truth, shared across all four
    surfaces). This function just produces a summary dict so the startup
    banner can tell operators what's available and what's missing.
    """
    expected = ("condition", "lab", "medication", "procedure", "vaccine", "body_structure")
    search_path = Path(search_dir)
    if not search_path.is_dir():
        logger.warning(
            "BM25 search index directory not found: %s "
            "(set MEDTERM4DS_SEARCH_INDEX_DIR to enable $search lexical/hybrid)",
            search_dir,
        )
        return {"loaded": [], "missing": list(expected), "flat_format": [], "dir": search_dir}

    loaded: list[str] = []
    missing: list[str] = []
    flat_format: list[str] = []
    for category in expected:
        json_path = search_path / f"{category}_bm25.json"
        if not json_path.exists():
            missing.append(category)
            continue
        try:
            with json_path.open() as f:
                index = json.load(f)
            if isinstance(index, dict) and "postings" in index:
                loaded.append(category)
                logger.info("  BM25 %s: %d records", category, index.get("num_records", 0))
            elif isinstance(index, list):
                loaded.append(category)
                flat_format.append(category)
                logger.info("  BM25 %s: %d records (flat)", category, len(index))
        except Exception:
            logger.exception("Failed to load BM25 index: %s", json_path)
            missing.append(category)

    return {"loaded": loaded, "missing": missing, "flat_format": flat_format, "dir": search_dir}


class _PatientFriendlyCache:
    """Per-app cache of patient-friendly JSON data.

    Was a module-global dict — moved to a class so multiple FHIR apps in one
    process get independent caches (test isolation, multi-tenant). Loaded
    once at startup, read on every $lookup.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, Any]]] = {}

    def load(self, db_path: Path) -> dict[str, Any]:
        """Load patient_friendly JSONs. Returns a summary dict for the banner."""
        baseline = Path(os.getenv("MEDTERM4DS_FHIR4PX_BASELINE", "/mnt/d/medterm4ds/reports/fhir4px"))
        sources = ("snomedct_us", "rxnorm", "icd10cm", "lnc", "cvx")
        loaded: list[str] = []
        missing: list[str] = []
        for source_lower in sources:
            json_path = baseline / f"patient_friendly_{source_lower}.json"
            if json_path.exists():
                try:
                    with json_path.open() as f:
                        self._data[source_lower] = json.load(f)
                    loaded.append(source_lower)
                except Exception:
                    logger.info(
                        "Could not parse patient_friendly JSON %s — "
                        "patient-friendly custom properties will be skipped for %s",
                        json_path, source_lower,
                    )
                    missing.append(source_lower)
            else:
                missing.append(source_lower)
        return {"loaded": loaded, "missing": missing, "dir": str(baseline)}

    def get(self, source: str, code: str) -> dict[str, Any] | None:
        """Look up pre-computed patient-friendly data for a code."""
        return self._data.get(source.lower(), {}).get(code)


def _log_startup_banner(
    settings: FhirApiSettings,
    bm25_summary: dict[str, Any],
    pf_summary: dict[str, Any],
) -> None:
    """Print a clear startup banner: what's available, what's missing, and how to enable it."""
    logger.info("=" * 72)
    logger.info("medterm4ds FHIR Terminology Server starting up")
    logger.info("  Database: %s", settings.db_path)

    bm25_loaded = bm25_summary.get("loaded", [])
    bm25_missing = bm25_summary.get("missing", [])
    if bm25_loaded:
        logger.info(
            "  BM25 search indexes: %d category/categories loaded %s",
            len(bm25_loaded), tuple(bm25_loaded),
        )
        logger.info("    → $search lexical and hybrid modes AVAILABLE")
    else:
        logger.warning(
            "  BM25 search indexes: NONE loaded (looked in %s)",
            bm25_summary.get("dir"),
        )
        logger.warning(
            "    → $search lexical/hybrid will return 503. "
            "Set MEDTERM4DS_SEARCH_INDEX_DIR to the directory containing "
            "<category>_bm25.json files.",
        )
    if bm25_missing and bm25_loaded:
        logger.info("    (missing categories: %s)", tuple(bm25_missing))

    # Semantic engine availability is checked lazily; report it without loading the model.
    # Narrow the except to OSError/ImportError so real config bugs (e.g. partial install
    # with model dir present but torch missing) surface as real tracebacks instead of
    # being silently swallowed as "model not found".
    sem_available = False
    sem_error: BaseException | None = None
    try:
        from medterm4ds.engines.fhir.semantic import get_semantic_engine
        sem_available = get_semantic_engine().is_available
    except (OSError, ImportError) as exc:
        sem_error = exc
    except Exception as exc:  # pragma: no cover — surface unexpected errors loudly
        logger.exception(
            "Unexpected error while probing SapBERT availability; "
            "treating semantic mode as unavailable.",
        )
        sem_error = exc
    if sem_available:
        logger.info("  SapBERT embedding model: available at MEDTERM4DS_EMBEDDING_MODEL_DIR")
        logger.info("    → $search semantic mode AVAILABLE")
    elif sem_error is not None:
        logger.warning(
            "  SapBERT embedding model: unavailable (%s: %s). "
            "Fix the underlying error or set MEDTERM4DS_EMBEDDING_MODEL_DIR.",
            type(sem_error).__name__, sem_error,
        )
    else:
        logger.warning(
            "  SapBERT embedding model: not found. "
            "Set MEDTERM4DS_EMBEDDING_MODEL_DIR to enable $search semantic mode.",
        )

    pf_loaded = pf_summary.get("loaded", [])
    pf_missing = pf_summary.get("missing", [])
    if pf_loaded:
        logger.info(
            "  Patient-friendly names: %d source(s) loaded %s",
            len(pf_loaded), tuple(pf_loaded),
        )
    if pf_missing:
        logger.info(
            "  Patient-friendly names: missing for %d source(s) %s "
            "(looked in %s; set MEDTERM4DS_FHIR4PX_BASELINE to override)",
            len(pf_missing), tuple(pf_missing), pf_summary.get("dir"),
        )

    _ready_host = os.getenv("MEDTERM4DS_API_HOST", "127.0.0.1")
    _ready_port = int(os.getenv("MEDTERM4DS_FHIR_API_PORT", str(DEFAULT_PORT)))
    logger.info("FHIR API ready on %s:%d", _ready_host, _ready_port)
    logger.info("  Tunable env vars (set before startup):")
    logger.info("    FHIR_VS_MAX_DEPTH=%s   (cap on $expand?fhir_vs=isa descendant depth)", os.getenv("FHIR_VS_MAX_DEPTH", "5"))
    logger.info("    MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS=%s   (cap on $extract input length)", MAX_EXTRACT_TEXT_CHARS)
    logger.info("    MEDTERM4DS_FHIR_API_PORT  | MEDTERM4DS_API_HOST  (bind config)")
    logger.info("    MEDTERM4DS_SEARCH_INDEX_DIR | MEDTERM4DS_EMBEDDING_MODEL_DIR  (search assets)")
    logger.info("    MEDTERM4DS_FHIR4PX_BASELINE  (patient_friendly JSONs dir)")
    logger.info("=" * 72)


try:
    import duckdb
    from fastapi import FastAPI, Query, Request
    from fastapi.responses import JSONResponse
except ImportError:
    FastAPI = None  # type: ignore[assignment,misc]


def create_fhir_app(settings: FhirApiSettings | None = None) -> Any:
    """Create the FHIR R4 terminology FastAPI app."""
    if FastAPI is None:
        raise ImportError(
            "Install medterm4ds[fhir] to use the FHIR API: pip install medterm4ds[fhir]"
        )

    app_settings = settings or FhirApiSettings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        if not app_settings.db_path.exists():
            raise RuntimeError(f"Database not found: {app_settings.db_path}")
        con = duckdb.connect(str(app_settings.db_path), read_only=True)
        config = local_duckdb_config(app_settings.memory_profile)
        engine = LocalDuckDBEngine(con, config=config)
        if app_settings.prepare_cache:
            engine.prepare_cache(app_settings.sources, create_indexes=False)
        # Per-app executors. DB work serializes on one worker (DuckDB Python
        # connections aren't thread-safe under concurrent use). NER work also
        # serializes on one worker because medspaCy pipelines aren't
        # thread-safe. The two are separate so a slow $extract doesn't block
        # $lookup, $validate-code, etc. — and neither blocks the event loop,
        # so /health and /fhir/metadata stay responsive.
        db_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fhir-db")
        ner_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fhir-ner")
        app.state.con = con
        app.state.engine = engine
        app.state.db_executor = db_executor
        app.state.ner_executor = ner_executor
        app.state.ready = True
        # Load optional search + patient-friendly caches and print a startup banner.
        bm25_summary = _load_bm25_indexes(app_settings.search_index_dir)
        pf_cache = _PatientFriendlyCache()
        pf_summary = pf_cache.load(app_settings.db_path)
        app.state.patient_friendly_cache = pf_cache
        # Configure the SearchService singleton with FHIR's settings. Without
        # this, the singleton would lazily initialize from env vars on first
        # $search call — fine in production but breaks test isolation (one
        # test's loaded indexes pollute the next test's "should be 503" case).
        from medterm4ds.services.search import configure_search_service
        configure_search_service(search_index_dir=app_settings.search_index_dir)
        _log_startup_banner(app_settings, bm25_summary, pf_summary)
        try:
            yield
        finally:
            app.state.ready = False
            # wait=True so any in-flight DuckDB / NER call finishes before
            # con.close() runs. cancel_futures=True cancels queued work; the
            # running future is uncancellable, so without wait=True the
            # connection could be closed out from under it.
            db_executor.shutdown(wait=True, cancel_futures=True)
            ner_executor.shutdown(wait=True, cancel_futures=True)
            con.close()

    app = FastAPI(
        title="medterm4ds FHIR Terminology Server",
        version="0.0.1",
        lifespan=lifespan,
    )

    def _engine(request) -> LocalDuckDBEngine:
        _check_ready(request)
        return request.app.state.engine

    def _check_ready(request) -> None:
        """Enforce the 503 startup gate. Called by handlers that don't otherwise
        need the engine (e.g. $search, $extract) so they 503 cleanly during
        startup instead of running against empty caches."""
        if not getattr(request.app.state, "ready", False):
            raise _fhir_error(503, "Service is starting up.")

    def _executor(request) -> ThreadPoolExecutor:
        return request.app.state.db_executor

    def _ner_executor(request) -> ThreadPoolExecutor:
        return request.app.state.ner_executor

    def _fhir_error(status: int, message: str) -> JSONResponse:
        # Sanitize: strip control chars + cap length. Defends against reflected
        # XSS in EHR clients that render OperationOutcome.diagnostics as HTML,
        # and against log-injection via newline/control chars in user-supplied
        # system URIs and codes.
        clean = "".join(c for c in message if c >= " " or c == " ")
        if len(clean) > MAX_ERROR_FIELD_CHARS:
            clean = clean[:MAX_ERROR_FIELD_CHARS] + "…"
        return JSONResponse(
            status_code=status,
            content=build_operation_outcome("error", "processing", clean),
        )

    # -- CapabilityStatement --
    @app.get("/fhir/metadata")
    async def metadata():
        # Build the advertised URL from the same env vars main() binds to, so
        # the statement stays correct when operators override the port
        # (MEDTERM4DS_FHIR_API_PORT) or host (MEDTERM4DS_API_HOST) for reverse
        # proxies or to avoid conflicts. Defaults match main()'s defaults.
        host = os.getenv("MEDTERM4DS_API_HOST", "127.0.0.1")
        port = int(os.getenv("MEDTERM4DS_FHIR_API_PORT", str(DEFAULT_PORT)))
        return build_capability_statement(f"http://{host}:{port}")

    # -- Lightweight liveness probe --
    # Pure async, no executor / DB / model touch. Returns instantly even when
    # the DB or NER worker is pegging CPU and the event loop is starved.
    # Use this for health checks; do NOT use /fhir/metadata (also fast today,
    # but adds JSON building that could regress).
    @app.get("/health")
    async def health():
        return {"status": "ok", "ready": getattr(app.state, "ready", False)}

    # -- Per-request timing log --
    # Lets us see what's actually slow under load. INFO for every request,
    # WARNING for >1s (the threshold fhir4ds's circuit breaker cares about).
    @app.middleware("http")
    async def log_request_timing(request: Request, call_next):
        import time as _time
        t0 = _time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (_time.perf_counter() - t0) * 1000
        path = request.url.path
        if elapsed_ms > 1000:
            logger.warning("SLOW %s %s -> %d in %.0fms", request.method, path, response.status_code, elapsed_ms)
        else:
            logger.info("%s %s -> %d in %.0fms", request.method, path, response.status_code, elapsed_ms)
        return response

    # -- CodeSystem $lookup --
    @app.get("/fhir/CodeSystem/$lookup")
    async def lookup_get(
        request: Request,
        system: str = Query(..., description="FHIR system URI"),
        code: str = Query(..., description="The code to look up"),
        version: str | None = Query(None),
    ):
        return await _run_db(
            _executor(request), _do_lookup, _engine(request),
            request.app.state.patient_friendly_cache,
            system, code,
        )

    @app.post("/fhir/CodeSystem/$lookup")
    async def lookup_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code = params.get("code")
        if not system or not code:
            return _fhir_error(400, "system and code are required.")
        return await _run_db(
            _executor(request), _do_lookup, _engine(request),
            request.app.state.patient_friendly_cache,
            system, code,
        )

    def _do_lookup(
        engine: LocalDuckDBEngine,
        pf_cache: _PatientFriendlyCache,
        system_uri: str,
        code: str,
    ):
        source = fhir_uri_to_system(system_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
        results = get_code_infos([CodeRef(source, code)], engine=engine)
        code_info = results[0] if results else None

        # Enrich with patient_friendly custom properties
        custom_props: dict[str, Any] = {}
        pf = pf_cache.get(source, code)
        if pf:
            custom_props["patient-friendly"] = pf.get("name")
            custom_props["match-type"] = pf.get("match_type")
            if pf.get("canonical_code"):
                custom_props["canonical-code"] = pf.get("canonical_code")
                custom_props["canonical-system"] = pf.get("canonical_system")
            if pf.get("tty"):
                custom_props["tty"] = pf.get("tty")

        return build_parameters_lookup(
            code_info,
            system_uri=system_uri,
            custom_properties=custom_props,
        )

    # -- CodeSystem $validate-code --
    @app.get("/fhir/CodeSystem/$validate-code")
    async def validate_get(
        request: Request,
        system: str = Query(...),
        code: str = Query(...),
    ):
        return await _run_db(_executor(request), _do_validate, _engine(request), system, code)

    @app.post("/fhir/CodeSystem/$validate-code")
    async def validate_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code = params.get("code")
        if not system or not code:
            return _fhir_error(400, "system and code are required.")
        return await _run_db(_executor(request), _do_validate, _engine(request), system, code)

    def _do_validate(engine: LocalDuckDBEngine, system_uri: str, code: str):
        source = fhir_uri_to_system(system_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
        results = get_code_infos([CodeRef(source, code)], engine=engine)
        code_info = results[0] if results else None
        return build_parameters_validate(
            code_info is not None,
            system_uri=system_uri,
            code=code,
            code_info=code_info,
        )

    # -- ConceptMap $translate --
    @app.get("/fhir/ConceptMap/$translate")
    async def translate_get(
        request: Request,
        system: str = Query(..., description="Source system URI"),
        code: str = Query(..., description="Source code"),
        targetsystem: str | None = Query(None, description="Target system URI"),
    ):
        return await _run_db(_executor(request), _do_translate, _engine(request), system, code, targetsystem)

    @app.post("/fhir/ConceptMap/$translate")
    async def translate_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code = params.get("code")
        targetsystem = params.get("targetsystem")
        if not system or not code:
            return _fhir_error(400, "system and code are required.")
        return await _run_db(_executor(request), _do_translate, _engine(request), system, code, targetsystem)

    def _do_translate(engine: LocalDuckDBEngine, source_uri: str, code: str, target_uri: str | None):
        source = fhir_uri_to_system(source_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized source system URI: {source_uri}")
        target_sources = []
        if target_uri:
            target_source = fhir_uri_to_system(target_uri)
            if target_source is None:
                return _fhir_error(400, f"Unrecognized target system URI: {target_uri}")
            target_sources = [target_source]
        else:
            target_sources = list(_all_systems_except(source))
        mappings = get_code_mappings(
            [CodeRef(source, code)],
            engine=engine,
            target_sources=target_sources,
            max_results_per_code=50,
        )
        return build_parameters_translate(
            mappings,
            source_system_uri=source_uri,
            source_code=code,
        )

    # -- CodeSystem $subsumes --
    @app.get("/fhir/CodeSystem/$subsumes")
    async def subsumes_get(
        request: Request,
        system: str = Query(...),
        codeA: str = Query(...),
        codeB: str = Query(...),
    ):
        return await _run_db(_executor(request), _do_subsumes, _engine(request), system, codeA, codeB)

    @app.post("/fhir/CodeSystem/$subsumes")
    async def subsumes_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code_a = params.get("codeA")
        code_b = params.get("codeB")
        if not system or not code_a or not code_b:
            return _fhir_error(400, "system, codeA, and codeB are required.")
        return await _run_db(_executor(request), _do_subsumes, _engine(request), system, code_a, code_b)

    def _do_subsumes(engine: LocalDuckDBEngine, system_uri: str, code_a: str, code_b: str):
        source = fhir_uri_to_system(system_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
        if code_a == code_b:
            return build_parameters_subsumes("equivalent")
        # Use BFS with early-exit (stop_at) so the typical case (1 hop) is one
        # SQL query. The previous recursive get_descendants(max_depth=20) walked
        # the entire A subtree and timed out for wide SNOMED roots.
        a_ref = CodeRef(source=source, code=code_a)
        b_ref = CodeRef(source=source, code=code_b)
        if is_descendant(a_ref, b_ref, engine=engine, max_depth=20):
            return build_parameters_subsumes("subsumes")
        if is_descendant(b_ref, a_ref, engine=engine, max_depth=20):
            return build_parameters_subsumes("subsumed-by")
        return build_parameters_subsumes("not-subsumed")

    # -- CodeSystem $closure --
    @app.post("/fhir/CodeSystem/$closure")
    async def closure_post(
        request: Request,
        body: dict[str, Any],
    ):
        """Maintain a named closure table for fast subsumption checks.

        If no concepts are provided, initializes/resets the closure.
        If concepts are provided, adds them and returns the updated state.
        """
        params = _parse_parameters(body)
        name = params.get("name")
        if not name:
            return _fhir_error(400, "name parameter is required for $closure.")
        return await _run_db(_executor(request), _do_closure, _engine(request), body, name)

    def _do_closure(engine: LocalDuckDBEngine, body: dict[str, Any], name: str):
        from medterm4ds.engines.fhir.closure import (
            build_closure_response,
            get_closure_manager,
        )

        manager = get_closure_manager()

        # Extract concept list from the Parameters body
        concepts: list[tuple[str, str, str]] = []  # (code, system, display)
        for param in body.get("parameter", []):
            if param.get("name") == "concept":
                coding = param.get("valueCoding", {})
                system_uri = coding.get("system", "")
                code = coding.get("code", "")
                display = coding.get("display", code)
                if code and system_uri:
                    source = fhir_uri_to_system(system_uri) or system_uri
                    concepts.append((code, source, display))

        if not concepts:
            # Initialize / reset
            closure = manager.reset(name)
        else:
            # Add concepts (batched — 2 walks per source, not 2 per concept)
            closure = manager.get_or_create(name)
            closure.add_concepts(concepts, engine)

        return build_closure_response(closure)

    # -- ValueSet $expand --
    @app.get("/fhir/ValueSet/$expand")
    async def expand_get(
        request: Request,
        url: str | None = Query(None, description="ValueSet canonical URL"),
        filter: str | None = Query(None, description="Text filter for code display"),
        count: int = Query(20, ge=1, le=1000),
        system: str | None = Query(None, description="System URI for filter expansion"),
    ):
        return await _run_db(
            _executor(request), _do_expand, _engine(request),
            url=url, filter_text=filter, count=count, system_uri=system,
        )

    @app.post("/fhir/ValueSet/$expand")
    async def expand_post(request: Request, body: dict[str, Any]):
        """Expand a ValueSet. Accepts either a ValueSet resource (intensional)
        or a Parameters resource (filter mode)."""
        resource_type = body.get("resourceType", "")
        if resource_type == "ValueSet":
            return await _run_db(_executor(request), _do_expand, _engine(request), value_set=body, count=1000)
        # Parameters-style: extract url, filter, count
        params = _parse_parameters(body)
        count = _parse_count_param(params.get("count"), default=20)
        if count is None:
            return _fhir_error(400, f"count must be an integer in [1, 1000] (got {params.get('count')!r}).")
        return await _run_db(
            _executor(request), _do_expand, _engine(request),
            url=params.get("url"),
            filter_text=params.get("filter"),
            count=count,
            system_uri=params.get("system"),
        )

    def _do_expand(
        engine: LocalDuckDBEngine,
        url: str | None = None,
        filter_text: str | None = None,
        count: int = 20,
        system_uri: str | None = None,
        value_set: dict[str, Any] | None = None,
    ):
        """Expand a ValueSet.

        Three modes:
        1. Intensional (inline ValueSet with compose.include.filter) — hierarchy walk
        2. URL-based (fhir_vs pattern) — SNOMED intensional shorthand
        3. Filter (text search) — EHR autocomplete
        """

        # Mode 1: Inline ValueSet with compose rules
        if value_set:
            return _expand_intensional(engine, value_set, count)

        # Mode 2: URL with fhir_vs pattern (SNOMED intensional shorthand)
        if url and "fhir_vs" in url:
            return _expand_url_pattern(engine, url, count)

        # Mode 3: Text filter (existing EHR autocomplete)
        if filter_text:
            sources = _resolve_sources(system_uri)
            if sources is None:
                return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
            results = search_names(filter_text, engine=engine, sources=sources, limit=count)
            contains = [
                {
                    "system": system_to_fhir_uri(r.code.source) or r.code.source,
                    "code": r.code.code,
                    "display": r.name,
                }
                for r in results
            ]
            return build_valueset_expand(contains, url=url, filter_text=filter_text)

        return _fhir_error(
            400,
            "Provide a ValueSet body, a fhir_vs URL, or a filter parameter.",
        )

    def _expand_intensional(engine, value_set: dict[str, Any], count: int):
        """Expand a ValueSet with compose.include/exclude rules.

        Supports:
        - Explicit concept lists (compose.include[].concept)
        - is-a / descendant-of filters (compose.include[].filter) — via BFS,
          bounded by FHIR_VS_MAX_DEPTH (default 5)
        - compose.exclude (removes codes from the expansion)

        Truncation: when count or depth is hit, emits the HL7 toocostly
        extension (see _truncation_extensions).
        """
        compose = value_set.get("compose", {})
        max_depth = int(os.getenv("FHIR_VS_MAX_DEPTH", "5"))
        contains: list[dict[str, Any]] = []
        depth_cap_hit = False

        for include in compose.get("include", []):
            inc_system = include.get("system", "")
            source = fhir_uri_to_system(inc_system) or inc_system

            # Explicit concept list
            if "concept" in include:
                for concept in include["concept"]:
                    contains.append({
                        "system": inc_system,
                        "code": str(concept.get("code", "")),
                        "display": concept.get("display", ""),
                    })

            # Intensional filter (is-a, descendant-of)
            for filt in include.get("filter", []):
                prop = filt.get("property", "")
                op = filt.get("op", "")
                val = filt.get("value", "")

                if prop == "concept" and op in ("is-a", "descendant-of"):
                    root_code = str(val)
                    include_root = (op == "is-a")

                    if include_root:
                        root_infos = get_code_infos(
                            [CodeRef(source, root_code)], engine=engine
                        )
                        if root_infos and root_infos[0]:
                            contains.append({
                                "system": inc_system,
                                "code": root_code,
                                "display": root_infos[0].name or root_code,
                            })

                    # BFS-bounded descendant walk (was: recursive get_descendants
                    # with max_depth=20, which timed out for wide SNOMED roots).
                    # Pass limit=count so BFS early-exits when the count cap is
                    # reached, rather than walking the entire subtree.
                    descendants, layer_depth_capped = get_descendants_bfs(
                        CodeRef(source=source, code=root_code),
                        engine=engine,
                        max_depth=max_depth,
                        limit=count,
                    )
                    if layer_depth_capped:
                        depth_cap_hit = True
                    for d in descendants:
                        contains.append({
                            "system": inc_system,
                            "code": d.target.code,
                            "display": d.target_display or d.target.code,
                        })
                else:
                    logger.debug("Unsupported filter: property=%s op=%s", prop, op)

        # Apply excludes
        for exclude in compose.get("exclude", []):
            exc_codes = {c.get("code") for c in exclude.get("concept", [])}
            contains = [c for c in contains if c["code"] not in exc_codes]

        # Deduplicate by (system, code)
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for c in contains:
            key = (c["system"], c["code"])
            if key not in seen:
                seen.add(key)
                deduped.append(c)

        count_limited = len(deduped) > count
        extensions = _truncation_extensions(
            count_limited=count_limited,
            depth_cap_hit=depth_cap_hit,
            count=count,
            max_depth=max_depth,
        )
        return build_valueset_expand(deduped[:count], url=value_set.get("url"), extensions=extensions)

    def _expand_url_pattern(engine, url: str, count: int):
        """HTTP-handler wrapper around the module-level expand_url_pattern.

        Delegates to the module-level function; catches ValueError and
        converts to a FHIR 400 OperationOutcome response.
        """
        try:
            return expand_url_pattern(engine, url, count=count)
        except ValueError as exc:
            return _fhir_error(400, str(exc))

    def _resolve_sources(system_uri: str | None) -> list[str] | None:
        """Resolve a FHIR system URI to a list of medterm4ds source names."""
        if system_uri:
            source = fhir_uri_to_system(system_uri)
            return [source] if source else None
        return ["SNOMEDCT_US", "ICD10CM", "RXNORM", "LNC"]

    # -- CodeSystem $search (custom, modeled after Patient $match) --
    # Uses asyncio.to_thread (default executor, multi-worker) because $search
    # doesn't touch the FHIR engine's DuckDB connection — it reads
    # module-global BM25 indexes (read-only) and the SapBERT singleton
    # (torch models in eval mode are thread-safe). Multi-worker lets
    # concurrent $search calls run in parallel without blocking the DB
    # executor that $lookup etc. depend on.
    @app.get("/fhir/CodeSystem/$search")
    async def search_get(
        request: Request,
        query: str = Query(..., description="Text to search for"),
        system: str | None = Query(None, description="Restrict to system URI"),
        count: int = Query(20, ge=1, le=200),
        searchMode: str = Query("lexical", pattern="^(lexical|hybrid|semantic)$"),
    ):
        _check_ready(request)
        return await asyncio.to_thread(_do_search, query, system, count, searchMode)

    @app.post("/fhir/CodeSystem/$search")
    async def search_post(request: Request, body: dict[str, Any]):
        _check_ready(request)
        params = _parse_parameters(body)
        query_text = params.get("query") or params.get("_query")
        system = params.get("system")
        count = _parse_count_param(params.get("count"), default=20)
        if count is None:
            return _fhir_error(400, f"count must be an integer in [1, 1000] (got {params.get('count')!r}).")
        search_mode = params.get("searchMode", "lexical")
        if not query_text:
            return _fhir_error(400, "query is required.")
        return await asyncio.to_thread(_do_search, str(query_text), system, count, search_mode)

    # -- CodeSystem $extract (custom: NER + ConText + search) --
    # Uses a dedicated NER executor (max_workers=1) because medspaCy pipelines
    # are not thread-safe. Separate from the DB executor so a slow $extract
    # doesn't block $lookup, $validate-code, etc.
    @app.get("/fhir/CodeSystem/$extract")
    async def extract_get(
        request: Request,
        text: str = Query(..., max_length=MAX_EXTRACT_TEXT_CHARS, description="Free text to extract concepts from"),
        format: str = Query("codes", pattern="^(codes|terms)$"),
        categories: str | None = Query(None, description="Comma-separated: condition,medication"),
        mode: str = Query("hybrid", pattern="^(lexical|semantic|hybrid)$"),
        minGrade: str = Query("certain", pattern="^(certain|probable|possible)$"),
        includeNegated: bool = Query(False),
    ):
        _check_ready(request)
        return await _run_db(_ner_executor(request), _do_extract, text, format, categories, mode, minGrade, includeNegated)

    @app.post("/fhir/CodeSystem/$extract")
    async def extract_post(request: Request, body: dict[str, Any]):
        _check_ready(request)
        params = _parse_parameters(body)
        text = params.get("text")
        if not text:
            return _fhir_error(400, "text is required.")
        if len(text) > MAX_EXTRACT_TEXT_CHARS:
            return _fhir_error(
                400,
                f"text length {len(text)} exceeds max {MAX_EXTRACT_TEXT_CHARS} chars "
                f"(set MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS to override).",
            )
        return await _run_db(
            _ner_executor(request), _do_extract, str(text),
            params.get("format", "codes"),
            params.get("categories"),
            params.get("mode", "hybrid"),
            params.get("minGrade", "certain"),
            params.get("includeNegated", "false").lower() == "true",
        )

    def _do_extract(text, fmt, categories_str, mode, min_grade, include_negated):
        from medterm4ds.services.extraction import extract as extract_service

        cats = categories_str.split(",") if categories_str else None
        results = extract_service(
            text,
            format=fmt,
            categories=cats,
            mode=mode,
            min_grade=min_grade,
            include_negated=include_negated,
        )
        entries = []
        for r in results:
            d = r.to_dict()
            entries.append({
                "fullUrl": f"CodeSystem/{d.get('system', d.get('entity_type', 'unknown'))}-{d.get('code', d.get('text', ''))}",
                "resource": d,
                "search": {"mode": "match"},
            })
        return {"resourceType": "Bundle", "type": "searchset", "total": len(entries), "entry": entries}

    def _do_search(query_text: str,
        system_uri: str | None,
        count: int,
        search_mode: str,
    ):
        # Delegate to services.search.SearchService — eliminates the FHIR-local
        # BM25 + semantic duplication. SearchService is the canonical impl used
        # by all four surfaces (lib, CLI, MCP, FHIR); we just shape its
        # SearchResult list into a FHIR Bundle here.
        from medterm4ds.services.search import get_search_service

        # Resolve system_uri → sources list (SearchService resolves sources →
        # categories internally via _SOURCE_TO_CATEGORIES, same mapping the old
        # _source_to_categories used).
        sources: list[str] | None = None
        if system_uri:
            source = fhir_uri_to_system(system_uri)
            if source is None:
                return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
            sources = [source]

        try:
            service = get_search_service()
            # Probe availability up-front so we return clean 503s instead of
            # letting SearchService raise inside the executor (would surface
            # as 500).
            if search_mode in ("lexical", "hybrid") and not service.lexical_available:
                return _fhir_error(
                    503,
                    "BM25 search index not loaded. Set MEDTERM4DS_SEARCH_INDEX_DIR.",
                )
            if search_mode == "semantic" and not service.semantic_available:
                return _fhir_error(
                    503,
                    "Embedding model not found. Set MEDTERM4DS_EMBEDDING_MODEL_DIR.",
                )
            if search_mode == "hybrid" and not service.semantic_available:
                return _fhir_error(
                    503,
                    "Embedding model not found. Set MEDTERM4DS_EMBEDDING_MODEL_DIR.",
                )

            results = service.search(
                query_text, mode=search_mode, sources=sources, count=count,
            )
        except RuntimeError as exc:
            # SearchService raises RuntimeError when indexes/models aren't
            # available — translate to 503.
            return _fhir_error(503, str(exc))

        # Convert SearchResult list → FHIR Bundle entry dicts.
        entries = []
        for r in results:
            sys_uri = system_to_fhir_uri(r.source.upper()) or r.source
            entries.append({
                "code": r.code,
                "system": sys_uri,
                "display": r.display,
                "score": r.score,
                "match_grade": r.match_grade,
            })
        return build_bundle_search(entries, query=query_text, search_mode=search_mode)

    def _parse_parameters(body: dict[str, Any]) -> dict[str, str]:
        """Extract named parameters from a FHIR Parameters resource body."""
        out: dict[str, str] = {}
        for param in body.get("parameter", []):
            name = param.get("name", "")
            for key in ("valueString", "valueUri", "valueCode", "valueInteger", "valueBoolean"):
                if key in param:
                    out[name] = str(param[key])
                    break
        return out

    def _parse_count_param(value: str | None, default: int) -> int | None:
        """Parse a `count`-style parameter. Returns None on invalid input so the
        caller can return a 400 OperationOutcome instead of letting int() raise
        ValueError inside the executor (which would surface as a 500).

        Mirrors the GET handlers' `Query(ge=1, le=1000)` boundary so POST
        can't bypass the upper limit (memory-exhaustion vector) or accept
        count<=0 (silent empty/negative-slice results)."""
        if value is None or value == "":
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        if parsed < 1 or parsed > 1000:
            return None
        return parsed

    def _all_systems_except(source: str) -> list[str]:
        all_sys = [
            "SNOMEDCT_US", "ICD10CM", "ICD10PCS", "RXNORM", "LNC", "CPT", "HCPCS", "CVX",
        ]
        return [s for s in all_sys if s != source]

    app.state.ready = False
    return app


def main() -> int:
    """Run the FHIR API server, bound to localhost."""
    import uvicorn

    host = os.getenv("MEDTERM4DS_API_HOST", "127.0.0.1")
    port = int(os.getenv("MEDTERM4DS_FHIR_API_PORT", str(DEFAULT_PORT)))
    if host not in {"127.0.0.1", "::1", "localhost"}:
        logger.warning(
            "Binding to %s — this exposes the FHIR API to external networks.", host
        )
    uvicorn.run(
        "medterm4ds.apps.fhir_api:create_fhir_app",
        factory=True,
        host=host,
        port=port,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
