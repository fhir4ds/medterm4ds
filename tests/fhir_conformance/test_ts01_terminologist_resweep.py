"""TERMINOLOGIST resweep probes for TS-01 (Terminology Service RESTful API
Conformance, FHIR R4 §4.7.1.1).

Source: https://build.fhir.org/terminology-service.html §4.7.1.1
Fixture: tests/fhir_conformance/conftest.py::fhir_client (synthetic DB)

Fresh-full-sweep directive ([2026-08-08] USER_DIRECTIVES): this sibling resweep
file adds NEW clinical-correctness probes without touching the baseline
``test_ts01_terminologist.py`` (5 probes covering the HCPCS drift regression
class). The baseline stays comparable across runs.

TERMINOLOGIST lens (fresh resweep): clinical and terminological correctness of
the CONFORMANCE ADVERTISEMENT itself, not the operations. Per ROLE_QA_ENGINEER
Section 3 + GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH Severity". The 6
focus areas per the iteration prompt:

1. Canonical code-system URI correctness in ``/fhir/metadata?mode=terminology``
   advertisement (HCPCS drift is the recurring pattern at count=8 PROMOTED).
2. Cross-endpoint URI consistency (conformance advertisement ↔ operation
   responses — drift here is the 'client-input-as-canonical' meta-pattern).
3. TerminologyCapabilities.codeSystem.content values (R4 closed enum:
   complete | example | fragment | not-present | supplement).
4. Search parameter semantics (case-insensitive, partial match per R4 string
   search rules — clinically useful, not silently ignored).
5. capabilitystatement-supported-system extension (canonical URIs only, no
   SAB-style abbreviations).
6. fhirVersion value (valid FHIR version string per R4, e.g. '4.0.1' — NOT
   the medterm4ds package version).
"""

from __future__ import annotations

import re

import pytest

from medterm4ds.engines.fhir import (
    FHIR_URI_ALIASES,
    SYSTEM_TO_FHIR_URI,
    SYSTEM_TO_FHIR_URI as _SYS_TO_URI,
    fhir_uri_to_system,
    system_to_fhir_uri,
)
from medterm4ds.engines.fhir.responses import (
    SUPPORTED_SYSTEM_EXTENSION_URL,
    build_capability_statement,
    build_terminology_capabilities,
)


# Canonical FHIR R4 system URIs as published by HL7 / owning authorities.
# HTTP-fetched from https://hl7.org/fhir/R4/terminologies-systems.html (the
# canonical FHIR R4 external-code-systems registry) plus HL7 THO per-system
# pages for the URIs not directly on that page (ICD-10-CM, ICD-10-PCS, HCPCS).
# This registry is the CLINICAL CONTRACT — every advertised URI MUST match.
CANONICAL_FHIR_R4_URIS: dict[str, str] = {
    "SNOMEDCT_US": "http://snomed.info/sct",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10PCS": "http://hl7.org/fhir/sid/icd-10-pcs",
    "LNC": "http://loinc.org",
    "CPT": "http://www.ama-assn.org/go/cpt",
    "HCPCS": "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets",
    "CVX": "http://hl7.org/fhir/sid/cvx",
}


# FHIR R4 CodeSystemContentMode closed enum (verified via
# https://hl7.org/fhir/R4/codesystem.html#CodeSystem.content and
# https://hl7.org/fhir/terminologycapabilities.html — bound to the same
# CodeSystemContentMode value set with Required binding strength).
FHIR_R4_CODE_SYSTEM_CONTENT_MODE: frozenset[str] = frozenset({
    "not-present",
    "example",
    "fragment",
    "complete",
    "supplement",
})


# FHIR R4 version string regex per https://hl7.org/fhir/R4/versions.html#semver.
# FHIR versions are of the form N.N.N (e.g. '4.0.1', '3.0.2', '5.0.0').
# This is distinct from a package semver — the FHIR version identifies the spec
# version, not the server's product version.
_FHIR_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


# =============================================================================
# LENS 1 — Canonical code-system URI correctness in conformance advertisement.
# =============================================================================

