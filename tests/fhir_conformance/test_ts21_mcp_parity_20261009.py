"""TS-21 MCP/FHIR cross-surface contract parity (2026-10-09).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261009c).
FRESH EVIDENCE — live probes executed this run against McpRuntime (the
tool layer is a thin single-worker _run_db wrapper over it); no
prior-run results cited.

Scope: after four fail-loud fix-batches on the FHIR facade (version
400s, unknown-param 400s, targetSystem required, code-in-system
checks), do the MCP sibling tools agree? GLOBAL_RULES cross-surface
consistency doctrine. Focused on the shared terminology operations:
lookup_code ↔ $lookup, resolve_codes ↔ $validate-code,
cross_reference ↔ $translate, search_names ↔ $search.

Z1 (LOW-MED, NEW) — cross_reference target widening diverges from the
    FHIR V3 contract. FHIR $translate now REQUIRES targetSystem (a
    missing target 400s — fixbatch3); MCP cross_reference(
    to_sources=None) silently widens to every system with a crosswalk
    (probe: 44054006 no-target → 1 result, target ICD10CM — exactly
    the cross-system answer a client forgetting the target gets
    without any signal). Mitigations that temper severity: the
    widening is a documented tool-parameter default (not a dropped
    param), and the response echoes to_sources: None so the widening
    is visible in hindsight — but a client that forgot to SET the
    target still receives plausible cross-system answers, the exact
    clinical-safety shape V3 closed on FHIR.
    Fix shape: require to_sources (drop the None default) or emit a
    prominent no-target banner in the response. Flip pins z10/z11.

Z3 (LOW-MED, NEW) — unknown-system semantics diverge. MCP
    lookup_code(source='BOGUS') returns a null-field record (name/
    cui/aui all None) — the silent-null shape — while FHIR $lookup
    rejects unknown systems with 400 OperationOutcome (error-shape
    matrix, iteration 20261008c). One doctrine must win: validate the
    source at the MCP boundary too (_validate_source_sab exists and
    rejects URI forms; the raw-SAB path apparently does not run it).
    Flip pin c11 (rename per fix direction).

Z2 (CONTROL, NEW PIN) — resolve_codes per-code isolation CONTRASTS
    with C1. FHIR $closure's C1 (one unknown poisons the whole
    request) was MEDIUM; MCP resolve_codes with mixed
    [known, unknown] codes returns per-code rows — 44054006 active
    AND 99999999 its own miss row in ONE response. Pinned as the
    cross-surface REFERENCE SHAPE for C1's fix (per-concept Out
    equivalences).

Controls (fresh): whitespace/empty inputs fail loud on MCP exactly as
    FHIR 400s (lookup_code '   ' → ValueError; search_names '   ' →
    ValueError; bogus system validated by _validate_source_sab);
    cross_reference with an EXPLICIT no-map target (LNC) returns
    result_count=0 — the engine-level target filter works, confirming
    fixbatch3's root-cause finding that the V2 bug was the FHIR
    param-name layer, not the engine; unknown-code lookup returns the
    null-field record shape (QC-323).
"""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip("medterm4ds.apps.mcp")

mcp_module = importlib.import_module("medterm4ds.apps.mcp")

SNOMED = "SNOMEDCT_US"
ICD10CM = "ICD10CM"
LNC = "LNC"
T2DM = "44054006"


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("mcp_parity") / "umls.duckdb"
    _make_conformance_db(db)
    rt = mcp_module.McpRuntime(
        mcp_module.McpSettings(db_path=db)
    )
    rt.open()
    yield rt
    rt.close()


class TestZ1TargetWideningDivergence:
    """Z1 — MCP no-target widening vs FHIR's required targetSystem."""

    def test_z10_no_target_widens(self, runtime):
        """to_sources=None widens to every crosswalked system (the V3
        shape FHIR closed). Flip when to_sources is required or the
        response carries a no-target banner."""
        r = runtime.cross_reference(code=T2DM, from_source=SNOMED)
        d = r if isinstance(r, dict) else {"raw": str(r)}
        assert d.get("result_count", 0) >= 1  # cross-system answer

    def test_z11_fhir_side_requires_target(self, fhir_client):
        """Premise control: the FHIR sibling REJECTS the same clinical
        question (no target) — the divergence is real, not historical."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": "http://snomed.info/sct", "code": T2DM,
            },
        )
        assert r.status_code == 400


class TestZ2PerCodeIsolation:
    """Z2 — resolve_codes per-code rows (C1's reference shape)."""

    def test_z20_mixed_known_unknown(self, runtime):
        r = runtime.resolve_codes(
            codes=[T2DM, "99999999"], sources=[SNOMED, SNOMED],
        )
        d = r if isinstance(r, dict) else {"raw": str(r)}
        results = d.get("results", [])
        assert len(results) == 2  # BOTH rows present — no poisoning
        by_code = {row.get("code"): row for row in results}
        assert by_code[T2DM].get("status") == "active"
        assert by_code["99999999"].get("status") != "active"


class TestMCPParityControls:
    """Controls — fail-loud input parity, fresh."""

    def test_c10_whitespace_code_fails_loud(self, runtime):
        with pytest.raises(ValueError, match="non-empty"):
            runtime.lookup_code(code="   ", source=SNOMED)

    def test_c11_bogus_source_returns_null_record(self, runtime):
        """Z3 (LOW-MED, NEW): MCP lookup_code with an unknown system
        returns a 200-shaped NULL-FIELD record (name=None, cui=None...)
        while FHIR $lookup rejects the same input with 400 (error-shape
        matrix, iteration 20261008c). Cross-surface divergence in
        unknown-system semantics: silent-null vs fail-loud. Flip when
        MCP validates the source (ValueError) or FHIR softens — the
        doctrines demand one answer."""
        r = runtime.lookup_code(code=T2DM, source="BOGUS")
        d = r if isinstance(r, dict) else {"raw": str(r)}
        assert d.get("name") is None  # silent null record

    def test_c12_whitespace_query_fails_loud(self, runtime):
        with pytest.raises(ValueError, match="empty"):
            runtime.search_names(query="   ", limit=3)

    def test_c13_explicit_target_filters(self, runtime):
        """Explicit no-map target → result_count=0 (engine filter
        works; fixbatch3's root-cause confirmation at the runtime
        layer)."""
        r = runtime.cross_reference(
            code=T2DM, from_source=SNOMED, to_sources=[LNC],
        )
        d = r if isinstance(r, dict) else {"raw": str(r)}
        assert d.get("result_count") == 0

    def test_c14_unknown_code_null_record(self, runtime):
        """QC-323 shape: unknown code returns the null-field record,
        not None and not an exception."""
        r = runtime.lookup_code(code="99999999", source=SNOMED)
        d = r if isinstance(r, dict) else {"raw": str(r)}
        assert d.get("code") == "99999999"
        assert d.get("status") != "active"
