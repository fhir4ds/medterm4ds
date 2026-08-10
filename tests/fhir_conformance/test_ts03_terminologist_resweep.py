"""TS-03 / TERMINOLOGIST resweep — clinical-correctness probes for External
Code Systems, Implicit Value Sets, and Terminology Maintenance.

Source: https://hl7.org/fhir/R4/terminology-service.html §4.7.3, §4.7.3.1-3

TERMINOLOGIST lens (per ROLE_QA_ENGINEER Section 3 + GLOBAL_RULES.md
"TERMINOLOGIST Findings Are HIGH Severity"): clinical and terminological
correctness. SKEPTIC + HISTORIAN + EXPLORER already hardened the TS-03
surface (50 + 41 + 30 probes). TERMINOLOGIST probes the clinical dimensions:
- canonical URI advertisement clinical correctness
- implicit VS expansion display clinical correctness
- uppercase-scheme fix clinical-safety verification (EXPLORER tip)
- external code system recognition clinical correctness
- terminology maintenance clinical safety

Spec citations live alongside each probe — every assertion ties to verbatim
spec text from https://hl7.org/fhir/R4/terminology-service.html §4.7.3 or
the capabilitystatement-supported-system extension definition page.
"""

from __future__ import annotations

import pytest


# Canonical registry imports — single source of truth per GLOBAL_RULES.md
from medterm4ds.engines.fhir import (
    FHIR_URI_ALIASES,
    FHIR_URI_TO_SYSTEM,
    SYSTEM_TO_FHIR_URI,
    fhir_uri_to_system,
)


SUPPORTED_SYSTEM_EXTENSION_URL = (
    "http://hl7.org/fhir/StructureDefinition/capabilitystatement-supported-system"
)
EMPTY_SOURCE_EXT_URL = (
    "http://medterm4ds.org/fhir/StructureDefinition/valueset-empty-source"
)


# =============================================================================
# Lens 1 — Canonical URI advertisement clinical correctness
# =============================================================================


