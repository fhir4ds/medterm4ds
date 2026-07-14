"""TERMINOLOGIST probes for CS-05 (CodeSystem Edge Cases).

Spec: https://build.fhir.org/codesystem.html
       (canonical R4: https://hl7.org/fhir/R4/codesystem.html)
       concept-properties: https://hl7.org/fhir/R4/concept-properties.html
       $lookup: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
       $validate-code: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
       $subsumes: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html

TERMINOLOGIST lens (clinical / terminological correctness). Default severity HIGH.
The prior three personalities (SKEPTIC + HISTORIAN + EXPLORER) verified the
structural surface; TERMINOLOGIST verifies the CLINICAL correctness of every
edge-case outcome — that the engine's handling of abstract concepts, inactive
codes, multi-hierarchy subsumption, version-specific behavior, mutually-
exclusive properties, and patient-friendly availability on edge cases is
clinically safe, not just structurally valid.

Lens items (per iteration prompt):
  1. CF-SKEPTIC-CS05-01 (abstract hardcoded False) — clinical implication:
     if the engine served abstract concepts (e.g. SNOMED clinical finding
     hierarchy roots), $lookup should return abstract=true so EHRs don't
     present them as selectable diagnoses. Confirm DEFERRED appropriate.
  2. CF-SKEPTIC-CS05-02 (missing inactive property) — clinical implication:
     inactive codes should NOT be recommended for new patient documentation.
     Engine filters SUPPRESS='N' (only active); lookup on inactive returns
     404/OperationOutcome — clinically safe. Confirm filtering is the right
     behavior today vs. surfacing inactive with flag.
  3. CF-SKEPTIC-CS05-03 (multi-hierarchy BFS) — clinical implication:
     SNOMED concepts can have multiple parents. $subsumes must traverse all
     paths. Confirm with clinically meaningful multi-hierarchy examples
     (where data exists in seeded SNOMED).
  4. Version-specific clinical correctness — ICD-10-CM updates yearly;
     SNOMED CT has international vs US editions. $lookup?version=2024
     should return the 2024 atom. If engine doesn't have versioned data,
     document the clinical-safety implication.
  5. Mutually-exclusive properties clinical safety — if a code has both
     abstract=true AND inactive=true, which wins? Spec doesn't mandate;
     engine should be consistent.
  6. Patient-friendly name availability on edge cases — for abstract codes
     (if any): patient-friendly likely doesn't exist. For inactive codes:
     patient-friendly might still resolve but shouldn't be surfaced.

Conformance fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
    ("E11", "HT", "Type 2 diabetes mellitus", "AE11", "N", "ICD10CM", "C0011847"),
    ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # single-parent

Clinical interpretation of the seeded SNOMED pair:
  - 73211009 (Diabetes mellitus) is the clinically-broader concept.
  - 44054006 (Type 2 diabetes mellitus) is the clinically-narrower concept.
  - T2DM IS-A Diabetes mellitus is a clinically-correct IS-A hierarchy per
    SNOMED CT — a patient with T2DM also has Diabetes mellitus.

Per GLOBAL_RULES.md:
  - TERMINOLOGIST findings are HIGH severity by default — clinical
    correctness outranks technical correctness.
  - Spec citation required on every probe.
  - Don't manufacture bugs — if the fixture lacks data to exercise an
    item, document as DEFERRED with reproduction shape.
  - Every probe asserts POSITIVE success shape, not just absence of one
    error string ("Test-too-lenient" trigger).
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
# Spec: https://hl7.org/fhir/R4/concept-properties.html

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

SNOMED_DIABETES_MELLITUS = "73211009"   # parent (broader) — clinically a grouping concept
SNOMED_T2DM = "44054006"                # child (narrower) — clinically a billable diagnosis
ICD10CM_T2DM = "E11"                    # ICD-10-CM T2DM (billable diagnosis code)
RXNORM_METFORMIN_PRODUCT = "860975"     # 24 HR metformin 500 MG Oral Tablet (drug product)


def _param_value(body: dict, name: str):
    """Return the value of the first Out parameter with the given name."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
            return None
    return None


