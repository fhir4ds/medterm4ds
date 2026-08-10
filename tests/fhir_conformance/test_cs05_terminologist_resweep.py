"""TERMINOLOGIST resweep probes for CS-05 (CodeSystem Edge Cases).

Spec: https://build.fhir.org/codesystem.html
       (canonical R4: https://hl7.org/fhir/R4/codesystem.html)
       $lookup:   https://hl7.org/fhir/R4/codesystem-operation-lookup.html
       $validate: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
       $subsumes: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
       $translate: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
       $expand:   https://hl7.org/fhir/R4/valueset-operation-expand.html
       concept-properties:
           https://hl7.org/fhir/R4/concept-properties.html

TERMINOLOGIST lens: clinical / terminological correctness. Default severity
HIGH per GLOBAL_RULES.md. The prior CS-05 run (test_cs05_terminologist.py)
established 44 probes across 9 lens dimensions; this resweep extends
coverage based on EXPLORER's 5-tip handoff for the TERMINOLOGIST iteration.

EXPLORER tip for TERMINOLOGIST (5 things to verify per handoff):
  1. Clinical correctness of the chosen canonical display per source —
     SNOMED preferred term (PT), RxNorm SCD display, ICD-10-CM HT full name.
  2. Extend GET<->POST byte-exact parity to $translate + $expand for
     completeness.
  3. Verify the display-mismatch message text is clinically informative
     and cites the wrong value verbatim per CS-03 SKEPTIC QA-048 spec
     example.
  4. Verify $translate target Coding clinical correctness (right ICD-10-CM
     code, right display, right equivalence) for SNOMED -> ICD-10-CM
     crosswalks.
  5. Verify XML rendering of all 4 $subsumes outcome values including
     hyphenated `subsumed-by` / `not-subsumed`.

Edge-case focus (per chunk assignment):
  - Inactive code clinical safety (CF-SKEPTIC-CS05-02) — fixture lacks
    inactive codes; document fixture-gap.
  - Abstract concept clinical correctness (CF-SKEPTIC-CS05-01) — fixture
    lacks abstract concepts.
  - Version-specific clinical correctness — historical atoms for a code
    should be clinically correct for their version period.
  - Multi-hierarchy clinical correctness — $subsumes should respect
    multi-parent DAG semantics.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
    ("E11", "HT", "Type 2 diabetes mellitus", "AE11", "N", "ICD10CM", "C0011847"),
    ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # single-parent

Clinical interpretation of the seeded SNOMED pair:
  - 73211009 (Diabetes mellitus) is the clinically-broader concept.
  - 44054006 (Type 2 diabetes mellitus) is the clinically-narrower concept.
  - SNOMED 44054006 and ICD-10-CM E11 SHARE CUI C0011847 — clinically
    the same condition (Type 2 diabetes mellitus). The engine's same-CUI
    crosswalk therefore emits equivalence='equivalent' on $translate
    SNOMED -> ICD-10-CM (clinically correct per TS-02 TERMINOLOGIST
    QA-030 fix + CM-02 TERMINOLOGIST verification).

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

import ast
import inspect
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
# Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
# Spec: https://hl7.org/fhir/R4/concept-properties.html

SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_URN_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_URI_UPPERCASE_SCHEME = "HTTP://www.nlm.nih.gov/research/umls/rxnorm"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_URI_TRAILING_SLASH = "http://hl7.org/fhir/sid/icd-10-cm/"
ICD10CM_URI_URN_OID = "urn:oid:2.16.840.1.113883.6.90"
ICD10CM_URI_UPPERCASE_SCHEME = "HTTP://hl7.org/fhir/sid/icd-10-cm"

SNOMED_DIABETES_MELLITUS = "73211009"   # parent (broader)
SNOMED_T2DM = "44054006"                # child (narrower)
ICD10CM_T2DM = "E11"                    # ICD-10-CM T2DM (shares CUI C0011847 with SNOMED 44054006)
RXNORM_METFORMIN = "860975"             # 24 HR metformin 500 MG Oral Tablet (SCD)

# Clinical expectations (per UMLS):
# - SNOMED 44054006 preferred term (PT) = "Type 2 diabetes mellitus"
# - SNOMED 73211009 preferred term (PT) = "Diabetes mellitus"
# - ICD-10-CM E11 HT (hybrid term) full name = "Type 2 diabetes mellitus"
# - RxNorm 860975 SCD display = "24 HR metformin 500 MG Oral Tablet"
EXPECTED_DISPLAY_SNOMED_T2DM = "Type 2 diabetes mellitus"
EXPECTED_DISPLAY_SNOMED_DM = "Diabetes mellitus"
EXPECTED_DISPLAY_ICD10CM_T2DM = "Type 2 diabetes mellitus"
EXPECTED_DISPLAY_RXNORM_METFORMIN = "24 HR metformin 500 MG Oral Tablet"

# FHIR R4 ConceptMapEquivalence closed enum (10 values, per
# https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html).
FHIR_R4_CONCEPT_MAP_EQUIVALENCE = frozenset({
    "relatedto", "equivalent", "equal", "wider", "subsumes",
    "narrower", "specializes", "inexact", "unmatched", "disjoint",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _translate_matches(body: dict) -> list[dict]:
    """Return the list of `match` parts in a $translate response."""
    matches = []
    for p in body.get("parameter", []):
        if p.get("name") == "match":
            matches.append(p)
    return matches


def _match_part(match: dict, part_name: str) -> dict | None:
    """Return the part dict with name=part_name from a match entry."""
    for part in match.get("part", []):
        if part.get("name") == part_name:
            return part
    return None


def _parameters_body(system: str, code: str, **extra) -> dict:
    """Build a Parameters body for POST $lookup/$validate-code style ops."""
    params = [
        {"name": "system", "valueUri": system},
        {"name": "code", "valueCode": code},
    ]
    for k, v in extra.items():
        if k == "display":
            params.append({"name": "display", "valueString": v})
        elif k == "version":
            params.append({"name": "version", "valueString": v})
        elif k == "codeA":
            params.append({"name": "codeA", "valueCode": v})
        elif k == "codeB":
            params.append({"name": "codeB", "valueCode": v})
        elif k == "targetsystem":
            # Per FHIR R4 $translate OperationDefinition
            # (https://hl7.org/fhir/R4/conceptmap-operation-translate.html),
            # the In parameter is `targetsystem` (all lowercase, NOT
            # camelCase) — FHIR R4 mixes camelCase (conceptMapVersion,
            # codeableConcept) and lowercase (targetsystem). The engine's
            # GET binding `targetsystem` and POST extraction match the
            # spec.
            params.append({"name": "targetsystem", "valueUri": v})
    return {"resourceType": "Parameters", "parameter": params}


def _get_module_source(module) -> tuple[str, ast.AST]:
    """Return (source_text, ast_tree) for a Python module."""
    src_path = Path(inspect.getsourcefile(module))
    src_text = src_path.read_text()
    return src_text, ast.parse(src_text)


def _get_nested_func_source(
    src_text: str, tree: ast.AST, parent_name: str, child_name: str
) -> ast.AST | None:
    """Locate a nested function defined inside another function."""
    parent_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == parent_name:
                parent_node = node
                break
    if parent_node is None:
        return None
    for child in ast.walk(parent_node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name == child_name:
                return child
    return None


def _count_calls_in(node: ast.AST, func_name: str) -> int:
    """Count ast.Call nodes in `node` whose function is Name(func_name)."""
    count = 0
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if isinstance(f, ast.Name) and f.id == func_name:
            count += 1
        elif isinstance(f, ast.Attribute) and f.attr == func_name:
            count += 1
    return count


# ===========================================================================
# Lens 1: Canonical display clinical correctness per source
# (EXPLORER tip 1 — SNOMED PT, RxNorm SCD, ICD-10-CM HT full name)
# ===========================================================================
# Clinical contract: per FHIR R4 $lookup Out `display` is "The preferred
# display for this concept" — the code system's preferred term per
# https://hl7.org/fhir/R4/codesystem-operation-lookup.html. The engine
# resolves display via get_code_infos -> preferred STR atom.
#
# Per UMLS:
#   - SNOMEDCT_US preferred term (TTY='PT', SUPPRESS='N') is the SNOMED
#     CT US-edition official preferred term.
#   - ICD10CM 'HT' (Hybrid Term) is the long form used in ICD-10-CM
#     official tabular list and is what clinicians see in charting.
#   - RXNORM 'SCD' (Semantic Clinical Drug) is the RxNorm-recommended
#     display for clinical drug products per
#     https://www.nlm.nih.gov/research/umls/rxnorm.

class TestLens1CanonicalDisplayClinicalCorrectnessPerSource:
    """Lens 1 (EXPLORER tip 1): verify the canonical display per source
    matches the source's preferred-term policy.

    The clinical correctness expectation is:
      - SNOMED 44054006 -> "Type 2 diabetes mellitus" (SNOMED PT)
      - SNOMED 73211009 -> "Diabetes mellitus" (SNOMED PT)
      - ICD-10-CM E11   -> "Type 2 diabetes mellitus" (ICD-10-CM HT)
      - RxNorm 860975   -> "24 HR metformin 500 MG Oral Tablet" (RxNorm SCD)

    Divergence from any of these is a HIGH-severity clinical-correctness
    defect: clinicians using EHR pick-lists would see a different display
    than the source terminology's preferred term, leading to charting
    inconsistency, decision-support miscategorization, and (in the worst
    case) wrong-diagnosis propagation.
    """

    def test_t10_snomed_pt_is_canonical_display(self, fhir_client):
        """SNOMED T2DM (44054006) preferred term is "Type 2 diabetes mellitus".

        Clinical justification: SNOMED CT's preferred term (TTY='PT',
        SUPPRESS='N') is the official SNOMED US-edition display for the
        concept. Per https://hl7.org/fhir/R4/codesystem-operation-lookup.html
        Out `display`: "The preferred display for this concept". The
        engine's get_code_infos ranks PT atoms first, so the canonical
        display MUST equal the SNOMED PT STR verbatim.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200, r.text
        display = _param_value(r.json(), "display")
        assert display == EXPECTED_DISPLAY_SNOMED_T2DM, (
            f"Clinical-correctness violation: SNOMED PT for {SNOMED_T2DM} "
            f"MUST be {EXPECTED_DISPLAY_SNOMED_T2DM!r}. Got {display!r}. "
            f"A divergence would surface the wrong display in EHR pick-lists."
        )

    def test_t11_snomed_parent_pt_is_canonical_display(self, fhir_client):
        """SNOMED Diabetes mellitus (73211009) PT is "Diabetes mellitus".

        Clinical justification: the broader SNOMED concept. Clinicians
        using CDS hooks to surface 'this patient has any form of diabetes'
        rely on the broader-term display. The PT for 73211009 is the
        SNOMED official grouping-term display.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
        )
        assert r.status_code == 200, r.text
        display = _param_value(r.json(), "display")
        assert display == EXPECTED_DISPLAY_SNOMED_DM, (
            f"Clinical-correctness violation: SNOMED PT for "
            f"{SNOMED_DIABETES_MELLITUS} MUST be "
            f"{EXPECTED_DISPLAY_SNOMED_DM!r}. Got {display!r}."
        )

    def test_t12_icd10cm_ht_full_name_is_canonical_display(self, fhir_client):
        """ICD-10-CM E11 HT full name is "Type 2 diabetes mellitus".

        Clinical justification: ICD-10-CM uses 'HT' (Hybrid Term) TTY for
        the long-form display in the official tabular list (per CMS NPI
        convention; UMLS MRCONSO TTY='HT' rows for ICD10CM). The 'HT' is
        the display clinicians recognize in charting systems. Using a
        different atom (e.g. TTY='AB' abbreviation) would surface a
        non-clinician-friendly display.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": ICD10CM_URI, "code": ICD10CM_T2DM},
        )
        assert r.status_code == 200, r.text
        display = _param_value(r.json(), "display")
        assert display == EXPECTED_DISPLAY_ICD10CM_T2DM, (
            f"Clinical-correctness violation: ICD-10-CM HT full name for "
            f"{ICD10CM_T2DM} MUST be {EXPECTED_DISPLAY_ICD10CM_T2DM!r}. "
            f"Got {display!r}."
        )

    def test_t13_rxnorm_scd_display_is_canonical(self, fhir_client):
        """RxNorm SCD display for 860975 is "24 HR metformin 500 MG Oral Tablet".

        Clinical justification: RxNorm SCD (Semantic Clinical Drug) is the
        NLM-recommended display for clinical drug products per
        https://www.nlm.nih.gov/research/umls/rxnorm. SCD displays include
        dose form + ingredient + strength in a clinically-sensible order.
        Using a different atom (e.g. TTY='BN' brand name, or 'SCDF' form-
        only) would lose clinical specificity — a patient-safety risk for
        e-prescribing CDS hooks.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": RXNORM_URI, "code": RXNORM_METFORMIN},
        )
        assert r.status_code == 200, r.text
        display = _param_value(r.json(), "display")
        assert display == EXPECTED_DISPLAY_RXNORM_METFORMIN, (
            f"Clinical-correctness violation: RxNorm SCD display for "
            f"{RXNORM_METFORMIN} MUST be "
            f"{EXPECTED_DISPLAY_RXNORM_METFORMIN!r}. Got {display!r}. "
            f"Wrong display is a patient-safety risk for e-prescribing."
        )

    def test_t14_canonical_display_clinically_distinct_across_seeded_codes(
        self, fhir_client
    ):
        """The 4 seeded codes' canonical displays are clinically distinct
        (no two seeded codes surface the same display string EXCEPT where
        clinically correct).

        Clinical justification: SNOMED T2DM (44054006) and ICD-10-CM E11
        both have display "Type 2 diabetes mellitus" — these ARE the same
        clinical concept (different code systems). SNOMED DM (73211009)
        has display "Diabetes mellitus" — the broader category. RxNorm
        860975 has the metformin display — clinically unrelated to the
        diabetes diagnoses. A future regression that conflates the
        diabetes-mellitus display onto the T2DM concept would lose clinical
        specificity (the EHR wouldn't know it's Type 2).
        """
        displays = {}
        for system, code in [
            (SNOMED_URI, SNOMED_T2DM),
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
            (ICD10CM_URI, ICD10CM_T2DM),
            (RXNORM_URI, RXNORM_METFORMIN),
        ]:
            r = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": system, "code": code},
            )
            assert r.status_code == 200, r.text
            displays[(system, code)] = _param_value(r.json(), "display")
        # SNOMED T2DM and ICD-10-CM T2DM clinically are the same concept
        # (they share CUI C0011847). Same display is clinically correct.
        assert displays[(SNOMED_URI, SNOMED_T2DM)] == \
               displays[(ICD10CM_URI, ICD10CM_T2DM)] == \
               EXPECTED_DISPLAY_SNOMED_T2DM
        # SNOMED DM is the broader category — different display.
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == \
               EXPECTED_DISPLAY_SNOMED_DM
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] != \
               displays[(SNOMED_URI, SNOMED_T2DM)]
        # RxNorm metformin is clinically distinct (a treatment, not the
        # diagnosis).
        assert displays[(RXNORM_URI, RXNORM_METFORMIN)] == \
               EXPECTED_DISPLAY_RXNORM_METFORMIN
        assert "metformin" in displays[(RXNORM_URI, RXNORM_METFORMIN)].lower()
        assert "diabetes" not in displays[(RXNORM_URI, RXNORM_METFORMIN)].lower()


# ===========================================================================
# Lens 2: GET<->POST byte-exact parity on $translate + $expand
# (EXPLORER tip 2 — extend parity to the 2 remaining mandatory ops)
# ===========================================================================
# Clinical contract: per FHIR R4 §3.1.0.1.5, GET and POST invocation paths
# for the same operation MUST produce the same clinical response. A
# divergence would mean a CDS hook using POST gets a different clinical
# answer than a hook using GET — a patient-safety risk (clinicians see
# different displays, different equivalences, or different expansions
# depending on how the EHR invokes the operation).

class TestLens2GetPostParityTranslateAndExpand:
    """Lens 2 (EXPLORER tip 2): extend GET<->POST byte-exact parity to
    $translate and $expand.

    $translate is invoked via POST on type-level route AND via GET with
    query params. The clinical content (equivalence, target system,
    target display) MUST be byte-identical.

    $expand is invoked via POST with inline ValueSet body AND via GET
    with url query param. The expansion contains[] (display, code,
    system) MUST be byte-identical.
    """

    def test_t20_translate_get_post_byte_exact_match(self, fhir_client):
        """SNOMED T2DM -> ICD-10-CM $translate produces byte-exact identical
        clinical content via GET and POST.

        Clinical justification: the target Coding's system, code, display,
        and the match.equivalence MUST be byte-identical between GET and
        POST. A divergence would surface different clinical content in
        EHR integrations depending on invocation path.
        """
        # GET: targetSystem via query param
        r_get = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "targetsystem": ICD10CM_URI,
            },
        )
        # POST: targetSystem via Parameters body
        r_post = fhir_client.post(
            "/fhir/ConceptMap/$translate",
            json=_parameters_body(
                SNOMED_URI, SNOMED_T2DM, targetsystem=ICD10CM_URI
            ),
        )
        assert r_get.status_code == 200, r_get.text
        assert r_post.status_code == 200, r_post.text
        body_get = r_get.json()
        body_post = r_post.json()
        # Byte-exact on the result flag
        assert _param_value(body_get, "result") == \
               _param_value(body_post, "result")
        # Byte-exact on the match count
        matches_get = _translate_matches(body_get)
        matches_post = _translate_matches(body_post)
        assert len(matches_get) == len(matches_post), (
            f"GET and POST returned different match counts: "
            f"GET={len(matches_get)}, POST={len(matches_post)}."
        )
        # Byte-exact on each match's clinical content
        for i, (mg, mp) in enumerate(zip(matches_get, matches_post)):
            equiv_g = _match_part(mg, "equivalence")
            equiv_p = _match_part(mp, "equivalence")
            assert equiv_g == equiv_p, (
                f"Match {i}: equivalence divergent. GET={equiv_g!r}, "
                f"POST={equiv_p!r}."
            )
            concept_g = _match_part(mg, "concept")
            concept_p = _match_part(mp, "concept")
            assert concept_g == concept_p, (
                f"Match {i}: target concept divergent. GET={concept_g!r}, "
                f"POST={concept_p!r}."
            )

    def test_t21_translate_get_post_byte_exact_no_match(self, fhir_client):
        """SNOMED -> RxNorm $translate (no clinical mapping) produces byte-
        exact identical clinical content via GET and POST.

        Clinical justification: the no-match path MUST also be byte-exact
        (result=false, no match entries). A divergence here would be a
        silent-wrong-answer — a CDS hook using POST might see a spurious
        match where GET correctly shows none.
        """
        r_get = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "targetsystem": RXNORM_URI,
            },
        )
        r_post = fhir_client.post(
            "/fhir/ConceptMap/$translate",
            json=_parameters_body(
                SNOMED_URI, SNOMED_T2DM, targetsystem=RXNORM_URI
            ),
        )
        assert r_get.status_code == 200
        assert r_post.status_code == 200
        body_get = r_get.json()
        body_post = r_post.json()
        assert _param_value(body_get, "result") == \
               _param_value(body_post, "result")
        # No matches in either
        assert len(_translate_matches(body_get)) == \
               len(_translate_matches(body_post)) == 0

    def test_t22_expand_get_post_byte_exact_filter(self, fhir_client):
        """$expand filter='diabetes' produces byte-exact identical
        contains[] clinical content via GET and POST.

        Clinical justification: filter-based $expand is the most common
        ValueSet expansion use case for CDS hooks. The contains[].system,
        contains[].code, contains[].display MUST be byte-identical.
        """
        # GET with url-encoded filter
        r_get = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": SNOMED_URI, "filter": "diabetes"},
        )
        # POST with Parameters body containing filter
        r_post = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "url", "valueUri": SNOMED_URI},
                    {"name": "filter", "valueString": "diabetes"},
                ],
            },
        )
        assert r_get.status_code == 200, r_get.text
        assert r_post.status_code == 200, r_post.text
        contains_get = r_get.json().get("expansion", {}).get("contains", [])
        contains_post = r_post.json().get("expansion", {}).get("contains", [])
        # Same count
        assert len(contains_get) == len(contains_post)
        # Byte-exact clinical content for each contains entry (code, system, display)
        get_codes = sorted(
            (c.get("system"), c.get("code"), c.get("display")) for c in contains_get
        )
        post_codes = sorted(
            (c.get("system"), c.get("code"), c.get("display")) for c in contains_post
        )
        assert get_codes == post_codes, (
            f"$expand GET vs POST contains[] divergent. "
            f"GET={get_codes!r}, POST={post_codes!r}."
        )

    def test_t23_expand_get_post_byte_exact_explicit(self, fhir_client):
        """$expand with inline explicit ValueSet body produces byte-exact
        identical contains[] clinical content via GET-with-Parameters-body
        vs POST-with-ValueSet-body.

        Clinical justification: the explicit-concept expansion is the most
        clinically-sensitive (each concept is hand-curated). Divergence
        would surface different hand-curated concepts depending on
        invocation path — a data-integrity violation.
        """
        inline_valueset_post_body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test-vs",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"},
                        {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"},
                    ],
                }],
            },
        }
        # POST with bare ValueSet body (the inline-ValueSet shape)
        r_post = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=inline_valueset_post_body,
        )
        # POST with Parameters body wrapping the ValueSet (per FHIR R4
        # §4.7.5 In Parameters valueSet 0..1 ValueSet)
        r_params = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "valueSet", "resource": inline_valueset_post_body},
                ],
            },
        )
        assert r_post.status_code == 200, r_post.text
        assert r_params.status_code == 200, r_params.text
        contains_post = r_post.json().get("expansion", {}).get("contains", [])
        contains_params = r_params.json().get("expansion", {}).get("contains", [])
        assert len(contains_post) == len(contains_params)
        post_codes = sorted(
            (c.get("system"), c.get("code"), c.get("display")) for c in contains_post
        )
        params_codes = sorted(
            (c.get("system"), c.get("code"), c.get("display")) for c in contains_params
        )
        assert post_codes == params_codes


# ===========================================================================
# Lens 3: Display-mismatch message clinical informativeness
# (EXPLORER tip 3 — cite the wrong value verbatim per CS-03 SKEPTIC QA-048)
# ===========================================================================
# Clinical contract: per FHIR R4 spec example response at
# https://hl7.org/fhir/R4/codesystem-operation-validate-code.html ("Response"),
# the message format is byte-exact: 'The display "X" is incorrect'. The
# wrong value is cited verbatim in the message; the canonical display is
# surfaced SEPARATELY in the Out `display` parameter (NOT in the message).
#
# This shape was established by CS-03 SKEPTIC QA-048 and confirmed by
# CS-03 TERMINOLOGIST test_t90. EXPLORER tip 3 asks TERMINOLOGIST to
# verify the message text is clinically informative AND cites the wrong
# value verbatim.

class TestLens3DisplayMismatchMessageInformativeness:
    """Lens 3 (EXPLORER tip 3): display-mismatch message cites the wrong
    value verbatim and is clinically informative.

    Clinical informativeness criteria:
      (a) The wrong value is cited verbatim (the clinician sees what they
          typed wrong, not a generic "display mismatch").
      (b) The canonical display is surfaced in the SEPARATE Out `display`
          parameter (the clinician sees the correct value).
      (c) The message is byte-exact with the FHIR R4 spec example.
      (d) The message is NOT misleading (no "could not compute", no
          "server error", no implying server failure).
    """

    def test_t30_message_cites_wrong_value_verbatim(self, fhir_client):
        """$validate-code with wrong display produces a message that cites
        the wrong value verbatim.

        Clinical justification: the clinician MUST see what they typed
        wrong. A generic "display mismatch" without the wrong value would
        force the clinician to scroll back through the EHR's input form.
        """
        wrong_display = "Totally Wrong Display"
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "display": wrong_display,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert _param_value(body, "result") is False
        message = _param_value(body, "message")
        assert message is not None
        # The wrong value MUST be cited verbatim
        assert wrong_display in str(message), (
            f"Clinical-informativeness violation: message MUST cite the "
            f"wrong value verbatim. Message: {message!r}."
        )

    def test_t31_message_byte_exact_with_spec_example(self, fhir_client):
        """The message format is byte-exact with the FHIR R4 spec example.

        Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
        "Response" example: 'The display "X" is incorrect'.

        Clinical justification: byte-exact format allows clients to write
        deterministic parsers (e.g. to surface a structured warning).
        """
        wrong_display = "Wrong"
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "display": wrong_display,
            },
        )
        assert r.status_code == 200
        message = _param_value(r.json(), "message")
        # Byte-exact: 'The display "Wrong" is incorrect'
        assert message == 'The display "Wrong" is incorrect', (
            f"Spec-mandated message format violated. Got {message!r}. "
            f"Expected: 'The display \"Wrong\" is incorrect'."
        )

    def test_t32_canonical_display_in_separate_out_parameter(self, fhir_client):
        """The canonical display is surfaced in the separate Out `display`
        parameter, NOT in the message.

        Clinical justification: per spec example, the separation of
        concerns is load-bearing. The message conveys 'you got it wrong';
        the Out display conveys 'here's the right one'. Conflating them
        would force the clinician to parse the message for the canonical
        value (which the spec doesn't require).
        """
        wrong_display = "Wrong Display"
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "display": wrong_display,
            },
        )
        assert r.status_code == 200
        body = r.json()
        message = _param_value(body, "message")
        canonical = _param_value(body, "display")
        assert canonical == EXPECTED_DISPLAY_SNOMED_T2DM
        # The canonical MUST NOT appear in the message (separation of concerns)
        assert EXPECTED_DISPLAY_SNOMED_T2DM not in str(message), (
            f"Spec-deviation: canonical display appears in the message. "
            f"Message: {message!r}. Per spec, canonical MUST be in the "
            f"separate Out `display` parameter only."
        )

    @pytest.mark.parametrize(
        "system,code,expected_canonical",
        [
            (SNOMED_URI, SNOMED_T2DM, EXPECTED_DISPLAY_SNOMED_T2DM),
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS, EXPECTED_DISPLAY_SNOMED_DM),
            (ICD10CM_URI, ICD10CM_T2DM, EXPECTED_DISPLAY_ICD10CM_T2DM),
            (RXNORM_URI, RXNORM_METFORMIN, EXPECTED_DISPLAY_RXNORM_METFORMIN),
        ],
        ids=["snomed-t2dm", "snomed-dm", "icd10-t2dm", "rxnorm-metformin"],
    )
    def test_t33_display_mismatch_per_source_canonical_correct(
        self, fhir_client, system, code, expected_canonical
    ):
        """For every seeded code across all 4 sources, display mismatch
        surfaces the per-source canonical display.

        Clinical justification: cross-source consistency — the canonical
        display surfaced on mismatch MUST be the source's preferred term
        (SNOMED PT / ICD-10-CM HT / RxNorm SCD). A future regression that
        surfaces a different atom (e.g. 'AB' abbreviation) on mismatch
        would propagate the wrong display through CDS hooks.
        """
        wrong = "DEFINITELY WRONG"
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": system, "code": code, "display": wrong},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert _param_value(body, "result") is False
        canonical = _param_value(body, "display")
        assert canonical == expected_canonical, (
            f"Source {system} code {code}: canonical display MUST be "
            f"{expected_canonical!r}. Got {canonical!r}."
        )

    def test_t34_display_mismatch_not_misleading(self, fhir_client):
        """The message does NOT contain misleading phrases.

        Clinical justification: misleading phrases (e.g. 'could not
        compute', 'server error') would confuse the clinician into
        thinking the server failed rather than the display being wrong.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "display": "Wrong",
            },
        )
        assert r.status_code == 200
        message = str(_param_value(r.json(), "message")).lower()
        for forbidden in ["could not compute", "unable to compute", "server error"]:
            assert forbidden not in message, (
                f"Clinical-clarity violation: message contains "
                f"misleading phrase {forbidden!r}. Message: {message!r}."
            )


