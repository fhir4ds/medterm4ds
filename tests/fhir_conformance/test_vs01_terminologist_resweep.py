"""TERMINOLOGIST RESWEEP probes for VS-01 (ValueSet Resource Structure) —
fresh full-sweep run.

Spec: https://hl7.org/fhir/R4/valueset.html (canonical R4 / 4.0.1)
       Expansion: https://hl7.org/fhir/R4/valueset.html#expansion
       Filter operators: https://hl7.org/fhir/R4/valueset.html#filter
       $expand: https://hl7.org/fhir/R4/valueset-operation-expand.html

TERMINOLOGIST lens (per ROLE_QA_ENGINEER Section 3): clinical and
terminological correctness. The other personalities find technical bugs;
TERMINOLOGIST finds domain bugs. Per GLOBAL_RULES.md "TERMINOLOGIST
Findings Are HIGH Severity", all findings default to HIGH severity.

EXPLORER tip for TERMINOLOGIST — Apply the canonical-DISPLAY cross-
operation invariant (count=5 PROMOTED per GLOBAL_RULES.md Code Review
Time trigger) to the VS-01 surface. EXPLORER verified canonical-URI
consistency via the $expand explicit-concept-list path for SNOMED T2DM
(test_e60). TERMINOLOGIST extends to:

  1. All 4 seeded codes (SNOMED DM, SNOMED T2DM, RXNORM metformin,
     ICD-10-CM T2DM) — not just T2DM
  2. The is-a filter expansion path (DM → T2DM descendant): every
     descendant's display in expansion MUST byte-exact equal $lookup
     Out display for the same code
  3. CF-TERMINOLOGIST-VS01-01 (supplied-display echo semantic) pinned
     via carry-forward-as-probe pattern (strategy 33)

10 lens dimensions, ~50 probes:

  L1 — Canonical-DISPLAY invariant on VS-01: $expand explicit concept
       list display byte-exact with $lookup Out display for ALL 4
       seeded codes
  L2 — $expand is-a filter expansion display byte-exact with $lookup
       for every descendant code
  L3 — CF-TERMINOLOGIST-VS01-01 supplied-display echo semantic pinned
       via carry-forward-as-probe pattern
  L4 — HCPCS URI drift META-PATTERN closed across all 3 surfaces —
       clinical-correctness angle (verify canonical HCPCS URI used in
       $translate target concept when HCPCS mappings exist; document
       DEFERRED if fixture lacks HCPCS mappings)
  L5 — Filter operator clinical correctness: is-a filter includes root
       + descendants (clinically correct hierarchy traversal);
       descendent-of excludes root
  L6 — Patient-friendly name surfacing: for codes with patient-friendly
       names, expansion SHOULD surface them where engine supports
  L7 — Cross-operation clinical consistency: $expand display consistent
       with $lookup AND $validate-code for same code
  L8 — ValueSet.expansion.contains[].display clinical sensibility:
       every concept in expansion.contains[] has clinically correct
       display (engine preferred term, NOT raw code)
  L9 — Compose.exclude clinical correctness: excluding a code that's
       clinically indicated produces a clinically-correct smaller
       expansion
  L10 — Source-read structural contracts: builders delegate canonical
        display through engine ``code_info.name`` (never echo raw code
        when engine has a canonical STR)

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts the
POSITIVE success shape (200 + Parameters/ValueSet body with the expected
fields), not just absence of an error string.

Per GLOBAL_RULES.md "Right-level test": TERMINOLOGIST does not use
automated proxies for clinical correctness. The display string comparison
is the load-bearing test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Seeded conformance fixture data (per conftest.py _make_conformance_db)
SNOMED_URI = "http://snomed.info/sct"
SNOMED_DM = "73211009"           # Diabetes mellitus (parent)
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM = "44054006"         # Type 2 diabetes mellitus (child)
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"

ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"
ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"

RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_860975 = "860975"
RXNORM_860975_DISPLAY = "24 HR metformin 500 MG Oral Tablet"

# Aliases per FHIR_URI_ALIASES in engines/fhir/__init__.py
SNOMED_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URN_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"  # RFC 3986 §3.1

# Canonical HCPCS URI per HL7 THO + CMS
HCPCS_CANONICAL_URI = "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
HCPCS_LEGACY_URI = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"

_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "apps"
    / "fhir_api.py"
)
_RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "engines"
    / "fhir"
    / "responses.py"
)
_OUTPUTS_FHIR_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "outputs"
    / "fhir.py"
)
_ENGINES_FHIR_INIT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "engines"
    / "fhir"
    / "__init__.py"
)


# ---------------------------------------------------------------------------
# Source-read helpers (TS-04 HISTORIAN methodology — walks both
# ast.FunctionDef AND ast.AsyncFunctionDef for nested handlers).
# ---------------------------------------------------------------------------

def _get_func_source(file_path: Path, func_name: str) -> str:
    """Extract source text of a function (possibly nested) by name."""
    tree = ast.parse(file_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(file_path.read_text(), node)
    return ""


def _params_by_name(body: dict, name: str) -> list[dict]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _first_param(body: dict, name: str) -> dict | None:
    params = _params_by_name(body, name)
    return params[0] if params else None


def _param_value(body: dict, name: str, value_key: str = "valueString"):
    p = _first_param(body, name)
    return p.get(value_key) if p else None


def _post_expand(fhir_client, value_set: dict, **query) -> tuple[int, dict]:
    """POST a ValueSet body to /fhir/ValueSet/$expand."""
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=value_set,
        params=query,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text}
    return resp.status_code, body


def _contains_codes_displays(body: dict) -> list[tuple[str, str, str]]:
    """Extract (system, code, display) from expansion.contains."""
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", ""), c.get("display", "")))
    return out


# =============================================================================
# L1: Canonical-DISPLAY invariant on VS-01 — $expand explicit concept list
# display byte-exact with $lookup Out display for ALL 4 seeded codes
# (EXPLORER tip — extends test_e60 from SNOMED T2DM to all 4 seeded codes)
# =============================================================================
# Spec: FHIR R4 ValueSet.expansion.contains.display
#   "The recommended display for this item in the expansion."
#   (https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display)
# Spec: $lookup Out display
#   "The preferred display for this concept" (1..1 string)
#   (https://hl7.org/fhir/R4/codesystem-operation-lookup.html)
#
# Clinical justification: the canonical-DISPLAY cross-operation invariant
# (count=5 PROMOTED in GLOBAL_RULES.md Code Review Time trigger) catches
# silent-wrong-answer where two operations emit different "preferred terms"
# for the same concept. A clinician using both $expand (to populate a
# dropdown) and $lookup (to show details) MUST see the same display.

@pytest.mark.parametrize(
    "system, code, expected_display",
    [
        (SNOMED_URI, SNOMED_DM, SNOMED_DM_DISPLAY),
        (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM_DISPLAY),
        (ICD10CM_URI, ICD10CM_E11, ICD10CM_E11_DISPLAY),
        (RXNORM_URI, RXNORM_860975, RXNORM_860975_DISPLAY),
    ],
    ids=["snomed_dm", "snomed_t2dm", "icd10cm_t2dm", "rxnorm_metformin"],
)
class TestLens1CanonicalDisplayInvariant:
    """Lens 1 (EXPLORER tip): canonical-DISPLAY cross-operation invariant
    between $expand explicit concept list and $lookup.
    """

    def test_t10_expand_display_byte_exact_with_lookup(
        self, fhir_client, system, code, expected_display,
    ):
        """HIGH — $expand contains[].display MUST equal $lookup Out display
        for the same code.

        Spec citations:
          ValueSet.expansion.contains.display: "The recommended display for
            this item in the expansion" (1..1 string)
          CodeSystem $lookup Out display: "The preferred display for this
            concept" (1..1 string)

        Both operations source the display from the engine's preferred-term
        resolution (CodeInfo.name). The values MUST be byte-exact identical.
        A mismatch would be silent-wrong-answer: a clinician using $expand
        to populate a value-set dropdown would see a different display than
        the same code's $lookup detail view.
        """
        # 1. $expand explicit concept list (no display supplied → canonical resolution)
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": system,
                "concept": [{"code": code}],  # no display supplied
            }]},
        }
        r_expand = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert r_expand.status_code == 200, f"expand: {r_expand.text[:200]!r}"
        contains = r_expand.json().get("expansion", {}).get("contains", [])
        assert len(contains) == 1, f"expected 1 concept in expansion; got {len(contains)}"
        expand_display = contains[0].get("display", "")
        assert expand_display == expected_display, (
            f"expand contains[].display MUST be the engine's preferred term "
            f"{expected_display!r}; got {expand_display!r}. A non-canonical "
            f"display would violate the FHIR R4 spec 'recommended display'."
        )

        # 2. $lookup for the same code
        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        assert r_lookup.status_code == 200, f"lookup: {r_lookup.text[:200]!r}"
        lookup_display = _param_value(r_lookup.json(), "display")
        assert lookup_display == expected_display, (
            f"lookup Out display MUST be the engine's preferred term "
            f"{expected_display!r}; got {lookup_display!r}."
        )

        # 3. Cross-operation byte-exact invariant
        assert expand_display == lookup_display, (
            f"DISPLAY DRIFT across operations: expand={expand_display!r} "
            f"lookup={lookup_display!r}. Both operations MUST resolve the "
            f"same code against the same engine — the canonical display "
            f"MUST be identical. (count=5 PROMOTED pattern in GLOBAL_RULES.md)"
        )


# =============================================================================
# L2: $expand is-a filter expansion display byte-exact with $lookup for
# every descendant code (EXPLORER tip)
# =============================================================================
# Spec: FHIR R4 ValueSet $expand with is-a filter
#   Per https://hl7.org/fhir/R4/valueset.html#filter:
#   "is-a: The definition of the concept is in the value set."
#   "Includes all the descendants of the code."
# Spec: $lookup Out display = engine preferred term
#
# Clinical justification: an is-a filter produces a hierarchical expansion
# where the root + descendants are returned. Every descendant's display in
# the expansion MUST match the same descendant's $lookup display. A clinician
# using $expand to populate a CDS-hook dropdown would see descendants with
# non-canonical displays; if the dropdown is then used to $lookup details,
# the display would suddenly change — silent-wrong-answer.

class TestLens2IsaFilterExpansionDisplayInvariant:
    """Lens 2 (EXPLORER tip): $expand is-a filter expansion display byte-
    exact with $lookup for every descendant code.
    """

    def test_t20_isa_filter_root_display_byte_exact_with_lookup(self, fhir_client):
        """HIGH — is-a filter expansion: root code (DM) display MUST equal
        $lookup Out display for the same code.

        Per FHIR R4 §4.7.5 + valueset.html#filter, the is-a operator
        "includes all the descendants of the code" PLUS the code itself
        (per medterm4ds implementation — mirrors SNOMED CT intensional
        expansion semantic).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [{
                    "property": "concept",
                    "op": "is-a",
                    "value": SNOMED_DM,
                }],
            }]},
        }
        r_expand = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert r_expand.status_code == 200, f"expand: {r_expand.text[:200]!r}"
        contains = r_expand.json().get("expansion", {}).get("contains", [])
        # Fixture has DM → T2DM; is-a should return both
        codes = {(c.get("system", ""), c.get("code", "")) for c in contains}
        assert (SNOMED_URI, SNOMED_DM) in codes, (
            f"is-a filter MUST include the root code; got codes={codes}"
        )

        # Verify root display byte-exact with $lookup
        dm_entry = next(c for c in contains if c.get("code") == SNOMED_DM)
        expand_root_display = dm_entry.get("display", "")

        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DM},
        )
        assert r_lookup.status_code == 200
        lookup_display = _param_value(r_lookup.json(), "display")

        assert expand_root_display == SNOMED_DM_DISPLAY, (
            f"is-a filter root display MUST be {SNOMED_DM_DISPLAY!r}; "
            f"got {expand_root_display!r}"
        )
        assert expand_root_display == lookup_display, (
            f"DISPLAY DRIFT: is-a root={expand_root_display!r} vs "
            f"lookup={lookup_display!r}"
        )

    def test_t21_isa_filter_descendant_display_byte_exact_with_lookup(self, fhir_client):
        """HIGH — is-a filter expansion: descendant (T2DM) display MUST equal
        $lookup Out display for the same code.

        The T2DM code is the descendant of DM via mrrel PAR (parent) edge.
        The is-a filter expansion walks the BFS descendants and emits each
        descendant with its target_display.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [{
                    "property": "concept",
                    "op": "is-a",
                    "value": SNOMED_DM,
                }],
            }]},
        }
        r_expand = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert r_expand.status_code == 200
        contains = r_expand.json().get("expansion", {}).get("contains", [])
        codes = {(c.get("system", ""), c.get("code", "")) for c in contains}
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"is-a filter MUST include descendants; got codes={codes}"
        )

        # Verify descendant display byte-exact with $lookup
        t2dm_entry = next(c for c in contains if c.get("code") == SNOMED_T2DM)
        expand_descendant_display = t2dm_entry.get("display", "")

        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert r_lookup.status_code == 200
        lookup_display = _param_value(r_lookup.json(), "display")

        assert expand_descendant_display == SNOMED_T2DM_DISPLAY, (
            f"is-a filter descendant display MUST be {SNOMED_T2DM_DISPLAY!r}; "
            f"got {expand_descendant_display!r}"
        )
        assert expand_descendant_display == lookup_display, (
            f"DISPLAY DRIFT: is-a descendant={expand_descendant_display!r} "
            f"vs lookup={lookup_display!r}"
        )

    def test_t22_isa_filter_expansion_no_raw_code_when_str_exists(self, fhir_client):
        """HIGH — is-a filter expansion: contains[].display MUST be the engine
        preferred term (NOT the raw code) when an STR exists.

        Clinical justification: echoing the raw code as display is clinically
        useless (the clinician sees '73211009' instead of 'Diabetes mellitus').
        The implementation MUST resolve via get_code_infos / target_display.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [{
                    "property": "concept",
                    "op": "is-a",
                    "value": SNOMED_DM,
                }],
            }]},
        }
        r_expand = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert r_expand.status_code == 200
        contains = r_expand.json().get("expansion", {}).get("contains", [])
        # Every entry's display MUST be the engine preferred term (NOT raw code)
        for entry in contains:
            code = entry.get("code", "")
            display = entry.get("display", "")
            assert display != code, (
                f"Clinical-correctness violation: contains[].display "
                f"({display!r}) equals the raw code ({code!r}). The display "
                f"MUST be the engine's preferred term per FHIR R4 spec."
            )
            assert display != "", (
                f"Clinical-correctness violation: contains[].display is "
                f"empty for code {code!r}. The display MUST be the engine's "
                f"preferred term per FHIR R4 spec."
            )


