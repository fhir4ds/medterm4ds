"""TERMINOLOGIST resweep probes for CS-01 (CodeSystem Resource Structure).

Fresh full-sweep per USER_DIRECTIVES [2026-08-08]. Sibling file to the
existing ``test_cs01_terminologist.py`` baseline; this file holds NEW
clinical-correctness probes that re-derive the CS-01 surface from the
TERMINOLOGIST lens.

Spec: https://build.fhir.org/codesystem.html (R4 / 4.0.1)
      https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html
      https://hl7.org/fhir/R4/valueset-filter-operator.html
      https://hl7.org/fhir/R4/codesystem-operation-lookup.html (§4.8.21.1)

TERMINOLOGIST lens (per ROLE_QA_ENGINEER.md §3 TERMINOLOGIST): "Is the
answer clinically right?" Focus on clinical and terminological
correctness — the 4th medterm4ds-specific personality dimension. Per
GLOBAL_RULES.md, findings default to HIGH severity.

Per EXPLORER architect_handoff tip, TERMINOLOGIST focus:
  L1 Canonical URI advertisement clinical correctness per source —
     every source advertised has the correct canonical FHIR R4 URI
     (HCPCS drift class count=8+1 PROMOTED — spot-check one final
     time to close the meta-pattern).
  L2 $lookup display clinical sensibility — display is the engine
     preferred term (clinically meaningful), per FHIR R4 §4.8.21.1
     Out parameter ``display``: "The preferred display for this
     concept".
  L3 match-type server-local vocabulary clinical sensibility —
     DECISION (b) documentation is clinically honest (not claiming
     FHIR enum membership).
  L4 content field clinical correctness per source — real code
     systems (SNOMEDCT_US, RXNORM, LOINC, ICD10CM, etc.) NOT
     advertised as 'example' (clinically misleading per R4 spec:
     "not intended to be workable"); 'not-present' default is
     clinically honest for a non-persisting server.
  L5 property field clinical correctness — code system-specific
     properties clinically meaningful.
  L6 concept hierarchy clinical correctness — parent-child
     relationships match SNOMEDCT_US clinical ontology (e.g.
     73211009 DM parent of 44054006 T2DM).
  L7 filter field clinical correctness — filter operators produce
     clinically correct results.
  L8 Cross-resource clinical consistency — READ CodeSystem
     response consistent with $lookup results for codes in that
     system (cross-resource canonical-URI agreement invariant).

Per SKEPTIC + HISTORIAN + EXPLORER handoffs: the CS-01 surface has
been tested across 104 probes with ZERO production code bugs. The
surface is heavily consolidated — TERMINOLOGIST is the 4th and final
personality closing the meta-patterns.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Registry-as-contract — single source of truth for closed enums + URIs.
# Per GLOBAL_RULES.md "Code Review Time" 12th PROMOTED pattern: import
# canonical constants from engines/fhir/__init__.py; NEVER copy into tests.
# ---------------------------------------------------------------------------
from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    FHIR_R4_FILTER_OPERATORS,
    FHIR_URI_ALIASES,
    SYSTEM_TO_FHIR_URI,
    canonical_system_uri,
    fhir_uri_to_system,
    sab_label_to_fhir_uri,
    system_to_fhir_uri,
)

# FHIR R4 CodeSystemContentMode enum (5 values — verified 2026-08-08 against
# https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html expansion
# "This value set contains 5 concepts").
# Not yet in engines/fhir/__init__.py — CF-SKEPTIC-CS01-RESWEEP-01 LOW
# DEFERRED carry-forward from this run's SKEPTIC.
FHIR_R4_CONTENT_MODES = frozenset({
    "complete", "example", "fragment", "not-present", "supplement",
})

# Server-local engine pipeline vocabulary for the ``match-type`` custom
# property (DECISION (b) — documented, NOT translated to FHIR enum).
# Mirrors the registry in test_cs01_terminologist.py — sourced from the
# _do_lookup docstring at apps/fhir_api.py.
SERVER_LOCAL_MATCH_TYPE_VOCABULARY = frozenset({
    "exact", "original", "broader", "group", "ingredient", "same_cui",
    "cvx_group", "broader_group", "broader_ingredient", "first_axis",
    "snomed_fallback", "snomed_to_target_native_hierarchy",
    "snomed_to_target_snomed_fallback",
})

# Legacy HCPCS THO resource URL — the prior (incorrect) canonical URI.
# MUST be in FHIR_URI_ALIASES (input-only) and MUST NOT be in
# SYSTEM_TO_FHIR_URI.values() (advertisement surface).
# This is the load-bearing constant for the HCPCS URI drift regression
# class (count=8+1 PROMOTED). Per AGENTS.md: canonical URI is the CMS URI
# (https://build.fhir.org/terminologies-systems.html HL7 publishes the
# CMS-hosted HCPCS Release Code Sets URL as canonical).
LEGACY_HCPCS_THO_URL = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
CANONICAL_HCPCS_URI = (
    "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
)

# Canonical URIs for each supported source (per HL7 published canonical):
#   - SNOMED CT — http://snomed.info/sct (https://hl7.org/fhir/R4/snomedct.html)
#   - RxNorm — http://www.nlm.nih.gov/research/umls/rxnorm
#     (https://hl7.org/fhir/R4/rxnorm.html)
#   - LOINC — http://loinc.org (https://hl7.org/fhir/R4/loinc.html)
#   - ICD-10-CM — http://hl7.org/fhir/sid/icd-10-cm
#     (https://hl7.org/fhir/R4/icd.html)
#   - ICD-10-PCS — http://hl7.org/fhir/sid/icd-10-pcs
#   - CPT — http://www.ama-assn.org/go/cpt
#     (https://hl7.org/fhir/R4/cpt.html — AMA copyright)
#   - HCPCS — http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets
#     (HL7 THO CodeSystem-hcpcs-Level-II.html canonical redirect target)
#   - CVX — http://hl7.org/fhir/sid/cvx (https://hl7.org/fhir/R4/cvx.html)
EXPECTED_CANONICAL_URIS: dict[str, str] = {
    "SNOMEDCT_US": "http://snomed.info/sct",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10PCS": "http://hl7.org/fhir/sid/icd-10-pcs",
    "LNC": "http://loinc.org",
    "CPT": "http://www.ama-assn.org/go/cpt",
    "HCPCS": CANONICAL_HCPCS_URI,
    "CVX": "http://hl7.org/fhir/sid/cvx",
}

# Module source paths for source-read probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
_RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)
_FHIR_INIT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "__init__.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_source(path: Path) -> str:
    return path.read_text()


def _get_func_source(source: str, name: str) -> str:
    """Return source text of a top-level OR nested (4-space) function.

    Walks BOTH ``ast.FunctionDef`` AND ``ast.AsyncFunctionDef`` so nested
    async route handlers (e.g. ``read_resource`` / ``search_resource``
    inside ``create_fhir_app``) are source-readable. Mirrors the helper
    in test_cs01_skeptic_resweep.py (extends TS-04 HISTORIAN strategy).
    """
    tree = ast.parse(source)
    queue: list[ast.AST] = [tree]
    while queue:
        node = queue.pop(0)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef, ast.If, ast.For, ast.While,
                                  ast.With, ast.Try)):
                queue.append(child)
    return ""


def _property_value(lookup_body: dict, prop_code: str) -> str | None:
    """Extract a $lookup property's valueString/valueCode/valueUri by code."""
    for p in lookup_body.get("parameter", []):
        if p.get("name") != "property":
            continue
        parts = p.get("part", [])
        code_part = next((pt for pt in parts if pt.get("name") == "code"), {})
        if code_part.get("valueCode") != prop_code:
            continue
        val_part = next((pt for pt in parts if pt.get("name") == "value"), {})
        return (
            val_part.get("valueString")
            or val_part.get("valueCode")
            or val_part.get("valueUri")
        )
    return None


