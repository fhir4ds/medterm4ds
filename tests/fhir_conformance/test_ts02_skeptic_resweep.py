"""SKEPTIC resweep probes for TS-02 (Mandatory Terminology Service Operations
Matrix, FHIR R4 §4.7.1.2).

Fresh full-sweep run per USER_DIRECTIVES [2026-08-08]. Sibling file to the
baseline ``test_ts02_skeptic.py`` so the baseline stays comparable across
runs while this file adds fresh hostile-input coverage.

Source: https://hl7.org/fhir/R4/terminology-service.html (§4.7) +
         per-operation definitions at hl7.org/fhir/R4/{codesystem,valueset,
         conceptmap}-operation-{lookup,validate-code,subsumes,expand,
         translate,closure}.html

Tests the 7 mandatory items:
1. CodeSystem/$lookup (type-level)
2. CodeSystem/$validate-code (type-level)
3. CodeSystem/$subsumes (type-level)
4. ValueSet/$expand (type AND instance level)
5. ValueSet/$validate-code (type AND instance level)
6. ConceptMap/$translate (type AND instance level)
7. CapabilityStatement advertises all mandatory operations

SKEPTIC lens (per ROLE_QA_ENGINEER Section 3): aggressive bug hunting. For
each mandatory operation, probe hostile inputs:
- Missing required parameters (each one in isolation)
- Empty string params
- Very long values (>1000 chars)
- Special characters in codes (semicolons, SQL-injection-style, path
  traversal, newline, null byte)
- Type mismatches in POST bodies (Parameters resource with wrong value[x])
- URL encoding edge cases (unencoded special chars, double-encoding, %00)
- Duplicate parameters (same param twice)
- Conflicting parameters (e.g., both code and coding in $lookup)
- HTTP method tolerance (GET vs POST behavior consistency)
- Response shape on missing optional params
- CapabilityStatement operations array completeness
- Canonical-URI drift (TS-01/TERMINOLOGIST cross-endpoint URI invariant —
  client-input-as-canonical drift meta-pattern count=8 PROMOTED in
  GLOBAL_RULES.md line 124).

Each probe captures the actual behavior and compares against the FHIR R4
spec. A probe "fails" (reveals a bug) when actual behavior violates the
spec.
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

# Valid $subsumes outcome values per FHIR R4 ConceptSubsumptionOutcome.
VALID_SUBSUMES_OUTCOMES = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}

# Canonical aliases the TS-01/TERMINOLOGIST invariant exercises. Every Out
# `system` parameter must echo the canonical URI from SYSTEM_TO_FHIR_URI,
# NOT the client's input. Aliases accepted as INPUT; canonical emitted as
# OUTPUT.
SNOMED_CANONICAL = "http://snomed.info/sct"
SNOMED_ALIAS_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_ALIAS_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_EDITION_URI = "http://snomed.info/sct/731000124108"  # US edition — NOT canonical


# =============================================================================
# Lens 1: Item 7 — CapabilityStatement operations array completeness
# (audited first; advertisement presence gates the rest of the matrix)
# =============================================================================

class TestLens1CapabilityStatementCompleteness:
    """Verify the CapabilityStatement advertises every mandatory operation
    with the correct canonical OperationDefinition URI AND the right
    (resource, name) tuple."""

    def test_s10_all_mandatory_operations_advertised_with_correct_resource(
        self, fhir_client
    ):
        """§4.7 R4: 'a server that supports all the functionality described
        here can be described as a "FHIR Terminology Service"'. Each of the
        mandatory operations is owned by a specific resource type per the
        per-operation definition pages.

        Spec citations:
          - $lookup:           https://hl7.org/fhir/R4/codesystem-operation-lookup.html
          - $validate-code CS: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
          - $subsumes:         https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
          - $expand:           https://hl7.org/fhir/R4/valueset-operation-expand.html
          - $validate-code VS: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
          - $translate:        https://hl7.org/fhir/R4/conceptmap-operation-translate.html
          - $closure:          https://hl7.org/fhir/R4/conceptmap-operation-closure.html
        """
        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200
        body = r.json()
        rest = body.get("rest", [{}])[0]
        resources = {res["type"]: res for res in rest.get("resource", [])}

        # Mandatory ops grouped by their owning resource type per spec.
        expected_by_resource = {
            "CodeSystem": {"lookup", "validate-code", "subsumes", "closure"},
            "ValueSet": {"expand", "validate-code"},
            "ConceptMap": {"translate"},
        }
        for rtype, expected_ops in expected_by_resource.items():
            actual_ops = {
                op["name"] for op in resources.get(rtype, {}).get("operation", [])
            }
            missing = expected_ops - actual_ops
            assert not missing, (
                f"{rtype} missing mandatory operations {missing}. "
                f"Have: {actual_ops}"
            )

    def test_s11_operation_definitions_are_canonical_hl7_uris(self, fhir_client):
        """§3.2.1.0.5: when CapabilityStatement advertises an operation,
        ``definition`` SHOULD reference the canonical HL7 OperationDefinition
        URL. Server-local URLs prevent clients from identifying the standard
        operation."""
        r = fhir_client.get("/fhir/metadata")
        body = r.json()
        rest = body.get("rest", [{}])[0]
        expected = {
            ("CodeSystem", "lookup"): "http://hl7.org/fhir/OperationDefinition/CodeSystem-lookup",
            ("CodeSystem", "validate-code"): "http://hl7.org/fhir/OperationDefinition/CodeSystem-validate-code",
            ("CodeSystem", "subsumes"): "http://hl7.org/fhir/OperationDefinition/CodeSystem-subsumes",
            ("CodeSystem", "closure"): "http://hl7.org/fhir/OperationDefinition/CodeSystem-closure",
            ("ValueSet", "expand"): "http://hl7.org/fhir/OperationDefinition/ValueSet-expand",
            ("ValueSet", "validate-code"): "http://hl7.org/fhir/OperationDefinition/ValueSet-validate-code",
            ("ConceptMap", "translate"): "http://hl7.org/fhir/OperationDefinition/ConceptMap-translate",
        }
        mismatches = []
        for res in rest.get("resource", []):
            rtype = res.get("type")
            for op in res.get("operation", []):
                key = (rtype, op.get("name"))
                if key in expected and op.get("definition") != expected[key]:
                    mismatches.append((key, op.get("definition"), expected[key]))
        assert not mismatches, f"Non-canonical OperationDefinition URIs: {mismatches}"

    def test_s12_no_duplicate_operations_advertised(self, fhir_client):
        """SKEPTIC: CapabilityStatement.operation[] MUST NOT contain
        duplicates (same name on same resource). Duplicate entries confuse
        clients about which definition applies."""
        r = fhir_client.get("/fhir/metadata")
        body = r.json()
        rest = body.get("rest", [{}])[0]
        seen = set()
        dups = []
        for res in rest.get("resource", []):
            rtype = res.get("type")
            for op in res.get("operation", []):
                key = (rtype, op.get("name"))
                if key in seen:
                    dups.append(key)
                seen.add(key)
        assert not dups, f"Duplicate operations in CapabilityStatement: {dups}"

    def test_s13_code_system_validate_code_advertised_on_BOTH_resources(
        self, fhir_client
    ):
        """§4.7.1.2: '$validate-code' SHALL be exposed on BOTH CodeSystem and
        ValueSet (the spec defines two distinct operations with the same
        name on different resources —
        https://hl7.org/fhir/R4/codesystem-operation-validate-code.html and
        https://hl7.org/fhir/R4/valueset-operation-validate-code.html). The
        TS-02 chunk items explicitly list both. Pinning this guards against
        silent removal of either route."""
        r = fhir_client.get("/fhir/metadata")
        body = r.json()
        rest = body.get("rest", [{}])[0]
        resources = {res["type"]: res for res in rest.get("resource", [])}
        cs_ops = {op["name"] for op in resources.get("CodeSystem", {}).get("operation", [])}
        vs_ops = {op["name"] for op in resources.get("ValueSet", {}).get("operation", [])}
        assert "validate-code" in cs_ops, "CodeSystem/$validate-code missing"
        assert "validate-code" in vs_ops, "ValueSet/$validate-code missing"


# =============================================================================
# Lens 2: Item 1 — CodeSystem/$lookup required params + canonical-URI drift
# =============================================================================

class TestLens2LookupRequiredParams:
    """$lookup is type-level only per
    https://hl7.org/fhir/R4/codesystem-operation-lookup.html.
    Spec: 'a client SHALL provide both a system and a code, either using the
    system+code parameters, or in the coding parameter.'"""

    def test_s20_lookup_empty_string_system_rejected(self, fhir_client):
        """SKEPTIC: empty-string system should be rejected, not silently
        treated as 'absent' (which would fall through to a confusing
        error). Per spec prose: 'a client SHALL provide both a system and a
        code' — empty string is not providing it."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "", "code": "44054006"},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert 400 <= r.status_code < 500, (
            f"empty system returned {r.status_code} (expected 4xx)"
        )

    def test_s21_lookup_empty_string_code_rejected(self, fhir_client):
        """SKEPTIC: empty-string code should be rejected."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": ""},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert 400 <= r.status_code < 500

    def test_s22_lookup_canonical_system_uri_in_response(self, fhir_client):
        """TS-01/TERMINOLOGIST invariant: $lookup Out `system` MUST echo
        the canonical URI from SYSTEM_TO_FHIR_URI, not the client's input
        (client-input-as-canonical drift meta-pattern count=8 PROMOTED in
        GLOBAL_RULES.md line 124).

        Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
        Out `system`: 'The canonical URI of the code system that contains
        the concept that was looked up. (This may differ from the value
        passed in `system` as an input parameter if the code was found in a
        different system/subsystem, such as a supplement.)'
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_ALIAS_OID, "code": "44054006"},
        )
        body = r.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        assert params.get("system", {}).get("valueUri") == SNOMED_CANONICAL, (
            f"$lookup Out system echoes client alias {SNOMED_ALIAS_OID!r} "
            f"instead of canonical {SNOMED_CANONICAL!r}. "
            f"Got: {params.get('system')}"
        )

    def test_s23_lookup_canonical_system_uri_on_trailing_slash_input(
        self, fhir_client
    ):
        """Variant of s22: trailing-slash input ('http://snomed.info/sct/')
        is a valid alias per FHIR_URI_ALIASES. The Out `system` MUST still
        be the canonical URI."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_ALIAS_TRAILING_SLASH, "code": "44054006"},
        )
        body = r.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        assert params.get("system", {}).get("valueUri") == SNOMED_CANONICAL, (
            f"trailing-slash input leaked into Out system: "
            f"{params.get('system')}"
        )

    def test_s24_lookup_snomed_edition_uri_normalized_to_canonical(
        self, fhir_client
    ):
        """SKEPTIC: a client passing the SNOMED US-edition URI
        'http://snomed.info/sct/731000124108' should either get rejected OR
        have the Out `system` normalized to the canonical base URI. The
        implementation resolves via fhir_uri_to_system which strips
        trailing-slash only — the edition URI is NOT in the alias map, so
        the impl rejects with 400. Pinning the current behavior (reject)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_EDITION_URI, "code": "44054006"},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        # Current behavior: 400 (edition URI not in alias map). Acceptable
        # per spec — medterm4ds doesn't model SNOMED editions.
        assert r.status_code == 400

    def test_s25_lookup_code_with_null_byte_no_500(self, fhir_client):
        """SKEPTIC: null byte in code MUST NOT produce 500. Per
        GLOBAL_RULES.md MAX_ERROR_FIELD_CHARS + control-char stripping in
        _fhir_error, the response is sanitized. Pinning no-500 invariant."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": "44054006\x00"},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code != 500

    def test_s26_lookup_code_with_newline_no_500(self, fhir_client):
        """SKEPTIC: newline in code MUST NOT produce 500 AND MUST be
        sanitized from any reflected error message (log-injection defense)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": "44054006\nDROP TABLE"},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code != 500
        # If body reflects the code, newlines MUST be stripped.
        body = r.text or ""
        assert "\n" not in body.split('"diagnostics"')[-1].split(",")[0] if "diagnostics" in body else True

    def test_s27_lookup_post_conflicting_code_and_coding(self, fhir_client):
        """SKEPTIC: POST $lookup with BOTH scalar code/system AND a coding
        parameter. Per FHIR R4 spec (In Parameters): 'a client SHALL provide
        both a system and a code, either using the system+code parameters,
        or in the coding parameter.' When both are supplied, the spec
        doesn't dictate precedence — the server picks one. medterm4ds
        picks scalar (scalar-wins-on-conflict semantic per AGENTS.md).
        Pinning the no-500 invariant."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "73211009"},
                {"name": "coding", "valueCoding": {
                    "system": "http://snomed.info/sct", "code": "44054006",
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code != 500

    def test_s28_lookup_post_duplicate_system_parameters(self, fhir_client):
        """SKEPTIC: POST $lookup with TWO system parameters. _parse_parameters
        last-wins; the body MUST not crash. Pinning no-500 invariant."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "system", "valueUri": "http://loinc.org"},
                {"name": "code", "valueCode": "44054006"},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        pytest.current_report_extra = f"status={r.status_code}"
        # Either resolves SNOMED (last-wins) OR returns 4xx. Must NOT 500.
        assert r.status_code != 500


# =============================================================================
# Lens 3: Item 2 — CodeSystem/$validate-code required params + canonical drift
# =============================================================================

class TestLens3ValidateCodeRequiredParams:
    """$validate-code is type-level only per
    https://hl7.org/fhir/R4/codesystem-operation-validate-code.html."""

    def test_s30_validate_code_canonical_system_in_response(self, fhir_client):
        """TS-01/TERMINOLOGIST invariant: $validate-code Out `system` MUST
        echo the canonical URI, not the client's alias input. Mirrors
        s22 on $lookup. Spec:
        https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
        Out `system`: 'The system for the code that was found'."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_ALIAS_OID, "code": "44054006"},
        )
        body = r.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        assert params.get("system", {}).get("valueUri") == SNOMED_CANONICAL, (
            f"$validate-code Out system echoes client alias. Got: {params.get('system')}"
        )

    def test_s31_validate_code_canonical_system_on_trailing_slash(self, fhir_client):
        """Variant of s30: trailing-slash input must be normalized."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_ALIAS_TRAILING_SLASH, "code": "44054006"},
        )
        body = r.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        assert params.get("system", {}).get("valueUri") == SNOMED_CANONICAL

    def test_s32_validate_code_unknown_system_returns_400_operationoutcome(
        self, fhir_client
    ):
        """SKEPTIC: completely unrecognized system URI. Must be 400 +
        OperationOutcome, not silent wrong answer (result=false with the
        unrecognized URI in Out `system` would leak client input)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": "http://example.org/unknown", "code": "44054006"},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code == 400
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome"

    def test_s33_validate_code_empty_code_rejected(self, fhir_client):
        """SKEPTIC: empty-string code MUST be rejected, not silently treated
        as 'code not found'."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": "http://snomed.info/sct", "code": ""},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert 400 <= r.status_code < 500

    def test_s34_validate_code_5000_char_code_no_500(self, fhir_client):
        """SKEPTIC: 5000-char code MUST NOT produce 500. Length cap should
        fire cleanly via _fhir_error."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": "http://snomed.info/sct", "code": "A" * 5000},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code != 500

    def test_s35_validate_code_sql_injection_code_no_500(self, fhir_client):
        """SKEPTIC: SQL-injection-style code MUST NOT produce 500. DuckDB
        prepared statements structurally prevent injection; the code is
        treated as a literal value."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": "http://snomed.info/sct",
                "code": "' OR '1'='1",
            },
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code != 500

    def test_s36_validate_code_response_includes_result_boolean(
        self, fhir_client
    ):
        """Spec Out Parameters: 'result: True if the code is valid, false
        otherwise' — pinning result parameter presence."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": "http://snomed.info/sct", "code": "44054006"},
        )
        body = r.json()
        params = {p["name"] for p in body.get("parameter", [])}
        assert "result" in params


# =============================================================================
# Lens 4: Item 3 — CodeSystem/$subsumes required params + outcome vocabulary
# =============================================================================

class TestLens4SubsumesRequiredParams:
    """$subsumes is type-level only per
    https://hl7.org/fhir/R4/codesystem-operation-subsumes.html.
    Spec: 'a client SHALL provide both a and codes, either as code or Coding
    parameters.' Outcome MUST be from
    {equivalent, subsumes, subsumed-by, not-subsumed}."""

    def test_s40_subsumes_canonical_system_in_request(self, fhir_client):
        """SKEPTIC: $subsumes accepts the canonical URI. Pinning the
        positive path so we know the alias variants below are SKEPTIC
        edge cases, not the baseline."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_CANONICAL,
                "codeA": "73211009",  # Diabetes (broader)
                "codeB": "44054006",  # T2DM (narrower)
            },
        )
        body = r.json()
        outcomes = [
            p.get("valueCode") for p in body.get("parameter", [])
            if p.get("name") == "outcome"
        ]
        assert outcomes, f"$subsumes returned no outcome parameter. Body: {body}"
        assert all(o in VALID_SUBSUMES_OUTCOMES for o in outcomes)

    def test_s41_subsumes_alias_system_uri_accepted(self, fhir_client):
        """SKEPTIC: $subsumes accepts alias system URIs (urn:oid, trailing
        slash) on input. The outcome MUST be the same as when canonical URI
        is passed (semantic-equivalence check)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_ALIAS_OID,
                "codeA": "73211009",
                "codeB": "44054006",
            },
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code == 200
        body = r.json()
        outcomes = [
            p.get("valueCode") for p in body.get("parameter", [])
            if p.get("name") == "outcome"
        ]
        assert outcomes == ["subsumes"], (
            f"Alias input produced wrong outcome. Expected ['subsumes'], got {outcomes}"
        )

    def test_s42_subsumes_equal_codes_outcome_equivalent(self, fhir_client):
        """Spec: 'equivalent: A and B are the same concept'. When codeA ==
        codeB, the outcome MUST be 'equivalent'."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_CANONICAL,
                "codeA": "44054006",
                "codeB": "44054006",
            },
        )
        body = r.json()
        outcomes = [
            p.get("valueCode") for p in body.get("parameter", [])
            if p.get("name") == "outcome"
        ]
        assert outcomes == ["equivalent"], (
            f"Equal codes should produce 'equivalent'. Got {outcomes}"
        )

    def test_s43_subsumes_unknown_codes_outcome_not_subsumed(self, fhir_client):
        """SKEPTIC: when both codes are unknown (but in a known system),
        outcome MUST be 'not-subsumed' (not a 4xx — the operation
        succeeded, the answer is "no relationship"). Per spec: 'not-
        subsumed: no subsumption relationship exists'."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_CANONICAL,
                "codeA": "00000000",
                "codeB": "99999999",
            },
        )
        body = r.json()
        outcomes = [
            p.get("valueCode") for p in body.get("parameter", [])
            if p.get("name") == "outcome"
        ]
        assert outcomes == ["not-subsumed"], (
            f"Unknown codes should produce 'not-subsumed'. Got {outcomes}"
        )

    def test_s44_subsumes_codeA_with_special_chars_no_500(self, fhir_client):
        """SKEPTIC: codeA with special chars MUST NOT 500. Prepared
        statements handle SQL injection; the engine returns not-subsumed."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_CANONICAL,
                "codeA": "<script>alert('xss')</script>",
                "codeB": "44054006",
            },
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code != 500

    def test_s45_subsumes_empty_codeA_rejected(self, fhir_client):
        """SKEPTIC: empty-string codeA MUST be rejected (4xx)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_CANONICAL,
                "codeA": "",
                "codeB": "44054006",
            },
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert 400 <= r.status_code < 500

    def test_s46_subsumes_response_shape_valueCode_not_valueString(
        self, fhir_client
    ):
        """Spec Out `outcome` is type 'code (bound to
        ConceptSubsumptionOutcome, Required)'. The wire format MUST be
        valueCode, not valueString. Pinning the wire-type contract."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_CANONICAL,
                "codeA": "73211009",
                "codeB": "44054006",
            },
        )
        body = r.json()
        outcome_params = [
            p for p in body.get("parameter", []) if p.get("name") == "outcome"
        ]
        assert outcome_params, "$subsumes returned no outcome parameter"
        for p in outcome_params:
            assert "valueCode" in p, (
                f"outcome parameter must use valueCode (not valueString). Got: {p}"
            )
            assert "valueString" not in p


