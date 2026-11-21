"""``fhir4ds/athena`` Parquet snapshot (standalone pinned HF repo).

Mirrors the ``fhir4ds/associations`` family (services/associations.py,
the established standalone-repo pattern): own release pin, fail-loud
manifest validation, stages-then-swaps download, explicit-pin-is-an-
operator-contract, empty-pin-is-unset (FDD .temp/athena_parquet_fdd.md
decision C).

Local layout (the HF repo content lands verbatim under the release
dir; built by ``scripts/build_athena_parquet.py``)::

    athena/<release>/manifest.json
    athena/<release>/MD5SUMS
    athena/<release>/concept/vocabulary_id=<V>/*.parquet  # partitioned
    athena/<release>/concept_ancestor.parquet
    athena/<release>/concept_relationship.parquet
    athena/<release>/vocabulary.parquet

Engine serving (decision C: NO ingest step): :func:`get_athena_snapshot`
attaches the local snapshot lazily, single-flight, as READ-ONLY DuckDB
views named exactly like the ``athena_*`` tables produced by
``services/athena.py:ingest_athena`` — no table copies, so
``_do_translate_athena`` produces identical answers over either
provider (pinned by tests/test_athena_parquet.py parity probes). The
engine path never downloads: acquisition happens through the
``medterm4ds data cache-refresh --athena`` stage or the Space startup
hook; an absent snapshot is an honest absence, not an error.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ATHENA_DIRNAME = "athena"
DEFAULT_ATHENA_REPO_ID = "fhir4ds/athena"
DEFAULT_ATHENA_RELEASE = "2026.11.16.1"
DEFAULT_ATHENA_HF_REVISION = "main"
ATHENA_PARQUET_FORMAT = "athena_parquet_v1"


class AthenaSnapshotError(RuntimeError):
    """A snapshot release is present but violates the contract (or is
    pinned-but-absent). Never silently serve."""


class AthenaSnapshotAbsentError(AthenaSnapshotError):
    """No local snapshot at the resolved location (the honest-absence
    signal the engine seam turns into the unchanged 400 remedy)."""


def repo_id() -> str:
    pinned = (os.getenv("MEDTERM4DS_ATHENA_REPO_ID") or "").strip()
    return pinned or DEFAULT_ATHENA_REPO_ID


def release() -> str:
    # Empty/whitespace-only env pin is unset (EV03-QA-01/02 mirrors).
    pinned = (os.getenv("MEDTERM4DS_ATHENA_RELEASE") or "").strip()
    return pinned or DEFAULT_ATHENA_RELEASE


def release_explicitly_pinned() -> bool:
    return bool(
        (os.getenv("MEDTERM4DS_ATHENA_RELEASE") or "").strip()
        or (os.getenv("MEDTERM4DS_ATHENA_DIR") or "").strip()
    )


def hf_revision() -> str:
    pinned = (os.getenv("MEDTERM4DS_ATHENA_HF_REVISION") or "").strip()
    return pinned or DEFAULT_ATHENA_HF_REVISION


def dir_override() -> Path | None:
    override = (os.getenv("MEDTERM4DS_ATHENA_DIR") or "").strip()
    return Path(override) if override else None


def base_dir(cache_root: Path, rel: str | None = None) -> Path:
    override = dir_override()
    if override is not None:
        return override
    return Path(cache_root) / ATHENA_DIRNAME / (rel or release())


def athena_root() -> Path:
    """Cache root holding ``athena/<release>/`` (shared with the
    terminology layout root — same as associations_root)."""
    from medterm4ds.core.paths import terminology_root

    return terminology_root()


def is_present(base: Path) -> bool:
    return (base / "manifest.json").is_file()


# -- Manifest contract (fail-loud, mirrors validate_manifest) --------


def _read_manifest(base: Path) -> dict:
    try:
        return json.loads((base / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise AthenaSnapshotError(
            f"athena snapshot at {base} has an unreadable manifest.json"
        ) from exc


def validate_manifest(manifest: dict, base: Path, rel: str) -> None:
    """Fail-loud manifest contract: release identity, format, CPT
    attestation, row counts (FDD §1)."""
    if manifest.get("release") != rel:
        raise AthenaSnapshotError(
            f"athena snapshot manifest at {base} declares release "
            f"{manifest.get('release')!r}, expected {rel!r}"
        )
    if manifest.get("format") != ATHENA_PARQUET_FORMAT:
        raise AthenaSnapshotError(
            f"athena snapshot manifest at {base} declares format "
            f"{manifest.get('format')!r}, expected "
            f"{ATHENA_PARQUET_FORMAT!r}. Refusing to serve a drifted "
            "artifact format (upgrade medterm4ds or pin "
            "MEDTERM4DS_ATHENA_RELEASE to a compatible release)."
        )
    if not isinstance(manifest.get("cpt_excluded"), bool):
        raise AthenaSnapshotError(
            f"athena snapshot manifest at {base} is missing the "
            "cpt_excluded boolean attestation (decision B — never "
            "serve an unattested snapshot)."
        )
    counts = manifest.get("row_counts")
    if not isinstance(counts, dict) or not counts:
        raise AthenaSnapshotError(
            f"athena snapshot manifest at {base} is missing per-table "
            "row_counts."
        )


def resolve(cache_root: Path, *, rel: str | None = None) -> tuple[Path, dict]:
    """Validate and locate the local snapshot release.

    Returns (base_dir, manifest); raises AthenaSnapshotAbsentError when
    absent and AthenaSnapshotError when present-but-invalid. A DIR
    override's manifest is the release identity (mirrors the
    terminology/associations binding contract)."""
    rel = rel or release()
    base = base_dir(cache_root, rel)
    if not is_present(base):
        raise AthenaSnapshotAbsentError(
            f"athena snapshot release {rel} is not present at {base}"
        )
    manifest = _read_manifest(base)
    if dir_override() is not None:
        rel = manifest.get("release") or rel
    validate_manifest(manifest, base, rel)
    _check_files(base)
    return base, manifest


def _check_files(base: Path) -> None:
    required = [
        base / "concept",
        base / "concept_ancestor.parquet",
        base / "concept_relationship.parquet",
        base / "vocabulary.parquet",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise AthenaSnapshotError(
            f"athena snapshot at {base} is incomplete: missing "
            f"{missing}"
        )
    if not any((base / "concept").rglob("*.parquet")):
        raise AthenaSnapshotError(
            f"athena snapshot at {base} carries no parquet files under "
            "concept/ (incomplete download — re-run 'medterm4ds data "
            "cache-refresh --athena')."
        )


def download(
    cache_root: Path,
    *,
    rel: str | None = None,
    token: str | None = None,
) -> Path:
    """Atomically fetch one release (stages into ``.tmp-*`` then swaps;
    never pre-deletes — the existing release is renamed aside and
    restored if the download fails, mirroring associations.download /
    EV07-QA-01/EV03-QA-04)."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ImportError(
            "huggingface_hub is required to auto-download the athena "
            "snapshot. Install with: pip install huggingface_hub\n"
            f"Or manually download from https://huggingface.co/{repo_id()}"
        ) from None

    rel = rel or release()
    base = base_dir(cache_root, rel)
    if base.exists() and not base.is_dir():
        raise AthenaSnapshotError(
            f"athena snapshot base {base} exists but is not a directory"
        )
    patterns = [
        "manifest.json",
        "MD5SUMS",
        "concept/**",
        "*.parquet",
    ]
    tmp = base.parent / f".tmp-{base.name}-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Downloading athena snapshot from Hugging Face (%s, %s)...",
        repo_id(), rel,
    )
    try:
        snapshot_download(
            repo_id=repo_id(),
            revision=hf_revision(),
            repo_type="model",
            local_dir=str(tmp),
            allow_patterns=patterns,
            token=token if token is not None else os.getenv("HF_TOKEN"),
        )
        validate_manifest(_read_manifest(tmp), tmp, rel)
        _check_files(tmp)
        base.parent.mkdir(parents=True, exist_ok=True)
        stale: Path | None = None
        if base.exists():
            stale = base.parent / f".stale-{base.name}-{os.getpid()}"
            base.rename(stale)
        try:
            tmp.rename(base)
        except Exception:
            if stale is not None:
                stale.rename(base)
            raise
        if stale is not None:
            shutil.rmtree(stale, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return base


# -- Serving attach (lazy, single-flight, read-only views) -----------


@dataclass(frozen=True)
class AthenaSnapshot:
    """Attached snapshot: validated manifest + a DuckDB connection
    whose views mirror the ingest-athena table schemas exactly."""

    release: str
    base: Path
    manifest: dict[str, Any]
    con: Any  # duckdb.DuckDBPyConnection


def _sql_literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _open_views(base: Path):
    """Create the read-only view connection (no table copies). Column
    names/types are pinned in lockstep with ingest_athena's staging
    CREATE TABLE schemas — the parity probes assert identical answers
    over both providers."""
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute(
        f"""
        CREATE VIEW athena_concept AS
        SELECT
            CAST(concept_id AS BIGINT) AS concept_id,
            concept_name, domain_id,
            CAST(vocabulary_id AS VARCHAR) AS vocabulary_id,
            concept_class_id, standard_concept, concept_code,
            valid_start_date, valid_end_date, invalid_reason
        FROM read_parquet(
            '{_sql_literal(base / 'concept')}/**/*.parquet',
            hive_partitioning=true)
        """
    )
    for table in ("concept_ancestor", "concept_relationship"):
        con.execute(
            f"""
            CREATE VIEW athena_{table} AS
            SELECT * FROM read_parquet(
                '{_sql_literal(base / f'{table}.parquet')}')
            """
        )
    con.execute(
        f"""
        CREATE VIEW athena_vocabulary AS
        SELECT * FROM read_parquet(
            '{_sql_literal(base / 'vocabulary.parquet')}')
        """
    )
    return con


_SNAPSHOT_SINGLETON: AthenaSnapshot | None = None
_SNAPSHOT_LOCK = threading.Lock()


def get_athena_snapshot() -> AthenaSnapshot:
    """Attach the LOCAL snapshot lazily, single-flight. Raises
    AthenaSnapshotAbsentError when absent (the engine never downloads
    on the request path — acquisition is cache-refresh/Space-startup's
    job) and AthenaSnapshotError loud when present-but-invalid."""
    global _SNAPSHOT_SINGLETON
    if _SNAPSHOT_SINGLETON is not None:
        return _SNAPSHOT_SINGLETON
    with _SNAPSHOT_LOCK:
        if _SNAPSHOT_SINGLETON is not None:
            return _SNAPSHOT_SINGLETON
        rel = release()
        if dir_override() is not None:
            rel_override = None  # identity comes from the manifest
        else:
            rel_override = rel
        resolved_base, manifest = resolve(athena_root(), rel=rel_override)
        resolved_rel = manifest.get("release") or rel
        _SNAPSHOT_SINGLETON = AthenaSnapshot(
            release=resolved_rel,
            base=resolved_base,
            manifest=manifest,
            con=_open_views(resolved_base),
        )
        logger.info(
            "Attached athena parquet snapshot %s at %s "
            "(cpt_excluded=%s, row_counts=%s)",
            resolved_rel, resolved_base,
            _SNAPSHOT_SINGLETON.manifest.get("cpt_excluded"),
            _SNAPSHOT_SINGLETON.manifest.get("row_counts"),
        )
        return _SNAPSHOT_SINGLETON


def reset_athena_snapshot_for_testing() -> None:
    """Drop the singleton so the next call re-resolves under a patched
    environment (established test pattern)."""
    global _SNAPSHOT_SINGLETON
    with _SNAPSHOT_LOCK:
        if _SNAPSHOT_SINGLETON is not None:
            try:
                _SNAPSHOT_SINGLETON.con.close()
            except Exception:  # noqa: BLE001 — teardown path
                pass
        _SNAPSHOT_SINGLETON = None