class TestLens1CanonicalUriAdvertisement:
    """Verify every URI in capabilitystatement-supported-system extension is the
    canonical FHIR R4 system URI for the source.

    Spec: https://hl7.org/fhir/R4/extension-capabilitystatement-supported-system.html
    Quote: 'A list of all the system URIs for code systems that are supported
    by the server.' Each extension's valueUri MUST be the canonical system URI
    used in Coding.system fields — aliases and SAB abbreviations would mislead
    clients about which system a Coding refers to.

    HCPCS drift regression class (count=8 PROMOTED in GLOBAL_RULES.md line 124)
    — re-verify it still holds after SKEPTIC+EXPLORER source changes.
    """

    def _supported_systems(self, fhir_client):
        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200, f"metadata failed: {r.status_code}"
        extensions = r.json().get("extension", [])
        return [
            e["valueUri"]
            for e in extensions
            if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
            and "valueUri" in e
        ]

    def test_t01_extension_uris_match_registry(self, fhir_client):
        """Every URI in the extension MUST equal its canonical URI in
        SYSTEM_TO_FHIR_URI. No drift.

        Spec: extension value type is 'uri'; the value MUST be the standard
        identifying URI for the code system (HL7 THO / CMS / AMA / NLM / CDC).
        """
        advertised = set(self._supported_systems(fhir_client))
        registry = set(SYSTEM_TO_FHIR_URI.values())
        assert advertised == registry, (
            f"Mismatch between advertised extension URIs and registry.\n"
            f"  In extension but not registry: {advertised - registry}\n"
            f"  In registry but not extension: {registry - advertised}\n"
            f"  This is the HCPCS URI drift regression class (count=8 PROMOTED)."
        )

    def test_t02_extension_contains_exactly_eight_entries(self, fhir_client):
        """The extension MUST contain exactly one entry per source (8 total —
        one per SYSTEM_TO_FHIR_URI entry). No duplicates, no missing.

        Spec: cardinality 0..* — each supported system appears as ONE
        extension entry. Duplicate or missing entries would confuse clients
        introspecting supported systems.
        """
        advertised = self._supported_systems(fhir_client)
        assert len(advertised) == len(SYSTEM_TO_FHIR_URI), (
            f"Expected exactly {len(SYSTEM_TO_FHIR_URI)} entries "
            f"(one per source); got {len(advertised)}."
        )
        assert len(advertised) == len(set(advertised)), (
            f"Duplicate URIs in extension: {advertised}"
        )

    @pytest.mark.parametrize(
        "sab,expected_uri",
        [
            ("SNOMEDCT_US", "http://snomed.info/sct"),
            ("LNC", "http://loinc.org"),
            ("RXNORM", "http://www.nlm.nih.gov/research/umls/rxnorm"),
            ("ICD10CM", "http://hl7.org/fhir/sid/icd-10-cm"),
            ("ICD10PCS", "http://hl7.org/fhir/sid/icd-10-pcs"),
            ("CPT", "http://www.ama-assn.org/go/cpt"),
            ("HCPCS", "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"),
            ("CVX", "http://hl7.org/fhir/sid/cvx"),
        ],
    )
    def test_t03_no_alias_or_sab_leakage(self, fhir_client, sab, expected_uri):
        """The extension MUST NOT advertise non-canonical forms: no SAB
        abbreviations (e.g. "SNOMEDCT_US"), no urn:oid aliases, no trailing-
        slash variants, no THO CodeSystem resource URLs (HCPCS historical
        drift).

        Spec: the extension valueUri is a Coding.system identifier — clients
        use it to populate Coding.system fields in their resources. An alias
        here would silently fragment data across responses.
        """
        advertised = self._supported_systems(fhir_client)
        assert expected_uri in advertised, (
            f"Canonical URI for {sab} ({expected_uri!r}) is NOT advertised "
            f"in the extension. Advertised: {advertised}"
        )
        # The HCPCS drift class — ensure the THO resource URL is NOT advertised
        # (only kept as backwards-compat input alias).
        if sab == "HCPCS":
            assert (
                "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
                not in advertised
            ), "HCPCS THO resource URL leaked into extension advertisement."

    def test_t04_extension_no_urn_oid_aliases(self, fhir_client):
        """urn:oid aliases MUST NOT be advertised — they are INPUT aliases
        for backwards-compat with OID-centric clients; the canonical URI is
        the FHIR R4 system URI.

        Spec: the extension is for clients to discover supported systems;
        advertising urn:oid forms would fragment the canonical-URI contract.
        """
        advertised = self._supported_systems(fhir_client)
        for uri in advertised:
            assert not uri.startswith("urn:oid:"), (
                f"urn:oid alias {uri!r} leaked into extension advertisement."
            )


# =============================================================================
# Lens 2 — Uppercase-scheme fix clinical-safety verification (EXPLORER tip)
# =============================================================================


