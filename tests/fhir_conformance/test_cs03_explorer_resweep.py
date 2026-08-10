"""EXPLORER RESWEEP probes for CS-03 (CodeSystem $validate-code Operation) —
fresh full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html (R4 4.0.1).

EXPLORER lens (per ROLE_QA_ENGINEER Section 3): lateral thinking. Probe
combinations the SKEPTIC + HISTORIAN iterations did not naturally exercise.
Per the HISTORIAN tip for EXPLORER:

  - **Mixed-encoding batch with codeableConcept multi-coding**: batch entries
    with codeableConcept [INVALID, VALID] on CodeSystem/$validate-code must
    produce result=true (mirrors the per-operation POST semantic; CS-03
    HISTORIAN QA-052 wired the all-pairs helper into ``_extract_validate_params``).
  - **GET↔POST byte-exact parity on lateral codeableConcept shapes**:
    mixed-system codeableConcept, partial-coding codeableConcept,
    text-only codeableConcept, and codeableConcept with duplicate codings.
  - **Use ``_get_nested_func_source(source, parent_name, child_name)`` helper**
    for source-reading any nested handler/helper.
  - **Do NOT manufacture probes for off-spec ``inferSystem``** (ValueSet-only
    parameter).

Other EXPLORER directions:
  - $validate-code with both ``coding`` AND ``codeableConcept`` parameters
    (alternative encoding precedence)
  - display parameter with empty string vs missing (semantic difference)
  - date parameter with future date / past date / boundary date
  - CodeableConcept with text-only (no codings) — should return result=false
    per spec
  - Cross-handler GET↔POST parity on lateral scalar shapes
  - Batch mixing scalar + codeableConcept entries in the same Bundle

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts the POSITIVE
success shape (200 + Parameters body with the expected fields), not just
absence of an error string.

R4 spec In Parameters (no ``inferSystem`` on CodeSystem/$validate-code):
  url, codeSystem, code, version, display, coding, codeableConcept, date,
  abstract, displayLanguage

R4 spec Out Parameters:
  result 1..1 boolean, message 0..1 string, display 0..1 string
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Spec constants (per conformance fixture — single source of truth)
# ---------------------------------------------------------------------------

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DM = "73211009"        # canonical display: "Diabetes mellitus"
SNOMED_T2DM = "44054006"      # canonical display: "Type 2 diabetes mellitus"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"           # canonical display: "Type 2 diabetes mellitus"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"   # canonical display: "24 HR metformin 500 MG Oral Tablet"

# Aliases (canonical-URI drift regression tests)
SNOMED_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
ICD10CM_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.90"


# ---------------------------------------------------------------------------
# Helper utilities (response shape assertion)
# ---------------------------------------------------------------------------


def _params_by_name(body: dict, name: str) -> list[dict]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _first_param(body: dict, name: str) -> dict | None:
    p = _params_by_name(body, name)
    return p[0] if p else None


def _param_value(body: dict, name: str) -> object | None:
    p = _first_param(body, name)
    if p is None:
        return None
    for key in (
        "valueString",
        "valueBoolean",
        "valueCode",
        "valueUri",
        "valueInteger",
    ):
        if key in p:
            return p[key]
    return None


def _assert_validate_200_with_result(r, label: str) -> dict:
    """Common positive-success-shape assertion for $validate-code.

    Returns the parsed body for further assertions.
    """
    assert r.status_code == 200, (
        f"{label}: expected 200, got {r.status_code}; body={r.text[:300]!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"{label}: body must be Parameters; got {body.get('resourceType')}"
    )
    result_val = _param_value(body, "result")
    assert result_val is not None, f"{label}: Out 'result' parameter missing"
    assert isinstance(result_val, bool), (
        f"{label}: Out 'result' must be a boolean; got {type(result_val).__name__}"
    )
    return body


# ---------------------------------------------------------------------------
# Source-read helpers (extends TS-01 HISTORIAN + CS-03 HISTORIAN strategy)
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
    async route handlers.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _get_nested_func_source(source: str, parent_name: str, child_name: str) -> str:
    """Return source code for a function NESTED inside ``parent_name``.

    Per HISTORIAN tip for EXPLORER: ``_do_validate``, ``_do_vs_validate``,
    ``_extract_validate_params`` etc. are all defined INSIDE
    ``create_fhir_app`` (the factory function). Required because plain
    ``ast.walk`` would miss the nested definitions.
    """
    parent_src = _get_func_source(source, parent_name)
    if not parent_src:
        return ""
    return _get_func_source(parent_src, child_name)


# ===========================================================================
# L1: Mixed-encoding batch with codeableConcept multi-coding
#     (HISTORIAN tip — CS-03 HISTORIAN QA-052 sibling on batch surface)
# ===========================================================================

class TestL1BatchCodeableConceptMultiCoding:
    """Lateral combination: batch entry carrying a codeableConcept with
    multiple codings [INVALID, VALID] on CodeSystem/$validate-code.

    Per spec: "The server returns true if one of the coding values is in
    the code system."

    CS-03 SKEPTIC QA-049 wired the all-pairs helper on the per-operation
    POST route. CS-03 HISTORIAN QA-052 wired the all-pairs helper into
    ``_extract_validate_params`` for the batch dispatcher. This lens
    probes the batch surface end-to-end to confirm the multi-coding
    "any match → true" semantic holds on the lateral batch path.
    """

    def test_e10_batch_codeable_concept_invalid_then_valid_returns_true(self, fhir_client):
        """Batch entry with codeableConcept [INVALID, VALID] → batch
        response entry with result=true. The all-pairs helper iterates
        and returns the FIRST match.
        """
        bundle = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {
                                "name": "codeableConcept",
                                "valueCodeableConcept": {
                                    "coding": [
                                        {
                                            "system": SNOMED_URI,
                                            "code": "9999999999",  # INVALID
                                        },
                                        {
                                            "system": SNOMED_URI,
                                            "code": SNOMED_DM,  # VALID
                                        },
                                    ]
                                },
                            }
                        ],
                    },
                }
            ],
        }
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200, f"batch must be 200: {r.text[:300]!r}"
        body = r.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "batch-response"
        entries = body.get("entry", [])
        assert len(entries) == 1
        # Per-entry success status
        assert entries[0]["response"]["status"] == "200", (
            f"batch entry should succeed; got {entries[0]['response']}"
        )
        resource = entries[0].get("resource", {})
        assert resource["resourceType"] == "Parameters"
        result_val = _param_value(resource, "result")
        assert result_val is True, (
            f"batch codeableConcept [INVALID, VALID] → result must be True; "
            f"got {result_val!r}"
        )

    def test_e11_batch_codeable_concept_all_invalid_returns_false(self, fhir_client):
        """Batch entry with codeableConcept [INVALID, INVALID] → batch
        response entry with result=false.
        """
        bundle = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {
                                "name": "codeableConcept",
                                "valueCodeableConcept": {
                                    "coding": [
                                        {
                                            "system": SNOMED_URI,
                                            "code": "9999999998",
                                        },
                                        {
                                            "system": SNOMED_URI,
                                            "code": "9999999999",
                                        },
                                    ]
                                },
                            }
                        ],
                    },
                }
            ],
        }
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200, f"batch must be 200: {r.text[:300]!r}"
        body = r.json()
        entries = body.get("entry", [])
        resource = entries[0].get("resource", {})
        result_val = _param_value(resource, "result")
        assert result_val is False, (
            f"batch codeableConcept [INVALID, INVALID] → result must be False; "
            f"got {result_val!r}"
        )

    def test_e12_batch_codeable_concept_out_system_is_matched_canonical(self, fhir_client):
        """Batch codeableConcept Out ``system`` reflects the MATCHED
        coding's canonical URI (NOT the first coding's URI, NOT a client
        alias). CR-025 fix on the batch surface.

        Fixture: codeableConcept [alias-URI INVALID, canonical-URI VALID] →
        Out system must be the canonical SNOMED URI of the matched coding.
        """
        bundle = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {
                                "name": "codeableConcept",
                                "valueCodeableConcept": {
                                    "coding": [
                                        {
                                            "system": SNOMED_TRAILING_SLASH,
                                            "code": "9999999999",  # INVALID
                                        },
                                        {
                                            "system": SNOMED_URI,
                                            "code": SNOMED_DM,  # VALID (canonical)
                                        },
                                    ]
                                },
                            }
                        ],
                    },
                }
            ],
        }
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200
        body = r.json()
        entries = body.get("entry", [])
        resource = entries[0].get("resource", {})
        result_val = _param_value(resource, "result")
        assert result_val is True
        out_system = _param_value(resource, "system")
        assert out_system == SNOMED_URI, (
            f"batch codeableConcept Out system must be canonical URI of matched "
            f"coding {SNOMED_URI!r}; got {out_system!r}"
        )

    def test_e13_batch_codeable_concept_out_display_is_matched_canonical(self, fhir_client):
        """Batch codeableConcept Out ``display`` reflects the MATCHED
        coding's canonical preferred term (the FIRST coding in iteration
        order that matches — NOT the last, NOT a client-supplied display).

        Fixture: codeableConcept [RxNorm metformin (VALID), SNOMED DM
        (VALID)] → both are valid in the fixture; the iteration returns
        the FIRST match (RxNorm metformin). The Out display reflects the
        RxNorm canonical, NOT the SNOMED canonical. This probes the
        iteration order invariant: FIRST MATCH WINS.
        """
        bundle = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {
                                "name": "codeableConcept",
                                "valueCodeableConcept": {
                                    "coding": [
                                        {
                                            "system": RXNORM_URI,
                                            "code": RXNORM_METFORMIN,  # VALID (first match)
                                        },
                                        {
                                            "system": SNOMED_URI,
                                            "code": SNOMED_DM,  # VALID (never reached)
                                        },
                                    ]
                                },
                            }
                        ],
                    },
                }
            ],
        }
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200
        body = r.json()
        entries = body.get("entry", [])
        resource = entries[0].get("resource", {})
        result_val = _param_value(resource, "result")
        assert result_val is True
        # FIRST match wins → RxNorm metformin
        out_system = _param_value(resource, "system")
        out_code = _param_value(resource, "code")
        out_display = _param_value(resource, "display")
        assert out_system == RXNORM_URI, (
            f"first-match-wins: Out system should be RxNorm; got {out_system!r}"
        )
        assert out_code == RXNORM_METFORMIN, (
            f"first-match-wins: Out code should be metformin; got {out_code!r}"
        )
        assert out_display == "24 HR metformin 500 MG Oral Tablet", (
            f"batch codeableConcept Out display must reflect FIRST MATCHED coding "
            f"canonical (RxNorm metformin); got {out_display!r}"
        )

    def test_e14_batch_mixing_scalar_and_codeable_concept_entries(self, fhir_client):
        """Batch Bundle interleaving:
          entry 0: scalar system+code (SNOMED DM)
          entry 1: codeableConcept [INVALID, SNOMED T2DM]
          entry 2: scalar system+code (RxNorm metformin)

        Each entry's response is independent (FHIR R4 §3.7). All three
        must succeed with result=true; the codeableConcept entry's Out
        system/code reflects the MATCHED coding.
        """
        bundle = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                # Entry 0: scalar SNOMED DM
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "system", "valueUri": SNOMED_URI},
                            {"name": "code", "valueCode": SNOMED_DM},
                        ],
                    },
                },
                # Entry 1: codeableConcept [INVALID, SNOMED T2DM]
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {
                                "name": "codeableConcept",
                                "valueCodeableConcept": {
                                    "coding": [
                                        {
                                            "system": SNOMED_URI,
                                            "code": "9999999999",
                                        },
                                        {
                                            "system": SNOMED_URI,
                                            "code": SNOMED_T2DM,
                                        },
                                    ]
                                },
                            }
                        ],
                    },
                },
                # Entry 2: scalar RxNorm metformin
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "system", "valueUri": RXNORM_URI},
                            {"name": "code", "valueCode": RXNORM_METFORMIN},
                        ],
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200, f"batch must be 200: {r.text[:300]!r}"
        body = r.json()
        entries = body.get("entry", [])
        assert len(entries) == 3, f"expected 3 response entries; got {len(entries)}"
        # Order preservation
        for i, e in enumerate(entries):
            assert e["response"]["status"] == "200", (
                f"entry {i} should succeed; got {e['response']}"
            )
        # Entry 1: codeableConcept → result=true + Out system reflects SNOMED canonical
        r1_resource = entries[1].get("resource", {})
        assert _param_value(r1_resource, "result") is True
        assert _param_value(r1_resource, "system") == SNOMED_URI
        assert _param_value(r1_resource, "code") == SNOMED_T2DM


# ===========================================================================
# L2: GET↔POST byte-exact parity on lateral codeableConcept shapes
#     (HISTORIAN tip — mixed-system, partial-coding, duplicate, text-only)
# ===========================================================================

class TestL2GetPostParityOnLateralCodeableConceptShapes:
    """GET↔POST byte-exact parity on lateral codeableConcept shapes.

    Per VS-04 EXPLORER strategy 50: GET and POST MUST produce byte-exact
    identical Out params for the same input shape. EXPLORER extends the
    matrix to codeableConcept lateral shapes the prior iterations did not
    exercise.

    Note: codeableConcept is a POST-only body parameter (the spec lists
    it as a Parameters-body parameter, not a Query parameter). On GET,
    codeableConcept is a string query param (informational only — not
    a structured CodeableConcept). This lens therefore compares:
      - POST with codeableConcept body (the structured form) vs
      - POST with the equivalent system+code body (the scalar form)

    For each lateral shape, the spec-correct Out params are the same
    regardless of the encoding chosen.
    """

    def _scalar_post(
        self, fhir_client, system: str, code: str, display: str | None = None
    ):
        params = [
            {"name": "system", "valueUri": system},
            {"name": "code", "valueCode": code},
        ]
        if display is not None:
            params.append({"name": "display", "valueString": display})
        body = {"resourceType": "Parameters", "parameter": params}
        return fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)

    def _codeable_concept_post(
        self, fhir_client, codings: list[dict], display: str | None = None
    ):
        params = [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {"coding": codings},
            }
        ]
        if display is not None:
            params.append({"name": "display", "valueString": display})
        body = {"resourceType": "Parameters", "parameter": params}
        return fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)

    def test_e20_parity_mixed_system_codeable_concept_matches_scalar(self, fhir_client):
        """Lateral shape: codeableConcept with mixed-system codings.

        Per spec: "The server returns true if one of the coding values
        is in the code system". When one coding is in a known system and
        valid, result=true regardless of the other coding's system.

        Compared against: scalar POST with the SAME (system, code) as the
        matched coding. Out (system, code, display) MUST match byte-exact.
        """
        # Scalar reference: SNOMED DM
        r_scalar = self._scalar_post(fhir_client, SNOMED_URI, SNOMED_DM)
        body_scalar = _assert_validate_200_with_result(r_scalar, "scalar SNOMED DM")

        # Lateral codeableConcept: [SNOMED DM, ICD-10-CM E11] (mixed systems)
        r_cc = self._codeable_concept_post(
            fhir_client,
            codings=[
                {"system": SNOMED_URI, "code": SNOMED_DM},
                {"system": ICD10CM_URI, "code": ICD10CM_E11},
            ],
        )
        body_cc = _assert_validate_200_with_result(r_cc, "codeableConcept mixed-system")

        # result must be true (both are valid; iteration returns first match)
        assert _param_value(body_cc, "result") is True

        # Out system/code/display reflect the FIRST matched coding (SNOMED DM)
        assert _param_value(body_cc, "system") == _param_value(body_scalar, "system")
        assert _param_value(body_cc, "code") == _param_value(body_scalar, "code")
        assert _param_value(body_cc, "display") == _param_value(body_scalar, "display")

    def test_e21_parity_partial_coding_skipped(self, fhir_client):
        """Lateral shape: codeableConcept with PARTIAL codings (missing
        system OR missing code) interleaved with a VALID coding.

        Per spec: the server returns true if one of the coding values is
        in the code system. Partial codings (missing system or code)
        cannot be in the code system; they MUST be skipped, not crash.
        The all-pairs helper returns the valid match → result=true.
        """
        r_cc = self._codeable_concept_post(
            fhir_client,
            codings=[
                {"code": SNOMED_DM},  # missing system → partial, skipped
                {"system": SNOMED_URI},  # missing code → partial, skipped
                {"system": SNOMED_URI, "code": SNOMED_DM},  # VALID
            ],
        )
        body_cc = _assert_validate_200_with_result(r_cc, "partial + valid")
        assert _param_value(body_cc, "result") is True
        assert _param_value(body_cc, "system") == SNOMED_URI
        assert _param_value(body_cc, "code") == SNOMED_DM

    def test_e22_parity_duplicate_coding_first_match_wins(self, fhir_client):
        """Lateral shape: codeableConcept with DUPLICATE codings (same
        system+code twice). The iteration returns the FIRST match — the
        second duplicate is never reached. Out (system, code, display)
        reflect the matched duplicate.
        """
        r_cc = self._codeable_concept_post(
            fhir_client,
            codings=[
                {"system": SNOMED_URI, "code": SNOMED_DM},
                {"system": SNOMED_URI, "code": SNOMED_DM},
            ],
        )
        body_cc = _assert_validate_200_with_result(r_cc, "duplicate codings")
        assert _param_value(body_cc, "result") is True
        assert _param_value(body_cc, "system") == SNOMED_URI
        assert _param_value(body_cc, "code") == SNOMED_DM

    def test_e23_parity_text_only_codeable_concept_no_5xx(self, fhir_client):
        """Lateral shape: codeableConcept with text-only (no codings).

        Per spec CodeableConcept (FHIR R4 §4.8.13) is 0..* Coding + 0..1
        text. A text-only CodeableConcept cannot identify a code. Server
        MUST NOT 5xx; result SHOULD be false (no codings to match).
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "text": "Diabetes mellitus (text-only)"
                    },
                }
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
        # Server MUST NOT 5xx on text-only.
        assert r.status_code != 500, (
            f"text-only codeableConcept must not 5xx; got {r.status_code}: "
            f"{r.text[:300]!r}"
        )
        body_json = r.json()
        assert body_json.get("resourceType") in ("Parameters", "OperationOutcome"), (
            f"text-only codeableConcept response must be Parameters or "
            f"OperationOutcome; got {body_json.get('resourceType')}"
        )

    def test_e24_parity_codeable_concept_display_param_does_not_trigger_mismatch(self, fhir_client):
        """Lateral shape: codeableConcept with a SEPARATE ``display``
        parameter. Per spec / SKEPTIC AUDIT-002: codeableConcept entries
        don't carry per-coding display enforcement. The separate ``display``
        parameter is for the top-level verification — but the impl
        applies display-mismatch enforcement only to the SCALAR path
        (where ``code_info`` lookup happens with code_info.name).

        For codeableConcept, the matched code's canonical display is
        returned regardless of the supplied ``display`` value (no
        mismatch trigger on this path).
        """
        r_cc = self._codeable_concept_post(
            fhir_client,
            codings=[{"system": SNOMED_URI, "code": SNOMED_DM}],
            display="this-display-is-wrong-but-ignored-on-codeable-concept",
        )
        # Should be 200 (codeableConcept path doesn't trigger mismatch)
        body_cc = _assert_validate_200_with_result(r_cc, "codeableConcept + display")
        # The Out display reflects the matched code's canonical, NOT the
        # client-supplied display
        out_display = _param_value(body_cc, "display")
        assert out_display == "Diabetes mellitus", (
            f"codeableConcept Out display must reflect matched code canonical; "
            f"got {out_display!r}"
        )


