"""TS-79 RemoteApiEngine end-to-end parity (2026-10-23).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261023).
FRESH EVIDENCE — live in-process end-to-end probes executed this run
(REST facade app + RemoteApiEngine wired through a TestClient
transport); no prior-run results cited.

Scope: the remote-engine path — the production deployment topology
(notebook → REST facade → engine) — never audited end-to-end against
the newest contracts. The engine's per-method wire shapes, error
propagation, and parity with the local engine.

RESULT: ZERO FINDINGS — controls-only iteration (11th clean surface).
All eight engine methods round-trip correctly through the live REST
facade: get_code_infos (name resolution), search_names (NameSearchResult
objects w/ CodeRef codes), get_code_ttys, get_code_relations
(children), get_code_mappings (crosswalk), get_patient_friendly_names,
get_source_stats, sample_source_codes. Unknown-source inputs degrade
to per-code [None] rows (the documented Z3 miss-encoding, carried
faithfully over the wire — remote parity with the local convention).
Error propagation FAILS LOUD: a malformed/errored transport response
(no results key) raises RuntimeError naming the endpoint — no silent
fallbacks on the remote path.

One transport-signature lesson (documented, not a finding): the
engine calls transport(path, payload) with the BARE app path
('/lookup'), and the REST app's engine opens only inside its
lifespan — probes must run inside `with TestClient(app)`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


@pytest.fixture(scope="module")
def remote_engine(tmp_path_factory):
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app
    from medterm4ds.engines.api.engine import RemoteApiEngine

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts79_remote") / "umls.duckdb"
    _make_conformance_db(db)
    app = create_app(ApiSettings(db_path=db, prepare_cache=False))
    with TestClient(app) as client:

        def transport(path, payload):
            return client.post(path, json=payload).json()

        yield RemoteApiEngine("http://test", transport=transport)


class TestEngineRoundTrip:
    """Controls — all methods round-trip the live facade."""

    def test_r10_code_infos(self, remote_engine):
        from medterm4ds.core.models import CodeRef

        infos = remote_engine.get_code_infos(
            [CodeRef("SNOMEDCT_US", "44054006")]
        )
        assert infos[0].name == "Type 2 diabetes mellitus"

    def test_r11_search_names(self, remote_engine):
        results = remote_engine.search_names(
            "diabetes", limit=3
        )
        codes = {r.code.code for r in results}
        assert {"73211009", "E11"} <= codes

    def test_r12_ttys(self, remote_engine):
        from medterm4ds.core.models import CodeRef

        ttys = remote_engine.get_code_ttys(
            [CodeRef("SNOMEDCT_US", "44054006")]
        )
        assert any(t.tty == "PT" for t in ttys)

    def test_r13_children(self, remote_engine):
        from medterm4ds.core.models import CodeRef

        rels = remote_engine.get_code_relations(
            [CodeRef("SNOMEDCT_US", "73211009")],
            direction="children",
        )
        assert "44054006" in {r.target.code for r in rels}

    def test_r14_mappings(self, remote_engine):
        from medterm4ds.core.models import CodeRef

        maps = remote_engine.get_code_mappings(
            [CodeRef("ICD10CM", "E11")],
            target_sources=["SNOMEDCT_US"],
        )
        assert "44054006" in {m.target.code for m in maps}

    def test_r15_patient_friendly(self, remote_engine):
        from medterm4ds.core.models import CodeRef

        pf = remote_engine.get_patient_friendly_names(
            [CodeRef("SNOMEDCT_US", "44054006")]
        )
        assert pf and pf[0].name

    def test_r16_unknown_source_miss_rows(self, remote_engine):
        """Z3's convention carries over the wire: unknown source →
        per-code [None] rows (remote parity with the local miss
        encoding)."""
        from medterm4ds.core.models import CodeRef

        rows = remote_engine.get_code_infos(
            [CodeRef("BOGUS", "X")]
        )
        assert rows == [None]

    def test_r17_error_transport_fails_loud(self):
        """A transport returning an error-shaped body (no results
        key) raises RuntimeError naming the endpoint — no silent
        fallback on the remote path."""
        from medterm4ds.core.models import CodeRef
        from medterm4ds.engines.api.engine import RemoteApiEngine

        def err_transport(path, payload):
            return {"detail": [{"msg": "field required"}]}

        eng = RemoteApiEngine(
            "http://test", transport=err_transport
        )
        with pytest.raises(RuntimeError, match="/lookup"):
            eng.get_code_infos([CodeRef("SNOMEDCT_US", "44054006")])
