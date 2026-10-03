"""CF-HISTORIAN-VS02-01 deeper-fixture observability suite (2026-10-02).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261002c).
FRESH EVIDENCE — every probe executed this run against a purpose-built
deeper fixture; no prior-run results cited.

Background (from the carry-forward registry):
  CF-HISTORIAN-VS02-01 (HIGH — DEFERRED): ``_expand_intensional`` and
  ``expand_url_pattern`` BFS-cap ``total`` semantics. The QA-057/QA-068
  fixes landed the "+1 probe" lower bound (``total = len(contains) + 1``
  when count_limited or depth_cap_hit), but the EXACT un-truncated count
  remains deferred. The bug has been INVISIBLE in CI because the shared
  conformance fixture carries exactly ONE mrrel hierarchy row
  (T2DM -> Diabetes), matching the count=1 BFS budget by coincidence.

This suite applies the CS-05 "carry-forward-with-reproduction-shape"
methodology: build the deeper fixture the CF demanded (multiple
descendants under one root), and pin the CURRENT behavior so that
(a) the deferred semantics are finally OBSERVABLE in CI,
(b) any future drift of the +1 lower-bound contract fails loudly,
(c) when the exact-count fix lands, these probes get tightened to the
    exact values (fixture is deterministic: root + 5 descendants).

Fixture (private to this suite, seeded SNOMEDCT_US):
  73211009 "Diabetes mellitus"            (root, exists in shared fixture)
  44054006 "Type 2 diabetes mellitus"     (level 1; also in shared fixture)
  10000001 "Observation diabetes type A"  (level 2, child of 44054006)
  10000002 "Observation diabetes type B"  (level 2, child of 44054006)
  10000003 "Observation diabetes type C"  (level 2, child of 44054006)
  10000004 "Diabetes type D"              (level 1, child of root)

  mrrel (generic SNOMED family: child in AUI1, parent in AUI2,
  RELA='isa', REL='PAR' — mirroring the shared fixture's row shape):
    44054006 isa 73211009   (already present)
    10000001 isa 44054006
    10000002 isa 44054006
    10000003 isa 44054006
    10000004 isa 73211009

  => full isa-expansion of 73211009 = 5 descendants + root = 6 total.

Expected CURRENT behavior under count caps (the +1 lower-bound contract):
  count=6 (fits everything)  -> total=6, no toocostly extension
  count=3 (root + 2 shown)   -> contains=3, total=4 (len+1 lower bound),
                                toocostly extension PRESENT
  count=1 (root only)        -> contains=1, total=2, extension PRESENT
"""

from __future__ import annotations

import pytest


SNOMED_URI = "http://snomed.info/sct"
ROOT = "73211009"
DESCENDANTS_L1 = ("44054006", "10000004")
DESCENDANTS_L2_UNDER_T2DM = ("10000001", "10000002", "10000003")
FULL_TOTAL = 1 + len(DESCENDANTS_L1) + len(DESCENDANTS_L2_UNDER_T2DM)  # 6


@pytest.fixture(scope="module")
def deep_client(tmp_path_factory):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("vs02_deep") / "umls.duckdb"
    _make_conformance_db(db_path)

    import duckdb

    con = duckdb.connect(str(db_path))
    # Seed deeper hierarchy: every new concept gets an mrconso atom + isa row.
    new_atoms = [
        ("10000001", "PT", "Observation diabetes type A", "A10000001"),
        ("10000002", "PT", "Observation diabetes type B", "A10000002"),
        ("10000003", "PT", "Observation diabetes type C", "A10000003"),
        ("10000004", "PT", "Diabetes type D", "A10000004"),
    ]
    con.executemany(
        "INSERT INTO mrconso VALUES (?, 'PT', ?, ?, 'N', 'SNOMEDCT_US', "
        "'C0000000')",
        [(code, name, aui, ) for code, name, aui in
         [(c, n, a) for (c, _t, n, a) in new_atoms]],
    )
    isa_rows = [
        ("A10000001", "A44054006", "isa", "PAR"),
        ("A10000002", "A44054006", "isa", "PAR"),
        ("A10000003", "A44054006", "isa", "PAR"),
        ("A10000004", "A73211009", "isa", "PAR"),
    ]
    con.executemany("INSERT INTO mrrel VALUES (?, ?, ?, ?)", isa_rows)
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


