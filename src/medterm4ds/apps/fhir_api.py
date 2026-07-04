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
_PATIENT_FRIENDLY_CACHE: dict[str, dict[str, dict[str, Any]]] = {}

# BM25 index storage (loaded once on startup).
_bm25_indexes: dict[str, list[dict[str, Any]]] = {}
_bm25_doc_count: dict[str, int] = {}


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
    """Load pre-built BM25 JSON indexes on startup.

    Returns a summary dict with `loaded` (list of categories) and `missing`
    (list of categories) so the caller can produce a clear startup banner.
    """
    expected = ("condition", "lab", "medication", "procedure", "vaccine", "body_structure")
    search_path = Path(search_dir)
    if not search_path.is_dir():
        logger.warning(
            "BM25 search index directory not found: %s "
            "(set MEDTERM4DS_SEARCH_INDEX_DIR to enable $search lexical/hybrid)",
            search_dir,
        )
        return {"loaded": [], "missing": list(expected), "dir": search_dir}

    loaded: list[str] = []
    missing: list[str] = []
    flat_format: list[str] = []  # categories loaded as legacy flat document list
    for category in expected:
        json_path = search_path / f"{category}_bm25.json"
        if not json_path.exists():
            missing.append(category)
            continue
        try:
            with json_path.open() as f:
                index = json.load(f)
            if isinstance(index, dict) and "postings" in index:
                _bm25_indexes[category] = index
                _bm25_doc_count[category] = index.get("num_records", 0)
                loaded.append(category)
            elif isinstance(index, list):
                _bm25_indexes[category] = index
                _bm25_doc_count[category] = len(index)
                loaded.append(category)
                flat_format.append(category)
        except Exception:
            logger.exception("Failed to load BM25 index: %s", json_path)
            missing.append(category)

    for category in loaded:
        marker = " (flat)" if category in flat_format else ""
        logger.info("  BM25 %s: %d records%s", category, _bm25_doc_count.get(category, 0), marker)

    return {"loaded": loaded, "missing": missing, "flat_format": flat_format, "dir": search_dir}


def _load_patient_friendly_cache(db_path: Path) -> dict[str, Any]:
    """Load patient_friendly JSONs for fast $lookup custom properties."""
    baseline = Path(os.getenv("MEDTERM4DS_FHIR4PX_BASELINE", "/mnt/d/medterm4ds/reports/fhir4px"))
    sources = ("snomedct_us", "rxnorm", "icd10cm", "lnc", "cvx")
    loaded: list[str] = []
    missing: list[str] = []
    for source_lower in sources:
        json_path = baseline / f"patient_friendly_{source_lower}.json"
        if json_path.exists():
            try:
                with json_path.open() as f:
                    _PATIENT_FRIENDLY_CACHE[source_lower] = json.load(f)
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


