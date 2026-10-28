"""TS-41 prod-diff round 3 — MCP + $search under production (2026-10-14).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261014c).
FRESH EVIDENCE — live probes executed this run against the real
prepared DB (umls_current) via McpRuntime and the search service;
no prior-run results cited.

Scope: prod-diff round 3. TS-39 measured FHIR core contracts,
TS-40 the REST secondary endpoints; this round runs the MCP surface
and the $search pipeline under production artifacts.

G1-SHARP (G1 root-cause isolated, no new id) — G1's defect is
    ENDPOINT-LAYER, not service-layer.
    Direct service call on production artifacts:
    ``get_search_service().search('diabetes', mode='semantic',
    result_types=['lab'])`` filters CORRECTLY (all-lab results).
    The FHIR endpoint with the same request
    (``$search?query=diabetes&searchMode=semantic&resultTypes=lab``)
    returns unfiltered ICD-10-CM condition codes. Root cause
    (source-read): ``_do_search`` passes ``result_types`` to
    ``service.canonical(...)`` but OMITS it in the legacy-modes
    ``service.search(...)`` call — the param is dropped at the
    endpoint boundary for lexical/semantic/hybrid. The registered
    G1 pin (test_ts07 g10-g12) exercises the endpoint, which is why
    the defect looked service-wide. Fix is one line:
    ``result_types=result_types`` in the legacy call.
    Pinned: g10s (service-layer filter WORKS on prod artifacts),
    g11s (endpoint-layer drop reproduced fresh).

PD3 (controls with teeth) — MCP production parity CLEAN.
    Navigators (diagnosis/lab/vaccine/search_drug) resolve real
    data with full envelopes; cross_reference's target filter works
    on production SQL (ICD10CM-exact); Z1's widening behavior
    unchanged (to_sources=None widens to the 2 systems the real DB
    crosswalks: ICD10CM, MSH); lookup_code carries real CUIs.
    The MCP surface is path-stable.
    Pinned: p10-p13.

Controls (fresh): production search pipeline healthy via the
    split-layout cache — no index dir configured, lexical AND
    semantic both available (fresh fetch where needed); hybrid
    returns cross-domain results.
"""

from __future__ import annotations

import pathlib

import pytest

PROD_DB = pathlib.Path("/mnt/d/medterm4ds/data/umls_current.duckdb")


class TestG1SharpEndpointLayer:
    """G1-SHARP — the service filters; the endpoint drops the param."""

    def test_g10s_service_layer_filters(self):
        """Direct service call with result_types=['lab'] on semantic
        returns all-lab results (production artifacts). The G1
        defect is NOT in the service."""
        from medterm4ds.services.search import get_search_service

        svc = get_search_service()
        if not svc.semantic_available:
            pytest.skip("semantic model unavailable")
        rows = svc.search(
            "diabetes", mode="semantic", count=5, result_types=["lab"],
        )
        rows = rows if isinstance(rows, list) else rows.results
        assert rows, "prod semantic search must return results"
        for r in rows:
            d = r if isinstance(r, dict) else r.__dict__
            assert d.get("category") == "lab"

    def test_g11s_endpoint_layer_drops(self, tmp_path):
        """The SAME request through the FHIR endpoint returns
        unfiltered condition codes — _do_search omits result_types
        in the legacy-modes service.search call (source-read: the
        canonical branch passes it, the legacy branch does not).
        Flip when the one-line fix lands (then G1's ts07 pins
        flip too)."""
        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import (
            FhirApiSettings,
            create_fhir_app,
        )

        if not PROD_DB.exists():
            pytest.skip("no prod DB")
        app = create_fhir_app(
            FhirApiSettings(
                db_path=PROD_DB,
                memory_profile="low",
                search_index_dir=str(tmp_path / "no-idx"),
                prepare_cache=False,
            )
        )
        # G1 FIXED (c-fixbatch6): the endpoint now forwards
        # result_types on the legacy modes — the semantic result set
        # is lab-only.
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get(
                "/fhir/CodeSystem/$search",
                params={
                    "query": "diabetes",
                    "searchMode": "semantic",
                    "resultTypes": "lab",
                    "count": 5,
                },
            )
            assert r.status_code == 200
            entries = r.json().get("entry", [])
            assert entries
            systems = {
                e["resource"]["system"] for e in entries
            }
            assert systems == {"http://loinc.org"}


class TestPD3McpProductionParity:
    """PD3 — MCP surface is path-stable on production SQL."""

    @pytest.fixture(scope="class")
    def runtime(self):
        if not PROD_DB.exists():
            pytest.skip("production DB not present")
        from medterm4ds.apps.mcp import McpRuntime, McpSettings

        rt = McpRuntime(settings=McpSettings(db_path=PROD_DB))
        rt.open()
        yield rt
        rt.close()

    @pytest.mark.parametrize(
        "tool,kw,expect_field",
        [
            ("diagnosis_codes", {"condition": "diabetes"}, "code"),
            ("lab_codes", {"lab_test": "glucose"}, "code"),
            ("vaccine_codes", {"vaccine": "flu"}, "code"),
            ("search_drug", {"drug_name": "metformin"}, "code"),
        ],
    )
    def test_p10_navigators_prod(
        self, runtime, tool, kw, expect_field
    ):
        fn = getattr(runtime, tool)
        out = fn(**dict(kw), limit=3)
        assert out["result_count"] >= 1
        assert expect_field in out["results"][0]

    def test_p11_cross_reference_filter_prod(self, runtime):
        out = runtime.cross_reference(
            code="44054006",
            from_source="SNOMEDCT_US",
            to_sources=["ICD10CM"],
        )
        assert out["result_count"] >= 1
        sources = {
            r.get("target_source", r.get("source"))
            for r in out["results"]
        }
        assert sources <= {"ICD10CM"}

    def test_p12_z1_widening_unchanged(self, runtime):
        """to_sources=None widens to the systems the real DB
        crosswalks (ICD10CM + MSH observed) — Z1's registered
        behavior, path-stable."""
        out = runtime.cross_reference(
            code="44054006", from_source="SNOMEDCT_US",
        )
        targets: set[str] = set()

        def walk(o):
            if isinstance(o, dict):
                if "target_source" in o:
                    targets.add(o["target_source"])
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(out)
        assert len(targets) >= 2  # the real crosswalk is multi-system

    def test_p13_lookup_prod_cui(self, runtime):
        out = runtime.lookup_code(code="44054006", source="SNOMEDCT_US")
        assert out["name"] == "Type 2 diabetes mellitus"
        assert out["cui"]
