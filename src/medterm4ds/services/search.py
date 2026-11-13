"""Unified text-to-code search service.

Provides lexical (BM25), semantic (SapBERT + FAISS), and hybrid search
across medical terminology codes. Used by all surfaces: Python facade,
CLI, MCP server, and FHIR API.

Data sources:
  - BM25 indexes: pre-built JSON files with inverted index (postings/idf)
  - SapBERT model + FAISS indexes: fine-tuned embeddings + ANN search

Both are lazy-loaded on first use. BM25 loads from MEDTERM4DS_SEARCH_INDEX_DIR
and SapBERT from MEDTERM4DS_EMBEDDING_MODEL_DIR; unset env means the
release-aware 'auto' resolution over the artifact cache root
(MEDTERM4DS_CACHE_DIR, else ~/.cache/medterm4ds/<rev>) — never a
machine-specific path (see medterm4ds.core.paths).

No GPU required. Runs on CPU (~1ms lexical, ~100ms semantic).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from medterm4ds.core import paths as _paths
from medterm4ds.core.models import CodeRef
from medterm4ds.core.normalize import SOURCE_LABELS, expand_source_aliases
from medterm4ds.core.paths import env_pin, terminology_root

logger = logging.getLogger(__name__)

# --- Cache directory and HF repo configuration ---

# Hugging Face repo holding prebuilt artifacts. Override via env vars for
# testing or private forks. Default revision tracks the artifact-set tag
# (v0.0.5 = 2026-08-27 canonical build: shorthand class aliases — SGLT2i/
# DPP4i/PPI/ARB, CDC vaccine groups, lab SNOMED crosswalk, ICD-10-PCS,
# guard hardening; v0.0.2 = Aug-2026 canonical indexes with CID corrections).

# Artifact cache layout. Two modes:
#
# DEFAULT (no MEDTERM4DS_CACHE_DIR): revision-keyed —
#   ~/.cache/medterm4ds/<revision>/{canonical,semantic,lexical}/...
# Each revision gets its own subtree, so switching MEDTERM4DS_HF_REVISION
# (or bumping the default at release) triggers the download automatically
# and rollback is instant. A provenance marker records what was pulled.
#
# OPERATOR-MANAGED (MEDTERM4DS_CACHE_DIR set): the directory is used
# as-is, without revision keying — the deploy.sh/data-dir contract where
# artifacts are placed by an external pipeline. Files must exist; nothing
# is re-checked or re-keyed.
# AMUX-44: cache-root resolution lives in medterm4ds.core.paths (single
# source of truth; the historical /mnt/d machine-specific defaults were
# removed). QC2-H4: the frozen sentinels are REFRESHED AS A UNIT here —
# reloading this module under a patched env (the documented test pattern)
# also refreshes paths' sentinels, so pin detection below always compares
# against the same defaults the caller's constants came from.
_paths._refresh_sentinels()
_CACHE_DIR = _paths.CACHE_DIR
_CACHE_REVISION_KEYED = _paths.CACHE_REVISION_KEYED
_HF_REPO_ID = _paths.HF_REPO_ID
_HF_REVISION = _paths.HF_REVISION

DEFAULT_SEARCH_INDEX_DIR = _paths.DEFAULT_SEARCH_INDEX_DIR
DEFAULT_EMBEDDING_MODEL_DIR = _paths.DEFAULT_EMBEDDING_MODEL_DIR
DEFAULT_CANONICAL_VALUE_SETS_PATH = _paths.DEFAULT_CANONICAL_VALUE_SETS_PATH
DEFAULT_CANONICAL_CONCEPTS_INDEX = _paths.DEFAULT_CANONICAL_CONCEPTS_INDEX
DEFAULT_CANONICAL_CONCEPTS_META = _paths.DEFAULT_CANONICAL_CONCEPTS_META
SEARCH_CATEGORIES = ("condition", "lab", "medication", "procedure", "vaccine", "body_structure")

# AMUX-46 hybrid fusion constants (benchmark winner; see
# benchmarks/fusion_benchmark.py — RRF k=10 + agreement-aware grades).
# AMUX-63 depth sweep (benchmarks/depth_sweep.py): quality is flat from
# 10 to 100 (one borderline grade differs at 100); fusion depth does NOT
# drive latency (the lexical orphan pass did — now cached). 25 keeps the
# agreement evidence headroom of deeper pools at no measured cost.
HYBRID_FUSION_K = 10
HYBRID_CANDIDATE_DEPTH = 25

# Grade-authority cutoffs for agreement-aware grading (AMUX-46): the
# cosine bands beneath 'certain' and 'probable'. These mirror
# engines/fhir/semantic._cosine_to_grade (the ENGINE-layer authority —
# engines must not import services, so each layer holds one definition
# and they cross-reference). Benchmark tooling imports THESE values; do
# not inline the numbers at call sites.
GRADE_CERTAIN_COSINE = 0.92
GRADE_PROBABLE_COSINE = 0.75

# --- Split-layout cache (Phase 3, docs/plans/artifact-governance-plan.md §4) ---
#
# models/<embedding_space_id>/   content-addressed model dirs (immutable)
# data/<data_revision>/          canonical data dirs (float via 'latest')
#
# The split root is MEDTERM4DS_CACHE_DIR in operator mode, else the cache
# root WITHOUT revision keying (~/.cache/medterm4ds). MEDTERM4DS_LAYOUT
# selects the resolution mode: 'auto' (default) prefers split artifacts
# when present and falls back to the legacy revision-keyed layout;
# 'legacy' forces the legacy layout (instant rollback kill-switch).
_LAYOUT_ENV = os.getenv("MEDTERM4DS_LAYOUT", "auto").strip().lower()
# 'auto' (default): terminology release → split → legacy.
# 'terminology' / 'split' / 'legacy' pin one layout (explicit operator
# contracts never silently fall through).
_LAYOUT_TERMINOLOGY = _LAYOUT_ENV in ("auto", "terminology")
MODELS_DIRNAME = "models"
DATA_DIRNAME = "data"
_SPLIT_ROOT = _paths.TERMINOLOGY_ROOT if not _CACHE_REVISION_KEYED else (Path.home() / ".cache" / "medterm4ds")
_LAYOUT_FORCED_LEGACY = _LAYOUT_ENV == "legacy"
_DATA_REVISION_ENV = os.getenv("MEDTERM4DS_DATA_REVISION") or None

_split_legacy_deprecation_logged = False


def _alias_key(text: str) -> str:
    """Normalization for the exact-match dictionary passes (alias + group).

    Lowercase + accent fold (the QC-329 matched-pair contract — query side
    mirrors index side) + whitespace collapse. EV02-QA-01 (2026-11-11
    evolution): accent folding so 'mullerian agenesis' exactly matches the
    'müllerian …' anchor name.
    """
    return " ".join(_strip_accents(text.lower()).split())


def _natural_key(name: str) -> list[int | str]:
    """Sort key for cdb_YYYY_MM_DD[_N] revision names (natural order)."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def _local_split_model_dir() -> Path | None:
    """Local split model dir for an ACCEPTED embedding space, if present."""
    if _LAYOUT_FORCED_LEGACY:
        return None
    from medterm4ds.core.artifact_manifest import (
        ACCEPTED_EMBEDDING_SPACES,
        effective_embedding_space,
    )

    # Serving pin first (governance rotation knob). K1: a set-but-
    # unaccepted pin raises (never silently serve the previous
    # generation); the lexicographic scan is the documented last
    # resort ONLY when the effective space's unit is absent locally.
    effective = effective_embedding_space()
    cand = _SPLIT_ROOT / "models" / effective
    if (cand / "model.safetensors").exists():
        return cand
    # EV03-QA-05 (2026-11-11 evolution): the lexicographic fallback is the
    # documented last resort (K1) — but it was SILENT. Say which space is
    # actually being served so operators can see the substitution.
    for space in sorted(ACCEPTED_EMBEDDING_SPACES):
        cand = _SPLIT_ROOT / "models" / space
        if (cand / "model.safetensors").exists():
            logger.warning(
                "Serving split model %s (lexicographic fallback): the "
                "effective space %s has no local model unit. Vectors stay "
                "consistent via _check_concepts_space; download the "
                "effective space to remove this substitution.",
                cand, effective,
            )
            return cand
    return None


def _local_latest_data_dir() -> Path | None:
    """Highest natural-ordered local data revision, if any (layout 'auto')."""
    if _LAYOUT_FORCED_LEGACY:
        return None
    if _DATA_REVISION_ENV:
        pinned = _SPLIT_ROOT / "data" / _DATA_REVISION_ENV
        return pinned if (pinned / "manifest.json").exists() else None
    data_root = _SPLIT_ROOT / "data"
    if not data_root.is_dir():
        return None
    revs = [
        d.name for d in data_root.iterdir()
        if d.is_dir() and (d / "manifest.json").exists()
        # EV03-QA-04 fix: symlinked revision dirs silently alias whatever
        # they point at (a repo tree, a dangling target) — the split layout
        # expects materialized, atomically-downloaded units. Skip them so a
        # stale operator symlink can never shadow or corrupt the revision
        # scan (the fail-loud _verify_space handles the rest).
        and not d.is_symlink()
    ]
    if not revs:
        return None
    return data_root / max(revs, key=_natural_key)


_TERM_CACHE: dict[tuple[str, str, str], object | None] = {}


def _local_artifacts_present() -> bool:
    """True when a servable legacy/split cache exists locally.

    Used only to gate the on-demand terminology download in 'auto'
    mode: an existing cache keeps serving (instant rollback, no silent
    3.7GB fetch on first query) until the operator runs
    ``data cache-refresh --terminology``. Fresh installs have no local
    artifacts and download the release eagerly on first use.
    """
    legacy_model = Path(DEFAULT_EMBEDDING_MODEL_DIR) / "model.safetensors"
    if legacy_model.exists():
        return True
    # Any accepted space, not just the serving pin: an existing cache
    # from a previous generation must not trigger a terminology fetch.
    models_root = _SPLIT_ROOT / MODELS_DIRNAME
    if models_root.is_dir() and any(
        (d / "model.safetensors").exists()
        for d in models_root.iterdir()
        if d.is_dir()
    ):
        return True
    data_root = _SPLIT_ROOT / DATA_DIRNAME
    if data_root.is_dir() and any(
        (d / "manifest.json").exists() for d in data_root.iterdir() if d.is_dir()
    ):
        return True
    lexical = Path(DEFAULT_SEARCH_INDEX_DIR)
    if lexical.is_dir() and any(lexical.glob("*.json")):
        return True
    return False


def _terminology_root() -> Path:
    """Cache root holding ``terminology/<release>/`` (shared across revisions)."""
    return terminology_root()


def _resolve_terminology(download: bool = True):
    """Resolve the ``fhir4ds/terminology`` release, or None when unavailable.

    Preferred layout (repoint 2026-10-31): one release dir per dated tag,
    keyed under the cache root so legacy revisions stay untouched. A
    malformed/foreign-space release raises (never silently serves wrong
    vectors); an absent one downloads on demand in 'auto'/'terminology'
    modes and degrades to split/legacy only in 'auto'.

    ``download=False`` is the read-only probe for availability properties:
    it never fetches and never caches an absent release (a later
    ``download=True`` call must still be able to fetch).
    """
    if not _LAYOUT_TERMINOLOGY:
        return None
    from medterm4ds.core import terminology as _term
    from medterm4ds.core.artifact_manifest import (
        effective_embedding_space,
    )

    space = effective_embedding_space()
    root = _terminology_root()
    key = (str(root), _term.release(), space)
    if key in _TERM_CACHE:
        return _TERM_CACHE[key]  # type: ignore[return-value]
    paths = _term.resolve(root, space_id=space)
    # An explicitly pinned release is an operator contract: fetch it even
    # when a servable local cache exists (which otherwise keeps serving in
    # 'auto' mode until cache-refresh — the silent-fallthrough guard).
    if (
        paths is None
        and download
        and (_term.release_explicitly_pinned() or not _local_artifacts_present())
    ):
        try:
            paths = _term.download(root, space_id=space)
        except _term.TerminologyLayoutError:
            raise
        except Exception as exc:
            if _LAYOUT_ENV == "terminology":
                raise
            logger.warning(
                "Terminology layout unavailable (%s); falling back to the "
                "split/legacy artifacts.", exc,
            )
            paths = None
    # EV03-QA-02 fix (2026-11-11 evolution): cache only RESOLVED paths. The
    # prior `paths is not None or download` form cached None after one
    # failed download=True attempt — one transient network blip permanently
    # disabled the terminology layout for a long-running server (later
    # calls hit the poisoned cache and never retried).
    if paths is not None:
        _TERM_CACHE[key] = paths
    return paths


def _maybe_log_legacy_deprecation() -> None:
    """One-time-per-process nudge when serving legacy artifacts."""
    global _split_legacy_deprecation_logged
    if _split_legacy_deprecation_logged:
        return
    _split_legacy_deprecation_logged = True
    logger.info(
        "Serving search artifacts from the legacy revision-keyed layout "
        "(%s). Run `medterm4ds data cache-refresh --split` to migrate to "
        "the split models/<space>/ + data/<revision>/ layout.",
        _CACHE_DIR,
    )

# Upper bound on query length accepted by every search entry point
# (QC-126/QC-140: no cap existed — 10K-char queries were silently tokenized).
# Matches the FHIR $search GET max_length; a ValueError (not silent truncation)
# is raised so clients learn their input was rejected.
MAX_QUERY_CHARS = 1000


def _validate_query(query: Any) -> str:
    """Validate free-text query at the service boundary.

    Raises TypeError for non-str input (QC-128: list/dict/int/bool queries
    previously crashed deep in the tokenizer with a bare AttributeError) and
    ValueError for empty/whitespace-only or over-length queries (QC-122:
    whitespace queries were silently embedded by SapBERT and returned
    confidently-ranked anchors; QC-126: unbounded input length).
    """
    if not isinstance(query, str):
        raise TypeError(f"query must be a string, got {type(query).__name__}")
    if not query.strip():
        raise ValueError("query must not be empty")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(
            f"query length {len(query)} exceeds max {MAX_QUERY_CHARS} chars."
        )
    return query


def _validate_count(count: Any) -> int:
    """Validate result count at the service boundary (QC-125).

    Negative counts previously hit Python slice semantics (count=-1 silently
    returned all-but-last); floats/strings raised bare TypeError mid-pipeline.
    """
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError(f"count must be a positive integer, got {count!r}")
    return count


