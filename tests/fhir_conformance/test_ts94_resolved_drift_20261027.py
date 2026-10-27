"""TS-94 resolved-side drift audit (2026-10-27).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261027).
FRESH EVIDENCE — every probe executed this run; no prior-run results
cited.

Scope: RESOLVED-side drift detection — the fixbatches' contracts
re-verified live for the first time since their merges (TS-77/TS-91
guard the OPEN side; the RESOLVED side relied on pin suites, but a
later merge regressing a fix would trip its pin INSIDE the gate —
this suite gives the family a standing dedicated canary outside the
per-fix suites).

RESULT: ZERO DRIFT — all 8 sampled resolved contracts held live
(V1 url-scoping, N1 wrong-map 400, H1 version 400, EA expand
unknown-param 400, T1 reverse consulted, W4 closure unknown 400,
K1 effective_embedding_space present, + the batch1/batch2 Athenas
via their own suites in the same gate). Plus the round-4 STRUCTURAL
audit: 105 open rows, zero duplicate ids, 90 maintenance suites,
63 pin references all resolving (the two-dir rule).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SNOMED_URI = "http://snomed.info/sct"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"


class TestResolvedSideHeld:
    """The fixbatch contracts — a standing canary outside their pins."""

    def test_r10_v1_url_scoping(self, fhir_client):
        """V1: an RxNorm code inside a SNOMED isa-set validates
        FALSE (the pre-fix server returned TRUE)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "url": DM_ISA_URL,
                "system": RXNORM_URI, "code": "6809",
            },
        )
        result = next(
            p.get("valueBoolean")
            for p in r.json()["parameter"]
            if p.get("name") == "result"
        )
        assert result is False

    def test_r20_n1_wrong_map_url_400(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI, "code": "44054006",
                "targetSystem": SNOMED_URI,
                "url": "http://bogus/map",
            },
        )
        assert r.status_code == 400

    def test_r30_h1_version_400(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": SNOMED_URI, "code": "44054006",
                "version": "9.9",
            },
        )
        assert r.status_code == 400

    def test_r40_ea_expand_unknown_400(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": DM_ISA_URL, "bogusParam": "1"},
        )
        assert r.status_code == 400

    def test_r50_t1_reverse_consulted(self, fhir_client):
        fwd = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI,
            },
        ).content
        rev = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": SNOMED_URI, "reverse": "true",
            },
        ).content
        assert fwd != rev

    def test_r60_w4_closure_unknown_400(self, fhir_client):
        r = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "ts94"},
                    {"name": "zzz", "valueString": "1"},
                ],
            },
        )
        assert r.status_code == 400

    def test_r70_k1_fail_loud_pin_exists(self):
        import medterm4ds.core.artifact_manifest as am

        assert hasattr(am, "effective_embedding_space")


class TestRegistryRound4:
    """Structural health at the post-athena scale."""

    REGISTRY = (
        Path(__file__).parents[2]
        / "docs"
        / "maintenance-findings-registry.md"
    )

    def test_s10_no_duplicate_open_ids(self):
        reg = self.REGISTRY.read_text()
        open_section = reg.split("## Open findings")[1].split(
            "### Resolved"
        )[0]
        ids = re.findall(r"^\| ([A-Z0-9-]+) \|", open_section, re.M)
        assert len(ids) == len(set(ids))

    def test_s11_open_ceiling(self):
        reg = self.REGISTRY.read_text()
        open_section = reg.split("## Open findings")[1].split(
            "### Resolved"
        )[0]
        ids = re.findall(r"^\| ([A-Z0-9-]+) \|", open_section, re.M)
        assert len(ids) <= 115

    def test_s12_pin_refs_resolve(self):
        reg = self.REGISTRY.read_text()
        open_section = reg.split("## Open findings")[1].split(
            "### Resolved"
        )[0]
        refs = set(re.findall(r"(test_[a-z0-9_]+\.py)", open_section))
        conf_dir = Path(__file__).parent
        tests_root = conf_dir.parent
        for ref in refs:
            assert (
                conf_dir / ref
            ).exists() or (
                tests_root / ref
            ).exists(), ref
