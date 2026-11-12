"""``fhir4ds/terminology`` artifact layout (release-keyed, 2026-10-31 repoint).

The terminology repo publishes one self-contained release per dated tag::

    manifest.json
    canonical/<release>/{value_sets.json, claimant_details.json,
                         decomposition_links.json}
    lexical/<release>/<category>.json
    semantic/<release>/{<category>.faiss, <category>_metadata.json,
                        anchors.faiss, anchors_metadata.json}
    model/<embedding_space_id>/...

This module owns the layout contract: release detection, path mapping,
manifest validation, embedding-space admission (via content fingerprint),
and the atomic per-release download. ``services.search`` consumes it as the
preferred layout; ``MEDTERM4DS_LAYOUT=legacy|split`` keeps the older
layouts reachable for rollback.

Nothing here imports ``services.search`` (that module passes its cache
root in), so the dependency direction stays core → services.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

TERMINOLOGY_DIRNAME = "terminology"
DEFAULT_TERMINOLOGY_REPO_ID = "fhir4ds/terminology"
DEFAULT_TERMINOLOGY_RELEASE = "2026.11.12.1"

# Canonical render policy of the published release. It is a property of the
# canonical build that produced the embedded texts (docs/plans/
# artifact-governance-plan.md §3); the terminology manifest does not carry
# it, so the consumer pins it per release. A mismatch surfaces as a
# different computed embedding space id (loud ManifestError).
DEFAULT_RENDER_POLICY_VERSION = "rp_1891c4cb719d"

SEMANTIC_CATEGORIES = (
    "condition",
    "lab",
    "medication",
    "procedure",
    "vaccine",
    "body_structure",
)

_FAMILY_FILES = {
    "canonical": ("value_sets.json", "claimant_details.json",
                  "decomposition_links.json"),
}


class TerminologyLayoutError(RuntimeError):
    """A terminology release is present but does not satisfy the contract."""


@dataclass(frozen=True)
class TerminologyPaths:
    """Resolved local paths for one terminology release."""

    repo_id: str
    release: str
    base: Path          # <cache>/terminology/<release>
    canonical: Path     # <base>/canonical/<release>
    lexical: Path       # <base>/lexical/<release>
    semantic: Path      # <base>/semantic/<release>
    model: Path         # <base>/model/<space_id>
    space_id: str
    manifest: dict


def repo_id() -> str:
    return os.getenv("MEDTERM4DS_TERMINOLOGY_REPO_ID", DEFAULT_TERMINOLOGY_REPO_ID)


def release() -> str:
    # EV03-QA-01 fix (2026-11-11 evolution): an explicitly-set but EMPTY
    # env var is "unset", not a pin — `os.getenv(...) or DEFAULT` mirrors
    # the embedding-space knob's fail-loud/empty semantics instead of
    # silently diverging from DEFAULT_TERMINOLOGY_RELEASE.
    return os.getenv("MEDTERM4DS_TERMINOLOGY_RELEASE") or DEFAULT_TERMINOLOGY_RELEASE


def release_explicitly_pinned() -> bool:
    """True when the operator pinned MEDTERM4DS_TERMINOLOGY_RELEASE.

    An explicit pin is an operator contract: the pinned release must be
    fetched and served even when a servable legacy/split cache exists
    locally (otherwise the pin silently falls through to the local
    artifacts — seen live during the 2026.11.05.1 flip, 2026-11-05).
    """
    return bool(os.getenv("MEDTERM4DS_TERMINOLOGY_RELEASE"))


def render_policy_version() -> str:
    return os.getenv(
        "MEDTERM4DS_RENDER_POLICY_VERSION", DEFAULT_RENDER_POLICY_VERSION
    )


def base_dir(
    cache_root: Path, rel: str | None = None
) -> Path:
    """Local directory holding release dirs for a cache root."""
    override = os.getenv("MEDTERM4DS_TERMINOLOGY_DIR")
    if override:
        return Path(override)
    return Path(cache_root) / TERMINOLOGY_DIRNAME / (rel or release())


def family_dir(base: Path, family: str, rel: str) -> Path:
    return base / family / rel


def is_present(base: Path) -> bool:
    return (base / "manifest.json").is_file()


def _read_manifest(base: Path) -> dict:
    try:
        return json.loads((base / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise TerminologyLayoutError(
            f"terminology release at {base} has an unreadable manifest.json"
        ) from exc


def _check_family_files(paths: TerminologyPaths) -> None:
    missing: list[str] = []
    for name in _FAMILY_FILES["canonical"]:
        if not (paths.canonical / name).is_file():
            missing.append(f"canonical/{paths.release}/{name}")
    for cat in SEMANTIC_CATEGORIES:
        for name, fam in (
            (f"{cat}.json", paths.lexical),
            (f"{cat}.faiss", paths.semantic),
            (f"{cat}_metadata.json", paths.semantic),
        ):
            if not (fam / name).is_file():
                missing.append(f"{fam.name}/{paths.release}/{name}")
    for name in ("anchors.faiss", "anchors_metadata.json"):
        if not (paths.semantic / name).is_file():
            missing.append(f"semantic/{paths.release}/{name}")
    for name in ("model.safetensors", "config.json", "tokenizer.json"):
        if not (paths.model / name).is_file():
            missing.append(f"model/{paths.space_id}/{name}")
    if missing:
        raise TerminologyLayoutError(
            "terminology release at "
            f"{paths.base} is incomplete; missing: {', '.join(missing[:6])}"
            + (" …" if len(missing) > 6 else "")
        )


def _verify_space(paths: TerminologyPaths) -> None:
    """Content-addressed admission: the model dir must fingerprint to its name."""
    from medterm4ds.core.artifact_manifest import (
        fingerprint_model_dir,
    )

    manifest = paths.manifest
    families = manifest.get("families") or {}
    declared = (families.get("model") or {}).get("embedding_space_id")
    if declared != paths.space_id:
        raise TerminologyLayoutError(
            f"terminology manifest declares model space {declared!r} but the "
            f"model directory is {paths.space_id!r}. The serving space and "
            "the release's encoder must agree — pin "
            "MEDTERM4DS_EMBEDDING_SPACE to the release's declared space "
            "(or update DEFAULT_SERVING_EMBEDDING_SPACE alongside the "
            "release rotation)."
        )
    try:
        fp = fingerprint_model_dir(
            paths.model, render_policy_version=render_policy_version()
        )
    except (OSError, ValueError) as exc:
        raise TerminologyLayoutError(
            f"cannot fingerprint model unit at {paths.model}: {exc}"
        ) from exc
    computed = fp.space_id()
    if computed != paths.space_id:
        raise TerminologyLayoutError(
            f"model unit at {paths.model} fingerprints to {computed}, not the "
            f"declared {paths.space_id} (render policy "
            f"{render_policy_version()}); refusing to serve mismatched vectors"
        )


def resolve(
    cache_root: Path,
    *,
    space_id: str | None = None,
    rel: str | None = None,
) -> TerminologyPaths | None:
    """Resolve the local terminology release, or None when absent.

    Raises TerminologyLayoutError when a release directory exists but
    violates the contract (incomplete family, foreign space, fingerprint
    mismatch) — a malformed published unit must never silently serve.
    """
    if space_id is None:
        from medterm4ds.core.artifact_manifest import effective_embedding_space

        space_id = effective_embedding_space()
    rel = rel or release()
    base = base_dir(cache_root, rel)
    if not is_present(base):
        return None
    manifest = _read_manifest(base)
    if os.getenv("MEDTERM4DS_TERMINOLOGY_DIR") and manifest.get("release"):
        # The operator pinned the release DIRECTORY — its manifest is the
        # release identity. Demanding release() to agree made the binding
        # brittle across default-release bumps (ts110 binding contract,
        # 2026-11-05 flip).
        rel = manifest["release"]
    elif manifest.get("release") != rel:
        raise TerminologyLayoutError(
            f"terminology manifest at {base} declares release "
            f"{manifest.get('release')!r}, expected {rel!r}"
        )
    families = manifest.get("families") or {}
    for fam in ("canonical", "lexical", "semantic"):
        declared = (families.get(fam) or {}).get("release")
        if declared != rel:
            raise TerminologyLayoutError(
                f"terminology family {fam!r} declares release {declared!r}, "
                f"expected {rel!r}"
            )
    paths = TerminologyPaths(
        repo_id=repo_id(),
        release=rel,
        base=base,
        canonical=family_dir(base, "canonical", rel),
        lexical=family_dir(base, "lexical", rel),
        semantic=family_dir(base, "semantic", rel),
        model=base / "model" / space_id,
        space_id=space_id,
        manifest=manifest,
    )
    _verify_space(paths)
    _check_family_files(paths)
    return paths


def download(
    cache_root: Path,
    *,
    rel: str | None = None,
    space_id: str | None = None,
    families: tuple[str, ...] = ("canonical", "lexical", "semantic", "model"),
) -> TerminologyPaths:
    """Atomically fetch one release into the cache and resolve it.

    Downloads into a sibling temp dir, then renames into place, so a
    partially fetched release is never visible to the resolver.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ImportError(
            "huggingface_hub is required to auto-download search artifacts.\n"
            "Install with: pip install huggingface_hub\n"
            f"Or manually download from https://huggingface.co/{repo_id()}"
        ) from None

    if space_id is None:
        from medterm4ds.core.artifact_manifest import effective_embedding_space

        space_id = effective_embedding_space()
    rel = rel or release()
    base = base_dir(cache_root, rel)
    if is_present(base):
        resolved = resolve(cache_root, space_id=space_id, rel=rel)
        if resolved is not None:
            return resolved

    patterns = ["manifest.json", "NOTICE.md"]
    for fam in families:
        if fam == "model":
            patterns.append(f"model/{space_id}/*")
        else:
            patterns.append(f"{fam}/{rel}/*")

    tmp = base.parent / f".tmp-{base.name}-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Downloading terminology artifacts from Hugging Face (%s, %s)...",
        repo_id(), rel,
    )
    snapshot_download(
        repo_id=repo_id(),
        revision=rel,
        repo_type="model",
        local_dir=str(tmp),
        allow_patterns=patterns,
        token=os.getenv("HF_TOKEN"),
    )
    base.parent.mkdir(parents=True, exist_ok=True)
    if base.exists():
        shutil.rmtree(base)
    tmp.rename(base)
    resolved = resolve(cache_root, space_id=space_id, rel=rel)
    if resolved is None:
        raise TerminologyLayoutError(
            f"download completed but release {rel} is not resolvable at {base}"
        )
    return resolved
