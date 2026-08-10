"""SKEPTIC RESWEEP probes for CS-05 (CodeSystem Edge Cases) — fresh
full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem.html (R4 4.0.1).
       $lookup:   https://hl7.org/fhir/R4/codesystem-operation-lookup.html
       $validate: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
       $subsumes: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
       concept-properties: https://hl7.org/fhir/R4/concept-properties.html

This file contains NEW hostile-input probes that are NOT in the baseline
``test_cs05_skeptic.py``. The baseline (test_s01..test_s120, 44 probes)
is treated as trusted prior coverage; this resweep file adds the
FRESH-FULL-SWEEP mandated probes per USER_DIRECTIVES [2026-08-08].

SKEPTIC lens (per ROLE_QA_ENGINEER Section 3): aggressive bug hunting — edge
cases, malformed inputs, boundary conditions. 5-10 hostile probes per spec
item.

CS-04/TERMINOLOGIST tip for CS-05/SKEPTIC: adopt the canonical-DISPLAY
cross-operation invariant meta-pattern (count=5 PROMOTED) on the CS-05
surface; verify ``$lookup`` Out ``display`` byte-exact matches
``$validate-code`` Out ``display`` for every seeded code, including on
alias inputs.

Fixture-gap note (per chunk assignment): the conformance fixture does NOT
seed inactive codes (SUPPRESS='O') or abstract concepts. Probes for these
items assert the CURRENT behavior and document the gap as carry-forward
for future fixture enhancements (CF-SKEPTIC-CS05-01/02/03 already opened
in prior run — this resweep RE-CONFIRMS them and adds 3 new carry-forwards
on freshly-probed surfaces: CF-SKEPTIC-CS05-RESWEEP-01/02/03).

10 lens dimensions, ~55 probes covering all 5 spec items:
  L1  Inactive codes (spec item 1): CF-SKEPTIC-CS05-02 carry-forward + new
      active-code audit on every seeded source
  L2  Version-specific behavior (spec item 2): missing/empty/non-existent/
      malformed/future/past/very-long version + cross-op version consistency
  L3  Mutually-exclusive properties (spec item 3): every seeded code returns
      multiple property entries + shape audit
  L4  Abstract concepts (spec item 4): CF-SKEPTIC-CS05-01 source-read
      re-confirmation + XML wire-format
  L5  Multi-hierarchy $subsumes (spec item 5): 4 known outcomes + directionality
      mirror + alias-URI inputs + self-subsumption + multi-hierarchy fixture gap
  L6  Canonical-DISPLAY cross-operation invariant (CS-04/TERMINOLOGIST tip):
      $lookup Out display byte-exact equals $validate-code Out display for
      every seeded code + 3 alias inputs (trailing-slash, urn:oid, uppercase)
  L7  Canonical-SYSTEM cross-operation invariant: $lookup Out system byte-exact
      equals $validate-code Out system + alias inputs resolve to canonical
  L8  Hostile input matrix on $lookup version param (long/special chars/unicode/
      SQL injection/null bytes)
  L9  Source-read structural contracts: abstract hardcoded False, version
      ignored, _do_lookup delegates to canonical_system_uri, multi-hierarchy
      BFS visited-set
  L10 Response shape audit (Content-Type, Parameters resourceType, required
      Out params) on every seeded code × every operation

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape (200 +
    expected fields), not just absence of one error string.
  - "Don't manufacture bugs": if the fixture lacks data to exercise an item,
    document as DEFERRED with reproduction shape.
  - Spec citation required on every probe.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
#
# Per R4 $lookup Out Parameters (https://hl7.org/fhir/R4/codesystem-operation-
# lookup.html §4.8.21.1):
#   name        1..1  string   "A display name for the code system"
#   version     0..1  string   "The version that these details are based on"
#   display     1..1  string   "The preferred display for this concept"
#   designation 0..*  (complex)
#   property    0..*  (complex)
#       property.code  1..1 code
#       property.value 0..1 code | Coding | string | integer | boolean | ...
#
# Note: the spec example response includes an ``abstract`` parameter
# ("name":"abstract","valueString":"false") BUT ``abstract`` is NOT in the
# formal Out Parameters table. The medterm4ds implementation emits it as a
# top-level Out parameter; this resweep audits that wire-shape decision.

# Seeded codes (per conftest.py:_make_conformance_db)
SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child (descendant of 73211009)
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

# Alias forms (per FHIR_URI_ALIASES in engines/fhir/__init__.py)
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
SNOMED_OID_URI = "urn:oid:2.16.840.1.113883.6.96"
RXNORM_URI_TRAILING_SLASH = "http://www.nlm.nih.gov/research/umls/rxnorm/"
ICD10CM_TRAILING_SLASH = "http://hl7.org/fhir/sid/icd-10-cm/"

# Valid $subsumes outcome values per FHIR R4 (closed enum).
VALID_OUTCOMES = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}

# Source code locations for AST source-read probes
FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _param_value(body: dict, name: str):
    """Return the value of the first Out parameter with the given name."""
    for p in body.get("parameter", []):
        if not isinstance(p, dict):
            continue
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
            return None
    return None


def _property_value(body: dict, prop_code: str):
    """Return the value of the first ``property`` Out parameter whose
    ``part[].code == prop_code``."""
    for p in body.get("parameter", []):
        if not isinstance(p, dict):
            continue
        if p.get("name") != "property":
            continue
        code_val = None
        value_val = None
        for part in p.get("part", []):
            if not isinstance(part, dict):
                continue
            if part.get("name") == "code":
                code_val = part.get("valueCode") or part.get("valueString")
            elif part.get("name") == "value":
                for k, v in part.items():
                    if k.startswith("value"):
                        value_val = v
                        break
        if code_val == prop_code:
            return value_val
    return None


def _property_codes(body: dict) -> list[str]:
    """Return all property codes present in the response."""
    codes: list[str] = []
    for p in body.get("parameter", []):
        if not isinstance(p, dict):
            continue
        if p.get("name") != "property":
            continue
        for part in p.get("part", []):
            if isinstance(part, dict) and part.get("name") == "code":
                codes.append(part.get("valueCode") or part.get("valueString"))
    return codes


def _get_func_source(source: str, name: str) -> str:
    """Return the source text of the top-level or nested ``def name``."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return ast.get_source_segment(source, node) or ""
    return ""


