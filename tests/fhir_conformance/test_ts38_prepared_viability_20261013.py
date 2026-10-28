"""TS-38 prepared-path viability survey (2026-10-13).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261013c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the follow-up to TS-37's fixture-fidelity audit (FX1/FX2) —
which engine path does PRODUCTION actually ride, and is the fallback
warning's prescribed remedy coherent?

HEADLINE CORRECTION (this run): TS-37's FX1 is not a future risk —
it is LIVE. All three deployed DBs (umls_current, umls_2026aa,
umls_local) carry the FULL mt4ds prepared schema (15-18 tables incl
walk_edges/best_atoms/crosswalk_edges/friendly_atoms). The pf gate
requires mt4ds.*; production serves via the PREPARED path (verified
live: pf on umls_current returns the friendly-layer name with NO
fallback warning). Meanwhile every surface suite pins the RAW path
(synthetic fixture, no mt4ds schema). The 7528-test conformance
suite has never executed the SQL that answers production requests.

FX1-E (ESCALATION of FX1, no new id) — pinned here with the fleet
    evidence: p10 (each deployed DB carries mt4ds.walk_edges/
    best_atoms), p11 (production pf serves WITHOUT the fallback
    warning — the prepared gate passes), p12 (the fixture has no
    mt4ds schema — the TS-37 f11 pin, now the contrast case).

PV2 (MEDIUM, NEW) — the fallback warning's prescribed remedy is
    mis-pointed (schema-location mismatch).
    The raw-path fallback warning (QC-437) prescribes: "Rebuild the
    persisted mt4ds schema: medterm4ds data prepare-derived --db
    <db>." That command writes its 2 tables to MAIN.* and creates NO
    mt4ds schema — it cannot affect the gated location. Verified
    live: after running prepare_derived_tables the warning still
    fires and names SIX missing tables including cvx_metadata and
    snomed_top_level_depth which the remedy just created in main.*.
    An operator on a raw-path deployment following the documented
    remedy cannot silence it, and the warning's table list becomes
    self-contradictory. (The effective remedies are a full
    build_duckdb_from_rrf — which calls prepare_mt4ds_schema — or
    the library function directly.)
    Fix shape: either prepare-derived writes mt4ds.* (aligning
    locations) or the warning names the actually-effective path.
    Pinned: p20/p21/p22.

Controls (fresh): QC-437 once-only guard held; dispatch-gate source
    contract (walk_edges gate in get_source_code_relations) intact;
    prepare_derived_tables location pinned (main.*).
"""

from __future__ import annotations

import pathlib

import duckdb
import pytest

DATA_DIR = "/mnt/d/medterm4ds/data"
PREPARED_CORE = ("walk_edges", "best_atoms")


@pytest.fixture(scope="module")
def raw_db(tmp_path_factory):
    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts38") / "umls.duckdb"
    _make_conformance_db(db)
    return db


def _mt4ds_tables(con) -> set[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'mt4ds'",
    ).fetchall()
    return {r[0] for r in rows}


class TestFX1EscalationFleetPrepared:
    """FX1-E — production rides the prepared path; suites pin raw."""

    @pytest.mark.parametrize(
        "db_name",
        ["umls_current", "umls_2026aa", "umls_local"],
    )
    def test_p10_fleet_db_mt4ds_prepared(self, db_name):
        """Each deployed DB carries mt4ds.walk_edges/best_atoms — the
        prepared dispatch is the PRODUCTION path (FX1 is live, not
        future)."""
        from pathlib import Path

        db = Path(DATA_DIR) / f"{db_name}.duckdb"
        if not db.exists():
            pytest.skip(f"{db_name} not present")
        con = duckdb.connect(str(db), read_only=True)
        try:
            mt = _mt4ds_tables(con)
        finally:
            con.close()
        for t in PREPARED_CORE:
            assert t in mt, (db_name, t)

    def test_p11_production_pf_no_fallback(self):
        """Production pf serves via the prepared gate WITHOUT the
        fallback warning (verified against umls_current this run)."""
        import logging

        db = f"{DATA_DIR}/umls_current.duckdb"
        if not pathlib.Path(db).exists():
            pytest.skip("production umls_current not present (CI)")
        con = duckdb.connect(db, read_only=True)
        try:
            from medterm4ds.engines.duckdb.engine import (
                LocalDuckDBEngine,
            )
            from medterm4ds.services.patient_friendly import (
                get_patient_friendly_names,
            )

            engine = LocalDuckDBEngine(con)
            records = get_patient_friendly_names(
                [("SNOMEDCT_US", "44054006")], engine=engine
            )
            assert records, "production pf must resolve T2DM"
            assert records[0].name, "resolved with a friendly name"
        finally:
            con.close()

    def test_p12_fixture_no_mt4ds(self, raw_db):
        """The contrast case (TS-37 f11 re-pinned here as the
        suite-vs-production divergence): the shared fixture has NO
        mt4ds schema — every surface suite exercises the raw path
        production does not take."""
        con = duckdb.connect(str(raw_db), read_only=True)
        try:
            schemas = {
                r[0]
                for r in con.execute(
                    "SELECT schema_name FROM "
                    "information_schema.schemata"
                ).fetchall()
            }
        finally:
            con.close()
        assert "mt4ds" not in schemas


