"""TS-61 cross-op display + property convention (2026-10-18).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261018d).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the DISPLAY convention across operations — the same code's
display in $lookup (display + name), $validate-code (display),
$expand (contains.display), $translate (match.concept.display), and
the patient-friendly overlay's placement. Individual displays are
probed everywhere; the CROSS-OP convention was never matrixed.

ZERO findings — controls-only iteration (6th clean surface after
TS-28/33/36/42/43). The convention holds uniformly:

DL-C1 — PT display is the single wire convention: $lookup,
    $validate-code, $expand.contains, and $translate.match.concept
    all emit 'Type 2 diabetes mellitus' for 44054006 (identical
    string, no per-op display drift).
DL-C2 — name ≠ display on $lookup: name carries the CODE SYSTEM's
    name ('SNOMED Clinical Terms (US)'), display the concept's —
    R4 §4.4.18.1 shape held.
DL-C3 — the patient-friendly overlay rides as a PROPERTY
    ('patient-friendly' = 'Diabetes Type 2', match-type 'same_cui')
    alongside the UMLS internals (cui/tty/aui) and canonical pair
    (canonical-code E11 / canonical-system icd-10-cm) — exactly the
    7-code set LK1 registered; the display fields stay PT-clean.
DL-C4 — property parts are code+value pairs (structure held), and
    the canonical pair agrees with $translate's forward match for
    the same code (cross-op consistency of the crosswalk).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
T2DM = "44054006"
DM = "73211009"
PT_DISPLAY = "Type 2 diabetes mellitus"
PF_DISPLAY = "Diabetes Type 2"


def _param(params_json: dict, name: str):
    for p in params_json.get("parameter", []):
        if p.get("name") == name:
            return next(
                (v for k, v in p.items() if k.startswith("value")), None
            )
    return None


def _properties(params_json: dict) -> dict:
    out = {}
    for p in params_json.get("parameter", []):
        if p.get("name") == "property":
            code = next(
                (q.get("valueCode") for q in p.get("part", [])
                 if q.get("name") == "code"), None
            )
            val = next(
                (next((v for k, v in q.items() if k.startswith("value")), None)
                 for q in p.get("part", []) if q.get("name") == "value"),
                None,
            )
            if code:
                out[code] = val
    return out


class TestCrossOpDisplayUniformity:
    """DL-C1 — one display convention across all four ops."""

    def test_c10_lookup_display_pt(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM},
        )
        assert _param(r.json(), "display") == PT_DISPLAY

    def test_c11_validate_display_pt(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": T2DM},
        )
        assert _param(r.json(), "display") == PT_DISPLAY

    def test_c12_expand_contains_display_pt(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}/{DM}?fhir_vs=isa"},
        )
        displays = {
            e["code"]: e["display"]
            for e in r.json()["expansion"]["contains"]
        }
        assert displays[T2DM] == PT_DISPLAY
        assert displays[DM] == "Diabetes mellitus"

    def test_c13_translate_concept_display_pt(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI,
            },
        )
        concept = None
        for p in r.json()["parameter"]:
            if p.get("name") == "match":
                for q in p.get("part", []):
                    if q.get("name") == "concept":
                        concept = q.get("valueCoding")
        assert concept is not None
        assert concept["display"] == PT_DISPLAY


class TestLookupShapeControls:
    """DL-C2 — name vs display; DL-C3 — property set; DL-C4 shape."""

    def test_c20_name_is_code_system_name(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM},
        )
        assert _param(r.json(), "name") == "SNOMED Clinical Terms (US)"
        assert _param(r.json(), "name") != PT_DISPLAY

    def test_c21_property_set_exact(self, fhir_client):
        """The 7-code property set (LK1's registry): 3 UMLS internals
        + patient-friendly/match-type + canonical pair."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM},
        )
        props = _properties(r.json())
        assert set(props) == {
            "cui", "tty", "aui",
            "patient-friendly", "match-type",
            "canonical-code", "canonical-system",
        }

    def test_c22_patient_friendly_overlay(self, fhir_client):
        """pf property = 'Diabetes Type 2' via same_cui match — the
        overlay rides as property, display stays PT."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM},
        )
        props = _properties(r.json())
        assert props["patient-friendly"] == PF_DISPLAY
        assert props["match-type"] == "same_cui"
        assert props["patient-friendly"] != PT_DISPLAY

    def test_c23_canonical_pair_agrees_with_translate(self, fhir_client):
        """DL-C4: lookup's canonical-code/system (E11/icd-10-cm)
        agree with $translate's forward match for the same code —
        cross-op consistency of the crosswalk."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM},
        )
        props = _properties(r.json())
        t = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetSystem": ICD10CM_URI,
            },
        )
        concept = None
        for p in t.json()["parameter"]:
            if p.get("name") == "match":
                for q in p.get("part", []):
                    if q.get("name") == "concept":
                        concept = q.get("valueCoding")
        assert props["canonical-code"] == concept["code"] == "E11"
        assert props["canonical-system"] == ICD10CM_URI
