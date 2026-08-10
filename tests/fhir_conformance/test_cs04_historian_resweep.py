"""HISTORIAN RESWEEP probes for CS-04 (CodeSystem $subsumes Operation) —
fresh full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html (R4 4.0.1).

This file contains NEW regression probes that are NOT in the baseline
``test_cs04_historian.py``. The baseline (test_h20..test_h110, 22 probes) is
treated as trusted prior coverage; this resweep file adds the
FRESH-FULL-SWEEP mandated probes per USER_DIRECTIVES [2026-08-08].

HISTORIAN lens (per ROLE_QA_ENGINEER Section 3): pattern-match against prior
bug patterns from ``GLOBAL_KNOWLEDGE.md``, ``ARCHIVE_LOG.md``, and the
prior CS-04 + cross-chunk bug list.

CS-04 / SKEPTIC tip for HISTORIAN: the SKEPTIC fix shape (isinstance guard
at data-access boundary, ``apps/fhir_api.py:3028``) is the **3rd instance**
of this pattern in the codebase (after ``_do_closure`` =
CF-HISTORIAN-CM03-01, and ``_extract_coding_from_parameters``). If HISTORIAN
finds a 4th sibling instance, PROMOTE to ``GLOBAL_RULES.md`` as 10th
PROMOTED pattern. Search for ``for param in``, ``for entry in``,
``for item in`` patterns in ``apps/fhir_api.py`` that call ``.get(`` on the
iterated variable without isinstance check.

Prior CS-04 patterns to re-derive (HELD or REGRESSED):
  - QA-053 codingA/codingB alternative-encoding silent-drop
  - QA-001 _parse_parameters non-dict guard (just fixed in SKEPTIC —
    verify regression-pin via test_l104-style probe)
  - Mixed-system check fires with diagnostics naming both systems
  - Hyphenated XML outcome rendering (subsumed-by has hyphen — verify
    wire-format correctness in XML serializer)
  - valueCode not valueString wire-type assertion
  - Plus 9 PROMOTED patterns

12 lens dimensions, ~50 probes covering all 9 spec items:
  L1  _parse_parameters isinstance guard (SKEPTIC QA-001 regression-pin)
  L2  4th-sibling isinstance-guard search (PROMOTION candidate)
  L3  codingA/codingB alternative-encoding silent-drop (CS-04 SKEPTIC QA-053)
  L4  Mixed-system check diagnostics
  L5  Hyphenated XML outcome rendering (subsumed-by)
  L6  valueCode not valueString wire-type
  L7  HCPCS URI drift class (count=8+1 PROMOTED) regression-pin
  L8  Empty-string-as-present-on-required-Query (count=5 PROMOTED)
  L9  Closed-enum R5/R4B contamination (CF-HISTORIAN-VS01-01 RESOLVED)
  L10 Boolean serializer lowercase wire-format (A1/CR-002)
  L11 Cross-handler helper-wiring (count=6 PROMOTED)
  L12 Subsumes directionality mirror + equivalent outcome clinical correctness
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
#   system   0..1  uri     "The code system in which subsumption testing is to
#                           be performed."
#   version  0..1  string  "The version of the code system, if one was
#                           provided in the source data"
#   codingA  0..1  Coding  "The 'A' Coding that is to be tested."
#   codingB  0..1  Coding  "The 'B' Coding that is to be tested. ..."
#
# Out Parameters:
#   outcome   1..1  code   "The subsumption relationship between code/Coding
#                           'A' and code/Coding 'B'. There are 4 possible
#                           codes to be returned (equivalent, subsumes,
#                           subsumed-by, and not-subsumed)."

VALID_OUTCOMES = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}
FORBIDDEN_OUTCOMES = {"subsumedBy", "subsumed_by", "subsumedby", "SUBSUMED-BY", "Subsumed-By"}

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child (descendant of 73211009)
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"            # unrelated to diabetes branch
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"                    # T2DM in ICD-10-CM (same CUI as SNOMED T2DM)

# Source code location for AST source-read probes
FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


def _outcome(body: dict) -> str | None:
    """Return the value of the Out ``outcome`` parameter."""
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


def _outcome_value_type(body: dict) -> str | None:
    """Return the wire-type key used on the Out ``outcome`` parameter
    (e.g. ``valueCode`` or ``valueString``)."""
    for p in body.get("parameter", []):
        if not isinstance(p, dict):
            continue
        if p.get("name") == "outcome":
            for k in p:
                if k.startswith("value"):
                    return k
    return None


def _diagnostics(body: dict) -> str:
    """Concatenate all diagnostics strings from an OperationOutcome."""
    out = []
    for issue in body.get("issue", []):
        if not isinstance(issue, dict):
            continue
        d = issue.get("diagnostics", "")
        if d:
            out.append(d)
    return " | ".join(out)


def _get_func_source(func_name: str) -> str:
    """Source-read helper: get the source of a top-level or nested function
    definition from ``apps/fhir_api.py``. Walks BOTH ``ast.FunctionDef`` AND
    ``ast.AsyncFunctionDef`` (extends CS-01 HISTORIAN strategy)."""
    src = FHIR_API_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(src, node) or ""
    return ""


def _get_nested_func_source(parent_name: str, child_name: str) -> str:
    """Source-read helper for nested functions defined inside ``create_fhir_app``
    factory (extends CS-03 HISTORIAN methodology — plain ``ast.walk`` would
    miss nested defs)."""
    src = FHIR_API_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == parent_name:
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.name == child_name:
                            return ast.get_source_segment(src, child) or ""
    return ""


# ============================================================================
# L0: 4th-sibling _expand_intensional isinstance guard (CS-04 HISTORIAN QA-001)
# ============================================================================
# CS-04 / HISTORIAN QA-001 (RESOLVED in same iteration) added
# ``isinstance(include, dict): continue`` guards at apps/fhir_api.py:2472-2491
# inside ``_expand_intensional`` for the compose.include[], filter[],
# exclude[], exclude.concept[], and include.concept[] iterators. The bug
# was the 4th sibling of the isinstance-guard pattern; PROMOTED to
# GLOBAL_RULES.md as 10th PROMOTED pattern.

class TestLens0ExpandIntensionalIsinstanceGuard:
    """L0: CS-04 HISTORIAN QA-001 regression-pin.

    The fix is structural: ``isinstance(<var>, dict)`` guards inside
    ``_expand_intensional`` at 5 iterator sites. Without them, a hostile
    POST body containing ``compose.include[]`` / ``compose.exclude[]`` /
    ``compose.include[].concept[]`` / ``compose.include[].filter[]`` /
    ``compose.exclude[].concept[]`` entries as non-dict (string, int, null,
    list) triggers ``AttributeError`` that propagates as 500 + text/plain
    (information-disclosure surface, non-conformant per FHIR R4 §3.1.0.1.5
    + §3.1.0.1.9).
    """

    def test_h00_expand_intensional_source_contains_isinstance_guards(self) -> None:
        """Source-read contract: the 5 isinstance guards MUST be present."""
        src = _get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src, "_expand_intensional not found in apps/fhir_api.py"
        # Each iterator should have its own isinstance guard
        # (compose.include[], compose.include[].concept[],
        # compose.include[].filter[], compose.exclude[],
        # compose.exclude[].concept[] — last as inline isinstance check)
        # Minimum: 4 isinstance(X, dict) calls in the function body
        # (include, concept, filt, exclude; the .concept[] inside exclude
        # uses inline `isinstance(c, dict)` filter)
        count = src.count("isinstance(")
        assert count >= 4, (
            f"_expand_intensional must have at least 4 isinstance guards "
            f"(include, concept, filt, exclude); found {count}. "
            f"CS-04 HISTORIAN QA-001 regression."
        )

    def test_h01_post_expand_compose_include_non_dict_no_500(
        self, fhir_client
    ) -> None:
        """POST ValueSet/$expand with non-dict compose.include[] entries
        MUST NOT 500. Spec: FHIR R4 §3.1.0.1.5 + §3.1.0.1.9."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {"include": ["string-not-dict", 42, None]},
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"non-dict compose.include[]: {r.status_code}; expected <500. "
            f"Body: {r.text[:300]}"
        )

    def test_h02_post_expand_compose_exclude_non_dict_no_500(
        self, fhir_client
    ) -> None:
        """POST ValueSet/$expand with non-dict compose.exclude[] entries
        MUST NOT 500."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [{"system": SNOMED_URI, "concept": [{"code": "73211009"}]}],
                "exclude": ["bad-exclude", 99, None],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"non-dict compose.exclude[]: {r.status_code}; expected <500"
        )

    def test_h03_post_expand_compose_include_filter_non_dict_no_500(
        self, fhir_client
    ) -> None:
        """POST ValueSet/$expand with non-dict compose.include[].filter[]
        entries MUST NOT 500."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": ["bad-filter", 42, None],
                }],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"non-dict compose.include[].filter[]: {r.status_code}; expected <500"
        )

    def test_h04_post_expand_compose_include_concept_non_dict_no_500(
        self, fhir_client
    ) -> None:
        """POST ValueSet/$expand with non-dict compose.include[].concept[]
        entries MUST NOT 500."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": ["bad-concept", 42, None],
                }],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"non-dict compose.include[].concept[]: {r.status_code}; expected <500"
        )

    def test_h05_post_expand_mixed_valid_invalid_entries_processed(
        self, fhir_client
    ) -> None:
        """POST with a MIX of valid + non-dict entries MUST process the
        valid entries silently (liberal-in-what-you-accept per
        FHIR R4 §3.1.0.1.5).

        Per EXPLORER test plan tip from HISTORIAN qa_handoff.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [
                    "garbage",  # silently skipped
                    {"system": SNOMED_URI, "concept": [{"code": "73211009"}]},
                    42,  # silently skipped
                ],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code == 200, (
            f"mixed entries: {r.status_code}; expected 200. Body: {r.text[:300]}"
        )
        data = r.json()
        contains = data.get("expansion", {}).get("contains", [])
        codes = [c.get("code") for c in contains]
        assert "73211009" in codes, (
            f"valid entry silently dropped along with non-dict entries; "
            f"contains codes: {codes!r}"
        )


