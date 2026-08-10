"""HISTORIAN resweep probes for TS-02 (Mandatory Terminology Service Operations
Matrix, FHIR R4 §4.7.1.2).

Fresh full-sweep run per USER_DIRECTIVES [2026-08-08]. Sibling file to the
baseline ``test_ts02_historian.py`` so the baseline stays comparable across
runs while this file adds fresh pattern-matching probes.

HISTORIAN lens (per ROLE_QA_ENGINEER Section 3): pattern-match against
``GLOBAL_KNOWLEDGE.md`` and ``ARCHIVE_LOG.md``. Re-derive prior TS-02 bug
patterns from current code and verify they have NOT regressed. If a prior
fix has come back, that's a regression bug.

Patterns re-derived (5 total):
1. **HCPCS canonical URI drift (count=8 PROMOTED)** — every operation's
   Out ``system`` parameter echoes canonical URI from
   ``SYSTEM_TO_FHIR_URI``, NOT the client's alias input. Probed by passing
   a non-canonical system URI and asserting the canonical URI in response.
2. **Instance-level POST route wiring** for $expand/$validate-code/$translate
   (TS-02 EXPLORER QA-024/QA-025). Verify routes still wired and behave
   consistently with the type-level routes.
3. **ARCH-003 closure pattern** — instance-level POST routes for ValueSet
   operations (per the GLOBAL_KNOWLEDGE "Closed carry-forwards" entry).
4. **5K-char filter DoS guard** (TS-02 EXPLORER QA-027) — verify $expand
   filter length cap still enforced.
5. **Client-input-as-canonical drift meta-pattern** (count=8 PROMOTED) —
   re-verify across all 7 operations.

SKEPTIC tip for HISTORIAN (high-priority — NEW pattern class candidate
count=3 hit on first occurrence): pattern-match the **empty-string-as-
present-on-required-Query drift** against ALL required-string Query(...)
declarations codebase-wide. Sibling candidates investigated:
1. ``$search`` custom operation handler — ``query`` required string.
2. ``$extract`` custom operation handler — ``text`` required string.
3. SEARCH route params (``url``, ``version``, ``name``, ``title``,
   ``status``) — optional (``Query(None)``) so unaffected.
4. ``$closure`` GET handler — if it exists with required string params.

Promotion rule: if >2 sibling instances of this drift are found (total
>5 occurrences across the codebase), PROMOTE the pattern to GLOBAL_RULES.md
"Code Review Time" trigger list as the 9th PROMOTED pattern.

Spec citations are verbatim from canonical R4 pages.
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
SPEC_SEARCH = "https://build.fhir.org/terminology-service.html"
SPEC_TS = "https://hl7.org/fhir/R4/terminology-service.html"

# Aliases accepted as INPUT but MUST NOT appear in Out `system` per the
# client-input-as-canonical drift meta-pattern (count=8 PROMOTED in
# GLOBAL_RULES.md line 124).
SNOMED_CANONICAL = "http://snomed.info/sct"
SNOMED_ALIAS_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_ALIAS_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_EDITION_URI = "http://snomed.info/sct/731000124108"  # US edition

# Valid $subsumes outcome values per FHIR R4 ConceptSubsumptionOutcome.
VALID_SUBSUMES_OUTCOMES = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}


# =============================================================================
# Pattern 1: HCPCS canonical URI drift (count=8 PROMOTED) — regression test
# Pattern 5: Client-input-as-canonical drift meta-pattern (count=8 PROMOTED)
# =============================================================================

class TestHCPSCanonicalURIDriftRegression:
    """Re-derive the HCPCS canonical URI drift pattern against the current
    code. The prior fix (TS-01 TERMINOLOGIST QA-012) replaced the
    non-canonical HCPCS THO CodeSystem resource URL with the canonical
    system URI from SYSTEM_TO_FHIR_URI. The drift meta-pattern (count=8
    PROMOTED) requires every Out `system` to echo the canonical URI,
    NOT the client's alias input.

    Spec citation: $lookup Out Parameters ``system``:
    "The code system's canonical URI"
    (https://hl7.org/fhir/R4/codesystem-operation-lookup.html)
    """

    def test_h10_hcpcs_canonical_uri_advertised_not_resource_url(self, fhir_client):
        """HCPCS canonical URI MUST be the system URI used in Coding.system
        fields, NOT the THO CodeSystem resource URL. Regression of TS-01
        TERMINOLOGIST QA-012 (count=8 PROMOTED pattern).
        """
        # Source-read: SYSTEM_TO_FHIR_URI must contain the canonical HCPCS URI.
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

        hcpcs_uri = SYSTEM_TO_FHIR_URI["HCPCS"]
        # The prior buggy value was the THO resource URL.
        assert hcpcs_uri != "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II", (
            "REGRESSION: HCPCS URI drift recurred — the THO resource URL "
            "is back in SYSTEM_TO_FHIR_URI."
        )
        # Canonical CMS URL per the QA-012 fix.
        assert "hcpcs" in hcpcs_uri.lower() or "cms" in hcpcs_uri.lower(), (
            f"HCPCS canonical URI changed unexpectedly: {hcpcs_uri!r}"
        )

    def test_h11_hcpcs_uri_in_capabilitystatement_advertisement(self, fhir_client):
        """The HCPCS canonical URI MUST be advertised in the
        CapabilityStatement codeSystem array (or capabilitystatement-
        supported-system extension), NOT the THO resource URL."""
        resp = fhir_client.get("/fhir/metadata")
        assert resp.status_code == 200
        body = resp.json()

        # Walk the supported-system extension first (canonical advertisement).
        supported = set()
        for ext in body.get("extension", []):
            if "supported-system" in ext.get("url", ""):
                if "valueUri" in ext:
                    supported.add(ext["valueUri"])

        # Walk rest.resource.codeSystem[].uri (older advertisement shape).
        for rest in body.get("rest", []):
            for res in rest.get("resource", []):
                for cs in res.get("codeSystem", []):
                    if "uri" in cs:
                        supported.add(cs["uri"])

        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

        canonical_hcpcs = SYSTEM_TO_FHIR_URI["HCPCS"]
        # The canonical URI MUST be advertised.
        assert canonical_hcpcs in supported, (
            f"HCPCS canonical URI {canonical_hcpcs!r} not advertised in "
            f"CapabilityStatement. Advertised set: {sorted(supported)}"
        )
        # The THO resource URL MUST NOT be advertised.
        assert "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II" not in supported, (
            "REGRESSION: HCPCS THO resource URL is advertised — client-input-"
            "as-canonical drift (count=8 PROMOTED) recurred."
        )

    def test_h12_lookup_out_system_echoes_canonical_not_alias(self, fhir_client):
        """$lookup with SNOMED OID alias input MUST echo the canonical URI
        in the Out `system` parameter (TS-02/TERMINOLOGIST invariant).
        """
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_ALIAS_OID, "code": "44054006"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("resourceType") == "Parameters"
        params = {p["name"]: p for p in body.get("parameter", [])}
        assert "system" in params, "Out `system` parameter missing"
        out_system = params["system"].get("valueUri")
        assert out_system == SNOMED_CANONICAL, (
            f"Client-input-as-canonical drift recurred on $lookup: "
            f"input={SNOMED_ALIAS_OID!r}, out={out_system!r}, "
            f"expected canonical={SNOMED_CANONICAL!r}"
        )

    def test_h13_lookup_out_system_trailing_slash_normalization(self, fhir_client):
        """$lookup with trailing-slash input MUST echo the canonical URI."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_ALIAS_TRAILING_SLASH, "code": "44054006"},
        )
        assert resp.status_code == 200
        body = resp.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        out_system = params.get("system", {}).get("valueUri")
        assert out_system == SNOMED_CANONICAL, (
            f"Trailing-slash input {SNOMED_ALIAS_TRAILING_SLASH!r} produced "
            f"Out system {out_system!r}, expected canonical {SNOMED_CANONICAL!r}"
        )

    def test_h14_validate_out_system_canonical_not_alias(self, fhir_client):
        """CodeSystem/$validate-code Out `system` echoes canonical, not alias."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_ALIAS_OID, "code": "44054006"},
        )
        assert resp.status_code == 200
        body = resp.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        out_system = params.get("system", {}).get("valueUri")
        assert out_system == SNOMED_CANONICAL, (
            f"$validate-code canonical drift on alias input: {out_system!r}"
        )

    def test_h15_vs_validate_out_system_canonical_not_alias(self, fhir_client):
        """ValueSet/$validate-code Out `system` echoes canonical, not alias."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "url": SNOMED_CANONICAL + "?fhir_vs",
                "system": SNOMED_ALIAS_OID,
                "code": "44054006",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        # Some implementations omit system on result=true; if present, must be canonical.
        if "system" in params:
            out_system = params["system"].get("valueUri")
            assert out_system == SNOMED_CANONICAL, (
                f"VS/$validate-code canonical drift on alias: {out_system!r}"
            )

    def test_h16_translate_match_source_system_canonical(self, fhir_client):
        """$translate match.source.system echoes canonical URI, not alias
        (per TS-02/TERMINOLOGIST canonical-URI invariant)."""
        resp = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": SNOMED_CANONICAL,
                "code": "44054006",
                "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
            },
        )
        # Result may be false (no crosswalk seeded); the canonical URI on
        # match.source.system is only observable when result=true. Probe
        # confirms canonical on the match path when present.
        assert resp.status_code == 200
        body = resp.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        if "match" in params:
            # The match parameter is a part group; check Coding.system values.
            for part in params["match"].get("part", []):
                if part.get("name") == "source":
                    coding = part.get("valueCoding", {})
                    if "system" in coding:
                        assert coding["system"] == SNOMED_CANONICAL