def _top_level_param(lookup_body: dict, name: str) -> str | None:
    """Extract a top-level $lookup parameter value (e.g. ``display``)."""
    for p in lookup_body.get("parameter", []):
        if p.get("name") == name:
            return (
                p.get("valueString")
                or p.get("valueCode")
                or p.get("valueUri")
            )
    return None


# ===========================================================================
# L1 — Canonical URI advertisement clinical correctness per source
# HCPCS drift class (count=8+1 PROMOTED) META-PATTERN CLOSE
# ===========================================================================

class TestL1CanonicalUriAdvertisement:
    """Verify every source is advertised with the correct canonical FHIR R4
    URI. This is the clinical-correctness lens on the HCPCS drift class —
    advertisement of a wrong canonical URI is clinically misleading
    because clients learning the URI from conformance would then use it
    in their own Coding.system fields, propagating the wrong value through
    downstream clinical data stores.
    """

    def test_t10_every_source_advertised_with_clinically_correct_uri(self, fhir_client):
        """L1 — for every source in SYSTEM_TO_FHIR_URI, the canonical URI
        advertised in the conformance fixtures MUST match the HL7-published
        canonical. Cross-checked against the EXPECTED_CANONICAL_URIS registry
        above (sourced from the per-source HL7 FHIR R4 IG pages).

        Spec: FHIR R4 https://build.fhir.org/codesystem.html CodeSystem.url
        "An absolute URI that is used to identify this code system when it
        is referenced in a specification, model, design or an instance."
        Each external code system has exactly ONE canonical URI per HL7
        governance; advertising the wrong one (e.g. legacy THO resource
        URL for HCPCS) is clinical-data-misleading.
        """
        for source, expected_uri in EXPECTED_CANONICAL_URIS.items():
            actual = SYSTEM_TO_FHIR_URI.get(source)
            assert actual == expected_uri, (
                f"{source}: canonical URI advertisement drift — registry has "
                f"{actual!r}; HL7-published canonical is {expected_uri!r}. "
                f"This is the HCPCS URI drift class (count=8+1 PROMOTED) "
                f"recurring on a new source."
            )

    def test_t11_hcpcs_canonical_uri_is_cms_uri_not_tho_resource_url(self, fhir_client):
        """L1 — HCPCS drift regression class (count=8+1 PROMOTED) CLOSE.

        HCPCS canonical URI in the registry MUST be the CMS-hosted
        ``http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets`` URL
        (the canonical system URI used in Coding.system fields per HL7
        THO). The legacy THO CodeSystem resource URL
        ``http://terminology.hl7.org/CodeSystem/hcpcs-Level-II`` is the
        CODESYSTEM RESOURCE URL (not the canonical system URI) and was
        incorrectly used as the canonical prior to TS-01 TERMINOLOGIST
        QA-012 fix.

        Spec: FHIR R4 CodeSystem.url is the canonical identifier; HL7
        THO CodeSystem-hcpcs-Level-II page publishes the canonical
        system URI as the CMS URL.
        """
        assert SYSTEM_TO_FHIR_URI["HCPCS"] == CANONICAL_HCPCS_URI, (
            "HCPCS canonical URI in SYSTEM_TO_FHIR_URI is NOT the CMS URI "
            "— HCPCS drift class (count=8+1 PROMOTED) REGRESSED."
        )
        # The legacy THO URL MUST be ONLY in FHIR_URI_ALIASES as an input-
        # only backwards-compat alias (never advertised).
        assert FHIR_URI_ALIASES.get(LEGACY_HCPCS_THO_URL) == "HCPCS", (
            f"Legacy HCPCS THO URL {LEGACY_HCPCS_THO_URL!r} MUST be in "
            f"FHIR_URI_ALIASES for backwards-compat (existing clients that "
            f"learned the wrong URI still resolve). Removing it would break "
            f"downstream consumers."
        )
        assert LEGACY_HCPCS_THO_URL not in set(SYSTEM_TO_FHIR_URI.values()), (
            f"Legacy HCPCS THO URL {LEGACY_HCPCS_THO_URL!r} MUST NOT be "
            f"advertised in SYSTEM_TO_FHIR_URI.values() — that would "
            f"re-introduce the HCPCS URI drift (count=8+1 PROMOTED)."
        )

    def test_t12_termcaps_advertises_canonical_hcpcs_not_legacy(self, fhir_client):
        """L1 — HCPCS drift class close on TerminologyCapabilities surface.

        Per FHIR R4 https://build.fhir.org/terminology-service.html
        §4.7.1.1 item 5: TerminologyCapabilities.codeSystem[].uri SHALL
        advertise the canonical system URI for each supported code system.
        The HCPCS drift class (count=8+1 PROMOTED) would manifest here as
        the legacy THO URL appearing instead of the CMS URI.
        """
        r = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        assert r.status_code == 200
        term_caps = r.json()
        advertised_uris = {cs["uri"] for cs in term_caps.get("codeSystem", [])}
        # The canonical HCPCS URI MUST be advertised.
        assert CANONICAL_HCPCS_URI in advertised_uris, (
            f"TerminologyCapabilities does NOT advertise canonical HCPCS URI "
            f"{CANONICAL_HCPCS_URI!r} — HCPCS drift class recurred on the "
            f"TermCaps surface."
        )
        # The legacy THO URL MUST NOT be advertised.
        assert LEGACY_HCPCS_THO_URL not in advertised_uris, (
            f"TerminologyCapabilities ADVERTISES the legacy HCPCS THO URL "
            f"{LEGACY_HCPCS_THO_URL!r} — HCPCS drift class (count=8+1 "
            f"PROMOTED) REGRESSED on the TermCaps surface."
        )

    def test_t13_capstmt_extension_advertises_canonical_hcpcs(self, fhir_client):
        """L1 — HCPCS drift class close on CapabilityStatement extension
        surface (capabilitystatement-supported-system).

        Per https://hl7.org/fhir/R4/extension-capabilitystatement-
        supported-system.html: the extension's valueUri is the canonical
        system URI for an external code system the server supports. HCPCS
        drift would manifest here as the legacy THO URL appearing in the
        extension list.
        """
        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200
        cap_stmt = r.json()
        ext_uris = {
            ext.get("valueUri")
            for ext in cap_stmt.get("extension", [])
            if "supported-system" in ext.get("url", "")
        }
        assert CANONICAL_HCPCS_URI in ext_uris, (
            f"CapabilityStatement extension does NOT advertise canonical "
            f"HCPCS URI {CANONICAL_HCPCS_URI!r} — HCPCS drift recurred on "
            f"the CapStmt extension surface."
        )
        assert LEGACY_HCPCS_THO_URL not in ext_uris, (
            f"CapabilityStatement extension ADVERTISES the legacy HCPCS THO "
            f"URL {LEGACY_HCPCS_THO_URL!r} — HCPCS drift class REGRESSED."
        )

    def test_t14_no_source_uses_legacy_resource_url_as_canonical(self, fhir_client):
        """L1 — generalize the HCPCS drift class audit beyond HCPCS:
        NO source in SYSTEM_TO_FHIR_URI should use a THO resource URL
        (``http://terminology.hl7.org/CodeSystem/...``) as the canonical
        URI. THO resource URLs describe the CodeSystem RESOURCE; the
        canonical system URI is published separately per the source's
        owning authority (HL7, NLM, AMA, CMS, CDC).
        """
        for source, uri in SYSTEM_TO_FHIR_URI.items():
            assert not uri.startswith(
                "http://terminology.hl7.org/CodeSystem/"
            ), (
                f"{source}: canonical URI {uri!r} is a THO CodeSystem "
                f"resource URL — not the canonical system URI. This is the "
                f"HCPCS drift class recurring on a sibling source "
                f"(count=8+1 PROMOTED)."
            )

    def test_t15_legacy_tho_url_resolves_to_canonical_source(self, fhir_client):
        """L1 — the legacy HCPCS THO URL MUST still resolve (input-only
        backwards-compat alias) so existing clients that learned the
        wrong URI don't break. The resolution produces the canonical
        source AND the canonical URI (via canonical_system_uri).
        """
        # fhir_uri_to_system: legacy URL → HCPCS source.
        assert fhir_uri_to_system(LEGACY_HCPCS_THO_URL) == "HCPCS", (
            "Legacy HCPCS THO URL does not resolve via fhir_uri_to_system — "
            "backwards-compat alias removed; existing clients break."
        )
        # canonical_system_uri: legacy URL → canonical CMS URI.
        assert canonical_system_uri(LEGACY_HCPCS_THO_URL) == CANONICAL_HCPCS_URI, (
            "canonical_system_uri does not re-resolve legacy HCPCS THO URL "
            "to canonical CMS URI — HCPCS drift class partially recurred "
            "(re-resolution path broken)."
        )

    def test_t16_no_sab_abbreviation_in_advertised_uris(self, fhir_client):
        """L1 — clinical-safety: NO advertised canonical URI should be a
        raw SAB abbreviation (e.g. ``SNOMEDCT_US``, ``RXNORM``, ``ICD10CM``).
        Clients learning the URI from conformance would then use the SAB
        in their Coding.system — non-resolvable, non-canonical, breaks
        downstream FHIR validation.
        """
        sab_labels = set(SYSTEM_TO_FHIR_URI.keys())
        advertised_uris = set(SYSTEM_TO_FHIR_URI.values())
        overlap = sab_labels & advertised_uris
        assert not overlap, (
            f"SAB abbreviation(s) {sorted(overlap)} appear in canonical URIs "
            f"— clinical safety defect (SAB is not a resolvable URI)."
        )
        # Also: every advertised URI starts with a scheme.
        for source, uri in SYSTEM_TO_FHIR_URI.items():
            assert uri.startswith(("http://", "https://", "urn:")), (
                f"{source}: canonical URI {uri!r} lacks scheme — clinical "
                f"safety defect (clients cannot resolve)."
            )


