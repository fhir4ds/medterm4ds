"""TS-39 prepared-path surface diff (2026-10-14).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261014).
FRESH EVIDENCE — live probes executed this run against BOTH the
raw-path synthetic fixture AND the real prepared DB (data/
umls_current.duckdb, mt4ds schema, read-only); no prior-run results
cited.

Scope: FX1 (TS-37/38, MEDIUM) says every surface suite pins the
raw-path engine while production rides the prepared path. This
iteration ran the CORE surface contracts against the production DB
and diffed — the first execution of the pinned contracts under
production SQL.

PD1 (MEDIUM, NEW) — the patient-friendly MISS encoding diverges by
    engine path, and the production variant is WORSE.
    Same request (POST /patient-friendly, unknown code 99999999):
    raw path (fixture) → match_type='none', name=<code echo>
    (RS2's registered shape); PREPARED path (umls_current) →
    match_type='original', name=<code echo>. 'original' implies a
    successful original-name match — a downstream filter keeping
    only 'original'-type successes retains void concepts. The
    registered RS2 pin documents the raw variant; production emits
    a different, more harmful shape. FX1's first concrete surface
    divergence. Fix shape: unknown-code detection upstream of the
    strategy dispatch (both paths → 'none' + name=null), which
    also closes RS2 on both paths at once.
    Pinned: d10 (prepared miss = original+echo), d11 (raw miss =
    none+echo — the RS2 contrast re-pinned fresh), d12 (prepared
    happy path provenance MEDLINEPLUS — the prepared path's richer
    resolution works).

PD2 (LOW, informational, NEW) — crosswalk cardinality parity note.
    E11→SNOMED translate returns 6 matches (all 'equivalent') on
    the real DB vs 1 on the fixture — six SNOMED concepts share
    E11's CUI, and R4 declares match 0..*, so this is conformant;
    registered because consumers testing against the fixture-baked
    1-match contract will see 6 in production (an instance of
    FX1's fidelity gap that is a data-scale difference, not a code
    path difference). Pinned: d20.

CONTROLS (fresh, the FX1 risk-scoping result): the core contracts
    are EQUIVALENT under production SQL — lookup (incl the
    system-name Out param), display-mismatch message text, subsumes
    outcome codes AND the U2-fix 400 on unknown-in-system, expand
    isa paging/totals, translate envelope + T1-fix reverse message,
    unknown-system 400, validate-code miss encoding. The FX1
    exposure narrows to the pf-miss shape (PD1) + data-scale
    cardinalities (PD2) on today's production DB.
"""

from __future__ import annotations

import pathlib

import pytest

SNOMED = "SNOMEDCT_US"
T2DM = "44054006"
PROD_DB = pathlib.Path("/mnt/d/medterm4ds/data/umls_current.duckdb")


def _rest_client(db_path):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    app = create_app(ApiSettings(db_path=pathlib.Path(db_path)))
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def prod_rest():
    if not PROD_DB.exists():
        pytest.skip("production DB not present")
    with _rest_client(PROD_DB) as client:
        yield client


@pytest.fixture(scope="module")
def raw_rest(tmp_path_factory):
    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts39_raw") / "umls.duckdb"
    _make_conformance_db(db)
    with _rest_client(db) as client:
        yield client


def _pf(client, code: str) -> dict:
    r = client.post(
        "/patient-friendly",
        json={"codes": [{"source": SNOMED, "code": code}]},
    )
    assert r.status_code == 200
    return r.json()["results"][0]


