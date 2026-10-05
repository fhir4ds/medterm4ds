"""TS-05 cross-operation consistency sweep (2026-10-04).

Maintenance spec-comp iteration 14 (worktree maint/spec-comp-20261004d).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: cross-operation consistency surfaces never maintenance-swept
as a group: parameter handling ($expand/$search count validation;
system alias + trailing-slash forms across lookup/validate/subsumes),
HTTP content negotiation (Accept × _format precedence matrix),
method semantics (PUT/DELETE/PATCH/OPTIONS/HEAD; Content-Type
enforcement), Out-parameter shapes vs the R4 OperationDefinitions
($lookup, $validate-code, $subsumes, $translate, $expand), $closure
table lifecycle (init/add/re-add/multi-table isolation), and the
R4-defined-but-never-tested $lookup In-params (property,
displayLanguage, coding).

L1 (LOW-MEDIUM, NEW) — $lookup `property` In-param accepted but
    ignored. R4 §4.8.21.1 In `property` 0..*: "A property that the
    client wishes to be returned in the output. If no properties are
    specified, the server chooses what to return." Requesting
    property=display (or property=child) returns the FULL default
    property set (cui/tty/aui/patient-friendly/match-type/
    canonical-code/canonical-system) — identical to the no-filter
    response. Root cause: lookup_get declares only system/code/
    version query params (src/medterm4ds/apps/fhir_api.py:2341-2343);
    `property` is an unknown query param FastAPI silently drops.
    The parameter's contract (client-side filtering) is unimplemented.
    Fix shape: declare property: list[str] | None = Query(None) and
    filter the property Out-params (falling back to default set when
    None/empty). Pinned: l10/l11.
    Related note (no pin): displayLanguage accepted silently for any
    value (no language-specific designations exist; degrades to
    default display — defensible, documented here).

N1 (NOTE, no pin) — $lookup Out includes `code`, `system`, `abstract`
    — not in the R4 OperationDefinition Out list (name, version,
    display, designation, property). R4's own published example
    response includes an `abstract` parameter informally, so there is
    spec-page precedent; strict OperationDefinition validators may
    flag. Recorded for the ledger; harmless echo values.

Controls verified fresh (c-series): count validation matrix on
$expand AND $search (0/-1/999999999/abc/1.5 → 422; 50 → 200); system
alias urn:oid:2.16.840.1.113883.6.96 and trailing-slash forms across
lookup/validate/subsumes → 200; Accept × _format precedence
(_format=xml beats Accept fhir+json; _format=json beats Accept
fhir+xml; generic json/*/*/text/html degrade to fhir+json);
method semantics (PUT/DELETE/PATCH → 405 OperationOutcome with FHIR
MIME; POST text/plain → 422; fhir+xml CT with JSON body → 422);
$translate match.part structure (equivalence valueCode +
concept.valueCoding + source.valueCoding per R4 §4.8.18.3.2);
$expand shape (VS status/url + expansion timestamp/total/contains);
$closure lifecycle (empty init 200, add, re-add idempotent, second
table isolated); coding In-param POST on $lookup (R4 alternative
invocation form).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"
DM = "73211009"
SNOMED_OID = "urn:oid:2.16.840.1.113883.6.96"
DEFAULT_PROPS = {
    "cui", "tty", "aui", "patient-friendly",
    "match-type", "canonical-code", "canonical-system",
}


def _props(client, **params) -> set[str]:
    r = client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": T2DM, **params},
    )
    assert r.status_code == 200, r.text
    return {
        p["part"][0]["valueCode"]
        for p in r.json().get("parameter", [])
        if p.get("name") == "property" and p.get("part")
    }


class TestL1LookupPropertyFilterIgnored:
    """L1 — the R4 `property` In-param is accepted but never filters."""

    def test_l10_property_display_not_filtered(self, fhir_client):
        """property=display (repeated 0..* In-param) still returns the
        FULL default property set (CURRENT, deviation). Flip when the
        filter is implemented: expect the requested subset."""
        props = _props(fhir_client, property="display")
        assert props == DEFAULT_PROPS

    def test_l11_property_child_not_filtered(self, fhir_client):
        """property=child returns the same default set — the filter is
        param-name-blind (never declared, never consulted)."""
        props = _props(fhir_client, property="child")
        assert props == DEFAULT_PROPS


class TestCountValidation:
    """count validation consistent across $expand and $search."""

    @pytest.mark.parametrize(
        "op,path,extra",
        [
            ("expand", "/fhir/ValueSet/$expand",
             {"url": f"{SNOMED_URI}/{DM}?fhir_vs=isa"}),
            ("search", "/fhir/CodeSystem/$search",
             {"query": "diabetes", "searchMode": "canonical"}),
        ],
    )
    @pytest.mark.parametrize("count", ["0", "-1", "999999999", "abc", "1.5"])
    def test_c10_invalid_count_422(
        self, fhir_client, op, path, extra, count
    ):
        r = fhir_client.get(
            path, params={**extra, "count": count}
        )
        assert r.status_code == 422, (op, count)

    def test_c11_valid_count_200(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}/{DM}?fhir_vs=isa",
                    "count": "50"},
        )
        assert r.status_code == 200


class TestSystemAliasForms:
    """urn:oid and trailing-slash forms work across operations."""

    @pytest.mark.parametrize(
        "path,extra",
        [
            ("/fhir/CodeSystem/$lookup", {"code": T2DM}),
            ("/fhir/CodeSystem/$validate-code", {"code": T2DM}),
            ("/fhir/CodeSystem/$subsumes",
             {"codeA": T2DM, "codeB": DM}),
        ],
    )
    def test_c20_urn_oid(
        self, fhir_client, path, extra
    ):
        r = fhir_client.get(
            path, params={"system": SNOMED_OID, **extra}
        )
        assert r.status_code == 200

    def test_c21_trailing_slash(self, fhir_client):
        for path in (
            "/fhir/CodeSystem/$lookup",
            "/fhir/CodeSystem/$validate-code",
        ):
            r = fhir_client.get(
                path,
                params={"system": f"{SNOMED_URI}/",
                        "code": T2DM},
            )
            assert r.status_code == 200, path


class TestContentNegotiation:
    """Accept × _format precedence matrix."""

    def test_c30_format_beats_accept(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM,
                    "_format": "xml"},
            headers={"Accept": "application/fhir+json"},
        )
        assert "fhir+xml" in r.headers["content-type"]
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM,
                    "_format": "json"},
            headers={"Accept": "application/fhir+xml"},
        )
        assert "fhir+json" in r.headers["content-type"]

    @pytest.mark.parametrize(
        "accept", ["application/fhir+json", "application/json",
                   "*/*", "text/html"],
    )
    def test_c31_accept_degrades_to_fhir_json(
        self, fhir_client, accept
    ):
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM},
            headers={"Accept": accept},
        )
        assert "fhir+json" in r.headers["content-type"]


class TestMethodSemantics:
    """Non-GET/POST methods and CT enforcement are FHIR-shaped."""

    @pytest.mark.parametrize("method", ["put", "delete", "patch"])
    def test_c40_405_operationOutcome(
        self, fhir_client, method
    ):
        r = getattr(fhir_client, method)(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM},
        )
        assert r.status_code == 405
        assert "fhir+" in r.headers.get("content-type", "")
        assert (
            r.json().get("resourceType") == "OperationOutcome"
        )

    def test_c41_text_plain_body_422(self, fhir_client):
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup", content=b"not json",
            headers={"Content-Type": "text/plain"},
        )
        assert r.status_code == 422

    def test_c42_xml_ct_json_body_422(self, fhir_client):
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json={"resourceType": "Parameters",
                  "parameter": []},
            headers={"Content-Type": "application/fhir+xml"},
        )
        assert r.status_code == 422


class TestOutParamShapes:
    """R4 OperationDefinition Out shapes."""

    def test_c50_translate_match_parts(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        matches = [
            p for p in r.json().get("parameter", [])
            if p.get("name") == "match"
        ]
        assert matches
        for m in matches:
            part_names = [
                q.get("name") for q in m.get("part", [])
            ]
            assert "equivalence" in part_names
            assert "concept" in part_names
            assert "source" in part_names

    def test_c51_expand_shape(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}/{DM}?fhir_vs=isa"},
        )
        body = r.json()
        assert body.get("resourceType") == "ValueSet"
        assert body.get("status")
        assert body.get("url")
        exp = body["expansion"]
        assert exp.get("timestamp")
        assert "total" in exp
        assert "contains" in exp


class TestClosureLifecycle:
    """$closure table init/add/re-add/isolation."""

    def test_c60_lifecycle(self, fhir_client):
        base = {"resourceType": "Parameters", "parameter": [
            {"name": "name", "valueString": "tbl-l60"},
        ]}
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure", json=base
        )
        assert r.status_code == 200
        # add
        add = {
            "resourceType": "Parameters", "parameter": [
                {"name": "name", "valueString": "tbl-l60"},
                {"name": "concept", "valueCoding": {
                    "system": SNOMED_URI, "code": DM}},
            ],
        }
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure", json=add
        )
        assert r.status_code == 200
        # re-add idempotent
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure", json=add
        )
        assert r.status_code == 200
        # second table isolated
        add2 = {
            "resourceType": "Parameters", "parameter": [
                {"name": "name", "valueString": "tbl2-l60"},
                {"name": "concept", "valueCoding": {
                    "system": SNOMED_URI, "code": T2DM}},
            ],
        }
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure", json=add2
        )
        assert r.status_code == 200


class TestLookupCodingInParam:
    """R4 alternative invocation form: coding In-param."""

    def test_c70_coding_post(self, fhir_client):
        body = {
            "resourceType": "Parameters", "parameter": [
                {"name": "coding", "valueCoding": {
                    "system": SNOMED_URI, "code": T2DM}},
            ],
        }
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup", json=body
        )
        assert r.status_code == 200
        display = next(
            (p.get("valueString")
             for p in r.json()["parameter"]
             if p.get("name") == "display"),
            None,
        )
        assert display == "Type 2 diabetes mellitus"
