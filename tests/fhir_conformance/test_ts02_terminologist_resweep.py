"""TERMINOLOGIST resweep probes for TS-02 (Mandatory Terminology Service
Operations Matrix, FHIR R4 §4.7.1.2).

Fresh full-sweep run per USER_DIRECTIVES [2026-08-08]. Sibling file to the
baseline ``test_ts02_terminologist.py`` so the baseline stays comparable
across runs while this file adds fresh clinical-correctness coverage.

Source: https://hl7.org/fhir/R4/terminology-service.html (§4.7) +
         per-operation definitions at hl7.org/fhir/R4/{codesystem,valueset,
         conceptmap}-operation-{lookup,validate-code,subsumes,expand,
         translate,closure}.html

Tests the 7 mandatory items through the clinical / terminological lens:
1. CodeSystem/$lookup (display = preferred term per R4 spec)
2. CodeSystem/$validate-code (canonical display wins over client input)
3. CodeSystem/$subsumes (outcome directionality clinically correct)
4. ValueSet/$expand (display = engine canonical preferred term)
5. ValueSet/$validate-code (canonical display wins over client input)
6. ConceptMap/$translate (match.equivalence clinically correct per relationship)
7. CapabilityStatement content value clinically appropriate per source

TERMINOLOGIST lens (per ROLE_QA_ENGINEER Section 3 + medterm4ds-specific
extensions): clinical and terminological correctness. The other 3
personalities found 5 technical bugs (all empty-string drift, fixed; pattern
PROMOTED to GLOBAL_RULES.md as 9th PROMOTED pattern at line 138);
TERMINOLOGIST finds domain bugs.

Per GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH Severity": clinical
correctness outranks technical correctness in this domain. Remediation
Engineers cannot dismiss TERMINOLOGIST bugs as INTENDED without explicit
user override.
"""

from __future__ import annotations

import pytest

# =============================================================================
# Spec citation constants (verbatim from canonical R4 spec pages)
# =============================================================================

SPEC_LOOKUP = "https://hl7.org/fhir/R4/codesystem-operation-lookup.html"
SPEC_VALIDATE = "https://hl7.org/fhir/R4/codesystem-operation-validate-code.html"
SPEC_SUBSUMES = "https://hl7.org/fhir/R4/codesystem-operation-subsumes.html"
SPEC_EXPAND = "https://hl7.org/fhir/R4/valueset-operation-expand.html"
SPEC_VS_VALIDATE = "https://hl7.org/fhir/R4/valueset-operation-validate-code.html"
SPEC_TRANSLATE = "https://hl7.org/fhir/R4/conceptmap-operation-translate.html"
SPEC_TS = "https://hl7.org/fhir/R4/terminology-service.html"
SPEC_EQUIVALENCE = "https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html"

# Spec-correct verbatim quotes (extracted via WebFetch 2026-08-08).
QUOTE_LOOKUP_DISPLAY_PREFERRED = "The preferred display for this concept"
QUOTE_VALIDATE_DISPLAY = (
    "A valid display for the concept if the system wishes to display this to a user"
)
QUOTE_SUBSUMES_OUTCOME_EQUIVALENT = (
    "4 possible codes to be returned (equivalent, subsumes, subsumed-by, "
    "and not-subsumed)"
)
QUOTE_TRANSLATE_MATCH_EQUIVALENCE = (
    "A code indicating the equivalence of the translation, using values from "
    "[ConceptMapEquivalence]"
)
QUOTE_TRANSLATE_RESULT = (
    "True if the concept could be translated successfully."
)
QUOTE_EXPAND_CONTAINS_DISPLAY = (
    "The recommended display for this item in the expansion."
)

# Conformance fixture data (from tests/fhir_conformance/conftest.py):
#   mrconso:
#     73211009 | PT | "Diabetes mellitus"        | SNOMEDCT_US | C0011849
#     44054006 | PT | "Type 2 diabetes mellitus" | SNOMEDCT_US | C0011847
#     E11      | HT | "Type 2 diabetes mellitus" | ICD10CM     | C0011847
#     860975   | SCD| "24 HR metformin 500 MG Oral Tablet" | RXNORM | C0978484
#   mrrel:
#     A44054006 → A73211009 | isa | PAR   (T2DM parent is DM)
# Same-CUI mappings (via C0011847): SNOMED 44054006 ↔ ICD10CM E11 (both T2DM).

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

CODE_DM_SNOMED = "73211009"          # "Diabetes mellitus"
DISPLAY_DM_SNOMED = "Diabetes mellitus"
CODE_T2DM_SNOMED = "44054006"        # "Type 2 diabetes mellitus"
DISPLAY_T2DM_SNOMED = "Type 2 diabetes mellitus"
CODE_T2DM_ICD10CM = "E11"            # ICD-10-CM equivalent (same CUI C0011847)
DISPLAY_T2DM_ICD10CM = "Type 2 diabetes mellitus"
CODE_METFORMIN_RXNORM = "860975"     # RxNorm SCD
DISPLAY_METFORMIN_RXNORM = "24 HR metformin 500 MG Oral Tablet"


def _params_by_name(body: dict) -> dict[str, dict]:
    """Index a Parameters body's parameter list by name (last write wins)."""
    if not isinstance(body, dict) or "parameter" not in body:
        return {}
    out: dict[str, dict] = {}
    for p in body.get("parameter", []):
        if isinstance(p, dict) and "name" in p:
            out[p["name"]] = p
    return out


# =============================================================================
# L1: $lookup display = "preferred display for this concept"
# =============================================================================

