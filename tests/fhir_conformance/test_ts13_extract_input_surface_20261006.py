"""TS-13 $extract input surface (2026-10-06).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261006e).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the custom $extract operation's INPUT contract — text payload
validation, transport parity, and the advertised OperationDefinition.
(Iterations 15/16 covered resultTypes handling on $extract/$search;
this sweep covers the text parameter surface + capability
introspection.)

Survey (fresh, this run; real GLiNER pipeline exercised):
* Happy path GET + POST: 200 searchset Bundle, total=1 for a diabetes
  mention; entry carries fullUrl urn:uuid + resource.
* Text limit is exactly 50,000 chars (x*50000 → 200; x*51000 → 400),
  overridable via MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS; both diagnostics
  are actionable (GET names the cap; POST names cap + env override).
* POST empty Parameters → 400 "text is required"; GET missing text →
  422 (required Query param).
* POST binding parity for the text param (valueString) — 200.

W1 (MEDIUM, NEW) — over-limit text: 422 on GET vs 400 on POST.
    Same logical violation (text > 50,000 chars), different status by
    transport: GET rejects via FastAPI Query max_length → 422
    ("String should have at most 50000 characters"); POST rejects via
    the handler's length check → 400 ("text length ... exceeds max
    ... set MEDTERM4DS_MAX_EXTRACT_TEXT_CHARS to override"). R4
    §3.1.0.5/§2.1.0.5 map validation failures to 400; the framework's
    422 on GET is the TS-10 transport-parity family (M1) with the
    polarity reversed (there POST was the capable one). Clients
    porting transports get different codes + severities for the same
    bad input. Fix: normalize — catch the GET-side validation and
    re-encode as 400 (the $search ValueError→400 wrapper pattern,
    QA-005) or accept 422 as the uniform code on both.

W2 (LOW, NEW) — whitespace-only text runs the full NLP pipeline.
    text='   ' → 200 empty searchset after a COMPLETE pipeline pass
    (fresh app: 79s incl. model load; warm: full NLP pass). A
    guaranteed-zero-result request pays full extraction cost —
    trivially scriptable resource-exhaustion surface (the 50k cap
    bounds per-request size but not per-request work). Fix: strip()
    pre-check → 400 (mirroring the empty-string 422 contract) or
    short-circuit to the empty Bundle before pipeline invocation.

W3 (LOW, NEW) — advertised OperationDefinition URLs are unresolvable.
    CapabilityStatement declares $extract/$search definitions at
    http://medterm4ds.org/fhir/... — the domain does not resolve
    (DNS), and no local /fhir/OperationDefinition/{name} route exists
    (404). A client cannot introspect the custom operations' contracts
    anywhere. Fix: host the OperationDefinition resources at the
    declared path on this server (or re-point the declaration at a
    served route) — same advertised-capability family as Q1 (TS-11).

Controls: happy-path shapes, empty/missing text rejection, exact
    50,000 boundary, POST binding parity, whitespace empty-Bundle
    output shape (valid searchset).
"""

from __future__ import annotations

import pytest

TEXT_PARAM = {"name": "text", "valueString": "patient has type 2 diabetes"}


def _post_extract(fhir_client, text: str):
    return fhir_client.post(
        "/fhir/CodeSystem/$extract",
        json={
            "resourceType": "Parameters",
            "parameter": [{"name": "text", "valueString": text}],
        },
    )


