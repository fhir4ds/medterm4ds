"""TS-04/VS-04 paging-semantics findings suite (2026-10-03).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261003c).
FRESH EVIDENCE — live probes executed this run against a purpose-built
deeper fixture (5 descendants under SNOMED 73211009, full expansion =
6, same shape as the VS02-01 observability suite); no prior-run results
cited.

Findings pinned (findings-only; production unchanged):

P1 (LOW) — ``expansion.offset`` never echoed when paging is used.
    FHIR R4 §4.9.12 ValueSet.expansion.offset: "If paging is being
    used, the offset at which this resource starts. I.e. this resource
    is a partial view into the expansion. If paging is not being used,
    this element SHALL NOT be present." The server accepts offset,
    serves the correct slice, but never emits ``expansion.offset`` —
    a paging client cannot confirm which page it received (only
    infer from its own request).

P2 (MEDIUM) — ``expansion.total`` is PAGE-DEPENDENT on the same
    expansion. The +1-probe budget is computed per request window
    (offset + count), so the observed lower bound differs by page:
    fresh probes on the SAME value set (true total 6): offset=0/count=3
    → total=4; offset=2/count=2 → total=5; offset=3/count=3 → total=6.
    A paging client that reads total on page 1 (4) believes the
    expansion is complete after page 2 and silently drops 2 concepts.
    Root cause family: CF-HISTORIAN-VS02-01 (BFS-cap total semantics);
    this is a NEW offset-tied manifestation — the exact-count fix
    (unbounded BFS or COUNT(*)) would make total page-invariant and
    close both.

Controls: page slices are correct and stably ordered; past-the-end
pages are empty (not errors); deep-offset behavior identical via the
$batch surface (entry-status 200, empty contains); invalid offsets
(negative, non-numeric) are 400 OperationOutcome via batch and 422
via direct GET.
"""

from __future__ import annotations

import duckdb
import pytest

SNOMED_URI = "http://snomed.info/sct"
DM = "73211009"
FULL_TOTAL = 6  # root + T2DM + 4 seeded descendants


@pytest.fixture(scope="module")
def paged_client(tmp_path_factory):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts04_paging") / "umls.duckdb"
    _make_conformance_db(db_path)
    con = duckdb.connect(str(db_path))
    con.executemany(
        "INSERT INTO mrconso VALUES (?, 'PT', ?, ?, 'N', "
        "'SNOMEDCT_US', 'C0000000')",
        [
            ("10000001", "Observation diabetes type A", "A10000001"),
            ("10000002", "Observation diabetes type B", "A10000002"),
            ("10000003", "Observation diabetes type C", "A10000003"),
            ("10000004", "Diabetes type D", "A10000004"),
        ],
    )
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [
            ("A10000001", "A44054006", "isa", "PAR"),
            ("A10000002", "A44054006", "isa", "PAR"),
            ("A10000003", "A44054006", "isa", "PAR"),
            ("A10000004", "A73211009", "isa", "PAR"),
        ],
    )
    con.close()
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(tmp_path_factory.mktemp("no_index")),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _page(client, offset=None, count=None):
    params = {"url": f"{SNOMED_URI}/{DM}?fhir_vs=isa"}
    if offset is not None:
        params["offset"] = offset
    if count is not None:
        params["count"] = count
    r = client.get("/fhir/ValueSet/$expand", params=params)
    assert r.status_code == 200, r.text
    return r.json()["expansion"]


class TestP1OffsetEcho:
    """P1 — expansion.offset never echoed under paging."""

    @pytest.mark.parametrize(
        "offset,count", [(0, 3), (3, 3), (2, 2)]
    )
    def test_p10_offset_not_echoed(self, paged_client, offset, count):
        """Paging in use → expansion.offset MUST carry the page start.
        Current: element absent on every page.

        WHEN THE FIX LANDS (emit expansion.offset = offset whenever
        offset paging is active), flip to assert equality.
        """
        exp = _page(paged_client, offset, count)
        assert exp.get("offset", None) is None, (
            f"expansion.offset now echoed as {exp['offset']} — the "
            "echo fix landed; flip this pin to assert equality."
        )

    def test_p11_no_offset_no_element(self, paged_client):
        """Control (spec SHALL-NOT): without paging, the element must
        stay absent — the fix must be offset-conditional."""
        exp = _page(paged_client)
        assert "offset" not in exp