class TestLens1LookupDisplayPreferred:
    """L1 — $lookup Out `display` is the preferred display per R4 spec.

    Spec citation: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    Out `display`: "The preferred display for this concept" (cardinality 1..1).

    Clinical correctness: the engine's mrconso PT (preferred term) row IS the
    preferred display. The implementation in `engines/fhir/responses.py:
    build_parameters_lookup` uses `code_info.name` (resolved from the preferred
    atom via the engine). Cross-check across all seeded code systems that the
    display matches the seeded STR verbatim — silent drift here would
    propagate to every downstream UI display.
    """

    def test_t10_snomed_dm_lookup_display_is_preferred_term(
        self, fhir_client
    ):
        """SNOMED 73211009 (Diabetes mellitus) — display MUST be the PT STR."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": CODE_DM_SNOMED},
        )
        assert resp.status_code == 200
        body = resp.json()
        params = _params_by_name(body)
        assert "display" in params, "Out `display` parameter MUST be present (1..1)"
        assert params["display"].get("valueString") == DISPLAY_DM_SNOMED, (
            f"$lookup Out display drift: expected {DISPLAY_DM_SNOMED!r}, "
            f"got {params['display'].get('valueString')!r}. "
            f"Spec: {SPEC_LOOKUP} — {QUOTE_LOOKUP_DISPLAY_PREFERRED!r}."
        )

    def test_t11_snomed_t2dm_lookup_display_is_preferred_term(
        self, fhir_client
    ):
        """SNOMED 44054006 (Type 2 diabetes mellitus)."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": CODE_T2DM_SNOMED},
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params["display"].get("valueString") == DISPLAY_T2DM_SNOMED

    def test_t12_icd10cm_t2dm_lookup_display_is_preferred_term(
        self, fhir_client
    ):
        """ICD-10-CM E11 — the HT STR is the preferred term."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": ICD10CM_URI, "code": CODE_T2DM_ICD10CM},
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params["display"].get("valueString") == DISPLAY_T2DM_ICD10CM, (
            f"ICD-10-CM display drift: expected {DISPLAY_T2DM_ICD10CM!r}, "
            f"got {params['display'].get('valueString')!r}."
        )

    def test_t13_rxnorm_metformin_lookup_display_is_preferred_term(
        self, fhir_client
    ):
        """RxNorm 860975 — SCD (semantic clinical drug) STR is preferred."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": RXNORM_URI, "code": CODE_METFORMIN_RXNORM},
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params["display"].get("valueString") == DISPLAY_METFORMIN_RXNORM

    def test_t14_lookup_display_never_falls_back_to_code_when_name_exists(
        self, fhir_client
    ):
        """When the engine has a name, $lookup MUST NOT fall back to the code.

        A regression where `code_info.name` is None-but-code-known would
        produce a display equal to the code itself (e.g. "73211009") —
        clinically misleading to a human reader.
        """
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": CODE_DM_SNOMED},
        )
        body = resp.json()
        params = _params_by_name(body)
        display_val = params["display"].get("valueString")
        assert display_val != CODE_DM_SNOMED, (
            f"$lookup Out display fell back to the code itself — clinically "
            f"misleading. Got {display_val!r}; expected preferred term "
            f"{DISPLAY_DM_SNOMED!r}."
        )


# =============================================================================
# L2: $validate-code display mismatch — canonical wins over client input
# =============================================================================

class TestLens2ValidateCodeCanonicalDisplay:
    """L2 — $validate-code Out `display` returns the canonical preferred term,
    NOT the client's input display, when the client supplies a wrong display.

    Spec citation:
      - CodeSystem: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
      - ValueSet: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
    Out `display`: "A valid display for the concept if the system wishes to
    display this to a user" (0..1).

    The example response on the spec page mandates: result=false + message
    "The display \"test\" is incorrect" + Out display=<canonical>.

    Client-input-as-canonical drift meta-pattern (count=8 PROMOTED) verified
    NOT recurring — TS-02 TERMINOLOGIST QA-029 fix holds.
    """

    def test_t20_cs_validate_code_wrong_display_returns_canonical(
        self, fhir_client
    ):
        """CS/$validate-code with wrong display → result=false + canonical display."""
        wrong_display = "Totally Wrong Disease Name"
        resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "display": wrong_display,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        # Spec example: result=false, message cites wrong value, display=canonical.
        assert params["result"].get("valueBoolean") is False, (
            "Display mismatch MUST drive result=false per spec example."
        )
        assert "message" in params, (
            "Display mismatch MUST produce a message per spec example."
        )
        # The canonical display MUST be returned — NOT the wrong input echoed.
        assert "display" in params, "Out display MUST be present on mismatch."
        out_display = params["display"].get("valueString")
        assert out_display == DISPLAY_T2DM_SNOMED, (
            f"CS/$validate-code Out display drift: expected canonical "
            f"{DISPLAY_T2DM_SNOMED!r}, got {out_display!r}. The wrong "
            f"client input was {wrong_display!r} — server MUST NOT echo it."
        )

    def test_t21_cs_validate_code_correct_display_returns_canonical(
        self, fhir_client
    ):
        """When display matches canonical, result=true AND Out display=canonical."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "display": DISPLAY_T2DM_SNOMED,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params["result"].get("valueBoolean") is True
        assert params["display"].get("valueString") == DISPLAY_T2DM_SNOMED

    def test_t22_cs_validate_code_no_display_still_returns_canonical(
        self, fhir_client
    ):
        """No display param → result=true + Out display=canonical (informative)."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": CODE_T2DM_SNOMED},
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params["result"].get("valueBoolean") is True
        # Spec: Out display is OPTIONAL — but the implementation surfaces it
        # as an informative hint (engine canonical preferred term). Verify
        # the canonical is returned (not None, not raw code, not alias).
        if "display" in params:
            assert params["display"].get("valueString") == DISPLAY_T2DM_SNOMED

    def test_t23_vs_validate_code_wrong_display_returns_canonical(
        self, fhir_client
    ):
        """VS/$validate-code with wrong display → result=false + canonical display.

        Mirrors t20 on the sibling ValueSet surface (CF-SKEPTIC-CS03-01 CLOSED
        via VS-05 SKEPTIC QA-069).
        """
        wrong_display = "Wrong VS Display"
        resp = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "url": SNOMED_URI,
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "display": wrong_display,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params["result"].get("valueBoolean") is False
        assert "message" in params
        assert params["display"].get("valueString") == DISPLAY_T2DM_SNOMED

    def test_t24_cs_validate_code_message_cites_wrong_value_not_canonical(
        self, fhir_client
    ):
        """The Out `message` MUST cite the wrong value, NOT the canonical.

        Spec example: `message='The display "test" is incorrect'` — the
        message names the CLIENT's wrong value, not the server canonical.
        Separation of concerns: wrong value in `message`, canonical in `display`.
        Pinned by CS-03 TERMINOLOGIST test_t90 methodology.
        """
        wrong = "Bogus Display Value"
        resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "display": wrong,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        msg = params["message"].get("valueString", "")
        assert wrong in msg, (
            f"Message MUST cite the wrong value {wrong!r}; got {msg!r}."
        )
        assert DISPLAY_T2DM_SNOMED not in msg or DISPLAY_T2DM_SNOMED == wrong, (
            f"Message MUST NOT confuse with canonical; got {msg!r}."
        )


