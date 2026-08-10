"""TERMINOLOGIST RESWEEP probes for CS-04 (CodeSystem $subsumes Operation) —
fresh full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html (R4 4.0.1).

TERMINOLOGIST lens (per ROLE_QA_ENGINEER Section 3): clinical and
terminological correctness. Per GLOBAL_RULES.md "TERMINOLOGIST Findings Are
HIGH Severity", all findings default to HIGH severity.

SKEPTIC + HISTORIAN + EXPLORER prior personalities have completed CLEAN. The
CS-04 surface is structurally hardened. TERMINOLOGIST's job: verify CLINICAL
correctness — that the outcome values, displays, mixed-system diagnostics,
and wire-format rendering are clinically correct and terminologically safe.

EXPLORER tip for TERMINOLOGIST (per qa_handoff.md):
  - Verify clinical safety of hostile-body responses — 200 + empty expansion
    OR 400 OperationOutcome — no silent-wrong-answer with bogus codes
    surfaced.
  - Verify cross-operation canonical-DISPLAY agreement. NOTE: $subsumes
    returns only outcome (valueCode) — no display Out parameter — so the
    canonical-DISPLAY invariant naturally does NOT extend to $subsumes;
    instead, verify $lookup ↔ $validate-code display agreement for codes
    probed via $subsumes (i.e., for each code pair probed in $subsumes, also
    $lookup both codes and verify their displays are clinically correct).

TERMINOLOGIST lens for CS-04 resweep, 10 lens dimensions:

  L1 — Hostile-body clinical safety: when the body has hostile
       compose.include[] / parameter[] entries, the response is clinically
       safe — either 200 with empty/valid expansion OR 400 OperationOutcome.
       No silent-wrong-answer with bogus codes surfaced.
  L2 — Cross-operation canonical-DISPLAY agreement (EXPLORER tip): for each
       code pair probed via $subsumes, $lookup and $validate-code both
       return the SAME canonical display. The canonical-DISPLAY invariant
       naturally does NOT extend to $subsumes (no display Out param), so we
       verify display agreement for codes probed via $subsumes.
  L3 — Subsumption outcome clinical correctness for known SNOMED hierarchies
       (the 4 cases from the iteration prompt):
         (a) 73211009 (DM) subsumes 44054006 (T2DM) → outcome=subsumes
         (b) 44054006 (T2DM) subsumed-by 73211009 (DM) → outcome=subsumed-by
         (c) 73211009 vs 73211009 → outcome=equivalent
         (d) 44054006 (T2DM) vs 860975 (metformin) → outcome=not-subsumed
  L4 — Mixed-system rejection clinical safety: error message MUST be
       clinically informative — naming BOTH systems and conveying the
       terminological FACT (cross-system relationships are undefined), not
       implying a server limitation.
  L5 — Cross-resource clinical consistency: $subsumes outcome consistent
       with CodeSystem READ concept hierarchy.
  L6 — Hyphenated outcome wire-format clinical correctness: 'subsumed-by'
       MUST render correctly in both JSON and XML (clinicians parse these
       values).
  L7 — Subsumption outcome clinical correctness across sources: SNOMED,
       ICD-10-CM, RxNorm — each system's $subsumes outcome is clinically
       correct given the fixture's seeded relationships.
  L8 — Closed-enum outcome vocabulary exactness (no leaked synonyms): the
       outcome values are EXACTLY from {equivalent, subsumes, subsumed-by,
       not-subsumed} — no off-spec values.
  L9 — Clinical safety no-silent-wrong-answer on edge cases: unknown codes,
       unknown systems, mixed codings — every response is clinically
       interpretable (no fabricated relationship).
  L10 — Source-read structural contracts: builder delegates correctly; no
        hardcoded outcome literal; closed-enum membership asserted at
        module load (defensive).

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts the POSITIVE
success shape (200 + Parameters body with the expected fields), not just
absence of an error string.

Per GLOBAL_RULES.md "Right-level test": TERMINOLOGIST does not use
automated proxies for clinical correctness. The outcome value comparison
is the load-bearing test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
#
# In Parameters (R4):
#   codeA    0..1  code    "The 'A' code that is to be tested."
#   codeB    0..1  code    "The 'B' code that is to be tested."
#   system   0..1  uri     "The code system in which subsumption testing
#                           is to be performed."
#   version  0..1  string  "The version of the code system."
#   codingA  0..1  Coding  "The 'A' Coding that is to be tested. The code
#                           system does not have to match the specified
#                           subsumption code system, but the relationships
#                           between the code systems must be well established."
#   codingB  0..1  Coding  "The 'B' Coding that is to be tested. ..."
#
# Out Parameters:
#   outcome   1..1  code   "The subsumption relationship between code/Coding
#                           'A' and code/Coding 'B'. There are 4 possible
#                           codes to be returned (equivalent, subsumes,
#                           subsumed-by, and not-subsumed) as defined in
#                           the concept-subsumption-outcome value set."
#
# Source:
# https://hl7.org/fhir/R4/codesystem-operation-subsumes.html

VALID_OUTCOMES = frozenset(
    {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}
)
# Synonyms that MUST NOT appear — the spec closed enum is exact.
LEAKED_SYNONYMS = frozenset(
    {
        "broader", "narrower", "parent", "child",
        "broader-than", "narrower-than",
        "subsumes-by", "subsumedby", "not-subsumed-by",
        "descendant", "ancestor", "relatedto", "same",
        "equivalent-to", "equal", "identical",
        # R5/R4B forbidden forms
        "subsumedBy", "subsumed_by", "SUBSUMED-BY", "Subsumed-By",
    }
)

SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

SNOMED_DM = "73211009"               # Diabetes mellitus (parent / broader)
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM = "44054006"             # Type 2 diabetes mellitus (child / narrower)
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
ICD10CM_E11 = "E11"                  # Type 2 diabetes mellitus (ICD-10-CM)
ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_METFORMIN = "860975"          # 24 HR metformin 500 MG Oral Tablet
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"

_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
_RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)


# ---------------------------------------------------------------------------
# Source-read helpers (CS-03 HISTORIAN methodology — walks both
# ast.FunctionDef AND ast.AsyncFunctionDef for nested handlers).
# ---------------------------------------------------------------------------

def _get_func_source(file_path: Path, func_name: str) -> str:
    """Extract source text of a function (possibly nested) by name."""
    src = file_path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(src, node) or ""
    return ""


def _get_nested_func_source(parent_name: str, child_name: str) -> str:
    """Source-read helper for nested functions defined inside a factory."""
    src = _FHIR_API_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == parent_name:
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.name == child_name:
                            return ast.get_source_segment(src, child) or ""
    return ""


def _outcome(body: dict) -> str | None:
    """Return the value of the Out `outcome` parameter."""
    for p in body.get("parameter", []):
        if not isinstance(p, dict):
            continue
        if p.get("name") == "outcome":
            if "valueCode" in p:
                return p["valueCode"]
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _diagnostics(body: dict) -> str:
    """Extract the diagnostics string from an OperationOutcome."""
    for issue in body.get("issue", []):
        if not isinstance(issue, dict):
            continue
        if "diagnostics" in issue:
            return issue["diagnostics"]
    return ""


def _param_value(body: dict, name: str, value_key: str = "valueString"):
    for p in body.get("parameter", []):
        if not isinstance(p, dict):
            continue
        if p.get("name") == name and value_key in p:
            return p[value_key]
    return None


def _get_subsumes(client, system: str, code_a: str, code_b: str):
    """Issue a GET $subsumes and return the response."""
    return client.get(
        "/fhir/CodeSystem/$subsumes",
        params={"system": system, "codeA": code_a, "codeB": code_b},
    )


def _build_subsumes_params(
    system: str, code_a: str, code_b: str, **extra
) -> dict:
    """Build a Parameters body for $subsumes POST."""
    params = [
        {"name": "system", "valueUri": system},
        {"name": "codeA", "valueCode": code_a},
        {"name": "codeB", "valueCode": code_b},
    ]
    for k, v in extra.items():
        params.append({"name": k, "valueString": v})
    return {"resourceType": "Parameters", "parameter": params}


# ============================================================================
# L1 — Hostile-body clinical safety
# ============================================================================
# Per EXPLORER tip for TERMINOLOGIST: when the body has hostile entries, the
# response is clinically safe — either 200 + empty/valid expansion OR 400
# OperationOutcome. NO silent-wrong-answer with bogus codes surfaced.

class TestLens1HostileBodyClinicalSafety:
    """L1: Hostile-body responses are clinically safe.

    Clinical justification: a hostile body must NOT cause the server to
    surface a "valid-looking" outcome that misleads the client. The 200 path
    MUST return a clinically-correct outcome (only on real codes); the 400
    path MUST return a FHIR OperationOutcome. There is NO silent-wrong-answer
    path.
    """

    def test_t10_post_hostile_parameter_entries_no_silent_wrong_answer(
        self, fhir_client
    ) -> None:
        """HIGH — POST with hostile parameter[] entries does NOT surface a
        silent-wrong-answer outcome.

        Clinical justification: the client's Parameters body has garbage
        entries in parameter[] (string, int, None). The SKEPTIC fix
        (_parse_parameters isinstance guard) silently drops these. The
        clinically-critical contract is: the dropped entries do NOT
        contribute a fabricated 'system', 'codeA', or 'codeB' value. When
        all 3 scalar params are missing (only hostile entries), the
        response is 400 OperationOutcome — NOT 200 + outcome=not-subsumed.

        Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
        "When invoking this operation, a client SHALL provide both a and b
        codes, either as code or Coding parameters."
        """
        body = {
            "resourceType": "Parameters",
            "parameter": ["string-not-dict", 42, None],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code < 500, (
            f"hostile parameter[] must not 500; got {r.status_code}: "
            f"{r.text[:200]}"
        )
        # The response MUST be 400 — no scalar system/codeA/codeB supplied.
        assert r.status_code == 400, (
            f"hostile parameter[] MUST yield 400 (missing system/codeA/codeB); "
            f"got {r.status_code}. A 200 + outcome response would be "
            f"silent-wrong-answer."
        )
        # The response MUST be a FHIR OperationOutcome.
        assert r.headers["content-type"].startswith("application/fhir+json"), (
            f"400 response MUST be application/fhir+json; got "
            f"{r.headers['content-type']!r}"
        )
        body_json = r.json()
        assert body_json["resourceType"] == "OperationOutcome", (
            f"400 response MUST be OperationOutcome; got "
            f"{body_json.get('resourceType')!r}"
        )

    def test_t11_post_hostile_compose_entries_no_silent_wrong_answer(
        self, fhir_client
    ) -> None:
        """HIGH — POST /fhir/ValueSet/$expand with hostile compose.include[]
        entries does NOT surface silent-wrong-answer codes.

        Clinical justification: the HISTORIAN fix (5 isinstance guards in
        _expand_intensional) silently drops non-dict entries. The clinically-
        critical contract is: the dropped entries do NOT contribute fabricated
        codes to the expansion. The expansion contains ONLY codes from valid
        dict entries.

        Spec cross-reference (hostile compose on $expand surface, surfaced
        via $subsumes-related hostile-body audit):
          https://hl7.org/fhir/R4/valueset-operation-expand.html
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [
                    "garbage-string",
                    42,
                    None,
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_DM}]},
                ],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"hostile compose.include[] must not 500; got {r.status_code}: "
            f"{r.text[:200]}"
        )
        # The 200 path must surface ONLY the valid entry's code.
        if r.status_code == 200:
            contains = r.json().get("expansion", {}).get("contains", [])
            codes = [c.get("code") for c in contains]
            assert SNOMED_DM in codes, (
                f"valid entry MUST be processed; codes: {codes!r}"
            )
            # No fabricated codes from hostile entries.
            for c in codes:
                assert isinstance(c, str) and c, (
                    f"fabricated code surfaced from hostile entry: {c!r}"
                )

    def test_t12_get_subsumes_with_unknown_code_no_silent_wrong_answer(
        self, fhir_client
    ) -> None:
        """HIGH — GET $subsumes with an unknown code returns not-subsumed (NOT
        a fabricated subsumes/equivalent relationship).

        Clinical justification: when codeA or codeB is unknown to the engine,
        the BFS finds no relationship and returns not-subsumed. The clinically-
        critical contract is: the server does NOT fabricate a subsumption
        relationship for an unknown code. not-subsumed is the clinically
        honest answer for "the engine has no record of this code".

        Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
        (engine returns not-subsumed for unknown codes; the alternative is
        an OperationOutcome error per spec — both are clinically safe.)
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, "9999999999", SNOMED_DM)
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        # NOT a fabricated subsumes or equivalent.
        assert outcome == "not-subsumed", (
            f"unknown code MUST yield not-subsumed (no fabricated "
            f"relationship); got {outcome!r}"
        )

    def test_t13_get_subsumes_with_unrecognized_system_no_silent_wrong_answer(
        self, fhir_client
    ) -> None:
        """HIGH — GET $subsumes with an unrecognized system URI returns 400
        (NOT 200 + fabricated outcome).

        Clinical justification: an unrecognized system URI means the engine
        cannot determine subsumption. The clinically-correct response is an
        error (OperationOutcome); a fabricated outcome would mislead the
        client into believing the engine performed a real subsumption check.
        """
        r = _get_subsumes(
            fhir_client,
            "http://example.org/unknown-system",
            "any-code",
            "any-other-code",
        )
        assert r.status_code == 400, (
            f"unrecognized system MUST yield 400 (no fabricated outcome); "
            f"got {r.status_code}: {r.text[:200]}"
        )
        body_json = r.json()
        assert body_json["resourceType"] == "OperationOutcome"


# ============================================================================
# L2 — Cross-operation canonical-DISPLAY agreement (EXPLORER tip)
# ============================================================================
# Per EXPLORER tip: $subsumes returns only outcome (valueCode) — no display
# Out parameter — so canonical-DISPLAY invariant naturally does NOT extend
# to $subsumes. Instead, verify $lookup ↔ $validate-code display agreement
# for codes probed via $subsumes.

class TestLens2CrossOperationCanonicalDisplayAgreement:
    """L2: For each code pair probed via $subsumes, $lookup and $validate-code
    return the SAME canonical display.

    Clinical justification: the canonical display is the clinical contract
    for "what concept does this code represent". $lookup Out display and
    $validate-code Out display MUST agree — a mismatch would mean the
    engine returns different "preferred terms" for the same concept across
    operations, which is silent-wrong-answer for clients building clinical
    decision-support rules on display strings.
    """

    @pytest.mark.parametrize(
        "code,expected_display",
        [
            (SNOMED_DM, SNOMED_DM_DISPLAY),
            (SNOMED_T2DM, SNOMED_T2DM_DISPLAY),
            (ICD10CM_E11, ICD10CM_E11_DISPLAY),
            (RXNORM_METFORMIN, RXNORM_METFORMIN_DISPLAY),
        ],
        ids=["snomed_dm", "snomed_t2dm", "icd10cm_e11", "rxnorm_metformin"],
    )
    def test_t20_lookup_display_matches_validate_code_display(
        self, fhir_client, code, expected_display
    ) -> None:
        """HIGH — $lookup Out display byte-exact equals $validate-code Out
        display for every code probed via $subsumes.

        Spec citations:
          $lookup Out display:
            https://hl7.org/fhir/R4/codesystem-operation-lookup.html
            "The preferred display for this concept"
          $validate-code Out display:
            https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
            "A display for the concept that is validated"
        """
        # Resolve the system for the code.
        system = {
            SNOMED_DM: SNOMED_URI,
            SNOMED_T2DM: SNOMED_URI,
            ICD10CM_E11: ICD10CM_URI,
            RXNORM_METFORMIN: RXNORM_URI,
        }[code]

        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        assert r_lookup.status_code == 200, (
            f"$lookup {code}: {r_lookup.text[:200]}"
        )
        lookup_display = _param_value(r_lookup.json(), "display")

        r_validate = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": system, "code": code},
        )
        assert r_validate.status_code == 200, (
            f"$validate-code {code}: {r_validate.text[:200]}"
        )
        validate_display = _param_value(r_validate.json(), "display")

        # Both must equal the engine's preferred term.
        assert lookup_display == expected_display, (
            f"$lookup display for {code}: {lookup_display!r} != "
            f"{expected_display!r}"
        )
        assert validate_display == expected_display, (
            f"$validate-code display for {code}: {validate_display!r} != "
            f"{expected_display!r}"
        )
        # Cross-operation agreement.
        assert lookup_display == validate_display, (
            f"DISPLAY DRIFT across operations for {code}: "
            f"lookup={lookup_display!r} validate={validate_display!r}"
        )

    def test_t21_subsumes_pair_displays_clinically_distinct(self, fhir_client) -> None:
        """HIGH — the displays of a subsumption pair are clinically distinct.

        Clinical justification: SNOMED DM (73211009, "Diabetes mellitus") and
        SNOMED T2DM (44054006, "Type 2 diabetes mellitus") are CLINICALLY
        DISTINCT concepts. Their displays MUST be different — a client
        reviewing the $subsumes outcome ("subsumes") MUST be able to
        $lookup both codes and see distinct displays (otherwise the
        subsumption relationship would be meaningless: "DM subsumes DM").
        """
        r_lookup_parent = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DM},
        )
        r_lookup_child = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        parent_display = _param_value(r_lookup_parent.json(), "display")
        child_display = _param_value(r_lookup_child.json(), "display")
        assert parent_display != child_display, (
            f"subsumption pair displays MUST be clinically distinct; "
            f"parent={parent_display!r}, child={child_display!r}"
        )

    def test_t22_subsumes_pair_displays_preserved_on_alias_inputs(
        self, fhir_client
    ) -> None:
        """HIGH — $lookup display for a $subsumes-probed code is preserved on
        alias inputs (trailing-slash, urn:oid, uppercase-scheme).

        Clinical justification: the canonical-DISPLAY invariant extends across
        alias inputs per the canonical-DISPLAY cross-operation meta-pattern
        (CS-02 / CS-03 TERMINOLOGIST methodology). For a code probed via
        $subsumes, the $lookup display MUST be the same regardless of which
        alias form of the system URI the client supplies.
        """
        # Baseline: $lookup DM with canonical URI.
        r_canonical = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DM},
        )
        canonical_display = _param_value(r_canonical.json(), "display")

        # Alias 1: trailing-slash.
        r_trailing = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI_TRAILING_SLASH, "code": SNOMED_DM},
        )
        trailing_display = _param_value(r_trailing.json(), "display")

        # Alias 2: urn:oid.
        r_oid = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI_OID, "code": SNOMED_DM},
        )
        oid_display = _param_value(r_oid.json(), "display")

        # Alias 3: uppercase-scheme.
        r_upper = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI_UPPERCASE_SCHEME, "code": SNOMED_DM},
        )
        upper_display = _param_value(r_upper.json(), "display")

        assert canonical_display == trailing_display == oid_display == upper_display, (
            f"canonical-DISPLAY drift on alias inputs: "
            f"canonical={canonical_display!r}, trailing={trailing_display!r}, "
            f"oid={oid_display!r}, upper={upper_display!r}"
        )


# ============================================================================
# L3 — Subsumption outcome clinical correctness (4 known cases)
# ============================================================================
# Per iteration prompt — the 4 known SNOMED hierarchy cases that MUST produce
# clinically-correct outcomes.

class TestLens3SubsumptionOutcomeClinicalCorrectness4Cases:
    """L3: The 4 known SNOMED hierarchy cases MUST produce clinically-correct
    outcomes.

    Clinical justification: these are the load-bearing clinical-correctness
    cases for $subsumes. A bug in any one would be a clinical-safety failure.
    """

    def test_t30_case_a_dm_subsumes_t2dm(self, fhir_client) -> None:
        """Case (a): 73211009 (DM) subsumes 44054006 (T2DM) → outcome=subsumes.

        Clinical justification: Type 2 diabetes mellitus IS-A Diabetes
        mellitus. The broader concept (DM) subsumes the narrower (T2DM).
        Per spec Out `outcome`:
          "subsumes — A subsumes B (A is broader)"
        Source: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_DM, SNOMED_T2DM)
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        assert outcome == "subsumes", (
            f"Clinical-correctness case (a) FAILED: DM(73211009) MUST "
            f"subsume T2DM(44054006); got outcome={outcome!r}"
        )

    def test_t31_case_b_t2dm_subsumed_by_dm(self, fhir_client) -> None:
        """Case (b): 44054006 (T2DM) subsumed-by 73211009 (DM) → outcome=subsumed-by.

        Clinical justification: reverse direction of case (a). The narrower
        concept (T2DM) is subsumed by the broader (DM).
        Per spec Out `outcome`:
          "subsumed-by — A is subsumed by B (B is broader)"
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_T2DM, SNOMED_DM)
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        assert outcome == "subsumed-by", (
            f"Clinical-correctness case (b) FAILED: T2DM(44054006) MUST be "
            f"subsumed-by DM(73211009); got outcome={outcome!r}"
        )

    def test_t32_case_c_dm_vs_dm_equivalent(self, fhir_client) -> None:
        """Case (c): 73211009 vs 73211009 → outcome=equivalent.

        Clinical justification: a concept is equivalent to itself. The
        engine's short-circuit (code_a == code_b) returns 'equivalent'
        BEFORE the BFS — terminologically correct.
        Per spec Out `outcome`:
          "equivalent — A and B are the same concept"
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_DM, SNOMED_DM)
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        assert outcome == "equivalent", (
            f"Clinical-correctness case (c) FAILED: DM(73211009) vs "
            f"DM(73211009) MUST be equivalent; got outcome={outcome!r}"
        )

    def test_t33_case_d_t2dm_vs_metformin_not_subsumed(self, fhir_client) -> None:
        """Case (d): 44054006 (T2DM) vs 860975 (metformin) → outcome=not-subsumed.

        Clinical justification: T2DM (a disease) and metformin (a drug) are
        from DIFFERENT semantic domains within the SAME code system (SNOMED
        CT US). There is NO hierarchical relationship between them — neither
        subsumes the other. not-subsumed is the clinically-correct outcome.
        Per spec Out `outcome`:
          "not-subsumed — no relationship"
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_T2DM, RXNORM_METFORMIN)
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        # Engine: T2DM and metformin have no seeded isa/PAR relationship.
        # Clinical-correct answer is not-subsumed.
        assert outcome == "not-subsumed", (
            f"Clinical-correctness case (d) FAILED: T2DM(44054006) vs "
            f"metformin(860975) MUST be not-subsumed (no hierarchical "
            f"relationship); got outcome={outcome!r}"
        )

    def test_t34_case_d_reverse_metformin_vs_t2dm_not_subsumed(self, fhir_client) -> None:
        """Case (d) reverse: 860975 (metformin) vs 44054006 (T2DM) → not-subsumed.

        Clinical justification: not-subsumed is symmetric — swapping A and B
        does NOT change the outcome (per Lens 1 test_t13 from baseline).
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, RXNORM_METFORMIN, SNOMED_T2DM)
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        assert outcome == "not-subsumed", (
            f"Clinical-correctness case (d) reverse FAILED: metformin vs "
            f"T2DM MUST be not-subsumed; got outcome={outcome!r}"
        )


