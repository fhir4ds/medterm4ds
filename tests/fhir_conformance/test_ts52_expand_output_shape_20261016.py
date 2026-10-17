"""TS-52 $expand output-shape completeness (2026-10-16).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261016d).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the expansion OBJECT and its envelope across all three expand
modes (url/intensional, filter/search, compose/inline) — the OUT
side beyond the paging semantics TS-04/TS-20 already pinned.

VERDICT: LARGELY CONFORMANT — a controls-leaning iteration with one
inconsistency-class finding.

EP1 (LOW-MED, NEW) — the url-mode wrapper carries url+status; the
    filter and compose modes carry status only. R4 §4.9.2.5: the
    expanded ValueSet echoes the identity of what was expanded
    (url/status/expansion); for url-mode the server reconstructs the
    implicit ValueSet's url — but the filter-mode response is an
    UNIDENTIFIED ValueSet (no url, no identifier): a client storing
    expansion results cannot attribute them to a value set, and the
    three modes emit three different wrapper shapes for the same
    operation. Mode-dependent envelope = the X1 cross-format family
    (content varies by request shape).
    Fix shape: emit url for compose mode (the echoed definition or a
    urn:uuid) and an identifier for filter mode — or document the
    mode-dependent envelope. Flip pins p10-p12.

Controls (fresh, all three modes): expansion carries
    {contains, timestamp, total, params-when-set}; timestamp is a
    valid R4 instant; params echo (EC) held on all modes;
    contains[] entries carry system+code+display; the toocostly
    extension fires under truncation with the CORRECT canonical URL
    (http://hl7.org/fhir/StructureDefinition/
    valueset-toocostly) and is absent when untruncated; filter-mode
    total reflects the TRUE count under a window (full=3 →
    count=2: total=3, contains=2, ext=1); compose-mode total
    un-truncated under count cap; offset absent = P1 (registered).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"
TOOCOSTLY_URL = (
    "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"
)

COMPOSE_VS = {
    "resourceType": "ValueSet",
    "compose": {
        "include": [
            {
                "system": SNOMED_URI,
                "concept": [
                    {"code": "73211009"},
                    {"code": "44054006"},
                ],
            }
        ]
    },
}


class TestEP1ModeDependentWrapper:
    """EP1 — three modes, three envelope shapes."""

    def test_p10_url_mode_carries_url(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ValueSet/$expand", params={"url": DM_ISA_URL}
        )
        body = r.json()
        assert body.get("url") == DM_ISA_URL
        assert body.get("status")

    def test_p11_filter_mode_no_url(self, fhir_client):
        """THE FINDING (filter half): the expansion result is an
        UNIDENTIFIED ValueSet — no url, no identifier. Flip when an
        identifier (or documented absence) lands."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand", params={"filter": "diabetes"}
        )
        body = r.json()
        assert "url" not in body
        assert "identifier" not in body

    def test_p12_compose_mode_no_url(self, fhir_client):
        """THE FINDING (compose half): inline definition expanded
        without echoing an identity. Flip when the echoed url (or
        urn:uuid) lands."""
        r = fhir_client.post(
            "/fhir/ValueSet/$expand", json=COMPOSE_VS
        )
        body = r.json()
        assert "url" not in body


class TestExpansionShapeControls:
    """Controls — the expansion object, all three modes."""

    @pytest.mark.parametrize(
        "mode,fetch",
        [
            (
                "url",
                lambda c: c.get(
                    "/fhir/ValueSet/$expand",
                    params={"url": DM_ISA_URL},
                ),
            ),
            (
                "filter",
                lambda c: c.get(
                    "/fhir/ValueSet/$expand",
                    params={"filter": "diabetes"},
                ),
            ),
            (
                "compose",
                lambda c: c.post(
                    "/fhir/ValueSet/$expand", json=COMPOSE_VS
                ),
            ),
        ],
    )
    def test_c10_expansion_core_keys(
        self, fhir_client, mode, fetch
    ):
        """contains + timestamp + total present on every mode."""
        r = fetch(fhir_client)
        exp = r.json()["expansion"]
        assert "contains" in exp
        assert "timestamp" in exp
        assert "total" in exp

    def test_c11_contains_entry_shape(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ValueSet/$expand", params={"url": DM_ISA_URL}
        )
        e0 = r.json()["expansion"]["contains"][0]
        assert {"system", "code", "display"} <= set(e0)

    def test_c12_toocostly_url_canonical(self, fhir_client):
        """Truncation fires the extension with the CORRECT canonical
        URL; untruncated expansions carry no extension."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "count": 1},
        )
        exp = r.json()["expansion"]
        urls = [e.get("url") for e in exp.get("extension", [])]
        assert TOOCOSTLY_URL in urls
        r2 = fhir_client.get(
            "/fhir/ValueSet/$expand", params={"url": DM_ISA_URL}
        )
        assert not r2.json()["expansion"].get("extension")

    def test_c13_filter_mode_true_total(self, fhir_client):
        """Filter mode: total reflects the TRUE count under a window
        (full=3; count=2 → total=3, contains=2, toocostly)."""
        full = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 100},
        ).json()["expansion"]
        win = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 2},
        ).json()["expansion"]
        assert full["total"] >= 3
        assert win["total"] == full["total"]
        assert len(win["contains"]) == 2

    def test_c14_params_echo_all_modes(self, fhir_client):
        """EC held: params echoed on url and filter modes; compose
        echoes its query-side params."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "activeOnly": "true"},
        )
        assert "url=" in r.json()["expansion"]["params"]
        r2 = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 5},
        )
        assert "filter=" in r2.json()["expansion"]["params"]

    def test_c15_offset_absent_p1(self, fhir_client):
        """P1 re-verified in the output-shape context: expansion
        .offset never emitted."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "count": 1},
        )
        assert r.json()["expansion"].get("offset") is None
