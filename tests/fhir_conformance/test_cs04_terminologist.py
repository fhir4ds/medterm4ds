"""TERMINOLOGIST probes for CS-04 (CodeSystem $subsumes Operation).

Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
      (build: https://build.fhir.org/codesystem-operation-subsumes.html)

TERMINOLOGIST lens (clinical / terminological correctness). Default severity HIGH.
The prior three personalities (SKEPTIC + HISTORIAN + EXPLORER) verified the
structural surface; TERMINOLOGIST verifies the CLINICAL correctness of every
outcome — that the engine's hierarchy traversal gives the clinically-correct
answer, not just a structurally-valid one.

Lens items (per iteration prompt):
  1. Directionality clinical correctness — SNOMED Diabetes (73211009) subsumes
     T2DM (44054006); the mirror must invert. Verify the BFS traversal gives
     the clinically-correct answer for the seeded parent/child pair.
  2. Outcome vocabulary exactness — EXACTLY {equivalent, subsumes, subsumed-by,
     not-subsumed}. No synonyms (broader, narrower, parent, child).
  3. Mixed-system error message clinical clarity — the message names both
     systems and does NOT mislead about the clinical relationship.
  4. Hierarchical correctness across systems — SNOMED well-defined; ICD-10-CM
     has hierarchy (E11 → E11.9 shape); RxNorm ingredient → product; LOINC.
     The fixture only seeds SNOMED parent/child; cross-system probes assert
     the not-subsumed outcome where no relationship exists (clinically
     correct: SNOMED and ICD-10-CM have no defined subsumption relationship).
  5. Code-system URI round-trips — codes judged equivalent by $subsumes
     should be the same code resolvable by $lookup.
  6. Self-subsumption — subsumes(A, A) → equivalent (not subsumes).
  7. CF-TERMINOLOGIST-03 carry-forward — real ConceptMap persistence needed
     to exercise broader/narrower equivalence paths — out of CS-04 scope.

Conformance fixture (tests/fhir_conformance/conftest.py) seeds exactly one
hierarchical relationship:
    SNOMED 44054006 (Type 2 diabetes mellitus) --isa/PAR--> SNOMED 73211009
    (Diabetes mellitus)
Clinical interpretation: 73211009 (Diabetes mellitus, broader) subsumes
44054006 (Type 2 diabetes mellitus, narrower). T2DM IS-A Diabetes mellitus
is a clinically-correct IS-A hierarchy per SNOMED CT.
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
# Out `outcome` closed enum (ConceptSubsumptionOutcome value set):
#   https://hl7.org/fhir/R4/valueset-concept-subsumption-outcome.html
VALID_OUTCOMES = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}

# Synonyms that MUST NOT appear — the spec closed enum is exact. If any of
# these leak, the server is using internal vocabulary instead of the FHIR R4
# ConceptSubsumptionOutcome value set.
LEAKED_SYNONYMS = {
    "broader", "narrower", "parent", "child",
    "broader-than", "narrower-than",
    "subsumes-by", "subsumedby", "not-subsumed-by",
    "descendant", "ancestor", "relatedto", "same",
    "equivalent-to", "equal", "identical",
}

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

SNOMED_DIABETES_MELLITUS = "73211009"   # parent (broader)
SNOMED_T2DM = "44054006"                # child (narrower) — descendant of 73211009
ICD10CM_T2DM = "E11"                    # ICD-10-CM T2DM (no hierarchical child seeded)
RXNORM_METFORMIN_PRODUCT = "860975"     # 24 HR metformin 500 MG Oral Tablet


def _outcome(body: dict) -> str | None:
    """Return the value of the Out `outcome` parameter, or None."""
    for p in body.get("parameter", []):
        if p.get("name") == "outcome":
            return p.get("valueCode")
    return None


def _get_subsumes(client, system: str, code_a: str, code_b: str):
    """Issue a GET $subsumes and return the response."""
    return client.get(
        "/fhir/CodeSystem/$subsumes",
        params={"system": system, "codeA": code_a, "codeB": code_b},
    )


# ---------------------------------------------------------------------------
# Lens 1: Directionality clinical correctness
# ---------------------------------------------------------------------------

class TestLens1DirectionalityClinicalCorrectness:
    """Lens 1: Verify the engine's hierarchy traversal gives the clinically-
    correct answer, not just structurally valid.

    Clinical fact (SNOMED CT): Type 2 diabetes mellitus (44054006) IS-A
    Diabetes mellitus (73211009). Therefore:
      - subsumes(A=73211009, B=44054006) → 'subsumes' (Diabetes subsumes T2DM)
      - subsumes(A=44054006, B=73211009) → 'subsumed-by' (T2DM is subsumed by
        Diabetes)
    The engine's BFS (`is_descendant`) must traverse the seeded mrrel row
    (`A44054006 isa PAR A73211009`) correctly in BOTH directions.
    """

    def test_t10_parent_subsumes_child_clinically_correct(self, fhir_client):
        """Diabetes mellitus (73211009) subsumes T2DM (44054006).

        Clinical justification: T2DM is a specific form of Diabetes mellitus.
        The broader concept (Diabetes) subsumes the narrower (T2DM). Per spec
        Out `outcome` item 5: "subsumes — A subsumes B (A is broader)".
        Source: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
        """
        r = _get_subsumes(
            fhir_client, SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_T2DM
        )
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        assert outcome == "subsumes", (
            f"Clinical directionality violated: Diabetes(73211009) MUST "
            f"subsume T2DM(44054006); got outcome={outcome!r}. "
            f"The engine's BFS traversed the seeded isa/PAR row incorrectly."
        )

    def test_t11_child_subsumed_by_parent_clinically_correct(self, fhir_client):
        """T2DM (44054006) is subsumed by Diabetes mellitus (73211009).

        Clinical justification: the reverse direction. The narrower concept
        (T2DM) is subsumed by the broader (Diabetes). Per spec Out `outcome`
        item 6: "subsumed-by — A is subsumed by B (B is broader)".
        """
        r = _get_subsumes(
            fhir_client, SNOMED_URI, SNOMED_T2DM, SNOMED_DIABETES_MELLITUS
        )
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        assert outcome == "subsumed-by", (
            f"Clinical directionality violated: T2DM(44054006) MUST be "
            f"subsumed-by Diabetes(73211009); got outcome={outcome!r}."
        )

    def test_t12_directionality_mirror_holds(self, fhir_client):
        """The subsumes ↔ subsumed-by inversion MUST hold for the seeded pair.

        Methodology (TS-04 TERMINOLOGIST single-vs-batch equivalence adapted
        to direction parity): issue both directions and assert the outcomes
        are exact mirrors. A future regression breaking one direction but
        not the other would fail this probe.
        """
        r_forward = _get_subsumes(
            fhir_client, SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_T2DM
        )
        r_reverse = _get_subsumes(
            fhir_client, SNOMED_URI, SNOMED_T2DM, SNOMED_DIABETES_MELLITUS
        )
        forward = _outcome(r_forward.json())
        reverse = _outcome(r_reverse.json())
        assert forward == "subsumes" and reverse == "subsumed-by", (
            f"Directionality mirror broken: forward={forward!r}, "
            f"reverse={reverse!r}. Clinical contract: Diabetes↔T2DM."
        )

    def test_t13_not_subsumed_is_symmetric(self, fhir_client):
        """not-subsumed is symmetric: swapping A/B does NOT change the outcome.

        Clinical justification: if A and B have no hierarchical relationship,
        neither A subsumes B nor B subsumes A. The mirror MUST also be
        not-subsumed. This is a structural-clinical invariant: there is no
        directionality to "no relationship".
        """
        # SNOMED_T2DM vs a SNOMED code with no seeded relationship
        unrelated = "9999999999"
        r1 = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_T2DM, unrelated)
        r2 = _get_subsumes(fhir_client, SNOMED_URI, unrelated, SNOMED_T2DM)
        o1 = _outcome(r1.json())
        o2 = _outcome(r2.json())
        assert o1 == "not-subsumed" and o2 == "not-subsumed", (
            f"not-subsumed symmetry broken: A→B={o1!r}, B→A={o2!r}. "
            f"Clinically, 'no relationship' has no direction."
        )


# ---------------------------------------------------------------------------
# Lens 2: Outcome vocabulary exactness
# ---------------------------------------------------------------------------

class TestLens2OutcomeVocabularyExactness:
    """Lens 2: Outcome values are EXACTLY from the closed enum. No synonyms.
    """

    @pytest.mark.parametrize(
        "code_a,code_b",
        [
            (SNOMED_T2DM, SNOMED_T2DM),                # equivalent
            (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM),   # subsumes
            (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS),   # subsumed-by
            (SNOMED_T2DM, "9999999999"),               # not-subsumed
        ],
        ids=["equivalent", "subsumes", "subsumed-by", "not-subsumed"],
    )
    def test_t20_outcome_always_from_closed_enum(
        self, fhir_client, code_a, code_b
    ):
        """Every outcome is from {equivalent, subsumes, subsumed-by, not-subsumed}.

        Spec basis: ConceptSubsumptionOutcome value set
        https://hl7.org/fhir/R4/valueset-concept-subsumption-outcome.html
        The closed enum is the contract; leaking internal vocabulary would
        be a clinical-correctness failure (clients would misinterpret the
        outcome).
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, code_a, code_b)
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        assert outcome in VALID_OUTCOMES, (
            f"Outcome {outcome!r} is NOT in the FHIR R4 closed enum "
            f"{sorted(VALID_OUTCOMES)}. The server is leaking internal "
            f"vocabulary — a clinical-correctness failure."
        )

    @pytest.mark.parametrize(
        "code_a,code_b",
        [
            (SNOMED_T2DM, SNOMED_T2DM),
            (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM),
            (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS),
            (SNOMED_T2DM, "9999999999"),
        ],
        ids=["equivalent", "subsumes", "subsumed-by", "not-subsumed"],
    )
    def test_t21_outcome_never_leaks_synonyms(
        self, fhir_client, code_a, code_b
    ):
        """The outcome never appears as a leaked synonym.

        Clinical justification: clients interpreting 'broader' or 'parent'
        instead of 'subsumes' would silently mis-rank clinical concepts.
        The closed enum must be byte-exact.
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, code_a, code_b)
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        assert outcome not in LEAKED_SYNONYMS, (
            f"Outcome {outcome!r} is a leaked synonym — clients would "
            f"misinterpret the clinical relationship."
        )

    def test_t22_outcome_parameter_uses_valueCode_not_valueString(self, fhir_client):
        """The Out `outcome` parameter MUST use valueCode (closed enum), not
        valueString.

        Clinical justification: valueCode signals "this is from a closed enum,
        validate strictly"; valueString would signal "free text". The wire
        type IS the clinical contract.
        Source: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
        Out `outcome` row: type = code.
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM)
        body = r.json()
        outcome_param = next(
            p for p in body["parameter"] if p.get("name") == "outcome"
        )
        assert "valueCode" in outcome_param, (
            f"Out `outcome` MUST use valueCode; got keys={list(outcome_param)}. "
            f"The wire type signals the closed-enum contract to clients."
        )
        assert "valueString" not in outcome_param


