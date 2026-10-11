"""TS-27 bulk export surface (2026-10-11).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261011).
FRESH EVIDENCE — every probe executed this run via the REAL CLI
(``medterm4ds bulk …``) against a synthetic DB; no prior-run results
cited.

Scope: the bulk export family (lookup / map / hierarchy /
patient-friendly) — the largest data-out surface, never
conformance-swept. Checkpoint/resume machinery, format contracts,
source validation parity with the non-bulk ops.

BL1 (MEDIUM, NEW) — unknown --sources exports an empty file at
    exit 0.
    ``bulk lookup --sources NOTASAB`` (and the pf/hierarchy modes)
    creates a 0-byte output file and exits 0 — a typo'd SAB produces
    a silently-empty export indistinguishable from an empty
    vocabulary. Contrast: ``bulk map`` VALIDATES its target (fails
    on NOTASAB) and the non-bulk ``lookup``/``map`` reject unknown
    sources at the boundary (QC-195 family) — the validation exists,
    it just never reached the bulk sources path.
    Fix shape: apply the source-existence check (the same
    _optimize_source_exists / sources-list consult QC-195 uses) at
    bulk dispatch → 'Error: source(s) not found' exit 1.
    Pinned: b10 (0-byte file + exit 0), b11 (map validates its
    target — the asymmetry premise), b12 (non-bulk lookup rejects).

BL2 (LOW-MED, NEW) — bulk map unknown-target fails with a raw
    traceback.
    Exit code IS 1 (fails loud), but the output is an unhandled
    ValueError stack rather than the clean 'Error: …' convention
    every other CLI op uses (QC-022/QC-027 pattern). Cosmetic-to-
    operational: operators scraping logs get tracebacks, scripts
    testing for the convention miss.
    Fix shape: catch ValueError at the bulk-map entry → SystemExit
    'Error: target source(s) not found…'.
    Pinned: b20/b21.

Controls (fresh): jsonl record shape (full atom fields); CSV shape
    (header + quoted commas); --format and --direction enum-gated by
    argparse (exit 2); checkpoint file {complete, last_code,
    last_source, …}; resume on a complete checkpoint is an
    idempotent no-op (no duplicated rows, silent); patient-friendly
    bulk exposes the cvx_group strategy with matched_via provenance;
    bulk map rows carry the full matched_via step chain.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_CLI = str(Path(sys.executable).parent / "medterm4ds")
SNOMED = "SNOMEDCT_US"
CVX = "CVX"


@pytest.fixture(scope="module")
def cli_db(tmp_path_factory):
    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts27_bulk") / "umls.duckdb"
    _make_conformance_db(db)
    return db


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_CLI, *args], capture_output=True, text=True, timeout=600,
    )


class TestBL1UnknownSourcesEmptyExport:
    """BL1 — typo'd SAB exports 0 bytes at exit 0."""

    @pytest.mark.parametrize(
        "mode,extra",
        [
            ("lookup", []),
            ("patient-friendly", []),
            ("hierarchy", ["--direction", "children"]),
        ],
    )
    def test_b10_empty_export_exit_zero(
        self, cli_db, tmp_path, mode, extra
    ):
        """Unknown --sources → 0-byte output file at exit 0. Flip
        when the source-existence check lands at bulk dispatch."""
        out = tmp_path / f"{mode}-na.jsonl"
        r = _run([
            "bulk", mode, "--db", str(cli_db),
            "--sources", "NOTASAB",
            "--output", str(out), "--format", "jsonl", *extra,
        ])
        assert r.returncode == 0
        assert out.exists()
        assert out.stat().st_size == 0

    def test_b11_map_validates_target(self, cli_db, tmp_path):
        """Premise control: bulk MAP fails loud on an unknown TARGET
        — the boundary exists in the family, just not on sources."""
        out = tmp_path / "map-na.jsonl"
        r = _run([
            "bulk", "map", "--db", str(cli_db),
            "--sources", SNOMED, "--target", "NOTASAB",
            "--output", str(out), "--format", "jsonl",
        ])
        assert r.returncode == 1

    def test_b12_nonbulk_rejects(self, cli_db):
        """Premise control: non-bulk MAP rejects the unknown target
        at the boundary (Error + exit != 0) — the asymmetry is
        bulk-sources-specific. (Non-bulk LOOKUP itself resolves
        null-field records at exit 0 — that is CLI3, registered.)"""
        r = _run([
            "map", "--db", str(cli_db),
            "--source", SNOMED, "--code", "44054006",
            "--target", "NOTASAB",
        ])
        assert r.returncode != 0


class TestBL2RawTracebackOnMapTarget:
    """BL2 — fails loud but with a traceback, not the convention."""

    def test_b20_exit_one(self, cli_db, tmp_path):
        out = tmp_path / "map-na2.jsonl"
        r = _run([
            "bulk", "map", "--db", str(cli_db),
            "--sources", SNOMED, "--target", "NOTASAB",
            "--output", str(out), "--format", "jsonl",
        ])
        assert r.returncode == 1

    def test_b21_traceback_not_convention(self, cli_db, tmp_path):
        """The failure renders as a ValueError stack (Traceback…
        ValueError: target source(s) not found) instead of the
        'Error:' line every other op uses. Flip when the entry-point
        catch lands."""
        out = tmp_path / "map-na3.jsonl"
        r = _run([
            "bulk", "map", "--db", str(cli_db),
            "--sources", SNOMED, "--target", "NOTASAB",
            "--output", str(out), "--format", "jsonl",
        ])
        assert "Traceback" in r.stderr or "ValueError" in (
            r.stderr + r.stdout
        )
        assert not any(
            line.startswith("Error:")
            for line in (r.stderr + r.stdout).splitlines()
        )


