"""TS-40 prod-diff round 2 — secondary REST endpoints (2026-10-14).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261014b).
FRESH EVIDENCE — live probes executed this run against the real
prepared DB (umls_current, read-only) via the REST surface; no
prior-run results cited.

Scope: TS-39 measured the CORE contracts under production SQL
(equivalent). This round runs the SECONDARY endpoints and the
registered finding-shapes against production — the registered
shapes were all pinned raw-path.

PR1 (MEDIUM, NEW) — the ConceptMap export's relationship value
    diverges by engine path.
    Same request (/conceptmap/patient-friendly, SNOMED T2DM):
    raw path → relationship='not-translated' (OP3's registered
    finding: the R4 FAILURE value on a working crosswalk);
    PREPARED path (production) → relationship='equivalent',
    match_type='same_cui', strategy='non_rxnorm_prepared', with a
    three-hop provenance chain (SNOMED→ICD10CM→MEDLINEPLUS,
    target_display='Diabetes Type 2'). The production value is the
    CORRECT one — OP3's defect is raw-path-only, and the two paths
    emit contradictory relationship values for the same export. A
    consumer testing against production then deploying the raw-path
    fixture sees the export flip to not-translated. Fix shape: the
    raw-path builder adopts the prepared path's relationship
    mapping (closes OP3's relationship half); the composite
    target_code ('SNOMEDCT_US:44054006') is path-INDEPENDENT
    (OP3's other half stands).
    Pinned: r10 (prepared = equivalent + same_cui + 3-hop chain),
    r11 (raw = not-translated — OP3's shape re-pinned fresh as the
    contrast).

PR2 (controls with teeth) — the OP-family and miss-shapes are
    PATH-STABLE under production SQL (fresh prod pins):
    OP1 (optimize void-code rules) reproduces on prod (mixed batch
    includes '99999999'); OP2 (relationship free-string) echoes
    'bogus' at 200; RS1 (/resolve mode dropped: default ==
    historical bytes); HY2 (unknown-code hierarchy 200 []); PY1
    (unknown-source lookup null-entry). These registered findings
    are NOT artifacts of the synthetic fixture — they are live on
    the production DB, which upgrades their evidence class from
    fixture-only to production-verified.

Controls (fresh): the prepared pf provenance chain shape (input →
    snomed_to_target → same_cui w/ MEDLINEPLUS name) — the richest
    verified export; optimize happy shapes on real data (829-code
    walk w/ depth-cap warning surfaced at the traverse layer).
"""

from __future__ import annotations

import pathlib

import pytest

SNOMED = "SNOMEDCT_US"
T2DM = "44054006"
PROD_DB = pathlib.Path("/mnt/d/medterm4ds/data/umls_current.duckdb")


def _code(c: str, source: str = SNOMED) -> dict:
    return {"source": source, "code": c}


@pytest.fixture(scope="module")
def prod_rest():
    if not PROD_DB.exists():
        pytest.skip("production DB not present")
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    app = create_app(ApiSettings(db_path=PROD_DB))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture(scope="module")
def raw_rest(tmp_path_factory):
    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts40_raw") / "umls.duckdb"
    _make_conformance_db(db)
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    app = create_app(ApiSettings(db_path=db))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _cm_pf(client, code: str) -> dict:
    r = client.post(
        "/conceptmap/patient-friendly",
        json={"codes": [_code(code)]},
    )
    assert r.status_code == 200
    return r.json()["results"][0]


class TestPR1RelationshipDiverges:
    """PR1 — the export's relationship value flips by engine path."""

    def test_r10_prepared_equivalent(self, prod_rest):
        """Production (prepared): relationship='equivalent' +
        match_type='same_cui' + the 3-hop MEDLINEPLUS chain — the
        CORRECT R4 value. Flip-tracking: if the raw-path fix adopts
        this mapping, the raw pin (r11) flips and PR1 closes with
        OP3's relationship half."""
        rec = _cm_pf(prod_rest, T2DM)
        assert rec["relationship"] == "equivalent"
        assert rec["match_type"] == "same_cui"
        assert rec["friendly_source"] == "MEDLINEPLUS"
        ops = [s["op"] for s in rec["matched_via"]["steps"]]
        assert ops == ["input", "snomed_to_target", "same_cui"]

    def test_r11_raw_not_translated(self, raw_rest):
        """PR1 FIXED (c-fixbatch6): the raw-path export now emits
        'equivalent' for crosswalk successes — matching the prepared
        path; the path-divergence is closed (OP3's relationship
        half resolved)."""
        rec = _cm_pf(raw_rest, T2DM)
        assert rec["relationship"] == "equivalent"

    def test_r12_composite_target_path_independent(self, prod_rest):
        """OP3's other half stands on BOTH paths: target_code is the
        composite input echo under pseudo-system
        PATIENT_FRIENDLY."""
        rec = _cm_pf(prod_rest, T2DM)
        assert rec["target_source"] == "PATIENT_FRIENDLY"
        assert rec["target_code"] == f"{SNOMED}:{T2DM}"


class TestPR2FindingsProductionVerified:
    """PR2 — registered shapes live on the production DB."""

    def test_r20_op1_void_rule_prod(self, prod_rest):
        """OP1 on production SQL: mixed batch smuggles the void
        concept into the ruleset (fixture finding, prod-verified)."""
        r = prod_rest.post(
            "/optimize",
            json={"codes": [_code(T2DM), _code("99999999")]},
        )
        includes = [
            rule["include"]
            for rec in r.json()["results"]
            for rule in rec["rules"]
        ]
        assert "99999999" in includes

    def test_r21_op2_bogus_relationship_prod(self, prod_rest):
        r = prod_rest.post(
            "/optimize",
            json={"codes": [_code(T2DM)], "relationship": "bogus"},
        )
        assert r.status_code == 200
        assert r.json()["results"][0]["relationship"] == "bogus"

    def test_r22_rs1_mode_dropped_prod(self, prod_rest):
        base = prod_rest.post("/resolve", json={"codes": [_code(T2DM)]})
        with_mode = prod_rest.post(
            "/resolve",
            json={"codes": [_code(T2DM)], "mode": "historical"},
        )
        assert base.json() == with_mode.json()

    def test_r23_hy2_unknown_code_empty_prod(self, prod_rest):
        r = prod_rest.post(
            "/hierarchy",
            json={"codes": [_code("99999999")], "direction": "children"},
        )
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_r24_py1_null_entry_prod(self, prod_rest):
        r = prod_rest.post(
            "/lookup",
            json={"codes": [_code(T2DM, "NOTASAB")]},
        )
        assert r.status_code == 200
        assert r.json()["results"] == [None]