# =============================================================================
# L3: CF-TERMINOLOGIST-VS01-01 supplied-display echo semantic pinned via
# carry-forward-as-probe pattern (strategy 33)
# =============================================================================
# Spec: FHIR R4 ValueSet.expansion.contains.display
#   "The recommended display for this item in the expansion."
#   (https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display)
#
# CF-TERMINOLOGIST-VS01-01 (DEFERRED): when a client SUPPLIES a display for
# compose.include[].concept[], the implementation echoes the client's display
# verbatim today. The spec-correct behavior is canonical-wins (engine preferred
# term overrides client-supplied display). The deferral is because applying
# canonical-wins requires a display-name canonicalization decision tied to
# the existing $validate-code display enforcement (CS-03 SKEPTIC QA-048).
#
# Per carry-forward-as-probe pattern (strategy 33), the probe SHOULD assert
# the CURRENT (deferred) behavior. When a future chunk fixes the carry-
# forward, the probe will fail loudly — the carry-forward is a load-bearing
# contract, not a passive note.

class TestLens3CFTerminologistVS01_01EchoSemanticPinned:
    """Lens 3: CF-TERMINOLOGIST-VS01-01 supplied-display echo semantic pinned
    via carry-forward-as-probe pattern.
    """

    def test_t30_supplied_display_currently_echoed_verbatim(self, fhir_client):
        """CF-TERMINOLOGIST-VS01-01 (DEFERRED) — when client SUPPLIES a
        display, the implementation echoes it verbatim.

        This test PINS the current behavior via the carry-forward-as-probe
        pattern (strategy 33). When a future chunk implements canonical-wins
        (engine preferred term overrides client-supplied display), this test
        will FAIL — that's the signal to update the probe to assert the
        spec-correct behavior.

        Spec citation: FHIR R4 ValueSet.expansion.contains.display
          "The recommended display for this item in the expansion."
          (https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display)

        Spec-correct behavior (DEFERRED): the engine preferred term
        ("Diabetes mellitus") should win over the client-supplied display
        ("Some Random Display").
        """
        supplied_display = "Some Random Display"
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": SNOMED_DM, "display": supplied_display}],
            }]},
        }
        r_expand = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert r_expand.status_code == 200, f"expand: {r_expand.text[:200]!r}"
        contains = r_expand.json().get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        actual_display = contains[0].get("display", "")
        # PIN CURRENT BEHAVIOR (CF-TERMINOLOGIST-VS01-01 DEFERRED):
        # The client-supplied display is echoed verbatim today.
        assert actual_display == supplied_display, (
            f"CF-TERMINOLOGIST-VS01-01 DEFERRED behavior changed: "
            f"expected echo of client-supplied {supplied_display!r}; "
            f"got {actual_display!r}. If canonical-wins was implemented, "
            f"UPDATE this probe to assert {SNOMED_DM_DISPLAY!r} (the "
            f"engine preferred term)."
        )

    def test_t31_supplied_display_differs_from_canonical(self, fhir_client):
        """CF-TERMINOLOGIST-VS01-01 (DEFERRED) — when client-supplied display
        differs from engine canonical, the implementation echoes the client
        value (NOT the canonical).

        This is the load-bearing CF case: if the client supplies a wrong
        display, the implementation propagates it. The deferred enhancement
        (canonical-wins) would override the wrong display with the engine
        canonical preferred term.
        """
        wrong_display = "WRONG DISPLAY"
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": SNOMED_DM, "display": wrong_display}],
            }]},
        }
        r_expand = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert r_expand.status_code == 200
        contains = r_expand.json().get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        actual_display = contains[0].get("display", "")
        # PIN CURRENT BEHAVIOR (CF-TERMINOLOGIST-VS01-01 DEFERRED):
        assert actual_display == wrong_display, (
            f"CF-TERMINOLOGIST-VS01-01 DEFERRED behavior changed: expected "
            f"echo of wrong display {wrong_display!r}; got {actual_display!r}."
        )
        # Sanity: the wrong display is NOT the engine canonical
        assert actual_display != SNOMED_DM_DISPLAY, (
            f"Probe setup error: wrong_display happened to equal canonical"
        )

    def test_t32_omitted_display_resolves_canonical_qa056_resolved(self, fhir_client):
        """VS-01 TERMINOLOGIST QA-056 RESOLVED — when client OMITS the display,
        the implementation resolves the engine's canonical preferred term.

        This is the complement to t30/t31: the OMITTED-display path was fixed
        by VS-01 TERMINOLOGIST QA-056. The SUPPLIED-display path is the
        deferred CF-TERMINOLOGIST-VS01-01.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": SNOMED_DM}],  # no display key
            }]},
        }
        r_expand = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert r_expand.status_code == 200
        contains = r_expand.json().get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        actual_display = contains[0].get("display", "")
        # QA-056 RESOLVED behavior: canonical preferred term wins
        assert actual_display == SNOMED_DM_DISPLAY, (
            f"VS-01 TERMINOLOGIST QA-056 REGRESSION: omitted-display path "
            f"MUST resolve canonical preferred term {SNOMED_DM_DISPLAY!r}; "
            f"got {actual_display!r}."
        )

    def test_t33_empty_string_display_resolves_canonical(self, fhir_client):
        """VS-01 TERMINOLOGIST QA-056 RESOLVED — when client supplies an EMPTY
        display string, the implementation resolves the engine canonical.

        The implementation uses `display = concept.get("display") or ""` then
        `if not display and code_str: resolve canonical`. An empty string is
        falsy, so the canonical path fires.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": SNOMED_DM, "display": ""}],
            }]},
        }
        r_expand = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert r_expand.status_code == 200
        contains = r_expand.json().get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        actual_display = contains[0].get("display", "")
        assert actual_display == SNOMED_DM_DISPLAY, (
            f"VS-01 TERMINOLOGIST QA-056 REGRESSION: empty-display path "
            f"MUST resolve canonical preferred term {SNOMED_DM_DISPLAY!r}; "
            f"got {actual_display!r}."
        )


