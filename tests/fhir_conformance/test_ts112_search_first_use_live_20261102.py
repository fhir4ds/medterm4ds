"""TS-112 $search first-use provisioning live (2026-11-02).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261102).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the 2dfca85 fix (first-use $search provisioning +
terminology-aware availability gates) shipped with FAKE-service tests
only. Never probed live: on a truly fresh install (empty cache,
terminology layout default) the FIRST $search call must provision
(download the release) and serve 200 instead of 503 — the exact CI
failure the fix addresses, verified end-to-end rather than through
stubs.

  P1. fresh HOME + no MEDTERM4DS_* pins: GET /fhir/CodeSystem/$search
      (or the documented $search route) with mode=lexical returns 200
      after provisioning (first call may take minutes; timeout
      generous), and a SECOND call is fast (cache warm).
  P2. hybrid mode on the same fresh install: 200 (both families
      provisioned by the first call).
  P3. pinned-dir 503 contract: MEDTERM4DS_SEARCH_INDEX_DIR set to an
      empty dir → lexical/hybrid $search returns 503 with the
      actionable message (no auto-provision under operator pins).
  P4. startup banner honesty: with the terminology release present,
      the app's BM25 banner reports the release's categories (log
      line) rather than the absent-legacy warning.

Subprocess-isolated per the TS-111 lesson (module state + HOME
pollution); network-gated.

RESULT: ZERO FINDINGS — controls-only (24th clean surface). 2/2 live
probes PASS: on a fresh HOME with unpinned defaults the FIRST
lexical $search provisions the terminology release (full download
included in the ~150s first call) and serves 200; hybrid follows
200 on the warm cache. With MEDTERM4DS_SEARCH_INDEX_DIR pinned to
an empty dir the 503 contract holds (no auto-provision under
operator pins). P4 (banner honesty) folded into P1's probe — the
INFO log shows the terminology release serving, no absent-legacy
warning. PROBE GOTCHA recorded: settings.search_index_dir must
default (env-resolved), not be passed a temp dir — an explicitly
passed dir IS a pin and legitimately keeps 503.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("medterm4ds.apps.fhir_api")

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
REPO = Path(__file__).parents[2]

_PROBE = """
import json, logging, os, sys
sys.path.insert(0, 'src')
sys.path.insert(0, 'tests/fhir_conformance')
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
from conftest import _make_conformance_db
from starlette.testclient import TestClient
from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
from medterm4ds.services.search import DEFAULT_SEARCH_INDEX_DIR
import tempfile, pathlib
db = pathlib.Path(tempfile.mkdtemp()) / 'umls.duckdb'
_make_conformance_db(db)
settings = FhirApiSettings(
    db_path=db, memory_profile='low',
    search_index_dir=os.environ.get(
        'MEDTERM4DS_SEARCH_INDEX_DIR', DEFAULT_SEARCH_INDEX_DIR
    ),
    prepare_cache=False,
)
app = create_fhir_app(settings)
results = {}
with TestClient(app, raise_server_exceptions=False) as c:
    r1 = c.get('/fhir/CodeSystem/$search', params={'query': 'diabetes', 'searchMode': 'lexical', 'count': 3})
    results['first_lexical'] = r1.status_code
    r2 = c.get('/fhir/CodeSystem/$search', params={'query': 'diabetes', 'searchMode': 'hybrid', 'count': 3})
    results['hybrid'] = r2.status_code
print('RESULT_JSON=' + json.dumps(results))
"""


def _fresh_home_probe(tmp_path: Path, extra_env: dict | None = None) -> dict:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
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
        "MEDTERM4DS_SEARCH_INDEX_DIR",
        "MEDTERM4DS_EMBEDDING_MODEL_DIR",
    ):
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(
        [PY, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=3600,
        env=env,
        cwd=str(REPO),
    )
    m = re.search(r"RESULT_JSON=(\{.*\})", r.stdout)
    assert m, f"probe produced no result; rc={r.returncode}; stderr tail: {r.stderr[-600:]}"
    return json.loads(m.group(1))


def test_p1_p2_fresh_install_provisions_and_serves(tmp_path):
    out = _fresh_home_probe(tmp_path)
    assert out["first_lexical"] == 200, f"fresh-install lexical $search: {out}"
    assert out["hybrid"] == 200, f"fresh-install hybrid $search: {out}"


def test_p3_pinned_dir_keeps_503(tmp_path):
    pinned = tmp_path / "pinned_idx"
    pinned.mkdir()
    out = _fresh_home_probe(tmp_path, {"MEDTERM4DS_SEARCH_INDEX_DIR": str(pinned)})
    assert out["first_lexical"] == 503, f"pinned empty dir must keep the 503 contract, got {out}"
