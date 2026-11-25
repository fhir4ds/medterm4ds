"""Tests for native thread-pool pinning (core.runtime.apply_thread_pinning).

FHIR4-51: default torch/faiss OpenMP pools race DuckDB workers and
corrupt the process on WSL2 (SIGSEGV + impossible pure-Python type
errors). The library must pin its own pools unless explicitly told not
to, via MEDTERM4DS_NATIVE_THREADS.
"""

from __future__ import annotations

import importlib

import pytest

import medterm4ds.core.runtime as runtime


@pytest.fixture(autouse=True)
def _reset_latch():
    runtime._applied = None
    yield
    runtime._applied = None


def test_default_pins_to_one(monkeypatch):
    monkeypatch.delenv("MEDTERM4DS_NATIVE_THREADS", raising=False)
    for var in runtime._FORWARDED_VARS:
        monkeypatch.delenv(var, raising=False)
    assert runtime.apply_thread_pinning() == 1
    # Default pin stays contained: no env-var forwarding.
    import os

    for var in runtime._FORWARDED_VARS:
        assert os.environ.get(var) is None


def test_explicit_value_is_applied_and_forwarded(monkeypatch):
    monkeypatch.setenv("MEDTERM4DS_NATIVE_THREADS", "2")
    assert runtime.apply_thread_pinning() == 2
    import os

    for var in runtime._FORWARDED_VARS:
        assert os.environ[var] == "2"
    try:
        import torch

        assert torch.get_num_threads() == 2
    except ImportError:
        pytest.skip("torch not installed")


def test_zero_disables_pinning(monkeypatch):
    monkeypatch.setenv("MEDTERM4DS_NATIVE_THREADS", "0")
    assert runtime.apply_thread_pinning() is None


def test_invalid_value_raises_named_error(monkeypatch):
    monkeypatch.setenv("MEDTERM4DS_NATIVE_THREADS", "abc")
    with pytest.raises(ValueError, match="MEDTERM4DS_NATIVE_THREADS"):
        runtime.apply_thread_pinning()


def test_latched_subsequent_calls_do_not_reapply(monkeypatch):
    monkeypatch.delenv("MEDTERM4DS_NATIVE_THREADS", raising=False)
    assert runtime.apply_thread_pinning() == 1
    # A later env change must not re-pin: engines call this on every
    # load and the pin is a process-wide one-shot.
    monkeypatch.setenv("MEDTERM4DS_NATIVE_THREADS", "8")
    assert runtime.apply_thread_pinning() == 1


def test_module_reimport_does_not_crash():
    importlib.reload(runtime)


def test_duplicate_omp_detection_finds_both_when_present():
    """After loading torch+faiss (the ML stack), >=2 runtimes -> detected.

    Skips silently on hosts without both libs; on the standard pip
    install (torch libgomp + faiss renamed libgomp) it must fire — this
    is the structural FHIR4-51 round-4 finding.
    """
    try:
        import faiss  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        pytest.skip("torch/faiss not installed")
    runtimes = runtime.detect_duplicate_omp_runtimes()
    assert len(runtimes) >= 2, f"expected duplicate OMP runtimes, got {runtimes}"


def test_warn_duplicate_omp_runtimes(caplog):
    runtime.warn_duplicate_omp_runtimes()  # must never raise
    assert True


def test_native_thread_count_no_ml_import(monkeypatch):
    """DuckDB-only paths must resolve the pin without importing torch."""
    monkeypatch.setenv("MEDTERM4DS_NATIVE_THREADS", "3")
    import sys

    sys.modules.pop("torch", None)
    assert runtime.native_thread_count() == 3
    assert "torch" not in sys.modules


def test_duckdb_thread_config_default_and_disabled(monkeypatch):
    monkeypatch.delenv("MEDTERM4DS_NATIVE_THREADS", raising=False)
    assert runtime.duckdb_thread_config() == {"threads": 1}
    monkeypatch.setenv("MEDTERM4DS_NATIVE_THREADS", "0")
    assert runtime.duckdb_thread_config() is None


def test_ancestor_fallback_pins_duckdb_pool(monkeypatch, tmp_path):
    """The hybrid path's ancestor-fallback DuckDB connection must be sized.

    FHIR4-51 round 2: this per-query connection was opened with no
    config, inheriting DuckDB's ambient default (all cores) — an
    invisible C++ pool racing the pinned ML stacks inside the hybrid
    loop, prime suspect for the null-call SIGSEGV.
    """
    from types import SimpleNamespace

    import medterm4ds.services.search as search_mod

    svc = search_mod.SearchService.__new__(search_mod.SearchService)  # bypass init

    def fake_semantic(query, sources=None, count=20):
        return [SimpleNamespace(code="1234", score=0.9)]

    svc.semantic = fake_semantic  # type: ignore[method-assign]
    svc._ensure_semantic = lambda: SimpleNamespace(
        _ensure_loaded=lambda: None
    )
    monkeypatch.setenv("MEDTERM4DS_UMLS_DB", str(tmp_path / "umls.duckdb"))
    monkeypatch.delenv("MEDTERM4DS_NATIVE_THREADS", raising=False)

    captured: dict = {}

    class _FakeCon:
        def execute(self, *a, **k):
            captured.setdefault("calls", []).append(a)
            return SimpleNamespace(fetchall=lambda: [])

        def close(self):
            pass

    def fake_connect(path, read_only=None, config=None):
        captured["config"] = config
        return _FakeCon()

    monkeypatch.setattr("duckdb.connect", fake_connect)
    result = svc._ancestor_fallback("query")
    assert result == []
    assert captured["config"] == {"threads": 1}


def test_client_connect_pins_default_threads(tmp_path, monkeypatch):
    """mt.connect() with a threads=None profile must honor the pin.

    FHIR4-51 round 2: the library's own engine connection inherited
    DuckDB's ambient default (all cores) and raced the pinned ML pools.
    An explicit config.threads stays authoritative.
    """
    import duckdb

    import medterm4ds as mt

    db = tmp_path / "lib.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t AS SELECT 42 AS x")
    con.close()

    monkeypatch.delenv("MEDTERM4DS_NATIVE_THREADS", raising=False)
    with mt.connect(str(db), memory_profile="balanced") as t:
        threads = t._connection.execute(  # noqa: SLF001 — seam under test
            "SELECT current_setting('threads')"
        ).fetchone()[0]
        assert int(threads) == 1


def test_open_duckdb_engine_passes_threads_at_connect(tmp_path, monkeypatch):
    """The connect kwarg itself must carry the pin (FHIR4-51 r3).

    The post-connect PRAGMA pins the pool but lets duckdb.connect()
    briefly spin it at the ambient default; host instrumentation of
    duckdb.connect saw config=None. The pin must be visible at the
    duckdb.connect call site.
    """
    import duckdb

    from medterm4ds.client import open_duckdb_engine

    db = tmp_path / "kw.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t AS SELECT 1 AS x")
    con.close()

    captured: dict = {}
    real_connect = duckdb.connect

    def spy_connect(path, **kwargs):
        captured.update(kwargs)
        return real_connect(path, **kwargs)

    monkeypatch.setattr("duckdb.connect", spy_connect)
    monkeypatch.delenv("MEDTERM4DS_NATIVE_THREADS", raising=False)
    open_duckdb_engine(str(db), read_only=True)
    assert captured.get("config") == {"threads": 1}