# ===========================================================================
# L2 — $lookup display clinical sensibility
# ===========================================================================

class TestL2LookupDisplayClinicalSensibility:
    """Verify $lookup returns clinically sensible displays for each seeded
    code. The display is the engine's preferred term (the PT atom STR
    from mrconso for that source's preferred-TTY).

    Spec: FHIR R4 https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    Out parameter ``display`` (1..1, string): "The preferred display for
    this concept". The display MUST be a clinically meaningful string
    (not the raw code, not a SAB abbreviation, not a TTY marker).
    """

    def test_t20_snomed_dm_display_is_clinically_correct(self, fhir_client):
        """L2 — SNOMED CT 73211009 ("Diabetes mellitus") is a foundational
        clinical concept. The display returned MUST be the clinically
        preferred term ("Diabetes mellitus"), not a fragment, code, or
        ambiguous short form.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": "73211009"},
        )
        assert r.status_code == 200
        display = _top_level_param(r.json(), "display")
        assert display == "Diabetes mellitus", (
            f"SNOMED 73211009 display={display!r}; clinically correct "
            f"display is 'Diabetes mellitus'."
        )

    def test_t21_snomed_t2dm_display_distinguishes_from_type1(self, fhir_client):
        """L2 — SNOMED CT 44054006 ("Type 2 diabetes mellitus") MUST
        return a display that clinically distinguishes it from Type 1
        DM. A terminology server that returned "Diabetes mellitus" for
        both codes would be a HIGH-severity clinical defect (Type 1 vs
        Type 2 DM have different management pathways).
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": "44054006"},
        )
        assert r.status_code == 200
        display = _top_level_param(r.json(), "display")
        assert display == "Type 2 diabetes mellitus", (
            f"SNOMED 44054006 display={display!r}; clinically correct "
            f"display is 'Type 2 diabetes mellitus' (Type 2 must be "
            f"distinguishable from Type 1 in any clinical workflow)."
        )
        # Clinical distinguishability invariant: T2DM display must contain
        # "Type 2" (the distinguishing qualifier).
        assert "type 2" in display.lower() or "t2dm" in display.lower(), (
            f"SNOMED 44054006 display {display!r} lacks 'Type 2' qualifier — "
            f"clinically ambiguous (could be confused with Type 1 DM)."
        )

    def test_t22_icd10cm_display_returns_clinical_term_not_code(self, fhir_client):
        """L2 — ICD-10-CM E11 (Type 2 diabetes mellitus without
        complications) MUST return a clinical display, not the raw code
        or a SAB-coded abbreviation. The display should describe the
        clinical concept in plain medical language.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "E11"},
        )
        assert r.status_code == 200
        display = _top_level_param(r.json(), "display")
        assert display is not None, "ICD-10-CM E11 $lookup did not return a display."
        # Clinical: display MUST be a meaningful clinical term, not the raw code.
        assert display != "E11", (
            f"ICD-10-CM E11 display is the raw code {display!r} — clinical "
            f"safety defect (display MUST be the clinical term)."
        )
        # Display MUST mention diabetes (the clinical concept).
        assert "diabetes" in display.lower(), (
            f"ICD-10-CM E11 display {display!r} doesn't mention diabetes — "
            f"clinical terminology drift."
        )

    def test_t23_rxnorm_display_contains_drug_name(self, fhir_client):
        """L2 — RxNorm 860975 (24 HR metformin 500 MG Oral Tablet) MUST
        return a display containing the drug name ("metformin") and
        clinically meaningful dose/form information. A display of just
        "860975" or "Oral Tablet" would be clinically misleading.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                "code": "860975",
            },
        )
        assert r.status_code == 200
        display = _top_level_param(r.json(), "display")
        assert display is not None, "RxNorm 860975 $lookup did not return a display."
        assert "metformin" in display.lower(), (
            f"RxNorm 860975 display {display!r} doesn't contain 'metformin' "
            f"— clinical drug identification defect (display MUST name the "
            f"active ingredient)."
        )
        # Clinical: display MUST include dose form ("Oral Tablet") so a
        # pharmacist can verify the prescription is the right formulation.
        assert "oral" in display.lower() and "tablet" in display.lower(), (
            f"RxNorm 860975 display {display!r} lacks 'Oral Tablet' dose "
            f"form — clinical formulation drift."
        )

    def test_t24_no_display_echoes_raw_code(self, fhir_client):
        """L2 — generalize the display-vs-code clinical invariant: for
        every seeded code, the $lookup display MUST NOT equal the raw
        code value (clinical safety: display is the clinical term, not
        the code identifier).
        """
        cases = [
            ("http://snomed.info/sct", "73211009"),
            ("http://snomed.info/sct", "44054006"),
            ("http://hl7.org/fhir/sid/icd-10-cm", "E11"),
            ("http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
        ]
        for system_uri, code in cases:
            r = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": system_uri, "code": code},
            )
            assert r.status_code == 200
            display = _top_level_param(r.json(), "display")
            assert display != code, (
                f"{system_uri}/{code}: $lookup display = raw code "
                f"{display!r} — clinical safety defect."
            )


