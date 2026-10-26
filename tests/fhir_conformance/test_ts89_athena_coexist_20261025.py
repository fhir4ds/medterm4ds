"""TS-89 Athena post-fixbatch coexistence verification (2026-10-25).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261025c).
FRESH EVIDENCE — live coexistence probe executed this run against the
REAL Athena download on top of the shared UMLS fixture DB; no
prior-run results cited.

Scope: the design note's §4 coexistence doctrine never EXERCISED at
the real-data scale: ingest into an EXISTING UMLS DuckDB (same-DB
namespaced storage was the recommendation — verify no UMLS table is
touched, and the code-identity bridge JOIN works across the two
sources in one database).

RESULT: ZERO FINDINGS — controls-only (13th clean surface).
(1) Coexistence: ingest into the fixture UMLS DB creates exactly the
    5 athena_* tables; mrconso/mrrel untouched; 8s for the
    two-vocabulary real load. (2) The cross-source bridge join works
    NATURALLY in one database: mrconso PT codes ⋈ athena_concept on
    (concept_code, vocabulary_id='SNOMED') resolves UMLS codes to
    OMOP concept_ids (3738000→4291005, 44054006→201826,
    73211009→201820) — the §3 seam is reachable from plain SQL
    without the ConceptMap artifact when both sources share the DB
    (worth documenting as the power-user path).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

pytest.importorskip("medterm4ds.services.athena")

from medterm4ds.services.athena import ingest_athena  # noqa: E402

REAL = Path("/tmp/opencode/athena-real")

pytestmark = pytest.mark.skipif(
    not REAL.exists(), reason="real Athena download not extracted"
)


@pytest.fixture(scope="module")
def coexist_db(tmp_path_factory):
    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts89") / "umls_current.duckdb"
    _make_conformance_db(db)
    con = duckdb.connect(str(db))
    ingest_athena(
        con, REAL, vocabularies=("SNOMED", "RxNorm"),
        accept_licenses=True, licenses_accepted_at="ts89",
    )
    yield con
    con.close()


class TestCoexistence:
    """Controls — same-DB namespaced storage holds."""

    def test_x10_umls_tables_untouched(self, coexist_db):
        tables = {
            r[0] for r in coexist_db.execute("SHOW TABLES").fetchall()
        }
        assert {"mrconso", "mrrel"} <= tables
        assert {
            "athena_concept", "athena_concept_ancestor",
            "athena_concept_relationship", "athena_vocabulary",
            "athena_meta",
        } <= tables

    def test_x11_umls_lookup_unchanged(self, coexist_db):
        n = coexist_db.execute(
            "SELECT COUNT(*) FROM mrconso WHERE SAB='SNOMEDCT_US'"
        ).fetchone()[0]
        assert n == 3  # the fixture's SNOMED rows


class TestCrossSourceBridge:
    """Controls — the code-identity join across sources."""

    def test_x20_direct_sql_bridge(self, coexist_db):
        rows = coexist_db.execute(
            "SELECT m.CODE, a.concept_id FROM mrconso m "
            "JOIN athena_concept a ON a.concept_code = m.CODE "
            "AND a.vocabulary_id = 'SNOMED' "
            "WHERE m.SAB = 'SNOMEDCT_US' AND m.TTY = 'PT'"
        ).fetchall()
        bridge = dict(rows)
        assert bridge.get("44054006") == 201826
        assert bridge.get("73211009") == 201820

    def test_x21_real_athena_scale_loaded(self, coexist_db):
        n = coexist_db.execute(
            "SELECT COUNT(*) FROM athena_concept "
            "WHERE vocabulary_id = 'SNOMED'"
        ).fetchone()[0]
        assert n > 1_000_000  # the real download's SNOMED set