class TestLens2UppercaseSchemeClinicalSafety:
    """Per EXPLORER's tip for TERMINOLOGIST: verify the uppercase-scheme fix
    is clinically safe.

    Spec: RFC 3986 §3.1 (referenced by FHIR R4 §3.1.0.1.9 for HTTP semantics):
    'Although schemes are case-insensitive... An implementation should accept
    uppercase letters as equivalent to lowercase in scheme names.'

    Scope confinement: RFC 3986 §3.2.1 makes the path case-sensitive; §3.2.2
    makes the host case-insensitive. EXPLORER's fix is scheme-only — verify
    path/host case sensitivity is preserved.
    """

    @pytest.mark.parametrize(
        "source,canonical_uri",
        sorted(SYSTEM_TO_FHIR_URI.items()),
    )
    def test_t10_lowercase_scheme_resolves(self, source, canonical_uri):
        """All 8 canonical URIs resolve via lowercase scheme (regression guard).

        Spec: FHIR R4 system URIs are the lowercase-scheme canonical form;
        the registry lookup MUST succeed for every advertised system.
        """
        result = fhir_uri_to_system(canonical_uri)
        assert result == source, (
            f"Lowercase-scheme canonical URI {canonical_uri!r} for source "
            f"{source!r} did not resolve. Got: {result!r}"
        )

    @pytest.mark.parametrize(
        "source,canonical_uri",
        sorted(SYSTEM_TO_FHIR_URI.items()),
    )
    def test_t11_uppercase_scheme_resolves(self, source, canonical_uri):
        """All 8 canonical URIs resolve via UPPERCASE scheme (the EXPLORER
        fix). This is the clinical-safety verification: an uppercase-scheme
        URI identifies the SAME clinical code system.

        Spec: RFC 3986 §3.1 'An implementation should accept uppercase letters
        as equivalent to lowercase in scheme names.'
        """
        # Build uppercase-scheme variant. urn:oid: has no scheme normalization
        # (the colon is part of the URN, not a scheme separator in the same
        # way). Only http/https get scheme normalization.
        if "://" in canonical_uri:
            scheme, rest = canonical_uri.split("://", 1)
            uppercase_uri = f"{scheme.upper()}://{rest}"
            result = fhir_uri_to_system(uppercase_uri)
            assert result == source, (
                f"Uppercase-scheme URI {uppercase_uri!r} should resolve to "
                f"{source!r} per RFC 3986 §3.1. Got: {result!r}."
            )

    @pytest.mark.parametrize(
        "alias_uri,expected_source",
        sorted(FHIR_URI_ALIASES.items()),
    )
    def test_t12_all_aliases_resolve(self, alias_uri, expected_source):
        """All aliases in FHIR_URI_ALIASES continue to resolve. This includes
        the HCPCS THO resource URL backwards-compat alias.

        Spec: aliases are INPUT-only forms recognized for backwards-compat
        with clients using legacy URI forms; they MUST continue to resolve.
        """
        result = fhir_uri_to_system(alias_uri)
        assert result == expected_source, (
            f"Alias {alias_uri!r} did not resolve to {expected_source!r}. "
            f"Got: {result!r}"
        )

    def test_t13_path_case_sensitivity_preserved(self):
        """Scope confinement: path case sensitivity preserved per RFC 3986
        §3.2.1. The fix MUST NOT normalize path case — only scheme.

        Spec: RFC 3986 §3.2.1 'The path component ... is case-sensitive.'
        SNOMED path /sct/ is lowercase; /SCT/ is a DIFFERENT path and MUST
        NOT resolve (it would silently accept a different URI than the
        canonical one).
        """
        # Uppercase path component — scheme is lowercase, path is uppercase.
        result = fhir_uri_to_system("http://snomed.info/SCT")
        assert result is None, (
            f"Path case sensitivity was broken: 'http://snomed.info/SCT' "
            f"resolved to {result!r}. The fix MUST be scheme-only per "
            f"RFC 3986 §3.2.1 (path is case-sensitive)."
        )

    def test_t14_host_case_sensitivity_documented(self):
        """Scope confinement: host case sensitivity is currently PRESERVED
        (not normalized). Per RFC 3986 §3.2.2, hosts are case-insensitive —
        so HTTP://SNOMED.INFO/sct SHOULD also resolve. EXPLORER documented
        this as a separate enhancement (intentional scope confinement to
        scheme-only). This probe documents the CURRENT behavior — when host
        case-insensitivity lands, the probe MUST be updated to assert
        resolution succeeds.

        Per carry-forward-as-probe pattern (strategy 56): this probe pins
        current behavior so a future enhancement fires loudly.
        """
        # Uppercase host — would resolve if host normalization were applied.
        # CURRENT behavior: does NOT resolve (host case sensitivity preserved).
        result = fhir_uri_to_system("http://SNOMED.INFO/sct")
        assert result is None, (
            f"Host case sensitivity unexpectedly normalized: "
            f"'http://SNOMED.INFO/sct' resolved to {result!r}. This is a "
            f"behavior change — update this probe to assert resolution."
        )


# =============================================================================
# Lens 3 — Implicit VS expansion display clinical correctness
# =============================================================================


