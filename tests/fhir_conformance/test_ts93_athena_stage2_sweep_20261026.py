"""TS-93 Athena stage-2 surface sweep (2026-10-26).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261026c).
FRESH EVIDENCE — live CLI/service probes executed this run; no
prior-run results cited.

Scope: spec-comp discipline over the JUST-MERGED stage-2 surface
(de202b9 — my own campaign code; findings-only per doctrine).

AS1 (LOW-MED, NEW) — pre-batch1 athena databases break stage-2 with
    a raw BinderException: DBs ingested before athena-batch1 carry
    the OLD ancestor column names (ancestor_id/descendant_id); the
    stage-2 queries join on the CDM-official names
    (ancestor_concept_id/descendant_concept_id) → raw traceback.
    Fresh post-batch1 DBs are correct (verified: current-code
    ingest produces the CDM names; queries work; unknown concepts
    return clean []). The design never specified the MIGRATION story
    for existing athena_* tables — re-ingest is the obvious remedy
    but nothing tells the user. Fix shape: catch the Binder error at
    the service boundary and raise the actionable 'athena tables
    predate athena-batch1 — re-run data ingest-athena' (or detect
    old columns in athena_health). Pinned as10/as11.

AS2 (LOW, NEW) — CLI athena-concepts silently accepts an unknown
    --vocabulary (results [] — indistinguishable from a legitimate
    empty search; AF3's sibling gap on the new command). The data
    ingest-athena side errors loudly post-batch1; the search side
    does not. Fix shape: validate against athena_vocabulary like
    AF3. Pinned as20.

Controls (fresh, current-code DB): closure ancestry works
    (mini-fixture DM self+root); unknown concept_id → clean [];
    the AF2 remedy raise at the service layer on table-less DBs;
    deprecated-excluded-by-default held.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("medterm4ds.services.athena")

from medterm4ds.services.athena import (  # noqa: E402
    ingest_athena,
    search_athena_concepts,
)

MINI = "/tmp/opencode/athena-mini"
PRE_BATCH_DB = "/tmp/opencode/m24.duckdb"  # ingested pre-batch1 (old cols)


def _cli(*args: str) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, "PYTHONPATH": "src"}
    return subprocess.run(
        [sys.executable, "-m", "medterm4ds.apps.cli", *args],
        capture_output=True, text=True, timeout=300, env=env,
    )


@pytest.fixture(scope="module")
def fresh_db(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("ts93") / "m.duckdb")
    con = duckdb.connect(db)
    ingest_athena(
        con, MINI, vocabularies=("SNOMED",),
        accept_licenses=True, licenses_accepted_at="ts93",
    )
    con.close()
    return db


class TestTS93Controls:
    """Controls — the stage-2 contracts that hold."""

    def test_c10_ancestry_current_schema(self, fresh_db):
        """The mini fixture's SNOMED set yields the T2DM self-row
        (ancestry includes the reflexive row) — the current-schema
        contract."""
        proc = _cli(
            "athena-hierarchy", "--db", fresh_db,
            "--concept-id", "21602787",
        )
        assert proc.returncode == 0
        assert "Type 2 diabetes mellitus" in proc.stdout
        assert "concept_id" in proc.stdout

    def test_c11_service_remedy_on_no_tables(self):
        con = duckdb.connect()
        from medterm4ds.services.athena import get_athena_ancestors

        with pytest.raises(RuntimeError, match="ingest-athena"):
            get_athena_ancestors(con, 1)
        con.close()

    def test_c12_deprecated_excluded_by_default(self, fresh_db):
        con = duckdb.connect(fresh_db, read_only=True)
        rows = search_athena_concepts(con, "diabetes", limit=10)
        assert all(r["invalid_reason"] is None for r in rows)
        con.close()


class TestASFixes:
    """athena-batch2: AS1/AS2 fixed — pins flipped."""

    @pytest.mark.skipif(
        not Path(PRE_BATCH_DB).exists(),
        reason="pre-batch1 fixture DB not present",
    )
    def test_as10_flipped_remedy(self):
        """AS1 FIXED: the old-schema DB now raises the actionable
        re-ingest remedy (exit 2, no traceback)."""
        proc = _cli(
            "athena-hierarchy", "--db", PRE_BATCH_DB,
            "--concept-id", "21602787",
        )
        assert proc.returncode == 2
        assert "predate athena-batch1" in proc.stderr
        assert "ingest-athena" in proc.stderr

    def test_as20_flipped_loud_vocabulary(self, fresh_db):
        """AS2 FIXED: unknown --vocabulary errors listing the
        carried set (exit 2)."""
        proc = _cli(
            "athena-concepts", "--db", fresh_db,
            "--query", "diabetes", "--vocabulary", "BOGUS",
        )
        assert proc.returncode == 2
        assert "unknown vocabulary_id" in proc.stderr
        assert "SNOMED" in proc.stderr


class TestStage4bSeam:
    """The runtime $translate Athena ConceptMap seam (4b)."""

    @pytest.fixture(scope="class")
    def seam_client(self, tmp_path_factory):
        import duckdb as _d
        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import (
            FhirApiSettings,
            create_fhir_app,
        )
        from medterm4ds.services.athena import ingest_athena

        from .conftest import _make_conformance_db

        db = tmp_path_factory.mktemp("ts93_4b") / "m.duckdb"
        _make_conformance_db(db)
        con = _d.connect(str(db))
        ingest_athena(
            con, "/tmp/opencode/athena-mini",
            vocabularies=("SNOMED",),
            accept_licenses=True, licenses_accepted_at="4b",
        )
        con.close()
        app = create_fhir_app(FhirApiSettings(
            db_path=db, memory_profile="low",
            search_index_dir=str(db.parent / "noi"),
            prepare_cache=False,
        ))
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    ACM = "urn:medterm4ds:athena-conceptmap"
    SNOMED = "http://snomed.info/sct"
    TGT = "urn:athena:concept-id"

    def _match_parts(self, body):
        m = next(
            p for p in body.get("parameter", [])
            if p.get("name") == "match"
        )
        return {
            q.get("name"): next(
                (v for k, v in q.items() if k.startswith("value")),
                None,
            )
            for q in m.get("part", [])
        }

    def test_4b10_happy_equivalent(self, seam_client):
        r = seam_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "url": self.ACM, "system": self.SNOMED,
                "code": "44054006", "targetSystem": self.TGT,
            },
        )
        assert r.status_code == 200
        parts = self._match_parts(r.json())
        assert parts["equivalence"] == "equivalent"
        assert parts["concept"]["code"] == "21602787"

    def test_4b11_deprecated_unmatched(self, seam_client):
        r = seam_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "url": self.ACM, "system": self.SNOMED,
                "code": "99999001", "targetSystem": self.TGT,
            },
        )
        parts = self._match_parts(r.json())
        assert parts["equivalence"] == "unmatched"

    def test_4b12_wrong_target_400(self, seam_client):
        r = seam_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "url": self.ACM, "system": self.SNOMED,
                "code": "44054006", "targetSystem": self.SNOMED,
            },
        )
        assert r.status_code == 400

    def test_4b13_reverse_400(self, seam_client):
        r = seam_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "url": self.ACM, "system": self.SNOMED,
                "code": "44054006", "targetSystem": self.TGT,
                "reverse": "true",
            },
        )
        assert r.status_code == 400

    def test_4b14_unknown_code_false(self, seam_client):
        r = seam_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "url": self.ACM, "system": self.SNOMED,
                "code": "ZZZ", "targetSystem": self.TGT,
            },
        )
        result = next(
            p.get("valueBoolean")
            for p in r.json()["parameter"]
            if p.get("name") == "result"
        )
        assert result is False

    def test_4b15_default_crosswalk_unchanged(self, seam_client):
        """url omitted → the UMLS crosswalk (the seam NEVER changes
        the default)."""
        r = seam_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": "http://hl7.org/fhir/sid/icd-10-cm",
                "code": "E11", "targetSystem": self.SNOMED,
            },
        )
        assert r.status_code == 200
        parts = self._match_parts(r.json())
        assert parts["concept"]["code"] == "44054006"