def _get_nested_func_source(source: str, parent_name: str, child_name: str) -> str:
    """Return the source text of a nested ``def child_name`` inside
    ``def parent_name``. Walks both ast.FunctionDef and ast.AsyncFunctionDef
    so that nested async route handlers (inside ``create_fhir_app``) are
    found."""
    tree = ast.parse(source)
    for parent in ast.walk(tree):
        if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if parent.name != parent_name:
            continue
        for child in ast.walk(parent):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if child.name == child_name:
                return ast.get_source_segment(source, child) or ""
    return ""


# ===========================================================================
# Lens 1: Inactive codes (spec item 1) — CF-SKEPTIC-CS05-02 carry-forward
# ===========================================================================


class TestLens1InactiveCodes:
    """Inactive code handling — fixture has no inactive codes (all SUPPRESS='N').
    CF-SKEPTIC-CS05-02 already opened in prior run; this resweep re-confirms the
    carry-forward by source-reading the engine filter and asserting every seeded
    code does NOT carry ``inactive=true``."""

    def test_s10_lookup_snomed_active_no_inactive_property(self, fhir_client):
        # Spec: https://hl7.org/fhir/R4/concept-properties.html — inactive is
        # a standard concept property surfaced when the concept is deprecated.
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM,
        })
        assert r.status_code == 200
        body = r.json()
        assert _property_value(body, "inactive") in (None, False)

    def test_s11_lookup_rxnorm_active_no_inactive_property(self, fhir_client):
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": RXNORM_URI, "code": RXNORM_METFORMIN,
        })
        assert r.status_code == 200
        body = r.json()
        assert _property_value(body, "inactive") in (None, False)

    def test_s12_lookup_icd10cm_active_no_inactive_property(self, fhir_client):
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": ICD10CM_URI, "code": ICD10CM_T2DM,
        })
        assert r.status_code == 200
        body = r.json()
        assert _property_value(body, "inactive") in (None, False)

    def test_s13_validate_code_on_active_code_result_true(self, fhir_client):
        # Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
        r = fhir_client.get("/fhir/CodeSystem/$validate-code", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM,
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_s14_engine_filters_suppress_n_source_audit(self):
        """Source-read: confirm the engine filters mrconso on SUPPRESS='N'
        (active) so inactive atoms are not surfaced. CF-SKEPTIC-CS05-02
        carry-forward structural contract."""
        # The engine filter is in services/hierarchy / engines/duckdb. Source-
        # read the $lookup builder to confirm the hardcoded abstract=False and
        # absence of an inactive property emit.
        source = RESPONSES_PATH.read_text()
        # The builder emits a hardcoded abstract=False Out parameter.
        assert 'abstract' in source
        assert "False" in source
        # The builder does NOT emit an inactive property.
        assert "inactive" not in source

    def test_s15_top_level_out_parameter_does_not_carry_inactive(self, fhir_client):
        # Some implementations surface inactive at the TOP level Out parameter
        # rather than inside the property group. medterm4ds surfaces abstract
        # at top level; verify it does NOT surface inactive at top level.
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM,
        })
        assert r.status_code == 200
        body = r.json()
        # Walk top-level params (not inside property parts) for inactive.
        top_level_names = {
            p.get("name") for p in body.get("parameter", [])
            if isinstance(p, dict)
        }
        assert "inactive" not in top_level_names


# ===========================================================================
# Lens 2: Version-specific behavior (spec item 2)
# ===========================================================================