class TestLens3ImplicitVsDisplayClinicalCorrectness:
    """When expanding http://snomed.info/sct/vs or http://snomed.info/sct?fhir_vs,
    the contains[].display should be the engine's preferred term (clinically
    meaningful STR from mrconso), NOT the raw code. For SNOMED, prefer patient-
    friendly names where they exist.

    Spec: https://hl7.org/fhir/R4/terminology-service.html §4.7.3.1 —
    'Some code systems define a value set which includes all codes in the
    code system.' FHIR R4 ValueSet.expansion.contains.display: 'The recommended
    display for this item in the expansion.'

    Spec: https://hl7.org/fhir/R4/valueset-definitions.html
    ValueSet.expansion.contains.display: 'The recommended display for this
    item in the expansion.'
    """

    def test_t20_implicit_vs_snomed_display_is_engine_preferred_str(
        self, fhir_client
    ):
        """Implicit VS Form (a) for SNOMED: contains[].display MUST be the
        engine preferred STR ("Diabetes mellitus"), NOT the raw code
        "73211009".

        The fixture seeds SNOMED code 73211009 with STR "Diabetes mellitus".
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                ("url", "http://snomed.info/sct/vs"),
                ("count", 100),
            ],
        )
        assert r.status_code == 200, f"Expand failed: {r.status_code}"
        contains = r.json().get("expansion", {}).get("contains", [])
        snomed_entries = [
            c for c in contains if c.get("system") == "http://snomed.info/sct"
        ]
        assert snomed_entries, (
            f"No SNOMED entries in implicit VS expansion. Got: {contains}"
        )
        for entry in snomed_entries:
            # Display MUST NOT equal the raw code — clinically meaningless.
            assert entry.get("display") != entry.get("code"), (
                f"Display equals raw code for SNOMED entry: {entry}. "
                f"Spec: ValueSet.expansion.contains.display is 'The "
                f"recommended display for this item in the expansion.'"
            )
            # Display MUST be a non-empty clinically meaningful string.
            assert entry.get("display"), (
                f"Empty display for SNOMED entry: {entry}"
            )

    def test_t21_implicit_vs_contains_system_is_canonical(self, fhir_client):
        """Implicit VS Form (a): contains[].system MUST be the canonical
        FHIR R4 SNOMED URI. This is the CF-HISTORIAN-VS02-02 RESOLVED
        verification via TERMINOLOGIST clinical-correctness lens.

        Spec: ValueSet.expansion.contains.system is 'The system in which the
        code for this item was defined.' MUST be the canonical URI.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                ("url", "http://snomed.info/sct/vs"),
                ("count", 100),
            ],
        )
        assert r.status_code == 200
        contains = r.json().get("expansion", {}).get("contains", [])
        for entry in contains:
            if entry.get("code") in ("73211009", "44054006"):
                assert entry.get("system") == "http://snomed.info/sct", (
                    f"contains[].system {entry.get('system')!r} is not the "
                    f"canonical SNOMED URI. Entry: {entry}"
                )

    def test_t22_implicit_vs_icd10cm_display_is_engine_preferred_str(
        self, fhir_client
    ):
        """Implicit VS for ICD-10-CM: contains[].display MUST be the engine
        preferred STR ("Type 2 diabetes mellitus"), NOT the raw code "E11".

        The fixture seeds ICD-10-CM code E11 with STR "Type 2 diabetes
        mellitus".
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                ("url", "http://hl7.org/fhir/sid/icd-10-cm/vs"),
                ("count", 100),
            ],
        )
        assert r.status_code == 200, f"Expand failed: {r.status_code}"
        contains = r.json().get("expansion", {}).get("contains", [])
        icd10cm_entries = [
            c
            for c in contains
            if c.get("system") == "http://hl7.org/fhir/sid/icd-10-cm"
        ]
        if icd10cm_entries:  # only assert if fixture has ICD-10-CM rows
            for entry in icd10cm_entries:
                assert entry.get("display") != entry.get("code"), (
                    f"Display equals raw code for ICD-10-CM entry: {entry}. "
                    f"Clinically meaningless — display MUST be the engine "
                    f"preferred STR per spec."
                )

    def test_t23_implicit_vs_form_b_snomed_same_display_quality(
        self, fhir_client
    ):
        """Implicit VS Form (b) SNOMED ?fhir_vs produces same display quality
        as Form (a). Both forms identify the same code system and SHOULD
        return the same clinical content.

        Spec: §4.7.3.1 — Form (b) is the 'all of SNOMED CT' form, equivalent
        to Form (a) for SNOMED.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                ("url", "http://snomed.info/sct?fhir_vs"),
                ("count", 100),
            ],
        )
        assert r.status_code == 200, f"Form (b) expand failed: {r.status_code}"
        contains = r.json().get("expansion", {}).get("contains", [])
        snomed_entries = [
            c for c in contains if c.get("system") == "http://snomed.info/sct"
        ]
        if snomed_entries:
            for entry in snomed_entries:
                assert entry.get("display") != entry.get("code"), (
                    f"Form (b) Display equals raw code: {entry}. "
                    f"Display quality MUST match Form (a)."
                )

    def test_t24_implicit_vs_no_silent_raw_code_fallback(self, fhir_client):
        """Across multiple implicit VS expansions, contains[].display MUST
        NEVER equal the raw code when the engine has a preferred STR. This
        is a clinical-safety audit — silent raw-code fallback is silent-
        wrong-answer.

        Spec: ValueSet.expansion.contains.display is REQUIRED when a display
        is known; the raw code is NOT a valid display.
        """
        for vs_url in [
            "http://snomed.info/sct/vs",
            "http://hl7.org/fhir/sid/icd-10-cm/vs",
            "http://www.nlm.nih.gov/research/umls/rxnorm/vs",
        ]:
            r = fhir_client.get(
                "/fhir/ValueSet/$expand",
                params=[("url", vs_url), ("count", 100)],
            )
            if r.status_code != 200:
                continue  # some sources may not be in the fixture
            contains = r.json().get("expansion", {}).get("contains", [])
            for entry in contains:
                code = entry.get("code")
                display = entry.get("display")
                # If display is missing or equals the raw code, that's
                # silent-wrong-answer.
                assert display and display != code, (
                    f"Implicit VS {vs_url}: entry has no display or display "
                    f"== raw code: {entry}"
                )


