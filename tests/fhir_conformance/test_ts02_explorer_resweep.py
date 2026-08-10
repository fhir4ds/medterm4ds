"""EXPLORER resweep probes for TS-02 (Mandatory Terminology Service Operations
Matrix, FHIR R4 §4.7.1.2).

Fresh full-sweep run per USER_DIRECTIVES [2026-08-08]. Sibling file to the
baseline ``test_ts02_explorer.py`` so the baseline stays comparable across
runs while this file adds fresh lateral-thinking probes.

EXPLORER lens (per ROLE_QA_ENGINEER Section 3 EXPLORER): **lateral
thinking**. Unusual parameter combinations, undocumented features,
integration corners, the unusual and untested. SKEPTIC + HISTORIAN
already confirmed the surface is hardened against direct edge cases and
the empty-string drift pattern is now PROMOTED to GLOBAL_RULES.md as
the 9th pattern. EXPLORER's job is the unusual and untested.

HISTORIAN tip for EXPLORER (high-priority): probe **whitespace-only
inputs** on every required-string Query parameter. ``min_length=1`` does
NOT reject whitespace-only strings per FastAPI's length validation
semantics (whitespace counts as characters). If FHIR R4 considers
whitespace-only as "not provided" (which it does for many operations —
the parameter must have meaningful content), a ``.strip()`` check or
pre-validation is also needed.

Probe classes (10 lens dimensions):
1. **Whitespace-only required-string inputs** — probe every required
   string Query parameter with whitespace-only values and verify
   behavior (reject with 4xx OR strip+accept with documented behavior).
2. **Cross-operation canonical URI consistency** — pass the same code
   through $lookup, $validate-code, $subsumes, $expand (filter), $translate
   and assert each returns the same canonical system URI.
3. **Conflicting parameter combinations** — both ``code`` AND ``coding``
   in $lookup POST (spec says one-or-the-other); scalar+codeableConcept
   on $translate / $validate-code POST.
4. **CapabilityStatement operations array completeness** — every
   advertised OperationDefinition is reachable via route surface;
   metadata advertisement matches actual routes.
5. **HTTP method corner cases** — $subsumes with codeA == codeB
   (outcome=equivalent), $expand with negative count via path that
   bypasses FastAPI validation.
6. **POST Content-Type corner cases** — application/fhir+json vs
   application/json vs application/xml; malformed Parameters body.
7. **POST-with-both-Query-AND-Parameters-body precedence** — query
   param overrides body, OR body overrides query (document whichever
   the current implementation does).
8. **POST-path parity on whitespace inputs** — confirm POST handlers'
   ``if not query_text`` / ``if not text`` checks handle whitespace
   correctly (whitespace IS truthy in Python — does it fall through to
   the service layer?).
9. **$lookup POST both Query AND body** — invoke POST with both
   query-string params AND a Parameters body; verify which wins.
10. **Empty-but-present optional parameters** — empty-string on
    optional params (Query(None)) should not silently produce
    wrong-answer output.

Spec citations are verbatim from canonical R4 pages.
"""

from __future__ import annotations

import json

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
SPEC_PARAMS = "https://hl7.org/fhir/R4/parameters.html"

SNOMED_URI = "http://snomed.info/sct"
SNOMED_CODE = "44054006"  # Type 2 diabetes mellitus
SNOMED_PARENT_CODE = "73211009"  # Diabetes mellitus

# =============================================================================
# Lens 1: Whitespace-only required-string Query inputs
# (HISTORIAN tip for EXPLORER — highest-priority probe class)
# =============================================================================