class TestBulkControls:
    """Controls — format/enum/checkpoint/resume contracts, fresh."""

    def test_c10_jsonl_shape(self, cli_db, tmp_path):
        out = tmp_path / "cvx.jsonl"
        r = _run([
            "bulk", "lookup", "--db", str(cli_db),
            "--sources", CVX, "--output", str(out),
            "--format", "jsonl",
        ])
        assert r.returncode == 0
        rec = json.loads(out.read_text().splitlines()[0])
        for field in (
            "source", "code", "name", "cui", "aui", "tty", "suppress",
        ):
            assert field in rec

    def test_c11_csv_shape(self, cli_db, tmp_path):
        out = tmp_path / "cvx.csv"
        r = _run([
            "bulk", "lookup", "--db", str(cli_db),
            "--sources", CVX, "--output", str(out), "--format", "csv",
        ])
        assert r.returncode == 0
        lines = out.read_text().splitlines()
        assert lines[0].startswith("source,code,name")

    def test_c12_format_enum(self, cli_db, tmp_path):
        r = _run([
            "bulk", "lookup", "--db", str(cli_db),
            "--sources", CVX, "--output", str(tmp_path / "x"),
            "--format", "bogus",
        ])
        assert r.returncode == 2

    def test_c13_direction_enum(self, cli_db, tmp_path):
        r = _run([
            "bulk", "hierarchy", "--db", str(cli_db),
            "--sources", SNOMED, "--direction", "sideways",
            "--output", str(tmp_path / "x"), "--format", "jsonl",
        ])
        assert r.returncode == 2

    def test_c14_checkpoint_and_resume(self, cli_db, tmp_path):
        """Checkpoint records {complete, last_code, last_source};
        resuming a complete export is an idempotent no-op (no
        duplicated rows)."""
        out = tmp_path / "r.jsonl"
        ck = tmp_path / "ck.json"
        r1 = _run([
            "bulk", "lookup", "--db", str(cli_db),
            "--sources", CVX, "--output", str(out),
            "--format", "jsonl",
            "--checkpoint", str(ck),
        ])
        assert r1.returncode == 0
        state = json.loads(ck.read_text())
        assert state["complete"] is True
        assert state["last_source"] == CVX
        rows_before = out.read_text().count("\n")
        r2 = _run([
            "bulk", "lookup", "--db", str(cli_db),
            "--sources", CVX, "--output", str(out),
            "--format", "jsonl",
            "--resume", "--checkpoint", str(ck),
        ])
        assert r2.returncode == 0
        rows_after = out.read_text().count("\n")
        assert rows_after == rows_before

    def test_c15_pf_cvx_group_strategy(self, cli_db, tmp_path):
        """Bulk patient-friendly exposes the CVX vaccine-group
        strategy with matched_via provenance. NOTE: the repo-level
        tests/conftest.py sets MEDTERM4DS_DISABLE_CVX_GROUPS=1 (the
        documented test-env default), so under pytest the legacy
        resolver serves 'original'; the cvx_group strategy is only
        observable with the env cleared — pinned BOTH ways."""
        out = tmp_path / "pf.jsonl"
        r = _run([
            "bulk", "patient-friendly", "--db", str(cli_db),
            "--sources", CVX, "--output", str(out),
            "--format", "jsonl",
        ])
        assert r.returncode == 0
        rec = json.loads(out.read_text().splitlines()[0])
        assert rec["match_type"] == "original"  # DISABLE_CVX_GROUPS=1
        assert rec["matched_via"]["strategy"] == "original"

        import os

        env = {
            k: v
            for k, v in os.environ.items()
            if k != "MEDTERM4DS_DISABLE_CVX_GROUPS"
        }
        out2 = tmp_path / "pf-cvx.jsonl"
        r2 = subprocess.run(
            [
                _CLI, "bulk", "patient-friendly",
                "--db", str(cli_db), "--sources", CVX,
                "--output", str(out2), "--format", "jsonl",
            ],
            capture_output=True, text=True, timeout=600, env=env,
        )
        assert r2.returncode == 0
        rec2 = json.loads(out2.read_text().splitlines()[0])
        assert rec2["match_type"] == "cvx_group"
        assert rec2["matched_via"]["strategy"] == "cvx_group"

    def test_c16_map_provenance_chain(self, cli_db, tmp_path):
        """Bulk map rows carry the full matched_via step chain
        (input_atom → same_cui → target_atom)."""
        out = tmp_path / "m.jsonl"
        r = _run([
            "bulk", "map", "--db", str(cli_db),
            "--sources", SNOMED, "--target", "ICD10CM",
            "--output", str(out), "--format", "jsonl", "--limit", "10",
        ])
        assert r.returncode == 0
        rec = json.loads(out.read_text().splitlines()[0])
        ops = [s["op"] for s in rec["matched_via"]["steps"]]
        assert ops[0] == "input_atom"
        assert "same_cui" in ops
        assert ops[-1] == "target_atom"