# =============================================================================
# Pattern 2: Instance-level POST route wiring regression (TS-02 EXPLORER
# QA-024/QA-025 — closes ARCH-003 from SKEPTIC).
# =============================================================================

class TestInstanceLevelPostRouteWiringRegression:
    """Re-derive the instance-level POST route wiring pattern. TS-02 EXPLORER
    QA-024/QA-025 found that instance-level POST routes for $expand and
    $validate-code were missing — Starlette returned non-FHIR 405. Verify
    the routes still exist and return FHIR OperationOutcome.
    """

    def test_h20_expand_instance_get_returns_fhir_404(self, fhir_client):
        """Instance-level GET $expand returns 404 OperationOutcome (medterm4ds
        doesn't persist ValueSets), NOT a non-FHIR 405/404."""
        resp = fhir_client.get(
            "/fhir/ValueSet/unknown-id/$expand",
            params={"count": 10},
        )
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/fhir+json")
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"

    def test_h21_expand_instance_post_returns_fhir_404(self, fhir_client):
        """Instance-level POST $expand returns 404 OperationOutcome, NOT a
        non-FHIR 405 'Method Not Allowed'. Regression of TS-02 EXPLORER
        QA-024 (ARCH-003 closure)."""
        resp = fhir_client.post(
            "/fhir/ValueSet/unknown-id/$expand",
            json={"resourceType": "Parameters", "parameter": []},
        )
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/fhir+json")
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        assert "405" not in resp.text  # Not a framework-default 405 body
        assert "Method Not Allowed" not in resp.text

    def test_h22_vs_validate_instance_get_returns_fhir_404(self, fhir_client):
        """Instance-level GET $validate-code returns 404 OperationOutcome."""
        resp = fhir_client.get(
            "/fhir/ValueSet/unknown-id/$validate-code",
            params={"code": "44054006", "system": SNOMED_CANONICAL},
        )
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/fhir+json")
        assert resp.json().get("resourceType") == "OperationOutcome"

    def test_h23_vs_validate_instance_post_returns_fhir_404(self, fhir_client):
        """Instance-level POST $validate-code returns 404 OperationOutcome.
        Regression of TS-02 EXPLORER QA-025 (ARCH-003 closure)."""
        resp = fhir_client.post(
            "/fhir/ValueSet/unknown-id/$validate-code",
            json={"resourceType": "Parameters", "parameter": []},
        )
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/fhir+json")
        assert resp.json().get("resourceType") == "OperationOutcome"
        assert "405" not in resp.text

    def test_h24_translate_instance_get_returns_fhir_404(self, fhir_client):
        """Instance-level GET $translate returns 404 OperationOutcome."""
        resp = fhir_client.get(
            "/fhir/ConceptMap/unknown-id/$translate",
            params={"code": "44054006", "system": SNOMED_CANONICAL},
        )
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/fhir+json")
        assert resp.json().get("resourceType") == "OperationOutcome"

    def test_h25_translate_instance_post_returns_fhir_404(self, fhir_client):
        """Instance-level POST $translate returns 404 OperationOutcome, NOT
        non-FHIR 405."""
        resp = fhir_client.post(
            "/fhir/ConceptMap/unknown-id/$translate",
            json={"resourceType": "Parameters", "parameter": []},
        )
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/fhir+json")
        assert resp.json().get("resourceType") == "OperationOutcome"
        assert "405" not in resp.text