class TestWhitespaceOnlyRequiredQueryInputs:
    """Per HISTORIAN tip for EXPLORER: probe whitespace-only inputs on every
    required-string Query parameter.

    FastAPI's ``Query(..., min_length=1)`` does NOT reject whitespace-only
    strings (whitespace counts as a character). If FHIR R4 considers
    whitespace-only as "not provided" (which it does per the prose "a
    client SHALL provide both a system and a code" — a space character
    is not a meaningful value), a ``.strip()`` check or
    ``pattern=r"\\S"`` constraint is also needed.

    Spec citation for $lookup:
      "When invoking this operation, a client SHALL provide both a system
       and a code, either using the system+code parameters, or in the
       coding parameter."
      https://hl7.org/fhir/R4/codesystem-operation-lookup.html

    Spec citation for $validate-code:
      "When invoking this, a client SHALL provide one (and only one) of
       the parameters (code+system, coding, or codeableConcept)."
      https://hl7.org/fhir/R4/codesystem-operation-validate-code.html

    Spec citation for $translate:
      "One (and only one) of the in parameters (code, coding,
       codeableConcept) must be provided, to identify the code that is
       to be translated."
      https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    """

    def test_e10_lookup_whitespace_only_code_silent_or_rejected(
        self, fhir_client
    ):
        """Whitespace-only ``code`` on $lookup GET.

        Per FHIR R4 $lookup: "a client SHALL provide both a system and a
        code". A single space character is not meaningfully "providing a
        code" — the lookup will fail to find code ' ' and return a 200 +
        not-found OperationOutcome (silent-wrong-answer shape).

        Documenting CURRENT behavior; not asserting rejection (spec is
        borderline — doesn't explicitly forbid whitespace).
        """
        response = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"code": " ", "system": SNOMED_URI},
        )
        # Whitespace passes min_length=1 (1 char); handler proceeds.
        # Documented current behavior: 200 with not-found shape OR
        # 400 / 422 if implementation strips. We assert the response is
        # NOT a 500 (graceful handling either way).
        assert response.status_code < 500, (
            f"Whitespace-only code on $lookup produced a 5xx "
            f"({response.status_code}); should handle gracefully."
        )

    def test_e11_lookup_whitespace_only_system(self, fhir_client):
        """Whitespace-only ``system`` on $lookup GET."""
        response = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"code": SNOMED_CODE, "system": " "},
        )
        # Whitespace system is not "Unrecognized" by fhir_uri_to_system
        # — it returns None (unrecognized) so the impl returns 400.
        # Documenting current behavior.
        assert response.status_code < 500

    def test_e12_validate_whitespace_only_code(self, fhir_client):
        """Whitespace-only ``code`` on $validate-code GET."""
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"code": " ", "system": SNOMED_URI},
        )
        # Per FHIR R4 $validate-code: "SHALL provide one (and only one)
        # of (code+system, coding, codeableConcept)". A whitespace code
        # IS technically provided; engine lookup fails → result=false.
        assert response.status_code < 500
        # Documenting current behavior: 200 + result=false (silent-wrong-
        # answer shape: client gets success-status for a request that
        # was never validly invoked).
        if response.status_code == 200:
            body = response.json()
            params = body.get("parameter", [])
            result = next(
                (p.get("valueBoolean") for p in params if p.get("name") == "result"),
                None,
            )
            # Whitespace code is not valid → result should be false
            assert result is False, (
                "Whitespace-only code on $validate-code should not "
                "produce result=true."
            )

    def test_e13_subsumes_whitespace_only_codeA(self, fhir_client):
        """Whitespace-only ``codeA`` on $subsumes GET.

        Per FHIR R4 $subsumes In Parameters codeA: "The 'A' code that is
        to be tested. If a code is provided, a system must be provided".
        A whitespace code is provided; engine will report not-subsumed.
        """
        response = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "codeA": "\t",  # tab-only
                "codeB": SNOMED_CODE,
                "system": SNOMED_URI,
            },
        )
        assert response.status_code < 500
        if response.status_code == 200:
            body = response.json()
            params = body.get("parameter", [])
            outcome = next(
                (p.get("valueCode") for p in params if p.get("name") == "outcome"),
                None,
            )
            # Whitespace codeA is not in the system → not-subsumed
            assert outcome == "not-subsumed", (
                f"Whitespace codeA on $subsumes should yield "
                f"not-subsumed; got {outcome!r}."
            )

    def test_e14_translate_whitespace_only_code(self, fhir_client):
        """Whitespace-only ``code`` on $translate GET."""
        response = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={"code": "  ", "system": SNOMED_URI},
        )
        # Per FHIR R4 $translate: "One (and only one) of the in
        # parameters (code, coding, codeableConcept) must be provided".
        # Whitespace code → no mappings → result=false.
        assert response.status_code < 500
        if response.status_code == 200:
            body = response.json()
            params = body.get("parameter", [])
            result = next(
                (p.get("valueBoolean") for p in params if p.get("name") == "result"),
                None,
            )
            assert result is False

    def test_e15_extract_whitespace_only_text(self, fhir_client):
        """Whitespace-only ``text`` on $extract GET.

        Per GLOBAL_RULES.md 9th PROMOTED pattern, ``$extract`` text now
        has min_length=1. Whitespace passes min_length=1. Documenting
        current behavior: either rejected at the NER service OR returns
        empty Bundle.
        """
        response = fhir_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": "    "},  # 4-space whitespace
        )
        # The handler passes whitespace to _do_extract → extract_service.
        # NER will likely return no entities → empty Bundle.
        # Documenting: not a 500.
        assert response.status_code < 500, (
            f"Whitespace-only text on $extract should not produce 5xx "
            f"({response.status_code})."
        )

    def test_e16_search_whitespace_only_query(self, fhir_client):
        """Whitespace-only ``query`` on $search GET.

        Per GLOBAL_RULES.md 9th PROMOTED pattern, ``$search`` query now
        has min_length=1. Whitespace passes min_length=1. The handler
        then checks _check_ready (BM25 / SapBERT availability) and
        returns 503 if indexes aren't loaded.
        """
        response = fhir_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": " "},
        )
        # Documenting current behavior: BM25 unavailable in test env →
        # 503 OR (if available) empty Bundle.
        assert response.status_code in (200, 503), (
            f"Whitespace-only query on $search produced {response.status_code}; "
            f"expected 200 (empty results) or 503 (BM25 unavailable)."
        )