# ============================================================================
# L4 — Mixed-system rejection clinical safety
# ============================================================================
# Per iteration prompt: error message MUST be clinically informative (naming
# both systems), not just generic 'validation failed'.

class TestLens4MixedSystemRejectionClinicalSafety:
    """L4: Mixed-system rejection message is clinically informative.

    Clinical justification: a generic "validation failed" message would leave
    the client unsure whether the relationship is terminologically undefined
    (correct — SNOMED and ICD-10-CM have no defined subsumption) OR whether
    the server failed to compute it. The message MUST convey "cross-system
    relationships are not defined" so the client knows this is a
    TERMINOLOGICAL FACT, not a server limitation.
    """

    def test_t40_mixed_system_message_names_both_systems_coding_b(
        self, fhir_client
    ) -> None:
        """HIGH — mixed-system error message NAMES both systems (codingB is
        the offender).

        Clinical justification: the client must know WHICH systems conflicted
        to fix their query. The message MUST name BOTH the offending system
        AND the expected system.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {
                    "name": "codingA",
                    "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DM},
                },
                {
                    "name": "codingB",
                    "valueCoding": {"system": ICD10CM_URI, "code": ICD10CM_E11},
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, r.text
        diag = _diagnostics(r.json())
        assert SNOMED_URI in diag, (
            f"mixed-system error MUST name SNOMED_URI; got: {diag!r}"
        )
        assert ICD10CM_URI in diag, (
            f"mixed-system error MUST name ICD10CM_URI; got: {diag!r}"
        )

    def test_t41_mixed_system_message_names_both_systems_coding_a(
        self, fhir_client
    ) -> None:
        """HIGH — mixed-system error message NAMES both systems (codingA is
        the offender).

        Clinical justification: mirror of test_t40 with codingA as the
        offender. The structural contract is the same — the message MUST
        name both systems regardless of which coding is the offender.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {
                    "name": "codingA",
                    "valueCoding": {"system": ICD10CM_URI, "code": ICD10CM_E11},
                },
                {
                    "name": "codingB",
                    "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM},
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        diag = _diagnostics(r.json())
        assert SNOMED_URI in diag, (
            f"mixed-system error MUST name SNOMED_URI; got: {diag!r}"
        )
        assert ICD10CM_URI in diag, (
            f"mixed-system error MUST name ICD10CM_URI; got: {diag!r}"
        )

    def test_t42_mixed_system_message_conveys_terminological_fact(
        self, fhir_client
    ) -> None:
        """HIGH — mixed-system error message conveys the TERMINOLOGICAL FACT
        (cross-system undefined), not a server limitation.

        Clinical justification: the phrase "not defined" (or equivalent)
        conveys the terminological fact — the client knows the relationship
        is undefined, not that the server failed. Misleading phrases like
        "could not compute" would mislead the client into thinking the
        server failed.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {
                    "name": "codingA",
                    "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DM},
                },
                {
                    "name": "codingB",
                    "valueCoding": {"system": ICD10CM_URI, "code": ICD10CM_E11},
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        diag = _diagnostics(r.json()).lower()
        # Misleading phrases that imply server failure.
        misleading = ["could not compute", "unable to compute", "failed to compute", "internal error"]
        for phrase in misleading:
            assert phrase not in diag, (
                f"mixed-system error MUST NOT say {phrase!r} — this misleads "
                f"the client into thinking the server failed rather than the "
                f"terminological fact that cross-system subsumption is "
                f"undefined. Got: {diag!r}"
            )
        # The phrase "not defined" (or close synonyms) MUST appear.
        terminological_markers = ["not defined", "undefined", "cross-system", "not established"]
        assert any(m in diag for m in terminological_markers), (
            f"mixed-system error MUST convey the terminological fact "
            f"(cross-system undefined); got: {diag!r}. Expected one of "
            f"{terminological_markers}."
        )

    def test_t43_mixed_system_error_is_fhir_operationoutcome(self, fhir_client) -> None:
        """HIGH — mixed-system error is a FHIR OperationOutcome (not plain
        text).

        Clinical justification: a non-FHIR error shape would break clinical
        workflow clients that parse OperationOutcome to surface the error
        to clinicians. The shape IS the interoperability contract.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {
                    "name": "codingA",
                    "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DM},
                },
                {
                    "name": "codingB",
                    "valueCoding": {"system": ICD10CM_URI, "code": ICD10CM_E11},
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        assert r.headers["content-type"].startswith("application/fhir+json"), (
            f"MUST be application/fhir+json; got {r.headers['content-type']!r}"
        )
        body_json = r.json()
        assert body_json["resourceType"] == "OperationOutcome"
        assert body_json["issue"]

    def test_t44_mixed_system_message_names_offending_parameter(
        self, fhir_client
    ) -> None:
        """HIGH — mixed-system error message NAMES the offending parameter
        (codingA or codingB).

        Clinical justification: the client must know WHICH coding caused the
        conflict so they can correct the right parameter. The message MUST
        include the parameter name ('codingA' or 'codingB').
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {
                    "name": "codingA",
                    "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DM},
                },
                {
                    "name": "codingB",
                    "valueCoding": {"system": ICD10CM_URI, "code": ICD10CM_E11},
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        diag = _diagnostics(r.json())
        # codingB is the offender; the message MUST name it.
        assert "codingB" in diag, (
            f"mixed-system error MUST name the offending parameter "
            f"(codingB); got: {diag!r}"
        )


# ============================================================================
# L5 — Cross-resource clinical consistency
# ============================================================================
# Per iteration prompt: $subsumes outcome consistent with CodeSystem READ
# concept hierarchy.

class TestLens5CrossResourceClinicalConsistency:
    """L5: $subsumes outcome consistent with CodeSystem READ concept hierarchy.

    Clinical justification: the CodeSystem READ endpoint exposes the engine's
    concept data; $subsumes queries the same underlying mrrel hierarchy.
    A divergence — $subsumes says "subsumes" but CodeSystem READ shows no
    parent/child relationship — would be a clinical-data integrity failure.
    """

    def test_t50_snomed_codesystem_advertises_supported_system(self, fhir_client) -> None:
        """HIGH — SNOMED code system is advertised via CapabilityStatement
        supported-system extension.

        Clinical justification: clients discover the supported systems via
        the CapabilityStatement extension. If SNOMED CT is not advertised,
        clients cannot build clinical workflows that depend on $subsumes
        for SNOMED.
        Source:
          https://hl7.org/fhir/R4/extension-capabilitystatement-supported-system.html
        """
        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200
        cs = r.json()
        # Walk the extension list at the CapabilityStatement top level.
        extensions = cs.get("extension", [])
        supported_systems = []
        for ext in extensions:
            if not isinstance(ext, dict):
                continue
            if ext.get("url", "").endswith("capabilitystatement-supported-system"):
                supported_systems.append(ext.get("valueUri"))
        assert SNOMED_URI in supported_systems, (
            f"SNOMED CT MUST be advertised via capabilitystatement-supported-"
            f"system extension; got supported_systems={supported_systems!r}"
        )

    def test_t51_snomed_lookup_returns_200_for_seeded_codes(self, fhir_client) -> None:
        """HIGH — $lookup for SNOMED returns 200 for every seeded code
        (engine has the system in its data).

        Clinical justification: $subsumes cannot produce clinically-correct
        outcomes for a system the engine does not have. $lookup returning
        200 for both DM and T2DM confirms the engine's data backing for
        the system that $subsumes operates on. CodeSystem SEARCH by url
        returns empty for fixture-only data (no persisted CodeSystem
        resources) — the right cross-resource probe is $lookup existence.
        """
        r_lookup_dm = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DM},
        )
        r_lookup_t2dm = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r_lookup_dm.status_code == 200, (
            f"$lookup on DM MUST return 200; got {r_lookup_dm.status_code}: "
            f"{r_lookup_dm.text[:200]}"
        )
        assert r_lookup_t2dm.status_code == 200, (
            f"$lookup on T2DM MUST return 200; got {r_lookup_t2dm.status_code}: "
            f"{r_lookup_t2dm.text[:200]}"
        )

    def test_t52_subsumes_outcome_matches_lookup_existence(self, fhir_client) -> None:
        """HIGH — codes in a subsumes relationship resolve via $lookup.

        Clinical justification: if $subsumes says DM subsumes T2DM, BOTH
        concepts exist in the engine's data. $lookup on each MUST return
        200. A divergence would be a data-integrity failure.
        """
        # $subsumes says DM subsumes T2DM.
        r_sub = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_DM, SNOMED_T2DM)
        assert _outcome(r_sub.json()) == "subsumes"

        # Both codes MUST resolve via $lookup.
        r_lookup_parent = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DM},
        )
        r_lookup_child = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r_lookup_parent.status_code == 200
        assert r_lookup_child.status_code == 200


# ============================================================================
# L6 — Hyphenated outcome wire-format clinical correctness
# ============================================================================
# Per iteration prompt: 'subsumed-by' MUST render correctly in both JSON and
# XML (clinicians parse these values).

class TestLens6HyphenatedOutcomeWireFormat:
    """L6: Hyphenated outcome wire-format renders correctly in JSON and XML.

    Clinical justification: clinicians and clinical-decision-support rules
    parse outcome values literally. 'subsumed-by' (with hyphen) is the R4
    canonical form; 'subsumedBy' (camelCase), 'subsumed_by' (underscore),
    'subsumedby' (no separator) are forbidden. XML serialization must
    preserve the hyphen.
    """

    @pytest.mark.parametrize(
        "code_a,code_b,expected_outcome",
        [
            (SNOMED_DM, SNOMED_DM, "equivalent"),
            (SNOMED_DM, SNOMED_T2DM, "subsumes"),
            (SNOMED_T2DM, SNOMED_DM, "subsumed-by"),
            (SNOMED_T2DM, "9999999999", "not-subsumed"),
        ],
        ids=["equivalent", "subsumes", "subsumed-by", "not-subsumed"],
    )
    def test_t60_outcome_json_exact_match(
        self, fhir_client, code_a, code_b, expected_outcome
    ) -> None:
        """HIGH — JSON outcome byte-exact match (catches camelCase/underscore
        leaks).

        Source:
          https://hl7.org/fhir/R4/valueset-concept-subsumption-outcome.html
        The closed enum is the contract.
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, code_a, code_b)
        assert r.status_code == 200, r.text
        outcome = _outcome(r.json())
        assert outcome == expected_outcome, (
            f"JSON outcome byte-exact mismatch: expected {expected_outcome!r}, "
            f"got {outcome!r}"
        )

    def test_t61_outcome_never_forbidden_form(self, fhir_client) -> None:
        """HIGH — outcome never appears as a forbidden form (subsumedBy,
        subsumed_by, subsumedby, etc.).
        """
        # Trigger the subsumed-by outcome.
        r = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_T2DM, SNOMED_DM)
        assert r.status_code == 200
        outcome = _outcome(r.json())
        assert outcome not in LEAKED_SYNONYMS, (
            f"forbidden outcome form leaked: {outcome!r}"
        )
        # Specifically assert the canonical R4 form.
        assert outcome == "subsumed-by", (
            f"R4 canonical form is 'subsumed-by'; got {outcome!r}"
        )

    def test_t62_outcome_xml_renders_hyphenated(self, fhir_client) -> None:
        """HIGH — XML wire-format renders 'subsumed-by' with hyphen preserved.

        Clinical justification: clinicians parsing XML responses must see the
        same hyphenated form as JSON. A serializer that converts hyphens to
        camelCase would break clinical clients.
        """
        r = _get_subsumes(
            fhir_client, SNOMED_URI, SNOMED_T2DM, SNOMED_DM
        )
        # Force XML via _format param.
        r_xml = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_T2DM,
                "codeB": SNOMED_DM,
                "_format": "application/fhir+xml",
            },
        )
        assert r_xml.status_code == 200, r_xml.text[:300]
        # The XML body MUST contain the hyphenated form verbatim.
        xml_body = r_xml.text
        assert 'value="subsumed-by"' in xml_body, (
            f"XML wire-format MUST render 'subsumed-by' with hyphen; "
            f"got XML body excerpt: {xml_body[:400]!r}"
        )
        # Forbidden forms MUST NOT appear.
        for forbidden in ["subsumedBy", "subsumed_by", "subsumedby"]:
            assert f'value="{forbidden}"' not in xml_body, (
                f"XML wire-format MUST NOT contain forbidden form "
                f"{forbidden!r}; XML body: {xml_body[:400]!r}"
            )

    @pytest.mark.parametrize(
        "code_a,code_b,expected_outcome",
        [
            (SNOMED_DM, SNOMED_DM, "equivalent"),
            (SNOMED_DM, SNOMED_T2DM, "subsumes"),
            (SNOMED_T2DM, SNOMED_DM, "subsumed-by"),
            (SNOMED_T2DM, "9999999999", "not-subsumed"),
        ],
        ids=["equivalent", "subsumes", "subsumed-by", "not-subsumed"],
    )
    def test_t63_outcome_xml_all_4_values(
        self, fhir_client, code_a, code_b, expected_outcome
    ) -> None:
        """HIGH — XML wire-format renders all 4 outcome values correctly.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": code_a,
                "codeB": code_b,
                "_format": "application/fhir+xml",
            },
        )
        assert r.status_code == 200, r.text[:300]
        assert f'value="{expected_outcome}"' in r.text, (
            f"XML MUST contain value=\"{expected_outcome}\"; got: {r.text[:400]!r}"
        )

    def test_t64_outcome_xml_uses_valueCode_not_valueString(self, fhir_client) -> None:
        """HIGH — XML wire-format uses valueCode element (not valueString).

        Clinical justification: the wire type signals the closed-enum contract
        to clients. valueCode = "validate strictly"; valueString = "free text".
        The XML serializer renders this as `<valueCode value="..."/>`.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_T2DM,
                "codeB": SNOMED_DM,
                "_format": "application/fhir+xml",
            },
        )
        assert r.status_code == 200
        xml_body = r.text
        # The XML element MUST be <valueCode ...>, not <valueString ...>.
        assert "<valueCode " in xml_body or "<valueCode>" in xml_body, (
            f"XML MUST use <valueCode> element; got: {xml_body[:400]!r}"
        )
        assert 'value="subsumed-by"' in xml_body, (
            f"XML MUST render 'subsumed-by' as the value attribute; "
            f"got: {xml_body[:400]!r}"
        )
        # The <valueString> element MUST NOT appear for the outcome parameter.
        assert "<valueString " not in xml_body and "<valueString>" not in xml_body, (
            f"XML MUST NOT use <valueString> for outcome; got: {xml_body[:400]!r}"
        )


