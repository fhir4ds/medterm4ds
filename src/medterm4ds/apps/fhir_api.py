"""FHIR R4 terminology server facade for medterm4ds.

Exposes $lookup, $validate-code, $translate, and $search operations
over standard FHIR R4 HTTP endpoints. Binds to 127.0.0.1 by default
(localhost-only multi-process sidecar; see SECURITY.md).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from medterm4ds.services.hierarchy import get_descendants
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.mapping import get_code_mappings
from medterm4ds.services.patient_friendly import get_patient_friendly_names

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8001
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


def _load_bm25_indexes(search_dir: str) -> None:
    """Load pre-built BM25 JSON indexes on startup."""
    search_path = Path(search_dir)
    if not search_path.is_dir():
        logger.warning("BM25 search index dir not found: %s — $search will return 503", search_dir)
        return
    for category in ("condition", "lab", "medication", "procedure", "vaccine", "body_structure"):
        json_path = search_path / f"{category}_bm25.json"
        if json_path.exists():
            try:
                with json_path.open() as f:
                    docs = json.load(f)
                if isinstance(docs, list):
                    _bm25_indexes[category] = docs
                    _bm25_doc_count[category] = len(docs)
                    logger.info("  BM25 %s: %d records", category, len(docs))
            except Exception:
                logger.exception("Failed to load BM25 index: %s", json_path)


def _load_patient_friendly_cache(db_path: Path) -> None:
    """Load patient_friendly JSONs for fast $lookup custom properties."""
    baseline = Path(os.getenv("MEDTERM4DS_FHIR4PX_BASELINE", "/mnt/d/medterm4ds/reports/fhir4px"))
    for source_lower in ("snomedct_us", "rxnorm", "icd10cm", "lnc", "cvx"):
        json_path = baseline / f"patient_friendly_{source_lower}.json"
        if json_path.exists():
            try:
                with json_path.open() as f:
                    _PATIENT_FRIENDLY_CACHE[source_lower] = json.load(f)
            except Exception:
                logger.debug("Could not load %s", json_path)


def _get_patient_friendly(source: str, code: str) -> dict[str, Any] | None:
    """Look up pre-computed patient-friendly data for a code."""
    source_lower = source.lower()
    cache = _PATIENT_FRIENDLY_CACHE.get(source_lower, {})
    return cache.get(code)


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
        app.state.con = con
        app.state.engine = engine
        app.state.ready = True
        # Load BM25 + patient_friendly caches
        _load_bm25_indexes(app_settings.search_index_dir)
        _load_patient_friendly_cache(app_settings.db_path)
        logger.info("FHIR API ready on 127.0.0.1:%d", DEFAULT_PORT)
        try:
            yield
        finally:
            app.state.ready = False
            con.close()

    app = FastAPI(
        title="medterm4ds FHIR Terminology Server",
        version="0.0.1",
        lifespan=lifespan,
    )

    def _engine(request) -> LocalDuckDBEngine:
        if not getattr(request.app.state, "ready", False):
            raise _fhir_error(503, "Service is starting up.")
        return request.app.state.engine

    def _fhir_error(status: int, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content=build_operation_outcome("error", "processing", message),
        )

    # -- CapabilityStatement --
    @app.get("/fhir/metadata")
    async def metadata():
        return build_capability_statement(f"http://127.0.0.1:{DEFAULT_PORT}")

    # -- CodeSystem $lookup --
    @app.get("/fhir/CodeSystem/$lookup")
    async def lookup_get(
        request: Request,
        system: str = Query(..., description="FHIR system URI"),
        code: str = Query(..., description="The code to look up"),
        version: str | None = Query(None),
    ):
        return _do_lookup(request, system, code)

    @app.post("/fhir/CodeSystem/$lookup")
    async def lookup_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code = params.get("code")
        if not system or not code:
            return _fhir_error(400, "system and code are required.")
        return _do_lookup(request, system, code)

    def _do_lookup(request: Request, system_uri: str, code: str):
        source = fhir_uri_to_system(system_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
        engine = _engine(request)
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
        return _do_validate(request, system, code)

    @app.post("/fhir/CodeSystem/$validate-code")
    async def validate_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code = params.get("code")
        if not system or not code:
            return _fhir_error(400, "system and code are required.")
        return _do_validate(request, system, code)

    def _do_validate(request: Request, system_uri: str, code: str):
        source = fhir_uri_to_system(system_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
        engine = _engine(request)
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
        return _do_translate(request, system, code, targetsystem)

    @app.post("/fhir/ConceptMap/$translate")
    async def translate_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code = params.get("code")
        targetsystem = params.get("targetsystem")
        if not system or not code:
            return _fhir_error(400, "system and code are required.")
        return _do_translate(request, system, code, targetsystem)

    def _do_translate(request: Request, source_uri: str, code: str, target_uri: str | None):
        source = fhir_uri_to_system(source_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized source system URI: {source_uri}")
        engine = _engine(request)
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
        return _do_subsumes(request, system, codeA, codeB)

    @app.post("/fhir/CodeSystem/$subsumes")
    async def subsumes_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code_a = params.get("codeA")
        code_b = params.get("codeB")
        if not system or not code_a or not code_b:
            return _fhir_error(400, "system, codeA, and codeB are required.")
        return _do_subsumes(request, system, code_a, code_b)

    def _do_subsumes(request: Request, system_uri: str, code_a: str, code_b: str):
        source = fhir_uri_to_system(system_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
        engine = _engine(request)
        if code_a == code_b:
            return build_parameters_subsumes("equivalent")
        # Check if B is a descendant of A → A subsumes B
        desc_of_a = get_descendants([CodeRef(source, code_a)], engine=engine, max_depth=20)
        if any(r.target.code == code_b for r in desc_of_a):
            return build_parameters_subsumes("subsumes")
        # Check if A is a descendant of B → A is subsumed by B
        desc_of_b = get_descendants([CodeRef(source, code_b)], engine=engine, max_depth=20)
        if any(r.target.code == code_a for r in desc_of_b):
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
        from medterm4ds.engines.fhir.closure import (
            build_closure_response,
            get_closure_manager,
        )

        params = _parse_parameters(body)
        name = params.get("name")
        if not name:
            return _fhir_error(400, "name parameter is required for $closure.")

        manager = get_closure_manager()
        engine = _engine(request)

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
        return _do_expand(request, url=url, filter_text=filter, count=count, system_uri=system)

    @app.post("/fhir/ValueSet/$expand")
    async def expand_post(request: Request, body: dict[str, Any]):
        """Expand a ValueSet. Accepts either a ValueSet resource (intensional)
        or a Parameters resource (filter mode)."""
        resource_type = body.get("resourceType", "")
        if resource_type == "ValueSet":
            return _do_expand(request, value_set=body, count=1000)
        # Parameters-style: extract url, filter, count
        params = _parse_parameters(body)
        return _do_expand(
            request,
            url=params.get("url"),
            filter_text=params.get("filter"),
            count=int(params.get("count", 20)),
            system_uri=params.get("system"),
        )

    def _do_expand(
        request: Request,
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
        engine = _engine(request)

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
        - is-a / descendant-of filters (compose.include[].filter)
        - compose.exclude (removes codes from the expansion)
        """
        compose = value_set.get("compose", {})
        contains: list[dict[str, Any]] = []

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
                        # Add the root code itself
                        root_infos = get_code_infos(
                            [CodeRef(source, root_code)], engine=engine
                        )
                        if root_infos and root_infos[0]:
                            contains.append({
                                "system": inc_system,
                                "code": root_code,
                                "display": root_infos[0].name or root_code,
                            })

                    # Walk descendants
                    descendants = get_descendants(
                        [CodeRef(source, root_code)], engine=engine, max_depth=20
                    )
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
            exc_system = exclude.get("system", "")
            exc_codes = {c.get("code") for c in exclude.get("concept", [])}
            if exc_system:
                exc_source = fhir_uri_to_system(exc_system)
                exc_codes |= {c.get("code") for c in exclude.get("concept", [])}
            contains = [c for c in contains if c["code"] not in exc_codes]

        # Deduplicate by (system, code)
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for c in contains:
            key = (c["system"], c["code"])
            if key not in seen:
                seen.add(key)
                deduped.append(c)

        truncated = len(deduped) > count
        return build_valueset_expand(deduped[:count], url=value_set.get("url"))

    def _expand_url_pattern(engine, url: str, count: int):
        """Expand a fhir_vs URL pattern.

        Supports SNOMED intensional URLs like:
          http://snomed.info/sct/404684003?fhir_vs=isa
          http://snomed.info/sct/404684003?fhir_vs
        """
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        # Extract code from path (e.g., /sct/404684003)
        path_parts = parsed.path.strip("/").split("/")
        query_params = parse_qs(parsed.query)
        fhir_vs = query_params.get("fhir_vs", [""])[0]

        # SNOMED pattern: /sct/<code>?fhir_vs[=isa|descendents]
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

            descendants = get_descendants(
                [CodeRef(source, code)], engine=engine, max_depth=20
            )
            for d in descendants:
                contains.append({
                    "system": system_uri,
                    "code": d.target.code,
                    "display": d.target_display or d.target.code,
                })

            return build_valueset_expand(contains[:count], url=url)

        return _fhir_error(400, f"Unsupported fhir_vs URL pattern: {url}")

    def _resolve_sources(system_uri: str | None) -> list[str] | None:
        """Resolve a FHIR system URI to a list of medterm4ds source names."""
        if system_uri:
            source = fhir_uri_to_system(system_uri)
            return [source] if source else None
        return ["SNOMEDCT_US", "ICD10CM", "RXNORM", "LNC"]

    # -- CodeSystem $search (custom, modeled after Patient $match) --
    @app.get("/fhir/CodeSystem/$search")
    async def search_get(
        request: Request,
        query: str = Query(..., description="Text to search for"),
        system: str | None = Query(None, description="Restrict to system URI"),
        count: int = Query(20, ge=1, le=200),
        searchMode: str = Query("lexical", pattern="^(lexical|hybrid|semantic)$"),
    ):
        return _do_search(request, query, system, count, searchMode)

    @app.post("/fhir/CodeSystem/$search")
    async def search_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        query_text = params.get("query") or params.get("_query")
        system = params.get("system")
        count = int(params.get("count", 20))
        search_mode = params.get("searchMode", "lexical")
        if not query_text:
            return _fhir_error(400, "query is required.")
        return _do_search(request, str(query_text), system, count, search_mode)

    def _do_search(
        request: Request,
        query_text: str,
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


def _bm25_search(query: str, categories: list[str], count: int) -> list[dict[str, Any]]:
    """Simple lexical search across BM25 JSON indexes.

    The pre-built BM25 JSON files contain per-document metadata with
    friendly_name, technical name, and synonyms. This does a case-insensitive
    substring + token-overlap ranking.
    """
    import math
    from collections import Counter

    query_tokens = set(query.lower().split())
    if not query_tokens:
        return []

    results: list[dict[str, Any]] = []
    for category in categories:
        docs = _bm25_indexes.get(category, [])
        for doc in docs:
            # Build searchable text from document fields
            text_parts = []
            for field in ("friendly_name", "name", "display"):
                val = doc.get(field)
                if val:
                    text_parts.append(str(val).lower())
            for syn in (doc.get("synonyms") or []):
                text_parts.append(str(syn).lower())
            doc_text = " ".join(text_parts)
            doc_tokens = set(doc_text.split())

            # Token overlap score (simple BM25-like)
            overlap = len(query_tokens & doc_tokens)
            if overlap == 0:
                continue
            score = overlap / math.sqrt(len(query_tokens) * len(doc_tokens or 1))

            # Get the canonical code info from the doc
            code_info = doc.get("code") or doc
            results.append({
                "code": str(code_info.get("code", doc.get("id", ""))),
                "system": system_to_fhir_uri(str(code_info.get("source", "")).upper()) or code_info.get("source", ""),
                "display": doc.get("friendly_name") or doc.get("name") or doc.get("display", ""),
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