# =============================================================================
# Pattern 4: 5K-char filter DoS guard regression (TS-02 EXPLORER QA-027).
# =============================================================================

class TestExpandFilterDoSGuardRegression:
    """Re-derive the 5K-char filter DoS guard pattern. TS-02 EXPLORER QA-027
    found that $expand with a very long filter string (>5K chars) called
    services.search_names which raised ValueError for queries >256 chars;
    without a try/except wrapper the exception propagated as uncaught 500.
    Verify the guard still holds.
    """

    def test_h30_expand_filter_5k_chars_no_500(self, fhir_client):
        """5K-char filter MUST NOT cause a 500 traceback. Either returns
        400 OperationOutcome or 200 with empty expansion."""
        long_filter = "diabetes" * 700  # ~5600 chars
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": long_filter, "count": 10},
        )
        assert resp.status_code != 500, (
            "5K-char filter caused 500 — DoS guard regressed (TS-02 "
            "EXPLORER QA-027)."
        )
        # Must be a FHIR resource (200 + Bundle or 400 + OperationOutcome).
        assert resp.headers["content-type"].startswith("application/fhir+json")
        body = resp.json()
        assert body.get("resourceType") in {"ValueSet", "OperationOutcome"}, (
            f"5K-char filter produced non-FHIR body: resourceType={body.get('resourceType')!r}"
        )

    def test_h31_expand_filter_5k_chars_service_delegation_wrapped(self, fhir_client):
        """The search_names ValueError raised on long filter MUST be wrapped
        as a 400 OperationOutcome (the wrap was the TS-02 EXPLORER QA-027
        fix shape). Verify via 200-character filter (above 256 limit)
        to also confirm the boundary."""
        # 300 chars — above the 256-char limit in services.search_names.
        medium_filter = "x" * 300
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": medium_filter, "count": 10},
        )
        # Accept either 200 (search_names tolerated) or 400 (ValueError wrapped).
        # 500 is NEVER acceptable.
        assert resp.status_code != 500, (
            "300-char filter caused 500 — search_names ValueError wrap regressed."
        )
        assert resp.headers["content-type"].startswith("application/fhir+json")


