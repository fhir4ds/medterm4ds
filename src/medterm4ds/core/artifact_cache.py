"""Artifact-cache operations for the HF-hosted search artifacts.

Companion to ``services.search``'s revision-keyed cache layout: reports
what is cached, force-refreshes a revision, and lists the repository's
available revisions. Used by the ``medterm4ds data cache-*`` CLI commands.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

ARTIFACT_FAMILIES = ("canonical", "semantic", "lexical")

PROVENANCE_FILENAME = "artifact_provenance.json"


def _effective_paths() -> dict[str, Any]:
    """Import search lazily (heavy module) and summarize the cache layout."""
    from medterm4ds.services import search as _search

    return {
        "cache_dir": str(_search._CACHE_DIR),
        "revision_keyed": _search._CACHE_REVISION_KEYED,
        "repo_id": _search._HF_REPO_ID,
        "revision": _search._HF_REVISION,
        "provenance_path": str(Path(_search._CACHE_DIR) / PROVENANCE_FILENAME),
    }


def read_provenance(cache_dir: str | Path | None = None) -> dict[str, Any] | None:
    """Read the provenance marker for a cache dir (None if absent)."""
    path = Path(cache_dir) / PROVENANCE_FILENAME if cache_dir else None
    if path is None:
        path = Path(_effective_paths()["provenance_path"])
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def cache_info() -> dict[str, Any]:
    """Report the effective cache layout and per-family state."""
    info = _effective_paths()
    root = Path(info["cache_dir"])
    info["mode"] = (
        "revision-keyed (HF-managed)" if info["revision_keyed"]
        else "operator-managed (MEDTERM4DS_CACHE_DIR; used as-is)"
    )
    info["provenance"] = read_provenance(root)
    families: dict[str, dict[str, Any]] = {}
    for fam in ARTIFACT_FAMILIES:
        fam_dir = root / fam
        if fam_dir.is_dir():
            files = [p for p in fam_dir.iterdir() if p.is_file()]
            families[fam] = {
                "present": True,
                "files": len(files),
                "bytes": sum(p.stat().st_size for p in files),
                # Artifact-governance (plan §6.4): surface the family's
                # manifest when present so incident correlation can see
                # embedding_space_id / data_revision / lineage without
                # loading the artifacts (fhir4ds ask). Legacy layout has
                # no manifests — "absent" keeps current semantics.
                "manifest": read_manifest_summary(fam_dir),
            }
        else:
            families[fam] = {"present": False}
    info["families"] = families
    info["split_layout"] = _split_layout_summary()
    return info


def _split_layout_summary() -> dict[str, Any]:
    """Split models/<space>/ + data/<revision>/ cache state (Phase 3)."""
    from medterm4ds.services import search as _search

    summary: dict[str, Any] = {
        "mode": _search._LAYOUT_ENV,
        "split_root": str(_search._SPLIT_ROOT),
    }
    models_dir = _search._SPLIT_ROOT / _search.MODELS_DIRNAME
    if models_dir.is_dir():
        spaces = {}
        for d in sorted(models_dir.iterdir()):
            if d.is_dir():
                spaces[d.name] = read_manifest_summary(d)
        summary["models"] = spaces
    else:
        summary["models"] = {}
    data_dir = _search._SPLIT_ROOT / _search.DATA_DIRNAME
    if data_dir.is_dir():
        revs = {}
        for d in sorted(data_dir.iterdir(), key=lambda p: _search._natural_key(p.name)):
            if d.is_dir():
                revs[d.name] = read_manifest_summary(d)
        summary["data"] = revs
        local_latest = _search._local_latest_data_dir()
        if local_latest is not None:
            summary["resolved_data_revision"] = local_latest.name
        if _search._DATA_REVISION_ENV:
            summary["pinned_data_revision"] = _search._DATA_REVISION_ENV
    else:
        summary["data"] = {}
    return summary


def read_manifest_summary(directory: Path) -> dict[str, Any]:
    """Best-effort manifest summary for cache-info (never raises).

    Returns {"status": "absent"} for the legacy layout, {"status":
    "present", ...provenance keys} for governed artifacts, and a
    malformed marker (validation problems still surface at component
    load with the full ManifestError).
    """
    from medterm4ds.core.artifact_manifest import read_manifest

    try:
        manifest = read_manifest(directory)
    except Exception as exc:  # noqa: BLE001 — reporting path, not a control path
        return {"status": "malformed", "error": str(exc)}
    if manifest is None:
        return {"status": "absent"}
    summary: dict[str, Any] = {"status": "present"}
    for key in ("artifact_kind", "embedding_space_id", "data_revision",
                "render_policy_version"):
        if key in manifest:
            summary[key] = manifest[key]
    lineage = manifest.get("index_lineage")
    if isinstance(lineage, dict):
        summary["index_lineage"] = {
            k: lineage[k]
            for k in ("embedding_space_id", "built_against_canonical_build")
            if k in lineage
        }
    return summary


def cache_refresh(
    revision: str | None = None,
    *,
    families: tuple[str, ...] = ARTIFACT_FAMILIES,
    split: bool = False,
    data_revision: str | None = None,
) -> dict[str, Any]:
    """Force-download artifact families for a revision.

    Deletes the revision's cache subtree first so the download cannot be
    satisfied by stale files (the lazy loader never re-pulls existing
    paths). Only meaningful in revision-keyed mode; in operator-managed
    mode this raises rather than deleting a pipeline-managed directory.

    ``split=True`` migrates to the split layout instead: downloads
    models/<accepted space>/ + data/<latest or data_revision> from the
    repo's main branch (atomically, per family unit) on top of the legacy
    families. After the migration, layout resolution prefers the split
    artifacts; the legacy dirs become deletable.

    Returns a summary dict; raises RuntimeError/ImportError on failure.
    """
    from medterm4ds.services import search as _search

    if not _search._CACHE_REVISION_KEYED and revision is None:
        raise RuntimeError(
            "MEDTERM4DS_CACHE_DIR is set (operator-managed layout); "
            "cache-refresh would delete pipeline-managed files. Refresh "
            "that directory with its owning pipeline instead, or unset "
            "MEDTERM4DS_CACHE_DIR to use the revision-keyed cache."
        )

    if split:
        return _cache_refresh_split(data_revision=data_revision)

    repo_id = _search._HF_REPO_ID
    rev = revision or _search._HF_REVISION
    cache_root = (
        Path(_search._CACHE_DIR) if revision is None
        else (Path.home() / ".cache" / "medterm4ds") / rev
    )
    # With MEDTERM4DS_CACHE_DIR unset the cache dir is already revision-
    # keyed; with an explicit --revision we key under the DEFAULT root so
    # an explicit refresh never writes into an operator-managed dir.
    if _search._CACHE_REVISION_KEYED and revision is not None:
        default_root = Path.home() / ".cache" / "medterm4ds"
        if Path(_search._CACHE_DIR).parent != default_root:
            cache_root = default_root / rev

    patterns: list[str] = []
    for fam in families:
        patterns.append(f"{fam}/*")
        subdir = cache_root / fam
        if subdir.is_dir():
            shutil.rmtree(subdir)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ImportError(
            "huggingface_hub is required for cache-refresh. "
            "Install with: pip install huggingface_hub"
        ) from None
    import datetime
    import os

    snapshot_download(
        repo_id=repo_id,
        revision=rev,
        repo_type="model",
        local_dir=str(cache_root),
        allow_patterns=patterns,
        token=os.getenv("HF_TOKEN"),
    )
    (cache_root / PROVENANCE_FILENAME).write_text(json.dumps({
        "repo_id": repo_id,
        "revision": rev,
        "downloaded_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }, indent=2))
    return {
        "cache_dir": str(cache_root),
        "revision": rev,
        "repo_id": repo_id,
        "families": list(families),
    }


def _cache_refresh_split(data_revision: str | None = None) -> dict[str, Any]:
    """Migrate the cache to the split layout (models/ + data/ from main)."""
    from medterm4ds.services import search as _search

    model_dir = _search._download_split_model()
    data_dir = _search._download_split_data(data_revision)
    return {
        "cache_dir": str(_search._SPLIT_ROOT),
        "layout": "split",
        "model_dir": str(model_dir),
        "data_dir": str(data_dir),
        "data_revision": data_dir.name,
        "note": (
            "Split layout ready; layout resolution now prefers models/<space>/"
            " + data/<revision>. The legacy revision-keyed families under "
            f"{_search._CACHE_DIR} can be deleted once verified."
        ),
    }


def cache_list_remote() -> dict[str, Any]:
    """List the artifact repo's tags and branches (network call)."""
    from medterm4ds.services import search as _search

    try:
        from huggingface_hub import HfApi
    except ImportError:
        raise ImportError(
            "huggingface_hub is required for cache-list. "
            "Install with: pip install huggingface_hub"
        ) from None
    import os

    api = HfApi(token=os.getenv("HF_TOKEN"))
    refs = api.list_repo_refs(_search._HF_REPO_ID, repo_type="model")
    return {
        "repo_id": _search._HF_REPO_ID,
        "active_revision": _search._HF_REVISION,
        "tags": sorted(t.name for t in refs.tags),
        "branches": sorted(b.name for b in refs.branches),
    }
