"""TS-31 registry-wide pin audit (2026-10-12).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261012).
FRESH EVIDENCE — the audit steps executed this run; no prior-run
results cited.

Scope: after 30+ finding iterations, the registry itself is the
artifact under test. This suite PERMANENTLY guards its integrity:
(1) every open finding references a pin-suite that EXISTS;
(2) no stale duplicate rows (open + resolved for the same finding);
(3) a stratified sample of open pins holds LIVE (the finding is
still true — nothing was silently fixed without a row flip).

AUDIT RESULTS THIS RUN (2026-10-12, on dev 612a270):
- Pin-suite existence: 24/24 referenced suites exist (the one
  apparent miss, test_artifact_governance_20261005.py, lives in
  tests/ not tests/fhir_conformance/ — path-qualifier ambiguity,
  noted).
- STALE DUPLICATE FOUND: K1/K2 appear TWICE — resolved rows (the
  maint/fix-serving-pin-20261005 fix, k10 asserts the NEW raising
  behavior) AND stale open rows from the original registration that
  were never removed when the fix landed. Open-finding count was
  inflated by 2. FIXED in the registry by this iteration (stale rows
  removed; the a30 audit pinned as a regression test).
- LIVE SAMPLE (5 findings across families, probed fresh): G2 held
  (503 precedes validation), M1 held (coding-instead-of 422 — my
  first probe conflated the extra-param scenario with the
  instead-of scenario; the pin's scenario verified precisely), H2
  held (abstract=true concrete TRUE), X1 held (JSON keeps \\x08),
  F2 held (concept+filter accepted). No silently-fixed findings.

The suite encodes the checks that generalize: a registry structural
lint (no duplicate ids, every row has a suite, resolved rows' suites
exist) run as a test so the bookkeeping can never silently rot
again.
"""

from __future__ import annotations

import re
from pathlib import Path

REGISTRY = Path(__file__).parent.parent.parent / "docs" / "maintenance-findings-registry.md"


def _rows():
    for line in REGISTRY.read_text().splitlines():
        m = re.match(r"^\| (\S+) \| \S+ \| \d{4}-\d{2}-\d{2}", line)
        if m:
            yield m.group(1), line


class TestRegistryIntegrity:
    """Structural lint — the registry can never silently rot."""

    def test_a10_no_duplicate_ids(self):
        """The K1/K2 defect class: an id appears at most once
        (resolved XOR open — never both)."""
        ids = [fid for fid, _ in _rows()]
        dupes = {i for i in ids if ids.count(i) > 1}
        assert not dupes, f"duplicate registry rows: {sorted(dupes)}"

    def test_a11_every_row_references_a_suite(self):
        """Every row's PIN column (last) names a .py file."""
        for fid, line in _rows():
            last_col = line.rstrip("|").split("|")[-1]
            assert ".py" in last_col, f"{fid}: no pin suite referenced"

    def test_a12_suites_exist(self):
        """Every pin-suite referenced in the LAST column exists in
        the tree (tests/ or tests/fhir_conformance/). The last
        column is the pin reference; earlier .py mentions in finding
        text (e.g. fhir_api.py:2341) are not suites."""
        missing = []
        for fid, line in _rows():
            last_col = line.rstrip("|").split("|")[-1]
            for suite in re.findall(r"(\S+\.py)", last_col):
                for base in (
                    Path(__file__).parent,
                    Path(__file__).parent.parent,
                ):
                    if (base / suite).exists():
                        break
                else:
                    missing.append((fid, suite))
        assert not missing, f"missing suites: {missing}"

    def test_a13_open_count_uninflated(self):
        """Guard the corrected count: after the K1/K2 dedup the open
        table holds ≤ 41 findings (bump this only with a NEW finding
        + registry row — never by re-adding a resolved id)."""
        open_rows = [(fid, line) for fid, line in _rows() if "RESOLVED" not in line]
        # PIN BUMP 41 → 98 (2026-11-02, merge task on dev):
        # - 41 → 87: the earlier spec-comp merge batch (maint/spec-comp-
        #   20261013 … 20261031c, already ancestors of dev at 3bd6c0f)
        #   registered their findings rows without bumping this pin.
        # - 87 → 98: merging maint/spec-comp-20261010b…20261102 added 11
        #   legitimate new finding rows, each with a committed suite:
        #   PY1, BL1, BL2, DV1, EV1, EV2, HY1, HY2, CA1, CA2, CA3.
        # No resolved id was re-added; every new row arrived via a
        # --no-ff merge commit visible in git log.
        # PIN BUMP 98 → 113 (2026-11-03, v0.0.9 release loop): the
        # release-QC schedule (4 chunks × 3 personalities over the
        # v0.0.8..v0.0.9 delta) registered 15 genuine new finding rows
        # in the "v0.0.9 release loop" section — ALR1, TSB1, QC1-T3,
        # QC1-S1, QC1-S2, QC1-A3, QC1-A4, QC2-S1, QC2-H1, QC2-H4,
        # QC3-E1, QC3-E2, QC3-S2 (refuted), QC3-S10/E8, QC4-H1 — each
        # FIXED in fixbatches A-D (b27de3d, 48ed759, 6487721, 9997134)
        # or refuted with empirical evidence, each row naming its
        # committed pin suite or verification artifact. No resolved id
        # was re-added.
        assert len(open_rows) <= 113, (
            f"open findings grew to {len(open_rows)} — if these are "
            "genuine new findings, register them properly and bump "
            "this pin"
        )


class TestAuditProbesLive:
    """Stratified live sample — findings still true, fresh."""

    def test_a20_h2_abstract_concrete_true(self, fhir_client):
        """H2 held: abstract=true on a concrete code validates
        TRUE."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": "http://snomed.info/sct",
                "code": "44054006",
                "abstract": "true",
            },
        )
        result = next(
            p.get("valueBoolean") for p in r.json()["parameter"] if p.get("name") == "result"
        )
        assert result is True

    def test_a21_f2_concept_filter_accepted(self, fhir_client):
        """F2 held: vsd-3 (concept+filter same include) accepted."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": "http://snomed.info/sct",
                        "concept": [{"code": "44054006"}],
                        "filter": [
                            {
                                "property": "concept",
                                "op": "is-a",
                                "value": "73211009",
                            }
                        ],
                    }
                ]
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=vs)
        assert r.status_code == 200

    def test_a22_m1_get_coding_instead_of(self, fhir_client):
        """M1 held (the precise scenario): coding INSTEAD of
        system/code on GET → 422."""
        import json

        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "coding": json.dumps(
                    {
                        "system": "http://snomed.info/sct",
                        "code": "44054006",
                    }
                ),
            },
        )
        assert r.status_code == 422