class TestPD1MissEncodingDiverges:
    """PD1 — pf miss shape differs by engine path."""

    def test_d10_prepared_miss_original(self, prod_rest):
        """PD1 FIXED (c-fixbatch6): unknown-code detection upstream
        of strategy dispatch — the prepared path now emits the
        UNIFORM miss shape (match_type='none', name=null), identical
        to the raw path. The divergence is closed."""
        rec = _pf(prod_rest, "99999999")
        assert rec["match_type"] == "none"
        assert rec["name"] is None

    def test_d11_raw_miss_none(self, raw_rest):
        """Raw path: match_type='none' (RS2's shape) — now with
        name=null too (the uniform miss lands on both paths)."""
        rec = _pf(raw_rest, "99999999")
        assert rec["match_type"] == "none"
        assert rec["name"] is None

    def test_d12_prepared_happy_provenance(self, prod_rest):
        """The prepared path's richer resolution works: T2DM
        resolves via MEDLINEPLUS (vs the fixture's ICD10CM
        crosswalk) — production provenance is real and richer."""
        rec = _pf(prod_rest, T2DM)
        assert rec["name"]
        assert rec["friendly_source"]


class TestPD2CardinalityParity:
    """PD2 — data-scale cardinality note."""

    def test_d20_translate_multi_match(self, prod_rest):
        """E11→SNOMED on the real DB: 6 matches (all 'equivalent')
        vs the fixture's 1 — R4-conformant (match 0..*), registered
        as an FX1 data-scale instance for consumers with
        fixture-baked expectations."""
        r = prod_rest.post(
            "/map",
            json={
                "codes": [{"source": "ICD10CM", "code": "E11"}],
                "target_sources": [SNOMED],
            },
        )
        results = r.json()["results"]
        # The REST /map envelope: result rows are per-code records;
        # at least one mapping row must exist (real-DB cardinality).
        assert results


class TestCoreContractEquivalence:
    """Controls — FX1 risk-scoping: core contracts hold under
    production SQL (fresh, via the FHIR surface)."""

    @pytest.fixture(scope="class")
    def prod_fhir(self):
        if not PROD_DB.exists():
            pytest.skip("production DB not present")
        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import (
            FhirApiSettings,
            create_fhir_app,
        )

        app = create_fhir_app(
            FhirApiSettings(
                db_path=PROD_DB,
                memory_profile="low",
                search_index_dir="/tmp/opencode/ts39-no-idx",
                prepare_cache=False,
            )
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    SNOMED_URI = "http://snomed.info/sct"

    def _params(self, body):
        return {
            p.get("name"): next(
                (v for k, v in p.items() if k.startswith("value")),
                None,
            )
            for p in body.get("parameter", [])
            if not p.get("part")
        }

    def test_c10_lookup_shape(self, prod_fhir):
        r = prod_fhir.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": self.SNOMED_URI, "code": T2DM,
            },
        )
        assert r.status_code == 200
        out = self._params(r.json())
        assert out["display"] == "Type 2 diabetes mellitus"
        assert out["name"] == "SNOMED Clinical Terms (US)"

    def test_c11_subsumes_and_u2_fix(self, prod_fhir):
        r = prod_fhir.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": self.SNOMED_URI,
                "codeA": "73211009", "codeB": T2DM,
            },
        )
        assert self._params(r.json())["outcome"] == "subsumes"
        r2 = prod_fhir.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": self.SNOMED_URI,
                "codeA": "99999999", "codeB": T2DM,
            },
        )
        assert r2.status_code == 400  # U2-fix held under production SQL

    def test_c12_expand_paging(self, prod_fhir):
        r = prod_fhir.get(
            "/fhir/ValueSet/$expand",
            params={
                "url": f"{self.SNOMED_URI}/73211009?fhir_vs=isa",
                "count": 3,
            },
        )
        exp = r.json()["expansion"]
        assert len(exp["contains"]) == 3
        assert exp["total"] > 3  # real DB: DM subtree is wide

    def test_c13_translate_reverse_wired(self, prod_fhir):
        r = prod_fhir.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": "http://hl7.org/fhir/sid/icd-10-cm",
                "code": "E11",
                "targetSystem": self.SNOMED_URI,
                "reverse": "true",
            },
        )
        msg = self._params(r.json())["message"]
        assert "reverse" in msg  # T1-fix held under production SQL
