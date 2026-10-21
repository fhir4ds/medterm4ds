"""TS-71 $expand system In-param + mode interactions (2026-10-21).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261021).
FRESH EVIDENCE — live probes executed this run (real BM25 index for
filter-mode); no prior-run results cited.

Scope: the $expand `system` In-param — declared (in the EA known-set
since fixbatch2), honored in filter mode (undocumented anywhere),
never matrixed against modes and alias forms.

EM1 (LOW, NEW) — `system` silently ignored in url-mode. With url=
SNOMED-isa + system=LOINC (a CONFLICTING filter), the response is
byte-equivalent to url-only: the param is declared for the operation
but applies to filter-mode only, with no diagnostic and no spec
statement. A client narrowing a url expansion by system believes it
narrowed. Fix shape: apply the system filter to url-mode results
(mechanically available — the same gate machinery V1 uses) or 400
the combination per §4.9.2. Pinned em10/em11.

CONTROLS-LEAN (the strongest param posture on the server, pinned so
    it stays): filter-mode system FILTERS correctly (diabetes →
    SNOMED-only codes); urn:oid form resolves (SNOMED OID narrows
    correctly); unknown URIs 400 loudly ('Unrecognized system URI');
    filter-mode baseline multi-system confirmed (the precondition
    that makes the filtering observable).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_OID = "urn:oid:2.16.840.1.113883.6.96"
LOINC_URI = "http://loinc.org"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"


@pytest.fixture(scope="module")
def expand_client(tmp_path_factory):
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    from medterm4ds.services.search import DEFAULT_SEARCH_INDEX_DIR

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts71_sys") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=DEFAULT_SEARCH_INDEX_DIR,
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _codes(body: dict) -> set:
    return {
        e["code"]
        for e in body.get("expansion", {}).get("contains", [])
    }


class TestEM1SystemIgnoredInUrlMode:
    """EM1 — declared param silently dropped in url-mode."""

    def test_em10_conflicting_system_ignored(self, expand_client):
        """url=SNOMED-isa + system=LOINC: response identical to
        url-only — the conflicting filter is dropped without a
        diagnostic. Flip when url-mode applies the system filter or
        400s the combination."""
        with_sys = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "system": LOINC_URI},
        )
        url_only = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL},
        )
        assert with_sys.status_code == 200
        assert _codes(with_sys.json()) == _codes(url_only.json())

    def test_em11_premise_filter_mode_honors_system(
        self, expand_client
    ):
        """Filter-mode DOES filter by system (the mode asymmetry
        premise)."""
        base = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 10},
        ).json()
        narrow = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={
                "filter": "diabetes", "count": 10,
                "system": SNOMED_URI,
            },
        ).json()
        base_codes = _codes(base)
        assert "E11" in base_codes  # multi-system baseline
        assert _codes(narrow) == {"73211009", "44054006"}


class TestTS71Controls:
    """Controls — the strongest param posture on the server."""

    def test_c10_urn_oid_resolves(self, expand_client):
        r = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={
                "filter": "diabetes", "count": 10,
                "system": SNOMED_OID,
            },
        )
        assert r.status_code == 200
        assert _codes(r.json()) == {"73211009", "44054006"}

    def test_c11_unknown_system_400(self, expand_client):
        r = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={
                "filter": "diabetes", "count": 10,
                "system": "http://nowhere.org",
            },
        )
        assert r.status_code == 400
        assert "Unrecognized system URI" in (
            r.json()["issue"][0]["diagnostics"]
        )

    def test_c12_filter_mode_baseline_multi_system(
        self, expand_client
    ):
        """Precondition control: unfiltered filter-mode returns the
        multi-system set (E11 + SNOMED) that makes c-narrowing
        observable."""
        r = expand_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 10},
        )
        codes = _codes(r.json())
        assert {"E11", "73211009", "44054006"} <= codes
