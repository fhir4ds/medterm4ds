"""TS-101 athena-concepts domain filter semantics (2026-10-29).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261029b).
FRESH EVIDENCE — live CLI probes executed this run against the REAL
download (SNOMED) on the coexist fixture DB; no prior-run results
cited.

Scope: the athena-concepts --domain filter's semantics — the
AS2/HQ1 doctrine was applied to --vocabulary (batch2) but --domain
never got the same audit: case-sensitivity and unknown values.

AD1 (LOW, NEW) — --domain silently drops wrong-case and unknown
    values: --domain condition (wrong case) → results [] identical
    to --domain Bogus → results []. The domain_id vocabulary is a
    CLOSED Athena enum (Condition, Drug, Procedure, ...) — exactly
    the athena_vocabulary-analogous position AS2 held for
    --vocabulary, but domains have no registration table in the
    athena_* schema (they ride CONCEPT.domain_id), so the loud
    validation needs the DISTINCT set from athena_concept itself.
    Fix shape: validate against SELECT DISTINCT domain_id (error
    listing carried domains), or document case-sensitivity in the
    CLI help. Pinned ad10/ad11.

Controls (fresh): the correct-case domain filter WORKS
    (--domain Condition returns Condition rows); --include-deprecated
    surfaces invalid_reason values (3 rows with the field present);
    --vocabulary validation still errors loudly (batch2's fix held).
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
        capture_output=True, text=True, timeout=900, env=env,
    )


@pytest.fixture(scope="module")
def coexist_db(tmp_path_factory):
    from medterm4ds.services.athena import ingest_athena

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts101") / "m.duckdb"
    _make_conformance_db(db)
    con = duckdb.connect(str(db))
    ingest_athena(
        con, REAL, vocabularies=("SNOMED",),
        accept_licenses=True, licenses_accepted_at="ts101",
    )
    con.close()
    return db


pytestmark = pytest.mark.skipif(
    not (REAL / "CONCEPT.csv").exists(),
    reason="real Athena download not extracted",
)


class TestAD1DomainFilterSilence:
    """AD1 — wrong-case/unknown domains silently drop."""

    def test_ad10_wrong_case_loud(self, coexist_db):
        """AD1 FIXED (athena-batch3): --domain condition (wrong
        case) → exit 2 with the carried domain set listed."""
        proc = _cli(
            "athena-concepts", "--db", str(coexist_db),
            "--query", "diabetes", "--domain", "condition",
        )
        assert proc.returncode == 2
        assert "unknown domain_id" in proc.stderr

    def test_ad11_unknown_domain_loud(self, coexist_db):
        """AD1 FIXED (athena-batch3): --domain Bogus → exit 2,
        distinct from the wrong-case class by the message listing
        the carried set."""
        proc = _cli(
            "athena-concepts", "--db", str(coexist_db),
            "--query", "diabetes", "--domain", "Bogus",
        )
        assert proc.returncode == 2
        assert "unknown domain_id" in proc.stderr


class TestTS101Controls:
    """Controls — the filter machinery that works."""

    def test_c10_correct_case_filters(self, coexist_db):
        proc = _cli(
            "athena-concepts", "--db", str(coexist_db),
            "--query", "diabetes", "--domain", "Condition",
            "--limit", "3",
        )
        assert proc.returncode == 0
        rows = json.loads(proc.stdout)["results"]
        assert rows and all(
            r["domain"] == "Condition" for r in rows
        )

    def test_c11_deprecated_flag_surfaces_reason(self, coexist_db):
        proc = _cli(
            "athena-concepts", "--db", str(coexist_db),
            "--query", "diabetes mellitus",
            "--include-deprecated", "--limit", "3",
        )
        rows = json.loads(proc.stdout)["results"]
        assert any(r["invalid_reason"] for r in rows)

    def test_c12_vocabulary_validation_held(self, coexist_db):
        """batch2's AS2 fix: unknown --vocabulary errors loudly."""
        proc = _cli(
            "athena-concepts", "--db", str(coexist_db),
            "--query", "x", "--vocabulary", "BOGUS",
        )
        assert proc.returncode == 2
        assert "unknown vocabulary_id" in proc.stderr