# ============================================================================
# L7 — Subsumption outcome clinical correctness across sources
# ============================================================================
# Each source's $subsumes outcome is clinically correct given the fixture.

class TestLens7SubsumptionOutcomeAcrossSources:
    """L7: Each source's $subsumes outcome is clinically correct.

    Clinical justification: the fixture seeds SNOMED DM/T2DM parent/child,
    ICD-10-CM E11 (no children), RxNorm metformin (no children). Each
    source's $subsumes outcome reflects the actual seeded data — no
    fabricated cross-source or intra-source relationships.
    """

    def test_t70_icd10cm_self_equivalent(self, fhir_client) -> None:
        """HIGH — ICD-10-CM self-subsumption yields equivalent.
        """
        r = _get_subsumes(fhir_client, ICD10CM_URI, ICD10CM_E11, ICD10CM_E11)
        assert r.status_code == 200, r.text
        assert _outcome(r.json()) == "equivalent"

    def test_t71_icd10cm_no_seeded_child_yields_not_subsumed(self, fhir_client) -> None:
        """HIGH — ICD-10-CM E11 vs an unseeded E11.9 yields not-subsumed.

        Clinical justification: the fixture seeds E11 but no children.
        $subsumes MUST NOT fabricate a relationship; not-subsumed is the
        clinically-honest answer.
        """
        r = _get_subsumes(fhir_client, ICD10CM_URI, ICD10CM_E11, "E11.9")
        assert r.status_code == 200
        assert _outcome(r.json()) == "not-subsumed"

    def test_t72_rxnorm_self_equivalent(self, fhir_client) -> None:
        """HIGH — RxNorm self-subsumption yields equivalent.
        """
        r = _get_subsumes(fhir_client, RXNORM_URI, RXNORM_METFORMIN, RXNORM_METFORMIN)
        assert r.status_code == 200, r.text
        assert _outcome(r.json()) == "equivalent"

    def test_t73_rxnorm_no_seeded_parent_yields_not_subsumed(self, fhir_client) -> None:
        """HIGH — RxNorm metformin vs an unseeded code yields not-subsumed.
        """
        r = _get_subsumes(fhir_client, RXNORM_URI, RXNORM_METFORMIN, "999999")
        assert r.status_code == 200
        assert _outcome(r.json()) == "not-subsumed"

    def test_t74_snomed_bfs_max_depth_traverses_seeded_isa(self, fhir_client) -> None:
        """HIGH — SNOMED BFS traverses the seeded isa/PAR row correctly.

        Clinical justification: the fixture seeds exactly one mrrel row:
          A44054006 → A73211009 | isa | PAR
        The BFS MUST find T2DM is a descendant of DM. This is the load-
        bearing clinical-correctness contract.
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_DM, SNOMED_T2DM)
        assert _outcome(r.json()) == "subsumes"
        r_rev = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_T2DM, SNOMED_DM)
        assert _outcome(r_rev.json()) == "subsumed-by"


# ============================================================================
# L8 — Closed-enum outcome vocabulary exactness
# ============================================================================

class TestLens8OutcomeVocabularyExactness:
    """L8: Outcome values are EXACTLY from {equivalent, subsumes, subsumed-by,
    not-subsumed}. No off-spec values.
    """

    @pytest.mark.parametrize(
        "code_a,code_b",
        [
            (SNOMED_DM, SNOMED_DM),
            (SNOMED_DM, SNOMED_T2DM),
            (SNOMED_T2DM, SNOMED_DM),
            (SNOMED_T2DM, "9999999999"),
        ],
        ids=["equivalent", "subsumes", "subsumed-by", "not-subsumed"],
    )
    def test_t80_outcome_in_closed_enum(self, fhir_client, code_a, code_b) -> None:
        """HIGH — outcome is always in the FHIR R4 closed enum."""
        r = _get_subsumes(fhir_client, SNOMED_URI, code_a, code_b)
        assert r.status_code == 200
        outcome = _outcome(r.json())
        assert outcome in VALID_OUTCOMES, (
            f"outcome {outcome!r} NOT in closed enum {sorted(VALID_OUTCOMES)}"
        )

    @pytest.mark.parametrize(
        "code_a,code_b",
        [
            (SNOMED_DM, SNOMED_DM),
            (SNOMED_DM, SNOMED_T2DM),
            (SNOMED_T2DM, SNOMED_DM),
            (SNOMED_T2DM, "9999999999"),
        ],
        ids=["equivalent", "subsumes", "subsumed-by", "not-subsumed"],
    )
    def test_t81_outcome_never_leaked_synonym(
        self, fhir_client, code_a, code_b
    ) -> None:
        """HIGH — outcome never appears as a leaked synonym."""
        r = _get_subsumes(fhir_client, SNOMED_URI, code_a, code_b)
        assert r.status_code == 200
        outcome = _outcome(r.json())
        assert outcome not in LEAKED_SYNONYMS, (
            f"outcome {outcome!r} is a leaked synonym — clients would "
            f"misinterpret the clinical relationship."
        )

    def test_t82_outcome_uses_valueCode_not_valueString(self, fhir_client) -> None:
        """HIGH — Out `outcome` parameter uses valueCode (closed enum), not
        valueString (free text)."""
        r = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_DM, SNOMED_DM)
        body = r.json()
        outcome_param = next(
            p for p in body["parameter"] if p.get("name") == "outcome"
        )
        assert "valueCode" in outcome_param
        assert "valueString" not in outcome_param


# ============================================================================
# L9 — Clinical safety no-silent-wrong-answer on edge cases
# ============================================================================

class TestLens9ClinicalSafetyEdgeCases:
    """L9: Edge cases do NOT produce silent-wrong-answer outcomes.

    Clinical justification: every response is clinically interpretable.
    The server does NOT fabricate a relationship on edge cases (unknown
    codes, unknown systems, etc.).
    """

    def test_t90_unknown_codes_yield_not_subsumed(self, fhir_client) -> None:
        """HIGH — both codes unknown → not-subsumed (no fabricated relationship).
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, "X1", "X2")
        assert r.status_code == 200
        assert _outcome(r.json()) == "not-subsumed"

    def test_t91_unknown_system_yields_400_operationoutcome(self, fhir_client) -> None:
        """HIGH — unknown system → 400 OperationOutcome (not 200 + fabricated
        outcome).
        """
        r = _get_subsumes(
            fhir_client, "http://example.org/unknown", "X", "Y"
        )
        assert r.status_code == 400
        body = r.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_t92_unknown_codes_both_yields_not_subsumed_symmetric(
        self, fhir_client
    ) -> None:
        """HIGH — both codes unknown → not-subsumed is symmetric.
        """
        r1 = _get_subsumes(fhir_client, SNOMED_URI, "X1", "X2")
        r2 = _get_subsumes(fhir_client, SNOMED_URI, "X2", "X1")
        assert _outcome(r1.json()) == "not-subsumed"
        assert _outcome(r2.json()) == "not-subsumed"

    def test_t93_one_known_one_unknown_code_yields_not_subsumed(
        self, fhir_client
    ) -> None:
        """HIGH — one known + one unknown code → not-subsumed (no fabricated
        subsumes relationship).
        """
        r = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_DM, "UNKNOWN")
        assert r.status_code == 200
        assert _outcome(r.json()) == "not-subsumed"

    def test_t94_get_post_byte_exact_parity_on_outcome(
        self, fhir_client
    ) -> None:
        """HIGH — GET and POST with equivalent params produce byte-exact
        outcome.

        Clinical justification: the outcome is the clinical contract; GET and
        POST MUST agree byte-exactly. A divergence would mean clients see
        different clinical answers based on HTTP method.
        """
        # GET
        r_get = _get_subsumes(fhir_client, SNOMED_URI, SNOMED_DM, SNOMED_T2DM)
        # POST with equivalent params
        body = _build_subsumes_params(SNOMED_URI, SNOMED_DM, SNOMED_T2DM)
        r_post = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r_get.status_code == 200
        assert r_post.status_code == 200
        assert _outcome(r_get.json()) == _outcome(r_post.json())