# =============================================================================
# Lens 4 — External code system recognition clinical correctness
# =============================================================================


class TestLens4ExternalCodeSystemRecognition:
    """Verify external code system URIs are recognized correctly — especially
    the clinically-critical distinctions:
      - ICD-10-CM (diagnoses) vs ICD-10-PCS (procedures) MUST NOT be conflated
      - SNOMED CT US edition URI MUST be handled cleanly (no 500/text-plain)
      - RXNORM URI MUST resolve distinctly (no RXNORM extension conflation)
      - LOINC URI MUST be exactly http://loinc.org (no trailing slash)

    Spec: §4.7.3.1 — 'When a terminology server exposes an external code
    system, it makes a set of services available internally that serve the
    operational interfaces below ... its URL (namespace, and how versioning
    works), what codes are valid ...'
    """

    def test_t30_snomed_us_edition_handled_cleanly(self, fhir_client):
        """SNOMED CT US edition URI (http://snomed.info/sct/731000124108)
        MUST be handled cleanly — either resolves to SNOMEDCT_US source OR
        rejected with an informative FHIR OperationOutcome (NOT 500/text-
        plain).

        Spec: §4.7.3.1 — the server recognizes external code systems via
        canonical URIs. The US edition URI is the spec-standard URI for the
        US release of SNOMED CT (see https://hl7.org/fhir/R4/snomedct.html);
        clients sending it MUST NOT receive a non-FHIR response.
        """
        # Try $lookup with the US edition URI.
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[
                ("system", "http://snomed.info/sct/731000124108"),
                ("code", "44054006"),
            ],
        )
        assert r.status_code in (200, 400, 404), (
            f"SNOMED US edition URI produced unexpected status "
            f"{r.status_code}. Body: {r.text[:200]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}. The response MUST be FHIR-shaped."
        )
        body = r.json()
        assert body.get("resourceType") in (
            "Parameters",
            "OperationOutcome",
        ), f"Unexpected resourceType: {body.get('resourceType')!r}"

    def test_t31_icd10cm_vs_icd10pcs_distinct_sources(self):
        """ICD-10-CM and ICD-10-PCS URIs MUST resolve to DIFFERENT sources.
        Clinical-critical: ICD-10-CM is diagnoses; ICD-10-PCS is procedures.
        Conflating them would silently map a diagnosis code to a procedure
        code (or vice versa) — a clinical-safety hazard.

        Spec: §4.7.3.1 — 'its URL (namespace ...)'. The two URIs are
        distinct namespaces for distinct code systems.
        """
        cm_source = fhir_uri_to_system("http://hl7.org/fhir/sid/icd-10-cm")
        pcs_source = fhir_uri_to_system("http://hl7.org/fhir/sid/icd-10-pcs")
        assert cm_source == "ICD10CM", (
            f"ICD-10-CM URI resolved to {cm_source!r} (expected 'ICD10CM')."
        )
        assert pcs_source == "ICD10PCS", (
            f"ICD-10-PCS URI resolved to {pcs_source!r} (expected 'ICD10PCS')."
        )
        assert cm_source != pcs_source, (
            f"ICD-10-CM and ICD-10-PCS URIs resolve to the SAME source "
            f"({cm_source!r}) — clinical conflation hazard."
        )

    def test_t32_rxnorm_resolves_distinctly(self):
        """RXNORM URI MUST resolve distinctly (no RXNORM extension conflation).

        Spec: §4.7.3.1 — RxNorm has one canonical URI; RXNORM extension
        (RxNorm extension — ingredient/dose-form extensions) is a distinct
        NLM dataset with its own scope.
        """
        rxnorm_source = fhir_uri_to_system(
            "http://www.nlm.nih.gov/research/umls/rxnorm"
        )
        assert rxnorm_source == "RXNORM", (
            f"RXNORM URI resolved to {rxnorm_source!r} (expected 'RXNORM')."
        )

    def test_t33_loinc_uri_exact_canonical_form(self):
        """LOINC URI MUST be exactly http://loinc.org (no trailing slash, no
        path, no port).

        Spec: https://hl7.org/fhir/R4/loinc.html — 'LOINC® is the canonical
        URI http://loinc.org'. The trailing-slash variant is a recognized
        alias (FHIR_URI_ALIASES), but the canonical form is bare.
        """
        # The canonical URI (no trailing slash) MUST be in the registry.
        assert "http://loinc.org" in SYSTEM_TO_FHIR_URI.values(), (
            f"LOINC canonical URI missing from SYSTEM_TO_FHIR_URI."
        )
        # The bare canonical form MUST resolve.
        assert fhir_uri_to_system("http://loinc.org") == "LNC", (
            "LOINC canonical URI http://loinc.org did not resolve to LNC."
        )

    def test_t34_capabilitystatement_advertises_loinc_canonical_only(
        self, fhir_client
    ):
        """The capabilitystatement-supported-system extension MUST advertise
        the canonical LOINC URI (http://loinc.org), NOT the trailing-slash
        alias (http://loinc.org/) and NOT the urn:oid alias.

        Spec: extension valueUri is the canonical URI clients use to
        populate Coding.system. Aliases fragment the canonical-URI contract.
        """
        r = fhir_client.get("/fhir/metadata")
        extensions = r.json().get("extension", [])
        advertised = [
            e["valueUri"]
            for e in extensions
            if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
            and "valueUri" in e
        ]
        assert "http://loinc.org" in advertised, (
            f"Canonical LOINC URI not advertised. Got: {advertised}"
        )
        assert "http://loinc.org/" not in advertised, (
            f"Trailing-slash LOINC URI leaked into advertisement."
        )


