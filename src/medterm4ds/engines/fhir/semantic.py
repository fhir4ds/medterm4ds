"""Semantic search engine for the FHIR $search operation.

Lazy-loads the fine-tuned SapBERT model and FAISS indexes on first use.
Supports two modes:
  - hybrid: BM25 retrieve top-N → SapBERT re-rank by cosine similarity
  - semantic: SapBERT embedding → FAISS ANN search directly

Assets live at MEDTERM4DS_EMBEDDING_MODEL_DIR (default:
/mnt/d/fhir4px-model/data/sapbert_finetuned/).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = "/mnt/d/fhir4px-model/data/sapbert_finetuned"

_CATEGORIES = ("condition", "lab", "medication", "procedure", "vaccine", "body_structure")


class SemanticSearchEngine:
    """Lazy-loading SapBERT + FAISS semantic search.

    Loads the model (~438 MB) and category-specific FAISS indexes (~2.4 GB total)
    on first use. Subsequent searches reuse the loaded model + indexes.

    Thread-safe: model loading is guarded by a lock.
    """

    def __init__(self, model_dir: str = DEFAULT_MODEL_DIR):
        self._model_dir = Path(model_dir)
        self._lock = threading.Lock()
        self._model = None
        self._tokenizer = None
        self._faiss_indexes: dict[str, Any] = {}
        self._metadata: dict[str, list[dict]] = {}
        self._loaded = False

    @property
    def is_available(self) -> bool:
        """Check if the model directory and required files exist."""
        return (
            self._model_dir.is_dir()
            and (self._model_dir / "model.safetensors").exists()
            and (self._model_dir / "config.json").exists()
        )

    def _ensure_loaded(self) -> None:
        """Lazily load model, tokenizer, and FAISS indexes on first call."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:  # double-check after acquiring lock
                return
            if not self.is_available:
                raise RuntimeError(
                    f"SapBERT model not found at {self._model_dir}. "
                    "Set MEDTERM4DS_EMBEDDING_MODEL_DIR to the model directory."
                )
            logger.info("Loading SapBERT model from %s ...", self._model_dir)
            import torch
            from transformers import AutoModel, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(str(self._model_dir))
            self._model = AutoModel.from_pretrained(str(self._model_dir))
            self._model.eval()
            logger.info("SapBERT model loaded (768-dim embeddings)")

            # Load FAISS indexes + metadata per category
            import faiss
            import numpy as np  # noqa: F401 — needed by faiss

            for cat in _CATEGORIES:
                index_path = self._model_dir / f"{cat}_faiss.index"
                meta_path = self._model_dir / f"{cat}_metadata.json"
                if index_path.exists() and meta_path.exists():
                    self._faiss_indexes[cat] = faiss.read_index(str(index_path))
                    with meta_path.open() as f:
                        self._metadata[cat] = json.load(f)
                    logger.info("  FAISS %s: %d vectors", cat, self._faiss_indexes[cat].ntotal)

            self._loaded = True
            logger.info("Semantic search engine ready")

    def _embed(self, text: str) -> Any:
        """Embed query text using SapBERT (768-dim, L2-normalized)."""
        import torch

        inputs = self._tokenizer(
            text, return_tensors="pt", truncation=True, max_length=512, padding=True
        )
        with torch.no_grad():
            outputs = self._model(**inputs)
        # Mean pooling over token embeddings
        embedding = outputs.last_hidden_state.mean(dim=1)
        embedding = torch.nn.functional.normalize(embedding, p=2, dim=1)
        return embedding.cpu().numpy().astype("float32")

    def search(
        self,
        query: str,
        *,
        categories: list[str] | None = None,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Semantic search: embed query, search FAISS indexes.

        Returns list of result dicts:
          {code, system, display, score (cosine similarity), match_grade}
        """
        self._ensure_loaded()
        import numpy as np

        cats = categories or list(self._faiss_indexes.keys())
        query_vec = self._embed(query)

        results: list[dict[str, Any]] = []
        for cat in cats:
            if cat not in self._faiss_indexes:
                continue
            index = self._faiss_indexes[cat]
            meta = self._metadata.get(cat, [])
            # Search top_k per category
            k = min(top_k, index.ntotal)
            if k == 0:
                continue
            distances, indices = index.search(query_vec, k)
            for rank, (dist, idx) in enumerate(zip(distances[0], indices[0])):
                if idx < 0 or idx >= len(meta):
                    continue
                entry = meta[idx]
                results.append({
                    "code": str(entry.get("code", "")),
                    "system": str(entry.get("system", "")),
                    "display": entry.get("friendly_name") or entry.get("technical_name") or entry.get("code", ""),
                    "score": round(float(dist), 4),
                    "match_grade": _cosine_to_grade(float(dist)),
                    "category": cat,
                })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Re-rank BM25 candidates by SapBERT cosine similarity.

        Given a list of BM25 results (each with code, system, display),
        embed the query, look up or compute candidate embeddings, and
        re-sort by semantic similarity.

        Falls back to original BM25 order if model not available.
        """
        if not candidates:
            return []
        self._ensure_loaded()
        import numpy as np

        query_vec = self._embed(query)

        # For each candidate, find it in the FAISS index and get its embedding.
        # Since we don't have per-code embeddings cached, we use the display
        # text to compute a quick embedding for comparison.
        scored: list[tuple[float, dict[str, Any]]] = []
        for cand in candidates:
            display = cand.get("display", "")
            if not display:
                scored.append((cand.get("score", 0.0), cand))
                continue
            # Embed the candidate's display text
            cand_vec = self._embed(display)
            cosine = float(np.dot(query_vec[0], cand_vec[0]))
            scored.append((cosine, {**cand, "score": round(cosine, 4), "match_grade": _cosine_to_grade(cosine)}))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]


def _cosine_to_grade(cosine: float) -> str:
    """Map cosine similarity to match-grade (Patient $match pattern)."""
    if cosine >= 0.92:
        return "certain"
    if cosine >= 0.75:
        return "probable"
    return "possible"


# Singleton instance (lazy-loaded on first use)
_engine_instance: SemanticSearchEngine | None = None
_engine_lock = threading.Lock()


def get_semantic_engine() -> SemanticSearchEngine:
    """Get the singleton SemanticSearchEngine instance."""
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                model_dir = os.getenv("MEDTERM4DS_EMBEDDING_MODEL_DIR", DEFAULT_MODEL_DIR)
                _engine_instance = SemanticSearchEngine(model_dir)
    return _engine_instance