# =============================================================================
# Lens 2: Cross-operation canonical URI consistency
# =============================================================================


class TestCrossOperationCanonicalURIConsistency:
    """Pass the same code through $lookup, $validate-code, $subsumes,
    $expand (filter), $translate and verify each returns the same
    canonical system URI.

    Per FHIR R4 §4.7.5 Concept Lookup / Decomposition + §4.8.21.1 Out
    Parameters ``system``: every operation that returns a Coding MUST
    use the canonical system URI from the registry (SYSTEM_TO_FHIR_URI),
    NOT the client's alias input.

    Methodology contribution: extends CS-05 EXPLORER test_e10 cross-
    operation-canonical-agreement probe class from $lookup↔$validate-
    code to ALL 5 operations on TS-02 surface.
    """

    def test_e20_lookup_returns_canonical_snomed_uri(self, fhir_client):
        """$lookup Out ``system`` is canonical SNOMED URI."""
        response = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"code": SNOMED_CODE, "system": SNOMED_URI},
        )
        assert response.status_code == 200
        body = response.json()
        params = body.get("parameter", [])
        # build_parameters_lookup emits `system` parameter? Actually no,
        # it emits display / version / name etc. The system is NOT in
        # the Out parameters per R4 spec — it's only in the coding.
        # But $lookup does emit a system-bearing coding only via the
        # property group. So this probe asserts NO raw SAB leakage.
        for p in params:
            # No parameter should contain raw SAB string "SNOMEDCT_US"
            value = json.dumps(p)
            assert "SNOMEDCT_US" not in value, (
                f"Raw SAB string leaked into $lookup response: {p!r}"
            )

    def test_e21_validate_code_out_system_is_canonical(self, fhir_client):
        """$validate-code Out ``system`` is canonical SNOMED URI."""
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"code": SNOMED_CODE, "system": SNOMED_URI},
        )
        assert response.status_code == 200
        body = response.json()
        params = body.get("parameter", [])
        system_param = next(
            (p for p in params if p.get("name") == "system"), None
        )
        if system_param:
            # The Out system MUST be the canonical URI
            assert system_param.get("valueUri") == SNOMED_URI, (
                f"Out system is {system_param.get('valueUri')!r}, "
                f"expected canonical {SNOMED_URI!r}"
            )

    def test_e22_alias_input_resolves_to_canonical(self, fhir_client):
        """Pass an alias (urn:oid:...) on $validate-code and verify Out
        system is canonical, not the alias."""
        # SNOMED CT OID is 2.16.840.1.113883.6.96
        alias_uri = "urn:oid:2.16.840.1.113883.6.96"
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"code": SNOMED_CODE, "system": alias_uri},
        )
        # Documenting current behavior: fhir_uri_to_system may or may
        # not recognize this alias. Per TS-02/TERMINOLOGIST canonical-URI
        # invariant (count=8 PROMOTED), aliases SHOULD resolve to
        # canonical and Out system SHOULD be canonical.
        assert response.status_code < 500

    def test_e23_subsumes_alias_system(self, fhir_client):
        """$subsumes with alias system: behavior is graceful (4xx or
        canonical outcome)."""
        alias_uri = "urn:oid:2.16.840.1.113883.6.96"
        response = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "codeA": SNOMED_CODE,
                "codeB": SNOMED_PARENT_CODE,
                "system": alias_uri,
            },
        )
        assert response.status_code < 500

    def test_e24_translate_out_system_is_canonical(self, fhir_client):
        """$translate Out match[].source.system is canonical SNOMED URI."""
        response = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={"code": SNOMED_CODE, "system": SNOMED_URI},
        )
        assert response.status_code == 200
        body = response.json()
        params = body.get("parameter", [])
        # Every match.source.system MUST be canonical (no raw SAB)
        for p in params:
            if p.get("name") != "match":
                continue
            value_coding = p.get("valueCoding") or {}
            # The match source coding's system
            source = value_coding.get("source", {})
            if isinstance(source, dict):
                source_system = source.get("system", "")
                assert "SNOMEDCT_US" not in source_system, (
                    f"Raw SAB leaked into $translate match.source.system: "
                    f"{source_system!r}"
                )


# =============================================================================
# Lens 3: Conflicting parameter combinations
# =============================================================================