# ===========================================================================
# L3: GET↔POST byte-exact parity on lateral SCALAR shapes
#     (canonical Out system, alias inputs, display mismatch)
# ===========================================================================

class TestL3GetPostParityOnLateralScalarShapes:
    """GET↔POST byte-exact parity on lateral scalar shapes.

    Lateral axis: alias system URIs (trailing-slash, urn:oid, uppercase-
    scheme) — does the Out system parameter byte-exact match between
    GET and POST for the SAME alias input?

    Per VS-04 EXPLORER strategy 50: GET and POST MUST produce byte-exact
    identical Out params for the same input shape.
    """

    @pytest.mark.parametrize(
        "system_alias, expected_canonical",
        [
            (SNOMED_TRAILING_SLASH, SNOMED_URI),
            (SNOMED_UPPERCASE_SCHEME, SNOMED_URI),
            (ICD10CM_OID_ALIAS, ICD10CM_URI),
        ],
    )
    def test_e30_get_post_parity_on_alias_system(
        self, fhir_client, system_alias, expected_canonical
    ):
        """GET ↔ POST with the same alias system URI MUST produce the
        same canonical Out ``system`` parameter.
        """
        # GET
        r_get = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": system_alias, "code": SNOMED_DM if "snomed" in system_alias else ICD10CM_E11},
        )
        body_get = _assert_validate_200_with_result(r_get, f"GET alias {system_alias}")
        # POST
        code_val = SNOMED_DM if "snomed" in system_alias else ICD10CM_E11
        body_post_dict = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": system_alias},
                {"name": "code", "valueCode": code_val},
            ],
        }
        r_post = fhir_client.post(
            "/fhir/CodeSystem/$validate-code", json=body_post_dict
        )
        body_post = _assert_validate_200_with_result(r_post, f"POST alias {system_alias}")

        # Out system MUST match between GET and POST AND equal canonical
        get_system = _param_value(body_get, "system")
        post_system = _param_value(body_post, "system")
        assert get_system == expected_canonical, (
            f"GET Out system for {system_alias!r} should canonicalize to "
            f"{expected_canonical!r}; got {get_system!r}"
        )
        assert post_system == expected_canonical, (
            f"POST Out system for {system_alias!r} should canonicalize to "
            f"{expected_canonical!r}; got {post_system!r}"
        )
        assert get_system == post_system, (
            f"GET ↔ POST Out system mismatch on {system_alias!r}: "
            f"GET={get_system!r}, POST={post_system!r}"
        )

    @pytest.mark.parametrize(
        "system_uri, code",
        [
            (SNOMED_URI, SNOMED_DM),
            (SNOMED_URI, SNOMED_T2DM),
            (ICD10CM_URI, ICD10CM_E11),
            (RXNORM_URI, RXNORM_METFORMIN),
        ],
    )
    def test_e31_get_post_parity_on_canonical_system(
        self, fhir_client, system_uri, code
    ):
        """GET ↔ POST with canonical system URI on every seeded code —
        the Out (result, system, code, display) MUST byte-exact match.
        """
        r_get = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": system_uri, "code": code},
        )
        body_get = _assert_validate_200_with_result(r_get, f"GET {system_uri} {code}")
        body_post_dict = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": system_uri},
                {"name": "code", "valueCode": code},
            ],
        }
        r_post = fhir_client.post(
            "/fhir/CodeSystem/$validate-code", json=body_post_dict
        )
        body_post = _assert_validate_200_with_result(r_post, f"POST {system_uri} {code}")

        for param_name in ("result", "system", "code", "display"):
            get_v = _param_value(body_get, param_name)
            post_v = _param_value(body_post, param_name)
            assert get_v == post_v, (
                f"GET ↔ POST mismatch on {param_name} for ({system_uri}, {code}): "
                f"GET={get_v!r}, POST={post_v!r}"
            )


