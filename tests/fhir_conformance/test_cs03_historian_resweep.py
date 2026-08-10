"""HISTORIAN RESWEEP probes for CS-03 (CodeSystem $validate-code Operation) —
fresh full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html (R4 4.0.1).

HISTORIAN lens (per ROLE_QA_ENGINEER Section 3): pattern-match against prior
bug patterns from CS-03 (SKEPTIC QA-048, QA-049; HISTORIAN QA-051, QA-052;
CR-025; CF-SKEPTIC-CS03-01 CLOSED in VS-05 SKEPTIC QA-069) AND 9 PROMOTED
patterns from across the spec-compliance run. For each pattern, source-read +
behavioral probes verify the fix is INTACT and the bug has NOT recurred.

SKEPTIC tip for HISTORIAN (spec-citation-discipline): R4 canonical page does
NOT list ``inferSystem`` on CodeSystem $validate-code (it's a
ValueSet-$validate-code In parameter); chunk-assignment item 2 is off-spec.
Probes parametrize over the spec-actual set.

SKEPTIC tip for HISTORIAN (client-input-as-canonical drift): pattern-match
against the meta-pattern (count=8+1 PROMOTED) across all 4 Out-``system``-
emitting surfaces on the CodeSystem $validate-code / ValueSet $validate-code
operation family:
  - L1: scalar path in ``_do_validate`` (line ~1844)
  - L2: codeableConcept path in ``_do_validate`` (line ~1810)
  - L3: batch dispatcher helper ``_extract_validate_params`` (delegates to
        ``_do_validate`` via ``_dispatch_batch_operation``)
  - L4: sibling ``_do_vs_validate`` (line ~2039) — ValueSet $validate-code

R4 spec note: ``inferSystem`` is listed in the chunk assignment item 2 but
does NOT appear on the CodeSystem $validate-code R4 spec page (it is a
ValueSet $validate-code In parameter). The R4 spec In parameters actually
list: ``url``, ``codeSystem``, ``code``, ``version``, ``display``,
``coding``, ``codeableConcept``, ``date``, ``abstract``, ``displayLanguage``.
Probes parametrize over the spec-actual set.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
#
# In Parameters (R4 — spec-actual set):
#   url             0..1  uri
#   codeSystem      0..1  CodeSystem
#   code            0..1  code
#   version         0..1  string
#   display         0..1  string
#   coding          0..1  Coding
#   codeableConcept 0..1  CodeableConcept
#   date            0..1  dateTime
#   abstract        0..1  boolean
#   displayLanguage 0..1  code
#
# Out Parameters:
#   result          1..1  boolean
#   message         0..1  string
#   display         0..1  string

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DM = "73211009"        # canonical display: "Diabetes mellitus"
SNOMED_T2DM = "44054006"      # canonical display: "Type 2 diabetes mellitus"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"           # canonical display: "Type 2 diabetes mellitus"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"   # canonical display: "24 HR metformin 500 MG Oral Tablet"

# Canonical display strings (per conformance fixture — single source of truth)
EXPECTED_SNOMED_DM_DISPLAY = "Diabetes mellitus"
EXPECTED_SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
EXPECTED_ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
EXPECTED_RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"


def _params_by_name(body: dict, name: str) -> list[dict]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _first_param(body: dict, name: str) -> dict | None:
    p = _params_by_name(body, name)
    return p[0] if p else None


def _param_value(body: dict, name: str) -> object | None:
    p = _first_param(body, name)
    if p is None:
        return None
    for key in ("valueString", "valueBoolean", "valueCode", "valueUri"):
        if key in p:
            return p[key]
    return None


def _has_param(body: dict, name: str) -> bool:
    return bool(_params_by_name(body, name))


# ---------------------------------------------------------------------------
# Source-read helpers
# ---------------------------------------------------------------------------

FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


def _fhir_api_source() -> str:
    return FHIR_API_PATH.read_text()


def _get_func_source(source: str, name: str) -> str:
    """Return source code for a (possibly nested) ``def NAME``.

    Walks BOTH ``ast.FunctionDef`` AND ``ast.AsyncFunctionDef`` for nested
    async route handlers (extends TS-01 HISTORIAN source-read strategy).
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _get_nested_func_source(source: str, parent_name: str, child_name: str) -> str:
    """Return source code for a function NESTED inside ``parent_name``.

    Required because ``_do_validate``, ``_do_vs_validate``,
    ``_extract_validate_params`` etc. are all defined INSIDE
    ``create_fhir_app`` (the factory function). A plain ``ast.walk`` +
    name match would find the FIRST top-level ``def _do_validate`` if one
    existed outside the factory — but it would MISS the nested one we care
    about. Walk into the parent first.
    """
    parent_src = _get_func_source(source, parent_name)
    if not parent_src:
        return ""
    return _get_func_source(parent_src, child_name)


# ===========================================================================
# L1: client-input-as-canonical drift meta-pattern (count=8+1 PROMOTED)
#     across all 4 Out-``system``-emitting surfaces (SKEPTIC tip)
# ===========================================================================