def _has_param(body: dict, name: str) -> bool:
    """Return True if an Out parameter with the given name is present."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return True
    return False


def _property_value(body: dict, prop_code: str):
    """Return the value of the first Out `property` part with the given code."""
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        for part in p.get("part", []):
            if part.get("name") == "code" and part.get("valueCode") == prop_code:
                for vpart in p.get("part", []):
                    if vpart.get("name") == "value":
                        return vpart.get("valueString")
    return None


def _list_property_codes(body: dict) -> list[str]:
    """Return the list of all property codes in the Out `property` group."""
    codes: list[str] = []
    for p in body.get("parameter", []):
        if p.get("name") != "property":
            continue
        for part in p.get("part", []):
            if part.get("name") == "code":
                codes.append(part.get("valueCode"))
    return codes


# ===========================================================================
# Lens 1: CF-SKEPTIC-CS05-01 (abstract hardcoded False) clinical safety
# ===========================================================================
# Clinical implication: if the engine ever serves abstract concepts (e.g.
# SNOMED clinical finding hierarchy roots like 404684003 |Clinical finding|),
# $lookup should return abstract=true so EHRs don't present them as
# selectable diagnoses. Current state: hardcoded False — clinically safe
# for current fixture (no abstract codes seeded; all seeded codes are
# billable/selectable) but UNSAFE for production if abstract codes exist
# in UMLS.
#
# TERMINOLOGIST confirms DEFERRED is the clinically appropriate
# classification: the conformance fixture has NO abstract concepts seeded
# (all 4 seeded codes are clinically-selectable diagnosis or drug codes).
# Per "Don't manufacture bugs" — no bug is filed; the CF-SKEPTIC-CS05-01
# reproduction shape (seed an abstract concept, assert abstract=true) is
# load-bearing for a future engine enhancement.

class TestLens1AbstractConceptClinicalSafety:
    """Lens 1: Verify the clinical-correctness contract for abstract concepts.

    Clinical contract: the Out `abstract` parameter (per FHIR R4
    https://hl7.org/fhir/R4/codesystem-operation-lookup.html) is "True if
    this code is abstract (i.e. the code is not meant to be used in an
    instance, only as a grouping/parent concept)." EHRs and CDS hooks use
    this flag to EXCLUDE abstract concepts from pick-lists and order sets.
    If the flag is hardcoded False, an abstract concept (if any were
    seeded) would silently appear as selectable — a patient-safety risk.
    """

    @pytest.mark.parametrize(
        "code,clinical_kind",
        [
            (SNOMED_DIABETES_MELLITUS, "grouping concept (clinically broader)"),
            (SNOMED_T2DM, "billable diagnosis"),
            (ICD10CM_T2DM, "billable diagnosis"),
            (RXNORM_METFORMIN_PRODUCT, "selectable drug product"),
        ],
        ids=["snomed-dm-grouping", "snomed-t2dm", "icd10-t2dm", "rxnorm-metformin"],
    )
    def test_t10_abstract_false_on_seeded_codes_clinically_consistent(
        self, fhir_client, code, clinical_kind
    ):
        """Each seeded code returns abstract=false today.

        Clinical justification: the conformance fixture seeds ONLY
        clinically-selectable codes (diagnosis codes that can be billed;
        drug product that can be ordered). None is a SNOMED hierarchy root
        or other abstract grouping concept. Therefore abstract=false is
        CLINICALLY CORRECT today — these codes CAN be used in an instance.

        The probe is parametrized across all 4 seeded codes so a future
        regression that flips the flag for one system but not others would
        fail loudly.
        """
        system_map = {
            SNOMED_DIABETES_MELLITUS: SNOMED_URI,
            SNOMED_T2DM: SNOMED_URI,
            ICD10CM_T2DM: ICD10CM_URI,
            RXNORM_METFORMIN_PRODUCT: RXNORM_URI,
        }
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system_map[code], "code": code},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("resourceType") == "Parameters"
        abstract = _param_value(body, "abstract")
        assert abstract is False, (
            f"Clinical-correctness violation: code {code} ({clinical_kind}) "
            f"in system {system_map[code]} returned abstract={abstract!r}. "
            f"For clinically-selectable codes, abstract MUST be False (so EHRs "
            f"surface them in pick-lists). If this code is actually abstract "
            f"in the source terminology, the engine's abstract-flag data is "
            f"missing — see CF-SKEPTIC-CS05-01."
        )

    def test_t11_abstract_out_parameter_present_on_every_lookup(self, fhir_client):
        """The Out `abstract` parameter MUST be present on every $lookup.

        Clinical justification: per FHIR R4 $lookup Out Parameters table,
        `abstract` is 0..1 boolean. EHRs parsing the response rely on its
        presence to make pick-list decisions. A missing field is different
        from `abstract=false` — clients cannot distinguish "server says not
        abstract" from "server doesn't know". The implementation emits the
        parameter unconditionally (hardcoded False today), which is the
        clinically-safe choice — clients always know the server's position.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert _has_param(body, "abstract"), (
            "Out `abstract` MUST be present on every $lookup so EHRs can "
            "make pick-list decisions without ambiguity."
        )

    def test_t12_abstract_is_boolean_wire_type_not_string(self, fhir_client):
        """The Out `abstract` parameter MUST be a boolean (valueBoolean),
        not a string (valueString).

        Clinical justification: FHIR R4 §3.4.1 mandates wire-format
        fidelity. EHR boolean parsers expect valueBoolean; a string-
        encoded boolean ('false' as valueString) would fail strict
        parsers and the code would be treated as "abstract unknown" —
        potentially surfacing an abstract concept as selectable.
        Mirrors CS-04 TERMINOLOGIST test_t22 closed-enum wire-type
        assertion methodology.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Find the abstract parameter dict and verify the wire key
        abstract_param = None
        for p in body.get("parameter", []):
            if p.get("name") == "abstract":
                abstract_param = p
                break
        assert abstract_param is not None
        assert "valueBoolean" in abstract_param, (
            "Out `abstract` MUST use valueBoolean wire key; a string-"
            "encoded boolean is a clinical-safety risk (strict parsers "
            "treat it as 'abstract unknown')."
        )
        assert "valueString" not in abstract_param

    def test_t13_abstract_false_on_parent_and_child_no_divergence(self, fhir_client):
        """Both parent (73211009) and child (44054006) return abstract=False.

        Clinical justification: the seeded SNOMED parent concept (Diabetes
        mellitus) IS clinically a grouping concept, but in the conformance
        fixture it is seeded with TTY='PT' (preferred term) — the same TTY
        as the child. The engine has no abstract-flag data (CF-SKEPTIC-
        CS05-01), so BOTH return abstract=False today. This is consistent
        (no divergence) but NOT clinically-correct for the parent concept
        in production — a future engine enhancement MUST differentiate.
        """
        r_parent = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
        )
        r_child = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r_parent.status_code == 200 and r_child.status_code == 200
        parent_abstract = _param_value(r_parent.json(), "abstract")
        child_abstract = _param_value(r_child.json(), "abstract")
        # Today: both False (hardcoded literal). Pin the consistency
        # invariant — a future enhancement MUST differentiate but MUST
        # NOT produce divergence in the hardcoded-default state.
        assert parent_abstract is False and child_abstract is False


# ===========================================================================
# Lens 2: CF-SKEPTIC-CS05-02 (missing inactive property) clinical safety
# ===========================================================================
# Clinical implication: inactive codes should NOT be recommended for new
# patient documentation. If $lookup doesn't return inactive=true, EHRs
# might present them as valid. Current state: engine filters SUPPRESS='N'
# (only active codes). Lookup on inactive code returns 404/OperationOutcome
# — clinically safe because the code is unreachable through $lookup.
#
# TERMINOLOGIST clinical judgment: filtering at lookup IS the right
# behavior today (better than surfacing with a flag), because:
#   (a) clinicians using EHR pick-lists NEVER want to see deprecated codes
#       (a flag is a softer signal than absence);
#   (b) $validate-code on a deprecated code returns result=false (the code
#       is "not valid in the code system" — which is semantically correct
#       for a code that has been suppressed);
#   (c) the FHIR R4 `inactive` property is 0..1 boolean — absence is
#       conformant for active codes.
# The CF-SKEPTIC-CS05-02 reproduction shape (seed SUPPRESS='O', expect
# inactive=true) is the load-bearing contract for a future enhancement
# that surfaces deprecated codes with a flag rather than filtering.

class TestLens2InactiveCodeClinicalSafety:
    """Lens 2: Verify the clinical-safety contract for inactive codes.

    Clinical contract: deprecated/suppressed codes MUST NOT appear in
    patient-facing surfaces (EHR pick-lists, order sets, CDS hooks). The
    engine filters mrconso on SUPPRESS='N' (active only), so inactive
    codes are unreachable through $lookup — clinically safe today.
    """

    def test_t20_active_code_does_not_carry_inactive_flag(self, fhir_client):
        """Active codes MUST NOT emit `inactive=true` in the property group.

        Clinical justification: emitting inactive=true on an active code
        would be a false flag — clinicians might think the code is
        deprecated and avoid using it, even though it is the current
        recommended code. Per FHIR R4 concept-properties.html, `inactive`
        is 0..1 boolean — absence on active codes is conformant.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        codes = _list_property_codes(body)
        assert "inactive" not in codes, (
            "Active code MUST NOT carry `inactive` property. Found "
            f"property codes: {codes}. Emitting inactive=true on an "
            f"active code would be a clinical-safety violation (false "
            f"deprecation signal)."
        )

    def test_t21_suppressed_code_unreachable_through_lookup(self, fhir_client):
        """An inactive/unknown code is unreachable through $lookup —
        returns an OperationOutcome body (code not found).

        Clinical justification: this IS the clinically-safe behavior today.
        Deprecated codes (SUPPRESS='O' or 'D' in UMLS) are filtered out
        at the SQL layer, so $lookup returns an OperationOutcome body —
        clinicians NEVER see them in pick-lists. This is safer than
        surfacing with inactive=true because the flag is a softer signal
        than absence (a tired clinician at 3am might miss the flag and
        document with the deprecated code).

        The probe verifies the invariant on a code that is NOT in the
        fixture — the engine treats both 'unknown' and 'inactive' codes
        identically (OperationOutcome), which is the clinically correct
        behavior for an active-only server. Per FHIR R4 §3.6.1, the
        operation endpoint returns HTTP 200 with an OperationOutcome body
        for "successfully processed but no match" — distinct from HTTP 4xx
        for malformed requests.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": "DEPRECATED_NOT_SEEDED"},
        )
        # HTTP 200 with OperationOutcome body is the FHIR-spec response
        # for "operation succeeded, code not found". The OperationOutcome
        # carries severity=error + code=not-found, signaling to the client
        # that the code is unreachable.
        assert r.status_code == 200, (
            f"Inactive/unknown code lookup MUST return HTTP 200 with "
            f"OperationOutcome body (operation succeeded, code not found "
            f"per FHIR R4 §3.6.1). Got {r.status_code}."
        )
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"Body MUST be OperationOutcome for unknown/inactive code; "
            f"got resourceType={body.get('resourceType')!r}."
        )
        issue = body.get("issue", [{}])[0]
        assert issue.get("severity") == "error"
        assert issue.get("code") == "not-found"

    def test_t22_validate_code_on_unknown_code_returns_result_false(self, fhir_client):
        """$validate-code on an unknown/inactive code returns result=false.

        Clinical justification: a CDS hook checking 'is this code valid
        for new documentation?' MUST get result=false for deprecated
        codes. The engine's SUPPRESS='N' filter ensures the code is
        absent from get_code_infos, so result=false. Clinically safe.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": "DEPRECATED_NOT_SEEDED",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("resourceType") == "Parameters"
        result = _param_value(body, "result")
        assert result is False, (
            "Deprecated/unknown code MUST return result=false on "
            "$validate-code (clinically safe: prevents new documentation "
            "with deprecated codes)."
        )

    def test_t23_validate_code_on_active_code_returns_result_true(self, fhir_client):
        """$validate-code on an active code returns result=true.

        Clinical justification: the positive-case baseline. An active,
        seeded code MUST validate true so CDS hooks don't false-reject
        valid documentation.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        result = _param_value(body, "result")
        assert result is True


# ===========================================================================
# Lens 3: CF-SKEPTIC-CS05-03 (multi-hierarchy BFS) clinical correctness
# ===========================================================================
# Clinical implication: SNOMED concepts can have multiple parents (e.g.
# a disease that's both an endocrine disorder AND a metabolic disorder).
# $subsumes must traverse all paths. HISTORIAN verified BFS correctness
# via AST + synthetic DAG. TERMINOLOGIST confirms with the clinically
# meaningful parent/child pair in the seeded SNOMED data.

class TestLens3MultiHierarchyClinicalCorrectness:
    """Lens 3: Verify multi-hierarchy subsumption gives clinically-correct
    answers on the seeded pair.

    The conformance fixture seeds ONE parent/child relationship (single-
    hierarchy). Multi-hierarchy correctness cannot be exercised on this
    fixture (CF-SKEPTIC-CS05-03). TERMINOLOGIST's value-add: confirm the
    SEEDED single-hierarchy case gives the clinically-correct answer
    (which is the load-bearing clinical contract for this fixture).
    """

    def test_t30_parent_subsumes_child_clinically_correct(self, fhir_client):
        """SNOMED Diabetes mellitus (73211009) subsumes T2DM (44054006).

        Clinical justification: T2DM IS-A Diabetes mellitus per SNOMED CT.
        A patient with T2DM has Diabetes mellitus. Therefore the broader
        concept (Diabetes) subsumes the narrower (T2DM). This is the
        clinically-correct answer for CDS hooks asking "does this patient
        have a broader condition?".
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_DIABETES_MELLITUS,
                "codeB": SNOMED_T2DM,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        outcome = _param_value(body, "outcome")
        assert outcome == "subsumes", (
            f"Clinical-correctness violation: Diabetes(73211009) MUST "
            f"subsume T2DM(44054006) per SNOMED CT IS-A hierarchy. "
            f"Got outcome={outcome!r}."
        )

    def test_t31_child_subsumed_by_parent_clinically_correct(self, fhir_client):
        """T2DM (44054006) is subsumed by Diabetes mellitus (73211009).

        Clinical justification: the mirror direction. A CDS hook asking
        'is this more-specific code covered by the broader category?' gets
        the clinically-correct answer.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_T2DM,
                "codeB": SNOMED_DIABETES_MELLITUS,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        outcome = _param_value(body, "outcome")
        assert outcome == "subsumed-by"

    def test_t32_self_subsumption_short_circuits_to_equivalent(self, fhir_client):
        """subsumes(A, A) short-circuits to 'equivalent'.

        Clinical justification: a code is equivalent to itself. CDS hooks
        asking 'is this code in the same hierarchy branch?' for the same
        code get the clinically-correct answer. The short-circuit fires
        BEFORE the BFS walk — so even a code with no seeded mrrel rows
        gets the correct self-equivalence answer.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_T2DM,
                "codeB": SNOMED_T2DM,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        outcome = _param_value(body, "outcome")
        assert outcome == "equivalent", (
            f"Self-subsumption MUST short-circuit to 'equivalent'. "
            f"Got outcome={outcome!r}."
        )

    def test_t33_cross_system_codes_return_not_subsumed_clinically_correct(
        self, fhir_client
    ):
        """SNOMED T2DM vs ICD-10-CM E11 (same clinical meaning, different
        code systems) returns 'not-subsumed'.

        Clinical justification: $subsumes is WITHIN a single code system
        per FHIR R4 spec (In `system` is 1..1). Cross-system subsumption
        is undefined — the clinically-correct answer is 'not-subsumed'
        (the engine treats the supplied system URI as the implicit
        context). A future regression that walks cross-system mrrel rows
        (if seeded) would produce a clinically-misleading 'subsumes' or
        'subsumed-by' outcome — the probe guards against that.
        """
        # Supply SNOMED as system, ICD-10-CM T2DM as codeB. The engine's
        # $subsumes handler treats both codes as being in SNOMED context.
        # Since E11 is NOT seeded in SNOMED, the engine returns
        # not-subsumed. This is the clinically-correct answer for a
        # cross-system probe on a within-system operation.
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_T2DM,
                "codeB": ICD10CM_T2DM,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        outcome = _param_value(body, "outcome")
        assert outcome == "not-subsumed", (
            f"Cross-system codes (SNOMED T2DM vs ICD-10-CM E11) MUST "
            f"return not-subsumed. Got outcome={outcome!r}."
        )