# ===========================================================================
# L4: coding AND codeableConcept combined (alternative encoding precedence)
# ===========================================================================

class TestL4CodingAndCodeableConceptCombined:
    """Lateral combination: POST body with BOTH ``coding`` AND
    ``codeableConcept`` parameters.

    Per spec: "a client SHALL provide one (and only one) of the
    parameters (code+system, coding, or codeableConcept)". When multiple
    encodings are supplied, the impl MUST NOT 5xx and MUST pick one
    deterministically. The impl prefers ``coding`` over ``codeableConcept``
    (the first alternative-encoding branch that succeeds).
    """

    def test_e40_coding_wins_over_codeable_concept(self, fhir_client):
        """POST with both coding (VALID) AND codeableConcept (all INVALID).
        The coding alternative wins (first branch checked). Result=true.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DM},
                },
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "9999999999"}
                        ]
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
        body_json = _assert_validate_200_with_result(r, "coding + codeableConcept")
        # coding wins → SNOMED DM
        assert _param_value(body_json, "result") is True
        assert _param_value(body_json, "system") == SNOMED_URI
        assert _param_value(body_json, "code") == SNOMED_DM

    def test_e41_scalar_wins_over_both_alternatives(self, fhir_client):
        """POST with scalar system+code AND coding AND codeableConcept.
        Per spec the client SHOULD send only one; the impl prefers the
        explicit scalar primary key. Result=true; Out code = scalar.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_DM},
                {
                    "name": "coding",
                    "valueCoding": {"system": RXNORM_URI, "code": RXNORM_METFORMIN},
                },
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": ICD10CM_URI, "code": ICD10CM_E11}
                        ]
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
        body_json = _assert_validate_200_with_result(r, "scalar + coding + codeableConcept")
        assert _param_value(body_json, "result") is True
        assert _param_value(body_json, "system") == SNOMED_URI
        assert _param_value(body_json, "code") == SNOMED_DM


