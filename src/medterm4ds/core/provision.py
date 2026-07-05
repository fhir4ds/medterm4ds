"""Auto-provisioning for mt.connect().

Builds ``lookup.duckdb`` from the user's own UMLS RRF download and
fetches derived artifacts (BM25, SapBERT, patient_friendly) from the
open Hugging Face dataset. Everything is cached so subsequent ``connect()``
calls are instant.

Two caches:
  - ``~/.medterm4ds/cache/lookup-{version}.duckdb`` — built locally,
    versioned by UMLS release. Multiple releases can coexist.
  - Standard HF cache (``~/.cache/huggingface/``) — derived artifacts
    via ``snapshot_download()``. Shared with other HF-using tools.

Override the medterm4ds cache root via ``MEDTERM4DS_HOME`` env var.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_UMLS_RELEASE = os.getenv("UMLS_RELEASE", "2026AA")
DEFAULT_HF_DATASET = os.getenv("MEDTERM4DS_HF_DATASET", "joelmontavon/medterm4ds-data")

# Minimum file size to consider lookup.duckdb "built" (not a stub/corrupt file).
_LOOKUP_MIN_SIZE = 1_000_000  # 1 MB


def resolve_cache_home() -> Path:
    """Return the medterm4ds cache root.

    Default: ``~/.medterm4ds/``.
    Override: ``MEDTERM4DS_HOME`` env var.
    """
    home = os.getenv("MEDTERM4DS_HOME", str(Path.home() / ".medterm4ds"))
    path = Path(home)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_lookup_db_path(
    version: str = DEFAULT_UMLS_RELEASE,
    cache_home: Path | None = None,
) -> Path:
    """Return the expected path for a versioned lookup.duckdb."""
    home = cache_home or resolve_cache_home()
    return home / "cache" / f"lookup-{version}.duckdb"


def is_lookup_cached(
    version: str = DEFAULT_UMLS_RELEASE,
    cache_home: Path | None = None,
) -> bool:
    """Check if a usable lookup.duckdb exists for the given version."""
    db_path = get_lookup_db_path(version, cache_home)
    return db_path.exists() and db_path.stat().st_size > _LOOKUP_MIN_SIZE


def _resolve_api_key(umls_api_key: str | None) -> str:
    """Resolve the UMLS API key from parameter or env."""
    key = umls_api_key or os.getenv("UMLS_API_KEY") or os.getenv("UTS_API_KEY")
    if not key:
        raise RuntimeError(
            "UMLS API key required to build lookup.duckdb.\n"
            "Get one (free) at https://www.nlm.nih.gov/account/\n"
            "Then either:\n"
            "  - Set UMLS_API_KEY environment variable\n"
            "  - Pass umls_api_key= to mt.connect()\n"
            "  - Run: python -m medterm4ds.setup"
        )
    return key.strip()


def build_lookup_db(
    version: str = DEFAULT_UMLS_RELEASE,
    api_key: str | None = None,
    cache_home: Path | None = None,
) -> Path:
    """Download UMLS RRF and build lookup.duckdb.

    Returns the path to the built database. Raises RuntimeError on
    download/build failure.
    """
    resolved_key = _resolve_api_key(api_key)
    home = cache_home or resolve_cache_home()
    db_path = get_lookup_db_path(version, home)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Building lookup.duckdb from UMLS %s (one-time, ~8 min)...", version
    )

    # Late import — avoids pulling duckdb into the import chain for users
    # who just want to check cache state.
    from medterm4ds.services.data_setup import download_release
    from medterm4ds.services.lookup_builder import build_lookup_from_rrf

    with tempfile.TemporaryDirectory(prefix="medterm4ds-umls-") as tmp:
        tmp_path = Path(tmp)

        # Download + extract UMLS release
        zip_path = download_release(
            output_dir=str(tmp_path),
            api_key=resolved_key,
            release_version=version,
            extract=True,
        )
        logger.info("  Downloaded: %s", zip_path.name)

        # Find META directory containing MRCONSO.RRF
        extract_dir = tmp_path / zip_path.stem
        meta_files = list(extract_dir.rglob("MRCONSO.RRF"))
        if not meta_files:
            raise RuntimeError(f"Could not find MRCONSO.RRF under {extract_dir}")
        rrf_dir = meta_files[0].parent
        logger.info("  RRF: %s", rrf_dir)

        # Build filtered lookup.duckdb directly (no intermediate)
        build_lookup_from_rrf(rrf_dir, db_path)

        shutil.rmtree(extract_dir, ignore_errors=True)

    size_mb = db_path.stat().st_size / 1e6
    logger.info("  Done: %s (%.0f MB)", db_path, size_mb)
    return db_path


def download_derived_artifacts(
    hf_token: str | None = None,
    hf_dataset: str = DEFAULT_HF_DATASET,
) -> dict[str, Path]:
    """Download BM25, SapBERT, and patient_friendly from the open HF dataset.

    These are derivative works (transformed beyond recognition from UMLS
    source strings) and are redistributable under the UMLS license. No
    gating — anyone can download.

    Returns a dict with keys:
      - ``baseline_dir``: directory containing patient_friendly_*.json
      - ``bm25_dir``: directory containing *_bm25.json
      - ``sapbert_dir``: directory containing model.safetensors + FAISS indexes

    On failure (network down, HF unavailable), returns an empty dict and
    logs a warning. Lookup/mapping/hierarchy operations still work without
    these; only $search and $extract are affected.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        logger.warning(
            "huggingface_hub not installed — derived artifacts (BM25, SapBERT) "
            "will not be available. Install with: pip install huggingface_hub"
        )
        return {}

    try:
        logger.info("Downloading derived artifacts from HF: %s...", hf_dataset)
        local_path = snapshot_download(
            repo_id=hf_dataset,
            repo_type="dataset",
            token=hf_token or os.getenv("HF_TOKEN"),
            allow_patterns=["patient_friendly_*.json", "bm25/*", "sapbert/*"],
        )
    except Exception as exc:
        logger.warning(
            "Derived artifact download failed: %s: %s. "
            "$search and $extract will not be available; "
            "lookup/mapping/hierarchy operations still work.",
            type(exc).__name__, exc,
        )
        return {}

    base = Path(local_path)
    return {
        "baseline_dir": base,
        "bm25_dir": base / "bm25",
        "sapbert_dir": base / "sapbert",
    }