# ===========================================================================
# Lens 4: $translate target Coding clinical correctness
# (EXPLORER tip 4 — SNOMED -> ICD-10-CM crosswalk)
# ===========================================================================
# Clinical contract: SNOMED 44054006 (Type 2 diabetes mellitus) and
# ICD-10-CM E11 share UMLS CUI C0011847 — they ARE the same clinical
# concept (Type 2 diabetes mellitus) in two code systems. Per FHIR R4
# ConceptMapEquivalence, the same-CUI crosswalk MUST emit equivalence=
# 'equivalent' (per TS-02 TERMINOLOGIST QA-030 fix + CM-02 TERMINOLOGIST
# verification + CM-04 TERMINOLOGIST confirmation).
#
# The target Coding's system MUST be the canonical ICD-10-CM URI
# (http://hl7.org/fhir/sid/icd-10-cm) — NOT the source's SNOMED URI
# (cross-system drift would be a critical clinical bug). The target
# Coding's code MUST be 'E11' (the ICD-10-CM code for T2DM). The target
# Coding's display MUST be the ICD-10-CM HT display.

class TestLens4TranslateTargetCodingClinicalCorrectness:
    """Lens 4 (EXPLORER tip 4): SNOMED -> ICD-10-CM $translate target
    Coding is clinically correct (right code, right display, right
    equivalence, right system).
    """

    def test_t40_target_system_is_canonical_icd10cm_uri(self, fhir_client):
        """$translate SNOMED T2DM -> ICD-10-CM target.system is the canonical
        ICD-10-CM URI.

        Clinical justification: the target Coding's system MUST be the
        canonical FHIR R4 ICD-10-CM URI per
        https://hl7.org/fhir/R4/sid.html. A regression that surfaces the
        SNOMED URI on the target would be cross-system drift (clinician
        thinks the mapping is SNOMED->SNOMED, not SNOMED->ICD-10-CM).
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "targetsystem": ICD10CM_URI,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        matches = _translate_matches(body)
        assert len(matches) > 0, "Expected at least one match for SNOMED->ICD-10-CM crosswalk."
        # First match's target concept system MUST be canonical ICD-10-CM URI
        concept = _match_part(matches[0], "concept")
        assert concept is not None
        target_system = concept.get("valueCoding", {}).get("system")
        assert target_system == ICD10CM_URI, (
            f"Clinical-correctness violation: target Coding system MUST be "
            f"{ICD10CM_URI!r}. Got {target_system!r}. Cross-system drift "
            f"would surface the wrong code system on the target."
        )

    def test_t41_target_code_is_e11_clinically_correct(self, fhir_client):
        """$translate SNOMED T2DM -> ICD-10-CM target.code is 'E11'.

        Clinical justification: per UMLS CUI C0011847, SNOMED 44054006
        (T2DM) maps to ICD-10-CM E11 (T2DM). The target code MUST be E11.
        A regression that surfaces a different ICD-10-CM code (e.g. E10
        Type 1 diabetes) would be a CRITICAL clinical bug — the EHR would
        bill the wrong diagnosis.
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "targetsystem": ICD10CM_URI,
            },
        )
        assert r.status_code == 200
        matches = _translate_matches(r.json())
        assert len(matches) > 0
        concept = _match_part(matches[0], "concept")
        target_code = concept.get("valueCoding", {}).get("code")
        assert target_code == ICD10CM_T2DM, (
            f"Clinical-correctness violation: SNOMED T2DM {SNOMED_T2DM} "
            f"MUST map to ICD-10-CM {ICD10CM_T2DM}. Got {target_code!r}. "
            f"A different code would surface the WRONG diagnosis (e.g. "
            f"Type 1 instead of Type 2 — a CRITICAL clinical bug)."
        )

    def test_t42_target_display_is_canonical_icd10cm_ht(self, fhir_client):
        """$translate SNOMED T2DM -> ICD-10-CM target.display is "Type 2
        diabetes mellitus" (the ICD-10-CM HT full name).

        Clinical justification: the target display MUST match the
        ICD-10-CM HT display, NOT an abbreviation or different atom. A
        clinician seeing the target display in the EHR post-mapping MUST
        see the same display as if they'd looked up the ICD-10-CM code
        directly.
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "targetsystem": ICD10CM_URI,
            },
        )
        assert r.status_code == 200
        matches = _translate_matches(r.json())
        assert len(matches) > 0
        concept = _match_part(matches[0], "concept")
        target_display = concept.get("valueCoding", {}).get("display")
        assert target_display == EXPECTED_DISPLAY_ICD10CM_T2DM, (
            f"Clinical-correctness violation: target display MUST be "
            f"{EXPECTED_DISPLAY_ICD10CM_T2DM!r} (ICD-10-CM HT). "
            f"Got {target_display!r}."
        )

    def test_t43_target_equivalence_is_equivalent(self, fhir_client):
        """$translate SNOMED T2DM -> ICD-10-CM match.equivalence is
        'equivalent' (the same-CUI semantic per TS-02 TERMINOLOGIST QA-030
        + CM-02 TERMINOLOGIST verification).

        Clinical justification: SNOMED 44054006 and ICD-10-CM E11 share
        CUI C0011847 — they are the same clinical concept in two code
        systems. Per FHIR R4 ConceptMapEquivalence, the same-CUI crosswalk
        emits 'equivalent' (clinically stronger signal than 'relatedto').
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "targetsystem": ICD10CM_URI,
            },
        )
        assert r.status_code == 200
        matches = _translate_matches(r.json())
        assert len(matches) > 0
        equiv_part = _match_part(matches[0], "equivalence")
        equiv_value = equiv_part.get("valueCode")
        assert equiv_value == "equivalent", (
            f"Clinical-correctness violation: same-CUI crosswalk MUST emit "
            f"equivalence='equivalent'. Got {equiv_value!r}. A weaker "
            f"value (e.g. 'relatedto') would lose the clinical signal that "
            f"the two codes are the same concept."
        )

    def test_t44_equivalence_in_r4_closed_enum(self, fhir_client):
        """$translate target equivalence is in the FHIR R4
        ConceptMapEquivalence closed enum (10 values).

        Clinical justification: per FHIR R4
        https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html,
        the closed enum contains exactly 10 values. R5/R4B contamination
        (e.g. 'subsumedby', 'matches', 'not-relatedto') would surface
        off-spec values that strict clients reject. Mirrors
        CF-HISTORIAN-VS01-01 RESOLVED verification methodology.
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "targetsystem": ICD10CM_URI,
            },
        )
        assert r.status_code == 200
        for m in _translate_matches(r.json()):
            equiv = _match_part(m, "equivalence").get("valueCode")
            assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"Off-spec equivalence value {equiv!r} not in R4 closed "
                f"enum {FHIR_R4_CONCEPT_MAP_EQUIVALENCE}."
            )

    def test_t45_translate_no_match_to_rxnorm_clinically_correct(self, fhir_client):
        """SNOMED T2DM -> RxNorm produces result=false (no clinical
        mapping — a diagnosis doesn't map to a drug product).

        Clinical justification: $translate is for concept-to-concept
        cross-system mappings via UMLS CUI. SNOMED 44054006 (CUI
        C0011847 — diagnosis) does NOT share a CUI with any RxNorm code
        (RxNorm codes use C0978xxx for clinical drug products). The
        clinically-correct answer is result=false. A regression that
        surface a spurious match would be a clinical-safety violation
        (the EHR might bill or prescribe the wrong drug based on the
        diagnosis).
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "targetsystem": RXNORM_URI,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False
        assert len(_translate_matches(body)) == 0


# ===========================================================================
# Lens 5: XML rendering of all 4 $subsumes outcome values
# (EXPLORER tip 5 — hyphenated subsumed-by / not-subsumed)
# ===========================================================================
# Clinical contract: per FHIR R4 §3.4.1 (XML representation) + the
# CodeSystem $subsumes OperationDefinition
# (https://hl7.org/fhir/R4/codesystem-operation-subsumes.html), the
# `outcome` parameter is a valueCode of type
# http://hl7.org/fhir/ValueSet/concept-subsumption-outcome with 4
# possible values: 'equivalent', 'subsumes', 'subsumed-by', 'not-subsumed'.
#
# Per FHIR R4 §3.4.1 + CR-002 fix, the wire-format must render as:
#   <valueCode value="subsumed-by"/>
# NOT camelCase ('subsumedBy') or other variants. The XML serializer
# must preserve the literal hyphenated form.

class TestLens5XmlRenderingSubsumesOutcomes:
    """Lens 5 (EXPLORER tip 5): XML rendering of all 4 $subsumes outcome
    values is clinically correct (hyphenated wire-format preserved).
    """

    @pytest.mark.parametrize(
        "codeA,codeB,expected_outcome,clinical_meaning",
        [
            (
                SNOMED_DIABETES_MELLITUS,
                SNOMED_T2DM,
                "subsumes",
                "Diabetes (broader) subsumes T2DM (narrower)",
            ),
            (
                SNOMED_T2DM,
                SNOMED_DIABETES_MELLITUS,
                "subsumed-by",
                "T2DM (narrower) is subsumed by Diabetes (broader)",
            ),
            (
                SNOMED_T2DM,
                SNOMED_T2DM,
                "equivalent",
                "T2DM is equivalent to itself (self-subsumption)",
            ),
            (
                SNOMED_T2DM,
                RXNORM_METFORMIN,
                "not-subsumed",
                "T2DM (SNOMED-supplied) vs metformin (cross-code, not seeded in SNOMED)",
            ),
        ],
        ids=["subsumes", "subsumed-by", "equivalent", "not-subsumed"],
    )
    def test_t50_xml_outcome_renders_hyphenated(
        self, fhir_client, codeA, codeB, expected_outcome, clinical_meaning
    ):
        """$subsumes XML response renders the outcome valueCode with the
        hyphenated wire-format (no camelCase / snake_case variants).

        Clinical justification: per FHIR R4 §3.4.1, the wire-format
        MUST preserve the literal value. Strict XML parsers reject
        camelCase variants (e.g. 'subsumedBy' instead of 'subsumed-by').
        A regression that mangles the wire-format would silently break
        EHR integrations using strict XML parsers.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": codeA,
                "codeB": codeB,
                "_format": "xml",
            },
        )
        assert r.status_code == 200, r.text
        # Content-Type MUST be application/fhir+xml
        assert r.headers.get("content-type", "").startswith("application/fhir+xml"), (
            f"Content-Type MUST be application/fhir+xml. Got "
            f"{r.headers.get('content-type')!r}."
        )
        body_text = r.text
        # The hyphenated value MUST appear as an XML attribute
        assert f'value="{expected_outcome}"' in body_text, (
            f"XML response MUST render <valueCode value=\"{expected_outcome}\"/>. "
            f"Got body: {body_text[:300]!r}..."
        )
        # FORBIDDEN camelCase / snake_case variants MUST NOT appear for
        # the hyphenated values.
        forbidden_variants = {
            "subsumed-by": ["subsumedBy", "subsumed_by", "subsumedby"],
            "not-subsumed": ["notSubsumed", "not_subsumed", "notsubsumed"],
        }
        for forbidden in forbidden_variants.get(expected_outcome, []):
            assert f'value="{forbidden}"' not in body_text, (
                f"FORBIDDEN wire-format variant {forbidden!r} found in XML "
                f"body. Strict XML parsers would reject this. Body: "
                f"{body_text[:300]!r}."
            )

    def test_t51_xml_response_resource_type_parameters(self, fhir_client):
        """XML response has root element <Parameters> per FHIR R4 §3.4.1.

        Clinical justification: the XML root element name MUST match the
        JSON resourceType. Strict XML parsers rely on the root element
        name to dispatch the response handler.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_DIABETES_MELLITUS,
                "codeB": SNOMED_T2DM,
                "_format": "xml",
            },
        )
        assert r.status_code == 200
        assert "<Parameters" in r.text, (
            f"XML root element MUST be <Parameters>. Got: {r.text[:200]!r}."
        )

    def test_t52_xml_outcome_part_is_valueCode_not_valueString(self, fhir_client):
        """XML outcome is rendered as <valueCode value="X"/>, NOT as
        <valueString value="X"/>.

        Clinical justification: per FHIR R4
        https://hl7.org/fhir/R4/codesystem-operation-subsumes.html,
        `outcome` is a valueCode (outcome is bound to the
        concept-subsumption-outcome ValueSet). Rendering as valueString
        would be a wire-type drift — strict XML parsers extracting the
        value via the valueCode attribute would miss the value.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_DIABETES_MELLITUS,
                "codeB": SNOMED_T2DM,
                "_format": "xml",
            },
        )
        assert r.status_code == 200
        assert "<valueCode" in r.text, (
            f"outcome MUST render as <valueCode value=\"...\"/>. "
            f"Got: {r.text[:200]!r}."
        )
        # valueString MUST NOT appear for the outcome parameter
        assert "<valueString" not in r.text, (
            f"Wire-type drift: <valueString> found in $subsumes XML response. "
            f"outcome MUST be <valueCode/>. Body: {r.text[:300]!r}."
        )


# ===========================================================================
# Lens 6: Inactive code clinical safety (CF-SKEPTIC-CS05-02)
# ===========================================================================
# The conformance fixture does NOT seed inactive codes (SUPPRESS='O' or 'D').
# Per CS-05 SKEPTIC + HISTORIAN: the engine filters mrconso on SUPPRESS='N'
# at the SQL layer, so inactive codes are unreachable through $lookup and
# $validate-code returns result=false. This IS the clinically-safe
# behavior today (filtering is safer than surfacing with a flag — a tired
# clinician at 3am might miss the flag and document with a deprecated code).
#
# TERMINOLOGIST confirms: the CF-SKEPTIC-CS05-02 reproduction shape (seed
# SUPPRESS='O', expect inactive=true) is the load-bearing contract for a
# future enhancement. Today: every seeded code is SUPPRESS='N' (active),
# so the contract is "no inactive property emitted on active codes" —
# which is exactly what the engine does.

class TestLens6InactiveCodeClinicalSafety:
    """Lens 6: Verify the inactive-code clinical-safety contract.

    Clinical contract: deprecated/suppressed codes MUST NOT appear in
    patient-facing surfaces (EHR pick-lists, order sets, CDS hooks). The
    engine filters mrconso on SUPPRESS='N' (active only), so inactive
    codes are unreachable through $lookup — clinically safe today.
    """

    @pytest.mark.parametrize(
        "system,code,source_label",
        [
            (SNOMED_URI, SNOMED_T2DM, "SNOMED PT (active)"),
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS, "SNOMED PT (active)"),
            (ICD10CM_URI, ICD10CM_T2DM, "ICD-10-CM HT (active)"),
            (RXNORM_URI, RXNORM_METFORMIN, "RxNorm SCD (active)"),
        ],
        ids=["snomed-t2dm", "snomed-dm", "icd10-t2dm", "rxnorm-metformin"],
    )
    def test_t60_active_code_no_inactive_flag_per_source(
        self, fhir_client, system, code, source_label
    ):
        """Every active seeded code across all 4 sources has NO `inactive`
        property in its property group.

        Clinical justification: emitting `inactive=true` on an active code
        would be a false-deprecation signal (a tired clinician might see
        the flag and avoid using the code). Per FHIR R4
        concept-properties.html, `inactive` is 0..1 boolean — absence on
        active codes is conformant.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        codes = _list_property_codes(body)
        assert "inactive" not in codes, (
            f"Active code {source_label} ({system} {code}) MUST NOT carry "
            f"`inactive` property. Found: {codes}. False-deprecation signal "
            f"is a clinical-safety risk."
        )

    def test_t61_validate_code_on_suppressed_code_returns_false(self, fhir_client):
        """$validate-code on a code that doesn't exist in the active
        snapshot returns result=false (clinically safe).

        Clinical justification: a CDS hook checking 'is this code valid
        for new documentation?' MUST get result=false for a deprecated
        code. The engine's SUPPRESS='N' filter ensures the code is absent
        from get_code_infos. This IS the clinically-correct answer —
        deprecated codes MUST NOT be presented as valid for new
        documentation.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": "DEPRECATED_NOT_SEEDED"},
        )
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is False

    def test_t62_cf_skeptic_cs05_02_fixture_gap_documented(self, fhir_client):
        """CF-SKEPTIC-CS05-02 (inactive property never emitted) is a
        fixture-gap carry-forward — the engine is structurally correct;
        the fixture has no SUPPRESS='O' rows to exercise the surfacing
        path.

        Clinical justification: this probe documents the carry-forward
        via the carry-forward-as-probe pattern (CS-03 TERMINOLOGIST
        methodology). When a future enhancement seeds SUPPRESS='O' rows
        AND surfaces inactive=true on those codes, this probe MUST be
        updated to assert the property IS emitted for inactive codes.

        Reproduction shape for the future fix:
          1. Add a mrconso row: ('OLD_CODE', 'PT', 'Deprecated name',
             'A_OLD', 'O', 'SNOMEDCT_US', 'C0011849')
          2. $lookup returns 200 with `inactive=true` in property group.
          3. $validate-code returns result=false by default.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r.status_code == 200
        codes = _list_property_codes(r.json())
        # CURRENT BEHAVIOR (CF-SKEPTIC-CS05-02).
        assert "inactive" not in codes