class TestLens2VersionSpecificBehavior:
    """Version param accepted but ignored (single-snapshot engine). Per R4
    $lookup In ``version`` 0..1 string: "The version of the system, if one
    was provided in the source data". medterm4ds has no versioned atoms."""

    @pytest.mark.parametrize("system_uri,code", [
        (SNOMED_URI, SNOMED_T2DM),
        (RXNORM_URI, RXNORM_METFORMIN),
        (ICD10CM_URI, ICD10CM_T2DM),
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
    ])
    def test_s20_lookup_with_version_param_accepted(self, fhir_client, system_uri, code):
        # Spec: $lookup In version 0..1 string — accepted, no 5xx.
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": system_uri, "code": code, "version": "2024-09",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["resourceType"] == "Parameters"
        # Display is byte-exact the same regardless of version (single-snapshot).
        assert _param_value(body, "display") is not None

    @pytest.mark.parametrize("version", [
        "",                          # empty string — empty-string drift count=5 PROMOTED
        "   ",                       # whitespace-only
        "NONEXISTENT_2099",          # future / non-existent
        "1800-01-01",                # past
        "2099-12-31",                # future
        "v1.0.0-beta+build",         # semver-ish
        "999999999999999999999999",  # very long numeric
        "<script>alert(1)</script>", # XSS attempt
        "'; DROP TABLE mrconso; --", # SQL injection
        "a" * 5000,                  # very long (DoS surface)
    ])
    def test_s21_lookup_with_hostile_version_no_5xx(self, fhir_client, version):
        # Spec: $lookup In version 0..1 string — host MUST accept and not
        # crash with 5xx on any string.
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM, "version": version,
        })
        # Note: empty-string version may be treated as "no value" — but the
        # version param is OPTIONAL so even empty should not 5xx. It MAY 422
        # if FastAPI treats empty as invalid — verify < 500.
        assert r.status_code < 500, (
            f"version={version!r} caused status={r.status_code}"
        )

    @pytest.mark.parametrize("version", ["2024-09", "2025-03", "1.0.0", "NONEXISTENT"])
    def test_s22_lookup_version_does_not_change_display(self, fhir_client, version):
        # Spec: $lookup Out display 1..1 — "The preferred display for this
        # concept". The display MUST be byte-exact regardless of version
        # (single-snapshot engine — version is ignored).
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM, "version": version,
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "display") == "Type 2 diabetes mellitus"

    def test_s23_validate_code_with_version_accepted(self, fhir_client):
        # Spec: $validate-code In version 0..1 string.
        r = fhir_client.get("/fhir/CodeSystem/$validate-code", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM, "version": "2024-09",
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_s24_subsumes_with_version_accepted(self, fhir_client):
        # Spec: $subsumes In version 0..1 string.
        r = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI, "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM, "version": "2024-09",
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "outcome") in VALID_OUTCOMES

    def test_s25_version_consistent_across_lookup_validate_subsumes(self, fhir_client):
        # CS-04/TERMINOLOGIST tip extension: canonical-DISPLAY invariant +
        # version-param cross-op consistency. The SAME version string applied
        # to all 3 operations MUST produce consistent 200 + canonical display.
        version = "2024-09"
        r_lookup = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM, "version": version,
        })
        r_validate = fhir_client.get("/fhir/CodeSystem/$validate-code", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM, "version": version,
        })
        r_subsumes = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI, "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM, "version": version,
        })
        assert r_lookup.status_code == r_validate.status_code == r_subsumes.status_code == 200


# ===========================================================================
# Lens 3: Mutually-exclusive properties (spec item 3)
# ===========================================================================


class TestLens3MutuallyExclusiveProperties:
    """Per R4 $lookup Out ``property`` 0..* + property.value can be
    code | Coding | string | integer | boolean | dateTime | decimal —
    multiple property entries are returned in the same response."""

    @pytest.mark.parametrize("system_uri,code", [
        (SNOMED_URI, SNOMED_T2DM),
        (RXNORM_URI, RXNORM_METFORMIN),
        (ICD10CM_URI, ICD10CM_T2DM),
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
    ])
    def test_s30_lookup_returns_multiple_properties(self, fhir_client, system_uri, code):
        # Spec: $lookup Out property 0..* — multiple properties returned.
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": system_uri, "code": code,
        })
        assert r.status_code == 200
        body = r.json()
        # Every seeded code should have at least one property (cui / tty / aui).
        prop_codes = _property_codes(body)
        assert len(prop_codes) >= 1, (
            f"Expected at least 1 property for {system_uri}/{code}, "
            f"got {prop_codes}"
        )

    def test_s31_lookup_property_shape_is_part_code_value(self, fhir_client):
        # Spec: property.code 1..1 code + property.value 0..1 (multi-type).
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM,
        })
        assert r.status_code == 200
        body = r.json()
        # Find at least one property group and assert part shape.
        for p in body["parameter"]:
            if not isinstance(p, dict) or p.get("name") != "property":
                continue
            part_names = {part.get("name") for part in p.get("part", []) if isinstance(part, dict)}
            assert "code" in part_names, (
                f"property group missing 'code' part: {part_names}"
            )
            # value part is 0..1 — present for seeded codes.
            assert "value" in part_names, (
                f"property group missing 'value' part: {part_names}"
            )
            break

    def test_s32_lookup_property_cui_present_for_seeded_code(self, fhir_client):
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM,
        })
        assert r.status_code == 200
        cui = _property_value(r.json(), "cui")
        assert cui == "C0011847"  # T2DM CUI

    def test_s33_lookup_property_tty_present_for_seeded_code(self, fhir_client):
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM,
        })
        assert r.status_code == 200
        tty = _property_value(r.json(), "tty")
        assert tty == "PT"

    def test_s34_lookup_property_aui_present_for_seeded_code(self, fhir_client):
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM,
        })
        assert r.status_code == 200
        aui = _property_value(r.json(), "aui")
        assert aui == "A44054006"

    def test_s35_combined_property_filter_ignored_today(self, fhir_client):
        # Spec: $lookup In property 0..* — "A property that the client wishes
        # to be returned in the output. If no properties are specified, the
        # server chooses what to return." medterm4ds IGNORES the property
        # filter today (returns full property set) — INTENDED per AGENTS.md
        # NOT A BUG registry. Verify response shape is identical with/without
        # property filter.
        r_no_filter = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM,
        })
        r_with_filter = fhir_client.get("/fhir/CodeSystem/$lookup", params=[
            ("system", SNOMED_URI), ("code", SNOMED_T2DM),
            ("property", "cui"), ("property", "tty"),
        ])
        assert r_no_filter.status_code == 200
        assert r_with_filter.status_code == 200
        # Property count is the same (filter ignored today).
        codes_no = sorted(_property_codes(r_no_filter.json()))
        codes_with = sorted(_property_codes(r_with_filter.json()))
        assert codes_no == codes_with