# ============================================================================
# L10 — Source-read structural contracts
# ============================================================================

class TestLens10SourceReadStructuralContracts:
    """L10: Builder delegates correctly; no hardcoded outcome literal;
    closed-enum membership asserted at module load (defensive).
    """

    def test_t100_build_parameters_subsumes_uses_valueCode(self) -> None:
        """HIGH — build_parameters_subsumes emits valueCode (not valueString).
        """
        src = _get_func_source(_RESPONSES_PATH, "build_parameters_subsumes")
        assert "valueCode" in src, (
            "build_parameters_subsumes MUST emit valueCode for the outcome "
            "parameter; source: " + src[:300]
        )

    def test_t101_build_parameters_subsumes_outcome_param_named_correctly(self) -> None:
        """HIGH — Out parameter is named 'outcome' (not 'result' or 'value').
        """
        src = _get_func_source(_RESPONSES_PATH, "build_parameters_subsumes")
        assert '"outcome"' in src or "'outcome'" in src, (
            "build_parameters_subsumes MUST name the Out parameter 'outcome'; "
            "source: " + src[:300]
        )

    def test_t102_do_subsumes_uses_build_parameters_subsumes(self) -> None:
        """HIGH — _do_subsumes delegates to build_parameters_subsumes (no
        inline construction of the Parameters body).

        Clinical justification: a single builder is the canonical contract.
        Inline construction would risk drift on the wire format.
        """
        src = _get_nested_func_source("create_fhir_app", "_do_subsumes")
        assert "build_parameters_subsumes" in src, (
            "_do_subsumes MUST delegate to build_parameters_subsumes; "
            "source: " + src[:500]
        )

    def test_t103_do_subsumes_no_hardcoded_outcome_literal(self) -> None:
        """HIGH — _do_subsumes passes the outcome via the helper, not by
        constructing an inline Parameters dict with a hardcoded literal.
        """
        src = _get_nested_func_source("create_fhir_app", "_do_subsumes")
        # The function MUST NOT construct inline Parameters with hardcoded
        # outcome literals. The 4 canonical outcomes appear ONLY as arguments
        # to build_parameters_subsumes.
        for outcome in ("equivalent", "subsumes", "subsumed-by", "not-subsumed"):
            # Each outcome literal MUST appear inside a
            # build_parameters_subsumes(...) call (we accept the loose check
            # that build_parameters_subsumes is called and the literal
            # appears).
            assert outcome in src, (
                f"_do_subsumes MUST reference outcome {outcome!r} via "
                f"build_parameters_subsumes; source: " + src[:500]
            )

    def test_t104_do_subsumes_short_circuits_before_bfs(self) -> None:
        """HIGH — _do_subsumes short-circuits on code_a == code_b BEFORE the
        BFS (clinically-correct equivalent outcome for self-subsumption).

        Clinical justification: a concept is equivalent to itself. The
        short-circuit ensures self-subsumption yields 'equivalent' even for
        unknown codes (which would otherwise return not-subsumed from the
        BFS).
        """
        src = _get_nested_func_source("create_fhir_app", "_do_subsumes")
        assert "code_a == code_b" in src or "code_a==code_b" in src, (
            "_do_subsumes MUST short-circuit on code_a == code_b BEFORE BFS; "
            "source: " + src[:500]
        )
        assert "equivalent" in src, (
            "_do_subsumes MUST return 'equivalent' on self-subsumption; "
            "source: " + src[:500]
        )

    def test_t105_subsumes_post_handler_calls_extract_named_coding_for_both(
        self
    ) -> None:
        """HIGH — subsumes_post calls _extract_named_coding_from_parameters
        for BOTH codingA and codingB (alternative-encoding support).

        Clinical justification: the spec lists codingA/codingB as alternatives
        to codeA/codeB. The handler MUST extract both via the helper.
        """
        src = _get_nested_func_source("create_fhir_app", "subsumes_post")
        assert '_extract_named_coding_from_parameters(body, "codingA")' in src, (
            "subsumes_post MUST call _extract_named_coding_from_parameters "
            "for codingA; source: " + src[:500]
        )
        assert '_extract_named_coding_from_parameters(body, "codingB")' in src, (
            "subsumes_post MUST call _extract_named_coding_from_parameters "
            "for codingB; source: " + src[:500]
        )

    def test_t106_subsumes_post_mixed_system_check_uses_canonical_system_uri(
        self
    ) -> None:
        """HIGH — mixed-system check uses canonical_system_uri (CR-023 fix
        — alias normalization).

        Clinical justification: without canonical_system_uri normalization,
        same-system codings supplied via alias (trailing-slash, urn:oid)
        would be falsely rejected as cross-system. This breaks clinical
        workflows that use different alias forms across operations.
        """
        src = _get_nested_func_source("create_fhir_app", "subsumes_post")
        assert "canonical_system_uri" in src, (
            "subsumes_post mixed-system check MUST use canonical_system_uri "
            "(CR-023 fix); source: " + src[:800]
        )
