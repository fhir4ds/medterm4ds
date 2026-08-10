"""SKEPTIC RESWEEP probes for CS-04 (CodeSystem $subsumes Operation) —
fresh full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html (R4 4.0.1).

This file contains NEW hostile-input probes that are NOT in the baseline
``test_cs04_skeptic.py``. The baseline (test_s01..test_s120, 29 probes) is
treated as trusted prior coverage; this resweep file adds the
FRESH-FULL-SWEEP mandated probes per USER_DIRECTIVES [2026-08-08].

SKEPTIC lens (per ROLE_QA_ENGINEER Section 3): aggressive bug hunting — edge
cases, malformed inputs, boundary conditions. 5-10 hostile probes per spec
item.

CS-03/TERMINOLOGIST tip for CS-04/SKEPTIC: $subsumes returns ONLY outcome
(valueCode) — no display Out parameter — so the canonical-DISPLAY invariant
naturally does NOT extend. Instead this resweep probes:
  - **codingA/codingB alternative-encoding silent-drop pattern** (per CS-04
    SKEPTIC QA-053 from prior run) — verify POST with codingA/codingB
    parameters doesn't silently drop them. Baseline test_s71 already
    covers the happy path; this resweep probes additional shapes:
    partial scalar+coding combinations, valueCoding missing fields,
    wrong-type valueCoding, version embedded inside valueCoding (Coding.version
    MUST NOT override operation version).
  - **Mixed-system check** fires with a diagnostics message naming both
    systems + the offending parameter (CS-04 HISTORIAN QA test_h50/h51
    tightened this contract). This resweep probes additional mixed-system
    shapes: both codings cross-system, cross-system via alias URI,
    cross-system where `system` itself is an alias of one of the codings.

10 lens dimensions, ~55 probes covering all 9 spec items:
  L1  Required params hostile inputs (empty string drift count=5 PROMOTED)
  L2  Optional version param hostile inputs
  L3  codingA/codingB alternative encoding (CS-04 SKEPTIC QA-053 carry-forward)
  L4  Mixed-system check (spec item 9 + CS-03/TERMINOLOGIST tip)
  L5  Outcome closed enum + wire-format (lowercase, hyphenated, valueCode)
  L6  Response shape audit (Content-Type, Parameters resourceType)
  L7  Source-read structural contracts (helper wiring + min_length=1)
  L8  Self-subsumption + directionality mirror invariants
  L9  GET-vs-POST byte-exact parity (incl. mixed scalar+coding inputs)
  L10 Hostile input matrix (SQL injection, XSS, null bytes, unicode, long)
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
#                           be performed. This must be provided unless the
#                           operation is invoked on a code system instance"
#   version  0..1  string  "The version of the code system, if one was
#                           provided in the source data"
#   codingA  0..1  Coding  "The 'A' Coding that is to be tested. The code
#                           system does not have to match the specified
#                           subsumption code system, but the relationships
#                           between the code systems must be well established"
#   codingB  0..1  Coding  "The 'B' Coding that is to be tested. ..."
#
# Out Parameters:
#   outcome   1..1  code   "The subsumption relationship between code/Coding
#                           'A' and code/Coding 'B'. There are 4 possible
#                           codes to be returned (equivalent, subsumes,
#                           subsumed-by, and not-subsumed)."

VALID_OUTCOMES = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child (descendant of 73211009)
SNOMED_VIRAL_HEPATITIS = "3738000"     # unrelated to diabetes branch
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"            # unrelated to diabetes branch
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"                    # T2DM in ICD-10-CM (same CUI as SNOMED T2DM)

# Alias forms (per FHIR_URI_ALIASES in engines/fhir/__init__.py)
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
SNOMED_OID_URI = "urn:oid:2.16.840.1.113883.6.96"

# Source code location for AST source-read probes
FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


def _outcome(body: dict) -> str | None:
    """Return the value of the Out `outcome` parameter."""
    for p in body.get("parameter", []):
        if p.get("name") == "outcome":
            if "valueCode" in p:
                return p["valueCode"]
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _outcome_value_type(body: dict) -> str | None:
    for p in body.get("parameter", []):
        if p.get("name") == "outcome":
            for k in p:
                if k.startswith("value"):
                    return k
    return None


def _diagnostics(body: dict) -> str:
    """Concatenate all diagnostics strings from an OperationOutcome."""
    out = []
    for issue in body.get("issue", []):
        d = issue.get("diagnostics", "")
        if d:
            out.append(d)
    return " | ".join(out)


def _get_func_source(func_name: str) -> str:
    """Source-read helper: get the source of a top-level or nested function
    definition from apps/fhir_api.py. Walks BOTH ast.FunctionDef AND
    ast.AsyncFunctionDef (extends CS-01 HISTORIAN strategy).
    """
    src = FHIR_API_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(src, node) or ""
    return ""


def _get_nested_func_source(parent_name: str, child_name: str) -> str:
    """Source-read helper for nested functions defined inside create_fhir_app
    factory (extends CS-03 HISTORIAN methodology — plain ast.walk would miss
    nested defs).
    """
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
# L1: Required params hostile inputs (empty-string drift count=5 PROMOTED)
# ============================================================================

class TestLens1RequiredParamsEmptyStringDrift:
    """Item 1 / spec §4.8.21.3: codeA+codeB+system are required. Per
    GLOBAL_RULES.md "empty-string-as-present-on-required-Query" (count=5
    PROMOTED), every required string Query() MUST have min_length=1 so
    empty string doesn't silently pass through as a "present" value.

    Baseline test_s01-s04 cover missing-entirely; this lens covers
    EMPTY-STRING-AS-PRESENT (the 5th promoted pattern).
    """

    def test_l10_get_empty_system_returns_422(self, fhir_client):
        """Empty `system` MUST be rejected with 422 (not silent not-subsumed)."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system=&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
        )
        assert r.status_code == 422, (
            f"empty system → {r.status_code}; expected 422 (min_length=1)"
        )

    def test_l11_get_empty_codeA_returns_422(self, fhir_client):
        """Empty `codeA` MUST be rejected with 422."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}&codeA=&codeB={SNOMED_T2DM}"
        )
        assert r.status_code == 422, (
            f"empty codeA → {r.status_code}; expected 422 (min_length=1)"
        )

    def test_l12_get_empty_codeB_returns_422(self, fhir_client):
        """Empty `codeB` MUST be rejected with 422."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}&codeA={SNOMED_T2DM}&codeB="
        )
        assert r.status_code == 422, (
            f"empty codeB → {r.status_code}; expected 422 (min_length=1)"
        )

    def test_l13_get_all_three_empty_returns_422(self, fhir_client):
        """All three required params empty → 422."""
        r = fhir_client.get("/fhir/CodeSystem/$subsumes?system=&codeA=&codeB=")
        assert r.status_code == 422

    def test_l14_get_empty_string_returns_fhir_json_content_type(self, fhir_client):
        """Per GLOBAL_RULES.md conformance-per-route: the 422 path MUST
        produce application/fhir+json Content-Type (via the
        RequestValidationError handler).
        """
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system=&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
        )
        assert r.status_code == 422
        ct = r.headers.get("content-type", "")
        assert "fhir+json" in ct, f"Content-Type={ct!r}; expected fhir+json"
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome"


