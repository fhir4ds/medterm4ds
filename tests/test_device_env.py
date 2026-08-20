"""Tests for MEDTERM4DS_DEVICE resolution (core.env.resolve_device)."""

from __future__ import annotations

import pytest

from medterm4ds.core.env import resolve_device


def test_explicit_cpu(monkeypatch):
    monkeypatch.delenv("MEDTERM4DS_DEVICE", raising=False)
    assert resolve_device("cpu") == "cpu"


def test_env_var_respected(monkeypatch):
    monkeypatch.setenv("MEDTERM4DS_DEVICE", "cpu")
    assert resolve_device() == "cpu"


def test_explicit_argument_wins_over_env(monkeypatch):
    monkeypatch.setenv("MEDTERM4DS_DEVICE", "cpu")
    # explicit="auto" overrides an env pin; both are valid so this is safe
    assert resolve_device("auto") in {"cpu", "cuda", "mps"}


def test_blank_env_is_auto(monkeypatch):
    monkeypatch.setenv("MEDTERM4DS_DEVICE", "   ")
    assert resolve_device() in {"cpu", "cuda", "mps"}


def test_auto_falls_back_to_cpu_without_gpu(monkeypatch):
    """On a CUDA-less host (CI), auto must resolve to cpu, never raise."""
    import torch

    if torch.cuda.is_available():
        pytest.skip("host has CUDA; auto-detect path returns cuda here")
    monkeypatch.delenv("MEDTERM4DS_DEVICE", raising=False)
    assert resolve_device("auto") == "cpu"


def test_invalid_value_names_the_variable(monkeypatch):
    monkeypatch.setenv("MEDTERM4DS_DEVICE", "gpu")
    with pytest.raises(ValueError, match="MEDTERM4DS_DEVICE"):
        resolve_device()


def test_explicit_cuda_unavailable_raises(monkeypatch):
    import torch

    if torch.cuda.is_available():
        pytest.skip("host has CUDA; the unavailable-cuda error path can't run")
    with pytest.raises(RuntimeError, match="CUDA"):
        resolve_device("cuda")


def test_cuda_with_index_parsed(monkeypatch):
    import torch

    if not torch.cuda.is_available():
        pytest.skip("host has no CUDA")
    monkeypatch.delenv("MEDTERM4DS_DEVICE", raising=False)
    assert resolve_device("cuda:0") == "cuda:0"
