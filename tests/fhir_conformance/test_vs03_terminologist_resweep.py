"""VS-03 TERMINOLOGIST (resweep): ValueSet $expand — Advanced.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Filter operator: https://hl7.org/fhir/R4/valueset-concept-operator.html
ValueSet resource: https://hl7.org/fhir/R4/valueset.html#compose
ValueSet.expansion.contains.display:
  https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display
valueset-toocostly extension:
  https://hl7.org/fhir/R4/extension-valueset-toocostly.html

TERMINOLOGIST lens (medterm4ds 4th personality, HIGH severity default per
GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH"): clinical and
terminological correctness on the VS-03 surface. Per EXPLORER handoff
(2026-08-09), TERMINOLOGIST MUST:

  1. Extend the canonical-DISPLAY cross-operation invariant (VS-01
     TERMINOLOGIST resweep test_t70 3-way invariant) to EVERY VS-03
     mode (filter / intensional / URL pattern / implicit / explicit
     concept list).
  2. Verify is-a descendant display clinical sensibility (descendants
     resolve to canonical preferred terms, not raw codes).
  3. Verify exclude semantics clinical correctness (exclude-after-is-a-
     filter removes only the excluded concept; clinical integrity intact).
  4. Verify multi-include cross-system clinical consistency (SNOMED DM +
     ICD-10-CM T2DM in same expansion — clinically sensible union).
  5. Verify per-source preferred-term policy (SNOMED=PT, ICD-10-CM=HT,
     RxNorm=SCD) — each code resolves to its source's preferred term
     type, not a generic engine atom.
  6. Reconfirm CF-TERMINOLOGIST-VS01-01 supplied-display echo resolution
     status (DEFERRED — pinned via carry-forward-as-probe pattern).

Conformance fixture (tests/fhir_conformance/conftest.py):
  mrconso:
    73211009 | PT  | "Diabetes mellitus"                    | SNOMEDCT_US | C0011849
    44054006 | PT  | "Type 2 diabetes mellitus"             | SNOMEDCT_US | C0011847
    E11      | HT  | "Type 2 diabetes mellitus"             | ICD10CM     | C0011847
    860975   | SCD | "24 HR metformin 500 MG Oral Tablet"   | RXNORM      | C0978484
  mrrel:
    A44054006 → A73211009 | isa | PAR

Default severity: HIGH per GLOBAL_RULES.md.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/valueset-concept-operator.html (Filter Operator)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display
# Spec: https://hl7.org/fhir/R4/parameters.html (resource property)
from medterm4ds.engines.fhir import (
    FHIR_R4_FILTER_OPERATORS,
    SYSTEM_TO_FHIR_URI,
    canonical_system_uri,
)

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"
LOINC_URI = "http://loinc.org"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Canonical preferred terms per the conformance fixture.
# Spec: FHIR R4 §4.9.5 contains[].display: "The recommended display for this
# item in the expansion" — implies the SERVER's canonical preferred term, not a
# client-supplied echo. The clinical-safety invariant: a CDS hook reading the
# expansion MUST see the same canonical name the engine uses for $lookup.
CANONICAL_DISPLAY_SNOMED_DM = "Diabetes mellitus"
CANONICAL_DISPLAY_SNOMED_T2DM = "Type 2 diabetes mellitus"
CANONICAL_DISPLAY_ICD10CM_T2DM = "Type 2 diabetes mellitus"
CANONICAL_DISPLAY_RXNORM_METFORMIN = "24 HR metformin 500 MG Oral Tablet"

# Per-source preferred-term policy. The fixture deliberately uses a DIFFERENT
# TTY for each source (PT/HT/SCD) so probes can verify the engine is picking
# the right preferred atom per source — not just any atom. If the engine
# returned an LOINC long-name atom or an HCD/HT atom for SNOMED, that would be
# silent-wrong-answer at the clinical level.
PER_SOURCE_PREFERRED_TTY = {
    "SNOMEDCT_US": "PT",   # Preferred Term
    "ICD10CM": "HT",       # Hypterms
    "RXNORM": "SCD",       # Semantic Clinical Drug
}


# =============================================================================
# Source path + AST helpers (mirrors TS-01 HISTORIAN strategy; extended by
# CS-03 HISTORIAN _get_nested_func_source helper)
# =============================================================================

FHIR_API_PATH = Path(inspect.getsourcefile(__import__("medterm4ds.apps.fhir_api", fromlist=["fhir_api"])))


def _read_module_source() -> str:
    return FHIR_API_PATH.read_text()


def _get_module_ast() -> ast.Module:
    return ast.parse(_read_module_source())


def _get_func_source(func_name: str) -> str | None:
    """Source-read a top-level function definition (mirrors VS-03 HISTORIAN).

    NOTE: ``ast.get_source_segment`` takes (source_string, node), NOT
    (tree, node). Passing the parsed Module object raises TypeError.
    """
    src = _read_module_source()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(src, node) or ""
    return None


def _get_nested_func_source(parent_name: str, child_name: str) -> str | None:
    """Source-read a function defined inside another function (e.g. inside
    ``create_fhir_app``). Plain ``ast.walk`` would miss nested defs. Mirrors
    the CS-03 HISTORIAN helper.

    NOTE: ``ast.get_source_segment`` takes (source_string, node), NOT
    (tree, node). Passing the parsed Module object raises TypeError.
    """
    src = _read_module_source()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parent_name:
            for child in ast.walk(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == child_name:
                    return ast.get_source_segment(src, child) or ""
    return None


# =============================================================================
# Helpers
# =============================================================================

def _post_expand(fhir_client, body: dict, *, params: dict | None = None) -> tuple[int, dict]:
    """POST a body to /fhir/ValueSet/$expand.

    Returns (status_code, body_json). The body may be a ValueSet resource
    (intensional/extensional) OR a Parameters resource (filter mode), per
    FHIR R4 §4.7.5.
    """
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=body,
        params=params or {},
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _get_expand(fhir_client, *, params: dict) -> tuple[int, dict]:
    """GET /fhir/ValueSet/$expand with query params."""
    resp = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=params,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _contains_codes(body: dict) -> list[tuple[str, str]]:
    """Extract (system, code) pairs from ValueSet.expansion.contains."""
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


def _contains_displays(body: dict) -> dict[tuple[str, str], str]:
    """Map (system, code) -> display from ValueSet.expansion.contains."""
    out = {}
    for c in body.get("expansion", {}).get("contains", []):
        out[(c.get("system", ""), c.get("code", ""))] = c.get("display", "")
    return out


def _lookup_display(fhir_client, system: str, code: str) -> str | None:
    """Get $lookup Out `display` parameter value for (system, code)."""
    resp = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
        headers={"Accept": "application/fhir+json"},
    )
    if resp.status_code != 200:
        return None
    for p in resp.json().get("parameter", []):
        if p.get("name") == "display":
            return p.get("valueString")
    return None


def _validate_display(fhir_client, system: str, code: str) -> str | None:
    """Get ValueSet $validate-code Out `display` for (system, code)."""
    resp = fhir_client.get(
        "/fhir/ValueSet/$validate-code",
        params={"system": system, "code": code, "url": system},
        headers={"Accept": "application/fhir+json"},
    )
    if resp.status_code != 200:
        return None
    for p in resp.json().get("parameter", []):
        if p.get("name") == "display":
            return p.get("valueString")
    return None


def _make_extensional_snomed(concepts=None) -> dict:
    """Build an extensional ValueSet with explicit concept list."""
    if concepts is None:
        concepts = [
            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"},
            {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"},
        ]
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-term-extensional",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "concept": concepts,
            }],
        },
    }


def _make_intensional_snomed_isa(root_code: str = SNOMED_DIABETES_MELLITUS) -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-term-intensional-isa",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "is-a", "value": root_code}
                ],
            }],
        },
    }


def _make_intensional_snomed_descendent_of(root_code: str = SNOMED_DIABETES_MELLITUS) -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-term-intensional-descendent-of",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "descendent-of", "value": root_code}
                ],
            }],
        },
    }


# =============================================================================
# Lens 1: Canonical-DISPLAY cross-operation invariant on EVERY VS-03 mode
# (EXPLORER tip: extend VS-01 TERMINOLOGIST resweep test_t70 3-way invariant
# to all 5 VS-03 modes — filter / intensional / URL pattern / implicit /
# explicit concept list).
#
# Spec: FHIR R4 ValueSet.expansion.contains.display: "The recommended display
# for this item in the expansion." Clinical-safety invariant: the recommended
# display MUST equal $lookup's Out `display` for the same (system, code) — a
# CDS hook passing the expansion's Coding through $lookup for enrichment MUST
# see the same display, not a different one. Mismatched displays are silent-
# wrong-answer at the clinical level (e.g., $expand returns the LOINC long
# name; $lookup returns the patient-friendly name — the user sees two
# different labels for the same code).
#
# CS-02 TERMINOLOGIST established the canonical-DISPLAY invariant across
# $lookup ↔ $validate-code ↔ $translate target concept. VS-03 TERMINOLOGIST
# extends it to: $lookup ↔ $validate-code ↔ $expand(every mode).
# =============================================================================


class TestLens1CanonicalDisplayEveryMode:
    """Lens 1: canonical-DISPLAY cross-operation invariant on every VS-03 mode.

    Per EXPLORER tip: extend the VS-01 TERMINOLOGIST 3-way invariant to every
    $expand mode. For each mode, the contains[].display for a given
    (system, code) MUST equal the $lookup Out `display` for the same pair.

    Modes probed: (a) explicit concept list, (b) is-a intensional, (c)
    descendent-of intensional, (d) filter (text search), (e) URL pattern
    (?fhir_vs=isa), (f) implicit value set (/vs).
    """

    @pytest.mark.parametrize("system,code,expected", [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, CANONICAL_DISPLAY_SNOMED_DM),
        (SNOMED_URI, SNOMED_T2DM, CANONICAL_DISPLAY_SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_T2DM, CANONICAL_DISPLAY_ICD10CM_T2DM),
        (RXNORM_URI, RXNORM_METFORMIN, CANONICAL_DISPLAY_RXNORM_METFORMIN),
    ])
    def test_t10_explicit_concept_list_display_matches_lookup(
        self, fhir_client, system, code, expected,
    ):
        """Mode (a): explicit concept list — contains[].display byte-exact
        equals $lookup Out `display` for the same (system, code).
        """
        vs = {
            "resourceType": "ValueSet",
            "url": f"http://example.org/vs/vs03-t10-{code}",
            "compose": {"include": [{
                "system": system, "concept": [{"code": code}],
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200, f"expand failed: {s} {b}"
        expand_display = _contains_displays(b).get((system, code))
        assert expand_display is not None, f"code missing in expansion: {b}"
        lookup_display = _lookup_display(fhir_client, system, code)
        assert lookup_display == expected, (
            f"$lookup display for ({system}, {code}) not canonical: "
            f"{lookup_display!r}, expected {expected!r}"
        )
        assert expand_display == lookup_display, (
            f"canonical-DISPLAY invariant broken on explicit concept list: "
            f"$expand={expand_display!r}, $lookup={lookup_display!r}"
        )

    @pytest.mark.parametrize("code,expected", [
        (SNOMED_DIABETES_MELLITUS, CANONICAL_DISPLAY_SNOMED_DM),
        (SNOMED_T2DM, CANONICAL_DISPLAY_SNOMED_T2DM),
    ])
    def test_t11_is_a_intensional_display_matches_lookup(
        self, fhir_client, code, expected,
    ):
        """Mode (b): is-a intensional — root and descendant displays byte-exact
        equal $lookup Out `display`.
        """
        vs = _make_intensional_snomed_isa()
        s, b = _post_expand(fhir_client, vs)
        assert s == 200, f"expand failed: {s} {b}"
        expand_display = _contains_displays(b).get((SNOMED_URI, code))
        assert expand_display is not None, f"code {code} missing in is-a expansion"
        lookup_display = _lookup_display(fhir_client, SNOMED_URI, code)
        assert lookup_display == expected
        assert expand_display == lookup_display, (
            f"canonical-DISPLAY invariant broken on is-a path for {code}: "
            f"$expand={expand_display!r}, $lookup={lookup_display!r}"
        )

    @pytest.mark.parametrize("code,expected", [
        (SNOMED_T2DM, CANONICAL_DISPLAY_SNOMED_T2DM),
    ])
    def test_t12_descendent_of_intensional_display_matches_lookup(
        self, fhir_client, code, expected,
    ):
        """Mode (c): descendent-of intensional — descendant display byte-exact
        equals $lookup Out `display`.
        """
        vs = _make_intensional_snomed_descendent_of()
        s, b = _post_expand(fhir_client, vs)
        assert s == 200, f"expand failed: {s} {b}"
        expand_display = _contains_displays(b).get((SNOMED_URI, code))
        assert expand_display is not None, f"descendant {code} missing"
        lookup_display = _lookup_display(fhir_client, SNOMED_URI, code)
        assert lookup_display == expected
        assert expand_display == lookup_display, (
            f"canonical-DISPLAY invariant broken on descendent-of path: "
            f"$expand={expand_display!r}, $lookup={lookup_display!r}"
        )

    @pytest.mark.parametrize("system,code,filter_text,expected", [
        # Filter 'diabetes' resolves both DM and T2DM in the SNOMED fixture.
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, "diabetes", CANONICAL_DISPLAY_SNOMED_DM),
        (SNOMED_URI, SNOMED_T2DM, "diabetes", CANONICAL_DISPLAY_SNOMED_T2DM),
        # 'metformin' resolves the RxNorm SCD.
        (RXNORM_URI, RXNORM_METFORMIN, "metformin", CANONICAL_DISPLAY_RXNORM_METFORMIN),
    ])
    def test_t13_filter_mode_display_matches_lookup(
        self, fhir_client, system, code, filter_text, expected,
    ):
        """Mode (d): filter (text search) — contains[].display byte-exact
        equals $lookup Out `display`. The filter mode uses search_names which
        returns engine canonical preferred terms (the `r.name` field).
        """
        s, b = _get_expand(fhir_client, params={
            "filter": filter_text, "system": system,
        })
        assert s == 200, f"filter expand failed: {s} {b}"
        expand_display = _contains_displays(b).get((system, code))
        assert expand_display is not None, (
            f"code {code} missing in filter expansion (display mismatch may be "
            f"masked by code absence): {b}"
        )
        lookup_display = _lookup_display(fhir_client, system, code)
        assert lookup_display == expected
        assert expand_display == lookup_display, (
            f"canonical-DISPLAY invariant broken on filter mode: "
            f"$expand={expand_display!r}, $lookup={lookup_display!r}"
        )

    def test_t14_url_pattern_isa_display_matches_lookup(self, fhir_client):
        """Mode (e): URL pattern (?fhir_vs=isa) — root and descendant displays
        byte-exact equal $lookup Out `display`.

        Per VS-04 surface: the URL pattern uses ``expand_url_pattern`` which
        resolves via get_code_infos (root) and get_descendants_bfs
        (descendants). The descendants use ``d.target_display`` which is the
        engine canonical preferred term.
        """
        url = f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        s, b = _get_expand(fhir_client, params={"url": url})
        assert s == 200, f"url pattern expand failed: {s} {b}"
        displays = _contains_displays(b)
        # Root
        root_disp = displays.get((SNOMED_URI, SNOMED_DIABETES_MELLITUS))
        assert root_disp is not None, f"root missing in url-pattern expansion: {b}"
        assert root_disp == _lookup_display(fhir_client, SNOMED_URI, SNOMED_DIABETES_MELLITUS), (
            f"canonical-DISPLAY invariant broken on url-pattern root: {root_disp!r}"
        )
        # Descendant
        desc_disp = displays.get((SNOMED_URI, SNOMED_T2DM))
        assert desc_disp is not None, f"descendant missing in url-pattern expansion: {b}"
        assert desc_disp == _lookup_display(fhir_client, SNOMED_URI, SNOMED_T2DM), (
            f"canonical-DISPLAY invariant broken on url-pattern descendant: {desc_disp!r}"
        )

    @pytest.mark.parametrize("system,code,expected", [
        (RXNORM_URI, RXNORM_METFORMIN, CANONICAL_DISPLAY_RXNORM_METFORMIN),
    ])
    def test_t15_implicit_value_set_display_matches_lookup(
        self, fhir_client, system, code, expected,
    ):
        """Mode (f): implicit value set (/vs) — contains[].display byte-exact
        equals $lookup Out `display`. The implicit path queries mrconso by SAB
        and resolves display via get_code_infos (canonical preferred term).
        """
        url = f"{system}/vs"
        s, b = _get_expand(fhir_client, params={"url": url})
        assert s == 200, f"implicit expand failed: {s} {b}"
        displays = _contains_displays(b)
        expand_display = displays.get((system, code))
        assert expand_display is not None, (
            f"code {code} missing in implicit expansion: {b}"
        )
        lookup_display = _lookup_display(fhir_client, system, code)
        assert lookup_display == expected
        assert expand_display == lookup_display, (
            f"canonical-DISPLAY invariant broken on implicit value set: "
            f"$expand={expand_display!r}, $lookup={lookup_display!r}"
        )


# =============================================================================
# Lens 2: is-a descendant display clinical sensibility (EXPLORER tip)
# Spec: https://hl7.org/fhir/R4/valueset-concept-operator.html
#   is-a: "The definition of the value set includes the concept and all of
#          its descendants in the code system."
# Clinical-safety: descendant displays MUST be clinically sensible canonical
# preferred terms — NOT raw codes, NOT FSN (Fully Specified Name), NOT
# obsolete atoms. A CDS rule evaluating "patient has T2DM" relies on the
# display being "Type 2 diabetes mellitus" not "DM2" or "T2DM (disorder)".
# =============================================================================


class TestLens2IsADescendantDisplayClinicalSensibility:
    """Lens 2: is-a descendant display is clinically sensible.

    The fixture's T2DM (44054006) is a SNOMED PT (Preferred Term). The is-a
    expansion MUST surface "Type 2 diabetes mellitus" — NOT the code, NOT
    a FSN, NOT an obsolete string. The display must also be clinically
    distinct from the parent (DM) so a CDS hook can distinguish them.
    """

    def test_t20_descendant_display_is_canonical_preferred_term(self, fhir_client):
        """Descendant display == SNOMED PT string per the fixture.

        SNOMED PT for 44054006 is "Type 2 diabetes mellitus" (per the
        conformance fixture). The expansion MUST surface this — NOT a FSN
        (Fully Specified Name like "Type 2 diabetes mellitus (disorder)"),
        NOT a synonym, NOT the code.
        """
        vs = _make_intensional_snomed_isa()
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        desc_display = displays.get((SNOMED_URI, SNOMED_T2DM))
        assert desc_display == CANONICAL_DISPLAY_SNOMED_T2DM, (
            f"descendant display not the SNOMED PT: got {desc_display!r}, "
            f"expected {CANONICAL_DISPLAY_SNOMED_T2DM!r}. If a FSN or synonym "
            f"surfaced, that's silent-wrong-answer at the clinical level."
        )

    def test_t21_descendant_display_not_raw_code(self, fhir_client):
        """Descendant display MUST NOT be the raw code itself (e.g. "44054006").

        Per FHIR R4 §4.9.5: contains[].display is "The recommended display
        for this item in the expansion" — implies a human-readable string,
        not the code. A raw-code display is silent-wrong-answer (the CDS hook
        shows the user "44054006" instead of "Type 2 diabetes mellitus").
        """
        vs = _make_intensional_snomed_isa()
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        desc_display = displays.get((SNOMED_URI, SNOMED_T2DM))
        assert desc_display != SNOMED_T2DM, (
            f"descendant display IS the raw code: {desc_display!r}. "
            f"The engine failed to resolve the canonical preferred term."
        )
        assert desc_display, "descendant display is empty/missing"

    def test_t22_descendant_display_clinically_distinct_from_parent(self, fhir_client):
        """Descendant display MUST be clinically distinct from the parent.

        SNOMED DM (73211009) vs SNOMED T2DM (44054006) — "Diabetes mellitus"
        vs "Type 2 diabetes mellitus". A CDS rule distinguishing "any diabetes"
        from "Type 2 diabetes" relies on this distinctness. If both displays
        were identical, the value set expansion would be clinically useless.
        """
        vs = _make_intensional_snomed_isa()
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        dm_disp = displays.get((SNOMED_URI, SNOMED_DIABETES_MELLITUS))
        t2dm_disp = displays.get((SNOMED_URI, SNOMED_T2DM))
        assert dm_disp != t2dm_disp, (
            f"parent and descendant displays are identical: {dm_disp!r}. "
            f"Clinically, DM is broader than T2DM — displays MUST differ."
        )
        # The descendant display MUST be more specific (longer) than the
        # parent — T2DM is a subtype of DM, so its display includes "Type 2".
        assert "Type 2" in t2dm_disp, (
            f"descendant display lacks 'Type 2' qualifier: {t2dm_disp!r}"
        )

    def test_t23_descendant_display_no_engine_internal_leakage(self, fhir_client):
        """Descendant display MUST NOT leak engine internals (AUI, CUI,
        SUPPRESS flag, etc.).

        The conformance fixture's mrconso has AUI='A44054006', CUI='C0011847',
        SUPPRESS='N'. The display MUST be ONLY the STR field
        ("Type 2 diabetes mellitus"), not "A44054006|C0011847|..." or any
        internal-identifier mash. Leaking internals is silent-wrong-answer at
        the clinical level (the CDS hook would display a meaningless string).
        """
        vs = _make_intensional_snomed_isa()
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        desc_display = displays.get((SNOMED_URI, SNOMED_T2DM))
        assert desc_display == CANONICAL_DISPLAY_SNOMED_T2DM
        # No AUI/CUI/SUPPRESS/internal tokens leaked.
        for forbidden in ("A44054006", "C0011847", "SNOMEDCT_US", "|"):
            assert forbidden not in desc_display, (
                f"descendant display leaks internal token {forbidden!r}: "
                f"{desc_display!r}"
            )

    def test_t24_descendent_of_descendant_display_also_canonical(self, fhir_client):
        """descendent-of descendant display == SNOMED PT (mirror of test_t20).

        The descendent-of operator excludes the root but its descendants'
        displays MUST still be canonical preferred terms.
        """
        vs = _make_intensional_snomed_descendent_of()
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        desc_display = displays.get((SNOMED_URI, SNOMED_T2DM))
        assert desc_display == CANONICAL_DISPLAY_SNOMED_T2DM


# =============================================================================
# Lens 3: Exclude semantics clinical correctness (EXPLORER tip)
# Spec: https://hl7.org/fhir/R4/valueset.html#compose
#   compose.exclude: "Excludes one or more codes from the value set."
# Clinical-safety: when exclude[] removes a concept from an include[] expansion,
# the result MUST be clinically correct — only the excluded concept(s) are
# removed, no collateral damage.
# =============================================================================


class TestLens3ExcludeSemanticsClinicalCorrectness:
    """Lens 3: exclude semantics are clinically correct.

    Per FHIR R4 ValueSet.compose.exclude: "Excludes one or more codes from
    the value set." The exclude MUST:
      (a) remove ONLY the excluded concept(s) — no collateral damage;
      (b) preserve other concepts' canonical displays;
      (c) produce a clinically sensible result (e.g., exclude root DM from
          is-a(DM) → only T2DM remains, which is the specific subtype).
    """

    def test_t30_exclude_after_is_a_filter_preserves_descendant(self, fhir_client):
        """is-a(DM) + exclude(DM) → only T2DM remains (the specific subtype).

        Clinical sensibility: a CDS rule "screen for specific diabetes
        subtypes (not the abstract grouping)" would use this exact pattern.
        The result MUST be {T2DM} — not {T2DM, DM}, not {}, not {DM}.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t30",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                    ],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200, f"expand failed: {s} {b}"
        codes = set(_contains_codes(b))
        assert codes == {(SNOMED_URI, SNOMED_T2DM)}, (
            f"is-a(DM) + exclude(DM) should leave only T2DM; got {codes}. "
            f"Clinically: the result is 'specific diabetes subtypes'."
        )

    def test_t31_exclude_preserves_remaining_displays(self, fhir_client):
        """Exclude does NOT corrupt the surviving concepts' displays.

        is-a(DM) + exclude(DM): the surviving T2DM display MUST still be the
        canonical SNOMED PT, not the code or an empty string.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t31",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                    ],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        assert displays.get((SNOMED_URI, SNOMED_T2DM)) == CANONICAL_DISPLAY_SNOMED_T2DM, (
            f"surviving display not canonical after exclude: {displays}"
        )

    def test_t32_exclude_unknown_code_no_collateral_damage(self, fhir_client):
        """Excluding a code NOT in the include expansion leaves it unchanged.

        Clinical safety: a typo'd exclude code (e.g., exclude '99999999')
        MUST NOT alter the include expansion. A bug that incorrectly drops
        codes when the exclude doesn't match is silent-wrong-answer.
        """
        vs_include_only = _make_intensional_snomed_isa()
        vs_with_noop_exclude = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t32",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                    ],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": "99999999"}],  # not in include
                }],
            },
        }
        s1, b1 = _post_expand(fhir_client, vs_include_only)
        s2, b2 = _post_expand(fhir_client, vs_with_noop_exclude)
        assert s1 == 200 and s2 == 200
        codes1 = set(_contains_codes(b1))
        codes2 = set(_contains_codes(b2))
        assert codes1 == codes2, (
            f"no-op exclude altered the expansion: include_only={codes1}, "
            f"with_noop_exclude={codes2}. The exclude MUST be a no-op when "
            f"the excluded code is not in the include."
        )

    def test_t33_exclude_descendant_leaves_root(self, fhir_client):
        """is-a(DM) + exclude(T2DM) → only DM remains (the abstract grouping).

        Clinical sensibility: a CDS rule for "the abstract diabetes concept
        only (not specific subtypes)" would use this exact pattern.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t33",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                    ],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_T2DM}],
                }],
            },
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        codes = set(_contains_codes(b))
        assert codes == {(SNOMED_URI, SNOMED_DIABETES_MELLITUS)}, (
            f"is-a(DM) + exclude(T2DM) should leave only DM; got {codes}. "
            f"Clinically: the result is 'the abstract diabetes concept only'."
        )

    def test_t34_exclude_cross_system_does_not_collar(self, fhir_client):
        """exclude from one system does NOT remove concepts from another system.

        Multi-include {SNOMED DM, ICD-10-CM T2DM} + exclude(SNOMED DM):
        only the SNOMED DM is removed; ICD-10-CM T2DM MUST remain.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t34",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]},
                    {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
                ],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        codes = set(_contains_codes(b))
        assert codes == {(ICD10CM_URI, ICD10CM_T2DM)}, (
            f"cross-system exclude altered non-excluded system: got {codes}. "
            f"Expected: only ICD-10-CM T2DM remains."
        )


# =============================================================================
# Lens 4: Multi-include cross-system clinical consistency (EXPLORER tip)
# Spec: https://hl7.org/fhir/R4/valueset.html#compose
#   compose.include (1..*): "An include clause specifies what to include in
#   the value set." Multiple include clauses MUST be unioned.
# Clinical-safety: a value set spanning multiple code systems (SNOMED DM +
# ICD-10-CM T2DM) represents a clinically sensible union — the same clinical
# concept represented in different terminologies. Each system's code MUST
# resolve to its OWN canonical preferred term (SNOMED=PT, ICD-10-CM=HT) — NOT
# to a cross-source leak.
# =============================================================================


class TestLens4MultiIncludeCrossSystemConsistency:
    """Lens 4: multi-include cross-system expansion is clinically consistent.

    SNOMED DM (73211009) and ICD-10-CM T2DM (E11) share CUI C0011847 in the
    fixture — they are clinically equivalent representations. The multi-
    include expansion MUST:
      (a) include BOTH codes (clinical completeness — one concept, two
          terminologies);
      (b) preserve each code's source-specific display (SNOMED=PT, ICD-10-
          CM=HT);
      (c) NOT leak one system's display into another (e.g., ICD-10-CM T2DM
          display MUST NOT be the SNOMED PT string).
    """

    def test_t40_multi_include_snomed_dm_and_icd10cm_t2dm_both_present(self, fhir_client):
        """SNOMED DM + ICD-10-CM T2DM in same expansion (clinically sensible
        union — both are diabetes concepts).

        The fixture shares CUI C0011847 between SNOMED 44054006 and ICD-10-CM
        E11 (both T2DM), but the multi-include here is SNOMED DM (73211009,
        the parent) + ICD-10-CM T2DM. Clinically: a value set for "diabetes
        regardless of terminology" would include both.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t40",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]},
                {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
            ]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        codes = set(_contains_codes(b))
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (ICD10CM_URI, ICD10CM_T2DM) in codes

    def test_t41_multi_include_each_code_resolves_own_system_display(self, fhir_client):
        """Each code in a multi-include resolves to its OWN source's canonical
        preferred term — no cross-source display leak.

        SNOMED DM display = "Diabetes mellitus" (PT).
        ICD-10-CM T2DM display = "Type 2 diabetes mellitus" (HT).
        Both share CUI C0011847 (clinically related) but the displays MUST
        come from their respective sources.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t41",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]},
                {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
                {"system": RXNORM_URI, "concept": [{"code": RXNORM_METFORMIN}]},
            ]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == CANONICAL_DISPLAY_SNOMED_DM
        assert displays[(ICD10CM_URI, ICD10CM_T2DM)] == CANONICAL_DISPLAY_ICD10CM_T2DM
        assert displays[(RXNORM_URI, RXNORM_METFORMIN)] == CANONICAL_DISPLAY_RXNORM_METFORMIN

    def test_t42_multi_include_no_cross_source_display_leak(self, fhir_client):
        """Multi-include: ICD-10-CM T2DM display MUST NOT be the SNOMED PT.

        The fixture's SNOMED T2DM (44054006) and ICD-10-CM T2DM (E11) share
        CUI C0011847 and the same display ("Type 2 diabetes mellitus"). This
        is a fixture coincidence — the probe asserts no INTERNAL cross-
        contamination (e.g., the engine NOT mixing up source mappings).
        """
        # Use SNOMED DM (CUI C0011849) — distinct from ICD-10-CM T2DM (C0011847)
        # to avoid the fixture-coincidence same-display.
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t42",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]},
                {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
            ]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        # SNOMED DM display MUST be "Diabetes mellitus" (its own PT).
        # ICD-10-CM T2DM display MUST be "Type 2 diabetes mellitus" (its own HT).
        # These are CLINICALLY DIFFERENT — DM is broader than T2DM. If the
        # engine confused sources, both displays would be the same.
        snomed_dm = displays.get((SNOMED_URI, SNOMED_DIABETES_MELLITUS))
        icd10_t2dm = displays.get((ICD10CM_URI, ICD10CM_T2DM))
        assert snomed_dm == CANONICAL_DISPLAY_SNOMED_DM
        assert icd10_t2dm == CANONICAL_DISPLAY_ICD10CM_T2DM
        assert snomed_dm != icd10_t2dm, (
            f"multi-include cross-source display leak: SNOMED DM and ICD-10-CM "
            f"T2DM have same display {snomed_dm!r} — they are clinically "
            f"different concepts (broader DM vs specific T2DM)."
        )

    def test_t43_multi_include_is_a_filter_per_system(self, fhir_client):
        """Multi-include with mixed concept+filter: SNOMED DM explicit +
        ICD-10-CM T2DM explicit + SNOMED is-a(DM) filter expansion.

        Each include clause operates independently; the result is the union.
        No source bleed: SNOMED codes stay SNOMED, ICD-10-CM stay ICD-10-CM.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t43",
            "compose": {"include": [
                # SNOMED explicit DM
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]},
                # SNOMED is-a(DM) → DM + T2DM
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS},
                ]},
                # ICD-10-CM explicit T2DM
                {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
            ]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200, f"expand failed: {s} {b}"
        codes = set(_contains_codes(b))
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes
        assert (ICD10CM_URI, ICD10CM_T2DM) in codes
        # Each code's display MUST resolve to its own source's canonical.
        displays = _contains_displays(b)
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == CANONICAL_DISPLAY_SNOMED_DM
        assert displays[(SNOMED_URI, SNOMED_T2DM)] == CANONICAL_DISPLAY_SNOMED_T2DM
        assert displays[(ICD10CM_URI, ICD10CM_T2DM)] == CANONICAL_DISPLAY_ICD10CM_T2DM