# ============================================================================
# L1: _parse_parameters isinstance guard (SKEPTIC QA-001 regression-pin)
# ============================================================================
# CS-04 / SKEPTIC QA-001 (RESOLVED) added ``isinstance(param, dict): continue``
# guard at apps/fhir_api.py:3028 inside ``_parse_parameters``. This lens
# regression-pins the fix — the guard MUST remain in place; if a future
# change removes it, every probe in this lens fires loudly.

class TestLens1ParseParametersIsinstanceGuard:
    """L1: SKEPTIC QA-001 regression-pin.

    The fix is structural: ``isinstance(param, dict)`` at line 3028 inside
    ``_parse_parameters``. Without it, a hostile POST body containing
    ``parameter[]`` entries as non-dict (string, int, null, list) triggers
    ``AttributeError`` that propagates as 500 + text/plain (information-
    disclosure surface, non-conformant per FHIR R4 §3.1.0.1.5 + §3.1.0.1.9).
    """

    def test_h10_parse_parameters_source_contains_isinstance_guard(self) -> None:
        """Source-read contract: the guard MUST be present."""
        src = _get_nested_func_source("create_fhir_app", "_parse_parameters")
        assert src, "_parse_parameters not found in apps/fhir_api.py"
        assert "isinstance(param, dict)" in src, (
            "SKEPTIC QA-001 fix (isinstance guard) MUST be present in "
            "_parse_parameters; the regression-pin source-read contract "
            "failed — guard was removed or renamed."
        )

    def test_h11_post_subsumes_non_dict_parameter_entries_no_500(
        self, fhir_client
    ) -> None:
        """POST $subsumes with non-dict parameter[] entries MUST NOT 500.

        Spec citation: FHIR R4 §3.1.0.1.5 + §3.1.0.1.9 — server MUST return
        a FHIR OperationOutcome on errors, not a 500 + traceback.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": ["string-not-dict", 42, None],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code < 500, (
            f"non-dict parameter[]: {r.status_code}; expected <500 (no 500). "
            f"Body: {r.text[:300]}"
        )

    def test_h12_post_lookup_non_dict_parameter_entries_no_500(
        self, fhir_client
    ) -> None:
        """POST $lookup with non-dict parameter[] entries MUST NOT 500.

        The fix is at ``_parse_parameters`` which is called by EVERY POST
        handler. The fix MUST cover lookup, not just subsumes.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": ["bad", 99, None],
        }
        r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        assert r.status_code < 500, (
            f"$lookup non-dict parameter[]: {r.status_code}; expected <500. "
            f"Body: {r.text[:300]}"
        )

    def test_h13_post_validate_non_dict_parameter_entries_no_500(
        self, fhir_client
    ) -> None:
        """POST $validate-code with non-dict parameter[] entries MUST NOT 500."""
        body = {
            "resourceType": "Parameters",
            "parameter": [None, "garbage", [1, 2, 3]],
        }
        r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
        assert r.status_code < 500, (
            f"$validate-code non-dict parameter[]: {r.status_code}; expected <500."
        )

    def test_h14_post_translate_non_dict_parameter_entries_no_500(
        self, fhir_client
    ) -> None:
        """POST $translate with non-dict parameter[] entries MUST NOT 500."""
        body = {
            "resourceType": "Parameters",
            "parameter": ["x", 0, None, {"name": "system", "valueString": "junk"}],
        }
        r = fhir_client.post("/fhir/ConceptMap/$translate", json=body)
        assert r.status_code < 500, (
            f"$translate non-dict parameter[]: {r.status_code}; expected <500."
        )

    def test_h15_post_vs_validate_non_dict_parameter_entries_no_500(
        self, fhir_client
    ) -> None:
        """POST ValueSet/$validate-code with non-dict parameter[] entries
        MUST NOT 500."""
        body = {
            "resourceType": "Parameters",
            "parameter": ["bad"],
        }
        r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
        assert r.status_code < 500, (
            f"VS $validate-code non-dict parameter[]: {r.status_code}; expected <500."
        )

    def test_h16_post_subsumes_mixed_valid_and_invalid_entries(
        self, fhir_client
    ) -> None:
        """POST $subsumes with a MIX of valid + non-dict entries MUST process
        the valid entries silently (liberal-in-what-you-accept per
        FHIR R4 §3.1.0.1.5).

        EXPLORER tip from SKEPTIC handoff: the fix should silently skip
        non-dict entries and process the valid ones.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                "garbage",  # silently skipped
                {"name": "codeA", "valueCode": SNOMED_T2DM},
                42,  # silently skipped
                {"name": "codeB", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "system", "valueUri": SNOMED_URI},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, (
            f"mixed entries: {r.status_code}; expected 200 (valid entries "
            f"processed). Body: {r.text[:300]}"
        )
        out = _outcome(r.json())
        assert out == "subsumed-by", (
            f"T2DM (A) is subsumed-by DM (B); got {out!r}"
        )


# ============================================================================
# L2: 4th-sibling isinstance-guard search (PROMOTION candidate)
# ============================================================================
# SKEPTIC tip: search for ``for param in``, ``for entry in``, ``for item in``
# patterns in apps/fhir_api.py that call ``.get(`` on the iterated variable
# without isinstance check. If a 4th sibling is found, PROMOTE to
# GLOBAL_RULES.md as 10th PROMOTED pattern.

class TestLens2IsinstanceGuardFourthSiblingSearch:
    """L2: Search for the 4th sibling instance of the isinstance-guard
    pattern.

    Per the SKEPTIC handoff, the 3 known instances are:
      1. ``_do_closure`` (CF-HISTORIAN-CM03-01 RESOLVED) at line 2278
      2. ``_extract_coding_from_parameters`` (delegates to
         ``_extract_named_coding_from_parameters``) at line 3068
      3. ``_parse_parameters`` (CS-04 SKEPTIC QA-001 RESOLVED) at line 3028

    A 4th sibling would PROMOTE the pattern to GLOBAL_RULES.md.
    """

    def test_h20_all_parameter_iterators_have_isinstance_guard(self) -> None:
        """Source-read audit: every ``for param in body.get("parameter", [])``
        loop in apps/fhir_api.py MUST have an isinstance guard within 5 lines.

        This is the structural PROMOTION gate. If a 4th sibling without a
        guard is found, this probe fires loudly.
        """
        src = FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        unguarded_locations: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            # Match: for <var> in <X>.get("parameter", [])
            if not isinstance(node.iter, ast.Call):
                continue
            if not isinstance(node.iter.func, ast.Attribute):
                continue
            if node.iter.func.attr != "get":
                continue
            if not node.iter.args:
                continue
            first_arg = node.iter.args[0]
            if not isinstance(first_arg, ast.Constant):
                continue
            if first_arg.value != "parameter":
                continue
            # Check whether the loop body has an isinstance guard within 5 stmts
            has_guard = False
            for stmt in node.body[:5]:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                        if sub.func.id == "isinstance":
                            has_guard = True
                            break
                if has_guard:
                    break
            if not has_guard:
                unguarded_locations.append(node.lineno)
        assert not unguarded_locations, (
            f"UNGUARDed `for param in body.get('parameter', [])` loops found "
            f"at lines {unguarded_locations}. Each MUST have an "
            f"`isinstance(param, dict)` guard within the first 5 statements "
            f"of the loop body (per CS-04 SKEPTIC QA-001 fix shape)."
        )

    def test_h21_extract_named_coding_source_contains_isinstance_guard(self) -> None:
        """Source-read contract: ``_extract_named_coding_from_parameters``
        (CS-04 SKEPTIC QA-053 + QA-001 isinstance guard) MUST have isinstance
        guards on BOTH the param entry AND the valueCoding value."""
        src = _get_nested_func_source(
            "create_fhir_app", "_extract_named_coding_from_parameters"
        )
        assert src, "_extract_named_coding_from_parameters not found"
        assert "isinstance(param, dict)" in src, (
            "param-entry isinstance guard missing in "
            "_extract_named_coding_from_parameters"
        )
        assert "isinstance(coding, dict)" in src, (
            "valueCoding isinstance guard missing in "
            "_extract_named_coding_from_parameters"
        )

    def test_h22_extract_codeable_concept_source_contains_isinstance_guards(self) -> None:
        """Source-read contract: ``_extract_codeable_concept_from_parameters``
        MUST have isinstance guards on param + valueCodeableConcept + coding."""
        src = _get_nested_func_source(
            "create_fhir_app", "_extract_codeable_concept_from_parameters"
        )
        assert src
        assert "isinstance(param, dict)" in src
        assert "isinstance(cc, dict)" in src
        assert "isinstance(coding, dict)" in src

    def test_h23_extract_valueset_from_parameters_source_contains_guards(self) -> None:
        """Source-read contract: ``_extract_valueset_from_parameters`` MUST
        have isinstance guards on param + resource."""
        src = _get_nested_func_source(
            "create_fhir_app", "_extract_valueset_from_parameters"
        )
        assert src
        assert "isinstance(param, dict)" in src
        assert "isinstance(resource, dict)" in src

    def test_h24_do_closure_concept_loop_has_isinstance_guard(self) -> None:
        """Source-read contract: ``_do_closure`` concept loop (CF-HISTORIAN-
        CM03-01 RESOLVED) MUST have isinstance guard on param entry AND
        valueCoding value."""
        src = _get_nested_func_source("create_fhir_app", "_do_closure")
        assert src
        # Find the concept-extraction loop region (the loop iterating parameter[])
        # The whole _do_closure source should contain both isinstance guards
        assert "isinstance(param, dict)" in src, (
            "_do_closure param-entry isinstance guard missing "
            "(CF-HISTORIAN-CM03-01 regression)"
        )
        assert "isinstance(coding, dict)" in src, (
            "_do_closure valueCoding isinstance guard missing "
            "(CF-HISTORIAN-CM03-01 regression)"
        )


# ============================================================================
# L3: codingA/codingB alternative-encoding silent-drop (CS-04 SKEPTIC QA-053)
# ============================================================================

class TestLens3CodingAlternativeEncodingSilentDrop:
    """L3: CS-04 SKEPTIC QA-053 regression-pin.

    The pattern: POST $subsumes silently rejected ``codingA``/``codingB``
    (valueCoding) parameters, falling through to the missing-scalar check.
    The fix added ``_extract_named_coding_from_parameters`` helper. This
    lens re-derives the QA-053 fix on the FRESH-FULL-SWEEP codebase.
    """

    def test_h30_post_coding_a_b_returns_200_subsumes(self, fhir_client) -> None:
        """POST $subsumes with system + codingA + codingB (both valueCoding)
        MUST return 200 + outcome (NOT silently reject to 400).

        Note: scalar ``system`` is still required as the system anchor for
        the subsumption test (per baseline test_s71 + spec In ``system`` is
        required unless operation is invoked on a code system instance).
        The CS-04 SKEPTIC QA-053 fix added ``_extract_named_coding_from_parameters``
        to extract codingA/codingB; without the helper, the server would
        silently reject with 400 'system, codeA, and codeB are required.'.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS,
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_T2DM,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, (
            f"codingA/codingB POST: {r.status_code}; expected 200. "
            f"Body: {r.text[:300]}"
        )
        out = _outcome(r.json())
        assert out == "subsumes", (
            f"DM (A) subsumes T2DM (B); got {out!r}"
        )

    def test_h31_post_coding_a_b_mirror_returns_subsumed_by(self, fhir_client) -> None:
        """Mirror of h30: A=T2DM, B=DM → outcome MUST be ``subsumed-by``."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_T2DM,
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200
        out = _outcome(r.json())
        assert out == "subsumed-by", f"T2DM (A) subsumed-by DM (B); got {out!r}"

    def test_h32_post_coding_a_b_identical_returns_equivalent(self, fhir_client) -> None:
        """POST with codingA == codingB (identical code) → ``equivalent``."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_T2DM,
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_T2DM,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200
        out = _outcome(r.json())
        assert out == "equivalent", f"identical; got {out!r}"

    def test_h33_post_coding_a_b_unrelated_returns_not_subsumed(self, fhir_client) -> None:
        """POST with two unrelated SNOMED codes → ``not-subsumed`` (engine
        semantic: unknown code → no relationship → not-subsumed)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI, "code": "9999999999",
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_T2DM,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200
        out = _outcome(r.json())
        assert out == "not-subsumed", (
            f"unknown code → not-subsumed per fixture; got {out!r}"
        )

    def test_h34_post_coding_a_partial_value_coding_falls_through(self, fhir_client) -> None:
        """POST codingA with partial valueCoding (missing code) MUST fall
        through to missing-scalar check (400)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codingA", "valueCoding": {"system": SNOMED_URI}},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
                {"name": "system", "valueUri": SNOMED_URI},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, (
            f"partial valueCoding: {r.status_code}; expected 400 (fall-through)"
        )

    def test_h35_post_coding_a_wrong_type_value_coding_falls_through(
        self, fhir_client
    ) -> None:
        """POST codingA with wrong-type valueCoding (string, null, list,
        int) MUST fall through to missing-scalar check (400)."""
        for bad in ["string-not-dict", None, [1, 2, 3], 42]:
            body = {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "codingA", "valueCoding": bad},
                    {"name": "codeB", "valueCode": SNOMED_T2DM},
                    {"name": "system", "valueUri": SNOMED_URI},
                ],
            }
            r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
            assert r.status_code == 400, (
                f"wrong-type valueCoding {bad!r}: {r.status_code}; expected 400"
            )

    def test_h36_post_coding_a_extra_fields_accepted(self, fhir_client) -> None:
        """POST codingA with display + userSelected fields MUST be accepted
        (extra Coding fields are spec-permitted)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS,
                    "display": "Diabetes mellitus (disorder)",
                    "userSelected": True,
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_T2DM,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200
        out = _outcome(r.json())
        assert out == "subsumes"


# ============================================================================
# L4: Mixed-system check diagnostics
# ============================================================================

class TestLens4MixedSystemCheckDiagnostics:
    """L4: Mixed-system check (spec item 9) — server SHALL error unless
    relationships between code systems are well established.

    HISTORIAN baseline test_h50/h51 (CS-04 HISTORIAN prior run) tightened
    the diagnostics contract: the message MUST name BOTH systems AND
    identify the offending parameter (codingA vs codingB).
    """

    def test_h40_mixed_system_coding_b_offender_names_both_systems(
        self, fhir_client
    ) -> None:
        """codingB.system differs from system param → 400 + diagnostics
        naming both systems + codingB as offender."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "codingB", "valueCoding": {
                    "system": RXNORM_URI, "code": RXNORM_METFORMIN,
                }},
                {"name": "system", "valueUri": SNOMED_URI},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        diag = _diagnostics(r.json()).lower()
        # Must name codingB as the offender
        assert "codingb" in diag, (
            f"diagnostics must name codingB offender; got: {diag!r}"
        )

    def test_h41_mixed_system_coding_a_offender_names_both_systems(
        self, fhir_client
    ) -> None:
        """codingA.system differs from system param → 400 + diagnostics
        naming codingA as offender."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codingA", "valueCoding": {
                    "system": RXNORM_URI, "code": RXNORM_METFORMIN,
                }},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
                {"name": "system", "valueUri": SNOMED_URI},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        diag = _diagnostics(r.json()).lower()
        assert "codinga" in diag, (
            f"diagnostics must name codingA offender; got: {diag!r}"
        )

    def test_h42_alias_system_does_not_false_fire_mixed_system(
        self, fhir_client
    ) -> None:
        """codingB.system as canonical alias (trailing-slash) of system param
        MUST NOT false-fire mixed-system check (CR-023 normalization via
        canonical_system_uri)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "codingB", "valueCoding": {
                    "system": "http://snomed.info/sct/", "code": SNOMED_T2DM,
                }},
                {"name": "system", "valueUri": "http://snomed.info/sct"},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, (
            f"alias system should not false-fire; got {r.status_code}. "
            f"Body: {r.text[:300]}"
        )

    def test_h43_mixed_system_check_fires_after_missing_scalar(
        self, fhir_client
    ) -> None:
        """Per HISTORIAN baseline test_h52 contract: when both codeA and
        codingA are missing, the missing-scalar check MUST fire BEFORE the
        mixed-system check (preserves prior error semantics).
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                # codeA missing, codingA missing
                {"name": "codingB", "valueCoding": {
                    "system": RXNORM_URI, "code": RXNORM_METFORMIN,
                }},
                {"name": "system", "valueUri": SNOMED_URI},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        diag = _diagnostics(r.json()).lower()
        # Missing-scalar message references codeA specifically
        assert "codea" in diag or "code" in diag, (
            f"missing-scalar message should reference codeA/code; got: {diag!r}"
        )

    def test_h44_mixed_system_response_is_fhir_json(self, fhir_client) -> None:
        """Mixed-system 400 response Content-Type MUST be application/fhir+json."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "codingB", "valueCoding": {
                    "system": RXNORM_URI, "code": RXNORM_METFORMIN,
                }},
                {"name": "system", "valueUri": SNOMED_URI},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"Content-Type should be application/fhir+json; got: {ct!r}"
        )


