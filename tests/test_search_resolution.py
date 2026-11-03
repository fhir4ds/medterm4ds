"""AMUX-44: search-asset acquisition contract.

One acquisition story across all three server surfaces (FHIR / MCP / REST
api — the CLI reuses the same singleton):

  explicit env pin (MEDTERM4DS_SEARCH_INDEX_DIR / _EMBEDDING_MODEL_DIR)
      → dir used as-is, layout PINNED, never auto-provisioned;
  env unset (None)
      → release-aware 'auto' resolution in services.search (terminology
        release → split → legacy cache), first-use provisioning enabled.

The historical bug: apps/fhir_api passed a dead /mnt/d/fhir4px-model dir
as an explicit default, defeating release resolution on every machine.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from medterm4ds.services import search as sm


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "MEDTERM4DS_SEARCH_INDEX_DIR",
        "MEDTERM4DS_EMBEDDING_MODEL_DIR",
        "MEDTERM4DS_CACHE_DIR",
        "MEDTERM4DS_SEMANTIC_INDEX_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path("/tmp/amux44-home"))
    sm.reset_search_service()
    yield
    sm.reset_search_service()


@pytest.fixture
def fake_release(tmp_path):
    """A terminology release resolved locally (provisioner 'ran')."""
    lex = tmp_path / "term" / "lexical"
    lex.mkdir(parents=True)
    (lex / "condition.json").write_text("{}")
    model = tmp_path / "term" / "model"
    model.mkdir(parents=True)
    (model / "model.safetensors").write_text("x")
    (model / "config.json").write_text("{}")
    term = SimpleNamespace(lexical=lex, model=model, semantic=tmp_path / "term")
    calls = {"n": 0}

    def _resolve(download=True):
        calls["n"] += 1
        return term

    return term, calls, _resolve


class TestFreshInstallResolvesRelease:
    def test_no_env_unpinned_and_release_serves_lexical(self, monkeypatch, fake_release):
        term, calls, _resolve = fake_release
        monkeypatch.setattr(sm, "_resolve_terminology", _resolve)
        svc = sm.get_search_service()
        # Contract: unset env means UNPINNED — auto resolution applies.
        assert svc._bm25_dir_pinned is False
        assert svc._model_dir_pinned is False
        assert svc.lexical_available is True, "release must serve lexical"
        assert calls["n"] >= 1

    def test_no_env_bm25_loads_from_release(self, monkeypatch, fake_release):
        term, calls, _resolve = fake_release
        monkeypatch.setattr(sm, "_resolve_terminology", _resolve)
        svc = sm.SearchService()
        svc._ensure_bm25()
        assert svc._bm25_dir == term.lexical

    def test_fresh_fhir_settings_pass_none_through(self, monkeypatch, fake_release):
        # The FHIR surface must not substitute any dir for an unset env var
        # (the original bug: a dead /mnt/d literal defeated the release).
        from medterm4ds.apps.fhir_api import FhirApiSettings

        monkeypatch.setenv("MEDTERM4DS_DB", "/tmp/amux44-home/umls.duckdb")
        settings = FhirApiSettings.from_env()
        assert settings.search_index_dir is None


class TestExplicitEnvPins:
    def test_env_index_dir_pins_and_skips_release(self, monkeypatch, fake_release):
        term, calls, _resolve = fake_release
        monkeypatch.setattr(sm, "_resolve_terminology", _resolve)
        monkeypatch.setenv("MEDTERM4DS_SEARCH_INDEX_DIR", "/tmp/amux44-pinned-lex")
        svc = sm.get_search_service()
        assert svc._bm25_dir_pinned is True
        assert str(svc._bm25_dir) == "/tmp/amux44-pinned-lex"
        # A pinned dir that does not exist must NOT be rescued by the
        # release (the operator contract: pinned means pinned).
        assert svc.lexical_available is False
        assert calls["n"] == 0

    def test_env_model_dir_pins(self, monkeypatch, fake_release):
        _, calls, _resolve = fake_release
        monkeypatch.setattr(sm, "_resolve_terminology", _resolve)
        monkeypatch.setenv("MEDTERM4DS_EMBEDDING_MODEL_DIR", "/tmp/amux44-pinned-model")
        svc = sm.get_search_service()
        assert svc._model_dir_pinned is True

    def test_configure_search_service_none_falls_back_to_env_then_auto(
        self, monkeypatch, fake_release
    ):
        _, calls, _resolve = fake_release
        monkeypatch.setattr(sm, "_resolve_terminology", _resolve)
        svc = sm.configure_search_service(search_index_dir=None)
        assert svc._bm25_dir_pinned is False


class TestSentinelBackcompat:
    def test_passing_sentinel_value_still_means_unpinned(self, fake_release):
        # Legacy callers (and tests) that pass the DEFAULT_* value
        # explicitly must keep the auto behavior — the value IS the
        # unpinned marker, not a pin.
        svc = sm.SearchService(search_index_dir=sm.DEFAULT_SEARCH_INDEX_DIR)
        assert svc._bm25_dir_pinned is False