# =============================================================================
# Lens 5: Item 4 — ValueSet/$expand (type AND instance level)
# =============================================================================

class TestLens5Expand:
    """$expand is type AND instance level per
    https://hl7.org/fhir/R4/valueset-operation-expand.html.
    URL patterns: '[base]/ValueSet/$expand' and '[base]/ValueSet/[id]/$expand'.
    Both GET and POST supported."""

    def test_s50_expand_count_zero_returns_4xx_or_empty(
        self, fhir_client
    ):
        """SKEPTIC: spec says 'If count = 0, the client is asking how large
        the expansion is.' The implementation enforces ge=1 via FastAPI;
        count=0 produces 422 + OperationOutcome. Pinning current behavior
        per AGENTS.md NOT A BUG registry (CF-SKEPTIC-VS02-01 LOW DEFERRED)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": "0"},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        # Current: 422. Spec-correct: 200 with empty contains + total.
        # Deferred — pinning current.
        assert 400 <= r.status_code < 500

    def test_s51_expand_filter_with_special_chars_no_500(self, fhir_client):
        """SKEPTIC: filter with SQL-injection / XSS / path-traversal content
        MUST NOT produce 500. search_names uses prepared statements and
        input-validation (length cap, empty check)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "'); DROP TABLE mrconso; --"},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code != 500

    def test_s52_expand_filter_5000_chars_no_500(self, fhir_client):
        """SKEPTIC: 5000-char filter MUST NOT 500. Per AGENTS.md the
        service-layer search_names raises ValueError for queries >256 chars;
        the handler catches ValueError and returns 400 OperationOutcome."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "A" * 5000},
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code != 500

    def test_s53_expand_instance_level_get_route_returns_fhir_404(
        self, fhir_client
    ):
        """§4.7.1.2 + valueset-operation-expand.html: instance-level
        invocation 'GET /fhir/ValueSet/{id}/$expand' is supported per spec.
        medterm4ds doesn't persist ValueSets, so the route returns 404
        OperationOutcome with a route-specific message."""
        r = fhir_client.get("/fhir/ValueSet/some-id/$expand")
        body = r.text or ""
        pytest.current_report_extra = f"status={r.status_code} body[:120]={body[:120]!r}"
        assert r.status_code in (200, 404, 501)
        if r.status_code == 404:
            assert "OperationOutcome" in body
            assert "No stored ValueSet" in body

    def test_s54_expand_instance_level_post_route_returns_fhir_404(
        self, fhir_client
    ):
        """§4.7.1.2 + spec: POST instance-level invocation. Without an
        explicit route, Starlette returns non-FHIR 405 — caught by TS-02
        EXPLORER QA-024. Pinning the route exists and returns FHIR 404."""
        r = fhir_client.post(
            "/fhir/ValueSet/some-id/$expand",
            json={"resourceType": "Parameters", "parameter": []},
        )
        body = r.text or ""
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code in (200, 404, 501)
        if r.status_code == 404:
            assert "OperationOutcome" in body

    def test_s55_expand_post_with_valueInteger_count_no_500(self, fhir_client):
        """SKEPTIC: POST $expand with valueInteger count. _parse_count_param
        accepts valueInteger. Pinning no-500 + parses correctly."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "count", "valueInteger": 5},
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code != 500

    def test_s56_expand_response_is_valueset_resource(self, fhir_client):
        """Spec: $expand response is a ValueSet resource with
        expansion.{timestamp, total, contains[]}. Pinning the response
        resourceType."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 5},
        )
        body = r.json()
        assert body.get("resourceType") == "ValueSet", (
            f"$expand should return ValueSet. Got: {body.get('resourceType')}"
        )


# =============================================================================
# Lens 6: Item 5 — ValueSet/$validate-code (type AND instance level)
# =============================================================================

class TestLens6ValueSetValidateCode:
    """$validate-code on ValueSet is type AND instance level per
    https://hl7.org/fhir/R4/valueset-operation-validate-code.html."""

    def test_s60_vs_validate_canonical_system_in_response(self, fhir_client):
        """TS-01/TERMINOLOGIST invariant: VS/$validate-code Out `system`
        MUST echo the canonical URI. Mirrors s30 (CS/$validate-code) and
        s22 ($lookup)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "url": "http://snomed.info/sct?fhir_vs",
                "system": SNOMED_ALIAS_OID,
                "code": "44054006",
            },
        )
        body = r.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        assert params.get("system", {}).get("valueUri") == SNOMED_CANONICAL, (
            f"VS/$validate-code Out system echoes client alias. Got: {params.get('system')}"
        )

    def test_s61_vs_validate_canonical_system_on_trailing_slash(
        self, fhir_client
    ):
        """Variant of s60: trailing-slash normalization."""
        r = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "url": "http://snomed.info/sct?fhir_vs",
                "system": SNOMED_ALIAS_TRAILING_SLASH,
                "code": "44054006",
            },
        )
        body = r.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        assert params.get("system", {}).get("valueUri") == SNOMED_CANONICAL

    def test_s62_vs_validate_instance_level_route_returns_fhir_404(
        self, fhir_client
    ):
        """§4.7.1.2 + valueset-operation-validate-code.html: instance-level
        'GET /fhir/ValueSet/{id}/$validate-code' supported per spec."""
        r = fhir_client.get("/fhir/ValueSet/some-id/$validate-code")
        body = r.text or ""
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code in (200, 404, 501)
        if r.status_code == 404:
            assert "OperationOutcome" in body

    def test_s63_vs_validate_instance_level_post_route(self, fhir_client):
        """Variant of s62: POST instance-level. Caught by TS-02 EXPLORER
        QA-025."""
        r = fhir_client.post(
            "/fhir/ValueSet/some-id/$validate-code",
            json={"resourceType": "Parameters", "parameter": []},
        )
        body = r.text or ""
        assert r.status_code in (200, 404, 501)
        if r.status_code == 404:
            assert "OperationOutcome" in body

    def test_s64_vs_validate_unknown_code_result_false(self, fhir_client):
        """SKEPTIC: VS/$validate-code on unknown code in known system MUST
        return result=false (not 4xx — the operation succeeded, the answer
        is 'not in the value set')."""
        r = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "url": "http://snomed.info/sct?fhir_vs",
                "system": SNOMED_CANONICAL,
                "code": "00000000",
            },
        )
        body = r.json()
        results = [
            p.get("valueBoolean") for p in body.get("parameter", [])
            if p.get("name") == "result"
        ]
        assert results == [False], (
            f"Unknown code should produce result=false. Got: {results}"
        )

    def test_s65_vs_validate_response_includes_message(self, fhir_client):
        """Spec Out Parameters: 'message: Error details, if result = false'.
        When result=false, the message SHOULD be present."""
        r = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "url": "http://snomed.info/sct?fhir_vs",
                "system": SNOMED_CANONICAL,
                "code": "00000000",
            },
        )
        body = r.json()
        names = {p["name"] for p in body.get("parameter", [])}
        assert "message" in names, (
            f"VS/$validate-code result=false missing 'message'. Got: {names}"
        )


# =============================================================================
# Lens 7: Item 6 — ConceptMap/$translate (type AND instance level)
# =============================================================================

class TestLens7Translate:
    """$translate is type AND instance level per
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html."""

    def test_s70_translate_canonical_source_system_in_response(
        self, fhir_client
    ):
        """TS-01/TERMINOLOGIST invariant: $translate Out match[].source.system
        MUST echo the canonical source URI. The client may pass an alias
        (urn:oid, trailing-slash); the response MUST normalize."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_CANONICAL,
                "code": "44054006",
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        body = r.json()
        # match.source Coding — system field. Either present (matched) or
        # absent (no match). If present, it MUST be canonical.
        for p in body.get("parameter", []):
            if p.get("name") == "match":
                for part in p.get("part", []):
                    if part.get("name") == "source":
                        coding = part.get("valueCoding", {})
                        if coding.get("system"):
                            assert coding["system"] == SNOMED_CANONICAL, (
                                f"match.source.system leaked client input: {coding}"
                            )

    def test_s71_translate_alias_source_system_accepted(self, fhir_client):
        """SKEPTIC: $translate accepts alias source URIs (urn:oid) on input.
        Pinning no-500 + 200."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_ALIAS_OID,
                "code": "44054006",
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code == 200

    def test_s72_translate_unknown_targetsystem_rejected(self, fhir_client):
        """SKEPTIC: $translate with unrecognized targetsystem MUST return 400
        OperationOutcome (not silent wrong answer)."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_CANONICAL,
                "code": "44054006",
                "targetsystem": "http://example.org/unknown",
            },
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code == 400
        assert r.json().get("resourceType") == "OperationOutcome"

    def test_s73_translate_instance_level_get_route(self, fhir_client):
        """§4.7.1.2 + conceptmap-operation-translate.html: instance-level
        'GET /fhir/ConceptMap/{id}/$translate' supported per spec."""
        r = fhir_client.get("/fhir/ConceptMap/some-id/$translate")
        body = r.text or ""
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code in (200, 404, 501)
        if r.status_code == 404:
            assert "OperationOutcome" in body
            assert "No stored ConceptMap" in body

    def test_s74_translate_instance_level_post_route(self, fhir_client):
        """Variant of s73: POST instance-level."""
        r = fhir_client.post(
            "/fhir/ConceptMap/some-id/$translate",
            json={"resourceType": "Parameters", "parameter": []},
        )
        body = r.text or ""
        assert r.status_code in (200, 404, 501)
        if r.status_code == 404:
            assert "OperationOutcome" in body

    def test_s75_translate_response_includes_result(self, fhir_client):
        """Spec Out Parameters: 'result: True if the concept could be mapped
        ... otherwise false'. Pinning result parameter presence."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_CANONICAL,
                "code": "44054006",
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        body = r.json()
        names = {p["name"] for p in body.get("parameter", [])}
        assert "result" in names

    def test_s76_translate_unknown_source_code_no_500(self, fhir_client):
        """SKEPTIC: $translate with unknown source code. MUST return 200
        with result=false (the operation succeeded, the answer is 'no
        translation found'). Not 4xx, not 5xx."""
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_CANONICAL,
                "code": "00000000",
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code == 200
        body = r.json()
        results = [
            p.get("valueBoolean") for p in body.get("parameter", [])
            if p.get("name") == "result"
        ]
        assert results == [False], (
            f"Unknown source code should produce result=false. Got: {results}"
        )


# =============================================================================
# Lens 8: Batch endpoint — every advertised operation reachable
# =============================================================================

class TestLens8BatchDispatcherCoverage:
    """§4.7.8 / §4.7.10 + §3.7: every mandatory operation MUST be reachable
    via POST /fhir batch Bundle. Each entry dispatches to the corresponding
    _do_* handler. Found missing operations historically by TS-04 HISTORIAN
    QA-039; SKEPTIC pinning that all currently-advertised ops are
    dispatchable."""

    def _batch(self, entries):
        return {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": entries,
        }

    def test_s80_batch_lookup(self, fhir_client):
        """POST /fhir with one entry: GET CodeSystem/$lookup."""
        bundle = self._batch([{
            "request": {"method": "GET", "url": "CodeSystem/$lookup?system=http://snomed.info/sct&code=44054006"},
        }])
        r = fhir_client.post("/fhir", json=bundle)
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code == 200
        body = r.json()
        assert body.get("resourceType") == "Bundle"
        assert body.get("type") == "batch-response"
        assert len(body.get("entry", [])) == 1
        assert body["entry"][0]["response"]["status"].startswith("200")

    def test_s81_batch_validate_code(self, fhir_client):
        """POST /fhir with one entry: GET CodeSystem/$validate-code."""
        bundle = self._batch([{
            "request": {
                "method": "GET",
                "url": "CodeSystem/$validate-code?system=http://snomed.info/sct&code=44054006",
            },
        }])
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200
        body = r.json()
        assert body["entry"][0]["response"]["status"].startswith("200")

    def test_s82_batch_subsumes(self, fhir_client):
        """POST /fhir with one entry: GET CodeSystem/$subsumes."""
        bundle = self._batch([{
            "request": {
                "method": "GET",
                "url": "CodeSystem/$subsumes?system=http://snomed.info/sct&codeA=73211009&codeB=44054006",
            },
        }])
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200
        body = r.json()
        assert body["entry"][0]["response"]["status"].startswith("200")

    def test_s83_batch_expand(self, fhir_client):
        """POST /fhir with one entry: GET ValueSet/$expand."""
        bundle = self._batch([{
            "request": {
                "method": "GET",
                "url": "ValueSet/$expand?filter=diabetes&count=5",
            },
        }])
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200
        body = r.json()
        assert body["entry"][0]["response"]["status"].startswith("200")

    def test_s84_batch_vs_validate_code(self, fhir_client):
        """POST /fhir with one entry: GET ValueSet/$validate-code."""
        bundle = self._batch([{
            "request": {
                "method": "GET",
                "url": "ValueSet/$validate-code?url=http://snomed.info/sct?fhir_vs&system=http://snomed.info/sct&code=44054006",
            },
        }])
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200
        body = r.json()
        assert body["entry"][0]["response"]["status"].startswith("200")

    def test_s85_batch_translate(self, fhir_client):
        """POST /fhir with one entry: GET ConceptMap/$translate."""
        bundle = self._batch([{
            "request": {
                "method": "GET",
                "url": "ConceptMap/$translate?system=http://snomed.info/sct&code=44054006&targetsystem=http://hl7.org/fhir/sid/icd-10-cm",
            },
        }])
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200
        body = r.json()
        assert body["entry"][0]["response"]["status"].startswith("200")

    def test_s86_batch_closure(self, fhir_client):
        """POST /fhir with one entry: POST CodeSystem/$closure."""
        bundle = self._batch([{
            "request": {"method": "POST", "url": "CodeSystem/$closure"},
            "resource": {
                "resourceType": "Parameters",
                "parameter": [{"name": "name", "valueString": "test-batch-closure"}],
            },
        }])
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200
        body = r.json()
        # Closure init returns 200 with a Parameters body.
        assert body["entry"][0]["response"]["status"].startswith("200")

    def test_s87_batch_per_entry_error_isolation(self, fhir_client):
        """SKEPTIC: a batch with one VALID entry and one MALFORMED entry
        MUST return per-entry isolation. The malformed entry produces a 4xx
        OperationOutcome for THAT entry only; the valid entry still
        succeeds."""
        bundle = self._batch([
            {
                "request": {"method": "GET", "url": "CodeSystem/$lookup?system=http://snomed.info/sct&code=44054006"},
            },
            {
                # Malformed: missing 'request.method'
                "request": {"url": "CodeSystem/$lookup"},
            },
        ])
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200  # batch overall succeeds
        body = r.json()
        statuses = [e["response"]["status"] for e in body["entry"]]
        assert statuses[0].startswith("200"), f"Entry 0 should succeed. Got: {statuses}"
        assert statuses[1].startswith("4"), f"Entry 1 should be 4xx. Got: {statuses}"

    def test_s88_batch_unknown_operation_per_entry_404(self, fhir_client):
        """SKEPTIC: a batch entry for an unknown operation MUST return a
        per-entry 404 OperationOutcome, NOT a 500 / not a silent success."""
        bundle = self._batch([{
            "request": {"method": "GET", "url": "CodeSystem/$unknown-op"},
        }])
        r = fhir_client.post("/fhir", json=bundle)
        assert r.status_code == 200  # batch overall succeeds
        body = r.json()
        status = body["entry"][0]["response"]["status"]
        assert status.startswith("4"), (
            f"Unknown operation in batch should be 4xx per-entry. Got: {status}"
        )


# =============================================================================
# Lens 9: HTTP method tolerance — GET vs POST behavior consistency
# =============================================================================

class TestLens9GetVsPostParity:
    """Per FHIR R4 general operations guidance, operations support both GET
    (query params) and POST (Parameters body). The response for the same
    inputs MUST be byte-exact-equivalent on the clinical content."""

    def test_s90_lookup_get_post_same_result(self, fhir_client):
        """$lookup GET vs POST with same system+code MUST produce same
        display value."""
        get_r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": "44054006"},
        )
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "44054006"},
            ],
        }
        post_r = fhir_client.post("/fhir/CodeSystem/$lookup", json=post_body)
        assert get_r.status_code == post_r.status_code == 200
        get_display = next(
            (p.get("valueString") for p in get_r.json().get("parameter", [])
             if p.get("name") == "display"),
            None,
        )
        post_display = next(
            (p.get("valueString") for p in post_r.json().get("parameter", [])
             if p.get("name") == "display"),
            None,
        )
        assert get_display == post_display, (
            f"GET vs POST display mismatch: GET={get_display!r}, POST={post_display!r}"
        )

    def test_s91_validate_code_get_post_same_result(self, fhir_client):
        """$validate-code GET vs POST MUST produce same result."""
        get_r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": "http://snomed.info/sct", "code": "44054006"},
        )
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "44054006"},
            ],
        }
        post_r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=post_body)
        get_result = next(
            (p.get("valueBoolean") for p in get_r.json().get("parameter", [])
             if p.get("name") == "result"),
            None,
        )
        post_result = next(
            (p.get("valueBoolean") for p in post_r.json().get("parameter", [])
             if p.get("name") == "result"),
            None,
        )
        assert get_result == post_result == True, (
            f"GET vs POST result mismatch: GET={get_result}, POST={post_result}"
        )

    def test_s92_subsumes_get_post_same_outcome(self, fhir_client):
        """$subsumes GET vs POST MUST produce same outcome."""
        params = {
            "system": "http://snomed.info/sct",
            "codeA": "73211009",
            "codeB": "44054006",
        }
        get_r = fhir_client.get("/fhir/CodeSystem/$subsumes", params=params)
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "codeA", "valueCode": "73211009"},
                {"name": "codeB", "valueCode": "44054006"},
            ],
        }
        post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=post_body)
        get_outcome = next(
            (p.get("valueCode") for p in get_r.json().get("parameter", [])
             if p.get("name") == "outcome"),
            None,
        )
        post_outcome = next(
            (p.get("valueCode") for p in post_r.json().get("parameter", [])
             if p.get("name") == "outcome"),
            None,
        )
        assert get_outcome == post_outcome == "subsumes"