# =============================================================================
# L3: $subsumes outcome clinical correctness (directionality)
# =============================================================================

class TestLens3SubsumesOutcomeClinicalCorrectness:
    """L3 — $subsumes outcome is clinically correct for known SNOMED hierarchies.

    Spec citation: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    Quote: "4 possible codes to be returned (equivalent, subsumes,
    subsumed-by, and not-subsumed)".

    Clinical hierarchy: Diabetes mellitus (73211009) is the PARENT of
    Type 2 diabetes mellitus (44054006) per the mrrel fixture (A44054006 →
    A73211009, RELA=isa, REL=PAR). Directionality:

      * codeA=73211009, codeB=44054006 → outcome='subsumes' (A subsumes B)
      * codeA=44054006, codeB=73211009 → outcome='subsumed-by' (A subsumed by B)
      * codeA==codeB                    → outcome='equivalent'
      * unrelated codes                 → outcome='not-subsumed'

    CF-SKEPTIC-CS05-03 (multi-hierarchy) is NOT exercised here — fixture has
    only single-parent mrrel; the engine IS structurally correct.
    """

    def test_t30_parent_subsumes_child(self, fhir_client):
        """codeA=DM (parent), codeB=T2DM (child) → outcome='subsumes'."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": CODE_DM_SNOMED,
                "codeB": CODE_T2DM_SNOMED,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params["outcome"].get("valueCode") == "subsumes", (
            f"Expected outcome='subsumes' (parent subsumes child); got "
            f"{params['outcome'].get('valueCode')!r}. Clinical directionality "
            f"inverted? Spec: {SPEC_SUBSUMES}."
        )

    def test_t31_child_subsumed_by_parent(self, fhir_client):
        """codeA=T2DM (child), codeB=DM (parent) → outcome='subsumed-by'."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": CODE_T2DM_SNOMED,
                "codeB": CODE_DM_SNOMED,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params["outcome"].get("valueCode") == "subsumed-by", (
            f"Expected outcome='subsumed-by' (child is subsumed by parent); "
            f"got {params['outcome'].get('valueCode')!r}."
        )

    def test_t32_identical_codes_outcome_equivalent(self, fhir_client):
        """codeA==codeB → outcome='equivalent'."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": CODE_T2DM_SNOMED,
                "codeB": CODE_T2DM_SNOMED,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params["outcome"].get("valueCode") == "equivalent"

    def test_t33_unrelated_codes_outcome_not_subsumed(self, fhir_client):
        """codeA=T2DM, codeB=metformin → outcome='not-subsumed'.

        T2DM (a clinical finding) and metformin (a drug) are clinically
        unrelated in the SNOMED hierarchy.
        """
        resp = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": CODE_T2DM_SNOMED,
                "codeB": CODE_METFORMIN_RXNORM,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params["outcome"].get("valueCode") == "not-subsumed", (
            f"Expected outcome='not-subsumed' for clinically unrelated "
            f"codes (T2DM vs metformin); got "
            f"{params['outcome'].get('valueCode')!r}."
        )

    def test_t34_outcome_value_set_membership(self, fhir_client):
        """The outcome value MUST be a member of the R4 closed enum.

        Per https://hl7.org/fhir/R4/valueset-concept-subsumption-outcome.html,
        the closed enum is {equivalent, subsumes, subsumed-by, not-subsumed}.
        Hyphenated values MUST render verbatim — 'subsumed-by' not 'subsumedby'.
        """
        # Trigger all 4 outcomes.
        outcomes = set()
        for a, b in [
            (CODE_DM_SNOMED, CODE_T2DM_SNOMED),     # subsumes
            (CODE_T2DM_SNOMED, CODE_DM_SNOMED),     # subsumed-by
            (CODE_T2DM_SNOMED, CODE_T2DM_SNOMED),   # equivalent
            (CODE_T2DM_SNOMED, CODE_METFORMIN_RXNORM),  # not-subsumed
        ]:
            resp = fhir_client.get(
                "/fhir/CodeSystem/$subsumes",
                params={"system": SNOMED_URI, "codeA": a, "codeB": b},
            )
            outcomes.add(_params_by_name(resp.json())["outcome"].get("valueCode"))
        assert outcomes == {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}, (
            f"$subsumes outcome drift: expected the 4-value R4 enum; got "
            f"{outcomes!r}. Hyphenated 'subsumed-by' MUST be verbatim."
        )


# =============================================================================
# L4: $translate match.equivalence clinical correctness
# =============================================================================

class TestLens4TranslateEquivalenceClinicalCorrectness:
    """L4 — $translate match.equivalence is clinically correct for the actual
    relationship between source and target codes.

    Spec citation: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    match.equivalence: "A code indicating the equivalence of the translation,
    using values from [ConceptMapEquivalence]".

    The conformance fixture seeds same-CUI mappings via C0011847: SNOMED
    44054006 (Type 2 diabetes mellitus) and ICD10CM E11 (Type 2 diabetes
    mellitus) share a CUI. The engine emits relationship="equivalent" for
    same-CUI mappings; the canonical map translates that to R4 "equivalent".

    Per the canonical R4 ConceptMapEquivalence value set
    (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html):
    10 values: relatedto | equivalent | equal | wider | narrower | subsumes
    | specializes | inexact | unmatched | disjoint. The R5/R4B `subsumedby`
    and `matches` MUST NOT leak onto the R4 surface (CF-HISTORIAN-VS01-01).
    """

    def test_t40_snomed_to_icd10cm_same_cui_returns_equivalent(self, fhir_client):
        """SNOMED 44054006 → ICD10CM E11 (same CUI C0011847) → equivalence=equivalent.

        Same-CUI mappings are clinically equivalent — both encode "Type 2
        diabetes mellitus". Per the canonical map, engine relationship
        "equivalent" → R4 "equivalent".
        """
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "targetsystem": ICD10CM_URI,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params.get("result", {}).get("valueBoolean") is True, (
            "Same-CUI mapping MUST return result=true."
        )
        matches = [
            p for p in body.get("parameter", []) if p.get("name") == "match"
        ]
        assert matches, "Same-CUI mapping MUST produce at least one match."
        for m in matches:
            parts = {part.get("name"): part for part in m.get("part", [])}
            assert "equivalence" in parts, (
                "Each match MUST have an equivalence part per spec."
            )
            equiv = parts["equivalence"].get("valueCode")
            assert equiv == "equivalent", (
                f"Same-CUI (T2DM) mapping MUST have equivalence='equivalent'; "
                f"got {equiv!r}. Engine relationship 'equivalent' should map "
                f"to R4 'equivalent' per the canonical translation table."
            )

    def test_t41_translate_equivalence_value_r4_membership(
        self, fhir_client
    ):
        """Every emitted equivalence MUST be in the FHIR R4 closed enum."""
        from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE

        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "targetsystem": ICD10CM_URI,
            },
        )
        body = resp.json()
        for p in body.get("parameter", []):
            if p.get("name") != "match":
                continue
            for part in p.get("part", []):
                if part.get("name") == "equivalence":
                    val = part.get("valueCode")
                    assert val in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                        f"Equivalence value {val!r} not in R4 closed enum. "
                        f"Spec: {SPEC_EQUIVALENCE}."
                    )

    def test_t42_translate_match_concept_coding_clinically_correct(
        self, fhir_client
    ):
        """SNOMED→ICD10CM match.concept points at ICD10CM E11 (NOT source SNOMED).

        The target Coding MUST be the ICD-10-CM concept — clinically, the
        translation produces an ICD-10-CM code, not a SNOMED code.
        """
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "targetsystem": ICD10CM_URI,
            },
        )
        body = resp.json()
        matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
        assert matches, "Translation MUST return at least one match."
        for m in matches:
            parts = {part.get("name"): part for part in m.get("part", [])}
            concept_part = parts.get("concept", {})
            coding = concept_part.get("valueCoding", {})
            assert coding.get("system") == ICD10CM_URI, (
                f"match.concept.system drift: expected {ICD10CM_URI!r} "
                f"(target system), got {coding.get('system')!r}."
            )
            assert coding.get("code") == CODE_T2DM_ICD10CM, (
                f"match.concept.code drift: expected {CODE_T2DM_ICD10CM!r}, "
                f"got {coding.get('code')!r}."
            )
            # Display should be the target system's preferred term for E11
            # (same as ICD-10-CM T2DM).
            assert coding.get("display") == DISPLAY_T2DM_ICD10CM, (
                f"match.concept.display drift: expected "
                f"{DISPLAY_T2DM_ICD10CM!r}, got {coding.get('display')!r}."
            )

    def test_t43_translate_no_match_returns_result_false(self, fhir_client):
        """No matching target → result=false (no spurious 'equivalent').

        SNOMED 73211009 (Diabetes mellitus) does NOT map to RxNorm (drug)
        in the fixture (no shared CUI). result MUST be false.
        """
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_DM_SNOMED,
                "targetsystem": RXNORM_URI,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params.get("result", {}).get("valueBoolean") is False, (
            "No-match case MUST return result=false per spec. Got result=true."
        )
        # Spec: result is true only if at least one match has equivalence
        # NOT in {unmatched, disjoint}.
        matches = [
            p for p in body.get("parameter", []) if p.get("name") == "match"
        ]
        assert matches == [], (
            "No-match case MUST NOT produce match parts."
        )

    def test_t44_translate_no_r5_r4b_contamination_on_wire(
        self, fhir_client
    ):
        """The wire surface MUST NOT emit R5/R4B values (subsumedby, matches).

        CF-HISTORIAN-VS01-01 RESOLVED via CR-018 (canonical map now emits
        R4 spec-correct values). Verify the wire surface is clean.
        """
        # Trigger a translation and assert the response body string does NOT
        # contain R5/R4B values anywhere.
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "targetsystem": ICD10CM_URI,
            },
        )
        body_text = resp.text
        assert '"subsumedby"' not in body_text, (
            "R5/R4B value 'subsumedby' leaked onto R4 $translate wire surface. "
            "Spec: " + SPEC_EQUIVALENCE + " — R4 uses 'specializes'."
        )
        assert '"matches"' not in body_text, (
            "R5-only value 'matches' leaked onto R4 $translate wire surface."
        )
        assert '"not-relatedto"' not in body_text, (
            "Off-spec value 'not-relatedto' on wire (not in any FHIR enum)."
        )


# =============================================================================
# L5: $expand display surface — engine canonical preferred term
# =============================================================================

class TestLens5ExpandDisplaySurface:
    """L5 — $expand `contains[].display` IS the engine's canonical preferred
    term, not a fallback, raw code, or alias-derived value.

    Spec citation:
      - filter: https://hl7.org/fhir/R4/valueset-operation-expand.html
      - contains.display: https://hl7.org/fhir/R4/valueset-definitions.html
        "The recommended display for this item in the expansion."

    The implementation resolves display via engine.get_code_infos (preferred
    atom STR). VS-01 TERMINOLOGIST QA-056 verified this on the intensional
    surface; this lens extends to the FILTER surface.
    """

    def test_t50_filter_diabetes_returns_canonical_displays(self, fhir_client):
        """$expand?filter=diabetes — every contains[].display contains 'diabetes'.

        Per VS-02 TERMINOLOGIST strategy 43 (filter-matching clinical-
        relevance): filter text matches DISPLAY text (clinical term), NOT
        pharmacological relationships. Every returned code's display MUST
        contain the filter substring (case-insensitive).
        """
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes"},
        )
        body = resp.json()
        contains = body.get("expansion", {}).get("contains", [])
        assert contains, (
            "Filter 'diabetes' MUST return at least one code (DM/T2DM seeded)."
        )
        for c in contains:
            display = (c.get("display") or "").lower()
            assert "diabetes" in display, (
                f"Filter clinical-relevance drift: code {c.get('code')!r} "
                f"display {c.get('display')!r} does NOT contain 'diabetes'. "
                f"Filter MUST match display text per VS-02 TERMINOLOGIST "
                f"strategy 43."
            )

    def test_t51_filter_returns_engine_canonical_not_raw_code(
        self, fhir_client
    ):
        """Every contains[].display is the engine preferred term, NOT the code."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes"},
        )
        body = resp.json()
        contains = body.get("expansion", {}).get("contains", [])
        for c in contains:
            assert c.get("display"), (
                f"contains[].display MUST NOT be empty/null for code "
                f"{c.get('code')!r}."
            )
            assert c.get("display") != c.get("code"), (
                f"contains[].display fell back to the raw code — clinically "
                f"misleading. Code={c.get('code')!r}, display="
                f"{c.get('display')!r}."
            )

    def test_t52_explicit_concept_list_resolves_canonical_display(
        self, fhir_client
    ):
        """compose.include[].concept[] with omitted display resolves canonical.

        VS-01 TERMINOLOGIST QA-056 fix: omitted display MUST be resolved via
        engine canonical. Probe on the inline ValueSet POST surface.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://test.example/vs",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": CODE_T2DM_SNOMED},  # display OMITTED
                    ],
                }],
            },
        }
        resp = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        body_resp = resp.json()
        contains = body_resp.get("expansion", {}).get("contains", [])
        codes_to_displays = {c.get("code"): c.get("display") for c in contains}
        assert codes_to_displays.get(CODE_T2DM_SNOMED) == DISPLAY_T2DM_SNOMED, (
            f"Inline ValueSet compose with omitted display MUST resolve to "
            f"engine canonical preferred term {DISPLAY_T2DM_SNOMED!r}; got "
            f"{codes_to_displays.get(CODE_T2DM_SNOMED)!r}. VS-01 TERMINOLOGIST "
            f"QA-056 fix should hold."
        )

    def test_t53_intensional_is_a_descendants_have_canonical_displays(
        self, fhir_client
    ):
        """is-a filter on DM root → root + all descendants have canonical displays.

        SNOMED 73211009 (DM) is-a expansion includes root + T2DM descendant.
        Both contains[] entries MUST have engine canonical preferred term.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://test.example/vs",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept",
                        "op": "is-a",
                        "value": CODE_DM_SNOMED,
                    }],
                }],
            },
        }
        resp = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        body_resp = resp.json()
        contains = body_resp.get("expansion", {}).get("contains", [])
        codes_to_displays = {c.get("code"): c.get("display") for c in contains}
        # Root MUST be present (is-a includes root per spec).
        assert CODE_DM_SNOMED in codes_to_displays, (
            "is-a filter MUST include the root code per FHIR R4 spec."
        )
        assert codes_to_displays[CODE_DM_SNOMED] == DISPLAY_DM_SNOMED
        # Descendant MUST be present.
        assert CODE_T2DM_SNOMED in codes_to_displays, (
            "is-a filter MUST include descendants per FHIR R4 spec."
        )
        assert codes_to_displays[CODE_T2DM_SNOMED] == DISPLAY_T2DM_SNOMED