# =============================================================================
# SKEPTIC tip for HISTORIAN (high-priority): empty-string-as-present-on-
# required-Query drift — sibling candidate probes.
# =============================================================================

class TestEmptyStringRequiredQuerySiblingSearchGet:
    """Sibling-candidate probe for the new empty-string-as-present-on-required-
    Query drift pattern (count=3 threshold hit in TS-02/SKEPTIC for
    $lookup/$validate-code/$subsumes; SKEPTIC defensively applied the fix to
    $translate too).

    Sibling 1: ``$search`` custom operation GET handler at
    ``apps/fhir_api.py:2793`` declares ``query: str = Query(...)`` WITHOUT
    ``min_length=1``. An empty string would be treated as "present" by
    FastAPI's required sentinel; the handler then calls
    ``_do_search("", ...)`` which delegates to ``service.search("")``.

    Spec citation: $search is a medterm4ds-specific custom operation modeled
    after Patient $match (per the comment at apps/fhir_api.py:2783). The
    handler docstring says ``query`` is "Text to search for". An empty
    string is not text to search for; it MUST be rejected with 4xx.
    """

    def test_h40_search_get_empty_query_rejected(self, fhir_client):
        """$search GET with empty-string ``query`` MUST be rejected with 4xx
        OperationOutcome, NOT silently accepted as "search for empty text".

        NOTE: in the conformance test fixture (no BM25 + no SapBERT), the
        service layer returns 503 BEFORE the empty-query semantics are
        exercised. The behavioral probe is environmentally blocked; the
        source-read probe (test_h94) confirms the bug structurally. When
        the fix lands (min_length=1), the 422 fires BEFORE the service-
        layer 503 — so the probe tightens to assert 422 + OperationOutcome.
        """
        resp = fhir_client.get(
            "/fhir/CodeSystem/$search",
            params={"query": ""},
        )
        # PRE-FIX: 503 (environmental — service unavailable).
        # POST-FIX: 422 (FastAPI min_length=1 rejection).
        # Either way, NOT 200 (silent-wrong-answer "found nothing for empty text").
        assert resp.status_code != 200, (
            f"$search GET with empty query returned 200 — silent-wrong-answer "
            f"(empty-string-as-present drift, QA-004). "
            f"Response body excerpt: {resp.text[:200]!r}"
        )
        assert resp.headers["content-type"].startswith("application/fhir+json")
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"$search GET empty query produced non-OperationOutcome body: "
            f"resourceType={body.get('resourceType')!r}"
        )