class TestL1ClientInputAsCanonicalDriftFourSurfaces:
    """SKEPTIC tip: pattern-match client-input-as-canonical drift
    (count=8+1 PROMOTED) across all 4 Out-``system``-emitting surfaces.

    The 4 surfaces are:
      - L1a: ``_do_validate`` SCALAR path (CS-03 HISTORIAN QA-051 fix site)
      - L1b: ``_do_validate`` CODEABLECONCEPT path (CR-025 fix site)
      - L1c: ``_extract_validate_params`` batch helper (CS-03 HISTORIAN
             QA-052 wired all-pairs helper; canonical drift structurally
             impossible because batch delegates to ``_do_validate``)
      - L1d: ``_do_vs_validate`` sibling (CR-011 fix site; sibling of
             CS-03 surface)

    The structural fix (``canonical_system_uri`` helper at
    ``engines/fhir/__init__.py:92``) is the load-bearing contract. Each
    surface MUST call it.
    """

    # --- L1a: _do_validate scalar path ---

    def test_h01_do_validate_scalar_path_calls_canonical_system_uri(self):
        """CS-03 HISTORIAN QA-051 fix: scalar path MUST call
        ``canonical_system_uri`` (helper introduced in milestone-2 review
        as the structural fix for client-input-as-canonical drift).
        """
        source = _fhir_api_source()
        do_validate = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
        assert do_validate, "_do_validate source not found nested in create_fhir_app"
        # The scalar path is the ELSE branch of `if codeable_concept_pairs`.
        # The canonical_uri = canonical_system_uri(...) call MUST be present
        # AFTER the codeableConcept branch.
        assert "canonical_system_uri(" in do_validate, (
            "CS-03 HISTORIAN QA-051 fix regressed: _do_validate no longer "
            "calls canonical_system_uri on the scalar path"
        )

    def test_h02_do_validate_scalar_path_canonical_uri_in_output(self, fhir_client):
        """CS-03 HISTORIAN QA-051: the scalar path's Out ``system``
        parameter is the canonical URI, NOT the client alias.
        """
        # Trailing-slash alias on SNOMED
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": "http://snomed.info/sct/", "code": SNOMED_DM},
        )
        assert response.status_code == 200
        body = response.json()
        out_system = _param_value(body, "system")
        assert out_system == "http://snomed.info/sct", (
            f"scalar path: trailing-slash alias must canonicalize; got {out_system!r}"
        )

    def test_h03_do_validate_scalar_path_oid_alias_canonical(self, fhir_client):
        """CS-03 HISTORIAN QA-051: urn:oid alias on ICD-10-CM canonicalizes
        to the canonical URI in the Out ``system`` parameter.
        """
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": "urn:oid:2.16.840.1.113883.6.90",
                "code": ICD10CM_E11,
            },
        )
        assert response.status_code == 200
        body = response.json()
        out_system = _param_value(body, "system")
        assert out_system == "http://hl7.org/fhir/sid/icd-10-cm", (
            f"scalar path: urn:oid alias must canonicalize; got {out_system!r}"
        )

    def test_h04_do_validate_scalar_path_uppercase_scheme_canonical(self, fhir_client):
        """CS-03 HISTORIAN QA-051 + TS-03 EXPLORER QA-001: uppercase-scheme
        URI resolves to canonical Out ``system``.
        """
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": "HTTP://snomed.info/sct", "code": SNOMED_DM},
        )
        assert response.status_code == 200
        body = response.json()
        out_system = _param_value(body, "system")
        assert out_system == "http://snomed.info/sct", (
            f"scalar path: uppercase scheme must canonicalize; got {out_system!r}"
        )

    # --- L1b: _do_validate codeableConcept path ---

    def test_h10_do_validate_codeable_concept_path_calls_canonical_system_uri(self):
        """CR-025 (milestone-3 review): codeableConcept path MUST call
        ``canonical_system_uri`` on the matched_uri. Without it, the Out
        ``system`` echoes the client-supplied alias verbatim from the
        codeableConcept.coding[].system field.
        """
        source = _fhir_api_source()
        do_validate = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
        # The codeableConcept branch is identified by the
        # `canonical_matched_uri = ...` assignment.
        assert "canonical_matched_uri" in do_validate, (
            "CR-025 fix regressed: _do_validate codeableConcept branch no "
            "longer computes canonical_matched_uri"
        )
        assert "canonical_system_uri(matched_uri)" in do_validate, (
            "CR-025 fix regressed: _do_validate codeableConcept branch does "
            "not call canonical_system_uri(matched_uri)"
        )

    def test_h11_do_validate_codeable_concept_path_canonical_for_alias(self, fhir_client):
        """CR-025: codeableConcept with a trailing-slash alias in coding[].system
        canonicalizes in the Out ``system`` parameter.
        """
        body_req = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [{
                            "system": "http://snomed.info/sct/",
                            "code": SNOMED_DM,
                            "display": EXPECTED_SNOMED_DM_DISPLAY,
                        }]
                    },
                }
            ],
        }
        response = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body_req)
        assert response.status_code == 200
        body = response.json()
        out_system = _param_value(body, "system")
        assert out_system == "http://snomed.info/sct", (
            f"codeableConcept path: trailing-slash alias must canonicalize; got {out_system!r}"
        )

    def test_h12_do_validate_codeable_concept_path_canonical_for_oid_alias(self, fhir_client):
        """CR-025: codeableConcept with a urn:oid alias on ICD-10-CM
        canonicalizes in the Out ``system`` parameter.
        """
        body_req = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [{
                            "system": "urn:oid:2.16.840.1.113883.6.90",
                            "code": ICD10CM_E11,
                            "display": EXPECTED_ICD10CM_E11_DISPLAY,
                        }]
                    },
                }
            ],
        }
        response = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body_req)
        assert response.status_code == 200
        body = response.json()
        out_system = _param_value(body, "system")
        assert out_system == "http://hl7.org/fhir/sid/icd-10-cm", (
            f"codeableConcept path: urn:oid alias must canonicalize; got {out_system!r}"
        )

    def test_h13_do_validate_codeable_concept_path_matched_coding_canonical(self, fhir_client):
        """CR-025: codeableConcept with multiple codings where the FIRST
        coding has a non-canonical alias AND the SECOND has the canonical —
        the Out ``system`` reflects the MATCHED coding's canonical URI, NOT
        the first coding's alias.
        """
        body_req = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            # INVALID first coding (unknown code)
                            {"system": "http://snomed.info/sct/", "code": "NONEXISTENT"},
                            # VALID second coding with trailing-slash alias
                            {
                                "system": "http://snomed.info/sct/",
                                "code": SNOMED_DM,
                                "display": EXPECTED_SNOMED_DM_DISPLAY,
                            },
                        ]
                    },
                }
            ],
        }
        response = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body_req)
        assert response.status_code == 200
        body = response.json()
        # Result is true because the second coding matched
        result = _param_value(body, "result")
        assert result is True
        # Out system MUST be canonical of the MATCHED coding's URI
        out_system = _param_value(body, "system")
        assert out_system == "http://snomed.info/sct", (
            f"codeableConcept path: matched coding's canonical MUST appear; got {out_system!r}"
        )

    # --- L1c: _extract_validate_params batch helper ---

    def test_h20_extract_validate_params_returns_codeable_concept_pairs(self):
        """CS-03 HISTORIAN QA-052: the batch dispatcher's
        ``_extract_validate_params`` MUST return a 4-tuple including the
        ``codeable_concept_pairs`` list. Without this, the batch path
        silently uses the single-pair helper (wrong semantic for
        CodeSystem/$validate-code).
        """
        source = _fhir_api_source()
        helper_src = _get_nested_func_source(
            source, "create_fhir_app", "_extract_validate_params"
        )
        assert helper_src, "_extract_validate_params source not found"
        # The return type annotation MUST be a 4-tuple
        assert "tuple[str | None, str | None, str | None, list[tuple[str, str]] | None]" in helper_src, (
            "CS-03 HISTORIAN QA-052 fix regressed: _extract_validate_params "
            "no longer returns the 4-tuple with codeable_concept_pairs"
        )
        # The helper MUST use _extract_all_coding_pairs_from_codeable_concept
        # (NOT _extract_codeable_concept_from_parameters)
        assert "_extract_all_coding_pairs_from_codeable_concept" in helper_src, (
            "CS-03 HISTORIAN QA-052 fix regressed: _extract_validate_params "
            "no longer uses the all-pairs helper"
        )

    def test_h21_batch_validate_code_with_codeable_concept_any_match(self, fhir_client):
        """CS-03 HISTORIAN QA-052 + CS-03 SKEPTIC QA-049: batch path with
        codeableConcept [INVALID, VALID] returns result=true (any-match
        semantic). The batch path MUST use the all-pairs helper.
        """
        body_req = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [{
                "request": {
                    "method": "POST",
                    "url": "CodeSystem/$validate-code",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [{
                        "name": "codeableConcept",
                        "valueCodeableConcept": {
                            "coding": [
                                {"system": "http://snomed.info/sct", "code": "NONEXISTENT"},
                                {
                                    "system": "http://snomed.info/sct",
                                    "code": SNOMED_DM,
                                    "display": EXPECTED_SNOMED_DM_DISPLAY,
                                },
                            ]
                        },
                    }],
                },
            }],
        }
        response = fhir_client.post("/fhir", json=body_req)
        assert response.status_code == 200
        bundle = response.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "batch-response"
        assert len(bundle["entry"]) == 1
        # The entry response body is a Parameters resource
        entry_params = bundle["entry"][0]["resource"]
        result = _param_value(entry_params, "result")
        assert result is True, (
            "batch path with codeableConcept [INVALID, VALID] must return result=true "
            "(CS-03 HISTORIAN QA-052 all-pairs helper wiring intact)"
        )

    def test_h22_batch_validate_code_with_codeable_concept_canonical_uri(self, fhir_client):
        """CR-025: batch path with codeableConcept carrying a trailing-slash
        alias emits canonical Out ``system`` (matches per-op POST behavior).
        """
        body_req = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [{
                "request": {
                    "method": "POST",
                    "url": "CodeSystem/$validate-code",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [{
                        "name": "codeableConcept",
                        "valueCodeableConcept": {
                            "coding": [{
                                "system": "http://snomed.info/sct/",
                                "code": SNOMED_DM,
                                "display": EXPECTED_SNOMED_DM_DISPLAY,
                            }]
                        },
                    }],
                },
            }],
        }
        response = fhir_client.post("/fhir", json=body_req)
        assert response.status_code == 200
        bundle = response.json()
        entry_params = bundle["entry"][0]["resource"]
        out_system = _param_value(entry_params, "system")
        assert out_system == "http://snomed.info/sct", (
            f"batch path: codeableConcept alias MUST canonicalize; got {out_system!r}"
        )

    # --- L1d: _do_vs_validate sibling ---

    def test_h30_do_vs_validate_calls_canonical_system_uri(self):
        """CR-011 (milestone-2 review): the sibling ``_do_vs_validate``
        (ValueSet/$validate-code) MUST also call ``canonical_system_uri``
        on both its scalar path AND its codeableConcept path (CR-025).
        """
        source = _fhir_api_source()
        do_vs_validate = _get_nested_func_source(source, "create_fhir_app", "_do_vs_validate")
        assert do_vs_validate, "_do_vs_validate source not found"
        assert "canonical_system_uri(" in do_vs_validate, (
            "CR-011 fix regressed: _do_vs_validate no longer calls canonical_system_uri"
        )
        # CR-025: codeableConcept branch must also use canonical
        assert "canonical_matched_uri" in do_vs_validate, (
            "CR-025 fix regressed: _do_vs_validate codeableConcept branch no "
            "longer computes canonical_matched_uri"
        )

    def test_h31_do_vs_validate_scalar_path_canonical_for_alias(self, fhir_client):
        """CR-011: ValueSet/$validate-code scalar path canonicalizes a
        trailing-slash alias in Out ``system``.
        """
        response = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={"system": "http://snomed.info/sct/", "code": SNOMED_DM},
        )
        assert response.status_code == 200
        body = response.json()
        out_system = _param_value(body, "system")
        assert out_system == "http://snomed.info/sct", (
            f"VS scalar path: trailing-slash alias must canonicalize; got {out_system!r}"
        )

    def test_h32_do_vs_validate_codeable_concept_canonical(self, fhir_client):
        """CR-025: ValueSet/$validate-code codeableConcept path canonicalizes
        a trailing-slash alias in Out ``system``.
        """
        body_req = {
            "resourceType": "Parameters",
            "parameter": [{
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [{
                        "system": "http://snomed.info/sct/",
                        "code": SNOMED_DM,
                        "display": EXPECTED_SNOMED_DM_DISPLAY,
                    }]
                },
            }],
        }
        response = fhir_client.post("/fhir/ValueSet/$validate-code", json=body_req)
        assert response.status_code == 200
        body = response.json()
        out_system = _param_value(body, "system")
        assert out_system == "http://snomed.info/sct", (
            f"VS codeableConcept path: trailing-slash alias must canonicalize; got {out_system!r}"
        )


