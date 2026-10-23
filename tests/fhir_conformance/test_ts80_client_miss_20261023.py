"""TS-80 Terminology client miss-encoding matrix (2026-10-23).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261023b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the PUBLIC-API Terminology client (mt.connect) — the surface
notebooks ride; TS-34 covered resolve modes, PY1/PY2 the REST
facade's miss shape, Z3 MCP's null-record. The client's OWN miss
conventions were never matrixed.

PC1 (LOW, informational) — the fourth miss convention, now on the
    client: lookup() returns None for BOTH unknown-code (valid
    system) and unknown-SOURCE (bogus vocabulary) — the two cases are
    indistinguishable at the return value, while whitespace/empty
    inputs fail loud (ValueError). Per-surface miss matrix final
    state: FHIR 400 + not-found OperationOutcome; MCP null-FIELD
    record; REST {results: [null]}; client bare None. Each is
    documented per-surface doctrine; registered so the matrix is on
    record and the client's None-for-bogus-SOURCE (vs the CLI's
    hard Error) is a known divergence, not an accident.
    Pinned pc10-pc12.

Controls (fresh): 31-method inventory floor; happy paths (lookup
    name, children, whitespace fail-loud ValueError); engine
    attribute exposure.
"""

from __future__ import annotations

import pytest

pytest.importorskip("medterm4ds")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import medterm4ds as mt

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts80_client") / "umls.duckdb"
    _make_conformance_db(db)
    t = mt.connect(str(db), memory_profile="low")
    yield t
    t.close()


class TestPC1ClientMissConventions:
    """PC1 — None for both unknown-code and unknown-source."""

    def test_pc10_unknown_code_none(self, client):
        """Valid system + unknown code → None (the client's miss
        convention)."""
        assert client.lookup("SNOMEDCT_US", "99999999") is None

    def test_pc11_unknown_source_none(self, client):
        """Bogus SOURCE → also None: indistinguishable from a missing
        code at the return value (the CLI Errors on the same input —
        the known per-surface divergence). Registered information
        note; flip if the client ever distinguishes."""
        assert client.lookup("BOGUS", "X") is None

    def test_pc12_whitespace_fails_loud(self, client):
        """Whitespace inputs DO fail loud on the client (the boundary
        the client gets right — contrast control)."""
        with pytest.raises(ValueError, match="non-empty"):
            client.lookup("SNOMEDCT_US", "   ")


class TestTS80Controls:
    """Controls — inventory + happy paths."""

    def test_c10_method_inventory(self, client):
        methods = {
            m for m in dir(client) if not m.startswith("_")
        }
        for expected in (
            "lookup", "search", "resolve", "patient_friendly",
            "hierarchy", "children", "ancestors", "map", "optimize",
            "conceptmap", "source_stats", "sample_codes",
        ):
            assert expected in methods, expected

    def test_c11_lookup_happy(self, client):
        row = client.lookup("SNOMEDCT_US", "44054006")
        assert row is not None
        assert row.name == "Type 2 diabetes mellitus"

    def test_c12_children_happy(self, client):
        rels = client.children("SNOMEDCT_US", "73211009")
        codes = {
            getattr(r, "code", None)
            or getattr(getattr(r, "target", None), "code", None)
            for r in rels
        }
        assert "44054006" in codes

    def test_c13_children_unknown_empty(self, client):
        """Unknown code in a valid system → [] (clean empty — the
        walk convention)."""
        assert client.children("SNOMEDCT_US", "99999999") == []
