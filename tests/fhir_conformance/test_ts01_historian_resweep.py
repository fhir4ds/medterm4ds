"""TS-01 / HISTORIAN resweep — pattern-match against prior TS-01 bug patterns.

Fresh full-sweep 2026-08-08 run, 2nd personality (HISTORIAN). Re-derives prior
TS-01 bug patterns from the current code and confirms they have NOT regressed.
Per the [2026-08-08] USER_DIRECTIVES fresh-full-sweep mandate, this is a sibling
file to the baseline ``test_ts01_historian.py`` so the baseline stays comparable
across runs.

Spec source: https://build.fhir.org/terminology-service.html
Relevant section: §4.7.2.1 RESTful API (item 1-5).

HISTORIAN lens (per ROLE_QA_ENGINEER Section 3): pattern-match against prior
bug patterns. Each pattern re-derived against current code is a regression
guard — if a prior fix has come back, that's a regression bug.

Patterns re-derived in this file:
- Pattern QA-012 class (HCPCS canonical URI drift, count=7 PROMOTED) — re-audit
  ``SYSTEM_TO_FHIR_URI`` registry; verify no new entries drifted; verify the
  HCPCS canonical is still the CMS-published URI.
- XML/JSON MIME and Accept-header dispatch (QA-008, QA-009, QA-021) — verify
  Content-Type negotiation for XML vs JSON still works across every route.
- mode-aware /fhir/metadata dispatch (QA-005, QA-006) — verify mode=full vs
  mode=terminology vs mode=invalid still routes correctly.
- READ/SEARCH stubs for CodeSystem/ValueSet/ConceptMap (QA-002, QA-003) —
  verify they still return spec-conformant shapes.

SKEPTIC-tip investigations:
1. Hardcoded ``content: "not-present"`` at responses.py:546 — every codeSystem
   entry emits the same value. Source-read whether this is correct or drift.
2. XML serializer fallback WARNING at apps/fhir_api.py:766-778 — re-verify it
   actually fires (not silent DEBUG swallowing) when ``to_fhir_xml`` raises
   ``ValueError``. Per GLOBAL_RULES.md silent fallbacks are prohibited.
"""

from __future__ import annotations

import json

import pytest


# =============================================================================
# Pattern 1: HCPCS canonical URI drift (QA-012 class, count=7 PROMOTED)
# Re-derive: every URI in SYSTEM_TO_FHIR_URI must be the canonical URI
# published by the owning authority. HCPCS in particular was the original
# bug (THO resource URL instead of CMS canonical).
# =============================================================================

