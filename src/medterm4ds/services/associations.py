"""``fhir4ds/associations`` graph serving (standalone pinned HF repo).

Serves the fhir4px associations graph (condition/med/lab/procedure
associations keyed in canonical cid space) as the opt-in ConceptMap
``urn:medterm4ds:associations-conceptmap`` on ``ConceptMap/$translate``
(FDD .temp/associations_translate_fdd.md). Layout contract::

    manifest.json                          # release, bundle_version, format, counts
    graph/<release>/associations.json.gz   # THE graph (by_cid/by_name/concepts)
    graph/<release>/associations.meta.json
    naming/<release>/display_names.json.gz # consumer naming aid (downloaded
                                           # with the bundle; not read on the
                                           # v1 serving path)

Decision A (FDD §3.3): a STANDALONE pinned repo — not a family under the
terminology release layout. The repo has its own release cadence, manifest
schema, and ``version_lock``; pinning mirrors ``core/terminology.py``
(fail-loud manifest validation, stages-then-swaps download, explicit-pin
-is-an-operator-contract, empty-pin-is-unset per EV03-QA-01/02).

Live-verified against release ``2026.11.15.1`` (bundle 2026-11-15.1640,
format ``fhir4px_associations_v1.4``) on 2026-11-16. NOTE: the repo's git
tags are not spell-stable (``2026.10.31.1`` plain vs ``v2026.11.2`` for
the current release), so the HF revision is pinned separately
(``MEDTERM4DS_ASSOCIATIONS_HF_REVISION``, default ``main``) while
``MEDTERM4DS_ASSOCIATIONS_RELEASE`` pins the manifest release identity.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from medterm4ds.engines.fhir import system_to_fhir_uri

logger = logging.getLogger(__name__)

ASSOCIATIONS_DIRNAME = "associations"
DEFAULT_ASSOCIATIONS_REPO_ID = "fhir4ds/associations"
DEFAULT_ASSOCIATIONS_RELEASE = "2026.11.15.1"
# The HF revision to fetch (tags are not spell-stable across releases —
# see module docstring). ``main``'s manifest is validated against the
# release pin at resolve() time, so a future main-branch release bump
# fails loudly instead of silently serving a different release.
DEFAULT_ASSOCIATIONS_HF_REVISION = "main"
ASSOCIATIONS_FORMAT = "fhir4px_associations_v1.4"

# Fixed published bucket order (FDD §4.6 match-ordering contract). All 13
# association types, live-verified against release 2026.11.15.1.
BUCKET_ORDER: tuple[str, ...] = (
    "lab", "adverse_effect", "treats", "procedure",
    "contraindicated_in", "vital", "causes_abnormality", "medication",
    "screens_before", "condition", "antidote_for", "vaccine",
    "interferes_with_test",
)
_BUCKET_RANK = {b: i for i, b in enumerate(BUCKET_ORDER)}

# Closed cid-prefix registry (FDD §2.5, live counts verified): prefix =
# first 3 dash-segments of the cid, mapped to the ENGINE source name whose
# ``SYSTEM_TO_FHIR_URI`` entry is the canonical FHIR system URI (single
# source of truth — the URI is DERIVED, and tests pin every derived URI
# against the canonical registry per the HCPCS QA-012 doctrine).
# VAL-COND-ICD10CM appears only via the crosswalk files; it is admitted in
# the registry so a crosswalk-sourced cid parses instead of failing.
CID_PREFIX_TO_SOURCE: dict[str, str] = {
    "VAL-LAB-LOINC": "LNC",
    "VAL-VIT-LOINC": "LNC",
    "VAL-COND-SNOMED": "SNOMEDCT_US",
    "VAL-SYMP-SNOMED": "SNOMEDCT_US",
    "VAL-PROC-SNOMED": "SNOMEDCT_US",
    "VAL-MED-RXNORM": "RXNORM",
    "VAL-PROC-CPT": "CPT",
    "VAL-DRUGCLASS-ATC": "ATC",
    "VAL-VAX-CVX": "CVX",
    "VAL-COND-ICD10CM": "ICD10CM",
}

# Canonical FHIR system URI per prefix (derived — do not edit by hand).
CID_PREFIX_TO_URI: dict[str, str] = {
    prefix: system_to_fhir_uri(source)
    for prefix, source in CID_PREFIX_TO_SOURCE.items()
}

# The closed target-system enum served by $translate (FDD §4.2 decision C).
TARGET_SYSTEM_URIS: frozenset[str] = frozenset(CID_PREFIX_TO_URI.values())

# Engine source -> candidate derivation prefixes (FDD §4.5 step 2), in
# registry order (deterministic fallback chain).
SOURCE_TO_PREFIXES: dict[str, tuple[str, ...]] = {}
for _prefix, _source in CID_PREFIX_TO_SOURCE.items():
    SOURCE_TO_PREFIXES.setdefault(_source, ())
    SOURCE_TO_PREFIXES[_source] = SOURCE_TO_PREFIXES[_source] + (_prefix,)

# Canonical value_sets members use "LOINC" where the engine/FHIR surface
# uses "LNC" (live-verified against terminology 2026.11.14.2 value_sets).
ENGINE_TO_CANONICAL_SYSTEM = {"LNC": "LOINC"}


class AssociationsLayoutError(RuntimeError):
    """An associations release is present/downloaded but violates the
    contract (or is pinned-but-absent). Never silently serve."""


def repo_id() -> str:
    pinned = (os.getenv("MEDTERM4DS_ASSOCIATIONS_REPO_ID") or "").strip()
    return pinned or DEFAULT_ASSOCIATIONS_REPO_ID


def release() -> str:
    # Empty/whitespace-only env pin is unset (EV03-QA-01/02 mirrors).
    pinned = (os.getenv("MEDTERM4DS_ASSOCIATIONS_RELEASE") or "").strip()
    return pinned or DEFAULT_ASSOCIATIONS_RELEASE


def release_explicitly_pinned() -> bool:
    return bool(
        (os.getenv("MEDTERM4DS_ASSOCIATIONS_RELEASE") or "").strip()
        or (os.getenv("MEDTERM4DS_ASSOCIATIONS_DIR") or "").strip()
    )


def hf_revision() -> str:
    pinned = (os.getenv("MEDTERM4DS_ASSOCIATIONS_HF_REVISION") or "").strip()
    return pinned or DEFAULT_ASSOCIATIONS_HF_REVISION


def dir_override() -> Path | None:
    override = (os.getenv("MEDTERM4DS_ASSOCIATIONS_DIR") or "").strip()
    return Path(override) if override else None


def base_dir(cache_root: Path, rel: str | None = None) -> Path:
    override = dir_override()
    if override is not None:
        return override
    return Path(cache_root) / ASSOCIATIONS_DIRNAME / (rel or release())


def associations_root() -> Path:
    """Cache root holding ``associations/<release>/`` (shared with the
    terminology layout root)."""
    from medterm4ds.core.paths import terminology_root

    return terminology_root()


def graph_file(base: Path, rel: str) -> Path:
    return base / "graph" / rel / "associations.json.gz"


def is_present(base: Path) -> bool:
    return (base / "manifest.json").is_file()


@dataclass
class AssociationsGraph:
    """In-memory indexes over one associations release (lazy,
    single-flight loaded by ``get_associations_graph`` — FDD OQ-1
    resolution: in-memory, loaded only when the map is selected)."""

    release: str
    by_cid: dict[str, str]
    by_name: dict[str, str]
    concepts: dict[str, Any]
    bucket_order: tuple[str, ...] = field(default=BUCKET_ORDER)

    def parse_cid(self, cid: str) -> tuple[str, str]:
        """Deterministic cid -> (engine_source, code) via the closed
        prefix registry.

        Prefix = first 3 dash-segments; the remainder is the code joined
        back verbatim (LOINC ``24321-2`` and ICD-10-CM ``A00-A09``
        contain hyphens — never split past the prefix). Unknown prefix
        raises (fail-loud closed-set doctrine).
        """
        parts = str(cid).split("-")
        if len(parts) < 4:
            raise AssociationsLayoutError(
                f"associations cid {cid!r} is too short to carry a "
                "registered prefix"
            )
        prefix = "-".join(parts[:3])
        source = CID_PREFIX_TO_SOURCE.get(prefix)
        if source is None:
            raise AssociationsLayoutError(
                f"associations cid {cid!r} carries unknown prefix "
                f"{prefix!r}; the closed registry is "
                f"{sorted(CID_PREFIX_TO_SOURCE)}. Refusing to serve an "
                "unregistered cid space (update CID_PREFIX_TO_SOURCE "
                "in the same release that introduces it)."
            )
        return source, "-".join(parts[3:])

    def entry_for_cid(self, cid: str) -> dict[str, Any] | None:
        """cid -> graph entry (None when the cid resolves to no
        concept node: leaf cids and name-only product entries — an
        honest empty per FDD §4.5)."""
        name = self.by_cid.get(cid)
        if name is None:
            return None
        return self.concepts.get(name)

    def members(self, entry: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """(bucket, member) pairs in the fixed published bucket order."""
        out: list[tuple[str, dict[str, Any]]] = []
        buckets = entry.get("buckets") or {}
        if not isinstance(buckets, dict):
            raise AssociationsLayoutError(
                "associations concept entry carries a non-dict 'buckets' "
                f"unit: {entry.get('name')!r}"
            )
        for bucket in self.bucket_order:
            members = buckets.get(bucket) or []
            if not isinstance(members, list):
                raise AssociationsLayoutError(
                    f"associations bucket {bucket!r} on "
                    f"{entry.get('name')!r} is not a list"
                )
            for member in members:
                if not isinstance(member, dict):
                    raise AssociationsLayoutError(
                        f"associations bucket {bucket!r} on "
                        f"{entry.get('name')!r} carries a non-dict member"
                    )
                out.append((bucket, member))
        # Any bucket outside the published order is a contract violation.
        unknown = set(buckets) - set(self.bucket_order)
        if unknown:
            raise AssociationsLayoutError(
                f"associations entry {entry.get('name')!r} carries "
                f"unknown bucket(s) {sorted(unknown)}; the closed order "
                "is BUCKET_ORDER."
            )
        return out

    def sort_key(self, bucket: str, member: dict[str, Any]):
        """Stable match ordering: (bucket rank, distance None-last, cid)."""
        distance = member.get("distance")
        return (
            _BUCKET_RANK[bucket],
            (1, 0) if distance is None else (0, distance),
            member.get("cid", ""),
        )


def _read_manifest(base: Path) -> dict:
    try:
        return json.loads((base / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise AssociationsLayoutError(
            f"associations release at {base} has an unreadable manifest.json"
        ) from exc


def validate_manifest(manifest: dict, base: Path, rel: str) -> None:
    """Fail-loud manifest contract (FDD §3.3): release identity, format,
    version_lock."""
    if manifest.get("release") != rel:
        raise AssociationsLayoutError(
            f"associations manifest at {base} declares release "
            f"{manifest.get('release')!r}, expected {rel!r}"
        )
    if manifest.get("format") != ASSOCIATIONS_FORMAT:
        raise AssociationsLayoutError(
            f"associations manifest at {base} declares format "
            f"{manifest.get('format')!r}, expected {ASSOCIATIONS_FORMAT!r}. "
            "Refusing to serve a drifted artifact format (upgrade "
            "medterm4ds or pin MEDTERM4DS_ASSOCIATIONS_RELEASE to a "
            "compatible release)."
        )
    lock = manifest.get("version_lock") or {}
    if lock.get("locked") is not True:
        raise AssociationsLayoutError(
            f"associations manifest at {base} does not carry "
            "version_lock.locked=true; an unlocked bundle must never be "
            "served."
        )
    for fam in ("graph", "naming"):
        if not lock.get(fam):
            raise AssociationsLayoutError(
                f"associations manifest at {base} is missing "
                f"version_lock.{fam}"
            )


def resolve(cache_root: Path, *, rel: str | None = None) -> tuple[Path, dict]:
    """Validate and locate the local associations release.

    Returns (base_dir, manifest); raises AssociationsLayoutError when the
    release is present but violates the contract. A DIR override's
    manifest is the release identity (mirrors core/terminology's binding
    contract)."""
    rel = rel or release()
    base = base_dir(cache_root, rel)
    if not is_present(base):
        raise AssociationsLayoutError(
            f"associations release {rel} is not present at {base}"
        )
    manifest = _read_manifest(base)
    if dir_override() is not None:
        rel = manifest.get("release") or rel
    validate_manifest(manifest, base, rel)
    if not graph_file(base, rel).is_file():
        raise AssociationsLayoutError(
            f"associations release at {base} is incomplete: missing "
            f"{graph_file(base, rel)}"
        )
    return base, manifest


def download(cache_root: Path, *, rel: str | None = None) -> Path:
    """Atomically fetch one release (stages into ``.tmp-*`` then swaps;
    never pre-deletes — the existing release is renamed aside and
    restored if the download fails, mirroring EV07-QA-01/EV03-QA-04)."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ImportError(
            "huggingface_hub is required to auto-download associations "
            "artifacts. Install with: pip install huggingface_hub\n"
            f"Or manually download from https://huggingface.co/{repo_id()}"
        ) from None

    rel = rel or release()
    base = base_dir(cache_root, rel)
    if base.exists() and not base.is_dir():
        raise AssociationsLayoutError(
            f"associations release base {base} exists but is not a directory"
        )
    patterns = [
        "manifest.json",
        f"graph/{rel}/*",
        f"naming/{rel}/*",
    ]
    tmp = base.parent / f".tmp-{base.name}-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Downloading associations artifacts from Hugging Face (%s, %s)...",
        repo_id(), rel,
    )
    try:
        snapshot_download(
            repo_id=repo_id(),
            revision=hf_revision(),
            repo_type="model",
            local_dir=str(tmp),
            allow_patterns=patterns,
            token=os.getenv("HF_TOKEN"),
        )
        validate_manifest(_read_manifest(tmp), tmp, rel)
        if not graph_file(tmp, rel).is_file():
            raise AssociationsLayoutError(
                f"associations download for {rel} landed without "
                f"{graph_file(Path(''), rel).as_posix()}"
            )
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


