"""fhir4ds/terminology release layout (2026-10-31 repoint).

Covers the release-keyed contract: manifest validation, family path
mapping, embedding-space admission by content fingerprint, incomplete /
foreign releases failing loud, BM25 + canonical + anchor loading from the
new layout, the minimal anchors_metadata schema adapter, and the
download-gating rule (an existing legacy/split cache is never re-fetched
behind the operator's back).
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import faiss
import numpy as np
import pytest

import medterm4ds.core.terminology as term

ENV_KEYS = (
    "MEDTERM4DS_CACHE_DIR",
    "MEDTERM4DS_TERMINOLOGY_DIR",
    "MEDTERM4DS_TERMINOLOGY_RELEASE",
    "MEDTERM4DS_LAYOUT",
    "MEDTERM4DS_EMBEDDING_SPACE",
    "MEDTERM4DS_EMBEDDING_MODEL_DIR",
    "MEDTERM4DS_SEARCH_INDEX_DIR",
    "MEDTERM4DS_TERMINOLOGY_REPO_ID",
    "MEDTERM4DS_RENDER_POLICY_VERSION",
)
RP = term.DEFAULT_RENDER_POLICY_VERSION
DIM = 768


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the operator's real ~/.cache/medterm4ds."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


@pytest.fixture(autouse=True)
def _restore():
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import medterm4ds.services.search as search_mod

    importlib.reload(search_mod)


def _unit_vec() -> np.ndarray:
    v = np.ones((1, DIM), dtype="float32")
    v /= np.linalg.norm(v)
    return v


def _write_faiss(path: Path, n: int = 3) -> None:
    """Distinct, decreasing-similarity vectors (rank order is asserted)."""
    index = faiss.IndexFlatIP(DIM)
    rows = []
    for i in range(n):
        v = np.ones(DIM, dtype="float32")
        v[DIM // 2:] = 1.0 - 0.01 * (i + 1)
        v = v / np.linalg.norm(v)
        rows.append(v)
    index.add(np.vstack(rows))
    faiss.write_index(index, str(path))


def _build_release(base: Path, *, release: str = term.DEFAULT_TERMINOLOGY_RELEASE) -> dict:
    """Synthetic but contract-complete release; returns helpers for tests."""
    model_dir = base / "model" / "_pending"
    model_dir.mkdir(parents=True)
    (model_dir / "model.safetensors").write_bytes(b"weights" * 64)
    (model_dir / "tokenizer.json").write_text('{"tok": 1}')
    (model_dir / "config.json").write_text('{"config": 1}')

    from medterm4ds.core.artifact_manifest import fingerprint_model_dir

    fp = fingerprint_model_dir(model_dir, render_policy_version=RP)
    space = fp.space_id()
    final_model = base / "model" / space
    model_dir.rename(final_model)

    canon = base / "canonical" / release
    canon.mkdir(parents=True)
    value_sets = [
        {
            "canonical_id": "VAL-MED-1",
            "anchor_system": "RXNORM",
            "anchor_code": "6809",
            "patient_friendly_name": "Metformin",
            "domain": ["Drug"],
            "members": [{"system": "RXNORM", "code": "6809"}],
        },
        {
            "canonical_id": "VAL-LAB-1",
            "anchor_system": "LOINC",
            "anchor_code": "4548-4",
            "patient_friendly_name": "Hemoglobin A1c",
            "domain": ["Laboratory"],
            "members": [],
        },
    ]
    (canon / "value_sets.json").write_text(json.dumps(value_sets))
    (canon / "claimant_details.json").write_text(json.dumps({"ICD10CM:E11": []}))
    (canon / "decomposition_links.json").write_text(
        json.dumps({"schema": "decomposition_links_v1", "links": []})
    )

    lex = base / "lexical" / release
    lex.mkdir(parents=True)
    for cat in term.SEMANTIC_CATEGORIES:
        (lex / f"{cat}.json").write_text(
            json.dumps({
                "category": cat,
                "num_records": 1,
                "names": ["Metformin"],
                "doc_lengths": [1],
                "postings": {"metformin": [[0, 1]]},
                "idf": {"metformin": 1.0},
                "avg_doc_length": 1.0,
                "rid_to_code": ["6809"],
                "rid_to_system": ["RXNORM"],
                "rid_to_friendly_name": ["Metformin"],
            })
        )

    sem = base / "semantic" / release
    sem.mkdir(parents=True)
    meta = [
        {"canonical_id": "VAL-MED-1", "name": "Metformin"},
        {"canonical_id": "VAL-LAB-1", "name": "Hemoglobin A1c"},
        {"canonical_id": "VAL-MED-2", "name": "Metformin Hydrochloride"},
    ]
    for cat in term.SEMANTIC_CATEGORIES:
        _write_faiss(sem / f"{cat}.faiss", n=3)
        (sem / f"{cat}_metadata.json").write_text(json.dumps(
            [{"code": "6809", "system": "rxnorm",
              "friendly_name": "Metformin"}] * 3
        ))
    _write_faiss(sem / "anchors.faiss", n=3)
    (sem / "anchors_metadata.json").write_text(json.dumps(meta))

    manifest = {
        "repo": "fhir4ds/terminology",
        "release": release,
        "families": {
            "model": {"embedding_space_id": space, "first_release": release},
            "canonical": {"release": release},
            "lexical": {"release": release},
            "semantic": {"release": release,
                         "embedding_space_id": space},
        },
    }
    (base / "manifest.json").write_text(json.dumps(manifest))
    return {"base": base, "space": space, "release": release,
            "manifest": manifest}


def _admit(monkeypatch, space: str) -> None:
    """Admit the synthetic fixture space for the duration of the test."""
    import medterm4ds.core.artifact_manifest as am

    monkeypatch.setattr(
        am, "ACCEPTED_EMBEDDING_SPACES",
        am.ACCEPTED_EMBEDDING_SPACES | {space},
    )
    monkeypatch.setattr(am, "DEFAULT_SERVING_EMBEDDING_SPACE", space)


def _reload_search(monkeypatch, **env):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import medterm4ds.services.search as search_mod

    return importlib.reload(search_mod)


class TestResolve:
    def test_maps_families_and_admits_space(self, tmp_path, monkeypatch):
        built = _build_release(tmp_path / "rel")
        monkeypatch.setenv("MEDTERM4DS_TERMINOLOGY_DIR", str(built["base"]))
        paths = term.resolve(tmp_path, space_id=built["space"])
        assert paths is not None
        assert paths.space_id == built["space"]
        assert paths.canonical.name == built["release"]
        assert paths.semantic.name == built["release"]
        assert paths.model.name == built["space"]
        assert (paths.canonical / "value_sets.json").is_file()

    def test_incomplete_release_fails_loud(self, tmp_path, monkeypatch):
        built = _build_release(tmp_path / "rel")
        (built["base"] / "semantic" / built["release"] / "anchors.faiss").unlink()
        monkeypatch.setenv("MEDTERM4DS_TERMINOLOGY_DIR", str(built["base"]))
        with pytest.raises(term.TerminologyLayoutError, match="is incomplete"):
            term.resolve(tmp_path, space_id=built["space"])

    def test_foreign_space_dir_fails_loud(self, tmp_path, monkeypatch):
        built = _build_release(tmp_path / "rel")
        monkeypatch.setenv("MEDTERM4DS_TERMINOLOGY_DIR", str(built["base"]))
        with pytest.raises(term.TerminologyLayoutError, match="model space"):
            term.resolve(tmp_path, space_id="esp_0000000000000000")

    def test_fingerprint_mismatch_fails_loud(self, tmp_path, monkeypatch):
        built = _build_release(tmp_path / "rel")
        monkeypatch.setenv("MEDTERM4DS_TERMINOLOGY_DIR", str(built["base"]))
        monkeypatch.setenv("MEDTERM4DS_RENDER_POLICY_VERSION", "rp_other")
        with pytest.raises(term.TerminologyLayoutError, match="fingerprints"):
            term.resolve(tmp_path, space_id=built["space"])


class TestServiceIntegration:
    def test_bm25_loads_new_filenames(self, tmp_path, monkeypatch):
        built = _build_release(tmp_path / "rel")
        _admit(monkeypatch, built["space"])
        search = _reload_search(
            monkeypatch,
            MEDTERM4DS_TERMINOLOGY_DIR=str(built["base"]),
        )
        svc = search.SearchService()
        svc._ensure_bm25()
        assert "condition" in svc._bm25_indexes
        assert any(
            c in svc._bm25_indexes for c in ("medication", "condition")
        )

    def test_canonical_value_sets_load(self, tmp_path, monkeypatch):
        built = _build_release(tmp_path / "rel")
        _admit(monkeypatch, built["space"])
        search = _reload_search(
            monkeypatch,
            MEDTERM4DS_TERMINOLOGY_DIR=str(built["base"]),
        )
        svc = search.SearchService()
        svc._ensure_canonical()
        assert "VAL-MED-1" in svc._canonical_by_id
        assert svc._canonical_by_id["VAL-MED-1"]["patient_friendly_name"] == (
            "Metformin"
        )

    def test_anchor_index_and_minimal_metadata_adapter(
        self, tmp_path, monkeypatch
    ):
        built = _build_release(tmp_path / "rel")
        _admit(monkeypatch, built["space"])
        search = _reload_search(
            monkeypatch,
            MEDTERM4DS_TERMINOLOGY_DIR=str(built["base"]),
        )
        svc = search.SearchService()
        svc._ensure_concepts()
        assert svc._concepts_faiss is not None
        assert len(svc._concepts_meta) == 3
        assert set(svc._concepts_meta[0]) == {"canonical_id", "name"}

        class _Engine:
            space_id = built["space"]

            def _ensure_loaded(self):
                return None

            def _embed(self, query):
                return _unit_vec()

        svc._ensure_semantic = lambda: _Engine()
        results = svc._canonical_concept_search("metformin", count=3)
        assert results, "minimal anchors metadata must resolve to cards"
        assert any(r.canonical_id == "VAL-MED-1" for r in results)
        top = next(r for r in results if r.canonical_id == "VAL-MED-1")
        assert top.canonical_id == "VAL-MED-1"
        assert top.anchor_system == "RXNORM"
        assert top.anchor_code == "6809"
        assert top.patient_friendly_name == "Metformin"
        assert top.matched_via_display == "Metformin"

    def test_cache_info_reports_terminology(self, tmp_path, monkeypatch):
        built = _build_release(tmp_path / "rel")
        _admit(monkeypatch, built["space"])
        _reload_search(
            monkeypatch,
            MEDTERM4DS_TERMINOLOGY_DIR=str(built["base"]),
        )
        from medterm4ds.core import artifact_cache

        info = artifact_cache.cache_info()
        term_info = info["terminology_layout"]
        assert term_info["release"] == built["release"]
        assert term_info["present"] is True
        assert term_info["resolved"]["status"] == "ok"
        assert term_info["resolved"]["embedding_space_id"] == built["space"]


class TestDownloadGating:
    def test_existing_split_cache_is_not_replaced(self, tmp_path, monkeypatch):
        """auto mode: a local split unit suppresses the 3.7GB fetch."""
        built = _build_release(tmp_path / "rel")
        _admit(monkeypatch, built["space"])
        search = _reload_search(monkeypatch)
        split_models = search._SPLIT_ROOT / "models" / built["space"]
        split_models.mkdir(parents=True)
        (split_models / "model.safetensors").write_bytes(b"w")
        called = {"n": 0}

        def _boom(*a, **k):
            called["n"] += 1
            raise AssertionError("terminology download must not run")

        monkeypatch.setattr(term, "download", _boom)
        monkeypatch.setattr(term, "base_dir", lambda *a, **k: tmp_path / "none")
        assert search._resolve_terminology() is None
        assert called["n"] == 0

    def test_forced_layout_surfaces_download_errors(self, tmp_path, monkeypatch):
        built = _build_release(tmp_path / "rel")
        _admit(monkeypatch, built["space"])
        search = _reload_search(monkeypatch, MEDTERM4DS_LAYOUT="terminology")
        monkeypatch.setattr(term, "base_dir", lambda *a, **k: tmp_path / "none")
        monkeypatch.setattr(
            term, "download", lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("network down")
            )
        )
        with pytest.raises(RuntimeError, match="network down"):
            search._resolve_terminology()


class TestTerminologyDirOverrideAdoptsManifestRelease:
    """Regression (2026-11-05 default flip): with MEDTERM4DS_TERMINOLOGY_DIR
    pointing directly at a release dir, the manifest IS the release identity
    — resolve must not demand release() agreement (ts110 binding contract)."""

    def test_override_dir_with_foreign_default_release_resolves(self, tmp_path, monkeypatch):
        built = _build_release(tmp_path, release="2001.01.01.1")
        release_dir = built["base"]
        monkeypatch.setenv("MEDTERM4DS_TERMINOLOGY_DIR", str(release_dir))
        monkeypatch.delenv("MEDTERM4DS_TERMINOLOGY_RELEASE", raising=False)
        resolved = term.resolve(tmp_path, space_id=built["space"])
        assert resolved is not None
        assert resolved.release == "2001.01.01.1"
        assert resolved.lexical == release_dir / "lexical" / "2001.01.01.1"
