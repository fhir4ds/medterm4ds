"""VS-01/VS-03 filter-semantics findings suite (2026-10-02).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261002e).
FRESH EVIDENCE — live probes executed this run against the shared
conformance fixture; no prior-run results cited.

Two NEW spec violations found by live probing (both citable):

F1 (HIGH) — multiple include.filters are OR-composed, spec says AND.
    FHIR R4 §4.9.12 ValueSet.compose.include.filter Definition:
    "If multiple filters are specified, they SHALL all be true."
    The implementation appends each filter's subtree independently
    (expand_intensional_value_set loops filters and appends), so two
    is-a filters over nested concepts return the UNION. On the shared
    fixture: [is-a 73211009, is-a 44054006] -> {73211009, 44054006}
    (OR); spec-correct AND answer is {44054006}. A client using such a
    compose to narrow a value set gets a WIDER set than requested —
    silent-wrong-answer class (clinical-safety relevant: over-broad
    value sets surface as wrong medication/problem pick-lists).

F2 (LOW) — vsd-3 not enforced: concept+filter in one include block.
    FHIR R4 invariant vsd-3 on ValueSet.compose.include:
    "Cannot have both concept and filter: concept.empty() or
    filter.empty()". The server accepts both and OR-appends their
    results (observed: concept 73211009 + is-a 44054006 ->
    {73211009, 44054006}). Per conformance-rules, invariants on
    submitted resources SHOULD be rejected (400) — at minimum this is
    an undocumented lenient path.

Also re-verified this run (stale-registry correction): exclude[].filter
IS implemented (QC-242 landed is-a/descendent-of subtree exclusion;
fresh probes: exclude is-a 44054006 removes T2DM only; exclude is-a
73211009 removes everything). The AGENTS.md line-89 note "(a) only
matches exclude[].concept[].code, ignores exclude[].filter[] (CF-
SKEPTIC-VS01-02)" is STALE — corrected in the registry this iteration.

All probes below pin CURRENT behavior with flip-on-fix instructions
(carry-forward-as-probe pattern). Findings-only: zero production code
changes.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
DM = "73211009"      # Diabetes mellitus (root)
T2DM = "44054006"    # Type 2 DM (child of DM)


def _expand(client, vs):
    r = client.post("/fhir/ValueSet/$expand", json=vs)
    assert r.status_code == 200, r.text
    body = r.json()
    return sorted(
        c["code"] for c in body["expansion"].get("contains", [])
    )


class TestF1MultipleFilterComposition:
    """F1 — filters SHALL all be true (AND); implementation ORs."""

    def test_f10_documenting_or_violation(self, fhir_client):
        """Two is-a filters (nested subtrees): observed UNION, spec AND.

        is-a DM = {DM, T2DM}; is-a T2DM = {T2DM}. AND = {T2DM}.
        Current implementation returns {DM, T2DM} — the union — which
        includes DM, violating "they SHALL all be true".

        WHEN THE FIX LANDS (intersect per-filter result sets within an
        include block), flip the expected value to [T2DM].
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "is-a", "value": DM},
                    {"property": "concept", "op": "is-a", "value": T2DM},
                ],
            }]},
        }
        codes = _expand(fhir_client, vs)
        assert codes == sorted([DM, T2DM]), (
            f"Filter composition changed: got {codes}. If ['{T2DM}'] the "
            "AND-semantics fix landed — flip this pin, file the registry "
            "update, and tighten f11."
        )

    def test_f11_single_filter_baseline_unchanged(self, fhir_client):
        """Control: a single filter behaves exactly as documented (the
        violation is only in COMPOSITION, not single-filter expansion)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "is-a", "value": DM},
                ],
            }]},
        }
        assert _expand(fhir_client, vs) == sorted([DM, T2DM])

    def test_f12_identical_filters_no_dupes(self, fhir_client):
        """Two IDENTICAL filters still dedupe (contains carries each
        code once) — the OR-composition at least deduplicates."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "is-a", "value": DM},
                    {"property": "concept", "op": "is-a", "value": DM},
                ],
            }]},
        }
        assert _expand(fhir_client, vs) == sorted([DM, T2DM])


class TestF2Vsd3ConceptPlusFilter:
    """F2 — vsd-3 (concept.empty() or filter.empty()) not enforced."""

    def test_f20_documenting_vsd3_leniency(self, fhir_client):
        """concept + filter in ONE include block: accepted, both applied
        (OR-appended). Spec invariant vsd-3 says this shape is invalid;
        the conformant response is 400 (or at minimum a documented
        lenient policy). Pin the current 200 + union.

        WHEN THE FIX LANDS (reject with 400 OperationOutcome citing
        vsd-3), flip this probe to expect 400.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": DM}],
                "filter": [
                    {"property": "concept", "op": "is-a", "value": T2DM},
                ],
            }]},
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=vs)
        assert r.status_code == 200, (
            "concept+filter now rejected — vsd-3 enforcement landed; "
            "flip this pin to assert the 400 OperationOutcome."
        )
        codes = sorted(
            c["code"] for c in r.json()["expansion"].get("contains", [])
        )
        assert codes == sorted([DM, T2DM])


class TestExcludeFilterReverification:
    """Stale-registry correction evidence: exclude[].filter IS implemented."""

    def test_r10_exclude_is_a_removes_subtree(self, fhir_client):
        """exclude is-a T2DM removes {T2DM}, keeps DM (QC-242 working)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": DM}, {"code": T2DM}],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": T2DM},
                    ],
                }],
            },
        }
        assert _expand(fhir_client, vs) == [DM]

    def test_r11_exclude_is_a_root_removes_all(self, fhir_client):
        """exclude is-a DM removes the whole subtree (fresh re-verification
        of the QC-242 fix — the registry note claiming exclude[].filter
        is ignored was stale)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": DM}, {"code": T2DM}],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": DM},
                    ],
                }],
            },
        }
        assert _expand(fhir_client, vs) == []