# ---------------------------------------------------------------------------
# Lens 3: Mixed-system error message clinical clarity
# ---------------------------------------------------------------------------

class TestLens3MixedSystemErrorMessageClarity:
    """Lens 3: Mixed-system error message names both systems and does NOT
    mislead about the clinical relationship.

    Clinical justification: a generic "error" message would leave the client
    unsure whether the relationship is undefined (correct — SNOMED and ICD-10-CM
    have no defined subsumption) OR whether the server failed to compute it.
    The message MUST convey "cross-system relationships are not defined" so
    the client knows this is a TERMINOLOGICAL FACT, not a server limitation.
    """

    def test_t30_mixed_system_message_names_both_systems(self, fhir_client):
        """The mixed-system error message NAMES both the offending system and
        the expected system.

        Clinical justification: the client must know WHICH systems conflicted
        to fix their query. A generic "system mismatch" is non-actionable.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {
                    "name": "codingA",
                    "valueCoding": {
                        "system": SNOMED_URI,
                        "code": SNOMED_DIABETES_MELLITUS,
                    },
                },
                {
                    "name": "codingB",
                    "valueCoding": {
                        "system": ICD10CM_URI,
                        "code": ICD10CM_T2DM,
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, r.text
        diagnostics = r.json()["issue"][0]["diagnostics"]
        # Both systems must be named so the client knows which conflicted.
        assert SNOMED_URI in diagnostics, (
            f"Mixed-system error MUST name SNOMED_URI; got: {diagnostics!r}"
        )
        assert ICD10CM_URI in diagnostics, (
            f"Mixed-system error MUST name ICD10CM_URI; got: {diagnostics!r}"
        )

    def test_t31_mixed_system_message_does_not_mislead_about_relationship(
        self, fhir_client
    ):
        """The mixed-system error message does NOT claim a subsumption
        relationship exists.

        Clinical justification: SNOMED and ICD-10-CM have NO defined
        subsumption relationship — they are independent code systems. The
        message MUST NOT imply the server "couldn't compute" a relationship
        that terminologically does not exist. The phrase "not defined" (or
        equivalent) conveys the terminological fact.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {
                    "name": "codingA",
                    "valueCoding": {
                        "system": SNOMED_URI,
                        "code": SNOMED_DIABETES_MELLITUS,
                    },
                },
                {
                    "name": "codingB",
                    "valueCoding": {
                        "system": ICD10CM_URI,
                        "code": ICD10CM_T2DM,
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        diagnostics = r.json()["issue"][0]["diagnostics"].lower()
        # The message must convey "not defined" or "cross-system" — NOT
        # "could not compute" (which would mislead about the relationship).
        misleading_phrases = ["could not compute", "unable to compute", "failed to compute"]
        for phrase in misleading_phrases:
            assert phrase not in diagnostics, (
                f"Mixed-system error MUST NOT say {phrase!r} — this misleads "
                f"the client into thinking the server failed rather than the "
                f"terminological fact that cross-system subsumption is undefined."
            )

    def test_t32_mixed_system_error_is_fhir_operationoutcome(self, fhir_client):
        """The mixed-system error is a FHIR OperationOutcome, not a plain
        text body.

        Clinical justification: a non-FHIR error shape would break clinical
        workflow clients that parse OperationOutcome to surface the error to
        clinicians. The shape IS the interoperability contract.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {
                    "name": "codingA",
                    "valueCoding": {
                        "system": SNOMED_URI,
                        "code": SNOMED_DIABETES_MELLITUS,
                    },
                },
                {
                    "name": "codingB",
                    "valueCoding": {
                        "system": ICD10CM_URI,
                        "code": ICD10CM_T2DM,
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        assert r.headers["content-type"] == "application/fhir+json", (
            f"Mixed-system error MUST be application/fhir+json; "
            f"got {r.headers['content-type']!r}"
        )
        body_json = r.json()
        assert body_json["resourceType"] == "OperationOutcome"
        assert body_json["issue"], "OperationOutcome MUST have issue[]"


# ---------------------------------------------------------------------------
# Lens 4: Hierarchical correctness across systems
# ---------------------------------------------------------------------------

class TestLens4HierarchicalCorrectnessAcrossSystems:
    """Lens 4: Hierarchical correctness — SNOMED has well-defined hierarchy;
    ICD-10-CM has hierarchy; RxNorm has ingredient → product; LOINC. The
    fixture only seeds SNOMED parent/child; cross-system probes assert the
    clinically-correct not-subsumed where no relationship exists.

    Clinical justification: the server MUST NOT fabricate a cross-system
    subsumption relationship that terminologically does not exist. SNOMED
    and ICD-10-CM are independent code systems — asserting a subsumption
    between them would be a clinical-safety failure.
    """

    def test_t40_snomed_parent_child_correct_outcome(self, fhir_client):
        """SNOMED parent/child produces the clinically-correct subsumes outcome.

        Clinical justification: the seeded SNOMED hierarchy (Diabetes → T2DM)
        IS a real SNOMED CT IS-A relationship. The server MUST honor it.
        """
        r = _get_subsumes(
            fhir_client, SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_T2DM
        )
        assert _outcome(r.json()) == "subsumes"

    def test_t41_cross_system_snomed_vs_icd10cm_yields_400(self, fhir_client):
        """SNOMED code vs ICD-10-CM code on the SAME `system` is blocked by
        the mixed-system check (via codingA/codingB).

        Clinical justification: SNOMED and ICD-10-CM have NO defined
        subsumption relationship. The server MUST refuse rather than guess.
        This is the clinically-correct answer — not a server limitation.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {
                    "name": "codingA",
                    "valueCoding": {
                        "system": SNOMED_URI,
                        "code": SNOMED_DIABETES_MELLITUS,
                    },
                },
                {
                    "name": "codingB",
                    "valueCoding": {
                        "system": ICD10CM_URI,
                        "code": ICD10CM_T2DM,
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, (
            "Cross-system SNOMED vs ICD-10-CM MUST be rejected (400); the "
            "server MUST NOT fabricate a subsumption relationship that "
            "terminologically does not exist."
        )

    def test_t42_same_system_different_codes_no_seed_yields_not_subsumed(
        self, fhir_client
    ):
        """Two ICD-10-CM codes with no seeded hierarchy yield not-subsumed.

        Clinical justification: the fixture seeds ICD-10-CM E11 but NO
        hierarchical children (e.g., E11.9). Querying E11 vs E11.9 (the
        latter not seeded) yields not-subsumed — the clinically-correct
        answer for "the engine has no record of this relationship". This
        is NOT a clinical-safety failure: the server is correctly reporting
        "no known relationship" rather than fabricating one.
        """
        r = _get_subsumes(fhir_client, ICD10CM_URI, ICD10CM_T2DM, "E11.9")
        assert r.status_code == 200
        outcome = _outcome(r.json())
        assert outcome == "not-subsumed", (
            f"ICD-10-CM E11 vs E11.9 (unseeded child) MUST be not-subsumed; "
            f"got {outcome!r}. The server must NOT fabricate a hierarchy "
            f"that is not in its data."
        )


# ---------------------------------------------------------------------------
# Lens 5: Code-system URI round-trips
# ---------------------------------------------------------------------------

class TestLens5CodeSystemUriRoundTrips:
    """Lens 5: Codes that $subsumes judges as equivalent should be the same
    code resolvable by $lookup.

    Clinical justification: if $subsumes says A ≡ B (equivalent), then both
    A and B MUST be resolvable by $lookup using the same system+code. A
    divergence would mean the engine judges "equivalent" on a code that
    $lookup cannot resolve — a clinical-data integrity failure.
    """

    def test_t50_equivalent_codes_resolvable_by_lookup(self, fhir_client):
        """The codes judged equivalent by $subsumes are both resolvable by
        $lookup.

        Methodology (TS-03 TERMINOLOGIST URI-round-trip-from-response adapted
        to $subsumes equivalent outcome).
        """
        # Self-equivalence: subsumes(A, A) → equivalent → A resolvable.
        code_a = SNOMED_DIABETES_MELLITUS
        r_sub = _get_subsumes(fhir_client, SNOMED_URI, code_a, code_a)
        assert _outcome(r_sub.json()) == "equivalent"

        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": code_a},
        )
        assert r_lookup.status_code == 200, (
            f"$lookup MUST resolve the code judged equivalent by $subsumes; "
            f"got {r_lookup.status_code}: {r_lookup.text}"
        )

    def test_t51_subsumes_pair_both_resolvable_by_lookup(self, fhir_client):
        """Both codes in a subsumes relationship are resolvable by $lookup.

        Clinical justification: if Diabetes subsumes T2DM, BOTH concepts
        exist in the engine's data. $lookup on each MUST return 200. A
        divergence (subsumes says yes, $lookup says no) would be a data-
        integrity failure.
        """
        r_lookup_parent = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
        )
        r_lookup_child = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r_lookup_parent.status_code == 200, (
            f"$lookup on parent (Diabetes) MUST return 200; "
            f"got {r_lookup_parent.status_code}"
        )
        assert r_lookup_child.status_code == 200, (
            f"$lookup on child (T2DM) MUST return 200; "
            f"got {r_lookup_child.status_code}"
        )

    def test_t52_uri_in_subsumes_matches_uri_in_lookup(self, fhir_client):
        """The system URI accepted by $subsumes is the same URI accepted by
        $lookup (no silent URI normalization between operations).

        Clinical justification: if $subsumes accepts `http://snomed.info/sct`
        but $lookup silently requires a different form, the client cannot
        build a consistent workflow. The URI space MUST be identical across
        operations.
        """
        # Both operations use the SAME system URI.
        r_sub = _get_subsumes(
            fhir_client, SNOMED_URI, SNOMED_T2DM, SNOMED_DIABETES_MELLITUS
        )
        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r_sub.status_code == 200
        assert r_lookup.status_code == 200
        # Cross-check: the URI is mutually accepted.
        # (Structural invariant; the URI is the contract.)


# ---------------------------------------------------------------------------
# Lens 6: Self-subsumption
# ---------------------------------------------------------------------------

class TestLens6SelfSubsumption:
    """Lens 6: subsumes(A, A) → equivalent (not subsumes).

    Clinical justification: a concept IS itself; the relationship is
    equivalence, not subsumption. Reporting 'subsumes' for self-comparison
    would be terminologically incorrect and could mislead a clinical
    decision-support rule that branches on the outcome.
    """

    @pytest.mark.parametrize(
        "code",
        [
            SNOMED_DIABETES_MELLITUS,
            SNOMED_T2DM,
            ICD10CM_T2DM,
            RXNORM_METFORMIN_PRODUCT,
        ],
        ids=["snomed_parent", "snomed_child", "icd10cm", "rxnorm"],
    )
    def test_t60_self_subsumption_yields_equivalent(
        self, fhir_client, code
    ):
        """subsumes(A, A) → equivalent for every seeded code in every system.

        The implementation short-circuits `if code_a == code_b: return
        equivalent` BEFORE the BFS traversal. This is clinically correct:
        a concept is equivalent to itself, not "subsumes itself" (which
        would imply a strict hierarchy where the concept is its own parent).
        """
        # Resolve the system for the code.
        system = {
            SNOMED_DIABETES_MELLITUS: SNOMED_URI,
            SNOMED_T2DM: SNOMED_URI,
            ICD10CM_T2DM: ICD10CM_URI,
            RXNORM_METFORMIN_PRODUCT: RXNORM_URI,
        }[code]
        r = _get_subsumes(fhir_client, system, code, code)
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        assert outcome == "equivalent", (
            f"Self-subsumption of {code} MUST yield 'equivalent', not "
            f"{outcome!r}. A concept is itself — reporting 'subsumes' would "
            f"be a terminological error."
        )

    def test_t61_self_subsumption_short_circuits_before_bfs(self, fhir_client):
        """Self-subsumption yields equivalent even when the code is UNKNOWN.

        Clinical justification: the engine's short-circuit (code_a == code_b)
        fires BEFORE the BFS lookup. So even an unknown code compared to
        itself yields equivalent — which is terminologically correct: an
        unknown concept, IF it exists, IS itself. The engine is not claiming
        the code exists; it is claiming the relationship.
        """
        unknown = "UNKNOWN_CODE_X"
        r = _get_subsumes(fhir_client, SNOMED_URI, unknown, unknown)
        assert r.status_code == 200
        assert _outcome(r.json()) == "equivalent"


# ---------------------------------------------------------------------------
# Lens 7: CF-TERMINOLOGIST-03 carry-forward (documentation only)
# ---------------------------------------------------------------------------

class TestLens7CarryForwards:
    """Lens 7: CF-TERMINOLOGIST-03 documents that real ConceptMap persistence
    is needed to exercise broader/narrower equivalence paths. This is OUT
    OF SCOPE for CS-04 (CodeSystem $subsumes does not consume ConceptMap).

    The probe documents the carry-forward as a load-bearing contract: when
    CM-* chunks add ConceptMap persistence, this probe MUST be re-evaluated
    to confirm $subsumes still produces correct outcomes when the engine's
    relationship data includes cross-system mappings.
    """

    def test_t70_cf_terminologist_03_documented(self, fhir_client):
        """CF-TERMINOLOGIST-03: real ConceptMap persistence needed to exercise
        broader/narrower equivalence paths.

        Today, $subsumes only consults the engine's mrrel hierarchy (single-
        system isa/PAR rows). Cross-system equivalence (e.g., SNOMED ≡ ICD-10-CM
        via a persisted ConceptMap) is NOT exercised. The mixed-system check
        correctly rejects cross-system queries today.

        This probe documents the CURRENT behavior. When CM-* chunks add
        ConceptMap persistence, this probe MUST be updated to assert the new
        cross-system behavior.
        """
        # Today: cross-system is rejected (mixed-system check fires).
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {
                    "name": "codingA",
                    "valueCoding": {
                        "system": SNOMED_URI,
                        "code": SNOMED_DIABETES_MELLITUS,
                    },
                },
                {
                    "name": "codingB",
                    "valueCoding": {
                        "system": ICD10CM_URI,
                        "code": ICD10CM_T2DM,
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, (
            "CF-TERMINOLOGIST-03 (current behavior): cross-system subsumes "
            "MUST be 400 today. When CM-* chunks add ConceptMap persistence, "
            "UPDATE THIS PROBE to assert the new behavior."
        )