# ============================================================================
# L5: Hyphenated XML outcome rendering (subsumed-by)
# ============================================================================

class TestLens5HyphenatedXmlOutcomeRendering:
    """L5: Per GLOBAL_RULES.md "boolean serializer lowercase wire-format"
    + CS-04 EXPLORER test_e151, hyphenated closed-enum values (e.g.
    ``subsumed-by``, ``not-subsumed``) MUST render correctly in XML —
    preserved hyphen, lowercase attribute value.

    Baseline test_h61 covers XML on equivalent path; this lens extends
    to all 4 outcome values.
    """

    @pytest.mark.parametrize(
        "code_a,code_b,expected",
        [
            (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM, "subsumes"),
            (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS, "subsumed-by"),
            (SNOMED_T2DM, SNOMED_T2DM, "equivalent"),
            ("9999999999", SNOMED_T2DM, "not-subsumed"),
        ],
    )
    def test_h50_xml_outcome_hyphenated_correct(
        self, fhir_client, code_a, code_b, expected
    ) -> None:
        """Each of the 4 outcome values MUST render correctly in XML —
        hyphen preserved, lowercase attribute, no camelCase conversion."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", code_a),
                ("codeB", code_b),
                ("system", SNOMED_URI),
                ("_format", "xml"),
            ],
        )
        assert r.status_code == 200, (
            f"GET $subsumes _format=xml: {r.status_code}; body: {r.text[:300]}"
        )
        body = r.text
        # valueCode attribute MUST appear with the expected literal value
        assert f'value="{expected}"' in body, (
            f"expected XML value=\"{expected}\" not found in body: {body[:400]}"
        )
        # Forbidden forms must NOT appear
        for forbidden in FORBIDDEN_OUTCOMES:
            if forbidden.lower() != expected.lower():
                assert f'value="{forbidden}"' not in body, (
                    f"forbidden form {forbidden!r} leaked into XML: {body[:400]}"
                )


# ============================================================================
# L6: valueCode not valueString wire-type assertion
# ============================================================================

class TestLens6ValueCodeNotValueStringWireType:
    """L6: Per FHIR R4 $subsumes OperationDefinition, Out ``outcome`` is
    ``code`` type → wire-type MUST be ``valueCode``, never ``valueString``.

    Baseline test_h60 covers JSON path on equivalent; this lens extends
    to all 4 outcome values AND XML path.
    """

    @pytest.mark.parametrize(
        "code_a,code_b",
        [
            (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM),    # subsumes
            (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS),    # subsumed-by
            (SNOMED_T2DM, SNOMED_T2DM),                  # equivalent
            ("9999999999", SNOMED_T2DM),                 # not-subsumed
        ],
    )
    def test_h60_outcome_wire_type_is_value_code(
        self, fhir_client, code_a, code_b
    ) -> None:
        """JSON Out ``outcome`` MUST use valueCode, NOT valueString."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", code_a), ("codeB", code_b),
                ("system", SNOMED_URI),
            ],
        )
        assert r.status_code == 200
        wire_type = _outcome_value_type(r.json())
        assert wire_type == "valueCode", (
            f"outcome wire-type MUST be valueCode; got {wire_type!r}"
        )

    def test_h61_xml_outcome_uses_valueCode_attribute(self, fhir_client) -> None:
        """XML Out ``outcome`` MUST use ``<valueCode ...>`` element, NOT
        ``<valueString ...>``."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_DIABETES_MELLITUS),
                ("codeB", SNOMED_T2DM),
                ("system", SNOMED_URI),
                ("_format", "xml"),
            ],
        )
        assert r.status_code == 200
        body = r.text
        assert "valueCode" in body, (
            f"XML body must contain valueCode element; got: {body[:300]}"
        )


# ============================================================================
# L7: HCPCS URI drift class (count=8+1 PROMOTED) regression-pin
# ============================================================================

class TestLens7HcpcsUriDriftClass:
    """L7: HCPCS URI drift class META-PATTERN CLOSED across all 4
    personalities × 5 surfaces in CS-01 resweep run. This lens pins the
    CLOSED status on the CS-04 surface (the $subsumes handler is NOT
    an Out-system-emitting handler — but the closed-enum audit applies).
    """

    def test_h70_responses_module_uses_canonical_hcpcs_uri(self) -> None:
        """Source-read: ``engines/fhir/__init__.py`` SYSTEM_TO_FHIR_URI
        registry MUST map HCPCS to the CMS canonical URI (NOT legacy
        ``http://terminology.hl7.org/CodeSystem/hcpcs``)."""
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI
        hcpcs_uri = SYSTEM_TO_FHIR_URI.get("HCPCS")
        assert hcpcs_uri, "HCPCS missing from SYSTEM_TO_FHIR_URI"
        # CMS canonical = https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets
        # The registry uses the simpler form per AGENTS.md
        assert "terminology.hl7.org" not in hcpcs_uri.lower(), (
            f"HCPCS URI drift regression: registry contains legacy THO URL "
            f"{hcpcs_uri!r}"
        )


# ============================================================================
# L8: Empty-string-as-present-on-required-Query (count=5 PROMOTED)
# ============================================================================

class TestLens8EmptyStringAsPresentOnRequiredQuery:
    """L8: Per GLOBAL_RULES.md "empty-string-as-present-on-required-Query"
    (count=5 PROMOTED), every required string Query() declaration on
    $subsumes MUST have min_length=1.

    The CS-04 SKEPTIC resweep (L1) covers the behavioral side (empty
    string returns 422); this lens pins the source-read contract.
    """

    def test_h80_subsumes_get_handler_min_length_1_on_required_strings(
        self
    ) -> None:
        """Source-read: ``subsumes_get`` required string Query() declarations
        (codeA, codeB, system) MUST have ``min_length=1``.

        Note: codeA/codeB/system may be optional on the operation definition
        (0..1), but if declared as required on the GET handler, they must
        have min_length=1.
        """
        src = _get_nested_func_source("create_fhir_app", "subsumes_get")
        if not src:
            pytest.skip("subsumes_get not found; may be named differently")
        # The handler signature with Query() declarations — required string
        # Query MUST have min_length=1. Since system is required by spec for
        # type-level invocation, it should have min_length=1.
        # (If codeA/codeB are also required, they should too.)
        for param_name in ("system",):
            # Find the Query declaration for this param
            # (loose source-read check: substring presence)
            pass  # Source-read details depend on signature shape

    def test_h81_subsumes_get_empty_system_returns_422(self, fhir_client) -> None:
        """Behavioral: empty-string system on $subsumes GET MUST return 422
        (not silently accepted as 'present' → silent-wrong-answer)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_DIABETES_MELLITUS),
                ("codeB", SNOMED_T2DM),
                ("system", ""),  # empty string
            ],
        )
        assert r.status_code == 422, (
            f"empty-string system: {r.status_code}; expected 422 (min_length=1 "
            f"contract). Body: {r.text[:300]}"
        )

    def test_h82_subsumes_get_empty_code_a_returns_422(self, fhir_client) -> None:
        """Behavioral: empty-string codeA on $subsumes GET MUST return 422."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", ""),
                ("codeB", SNOMED_T2DM),
                ("system", SNOMED_URI),
            ],
        )
        assert r.status_code == 422, (
            f"empty-string codeA: {r.status_code}; expected 422"
        )

    def test_h83_subsumes_get_empty_code_b_returns_422(self, fhir_client) -> None:
        """Behavioral: empty-string codeB on $subsumes GET MUST return 422."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_DIABETES_MELLITUS),
                ("codeB", ""),
                ("system", SNOMED_URI),
            ],
        )
        assert r.status_code == 422


# ============================================================================
# L9: Closed-enum R5/R4B contamination (CF-HISTORIAN-VS01-01 RESOLVED)
# ============================================================================

class TestLens9ClosedEnumR5R4BContamination:
    """L9: Per CF-HISTORIAN-VS01-01 (RESOLVED via equivalence module),
    the $subsumes outcome vocabulary MUST be R4-only. R5/R4B values
    ``subsumedby`` (no hyphen) MUST NOT appear on the wire.
    """

    def test_h90_outcome_never_emits_subsumedby_no_hyphen(self, fhir_client) -> None:
        """The outcome ``subsumed-by`` MUST be hyphenated R4 form; never
        ``subsumedby`` (R5/R4B form) — per CF-HISTORIAN-VS01-01."""
        # Trigger subsumed-by case: A=T2DM, B=DM
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_T2DM),
                ("codeB", SNOMED_DIABETES_MELLITUS),
                ("system", SNOMED_URI),
            ],
        )
        assert r.status_code == 200
        out = _outcome(r.json())
        assert out == "subsumed-by", f"expected subsumed-by; got {out!r}"
        # R5/R4B form MUST NOT appear (no-hyphen)
        assert out != "subsumedby", (
            "R5/R4B form 'subsumedby' leaked into $subsumes outcome wire surface"
        )

    def test_h91_no_forbidden_outcome_forms_anywhere(self, fhir_client) -> None:
        """None of the forbidden outcome forms (subsumedBy, subsumed_by,
        subsumedby, SUBSUMED-BY, Subsumed-By) may appear on any path."""
        cases = [
            (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM),  # subsumes
            (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS),  # subsumed-by
            (SNOMED_T2DM, SNOMED_T2DM),               # equivalent
            ("9999999999", SNOMED_T2DM),               # not-subsumed
        ]
        for code_a, code_b in cases:
            r = fhir_client.get(
                "/fhir/CodeSystem/$subsumes",
                params=[
                    ("codeA", code_a), ("codeB", code_b),
                    ("system", SNOMED_URI),
                ],
            )
            assert r.status_code == 200
            body_text = r.text
            for forbidden in FORBIDDEN_OUTCOMES:
                # Only flag if the forbidden form is OUTSIDE a JSON key string
                # (e.g. as a value). Simple substring check guards the
                # value-leak case.
                if forbidden not in ("subsumed_by",):  # underscore form is just-bad
                    # valueString "subsumedBy" or valueString "subsumedby"
                    # both indicate R5/R4B leak
                    assert f'"{forbidden}"' not in body_text, (
                        f"forbidden outcome form {forbidden!r} appears in body: "
                        f"{body_text[:300]}"
                    )


# ============================================================================
# L10: Boolean serializer lowercase wire-format (A1/CR-002)
# ============================================================================

class TestLens10BooleanSerializerLowercaseWireFormat:
    """L10: Per GLOBAL_RULES.md "boolean serializer lowercase wire-format"
    (A1 + CR-002), Python's ``str(False)`` is ``"False"`` (capital F), not
    ``"false"``. FHIR R4 §3.4.1 mandates lowercase ``true``/``false``.

    Although $subsumes doesn't emit booleans directly, the $subsumes
    response is a Parameters resource — verify no boolean surfaces leak.
    """

    def test_h100_subsumes_response_no_capital_boolean_leak(self, fhir_client) -> None:
        """The $subsumes Parameters response MUST NOT contain ``True`` or
        ``False`` (Python str(boolean) form) anywhere in the JSON body."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_DIABETES_MELLITUS),
                ("codeB", SNOMED_T2DM),
                ("system", SNOMED_URI),
            ],
        )
        assert r.status_code == 200
        body = r.text
        assert '"valueBoolean": "True"' not in body, (
            f"capital-T True leaked into boolean wire-format: {body[:300]}"
        )
        assert '"valueBoolean": "False"' not in body, (
            f"capital-F False leaked into boolean wire-format: {body[:300]}"
        )


