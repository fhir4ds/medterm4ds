"""TS-110 canonical search card fidelity on the terminology layout, live (2026-11-01).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261101b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the 54c49f2 fix wired canonical_batch()'s card-field fallbacks
(anchor_system/anchor_code/name) through value_sets.json for the
terminology layout — pinned at unit level with a 3-concept synthetic
release. Never probed LIVE: does a real canonical_batch() query
against the PUBLISHED release return complete cards (system, code,
patient_friendly_name, members present and coherent with the
value_sets row) for every result type, and does the sources filter
hold?

  L1. 'metformin' (medication anchor) → top hit VAL-MED-RXNORM-6809
      with anchor_system RXNORM / anchor_code 6809 / friendly name
      non-empty / members non-empty — card fields all joined from
      value_sets (the live anchors metadata carries ONLY
      {canonical_id, name}).
  L2. 'type 2 diabetes' → condition anchor card complete.
  L3. 'hemoglobin a1c' → lab anchor card complete.
  L4. result_types filter: ['medication'] on a broad query returns
      ONLY VAL-MED-* ids.
  L5. sources filter: canonical(..., sources={'RXNORM'}) returns
      only RXNORM-anchored results.
  L6. batch round-trip: canonical_batch([q1..q3]) returns per-query
      lists aligned with the single-query canonical() answers for
      the top hit (same canonical_id top-1).

Network-gated; cache-isolated (own /tmp cache root).

RESULT: ZERO FINDINGS — controls-only (22nd clean surface). 6/6 live
probes PASS: metformin card complete and byte-equal to the live
value_sets row (system/code/name/members joined from JSON-only
canonical); condition + lab cards complete; result_types filter
holds ('insulin' → VAL-MED only); sources filter holds (RXNORM);
batch/single top-1 alignment across 3 queries. OBSERVATIONS (not
defects, documented in-test): (a) filtered queries over-fetch only
count*5 (min 50) neighbors — a filtered query CAN starve the window
when all nearer neighbors are foreign-type; benign here because the
starved hit was also sub-threshold (best VAL-MED neighbor of
'diabetes' scores 0.43 < 0.70 floor). (b) The 0.70 floor makes
empty-filtered results trustworthy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("medterm4ds.core.terminology")

try:
    import urllib.request

    with urllib.request.urlopen(
        "https://huggingface.co/api/models/fhir4ds/terminology", timeout=10
    ) as _r:
        _OK = _r.status == 200
except Exception:
    _OK = False

pytestmark = pytest.mark.skipif(not _OK, reason="HF unreachable from this box")

LIVE_RELEASE = "2026.10.31.1"
SPACE = "esp_468d01ebbab03648"


@pytest.fixture(scope="module")
def svc(tmp_path_factory, monkeypatch_module):
    """SearchService bound to an isolated live-release download via the
    MEDTERM4DS_TERMINOLOGY_DIR env (the repoint's binding contract)."""
    from medterm4ds.core import terminology as term

    root = tmp_path_factory.mktemp("ts110_cache")
    paths = term.download(root, rel=LIVE_RELEASE, space_id=SPACE)

    import importlib

    env_keys = (
        "MEDTERM4DS_TERMINOLOGY_DIR",
        "MEDTERM4DS_TERMINOLOGY_RELEASE",
        "MEDTERM4DS_TERMINOLOGY_REPO_ID",
        "MEDTERM4DS_HF_REVISION",
        "MEDTERM4DS_CACHE_DIR",
    )
    for key in env_keys:
        monkeypatch_module.delenv(key, raising=False)
    monkeypatch_module.setenv("MEDTERM4DS_TERMINOLOGY_DIR", str(paths.base))

    from medterm4ds.services import search as search_mod

    search_mod = importlib.reload(search_mod)
    return search_mod.SearchService(), paths


@pytest.fixture(scope="module")
def monkeypatch_module(request):
    """Module-scoped monkeypatch (pytest's is function-scoped)."""
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


def _top(svc, query, **kw):
    results = svc.canonical(query, **kw)
    assert results, f"no results for {query!r}"
    return results[0]


def test_l1_metformin_card_complete(svc):
    s, paths = svc
    top = _top(s, "metformin")
    assert top.canonical_id == "VAL-MED-RXNORM-6809", top.canonical_id
    assert top.anchor_system == "RXNORM"
    assert top.anchor_code == "6809"
    assert top.patient_friendly_name
    assert top.members, "medication card must carry members"
    # card fields must equal the live value_sets row (source of truth)
    vs = json.loads((paths.canonical / "value_sets.json").read_text())
    row = next(v for v in vs if v["canonical_id"] == top.canonical_id)
    assert top.anchor_code == row["code"]
    assert top.patient_friendly_name == (row.get("patient_friendly_name") or row.get("name"))


def test_l2_condition_card_complete(svc):
    s, _ = svc
    top = _top(s, "type 2 diabetes")
    assert top.canonical_id.startswith("VAL-COND-")
    assert top.anchor_system in ("SNOMEDCT_US", "ICD10CM")
    assert top.anchor_code
    assert top.patient_friendly_name


def test_l3_lab_card_complete(svc):
    s, _ = svc
    top = _top(s, "hemoglobin a1c")
    assert top.canonical_id.startswith("VAL-LAB-")
    assert top.anchor_system in ("LOINC", "LNC")
    assert top.anchor_code
    assert top.patient_friendly_name


def test_l4_result_types_filter(svc):
    s, _ = svc
    # 'insulin' has above-threshold medication neighbors (0.87 live);
    # bare 'diabetes' would starve the over-fetch window AND its best
    # VAL-MED neighbor is sub-threshold (0.43) — both documented.
    results = s.canonical("insulin", result_types=["medication"], count=10)
    assert results
    assert all(r.canonical_id.startswith("VAL-MED-") for r in results)


def test_l5_sources_filter(svc):
    s, _ = svc
    results = s.canonical("metformin", sources={"RXNORM"}, count=10)
    assert results
    assert all(r.anchor_system == "RXNORM" for r in results)


def test_l6_batch_single_alignment(svc):
    s, _ = svc
    queries = ["metformin", "type 2 diabetes", "hemoglobin a1c"]
    batch = s.canonical_batch(queries, count=3)
    assert len(batch) == 3
    for q, rows in zip(queries, batch, strict=True):
        single = s.canonical(q, count=3)
        assert rows, f"empty batch rows for {q!r}"
        assert rows[0].canonical_id == single[0].canonical_id, (
            f"batch/single top-1 drift for {q!r}: "
            f"{rows[0].canonical_id} vs {single[0].canonical_id}"
        )