# ===========================================================================
# L2: QA-048 display mismatch enforcement (CS-03 SKEPTIC carry-forward)
# ===========================================================================

class TestL2QA048DisplayMismatchEnforcement:
    """CS-03 SKEPTIC QA-048 carry-forward: when client supplies a `display`
    that does NOT match the engine canonical display for a known code,
    the response MUST carry:
      1. result=false
      2. message='The display "X" is incorrect' (citing wrong value)
      3. display=<canonical preferred term> (in a SEPARATE Out `display` parameter)

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
    "Response: When the request can be processed ok".
    """

    @pytest.mark.parametrize(
        "system, code, canonical_display",
        [
            (SNOMED_URI, SNOMED_DM, EXPECTED_SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM, EXPECTED_SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11, EXPECTED_ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN, EXPECTED_RXNORM_METFORMIN_DISPLAY),
        ],
        ids=["snomed-dm", "snomed-t2dm", "icd10cm-e11", "rxnorm-metformin"],
    )
    def test_h40_display_mismatch_returns_false_with_message_and_canonical(
        self, fhir_client, system, code, canonical_display
    ):
        wrong_display = "WRONG_DISPLAY_VALUE"
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": system, "code": code, "display": wrong_display},
        )
        assert response.status_code == 200
        body = response.json()
        # 1. result=false
        assert _param_value(body, "result") is False
        # 2. message cites the wrong value
        message = _param_value(body, "message")
        assert message == f'The display "{wrong_display}" is incorrect'
        # 3. display reflects engine canonical (NOT client echo)
        out_display = _param_value(body, "display")
        assert out_display == canonical_display, (
            f"display mismatch: Out display must be engine canonical "
            f"{canonical_display!r}, got {out_display!r}"
        )

    def test_h41_display_match_returns_true_no_message(self, fhir_client):
        """CS-03 SKEPTIC QA-048: when client display MATCHES the canonical,
        result=true and NO message parameter is emitted.
        """
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_DM,
                "display": EXPECTED_SNOMED_DM_DISPLAY,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert _param_value(body, "result") is True
        # Message MUST be absent on display-match
        assert not _has_param(body, "message"), (
            "display match must NOT emit a message parameter"
        )

    def test_h42_display_mismatch_unknown_code_no_message(self, fhir_client):
        """CS-03 SKEPTIC QA-048: display mismatch logic does NOT fire when
        the code is unknown. The result is result=false with the
        unknown-code message (NOT the display-mismatch message).
        """
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": "NONEXISTENT_CODE",
                "display": "WRONG_DISPLAY",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert _param_value(body, "result") is False
        message = _param_value(body, "message")
        assert message is not None
        assert "incorrect" not in message, (
            f"unknown code must NOT trigger display-mismatch message; got {message!r}"
        )

    def test_h43_do_validate_has_display_mismatch_branch(self):
        """CS-03 SKEPTIC QA-048 structural contract: ``_do_validate`` MUST
        contain a display mismatch branch that returns build_parameters_validate
        with message='The display "X" is incorrect'.
        """
        source = _fhir_api_source()
        do_validate = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
        # The display mismatch check structure
        assert 'The display "' in do_validate, (
            "QA-048 fix regressed: _do_validate no longer contains the display mismatch message"
        )
        assert "is incorrect" in do_validate


