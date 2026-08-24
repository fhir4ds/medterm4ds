"""Tests for the revision-keyed artifact cache and cache-* operations."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def _reload_search(monkeypatch, cache_dir=None, revision=None):
    """Reload services.search with controlled env and return the module."""
    monkeypatch.delenv("MEDTERM4DS_CACHE_DIR", raising=False)
    monkeypatch.delenv("MEDTERM4DS_HF_REVISION", raising=False)
    if cache_dir is not None:
        monkeypatch.setenv("MEDTERM4DS_CACHE_DIR", cache_dir)
    if revision is not None:
        monkeypatch.setenv("MEDTERM4DS_HF_REVISION", revision)
    import medterm4ds.services.search as search_mod

    return importlib.reload(search_mod)


class TestCacheLayout:
    def test_default_cache_is_revision_keyed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch, revision="v0.0.4")
        assert mod._CACHE_REVISION_KEYED is True
        assert mod._CACHE_DIR == tmp_path / ".cache" / "medterm4ds" / "v0.0.4"

    def test_operator_managed_dir_used_as_is(self, monkeypatch, tmp_path):
        mod = _reload_search(monkeypatch, cache_dir=str(tmp_path / "managed"))
        assert mod._CACHE_REVISION_KEYED is False
        assert mod._CACHE_DIR == tmp_path / "managed"
        # Revision keying must NOT leak into operator-managed paths
        assert "v0.0.2" not in str(mod.DEFAULT_CANONICAL_VALUE_SETS_PATH)

    def test_switching_revision_changes_cache_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Capture values, not module refs: importlib.reload mutates the
        # shared module object in place, so earlier refs see later state.
        a = str(_reload_search(monkeypatch, revision="v0.0.2")._CACHE_DIR)
        b = str(_reload_search(monkeypatch, revision="v0.0.4-canonical")._CACHE_DIR)
        assert a != b
        assert a.endswith("v0.0.2")
        assert b.endswith("v0.0.4-canonical")


class TestProvenanceAndRefresh:
    def test_refresh_writes_provenance_and_files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch, revision="vX-test")

        import medterm4ds.core.artifact_cache as ac

        def fake_snapshot(*, repo_id, revision, repo_type, local_dir,
                          allow_patterns, token):
            d = Path(local_dir)
            for fam in allow_patterns:
                sub = d / fam.rstrip("/*")
                sub.mkdir(parents=True, exist_ok=True)
                (sub / "artifact.bin").write_bytes(b"x" * 16)
            return str(d)

        monkeypatch.setattr(
            "huggingface_hub.snapshot_download", fake_snapshot, raising=False)
        # import path inside artifact_cache does `from huggingface_hub import
        # snapshot_download` at call time, so patch the module attribute.
        import huggingface_hub

        monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)

        report = ac.cache_refresh(revision=None)
        assert report["revision"] == "vX-test"
        prov = json.loads(
            (tmp_path / ".cache" / "medterm4ds" / "vX-test"
             / ac.PROVENANCE_FILENAME).read_text())
        assert prov["revision"] == "vX-test"
        assert prov["repo_id"]

    def test_refresh_refuses_operator_managed_dir(self, monkeypatch, tmp_path):
        managed = tmp_path / "managed"
        managed.mkdir()
        _reload_search(monkeypatch, cache_dir=str(managed))
        import medterm4ds.core.artifact_cache as ac

        with pytest.raises(RuntimeError, match="operator-managed"):
            ac.cache_refresh(revision=None)

    def test_refresh_replaces_stale_files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _reload_search(monkeypatch, revision="vY-test")
        import medterm4ds.core.artifact_cache as ac

        stale = (tmp_path / ".cache" / "medterm4ds" / "vY-test" / "canonical")
        stale.mkdir(parents=True)
        (stale / "stale.bin").write_bytes(b"old")

        import huggingface_hub

        def fake_snapshot(*, local_dir, allow_patterns, **kw):
            for fam in allow_patterns:
                sub = Path(local_dir) / fam.rstrip("/*")
                sub.mkdir(parents=True, exist_ok=True)
                (sub / "fresh.bin").write_bytes(b"new")
            return local_dir

        monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
        ac.cache_refresh(revision=None)
        assert not (stale / "stale.bin").exists()
        assert (stale / "fresh.bin").exists()


class TestCacheInfo:
    def test_info_reports_families(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _reload_search(monkeypatch, revision="vZ-info")
        import medterm4ds.core.artifact_cache as ac

        fam = tmp_path / ".cache" / "medterm4ds" / "vZ-info" / "canonical"
        fam.mkdir(parents=True)
        (fam / "canonical_anchor_value_sets.json").write_bytes(b"x" * 100)
        info = ac.cache_info()
        assert info["mode"].startswith("revision-keyed")
        assert info["families"]["canonical"]["present"] is True
        assert info["families"]["lexical"]["present"] is False
