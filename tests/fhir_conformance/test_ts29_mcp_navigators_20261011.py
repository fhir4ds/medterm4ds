"""TS-29 MCP navigator + evidence tool contracts (2026-10-11).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261011c).
FRESH EVIDENCE — live probes executed this run at the McpRuntime
layer (the FastMCP layer is a thin _run_db wrapper); no prior-run
results cited.

Scope: iter-32 covered 3 MCP tools (lookup/cross_reference/
resolve_codes parity). This suite covers the rest of the 38-tool
surface: the CLINICAL NAVIGATORS (diagnosis_codes, lab_codes,
procedure_codes, vaccine_codes, search_drug, hcpcs_drugs,
lab_value_codes — DB-backed, deterministic) and the EVIDENCE family
(guideline_*, fda_label_by_rxcui, indication_search — NETWORK-backed
via PubMed E-utilities / openFDA).

NAV findings: NONE — the navigator contracts are conformant.
    Whitespace/empty queries fail loud ('query must not be empty');
    limit floors enforced ('limit must be at least 1'); result
    envelopes carry {query, echo'd args, sources, result_count,
    results}; result rows are full atoms w/ match_type taxonomy.
    This iteration PINS those contracts (controls) so drift fails.

EV1 (LOW, NEW, informational) — evidence tools are live-network.
    guideline_search hits PubMed E-utilities at CALL TIME (real
    PMIDs returned against a synthetic DB — zero local evidence
    store); indication_search/fda_label_by_rxcui hit openFDA. There
    IS a documented failure envelope (_external_error_response:
    status='error', service named, echo'd query) — pinned here
    DETERMINISTICALLY via a failing injected client, plus the live
    happy path behind a network-availability guard. Registration is
    informational: latency/availability coupling of a terminology
    MCP server to public APIs was never documented as a deployment
    consideration. Fix shape: document (or add caching/timeout
    policy) in the MCP tool descriptions.

EV2 (LOW, NEW) — tool-name/keyword mismatches invite client errors.
    drugs_for_indication(condition=...) — the tool is named
    *_indication but the keyword is *condition*; indication_search
    takes indication= not query=. Each mismatch surfaced as a raw
    TypeError to the LLM client. Fix shape: accept both spellings or
    rename the keyword to match the tool family. Pinned: e20/e21
    (the TypeError shapes) as documentation of the contract.
"""

from __future__ import annotations

import pytest

SNOMED = "SNOMEDCT_US"


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    from medterm4ds.apps.mcp import McpRuntime, McpSettings

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts29_mcp") / "umls.duckdb"
    _make_conformance_db(db)
    rt = McpRuntime(settings=McpSettings(db_path=db))
    rt.open()
    yield rt
    rt.close()


class TestNavigatorControls:
    """Controls — DB-backed navigator contracts, fresh."""

    @pytest.mark.parametrize(
        "tool,kw",
        [
            ("diagnosis_codes", {"condition": "diabetes"}),
            ("lab_codes", {"lab_test": "glucose"}),
            ("procedure_codes", {"procedure": "bypass"}),
            ("vaccine_codes", {"vaccine": "flu"}),
            ("search_drug", {"drug_name": "metformin"}),
            ("hcpcs_drugs", {"drug_name": "fluconazole"}),
            ("lab_value_codes", {"clinical_value": "severe"}),
        ],
    )
    def test_n10_envelope_shape(self, runtime, tool, kw):
        fn = getattr(runtime, tool)
        out = fn(**kw)
        assert out["query"] == tool
        for arg, val in kw.items():
            assert out[arg] == val
        assert "sources" in out
        assert out["result_count"] == len(out["results"])

    def test_n11_result_atoms(self, runtime):
        out = runtime.diagnosis_codes(condition="diabetes")
        row = out["results"][0]
        for field in (
            "source", "code", "name", "cui", "aui", "tty",
            "match_type",
        ):
            assert field in row

    @pytest.mark.parametrize(
        "tool,kw",
        [
            ("diagnosis_codes", {"condition": "   "}),
            ("diagnosis_codes", {"condition": ""}),
            ("search_drug", {"drug_name": "   "}),
        ],
    )
    def test_n12_whitespace_empty_rejected(self, runtime, tool, kw):
        fn = getattr(runtime, tool)
        with pytest.raises(ValueError, match="must not be empty"):
            fn(**kw)

    @pytest.mark.parametrize("bad", [0, -5])
    def test_n13_limit_floor(self, runtime, bad):
        with pytest.raises(ValueError, match="at least 1"):
            runtime.vaccine_codes(vaccine="flu", limit=bad)


class TestEV1EvidenceNetwork:
    """EV1 — network-backed evidence tools; deterministic pins."""

    def test_e10_offline_error_envelope(self, runtime, monkeypatch):
        """A failing client (offline shape) yields the DOCUMENTED
        error envelope: status=error, the external service named,
        the query echoed — not a traceback."""
        from medterm4ds.domains import evidence as ev

        class _Failing:
            def search_guidelines(self, *a, **kw):
                raise ConnectionError("no route to host")

        monkeypatch.setattr(
            ev, "PubMedGuidelineClient", lambda: _Failing()
        )
        out = ev.guideline_search("diabetes")
        assert out["status"] == "error"
        assert "PubMed" in out["source"]
        assert out["search"] == "diabetes"
        assert out["results"] == []

    def test_e11_live_happy_path(self, runtime):
        """The live PubMed path (network-guarded: skipped when the
        box is offline — fresh evidence when it runs)."""
        import socket

        try:
            socket.create_connection(
                ("eutils.ncbi.nlm.nih.gov", 443), timeout=5
            ).close()
        except OSError:
            pytest.skip("offline — live PubMed path not probeable")

        out = runtime.guideline_search(query="diabetes")
        assert out["status"] == "ok"
        assert out["result_count"] > 0
        assert out["results"][0]["pmid"]


class TestEV2KeywordMismatches:
    """EV2 — tool-name/keyword mismatches documented."""

    def test_e20_drugs_for_indication_condition_kw(self, runtime):
        """The tool is drugs_for_INDICATION but the keyword is
        condition= — an LLM client guessing indication= gets a raw
        TypeError. Pinned as the contract's documentation."""
        with pytest.raises(TypeError):
            runtime.drugs_for_indication(indication="diabetes")

    def test_e21_indication_search_kw(self, runtime):
        """indication_search takes indication= not query=."""
        with pytest.raises(TypeError):
            runtime.indication_search(query="diabetes")
