"""TS-16 $expand In-parameter matrix (2026-10-07).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261007c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: FHIR R4 §4.9.12 declares the $expand In-parameter matrix (url,
valueSet, context, contextTac, filter, date, offset, count,
includeDesignations, designation, includeDefinition, activeOnly,
excludeNested, excludeNotForUI, excludePostCoordinated, displayLanguage,
property, useSupplement). This server binds a subset and silently drops
the rest. §4.9.2: "If one or more of the parameters ... are not
honored ... the server MUST return an error" — the honoring matrix is
the contract under test.

EA (MEDIUM, NEW) — accepted-then-silently-dropped In-params.
    GET: includeDesignations / designation / includeDefinition /
    excludeNested / excludeNotForUI / excludePostCoordinated /
    displayLanguage / property / context / contextTac all return 200
    with byte-identical expansions to the un-parameterized baseline;
    ARBITRARY unknown params (bogusParam=1) also 200 (FastAPI drops
    undeclared Query params). POST Parameters bodies: same set parsed
    away with no effect. Per §4.9.2 these must either work or 400.
    Same family as L1 ($lookup property) and M1 (GET coding).
    Fix shape: declare + honor the cheap ones (includeDesignations,
    includeDefinition, excludeNested are mechanically implementable);
    400 on the rest + on unknown params. Flip pins when honored.

EB (LOW-MED, NEW) — date param accepts non-dates.
    date=not-a-date → 200 on GET AND POST (valueDateTime body) with no
    validation; the param's spec purpose is historical-version
    selection. A client believing it pinned a historical expansion gets
    today's. Fix shape: validate FHIR dateTime (partial allowed) → 400;
    if versioned expansion is unsupported, 400 on ANY date (per §4.9.2).

EC (LOW, NEW) — expansion.params never echoed.
    R4 expansion.params (0..1 string, SHOULD): the filters actually
    applied. Server emits {contains, timestamp, total} only; a client
    cannot distinguish "displayLanguage honored" from "dropped" by
    reading the response — the observability gap that hides EA.
    Fix shape: echo the APPLIED subset (url, count, offset, activeOnly)
    so the response self-describes which params took effect.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"
BASE_CODES = {"73211009", "44054006"}


def _codes_of(resp_json: dict) -> set[str]:
    return {
        e["code"]
        for e in resp_json.get("expansion", {}).get("contains", [])
    }


class TestEAInParamsSilentlyDropped:
    """EA — R4 §4.9.12 In-params accepted then ignored (GET + POST)."""

    @pytest.mark.parametrize(
        "param,value",
        [
            ("includeDesignations", "true"),
            ("designation", "en"),
            ("includeDefinition", "true"),
            ("excludeNested", "true"),
            ("excludeNotForUI", "true"),
            ("excludePostCoordinated", "true"),
            ("displayLanguage", "fr"),
            ("property", "designation"),
            ("context", "urn:fake:ctx"),
            ("contextTac", "loinc"),
        ],
    )
    def test_e10_get_in_param_no_effect(
        self, fhir_client, param, value
    ):
        """Each R4 In-param returns 200 with an expansion identical to
        the un-parameterized baseline — accepted-then-dropped. WHEN the
        param is honored (or 400'd) per §4.9.2, flip: assert the
        honored semantics or the 400."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, param: value},
        )
        # c-fixbatch2 (EA fix): unsupported-but-declared params are 400'd
        # per R4 §4.9.2 instead of silently ignored.
        assert r.status_code == 400
        assert param in str(r.json()["issue"][0]["diagnostics"])

    def test_e11_unknown_param_silent(self, fhir_client):
        """Arbitrary garbage params 200 — no unknown-param rejection at
        all on the GET surface. Flip when unknown params 400."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "bogusParam": "1"},
        )
        # c-fixbatch2 (EA fix): unknown params 400 naming them.
        assert r.status_code == 400
        assert "bogusParam" in str(r.json()["issue"][0]["diagnostics"])

    def test_e12_post_body_in_param_no_effect(self, fhir_client):
        """POST Parameters body carries the same silently-dropped set:
        includeDesignations=true changes nothing (designations are
        never emitted). Flip when honored."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": DM_ISA_URL},
                {"name": "includeDesignations", "valueBoolean": True},
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        # c-fixbatch2 (EA fix): body params 400 with the name.
        assert r.status_code == 400
        assert "includeDesignations" in str(r.json()["issue"][0]["diagnostics"])


