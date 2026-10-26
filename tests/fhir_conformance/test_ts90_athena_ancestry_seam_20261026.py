"""TS-90 Athena ancestry seam survey (stage-2 pre-work) (2026-10-26).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261026).
FRESH EVIDENCE — live ancestry queries over the REAL download this
run; no prior-run results cited.

Scope: findings-only pre-work for Athena stage 2 — what the
CONCEPT_ANCESTOR transitive-closure table ACTUALLY gives the query
surfaces, measured on real data (the design's exact-total claim).

AN1 (LOW, informational — stage-2 design input) — the closure table
    answers the VS02-01 family EXACTLY and CHEAPLY: T2DM
    (concept_id 201826) has exactly 9 ancestors with per-pair
    min/max separation depths (1..6) — where the UMLS BFS walk
    returns approximate totals under caps (VS02-01 HIGH, open since
    the sweep), the Athena path is `SELECT COUNT(*)` on a
    precomputed closure: exact, no cap, one indexed scan. The
    hierarchy surface for Athena-sourced codes should read
    CONCEPT_ANCESTOR directly (stage 2's plan) and this pin records
    the real-data semantics: exact totals + multi-path min/max
    depths (e.g. Clinical finding is +5-6 — multiple IS-A paths,
    faithfully distinguished). Pinned an10/an11.

Controls (fresh): the closure is reflexive (self-row depth 0);
    Diabetes mellitus (201820) is the +1 parent — the fixture-matched
    hierarchy shape.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

REAL = Path("/tmp/opencode/athena-real")

pytestmark = pytest.mark.skipif(
    not (REAL / "CONCEPT_ANCESTOR.csv").exists(),
    reason="real Athena download not extracted",
)

T2DM_ID = "201826"
DM_ID = "201820"


@pytest.fixture(scope="module")
def anc():
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW anc AS SELECT * FROM read_csv("
        f"'{REAL}/CONCEPT_ANCESTOR.csv', header=true, delim='\\t', "
        f"quote='', all_varchar=true)"
    )
    yield con
    con.close()


class TestAN1ExactAncestry:
    """AN1 — exact totals + multi-path depths on real data."""

    def test_an10_exact_ancestor_count(self, anc):
        n = anc.execute(
            f"SELECT COUNT(*) FROM anc "
            f"WHERE descendant_concept_id = '{T2DM_ID}'"
        ).fetchone()[0]
        assert n == 9

    def test_an11_multipath_depths(self, anc):
        rows = {
            r[0]: (int(r[1]), int(r[2]))
            for r in anc.execute(
                f"SELECT ancestor_concept_id, "
                f"min_levels_of_separation, "
                f"max_levels_of_separation FROM anc "
                f"WHERE descendant_concept_id = '{T2DM_ID}'"
            ).fetchall()
        }
        # multi-path ancestor: min != max
        assert any(
            mn != mx for mn, mx in rows.values()
        )
        # the +1 parent is Diabetes mellitus
        assert (1, 1) == rows[DM_ID]

    def test_an12_reflexive_self_row(self, anc):
        row = anc.execute(
            f"SELECT min_levels_of_separation, "
            f"max_levels_of_separation FROM anc "
            f"WHERE descendant_concept_id = '{T2DM_ID}' "
            f"AND ancestor_concept_id = '{T2DM_ID}'"
        ).fetchone()
        assert row == ("0", "0")