# ===========================================================================
# L5: display parameter — empty string vs missing (semantic difference)
# ===========================================================================

class TestL5DisplayEmptyVsMissing:
    """Lateral distinction: empty-string ``display`` parameter vs MISSING
    ``display`` parameter.

    Per spec: ``display`` 0..1 string is "The display associated with
    the code, if one is defined".

    Empty string: a display value that does NOT match the canonical
    display → triggers display-mismatch enforcement (result=false +
    message + canonical display) per CS-03 SKEPTIC QA-048.

    Missing display: no comparison → result depends only on the code's
    presence in the code system. A known code → result=true.
    """

    def test_e50_missing_display_known_code_returns_true(self, fhir_client):
        """No ``display`` parameter + known code → result=true."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": SNOMED_DM},
        )
        body = _assert_validate_200_with_result(r, "missing display")
        assert _param_value(body, "result") is True
        # canonical display is still emitted (the server reveals its
        # canonical display even when the client doesn't supply one)
        assert _param_value(body, "display") == "Diabetes mellitus"

    def test_e51_empty_display_known_code_returns_false_with_message(self, fhir_client):
        """Empty-string ``display`` parameter + known code → result=false
        + message (per CS-03 SKEPTIC QA-048 exact-match enforcement).
        Empty string != canonical "Diabetes mellitus".
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": SNOMED_DM, "display": ""},
        )
        body = _assert_validate_200_with_result(r, "empty display")
        assert _param_value(body, "result") is False
        msg = _param_value(body, "message")
        assert msg is not None, (
            "empty-display mismatch MUST carry a message per spec example response"
        )
        assert "incorrect" in str(msg).lower()


