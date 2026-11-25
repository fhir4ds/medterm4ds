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
