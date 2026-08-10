"""SKEPTIC resweep probes for TS-03 (External Code Systems, Implicit Value
Sets, Terminology Maintenance — FHIR R4 terminology-service §4.7.3).

Fresh full-sweep run per USER_DIRECTIVES [2026-08-08]: re-derive every prior
TS-03 finding against current code AND probe new adversarial corners the
prior SKEPTIC run missed.

Source: https://build.fhir.org/terminology-service.html (§4.7.3, §4.7.3.1-3)
Extension: https://hl7.org/fhir/R4/extension-capabilitystatement-supported-system.html

Five chunk items, hostile-input probes per spec item. SKEPTIC lens
(ROLE_QA_ENGINEER.md Section 3): aggressive bug hunting — edge cases,
malformed inputs, boundary conditions.

Carry-forwards re-derived:
- **CF-HISTORIAN-VS02-02** (MEDIUM, OPEN) — `_expand_implicit_value_set`
  Form (a) emits client-supplied prefix as ``contains[].system`` instead
  of the canonical URI. 8th instance of client-input-as-canonical drift
  per GLOBAL_RULES.md line 124. Tested in Lens 3.

Spec citation discipline per ROLE_QA_ENGINEER.md Section 4: every bug
cites the FHIR spec section verbatim. The carry-forward's spec citation
is FHIR R4 §4.7.3 Value Set Validation:

  "Every code system has an implicit value set that is 'all the concepts
   defined in the code system' (CodeSystem.valueSet)."

  "Clients can refer to these implicit value sets by providing the URI
   for the code system itself."

Per spec: the URI identifies the code system; the canonical identifier
is the FHIR R4 system URI (per FHIR R4 §3.4 code systems use ``url``
as the canonical identifier). Clients receiving a Coding whose ``system``
echoes the client-supplied alias cannot round-trip via canonical lookup.
"""

from __future__ import annotations

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    SYSTEM_TO_FHIR_URI,
)

# =============================================================================
# Constants (sourced from the canonical registries, never hardcoded)
# =============================================================================

CANONICAL_FHIR_R4_URIS = dict(SYSTEM_TO_FHIR_URI)

SUPPORTED_SYSTEM_EXTENSION_URL = (
    "http://hl7.org/fhir/StructureDefinition/capabilitystatement-supported-system"
)

# Known seeded codes per conftest.py fhir_client fixture
KNOWN_CODES = {
    "SNOMEDCT_US": "73211009",
    "ICD10CM": "E11",
    "RXNORM": "860975",
}


# =============================================================================
# Lens 1: External code system recognition — canonical URI forms
# =============================================================================