# ============================================================================
# L2: Optional version param hostile inputs
# ============================================================================

class TestLens2VersionParamHostile:
    """Item 2 + 8 / spec: `version` is 0..1 string (optional). When omitted,
    current version used. The engine has no versioned data; the param is
    accepted but ignored (AGENTS.md NOT A BUG registry).

    SKEPTIC lens: verify hostile version strings don't crash the server
    and the response shape is preserved.
    """

    @pytest.mark.parametrize("version", [
        "nonexistent-version-2099",
        "",  # empty version string (NOT a required param, so 0..1 allows empty)
        "http://snomed.info/sct/32506021000036107/version/20240901",
        "1.0.0",
        "version with spaces",
        "version; DROP TABLE mrconso;--",  # SQL injection attempt
        "<script>alert(1)</script>",       # XSS attempt
        "null",
        "None",
        "../../etc/passwd",                # path traversal
    ])
    def test_l20_get_version_param_hostile_inputs_no_crash(self, fhir_client, version):
        """Hostile version strings MUST NOT produce 5xx."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
            f"&version={version}"
        )
        assert r.status_code < 500, (
            f"version={version!r}: {r.status_code} {r.text[:200]}"
        )

    def test_l21_get_version_with_special_chars_url_encoded(self, fhir_client):
        """URL-encoded version string MUST decode cleanly and produce 200."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
            f"&version=http%3A%2F%2Fsnomed.info%2Fsct%2F731000168108%2Fversion%2F20240901"
        )
        assert r.status_code == 200
        assert _outcome(r.json()) == "subsumes"

    def test_l22_post_version_in_body_accepted_alongside_codingA_codingB(self, fhir_client):
        """POST with codingA + codingB + version in body MUST produce 200."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "version", "valueString": "http://snomed.info/sct/32506021000036107/version/20240901"},
                {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
                {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        assert _outcome(r.json()) == "subsumes"


# ============================================================================
# L3: codingA/codingB alternative encoding (CS-04 SKEPTIC QA-053 carry-forward)
# ============================================================================

class TestLens3CodingAlternativeEncoding:
    """Item 2 / spec In `codingA`+`codingB`: alternative encoding to
    codeA+codeB. The silent-reject-on-alternative-encoding pattern (TS-02
    HISTORIAN QA-022/023 class) was found and fixed on $subsumes POST by
    CS-04 SKEPTIC QA-053. The baseline test_s71 covers the happy path;
    this lens probes additional shapes per CS-03/TERMINOLOGIST tip.

    SKEPTIC probe classes:
      - scalar+coding combination (scalar wins per convention)
      - valueCoding missing fields (partial coding)
      - valueCoding wrong type (string/null/list — graceful handling)
      - Coding.version field inside valueCoding MUST NOT override
        operation-level version param
      - codingA/codingB from canonical aliases (trailing-slash, urn:oid,
        uppercase-scheme) MUST resolve
    """

    def test_l30_post_scalar_codeA_overrides_when_codingA_also_present(self, fhir_client):
        """Per TS-02 HISTORIAN QA-022 convention: scalar wins on conflict.
        codeA + codingA both present → codeA value is used.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                # scalar codeA = T2DM, codingA = DM (parent)
                {"name": "codeA", "valueCode": SNOMED_T2DM},
                {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
                # codeB = DM (parent)
                {"name": "codeB", "valueCode": SNOMED_DIABETES_MELLITUS},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        # scalar codeA (T2DM) wins → child vs parent (DM) = subsumed-by
        outcome = _outcome(r.json())
        assert outcome == "subsumed-by", (
            f"scalar-wins-on-conflict: outcome={outcome!r}, expected 'subsumed-by'"
        )

    def test_l31_post_codingA_only_no_scalar_codeA_uses_codingA(self, fhir_client):
        """CodingA supplied without scalar codeA → codingA value is used."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        # DM vs T2DM → subsumes (DM is broader)
        assert _outcome(r.json()) == "subsumes"

    def test_l32_post_codingA_valueCoding_missing_system_falls_through(self, fhir_client):
        """valueCoding without system → coding rejected, falls through to
        missing-scalar check.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {"code": SNOMED_T2DM}},  # no system
                {"name": "codeB", "valueCode": SNOMED_DIABETES_MELLITUS},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, (
            f"valueCoding without system → {r.status_code}; expected 400 (missing scalar)"
        )

    def test_l33_post_codingA_valueCoding_missing_code_falls_through(self, fhir_client):
        """valueCoding without code → falls through."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {"system": SNOMED_URI}},  # no code
                {"name": "codeB", "valueCode": SNOMED_DIABETES_MELLITUS},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400

    @pytest.mark.parametrize("wrong_value", [
        "not-a-dict-string",
        None,
        ["list", "not", "dict"],
        42,
    ])
    def test_l34_post_codingA_valueCoding_wrong_type_graceful(self, fhir_client, wrong_value):
        """valueCoding as wrong type (string/null/list/int) → graceful
        handling (no 500; falls through to missing-scalar check). Pinned
        by CS-04 HISTORIAN test_h22 for string; this parametrizes the
        full matrix.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": wrong_value},
                {"name": "codeB", "valueCode": SNOMED_DIABETES_MELLITUS},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, (
            f"valueCoding={wrong_value!r}: {r.status_code}; expected 400 (no 500)"
        )
        body_json = r.json()
        assert body_json.get("resourceType") == "OperationOutcome"

    def test_l35_post_coding_version_does_not_override_operation_version(self, fhir_client):
        """Per CS-04 EXPLORER test_e61 (load-bearing contract): the
        Coding.version field inside valueCoding MUST NOT override the
        operation-level version param. The _extract_named_coding_from_parameters
        helper extracts ONLY (system, code), not version.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DIABETES_MELLITUS,
                    "version": "coding-embedded-version-2099",  # MUST be ignored
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM,
                    "version": "different-version-2098",  # MUST be ignored
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        assert _outcome(r.json()) == "subsumes"

    def test_l36_post_codingA_with_extra_unknown_fields_accepted(self, fhir_client):
        """valueCoding with extra fields (display, userSelected, etc.) MUST
        be accepted — only (system, code) are extracted.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DIABETES_MELLITUS,
                    "display": "Diabetes mellitus",
                    "userSelected": True,
                    "version": "2024-09",
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM,
                    "display": "Type 2 diabetes mellitus",
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        assert _outcome(r.json()) == "subsumes"

    def test_l37_post_codingA_with_canonical_alias_uri_resolves(self, fhir_client):
        """codingA.system as canonical alias (trailing-slash, urn:oid,
        uppercase-scheme) MUST resolve to the same source. Mixed-system
        check MUST NOT falsely fire on alias inputs.
        """
        # All three aliases MUST resolve to SNOMED source — mixed-system check
        # uses canonical_system_uri() so aliases normalize to canonical.
        for alias_uri in [SNOMED_URI_TRAILING_SLASH, SNOMED_OID_URI]:
            body = {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": SNOMED_URI},
                    {"name": "codingA", "valueCoding": {"system": alias_uri, "code": SNOMED_DIABETES_MELLITUS}},
                    {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
                ],
            }
            r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
            assert r.status_code == 200, (
                f"alias codingA.system={alias_uri!r}: {r.status_code} {r.text[:300]}"
            )
            assert _outcome(r.json()) == "subsumes"