# =============================================================================
# Lens 5: Per-source preferred-term policy (EXPLORER tip)
# Spec: FHIR R4 §4.9.5 contains[].display: "The recommended display for this
#   item in the expansion."
# Clinical-safety: each source has a different preferred-term type:
#   - SNOMED CT → PT (Preferred Term) — clinically validated display
#   - ICD-10-CM → HT (Hypterms) — preferred billing classification term
#   - RxNorm → SCD (Semantic Clinical Drug) — preferred clinically precise form
# The engine MUST surface each source's preferred-term type. If it surfaces
# LO (Layout) or HCD (Historical Clinical Drug) for RxNorm, or OBSOLETE for
# SNOMED, that's silent-wrong-answer at the clinical level.
#
# Implementation note: this lens verifies the END-TO-END result (display
# equals the fixture's preferred-term STR) without coupling to engine internals
# (e.g., TTY ranking). The conformance fixture has exactly one atom per code,
# so the engine's preferred-term policy is verified indirectly via the
# display string itself.
# =============================================================================


class TestLens5PerSourcePreferredTermPolicy:
    """Lens 5: per-source preferred-term policy.

    Each source's code in an expansion resolves to that source's preferred-
    term type. The conformance fixture uses a different TTY per source
    (PT/HT/SCD) precisely so probes can verify the engine picks the right
    one. If the engine returned, say, an SCD for SNOMED or a PT for RxNorm,
    that would be a clinical-correctness defect.
    """

    @pytest.mark.parametrize("system,code,expected_display,source_label", [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, CANONICAL_DISPLAY_SNOMED_DM, "SNOMEDCT_US"),
        (SNOMED_URI, SNOMED_T2DM, CANONICAL_DISPLAY_SNOMED_T2DM, "SNOMEDCT_US"),
        (ICD10CM_URI, ICD10CM_T2DM, CANONICAL_DISPLAY_ICD10CM_T2DM, "ICD10CM"),
        (RXNORM_URI, RXNORM_METFORMIN, CANONICAL_DISPLAY_RXNORM_METFORMIN, "RXNORM"),
    ])
    def test_t50_expand_resolves_per_source_preferred_term(
        self, fhir_client, system, code, expected_display, source_label,
    ):
        """Each (system, code) in an expansion resolves to the fixture's
        preferred-term STR (PT for SNOMED, HT for ICD-10-CM, SCD for RxNorm).

        This is a clinical-truth probe: the conformance fixture has exactly
        one atom per code at the preferred TTY. If the engine returns the
        correct STR, its preferred-term policy is intact for that source.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": f"http://example.org/vs/vs03-t50-{source_label}-{code}",
            "compose": {"include": [{
                "system": system, "concept": [{"code": code}],
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200, f"expand failed: {s} {b}"
        displays = _contains_displays(b)
        actual = displays.get((system, code))
        assert actual == expected_display, (
            f"per-source preferred-term policy broken for source={source_label}: "
            f"expected {expected_display!r}, got {actual!r}. The engine may be "
            f"picking a non-preferred atom (LO/HCD/obsolete) instead of the "
            f"preferred TTY ({PER_SOURCE_PREFERRED_TTY[source_label]})."
        )

    @pytest.mark.parametrize("system,code,source_label", [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, "SNOMEDCT_US"),
        (SNOMED_URI, SNOMED_T2DM, "SNOMEDCT_US"),
        (ICD10CM_URI, ICD10CM_T2DM, "ICD10CM"),
        (RXNORM_URI, RXNORM_METFORMIN, "RXNORM"),
    ])
    def test_t51_lookup_validate_expand_three_way_display_agreement(
        self, fhir_client, system, code, source_label,
    ):
        """Three-way display agreement: $lookup Out display == $validate-code
        Out display == $expand contains[].display.

        VS-01 TERMINOLOGIST resweep test_t70 established the 3-way invariant
        on the VS-01 surface. VS-03 TERMINOLOGIST EXTENDS it to every source
        AND every VS-03 mode. The invariant is: the SAME (system, code)
        produces the SAME display across $lookup, $validate-code, and $expand
        (explicit concept list mode). A mismatch is silent-wrong-answer at
        the clinical level — a CDS hook enriching the expansion's Coding
        through $lookup would see two different displays for the same code.
        """
        # $lookup display
        lu = _lookup_display(fhir_client, system, code)
        # $validate-code display
        vc = _validate_display(fhir_client, system, code)
        # $expand display (explicit concept list)
        vs = {
            "resourceType": "ValueSet",
            "url": f"http://example.org/vs/vs03-t51-{source_label}-{code}",
            "compose": {"include": [{
                "system": system, "concept": [{"code": code}],
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200, f"expand failed: {s} {b}"
        ex = _contains_displays(b).get((system, code))

        # All three MUST be non-None and agree.
        assert lu is not None, f"$lookup display is None for ({system}, {code})"
        assert vc is not None, f"$validate-code display is None for ({system}, {code})"
        assert ex is not None, f"$expand display is None for ({system}, {code})"
        assert lu == vc == ex, (
            f"3-way canonical-DISPLAY invariant broken for source={source_label} "
            f"code={code}: $lookup={lu!r}, $validate-code={vc!r}, $expand={ex!r}"
        )

    def test_t52_filter_mode_per_source_preferred_term(self, fhir_client):
        """Filter mode (text search) resolves each source's preferred term.

        Per EXPLORER tip: filter mode uses search_names which returns engine
        canonical preferred terms. The probe asserts:
          - filter='diabetes' + system=SNOMED → returns DM (PT) and T2DM (PT)
          - filter='metformin' + system=RxNorm → returns SCD
        No FSN/synonym/obsolete atom leaks.
        """
        # SNOMED filter 'diabetes'
        s, b = _get_expand(fhir_client, params={"filter": "diabetes", "system": SNOMED_URI})
        assert s == 200
        displays = _contains_displays(b)
        if (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in displays:
            assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == CANONICAL_DISPLAY_SNOMED_DM
        if (SNOMED_URI, SNOMED_T2DM) in displays:
            assert displays[(SNOMED_URI, SNOMED_T2DM)] == CANONICAL_DISPLAY_SNOMED_T2DM

        # RxNorm filter 'metformin'
        s2, b2 = _get_expand(fhir_client, params={"filter": "metformin", "system": RXNORM_URI})
        assert s2 == 200
        displays2 = _contains_displays(b2)
        if (RXNORM_URI, RXNORM_METFORMIN) in displays2:
            assert displays2[(RXNORM_URI, RXNORM_METFORMIN)] == CANONICAL_DISPLAY_RXNORM_METFORMIN


# =============================================================================
# Lens 6: CF-TERMINOLOGIST-VS01-01 supplied-display echo resolution status
# (EXPLORER tip)
# Carry-forward status: DEFERRED. The current behavior is: when a client
# SUPPLIES compose.include[].concept[].display, ``_expand_intensional``
# echoes it verbatim (apps/fhir_api.py:2593: ``display = concept.get("display")
# or ""``).
#
# Per CF-TERMINOLOGIST-VS01-01 (MEDIUM — DEFERRED): applying canonical-wins
# requires a display-name canonicalization decision tied to AGENTS.md NOT A
# BUG registry. CS-03 TERMINOLOGIST established the precedent (canonical
# wins over client input for $validate-code Out display). Until the CF is
# closed, these probes document the CURRENT behavior so the CF remains a
# load-bearing contract.
# =============================================================================


class TestLens6CFTerminologistVS01One:
    """Lens 6: CF-TERMINOLOGIST-VS01-01 carry-forward-as-probe (DEFERRED).

    These probes assert the CURRENT (deferred) behavior so when the CF is
    closed (canonical-wins applied), the probe MUST fail loudly. Mirrors
    CS-03 TERMINOLOGIST methodology (carry-forward-as-probe pattern strategy
    56).
    """

    def test_t60_supplied_display_currently_echoed_verbatim(self, fhir_client):
        """CF-TERMINOLOGIST-VS01-01 pin: client-supplied display IS echoed
        verbatim by ``_expand_intensional`` (apps/fhir_api.py:2593).

        Per FHIR R4 §4.9.5 contains[].display "The recommended display for
        this item in the expansion" — implies the SERVER's canonical preferred
        term, NOT a client-supplied echo. The CF tracks this as a DEFERRED
        enhancement tied to a display-name canonicalization decision.

        When the CF is closed, this probe MUST be updated to assert canonical-
        wins (the supplied display is overridden by the engine's canonical
        preferred term).
        """
        wrong_display = "WRONG CLIENT DISPLAY 73211009"
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t60",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [
                    {"code": SNOMED_DIABETES_MELLITUS, "display": wrong_display},
                ],
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        # CURRENT (deferred) behavior: client display echoed verbatim.
        actual = displays.get((SNOMED_URI, SNOMED_DIABETES_MELLITUS))
        assert actual == wrong_display, (
            f"CF-TERMINOLOGIST-VS01-01 pin: client-supplied display should be "
            f"echoed verbatim (current deferred behavior). Got: {actual!r}. "
            f"If this FAILED, the CF may be CLOSED — update to assert canonical-"
            f"wins (actual should be {CANONICAL_DISPLAY_SNOMED_DM!r})."
        )

    def test_t61_supplied_display_overrides_engine_canonical(self, fhir_client):
        """CF-TERMINOLOGIST-VS01-01 pin (mirror): when client supplies a
        non-canonical display AND a known code, the supplied display wins
        over the engine canonical preferred term.

        This is the SECOND pin (carry-forward-as-probe pattern strategy 56):
        pin the deferred behavior on a DIFFERENT code to ensure the CF is
        structurally present (not fixture-specific).
        """
        supplied = "Patient-Friendly T2DM Display"
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t61",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [
                    {"code": SNOMED_T2DM, "display": supplied},
                ],
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        actual = displays.get((SNOMED_URI, SNOMED_T2DM))
        # CURRENT (deferred) behavior: client display echoed, NOT canonical.
        assert actual == supplied, (
            f"CF-TERMINOLOGIST-VS01-01 pin (2nd): supplied display should win "
            f"over canonical. Got: {actual!r}. If this FAILED, the CF may be "
            f"CLOSED — update to assert canonical-wins."
        )

    def test_t62_cf_terminologist_vs01_01_source_read_structural_pin(self):
        """Source-read: ``_expand_intensional`` line 2593 contains the
        ``display = concept.get("display") or ""`` pattern (the load-bearing
        CF-VS01-01 line).

        This is a STRUCTURAL pin: the CF is "open" IFF the source code echoes
        client-supplied display verbatim. When the CF is closed, the source
        will change to apply canonical-wins and this probe MUST fail loudly.
        """
        src = _get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src is not None, "_expand_intensional not found in create_fhir_app"
        # The CF-VS01-01 pattern: client-supplied display takes precedence.
        assert 'concept.get("display")' in src, (
            "CF-TERMINOLOGIST-VS01-01 structural pin: _expand_intensional no "
            "longer uses `concept.get(\"display\")` — the CF may be CLOSED."
        )


# =============================================================================
# Lens 7: Cross-resource clinical consistency
# Spec: FHIR R4 §4.7.3 (value set validation), §4.9.1 (expansion shape)
# Clinical-safety: the canonical-DISPLAY invariant extends across resources
# (CodeSystem $lookup ↔ ValueSet $expand ↔ ValueSet $validate-code). The
# engine MUST produce consistent displays regardless of which operation
# surfaced them.
# =============================================================================


class TestLens7CrossResourceClinicalConsistency:
    """Lens 7: cross-resource clinical consistency.

    The conformance fixture's SNOMED DM / T2DM / ICD-10-CM T2DM / RxNorm
    Metformin are referenced consistently across the FHIR surface. A CDS
    hook passing an expansion's Coding to $lookup or $validate-code MUST
    see the same display.
    """

    def test_t70_lookup_validate_code_agree_on_result_for_known_code(self, fhir_client):
        """$lookup and $validate-code agree on result for a known code.

        $lookup returns 200 + display. $validate-code returns 200 + result=true
        + display. Both displays MUST agree (canonical-DISPLAY invariant).
        """
        for system, code, expected in [
            (SNOMED_URI, SNOMED_DIABETES_MELLITUS, CANONICAL_DISPLAY_SNOMED_DM),
            (ICD10CM_URI, ICD10CM_T2DM, CANONICAL_DISPLAY_ICD10CM_T2DM),
            (RXNORM_URI, RXNORM_METFORMIN, CANONICAL_DISPLAY_RXNORM_METFORMIN),
        ]:
            lu = _lookup_display(fhir_client, system, code)
            vc = _validate_display(fhir_client, system, code)
            assert lu == expected, (
                f"$lookup display mismatch for ({system}, {code}): {lu!r}"
            )
            assert vc == expected, (
                f"$validate-code display mismatch for ({system}, {code}): {vc!r}"
            )

    def test_t71_expand_advertised_canonical_uri_no_alias(self, fhir_client):
        """Expansion contains[].system is the canonical FHIR URI, not an
        alias input (trailing-slash, urn:oid, etc.).

        Per CR-013 (milestone-2 review) + CF-HISTORIAN-VS02-02 (RESOLVED):
        ``_expand_intensional`` re-resolves inc_system through
        ``canonical_system_uri``. A client POSTing with an alias URI MUST get
        back the canonical URI in contains[].system. This is the 9th-instance
        client-input-as-canonical drift pattern (PROMOTED).
        """
        alias_uri = SNOMED_URI + "/"  # trailing-slash alias
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t71",
            "compose": {"include": [{
                "system": alias_uri,
                "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        for sys_uri, _ in _contains_codes(b):
            assert sys_uri == SNOMED_URI, (
                f"contains[].system is alias {sys_uri!r}, expected canonical "
                f"{SNOMED_URI!r}"
            )

    def test_t72_expand_advertised_canonical_uri_via_urn_oid(self, fhir_client):
        """Expansion contains[].system is canonical even via urn:oid alias.

        Per CF-HISTORIAN-VS02-02 (RESOLVED) + CR-013: the canonical_system_uri
        helper re-resolves urn:oid aliases to the canonical FHIR URI.
        """
        urn_oid = "urn:oid:2.16.840.1.113883.6.96"  # SNOMED CT OID
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t72",
            "compose": {"include": [{
                "system": urn_oid,
                "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200, f"urn:oid alias failed: {s} {b}"
        for sys_uri, _ in _contains_codes(b):
            assert sys_uri == SNOMED_URI, (
                f"contains[].system is urn:oid {sys_uri!r}, expected canonical "
                f"{SNOMED_URI!r}"
            )


# =============================================================================
# Lens 8: CF-TERMINOLOGIST-VS02-04 unknown code display fallback (RESOLVED)
# Per VS-02 TERMINOLOGIST QA-001 RESOLVED: an unknown code in an explicit
# concept list falls back to the code string (so the entry has a non-empty
# "recommended display" per FHIR R4 §4.9.1). This lens reconfirms the
# RESOLVED behavior via clinical-correctness probes.
# =============================================================================


class TestLens8CFTerminologistVS02FourResolved:
    """Lens 8: CF-TERMINOLOGIST-VS02-04 RESOLVED verification.

    Per VS-02 TERMINOLOGIST QA-001 RESOLVED: an unknown code in an explicit
    concept list MUST fall back to the code string (NOT empty display) so
    the entry has a non-empty "recommended display" per FHIR R4 §4.9.1.

    Clinical-safety: an empty display for an unknown code is silent-wrong-
    answer (a CDS hook reading the expansion sees an empty string and may
    silently drop the entry). The fallback ensures the entry is at least
    identifiable by its code.
    """

    def test_t80_unknown_code_falls_back_to_code_string(self, fhir_client):
        """Unknown SNOMED code → contains[].display == code string.

        Per CF-TERMINOLOGIST-VS02-04 RESOLVED: the 3rd-tier fallback in
        ``_expand_intensional`` (apps/fhir_api.py:2613) sets
        ``display = code_str`` when get_code_infos returns empty.
        """
        unknown_code = "99999999"  # not in fixture
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t80",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": unknown_code}],  # display omitted
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        actual = displays.get((SNOMED_URI, unknown_code))
        assert actual == unknown_code, (
            f"unknown code display should fall back to code string "
            f"{unknown_code!r}; got {actual!r}. CF-TERMINOLOGIST-VS02-04 "
            f"RESOLVED contract is broken."
        )

    def test_t81_unknown_code_display_not_empty(self, fhir_client):
        """Unknown code → display is non-empty (clinical-safety)."""
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t81",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": "99999999"}],
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        actual = displays.get((SNOMED_URI, "99999999"))
        assert actual, (
            f"unknown code display is empty/missing — CF-TERMINOLOGIST-VS02-04 "
            f"3rd-tier fallback broken."
        )


# =============================================================================
# Lens 9: Source-read structural contracts for canonical-DISPLAY invariant
# These probes verify the LOAD-BEARING code paths that ensure canonical-DISPLAY
# agreement across operations. They are the structural layer under Lens 1's
# behavioral probes.
# =============================================================================


class TestLens9SourceReadStructuralContracts:
    """Lens 9: source-read contracts for canonical-DISPLAY invariant.

    Behavioral probes (Lens 1) verify the OUTPUT; source-read probes verify
    the STRUCTURAL conditions that produce the output. Together they form a
    defense-in-depth contract: a future regression that breaks the structural
    contract fails BOTH layers.
    """

    def test_t90_canonical_system_uri_imported(self):
        """canonical_system_uri is imported into fhir_api.py."""
        src = _read_module_source()
        assert "from medterm4ds.engines.fhir import" in src or \
               "canonical_system_uri" in src, (
            "canonical_system_uri not imported in fhir_api.py"
        )
        # Stronger: it's imported from engines.fhir (canonical location).
        assert "canonical_system_uri" in src, (
            "canonical_system_uri helper not referenced in fhir_api.py"
        )

    def test_t91_expand_intensional_calls_canonical_system_uri(self):
        """_expand_intensional calls canonical_system_uri (CR-013 9th-instance
        of client-input-as-canonical drift pattern)."""
        src = _get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src is not None
        assert "canonical_system_uri(" in src, (
            "_expand_intensional does not call canonical_system_uri — CR-013 "
            "regression risk: contains[].system may echo client alias input."
        )

    def test_t92_expand_intensional_uses_canonical_inc_in_contains(self):
        """_expand_intensional uses canonical_inc (NOT inc_system) in
        contains.append for the explicit concept list path."""
        src = _get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src is not None
        # The load-bearing line: contains.append({..., "system": canonical_inc, ...})
        assert '"system": canonical_inc' in src, (
            "_expand_intensional does not use canonical_inc in contains.append "
            "for the explicit concept list path — CR-013 regression risk."
        )

    def test_t93_expand_intensional_uses_canonical_inc_in_is_a_root(self):
        """_expand_intensional uses canonical_inc in the is-a root contains.append."""
        src = _get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src is not None
        # Count occurrences — should be >= 3 (explicit concept list, is-a root,
        # descendant loop).
        occurrences = src.count('"system": canonical_inc')
        assert occurrences >= 3, (
            f"_expand_intensional uses canonical_inc only {occurrences} times; "
            f"expected >= 3 (explicit concept list + is-a root + descendant loop)."
        )

    def test_t94_expand_implicit_value_set_calls_canonical_system_uri(self):
        """_expand_implicit_value_set calls canonical_system_uri (CF-HISTORIAN-
        VS02-02 RESOLVED)."""
        src = _get_nested_func_source("create_fhir_app", "_expand_implicit_value_set")
        assert src is not None
        assert "canonical_system_uri(" in src, (
            "_expand_implicit_value_set does not call canonical_system_uri — "
            "CF-HISTORIAN-VS02-02 RESOLVED regression risk."
        )

    def test_t95_filter_mode_uses_engine_name_for_display(self):
        """Filter mode uses r.name (engine canonical preferred term) for
        contains[].display — NOT a raw code or empty string."""
        src = _get_nested_func_source("create_fhir_app", "_do_expand")
        assert src is not None
        # The load-bearing line: contains.append({..., "display": r.name, ...})
        assert '"display": r.name' in src, (
            "_do_expand filter mode does not use r.name for display — "
            "canonical-DISPLAY invariant regression risk on filter mode."
        )

    def test_t96_isinstance_guards_present_in_expand_intensional(self):
        """10th PROMOTED pattern: _expand_intensional has >= 5 isinstance
        guards (compose/include/concept/filter/exclude)."""
        src = _get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src is not None
        # Count isinstance() calls in the function source.
        occurrences = src.count("isinstance(")
        assert occurrences >= 5, (
            f"_expand_intensional has only {occurrences} isinstance guards; "
            f"expected >= 5 (10th PROMOTED pattern)."
        )

    def test_t97_build_valueset_expand_has_total_param(self):
        """build_valueset_expand signature has total: int | None = None
        (VS-02 SKEPTIC QA-057 PROMOTED count=3)."""
        # build_valueset_expand is in responses.py, not fhir_api.py.
        responses_path = Path(inspect.getsourcefile(
            __import__("medterm4ds.engines.fhir.responses", fromlist=["responses"])
        ))
        src = responses_path.read_text()
        # Find the function definition.
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "build_valueset_expand":
                func_src = ast.get_source_segment(src, node) or ""
                assert "total: int | None = None" in func_src, (
                    "build_valueset_expand signature lacks `total: int | None = None` "
                    "— VS-02 SKEPTIC QA-057 PROMOTED pattern regression."
                )
                return
        pytest.fail("build_valueset_expand not found in responses.py")

    def test_t98_filter_operator_enum_imported_from_canonical(self):
        """FHIR_R4_FILTER_OPERATORS is imported from engines.fhir (canonical
        location per GLOBAL_RULES.md single-source-of-truth)."""
        # The constant is imported at top of this file — verify it's the
        # spec-correct 9-value set.
        assert FHIR_R4_FILTER_OPERATORS == frozenset({
            "=", "is-a", "descendent-of", "is-not-a", "regex",
            "in", "not-in", "generalizes", "exists",
        }), f"FHIR_R4_FILTER_OPERATORS changed: {FHIR_R4_FILTER_OPERATORS}"

    def test_t99_canonical_system_uri_helper_callable(self):
        """canonical_system_uri helper is callable and returns canonical URIs
        for alias inputs."""
        # Trailing-slash SNOMED alias → canonical
        assert canonical_system_uri(SNOMED_URI + "/") == SNOMED_URI
        # urn:oid SNOMED alias → canonical
        assert canonical_system_uri("urn:oid:2.16.840.1.113883.6.96") == SNOMED_URI
        # Canonical → canonical (idempotent)
        assert canonical_system_uri(SNOMED_URI) == SNOMED_URI
        # ICD-10-CM canonical
        assert canonical_system_uri(ICD10CM_URI) == ICD10CM_URI


# =============================================================================
# Lens 10: Clinical safety no-silent-wrong-answer (TERMINOLOGIST meta-pattern)
# These probes verify that NO mode produces a silent-wrong-answer at the
# clinical level. A silent-wrong-answer is when the engine returns 200 + a
# clinically misleading display/code/result instead of an explicit error.
# =============================================================================


class TestLens10ClinicalSafetyNoSilentWrongAnswer:
    """Lens 10: clinical safety — no silent-wrong-answer on VS-03 surface.

    Per TS-02 TERMINOLOGIST methodology (strategy 58): for each $expand mode,
    verify no silent-wrong-answer on missing/unknown/malformed data. The
    clinical-correctness dimension: the engine MUST surface the right signal
    (error, empty expansion, fallback display) rather than a misleading
    successful-looking response.
    """

    def test_t100_unknown_code_in_explicit_list_signals_via_display(self, fhir_client):
        """Unknown code in explicit concept list: the entry surfaces with
        display=code (fallback), NOT a silent 200 + empty display.

        This is the CF-TERMINOLOGIST-VS02-04 RESOLVED contract — the 3rd-tier
        fallback ensures the entry is identifiable by its code, NOT silently
        empty.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t100",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [{"code": "99999999"}],  # unknown; display omitted
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        displays = _contains_displays(b)
        actual = displays.get((SNOMED_URI, "99999999"))
        # Clinical-safety: display is the code string (NOT empty, NOT a
        # fabricated clinical term).
        assert actual == "99999999", (
            f"unknown code display should fall back to code (clinical-safety); "
            f"got {actual!r}"
        )

    def test_t101_is_a_on_nonexistent_root_returns_no_descendants(self, fhir_client):
        """is-a on a nonexistent root produces an empty expansion (no
        descendants fabricated)."""
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t101",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "is-a", "value": "99999999"}
                ],
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200, f"is-a on nonexistent root failed: {s} {b}"
        codes = _contains_codes(b)
        assert codes == [], (
            f"is-a on nonexistent root produced non-empty expansion: {codes}. "
            f"Clinical-safety: no descendants should be fabricated."
        )

    def test_t102_filter_no_matches_returns_empty_expansion(self, fhir_client):
        """Filter with no matches produces an empty expansion (no fabricated
        matches)."""
        s, b = _get_expand(fhir_client, params={
            "filter": "zzzznomatch", "system": SNOMED_URI,
        })
        assert s == 200, f"filter no-match failed: {s} {b}"
        codes = _contains_codes(b)
        assert codes == [], (
            f"filter with no matches produced non-empty expansion: {codes}. "
            f"Clinical-safety: no fabricated matches."
        )

    def test_t103_offspec_filter_op_silently_dropped(self, fhir_client):
        """Off-spec filter operator (e.g. '=') is silently dropped → empty
        expansion (CF-SKEPTIC-VS01-01 pin).

        The engine accepts only 'is-a' and 'descendent-of' today; the other 7
        spec-defined operators are silently dropped (debug log). The result
        MUST be an empty expansion, NOT a fabricated match.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-t103",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "=", "value": SNOMED_DIABETES_MELLITUS}
                ],
            }]},
        }
        s, b = _post_expand(fhir_client, vs)
        assert s == 200
        codes = _contains_codes(b)
        assert codes == [], (
            f"off-spec '=' operator produced non-empty expansion: {codes}. "
            f"Clinical-safety: no fabricated matches on unsupported operator."
        )

    def test_t104_implicit_value_set_unknown_system_returns_400(self, fhir_client):
        """Implicit value set with unknown system URI returns 400 (not a
        silent 200 with fabricated codes)."""
        s, b = _get_expand(fhir_client, params={
            "url": "http://unknown.example.org/vs",
        })
        # The engine should reject the unknown system URI.
        assert s in (200, 400), (
            f"implicit value set unknown system produced unexpected status: "
            f"{s} {b}"
        )
        if s == 200:
            # If 200, the expansion MUST be empty (no fabricated codes).
            codes = _contains_codes(b)
            assert codes == [], (
                f"implicit value set with unknown system produced non-empty "
                f"expansion: {codes}. Clinical-safety: no fabricated codes."
            )