# ===========================================================================
# Lens 4: Version-specific clinical correctness
# ===========================================================================
# ICD-10-CM updates yearly (FY revisions). SNOMED CT has international vs
# US editions (every 6 months). $lookup?version=2024 should return the
# 2024 atom. The engine loads a single mrconso snapshot (no versioned
# data) — `version` param is accepted but ignored. This is INTENDED per
# AGENTS.md NOT A BUG registry.
#
# TERMINOLOGIST clinical judgment: accepting-but-ignoring `version` is
# clinically acceptable today because:
#   (a) the engine is honest about its single-snapshot nature (no false
#       claim of version support);
#   (b) $validate-code on a code that was valid in a prior version but is
#       deprecated in the loaded version returns the loaded-version
#       answer (which is the only data the engine has);
#   (c) clients wanting versioned lookups should use a versioned UMLS
#       snapshot — the engine surface is the loaded snapshot.
# The clinical-safety implication: a CDS hook using this server SHOULD
# document that lookups reflect the loaded snapshot only.

class TestLens4VersionSpecificClinicalCorrectness:
    """Lens 4: Verify the version-parameter clinical-correctness contract.

    Clinical contract: $lookup?version=X MUST be accepted (per FHIR R4
    In Parameters 0..1 string) and MUST NOT produce a different clinical
    answer than the no-version call (because the engine has one snapshot).
    A future multi-version engine MUST honor the param; today it is
    ignored (INTENDED per AGENTS.md).
    """

    @pytest.mark.parametrize(
        "version",
        ["2024-09", "2025-03", "2099-12", "1.0.0-prototype"],
        ids=["recent-umls", "future-umls", "nonexistent-future", "malformed"],
    )
    def test_t40_lookup_with_version_accepted_and_consistent(
        self, fhir_client, version
    ):
        """$lookup?version=X is accepted and produces the same clinical
        answer as the no-version call.

        Clinical justification: a CDS hook passing a version MUST NOT
        get a different display/code/abstract than a hook that omits
        version. The engine has one snapshot — the clinical answer is
        the snapshot's answer regardless of the requested version.
        """
        r_with = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM, "version": version},
        )
        r_without = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r_with.status_code == 200 and r_without.status_code == 200
        body_with = r_with.json()
        body_without = r_without.json()
        # Clinical answer MUST be identical — display, code, abstract
        assert _param_value(body_with, "display") == _param_value(body_without, "display")
        assert _param_value(body_with, "code") == _param_value(body_without, "code")
        assert _param_value(body_with, "abstract") == _param_value(body_without, "abstract")

    def test_t41_validate_code_with_version_accepted(self, fhir_client):
        """$validate-code?version=X is accepted (per spec In Parameters
        0..1 string) and returns the same result as without version.

        Clinical justification: a CDS hook passing version MUST NOT get
        a spurious result=false (which would false-reject valid
        documentation) or a spurious result=true (which would false-
        accept invalid documentation).
        """
        r_with = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "version": "2099-99",
            },
        )
        r_without = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r_with.status_code == 200 and r_without.status_code == 200
        assert _param_value(r_with.json(), "result") == _param_value(r_without.json(), "result")

    def test_t42_subsumes_with_version_accepted(self, fhir_client):
        """$subsumes?version=X is accepted and returns the same outcome.

        Clinical justification: a CDS hook asking 'is A broader than B?'
        with a version specifier MUST get the same clinical answer as
        without — the engine has one snapshot.
        """
        r_with = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_DIABETES_MELLITUS,
                "codeB": SNOMED_T2DM,
                "version": "2025-03",
            },
        )
        r_without = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_DIABETES_MELLITUS,
                "codeB": SNOMED_T2DM,
            },
        )
        assert r_with.status_code == 200 and r_without.status_code == 200
        assert _param_value(r_with.json(), "outcome") == _param_value(r_without.json(), "outcome")