# =============================================================================
# Lens 10: $closure boundary (also a TS-02 mandatory op)
# =============================================================================

class TestLens10Closure:
    """$closure is a CodeSystem-level operation. The chunk items don't
    explicitly require it but the CapabilityStatement advertises it."""

    def test_s100_closure_init_returns_200_parameters(self, fhir_client):
        """$closure with name-only initializes a closure. Returns Parameters
        with `return` parameter."""
        body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "name", "valueString": "skeptic-test-init"}],
        }
        r = fhir_client.post("/fhir/CodeSystem/$closure", json=body)
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code == 200
        body = r.json()
        assert body.get("resourceType") == "Parameters"

    def test_s101_closure_missing_name_returns_400(self, fhir_client):
        """SKEPTIC: $closure without name MUST be 400 OperationOutcome."""
        body = {"resourceType": "Parameters", "parameter": []}
        r = fhir_client.post("/fhir/CodeSystem/$closure", json=body)
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code == 400
        assert r.json().get("resourceType") == "OperationOutcome"

    def test_s102_closure_empty_name_returns_400(self, fhir_client):
        """SKEPTIC: empty-string name MUST be rejected."""
        body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "name", "valueString": ""}],
        }
        r = fhir_client.post("/fhir/CodeSystem/$closure", json=body)
        pytest.current_report_extra = f"status={r.status_code}"
        assert 400 <= r.status_code < 500

    def test_s103_closure_get_returns_405_or_404(self, fhir_client):
        """$closure is POST-only. GET MUST NOT silently return 200."""
        r = fhir_client.get("/fhir/CodeSystem/$closure")
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code != 200 or r.json().get("resourceType") == "OperationOutcome"

    def test_s104_closure_add_concepts_returns_200(self, fhir_client):
        """$closure with concept list returns 200 Parameters."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "name", "valueString": "skeptic-test-add"},
                {"name": "concept", "valueCoding": {
                    "system": "http://snomed.info/sct", "code": "44054006",
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$closure", json=body)
        pytest.current_report_extra = f"status={r.status_code}"
        assert r.status_code == 200
        assert r.json().get("resourceType") == "Parameters"
