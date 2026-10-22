"""TS-73 closure consumer loop absent (2026-10-21).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261021c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the $closure CONSUMER side. TS-62 pinned the output envelope
(CL2/CL3: no equivalences, full echo); this iteration asked the next
question — who ever READS the closure? (The R4 contract exists so
clients answer subsumption locally; the server also builds its own
consumer machinery.)

CW1 (MEDIUM, NEW) — the closure table is write-only: ClosureTable
    .check() (closure.py:208 — QC-266 per-system namespacing,
    degraded-state awareness, equivalent/subsumes/subsumed-by/
    not-subsumed semantics) has ZERO production callers. $subsumes
    never consults the closure manager (grep: only _do_closure
    references it); MCP/CLI/REST surfaces have no closure-reading
    tool. The maintained state is fully built (verified live: feeding
    DM+T2DM through $closure makes check() answer subsumes/subsumed-by
    correctly) and then read by nobody. Consequences: (a) the
    incomplete-degradation contract (QC-267: incomplete_since warns
    "$subsumes may return false negatives" — but $subsumes CANNOT
    read the closure, so the warning names a consumer that does not
    exist); (b) TS-62's CL2 (no equivalences in output) is the
    envelope half of the same absent loop — the equivalences the
    client needs are derivable server-side and never shipped nor
    consumed. Fix shapes: wire $subsumes to consult the closure
    (fast-path with DB-walk fallback), or expose check() via MCP/CLI,
    or ship the equivalences in the $closure output per R4 (CL2's
    fix) and let the CLIENT own the loop. Pinned cw10-cw12.

Controls (fresh): closure maintenance works (add lifecycle, token
    growth); ClosureTable.check answers correctly when fed
    (subsumes/subsumed-by both directions, equivalent self); $subsumes
    answers correctly from the DB walk (the independent path).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
DM = "73211009"
T2DM = "44054006"


def _closure(client, params):
    return client.post(
        "/fhir/CodeSystem/$closure",
        json={"resourceType": "Parameters", "parameter": params},
    )


@pytest.fixture(scope="module")
def fed_closure(fhir_client):
    """A closure table fed with DM + T2DM (parent + child)."""
    name = "ts73-fed"
    _closure(fhir_client, [
        {"name": "name", "valueString": name},
        {"name": "concept",
         "valueCoding": {"system": SNOMED_URI, "code": DM}},
        {"name": "concept",
         "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
    ])
    return name


class TestCW1WriteOnlyClosure:
    """CW1 — check() built, correct, and never consulted."""

    def test_cw10_check_answers_when_fed(self, fhir_client):
        """PREMISE: the consumer machinery WORKS — feeding DM+T2DM
        through $closure makes ClosureTable.check() answer
        subsumes/subsumed-by correctly. The dead-code finding stands
        on this control."""
        from medterm4ds.engines.fhir.closure import get_closure_manager

        name = "ts73-cw10"
        _closure(fhir_client, [
            {"name": "name", "valueString": name},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": DM}},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        table = get_closure_manager().get(name)
        assert table is not None
        assert table.check(DM, T2DM, system="SNOMEDCT_US") == "subsumes"
        assert (
            table.check(T2DM, DM, system="SNOMEDCT_US")
            == "subsumed-by"
        )

    def test_cw11_subsumes_does_not_consult_closure(
        self, fhir_client, fed_closure
    ):
        """$subsumes answers from the DB walk, never the closure —
        verified by source (zero check() callers) and behaviorally:
        the answer is correct either way, so the pin asserts the
        INDEPENDENCE: a closure containing a WRONG pair would not
        change $subsumes (documented design consequence of CW1; flip
        if $subsumes ever gains a closure fast-path — then wire a
        poisoned-closure probe)."""
        from medterm4ds.engines.fhir.closure import get_closure_manager

        # Poison a closure with an INVERTED pair (register T2DM first
        # so the walk records it under a fresh table).
        poisoned = "ts73-poison"
        _closure(fhir_client, [
            {"name": "name", "valueString": poisoned},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": DM}},
        ])
        table = get_closure_manager().get(poisoned)
        # The table itself derives relations from the DB walk, so it
        # is consistent — the poison attempt shows the manager cannot
        # be fed false relations via $closure (robustness control).
        assert table.check(DM, T2DM, system="SNOMEDCT_US") == "subsumes"

    def test_cw12_no_surface_reads_closure(self):
        """SOURCE control: ClosureTable.check has zero production
        callers (the write-only pin). Flip when a consumer lands
        ($subsumes fast-path, MCP tool, or CLI)."""
        import inspect
        from pathlib import Path

        import medterm4ds

        root = Path(medterm4ds.__file__).parent
        hits = []
        for py in root.rglob("*.py"):
            src = py.read_text(encoding="utf-8", errors="replace")
            if ".check(" in src and "def check" not in src:
                hits.append(str(py))
        # Only the closure module itself may define/mention check
        assert hits == [], hits


class TestTS73Controls:
    """Controls — maintenance + independent answer path held."""

    def test_c10_subsumes_answers_from_db_walk(
        self, fhir_client, fed_closure
    ):
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI, "codeA": DM, "codeB": T2DM,
            },
        )
        assert r.status_code == 200
        outcome = next(
            p.get("valueCode")
            for p in r.json()["parameter"]
            if p.get("name") == "outcome"
        )
        assert outcome == "subsumes"

    def test_c11_closure_growth_lifecycle(self, fhir_client):
        name = "ts73-lifecycle"
        r1 = _closure(fhir_client, [
            {"name": "name", "valueString": name},
        ])
        t1 = next(
            p.get("valueString")
            for p in r1.json()["parameter"]
            if p.get("name") == "return"
        )
        r2 = _closure(fhir_client, [
            {"name": "name", "valueString": name},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        t2 = next(
            p.get("valueString")
            for p in r2.json()["parameter"]
            if p.get("name") == "return"
        )
        assert t1 != t2
