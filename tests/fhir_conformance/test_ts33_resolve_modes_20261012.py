"""TS-33 resolve-mode semantics (2026-10-12).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261012c).
FRESH EVIDENCE — live probes executed this run against a retired-code
fixture (RXNORM 1111111 SUPPRESS=O replaced-by 2222222 active; the
shared builder's schema: mrconso CODE,TTY,STR,AUI,SUPPRESS,SAB,CUI
and mrrel AUI1,AUI2,RELA,REL); no prior-run results cited.

Scope: the 3-mode resolve contract (active_only / historical /
resolve_current) on /lookup and /resolve — the clinical
retired-code semantics, never pinned. TS-25 registered the knob's
ABSENCE on /resolve (RS1); this suite pins what the modes DO.

FINDINGS: NONE new — the mode semantics are conformant and precise.
A controls iteration pinning:
    LOOKUP 3-MODE MATRIX on a retired code: active_only → null;
    historical → the old record (suppress=O surfaced);
    resolve_current → the REPLACEMENT record (QC-495 verified live).
    RESOLVE REPLACEMENT: match_type=historical_replacement,
    resolved code/display of the replacement, provenance chain.
    AMBIGUITY: two replacements → status=ambiguous,
    resolved_code=null, BOTH candidates listed (no arbitrary pick).
    RELA gating: only documented _REPLACEMENT_RELAS edges resolve
    ('replacement_of' does not — the negative case pinned).
"""

from __future__ import annotations

import duckdb
import pytest

RXNORM = "RXNORM"
OLD, NEW, ALT = "1111111", "2222222", "3333333"


def _seed(db_path, with_alt: bool):
    con = duckdb.connect(str(db_path))
    con.execute(
        "INSERT INTO mrconso VALUES (?,?,?,?,?,'RXNORM',?)",
        [OLD, "SCD", "Old Drug Name", f"A{OLD}", "O", "C2111111"],
    )
    con.execute(
        "INSERT INTO mrconso VALUES (?,?,?,?,?,'RXNORM',?)",
        [NEW, "SCD", "New Drug Name", f"A{NEW}", "N", "C2222222"],
    )
    con.execute(
        "INSERT INTO mrrel VALUES (?,?,?,?)",
        [f"A{OLD}", f"A{NEW}", "replaced_by", "RXREL"],
    )
    if with_alt:
        con.execute(
            "INSERT INTO mrconso VALUES (?,?,?,?,?,'RXNORM',?)",
            [ALT, "SCD", "Alt Drug", f"A{ALT}", "N", "C3333333"],
        )
        con.execute(
            "INSERT INTO mrrel VALUES (?,?,?,?)",
            [f"A{OLD}", f"A{ALT}", "replaced_by", "RXREL"],
        )
    con.close()


@pytest.fixture()
def app_client(tmp_path):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    from .conftest import _make_conformance_db

    db = tmp_path / "umls.duckdb"
    _make_conformance_db(db)
    _seed(db, with_alt=False)
    app = create_app(ApiSettings(db_path=db))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def app_client_ambiguous(tmp_path):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    from .conftest import _make_conformance_db

    db = tmp_path / "umls.duckdb"
    _make_conformance_db(db)
    _seed(db, with_alt=True)
    app = create_app(ApiSettings(db_path=db))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestLookupModeMatrix:
    """Controls — the 3-mode contract on a retired code."""

    @pytest.mark.parametrize(
        "mode,expect_name,expect_code",
        [
            ("active_only", None, None),
            ("historical", "Old Drug Name", OLD),
            ("resolve_current", "New Drug Name", NEW),
        ],
    )
    def test_r10_mode_semantics(
        self, app_client, mode, expect_name, expect_code
    ):
        r = app_client.post(
            "/lookup",
            json={
                "codes": [{"source": RXNORM, "code": OLD}],
                "resolve_mode": mode,
            },
        )
        rec = r.json()["results"][0]
        if expect_name is None:
            assert rec is None
        else:
            assert rec["name"] == expect_name
            assert rec["code"] == expect_code

    def test_r11_default_active_only(self, app_client):
        """Omitted mode = active_only (the documented default)."""
        r = app_client.post(
            "/lookup",
            json={"codes": [{"source": RXNORM, "code": OLD}]},
        )
        assert r.json()["results"][0] is None

    def test_r12_historical_surfaces_suppress(self, app_client):
        """The historical record carries suppress='O' — the retired
        flag is visible, not laundered."""
        r = app_client.post(
            "/lookup",
            json={
                "codes": [{"source": RXNORM, "code": OLD}],
                "resolve_mode": "historical",
            },
        )
        assert r.json()["results"][0]["suppress"] == "O"


class TestResolveReplacement:
    """Controls — /resolve's replacement semantics."""

    def test_r20_single_replacement(self, app_client):
        r = app_client.post(
            "/resolve", json={"codes": [{"source": RXNORM, "code": OLD}]}
        )
        rec = r.json()["results"][0]
        assert rec["status"] == "replaced"
        assert rec["match_type"] == "historical_replacement"
        assert rec["resolved_code"] == NEW
        assert rec["resolved_display"] == "New Drug Name"

    def test_r21_provenance_chain(self, app_client):
        r = app_client.post(
            "/resolve", json={"codes": [{"source": RXNORM, "code": OLD}]}
        )
        rec = r.json()["results"][0]
        assert rec["matched_via"]["strategy"] == "historical_replacement"
        assert rec["matched_via"]["steps"]

    def test_r22_ambiguous_no_pick(self, app_client_ambiguous):
        """Two replacements: status=ambiguous, resolved_code=null,
        BOTH candidates listed — no arbitrary winner."""
        r = app_client_ambiguous.post(
            "/resolve", json={"codes": [{"source": RXNORM, "code": OLD}]}
        )
        rec = r.json()["results"][0]
        assert rec["status"] == "ambiguous"
        assert rec["match_type"] == "multiple_historical_replacements"
        assert rec["resolved_code"] is None
        codes = sorted(c["code"] for c in rec["candidates"])
        assert codes == sorted([NEW, ALT])


class TestRelaGating:
    """Controls — only documented replacement RELAs resolve."""

    def test_r30_undocumented_rela_no_resolve(self, tmp_path):
        """A 'replacement_of' edge (not in _REPLACEMENT_RELAS) does
        NOT resolve — the negative case; the resolver falls back to
        historical_exact (the old record, no replacement)."""
        pytest.importorskip("fastapi")
        from starlette.testclient import TestClient

        from medterm4ds.apps.api import ApiSettings, create_app

        from .conftest import _make_conformance_db

        db = tmp_path / "umls.duckdb"
        _make_conformance_db(db)
        con = duckdb.connect(str(db))
        con.execute(
            "INSERT INTO mrconso VALUES (?,?,?,?,?,'RXNORM',?)",
            [OLD, "SCD", "Old Drug Name", f"A{OLD}", "O", "C2111111"],
        )
        con.execute(
            "INSERT INTO mrconso VALUES (?,?,?,?,?,'RXNORM',?)",
            [NEW, "SCD", "New Drug Name", f"A{NEW}", "N", "C2222222"],
        )
        con.execute(
            "INSERT INTO mrrel VALUES (?,?,?,?)",
            [f"A{OLD}", f"A{NEW}", "replacement_of", "RXREL"],
        )
        con.close()
        app = create_app(ApiSettings(db_path=db))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/resolve",
                json={"codes": [{"source": RXNORM, "code": OLD}]},
            )
            rec = r.json()["results"][0]
            assert rec["match_type"] == "historical_exact"
            assert rec["resolved_code"] == OLD  # self, no replacement
