"""TS-115 without-cpt attestation audit, LIVE release (2026-11-02).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261102d).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the release's `variant: without-cpt / cpt: excluded`
attestation vs the ACTUAL payload. Precedent: the v2026.11.1 release
carried 6,168 CPT:* keys in claimant_details.json despite the
attestation (flagged to the model team 2026-10-31); 2026.10.31.1 was
expected to fix it. This suite pins the CLEAN state as a live
regression guard across every family.

  C1. canonical/value_sets.json: zero CPT-system rows.
  C2. canonical/claimant_details.json: zero CPT:* keys (the v2026.11.1
      regression shape).
  C3. canonical/decomposition_links.json: zero CPT references.
  C4. lexical/<category>.json for all six categories: no cpt-ish
      rid_to_system values (systems enumerated fully — also pins the
      expected source vocabulary per category).
  C5. semantic/<category>_metadata.json for all seven (incl.
      anchors): zero CPT-ish refs.
  C6. NOTICE.md present with the without-cpt variant declaration and
      the per-source attribution block (LOINC/SNOMED/UMLS/RxNorm/CMS).

Network-gated (snapshot_download of the live release); the audit is
read-only and cache-shared with the HF cache.

RESULT: ZERO FINDINGS — controls-only (26th clean surface). 6/6 live
probes PASS fresh: the 2026.10.31.1 release is FULLY CPT-CLEAN — the
model team's claimant_details fix landed (0 CPT:* keys vs 6,168 on
v2026.11.1); zero CPT rows/refs across value_sets, decomposition,
all six lexical categories (with the expected source-vocabulary sets
enumerated per category), and all seven semantic metadata files;
NOTICE.md declares the variant and carries every required
attribution. The attestation now matches the payload.
"""

from __future__ import annotations

import json
import re
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

REL = "2026.10.31.1"
CPT_RE = re.compile(r'"CPT[:"]|\"system\"\s*:\s*\"[^\"]*cpt[^\"]*\"', re.I)


@pytest.fixture(scope="module")
def snap():
    from huggingface_hub import snapshot_download

    return Path(snapshot_download("fhir4ds/terminology", revision=REL))


def test_c1_value_sets_no_cpt(snap):
    vs = json.loads((snap / f"canonical/{REL}/value_sets.json").read_text())
    assert len(vs) == 53169
    assert not [v for v in vs if (v.get("system") or "").upper().startswith("CPT")]


def test_c2_claimant_no_cpt_keys(snap):
    """The v2026.11.1 regression shape: 6,168 CPT:* claim keys under a
    without-cpt attestation. Must stay zero."""
    claim = json.loads((snap / f"canonical/{REL}/claimant_details.json").read_text())
    cpt_keys = [k for k in claim if str(k).upper().startswith("CPT")]
    assert not cpt_keys, f"CPT claim keys leaked: {cpt_keys[:5]}... ({len(cpt_keys)})"


def test_c3_decomposition_no_cpt(snap):
    dl = (snap / f"canonical/{REL}/decomposition_links.json").read_text()
    assert not re.findall(r'CPT[:"]', dl)


def test_c4_lexical_systems_clean(snap):
    expected = {
        "condition": {"icd10cm", "snomedct_us"},
        "medication": {"atc", "rxnorm", "snomedct_us"},
        "lab": {"lnc", "snomedct_us"},
        "procedure": {"hcpcs", "icd10pcs", "snomedct_us"},
        "body_structure": {"snomedct_us"},
        "vaccine": {"cvx", "snomedct_us"},
    }
    for cat, want in expected.items():
        lex = json.loads((snap / f"lexical/{REL}/{cat}.json").read_text())
        systems = set(lex.get("rid_to_system") or [])
        assert systems == want, f"{cat}: systems {sorted(systems)} != {sorted(want)}"
        assert not {s for s in systems if "cpt" in s.lower()}


def test_c5_semantic_no_cpt(snap):
    for cat in (
        "condition",
        "medication",
        "lab",
        "procedure",
        "body_structure",
        "vaccine",
        "anchors",
    ):
        meta = (snap / f"semantic/{REL}/{cat}_metadata.json").read_text()
        hits = CPT_RE.findall(meta)
        assert not hits, f"{cat}: CPT-ish refs leaked: {hits[:3]}"


def test_c6_notice_declares_variant_and_attribution(snap):
    notice = (snap / "NOTICE.md").read_text()
    assert "without-cpt" in notice and "cpt: excluded" in notice
    for attribution in ("LOINC", "SNOMED CT", "RxNorm", "UMLS", "Centers for Medicare"):
        assert attribution in notice, f"NOTICE.md missing {attribution!r} attribution"