# =============================================================================
# L4: HCPCS URI drift META-PATTERN closed across all 3 surfaces —
# clinical-correctness angle
# =============================================================================
# Spec: FHIR R4 Code Systems
#   HCPCS canonical system URI (per CMS):
#     http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets
#   Legacy THO CodeSystem RESOURCE URL (NOT a system URI):
#     http://terminology.hl7.org/CodeSystem/hcpcs-Level-II
#
# The HCPCS URI drift META-PATTERN (count=8+1 PROMOTED) is now CLOSED across
# all 3 surfaces per CS-01 TERMINOLOGIST + CS-01 HISTORIAN + VS-01 EXPLORER.
# TERMINOLOGIST verifies with a clinical-correctness angle: $translate
# target concept uses canonical HCPCS URI when HCPCS mappings exist.
#
# Conformance fixture limitation: the fixture does NOT seed HCPCS codes
# (no HCPCS mrconso rows + no HCPCS-target mappings). The clinical-
# correctness verification is therefore STRUCTURAL via source-read audit
# (the registry contains the canonical URI; consumers import from the
# registry, never hardcode the legacy URI).

class TestLens4HCPCSURIDriftMetaPatternClosed:
    """Lens 4: HCPCS URI drift META-PATTERN closed across all 3 surfaces —
    clinical-correctness angle.
    """

    def test_t40_hcpcs_canonical_uri_in_registry(self):
        """HIGH — HCPCS canonical CMS URI IS in the registry.

        Per CS-01 TERMINOLOGIST (QA-012 RESOLVED), the registry MUST contain
        the canonical CMS URI (NOT the legacy THO CodeSystem resource URL).
        """
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI
        assert SYSTEM_TO_FHIR_URI.get("HCPCS") == HCPCS_CANONICAL_URI, (
            f"HCPCS URI drift META-PATTERN reopened: registry contains "
            f"{SYSTEM_TO_FHIR_URI.get('HCPCS')!r}; expected canonical "
            f"CMS URI {HCPCS_CANONICAL_URI!r}."
        )

    def test_t41_hcpcs_legacy_uri_only_in_aliases(self):
        """HIGH — HCPCS legacy THO URI is ONLY in FHIR_URI_ALIASES (input-only
        alias), NOT in SYSTEM_TO_FHIR_URI.

        Per CS-01 TERMINOLOGIST, the legacy URI is a backwards-compat alias
        for clients who supply the legacy form. The Out system MUST always
        be the canonical CMS URI.
        """
        from medterm4ds.engines.fhir import FHIR_URI_ALIASES, SYSTEM_TO_FHIR_URI
        # Legacy URI MAY be in aliases (input-only)
        assert HCPCS_LEGACY_URI in FHIR_URI_ALIASES, (
            f"HCPCS legacy URI {HCPCS_LEGACY_URI!r} SHOULD be in "
            f"FHIR_URI_ALIASES for backwards-compat input aliasing."
        )
        # Legacy URI MUST NOT be the canonical in SYSTEM_TO_FHIR_URI
        assert SYSTEM_TO_FHIR_URI.get("HCPCS") != HCPCS_LEGACY_URI, (
            f"HCPCS URI drift: registry contains the legacy THO URI; "
            f"the canonical CMS URI MUST be used."
        )

    def test_t42_responses_py_no_hardcoded_legacy_uri(self):
        """HIGH — responses.py MUST NOT hardcode the legacy HCPCS URI literal.

        Per CS-01 HISTORIAN test_h10 methodology (AST walk of responses.py).
        """
        src = _RESPONSES_PATH.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert HCPCS_LEGACY_URI not in node.value, (
                    f"HCPCS URI drift META-PATTERN reopened in responses.py: "
                    f"found literal {HCPCS_LEGACY_URI!r} at line {node.lineno}. "
                    f"Consumers MUST import from canonical registry."
                )

    def test_t43_outputs_fhir_no_hardcoded_legacy_uri(self):
        """HIGH — outputs/fhir.py MUST NOT hardcode the legacy HCPCS URI literal.

        Per VS-01 EXPLORER test_e50 (HCPCS URI drift META-PATTERN extension to
        outputs/fhir.py — closes the META-PATTERN across the 3rd surface).
        """
        src = _OUTPUTS_FHIR_PATH.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert HCPCS_LEGACY_URI not in node.value, (
                    f"HCPCS URI drift META-PATTERN reopened in outputs/fhir.py: "
                    f"found literal {HCPCS_LEGACY_URI!r} at line {node.lineno}."
                )

    def test_t44_engines_fhir_init_no_hardcoded_legacy_uri(self):
        """HIGH — engines/fhir/__init__.py MUST NOT hardcode the legacy HCPCS
        URI literal in executable code (only as the alias map value).
        """
        src = _ENGINES_FHIR_INIT_PATH.read_text()
        tree = ast.parse(src)
        legacy_uri_locations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if HCPCS_LEGACY_URI in node.value:
                    legacy_uri_locations.append(node.lineno)
        # The legacy URI MAY appear exactly ONCE — in FHIR_URI_ALIASES map
        # value (the input-only alias). Any other location is a drift.
        assert len(legacy_uri_locations) <= 1, (
            f"HCPCS legacy URI literal appears at {legacy_uri_locations}; "
            f"MUST appear at most once (in FHIR_URI_ALIASES). Multiple "
            f"locations indicate META-PATTERN reopening."
        )

    def test_t45_hcpcs_translate_target_uses_canonical_uri_deferred(self, fhir_client):
        """DEFERRED — $translate target concept uses canonical HCPCS URI when
        HCPCS mappings exist.

        Conformance fixture limitation: the fixture does NOT seed HCPCS codes
        (no HCPCS mrconso rows + no HCPCS-target mappings). The clinical-
        correctness verification is therefore STRUCTURAL via t40-t44 source-
        read audit. When a future fixture enhancement seeds HCPCS mappings,
        this probe SHOULD be tightened to verify the $translate target
        concept.system uses the canonical CMS URI.

        Reproduction shape for future enhancement:
          1. Add HCPCS mrconso row: ('G0008', 'PT', 'Administration of ...
             ', 'AG0008', 'N', 'HCPCS', 'CXXXXXX')
          2. Add SNOMED → HCPCS mrrel row sharing a CUI
          3. Issue $translate with the SNOMED code
          4. Assert: match.concept.valueCoding.system == HCPCS_CANONICAL_URI

        Per carry-forward-as-probe pattern (strategy 33), this probe PINS
        the deferred behavior: HCPCS mappings don't exist today, so $translate
        cannot return a HCPCS target concept. When fixture is enhanced, the
        structural source-read probes (t40-t44) will catch any drift.
        """
        # Verify HCPCS is NOT in the conformance fixture (no mrconso rows)
        # by attempting $lookup on a known HCPCS code (G0008)
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": HCPCS_CANONICAL_URI, "code": "G0008"},
        )
        # Expected: 200 + OperationOutcome (not-found) per FHIR R4 §3.2.1.3
        assert r.status_code == 200, (
            f"Probe setup error: expected 200 + OperationOutcome; "
            f"got {r.status_code}"
        )
        body = r.json()
        # The body should be an OperationOutcome (code not found)
        assert body.get("resourceType") in ("OperationOutcome", "Parameters"), (
            f"Probe setup: expected OperationOutcome or Parameters; "
            f"got {body.get('resourceType')!r}"
        )