def _hf_download(allow_patterns: list[str]) -> None:
    """Download artifacts from Hugging Face Hub to the cache directory.

    Called lazily by _ensure_canonical / _ensure_semantic / _ensure_bm25
    when local artifacts are missing. Uses huggingface_hub.snapshot_download
    with allow_patterns so only the needed subfolder is fetched.

    Raises:
        ImportError: if huggingface_hub is not installed.
        Exception: from the HF API on network/auth errors.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ImportError(
            "huggingface_hub is required to auto-download search artifacts.\n"
            "Install with: pip install huggingface_hub\n"
            f"Or manually download from https://huggingface.co/{_HF_REPO_ID}"
        ) from None
    logger.info("Downloading artifacts from Hugging Face (%s, revision=%s)...",
                _HF_REPO_ID, _HF_REVISION)
    snapshot_download(
        repo_id=_HF_REPO_ID,
        revision=_HF_REVISION,
        repo_type="model",
        local_dir=str(_CACHE_DIR),
        allow_patterns=allow_patterns,
        token=os.getenv("HF_TOKEN"),
    )
    # Provenance marker so cache-info/refresh can report WHAT the cache
    # holds without trusting mtimes or file contents.
    try:
        import datetime
        (_CACHE_DIR / "artifact_provenance.json").write_text(json.dumps({
            "repo_id": _HF_REPO_ID,
            "revision": _HF_REVISION,
            "downloaded_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }, indent=2))
    except OSError:
        pass
    logger.info("Download complete → %s", _CACHE_DIR)


def _hf_download_atomic(
    allow_patterns: list[str],
    *,
    target_root: Path,
    family: str,
    revision: str,
    unit_subpath: str | None = None,
) -> None:
    """Atomic variant of _hf_download for one family unit.

    Downloads into a tmp dir (sibling of the target) and renames the
    family dir into place, so an FAISS index and its metadata can never
    be observed mixed (dual-edge rule, plan §5; model-team correction 4).

    ``unit_subpath`` renames a CHILD of the family dir (e.g. the
    models/<space_id> unit) instead of the family root — needed when the
    family root is shared across units (models/ holds several embedding
    spaces, so a family-level rename collides with the existing dir and
    the unit can never land).
    """
    # EV03-QA-06 hardening: a subpath bearing '/' or '..' would land
    # outside the family root — reject before any path arithmetic.
    if unit_subpath is not None and ("/" in unit_subpath or ".." in unit_subpath):
        raise ValueError(
            f"unit_subpath must be a bare directory name, got {unit_subpath!r}"
        )
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ImportError(
            "huggingface_hub is required to auto-download search artifacts.\n"
            "Install with: pip install huggingface_hub\n"
            f"Or manually download from https://huggingface.co/{_HF_REPO_ID}"
        ) from None
    from medterm4ds.core.artifact_manifest import atomic_rename_dir

    os_pid = os.getpid()
    tmp_root = target_root.parent / f".tmp-{family}-{os_pid}"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_root.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Downloading artifacts from Hugging Face (%s, revision=%s) → %s/%s ...",
        _HF_REPO_ID, revision, target_root, family,
    )
    snapshot_download(
        repo_id=_HF_REPO_ID,
        revision=revision,
        repo_type="model",
        local_dir=str(tmp_root),
        allow_patterns=allow_patterns,
        token=os.getenv("HF_TOKEN"),
    )
    if unit_subpath is None:
        atomic_rename_dir(tmp_root / family, target_root / family)
    else:
        atomic_rename_dir(tmp_root / family / unit_subpath,
                          target_root / family / unit_subpath)
    shutil.rmtree(tmp_root, ignore_errors=True)


def _remote_latest_data_revision() -> str:
    """Latest data revision published on the HF repo's main branch."""
    import os as _os

    try:
        from huggingface_hub import HfApi
    except ImportError:
        raise ImportError(
            "huggingface_hub is required to resolve the latest data "
            "revision. Install with: pip install huggingface_hub"
        ) from None
    api = HfApi(token=_os.getenv("HF_TOKEN"))
    files = api.list_repo_files(_HF_REPO_ID, repo_type="model", revision="main")
    revs = [
        m.group(1)
        for f in files
        if (m := re.match(r"^data/([^/]+)/manifest\.json$", f))
    ]
    if not revs:
        raise RuntimeError(
            f"No data/*/manifest.json found in {_HF_REPO_ID}@main — the "
            "split layout is not published yet; run `medterm4ds data "
            "cache-refresh` (legacy) instead."
        )
    latest = max(revs, key=_natural_key)
    logger.info("Resolved latest data revision: data/%s", latest)
    return latest


def _download_split_data(revision: str | None = None) -> Path:
    """Download a split data/<revision> unit atomically; return its dir.

    Resolution order: explicit revision (MEDTERM4DS_DATA_REVISION or the
    argument) → local latest → remote latest (main). The downloaded
    manifest is validated BEFORE the dir is considered servable; a space
    mismatch is a loud ManifestError (never a silent legacy fallback —
    mixing spaces serves wrong vectors).
    """
    from medterm4ds.core.artifact_manifest import (
        read_manifest,
        validate_data_manifest,
    )

    rev = revision or _DATA_REVISION_ENV
    target: Path | None = None
    if rev:
        target = _SPLIT_ROOT / "data" / rev
        if not (target / "manifest.json").exists():
            # Per-revision atomic unit (EV03-QA-01): the shared data/
            # root holds coexisting revisions — a family-level rename
            # rmtree's every sibling (a bogus pin or even a SUCCESSFUL
            # new-revision download destroyed all other revisions).
            _hf_download_atomic(
                [f"data/{rev}/*"], target_root=_SPLIT_ROOT, family="data",
                revision="main", unit_subpath=rev,
            )
    else:
        local = _local_latest_data_dir()
        if local is not None:
            return local
        rev = _remote_latest_data_revision()
        target = _SPLIT_ROOT / "data" / rev
        # EV03-QA-01: unit_subpath here too — same sibling-wipe shape.
        _hf_download_atomic(
            [f"data/{rev}/*"], target_root=_SPLIT_ROOT, family="data",
            revision="main", unit_subpath=rev,
        )
    manifest = read_manifest(target)
    if manifest is None:
        raise RuntimeError(f"Data unit at {target} has no manifest.json")
    validate_data_manifest(manifest, source=str(target))
    logger.info("Split data unit ready: %s", target)
    return target


def _download_split_model(space_id: str | None = None) -> Path:
    """Download the split models/<space_id> unit atomically; return dir."""
    if space_id is None:
        from medterm4ds.core.artifact_manifest import (
            ACCEPTED_EMBEDDING_SPACES,
            effective_embedding_space,
        )

        if not ACCEPTED_EMBEDDING_SPACES:
            raise RuntimeError(
                "No accepted embedding spaces in this medterm4ds release — "
                "cannot auto-download the split model layout."
            )
        # Governance pin (env override over the release default).
        # K1: an unaccepted pin raises instead of silently falling
        # back — the sorted()[0] scan (which served whichever id
        # happened to sort first across generations) is now only the
        # absent-registry edge, never a typo swallower.
        space_id = effective_embedding_space()
    target = _SPLIT_ROOT / "models" / space_id
    if not (target / "model.safetensors").exists():
        # Per-space atomic unit (2026-11-11): the shared models/ root can
        # already hold other embedding spaces — a family-level rename
        # collided with it and the unit never landed, silently degrading
        # query embeddings to the legacy space against the new index.
        _hf_download_atomic(
            [f"models/{space_id}/*"], target_root=_SPLIT_ROOT, family="models",
            revision="main", unit_subpath=space_id,
        )
    return target

_SOURCE_TO_CATEGORIES = {
    "SNOMEDCT_US": list(SEARCH_CATEGORIES),
    "ICD10CM": ["condition"],
    "ICD10PCS": ["procedure"],
    "RXNORM": ["medication"],
    "LNC": ["lab"],
    "CPT": ["procedure"],
    "HCPCS": ["procedure"],
    "CVX": ["vaccine"],
}

# Canonical source name → lowercase system label for SearchResult
_SOURCE_LABELS = {
    # Delegates to core.normalize.SOURCE_LABELS — single source of truth
    # shared with services.extraction. Kept as a local alias for back-compat
    # with any external code importing _SOURCE_LABELS directly.
    **SOURCE_LABELS,
}


@dataclass
class SearchResult:
    """One ranked search result."""

    code: str
    source: str  # internal source name (e.g., "SNOMEDCT_US")
    display: str
    score: float  # 0.0–1.0
    match_grade: str  # "certain" | "probable" | "possible"
    category: str = ""  # "condition", "medication", etc.
    # AMUX-18: query surface equals the row's indexed name or friendly
    # display (computed in lexical; carry-only everywhere else). Excluded
    # from repr/compare and from to_dict — ranking metadata, not contract.
    _exact_surface: bool = field(default=False, repr=False, compare=False)

    @property
    def system_label(self) -> str:
        """Lowercase system label for API/JSON consumers."""
        return _SOURCE_LABELS.get(self.source, self.source.lower())

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source": self.source,
            "system": self.system_label,
            "display": self.display,
            "score": self.score,
            "match_grade": self.match_grade,
            "category": self.category,
        }

    def to_coderef(self) -> CodeRef:
        return CodeRef(source=self.source, code=self.code)


@dataclass
class CanonicalSearchResult:
    """One ranked canonical value set search result."""

    canonical_id: str
    domain: str
    anchor_system: str
    anchor_code: str
    patient_friendly_name: str
    score: float
    match_grade: str
    matched_via_code: str
    matched_via_display: str
    total_member_count: int
    members: list[dict[str, Any]] = field(default_factory=list)
    combination_members: list[dict[str, Any]] = field(default_factory=list)
    # Composite-group fan-out (dictionary pass). Populated on every row of a
    # group result: group_id identifies the group; group_cids lists the parent
    # CID first (when the composite is anchored) then component CIDs.
    # Pre-migration consumers take top-1 (the parent) and get today's
    # behavior; group-aware consumers emit one item per component (skip the
    # parent entry) per DECOMPOSITION_ARCHITECTURE.md 5.
    group_id: str | None = None
    group_cids: list[str] = field(default_factory=list)

    @property
    def code(self) -> str:
        return self.anchor_code

    @property
    def source(self) -> str:
        return self.anchor_system

    @property
    def display(self) -> str:
        return self.patient_friendly_name

    @property
    def result_type(self) -> str:
        """Clinical result type derived from the canonical_id prefix.

        Single source of truth for "what kind of anchor is this?" — distinct
        from the legacy BM25 `category` field on SearchResult. Useful when
        category lumping happens upstream (e.g., extraction maps medication
        label to search across medication + drug_class + vaccine anchors).
        Lets callers differentiate without parsing canonical_id themselves.

        Returns: "condition" | "symptom" | "lab" | "vital" | "medication" |
                 "drug_class" | "procedure" | "vaccine" | "" (unknown)
        """
        return _prefix_to_result_type(self.canonical_id)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "canonical_id": self.canonical_id,
            "domain": self.domain,
            "result_type": self.result_type,
            "anchor_system": self.anchor_system,
            "anchor_code": self.anchor_code,
            "patient_friendly_name": self.patient_friendly_name,
            "score": self.score,
            "match_grade": self.match_grade,
            "matched_via_code": self.matched_via_code,
            "matched_via_display": self.matched_via_display,
            "total_member_count": self.total_member_count,
            "members": self.members,
            "combination_members": self.combination_members,
        }
        if self.group_id:
            d["group_id"] = self.group_id
            d["group_cids"] = self.group_cids
        return d


def _score_to_grade(score: float) -> str:
    if score >= 0.8:
        return "certain"
    if score >= 0.4:
        return "probable"
    return "possible"


# Map clinical result_type → canonical_id prefix(es).
# Used by SearchService.canonical(result_types=...) to filter at the service
# level. Single source systems (LOINC, RXNORM, ATC, CVX, CPT) map cleanly to
# one result type; SNOMEDCT_US spans conditions, procedures, symptoms, and
# body structures, so the result_types filter is needed to disambiguate.
_RESULT_TYPE_TO_PREFIXES: dict[str, tuple[str, ...]] = {
    "condition": ("VAL-COND-",),
    "symptom": ("VAL-SYMP-",),
    "lab": ("VAL-LAB-",),
    "vital": ("VAL-VIT-",),
    "medication": ("VAL-MED-",),
    "drug_class": ("VAL-DRUGCLASS-",),
    "procedure": ("VAL-PROC-",),
    "vaccine": ("VAL-VAX-",),
}

# Valid canonical-mode result types (the keys above), for callers that need
# to validate/intersect --result-types values before they reach
# ``SearchService.canonical`` (which raises on unknown types).
CANONICAL_RESULT_TYPES: tuple[str, ...] = tuple(_RESULT_TYPE_TO_PREFIXES)


def _result_types_to_prefixes(result_types: str | list[str] | None) -> set[str]:
    """Normalize result_types parameter to a set of canonical_id prefixes.

    Returns an empty set if result_types is None (no filter).
    Raises ValueError for unknown result types.
    """
    if result_types is None:
        return set()
    types = [result_types] if isinstance(result_types, str) else list(result_types)
    prefixes: set[str] = set()
    for t in types:
        key = t.lower().strip()
        if key not in _RESULT_TYPE_TO_PREFIXES:
            valid = ", ".join(sorted(_RESULT_TYPE_TO_PREFIXES))
            raise ValueError(
                f"Unknown result type: {t!r}. Valid: {valid}."
            )
        prefixes.update(_RESULT_TYPE_TO_PREFIXES[key])
    return prefixes


# Reverse map for deriving the result_type from a canonical_id prefix.
# Used by CanonicalSearchResult.result_type and
# ExtractedConcept.result_type so downstream consumers can differentiate
# medication vs drug_class vs vaccine without parsing canonical_id themselves.
_PREFIX_TO_RESULT_TYPE: dict[str, str] = {
    prefix: rt
    for rt, prefixes in _RESULT_TYPE_TO_PREFIXES.items()
    for prefix in prefixes
}


def _prefix_to_result_type(canonical_id: str) -> str:
    """Derive the result type from a canonical_id prefix.

    Returns the empty string if no prefix matches (defensive — should not
    happen with well-formed canonical_ids).
    """
    for prefix, rt in _PREFIX_TO_RESULT_TYPE.items():
        if canonical_id.startswith(prefix):
            return rt
    return ""


def _strip_accents(text: str) -> str:
    """Fold Latin accents to ASCII (NFD + drop combining marks).

    QC-329 (HIGH): the pre-built BM25 indexes tokenize documents with an
    ASCII-only ``[a-zA-Z]{3,}`` regex (``build_bm25_v2.tokenize``), so every
    posting key is unaccented. Query tokens taken verbatim
    (``query.lower().split()``) kept accents AND punctuation attached, so
    accented clinical queries ('Guillain-Barré', 'Chédiak', 'Déjà vu')
    silently returned 0 hits for concepts whose preferred terms exist
    verbatim in mrconso. Fold accents + extract alphanumeric runs so the
    query tokenizes exactly like the indexed documents.
    """
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _query_tokens(query: str) -> list[str]:
    """Tokenize a free-text query for BM25 lookup (QC-329, AMUX-20).

    Accents fold and punctuation splits exactly like the index-build
    tokenizer (QC-329), but alphanumeric runs of >= 2 chars are kept —
    the build tokenizer (ASCII ``[a-zA-Z]{3,}``) digitized acronyms and
    alphanumerics out of the postings (A1c, HbA1c, MI, PE, PT), so the
    query side must preserve them for the posting-candidate fallback
    (letter runs, e.g. 'hba1c' -> 'hba') and the surface-name pass in
    ``SearchService.lexical``. Tokens that can never match a posting key
    are harmless: lookup simply yields no candidates for them.
    """
    return re.findall(r"[a-z0-9]{2,}", _strip_accents(query.lower()))


# ASCII letter runs of >= 3 chars — the shape of every posting key in the
# pre-built BM25 indexes (build tokenizer was ``[a-zA-Z]{3,}``).
_LETTER_RUN_RE = re.compile(r"[a-z]{3,}")


def _posting_candidates(token: str, postings: dict) -> list[str]:
    """Posting keys a query token can look up (AMUX-20, v3-aware).

    v3 indexes (AMUX-69) tokenize alphanumeric terms, so an exact
    posting-key hit is tried FIRST and short-circuits everything else —
    'hba1c' must look up 'hba1c', not its letter runs, when the index
    carries the full term. On v2 indexes (letter runs of >= 3 chars
    only) the exact key misses and behavior is unchanged: a pure >=
    3-letter token maps to itself (stem fallback as before); an
    alphanumeric or 2-char token maps to its >= 3-letter runs
    ('hba1c' -> 'hba'). Tokens with no run (a1c, mi, pe, pt, b12)
    return [] — the lexical surface pass covers those.
    """
    cands: list[str] = []
    if token in postings and not (token.isalpha() and len(token) >= 3):
        # v3 alphanumeric/short posting key — exact evidence; letter-run
        # and stem fallbacks must not dilute it ('hba1c' -> 'hba').
        # Pure >= 3-letter tokens keep the historical dual-candidate
        # behavior below (self + stem), which v2 ranking relies on.
        return [token]
    if token.isalpha() and len(token) >= 3:
        runs = [token]
    else:
        runs = _LETTER_RUN_RE.findall(token)
    for run in runs:
        cands.append(run)
        stemmed = _stem_token(run)
        if stemmed != run:
            cands.append(stemmed)
    return [c for c in dict.fromkeys(cands) if c in postings]


