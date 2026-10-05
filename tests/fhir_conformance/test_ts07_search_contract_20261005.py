"""TS-07 $search contract sweep (2026-10-05).

Maintenance spec-comp iteration 16 (worktree maint/spec-comp-20261005b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: first maintenance sweep of the custom $search operation's
parameter contract across ALL modes (lexical/semantic/hybrid/canonical),
GET + POST. Companion to TS-06 ($extract).

G1 (MEDIUM, NEW) — resultTypes silently IGNORED on the semantic path.
    searchMode=semantic with resultTypes=lab (or condition) returns
    the UNFILTERED result set: 50 ICD-10-CM condition entries (E08
    "Diabetes" et al.) under resultTypes=lab, identical totals with
    and without the filter, identical systems. POST form identical.
    Contrast: searchMode=canonical VALIDATES resultTypes and 400s on
    garbage ("Unknown result type: 'bogus'. Valid: …") — so the
    parameter is load-bearing in one mode and a silent no-op in
    another. A client filtering semantic results to labs gets
    conditions with a 200. Fix shape: wire result_types into the
    semantic path (search.py semantic query) or reject the combination
    explicitly. Pinned: g10 (GET unfiltered), g11 (POST unfiltered),
    g12 (canonical validates — the contrast control doubling as a
    finding pin).

G2 (LOW, NEW) — service-availability gate precedes input validation.
    Default-mode (hybrid) $search with an INVALID enum value
    (engine=bogus / resultTypes=bogus / sources=bogus) returns 503
    (index unavailable) rather than 422/400 — the availability probe
    runs before syntax validation, so a client with BOTH a bad
    parameter and a missing index sees only the availability error
    and cannot discover its own malformed request until the index is
    fixed. R4 §3.1.0.7 general principle: validate inputs before
    business logic. Fix shape: validate enum params at the route
    (Query pattern guards like $extract's mode/minGrade — those DO
    422 before availability). Pinned: g20/g21.

Controls verified fresh (g-series): searchMode enum guarded (422);
query required/min_length/over-length guarded (422 empty / 422
missing / 422 5000-char); whitespace query → 400 (deliberate
ValueError path); lexical/hybrid without index → 503 with guidance
diagnostics; semantic + canonical happy paths → 200 Bundle (model
auto-download per Phase 3); canonical resultTypes=condition filters
(or at minimum is validated); canonical sources=SNOMEDCT_US scoped
vs bogus-sources parity (sources scope-ignored for canonical —
documented no-op, ledger note N2); count=50 honored; POST Parameters
body parity with GET (same query semantics); match-grade extension
present on entries; search.mode=match.

N2 (NOTE, no pin): sources param is a no-op for canonical mode
(valid and bogus values return identical totals) — like engine
(hybrid-only), sources appears lexical/hybrid-scoped; recorded as a
documented-scope decision to make when G1 is fixed.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from .conftest import _make_conformance_db

SEARCH = "/fhir/CodeSystem/$search"
SNOMED_URI = "http://snomed.info/sct"


@pytest.fixture(scope="module")
def search_client(tmp_path_factory):
    """Module client for $search probes (model auto-downloads once)."""
    pytest.importorskip("fastapi")
    from medterm4ds.apps.fhir_api import (
        FhirApiSettings,
        create_fhir_app,
    )

    db_path = tmp_path_factory.mktemp("ts07") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(tmp_path_factory.mktemp("no_index")),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _systems(client, **params) -> set[str]:
    r = client.get(SEARCH, params=params)
    assert r.status_code == 200, r.text
    return {
        e["resource"]["system"]
        for e in r.json().get("entry", [])
    }


class TestG1SemanticResultTypesIgnored:
    """G1 — resultTypes is a silent no-op on the semantic path."""

    def test_g10_get_unfiltered(self, search_client):
        """semantic + resultTypes=lab returns ICD-10-CM condition
        codes (CURRENT, deviation). Flip when the filter is wired:
        expect only lab-system entries (LOINC)."""
        systems = _systems(
            search_client,
            query="diabetes",
            searchMode="semantic",
            resultTypes="lab",
            count=50,
        )
        assert "http://hl7.org/fhir/sid/icd-10-cm" in systems

    def test_g11_post_unfiltered(self, search_client):
        """POST form identical — the parameter never reaches the
        semantic query on either verb."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "query", "valueString": "diabetes"},
                {"name": "searchMode", "valueCode": "semantic"},
                {"name": "resultTypes", "valueCode": "lab"},
                {"name": "count", "valueInteger": 10},
            ],
        }
        r = search_client.post(SEARCH, json=body)
        assert r.status_code == 200
        systems = {
            e["resource"]["system"]
            for e in r.json().get("entry", [])
        }
        assert "http://hl7.org/fhir/sid/icd-10-cm" in systems

    def test_g12_canonical_validates(self, search_client):
        """CONTRAST: canonical mode validates resultTypes and 400s
        on garbage — the same parameter is load-bearing there."""
        r = search_client.get(
            SEARCH,
            params={
                "query": "diabetes",
                "searchMode": "canonical",
                "resultTypes": "bogus",
            },
        )
        assert r.status_code == 400
        assert "Unknown result type" in str(r.json())