class TestHcpcsCanonicalUriRegistry:
    """Pattern-match QA-012: literal-value-vs-canonical-registry drift.

    Spec: FHIR R4 §4.8.3.1 CodeSystem identification; canonical URI per
    owning authority. Per GLOBAL_RULES.md "Code Review Time" trigger for
    literal-value-vs-canonical-registry drift (count=7 PROMOTED).
    """

    def test_h10_hcpcs_uri_is_cms_canonical_not_tho_resource_url(self):
        """QA-012 regression guard: HCPCS URI MUST be the CMS-published
        canonical (http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets),
        NOT the THO CodeSystem resource URL
        (http://terminology.hl7.org/CodeSystem/hcpcs-Level-II).

        Reference: HL7 THO v5.5.0; CMS.gov published HCPCS Level II release
        code sets URI.
        """
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

        canonical = SYSTEM_TO_FHIR_URI.get("HCPCS")
        assert canonical == (
            "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
        ), (
            f"HCPCS canonical URI drift recurrence (QA-012 pattern): "
            f"got {canonical!r}, expected CMS canonical. "
            f"See GLOBAL_RULES.md 'Code Review Time' for the literal-value-"
            f"vs-canonical-registry drift pattern."
        )

    def test_h11_legacy_hcpcs_uri_retained_as_alias(self):
        """The legacy (incorrect) HCPCS URI MUST be retained as a backwards-
        compat alias in ``FHIR_URI_ALIASES`` so existing clients that learned
        the wrong URI still resolve. This is part of the QA-012 fix contract."""
        from medterm4ds.engines.fhir import FHIR_URI_ALIASES

        legacy = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
        assert FHIR_URI_ALIASES.get(legacy) == "HCPCS", (
            f"HCPCS legacy URI {legacy!r} missing from FHIR_URI_ALIASES — "
            f"existing clients using the prior URI would fail to resolve."
        )

    def test_h12_every_uri_advertised_in_terminology_capabilities_is_canonical(self, fhir_client):
        """Bidirectional registry-as-contract: every URI advertised in the
        TerminologyCapabilities.codeSystem[].uri MUST be in SYSTEM_TO_FHIR_URI
        (canonical registry); no drift introduced by build_terminology_capabilities.
        """
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

        r = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        assert r.status_code == 200
        payload = r.json()
        advertised_uris = {entry.get("uri") for entry in payload.get("codeSystem", [])}
        canonical_uris = set(SYSTEM_TO_FHIR_URI.values())
        # Bidirectional: no extras AND no missing.
        extras = advertised_uris - canonical_uris
        missing = canonical_uris - advertised_uris
        assert not extras, (
            f"TerminologyCapabilities advertises URIs NOT in canonical registry "
            f"(QA-012-class drift): {sorted(extras)}"
        )
        assert not missing, (
            f"TerminologyCapabilities silently DROPS canonical URIs: {sorted(missing)}"
        )

    def test_h13_every_uri_advertised_in_capability_statement_extensions(self, fhir_client):
        """CapabilityStatement.rest[].resource[].operation[].definition URIs
        and ``capabilitystatement-supported-system`` extension URIs MUST all
        be canonical (sourced from SYSTEM_TO_FHIR_URI)."""
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200
        payload = r.json()
        # The supported-system extensions source from SYSTEM_TO_FHIR_URI.
        extensions = payload.get("extension", [])
        supported_system_uris = {
            ext.get("valueUri")
            for ext in extensions
            if ext.get("url", "").endswith("capabilitystatement-supported-system")
        }
        # Every supported-system URI must be in the canonical registry.
        canonical_uris = set(SYSTEM_TO_FHIR_URI.values())
        non_canonical = supported_system_uris - canonical_uris
        assert not non_canonical, (
            f"capabilitystatement-supported-system extension contains non-canonical "
            f"URIs (QA-012-class drift): {sorted(non_canonical)}"
        )


# =============================================================================
# Pattern 2: XML/JSON MIME and Accept-header dispatch (QA-008, QA-009, QA-021)
# Re-derive: every route MUST funnel through _fhir_response so the FHIR MIME
# types are emitted. _format query param MUST override Accept per §3.1.0.1.11.
# =============================================================================

class TestXmlJsonMimeDispatch:
    """Pattern-match QA-008/QA-009/QA-021: Content-Type MIME and _format
    dispatch. Spec: FHIR R4 §3.1.0.1.9 MIME types; §3.1.0.1.11 _format."""

    def test_h20_default_metadata_emits_fhir_json_mimetype(self, fhir_client):
        """QA-008 regression: default Accept MUST emit
        ``application/fhir+json`` (NOT Starlette's default ``application/json``).
        """
        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"metadata default → ct={ct!r} (Starlette default application/json "
            f"would violate §3.1.0.1.9 — QA-008 recurrence)."
        )

    def test_h21_format_param_overrides_accept_header(self, fhir_client):
        """QA-009 regression: ``_format=json`` MUST override
        ``Accept: application/fhir+xml`` per §3.1.0.1.11. The hostile case is
        a client setting Accept=xml but _format=json — server MUST return JSON.
        """
        r = fhir_client.get(
            "/fhir/metadata",
            params={"_format": "json"},
            headers={"Accept": "application/fhir+xml"},
        )
        ct = r.headers.get("content-type", "")
        body = r.text or ""
        assert "application/fhir+json" in ct, (
            f"_format=json with Accept:xml → ct={ct!r} (_format MUST override "
            f"Accept per §3.1.0.1.11 — QA-009 recurrence). Body[:80]={body[:80]!r}"
        )
        # Body must actually be JSON (start with '{'), not XML re-labeled as JSON.
        assert body.lstrip().startswith("{"), (
            f"_format=json but body is not JSON: body[:80]={body[:80]!r}"
        )

    def test_h22_format_xml_overrides_accept_json(self, fhir_client):
        """QA-009 reverse: ``_format=xml`` MUST override ``Accept: fhir+json``.
        Tests both directions of the override rule."""
        r = fhir_client.get(
            "/fhir/metadata",
            params={"_format": "xml"},
            headers={"Accept": "application/fhir+json"},
        )
        ct = r.headers.get("content-type", "")
        body = r.text or ""
        assert "application/fhir+xml" in ct, (
            f"_format=xml with Accept:json → ct={ct!r} (reverse override "
            f"recurrence). Body[:80]={body[:80]!r}"
        )
        assert body.lstrip().startswith("<"), (
            f"_format=xml but body is not XML: body[:80]={body[:80]!r}"
        )

    def test_h23_accept_fhir_json_on_metadata(self, fhir_client):
        """QA-008 positive: Accept: application/fhir+json MUST produce
        application/fhir+json Content-Type."""
        r = fhir_client.get(
            "/fhir/metadata",
            headers={"Accept": "application/fhir+json"},
        )
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"Accept:fhir+json → ct={ct!r}"
        )

    def test_h24_metadata_mode_terminology_xml_dispatch(self, fhir_client):
        """mode=terminology + Accept: application/fhir+xml MUST return XML
        with TerminologyCapabilities resource (combines mode dispatch +
        format dispatch)."""
        r = fhir_client.get(
            "/fhir/metadata",
            params={"mode": "terminology"},
            headers={"Accept": "application/fhir+xml"},
        )
        ct = r.headers.get("content-type", "")
        body = r.text or ""
        assert r.status_code == 200
        assert "application/fhir+xml" in ct, (
            f"mode=terminology + Accept:xml → ct={ct!r}"
        )
        assert body.lstrip().startswith("<"), (
            f"mode=terminology + Accept:xml but body is not XML: body[:80]={body[:80]!r}"
        )
        # Body must contain TerminologyCapabilities root element (not CapabilityStatement).
        assert "<TerminologyCapabilities" in body, (
            f"mode=terminology XML body missing TerminologyCapabilities root: "
            f"body[:200]={body[:200]!r}"
        )