def _surface_key(text: str) -> str:
    """Normalization for exact surface comparison (AMUX-18).

    Accents folded, lowercased, non-alphanumerics dropped, whitespace
    collapsed — the same family as _query_tokens, so 'Tylenol',
    'tylenol ' and 'TYLENOL' all normalize identically.
    """
    return " ".join(re.findall(r"[a-z0-9]+", _strip_accents(text.lower())))


def _sorted_lazy_surface_tiebreak(rows, cheap_key, full_key, count):
    """sorted(rows, key=full_key) with the expensive surface-distance
    tier computed only where it can matter (AMUX-63/v3: real alphanumeric
    postings enlarge the scored set to thousands of rows and the
    O(len^2) edit distance per row dominated the whole lexical path).

    Exact-equivalent to ``sorted(rows, key=full_key)`` restricted to the
    rows that can reach the top ``count``, PROVIDED full_key's leading
    components are exactly cheap_key (a tuple prefix). Sort by the cheap
    prefix, keep the top ``count`` rows PLUS every row tied with the
    boundary row (rows strictly worse on the prefix can never enter the
    top count of the full order), then apply the full key to that slice.
    The returned list may exceed ``count`` by the boundary ties; callers
    slice as before.
    """
    ordered = sorted(rows, key=cheap_key)
    if len(ordered) <= count:
        ordered.sort(key=full_key)
        return ordered
    boundary = cheap_key(ordered[count - 1])
    end = count
    while end < len(ordered) and cheap_key(ordered[end]) == boundary:
        end += 1
    kept = ordered[:end]
    kept.sort(key=full_key)
    return kept


def _surface_distance(query_key: str, text: str, cap: int = 64) -> float:
    """NORMALIZED Damerau-Levenshtein distance between the query's
    surface key and a candidate's surface key — the UNIFIED tie-break
    for lexical and semantic ranking (AMUX-18 follow-up). Distance 0 IS
    the exact-surface match, so the exact tier and the proximity tier
    are one scale.

    Normalized by the longer string's length so longer surfaces that
    CONTAIN the query ('Tylenol PM Extra Strength Tylenol' for query
    'Tylenol PM') rank closer than short unrelated ones
    ('Acetaminophen') — raw edit distance is length-biased the other way.

    Model-free and deterministic; invoked as a sort-key component on every
    candidate row (not only score ties — it ranks the proximity tier
    after the exact tier), so inputs are capped to bound the DP cost.
    """
    a = query_key[:cap]
    b = _surface_key(text)[:cap]
    if a == b:
        return 0.0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 1.0
    # Classic two-row Damerau-Levenshtein (optimal string alignment).
    prev2: list[int] | None = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if (
                i > 1 and j > 1
                and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]
            ):
                cur[j] = min(cur[j], (prev2 or cur)[j - 2] + 1)  # transposition
        prev2, prev = prev, cur
    return prev[lb] / max(la, lb)


def _cased_token_hits(typed_tokens: list[str], text: str) -> int:
    """Count acronym tokens appearing in ``text`` as the user typed them (AMUX-20).

    Only tokens the user typed with casing ('MI', 'PT', 'A1c') corroborate,
    matched as a whole word either in the typed form or fully uppercased:
    '(MI)', 'Prothrombin Time (PT) Test', 'HbA1c' hit; 'Mi-2 Ab',
    'Mi-Acid Gas Relief', 'New Pt 10mins' never do. Lowercase-typed tokens
    corroborate nothing (no signal). Deterministic, model-free.
    """
    hits = 0
    for typed in typed_tokens:
        if typed == typed.lower():
            continue
        pattern = rf"\b(?:{re.escape(typed)}|{re.escape(typed.upper())})\b"
        if re.search(pattern, _strip_accents(text)):
            hits += 1
    return hits


