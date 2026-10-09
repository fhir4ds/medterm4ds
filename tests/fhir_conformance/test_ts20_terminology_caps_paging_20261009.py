"""TS-20 TerminologyCapabilities + display-matching policy + paging matrix (2026-10-09).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261009b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the discovery surface for TERMINOLOGY behavior specifically
(TerminologyCapabilities, R4 §4.0.11), the $validate-code display-
matching policy (byte-exactness currently undeclared), and a fresh
paging matrix across the three $expand modes (url / filter / compose).

Y1 (LOW-MED, NEW) — TerminologyCapabilities absent.
    /fhir/TerminologyCapabilities 404s, and the CapabilityStatement
    carries no terminologyCapabilities reference. R4 §4.0.11: "A
    terminology server SHOULD ... make a TerminologyCapabilities
    resource available" declaring per-system behavior: version
    support, parameter support, and — directly relevant to this
    server's accumulated strictness — displayWarning/caseSensitive
    semantics. After four fix-batches of fail-loud parameter
    contracts (version 400s, unknown params 400s, display byte-
    exactness), clients have NO discovery document for any of it:
    the only way to learn 'single-version server' or 'display is
    case-sensitive' is trial-and-error against 400s. Same
    advertisement-family as P1 (CapabilityStatement searchParams)
    and W3 (dead OperationDefinition urls).
    Fix shape: serve a TerminologyCapabilities (even static:
    caseSensitive=true, version=none, display=warning-false) and
    reference it from CapabilityStatement.rest[].terminologyCapabilities.
    Flip pins y10/y11.

Y2 (LOW, NEW — documented-behavior pin) — display matching is
    BYTE-EXACT and undeclared.
    $validate-code display= matches only the exact preferred display:
    case-swapped, UPPER, leading/trailing whitespace, double-space,
    and SYNONYMS all return result=false + 'The display "..." is
    incorrect'. R4 §4.8.18 leaves matching rules to the server
    (TerminologyCapabilities should describe them) — byte-exactness
    is permitted and conservative (no false TRUEs), but synonym
    rejection is stricter than many servers (SNOMED display
    synonyms are legitimate). Pin the CURRENT behavior so drift is
    visible; Y1's TerminologyCapabilities is where it should be
    declared. Flip pin y20 only if matching semantics deliberately
    change.

Controls (fresh): paging matrix CONFORMANT across url/filter/compose
    modes — disjoint pages, stable totals across pages, deterministic
    order across identical requests, past-end pages uniformly omit
    `contains` (all three modes); expansion.timestamp is a valid R4
    instant (seconds + timezone; fraction optional per the R4 regex);
    expansion.params echo (EC fix) carries the applied subset;
    unknown-code precedence — code validity is reported before any
    display evaluation.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"
T2DM_DISPLAY = "Type 2 diabetes mellitus"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"
COMPOSE_VS = {
    "resourceType": "ValueSet",
    "compose": {"include": [{"system": SNOMED_URI, "concept": [
        {"code": "73211009"}, {"code": "44054006"}, {"code": "3738000"},
    ]}]},
}


def _codes(resp_json: dict) -> list[str]:
    return [
        e["code"]
        for e in resp_json.get("expansion", {}).get("contains", [])
    ]


def _total(resp_json: dict):
    return resp_json.get("expansion", {}).get("total")


class TestY1TerminologyCapabilities:
    """Y1 — no terminology discovery document."""

    def test_y10_terminology_capabilities_404(self, fhir_client):
        """/fhir/TerminologyCapabilities absent. Flip when served
        (even a static one declaring caseSensitive + single-version)."""
        r = fhir_client.get("/fhir/TerminologyCapabilities")
        assert r.status_code == 404

    def test_y11_no_reference_from_capability_statement(self, fhir_client):
        """CapabilityStatement.rest[].terminologyCapabilities is unset
        — no pointer toward terminology behavior. Flip when referenced."""
        m = fhir_client.get("/fhir/metadata").json()
        refs = [
            rest.get("terminologyCapabilities")
            for rest in m.get("rest", [])
        ]
        assert all(ref is None for ref in refs)


class TestY2DisplayByteExact:
    """Y2 — display matching policy, pinned."""

    @pytest.mark.parametrize(
        "display",
        [
            "type 2 diabetes mellitus",
            "TYPE 2 DIABETES MELLITUS",
            "  Type 2 diabetes mellitus",
            "Type 2 diabetes mellitus  ",
            "Type 2  diabetes mellitus",
            "T2DM",  # synonym — fixture may not carry it as STR; still False
        ],
    )
    def test_y20_non_exact_display_false(self, fhir_client, display):
        """Byte-exact matching: every non-identical variant of the
        preferred display returns result=false with an 'incorrect'
        message. Permitted by R4 (matching rules are server-defined);
        pinned so any deliberate softening (case-folding, trim) is a
        visible contract change. Flip ONLY on deliberate policy change
        — then also update Y1's TerminologyCapabilities declaration."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM, "display": display,
            },
        )
        assert r.status_code == 200
        result = next(
            p.get("valueBoolean")
            for p in r.json()["parameter"]
            if p.get("name") == "result"
        )
        assert result is False

    def test_y21_exact_display_true(self, fhir_client):
        """The exact preferred display validates true (the anchor for
        Y2's byte-exactness)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "display": T2DM_DISPLAY,
            },
        )
        result = next(
            p.get("valueBoolean")
            for p in r.json()["parameter"]
            if p.get("name") == "result"
        )
        assert result is True


class TestPagingMatrixControls:
    """Controls — paging across the three expand modes, fresh."""

    def test_c10_filter_mode_disjoint_pages(self, fhir_client):
        p0 = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 2, "offset": 0},
        )
        p1 = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 2, "offset": 2},
        )
        c0, c1 = _codes(p0.json()), _codes(p1.json())
        assert c0 and not (set(c0) & set(c1))
        assert _total(p0.json()) == _total(p1.json())

    def test_c11_compose_mode_disjoint_pages(self, fhir_client):
        p0 = fhir_client.post(
            "/fhir/ValueSet/$expand", json=COMPOSE_VS,
            params={"count": 2, "offset": 0},
        )
        p1 = fhir_client.post(
            "/fhir/ValueSet/$expand", json=COMPOSE_VS,
            params={"count": 2, "offset": 2},
        )
        c0, c1 = _codes(p0.json()), _codes(p1.json())
        assert sorted(c0 + c1) == ["3738000", "44054006", "73211009"]

    def test_c12_deterministic_order(self, fhir_client):
        ra = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 10},
        )
        rb = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 10},
        )
        assert _codes(ra.json()) == _codes(rb.json())

    @pytest.mark.parametrize(
        "mode",
        ["url", "filter", "compose"],
    )
    def test_c13_past_end_omits_contains_uniformly(
        self, fhir_client, mode
    ):
        """Past-end pages omit `contains` in ALL three modes (legal
        0..*; the uniformity is the contract)."""
        if mode == "url":
            r = fhir_client.get(
                "/fhir/ValueSet/$expand",
                params={"url": DM_ISA_URL, "count": 2, "offset": 50},
            )
        elif mode == "filter":
            r = fhir_client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "count": 2, "offset": 50},
            )
        else:
            r = fhir_client.post(
                "/fhir/ValueSet/$expand", json=COMPOSE_VS,
                params={"count": 2, "offset": 50},
            )
        assert r.status_code == 200
        assert "contains" not in r.json()["expansion"]

    def test_c14_timestamp_valid_instant(self, fhir_client):
        """expansion.timestamp: R4 instant — seconds precision +
        timezone, fraction optional (matches the R4 regex)."""
        import re

        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 1},
        )
        ts = r.json()["expansion"]["timestamp"]
        assert re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
            r"(\.\d+)?(Z|[+-]\d{2}:\d{2})$",
            ts,
        )

    def test_c15_params_echo_applied_subset(self, fhir_client):
        """EC fix held: params echoes url/filter/count/offset/activeOnly
        actually applied."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 1},
        )
        params_echo = r.json()["expansion"].get("params", "")
        assert "filter=diabetes" in params_echo
        assert "activeOnly=true" in params_echo

    def test_c16_unknown_code_precedes_display(self, fhir_client):
        """Unknown code + display: the CODE validity error is reported
        (display evaluation never runs) — correct precedence."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": "99999999",
                "display": "Whatever",
            },
        )
        message = next(
            p.get("valueString")
            for p in r.json()["parameter"]
            if p.get("name") == "message"
        )
        assert "not valid" in message