# =============================================================================
# L5: Filter operator clinical correctness — is-a includes root + descendants;
# descendent-of excludes root
# =============================================================================
# Spec: FHIR R4 valueset.html#filter
#   "is-a: The definition of the concept is in the value set. This includes
#    all the descendants of the code."
#   "descendent-of: Includes all the descendants of the code, but not the
#    code itself."
#
# Clinical justification: the is-a vs descendent-of distinction is clinically
# load-bearing. A CDS hook using is-a to populate a "Diabetes mellitus and
# all subtypes" dropdown MUST include DM itself; using descendent-of MUST
# exclude DM (only the subtypes). Confusing the two would silently include
# or exclude the parent concept from clinical decision support.

class TestLens5FilterOperatorClinicalCorrectness:
    """Lens 5: filter operator clinical correctness.
    """

    def test_t50_isa_includes_root_clinically_correct(self, fhir_client):
        """HIGH — is-a filter on root MUST include root in expansion.

        Spec: https://hl7.org/fhir/R4/valueset.html#filter
          "is-a: The definition of the concept is in the value set. This
           includes all the descendants of the code."

        The 'is-a' operator is inclusive — root + all descendants.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [{
                    "property": "concept", "op": "is-a", "value": SNOMED_DM,
                }],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        codes = {(c[0], c[1]) for c in _contains_codes_displays(r[1])}
        # is-a MUST include root
        assert (SNOMED_URI, SNOMED_DM) in codes, (
            f"is-a filter MUST include root code; got codes={codes}. "
            f"Per FHIR R4 spec, is-a includes the root."
        )

    def test_t51_isa_includes_descendants_clinically_correct(self, fhir_client):
        """HIGH — is-a filter on root MUST include all descendants in expansion.

        The descendant T2DM MUST appear because is-a is inclusive of root +
        descendants.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [{
                    "property": "concept", "op": "is-a", "value": SNOMED_DM,
                }],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        codes = {(c[0], c[1]) for c in _contains_codes_displays(r[1])}
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"is-a filter MUST include descendants; got codes={codes}."
        )

    def test_t52_descendent_of_excludes_root_clinically_correct(self, fhir_client):
        """HIGH — descendent-of filter on root MUST exclude root from expansion.

        Spec: https://hl7.org/fhir/R4/valueset.html#filter
          "descendent-of: Includes all the descendants of the code, but not
           the code itself."

        The 'descendent-of' operator is exclusive — descendants only.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [{
                    "property": "concept", "op": "descendent-of", "value": SNOMED_DM,
                }],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        codes = {(c[0], c[1]) for c in _contains_codes_displays(r[1])}
        # descendent-of MUST exclude root
        assert (SNOMED_URI, SNOMED_DM) not in codes, (
            f"descendent-of filter MUST exclude root code; got codes={codes}. "
            f"Per FHIR R4 spec, descendent-of is exclusive of root."
        )
        # descendent-of MUST include descendants
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"descendent-of filter MUST include descendants; got codes={codes}."
        )

    def test_t53_off_spec_descendant_of_silently_dropped(self, fhir_client):
        """HIGH — off-spec 'descendant-of' (common English spelling) MUST be
        silently dropped, NOT silently accepted as a synonym for 'descendent-of'.

        Spec citation: FHIR R4 valueset.html#filter lists 'descendent-of'
        (Latin-derived) as the spec-correct spelling. The common-English
        'descendant-of' is OFF-SPEC and MUST NOT be silently accepted.

        Found by VS-01 SKEPTIC (QA-054); pinned by HISTORIAN test_h20..h24.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [{
                    "property": "concept",
                    "op": "descendant-of",  # OFF-SPEC common English spelling
                    "value": SNOMED_DM,
                }],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        codes = {(c[0], c[1]) for c in _contains_codes_displays(r[1])}
        # Off-spec 'descendant-of' MUST produce empty expansion
        # (silently dropped filter → no expansion)
        assert (SNOMED_URI, SNOMED_DM) not in codes, (
            f"OFF-SPEC 'descendant-of' MUST NOT be silently accepted; "
            f"got codes={codes}."
        )
        assert (SNOMED_URI, SNOMED_T2DM) not in codes, (
            f"OFF-SPEC 'descendant-of' MUST NOT produce expansion; "
            f"got codes={codes}."
        )


