"""Unified text-to-code search service.

Provides lexical (BM25), semantic (SapBERT + FAISS), and hybrid search
across medical terminology codes. Used by all surfaces: Python facade,
CLI, MCP server, and FHIR API.

Data sources:
  - BM25 indexes: pre-built JSON files with inverted index (postings/idf)
  - SapBERT model + FAISS indexes: fine-tuned embeddings + ANN search

Both are lazy-loaded on first use. BM25 loads from MEDTERM4DS_SEARCH_INDEX_DIR
(default: /mnt/d/fhir4px-model/dist/naming_bm25). SapBERT loads from
MEDTERM4DS_EMBEDDING_MODEL_DIR (default: /mnt/d/fhir4px-model/data/sapbert_finetuned).

No GPU required. Runs on CPU (~1ms lexical, ~100ms semantic).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from medterm4ds.core.models import CodeRef
from medterm4ds.core.normalize import SOURCE_LABELS

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

# --- Cache directory and HF repo configuration ---

# All medterm4ds artifacts (canonical, semantic, lexical) live under this dir.
# Override via MEDTERM4DS_CACHE_DIR env var. Defaults to platform cache dir.
_CACHE_DIR = Path(os.getenv(
    "MEDTERM4DS_CACHE_DIR",
    str(Path.home() / ".cache" / "medterm4ds"),
))

# Hugging Face repo holding prebuilt artifacts. Override via env vars for
# testing or private forks.
_HF_REPO_ID = os.getenv("MEDTERM4DS_HF_REPO_ID", "fhir4ds/medterm4ds")
_HF_REVISION = os.getenv("MEDTERM4DS_HF_REVISION", "v0.0.1")

DEFAULT_SEARCH_INDEX_DIR = str(_CACHE_DIR / "lexical")
DEFAULT_EMBEDDING_MODEL_DIR = str(_CACHE_DIR / "semantic")
DEFAULT_CANONICAL_VALUE_SETS_PATH = str(_CACHE_DIR / "canonical" / "canonical_anchor_value_sets.json")
DEFAULT_CANONICAL_CONCEPTS_INDEX = str(_CACHE_DIR / "canonical" / "canonical_concepts_faiss.index")
DEFAULT_CANONICAL_CONCEPTS_META = str(_CACHE_DIR / "canonical" / "canonical_concepts_metadata.json")
SEARCH_CATEGORIES = ("condition", "lab", "medication", "procedure", "vaccine", "body_structure")

# Upper bound on query length accepted by every search entry point
# (QC-126/QC-140: no cap existed — 10K-char queries were silently tokenized).
# Matches the FHIR $search GET max_length; a ValueError (not silent truncation)
# is raised so clients learn their input was rejected.
MAX_QUERY_CHARS = 1000


def _validate_query(query: Any) -> str:
    """Validate free-text query at the service boundary.

    Raises TypeError for non-str input (QC-128: list/dict/int/bool queries
    previously crashed deep in the tokenizer with a bare AttributeError) and
    ValueError for empty/whitespace-only or over-length queries (QC-122:
    whitespace queries were silently embedded by SapBERT and returned
    confidently-ranked anchors; QC-126: unbounded input length).
    """
    if not isinstance(query, str):
        raise TypeError(f"query must be a string, got {type(query).__name__}")
    if not query.strip():
        raise ValueError("query must not be empty")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(
            f"query length {len(query)} exceeds max {MAX_QUERY_CHARS} chars."
        )
    return query


def _validate_count(count: Any) -> int:
    """Validate result count at the service boundary (QC-125).

    Negative counts previously hit Python slice semantics (count=-1 silently
    returned all-but-last); floats/strings raised bare TypeError mid-pipeline.
    """
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError(f"count must be a positive integer, got {count!r}")
    return count


def _hf_download(allow_patterns: list[str]) -> None:
    """Download artifacts from Hugging Face Hub to the cache directory.

    Called lazily by _ensure_canonical / _ensure_semantic / _ensure_bm25
    when local artifacts are missing. Uses huggingface_hub.snapshot_download
    with allow_patterns so only the needed subfolder is fetched.

    Raises:
        ImportError: if huggingface_hub is not installed.
        Exception: from the HF API on network/auth errors.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ImportError(
            "huggingface_hub is required to auto-download search artifacts.\n"
            "Install with: pip install huggingface_hub\n"
            f"Or manually download from https://huggingface.co/{_HF_REPO_ID}"
        )
    logger.info("Downloading artifacts from Hugging Face (%s, revision=%s)...",
                _HF_REPO_ID, _HF_REVISION)
    snapshot_download(
        repo_id=_HF_REPO_ID,
        revision=_HF_REVISION,
        repo_type="model",
        local_dir=str(_CACHE_DIR),
        allow_patterns=allow_patterns,
        token=os.getenv("HF_TOKEN"),
    )
    logger.info("Download complete → %s", _CACHE_DIR)

_SOURCE_TO_CATEGORIES = {
    "SNOMEDCT_US": list(SEARCH_CATEGORIES),
    "ICD10CM": ["condition"],
    "ICD10PCS": ["procedure"],
    "RXNORM": ["medication"],
    "LNC": ["lab"],
    "CPT": ["procedure"],
    "HCPCS": ["procedure"],
    "CVX": ["vaccine"],
}

# Canonical source name → lowercase system label for SearchResult
_SOURCE_LABELS = {
    # Delegates to core.normalize.SOURCE_LABELS — single source of truth
    # shared with services.extraction. Kept as a local alias for back-compat
    # with any external code importing _SOURCE_LABELS directly.
    **SOURCE_LABELS,
}


@dataclass
class SearchResult:
    """One ranked search result."""

    code: str
    source: str  # internal source name (e.g., "SNOMEDCT_US")
    display: str
    score: float  # 0.0–1.0
    match_grade: str  # "certain" | "probable" | "possible"
    category: str = ""  # "condition", "medication", etc.

    @property
    def system_label(self) -> str:
        """Lowercase system label for API/JSON consumers."""
        return _SOURCE_LABELS.get(self.source, self.source.lower())

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source": self.source,
            "system": self.system_label,
            "display": self.display,
            "score": self.score,
            "match_grade": self.match_grade,
            "category": self.category,
        }

    def to_coderef(self) -> CodeRef:
        return CodeRef(source=self.source, code=self.code)