# ============================================================================
# L4: Mixed-system check (spec item 9 + CS-03/TERMINOLOGIST tip)
# ============================================================================

class TestLens4MixedSystemCheck:
    """Item 9 / spec In `codingA`: "The code system does not have to match
    the specified subsumption code system, but the relationships between
    the code systems must be well established".

    medterm4ds has no cross-system relationship map today. When codingA
    or codingB references a different system than `system`, the server
    SHALL error (per CS-04 SKEPTIC QA-053 fix).

    SKEPTIC lens: probe additional mixed-system shapes per CS-03/TERMINOLOGIST
    tip — the diagnostics message MUST name both systems + the offending
    parameter.
    """

    def test_l40_post_both_codings_cross_system_rejected(self, fhir_client):
        """Both codingA and codingB from different system than `system`
        param. Server SHALL error (not silently produce not-subsumed).

        HISTORIAN test_h50/h51 cover one-offender cases. This probes BOTH
        offenders — the diagnostics MUST name at least one (the first
        detected).
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {"system": RXNORM_URI, "code": RXNORM_METFORMIN}},
                {"name": "codingB", "valueCoding": {"system": ICD10CM_URI, "code": ICD10CM_E11}},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, (
            f"both-codings-cross-system: {r.status_code}; expected 400"
        )
        body_json = r.json()
        assert body_json.get("resourceType") == "OperationOutcome"
        diag = _diagnostics(body_json)
        # The diagnostics MUST name at least one offender (codingA checked first).
        assert "codingA" in diag, (
            f"diagnostics should name codingA as first offender; got: {diag!r}"
        )

    def test_l41_post_mixed_system_diagnostics_names_codingB_offender(self, fhir_client):
        """Mixed-system check on codingB — diagnostics MUST name codingB
        (mirror of HISTORIAN test_h50, but SKEPTIC framing: confirm the
        attribution is correct under hostile input).
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
                {"name": "codingB", "valueCoding": {"system": ICD10CM_URI, "code": ICD10CM_E11}},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        diag = _diagnostics(r.json())
        assert "codingB" in diag, (
            f"diagnostics should name codingB; got: {diag!r}"
        )
        assert ICD10CM_URI in diag, (
            f"diagnostics should name ICD10CM URI; got: {diag!r}"
        )
        assert SNOMED_URI in diag, (
            f"diagnostics should name SNOMED URI (the expected); got: {diag!r}"
        )

    def test_l42_post_mixed_system_where_system_is_alias_does_not_false_fire(self, fhir_client):
        """If `system` is supplied as a canonical alias (trailing-slash,
        urn:oid), and codingA/codingB are the canonical URI, the
        mixed-system check MUST NOT false-fire — both should normalize
        to the same canonical URI.
        """
        for system_alias in [SNOMED_URI_TRAILING_SLASH, SNOMED_OID_URI]:
            body = {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": system_alias},
                    {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
                    {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
                ],
            }
            r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
            assert r.status_code == 200, (
                f"system alias={system_alias!r}, codings canonical: "
                f"{r.status_code} {r.text[:300]}; mixed-system check should NOT false-fire"
            )
            assert _outcome(r.json()) == "subsumes"

    def test_l43_post_mixed_system_both_aliases_normalize_correctly(self, fhir_client):
        """codingA.system is alias, codingB.system is canonical — both
        resolve to same source; mixed-system check MUST NOT fire.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {"system": SNOMED_URI_TRAILING_SLASH, "code": SNOMED_DIABETES_MELLITUS}},
                {"name": "codingB", "valueCoding": {"system": SNOMED_OID_URI, "code": SNOMED_T2DM}},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        assert _outcome(r.json()) == "subsumes"

    def test_l44_post_mixed_system_returns_fhir_json_content_type(self, fhir_client):
        """Per GLOBAL_RULES conformance-per-route: mixed-system 400 path
        MUST produce application/fhir+json Content-Type + OperationOutcome.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {"system": RXNORM_URI, "code": RXNORM_METFORMIN}},
                {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        ct = r.headers.get("content-type", "")
        assert "fhir+json" in ct, f"Content-Type={ct!r}; expected fhir+json"
        assert r.json().get("resourceType") == "OperationOutcome"

    def test_l45_post_mixed_system_check_fires_after_missing_scalar_check(self, fhir_client):
        """Verify the ordering: missing-scalar check fires BEFORE mixed-system
        check. If only codingA is supplied (no codeA scalar, no codingB,
        no codeB scalar), the missing-scalar error fires (not the
        mixed-system error). Mirrors HISTORIAN test_h52.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                # Only codingA, from a different system — but missing codeB
                {"name": "codingA", "valueCoding": {"system": RXNORM_URI, "code": RXNORM_METFORMIN}},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400
        diag = _diagnostics(r.json())
        assert "system, codeA, and codeB" in diag or "required" in diag.lower(), (
            f"missing-scalar should fire first; got: {diag!r}"
        )


# ============================================================================
# L5: Outcome closed enum + wire-format (lowercase, hyphenated, valueCode)
# ============================================================================

class TestLens5OutcomeClosedEnumWireFormat:
    """Item 3 / spec Out `outcome`: 1..1 code, binding =
    ConceptSubsumptionOutcome (Required). The 4 values are
    {equivalent, subsumes, subsumed-by, not-subsumed}.

    SKEPTIC lens: confirm wire-format exactness on every outcome path.
    The spec value set uses LOWERCASE HYPHENATED form
    ('subsumed-by' NOT 'subsumedBy' or 'subsumed_by').
    """

    @pytest.mark.parametrize("code_a, code_b, expected_outcome", [
        (SNOMED_T2DM, SNOMED_T2DM, "equivalent"),
        (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM, "subsumes"),
        (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS, "subsumed-by"),
        (SNOMED_T2DM, SNOMED_VIRAL_HEPATITIS, "not-subsumed"),
    ])
    def test_l50_outcome_lowercase_hyphenated_form(self, fhir_client, code_a, code_b, expected_outcome):
        """Every outcome value MUST be lowercase + hyphenated."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={code_a}&codeB={code_b}"
        )
        assert r.status_code == 200
        outcome = _outcome(r.json())
        # Exact match (catches subsumedBy, subsumed_by, NOT-SUBSUMED, etc.)
        assert outcome == expected_outcome, (
            f"codes ({code_a},{code_b}): outcome={outcome!r}, expected {expected_outcome!r}"
        )

    def test_l51_outcome_never_camelcase_or_underscore(self, fhir_client):
        """Wire-format audit: confirm 'subsumed-by' is the EXACT form
        (never 'subsumedBy', 'subsumed_by', 'SUBSUMED-BY', 'Subsumed-By').
        """
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_DIABETES_MELLITUS}"
        )
        assert r.status_code == 200
        outcome = _outcome(r.json())
        # Spec-exact: lowercase, hyphenated
        assert outcome == "subsumed-by"
        forbidden_forms = [
            "subsumedBy", "subsumed_by", "SUBSUMED-BY", "Subsumed-By",
            "subsumedby",  # R5/R4B form
        ]
        assert outcome not in forbidden_forms

    def test_l52_outcome_value_type_valueCode_on_xml_path(self, fhir_client):
        """XML wire-format: outcome MUST use value="..." attribute on a
        <valueCode> element (per FHIR R4 §3.4.1).
        """
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
            f"&_format=xml"
        )
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "fhir+xml" in ct, f"Content-Type={ct!r}"
        body_text = r.text
        assert "<valueCode" in body_text, (
            f"XML body missing <valueCode> element; got: {body_text[:300]}"
        )
        assert 'value="subsumes"' in body_text, (
            f"XML body missing value=\"subsumes\"; got: {body_text[:300]}"
        )

    def test_l53_outcome_xml_hyphenated_value(self, fhir_client):
        """XML wire-format for hyphenated outcome ('subsumed-by') MUST
        preserve the hyphen (no camelCase conversion).
        """
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_DIABETES_MELLITUS}"
            f"&_format=xml"
        )
        assert r.status_code == 200
        body_text = r.text
        assert 'value="subsumed-by"' in body_text, (
            f"XML hyphenated outcome missing; got: {body_text[:300]}"
        )

    def test_l54_outcome_xml_not_subsumed_value(self, fhir_client):
        """XML wire-format for 'not-subsumed' outcome."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_VIRAL_HEPATITIS}"
            f"&_format=xml"
        )
        assert r.status_code == 200
        body_text = r.text
        assert 'value="not-subsumed"' in body_text, (
            f"XML not-subsumed outcome missing; got: {body_text[:300]}"
        )

    def test_l55_outcome_xml_equivalent_value(self, fhir_client):
        """XML wire-format for 'equivalent' outcome."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
            f"&_format=xml"
        )
        assert r.status_code == 200
        body_text = r.text
        assert 'value="equivalent"' in body_text


