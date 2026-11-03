"""Canonical artifact-path resolution (AMUX-44).

Single source of truth for cache-root-derived default paths. No shipped
default may point at a machine-specific absolute path (the historical
``/mnt/d/...`` constants failed live in containers and on any machine
without the author's mount layout), and nothing may default into the
``fhir4px-model`` tree — that repo is a data home pending migration, not
a stable API surface. The published ``fhir4ds/terminology`` release
(provisioned under the cache root) is the source of truth for search
artifacts; ``services.search`` resolves it lazily from these defaults.

Resolution order everywhere in this module:
  1. explicit env var (``MEDTERM4DS_CACHE_DIR`` / ``MEDTERM4DS_HF_REVISION``)
  2. derived from the cache root (``~/.cache/medterm4ds/<revision>``)

The ``*_dir()`` / ``*_revision()`` helpers read the environment on every
call so test suites that ``importlib.reload`` consumer modules under a
patched env get fresh values. The module-level ``DEFAULT_*`` constants
freeze one evaluation at import — consumers compare against them as
"unpinned" sentinels, which requires stable values within a process.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

LEGACY_REVISION = "v0.0.5"


def hf_repo_id() -> str:
    """HF repo holding prebuilt artifacts (``MEDTERM4DS_HF_REPO_ID``)."""
    return os.getenv("MEDTERM4DS_HF_REPO_ID", "fhir4ds/medterm4ds")


def hf_revision() -> str:
    """Artifact-set revision tag (``MEDTERM4DS_HF_REVISION``)."""
    return os.getenv("MEDTERM4DS_HF_REVISION", LEGACY_REVISION)


def cache_dir() -> Path:
    """Artifact cache root.

    ``MEDTERM4DS_CACHE_DIR`` set → used as-is (operator-managed, not
    revision-keyed; the deploy.sh/data-dir contract). Otherwise
    revision-keyed under ``~/.cache/medterm4ds/<revision>/`` so switching
    ``MEDTERM4DS_HF_REVISION`` (or bumping the default at release)
    triggers a fresh download and rollback is instant.
    """
    env_root = os.getenv("MEDTERM4DS_CACHE_DIR")
    if env_root:
        return Path(env_root)
    return Path.home() / ".cache" / "medterm4ds" / hf_revision()


def cache_revision_keyed() -> bool:
    return not os.getenv("MEDTERM4DS_CACHE_DIR")


def warn_on_prerevision_cache() -> None:
    """Pre-revision cache detection (root-level family dirs): it cannot be
    attributed to a revision, so it is never silently adopted. Tell the
    operator instead of re-downloading behind their back."""
    cache = cache_dir()
    if not cache_revision_keyed():
        return
    legacy_root = Path.home() / ".cache" / "medterm4ds"
    if (
        cache != legacy_root
        and any((legacy_root / fam).is_dir() for fam in ("canonical", "semantic", "lexical"))
        and not (cache / "canonical").is_dir()
    ):
        logger.warning(
            "Pre-revision artifact cache detected at %s (canonical/semantic/"
            "lexical at the cache root). It will NOT be used because cached "
            "artifacts cannot be attributed to revision %s. Run "
            "`medterm4ds data cache-refresh` to fetch this revision, then "
            "delete the root-level copies.",
            legacy_root,
            hf_revision(),
        )


def terminology_root() -> Path:
    """Cache root holding ``terminology/<release>/`` (shared across revisions)."""
    if cache_revision_keyed():
        return Path.home() / ".cache" / "medterm4ds"
    return cache_dir()


# --- Import-time sentinels (one frozen evaluation; see module docstring) ---

_CACHE_DIR = cache_dir()
warn_on_prerevision_cache()

CACHE_DIR = _CACHE_DIR
CACHE_REVISION_KEYED = cache_revision_keyed()
HF_REPO_ID = hf_repo_id()
HF_REVISION = hf_revision()
TERMINOLOGY_ROOT = terminology_root()

DEFAULT_SEARCH_INDEX_DIR = str(_CACHE_DIR / "lexical")
DEFAULT_EMBEDDING_MODEL_DIR = str(_CACHE_DIR / "semantic")
DEFAULT_CANONICAL_VALUE_SETS_PATH = str(
    _CACHE_DIR / "canonical" / "canonical_anchor_value_sets.json"
)
DEFAULT_CANONICAL_CONCEPTS_INDEX = str(_CACHE_DIR / "canonical" / "canonical_concepts_faiss.index")
DEFAULT_CANONICAL_CONCEPTS_META = str(_CACHE_DIR / "canonical" / "canonical_concepts_metadata.json")

# Patient-friendly baseline artifacts are release reports, not part of the
# terminology release; they default to a reports root under the cache.
DEFAULT_FHIR4PX_BASELINE_DIR = str(TERMINOLOGY_ROOT / "reports" / "fhir4px")
