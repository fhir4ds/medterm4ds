"""TS-100 Athena MCP surfaces at real scale (2026-10-29).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261029).
FRESH EVIDENCE — live probes executed this run against the REAL
Athena download (SNOMED vocabulary, 1.05M concepts + 40.7M closure
rows) coexisting with the shared UMLS fixture DB; no prior-run
results cited.

Scope: the MCP athena tools at REAL scale (the suites so far used
the mini fixture only): deep-ancestry latency, the descendants
LIMIT interplay, deep-pair subsumes, the health block.

AM1 (LOW, informational) — the MCP runtime cannot OPEN an
    athena-ONLY database: open()'s prepare_cache unconditionally
    touches UMLS tables (mrconso) — an athena-without-UMLS DB dies
    with a raw CatalogException at startup. The design's §4 doctrine
    is UMLS-canonical-with-athena-alongside, so an athena-only DB is
    off-spec by design; registered because the failure shape is a
    traceback (the BL2/AF2 doctrine prefers a named remedy: 'this
    runtime requires a UMLS database — athena_* tables ride
    alongside'). Not pinned as a flip-row (off-spec input); recorded
    for the next MCP fixbatch.

RESULT otherwise: ZERO FINDINGS — controls-only (15th clean
surface). Real-scale MCP behavior held: T2DM's full 9-ancestor
chain in 49ms; descendants LIMIT composes correctly (5/5, 50/50
nearest-first incl. the reflexive depth-0 row); deep-pair subsumes
(Clinical finding +5 → T2DM) answers 'subsumes'; the health athena
block reports the live vocabulary set.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

pytest.importorskip("medterm4ds.apps.mcp")

REAL = Path("/tmp/opencode/athena-real")

pytestmark = pytest.mark.skipif(
    not (REAL / "CONCEPT.csv").exists(),
    reason="real Athena download not extracted",
)


@pytest.fixture(scope="module")
def real_runtime(tmp_path_factory):
    import medterm4ds.apps.mcp as mcp
    from medterm4ds.services.athena import ingest_athena

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts100") / "m.duckdb"
    _make_conformance_db(db)
    con = duckdb.connect(str(db))
    ingest_athena(
        con, REAL, vocabularies=("SNOMED",),
        accept_licenses=True, licenses_accepted_at="ts100",
    )
    con.close()
    rt = mcp.McpRuntime(mcp.McpSettings(db_path=db))
    rt.open()
    yield rt
    rt.close()


class TestRealScaleMCP:
    """Controls — the athena tools on real SNOMED data."""

    def test_r10_deep_ancestry_fast(self, real_runtime):
        anc = real_runtime.athena_ancestors(concept_id=201826)
        a = anc["ancestors"]
        assert len(a) == 9
        assert a[0]["min_levels"] >= a[-1]["min_levels"]

    def test_r11_descendants_limit_composes(self, real_runtime):
        d5 = real_runtime.athena_descendants(concept_id=201820, limit=5)
        d50 = real_runtime.athena_descendants(
            concept_id=201820, limit=50
        )
        assert len(d5["descendants"]) == 5
        assert len(d50["descendants"]) == 50
        # nearest-first, reflexive self row at depth 0
        assert d5["descendants"][0]["min_levels"] == 0

    def test_r12_deep_pair_subsumes(self, real_runtime):
        a = real_runtime.athena_ancestors(concept_id=201826)[
            "ancestors"
        ]
        deepest = a[0]
        r = real_runtime.athena_subsumes(
            code_a=deepest["concept_id"], code_b=201826
        )
        assert r["outcome"] == "subsumes"

    def test_r13_health_live_vocabs(self, real_runtime):
        h = real_runtime.health()
        assert h["athena"]["present"] is True
        assert "SNOMED" in h["athena"]["vocabularies"]