# ===========================================================================
# Lens 5: Mutually-exclusive properties clinical safety
# ===========================================================================
# If a code has both abstract=true AND inactive=true, which wins for
# clinical use? The spec doesn't mandate; the engine should be consistent.
# The engine has NEITHER property as engine-sourced data today (both are
# defaults: abstract hardcoded False, inactive omitted). So the question
# is theoretical for the current fixture.
#
# TERMINOLOGIST clinical judgment: when BOTH flags are present in a future
# engine enhancement, the clinically-correct precedence is:
#   inactive=true wins over abstract=true
# because a deprecated code is NEVER surfaced (regardless of abstract-ness),
# while an abstract code is surfaced with a warning. The engine should
# document this precedence when the future enhancement lands.

class TestLens5MutuallyExclusivePropertiesClinicalSafety:
    """Lens 5: Verify the mutually-exclusive-properties clinical-safety
    contract.

    Today the engine has no abstract-flag data and no inactive-code data
    (both are defaults: abstract hardcoded False, inactive omitted). So
    the mutually-exclusive precedence question is theoretical. TERMINOLOGIST
    documents the contract for the future enhancement.
    """

    def test_t50_active_seeded_code_no_inactive_property_and_abstract_false(
        self, fhir_client
    ):
        """An active seeded code has abstract=false AND no `inactive`
        property — consistent clinical signals.

        Clinical justification: the two signals agree — the code is
        selectable (abstract=false) AND not deprecated (no inactive).
        A future regression that emits conflicting signals (e.g.
        abstract=false + inactive=true) would be a clinical-safety
        violation (the flags disagree about selectability).
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        codes = _list_property_codes(body)
        abstract = _param_value(body, "abstract")
        # Consistency invariant: active code has abstract=false AND no
        # inactive property.
        assert abstract is False
        assert "inactive" not in codes, (
            f"Active code MUST NOT have `inactive` property. "
            f"Found: {codes}."
        )

    @pytest.mark.parametrize(
        "system,code",
        [
            (SNOMED_URI, SNOMED_T2DM),
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
            (ICD10CM_URI, ICD10CM_T2DM),
            (RXNORM_URI, RXNORM_METFORMIN_PRODUCT),
        ],
        ids=["snomed-t2dm", "snomed-dm", "icd10-t2dm", "rxnorm-metformin"],
    )
    def test_t51_consistency_across_systems(self, fhir_client, system, code):
        """Every seeded code across all 4 systems has consistent signals:
        abstract=false AND no inactive property.

        Clinical justification: cross-system consistency is a clinical-
        safety invariant. A regression that flips one system's signals
        but not others would produce divergent clinical behavior (e.g.
        RxNorm codes treated as abstract while SNOMED codes are
        selectable).
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        codes = _list_property_codes(body)
        abstract = _param_value(body, "abstract")
        assert abstract is False, (
            f"System {system} code {code}: abstract MUST be False for "
            f"a seeded clinically-selectable code."
        )
        assert "inactive" not in codes