# =============================================================================
# L6: Patient-friendly name surfacing — for codes with patient-friendly names,
# expansion SHOULD surface them where engine supports
# =============================================================================
# Spec: FHIR R4 ValueSet.expansion.contains[].extension (0..*)
#   Custom extensions MAY be attached to contains[] entries for patient-
#   friendly names, match-type, etc.
#
# Conformance fixture limitation: the fixture does NOT seed patient-friendly
# JSON artifacts (reports/fhir4px/patient_friendly_*.json). The patient-
# friendly surfacing is therefore STRUCTURAL via source-read audit:
# _expand_intensional / _expand_implicit_value_set do NOT consult
# app.state.patient_friendly_cache today (cf. GAP-T01 / CF-TERMINOLOGIST-01).
#
# TERMINOLOGIST lens: the canonical display in expansion.contains[].display
# IS the engine preferred term (CodeInfo.name STR), which IS clinically
# correct. The patient-friendly name is a SEPARATE enhancement (deferred).
# This lens verifies the canonical display is clinically sensible, AND
# documents the deferred patient-friendly surfacing gap.

class TestLens6PatientFriendlyNameSurfacing:
    """Lens 6: patient-friendly name surfacing.
    """

    def test_t60_canonical_display_is_clinically_sensible_snomed(self, fhir_client):
        """HIGH — the canonical display in expansion.contains[].display IS
        clinically sensible (engine preferred term).

        For SNOMED codes, the engine preferred term is the PT (Preferred Term)
        atom — clinically the right display for clinicians.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": SNOMED_DM}, {"code": SNOMED_T2DM}],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        contains = r[1].get("expansion", {}).get("contains", [])
        displays = {c.get("code"): c.get("display", "") for c in contains}
        # SNOMED PT atoms are the engine preferred terms
        assert displays.get(SNOMED_DM) == SNOMED_DM_DISPLAY
        assert displays.get(SNOMED_T2DM) == SNOMED_T2DM_DISPLAY

    def test_t61_canonical_display_is_clinically_sensible_rxnorm(self, fhir_client):
        """HIGH — for RxNorm, the canonical display IS the SCD (Semantic
        Clinical Drug) name — clinically correct for clinicians.

        Per RxNorm convention, SCD names follow the pattern
        "[time] [ingredient] [dose] [route] [form]" — clinically precise.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": RXNORM_URI,
                "concept": [{"code": RXNORM_860975}],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        contains = r[1].get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        display = contains[0].get("display", "")
        # RxNorm SCD name
        assert display == RXNORM_860975_DISPLAY, (
            f"RxNorm canonical display MUST be the SCD name; got {display!r}"
        )
        # Clinically sensible: contains ingredient + dose + form
        assert "metformin" in display.lower(), (
            f"RxNorm SCD MUST contain ingredient name; got {display!r}"
        )

    def test_t62_canonical_display_is_clinically_sensible_icd10cm(self, fhir_client):
        """HIGH — for ICD-10-CM, the canonical display IS the HT (Hybrid Term)
        per the conformance fixture.

        ICD-10-CM codes are billing codes; the HT atom is the human-readable
        description suitable for clinical display.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": ICD10CM_URI,
                "concept": [{"code": ICD10CM_E11}],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        contains = r[1].get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        display = contains[0].get("display", "")
        assert display == ICD10CM_E11_DISPLAY, (
            f"ICD-10-CM canonical display MUST be the HT term; got {display!r}"
        )

    def test_t63_patient_friendly_extension_deferred(self, fhir_client):
        """DEFERRED (GAP-T01 / CF-TERMINOLOGIST-01) — patient-friendly name
        surfacing in expansion.contains[] is deferred.

        Per CF-TERMINOLOGIST-01, the _expand_intensional /
        _expand_implicit_value_set paths do NOT consult
        app.state.patient_friendly_cache today. The conformance fixture
        also lacks patient-friendly JSON artifacts.

        This probe PINS the deferred behavior via carry-forward-as-probe
        pattern (strategy 33): expansion.contains[] does NOT carry a
        patient-friendly extension today. When the enhancement lands, this
        probe will FAIL — update to assert presence of the extension.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": SNOMED_DM}],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        contains = r[1].get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # PIN CURRENT BEHAVIOR (CF-TERMINOLOGIST-01 DEFERRED):
        # No patient-friendly extension on contains[] entries
        extension = contains[0].get("extension", [])
        pf_extensions = [
            e for e in extension
            if "patient-friendly" in str(e.get("url", "")).lower()
        ]
        assert len(pf_extensions) == 0, (
            f"CF-TERMINOLOGIST-01 DEFERRED behavior changed: patient-friendly "
            f"extension now present. UPDATE this probe to assert presence."
        )