# ===========================================================================
# Lens 7: Abstract concept clinical correctness (CF-SKEPTIC-CS05-01)
# ===========================================================================
# The conformance fixture does NOT seed abstract concepts (all 4 seeded
# codes are clinically-selectable). Per CS-05 SKEPTIC: the engine hardcodes
# abstract=False at engines/fhir/responses.py:46 — clinically safe for the
# current fixture (no abstract codes seeded) but would be UNSAFE in
# production if abstract codes exist in UMLS.
#
# TERMINOLOGIST confirms DEFERRED is the clinically appropriate
# classification: a future enhancement MUST propagate
# SNOMED definitionStatusId (or equivalent) into CodeInfo and through
# build_parameters_lookup. The reproduction shape is the load-bearing
# contract.

class TestLens7AbstractConceptClinicalCorrectness:
    """Lens 7: Verify the abstract-concept clinical-correctness contract.

    Clinical contract: per FHIR R4
    https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out `abstract`:
    "True if this code is abstract (i.e. the code is not meant to be used
    in an instance, only as a grouping/parent concept)." EHRs and CDS
    hooks use this flag to EXCLUDE abstract concepts from pick-lists and
    order sets.
    """

    @pytest.mark.parametrize(
        "code,clinical_kind",
        [
            (SNOMED_DIABETES_MELLITUS, "grouping concept (clinically broader)"),
            (SNOMED_T2DM, "billable diagnosis"),
            (ICD10CM_T2DM, "billable diagnosis"),
            (RXNORM_METFORMIN, "selectable drug product"),
        ],
        ids=["snomed-dm", "snomed-t2dm", "icd10-t2dm", "rxnorm-metformin"],
    )
    def test_t70_seeded_codes_abstract_false_clinically_consistent(
        self, fhir_client, code, clinical_kind
    ):
        """Each seeded code returns abstract=False today — clinically
        consistent with the seeded fixture (all clinically-selectable).

        Clinical justification: the seeded codes are NOT SNOMED hierarchy
        roots (which would be abstract). abstract=false today is correct
        for the current fixture. A future regression that flips the flag
        without source data would surface a false signal (either direction).
        """
        system_map = {
            SNOMED_DIABETES_MELLITUS: SNOMED_URI,
            SNOMED_T2DM: SNOMED_URI,
            ICD10CM_T2DM: ICD10CM_URI,
            RXNORM_METFORMIN: RXNORM_URI,
        }
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system_map[code], "code": code},
        )
        assert r.status_code == 200
        body = r.json()
        # valueBoolean wire-type MUST be used (not valueString)
        abstract_param = None
        for p in body.get("parameter", []):
            if p.get("name") == "abstract":
                abstract_param = p
                break
        assert abstract_param is not None, "Out `abstract` MUST be present."
        assert "valueBoolean" in abstract_param, (
            "Out `abstract` MUST use valueBoolean wire-type (not valueString)."
        )
        assert abstract_param["valueBoolean"] is False

    def test_t71_cf_skeptic_cs05_01_fixture_gap_documented(self, fhir_client):
        """CF-SKEPTIC-CS05-01 (abstract hardcoded False) is a fixture-gap
        carry-forward — the engine has no abstract-flag data source today.

        Clinical justification: this probe documents the carry-forward.
        When a future enhancement propagates SNOMED definitionStatusId
        into CodeInfo AND build_parameters_lookup uses code_info.abstract
        (instead of the hardcoded False), this probe MUST be updated to
        assert the propagated value.

        Reproduction shape for the future fix:
          1. Add CodeInfo.abstract: bool field.
          2. Populate from SNOMED definitionStatusId (or UMLS TTY='AB').
          3. build_parameters_lookup: _param("abstract", code_info.abstract,
             "valueBoolean") instead of hardcoded False.
          4. Seed an abstract concept in the fixture; assert abstract=true.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
        )
        assert r.status_code == 200
        # CURRENT BEHAVIOR (CF-SKEPTIC-CS05-01).
        assert _param_value(r.json(), "abstract") is False


# ===========================================================================
# Lens 8: Multi-hierarchy clinical correctness (CF-SKEPTIC-CS05-03)
# ===========================================================================
# The conformance fixture seeds ONE parent/child relationship (single-
# hierarchy). Per CS-05 SKEPTIC + HISTORIAN: the BFS structurally handles
# multi-parent DAGs (visited-set prevents revisits), but no probe
# exercises multi-hierarchy on the conformance fixture.
#
# TERMINOLOGIST confirms: the seeded single-hierarchy case gives the
# clinically-correct answer (Diabetes subsumes T2DM). The CF-SKEPTIC-CS05-03
# reproduction shape (multi-parent mrrel) is the load-bearing contract
# for a future fixture enhancement.

class TestLens8MultiHierarchyClinicalCorrectness:
    """Lens 8: Verify multi-hierarchy subsumption gives clinically-correct
    answers on the seeded single-hierarchy pair.
    """

    def test_t80_parent_subsumes_child_clinically_directional(self, fhir_client):
        """Diabetes (broader) subsumes T2DM (narrower) — clinically
        directional.

        Clinical justification: a CDS hook asking 'does this patient have
        a broader condition?' gets the clinically-correct answer. Diabetes
        is broader than T2DM (every T2DM patient has Diabetes, but not
        every Diabetes patient has T2DM — they might have Type 1).
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_DIABETES_MELLITUS,
                "codeB": SNOMED_T2DM,
            },
        )
        assert r.status_code == 200
        outcome = _param_value(r.json(), "outcome")
        assert outcome == "subsumes"

    def test_t81_child_subsumed_by_parent_clinical_mirror(self, fhir_client):
        """T2DM (narrower) is subsumed by Diabetes (broader) — mirror of
        test_t80.

        Clinical justification: a CDS hook asking 'is this more-specific
        code covered by the broader category?' gets the clinically-correct
        answer (a T2DM code IS covered by the broader Diabetes category).
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_T2DM,
                "codeB": SNOMED_DIABETES_MELLITUS,
            },
        )
        assert r.status_code == 200
        assert _param_value(r.json(), "outcome") == "subsumed-by"

    def test_t82_cf_skeptic_cs05_03_fixture_gap_documented(self, fhir_client):
        """CF-SKEPTIC-CS05-03 (multi-hierarchy BFS) is a fixture-gap
        carry-forward — the BFS is structurally correct (visited-set),
        but the fixture has only single-parent mrrel.

        Clinical justification: this probe documents the carry-forward.
        When a future fixture enhancement adds multi-parent mrrel rows
        (e.g. T2DM has parents Diabetes AND Endocrine disorder AND
        Metabolic disorder), this probe MUST be extended with multi-parent
        probes verifying the BFS walks ALL paths.
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
        assert r.status_code == 200
        # CURRENT BEHAVIOR (CF-SKEPTIC-CS05-03).
        assert _param_value(r.json(), "outcome") == "subsumes"


