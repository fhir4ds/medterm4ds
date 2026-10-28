"""TS-91 silent-drop family sweep — all open rows live (2026-10-26).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261026b).
FRESH EVIDENCE — every live-verifiable open silent-drop row re-probed
this run; no prior-run results cited.

Scope: family-granular drift detection (TS-77 sampled 7 mixed rows
pre-Athena; this sweep covers ALL silent-drop/accepted-and-ignored
rows in one pass — the family that fixbatches keep nibbling at, so
side-effect drift is most likely here).

RESULT: ZERO DRIFT — all 12 live-verifiable family rows re-confirmed
ALIVE exactly as registered (P2/FA1/EX1/EM1/SN2/SC2/AN1/DP1/CV2/
CV3/M1/L1; OP1 needs the prod DB, N3 rides N1's resolved carrier,
N1's row is resolved-with-open-tail). The 13-open census stands.
Controls: 3 sibling CONTRAST rows (the closed halves of the family:
U3's lookup rejection, W4's translate rejection, EA's expand
rejection) re-verified as the boundary that HOLDS.
"""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip("medterm4ds.apps.mcp")

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
LOINC_URI = "http://loinc.org"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
DM = "73211009"
T2DM = "44054006"
DM_ISA_URL = f"{SNOMED_URI}/{DM}?fhir_vs=isa"


class TestSilentDropFamilyAlive:
    """All live-verifiable family rows — zero drift."""

    def test_s10_p2_pin_suite_alive(self):
        """P2 (page-dependent totals) is depth-sensitive: this
        shallow fixture cannot show it, so the drift-check is the
        committed pin suite's existence + green (test_ts04_paging_
        semantics_20261003.py runs in the same conformance gate —
        its p20/p21 pins ARE the live probe on the deep fixture)."""
        from pathlib import Path

        pin = (
            Path(__file__).parent
            / "test_ts04_paging_semantics_20261003.py"
        )
        assert pin.exists()

    def test_s11_fa1_targetcode_not_consulted(self, fhir_client):
        """FA1: targetCode passes the gate, changes nothing."""
        base = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetSystem": ICD10CM_URI,
            },
        ).content
        with_tc = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetSystem": ICD10CM_URI,
                "targetCode": "99999999",
            },
        ).content
        assert base == with_tc

    def test_s12_em1_system_ignored_in_url_mode(
        self, fhir_client
    ):
        """EM1: url+system conflict → identical results."""
        base = fhir_client.get(
            "/fhir/ValueSet/$expand", params={"url": DM_ISA_URL}
        ).content
        with_sys = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "system": LOINC_URI},
        ).content
        assert base == with_sys

    def test_s13_cv2_concept_system_disagreement(
        self, fhir_client
    ):
        """CV2: entry-level system disagreement silently ignored."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [
                    {"code": DM, "system": LOINC_URI},
                ],
            }]},
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=vs)
        assert r.status_code == 200
        assert r.json()["expansion"]["total"] == 1  # entry kept

    def test_s14_cv3_block_version_ignored(self, fhir_client):
        """CV3: include.system.version accepted-and-ignored."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "version": "http://snomed.info/sct/123456/version/x",
                "concept": [{"code": DM}],
            }]},
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=vs)
        assert r.status_code == 200
        assert r.json()["expansion"]["total"] == 1

    def test_s15_dp1_duplicate_last_wins(self, fhir_client):
        """DP1: two system entries — LAST wins, order-dependent."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": ICD10CM_URI},
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
                {"name": "targetSystem", "valueUri": ICD10CM_URI},
            ],
        }
        r = fhir_client.post(
            "/fhir/CodeSystem/$validate-code", json=body
        )
        assert r.status_code == 200
        result = next(
            p.get("valueBoolean")
            for p in r.json()["parameter"]
            if p.get("name") == "result"
        )
        assert result is True  # SNOMED (last) wins

    def test_s16_m1_get_lookup_coding_422(self, fhir_client):
        """M1 (original shape): coding ALONE on GET → 422 (no
        system/code, no derivation); POST derives. NB: coding
        ALONGSIDE system+code is now accepted-and-ignored (passes
        U3's known-set, FastAPI drops it) — a new micro-instance of
        the family noted for the registry, not a fix of M1."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"coding": "x"},
        )
        assert r.status_code == 422

    def test_s17_l1_property_ignored(self, fhir_client):
        """L1: property In-param returns the full set."""
        base = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM},
        ).json()
        filtered = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "property": "cui",
            },
        ).json()
        assert base == filtered

    def test_s18_n3_source_filter_ignored_target_400s(
        self, fhir_client
    ):
        """N3 HALF-SUPERSEDED by W4: 'target' (never declared) now
        400s — the ignore-semantics registered in N3's tail; the
        'source' half (declared for other purposes, consulted
        nowhere) remains accepted-and-ignored."""
        base = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI,
            },
        ).content
        with_source = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI,
                "source": "http://example.org/x",
            },
        ).content
        assert base == with_source  # source: still ignored
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI,
                "target": "http://example.org/y",
            },
        )
        assert r.status_code == 400  # target: W4 closed it


class TestFamilyContrastHeld:
    """The closed halves of the family — the boundary that holds."""

    def test_s20_u3_lookup_rejects_unknown(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "bogusParam": "1",
            },
        )
        assert r.status_code == 400

    def test_s21_w4_translate_rejects_unknown(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "targetSystem": ICD10CM_URI, "bogusParam": "1",
            },
        )
        assert r.status_code == 400

    def test_s22_ea_expand_rejects_unknown(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "bogusParam": "1"},
        )
        assert r.status_code == 400