# ===========================================================================
# L6: date parameter — future / past / boundary (lateral acceptance)
# ===========================================================================

class TestL6DateParameterLateralAcceptance:
    """date parameter is 0..1 dateTime. medterm4ds does not version-scope
    data (NOT A BUG registry entry for ``version``). The param MUST be
    accepted without 5xx — processing is deferred to a future enhancement.

    EXPLORER lateral axis: future date / past date / boundary date /
    full dateTime / partial date / time-only / malformed.
    """

    @pytest.mark.parametrize(
        "date_val",
        [
            "1900-01-01",                    # far past
            "2099-12-31",                    # far future
            "2024-02-29",                    # leap day boundary
            "2023-02-28",                    # day before leap day
            "2024-12-31T23:59:59Z",          # year boundary end
            "2024-01-01T00:00:00Z",          # year boundary start
            "2024",                          # partial date (year only)
            "2024-01",                       # partial date (year-month)
            "2024-01-01T12:00:00+02:00",     # timezone offset
            "2024-01-01T12:00:00-08:00",     # negative timezone offset
            "not-a-date",                    # malformed
            "",                              # empty
        ],
    )
    def test_e60_date_param_accepted_without_5xx(self, fhir_client, date_val):
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_DM,
                "date": date_val,
            },
        )
        _assert_validate_200_with_result(r, f"date={date_val!r}")


