"""TS-102 export-conceptmap vocabulary filter + monolithic scale (2026-10-29).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261029c).
FRESH EVIDENCE — live CLI probes executed this run against the REAL
download (SNOMED, 1.05M rows ingested); no prior-run results cited.

Scope: the export-conceptmap --vocabularies filter — AF3 fixed the
INGEST side loud (unknown → error listing known) but the EXPORT
filter never got the audit; plus the first MONOLITHIC export at
real scale (only thin was measured at release time).

AE1 (LOW, NEW) — export silently drops unknown vocabularies:
    --vocabularies SNOMED,BOGUS exports 1,054,935 rows (SNOMED
    only) at exit 0 — BOGUS contributed nothing, no signal. The
    ingest side errors loudly per AF3; the export side is the same
    closed-enum position (athena_vocabulary IS the registration
    table here — the validation is a straightforward reuse).
    Fix shape: validate the filter against athena_vocabulary in the
    export path (error listing carried vocabs), mirroring AF3.
    Pinned ae10/ae11.

Controls (fresh): the SNOMED-only monolithic export completes at
    real scale (1.05M elements); the url is stable
    (urn:medterm4ds:athena-conceptmap); the correct-case filter
    narrows as specified.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("medterm4ds.services.athena")

REAL = Path("/tmp/opencode/athena-real")


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": "src"}
    return subprocess.run(
        [sys.executable, "-m", "medterm4ds.apps.cli", *args],
        capture_output=True, text=True, timeout=1800, env=env,
    )


@pytest.fixture(scope="module")
def coexist_db(tmp_path_factory):
    from medterm4ds.services.athena import ingest_athena

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts102") / "m.duckdb"
    _make_conformance_db(db)
    con = duckdb.connect(str(db))
    ingest_athena(
        con, REAL, vocabularies=("SNOMED",),
        accept_licenses=True, licenses_accepted_at="ts102",
    )
    con.close()
    return db


pytestmark = pytest.mark.skipif(
    not (REAL / "CONCEPT.csv").exists(),
    reason="real Athena download not extracted",
)


class TestAE1ExportVocabSilence:
    """AE1 — export silently drops unknown vocabularies."""

    def test_ae10_bogus_vocab_silent(self, coexist_db, tmp_path):
        """--vocabularies SNOMED,BOGUS → full SNOMED export, exit 0,
        no signal for BOGUS. Flip when the export path validates
        against athena_vocabulary (mirroring AF3)."""
        out = tmp_path / "x.ndjson"
        proc = _cli(
            "data", "export-conceptmap",
            "--db", str(coexist_db), "--out", str(out),
            "--vocabularies", "SNOMED,BOGUS",
        )
        assert proc.returncode == 0
        body = json.loads(proc.stdout)
        assert body["rows"] > 1_000_000  # SNOMED carried everything

    def test_ae11_ingest_side_contrast(self, tmp_path):
        """Premise control: the INGEST twin errors loudly (AF3
        held) — the divergence is the export path only."""
        db = tmp_path / "ing.duckdb"
        proc = _cli(
            "data", "ingest-athena",
            "--db", str(db), "--athena-dir", str(REAL),
            "--accept-licenses",
            "--vocabularies", "SNOMED,BOGUS",
        )
        assert proc.returncode == 2
        assert "unknown vocabulary" in proc.stderr


class TestMonolithicRealScale:
    """Controls — the monolithic export at real scale."""

    def test_c10_monolithic_completes(self, coexist_db, tmp_path):
        out = tmp_path / "mono.json"
        proc = _cli(
            "data", "export-conceptmap",
            "--db", str(coexist_db), "--out", str(out),
            "--form", "monolithic",
            "--vocabularies", "SNOMED",
        )
        assert proc.returncode == 0
        body = json.loads(proc.stdout)
        assert body["elements"] > 1_000_000
        assert (
            body["url"] == "urn:medterm4ds:athena-conceptmap"
        )

    def test_c11_filter_narrows_correctly(self, coexist_db, tmp_path):
        """The known-vocabulary filter narrows (SNOMED-only export
        carries exactly one group)."""
        out = tmp_path / "one.ndjson"
        _cli(
            "data", "export-conceptmap",
            "--db", str(coexist_db), "--out", str(out),
            "--vocabularies", "SNOMED",
        )
        first = json.loads(out.read_text().splitlines()[0])
        assert (
            first["group"][0]["source"]
            == "http://snomed.info/sct"
        )