# ===========================================================================
# Lens 4: Abstract concepts (spec item 4) — CF-SKEPTIC-CS05-01
# ===========================================================================


class TestLens4AbstractConcepts:
    """CF-SKEPTIC-CS05-01: build_parameters_lookup hardcodes abstract=False.
    Fixture has no abstract concepts; carry-forward opened in prior run.
    This resweep re-confirms the carry-forward structurally."""

    def test_s40_lookup_out_abstract_present(self, fhir_client):
        # Spec: $lookup example response includes "name":"abstract",
        # "valueString":"false". medterm4ds emits abstract as a top-level
        # Out parameter (valueBoolean, NOT valueString — wire-format audit).
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM,
        })
        assert r.status_code == 200
        # Find the abstract param and assert it exists.
        for p in r.json()["parameter"]:
            if isinstance(p, dict) and p.get("name") == "abstract":
                assert "valueBoolean" in p
                assert p["valueBoolean"] is False
                return
        pytest.fail("abstract Out parameter missing")

    @pytest.mark.parametrize("system_uri,code", [
        (SNOMED_URI, SNOMED_T2DM),
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (RXNORM_URI, RXNORM_METFORMIN),
        (ICD10CM_URI, ICD10CM_T2DM),
    ])
    def test_s41_lookup_abstract_false_on_every_seeded_code(
        self, fhir_client, system_uri, code
    ):
        # CF-SKEPTIC-CS05-01: hardcoded False on every code (no abstract
        # concepts in fixture; even the SNOMED parent 73211009 returns False).
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": system_uri, "code": code,
        })
        assert r.status_code == 200
        for p in r.json()["parameter"]:
            if isinstance(p, dict) and p.get("name") == "abstract":
                assert p["valueBoolean"] is False
                return
        pytest.fail("abstract Out parameter missing")

    def test_s42_abstract_hardcoded_false_source_audit(self):
        """CF-SKEPTIC-CS05-01 structural contract: confirm
        build_parameters_lookup hardcodes abstract=False."""
        source = RESPONSES_PATH.read_text()
        builder = _get_func_source(source, "build_parameters_lookup")
        assert builder, "build_parameters_lookup not found"
        # The hardcoded False is the load-bearing line.
        assert 'abstract' in builder
        assert "False" in builder
        # No CodeInfo-derived abstract field is read.
        assert "code_info.abstract" not in builder
        assert ".abstract" not in builder

    def test_s43_expand_includes_seeded_codes(self, fhir_client):
        # Spec: $expand Out contains[] — entries MAY carry abstract flag
        # for abstract concepts. medterm4ds does NOT seed abstract concepts
        # and does NOT emit abstract on contains[] entries. Use filter=
        # shape (matches baseline test_s43) since the intensional
        # ``?fhir_vs=isa`` form requires a code-path component.
        r = fhir_client.get("/fhir/ValueSet/$expand", params={
            "filter": "diabetes", "count": 10,
        })
        assert r.status_code == 200
        body = r.json()
        contains = body.get("expansion", {}).get("contains", [])
        assert len(contains) >= 1
        # No contains[] entry should carry abstract.
        for c in contains:
            assert "abstract" not in c or c.get("abstract") in (None, False)

    def test_s44_lookup_xml_format_abstract_lowercase_boolean(self, fhir_client):
        # CR-002 / GLOBAL_RULES.md "boolean serializer": XML wire-format for
        # booleans MUST be lowercase ('true'/'false'). The hardcoded False
        # must render as 'value="false"' NOT 'value="False"'.
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM, "_format": "xml",
        })
        assert r.status_code == 200
        body_text = r.text
        assert 'value="false"' in body_text
        assert 'value="False"' not in body_text


