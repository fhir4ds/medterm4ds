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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from medterm4ds.core.models import CodeRef

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_INDEX_DIR = "/mnt/d/fhir4px-model/dist/naming_bm25"
DEFAULT_EMBEDDING_MODEL_DIR = "/mnt/d/fhir4px-model/data/sapbert_finetuned"
SEARCH_CATEGORIES = ("condition", "lab", "medication", "procedure", "vaccine", "body_structure")

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
    "SNOMEDCT_US": "snomedct_us",
    "RXNORM": "rxnorm",
    "ICD10CM": "icd10",
    "ICD10PCS": "icd10pcs",
    "LNC": "lnc",
    "CPT": "cpt",
    "HCPCS": "hcpcs",
    "CVX": "cvx",
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


def _score_to_grade(score: float) -> str:
    if score >= 0.8:
        return "certain"
    if score >= 0.4:
        return "probable"
    return "possible"


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
    ):
        self._bm25_dir = Path(search_index_dir)
        self._model_dir = Path(embedding_model_dir)
        self._bm25_indexes: dict[str, dict] = {}
        self._semantic_engine = None
        self._bm25_loaded = False

    @property
    def lexical_available(self) -> bool:
        return self._bm25_loaded or (self._bm25_dir.is_dir() and any(self._bm25_dir.glob("*_bm25.json")))

    @property
    def semantic_available(self) -> bool:
        return (self._model_dir / "model.safetensors").exists()

    def _ensure_bm25(self) -> None:
        if self._bm25_loaded:
            return
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
            raise RuntimeError(f"SapBERT model not found at {self._model_dir}")
        from medterm4ds.engines.fhir.semantic import SemanticSearchEngine
        self._semantic_engine = SemanticSearchEngine(str(self._model_dir))
        return self._semantic_engine

    def _resolve_categories(self, sources: list[str] | None) -> list[str]:
        if sources is None:
            return list(SEARCH_CATEGORIES)
        cats: list[str] = []
        for source in sources:
            source_upper = source.upper()
            cats.extend(_SOURCE_TO_CATEGORIES.get(source_upper, []))
        return list(dict.fromkeys(cats))  # dedupe, preserve order

    def lexical(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
    ) -> list[SearchResult]:
        """BM25 lexical search (~1ms). Token matching with stemming."""
        self._ensure_bm25()
        categories = self._resolve_categories(sources)
        query_tokens = query.lower().split()
        if not query_tokens:
            return []

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

            for rid, score in sorted(scores.items(), key=lambda x: -x[1])[:count]:
                code = rid_to_code[rid] if rid < len(rid_to_code) else str(rid)
                display = rid_to_friendly[rid] if rid < len(rid_to_friendly) else code
                sys_name = (rid_to_system[rid] if rid < len(rid_to_system) else "").upper()
                source = _SYSTEM_LABELS_REVERSE.get(sys_name, sys_name)
                normalized = min(score / (sum(idf.get(_stem_token(t), idf.get(t, 1.0)) for t in query_tokens) * 2.5 + 0.001), 1.0)
                results.append(SearchResult(
                    code=str(code), source=source, display=display,
                    score=round(normalized, 4), match_grade=_score_to_grade(normalized),
                    category=category,
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:count]

    def semantic(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
    ) -> list[SearchResult]:
        """SapBERT embedding + FAISS ANN search (~100ms on CPU)."""
        engine = self._ensure_semantic()
        categories = self._resolve_categories(sources)
        raw = engine.search(query, categories=categories, top_k=count)

        results: list[SearchResult] = []
        for r in raw:
            sys_name = r.get("system", "").upper()
            source = _SYSTEM_LABELS_REVERSE.get(sys_name, sys_name)
            results.append(SearchResult(
                code=r["code"], source=source, display=r["display"],
                score=r["score"], match_grade=r["match_grade"],
                category=r.get("category", ""),
            ))
        return results

    def hybrid(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
    ) -> list[SearchResult]:
        """BM25 retrieve + SapBERT re-rank (~110ms on CPU)."""
        self._ensure_bm25()
        engine = self._ensure_semantic()
        categories = self._resolve_categories(sources)

        # Stage 1: BM25 retrieve
        bm25_results = self.lexical(query, sources=sources, count=min(count * 3, 50))
        if not bm25_results:
            # Fall back to semantic-only
            return self.semantic(query, sources=sources, count=count)

        # Stage 2: SapBERT re-rank
        candidates = [{"code": r.code, "system": r.source, "display": r.display} for r in bm25_results]
        reranked = engine.rerank(query, candidates, top_k=count)

        results: list[SearchResult] = []
        for r in reranked:
            sys_name = r.get("system", "").upper()
            source = _SYSTEM_LABELS_REVERSE.get(sys_name, sys_name)
            results.append(SearchResult(
                code=r["code"], source=source, display=r["display"],
                score=r["score"], match_grade=r["match_grade"],
            ))
        return results

    def search(
        self,
        query: str,
        *,
        mode: str = "lexical",
        sources: list[str] | None = None,
        count: int = 20,
    ) -> list[SearchResult]:
        """Unified entry point. mode: 'lexical', 'semantic', or 'hybrid'."""
        if mode == "lexical":
            return self.lexical(query, sources=sources, count=count)
        if mode == "semantic":
            return self.semantic(query, sources=sources, count=count)
        if mode == "hybrid":
            return self.hybrid(query, sources=sources, count=count)
        raise ValueError(f"Unknown search mode: {mode}. Use 'lexical', 'semantic', or 'hybrid'.")


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
) -> list[SearchResult]:
    """Search for medical codes by free text.

    Parameters
    ----------
    query : Free text to search for (e.g., "high blood sugar").
    mode : 'lexical' (BM25, ~1ms), 'semantic' (SapBERT, ~100ms), or 'hybrid' (cascade, ~110ms).
    sources : Restrict to specific source systems (e.g., ["SNOMEDCT_US"]). None = all.
    count : Maximum results to return.

    Returns
    -------
    list[SearchResult]
        Ranked results with score and match_grade (certain/probable/possible).
    """
    return get_search_service().search(query, mode=mode, sources=sources, count=count)