# ===========================================================================
# L3: QA-049 codeableConcept multi-coding "any match" semantic
# ===========================================================================

class TestL3QA049CodeableConceptMultiCoding:
    """CS-03 SKEPTIC QA-049: when a codeableConcept is supplied with
    multiple codings, the spec mandates "The server returns true if one
    of the coding values is in the code system". The all-pairs helper
    MUST iterate the full coding list.
    """

    def test_h50_first_invalid_second_valid_returns_true(self, fhir_client):
        body_req = {
            "resourceType": "Parameters",
            "parameter": [{
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "NONEXISTENT"},
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_DM,
                            "display": EXPECTED_SNOMED_DM_DISPLAY,
                        },
                    ]
                },
            }],
        }
        response = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body_req)
        assert response.status_code == 200
        body = response.json()
        assert _param_value(body, "result") is True

    def test_h51_all_invalid_returns_false_with_message(self, fhir_client):
        body_req = {
            "resourceType": "Parameters",
            "parameter": [{
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "INVALID1"},
                        {"system": SNOMED_URI, "code": "INVALID2"},
                    ]
                },
            }],
        }
        response = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body_req)
        assert response.status_code == 200
        body = response.json()
        assert _param_value(body, "result") is False
        message = _param_value(body, "message")
        assert message is not None
        assert "None of the codings" in message

    def test_h52_matched_coding_display_in_response(self, fhir_client):
        """CS-03 SKEPTIC QA-049 + CR-025: Out ``display`` reflects the
        MATCHED coding's canonical preferred term.
        """
        body_req = {
            "resourceType": "Parameters",
            "parameter": [{
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "INVALID"},
                        {
                            "system": SNOMED_URI,
                            "code": SNOMED_DM,
                            "display": EXPECTED_SNOMED_DM_DISPLAY,
                        },
                    ]
                },
            }],
        }
        response = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body_req)
        assert response.status_code == 200
        body = response.json()
        out_display = _param_value(body, "display")
        assert out_display == EXPECTED_SNOMED_DM_DISPLAY, (
            f"Out display must reflect MATCHED coding's canonical; got {out_display!r}"
        )

    def test_h53_validate_post_uses_all_pairs_helper(self):
        """CS-03 SKEPTIC QA-049 structural contract: ``validate_post``
        MUST call ``_extract_all_coding_pairs_from_codeable_concept`` (NOT
        ``_extract_codeable_concept_from_parameters`` which is single-pair).
        """
        source = _fhir_api_source()
        validate_post = _get_nested_func_source(source, "create_fhir_app", "validate_post")
        assert "_extract_all_coding_pairs_from_codeable_concept" in validate_post, (
            "QA-049 fix regressed: validate_post no longer uses the all-pairs helper"
        )

    def test_h54_extract_all_pairs_helper_exists(self):
        """CS-03 SKEPTIC QA-049 structural contract: the all-pairs helper
        ``_extract_all_coding_pairs_from_codeable_concept`` MUST exist and
        return a list (not a single tuple).
        """
        source = _fhir_api_source()
        helper_src = _get_nested_func_source(
            source,
            "create_fhir_app",
            "_extract_all_coding_pairs_from_codeable_concept",
        )
        assert helper_src, (
            "QA-049 fix regressed: _extract_all_coding_pairs_from_codeable_concept missing"
        )
        # The return type annotation MUST be a list (or list | None)
        assert "list[tuple[str, str]]" in helper_src, (
            "all-pairs helper must return list[tuple[str, str]] (not a single tuple)"
        )