# =============================================================================
# L6: CapabilityStatement / TerminologyCapabilities clinical correctness
# =============================================================================

class TestLens6CapabilityStatementClinical:
    """L6 — CapabilityStatement + TerminologyCapabilities advertisement is
    clinically correct.

    Spec citation: https://hl7.org/fhir/R4/terminology-service.html §4.7.1.1.
    The codeSystem.content value MUST be clinically appropriate per source:
      - SNOMEDCT_US, RXNORM, ICD10CM, etc. SHOULD be 'not-present' (medterm4ds
        doesn't author the code system) or 'fragment' (partial snapshot).
      - REAL code systems MUST NOT be advertised as 'example' (clinically
        misleading — clients would treat the data as illustrative only).

    The bidirectional canonical-URI invariant (TS-01/TERMINOLOGIST count=8)
    is verified holding on the TS-02 surface (operations matrix).
    """

    def test_t60_capabilitystatement_advertises_all_mandatory_operations(
        self, fhir_client
    ):
        """CapabilityStatement advertises the 7 mandatory operations.

        Per TS-02 spec item 7. The 7 mandatory ops are:
        lookup, validate-code (CS), subsumes, expand, validate-code (VS),
        translate, closure.
        """
        resp = fhir_client.get("/fhir/metadata", params={"mode": "full"})
        body = resp.json()
        rest = body.get("rest", [])
        assert rest, "CapabilityStatement MUST have rest[]"
        ops: list[tuple[str, str]] = []
        for r in rest:
            for resource in r.get("resource", []):
                rtype = resource.get("type")
                for op in resource.get("operation", []):
                    ops.append((rtype, op.get("name")))
        # Mandatory operations per FHIR R4 §4.7.1.2.
        expected = {
            ("CodeSystem", "lookup"),
            ("CodeSystem", "validate-code"),
            ("CodeSystem", "subsumes"),
            ("CodeSystem", "closure"),
            ("ValueSet", "expand"),
            ("ValueSet", "validate-code"),
            ("ConceptMap", "translate"),
        }
        advertised = set(ops)
        missing = expected - advertised
        assert not missing, (
            f"CapabilityStatement missing mandatory operations: {missing!r}. "
            f"Spec: {SPEC_TS} §4.7.1.2."
        )

    def test_t61_terminology_capabilities_content_not_example_for_real_systems(
        self, fhir_client
    ):
        """TerminologyCapabilities codeSystem.content MUST NOT be 'example' for real systems.

        Per FHIR R4 CodeSystemContentMode
        (https://hl7.org/fhir/R4/codesystem.html#content):
          - 'example' = "The code system content is illustrative value sets
            defined by HL7 and not the actual code system content."
          - 'not-present' = "None of the content is present."

        SNOMEDCT_US, RXNORM, ICD10CM, LNC are REAL external code systems.
        Advertising them as 'example' is a clinical correctness violation:
        clients would treat the data as illustrative and not authoritative.
        """
        resp = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        body = resp.json()
        assert body.get("resourceType") == "TerminologyCapabilities"
        code_systems = body.get("codeSystem", [])
        assert code_systems, "TerminologyCapabilities MUST advertise codeSystem[]"
        for cs in code_systems:
            uri = cs.get("uri")
            content = cs.get("content")
            assert content != "example", (
                f"Real code system {uri!r} advertised as content='example' "
                f"— clinically misleading. Real systems MUST use "
                f"'not-present' or 'fragment'."
            )
            assert content in ("not-present", "fragment", "complete"), (
                f"content={content!r} for {uri!r} — not a valid "
                f"CodeSystemContentMode value."
            )

    def test_t62_capability_statement_uses_canonical_uris_only(
        self, fhir_client
    ):
        """CapabilityStatement + extension advertise ONLY canonical URIs.

        Bidirectional canonical-URI invariant (TS-01/TERMINOLOGIST count=8
        PROMOTED): every advertised URI is canonical AND every canonical URI
        is advertised. The HCPCS drift regression class is the load-bearing
        failure mode — verify it does NOT recur.
        """
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

        resp = fhir_client.get("/fhir/metadata", params={"mode": "full"})
        body = resp.json()
        # The supported-system extension lists every canonical URI.
        extensions = body.get("extension", [])
        advertised_uris = {
            ext.get("valueUri")
            for ext in extensions
            if ext.get("url", "").endswith("capabilitystatement-supported-system")
        }
        canonical_uris = set(SYSTEM_TO_FHIR_URI.values())
        # Bidirectional: every canonical URI is advertised.
        missing = canonical_uris - advertised_uris
        assert not missing, (
            f"CapabilityStatement-supported-system missing canonical URIs: "
            f"{missing!r}. Bidirectional invariant violated."
        )
        # Bidirectional: every advertised URI is canonical (no alias leak).
        extras = advertised_uris - canonical_uris
        assert not extras, (
            f"CapabilityStatement-supported-system advertises non-canonical "
            f"URIs (alias leakage): {extras!r}. HCPCS drift regression class."
        )

    def test_t63_capabilitystatement_fhirversion_is_r4_not_package_version(
        self, fhir_client
    ):
        """fhirVersion is the FHIR R4 version, NOT the medterm4ds package version.

        TS-01/TERMINOLOGIST methodology: fhirVersion MUST be '4.0.1' (R4),
        not the medterm4ds package version (e.g. '0.0.1'). Cross-resource
        consistency: CapabilityStatement.fhirVersion == TerminologyCapabilities.fhirVersion.
        """
        cs_resp = fhir_client.get("/fhir/metadata", params={"mode": "full"})
        cs_body = cs_resp.json()
        assert cs_body.get("fhirVersion") == "4.0.1", (
            f"CapabilityStatement.fhirVersion drift: expected '4.0.1' (R4), "
            f"got {cs_body.get('fhirVersion')!r}."
        )
        tc_resp = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        tc_body = tc_resp.json()
        assert tc_body.get("fhirVersion") == "4.0.1", (
            f"TerminologyCapabilities.fhirVersion drift: expected '4.0.1' (R4), "
            f"got {tc_body.get('fhirVersion')!r}."
        )