class TestG2AvailabilityBeforeValidation:
    """G2 — 503 availability gate precedes input validation."""

    def test_g20_engine_bogus_503_not_422(self, search_client):
        """Default (hybrid) mode with engine=bogus → 503 (index
        unavailable), not 422 — the malformed parameter is invisible
        until the index exists (CURRENT, deviation). Flip when enum
        params are route-guarded: expect 422 regardless of index."""
        r = search_client.get(
            SEARCH,
            params={"query": "diabetes", "engine": "bogus"},
        )
        assert r.status_code == 503

    def test_g21_result_types_bogus_503_not_422(self, search_client):
        """Same ordering issue for resultTypes under hybrid."""
        r = search_client.get(
            SEARCH,
            params={
                "query": "diabetes", "resultTypes": "bogus",
            },
        )
        assert r.status_code == 503


class TestSearchControls:
    """Controls: guarded params + happy paths, fresh."""

    def test_g30_mode_enum_422(self, search_client):
        r = search_client.get(
            SEARCH,
            params={"query": "diabetes", "searchMode": "bogus"},
        )
        assert r.status_code == 422

    @pytest.mark.parametrize(
        "params",
        [
            {"query": ""},
            {},
            {"query": "x" * 5000},
        ],
        ids=["empty", "missing", "overlong"],
    )
    def test_g31_query_guards(
        self, search_client, params
    ):
        r = search_client.get(SEARCH, params=params)
        assert r.status_code == 422

    def test_g32_whitespace_query_400(self, search_client):
        r = search_client.get(
            SEARCH, params={"query": "   "}
        )
        assert r.status_code == 400

    @pytest.mark.parametrize("mode", ["lexical", "hybrid"])
    def test_g33_no_index_503(self, search_client, mode):
        r = search_client.get(
            SEARCH,
            params={"query": "diabetes", "searchMode": mode},
        )
        assert r.status_code == 503
        assert (
            r.json().get("resourceType") == "OperationOutcome"
        )

    @pytest.mark.parametrize("mode", ["semantic", "canonical"])
    def test_g34_happy_paths(self, search_client, mode):
        r = search_client.get(
            SEARCH,
            params={"query": "diabetes", "searchMode": mode},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("resourceType") == "Bundle"
        assert body.get("type") == "searchset"
        if body.get("entry"):
            e0 = body["entry"][0]
            assert e0.get("search", {}).get("mode") == "match"
            assert e0["resource"].get("code")

    def test_g35_count_honored(self, search_client):
        r = search_client.get(
            SEARCH,
            params={
                "query": "diabetes",
                "searchMode": "canonical",
                "count": 3,
            },
        )
        assert r.status_code == 200
        assert len(r.json().get("entry", [])) <= 3

    def test_g36_match_grade_extension(self, search_client):
        r = search_client.get(
            SEARCH,
            params={
                "query": "diabetes",
                "searchMode": "canonical",
            },
        )
        entries = r.json().get("entry", [])
        if entries:
            ext = entries[0].get("search", {}).get(
                "extension", []
            )
            urls = [x.get("url") for x in ext]
            assert any(
                "match-grade" in u for u in urls
            )