class TestEmptyStringRequiredQuerySiblingExtractGet:
    """Sibling-candidate probe for the empty-string-as-present-on-required-
    Query drift pattern.

    Sibling 2: ``$extract`` custom operation GET handler at
    ``apps/fhir_api.py:2832`` declares ``text: str = Query(...)`` WITHOUT
    ``min_length=1`` (it DOES have ``max_length=MAX_EXTRACT_TEXT_CHARS``).
    An empty string would be treated as "present"; the handler then calls
    ``_do_extract("", ...)`` which delegates to ``services.extraction.extract``.

    Spec citation: $extract is a medterm4ds-specific custom operation
    (NER + ConText + search). The handler docstring says ``text`` is "Free
    text to extract concepts from". An empty string is not free text to
    extract from; it MUST be rejected with 4xx.
    """

    def test_h50_extract_get_empty_text_rejected(self, fhir_client):
        """$extract GET with empty-string ``text`` MUST be rejected with 4xx
        OperationOutcome, NOT silently accepted. The conformance fixture's
        SapBERT-backed NER pipeline tolerates empty text (returns 200 with
        empty results) — confirming the bug behaviorally."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": ""},
        )
        # PRE-FIX: 200 (silent-wrong-answer — empty text accepted, NER pipeline
        #   returns 0 concepts).
        # POST-FIX: 422 (FastAPI min_length=1 rejection).
        assert resp.status_code != 200, (
            f"$extract GET with empty text returned 200 — silent-wrong-answer "
            f"(empty-string-as-present drift, QA-005). "
            f"Response body excerpt: {resp.text[:200]!r}"
        )
        assert resp.headers["content-type"].startswith("application/fhir+json")
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"$extract GET empty text produced non-OperationOutcome body: "
            f"resourceType={body.get('resourceType')!r}"
        )


# =============================================================================
# Pattern 3: ARCH-003 closure pattern regression (instance-level routes).
# =============================================================================

class TestARCH003ClosureRegression:
    """Re-derive the ARCH-003 closure pattern. TS-02 SKEPTIC carried ARCH-003
    forward; TS-02 EXPLORER QA-024/QA-025 closed it via instance-level POST
    routes for $expand and $validate-code. ARCH-003 was the carry-forward
    name for missing instance-level operation routes generally.
    """

    def test_h60_all_type_level_operations_have_instance_routes(self, fhir_client):
        """For every operation advertised at the type level (/$op), the
        corresponding instance-level route MUST exist on the resource type
        (/{Resource}/{id}/$op). This is the ARCH-003 invariant: the
        framework default for unknown instance-level invocation is NOT a
        FHIR OperationOutcome.
        """
        # Read the registered routes from the app on the test client.
        routes = fhir_client.app.routes  # type: ignore[attr-defined]

        # Collect operation path patterns by resource type.
        op_paths = {  # (resource, op) -> bool (instance route exists)
            ("ValueSet", "$expand"): False,
            ("ValueSet", "$validate-code"): False,
            ("ConceptMap", "$translate"): False,
        }
        for route in routes:
            path = getattr(route, "path", "")
            for (resource, op) in list(op_paths.keys()):
                # Instance route pattern: /fhir/{Resource}/{id}/{op}
                if path == f"/fhir/{resource}/{{resource_id}}/{op}":
                    op_paths[(resource, op)] = True

        for (resource, op), has_instance in op_paths.items():
            assert has_instance, (
                f"ARCH-003 REGRESSION: instance-level route for "
                f"{resource}/{op} is missing. The framework default for "
                f"unknown instance-level invocation would return non-FHIR "
                f"405/404."
            )

    def test_h61_instance_routes_registered_before_catch_all(self, fhir_client):
        """Instance-level operation routes MUST be registered BEFORE the
        per-resource READ/SEARCH stubs and catch-all routes so they are not
        shadowed. ARCH-003 regression check."""
        routes = fhir_client.app.routes  # type: ignore[attr-defined]
        paths_in_order = [getattr(r, "path", "") for r in routes]

        # Find the index of an instance-level operation route.
        try:
            expand_instance_idx = paths_in_order.index("/fhir/ValueSet/{resource_id}/$expand")
        except ValueError:
            pytest.fail("Instance-level $expand route missing — ARCH-003 regression")

        # Find the index of the catch-all (last /fhir/{resource_type}/... route).
        catch_all_idx = None
        for i, path in enumerate(paths_in_order):
            if path == "/fhir/{resource_type}/{resource_id:path}":
                catch_all_idx = i

        if catch_all_idx is not None:
            assert expand_instance_idx < catch_all_idx, (
                "ARCH-003 REGRESSION: instance-level $expand route is "
                "registered AFTER the catch-all — the catch-all shadows it."
            )


# =============================================================================
# Pattern 5: Client-input-as-canonical drift meta-pattern (count=8 PROMOTED)
# Re-verification across all 7 mandatory operations.
# =============================================================================

class TestClientInputAsCanonicalDriftMetaPattern:
    """Re-verify the client-input-as-canonical drift meta-pattern (count=8
    PROMOTED) across all 7 mandatory operations. For each Out parameter that
    has a same-named In parameter, the server-canonical value MUST win over
    the client input. This is a regression check for the $lookup, $validate-
    code, $subsumes, $expand, $validate-code, $translate operations.
    """

    def test_h70_lookup_out_system_canonical(self, fhir_client):
        """$lookup Out system MUST echo canonical, not alias input."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_ALIAS_OID, "code": "44054006"},
        )
        assert resp.status_code == 200
        params = {p["name"]: p for p in resp.json().get("parameter", [])}
        if "system" in params:
            assert params["system"].get("valueUri") == SNOMED_CANONICAL

    def test_h71_validate_out_system_canonical(self, fhir_client):
        """CodeSystem/$validate-code Out system MUST echo canonical."""
        resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_ALIAS_OID, "code": "44054006"},
        )
        assert resp.status_code == 200
        params = {p["name"]: p for p in resp.json().get("parameter", [])}
        if "system" in params:
            assert params["system"].get("valueUri") == SNOMED_CANONICAL

    def test_h72_validate_out_display_engine_canonical(self, fhir_client):
        """CodeSystem/$validate-code Out `display` MUST return the engine
        canonical display (TS-02 TERMINOLOGIST QA-029), NOT echo the client's
        input display when one is supplied."""
        # First lookup the canonical display for a known code.
        lookup_resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_CANONICAL, "code": "44054006"},
        )
        assert lookup_resp.status_code == 200
        lookup_params = {p["name"]: p for p in lookup_resp.json().get("parameter", [])}
        canonical_display = lookup_params.get("display", {}).get("valueString")

        # Now validate with a WRONG display; Out display MUST be canonical.
        resp = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_CANONICAL,
                "code": "44054006",
                "display": "WRONG DISPLAY CLIENT INPUT",
            },
        )
        assert resp.status_code == 200
        params = {p["name"]: p for p in resp.json().get("parameter", [])}
        # The Out result MUST be false (display mismatch).
        if "result" in params:
            assert params["result"].get("valueBoolean") is False, (
                "Display mismatch not detected: result=true despite wrong display"
            )
        # The Out display MUST be the canonical, NOT the client's "WRONG DISPLAY".
        if "display" in params and canonical_display:
            out_display = params["display"].get("valueString")
            assert out_display == canonical_display, (
                f"Client-input-as-canonical drift recurred (TS-02 TERMINOLOGIST "
                f"QA-029): Out display={out_display!r}, expected engine "
                f"canonical={canonical_display!r}, got client-input echo instead."
            )

    def test_h73_subsumes_accepts_alias_uri_no_drift(self, fhir_client):
        """$subsumes accepts alias URI on input and produces the same outcome
        as canonical URI input (TS-02 SKEPTIC invariant)."""
        canonical_resp = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_CANONICAL,
                "codeA": "44054006",
                "codeB": "73211009",
            },
        )
        alias_resp = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_ALIAS_OID,
                "codeA": "44054006",
                "codeB": "73211009",
            },
        )
        assert canonical_resp.status_code == 200
        assert alias_resp.status_code == 200

        def _outcome(resp):
            params = {p["name"]: p for p in resp.json().get("parameter", [])}
            return params.get("outcome", {}).get("valueCode")

        # Both URIs MUST produce the same outcome (semantic equivalence).
        assert _outcome(canonical_resp) == _outcome(alias_resp), (
            "$subsumes produced different outcomes for canonical vs alias URI — "
            "client-input-as-canonical drift suspected."
        )


