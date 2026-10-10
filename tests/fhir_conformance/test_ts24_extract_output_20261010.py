"""TS-24 $extract output contract + filter-param tail (2026-10-10).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261010c).
FRESH EVIDENCE — live probes executed this run against the real GLiNER
+ ConText + search pipeline (each POST ~165s cold / ~5s warm); no
prior-run results cited.

Scope: the $extract OUTPUT contract (the clinical payload — first
conformance-level pin of the record shape and the assertion-status
taxonomy) plus the filter-parameter surface (includeNegated et al.)
and the op-tail of the unknown-param family.

XA1 (MEDIUM, NEW) — unknown-param rejection absent at $extract/$search.
    bogusParam=1 → 200 on BOTH ops (GET); engine=bogus → 200 on BOTH
    (extract declares engine; $search's G2 already noted unguarded
    enums there — on extract the enum gap is new). This is W4's op
    tail #3: fixbatches covered lookup/validate-code/subsumes (U3)
    and translate/closure (W4) — extract/search never joined.
    Fix shape: _EXTRACT_KNOWN_PARAMS/_SEARCH_KNOWN_PARAMS via
    reject_unknown_query_params + body-side name checks (the W4
    pattern, mechanical). Flip pins x10-x12.

XA2 (LOW, NEW) — plausible filter spellings silently accepted.
    includeFamily binds (status 'family' surfaces); the natural longer
    spellings includeFamilyHistory / includeHistoricalFamily 200 with
    ZERO results — a client using the descriptive spelling gets a
    confident empty answer (EA-family shape at the VALUE level: the
    param NAME is unknown but the request succeeds).
    Fix shape: rides with XA1's known-sets (unknown names 400) — the
    spellings become 400s naming the accepted set. Flip pin x20/x21.

Controls (fresh, first conformance-level pins):
    STATUS TAXONOMY — affirmed / negated / uncertain / family observed
    live on a 4-assertion text (T2DM affirmed; hypertension negated;
    pneumonia uncertain; asthma family). DEFAULT EXCLUSION — negated/
    uncertain/family all excluded by default (total=0 on the 3-assertion
    text); includeNegated/includeUncertain/includeFamily each surface
    their class on BOTH transports (GET query + POST body valueBoolean).
    RECORD SHAPE — code/source/system/display/matched_text/status/
    section/confidence/match_grade/ner_label/result_type/canonical_id/
    combination_members/span_start/span_end all present; spans index
    the matched_text in the input (T2DM span 12-27 = 'type 2 diabetes').
    Bundle searchset + urn:uuid fullUrls + search.mode=match.
"""

from __future__ import annotations

import pytest

TEXT_4 = (
    "Patient has type 2 diabetes. No evidence of hypertension. "
    "Family history of asthma. Possible pneumonia."
)


def _codes(body: dict) -> list[str]:
    return [
        e["resource"]["code"] for e in body.get("entry", [])
    ]


def _status_of(body: dict, code: str) -> str | None:
    for e in body.get("entry", []):
        if e["resource"]["code"] == code:
            return e["resource"]["status"]
    return None


class TestXA1UnknownParamsExtractSearch:
    """XA1 — W4's op tail #3: extract/search."""

    def test_x10_extract_bogus_param(self, fhir_client):
        """bogusParam on $extract GET → 200 (unknown-param rejection
        never reached this op). Flip when _EXTRACT_KNOWN_PARAMS
        lands."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": "diabetes", "bogusParam": "1"},
        )
        assert r.status_code == 200

    def test_x11_extract_bogus_engine(self, fhir_client):
        """engine=bogus on $extract → 200 (enum unguarded — G2's shape
        on the extract op). Flip with the known-set + enum check."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": "diabetes", "engine": "bogus"},
        )
        assert r.status_code == 200

    def test_x12_search_bogus_param(self, fhir_client):
        """bogusParam on $search GET → 200. Flip with
        _SEARCH_KNOWN_PARAMS."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes",
                "searchMode": "canonical",
                "bogusParam": "1",
            },
        )
        assert r.status_code == 200


class TestXA2FilterSpellings:
    """XA2 — plausible spellings 200 with confident-empty answers."""

    @pytest.mark.parametrize(
        "param", ["includeFamilyHistory", "includeHistoricalFamily"]
    )
    def test_x20_descriptive_spelling_empty(self, fhir_client, param):
        """The descriptive spellings are silently accepted with ZERO
        results — the client believes it requested family history.
        Flip when XA1's known-sets 400 these names."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$extract",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "text", "valueString": TEXT_4},
                    {"name": param, "valueBoolean": True},
                ],
            },
        )
        assert r.status_code == 200
        # The unknown spelling is a NO-OP: output identical to the
        # unfiltered request (affirmed-only, total=1) — the family
        # history the client asked for is silently absent.
        assert r.json()["total"] == 1
        assert _codes(r.json()) == ["44054006"]

    def test_x21_binding_spelling_works(self, fhir_client):
        """Control-within-finding: includeFamily (the binding
        spelling, matching CLI --include-family) DOES surface the
        family assertion."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$extract",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "text", "valueString": TEXT_4},
                    {"name": "includeFamily", "valueBoolean": True},
                ],
            },
        )
        assert r.status_code == 200
        assert _status_of(r.json(), "195967001") == "family"


class TestExtractOutputControls:
    """Controls — status taxonomy, exclusion defaults, record shape."""

    def test_c10_default_excludes_modalities(self, fhir_client):
        """Negated/uncertain/family assertions are EXCLUDED by
        default: the 4-assertion text yields only the affirmed T2DM."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$extract",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "text", "valueString": TEXT_4},
                ],
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert _codes(r.json()) == ["44054006"]
        assert _status_of(r.json(), "44054006") == "affirmed"

    def test_c11_negated_and_uncertain_surface(self, fhir_client):
        """includeNegated + includeUncertain surface their classes
        with correct status attribution."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$extract",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "text", "valueString": TEXT_4},
                    {"name": "includeNegated", "valueBoolean": True},
                    {"name": "includeUncertain", "valueBoolean": True},
                ],
            },
        )
        assert r.status_code == 200
        assert _status_of(r.json(), "38341003") == "negated"
        assert _status_of(r.json(), "233604007") == "uncertain"

    def test_c12_get_transport_filters(self, fhir_client):
        """GET query carries the same boolean filters (transport
        parity for the binding spellings)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT_4, "includeNegated": "true"},
        )
        assert r.status_code == 200
        assert _status_of(r.json(), "38341003") == "negated"

    def test_c13_record_shape(self, fhir_client):
        """First conformance-level pin of the extract record: every
        documented field present, spans index the input text."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$extract",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "text", "valueString": TEXT_4},
                ],
            },
        )
        entry = r.json()["entry"][0]
        assert entry["search"] == {"mode": "match"}
        assert entry["fullUrl"].startswith("urn:uuid:")
        res = entry["resource"]
        for field in (
            "code", "source", "system", "display", "matched_text",
            "status", "confidence", "match_grade", "ner_label",
            "result_type", "canonical_id", "combination_members",
            "span_start", "span_end",
        ):
            assert field in res, field
        assert TEXT_4[res["span_start"]:res["span_end"]] == (
            res["matched_text"]
        )
        assert res["canonical_id"] == "VAL-COND-SNOMED-44054006"

    def test_c14_bundle_shape(self, fhir_client):
        body = fhir_client.post(
            "/fhir/CodeSystem/$extract",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "text", "valueString": TEXT_4},
                ],
            },
        ).json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "searchset"
        assert body["total"] == len(body["entry"])
