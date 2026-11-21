"""Thread-safety of native inference seams (FHIR4-22).

Root cause reproduced in the wild and locally: the HF fast tokenizer and
the torch forward pass are not safe to invoke concurrently on one
SemanticSearchEngine instance. Under the fhir_api thread pools (and any
in-process consumer with threads), concurrent calls corrupt native heap
state, surfacing as transient TypeErrors in unrelated pure-Python frames
('int' object is not callable at search.py:_surface_distance) or
segfaults. These tests pin the serialization contract.
"""

from __future__ import annotations

import threading
import time

import pytest

from medterm4ds.engines.fhir.semantic import SemanticSearchEngine


class _ConcurrencyProbe:
    """Records peak concurrent entries across all hooked callables."""

    def __init__(self) -> None:
        self.current = 0
        self.peak = 0
        self._lock = threading.Lock()

    def enter(self) -> None:
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def exit(self) -> None:
        with self._lock:
            self.current -= 1


class _FakeTensor:
    def __init__(self, probe: _ConcurrencyProbe, hold: float) -> None:
        self._probe = probe
        self._hold = hold

    def mean(self, dim=1):  # noqa: ANN001, ANN201 — torch-callable shape
        return self

    def cpu(self):  # noqa: ANN201
        return self

    def numpy(self):  # noqa: ANN201
        return self

    def astype(self, dtype):  # noqa: ANN001, ANN201
        return self


class _FakeInputs(_FakeTensor):
    def to(self, device):  # noqa: ANN001, ANN201
        return self


class _FakeOutput(_FakeTensor):
    @property
    def last_hidden_state(self):  # noqa: ANN201
        return _FakeTensor(self._probe, self._hold)


class _FakeTokenizer:
    def __init__(self, probe: _ConcurrencyProbe, hold: float) -> None:
        self._probe = probe
        self._hold = hold

    def __call__(self, text, **kwargs):  # noqa: ANN001, ANN201
        self._probe.enter()
        time.sleep(self._hold)
        self._probe.exit()
        return _FakeInputs(self._probe, self._hold)


class _FakeModel:
    def __init__(self, probe: _ConcurrencyProbe, hold: float) -> None:
        self._probe = probe
        self._hold = hold
        self.device = "cpu"

    def __call__(self, **kwargs):  # noqa: ANN003, ANN201
        self._probe.enter()
        time.sleep(self._hold)
        self._probe.exit()
        return _FakeOutput(self._probe, self._hold)


def _loaded_engine(hold: float = 0.05) -> tuple[SemanticSearchEngine, _ConcurrencyProbe]:
    engine = SemanticSearchEngine(model_dir="/nonexistent")
    probe = _ConcurrencyProbe()
    engine._tokenizer = _FakeTokenizer(probe, hold)  # noqa: SLF001 — test seam
    engine._model = _FakeModel(probe, hold)  # noqa: SLF001 — test seam
    engine._loaded = True  # noqa: SLF001 — bypass disk layout
    return engine, probe


def test_embed_serializes_concurrent_tokenizer_and_forward() -> None:
    """Concurrent _embed calls must not overlap in tokenizer/forward."""
    engine, probe = _loaded_engine()
    start = threading.Barrier(8)

    def worker() -> None:
        start.wait()
        engine._embed("query")  # noqa: SLF001 — the seam under contract

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert probe.peak == 1, (
        f"tokenizer/forward overlapped (peak={probe.peak}): concurrent "
        "native inference corrupts heap state (FHIR4-22)"
    )


def test_embed_batch_serializes_concurrent_batches() -> None:
    """Concurrent embed_batch calls must not overlap in tokenizer/forward."""
    engine, probe = _loaded_engine()
    start = threading.Barrier(4)

    def worker() -> None:
        start.wait()
        engine.embed_batch(["a", "b", "c"])

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert probe.peak == 1, (
        f"tokenizer/forward overlapped (peak={probe.peak}): concurrent "
        "native inference corrupts heap state (FHIR4-22)"
    )


def test_ensure_canonical_loads_exactly_once_under_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent _ensure_canonical must run the load body exactly once.

    CR2-001: the associations $translate path added a concurrent consumer
    of the canonical index (code_to_canonical_id) alongside the search /
    lookup paths; an unlocked lazy init double-loads and can expose a
    partially-built index (transient result=false empty).
    """
    from medterm4ds.services.search import SearchService

    service = SearchService.__new__(SearchService)
    service._canonical_loaded = False
    service._ensure_lock = threading.Lock()
    calls = []

    def fake_load(service_self) -> None:  # noqa: ANN001, ANN202
        if service_self._canonical_loaded:  # double-check under the lock
            return
        calls.append(threading.get_ident())
        time.sleep(0.05)
        service_self._canonical_loaded = True

    monkeypatch.setattr(SearchService, "_ensure_canonical_locked", fake_load)
    start = threading.Barrier(8)

    def worker() -> None:
        start.wait()
        service._ensure_canonical()  # noqa: SLF001 — the seam under contract

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(calls) == 1, f"canonical load body ran {len(calls)}x (expected exactly 1)"


def test_ensure_bm25_loads_exactly_once_under_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent _ensure_bm25 must run the load body exactly once."""
    from medterm4ds.services.search import SearchService

    service = SearchService.__new__(SearchService)  # skip __init__ env resolution
    service._bm25_loaded = False  # noqa: SLF001 — test seam
    service._ensure_lock = threading.Lock()  # noqa: SLF001 — test seam
    service._bm25_indexes = {}  # noqa: SLF001 — test seam
    calls = []

    def fake_load(service_self) -> None:  # noqa: ANN001, ANN202
        if service_self._bm25_loaded:  # double-check under the lock (real body's first step)
            return
        calls.append(threading.get_ident())
        time.sleep(0.05)
        service_self._bm25_loaded = True

    monkeypatch.setattr(SearchService, "_ensure_bm25_locked", fake_load)
    start = threading.Barrier(8)

    def worker() -> None:
        start.wait()
        service._ensure_bm25()  # noqa: SLF001 — the seam under contract

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(calls) == 1, f"BM25 load body ran {len(calls)}x (expected exactly 1)"