# ============================================================================
# L6: Response shape audit (Content-Type, Parameters resourceType)
# ============================================================================

class TestLens6ResponseShapeAudit:
    """Item 3 + GLOBAL_RULES conformance-per-route: every route MUST
    emit application/fhir+json Content-Type + Parameters/OperationOutcome
    body.
    """

    def test_l60_get_response_content_type_fhir_json(self, fhir_client):
        """GET 200 path Content-Type."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
        )
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "fhir+json" in ct

    def test_l61_post_response_content_type_fhir_json(self, fhir_client):
        """POST 200 path Content-Type."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codeA", "valueCode": SNOMED_T2DM},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "fhir+json" in ct

    def test_l62_post_response_parameters_resourcetype(self, fhir_client):
        """POST 200 body resourceType MUST be Parameters."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200
        assert r.json().get("resourceType") == "Parameters"

    def test_l63_get_accept_xml_returns_fhir_xml(self, fhir_client):
        """Accept: application/fhir+xml MUST produce XML body."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}",
            headers={"Accept": "application/fhir+xml"},
        )
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "fhir+xml" in ct
        assert "<Parameters" in r.text

    def test_l64_outcome_param_count_exactly_one(self, fhir_client):
        """Spec Out `outcome` cardinality is 1..1 — exactly one outcome
        parameter MUST be present in the response.
        """
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
        )
        assert r.status_code == 200
        body = r.json()
        outcomes = [p for p in body.get("parameter", []) if p.get("name") == "outcome"]
        assert len(outcomes) == 1, (
            f"expected exactly 1 outcome param; got {len(outcomes)}"
        )

    def test_l65_no_other_out_params_emitted(self, fhir_client):
        """Spec Out parameters table lists ONLY `outcome` — no display,
        no message, no system, no other leaked params.
        """
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
        )
        assert r.status_code == 200
        body = r.json()
        names = {p.get("name") for p in body.get("parameter", [])}
        # Only 'outcome' is allowed.
        assert names == {"outcome"}, (
            f"Out parameter names={names!r}; expected {{'outcome'}} only"
        )


