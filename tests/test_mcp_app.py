from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("fastmcp")

from medterm4ds.apps.mcp import McpRuntime, McpSettings, build_code_refs, create_mcp_server


def _mcp_tool_names(server) -> set[str]:
    if hasattr(server, "list_tools"):
        tools = server.list_tools()
        if inspect.isawaitable(tools):
            tools = asyncio.run(tools)
    else:
        tools = server._tool_manager.list_tools()
    return {tool.name for tool in tools}


def _make_duckdb(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE mrconso (
                CODE VARCHAR,
                TTY VARCHAR,
                STR VARCHAR,
                AUI VARCHAR,
                SUPPRESS VARCHAR,
                SAB VARCHAR,
                CUI VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mrrel (
                AUI1 VARCHAR,
                AUI2 VARCHAR,
                RELA VARCHAR,
                REL VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("E11.9", "PT", "Type 2 diabetes mellitus", "ICD_E119", "N", "ICD10CM", "C_DIAB"),
                ("E11", "PT", "Type 2 diabetes mellitus", "ICD_E11", "N", "ICD10CM", "C_E11"),
                ("44054006", "PT", "Diabetes mellitus type 2", "SNOMED_DIAB", "N", "SNOMEDCT_US", "C_DIAB"),
                ("D_DIAB", "MH", "Diabetes", "MP_DIAB", "N", "MEDLINEPLUS", "C_DIAB"),
                ("208", "PT", "COVID-19 Vaccine", "CVX_208", "N", "CVX", "C_CVX"),
            ],
        )
        con.executemany(
            "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
            [
                ("ICD_E119", "ICD_E11", "isa", "PAR"),
            ],
        )
    finally:
        con.close()


def _settings(db_path: Path, *, prepare_cache: bool = True) -> McpSettings:
    return McpSettings(
        db_path=db_path,
        sources=("ICD10CM", "CVX"),
        memory_profile="low",
        prepare_cache=prepare_cache,
    )


def test_mcp_runtime_patient_friendly_tools(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    runtime = McpRuntime(_settings(db_path, prepare_cache=True))

    runtime.open()
    try:
        assert runtime.health()["cache_prepared"] is True

        single = runtime.patient_friendly_name(code="E11.9", source="ICD10CM")
        assert single["name"] == "Diabetes"
        assert single["match_type"] == "exact"

        lookup = runtime.lookup_code(code="E11.9", source="ICD10-CM")
        assert lookup["name"] == "Type 2 diabetes mellitus"
        assert lookup["source"] == "ICD10CM"

        batch = runtime.patient_friendly_names(
            codes=["E11.9", "208"],
            sources=["ICD10CM", "CVX"],
        )
        assert [row["name"] for row in batch["results"]] == ["Diabetes", "COVID-19 Vaccine"]

        lookup_batch = runtime.lookup_codes(
            codes=["E11.9", "NOPE"],
            sources=["ICD10CM", "CVX"],
        )
        assert [row["name"] if row else None for row in lookup_batch["results"]] == [
            "Type 2 diabetes mellitus",
            None,
        ]

        parents = runtime.parents(codes=["E11.9"], sources=["ICD10CM"])
        assert parents["results"][0]["target_code"] == "E11"
        assert parents["results"][0]["relationship"] == "parent"

        generic = runtime.code_relations(
            codes=["E11.9"],
            sources=["ICD10CM"],
            direction="ancestors",
        )
        assert [(row["target_code"], row["relationship"]) for row in generic["results"]] == [
            ("E11", "ancestor")
        ]
    finally:
        runtime.close()

    assert runtime.health()["ready"] is False


def test_mcp_runtime_concept_map_tool(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    runtime = McpRuntime(_settings(db_path, prepare_cache=False))

    runtime.open()
    try:
        rows = runtime.patient_friendly_concept_map(
            codes=["E11.9"],
            sources=["ICD10CM"],
        )["results"]
    finally:
        runtime.close()

    assert rows[0]["source"] == "ICD10CM"
    assert rows[0]["code"] == "E11.9"
    assert rows[0]["target_display"] == "Diabetes"
    assert rows[0]["relationship"] == "equivalent"


def test_mcp_runtime_discovery_tools(tmp_path, monkeypatch):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    runtime = McpRuntime(_settings(db_path, prepare_cache=False))
    monkeypatch.setattr(
        "medterm4ds.apps.mcp.evidence_domain.fda_label_by_rxcui",
        lambda rxcui: {
            "query": "fda_label_by_rxcui",
            "status": "ok",
            "rxcui": rxcui,
            "result_count": 0,
            "results": [],
        },
    )

    runtime.open()
    try:
        source_rows = runtime.source_stats(sources=["ICD10-CM", "CVX"])["results"]
        sample_rows = runtime.sample_codes(sources=["ICD10CM", "CVX"], per_source=1)["results"]
        tty_rows = runtime.code_ttys(codes=["E11.9"], sources=["ICD10CM"])["results"]
        search_rows = runtime.search_names(
            query="diabetes",
            sources=["ICD10CM", "MEDLINEPLUS"],
            tty_filters=["MH"],
        )["results"]
        diagnosis_rows = runtime.diagnosis_codes(condition="diabetes", limit=5)["results"]
        xref_rows = runtime.cross_reference(
            code="E11.9",
            from_source="ICD10CM",
            to_sources=["SNOMED"],
        )["results"]
        resolved_rows = runtime.resolve_codes(
            codes=["E11.9"],
            sources=["ICD10CM"],
        )["results"]
        evidence = runtime.fda_label_by_rxcui(rxcui="12345")
    finally:
        runtime.close()

    assert source_rows == [
        {"source": "CVX", "code_count": 1, "atom_count": 1},
        {"source": "ICD10CM", "code_count": 2, "atom_count": 2},
    ]
    assert [(row["source"], row["code"]) for row in sample_rows] == [
        ("CVX", "208"),
        ("ICD10CM", "E11"),
    ]
    assert [row["tty"] for row in tty_rows] == ["PT"]
    assert [(row["source"], row["code"], row["match_type"]) for row in search_rows] == [
        ("MEDLINEPLUS", "D_DIAB", "exact")
    ]
    assert {row["source"] for row in diagnosis_rows} == {"ICD10CM", "SNOMEDCT_US"}
    assert xref_rows[0]["target_source"] == "SNOMEDCT_US"
    assert resolved_rows[0]["status"] == "active"
    assert evidence["status"] == "ok"


def test_mcp_runtime_map_codes_tool(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    runtime = McpRuntime(_settings(db_path, prepare_cache=False))

    runtime.open()
    try:
        rows = runtime.map_codes(
            codes=["E11.9", "208"],
            sources=["ICD10CM", "CVX"],
            target_sources=["SNOMED"],
        )["results"]
    finally:
        runtime.close()

    assert [(row["source"], row["code"], row["target_source"], row["target_code"]) for row in rows] == [
        ("ICD10CM", "E11.9", "SNOMEDCT_US", "44054006")
    ]
    assert rows[0]["match_type"] == "same_cui"


def test_mcp_server_registers_expected_tools(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    server = create_mcp_server(_settings(db_path, prepare_cache=False))

    tool_names = _mcp_tool_names(server)

    assert {
        "health",
        "code_ttys",
        "cross_reference",
        "diagnosis_codes",
        "discover",
        "drugs_by_class",
        "drugs_for_indication",
        "fda_label_by_rxcui",
        "guideline_fulltext",
        "guideline_recommendations",
        "guideline_search",
        "guidelines_for_code",
        "hcpcs_drugs",
        "indication_search",
        "lab_codes",
        "lab_value_codes",
        "lookup_code",
        "lookup_codes",
        "map_codes",
        "optimize",
        "procedure_codes",
        "resolve_codes",
        "sample_codes",
        "search_names",
        "search_drug",
        "source_stats",
        "sources",
        "vaccine_codes",
        "code_relations",
        "get_ancestors",
        "get_children",
        "get_descendants",
        "get_parents",
        "patient_friendly_name",
        "patient_friendly_names",
        "patient_friendly_concept_map",
    }.issubset(tool_names)


def test_code_refs_accepts_one_source_for_many_codes():
    refs = build_code_refs(["E11.9", "E10.9"], ["ICD10CM"])

    assert [(ref.source, ref.code) for ref in refs] == [
        ("ICD10CM", "E11.9"),
        ("ICD10CM", "E10.9"),
    ]


def test_code_refs_validates_lengths():
    with pytest.raises(ValueError, match="sources must contain"):
        build_code_refs(["E11.9", "208"], ["ICD10CM", "CVX", "RXNORM"])


def test_code_refs_rejects_none_codes():
    """QC-050 (MEDIUM): codes=None must raise TypeError, not leak
    'object of type NoneType has no len()' raw TypeError."""
    with pytest.raises(TypeError):
        build_code_refs(codes=None, sources=["ICD10CM"])  # type: ignore[arg-type]


def test_code_refs_rejects_empty_codes_symmetric_with_empty_sources():
    """QC-060 (MEDIUM): empty codes must raise ValueError, symmetric with
    the existing empty-sources ValueError. Pre-fix empty-codes silently
    returned [] while empty-sources raised."""
    with pytest.raises(ValueError, match="codes must not be empty"):
        build_code_refs(codes=[], sources=["ICD10CM"])
    with pytest.raises(ValueError, match="sources must not be empty"):
        build_code_refs(codes=["E11.9"], sources=[])


def test_code_refs_rejects_none_entry_in_codes():
    """QC-052 (MEDIUM): a single None entry in codes must surface a clear
    TypeError pointing at the bad entry, not leak CodeRef's internal
    'CodeRef.code must be a string, got None' error that fails the batch."""
    with pytest.raises(TypeError, match="each code must be a string"):
        build_code_refs(codes=["44054006", None], sources=["SNOMEDCT_US"])  # type: ignore[list-item]


# =============================================================================
# Regression: single-code MCP tools validate code/source before CodeRef.
# Found by QC-078 (MEDIUM) + QC-086 (MEDIUM): patient_friendly_name /
# cross_reference / optimize / guidelines_for_code construct CodeRef
# directly, bypassing build_code_refs validation. Pre-fix, CodeRef's raw
# TypeError 'CodeRef.code must be a string, got NoneType' leaked.
# =============================================================================


def test_mcp_single_code_tools_reject_none_inputs(tmp_path):
    """QC-078/QC-086 (MEDIUM): single-code MCP tools raise clean TypeError
    with a clear message ('code must be a string') rather than the raw
    CodeRef.__post_init__ error."""
    from medterm4ds.apps.mcp import _validate_single_code_inputs

    with pytest.raises(TypeError, match="code must be a string"):
        _validate_single_code_inputs(code=None, source="ICD10CM")
    with pytest.raises(TypeError, match="source must be a string"):
        _validate_single_code_inputs(code="E11.9", source=None)
    with pytest.raises(TypeError, match="code must be a string"):
        _validate_single_code_inputs(code=42, source="ICD10CM")
    # Valid inputs pass without raising.
    _validate_single_code_inputs(code="E11.9", source="ICD10CM")



# =============================================================================
# Filter-then-limit regression (same bug class the CLI had): result_types
# must be forwarded to the SERVICE so `count` caps the filtered set — the
# former tool post-filtered after truncation and silently discarded slots.
# =============================================================================


def _fake_search_results():
    from types import SimpleNamespace

    def make(code, display, category, result_type, score):
        return SimpleNamespace(
            to_dict=lambda _c=code, _d=display, _cat=category, _rt=result_type, _s=score: {
                "code": _c, "display": _d, "category": _cat,
                "result_type": _rt, "score": _s,
            },
            category=category,
            result_type=result_type,
        )

    # Medication ranks first; a client-side post-filter with count=1 would
    # drop it and return zero labs.
    return [
        make("6809", "metformin", "medication", "medication", 0.99),
        make("LP15098-4", "Potassium", "lab", "lab", 0.98),
    ]


def test_mcp_search_result_types_forwarded_not_postfiltered(tmp_path, monkeypatch):
    """`search(result_types=['lab'], count=1)` must return the one lab result
    AND the service must receive result_types (service-side filtering)."""
    import asyncio

    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    runtime = McpRuntime(_settings(db_path, prepare_cache=False))
    runtime.open()
    captured: dict = {}

    def fake_service(query, *, mode=None, sources=None, count=None,
                     result_types=None, engine=None):
        captured.update(
            mode=mode, count=count, result_types=result_types,
        )
        if result_types:
            return [r for r in _fake_search_results()
                    if r.category in result_types or r.result_type in result_types]
        return _fake_search_results()

    monkeypatch.setattr("medterm4ds.services.search.search", fake_service)

    from medterm4ds.apps.mcp import create_mcp_server
    from fastmcp import Client

    async def run():
        server = create_mcp_server(runtime=runtime)
        async with Client(server) as client:
            res = await client.call_tool(
                "search",
                {"query": "potassium", "mode": "lexical", "count": 1,
                 "result_types": ["lab"]},
            )
            return res

    try:
        result = asyncio.run(run())
    finally:
        runtime.close()

    payload = result.data if hasattr(result, "data") else result
    rows = payload["results"]
    assert len(rows) == 1, rows
    assert rows[0]["code"] == "LP15098-4"
    # The service, not the tool, applied the filter.
    assert captured["result_types"] == ["lab"]
    assert captured["count"] == 1


def test_mcp_search_unknown_result_types_skip_service(tmp_path, monkeypatch):
    """Every requested type outside the mode's vocabulary → empty result
    without calling the (expensive) service; a warning is surfaced."""
    import asyncio

    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    runtime = McpRuntime(_settings(db_path, prepare_cache=False))
    runtime.open()
    called = {"n": 0}

    def fake_service(*args, **kwargs):
        called["n"] += 1
        return _fake_search_results()

    monkeypatch.setattr("medterm4ds.services.search.search", fake_service)

    from medterm4ds.apps.mcp import create_mcp_server
    from fastmcp import Client

    async def run():
        server = create_mcp_server(runtime=runtime)
        async with Client(server) as client:
            return await client.call_tool(
                "search",
                {"query": "x", "mode": "lexical", "count": 5,
                 "result_types": ["bogus_type"]},
            )

    try:
        result = asyncio.run(run())
    finally:
        runtime.close()

    payload = result.data if hasattr(result, "data") else result
    assert payload["results"] == []
    assert called["n"] == 0
    assert any("bogus_type" in w for w in payload.get("warnings", []))
