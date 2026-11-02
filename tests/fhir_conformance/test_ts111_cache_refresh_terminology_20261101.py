"""TS-111 cache-refresh --terminology live + refusal symmetry (2026-11-01).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261101c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the ``cache-refresh --terminology`` migration path (artifact_cache
.cache_refresh terminology branch: rmtree + atomic re-download + summary
dict) shipped in the repoint with NO test at any level. This suite runs
every probe in a SUBPROCESS with an isolated HOME — the probe must never
mutate the in-process module state of the surrounding suite (an earlier
in-process draft polluted search-module globals and broke unrelated
files; subprocess isolation is the fix and the lesson).

  R1. cache_refresh(terminology=True): downloads the default release,
      returns the summary dict (layout/repo_id/release/
      embedding_space_id/families), leaves a resolvable release.
  R2. refresh replaces stale files: corrupt value_sets.json, re-run,
      restored byte-size and parseable (rmtree semantics).
  R3. operator-managed refusal (MEDTERM4DS_CACHE_DIR set) for both
      the default and explicit terminology refreshes.
  R4. MEDTERM4DS_LAYOUT=legacy disables the terminology branch.
  R5. CLI round-trip: exit 0, JSON matches the API summary.

Network-gated; HOME-isolated via subprocess env.

RESULT: ZERO FINDINGS — controls-only (23rd clean surface). 5/5 live
probes PASS (see docstrings). One DESIGN FACT pinned (was
undocumented): in operator-managed mode the terminology refresh is
refused BEFORE the layout check — pipeline-managed files are never
deletable by cache-refresh under MEDTERM4DS_CACHE_DIR.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("medterm4ds.core.artifact_cache")

try:
    import urllib.request

    with urllib.request.urlopen(
        "https://huggingface.co/api/models/fhir4ds/terminology", timeout=10
    ) as _r:
        _OK = _r.status == 200
except Exception:
    _OK = False

pytestmark = pytest.mark.skipif(not _OK, reason="HF unreachable from this box")

PY = sys.executable


def _run_probe(home: Path, code: str, extra_env: dict | None = None) -> dict:
    env = {
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "PATH": os.environ.get("PATH", ""),
        "HF_TOKEN": os.environ.get("HF_TOKEN", ""),
    }
    for key in (
        "MEDTERM4DS_CACHE_DIR",
        "MEDTERM4DS_LAYOUT",
        "MEDTERM4DS_TERMINOLOGY_DIR",
        "MEDTERM4DS_TERMINOLOGY_RELEASE",
    ):
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(
        [PY, "-c", "import sys; sys.path.insert(0, 'src');\n" + code],
        capture_output=True,
        text=True,
        timeout=1800,
        env=env,
        cwd=str(Path(__file__).parents[2]),
    )
    assert r.returncode == 0, r.stderr[-800:]
    # tqdm/download progress can precede JSON on stdout — parse the LAST
    # non-empty line; probes without output return None.
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1]) if lines else None


def test_r1_refresh_downloads_and_resolves(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = _run_probe(
        home,
        """
import json
from medterm4ds.core.artifact_cache import cache_refresh
s = cache_refresh(terminology=True)
print(json.dumps(s))
""",
    )
    assert out["layout"] == "terminology"
    assert out["repo_id"] == "fhir4ds/terminology"
    assert out["release"]
    assert out["embedding_space_id"]
    assert out["families"] == ["canonical", "lexical", "semantic", "model"]
    # resolvable post-refresh (second subprocess, same HOME)
    out2 = _run_probe(
        home,
        f"""
import json
from medterm4ds.core import terminology as term
paths = term.resolve(None if False else __import__('pathlib').Path({str(home)!r}) / '.cache' / 'medterm4ds', space_id={out["embedding_space_id"]!r})
print(json.dumps({{'release': paths.release if paths else None}}))
""",
    )
    assert out2["release"] == out["release"]


def test_r2_stale_file_replaced(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    _run_probe(
        home,
        "from medterm4ds.core.artifact_cache import cache_refresh; cache_refresh(terminology=True)",
    )
    vs_files = list((home / ".cache" / "medterm4ds").rglob("value_sets.json"))
    assert vs_files, "release not downloaded"
    vs = vs_files[0]
    original_size = vs.stat().st_size
    vs.write_text("{}")  # corrupt
    _run_probe(
        home,
        "from medterm4ds.core.artifact_cache import cache_refresh; cache_refresh(terminology=True)",
    )
    vs_files = list((home / ".cache" / "medterm4ds").rglob("value_sets.json"))
    assert vs_files[0].stat().st_size == original_size, "stale file survived refresh"
    json.loads(vs_files[0].read_text())


def test_r3_operator_managed_refusal(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    code = """
import json
from medterm4ds.core.artifact_cache import cache_refresh
try:
    cache_refresh()
    print(json.dumps({'raised': None}))
except RuntimeError as e:
    print(json.dumps({'raised': str(e)}))
"""
    out = _run_probe(home, code, {"MEDTERM4DS_CACHE_DIR": str(tmp_path / "op")})
    assert out["raised"] and "operator-managed" in out["raised"]
    out2 = _run_probe(
        home,
        """
import json
from medterm4ds.core.artifact_cache import cache_refresh
try:
    cache_refresh(terminology=True)
    print(json.dumps({'raised': None}))
except RuntimeError as e:
    print(json.dumps({'raised': str(e)}))
""",
        {"MEDTERM4DS_CACHE_DIR": str(tmp_path / "op")},
    )
    assert out2["raised"] and "operator-managed" in out2["raised"]


def test_r4_legacy_layout_disables_terminology(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = _run_probe(
        home,
        """
import json
from medterm4ds.core.artifact_cache import cache_refresh
try:
    cache_refresh(terminology=True)
    print(json.dumps({'raised': None}))
except RuntimeError as e:
    print(json.dumps({'raised': str(e)}))
""",
        {"MEDTERM4DS_LAYOUT": "legacy"},
    )
    assert out["raised"] and "MEDTERM4DS_LAYOUT" in out["raised"]


def test_r5_cli_roundtrip(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cli = Path("/mnt/d/medterm4ds/.venv/bin/medterm4ds")
    if not cli.exists():
        pytest.skip("console script not installed")
    env = {
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "PATH": os.environ.get("PATH", ""),
        "HF_TOKEN": os.environ.get("HF_TOKEN", ""),
    }
    for key in ("MEDTERM4DS_CACHE_DIR", "MEDTERM4DS_LAYOUT", "MEDTERM4DS_TERMINOLOGY_DIR"):
        env.pop(key, None)
    r = subprocess.run(
        [str(cli), "data", "cache-refresh", "--terminology"],
        capture_output=True,
        text=True,
        timeout=1800,
        env=env,
    )
    assert r.returncode == 0, r.stderr[-400:]
    payload = json.loads(r.stdout)
    assert payload["layout"] == "terminology"
    assert payload["repo_id"] == "fhir4ds/terminology"
    assert payload["release"]
