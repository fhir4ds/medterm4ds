"""TS-04 Bundle $batch/$transaction semantics findings (2026-10-04).

Maintenance spec-comp iteration 13 (worktree maint/spec-comp-20261004c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: first maintenance sweep of the POST /fhir Bundle dispatcher
(R4 §3.6 batch, §3.7 transaction). Controls verified fresh: non-batch
Bundle types (collection/document/message/history) → 400 OperationOutcome;
missing type → 400; per-entry isolation (valid + invalid + unknown-path
entries coexist: 200/400/404 by entry, order preserved); response Bundle
type batch-response; entries without `request` receive per-entry 400
(NOT dropped); mixed batch (success + failure) correctly returns 200
with per-entry statuses (batch semantics permit this).

B1 (MEDIUM, NEW) — transaction accepted with BATCH (non-atomic)
    semantics. type=transaction is advertised in the CapabilityStatement
    rest-level interactions AND processed by the dispatcher: a
    transaction containing a failing entry returns HTTP 200 +
    transaction-response with per-entry statuses (200/404 mixed).
    R4 §3.7.2: "If any entry fails, the transaction fails, and no
    resources are committed" — the response SHALL be 4xx/5xx with an
    OperationOutcome. A client relying on atomicity (all-or-nothing)
    receives a 200 that implies commit while entries silently failed.
    Server is read-only (writes 404/405) which bounds the harm, but the
    200 transaction-response is a false commit signal.
    Fix shape: reject type=transaction with 400 not-supported (server
    cannot honor atomicity) OR fail whole-transaction when any entry
    errors. Pinned: b10/b11/b12 with flip-on-fix.

B2 (LOW, NEW) — metadata unreachable from batch entries.
    entry.request.url='metadata' (and 'fhir/metadata',
    '/fhir/metadata') → per-entry 404 "Unknown operation or resource
    path"; only operation paths (CodeSystem/$lookup etc.) are routed.
    R4 §3.6.1: entry URLs are relative to the FHIR base — 'metadata'
    should resolve. Rare client pattern (metadata-in-batch) but a
    resolution gap.
    Pinned: b20.

Controls: b30-b36.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
LOOKUP_OK = (
    "CodeSystem/$lookup"
    f"?system={SNOMED_URI}&code=44054006"
)


def _entry_status(client, bundle: dict) -> tuple[int, str | None, list[str | None]]:
    r = client.post("/fhir", json=bundle)
    body = r.json()
    statuses = [
        e.get("response", {}).get("status")
        for e in body.get("entry", [])
    ]
    return r.status_code, body.get("type"), statuses


class TestB1TransactionNonAtomic:
    """B1 — transaction gets batch-like 200 + partial statuses."""

    def test_b10_transaction_advertised_and_accepted(self, fhir_client):
        """type=transaction → 200 transaction-response (CURRENT,
        deviation). Flip when fixed: 400 not-supported OR whole-
        transaction failure semantics."""
        bundle = {
            "resourceType": "Bundle", "type": "transaction",
            "entry": [{"request": {
                "method": "GET", "url": LOOKUP_OK,
            }}],
        }
        status, btype, statuses = _entry_status(
            fhir_client, bundle
        )
        assert status == 200
        assert btype == "transaction-response"
        assert statuses == ["200"]

    def test_b11_transaction_partial_failure_200(self, fhir_client):
        """THE FINDING: failing entry inside transaction still yields
        HTTP 200 + per-entry statuses. R4 §3.7.2: whole transaction
        SHALL fail. Flip when fixed."""
        bundle = {
            "resourceType": "Bundle", "type": "transaction",
            "entry": [
                {"request": {"method": "GET", "url": LOOKUP_OK}},
                {"request": {"method": "GET", "url": "Patient/1"}},
            ],
        }
        status, btype, statuses = _entry_status(
            fhir_client, bundle
        )
        assert status == 200, (
            "Transaction now fails whole-cloth — B1 fixed; flip "
            "this pin."
        )
        assert btype == "transaction-response"
        assert "404" in statuses

    def test_b12_transaction_write_attempt_200(self, fhir_client):
        """Write entry (POST Patient) in transaction: 200 + per-entry
        404 — false commit signal for a write-bearing transaction."""
        bundle = {
            "resourceType": "Bundle", "type": "transaction",
            "entry": [
                {"request": {"method": "GET", "url": LOOKUP_OK}},
                {
                    "fullUrl": "urn:uuid:1",
                    "resource": {
                        "resourceType": "Patient", "id": "x",
                    },
                    "request": {
                        "method": "POST", "url": "Patient",
                    },
                },
            ],
        }
        status, _, statuses = _entry_status(fhir_client, bundle)
        assert status == 200
        assert "404" in statuses


class TestB2MetadataUnreachableFromBatch:
    """B2 — 'metadata' entry URL 404s in every relative form."""

    def test_b20_metadata_entry_404(self, fhir_client):
        """entry url='metadata' → per-entry 404 (CURRENT, deviation).
        R4 §3.6.1: entry URLs resolve against the FHIR base."""
        for url in ("metadata", "fhir/metadata", "/fhir/metadata"):
            bundle = {
                "resourceType": "Bundle", "type": "batch",
                "entry": [{
                    "request": {"method": "GET", "url": url},
                }],
            }
            _, _, statuses = _entry_status(fhir_client, bundle)
            assert statuses == ["404"], url


class TestBatchControls:
    """Controls: conformant batch shapes re-verified fresh."""

    @pytest.mark.parametrize(
        "bad_type", ["collection", "document", "message", "history"]
    )
    def test_b30_non_batch_types_400(self, fhir_client, bad_type):
        """Non-batch/transaction Bundle types → 400 OperationOutcome."""
        r = fhir_client.post(
            "/fhir",
            json={
                "resourceType": "Bundle",
                "type": bad_type,
                "entry": [],
            },
        )
        assert r.status_code == 400
        assert (
            r.json().get("resourceType") == "OperationOutcome"
        )

    def test_b31_missing_type_400(self, fhir_client):
        r = fhir_client.post(
            "/fhir", json={"resourceType": "Bundle", "entry": []}
        )
        assert r.status_code == 400

    def test_b32_per_entry_isolation_and_order(self, fhir_client):
        """Valid + invalid + unknown entries coexist; order kept."""
        bundle = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [
                {"request": {"method": "GET", "url": LOOKUP_OK}},
                {"request": {
                    "method": "GET",
                    "url": (
                        "CodeSystem/$lookup"
                        "?system=http://fake.example/sys&code=1"
                    ),
                }},
                {"request": {"method": "GET", "url": "Patient/1"}},
            ],
        }
        status, btype, statuses = _entry_status(
            fhir_client, bundle
        )
        assert status == 200 and btype == "batch-response"
        assert statuses == ["200", "400", "404"]

    def test_b33_entry_without_request_per_entry_400(
        self, fhir_client
    ):
        """Resource-only entry (no request) → per-entry 400, entry
        count preserved (never silently dropped)."""
        bundle = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [
                {"request": {"method": "GET", "url": LOOKUP_OK}},
                {"resource": {"resourceType": "Parameters"}},
                {"request": {"method": "GET", "url": LOOKUP_OK}},
            ],
        }
        status, _, statuses = _entry_status(fhir_client, bundle)
        assert status == 200
        assert len(statuses) == 3
        assert statuses[1] == "400"

    def test_b34_mixed_batch_200(self, fhir_client):
        """Batch (unlike transaction) MAY mix success/failure per
        entry — 200 + per-entry statuses is CORRECT here."""
        bundle = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [
                {"request": {"method": "GET", "url": LOOKUP_OK}},
                {"request": {"method": "GET", "url": "Patient/1"}},
            ],
        }
        status, _, statuses = _entry_status(fhir_client, bundle)
        assert status == 200
        assert statuses == ["200", "404"]

    def test_b35_response_bundle_shape(self, fhir_client):
        """Response Bundle: type=batch-response, one entry per
        request entry, each with response.status."""
        bundle = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [
                {"request": {"method": "GET", "url": LOOKUP_OK}},
            ],
        }
        r = fhir_client.post("/fhir", json=bundle)
        body = r.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "batch-response"
        assert len(body["entry"]) == 1
        assert "status" in body["entry"][0]["response"]

    def test_b36_cs_advertises_batch_and_transaction(
        self, fhir_client
    ):
        """CapabilityStatement rest interactions include batch AND
        transaction — B1's premise (transaction is a declared
        capability, so its semantics matter)."""
        m = fhir_client.get("/fhir/metadata").json()
        codes = [
            i.get("code")
            for r in m.get("rest", [])
            for i in r.get("interaction", [])
        ]
        assert "batch" in codes
        assert "transaction" in codes