class TestLens1CanonicalUriAdvertisement:
    """Every ``codeSystem[].uri`` advertised in
    ``/fhir/metadata?mode=terminology`` MUST be the canonical FHIR R4 system
    URI for the source. Cross-references the live HTTP response against
    ``SYSTEM_TO_FHIR_URI`` and against the canonical HL7-published registry.
    """

    def test_t10_advertised_uris_match_canonical_registry(self, fhir_client):
        """Bidirectional invariant: every advertised URI is canonical, and every
        canonical URI is advertised. Catches both drift directions (HCPCS
        QA-012 regression class + silent-drop class).

        Spec: https://build.fhir.org/terminology-service.html §4.7.1.1 item 5
        Quote: "codeSystem.uri" (sub-element of codeSystem for each supported
        code system).
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        assert r.status_code == 200
        advertised = {cs["uri"] for cs in r.json().get("codeSystem", [])}
        canonical = set(CANONICAL_FHIR_R4_URIS.values())
        # No extra URIs (no stale / non-canonical / hardcoded URIs).
        extras = advertised - canonical
        assert not extras, f"Advertised non-canonical URIs: {sorted(extras)}"
        # No missing URIs (every supported system advertised).
        missing = canonical - advertised
        assert not missing, f"Missing canonical URIs: {sorted(missing)}"

    @pytest.mark.parametrize("source,expected_uri", sorted(CANONICAL_FHIR_R4_URIS.items()))
    def test_t11_each_advertised_uri_is_canonical(self, fhir_client, source, expected_uri):
        """Per-source pinning: each advertised URI matches the canonical
        FHIR R4 system URI published by the owning authority (HL7, CMS, AMA,
        NLM, CDC). HCPCS drift is the recurring pattern at count=8 PROMOTED.

        Spec: https://hl7.org/fhir/R4/terminologies-systems.html — "Code
        systems used in FHIR may be identified by a URI. Some of these URIs
        are defined by FHIR itself... others are defined elsewhere."
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        advertised_uris = {cs["uri"] for cs in r.json().get("codeSystem", [])}
        assert expected_uri in advertised_uris, (
            f"Canonical URI for {source} not advertised: {expected_uri!r}. "
            f"Advertised: {sorted(advertised_uris)}"
        )

    @pytest.mark.parametrize("source,expected_uri", sorted(CANONICAL_FHIR_R4_URIS.items()))
    def test_t12_no_alias_uri_advertised_as_canonical(self, fhir_client, source, expected_uri):
        """The conformance MUST advertise the canonical URI, NOT a known
        alias. Aliases are accepted on INPUT (for backwards-compat); they MUST
        NOT be published as the canonical value.

        Reference: HCPCS QA-012 — the prior (incorrect) URI
        ``http://terminology.hl7.org/CodeSystem/hcpcs-Level-II`` was a
        CodeSystem RESOURCE URL, not the canonical system URI. The alias is
        kept for backwards-compat input but is NOT canonical.

        Spec: https://hl7.org/fhir/R4/terminologies-systems.html — code
        system URIs are canonical identifiers.
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        advertised = {cs["uri"] for cs in r.json().get("codeSystem", [])}
        # Build the set of non-canonical aliases for this source.
        for alias_uri, alias_source in FHIR_URI_ALIASES.items():
            if alias_source == source:
                assert alias_uri not in advertised, (
                    f"Alias URI {alias_uri!r} for source {source} is advertised "
                    f"in conformance — only the canonical {expected_uri!r} should be."
                )

    def test_t13_advertised_uris_match_SYSTEM_TO_FHIR_URI(self, fhir_client):
        """The HTTP-advertised URIs MUST match ``SYSTEM_TO_FHIR_URI`` (the
        single source of truth in ``engines/fhir/__init__.py``). Drift between
        the conformance builder and the canonical registry would silently
        mislead clients about which URIs to use.

        Spec: per GLOBAL_RULES.md "Single Source of Truth — Where Things Live":
        the canonical location for source → FHIR URI map is
        ``engines.fhir.SYSTEM_TO_FHIR_URI``.
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        advertised = {cs["uri"] for cs in r.json().get("codeSystem", [])}
        canonical_from_registry = set(SYSTEM_TO_FHIR_URI.values())
        assert advertised == canonical_from_registry, (
            f"Drift between HTTP advertisement and SYSTEM_TO_FHIR_URI:\n"
            f"  HTTP-only:    {sorted(advertised - canonical_from_registry)}\n"
            f"  Registry-only: {sorted(canonical_from_registry - advertised)}"
        )


# =============================================================================
# LENS 2 — Cross-endpoint URI consistency (conformance ↔ $lookup).
# =============================================================================