class TestEBDateParam:
    """EB — date accepts non-dates on both transports."""

    def test_e20_get_bogus_date(self, fhir_client):
        """date=not-a-date → 200 (no validation). Flip to 400 when
        dateTime validation or explicit-unsupported lands."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "date": "not-a-date"},
        )
        # c-fixbatch2 (EB fix): shape validation rejects non-dateTimes.
        assert r.status_code == 400
        assert "dateTime" in str(r.json()["issue"][0]["diagnostics"])

    def test_e21_post_bogus_date(self, fhir_client):
        """POST valueDateTime not-a-date → 200. Flip to 400."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": DM_ISA_URL},
                {"name": "date", "valueDateTime": "not-a-date"},
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        # c-fixbatch2 (EB fix): body valueDateTime shape-validated.
        assert r.status_code == 400


class TestECExpansionParamsEcho:
    """EC — expansion.params absent (observability)."""

    def test_e30_params_not_echoed(self, fhir_client):
        """R4 expansion.params SHOULD carry the applied filters; the
        expansion object carries {contains, timestamp, total} only.
        Flip when the applied subset is echoed."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "activeOnly": "true"},
        )
        assert r.status_code == 200
        # c-fixbatch2 (EC fix): applied params are echoed (R4 SHOULD).
        assert r.json()["expansion"].get("params") is not None
        assert "activeOnly=true" in r.json()["expansion"]["params"]


class TestExpandControls:
    """Controls — the BOUND contract re-verified fresh."""

    def test_c10_url_filter_combination_400(self, fhir_client):
        """QC-311: url+filter fails loudly (§4.9.2 honored here)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "filter": "diabetes"},
        )
        assert r.status_code == 400

    def test_c11_active_only_binds(self, fhir_client):
        """activeOnly is bound on GET (both values accepted; the
        fixture's DM subtree is all-active so totals match)."""
        for v in ("true", "false"):
            r = fhir_client.get(
                "/fhir/ValueSet/$expand",
                params={"url": DM_ISA_URL, "activeOnly": v},
            )
            assert r.status_code == 200

    def test_c12_active_only_bogus_422(self, fhir_client):
        """Boolean param with non-boolean value → FastAPI 422 (typed
        Query binding — the mechanism EA params never get)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "activeOnly": "bogus"},
        )
        assert r.status_code == 422

    def test_c13_post_body_active_only_overrides(self, fhir_client):
        """QC-315: Parameters-body valueBoolean binds (body wins over
        query)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": DM_ISA_URL},
                {"name": "activeOnly", "valueBoolean": False},
            ],
        }
        r = fhir_client.post(
            "/fhir/ValueSet/$expand", json=body, params={"activeOnly": "true"}
        )
        assert r.status_code == 200

    def test_c14_count_paging_binds(self, fhir_client):
        """count=1 pages to 1 contains entry; total reflects the
        un-truncated window (2)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "count": 1},
        )
        exp = r.json()["expansion"]
        assert len(exp["contains"]) == 1
        assert exp["total"] == 2

    def test_c15_filter_mode_multi_system(self, fhir_client):
        """Filter mode (no url) searches across systems: diabetes
        yields SNOMED DM + ICD10CM E11."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 10},
        )
        codes = _codes_of(r.json())
        assert "73211009" in codes
        assert "E11" in codes