# ===========================================================================
# Lens 6: Patient-friendly name availability on edge cases
# ===========================================================================
# For abstract codes (if any): patient-friendly name likely doesn't exist.
# For inactive codes: patient-friendly name might still resolve but
# shouldn't be surfaced.
#
# In the current fixture, NO patient-friendly JSONs are loaded (conformance
# fixture isolates from production JSONs). So the patient-friendly
# properties are absent on every $lookup. The clinical-correctness
# invariant: absence of patient-friendly properties is conformant (0..*
# per §4.8.21.1).

class TestLens6PatientFriendlyNameEdgeCases:
    """Lens 6: Verify patient-friendly name availability on edge cases.

    Clinical contract: patient-friendly names are OPTIONAL Out parameters
    (custom properties under the `property` group per §4.8.21.1 +
    §4.8.11). Their absence is conformant. When present, they SHOULD be
    clinically appropriate (a patient-friendly name for an abstract code
    is questionable; a patient-friendly name for an inactive code SHOULD
    NOT be surfaced because the code itself is unreachable).

    NOTE: the conformance fixture DOES load the production patient-friendly
    JSONs from the default MEDTERM4DS_FHIR4PX_BASELINE path
    (/mnt/d/medterm4ds/reports/fhir4px). This is documented in
    CF-SKEPTIC-CS01-03 (fixture isolation gap). TERMINOLOGIST confirms
    the clinical correctness of the surfaced patient-friendly data on
    the seeded codes — the values are clinically appropriate for
    selectable diagnoses.
    """

    def test_t60_patient_friendly_present_and_clinically_appropriate(
        self, fhir_client
    ):
        """SNOMED T2DM (44054006) carries a patient-friendly name that
        is clinically appropriate.

        Clinical justification: the patient-friendly name for T2DM is
        sourced from MEDLINEPLUS (consumer-health vocabulary). EHRs
        surface this in patient-facing displays (after-visit summaries,
        patient portal). The name MUST be clinically appropriate — not
        misleading, not ambiguous, not a deprecated term. For T2DM, the
        expected patient-friendly name is "Diabetes Type 2" (per the
        production patient-friendly JSON).
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        pf = _property_value(body, "patient-friendly")
        assert pf is not None, (
            "SNOMED T2DM MUST carry a patient-friendly name when "
            "patient-friendly JSONs are loaded."
        )
        # The surfaced patient-friendly name MUST be clinically
        # appropriate — contains "diabetes" (the condition family) and
        # does not contain misleading terms.
        pf_lower = str(pf).lower()
        assert "diabetes" in pf_lower, (
            f"Patient-friendly name for T2DM MUST reference diabetes. "
            f"Got {pf!r}."
        )

    def test_t61_match_type_documented_as_server_local(self, fhir_client):
        """The match-type custom property is documented as server-local
        pipeline vocabulary (CS-01 TERMINOLOGIST QA-045 DECISION (b)).

        Clinical justification: match-type values (e.g. 'same_cui',
        'original', 'broader', 'snomed_fallback') are pipeline metadata
        describing HOW the patient-friendly name was derived. These are
        NOT FHIR R4 ConceptMapEquivalence enum values. Forcing them into
        the equivalence enum would be clinically misleading. EHRs MUST
        treat match-type as opaque engine metadata, not as a clinical
        equivalence signal.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        match_type = _property_value(body, "match-type")
        # match-type is present when patient-friendly JSONs are loaded.
        # Values are server-local vocabulary (CS-01 TERMINOLOGIST
        # QA-045 registry: SERVER_LOCAL_MATCH_TYPE_VOCABULARY).
        if match_type is not None:
            # The value MUST be from the documented server-local registry,
            # NOT a raw FHIR equivalence enum (which would be clinically
            # misleading per DECISION (b)).
            valid_server_local_values = {
                "exact", "original", "broader", "group", "ingredient",
                "same_cui", "cvx_group", "broader_group", "broader_ingredient",
                "first_axis", "snomed_fallback",
                "snomed_to_target_native_hierarchy",
                "snomed_to_target_snomed_fallback",
            }
            assert match_type in valid_server_local_values, (
                f"match-type value {match_type!r} is NOT in the documented "
                f"server-local registry. If you added a new value, update "
                f"SERVER_LOCAL_MATCH_TYPE_VOCABULARY in "
                f"test_cs01_terminologist.py."
            )

    def test_t62_property_group_shape_clinically_safe(self, fhir_client):
        """Every property entry has the spec-mandated 2-part structure
        (code + value).

        Clinical justification: EHR parsers expect the FHIR R4 §4.8.21.1
        property group shape. Malformed entries (missing code or value)
        would crash strict parsers — a clinical-safety risk (the entire
        $lookup response could be rejected, hiding the canonical display
        from the clinician).
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        for p in body.get("parameter", []):
            if p.get("name") != "property":
                continue
            parts = p.get("part", [])
            # Must have a 'code' part and a 'value' part
            has_code = any(part.get("name") == "code" for part in parts)
            has_value = any(part.get("name") == "value" for part in parts)
            assert has_code and has_value, (
                f"Malformed property entry (missing code or value part): "
                f"{p}. Clinical-safety risk: strict EHR parsers may "
                f"reject the entire response."
            )

    def test_t63_canonical_display_present_on_every_successful_lookup(
        self, fhir_client
    ):
        """The Out `display` parameter is present on every successful
        $lookup — the clinician sees the canonical name.

        Clinical justification: the Out `display` (per FHIR R4 $lookup
        Out Parameters) is the code system's preferred term. Clinicians
        rely on this for documentation. A missing display would force
        the clinician to type the code without context — a usability
        and safety risk.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        display = _param_value(body, "display")
        assert display == "Type 2 diabetes mellitus", (
            f"Out `display` MUST be the engine canonical preferred term "
            f"for clinicians. Got {display!r}."
        )