@dataclass
class CanonicalSearchResult:
    """One ranked canonical value set search result."""

    canonical_id: str
    domain: str
    anchor_system: str
    anchor_code: str
    patient_friendly_name: str
    score: float
    match_grade: str
    matched_via_code: str
    matched_via_display: str
    total_member_count: int
    members: list[dict[str, Any]] = field(default_factory=list)
    combination_members: list[dict[str, Any]] = field(default_factory=list)

    @property
    def code(self) -> str:
        return self.anchor_code

    @property
    def source(self) -> str:
        return self.anchor_system

    @property
    def display(self) -> str:
        return self.patient_friendly_name

    @property
    def result_type(self) -> str:
        """Clinical result type derived from the canonical_id prefix.

        Single source of truth for "what kind of anchor is this?" — distinct
        from the legacy BM25 `category` field on SearchResult. Useful when
        category lumping happens upstream (e.g., extraction maps medication
        label to search across medication + drug_class + vaccine anchors).
        Lets callers differentiate without parsing canonical_id themselves.

        Returns: "condition" | "symptom" | "lab" | "vital" | "medication" |
                 "drug_class" | "procedure" | "vaccine" | "" (unknown)
        """
        return _prefix_to_result_type(self.canonical_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "domain": self.domain,
            "result_type": self.result_type,
            "anchor_system": self.anchor_system,
            "anchor_code": self.anchor_code,
            "patient_friendly_name": self.patient_friendly_name,
            "score": self.score,
            "match_grade": self.match_grade,
            "matched_via_code": self.matched_via_code,
            "matched_via_display": self.matched_via_display,
            "total_member_count": self.total_member_count,
            "members": self.members,
            "combination_members": self.combination_members,
        }


def _score_to_grade(score: float) -> str:
    if score >= 0.8:
        return "certain"
    if score >= 0.4:
        return "probable"
    return "possible"


# Map clinical result_type → canonical_id prefix(es).
# Used by SearchService.canonical(result_types=...) to filter at the service
# level. Single source systems (LOINC, RXNORM, ATC, CVX, CPT) map cleanly to
# one result type; SNOMEDCT_US spans conditions, procedures, symptoms, and
# body structures, so the result_types filter is needed to disambiguate.
_RESULT_TYPE_TO_PREFIXES: dict[str, tuple[str, ...]] = {
    "condition": ("VAL-COND-",),
    "symptom": ("VAL-SYMP-",),
    "lab": ("VAL-LAB-",),
    "vital": ("VAL-VIT-",),
    "medication": ("VAL-MED-",),
    "drug_class": ("VAL-DRUGCLASS-",),
    "procedure": ("VAL-PROC-",),
    "vaccine": ("VAL-VAX-",),
}

# Valid canonical-mode result types (the keys above), for callers that need
# to validate/intersect --result-types values before they reach
# ``SearchService.canonical`` (which raises on unknown types).
CANONICAL_RESULT_TYPES: tuple[str, ...] = tuple(_RESULT_TYPE_TO_PREFIXES)


def _result_types_to_prefixes(result_types: str | list[str] | None) -> set[str]:
    """Normalize result_types parameter to a set of canonical_id prefixes.

    Returns an empty set if result_types is None (no filter).
    Raises ValueError for unknown result types.
    """
    if result_types is None:
        return set()
    types = [result_types] if isinstance(result_types, str) else list(result_types)
    prefixes: set[str] = set()
    for t in types:
        key = t.lower().strip()
        if key not in _RESULT_TYPE_TO_PREFIXES:
            valid = ", ".join(sorted(_RESULT_TYPE_TO_PREFIXES))
            raise ValueError(
                f"Unknown result type: {t!r}. Valid: {valid}."
            )
        prefixes.update(_RESULT_TYPE_TO_PREFIXES[key])
    return prefixes


# Reverse map for deriving the result_type from a canonical_id prefix.
# Used by CanonicalSearchResult.result_type and
# ExtractedConcept.result_type so downstream consumers can differentiate
# medication vs drug_class vs vaccine without parsing canonical_id themselves.
_PREFIX_TO_RESULT_TYPE: dict[str, str] = {
    prefix: rt
    for rt, prefixes in _RESULT_TYPE_TO_PREFIXES.items()
    for prefix in prefixes
}


def _prefix_to_result_type(canonical_id: str) -> str:
    """Derive the result type from a canonical_id prefix.

    Returns the empty string if no prefix matches (defensive — should not
    happen with well-formed canonical_ids).
    """
    for prefix, rt in _PREFIX_TO_RESULT_TYPE.items():
        if canonical_id.startswith(prefix):
            return rt
    return ""


def _strip_accents(text: str) -> str:
    """Fold Latin accents to ASCII (NFD + drop combining marks).

    QC-329 (HIGH): the pre-built BM25 indexes tokenize documents with an
    ASCII-only ``[a-zA-Z]{3,}`` regex (``build_bm25_v2.tokenize``), so every
    posting key is unaccented. Query tokens taken verbatim
    (``query.lower().split()``) kept accents AND punctuation attached, so
    accented clinical queries ('Guillain-Barré', 'Chédiak', 'Déjà vu')
    silently returned 0 hits for concepts whose preferred terms exist
    verbatim in mrconso. Fold accents + extract alphanumeric runs so the
    query tokenizes exactly like the indexed documents.
    """
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _query_tokens(query: str) -> list[str]:
    """Tokenize a free-text query for BM25 lookup (QC-329).

    Mirrors the index-build tokenizer (ASCII alphanumeric runs of >= 3
    chars, lowercased, accents folded): hyphens and other punctuation split
    tokens the same way the build split them, and accented characters fold
    to their unaccented base instead of acting as token boundaries. Index
    postings only contain build-token keys, so shorter/digit-only runs could
    never match anyway.
    """
    return re.findall(r"[a-z0-9]{3,}", _strip_accents(query.lower()))


