"""TS-104 batch Bundle shape conformance (2026-10-30).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261030b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the $batch surface's Bundle-level SHAPE contract under FHIR R4
§3.7 (http.html#transaction) — surfaces the dispatcher seam sweeps
(ts44/ts48/ts95/ts96/ts97) probed per-OPERATION params, never the
Bundle envelope itself:

  E1. entry ORDER preservation (R4: response entries 'in the same
      order as the request entries').
  E2. entry CARDINALITY: N request entries → exactly N response
      entries, including when some entries fail (per-entry isolation
      must not drop or add entries).
  E3. empty batch (entry omitted entirely) → 200 batch-response with
      empty/absent entry list, not an error.
  E4. transaction-response type mirroring (QC-288 pinned) at the
      envelope level with mixed success/failure entries.
  E5. response.status is a STRING (R4 Bundle.entry.response.status
      is string, not integer) on success AND failure entries.
  E6. non-list `entry` (e.g. object) → 400 envelope error, not 500.
  E7. `entry: null` (JSON null) → 400 envelope error with
      OperationOutcome, not a TypeError 500.

RESULT: ZERO FINDINGS — controls-only (17th clean surface). All
seven shape probes PASS fresh this run: order preserved (P/P/OO/P),
cardinality exact under per-entry isolation, empty batch → 200 with
no fabricated entries, transaction-response mirroring, string
status values on success AND failure entries, non-list/null entry →
400 OperationOutcome envelopes. One OBSERVATION (not a finding,
pinned contract): unknown-code $lookup carries HTTP 200 with an
OperationOutcome body per cases.json 'lookup-invalid-code' — the
batch path mirrors the direct route faithfully (dual-invocation
equivalence holds at the envelope level).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

SNOMED_URI = "http://snomed.info/sct"


def _entry(method: str, url: str, resource: dict | None = None) -> dict:
    e: dict = {"request": {"method": method, "url": url}}
    if resource is not None:
        e["resource"] = resource
    return e


def _lookup_entry(code: str) -> dict:
    return _entry(
        "GET",
        f"CodeSystem/$lookup?system={SNOMED_URI}&code={code}",
    )


@pytest.fixture(scope="module")
def client(fhir_client):
    return fhir_client


def test_e1_order_preserved(client):
    """E1+E2: 4 entries (3 distinct codes + 1 unknown) → 4 responses,
    same order. Unknown-code lookup carries the PINNED contract
    (cases.json lookup-invalid-code): HTTP 200 with an OperationOutcome
    resource — so order is verified by resource type: Parameters/OK/
    OperationOutcome/Parameters."""
    codes = ["73211009", "44054006", "ZZZ999", "44054006"]
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [_lookup_entry(c) for c in codes],
    }
    r = client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "batch-response"
    entries = body["entry"]
    assert len(entries) == 4, "cardinality: 4 requests → 4 responses"
    types = [e["resource"]["resourceType"] for e in entries]
    assert types == ["Parameters", "Parameters", "OperationOutcome", "Parameters"], (
        f"order+isolation: expected P/P/OO/P, got {types}"
    )
    # the failing entry still reports '200' per the pinned contract, but its
    # resource carries the not-found error — position preserved.
    assert entries[2]["resource"]["issue"][0]["code"] == "not-found"


def test_e2_isolation_no_drop(client):
    """A failing entry between two good ones does not affect them."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            _lookup_entry("73211009"),
            _entry("POST", "CodeSystem/$nope", {"resourceType": "Parameters", "parameter": []}),
            _lookup_entry("44054006"),
        ],
    }
    r = client.post("/fhir", json=bundle)
    assert r.status_code == 200
    entries = r.json()["entry"]
    assert len(entries) == 3
    assert entries[0]["response"]["status"] == "200"
    assert entries[1]["response"]["status"].startswith("4")
    assert entries[2]["response"]["status"] == "200"


def test_e3_empty_batch(client):
    """E3: entry omitted → 200 with no entry key (or empty list)."""
    r = client.post("/fhir", json={"resourceType": "Bundle", "type": "batch"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "batch-response"
    assert not body.get("entry"), "empty batch must not fabricate entries"


def test_e4_transaction_response_type(client):
    """E4: type=transaction → type=transaction-response even with failures."""
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            _lookup_entry("73211009"),
            _entry("DELETE", "CodeSystem/123"),
        ],
    }
    r = client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "transaction-response"
    assert body["entry"][1]["response"]["status"] == "405"


def test_e5_status_is_string(client):
    """E5: response.status values are JSON strings on success and failure."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            _lookup_entry("73211009"),
            _entry("GET", "CodeSystem/$lookup"),  # missing params → 400
        ],
    }
    r = client.post("/fhir", json=bundle)
    entries = r.json()["entry"]
    for e in entries:
        s = e["response"]["status"]
        assert isinstance(s, str), f"response.status must be string, got {type(s).__name__}"


def test_e6_entry_not_a_list(client):
    """E6: entry as object → 400 envelope OperationOutcome."""
    r = client.post(
        "/fhir",
        json={"resourceType": "Bundle", "type": "batch", "entry": {"request": {}}},
    )
    assert r.status_code == 400
    assert r.json()["resourceType"] == "OperationOutcome"


def test_e7_entry_null(client):
    """E7: entry: null → 400 envelope error (JSON null is not a list)."""
    r = client.post(
        "/fhir",
        json={"resourceType": "Bundle", "type": "batch", "entry": None},
    )
    assert r.status_code == 400
    assert r.json()["resourceType"] == "OperationOutcome"


def test_control_mixed_lookup_and_validate(client):
    """Control: known-good mixed batch behaves (lookup + validate-code)."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            _lookup_entry("44054006"),
            _entry(
                "GET",
                f"CodeSystem/$validate-code?system={SNOMED_URI}&code=44054006",
            ),
        ],
    }
    r = client.post("/fhir", json=bundle)
    entries = r.json()["entry"]
    assert all(e["response"]["status"] == "200" for e in entries)