class TestW1TransportStatusDivergence:
    """W1 — over-limit text: 422 GET vs 400 POST."""

    OVER = "x" * 50_001

    def test_w10_get_over_limit_422(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$extract", params={"text": self.OVER}
        )
        assert r.status_code == 422
        assert r.json()["resourceType"] == "OperationOutcome"
        assert "50000" in r.json()["issue"][0]["diagnostics"]

    def test_w11_post_over_limit_400(self, fhir_client):
        r = _post_extract(fhir_client, self.OVER)
        assert r.status_code == 400
        assert r.json()["resourceType"] == "OperationOutcome"
        assert "50000" in r.json()["issue"][0]["diagnostics"]

    def test_w12_divergence_pinned(self, fhir_client):
        """THE FINDING: same violation, different status per
        transport. WHEN normalized (both 400, or both 422), flip
        w10/w11/w12 to the uniform expectation."""
        g = fhir_client.get(
            "/fhir/CodeSystem/$extract", params={"text": self.OVER}
        )
        p = _post_extract(fhir_client, self.OVER)
        assert (g.status_code, p.status_code) == (422, 400), (
            f"GET/POST over-limit codes now ({g.status_code}, "
            f"{p.status_code}) — normalized; flip these pins."
        )


class TestW2WhitespacePipelineCost:
    """W2 — whitespace-only text pays full pipeline cost."""

    def test_w20_whitespace_returns_empty_searchset(self, fhir_client):
        """Current contract: whitespace runs the pipeline and returns
        a valid empty searchset (total=0, entry=[]). NOTE: unlike
        QC-330's valueless omission, entry IS present-and-empty here.
        WHEN the pre-trim lands, flip to 400 (mirroring empty-string
        rejection)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$extract", params={"text": "   "}
        )
        assert r.status_code == 200
        j = r.json()
        assert j["resourceType"] == "Bundle"
        assert j["type"] == "searchset"
        assert j["total"] == 0


class TestW3OperationDefinitionUnresolvable:
    """W3 — advertised OperationDefinition URLs 404/DNS-dead."""

    def test_w30_capability_declares_custom_ops(self, fhir_client):
        """Control: the CapabilityStatement DOES declare $extract and
        $search with medterm4ds.org definition URLs (the advertised
        contract this finding measures)."""
        m = fhir_client.get("/fhir/metadata").json()
        cs_ops = []
        for r in m.get("rest", [{}])[0].get("resource", []):
            for o in r.get("operation", []):
                cs_ops.append(o.get("name"))
        assert "extract" in cs_ops and "search" in cs_ops

    def test_w31_local_opdef_route_404(self, fhir_client):
        """No local OperationDefinition route serves the declared
        contracts. WHEN hosted, flip to 200 + OperationDefinition
        body."""
        r = fhir_client.get("/fhir/OperationDefinition/CodeSystem-extract")
        assert r.status_code == 404, (
            f"OperationDefinition now served ({r.status_code}) — flip "
            "this pin."
        )

    def test_w32_declared_domain_unresolvable(self):
        """The declared definition host does not resolve in DNS —
        the URLs are unresolvable ANYWHERE (asserted at the socket
        layer, not via the app)."""
        import socket

        with pytest.raises(OSError):
            socket.getaddrinfo("medterm4ds.org", 80)


class TestExtractInputControls:
    """Controls: documented input contract, fresh."""

    def test_e10_exact_boundary_50000_ok(self, fhir_client):
        """Exactly 50,000 chars is accepted (limit is inclusive-max).
        Runs the pipeline; boundary filler 'x' yields no concepts."""
        r = _post_extract(fhir_client, "x" * 50_000)
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_e11_post_empty_params_400(self, fhir_client):
        r = fhir_client.post(
            "/fhir/CodeSystem/$extract",
            json={"resourceType": "Parameters", "parameter": []},
        )
        assert r.status_code == 400
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_e12_get_empty_text_422(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$extract", params={"text": ""}
        )
        assert r.status_code == 422

    def test_e13_post_happy_path_bundle(self, fhir_client):
        """POST with a real clinical mention → searchset with ≥1
        entry (fullUrl urn:uuid + resource)."""
        r = _post_extract(fhir_client, "patient has type 2 diabetes")
        assert r.status_code == 200
        j = r.json()
        assert j["type"] == "searchset"
        assert j["total"] >= 1
        assert j["entry"][0]["fullUrl"].startswith("urn:uuid:")