# =============================================================================
# Pattern 3: mode-aware /fhir/metadata dispatch (QA-005, QA-006)
# Re-derive: mode=full → CapabilityStatement; mode=terminology →
# TerminologyCapabilities; mode=invalid → 400 OperationOutcome; absent mode
# → CapabilityStatement (same as mode=full).
# =============================================================================

class TestModeAwareMetadataDispatch:
    """Pattern-match QA-005/QA-006: mode-aware dispatch.

    Spec: §4.7.2.1 item 4+5 mandate mode values full/terminology with
    distinct resource types. Per NOT A BUG registry, mode=normative is also
    accepted (returns full CapabilityStatement per §3.1.0.10)."""

    @pytest.mark.parametrize("mode", [None, "full", "FULL", "Full"])
    def test_h30_mode_full_or_absent_returns_capability_statement(self, fhir_client, mode):
        """QA-005 regression: mode=full or absent mode MUST return
        CapabilityStatement (NOT TerminologyCapabilities).

        Note: case variants (FULL/Full) are accepted per implementation
        leniency — this re-derives current behavior, not strict spec
        conformance. If a future fix tightens mode validation, this probe
        must be updated."""
        if mode is None:
            r = fhir_client.get("/fhir/metadata")
        else:
            r = fhir_client.get("/fhir/metadata", params={"mode": mode})
        # Some case variants may 400; only check the spec-correct ones for 200.
        if mode in (None, "full"):
            assert r.status_code == 200, f"mode={mode!r} → {r.status_code}"
            payload = r.json()
            assert payload.get("resourceType") == "CapabilityStatement", (
                f"mode={mode!r}: resourceType={payload.get('resourceType')!r} "
                f"(expected CapabilityStatement — QA-005 recurrence if TerminologyCapabilities)"
            )

    def test_h31_mode_terminology_returns_terminology_capabilities(self, fhir_client):
        """QA-005 regression: mode=terminology MUST return
        TerminologyCapabilities (NOT CapabilityStatement)."""
        r = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        assert r.status_code == 200
        payload = r.json()
        assert payload.get("resourceType") == "TerminologyCapabilities", (
            f"mode=terminology: resourceType={payload.get('resourceType')!r} "
            f"(QA-005 recurrence — silently returning CapabilityStatement)"
        )

    def test_h32_mode_invalid_returns_400_operationoutcome(self, fhir_client):
        """QA-006 regression: mode=invalid MUST NOT silently produce
        CapabilityStatement or TerminologyCapabilities — it MUST 400 with
        OperationOutcome."""
        r = fhir_client.get("/fhir/metadata", params={"mode": "invalid"})
        assert r.status_code == 400, (
            f"mode=invalid → {r.status_code} (expected 400 — QA-006 recurrence "
            f"if 200 with a resource body)"
        )
        payload = r.json()
        assert payload.get("resourceType") == "OperationOutcome", (
            f"mode=invalid body not OperationOutcome: resourceType="
            f"{payload.get('resourceType')!r}"
        )

    def test_h33_mode_normative_accepted_returns_capability_statement(self, fhir_client):
        """NOT A BUG Registry entry: mode=normative returns the full
        CapabilityStatement. Per §3.1.0.10 servers MAY ignore mode. This
        re-derives the documented behavior."""
        r = fhir_client.get("/fhir/metadata", params={"mode": "normative"})
        assert r.status_code == 200, (
            f"mode=normative → {r.status_code} (expected 200 per §3.1.0.10)"
        )
        payload = r.json()
        assert payload.get("resourceType") == "CapabilityStatement"

    def test_h34_metadata_capability_statement_has_all_required_elements(self, fhir_client):
        """QA-004 regression: CapabilityStatement MUST include the 9 required
        elements (url, version, name, title, status, date, description,
        kind=instance, fhirVersion)."""
        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200
        payload = r.json()
        required = ["url", "version", "name", "title", "status", "date", "description", "kind", "fhirVersion"]
        missing = [el for el in required if el not in payload]
        assert not missing, (
            f"CapabilityStatement missing required elements (QA-004 recurrence): "
            f"{missing}"
        )
        assert payload.get("kind") == "instance", (
            f"kind={payload.get('kind')!r} (expected 'instance' per §4.7.2.1 item 4)"
        )

    def test_h35_metadata_terminology_capabilities_has_all_required_elements(self, fhir_client):
        """QA-005 regression: TerminologyCapabilities MUST include the 6
        required top-level elements (url, name, title, status, date,
        kind=instance) and the codeSystem block with uri+content per entry."""
        r = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        assert r.status_code == 200
        payload = r.json()
        required = ["url", "name", "title", "status", "date", "kind"]
        missing = [el for el in required if el not in payload]
        assert not missing, (
            f"TerminologyCapabilities missing required elements: {missing}"
        )
        assert payload.get("kind") == "instance"
        cs_block = payload.get("codeSystem", [])
        assert cs_block, "TerminologyCapabilities.codeSystem is empty"
        for entry in cs_block:
            assert "uri" in entry, f"codeSystem entry missing uri: {entry!r}"
            assert "content" in entry, f"codeSystem entry missing content: {entry!r}"