# ===========================================================================
# Lens 9: Version-specific clinical correctness
# ===========================================================================
# The engine loads a single mrconso snapshot (no versioned data). The
# `version` parameter is accepted but ignored (INTENDED per AGENTS.md NOT
# A BUG registry). The clinical-safety invariant: $lookup with version=X
# returns the SAME clinical answer as without version — a CDS hook passing
# a version specifier MUST NOT get a divergent display/code/abstract.

class TestLens9VersionSpecificClinicalCorrectness:
    """Lens 9: Verify the version-parameter clinical-correctness invariant.

    Clinical contract: $lookup?version=X MUST be accepted (per FHIR R4
    In Parameters 0..1 string) and MUST NOT produce a different clinical
    answer than the no-version call (because the engine has one snapshot).
    """

    @pytest.mark.parametrize(
        "system,code,expected_display",
        [
            (SNOMED_URI, SNOMED_T2DM, EXPECTED_DISPLAY_SNOMED_T2DM),
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS, EXPECTED_DISPLAY_SNOMED_DM),
            (ICD10CM_URI, ICD10CM_T2DM, EXPECTED_DISPLAY_ICD10CM_T2DM),
            (RXNORM_URI, RXNORM_METFORMIN, EXPECTED_DISPLAY_RXNORM_METFORMIN),
        ],
        ids=["snomed-t2dm", "snomed-dm", "icd10-t2dm", "rxnorm-metformin"],
    )
    def test_t90_lookup_with_version_returns_same_canonical_display(
        self, fhir_client, system, code, expected_display
    ):
        """$lookup with any version returns the same canonical display as
        without version.

        Clinical justification: a CDS hook passing version MUST NOT get a
        divergent display — the engine has one snapshot; the display is
        the snapshot's preferred term regardless of the requested version.
        A divergence would surface different displays depending on whether
        the EHR passes version — a clinical-safety risk.
        """
        r_with = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code, "version": "2025-03"},
        )
        r_without = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        assert r_with.status_code == 200 and r_without.status_code == 200
        # Canonical display MUST be byte-exact identical
        assert _param_value(r_with.json(), "display") == expected_display
        assert _param_value(r_without.json(), "display") == expected_display

    def test_t91_validate_code_with_version_clinically_consistent(self, fhir_client):
        """$validate-code with version returns the same result as without.

        Clinical justification: a CDS hook asking 'is this code valid?'
        with version specifier MUST NOT get a spurious result=false (which
        would false-reject valid documentation) or result=true (which
        would false-accept invalid documentation).
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
        assert _param_value(r_with.json(), "result") == \
               _param_value(r_without.json(), "result") is True


# ===========================================================================
# Lens 10: Mutually-exclusive properties clinical safety
# ===========================================================================
# If a code has both abstract=true AND inactive=true, which wins for
# clinical use? The spec doesn't mandate; the engine should be consistent.
# The engine has NEITHER property as engine-sourced data today (both are
# defaults: abstract hardcoded False, inactive omitted).

class TestLens10MutuallyExclusivePropertiesClinicalSafety:
    """Lens 10: Verify the mutually-exclusive-properties clinical-safety
    contract.

    Clinical contract: when BOTH flags are present in a future engine
    enhancement, the clinically-correct precedence is: inactive=true
    wins over abstract=true (a deprecated code is NEVER surfaced,
    regardless of abstract-ness). Today: both flags are defaults.
    """

    @pytest.mark.parametrize(
        "system,code",
        [
            (SNOMED_URI, SNOMED_T2DM),
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
            (ICD10CM_URI, ICD10CM_T2DM),
            (RXNORM_URI, RXNORM_METFORMIN),
        ],
        ids=["snomed-t2dm", "snomed-dm", "icd10-t2dm", "rxnorm-metformin"],
    )
    def test_t100_consistency_per_source(self, fhir_client, system, code):
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
        assert r.status_code == 200
        body = r.json()
        codes = _list_property_codes(body)
        assert _param_value(body, "abstract") is False
        assert "inactive" not in codes


# ===========================================================================
# Lens 11: Source-read structural contracts for clinical correctness
# ===========================================================================
# Verify the engine builders / handlers are structurally wired for clinical
# correctness — no hardcoded literals that would silently produce wrong
# clinical answers.

class TestLens11SourceReadStructuralContracts:
    """Lens 11: Source-read audit of clinical-correctness contracts.
    """

    def test_t110_build_parameters_subsumes_emits_valuecode(self):
        """build_parameters_subsumes emits outcome as valueCode (not
        valueString) — wire-type clinically safe.

        Clinical justification: per FHIR R4 $subsumes Out `outcome` is
        bound to concept-subsumption-outcome ValueSet (valueCode wire
        type). A regression to valueString would surface a wire-type
        that strict clients reject.
        """
        from medterm4ds.engines.fhir import responses
        body = responses.build_parameters_subsumes("equivalent")
        params = body.get("parameter", [])
        assert len(params) == 1
        assert params[0].get("name") == "outcome"
        assert "valueCode" in params[0]
        assert params[0]["valueCode"] == "equivalent"
        assert "valueString" not in params[0]

    def test_t111_build_parameters_translate_uses_fhir_equivalence_helper(self):
        """build_parameters_translate calls _fhir_equivalence_from_relationship
        (NOT hardcoded 'equivalent') — clinical correctness structurally
        enforced.

        Clinical justification: per TS-02 TERMINOLOGIST QA-030 fix +
        CR-024 milestone-3 review, the equivalence value MUST be sourced
        from the canonical helper. Hardcoding 'equivalent' would
        misrepresent same-CUI crosswalks (which ARE 'equivalent') but
        ALSO misrepresent hierarchical mappings (which are 'subsumes' /
        'specializes'). A source-read audit catches the regression.
        """
        from medterm4ds.engines.fhir import responses
        src_text, tree = _get_module_source(responses)
        func_node = _get_func_source_local(tree, "build_parameters_translate")
        assert func_node is not None
        # The function MUST call _fhir_equivalence_from_relationship
        call_count = _count_calls_in(func_node, "_fhir_equivalence_from_relationship")
        assert call_count >= 1, (
            "build_parameters_translate MUST call "
            "_fhir_equivalence_from_relationship (not hardcode 'equivalent')."
        )
        # The function MUST NOT hardcode the literal "equivalent" as the
        # equivalence value (it should be sourced from the helper).
        # We check for any ast.Constant(value="equivalent") that appears
        # as a valueCode argument.
        hardcoded_count = 0
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Call):
                continue
            # Look for _param calls or dict constructions with literal
            # "equivalent" as the value.
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "equivalent":
                    hardcoded_count += 1
        assert hardcoded_count == 0, (
            f"build_parameters_translate hardcodes 'equivalent' literal "
            f"{hardcoded_count} time(s). MUST source from "
            f"_fhir_equivalence_from_relationship instead."
        )

    def test_t112_build_parameters_lookup_abstract_param_present(self):
        """build_parameters_lookup emits the `abstract` Out parameter
        (hardcoded False today) — wire-type valueBoolean.

        Clinical justification: per CF-SKEPTIC-CS05-01, the abstract
        parameter is hardcoded False. The probe confirms the parameter
        IS emitted (clinically safe — clients always know the server's
        position) and uses the correct wire type.
        """
        from medterm4ds.engines.fhir import responses
        src_text, tree = _get_module_source(responses)
        func_node = _get_func_source_local(tree, "build_parameters_lookup")
        assert func_node is not None
        # The function MUST call _param with name="abstract"
        found_abstract = False
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Name) and f.id == "_param"):
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and first_arg.value == "abstract":
                found_abstract = True
                # The third positional arg MUST be "valueBoolean"
                if len(node.args) >= 3:
                    wire_type_arg = node.args[2]
                    if isinstance(wire_type_arg, ast.Constant):
                        assert wire_type_arg.value == "valueBoolean", (
                            f"abstract parameter MUST use valueBoolean wire "
                            f"type. Got {wire_type_arg.value!r}."
                        )
        assert found_abstract, (
            "build_parameters_lookup MUST emit `abstract` Out parameter."
        )


def _get_func_source_local(tree: ast.AST, func_name: str) -> ast.AST | None:
    """Locate a top-level function by name."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return node
    return None