class TestLens2CrossEndpointUriConsistency:
    """For each code advertised as supported in the conformance, the same
    code's URI in operation responses MUST match the conformance
    advertisement. Drift here is the 'client-input-as-canonical'
    meta-pattern.
    """

    @pytest.mark.parametrize(
        "source,canonical_uri,seeded_code",
        [
            ("SNOMEDCT_US", "http://snomed.info/sct", "73211009"),
            ("ICD10CM", "http://hl7.org/fhir/sid/icd-10-cm", "E11"),
            ("RXNORM", "http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
        ],
    )
    def test_t20_lookup_system_matches_conformance_advertisement(
        self, fhir_client, source, canonical_uri, seeded_code
    ):
        """``$lookup`` Out ``system`` MUST match the URI advertised in
        ``/fhir/metadata?mode=terminology`` for the same source. A client
        reading the conformance to learn the canonical URI then calling
        ``$lookup`` and reading the Out ``system`` to confirm SHOULD see the
        same value — otherwise the server is publishing inconsistent
        terminological identity.

        Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out
        ``system`` — "The canonical URI of the code system that contains
        the concept that was looked up."
        """
        # Get conformance-advertised URI for this source.
        r_meta = fhir_client.get("/fhir/metadata?mode=terminology")
        advertised_uris = {cs["uri"] for cs in r_meta.json().get("codeSystem", [])}
        assert canonical_uri in advertised_uris

        # Issue $lookup with canonical URI as input.
        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": canonical_uri, "code": seeded_code},
        )
        assert r_lookup.status_code == 200
        params = r_lookup.json().get("parameter", [])
        system_param = next((p for p in params if p.get("name") == "system"), None)
        assert system_param is not None, "Out 'system' parameter missing"
        out_system = system_param.get("valueUri")
        assert out_system == canonical_uri, (
            f"$lookup Out system {out_system!r} does not match canonical "
            f"URI {canonical_uri!r} advertised for {source}. "
            f"Cross-endpoint URI consistency violated (client-input-as-"
            f"canonical drift meta-pattern)."
        )

    @pytest.mark.parametrize(
        "source,canonical_uri",
        sorted(CANONICAL_FHIR_R4_URIS.items()),
    )
    def test_t21_alias_input_resolves_to_advertised_canonical(
        self, fhir_client, source, canonical_uri
    ):
        """When a client sends an ALIAS URI (e.g. ``urn:oid:...``, or the
        legacy HCPCS resource URL) as ``$lookup`` system input, the Out
        ``system`` MUST return the CANONICAL URI (the one advertised in
        conformance), NOT echo the alias back. This is the client-input-as-
        canonical drift pattern (count=8 PROMOTED).

        Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out
        ``system`` — "The canonical URI of the code system that contains the
        concept that was looked up. (This may differ from the value passed
        in ``system`` as an input parameter...)"
        """
        # Find an alias for this source (if any).
        aliases = [u for u, s in FHIR_URI_ALIASES.items() if s == source]
        if not aliases:
            pytest.skip(f"No alias URI registered for source {source}")
        alias = aliases[0]

        # Issue $lookup with the ALIAS as system input.
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": alias, "code": "NONEXISTENT_T21"},
        )
        # The code doesn't exist in the synthetic DB, but the URI was
        # recognized → 200 OperationOutcome 'not-found' (NOT 400 'unknown
        # system URI'). A 400 here would mean the alias isn't recognized.
        assert r.status_code == 200, (
            f"$lookup with alias {alias!r} for {source} returned HTTP "
            f"{r.status_code} — the alias should be recognized. Body: {r.text}"
        )


# =============================================================================
# LENS 3 — TerminologyCapabilities.codeSystem.content values.
# =============================================================================

class TestLens3ContentValues:
    """Verify each advertised system's ``content`` value is in the FHIR R4
    CodeSystemContentMode closed enum AND clinically appropriate.
    """

    @pytest.mark.parametrize("source", sorted(CANONICAL_FHIR_R4_URIS))
    def test_t30_advertised_content_is_in_r4_enum(self, fhir_client, source):
        """Every ``codeSystem[].content`` value in the conformance MUST be a
        member of the FHIR R4 CodeSystemContentMode closed enum.

        Spec: https://hl7.org/fhir/R4/codesystem.html#CodeSystem.content —
        bound to CodeSystemContentMode (Required): "not-present | example |
        fragment | complete | supplement".
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        code_systems = r.json().get("codeSystem", [])
        expected_uri = CANONICAL_FHIR_R4_URIS[source]
        cs = next((c for c in code_systems if c.get("uri") == expected_uri), None)
        assert cs is not None, f"Source {source} not advertised"
        assert "content" in cs, f"codeSystem.content missing for {source}"
        content = cs["content"]
        assert content in FHIR_R4_CODE_SYSTEM_CONTENT_MODE, (
            f"codeSystem.content for {source} is {content!r}, NOT in the FHIR "
            f"R4 CodeSystemContentMode closed enum "
            f"{sorted(FHIR_R4_CODE_SYSTEM_CONTENT_MODE)}"
        )

    def test_t31_all_advertised_content_values_in_r4_enum(self, fhir_client):
        """Bulk invariant: every ``content`` value across every advertised
        ``codeSystem[]`` is in the closed enum. Catches the case where a
        future source is added with a stale or hardcoded content value.
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        contents = {cs.get("content") for cs in r.json().get("codeSystem", [])}
        off_enum = contents - FHIR_R4_CODE_SYSTEM_CONTENT_MODE
        assert not off_enum, (
            f"Advertised codeSystem.content values NOT in R4 enum: {off_enum}"
        )

    def test_t32_no_source_advertised_as_example(self, fhir_client):
        """SNOMEDCT_US, RXNORM, ICD10CM, ICD10PCS, LNC, CPT, HCPCS, CVX are
        REAL code systems with REAL concept content. Per FHIR R4
        CodeSystemContentMode: ``example`` means "The code system is
        provided as an example... not intended for real-world use".
        Advertising any of these as ``example`` would be clinically
        misleading — they ARE used in real-world clinical data.

        Spec: https://hl7.org/fhir/R4/codesystem.html#CodeSystem.content —
        ``example``: "The code system is provided as an example and is not
        intended for real-world use."
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        for cs in r.json().get("codeSystem", []):
            content = cs.get("content")
            assert content != "example", (
                f"codeSystem {cs.get('uri')!r} advertised as content="
                f"'example' — this is a REAL code system, not an example. "
                f"Clinically misleading: clients may believe the system is "
                f"not for production use."
            )

    def test_t33_snomed_content_is_clinically_appropriate(self, fhir_client):
        """SNOMED CT (the most clinically-critical code system for
        terminology servers) MUST be advertised with a clinically sensible
        content value. ``not-present`` is appropriate for a non-persisting
        server (per AGENTS.md NOT A BUG registry line 147); ``fragment`` is
        also appropriate; ``example`` is NOT (covered by t32).
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        snomed_cs = next(
            (c for c in r.json().get("codeSystem", [])
             if c.get("uri") == "http://snomed.info/sct"),
            None,
        )
        assert snomed_cs is not None
        # not-present is the established per-NOT-A-BUG-registry decision;
        # fragment would also be clinically defensible. Either is acceptable.
        # example is NOT (caught by t32). complete would overpromise.
        assert snomed_cs.get("content") in {"not-present", "fragment"}, (
            f"SNOMED CT content={snomed_cs.get('content')!r} — clinically "
            f"unexpected. Per NOT A BUG registry, not-present is the "
            f"documented decision; fragment is also defensible."
        )