def _rss_mb() -> float:
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _load_graph() -> AssociationsGraph:
    t0 = time.perf_counter()
    rss0 = _rss_mb()
    root = associations_root()
    rel = release()
    base = base_dir(root, rel)
    if dir_override() is not None:
        rel_override = None  # identity comes from the manifest
    else:
        rel_override = rel
    if not is_present(base):
        if release_explicitly_pinned():
            raise AssociationsLayoutError(
                f"associations release {rel!r} is pinned but absent at "
                f"{base}. Provision it with 'medterm4ds data cache-refresh "
                "--associations' (or unset the pin to auto-download)."
            )
        download(root)
    resolved_base, manifest = resolve(root, rel=rel_override)
    resolved_rel = manifest.get("release") or rel
    path = graph_file(resolved_base, resolved_rel)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("format") != ASSOCIATIONS_FORMAT:
        raise AssociationsLayoutError(
            f"associations graph at {path} declares format "
            f"{(data or {}).get('format') if isinstance(data, dict) else None!r}, "
            f"expected {ASSOCIATIONS_FORMAT!r}"
        )
    for key, kind in (
        ("by_cid", dict), ("by_name", dict), ("concepts", dict),
    ):
        unit = data.get(key)
        if not isinstance(unit, kind):
            raise AssociationsLayoutError(
                f"associations graph at {path} is missing a dict {key!r} "
                "unit (contract violation — never silently serve)"
            )
    graph = AssociationsGraph(
        release=resolved_rel,
        by_cid=data["by_cid"],
        by_name=data["by_name"],
        concepts=data["concepts"],
    )
    logger.info(
        "Loaded associations graph %s (%d cids, %d names, %d concepts) "
        "in %.1fs, RSS delta ~%.0f MB (peak %.0f MB)",
        resolved_rel, len(graph.by_cid), len(graph.by_name),
        len(graph.concepts), time.perf_counter() - t0,
        max(0.0, _rss_mb() - rss0), _rss_mb(),
    )
    return graph


_GRAPH_SINGLETON: AssociationsGraph | None = None
_GRAPH_LOCK = threading.Lock()


def get_associations_graph() -> AssociationsGraph:
    """Process singleton; thread-safe double-checked lazy load. Raises
    AssociationsLayoutError loud on any contract violation."""
    global _GRAPH_SINGLETON
    if _GRAPH_SINGLETON is not None:
        return _GRAPH_SINGLETON
    with _GRAPH_LOCK:
        if _GRAPH_SINGLETON is not None:
            return _GRAPH_SINGLETON
        _GRAPH_SINGLETON = _load_graph()
        return _GRAPH_SINGLETON


def reset_associations_graph_for_testing() -> None:
    """Drop the singleton so the next call re-resolves under a patched
    environment (established test pattern — mirrors
    services.search.reset_search_service_for_testing)."""
    global _GRAPH_SINGLETON
    with _GRAPH_LOCK:
        _GRAPH_SINGLETON = None
