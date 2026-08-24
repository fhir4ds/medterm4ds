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
            }
        else:
            families[fam] = {"present": False}
    info["families"] = families
    return info


def cache_refresh(revision: str | None = None, *, families: tuple[str, ...] = ARTIFACT_FAMILIES) -> dict[str, Any]:
    """Force-download artifact families for a revision.

    Deletes the revision's cache subtree first so the download cannot be
    satisfied by stale files (the lazy loader never re-pulls existing
    paths). Only meaningful in revision-keyed mode; in operator-managed
    mode this raises rather than deleting a pipeline-managed directory.

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
        )
    import os
    import datetime

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


def cache_list_remote() -> dict[str, Any]:
    """List the artifact repo's tags and branches (network call)."""
    from medterm4ds.services import search as _search

    try:
        from huggingface_hub import HfApi
    except ImportError:
        raise ImportError(
            "huggingface_hub is required for cache-list. "
            "Install with: pip install huggingface_hub"
        )
    import os

    api = HfApi(token=os.getenv("HF_TOKEN"))
    refs = api.list_repo_refs(_search._HF_REPO_ID, repo_type="model")
    return {
        "repo_id": _search._HF_REPO_ID,
        "active_revision": _search._HF_REVISION,
        "tags": sorted(t.name for t in refs.tags),
        "branches": sorted(b.name for b in refs.branches),
    }