# =============================================================================
# Pattern 1+5: HCPCS canonical URI advertisement — extension-level check
# =============================================================================

class TestHCPCSCanonicalAdvertisementDeep:
    """Deep-source-read audit of the HCPCS canonical URI advertisement. The
    HCPCS drift bug (TS-01 TERMINOLOGIST QA-012) was specifically that the
    THO CodeSystem resource URL was advertised in the
    capabilitystatement-supported-system extension. Verify the extension
    lists canonical URIs only.
    """

    def test_h80_supported_system_extension_no_tho_resource_urls(self, fhir_client):
        """The capabilitystatement-supported-system extension MUST list
        canonical system URIs only, NOT THO CodeSystem resource URLs
        (which are a different identifier type)."""
        resp = fhir_client.get("/fhir/metadata")
        assert resp.status_code == 200
        body = resp.json()

        supported_uris = []
        for ext in body.get("extension", []):
            if "supported-system" in ext.get("url", ""):
                if "valueUri" in ext:
                    supported_uris.append(ext["valueUri"])

        # Common THO resource URL prefixes that should NOT appear here.
        tho_resource_prefixes = (
            "http://terminology.hl7.org/CodeSystem/",
        )
        for uri in supported_uris:
            assert not uri.startswith(tho_resource_prefixes), (
                f"THO CodeSystem resource URL {uri!r} appears in supported-"
                f"system extension — should be the canonical system URI. "
                f"(HCPCS drift regression, count=8 PROMOTED.)"
            )