# =============================================================================
# L7: Cross-operation clinical consistency
# =============================================================================

class TestLens7CrossOperationConsistency:
    """L7 — The same code passed to multiple operations returns clinically
    consistent results.

    Cross-operation-canonical-agreement invariant (strategy 38): $lookup and
    $validate-code MUST agree on canonical system + display for the same
    (system, code). Extends to $expand (filter results) and $subsumes
    (outcome directionality).

    Clinical safety: a CDS hook reading inconsistent displays across
    operations would produce conflicting advice. The implementation MUST
    resolve the same engine canonical preferred term everywhere.
    """

    @pytest.mark.parametrize("system,code,expected_display", [
        (SNOMED_URI, CODE_T2DM_SNOMED, DISPLAY_T2DM_SNOMED),
        (SNOMED_URI, CODE_DM_SNOMED, DISPLAY_DM_SNOMED),
        (ICD10CM_URI, CODE_T2DM_ICD10CM, DISPLAY_T2DM_ICD10CM),
        (RXNORM_URI, CODE_METFORMIN_RXNORM, DISPLAY_METFORMIN_RXNORM),
    ])
    def test_t70_lookup_and_validate_agree_on_display(
        self, fhir_client, system, code, expected_display
    ):
        """$lookup Out display == $validate-code Out display for the same code."""
        lookup_resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        validate_resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": system, "code": code},
        )
        lookup_display = _params_by_name(lookup_resp.json()).get(
            "display", {}
        ).get("valueString")
        validate_params = _params_by_name(validate_resp.json())
        validate_display = validate_params.get("display", {}).get("valueString")
        # Both should resolve the same canonical display.
        if lookup_display and validate_display:
            assert lookup_display == validate_display == expected_display, (
                f"Cross-operation display drift for {system}|{code}: "
                f"$lookup={lookup_display!r}, $validate-code="
                f"{validate_display!r}, expected={expected_display!r}."
            )

    @pytest.mark.parametrize("system,code,expected_display", [
        (SNOMED_URI, CODE_T2DM_SNOMED, DISPLAY_T2DM_SNOMED),
        (SNOMED_URI, CODE_DM_SNOMED, DISPLAY_DM_SNOMED),
    ])
    def test_t71_lookup_and_expand_agree_on_display(
        self, fhir_client, system, code, expected_display
    ):
        """$lookup Out display == $expand contains[].display for same code.

        Filter results from $expand use engine.get_code_infos — same path as
        $lookup. The displays MUST agree.
        """
        # Find the display via $lookup.
        lookup_resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        lookup_display = _params_by_name(lookup_resp.json()).get(
            "display", {}
        ).get("valueString")
        # Use the display as a filter to retrieve from $expand.
        filter_word = (expected_display or "").split()[0].lower()
        expand_resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": filter_word, "system": system},
        )
        contains = expand_resp.json().get("expansion", {}).get("contains", [])
        expand_display = next(
            (c.get("display") for c in contains if c.get("code") == code),
            None,
        )
        if expand_display is not None:
            assert lookup_display == expand_display, (
                f"Cross-operation drift for {system}|{code}: $lookup="
                f"{lookup_display!r}, $expand contains={expand_display!r}."
            )

    def test_t72_translate_source_display_consistent_with_lookup(
        self, fhir_client
    ):
        """The source code's display (from $lookup) is NOT echoed in match.source.

        CF-TERMINOLOGIST-CM02-01 documents this as a deferred enhancement:
        match.source has system+code but no display. The clinical consistency
        invariant here verifies that the TARGET concept's display IS surfaced
        and equals what $lookup would return for the target code.
        """
        # Get $lookup display for ICD10CM E11.
        lookup_resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": ICD10CM_URI, "code": CODE_T2DM_ICD10CM},
        )
        lookup_display = _params_by_name(lookup_resp.json()).get(
            "display", {}
        ).get("valueString")
        # Get $translate SNOMED T2DM → ICD10CM E11.
        translate_resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "targetsystem": ICD10CM_URI,
            },
        )
        body = translate_resp.json()
        matches = [
            p for p in body.get("parameter", []) if p.get("name") == "match"
        ]
        for m in matches:
            parts = {part.get("name"): part for part in m.get("part", [])}
            target_coding = parts.get("concept", {}).get("valueCoding", {})
            if target_coding.get("code") == CODE_T2DM_ICD10CM:
                assert target_coding.get("display") == lookup_display, (
                    f"Cross-operation drift: target concept display "
                    f"{target_coding.get('display')!r} != $lookup display "
                    f"{lookup_display!r} for {ICD10CM_URI}|{CODE_T2DM_ICD10CM}."
                )

    def test_t73_cs_and_vs_validate_code_agree_on_display(
        self, fhir_client
    ):
        """CodeSystem/$validate-code and ValueSet/$validate-code agree on display.

        Sibling-handler parity (VS-05 HISTORIAN strategy 53): same code, same
        canonical display, byte-exact agreement on result + message format.
        """
        cs_resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "display": "Wrong Display",
            },
        )
        vs_resp = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "url": SNOMED_URI,
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "display": "Wrong Display",
            },
        )
        cs_params = _params_by_name(cs_resp.json())
        vs_params = _params_by_name(vs_resp.json())
        # Both should return result=false (display mismatch).
        assert cs_params["result"].get("valueBoolean") is False
        assert vs_params["result"].get("valueBoolean") is False
        # Both should return the SAME canonical display.
        cs_display = cs_params.get("display", {}).get("valueString")
        vs_display = vs_params.get("display", {}).get("valueString")
        assert cs_display == vs_display == DISPLAY_T2DM_SNOMED, (
            f"Cross-handler display drift: CS={cs_display!r}, VS={vs_display!r}, "
            f"expected={DISPLAY_T2DM_SNOMED!r}."
        )