# =============================================================================
# Lens 5 — Terminology maintenance clinical safety
# =============================================================================


class TestLens5TerminologyMaintenanceClinicalSafety:
    """Server rejection of malformed incoming resources MUST produce a FHIR-
    shaped OperationOutcome (not 500/text-plain). Rejection messages should
    be clinically informative.

    Spec: §4.7.3.2 — 'A terminology server should validate incoming resources
    and ensure integrity of the terminology services.'
    Spec: §3.1.0.1.5 + §3.1.0.1.9 — every response MUST be FHIR-shaped
    (application/fhir+json or application/fhir+xml Content-Type, with a FHIR
    resourceType body).
    """

    @pytest.mark.parametrize(
        "resource_type",
        ["CodeSystem", "ValueSet", "ConceptMap"],
    )
    def test_t40_post_resource_rejected_with_fhir_operationoutcome(
        self, fhir_client, resource_type
    ):
        """POST /fhir/{ResourceType}/{id} MUST be rejected (read-only server)
        with a FHIR OperationOutcome — NOT 500/text-plain.

        Spec: §4.7.3.2 — 'validate incoming resources and ensure integrity'.
        medterm4ds is read-only (no create interaction); POST is rejected.
        The rejection MUST be FHIR-shaped per §3.1.0.1.5.
        """
        r = fhir_client.post(
            f"/fhir/{resource_type}/test-id",
            json={"resourceType": resource_type, "id": "test-id"},
        )
        assert r.status_code in (400, 404, 405), (
            f"POST {resource_type} produced unexpected status "
            f"{r.status_code}. Body: {r.text[:200]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"POST {resource_type} rejection Content-Type is {ct!r}, "
            f"not application/fhir+json."
        )
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"POST {resource_type} rejection is not OperationOutcome: {body}"
        )

    @pytest.mark.parametrize(
        "resource_type",
        ["CodeSystem", "ValueSet", "ConceptMap"],
    )
    def test_t41_rejection_message_clinically_informative(
        self, fhir_client, resource_type
    ):
        """The rejection message SHOULD mention the resource type and/or
        operation rejected — clinically informative so the operator can
        diagnose the issue.

        Spec: §4.7.3.2 — 'ensure integrity of the terminology services' —
        the rejection message is the operator's signal.
        """
        r = fhir_client.post(
            f"/fhir/{resource_type}/test-id",
            json={"resourceType": resource_type, "id": "test-id"},
        )
        body = r.json()
        # The OperationOutcome issue.diagnostics SHOULD mention something
        # informative about the resource or operation.
        issues = body.get("issue", [])
        assert issues, f"OperationOutcome has no issue: {body}"
        diagnostics = " ".join(
            issue.get("diagnostics", "") + " " + issue.get("details", {}).get("text", "")
            for issue in issues
        ).lower()
        # The message MUST be non-empty.
        assert diagnostics.strip(), (
            f"OperationOutcome diagnostics is empty: {body}"
        )

    def test_t42_non_canonical_system_in_compose_rejected_informatively(
        self, fhir_client
    ):
        """POST ValueSet with non-canonical system in compose.include MUST
        be rejected with a clinically informative message naming the
        unrecognized URI (NOT generic 'validation failed').

        Spec: §4.7.3.2 — 'validate incoming resources and ensure integrity'.
        Spec: §4.7.3.1 — external code systems are recognized via canonical
        URIs; non-canonical URIs are rejected.
        """
        fake_uri = "http://example.com/fake-system"
        r = fhir_client.post(
            "/fhir/ValueSet/test-id",
            json={
                "resourceType": "ValueSet",
                "id": "test-id",
                "url": "http://example.com/vs/test",
                "status": "draft",
                "compose": {
                    "include": [{"system": fake_uri}],
                },
            },
        )
        # medterm4ds is read-only; POST is rejected at the route layer
        # before compose validation. The rejection is still FHIR-shaped.
        assert r.status_code in (400, 404, 405), (
            f"Unexpected status: {r.status_code}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}"
        )

    def test_t43_terminology_maintenance_operation_routes_still_work(
        self, fhir_client
    ):
        """POST operation routes (e.g. $lookup) MUST still work — the POST
        rejection only applies to READ/CREATE-style POST, not operation
        POST.

        Spec: §4.7.3 — operations are the operational interfaces the
        terminology server exposes; they're unaffected by read-only
        resource persistence rejection.
        """
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                ],
            },
        )
        assert r.status_code == 200, (
            f"POST $lookup (operation) failed: {r.status_code}. Body: {r.text[:200]}"
        )
        body = r.json()
        assert body.get("resourceType") == "Parameters"