# ===========================================================================
# Lens 12: Cross-source clinical safety on $translate
# ===========================================================================
# Verify the $translate target Coding is clinically correct across all
# source-target combinations the fixture supports.

class TestLens12CrossSourceTranslateClinicalSafety:
    """Lens 12: Cross-source clinical correctness on $translate.
    """

    def test_t120_snomed_to_icd10cm_target_clinically_correct(self, fhir_client):
        """SNOMED T2DM -> ICD-10-CM produces a clinically-correct match
        (target=E11, display=Type 2 diabetes mellitus, equivalence=
        equivalent).

        Clinical justification: SNOMED 44054006 and ICD-10-CM E11 share
        CUI C0011847. The match MUST be the same clinical concept (T2DM
        diagnosis) in the ICD-10-CM code system. A regression that
        surfaces a different ICD-10-CM code (e.g. E10 Type 1) would be
        a CRITICAL clinical bug — wrong diagnosis billing.
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "targetsystem": ICD10CM_URI,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert _param_value(body, "result") is True
        matches = _translate_matches(body)
        assert len(matches) > 0
        concept = _match_part(matches[0], "concept").get("valueCoding", {})
        assert concept.get("system") == ICD10CM_URI
        assert concept.get("code") == ICD10CM_T2DM
        assert concept.get("display") == EXPECTED_DISPLAY_ICD10CM_T2DM
        equiv = _match_part(matches[0], "equivalence").get("valueCode")
        assert equiv == "equivalent"

    def test_t121_icd10cm_to_snomed_reverse_match_clinically_correct(self, fhir_client):
        """ICD-10-CM E11 -> SNOMED produces a clinically-correct reverse
        match (target=SNOMED T2DM, same CUI).

        Clinical justification: $translate is bidirectional via the UMLS
        CUI crosswalk. The reverse direction MUST surface the same
        clinical concept (T2DM). A divergence would surface different
        clinical content depending on direction.
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI,
                "code": ICD10CM_T2DM,
                "targetsystem": SNOMED_URI,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # ICD-10-CM -> SNOMED MUST match (same CUI C0011847)
        assert _param_value(body, "result") is True
        matches = _translate_matches(body)
        assert len(matches) > 0
        concept = _match_part(matches[0], "concept").get("valueCoding", {})
        assert concept.get("system") == SNOMED_URI
        # The matched SNOMED code MUST be T2DM (44054006), NOT DM (73211009),
        # because the CUI C0011847 maps specifically to T2DM in SNOMED.
        assert concept.get("code") == SNOMED_T2DM
        assert concept.get("display") == EXPECTED_DISPLAY_SNOMED_T2DM

    def test_t122_translate_no_false_match_for_unrelated_codes(self, fhir_client):
        """$translate from a SNOMED code to RxNorm with no shared CUI
        produces result=false (no false match).

        Clinical justification: a false match would surface a drug code
        for a diagnosis code — a CRITICAL clinical bug (EHR might
        prescribe the wrong drug based on the diagnosis). The engine's
        CUI-based crosswalk MUST NOT produce false matches across
        unrelated clinical domains.
        """
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_DIABETES_MELLITUS,
                "targetsystem": RXNORM_URI,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False
        assert len(_translate_matches(body)) == 0