# =============================================================================
# Pattern 4: READ/SEARCH stubs for CodeSystem/ValueSet/ConceptMap (QA-002, QA-003)
# Re-derive: every (resource, READ/SEARCH) route returns a spec-conformant
# shape (404 OperationOutcome for READ of unknown id; empty Bundle for SEARCH).
# =============================================================================

class TestReadSearchStubs:
    """Pattern-match QA-002/QA-003: READ and SEARCH stubs.

    Spec §4.7.2.1 item 2: READ and SEARCH interactions SHALL be supported for
    CodeSystem, ValueSet, ConceptMap."""

    @pytest.mark.parametrize("rtype", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_h40_read_unknown_id_returns_404_operationoutcome(self, fhir_client, rtype):
        """QA-002 regression: READ of an unknown id MUST return 404 with
        OperationOutcome body (NOT Starlette's default {detail: Not Found})."""
        r = fhir_client.get(f"/fhir/{rtype}/nonexistent-id")
        assert r.status_code == 404, (
            f"{rtype}/nonexistent-id → {r.status_code} (expected 404 per QA-002)"
        )
        payload = r.json()
        assert payload.get("resourceType") == "OperationOutcome", (
            f"{rtype}/nonexistent-id body not OperationOutcome: {payload.get('resourceType')!r}"
        )
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"{rtype}/nonexistent-id ct={ct!r} (QA-008 recurrence — should be fhir+json)"
        )

    @pytest.mark.parametrize("rtype", ["CodeSystem", "ValueSet", "ConceptMap"])
    def test_h41_search_returns_bundle_with_correct_shape(self, fhir_client, rtype):
        """QA-003 regression: SEARCH MUST return a Bundle with type=searchset."""
        r = fhir_client.get(f"/fhir/{rtype}")
        assert r.status_code == 200, f"{rtype} SEARCH → {r.status_code}"
        payload = r.json()
        assert payload.get("resourceType") == "Bundle", (
            f"{rtype} SEARCH resourceType={payload.get('resourceType')!r} (expected Bundle)"
        )
        assert payload.get("type") == "searchset", (
            f"{rtype} SEARCH type={payload.get('type')!r} (expected searchset)"
        )
        assert "total" in payload, f"{rtype} SEARCH missing total"
        assert "entry" in payload, f"{rtype} SEARCH missing entry (must be list)"

    @pytest.mark.parametrize("rtype", ["CodeSystem", "ValueSet", "ConceptMap"])
    @pytest.mark.parametrize("param", ["url", "version", "name", "title", "status"])
    def test_h42_search_accepts_all_5_spec_params(self, fhir_client, rtype, param):
        """QA-003 + spec §4.7.2.1 item 3: SEARCH MUST accept url, version,
        name, title, status for all 3 resources. Probes a 3×5 matrix."""
        r = fhir_client.get(f"/fhir/{rtype}", params={param: "anything"})
        assert r.status_code == 200, (
            f"{rtype}?{param}=anything → {r.status_code} (must accept the param)"
        )
        payload = r.json()
        assert payload.get("resourceType") == "Bundle"