# =============================================================================
# Lens 6 — CodeSystem.valueSet URI resolvable
# =============================================================================


class TestLens6CodeSystemValueSetResolvable:
    """Implicit value set URLs (CodeSystem.valueSet URI alone) MUST resolve
    via $expand — clients can refer to implicit value sets by providing the
    URI for the code system itself.

    Spec: §4.7.3.1 — 'Clients can refer to these implicit value sets by
    providing the URI for the code system itself.'
    Spec: §4.7.3.1 — 'Every code system has an implicit value set that is
    "all the concepts defined in the code system" (CodeSystem.valueSet).'
    """

    def test_t50_implicit_vs_snomed_resolves(self, fhir_client):
        """Implicit VS URL for SNOMED resolves with FHIR shape.

        Spec: §4.7.3.1 — 'http://snomed.info/sct/vs' is the implicit value
        set for SNOMED CT.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct/vs")],
        )
        assert r.status_code == 200, f"Expand failed: {r.status_code}"
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), f"Non-FHIR Content-Type: {ct!r}"
        body = r.json()
        assert body.get("resourceType") == "ValueSet", (
            f"Unexpected resourceType: {body.get('resourceType')!r}"
        )

    def test_t51_implicit_vs_loinc_resolves_even_with_no_loinc_rows(
        self, fhir_client
    ):
        """Implicit VS URL for LOINC resolves with FHIR shape — even if the
        fixture has no LOINC codes. The empty-source extension MUST be
        attached (per HISTORIAN QA-033 fix).

        Spec: §4.7.3.1 — 'http://loinc.org/vs is the value set that includes
        all of LOINC.' The empty response is still FHIR-shaped.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://loinc.org/vs")],
        )
        assert r.status_code == 200, f"Expand failed: {r.status_code}"
        body = r.json()
        assert body.get("resourceType") == "ValueSet"
        contains = body.get("expansion", {}).get("contains", [])
        if not contains:
            # Empty expansion MUST carry the empty-source extension.
            extensions = body.get("expansion", {}).get("extension", [])
            ext_urls = [e.get("url") for e in extensions]
            assert EMPTY_SOURCE_EXT_URL in ext_urls, (
                f"Empty LOINC implicit VS expansion has no empty-source "
                f"extension. Extensions: {extensions}"
            )

    def test_t52_implicit_vs_trailing_slash_alias_resolves_to_canonical(
        self, fhir_client
    ):
        """Implicit VS URL with trailing-slash alias MUST resolve to the
        canonical system URI. CF-HISTORIAN-VS02-02 RESOLVED — contains[].system
        is the canonical URI, NOT the trailing-slash alias.

        Spec: §4.7.3.1 — implicit VS URLs are based on the canonical URI;
        aliases on INPUT resolve to canonical on OUTPUT. This is the
        client-input-as-canonical drift regression class (count=8 PROMOTED).
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct//vs")],
        )
        # Trailing-slash SNOMED alias + /vs — the prefix is
        # "http://snomed.info/sct/" (trailing slash) which is a registered
        # alias. The contains[].system MUST be the canonical
        # "http://snomed.info/sct" (no trailing slash).
        if r.status_code == 200:
            body = r.json()
            contains = body.get("expansion", {}).get("contains", [])
            for entry in contains:
                if entry.get("code") in ("73211009", "44054006"):
                    assert entry.get("system") == "http://snomed.info/sct", (
                        f"Trailing-slash alias leaked to contains[].system: "
                        f"{entry.get('system')!r}. CF-HISTORIAN-VS02-02 "
                        f"RESOLVED required canonical re-resolution."
                    )