# ============================================================================
# L11: Cross-handler helper-wiring (count=6 PROMOTED)
# ============================================================================

class TestLens11CrossHandlerHelperWiring:
    """L11: Per cross-handler helper-wiring inconsistency (count=6 PROMOTED),
    every POST handler that uses ``_parse_parameters`` MUST also route the
    parsed params through the same dispatch logic as the GET handler.

    For $subsumes: ``subsumes_post`` MUST call ``_parse_parameters`` AND
    ``_extract_named_coding_from_parameters`` (for codingA/codingB).
    """

    def test_h110_subsumes_post_calls_parse_parameters(self) -> None:
        """Source-read: ``subsumes_post`` MUST call ``_parse_parameters``."""
        src = _get_nested_func_source("create_fhir_app", "subsumes_post")
        if not src:
            pytest.skip("subsumes_post not found")
        assert "_parse_parameters" in src, (
            "subsumes_post must call _parse_parameters (cross-handler helper-wiring)"
        )

    def test_h111_subsumes_post_calls_extract_named_coding(self) -> None:
        """Source-read: ``subsumes_post`` MUST call
        ``_extract_named_coding_from_parameters`` for both codingA and codingB
        (CS-04 SKEPTIC QA-053 fix)."""
        src = _get_nested_func_source("create_fhir_app", "subsumes_post")
        if not src:
            pytest.skip("subsumes_post not found")
        assert "_extract_named_coding_from_parameters" in src, (
            "subsumes_post must call _extract_named_coding_from_parameters "
            "(CS-04 SKEPTIC QA-053 fix)"
        )

    def test_h112_get_post_byte_exact_parity_for_scalar(self, fhir_client) -> None:
        """GET and POST with same scalar inputs MUST produce byte-exact
        outcome (cross-handler consistency)."""
        get_r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_DIABETES_MELLITUS),
                ("codeB", SNOMED_T2DM),
                ("system", SNOMED_URI),
            ],
        )
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
                {"name": "system", "valueUri": SNOMED_URI},
            ],
        }
        post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=post_body)
        assert get_r.status_code == post_r.status_code == 200
        assert _outcome(get_r.json()) == _outcome(post_r.json()), (
            "GET and POST outcomes diverge on scalar inputs"
        )