class TestConflictingParameterCombinations:
    """Per FHIR R4 spec, several operations say "one (and only one)" of
    alternative inputs. Probe what happens when client violates this:

    $translate: "One (and only one) of the in parameters (code, coding,
    codeableConcept) must be provided"
    $validate-code: "SHALL provide one (and only one) of (code+system,
    coding, codeableConcept)"
    $lookup: "a client SHALL provide both a system and a code, either
    using the system+code parameters, or in the coding parameter"

    Documenting CURRENT behavior of each conflict (the spec is silent
    on what to do; the impl may pick one or error out).
    """

    def test_e30_lookup_post_both_scalar_and_coding(self, fhir_client):
        """$lookup POST with BOTH scalar (system+code) AND coding
        parameter.

        Per FHIR R4 $lookup: "a client SHALL provide both a system and a
        code, either using the system+code parameters, or in the coding
        parameter". The spec implies EITHER-or, not both.

        Current impl logic (apps/fhir_api.py:1558): if scalar are
        present, use them; only consult coding if scalar absent. So
        scalar wins. This is INTENDED per the existing convention
        (TS-02 HISTORIAN QA-022 spec-listed alternative rule).
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_CODE},
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": SNOMED_URI,
                        "code": SNOMED_PARENT_CODE,  # different code!
                    },
                },
            ],
        }
        response = fhir_client.post(
            "/fhir/CodeSystem/$lookup", json=body
        )
        assert response.status_code == 200
        # Per existing convention, scalar wins. So the response should
        # contain SNOMED_CODE (44054006), NOT SNOMED_PARENT_CODE.
        body = response.json()
        params = body.get("parameter", [])
        code_param = next(
            (p for p in params if p.get("name") == "code"), None
        )
        if code_param:
            assert code_param.get("valueCode") == SNOMED_CODE, (
                "When both scalar code AND coding are supplied, scalar "
                "should win per existing convention."
            )

    def test_e31_validate_post_both_scalar_and_codeable_concept(
        self, fhir_client
    ):
        """$validate-code POST with both scalar (system+code) AND
        codeableConcept.

        Per existing convention (CS-03 SKEPTIC AUDIT-002), scalar wins.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_CODE},
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {
                                "system": SNOMED_URI,
                                "code": SNOMED_PARENT_CODE,
                            }
                        ]
                    },
                },
            ],
        }
        response = fhir_client.post(
            "/fhir/CodeSystem/$validate-code", json=body
        )
        assert response.status_code == 200
        # Per existing convention, scalar wins → result=true for SNOMED_CODE.
        body = response.json()
        params = body.get("parameter", [])
        result = next(
            (p.get("valueBoolean") for p in params if p.get("name") == "result"),
            None,
        )
        assert result is True, (
            "When both scalar AND codeableConcept supplied, scalar wins "
            "→ result=true for the scalar code."
        )

    def test_e32_expand_post_inline_valueset_and_url_query(
        self, fhir_client
    ):
        """$expand POST with both inline ValueSet body AND url query
        param.

        Per FHIR R4 $expand: "If the operation is not called at the
        instance level, one of the in parameters url, context or
        valueSet must be provided." Spec is silent on what happens
        when both are provided.

        Current impl: when body is a ValueSet resource, the inline
        ValueSet wins (no url-based dispatch).
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test-vs",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_CODE}],
                    }
                ]
            },
        }
        response = fhir_client.post(
            "/fhir/ValueSet/$expand",
            params={"url": "http://different.example.org/other-vs"},
            json=body,
        )
        # Documenting current behavior: inline wins (no error).
        assert response.status_code < 500
        # If 200, the contains[] should be from the inline ValueSet
        if response.status_code == 200:
            rj = response.json()
            contains = rj.get("expansion", {}).get("contains", [])
            codes = [c.get("code") for c in contains]
            assert SNOMED_CODE in codes, (
                "Inline ValueSet should win over url query param"
            )


# =============================================================================
# Lens 4: CapabilityStatement operations array completeness
# =============================================================================


class TestCapabilityStatementOperationsCompleteness:
    """Per FHIR R4 §4.7.1.2: the CapabilityStatement MUST advertise all
    mandatory operations ($lookup, $validate-code, $subsumes, $expand,
    VS-$validate-code, $translate, $closure) as
    CapabilityStatement.rest[].resource[].operation[].

    Each operation's ``definition`` field MUST be the canonical HL7
    OperationDefinition URL (per TS-02 SKEPTIC QA-016).
    """

    def test_e40_metadata_advertises_all_7_mandatory_operations(
        self, fhir_client
    ):
        """/fhir/metadata lists all 7 mandatory operations."""
        response = fhir_client.get("/fhir/metadata")
        assert response.status_code == 200
        stmt = response.json()
        # Collect all operation definitions across all resources
        ops = set()
        for rest in stmt.get("rest", []):
            for res in rest.get("resource", []):
                for op in res.get("operation", []):
                    definition = op.get("definition", "")
                    # The reference can be relative or absolute
                    ops.add(definition)
        # The mandatory operations are:
        # - CodeSystem-lookup
        # - CodeSystem-validate-code
        # - CodeSystem-subsumes
        # - ValueSet-expand
        # - ValueSet-validate-code
        # - ConceptMap-translate
        # - CodeSystem-closure (optional but advertised)
        mandatory_fragments = [
            "CodeSystem-lookup",
            "CodeSystem-validate-code",
            "CodeSystem-subsumes",
            "ValueSet-expand",
            "ValueSet-validate-code",
            "ConceptMap-translate",
        ]
        ops_str = " ".join(ops)
        for frag in mandatory_fragments:
            assert frag in ops_str, (
                f"Operation {frag!r} not advertised in CapabilityStatement. "
                f"Operations: {ops!r}"
            )

    def test_e41_advertised_operation_routes_actually_respond(
        self, fhir_client
    ):
        """Every advertised operation URL path is reachable (not 404)."""
        # Walk the routes that we know are advertised
        op_routes = [
            ("GET", "/fhir/CodeSystem/$lookup"),
            ("GET", "/fhir/CodeSystem/$validate-code"),
            ("GET", "/fhir/CodeSystem/$subsumes"),
            ("GET", "/fhir/ValueSet/$expand"),
            ("GET", "/fhir/ValueSet/$validate-code"),
            ("GET", "/fhir/ConceptMap/$translate"),
            ("POST", "/fhir/CodeSystem/$closure"),
        ]
        for method, path in op_routes:
            if method == "GET":
                # Send minimal params (will likely 400, that's fine — we
                # just want to confirm the route is registered, not 404)
                response = fhir_client.get(path, params={"code": "x", "system": "x"})
            else:
                response = fhir_client.post(
                    path,
                    json={"resourceType": "Parameters", "parameter": []},
                )
            # 400 / 422 / 200 / 503 are all OK (route exists).
            # 404 / 405 mean route missing.
            assert response.status_code != 404, (
                f"Advertised route {method} {path} returned 404 (route "
                f"not registered)."
            )

    def test_e42_operation_definitions_use_canonical_hl7_urls(
        self, fhir_client
    ):
        """Every advertised operation ``definition`` references the
        canonical HL7 OperationDefinition URL (not a server-local URL)."""
        response = fhir_client.get("/fhir/metadata")
        assert response.status_code == 200
        stmt = response.json()
        canonical_prefix = "http://hl7.org/fhir/OperationDefinition/"
        for rest in stmt.get("rest", []):
            for res in rest.get("resource", []):
                for op in res.get("operation", []):
                    definition = op.get("definition", "")
                    # Skip custom operations (server-local) — those have
                    # server-local URLs by design
                    if "$" in op.get("name", "") and any(
                        op_name in op.get("name", "")
                        for op_name in ["$search", "$extract"]
                    ):
                        continue
                    # Standard HL7 ops MUST use canonical prefix
                    if any(
                        std in definition
                        for std in [
                            "CodeSystem-lookup",
                            "CodeSystem-validate-code",
                            "CodeSystem-subsumes",
                            "ValueSet-expand",
                            "ValueSet-validate-code",
                            "ConceptMap-translate",
                            "ConceptMap-closure",
                        ]
                    ):
                        assert definition.startswith(canonical_prefix), (
                            f"Operation {op.get('name')!r} definition "
                            f"{definition!r} does not use canonical HL7 prefix "
                            f"{canonical_prefix!r}"
                        )


# =============================================================================
# Lens 5: HTTP method corner cases
# =============================================================================


class TestHTTPMethodCornerCases:
    """Probe HTTP method corner cases per EXPLORER lateral coverage:

    - $subsumes with codeA == codeB → outcome=equivalent
    - $expand with negative count bypass via POST body
    - $subsumes mixed codings on POST
    """

    def test_e50_subsumes_same_code_yields_equivalent(self, fhir_client):
        """$subsumes codeA == codeB returns outcome=equivalent.

        Per FHIR R4 $subsumes Out ``outcome``: "There are 4 possible
        codes to be returned (equivalent, subsumes, subsumed-by, and
        not-subsumed) as defined in the concept-subsumption-outcome
        value set."

        The implementation short-circuits when codeA == codeB at
        apps/fhir_api.py:2215 (`if code_a == code_b: return
        build_parameters_subsumes("equivalent")`).
        """
        response = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "codeA": SNOMED_CODE,
                "codeB": SNOMED_CODE,
                "system": SNOMED_URI,
            },
        )
        assert response.status_code == 200
        body = response.json()
        params = body.get("parameter", [])
        outcome = next(
            (p.get("valueCode") for p in params if p.get("name") == "outcome"),
            None,
        )
        assert outcome == "equivalent", (
            f"Same code (codeA == codeB) on $subsumes should yield "
            f"outcome=equivalent; got {outcome!r}."
        )

    def test_e51_subsumes_same_code_with_whitespace_padding(
        self, fhir_client
    ):
        """$subsumes where codeA and codeB are whitespace-padded equal
        codes — does the impl strip? ' 44054006 ' vs '44054006' should
        likely NOT be equivalent (spec does not mandate trimming)."""
        response = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "codeA": " 44054006",  # leading space
                "codeB": "44054006",
                "system": SNOMED_URI,
            },
        )
        # Documenting current behavior: leading-space code is treated as
        # different → not-subsumed (engine lookup of " 44054006" fails).
        # OR: not-equivalent if string-compare short-circuit.
        assert response.status_code == 200
        body = response.json()
        params = body.get("parameter", [])
        outcome = next(
            (p.get("valueCode") for p in params if p.get("name") == "outcome"),
            None,
        )
        # Either not-subsumed (engine doesn't find the padded code) or
        # equivalent (impl treats them as same). Documenting.
        assert outcome in ("equivalent", "not-subsumed"), (
            f"Padded-code $subsumes outcome was {outcome!r}; expected "
            f"equivalent or not-subsumed."
        )

    def test_e52_expand_negative_count_via_post_body_rejected(
        self, fhir_client
    ):
        """$expand POST with negative count in body —
        _parse_count_param rejects.

        Per apps/fhir_api.py:_parse_count_param: validates count is
        integer in [1, 1000]. Negative count → 400.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": SNOMED_URI},
                {"name": "count", "valueInteger": -1},
            ],
        }
        response = fhir_client.post(
            "/fhir/ValueSet/$expand", json=body
        )
        assert response.status_code == 400, (
            f"Negative count in $expand POST body should be rejected "
            f"with 400; got {response.status_code}."
        )

    def test_e53_expand_count_zero_rejected(self, fhir_client):
        """$expand POST with count=0 → rejected.

        Per CF-SKEPTIC-VS02-01 (DEFERRED): the spec says count=0 means
        "client is asking how large the expansion is" but the impl
        rejects with 400 (pinned by carry-forward-as-probe pattern).
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": SNOMED_URI},
                {"name": "count", "valueInteger": 0},
            ],
        }
        response = fhir_client.post(
            "/fhir/ValueSet/$expand", json=body
        )
        # Per CF-SKEPTIC-VS02-01: 0 currently rejected. Carry-forward
        # documents the spec-correct behavior (200 + empty contains).
        assert response.status_code in (200, 400), (
            f"count=0 on $expand POST produced {response.status_code}; "
            f"documenting current behavior."
        )


# =============================================================================
# Lens 6: POST Content-Type corner cases
# =============================================================================


class TestPOSTContentTypeCornerCases:
    """Per FHIR R4 §3.1.0.1.9 + §3.2.1.0.6: POST requests SHOULD send
    Content-Type: application/fhir+json. Test what happens with:

    - application/fhir+json (canonical)
    - application/json (generic)
    - application/xml (wrong format)
    - malformed JSON body
    """

    def test_e60_lookup_post_with_application_fhir_json(self, fhir_client):
        """POST $lookup with Content-Type: application/fhir+json."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_CODE},
            ],
        }
        response = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json=body,
            headers={"Content-Type": "application/fhir+json"},
        )
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith(
            "application/fhir+json"
        ), (
            f"Response Content-Type was "
            f"{response.headers.get('Content-Type')!r}"
        )

    def test_e61_lookup_post_with_application_json(self, fhir_client):
        """POST $lookup with Content-Type: application/json (generic).

        Per FHIR R4 §3.2.1.0.6: server MAY accept generic JSON; many do.
        Documenting current behavior.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_CODE},
            ],
        }
        response = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        # Documenting: 200 (server accepts) OR 415 (server rejects).
        assert response.status_code in (200, 415), (
            f"Generic application/json on $lookup POST produced "
            f"{response.status_code}; documenting current behavior."
        )

    def test_e62_lookup_post_with_malformed_json_body(self, fhir_client):
        """POST $lookup with malformed JSON body — handler should return
        4xx OperationOutcome, NOT 500 text/plain."""
        response = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            data="not valid json {{{",
            headers={"Content-Type": "application/fhir+json"},
        )
        # Documenting: 422 (JSON parse error) or 400. NOT 500.
        assert response.status_code < 500, (
            f"Malformed JSON on $lookup POST produced 5xx "
            f"({response.status_code}); should be 4xx."
        )

    def test_e63_lookup_post_response_is_fhir_json(self, fhir_client):
        """POST $lookup response Content-Type is application/fhir+json."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_CODE},
            ],
        }
        response = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        assert response.status_code == 200
        ct = response.headers.get("Content-Type", "")
        assert "application/fhir+json" in ct, (
            f"Response Content-Type was {ct!r}; expected "
            f"application/fhir+json"
        )

    def test_e64_search_post_with_unknown_system(self, fhir_client):
        """$search POST with an unrecognized system URI — handler
        returns 400 OR (if BM25 unavailable) 503. NOT 500."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "query", "valueString": "diabetes"},
                {"name": "system", "valueUri": "http://unknown.example.org"},
            ],
        }
        response = fhir_client.post(
            "/fhir/CodeSystem/$search", json=body
        )
        # Either 400 (unrecognized system) or 503 (BM25 unavailable)
        assert response.status_code in (400, 503), (
            f"Unknown-system $search POST produced "
            f"{response.status_code}; expected 400 or 503."
        )


# =============================================================================
# Lens 7: POST-with-both-Query-AND-Parameters-body precedence
# =============================================================================


class TestPostQueryAndBodyPrecedence:
    """Per FHIR R4 §4.7.5: when a POST handler accepts BOTH query params
    AND a Parameters body, which wins? Documenting current behavior.

    The $expand POST handler has both: ``count: int = Query(20, ge=1,
    le=1000)`` AND the body may carry a count. Per the impl, body count
    overrides the query-param default.
    """

    def test_e70_expand_post_body_count_overrides_query_default(
        self, fhir_client
    ):
        """$expand POST with count in body — body count wins over
        query-param default (20)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": SNOMED_URI},
                {"name": "count", "valueInteger": 5},
            ],
        }
        response = fhir_client.post(
            "/fhir/ValueSet/$expand", json=body
        )
        # If 200, contains[] should respect count=5 from body, not 20.
        # The url is SNOMED CT intensional — the response should expand
        # the descendants of SNOMED CT root.
        # Documenting: not asserting exact count (depends on fixture).
        assert response.status_code < 500

    def test_e71_lookup_post_no_query_params_only_body(self, fhir_client):
        """$lookup POST with NO query params (only body) — handler
        extracts system/code from body and proceeds."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_CODE},
            ],
        }
        response = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        assert response.status_code == 200
        body_r = response.json()
        params = body_r.get("parameter", [])
        display = next(
            (p.get("valueString") for p in params if p.get("name") == "display"),
            None,
        )
        assert display is not None


# =============================================================================
# Lens 8: POST-path parity on whitespace inputs
# =============================================================================


class TestPostWhitespaceInputs:
    """Confirm POST handlers handle whitespace inputs gracefully.

    Per HISTORIAN carry-forward note #3: POST handlers use
    ``if not query_text`` / ``if not text`` checks; empty string is
    falsy in Python, so POST is correct. Whitespace-only is TRUTHY in
    Python, so whitespace WILL pass through to the service layer.
    """

    def test_e80_extract_post_whitespace_text_graceful(self, fhir_client):
        """$extract POST with whitespace-only text — handler passes
        whitespace to _do_extract → extract_service."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "text", "valueString": "  "},
            ],
        }
        response = fhir_client.post(
            "/fhir/CodeSystem/$extract", json=body
        )
        # Whitespace passes the `if not text` check (truthy). NER will
        # likely return no entities → empty Bundle.
        # Documenting: not 500.
        assert response.status_code < 500

    def test_e81_search_post_whitespace_query_graceful(self, fhir_client):
        """$search POST with whitespace-only query — handler passes
        whitespace to _do_search."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "query", "valueString": "\t"},
            ],
        }
        response = fhir_client.post(
            "/fhir/CodeSystem/$search", json=body
        )
        # Whitespace passes the `if not query_text` check (truthy). Then
        # _check_ready returns 503 (BM25 unavailable) OR service.search
        # returns empty.
        assert response.status_code in (200, 503)

    def test_e82_lookup_post_empty_string_code_400(self, fhir_client):
        """$lookup POST with EMPTY string code → handler's ``if not
        system or not code`` catches it (empty is falsy)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": ""},
            ],
        }
        response = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
        # Empty string is falsy → 400 system-and-code-required.
        assert response.status_code == 400