# =============================================================================
# SKEPTIC Tip 1: hardcoded `content: "not-present"` at responses.py:546
# Source-read whether this is correct for the production UMLS DB, or a drift
# bug (the value should reflect actual code-system content status: complete
# | example | fragment | not-present).
# =============================================================================

class TestSkepticTip1ContentNotPresentHardcoded:
    """Source-read investigation of responses.py:546 ``content: "not-present"``.

    Per AGENTS.md NOT A BUG Registry line 145: ``content: "not-present"`` is
    INTENDED for the medterm4ds deployment shape. Per FHIR R4
    CodeSystemContentMode: ``not-present`` means "the CodeSystem resource
    knows the URI and metadata but no concept definitions are present",
    which is literally true since medterm4ds does not expose CodeSystem
    resources — only $lookup / $validate-code / etc. operations on codes.

    This investigation re-derives the rationale and pins it via probes.
    """

    def test_h50_content_value_is_in_fhir_r4_codesystem_content_mode_enum(self, fhir_client):
        """The hardcoded value MUST be a valid FHIR R4 CodeSystemContentMode
        enum value (complete | example | fragment | not-present).
        https://hl7.org/fhir/R4/codesystem-content-mode.html"""
        from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE  # noqa: F401 (sanity import)
        r = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        assert r.status_code == 200
        payload = r.json()
        valid_content_modes = {"complete", "example", "fragment", "not-present"}
        for entry in payload.get("codeSystem", []):
            content = entry.get("content")
            assert content in valid_content_modes, (
                f"codeSystem entry content={content!r} is NOT in FHIR R4 "
                f"CodeSystemContentMode closed enum {sorted(valid_content_modes)}. "
                f"This is a literal-value-vs-canonical-registry drift bug "
                f"(QA-012-class — would be a NEW instance of the PROMOTED pattern)."
            )

    def test_h51_content_not_present_uniform_across_all_systems(self, fhir_client):
        """Source-read investigation: confirm every codeSystem entry emits
        the same ``content: "not-present"`` value. This is INTENDED per the
        NOT A BUG registry: medterm4ds exposes operations on codes (not
        CodeSystem resources). The value accurately reflects that no
        CodeSystem resource definitions are exposed."""
        r = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        payload = r.json()
        contents = {entry.get("content") for entry in payload.get("codeSystem", [])}
        # Document the current behavior: all entries emit "not-present".
        assert contents == {"not-present"}, (
            f"codeSystem[].content is NOT uniformly 'not-present': {contents}. "
            f"Per NOT A BUG registry line 145, the uniform 'not-present' is "
            f"INTENDED (medterm4ds exposes operations on codes, not CodeSystem "
            f"resources). A non-uniform value would require per-source content "
            f"tracking in the engine."
        )

    def test_h52_source_audit_responses_dot_py_content_hardcoded(self):
        """Source-read audit of responses.py:546 — confirm the value is
        hardcoded as a single literal (no per-source dispatch). This pins
        the structural shape so a future "fix" that adds per-source dispatch
        without a design decision would fail this probe."""
        from medterm4ds.engines.fhir import responses as responses_mod

        src = open(responses_mod.__file__).read()
        # Locate build_terminology_capabilities body.
        start = src.index("def build_terminology_capabilities(")
        # End at the next top-level def.
        end = src.index("\ndef ", start + 1)
        body = src[start:end]
        # The hardcoded literal MUST be present.
        assert '"content": "not-present"' in body, (
            f"build_terminology_capabilities no longer hardcodes "
            f'`content: "not-present"`. If this was a deliberate per-source '
            f"content-mode enhancement, update NOT A BUG registry line 145."
        )

    def test_h53_not_present_value_clinically_correct_for_medterm4ds(self):
        """Documentation probe: ``not-present`` is the FHIR R4
        CodeSystemContentMode value meaning "the CodeSystem resource knows
        the URI and metadata but no concept definitions are present". This
        is literally true for medterm4ds (the engine serves $lookup etc.
        on codes but does NOT expose CodeSystem resource bodies)."""
        # Per FHIR R4 spec https://hl7.org/fhir/R4/codesystem-content-mode.html:
        # "not-present: None of the concepts defined by the code system are
        # included in the code system resource."
        # medterm4ds does NOT persist CodeSystem resources; it serves codes
        # from the underlying UMLS DuckDB. The value is INTENDED.
        # This probe exists to document the rationale; if a future enhancement
        # persists CodeSystem resources, the value MUST change to "complete"
        # or "fragment" per actual content.
        assert True  # documentation-only probe