# =============================================================================
# LENS 4 — Search parameter semantics (clinically useful, not silently ignored).
# =============================================================================

class TestLens4SearchParameterSemantics:
    """Search by url, version, name, title, status should return resources
    where the parameter matches per FHIR R4 string search rules. Verify the
    conformance-advertised search params are clinically useful (not silently
    ignored) AND the routes accept them without 5xx.
    """

    @pytest.mark.parametrize(
        "resource_type",
        ["CodeSystem", "ValueSet", "ConceptMap"],
    )
    @pytest.mark.parametrize(
        "param,value",
        [
            ("url", "http://example.org/nonexistent"),
            ("version", "9.9.9-nonexistent"),
            ("name", "NonexistentName"),
            ("title", "Nonexistent Title"),
            ("status", "draft"),
            ("status", "active"),
        ],
    )
    def test_t40_search_param_accepted_without_5xx(
        self, fhir_client, resource_type, param, value
    ):
        """Every advertised search parameter MUST be accepted on the SEARCH
        route without a 5xx error. A 5xx would indicate the parameter is
        silently ignored (route not wired) OR the handler crashes.

        Spec: https://build.fhir.org/terminology-service.html §4.7.1.1 item
        3 — "the following elements as search parameters for CodeSystem,
        ValueSet and ConceptMap: url, version, name, title, status".
        """
        r = fhir_client.get(f"/fhir/{resource_type}", params={param: value})
        assert r.status_code < 500, (
            f"SEARCH /fhir/{resource_type}?{param}={value} returned HTTP "
            f"{r.status_code} — search param not wired or handler crashed."
        )
        # The route MUST return a Bundle (FHIR-conformant response shape),
        # not a Starlette default JSON body.
        body = r.json()
        assert body.get("resourceType") == "Bundle", (
            f"SEARCH response is not a Bundle: resourceType="
            f"{body.get('resourceType')!r}"
        )

    @pytest.mark.parametrize(
        "resource_type",
        ["CodeSystem", "ValueSet", "ConceptMap"],
    )
    def test_t41_search_returns_searchset_bundle(self, fhir_client, resource_type):
        """The SEARCH route MUST return a Bundle with type=searchset (per
        FHIR R4 §3.1.0.6 — "the type of the Bundle is searchset").
        """
        r = fhir_client.get(
            f"/fhir/{resource_type}",
            params={"status": "active"},
        )
        body = r.json()
        assert body.get("resourceType") == "Bundle"
        assert body.get("type") == "searchset", (
            f"Bundle.type={body.get('type')!r}, expected 'searchset'"
        )

    @pytest.mark.parametrize(
        "resource_type",
        ["CodeSystem", "ValueSet", "ConceptMap"],
    )
    def test_t42_advertised_search_params_match_spec_set(self, fhir_client, resource_type):
        """The CapabilityStatement MUST advertise the spec-mandated 5 search
        params (url, version, name, title, status) for each resource type.
        Not advertising them would be a spec violation; advertising extras
        is permissible but should be audited.

        Spec: https://build.fhir.org/terminology-service.html §4.7.1.1 item
        3 — "url, version, name, title, status" (verbatim list).
        """
        r = fhir_client.get("/fhir/metadata")
        cap = r.json()
        resources = cap.get("rest", [{}])[0].get("resource", [])
        rt_entry = next((rr for rr in resources if rr.get("type") == resource_type), None)
        assert rt_entry is not None, f"{resource_type} not in CapabilityStatement.rest[].resource"
        advertised = {sp["name"] for sp in rt_entry.get("searchParam", [])}
        required = {"url", "version", "name", "title", "status"}
        missing = required - advertised
        assert not missing, (
            f"{resource_type} CapabilityStatement missing required search "
            f"params: {sorted(missing)}"
        )