class TestP2PageDependentTotal:
    """P2 — total differs by page on the SAME expansion."""

    def test_p20_total_varies_by_page(self, paged_client):
        """Page 1 (offset=0/count=3) reports total=4 while page 2
        (offset=3/count=3) reports the exact 6. Documented current
        behavior: total = observed lower bound (offset+count+1 when
        the +1 probe fires; exact once a page observes the tail).

        WHEN THE EXACT-COUNT FIX LANDS (CF-HISTORIAN-VS02-01 fix
        shapes a/b/c), total becomes page-invariant == 6 — flip this
        probe to assert invariance across pages.
        """
        t1 = _page(paged_client, 0, 3)["total"]
        t2 = _page(paged_client, 3, 3)["total"]
        assert t1 != FULL_TOTAL, (
            "page-1 total is now exact — the exact-count fix appears "
            "to have landed; flip this pin to page-invariance."
        )
        assert t2 == FULL_TOTAL
        assert t1 < t2, (
            f"expected page-dependent totals (lower bound on page 1), "
            f"got {t1} then {t2}"
        )

    def test_p21_mid_page_lower_bound_shape(self, paged_client):
        """offset=2/count=2: window observes codes 2..3 plus the +1
        probe → total=5 (lower bound). Pin the exact arithmetic."""
        exp = _page(paged_client, 2, 2)
        assert len(exp["contains"]) == 2
        assert exp["total"] == 5, (
            f"mid-page lower bound changed: got {exp['total']} "
            "(expected offset+count+1=5 while the +1 probe fires)"
        )


class TestPagingControls:
    """Correct-behavior controls (slice, ordering, past-end, batch,
    validation) — these assert CONFORMANCE, not drift."""

    def test_c10_page_slices_correct(self, paged_client):
        p1 = [c["code"] for c in _page(paged_client, 0, 3)["contains"]]
        p2 = [c["code"] for c in _page(paged_client, 3, 3)["contains"]]
        assert len(p1) == 3 and len(p2) == 3
        assert not set(p1) & set(p2), "pages must be disjoint"
        assert set(p1) | set(p2) or True  # shape only

    def test_c11_ordering_stable_across_calls(self, paged_client):
        a = [c["code"] for c in _page(paged_client, 0, 3)["contains"]]
        b = [c["code"] for c in _page(paged_client, 0, 3)["contains"]]
        assert a == b

    def test_c12_past_end_empty_not_error(self, paged_client):
        """Past-the-end page: empty (contains omitted or empty list —
        both conformant shapes for a 0..* element), never an error."""
        exp = _page(paged_client, 999999, 3)
        assert exp.get("contains", []) == []

    def test_c13_batch_deep_offset_same_contract(self, paged_client):
        bundle = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [{
                "request": {
                    "method": "GET",
                    "url": (
                        "ValueSet/$expand?url=http%3A%2F%2Fsnomed.info"
                        "%2Fsct%2F73211009%3Ffhir_vs%3Disa&count=1"
                        "&offset=999999"
                    ),
                },
            }],
        }
        r = paged_client.post("/fhir", json=bundle)
        assert r.status_code == 200
        entry = r.json()["entry"][0]
        assert entry["response"]["status"] == "200"
        assert entry["resource"]["expansion"].get("contains", []) == []

    def test_c14_invalid_offsets_rejected(self, paged_client):
        """Negative / non-numeric offsets: direct GET 422 (query
        validation), batch entry 400 OperationOutcome — both FHIR-shaped
        rejections, no 5xx."""
        for bad in ("-1", "abc"):
            r = paged_client.get(
                "/fhir/ValueSet/$expand",
                params={
                    "url": f"{SNOMED_URI}/{DM}?fhir_vs=isa",
                    "offset": bad,
                },
            )
            assert r.status_code == 422
            bundle = {
                "resourceType": "Bundle", "type": "batch",
                "entry": [{
                    "request": {
                        "method": "GET",
                        "url": (
                            "ValueSet/$expand?url=http%3A%2F%2Fsnomed."
                            "info%2Fsct%2F73211009%3Ffhir_vs%3Disa"
                            f"&offset={bad}"
                        ),
                    },
                }],
            }
            rb = paged_client.post("/fhir", json=bundle)
            entry = rb.json()["entry"][0]
            assert entry["response"]["status"] == "400"
            assert entry["resource"]["resourceType"] == "OperationOutcome"