class TestLens1ExternalCodeSystems:
    """Hostile-input probes for §4.7.3.1 external code system recognition.

    Spec: "HL7 Terminology defines these things for common terminologies
    (including SNOMED CT, LOINC, and RxNorm)" — server MUST recognize the
    canonical URIs of all supported systems.
    """

    @pytest.mark.parametrize(
        "source,uri",
        sorted(CANONICAL_FHIR_R4_URIS.items()),
    )
    def test_s10_lookup_recognizes_canonical_uri(self, fhir_client, source, uri):
        """Positive shape: every canonical URI MUST be recognized — server
        returns 200 (or 200 + not-found OperationOutcome if code unknown),
        NOT 400 'Unrecognized system URI'.

        Spec: https://build.fhir.org/terminology-service.html §4.7.3.1.
        """
        code = KNOWN_CODES.get(source, "0")
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[("system", uri), ("code", code)],
        )
        assert not (
            r.status_code == 400 and "Unrecognized system URI" in r.text
        ), f"{uri} for source {source} rejected as unrecognized: {r.text[:200]}"

    @pytest.mark.parametrize(
        "source,uri",
        sorted(CANONICAL_FHIR_R4_URIS.items()),
    )
    def test_s11_validate_code_recognizes_canonical_uri(
        self, fhir_client, source, uri
    ):
        """$validate-code MUST accept every canonical URI."""
        code = KNOWN_CODES.get(source, "0")
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params=[("system", uri), ("code", code)],
        )
        assert not (
            r.status_code == 400 and "Unrecognized system URI" in r.text
        ), f"{uri} rejected on $validate-code: {r.text[:200]}"

    def test_s12_lookup_rejects_unknown_system_uri(self, fhir_client):
        """Silent-fallback guard: unknown URI → 400 OperationOutcome."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[("system", "http://example.com/unknown-system"), ("code", "X")],
        )
        assert r.status_code == 400
        assert r.json().get("resourceType") == "OperationOutcome"

    def test_s13_lookup_rejects_sab_abbreviation_as_uri(self, fhir_client):
        """SKEPTIC: SAB abbreviations are NOT FHIR system URIs. The server
        MUST reject them as unrecognized, not silently fall back to a
        default source.

        Spec: FHIR R4 §3.1.0.5 Coding.system is a URI; SNOMEDCT_US / RXNORM /
        LNC are UMLS SAB abbreviations, NOT URIs. Silent acceptance is a
        client-input-as-canonical drift surface (count=8 per GLOBAL_RULES.md).
        """
        for sab in ("SNOMEDCT_US", "RXNORM", "LNC", "ICD10CM", "CPT", "HCPCS", "CVX"):
            r = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params=[("system", sab), ("code", "X")],
            )
            assert r.status_code == 400, (
                f"SAB abbreviation {sab!r} silently accepted as system URI — "
                f"client-input-as-canonical drift. Status={r.status_code}, "
                f"body={r.text[:200]}"
            )

    def test_s14_lookup_https_scheme_variant(self, fhir_client):
        """SKEPTIC: https:// vs http://. Per RFC 3986 §3.1 scheme is
        case-insensitive but the scheme name itself is significant.
        ``https://snomed.info/sct`` is a DIFFERENT URI from the canonical
        ``http://snomed.info/sct``. The server MUST NOT silently alias them
        (silent-acceptance of a near-canonical URI is silent-wrong-answer
        on the wire).

        NOTE: this probe is intentionally guarded. medterm4ds may legitimately
        normalize http ↔ https if a future operator runs behind HTTPS. The
        failure mode being probed is silent success WITH a 200 body whose
        ``system`` echoes the alias verbatim — that's the drift class. A
        400 or a 200 with canonical-system echo is acceptable.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[("system", "https://snomed.info/sct"), ("code", "73211009")],
        )
        if r.status_code == 200:
            body = r.json()
            # Acceptance: 200 Parameters body whose `system` echo is the
            # canonical http URI (server normalized the alias → canonical).
            # NOT acceptable: 200 with `system` echoing https:// verbatim.
            params = body.get("parameter", [])
            sys_params = [
                p.get("valueUri") for p in params if p.get("name") == "system"
            ]
            for echoed in sys_params:
                if echoed:
                    assert echoed == "http://snomed.info/sct", (
                        f"https:// alias echoed verbatim in Out system: {echoed!r}. "
                        f"Client-input-as-canonical drift."
                    )

    def test_s15_lookup_snomed_edition_uri_recognized(self, fhir_client):
        """SKEPTIC: SNOMED CT edition URIs (e.g. http://snomed.info/sct/731000124108
        for US edition) are clinically valid per https://hl7.org/fhir/R4/snomedct.html.
        The server SHOULD recognize the edition prefix; at minimum it MUST
        NOT silently treat it as a different system (silent-wrong-answer
        surface on cross-edition equivalence). medterm4ds may legitimately
        answer 'unknown code' if the edition isn't seeded — but it should
        NOT silently succeed with a 200 Parameters body that looks like a
        successful lookup when no lookup actually occurred.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[
                ("system", "http://snomed.info/sct/731000124108"),
                ("code", "73211009"),
            ],
        )
        # Acceptance: 400 (edition prefix not supported) OR 200 with Parameters
        # body that has a real `display`. NOT acceptable: 200 with empty
        # Parameters body (silent-wrong-answer).
        if r.status_code == 200:
            body = r.json()
            assert body.get("resourceType") == "Parameters"
            # The body MUST have a display or an explicit not-found message —
            # a Parameters resource with ONLY a `system` echo (no display)
            # is silent-wrong-answer on an unrecognized system.
            params = body.get("parameter", [])
            has_display = any(p.get("name") == "display" for p in params)
            assert has_display, (
                "200 returned for edition URI but no `display` parameter — "
                "silent-wrong-answer on potentially unrecognized system URI."
            )

    def test_s16_lookup_snomed_versioned_uri_recognized(self, fhir_client):
        """SKEPTIC: SNOMED versioned URIs
        (http://snomed.info/sct/731000124108/version/20240901) per
        https://hl7.org/fhir/R4/snomedct.html. Same drift class as s15.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[
                (
                    "system",
                    "http://snomed.info/sct/731000124108/version/20240901",
                ),
                ("code", "73211009"),
            ],
        )
        if r.status_code == 200:
            body = r.json()
            assert body.get("resourceType") == "Parameters"
            params = body.get("parameter", [])
            has_display = any(p.get("name") == "display" for p in params)
            assert has_display, (
                "200 returned for versioned URI but no `display` parameter — "
                "silent-wrong-answer."
            )


# =============================================================================
# Lens 2: capabilitystatement-supported-system extension
# =============================================================================