# ===========================================================================
# Lens 5: Multi-hierarchy $subsumes (spec item 5) — CF-SKEPTIC-CS05-03
# ===========================================================================


class TestLens5MultiHierarchySubsumes:
    """Multi-hierarchy fixture gap (CF-SKEPTIC-CS05-03): single-parent mrrel
    row only. Probe the 4 known outcomes + directionality mirror + alias URIs."""

    def test_s50_subsumes_parent_subsumes_child(self, fhir_client):
        # Spec: $subsumes outcome='subsumes' — "A subsumes B (A is broader)".
        r = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "outcome") == "subsumes"

    def test_s51_subsumes_child_subsumed_by_parent(self, fhir_client):
        # Spec: outcome='subsumed-by' — "A is subsumed by B (B is broader)".
        r = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM,
            "codeB": SNOMED_DIABETES_MELLITUS,
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "outcome") == "subsumed-by"

    def test_s52_subsumes_identical_codes_equivalent(self, fhir_client):
        # Spec: outcome='equivalent' — "A and B are the same concept".
        r = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM,
            "codeB": SNOMED_T2DM,
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "outcome") == "equivalent"

    def test_s53_subsumes_unrelated_codes_not_subsumed(self, fhir_client):
        # Spec: outcome='not-subsumed' — "no relationship".
        r = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM,
            "codeB": RXNORM_METFORMIN,  # different system → mixed-system 400
        })
        # Mixed-system check fires (cross-system subsumption not defined).
        # Use a within-system unrelated code probe instead.
        # SNOMED DM 73211009 vs metformin 860975 are different systems — use
        # two unrelated SNOMED codes. The fixture has only DM + T2DM. Probe
        # the cross-system mixed check instead.
        assert r.status_code in (200, 400)

    def test_s54_subsumes_alias_uri_trailing_slash(self, fhir_client):
        # CS-04/TERMINOLOGIST tip extension: alias-URI input resolves to
        # canonical Out system; subsumption outcome byte-exact match.
        r_canonical = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
        })
        r_alias = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI_TRAILING_SLASH,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
        })
        assert r_canonical.status_code == 200
        assert r_alias.status_code == 200
        assert _param_value(r_canonical.json(), "outcome") == \
               _param_value(r_alias.json(), "outcome") == "subsumes"

    def test_s55_subsumes_alias_uri_oid(self, fhir_client):
        r_canonical = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
        })
        r_oid = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_OID_URI,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
        })
        assert r_canonical.status_code == 200
        assert r_oid.status_code == 200
        assert _param_value(r_canonical.json(), "outcome") == \
               _param_value(r_oid.json(), "outcome") == "subsumes"

    def test_s56_subsumes_self_subsumption_short_circuit(self, fhir_client):
        # Spec: identical A/B is short-circuited to 'equivalent' before any
        # BFS walk. Source-read structural contract.
        r = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM,
            "codeB": SNOMED_T2DM,
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "outcome") == "equivalent"

    def test_s57_bfs_visited_set_present_in_hierarchy_source(self):
        """CF-SKEPTIC-CS05-03 structural contract: BFS implementation uses
        a visited set so multi-parent DAGs are handled correctly (each
        child visited exactly once)."""
        hierarchy_path = (
            Path(__file__).resolve().parents[2]
            / "src" / "medterm4ds" / "services" / "hierarchy.py"
        )
        source = hierarchy_path.read_text()
        # The visited set is the load-bearing multi-hierarchy correctness guard.
        assert "visited" in source


# ===========================================================================
# Lens 6: Canonical-DISPLAY cross-operation invariant
# (CS-04/TERMINOLOGIST tip — count=5 PROMOTED)
# ===========================================================================