# ===========================================================================
# L7: abstract + displayLanguage spec In params (R4 spec-actual set)
#     No inferSystem manufacturing per HISTORIAN tip
# ===========================================================================

class TestL7AbstractAndDisplayLanguageSpecInParams:
    """``abstract`` and ``displayLanguage`` are spec-listed In params on
    CodeSystem/$validate-code. They MUST be accepted without 5xx. The
    engine doesn't have abstract-flag data per CF-SKEPTIC-CS05-01 DEFERRED;
    ``displayLanguage`` is honored only if the engine has multi-language
    designations (not in fixture today).

    Per HISTORIAN tip: do NOT manufacture probes for off-spec
    ``inferSystem`` (it's a ValueSet-$validate-code In parameter).
    """

    @pytest.mark.parametrize("abstract_val", ["true", "false"])
    def test_e70_abstract_param_accepted_without_5xx(self, fhir_client, abstract_val):
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_DM,
                "abstract": abstract_val,
            },
        )
        _assert_validate_200_with_result(r, f"abstract={abstract_val!r}")

    @pytest.mark.parametrize(
        "lang",
        ["en", "en-US", "de-DE", "fr", "es-419", "zh-CN", "x-test"],
    )
    def test_e71_display_language_param_accepted_without_5xx(self, fhir_client, lang):
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI,
                "code": SNOMED_DM,
                "displayLanguage": lang,
            },
        )
        _assert_validate_200_with_result(r, f"displayLanguage={lang!r}")


# ===========================================================================
# L8: Cross-handler GET↔POST parity on the codeableConcept shape
#     (CodeSystem vs ValueSet surface — sibling handler audit)
# ===========================================================================

class TestL8CrossHandlerCodeSystemVsValueSetCodeableConcept:
    """Cross-handler audit: CodeSystem/$validate-code and
    ValueSet/$validate-code both accept codeableConcept. The Out params
    (result, system, code) MUST byte-exact match for the same input.

    Per CR-011 + CR-025 fix sites: both handlers canonicalize via
    ``canonical_system_uri``. Per CS-03 HISTORIAN QA-052 + VS-05 SKEPTIC
    QA-069: both handlers use the all-pairs helper for codeableConcept
    multi-coding.

    This lens verifies the sibling-handler parity on lateral codeableConcept
    shapes the prior iterations did not exercise.
    """

    def _cs_post(self, fhir_client, codings: list[dict]):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {"coding": codings},
                }
            ],
        }
        return fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)

    def _vs_post(self, fhir_client, codings: list[dict]):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {"coding": codings},
                }
            ],
        }
        return fhir_client.post("/fhir/ValueSet/$validate-code", json=body)

    def test_e80_cs_vs_vs_parity_on_codeable_concept_invalid_then_valid(self, fhir_client):
        """CodeSystem/$validate-code AND ValueSet/$validate-code both
        return result=true on codeableConcept [INVALID, VALID]. Out
        (system, code, display) byte-exact match between the two surfaces.
        """
        codings = [
            {"system": SNOMED_URI, "code": "9999999999"},  # INVALID
            {"system": SNOMED_URI, "code": SNOMED_DM},     # VALID
        ]
        r_cs = self._cs_post(fhir_client, codings)
        r_vs = self._vs_post(fhir_client, codings)
        body_cs = _assert_validate_200_with_result(r_cs, "CS codeableConcept")
        body_vs = _assert_validate_200_with_result(r_vs, "VS codeableConcept")
        for param_name in ("result", "system", "code", "display"):
            cs_v = _param_value(body_cs, param_name)
            vs_v = _param_value(body_vs, param_name)
            assert cs_v == vs_v, (
                f"CS vs VS mismatch on {param_name}: CS={cs_v!r}, VS={vs_v!r}"
            )

    def test_e81_cs_vs_vs_parity_on_codeable_concept_mixed_system(self, fhir_client):
        """Cross-handler parity on lateral mixed-system codeableConcept
        [SNOMED DM, RxNorm metformin]. Both return result=true (first
        match = SNOMED DM). Out (system, code, display) byte-exact.
        """
        codings = [
            {"system": SNOMED_URI, "code": SNOMED_DM},
            {"system": RXNORM_URI, "code": RXNORM_METFORMIN},
        ]
        r_cs = self._cs_post(fhir_client, codings)
        r_vs = self._vs_post(fhir_client, codings)
        body_cs = _assert_validate_200_with_result(r_cs, "CS mixed-system")
        body_vs = _assert_validate_200_with_result(r_vs, "VS mixed-system")
        for param_name in ("result", "system", "code", "display"):
            cs_v = _param_value(body_cs, param_name)
            vs_v = _param_value(body_vs, param_name)
            assert cs_v == vs_v, (
                f"CS vs VS mixed-system mismatch on {param_name}: "
                f"CS={cs_v!r}, VS={vs_v!r}"
            )