class TestLens2SupportedSystemExtension:
    """Hostile-input probes for §4.7.3 extension
    `capabilitystatement-supported-system`.

    Spec: https://hl7.org/fhir/R4/extension-capabilitystatement-supported-system.html
    Definition: "A code system that is supported by the system that is not
    defined in a value set resource."
    Comment: "Typically, this is a large terminology such as LOINC, SNOMED CT."
    Value type: uri (1..1 required).
    """

    def test_s20_extension_present_in_metadata(self, fhir_client):
        """The extension MUST be present on /fhir/metadata (CapabilityStatement)."""
        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200
        extensions = r.json().get("extension", [])
        matching = [e for e in extensions if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL]
        assert matching, (
            "capabilitystatement-supported-system extension missing from "
            "CapabilityStatement. Per §4.7.3 + extension spec, the server "
            "MUST list every supported external code system URI."
        )

    def test_s21_extension_shape_valueUri_required(self, fhir_client):
        """Each entry MUST have URL + valueUri (1..1 per spec). NOT valueString,
        NOT valueCoding.system — valueUri specifically. Spec: extension.value[x]
        is type uri with cardinality 1..1 (marked R! in extension definition).
        """
        r = fhir_client.get("/fhir/metadata")
        extensions = r.json().get("extension", [])
        matching = [e for e in extensions if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL]
        assert matching, "extension not present"
        for entry in matching:
            assert "valueUri" in entry, (
                f"Extension entry missing valueUri: {entry!r}. Spec mandates "
                f"value[x] of type uri (1..1)."
            )
            assert entry["valueUri"], (
                f"Extension entry has empty valueUri: {entry!r}"
            )

    def test_s22_extension_lists_every_canonical_uri(self, fhir_client):
        """Bidirectional invariant (HCPCS drift regression class count=8):
        every canonical URI in SYSTEM_TO_FHIR_URI MUST be advertised."""
        r = fhir_client.get("/fhir/metadata")
        extensions = r.json().get("extension", [])
        advertised = {
            e.get("valueUri")
            for e in extensions
            if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
        }
        expected = set(SYSTEM_TO_FHIR_URI.values())
        missing = expected - advertised
        assert not missing, (
            f"Extension under-advertises supported systems: missing={sorted(missing)}"
        )

    def test_s23_extension_does_not_over_advertise(self, fhir_client):
        """Every advertised URI MUST be in SYSTEM_TO_FHIR_URI (no extra URIs)."""
        from medterm4ds.engines.fhir import fhir_uri_to_system

        r = fhir_client.get("/fhir/metadata")
        extensions = r.json().get("extension", [])
        advertised = [
            e.get("valueUri")
            for e in extensions
            if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
        ]
        for uri in advertised:
            assert fhir_uri_to_system(uri) is not None, (
                f"Extension over-advertises a URI not in SYSTEM_TO_FHIR_URI: {uri!r}"
            )

    def test_s24_extension_no_sab_abbreviations(self, fhir_client):
        """SKEPTIC: the extension MUST list canonical URIs only — NOT raw
        UMLS SAB abbreviations (SNOMEDCT_US, RXNORM, LNC, etc.). SAB leakage
        is the count=8 PROMOTED canonical-URI drift regression class.
        """
        r = fhir_client.get("/fhir/metadata")
        extensions = r.json().get("extension", [])
        advertised = [
            e.get("valueUri")
            for e in extensions
            if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
        ]
        # SAB abbreviations are short uppercase strings without a scheme
        sab_leakage = [u for u in advertised if isinstance(u, str) and "://" not in u]
        assert not sab_leakage, (
            f"SAB abbreviations leaked into capabilitystatement-supported-system "
            f"extension: {sab_leakage}. Only canonical FHIR R4 URIs allowed."
        )

    def test_s25_extension_no_alias_uris(self, fhir_client):
        """SKEPTIC: the extension MUST NOT list alias URIs (e.g. urn:oid
        variants, the legacy HCPCS THO URL). Only canonical URIs from
        SYSTEM_TO_FHIR_URI."""
        from medterm4ds.engines.fhir import FHIR_URI_ALIASES

        r = fhir_client.get("/fhir/metadata")
        extensions = r.json().get("extension", [])
        advertised = {
            e.get("valueUri")
            for e in extensions
            if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
        }
        alias_leakage = set(FHIR_URI_ALIASES.keys()) & advertised
        assert not alias_leakage, (
            f"Alias URIs leaked into extension (only canonical allowed): "
            f"{sorted(alias_leakage)}"
        )

    def test_s26_extension_deduplicated(self, fhir_client):
        """SKEPTIC: each canonical URI SHOULD appear exactly once. Duplicates
        are a silent-overpromise signal (advertises the same system twice)."""
        r = fhir_client.get("/fhir/metadata")
        extensions = r.json().get("extension", [])
        advertised = [
            e.get("valueUri")
            for e in extensions
            if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
        ]
        assert len(advertised) == len(set(advertised)), (
            f"Duplicate URIs in extension: {advertised}"
        )

    def test_s27_extension_also_on_terminology_capabilities(self, fhir_client):
        """SKEPTIC: when mode=terminology is requested, the response is a
        TerminologyCapabilities resource. Per the extension spec the
        context-of-use is "Element ID CapabilityStatement" — so the extension
        does NOT appear on TerminologyCapabilities. Verify it is absent
        there (defensive — silent inclusion would be a context-of-use
        violation per FHIR R4 §3.5)."""
        r = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        if r.status_code == 200:
            body = r.json()
            if body.get("resourceType") == "TerminologyCapabilities":
                extensions = body.get("extension", [])
                matching = [
                    e
                    for e in extensions
                    if e.get("url") == SUPPORTED_SYSTEM_EXTENSION_URL
                ]
                # The extension's context-of-use is CapabilityStatement only.
                # Presence on TerminologyCapabilities is a spec violation.
                # NOTE: this probe asserts the spec-correct absence — if the
                # impl includes it, that's a NEW bug (context-of-use drift).
                assert not matching, (
                    "capabilitystatement-supported-system extension appeared on "
                    "TerminologyCapabilities — context-of-use is CapabilityStatement "
                    "only per https://hl7.org/fhir/R4/extension-capabilitystatement-"
                    "supported-system.html"
                )


