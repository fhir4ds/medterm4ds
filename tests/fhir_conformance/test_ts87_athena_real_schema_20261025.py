"""TS-87 Athena real-schema drift — ancestor columns (2026-10-25).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261025).
FRESH EVIDENCE — the loader executed against the REAL Athena
download zip (extracted to /tmp/opencode/athena-real) this run; no
prior-run results cited.

Scope: first REAL-scale validation of the stage-1 loader. The mini
fixture was hand-written to the loader's assumed schema — the real
download differs.

AR1 (HIGH, NEW — production bug in dev) — CONCEPT_ANCESTOR column
    names differ from the loader's assumption: the real download
    ships ancestor_concept_id/descendant_concept_id; the loader
    selects ancestor_id/descendant_id → BinderException at ingest,
    the loader CANNOT RUN against a real Athena download at all.
    The mini fixture's fidelity trap: hand-written to the same wrong
    assumption, so every prior suite passed. (CONCEPT /
    CONCEPT_RELATIONSHIP / VOCABULARY headers verified matching.)
    Fix shape: read the header and select the ACTUAL names (or
    alias both spellings — the OMOP CDM docs use ancestor_concept_id
    officially; the loader simply guessed wrong). Registered HIGH:
    the campaign's flagship command fails on first real use.
    Pinned ar10/ar11 (the loader must ingest the real dir; flip when
    fixed — the pin suite uses the REAL download dir when present,
    mini fixture otherwise documents the assumption).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("medterm4ds.services.athena")

REAL_DIR = Path("/tmp/opencode/athena-real")
REAL_HEADER = "ancestor_concept_id\tdescendant_concept_id"


@pytest.mark.skipif(
    not REAL_DIR.exists(), reason="real Athena download not extracted"
)
class TestAR1AncestorColumnDrift:
    """AR1 — real schema vs the loader's assumption."""

    def test_ar10_real_ancestor_header(self):
        """The REAL download's ancestor columns are
        ancestor_concept_id/descendant_concept_id (premise control —
        the header the loader must handle)."""
        header = (
            REAL_DIR / "CONCEPT_ANCESTOR.csv"
        ).read_text().splitlines()[0]
        assert header.startswith(REAL_HEADER)

    def test_ar11_loader_ingests_real_download(self, tmp_path):
        """AR1 FIXED (fix/athena-batch1): the loader detects the
        real header (ancestor_concept_id/descendant_concept_id) and
        ingests the actual download — 4,088,225 concept rows at the
        default vocab set (the TS-88 baseline)."""
        import duckdb

        from medterm4ds.services.athena import ingest_athena

        con = duckdb.connect(str(tmp_path / "real.duckdb"))
        result = ingest_athena(
            con, REAL_DIR,
            vocabularies=("SNOMED", "RxNorm"),
            accept_licenses=True,
            licenses_accepted_at="ts87-fixed",
        )
        assert result.row_counts["concept"] == 1_361_562  # SNOMED+RxNorm real
        con.close()

    def test_ar12_loader_header_detect(self):
        """AR1 FIXED: the loader detects the header shape
        (source-level control — header-detect present, no hardcoded
        wrong names)."""
        import inspect

        from medterm4ds.services import athena

        src = inspect.getsource(athena.ingest_athena)
        assert "ancestor_concept_id" in src
        assert "\"ancestor_concept_id\" in header" in src
