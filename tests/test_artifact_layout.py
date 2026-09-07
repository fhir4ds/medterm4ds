"""Phase 3 split-layout tests: models/<space>/ + data/<revision>/ resolution.

Covers the locked design (docs/plans/artifact-governance-plan.md §4/§7.4):
layout preference, legacy fallback + deprecation nudge, natural-sort latest
resolution, data revision pinning, space cross-check failure, atomic
downloads, cache-refresh --split, and the semantic engine's index_dir
bridge.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

SPACE = "esp_5a508816b4bb95e9"
ENV_KEYS = (
    "MEDTERM4DS_CACHE_DIR",
    "MEDTERM4DS_HF_REVISION",
    "MEDTERM4DS_LAYOUT",
    "MEDTERM4DS_DATA_REVISION",
)


@pytest.fixture(autouse=True)
def _restore_search_module():
    """importlib.reload mutates services.search in place — restore env +
    Path.home and re-execute under the real environment after each test
    (same pattern as tests/test_artifact_cache.py)."""
    saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
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


def _reload_search(monkeypatch, **env):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        if value is not None:
            monkeypatch.setenv(key, value)
    import medterm4ds.services.search as search_mod

    return importlib.reload(search_mod)


def _make_model_unit(dirpath: Path, *, manifest: dict | None = None) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "model.safetensors").write_bytes(b"weights")
    (dirpath / "config.json").write_text("{}")
    (dirpath / "tokenizer.json").write_text("{}")
    if manifest is not None:
        (dirpath / "manifest.json").write_text(json.dumps(manifest))
    return dirpath


def _make_data_unit(dirpath: Path, *, manifest: dict) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "canonical_anchor_value_sets.json").write_text("[]")
    (dirpath / "canonical_concepts_faiss.index").write_bytes(b"idx")
    (dirpath / "canonical_concepts_metadata.json").write_text("[]")
    (dirpath / "manifest.json").write_text(json.dumps(manifest))
    return dirpath


def _data_manifest(
    *,
    data_revision: str = "cdb_2026_09_07",
    embedding_space_id: str = SPACE,
    render_policy_version: str = "rp_1891c4cb719d",
    index_lineage: dict | None = None,
) -> dict:
    lineage = index_lineage if index_lineage is not None else {
        "built_against_canonical_build": data_revision,
        "embedding_space_id": embedding_space_id,
        "index_md5": _md5_bytes(b"idx"),
        "metadata_md5": _md5_bytes(b"[]"),
        "files": {
            "canonical_concepts_faiss.index": _md5_bytes(b"idx"),
            "canonical_concepts_metadata.json": _md5_bytes(b"[]"),
        },
    }
    return {
        "artifact_kind": "data",
        "schema_version": 1,
        "data_revision": data_revision,
        "embedding_space_id": embedding_space_id,
        "render_policy_version": render_policy_version,
        "index_lineage": lineage,
    }


def _model_manifest(*, embedding_space_id: str = SPACE) -> dict:
    return {
        "artifact_kind": "model",
        "schema_version": 1,
        "model_name": "sapbert_finetuned",
        "model_rev": "c4e5a52274b3e34d",
        "embedding_space_id": embedding_space_id,
        "space_fingerprint": {
            "weights_md5": _md5_bytes(b"weights"),
            "tokenizer_md5": _md5_bytes(b"{}"),
            "render_policy_version": "rp_1891c4cb719d",
            "pooling": "mean",
            "l2_normalize": True,
            "max_seq_length": 512,
        },
    }


def _md5_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.md5(payload).hexdigest()


class TestLayoutResolution:
    def test_local_split_model_dir_found(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch, MEDTERM4DS_HF_REVISION="v0.0.5")
        _make_model_unit(tmp_path / ".cache" / "medterm4ds" / "models" / SPACE)
        found = mod._local_split_model_dir()
        assert found is not None
        assert found.name == SPACE

    def test_local_split_model_dir_respects_legacy_force(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(
            monkeypatch, MEDTERM4DS_LAYOUT="legacy", MEDTERM4DS_HF_REVISION="v0.0.5"
        )
        _make_model_unit(tmp_path / ".cache" / "medterm4ds" / "models" / SPACE)
        assert mod._local_split_model_dir() is None

    def test_latest_data_dir_natural_sort(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        root = tmp_path / ".cache" / "medterm4ds" / "data"
        for rev in ("cdb_2026_09_07", "cdb_2026_09_10", "cdb_2026_08_30"):
            _make_data_unit(root / rev, manifest=_data_manifest(data_revision=rev))
        latest = mod._local_latest_data_dir()
        assert latest is not None and latest.name == "cdb_2026_09_10"

    def test_data_revision_env_pins(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(
            monkeypatch, MEDTERM4DS_DATA_REVISION="cdb_2026_08_30"
        )
        root = tmp_path / ".cache" / "medterm4ds" / "data"
        for rev in ("cdb_2026_09_07", "cdb_2026_08_30"):
            _make_data_unit(root / rev, manifest=_data_manifest(data_revision=rev))
        latest = mod._local_latest_data_dir()
        assert latest is not None and latest.name == "cdb_2026_08_30"

    def test_semantic_available_prefers_split(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        svc = mod.SearchService()
        assert not svc.semantic_available  # nothing cached
        _make_model_unit(
            tmp_path / ".cache" / "medterm4ds" / "models" / SPACE,
            manifest=_model_manifest(),
        )
        assert svc.semantic_available

    def test_semantic_available_forced_legacy_ignores_split(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch, MEDTERM4DS_LAYOUT="legacy")
        _make_model_unit(
            tmp_path / ".cache" / "medterm4ds" / "models" / SPACE,
            manifest=_model_manifest(),
        )
        svc = mod.SearchService()
        assert not svc.semantic_available


class TestResolveSemanticLayouts:
    def test_split_model_with_bridged_index_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        split = _make_model_unit(
            tmp_path / ".cache" / "medterm4ds" / "models" / SPACE,
            manifest=_model_manifest(),
        )
        # Legacy semantic/ dir with byte-identical weights+tokenizer and a
        # per-cat FAISS index.
        legacy = tmp_path / ".cache" / "medterm4ds" / "v0.0.5" / "semantic"
        legacy.mkdir(parents=True)
        (legacy / "model.safetensors").write_bytes(b"weights")
        (legacy / "tokenizer.json").write_text("{}")
        (legacy / "condition_faiss.index").write_bytes(b"f")
        (legacy / "condition_metadata.json").write_text("[]")

        svc = mod.SearchService()
        model_dir, index_dir = svc._resolve_semantic_layouts()
        assert Path(model_dir) == split
        assert index_dir is not None and Path(index_dir) == legacy

    def test_split_model_without_legacy_falls_back_to_no_index(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        _make_model_unit(
            tmp_path / ".cache" / "medterm4ds" / "models" / SPACE,
            manifest=_model_manifest(),
        )
        svc = mod.SearchService()
        model_dir, index_dir = svc._resolve_semantic_layouts()
        assert Path(model_dir).name == SPACE
        assert index_dir is None

    def test_bridge_rejects_different_weights(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        split = _make_model_unit(
            tmp_path / ".cache" / "medterm4ds" / "models" / SPACE,
            manifest=_model_manifest(),
        )
        legacy = tmp_path / ".cache" / "medterm4ds" / "v0.0.5" / "semantic"
        legacy.mkdir(parents=True)
        (legacy / "model.safetensors").write_bytes(b"DIFFERENT-WEIGHTS")
        (legacy / "tokenizer.json").write_text("{}")
        (legacy / "condition_faiss.index").write_bytes(b"f")
        (legacy / "condition_metadata.json").write_text("[]")

        svc = mod.SearchService()
        model_dir, index_dir = svc._resolve_semantic_layouts()
        assert Path(model_dir) == split
        assert index_dir is None

    def test_legacy_served_with_deprecation_log(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        legacy = tmp_path / ".cache" / "medterm4ds" / "v0.0.5" / "semantic"
        _make_model_unit(legacy)

        import logging

        with caplog.at_level(logging.INFO, logger="medterm4ds.services.search"):
            svc = mod.SearchService()
            model_dir, index_dir = svc._resolve_semantic_layouts()
        assert Path(model_dir) == legacy
        assert index_dir is None
        assert any("cache-refresh --split" in r.message for r in caplog.records)


class TestResolveSplitData:
    def test_space_cross_check_failure_is_loud(self, monkeypatch, tmp_path):
        from medterm4ds.core.artifact_manifest import ManifestError

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        _make_data_unit(
            tmp_path / ".cache" / "medterm4ds" / "data" / "cdb_2026_09_07",
            manifest=_data_manifest(embedding_space_id="esp_DEADBEEFDEADBEEF"),
        )
        svc = mod.SearchService()
        with pytest.raises(ManifestError):
            svc._resolve_split_data_dir()

    def test_companion_md5_mismatch_fails_concepts(self, monkeypatch, tmp_path):
        from medterm4ds.core.artifact_manifest import ManifestError

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        unit = _make_data_unit(
            tmp_path / ".cache" / "medterm4ds" / "data" / "cdb_2026_09_07",
            manifest=_data_manifest(),
        )
        # Corrupt the index AFTER writing a matching manifest.
        (unit / "canonical_concepts_faiss.index").write_bytes(b"CORRUPTED")
        svc = mod.SearchService()
        with pytest.raises(ManifestError):
            svc._ensure_concepts()

    def test_engine_space_mismatch_fails_concept_search(self, monkeypatch, tmp_path):
        """_check_concepts_space: data manifest space != engine space → hard error."""
        from medterm4ds.core.artifact_manifest import ManifestError

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        _make_data_unit(
            tmp_path / ".cache" / "medterm4ds" / "data" / "cdb_2026_09_07",
            manifest=_data_manifest(embedding_space_id="esp_DEADBEEFDEADBEEF"),
        )
        # The manifest's lineage declares the WRONG space too — concepts
        # load fails at the lineage check before the engine cross-check.
        svc = mod.SearchService()
        with pytest.raises(ManifestError):
            svc._ensure_concepts()

    def test_check_concepts_space_engine_mismatch(self, monkeypatch, tmp_path):
        from medterm4ds.core.artifact_manifest import ManifestError

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        unit = _make_data_unit(
            tmp_path / ".cache" / "medterm4ds" / "data" / "cdb_2026_09_07",
            manifest=_data_manifest(),
        )
        svc = mod.SearchService()
        # Simulate the post-_ensure_concepts state (avoids needing a real
        # FAISS index file); the cross-check is a pure manifest comparison.
        svc._concepts_data_dir = unit
        svc._concepts_data_manifest = _data_manifest(
            embedding_space_id="esp_OTHERSPACE000000"
        )

        class FakeEngine:
            space_id = SPACE

        with pytest.raises(ManifestError):
            svc._check_concepts_space(FakeEngine())

    def test_check_concepts_space_ok_when_engine_matches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        _make_data_unit(
            tmp_path / ".cache" / "medterm4ds" / "data" / "cdb_2026_09_07",
            manifest=_data_manifest(),
        )
        svc = mod.SearchService()

        class FakeEngine:
            space_id = SPACE

        svc._check_concepts_space(FakeEngine())  # no raise
        svc._check_concepts_space(FakeEngine())  # once-only

    def test_canonical_loads_from_split_unit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        unit = _make_data_unit(
            tmp_path / ".cache" / "medterm4ds" / "data" / "cdb_2026_09_07",
            manifest=_data_manifest(),
        )
        (unit / "canonical_anchor_value_sets.json").write_text(
            json.dumps([{
                "canonical_id": "VAL-MED-1",
                "anchor_system": "RXNORM",
                "anchor_code": "1",
                "patient_friendly_name": "Test",
                "members": [],
            }])
        )
        svc = mod.SearchService()
        svc._ensure_canonical()
        assert svc._canonical_loaded
        assert "VAL-MED-1" in svc._canonical_by_id

    def test_network_failure_falls_back_to_legacy(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)
        # Legacy canonical present; no split data; downloads explode.
        legacy_json = (
            tmp_path / ".cache" / "medterm4ds" / "v0.0.5" / "canonical"
            / "canonical_anchor_value_sets.json"
        )
        legacy_json.parent.mkdir(parents=True)
        legacy_json.write_text("[]")

        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr(mod, "_download_split_data", boom)
        svc = mod.SearchService()
        svc._ensure_canonical()
        assert svc._canonical_loaded


class TestAtomicDownloads:
    def test_download_uses_tmp_then_rename(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)

        def fake_snapshot(**kwargs):
            local_dir = Path(kwargs["local_dir"])
            for pattern in kwargs["allow_patterns"]:
                fam = pattern.split("/")[0]
                (local_dir / fam).mkdir(parents=True, exist_ok=True)
                (local_dir / fam / "artifact.bin").write_bytes(b"x")
            return str(local_dir)

        monkeypatch.setattr(
            "huggingface_hub.snapshot_download", fake_snapshot
        )
        target_root = tmp_path / "cache"
        target_root.mkdir()
        mod._hf_download_atomic(
            ["data/cdb_1/*"], target_root=target_root, family="data",
            revision="main",
        )
        assert (target_root / "data" / "artifact.bin").exists()
        # tmp dir cleaned up (renamed away)
        leftovers = [
            p for p in target_root.parent.iterdir() if p.name.startswith(".tmp-")
        ]
        assert leftovers == []


class TestCacheRefreshSplit:
    def test_refresh_split_reports_units(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _reload_search(monkeypatch)
        _make_model_unit(
            tmp_path / ".cache" / "medterm4ds" / "models" / SPACE,
            manifest=_model_manifest(),
        )
        _make_data_unit(
            tmp_path / ".cache" / "medterm4ds" / "data" / "cdb_2026_09_07",
            manifest=_data_manifest(),
        )

        from medterm4ds.core import artifact_cache

        report = artifact_cache.cache_refresh(split=True)
        assert report["layout"] == "split"
        assert report["data_revision"] == "cdb_2026_09_07"

    def test_cache_info_reports_split_layout(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _reload_search(monkeypatch)
        _make_model_unit(
            tmp_path / ".cache" / "medterm4ds" / "models" / SPACE,
            manifest=_model_manifest(),
        )
        _make_data_unit(
            tmp_path / ".cache" / "medterm4ds" / "data" / "cdb_2026_09_07",
            manifest=_data_manifest(),
        )

        from medterm4ds.core import artifact_cache

        info = artifact_cache.cache_info()
        split = info["split_layout"]
        assert split["mode"] == "auto"
        assert SPACE in split["models"]
        assert "cdb_2026_09_07" in split["data"]
        assert split["resolved_data_revision"] == "cdb_2026_09_07"


class TestEngineIndexDir:
    def test_engine_index_dir_param(self, tmp_path):
        from medterm4ds.engines.fhir.semantic import SemanticSearchEngine

        model_dir = _make_model_unit(tmp_path / "model")
        index_dir = tmp_path / "indexes"
        index_dir.mkdir()
        (index_dir / "condition_faiss.index").write_bytes(b"f")
        (index_dir / "condition_metadata.json").write_text("[]")
        engine = SemanticSearchEngine(str(model_dir), index_dir=str(index_dir))
        assert engine._index_root == index_dir
        assert engine.space_id is None  # init fix: no AttributeError

    def test_engine_index_root_defaults_to_model_dir(self, tmp_path):
        from medterm4ds.engines.fhir.semantic import SemanticSearchEngine

        model_dir = _make_model_unit(tmp_path / "model")
        engine = SemanticSearchEngine(str(model_dir))
        assert engine._index_root == model_dir


class TestBareServiceOffline:
    def test_construction_is_network_free(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mod = _reload_search(monkeypatch)

        def boom(*a, **k):
            raise AssertionError("network call during __init__")

        monkeypatch.setattr(mod, "_hf_download", boom)
        monkeypatch.setattr(mod, "_download_split_model", boom)
        monkeypatch.setattr(mod, "_download_split_data", boom)
        svc = mod.SearchService()
        assert svc._canonical_by_id == {}
        assert not svc.semantic_available