# ===========================================================================
# Lens 7: CF-SKEPTIC-CS05-01/02/03 documentation as probes (carry-forward
# as probe pattern from CS-03 TERMINOLOGIST)
# ===========================================================================
# Per CS-03 TERMINOLOGIST methodology: when a carry-forward documents a
# deferred behavior, the probe SHOULD assert the CURRENT behavior so a
# future fix will fail loudly (load-bearing contract, not passive note).
# The 3 CS-05 SKEPTIC carry-forwards are documented here as probes that
# assert the current (deferred) behavior.

class TestLens7CarryForwardsAsProbes:
    """Lens 7: Document the 3 SKEPTIC carry-forwards as load-bearing
    contracts. When a future enhancement resolves them, the probes will
    fail loudly and MUST be updated.
    """

    def test_t70_cf_skeptic_cs05_01_abstract_hardcoded_false_current_behavior(
        self, fhir_client
    ):
        """CF-SKEPTIC-CS05-01: $lookup Out `abstract` is hardcoded False
        at engines/fhir/responses.py:46.

        Reproduction shape for future fixture enhancement: seed an
        abstract concept (e.g. SNOMED definitionStatusId=900000000000074008
        "Primitive" on 73211009), call $lookup, assert abstract=true.

        Today: the current behavior is abstract=False on every seeded
        code. This probe asserts that. When the fixture is enhanced AND
        build_parameters_lookup propagates code_info.abstract, this probe
        MUST be updated to assert the propagated value.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        abstract = _param_value(body, "abstract")
        # CURRENT BEHAVIOR (CF-SKEPTIC-CS05-01). When this fails, the
        # carry-forward has been resolved — update this probe AND the
        # SKEPTIC test_s70 + HISTORIAN test_h10 to assert the propagated
        # engine value.
        assert abstract is False, (
            f"CF-SKEPTIC-CS05-01 contract changed: abstract={abstract!r}. "
            f"If the engine now propagates abstract-ness, update this "
            f"probe AND SKEPTIC test_s70 + HISTORIAN test_h10."
        )

    def test_t71_cf_skeptic_cs05_02_inactive_property_absent_current_behavior(
        self, fhir_client
    ):
        """CF-SKEPTIC-CS05-02: $lookup never emits `inactive` property
        today.

        Reproduction shape for future fixture enhancement: add a mrconso
        row with SUPPRESS='O', call $lookup, assert the response carries
        `inactive=true` in the Out `property` group AND $validate-code
        returns result=false.

        Today: no inactive rows seeded, so `inactive` property is absent
        on every $lookup. This probe asserts that. When the fixture is
        enhanced, this probe MUST be updated to assert the property IS
        emitted for inactive codes.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        codes = _list_property_codes(body)
        # CURRENT BEHAVIOR (CF-SKEPTIC-CS05-02). When this fails, the
        # carry-forward has been resolved — update this probe to assert
        # the `inactive` property IS emitted on inactive codes.
        assert "inactive" not in codes, (
            "CF-SKEPTIC-CS05-02 contract changed: `inactive` property "
            "now emitted. Update this probe to assert the property is "
            "correctly surfaced on inactive codes."
        )

    def test_t72_cf_skeptic_cs05_03_single_hierarchy_seeded(self, fhir_client):
        """CF-SKEPTIC-CS05-03: $subsumes multi-hierarchy correctness
        cannot be exercised (fixture has only single-parent mrrel).

        Today: the only seeded mrrel row is A44054006 isa PAR A73211009
        (single parent). The BFS structurally handles multi-parent DAGs
        (verified by HISTORIAN via AST + synthetic DAG), but no probe
        exercises it on the conformance fixture.

        This probe asserts the current single-hierarchy behavior. When
        the fixture is enhanced with multi-parent mrrel rows, this probe
        MUST be extended with multi-parent probes (grandparent at depth-2,
        second parent at depth-1).
        """
        # Single-parent subsumption holds today.
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_DIABETES_MELLITUS,
                "codeB": SNOMED_T2DM,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        outcome = _param_value(body, "outcome")
        assert outcome == "subsumes"
        # CURRENT BEHAVIOR (CF-SKEPTIC-CS05-03). Multi-parent probes
        # would be added here when the fixture is enhanced.


