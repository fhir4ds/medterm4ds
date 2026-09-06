"""Tests for core.artifact_manifest (Phase 1 of the governance plan).

Covers the space fingerprint stability, manifest validation rules
(hard-fail vs warn), the acceptance registry, and the atomic-rename
helper. Uses only synthetic files — no SapBERT download.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medterm4ds.core.artifact_manifest import (
    ACCEPTED_MANIFEST_SCHEMA_VERSIONS,
    ManifestError,
    SpaceFingerprint,
    atomic_rename_dir,
    fingerprint_model_dir,
    manifest_str,
    md5_of_file,
    read_manifest,
    validate_data_manifest,
    validate_index_lineage,
    validate_model_manifest,
)


def _model_manifest(space_id: str = "esp_deadbeefdeadbeef") -> dict:
    return {
        "schema_version": 1,
        "artifact_kind": "model",
        "embedding_space_id": space_id,
        "model_name": "sapbert_finetuned",
        "model_rev": "abc123",
        "space_fingerprint": {
            "weights_md5": "w" * 32,
            "tokenizer_md5": "t" * 32,
            "pooling": "mean",
            "l2_normalize": True,
            "max_seq_length": 512,
            "render_policy_version": "rp_hash1",
        },
        "published_at": "2026-09-05T00:00:00Z",
    }


def _data_manifest(space_id: str = "esp_deadbeefdeadbeef") -> dict:
    return {
        "schema_version": 1,
        "artifact_kind": "data",
        "data_revision": "cdb_2026_09_05_1",
        "embedding_space_id": space_id,
        "render_policy_version": "rp_hash1",
        "published_at": "2026-09-05T00:00:00Z",
    }


class TestSpaceFingerprint:
    def test_id_is_deterministic_and_prefixed(self):
        fp = SpaceFingerprint(
            weights_md5="w" * 32, tokenizer_md5="t" * 32, render_policy_version="rp1"
        )
        assert fp.space_id() == fp.space_id()
        assert fp.space_id().startswith("esp_")

    def test_render_policy_changes_the_space(self):
        """The 2026-09-04 rename-wave lesson: policy is part of the space."""
        base = SpaceFingerprint(weights_md5="w" * 32, tokenizer_md5="t" * 32)
        with_policy = SpaceFingerprint(
            weights_md5="w" * 32, tokenizer_md5="t" * 32, render_policy_version="rp2"
        )
        assert base.space_id() != with_policy.space_id()

    def test_weights_change_changes_the_space(self):
        a = SpaceFingerprint(weights_md5="a" * 32, tokenizer_md5="t" * 32)
        b = SpaceFingerprint(weights_md5="b" * 32, tokenizer_md5="t" * 32)
        assert a.space_id() != b.space_id()

    def test_fingerprint_model_dir_reads_real_files(self, tmp_path: Path):
        (tmp_path / "model.safetensors").write_bytes(b"weights")
        (tmp_path / "tokenizer.json").write_text("{}")
        fp = fingerprint_model_dir(tmp_path, render_policy_version="rp1")
        assert fp.weights_md5 == md5_of_file(tmp_path / "model.safetensors")
        assert fp.tokenizer_md5 == md5_of_file(tmp_path / "tokenizer.json")
        assert fp.pooling == "mean" and fp.l2_normalize is True
        assert fp.max_seq_length == 512  # semantic.py _embed contract


class TestReadManifest:
    def test_absent_manifest_returns_none(self, tmp_path: Path):
        assert read_manifest(tmp_path) is None

    def test_malformed_manifest_raises(self, tmp_path: Path):
        (tmp_path / "manifest.json").write_text("{not json")
        with pytest.raises(ManifestError, match="Malformed"):
            read_manifest(tmp_path)

    def test_non_object_manifest_raises(self, tmp_path: Path):
        (tmp_path / "manifest.json").write_text("[1, 2]")
        with pytest.raises(ManifestError, match="not a JSON object"):
            read_manifest(tmp_path)

    def test_valid_manifest_round_trips(self, tmp_path: Path):
        (tmp_path / "manifest.json").write_text(json.dumps(_model_manifest()))
        assert read_manifest(tmp_path)["artifact_kind"] == "model"


class TestValidateModelManifest:
    def test_accepts_well_formed(self):
        space = validate_model_manifest(_model_manifest())
        assert space == "esp_deadbeefdeadbeef"

    def test_rejects_unknown_schema_version(self):
        m = _model_manifest()
        m["schema_version"] = 99
        with pytest.raises(ManifestError, match="schema_version 99 not accepted"):
            validate_model_manifest(m)

    def test_rejects_missing_fingerprint_fields(self):
        m = _model_manifest()
        del m["space_fingerprint"]["render_policy_version"]
        with pytest.raises(ManifestError, match="render_policy_version"):
            validate_model_manifest(m)

    def test_rejects_empty_render_policy(self):
        m = _model_manifest()
        m["space_fingerprint"]["render_policy_version"] = ""
        with pytest.raises(ManifestError, match="cannot be declared by hand"):
            validate_model_manifest(m)

    def test_rejects_unaccepted_space_when_registry_populated(self, monkeypatch):
        import medterm4ds.core.artifact_manifest as mod

        monkeypatch.setattr(
            mod, "ACCEPTED_EMBEDDING_SPACES", frozenset({"esp_other"})
        )
        with pytest.raises(ManifestError, match="not accepted by this"):
            validate_model_manifest(_model_manifest())

    def test_empty_registry_accepts_any_wellformed_manifest(self):
        # Migration semantics: acceptance enforcement starts when the
        # registry is populated (Phase 2 dual-publish).
        validate_model_manifest(_model_manifest(space_id="esp_brandnew"))


class TestValidateDataManifest:
    def test_accepts_matching_space(self):
        validate_data_manifest(_data_manifest(), serving_space_id="esp_deadbeefdeadbeef")

    def test_rejects_space_mismatch_loudly(self):
        with pytest.raises(ManifestError, match="produced against embedding space"):
            validate_data_manifest(
                _data_manifest(space_id="esp_old"), serving_space_id="esp_new"
            )

    def test_requires_render_policy_version(self):
        m = _data_manifest()
        del m["render_policy_version"]
        with pytest.raises(ManifestError, match="render_policy_version"):
            validate_data_manifest(m)

    def test_none_serving_space_skips_cross_check(self):
        # Lexical-only consumers pass no serving space.
        validate_data_manifest(_data_manifest(), serving_space_id=None)


class TestValidateIndexLineage:
    def _index_dir(self, tmp_path: Path, *, md5s_match: bool = True) -> Path:
        d = tmp_path / "idx"
        d.mkdir()
        (d / "concepts_faiss.index").write_bytes(b"index-bytes")
        (d / "concepts_metadata.json").write_text("{}")
        m = _data_manifest()
        m["index_lineage"] = {
            "embedding_space_id": "esp_deadbeefdeadbeef",
            "built_against_canonical_build": "cdb_2026_09_05_1",
            "index_md5": md5_of_file(d / "concepts_faiss.index") if md5s_match else "0" * 32,
            "metadata_md5": md5_of_file(d / "concepts_metadata.json"),
            "files": {
                "concepts_faiss.index": md5_of_file(d / "concepts_faiss.index"),
                "concepts_metadata.json": md5_of_file(d / "concepts_metadata.json"),
            },
        }
        (d / "manifest.json").write_text(json.dumps(m))
        return d

    def test_hard_fails_on_space_mismatch(self, tmp_path: Path):
        d = self._index_dir(tmp_path)
        m = read_manifest(d)
        with pytest.raises(ManifestError, match="not comparable"):
            validate_index_lineage(
                m, d, serving_space_id="esp_DIFFERENT", data_revision="cdb_2026_09_05_1"
            )

    def test_hard_fails_on_md5_mismatch(self, tmp_path: Path):
        d = self._index_dir(tmp_path, md5s_match=False)
        m = read_manifest(d)
        # Corrupt the on-disk pair relative to the manifest's declared md5s:
        # keep index_md5 wrong AND make the files map disagree too.
        m["index_lineage"]["files"]["concepts_faiss.index"] = "0" * 32
        with pytest.raises(ManifestError, match="not the same build"):
            validate_index_lineage(
                m, d, serving_space_id="esp_deadbeefdeadbeef"
            )

    def test_stale_index_warns_but_serves(self, tmp_path: Path, caplog):
        d = self._index_dir(tmp_path)
        m = read_manifest(d)
        with caplog.at_level("WARNING"):
            validate_index_lineage(
                m,
                d,
                serving_space_id="esp_deadbeefdeadbeef",
                data_revision="cdb_2026_09_06_2",  # newer wave
            )
        assert "STALE INDEX" in caplog.text
        assert "cdb_2026_09_05_1" in caplog.text and "cdb_2026_09_06_2" in caplog.text

    def test_fresh_index_is_silent(self, tmp_path: Path, caplog):
        d = self._index_dir(tmp_path)
        m = read_manifest(d)
        with caplog.at_level("WARNING"):
            validate_index_lineage(
                m,
                d,
                serving_space_id="esp_deadbeefdeadbeef",
                data_revision="cdb_2026_09_05_1",  # exact match
            )
        assert "STALE INDEX" not in caplog.text


class TestHelpers:
    def test_manifest_str_includes_provenance_keys(self):
        s = manifest_str(_model_manifest())
        assert "embedding_space_id=esp_deadbeefdeadbeef" in s
        assert "artifact_kind=model" in s

    def test_manifest_str_none(self):
        assert manifest_str(None) == "<no manifest>"

    def test_atomic_rename_moves_whole_unit(self, tmp_path: Path):
        tmp = tmp_path / "models" / "esp_x.tmp-download"
        tmp.mkdir(parents=True)
        (tmp / "a.index").write_bytes(b"a")
        (tmp / "b.json").write_text("{}")
        target = tmp_path / "models" / "esp_x"
        atomic_rename_dir(tmp, target)
        assert (target / "a.index").exists() and (target / "b.json").exists()
        assert not tmp.exists()

    def test_accepted_schema_versions_current(self):
        assert 1 in ACCEPTED_MANIFEST_SCHEMA_VERSIONS