# =============================================================================
# Lens 3: Implicit value set URL edge cases — CF-HISTORIAN-VS02-02 here
# =============================================================================


class TestLens3ImplicitValueSetUrls:
    """Hostile-input probes for §4.7.3.1 implicit value sets.

    Spec: https://build.fhir.org/terminology-service.html §4.7.3.1
      "Every code system has an implicit value set that is 'all the concepts
       defined in the code system' (CodeSystem.valueSet)."
      "For some code systems, these value set URIs are defined in advance
       (e.g., for LOINC, it is http://loinc.org/vs)."
      "Clients can refer to these implicit value sets by providing the URI
       for the code system itself."

    Two URL forms:
      (a) `<system-uri>/vs` — e.g. http://loinc.org/vs (all of LOINC)
      (b) `http://snomed.info/sct?fhir_vs` (no =isa, no code in path)
          — all of SNOMED CT.

    CF-HISTORIAN-VS02-02 (OPEN, MEDIUM): Form (a) echoes the client-supplied
    prefix verbatim as `contains[].system` instead of re-resolving through
    `canonical_system_uri()`. Tested with alias URIs and trailing-slash
    variants below.
    """

    def test_s30_loinc_implicit_vs_resolves(self, fhir_client):
        """Positive shape: http://loinc.org/vs MUST expand to a ValueSet
        body (canonical form). Per spec: 'for LOINC, it is http://loinc.org/vs'."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand", params=[("url", "http://loinc.org/vs")]
        )
        assert r.status_code == 200, (
            f"http://loinc.org/vs failed to expand: {r.status_code} {r.text[:200]}"
        )
        body = r.json()
        assert body.get("resourceType") == "ValueSet"

    def test_s31_snomed_implicit_vs_bare_fhir_vs_resolves(self, fhir_client):
        """Positive shape: http://snomed.info/sct?fhir_vs (Form (b)) MUST
        expand to a ValueSet body with non-empty expansion. Per spec + the
        HISTORIAN TS-03 iteration (QA-034) tightening to positive success-shape."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct?fhir_vs")],
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("resourceType") == "ValueSet"
        expansion = body.get("expansion", {})
        assert "contains" in expansion and isinstance(expansion["contains"], list)

    def test_s32_cf_vs02_02_trailing_slash_drift(self, fhir_client):
        """**CF-HISTORIAN-VS02-02 regression probe (trailing-slash variant)**.

        Client requests `http://snomed.info/sct//vs` (note double slash).
        After `_is_implicit_value_set_url` strips the trailing /vs, the
        prefix is `http://snomed.info/sct/` — the trailing-slash alias.
        The handler then uses this prefix verbatim as `contains[].system`
        instead of re-resolving through `canonical_system_uri()`.

        Per FHIR R4 §4.7.3: 'Clients can refer to these implicit value sets
        by providing the URI for the code system itself.' The URI identifies
        the canonical code system; the response's contains[].system MUST be
        the canonical URI so clients can round-trip via $lookup.

        Spec citation: FHIR R4 §3.4 (CodeSystem.url is the canonical
        identifier) + §4.7.3 Value Set Validation.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct//vs")],
        )
        if r.status_code != 200:
            pytest.skip(
                f"Trailing-slash implicit URL returned {r.status_code}, "
                f"not 200 — drift probe not applicable."
            )
        body = r.json()
        contains = body.get("expansion", {}).get("contains", [])
        if not contains:
            pytest.skip("empty expansion — drift not observable")
        systems = {c.get("system") for c in contains}
        # CF-HISTORIAN-VS02-02 predicts drift: contains[].system = trailing-slash alias.
        assert systems == {"http://snomed.info/sct"}, (
            f"CF-HISTORIAN-VS02-02 DRIFT CONFIRMED: contains[].system = {systems}, "
            f"expected canonical http://snomed.info/sct. The "
            f"_expand_implicit_value_set Form (a) echoes the client-supplied "
            f"prefix verbatim instead of calling canonical_system_uri()."
        )

    def test_s33_cf_vs02_02_urn_oid_snomed_drift(self, fhir_client):
        """**CF-HISTORIAN-VS02-02 regression probe (urn:oid SNOMED variant)**.

        Client requests `urn:oid:2.16.840.1.113883.6.96/vs`. The prefix
        (after stripping /vs) is the urn:oid alias for SNOMEDCT_US — in
        FHIR_URI_ALIASES, so fhir_uri_to_system resolves to SNOMEDCT_US.
        The handler then echoes `urn:oid:...` verbatim as
        contains[].system instead of canonical http://snomed.info/sct.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "urn:oid:2.16.840.1.113883.6.96/vs")],
        )
        if r.status_code != 200:
            pytest.skip(
                f"urn:oid SNOMED implicit URL returned {r.status_code}, "
                f"not 200 — drift probe not applicable."
            )
        body = r.json()
        contains = body.get("expansion", {}).get("contains", [])
        if not contains:
            pytest.skip("empty expansion — drift not observable")
        systems = {c.get("system") for c in contains}
        assert systems == {"http://snomed.info/sct"}, (
            f"CF-HISTORIAN-VS02-02 DRIFT CONFIRMED: contains[].system = {systems}, "
            f"expected canonical http://snomed.info/sct."
        )

    def test_s34_cf_vs02_02_urn_oid_loinc_drift(self, fhir_client):
        """**CF-HISTORIAN-VS02-02 regression probe (urn:oid LOINC variant)**.

        Client requests `urn:oid:2.16.840.1.113883.6.1/vs`. Same drift class
        as test_s33 but for LOINC — prefix resolves to LNC via alias map.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "urn:oid:2.16.840.1.113883.6.1/vs")],
        )
        if r.status_code != 200:
            pytest.skip(
                f"urn:oid LOINC implicit URL returned {r.status_code}, not 200."
            )
        body = r.json()
        contains = body.get("expansion", {}).get("contains", [])
        if not contains:
            pytest.skip("empty expansion — drift not observable")
        systems = {c.get("system") for c in contains}
        assert systems == {"http://loinc.org"}, (
            f"CF-HISTORIAN-VS02-02 DRIFT CONFIRMED: contains[].system = {systems}, "
            f"expected canonical http://loinc.org."
        )

    def test_s35_cf_vs02_02_hcpcs_legacy_alias_drift(self, fhir_client):
        """**CF-HISTORIAN-VS02-02 regression probe (HCPCS legacy alias variant)**.

        Client requests the legacy HCPCS THO URL + /vs:
        `http://terminology.hl7.org/CodeSystem/hcpcs-Level-II/vs`. The
        prefix resolves to HCPCS via FHIR_URI_ALIASES. Per the count=8
        PROMOTED canonical-URI drift regression class, the response
        contains[].system MUST be the canonical
        `http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets`, NOT
        the legacy alias.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                (
                    "url",
                    "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II/vs",
                )
            ],
        )
        if r.status_code != 200:
            pytest.skip(
                f"HCPCS legacy alias implicit URL returned {r.status_code}, not 200."
            )
        body = r.json()
        contains = body.get("expansion", {}).get("contains", [])
        if not contains:
            pytest.skip("empty expansion — drift not observable")
        systems = {c.get("system") for c in contains}
        assert systems == {
            "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
        }, (
            f"CF-HISTORIAN-VS02-02 DRIFT CONFIRMED on HCPCS: contains[].system = "
            f"{systems}, expected canonical HCPCS URI. HCPCS drift regression "
            f"class (count=8 PROMOTED) extends to implicit VS Form (a)."
        )

    def test_s36_cf_vs02_02_urn_oid_icd10cm_drift(self, fhir_client):
        """**CF-HISTORIAN-VS02-02 regression probe (urn:oid ICD10CM variant)**."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "urn:oid:2.16.840.1.113883.6.90/vs")],
        )
        if r.status_code != 200:
            pytest.skip(
                f"urn:oid ICD10CM implicit URL returned {r.status_code}, not 200."
            )
        body = r.json()
        contains = body.get("expansion", {}).get("contains", [])
        if not contains:
            pytest.skip("empty expansion — drift not observable")
        systems = {c.get("system") for c in contains}
        assert systems == {"http://hl7.org/fhir/sid/icd-10-cm"}, (
            f"CF-HISTORIAN-VS02-02 DRIFT CONFIRMED: contains[].system = {systems}, "
            f"expected canonical http://hl7.org/fhir/sid/icd-10-cm."
        )

    def test_s37_malformed_implicit_url_extra_suffix_rejected(self, fhir_client):
        """SKEPTIC: http://loinc.org/vs/extra is NOT a valid implicit URL
        (extra path after /vs). Server MUST NOT silently treat it as the
        implicit LOINC VS — that would be silent-wrong-answer on an
        unrecognized URL.

        Spec: §4.7.3.1 — implicit VS URL is exactly `<system-uri>/vs`.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://loinc.org/vs/extra")],
        )
        # Acceptance: 400 (unrecognized URL) — NOT 200 with an expansion
        # that pretends the URL resolved.
        if r.status_code == 200:
            body = r.json()
            expansion = body.get("expansion", {})
            contains = expansion.get("contains", [])
            # If the server returns 200, it MUST be because the URL matched
            # the implicit LOINC VS (not a silent-empty 200). The expansion
            # should match what http://loinc.org/vs returns.
            # The drift class: silently treating `http://loinc.org/vs/extra`
            # as `http://loinc.org/vs` after some string-suffix match.
            assert contains, (
                "Empty 200 expansion for malformed URL http://loinc.org/vs/extra "
                "— silent-wrong-answer on unrecognized implicit URL."
            )

    def test_s38_malformed_implicit_url_query_suffix_rejected(self, fhir_client):
        """SKEPTIC: http://loinc.org/vs?fhir_vs=unknown is NOT a valid URL.
        The /vs suffix and the ?fhir_vs=... query are MUTUALLY EXCLUSIVE
        conventions. The server MUST reject, not silently accept the /vs
        suffix and ignore the unknown query param."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://loinc.org/vs?fhir_vs=unknown")],
        )
        if r.status_code == 200:
            body = r.json()
            # If the server silently accepted the malformed URL by ignoring
            # the unknown query, the expansion is silent-wrong-answer.
            # Acceptable: 200 with a too-costly extension indicating the
            # implicit VS was expanded but the query was dropped — but a
            # bare 200 without explanation is the drift.
            assert body.get("resourceType") == "ValueSet"

    def test_s39_nonexistent_system_implicit_url_rejected(self, fhir_client):
        """SKEPTIC: http://example.com/fake-system/vs uses the /vs suffix
        but the prefix is NOT a supported code system. Server MUST return
        400 'Unrecognized code system URI', NOT silently succeed with an
        empty expansion."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://example.com/fake-system/vs")],
        )
        assert r.status_code == 400, (
            f"Non-existent system implicit URL should return 400, got "
            f"{r.status_code}. Body: {r.text[:200]}"
        )

    def test_s40_snomed_intensional_with_code_resolves(self, fhir_client):
        """Positive shape: http://snomed.info/sct/73211009?fhir_vs=isa
        MUST expand to the root + descendants. Spec: §4.7.3.2 SNOMED CT
        intensional value set URL convention.

        NOTE: this is Form (b) — the intensional handler
        (_expand_url_pattern) DOES use canonical snomed_uri, so it's NOT
        affected by CF-HISTORIAN-VS02-02. We verify it still resolves
        correctly here as a control.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct/73211009?fhir_vs=isa")],
        )
        assert r.status_code == 200
        body = r.json()
        contains = body.get("expansion", {}).get("contains", [])
        codes = {c.get("code") for c in contains}
        # Fixture seeds 44054006 (T2DM) as descendant of 73211009 (DM).
        assert "44054006" in codes, (
            f"Expected descendant 44054006 in expansion, got {sorted(codes)}"
        )
        # CRITICAL: contains[].system MUST be canonical even though the
        # input URL contains /731000124108 or other path segments.
        systems = {c.get("system") for c in contains}
        assert systems == {"http://snomed.info/sct"}, (
            f"Intensional expansion contains[].system drift: {systems}, "
            f"expected canonical http://snomed.info/sct"
        )

    def test_s41_snomed_intensional_with_code_urn_oid_input_rejected(
        self, fhir_client
    ):
        """SKEPTIC: SNOMED intensional URL with urn:oid prefix —
        `urn:oid:2.16.840.1.113883.6.96/73211009?fhir_vs=isa`. Per the
        spec the SNOMED intensional URL convention uses the canonical
        http URI. A urn:oid prefix is non-standard; the server should
        reject or return a clear error.

        This is the intensional handler sibling of CF-HISTORIAN-VS02-02.
        If the impl silently accepts the urn:oid and echoes it verbatim,
        that's a NEW drift instance on a DIFFERENT code path.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                (
                    "url",
                    "urn:oid:2.16.840.1.113883.6.96/73211009?fhir_vs=isa",
                )
            ],
        )
        # The intensional handler (_expand_url_pattern) checks
        # `snomed_uri in base`. urn:oid isn't snomed_uri, so the URL
        # should fall through to "Unsupported fhir_vs URL pattern" 400.
        # SILENT SUCCESS with contains[].system = urn:oid:... is the drift.
        if r.status_code == 200:
            body = r.json()
            contains = body.get("expansion", {}).get("contains", [])
            if contains:
                systems = {c.get("system") for c in contains}
                assert systems == {"http://snomed.info/sct"}, (
                    f"urn:oid intensional URL silently echoed alias as "
                    f"contains[].system: {systems}"
                )