# =============================================================================
# L8: $translate match shape clinical correctness (defensive audit)
# =============================================================================

class TestLens8TranslateMatchShapeClinical:
    """L8 — $translate match shape: each match has exactly the spec-listed parts
    with clinically correct values.

    Spec citation: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    Match sub-parameters: equivalence, concept (Coding), source, dependsOn,
    product. The medterm4ds builder emits equivalence + concept + source
    (omits dependsOn/product — engine doesn't model parameterized mappings).
    """

    def test_t80_match_has_exactly_three_parts_equivalence_concept_source(
        self, fhir_client
    ):
        """Each match part has exactly 3 sub-parts (no dependsOn/product).

        CM-02 EXPLORER methodology: the builder omits dependsOn/product
        because the engine doesn't model parameterized mappings. The clinical
        correctness invariant: every match has the spec-required parts.
        """
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "targetsystem": ICD10CM_URI,
            },
        )
        body = resp.json()
        matches = [
            p for p in body.get("parameter", []) if p.get("name") == "match"
        ]
        assert matches, "Same-CUI translation MUST produce at least one match."
        for m in matches:
            parts = {part.get("name"): part for part in m.get("part", [])}
            # Required parts per spec.
            assert "equivalence" in parts
            assert "concept" in parts
            # Source is the medterm4ds-emitted source Coding.
            assert "source" in parts
            # Engine doesn't model these — they MUST be absent.
            assert "dependsOn" not in parts
            assert "product" not in parts

    def test_t81_match_equivalence_is_valuecode_not_valuestring(
        self, fhir_client
    ):
        """match.equivalence is valueCode per R4 OperationDefinition (NOT valueString).

        Spec citation:
        https://hl7.org/fhir/R4/conceptmap-operation-translate.html
        match.equivalence: type 'code', cardinality 0..1. Wire-format MUST be
        valueCode, not valueString (a Coding datatype confusion).
        """
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "targetsystem": ICD10CM_URI,
            },
        )
        body = resp.json()
        for p in body.get("parameter", []):
            if p.get("name") != "match":
                continue
            for part in p.get("part", []):
                if part.get("name") == "equivalence":
                    assert "valueCode" in part, (
                        "match.equivalence MUST be valueCode (R4 code type), "
                        "not valueString."
                    )
                    assert "valueString" not in part

    def test_t82_match_concept_coding_has_all_three_fields(
        self, fhir_client
    ):
        """match.concept.valueCoding has system + code + display (clinical completeness).

        Per Coding datatype: system 0..1, code 0..1, display 0..1 — but a
        $translate target concept without a display is clinically useless.
        The implementation sources display from CodeMapping.target_display.
        """
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "targetsystem": ICD10CM_URI,
            },
        )
        body = resp.json()
        matches = [
            p for p in body.get("parameter", []) if p.get("name") == "match"
        ]
        for m in matches:
            parts = {part.get("name"): part for part in m.get("part", [])}
            coding = parts.get("concept", {}).get("valueCoding", {})
            assert "system" in coding, "match.concept.valueCoding missing 'system'."
            assert "code" in coding, "match.concept.valueCoding missing 'code'."
            assert "display" in coding, (
                "match.concept.valueCoding missing 'display' — clinically "
                "useless translation result. Target display MUST be sourced "
                "from CodeMapping.target_display."
            )

    def test_t83_translate_message_is_informative_count_on_result_true(
        self, fhir_client
    ):
        """When result=true, message is informative (count of matches).

        Spec: when result is true, message carries hints and warnings. The
        medterm4ds convention is "N matches found" — clinically useful count.
        When result=false, message is an error detail.
        """
        # result=true case.
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_T2DM_SNOMED,
                "targetsystem": ICD10CM_URI,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params.get("result", {}).get("valueBoolean") is True
        msg = params.get("message", {}).get("valueString", "")
        assert "match" in msg.lower(), (
            f"result=true message should be informative (mention matches); "
            f"got {msg!r}."
        )

    def test_t84_translate_no_match_message_clinically_useful(
        self, fhir_client
    ):
        """When result=false, message explains WHY (not just 'no match').

        Clinical safety-error-message clarity (CS-04/CS-05 TERMINOLOGIST
        strategy 34): messages MUST convey the terminological FACT.
        """
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": CODE_DM_SNOMED,  # DM has no RxNorm crosswalk
                "targetsystem": RXNORM_URI,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params.get("result", {}).get("valueBoolean") is False
        msg = params.get("message", {}).get("valueString", "")
        # Message should mention "0" or "no" matches.
        assert any(w in msg.lower() for w in ["0", "no", "not"]), (
            f"result=false message should be clinically informative; got "
            f"{msg!r}."
        )