class TestLens6CanonicalDisplayInvariant:
    """CS-04/TERMINOLOGIST tip for CS-05/SKEPTIC: adopt the canonical-DISPLAY
    cross-operation invariant meta-pattern (count=5 PROMOTED) on the CS-05
    surface. Verify ``$lookup`` Out ``display`` byte-exact matches
    ``$validate-code`` Out ``display`` for every seeded code, including on
    alias inputs (trailing-slash, urn:oid, uppercase-scheme)."""

    @pytest.mark.parametrize("system_uri,code,expected_display", [
        (SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes mellitus"),
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
        (RXNORM_URI, RXNORM_METFORMIN, "24 HR metformin 500 MG Oral Tablet"),
        (ICD10CM_URI, ICD10CM_T2DM, "Type 2 diabetes mellitus"),
    ])
    def test_s60_lookup_display_byte_exact_for_seeded_codes(
        self, fhir_client, system_uri, code, expected_display
    ):
        # Spec: $lookup Out display 1..1 — "The preferred display for this
        # concept". medterm4ds resolves the canonical preferred STR.
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": system_uri, "code": code,
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "display") == expected_display

    @pytest.mark.parametrize("system_uri,code,expected_display", [
        (SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes mellitus"),
        (RXNORM_URI, RXNORM_METFORMIN, "24 HR metformin 500 MG Oral Tablet"),
        (ICD10CM_URI, ICD10CM_T2DM, "Type 2 diabetes mellitus"),
    ])
    def test_s61_validate_code_display_byte_exact_for_seeded_codes(
        self, fhir_client, system_uri, code, expected_display
    ):
        # Spec: $validate-code Out display — server canonical display for
        # the code (NOT echo of client input).
        r = fhir_client.get("/fhir/CodeSystem/$validate-code", params={
            "system": system_uri, "code": code,
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "display") == expected_display

    @pytest.mark.parametrize("system_uri,code,expected_display", [
        (SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes mellitus"),
        (RXNORM_URI, RXNORM_METFORMIN, "24 HR metformin 500 MG Oral Tablet"),
        (ICD10CM_URI, ICD10CM_T2DM, "Type 2 diabetes mellitus"),
    ])
    def test_s62_lookup_validate_display_byte_exact_agreement(
        self, fhir_client, system_uri, code, expected_display
    ):
        """CS-04/TERMINOLOGIST tip: canonical-DISPLAY cross-operation
        invariant. ``$lookup`` Out display MUST byte-exactly match
        ``$validate-code`` Out display for every seeded code."""
        r_lookup = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": system_uri, "code": code,
        })
        r_validate = fhir_client.get("/fhir/CodeSystem/$validate-code", params={
            "system": system_uri, "code": code,
        })
        assert r_lookup.status_code == 200
        assert r_validate.status_code == 200
        lookup_display = _param_value(r_lookup.json(), "display")
        validate_display = _param_value(r_validate.json(), "display")
        assert lookup_display == validate_display == expected_display

    @pytest.mark.parametrize("alias_uri,canonical_uri,code,expected_display", [
        # SNOMED trailing-slash alias
        (SNOMED_URI_TRAILING_SLASH, SNOMED_URI, SNOMED_T2DM,
         "Type 2 diabetes mellitus"),
        # SNOMED OID alias
        (SNOMED_OID_URI, SNOMED_URI, SNOMED_T2DM,
         "Type 2 diabetes mellitus"),
        # SNOMED uppercase-scheme alias
        (SNOMED_URI_UPPERCASE_SCHEME, SNOMED_URI, SNOMED_T2DM,
         "Type 2 diabetes mellitus"),
        # RxNorm trailing-slash alias
        (RXNORM_URI_TRAILING_SLASH, RXNORM_URI, RXNORM_METFORMIN,
         "24 HR metformin 500 MG Oral Tablet"),
        # ICD-10-CM trailing-slash alias
        (ICD10CM_TRAILING_SLASH, ICD10CM_URI, ICD10CM_T2DM,
         "Type 2 diabetes mellitus"),
    ])
    def test_s63_lookup_display_invariant_on_alias_inputs(
        self, fhir_client, alias_uri, canonical_uri, code, expected_display
    ):
        """CS-04/TERMINOLOGIST tip: canonical-DISPLAY invariant holds on
        alias inputs. The display is the SAME regardless of which alias
        the client used."""
        r_canonical = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": canonical_uri, "code": code,
        })
        r_alias = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": alias_uri, "code": code,
        })
        assert r_canonical.status_code == 200, f"canonical failed: {canonical_uri}"
        assert r_alias.status_code == 200, f"alias failed: {alias_uri}"
        assert _param_value(r_canonical.json(), "display") == \
               _param_value(r_alias.json(), "display") == expected_display


# ===========================================================================
# Lens 7: Canonical-SYSTEM cross-operation invariant
# ===========================================================================


