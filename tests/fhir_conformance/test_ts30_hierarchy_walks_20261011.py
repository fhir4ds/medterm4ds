"""TS-30 hierarchy walk contracts (2026-10-11).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261011d).
FRESH EVIDENCE — live probes executed this run against the REST
facade (with a purpose-built 3-level chain fixture) and the CLI; no
prior-run results cited.

Scope: the hierarchy walk semantics — depth caps, layered BFS
attribution, direction parity — plus the unknown-input matrix on
/hierarchy (REST + CLI).

HY1 (LOW-MED, NEW) — unknown source walks to empty at 200.
    /hierarchy with a typo'd SAB returns 200 {"results": []} (REST
    and CLI alike) — indistinguishable from a legitimate childless
    concept in a valid vocabulary. The QC-195 source-existence
    boundary is held by /sample-codes, /source-stats, /map and
    /optimize (all 400) but absent on hierarchy. With BL1 (bulk) and
    CLI3/Z3 (lookup), the unknown-source matrix now spans 400 /
    null-record / empty-walk — three conventions for one question.
    Fix shape: the same sources-list consult the sibling endpoints
    use → 400 'source not found in this database'.
    Pinned: h10/h11.

HY2 (LOW, NEW) — unknown code walks to empty at 200.
    An unknown CODE in a VALID system also returns 200 [] — a void
    concept looks like a leaf. The hierarchy member of the miss-shape
    matrix (contrast /lookup's null entry, /patient-friendly's
    identifier echo). Defensible as "no descendants of nothing", but
    never documented. Fix shape: document, or distinguish via a
    not-found marker. Pinned: h20.

Controls (fresh, 3-level chain fixture 73211009 → L1 → L2 → L3):
    DEPTH CAPS EXACT — max_depth=1/2/3/5 return exactly the layers
    ≤ cap with correct per-node depth attribution (QC-432 held);
    ANCESTORS mirror (ancestors d2 of L3 = [L2@1, L1@2]);
    max_depth floor (0 → 422 pydantic); direction enum-gated;
    full relation row shape (source/target codes+displays+cuis+auis,
    rel/rela, relationship, depth).
"""

from __future__ import annotations

import duckdb
import pytest

SNOMED = "SNOMEDCT_US"
ROOT = "73211009"
L1, L2, L3 = "20000001", "20000002", "20000003"


@pytest.fixture(scope="module")
def deep_client(tmp_path_factory):
    """REST client on the shared fixture + a 3-level chain."""
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts30_hier") / "umls.duckdb"
    _make_conformance_db(db)
    con = duckdb.connect(str(db))
    for code, name, cui in [
        (L1, "Level1", "C10000001"),
        (L2, "Level2", "C10000002"),
        (L3, "Level3", "C10000003"),
    ]:
        con.execute(
            "INSERT INTO mrconso VALUES (?,?,?,?,'N',?,?)",
            [code, "PT", name, f"A{code}", SNOMED, cui],
        )
    for parent, child in [
        (ROOT, L1),
        (L1, L2),
        (L2, L3),
    ]:
        con.execute(
            "INSERT INTO mrrel VALUES (?,?,?,?)",
            [f"A{child}", f"A{parent}", "isa", "PAR"],
        )
    con.close()
    app = create_app(ApiSettings(db_path=db))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _descendants(client, code: str, depth: int):
    r = client.post(
        "/hierarchy",
        json={
            "codes": [{"source": SNOMED, "code": code}],
            "direction": "descendants",
            "max_depth": depth,
        },
    )
    assert r.status_code == 200
    return {(row["target_code"], row["depth"]) for row in r.json()["results"]}


class TestHY1UnknownSource:
    """HY1 — typo'd SAB walks to empty at 200."""

    def test_h10_rest_unknown_source_empty(self, deep_client):
        """Unknown source → 200 [] (vs the 400s of sample-codes/
        source-stats/map/optimize). Flip when the QC-195 boundary
        reaches hierarchy."""
        r = deep_client.post(
            "/hierarchy",
            json={
                "codes": [{"source": "NOTASAB", "code": "1"}],
                "direction": "descendants",
            },
        )
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_h11_cli_parity(self, tmp_path):
        """CLI descendants on an unknown source → results [] exit 0
        (same gap, both surfaces)."""
        import os
        import subprocess
        import sys
        from pathlib import Path

        from .conftest import _make_conformance_db

        db = tmp_path / "umls.duckdb"
        _make_conformance_db(db)
        cli = str(Path(sys.executable).parent / "medterm4ds")
        r = subprocess.run(
            [
                cli,
                "hierarchy",
                "descendants",
                "--db",
                str(db),
                "--source",
                "NOTASAB",
                "--code",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert r.returncode == 0
        assert '"results": []' in r.stdout


class TestHY2UnknownCode:
    """HY2 — void concept indistinguishable from a leaf."""

    def test_h20_unknown_code_empty(self, deep_client):
        r = deep_client.post(
            "/hierarchy",
            json={
                "codes": [{"source": SNOMED, "code": "99999999"}],
                "direction": "descendants",
            },
        )
        assert r.status_code == 200
        assert r.json()["results"] == []


class TestHierarchyControls:
    """Controls — depth semantics on the chain fixture, fresh."""

    @pytest.mark.parametrize(
        "cap,expected",
        [
            (1, {(L1, 1), ("44054006", 1)}),
            (2, {(L1, 1), (L2, 2), ("44054006", 1)}),
            (3, {(L1, 1), (L2, 2), (L3, 3), ("44054006", 1)}),
            (5, {(L1, 1), (L2, 2), (L3, 3), ("44054006", 1)}),
        ],
    )
    def test_c10_depth_caps_exact(self, deep_client, cap, expected):
        """Layered BFS: exactly the layers ≤ cap, correct depth
        attribution per node (QC-432 held)."""
        assert _descendants(deep_client, ROOT, cap) == expected

    def test_c11_ancestors_mirror(self, deep_client):
        r = deep_client.post(
            "/hierarchy",
            json={
                "codes": [{"source": SNOMED, "code": L3}],
                "direction": "ancestors",
                "max_depth": 2,
            },
        )
        got = {(row["target_code"], row["depth"]) for row in r.json()["results"]}
        assert got == {(L2, 1), (L1, 2)}

    def test_c12_depth_floor(self, deep_client):
        r = deep_client.post(
            "/hierarchy",
            json={
                "codes": [{"source": SNOMED, "code": ROOT}],
                "direction": "descendants",
                "max_depth": 0,
            },
        )
        assert r.status_code == 422

    def test_c13_direction_enum(self, deep_client):
        r = deep_client.post(
            "/hierarchy",
            json={
                "codes": [{"source": SNOMED, "code": ROOT}],
                "direction": "sideways",
            },
        )
        assert r.status_code == 422

    def test_c14_row_shape(self, deep_client):
        r = deep_client.post(
            "/hierarchy",
            json={
                "codes": [{"source": SNOMED, "code": ROOT}],
                "direction": "descendants",
                "max_depth": 1,
            },
        )
        row = r.json()["results"][0]
        for field in (
            "source",
            "code",
            "source_display",
            "target_source",
            "target_code",
            "target_display",
            "relationship",
            "depth",
            "rel",
            "rela",
            "source_cui",
            "target_cui",
        ):
            assert field in row