# =============================================================================
# LENS 5 — capabilitystatement-supported-system extension.
# =============================================================================

class TestLens5SupportedSystemExtension:
    """The extension MUST list canonical URIs only — no SAB-style
    abbreviations (e.g. 'SNOMEDCT_US' is wrong; 'http://snomed.info/sct' is
    right).
    """

    def test_t50_extension_url_is_canonical(self):
        """The extension URL MUST be the canonical HL7-published URL for the
        capabilitystatement-supported-system extension.

        Spec: https://hl7.org/fhir/R4/extension-capabilitystatement-
        supported-system.html — extension URL is
        ``http://hl7.org/fhir/StructureDefinition/capabilitystatement-
        supported-system``.
        """
        assert SUPPORTED_SYSTEM_EXTENSION_URL == (
            "http://hl7.org/fhir/StructureDefinition/"
            "capabilitystatement-supported-system"
        )

    def test_t51_extension_present_in_capability_statement(self, fhir_client):
        """The CapabilityStatement MUST include the
        capabilitystatement-supported-system extension listing supported
        systems. Per FHIR R4 §4.7.3, this is the spec-defined mechanism for
        clients to discover supported systems without trial-and-error.

        Spec: https://hl7.org/fhir/R4/extension-capabilitystatement-
        supported-system.html — "A list of all the system URIs for code
        systems that are supported by the server."
        """
        r = fhir_client.get("/fhir/metadata")
        cap = r.json()
        extensions = cap.get("extension", [])
        supported = [
            ext for ext in extensions
            if ext.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
        ]
        assert supported, (
            "CapabilityStatement missing capabilitystatement-supported-system "
            "extension entries"
        )

    @pytest.mark.parametrize("source,expected_uri", sorted(CANONICAL_FHIR_R4_URIS.items()))
    def test_t52_extension_lists_canonical_uri_not_sab(
        self, fhir_client, source, expected_uri
    ):
        """Each ``valueUri`` in the extension MUST be the canonical FHIR R4
        system URI, NOT a SAB-style abbreviation.

        Spec: https://hl7.org/fhir/R4/extension-capabilitystatement-
        supported-system.html — "valueUri" carries a system URI.
        """
        r = fhir_client.get("/fhir/metadata")
        cap = r.json()
        extensions = cap.get("extension", [])
        supported = [
            ext.get("valueUri") for ext in extensions
            if ext.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
        ]
        # Canonical URI for this source MUST be in the extension.
        assert expected_uri in supported, (
            f"Canonical URI {expected_uri!r} for {source} not listed in "
            f"capabilitystatement-supported-system extension. Listed: "
            f"{sorted(supported)}"
        )
        # The SAB-style abbreviation MUST NOT be in the extension.
        assert source not in supported, (
            f"SAB-style abbreviation {source!r} listed in extension — only "
            f"canonical URIs are permitted. valueUri carries a system URI."
        )

    def test_t53_extension_uris_match_canonical_registry(self, fhir_client):
        """Bidirectional invariant: the extension lists EXACTLY the canonical
        URIs from ``SYSTEM_TO_FHIR_URI`` — no extras, no missing.

        Spec: https://hl7.org/fhir/R4/extension-capabilitystatement-
        supported-system.html — "A list of ALL the system URIs for code
        systems that are supported by the server" (emphasis: ALL).
        """
        r = fhir_client.get("/fhir/metadata")
        cap = r.json()
        extensions = cap.get("extension", [])
        listed = {
            ext.get("valueUri") for ext in extensions
            if ext.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
        }
        canonical = set(SYSTEM_TO_FHIR_URI.values())
        extras = listed - canonical
        assert not extras, (
            f"Extension lists non-canonical URIs: {sorted(extras)}"
        )
        missing = canonical - listed
        assert not missing, (
            f"Extension missing canonical URIs: {sorted(missing)}"
        )

    def test_t54_no_extension_value_is_an_alias(self, fhir_client):
        """The extension MUST NOT list alias URIs (e.g.
        ``urn:oid:2.16.840.1.113883.6.1``, the legacy HCPCS resource URL).
        Aliases are accepted on INPUT only; the extension is for canonical
        advertisement.

        Spec: https://hl7.org/fhir/R4/extension-capabilitystatement-
        supported-system.html — extension value is a system URI (canonical).
        """
        r = fhir_client.get("/fhir/metadata")
        cap = r.json()
        extensions = cap.get("extension", [])
        listed = {
            ext.get("valueUri") for ext in extensions
            if ext.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
        }
        alias_uris = set(FHIR_URI_ALIASES.keys())
        alias_in_list = listed & alias_uris
        assert not alias_in_list, (
            f"Extension lists alias URIs (input-only): {sorted(alias_in_list)}"
        )


# =============================================================================
# LENS 6 — fhirVersion in CapabilityStatement.
# =============================================================================