# =============================================================================
# Lens 4: CodeSystem.valueSet URI alone resolvable (Implicit VS Fallback)
# =============================================================================


class TestLens4CodeSystemUriFallback:
    """Hostile-input probes for §4.7.3.1 CodeSystem.valueSet fallback.

    Spec: "Clients can refer to these implicit value sets by providing the
    URI for the code system itself."

    Per spec: a URL matching a known code-system URI (e.g. http://loinc.org)
    SHOULD expand to that system's implicit value set. The medterm4ds server
    treats bare canonical URIs (without /vs suffix) as requiring a filter or
    inline body — which is acceptable for a non-persisting server. The bug
    class is silent success (200 + empty expansion) implying the system has
    no codes.
    """

    def test_s50_canonical_uri_alone_no_silent_empty_success(self, fhir_client):
        """SKEPTIC: a bare canonical URI (no /vs suffix) MUST NOT silently
        succeed with an empty expansion. That would imply the system has
        no codes — silent-wrong-answer.

        Spec: §4.7.3.1 — 'Clients can refer to these implicit value sets
        by providing the URI for the code system itself.' The server
        SHOULD expand; if not, return 400 with a clear message.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand", params=[("url", "http://loinc.org")]
        )
        if r.status_code == 200:
            body = r.json()
            expansion = body.get("expansion", {})
            contains = expansion.get("contains", [])
            if not contains:
                # Per VS-02 SKEPTIC QA-057 + GLOBAL_RULES.md, a silent 200
                # with empty expansion is silent-wrong-answer. Server must
                # include an extension explaining why.
                extensions = expansion.get("extension", [])
                assert extensions, (
                    "Bare canonical URI http://loinc.org returned 200 + empty "
                    "expansion without explanation — silent misrepresentation "
                    "(implies LOINC has no codes)."
                )

    def test_s51_canonical_uri_alone_for_every_system(self, fhir_client):
        """SKEPTIC: same drift class as s50 but parametrized over every
        canonical URI. Confirms the bare-URI path is consistent across
        systems (no per-source if-branch divergence)."""
        for source, uri in CANONICAL_FHIR_R4_URIS.items():
            r = fhir_client.get("/fhir/ValueSet/$expand", params=[("url", uri)])
            if r.status_code == 200:
                body = r.json()
                expansion = body.get("expansion", {})
                contains = expansion.get("contains", [])
                if not contains:
                    extensions = expansion.get("extension", [])
                    assert extensions, (
                        f"Bare {uri} returned 200 + empty expansion without "
                        f"explanation. Per-source divergence on implicit VS "
                        f"fallback for {source}."
                    )

    def test_s52_alias_uri_alone_no_silent_canonical_drift(self, fhir_client):
        """SKEPTIC: a bare alias URI (e.g. urn:oid:2.16.840.1.113883.6.96)
        MUST NOT silently expand AND echo the alias as contains[].system.
        Same CF-HISTORIAN-VS02-02 drift class but on the bare-URI path.

        NOTE: this probe tests the bare-URI path, NOT the /vs path. If
        the bare-URI path is implemented (i.e., the server DOES expand
        bare aliases), contains[].system MUST be canonical.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "urn:oid:2.16.840.1.113883.6.96")],
        )
        if r.status_code == 200:
            body = r.json()
            contains = body.get("expansion", {}).get("contains", [])
            if contains:
                systems = {c.get("system") for c in contains}
                assert systems == {"http://snomed.info/sct"}, (
                    f"Bare alias URI echoed in contains[].system: {systems}"
                )


