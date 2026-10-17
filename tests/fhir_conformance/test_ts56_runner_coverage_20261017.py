"""TS-56 conformance-runner coverage vs shipped contracts (2026-10-17).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261017b).
FRESH EVIDENCE — live probes + cases.json analysis executed this run;
no prior-run results cited.

Scope: cases.json (the portable runner contract driving
test_runner.py — the CI-facing conformance surface) vs the five
fix-batches' shipped 400-contracts. Iteration 30 (TS-19 c14) verified
the runner's EXISTING cases match live behavior; this iteration
surveys the COVERAGE axis: which shipped contracts have no runner
case at all.

CN1 (LOW-MED, NEW) — runner coverage lags shipped contracts.
    Fix-batches 1-4 + 6 landed reject-loudly contracts that exist
    ONLY in the personality suites; cases.json (35 cases) has ZERO
    entries for: version/systemVersion 400s (H1/U4), unknown-param
    400s on any op (EA/W4/U3), reverse=true semantics (T1), date
    validation (EB/H4). The runner — the surface an external CI or
    certification harness would drive — cannot distinguish this
    server from a pre-fixbatch server on any of those contracts.
    Fix shape: add the case classes (each verified live this run —
    all six probe shapes behave as the fix-batches pinned: five 400s
    + one reverse-200). This suite pins the GAP; the runner
    extension itself is Joel's call (shared-file policy).
    Pinned: cn10 (gap census, structural), cn11/cn12 (the contract
    shapes verified live so the future cases' expected values are
    pre-derived).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
T2DM = "44054006"


def _case_ids() -> list[str]:
    cases = json.loads(
        (Path(__file__).parent / "cases.json").read_text()
    )["cases"]
    return [c["id"] for c in cases]


class TestCN1RunnerCoverageGap:
    """CN1 — fix-batch contracts absent from the portable runner."""

    def test_cn10_gap_census(self):
        """Structural census: zero cases mention version-400s,
        unknown-param 400s, reverse, or date validation. Flip when
        the case classes land (assert >=1 per family)."""
        ids = _case_ids()
        assert not any("version" in i for i in ids), (
            "version cases landed — flip this census"
        )
        assert not any(
            "unknown-param" in i or "bogus" in i for i in ids
        ), "unknown-param cases landed — flip"
        assert not any("reverse" in i for i in ids), (
            "reverse cases landed — flip"
        )

    def test_cn11_live_contract_shapes_400(self, fhir_client):
        """The six fix-batch contract shapes verified LIVE (so the
        future cases' expected_status values are pre-derived):
        version / systemVersion / unknown-param (lookup + translate) /
        date all 400."""
        probes = [
            ("/fhir/CodeSystem/$lookup",
             {"system": SNOMED_URI, "code": T2DM, "version": "9.9"}),
            ("/fhir/CodeSystem/$lookup",
             {"system": SNOMED_URI, "code": T2DM,
              "systemVersion": "9.9"}),
            ("/fhir/CodeSystem/$lookup",
             {"system": SNOMED_URI, "code": T2DM,
              "bogusParam": "1"}),
            ("/fhir/ConceptMap/$translate",
             {"system": SNOMED_URI, "code": T2DM,
              "targetSystem": SNOMED_URI, "bogusParam": "1"}),
            ("/fhir/ValueSet/$expand",
             {"url": f"{SNOMED_URI}/73211009?fhir_vs=isa",
              "date": "garbage"}),
        ]
        for path, params in probes:
            r = fhir_client.get(path, params=params)
            assert r.status_code == 400, (path, params)

    def test_cn12_live_reverse_shape(self, fhir_client):
        """reverse=true (T1) live shape: 200 with the reverse-named
        message — the expected shape for a future runner case."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI, "reverse": "true",
            },
        )
        assert r.status_code == 200
        message = next(
            p.get("valueString")
            for p in r.json()["parameter"]
            if p.get("name") == "message"
        )
        assert "reverse" in message


class TestTS56Controls:
    """Controls — the runner's existing contract still holds."""

    def test_c10_case_count_floor(self):
        """The driver is substantial (35 cases at survey time) and
        structurally sound — the gap is absence of NEW classes, not
        erosion."""
        assert len(_case_ids()) >= 30

    def test_c11_translate_no_target_case_exists(self):
        """The one modern contract that DID get a runner case
        (fixbatch3's translate-no-target → 400) — control that the
        gap is per-family, not universal."""
        assert any("translate-no-target" in i for i in _case_ids())