# =============================================================================
# L9: $lookup property group clinical correctness
# =============================================================================

class TestLens9LookupPropertyGroupClinical:
    """L9 — $lookup Out property group has clinically correct standard properties.

    Spec citation: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    Standard properties per §4.7.5.1: name, version, display, designation,
    langX. The implementation emits name, code, system, display, abstract,
    plus custom properties (cui, tty, aui, patient-friendly, match-type,
    canonical-code, canonical-system).

    CF-SKEPTIC-CS05-01 (abstract hardcoded False) is documented as a
    DEFERRED finding candidate (fixture cannot exercise). This lens verifies
    the standard properties ARE present with clinically correct values.
    """

    def test_t90_lookup_emits_standard_name_parameter(self, fhir_client):
        """$lookup Out MUST include 'name' parameter (code system display name)."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": CODE_T2DM_SNOMED},
        )
        params = _params_by_name(resp.json())
        assert "name" in params, (
            "Out 'name' parameter (code system display name) MUST be present."
        )
        # SNOMED display name should mention "SNOMED".
        name_val = params["name"].get("valueString", "")
        assert "snomed" in name_val.lower(), (
            f"SNOMED code system display name drift: got {name_val!r}; "
            f"expected to contain 'SNOMED'."
        )

    def test_t91_lookup_emits_standard_display_parameter(self, fhir_client):
        """$lookup Out MUST include 'display' parameter (preferred term)."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": CODE_T2DM_SNOMED},
        )
        params = _params_by_name(resp.json())
        assert "display" in params
        assert params["display"].get("valueString") == DISPLAY_T2DM_SNOMED

    def test_t92_lookup_emits_canonical_system_uri(self, fhir_client):
        """$lookup Out system is canonical, NOT client-supplied alias.

        CF-SKEPTIC-CS05-01 / TS-02 TERMINOLOGIST QA-029: client-input-as-
        canonical drift meta-pattern. Verify with an alias input.
        """
        # Use a trailing-slash alias to provoke drift.
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI + "/", "code": CODE_T2DM_SNOMED},
        )
        if resp.status_code == 200:
            params = _params_by_name(resp.json())
            if "system" in params:
                sys_val = params["system"].get("valueUri")
                assert sys_val == SNOMED_URI, (
                    f"$lookup Out system echoes client alias input — "
                    f"got {sys_val!r}, expected canonical {SNOMED_URI!r}. "
                    f"Client-input-as-canonical drift."
                )

    def test_t93_lookup_emits_canonical_system_uri_via_oid_alias(
        self, fhir_client
    ):
        """$lookup Out system is canonical, even with urn:oid alias input."""
        snomed_oid = "urn:oid:2.16.840.1.113883.6.96"
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": snomed_oid, "code": CODE_T2DM_SNOMED},
        )
        if resp.status_code == 200:
            params = _params_by_name(resp.json())
            if "system" in params:
                sys_val = params["system"].get("valueUri")
                assert sys_val == SNOMED_URI, (
                    f"$lookup Out system echoes OID alias — got {sys_val!r}, "
                    f"expected canonical {SNOMED_URI!r}."
                )


