"""TS-08 serving-pin + artifact-governance surface (2026-10-05).

Maintenance spec-comp iteration 17 (worktree maint/spec-comp-20261005c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the embedding-space serving pin landed in 35e6fc9 (rotation to
esp_f6c5c18d5f3ee3b5) — the first maintenance look at the NEW
governance/config surface. Matrix probed fresh: default pin resolves
f6c5; MEDTERM4DS_EMBEDDING_SPACE override works; layout kill-switch
(MEDTERM4DS_LAYOUT=legacy) disables split resolution; layout suite
23/23 green on the rotated registry.

K1 (LOW-MED, NEW) — a garbage serving-pin override SILENTLY degrades
    to the lexicographic fallback (sorted(ACCEPTED)[0] =
    esp_5a508816b4bb95e9 — the OLD generation). MEDTERM4DS_EMBEDDING_
    SPACE=esp_GARBAGE with both spaces present locally serves the old
    space with no warning: an operator typo'ing the rotation knob
    silently un-does the rotation instead of failing fast. 35e6fc9's
    own comments establish the lexicographic fallback as
    "last-resort"; a typo is not that. Fix shape: warn (one-time log)
    or raise when the env pin is set but not in
    ACCEPTED_EMBEDDING_SPACES — the fallback should only apply when
    NO pin is configured. Pinned: k10/k11.

K2 (LOW, NEW) — cache-info does not surface the serving pin or the
    would-serve space. The report carries split_root, mode, models{},
    resolved_data_revision — but not serving_embedding_space() nor
    which space resolution WOULD pick. Operators cannot observe the
    rotation knob's effective value (the K1 typo would be invisible
    here too). Fix shape: add serving_space + effective/pinned fields
    to the cache-info summary. Pinned: k20.

Controls verified fresh (k30-k34): default pin = f6c5 (function +
local resolution with both spaces present); env override to the
legacy space honored; legacy kill-switch forces legacy layout;
layout test suite green on the rotated registry (23/23).
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

NEW_SPACE = "esp_f6c5c18d5f3ee3b5"
OLD_SPACE = "esp_5a508816b4bb95e9"

ENV_KEYS = (
    "MEDTERM4DS_CACHE_DIR",
    "MEDTERM4DS_LAYOUT",
    "MEDTERM4DS_EMBEDDING_SPACE",
    "MEDTERM4DS_DATA_REVISION",
    "MEDTERM4DS_HF_REVISION",
)


@pytest.fixture(autouse=True)
def _restore_env():
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # NB: no artifact_manifest reload here. serving_embedding_space()
    # reads the env per call (35e6fc9), and reloading would rebind
    # ManifestError et al. — breaking later suites' module-level
    # imports via class-identity drift.
    import medterm4ds.services.search as search_mod

    importlib.reload(search_mod)


def _boot(tmp_path: Path, **env) -> object:
    """Fresh search module against a synthetic split root."""
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    os.environ["MEDTERM4DS_CACHE_DIR"] = str(tmp_path)
    for k, v in env.items():
        if v is not None:
            os.environ[k] = v
    import medterm4ds.services.search as search_mod

    return importlib.reload(search_mod)


def _make_spaces(root: Path, *spaces: str) -> None:
    for sp in spaces:
        d = root / "models" / sp
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.safetensors").write_bytes(b"x")


class TestK1GarbagePinSilentFallback:
    """K1 — a set-but-garbage pin silently serves the OLD space."""

    def test_k10_garbage_pin_picks_old(self, tmp_path):
        """MEDTERM4DS_EMBEDDING_SPACE=esp_GARBAGE with both spaces
        local → serves esp_5a50 (CURRENT, deviation). Flip when
        fixed: expect a loud failure or warning + pinned-absent
        fallback only when NO pin is set."""
        _make_spaces(tmp_path, OLD_SPACE, NEW_SPACE)
        mod = _boot(
            tmp_path, MEDTERM4DS_EMBEDDING_SPACE="esp_GARBAGE"
        )
        picked = mod._local_split_model_dir()
        assert picked is not None
        assert picked.name == OLD_SPACE

    def test_k11_unset_pin_picks_new(self, tmp_path):
        """No pin set → default serving space (f6c5) wins over the
        lexicographic old — the rotation's intended steady state."""
        _make_spaces(tmp_path, OLD_SPACE, NEW_SPACE)
        mod = _boot(tmp_path)
        picked = mod._local_split_model_dir()
        assert picked is not None
        assert picked.name == NEW_SPACE


class TestK2CacheInfoBlindToPin:
    """K2 — cache-info summary lacks the serving-space fields."""

    def test_k20_no_serving_space_in_report(self, tmp_path):
        """The split summary carries mode/models/data_revision but
        NOT the serving pin or effective space (CURRENT, deviation).
        Flip when fixed: expect a serving_space field."""
        from medterm4ds.core import artifact_cache

        _make_spaces(tmp_path, NEW_SPACE)
        _boot(tmp_path)
        report = artifact_cache.cache_info()
        flat = str(report)
        assert NEW_SPACE in flat  # models listing shows the unit
        summary = report.get("split") or report
        assert "serving_space" not in str(
            list(summary.keys())
        ), (
            "cache-info now reports serving_space — K2 fixed; flip "
            "this pin to assert the field's presence/value."
        )


class TestGovernanceControls:
    """Controls: pin + kill-switch matrix, fresh."""

    def test_k30_default_pin_value(self):
        from medterm4ds.core.artifact_manifest import (
            serving_embedding_space,
        )

        os.environ.pop("MEDTERM4DS_EMBEDDING_SPACE", None)
        assert serving_embedding_space() == NEW_SPACE

    def test_k31_env_override_to_legacy(self):
        from medterm4ds.core.artifact_manifest import (
            serving_embedding_space,
        )

        os.environ["MEDTERM4DS_EMBEDDING_SPACE"] = OLD_SPACE
        assert serving_embedding_space() == OLD_SPACE

    def test_k32_legacy_kill_switch(self, tmp_path):
        """MEDTERM4DS_LAYOUT=legacy disables split resolution
        entirely (rollback path intact post-rotation)."""
        _make_spaces(tmp_path, OLD_SPACE, NEW_SPACE)
        mod = _boot(tmp_path, MEDTERM4DS_LAYOUT="legacy")
        assert mod._LAYOUT_FORCED_LEGACY is True
        assert mod._local_split_model_dir() is None

    def test_k33_only_old_present_fallback(self, tmp_path):
        """Pinned space absent locally (pre-download) → falls back
        to any accepted space present — documented last-resort."""
        _make_spaces(tmp_path, OLD_SPACE)
        mod = _boot(tmp_path)
        picked = mod._local_split_model_dir()
        assert picked is not None and picked.name == OLD_SPACE

    def test_k34_layout_suite_green(self):
        """The committed artifact-layout suite passes on the
        rotated registry (fresh run inside this iteration)."""
        import subprocess

        r = subprocess.run(
            [
                ".venv/bin/python", "-m", "pytest",
                "tests/test_artifact_layout.py", "-q",
                "-p", "no:cacheprovider",
            ],
            capture_output=True, text=True, timeout=300,
        )
        assert r.returncode == 0, r.stdout[-400:] + r.stderr[-200:]
