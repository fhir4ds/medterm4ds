"""TS-57 $search lexical result envelope (2026-10-17).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261017c).
FRESH EVIDENCE — live probes executed this run against the DEFAULT
BM25 index; no prior-run results cited.

Scope: the lexical RESULT envelope — Bundle.total semantics, count
edges, offset behavior, entry shape, system filter. TS-46 covered
input seams (SB1/SB2); TS-32/43 the canonical mode; the lexical
envelope contract was never matrixed.

SC1 (MEDIUM, NEW) — Bundle.total always equals the RETURNED entry
    count, never the true match count. R4 bundle.html: total = "total
    number of matches" (for searchsets). 'drug' has >=100 matches;
    count=2 reports total=2, count=50 → total=50 — total tracks the
    page size exactly. Combined with NO link[rel=next] (never
    emitted), a client concludes the search is EXHAUSTED at whatever
    count it asked for. The $expand surface went through this exact
    class (VS02-01 family) — the search envelope regresses it.
    Fix shape: total = the engine's true hit count (BM25 indexes
    know candidate counts) or omit total entirely (0..1 — absence is
    honest) + emit next-link when truncating. Flip pins sc10/sc11.

SC2 (LOW-MED, NEW) — offset accepted-and-ignored. offset=2 returns
    byte-identical content to offset=0 (top-5 codes equal). Paging
    is impossible on this surface (compounds SC1: no next-link, no
    working offset). Same accepted-ignored family as EA; the $expand
    offset (QC-241) pages correctly — the two surfaces diverge.
    Fix shape: slice results [offset:offset+count] or 400 on nonzero
    offset while unsupported. Flip pin sc20/sc21.

Controls (fresh): count validation 422 at 0/-1/1000/1001 (bounds
    enforced); count=1 exact; system filter narrows (SNOMED-only
    returned); entry shape fullUrl+resource+search with score +
    mode=match; resource carries system+code+display.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::FutureWarning")


@pytest.fixture(scope="module")
def search_client(tmp_path_factory):
    """search_client twin with the DEFAULT BM25 index dir wired
    (shared fixture 503s without an index; iteration-57 pattern)."""
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import (
        DEFAULT_SEARCH_INDEX_DIR,
        FhirApiSettings,
        create_fhir_app,
    )

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts57") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=DEFAULT_SEARCH_INDEX_DIR,
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app) as client:
        yield client


def _codes(r) -> list[str]:
    return [e["resource"]["code"] for e in r.json().get("entry", [])]


class TestSC1TotalTracksPageSize:
    """SC1 — total never reports the true match count."""

    def test_sc10_total_equals_entries(self, search_client):
        """count=2 → total=2 while the query has >=100 matches.
        Flip when total carries the true hit count (or is omitted
        while truncating)."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": "drug", "searchMode": "lexical", "count": 2},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["entry"]) == 2
        assert body["total"] == 2  # flip: true count or absent

    def test_sc11_total_scales_with_count(self, search_client):
        """The same query reports total=2, 5, 50 as count changes —
        total is a function of the REQUEST, not the data."""
        totals = {}
        for cv in (2, 5, 50):
            r = search_client.get(
                "/fhir/CodeSystem/$search",
                params={
                    "query": "drug", "searchMode": "lexical", "count": cv,
                },
            )
            totals[cv] = r.json()["total"]
        assert totals == {2: 2, 5: 5, 50: 50}  # flip w/ SC1

    def test_sc12_no_next_link(self, search_client):
        """link[rel=next] never emitted under truncation — the
        client has no traversal handle. Flip with SC1's fix."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": "drug", "searchMode": "lexical", "count": 2},
        )
        assert r.json().get("link") is None


class TestSC2OffsetNoOp:
    """SC2 — offset accepted, does nothing."""

    def test_sc20_offset_content_identical(self, search_client):
        """offset=2 vs offset=0: byte-identical top-5 codes. Flip
        when offset slices (or nonzero offset 400s while
        unsupported)."""
        r0 = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical", "count": 5,
            },
        )
        r2 = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical",
                "count": 5, "offset": 2,
            },
        )
        assert _codes(r0) == _codes(r2)

    def test_sc21_expand_offset_pages_as_contrast(self, search_client):
        """CONTRAST: $expand's offset (QC-241) pages correctly — the
        surfaces diverge on the same parameter name. Uses THIS
        suite's app: the shared fhir_client fixture would reconfigure
        the process-global SearchService singleton to its empty index
        dir on lifespan entry, 503ing subsequent search probes
        (app-lifespan configure_search_service side effect)."""
        r0 = search_client.get(
            "/fhir/ValueSet/$expand",
            params={
                "url": "http://snomed.info/sct/73211009?fhir_vs=isa",
                "count": 1,
            },
        )
        r1 = search_client.get(
            "/fhir/ValueSet/$expand",
            params={
                "url": "http://snomed.info/sct/73211009?fhir_vs=isa",
                "count": 1, "offset": 1,
            },
        )
        c0 = [e["code"] for e in r0.json()["expansion"]["contains"]]
        c1 = [e["code"] for e in r1.json()["expansion"]["contains"]]
        assert c0 != c1


class TestTS57Controls:
    """Controls — count bounds + envelope shape, fresh."""

    @pytest.mark.parametrize("cv", [0, -1, 1000, 1001])
    def test_c10_count_bounds_422(self, search_client, cv):
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical", "count": cv,
            },
        )
        assert r.status_code == 422

    def test_c11_count_one_exact(self, search_client):
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": "diabetes", "searchMode": "lexical", "count": 1},
        )
        assert len(r.json()["entry"]) == 1

    def test_c12_system_filter_narrows(self, search_client):
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical",
                "system": "http://snomed.info/sct", "count": 10,
            },
        )
        systems = {e["resource"]["system"] for e in r.json()["entry"]}
        assert systems == {"http://snomed.info/sct"}

    def test_c13_entry_shape(self, search_client):
        """Entry carries fullUrl+resource+search; search.score
        present + mode=match; resource has system+code+display."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": "diabetes", "searchMode": "lexical", "count": 2},
        )
        e0 = r.json()["entry"][0]
        assert {"fullUrl", "resource", "search"} <= set(e0)
        assert e0["search"]["mode"] == "match"
        assert e0["search"].get("score") is not None
        assert {"system", "code", "display"} <= set(e0["resource"])