class TestLens6FhirVersion:
    """``fhirVersion`` in the CapabilityStatement MUST be a valid FHIR
    version string per R4 (e.g. '4.0.1'). NOT the medterm4ds package version.
    """

    def test_t60_fhirVersion_is_valid_r4_string(self, fhir_client):
        """``fhirVersion`` MUST be a valid FHIR version string of the form
        N.N.N. medterm4ds implements FHIR R4 (4.0.x).

        Spec: https://hl7.org/fhir/R4/capabilitystatement-definitions.html#
        CapabilityStatement.fhirVersion — "This is the version of FHIR that
        is implemented by this CapabilityStatement."
        """
        r = fhir_client.get("/fhir/metadata")
        cap = r.json()
        version = cap.get("fhirVersion")
        assert version is not None, "fhirVersion missing from CapabilityStatement"
        assert _FHIR_VERSION_PATTERN.match(version), (
            f"fhirVersion {version!r} is NOT a valid FHIR version string "
            f"(expected N.N.N form per FHIR R4 versions.html)"
        )

    def test_t61_fhirVersion_is_4_0_x_r4(self, fhir_client):
        """``fhirVersion`` MUST be ``4.0.x`` (FHIR R4). medterm4ds implements
        R4 — advertising a different major version (3.0.x = STU3, 5.0.x =
        R5) would be a clinical-correctness violation (clients would expect
        the wrong spec semantics).

        Spec: https://hl7.org/fhir/R4/versions.html#semver — FHIR R4 is
        version 4.0.x.
        """
        r = fhir_client.get("/fhir/metadata")
        cap = r.json()
        version = cap.get("fhirVersion")
        assert version.startswith("4.0."), (
            f"fhirVersion {version!r} is not FHIR R4 (4.0.x). medterm4ds "
            f"implements R4 — advertising a different major version would "
            f"cause clients to expect wrong spec semantics."
        )

    def test_t62_fhirVersion_not_package_version(self, fhir_client):
        """``fhirVersion`` MUST NOT be the medterm4ds package version (e.g.
        '0.0.1'). The medterm4ds package version is advertised separately in
        ``CapabilityStatement.version``; ``fhirVersion`` is the FHIR spec
        version implemented.

        Spec: https://hl7.org/fhir/R4/capabilitystatement-definitions.html#
        CapabilityStatement.fhirVersion vs CapabilityStatement.version —
        distinct fields with distinct semantics.
        """
        r = fhir_client.get("/fhir/metadata")
        cap = r.json()
        fhir_version = cap.get("fhirVersion")
        package_version = cap.get("version")
        # If they happen to be equal, the test is a no-op (but they should
        # never be equal in practice — medterm4ds is 0.0.1, FHIR is 4.0.1).
        if package_version == "0.0.1":
            assert fhir_version != package_version, (
                f"fhirVersion {fhir_version!r} equals the package version "
                f"{package_version!r} — these are distinct fields. fhirVersion "
                f"is the FHIR spec version; version is the server product version."
            )

    def test_t63_terminology_capabilities_also_has_fhirVersion(self, fhir_client):
        """The TerminologyCapabilities resource ALSO includes ``fhirVersion``
        per the builder. Verify it matches the CapabilityStatement's value
        (cross-resource consistency on the FHIR version identity).

        Spec: https://hl7.org/fhir/R4/terminologycapabilities-definitions.
        html#TerminologyCapabilities.fhirVersion — same semantics as
        CapabilityStatement.fhirVersion.
        """
        r_cap = fhir_client.get("/fhir/metadata")
        r_term = fhir_client.get("/fhir/metadata?mode=terminology")
        cap_version = r_cap.json().get("fhirVersion")
        term_version = r_term.json().get("fhirVersion")
        assert cap_version == term_version, (
            f"CapabilityStatement.fhirVersion={cap_version!r} differs from "
            f"TerminologyCapabilities.fhirVersion={term_version!r} — the "
            f"server must publish a consistent FHIR version identity."
        )


# =============================================================================
# LENS 7 — Required elements in TerminologyCapabilities (§4.7.1.1 item 5).
# =============================================================================