def _stem_token(token: str) -> str:
    """Porter-like stemmer to match pre-built BM25 tokenization."""
    token = token.lower()
    for suffix in ("ational", "tional", "iveness", "fulness", "ousness",
                   "ization", "ation", "ations", "izer", "ator", "alism",
                   "iciti", "ical", "ness", "ements", "ement", "ments",
                   "ment", "iences", "ience", "iable", "ing", "ies", "ied",
                   "ily", "sses", "ss", "s", "y", "e"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            if suffix == "ies":
                return token[:-3] + "i"
            if suffix == "ied":
                return token[:-3] + "i"
            if suffix == "sses":
                return token[:-2]
            if suffix == "s" and not token.endswith("ss"):
                return token[:-1]
            if suffix == "e" and len(token) > 3:
                return token[:-1]
            if suffix == "ing":
                return token[:-3]
            if suffix == "y":
                return token[:-1] + "i"
            return token[: -len(suffix)]
    return token


def apply_preferred_display(
    results: list[SearchResult],
    engine,
) -> list[SearchResult]:
    """Canonicalize legacy-mode result displays to the engine preferred term.

    QC-400 (MEDIUM) — the QC-317 fix was applied on the FHIR $search surface
    only, so the same query/mode returned identical codes, scores, and ranking
    but DIFFERENT display strings on FHIR vs Python/CLI/MCP. The fix lives in
    this service (single source of truth) so every surface that passes an
    engine inherits it. The BM25 search index is cross-source: the matched
    ``display`` can be an anchor/CHV synonym that does not exist in the
    result's own code system (e.g. SNOMED 73211009 surfaced as 'Diabetes',
    which is not any SNOMED synonym). Resolve via ONE batched get_code_infos
    call; fall back to the matched index synonym only when no preferred atom
    resolves. Requires an engine — the CLI ``search`` command deliberately
    opens no database (QC-382) and therefore keeps the index display.
    """
    from medterm4ds.services.lookup import get_code_infos

    infos = get_code_infos(
        [CodeRef(r.source, r.code) for r in results], engine=engine
    )
    updated: list[SearchResult] = []
    for r, info in zip(results, infos):
        name = info.name if info else None
        updated.append(replace(r, display=name) if name else r)
    return updated


class SearchService:
    """Unified search service with lazy-loaded BM25 + SapBERT indexes.

    Thread-safe singleton pattern. First call to any search method triggers
    index loading. Subsequent calls reuse cached indexes.
    """

    def __init__(
        self,
        *,
        search_index_dir: str = DEFAULT_SEARCH_INDEX_DIR,
        embedding_model_dir: str = DEFAULT_EMBEDDING_MODEL_DIR,
        canonical_path: str = DEFAULT_CANONICAL_VALUE_SETS_PATH,
        canonical_concepts_index: str = DEFAULT_CANONICAL_CONCEPTS_INDEX,
        canonical_concepts_meta: str = DEFAULT_CANONICAL_CONCEPTS_META,
    ):
        self._bm25_dir = Path(search_index_dir)
        self._model_dir = Path(embedding_model_dir)
        self._canonical_path = Path(canonical_path)
        self._concepts_index_path = Path(canonical_concepts_index)
        self._concepts_meta_path = Path(canonical_concepts_meta)
        self._bm25_indexes: dict[str, dict] = {}
        self._semantic_engine = None
        self._bm25_loaded = False
        self._canonical_loaded = False
        self._canonical_by_id: dict[str, dict] = {}
        self._canonical_by_anchor: dict[tuple[str, str], str] = {}
        self._code_to_canonical_id: dict[tuple[str, str], str] = {}
        self._concepts_faiss = None
        self._concepts_meta: list[dict] = []
        self._concepts_loaded = False

    @property
    def lexical_available(self) -> bool:
        return self._bm25_loaded or (self._bm25_dir.is_dir() and any(self._bm25_dir.glob("*_bm25.json")))

    @property
    def semantic_available(self) -> bool:
        return (self._model_dir / "model.safetensors").exists()

    def _ensure_bm25(self) -> None:
        if self._bm25_loaded:
            return
        if not self._bm25_dir.is_dir() or not any(self._bm25_dir.glob("*_bm25.json")):
            _hf_download(["lexical/*"])
        if not self._bm25_dir.is_dir():
            raise RuntimeError(f"BM25 index directory not found: {self._bm25_dir}")
        for category in SEARCH_CATEGORIES:
            json_path = self._bm25_dir / f"{category}_bm25.json"
            if json_path.exists():
                with json_path.open() as f:
                    index = json.load(f)
                if isinstance(index, dict) and "postings" in index:
                    self._bm25_indexes[category] = index
                    logger.info("BM25 %s: %d records", category, index.get("num_records", 0))
        self._bm25_loaded = True

    def _ensure_semantic(self):
        if self._semantic_engine is not None:
            return self._semantic_engine
        if not self.semantic_available:
            _hf_download(["semantic/*"])
        if not self.semantic_available:
            raise RuntimeError(f"SapBERT model not found at {self._model_dir}")
        from medterm4ds.engines.fhir.semantic import SemanticSearchEngine
        self._semantic_engine = SemanticSearchEngine(str(self._model_dir))
        return self._semantic_engine

    def _ensure_canonical(self) -> None:
        if self._canonical_loaded:
            return
        if not self._canonical_path.exists():
            _hf_download(["canonical/*"])
        if not self._canonical_path.exists():
            raise RuntimeError(f"Canonical ValueSets file not found: {self._canonical_path}")
        with self._canonical_path.open() as f:
            vsets = json.load(f)
        for v in vsets:
            cid = v["canonical_id"]
            self._canonical_by_id[cid] = v
            anchor_system = v.get("anchor_system") or v.get("system")
            anchor_code = v.get("anchor_code") or v.get("code")
            self._canonical_by_anchor[(anchor_system, anchor_code)] = cid
            self._code_to_canonical_id[(anchor_system, anchor_code)] = cid
            for m in v.get("members", []):
                self._code_to_canonical_id[(m["system"], m["code"])] = cid
        self._canonical_loaded = True
        logger.info("Loaded %d Master Canonical Value Sets (%d code pairs)", len(vsets), len(self._code_to_canonical_id))

    def _resolve_categories(self, sources: list[str] | None) -> list[str]:
        if sources is None:
            return list(SEARCH_CATEGORIES)
        cats: list[str] = []
        for source in sources:
            source_upper = source.upper()
            cats.extend(_SOURCE_TO_CATEGORIES.get(source_upper, []))
        return list(dict.fromkeys(cats))  # dedupe, preserve order

    @staticmethod
    def _restrict_categories(
        categories: list[str], result_types: str | list[str] | None
    ) -> list[str]:
        """Intersect search categories with requested ``result_types``.

        Legacy-mode analogue of ``canonical(result_types=...)``: restricts
        which category indexes are searched BEFORE retrieval, so ``count``
        caps the filtered result set instead of truncating before a
        client-side filter drops non-matching rows (``search
        --result-types lab --limit 1`` returned 0 results pre-fix).

        Values that are not SEARCH_CATEGORIES (e.g. canonical-only types
        like "symptom") intersect away; an empty intersection means the
        request can match nothing, which callers turn into an empty result.
        """
        if not result_types:
            return categories
        if isinstance(result_types, str):
            result_types = [result_types]
        wanted = set(result_types)
        return [c for c in categories if c in wanted]

    @staticmethod
    def _filter_by_source(
        results: list["SearchResult"], sources: list[str] | None
    ) -> list["SearchResult"]:
        """Drop results whose source isn't in the requested set.

        Category-level filtering alone isn't sufficient — ICD10CM and SNOMEDCT_US
        both map to the "condition" category, so a SNOMED-only request would
        otherwise return ICD-10 codes that share a concept name (e.g., querying
        "Crohn disease" with sources=['SNOMEDCT_US'] was returning K50.91).
        """
        if not sources:
            return results
        allowed = {s.upper() for s in sources}
        return [r for r in results if r.source.upper() in allowed]

    @staticmethod
    def _total_member_count(vset: dict, m: dict | None = None) -> int:
        """Resolve the value set's true member count (QC-143).

        Read-side fallback chain — the deployed artifacts are NOT rebuilt:
        (a) ``total_member_count`` on the value-set JSON (absent on all
        47,266 deployed value sets), (b) ``total_member_count`` on the
        concepts-metadata entry (present but 0 on all 91,398 entries — the
        index build never populated it), (c) ``member_count`` on the
        value-set JSON (present on 18,917), (d) ``len(members)``.
        """
        total = vset.get("total_member_count", (m or {}).get("total_member_count", 0))
        if total:
            return int(total)
        total = vset.get("member_count", 0)
        if total:
            return int(total)
        return len(vset.get("members", []))

    def _ensure_concepts(self):
        """Lazy-load canonical concept FAISS index + metadata (Option C-B)."""
        if self._concepts_loaded:
            return
        if not self._concepts_index_path.exists() or not self._concepts_meta_path.exists():
            _hf_download(["canonical/*"])
        if not self._concepts_index_path.exists() or not self._concepts_meta_path.exists():
            self._concepts_loaded = True  # mark as checked; concept search unavailable
            return
        import faiss
        self._concepts_faiss = faiss.read_index(str(self._concepts_index_path))
        with self._concepts_meta_path.open() as f:
            self._concepts_meta = json.load(f)
        self._concepts_loaded = True
        logger.info("Loaded canonical concept index: %d vectors", self._concepts_faiss.ntotal)

    def _canonical_concept_search(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        result_types: str | list[str] | None = None,
        count: int = 20,
        min_score: float = 0.70,
    ) -> list[CanonicalSearchResult]:
        """Search the canonical concept FAISS index directly (Option C-B).

        Encodes the query with SapBERT and finds nearest canonical anchor by
        patient_friendly_name. No UMLS code intermediary, no crosswalk
        dependency. Supports ``sources`` filtering via metadata and
        ``result_types`` filtering via canonical_id prefix.

        Results below ``min_score`` (default 0.70) are excluded — this prevents
        confidently wrong matches for rare diseases, brand names, and drug
        classes that have no corresponding canonical anchor.
        """
        self._ensure_canonical()
        self._ensure_concepts()
        if self._concepts_faiss is None:
            return []  # concept index not available

        engine = self._ensure_semantic()
        engine._ensure_loaded()
        query_emb = engine._embed(query)

        # Normalize result_types to a set of canonical_id prefixes.
        # result_types="procedure" → {"VAL-PROC-"}
        # result_types=["condition","symptom"] → {"VAL-COND-", "VAL-SYMP-"}
        result_type_prefixes = _result_types_to_prefixes(result_types)

        # Over-fetch to account for source and result_type filtering
        needs_filter = bool(sources or result_type_prefixes)
        k = min(self._concepts_faiss.ntotal, max(count * 5 if needs_filter else count, 50))
        scores, ids = self._concepts_faiss.search(query_emb, k)

        source_set = set(sources) if sources else None
        # Normalize source names. RXNORM ↔ ATC because drug-class queries
        # commonly filter to ["RXNORM"] but expect class anchors (ATC) to be
        # included — without this normalization, "ACE inhibitors" filters out
        # the ATC class anchor and falls back to a specific drug (Perindopril).
        # Same pattern as LNC ↔ LOINC and SNOMEDCT_US ↔ SNOMED.
        if source_set:
            normalized = set()
            for s in source_set:
                su = s.upper()
                normalized.add(su)
                if su == "LNC":
                    normalized.add("LOINC")
                elif su == "LOINC":
                    normalized.add("LNC")
                elif su == "SNOMED":
                    normalized.add("SNOMEDCT_US")
                elif su == "SNOMEDCT_US":
                    normalized.add("SNOMED")
                elif su == "RXNORM":
                    normalized.add("ATC")
                elif su == "ATC":
                    normalized.add("RXNORM")
            source_set = normalized
        results: list[CanonicalSearchResult] = []
        seen_ids: set[str] = set()
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0:
                break
            if score < min_score:
                break  # results are sorted by score; stop at threshold
            m = self._concepts_meta[idx]
            if source_set and m["anchor_system"] not in source_set:
                continue
            cid = m["canonical_id"]
            if result_type_prefixes and not any(cid.startswith(p) for p in result_type_prefixes):
                continue
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            vset = self._canonical_by_id.get(cid, {})
            # Anchor's primary name — what we want to display. For alias hits
            # (m["is_alias"]=True), m["patient_friendly_name"] is the alias text
            # (e.g., "Adbry"); we surface the actual anchor name instead
            # (e.g., "Tralokinumab") so downstream consumers don't see brand and
            # ingredient as two separate concepts. The alias text goes only in
            # matched_via_display.
            primary_name = vset.get("patient_friendly_name") or m["patient_friendly_name"]
            results.append(CanonicalSearchResult(
                canonical_id=cid,
                domain=vset.get("domain", m.get("domain", [])),
                anchor_system=m["anchor_system"],
                anchor_code=m["anchor_code"],
                patient_friendly_name=primary_name,
                score=float(score),
                match_grade="exact" if score > 0.95 else "probable" if score > 0.80 else "possible",
                matched_via_code=f"concept:{m['anchor_code']}",
                matched_via_display=m["patient_friendly_name"],
                total_member_count=self._total_member_count(vset, m),
                members=vset.get("members", []),
                combination_members=vset.get("combination_members", []),
            ))
            if len(results) >= count:
                break
        return results

    def _ancestor_fallback(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
    ) -> list[CanonicalSearchResult]:
        """Ancestor fallback for queries that don't match any canonical anchor.

        Finds the SNOMED code for the query via semantic search, walks UP the
        hierarchy, and returns the nearest canonical ANCHOR (not just any
        member). This handles queries for conditions like "Hashimoto Thyroiditis"
        that aren't canonical anchors but ARE descendants of one (e.g.,
        "Hypothyroidism").

        Uses ``_canonical_by_anchor`` (anchor codes only) rather than
        ``_code_to_canonical_id`` (which includes all descendant members and
        can map to the wrong anchor via overly broad descendant walks).

        Returns results with ``match_grade='broader'`` and discounted score.
        """
        import duckdb

        engine = self._ensure_semantic()
        engine._ensure_loaded()

        # Find SNOMED candidate codes for the query
        sem_results = self.semantic(query, sources=['SNOMEDCT_US'], count=max(count * 3, 10))
        if not sem_results:
            return []
        # QC-148: confidence floor on the semantic seed. The direct concept
        # search above thresholds at 0.70, but this fallback previously took
        # the top SNOMED hit unconditionally — for nonsense input SapBERT
        # still returns a nearest neighbor (~0.4-0.5) whose CHV-bearing
        # ancestors surfaced as sensitive 'broader' anchors (Paraphilia,
        # Sexual Sadism Disorder) at scores as low as 0.20. Gate the ancestor
        # walk on the same 0.70 floor so garbage queries return [] like the
        # concept-index path.
        if sem_results[0].score < 0.70:
            return []

        # Collect unique SNOMED codes to check
        snomed_codes = []
        seen_codes = set()
        for r in sem_results:
            if r.code not in seen_codes:
                seen_codes.add(r.code)
                snomed_codes.append((r.code, r.score))

        # Walk UP ancestors for each candidate via closure table (bulk query).
        # UMLS DB location: prefer MEDTERM4DS_UMLS_DB env var, fall back to
        # default location in medterm4ds/data/.
        db_path = os.environ.get(
            "MEDTERM4DS_UMLS_DB",
            "/mnt/d/medterm4ds/data/umls_2026aa.duckdb",
        )
        try:
            con = duckdb.connect(db_path, read_only=True)
        except Exception:
            return []

        codes_str = ','.join(f"'{c}'" for c, _ in snomed_codes)
        # walk_closure_limited: from_code=descendant, to_code=ancestor
        # Find ancestors of the query codes. For each ancestor, also check if
        # it has a CHV/MLP consumer name — CHV/MLP presence indicates the
        # ancestor is at the right consumer level (not too broad). We prefer
        # CHV-bearing ancestors over non-CHV ones at the same depth.
        anc_rows = con.execute(f"""
            WITH ancestors AS (
                SELECT c.from_code AS query_code, c.to_code AS ancestor_code, c.depth
                FROM mt4ds.walk_closure_limited c
                WHERE c.source = 'SNOMEDCT_US'
                  AND c.from_code IN ({codes_str})
                  AND c.depth <= 5
            ),
            chv_check AS (
                SELECT DISTINCT a.query_code, a.ancestor_code, a.depth,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM mrconso m1
                        JOIN mrconso m2 ON m2.CUI = m1.CUI
                            AND m2.SAB IN ('CHV', 'MEDLINEPLUS')
                            AND m2.SUPPRESS = 'N'
                            AND m2.LAT = 'ENG'
                        WHERE m1.CODE = a.ancestor_code
                          AND m1.SAB = 'SNOMEDCT_US'
                          AND m1.SUPPRESS = 'N'
                    ) THEN 1 ELSE 0 END AS has_chv
                FROM ancestors a
            )
            SELECT query_code, ancestor_code, depth, has_chv
            FROM chv_check
            ORDER BY query_code, has_chv DESC, depth
        """).fetchall()
        con.close()

        # Build: query_code → [(ancestor_code, depth, has_chv), ...] sorted by
        # CHV preference first, then depth. CHV-bearing ancestors are preferred
        # because they're at the right consumer level.
        ancestors_by_code: dict[str, list[tuple[str, int, int]]] = {}
        for q_code, a_code, depth, has_chv in anc_rows:
            ancestors_by_code.setdefault(q_code, []).append((a_code, depth, has_chv))

        # For each query code, find the best ancestor that is a canonical anchor.
        # Prefer CHV-bearing ancestors (has_chv=1) at the shallowest depth.
        # Fall back to non-CHV ancestors only if no CHV-bearing one is an anchor.
        results: list[CanonicalSearchResult] = []
        seen_ids: set[str] = set()
        for q_code, q_score in snomed_codes:
            ancestors = ancestors_by_code.get(q_code, [])
            # Sort: CHV first, then by depth
            chv_ancestors = [(a, d) for a, d, h in ancestors if h == 1]
            non_chv_ancestors = [(a, d) for a, d, h in ancestors if h == 0]

            found = False
            # Try CHV-bearing ancestors first
            for a_code, depth in sorted(chv_ancestors, key=lambda x: x[1]):
                key = ('SNOMEDCT_US', a_code)
                cid = self._canonical_by_anchor.get(key)
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    vset = self._canonical_by_id.get(cid, {})
                    results.append(CanonicalSearchResult(
                        canonical_id=cid,
                        domain=vset.get("domain", []),
                        anchor_system='SNOMEDCT_US',
                        anchor_code=a_code,
                        patient_friendly_name=vset.get("patient_friendly_name", ""),
                        score=q_score * max(0.3, 1.0 - depth * 0.15),
                        match_grade="broader",
                        matched_via_code=f"chv-ancestor:{a_code}",
                        matched_via_display=vset.get("patient_friendly_name", ""),
                        total_member_count=self._total_member_count(vset),
                        members=vset.get("members", []),
                combination_members=vset.get("combination_members", []),
                    ))
                    found = True
                    break

            if not found:
                # Fall back to non-CHV ancestors (broader but still an anchor)
                for a_code, depth in sorted(non_chv_ancestors, key=lambda x: x[1]):
                    key = ('SNOMEDCT_US', a_code)
                    cid = self._canonical_by_anchor.get(key)
                    if cid and cid not in seen_ids:
                        seen_ids.add(cid)
                        vset = self._canonical_by_id.get(cid, {})
                        results.append(CanonicalSearchResult(
                            canonical_id=cid,
                            domain=vset.get("domain", []),
                            anchor_system='SNOMEDCT_US',
                            anchor_code=a_code,
                            patient_friendly_name=vset.get("patient_friendly_name", ""),
                            score=q_score * max(0.2, 1.0 - depth * 0.20),
                            match_grade="broader",
                            matched_via_code=f"ancestor:{a_code}",
                            matched_via_display=vset.get("patient_friendly_name", ""),
                            total_member_count=self._total_member_count(vset),
                            members=vset.get("members", []),
                combination_members=vset.get("combination_members", []),
                        ))
                        break
            if len(results) >= count:
                break

        # Filter by sources if specified
        if sources:
            source_set = set(sources)
            results = [r for r in results if r.anchor_system in source_set]

        return results

    def canonical_batch(
        self,
        queries: list[str],
        *,
        count: int = 5,
        min_score: float = 0.70,
    ) -> list[list[CanonicalSearchResult]]:
        """Batch canonical search — embed all queries in one SapBERT pass.

        Much faster than calling canonical() per query for large query sets
        (e.g., extraction pipelines with 100+ spans). Turns N×100ms into
        ~100ms total for the embedding step.

        Does NOT apply source or result_type filtering — caller post-filters
        per query. Over-fetches (count*3) to leave room for post-filtering.
        """
        _validate_count(count)
        for q in queries:
            _validate_query(q)
        self._ensure_canonical()
        self._ensure_concepts()
        if self._concepts_faiss is None or not queries:
            return [[] for _ in queries]

        engine = self._ensure_semantic()
        engine._ensure_loaded()

        # Batch-embed all queries
        import numpy as np
        import torch
        BATCH_SIZE = 64
        all_embeddings = []
        for i in range(0, len(queries), BATCH_SIZE):
            batch = queries[i:i + BATCH_SIZE]
            inputs = engine._tokenizer(
                batch, return_tensors="pt", truncation=True, max_length=512, padding=True,
            )
            inputs = inputs.to(engine._model.device)
            with torch.no_grad():
                outputs = engine._model(**inputs)
                emb = outputs.last_hidden_state.mean(dim=1)
                emb = torch.nn.functional.normalize(emb, p=2, dim=1)
                all_embeddings.append(emb.cpu().numpy().astype("float32"))
        query_embs = np.vstack(all_embeddings) if all_embeddings else np.zeros((0, 768), dtype="float32")

        # Batch FAISS search (over-fetch to leave room for per-query filtering)
        k = min(self._concepts_faiss.ntotal, max(count * 3, 20))
        scores, ids = self._concepts_faiss.search(query_embs, k)

        # Process results per query
        all_results: list[list[CanonicalSearchResult]] = []
        for q_idx in range(len(queries)):
            results: list[CanonicalSearchResult] = []
            seen_ids: set[str] = set()
            for score, idx in zip(scores[q_idx], ids[q_idx]):
                if idx < 0 or score < min_score:
                    break
                m = self._concepts_meta[idx]
                cid = m["canonical_id"]
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                vset = self._canonical_by_id.get(cid, {})
                primary_name = vset.get("patient_friendly_name") or m["patient_friendly_name"]
                results.append(CanonicalSearchResult(
                    canonical_id=cid,
                    domain=vset.get("domain", m.get("domain", [])),
                    anchor_system=m["anchor_system"],
                    anchor_code=m["anchor_code"],
                    patient_friendly_name=primary_name,
                    score=float(score),
                    match_grade="exact" if score > 0.95 else "probable" if score > 0.80 else "possible",
                    matched_via_code=f"concept:{m['anchor_code']}",
                    matched_via_display=m["patient_friendly_name"],
                    total_member_count=self._total_member_count(vset, m),
                    members=vset.get("members", []),
                    combination_members=vset.get("combination_members", []),
                ))
            all_results.append(results)
        return all_results

    def canonical(
        self,
        query: str,
        *,
        sub_mode: str = "semantic",
        result_types: str | list[str] | None = None,
        sources: list[str] | None = None,
        count: int = 20,
    ) -> list[CanonicalSearchResult]:
        """Search and map top candidate code results to Master Canonical Value Sets.

        Uses the canonical concept FAISS index (Option C-B: direct embedding
        search on canonical anchor names) when available. Falls back to the
        existing semantic/hybrid code-based search + canonical mapping ONLY
        if the concept index file is missing (not installed). When the concept
        index IS available, results below the confidence threshold (0.70)
        return empty — no fallback to the un-thresholded old pipeline.

        Parameters
        ----------
        result_types : str | list[str] | None
            Result-type filter. Filters results by canonical_id prefix, which
            encodes the anchor's clinical type. Useful for SNOMEDCT_US queries
            where the source system contains multiple types (conditions,
            procedures, symptoms, body structures).
            Valid: "condition", "symptom", "lab", "vital", "medication",
            "drug_class", "procedure", "vaccine". Accepts a single string or
            a list (OR semantics within the parameter).
        sources : list[str] | None
            Code-system filter (RXNORM, ATC, LOINC, SNOMEDCT_US, CPT, CVX).
            AND-composed with result_types if both are set.
        """
        _validate_query(query)
        _validate_count(count)
        self._ensure_canonical()
        self._ensure_concepts()

        # Option C-B: concept index is the primary path when available.
        # If it returns empty (all results below 0.70 threshold), that's
        # intentional — don't fall back to the un-thresholded old pipeline.
        if self._concepts_faiss is not None:
            concept_results = self._canonical_concept_search(
                query, sources=sources, result_types=result_types, count=count
            )
            if concept_results:
                return concept_results
            # No direct concept match above threshold — try ancestor fallback.
            # Find the SNOMED code for the query via semantic search, walk UP
            # the hierarchy, and return the nearest canonical anchor as a
            # "broader" match. This gracefully handles queries for conditions
            # that aren't canonical anchors but are descendants of one.
            # Skip the fallback when result_types is set — the caller explicitly
            # asked for specific clinical types, and the broader fallback
            # often lands in a different type (e.g., Syncope with
            # result_types='vital' would fall back to a condition anchor).
            if result_types is None:
                broader = self._ancestor_fallback(query, sources=sources, count=count)
                if broader:
                    return broader
            return []

        # Fallback: old pipeline — ONLY used when concept index file is missing
        # (e.g. fresh install before build_canonical_concept_index.py has run).
        # Retrieve candidate code results using sub_mode ('semantic', 'hybrid', or 'lexical')
        raw_results = self.search(query, mode=sub_mode, sources=sources, count=max(count * 5, 50))
        
        # If sub_mode returned fewer candidates, fall back to semantic search for rich semantic recall
        if len(raw_results) < count and sub_mode != "semantic":
            sem_results = self.semantic(query, sources=sources, count=max(count * 5, 50))
            seen_code_pairs = {(r.source, r.code) for r in raw_results}
            for sr in sem_results:
                if (sr.source, sr.code) not in seen_code_pairs:
                    raw_results.append(sr)

        canonical_results: list[CanonicalSearchResult] = []
        seen_ids: set[str] = set()

        for r in raw_results:
            cid = self._code_to_canonical_id.get((r.source, r.code))
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                vset = self._canonical_by_id[cid]
                canonical_results.append(CanonicalSearchResult(
                    canonical_id=vset["canonical_id"],
                    domain=vset["domain"],
                    anchor_system=vset["anchor_system"],
                    anchor_code=vset["anchor_code"],
                    patient_friendly_name=vset["patient_friendly_name"],
                    score=r.score,
                    match_grade=r.match_grade,
                    matched_via_code=f"{r.source}:{r.code}",
                    matched_via_display=r.display,
                    total_member_count=self._total_member_count(vset),
                    members=vset.get("members", []),
                combination_members=vset.get("combination_members", []),
                ))
                if len(canonical_results) >= count:
                    break

        return canonical_results

    def lexical(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
        result_types: str | list[str] | None = None,
    ) -> list[SearchResult]:
        """BM25 lexical search (~1ms). Token matching with stemming.

        ``result_types`` restricts which category indexes are searched
        (see ``_restrict_categories``) so ``count`` caps the FILTERED set —
        the service-side replacement for the CLI's former post-truncation
        client filter.
        """
        _validate_query(query)
        _validate_count(count)
        self._ensure_bm25()
        categories = self._restrict_categories(
            self._resolve_categories(sources), result_types
        )
        if not categories:
            return []
        # QC-329: fold accents + split on non-alphanumerics so accented
        # queries match the accent-stripped index (see _query_tokens).
        query_tokens = _query_tokens(query)
        if not query_tokens:
            return []
        # QC-124: over-fetch per category when a source filter is set so the
        # filter doesn't drop the top-N slice down to 0 cross-source hits
        # (e.g., 'insulin' sources=['RXNORM'] count=1: the first RXNORM hit
        # ranks #6 in the shared medication index behind 5 SNOMED entries).
        # Mirrors the semantic (count*3) over-fetch with a floor of 10 — the
        # measured first-cross-source rank for tight single-token queries.
        fetch_count = max(count * 3, 10) if sources else count

        results: list[SearchResult] = []
        for category in categories:
            index = self._bm25_indexes.get(category)
            if index is None or not isinstance(index, dict) or "postings" not in index:
                continue

            postings = index["postings"]
            idf = index.get("idf", {})
            doc_lengths = index.get("doc_lengths", [])
            avg_doc_length = index.get("avg_doc_length", 20.0) or 20.0
            rid_to_code = index.get("rid_to_code", [])
            rid_to_friendly = index.get("rid_to_friendly_name", [])
            rid_to_system = index.get("rid_to_system", [])

            scores: dict[int, float] = {}
            for raw_token in query_tokens:
                token = raw_token if raw_token in postings else _stem_token(raw_token)
                if token not in postings:
                    continue
                token_idf = idf.get(token, idf.get(raw_token, 1.0))
                for entry in postings[token]:
                    if not (isinstance(entry, list) and len(entry) >= 2):
                        continue
                    rid, tf = int(entry[0]), float(entry[1])
                    doc_len = doc_lengths[rid] if rid < len(doc_lengths) else avg_doc_length
                    k1, b = 1.5, 0.75
                    tf_comp = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_doc_length))
                    scores[rid] = scores.get(rid, 0.0) + token_idf * tf_comp

            # Hoist the IDF denominator out of the per-result loop — it depends
            # only on query_tokens and idf, both of which are constant across
            # the top-N results. Previously recomputed once per result.
            idf_denom = sum(idf.get(_stem_token(t), idf.get(t, 1.0)) for t in query_tokens) * 2.5 + 0.001
            for rid, score in sorted(scores.items(), key=lambda x: -x[1])[:fetch_count]:
                code = rid_to_code[rid] if rid < len(rid_to_code) else str(rid)
                display = rid_to_friendly[rid] if rid < len(rid_to_friendly) else code
                sys_name = (rid_to_system[rid] if rid < len(rid_to_system) else "").upper()
                source = _SYSTEM_LABELS_REVERSE.get(sys_name, sys_name)
                normalized = min(score / idf_denom, 1.0)
                results.append(SearchResult(
                    code=str(code), source=source, display=display,
                    score=round(normalized, 4), match_grade=_score_to_grade(normalized),
                    category=category,
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return self._filter_by_source(results, sources)[:count]

    def semantic(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
        result_types: str | list[str] | None = None,
    ) -> list[SearchResult]:
        """SapBERT embedding + FAISS ANN search (~100ms on CPU).

        ``result_types`` restricts which category indexes are searched
        (see ``_restrict_categories``); an empty intersection returns []
        WITHOUT calling the engine — ``SemanticSearchEngine.search`` treats
        an empty categories list as "search all".
        """
        _validate_query(query)
        _validate_count(count)
        engine = self._ensure_semantic()
        categories = self._restrict_categories(
            self._resolve_categories(sources), result_types
        )
        if not categories:
            return []
        # Over-fetch when filtering by source so we still have `count` results
        # after the source filter drops cross-source hits (e.g., ICD-10 codes
        # that share a name with a SNOMED concept).
        k = count * 3 if sources else count
        raw = engine.search(query, categories=categories, top_k=k)

        results: list[SearchResult] = []
        for r in raw:
            sys_name = r.get("system", "").upper()
            source = _SYSTEM_LABELS_REVERSE.get(sys_name, sys_name)
            results.append(SearchResult(
                code=r["code"], source=source, display=r["display"],
                score=r["score"], match_grade=r["match_grade"],
                category=r.get("category", ""),
            ))
        return self._filter_by_source(results, sources)[:count]

    def hybrid(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
        result_types: str | list[str] | None = None,
    ) -> list[SearchResult]:
        """BM25 retrieve + SapBERT re-rank (~110ms on CPU).

        The BM25 candidate pool is capped at ``max(50, count)`` — the former
        hard cap of 50 silently truncated hybrid(count=N>50) requests (QC-138).

        ``result_types`` restricts the category indexes searched in both the
        BM25 stage and the semantic-only fallback (see
        ``_restrict_categories``).
        """
        _validate_query(query)
        _validate_count(count)
        self._ensure_bm25()
        engine = self._ensure_semantic()
        categories = self._restrict_categories(
            self._resolve_categories(sources), result_types
        )
        if not categories:
            return []

        # Stage 1: BM25 retrieve
        bm25_results = self.lexical(
            query,
            sources=sources,
            count=min(count * 3, max(50, count)),
            result_types=result_types,
        )
        if not bm25_results:
            # Fall back to semantic-only
            return self.semantic(
                query, sources=sources, count=count, result_types=result_types
            )

        # Stage 2: SapBERT re-rank
        candidates = [{"code": r.code, "system": r.source, "display": r.display} for r in bm25_results]
        reranked = engine.rerank(query, candidates, top_k=count)

        cat_map = {(r.source, r.code): r.category for r in bm25_results}

        results: list[SearchResult] = []
        for r in reranked:
            sys_name = r.get("system", "").upper()
            source = _SYSTEM_LABELS_REVERSE.get(sys_name, sys_name)
            cat = cat_map.get((source, r["code"]), "")
            results.append(SearchResult(
                code=r["code"], source=source, display=r["display"],
                score=r["score"], match_grade=r["match_grade"],
                category=cat,
            ))
        return self._filter_by_source(results, sources)[:count]

    def search(
        self,
        query: str,
        *,
        mode: str = "lexical",
        sources: list[str] | None = None,
        count: int = 20,
        engine=None,
        result_types: str | list[str] | None = None,
    ) -> list[SearchResult] | list[CanonicalSearchResult]:
        """Unified entry point. mode: 'lexical', 'semantic', 'hybrid', or 'canonical'.

        ``engine`` (optional) canonicalizes legacy-mode result displays to the
        engine preferred term (QC-400). Pass it wherever a terminology engine
        is already open — Python facade, MCP, FHIR $search — so all surfaces
        emit ONE display convention for the same result row.

        ``result_types`` filters SERVICE-SIDE in every mode: canonical mode
        filters by canonical_id prefix (``canonical(result_types=...)``);
        legacy modes restrict the category indexes searched
        (``_restrict_categories``). In both cases ``count`` caps the
        FILTERED result set — callers must not truncate before filtering.
        Legacy modes accept only SEARCH_CATEGORIES values; values outside
        that set (e.g. canonical-only "symptom") match nothing.
        """
        if mode == "canonical":
            # Canonical results carry the anchor's patient_friendly_name by
            # design — no preferred-term canonicalization applies.
            return self.canonical(
                query, result_types=result_types, sources=sources, count=count
            )
        if mode == "lexical":
            results = self.lexical(
                query, sources=sources, count=count, result_types=result_types
            )
        elif mode == "semantic":
            results = self.semantic(
                query, sources=sources, count=count, result_types=result_types
            )
        elif mode == "hybrid":
            results = self.hybrid(
                query, sources=sources, count=count, result_types=result_types
            )
        else:
            raise ValueError(
                f"Unknown search mode: {mode}. Use 'lexical', 'semantic', 'hybrid', or 'canonical'."
            )
        if engine is not None and results:
            results = apply_preferred_display(results, engine)
        return results


# Reverse lookup: lowercase system label → internal source name
_SYSTEM_LABELS_REVERSE: dict[str, str] = {
    "snomedct_us": "SNOMEDCT_US",
    "rxnorm": "RXNORM",
    "icd10cm": "ICD10CM",
    "icd10": "ICD10CM",
    "icd10pcs": "ICD10PCS",
    "lnc": "LNC",
    "loinc": "LNC",
    "cpt": "CPT",
    "hcpcs": "HCPCS",
    "cvx": "CVX",
}


# Singleton
_service: SearchService | None = None


def get_search_service() -> SearchService:
    global _service
    if _service is None:
        _service = SearchService(
            search_index_dir=os.getenv("MEDTERM4DS_SEARCH_INDEX_DIR", DEFAULT_SEARCH_INDEX_DIR),
            embedding_model_dir=os.getenv("MEDTERM4DS_EMBEDDING_MODEL_DIR", DEFAULT_EMBEDDING_MODEL_DIR),
            canonical_path=os.getenv("MEDTERM4DS_CANONICAL_PATH", DEFAULT_CANONICAL_VALUE_SETS_PATH),
            canonical_concepts_index=os.getenv("MEDTERM4DS_CANONICAL_INDEX", DEFAULT_CANONICAL_CONCEPTS_INDEX),
            canonical_concepts_meta=os.getenv("MEDTERM4DS_CANONICAL_META", DEFAULT_CANONICAL_CONCEPTS_META),
        )
    return _service


def configure_search_service(
    *,
    search_index_dir: str | None = None,
    embedding_model_dir: str | None = None,
) -> SearchService:
    """Replace the singleton with a new instance configured with the given dirs.

    Used by apps that need to override the env-var defaults at startup (e.g.
    the FHIR server, which reads MEDTERM4DS_SEARCH_INDEX_DIR from its own
    settings dataclass). Also useful for tests that need an isolated service.
    """
    global _service
    _service = SearchService(
        search_index_dir=search_index_dir or os.getenv("MEDTERM4DS_SEARCH_INDEX_DIR", DEFAULT_SEARCH_INDEX_DIR),
        embedding_model_dir=embedding_model_dir or os.getenv("MEDTERM4DS_EMBEDDING_MODEL_DIR", DEFAULT_EMBEDDING_MODEL_DIR),
    )
    return _service


def reset_search_service() -> None:
    """Drop the singleton. Next get_search_service() call will create a fresh
    instance with current env-var defaults. Used between tests."""
    global _service
    _service = None


def search(
    query: str,
    *,
    mode: str = "lexical",
    sources: list[str] | None = None,
    count: int = 20,
    engine=None,
    result_types: str | list[str] | None = None,
) -> list[SearchResult]:
    """Search for medical codes by free text.

    Parameters
    ----------
    query : Free text to search for (e.g., "high blood sugar").
    mode : 'lexical' (BM25, ~1ms), 'semantic' (SapBERT, ~100ms), or 'hybrid' (cascade, ~110ms).
    sources : Restrict to specific source systems (e.g., ["SNOMEDCT_US"]). None = all.
    count : Maximum results to return.
    engine : Optional terminology engine. When provided, legacy-mode result
        displays are canonicalized to the engine preferred term (QC-400) so
        all surfaces emit one display convention for the same result.
    result_types : Filter by clinical result type, applied SERVICE-SIDE so
        ``count`` caps the filtered set (canonical mode filters by
        canonical_id prefix; legacy modes restrict the category indexes
        searched). Legacy modes accept only SEARCH_CATEGORIES values.

    Returns
    -------
    list[SearchResult]
        Ranked results with score and match_grade (certain/probable/possible).
    """
    if sources is not None:
        # QC-425 (MEDIUM): an all-empty source filter (e.g. sources=[''])
        # previously returned silent 0-row success on the BM25 path while
        # the LIKE sibling (search_names) WIDENED and source_stats rejected —
        # one input, three behaviors. Reject, matching get_source_stats'
        # QC-395 guard; ``sources=None`` still means all.
        if isinstance(sources, (str, bytes)) or not isinstance(sources, (list, tuple)):
            raise TypeError(
                "sources must be a list of vocabulary names, got "
                f"{type(sources).__name__}"
            )
        if not [s for s in sources if isinstance(s, str) and s.strip()]:
            raise ValueError(
                "sources must contain at least one non-empty vocabulary name "
                "(e.g. SNOMEDCT_US) when provided."
            )
        # QC-424 note: unknown-source presence for BM25/semantic modes is NOT
        # validated here — those modes read the search indexes, not the
        # terminology tables, so a DB probe is the wrong instrument. The LIKE
    # path (search_names) presence-checks in the engine.
    return get_search_service().search(
        query, mode=mode, sources=sources, count=count, engine=engine,
        result_types=result_types,
    )