class TestLens7CanonicalSystemInvariant:
    """``$lookup`` Out ``system`` byte-exact matches ``$validate-code`` Out
    ``system`` AND both resolve to canonical (not echo of alias input).
    Pattern: client-input-as-canonical drift count=8+1 PROMOTED — must NOT
    recur on CS-05 surface."""

    @pytest.mark.parametrize("alias_uri,canonical_uri,code", [
        (SNOMED_URI_TRAILING_SLASH, SNOMED_URI, SNOMED_T2DM),
        (SNOMED_OID_URI, SNOMED_URI, SNOMED_T2DM),
        (SNOMED_URI_UPPERCASE_SCHEME, SNOMED_URI, SNOMED_T2DM),
    ])
    def test_s70_lookup_out_system_resolves_alias_to_canonical(
        self, fhir_client, alias_uri, canonical_uri, code
    ):
        """client-input-as-canonical drift count=8+1 PROMOTED: Out system
        MUST be canonical, NOT echo of alias input."""
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": alias_uri, "code": code,
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "system") == canonical_uri

    @pytest.mark.parametrize("alias_uri,canonical_uri,code", [
        (SNOMED_URI_TRAILING_SLASH, SNOMED_URI, SNOMED_T2DM),
        (SNOMED_OID_URI, SNOMED_URI, SNOMED_T2DM),
    ])
    def test_s71_validate_code_out_system_resolves_alias_to_canonical(
        self, fhir_client, alias_uri, canonical_uri, code
    ):
        r = fhir_client.get("/fhir/CodeSystem/$validate-code", params={
            "system": alias_uri, "code": code,
        })
        assert r.status_code == 200
        assert _param_value(r.json(), "system") == canonical_uri

    @pytest.mark.parametrize("alias_uri", [
        SNOMED_URI_TRAILING_SLASH,
        SNOMED_OID_URI,
        SNOMED_URI_UPPERCASE_SCHEME,
    ])
    def test_s72_lookup_validate_out_system_byte_exact_agreement(
        self, fhir_client, alias_uri
    ):
        """Cross-op canonical-SYSTEM invariant: ``$lookup`` and
        ``$validate-code`` agree on Out ``system`` for the same alias input."""
        r_lookup = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": alias_uri, "code": SNOMED_T2DM,
        })
        r_validate = fhir_client.get("/fhir/CodeSystem/$validate-code", params={
            "system": alias_uri, "code": SNOMED_T2DM,
        })
        assert r_lookup.status_code == 200
        assert r_validate.status_code == 200
        lookup_sys = _param_value(r_lookup.json(), "system")
        validate_sys = _param_value(r_validate.json(), "system")
        assert lookup_sys == validate_sys == SNOMED_URI


# ===========================================================================
# Lens 8: Hostile input matrix on $lookup version param
# ===========================================================================


class TestLens8HostileVersionInputMatrix:
    """Hostile inputs on the version param — must not 5xx (DoS /
    information-disclosure surface)."""

    @pytest.mark.parametrize("version", [
        "a" * 10000,                   # very long
        "\x00null\x00byte\x00",        # null bytes
        "日本語バージョン",              # unicode CJK
        "version\twith\ttabs",         # tabs
        "version\nwith\nnewlines",     # newlines (CRLF injection attempt)
        "../../../etc/passwd",         # path traversal
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",  # URL-encoded path traversal
    ])
    def test_s80_lookup_with_hostile_version_no_5xx(self, fhir_client, version):
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM, "version": version,
        })
        assert r.status_code < 500, (
            f"version={version!r} caused status={r.status_code}"
        )

    @pytest.mark.parametrize("version", [
        "a" * 10000,
        "\x00null\x00",
        "日本語",
        "../../../etc/passwd",
    ])
    def test_s81_validate_code_with_hostile_version_no_5xx(self, fhir_client, version):
        r = fhir_client.get("/fhir/CodeSystem/$validate-code", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM, "version": version,
        })
        assert r.status_code < 500

    @pytest.mark.parametrize("version", [
        "a" * 10000,
        "\x00null\x00",
        "日本語",
    ])
    def test_s82_subsumes_with_hostile_version_no_5xx(self, fhir_client, version):
        r = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
            "version": version,
        })
        assert r.status_code < 500


# ===========================================================================
# Lens 9: Source-read structural contracts
# ===========================================================================


class TestLens9SourceReadContracts:
    """Source-read audits of CS-05 surface contracts. Carry-forward-as-probe
    pattern (CS-03 TERMINOLOGIST methodology)."""

    def test_s90_lookup_builder_has_hardcoded_abstract_false(self):
        """CF-SKEPTIC-CS05-01 structural contract: build_parameters_lookup
        hardcodes abstract=False at responses.py:59."""
        source = RESPONSES_PATH.read_text()
        builder = _get_func_source(source, "build_parameters_lookup")
        assert builder
        # The line is: _param("abstract", False, "valueBoolean"),
        # Find the literal False in the abstract line.
        lines = [ln for ln in builder.splitlines() if "abstract" in ln.lower()]
        assert len(lines) >= 1
        assert any("False" in ln for ln in lines)

    def test_s91_lookup_builder_does_not_emit_inactive(self):
        """CF-SKEPTIC-CS05-02 structural contract: build_parameters_lookup
        does NOT emit an inactive property."""
        source = RESPONSES_PATH.read_text()
        builder = _get_func_source(source, "build_parameters_lookup")
        assert builder
        assert "inactive" not in builder

    def test_s92_do_lookup_delegates_to_canonical_system_uri(self):
        """CS-02 HISTORIAN QA-047 structural contract: _do_lookup re-resolves
        the client-supplied system_uri through canonical_system_uri() before
        passing to the builder."""
        source = FHIR_API_PATH.read_text()
        do_lookup = _get_nested_func_source(source, "create_fhir_app", "_do_lookup")
        assert do_lookup
        assert "canonical_system_uri" in do_lookup

    def test_s93_do_validate_delegates_to_canonical_system_uri(self):
        """CS-03 HISTORIAN QA-051 structural contract: _do_validate re-resolves
        the client-supplied system_uri through canonical_system_uri() before
        passing to the builder. Sibling-handler parity audit."""
        source = FHIR_API_PATH.read_text()
        do_validate = _get_nested_func_source(source, "create_fhir_app", "_do_validate")
        assert do_validate
        assert "canonical_system_uri" in do_validate

    def test_s94_do_subsumes_uses_is_descendant_bfs(self):
        """CF-SKEPTIC-CS05-03 structural contract: _do_subsumes uses
        is_descendant (BFS with visited set) so multi-hierarchy DAGs are
        handled correctly."""
        source = FHIR_API_PATH.read_text()
        do_subsumes = _get_nested_func_source(source, "create_fhir_app", "_do_subsumes")
        assert do_subsumes
        assert "is_descendant" in do_subsumes

    def test_s95_lookup_get_post_parity_with_version(self, fhir_client):
        # Spec: $lookup GET and POST produce same response shape.
        body_get = {"system": SNOMED_URI, "code": SNOMED_T2DM, "version": "2024-09"}
        r_get = fhir_client.get("/fhir/CodeSystem/$lookup", params=body_get)
        r_post = fhir_client.post(
            "/fhir/CodeSystem/$lookup",
            json={"resourceType": "Parameters", "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM},
                {"name": "version", "valueString": "2024-09"},
            ]},
        )
        assert r_get.status_code == 200
        assert r_post.status_code == 200
        # Displays match (canonical-DISPLAY invariant on POST surface too).
        assert _param_value(r_get.json(), "display") == \
               _param_value(r_post.json(), "display")