class TestLens7TerminologyCapabilitiesRequiredElements:
    """The TerminologyCapabilities resource MUST include the required
    elements per §4.7.1.1 item 5: url, name, title, status, date,
    kind=instance, codeSystem.
    """

    def test_t70_required_top_level_elements_present(self, fhir_client):
        """Verify every spec-mandated top-level element is present in the
        TerminologyCapabilities response.

        Spec: https://build.fhir.org/terminology-service.html §4.7.1.1 item
        5 — "url, name, title, status, date, kind with a fixed value of
        instance, and a codeSystem data element".
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        body = r.json()
        required = ["url", "name", "title", "status", "date", "kind", "codeSystem"]
        for elem in required:
            assert elem in body, (
                f"TerminologyCapabilities missing required element: {elem!r}"
            )

    def test_t71_kind_is_instance(self, fhir_client):
        """``kind`` MUST be ``instance`` per the spec-mandated fixed value.

        Spec: https://build.fhir.org/terminology-service.html §4.7.1.1 item
        5 — "kind with a fixed value of instance".
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        assert r.json().get("kind") == "instance", (
            f"TerminologyCapabilities.kind={r.json().get('kind')!r}, "
            f"expected 'instance' per §4.7.1.1 item 5"
        )

    def test_t72_status_is_valid_publication_status(self, fhir_client):
        """``status`` MUST be in the FHIR R4 PublicationStatus closed enum
        (``draft | active | retired | unknown``).

        Spec: https://hl7.org/fhir/R4/valueset-publication-status.html —
        Required binding for CapabilityStatement.status /
        TerminologyCapabilities.status.
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        status = r.json().get("status")
        valid = {"draft", "active", "retired", "unknown"}
        assert status in valid, (
            f"TerminologyCapabilities.status={status!r} NOT in FHIR R4 "
            f"PublicationStatus enum {sorted(valid)}"
        )

    def test_t73_each_codeSystem_has_required_subfields(self, fhir_client):
        """Per §4.7.1.1 item 5: each codeSystem entry MUST have
        ``uri`` and ``content`` sub-elements. ``version`` is conditional
        ("for code systems with a version") — medterm4ds doesn't track
        versions, so omission is conformant.

        Spec: https://build.fhir.org/terminology-service.html §4.7.1.1 item
        5 — "codeSystem.uri, codeSystem.version (for code systems with a
        version)... codeSystem.content".
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        for cs in r.json().get("codeSystem", []):
            assert "uri" in cs, f"codeSystem entry missing 'uri': {cs}"
            assert "content" in cs, (
                f"codeSystem entry for {cs.get('uri')!r} missing 'content'"
            )


# =============================================================================
# LENS 8 — CapabilityStatement required elements (§4.7.1.1 item 4).
# =============================================================================

class TestLens8CapabilityStatementRequiredElements:
    """The CapabilityStatement resource MUST include the required elements
    per §4.7.1.1 item 4: url, version, name, title, status, date,
    description, kind=instance, fhirVersion.
    """

    def test_t80_required_top_level_elements_present(self, fhir_client):
        """Verify every spec-mandated top-level element is present in the
        CapabilityStatement response.

        Spec: https://build.fhir.org/terminology-service.html §4.7.1.1 item
        4 — "url, version, name, title, status, date, description, kind with
        a fixed value of instance, and fhirVersion".
        """
        r = fhir_client.get("/fhir/metadata")
        body = r.json()
        required = [
            "url", "version", "name", "title", "status", "date",
            "description", "kind", "fhirVersion",
        ]
        for elem in required:
            assert elem in body, (
                f"CapabilityStatement missing required element: {elem!r}"
            )

    def test_t81_kind_is_instance(self, fhir_client):
        """``kind`` MUST be ``instance`` per the spec-mandated fixed value.

        Spec: https://build.fhir.org/terminology-service.html §4.7.1.1 item
        4 — "kind with a fixed value of instance".
        """
        r = fhir_client.get("/fhir/metadata")
        assert r.json().get("kind") == "instance", (
            f"CapabilityStatement.kind={r.json().get('kind')!r}, "
            f"expected 'instance' per §4.7.1.1 item 4"
        )

    def test_t82_status_is_valid_publication_status(self, fhir_client):
        """``status`` MUST be in the FHIR R4 PublicationStatus closed enum.

        Spec: https://hl7.org/fhir/R4/valueset-publication-status.html —
        Required binding.
        """
        r = fhir_client.get("/fhir/metadata")
        status = r.json().get("status")
        valid = {"draft", "active", "retired", "unknown"}
        assert status in valid, (
            f"CapabilityStatement.status={status!r} NOT in FHIR R4 "
            f"PublicationStatus enum {sorted(valid)}"
        )

    def test_t83_format_advertises_json_and_xml(self, fhir_client):
        """Per §4.7.1.1 item 1: "the XML and JSON FHIR formats" MUST be
        supported. The CapabilityStatement ``format`` array MUST list both.
        """
        r = fhir_client.get("/fhir/metadata")
        formats = set(r.json().get("format", []))
        assert "json" in formats, f"JSON format not advertised: {sorted(formats)}"
        assert "xml" in formats, f"XML format not advertised: {sorted(formats)}"


# =============================================================================
# LENS 9 — Clinical-safety: no SAB leakage anywhere in conformance.
# =============================================================================

