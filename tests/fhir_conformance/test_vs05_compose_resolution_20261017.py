"""TS-54 $expand compose.include system/concept resolution (2026-10-17).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261016f).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the compose.include block matrix — block-system recognition,
concept resolution against the block system, concept.system mismatch,
versioned/oid block systems, multi-block union, include/exclude.
VS-01 (iteration 6) covered compose FILTER semantics; the
concept-resolution contract itself was never swept.

CV1 (MEDIUM, NEW) — compose concepts are NEVER resolved against the
    block system; the expansion echoes them verbatim under the block's
    system label. Fresh probes: a SNOMED block containing
    concept {code: 'E11'} (E11 exists ONLY in ICD-10-CM) expands to
    {system: snomed, code: E11, display: 'E11'}; an unknown code
    {code: '99999999'} expands verbatim too (display = the code).
    The resulting ValueSet expansion ASSERTS memberships that do not
    exist in the labeled system — downstream consumers (validators,
    $validate-code against the expansion, cohort definitions) receive
    phantom concepts labeled with an authoritative system URI. R4
    §4.9.2: the code system SHALL be used to determine concept
    validity; §4.5 (compose): concept.code selects a concept FROM the
    code system.
    Fix shape: resolve each concept.code via the block system (unknown
    → 400 OperationOutcome naming code+system, mirroring the url-mode
    unknown-root QC-247 contract; or omit+annotate). Flip pins
    cv10-cv12.

CV2 (LOW, NEW) — concept.system mismatch silently ignored.
    Block system=SNOMED with concept {code: E11, system: icd-10-cm}
    behaves identically to the bare form (both expand under the SNOMED
    label). Either the mismatch must 400 (R4 concept.system disagrees
    with include.system) or the concept's own system must win; silence
    produces cross-system contamination inside one block. Flip pin
    cv13/cv14.

CV3 (LOW) — versioned block system accepted-and-ignored.
    include.system.version (SNOMED version URI) expands identically to
    unversioned — H1/U4 family inside the compose block (op-param
    versions are rejected loudly; block-level versions are silent).
    Flip pin cv20.

Controls (fresh): unknown BLOCK system → 400 naming known systems
    (boundary held); empty include block → 200 empty expansion; urn:oid
    block system resolves (2.16.840.1.113883.6.96 → SNOMED, AL2's
    covered pair); two blocks union across systems; include+exclude
    removes exactly the excluded code.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_OID = "urn:oid:2.16.840.1.113883.6.96"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
T2DM = "44054006"
DM = "73211009"


def _compose_expansion(client, vs: dict) -> tuple[int, list[dict] | str]:
    r = client.post("/fhir/ValueSet/$expand", json=vs)
    if r.status_code != 200:
        return r.status_code, r.json()["issue"][0]["diagnostics"]
    return 200, r.json()["expansion"].get("contains", [])


def _snomed_block(*concepts: dict) -> dict:
    return {
        "resourceType": "ValueSet",
        "compose": {"include": [{"system": SNOMED_URI, "concept": list(concepts)}]},
    }


class TestCV1ConceptsNotResolved:
    """CV1 — verbatim echo, no resolution against the block system."""

    def test_cv10_wrong_system_code_expands(self, fhir_client):
        """E11 (ICD-10-CM-only) in a SNOMED block expands verbatim
        under the SNOMED label. Flip when resolution lands (omit or
        400 naming code+system)."""
        vs = _snomed_block({"code": "E11"})
        status, contains = _compose_expansion(fhir_client, vs)
        assert status == 200
        assert contains == [
            {"system": SNOMED_URI, "code": "E11", "display": "E11"}
        ]

    def test_cv11_unknown_code_expands(self, fhir_client):
        """A never-existed code expands verbatim (display = the code) —
        phantom membership labeled with an authoritative system.
        Flip with CV1's resolution."""
        vs = _snomed_block({"code": "99999999"})
        status, contains = _compose_expansion(fhir_client, vs)
        assert status == 200
        assert contains == [
            {
                "system": SNOMED_URI, "code": "99999999",
                "display": "99999999",
            }
        ]

    def test_cv12_mixed_real_and_phantom(self, fhir_client):
        """Real concept (resolved display) and phantom (echoed code)
        in one expansion — the display field betrays which entries
        were resolved. Flip when both resolve or both reject."""
        vs = _snomed_block({"code": "99999999"}, {"code": T2DM})
        status, contains = _compose_expansion(fhir_client, vs)
        assert status == 200
        displays = {e["code"]: e["display"] for e in contains}
        assert displays[T2DM] == "Type 2 diabetes mellitus"
        assert displays["99999999"] == "99999999"