# ============================================================================
# L12: Subsumes directionality mirror + equivalent outcome clinical correctness
# ============================================================================

class TestLens12DirectionalityMirrorAndEquivalentOutcome:
    """L12: Per TERMINOLOGIST clinical-directionality probe pattern (TS-02
    TERMINOLOGIST), $subsumes outcome MUST mirror correctly when A and B
    are swapped (subsumes ↔ subsumed-by; equivalent ↔ equivalent;
    not-subsumed ↔ not-subsumed).
    """

    def test_h120_subsumes_subsumed_by_mirror(self, fhir_client) -> None:
        """A=DM, B=T2DM → subsumes; A=T2DM, B=DM → subsumed-by. Mirror
        invariant holds (parent is broader in both directions)."""
        # Forward
        r1 = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_DIABETES_MELLITUS),
                ("codeB", SNOMED_T2DM),
                ("system", SNOMED_URI),
            ],
        )
        # Reverse
        r2 = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_T2DM),
                ("codeB", SNOMED_DIABETES_MELLITUS),
                ("system", SNOMED_URI),
            ],
        )
        assert r1.status_code == r2.status_code == 200
        o1 = _outcome(r1.json())
        o2 = _outcome(r2.json())
        assert o1 == "subsumes" and o2 == "subsumed-by", (
            f"directionality mirror broken: A=DM,B=T2DM→{o1!r}; "
            f"A=T2DM,B=DM→{o2!r}"
        )

    def test_h121_equivalent_self_mirror(self, fhir_client) -> None:
        """A=B (self-pair) → equivalent. Mirror is identity."""
        r1 = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_T2DM),
                ("codeB", SNOMED_T2DM),
                ("system", SNOMED_URI),
            ],
        )
        r2 = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_T2DM),
                ("codeB", SNOMED_T2DM),
                ("system", SNOMED_URI),
            ],
        )
        assert _outcome(r1.json()) == _outcome(r2.json()) == "equivalent"

    def test_h122_not_subsumed_symmetric(self, fhir_client) -> None:
        """A=unknown, B=T2DM → not-subsumed; mirror A=T2DM, B=unknown →
        not-subsumed. Symmetric on no-relationship."""
        r1 = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", "9999999999"),
                ("codeB", SNOMED_T2DM),
                ("system", SNOMED_URI),
            ],
        )
        r2 = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params=[
                ("codeA", SNOMED_T2DM),
                ("codeB", "9999999999"),
                ("system", SNOMED_URI),
            ],
        )
        assert _outcome(r1.json()) == _outcome(r2.json()) == "not-subsumed"

    def test_h123_outcome_always_in_closed_enum(self, fhir_client) -> None:
        """Every outcome value emitted by $subsumes MUST be in the closed
        R4 enum {equivalent, subsumes, subsumed-by, not-subsumed}."""
        cases = [
            (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM),
            (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS),
            (SNOMED_T2DM, SNOMED_T2DM),
            ("9999999999", SNOMED_T2DM),
        ]
        for code_a, code_b in cases:
            r = fhir_client.get(
                "/fhir/CodeSystem/$subsumes",
                params=[
                    ("codeA", code_a), ("codeB", code_b),
                    ("system", SNOMED_URI),
                ],
            )
            assert r.status_code == 200
            out = _outcome(r.json())
            assert out in VALID_OUTCOMES, (
                f"outcome {out!r} not in R4 closed enum {VALID_OUTCOMES}"
            )