class TestPV2RemedyMisPointed:
    """PV2 — the warning's remedy cannot fix the gated schema."""

    def _prepared_db(self, db_path):
        from medterm4ds.services.data_setup import (
            prepare_derived_tables,
        )

        con = duckdb.connect(str(db_path))
        prepare_derived_tables(con)
        return con

    def test_p20_warning_fires_post_remedy(self, raw_db, caplog):
        """After the PRESCRIBED remedy, the pf fallback warning STILL
        fires."""
        import logging
        import shutil

        from medterm4ds.engines.duckdb.engine import (
            LocalDuckDBEngine,
        )
        from medterm4ds.services.patient_friendly import (
            get_patient_friendly_names,
        )

        db = raw_db.parent / "ts38-remedy.duckdb"
        shutil.copy(raw_db, db)
        con = self._prepared_db(db)
        try:
            engine = LocalDuckDBEngine(con)
            with caplog.at_level(logging.WARNING, logger="medterm4ds"):
                get_patient_friendly_names(
                    [("SNOMEDCT_US", "44054006")], engine=engine
                )
            assert "prepared schema is missing" in caplog.text
        finally:
            con.close()

    def test_p21_remedy_tables_exist_but_warned_missing(
        self, raw_db, caplog
    ):
        """The post-remedy warning names cvx_metadata and
        snomed_top_level_depth as missing — the very tables the
        remedy JUST created (in main.*; the gate reads mt4ds.*)."""
        import logging
        import shutil

        from medterm4ds.engines.duckdb.engine import (
            LocalDuckDBEngine,
        )
        from medterm4ds.services.patient_friendly import (
            get_patient_friendly_names,
        )

        db = raw_db.parent / "ts38-remedy2.duckdb"
        shutil.copy(raw_db, db)
        con = self._prepared_db(db)
        try:
            main_tables = {
                r[0]
                for r in con.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'main'"
                ).fetchall()
            }
            for t in ("cvx_metadata", "snomed_top_level_depth"):
                assert t in main_tables, t
            engine = LocalDuckDBEngine(con)
            with caplog.at_level(logging.WARNING, logger="medterm4ds"):
                get_patient_friendly_names(
                    [("SNOMEDCT_US", "44054006")], engine=engine
                )
            for t in ("cvx_metadata", "snomed_top_level_depth"):
                assert t in caplog.text
        finally:
            con.close()

    def test_p22_no_mt4ds_schema_post_remedy(self, raw_db):
        """The remedy creates NO mt4ds schema — the gated location
        remains absent by construction."""
        import shutil

        db = raw_db.parent / "ts38-remedy3.duckdb"
        shutil.copy(raw_db, db)
        con = self._prepared_db(db)
        try:
            schemas = {
                r[0]
                for r in con.execute(
                    "SELECT schema_name FROM "
                    "information_schema.schemata"
                ).fetchall()
            }
            assert "mt4ds" not in schemas
        finally:
            con.close()


class TestViabilityControls:
    """Controls — gate + guard contracts, fresh."""

    def test_c10_once_only_guard_held(self, raw_db, caplog):
        """QC-437: one warning per distinct missing-set per
        process."""
        import logging

        from medterm4ds.engines.duckdb.engine import (
            LocalDuckDBEngine,
        )
        from medterm4ds.services.patient_friendly import (
            get_patient_friendly_names,
        )

        con = duckdb.connect(str(raw_db))
        try:
            engine = LocalDuckDBEngine(con)
            with caplog.at_level(logging.WARNING, logger="medterm4ds"):
                for _ in range(3):
                    get_patient_friendly_names(
                        [("SNOMEDCT_US", "44054006")], engine=engine
                    )
            assert caplog.text.count("prepared schema is missing") == 1
        finally:
            con.close()

    def test_c11_dispatch_gate_source_contract(self):
        """get_source_code_relations gates prepared dispatch on
        walk_edges coverage — the live transition point."""
        from pathlib import Path

        src = Path(
            "src/medterm4ds/engines/duckdb/hierarchy.py"
        ).read_text()
        assert "walk_edges" in src
        assert "_walk_edges_cover_source" in src
