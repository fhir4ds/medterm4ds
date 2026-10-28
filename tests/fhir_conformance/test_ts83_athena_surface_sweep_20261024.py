"""TS-83 Athena stage-1 surface spec-comp sweep (2026-10-24).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261024).
FRESH EVIDENCE — live CLI probes executed this run against the mini
Athena fixture; no prior-run results cited.

Scope: spec-comp discipline over the JUST-LANDED Athena stage-1
surface (commit db0ccb6 — my own campaign code; findings-only per
maintenance doctrine; fixes ride the campaign branch or a follow-up
fixbatch).

AF1 (LOW-MED, NEW) — export on a MISSING database dies with a raw
    _duckdb.IOException traceback (read-only connect fails, exit 1).
    The BL2/HT2 doctrine for config-boundary failures demands an
    actionable Error naming the path. Pinned af10.

AF2 (LOW-MED, NEW) — export on a VALID database WITHOUT athena
    tables dies with a raw _duckdb.CatalogException ('Table with
    name athena_vocabulary does not exist'). A user who forgot
    ingest-athena (or pointed at the wrong DB) gets a traceback, not
    'no athena_* tables — run medterm4ds data ingest-athena first'.
    Pinned af20.

AF3 (LOW, NEW) — unknown vocabularies are silently dropped from
    --vocabularies: export with SNOMED,BOGUS returns the SNOMED rows
    with no signal BOGUS matched nothing; ingest with BOGUS-only
    succeeds at 0 rows exit 0 (indistinguishable from an empty
    download). The sibling discovery commands Error on unknown
    sources (HQ1's fixed doctrine). Fix shape: intersect the filter
    against athena_vocabulary at ingest AND export; Error on empty
    intersection. Pinned af30/af31.

AF4 (LOW, NEW) — the thin form writes NDJSON line content to
    whatever extension --out carries (.json included): the content
    form is invisible in the filename. Fix shape: derive the form
    from the extension when --form is absent (.ndjson → thin,
    .json → monolithic) and warn on mismatch. Pinned af40.

Controls (fresh): license gate still exits 2 with the notice before
    any load; ingest→export round-trip on the mini fixture; the
    vocabulary filter narrows correctly when spelled right; thin
    NDJSON content parses line-by-line as ConceptMap resources.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("medterm4ds.services.athena")

MINI = None  # injected per-fixture
CLI = [sys.executable, "-m", "medterm4ds.apps.cli"]


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": "src"}
    return subprocess.run(
        [sys.executable, "-m", "medterm4ds.apps.cli", *args],
        capture_output=True, text=True, timeout=300, env=env,
    )


@pytest.fixture(scope="module")
def ingested_db(tmp_path_factory, athena_mini_dir):
    global MINI
    MINI = str(athena_mini_dir)
    db = str(tmp_path_factory.mktemp("ts83") / "m.duckdb")
    # AF3 (fixbatch): the default vocabulary list would error
    # against the mini fixture's two-vocab VOCABULARY.csv; the
    # fixture legitimately ingests just the vocabs it has.
    proc = _cli(
        "data", "ingest-athena",
        "--db", db, "--athena-dir", str(MINI),
        "--accept-licenses", "--vocabularies", "SNOMED,RxNorm",
    )
    assert proc.returncode == 0
    return db


class TestAF1MissingDB:
    """AF1 — raw IOException at the config boundary."""

    def test_af10_missing_db_actionable(self, tmp_path):
        """AF1 FIXED: actionable Error naming the path (was raw
        IOException traceback)."""
        proc = _cli(
            "data", "export-conceptmap",
            "--db", str(tmp_path / "missing.duckdb"),
            "--out", str(tmp_path / "x.json"),
        )
        assert proc.returncode == 2
        assert "database not found" in proc.stderr
        assert "missing.duckdb" in proc.stderr


class TestAF2NoAthenaTables:
    """AF2 — CatalogException without ingest."""

    def test_af20_valid_db_no_tables_actionable(self, tmp_path):
        """AF2 FIXED: 'run ingest-athena first' names the remedy
        (was raw CatalogException)."""
        import duckdb

        db = str(tmp_path / "empty.duckdb")
        duckdb.connect(db).close()
        proc = _cli(
            "data", "export-conceptmap",
            "--db", db, "--out", str(tmp_path / "x.json"),
        )
        assert proc.returncode == 2
        assert "ingest-athena" in proc.stderr


class TestAF3UnknownVocabularies:
    @pytest.fixture(autouse=True)
    def _mini(self, athena_mini_dir):
        global MINI
        MINI = str(athena_mini_dir)

    """AF3 — silent drop from --vocabularies."""

    def test_af30_ingest_bogus_vocab_loud(self, tmp_path):
        """AF3 FIXED (ingest side): unknown vocabulary_id errors
        listing the download's actual vocabularies (was silent 0
        rows exit 0)."""
        proc = _cli(
            "data", "ingest-athena",
            "--db", str(tmp_path / "b.duckdb"),
            "--athena-dir", str(MINI),
            "--accept-licenses", "--vocabularies", "SNOMED,BOGUS",
        )
        assert proc.returncode == 2
        assert "BOGUS" in proc.stderr
        assert "unknown vocabulary" in proc.stderr

    def test_af31_ingest_bogus_only_errors(self, tmp_path):
        """AF3 FIXED: BOGUS-only errors with the known list."""
        proc = _cli(
            "data", "ingest-athena",
            "--db", str(tmp_path / "b.duckdb"),
            "--athena-dir", str(MINI),
            "--accept-licenses", "--vocabularies", "BOGUS",
        )
        assert proc.returncode == 2
        assert "SNOMED" in proc.stderr  # the remedy lists the known


class TestAF4ExtensionFormMismatch:
    """AF4 — thin NDJSON content under a .json name."""

    def test_af40_form_derived_from_extension(self, ingested_db, tmp_path):
        """AF4 FIXED: auto (default) derives the form from the
        extension — .json gets monolithic (one JSON doc), .ndjson
        gets thin; explicit mismatch warns."""
        out_json = tmp_path / "x.json"
        proc = _cli(
            "data", "export-conceptmap",
            "--db", ingested_db, "--out", str(out_json),
        )
        assert proc.returncode == 0
        assert json.loads(proc.stdout)["form"] == "monolithic"
        # one JSON document, not NDJSON lines
        json.loads(out_json.read_text())
        # explicit mismatch warns:
        proc2 = _cli(
            "data", "export-conceptmap",
            "--db", ingested_db, "--out", str(tmp_path / "y.json"),
            "--form", "thin",
        )
        assert proc2.returncode == 0
        assert "Warning" in proc2.stderr


class TestTS83Controls:
    """Controls — the stage-1 contracts that hold."""

    def test_c10_license_gate_exits_2(self, tmp_path):
        proc = _cli(
            "data", "ingest-athena",
            "--db", str(tmp_path / "g.duckdb"),
            "--athena-dir", str(MINI),
        )
        assert proc.returncode == 2
        assert "accept-licenses" in proc.stderr

    def test_c11_round_trip(self, ingested_db, tmp_path):
        out = tmp_path / "rt.ndjson"
        proc = _cli(
            "data", "export-conceptmap",
            "--db", ingested_db, "--out", str(out),
        )
        assert proc.returncode == 0
        rows = [
            json.loads(line)
            for line in out.read_text().strip().splitlines()
        ]
        assert rows
        assert all(
            r["resourceType"] == "ConceptMap" for r in rows
        )

    def test_c12_vocab_filter_narrows(self, ingested_db, tmp_path):
        out_s = tmp_path / "s.ndjson"
        proc = _cli(
            "data", "export-conceptmap",
            "--db", ingested_db, "--out", str(out_s),
            "--vocabularies", "SNOMED",
        )
        assert proc.returncode == 0
        systems = {
            json.loads(line)["group"][0]["source"]
            for line in out_s.read_text().strip().splitlines()
        }
        assert systems == {"http://snomed.info/sct"}