def _get_patient_friendly(source: str, code: str) -> dict[str, Any] | None:
    """Look up pre-computed patient-friendly data for a code."""
    source_lower = source.lower()
    cache = _PATIENT_FRIENDLY_CACHE.get(source_lower, {})
    return cache.get(code)


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

    logger.info("FHIR API ready on 127.0.0.1:%d", DEFAULT_PORT)
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
        pf_summary = _load_patient_friendly_cache(app_settings.db_path)
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

    # Canonical HL7 extension for "expansion was truncated because it was too
    # costly to compute fully". Per the FHIR R4 spec, this extension lives on
    # ValueSet.expansion and uses valueBoolean=true. We add a sibling
    # valueString with a human-readable reason (depth vs count) for clients
    # that want to surface diagnostics; the valueBoolean is the contract.
    TRUNCATION_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

    def _truncation_extensions(
        *,
        count_limited: bool,
        depth_cap_hit: bool,
        count: int | None = None,
        max_depth: int | None = None,
    ) -> list[dict[str, Any]]:
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
        return build_capability_statement(f"http://127.0.0.1:{DEFAULT_PORT}")

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
        return await _run_db(_executor(request), _do_lookup, _engine(request), system, code)

    @app.post("/fhir/CodeSystem/$lookup")
    async def lookup_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code = params.get("code")
        if not system or not code:
            return _fhir_error(400, "system and code are required.")
        return await _run_db(_executor(request), _do_lookup, _engine(request), system, code)

    def _do_lookup(engine: LocalDuckDBEngine, system_uri: str, code: str):
        source = fhir_uri_to_system(system_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
        results = get_code_infos([CodeRef(source, code)], engine=engine)
        code_info = results[0] if results else None

        # Enrich with patient_friendly custom properties
        custom_props: dict[str, Any] = {}
        pf = _get_patient_friendly(source, code)
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
            # Add concepts
            closure = manager.get_or_create(name)
            for code, source, display in concepts:
                closure.add_concept(code, source, display, engine)

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
        """Expand a fhir_vs URL pattern.

        Supports SNOMED intensional URLs like:
          http://snomed.info/sct/404684003?fhir_vs=isa
          http://snomed.info/sct/404684003?fhir_vs

        Performance contract: descendant walk is capped at FHIR_VS_MAX_DEPTH
        (default 5) levels. Uses services.hierarchy.get_descendants_bfs — a
        layer-by-layer BFS that visits each node once (O(nodes)) rather than
        the recursive CTE in get_descendants which enumerates all paths via
        path-string cycle prevention (O(paths), explodes for wide subtrees;
        SNOMED Diabetes Mellitus timed out at 5min via the CTE, <1s via BFS).
        Depth 5 covers all clinical value-set definitions; deeper needs
        pre-computed closure (planned). Set FHIR_VS_MAX_DEPTH to override.
        """
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        path_parts = parsed.path.strip("/").split("/")
        query_params = parse_qs(parsed.query)
        fhir_vs = query_params.get("fhir_vs", [""])[0]

        if "snomed.info/sct" in base and len(path_parts) >= 2:
            code = path_parts[-1]
            source = "SNOMEDCT_US"
            system_uri = "http://snomed.info/sct"
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
            # Reserve slots for descendants based on how many the root took.
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

            # Truncation detection. Two independent causes:
            # - count cap: contains was clamped (response == count, descendants may exist beyond)
            # - depth cap: BFS stopped at max_depth with frontier still non-empty
            count_limited = len(relations) >= descendant_budget
            extensions = _truncation_extensions(
                count_limited=count_limited,
                depth_cap_hit=depth_cap_hit,
                count=count,
                max_depth=max_depth,
            )

            return build_valueset_expand(contains[:count], url=url, extensions=extensions)

        return _fhir_error(400, f"Unsupported fhir_vs URL pattern: {url}")

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
            params.get("includeNegated", "false") == "true",
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
        # Determine which categories to search
        if system_uri:
            source = fhir_uri_to_system(system_uri)
            if source is None:
                return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
            categories = _source_to_categories(source)
        else:
            categories = list(_bm25_indexes.keys()) or [
                "condition", "lab", "medication", "procedure", "vaccine", "body_structure"
            ]

        if search_mode == "lexical":
            if not _bm25_indexes:
                return _fhir_error(
                    503,
                    "BM25 search index not loaded. Set MEDTERM4DS_SEARCH_INDEX_DIR.",
                )
            results = _bm25_search(query_text, categories, count)
            return build_bundle_search(results, query=query_text, search_mode="lexical")

        if search_mode == "semantic":
            from medterm4ds.engines.fhir.semantic import get_semantic_engine
            engine = get_semantic_engine()
            if not engine.is_available:
                return _fhir_error(
                    503,
                    "Embedding model not found. Set MEDTERM4DS_EMBEDDING_MODEL_DIR.",
                )
            results = engine.search(query_text, categories=categories, top_k=count)
            # Normalize system names to FHIR URIs
            for r in results:
                r["system"] = system_to_fhir_uri(r["system"].upper()) or r["system"]
            return build_bundle_search(results, query=query_text, search_mode="semantic")

        if search_mode == "hybrid":
            # Stage 1: BM25 retrieve top 50 candidates
            if not _bm25_indexes:
                return _fhir_error(
                    503,
                    "BM25 search index not loaded. Set MEDTERM4DS_SEARCH_INDEX_DIR.",
                )
            from medterm4ds.engines.fhir.semantic import get_semantic_engine
            sem_engine = get_semantic_engine()
            if not sem_engine.is_available:
                return _fhir_error(
                    503,
                    "Embedding model not found. Set MEDTERM4DS_EMBEDDING_MODEL_DIR.",
                )
            bm25_results = _bm25_search(query_text, categories, min(count * 3, 50))
            if not bm25_results:
                # BM25 returned nothing — fall back to semantic
                results = sem_engine.search(query_text, categories=categories, top_k=count)
                for r in results:
                    r["system"] = system_to_fhir_uri(r["system"].upper()) or r["system"]
                return build_bundle_search(results, query=query_text, search_mode="semantic-fallback")
            # Stage 2: SapBERT re-rank
            reranked = sem_engine.rerank(query_text, bm25_results, top_k=count)
            for r in reranked:
                r["system"] = system_to_fhir_uri(str(r.get("system", "")).upper()) or r.get("system", "")
            return build_bundle_search(reranked, query=query_text, search_mode="hybrid")

        return _fhir_error(400, f"Unknown searchMode: {search_mode}")

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


def _source_to_categories(source: str) -> list[str]:
    """Map an internal source name to BM25 search categories."""
    mapping = {
        "SNOMEDCT_US": ["condition", "lab", "medication", "procedure", "vaccine", "body_structure"],
        "ICD10CM": ["condition"],
        "ICD10PCS": ["procedure"],
        "RXNORM": ["medication"],
        "LNC": ["lab"],
        "CPT": ["procedure"],
        "HCPCS": ["procedure"],
        "CVX": ["vaccine"],
    }
    return mapping.get(source, [])


def _stem_token(token: str) -> str:
    """Simple Porter-like stemmer to match pre-built BM25 tokenization.

    The BM25 indexes use aggressive stemming (e.g., 'diabetes' → 'diabete',
    'infections' → 'infection', 'containing' → 'contain'). This stemmer
    applies common English suffix stripping to approximate that.
    """
    token = token.lower()
    for suffix in ("ational", "tional", "iveness", "fulness", "ousness",
                   "ization", "ization", "ation", "ations",
                   "izer", "ator", "alism", "iciti", "ical", "ness",
                   "ements", "ement", "ments", "ment",
                   "iences", "ience", "iable", "iable",
                   "ing", "ies", "ied", "ies", "ied",
                   "ily", "ily",
                   "sses", "ies", "ss", "s",
                   "y", "e"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            if suffix == "ies":
                return token[:-3] + "i"
            if suffix == "ied":
                return token[:-3] + "i"
            if suffix == "sses":
                return token[:-2]
            if suffix == "s" and not token.endswith("ss"):
                return token[:-1]
            if suffix == "e" and token.endswith("e") and len(token) > 3:
                return token[:-1]
            if suffix == "ing":
                return token[:-3]
            if suffix == "y":
                return token[:-1] + "i"
            return token[: -len(suffix)]
    return token


def _bm25_search(query: str, categories: list[str], count: int) -> list[dict[str, Any]]:
    """BM25 lexical search using pre-built inverted indexes.

    Supports two index formats:
    1. Pre-built BM25 (dict with postings/idf/rid_to_code): proper BM25 scoring
    2. Flat document list (legacy): token-overlap scoring
    """
    import math

    query_tokens = query.lower().split()
    if not query_tokens:
        return []

    results: list[dict[str, Any]] = []
    for category in categories:
        index = _bm25_indexes.get(category)
        if index is None:
            continue

        if isinstance(index, dict) and "postings" in index:
            # Pre-built BM25 inverted index
            postings = index["postings"]
            idf = index.get("idf", {})
            doc_lengths = index.get("doc_lengths", [])
            avg_doc_length = index.get("avg_doc_length", 20.0) or 20.0
            rid_to_code = index.get("rid_to_code", [])
            rid_to_friendly = index.get("rid_to_friendly_name", [])
            rid_to_system = index.get("rid_to_system", [])

            # Accumulate BM25 scores per document
            scores: dict[int, float] = {}
            for raw_token in query_tokens:
                # Try raw token, then stemmed version
                token = raw_token
                if token not in postings:
                    token = _stem_token(raw_token)
                if token not in postings:
                    continue
                token_idf = idf.get(token, idf.get(raw_token, 1.0))
                for entry in postings[token]:
                    if isinstance(entry, list) and len(entry) >= 2:
                        rid, tf = int(entry[0]), float(entry[1])
                    else:
                        continue
                    doc_len = doc_lengths[rid] if rid < len(doc_lengths) else avg_doc_length
                    # BM25 formula: k1=1.5, b=0.75
                    k1, b = 1.5, 0.75
                    tf_component = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_doc_length))
                    scores[rid] = scores.get(rid, 0.0) + token_idf * tf_component

            # Convert to results
            for rid, score in sorted(scores.items(), key=lambda x: -x[1])[:count]:
                code = rid_to_code[rid] if rid < len(rid_to_code) else str(rid)
                display = rid_to_friendly[rid] if rid < len(rid_to_friendly) else code
                sys_name = rid_to_system[rid] if rid < len(rid_to_system) else ""
                system_uri = system_to_fhir_uri(sys_name.upper()) if sys_name else ""
                # Normalize BM25 score to 0-1 range (rough: divide by max possible)
                normalized = min(score / (sum(idf.get(t, 1.0) for t in query_tokens) * 2.5 + 0.001), 1.0)
                results.append({
                    "code": str(code),
                    "system": system_uri or sys_name,
                    "display": display,
                    "score": round(normalized, 4),
                    "match_grade": _score_to_grade(normalized),
                })

        elif isinstance(index, list):
            # Legacy flat document list — token overlap scoring
            query_token_set = set(query_tokens)
            for doc in index:
                text_parts = []
                for field in ("friendly_name", "name", "display"):
                    val = doc.get(field) if isinstance(doc, dict) else None
                    if val:
                        text_parts.append(str(val).lower())
                for syn in (doc.get("synonyms") or [] if isinstance(doc, dict) else []):
                    text_parts.append(str(syn).lower())
                doc_text = " ".join(text_parts)
                doc_tokens = set(doc_text.split())
                overlap = len(query_token_set & doc_tokens)
                if overlap == 0:
                    continue
                score = overlap / math.sqrt(len(query_token_set) * len(doc_tokens or 1))
                code_info = doc.get("code", doc) if isinstance(doc, dict) else {}
                results.append({
                    "code": str(code_info.get("code", "")) if isinstance(code_info, dict) else str(doc.get("id", "")),
                    "system": system_to_fhir_uri(str(code_info.get("source", "")).upper()) if isinstance(code_info, dict) else "",
                    "display": doc.get("friendly_name") or doc.get("name", "") if isinstance(doc, dict) else "",
                    "score": round(score, 4),
                    "match_grade": _score_to_grade(score),
                })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:count]


def _score_to_grade(score: float) -> str:
    """Map a normalized search score to a match-grade (Patient $match pattern)."""
    if score >= 0.8:
        return "certain"
    if score >= 0.4:
        return "probable"
    return "possible"


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