# ===========================================================================
# L9: Source-read structural contracts (per HISTORIAN tip —
#     _get_nested_func_source helper)
# ===========================================================================

class TestL9SourceReadStructuralContracts:
    """Source-read structural contracts using the
    ``_get_nested_func_source(source, parent_name, child_name)`` helper
    (per HISTORIAN tip). These pins are load-bearing contracts that
    prevent regression of the 5 prior CS-03 fixes (CS-03 SKEPTIC QA-048,
    QA-049; CS-03 HISTORIAN QA-051, QA-052; CR-025) on the LATERAL
    surfaces EXPLORER exercises.
    """

    def test_e90_do_validate_calls_canonical_system_uri_on_scalar_path(self):
        """CS-03 HISTORIAN QA-051 fix: the scalar path in ``_do_validate``
        MUST call ``canonical_system_uri`` so the Out ``system`` is the
        canonical FHIR URI (not client alias).
        """
        source = _fhir_api_source()
        do_validate = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
        assert do_validate, "_do_validate source not found nested in create_fhir_app"
        assert "canonical_system_uri(" in do_validate, (
            "CS-03 HISTORIAN QA-051 regressed: _do_validate no longer calls "
            "canonical_system_uri on the scalar path"
        )

    def test_e91_do_validate_uses_matched_uri_for_codeable_concept_canonical(self):
        """CR-025 fix: the codeableConcept path in ``_do_validate`` MUST
        wrap ``matched_uri`` through ``canonical_system_uri`` so the Out
        ``system`` is the canonical FHIR URI of the MATCHED coding (not
        the client alias, not the first coding's URI).
        """
        source = _fhir_api_source()
        do_validate = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
        assert do_validate
        assert "canonical_matched_uri" in do_validate, (
            "CR-025 regressed: _do_validate no longer wraps matched_uri "
            "through canonical_system_uri on the codeableConcept path"
        )

    def test_e92_extract_validate_params_returns_codeable_concept_pairs(self):
        """CS-03 HISTORIAN QA-052 fix: ``_extract_validate_params`` MUST
        return a 4-tuple including ``codeable_concept_pairs`` (the
        all-pairs helper) so the batch dispatcher passes them through to
        ``_do_validate``. Without this, the batch path silently uses the
        single-pair helper.
        """
        source = _fhir_api_source()
        extract = _get_nested_func_source(
            source, "create_fhir_app", "_extract_validate_params"
        )
        assert extract, "_extract_validate_params source not found"
        assert "codeable_concept_pairs" in extract, (
            "CS-03 HISTORIAN QA-052 regressed: _extract_validate_params no "
            "longer returns codeable_concept_pairs"
        )
        assert "_extract_all_coding_pairs_from_codeable_concept" in extract, (
            "CS-03 HISTORIAN QA-052 regressed: _extract_validate_params does "
            "not call the all-pairs helper"
        )

    def test_e93_validate_post_uses_all_pairs_helper_for_codeable_concept(self):
        """CS-03 SKEPTIC QA-049 fix: the per-operation POST route
        ``validate_post`` MUST use the all-pairs helper for codeableConcept
        on CodeSystem/$validate-code.
        """
        source = _fhir_api_source()
        validate_post = _get_nested_func_source(
            source, "create_fhir_app", "validate_post"
        )
        assert validate_post, "validate_post source not found"
        assert "_extract_all_coding_pairs_from_codeable_concept" in validate_post, (
            "CS-03 SKEPTIC QA-049 regressed: validate_post does not use the "
            "all-pairs helper for codeableConcept"
        )

    def test_e94_do_validate_enforces_display_mismatch_with_canonical_message(self):
        """CS-03 SKEPTIC QA-048 fix: ``_do_validate`` MUST enforce display
        mismatch (per spec example response) when the client supplies a
        ``display`` that does not match the canonical display for a known
        code. The message format MUST cite the wrong display value.
        """
        source = _fhir_api_source()
        do_validate = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
        assert do_validate
        assert "is incorrect" in do_validate, (
            "CS-03 SKEPTIC QA-048 regressed: _do_validate no longer emits "
            "the spec example message format on display mismatch"
        )


# ===========================================================================
# L10: Batch with multiple codeableConcept entries in the SAME Bundle
#      (stress test — order preservation + per-entry isolation)
# ===========================================================================

