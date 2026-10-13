"""TS-37 fixture-fidelity audit (2026-10-13).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261013b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: where do the 40 maintenance suites actually point? The shared
conformance fixture carries mrconso+mrrel only — the RAW-path engine.
The production deployment default after ``medterm4ds data prepare`` is
the PREPARED schema (walk_edges/best_atoms/friendly_atoms/…), and
``get_source_code_relations`` dispatches to DIFFERENT SQL when those
tables exist. This audit pins what the fixture can and cannot reach.

FX1 (LOW-MED, NEW) — surface contracts pinned raw-path only.
    Every open finding and every maintenance control (TS-01..TS-36,
    7528 tests) exercises the raw-path engine: the fixture has no
    prepared tables, so the engine's prepared dispatch
    (get_source_code_relations_prepared via the walk_edges gate) never
    runs under the FHIR/REST/MCP surfaces. Unit-level prepared tests
    exist (test_crosswalk_prepared, test_find_via_walk,
    test_patient_friendly_prepared) at the SERVICE layer — but the
    surface contracts (status codes, envelopes, miss shapes) are not
    validated against prepared SQL. A prepared-path regression in
    surface semantics would pass the entire conformance suite.
    Fix shape: a prepare-capable conformance fixture (full-vocab
    synthetic seed) or a dual-path smoke marker on the core surfaces.
    Pinned: f10 (fixture = raw tables only), f11 (prepared tables
    ABSENT on the fixture), f12 (unit-layer prepared coverage exists
    — the premise that the gap is surface-level, not total).

FX2 (LOW, NEW) — the fixture cannot reach prepared paths at all.
    prepare_derived_tables on the synthetic DB builds only 2 of the
    prepared tables (snomed_top_level_depth, cvx_metadata) — the
    vocabulary coverage needed for walk_edges/best_atoms is absent,
    so even deliberately-preparing fixtures cannot exercise the
    hierarchy prepared path without a fuller seed. Documented here as
    the structural constraint behind FX1.
    Pinned: f20/f21.

Controls (fresh): prepare_derived_tables runs clean on the synthetic
    DB (2 tables, statuses recorded); the raw-path fallback WARNING
    contract (missing patient-friendly tables → named list + remedy)
    fires on the fixture and names the exact tables; the env-pin
    divergences (MEDTERM4DS_DISABLE_CVX_GROUPS=1, MEDTERM4DS_DEVICE=
    cpu) are deliberate, commented, and documented in code.
"""

from __future__ import annotations

import duckdb
import pytest

PREPARED_TABLES = (
    "walk_edges", "best_atoms", "friendly_atoms",
    "patient_friendly_strategy", "snomed_top_level_depth",
    "cvx_metadata",
)


@pytest.fixture(scope="module")
def raw_db(tmp_path_factory):
    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts37") / "umls.duckdb"
    _make_conformance_db(db)
    return db


class TestFX1RawPathOnly:
    """FX1 — surface suites pin the raw-path engine."""

    def test_f10_fixture_raw_tables(self, raw_db):
        """The shared fixture carries exactly mrconso+mrrel (the
        raw-path engine's inputs). Flip only when a prepare-capable
        fixture lands (then FX1 closes)."""
        con = duckdb.connect(str(raw_db), read_only=True)
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        finally:
            con.close()
        assert tables == {"mrconso", "mrrel"}

    def test_f11_prepared_tables_absent(self, raw_db):
        """None of the prepared-path tables exist on the fixture —
        the engine's prepared dispatch is unreachable in every
        surface suite."""
        con = duckdb.connect(str(raw_db), read_only=True)
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        finally:
            con.close()
        assert not (set(PREPARED_TABLES) & tables)

    def test_f12_unit_layer_prepared_coverage_exists(self):
        """Premise control: prepared paths ARE tested — at the unit
        layer (service/engine level), not the surface layer. FX1 is
        the surface gap, not a total gap."""
        from pathlib import Path

        unit_suites = (
            "test_crosswalk_prepared.py",
            "test_find_via_walk.py",
            "test_patient_friendly_prepared.py",
        )
        for name in unit_suites:
            assert (
                Path("tests") / name
            ).exists(), name


class TestFX2FixtureCannotPrepare:
    """FX2 — structural constraint behind FX1."""

    def test_f20_partial_prepare(self, raw_db):
        """prepare_derived_tables succeeds but builds only 2 prepared
        tables on the synthetic seed (no walk_edges/best_atoms — the
        vocab coverage is absent)."""
        from medterm4ds.services.data_setup import (
            prepare_derived_tables,
        )

        con = duckdb.connect(str(raw_db))
        try:
            stats = prepare_derived_tables(con)
            tables = {
                r[0] for r in con.execute("SHOW TABLES").fetchall()
            }
        finally:
            con.close()
        built = set(stats) & set(tables)
        assert built == {
            "snomed_top_level_depth", "cvx_metadata",
        }

    def test_f21_walk_edges_unbuildable(self, raw_db):
        """Even post-prepare, walk_edges remains absent — the
        hierarchy prepared path cannot be exercised from this
        fixture regardless of intent."""
        from medterm4ds.services.data_setup import (
            prepare_derived_tables,
        )

        con = duckdb.connect(str(raw_db))
        try:
            prepare_derived_tables(con)
            tables = {
                r[0] for r in con.execute("SHOW TABLES").fetchall()
            }
        finally:
            con.close()
        assert "walk_edges" not in tables


class TestAuditControls:
    """Controls — fallback warning + env-pin documentation."""

    def test_c10_fallback_warning_names_tables(self, raw_db, caplog):
        """The raw-path fallback warning (a logging.WARNING, once per
        distinct missing-table set) names the EXACT missing prepared
        tables and the remedy — the documented contract that makes
        raw-path serving legible."""
        import logging

        from medterm4ds.engines.duckdb.engine import LocalDuckDBEngine
        from medterm4ds.services.patient_friendly import (
            get_patient_friendly_names,
        )

        con = duckdb.connect(str(raw_db), read_only=True)
        try:
            engine = LocalDuckDBEngine(con)
            with caplog.at_level(logging.WARNING, logger="medterm4ds"):
                get_patient_friendly_names(
                    [("SNOMEDCT_US", "44054006")], engine=engine
                )
            text = caplog.text
            assert "friendly_atoms" in text
            assert "prepare-derived" in text
        finally:
            con.close()

    def test_c11_env_pins_documented_in_code(self):
        """MEDTERM4DS_DISABLE_CVX_GROUPS documented at its use site;
        MEDTERM4DS_DEVICE in core/env.py — deliberate divergences,
        commented."""
        from pathlib import Path

        base = (
            Path("src/medterm4ds/engines/duckdb/_engine_base.py")
            .read_text()
        )
        assert "MEDTERM4DS_DISABLE_CVX_GROUPS" in base
        env_py = Path("src/medterm4ds/core/env.py").read_text()
        assert "MEDTERM4DS_DEVICE" in env_py