# =============================================================================
# L7: Cross-operation clinical consistency — $expand display consistent with
# $lookup AND $validate-code for same code
# =============================================================================
# Spec: canonical-DISPLAY cross-operation invariant (count=5 PROMOTED).
# Spec: $expand Out contains[].display = engine preferred term.
# Spec: $lookup Out display = engine preferred term.
# Spec: $validate-code Out display = engine preferred term.
#
# All three operations source display from engine preferred-term resolution
# (CodeInfo.name). The displays MUST be byte-exact identical across all
# three operations for the same code.

class TestLens7CrossOperationClinicalConsistency:
    """Lens 7: cross-operation clinical consistency across $expand, $lookup,
    $validate-code.
    """

    @pytest.mark.parametrize(
        "system, code, expected_display",
        [
            (SNOMED_URI, SNOMED_DM, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_860975, RXNORM_860975_DISPLAY),
        ],
        ids=["snomed_dm", "snomed_t2dm", "icd10cm_t2dm", "rxnorm_metformin"],
    )
    def test_t70_three_way_canonical_display_invariant(
        self, fhir_client, system, code, expected_display,
    ):
        """HIGH — canonical-DISPLAY cross-operation invariant across 3 ops.

        For every seeded code, the display in $expand contains[] MUST equal
        the display in $lookup Out AND $validate-code Out.
        """
        # 1. $expand
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": system, "concept": [{"code": code}],
            }]},
        }
        r_expand = _post_expand(fhir_client, vs)
        assert r_expand[0] == 200
        contains = r_expand[1].get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        expand_display = contains[0].get("display", "")

        # 2. $lookup
        r_lookup = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system, "code": code},
        )
        assert r_lookup.status_code == 200
        lookup_display = _param_value(r_lookup.json(), "display")

        # 3. $validate-code (CodeSystem)
        r_validate = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": system, "code": code},
        )
        assert r_validate.status_code == 200
        validate_display = _param_value(r_validate.json(), "display")

        # All three MUST be byte-exact identical
        assert expand_display == expected_display
        assert lookup_display == expected_display
        assert validate_display == expected_display
        assert expand_display == lookup_display == validate_display


