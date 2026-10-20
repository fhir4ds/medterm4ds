"""TS-66 transaction atomicity LIVE-STATE (2026-10-19).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261019d).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited. (The R4 §3.7.2 atomicity citation was verified live back in
iteration 13's B1 registration; this suite adds the STATE
consequence.)

Scope: B1 (TS-04, registered 20261004c) pinned that a transaction is
processed with batch semantics — 200 transaction-response + per-entry
statuses despite failing entries. What was never probed: the LIVE
STATE consequence on the server's only STATEFUL operation ($closure).
A client receiving a failed transaction reasonably assumes NOTHING
happened.

TX1 (MEDIUM, NEW — B1's live-state exhibit) — a transaction
    containing a FAILING entry COMMITS its sibling's state mutation.
    Probe: closure txn-76 grown to {44054006}; a transaction bundle
    [closure-add 73211009, lookup-unknown-system] executes BOTH:
    entry statuses ['200', '400'], response type transaction-response
    — and the closure now contains 73211009. R4 §3.7.2: a transaction
    is atomic; when any entry fails the server SHALL return an error
    and no entries are processed. The terminology server has exactly
    one stateful surface, and it mutates under failed transactions.
    Clinical shape: a client batching closure maintenance with lookup
    work loses the all-or-nothing contract silently — partial closure
    state that the client believes was rolled back.
    Fix shape (B1's): reject failing transactions atomically (process
    none on any failure), or stop advertising 'transaction' in the
    CapabilityStatement (batch-only). Flip pins tx10-tx12 together
    with B1's.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"
DM = "73211009"
EMPTY_HASH_PREFIX = "77509ea1ea0d"


def _closure(client, params):
    return client.post(
        "/fhir/CodeSystem/$closure",
        json={"resourceType": "Parameters", "parameter": params},
    )


def _token(body: dict):
    for p in body.get("parameter", []):
        if p.get("name") == "return":
            return p.get("valueString")
    return None


def _closure_codes(client, name):
    """Current closure concept codes (re-add echo trick: an add of an
    already-present code echoes the whole table)."""
    r = _closure(client, [
        {"name": "name", "valueString": name},
        {"name": "concept",
         "valueCoding": {"system": SNOMED_URI, "code": DM}},
    ])
    return {
        p.get("valueCoding", {}).get("code")
        for p in r.json().get("parameter", [])
        if p.get("name") == "concept"
    }


class TestTX1FailedTransactionCommits:
    """TX1 — sibling mutations land under failed transactions."""

    def test_tx10_failing_txn_commits_sibling(self, fhir_client):
        """THE FINDING: a transaction [closure-add, failing-lookup]
        returns per-entry statuses AND the closure mutated. Flip with
        B1's fix (atomic failure — closure unchanged, or transaction
        not advertised)."""
        name = "ts66-tx10"
        _closure(fhir_client, [{"name": "name", "valueString": name}])
        _closure(fhir_client, [
            {"name": "name", "valueString": name},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])

        txn = {
            "resourceType": "Bundle",
            "type": "transaction",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": name},
                            {"name": "concept", "valueCoding": {
                                "system": SNOMED_URI, "code": DM,
                            }},
                        ],
                    },
                },
                {
                    "request": {
                        "method": "GET",
                        "url": (
                            "CodeSystem/$lookup"
                            "?system=http://nowhere.org&code=X"
                        ),
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir", json=txn)
        assert r.status_code == 200
        assert r.json()["type"] == "transaction-response"
        statuses = [
            e.get("response", {}).get("status")
            for e in r.json().get("entry", [])
        ]
        assert statuses == ["200", "400"]  # B1: batch semantics

        # TX1: the sibling's mutation COMMITTED despite the failure.
        codes = _closure_codes(fhir_client, name)
        assert DM in codes

    def test_tx11_batch_same_behavior_documented(self, fhir_client):
        """Contrast control: BATCH semantics are correct for batch —
        per-entry outcomes expected, partial application legitimate.
        The finding is the transaction/batch DISTINCTION being lost
        (tx10), not batch behavior itself."""
        name = "ts66-tx11"
        _closure(fhir_client, [{"name": "name", "valueString": name}])
        batch = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": name},
                            {"name": "concept", "valueCoding": {
                                "system": SNOMED_URI, "code": DM,
                            }},
                        ],
                    },
                },
                {
                    "request": {
                        "method": "GET",
                        "url": (
                            "CodeSystem/$lookup"
                            "?system=http://nowhere.org&code=X"
                        ),
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir", json=batch)
        assert r.status_code == 200
        assert r.json()["type"] == "batch-response"
        codes = _closure_codes(fhir_client, name)
        assert DM in codes  # correct FOR BATCH

    def test_tx12_all_success_txn_commits(self, fhir_client):
        """Premise control: an all-success transaction commits
        normally (the mutation path itself is sound; TX1 is about
        failure isolation only)."""
        name = "ts66-tx12"
        _closure(fhir_client, [{"name": "name", "valueString": name}])
        txn = {
            "resourceType": "Bundle",
            "type": "transaction",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": name},
                            {"name": "concept", "valueCoding": {
                                "system": SNOMED_URI, "code": T2DM,
                            }},
                        ],
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir", json=txn)
        assert r.status_code == 200
        statuses = [
            e.get("response", {}).get("status")
            for e in r.json().get("entry", [])
        ]
        assert statuses == ["200"]
        codes = _closure_codes(fhir_client, name)
        assert T2DM in codes
