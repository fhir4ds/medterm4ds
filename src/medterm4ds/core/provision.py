"""Auto-provisioning for mt.connect().

Builds ``lookup.duckdb`` from the user's own UMLS RRF download (NLM UTS
API). Everything is cached so subsequent ``connect()`` calls are instant.

Derived search artifacts (BM25, SapBERT, canonical value sets) are NOT
provisioned here — they self-download from the ``fhir4ds/terminology``
release layout on first use (see ``core/terminology.py``).

One cache:
  - ``~/.medterm4ds/cache/lookup-{version}.duckdb`` — built locally,
    versioned by UMLS release. Multiple releases can coexist.

Override the medterm4ds cache root via ``MEDTERM4DS_HOME`` env var.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_UMLS_RELEASE = os.getenv("UMLS_RELEASE") or "2026AA"

# DuckDB database files carry the magic bytes "DUCK" at offset 8 of the main
# header block (the first 8 bytes are a checksum). QC-475/QC-476 (MEDIUM):
# the cache predicate was size-only (>1 MB), so a 2 MB file of zeros passed
# as "cached" and provision() handed it to duckdb.connect — every subsequent
# mt.connect() crashed with a raw IOException until the user manually deleted
# the file — while a valid 536 KB lookup DB was rejected in the other
# direction. Header-magic validation replaces the size floor; 4096 bytes is
# the size of the database header block itself, the minimum any real DB has.
_DUCKDB_MAGIC = b"DUCK"
_DUCKDB_HEADER_BYTES = 12
_LOOKUP_MIN_SIZE = 4096


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


def _is_usable_lookup_db(path: Path) -> bool:
    """QC-475/QC-476/QC-477: is this cache entry a usable DuckDB database?

    Must be a regular file (directories and DANGLING SYMLINKS return False
    from ``Path.is_file()`` without the ``FileNotFoundError`` a bare
    ``stat()`` raises — pre-fix one dangling symlink crashed BOTH public
    read APIs), at least one header block in size, and carrying the DuckDB
    magic bytes (so a truncated download or a zeros-filled file no longer
    passes as "cached").
    """
    try:
        if not path.is_file():
            return False
        if path.stat().st_size < _LOOKUP_MIN_SIZE:
            return False
        with path.open("rb") as fh:
            header = fh.read(_DUCKDB_HEADER_BYTES)
        return header[8:12] == _DUCKDB_MAGIC
    except OSError:
        # Optional-probe semantics: an entry that vanished between glob and
        # read (or is unreadable) is not a usable cached version.
        return False


def is_lookup_cached(
    version: str = DEFAULT_UMLS_RELEASE,
    cache_home: Path | None = None,
) -> bool:
    """Check if a usable lookup.duckdb exists for the given version."""
    return _is_usable_lookup_db(get_lookup_db_path(version, cache_home))


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

    logger.info("Building lookup.duckdb from UMLS %s (one-time, ~8 min)...", version)

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


def set_env_vars(
    db_path: Path,
    memory_profile: str = "fast",
) -> None:
    """Set MEDTERM4DS_* env vars so downstream services find the data.

    Called by provision() after the lookup build completes. Existing env
    vars are NOT overridden (user may have set custom paths). Search
    artifacts are NOT pinned here — they resolve from the
    ``fhir4ds/terminology`` release layout (see ``core/terminology.py``).
    """
    os.environ.setdefault("MEDTERM4DS_DB", str(db_path))
    os.environ.setdefault("MEDTERM4DS_MEMORY_PROFILE", memory_profile)


def provision(
    version: str = DEFAULT_UMLS_RELEASE,
    umls_api_key: str | None = None,
    cache_home: Path | None = None,
    memory_profile: str = "fast",
    offline: bool = False,
) -> Path:
    """Full provisioning: build lookup.duckdb + set env vars.

    Derived search artifacts (lexical/semantic/canonical) self-download
    from the ``fhir4ds/terminology`` release layout on first use — they
    are no longer provisioned here.

    Args:
        version: UMLS release tag (default ``2026AA``).
        umls_api_key: NLM UTS API key. Falls back to ``UMLS_API_KEY`` /
            ``UTS_API_KEY`` env vars.
        cache_home: Override cache root (default ``~/.medterm4ds/``).
        memory_profile: DuckDB memory profile (``low``, ``balanced``, ``fast``).
        offline: If True, skip all network calls. Use existing cache only;
            error if data is missing. A corrupt or truncated
            ``lookup-{version}.duckdb`` is NOT considered cached (header
            magic is verified), so offline mode fails fast with a clear
            error instead of handing a corrupt file to duckdb.connect
            (QC-476).

    Returns:
        Path to the lookup.duckdb file.

    Raises:
        RuntimeError: If offline mode is set and cache is missing, or if
            the UMLS API key is missing and lookup.duckdb needs to be built.
    """
    home = cache_home or resolve_cache_home()

    # Step 1: lookup.duckdb (critical — nothing works without it)
    expected_path = get_lookup_db_path(version, home)
    if is_lookup_cached(version, home):
        db_path = expected_path
        logger.info("lookup.duckdb cached (%s)", db_path.name)
    elif offline and expected_path.exists():
        # QC-476 (MEDIUM): the file is present but failed the header-magic
        # check (corrupt/truncated, e.g. an interrupted download). Say so and
        # name the remediation — pre-fix this handed the corrupt file to
        # duckdb.connect and every mt.connect() crashed with a raw
        # IOException until the user manually deleted it.
        raise RuntimeError(
            f"lookup-{version}.duckdb exists at {expected_path} but is not a "
            f"valid DuckDB database (corrupt or truncated download). Delete "
            f"it and run mt.connect() online to rebuild, or "
            f"mt.cache_clear() to drop it from the cache."
        )
    elif offline:
        raise RuntimeError(
            f"Offline mode is set but lookup-{version}.duckdb is not cached "
            f"at {expected_path}. Run mt.connect() online "
            f"first to build the cache."
        )
    else:
        db_path = build_lookup_db(
            version=version,
            api_key=umls_api_key,
            cache_home=home,
        )
        if not _is_usable_lookup_db(db_path):
            raise RuntimeError(
                f"Built lookup-{version}.duckdb at {db_path} failed its "
                f"header validation — the cache was not updated with a "
                f"usable database."
            )

    # Step 2: set env vars for downstream services. Search artifacts
    # ($search/$extract) self-download from the fhir4ds/terminology
    # release layout on first use — nothing to fetch here.
    set_env_vars(db_path, memory_profile=memory_profile)

    return db_path


# ============================================================================
# Cache inspection + management
# ============================================================================


def cache_info() -> dict[str, Any]:
    """Return a summary of the medterm4ds cache state.

    Includes paths, sizes, and UMLS versions present. Search artifacts
    are managed separately by ``core/artifact_cache.py`` (release layout
    at ``fhir4ds/terminology``).
    """
    import time as _time

    home = resolve_cache_home()
    cache_dir = home / "cache"

    # lookup.duckdb versions
    # QC-475 (MEDIUM): list only USABLE cache entries (DuckDB magic-verified,
    # regular files, non-empty version tag). Pre-fix cache_info advertised
    # zero-filled stubs, directories named lookup-*.duckdb, and a phantom ''
    # version from lookup-.duckdb while is_lookup_cached() said False for the
    # same entries — four public functions, four different predicates.
    versions: list[dict[str, Any]] = []
    total_lookup_size = 0
    if cache_dir.is_dir():
        for db_path in sorted(cache_dir.glob("lookup-*.duckdb")):
            version_tag = db_path.stem.replace("lookup-", "")
            if not version_tag or not _is_usable_lookup_db(db_path):
                continue
            stat = db_path.stat()
            versions.append(
                {
                    "version": version_tag,
                    "path": str(db_path),
                    "size_mb": round(stat.st_size / 1e6, 1),
                    "modified": _time.strftime("%Y-%m-%d %H:%M", _time.localtime(stat.st_mtime)),
                }
            )
            total_lookup_size += stat.st_size

    return {
        "cache_home": str(home),
        "lookup_dbs": versions,
        "lookup_total_mb": round(total_lookup_size / 1e6, 1),
    }


def cache_versions() -> list[str]:
    """Return the list of UMLS release versions cached locally.

    QC-475: only usable (DuckDB magic-verified) versions are reported, and
    a phantom ``''`` tag from a malformed ``lookup-.duckdb`` filename is
    excluded. Non-file entries (directories, dangling symlinks) are
    skipped instead of crashing the read (QC-477).
    """
    home = resolve_cache_home()
    cache_dir = home / "cache"
    if not cache_dir.is_dir():
        return []
    return sorted(
        p.stem.replace("lookup-", "")
        for p in cache_dir.glob("lookup-*.duckdb")
        if p.stem.replace("lookup-", "") and _is_usable_lookup_db(p)
    )


def cache_clear(
    keep: str | None = None,
    *,
    keep_current: bool = True,
) -> list[str]:
    """Remove old lookup.duckdb versions from the cache.

    Args:
        keep: Version to keep (e.g., ``"2026AA"``). If None, keeps the
            most recently modified version.
        keep_current: If True, also keep the version that mt.connect()
            would use by default (``DEFAULT_UMLS_RELEASE``).

    Returns:
        List of removed version tags.
    """
    home = resolve_cache_home()
    cache_dir = home / "cache"
    if not cache_dir.is_dir():
        return []

    # QC-475: only removable entries that are real usable cache files —
    # the pre-fix unfiltered glob treated stubs and directories as
    # removable, and its mtime sort key crashed on dangling symlinks
    # (QC-477).
    # CR-046 (review-5 finding 8): the sort-key stat() was itself unguarded
    # — a concurrent cache_clear / connect() download removing an entry
    # between the filter and the sort raised FileNotFoundError mid-clear
    # (TOCTOU sibling of QC-463/QC-477).
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    all_dbs = sorted(
        (
            p
            for p in cache_dir.glob("lookup-*.duckdb")
            if p.stem.replace("lookup-", "") and _is_usable_lookup_db(p)
        ),
        key=_mtime,
        reverse=True,
    )
    if not all_dbs:
        return []

    # Determine which to keep
    keep_versions: set[str] = set()
    if keep is not None:
        keep_versions.add(keep)
    if keep_current:
        # QC-465 sibling (HIGH QC-463's phantom-keep): an exported-but-empty
        # UMLS_RELEASE='' used to make DEFAULT_UMLS_RELEASE '' so this match
        # kept NOTHING — cache_clear(keep='2026AA') still deleted the
        # current release. DEFAULT_UMLS_RELEASE now falls back to 2026AA
        # when the env var is blank.
        keep_versions.add(DEFAULT_UMLS_RELEASE)
    # Always keep the most recent if nothing else is specified
    if not keep_versions:
        keep_versions.add(all_dbs[0].stem.replace("lookup-", ""))

    removed: list[str] = []
    for db_path in all_dbs:
        version = db_path.stem.replace("lookup-", "")
        if version in keep_versions:
            continue
        # QC-463 (HIGH): stat() ran AFTER unlink() on the deleted path, so
        # the FIRST removable version was deleted and the function crashed
        # with FileNotFoundError, leaving the rest behind. Capture the size
        # before unlinking.
        # CR-046 (review-5 finding 8): guard the pair — a concurrent process
        # can remove the entry between the filter and here; one vanishing
        # entry must not abort the remaining clears.
        try:
            size_mb = db_path.stat().st_size / 1e6
            db_path.unlink()
        except OSError:
            continue
        removed.append(version)
        logger.info("Removed cached lookup-%s.duckdb (%.0f MB)", version, size_mb)

    return removed
