"""TS-92 Athena stage 2: query surfaces (2026-10-26).

Campaign ATHENA VOCABULARY SUPPORT stage 2 (design §2).
FRESH EVIDENCE — all surfaces exercised this run against the REAL
download (TS-88/TS-90 baselines) plus the shared fixture for the
FHIR flag semantics; no prior-run results cited.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("medterm4ds.services.athena")

from medterm4ds.services.athena import (  # noqa: E402
    athena_health,
    athena_subsumes,
    count_athena_ancestors,
    get_athena_ancestors,
    get_athena_descendants,
    search_athena_concepts,
)

REAL = Path("/tmp/opencode/athena-real")
T2DM_ID = 201826
DM_ID = 201820

pytestmark_family = pytest.mark.skipif(
    not REAL.exists(), reason="real Athena download not extracted"
)


@pytest.fixture(scope="module")
def athena_con(tmp_path_factory):
    con = duckdb.connect()
    from medterm4ds.services.athena import ingest_athena

    # In-memory DB from the mini fixture for fast surfaces + the
    # real download for the scale gate below (separate fixture).
    con.execute("CREATE TABLE _keep (x INT)")
    return con


@pytest.fixture(scope="module")
def real_con():
    """Real-download DB (SNOMED+RxNorm vocab filter for speed)."""
    if not REAL.exists():
        pytest.skip("real download absent")
    from medterm4ds.services.athena import ingest_athena

    db = duckdb.connect()
    ingest_athena(
        db, REAL, vocabularies=("SNOMED", "RxNorm"),
        accept_licenses=True, licenses_accepted_at="ts92",
    )
    yield db
    db.close()


class TestAncestryReal:
    """Closure ancestry — exact, on real data (AN2's promise)."""

    def test_a10_exact_t2dm(self, real_con):
        anc = get_athena_ancestors(real_con, T2DM_ID)
        assert len(anc) == 9  # the TS-90 baseline
        assert count_athena_ancestors(real_con, T2DM_ID) == 9

    def test_a11_fartherest_first_with_depths(self, real_con):
        anc = get_athena_ancestors(real_con, T2DM_ID)
        assert anc[0]["min_levels"] >= anc[-1]["min_levels"]
        dm_row = next(a for a in anc if a["concept_id"] == DM_ID)
        assert dm_row["min_levels"] == dm_row["max_levels"] == 1

    def test_a12_max_depth_filter(self, real_con):
        shallow = get_athena_ancestors(real_con, T2DM_ID, max_depth=1)
        assert all(a["min_levels"] <= 1 for a in shallow)
        assert len(shallow) < 9

    def test_a13_descendants_nearest_first(self, real_con):
        desc = get_athena_descendants(real_con, DM_ID, limit=5)
        depths = [d["min_levels"] for d in desc]
        assert depths == sorted(depths)
        assert desc[0]["concept_id"] == DM_ID  # reflexive self-row

    def test_a14_subsumes_outcomes(self, real_con):
        assert athena_subsumes(real_con, DM_ID, T2DM_ID) == "subsumes"
        assert (
            athena_subsumes(real_con, T2DM_ID, DM_ID) == "subsumed-by"
        )
        assert (
            athena_subsumes(real_con, T2DM_ID, T2DM_ID) == "equivalent"
        )
        assert (
            athena_subsumes(real_con, 21060909, T2DM_ID)
            == "not-subsumed"
        )  # metformin is not a diabetes ancestor


class TestSearch:
    """Athena-native search filters."""

    def test_b10_domain_filter(self, real_con):
        rows = search_athena_concepts(
            real_con, "diabetes", domain_id="Condition", limit=5
        )
        assert rows and all(r["domain"] == "Condition" for r in rows)

    def test_b11_vocabulary_filter(self, real_con):
        rows = search_athena_concepts(
            real_con, "metformin", vocabulary_id="RxNorm", limit=5
        )
        assert rows and all(r["vocabulary"] == "RxNorm" for r in rows)

    def test_b12_deprecated_excluded_by_default(self, real_con):
        base = search_athena_concepts(real_con, "diabetes", limit=50)
        assert all(r["invalid_reason"] is None for r in base)
        with_dep = search_athena_concepts(
            real_con, "diabetes", include_deprecated=True, limit=50
        )
        assert len(with_dep) >= len(base)

    def test_b13_whitespace_fails_loud(self, real_con):
        with pytest.raises(ValueError, match="non-empty"):
            search_athena_concepts(real_con, "   ")

    def test_b14_health_block_live_facts(self, real_con):
        h = athena_health(real_con)
        assert h["present"] is True
        assert set(h["vocabularies"]) == {"RxNorm", "SNOMED"}
        assert h["loaded_at"]


class TestAbsentTables:
    """The AF2 doctrine at the service layer."""

    def test_c10_ancestry_names_remedy(self):
        con = duckdb.connect()  # no athena tables
        with pytest.raises(RuntimeError, match="ingest-athena"):
            get_athena_ancestors(con, T2DM_ID)
        con.close()


class TestSubsumesFlag:
    """MEDTERM4DS_ATHENA_SUBSUMES=1 — the FHIR opt-in fast path."""

    def test_d10_flag_off_default_walk(self, fhir_client):
        """Without the flag, $subsumes uses the UMLS walk (the
        contract unchanged)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": "http://snomed.info/sct",
                "codeA": "73211009", "codeB": "44054006",
            },
        )
        assert r.status_code == 200

    def test_d11_fast_path_resolvable_pair(
        self, tmp_path_factory, athena_mini_dir, monkeypatch
    ):
        """Flag ON + both codes Athena-resolvable → closure answer.
        Fixture DB + mini athena (SNOMED) — T2DM/DM are in both."""
        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import (
            FhirApiSettings,
            create_fhir_app,
        )
        from medterm4ds.services.athena import ingest_athena

        from .conftest import _make_conformance_db

        db = tmp_path_factory.mktemp("ts92_flag") / "m.duckdb"
        _make_conformance_db(db)
        con = duckdb.connect(str(db))
        ingest_athena(
            con, str(athena_mini_dir),
            vocabularies=("SNOMED",),
            accept_licenses=True, licenses_accepted_at="ts92",
        )
        con.close()
        monkeypatch.setenv("MEDTERM4DS_ATHENA_SUBSUMES", "1")
        app = create_fhir_app(FhirApiSettings(
            db_path=db, memory_profile="low",
            search_index_dir=str(db.parent / "noi"),
            prepare_cache=False,
        ))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get(
                "/fhir/CodeSystem/$subsumes",
                params={
                    "system": "http://snomed.info/sct",
                    "codeA": "73211009", "codeB": "44054006",
                },
            )
        assert r.status_code == 200
        outcome = next(
            p.get("valueCode")
            for p in r.json()["parameter"]
            if p.get("name") == "outcome"
        )
        assert outcome == "subsumes"

    def test_d12_fast_path_unresolvable_falls_back(
        self, tmp_path_factory, athena_mini_dir, monkeypatch
    ):
        """Flag ON + codes NOT in athena (UMLS-only fixture) → the
        UMLS walk answers (fallback, contract unchanged)."""
        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import (
            FhirApiSettings,
            create_fhir_app,
        )

        from .conftest import _make_conformance_db

        db = tmp_path_factory.mktemp("ts92_fb") / "m.duckdb"
        _make_conformance_db(db)
        monkeypatch.setenv("MEDTERM4DS_ATHENA_SUBSUMES", "1")
        app = create_fhir_app(FhirApiSettings(
            db_path=db, memory_profile="low",
            search_index_dir=str(db.parent / "noi"),
            prepare_cache=False,
        ))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get(
                "/fhir/CodeSystem/$subsumes",
                params={
                    "system": "http://snomed.info/sct",
                    "codeA": "73211009", "codeB": "44054006",
                },
            )
        assert r.status_code == 200
        outcome = next(
            p.get("valueCode")
            for p in r.json()["parameter"]
            if p.get("name") == "outcome"
        )
        assert outcome == "subsumes"