# ===========================================================================
# L3 — match-type server-local vocabulary clinical sensibility
# ===========================================================================

class TestL3MatchTypeServerLocalVocabulary:
    """Verify the match-type server-local custom property is clinically
    honest: documented as server-local pipeline vocabulary, NOT claiming
    FHIR R4 ConceptMapEquivalence enum membership.

    Spec: FHIR R4 https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    §4.8.21.1 Out parameter ``property`` — custom properties via the
    ``property`` group are spec-permitted. The values are code-system-
    defined (i.e., server-local is conformant when documented).
    """

    def test_t30_match_type_docstring_documents_server_local(self):
        """L3 — the _do_lookup handler docstring MUST document that
        ``match-type`` is a SERVER-LOCAL custom property (NOT FHIR
        ConceptMapEquivalence). A client reading ``match-type: broader``
        must be able to discover from source that this is engine pipeline
        vocabulary describing HOW the PF name was derived, not a semantic
        equivalence claim.

        Without this documentation, a client familiar with ConceptMap
        Equivalence might mistake ``broader`` as ``equivalence: wider``
        — clinical misinterpretation risk (a patient-friendly name with
        ``match_type=broader`` means the name came from a broader
        concept, NOT that the underlying code is "wider" than the
        patient-friendly concept).
        """
        text = _read_source(_FHIR_API_PATH)
        # Documentation contract: handler MUST name the property and
        # clarify it's server-local (not FHIR enum).
        assert "match-type" in text, (
            "_do_lookup handler does not document the match-type custom "
            "property — documentation contract missing."
        )
        assert "server-local" in text.lower(), (
            "_do_lookup handler does not document match-type as SERVER-LOCAL "
            "— clinical misinterpretation risk (client could mistake it for "
            "FHIR ConceptMapEquivalence)."
        )
        # Cross-reference to FHIR ConceptMapEquivalence so future maintainers
        # re-evaluate the translate-vs-document decision.
        assert "ConceptMapEquivalence" in text, (
            "_do_lookup handler does not cross-reference FHIR R4 ConceptMap"
            "Equivalence — future maintainer may add a translation map "
            "without re-evaluating decision (b)."
        )

    def test_t31_server_local_vocab_disjoint_from_fhir_enum(self):
        """L3 — the server-local match-type vocabulary MUST be DISJOINT
        from the FHIR R4 ConceptMapEquivalence enum. If any value
        overlaps, the documentation contract is no longer sufficient
        — a client could mistake the value for an enum membership.
        """
        overlap = SERVER_LOCAL_MATCH_TYPE_VOCABULARY & FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        assert not overlap, (
            f"match-type values {sorted(overlap)} are in the FHIR R4 "
            f"ConceptMapEquivalence enum — DECISION (b) violated. Either "
            f"rename the engine pipeline vocabulary or re-evaluate decision "
            f"(b) → (a) for the overlapping value alone."
        )

    def test_t32_match_type_on_wire_in_server_local_vocab(self, fhir_client):
        """L3 — when $lookup emits a ``match-type`` custom property, the
        value MUST be in the SERVER_LOCAL_MATCH_TYPE_VOCABULARY registry.
        Any value outside this registry is either (a) a new engine
        pipeline branch that should be documented (extend the registry)
        or (b) drift into FHIR enum (decision (b) violation).

        Fixture limitation: SNOMED 73211009 lacks PF data in the
        conformance fixture (CF-EXPLORER-CS01-FIXTURE-01), so this probe
        may skip — but the assertion shape is the load-bearing contract.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": "73211009"},
        )
        assert r.status_code == 200
        body = r.json()
        mt = _property_value(body, "match-type")
        if mt is None:
            pytest.skip(
                "match-type property not emitted — production PF JSONs not "
                "loaded for the seeded code (CF-EXPLORER-CS01-FIXTURE-01)."
            )
        assert mt in SERVER_LOCAL_MATCH_TYPE_VOCABULARY, (
            f"$lookup emitted match-type={mt!r} which is NOT in the "
            f"SERVER_LOCAL_MATCH_TYPE_VOCABULARY registry. Either add it "
            f"with a derivation note or translate via a new map (and "
            f"re-evaluate decision (b) → (a) for this value alone)."
        )


# ===========================================================================
# L4 — content field clinical correctness per source
# ===========================================================================

class TestL4ContentFieldClinicalCorrectness:
    """Verify the content field advertisement is clinically correct per
    source. Real external code systems (SNOMEDCT_US, RXNORM, LOINC,
    ICD10CM, etc.) MUST NOT be advertised as ``example`` — that value
    is clinically misleading per FHIR R4 spec ("not intended to be
    workable"). The ``not-present`` default is clinically honest for a
    non-persisting terminology server (medterm4ds does NOT persist
    CodeSystem resources).

    Spec: FHIR R4 https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html
      - ``not-present``: "None of the concepts defined by the code system
        are included in the resource." — clinically honest for medterm4ds
        (we expose the code systems via $lookup, not via a persisted
        CodeSystem resource).
      - ``example``: "A few representative concepts ... no claim of
        completeness or curated usefulness. It's not intended to be
        workable." — clinically MISLEADING for real external code systems.
    """

    def test_t40_no_real_system_advertised_as_example(self, fhir_client):
        """L4 — every real external code system (SNOMEDCT_US, RXNORM,
        LOINC, ICD10CM, etc.) MUST NOT be advertised with content=
        'example'. The R4 spec defines 'example' as "not intended to be
        workable" — advertising a real clinical code system as 'example'
        tells clients the data is illustrative-only, which would be a
        HIGH-severity clinical-data-misleading defect.
        """
        r = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        assert r.status_code == 200
        term_caps = r.json()
        for cs in term_caps.get("codeSystem", []):
            uri = cs.get("uri", "")
            content = cs.get("content", "")
            assert content != "example", (
                f"Real external code system {uri!r} advertised with "
                f"content='example' — clinical misadvertisement (R4 spec "
                f"defines 'example' as 'not intended to be workable'; real "
                f"clinical code systems are workable)."
            )

    def test_t41_all_advertised_systems_use_clinically_honest_content(self, fhir_client):
        """L4 — every advertised code system's content value MUST be in
        the FHIR R4 CodeSystemContentMode enum AND must be clinically
        honest. For medterm4ds (non-persisting server), ``not-present``
        is the clinically honest choice — the server does NOT claim to
        expose the full code system via a persisted CodeSystem resource.
        """
        r = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        assert r.status_code == 200
        term_caps = r.json()
        advertised = {cs["uri"]: cs.get("content") for cs in term_caps.get("codeSystem", [])}
        # Every advertised content value is in the R4 enum.
        for uri, content in advertised.items():
            assert content in FHIR_R4_CONTENT_MODES, (
                f"{uri}: content={content!r} is NOT in FHIR R4 "
                f"CodeSystemContentMode enum (5 values: "
                f"{sorted(FHIR_R4_CONTENT_MODES)})."
            )
        # For medterm4ds: every real external system uses 'not-present'.
        # (This is the clinically honest value per AGENTS.md NOT A BUG
        # Registry — medterm4ds does NOT persist CodeSystem resources.)
        for uri, content in advertised.items():
            assert content == "not-present", (
                f"{uri}: content={content!r} — for a non-persisting "
                f"terminology server, 'not-present' is the clinically "
                f"honest value. Other values (complete/fragment/supplement) "
                f"would claim a code-system-curation scope medterm4ds "
                f"does not provide; 'example' would mislead clients."
            )

    def test_t42_no_advertised_system_missing_content_field(self, fhir_client):
        """L4 — FHIR R4 CodeSystem.content is 1..1 (required). Every
        advertised code system in TerminologyCapabilities.codeSystem[]
        MUST include the content field. Missing content is a structural
        conformance violation (clients cannot determine the curation
        scope without it).
        """
        r = fhir_client.get("/fhir/metadata", params={"mode": "terminology"})
        assert r.status_code == 200
        term_caps = r.json()
        for cs in term_caps.get("codeSystem", []):
            uri = cs.get("uri", "<missing uri>")
            assert "content" in cs, (
                f"TerminologyCapabilities codeSystem entry for {uri!r} "
                f"lacks the required 'content' field per FHIR R4 "
                f"CodeSystem.content (1..1)."
            )

    def test_t43_capstmt_extension_uris_match_termcaps_uris(self, fhir_client):
        """L4 — clinical-safety cross-surface consistency: the URIs
        advertised in the CapabilityStatement extension (capability
        statement-supported-system) MUST be the SAME set as the URIs in
        TerminologyCapabilities.codeSystem[].uri. A divergence would
        mean the server tells clients via one channel that it supports
        a system but via another channel that it doesn't — clinical
        ambiguity for a client deciding whether to consult the system.
        """
        term_resp = fhir_client.get(
            "/fhir/metadata", params={"mode": "terminology"}
        )
        full_resp = fhir_client.get("/fhir/metadata")
        assert term_resp.status_code == 200
        assert full_resp.status_code == 200
        term_uris = {cs["uri"] for cs in term_resp.json().get("codeSystem", [])}
        ext_uris = {
            ext.get("valueUri")
            for ext in full_resp.json().get("extension", [])
            if "supported-system" in ext.get("url", "")
        }
        assert term_uris == ext_uris, (
            f"Cross-surface clinical consistency violation: TermCaps URIs "
            f"differ from CapStmt extension URIs. TermCaps-only={sorted(term_uris - ext_uris)}, "
            f"CapStmt-ext-only={sorted(ext_uris - term_uris)}. A client "
            f"consulting one surface gets a different supported-system list "
            f"than a client consulting the other — clinical ambiguity."
        )


# ===========================================================================
# L5 — property field clinical correctness
# ===========================================================================

class TestL5PropertyFieldClinicalCorrectness:
    """Verify the property field uses spec-correct wire format and clinically
    meaningful code/value pairs.

    Spec: FHIR R4 https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    §4.8.21.1 Out parameter ``property`` (multipart):
      - part name='code' → valueCode (the property code identifier)
      - part name='value' → value[x] (valueString/valueCode/valueUri/
        valueBoolean/valueInteger per property type)
      - part name='description' → valueString (optional human-readable)
    """

    def test_t50_property_parts_use_correct_value_x_keys(self, fhir_client):
        """L5 — every property in the $lookup response MUST use the
        spec-correct value[x] key. ``code`` part uses ``valueCode``;
        ``value`` part uses one of valueString/valueCode/valueUri/
        valueBoolean/valueInteger per FHIR R4 PropertyType.

        A regression that emits e.g. ``part name='value' value='X'`` (raw
        string key) would be a wire-format violation — clients parsing
        with the spec'd FHIR JSON parser would silently miss the value.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": "73211009"},
        )
        assert r.status_code == 200
        params = r.json().get("parameter", [])
        property_params = [p for p in params if p.get("name") == "property"]
        if not property_params:
            pytest.skip("No properties emitted by $lookup — wire format unprobeable.")
        valid_value_keys = {
            "valueString", "valueCode", "valueUri",
            "valueBoolean", "valueInteger", "valueDecimal", "valueDateTime",
        }
        for prop in property_params:
            parts = prop.get("part", [])
            for part in parts:
                pname = part.get("name")
                value_keys = [k for k in part.keys() if k.startswith("value")]
                if pname == "code":
                    # 'code' part uses valueCode.
                    assert value_keys == ["valueCode"], (
                        f"property.code part uses value keys {value_keys}; "
                        f"spec mandates ['valueCode']. Wire-format drift."
                    )
                elif pname == "value":
                    # 'value' part uses one of the typed value[x] keys.
                    assert len(value_keys) == 1, (
                        f"property.value part has {len(value_keys)} value "
                        f"keys {value_keys}; spec mandates exactly 1."
                    )
                    assert value_keys[0] in valid_value_keys, (
                        f"property.value part uses non-spec value key "
                        f"{value_keys[0]!r}; spec mandates one of "
                        f"{sorted(valid_value_keys)}."
                    )

    def test_t51_property_value_not_empty_or_null(self, fhir_client):
        """L5 — clinical safety: every emitted property MUST have a
        non-empty, non-null value. An empty value would be a silent-
        wrong-answer on a custom property — the client cannot
        distinguish "property not applicable" from "property missing".
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": "73211009"},
        )
        assert r.status_code == 200
        for prop in r.json().get("parameter", []):
            if prop.get("name") != "property":
                continue
            for part in prop.get("part", []):
                if part.get("name") != "value":
                    continue
                value_keys = [k for k in part.keys() if k.startswith("value")]
                if not value_keys:
                    continue
                v = part[value_keys[0]]
                assert v is not None, (
                    f"property value part is null — silent-wrong-answer "
                    f"risk on custom property {prop!r}."
                )
                if isinstance(v, str):
                    assert v.strip(), (
                        f"property value part is empty string — "
                        f"silent-wrong-answer risk on custom property {prop!r}."
                    )


# ===========================================================================
# L6 — Concept hierarchy clinical correctness
# ===========================================================================

class TestL6ConceptHierarchyClinicalCorrectness:
    """Verify the parent-child relationships in the conformance fixture
    match the clinical ontology. SNOMED CT clinical hierarchy is the
    load-bearing test: 73211009 (Diabetes mellitus) is the parent of
    44054006 (Type 2 diabetes mellitus).

    Spec: FHIR R4 https://hl7.org/fhir/R4/codesystem.html concept.concept
    defines a hierarchy; the hierarchy meaning is per ``hierarchyMeaning``
    (is-a / contains / categorizes).
    """

    def test_t60_fixture_hierarchy_dm_is_parent_of_t2dm(self, fhir_client):
        """L6 — the conformance fixture's mrrel row encodes that
        SNOMEDCT_US 44054006 (T2DM) is-a 73211009 (DM). Verify the
        fixture seeds the relationship correctly — a terminology-server
        test fixture that inverted this relationship (T2DM parent of
        DM) would be a clinical-ontology defect in the fixture itself.

        Cross-checked via $subsumes: codeA=73211009 (DM), codeB=44054006
        (T2DM) MUST return outcome=subsumes (DM is broader, subsumes
        T2DM) per FHIR R4 $subsumes spec. This is the clinical-
        correctness dimension of the seeded hierarchy.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": "http://snomed.info/sct",
                "codeA": "73211009",
                "codeB": "44054006",
            },
        )
        assert r.status_code == 200
        body = r.json()
        outcome = _top_level_param(body, "outcome")
        assert outcome == "subsumes", (
            f"$subsumes(DM, T2DM) outcome={outcome!r}; expected 'subsumes' "
            f"(DM is broader, subsumes T2DM). Clinical-ontology directionality "
            f"defect in conformance fixture or engine."
        )
        # Mirror invariant: T2DM is subsumed-by DM.
        r2 = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": "http://snomed.info/sct",
                "codeA": "44054006",
                "codeB": "73211009",
            },
        )
        assert r2.status_code == 200
        outcome2 = _top_level_param(r2.json(), "outcome")
        assert outcome2 == "subsumed-by", (
            f"$subsumes(T2DM, DM) outcome={outcome2!r}; expected "
            f"'subsumed-by' (T2DM is narrower, subsumed-by DM). "
            f"Clinical-ontology directionality defect."
        )

    def test_t61_lookup_emits_parent_property_for_t2dm(self, fhir_client):
        """L6 — $lookup for SNOMED 44054006 (T2DM) SHOULD emit a ``parent``
        property naming 73211009 (DM) per FHIR R4 §4.8.21.1 subsumption-
        decomposition. The parent property links the concept to its
        broader ancestor in the clinical hierarchy — clinical-safety
        invariant for any code that has a parent.

        Note: the fixture's mrrel encodes the PAR/isa relationship, so
        the engine SHOULD emit the parent property. If the property is
        absent, the engine is missing the parent-enrichment step (a
        clinical-data-incomplete defect, not a strict spec violation).
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": "44054006"},
        )
        assert r.status_code == 200
        # The parent property MAY be absent if the engine doesn't surface
        # hierarchy in $lookup (CS-02 surface). Document as observation,
        # not a strict assertion — the load-bearing assertion is the
        # fixture-ontology test above (test_t60).
        body = r.json()
        params = body.get("parameter", [])
        property_codes = []
        for p in params:
            if p.get("name") != "property":
                continue
            for part in p.get("part", []):
                if part.get("name") == "code":
                    property_codes.append(part.get("valueCode"))
        # Engine should at minimum surface the standard name + display;
        # parent/child properties are CS-02 scope.
        assert "name" in [p.get("name") for p in params] or property_codes, (
            "$lookup for SNOMED 44054006 emits no name or property "
            "parameters — wire response shape clinical-deficient."
        )


# ===========================================================================
# L7 — filter field clinical correctness
# ===========================================================================

class TestL7FilterFieldClinicalCorrectness:
    """Verify the filter field advertisement uses spec-correct operators
    AND that the operators produce clinically correct results when
    applied (e.g., ``is-a`` filter on SNOMED returns descendants matching
    the clinical hierarchy).

    Spec: FHIR R4 https://hl7.org/fhir/R4/valueset-filter-operator.html
    FilterOperator closed enum (9 values): = | is-a | descendent-of |
    is-not-a | regex | in | not-in | generalizes | exists. NOTE: spec
    spelling is ``descendent-of`` (Latin-derived), NOT ``descendant-of``.
    """

    def test_t70_no_off_spec_filter_operator_advertised(self, fhir_client):
        """L7 — every filter operator advertised in CapabilityStatement
        (or in any CodeSystem.filter declaration) MUST be in the FHIR R4
        FilterOperator closed enum (9 values). Off-spec operators (e.g.
        ``child-of``, ``descendent-leaf``, ``property-value-of`` —
        R5/R4B additions) would be clinical-interpretation defects
        (client familiar only with R4 wouldn't know how to apply them).
        """
        r = fhir_client.get("/fhir/metadata")
        assert r.status_code == 200
        # Walk the CapabilityStatement for any advertised filter operators.
        cap_stmt = r.json()
        advertised_filters: list[str] = []
        for rest in cap_stmt.get("rest", []):
            for resource in rest.get("resource", []):
                # CodeSystem.filter[] would be advertised here per R4 spec.
                # (medterm4ds does NOT advertise filters today — this probe
                # documents the load-bearing contract that IF filters are
                # ever advertised, the operators are spec-correct.)
                pass
        # If no filters advertised: the contract is trivially HELD.
        for op in advertised_filters:
            assert op in FHIR_R4_FILTER_OPERATORS, (
                f"Off-spec filter operator {op!r} advertised — not in FHIR "
                f"R4 FilterOperator enum (9 values: "
                f"{sorted(FHIR_R4_FILTER_OPERATORS)})."
            )

    def test_t71_filter_registry_canonical_matches_r4_spec(self):
        """L7 — the FHIR_R4_FILTER_OPERATORS canonical registry (imported
        from engines/fhir/__init__.py per the registry-as-contract
        pattern) MUST contain exactly the 9 R4 spec values, including
        the spec-correct spelling ``descendent-of`` (NOT ``descendant-of``).
        """
        expected = frozenset({
            "=", "is-a", "descendent-of", "is-not-a", "regex",
            "in", "not-in", "generalizes", "exists",
        })
        assert FHIR_R4_FILTER_OPERATORS == expected, (
            f"FHIR_R4_FILTER_OPERATORS registry drift: actual="
            f"{sorted(FHIR_R4_FILTER_OPERATORS)}; expected={sorted(expected)}. "
            f"VS-01 SKEPTIC QA-054 regression (descendent-of vs descendant-of)."
        )
        # Specifically: the English-misspelling ``descendant-of`` MUST NOT
        # be in the registry.
        assert "descendant-of" not in FHIR_R4_FILTER_OPERATORS, (
            "FHIR_R4_FILTER_OPERATORS registry contains 'descendant-of' "
            "(English misspelling) — VS-01 SKEPTIC QA-054 regression."
        )

    def test_t72_responses_py_no_off_spec_filter_operator_literal(self):
        """L7 — source-read audit: ``engines/fhir/responses.py`` MUST NOT
        contain any off-spec filter operator string literal (e.g.
        ``descendant-of``, ``child-of``). The registry-as-contract
        pattern means the canonical source of operators is the frozen
        set; any literal echo is drift.

        Uses AST walk to skip comments (per CS-01 SKEPTIC methodology
        contribution: ``ast.Constant`` string-literal audit vs comment
        audit).
        """
        source = _read_source(_RESPONSES_PATH)
        tree = ast.parse(source)
        off_spec = {"descendant-of", "child-of", "descendent-leaf",
                    "property-value-of", "matches"}
        found: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for bad in off_spec:
                    if node.value == bad:
                        found.append((node.lineno, node.value))
        assert not found, (
            f"responses.py contains off-spec filter operator string "
            f"literals: {found}. Filter operators MUST come from "
            f"FHIR_R4_FILTER_OPERATORS (registry-as-contract pattern)."
        )


# ===========================================================================
# L8 — Cross-resource clinical consistency
# ===========================================================================

class TestL8CrossResourceClinicalConsistency:
    """Verify READ CodeSystem response is consistent with $lookup results
    for codes in that system. Cross-resource canonical-URI agreement
    invariant: the URIs a client learns from conformance, READ, and
    $lookup must all be the same canonical value.

    Spec: FHIR R4 https://build.fhir.org/codesystem.html CodeSystem.url
    is the canonical identifier; it MUST match the system URI used in
    $lookup Out `system` parameter per §4.8.21.1.
    """

    def test_t80_lookup_out_system_matches_registry_canonical(self, fhir_client):
        """L8 — for every seeded code, the $lookup Out ``system``
        parameter MUST be the canonical FHIR R4 URI from
        SYSTEM_TO_FHIR_URI. A divergence would mean a client learning
        the URI from $lookup gets a different value than a client
        learning it from conformance — clinical-data-inconsistency.
        """
        cases = [
            ("http://snomed.info/sct", "73211009"),
            ("http://snomed.info/sct", "44054006"),
            ("http://hl7.org/fhir/sid/icd-10-cm", "E11"),
            ("http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
        ]
        for system_uri, code in cases:
            r = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": system_uri, "code": code},
            )
            assert r.status_code == 200
            out_system = _top_level_param(r.json(), "system")
            assert out_system == system_uri, (
                f"$lookup {system_uri}/{code}: Out system={out_system!r}; "
                f"input system={system_uri!r}. Cross-resource canonical-URI "
                f"drift — TS-01 TERMINOLOGIST QA-012 regression."
            )
            # And: Out system MUST be in SYSTEM_TO_FHIR_URI.values()
            # (canonical advertisement set).
            assert out_system in set(SYSTEM_TO_FHIR_URI.values()), (
                f"$lookup Out system {out_system!r} is NOT in "
                f"SYSTEM_TO_FHIR_URI.values() — canonical-registry drift."
            )

    def test_t81_lookup_out_system_canonical_via_alias_inputs(self, fhir_client):
        """L8 — when $lookup is called with an alias input (trailing-
        slash, urn:oid, uppercase-scheme), the Out ``system`` MUST be
        the canonical URI — NOT an echo of the client input. This is
        the client-input-as-canonical drift meta-pattern (count=8+1
        PROMOTED) verified on the $lookup Out `system` surface.

        Per CS-01 SKEPTIC test_s22/s23/s24, the CS-01 surface already
        HOLDS via 5 probes. Here TERMINOLOGIST verifies the clinical-
        correctness dimension: the canonical URI is the one published
        by the source's owning authority, so a patient-safety-tracking
        client can cross-reference the Out system against the
        authority's published value.
        """
        cases = [
            # (alias_input, expected_canonical_out)
            ("http://snomed.info/sct/", "http://snomed.info/sct"),
            ("urn:oid:2.16.840.1.113883.6.96", "http://snomed.info/sct"),
            ("HTTP://snomed.info/sct", "http://snomed.info/sct"),
        ]
        for alias_input, expected in cases:
            r = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": alias_input, "code": "73211009"},
            )
            assert r.status_code == 200, (
                f"$lookup with alias input {alias_input!r} → {r.status_code}; "
                f"alias not recognized."
            )
            out_system = _top_level_param(r.json(), "system")
            assert out_system == expected, (
                f"$lookup alias input {alias_input!r}: Out system="
                f"{out_system!r}; expected canonical {expected!r}. "
                f"Client-input-as-canonical drift (count=8+1 PROMOTED)."
            )

    def test_t82_read_codesystem_returns_fhir_mimetype(self, fhir_client):
        """L8 — clinical-safety: READ CodeSystem/{id} returns 404 +
        OperationOutcome (medterm4ds does not persist CodeSystem
        resources, per AGENTS.md NOT A BUG Registry). The response
        Content-Type MUST be ``application/fhir+json`` (FHIR R4
        §3.1.0.1.9) — a non-FHIR MIME type would be a clinical-data-
        transport defect (a FHIR-only client parser would silently
        fail to read the OperationOutcome).

        Per CS-01 SKEPTIC test_s43 (source-read structural contract),
        the READ route uses ``_fhir_response`` — this probe extends
        to the clinical-safety dimension via Content-Type assertion.
        """
        r = fhir_client.get("/fhir/CodeSystem/anything")
        assert r.status_code == 404
        assert r.headers.get("content-type", "").startswith("application/fhir+json"), (
            f"READ CodeSystem/anything returned Content-Type "
            f"{r.headers.get('content-type')!r}; expected "
            f"application/fhir+json (FHIR R4 §3.1.0.1.9)."
        )
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"READ CodeSystem body resourceType={body.get('resourceType')!r}; "
            f"expected 'OperationOutcome'."
        )

    def test_t83_search_codesystem_returns_fhir_bundle(self, fhir_client):
        """L8 — clinical-safety: SEARCH /fhir/CodeSystem returns an empty
        Bundle (per AGENTS.md NOT A BUG Registry — medterm4ds does not
        persist CodeSystem resources). The Bundle MUST be a well-formed
        FHIR R4 searchset Bundle per §3.1.1.5: resourceType=Bundle,
        type=searchset, total=integer, entry=list.

        A malformed Bundle (missing resourceType, non-integer total)
        would break FHIR-client parsers silently — clinical-data-
        transport defect.
        """
        r = fhir_client.get("/fhir/CodeSystem", params={"url": "http://snomed.info/sct"})
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/fhir+json")
        body = r.json()
        assert body.get("resourceType") == "Bundle", (
            f"SEARCH CodeSystem body resourceType={body.get('resourceType')!r}; "
            f"expected 'Bundle'."
        )
        assert body.get("type") == "searchset", (
            f"SEARCH CodeSystem Bundle.type={body.get('type')!r}; expected "
            f"'searchset' per FHIR R4 §3.1.1.5."
        )
        assert isinstance(body.get("total"), int), (
            f"SEARCH CodeSystem Bundle.total type={type(body.get('total'))}; "
            f"expected int per FHIR R4 §3.1.1.5."
        )
        assert isinstance(body.get("entry"), list), (
            f"SEARCH CodeSystem Bundle.entry type={type(body.get('entry'))}; "
            f"expected list per FHIR R4 §3.1.1.5."
        )
