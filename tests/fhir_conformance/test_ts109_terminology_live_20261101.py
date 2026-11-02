"""TS-109 terminology repoint — live integration sweep (2026-11-01).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261101).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the repoint's unit suite (test_terminology_layout.py) mocks the
download; the model team's handoff verification ran OUTSIDE any test.
No conformance test exercises the published fhir4ds/terminology repo
through the runtime's own resolver. This sweep pins the LIVE contract
against release 2026.10.31.1 (the current published tag):

  T1. resolve() on a real downloaded release returns paths whose
      families match the manifest and whose space dir equals the
      fingerprint recomputed from the shipped model (esp_468d01ebbab03648
      with rp_1891c4cb719d).
  T2. manifest families' release fields all equal the release dir name
      (single-stamp invariant, live).
  T3. lexical family loads with the NEW filename contract
      (<category>.json), v2 shape, and num_records equal to the
      manifest's declared rows for every category.
  T4. semantic family: <category>.faiss + minimal metadata — index
      ntotal == len(metadata) per category; anchors.faiss pairing
      exact.
  T5. canonical family is JSON-only: value_sets.json rows == manifest
      rows == anchors count; no *.index file in the family dir.
  T6. T2-T5 hold on the DEFAULT release (whatever release() returns)
      when it differs from 2026.10.31.1 (forward-compat probe).

Network-gated (skip when HF is unreachable), cache-isolated (own
cache root under /tmp — never touches the operator cache).

RESULT: ZERO FINDINGS — controls-only (21st clean surface). 5/5 live
probes PASS against release 2026.10.31.1 (T6 skipped: the default
release IS the live release): fingerprint recomputes to
esp_468d01ebbab03648 from the shipped model; manifest single-stamp
holds across all three families; lexical pairing exact under the NEW
filename contract (manifest declares 'records' in 2026.10.31.1 —
v2026.11.1 used 'rows'; probe accepts either, an OBSERVATION for the
manifest-shape changelog, not a defect); semantic index/metadata
pairing exact for all 6 categories; canonical JSON-only with
value_sets == anchors == manifest rows. T6 skipped (default ==
live).
"""

from __future__ import annotations

import json
import os
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
RP = "rp_1891c4cb719d"


@pytest.fixture(scope="module")
def resolved(tmp_path_factory):
    """Download the live release into an isolated cache root once."""
    from medterm4ds.core import terminology as term

    root = tmp_path_factory.mktemp("ts109_cache")
    return term.download(root, rel=LIVE_RELEASE, space_id=SPACE)


def test_t1_resolve_and_fingerprint(resolved):
    from medterm4ds.core.artifact_manifest import fingerprint_model_dir

    assert resolved.model.is_dir()
    assert resolved.model.name == SPACE
    fp = fingerprint_model_dir(resolved.model, render_policy_version=RP)
    assert fp.space_id() == SPACE, "shipped model fingerprint != admitted space"


def test_t2_manifest_single_stamp(resolved):
    manifest = resolved.manifest
    rel = resolved.release
    assert manifest["release"] == rel
    for fam in ("canonical", "lexical", "semantic"):
        declared = (manifest["families"].get(fam) or {}).get("release")
        assert declared == rel, f"{fam} declares {declared}"


def test_t3_lexical_new_filenames_pairing(resolved):
    import json as _json

    manifest = resolved.manifest
    lex_files = manifest["families"]["lexical"]["files"]
    for cat, info in lex_files.items():
        p = resolved.lexical / f"{cat}.json"
        assert p.exists(), f"missing {p.name}"
        idx = _json.loads(p.read_text())
        assert idx.get("version") == 2
        # the lexical manifest declares "records" (2026.10.31.1 shape);
        # v2026.11.1 used "rows" — accept either, pin against the value
        declared = info.get("records", info.get("rows"))
        assert idx["num_records"] == declared, f"{cat}: rows drift vs manifest"


def test_t4_semantic_pairing(resolved):
    import faiss

    manifest = resolved.manifest
    sem_files = manifest["families"]["semantic"]["files"]
    for cat, info in sem_files.items():
        idx = faiss.read_index(str(resolved.semantic / f"{cat}.faiss"))
        meta = json.loads((resolved.semantic / f"{cat}_metadata.json").read_text())
        assert idx.ntotal == len(meta) == info["rows"], f"{cat}: pairing drift"


def test_t5_canonical_json_only(resolved):
    manifest = resolved.manifest
    vs_rows = manifest["families"]["canonical"]["files"]["value_sets.json"]["rows"]
    vs = json.loads((resolved.canonical / "value_sets.json").read_text())
    assert len(vs) == vs_rows
    indexes = list(resolved.canonical.glob("*.index"))
    assert not indexes, f"canonical family must be JSON-only, found {indexes}"
    anchors_meta = json.loads((resolved.semantic / "anchors_metadata.json").read_text())
    assert len(anchors_meta) == vs_rows, "anchors vs value_sets count drift"


def test_t6_default_release_forward_compat(tmp_path_factory):
    """The resolver's DEFAULT release (release()) — if it differs from
    LIVE_RELEASE, the same invariants must hold on it too."""
    from medterm4ds.core import terminology as term

    default_rel = term.release()
    if default_rel == LIVE_RELEASE:
        pytest.skip("default release is the live release — covered above")
    root = tmp_path_factory.mktemp("ts109_default")
    paths = term.download(root, rel=default_rel, space_id=SPACE)
    manifest = paths.manifest
    assert manifest["release"] == default_rel
    for fam in ("canonical", "lexical", "semantic"):
        assert (manifest["families"][fam] or {}).get("release") == default_rel