def _expand(client, count):
    r = client.get(
        "/fhir/ValueSet/$expand",
        params={"url": f"{SNOMED_URI}/{ROOT}?fhir_vs=isa", "count": count},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _toocostly(body) -> bool:
    return any(
        "valueset-toocostly" in (e.get("url", "") or "")
        for e in body["expansion"].get("extension", [])
    )


class TestDeepFixtureBfsTotal:
    """CF-HISTORIAN-VS02-01 made observable: BFS-capped total semantics."""

    def test_d10_full_fit_total_exact(self, deep_client):
        """count >= full size: total is exact, no truncation extension."""
        body = _expand(deep_client, FULL_TOTAL)
        assert len(body["expansion"]["contains"]) == FULL_TOTAL
        assert body["expansion"]["total"] == FULL_TOTAL
        assert not _toocostly(body)

    def test_d20_count_cap_lower_bound_total(self, deep_client):
        """count=3: contains=3 but 5 descendants exist -> total >= 4.

        This is the probe the shared fixture could NEVER express: the BFS
        cap fires with more remaining, and the +1 lower-bound contract
        (QA-057/QA-068) must produce total=len(contains)+1=4 — NOT the
        exact 6 (still deferred) and NOT the truncated 3 (the original
        bug). The toocostly extension must also fire.
        """
        body = _expand(deep_client, 3)
        assert len(body["expansion"]["contains"]) == 3
        assert body["expansion"]["total"] == 4, (
            "BFS-capped total must surface the +1 lower bound "
            f"(len(contains)+1=4); got {body['expansion']['total']}. "
            "If this reads 3 the original VS02-01 bug regressed; if it "
            "reads 6 the exact-count fix landed — tighten this probe."
        )
        assert _toocostly(body)

    def test_d30_count_one_root_only_lower_bound(self, deep_client):
        """count=1: root only shown, total=2 lower bound, extension fires."""
        body = _expand(deep_client, 1)
        assert len(body["expansion"]["contains"]) == 1
        assert body["expansion"]["contains"][0]["code"] == ROOT
        assert body["expansion"]["total"] == 2
        assert _toocostly(body)

    def test_d40_exact_count_still_lower_bound_documented(self, deep_client):
        """CF-HISTORIAN-VS02-01 remains deferred: total != exact under caps.

        Pins the DEFERRAL itself: with 6 true members and count=3, total
        is the lower bound 4, not the exact 6. When the exact-count fix
        lands (unbounded BFS or COUNT(*)), flip this to assert 6 and
        update test_d20/d30 accordingly.
        """
        body = _expand(deep_client, 3)
        assert body["expansion"]["total"] != FULL_TOTAL, (
            "total equals the exact un-truncated size — the exact-count "
            "enhancement appears to have landed; tighten d20/d30/d40."
        )

    def test_d50_intensional_compose_path_same_contract(self, deep_client):
        """The compose.include filter path (expand_intensional_value_set)
        shares the +1-probe contract — with a DIFFERENT budget shape than
        the URL path: the compose path appends root FIRST, then BFS with
        limit=count+1 (no root-slot budgeting), so a truncated expansion
        observes count+2 codes (root + count+1 descendants) at count=2.
        total = the observed lower bound 4 (not the exact 6 — the CF
        deferral — and not the truncated 2). toocostly must fire."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept",
                         "op": "is-a",
                         "value": ROOT},
                    ],
                }],
            },
        }
        r = deep_client.post(
            "/fhir/ValueSet/$expand", json=vs, params={"count": 2}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["expansion"]["contains"]) == 2
        assert body["expansion"]["total"] == 4, (
            "compose-path total must surface the observed +1-probe lower "
            f"bound (root + count+1 descendants = 4); got "
            f"{body['expansion']['total']}. If 6, the exact-count fix "
            "landed — tighten this probe."
        )
        assert _toocostly(body)
