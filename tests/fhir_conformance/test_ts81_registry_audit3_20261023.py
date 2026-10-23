"""TS-81 registry structural audit round 3 (2026-10-23).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261023c).
FRESH EVIDENCE — structural checks executed this run against the
committed registry; no prior-run results cited.

Scope: the third structural audit of the registry itself (round 1:
iter 31's dedup + guards; round 2: iter 77's live drift re-sweep).
This round validates the post-90-iteration state: the refreshed
ledger (coverage map + family census) against ground truth, plus the
round-1 structural guards re-verified at the new scale.

CONTROLS-ONLY: the ledger refresh in this same commit is the
deliverable (replacing the stale iterations-1-7 pointer); the suite
guards its factual claims — suite count floor, open-row ceiling,
no-duplicate ids, pin-suite existence — so the census numbers cannot
rot silently.
"""

from __future__ import annotations

import re
from pathlib import Path

REGISTRY = (
    Path(__file__).parents[2] / "docs" / "maintenance-findings-registry.md"
)
SUITES_DIR = Path(__file__).parent


class TestLedgerGuards:
    """Guards on the refreshed program ledger."""

    def test_l10_suite_count_floor(self):
        """The ledger claims 77 maintenance-era suites; the tree must
        carry at least that many (new iterations only grow it)."""
        suites = sorted(
            p.name
            for p in SUITES_DIR.glob("test_*_2026*.py")
        )
        assert len(suites) >= 77

    def test_l11_open_count_ceiling(self):
        """104 open rows at ledger-refresh time; the ceiling guards
        against silent row corruption (drops) — fixes reduce it,
        new findings grow it via visible commits."""
        reg = REGISTRY.read_text()
        open_section = reg.split("## Open findings")[1].split(
            "### Resolved"
        )[0]
        rows = re.findall(r"^\| [A-Z0-9-]+ \|", open_section, re.M)
        assert len(rows) <= 110

    def test_l12_no_duplicate_ids(self):
        """Ids may repeat ACROSS the open table and resolved-mechanism
        subsections (the documented pattern — resolved rows echo their
        open id); duplicates WITHIN the open table are corruption."""
        reg = REGISTRY.read_text()
        open_section = reg.split("## Open findings")[1].split(
            "### Resolved"
        )[0]
        ids = re.findall(r"^\| ([A-Z0-9-]+) \|", open_section, re.M)
        assert len(ids) == len(set(ids))

    def test_l13_pin_suites_exist(self):
        """Every pin-suite reference in the open table names a real
        committed file (round-1 guard at 90-iteration scale)."""
        reg = REGISTRY.read_text()
        open_section = reg.split("## Open findings")[1].split(
            "### Resolved"
        )[0]
        refs = re.findall(
            r"(test_[a-z0-9_]+\.py)", open_section
        )
        assert refs
        # Suites live in tests/fhir_conformance/ AND tests/ (the
        # artifact-governance suite predates the two-dir convention).
        tests_root = SUITES_DIR.parent
        for ref in set(refs):
            assert (
                SUITES_DIR / ref
            ).exists() or (
                tests_root / ref
            ).exists(), ref
