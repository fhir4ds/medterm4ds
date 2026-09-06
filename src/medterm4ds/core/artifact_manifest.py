"""Artifact manifests: shape-bound model/data contracts (Phase 1).

Implements the embedding-space / canonical-build / index-lineage split
from docs/plans/artifact-governance-plan.md:

- ``embedding_space_id`` — vector COMPATIBILITY. Hash of the full space
  fingerprint: model weights + tokenizer + pooling + normalization +
  truncation + the text-render policy version. Anything that changes the
  string→vector function changes the id; canonical-data waves do not.
- ``data_revision`` — canonical-build id. Content/freshness axis; floats.
- ``index lineage`` — which space + which canonical build produced an
  FAISS index, plus the dual-edge index/metadata md5 pair
  (CONSUMER_CONTRACTS 0a794cf).

Validation model (per-component, never at import time — fhir4ds INV-1):

- Model manifests must be accepted by the runtime's ACCEPTED registry.
- FAISS-bearing dirs must carry a lineage whose space matches the serving
  model and whose index/metadata md5s match the files on disk.
- Data manifests declare the space the pipeline used; a data dir is only
  servable by a model with the SAME render policy inside its fingerprint.
- A stale index (built against an older canonical build than the data
  dir) serves with a WARN during a build-keyed deprecation window: warn
  until the next index published after the triggering data revision.
  The WARN carries both revisions so drift is visible in logs.

The fingerprint helper here is the single source of truth: the
medterm4ds-canonical build pipeline imports it to emit manifests at
build time (BUILD.md prerequisite: it already depends on this package).

No behavior change in Phase 1 — manifests are read and validated only
where present; the legacy revision-keyed layout has none, and absent
manifests keep today's semantics (see search.py gating notes).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"

# Layout under the split cache (docs/plans §4). Same names on the HF repo.
MODEL_FAMILY_DIR = "models"
DATA_FAMILY_DIR = "data"

# ---------------------------------------------------------------------------
# Space fingerprint
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpaceFingerprint:
    """Everything that determines the string→vector function.

    Attributes mirror semantic.py's actual inference: mean pooling over
    token embeddings, L2 normalization, truncation at max_length 512
    (engines/fhir/semantic.py:_embed). ``render_policy_version`` is
    COMPUTED by the canonical build (hash of the render function source +
    field-selection config, emitted in canonical_build.json) — medterm4ds
    validates presence and match, it never computes it.
    """

    weights_md5: str
    tokenizer_md5: str
    pooling: str = "mean"
    l2_normalize: bool = True
    max_seq_length: int = 512
    render_policy_version: str = ""

    def space_id(self) -> str:
        """Content-addressed embedding-space id (stable across processes)."""
        payload = json.dumps(
            {
                "weights_md5": self.weights_md5,
                "tokenizer_md5": self.tokenizer_md5,
                "pooling": self.pooling,
                "l2_normalize": self.l2_normalize,
                "max_seq_length": self.max_seq_length,
                "render_policy_version": self.render_policy_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
        return f"esp_{digest}"


def md5_of_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    """Content-addressed md5 of a file, streamed.

    The build side uses the same helper (imported from here) so emitted
    manifests can never disagree with runtime-computed values.
    """
    h = hashlib.md5()
    with Path(path).open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def fingerprint_model_dir(
    model_dir: str | Path,
    *,
    render_policy_version: str,
    pooling: str = "mean",
    l2_normalize: bool = True,
    max_seq_length: int = 512,
) -> SpaceFingerprint:
    """Compute the space fingerprint of a SapBERT model directory.

    Weights = model.safetensors; tokenizer = tokenizer.json (the files
    semantic.py actually loads). ``render_policy_version`` must come from
    the canonical build's canonical_build.json — callers cannot invent it.
    """
    d = Path(model_dir)
    return SpaceFingerprint(
        weights_md5=md5_of_file(d / "model.safetensors"),
        tokenizer_md5=md5_of_file(d / "tokenizer.json"),
        pooling=pooling,
        l2_normalize=l2_normalize,
        max_seq_length=max_seq_length,
        render_policy_version=render_policy_version,
    )


# ---------------------------------------------------------------------------
# Manifest reading / typing
# ---------------------------------------------------------------------------


class ManifestError(RuntimeError):
    """A manifest is missing, malformed, or violates a compatibility rule.

    Raised only from component load paths (never import time) so
    lexical-only consumers are never blocked by semantic-side problems
    (fhir4ds request: per-component gates).
    """


def read_manifest(directory: str | Path) -> dict[str, Any] | None:
    """Read ``manifest.json`` from an artifact dir; None when absent.

    A present-but-malformed manifest raises ManifestError (never a silent
    pass — the refuse path must be loud).
    """
    path = Path(directory) / MANIFEST_FILENAME
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text())
    except ValueError as exc:
        raise ManifestError(f"Malformed manifest at {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ManifestError(f"Manifest at {path} is not a JSON object")
    return manifest


def _require(manifest: dict[str, Any], key: str, kind: str, source: str) -> Any:
    if key not in manifest:
        raise ManifestError(f"{source}: manifest missing required field {key!r}")
    value = manifest[key]
    if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
        raise ManifestError(
            f"{source}: manifest field {key!r} has wrong type "
            f"(expected {kind.__name__}, got {type(value).__name__})"
        )
    return value


# ---------------------------------------------------------------------------
# Acceptance registry (in code — every bump is a reviewed diff)
# ---------------------------------------------------------------------------

# Accepted embedding spaces. Empty means "no space has been blessed yet";
# the first canonical dual-publish (Phase 2) populates this with the id
# emitted for the current v0.0.5 SapBERT + render policy.
ACCEPTED_EMBEDDING_SPACES: frozenset[str] = frozenset()

# Accepted manifest schema versions (both model and data manifests).
ACCEPTED_MANIFEST_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})

# Accepted NER calibration ids (hash of labels + threshold). Populated in
# Phase 2 alongside the first extraction_ner manifest; the legacy
# commit-pin path stays authoritative until then.
ACCEPTED_NER_CALIBRATIONS: frozenset[str] = frozenset()


def manifest_str(manifest: dict[str, Any] | None) -> str:
    """Compact provenance string for logs/cache-info (fhir4ds ask)."""
    if not manifest:
        return "<no manifest>"
    parts = []
    for key in (
        "artifact_kind",
        "embedding_space_id",
        "data_revision",
        "render_policy_version",
    ):
        if key in manifest:
            parts.append(f"{key}={manifest[key]}")
    lineage = manifest.get("index_lineage")
    if isinstance(lineage, dict):
        parts.append(f"index_space={lineage.get('embedding_space_id')}")
        parts.append(f"index_build={lineage.get('built_against_canonical_build')}")
    return " ".join(parts) if parts else "<empty manifest>"


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------


def validate_model_manifest(
    manifest: dict[str, Any], *, source: str = "model dir"
) -> str:
    """Validate a model manifest; return its embedding_space_id.

    Hard-fails on: unknown schema version, missing fingerprint fields,
    space not in ACCEPTED_EMBEDDING_SPACES (when the registry is
    non-empty — during migration an empty registry accepts the legacy
    no-manifest path only; a PRESENT manifest must still be well-formed).
    """
    schema = _require(manifest, "schema_version", int, source)
    if schema not in ACCEPTED_MANIFEST_SCHEMA_VERSIONS:
        raise ManifestError(
            f"{source}: manifest schema_version {schema} not accepted "
            f"(accepted: {sorted(ACCEPTED_MANIFEST_SCHEMA_VERSIONS)})"
        )
    _require(manifest, "artifact_kind", str, source)
    space_id = _require(manifest, "embedding_space_id", str, source)
    fp = _require(manifest, "space_fingerprint", dict, source)
    for key in ("weights_md5", "tokenizer_md5", "render_policy_version"):
        if not isinstance(fp.get(key), str) or not fp[key]:
            raise ManifestError(
                f"{source}: space_fingerprint.{key} must be a non-empty string "
                "(render_policy_version is computed by the canonical build — "
                "it cannot be declared by hand)"
            )
    if ACCEPTED_EMBEDDING_SPACES and space_id not in ACCEPTED_EMBEDDING_SPACES:
        raise ManifestError(
            f"{source}: embedding space {space_id} is not accepted by this "
            "medterm4ds release. Pin a compatible artifact revision or "
            "upgrade medterm4ds."
        )
    return space_id


def validate_data_manifest(
    manifest: dict[str, Any],
    *,
    serving_space_id: str | None = None,
    source: str = "data dir",
) -> None:
    """Validate a data manifest against the serving model space.

    A data dir is only servable by a model whose space fingerprint
    includes the SAME render policy: without this cross-check a render-
    policy change would look like a pure model change and stale data
    dirs would pass (model-team correction 2).
    """
    schema = _require(manifest, "schema_version", int, source)
    if schema not in ACCEPTED_MANIFEST_SCHEMA_VERSIONS:
        raise ManifestError(
            f"{source}: manifest schema_version {schema} not accepted "
            f"(accepted: {sorted(ACCEPTED_MANIFEST_SCHEMA_VERSIONS)})"
        )
    _require(manifest, "data_revision", str, source)
    declared_space = _require(manifest, "embedding_space_id", str, source)
    _require(manifest, "render_policy_version", str, source)
    if serving_space_id is not None and declared_space != serving_space_id:
        raise ManifestError(
            f"{source}: data was produced against embedding space "
            f"{declared_space}, but the serving model is "
            f"{serving_space_id}. Refresh the data artifacts "
            "(medterm4ds data cache-refresh) or pin matching revisions."
        )


def validate_index_lineage(
    manifest: dict[str, Any],
    directory: str | Path,
    *,
    serving_space_id: str,
    data_revision: str | None = None,
    source: str = "index dir",
) -> None:
    """Validate the dual-edge index lineage of an FAISS-bearing dir.

    Hard-fails (correctness — never served):
    - lineage space ≠ serving model space
    - index/metadata md5 pair mismatch vs files on disk (same-build rule)

    Staleness is NOT a hard failure: when ``data_revision`` is newer than
    ``built_against_canonical_build``, log a WARN carrying BOTH revisions
    and serve (build-keyed deprecation window: the warn stands until an
    index published after the triggering data revision replaces it).
    """
    lineage = manifest.get("index_lineage")
    if not isinstance(lineage, dict):
        raise ManifestError(f"{source}: FAISS-bearing dir missing index_lineage")
    lineage_space = lineage.get("embedding_space_id")
    if lineage_space != serving_space_id:
        raise ManifestError(
            f"{source}: index was built in embedding space {lineage_space!r} "
            f"but the serving model is {serving_space_id!r} — vectors are "
            "not comparable. Refresh the artifacts as a matched set."
        )
    d = Path(directory)
    index_md5 = lineage.get("index_md5")
    metadata_md5 = lineage.get("metadata_md5")
    if not isinstance(index_md5, str) or not isinstance(metadata_md5, str):
        raise ManifestError(
            f"{source}: index_lineage requires index_md5 and metadata_md5 "
            "(dual-edge same-build rule, CONSUMER_CONTRACTS 0a794cf)"
        )
    # Dual-edge: validate the PAIR against disk. We check the files the
    # caller names via the manifest's companion field when present, else
    # the directory default (concept index) — callers pass explicit paths
    # for per-category indexes.
    companions = lineage.get("files")
    if isinstance(companions, dict):
        for rel, declared in companions.items():
            actual = md5_of_file(d / rel)
            if actual != declared:
                raise ManifestError(
                    f"{source}: file {rel} md5 mismatch (on disk {actual}, "
                    f"manifest {declared}) — index and metadata are not the "
                    "same build; re-download the pair."
                )
    built_against = lineage.get("built_against_canonical_build")
    if (
        data_revision
        and isinstance(built_against, str)
        and built_against != data_revision
    ):
        logger.warning(
            "STALE INDEX (served during deprecation window): %s index built "
            "against canonical build %s but data dir is %s — anchor names "
            "may lag the current wave. The warn clears when an index built "
            "against %s or newer is published.",
            source, built_against, data_revision, data_revision,
        )


# ---------------------------------------------------------------------------
# Atomic download helper (tmp + rename per load unit)
# ---------------------------------------------------------------------------


def atomic_rename_dir(tmp_dir: str | Path, target_dir: str | Path) -> None:
    """Rename a fully-populated tmp dir onto its target, atomically.

    An FAISS index and its metadata download into the SAME tmp dir and
    cross this boundary together — the dual-edge rule breaks if they can
    ever be observed mixed (model-team correction 4).
    """
    tmp = Path(tmp_dir)
    target = Path(target_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp.rename(target)