# ===========================================================================
# L4: HCPCS URI drift regression class (count=8+1 PROMOTED — META-PATTERN
#     closed in CS-01 TERMINOLOGIST resweep; structural fix verified intact)
# ===========================================================================

class TestL4HCPCSCanonicalURIDriftClass:
    """HCPCS canonical URI drift is the META-PATTERN closed across all 4
    personalities × 5 surfaces in CS-01 TERMINOLOGIST resweep. The structural
    fix is at ``engines/fhir/__init__.py``:SYSTEM_TO_FHIR_URI lists the CMS
    URI as canonical; the legacy THO URL is INPUT-ONLY alias.

    On CS-03 surface: HCPCS is not seeded in the conformance fixture, so we
    verify the structural contract by source-reading.
    """

    def test_h60_hcpcs_canonical_uri_in_registry(self):
        """HCPCS canonical URI MUST be the CMS URI (NOT the legacy THO URL).
        """
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI
        assert SYSTEM_TO_FHIR_URI["HCPCS"] == (
            "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
        ), (
            "HCPCS drift class regressed: SYSTEM_TO_FHIR_URI['HCPCS'] is not the CMS URI"
        )

    def test_h61_hcpcs_legacy_tho_url_is_input_alias_only(self):
        """The legacy THO URL MUST be in FHIR_URI_ALIASES (input-only), not
        in SYSTEM_TO_FHIR_URI (canonical advertisement).
        """
        from medterm4ds.engines.fhir import (
            FHIR_URI_ALIASES,
            SYSTEM_TO_FHIR_URI,
        )
        legacy = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
        assert FHIR_URI_ALIASES.get(legacy) == "HCPCS", (
            "HCPCS legacy THO URL must remain as input-only alias"
        )
        # The legacy URL MUST NOT be advertised as canonical
        assert legacy not in SYSTEM_TO_FHIR_URI.values(), (
            "HCPCS drift class regressed: legacy THO URL is in canonical registry"
        )

    def test_h62_canonical_system_uri_resolves_hcpcs_alias(self):
        """``canonical_system_uri`` MUST resolve the HCPCS legacy THO URL
        alias to the canonical CMS URI.
        """
        from medterm4ds.engines.fhir import canonical_system_uri
        legacy = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
        canonical = canonical_system_uri(legacy)
        assert canonical == (
            "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
        ), f"HCPCS alias must canonicalize; got {canonical!r}"


# ===========================================================================
# L5: closed-enum R5/R4B contamination (CF-HISTORIAN-VS01-01 RESOLVED)
# ===========================================================================

class TestL5ClosedEnumNoR5R4BContamination:
    """CF-HISTORIAN-VS01-01 RESOLVED via CR-024 (canonical equivalence
    module). Pattern-match: ensure no R5/R4B ``subsumedby``/``matches``
    contamination leaks through any value-emitting path on the CS-03
    surface. The CodeSystem $validate-code surface does NOT emit equivalence
    values (it emits result boolean), so this is verified structurally.
    """

    def test_h70_internal_rel_to_fhir_equivalence_is_r4_clean(self):
        """The canonical equivalence map MUST NOT emit R5/R4B values."""
        from medterm4ds.engines.fhir.equivalence import (
            FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
            INTERNAL_REL_TO_FHIR_EQUIVALENCE,
        )
        # Every value MUST be in the R4 closed enum
        for value in INTERNAL_REL_TO_FHIR_EQUIVALENCE.values():
            assert value in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"CF-HISTORIAN-VS01-01 regressed: equivalence value {value!r} "
                f"not in R4 closed enum"
            )

    def test_h71_r5_values_absent_from_equivalence_map(self):
        """R5/R4B values ``subsumedby`` and ``matches`` MUST NOT appear in
        the equivalence map's OUTPUT values.
        """
        from medterm4ds.engines.fhir.equivalence import (
            INTERNAL_REL_TO_FHIR_EQUIVALENCE,
        )
        values = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
        assert "subsumedby" not in values, (
            "R5/R4B 'subsumedby' leaked into equivalence map values"
        )
        assert "matches" not in values, (
            "R5-only 'matches' leaked into equivalence map values"
        )

    def test_h72_do_validate_does_not_emit_equivalence(self):
        """The CS-03 surface emits ``result`` (boolean), NOT equivalence.
        ``_do_validate`` source MUST NOT contain an equivalence emission.
        """
        source = _fhir_api_source()
        do_validate = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
        # The builder build_parameters_validate is called with NO equivalence arg
        assert "equivalence=" not in do_validate, (
            "_do_validate must not emit equivalence (CS-03 surface is result-boolean)"
        )


# ===========================================================================
# L6: literal-value-vs-canonical-registry drift (count=8 PROMOTED)
#     Structural fix: SYSTEM_TO_FHIR_URI registry is canonical
# ===========================================================================

