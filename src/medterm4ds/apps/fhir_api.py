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
import duckdb
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from medterm4ds.apps._asyncutil import run_db as _run_db
from medterm4ds.core.config import local_duckdb_config
from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.fhir import (
    canonical_system_uri,
    fhir_uri_to_system,
    sab_label_to_fhir_uri,
    system_to_fhir_uri,
)
from medterm4ds.engines.fhir.responses import (
    MATCH_GRADE_EXTENSION_URL,
    build_bundle_search,
    build_capability_statement,
    build_operation_outcome,
    build_parameters_lookup,
    build_parameters_subsumes,
    build_parameters_translate,
    build_parameters_validate,
    build_terminology_capabilities,
    build_valueset_expand,
)
from medterm4ds.engines.fhir.xml import to_fhir_xml
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


def _resolve_max_depth(default: int = 5) -> int:
    """Read ``FHIR_VS_MAX_DEPTH`` env var with defensive parsing.

    Returns the integer depth cap, falling back to ``default`` on missing,
    non-numeric, or negative values. Non-numeric / negative values log a
    WARNING (per GLOBAL_RULES "Silent Fallbacks" — operator misconfiguration
    is a real signal that must NOT propagate as a raw Python traceback
    through the HTTP layer).

    VS-04 SKEPTIC QA-066: the prior ``int(os.getenv(...))`` raised
    ``ValueError: invalid literal for int()`` on non-numeric values, which
    propagated as a 500 error with a raw traceback (information-disclosure
    surface). Reference:
    https://hl7.org/fhir/R4/operationoutcome.html (OperationOutcome mandate).

    VS-04 HISTORIAN QA-067: negative values were silently accepted and
    produced silent-wrong-answer in ``expand_url_pattern`` — the QA-065
    synthesis (``if max_depth == 0: depth_cap_hit = True``) only covers
    ``max_depth == 0``, not negative values. The cleanest fix is to reject
    negatives here (operator misconfiguration) rather than extend the
    synthesis at every call site. Per the depth-cap contract: 0 means
    "root only" (explicit truncation signal); negative values have no
    spec-defined meaning and are almost certainly operator typos.
    """
    raw = os.getenv("FHIR_VS_MAX_DEPTH")
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "FHIR_VS_MAX_DEPTH=%r is not a valid integer; using default %d. "
            "Set to a non-negative integer to override the descendant depth cap.",
            raw, default,
        )
        return default
    if value < 0:
        logger.warning(
            "FHIR_VS_MAX_DEPTH=%d is negative; using default %d. Negative "
            "values are not meaningful (use 0 for root-only expansion).",
            value, default,
        )
        return default
    return value


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
        ValueError: If the URL pattern is unsupported, the system URI is
            unrecognized, OR the ``fhir_vs`` value is unrecognized.
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

        # VS-04 SKEPTIC QA-060 / QA-061 / QA-062: the fhir_vs value dispatch
        # MUST be explicit about which values are recognized AND what each
        # value means (include_root, walk_descendants, walk_refset). The
        # prior implementation used `include_root = fhir_vs in ("", "isa",
        # "refset")` and walked descendants UNCONDITIONALLY — producing
        # silent-wrong-answer for unrecognized values (descendants-only,
        # root excluded) and for case variants (ISA, Isa). The dispatch
        # table below is the narrowest fix: it (a) accepts case-insensitive
        # value lookup (per SNOMED CT URL conventions), (b) raises
        # ValueError for unrecognized values (no silent partial), and
        # (c) treats `refset` as an UNIMPLEMENTED operation (medterm4ds
        # lacks SNOMED refset data) — surfacing this as an explicit error
        # rather than silently equating to isa.
        # Reference: https://hl7.org/fhir/R4/snomedct.html (Implicit Value Sets)
        fhir_vs_normalized = fhir_vs.lower()
        if fhir_vs_normalized not in ("", "isa", "refset"):
            raise ValueError(
                f"Unsupported fhir_vs value: {fhir_vs!r}. Supported values "
                f"are 'isa' (root + descendants), 'refset' (refset members), "
                f"and the bare form '?fhir_vs' (equivalent to 'isa'). URL: {url!r}"
            )
        if fhir_vs_normalized == "refset":
            # medterm4ds does not load SNOMED CT refset data (no mrrefset
            # table). Surface this explicitly rather than silently equating
            # to isa. The error is FHIR-shaped via the HTTP layer's
            # _expand_url_pattern ValueError → 400 OperationOutcome mapping.
            # Reference: https://hl7.org/fhir/R4/snomedct.html (Reference Sets)
            raise ValueError(
                f"?fhir_vs=refset is not implemented: medterm4ds does not "
                f"load SNOMED CT Reference Set data. URL: {url!r}"
            )
        # isa or bare (equivalent): include root + walk descendants.
        include_root = True

        contains: list[dict[str, Any]] = []
        if include_root:
            root_infos = get_code_infos([CodeRef(source, code)], engine=engine)
            if root_infos and root_infos[0]:
                contains.append({
                    "system": system_uri,
                    "code": code,
                    "display": root_infos[0].name or code,
                })

        max_depth = _resolve_max_depth()
        # VS-04 TERMINOLOGIST QA-068: descendant_budget MUST reflect the
        # remaining slots after root placement — ``max(0, count - len(contains))``
        # NOT ``max(1, ...)``. The prior ``max(1, ...)`` clamping always
        # allowed at least 1 descendant even when count=1 (root only fits),
        # causing the descendant walk to fetch and add a descendant that
        # ``contains[:count]`` then silently dropped — but the count_limited
        # signal ALSO didn't fire because the implementation tested
        # ``len(relations) >= descendant_budget`` (>=, not >), so a complete
        # expansion at exactly the budget incorrectly fired the toocostly
        # extension. The "+1 probe" + strict-greater-than pattern mirrors
        # the sibling call sites at lines 2272 and 2439
        # (``len(deduped) > count`` / ``len(rows) > count``) and is the
        # load-bearing contract for "more existed beyond the cap".
        # Reference: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
        descendant_budget = max(0, count - len(contains))
        relations, depth_cap_hit = get_descendants_bfs(
            CodeRef(source=source, code=code),
            engine=engine,
            max_depth=max_depth,
            # Probe one past the budget so count_limited can distinguish
            # "BFS exhausted at exactly the budget" (NOT truncated) from
            # "BFS hit the limit with more remaining" (truncated).
            limit=(descendant_budget + 1) if descendant_budget > 0 else 1,
        )
        # count_limited: more descendants observed than fit in the budget.
        count_limited = len(relations) > descendant_budget
        for rel in relations[:descendant_budget]:
            contains.append({
                "system": system_uri,
                "code": rel.target.code,
                "display": rel.target_display or rel.target.code,
            })
        # VS-04 SKEPTIC QA-065: when max_depth=0 (operator explicitly caps
        # at root-only), the descendant walk produces no relations BUT the
        # expansion is still truncated — the client must see the toocostly
        # extension to know more concepts exist beyond the cap. Prior code
        # returned no extension because BFS with max_depth<1 returns
        # depth_cap_hit=False (early exit before the loop). Synthesize the
        # signal here when the env var is explicitly zero.
        # Reference: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
        if max_depth == 0:
            depth_cap_hit = True
        extensions = _truncation_extensions(
            count_limited=count_limited,
            depth_cap_hit=depth_cap_hit,
            count=count,
            max_depth=max_depth,
        )

        # VS-02 SKEPTIC QA-057: pass the UN-truncated size as ``total``
        # so the response's ``expansion.total`` reflects the full expansion
        # size, not the post-truncation count. Per FHIR R4 §4.9.2: "The
        # total number of concepts in the expansion." Clients paging rely
        # on this field to know how many entries to expect across all pages.
        #
        # VS-04 TERMINOLOGIST QA-068 (continued): when count_limited is
        # True, the contains list was sliced to ``descendant_budget``
        # descendants (plus root), but at least one more descendant was
        # observed beyond the budget. The ``total`` MUST reflect this
        # observation: AT LEAST ``len(contains) + 1`` entries. The exact
        # un-truncated count would require an unbounded BFS walk (CF-
        # HISTORIAN-VS02-01); for now we surface the lower bound so
        # clients paging via ``total`` know to request additional pages.
        if count_limited:
            # Contains has root + descendant_budget descendants = the cap
            # value; at least one more descendant exists beyond it.
            total = len(contains) + 1
        else:
            total = len(contains)
        return build_valueset_expand(
            contains[:count], url=url, extensions=extensions, total=total,
        )

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
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import Response
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

    # FHIR R4 §3.1.0.1.5: "The OperationOutcome may be returned with any HTTP 4xx
    # or 5xx response." FastAPI's default 422 RequestValidationError response is a
    # {'detail': [...]} body that is NOT a FHIR OperationOutcome — clients validating
    # the response shape will reject it. Register an exception handler that converts
    # validation errors into OperationOutcome. Found by SKEPTIC iteration TS-02 (QA-020).
    @app.exception_handler(RequestValidationError)
    async def _fhir_validation_exception_handler(request: Request, exc: RequestValidationError):
        # Compose a brief diagnostics string from the validation errors. Cap at
        # MAX_ERROR_FIELD_CHARS — the same XSS / log-injection defense used in
        # _fhir_error. Missing-required errors produce a short, readable message.
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("query", "body"))
            msg = first.get("msg", "Invalid request")
            diagnostics = f"Parameter '{loc or 'unknown'}': {msg}"
            if len(errors) > 1:
                diagnostics += f" (and {len(errors) - 1} more validation error(s))"
        else:
            diagnostics = "Request validation failed."
        return _fhir_error(422, diagnostics)

    # CF-HISTORIAN-CS04-02 (milestone-2 review, CR-019): systemic
    # ``duckdb.Error`` boundary for every per-operation ``_do_*`` handler.
    # Without it, a transient DuckDB operational failure (connection issue,
    # lock contention, OOM) propagates past the per-operation handler to
    # Starlette's default 500 with ``text/plain`` body — non-conformant per
    # FHIR R4 §3.1.0.1.5 (OperationOutcome on 4xx/5xx) + §3.1.0.1.9 (correct
    # MIME type). The narrow exception type (``duckdb.Error``, not
    # ``Exception``) per GLOBAL_RULES.md "Silent Fallbacks" — programming
    # bugs (TypeError, AttributeError, KeyError) MUST propagate.
    # Status code 503 (Service Unavailable) signals transient DB issues;
    # clients can retry with backoff. The batch dispatcher's own
    # ``try: ... except Exception`` boundary (TS-04 HISTORIAN QA-038)
    # already catches this for batch invocations; this handler covers the
    # per-operation (single-entry) path which was the systemic gap.
    @app.exception_handler(duckdb.Error)
    async def _duckdb_error_handler(request: Request, exc: duckdb.Error):
        logger.warning(
            "DuckDB operational failure on %s: %s", request.url.path, exc
        )
        return _fhir_error_response(
            request, 503, f"Database temporarily unavailable: {exc}"
        )

    def _engine(request) -> LocalDuckDBEngine:
        return request.app.state.engine

    def _check_ready(request) -> Response | None:
        """Enforce the 503 startup gate. Called by handlers that don't otherwise
        need the engine (e.g. $search, $extract) so they 503 cleanly during
        startup instead of running against empty caches.

        Returns a 503 Response when the app is not ready, else None. Callers
        check the return value and return early if non-None — do NOT raise
        the Response. CR-005 (milestone-1 review, still open at milestone-2):
        ``raise _fhir_error(...)`` raised ``TypeError`` because ``Response`` is
        not a ``BaseException`` subclass — exactly the failure shape the
        surrounding defensive code was meant to prevent (Python traceback body
        with text/plain Content-Type). The bug was latent (``ready=True``
        throughout normal server lifecycle) but fired on any in-flight request
        during shutdown/startup transient states. Spec: FHIR R4 §3.1.0.1.5
        (OperationOutcome on 4xx/5xx) + §3.1.0.1.9 (correct MIME type).
        """
        if not getattr(request.app.state, "ready", False):
            return _fhir_error(503, "Service is starting up.")
        return None

    def _executor(request) -> ThreadPoolExecutor:
        return request.app.state.db_executor

    def _ner_executor(request) -> ThreadPoolExecutor:
        return request.app.state.ner_executor

    def _fhir_error(status: int, message: str) -> Response:
        # Sanitize: strip control chars + cap length. Defends against reflected
        # XSS in EHR clients that render OperationOutcome.diagnostics as HTML,
        # and against log-injection via newline/control chars in user-supplied
        # system URIs and codes.
        clean = "".join(c for c in message if c >= " " or c == " ")
        if len(clean) > MAX_ERROR_FIELD_CHARS:
            clean = clean[:MAX_ERROR_FIELD_CHARS] + "…"
        # Per §3.1.0.1.9: error responses MUST also use the FHIR MIME type,
        # not application/json. Goes through _fhir_json_response so the
        # Content-Type is uniform across success and error paths.
        return _fhir_json_response(
            build_operation_outcome("error", "processing", clean),
            status=status,
        )

    def _fhir_error_response(
        request: Request, status: int, message: str
    ) -> Response:
        """Accept-aware error response builder.

        Per FHIR R4 §3.1.0.1.9 / §3.1.0.1.5: when a client negotiates XML via
        ``Accept: application/fhir+xml`` or ``_format=xml``, the error path
        SHOULD honor the same negotiation — not unconditionally emit JSON. The
        plain ``_fhir_error`` helper always returns JSON, which is a format
        mismatch on the error path. This variant wraps the OperationOutcome
        payload through ``_fhir_response`` so XML clients get an XML body and
        ``Content-Type: application/fhir+xml``.

        CR-003 (milestone-1 review): use this helper at any error site where
        (a) the calling handler has ``request`` in scope AND (b) the success
        path of the same handler already routes through ``_fhir_response``.
        Sites without ``request`` (e.g. the batch dispatcher's per-entry error
        builder, the FastAPI RequestValidationError handler) keep using
        ``_fhir_error`` — XML-on-error is out of scope for those paths today
        and threading ``request`` through them is significant churn.
        """
        clean = "".join(c for c in message if c >= " " or c == " ")
        if len(clean) > MAX_ERROR_FIELD_CHARS:
            clean = clean[:MAX_ERROR_FIELD_CHARS] + "…"
        return _fhir_response(
            request,
            build_operation_outcome("error", "processing", clean),
            status=status,
        )

    def _wants_xml(request: Request) -> bool:
        """Negotiate XML vs JSON from the Accept header OR the _format query param.

        FHIR R4 §4.7.1.1 item 1 requires the server to honor both
        ``application/fhir+xml`` and ``application/fhir+json``. Default is JSON.

        Per §3.1.0.1.11, the ``_format`` query parameter overrides the Accept
        header (it exists for clients that cannot set headers, e.g. some XSLT
        pipelines). The values ``xml``, ``text/xml``, ``application/xml``, and
        ``application/fhir+xml`` SHALL be interpreted to mean XML.
        """
        # _format takes precedence over Accept per §3.1.0.1.11.
        fmt = request.query_params.get("_format", "").lower().strip()
        if fmt in ("xml", "text/xml", "application/xml", "application/fhir+xml"):
            return True
        if fmt in ("json", "application/json", "application/fhir+json"):
            return False
        # _format absent or unrecognized → fall through to Accept header.
        accept = request.headers.get("accept", "").lower()
        # Honor explicit fhir+xml / fhir+json. Fall back to */* → JSON.
        if "application/fhir+xml" in accept or accept.endswith("/xml"):
            return True
        return False

    def _fhir_response(
        request: Request, payload: dict[str, Any], *, status: int = 200
    ) -> Any:
        """Render a FHIR resource as XML or JSON based on Accept/_format.

        Conformance: §4.7.1.1 item 1 requires XML and JSON support. This is
        the single dispatch point — every handler that returns a FHIR resource
        should funnel through here so format support is uniform.

        Per §3.1.0.1.9 the correct FHIR MIME types MUST be used:
        ``application/fhir+xml`` and ``application/fhir+json``. Using
        ``JSONResponse`` directly would emit ``application/json`` (Starlette
        default) which violates the spec — clients that strictly validate the
        Content-Type against the FHIR MIME type would reject the response.
        """
        if _wants_xml(request):
            try:
                body = to_fhir_xml(payload)
            except ValueError as exc:
                # Resource shape not serializable to XML — degrade to JSON so
                # the client still gets a structured response. Logged at
                # WARNING because the conformance advertisement promises XML
                # support; silent degradation would be a fallback anti-pattern.
                logger.warning(
                    "FHIR XML serialization failed for %s: %s — returning JSON.",
                    payload.get("resourceType", "<unknown>"), exc,
                )
                return _fhir_json_response(payload, status=status)
            return Response(
                content=body,
                status_code=status,
                media_type="application/fhir+xml",
            )
        return _fhir_json_response(payload, status=status)

    def _fhir_json_response(payload: dict[str, Any], *, status: int = 200) -> Response:
        """Serialize ``payload`` as JSON with the FHIR-spec MIME type.

        Per §3.1.0.1.9: 'The correct mime type SHALL be used by clients and
        servers' — JSON responses MUST be ``application/fhir+json``, not the
        generic ``application/json`` Starlette emits by default.

        Note: ``json.dumps`` is used without ``default=str`` or any other
        fallback encoder. A non-serializable value MUST raise TypeError so
        the bug surfaces — adding ``default=str`` here would be a silent-
        fallback anti-pattern (see GLOBAL_RULES.md). All ``build_*`` helpers
        in ``engines/fhir/responses.py`` return dicts of primitives, so this
        never fires today; the gate exists to prevent future regressions.
        """
        import json as _json

        return Response(
            content=_json.dumps(payload),
            status_code=status,
            media_type="application/fhir+json",
        )

    # -- CapabilityStatement / TerminologyCapabilities --
    def _deployment_base_url() -> str:
        """Construct the deployment base URL from env vars.

        Per GLOBAL_RULES.md "FHIR API Specifics": CapabilityStatement endpoint
        URLs MUST reflect MEDTERM4DS_API_HOST and MEDTERM4DS_FHIR_API_PORT.

        Scheme handling (found by SKEPTIC iteration TS-04, QA-037):
        The prior `f"http://{host}:{port}"` literal produced a malformed
        `http://https://host:port` URL when the operator set
        MEDTERM4DS_API_HOST=https://fhir.example.com to advertise an HTTPS
        deployment behind a reverse proxy. Per §4.7.2 'Servers SHOULD ensure
        that all interactions occur over a secure connection' — the
        advertisement MUST NOT silently downgrade an HTTPS deployment to
        plain HTTP.

        Resolution rules:
        1. If MEDTERM4DS_API_HOST carries an explicit scheme (e.g.
           `https://host`), honor it as-is and append port only when the
           scheme's default port isn't already present.
        2. Else use `http://` as the default scheme (localhost dev / behind
           an HTTP reverse proxy). Operators wanting HTTPS MUST set the
           host env var with a scheme OR set MEDTERM4DS_API_SCHEME=https.
        """
        host = os.getenv("MEDTERM4DS_API_HOST", "127.0.0.1")
        port = int(os.getenv("MEDTERM4DS_FHIR_API_PORT", str(DEFAULT_PORT)))
        # Honor an explicit scheme-on-host (e.g. "https://fhir.example.com")
        # or a separate scheme env var. The prior behavior hardcoded "http://"
        # which silently downgraded HTTPS deployments (QA-037).
        #
        # Edge cases (found by HISTORIAN TS-04 QA-040):
        # - IPv6 host (e.g. "https://[::1]"): the previous port-stripping
        #   check `":" not in stripped.split("://", 1)[1]` evaluated False
        #   for IPv6 (brackets contain `:`), causing the port to be dropped.
        # - Trailing slash on host without scheme (e.g. "example.com/"):
        #   the previous strip-trailing-slash logic only ran when the host
        #   had a scheme, so the slash ended up between host and port
        #   ("http://example.com/:8000").
        # Fix: normalize host by stripping trailing slashes unconditionally
        # and detect IPv6 by bracket presence rather than colon presence.
        if "://" in host:
            # Host already carries scheme — strip trailing slash and use as-is.
            # Append port only if the host string doesn't already include one
            # (excluding IPv6 brackets which always contain `:`).
            stripped = host.rstrip("/")
            scheme_part, rest = stripped.split("://", 1)
            # IPv6 hosts are bracketed: [::1]:port or [::1]. If the host
            # already has a port (form [::1]:port OR hostname:port), don't
            # append another.
            # Detect existing port by checking for `]:NNN` (IPv6) or `:NNN`
            # at the end (regular host) — must come after the host portion.
            if rest.startswith("["):
                # IPv6 form. Look for `]:` to find a port.
                if "]" in rest and rest.rfind("]:") != -1:
                    return stripped  # already has port
                return f"{stripped}:{port}"
            # Regular host: a colon AFTER the first character indicates port.
            # (hostname cannot contain `:`.)
            if ":" in rest:
                return stripped  # already has port
            return f"{stripped}:{port}"
        # Strip trailing slash from host portion (defends against operator
        # typos like "example.com/" producing malformed "host/:port").
        host = host.rstrip("/")
        scheme = os.getenv("MEDTERM4DS_API_SCHEME", "http")
        return f"{scheme}://{host}:{port}"

    @app.get("/fhir/metadata")
    async def metadata(request: Request, mode: str | None = Query(None)):
        """FHIR R4 §4.7.1.1 items 4 + 5: capabilities interaction.

        - mode absent or "full" → CapabilityStatement (item 4)
        - mode="normative" → full CapabilityStatement (per §3.1.0.10 the
          normative-only subset is acceptable; producing the truly normative
          subset is a future enhancement — the spec also says servers MAY
          ignore mode entirely, so returning the full statement is conformant)
        - mode="terminology" → TerminologyCapabilities (item 5)
        - any other value → 400 OperationOutcome (input validation)
        """
        base_url = _deployment_base_url()

        if mode is None or mode in ("full", "normative"):
            payload = build_capability_statement(base_url)
        elif mode == "terminology":
            payload = build_terminology_capabilities(base_url)
        else:
            # Input validation per FHIR conformance — only None/full/normative/
            # terminology are valid mode values per §3.1.0.10. Returning the
            # default statement silently would be a silent-wrong-answer
            # anti-pattern (was QA-006).
            # CR-003: route through _fhir_error_response so XML clients get XML.
            return _fhir_error_response(
                request,
                400,
                f"Invalid mode parameter value {mode!r}. "
                "Valid values: 'full', 'normative', or 'terminology'.",
            )
        return _fhir_response(request, payload)

    # -- Batch / Transaction endpoint --
    # FHIR R4 §4.7.8 / §4.7.10 + §3.7 (HTTP Batch/Transaction):
    # 'A client can execute multiple operations in a single HTTP request by
    # submitting a Bundle with type=batch to the FHIR endpoint.' The server
    # returns a Bundle with type=batch-response, with one entry per request
    # entry, in the same order.
    #
    # Spec: https://hl7.org/fhir/R4/http.html#transaction
    #
    # Found missing by SKEPTIC iteration TS-04 (QA-036). Without an explicit
    # POST /fhir route, Starlette returns its default 404 — non-conformant.
    #
    # Implementation: process each entry by dispatching to the existing
    # operation handlers (re-using the same _do_* callables). Per-entry
    # error isolation: a malformed entry MUST NOT poison the whole batch
    # (§3.7: 'In a batch ... each entry is processed independently ... the
    # response for each entry is independent of the other entries').
    #
    # Bundle type=transaction is documented as atomic (all-or-nothing) per
    # §3.7. medterm4ds is read-only and has no persistence layer; we accept
    # transaction Bundles and process them as if they were batch (independent
    # entries) — there's nothing to roll back. The behavior is conformant
    # because every operation exposed is side-effect-free.
    @app.post("/fhir")
    async def batch_endpoint(request: Request, body: dict[str, Any]):
        """Process a FHIR R4 batch Bundle (POST /fhir).

        Per §3.7 the server MUST return a Bundle with type=batch-response,
        containing one entry per request entry, in the same order, with
        response.status and resource (or outcome) for each.

        Per-entry error isolation: a malformed entry produces a 4xx
        OperationOutcome response for THAT entry only; the other entries
        are processed independently.
        """
        # Body shape validation. The body MUST be a Bundle; anything else
        # is a 400 OperationOutcome (per GLOBAL_RULES.md "Silent Fallbacks"
        # — return a structured FHIR error, never a Starlette default).
        resource_type = body.get("resourceType")
        if resource_type != "Bundle":
            return _fhir_error(
                400,
                f"POST /fhir requires a Bundle body; got resourceType={resource_type!r}.",
            )
        bundle_type = body.get("type")
        if bundle_type not in ("batch", "transaction"):
            return _fhir_error(
                400,
                f"POST /fhir requires Bundle.type=batch (or transaction); "
                f"got type={bundle_type!r}.",
            )
        # Note: we accept `transaction` and process it as `batch` (above).
        # If a future chunk adds write operations, transaction MUST be
        # re-implemented with proper atomicity.

        request_entries = body.get("entry", [])
        if not isinstance(request_entries, list):
            return _fhir_error(
                400,
                "Bundle.entry must be a list (or omitted for an empty batch).",
            )

        response_entries: list[dict[str, Any]] = []
        for entry in request_entries:
            response_entries.append(
                await _process_batch_entry(request, entry)
            )

        return _fhir_response(
            request,
            {
                "resourceType": "Bundle",
                "type": "batch-response",
                "entry": response_entries,
            },
        )

    async def _process_batch_entry(request: Request, entry: dict[str, Any]) -> dict[str, Any]:
        """Process one entry from a batch Bundle and return a response entry.

        Returns a dict with shape:
            {"response": {"status": "<code>"}, "resource": <FHIR resource>}

        Per-entry error isolation: any malformed shape, unknown operation,
        or operation error produces a 4xx/5xx OperationOutcome for THIS entry
        only — the caller continues processing the remaining entries.
        """
        if not isinstance(entry, dict):
            return _batch_error_entry(400, "Bundle entry must be a JSON object.")

        req_block = entry.get("request")
        if not isinstance(req_block, dict):
            return _batch_error_entry(
                400, "Bundle entry missing 'request' block."
            )
        method = req_block.get("method", "").upper()
        url = req_block.get("url", "")
        if not method or not url:
            return _batch_error_entry(
                400,
                "Bundle entry.request requires both 'method' and 'url'.",
            )

        # Normalize the URL: strip an absolute prefix (host) so we dispatch
        # against our local routes. Per §3.7: 'The url element ... can be
        # either absolute or relative.'
        path, query_string = _parse_batch_entry_url(url)
        if path is None:
            return _batch_error_entry(
                400, f"Unparseable entry.request.url: {url!r}."
            )

        # Parameters: from query string (GET) OR from entry.resource (POST).
        # Per §3.7 GET entries pass params via query string; POST entries
        # pass via the resource Parameters body.
        params: dict[str, str] = {}
        body_resource: dict[str, Any] | None = entry.get("resource")
        if method == "GET":
            params.update(query_string)
        elif method == "POST":
            # POST entries: the resource is a Parameters body.
            if body_resource is None:
                return _batch_error_entry(
                    400,
                    f"POST entry requires a 'resource' (Parameters body).",
                )
        elif method in ("PUT", "PATCH"):
            # medterm4ds is read-only; write methods not supported.
            return _batch_error_entry(
                405,
                f"Method {method} not supported on a read-only terminology server.",
            )
        else:
            return _batch_error_entry(
                400, f"Unsupported method {method!r} in batch entry."
            )

        # Dispatch to the appropriate operation handler. The handler returns
        # either a Response (success) or a Response wrapping an
        # OperationOutcome (error). We extract status + body.
        #
        # Per-entry error isolation (§3.7): if the dispatched operation raises
        # ANY exception that isn't caught inside _dispatch_batch_operation
        # (which catches ValueError for input-validation), we MUST catch it
        # here and return a per-entry 5xx OperationOutcome. Without this
        # catch, an unhandled TypeError / AttributeError / KeyError inside
        # one entry's _do_* handler propagates through batch_endpoint and
        # becomes a 500 with `text/plain` Content-Type for the WHOLE batch
        # — defeating per-entry error isolation (found by HISTORIAN TS-04
        # QA-038). Logged at WARNING so programming bugs aren't silent.
        try:
            return await _dispatch_batch_operation(
                request, method, path, params, body_resource,
            )
        except Exception as exc:
            # Per-entry isolation boundary. We catch broadly here because
            # this IS the spec-mandated boundary: §3.7 requires per-entry
            # independence regardless of failure mode. The narrow exception
            # practice (GLOBAL_RULES.md "Silent Fallbacks") applies to
            # operational-error handling inside the dispatched operation;
            # at the boundary we MUST catch all to honor the spec.
            logger.warning(
                "Batch entry raised unhandled exception (path=%s, method=%s): "
                "%s: %s",
                path, method, type(exc).__name__, exc,
            )
            return _batch_error_entry(
                500,
                f"Batch entry failed with an unhandled exception: "
                f"{type(exc).__name__}: {exc}",
            )

    def _batch_error_entry(status: int, message: str) -> dict[str, Any]:
        """Build a batch-response entry for a malformed/failed entry.

        Per-entry error isolation: the entry carries the OperationOutcome
        as its resource and the appropriate 4xx status in response.status.
        """
        return {
            "response": {"status": str(status)},
            "resource": build_operation_outcome(
                "error",
                "invalid" if status < 500 else "exception",
                message,
            ),
        }

    def _parse_batch_entry_url(url: str) -> tuple[str | None, dict[str, str]]:
        """Parse a batch entry's request.url into (path, query_params).

        Accepts both base-relative URLs (e.g. 'CodeSystem/$validate-code')
        and absolute URLs (e.g. 'https://host/fhir/CodeSystem/$validate-code').
        Strips the leading '/fhir' if present so the path matches the
        internal route shape.

        Returns (None, {}) on unparseable input.
        """
        from urllib.parse import parse_qsl, urlparse

        if not isinstance(url, str) or not url:
            return None, {}
        parsed = urlparse(url)
        path = parsed.path
        # If absolute, the path begins with /fhir/...; strip it.
        if path.startswith("/fhir/"):
            path = path[len("/fhir"):]
        elif not path.startswith("/"):
            # Base-relative URL like 'CodeSystem/$validate-code'.
            path = "/" + path
        # parse_qsl gives a list of (key, value); convert to dict (last-wins).
        query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        return path, query_params

    async def _dispatch_batch_operation(
        request: Request,
        method: str,
        path: str,
        params: dict[str, str],
        body_resource: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Dispatch a single batch entry to the appropriate operation handler.

        Returns a batch-response entry dict: {"response": {"status": ...},
        "resource": <FHIR resource>}.
        """
        engine = _engine(request)
        executor = _executor(request)
        pf_cache = request.app.state.patient_friendly_cache

        # Route based on path. Supported operations:
        #   POST /CodeSystem/$lookup
        #   POST /CodeSystem/$validate-code
        #   POST /ValueSet/$validate-code
        #   POST /ConceptMap/$translate
        #   GET  /CodeSystem/$validate-code?system=...&code=...
        #   GET  /ConceptMap/$translate?system=...&code=...&targetsystem=...
        #   GET  /CodeSystem/$lookup?system=...&code=...
        #   GET  /CodeSystem/$subsumes?system=...&codeA=...&codeB=...
        try:
            if path == "/CodeSystem/$validate-code":
                system, code, display, codeable_pairs = _extract_validate_params(method, params, body_resource)
                if system is None or code is None:
                    return _batch_error_entry(
                        400, "system and code are required for $validate-code."
                    )
                payload = await _run_db(
                    executor, _do_validate, engine, system, code,
                    display=display, codeable_concept_pairs=codeable_pairs,
                )
            elif path == "/ValueSet/$validate-code":
                system, code, display, url_param, codeable_pairs = _extract_vs_validate_params(
                    method, params, body_resource,
                )
                if codeable_pairs is None and (system is None or code is None):
                    return _batch_error_entry(
                        400, "system and code are required for $validate-code."
                    )
                payload = await _run_db(
                    executor, _do_vs_validate, engine,
                    url=url_param, code=code, system_uri=system, display=display,
                    codeable_concept_pairs=codeable_pairs,
                )
            elif path == "/ConceptMap/$translate":
                system, code, targetsystem = _extract_translate_params(
                    method, params, body_resource,
                )
                if system is None or code is None:
                    return _batch_error_entry(
                        400, "system and code are required for $translate."
                    )
                payload = await _run_db(
                    executor, _do_translate, engine, system, code, targetsystem,
                )
            elif path == "/CodeSystem/$lookup":
                system, code = _extract_lookup_params(method, params, body_resource)
                if system is None or code is None:
                    return _batch_error_entry(
                        400, "system and code are required for $lookup."
                    )
                payload = await _run_db(
                    executor, _do_lookup, engine, pf_cache, system, code,
                )
            elif path == "/CodeSystem/$subsumes":
                system, code_a, code_b = _extract_subsumes_params(
                    method, params, body_resource,
                )
                if system is None or code_a is None or code_b is None:
                    return _batch_error_entry(
                        400, "system, codeA, and codeB are required for $subsumes."
                    )
                payload = await _run_db(
                    executor, _do_subsumes, engine, system, code_a, code_b,
                )
            elif path == "/ValueSet/$expand":
                # Mandatory per §4.7.1.2 — found missing from batch dispatcher
                # by HISTORIAN TS-04 QA-039 (4-tuple coverage audit).
                url_param, filter_text, count, system_uri = _extract_expand_params(
                    method, params, body_resource,
                )
                payload = await _run_db(
                    executor, _do_expand, engine,
                    url=url_param, filter_text=filter_text, count=count,
                    system_uri=system_uri,
                )
            elif path == "/CodeSystem/$closure":
                # Mandatory per §4.7.1.2 — found missing from batch dispatcher
                # by HISTORIAN TS-04 QA-039.
                if body_resource is None:
                    return _batch_error_entry(
                        400,
                        "$closure requires a Parameters body (POST entries only).",
                    )
                name_val = (params.get("name") if params else None) or \
                    _parse_parameters(body_resource).get("name")
                if not name_val:
                    return _batch_error_entry(
                        400, "name parameter is required for $closure."
                    )
                payload = await _run_db(
                    executor, _do_closure, engine, body_resource, name_val,
                )
            else:
                return _batch_error_entry(
                    404,
                    f"Unknown operation or resource path in batch entry: {path!r}. "
                    "Supported: CodeSystem/$lookup, CodeSystem/$validate-code, "
                    "CodeSystem/$subsumes, CodeSystem/$closure, "
                    "ValueSet/$validate-code, ValueSet/$expand, "
                    "ConceptMap/$translate.",
                )
        except ValueError as exc:
            return _batch_error_entry(400, str(exc))

        # payload is either a dict (success — FHIR resource) or a Response
        # (error path produced by _fhir_error). Extract both shapes.
        if isinstance(payload, Response):
            # Error path: _fhir_error wrapped an OperationOutcome.
            try:
                body_dict = json.loads(payload.body)
            except (json.JSONDecodeError, TypeError):
                body_dict = build_operation_outcome(
                    "error", "exception", "Batch entry failed."
                )
            return {
                "response": {"status": str(payload.status_code)},
                "resource": body_dict,
            }
        # Success path: payload is a FHIR resource dict.
        return {
            "response": {"status": "200"},
            "resource": payload,
        }

    def _extract_validate_params(
        method: str,
        params: dict[str, str],
        body_resource: dict[str, Any] | None,
    ) -> tuple[str | None, str | None, str | None, list[tuple[str, str]] | None]:
        """Extract (system, code, display, codeable_concept_pairs) for $validate-code.

        Returns the codeableConcept pairs (CS-03 HISTORIAN QA-052) so the batch
        dispatcher can pass them through to ``_do_validate``. Without this,
        the batch path silently uses the single-pair helper
        (``_extract_codeable_concept_from_parameters``) which picks the FIRST
        coding — wrong semantic for CodeSystem/$validate-code per spec ("the
        server returns true if one of the coding values is in the code
        system"). The per-operation POST route already uses the all-pairs
        helper (CS-03 SKEPTIC QA-049); the batch path MUST honor the same
        semantic.
        """
        if method == "GET":
            return params.get("system"), params.get("code"), params.get("display"), None
        # POST: parse Parameters body.
        if body_resource is None:
            return None, None, None, None
        p = _parse_parameters(body_resource)
        system = p.get("system")
        code = p.get("code")
        codeable_pairs: list[tuple[str, str]] | None = None
        if not system or not code:
            coding_pair = _extract_coding_from_parameters(body_resource)
            if coding_pair is None:
                # CodeSystem/$validate-code codeableConcept semantics (CS-03
                # HISTORIAN QA-052): use the ALL-PAIRS helper so the batch
                # path matches the per-operation POST route's "any coding
                # matches → true" semantic. The single-pair helper is
                # insufficient — it silently picks the first coding.
                codeable_pairs = _extract_all_coding_pairs_from_codeable_concept(body_resource)
                if codeable_pairs:
                    system, code = codeable_pairs[0]
            elif coding_pair is not None:
                system, code = coding_pair
        return system, code, p.get("display"), codeable_pairs

    def _extract_vs_validate_params(
        method: str,
        params: dict[str, str],
        body_resource: dict[str, Any] | None,
    ) -> tuple[str | None, str | None, str | None, str | None, list[tuple[str, str]] | None]:
        """Extract (system, code, display, url, codeable_concept_pairs) for
        ValueSet/$validate-code.

        Returns the codeableConcept pairs (VS-05 SKEPTIC QA-069) so the batch
        dispatcher can pass them through to ``_do_vs_validate``. Without this,
        the batch path silently uses the single-pair helper
        (``_extract_codeable_concept_from_parameters``) which picks the FIRST
        coding — wrong semantic for ValueSet/$validate-code per spec ("the
        server returns true if one of the coding values is in the code
        system"). The per-operation POST route already uses the all-pairs
        helper; the batch path MUST honor the same semantic. Mirrors CS-03
        HISTORIAN QA-052 (same drift class on the sibling CodeSystem
        operation's batch dispatcher).
        """
        if method == "GET":
            return (
                params.get("system"), params.get("code"),
                params.get("display"), params.get("url"), None,
            )
        if body_resource is None:
            return None, None, None, None, None
        p = _parse_parameters(body_resource)
        system = p.get("system")
        code = p.get("code")
        codeable_pairs: list[tuple[str, str]] | None = None
        if not system or not code:
            coding_pair = _extract_coding_from_parameters(body_resource)
            if coding_pair is None:
                # ValueSet/$validate-code codeableConcept semantics (VS-05
                # SKEPTIC QA-069): use the ALL-PAIRS helper so the batch
                # path matches the per-operation POST route's "any coding
                # matches → true" semantic. The single-pair helper is
                # insufficient — it silently picks the first coding.
                codeable_pairs = _extract_all_coding_pairs_from_codeable_concept(body_resource)
                if codeable_pairs:
                    system, code = codeable_pairs[0]
            elif coding_pair is not None:
                system, code = coding_pair
        return system, code, p.get("display"), p.get("url"), codeable_pairs

    def _extract_translate_params(
        method: str,
        params: dict[str, str],
        body_resource: dict[str, Any] | None,
    ) -> tuple[str | None, str | None, str | None]:
        """Extract (system, code, targetsystem) for $translate."""
        if method == "GET":
            return (
                params.get("system"), params.get("code"),
                params.get("targetsystem"),
            )
        if body_resource is None:
            return None, None, None
        p = _parse_parameters(body_resource)
        return p.get("system"), p.get("code"), p.get("targetsystem")

    def _extract_lookup_params(
        method: str,
        params: dict[str, str],
        body_resource: dict[str, Any] | None,
    ) -> tuple[str | None, str | None]:
        """Extract (system, code) for $lookup."""
        if method == "GET":
            return params.get("system"), params.get("code")
        if body_resource is None:
            return None, None
        p = _parse_parameters(body_resource)
        system = p.get("system")
        code = p.get("code")
        if not system or not code:
            coding_pair = _extract_coding_from_parameters(body_resource)
            if coding_pair is not None:
                system, code = coding_pair
        return system, code

    def _extract_subsumes_params(
        method: str,
        params: dict[str, str],
        body_resource: dict[str, Any] | None,
    ) -> tuple[str | None, str | None, str | None]:
        """Extract (system, codeA, codeB) for $subsumes."""
        if method == "GET":
            return params.get("system"), params.get("codeA"), params.get("codeB")
        if body_resource is None:
            return None, None, None
        p = _parse_parameters(body_resource)
        return p.get("system"), p.get("codeA"), p.get("codeB")

    def _extract_expand_params(
        method: str,
        params: dict[str, str],
        body_resource: dict[str, Any] | None,
    ) -> tuple[str | None, str | None, int, str | None]:
        """Extract (url, filter, count, system) for $expand.

        Returns count with default=20 if absent; None otherwise for absent
        optional params. Used by the batch dispatcher (HISTORIAN TS-04
        QA-039 — closed the 4-tuple coverage gap for $expand).
        """
        if method == "GET":
            url_param = params.get("url")
            filter_text = params.get("filter")
            count_val = _parse_count_param(params.get("count"), default=20)
            system_uri = params.get("system")
            return url_param, filter_text, count_val or 20, system_uri
        # POST: parse Parameters body. $expand also accepts a ValueSet
        # resource body but that path is operation-specific; the batch
        # dispatcher always passes a Parameters body for entry.resource.
        if body_resource is None:
            return None, None, 20, None
        p = _parse_parameters(body_resource)
        count_val = _parse_count_param(p.get("count"), default=20)
        return p.get("url"), p.get("filter"), count_val or 20, p.get("system")

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
        version: str | None = Query(None, description="Code system version (passed through)"),
    ):
        payload = await _run_db(
            _executor(request), _do_lookup, _engine(request),
            request.app.state.patient_friendly_cache,
            system, code,
        )
        # Pass through Accept/_format negotiation. If _do_lookup returned a Response
        # (error path), return it as-is — already a FHIR-formatted Response.
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    @app.post("/fhir/CodeSystem/$lookup")
    async def lookup_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code = params.get("code")
        # FHIR R4 $lookup allows `coding` as a complete alternative to system+code.
        # If system/code are absent, try to derive them from a `coding` parameter.
        # Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html —
        # 'In addition, the 'coding' parameter allows a complete coding to be
        # supplied rather than the separate system and code parameters.'
        # (TS-02 HISTORIAN QA-022).
        if (not system or not code):
            coding_pair = _extract_coding_from_parameters(body)
            if coding_pair is not None:
                system, code = coding_pair
        if not system or not code:
            return _fhir_error_response(request, 400, "system and code are required.")
        payload = await _run_db(
            _executor(request), _do_lookup, _engine(request),
            request.app.state.patient_friendly_cache,
            system, code,
        )
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    def _do_lookup(
        engine: LocalDuckDBEngine,
        pf_cache: _PatientFriendlyCache,
        system_uri: str,
        code: str,
    ):
        """Resolve a code/system via the engine and build a FHIR R4 Parameters
        body for ``CodeSystem/$lookup`` (https://hl7.org/fhir/R4/codesystem-
        operation-lookup.html §4.8.21.1).

        Server-local custom properties (added under the Out ``property`` group
        per §4.8.21.1 + §4.8.11 — CodeSystem.property.type 'code'/'string'
        allows code-system-defined custom properties):

          * ``patient-friendly`` (string) — prepared layperson-friendly
            display name from the patient-friendly JSONs.
          * ``match-type`` (code) — SERVER-LOCAL engine pipeline vocabulary
            describing HOW the patient-friendly name was derived (which
            fallback branch produced it). Values are documented in
            ``tests/fhir_conformance/test_cs01_terminologist.py`` as
            ``SERVER_LOCAL_MATCH_TYPE_VOCABULARY`` (``exact``, ``original``,
            ``broader``, ``group``, ``ingredient``, ``same_cui``,
            ``cvx_group``, ``broader_group``, ``broader_ingredient``,
            ``first_axis``, ``snomed_fallback``, ``snomed_to_target_*``).
            IMPORTANT: these are NOT FHIR R4 ConceptMapEquivalence enum
            values (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html).
            ``match-type`` describes the engine derivation path, not a
            concept-to-concept semantic equivalence — e.g. ``original`` =
            "no PF data found, returned the code's canonical preferred
            term", which has no equivalence analog. Forcing them into the
            FHIR enum would be clinically misleading (CF-SKEPTIC-CS01-02
            decision (b): document, do not translate). If a future engine
            vocabulary value grows a clean clinical equivalence mapping,
            re-evaluate decision (b) vs (a) for that value alone.
          * ``canonical-code`` (code) — code in the canonical-system that
            the patient-friendly crosswalk resolved to. MAY be a chapter
            RANGE (e.g. ICD-10-CM ``E08-E13`` for SNOMED 73211009) per
            CF-EXPLORER-CS01-01; clients must validate before treating as
            a single billable code.
          * ``canonical-system`` (uri) — FHIR R4 canonical system URI for
            canonical-code, translated from the raw UMLS SAB label stored
            in the patient-friendly JSON via ``sab_label_to_fhir_uri``
            (CS-01 SKEPTIC QA-043; HISTORIAN QA-044 added the WARNING log
            on translation failure).
          * ``tty`` (code) — raw UMLS term-type vocabulary (e.g. ``PT``,
            ``SCD``, ``LN``) of the resolved atom.
        """
        source = fhir_uri_to_system(system_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
        # Per CS-02 HISTORIAN QA-047: re-resolve to the canonical FHIR URI
        # before passing to the response builder. Without this, the Out
        # `system` parameter echoes the client input verbatim — including
        # aliases (urn:oid:...) and trailing-slash variants. This is the
        # TS-02 TERMINOLOGIST QA-029 "client-input-as-canonical" drift
        # pattern (recurring count). The SYSTEM_TO_FHIR_URI registry is the
        # canonical source of truth per GLOBAL_RULES.md. Fall back to the
        # client input only if the source is somehow absent from the map
        # (defensive — should not happen for sources resolved above).
        # Structural fix (milestone-2 review): delegate to the shared
        # ``canonical_system_uri`` helper in ``engines/fhir/__init__.py``
        # rather than inlining ``system_to_fhir_uri(source) or system_uri``
        # at every call site (CR-011/012/013 pattern recurrence).
        canonical_uri = canonical_system_uri(system_uri, source=source)
        results = get_code_infos([CodeRef(source, code)], engine=engine)
        code_info = results[0] if results else None

        # Enrich with patient_friendly custom properties.
        # Per CS-02 HISTORIAN QA-046: defensively guard against malformed
        # pf_cache entries (per-code value that is not a dict). The TS-04
        # HISTORIAN QA-038 "silent-wrong-answer at error-isolation boundary"
        # class applies — without this guard, ``pf.get('name')`` raises
        # ``AttributeError`` (e.g. when ``pf`` is a list), which propagates
        # past the route handler (which only checks ``isinstance(payload,
        # Response)``) to FastAPI's default 500 with text/plain body. FHIR R4
        # §3.1.0.1.5 + §3.1.0.1.9 require an OperationOutcome body with
        # ``application/fhir+json`` Content-Type on every error response.
        # Skip silently when the entry is malformed (consistent with "no PF
        # data found" — the custom properties are optional Out parameters
        # whose absence is spec-conformant per §4.8.21.1 0..* cardinality).
        custom_props: dict[str, Any] = {}
        pf = pf_cache.get(source, code)
        if isinstance(pf, dict):
            custom_props["patient-friendly"] = pf.get("name")
            custom_props["match-type"] = pf.get("match_type")
            if pf.get("canonical_code"):
                custom_props["canonical-code"] = pf.get("canonical_code")
                # Translate the raw UMLS SAB label (e.g. "icd10") stored in the
                # patient-friendly JSON to the FHIR R4 canonical system URI
                # (e.g. "http://hl7.org/fhir/sid/icd-10-cm"). Per CS-01 SKEPTIC
                # QA-043: echoing the raw SAB label verbatim produces a value
                # that is not a FHIR URI — clients parsing it as Coding.system
                # get a string that doesn't resolve. Fall back to the raw
                # value only if translation fails (unrecognized SAB), and log
                # at WARNING per GLOBAL_RULES.md "Silent Fallbacks" — silent
                # raw-label emission is silent-wrong-answer if a future source
                # addition outpaces the _SAB_LABEL_TO_SOURCE map. CS-01
                # HISTORIAN QA-044.
                raw_sab = pf.get("canonical_system")
                fhir_uri = sab_label_to_fhir_uri(raw_sab) if raw_sab else None
                if fhir_uri:
                    custom_props["canonical-system"] = fhir_uri
                elif raw_sab:
                    logger.warning(
                        "sab_label_to_fhir_uri(%r) returned None — emitting raw "
                        "SAB label as canonical-system. Add %r to "
                        "engines.fhir._SAB_LABEL_TO_SOURCE to translate.",
                        raw_sab, raw_sab.lower(),
                    )
                    custom_props["canonical-system"] = raw_sab
            if pf.get("tty"):
                custom_props["tty"] = pf.get("tty")
        elif pf is not None:
            # Malformed entry — log at WARNING (operator signal) and skip
            # custom-property enrichment. The Out `property` group is 0..*
            # per FHIR R4 §4.8.21.1, so absence is spec-conformant; the
            # lookup still succeeds with the engine's canonical data.
            logger.warning(
                "Malformed patient-friendly cache entry for source=%r "
                "code=%r (expected dict, got %s) — skipping custom-property "
                "enrichment. Re-generate the patient_friendly_%s.json "
                "artifact to silence this warning.",
                source, code, type(pf).__name__, source.lower(),
            )

        return build_parameters_lookup(
            code_info,
            system_uri=canonical_uri,
            custom_properties=custom_props,
        )

    # -- CodeSystem $validate-code --
    @app.get("/fhir/CodeSystem/$validate-code")
    async def validate_get(
        request: Request,
        system: str = Query(...),
        code: str = Query(...),
        version: str | None = Query(None, description="Code system version (passed through)"),
        display: str | None = Query(
            None,
            description="Display string to verify against the code (per FHIR R4 $validate-code).",
        ),
    ):
        payload = await _run_db(
            _executor(request), _do_validate, _engine(request),
            system, code, display=display, codeable_concept_pairs=None,
        )
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    @app.post("/fhir/CodeSystem/$validate-code")
    async def validate_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code = params.get("code")
        display = params.get("display")
        # FHIR R4 $validate-code: 'a client SHALL provide one (and only one) of
        # the parameters (code+system, coding, or codeableConcept).' Allow
        # `coding` and `codeableConcept` as complete alternatives to system+code
        # (TS-02 HISTORIAN QA-023 wired `coding`; EXPLORER QA-026 wires
        # `codeableConcept` — same shape, same root cause).
        codeable_pairs: list[tuple[str, str]] | None = None
        if (not system or not code):
            coding_pair = _extract_coding_from_parameters(body)
            if coding_pair is None:
                # CodeSystem/$validate-code codeableConcept semantics (CS-03
                # SKEPTIC QA-049): "The server returns true if one of the
                # coding values is in the code system". The full list of
                # codings MUST be examined — picking just the first coding
                # silently wrong-answers when the first coding is invalid
                # but a later coding is valid.
                codeable_pairs = _extract_all_coding_pairs_from_codeable_concept(body)
                if codeable_pairs:
                    system, code = codeable_pairs[0]
            elif coding_pair is not None:
                system, code = coding_pair
        if not system or not code:
            return _fhir_error_response(request, 400, "system and code are required.")
        payload = await _run_db(
            _executor(request), _do_validate, _engine(request),
            system, code, display=display, codeable_concept_pairs=codeable_pairs,
        )
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    def _do_validate(
        engine: LocalDuckDBEngine,
        system_uri: str,
        code: str,
        *,
        display: str | None = None,
        codeable_concept_pairs: list[tuple[str, str]] | None = None,
    ):
        # CodeSystem/$validate-code (CS-03 SKEPTIC QA-049): when a
        # codeableConcept is supplied, the spec mandates "The server returns
        # true if one of the coding values is in the code system". The full
        # list MUST be examined — picking only the first coding is silent-
        # wrong-answer.
        if codeable_concept_pairs:
            matched_info: CodeInfo | None = None
            matched_uri: str | None = None
            matched_code: str | None = None
            for cc_uri, cc_code in codeable_concept_pairs:
                cc_source = fhir_uri_to_system(cc_uri)
                if cc_source is None:
                    continue
                cc_results = get_code_infos([CodeRef(cc_source, cc_code)], engine=engine)
                if cc_results and cc_results[0] is not None:
                    matched_info = cc_results[0]
                    matched_uri = cc_uri
                    matched_code = cc_code
                    break
            if matched_info is not None:
                # At least one coding matched → result=true. Display mismatch
                # semantics only apply to the system-supplied `display` against
                # the matched code; for codeableConcept, the spec does not
                # mandate display enforcement.
                #
                # CR-025 (milestone-3 review): wrap ``matched_uri`` through
                # ``canonical_system_uri`` so the Out ``system`` parameter
                # is the canonical FHIR URI (not the client-supplied alias).
                # The sibling scalar-system path (line 1715) was fixed in
                # milestone-2 (CR-007); this codeableConcept branch was
                # missed. Same client-input-as-canonical drift pattern
                # (count=8 cumulative). Spec: FHIR R4 §4.8.21.1 Out
                # ``system``.
                canonical_matched_uri = (
                    canonical_system_uri(matched_uri) if matched_uri else system_uri
                )
                return build_parameters_validate(
                    True,
                    system_uri=canonical_matched_uri,
                    code=matched_code or code,
                    display=display,
                    code_info=matched_info,
                )
            # No coding matched → result=false. Use the first pair for the
            # response shape (system/code echoed for client context).
            first_uri, first_code = codeable_concept_pairs[0]
            return build_parameters_validate(
                False,
                system_uri=first_uri,
                code=first_code,
                display=display,
                code_info=None,
                message="None of the codings in the codeableConcept are in the code system.",
            )
        source = fhir_uri_to_system(system_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
        # CS-03 HISTORIAN QA-051: re-resolve to the canonical FHIR URI before
        # passing to the response builder. Without this, the Out `system`
        # parameter echoes the client input verbatim — including aliases
        # (urn:oid:...) and trailing-slash variants. This is the same drift
        # pattern as CS-02 HISTORIAN QA-047 in `_do_lookup` (count=5 of the
        # client-input-as-canonical drift pattern). SKEPTIC's QA-048 display
        # mismatch fix did NOT include this re-resolution; HISTORIAN caught
        # the regression. The SYSTEM_TO_FHIR_URI registry is the canonical
        # source of truth per GLOBAL_RULES.md.
        # Structural fix (milestone-2 review): delegate to the shared
        # ``canonical_system_uri`` helper (CR-011/012/013 pattern).
        canonical_uri = canonical_system_uri(system_uri, source=source)
        results = get_code_infos([CodeRef(source, code)], engine=engine)
        code_info = results[0] if results else None
        # CS-03 SKEPTIC QA-048: enforce display mismatch per spec example
        # response. When the client supplies a `display` AND the engine has a
        # canonical display AND they don't match → result=false + message +
        # canonical display. Spec: https://hl7.org/fhir/R4/codesystem-operation-
        # validate-code.html "Response: When the request can be processed ok".
        canonical_display = code_info.name if code_info and code_info.name else None
        if (
            code_info is not None
            and display is not None
            and canonical_display is not None
            and display != canonical_display
        ):
            return build_parameters_validate(
                False,
                system_uri=canonical_uri,
                code=code,
                display=display,
                code_info=code_info,
                message=f'The display "{display}" is incorrect',
            )
        # Code unknown → result=false with optional message. Spec Out message:
        # "Error details, if result = false". A bare result=false is also
        # conformant (cardinality 0..1) but a message improves clinical UX.
        if code_info is None:
            return build_parameters_validate(
                False,
                system_uri=canonical_uri,
                code=code,
                display=display,
                code_info=None,
                message=f"Code {code} is not valid in code system {canonical_uri}.",
            )
        return build_parameters_validate(
            True,
            system_uri=canonical_uri,
            code=code,
            display=display,
            code_info=code_info,
        )

    # -- ValueSet $validate-code --
    # FHIR R4 §4.7.1.2: servers exposing $validate-code on CodeSystem SHALL also
    # expose it on ValueSet. Found missing by SKEPTIC iteration TS-02 (QA-013).
    # The semantic is: does the supplied (system, code) belong to the ValueSet's
    # expansion? For now, without persisted ValueSets, we resolve code presence in
    # the underlying code system and report result accordingly, with a `message`
    # noting the ValueSet scope is approximate. Full membership evaluation against
    # a ValueSet's compose rules is a future enhancement.
    @app.get("/fhir/ValueSet/$validate-code")
    async def vs_validate_get(
        request: Request,
        url: str | None = Query(None, description="ValueSet canonical URL"),
        code: str | None = Query(None, description="The code to validate"),
        system: str | None = Query(None, description="The code system URI for the code"),
        codeableConcept: str | None = Query(None),
        display: str | None = Query(None, description="Display string to verify"),
    ):
        # VS-05 SKEPTIC QA-069: pass codeableConcept pairs through to the
        # handler so the multi-coding "any match → true" semantic is honored
        # (mirrors CS-03 SKEPTIC QA-049 on the sibling CodeSystem handler).
        # On GET, codeableConcept is a query param (string) — not usable for
        # multi-coding; the POST path carries the structured body. None here.
        payload = await _run_db(
            _executor(request), _do_vs_validate, _engine(request),
            url=url, code=code, system_uri=system, display=display,
            codeable_concept_pairs=None,
        )
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    @app.post("/fhir/ValueSet/$validate-code")
    async def vs_validate_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        url = params.get("url")
        code = params.get("code")
        system_uri = params.get("system")
        display = params.get("display")
        # FHIR R4 ValueSet/$validate-code: same alternative-encoding rule as
        # CodeSystem/$validate-code. Spec In Parameters lists `coding` and
        # `codeableConcept` as alternatives to system+code. Without these
        # branches, a spec-compliant POST is silently rejected with 400
        # 'code and system are required for $validate-code.' (TS-02 EXPLORER
        # QA-028 — same root cause as HISTORIAN QA-022/QA-023; the
        # `_extract_coding_from_parameters` helper existed but was never
        # wired into this handler.)
        #
        # VS-05 SKEPTIC QA-069: codeableConcept multi-coding semantics
        # (mirrors CS-03 SKEPTIC QA-049 on the sibling CodeSystem handler).
        # The spec mandates "The server returns true if one of the coding
        # values is in the code system". The prior implementation used the
        # SINGLE-PAIR helper (`_extract_codeable_concept_from_parameters`)
        # which silently picks the FIRST coding — wrong-answer when the
        # first coding is invalid but a later coding is valid. The fix wires
        # the ALL-PAIRS helper and passes the list through to
        # ``_do_vs_validate`` for iteration.
        codeable_pairs: list[tuple[str, str]] | None = None
        if (not code or not system_uri):
            coding_pair = _extract_coding_from_parameters(body)
            if coding_pair is None:
                codeable_pairs = _extract_all_coding_pairs_from_codeable_concept(body)
                if codeable_pairs:
                    system_uri, code = codeable_pairs[0]
            elif coding_pair is not None:
                system_uri, code = coding_pair
        if not codeable_pairs and (not code or not system_uri):
            return _fhir_error_response(request, 400, "code and system are required for $validate-code.")
        payload = await _run_db(
            _executor(request), _do_vs_validate, _engine(request),
            url=url, code=code, system_uri=system_uri, display=display,
            codeable_concept_pairs=codeable_pairs,
        )
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    def _do_vs_validate(
        engine: LocalDuckDBEngine,
        *,
        url: str | None,
        code: str | None,
        system_uri: str | None,
        display: str | None,
        codeable_concept_pairs: list[tuple[str, str]] | None = None,
    ):
        # Without persisted ValueSets, membership evaluation reduces to "is the code
        # present in the underlying code system". The url param is accepted for
        # spec-compatibility but not used to restrict the membership check today.
        # CodeableConcept multi-coding semantics (VS-05 SKEPTIC QA-069, mirroring
        # CS-03 SKEPTIC QA-049 on the sibling CodeSystem handler): when a
        # codeableConcept is supplied, the spec mandates "The server returns true
        # if one of the coding values is in the code system". The full list MUST
        # be examined — picking only the first coding silently wrong-answers.
        if codeable_concept_pairs:
            matched_info: CodeInfo | None = None
            matched_uri: str | None = None
            matched_code: str | None = None
            for cc_uri, cc_code in codeable_concept_pairs:
                cc_source = fhir_uri_to_system(cc_uri)
                if cc_source is None:
                    continue
                cc_results = get_code_infos([CodeRef(cc_source, cc_code)], engine=engine)
                if cc_results and cc_results[0] is not None:
                    matched_info = cc_results[0]
                    matched_uri = cc_uri
                    matched_code = cc_code
                    break
            if matched_info is not None:
                # At least one coding matched → result=true. Display mismatch
                # semantics only apply to the system-supplied `display` against
                # the matched code; for codeableConcept, the spec does not
                # mandate display enforcement (CS-03 SKEPTIC AUDIT-002).
                #
                # CR-025 (milestone-3 review): wrap ``matched_uri`` through
                # ``canonical_system_uri`` so the Out ``system`` parameter
                # is the canonical FHIR URI (not the client-supplied alias).
                # The sibling scalar-system path (line 1898) was fixed in
                # milestone-2 (CR-011); this codeableConcept branch was
                # missed. Same client-input-as-canonical drift pattern
                # (count=8 cumulative). Spec: FHIR R4 §4.8.21.1 Out
                # ``system``.
                canonical_matched_uri = (
                    canonical_system_uri(matched_uri) if matched_uri else (system_uri or "")
                )
                return build_parameters_validate(
                    True,
                    system_uri=canonical_matched_uri,
                    code=matched_code or code or "",
                    display=display,
                    code_info=matched_info,
                )
            first_uri = codeable_concept_pairs[0][0] if codeable_concept_pairs else (system_uri or "")
            first_code = codeable_concept_pairs[0][1] if codeable_concept_pairs else (code or "")
            return build_parameters_validate(
                False,
                system_uri=first_uri,
                code=first_code,
                display=display,
                code_info=None,
                message="None of the codings in the codeableConcept are in the code system.",
            )
        if not system_uri:
            return _fhir_error(400, "system is required for $validate-code.")
        if not code:
            return _fhir_error(400, "code is required for $validate-code.")
        source = fhir_uri_to_system(system_uri)
        if source is None:
            return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
        # CR-011 (milestone-2 review): re-resolve to the canonical FHIR URI
        # before passing to the response builder. Without this, the Out
        # `system` parameter echoes the client input verbatim — including
        # aliases (urn:oid:...) and trailing-slash variants. Same client-
        # input-as-canonical drift pattern as CS-02 HISTORIAN QA-047
        # (_do_lookup) and CS-03 HISTORIAN QA-051 (_do_validate); this
        # ValueSet/$validate-code handler was missed. Spec: FHIR R4 §4.8.21.1
        # Out `system`. Structural fix: shared ``canonical_system_uri``.
        canonical_uri = canonical_system_uri(system_uri, source=source)
        results = get_code_infos([CodeRef(source, code)], engine=engine)
        code_info = results[0] if results else None
        # CF-SKEPTIC-CS03-01 (MEDIUM, RESOLVED in VS-05 SKEPTIC): enforce
        # display mismatch per spec example response, mirroring CS-03 SKEPTIC
        # QA-048 on the sibling CodeSystem/$validate-code handler. The carry-
        # forward was opened in CS-03 SKEPTIC and pinned by CS-03 TERMINOLOGIST
        # test_t60 (asserts the CURRENT-buggy behavior; the probe was updated
        # in the same PR as this fix). Spec: FHIR R4 In Parameters ``display``:
        # "A display to verify" + Out Parameters example showing result=false +
        # message + canonical display. The same structural shape applies to
        # ValueSet/$validate-code per FHIR R4 §4.9.3 cross-reference to the
        # CodeSystem operation.
        canonical_display = code_info.name if code_info and code_info.name else None
        if (
            code_info is not None
            and display is not None
            and canonical_display is not None
            and display != canonical_display
        ):
            return build_parameters_validate(
                False,
                system_uri=canonical_uri,
                code=code,
                display=display,
                code_info=code_info,
                message=f'The display "{display}" is incorrect',
            )
        if code_info is None:
            return build_parameters_validate(
                False,
                system_uri=canonical_uri,
                code=code,
                display=display,
                code_info=None,
                message=f"Code {code} is not valid in code system {canonical_uri}.",
            )
        return build_parameters_validate(
            code_info is not None,
            system_uri=canonical_uri,
            code=code,
            display=display,
            code_info=code_info,
        )

    # -- ConceptMap $translate --
    @app.get("/fhir/ConceptMap/$translate")
    async def translate_get(
        request: Request,
        system: str = Query(..., description="Source system URI"),
        code: str = Query(..., description="Source code"),
        targetsystem: str | None = Query(None, description="Target system URI"),
        source: str | None = Query(
            None,
            description="Canonical ConceptMap URL to use (per FHIR R4 $translate). Passed through; not yet used to select a named ConceptMap.",
        ),
        targetCode: str | None = Query(
            None,
            description="Target code — used with reverse=true to find source codes mapping to this target.",
        ),
    ):
        payload = await _run_db(_executor(request), _do_translate, _engine(request), system, code, targetsystem)
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    @app.post("/fhir/ConceptMap/$translate")
    async def translate_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code = params.get("code")
        targetsystem = params.get("targetsystem")
        if not system or not code:
            return _fhir_error_response(request, 400, "system and code are required.")
        payload = await _run_db(_executor(request), _do_translate, _engine(request), system, code, targetsystem)
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

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
        # CR-012 (milestone-2 review): re-resolve the source system URI to
        # canonical before passing to the response builder. Without this,
        # the Out ``match[].source.system`` field echoes the client-supplied
        # ``source_uri`` verbatim — including aliases (urn:oid:...) and
        # trailing-slash variants. The ``target.system`` field is already
        # canonical (resolved via ``system_to_fhir_uri(m.target.source)``
        # at responses.py:172); this fixes the source side to match. Same
        # client-input-as-canonical drift pattern as CR-011 / CR-013.
        # Spec: FHIR R4 §4.8.21.1 Out Coding.system. Structural fix:
        # shared ``canonical_system_uri``.
        canonical_source_uri = canonical_system_uri(source_uri, source=source)
        return build_parameters_translate(
            mappings,
            source_system_uri=canonical_source_uri,
            source_code=code,
        )

    # -- CodeSystem $subsumes --
    @app.get("/fhir/CodeSystem/$subsumes")
    async def subsumes_get(
        request: Request,
        system: str = Query(...),
        codeA: str = Query(...),
        codeB: str = Query(...),
        version: str | None = Query(None, description="Code system version (passed through)"),
    ):
        payload = await _run_db(_executor(request), _do_subsumes, _engine(request), system, codeA, codeB)
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    @app.post("/fhir/CodeSystem/$subsumes")
    async def subsumes_post(request: Request, body: dict[str, Any]):
        params = _parse_parameters(body)
        system = params.get("system")
        code_a = params.get("codeA")
        code_b = params.get("codeB")
        # Per FHIR R4 §4.8.21.3 In Parameters: codingA/codingB (valueCoding) are
        # spec-listed alternatives to codeA/codeB (valueCode). The scalar-only
        # _parse_parameters drops valueCoding silently (TS-02 HISTORIAN QA-022
        # pattern class); consult the codingA/codingB extractors when the
        # scalar codeA/codeB are absent. Source:
        # https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
        coding_a_pair = _extract_named_coding_from_parameters(body, "codingA")
        coding_b_pair = _extract_named_coding_from_parameters(body, "codingB")
        if not code_a and coding_a_pair is not None:
            code_a = coding_a_pair[1]
        if not code_b and coding_b_pair is not None:
            code_b = coding_b_pair[1]
        if not system or not code_a or not code_b:
            return _fhir_error_response(request, 400, "system, codeA, and codeB are required.")
        # Mixed-system check (spec In `codingA`: "the relationships between the
        # code systems must be well established"). medterm4ds has no
        # cross-system relationship map today; when either coding references a
        # different system than `system`, the server SHALL error.
        if coding_a_pair is not None and coding_a_pair[0] != system:
            return _fhir_error_response(
                request,
                400,
                f"codingA system {coding_a_pair[0]!r} differs from subsumption "
                f"system {system!r}; cross-system relationships are not defined.",
            )
        if coding_b_pair is not None and coding_b_pair[0] != system:
            return _fhir_error_response(
                request,
                400,
                f"codingB system {coding_b_pair[0]!r} differs from subsumption "
                f"system {system!r}; cross-system relationships are not defined.",
            )
        payload = await _run_db(_executor(request), _do_subsumes, _engine(request), system, code_a, code_b)
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

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
            return _fhir_error_response(request, 400, "name parameter is required for $closure.")
        payload = await _run_db(_executor(request), _do_closure, _engine(request), body, name)
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    def _do_closure(engine: LocalDuckDBEngine, body: dict[str, Any], name: str):
        from medterm4ds.engines.fhir.closure import (
            build_closure_response,
            get_closure_manager,
        )

        manager = get_closure_manager()

        # Extract concept list from the Parameters body.
        #
        # Per FHIR R4 $closure In Parameters: ``concept`` is 0..* Coding.
        # The ``isinstance(coding, dict)`` guard is load-bearing — without
        # it, a client supplying ``valueCoding`` as a non-dict (string,
        # list, null) triggers AttributeError that propagates as a 500-
        # with-traceback response. Pattern-match to CS-04 SKEPTIC QA-053
        # (``_extract_named_coding_from_parameters`` has the same guard
        # for $subsumes codingA/codingB). Found by CM-03 HISTORIAN
        # (CF-HISTORIAN-CM03-01). The systemic duckdb.Error handler does
        # NOT catch AttributeError (per GLOBAL_RULES.md "Silent Fallbacks"
        # — programming bugs MUST propagate); the guard at the data-
        # access boundary is the conformant fix.
        concepts: list[tuple[str, str, str]] = []  # (code, system, display)
        for param in body.get("parameter", []):
            if param.get("name") != "concept":
                continue
            coding = param.get("valueCoding")
            if not isinstance(coding, dict):
                # Malformed valueCoding (non-dict) — silently drop this
                # concept entry. Mirrors the silent-drop semantic for
                # missing-code / missing-system cases below.
                continue
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
        offset: int = Query(0, ge=0, description="Paging offset (per FHIR R4 $expand). Passed through; not yet used to slice results."),
        system: str | None = Query(None, description="System URI for filter expansion"),
    ):
        payload = await _run_db(
            _executor(request), _do_expand, _engine(request),
            url=url, filter_text=filter, count=count, system_uri=system,
        )
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    @app.post("/fhir/ValueSet/$expand")
    async def expand_post(
        request: Request,
        body: dict[str, Any],
        count: int = Query(20, ge=1, le=1000),
    ):
        """Expand a ValueSet. Accepts either a ValueSet resource (intensional)
        or a Parameters resource (filter mode).

        The ``count`` query parameter is honored for both shapes per FHIR R4
        §4.7.5 (https://hl7.org/fhir/R4/valueset-operation-expand.html In
        ``count``). Found by TERMINOLOGIST iteration VS-01 (QA-055) — the prior
        implementation hardcoded ``count=1000`` for the ValueSet-body branch,
        silently ignoring the client's request and never surfacing the
        ``valueset-toocostly`` truncation extension (clinical-safety signal).
        """
        resource_type = body.get("resourceType", "")
        if resource_type == "ValueSet":
            payload = await _run_db(_executor(request), _do_expand, _engine(request), value_set=body, count=count)
            return payload if isinstance(payload, Response) else _fhir_response(request, payload)
        # Per FHIR R4 §4.7.5 In Parameters ``valueSet`` (0..1 ValueSet):
        # "The value set is provided directly as part of the request." The
        # body shape is a Parameters resource with a parameter carrying a
        # nested ValueSet via the ``resource`` property (per
        # https://hl7.org/fhir/R4/parameters.html — "A parameter can have a
        # resource as a value"). Found by SKEPTIC iteration VS-03 (QA-059)
        # — the prior implementation only honored the bare-ValueSet body
        # shape and silently dropped the Parameters-with-valueSet form,
        # falling through to the no-url/no-filter 400 path. Same shape as
        # the helper-exists-but-not-wired pattern (TS-02 HISTORIAN QA-022/
        # QA-023, TS-02 EXPLORER QA-026/QA-028): the sibling helper is the
        # 4th instance of the pattern.
        #
        # When the body is Parameters-with-valueSet, the spec-listed In
        # parameters (count, offset, etc.) MAY be co-located in the same
        # Parameters body. The query-param ``count`` is the GET default
        # (20); when the body carries an explicit count, it takes
        # precedence per FHIR R4 §4.7.5 (Parameters-body parameters
        # override defaults, same as the bare-ValueSet branch where the
        # query-param count still applies).
        inline_vs = _extract_valueset_from_parameters(body)
        if inline_vs is not None:
            # Extract scalar In params from the same Parameters body so
            # clients that pass ``count`` etc. alongside the inline
            # valueSet get them honored (mirror _parse_parameters on the
            # same body).
            inline_params = _parse_parameters(body)
            inline_count = _parse_count_param(
                inline_params.get("count"), default=count
            )
            if inline_count is None:
                return _fhir_error_response(
                    request, 400,
                    f"count must be an integer in [1, 1000] (got {inline_params.get('count')!r})."
                )
            payload = await _run_db(_executor(request), _do_expand, _engine(request), value_set=inline_vs, count=inline_count)
            return payload if isinstance(payload, Response) else _fhir_response(request, payload)
        # Parameters-style: extract url, filter, count
        params = _parse_parameters(body)
        count = _parse_count_param(params.get("count"), default=20)
        if count is None:
            return _fhir_error_response(request, 400, f"count must be an integer in [1, 1000] (got {params.get('count')!r}).")
        payload = await _run_db(
            _executor(request), _do_expand, _engine(request),
            url=params.get("url"),
            filter_text=params.get("filter"),
            count=count,
            system_uri=params.get("system"),
        )
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

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

        # Mode 2: Implicit value set URL — FHIR R4 §4.7.3.1 convention-based
        # value set URLs derived from code system URIs. Two forms:
        #   (a) `<system-uri>/vs` — e.g. http://loinc.org/vs (all of LOINC)
        #   (b) `http://snomed.info/sct?fhir_vs` (no =isa, no code in path) —
        #       all of SNOMED CT.
        # The server SHOULD expand these even though no explicit ValueSet
        # resource exists. Found by SKEPTIC iteration TS-03 (QA-032).
        if url and _is_implicit_value_set_url(url):
            return _expand_implicit_value_set(engine, url, count)

        # Mode 3: URL with fhir_vs pattern (SNOMED intensional shorthand with
        # a code in the path: http://snomed.info/sct/<code>?fhir_vs=isa)
        if url and "fhir_vs" in url:
            return _expand_url_pattern(engine, url, count)

        # Mode 4: Text filter (existing EHR autocomplete)
        if filter_text:
            sources = _resolve_sources(system_uri)
            if sources is None:
                return _fhir_error(400, f"Unrecognized system URI: {system_uri}")
            # search_names enforces an input-length cap (>256 chars) and a
            # non-empty query — both raise ValueError. Without this catch the
            # ValueError propagates as an uncaught 500 with a non-FHIR body
            # (CPU-waste / DoS surface). Found by TS-02 EXPLORER QA-027.
            try:
                results = search_names(filter_text, engine=engine, sources=sources, limit=count)
            except ValueError as exc:
                return _fhir_error(400, f"Invalid filter: {exc}")
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
        max_depth = _resolve_max_depth()
        contains: list[dict[str, Any]] = []
        depth_cap_hit = False

        for include in compose.get("include", []):
            inc_system = include.get("system", "")
            source = fhir_uri_to_system(inc_system) or inc_system
            # CR-013 (milestone-2 review): re-resolve the include[].system
            # to its canonical FHIR URI once per include block. Without
            # this, every ``contains[].system`` echoes the client-supplied
            # ``inc_system`` verbatim — including aliases (urn:oid:...) and
            # trailing-slash variants. A client parsing the response's
            # ``contains[]`` then has a Coding whose ``system`` is the
            # alias, not the canonical URI — fails strict validation. The
            # same drift applies to the is-a filter path AND the
            # descendants loop. Spec: FHIR R4 §4.7.5 Out
            # ``contains[].system`` ("An absolute URI which is the code
            # system URI of the code system from which the code in the
            # expansion was defined" — implies canonical, not alias). Same
            # client-input-as-canonical drift pattern as CR-011 / CR-012.
            # Structural fix: shared ``canonical_system_uri``.
            canonical_inc = canonical_system_uri(inc_system, source=source if source else None)

            # Explicit concept list
            if "concept" in include:
                for concept in include["concept"]:
                    code_str = str(concept.get("code", ""))
                    # Per FHIR R4 ValueSet.expansion.contains.display
                    # (https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display):
                    # "The recommended display for this item in the expansion."
                    # When the client OMITS the display, resolve the engine's
                    # canonical preferred term via get_code_infos (mirror the
                    # is-a root display resolution below). Found by TERMINOLOGIST
                    # iteration VS-01 (QA-056) — the prior implementation echoed
                    # empty string for omitted display, producing clinically
                    # useless expansions. NOTE: when the client SUPPLIES a
                    # display, the implementation still echoes it verbatim —
                    # see CF-TERMINOLOGIST-VS01-01 for the deferred canonical-
                    # wins decision on the supplied-display case.
                    display = concept.get("display") or ""
                    if not display and code_str:
                        concept_infos = get_code_infos(
                            [CodeRef(source, code_str)], engine=engine
                        )
                        if concept_infos and concept_infos[0]:
                            display = concept_infos[0].name or code_str
                    contains.append({
                        "system": canonical_inc,
                        "code": code_str,
                        "display": display,
                    })

            # Intensional filter (is-a, descendant-of)
            for filt in include.get("filter", []):
                prop = filt.get("property", "")
                op = filt.get("op", "")
                val = filt.get("value", "")

                # FHIR R4 §4.9.5 / https://hl7.org/fhir/R4/valueset.html#filter:
                # op is bound to Filter Operator (Required) — 9-value enum:
                #   = | is-a | descendent-of | is-not-a | regex | in | not-in
                #   | generalizes | exists
                # NOTE: the spec spelling is "descendent-of" (Latin-derived),
                # NOT "descendant-of" (common English). Found by SKEPTIC
                # iteration VS-01 (QA-054) — the prior "descendant-of" form
                # silently honored an off-spec value while the spec-correct
                # "descendent-of" was silently dropped.
                if prop == "concept" and op in ("is-a", "descendent-of"):
                    root_code = str(val)
                    include_root = (op == "is-a")

                    if include_root:
                        root_infos = get_code_infos(
                            [CodeRef(source, root_code)], engine=engine
                        )
                        if root_infos and root_infos[0]:
                            contains.append({
                                "system": canonical_inc,
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
                            "system": canonical_inc,
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
        # VS-02 SKEPTIC QA-057: pass UN-truncated ``total`` so the response
        # reflects the full expansion size per FHIR R4 §4.9.2: "The total
        # number of concepts in the expansion." The toocostly extension
        # carries the truncation signal; total carries the count of unique
        # concepts that would have been returned without truncation.
        return build_valueset_expand(
            deduped[:count],
            url=value_set.get("url"),
            extensions=extensions,
            total=len(deduped),
        )

    def _expand_url_pattern(engine, url: str, count: int):
        """HTTP-handler wrapper around the module-level expand_url_pattern.

        Delegates to the module-level function; catches ValueError and
        converts to a FHIR 400 OperationOutcome response.
        """
        try:
            return expand_url_pattern(engine, url, count=count)
        except ValueError as exc:
            return _fhir_error(400, str(exc))

    def _is_implicit_value_set_url(url: str) -> bool:
        """Detect a FHIR R4 §4.7.3.1 implicit value set URL.

        Two convention-based forms are recognized:
          (a) `<system-uri>/vs` — e.g. http://loinc.org/vs (all of LOINC),
              http://hl7.org/fhir/sid/icd-10-cm/vs (all of ICD-10-CM), etc.
          (b) `http://snomed.info/sct?fhir_vs` with no code in the path —
              all of SNOMED CT. (When a code IS in the path it's the
              intensional form handled by _expand_url_pattern.)

        The check is structural only — the engine is consulted in
        _expand_implicit_value_set to confirm the URI is one of the supported
        systems. Found by SKEPTIC iteration TS-03 (QA-032).
        """
        from urllib.parse import urlparse

        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

        parsed = urlparse(url)
        path = parsed.path.rstrip("/")

        # Form (a): URL ends with /vs and the prefix (scheme://host[:port]/path)
        # is a known code system URI. Strip the trailing /vs from the full URL
        # to reconstruct the system URI (the path alone loses scheme+host).
        if path.endswith("/vs"):
            # Reconstruct the prefix URL by removing the trailing "/vs" from
            # the original URL string. urlparse + path manipulation alone
            # would lose the scheme://host portion.
            stripped = url.rstrip("/")
            if stripped.endswith("/vs"):
                prefix = stripped[:-3]
                if fhir_uri_to_system(prefix) is not None:
                    return True

        # Form (b): SNOMED all-codes — base SNOMED URI with ?fhir_vs (no =isa)
        # and no code in the path. The intensional form with a code (e.g.
        # /sct/73211009?fhir_vs=isa) is handled by _expand_url_pattern.
        #
        # Two parsing subtleties (found by HISTORIAN iteration TS-03 QA-034):
        # 1. parse_qs("fhir_vs") returns {} because parse_qs requires key=value
        #    pairs. A bare `fhir_vs` query (no `=`) needs raw-string inspection:
        #    check `parsed.query == "fhir_vs"` (exactly the bare form).
        # 2. The SNOMED URI is `http://snomed.info/sct` — netloc=`snomed.info`
        #    is in the MIDDLE, not the suffix. The prior
        #    `snomed_uri.rstrip("/").endswith(f"/{parsed.netloc}")` was always
        #    False. Compare netlocs directly via urlparse.
        snomed_uri = SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]
        snomed_parsed = urlparse(snomed_uri)
        if (
            parsed.netloc
            and parsed.netloc == snomed_parsed.netloc
            and path == snomed_parsed.path.rstrip("/")
        ):
            # path is exactly the SNOMED base path AND query is the bare
            # `fhir_vs` form (no value, no =isa). Anything with a code in
            # the path or a value (e.g. `=isa`/`=refset`) goes to
            # _expand_url_pattern.
            if parsed.query == "fhir_vs":
                return True

        return False

    def _expand_implicit_value_set(engine, url: str, count: int):
        """Expand a FHIR R4 §4.7.3.1 implicit value set URL.

        Per the spec: 'Some code systems define a value set which includes all
        codes in the code system. These are "implicit" value sets... For example,
        http://loinc.org/vs is the value set that includes all of LOINC.'

        Implementation: resolve the URL to a medterm4ds source name and expand
        to all codes in that source (capped at `count`). For very large code
        systems (LOINC, SNOMED) the count cap will trigger the `too-costly`
        truncation extension.

        Spec: https://hl7.org/fhir/terminology-service.html#4.7.3.1
        Found by SKEPTIC iteration TS-03 (QA-032).
        """
        from urllib.parse import urlparse

        from medterm4ds.core.models import CodeRef
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI
        from medterm4ds.services.lookup import get_code_infos

        parsed = urlparse(url)
        path = parsed.path.rstrip("/")

        # Determine the source name from the URL form.
        if path.endswith("/vs"):
            # Form (a): <system-uri>/vs — reconstruct the system URI by
            # stripping the trailing /vs from the full URL (path alone loses
            # scheme+host).
            stripped = url.rstrip("/")
            prefix = stripped[:-3] if stripped.endswith("/vs") else ""
            source = fhir_uri_to_system(prefix)
            if source is None:
                return _fhir_error(
                    400,
                    f"Unrecognized code system URI in implicit value set URL: {url!r}",
                )
            system_uri = prefix
        else:
            # Form (b): SNOMED ?fhir_vs (no code)
            source = "SNOMEDCT_US"
            system_uri = SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]

        # For a code system with potentially millions of codes, we cannot
        # enumerate the entire mrconso. Issue a count-bounded lookup and emit
        # the too-costly truncation extension when the count cap is hit.
        # For the conformance suite's synthetic DB this is small enough to
        # return everything; for production LOINC/SNOMED it will truncate.
        # Per GLOBAL_RULES.md "Silent Fallbacks": catch duckdb.Error only,
        # NOT broad Exception — programming errors MUST propagate.
        import duckdb
        try:
            rows = engine.con.execute(
                "SELECT DISTINCT CODE FROM mrconso WHERE SAB = ? AND SUPPRESS = 'N' LIMIT ?",
                [source, count + 1],
            ).fetchall()
        except duckdb.Error as exc:
            return _fhir_error(
                500,
                f"Database error enumerating implicit value set for {url!r}: {exc}",
            )

        contains: list[dict[str, Any]] = []
        for (code,) in rows:
            if len(contains) >= count:
                break
            # Resolve display via get_code_infos for the canonical name.
            infos = get_code_infos([CodeRef(source, code)], engine=engine)
            display = (infos[0].name if infos and infos[0] else None) or code
            contains.append({
                "system": system_uri,
                "code": code,
                "display": display,
            })

        count_limited = len(rows) > count
        max_depth = _resolve_max_depth()
        extensions = _truncation_extensions(
            count_limited=count_limited,
            depth_cap_hit=False,
            count=count,
            max_depth=max_depth,
        )
        # Empty-expansion signal (found by HISTORIAN iteration TS-03 QA-033):
        # when the implicit enumeration returns 0 codes for a known system,
        # attach an explanatory extension so the client can distinguish "the
        # system has 0 codes" from "the server failed silently". A bare
        # `total:0` with no extension is silent-wrong-answer — the spec
        # expects implicit value sets to include "all codes in the code
        # system" (§4.7.3.1), so a 0-code response for an advertised system
        # is non-conformant without a signal.
        if not contains:
            extensions.append({
                "url": "http://medterm4ds.org/fhir/StructureDefinition/valueset-empty-source",
                "valueString": (
                    f"Implicit value set {url!r} expanded to 0 codes — the "
                    f"underlying store has no rows for source {source!r}."
                ),
            })
        # VS-02 SKEPTIC QA-057: pass UN-truncated ``total`` so the response
        # reflects the full expansion size (FHIR R4 §4.9.2). The implicit
        # value set path queries ``LIMIT count + 1`` so we can detect
        # truncation: when len(rows) > count, the true total is unknown
        # (could be much larger); use len(rows) which is count+1 — this is
        # the minimum correct lower bound and matches the toocostly signal.
        untruncated_total = len(rows) if len(rows) > count else len(contains)
        return build_valueset_expand(
            contains[:count], url=url, extensions=extensions, total=untruncated_total,
        )

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
        # CR-005 (milestone-1 review): _check_ready returns a 503 Response
        # when the app is not ready — return it instead of raising (Response
        # is not a BaseException; raising causes TypeError).
        not_ready = _check_ready(request)
        if not_ready is not None:
            return not_ready
        # CR-001 (milestone-1 review): funnel through _fhir_response so the
        # Content-Type is application/fhir+json (or application/fhir+xml when
        # negotiated), not Starlette's application/json default. Same
        # dispatcher pattern as $lookup/$validate-code (TS-02 QA-021).
        # _do_search may itself return a Response (its 503-no-index path
        # calls _fhir_error) — pass Response through as-is, matching the
        # pattern used by $lookup/$validate-code/$translate handlers.
        payload = await asyncio.to_thread(_do_search, query, system, count, searchMode)
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

    @app.post("/fhir/CodeSystem/$search")
    async def search_post(request: Request, body: dict[str, Any]):
        # CR-005: return-don't-raise (see search_get).
        not_ready = _check_ready(request)
        if not_ready is not None:
            return not_ready
        params = _parse_parameters(body)
        query_text = params.get("query") or params.get("_query")
        system = params.get("system")
        count = _parse_count_param(params.get("count"), default=20)
        if count is None:
            return _fhir_error_response(request, 400, f"count must be an integer in [1, 1000] (got {params.get('count')!r}).")
        search_mode = params.get("searchMode", "lexical")
        if not query_text:
            return _fhir_error_response(request, 400, "query is required.")
        # CR-001: see search_get — funnel through _fhir_response.
        payload = await asyncio.to_thread(_do_search, str(query_text), system, count, search_mode)
        return payload if isinstance(payload, Response) else _fhir_response(request, payload)

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
        # CR-005: return-don't-raise (see search_get).
        not_ready = _check_ready(request)
        if not_ready is not None:
            return not_ready
        # CR-001 (milestone-1 review): funnel through _fhir_response so the
        # Content-Type is application/fhir+json (or application/fhir+xml when
        # negotiated), not Starlette's application/json default.
        payload = await _run_db(_ner_executor(request), _do_extract, text, format, categories, mode, minGrade, includeNegated)
        return _fhir_response(request, payload)

    @app.post("/fhir/CodeSystem/$extract")
    async def extract_post(request: Request, body: dict[str, Any]):
        # CR-005: return-don't-raise (see search_get).
        not_ready = _check_ready(request)
        if not_ready is not None:
            return not_ready
        params = _parse_parameters(body)
        text = params.get("text")
        if not text:
            return _fhir_error_response(request, 400, "text is required.")
        if len(text) > MAX_EXTRACT_TEXT_CHARS:
            return _fhir_error_response(
                request,
                400,
                f"text length {len(text)} exceeds max {MAX_EXTRACT_TEXT_CHARS} chars "
                f"(set MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS to override).",
            )
        # CR-001: see extract_get — funnel through _fhir_response.
        payload = await _run_db(
            _ner_executor(request), _do_extract, str(text),
            params.get("format", "codes"),
            params.get("categories"),
            params.get("mode", "hybrid"),
            params.get("minGrade", "certain"),
            params.get("includeNegated", "false").lower() == "true",
        )
        return _fhir_response(request, payload)

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
        """Extract named scalar parameters from a FHIR Parameters resource body.

        Note: only extracts scalar ``value*`` entries. The ``coding`` parameter
        (``valueCoding``) and ``codeableConcept`` (``valueCodeableConcept``)
        are complex types — use ``_extract_coding_from_parameters`` to pull
        ``(system, code)`` out of them. FHIR R4 explicitly allows ``coding``
        as a complete alternative to ``system``+``code`` on $lookup and
        $validate-code; the helper below closes that spec-compliance gap.
        """
        out: dict[str, str] = {}
        for param in body.get("parameter", []):
            name = param.get("name", "")
            for key in ("valueString", "valueUri", "valueCode", "valueInteger", "valueBoolean"):
                if key in param:
                    out[name] = str(param[key])
                    break
        return out

    def _extract_coding_from_parameters(body: dict[str, Any]) -> tuple[str, str] | None:
        """Extract (system, code) from a FHIR Parameters body's `coding` parameter.

        Per FHIR R4 $lookup and $validate-code, the ``coding`` parameter
        (``valueCoding``) is a complete alternative to ``system``+``code``.
        Without this helper, POST requests using ``coding`` are silently
        rejected with 400 'system and code are required.' — silent-wrong-answer
        (TS-02 HISTORIAN QA-022, QA-023).

        Returns ``(system, code)`` if a coding with both fields is present,
        else ``None``. If multiple coding parameters are supplied, the first
        one with both fields wins (spec allows multiple but server picks one).
        """
        return _extract_named_coding_from_parameters(body, "coding")

    def _extract_named_coding_from_parameters(
        body: dict[str, Any], name: str
    ) -> tuple[str, str] | None:
        """Extract (system, code) from a named `valueCoding` parameter.

        Generalization of ``_extract_coding_from_parameters`` for any named
        coding parameter. Used by CodeSystem/$subsumes to extract
        ``codingA`` / ``codingB`` per FHIR R4 §4.8.21.3 (CS-04 SKEPTIC
        QA-053 — the silent-reject-on-alternative-encoding pattern fired
        again on the $subsumes POST handler).

        Returns ``(system, code)`` if a coding with both fields is present
        under the named parameter, else ``None``.
        """
        for param in body.get("parameter", []):
            if param.get("name") != name:
                continue
            coding = param.get("valueCoding")
            if not isinstance(coding, dict):
                continue
            system = coding.get("system")
            code = coding.get("code")
            if system and code:
                return str(system), str(code)
        return None

    def _extract_codeable_concept_from_parameters(body: dict[str, Any]) -> tuple[str, str] | None:
        """Extract (system, code) from a FHIR Parameters body's `codeableConcept`.

        Per FHIR R4 $validate-code, a client SHALL provide one (and only one)
        of (code+system, coding, codeableConcept). The CodeableConcept wraps
        a list of Coding; the server picks the first coding with both
        system and code (the spec doesn't define server behavior for multiple
        codings on $validate-code — picking the first is a reasonable default).

        Without this helper, POST $validate-code with codeableConcept is
        silently rejected with 400 'system and code are required.' — same
        silent-wrong-answer shape as QA-022/QA-023 (found by TS-02 EXPLORER
        QA-026).

        Returns ``(system, code)`` if a codeableConcept with at least one
        valid coding is present, else ``None``.

        Note: for CodeSystem/$validate-code where the spec mandates "the
        server returns true if **one of the coding values** is in the code
        system", use ``_extract_all_coding_pairs_from_codeable_concept`` to
        get the full list (CS-03 SKEPTIC QA-049 — picking only the first
        coding silently wrong-answers when the first coding is invalid but
        a later coding is valid).
        """
        for param in body.get("parameter", []):
            if param.get("name") != "codeableConcept":
                continue
            cc = param.get("valueCodeableConcept")
            if not isinstance(cc, dict):
                continue
            for coding in cc.get("coding", []):
                if not isinstance(coding, dict):
                    continue
                system = coding.get("system")
                code = coding.get("code")
                if system and code:
                    return str(system), str(code)
        return None

    def _extract_all_coding_pairs_from_codeable_concept(
        body: dict[str, Any],
    ) -> list[tuple[str, str]] | None:
        """Extract ALL (system, code) pairs from a FHIR Parameters body's
        `codeableConcept` parameter.

        Per FHIR R4 CodeSystem/$validate-code In Parameters (https://hl7.org/
        fhir/R4/codesystem-operation-validate-code.html), the spec mandates:
        "A full codeableConcept to validate. **The server returns true if one
        of the coding values is in the code system**, and may also validate
        that the codings are not in conflict with each other if more than one
        is present."

        The first-coding-only helper ``_extract_codeable_concept_from_parameters``
        is insufficient for this semantic — it silently wrong-answers when the
        first coding is invalid but a later coding is valid. Found by CS-03
        SKEPTIC iteration (QA-049).

        Returns the list of ``(system, code)`` pairs from the first
        codeableConcept with at least one valid coding, else ``None``.
        """
        for param in body.get("parameter", []):
            if param.get("name") != "codeableConcept":
                continue
            cc = param.get("valueCodeableConcept")
            if not isinstance(cc, dict):
                continue
            pairs: list[tuple[str, str]] = []
            for coding in cc.get("coding", []):
                if not isinstance(coding, dict):
                    continue
                system = coding.get("system")
                code = coding.get("code")
                if system and code:
                    pairs.append((str(system), str(code)))
            if pairs:
                return pairs
        return None

    def _extract_valueset_from_parameters(body: dict[str, Any]) -> dict[str, Any] | None:
        """Extract an inline ValueSet from a FHIR Parameters body.

        Per FHIR R4 §4.7.5 In Parameters ``valueSet`` (0..1 ValueSet):
        "The value set is provided directly as part of the request. Servers
        SHOULD expand the value set and SHOULD NOT use the value set cached
        on the server." The body shape is a Parameters resource with a
        parameter carrying a nested ValueSet via the ``resource`` property
        (per https://hl7.org/fhir/R4/parameters.html — "A parameter can
        have a resource as a value using the ``resource`` property rather
        than value[x]").

        Without this helper, POST $expand with a Parameters-with-valueSet
        body silently drops the complex-type parameter (the scalar-only
        ``_parse_parameters`` ignores it) and falls through to the no-url/
        no-filter 400 path — silent-wrong-answer on a spec-listed
        alternative encoding. Found by SKEPTIC iteration VS-03 (QA-059).
        Same shape as the helper-exists-but-not-wired pattern (TS-02
        HISTORIAN QA-022/QA-023, TS-02 EXPLORER QA-026/QA-028).

        Returns the nested ValueSet dict if a parameter named ``valueSet``
        carries a valid ValueSet resource (resourceType == "ValueSet"),
        else None. Malformed shapes (missing resource, wrong resourceType)
        return None and let the caller fall through to the existing 400
        path (graceful degradation, no crash).
        """
        for param in body.get("parameter", []):
            if param.get("name") != "valueSet":
                continue
            resource = param.get("resource")
            if not isinstance(resource, dict):
                continue
            if resource.get("resourceType") == "ValueSet":
                return resource
        return None

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

    # -- CodeSystem/ValueSet/ConceptMap READ + SEARCH stubs --
    # Registered AFTER all $operation routes so the operation paths
    # (/fhir/CodeSystem/$lookup etc.) match before the catch-all READ route.
    # FHIR R4 §4.7.1.1 items 2 + 3 require READ and SEARCH interactions for
    # these three resource types. medterm4ds is a terminology *service*, not a
    # terminology *store* — it doesn't persist CodeSystem/ValueSet/ConceptMap
    # resources. The conformance requirement is that the routes exist and
    # return FHIR-structured responses (Bundle for SEARCH, OperationOutcome for
    # an unknown READ id). Full CRUD persistence is out of scope for v0.0.x.
    _CONFORMANCE_RESOURCE_TYPES = ("CodeSystem", "ValueSet", "ConceptMap")
    _CONFORMANCE_SEARCH_PARAMS = ("url", "version", "name", "title", "status")

    # -- Instance-level operations --
    # FHIR R4 §4.7.1.2: $expand/$validate-code/$translate MAY be invoked on a
    # resource instance, not just by type. medterm4ds doesn't persist ValueSets
    # or ConceptMaps, so the instance-level routes return 404 OperationOutcome
    # 'not-found' for unknown ids — but the ROUTE must exist so the catch-all
    # doesn't shadow it. Found missing by SKEPTIC iteration TS-02 (QA-014,
    # QA-015). Registered here so they sit AFTER the type-level operation
    # routes and BEFORE the per-resource READ/SEARCH stubs and catch-alls.
    @app.get("/fhir/ValueSet/{resource_id}/$expand")
    async def expand_instance(
        request: Request,
        resource_id: str,
        url: str | None = Query(None),
        filter: str | None = Query(None),
        count: int = Query(20, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        # The server doesn't persist ValueSets — every id is unknown. Return
        # a structured OperationOutcome rather than silently running type-level
        # expansion (which would be a silent-wrong-answer anti-pattern).
        return _fhir_response(
            request,
            build_operation_outcome(
                "error",
                "not-found",
                f"No stored ValueSet with id {resource_id!r}. medterm4ds exposes "
                "type-level $expand (/fhir/ValueSet/$expand) for ad-hoc expansions "
                "but does not persist ValueSet resources for instance-level invocation.",
            ),
            status=404,
        )

    @app.get("/fhir/ValueSet/{resource_id}/$validate-code")
    async def vs_validate_instance(
        request: Request,
        resource_id: str,
        code: str | None = Query(None),
        system: str | None = Query(None),
        display: str | None = Query(None),
    ):
        return _fhir_response(
            request,
            build_operation_outcome(
                "error",
                "not-found",
                f"No stored ValueSet with id {resource_id!r}. medterm4ds exposes "
                "type-level $validate-code (/fhir/ValueSet/$validate-code) but does "
                "not persist ValueSet resources for instance-level invocation.",
            ),
            status=404,
        )

    @app.post("/fhir/ValueSet/{resource_id}/$expand")
    async def expand_instance_post(
        request: Request,
        resource_id: str,
        body: dict[str, Any] | None = None,
    ):
        """POST instance-level $expand. medterm4ds doesn't persist ValueSets,
        so the route returns a structured 404 OperationOutcome rather than
        silently running type-level expansion. FHIR R4 §3.1.0.1.1 permits
        operations to be invoked via POST on instances. Without this route,
        Starlette returns a non-FHIR 405 'Method Not Allowed' (found by TS-02
        EXPLORER QA-024)."""
        return _fhir_response(
            request,
            build_operation_outcome(
                "error",
                "not-found",
                f"No stored ValueSet with id {resource_id!r}. medterm4ds exposes "
                "type-level $expand (/fhir/ValueSet/$expand) for ad-hoc expansions "
                "but does not persist ValueSet resources for instance-level invocation.",
            ),
            status=404,
        )

    @app.post("/fhir/ValueSet/{resource_id}/$validate-code")
    async def vs_validate_instance_post(
        request: Request,
        resource_id: str,
        body: dict[str, Any] | None = None,
    ):
        """POST instance-level $validate-code. Same shape as expand_instance_post.
        Without this route, Starlette returns a non-FHIR 405 (found by TS-02
        EXPLORER QA-025)."""
        return _fhir_response(
            request,
            build_operation_outcome(
                "error",
                "not-found",
                f"No stored ValueSet with id {resource_id!r}. medterm4ds exposes "
                "type-level $validate-code (/fhir/ValueSet/$validate-code) but does "
                "not persist ValueSet resources for instance-level invocation.",
            ),
            status=404,
        )

    @app.get("/fhir/ConceptMap/{resource_id}/$translate")
    async def translate_instance_get(
        request: Request,
        resource_id: str,
        system: str | None = Query(None),
        code: str | None = Query(None),
        targetsystem: str | None = Query(None),
    ):
        return _fhir_response(
            request,
            build_operation_outcome(
                "error",
                "not-found",
                f"No stored ConceptMap with id {resource_id!r}. medterm4ds exposes "
                "type-level $translate (/fhir/ConceptMap/$translate) but does not "
                "persist ConceptMap resources for instance-level invocation.",
            ),
            status=404,
        )

    @app.post("/fhir/ConceptMap/{resource_id}/$translate")
    async def translate_instance_post(
        request: Request,
        resource_id: str,
        body: dict[str, Any] | None = None,
    ):
        return _fhir_response(
            request,
            build_operation_outcome(
                "error",
                "not-found",
                f"No stored ConceptMap with id {resource_id!r}. medterm4ds exposes "
                "type-level $translate (/fhir/ConceptMap/$translate) but does not "
                "persist ConceptMap resources for instance-level invocation.",
            ),
            status=404,
        )

    def _register_conformance_routes() -> None:
        for rtype in _CONFORMANCE_RESOURCE_TYPES:

            @app.get(f"/fhir/{rtype}/{{resource_id}}")
            async def read_resource(
                request: Request,
                resource_id: str,
                _rtype: str = rtype,
            ):
                # READ by id. Operation routes ($lookup etc.) are registered
                # earlier, so resource_id will never be "$lookup". Reject any
                # $-prefixed id explicitly — those are operation names misused
                # as resource ids.
                if resource_id.startswith("$"):
                    return _fhir_error(
                        404,
                        f"Unknown operation {resource_id!r}. "
                        f"See /fhir/metadata for the list of supported operations.",
                    )
                # The server doesn't persist these resources, so every id is
                # "not found". Return a structured OperationOutcome (not
                # FastAPI's default {"detail":"Not Found"} 404).
                return _fhir_response(
                    request,
                    build_operation_outcome(
                        "error",
                        "not-found",
                        f"No stored {_rtype} resource with id {resource_id!r}. "
                        "medterm4ds exposes terminology operations ($lookup, "
                        "$expand, etc.) rather than persisted resource instances.",
                    ),
                    status=404,
                )

            @app.get(f"/fhir/{rtype}")
            async def search_resource(
                request: Request,
                _rtype: str = rtype,
                # Declare the spec-required search params so they appear in
                # OpenAPI docs and so FastAPI doesn't reject them as unknown.
                url: str | None = Query(None),
                version: str | None = Query(None),
                name: str | None = Query(None),
                title: str | None = Query(None),
                status: str | None = Query(None),
            ):
                # SEARCH interaction. The server has no persisted resources to
                # match against, so an empty Bundle is the conformant response.
                # The spec-required params (url/version/name/title/status) are
                # accepted structurally — clients get a 200 Bundle with total=0.
                bundle = {
                    "resourceType": "Bundle",
                    "type": "searchset",
                    "total": 0,
                    "entry": [],
                }
                return _fhir_response(request, bundle)

            # POST /fhir/{CodeSystem|ValueSet|ConceptMap} — server is read-only
            # (no `create` interaction advertised in the CapabilityStatement).
            # Without an explicit handler Starlette returns its default 405
            # `{"detail":"Method Not Allowed"}` with Content-Type
            # `application/json` — non-conformant per FHIR R4 §3.1.0.1.5
            # ("The OperationOutcome may be returned with any HTTP 4xx or 5xx
            # response"). Same shape as TS-01 EXPLORER QA-011 (catch-all) and
            # TS-02 EXPLORER QA-024/QA-025 (instance-level POST). Found by
            # TS-03 EXPLORER (QA-035).
            @app.post(f"/fhir/{rtype}")
            async def create_resource_rejected(
                request: Request,
                _rtype: str = rtype,
            ):
                return _fhir_response(
                    request,
                    build_operation_outcome(
                        "error",
                        "not-supported",
                        f"POST /fhir/{_rtype} is not supported. medterm4ds is a "
                        "read-only terminology server — only the operations "
                        "($lookup, $expand, $validate-code, $translate, "
                        "$subsumes, $closure, $search) and READ/SEARCH "
                        "interactions are supported. No resource creation, "
                        "update, or deletion is performed.",
                    ),
                    status=405,
                )

    _register_conformance_routes()

    # -- Catch-all for unknown FHIR resource types --
    # Registered LAST so all explicit routes (operations + CodeSystem/ValueSet/
    # ConceptMap READ+SEARCH) match first. Without this, requests for any other
    # FHIR resource type (Patient, Observation, etc.) get Starlette's default
    # {"detail":"Not Found"} 404 — which is NOT a FHIR OperationOutcome. Per
    # §3.1.0.1.5: "The OperationOutcome may be returned with any HTTP 4xx or
    # 5xx response". Found by EXPLORER iteration TS-01 (QA-011).
    @app.get("/fhir/{resource_type}/{resource_id:path}")
    async def read_unknown_resource_type(
        request: Request,
        resource_type: str,
        resource_id: str,
    ):
        return _fhir_response(
            request,
            build_operation_outcome(
                "error",
                "not-found",
                f"Resource type {resource_type!r} is not supported by this "
                "terminology server. medterm4ds exposes CodeSystem, ValueSet, "
                "and ConceptMap (per FHIR R4 §4.7.1.1) plus the terminology "
                "operations listed in /fhir/metadata.",
            ),
            status=404,
        )

    @app.get("/fhir/{resource_type}")
    async def search_unknown_resource_type(
        request: Request,
        resource_type: str,
    ):
        return _fhir_response(
            request,
            build_operation_outcome(
                "error",
                "not-found",
                f"Resource type {resource_type!r} is not supported by this "
                "terminology server. medterm4ds exposes CodeSystem, ValueSet, "
                "and ConceptMap (per FHIR R4 §4.7.1.1) plus the terminology "
                "operations listed in /fhir/metadata.",
            ),
            status=404,
        )

    # POST catch-alls for unknown FHIR resource types (CF-EXPLORER-01).
    # The TS-01 EXPLORER QA-011 catch-alls above only handle GET; the
    # TS-03 EXPLORER QA-035 type-level POST handler only covers
    # CodeSystem/ValueSet/ConceptMap (registered inside
    # `_register_conformance_routes`). Without these POST catch-alls,
    # requests like POST /fhir/Patient fall through to Starlette's default
    # 405 handler emitting `application/json` Content-Type and
    # `{"detail":"Method Not Allowed"}` body — non-conformant per FHIR R4
    # §3.1.0.1.5 (any 4xx/5xx response MAY carry an OperationOutcome) and
    # §3.1.0.1.9 (the correct MIME type SHALL be used).
    # Found by EXPLORER iteration TS-04 (QA-042); count=4 for the framework-
    # default drift on POST routes pattern (TS-02 EXPLORER QA-024/QA-025
    # instance-level POST, TS-03 EXPLORER QA-035 type-level POST to
    # CodeSystem/ValueSet/ConceptMap, now TS-04 unknown-resource-type POST).
    @app.post("/fhir/{resource_type}/{resource_id:path}")
    async def write_unknown_resource_type(
        request: Request,
        resource_type: str,
        resource_id: str,
    ):
        return _fhir_response(
            request,
            build_operation_outcome(
                "error",
                "not-supported",
                f"Resource type {resource_type!r} is not supported by this "
                "terminology server. medterm4ds is read-only and exposes "
                "CodeSystem, ValueSet, and ConceptMap (per FHIR R4 §4.7.1.1) "
                "plus the terminology operations listed in /fhir/metadata.",
            ),
            status=405,
        )

    @app.post("/fhir/{resource_type}")
    async def create_unknown_resource_type(
        request: Request,
        resource_type: str,
    ):
        return _fhir_response(
            request,
            build_operation_outcome(
                "error",
                "not-supported",
                f"Resource type {resource_type!r} is not supported by this "
                "terminology server. medterm4ds is read-only and exposes "
                "CodeSystem, ValueSet, and ConceptMap (per FHIR R4 §4.7.1.1) "
                "plus the terminology operations listed in /fhir/metadata.",
            ),
            status=405,
        )

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