def set_env_vars(
    db_path: Path,
    derived: dict[str, Path],
    memory_profile: str = "balanced",
) -> None:
    """Set MEDTERM4DS_* env vars so downstream services find the data.

    Called by provision() after build/download completes. Existing env
    vars are NOT overridden (user may have set custom paths).
    """
    os.environ.setdefault("MEDTERM4DS_DB", str(db_path))
    os.environ.setdefault("MEDTERM4DS_MEMORY_PROFILE", memory_profile)

    if "baseline_dir" in derived:
        os.environ.setdefault(
            "MEDTERM4DS_FHIR4PX_BASELINE", str(derived["baseline_dir"])
        )
    if "bm25_dir" in derived:
        os.environ.setdefault(
            "MEDTERM4DS_SEARCH_INDEX_DIR", str(derived["bm25_dir"])
        )
    if "sapbert_dir" in derived:
        os.environ.setdefault(
            "MEDTERM4DS_EMBEDDING_MODEL_DIR", str(derived["sapbert_dir"])
        )


def provision(
    version: str = DEFAULT_UMLS_RELEASE,
    umls_api_key: str | None = None,
    cache_home: Path | None = None,
    hf_token: str | None = None,
    memory_profile: str = "balanced",
    offline: bool = False,
) -> Path:
    """Full provisioning: build lookup.duckdb + download derived + set env vars.

    Args:
        version: UMLS release tag (default ``2026AA``).
        umls_api_key: NLM UTS API key. Falls back to ``UMLS_API_KEY`` /
            ``UTS_API_KEY`` env vars.
        cache_home: Override cache root (default ``~/.medterm4ds/``).
        hf_token: Hugging Face token (optional — derived dataset is open).
        memory_profile: DuckDB memory profile (``low``, ``balanced``, ``high``).
        offline: If True, skip all network calls. Use existing cache only;
            error if data is missing.

    Returns:
        Path to the lookup.duckdb file.

    Raises:
        RuntimeError: If offline mode is set and cache is missing, or if
            the UMLS API key is missing and lookup.duckdb needs to be built.
    """
    home = cache_home or resolve_cache_home()

    # Step 1: lookup.duckdb (critical — nothing works without it)
    if is_lookup_cached(version, home):
        db_path = get_lookup_db_path(version, home)
        logger.info("lookup.duckdb cached (%s)", db_path.name)
    elif offline:
        raise RuntimeError(
            f"Offline mode is set but lookup-{version}.duckdb is not cached "
            f"at {get_lookup_db_path(version, home)}. Run mt.connect() online "
            f"first to build the cache."
        )
    else:
        db_path = build_lookup_db(
            version=version,
            api_key=umls_api_key,
            cache_home=home,
        )

    # Step 2: derived artifacts (optional — $search/$extract only)
    derived: dict[str, Path] = {}
    if not offline:
        derived = download_derived_artifacts(hf_token=hf_token)

    # Step 3: set env vars for downstream services
    set_env_vars(db_path, derived, memory_profile=memory_profile)

    return db_path