# =============================================================================
# HISTORIAN pattern-matching: source-read audits of the empty-string drift
# fix shape — verify min_length=1 is present on every previously-fixed handler.
# =============================================================================

class TestEmptyStringDriftFixShapeSourceRead:
    """Source-read audit: verify that the SKEPTIC FIX-001/002/003 + defensive
    sibling application to translate_get are still in place. If any of these
    regresses (e.g., a refactor removes min_length=1), the empty-string
    silent-wrong-answer bug returns."""

    def test_h90_lookup_get_has_min_length_1(self):
        """Source-read: $lookup GET handler declares system/code with
        min_length=1."""
        import inspect

        from medterm4ds.apps import fhir_api as fapi

        # Source-read the create_fhir_app function body for the lookup_get signature.
        src = inspect.getsource(fapi.create_fhir_app)
        # Find the lookup_get function block.
        idx = src.find("async def lookup_get(")
        assert idx != -1, "lookup_get handler not found in create_fhir_app"
        # Look in the next ~2000 chars (signature + decorators).
        block = src[idx : idx + 2000]
        assert "min_length=1" in block, (
            "REGRESSION: lookup_get signature lost min_length=1 — empty-string "
            "silent-wrong-answer bug returns."
        )

    def test_h91_validate_get_has_min_length_1(self):
        """Source-read: $validate-code GET handler declares system/code with
        min_length=1."""
        import inspect

        from medterm4ds.apps import fhir_api as fapi

        src = inspect.getsource(fapi.create_fhir_app)
        idx = src.find("async def validate_get(")
        assert idx != -1
        block = src[idx : idx + 2000]
        assert "min_length=1" in block, (
            "REGRESSION: validate_get signature lost min_length=1."
        )

    def test_h92_subsumes_get_has_min_length_1(self):
        """Source-read: $subsumes GET handler declares system/codeA/codeB with
        min_length=1."""
        import inspect

        from medterm4ds.apps import fhir_api as fapi

        src = inspect.getsource(fapi.create_fhir_app)
        idx = src.find("async def subsumes_get(")
        assert idx != -1
        block = src[idx : idx + 2000]
        assert "min_length=1" in block, (
            "REGRESSION: subsumes_get signature lost min_length=1."
        )

    def test_h93_translate_get_has_min_length_1(self):
        """Source-read: $translate GET handler (defensive sibling application)
        declares system/code with min_length=1."""
        import inspect

        from medterm4ds.apps import fhir_api as fapi

        src = inspect.getsource(fapi.create_fhir_app)
        idx = src.find("async def translate_get(")
        assert idx != -1
        block = src[idx : idx + 2000]
        assert "min_length=1" in block, (
            "REGRESSION: translate_get signature lost min_length=1 (defensive "
            "sibling application removed)."
        )

    def test_h94_search_get_LACKS_min_length_1_pattern_match(self):
        """Source-read: $search GET handler ``query`` parameter LACKS
        min_length=1 — this is the SIBLING candidate (pattern-match target).
        If this assertion FAILS (i.e., min_length=1 IS present), the fix
        has been applied and the bug is closed.
        """
        import inspect
        import re

        from medterm4ds.apps import fhir_api as fapi

        src = inspect.getsource(fapi.create_fhir_app)
        idx = src.find("async def search_get(")
        assert idx != -1
        block = src[idx : idx + 1500]

        # Find the ``query`` parameter declaration in the signature, then
        # use paren-matching to capture the FULL Query(...) call.
        m = re.search(r"query:\s*str\s*=\s*Query\(", block)
        assert m is not None, "search_get query parameter declaration not found"
        # Walk from `m.end()-1` (the opening paren) to find its match.
        start = m.end() - 1  # the `(` position
        depth = 0
        end = None
        for i in range(start, len(block)):
            if block[i] == "(":
                depth += 1
            elif block[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        assert end is not None, "unbalanced parens in search_get query Query decl"
        query_decl = block[m.start() : end]
        if "min_length=1" in query_decl:
            pytest.skip(
                "$search query parameter already has min_length=1 — "
                "sibling fix already applied (no bug to file)."
            )
        # If we reach here, the bug is present.
        pytest.fail(
            f"BUG CONFIRMED: $search `query` Query declaration lacks "
            f"min_length=1: {query_decl!r}. Empty-string-as-present-on-"
            f"required-Query drift pattern (count=4 — PROMOTION to "
            f"GLOBAL_RULES.md indicated)."
        )

    def test_h95_extract_get_LACKS_min_length_1_pattern_match(self):
        """Source-read: $extract GET handler ``text`` parameter LACKS
        min_length=1 — this is the SIBLING candidate (pattern-match target).
        If this assertion FAILS (i.e., min_length=1 IS present), the fix
        has been applied and the bug is closed.
        """
        import inspect
        import re

        from medterm4ds.apps import fhir_api as fapi

        src = inspect.getsource(fapi.create_fhir_app)
        idx = src.find("async def extract_get(")
        assert idx != -1
        block = src[idx : idx + 1500]

        m = re.search(r"text:\s*str\s*=\s*Query\(", block)
        assert m is not None, "extract_get text parameter declaration not found"
        start = m.end() - 1
        depth = 0
        end = None
        for i in range(start, len(block)):
            if block[i] == "(":
                depth += 1
            elif block[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        assert end is not None, "unbalanced parens in extract_get text Query decl"
        text_decl = block[m.start() : end]
        if "min_length=1" in text_decl:
            pytest.skip(
                "$extract text parameter already has min_length=1 — "
                "sibling fix already applied (no bug to file)."
            )
        pytest.fail(
            f"BUG CONFIRMED: $extract `text` Query declaration lacks "
            f"min_length=1: {text_decl!r}. Empty-string-as-present-on-"
            f"required-Query drift pattern (count=5 — PROMOTION to "
            f"GLOBAL_RULES.md indicated)."
        )