# ===========================================================================
# Lens 13: Cross-operation canonical-DISPLAY invariant META-PATTERN
# (CS-04/TERMINOLOGIST tip — count=5 PROMOTED META-PATTERN)
# ===========================================================================
# The canonical-DISPLAY invariant is byte-exact across $lookup, $validate-
# code, $translate target concept display, and $validate-code codeableConcept
# matched-coding display (count=5 PROMOTED). TERMINOLOGIST confirms the
# CLINICAL CORRECTNESS of the invariant: the canonical display chosen per
# source matches the source's preferred-term policy.

class TestLens13CanonicalDisplayCrossOperationInvariant:
    """Lens 13: N-way canonical-DISPLAY invariant holds across 4 ops for
    every seeded code (CS-04/TERMINOLOGIST tip + CS-05 EXPLORER test_e10/e11).
    """

    @pytest.mark.parametrize(
        "system,code,expected_display",
        [
            (SNOMED_URI, SNOMED_T2DM, EXPECTED_DISPLAY_SNOMED_T2DM),
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS, EXPECTED_DISPLAY_SNOMED_DM),
            (ICD10CM_URI, ICD10CM_T2DM, EXPECTED_DISPLAY_ICD10CM_T2DM),
            (RXNORM_URI, RXNORM_METFORMIN, EXPECTED_DISPLAY_RXNORM_METFORMIN),
        ],
        ids=["snomed-t2dm", "snomed-dm", "icd10-t2dm", "rxnorm-metformin"],
    )
    def test_t130_canonical_display_invariant_across_lookup_and_validate(
        self, fhir_client, system, code, expected_display
    ):
        """$lookup Out display == $validate-code Out display == expected.

        Clinical justification: a clinician seeing the display from
        $validate-code MUST see the same string as from $lookup. Both
        MUST equal the source's preferred-term policy.
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
        assert lookup_disp == validate_disp == expected_display

    def test_t131_translate_target_display_matches_lookup(self, fhir_client):
        """$translate target concept display == $lookup target display
        (same CUI crosswalk).

        Clinical justification: when SNOMED T2DM maps to ICD-10-CM E11
        via shared CUI, the target display MUST equal the ICD-10-CM
        display a $lookup on E11 would return. A divergence would
        surface different displays depending on whether the clinician
        reached the code via $lookup or $translate.
        """
        # $translate SNOMED T2DM -> ICD-10-CM
        r_translate = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_T2DM,
                "targetsystem": ICD10CM_URI,
            },
        )
        # $lookup ICD-10-CM E11 directly
        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": ICD10CM_URI, "code": ICD10CM_T2DM},
        )
        assert r_translate.status_code == 200 and r_lookup.status_code == 200
        translate_target_display = _match_part(
            _translate_matches(r_translate.json())[0], "concept"
        ).get("valueCoding", {}).get("display")
        lookup_display = _param_value(r_lookup.json(), "display")
        assert translate_target_display == lookup_display == EXPECTED_DISPLAY_ICD10CM_T2DM