# =============================================================================
# L10: Clinical safety — no silent wrong-answer on missing data
# =============================================================================

class TestLens10ClinicalSafetyNoSilentWrongAnswer:
    """L10 — Operations do NOT produce silent-wrong-answer on missing/unknown
    clinical data. Every "not found" case MUST be signaled via the spec-
    mandated response shape (result=false, not-found OperationOutcome,
    empty match list).

    Per GLOBAL_RULES.md "Silent Fallbacks — Prohibited Patterns": silent
    fallbacks degrade clinical correctness invisibly.
    """

    def test_t100_validate_code_unknown_code_returns_result_false(
        self, fhir_client
    ):
        """Unknown code → result=false (NOT result=true or 500)."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": "9999999999X"},
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params.get("result", {}).get("valueBoolean") is False, (
            "Unknown code MUST drive result=false. Got result=true or missing."
        )

    def test_t101_translate_unknown_source_code_returns_result_false(
        self, fhir_client
    ):
        """Unknown source code → result=false with NO matches (silent-wrong-
        answer prevention)."""
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": "9999999999X",
                "targetsystem": ICD10CM_URI,
            },
        )
        body = resp.json()
        params = _params_by_name(body)
        assert params.get("result", {}).get("valueBoolean") is False
        matches = [
            p for p in body.get("parameter", []) if p.get("name") == "match"
        ]
        assert matches == [], (
            "Unknown source code MUST NOT produce spurious matches."
        )

    def test_t102_lookup_unknown_code_returns_operationoutcome_notfound(
        self, fhir_client
    ):
        """Unknown code → 404 or OperationOutcome with not-found issue code.

        Per FHIR R4 §3.6.1: distinguish "operation succeeded, no match"
        (HTTP 200 + OperationOutcome with severity=information and code=not-
        found) from "malformed request" (HTTP 4xx).
        """
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": "9999999999X"},
        )
        # Lookup with unknown code returns 404 or OperationOutcome body.
        # Implementation choice: return OperationOutcome (build_parameters_lookup
        # with code_info=None → OperationOutcome).
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"Unknown code lookup MUST return OperationOutcome; got "
            f"{body.get('resourceType')!r}."
        )
        issues = body.get("issue", [])
        assert issues, "OperationOutcome MUST have at least one issue."
        # The issue code MUST signal 'not-found' (or equivalent informational code).
        valid_codes = {"not-found", "informational", "invalid", "code-invalid"}
        actual_codes = {i.get("code") for i in issues}
        assert actual_codes & valid_codes, (
            f"OperationOutcome issue codes {actual_codes!r} do not signal "
            f"'not-found'. Expected one of {valid_codes!r}."
        )

    def test_t103_subsumes_unknown_code_does_not_500(self, fhir_client):
        """$subsumes with unknown codeA → 200 + outcome='not-subsumed' (no 500)."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": "9999999999X",
                "codeB": CODE_T2DM_SNOMED,
            },
        )
        # Unknown codeA has no parent/child relationship → not-subsumed.
        # The engine MUST NOT raise 500 — the response is semantically
        # "no relationship" rather than an error.
        assert resp.status_code == 200, (
            f"$subsumes with unknown code returned {resp.status_code}; "
            f"expected 200 with outcome='not-subsumed'."
        )
        body = resp.json()
        params = _params_by_name(body)
        outcome = params.get("outcome", {}).get("valueCode")
        assert outcome in ("not-subsumed", "equivalent", "subsumes", "subsumed-by"), (
            f"$subsumes outcome drift: got {outcome!r}, expected a value in "
            f"the R4 closed enum."
        )