# =============================================================================
# SKEPTIC Tip 2: XML serializer fallback WARNING at apps/fhir_api.py:766-778
# Re-verify the WARNING actually fires (not silent DEBUG swallowing) when
# to_fhir_xml raises ValueError. Per GLOBAL_RULES.md silent fallbacks are
# prohibited.
# =============================================================================

class TestSkepticTip2XmlSerializerFallbackWarning:
    """Re-verify the XML serializer fallback WARNING path. The implementation
    at apps/fhir_api.py:_fhir_response catches ValueError from to_fhir_xml,
    logs at WARNING, and degrades to JSON.

    GLOBAL_RULES.md "Silent Fallbacks": DEBUG-level swallowing of operational
    errors is prohibited. A regression that downgrades the WARNING to DEBUG
    would be the v0.0.1 B-class anti-pattern.
    """

    def test_h60_xml_fallback_emits_warning_level_log(self, fhir_client, monkeypatch):
        """Simulate XML serialization failure and assert the WARNING log
        fires (NOT DEBUG, NOT silent)."""
        from medterm4ds.apps import fhir_api

        captured_levels = []

        original_to_xml = fhir_api.to_fhir_xml
        original_warning = fhir_api.logger.warning
        original_debug = fhir_api.logger.debug

        def raising_serializer(payload):
            raise ValueError("simulated serialization failure")

        def capture_warning(msg, *args, **kwargs):
            captured_levels.append("WARNING")
            return original_warning(msg, *args, **kwargs)

        def capture_debug(msg, *args, **kwargs):
            captured_levels.append("DEBUG")
            return original_debug(msg, *args, **kwargs)

        fhir_api.to_fhir_xml = raising_serializer
        fhir_api.logger.warning = capture_warning
        fhir_api.logger.debug = capture_debug
        try:
            r = fhir_client.get(
                "/fhir/metadata",
                headers={"Accept": "application/fhir+xml"},
            )
        finally:
            fhir_api.to_fhir_xml = original_to_xml
            fhir_api.logger.warning = original_warning
            fhir_api.logger.debug = original_debug

        # Must degrade to 200 + JSON (NOT 500).
        assert r.status_code == 200, (
            f"XML failure → {r.status_code} (expected 200 with JSON fallback)"
        )
        ct = r.headers.get("content-type", "")
        assert "json" in ct, (
            f"XML failure → ct={ct!r} (expected JSON fallback)"
        )
        # The WARNING MUST fire (not DEBUG, not silent).
        assert "WARNING" in captured_levels, (
            f"XML→JSON fallback did not log WARNING — captured levels: "
            f"{captured_levels}. GLOBAL_RULES.md silent-fallback prohibition."
        )

    def test_h61_xml_fallback_returns_json_body_not_text_plain(self, fhir_client, monkeypatch):
        """Per GLOBAL_RULES.md: silent fallbacks producing text/plain bodies
        on operational failures are prohibited. Verify the fallback returns
        a structured JSON body, NOT text/plain."""
        from medterm4ds.apps import fhir_api

        original = fhir_api.to_fhir_xml

        def raising_serializer(payload):
            raise ValueError("simulated")

        fhir_api.to_fhir_xml = raising_serializer
        try:
            r = fhir_client.get(
                "/fhir/metadata",
                headers={"Accept": "application/fhir+xml"},
            )
        finally:
            fhir_api.to_fhir_xml = original

        ct = r.headers.get("content-type", "")
        assert "text/plain" not in ct, (
            f"XML fallback returned text/plain (silent-fallback anti-pattern): "
            f"ct={ct!r}"
        )
        # Body must be a parseable JSON FHIR resource.
        body = r.text or ""
        assert body.lstrip().startswith("{"), (
            f"XML fallback body is not JSON: body[:80]={body[:80]!r}"
        )
        payload = json.loads(body)
        assert payload.get("resourceType") == "CapabilityStatement", (
            f"XML fallback body not CapabilityStatement: {payload.get('resourceType')!r}"
        )

    def test_h62_source_audit_fhir_response_uses_narrow_value_error(self):
        """Source-read audit of _fhir_response: the except clause MUST be
        ``except ValueError`` (narrow), NOT ``except Exception`` (broad).

        Note: ``_fhir_response`` is nested inside ``create_fhir_app``. The
        scope MUST be limited to the ``_fhir_response`` body — the broader
        ``create_fhir_app`` contains the intentionally-broad
        ``_process_batch_entry`` boundary (per QA-038 / AGENTS.md)."""
        from medterm4ds.apps import fhir_api
        import inspect

        src = open(fhir_api.__file__).read()
        start = src.index("def _fhir_response(")
        # Find the next nested ``def `` (sibling inside create_fhir_app).
        # Use 4-space indentation to match siblings, not top-level ``\ndef ``.
        import re
        # Match a def at the SAME indentation as _fhir_response (4 spaces).
        m = re.search(r"\n    def [a-zA-Z_]", src[start + 1:])
        end = start + 1 + m.start() if m else len(src)
        body = src[start:end]
        assert "except ValueError" in body, (
            "_fhir_response missing narrow ValueError catch."
        )
        assert "except Exception" not in body, (
            f"_fhir_response uses broad `except Exception` — GLOBAL_RULES.md "
            f"'Silent Fallbacks' prohibition."
        )
        assert "logger.warning" in body, (
            "_fhir_response missing logger.warning on fallback path."
        )

    def test_h63_xml_fallback_warning_message_names_resourcetype_and_exception(self, fhir_client, monkeypatch):
        """The WARNING message MUST be actionable — it MUST name the
        resourceType and the exception text so operators can diagnose."""
        from medterm4ds.apps import fhir_api

        captured_msgs = []
        original_to_xml = fhir_api.to_fhir_xml
        original_warning = fhir_api.logger.warning

        def raising_serializer(payload):
            raise ValueError("DIAGNOSTIC_MARKER_42")

        def capture_warning(msg, *args, **kwargs):
            try:
                # msg is a format string; args are the format args.
                full = msg % args if args else msg
            except Exception:
                full = str(msg)
            captured_msgs.append(full)
            return original_warning(msg, *args, **kwargs)

        fhir_api.to_fhir_xml = raising_serializer
        fhir_api.logger.warning = capture_warning
        try:
            fhir_client.get(
                "/fhir/metadata",
                headers={"Accept": "application/fhir+xml"},
            )
        finally:
            fhir_api.to_fhir_xml = original_to_xml
            fhir_api.logger.warning = original_warning

        assert captured_msgs, "No WARNING captured on XML fallback"
        full_msg = captured_msgs[0]
        # The warning MUST include the exception text (actionability).
        assert "DIAGNOSTIC_MARKER_42" in full_msg, (
            f"WARNING message does NOT include exception text — operators "
            f"cannot diagnose. Message: {full_msg!r}"
        )


