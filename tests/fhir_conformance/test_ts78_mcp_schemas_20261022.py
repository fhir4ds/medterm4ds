"""TS-78 MCP tool-schema quality (LLM-facing contract) (2026-10-22).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261022d).
FRESH EVIDENCE — live schema introspection executed this run; no
prior-run results cited.

Scope: the FastMCP tool DECLARATION layer an LLM client sees (tool
descriptions, parameter schemas, required markers). TS-29/40/64
covered runtime behavior; the schema layer was never audited. EV2's
keyword-mismatch finding (raw TypeErrors when the LLM guesses a
param name) is exactly the failure this layer exists to prevent.

MS1 (MEDIUM, NEW) — ZERO of 142 tool parameters carry descriptions.
    Every tool has a top-level description (38/38), but the parameter
    schemas expose bare names + types: 0/142 params documented. An
    LLM client must guess semantics from names alone — for this
    surface that means guessing e.g. `condition` vs `indication`
    (drugs_for_indication takes condition=, indication_search takes
    indication= — the exact EV2 confusion), `from_source`/`to_sources`
    SAB spellings, resolve_mode values, tty_filters format. EV2's
    TypeErrors are the visible cost; subtler wrong-argument
    successes (silent default use) are worse. Fix shape: pydantic
    Field(description=...) on the parameter models (or docstring
    parsing if FastMCP supports it — check; the descriptions live in
    the method docstrings today and are NOT being lifted).
    Pinned ms10-ms12.

Controls (fresh): all 38 tools carry top-level descriptions (the
    tool-level layer is healthy); required markers are present and
    match the runtime signatures (spot-checked: lookup_code requires
    code+source; drugs_for_indication requires condition); the
    schema param sets match the McpRuntime method signatures (no
    schema/runtime drift — the drugs_for_indication multi-param set
    is the real runtime signature, verified against source).
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

pytest.importorskip("medterm4ds.apps.mcp")

mcp_module = importlib.import_module("medterm4ds.apps.mcp")


@pytest.fixture(scope="module")
def tool_catalog(tmp_path_factory):
    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts78_schema") / "umls.duckdb"
    _make_conformance_db(db)
    rt = mcp_module.McpRuntime(mcp_module.McpSettings(db_path=db))
    server = mcp_module.create_mcp_server(runtime=rt)
    tools = asyncio.run(server.list_tools())
    catalog = {}
    for t in tools:
        params = getattr(t, "parameters", None)
        schema = (
            params.model_json_schema()
            if params is not None and hasattr(params, "model_json_schema")
            else (params if isinstance(params, dict) else None)
        )
        catalog[t.name] = {
            "description": t.description or "",
            "schema": schema or {},
        }
    return catalog


class TestMS1ParamDescriptionsAbsent:
    """MS1 — 0/142 params documented."""

    def test_ms10_zero_param_descriptions(self, tool_catalog):
        """THE FINDING: not one tool parameter carries a description.
        Flip when pydantic Field descriptions land (target: 100% of
        required params, then optional)."""
        total = 0
        documented = 0
        for _name, entry in tool_catalog.items():
            for _pname, pdef in (
                entry["schema"].get("properties", {}) or {}
            ).items():
                total += 1
                if isinstance(pdef, dict) and pdef.get("description"):
                    documented += 1
        assert total >= 100  # the surface is substantial
        assert documented == 0

    def test_ms11_ev2_pair_undocumented(self, tool_catalog):
        """The EV2 confusion pair stays guesswork: condition vs
        indication semantics are invisible in the schemas."""
        d = tool_catalog["drugs_for_indication"]["schema"]["properties"]
        i = tool_catalog["indication_search"]["schema"]["properties"]
        assert "condition" in d and "indication" in i
        assert not d["condition"].get("description")
        assert not i["indication"].get("description")

    def test_ms12_required_markers_present(self, tool_catalog):
        """Required params ARE marked (the layer that works —
        contrast control for ms10)."""
        lk = tool_catalog["lookup_code"]["schema"]
        assert set(lk.get("required", [])) >= {"code", "source"}


class TestTS78Controls:
    """Controls — the healthy half of the layer."""

    def test_c10_all_tools_described(self, tool_catalog):
        assert len(tool_catalog) >= 38
        for name, entry in tool_catalog.items():
            assert entry["description"].strip(), name

    def test_c11_schema_matches_runtime(self, tool_catalog):
        """Spot check: the multi-param drugs_for_indication schema
        equals the McpRuntime method signature (no drift between the
        declared and executable layers)."""
        import inspect

        rt = mcp_module.McpRuntime
        sig = inspect.signature(rt.drugs_for_indication)
        runtime_params = set(sig.parameters.keys()) - {"self"}
        schema_params = set(
            tool_catalog["drugs_for_indication"]["schema"][
                "properties"
            ].keys()
        )
        assert schema_params == runtime_params