def _stem_token(token: str) -> str:
    """Porter-like stemmer to match pre-built BM25 tokenization."""
    token = token.lower()
    for suffix in ("ational", "tional", "iveness", "fulness", "ousness",
                   "ization", "ation", "ations", "izer", "ator", "alism",
                   "iciti", "ical", "ness", "ements", "ement", "ments",
                   "ment", "iences", "ience", "iable", "ing", "ies", "ied",
                   "ily", "sses", "ss", "s", "y", "e"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            if suffix == "ies":
                return token[:-3] + "i"
            if suffix == "ied":
                return token[:-3] + "i"
            if suffix == "sses":
                return token[:-2]
            if suffix == "s" and not token.endswith("ss"):
                return token[:-1]
            if suffix == "e" and len(token) > 3:
                return token[:-1]
            if suffix == "ing":
                return token[:-3]
            if suffix == "y":
                return token[:-1] + "i"
            return token[: -len(suffix)]
    return token


def apply_preferred_display(
    results: list[SearchResult],
    engine,
) -> list[SearchResult]:
    """Canonicalize legacy-mode result displays to the engine preferred term.

    QC-400 (MEDIUM) — the QC-317 fix was applied on the FHIR $search surface
    only, so the same query/mode returned identical codes, scores, and ranking
    but DIFFERENT display strings on FHIR vs Python/CLI/MCP. The fix lives in
    this service (single source of truth) so every surface that passes an
    engine inherits it. The BM25 search index is cross-source: the matched
    ``display`` can be an anchor/CHV synonym that does not exist in the
    result's own code system (e.g. SNOMED 73211009 surfaced as 'Diabetes',
    which is not any SNOMED synonym). Resolve via ONE batched get_code_infos
    call; fall back to the matched index synonym only when no preferred atom
    resolves. Requires an engine — the CLI ``search`` command deliberately
    opens no database (QC-382) and therefore keeps the index display.
    """
    from medterm4ds.services.lookup import get_code_infos

    infos = get_code_infos(
        [CodeRef(r.source, r.code) for r in results], engine=engine
    )
    updated: list[SearchResult] = []
    for r, info in zip(results, infos, strict=False):
        name = info.name if info else None
        updated.append(replace(r, display=name) if name else r)
    return updated


class SearchService:
    """Unified search service with lazy-loaded BM25 + SapBERT indexes.

    Thread-safe singleton pattern. First call to any search method triggers
    index loading. Subsequent calls reuse cached indexes.
    """

    def __init__(
        self,
        *,
        search_index_dir: str | None = None,
        embedding_model_dir: str | None = None,
        canonical_path: str | None = None,
        canonical_concepts_index: str | None = None,
        canonical_concepts_meta: str | None = None,
    ):
        # AMUX-44 acquisition story (one story, three surfaces):
        #   explicit value (env pin / operator arg) -> pinned, used as-is;
        #   None (env unset) -> release-aware 'auto' resolution:
        #     terminology release -> split -> legacy cache dir, with
        #     first-use provisioning (never a machine-specific path).
        # The DEFAULT_* constants only name where 'auto' provisioning lands
        # (cache-root derived) and double as unpinned sentinels for legacy
        # callers passing them explicitly.
        self._bm25_dir_pinned = (
            search_index_dir is not None
            and search_index_dir != DEFAULT_SEARCH_INDEX_DIR
        )
        self._model_dir_pinned = (
            embedding_model_dir is not None
            and embedding_model_dir != DEFAULT_EMBEDDING_MODEL_DIR
        )
        self._bm25_dir = Path(search_index_dir or DEFAULT_SEARCH_INDEX_DIR)
        self._model_dir = Path(embedding_model_dir or DEFAULT_EMBEDDING_MODEL_DIR)
        self._canonical_path = Path(canonical_path or DEFAULT_CANONICAL_VALUE_SETS_PATH)
        self._concepts_index_path = Path(
            canonical_concepts_index or DEFAULT_CANONICAL_CONCEPTS_INDEX
        )
        self._concepts_meta_path = Path(
            canonical_concepts_meta or DEFAULT_CANONICAL_CONCEPTS_META
        )
        # Split-layout resolution only applies to DEFAULT (unpinned) paths —
        # explicitly passed dirs (operator contracts, fhir4ds
        # FHIR4DS_TERMINOLOGY_SEARCH_INDEX_DIR passthrough, provision flows)
        # are used exactly as given (plan §6.2).
        self._paths_pinned = (
            (
                canonical_path is not None
                and canonical_path != DEFAULT_CANONICAL_VALUE_SETS_PATH
            )
            or (
                canonical_concepts_index is not None
                and canonical_concepts_index != DEFAULT_CANONICAL_CONCEPTS_INDEX
            )
            or (
                canonical_concepts_meta is not None
                and canonical_concepts_meta != DEFAULT_CANONICAL_CONCEPTS_META
            )
        )
        self._resolved_index_dir: str | None = None
        self._concepts_data_dir: Path | None = None
        self._concepts_data_manifest: dict | None = None
        self._concepts_space_checked = False
        self._bm25_indexes: dict[str, dict] = {}
        self._semantic_engine = None
        self._bm25_loaded = False
        self._canonical_loaded = False
        self._canonical_by_id: dict[str, dict] = {}
        self._group_terms: dict[str, dict] = {}
        self._alias_to_cid: dict[str, str] = {}
        self._canonical_by_anchor: dict[tuple[str, str], str] = {}
        self._code_to_canonical_id: dict[tuple[str, str], str] = {}
        self._concepts_faiss = None
        self._concepts_meta: list[dict] = []
        self._concepts_loaded = False

    def ensure_lexical(self) -> bool:
        """First-use lexical provisioning: load BM25 (downloading when the
        dirs are unpinned), returning availability.

        The FHIR $search availability gate calls this so a fresh install
        provisions on first use instead of 503ing until some other
        surface happens to warm the cache. Explicitly pinned index dirs
        (operator contracts, isolation-pinned tests) are never
        auto-provisioned — the 503 contract for those is unchanged.
        """
        if self.lexical_available:
            return True
        if self._bm25_dir_pinned:
            return False
        try:
            self._ensure_bm25()
        except Exception as exc:  # noqa: BLE001 — best-effort availability probe
            logger.warning(
                "Lexical provisioning unavailable: %s: %s",
                type(exc).__name__, exc,
            )
            return False
        return self.lexical_available

    def ensure_semantic(self) -> bool:
        """First-use semantic provisioning (see ``ensure_lexical``)."""
        if self.semantic_available:
            return True
        if self._model_dir_pinned:
            return False
        try:
            self._ensure_semantic()
        except Exception as exc:  # noqa: BLE001 — best-effort availability probe
            logger.warning(
                "Semantic provisioning unavailable: %s: %s",
                type(exc).__name__, exc,
            )
            return False
        return self.semantic_available

    @property
    def lexical_available(self) -> bool:
        """Whether lexical BM25 assets are available (read-only, no download).

        Terminology-layout aware: with the DEFAULT (unpinned) index dir and
        a locally present ``fhir4ds/terminology`` release, the lexical
        family counts even though the legacy dir is absent (the
        fresh-install path — CI runners, new machines).
        """
        if self._bm25_loaded:
            return True
        if self._bm25_dir.is_dir() and any(self._bm25_dir.glob("*_bm25.json")):
            return True
        if not self._bm25_dir_pinned:
            term = _resolve_terminology(download=False)
            if term is not None and any(term.lexical.glob("*.json")):
                return True
        return False

    @property
    def semantic_available(self) -> bool:
        """Whether semantic search assets are available (read-only, no download).

        Split-layout aware: when the model dir is not pinned and a local
        split models/<space>/ unit exists for an accepted embedding space,
        semantic search is available even if the legacy semantic/ dir is
        absent (the post-`cache-refresh --split` state). The terminology
        release's model unit (model/<space>/) counts the same way.
        """
        if not self._model_dir_pinned:
            if _local_split_model_dir() is not None:
                return True
            term = _resolve_terminology(download=False)
            if term is not None and (term.model / "model.safetensors").exists():
                return True
        return (self._model_dir / "model.safetensors").exists()

    def _ensure_bm25(self) -> None:
        if self._bm25_loaded:
            return
        # Terminology layout: <family>/<release>/<category>.json — resolved
        # before the legacy download so the new default source wins.
        if not self._bm25_dir_pinned:
            term = _resolve_terminology()
            if term is not None:
                self._bm25_dir = term.lexical
        if not self._bm25_dir.is_dir() or not any(
            p.name.endswith("_bm25.json") or p.suffix == ".json"
            for p in self._bm25_dir.iterdir()
        ):
            # EV05-QA-03 (QC2-A11 pinned contract): an explicitly pinned
            # index dir is an operator/test isolation contract — when it is
            # missing, raise (below) instead of attempting an HF download.
            # The lexical/semantic ensure paths already honor this; $extract
            # mode=lexical reaches _ensure_bm25 with no ensure_lexical gate.
            if self._bm25_dir_pinned:
                raise RuntimeError(
                    f"BM25 index directory not found: {self._bm25_dir} "
                    "(MEDTERM4DS_SEARCH_INDEX_DIR is pinned; refusing to "
                    "download)"
                )
            _hf_download_atomic(
                ["lexical/*"], target_root=_CACHE_DIR, family="lexical",
                revision=_HF_REVISION,
            )
        if not self._bm25_dir.is_dir():
            raise RuntimeError(f"BM25 index directory not found: {self._bm25_dir}")
        for category in SEARCH_CATEGORIES:
            json_path = self._bm25_dir / f"{category}_bm25.json"
            if not json_path.exists():
                json_path = self._bm25_dir / f"{category}.json"
            if json_path.exists():
                with json_path.open() as f:
                    index = json.load(f)
                if isinstance(index, dict) and "postings" in index:
                    self._bm25_indexes[category] = index
                    logger.info("BM25 %s: %d records", category, index.get("num_records", 0))
        self._bm25_loaded = True

    def _split_compatible_legacy_index_dir(self, split_dir: Path) -> str | None:
        """Legacy semantic/ dir usable as per-category index source for a split model.

        The bridge is valid ONLY when weights + tokenizer are byte-identical
        (md5) — same-build proof, computed once per resolution; both dirs are
        local so no download is involved. Render-policy staleness of the
        legacy per-category indexes is pre-existing v0.0.5 behavior, not
        worsened by the bridge (transitional, logged).
        """
        legacy = self._model_dir  # DEFAULT_EMBEDDING_MODEL_DIR (legacy semantic/)
        try:
            from medterm4ds.core.artifact_manifest import md5_of_file

            for name in ("model.safetensors", "tokenizer.json"):
                if not (legacy / name).exists() or not (split_dir / name).exists():
                    return None
                if md5_of_file(legacy / name) != md5_of_file(split_dir / name):
                    return None
            if not any(
                (legacy / f"{cat}_faiss.index").exists() for cat in SEARCH_CATEGORIES
            ):
                return None
        except OSError:
            return None
        logger.info(
            "Bridging per-category FAISS indexes from legacy %s to split "
            "model %s (byte-identical weights + tokenizer).",
            legacy, split_dir,
        )
        return str(legacy)

    def _resolve_semantic_layouts(self) -> tuple[str, str | None]:
        """Resolve (model_dir, index_dir) for the semantic engine.

        Pinned dirs are used as-is (operator contract). Otherwise:
        (a) local split models/<space>/ unit → use it, bridging per-category
            FAISS indexes from the legacy semantic/ dir when provably from
            the same build (byte-identical weights + tokenizer);
        (b) legacy semantic/ dir present → serve it with a one-time
            deprecation nudge;
        (c) nothing cached → download the split unit from main; only on
            download failure fall back to the legacy semantic/* download.
        """
        if self._model_dir_pinned:
            return (
                str(self._model_dir),
                os.getenv("MEDTERM4DS_SEMANTIC_INDEX_DIR") or None,
            )

        term = _resolve_terminology()
        if term is not None:
            self._resolved_index_dir = str(term.semantic)
            return str(term.model), str(term.semantic)

        split_dir = _local_split_model_dir()
        if split_dir is None and not self.semantic_available:
            try:
                split_dir = _download_split_model()
            except Exception as exc:
                logger.warning(
                    "Split model download failed (%s); falling back to the "
                    "legacy semantic/* download.", exc,
                )
        if split_dir is not None:
            bridge = self._resolved_index_dir or self._split_compatible_legacy_index_dir(split_dir)
            self._resolved_index_dir = bridge
            index_dir = os.getenv("MEDTERM4DS_SEMANTIC_INDEX_DIR") or bridge
            if index_dir is None:
                logger.warning(
                    "Serving split model %s WITHOUT per-category FAISS "
                    "indexes: semantic mode is degraded (canonical concept "
                    "search and hybrid re-ranking are unaffected). The "
                    "legacy semantic/ dir is absent or was built from "
                    "different weights.",
                    split_dir,
                )
            return str(split_dir), index_dir

        if not self.semantic_available:
            _hf_download_atomic(
                ["semantic/*"], target_root=_CACHE_DIR, family="semantic",
                revision=_HF_REVISION,
            )
        if not self.semantic_available:
            raise RuntimeError(f"SapBERT model not found at {self._model_dir}")
        if not _LAYOUT_FORCED_LEGACY:
            _maybe_log_legacy_deprecation()
        return str(self._model_dir), None

    def _ensure_semantic(self):
        if self._semantic_engine is not None:
            return self._semantic_engine
        model_dir, index_dir = self._resolve_semantic_layouts()
        from medterm4ds.engines.fhir.semantic import SemanticSearchEngine
        # Terminology model units carry no manifest.json; the release
        # manifest's space (already fingerprint-verified in the resolver)
        # is handed to the engine so space cross-checks stay live.
        term = _resolve_terminology()
        self._semantic_engine = SemanticSearchEngine(
            model_dir,
            index_dir=index_dir,
            space_id=term.space_id if term is not None else None,
        )
        return self._semantic_engine

    def _resolve_split_data_dir(self) -> Path | None:
        """Resolve the split data/<revision> dir; None means use legacy.

        Manifest shape problems and unacceptable embedding spaces are loud
        (ManifestError propagates — a malformed or foreign-space published
        unit must not silently serve); network or not-published-yet
        conditions degrade to the legacy layout with a warning.
        """
        from medterm4ds.core.artifact_manifest import (
            ACCEPTED_EMBEDDING_SPACES,
            ManifestError,
            read_manifest,
            validate_data_manifest,
        )

        target = _local_latest_data_dir()
        if target is None:
            try:
                target = _download_split_data()
            except ManifestError:
                raise
            except Exception as exc:
                logger.warning(
                    "Split data layout unavailable (%s); falling back to "
                    "legacy canonical artifacts.", exc,
                )
                return None
        manifest = read_manifest(target)
        if manifest is None:
            raise ManifestError(f"Data unit at {target} has no manifest.json")
        validate_data_manifest(manifest, source=str(target))
        declared = manifest.get("embedding_space_id")
        if ACCEPTED_EMBEDDING_SPACES and declared not in ACCEPTED_EMBEDDING_SPACES:
            raise ManifestError(
                f"Data unit at {target} declares embedding space "
                f"{declared!r}, which this medterm4ds release does not "
                f"accept (accepted: {sorted(ACCEPTED_EMBEDDING_SPACES)}). "
                "Pin MEDTERM4DS_DATA_REVISION to an accepted revision or "
                "upgrade medterm4ds."
            )
        return target

    def _ensure_canonical(self) -> None:
        if self._canonical_loaded:
            return
        path = self._canonical_path
        if not self._paths_pinned:
            term = _resolve_terminology()
            if term is not None:
                path = term.canonical / "value_sets.json"
            data_dir = None if term is not None else self._resolve_split_data_dir()
            if data_dir is not None:
                if not (data_dir / "canonical_anchor_value_sets.json").exists():
                    raise RuntimeError(
                        f"Split data unit at {data_dir} is missing "
                        "canonical_anchor_value_sets.json"
                    )
                path = data_dir / "canonical_anchor_value_sets.json"
        if not path.exists():
            _hf_download_atomic(
                ["canonical/*"], target_root=_CACHE_DIR, family="canonical",
                revision=_HF_REVISION,
            )
        if not path.exists():
            raise RuntimeError(f"Canonical ValueSets file not found: {path}")
        if path == self._canonical_path and not _LAYOUT_FORCED_LEGACY:
            _maybe_log_legacy_deprecation()
        with path.open() as f:
            vsets = json.load(f)
        for v in vsets:
            cid = v["canonical_id"]
            self._canonical_by_id[cid] = v
            anchor_system = v.get("anchor_system") or v.get("system")
            anchor_code = v.get("anchor_code") or v.get("code")
            self._canonical_by_anchor[(anchor_system, anchor_code)] = cid
            self._code_to_canonical_id[(anchor_system, anchor_code)] = cid
            for m in v.get("members", []):
                self._code_to_canonical_id[(m["system"], m["code"])] = cid
        self._build_group_dictionary(vsets)
        self._build_alias_dictionary(vsets)
        self._canonical_loaded = True
        logger.info("Loaded %d Master Canonical Value Sets (%d code pairs, %d group terms, %d aliases)",
                    len(vsets), len(self._code_to_canonical_id), len(self._group_terms),
                    len(self._alias_to_cid))

    def _build_group_dictionary(self, vsets: list[dict]) -> None:
        """Inverted index for the composite-group dictionary pass.

        Terms map to {group_id, parent_cid, member_cids}: the materialized
        composite_groups term (panel display name), plus the parent anchor's
        name and consumer synonyms when the composite is itself anchored.
        Group membership = anchors carrying the group_id; the parent is the
        anchor whose code equals the group_id suffix (join contract:
        group_id == f'GP-{anchor code}').

        EV02-QA-01/QA-03 (2026-11-11 evolution): term keys fold accents (the
        QC-329 matched-pair contract — query side mirrors index side) and
        terms claimed by 2+ groups are excluded, mirroring the alias
        dictionary's ambiguity exclusion (was: silent first-wins).
        """
        members_by_group: dict[str, list[str]] = {}
        terms: dict[str, str] = {}
        ambiguous_terms: set[str] = set()
        for v in vsets:
            # Same structural-hub exclusion as the alias dictionary: hubs
            # are absent from the concept index and must not surface as
            # exact group matches either (amux-canonical 1cdb6ef).
            if v.get("structural_hub"):
                continue
            cid = v["canonical_id"]
            for g in v.get("composite_groups") or []:
                gid = g.get("group_id")
                if not gid:
                    continue
                lst = members_by_group.setdefault(gid, [])
                if cid not in lst:
                    lst.append(cid)
                # Same normalization as the query side (lowercase + accent
                # fold + whitespace collapse) so term/query normalization can
                # never diverge.
                term = _alias_key(g.get("term") or "")
                if term:
                    if terms.get(term, gid) != gid:
                        ambiguous_terms.add(term)
                    else:
                        terms[term] = gid
        # parent anchor per group (code == gid suffix) + its name/synonyms as terms.
        # Keyed by bare code — group families (LOINC panels, CVX combos,
        # RxNorm INs) do not collide across systems; revisit if a
        # mixed-system group family ever appears.
        parent_by_group: dict[str, str] = {}
        code_to_cid = {
            code: cid for (system, code), cid in self._canonical_by_anchor.items()
            if not (self._canonical_by_id.get(cid, {}).get("structural_hub"))
        }
        for gid in members_by_group:
            suffix = gid[3:] if gid.startswith("GP-") else gid
            pcid = code_to_cid.get(suffix)
            if pcid:
                parent_by_group[gid] = pcid
                pa = self._canonical_by_id.get(pcid, {})
                for t in [pa.get("patient_friendly_name")] + list(pa.get("consumer_synonyms") or []):
                    if t and str(t).strip():
                        key = _alias_key(str(t))
                        if terms.get(key, gid) != gid:
                            ambiguous_terms.add(key)
                        else:
                            terms[key] = gid
        for key in ambiguous_terms:
            terms.pop(key, None)
        self._group_terms = {
            t: {"group_id": gid,
                "parent_cid": parent_by_group.get(gid),
                "member_cids": sorted(members_by_group[gid])}
            for t, gid in terms.items()
        }

    def _build_alias_dictionary(self, vsets: list[dict]) -> None:
        """Exact-alias index over anchor names and their synonyms.

        Abbreviation queries ('HLD', 'DM2', 'ASA', 'NSTEMI') name their
        anchor exactly but cannot reach it through embedding search —
        SapBERT ranks semantic neighbours ('HLD' -> Hodgkin's Lymphoma)
        above the true anchor. An exact alias hit returns the anchor
        directly (2026-11-11 addendum B). Ambiguous aliases (claimed by
        2+ anchors) are excluded — an exact match must be unambiguous.
        """
        alias_map: dict[str, str] = {}
        ambiguous: set[str] = set()
        for v in vsets:
            # Structural hubs (LOINC grouping parents like LP130385-0
            # 'Infection') are absent from the concept index by design —
            # the dictionary passes must skip them too, or an exact alias
            # returns an anchor the embedding path would never serve
            # (canonical('infection') -> structural LOINC hub at 1.0
            # instead of the real condition; amux-canonical 1cdb6ef).
            if v.get("structural_hub"):
                continue
            cid = v["canonical_id"]
            names = [v.get("patient_friendly_name"), v.get("snomed_pt"),
                     v.get("rxnorm_display")]
            names += list(v.get("consumer_synonyms") or [])
            names += list(v.get("anchor_synonyms") or [])
            for nm in names:
                if not nm or not str(nm).strip():
                    continue
                key = _alias_key(str(nm))
                if not key:
                    continue
                if alias_map.get(key, cid) != cid:
                    ambiguous.add(key)
                else:
                    alias_map[key] = cid
        for key in ambiguous:
            alias_map.pop(key, None)
        self._alias_to_cid = alias_map

    def _alias_dictionary_search(
        self,
        query: str,
        *,
        result_types: str | list[str] | None,
        sources: list[str] | None,
    ) -> CanonicalSearchResult | None:
        """Exact consumer/anchor-alias pass (before embedding search).

        Returns the uniquely-claimed anchor for the query's normalized
        form, or None (caller falls through to embedding search). The
        result_types/sources filters are honored: a filtered-out anchor
        is a miss, not a forced return.
        """
        if not self._alias_to_cid:
            return None
        cid = self._alias_to_cid.get(_alias_key(query))
        if cid is None:
            return None
        v = self._canonical_by_id.get(cid)
        if v is None:
            return None
        anchor_system = v.get("anchor_system") or v.get("system")
        if sources:
            # Canonical SAB aliasing (core.normalize.SOURCE_SAB_ALIASES) —
            # same contract as _ancestor_fallback.
            if anchor_system.upper() not in expand_source_aliases(sources):
                return None
        if result_types:
            prefixes = _result_types_to_prefixes(result_types)
            if prefixes and not any(cid.startswith(p) for p in prefixes):
                return None
        return CanonicalSearchResult(
            canonical_id=cid,
            domain=v.get("domain", []),
            anchor_system=anchor_system,
            anchor_code=v.get("anchor_code") or v.get("code"),
            patient_friendly_name=v.get("patient_friendly_name", ""),
            score=1.0,
            match_grade="exact",
            matched_via_code=f"alias:{anchor_system}:{v.get('anchor_code') or v.get('code')}",
            matched_via_display=query,
            total_member_count=self._total_member_count(v),
            members=v.get("members", []),
            combination_members=v.get("combination_members", []),
        )

    def _group_dictionary_search(
        self,
        query: str,
        *,
        result_types: str | list[str] | None,
        sources: list[str] | None,
        count: int,
    ) -> list[CanonicalSearchResult]:
        """Exact-match dictionary pass over composite-group terms.

        Runs BEFORE embedding search (DECOMPOSITION_ARCHITECTURE.md 5): a
        query that exactly names a composite returns the group fan-out —
        parent CID first (naive consumers keep today's behavior), then
        components — each row tagged with group_id/group_cids. Group-ID
        identity is the trigger, never score ties.
        """
        hit = self._group_terms.get(_alias_key(query))
        if hit is None:
            return []
        gid = hit["group_id"]
        row_cids: list[str] = []
        if hit["parent_cid"]:
            row_cids.append(hit["parent_cid"])
        row_cids += [c for c in hit["member_cids"] if c != hit["parent_cid"]]

        result_type_prefixes = _result_types_to_prefixes(result_types)
        source_set = set(sources or []) or None
        if source_set:
            # Pairwise system aliasing only (same pairs the extraction-side
            # source filter uses) — NOT an admit-all widening. The previous
            # form added every major system whenever ANY filter was set,
            # so canonical("lipid panel", sources=["CVX"]) returned LOINC
            # rows: the caller's filter became a no-op (caught by
            # test_source_filter_excludes_foreign_rows).
            upper = {s.upper() for s in source_set}
            aliases = {
                "LNC": {"LOINC"}, "LOINC": {"LNC"},
                "RXNORM": {"ATC"}, "ATC": {"RXNORM"},
                "SNOMED": {"SNOMEDCT_US"}, "SNOMEDCT_US": {"SNOMED"},
            }
            widened = set(upper)
            for s in upper:
                widened |= aliases.get(s, set())
            source_set = widened

        rows: list[CanonicalSearchResult] = []
        for cid in row_cids:
            v = self._canonical_by_id.get(cid)
            if v is None:
                continue
            anchor_system = v.get("anchor_system") or v.get("system")
            if source_set and anchor_system.upper() not in source_set:
                continue
            if result_type_prefixes and not any(cid.startswith(p) for p in result_type_prefixes):
                continue
            rows.append(CanonicalSearchResult(
                canonical_id=cid,
                domain=v.get("domain", []),
                anchor_system=anchor_system,
                anchor_code=v.get("anchor_code") or v.get("code"),
                patient_friendly_name=v.get("patient_friendly_name", ""),
                score=1.0,
                match_grade="exact",
                matched_via_code=f"group:{gid}",
                matched_via_display=query,
                total_member_count=self._total_member_count(v),
                members=v.get("members", []),
                combination_members=v.get("combination_members", []),
                group_id=gid,
                group_cids=row_cids,
            ))
            if len(rows) >= count:
                break
        return rows

    def _resolve_categories(self, sources: list[str] | None) -> list[str]:
        if sources is None:
            return list(SEARCH_CATEGORIES)
        cats: list[str] = []
        for source in sources:
            source_upper = source.upper()
            cats.extend(_SOURCE_TO_CATEGORIES.get(source_upper, []))
        return list(dict.fromkeys(cats))  # dedupe, preserve order

    @staticmethod
    def _restrict_categories(
        categories: list[str], result_types: str | list[str] | None
    ) -> list[str]:
        """Intersect search categories with requested ``result_types``.

        Legacy-mode analogue of ``canonical(result_types=...)``: restricts
        which category indexes are searched BEFORE retrieval, so ``count``
        caps the filtered result set instead of truncating before a
        client-side filter drops non-matching rows (``search
        --result-types lab --limit 1`` returned 0 results pre-fix).

        Values that are not SEARCH_CATEGORIES (e.g. canonical-only types
        like "symptom") intersect away; an empty intersection means the
        request can match nothing, which callers turn into an empty result.
        """
        if not result_types:
            return categories
        if isinstance(result_types, str):
            result_types = [result_types]
        wanted = set(result_types)
        return [c for c in categories if c in wanted]

    @staticmethod
    def _filter_by_source(
        results: list[SearchResult], sources: list[str] | None
    ) -> list[SearchResult]:
        """Drop results whose source isn't in the requested set.

        Category-level filtering alone isn't sufficient — ICD10CM and SNOMEDCT_US
        both map to the "condition" category, so a SNOMED-only request would
        otherwise return ICD-10 codes that share a concept name (e.g., querying
        "Crohn disease" with sources=['SNOMEDCT_US'] was returning K50.91).
        """
        if not sources:
            return results
        allowed = {s.upper() for s in sources}
        return [r for r in results if r.source.upper() in allowed]

    @staticmethod
    def _total_member_count(vset: dict, m: dict | None = None) -> int:
        """Resolve the value set's true member count (QC-143).

        Read-side fallback chain — the deployed artifacts are NOT rebuilt:
        (a) ``total_member_count`` on the value-set JSON (absent on all
        47,266 deployed value sets), (b) ``total_member_count`` on the
        concepts-metadata entry (present but 0 on all 91,398 entries — the
        index build never populated it), (c) ``member_count`` on the
        value-set JSON (present on 18,917), (d) ``len(members)``.
        """
        total = vset.get("total_member_count", (m or {}).get("total_member_count", 0))
        if total:
            return int(total)
        total = vset.get("member_count", 0)
        if total:
            return int(total)
        return len(vset.get("members", []))

    def _ensure_concepts(self):
        """Lazy-load canonical concept FAISS index + metadata (Option C-B)."""
        if self._concepts_loaded:
            return
        index_path = self._concepts_index_path
        meta_path = self._concepts_meta_path
        if not self._paths_pinned:
            term = _resolve_terminology()
            if term is not None:
                index_path = term.semantic / "anchors.faiss"
                meta_path = term.semantic / "anchors_metadata.json"
            data_dir = None if term is not None else self._resolve_split_data_dir()
            if data_dir is not None:
                from medterm4ds.core.artifact_manifest import (
                    ManifestError,
                    read_manifest,
                    validate_index_lineage,
                )

                manifest = read_manifest(data_dir)
                if manifest is None:
                    raise ManifestError(
                        f"Data unit at {data_dir} has no manifest.json"
                    )
                self._concepts_data_dir = data_dir
                self._concepts_data_manifest = manifest
                index_path = data_dir / "canonical_concepts_faiss.index"
                meta_path = data_dir / "canonical_concepts_metadata.json"
                if not (index_path.exists() and meta_path.exists()):
                    raise RuntimeError(
                        f"Split data unit at {data_dir} is missing the "
                        "canonical concepts index/metadata pair"
                    )
                # Dual-edge check: index+metadata md5s vs the manifest, and
                # the lineage must match the manifest's declared space.
                validate_index_lineage(
                    manifest,
                    data_dir,
                    serving_space_id=str(manifest.get("embedding_space_id", "")),
                    data_revision=str(manifest.get("data_revision", "")) or None,
                    source=f"{data_dir} (canonical concepts)",
                )
        if not index_path.exists() or not meta_path.exists():
            _hf_download_atomic(
                ["canonical/*"], target_root=_CACHE_DIR, family="canonical",
                revision=_HF_REVISION,
            )
        if not index_path.exists() or not meta_path.exists():
            self._concepts_loaded = True  # mark as checked; concept search unavailable
            return
        import faiss
        self._concepts_faiss = faiss.read_index(str(index_path))
        with meta_path.open() as f:
            self._concepts_meta = json.load(f)
        self._concepts_loaded = True
        logger.info("Loaded canonical concept index: %d vectors", self._concepts_faiss.ntotal)

    def _check_concepts_space(self, engine) -> None:
        """Hard space cross-check once the serving engine is loaded.

        The split data manifest's declared embedding_space_id must equal the
        engine's manifest-validated serving space — mixing spaces serves
        wrong vectors. Legacy layouts (engine space None) keep today's
        semantics. Runs once per service instance.
        """
        if self._concepts_space_checked:
            return
        # EV05-QA-02 fix (2026-11-11 evolution): latch ONLY after successful
        # validation. The prior latch-before-validate form permanently
        # skipped the space check on the process singleton after one raise —
        # the first request 503'd loud while every later request silently
        # served on the mismatched embedding space.
        if self._concepts_data_manifest is None:
            self._concepts_space_checked = True
            return
        serving_space_id = getattr(engine, "space_id", None)
        if serving_space_id is None:
            self._concepts_space_checked = True
            return
        from medterm4ds.core.artifact_manifest import validate_data_manifest

        validate_data_manifest(
            self._concepts_data_manifest,
            serving_space_id=serving_space_id,
            source=str(self._concepts_data_dir),
        )
        self._concepts_space_checked = True

    # amux-canonical tie-rule spec (agreed by all three lanes, 2026-11-12):
    # candidates whose scores differ by less than this epsilon count as
    # TIED — tie detection must be score-epsilon based, never exact-float,
    # vector-index order, or string order. 1e-6 covers the identical-vector
    # case (exact 1.000 ties from cross-type twins) plus 1-ULP float noise
    # between the batch and single embed paths.
    CANONICAL_TIE_EPSILON = 1e-6

    # Cross-type twin tie priority (owner ruling, Joel 2026-11-12): for
    # equal-score ties the order is TYPE-based, mirroring the extraction
    # layer's doctrine (no context → drug family wins; labs lose unless a
    # consumer applies its own cue logic). Lower rank = first.
    _TIE_TYPE_PRIORITY = {
        "VAX": 0,        # safety-relevant, specific
        "MED": 1,        # no context → medication (extraction doctrine)
        "DRUGCLASS": 2,  # drug family, after the ingredient
        "VIT": 3,        # vital measurement beats the lab twin
        "LAB": 4,        # loses without lab cues (extraction doctrine)
        "PROC": 5,       # ordering of a measurement ranks below the analyte
        "COND": 6,       # condition beats symptom
        "SYMP": 7,
    }
    _TIE_TYPE_DEFAULT_RANK = 8

    # Exact class terms whose ATC DRUGCLASS anchor must outrank the
    # verbatim-identical RxNorm ingredient anchor on ties (amux-canonical
    # AMUX-107 follow-up, 2026-11-12: canonical keeps both anchors; the
    # class-vs-ingredient preference belongs here). Matched on the
    # anchor's primary name, lowercased.
    CLASS_PREFERRED_TERMS = frozenset({
        "estrogens",
        "belladonna alkaloids",
        "posterior pituitary hormones",
    })

    @classmethod
    def _tie_type_rank(cls, canonical_id: str) -> tuple[int, str]:
        typ = canonical_id.split("-")[1] if "-" in canonical_id else ""
        return (cls._TIE_TYPE_PRIORITY.get(typ, cls._TIE_TYPE_DEFAULT_RANK), typ)

    @classmethod
    def _break_exact_ties(
        cls,
        results: list[CanonicalSearchResult],
    ) -> list[CanonicalSearchResult]:
        """Deterministic order for epsilon-tied scores (cross-type twins).

        Identical anchor texts (e.g. 'Methotrexate' as a medication anchor
        and as a LOINC lab-ingredient anchor) embed to IDENTICAL vectors,
        so FAISS returns exact score ties — and ``IndexFlatIP`` settles
        those ties by opaque internal layout, not insertion order (two
        release builds with opposite emission orders flipped every
        twin query's top-1). The tie policy lives HERE, at the ranking
        layer, so results are build-independent.

        Spec (owner ruling, Joel 2026-11-12 — supersedes the earlier
        type-neutral amux-canonical proposal):
          1. tie detection is SCORE-EPSILON based (CANONICAL_TIE_EPSILON)
          2. within a tie group: type priority
             VAX > MED > DRUGCLASS > VITAL > LAB > PROC > COND > SYMP
             (mirrors the extraction layer's med-precedence doctrine:
             with no context the drug family wins; labs lose unless the
             consumer applies its own cue logic, e.g. 'level'/'draw'),
             then canonical_id ASC as the final deterministic key
          3. EXCEPTION (amux-canonical AMUX-107 follow-up): for the named
             class terms in CLASS_PREFERRED_TERMS ('estrogens',
             'belladonna alkaloids', 'posterior pituitary hormones'),
             the DRUGCLASS anchor outranks the ingredient within the tie
          4. order-only: nothing is dropped, non-tied ordering unchanged;
             both type variants always survive the tie (downstream
             consumers see them all)

        The extraction layer's type-aware cross-type tie rule still
        applies its own doctrine downstream on labeled queries.
        """
        if len(results) < 2:
            return results
        ordered = sorted(results, key=lambda r: -r.score)
        out: list[CanonicalSearchResult] = []
        i = 0
        while i < len(ordered):
            anchor = ordered[i]
            j = i + 1
            while (
                j < len(ordered)
                and anchor.score - ordered[j].score < cls.CANONICAL_TIE_EPSILON
            ):
                j += 1
            group = ordered[i:j]
            # Named class terms (CLASS_PREFERRED_TERMS): the DRUGCLASS
            # anchor outranks the ingredient within this tie group —
            # amux-canonical AMUX-107 follow-up; the preference is
            # app-side by agreement.
            class_preferred = any(
                (r.patient_friendly_name or "").strip().lower()
                in cls.CLASS_PREFERRED_TERMS
                for r in group
            )
            if class_preferred:
                # EV02-QA-02 (run 2): DRUGCLASS first, but keep the type
                # priority for the non-class members of the group (a VAX
                # twin must not be demoted below the class anchor).
                group = sorted(
                    group,
                    key=lambda r: (
                        (0 if r.canonical_id.startswith("VAL-DRUGCLASS-") else 1),
                        *cls._tie_type_rank(r.canonical_id),
                        r.canonical_id,
                    ),
                )
            else:
                group = sorted(
                    group,
                    key=lambda r: (
                        cls._tie_type_rank(r.canonical_id), r.canonical_id),
                )
            out.extend(group)
            i = j
        return out

    def _canonical_concept_search(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        result_types: str | list[str] | None = None,
        count: int = 20,
        min_score: float = 0.70,
    ) -> list[CanonicalSearchResult]:
        """Search the canonical concept FAISS index directly (Option C-B).

        Encodes the query with SapBERT and finds nearest canonical anchor by
        patient_friendly_name. No UMLS code intermediary, no crosswalk
        dependency. Supports ``sources`` filtering via metadata and
        ``result_types`` filtering via canonical_id prefix.

        Results below ``min_score`` (default 0.70) are excluded — this prevents
        confidently wrong matches for rare diseases, brand names, and drug
        classes that have no corresponding canonical anchor.
        """
        self._ensure_canonical()
        self._ensure_concepts()
        if self._concepts_faiss is None:
            return []  # concept index not available

        engine = self._ensure_semantic()
        engine._ensure_loaded()
        self._check_concepts_space(engine)
        query_emb = engine._embed(query)

        # Normalize result_types to a set of canonical_id prefixes.
        # result_types="procedure" → {"VAL-PROC-"}
        # result_types=["condition","symptom"] → {"VAL-COND-", "VAL-SYMP-"}
        result_type_prefixes = _result_types_to_prefixes(result_types)

        # Over-fetch to account for source and result_type filtering
        needs_filter = bool(sources or result_type_prefixes)
        k = min(self._concepts_faiss.ntotal, max(count * 5 if needs_filter else count, 50))
        scores, ids = self._concepts_faiss.search(query_emb, k)

        source_set = set(sources) if sources else None
        # Normalize source names. RXNORM ↔ ATC because drug-class queries
        # commonly filter to ["RXNORM"] but expect class anchors (ATC) to be
        # included — without this normalization, "ACE inhibitors" filters out
        # the ATC class anchor and falls back to a specific drug (Perindopril).
        # Same pattern as LNC ↔ LOINC and SNOMEDCT_US ↔ SNOMED.
        if source_set:
            normalized = set()
            for s in source_set:
                su = s.upper()
                normalized.add(su)
                if su == "LNC":
                    normalized.add("LOINC")
                elif su == "LOINC":
                    normalized.add("LNC")
                elif su == "SNOMED":
                    normalized.add("SNOMEDCT_US")
                elif su == "SNOMEDCT_US":
                    normalized.add("SNOMED")
                elif su == "RXNORM":
                    normalized.add("ATC")
                elif su == "ATC":
                    normalized.add("RXNORM")
            source_set = normalized
        results: list[CanonicalSearchResult] = []
        seen_ids: set[str] = set()
        for score, idx in zip(scores[0], ids[0], strict=False):
            if idx < 0:
                break
            if score < min_score:
                break  # results are sorted by score; stop at threshold
            m = self._concepts_meta[idx]
            cid = m["canonical_id"]
            vset = self._canonical_by_id.get(cid, {})
            # Terminology-layout metadata carries only {canonical_id, name};
            # card fields come from value_sets.json (single source of truth).
            anchor_system = (
                m.get("anchor_system")
                or vset.get("anchor_system")
                or vset.get("system")
            )
            anchor_code = (
                m.get("anchor_code")
                or vset.get("anchor_code")
                or vset.get("code")
            )
            meta_name = m.get("patient_friendly_name") or m.get("name")
            if source_set and anchor_system not in source_set:
                continue
            if result_type_prefixes and not any(cid.startswith(p) for p in result_type_prefixes):
                continue
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            # Anchor's primary name — what we want to display. For alias hits
            # (m["is_alias"]=True), m["patient_friendly_name"] is the alias text
            # (e.g., "Adbry"); we surface the actual anchor name instead
            # (e.g., "Tralokinumab") so downstream consumers don't see brand and
            # ingredient as two separate concepts. The alias text goes only in
            # matched_via_display.
            primary_name = vset.get("patient_friendly_name") or meta_name
            results.append(CanonicalSearchResult(
                canonical_id=cid,
                domain=vset.get("domain", m.get("domain", [])),
                anchor_system=anchor_system,
                anchor_code=anchor_code,
                patient_friendly_name=primary_name,
                score=float(score),
                match_grade="exact" if score > 0.95 else "probable" if score > 0.80 else "possible",
                matched_via_code=f"concept:{anchor_code}",
                matched_via_display=meta_name,
                total_member_count=self._total_member_count(vset, m),
                members=vset.get("members", []),
                combination_members=vset.get("combination_members", []),
            ))
        # EV02-QA-01 (run 2): do NOT break at count inside the loop — the
        # FAISS-layout-first twin would win the boundary slot BEFORE the
        # tie rule runs (top-1 became index-layout-dependent at count=1).
        # Collect the full above-threshold pool, then order, then slice —
        # mirroring the batch path. The k over-fetch bounds the pool.
        # Cross-type twin ties: FAISS tie order is index-layout-dependent
        # (undefined across builds) — apply the deterministic content rule.
        return self._break_exact_ties(results)[:count]

    def _ancestor_fallback(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
    ) -> list[CanonicalSearchResult]:
        """Ancestor fallback for queries that don't match any canonical anchor.

        Finds the SNOMED code for the query via semantic search, walks UP the
        hierarchy, and returns the nearest canonical ANCHOR (not just any
        member). This handles queries for conditions like "Hashimoto Thyroiditis"
        that aren't canonical anchors but ARE descendants of one (e.g.,
        "Hypothyroidism").

        Uses ``_canonical_by_anchor`` (anchor codes only) rather than
        ``_code_to_canonical_id`` (which includes all descendant members and
        can map to the wrong anchor via overly broad descendant walks).

        Returns results with ``match_grade='broader'`` and discounted score.
        """
        import duckdb

        # EV02-QA-02 fix (2026-11-11 evolution): the sources filter now
        # honors the same aliasing contract as the dictionary passes —
        # uppercase + pairwise system aliasing via the canonical
        # SOURCE_SAB_ALIASES map (core.normalize), applied BEFORE the
        # ancestor walk. Every ancestor result is SNOMEDCT_US, so the
        # filter is all-or-nothing: return [] up front instead of paying
        # the full walk and post-filtering a count-truncated list (the
        # prior verbatim case-sensitive check also rejected
        # sources=['SNOMED'] entirely).
        if sources:
            if "SNOMEDCT_US" not in expand_source_aliases(sources):
                return []

        engine = self._ensure_semantic()
        engine._ensure_loaded()

        # Find SNOMED candidate codes for the query
        sem_results = self.semantic(query, sources=['SNOMEDCT_US'], count=max(count * 3, 10))
        if not sem_results:
            return []
        # QC-148: confidence floor on the semantic seed. The direct concept
        # search above thresholds at 0.70, but this fallback previously took
        # the top SNOMED hit unconditionally — for nonsense input SapBERT
        # still returns a nearest neighbor (~0.4-0.5) whose CHV-bearing
        # ancestors surfaced as sensitive 'broader' anchors (Paraphilia,
        # Sexual Sadism Disorder) at scores as low as 0.20. Gate the ancestor
        # walk on the same 0.70 floor so garbage queries return [] like the
        # concept-index path.
        if sem_results[0].score < 0.70:
            return []

        # Collect unique SNOMED codes to check
        snomed_codes = []
        seen_codes = set()
        for r in sem_results:
            if r.code not in seen_codes:
                seen_codes.add(r.code)
                snomed_codes.append((r.code, r.score))

        # Walk UP ancestors for each candidate via closure table (bulk query).
        # UMLS DB location: MEDTERM4DS_UMLS_DB, then MEDTERM4DS_DB; with
        # neither set the fallback is disabled LOUDLY (AMUX-44: the old
        # /mnt/d machine default silently returned [] on every machine
        # but the author's).
        db_path = (
            os.environ.get("MEDTERM4DS_UMLS_DB")
            or os.environ.get("MEDTERM4DS_DB")
        )
        if not db_path:
            logger.warning(
                "Ancestor fallback disabled: no UMLS DB configured. Set "
                "MEDTERM4DS_UMLS_DB (or MEDTERM4DS_DB) to the DuckDB file "
                "with mt4ds.walk_closure_limited to enable broader-result "
                "fallback for semantic queries."
            )
            return []
        try:
            con = duckdb.connect(db_path, read_only=True)
        except Exception as exc:
            logger.warning(
                "Ancestor fallback disabled: cannot open UMLS DB at %s "
                "(%s: %s). Set MEDTERM4DS_UMLS_DB to a valid DuckDB file "
                "with mt4ds.walk_closure_limited.",
                db_path, type(exc).__name__, exc,
            )
            return []

        codes_str = ','.join(f"'{c}'" for c, _ in snomed_codes)
        # walk_closure_limited: from_code=descendant, to_code=ancestor
        # Find ancestors of the query codes. For each ancestor, also check if
        # it has a CHV/MLP consumer name — CHV/MLP presence indicates the
        # ancestor is at the right consumer level (not too broad). We prefer
        # CHV-bearing ancestors over non-CHV ones at the same depth.
        anc_rows = con.execute(f"""
            WITH ancestors AS (
                SELECT c.from_code AS query_code, c.to_code AS ancestor_code, c.depth
                FROM mt4ds.walk_closure_limited c
                WHERE c.source = 'SNOMEDCT_US'
                  AND c.from_code IN ({codes_str})
                  AND c.depth <= 5
            ),
            chv_check AS (
                SELECT DISTINCT a.query_code, a.ancestor_code, a.depth,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM mrconso m1
                        JOIN mrconso m2 ON m2.CUI = m1.CUI
                            AND m2.SAB IN ('CHV', 'MEDLINEPLUS')
                            AND m2.SUPPRESS = 'N'
                            AND m2.LAT = 'ENG'
                        WHERE m1.CODE = a.ancestor_code
                          AND m1.SAB = 'SNOMEDCT_US'
                          AND m1.SUPPRESS = 'N'
                    ) THEN 1 ELSE 0 END AS has_chv
                FROM ancestors a
            )
            SELECT query_code, ancestor_code, depth, has_chv
            FROM chv_check
            ORDER BY query_code, has_chv DESC, depth
        """).fetchall()
        con.close()

        # Build: query_code → [(ancestor_code, depth, has_chv), ...] sorted by
        # CHV preference first, then depth. CHV-bearing ancestors are preferred
        # because they're at the right consumer level.
        ancestors_by_code: dict[str, list[tuple[str, int, int]]] = {}
        for q_code, a_code, depth, has_chv in anc_rows:
            ancestors_by_code.setdefault(q_code, []).append((a_code, depth, has_chv))

        # For each query code, find the best ancestor that is a canonical anchor.
        # Prefer CHV-bearing ancestors (has_chv=1) at the shallowest depth.
        # Fall back to non-CHV ancestors only if no CHV-bearing one is an anchor.
        results: list[CanonicalSearchResult] = []
        seen_ids: set[str] = set()
        for q_code, q_score in snomed_codes:
            ancestors = ancestors_by_code.get(q_code, [])
            # Sort: CHV first, then by depth
            chv_ancestors = [(a, d) for a, d, h in ancestors if h == 1]
            non_chv_ancestors = [(a, d) for a, d, h in ancestors if h == 0]

            found = False
            # Try CHV-bearing ancestors first
            for a_code, depth in sorted(chv_ancestors, key=lambda x: x[1]):
                key = ('SNOMEDCT_US', a_code)
                cid = self._canonical_by_anchor.get(key)
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    vset = self._canonical_by_id.get(cid, {})
                    results.append(CanonicalSearchResult(
                        canonical_id=cid,
                        domain=vset.get("domain", []),
                        anchor_system='SNOMEDCT_US',
                        anchor_code=a_code,
                        patient_friendly_name=vset.get("patient_friendly_name", ""),
                        score=q_score * max(0.3, 1.0 - depth * 0.15),
                        match_grade="broader",
                        matched_via_code=f"chv-ancestor:{a_code}",
                        matched_via_display=vset.get("patient_friendly_name", ""),
                        total_member_count=self._total_member_count(vset),
                        members=vset.get("members", []),
                combination_members=vset.get("combination_members", []),
                    ))
                    found = True
                    break

            if not found:
                # Fall back to non-CHV ancestors (broader but still an anchor)
                for a_code, depth in sorted(non_chv_ancestors, key=lambda x: x[1]):
                    key = ('SNOMEDCT_US', a_code)
                    cid = self._canonical_by_anchor.get(key)
                    if cid and cid not in seen_ids:
                        seen_ids.add(cid)
                        vset = self._canonical_by_id.get(cid, {})
                        results.append(CanonicalSearchResult(
                            canonical_id=cid,
                            domain=vset.get("domain", []),
                            anchor_system='SNOMEDCT_US',
                            anchor_code=a_code,
                            patient_friendly_name=vset.get("patient_friendly_name", ""),
                            score=q_score * max(0.2, 1.0 - depth * 0.20),
                            match_grade="broader",
                            matched_via_code=f"ancestor:{a_code}",
                            matched_via_display=vset.get("patient_friendly_name", ""),
                            total_member_count=self._total_member_count(vset),
                            members=vset.get("members", []),
                combination_members=vset.get("combination_members", []),
                        ))
                        break
            if len(results) >= count:
                break

        return results

    def canonical_batch(
        self,
        queries: list[str],
        *,
        count: int = 5,
        min_score: float = 0.70,
    ) -> list[list[CanonicalSearchResult]]:
        """Batch canonical search — embed all queries in one SapBERT pass.

        Much faster than calling canonical() per query for large query sets
        (e.g., extraction pipelines with 100+ spans). Turns N×100ms into
        ~100ms total for the embedding step.

        Does NOT apply source or result_type filtering — caller post-filters
        per query. Over-fetches (count*3) to leave room for post-filtering.
        """
        _validate_count(count)
        for q in queries:
            _validate_query(q)
        self._ensure_canonical()
        self._ensure_concepts()
        if self._concepts_faiss is None or not queries:
            return [[] for _ in queries]

        engine = self._ensure_semantic()
        # EV06-QA-01 fix (2026-11-11 evolution): the batch path skipped the
        # space cross-check its single-query sibling performs — a mismatched
        # data/model pair served silently wrong vectors in batches.
        engine._ensure_loaded()
        self._check_concepts_space(engine)
        # ARCH-001: batch embedding goes through the engine's public API —
        # the service no longer reaches into _tokenizer/_model internals.
        query_embs = engine.embed_batch(queries)

        # Batch FAISS search (over-fetch to leave room for per-query filtering)
        k = min(self._concepts_faiss.ntotal, max(count * 3, 20))
        scores, ids = self._concepts_faiss.search(query_embs, k)

        # Process results per query
        all_results: list[list[CanonicalSearchResult]] = []
        for q_idx, query in enumerate(queries):
            # EV02-QA-05 fix (2026-11-11 evolution): the composite-group
            # dictionary pass runs BEFORE the alias pass — same dispatch
            # order as canonical(). 336 terms are both group terms and
            # alias keys; without this pass the batch silently served a
            # single alias row where the single-query surface serves the
            # group fan-out.
            group_results = self._group_dictionary_search(
                query, result_types=None, sources=None, count=count)
            if group_results:
                all_results.append(group_results)
                continue
            # Exact-alias pass per query (2026-11-11 addendum B): an exact
            # anchor/consumer-synonym hit is authoritative — it leads the
            # result list. The already-computed FAISS candidates follow
            # (deduped) so consumers that post-filter the alias away (e.g.
            # extraction's label-constrain) still see the same-text
            # candidates canonical() would have served (EV02-QA-04).
            alias_result = self._alias_dictionary_search(
                query, result_types=None, sources=None)
            if alias_result is not None:
                merged = [alias_result]
                # EV06-QA-04 fix: dedup by canonical_id like the embedding
                # loop below — the merged append previously admitted
                # duplicate anchors and wasted count slots.
                seen_ids = {alias_result.canonical_id}
                tail: list[CanonicalSearchResult] = []
                for score, idx in zip(scores[q_idx], ids[q_idx], strict=False):
                    if idx < 0 or score < min_score:
                        break
                    cid = self._concepts_meta[idx]["canonical_id"]
                    if cid in seen_ids:
                        continue
                    seen_ids.add(cid)
                    m = self._concepts_meta[idx]
                    vset = self._canonical_by_id.get(cid, {})
                    anchor_system = (
                        m.get("anchor_system")
                        or vset.get("anchor_system")
                        or vset.get("system")
                    )
                    anchor_code = (
                        m.get("anchor_code")
                        or vset.get("anchor_code")
                        or vset.get("code")
                    )
                    meta_name = m.get("patient_friendly_name") or m.get("name")
                    tail.append(CanonicalSearchResult(
                        canonical_id=cid,
                        domain=vset.get("domain", m.get("domain", [])),
                        anchor_system=anchor_system,
                        anchor_code=anchor_code,
                        patient_friendly_name=vset.get("patient_friendly_name") or meta_name,
                        score=float(score),
                        match_grade="exact" if score > 0.95 else "probable" if score > 0.80 else "possible",
                        matched_via_code=f"concept:{anchor_code}",
                        matched_via_display=meta_name,
                        total_member_count=self._total_member_count(vset, m),
                        members=vset.get("members", []),
                        combination_members=vset.get("combination_members", []),
                    ))
                # Cross-type twin ties in the FAISS tail: same deterministic
                # content rule (see _break_exact_ties); the alias hit keeps
                # the lead regardless.
                merged.extend(self._break_exact_ties(tail)[:count - 1])
                all_results.append(merged)
                continue
            results: list[CanonicalSearchResult] = []
            seen_ids: set[str] = set()
            for score, idx in zip(scores[q_idx], ids[q_idx], strict=False):
                if idx < 0 or score < min_score:
                    break
                m = self._concepts_meta[idx]
                cid = m["canonical_id"]
                vset = self._canonical_by_id.get(cid, {})
                # Terminology-layout metadata carries only {canonical_id, name};
                # card fields come from value_sets.json (single source of truth)
                # — same fallback chain as canonical().
                anchor_system = (
                    m.get("anchor_system")
                    or vset.get("anchor_system")
                    or vset.get("system")
                )
                anchor_code = (
                    m.get("anchor_code")
                    or vset.get("anchor_code")
                    or vset.get("code")
                )
                meta_name = m.get("patient_friendly_name") or m.get("name")
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                primary_name = vset.get("patient_friendly_name") or meta_name
                results.append(CanonicalSearchResult(
                    canonical_id=cid,
                    domain=vset.get("domain", m.get("domain", [])),
                    anchor_system=anchor_system,
                    anchor_code=anchor_code,
                    patient_friendly_name=primary_name,
                    score=float(score),
                    match_grade="exact" if score > 0.95 else "probable" if score > 0.80 else "possible",
                    matched_via_code=f"concept:{anchor_code}",
                    matched_via_display=meta_name,
                    total_member_count=self._total_member_count(vset, m),
                    members=vset.get("members", []),
                    combination_members=vset.get("combination_members", []),
                ))
            # Cross-type twin ties: same deterministic content rule as
            # canonical()/_canonical_concept_search (FAISS tie order is
            # index-layout-dependent; see _break_exact_ties).
            results = self._break_exact_ties(results)[:count]
            all_results.append(results)
        return all_results

    def canonical(
        self,
        query: str,
        *,
        sub_mode: str = "semantic",
        result_types: str | list[str] | None = None,
        sources: list[str] | None = None,
        count: int = 20,
    ) -> list[CanonicalSearchResult]:
        """Search and map top candidate code results to Master Canonical Value Sets.

        Uses the canonical concept FAISS index (Option C-B: direct embedding
        search on canonical anchor names) when available. Falls back to the
        existing semantic/hybrid code-based search + canonical mapping ONLY
        if the concept index file is missing (not installed). When the concept
        index IS available, results below the confidence threshold (0.70)
        return empty — no fallback to the un-thresholded old pipeline.

        Parameters
        ----------
        result_types : str | list[str] | None
            Result-type filter. Filters results by canonical_id prefix, which
            encodes the anchor's clinical type. Useful for SNOMEDCT_US queries
            where the source system contains multiple types (conditions,
            procedures, symptoms, body structures).
            Valid: "condition", "symptom", "lab", "vital", "medication",
            "drug_class", "procedure", "vaccine". Accepts a single string or
            a list (OR semantics within the parameter).
        sources : list[str] | None
            Code-system filter (RXNORM, ATC, LOINC, SNOMEDCT_US, CPT, CVX).
            AND-composed with result_types if both are set.
        """
        _validate_query(query)
        _validate_count(count)
        self._ensure_canonical()
        self._ensure_concepts()

        # Composite-group dictionary pass BEFORE embedding search: an exact
        # group-term match fans out to the group (parent first). Falls
        # through to the concept-index path on any miss.
        group_results = self._group_dictionary_search(
            query, result_types=result_types, sources=sources, count=count)
        if group_results:
            return group_results

        # Exact-alias pass (2026-11-11 addendum B): abbreviation queries
        # ('HLD', 'DM2', 'ASA', 'NSTEMI', 'FOLFOX') name their anchor via
        # an anchor/consumer synonym — return it exactly instead of letting
        # embedding search rank a semantic neighbour above it.
        alias_result = self._alias_dictionary_search(
            query, result_types=result_types, sources=sources)
        if alias_result is not None:
            # EV06-QA-02 fix (2026-11-11 evolution): the alias hit LEADS the
            # result list, with the embedding candidates merged behind it
            # (deduped, count-capped) — matching the canonical_batch shape
            # (EV02-QA-04). The prior early-return served a 1-element list
            # on $search?query=HLD&count=10 while the batch path served 10.
            merged = [alias_result]
            if self._concepts_faiss is not None:
                for r in self._canonical_concept_search(
                    query, sources=sources, result_types=result_types, count=count,
                ):
                    if r.canonical_id == alias_result.canonical_id:
                        continue
                    merged.append(r)
                    if len(merged) >= count:
                        break
            return merged

        # Option C-B: concept index is the primary path when available.
        # If it returns empty (all results below 0.70 threshold), that's
        # intentional — don't fall back to the un-thresholded old pipeline.
        if self._concepts_faiss is not None:
            concept_results = self._canonical_concept_search(
                query, sources=sources, result_types=result_types, count=count
            )
            if concept_results:
                return concept_results
            # No direct concept match above threshold — try ancestor fallback.
            # Find the SNOMED code for the query via semantic search, walk UP
            # the hierarchy, and return the nearest canonical anchor as a
            # "broader" match. This gracefully handles queries for conditions
            # that aren't canonical anchors but are descendants of one.
            # Skip the fallback when result_types is set — the caller explicitly
            # asked for specific clinical types, and the broader fallback
            # often lands in a different type (e.g., Syncope with
            # result_types='vital' would fall back to a condition anchor).
            if result_types is None:
                broader = self._ancestor_fallback(query, sources=sources, count=count)
                if broader:
                    return broader
            return []

        # Fallback: old pipeline — ONLY used when concept index file is missing
        # (e.g. fresh install before build_canonical_concept_index.py has run).
        # Retrieve candidate code results using sub_mode ('semantic', 'hybrid', or 'lexical')
        raw_results = self.search(query, mode=sub_mode, sources=sources, count=max(count * 5, 50))

        # If sub_mode returned fewer candidates, fall back to semantic search for rich semantic recall
        if len(raw_results) < count and sub_mode != "semantic":
            sem_results = self.semantic(query, sources=sources, count=max(count * 5, 50))
            seen_code_pairs = {(r.source, r.code) for r in raw_results}
            for sr in sem_results:
                if (sr.source, sr.code) not in seen_code_pairs:
                    raw_results.append(sr)

        canonical_results: list[CanonicalSearchResult] = []
        seen_ids: set[str] = set()

        for r in raw_results:
            cid = self._code_to_canonical_id.get((r.source, r.code))
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                vset = self._canonical_by_id[cid]
                canonical_results.append(CanonicalSearchResult(
                    canonical_id=vset["canonical_id"],
                    domain=vset["domain"],
                    anchor_system=vset["anchor_system"],
                    anchor_code=vset["anchor_code"],
                    patient_friendly_name=vset["patient_friendly_name"],
                    score=r.score,
                    match_grade=r.match_grade,
                    matched_via_code=f"{r.source}:{r.code}",
                    matched_via_display=r.display,
                    total_member_count=self._total_member_count(vset),
                    members=vset.get("members", []),
                combination_members=vset.get("combination_members", []),
                ))
                if len(canonical_results) >= count:
                    break

        return canonical_results

    def lexical(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
        result_types: str | list[str] | None = None,
    ) -> list[SearchResult]:
        """BM25 lexical search (~1ms). Token matching with stemming.

        ``result_types`` restricts which category indexes are searched
        (see ``_restrict_categories``) so ``count`` caps the FILTERED set —
        the service-side replacement for the CLI's former post-truncation
        client filter.
        """
        _validate_query(query)
        _validate_count(count)
        self._ensure_bm25()
        categories = self._restrict_categories(
            self._resolve_categories(sources), result_types
        )
        if not categories:
            return []
        # QC-329: fold accents + split on non-alphanumerics so accented
        # queries match the accent-stripped index (see _query_tokens).
        query_tokens = _query_tokens(query)
        if not query_tokens:
            return []
        # Original-case form of each query token (first occurrence wins),
        # for acronym corroboration in the surface pass (AMUX-20): a token
        # typed 'MI' only corroborates names carrying uppercase 'MI'.
        _typed_by_lower: dict[str, str] = {}
        for typed in re.findall(r"[A-Za-z0-9]{2,}", _strip_accents(query)):
            _typed_by_lower.setdefault(typed.lower(), typed)
        # QC-124: over-fetch per category when a source filter is set so the
        # filter doesn't drop the top-N slice down to 0 cross-source hits
        # (e.g., 'insulin' sources=['RXNORM'] count=1: the first RXNORM hit
        # ranks #6 in the shared medication index behind 5 SNOMED entries).
        # Mirrors the semantic (count*3) over-fetch with a floor of 10 — the
        # measured first-cross-source rank for tight single-token queries.
        fetch_count = max(count * 3, 10) if sources else count

        results: list[SearchResult] = []
        for category in categories:
            index = self._bm25_indexes.get(category)
            if index is None or not isinstance(index, dict) or "postings" not in index:
                continue

            postings = index["postings"]
            idf = index.get("idf", {})
            # Bound once per category (CR-002 hoist): the 2-char casing
            # guard and the orphan pass both read the raw name documents.
            names_raw = index.get("names") or []
            doc_lengths = index.get("doc_lengths", [])
            avg_doc_length = index.get("avg_doc_length", 20.0) or 20.0
            rid_to_code = index.get("rid_to_code", [])
            rid_to_friendly = index.get("rid_to_friendly_name", [])
            rid_to_system = index.get("rid_to_system", [])

            scores: dict[int, float] = {}
            # Per-query-token idf actually used (for the shared score
            # denominator). Orphan tokens (no posting candidates — 2-char
            # acronyms, digit-bearing runs with no >= 3-letter run) are
            # scored by the surface pass below with the index's max idf as
            # a rarity proxy, and contribute that same proxy to the
            # denominator so surface-only rows grade honestly (AMUX-20).
            matched_idfs: list[float] = []
            orphan_tokens: list[str] = []
            for raw_token in query_tokens:
                keys = _posting_candidates(raw_token, postings)
                # v3 df-prune (AMUX-63 validation): v3 indexes post
                # near-stopword alphanumeric/2-char terms ('mg' hits 40%
                # of medication rows) whose per-query enumeration costs
                # more than their evidence is worth. Keys that could
                # never exist in a v2 index are pruned at a 15%
                # document-frequency ceiling — pure >= 3-letter keys are
                # NEVER pruned, so scoring on v2 indexes and for
                # ordinary words is bit-identical. A token whose keys
                # are all pruned falls through to the orphan surface
                # pass, exactly where v2 left it.
                n_records = index.get("num_records") or 0
                if n_records:
                    keys = [
                        k for k in keys
                        if k.isalpha() and len(k) >= 3
                        or len(postings.get(k, ())) <= 0.15 * n_records
                    ]
                if not keys:
                    orphan_tokens.append(raw_token)
                    continue
                # 2-char alpha casing guard (Joel ruling, AMUX-22): a
                # 2-char posting key exists only in v3 and is an acronym
                # by construction — raw tf/idf on it mis-ranks ('MI' ->
                # 'Mi-2 Ab' rows, short lab docs, BM25-length-boosted,
                # outrank 'Acute Myocardial Infarction (MI)'). Such a key
                # only scores rows whose indexed surface carries the
                # token AS TYPED or uppercased ('(MI)', 'HbA1c'); a key
                # that corroborates nothing is dropped and the token
                # falls through to the orphan surface pass — v2 behavior
                # for 2-char tokens. Only affects keys v2 could never
                # have; a lowercase-typed token corroborates only rows
                # carrying it verbatim-lowercase ('(mi)') or uppercased
                # ('(MI)') — cased acronyms need cased queries.
                # CR-001 (review follow-up): the cased 2-char words are
                # precomputed once per index (same lazy pattern as
                # _orphan_surfaces_lower) — the per-query corroboration
                # is then pure set work, no regex over the posting.
                two_char = index.get("_two_char_cased")
                if two_char is None:
                    two_char = {}
                    for tc_key, tc_posting in postings.items():
                        if len(tc_key) != 2 or not tc_key.isalpha():
                            continue
                        cased_words: list[tuple[int, str]] = []
                        for e in tc_posting:
                            if not (isinstance(e, list) and len(e) >= 2):
                                continue
                            e_rid = int(e[0])
                            if e_rid >= len(names_raw):
                                continue
                            for w in re.findall(
                                r"\b[a-zA-Z]{2}\b",
                                _strip_accents(names_raw[e_rid] or ""),
                            ):
                                if w.lower() == tc_key:
                                    cased_words.append((e_rid, w))
                        two_char[tc_key] = cased_words
                    index["_two_char_cased"] = two_char
                guarded_rids: dict[str, set[int]] = {}
                for k in list(keys):
                    if len(k) == 2 and k.isalpha():
                        typed = _typed_by_lower.get(k, k)
                        typed_upper = typed.upper()
                        rids: set[int] = {
                            rid
                            for rid, w in two_char.get(k, ())
                            if w == typed or w == typed_upper
                        }
                        if rids:
                            guarded_rids[k] = rids
                        else:
                            keys.remove(k)
                if not keys:
                    orphan_tokens.append(raw_token)
                    continue
                # idf of the strongest matching key (pre-fix behavior for
                # ordinary >= 3-letter queries: idf.get(stem, idf.get(raw))).
                matched_idfs.append(max(idf.get(k, 1.0) for k in keys))
                for token in keys:
                    token_idf = idf.get(token, 1.0)
                    for entry in postings[token]:
                        if not (isinstance(entry, list) and len(entry) >= 2):
                            continue
                        rid, tf = int(entry[0]), float(entry[1])
                        if token in guarded_rids and rid not in guarded_rids[token]:
                            continue
                        doc_len = doc_lengths[rid] if rid < len(doc_lengths) else avg_doc_length
                        k1, b = 1.5, 0.75
                        tf_comp = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_doc_length))
                        scores[rid] = scores.get(rid, 0.0) + token_idf * tf_comp

            if orphan_tokens:
                # AMUX-20 surface pass: tokens the index build digitized or
                # length-truncated out of the postings (MI, PE, PT, A1c)
                # are recovered from the row's indexed name text and
                # friendly display, which carry them verbatim ('Acute
                # Myocardial Infarction (MI)'). A row matches when its
                # tokenized surfaces contain EVERY query token —
                # full-query coverage keeps the pass precise. Scoring
                # mirrors a tf=1 BM25 hit with max idf as the rarity
                # proxy: deterministic, model-free, and lands in the same
                # normalization pipeline as posting hits.
                orphan_idf = max(idf.values(), default=1.0)
                matched_idfs.extend([orphan_idf] * len(orphan_tokens))
                posting_rids = set(scores)
                orphan_touched: set[int] = set()
                k1, b = 1.5, 0.75
                q_token_set = set(query_tokens)
                # Longest token first: the rarest discriminator fails
                # fastest, and later checks are skipped entirely.
                q_tokens = sorted(q_token_set, key=len, reverse=True)
                q_tokens_rest = q_tokens[1:]
                typed_orphans = [_typed_by_lower.get(t, t) for t in orphan_tokens]
                # AMUX-63: the lowercase combined surface is identical on
                # every query, so it is precomputed once per index and
                # cached in the (immutable, in-memory) index dict. The
                # per-query scan below is then pure C-level substring
                # checks — rebuilding and lowercasing the f-string per row
                # per query made any orphan-token query O(index) Python
                # work (~180ms+) and dominated hybrid $search latency.
                surfaces = index.get("_orphan_surfaces_lower")
                if surfaces is None:
                    surfaces = [
                        (
                            f"{names_raw[i] if i < len(names_raw) else ''} "
                            f"{rid_to_friendly[i] if i < len(rid_to_friendly) else ''}".lower()
                        )
                        for i in range(max(len(names_raw), len(rid_to_friendly)))
                    ]
                    index["_orphan_surfaces_lower"] = surfaces
                for rid, low in enumerate(surfaces):
                    if not low:
                        continue
                    # Cheap substring prefilter before tokenizing — a row
                    # whose combined surfaces don't even contain each query
                    # token as a substring cannot cover the token set.
                    # (Plain lower(); the exact coverage check below
                    # applies the full accent-folding tokenizer. Written
                    # as an explicit loop, not a genexpr: this runs over
                    # every row of every category index, and a per-row
                    # generator frame dominated the whole lexical path.)
                    if q_tokens[0] not in low:
                        continue
                    if any(t not in low for t in q_tokens_rest):
                        continue
                    # Token coverage over the indexed name document AND
                    # the friendly display — the two surfaces a row is
                    # known by. (Space separator: no token can span it.)
                    # Surviving rows are rare: rebuild the cased surfaces
                    # on demand instead of carrying them through the scan.
                    name_raw = names_raw[rid] if rid < len(names_raw) else ""
                    friendly = rid_to_friendly[rid] if rid < len(rid_to_friendly) else ""
                    if not q_token_set <= set(_query_tokens(low)):
                        continue
                    doc_len = doc_lengths[rid] if rid < len(doc_lengths) else avg_doc_length
                    tf_comp = (k1 + 1) / (1 + k1 * (1 - b + b * doc_len / avg_doc_length))
                    # Acronym corroboration: an orphan token typed with
                    # casing that appears in the surface as typed or fully
                    # uppercased ('(MI)', 'Prothrombin Time (PT) Test',
                    # 'HbA1c') is the sense the user typed; plain-word hits
                    # ('Mi-2 Ab', 'New Pt 10mins') keep the base score and
                    # rank below.
                    cased_hits = max(
                        _cased_token_hits(typed_orphans, name_raw),
                        _cased_token_hits(typed_orphans, friendly),
                    )
                    boost = 1.0 + 0.5 * cased_hits
                    scores[rid] = scores.get(rid, 0.0) + (
                        orphan_idf * tf_comp * len(orphan_tokens) * boost
                    )
                    orphan_touched.add(rid)
            else:
                orphan_touched = set()
            # Rows scored ONLY by the orphan pass (no posting matched the
            # query): their evidence is substring-level surface coverage,
            # not a term match — graded below (QC1-T3: 'MS' hits
            # '(0.58-0.68 MS)' microsphere notation and 'Morphine'
            # substrings; without the cap these graded 'certain').
            if orphan_tokens:
                orphan_only_rids = orphan_touched - posting_rids
            else:
                orphan_only_rids = set()

            # Hoist the IDF denominator out of the per-result loop — it depends
            # only on the idfs used above, constant across the top-N results.
            idf_denom = sum(matched_idfs) * 2.5 + 0.001
            q_key = _surface_key(query)
            name_list = index.get("names") or []

            # Default-arg binding: these closures must not capture the
            # category-loop variables (B023). CR-003: display is a pure
            # function of rid (derived from rid_to_friendly), so the
            # exact-surface flag is memoized per rid for this query —
            # the cheap key, the full rank key and the emit loop all
            # ask for the same rows.
            _exact_cache: dict[int, bool] = {}

            def _row_exact(
                rid: int,
                display: str,
                q_key: str = q_key,
                name_list: list = name_list,
                _cache: dict = _exact_cache,
            ) -> bool:
                """AMUX-18: exact surface = the query normalized equals
                EITHER the row's indexed name document ('Tylenol', the
                synonym the postings matched) or its friendly display."""
                hit = _cache.get(rid)
                if hit is not None:
                    return hit
                result = (
                    _surface_key(display) == q_key
                    or (
                        rid < len(name_list)
                        and bool(name_list[rid])
                        and _surface_key(name_list[rid]) == q_key
                    )
                )
                _cache[rid] = result
                return result

            def _row_rank(
                item: tuple[int, float],
                _exact=_row_exact,
                _codes: list = rid_to_code,
                _friendly: list = rid_to_friendly,
                _q_key: str = q_key,
            ) -> tuple[bool, float, int, str]:
                """AMUX-18 (+ unified tie-break): exact surface match
                first, then BM25 score, then surface edit distance, then
                code — a row literally named 'Tylenol' wins the tf=1 tie
                against longer combination-product rows, and among
                equal-score rows the closest surface text wins,
                deterministically."""
                rid, score = item
                code = _codes[rid] if rid < len(_codes) else str(rid)
                display = _friendly[rid] if rid < len(_friendly) else code
                return (
                    not _exact(rid, display),
                    -score,
                    _surface_distance(_q_key, display),
                    str(code),
                )

            def _cheap_key(
                item: tuple[int, float],
                _exact=_row_exact,
                _friendly: list = rid_to_friendly,
            ) -> tuple[bool, float]:
                # B023: _friendly bound as a default arg, not a closure
                # over the category-loop variable.
                rid_ = item[0]
                return (
                    not _exact(
                        rid_,
                        _friendly[rid_] if rid_ < len(_friendly) else str(rid_),
                    ),
                    -item[1],
                )

            for rid, score in _sorted_lazy_surface_tiebreak(
                scores.items(),
                _cheap_key,
                _row_rank,
                fetch_count,
            )[:fetch_count]:
                code = rid_to_code[rid] if rid < len(rid_to_code) else str(rid)
                display = rid_to_friendly[rid] if rid < len(rid_to_friendly) else code
                sys_name = (rid_to_system[rid] if rid < len(rid_to_system) else "").upper()
                source = _SYSTEM_LABELS_REVERSE.get(sys_name, sys_name)
                normalized = min(score / idf_denom, 1.0)
                row_exact = _row_exact(rid, display)
                # Orphan-pass-only rows (no posting matched the query)
                # cap at 'probable': substring surface coverage is weaker
                # evidence than a posting match, and the max-idf proxy
                # inflates the normalized score (QC1-T3). Exact-surface
                # rows are exempt — the surface IS the query.
                grade = _score_to_grade(normalized)
                if (
                    rid in orphan_only_rids
                    and grade == "certain"
                    and not row_exact
                ):
                    grade = "probable"
                results.append(SearchResult(
                    code=str(code), source=source, display=display,
                    score=round(normalized, 4), match_grade=grade,
                    category=category, _exact_surface=row_exact,
                ))

        # AMUX-18 (+ unified tie-break): exact surface matches first across
        # categories too, then score, then surface edit distance, then code
        # — fully deterministic ordering, identical to the semantic path.
        _q_key_all = _surface_key(query)
        results.sort(
            key=lambda r: (
                not r._exact_surface,
                -r.score,
                _surface_distance(_q_key_all, r.display),
                r.code,
            )
        )
        return self._filter_by_source(results, sources)[:count]

    def semantic(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
        result_types: str | list[str] | None = None,
    ) -> list[SearchResult]:
        """SapBERT embedding + FAISS ANN search (~100ms on CPU).

        ``result_types`` restricts which category indexes are searched
        (see ``_restrict_categories``); an empty intersection returns []
        WITHOUT calling the engine — ``SemanticSearchEngine.search`` treats
        an empty categories list as "search all".
        """
        _validate_query(query)
        _validate_count(count)
        engine = self._ensure_semantic()
        categories = self._restrict_categories(
            self._resolve_categories(sources), result_types
        )
        if not categories:
            return []
        # Over-fetch when filtering by source so we still have `count` results
        # after the source filter drops cross-source hits (e.g., ICD-10 codes
        # that share a name with a SNOMED concept).
        k = count * 3 if sources else count
        raw = engine.search(query, categories=categories, top_k=k)

        results: list[SearchResult] = []
        for r in raw:
            sys_name = r.get("system", "").upper()
            source = _SYSTEM_LABELS_REVERSE.get(sys_name, sys_name)
            display = r["display"]
            q_key = _surface_key(query)
            results.append(SearchResult(
                code=r["code"], source=source, display=display,
                score=r["score"], match_grade=r["match_grade"],
                category=r.get("category", ""),
                _exact_surface=_surface_key(display) == q_key,
            ))
        # Unified tie-break (AMUX-18 follow-up): exact surface first, then
        # cosine score, then surface edit distance, then code — the SAME
        # ordering contract as the lexical path, so equal-cosine rows
        # resolve by closest surface text instead of FAISS/category
        # iteration order.
        _q_key = _surface_key(query)
        results.sort(
            key=lambda r: (
                not r._exact_surface,
                -r.score,
                _surface_distance(_q_key, r.display),
                r.code,
            )
        )
        return self._filter_by_source(results, sources)[:count]

    def hybrid(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        count: int = 20,
        result_types: str | list[str] | None = None,
    ) -> list[SearchResult]:
        """RRF fusion over the UNION of per-system candidates (AMUX-46).

        Replaces the cascade (BM25 pool -> SapBERT re-rank), where lexical
        recall was a hard gate: BM25's tf-tie lottery could drop the
        semantic best row ('lisinopril 5mg' -> 5mg tablet, 0.93 cosine)
        from the pool entirely, making it invisible to hybrid. Either
        system can now carry a row.

        Ranking: reciprocal-rank fusion, sum of 1/(HYBRID_FUSION_K + rank)
        per system over the union of the per-system top-K (K =
        HYBRID_CANDIDATE_DEPTH). k=10 won the offline benchmark
        (benchmarks/fusion_benchmark.py): hit@1 53.1% / hit@5 71.9% /
        FP@1 3.1% vs cascade 46.9% / 62.5% / 9.4% on the golden families;
        weighted-sum variants lost on precision (FP 53-59%).
        (AMUX-63: depth was lowered 100 -> 25 — the depth sweep showed
        quality flat 10..100 and latency driven by the lexical orphan
        pass, not fusion depth.)

        Confidence (match_grade): the fused RRF score is a RANK signal,
        not a confidence signal (measured: true/wrong top-1 score medians
        overlap). Grades therefore require system concordance plus the
        underlying calibrated cosine:
          certain  — row in BOTH systems' candidates AND cosine >= 0.92
          probable — cosine >= 0.75, or a lexical exact-surface match
                     (AMUX-18) at lexical rank <= 3
          possible — otherwise
        Lexical-only rows can never be 'certain' (single-evidence
        ceiling): lexical's old precision veto, lost when the pool gate
        went away, returns as a grade gate. 'score' stays the calibrated
        underlying value (cosine when the semantic system saw the row,
        else normalized BM25) so wire consumers see [0,1] confidences.

        ``result_types`` restricts the categories searched (both systems).
        """
        _validate_query(query)
        _validate_count(count)
        categories = self._restrict_categories(
            self._resolve_categories(sources), result_types
        )
        if not categories:
            return []
        self._ensure_bm25()
        engine = self._ensure_semantic()

        depth = max(HYBRID_CANDIDATE_DEPTH, count)
        bm25_results = self.lexical(
            query,
            sources=sources,
            count=depth,
            result_types=result_types,
        )
        sem_raw = engine.search(query, categories=categories, top_k=depth)

        # Per-system evidence keyed by (source, code). setdefault keeps
        # the FIRST (best-ranked) occurrence of a duplicate identity — a
        # code indexed under two categories must not lose its better
        # rank to the later, worse one (QC1-S1).
        lex_ev: dict[tuple, dict] = {}
        for rank, r in enumerate(bm25_results, start=1):
            lex_ev.setdefault((r.source, r.code), {
                "rank": rank, "score": r.score, "display": r.display,
                "category": r.category, "exact": r._exact_surface,
            })
        sem_ev: dict[tuple, dict] = {}
        for rank, r in enumerate(sem_raw, start=1):
            sys_name = r.get("system", "").upper()
            source = _SYSTEM_LABELS_REVERSE.get(sys_name, sys_name)
            sem_ev.setdefault((source, r["code"]), {
                "rank": rank, "cos": float(r["score"]),
                "display": r["display"], "category": r.get("category", ""),
            })

        union = set(lex_ev) | set(sem_ev)
        q_key = _surface_key(query)
        fused: list[tuple[float, SearchResult]] = []
        for ident in union:
            lx = lex_ev.get(ident)
            se = sem_ev.get(ident)
            rrf = (
                (1.0 / (HYBRID_FUSION_K + lx["rank"]) if lx else 0.0)
                + (1.0 / (HYBRID_FUSION_K + se["rank"]) if se else 0.0)
            )
            display = (lx or se)["display"]
            cos = se["cos"] if se is not None else 0.0
            # AMUX-18 exactness as lexical defined it (display OR indexed
            # name document equals the query) — reusing the carried flag,
            # not re-deriving a weaker display-only check (QC1-A4).
            exact = bool(lx and lx["exact"])
            # Agreement-aware grade guard (see docstring).
            if lx is not None and se is not None and cos >= GRADE_CERTAIN_COSINE:
                grade = "certain"
            elif cos >= GRADE_PROBABLE_COSINE or (
                lx is not None and lx["rank"] <= 3 and exact
            ):
                grade = "probable"
            else:
                grade = "possible"
            score = cos if se is not None else (lx["score"] if lx else 0.0)
            fused.append(
                (rrf, SearchResult(
                    code=ident[1], source=ident[0], display=display,
                    score=round(score, 4), match_grade=grade,
                    category=(lx or se)["category"],
                    _exact_surface=exact,
                ))
            )

        # Deterministic order: RRF desc, then the unified AMUX-49 key
        # (exact surface, surface distance, code, source). The trailing
        # source term makes the key a total order — two rows colliding
        # on code across systems must not fall back to hash-seed-dependent
        # set iteration order (QC1-S2).
        fused.sort(
            key=lambda t: (
                -t[0],
                not t[1]._exact_surface,
                _surface_distance(q_key, t[1].display),
                t[1].code,
                t[1].source,
            )
        )
        results = [r for _, r in fused]
        return self._filter_by_source(results, sources)[:count]

    def search(
        self,
        query: str,
        *,
        mode: str = "lexical",
        sources: list[str] | None = None,
        count: int = 20,
        engine=None,
        result_types: str | list[str] | None = None,
    ) -> list[SearchResult] | list[CanonicalSearchResult]:
        """Unified entry point. mode: 'lexical', 'semantic', 'hybrid', or 'canonical'.

        ``engine`` (optional) canonicalizes legacy-mode result displays to the
        engine preferred term (QC-400). Pass it wherever a terminology engine
        is already open — Python facade, MCP, FHIR $search — so all surfaces
        emit ONE display convention for the same result row.

        ``result_types`` filters SERVICE-SIDE in every mode: canonical mode
        filters by canonical_id prefix (``canonical(result_types=...)``);
        legacy modes restrict the category indexes searched
        (``_restrict_categories``). In both cases ``count`` caps the
        FILTERED result set — callers must not truncate before filtering.
        Legacy modes accept only SEARCH_CATEGORIES values; values outside
        that set (e.g. canonical-only "symptom") match nothing.
        """
        if mode == "canonical":
            # Canonical results carry the anchor's patient_friendly_name by
            # design — no preferred-term canonicalization applies.
            return self.canonical(
                query, result_types=result_types, sources=sources, count=count
            )
        if mode == "lexical":
            results = self.lexical(
                query, sources=sources, count=count, result_types=result_types
            )
        elif mode == "semantic":
            results = self.semantic(
                query, sources=sources, count=count, result_types=result_types
            )
        elif mode == "hybrid":
            results = self.hybrid(
                query, sources=sources, count=count, result_types=result_types
            )
        else:
            raise ValueError(
                f"Unknown search mode: {mode}. Use 'lexical', 'semantic', 'hybrid', or 'canonical'."
            )
        if engine is not None and results:
            results = apply_preferred_display(results, engine)
        return results