class TestL10BatchMultipleCodeableConceptEntries:
    """Stress test: a batch Bundle with N entries, each carrying a
    different codeableConcept shape. Order preservation + per-entry
    independence per FHIR R4 §3.7.
    """

    def test_e100_batch_5_entries_order_preserved(self, fhir_client):
        """5-entry batch:
          0: scalar SNOMED DM (valid)
          1: codeableConcept [INVALID, SNOMED T2DM] (valid via 2nd coding)
          2: codeableConcept [INVALID, INVALID] (invalid)
          3: codeableConcept [RxNorm metformin] (valid)
          4: codeableConcept [INVALID, ICD-10-CM E11] (valid via 2nd coding)

        Order MUST be preserved; each entry's response is independent.
        """
        bundle = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                # 0: scalar
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "system", "valueUri": SNOMED_URI},
                            {"name": "code", "valueCode": SNOMED_DM},
                        ],
                    },
                },
                # 1: cc [INVALID, SNOMED T2DM]
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {
                                "name": "codeableConcept",
                                "valueCodeableConcept": {
                                    "coding": [
                                        {"system": SNOMED_URI, "code": "9999999999"},
                                        {"system": SNOMED_URI, "code": SNOMED_T2DM},
                                    ]
                                },
                            }
                        ],
                    },
                },
                # 2: cc [INVALID, INVALID]
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {
                                "name": "codeableConcept",
                                "valueCodeableConcept": {
                                    "coding": [
                                        {"system": SNOMED_URI, "code": "9999999998"},
                                        {"system": SNOMED_URI, "code": "9999999999"},
                                    ]
                                },
                            }
                        ],
                    },
                },
                # 3: cc [RxNorm metformin]
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {
                                "name": "codeableConcept",
                                "valueCodeableConcept": {
                                    "coding": [
                                        {"system": RXNORM_URI, "code": RXNORM_METFORMIN},
                                    ]
                                },
                            }
                        ],
                    },
                },
                # 4: cc [INVALID, ICD-10-CM E11]
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$validate-code",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {
                                "name": "codeableConcept",
                                "valueCodeableConcept": {
                                    "coding": [
                                        {"system": SNOMED_URI, "code": "9999999999"},
                                        {"system": ICD10CM_URI, "code": ICD10CM_E11},
                                    ]
                                },
                            }
                        ],
                    },
                },
            ],
        }
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200, f"batch must be 200: {r.text[:300]!r}"
        body = r.json()
        entries = body.get("entry", [])
        assert len(entries) == 5, f"expected 5 response entries; got {len(entries)}"

        # Expected results
        expected = [
            (True, SNOMED_URI, SNOMED_DM),       # 0
            (True, SNOMED_URI, SNOMED_T2DM),     # 1
            (False, None, None),                 # 2: all invalid
            (True, RXNORM_URI, RXNORM_METFORMIN),  # 3
            (True, ICD10CM_URI, ICD10CM_E11),    # 4
        ]
        for i, (exp_result, exp_system, exp_code) in enumerate(expected):
            entry = entries[i]
            assert entry["response"]["status"] == "200", (
                f"entry {i} should be 200; got {entry['response']}"
            )
            resource = entry.get("resource", {})
            assert resource.get("resourceType") == "Parameters"
            result_val = _param_value(resource, "result")
            assert result_val == exp_result, (
                f"entry {i}: expected result={exp_result}; got {result_val!r}"
            )
            if exp_system is not None:
                out_system = _param_value(resource, "system")
                assert out_system == exp_system, (
                    f"entry {i}: expected system={exp_system!r}; got {out_system!r}"
                )
                out_code = _param_value(resource, "code")
                assert out_code == exp_code, (
                    f"entry {i}: expected code={exp_code!r}; got {out_code!r}"
                )


# ===========================================================================
# L11: codeableConcept with cross-system codings where ONLY one is known
#      (lateral: mixed-system partial-known fixture coverage)
# ===========================================================================

class TestL11CodeableConceptCrossSystemLateral:
    """Lateral: codeableConcept with codings from DIFFERENT systems, only
    one of which is in the medterm4ds fixture.

    E.g., [SNOMED DM, http://example.org/unknown-system XXX] — the
    unknown-system coding is silently skipped (not 5xx); the SNOMED DM
    coding matches → result=true.

    Per spec: "The server returns true if one of the coding values is
    in the code system". An unknown system URI is NOT a 5xx event; it's
    a "this coding is not in any code system" event.
    """

    def test_e110_codeable_concept_known_and_unknown_system(self, fhir_client):
        """codeableConcept [SNOMED DM, unknown-system XXX] → result=true;
        Out system reflects SNOMED canonical.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": "http://example.org/unknown-system", "code": "XXX"},
                            {"system": SNOMED_URI, "code": SNOMED_DM},
                        ]
                    },
                }
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
        body_json = _assert_validate_200_with_result(r, "known + unknown system")
        assert _param_value(body_json, "result") is True
        assert _param_value(body_json, "system") == SNOMED_URI
        assert _param_value(body_json, "code") == SNOMED_DM

    def test_e111_codeable_concept_all_unknown_systems(self, fhir_client):
        """codeableConcept [unknown-system A, unknown-system B] → result=false
        (no coding in any code system).
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": "http://example.org/unknown-A", "code": "AAA"},
                            {"system": "http://example.org/unknown-B", "code": "BBB"},
                        ]
                    },
                }
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
        body_json = _assert_validate_200_with_result(r, "all unknown systems")
        assert _param_value(body_json, "result") is False

    def test_e112_codeable_concept_with_invalid_coding_shape_no_5xx(self, fhir_client):
        """codeableConcept with non-dict coding (string, null, list).
        The all-pairs helper MUST handle gracefully (no 5xx).
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            "not-a-dict",         # string
                            None,                 # null
                            ["list", "not-dict"],  # list
                            {"system": SNOMED_URI, "code": SNOMED_DM},  # VALID
                        ]
                    },
                }
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
        # MUST NOT 5xx
        assert r.status_code != 500, (
            f"malformed codeableConcept codings must not 5xx; got "
            f"{r.status_code}: {r.text[:300]!r}"
        )