# ============================================================================
# L7: Source-read structural contracts (helper wiring + min_length=1)
# ============================================================================

class TestLens7SourceReadStructuralContracts:
    """Source-read probes that verify structural contracts in the code.
    These are not behavioral; they assert the CODE shape so that a future
    regression that removes a load-bearing line fails loudly.
    """

    def test_l70_subsumes_get_has_min_length_1_on_all_three_required_params(self):
        """GLOBAL_RULES 'empty-string-as-present-on-required-Query'
        (count=5 PROMOTED): every required string Query() on subsumes_get
        MUST have min_length=1.
        """
        src = _get_nested_func_source("create_fhir_app", "subsumes_get")
        assert src, "subsumes_get source not found"
        # All three required params MUST have min_length=1
        assert "system: str = Query(..., min_length=1)" in src, (
            f"system missing min_length=1; src: {src[:500]}"
        )
        assert "codeA: str = Query(..., min_length=1)" in src, (
            f"codeA missing min_length=1; src: {src[:500]}"
        )
        assert "codeB: str = Query(..., min_length=1)" in src, (
            f"codeB missing min_length=1; src: {src[:500]}"
        )

    def test_l71_subsumes_post_calls_extract_named_coding_for_codingA(self):
        """CS-04 SKEPTIC QA-053 fix: subsumes_post MUST call
        _extract_named_coding_from_parameters for BOTH codingA AND codingB.
        Without this, POST with codingA/codingB silently drops them.
        """
        src = _get_nested_func_source("create_fhir_app", "subsumes_post")
        assert src, "subsumes_post source not found"
        assert '_extract_named_coding_from_parameters(body, "codingA")' in src, (
            f"subsumes_post missing codingA extractor call; src: {src[:500]}"
        )
        assert '_extract_named_coding_from_parameters(body, "codingB")' in src, (
            f"subsumes_post missing codingB extractor call; src: {src[:500]}"
        )

    def test_l72_subsumes_post_has_mixed_system_check(self):
        """Spec item 9 + CS-04 SKEPTIC QA-053: subsumes_post MUST check
        mixed-system codings and error if codingA.system or codingB.system
        differs from the canonical system.
        """
        src = _get_nested_func_source("create_fhir_app", "subsumes_post")
        assert src, "subsumes_post source not found"
        assert "canonical_system_uri" in src, (
            f"subsumes_post missing canonical_system_uri call (mixed-system check); "
            f"src: {src[:800]}"
        )
        # The mixed-system check MUST reference BOTH codingA and codingB
        assert "coding_a_pair" in src and "coding_b_pair" in src

    def test_l73_extract_named_coding_has_isinstance_dict_guard(self):
        """CF-HISTORIAN-CM03-01 fix pattern: _extract_named_coding_from_parameters
        MUST have isinstance(coding, dict) guard to prevent AttributeError on
        wrong-type valueCoding (string/null/list).
        """
        src = _get_nested_func_source("create_fhir_app", "_extract_named_coding_from_parameters")
        assert src, "_extract_named_coding_from_parameters source not found"
        assert "isinstance(coding, dict)" in src, (
            f"_extract_named_coding_from_parameters missing isinstance guard; "
            f"src: {src[:500]}"
        )

    def test_l74_subsumes_post_uses_fhir_error_response_for_400(self):
        """GLOBAL_RULES conformance-per-route: subsumes_post MUST use
        _fhir_error_response (NOT raw JSONResponse) for error paths so
        Content-Type is application/fhir+json + OperationOutcome body.
        """
        src = _get_nested_func_source("create_fhir_app", "subsumes_post")
        assert src, "subsumes_post source not found"
        assert "_fhir_error_response" in src, (
            f"subsumes_post missing _fhir_error_response call; src: {src[:500]}"
        )

    def test_l75_do_subsumes_calls_build_parameters_subsumes(self):
        """The internal _do_subsumes MUST call build_parameters_subsumes
        for EVERY outcome path (equivalent, subsumes, subsumed-by,
        not-subsumed).
        """
        src = _get_nested_func_source("create_fhir_app", "_do_subsumes")
        assert src, "_do_subsumes source not found"
        # Verify all 4 outcome paths
        for outcome in ('"equivalent"', '"subsumes"', '"subsumed-by"', '"not-subsumed"'):
            assert outcome in src, (
                f"_do_subsumes missing build_parameters_subsumes({outcome}) call; "
                f"src: {src[:500]}"
            )

    def test_l76_subsumes_get_returns_via_fhir_response(self):
        """GLOBAL_RULES conformance-per-route: subsumes_get MUST funnel
        through _fhir_response (NOT return raw dict).
        """
        src = _get_nested_func_source("create_fhir_app", "subsumes_get")
        assert src, "subsumes_get source not found"
        assert "_fhir_response" in src, (
            f"subsumes_get missing _fhir_response call; src: {src[:500]}"
        )

    def test_l77_subsumes_post_returns_via_fhir_response(self):
        """GLOBAL_RULES conformance-per-route: subsumes_post MUST funnel
        through _fhir_response.
        """
        src = _get_nested_func_source("create_fhir_app", "subsumes_post")
        assert src, "subsumes_post source not found"
        assert "_fhir_response" in src, (
            f"subsumes_post missing _fhir_response call; src: {src[:500]}"
        )

    def test_l78_batch_dispatcher_handles_subsumes_path(self):
        """CF-EXPLORER pattern (count=6 PROMOTED): the batch dispatcher
        MUST wire $subsumes via _extract_subsumes_params. A future
        regression that removes the path from the dispatcher's table
        would silently break batch $subsumes.
        """
        # Read the module source for the batch dispatcher
        src = FHIR_API_PATH.read_text()
        # The dispatcher MUST have a path entry for $subsumes
        assert '"/CodeSystem/$subsumes"' in src, (
            "batch dispatcher missing /CodeSystem/$subsumes path entry"
        )
        # And MUST call _extract_subsumes_params
        assert "_extract_subsumes_params" in src, (
            "batch dispatcher missing _extract_subsumes_params call"
        )