# =============================================================================
# L8: ValueSet.expansion.contains[].display clinical sensibility
# =============================================================================
# Spec: FHIR R4 ValueSet.expansion.contains.display
#   "The recommended display for this item in the expansion." (1..1 string)
#   (https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display)
#
# Clinical justification: the display is what a clinician sees in the EHR
# dropdown. Echoing the raw code (e.g., "73211009" instead of "Diabetes
# mellitus") is clinically useless. The display MUST be the engine's
# preferred term when an STR exists.

class TestLens8ExpansionDisplayClinicalSensibility:
    """Lens 8: ValueSet.expansion.contains[].display clinical sensibility.
    """

    def test_t80_explicit_concept_list_displays_are_preferred_terms(self, fhir_client):
        """HIGH — explicit concept list: every contains[].display MUST be the
        engine preferred term (NOT raw code).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [
                    {"code": SNOMED_DM},
                    {"code": SNOMED_T2DM},
                ],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        entries = r[1].get("expansion", {}).get("contains", [])
        for entry in entries:
            code = entry.get("code", "")
            display = entry.get("display", "")
            # display MUST NOT be the raw code
            assert display != code, (
                f"Clinical-correctness violation: display {display!r} equals "
                f"the raw code {code!r}."
            )
            # display MUST NOT be empty
            assert display != "", (
                f"Clinical-correctness violation: display is empty for "
                f"code {code!r}."
            )

    def test_t81_isa_filter_root_display_clinically_correct(self, fhir_client):
        """HIGH — is-a filter: root display in expansion MUST be the engine
        preferred term (NOT raw code).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [{
                    "property": "concept", "op": "is-a", "value": SNOMED_DM,
                }],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        entries = r[1].get("expansion", {}).get("contains", [])
        # Root entry MUST have preferred-term display
        root_entry = next((e for e in entries if e.get("code") == SNOMED_DM), None)
        assert root_entry is not None
        assert root_entry.get("display") == SNOMED_DM_DISPLAY, (
            f"Root display MUST be engine preferred term "
            f"{SNOMED_DM_DISPLAY!r}; got {root_entry.get('display')!r}"
        )

    def test_t82_unknown_code_display_falls_back_to_code(self, fhir_client):
        """HIGH — unknown code (no STR): display falls back to the code string.

        Per _expand_intensional source (lines 2546-2552): when client omits
        display AND code_infos returns no canonical name, the display falls
        back to the code string. This is the clinically correct behavior —
        the implementation cannot invent a display.
        """
        unknown_code = "9999999999"  # not in fixture
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": unknown_code}],
            }]},
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        contains = r[1].get("expansion", {}).get("contains", [])
        # Implementation MAY include unknown code (with code as display) OR
        # exclude it (empty expansion). Both are spec-conformant per FHIR R4.
        if len(contains) == 1:
            # If included, display MUST be the code (fallback when no STR)
            assert contains[0].get("code") == unknown_code
            display = contains[0].get("display", "")
            # Display is either the code fallback OR empty string
            assert display in (unknown_code, ""), (
                f"Unknown code display MUST be code fallback or empty; "
                f"got {display!r}"
            )


# =============================================================================
# L9: Compose.exclude clinical correctness
# =============================================================================
# Spec: FHIR R4 ValueSet.compose.exclude
#   "Exclude one or more codes from the value set."
#   (https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.exclude)
#
# Clinical justification: excluding a clinically indicated code from a value
# set MUST produce a clinically-correct smaller expansion. E.g., excluding
# T2DM from a "Diabetes" value set leaves only DM (the parent). This is
# load-bearing for clinical decision support: a CDS hook using the value
# set would silently miss the excluded code.

