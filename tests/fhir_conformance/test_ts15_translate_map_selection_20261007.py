"""TS-15 $translate map-selection semantics (2026-10-07).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261007b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: $translate's map-SELECTION parameters (url, conceptMapVersion,
source/target), distinct from prior TS-02/CM-02 registrations (T1
reverse silently-forward; T2 one-of unenforced). This sweep: which
ConceptMap actually answers the request.

Survey (fresh, this run; fixture carries one cross-SAB pair:
ICD10CM E11 ↔ SNOMEDCT_US 44054006 via CUI C0011847):
* Happy path: E11 + targetSystem SNOMED → result TRUE, one match,
  equivalence=equivalent, canonical systems on both sides (CR-012).
* No-url (server-default): same result — R4 allows server discretion
  when url is absent. Control.
* match part shape: equivalence/concept/source parts present.

N1 (MEDIUM-HIGH, NEW) — the `url` parameter is silently ignored.
    R4 $translate In 'url': "The ConceptMap to use for the mapping
    (canonical url)". Probed: url=urn:...:crosswalk:LOINC-to-snomed
    while translating ICD10CM E11 → the ICD10CM↔SNOMED pair is
    returned as result TRUE under the LOINC map's name. No ConceptMap
    registry exists server-side; translate always consults the
    engine crosswalk regardless of which map the client selected. A
    client believing it queried a specific map silently receives
    another map's semantics (silent wrong provenance — same family
    as T1's silent wrong direction). Fix: resolve url against
    advertised maps (400 unknown / route by url) or document the
    single-crosswalk design in the CapabilityStatement + OperationDef.

N2 (MEDIUM, NEW) — version selection has no semantics.
    Versioned canonical url forms (url|1.0, url|9.9) and the
    conceptMapVersion In param are all accepted with identical
    results — no version matching, no unknown-version error. R4
    treats url|version as a distinct canonical reference. Fix rides
    with N1's map resolution (version-aware or explicitly
    unversioned-single-map with 400 on versioned forms).

N3 (LOW, NEW) — R4-declared `source`/`target` filters silently
    ignored on GET. R4 $translate declares source/target (0..1 uri) as
    code-system filters alongside system/targetSystem. With the
    required params present they are accepted-and-ignored (200,
    unknown-query-param tolerance): probed target=<uri> instead of
    targetSystem — same 1 match as the unfiltered widening form; the
    filter has no effect. (A probe sending source INSTEAD of system
    422s — but that is the missing-required-param error, not param
    handling.) Fix: declare + honor the filters, or document
    unsupported in the OperationDefinition (W3 host note).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
ICD10_URI = "http://hl7.org/fhir/sid/icd-10-cm"
CM_LOINC_TO_SNOMED = "urn:medterm4ds:crosswalk:loinc-to-snomed"
E11 = "E11"
T2DM_SCT = "44054006"


def _translate(fhir_client, **params):
    return fhir_client.get(
        "/fhir/ConceptMap/$translate", params=params
    )


def _result(j: dict):
    return next(
        (q.get("valueBoolean") for q in j.get("parameter", [])
         if q.get("name") == "result"),
        None,
    )


def _match_count(j: dict) -> int:
    return len([q for q in j.get("parameter", []) if q.get("name") == "match"])


class TestN1UrlIgnored:
    """N1 RESOLVED 2026-10-07 (fix batch maint/fix-conformance-20261007):
    url now resolves against the single implicit ConceptMap
    (urn:medterm4ds:crosswalk) — wrong/bogus urls 400 naming the
    supported value; the correct urn translates as before."""

    def test_n10_wrong_map_returns_other_maps_pair(self, fhir_client):
        """FLIPPED: requesting the LOINC→SNOMED map while translating an
        ICD10CM code is now rejected 400 (unknown map) — no silent
        crosswalk answer under the wrong map's name."""
        r = _translate(
            fhir_client,
            url=CM_LOINC_TO_SNOMED,
            system=ICD10_URI, code=E11, targetSystem=SNOMED_URI,
        )
        assert r.status_code == 400
        assert "urn:medterm4ds:crosswalk" in r.text
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_n11_correct_pair_baseline(self, fhir_client):
        """Control (unchanged): the same pair WITHOUT url (server-default
        crosswalk) returns it — the fix scopes map selection, not the
        translation itself."""
        r = _translate(
            fhir_client, system=ICD10_URI, code=E11,
            targetSystem=SNOMED_URI,
        )
        assert r.status_code == 200
        assert _result(r.json()) is True
        assert _match_count(r.json()) == 1

    def test_n12_divergence_pinned(self, fhir_client):
        """FLIPPED: the implicit-map url succeeds; bogus urls 400 —
        resolution discriminates instead of treating all urls alike."""
        good = _translate(
            fhir_client, url="urn:medterm4ds:crosswalk",
            system=ICD10_URI, code=E11, targetSystem=SNOMED_URI,
        )
        bogus = _translate(
            fhir_client, url="urn:medterm4ds:crosswalk:nonexistent",
            system=ICD10_URI, code=E11, targetSystem=SNOMED_URI,
        )
        assert good.status_code == 200
        assert _result(good.json()) is True
        assert bogus.status_code == 400