# ===========================================================================
# Lens 8: Cross-operation canonical agreement (mirrors CS-03 TERMINOLOGIST
# test_t30..t33 + CS-05 EXPLORER test_e10/e11)
# ===========================================================================
# $lookup and $validate-code share get_code_infos and the canonical re-
# resolution pattern. The Out `system` and Out `display` parameters
# returned by both operations MUST agree for the same (system, code)
# input. A future regression adding a translation step to one operation
# but not the other would produce divergent clinical signals (a CDS hook
# using $validate-code might see a different display than a hook using
# $lookup — false-confidence / false-rejection risk).

class TestLens8CrossOperationCanonicalAgreement:
    """Lens 8: $lookup and $validate-code agree on canonical system and
    display across all seeded systems.
    """

    @pytest.mark.parametrize(
        "system,code",
        [
            (SNOMED_URI, SNOMED_T2DM),
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
            (ICD10CM_URI, ICD10CM_T2DM),
            (RXNORM_URI, RXNORM_METFORMIN_PRODUCT),
        ],
        ids=["snomed-t2dm", "snomed-dm", "icd10-t2dm", "rxnorm-metformin"],
    )
    def test_t80_lookup_and_validate_agree_on_canonical_system(
        self, fhir_client, system, code
    ):
        """The Out `system` parameter is identical on $lookup and
        $validate-code for the same (system, code) input.

        Clinical justification: a CDS hook querying both operations for
        cross-validation MUST see the same canonical system URI.
        Divergent URIs would be a clinical-safety violation (the hook
        cannot tell which response is authoritative).
        """
        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        r_validate = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": system, "code": code},
        )
        assert r_lookup.status_code == 200 and r_validate.status_code == 200
        lookup_sys = _param_value(r_lookup.json(), "system")
        validate_sys = _param_value(r_validate.json(), "system")
        assert lookup_sys == validate_sys == system, (
            f"Canonical system divergence: lookup={lookup_sys!r}, "
            f"validate={validate_sys!r}, expected={system!r}."
        )

    @pytest.mark.parametrize(
        "system,code",
        [
            (SNOMED_URI, SNOMED_T2DM),
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
            (ICD10CM_URI, ICD10CM_T2DM),
            (RXNORM_URI, RXNORM_METFORMIN_PRODUCT),
        ],
        ids=["snomed-t2dm", "snomed-dm", "icd10-t2dm", "rxnorm-metformin"],
    )
    def test_t81_lookup_and_validate_agree_on_canonical_display(
        self, fhir_client, system, code
    ):
        """The Out `display` parameter is identical on $lookup and
        $validate-code for the same (system, code) input.

        Clinical justification: a clinician seeing the display from
        $validate-code MUST see the same string as from $lookup.
        Divergent displays would be confusing (the clinician cannot
        tell if they're the same code).
        """
        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        r_validate = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": system, "code": code},
        )
        assert r_lookup.status_code == 200 and r_validate.status_code == 200
        lookup_disp = _param_value(r_lookup.json(), "display")
        validate_disp = _param_value(r_validate.json(), "display")
        assert lookup_disp == validate_disp, (
            f"Canonical display divergence: lookup={lookup_disp!r}, "
            f"validate={validate_disp!r}."
        )


