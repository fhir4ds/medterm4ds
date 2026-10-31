"""TS-108 data verify on athena-coexisting DBs + verdict edges (2026-10-31).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261031c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the ``data verify`` deploy gate (verify_duckdb, QC-441/451)
against the ATHENA coexistence shape — every verify pin predates the
athena campaign; none probed a DB carrying athena_* tables alongside
mrconso/mrrel. The deploy-gate questions:
  V1. athena_* tables appear in ``tables`` and do NOT flip
      ``has_required_tables`` or the ``ok`` verdict.
  V2. the athena staging residue (``athena_concept_staging`` left by
      an interrupted swap) is visible in ``tables`` but still does
      not fail verification.
  V3. source_counts still count ONLY UMLS SABs (athena vocabularies
      must not leak into UMLS source_counts — SNOMED the SAB vs
      SNOMED the athena vocabulary are different stores).
  V4. unknown source names remain a loud client error (the
      _normalize_sources guard's Python-API symmetry — sources with
      a bogus name raise, not zero-count silently).
  V5. the QC-451 verdict symmetry: the SAME DB verifies ok=True
      through the CLI subprocess (exit 0) and the Python API.

Controls: plain fixture DB ok=True (c1); mrrel-less DB ok=False (c2).

RESULT: TWO FINDINGS, both pinned as strict-xfail flip-rows:
  VV1 (MEDIUM) — verify_duckdb's Python API silently accepts unknown
  source names (CLI validates; API returns ok with zero counts) —
  the QC-451 'gate is a no-op' shape on the validation axis.
  VV2 (MEDIUM) — a MALFORMED mrconso (missing column) crashes with a
  raw BinderException instead of ok:false; QC-441 covered the
  missing-TABLE shape only.
Controls PASS fresh: athena tables + staging residue do not flip the
verdict; source_counts stay UMLS-only (no athena vocab leakage); CLI
vs API verdict symmetric (console script); plain ok / missing-table
not-ok both hold.
"""

from __future__ import annotations

import duckdb
import pytest

pytest.importorskip("medterm4ds.services.data_setup")

SNOMED_SAB = "SNOMEDCT_US"


@pytest.fixture(scope="module")
def athena_codb(tmp_path_factory, athena_mini_dir):
    """Conformance fixture DB + mini athena ingested + staging residue."""
    from medterm4ds.services.athena import ingest_athena

    from .conftest import _make_conformance_db

    d = tmp_path_factory.mktemp("ts108")
    db = d / "m.duckdb"
    _make_conformance_db(db)
    con = duckdb.connect(str(db))
    ingest_athena(
        con,
        str(athena_mini_dir),
        vocabularies=("SNOMED", "RxNorm"),
        accept_licenses=True,
        licenses_accepted_at="ts108",
    )
    # simulate an interrupted staging swap: leave a staging table behind
    con.execute("CREATE TABLE athena_concept_staging AS SELECT * FROM athena_concept LIMIT 1")
    con.close()
    return db


def test_v1_athena_tables_do_not_flip_verdict(athena_codb):
    from medterm4ds.services.data_setup import verify_duckdb

    r = verify_duckdb(athena_codb)
    assert "athena_concept" in r["tables"]
    assert r["has_required_tables"] is True
    assert r["ok"] is True, f"athena tables must not fail verification: {r}"


def test_v2_staging_residue_visible_ok(athena_codb):
    from medterm4ds.services.data_setup import verify_duckdb

    r = verify_duckdb(athena_codb)
    assert "athena_concept_staging" in r["tables"]
    assert r["ok"] is True, "staging residue is not a verification failure"


def test_v3_source_counts_umls_only(athena_codb):
    from medterm4ds.services.data_setup import verify_duckdb

    r = verify_duckdb(athena_codb)
    counts = r["source_counts"]
    assert SNOMED_SAB in counts, "UMLS SAB must still be counted"
    # athena vocabulary ids ('SNOMED', 'RxNorm') must NOT appear as keys
    for athena_vocab in ("SNOMED", "RxNorm"):
        assert athena_vocab not in counts, (
            f"athena vocabulary {athena_vocab} leaked into UMLS source_counts"
        )


@pytest.mark.xfail(
    strict=True,
    reason="VV1 pinned: Python API accepts unknown source names silently — flip when fixed",
)
def test_v4_unknown_source_loud(athena_codb):
    """V4 FINDING (VV1, MEDIUM — pinned flip-row, xfail until fix):
    the Python API silently accepts unknown source names. The CLI
    guard (_normalize_sources_or_exit) validates names; verify_duckdb
    does NOT — sources=['NOT_A_REAL_SOURCE'] returns ok with an empty
    source_counts (a typo'd deploy gate reads green with zero codes
    counted — the exact QC-451 'gate is a no-op' shape, on the
    validation axis). Same API/CLI asymmetry class as QC-446/QC-453.
    Flip when verify_duckdb validates source names (or documents the
    pass-through contract)."""
    from medterm4ds.services.data_setup import verify_duckdb

    with pytest.raises((ValueError, KeyError)):
        verify_duckdb(athena_codb, sources=["NOT_A_REAL_SOURCE"])


def test_v5_cli_api_verdict_symmetry(athena_codb, tmp_path):
    from medterm4ds.services.data_setup import verify_duckdb

    api_ok = verify_duckdb(athena_codb)["ok"]
    import subprocess
    from pathlib import Path

    cli = Path("/mnt/d/medterm4ds/.venv/bin/medterm4ds")
    pytest.mark.skipif(not cli.exists(), reason="console script not installed")
    r = subprocess.run(
        [str(cli), "data", "verify", "--db", str(athena_codb)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert (r.returncode == 0) == api_ok, (
        f"CLI exit {r.returncode} != API ok={api_ok}; stderr={r.stderr[-300:]}"
    )


def test_c1_plain_db_ok(tmp_path_factory):
    from medterm4ds.services.data_setup import verify_duckdb

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts108_plain") / "m.duckdb"
    _make_conformance_db(db)
    r = verify_duckdb(db)
    assert r["ok"] is True


@pytest.mark.xfail(
    strict=True,
    reason="VV2 pinned: malformed mrconso (missing column) raises BinderException instead of ok:false — flip when fixed",
)
def test_c2_missing_mrrel_not_ok(tmp_path_factory):
    """C2 FINDING (VV2, MEDIUM — pinned flip-row): a MALFORMED mrconso
    (table present, required column absent) crashes verify_duckdb with
    a raw duckdb BinderException instead of returning ok:false.
    QC-441 guarded the missing-TABLE shape; the missing-COLUMN shape
    (a truncated/partial build) still escapes the verdict contract —
    `verify && deploy` dies with a traceback rather than a clean
    gate failure. Flip when the source-count query failure becomes
    has_required_tables:false (or a structured not-ok report)."""
    from medterm4ds.services.data_setup import verify_duckdb

    d = tmp_path_factory.mktemp("ts108_bad")
    db = d / "m.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE mrconso (CODE VARCHAR)")
    con.close()
    r = verify_duckdb(db)
    assert r["ok"] is False
