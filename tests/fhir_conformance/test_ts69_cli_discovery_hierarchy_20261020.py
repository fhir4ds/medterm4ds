"""TS-69 CLI discovery + hierarchy families (2026-10-20).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261020c).
FRESH EVIDENCE — live CLI subprocess probes executed this run against
the production umls_current.duckdb; no prior-run results cited.

Scope: the CLI commands never audited — the discovery family
(sources/source-stats/sample-codes/code-ttys/search-names) and the
hierarchy family (hierarchy children et al). TS-22 covered search/
extract, TS-38 bulk, TS-25 resolve; the inventory's remaining
read-only commands are swept here.

HQ1 (LOW, NEW) — hierarchy CLI unknown-source inherits PV2's
    mis-pointed fallback warning. `hierarchy children --source BOGUS`
    prints the prepared-tables warning ("mt4ds.walk_edges has 0 rows
    for source 'BOGUS' ... rebuild with medterm4ds data
    prepare-derived") then 200-equivalent {"results": []} exit 0.
    The remedy is wrong twice for a typo'd source: prepare-derived
    cannot fix an unknown SAB, and the sibling discovery commands
    (sources/sample-codes) correctly ERROR on the same input. HY1's
    unknown-source matrix (3 conventions) gains its CLI branch:
    discovery-Error vs hierarchy-warn-empty — per-command divergence
    in one binary. Fix shape: hierarchy validates source against the
    DB inventory like sample-codes does. Pinned hq10/hq11.

HQ2 (LOW) — CLI search-names is SN1's branch: bare {results}
    envelope, truncation unobservable (limit=2 rows=2; the count/
    truncated fields are absent). Registered under SN1's family;
    pinned here as the CLI instance. Pinned hq20.

Controls (fresh, all production-DB): sources/source-stats emit
    per-source atom/code counts (JSON, sorted); sample-codes
    per-source sampling works + unknown-source ERRORS exit≠0
    ('source(s) not found in this database'); code-ttys full atom
    rows (aui/cui/tty/suppress) for a known code + clean {"results":
    []} for an unknown code; hierarchy children returns direct
    children with depth=1 and a GOOD ergonomics warning when
    --max-depth is passed to a direct-traversal subcommand (points
    the user at ancestors/descendants).
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

PROD_DB = pathlib.Path("/mnt/d/medterm4ds/data/umls_current.duckdb")

pytestmark = pytest.mark.skipif(
    not PROD_DB.exists(),
    reason="production umls_current.duckdb not present",
)

# Installed entry point via the worktree's venv symlink.
MEDTERM = str(pathlib.Path(__file__).parents[2] / ".venv/bin/medterm4ds")
DB = str(PROD_DB)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [MEDTERM, *args, "--db", DB],
        capture_output=True,
        text=True,
        timeout=300,
    )


class TestHQ1HierarchyUnknownSource:
    """HQ1 — warn-empty instead of Error on unknown source."""

    def test_hq10_unknown_source_empty_results(self):
        """hierarchy children --source BOGUS → warning + {"results":
        []} + exit 0 (the sibling discovery commands ERROR). Flip when
        hierarchy validates the source against the inventory."""
        proc = _run(
            "hierarchy", "children",
            "--source", "BOGUS", "--code", "X",
        )
        assert proc.returncode == 0
        assert json.loads(proc.stdout)["results"] == []
        assert "walk_edges" in proc.stderr

    def test_hq11_sibling_commands_error(self):
        """Contrast control: sources/sample-codes ERROR on the same
        unknown source (exit≠0) — the per-command divergence."""
        for cmd in (
            ["sources", "--sources", "BOGUS"],
            ["sample-codes", "--sources", "BOGUS", "--per-source", "1"],
        ):
            proc = _run(*cmd)
            assert proc.returncode != 0, cmd
            assert "not found" in proc.stderr


class TestHQ2CLISearchEnvelope:
    """HQ2 — SN1's CLI branch (bare envelope)."""

    def test_hq20_bare_results_envelope(self):
        proc = _run(
            "search-names", "--query", "diabetes", "--limit", "2"
        )
        body = json.loads(proc.stdout)
        assert set(body.keys()) == {"results"}
        assert len(body["results"]) == 2  # capped; count unobservable


class TestTS69Controls:
    """Controls — discovery family conformant on the production DB."""

    def test_c10_sources_shape(self):
        proc = _run("sources")
        rows = json.loads(proc.stdout)["results"]
        assert rows
        first = rows[0]
        assert {"source", "atom_count", "code_count"} <= set(first)

    def test_c11_sample_codes_per_source(self):
        proc = _run(
            "sample-codes",
            "--sources", "SNOMEDCT_US", "--per-source", "1",
        )
        rows = json.loads(proc.stdout)["results"]
        assert len(rows) == 1
        assert rows[0]["source"] == "SNOMEDCT_US"

    def test_c12_code_ttys_known(self):
        proc = _run(
            "code-ttys", "--source", "SNOMEDCT_US", "--code", "44054006"
        )
        rows = json.loads(proc.stdout)["results"]
        assert rows
        first = rows[0]
        assert {
            "aui", "code", "cui", "name", "source", "suppress", "tty",
        } <= set(first)

    def test_c13_code_ttys_unknown_clean_empty(self):
        proc = _run(
            "code-ttys", "--source", "SNOMEDCT_US",
            "--code", "99999999",
        )
        assert json.loads(proc.stdout)["results"] == []

    def test_c14_hierarchy_children_depth(self):
        proc = _run(
            "hierarchy", "children",
            "--source", "SNOMEDCT_US", "--code", "73211009",
        )
        rows = json.loads(proc.stdout)["results"]
        assert rows
        assert all(r.get("depth") == 1 for r in rows)

    def test_c15_max_depth_ergonomics_warning(self):
        """GOOD ergonomics pinned: passing --max-depth to a direct
        traversal warns and points at ancestors/descendants."""
        proc = _run(
            "hierarchy", "children",
            "--source", "SNOMEDCT_US", "--code", "73211009",
            "--max-depth", "5",
        )
        assert proc.returncode == 0
        assert "ancestors" in proc.stderr
