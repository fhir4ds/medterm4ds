"""TS-64 MCP domain-search output envelopes (2026-10-19).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261019b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the OUTPUT envelope conventions of the MCP runtime's
domain-search tools (terminology_domain.terminology_search family +
the bare search_names). TS-21/29/40 covered MCP input parity, tool
inventory, and evidence-family network coupling; the envelope
CONVENTIONS (echo, truncation observability) were never audited.

MC1 (LOW-MED, NEW) — truncation is unobservable in domain-search
    envelopes. diagnosis_codes(limit=2) returns result_count=2 with
    exactly 2 rows; diagnosis_codes(limit=9) returns 3 — the fixture
    universe holds 3, so limit=2 WAS truncated, but nothing in the
    envelope says so: no truncated flag, no true count, no
    has-more. An LLM client cannot distinguish "exactly N exist"
    from "capped at N" — it will answer "there are 2 diabetes codes"
    with confidence. Same class as SC1 ($search Bundle.total =
    entry count) and VS02-01 ($expand total under caps) — this is
    that family's MCP branch. The BARE search_names envelope is
    even thinner ({results} only — no count field at all).
    Fix shape: echo the applied limit + a truncated/has_more flag
    (one extra LIMIT+1 row at the service boundary, the $expand
    "+1 probe" pattern), or result_count semantics documented as
    returned-count.
    Pinned mc10-mc12.

MC2 (LOW, informational) — envelope-shape inconsistency across the
    tool families: terminology_search helpers emit
    {query: <op-name>, condition, sources, result_count, results};
    search_names emits bare {results}; discover emits a full
    parameter echo {root, code, depth, descendants, ancestors, ...}.
    Three conventions for one surface. Not a correctness issue (each
    is self-consistent); registered for the day a clientSDK
    normalizes envelopes.
    Pinned mc20-mc22 as controls-documentation.

Controls (fresh): limit floor validation fails loud everywhere
    (0 / -1 → ValueError 'limit must be at least 1' on both tool
    families); ceiling unenforced (1001 accepted — fixture too small
    to distinguish); 'query' envelope field carries the OP NAME by
    convention (terminology.py:50 'query': query_name) while the
    user input rides 'condition' — verified not a leak of user data
    into the wrong field; sources echo lists the normalized SABs.
"""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip("medterm4ds.apps.mcp")

mcp_module = importlib.import_module("medterm4ds.apps.mcp")

SNOMED = "SNOMEDCT_US"


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts64_mcp") / "umls.duckdb"
    _make_conformance_db(db)
    rt = mcp_module.McpRuntime(mcp_module.McpSettings(db_path=db))
    rt.open()
    yield rt
    rt.close()


class TestMC1TruncationUnobservable:
    """MC1 — capped results indistinguishable from complete."""

    def test_mc10_limit2_result_count_2(self, runtime):
        """limit=2 → result_count=2: with a 3-match universe this WAS
        truncated, but the envelope asserts nothing about it. Flip
        when a truncated/has_more flag (or true count) lands."""
        out = runtime.diagnosis_codes(condition="diabetes", limit=2)
        assert out["result_count"] == 2
        assert "truncated" not in out and "has_more" not in out

    def test_mc11_limit9_reveals_true_count(self, runtime):
        """The same query with limit=9 returns 3 — proof that mc10's
        result_count=2 was a cap, not the universe size (the
        fixture-establishing control)."""
        out = runtime.diagnosis_codes(condition="diabetes", limit=9)
        assert out["result_count"] == 3

    def test_mc12_bare_search_names_thinner(self, runtime):
        """search_names emits {results} only — no count field at all;
        the LLM must count rows itself. Flip when the envelope gains
        count/truncation fields."""
        out = runtime.search_names(query="diabetes", limit=2)
        assert set(out.keys()) == {"results"}


class TestMC2EnvelopeShapes:
    """MC2 — three envelope conventions, pinned as documentation."""

    def test_mc20_terminology_search_shape(self, runtime):
        out = runtime.diagnosis_codes(condition="diabetes", limit=2)
        assert {"query", "condition", "sources",
                "result_count", "results"} <= set(out.keys())

    def test_mc21_query_field_carries_op_name(self, runtime):
        """'query' = the operation name (terminology.py convention),
        NOT the user input — the input rides 'condition'."""
        out = runtime.diagnosis_codes(condition="diabetes", limit=2)
        assert out["query"] == "diagnosis_codes"
        assert out["condition"] == "diabetes"

    def test_mc22_discover_full_echo(self, runtime):
        """discover echoes its full parameter set — the third
        convention."""
        out = runtime.discover(
            source_terminology=SNOMED, code="44054006", depth=1,
        )
        assert {"root", "code", "depth", "descendants"} <= set(
            out.keys()
        )


class TestTS64Controls:
    """Controls — limit validation held."""

    @pytest.mark.parametrize("bad", [0, -1])
    def test_c10_limit_floor_fails_loud(self, runtime, bad):
        with pytest.raises(ValueError, match="at least 1"):
            runtime.diagnosis_codes(
                condition="diabetes", limit=bad
            )

    def test_c11_search_names_floor_too(self, runtime):
        with pytest.raises(ValueError, match="at least 1"):
            runtime.search_names(query="diabetes", limit=0)

    def test_c12_sources_echo_normalized(self, runtime):
        out = runtime.diagnosis_codes(condition="diabetes", limit=2)
        assert set(out["sources"]) == {"ICD10CM", SNOMED}