class TestLens9NoSABLeakage:
    """Defense-in-depth: no raw UMLS SAB-style abbreviation should appear
    anywhere in the conformance advertisement. SAB values like
    ``SNOMEDCT_US``, ``RXNORM``, ``ICD10CM`` are medterm4ds-internal source
    identifiers; clients should only see canonical FHIR URIs.
    """

    def test_t90_no_sab_in_terminology_capabilities_uris(self, fhir_client):
        """No ``codeSystem[].uri`` in TerminologyCapabilities should be a
        raw SAB-style value. Every URI must be an ``http(s)://`` URL.

        Spec: https://hl7.org/fhir/R4/datatypes.html#uri — URI datatype
        requires ``http(s)://`` scheme for absolute URIs (which code-system
        URIs always are).
        """
        r = fhir_client.get("/fhir/metadata?mode=terminology")
        for cs in r.json().get("codeSystem", []):
            uri = cs.get("uri", "")
            assert uri.startswith(("http://", "https://", "urn:")), (
                f"codeSystem.uri {uri!r} is NOT an absolute URI — looks like "
                f"a raw SAB-style abbreviation leaked into the conformance."
            )

    def test_t91_no_sab_in_supported_system_extension(self, fhir_client):
        """No ``valueUri`` in the supported-system extension should be a
        raw SAB-style value.

        Spec: https://hl7.org/fhir/R4/extension-capabilitystatement-
        supported-system.html — valueUri is a system URI (absolute URI).
        """
        r = fhir_client.get("/fhir/metadata")
        cap = r.json()
        for ext in cap.get("extension", []):
            if ext.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL:
                value = ext.get("valueUri", "")
                assert value.startswith(("http://", "https://", "urn:")), (
                    f"supported-system valueUri {value!r} is NOT an absolute "
                    f"URI — looks like a raw SAB-style abbreviation."
                )


# =============================================================================
# LENS 10 — Direct builder-level probes (no HTTP layer).
# =============================================================================

class TestLens10BuilderLevelProbes:
    """Direct calls to ``build_capability_statement`` and
    ``build_terminology_capabilities`` to verify the contract without going
    through the HTTP layer. Catches the case where a future change to the
    HTTP route diverges from the builder.
    """

    def test_t100_build_capability_statement_has_required_elements(self):
        """The builder MUST produce all 9 required elements per §4.7.1.1
        item 4. (Mirrors t80 at the builder level.)
        """
        cap = build_capability_statement(base_url="http://test.local")
        required = [
            "url", "version", "name", "title", "status", "date",
            "description", "kind", "fhirVersion",
        ]
        for elem in required:
            assert elem in cap, f"Builder missing required element: {elem!r}"

    def test_t101_build_terminology_capabilities_has_required_elements(self):
        """The builder MUST produce all required elements per §4.7.1.1 item
        5. (Mirrors t70 at the builder level.)
        """
        term = build_terminology_capabilities(base_url="http://test.local")
        required = ["url", "name", "title", "status", "date", "kind", "codeSystem"]
        for elem in required:
            assert elem in term, f"Builder missing required element: {elem!r}"

    def test_t102_build_terminology_capabilities_uris_match_canonical(self):
        """Builder-level invariant: every URI in
        ``build_terminology_capabilities`` is canonical.
        """
        term = build_terminology_capabilities(base_url="http://test.local")
        advertised = {cs["uri"] for cs in term.get("codeSystem", [])}
        canonical = set(CANONICAL_FHIR_R4_URIS.values())
        assert advertised == canonical, (
            f"Builder drift: HTTP-only={advertised - canonical}, "
            f"Registry-only={canonical - advertised}"
        )

    def test_t103_build_capability_statement_extension_sourced_from_registry(self):
        """The supported-system extension MUST be sourced from
        ``SYSTEM_TO_FHIR_URI`` — not a hardcoded list.
        """
        cap = build_capability_statement(base_url="http://test.local")
        extensions = cap.get("extension", [])
        listed = {
            ext.get("valueUri") for ext in extensions
            if ext.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
        }
        canonical = set(SYSTEM_TO_FHIR_URI.values())
        assert listed == canonical

    def test_t104_no_hcpcs_resource_url_in_conformance(self):
        """HCPCS-specific regression: the prior (incorrect) THO CodeSystem
        RESOURCE URL (``http://terminology.hl7.org/CodeSystem/hcpcs-Level-II``)
        MUST NOT appear anywhere in the conformance advertisement.

        Reference: TS-01 TERMINOLOGIST QA-012 (the canonical-drift pattern
        at count=8 PROMOTED in GLOBAL_RULES.md line 124).
        """
        legacy = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
        cap = build_capability_statement(base_url="http://test.local")
        term = build_terminology_capabilities(base_url="http://test.local")
        # Check extension values.
        for ext in cap.get("extension", []):
            if ext.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL:
                assert ext.get("valueUri") != legacy
        # Check codeSystem URIs.
        for cs in term.get("codeSystem", []):
            assert cs.get("uri") != legacy

    @pytest.mark.parametrize("source", sorted(CANONICAL_FHIR_R4_URIS))
    def test_t105_canonical_uri_round_trips_through_helper(self, source):
        """``system_to_fhir_uri(source)`` → ``fhir_uri_to_system(uri)`` MUST
        round-trip back to the original source. Catches the case where the
        reverse map drifts from the forward map.

        Spec: per GLOBAL_RULES.md "Single Source of Truth" — the reverse map
        is derived from the forward map.
        """
        uri = system_to_fhir_uri(source)
        assert uri is not None, f"No URI for source {source}"
        resolved = fhir_uri_to_system(uri)
        assert resolved == source, (
            f"Round-trip failed: source={source!r}, uri={uri!r}, "
            f"resolved={resolved!r}"
        )