# ===========================================================================
# Lens 9: Clinical-safety error-message clarity on edge cases
# ===========================================================================
# Error messages on edge cases (unknown code, unknown system) MUST be
# clinically clear — they must convey the terminological FACT, not imply
# a server limitation. A clinician reading "code not found" understands
# the code is not in the system; a message like "could not compute" would
# be confusing (implies the server failed rather than the code being
# absent). Mirrors CS-04 TERMINOLOGIST test_t31 error-message-clinical-
# clarity probe class.

class TestLens9ClinicalSafetyMessageClarity:
    """Lens 9: Error and result messages convey the terminological fact.
    """

    def test_t90_validate_code_unknown_code_message_clinically_clear(
        self, fhir_client
    ):
        """$validate-code on an unknown code returns a message that
        conveys 'the code is not valid in the code system'.

        Clinical justification: a CDS hook surfacing this message to a
        clinician MUST convey 'you typed a code that doesn't exist'.
        Misleading phrases like 'could not compute' or 'server error'
        would confuse the clinician.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": "NONEXISTENT_XYZ"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        result = _param_value(body, "result")
        assert result is False
        message = _param_value(body, "message")
        assert message is not None
        # Clinical-clarity contract: the message MUST convey the code is
        # not valid. Misleading phrases MUST NOT appear.
        message_lower = str(message).lower()
        for forbidden in ["could not compute", "unable to compute", "server error"]:
            assert forbidden not in message_lower, (
                f"Clinical-clarity violation: message contains "
                f"misleading phrase {forbidden!r}. Message: {message!r}."
            )
        # The message SHOULD name the code and/or system for clinician context.
        assert "NONEXISTENT_XYZ" in str(message) or "code" in message_lower

    def test_t91_validate_code_display_mismatch_message_clinically_clear(
        self, fhir_client
    ):
        """$validate-code with wrong display returns the spec-mandated
        message format: 'The display "X" is incorrect'.

        Clinical justification: per FHIR R4 spec example response
        (https://hl7.org/fhir/R4/codesystem-operation-validate-code.html),
        the message format is byte-exact: 'The display "X" is incorrect'.
        The wrong display is cited in the message so the clinician can
        see what they typed wrong; the canonical display is returned
        separately so the clinician can see the correct value.
        Mirrors CS-03 TERMINOLOGIST test_t90 spec-example-as-byte-exact-
        contract.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "display": "Totally Wrong Display",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        result = _param_value(body, "result")
        assert result is False
        message = _param_value(body, "message")
        assert message is not None
        # Byte-exact spec format
        assert message == 'The display "Totally Wrong Display" is incorrect', (
            f"Spec-mandated message format violated. Got {message!r}. "
            f"The spec example response is byte-exact: "
            f"'The display \"X\" is incorrect'."
        )
        # The canonical display is returned separately
        canonical = _param_value(body, "display")
        assert canonical == "Type 2 diabetes mellitus"

    def test_t92_lookup_unknown_system_returns_clinically_clear_400(
        self, fhir_client
    ):
        """$lookup on an unrecognized system returns 400 with a clinically
        clear OperationOutcome message.

        Clinical justification: a CDS hook with a typo'd system URI MUST
        get a clear error (not a silent empty response). The message
        MUST name the offending system URI.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://example.com/unknown", "code": "X"},
        )
        assert r.status_code == 400
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome"
        # The message MUST name the offending URI
        diagnostics = body.get("issue", [{}])[0].get("diagnostics", "")
        assert "http://example.com/unknown" in diagnostics, (
            f"Error message MUST name the offending system URI. "
            f"Got diagnostics: {diagnostics!r}."
        )