# Reverse lookup: lowercase system label → internal source name
_SYSTEM_LABELS_REVERSE: dict[str, str] = {
    "snomedct_us": "SNOMEDCT_US",
    "rxnorm": "RXNORM",
    "icd10cm": "ICD10CM",
    "icd10": "ICD10CM",
    "icd10pcs": "ICD10PCS",
    "lnc": "LNC",
    "loinc": "LNC",
    "cpt": "CPT",
    "hcpcs": "HCPCS",
    "cvx": "CVX",
}


# Singleton
_service: SearchService | None = None


def get_search_service() -> SearchService:
    global _service
    if _service is None:
        _service = SearchService(
            search_index_dir=env_pin("MEDTERM4DS_SEARCH_INDEX_DIR"),
            embedding_model_dir=env_pin("MEDTERM4DS_EMBEDDING_MODEL_DIR"),
            canonical_path=os.getenv("MEDTERM4DS_CANONICAL_PATH", DEFAULT_CANONICAL_VALUE_SETS_PATH),
            canonical_concepts_index=os.getenv("MEDTERM4DS_CANONICAL_INDEX", DEFAULT_CANONICAL_CONCEPTS_INDEX),
            canonical_concepts_meta=os.getenv("MEDTERM4DS_CANONICAL_META", DEFAULT_CANONICAL_CONCEPTS_META),
        )
    return _service