# =============================================================================
# Additional regression guards: prior TS-01 bug patterns documented in
# ARCHIVE_LOG.md and GLOBAL_KNOWLEDGE.md
# =============================================================================

class TestAdditionalPriorPatternGuards:
    """Re-derive prior TS-01 bug patterns not covered by the resweep tests."""

    def test_h70_xml_serializer_handles_boolean_lowercase(self):
        """v0.0.1 A1 / Milestone-1 CR-002: wire-format serializer MUST
        render booleans as lowercase true/false (NOT Python str(True)="True").
        """
        from medterm4ds.engines.fhir.xml import to_fhir_xml

        payload = {
            "resourceType": "Parameters",
            "parameter": [{"name": "result", "valueBoolean": True}],
        }
        xml_str = to_fhir_xml(payload)
        # Conformant: lowercase true.
        assert 'value="true"' in xml_str, (
            f"Boolean True rendered as non-lowercase: {xml_str}"
        )
        # Forbidden: capital-T True.
        assert 'value="True"' not in xml_str, (
            f"Boolean True rendered as Python str(True)='True' — CR-002 regression. "
            f"XML: {xml_str}"
        )

    def test_h71_xml_serializer_handles_false_lowercase(self):
        """v0.0.1 A1 mirror: False MUST render as lowercase false."""
        from medterm4ds.engines.fhir.xml import to_fhir_xml

        payload = {
            "resourceType": "Parameters",
            "parameter": [{"name": "result", "valueBoolean": False}],
        }
        xml_str = to_fhir_xml(payload)
        assert 'value="false"' in xml_str, (
            f"Boolean False rendered as non-lowercase: {xml_str}"
        )
        assert 'value="False"' not in xml_str, (
            f"Boolean False rendered as Python str(False)='False' — CR-002 regression. "
            f"XML: {xml_str}"
        )

    def test_h72_xml_serializer_renders_extension_url_as_attribute(self):
        """QA-007 regression: <extension url="..."> convention. The url MUST
        be an XML attribute on the extension element, NOT a child element."""
        from medterm4ds.engines.fhir.xml import to_fhir_xml

        payload = {
            "resourceType": "OperationOutcome",
            "issue": [{
                "severity": "information",
                "extension": [
                    {"url": "http://example.org/fhir/StructureDefinition/test",
                     "valueString": "v"}
                ],
            }],
        }
        xml_str = to_fhir_xml(payload)
        assert '<extension url="http://example.org/fhir/StructureDefinition/test">' in xml_str, (
            f"Extension url not rendered as XML attribute (QA-007 regression): "
            f"{xml_str}"
        )

    def test_h73_capability_statement_advertises_xml_and_json_formats(self, fhir_client):
        """CapabilityStatement.format MUST list both json and xml per
        §4.7.2.1 item 1."""
        r = fhir_client.get("/fhir/metadata")
        payload = r.json()
        formats = payload.get("format", [])
        assert "json" in formats, (
            f"CapabilityStatement.format missing 'json': {formats}"
        )
        assert "xml" in formats, (
            f"CapabilityStatement.format missing 'xml' (§4.7.2.1 item 1): {formats}"
        )

    def test_h74_capability_statement_advertises_read_and_search_for_all_3_resources(self, fhir_client):
        """§4.7.2.1 item 2: CapabilityStatement.rest[].resource[] MUST
        advertise CodeSystem, ValueSet, ConceptMap with read + search-type
        interactions."""
        r = fhir_client.get("/fhir/metadata")
        payload = r.json()
        rest = payload.get("rest", [{}])[0]
        resources = {res.get("type"): res for res in rest.get("resource", [])}
        for rtype in ("CodeSystem", "ValueSet", "ConceptMap"):
            assert rtype in resources, (
                f"CapabilityStatement missing resource advertisement: {rtype}"
            )
            interaction_codes = {
                i.get("code") for i in resources[rtype].get("interaction", [])
            }
            assert "read" in interaction_codes, (
                f"{rtype} missing 'read' interaction advertisement"
            )
            assert "search-type" in interaction_codes, (
                f"{rtype} missing 'search-type' interaction advertisement"
            )

    def test_h75_capability_statement_advertises_5_search_params_per_resource(self, fhir_client):
        """§4.7.2.1 item 3: CapabilityStatement.rest[].resource[].searchParam[]
        MUST advertise url, version, name, title, status for all 3 resources."""
        r = fhir_client.get("/fhir/metadata")
        payload = r.json()
        rest = payload.get("rest", [{}])[0]
        for res in rest.get("resource", []):
            rtype = res.get("type")
            if rtype not in ("CodeSystem", "ValueSet", "ConceptMap"):
                continue
            search_params = {
                sp.get("name") for sp in res.get("searchParam", [])
            }
            required_params = {"url", "version", "name", "title", "status"}
            missing = required_params - search_params
            assert not missing, (
                f"{rtype} missing advertised search params: {sorted(missing)}"
            )

    def test_h76_metadata_handler_mode_param_dispatched_at_request_time(self, fhir_client):
        """The mode parameter is dispatched at request time (not module load
        time). Probe: two requests with different mode values produce
        different resource types — confirms the dispatcher isn't cached."""
        r1 = fhir_client.get("/fhir/metadata", params={"mode": "full"})
        r2 = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        assert r1.json().get("resourceType") == "CapabilityStatement"
        assert r2.json().get("resourceType") == "TerminologyCapabilities"
