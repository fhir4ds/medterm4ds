"""Tests for the revision-keyed artifact cache and cache-* operations."""

from __future__ import annotations

import importlib
import json
import os
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


_ENV_KEYS = ("MEDTERM4DS_CACHE_DIR", "MEDTERM4DS_HF_REVISION")


@pytest.fixture(autouse=True)
def _restore_search_module():
    """importlib.reload mutates the search module IN PLACE — the test-time
    _CACHE_DIR (a dead tmp dir, possibly holding empty fixture files) stays
    live for every later test in the process, and extraction tests then read
    an empty canonical JSON (JSONDecodeError, 12 failures in full-suite
    runs while passing alone). monkeypatch cannot undo a reload, so this
    finalizer restores the real env + Path.home FIRST (its finalizer runs
    before monkeypatch's LIFO undo) and re-executes the module once more
    under the operator's actual environment."""
    import os

    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    saved_home = Path.home
    yield
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    Path.home = saved_home
    import medterm4ds.services.search as search_mod

    importlib.reload(search_mod)


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
        _reload_search(monkeypatch, revision="vX-test")

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

    def test_refresh_mid_swap_failure_leaves_no_provenance(
        self, monkeypatch, tmp_path
    ):
        """CR-SHOULD-FIX-1: a raise between family swaps must NOT leave the
        OLD provenance marker over a mixed-revision cache. The marker is
        unlinked before the swap loop, so a partially-swapped cache is
        detectable by its missing marker ("refresh incomplete")."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _reload_search(monkeypatch, revision="vM-swap")
        import medterm4ds.core.artifact_cache as ac

        root = tmp_path / ".cache" / "medterm4ds" / "vM-swap"
        fam = root / "canonical"
        fam.mkdir(parents=True)
        (fam / "existing.bin").write_bytes(b"keep")
        (root / ac.PROVENANCE_FILENAME).write_text('{"revision": "old"}')

        import huggingface_hub

        def fake_snapshot(*, local_dir, allow_patterns, **kw):
            for pattern in allow_patterns:
                sub = Path(local_dir) / pattern.rstrip("/*")
                sub.mkdir(parents=True, exist_ok=True)
                (sub / "fresh.bin").write_bytes(b"new")
            return local_dir

        monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)

        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError("simulated kill mid-swap")
            return real_replace(src, dst)

        monkeypatch.setattr(ac.os, "replace", flaky_replace)
        with pytest.raises(OSError, match="simulated kill mid-swap"):
            ac.cache_refresh(revision=None)
        # First family was swapped to the new revision, the rest stay old —
        # a mixed cache — but the stale marker is GONE (observable, not
        # silently mislabeled).
        assert not (root / ac.PROVENANCE_FILENAME).exists()
        assert not [
            p for p in root.parent.iterdir() if p.name.startswith(".refresh-")
        ]


    def test_refresh_download_failure_preserves_cache(self, monkeypatch, tmp_path):
        """EV07-QA-01: a failing snapshot_download must leave the existing
        families and provenance marker untouched (delete-first was the
        hazard) and clean up the staging dir."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _reload_search(monkeypatch, revision="vW-fail")
        import medterm4ds.core.artifact_cache as ac

        root = tmp_path / ".cache" / "medterm4ds" / "vW-fail"
        fam = root / "canonical"
        fam.mkdir(parents=True)
        (fam / "existing.bin").write_bytes(b"keep")
        (root / ac.PROVENANCE_FILENAME).write_text('{"revision": "old"}')

        import huggingface_hub

        def boom(**kw):
            raise RuntimeError("hub unreachable")

        monkeypatch.setattr(huggingface_hub, "snapshot_download", boom)
        with pytest.raises(RuntimeError, match="hub unreachable"):
            ac.cache_refresh(revision=None)
        assert (fam / "existing.bin").read_bytes() == b"keep"
        prov = json.loads((root / ac.PROVENANCE_FILENAME).read_text())
        assert prov["revision"] == "old"
        assert not [
            p for p in root.parent.iterdir() if p.name.startswith(".refresh-")
        ]

    def test_refresh_empty_download_preserves_cache(self, monkeypatch, tmp_path):
        """EV07-QA-01: huggingface_hub silently "returns the existing
        local_dir" when the hub is unreachable — a download that produced
        no family dirs must fail loud, never rewrite provenance."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _reload_search(monkeypatch, revision="vW-empty")
        import medterm4ds.core.artifact_cache as ac

        root = tmp_path / ".cache" / "medterm4ds" / "vW-empty"
        fam = root / "canonical"
        fam.mkdir(parents=True)
        (fam / "existing.bin").write_bytes(b"keep")
        (root / ac.PROVENANCE_FILENAME).write_text('{"revision": "old"}')

        import huggingface_hub

        def silent_noop(**kw):
            return kw["local_dir"]

        monkeypatch.setattr(huggingface_hub, "snapshot_download", silent_noop)
        with pytest.raises(RuntimeError, match="family dir"):
            ac.cache_refresh(revision=None)
        assert (fam / "existing.bin").read_bytes() == b"keep"
        prov = json.loads((root / ac.PROVENANCE_FILENAME).read_text())
        assert prov["revision"] == "old"

    def test_refresh_rejects_split_with_terminology(self, monkeypatch, tmp_path):
        """EV07-QA-12: --split + --terminology is a flag conflict — the
        split branch previously returned before consulting the flag,
        silently dropping the terminology refresh."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _reload_search(monkeypatch, revision="vX-mut")
        import medterm4ds.core.artifact_cache as ac

        with pytest.raises(ValueError, match="mutually exclusive"):
            ac.cache_refresh(split=True, terminology=True)


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