def configure_search_service(
    *,
    search_index_dir: str | None = None,
    embedding_model_dir: str | None = None,
) -> SearchService:
    """Replace the singleton with a new instance configured with the given dirs.

    Used by apps that need to override the env-var defaults at startup (e.g.
    the FHIR server, which reads MEDTERM4DS_SEARCH_INDEX_DIR from its own
    settings dataclass). Also useful for tests that need an isolated service.
    """
    global _service
    _service = SearchService(
        search_index_dir=search_index_dir or env_pin("MEDTERM4DS_SEARCH_INDEX_DIR"),
        embedding_model_dir=embedding_model_dir or env_pin("MEDTERM4DS_EMBEDDING_MODEL_DIR"),
    )
    return _service


def reset_search_service() -> None:
    """Drop the singleton. Next get_search_service() call will create a fresh
    instance with current env-var defaults. Used between tests."""
    global _service
    _service = None


def search(
    query: str,
    *,
    mode: str = "lexical",
    sources: list[str] | None = None,
    count: int = 20,
    engine=None,
    result_types: str | list[str] | None = None,
) -> list[SearchResult]:
    """Search for medical codes by free text.

    Parameters
    ----------
    query : Free text to search for (e.g., "high blood sugar").
    mode : 'lexical' (BM25, ~1ms), 'semantic' (SapBERT, ~100ms), or 'hybrid' (RRF union fusion of both systems, AMUX-46).
    sources : Restrict to specific source systems (e.g., ["SNOMEDCT_US"]). None = all.
    count : Maximum results to return.
    engine : Optional terminology engine. When provided, legacy-mode result
        displays are canonicalized to the engine preferred term (QC-400) so
        all surfaces emit one display convention for the same result.
    result_types : Filter by clinical result type, applied SERVICE-SIDE so
        ``count`` caps the filtered set (canonical mode filters by
        canonical_id prefix; legacy modes restrict the category indexes
        searched). Legacy modes accept only SEARCH_CATEGORIES values.

    Returns
    -------
    list[SearchResult]
        Ranked results with score and match_grade (certain/probable/possible).
    """
    if sources is not None:
        # QC-425 (MEDIUM): an all-empty source filter (e.g. sources=[''])
        # previously returned silent 0-row success on the BM25 path while
        # the LIKE sibling (search_names) WIDENED and source_stats rejected —
        # one input, three behaviors. Reject, matching get_source_stats'
        # QC-395 guard; ``sources=None`` still means all.
        if isinstance(sources, (str, bytes)) or not isinstance(sources, (list, tuple)):
            raise TypeError(
                "sources must be a list of vocabulary names, got "
                f"{type(sources).__name__}"
            )
        if not [s for s in sources if isinstance(s, str) and s.strip()]:
            raise ValueError(
                "sources must contain at least one non-empty vocabulary name "
                "(e.g. SNOMEDCT_US) when provided."
            )
        # QC-424 note: unknown-source presence for BM25/semantic modes is NOT
        # validated here — those modes read the search indexes, not the
        # terminology tables, so a DB probe is the wrong instrument. The LIKE
    # path (search_names) presence-checks in the engine.
    return get_search_service().search(
        query, mode=mode, sources=sources, count=count, engine=engine,
        result_types=result_types,
    )