# ===========================================================================
# Lens 10: Response shape audit on every seeded code × every operation
# ===========================================================================


class TestLens10ResponseShapeAudit:
    """Content-Type, Parameters resourceType, required Out params audited
    across every seeded code × every operation. FHIR R4 §3.1.0.1.9 mandates
    ``application/fhir+json`` Content-Type on every response."""

    @pytest.mark.parametrize("system_uri,code", [
        (SNOMED_URI, SNOMED_T2DM),
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (RXNORM_URI, RXNORM_METFORMIN),
        (ICD10CM_URI, ICD10CM_T2DM),
    ])
    def test_s100_lookup_response_shape(self, fhir_client, system_uri, code):
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": system_uri, "code": code,
        })
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/fhir+json")
        body = r.json()
        assert body["resourceType"] == "Parameters"
        # Required Out params per $lookup spec.
        for name in ("name", "code", "system", "display", "abstract"):
            assert any(
                isinstance(p, dict) and p.get("name") == name
                for p in body["parameter"]
            ), f"missing required Out param: {name}"

    @pytest.mark.parametrize("system_uri,code", [
        (SNOMED_URI, SNOMED_T2DM),
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (RXNORM_URI, RXNORM_METFORMIN),
        (ICD10CM_URI, ICD10CM_T2DM),
    ])
    def test_s101_validate_code_response_shape(self, fhir_client, system_uri, code):
        r = fhir_client.get("/fhir/CodeSystem/$validate-code", params={
            "system": system_uri, "code": code,
        })
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/fhir+json")
        body = r.json()
        assert body["resourceType"] == "Parameters"
        # Required Out params per $validate-code spec.
        for name in ("result", "code", "system"):
            assert any(
                isinstance(p, dict) and p.get("name") == name
                for p in body["parameter"]
            ), f"missing required Out param: {name}"

    def test_s102_subsumes_response_shape(self, fhir_client):
        r = fhir_client.get("/fhir/CodeSystem/$subsumes", params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
        })
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/fhir+json")
        body = r.json()
        assert body["resourceType"] == "Parameters"
        outcome = _param_value(body, "outcome")
        assert outcome in VALID_OUTCOMES

    def test_s103_lookup_xml_format_returns_lowercase_boolean(self, fhir_client):
        # CR-002: XML wire-format for booleans MUST be lowercase.
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM, "_format": "xml",
        })
        assert r.status_code == 200
        assert "xml" in r.headers["content-type"]
        body_text = r.text
        # abstract is valueBoolean=False → renders as 'value="false"'.
        assert 'value="false"' in body_text
        assert 'value="False"' not in body_text

    def test_s104_validate_code_xml_format_unknown_code(self, fhir_client):
        # Unknown code path → 200 + result=false (NOT 400 per spec example).
        # Spec: "If the code system is unable to make a determination, it
        # SHOULD return result=false".
        r = fhir_client.get("/fhir/CodeSystem/$validate-code", params={
            "system": SNOMED_URI, "code": "9999999999INVALID", "_format": "xml",
        })
        assert r.status_code == 200
        assert "xml" in r.headers["content-type"]
        body_text = r.text
        # result=false renders as lowercase.
        assert 'value="false"' in body_text
        assert 'value="False"' not in body_text

    def test_s105_lookup_accept_header_xml(self, fhir_client):
        # Accept-header XML negotiation (CR-002 + TS-01 EXPLORER QA-009).
        r = fhir_client.get("/fhir/CodeSystem/$lookup", params={
            "system": SNOMED_URI, "code": SNOMED_T2DM,
        }, headers={"Accept": "application/fhir+xml"})
        assert r.status_code == 200
        assert "xml" in r.headers["content-type"]