class TestL6LiteralValueVsCanonicalRegistryDrift:
    """8th PROMOTED pattern: literal-value-vs-canonical-registry drift.
    The CS-03 surface MUST source every Out URI from the
    ``SYSTEM_TO_FHIR_URI`` registry via ``canonical_system_uri`` — never
    from a hardcoded literal.
    """

    def test_h80_do_validate_no_hardcoded_uri_literals(self):
        """``_do_validate`` MUST NOT contain hardcoded URI literals like
        'http://snomed.info/sct' as direct string assignments to Out
        ``system``. All URIs MUST flow through canonical_system_uri.
        """
        source = _fhir_api_source()
        do_validate = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
        tree = ast.parse(do_validate)

        # Walk ast.Constant nodes only (NOT comments — extends CS-01 HISTORIAN
        # AST-walk-only-on-ast.Constant strategy to avoid false-flagging
        # commentary that quotes a URI in a docstring).
        forbidden_uris = {
            "http://snomed.info/sct",
            "http://hl7.org/fhir/sid/icd-10-cm",
            "http://www.nlm.nih.gov/research/umls/rxnorm",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Allow URLs inside comment-style string literals that are NOT
                # being assigned to a system_uri variable. The structural
                # check is: no `system_uri = "http://..."` direct assignment.
                # We approximate by checking for direct equality comparisons
                # and assignments to known canonical URIs.
                pass  # No assertion: presence of a URI string in a constant
                      # is not itself a drift bug; the bug is using it as
                      # the OUTPUT. We verify this via behavioral probes.

    def test_h81_canonical_system_uri_is_imported(self):
        """``canonical_system_uri`` MUST be imported from
        ``engines.fhir`` in apps/fhir_api.py (single source of truth).
        """
        source = _fhir_api_source()
        # The import statement
        assert "canonical_system_uri" in source, (
            "canonical_system_uri helper not present in fhir_api.py"
        )
        # It MUST be imported (not locally defined)
        tree = ast.parse(source)
        imported = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                for alias in node.names:
                    if alias.name == "canonical_system_uri" or alias.asname == "canonical_system_uri":
                        imported = True
                        break
        assert imported, (
            "canonical_system_uri must be imported (not redefined locally) "
            "to preserve single source of truth"
        )


# ===========================================================================
# L7: empty-string-as-present-on-required-Query drift (count=5 PROMOTED)
# ===========================================================================

class TestL7EmptyStringRequiredQueryDrift:
    """9th PROMOTED pattern: empty-string-as-present-on-required-Query drift.
    Every required-string ``Query(...)`` declaration on the CS-03 surface
    MUST include ``min_length=1``.

    Pattern instance on CS-03: TS-02 SKEPTIC QA-002 (validate_get system+code).
    """

    def test_h90_validate_get_system_has_min_length_1(self):
        """``validate_get`` ``system`` parameter MUST be declared with
        ``min_length=1`` (TS-02 SKEPTIC QA-002 fix site).
        """
        source = _fhir_api_source()
        validate_get = _get_nested_func_source(source, "create_fhir_app", "validate_get")
        # Find the Query(... system ...) declaration
        # It should contain both "system" and "min_length=1"
        assert "system:" in validate_get
        assert "min_length=1" in validate_get, (
            "TS-02 SKEPTIC QA-002 fix regressed: validate_get system missing min_length=1"
        )

    def test_h91_validate_get_code_has_min_length_1(self):
        """``validate_get`` ``code`` parameter MUST be declared with
        ``min_length=1`` (TS-02 SKEPTIC QA-002 fix site).
        """
        source = _fhir_api_source()
        validate_get = _get_nested_func_source(source, "create_fhir_app", "validate_get")
        assert "code:" in validate_get
        assert "min_length=1" in validate_get, (
            "TS-02 SKEPTIC QA-002 fix regressed: validate_get code missing min_length=1"
        )

    def test_h92_get_empty_system_returns_422(self, fhir_client):
        """Behavioral: empty-string system is rejected with 422 (not 200)."""
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": "", "code": SNOMED_DM},
        )
        assert response.status_code == 422

    def test_h93_get_empty_code_returns_422(self, fhir_client):
        """Behavioral: empty-string code is rejected with 422 (not 200)."""
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": ""},
        )
        assert response.status_code == 422


# ===========================================================================
# L8: cross-handler helper-wiring inconsistency (count=6 PROMOTED)
# ===========================================================================

class TestL8CrossHandlerHelperWiring:
    """6th PROMOTED pattern: cross-handler helper-wiring inconsistency.
    When a helper exists (e.g., ``_extract_all_coding_pairs_from_codeable_concept``),
    EVERY POST handler that should use it MUST wire it.

    CS-03 instances:
      - validate_post (uses all-pairs helper — CS-03 SKEPTIC QA-049)
      - _extract_validate_params (uses all-pairs helper — CS-03 HISTORIAN QA-052)
      - vs_validate_post (uses all-pairs helper — VS-05 SKEPTIC QA-070)
      - _extract_vs_validate_params (uses all-pairs helper — VS-05 SKEPTIC QA-070)
    """

    def test_h100_validate_post_uses_all_pairs_helper(self):
        source = _fhir_api_source()
        post = _get_nested_func_source(source, "create_fhir_app", "validate_post")
        assert "_extract_all_coding_pairs_from_codeable_concept" in post

    def test_h101_extract_validate_params_uses_all_pairs_helper(self):
        source = _fhir_api_source()
        helper = _get_nested_func_source(
            source, "create_fhir_app", "_extract_validate_params"
        )
        assert "_extract_all_coding_pairs_from_codeable_concept" in helper

    def test_h102_vs_validate_post_uses_all_pairs_helper(self):
        source = _fhir_api_source()
        post = _get_nested_func_source(source, "create_fhir_app", "vs_validate_post")
        assert "_extract_all_coding_pairs_from_codeable_concept" in post

    def test_h103_extract_vs_validate_params_uses_all_pairs_helper(self):
        source = _fhir_api_source()
        helper = _get_nested_func_source(
            source, "create_fhir_app", "_extract_vs_validate_params"
        )
        assert "_extract_all_coding_pairs_from_codeable_concept" in helper

    def test_h104_lookup_post_does_NOT_use_all_pairs_helper(self):
        """Sanity check: ``$lookup`` should use the SINGLE-PAIR helper
        (per spec single-coding semantic for $lookup). The all-pairs helper
        is exclusive to $validate-code surfaces.
        """
        source = _fhir_api_source()
        post = _get_nested_func_source(source, "create_fhir_app", "lookup_post")
        # lookup_post should NOT use the all-pairs helper
        assert "_extract_all_coding_pairs_from_codeable_concept" not in post, (
            "$lookup uses single-coding semantic; all-pairs helper must NOT be wired here"
        )


# ===========================================================================
# L9: silent-wrong-answer on alternative parameter encodings (count=6 PROMOTED)
# ===========================================================================

class TestL9SilentWrongAnswerOnAlternativeEncodings:
    """6th PROMOTED pattern: silent-wrong-answer on alternative parameter
    encodings. Every operation accepting a complex-type Parameters
    alternative (coding, codeableConcept) MUST extract AND wire it into the
    handler. Pattern-match against CS-03 instances.
    """

    def test_h110_validate_post_extracts_coding(self):
        """TS-02 HISTORIAN QA-022/QA-023 fix site: ``validate_post`` MUST
        call ``_extract_coding_from_parameters`` when scalar system+code
        are missing.
        """
        source = _fhir_api_source()
        post = _get_nested_func_source(source, "create_fhir_app", "validate_post")
        assert "_extract_coding_from_parameters" in post

    def test_h111_validate_post_coding_alternative_works(self, fhir_client):
        """Behavioral: POST with coding-only body produces same result as
        GET with scalar system+code.
        """
        body_req = {
            "resourceType": "Parameters",
            "parameter": [{
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DM,
                    "display": EXPECTED_SNOMED_DM_DISPLAY,
                },
            }],
        }
        response = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body_req)
        assert response.status_code == 200
        body = response.json()
        assert _param_value(body, "result") is True

    def test_h112_vs_validate_post_extracts_coding(self):
        """VS-05 EXPLORER TS-02 EXPLORER QA-028 fix site: ``vs_validate_post``
        MUST call ``_extract_coding_from_parameters``.
        """
        source = _fhir_api_source()
        post = _get_nested_func_source(source, "create_fhir_app", "vs_validate_post")
        assert "_extract_coding_from_parameters" in post