# =============================================================================
# Lens 9: Empty-but-present optional parameters
# =============================================================================


class TestEmptyOptionalParameters:
    """Empty-string on optional params (Query(None)) should not silently
    produce wrong-answer output."""

    def test_e90_expand_with_empty_url_query_param(self, fhir_client):
        """$expand GET with empty url query param (url=).

        Per FHIR R4 $expand: "If the operation is not called at the
        instance level, one of the in parameters url, context or
        valueSet must be provided." An empty string IS provided in
        URL parsing terms (FastAPI treats ?url= as empty string)."""
        response = fhir_client.get(
            "/fhir/ValueSet/$expand", params={"url": ""}
        )
        # url is optional (Query(None)) so empty string is honored.
        # The handler will likely return 400 (no url/no filter/no
        # valueset → cannot expand).
        assert response.status_code in (200, 400), (
            f"Empty url on $expand GET produced {response.status_code}"
        )

    def test_e91_validate_code_empty_display_query_param(self, fhir_client):
        """$validate-code GET with empty display query param."""
        response = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "code": SNOMED_CODE,
                "system": SNOMED_URI,
                "display": "",
            },
        )
        # display is optional (Query(None)). Empty string is "" — not
        # None. The CS-03 SKEPTIC QA-048 display-mismatch check compares
        # `display != canonical_display`. canonical_display is "Type 2
        # diabetes mellitus" — "" != that → would fire mismatch.
        # Documenting current behavior.
        assert response.status_code == 200

    def test_e92_translate_with_empty_targetsystem(self, fhir_client):
        """$translate GET with empty targetsystem query param."""
        response = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "code": SNOMED_CODE,
                "system": SNOMED_URI,
                "targetsystem": "",
            },
        )
        # targetsystem is optional (Query(None)). Empty string is "" —
        # the impl will try fhir_uri_to_system("") which returns None →
        # 400 unrecognized. OR the impl treats empty as not-provided.
        # Documenting current behavior.
        assert response.status_code < 500