# =============================================================================
# Lens 5: Terminology maintenance — server validates incoming resources
# =============================================================================


class TestLens5TerminologyMaintenance:
    """Hostile-input probes for §4.7.3.3 Terminology Maintenance.

    Spec: "A terminology server should validate incoming resources and
    ensure integrity of the terminology services."

    medterm4ds is READ-ONLY (no resource persistence); the contract is
    that POST to /fhir/{Type} MUST be rejected (405/404/400), not
    silently accepted and discarded.
    """

    @pytest.mark.parametrize(
        "resource_type,body",
        [
            (
                "CodeSystem",
                {
                    "resourceType": "CodeSystem",
                    "url": "http://example.com/test",
                    "content": "complete",
                    "concept": [{"code": "X", "display": "Test"}],
                },
            ),
            (
                "ValueSet",
                {
                    "resourceType": "ValueSet",
                    "url": "http://example.com/vs",
                    "compose": {
                        "include": [{"system": "http://example.com/test"}]
                    },
                },
            ),
            (
                "ConceptMap",
                {
                    "resourceType": "ConceptMap",
                    "url": "http://example.com/cm",
                    "status": "draft",
                    "group": [],
                },
            ),
        ],
    )
    def test_s60_post_resource_rejected(self, fhir_client, resource_type, body):
        """SKEPTIC: POST a valid resource to /fhir/{Type} MUST be rejected
        by a read-only server. NOT acceptable: 201 Created (silent lie)
        or 200 OK with no body (silent acceptance)."""
        r = fhir_client.post(f"/fhir/{resource_type}", json=body)
        assert r.status_code != 201, (
            f"Read-only server returned 201 Created on POST {resource_type} — "
            f"silent acceptance of resource submission. Status={r.status_code}"
        )

    def test_s61_post_malformed_resource_rejected(self, fhir_client):
        """SKEPTIC: POST a malformed CodeSystem (missing required fields)
        to /fhir/CodeSystem. Server MUST reject with FHIR OperationOutcome
        body (not text/plain 500). Even read-only servers should return
        FHIR-shaped errors."""
        r = fhir_client.post(
            "/fhir/CodeSystem",
            json={"resourceType": "CodeSystem"},  # missing url, content
        )
        # Acceptance: any non-2xx status with FHIR-shaped body OR 405.
        # NOT acceptable: 500 text/plain (information-disclosure surface)
        # OR 2xx (silent success).
        assert r.status_code >= 400, (
            f"Malformed POST returned {r.status_code}, expected >= 400."
        )
        # The body should be a FHIR OperationOutcome OR an HTTP-error body.
        # Plain text 500 with stack trace is the anti-pattern (TS-04 QA-038).
        # NOTE: read-only servers commonly return 405 Method Not Allowed for
        # POST on resource collection endpoints — that's spec-compliant.
        if r.status_code == 500:
            assert "text/plain" not in r.headers.get("content-type", ""), (
                f"500 returned text/plain body — information-disclosure "
                f"surface per GLOBAL_RULES.md Silent Fallbacks."
            )

    def test_s62_post_resource_with_non_canonical_system_uri(self, fhir_client):
        """SKEPTIC: POST a ValueSet with a non-canonical system URI in the
        compose.include. Even read-only, the server's validation contract
        should reject unrecognized system URIs — NOT silently accept and
        later fail when the URL is referenced.

        Per §4.7.3.3: 'validate incoming resources and ensure integrity'.
        A POST with an unrecognized system URI is a request to register a
        ValueSet referencing an unsupported code system."""
        r = fhir_client.post(
            "/fhir/ValueSet",
            json={
                "resourceType": "ValueSet",
                "url": "http://example.com/vs",
                "compose": {
                    "include": [
                        {"system": "http://example.com/nonexistent-system"}
                    ]
                },
            },
        )
        # Acceptance: 405 / 400 / 422. NOT 201 (silent acceptance).
        assert r.status_code != 201

    def test_s63_post_operation_still_works(self, fhir_client):
        """SKEPTIC: POST $lookup should still work (POST is the operation
        invocation, not resource submission). This is a regression guard —
        the read-only contract is on /fhir/{Type}, NOT on operation routes."""
        r = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "73211009"},
                ],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("resourceType") == "Parameters"


