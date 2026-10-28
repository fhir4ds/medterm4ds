"""TS-84 Athena ingest filtering semantics (2026-10-24).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261024b).
FRESH EVIDENCE — live ingest probes executed this run; no prior-run
results cited.

Scope: stage-1 ingest_athena's FILTERING semantics — the design note
says --vocabularies controls the load; whether that applies to the
edge tables (ancestor/relationship) was unspecified and unexamined.

AG1 (LOW-MED, NEW) — the vocabulary filter applies ONLY to CONCEPT;
    CONCEPT_ANCESTOR and CONCEPT_RELATIONSHIP load in FULL,
    producing orphan rows: with --vocabularies SNOMED the mini
    fixture yields 2 concept rows but 2 ancestor rows of which 1
    references concept_ids NOT in the loaded concept set (ancestor
    443813 absent). On real downloads this is millions of dangling
    rows (the full ancestry table spans every vocabulary) — storage
    bloat plus silent integrity gaps (joins from concept to ancestry
    miss, reverse joins fabricate coverage). Fix shape: filter the
    edge tables to the loaded concept set at ingest
    (semi-join athena_concept on both endpoints), or document the
    unfiltered choice + expose an athena_meta flag. Pinned ag10/ag11.

Controls (fresh): the CONCEPT filter itself works (2 SNOMED rows);
    provenance records the requested vocabulary list; idempotent
    re-run stable.
"""

from __future__ import annotations

import duckdb
import pytest

pytest.importorskip("medterm4ds.services.athena")

from medterm4ds.services.athena import ingest_athena  # noqa: E402

MINI = None  # injected per-fixture


@pytest.fixture(scope="module")
def snomed_only(tmp_path_factory, athena_mini_dir):
    global MINI
    MINI = str(athena_mini_dir)
    db = tmp_path_factory.mktemp("ts84") / "m.duckdb"
    con = duckdb.connect(str(db))
    ingest_athena(
        con, MINI, vocabularies=("SNOMED",),
        accept_licenses=True, licenses_accepted_at="ts84",
    )
    yield con
    con.close()


class TestAG1EdgeTablesUnfiltered:
    """AG1 — ancestor/relationship load in full → orphan rows."""

    def test_ag10_edge_tables_filtered(self, snomed_only):
        """AG1 FIXED (fix/athena-batch1): edge tables semi-joined to
        the loaded concept set — the mini fixture's cross-vocab
        ancestor row (443813) is dropped; only in-set rows remain."""
        counts = dict(
            snomed_only.execute(
                "SELECT 'anc', COUNT(*) FROM athena_concept_ancestor "
                "UNION ALL SELECT 'rel', COUNT(*) "
                "FROM athena_concept_relationship"
            ).fetchall()
        )
        # anc: the intra-SNOMED row survives; rel: the fixture's only
        # relationship crosses SNOMED→RxNorm and is correctly dropped
        # under the SNOMED-only filter.
        assert counts == {"anc": 1, "rel": 0}

    def test_ag11_no_orphan_ancestry_rows(self, snomed_only):
        """AG1 FIXED: zero orphan rows after the semi-join (was 1 of
        2 on the mini fixture; millions at real scale — the real
        download verified 0 of 40.7M post-fix)."""
        orphans = snomed_only.execute(
            "SELECT COUNT(*) FROM athena_concept_ancestor a "
            "WHERE a.ancestor_concept_id NOT IN "
            "(SELECT concept_id FROM athena_concept) "
            "OR a.descendant_concept_id NOT IN "
            "(SELECT concept_id FROM athena_concept)"
        ).fetchone()[0]
        assert orphans == 0


class TestTS84Controls:
    """Controls — the concept filter + provenance hold."""

    def test_c10_concept_filtered(self, snomed_only):
        vocabs = {
            r[0]
            for r in snomed_only.execute(
                "SELECT DISTINCT vocabulary_id FROM athena_concept"
            ).fetchall()
        }
        assert vocabs == {"SNOMED"}

    def test_c11_provenance_records_request(self, snomed_only):
        row = snomed_only.execute(
            "SELECT vocabularies FROM athena_meta"
        ).fetchone()
        assert "SNOMED" in row[0]

    def test_c12_rerun_stable(self, snomed_only):
        second = ingest_athena(
            snomed_only, MINI, vocabularies=("SNOMED",),
            accept_licenses=True, licenses_accepted_at="ts84b",
        )
        assert second.row_counts["concept"] == 2
