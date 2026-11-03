"""TS-114 terminology-layout alias/brand recall regression (2026-11-02).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261102c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the terminology release's anchors index carries ONLY
{canonical_id, name} vectors (53,169 anchor rows, verified fresh from
the published 2026.10.31.1). The LEGACY canonical index embedded
289,165 rows of which 235,622 (81%) were ALIAS vectors (every
synonym/brand embedded; is_alias rows). The runtime's alias-surfacing
path (canonical(): 'for alias hits m["is_alias"]=True, surface the
anchor name, alias text goes to matched_via_display') is therefore
DEAD on the new layout — and, more importantly, BRAND/SYNONYM RECALL
has regressed: queries that resolved via alias vectors now return
[].

AL1 (MEDIUM-HIGH, pinned flip-row) — brand-name queries regress to
    empty on the terminology layout:
      'Tylenol'  -> legacy: VAL-MED-RXNORM-161 (alias 'Tylenol');
                    terminology live: [] (below the 0.70 floor)
      'Adbry'    -> legacy: VAL-MED-RXNORM-2589225 (alias 'Adbry');
                    terminology live: []
    Fresh dual evidence (this run): legacy ground truth confirmed
    from /mnt/d/medterm4ds/data/canonical metadata (alias rows
    present); terminology behavior confirmed live against the
    published release (subprocess, isolated HOME).
    Root cause: the release build embeds anchor names only; the
    consumer's alias contract (81% of the old index) was not part of
    the release contract. Fix shape: EITHER the model team embeds
    alias vectors into anchors.faiss (restores parity) OR the
    runtime joins value_sets consumer_synonyms/derived_synonyms at
    query time (exact-dictionary first, like _group_dictionary_search).
    Owner call — flagged to the bus, not fixed here (findings-only).

Controls: anchor-name queries still resolve exactly on the new
layout ('acetaminophen' 1.0; 'Plasma Metanephrines' 1.0 — the
latter IS the anchor name live, verifying anchor-name recall is
intact); the 0.70 floor correctly rejects the now-distant brand
vectors rather than mis-resolving.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("medterm4ds.services.search")

try:
    import urllib.request

    with urllib.request.urlopen(
        "https://huggingface.co/api/models/fhir4ds/terminology", timeout=10
    ) as _r:
        _OK = _r.status == 200
except Exception:
    _OK = False

PY = sys.executable
REPO = Path(__file__).parents[2]

_PROBE = """
import json, sys
sys.path.insert(0, 'src')
import medterm4ds.services.search as sm
svc = sm.SearchService()
out = {}
for q in sys.argv[1:]:
    r = svc.canonical(q, count=3)
    out[q] = [
        {'canonical_id': x.canonical_id, 'score': round(x.score, 3),
         'matched_via_display': x.matched_via_display}
        for x in r[:2]
    ]
print('RESULT_JSON=' + json.dumps(out))
"""


def _probe(home: Path, queries: list[str]) -> dict:
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
    r = subprocess.run(
        [PY, "-c", _PROBE, *queries],
        capture_output=True,
        text=True,
        timeout=3600,
        env=env,
        cwd=str(REPO),
    )
    m = re.search(r"RESULT_JSON=(\{.*\})", r.stdout, re.S)
    assert m, f"probe failed rc={r.returncode}; stderr: {r.stderr[-600:]}"
    return json.loads(m.group(1))


@pytest.fixture(scope="module")
def shared_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("ts114_home") / "home"
    home.mkdir(parents=True)
    return home


@pytest.fixture(scope="module")
def live_results(shared_home):
    return _probe(shared_home, ["Tylenol", "Adbry", "acetaminophen", "Plasma Metanephrines"])


class TestAliasRecallRegression:
    # AL1 FIXED (release 2026.10.31.1+ embeds 232,544 alias vectors):
    # flipped from strict-xfail to a positive regression test on
    # 2026-11-02. Contract pinned: brand queries (Tylenol, Adbry) MUST
    # resolve to their primary anchors via the embedded alias vectors —
    # if this fails again, alias vectors were dropped from the release
    # (the original AL1 regression).
    def test_al1_brand_queries_resolve(self, live_results):
        tylenol = live_results["Tylenol"]
        adbry = live_results["Adbry"]
        assert any(r["canonical_id"] == "VAL-MED-RXNORM-161" for r in tylenol), (
            f"Tylenol must resolve to the acetaminophen anchor "
            f"(legacy alias evidence); got {tylenol}"
        )
        assert any(r["canonical_id"] == "VAL-MED-RXNORM-2589225" for r in adbry), (
            f"Adbry must resolve to the tralokinumab anchor; got {adbry}"
        )


class TestControls:
    def test_c1_anchor_names_exact(self, live_results):
        """Anchor-name recall intact: exact 1.0 hits on anchor names."""
        aceta = live_results["acetaminophen"]
        assert any(r["canonical_id"] == "VAL-MED-RXNORM-161" for r in aceta)
        pm = live_results["Plasma Metanephrines"]
        assert any(r["canonical_id"] == "VAL-LAB-LOINC-43108-0" for r in pm)

    def test_c2_floor_rejects_not_misresolves(self, live_results):
        """The 0.70 floor admits only above-threshold results — never a
        WRONG anchor (defense against the regression silently
        mis-resolving). Post-AL1 the brand vectors resolve above the
        floor, so Tylenol/Adbry results here must all be ≥ 0.70 AND
        land on the correct anchors (pinned by test_al1 above)."""
        for q in ("Tylenol", "Adbry"):
            for r in live_results[q]:
                assert r["score"] >= 0.70, f"{q}: below-floor result leaked: {r}"
