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

    def __init__(
        self,
        model_dir: str = DEFAULT_MODEL_DIR,
        device: str | None = None,
        index_dir: str | None = None,
    ):
        self._model_dir = Path(model_dir)
        # Split-layout (Phase 3): model weights live under models/<space_id>/
        # while per-category FAISS indexes may live in another dir (the
        # legacy semantic/ dir). None → indexes are looked up next to the
        # model (the co-located legacy layout).
        self._index_dir = Path(index_dir) if index_dir else None
        self._device_param = device
        self._device = "cpu"
        self._lock = threading.Lock()
        self._model = None
        self._tokenizer = None
        self._space_id: str | None = None
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

    @property
    def space_id(self) -> str | None:
        """Embedding-space id from the model manifest (None until loaded
        or when the layout carries no manifest)."""
        return self._space_id

    @property
    def _index_root(self) -> Path:
        """Directory holding the per-category FAISS indexes."""
        return self._index_dir or self._model_dir

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

            # Artifact-governance gate (docs/plans/artifact-governance-plan.md
            # §5): manifest validation at COMPONENT LOAD, never import time.
            # Absent manifests (legacy revision-keyed layout / operator dirs)
            # keep today's semantics — Phase 2 dual-publish adds them.
            from medterm4ds.core.artifact_manifest import (
                manifest_str,
                read_manifest,
                validate_index_lineage,
                validate_model_manifest,
            )
            manifest = read_manifest(self._model_dir)
            if manifest is not None:
                self._space_id = validate_model_manifest(
                    manifest, source=str(self._model_dir)
                )
                # Dual-edge check: the per-category FAISS indexes must come
                # from a lineage matching the serving space. Indexes may be
                # co-located with the model (legacy layout) or live in a
                # separate dir (split layout reusing legacy indexes). Only
                # manifests that CARRY an index_lineage block are checked —
                # the split models/<space>/ manifest intentionally omits it
                # (lineage lives in the data manifest).
                if isinstance(manifest.get("index_lineage"), dict):
                    for cat in _CATEGORIES:
                        index_path = self._index_root / f"{cat}_faiss.index"
                        if index_path.exists():
                            validate_index_lineage(
                                manifest,
                                self._index_root,
                                serving_space_id=self._space_id,
                                source=f"{self._index_root} ({cat} index)",
                            )
                            break
                logger.info(
                    "SapBERT manifest validated: %s", manifest_str(manifest)
                )
            else:
                self._space_id = None

            import torch
            from transformers import AutoModel, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(str(self._model_dir))
            self._model = AutoModel.from_pretrained(str(self._model_dir))
            self._model.eval()
            # GPU/MPS placement (MEDTERM4DS_DEVICE, default auto-detect).
            from medterm4ds.core.env import resolve_device
            self._device = resolve_device(self._device_param)
            if self._device != "cpu":
                self._model = self._model.to(self._device)
            logger.info("SapBERT model loaded (768-dim embeddings, device=%s)",
                        self._device)

            # Load FAISS indexes + metadata per category
            import faiss
            import numpy as np  # noqa: F401 — needed by faiss

            for cat in _CATEGORIES:
                index_path = self._index_root / f"{cat}_faiss.index"
                meta_path = self._index_root / f"{cat}_metadata.json"
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
        inputs = inputs.to(self._model.device)
        with torch.no_grad():
            outputs = self._model(**inputs)
        # Mean pooling over token embeddings
        embedding = outputs.last_hidden_state.mean(dim=1)
        embedding = torch.nn.functional.normalize(embedding, p=2, dim=1)
        return embedding.cpu().numpy().astype("float32")

    def embed_batch(self, texts: list[str]) -> Any:
        """Embed query texts with SapBERT (768-dim, L2-normalized), batched.

        Public API for service-layer batch embedding (ARCH-001): services
        must not reach into _tokenizer/_model internals to build batched
        inputs. Batch size comes from MEDTERM4DS_EMBED_BATCH_SIZE
        (default 64). Returns a (len(texts), 768) float32 array; empty
        input returns an empty (0, 768) array.
        """
        import numpy as np
        import torch

        from medterm4ds.core.env import env_int

        self._ensure_loaded()
        if not texts:
            return np.zeros((0, 768), dtype="float32")

        batch_size = env_int("MEDTERM4DS_EMBED_BATCH_SIZE", minimum=1) or 64
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            inputs = self._tokenizer(
                batch, return_tensors="pt", truncation=True, max_length=512,
                padding=True,
            )
            inputs = inputs.to(self._model.device)
            with torch.no_grad():
                outputs = self._model(**inputs)
                emb = outputs.last_hidden_state.mean(dim=1)
                emb = torch.nn.functional.normalize(emb, p=2, dim=1)
                all_embeddings.append(emb.cpu().numpy().astype("float32"))
        return np.vstack(all_embeddings)

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
                    index_dir = os.getenv("MEDTERM4DS_SEMANTIC_INDEX_DIR") or None
                    _engine_instance = SemanticSearchEngine(model_dir, index_dir=index_dir)
    return _engine_instance