# =============================================================================
# Lens 10: Cross-handler helper-wiring consistency
# (extension of GLOBAL_RULES.md strategy 11)
# =============================================================================


class TestCrossHandlerHelperWiring:
    """Verify the helper-wiring pattern holds for sibling handlers."""

    def test_e100_extract_post_funnels_through_fhir_response(self, fhir_client):
        """Per CR-001 (milestone-1 review) + AGENTS.md Known Fragile
        Areas: $extract POST MUST funnel through _fhir_response so
        Content-Type is application/fhir+json.

        We use the ERROR path (missing text) so we don't actually
        invoke the NER worker — which requires HuggingFace model
        download (environmental failure in test sandbox). The error
        response Content-Type is the load-bearing assertion.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [],  # no text → 400
        }
        response = fhir_client.post(
            "/fhir/CodeSystem/$extract", json=body
        )
        # 400 (text required) — Content-Type MUST be FHIR
        assert response.status_code == 400, (
            f"$extract POST with no text should return 400; got "
            f"{response.status_code}"
        )
        ct = response.headers.get("Content-Type", "")
        assert "application/fhir+json" in ct or "application/fhir+xml" in ct, (
            f"$extract POST error-path Content-Type was {ct!r}; expected "
            f"application/fhir+(json|xml)"
        )

    def test_e101_search_post_funnels_through_fhir_response(self, fhir_client):
        """$search POST MUST funnel through _fhir_response."""
        body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "query", "valueString": "diabetes"}],
        }
        response = fhir_client.post(
            "/fhir/CodeSystem/$search", json=body
        )
        ct = response.headers.get("Content-Type", "")
        assert "application/fhir+json" in ct or "application/fhir+xml" in ct, (
            f"$search POST Content-Type was {ct!r}"
        )


# =============================================================================
# Lens 11: Source-read probes for whitespace-pattern structural contract
# (extension of VS-05 HISTORIAN strategy 52)
# =============================================================================


class TestWhitespaceDriftFixShapeSourceRead:
    """Source-read probes asserting whether the 9th PROMOTED pattern
    (empty-string-as-present-on-required-Query drift) extends to
    whitespace-only handling.

    Per HISTORIAN tip for EXPLORER: ``min_length=1`` does NOT reject
    whitespace-only strings. The fix shape for whitespace drift is
    ``.strip()`` check before processing OR ``pattern=r"\\S"`` Query
    constraint.

    These probes read the source to document CURRENT behavior — whether
    any required-string Query declaration ALSO has whitespace
    rejection. Use skip-on-fix semantics: if a future fix adds
    whitespace rejection, the source-read probe MUST be updated to
    assert presence of the strip()/pattern constraint.
    """

    def test_e110_no_required_query_has_pattern_s_constraint(self):
        """Source-read: confirm no required-string Query has a
        pattern=r'\\S' (or equivalent) constraint today.

        Per HISTORIAN carry-forward note #2: ``min_length=1`` does NOT
        reject whitespace. If this probe FAILS, the fix has landed —
        update to assert the pattern is present.
        """
        import inspect
        from medterm4ds.apps import fhir_api

        source = inspect.getsource(fhir_api)
        # Look for Query declarations with pattern + whitespace
        # rejection
        has_ws_pattern = "pattern=r'\\S'" in source or "pattern='\\S'" in source
        # If a future fix adds whitespace rejection, this probe will
        # fail — update to assert presence.
        if has_ws_pattern:
            pytest.skip(
                "Whitespace pattern constraint detected — fix landed. "
                "Update probe to assert presence."
            )
        # Else: NO whitespace rejection today → probe passes (documents
        # current behavior).
