"""TS-77 registry live re-sweep — drift detection (2026-10-22).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261022c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: iteration 42 audited the registry's STRUCTURAL health; the
flip-on-fix discipline relies on pin suites, but open rows without
committed pins (sweep-era CFs) can drift silently when a fixbatch
lands a sibling contract. This iteration re-verifies a SAMPLE of open
rows LIVE to detect fixed-by-side-effect drift.

RESULT: ZERO DRIFT — all 9 sampled open findings re-verified ALIVE
exactly as registered (controls-only; 10th clean surface). Sampled:
F2 (vsd-3 coexistence 200), X1 (control-char divergence between
transports byte-visible), CF-SKEPTIC-VS02-01 (count=0 422),
CF-EXPLORER-TS01-01 (generic Accept → fhir+json), BC1 (custom ops
404 from batch w/ false diagnostic), P2 (searchset self-link absent),
HM1 (Allow header lies: 'GET' on a POST-capable route).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

SNOMED_URI = "http://snomed.info/sct"
NS = {"f": "http://hl7.org/fhir"}
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"


class TestRegistryDriftSample:
    """Live re-verification of sampled open rows."""

    def test_d10_f2_vsd3_coexistence_alive(self, fhir_client):
        """F2 (LOW): concept+filter in the same include block still
        accepted (200), not the vsd-3 400."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [{
                    "property": "concept", "op": "is-a",
                    "value": "73211009",
                }],
                "concept": [{"code": "44054006"}],
            }]},
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=vs)
        assert r.status_code == 200

    def test_d11_x1_cross_transport_divergence_alive(
        self, fhir_client
    ):
        """X1 (LOW): the \\x08 still survives JSON and is stripped in
        XML — the undocumented asymmetry holds."""
        params = {
            "system": SNOMED_URI, "code": "44054006",
            "display": "w\x08rong",
        }
        rj = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={**params, "_format": "json"},
        )
        jm = next(
            p.get("valueString")
            for p in rj.json()["parameter"]
            if p.get("name") == "message"
        )
        rx = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={**params, "_format": "xml"},
        )
        xm = None
        for p in ET.fromstring(rx.text).findall("f:parameter", NS):
            n = p.find("f:name", NS)
            if n is not None and n.get("value") == "message":
                v = p.find("f:valueString", NS)
                xm = v.get("value") if v is not None else None
        assert jm == 'The display "w\x08rong" is incorrect'
        assert xm == 'The display "wrong" is incorrect'

    def test_d12_count0_422_alive(self, fhir_client):
        """CF-SKEPTIC-VS02-01: count=0 still rejected 422 (spec
        permits an empty expansion)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "count": 0},
        )
        assert r.status_code == 422

    def test_d13_generic_accept_fhir_mime_alive(self, fhir_client):
        """CF-EXPLORER-TS01-01: Accept */* still yields fhir+json."""
        r = fhir_client.get(
            "/fhir/metadata", headers={"Accept": "*/*"}
        )
        assert "fhir+json" in r.headers["content-type"]

    def test_d14_bc1_custom_op_batch_404_alive(self, fhir_client):
        """BC1: $search from a batch entry still 404s."""
        batch = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [{
                "request": {
                    "method": "GET",
                    "url": "CodeSystem/$search?query=x",
                },
            }],
        }
        r = fhir_client.post("/fhir", json=batch)
        entry = r.json()["entry"][0]["response"]
        assert entry["status"] == "404"

    def test_d15_p2_self_link_absent_alive(self, fhir_client):
        """P2: searchset Bundle still carries no link[rel=self]."""
        r = fhir_client.get(
            "/fhir/CodeSystem", params={"url": SNOMED_URI}
        )
        assert r.json().get("link") is None

    def test_d16_hm1_allow_header_lies_alive(self, fhir_client):
        """HM1: OPTIONS on $lookup still advertises Allow: GET only
        while POST 200s."""
        r = fhir_client.options("/fhir/CodeSystem/$lookup")
        assert r.headers.get("allow") == "GET"
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
            ],
        }
        r2 = fhir_client.post(
            "/fhir/CodeSystem/$lookup", json=body
        )
        assert r2.status_code == 200