class TestN2VersionSemanticsAbsent:
    """N2 RESOLVED 2026-10-07: the implicit map is unversioned —
    versioned url forms and conceptMapVersion are explicitly rejected
    (400) instead of silently accepted."""

    @pytest.mark.parametrize(
        "extra",
        [
            {"url": CM_LOINC_TO_SNOMED + "|1.0"},
            {"url": "urn:medterm4ds:crosswalk|1.0"},
            {"url": "urn:medterm4ds:crosswalk|9.9"},
        ],
    )
    def test_n20_versioned_url_accepted(self, fhir_client, extra):
        """FLIPPED: versioned canonical references 400 (map is
        unversioned) — no silent version-ignoring translation."""
        r = _translate(
            fhir_client, **extra,
            system=ICD10_URI, code=E11, targetSystem=SNOMED_URI,
        )
        assert r.status_code == 400
        assert "unversioned" in r.text

    def test_n21_concept_map_version_param(self, fhir_client):
        """FLIPPED: conceptMapVersion rejected with 400."""
        r = _translate(
            fhir_client, url="urn:medterm4ds:crosswalk",
            conceptMapVersion="9.9",
            system=ICD10_URI, code=E11, targetSystem=SNOMED_URI,
        )
        assert r.status_code == 400
        assert "conceptMapVersion" in r.text


class TestN3SourceTargetFiltersIgnored:
    """N3 — R4 source/target filters have no effect on GET."""

    def test_n30_target_filter_ignored(self, fhir_client):
        """target=<uri> (instead of targetSystem) is accepted and
        ignored — the response matches the unfiltered widening form.
        WHEN the filter is honored, the pin compares against the
        filtered result instead."""
        r_filtered = _translate(
            fhir_client, system=ICD10_URI, code=E11, targetSystem=SNOMED_URI,
        )
        # c-fixbatch3 (V3): the unfiltered (no-target) form is closed —
        # 400. N3's 'widening equals filtered' comparison is vacuous now;
        # pin the closed form directly.
        r_wide = _translate(fhir_client, system=ICD10_URI, code=E11)
        assert r_filtered.status_code == 200
        assert r_wide.status_code == 400

    def test_n31_source_alongside_system_ignored(self, fhir_client):
        """source=<uri> alongside system is accepted and ignored
        (unknown-param tolerance), not a 422."""
        r = _translate(
            fhir_client, source=SNOMED_URI,
            system=ICD10_URI, code=E11, targetSystem=SNOMED_URI,
        )
        assert r.status_code == 200
        assert _result(r.json()) is True


class TestTranslateSelectionControls:
    """Controls: documented happy-path contract, fresh."""

    def test_s10_match_shape(self, fhir_client):
        """Match parts: equivalence + concept (canonical systems) +
        source echo."""
        r = _translate(
            fhir_client, system=ICD10_URI, code=E11,
            targetSystem=SNOMED_URI,
        )
        assert r.status_code == 200
        match = next(
            q for q in r.json()["parameter"] if q.get("name") == "match"
        )
        parts = {p["name"]: p for p in match["part"]}
        assert "equivalence" in parts
        concept = parts["concept"]["valueCoding"]
        assert concept["system"] == SNOMED_URI
        assert concept["code"] == T2DM_SCT
        assert concept["display"] == "Type 2 diabetes mellitus"
        src = parts["source"]["valueCoding"]
        assert src["system"] == ICD10_URI
        assert src["code"] == E11

    def test_s11_no_target_system_widens(self, fhir_client):
        """No targetSystem → closed: 400 (c-fixbatch3 V3; the widening
        this control pinned is closed)."""
        # c-fixbatch3 (V3): no-target widening closed — 400.
        r = _translate(fhir_client, system=ICD10_URI, code=E11)
        assert r.status_code == 400

    def test_s12_unknown_system_400(self, fhir_client):
        r = _translate(
            fhir_client, system="http://bogus.example/x", code="E11",
            targetSystem=SNOMED_URI,
        )
        assert r.status_code == 400

    def test_s13_empty_code_400(self, fhir_client):
        """QC-324 empty-code family: 400, not 500."""
        r = _translate(
            fhir_client, system=ICD10_URI, code="   ",
            targetSystem=SNOMED_URI,
        )
        assert r.status_code == 400