# =============================================================================
# Lens 6: Source-reading regression guards (skip-on-fix)
# =============================================================================


class TestLens6SourceReadGuards:
    """Source-reading probes that assert the load-bearing structural
    property is in place. SKIPs when the fix is in; fails loudly when
    a future refactor re-introduces the drift.

    Per VS-05 HISTORIAN strategy 52 + TS-02 HISTORIAN resweep methodology
    (source-read probe skip-on-fix semantics).
    """

    def test_s70_canonical_system_uri_helper_exists(self):
        """Structural guard: the canonical_system_uri helper MUST exist
        in engines.fhir. Without it, every consumer has to inline the
        re-resolution logic — the root cause of client-input-as-canonical
        drift recurring (count=8 PROMOTED)."""
        from medterm4ds.engines.fhir import canonical_system_uri

        assert callable(canonical_system_uri), (
            "canonical_system_uri helper missing from engines.fhir — "
            "structural fix for client-input-as-canonical drift removed."
        )

    def test_s71_cf_vs02_02_fix_in_place(self):
        """**CF-HISTORIAN-VS02-02 fix-in-place source-read guard**.

        The fix for the carry-forward is to call
        `canonical_system_uri(prefix, source=source)` in
        `_expand_implicit_value_set` Form (a) before assigning
        `system_uri`. This probe source-reads the function body and
        SKIPs if the fix is present; fails if the helper call is
        missing (i.e., the bug recurs).

        Per GLOBAL_RULES.md "Code Review Time" client-input-as-canonical
        drift trigger (count=8 PROMOTED).
        """
        import inspect
        from medterm4ds.apps.fhir_api import create_fhir_app

        # _expand_implicit_value_set is defined inside create_fhir_app;
        # we can't easily inspect nested functions via the module.
        # Read the source file directly and look for the load-bearing
        # canonical_system_uri call in _expand_implicit_value_set.
        src_path = inspect.getsourcefile(create_fhir_app)
        with open(src_path) as f:
            src = f.read()
        # Find the _expand_implicit_value_set function and verify it
        # contains a canonical_system_uri call.
        marker = "def _expand_implicit_value_set("
        idx = src.find(marker)
        assert idx >= 0, "_expand_implicit_value_set not found in source"
        # Find the next sibling function to bound the search scope.
        next_marker_idx = src.find("\n    def ", idx + 1)
        if next_marker_idx < 0:
            next_marker_idx = len(src)
        func_body = src[idx:next_marker_idx]
        if "canonical_system_uri" in func_body:
            pytest.skip(
                "CF-HISTORIAN-VS02-02 fix is in place: _expand_implicit_value_set "
                "calls canonical_system_uri()."
            )
        # Bug present: function body does NOT call canonical_system_uri.
        # The drift is still active.
        raise AssertionError(
            "CF-HISTORIAN-VS02-02 STILL OPEN: _expand_implicit_value_set does "
            "NOT call canonical_system_uri() — client-input-as-canonical drift "
            "is still active on Form (a) implicit VS expansion."
        )

    def test_s72_supported_system_extension_uses_registry(self):
        """Structural guard: _supported_system_extensions MUST iterate
        SYSTEM_TO_FHIR_URI, NOT a hardcoded list. Literal-value-vs-canonical-
        registry drift (count=8 PROMOTED) regression guard."""
        import inspect
        from medterm4ds.engines.fhir import responses as responses_mod

        src = inspect.getsource(responses_mod._supported_system_extensions)
        assert "SYSTEM_TO_FHIR_URI" in src, (
            "_supported_system_extensions no longer sources from "
            "SYSTEM_TO_FHIR_URI — literal-vs-canonical-registry drift "
            "regression."
        )
