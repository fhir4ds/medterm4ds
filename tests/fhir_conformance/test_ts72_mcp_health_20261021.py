"""TS-72 MCP runtime health/lifecycle honesty (2026-10-21).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261021b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the MCP runtime's health() lifecycle contract — never audited
(TS-21/29/40/64 covered tools; the runtime lifecycle itself not).

HH1 (LOW, NEW) — health() reports `sources` from SETTINGS, not from
    the engine: a never-opened runtime (engine None, DB never
    touched) lists all 9 configured sources, and a CLOSED runtime
    keeps listing them under status='starting'. The field reads as a
    live inventory but is the configured inventory (mcp.py:135
    'sources': list(self.settings.sources)). A monitoring client
    cannot distinguish 'serving these sources' from 'configured for
    these sources'. Fix shape: report sources from the live engine
    when ready, [] (or null) when not, or rename the field
    configured_sources. Pinned hh10/hh11.

Controls (fresh): status/ready transitions ok→starting on close;
    reopen() restores ready=True; cache_prepared correctly reflects
    the engine (True open, False closed/never-opened — the field
    health() gets RIGHT, contrast for hh10).
"""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip("medterm4ds.apps.mcp")

mcp_module = importlib.import_module("medterm4ds.apps.mcp")


@pytest.fixture()
def runtime(tmp_path):
    from .conftest import _make_conformance_db

    db = tmp_path / "umls.duckdb"
    _make_conformance_db(db)
    rt = mcp_module.McpRuntime(mcp_module.McpSettings(db_path=db))
    yield rt
    if rt.ready:
        rt.close()


class TestHH1SourcesFromSettings:
    """HH1 — configured inventory presented as live inventory."""

    def test_hh10_never_opened_lists_sources(self, runtime):
        """engine is None (DB never touched) yet health() lists all
        configured sources. Flip when sources report live-engine
        state (or the field is renamed configured_sources)."""
        h = runtime.health()
        assert h["ready"] is False
        assert h["status"] == "starting"
        assert h["sources"]  # populated without any DB connection

    def test_hh11_closed_keeps_sources(self, runtime):
        """After close(): status reverts to 'starting' but the
        sources list persists (stale for a not-serving runtime)."""
        runtime.open()
        runtime.close()
        h = runtime.health()
        assert h["ready"] is False
        assert h["status"] == "starting"
        assert h["sources"]


class TestTS72Controls:
    """Controls — lifecycle mechanics held."""

    def test_c10_open_close_status(self, runtime):
        runtime.open()
        h = runtime.health()
        assert h["status"] == "ok"
        assert h["ready"] is True
        runtime.close()
        h2 = runtime.health()
        assert h2["status"] == "starting"
        assert h2["ready"] is False

    def test_c11_reopen(self, runtime):
        runtime.open()
        runtime.close()
        runtime.open()
        assert runtime.ready is True

    def test_c12_cache_prepared_reflects_engine(self, runtime):
        """cache_prepared is the field health() gets right — False
        when engine is None, the engine's actual state when open
        (contrast control for hh10)."""
        assert runtime.health()["cache_prepared"] is False
        runtime.open()
        assert isinstance(runtime.health()["cache_prepared"], bool)