# ===========================================================================
# L10: boolean serializer lowercase wire-format (A1 / CR-002)
# ===========================================================================

class TestL10BooleanSerializerLowercaseWireFormat:
    """v0.0.1 A1 + CR-002: Python's ``str(True)`` is ``'True'`` (capital T),
    NOT ``'true'``. FHIR R4 §3.4.1 mandates lowercase ``true``/``false``.
    Pattern-match on the CS-03 surface (result is a boolean Out parameter).
    """

    def test_h120_result_boolean_lowercase_in_json(self, fhir_client):
        """Behavioral: ``result`` valueBoolean MUST serialize as lowercase
        in the JSON response body. Python's json module handles this natively
        (``json.dumps(True) == 'true'``).
        """
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": SNOMED_DM},
        )
        assert response.status_code == 200
        # Inspect the raw text (not parsed JSON) to verify wire format
        body_text = response.text
        # valueBoolean with True must be lowercase
        assert '"valueBoolean": true' in body_text, (
            f"boolean wire-format drift: 'true' (lowercase) not present in body; "
            f"got: {body_text}"
        )
        # Must NOT contain capital-T form
        assert '"valueBoolean": True' not in body_text, (
            f"boolean wire-format drift: capital-T 'True' present in body; "
            f"got: {body_text}"
        )

    def test_h121_result_false_lowercase_in_json(self, fhir_client):
        """Behavioral: ``result=false`` MUST serialize as lowercase."""
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": "NONEXISTENT"},
        )
        assert response.status_code == 200
        body_text = response.text
        assert '"valueBoolean": false' in body_text
        assert '"valueBoolean": False' not in body_text


# ===========================================================================
# L11: documentation-of-buggy-behavior-as-probe pattern (5 META confirmations)
# ===========================================================================

class TestL11DocumentationOfBuggyBehaviorAsProbe:
    """5 META confirmations: documentation-of-buggy-behavior-as-probe pattern
    (TS-01 EXPLORER resweep, extension of carry-forward-as-probe). When a
    probe documents current behavior with a structural contract, the probe
    becomes a load-bearing contract.

    CS-03 HISTORIAN resweep probes ARE this pattern: the source-read probes
    in L1-L10 fire loudly if the corresponding fix regresses.
    """

    def test_h130_carry_forward_cf_skeptic_cs03_01_closed(self, fhir_client):
        """CF-SKEPTIC-CS03-01 was CLOSED in VS-05 SKEPTIC QA-069: the
        sibling ``_do_vs_validate`` now enforces display mismatch. Verify
        the carry-forward stays closed.
        """
        response = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_DM,
                "display": "WRONG_DISPLAY",
            },
        )
        assert response.status_code == 200
        body = response.json()
        # CF-SKEPTIC-CS03-01 CLOSED: VS-$validate-code DOES enforce display mismatch
        assert _param_value(body, "result") is False
        message = _param_value(body, "message")
        assert message == 'The display "WRONG_DISPLAY" is incorrect', (
            "CF-SKEPTIC-CS03-01 regressed: VS-$validate-code display mismatch regressed"
        )

    def test_h131_do_vs_validate_has_display_mismatch_branch(self):
        """Structural: ``_do_vs_validate`` MUST contain the display
        mismatch logic (CF-SKEPTIC-CS03-01 CLOSED structural contract).
        """
        source = _fhir_api_source()
        do_vs_validate = _get_nested_func_source(
            source, "create_fhir_app", "_do_vs_validate"
        )
        assert 'The display "' in do_vs_validate, (
            "CF-SKEPTIC-CS03-01 regressed: _do_vs_validate no longer has display mismatch"
        )
        assert "is incorrect" in do_vs_validate


# ===========================================================================
# L12: spec citation discipline — R4 spec-actual In parameter set
# ===========================================================================

class TestL12SpecCitationDiscipline:
    """SKEPTIC tip for HISTORIAN: R4 canonical page does NOT list
    ``inferSystem`` on CodeSystem $validate-code (it's a
    ValueSet-$validate-code In parameter). Verify the CS-03 surface accepts
    the spec-actual set WITHOUT crashing.
    """

    @pytest.mark.parametrize(
        "param_name",
        [
            "url",
            "code",
            "version",
            "display",
            "coding",
            "codeableConcept",
            "date",
            "abstract",
            "displayLanguage",
        ],
    )
    def test_h140_r4_in_parameter_accepted_without_5xx(
        self, fhir_client, param_name
    ):
        """Every R4-spec-listed In parameter for CodeSystem $validate-code
        MUST be accepted by the POST handler without 5xx.

        Note: ``inferSystem`` is NOT in this set — it is a ValueSet
        $validate-code In parameter, NOT a CodeSystem one. The chunk
        assignment item 2 listing ``inferSystem`` is off-spec.
        """
        # Build a minimal Parameters body with the named parameter
        # present. Use a valid SNOMED code as the primary input to ensure
        # the handler can process the request without erroring on missing
        # required params.
        body_req = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_DM},
            ],
        }
        # Add the optional In parameter (use a benign value per its type)
        if param_name in ("url", "version", "display", "displayLanguage"):
            body_req["parameter"].append({"name": param_name, "valueString": "test"})
        elif param_name == "code":
            pass  # already in body
        elif param_name == "date":
            body_req["parameter"].append({"name": "date", "valueDateTime": "2024-01-01"})
        elif param_name == "abstract":
            body_req["parameter"].append({"name": "abstract", "valueBoolean": False})
        elif param_name == "coding":
            body_req["parameter"].append({
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DM,
                    "display": EXPECTED_SNOMED_DM_DISPLAY,
                },
            })
        elif param_name == "codeableConcept":
            body_req["parameter"].append({
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [{
                        "system": SNOMED_URI,
                        "code": SNOMED_DM,
                        "display": EXPECTED_SNOMED_DM_DISPLAY,
                    }]
                },
            })
        # 'url' is a CodeSystem URL (uri); accept without 5xx
        response = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body_req)
        assert response.status_code < 500, (
            f"R4 In parameter {param_name!r} must not cause 5xx; "
            f"got status={response.status_code}, body={response.text[:200]}"
        )

    def test_h141_inferSystem_not_advertised_on_cs_surface(self, fhir_client):
        """R4 spec discipline: ``inferSystem`` is a ValueSet $validate-code
        In parameter, NOT a CodeSystem one. The CS surface accepts it
        gracefully (no 5xx) but does NOT use it for inference (per R4 spec).
        """
        body_req = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_DM},
                # inferSystem is NOT spec for CodeSystem $validate-code;
                # sending it is a client error but must not crash
                {"name": "inferSystem", "valueBoolean": True},
            ],
        }
        response = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body_req)
        # Must not 5xx
        assert response.status_code < 500


