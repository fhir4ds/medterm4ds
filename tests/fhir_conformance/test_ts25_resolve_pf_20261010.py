"""TS-25 resolve/patient-friendly facade contracts (2026-10-10).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261010d).
FRESH EVIDENCE — live probes executed this run against the REST facade
and the CLI; no prior-run results cited.

Scope: the resolution-family surfaces never swept — REST /resolve and
/patient-friendly output contracts, the resolve_mode knob's
availability matrix, and the miss-encoding conventions within the
facade (PY1's cross-surface matrix extended to its remaining members).

RS1 (LOW-MED, NEW) — /resolve lost the resolve_mode knob.
    The SERVICE supports 3 modes (active_only/historical/
    resolve_current); QC-495's own comment on PatientFriendlyRequest
    documents the silent-mode-divergence lesson; the CLI exposes
    --resolve-mode (enum-gated); sibling REST endpoints /lookup and
    /patient-friendly accept resolve_mode (validated 400 at the
    service boundary). But ResolveRequest has NO mode field — the
    endpoint whose PURPOSE is resolution hardcodes active_only, and a
    client sending mode='historical' (or garbage) gets it silently
    dropped (pydantic ignore-extra) at 200. Fix shape: add the field
    (str, service-boundary validated like the siblings) or Literal
    enum. Flip pins r10-r12.

RS2 (LOW, NEW) — /patient-friendly miss encodes the identifier as
    its own display.
    Unknown code → 200 match_type='none' with name=<the code itself>
    ('99999999' as name) and friendly_source=<input source>. Contrast
    the same facade's /lookup null-entry (PY1) and /resolve
    not_found nulls — THREE miss shapes on ONE facade. A downstream
    display layer rendering name blindly shows the identifier as a
    clinical term. Fix shape: name=null on match_type='none' (align
    with /resolve's null convention). Flip pins r20/r21.

Controls (fresh): resolve_mode validated at the SERVICE boundary on
    /lookup + /patient-friendly (400 naming the 3 valid values);
    /resolve not_found shape is the GOOD convention (status+match_type
    'not_found', null resolved_* fields); CLI --resolve-mode enum
    argparse; /patient-friendly happy path crosses vocabularies
    (friendly_source ICD10CM for a SNOMED input — the crosswalk
    works); mixed-batch isolation on /patient-friendly.
"""

from __future__ import annotations

import pytest

SNOMED = "SNOMEDCT_US"


@pytest.fixture(scope="module")
def rest_client(tmp_path_factory):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts25_rest") / "umls.duckdb"
    _make_conformance_db(db)
    app = create_app(ApiSettings(db_path=db))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _code(c: str, source: str = SNOMED) -> dict:
    return {"source": source, "code": c}


class TestRS1ResolveModeMissing:
    """RS1 — /resolve hardcodes active_only; mode silently dropped."""

    def test_r10_mode_dropped(self, rest_client):
        """mode='historical' on /resolve → 200 with output identical
        to the default (the key is pydantic-dropped). Flip when the
        field exists (and diverges per mode)."""
        base = rest_client.post(
            "/resolve", json={"codes": [_code("44054006")]}
        )
        with_mode = rest_client.post(
            "/resolve",
            json={
                "codes": [_code("44054006")],
                "mode": "historical",
            },
        )
        assert base.status_code == 200
        assert with_mode.status_code == 200
        assert base.json() == with_mode.json()

    def test_r11_bogus_mode_ignored(self, rest_client):
        """mode='bogus' → 200 (dropped key — no validation because
        the field does not exist). Flip when the field lands (then
        bogus 400s at the service boundary like the siblings)."""
        r = rest_client.post(
            "/resolve",
            json={"codes": [_code("44054006")], "mode": "bogus"},
        )
        assert r.status_code == 200

    def test_r12_siblings_have_the_knob(self, rest_client):
        """Premise control: /lookup and /patient-friendly DO accept
        resolve_mode (validated) — the gap is /resolve alone."""
        r = rest_client.post(
            "/lookup",
            json={
                "codes": [_code("44054006")],
                "resolve_mode": "historical",
            },
        )
        assert r.status_code == 200
        r2 = rest_client.post(
            "/lookup",
            json={
                "codes": [_code("44054006")],
                "resolve_mode": "bogus",
            },
        )
        assert r2.status_code == 400
        assert "resolve_mode" in r2.json()["detail"]


class TestRS2MissEncodesIdentifierAsName:
    """RS2 — /patient-friendly unknown-code shape."""

    def test_r20_unknown_code_name_echo(self, rest_client):
        """match_type='none' carries name=<the code> and
        friendly_source=<input source> — the identifier posing as a
        display. Flip when name=null on miss."""
        r = rest_client.post(
            "/patient-friendly", json={"codes": [_code("99999999")]}
        )
        assert r.status_code == 200
        rec = r.json()["results"][0]
        assert rec["match_type"] == "none"
        assert rec["name"] == "99999999"
        assert rec["friendly_source"] == SNOMED

    def test_r21_resolve_miss_shape_contrast(self, rest_client):
        """The GOOD convention on the same facade: /resolve miss
        carries status/match_type='not_found' with NULL resolved
        fields (no identifier-as-display). The contrast is the
        finding — one facade, two miss shapes."""
        r = rest_client.post(
            "/resolve", json={"codes": [_code("99999999")]}
        )
        rec = r.json()["results"][0]
        assert rec["match_type"] == "not_found"
        assert rec["resolved_code"] is None
        assert rec["resolved_display"] is None


class TestResolveControls:
    """Controls — the resolution family's conformant shapes, fresh."""

    def test_c10_resolve_happy(self, rest_client):
        r = rest_client.post(
            "/resolve", json={"codes": [_code("44054006")]}
        )
        rec = r.json()["results"][0]
        assert rec["status"] == "active"
        assert rec["match_type"] == "active_exact"
        assert rec["resolved_code"] == "44054006"
        assert rec["resolved_display"] == "Type 2 diabetes mellitus"

    def test_c11_pf_happy_crosswalk(self, rest_client):
        """/patient-friendly crosses vocabularies: SNOMED input gets
        an ICD10CM-sourced friendly name (the crosswalk works)."""
        r = rest_client.post(
            "/patient-friendly", json={"codes": [_code("44054006")]}
        )
        rec = r.json()["results"][0]
        assert rec["match_type"] == "original"
        assert rec["friendly_source"] == "ICD10CM"
        assert rec["name"] == "Type 2 Diabetes Mellitus"

    def test_c12_pf_mode_validated(self, rest_client):
        """/patient-friendly resolve_mode validated at the service
        boundary (QC-495's contract held)."""
        r = rest_client.post(
            "/patient-friendly",
            json={
                "codes": [_code("44054006")],
                "resolve_mode": "bogus",
            },
        )
        assert r.status_code == 400
        assert "active_only" in r.json()["detail"]

    def test_c13_pf_mixed_batch(self, rest_client):
        """Mixed-batch isolation on /patient-friendly too ([valid,
        unknown] → [record, none-shape])."""
        r = rest_client.post(
            "/patient-friendly",
            json={"codes": [_code("44054006"), _code("99999999")]},
        )
        results = r.json()["results"]
        assert len(results) == 2
        assert results[0]["match_type"] == "original"
        assert results[1]["match_type"] == "none"