class TestCV2ConceptSystemMismatch:
    """CV2 — concept.system disagreement silently ignored."""

    def test_cv13_mismatch_expands_under_block_system(self, fhir_client):
        """concept.system=icd-10-cm inside a SNOMED block: the entry
        still carries the SNOMED label. Flip when the mismatch 400s or
        the concept system wins."""
        vs = _snomed_block(
            {"code": "E11", "system": ICD10CM_URI}
        )
        status, contains = _compose_expansion(fhir_client, vs)
        assert status == 200
        assert contains[0]["system"] == SNOMED_URI

    def test_cv14_matching_concept_system_unchanged(self, fhir_client):
        """concept.system agreeing with the block system is the
        no-op control for cv13."""
        vs = _snomed_block({"code": T2DM, "system": SNOMED_URI})
        status, contains = _compose_expansion(fhir_client, vs)
        assert status == 200
        assert contains == [
            {
                "system": SNOMED_URI, "code": T2DM,
                "display": "Type 2 diabetes mellitus",
            }
        ]


class TestCV3VersionedBlockSystem:
    """CV3 — block-level version accepted-and-ignored (H1 family)."""

    def test_cv20_versioned_system_same_expansion(self, fhir_client):
        """include.system.version present vs absent: identical
        expansions. Flip when versioned blocks are rejected loudly
        (single-version server) or honored."""
        versioned = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "version": (
                    "http://snomed.info/sct/723100002/version/20260301"
                ),
                "concept": [{"code": T2DM}],
            }]},
        }
        unversioned = _snomed_block({"code": T2DM})
        sv, cv = _compose_expansion(fhir_client, versioned)
        su, cu = _compose_expansion(fhir_client, unversioned)
        assert sv == su == 200
        assert cv == cu


class TestTS54Controls:
    """Controls — the block-boundary contract, fresh."""

    def test_c10_unknown_block_system_400(self, fhir_client):
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": "http://nowhere.org",
                "concept": [{"code": "X"}],
            }]},
        }
        status, diag = _compose_expansion(fhir_client, vs)
        assert status == 400
        assert "nowhere.org" in diag

    def test_c11_empty_include_block(self, fhir_client):
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{}]},
        }
        status, contains = _compose_expansion(fhir_client, vs)
        assert status == 200
        assert contains == []

    def test_c12_oid_block_system_resolves(self, fhir_client):
        """urn:oid form of SNOMED (AL2's covered pair) resolves codes
        under the same engine."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_OID, "concept": [{"code": T2DM}],
            }]},
        }
        status, contains = _compose_expansion(fhir_client, vs)
        assert status == 200
        assert any(e["code"] == T2DM for e in contains)

    def test_c13_two_blocks_union(self, fhir_client):
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": DM}]},
                {
                    "system": ICD10CM_URI,
                    "concept": [{"code": "E11"}],
                },
            ]},
        }
        status, contains = _compose_expansion(fhir_client, vs)
        assert status == 200
        codes = {e["code"] for e in contains}
        assert codes == {DM, "E11"}
        systems = {e["system"] for e in contains}
        assert systems == {SNOMED_URI, ICD10CM_URI}

    def test_c14_exclude_removes_exactly(self, fhir_client):
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": DM}, {"code": T2DM}],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": T2DM}],
                }],
            },
        }
        status, contains = _compose_expansion(fhir_client, vs)
        assert status == 200
        assert [e["code"] for e in contains] == [DM]