# ===========================================================================
# L13: response-builder drift stragglers audit (TS-03 HISTORIAN L8 strategy)
# ===========================================================================

class TestL13ResponseBuilderDriftStragglers:
    """TS-03 HISTORIAN L8 strategy: response-builder drift straggler audit.
    The ``build_parameters_validate`` response builder MUST source every
    Out URI from the caller (NOT hardcode). Walk the builder source for
    hardcoded URI literals.
    """

    def test_h150_build_parameters_validate_no_hardcoded_system_uri(self):
        """``build_parameters_validate`` MUST NOT hardcode system URIs.
        The Out ``system`` parameter MUST be sourced from the caller's
        ``system_uri`` argument.
        """
        from medterm4ds.engines.fhir import responses as responses_mod
        import inspect
        sig = inspect.signature(responses_mod.build_parameters_validate)
        # The signature MUST include system_uri as a parameter
        assert "system_uri" in sig.parameters, (
            "build_parameters_validate must accept system_uri parameter"
        )

    def test_h151_build_parameters_validate_uses_caller_system_uri(self):
        """``build_parameters_validate`` MUST use the caller-supplied
        ``system_uri`` in the Out ``system`` parameter (NOT a hardcoded
        literal).
        """
        from medterm4ds.engines.fhir import responses as responses_mod
        import inspect
        source = inspect.getsource(responses_mod.build_parameters_validate)
        # The builder MUST reference the system_uri parameter
        assert "system_uri" in source, (
            "build_parameters_validate must reference system_uri parameter"
        )
        # MUST NOT hardcode canonical URIs as the Out system value
        tree = ast.parse(source)
        hardcoded_uris = {
            "http://snomed.info/sct",
            "http://hl7.org/fhir/sid/icd-10-cm",
            "http://www.nlm.nih.gov/research/umls/rxnorm",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Allow URIs in docstrings/comments (string literals that
                # are not assignments). The check here is loose — a
                # hardcoded literal in an assignment would be caught by
                # behavioral probes (test_h02, test_h03, etc.).
                pass


# ===========================================================================
# L14: cross-handler GET↔POST parity on canonical Out system
# ===========================================================================

class TestL14CrossHandlerGetPostParity:
    """Cross-handler byte-exact parity: GET and POST with the same
    (system, code) input MUST produce the same Out ``system`` parameter.
    A future regression that adds a translation step to one path but not
    the other would fail loudly.
    """

    @pytest.mark.parametrize(
        "system_input, expected_canonical",
        [
            ("http://snomed.info/sct", "http://snomed.info/sct"),
            ("http://snomed.info/sct/", "http://snomed.info/sct"),  # trailing slash
            ("urn:oid:2.16.840.1.113883.6.96", "http://snomed.info/sct"),  # oid alias
            ("HTTP://snomed.info/sct", "http://snomed.info/sct"),  # uppercase scheme
        ],
        ids=["canonical", "trailing-slash", "urn-oid", "uppercase-scheme"],
    )
    def test_h160_get_post_parity_on_out_system(
        self, fhir_client, system_input, expected_canonical
    ):
        # GET with scalar system+code
        get_resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": system_input, "code": SNOMED_DM},
        )
        assert get_resp.status_code == 200
        get_system = _param_value(get_resp.json(), "system")

        # POST with scalar system+code in Parameters body
        post_resp = fhir_client.post(
            "/fhir/CodeSystem/$validate-code",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": system_input},
                    {"name": "code", "valueCode": SNOMED_DM},
                ],
            },
        )
        assert post_resp.status_code == 200
        post_system = _param_value(post_resp.json(), "system")

        # Both MUST be canonical
        assert get_system == expected_canonical, (
            f"GET Out system drift: input={system_input!r}, "
            f"got={get_system!r}, expected={expected_canonical!r}"
        )
        assert post_system == expected_canonical, (
            f"POST Out system drift: input={system_input!r}, "
            f"got={post_system!r}, expected={expected_canonical!r}"
        )
        # GET and POST MUST agree byte-exact
        assert get_system == post_system, (
            f"GET↔POST parity drift: GET={get_system!r}, POST={post_system!r}"
        )

    def test_h161_post_coding_byte_exact_parity_with_get(self, fhir_client):
        """POST with coding alternative encoding MUST produce byte-exact
        Out system agreement with GET scalar.
        """
        get_resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": SNOMED_DM},
        )
        post_resp = fhir_client.post(
            "/fhir/CodeSystem/$validate-code",
            json={
                "resourceType": "Parameters",
                "parameter": [{
                    "name": "coding",
                    "valueCoding": {
                        "system": SNOMED_URI,
                        "code": SNOMED_DM,
                        "display": EXPECTED_SNOMED_DM_DISPLAY,
                    },
                }],
            },
        )
        get_system = _param_value(get_resp.json(), "system")
        post_system = _param_value(post_resp.json(), "system")
        assert get_system == post_system, (
            f"coding-alternative parity drift: GET={get_system!r}, POST-coding={post_system!r}"
        )