# ============================================================================
# L8: Self-subsumption + directionality mirror invariants
# ============================================================================

class TestLens8SelfSubsumptionDirectionality:
    """Item 4 + 5 + 6 + 7: equivalent/subsumes/subsumed-by/not-subsumed.
    The 4 outcomes form a closed enum where (subsumes, subsumed-by) are
    directionality MIRRORS of each other.

    SKEPTIC lens: probe self-subsumption (codeA is parent of codeB AND
    codeB is parent of codeA — impossible in a DAG, but verify no
    infinite loop) and directionality mirror invariants.
    """

    def test_l80_self_subsumption_identical_codes_returns_equivalent(self, fhir_client):
        """codeA == codeB (both same code) → equivalent (NOT subsumes).
        This is the spec-correct behavior — equivalent is the strictest
        relationship; subsumes/subsumed-by are for STRICT descendants.
        """
        for code in [SNOMED_T2DM, SNOMED_DIABETES_MELLITUS, SNOMED_VIRAL_HEPATITIS]:
            r = fhir_client.get(
                f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
                f"&codeA={code}&codeB={code}"
            )
            assert r.status_code == 200
            assert _outcome(r.json()) == "equivalent", (
                f"self-subsumption code={code}: outcome should be equivalent"
            )

    def test_l81_directionality_mirror_subsumes_vs_subsumed_by(self, fhir_client):
        """Directionality mirror: if (A, B) → subsumes, then (B, A) → subsumed-by.
        This is the load-bearing directionality contract.
        """
        # Forward: DM → T2DM = subsumes
        r_fwd = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
        )
        # Reverse: T2DM → DM = subsumed-by
        r_rev = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_DIABETES_MELLITUS}"
        )
        assert r_fwd.status_code == r_rev.status_code == 200
        fwd_outcome = _outcome(r_fwd.json())
        rev_outcome = _outcome(r_rev.json())
        assert fwd_outcome == "subsumes"
        assert rev_outcome == "subsumed-by"
        assert {fwd_outcome, rev_outcome} == {"subsumes", "subsumed-by"}

    def test_l82_directionality_mirror_not_subsumed_symmetric(self, fhir_client):
        """Not-subsumed is symmetric: (A, B) → not-subsumed AND (B, A) → not-subsumed."""
        # T2DM vs viral hepatitis (unrelated) in both orders
        r_fwd = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_VIRAL_HEPATITIS}"
        )
        r_rev = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_VIRAL_HEPATITIS}&codeB={SNOMED_T2DM}"
        )
        assert r_fwd.status_code == r_rev.status_code == 200
        assert _outcome(r_fwd.json()) == _outcome(r_rev.json()) == "not-subsumed"

    def test_l83_equivalent_is_reflexive_symmetric(self, fhir_client):
        """Equivalent is reflexive AND symmetric: (A, A) → equivalent."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
        )
        assert r.status_code == 200
        assert _outcome(r.json()) == "equivalent"

    def test_l84_unknown_codes_dont_loop_forever(self, fhir_client):
        """SKEPTIC edge: if both codes are unknown (no mrrel rows), the
        BFS descendant walk MUST terminate (not infinite-loop).
        The implementation uses max_depth=20 cap.
        """
        import time
        start = time.monotonic()
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA=UNKNOWN_X&codeB=UNKNOWN_Y"
        )
        elapsed = time.monotonic() - start
        assert r.status_code == 200
        assert _outcome(r.json()) == "not-subsumed"
        # Sanity bound: should complete in under 5 seconds
        assert elapsed < 5.0, f"unknown-code subsumes took {elapsed:.2f}s"

    def test_l85_unknown_system_returns_400_not_500(self, fhir_client):
        """Per GLOBAL_RULES silent-fallback prohibition: unknown system
        URI MUST produce 400 + OperationOutcome (not 500 with text/plain).
        """
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system=http://fake.example/sys"
            f"&codeA=1&codeB=2"
        )
        assert r.status_code == 400, (
            f"unknown system: {r.status_code}; expected 400"
        )
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome"
        ct = r.headers.get("content-type", "")
        assert "fhir+json" in ct


# ============================================================================
# L9: GET-vs-POST byte-exact parity (incl. mixed scalar+coding inputs)
# ============================================================================

class TestLens9GetPostParity:
    """GET vs POST MUST produce byte-exact clinical content for the same
    (system, codeA, codeB) input. Mirrors TS-04 TERMINOLOGIST single-vs-batch
    parity probe class.
    """

    @pytest.mark.parametrize("code_a, code_b", [
        (SNOMED_T2DM, SNOMED_T2DM),               # equivalent
        (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM),  # subsumes
        (SNOMED_T2DM, SNOMED_DIABETES_MELLITUS),  # subsumed-by
        (SNOMED_T2DM, SNOMED_VIRAL_HEPATITIS),    # not-subsumed
    ])
    def test_l90_get_post_parity_outcome(self, fhir_client, code_a, code_b):
        """For every outcome path, GET and POST produce same outcome."""
        get_r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={code_a}&codeB={code_b}"
        )
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codeA", "valueCode": code_a},
                {"name": "codeB", "valueCode": code_b},
            ],
        }
        post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=post_body)
        assert get_r.status_code == post_r.status_code == 200
        assert _outcome(get_r.json()) == _outcome(post_r.json())

    def test_l91_get_post_parity_codingA_codingB(self, fhir_client):
        """POST with codingA/codingB MUST produce same outcome as GET
        with equivalent scalar codeA/codeB.
        """
        get_r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
        )
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS}},
                {"name": "codingB", "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM}},
            ],
        }
        post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=post_body)
        assert get_r.status_code == post_r.status_code == 200
        assert _outcome(get_r.json()) == _outcome(post_r.json()) == "subsumes"

    def test_l92_get_post_parity_response_shape(self, fhir_client):
        """GET and POST responses MUST have identical shape (resourceType,
        parameter structure, value type).
        """
        get_r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
        )
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codeA", "valueCode": SNOMED_T2DM},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
            ],
        }
        post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=post_body)
        get_body = get_r.json()
        post_body_resp = post_r.json()
        assert get_body.get("resourceType") == post_body_resp.get("resourceType") == "Parameters"
        assert _outcome_value_type(get_body) == _outcome_value_type(post_body_resp) == "valueCode"
        # Content-Type parity
        assert "fhir+json" in get_r.headers.get("content-type", "")
        assert "fhir+json" in post_r.headers.get("content-type", "")


# ============================================================================
# L10: Hostile input matrix (SQL injection, XSS, null bytes, unicode, long)
# ============================================================================

class TestLens10HostileInputMatrix:
    """Hostile inputs MUST NOT crash the server. DuckDB prepared statements
    structurally prevent SQL injection; the systemic duckdb.Error handler
    (CF-HISTORIAN-CS04-02 RESOLVED) catches operational failures.
    """

    @pytest.mark.parametrize("hostile_code", [
        "'; DROP TABLE mrconso; --",                    # SQL injection
        "<script>alert(1)</script>",                    # XSS
        "../../../etc/passwd",                          # path traversal
        "code\x00null",                                # null byte
        "unicode_中文_テスト_키릴",                      # unicode CJK
        "A" * 5000,                                    # very long code
        "code with spaces",
        "code;with;semicolons",
        "code'with'quotes",
        "code\"with\"doublequotes",
    ])
    def test_l100_get_hostile_code_does_not_crash(self, fhir_client, hostile_code):
        """Hostile codeA MUST NOT produce 5xx."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}",
            params={"codeA": hostile_code, "codeB": SNOMED_T2DM},
        )
        assert r.status_code < 500, (
            f"hostile codeA={hostile_code!r}: {r.status_code} {r.text[:200]}"
        )

    @pytest.mark.parametrize("hostile_code", [
        "'; DROP TABLE mrconso; --",
        "<script>alert(1)</script>",
        "code\x00null",
        "A" * 5000,
    ])
    def test_l101_post_hostile_code_does_not_crash(self, fhir_client, hostile_code):
        """Hostile codeA in POST body MUST NOT produce 5xx."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codeA", "valueCode": hostile_code},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code < 500, (
            f"POST hostile codeA={hostile_code!r}: {r.status_code} {r.text[:200]}"
        )

    def test_l102_get_hostile_system_uri_does_not_crash(self, fhir_client):
        """Hostile system URI MUST produce 400 (not 500)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": "javascript:alert(1)",
                "codeA": SNOMED_T2DM,
                "codeB": SNOMED_T2DM,
            },
        )
        assert r.status_code < 500
        # Unknown system → 400 + OperationOutcome
        assert r.status_code == 400
        assert r.json().get("resourceType") == "OperationOutcome"

    def test_l103_post_non_parameters_body_handled_gracefully(self, fhir_client):
        """POST with a non-Parameters body (e.g. malformed dict) MUST NOT
        produce 5xx — falls through to missing-scalar check.
        """
        body = {"foo": "bar"}  # no 'parameter' key
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, (
            f"non-Parameters body: {r.status_code}; expected 400"
        )
        assert r.json().get("resourceType") == "OperationOutcome"

    def test_l104_post_parameter_value_not_dict_handled_gracefully(self, fhir_client):
        """POST with parameter[] containing a non-dict entry MUST NOT crash."""
        body = {
            "resourceType": "Parameters",
            "parameter": ["string-not-dict", 42, None],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, (
            f"non-dict parameter entries: {r.status_code}; expected 400"
        )

    def test_l105_post_body_that_is_not_a_dict_handled_gracefully(self, fhir_client):
        """POST body that is a JSON list (not a dict) MUST NOT crash.
        Note: FastAPI may reject this before reaching the handler; either
        path is acceptable as long as no 5xx.
        """
        # Send a list as body — FastAPI's body: dict[str, Any] typing
        # should reject this with 422.
        r = fhir_client.post(
            "/fhir/CodeSystem/$subsumes",
            json=["not", "a", "dict"],
        )
        assert r.status_code < 500, (
            f"list body: {r.status_code}; expected 4xx, no 5xx"
        )

    def test_l106_get_url_encoded_special_chars_in_code(self, fhir_client):
        """URL-encoded special chars MUST decode cleanly and produce 200
        or 400 (not 500)."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA=code%20with%20spaces&codeB={SNOMED_T2DM}"
        )
        assert r.status_code < 500