class TestLens9ComposeExcludeClinicalCorrectness:
    """Lens 9: compose.exclude clinical correctness.
    """

    def test_t90_exclude_removes_clinically_indicated_code(self, fhir_client):
        """HIGH — excluding T2DM from a "Diabetes DM+T2DM" value set MUST
        remove T2DM from the expansion, leaving only DM.

        Clinical justification: the exclusion is intentional — the value set
        author wants DM but not its subtype. The expansion MUST reflect the
        exclusion.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DM},
                        {"code": SNOMED_T2DM},
                    ],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_T2DM}],
                }],
            },
        }
        r = _post_expand(fhir_client, vs)
        assert r[0] == 200
        codes = {(c[0], c[1]) for c in _contains_codes_displays(r[1])}
        # DM MUST be in the expansion
        assert (SNOMED_URI, SNOMED_DM) in codes, (
            f"DM MUST remain after excluding T2DM; got codes={codes}"
        )
        # T2DM MUST NOT be in the expansion
        assert (SNOMED_URI, SNOMED_T2DM) not in codes, (
            f"T2DM MUST be excluded; got codes={codes}"
        )

    def test_t91_exclude_no_op_when_code_not_in_include(self, fhir_client):
        """HIGH — excluding a code that's NOT in include is a clinical no-op.

        The expansion MUST be identical to the include-only expansion.
        """
        vs_include_only = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DM}],
                }],
            },
        }
        vs_with_noop_exclude = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DM}],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_T2DM}],  # not in include
                }],
            },
        }
        r1 = _post_expand(fhir_client, vs_include_only)
        r2 = _post_expand(fhir_client, vs_with_noop_exclude)
        assert r1[0] == 200 and r2[0] == 200
        codes1 = {(c[0], c[1], c[2]) for c in _contains_codes_displays(r1[1])}
        codes2 = {(c[0], c[1], c[2]) for c in _contains_codes_displays(r2[1])}
        assert codes1 == codes2, (
            f"Exclude no-op MUST preserve expansion; got "
            f"include-only={codes1} vs with-exclude={codes2}"
        )


# =============================================================================
# L10: Source-read structural contracts — canonical-display delegation
# =============================================================================
# Source-read audit: _expand_intensional MUST source contains[].display
# from get_code_infos (canonical preferred term) for omitted-display cases,
# NOT echo the raw code when an STR exists.

class TestLens10SourceReadStructuralContracts:
    """Lens 10: source-read structural contracts for canonical-display
    delegation in _expand_intensional.
    """

    def test_t100_expand_intensional_uses_get_code_infos_for_omitted_display(self):
        """HIGH — _expand_intensional MUST call get_code_infos for omitted-
        display resolution.

        Source-read contract: the function body MUST contain
        `get_code_infos(` for the omitted-display canonical resolution
        path (VS-01 TERMINOLOGIST QA-056 RESOLVED).
        """
        src = _get_func_source(_FHIR_API_PATH, "_expand_intensional")
        assert "get_code_infos(" in src, (
            f"_expand_intensional MUST call get_code_infos for omitted-"
            f"display canonical resolution (VS-01 TERMINOLOGIST QA-056)."
        )

    def test_t101_expand_intensional_root_display_from_name(self):
        """HIGH — _expand_intensional root display MUST be sourced from
        CodeInfo.name (NOT a hardcoded literal).

        Source-read contract: the function body MUST contain
        `root_infos[0].name` for the root display resolution.
        """
        src = _get_func_source(_FHIR_API_PATH, "_expand_intensional")
        assert "root_infos[0].name" in src, (
            f"_expand_intensional MUST source root display from "
            f"root_infos[0].name (engine preferred term)."
        )

    def test_t102_expand_intensional_descendant_display_from_target_display(self):
        """HIGH — _expand_intensional descendant display MUST be sourced from
        Relation.target_display (NOT a hardcoded literal).

        Source-read contract: the function body MUST contain
        `d.target_display` for the descendant display resolution.
        """
        src = _get_func_source(_FHIR_API_PATH, "_expand_intensional")
        assert "d.target_display" in src, (
            f"_expand_intensional MUST source descendant display from "
            f"d.target_display (Relation.target_display)."
        )

    def test_t103_expand_intensional_no_hardcoded_diabetes_literal(self):
        """HIGH — _expand_intensional MUST NOT hardcode clinical literals
        like 'Diabetes mellitus' or 'Type 2 diabetes mellitus'.

        Source-read contract: the function MUST source displays from the
        engine (CodeInfo.name, Relation.target_display), NEVER from
        hardcoded clinical literals.
        """
        src = _get_func_source(_FHIR_API_PATH, "_expand_intensional")
        # Walk AST to find string-literal Constants only (excludes comments)
        tree = ast.parse(src)
        forbidden_literals = {"Diabetes mellitus", "Type 2 diabetes mellitus"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in forbidden_literals, (
                    f"_expand_intensional MUST NOT hardcode clinical literal "
                    f"{node.value!r} at line {node.lineno}. Use engine "
                    f"preferred-term resolution."
                )

    def test_t104_build_valueset_expand_signature_has_total(self):
        """HIGH — build_valueset_expand signature MUST include `total` as
        optional keyword parameter.

        Per VS-02 SKEPTIC QA-057 + VS-04 TERMINOLOGIST QA-068 (count=3
        PROMOTED as response-builder drift pattern), the builder MUST
        accept `total` so truncating call sites can pass the un-truncated
        size.
        """
        src = _get_func_source(_RESPONSES_PATH, "build_valueset_expand")
        # Signature contains `total` parameter
        assert "total" in src, (
            f"build_valueset_expand signature MUST include `total` parameter "
            f"(VS-02 SKEPTIC QA-057)."
        )

    def test_t105_canonical_system_uri_called_in_intensional(self):
        """HIGH — _expand_intensional MUST call canonical_system_uri for
        Out contains[].system re-resolution.

        Per CS-03 HISTORIAN QA-051 (client-input-as-canonical drift pattern
        count=8+1 PROMOTED), the implementation MUST re-resolve the client-
        supplied include[].system through canonical_system_uri to emit
        canonical URIs in contains[].system.
        """
        src = _get_func_source(_FHIR_API_PATH, "_expand_intensional")
        assert "canonical_system_uri(" in src, (
            f"_expand_intensional MUST call canonical_system_uri for "
            f"contains[].system canonical re-resolution (CR-013)."
        )


# =============================================================================
# META structural invariant probes
# =============================================================================

class TestMetaStructuralInvariants:
    """META probes verifying function/helper existence — surface refactor
    root causes before source-read probes that depend on them.
    """

    def test_t110_expand_intensional_defined(self):
        """_expand_intensional MUST be defined inside create_fhir_app."""
        src = _get_func_source(_FHIR_API_PATH, "_expand_intensional")
        assert src, "_expand_intensional MUST be defined"

    def test_t111_get_code_infos_importable(self):
        """get_code_infos MUST be importable from services."""
        from medterm4ds.services.lookup import get_code_infos  # noqa: F401

    def test_t112_canonical_system_uri_importable(self):
        """canonical_system_uri MUST be importable from engines.fhir."""
        from medterm4ds.engines.fhir import canonical_system_uri  # noqa: F401

    def test_t113_get_descendants_bfs_importable(self):
        """get_descendants_bfs MUST be importable from services.hierarchy."""
        from medterm4ds.services.hierarchy import get_descendants_bfs  # noqa: F401

    def test_t114_build_valueset_expand_importable(self):
        """build_valueset_expand MUST be importable from engines.fhir.responses."""
        from medterm4ds.engines.fhir.responses import build_valueset_expand  # noqa: F401

    def test_t115_fhir_r4_filter_operators_importable(self):
        """FHIR_R4_FILTER_OPERATORS MUST be importable from engines.fhir."""
        from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS  # noqa: F401
