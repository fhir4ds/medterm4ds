"""TS-59 POST Parameters typed-binding + duplicate-param matrix (2026-10-18).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261018b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the POST Parameters BODY-TYPING contract across ops —
value[x] coercion, valueless/null/unknown-typed entries, nested
resource params, count bounds, and duplicate 0..1 params. QC-245
covered boolean drops in the scalar parser; the typed-binding matrix
was never swept.

DP1 (LOW-MED, NEW) — duplicate 0..1 parameters silently resolved
    LAST-WINS. A Parameters body with two `system` entries (SNOMED
    then ICD-10-CM) misses on a SNOMED-only code — the SECOND entry
    won; reversed order hits. R4 parameters.html invariant par-1: a
    parameter SHALL NOT have more than one value unless the
    OperationDefinition declares it repeating (0..1 here). The
    client's contradiction is silently resolved with NO 400 and no
    signal — the request means whichever value came last. Same
    silent-resolution family as T2 (scalar-over-coding precedence).
    Fix shape: 400 on duplicate non-repeating param names (the body
    parser knows cardinality), or document last-wins. Flip pins
    dp10/dp11.

Controls (fresh) — the typed-binding matrix is otherwise CONFORMANT:
    count binds as valueInteger AND valueString ('2' coerced);
    system binds as valueUri AND valueString; valueless parameter →
    400 missing; explicit null value → 400; unknown value type
    (valueCanonical) → 400 (not silently accepted); count bounds
    enforced at the BODY too (-5/0 → 400 with the documented
    range diagnostic); the nested `resource` form of the valueSet
    parameter (R4's own shape) expands correctly.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
T2DM = "44054006"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"


class TestDP1DuplicateParams:
    """DP1 — duplicate 0..1 params silently last-win."""

    def test_dp10_duplicate_system_last_wins_miss(self, fhir_client):
        """[SNOMED, ICD-10-CM] duplicates: the SNOMED-only code
        misses — the second entry silently won. Flip when duplicates
        400 (or a documented rule lands)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "system", "valueUri": ICD10CM_URI},
                {"name": "code", "valueCode": T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        assert r.status_code == 200
        issue = r.json()["issue"][0]
        assert issue["code"] == "not-found"

    def test_dp11_reversed_order_hits(self, fhir_client):
        """The SAME contradictory body with entries reversed hits —
        proof the resolution is order-based (last-wins), not
        any-wins or error."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": ICD10CM_URI},
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        assert r.status_code == 200
        assert r.json()["resourceType"] == "Parameters"


class TestTypedBindingControls:
    """Controls — the typed-binding matrix, fresh."""

    @pytest.mark.parametrize(
        "value_key,value",
        [
            ("valueInteger", 2),
            ("valueString", "2"),
        ],
    )
    def test_c10_count_binds_both_types(
        self, fhir_client, value_key, value
    ):
        """count as valueInteger or valueString both page to 2
        entries (string coercion held)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": DM_ISA_URL},
                {"name": "count", value_key: value},
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code == 200
        assert len(r.json()["expansion"]["contains"]) == 2

    @pytest.mark.parametrize("value_key", ["valueUri", "valueString"])
    def test_c11_system_binds_both_types(
        self, fhir_client, value_key
    ):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", value_key: SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        assert r.status_code == 200
        assert r.json()["resourceType"] == "Parameters"

    def test_c12_valueless_param_400(self, fhir_client):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system"},
                {"name": "code", "valueCode": T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        assert r.status_code == 400

    def test_c13_null_value_400(self, fhir_client):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": None},
                {"name": "code", "valueCode": T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        assert r.status_code == 400

    def test_c14_unknown_value_type_400(self, fhir_client):
        """valueCanonical for system — an unrecognized value[x] type
        fails loudly rather than binding as None."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueCanonical": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        assert r.status_code == 400

    @pytest.mark.parametrize("count", [-5, 0])
    def test_c15_count_bounds_enforced_in_body(
        self, fhir_client, count
    ):
        """The GET-side count range (1..1000) enforces at the body
        too, with the documented diagnostic."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": DM_ISA_URL},
                {"name": "count", "valueInteger": count},
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code == 400
        assert "count" in r.json()["issue"][0]["diagnostics"]

    def test_c16_nested_resource_param(self, fhir_client):
        """R4's own valueSet shape — parameter.resource carrying the
        ValueSet — expands correctly (VS-03 body mode held)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": T2DM}],
            }]},
        }
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": vs},
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code == 200
        codes = [
            e["code"]
            for e in r.json()["expansion"]["contains"]
        ]
        assert codes == [T2DM]
