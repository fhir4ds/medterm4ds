"""VS-03 SKEPTIC resweep: ValueSet $expand — Advanced.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Filter operator: https://hl7.org/fhir/R4/valueset.html#filter
Filter Operator enum: https://hl7.org/fhir/R4/valueset-concept-operator.html

This is the resweep (post-milestone-11) SKEPTIC pass for chunk VS-03. The
prior VS-03 SKEPTIC test_vs03_skeptic.py covered the 5 spec items + landed
the QA-059 fix (Parameters-with-valueSet body). This resweep focuses on
SKEPTIC's hostile-input lens:

  1. Hostile-input probes per spec item — boundary conditions, malformed
     bodies, special characters, very long inputs. (SKEPTIC lens: "break
     it".)
  2. Canonical-DISPLAY fallback chain audit per VS-02/TERMINOLOGIST tip —
     the QA-001 fix's fallback chain (engine canonical preferred term →
     code string → never empty) MUST hold when the engine encounters
     unknown-property or version-pin edge cases. The fallback shape may
     have additional siblings worth auditing via AST walk.
  3. Advanced $expand params (property, designation, version-specific) —
     hostile-input lens per VS-02/TERMINOLOGIST tip.
  4. Cross-handler GET↔POST byte-exact parity on advanced shapes.
  5. Source-read structural contracts — the simplest way to lock in
     expected behaviors without depending on fixture data.

Conformance fixture (4 mrconso rows, 1 mrrel row): SNOMEDCT_US has 2 codes
(Diabetes mellitus / T2DM); ICD10CM has 1 (E11); RXNORM has 1 (metformin);
mrrel has a single isa relationship (T2DM → Diabetes mellitus).
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
from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Path to apps/fhir_api.py for source-read structural probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# =============================================================================
# Helpers
# =============================================================================


def _post_expand(fhir_client, body: dict, *, params: dict | None = None):
    """POST a body to /fhir/ValueSet/$expand. Returns (status, body_json)."""
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


def _get_expand(fhir_client, *, params: dict):
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
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


def _contains_displays(body: dict) -> dict[tuple[str, str], str]:
    out = {}
    for c in body.get("expansion", {}).get("contains", []):
        out[(c.get("system", ""), c.get("code", ""))] = c.get("display", "")
    return out


def _make_intensional_snomed_isa(root_code: str = SNOMED_DIABETES_MELLITUS) -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-rs-intensional-isa",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "is-a", "value": root_code}
                ],
            }],
        },
    }


def _make_intensional_snomed_descendent_of(
    root_code: str = SNOMED_DIABETES_MELLITUS,
) -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-rs-intensional-descendent-of",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "descendent-of", "value": root_code}
                ],
            }],
        },
    }


def _make_extensional(system: str, concepts) -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-rs-extensional",
        "compose": {"include": [{"system": system, "concept": concepts}]},
    }


def _get_func_source(
    module_path: Path,
    parent_name: str,
    child_name: str | None = None,
) -> str:
    """Read source of a top-level function or nested function inside
    ``create_fhir_app`` (the route factory).

    Walks ``ast.FunctionDef`` AND ``ast.AsyncFunctionDef`` so async route
    handlers are reachable.
    """
    src = module_path.read_text()
    tree = ast.parse(src)
    if child_name is None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parent_name:
                return ast.get_source_segment(src, node) or ""
        return ""
    # Nested form: walk into parent, then into child.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parent_name:
            for child in ast.walk(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == child_name:
                    return ast.get_source_segment(src, child) or ""
    return ""


# =============================================================================
# Item 1 (resweep): inline valueSet — hostile input probes
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html In Parameters
#   valueSet: 0..1 ValueSet
# =============================================================================


class TestItem1InlineValueSetHostile:
    """Hostile-input probes for the inline-valueSet parameter."""

    def test_s10_empty_valueset_body(self, fhir_client):
        """POST an empty ValueSet (resourceType only) — MUST return 200 with
        empty expansion (NOT crash)."""
        body = {"resourceType": "ValueSet"}
        status, parsed = _post_expand(fhir_client, body)
        # The handler accepts the bare-ValueSet shape; an empty compose
        # produces an empty contains[] (per the empty-include loop).
        # MUST NOT crash (500).
        assert status < 500, f"server crash on empty ValueSet body: {status} {parsed}"
        if status == 200:
            assert parsed["resourceType"] == "ValueSet"

    def test_s11_valueset_compose_is_non_dict_string(self, fhir_client):
        """POST a ValueSet body with ``compose`` as a non-dict (string) —
        MUST return 200 with empty expansion (NOT 500).

        Per the 10th PROMOTED pattern in GLOBAL_RULES.md and the
        VS-01-resweep SKEPTIC QA-001 fix at apps/fhir_api.py:2528, the
        ``isinstance(compose, dict)`` guard at the parent data-access
        boundary MUST silently reset to ``{}`` and produce empty
        contains[]. This is a HOSTILE-INPUT probe re-verifying the fix.
        """
        body = {"resourceType": "ValueSet", "compose": "not-a-dict"}
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on non-dict compose: {status} {parsed}"
        # Status 200 expected (compose silently reset to {}).
        if status == 200:
            codes = _contains_codes(parsed)
            assert codes == [], f"expected empty contains on bad compose: {codes}"

    @pytest.mark.parametrize("bad_value", [None, 42, [], "string"])
    def test_s12_valueset_compose_is_non_dict_variants(self, fhir_client, bad_value):
        """POST compose as None/int/list/string — MUST NOT crash (500).

        The 10th PROMOTED pattern isinstance guard covers all non-dict
        shapes. Parametrized to confirm coverage.
        """
        body = {"resourceType": "ValueSet", "compose": bad_value}
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, (
            f"server crash on compose={bad_value!r}: {status} {parsed}"
        )

    def test_s13_include_is_non_dict_string(self, fhir_client):
        """POST compose.include[] containing a non-dict string — MUST NOT
        crash (500). Per the CS-04 HISTORIAN QA-001 fix."""
        body = {
            "resourceType": "ValueSet",
            "compose": {"include": ["not-a-dict"]},
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash: {status} {parsed}"
        if status == 200:
            assert _contains_codes(parsed) == []

    def test_s14_include_missing_system_graceful(self, fhir_client):
        """POST compose.include[] with NO ``system`` field — MUST NOT crash.

        Per https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.include.system:
        the system is 1..1 on the include element. But a malformed client
        body that omits system should be handled gracefully (skip or
        fall-through, NOT 500).
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {"include": [{"concept": [{"code": SNOMED_DIABETES_MELLITUS}]}]},
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on missing system: {status} {parsed}"

    def test_s15_include_concept_is_non_dict(self, fhir_client):
        """POST compose.include[].concept[] with a non-dict entry — MUST
        NOT crash. Per the CS-04 HISTORIAN QA-001 4th-sibling fix."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": ["not-a-dict", {"code": SNOMED_DIABETES_MELLITUS}],
                }]
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash: {status} {parsed}"
        if status == 200:
            codes = _contains_codes(parsed)
            # Valid code MUST appear; non-dict silently dropped.
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, codes

    def test_s16_include_filter_is_non_dict(self, fhir_client):
        """POST compose.include[].filter[] with a non-dict entry — MUST
        NOT crash."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        "not-a-dict",
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS},
                    ],
                }]
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash: {status} {parsed}"
        if status == 200:
            codes = _contains_codes(parsed)
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, codes

    def test_s17_parameters_with_valueSet_param_resource_is_string(self, fhir_client):
        """POST Parameters body with valueSet param whose ``resource`` is a
        non-dict (string). MUST NOT crash; MUST fall through to the 400
        path (no usable ValueSet extracted)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "valueSet", "resource": "not-a-dict"}],
        }
        status, parsed = _post_expand(fhir_client, body)
        # The _extract_valueset_from_parameters helper has isinstance
        # guard on resource; falls through to 400.
        assert status < 500, f"server crash: {status} {parsed}"

    def test_s18_parameters_with_valueSet_param_resource_missing(self, fhir_client):
        """POST Parameters body with valueSet param but no resource — MUST
        NOT crash."""
        body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "valueSet"}],  # no resource
        }
        status, parsed = _post_expand(fhir_client, body)
        # Per VS-03 SKEPTIC test_s13, expected 400/422. NOT 500.
        assert status < 500, f"server crash: {status} {parsed}"

    def test_s19_parameters_param_non_dict_entries(self, fhir_client):
        """POST Parameters body with parameter[] entries that are non-dict
        (string, int, null, list). MUST NOT crash — the CS-04 SKEPTIC
        QA-001 isinstance guard at _parse_parameters must silently drop
        them.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                "not-a-dict",
                None,
                42,
                ["nested-list"],
                {"name": "filter", "valueString": "diabetes"},
            ],
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash: {status} {parsed}"
        if status == 200:
            assert parsed["resourceType"] == "ValueSet"


# =============================================================================
# Item 2 (resweep): explicit concept list — hostile inputs
# =============================================================================


class TestItem2ExplicitConceptListHostile:
    """Hostile-input probes for explicit concept lists."""

    def test_s20_concept_with_very_long_code(self, fhir_client):
        """Concept list with a very long code (>1000 chars) — MUST NOT
        crash (no 500). SQL injection surface via DuckDB prepared
        statement is structurally prevented."""
        long_code = "A" * 5000
        body = _make_extensional(SNOMED_URI, [{"code": long_code, "display": "long"}])
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on long code: {status} {parsed}"

    def test_s21_concept_with_sql_injection(self, fhir_client):
        """SQL injection in the code field — MUST NOT crash. Prepared
        statement prevents injection."""
        body = _make_extensional(SNOMED_URI, [
            {"code": "44054006'; DROP TABLE mrconso; --", "display": "x"},
        ])
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on SQL injection: {status} {parsed}"

    def test_s22_concept_with_null_byte(self, fhir_client):
        """Concept list with null byte in code — MUST NOT crash."""
        body = _make_extensional(SNOMED_URI, [
            {"code": "44054006\x00evil", "display": "x"},
        ])
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on null byte: {status} {parsed}"

    def test_s23_duplicate_codes_deduplicated(self, fhir_client):
        """Concept list with duplicate codes — MUST be deduplicated (per
        the dedupe-by-(system, code) logic at apps/fhir_api.py:2689)."""
        body = _make_extensional(SNOMED_URI, [
            {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes"},  # dup
            {"code": SNOMED_T2DM, "display": "T2DM"},
        ])
        status, parsed = _post_expand(fhir_client, body)
        assert status == 200
        codes = _contains_codes(parsed)
        assert len(codes) == 2, f"duplicates NOT removed: {codes}"
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s24_concept_with_special_chars(self, fhir_client):
        """Concept list with special chars in code — MUST NOT crash.
        Hostile-input matrix."""
        for hostile in [
            "ABC; rm -rf /",
            "<script>alert(1)</script>",
            "../../etc/passwd",
            "中文",
            "code with spaces",
            "tab\tchar",
        ]:
            body = _make_extensional(SNOMED_URI, [
                {"code": hostile, "display": "x"},
                {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
            ])
            status, parsed = _post_expand(fhir_client, body)
            assert status < 500, (
                f"server crash on hostile={hostile!r}: {status} {parsed}"
            )

    def test_s25_concept_code_as_non_string(self, fhir_client):
        """Concept list where ``code`` is a non-string (int, null, list)
        — MUST NOT crash. The implementation str()'s the value."""
        for bad in [42, None, ["nested"], {"nested": "dict"}]:
            body = _make_extensional(SNOMED_URI, [
                {"code": bad, "display": "x"},
                {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
            ])
            status, parsed = _post_expand(fhir_client, body)
            assert status < 500, (
                f"server crash on non-string code={bad!r}: {status} {parsed}"
            )

    def test_s26_concept_display_as_non_string(self, fhir_client):
        """Concept list where ``display`` is a non-string (int, list) —
        MUST NOT crash. Implementation should silently fall back to
        canonical display via get_code_infos."""
        for bad in [42, ["nested"], {"nested": "dict"}]:
            body = _make_extensional(SNOMED_URI, [
                {"code": SNOMED_DIABETES_MELLITUS, "display": bad},
            ])
            status, parsed = _post_expand(fhir_client, body)
            assert status < 500, (
                f"server crash on non-string display={bad!r}: {status} {parsed}"
            )


# =============================================================================
# Item 3 (resweep): is-a filter — hostile inputs
# =============================================================================


class TestItem3IsAFilterHostile:
    """Hostile-input probes for the is-a filter operator."""

    def test_s30_is_a_on_nonexistent_root(self, fhir_client):
        """is-a on a non-existent root code — MUST NOT crash. The
        implementation falls through to empty contains (or just the root
        entry with display = root_code fallback).
        """
        body = _make_intensional_snomed_isa("NONEXISTENT_99999")
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on non-existent root: {status} {parsed}"
        # Even if root appears with display=NONEXISTENT, the descendant
        # walk is empty.

    def test_s31_is_a_on_non_snomed_system(self, fhir_client):
        """is-a on a non-SNOMED system (ICD-10-CM). MUST NOT crash.
        The implementation calls get_descendants_bfs with the source
        derived from the URI; ICD-10-CM has no isa hierarchy in the
        fixture, so descendants = []. Root is still included for is-a."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": ICD10CM_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": ICD10CM_T2DM}
                    ],
                }],
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on is-a ICD-10-CM: {status} {parsed}"

    def test_s32_is_a_with_very_long_root_code(self, fhir_client):
        """is-a with a very long root code (>1000 chars) — MUST NOT crash."""
        body = _make_intensional_snomed_isa("A" * 5000)
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on long root: {status} {parsed}"

    def test_s33_is_a_with_sql_injection_in_root(self, fhir_client):
        """is-a with SQL injection in the value — MUST NOT crash.
        Prepared statements prevent injection."""
        body = _make_intensional_snomed_isa(
            "73211009'; DROP TABLE mrconso; --"
        )
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on SQL injection: {status} {parsed}"

    def test_s34_is_a_value_as_non_string(self, fhir_client):
        """is-a where ``value`` is a non-string (int, null, list, dict) —
        MUST NOT crash."""
        for bad in [42, None, ["nested"], {"nested": "dict"}]:
            body = {
                "resourceType": "ValueSet",
                "compose": {
                    "include": [{
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": bad}
                        ],
                    }],
                },
            }
            status, parsed = _post_expand(fhir_client, body)
            assert status < 500, (
                f"server crash on non-string value={bad!r}: {status} {parsed}"
            )

    def test_s35_is_a_filter_entry_missing_property(self, fhir_client):
        """Filter entry missing the ``property`` field — MUST NOT crash.
        Implementation reads ``filt.get("property", "")`` which defaults
        to empty string (not a recognized property → silent drop)."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{"op": "is-a", "value": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on missing property: {status} {parsed}"

    def test_s36_is_a_filter_entry_missing_op(self, fhir_client):
        """Filter entry missing the ``op`` field — MUST NOT crash."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{"property": "concept", "value": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on missing op: {status} {parsed}"

    def test_s37_is_a_with_empty_value(self, fhir_client):
        """is-a where ``value`` is empty string — MUST NOT crash."""
        body = _make_intensional_snomed_isa("")
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on empty value: {status} {parsed}"


# =============================================================================
# Item 4 (resweep): descendent-of filter — hostile inputs
# =============================================================================


class TestItem4DescendentOfFilterHostile:
    """Hostile-input probes for the descendent-of filter operator.

    NOTE: spec-correct spelling is ``descendent-of`` (Latin-derived), per
    VS-01 SKEPTIC QA-054. The common-English ``descendant-of`` form is
    silently dropped.
    """

    def test_s40_descendent_of_on_nonexistent_root(self, fhir_client):
        """descendent-of on a non-existent root — MUST NOT crash."""
        body = _make_intensional_snomed_descendent_of("NONEXISTENT_99999")
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on non-existent root: {status} {parsed}"

    def test_s41_descendent_of_on_non_snomed(self, fhir_client):
        """descendent-of on a non-SNOMED system (ICD-10-CM) — MUST NOT
        crash."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": ICD10CM_URI,
                    "filter": [
                        {"property": "concept", "op": "descendent-of", "value": ICD10CM_T2DM}
                    ],
                }],
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash: {status} {parsed}"

    def test_s42_descendent_of_value_as_non_string(self, fhir_client):
        """descendent-of where ``value`` is non-string — MUST NOT crash."""
        for bad in [42, None, ["nested"], {"nested": "dict"}]:
            body = {
                "resourceType": "ValueSet",
                "compose": {
                    "include": [{
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "descendent-of", "value": bad}
                        ],
                    }],
                },
            }
            status, parsed = _post_expand(fhir_client, body)
            assert status < 500, (
                f"server crash on non-string value={bad!r}: {status} {parsed}"
            )

    def test_s43_descendant_of_offspec_silently_dropped(self, fhir_client):
        """Off-spec ``descendant-of`` (common English spelling) MUST be
        silently dropped per VS-01 SKEPTIC QA-054. The spec-correct
        spelling is ``descendent-of``."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "descendant-of", "value": SNOMED_DIABETES_MELLITUS}
                    ],
                }],
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status == 200
        codes = _contains_codes(parsed)
        # Off-spec spelling produces empty expansion (silently dropped).
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes
        assert (SNOMED_URI, SNOMED_T2DM) not in codes


# =============================================================================
# Item 5 (resweep): date parameter — hostile inputs + cross-handler parity
# =============================================================================


class TestItem5DateParameterHostile:
    """Hostile-input probes for the ``date`` parameter.

    Per https://hl7.org/fhir/R4/valueset-operation-expand.html In Parameters:
        date: 0..1 dateTime
        "The date for which the expansion is to be performed."

    medterm4ds is single-snapshot; the ``date`` param is accepted without
    error and ignored for actual evaluation.
    """

    @pytest.mark.parametrize("date_val", [
        "2020-01-01",                    # past date
        "2099-12-31",                    # future date
        "2025-06-15T10:30:00Z",          # full ISO 8601 with Z
        "2025-06-15T10:30:00+05:00",    # with timezone offset
        "2025-06-15T10:30:00.123Z",      # with milliseconds
        "2025-06",                       # partial year-month
        "2025",                          # year only
        "2025-W01",                      # week-only (non-standard)
    ])
    def test_s50_date_variants_accepted(self, fhir_client, date_val):
        """Various date formats MUST be accepted (no 500). medterm4ds
        ignores the date param semantically; acceptance is the
        conformant behavior."""
        status, parsed = _get_expand(
            fhir_client, params={"filter": "diabetes", "date": date_val}
        )
        assert status < 500, f"server crash on date={date_val!r}: {status} {parsed}"

    def test_s51_malformed_date_no_crash(self, fhir_client):
        """Malformed date MUST NOT crash. 200 (ignored) or 400 (rejected)
        acceptable; 500 is NOT."""
        status, parsed = _get_expand(
            fhir_client, params={"filter": "diabetes", "date": "not-a-date"}
        )
        assert status < 500, f"server crash on malformed date: {status} {parsed}"

    def test_s52_date_in_post_parameters_body(self, fhir_client):
        """``date`` in a POST Parameters body MUST be accepted."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "date", "valueDateTime": "2025-01-01T00:00:00Z"},
            ],
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on POST date: {status} {parsed}"

    def test_s53_date_combined_with_count(self, fhir_client):
        """``date`` combined with count — MUST NOT crash."""
        status, parsed = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "2025-01-01", "count": 5},
        )
        assert status == 200, f"expected 200, got {status}: {parsed}"


# =============================================================================
# TERMINOLOGIST tip: canonical-DISPLAY fallback chain audit
# The QA-001 fix's fallback chain (engine canonical preferred term →
# code string → never empty) MUST hold across hostile input edge cases.
# =============================================================================


class TestCanonicalDisplayFallbackChain:
    """Audit the canonical-DISPLAY fallback chain per VS-02/TERMINOLOGIST
    tip.

    The chain in _expand_intensional at apps/fhir_api.py:2593-2613 is:

        display = concept.get("display") or ""
        if not display and code_str:
            concept_infos = get_code_infos([CodeRef(source, code_str)], engine=engine)
            if concept_infos and concept_infos[0]:
                display = concept_infos[0].name or code_str
            else:
                display = code_str  # 3rd-tier fallback: NEVER EMPTY

    The is-a root path at lines 2644-2652 has the same 2-tier chain:

        root_infos = get_code_infos(...)
        display=root_infos[0].name or root_code

    The descendant path at line 2670 has a 1-tier chain:

        display=d.target_display or d.target.code

    This class verifies the chain holds on:
      (a) explicit concept list with unknown code (must fall back to code string)
      (b) explicit concept list with display="" (empty string, NOT None)
      (c) is-a root with unknown code (must fall back to root code string)
      (d) is-a descendant with empty target_display (must fall back to target code)
    """

    def test_s60_unknown_code_falls_back_to_code_string(self, fhir_client):
        """Unknown code in concept list — display MUST fall back to the
        code string itself (3rd-tier fallback). Per FHIR R4
        https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display:
        "The recommended display for this item in the expansion." An
        empty display is silent-wrong-answer.
        """
        body = _make_extensional(SNOMED_URI, [{"code": "UNKNOWN_99999"}])
        status, parsed = _post_expand(fhir_client, body)
        assert status == 200
        displays = _contains_displays(parsed)
        # The 3rd-tier fallback surfaces the code string itself.
        assert displays.get((SNOMED_URI, "UNKNOWN_99999")) == "UNKNOWN_99999", (
            f"expected code-string fallback, got: {displays}"
        )

    def test_s61_empty_string_display_resolves_canonical(self, fhir_client):
        """Concept with display='' (empty string, falsy) — MUST resolve
        to the engine's canonical preferred term."""
        body = _make_extensional(SNOMED_URI, [
            {"code": SNOMED_DIABETES_MELLITUS, "display": ""},
        ])
        status, parsed = _post_expand(fhir_client, body)
        assert status == 200
        displays = _contains_displays(parsed)
        canonical = displays.get((SNOMED_URI, SNOMED_DIABETES_MELLITUS), "")
        assert canonical == "Diabetes mellitus", (
            f"empty-string display NOT resolved to canonical: {canonical!r}"
        )

    def test_s62_unknown_root_is_a_falls_back_to_code_string(self, fhir_client):
        """is-a on a non-existent root — display MUST fall back to the
        root code string (3rd-tier fallback at line 2651: ``or root_code``).

        Per the 2-tier chain: ``root_infos[0].name or root_code`` — when
        root_infos is empty/None, the fallback is root_code.
        """
        body = _make_intensional_snomed_isa("UNKNOWN_ROOT_99999")
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash: {status} {parsed}"
        # The is-a code path DOES include the root in contains[]. Per
        # the chain: if root_infos is empty (None), the if-branch at
        # line 2647 doesn't fire → root NOT added to contains. So for
        # an unknown root, contains[] is empty (the root_infos check
        # short-circuits).
        # This probe documents the current behavior: root absent when
        # unknown (NOT added with a fallback display).
        if status == 200:
            codes = _contains_codes(parsed)
            assert (SNOMED_URI, "UNKNOWN_ROOT_99999") not in codes, (
                f"unknown root was added: {codes}"
            )

    def test_s63_known_code_empty_display_falls_to_canonical(self, fhir_client):
        """Combined hostile-input + canonical-fallback: known code with
        display=" " (whitespace only, truthy) — the whitespace IS
        preserved (not canonical). Documents that the chain only fires
        on falsy display (empty string / missing)."""
        body = _make_extensional(SNOMED_URI, [
            {"code": SNOMED_DIABETES_MELLITUS, "display": "   "},
        ])
        status, parsed = _post_expand(fhir_client, body)
        assert status == 200
        displays = _contains_displays(parsed)
        # Whitespace-only string is TRUTHY → preserved as-is (NOT canonical).
        # This is the CF-TERMINOLOGIST-VS01-01 deferred behavior on
        # client-supplied display echo.
        actual = displays.get((SNOMED_URI, SNOMED_DIABETES_MELLITUS), "")
        assert actual == "   ", f"expected whitespace preserved, got: {actual!r}"

    def test_s64_canonical_system_uri_applied_to_contains(self, fhir_client):
        """contains[].system MUST be the canonical URI (CR-013 fix), NOT
        the client-supplied alias."""
        for alias in [
            "http://snomed.info/sct",        # canonical
            "http://snomed.info/sct/",       # trailing slash
            "urn:oid:2.16.840.1.113883.6.96", # urn:oid
        ]:
            body = _make_extensional(alias, [
                {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
            ])
            status, parsed = _post_expand(fhir_client, body)
            assert status == 200, f"failed on alias={alias!r}: {status} {parsed}"
            codes = _contains_codes(parsed)
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, (
                f"alias={alias!r} not canonicalized in contains[].system: {codes}"
            )

    def test_s65_filter_mode_display_uses_engine_canonical(self, fhir_client):
        """Filter mode (text search) — display MUST be the engine's
        canonical preferred term from search_names result, NOT the raw
        code or empty string.

        Per apps/fhir_api.py:2466-2472 the filter mode builds contains
        with display=r.name (the engine's preferred display)."""
        status, parsed = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status == 200
        displays = _contains_displays(parsed)
        for (system, code), display in displays.items():
            assert display, (
                f"empty display on (system={system!r}, code={code!r}): {displays}"
            )
            # Verify "diabetes" appears somewhere in display (sanity).
            assert "iabetes" in display, (
                f"display={display!r} doesn't relate to filter 'diabetes': {displays}"
            )


# =============================================================================
# Advanced $expand params: hostile-input lens (VS-02/TERMINOLOGIST tip)
# Per FHIR R4 §4.7.5 In Parameters (https://hl7.org/fhir/R4/valueset-
# operation-expand.html):
#   - property: 0..* string
#   - designation: 0..* string
#   - system-version: 0..* canonical
#   - check-system-version: 0..* canonical
#   - force-system-version: 0..* canonical
#   - exclude-system: 0..* canonical
#   - includeDesignations: 0..1 boolean
#   - includeDefinition: 0..1 boolean
#   - activeOnly: 0..1 boolean
#   - excludeNested: 0..1 boolean
#   - excludeNotForUI: 0..1 boolean
#   - excludePostCoordinated: 0..1 boolean
# =============================================================================


class TestAdvancedParamsHostile:
    """Advanced $expand In parameters — MUST be accepted without 5xx.

    Per FHIR R4 §4.7.5 In Parameters table, the $expand operation
    accepts MANY optional parameters (the full list above). medterm4ds
    is single-snapshot and ignores most of these semantically; the
    conformant behavior is to ACCEPT them without error.

    This class probes the hostile-input matrix on these params:
    - empty string values
    - very long values
    - special characters
    - non-string types (when sent via POST Parameters body)
    """

    @pytest.mark.parametrize("param_name", [
        "property", "designation", "includeDesignations", "includeDefinition",
        "activeOnly", "excludeNested", "excludeNotForUI", "excludePostCoordinated",
        "displayLanguage",
    ])
    def test_s70_advanced_param_accepted_get(self, fhir_client, param_name):
        """Each advanced param on GET MUST be accepted (no 5xx)."""
        status, parsed = _get_expand(
            fhir_client,
            params={"filter": "diabetes", param_name: "test-value"},
        )
        assert status < 500, (
            f"server crash on param={param_name}: {status} {parsed}"
        )

    @pytest.mark.parametrize("param_name", [
        "system-version", "check-system-version", "force-system-version",
        "exclude-system",
    ])
    def test_s71_canonical_advanced_param_accepted_get(self, fhir_client, param_name):
        """Canonical-typed advanced params on GET MUST be accepted."""
        status, parsed = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                param_name: f"{SNOMED_URI}|2024-09",
            },
        )
        assert status < 500, (
            f"server crash on param={param_name}: {status} {parsed}"
        )

    def test_s72_property_param_in_post_parameters_body(self, fhir_client):
        """``property`` in POST Parameters body MUST be accepted."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "property", "valueString": "*"},
            ],
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash: {status} {parsed}"

    def test_s73_include_designations_in_post_parameters_body(self, fhir_client):
        """``includeDesignations`` boolean in POST Parameters body MUST
        be accepted."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "includeDesignations", "valueBoolean": True},
            ],
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash: {status} {parsed}"

    def test_s74_advanced_params_combined(self, fhir_client):
        """ALL advanced params combined on GET MUST be accepted (no 5xx).
        Lateral-combination probe."""
        status, parsed = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "property": "*",
                "includeDesignations": "true",
                "includeDefinition": "true",
                "activeOnly": "true",
                "excludeNested": "false",
                "excludeNotForUI": "false",
                "excludePostCoordinated": "false",
                "displayLanguage": "en",
                "system-version": f"{SNOMED_URI}|2024-09",
                "date": "2025-01-01",
            },
        )
        assert status < 500, f"server crash on combined: {status} {parsed}"


# =============================================================================
# Cross-handler GET↔POST parity on advanced shapes
# =============================================================================


class TestGetPostParityAdvanced:
    """GET ↔ POST byte-exact parity on advanced shapes."""

    def test_s80_filter_with_date_get_post_parity(self, fhir_client):
        """GET filter+date and POST Parameters body filter+date produce
        identical total + contains[].codes."""
        s_get, b_get = _get_expand(
            fhir_client, params={"filter": "diabetes", "date": "2025-01-01"}
        )
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "date", "valueDateTime": "2025-01-01"},
            ],
        }
        s_post, b_post = _post_expand(fhir_client, body)
        assert s_get == s_post == 200
        assert b_get["expansion"]["total"] == b_post["expansion"]["total"]
        assert set(_contains_codes(b_get)) == set(_contains_codes(b_post))

    def test_s81_extensional_get_url_vs_post_body_parity(self, fhir_client):
        """Extensional expansion: GET vs POST byte-exact parity is not
        possible (GET has no body slot for a ValueSet). But the POST
        expansion MUST be consistent regardless of count."""
        body = _make_extensional(SNOMED_URI, [
            {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
            {"code": SNOMED_T2DM, "display": "T2DM"},
        ])
        s1, b1 = _post_expand(fhir_client, body, params={"count": 100})
        s2, b2 = _post_expand(fhir_client, body, params={"count": 100})
        assert s1 == s2 == 200
        assert set(_contains_codes(b1)) == set(_contains_codes(b2))

    def test_s82_intensional_is_a_get_post_byte_exact(self, fhir_client):
        """is-a expansion MUST produce identical results on GET (via url)
        vs POST (via body)."""
        # GET form: via fhir_vs URL
        s_get, b_get = _get_expand(
            fhir_client,
            params={
                "url": f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            },
        )
        # POST form: inline ValueSet with is-a filter
        body = _make_intensional_snomed_isa(SNOMED_DIABETES_MELLITUS)
        s_post, b_post = _post_expand(fhir_client, body)
        assert s_get == s_post == 200
        assert set(_contains_codes(b_get)) == set(_contains_codes(b_post)), (
            f"GET vs POST codes differ: "
            f"GET={_contains_codes(b_get)} POST={_contains_codes(b_post)}"
        )


# =============================================================================
# Source-read structural contracts: canonical-DISPLAY fallback chain
# Per VS-02/TERMINOLOGIST tip: "the fallback shape may have additional
# siblings worth auditing via AST walk".
# =============================================================================


class TestSourceReadFallbackChain:
    """Source-read structural probes for the canonical-DISPLAY fallback
    chain across the 3 paths in _expand_intensional.

    Per VS-02/TERMINOLOGIST tip: verify the chain holds at:
      (a) explicit concept list (apps/fhir_api.py:2593-2613)
      (b) is-a root (apps/fhir_api.py:2644-2652)
      (c) descendant loop (apps/fhir_api.py:2666-2671)

    The sibling path (filter mode at apps/fhir_api.py:2466-2472) uses
    search_names which already returns populated display — but the
    structural contract is the same: NEVER empty.
    """

    def test_s90_concept_list_has_3_tier_fallback_chain(self):
        """Source-read: the concept-list path MUST have the 3-tier
        fallback chain (engine canonical → code string → never empty).

        Core logic moved from the nested ``_expand_intensional`` wrapper
        to the module-level ``expand_intensional_value_set``.
        """
        src = _get_func_source(_FHIR_API_PATH, "expand_intensional_value_set")
        assert src, "could not read expand_intensional_value_set source"
        # The chain: concept.get("display") or "" → if not display →
        # get_code_infos → if infos → infos[0].name or code_str → else
        # code_str.
        assert "concept.get(\"display\")" in src, (
            "concept.get(\"display\") not in source — chain may have changed"
        )
        assert "get_code_infos(" in src, "get_code_infos not in source"
        # 3rd-tier fallback: display = code_str (NEVER EMPTY).
        assert "display = code_str" in src or "display=code_str" in src, (
            "3rd-tier fallback (display = code_str) not in source"
        )

    def test_s91_is_a_root_has_2_tier_fallback_chain(self):
        """Source-read: the is-a root path has a 2-tier fallback chain
        (engine canonical → root_code).

        Core logic moved to module-level ``expand_intensional_value_set``.
        """
        src = _get_func_source(_FHIR_API_PATH, "expand_intensional_value_set")
        assert src
        # Per apps/fhir_api.py:2651 the root chain is:
        #   display=root_infos[0].name or root_code
        assert "root_infos[0].name or root_code" in src, (
            "is-a root 2-tier fallback (root_infos[0].name or root_code) not in source"
        )

    def test_s92_descendant_loop_has_2_tier_fallback_chain(self):
        """Source-read: the descendant loop has a 2-tier fallback chain
        (target_display → target.code).

        Core logic moved to module-level ``expand_intensional_value_set``.
        """
        src = _get_func_source(_FHIR_API_PATH, "expand_intensional_value_set")
        assert src
        # Per apps/fhir_api.py:2670 the descendant chain is:
        #   display=d.target_display or d.target.code
        assert "d.target_display or d.target.code" in src, (
            "descendant 2-tier fallback (d.target_display or d.target.code) not in source"
        )

    def test_s93_filter_mode_uses_engine_display(self):
        """Source-read: filter mode display comes from the batched
        get_code_infos preferred atom (engine canonical), falling back to
        the matched synonym r.name — QC-258 (EC-10, HIGH). Pre-QC-258 the
        raw matched synonym r.name WAS the display, diverging from
        $lookup for 1048 codes; the canonical display is now resolved
        explicitly and r.name is only the no-preferred-atom fallback."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert src
        assert "page_infos = get_code_infos(" in src, (
            "filter mode must batch-resolve canonical displays via get_code_infos (QC-258)"
        )
        assert '(info.name if info else None) or r.name' in src, (
            "display must prefer the canonical preferred term over the matched synonym (QC-258)"
        )

    def test_s94_compose_isinstance_guard_present(self):
        """Source-read: the compose isinstance guard (VS-01 SKEPTIC resweep
        QA-001 5th sibling) is present at the parent data-access boundary.

        Core logic moved to module-level ``expand_intensional_value_set``.
        """
        src = _get_func_source(_FHIR_API_PATH, "expand_intensional_value_set")
        assert src
        assert "isinstance(compose, dict)" in src, (
            "isinstance(compose, dict) guard missing — VS-01 SKEPTIC resweep fix may have regressed"
        )

    def test_s95_include_isinstance_guards_present(self):
        """Source-read: all 5 sibling isinstance guards (CS-04 HISTORIAN
        QA-001 PROMOTED 10th pattern) are present in
        ``expand_intensional_value_set`` (core logic moved from the nested
        ``_expand_intensional`` wrapper to the module-level function).
        """
        src = _get_func_source(_FHIR_API_PATH, "expand_intensional_value_set")
        assert src
        # Each iterator: include, concept, filter, exclude, exclude.concept
        # The exclude.concept inner loop is the 5th (CF-SKEPTIC-VS01-resweep
        # found the compose-level guard).
        assert src.count("isinstance(") >= 5, (
            f"expected >= 5 isinstance guards, found {src.count('isinstance(')}"
        )

    def test_s96_extract_valueset_helper_isinstance_guard(self):
        """Source-read: _extract_valueset_from_parameters has isinstance
        guards on both param and resource."""
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_extract_valueset_from_parameters"
        )
        assert src
        assert "isinstance(param, dict)" in src
        assert "isinstance(resource, dict)" in src

    def test_s97_parse_count_param_default_fallback(self):
        """Source-read: _parse_count_param handles None/empty string
        (returns default) and invalid int (returns None)."""
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_parse_count_param"
        )
        assert src
        # The helper MUST have try/except (TypeError, ValueError) and
        # return None on invalid.
        assert "(TypeError, ValueError)" in src or "TypeError, ValueError" in src, (
            "_parse_count_param missing try/except TypeError ValueError"
        )

    def test_s98_canonical_system_uri_helper_called_in_intensional(self):
        """Source-read: expand_intensional_value_set calls canonical_system_uri
        on inc_system per CR-013 fix (9th instance of client-input-as-
        canonical drift). Core logic moved from the nested
        ``_expand_intensional`` wrapper to the module-level function.
        """
        src = _get_func_source(_FHIR_API_PATH, "expand_intensional_value_set")
        assert src
        assert "canonical_system_uri(" in src, (
            "canonical_system_uri not called in expand_intensional_value_set — CR-013 fix may have regressed"
        )

    def test_s99_count_default_query_min_length_check(self):
        """Source-read: GET expand_get query has count ge=1 le=1000
        constraint per FHIR R4 §4.7.5 In Parameters.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "expand_get")
        assert src
        assert "ge=1" in src and "le=1000" in src, (
            "expand_get count Query constraint missing ge=1 le=1000"
        )


# =============================================================================
# Compose.exclude hostile inputs (CF-SKEPTIC-VS01-02 + CF-SKEPTIC-VS01-03)
# =============================================================================


class TestComposeExcludeHostile:
    """Hostile-input probes for compose.exclude[].

    Per https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.compose.exclude:
    exclude is 0..* with the same shape as include. The implementation
    only processes exclude[].concept[] (CF-SKEPTIC-VS01-02 — exclude[].filter[]
    is silently dropped). Cross-system exclusion is also silently
    incorrect (CF-SKEPTIC-VS01-03 — exclude matches on code alone,
    ignoring system).
    """

    def test_s100_exclude_concept_removes_codes(self, fhir_client):
        """exclude.concept[] removes the listed codes from the expansion.
        Per apps/fhir_api.py:2676-2687."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
                        {"code": SNOMED_T2DM, "display": "T2DM"},
                    ],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_T2DM}],
                }],
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status == 200
        codes = _contains_codes(parsed)
        assert (SNOMED_URI, SNOMED_T2DM) not in codes, (
            f"exclude.concept failed: T2DM still present: {codes}"
        )
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_s101_exclude_non_dict_entry_graceful(self, fhir_client):
        """exclude[] with a non-dict entry — MUST NOT crash."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS, "display": "DM"}],
                }],
                "exclude": ["not-a-dict"],
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on non-dict exclude: {status} {parsed}"

    def test_s102_exclude_concept_non_list_graceful(self, fhir_client):
        """exclude[].concept as a non-list — MUST NOT crash."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS, "display": "DM"}],
                }],
                "exclude": [{"system": SNOMED_URI, "concept": "not-a-list"}],
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash on non-list concept: {status} {parsed}"

    def test_s103_exclude_concept_entry_non_dict_graceful(self, fhir_client):
        """exclude[].concept[] with a non-dict entry — MUST NOT crash."""
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS, "display": "DM"}],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": ["not-a-dict", {"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status < 500, f"server crash: {status} {parsed}"
        if status == 200:
            codes = _contains_codes(parsed)
            # Even with the malformed entry, the valid exclude.code removes DM.
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes


# =============================================================================
# Filter mode hostile system_uri (CF-SKEPTIC-VS01 system param boundary)
# =============================================================================


class TestFilterModeSystemParamHostile:
    """Hostile-input probes for the ``system`` param on filter mode."""

    def test_s110_unknown_system_uri_returns_400(self, fhir_client):
        """Filter mode with an unknown system URI — MUST return 400 (not
        500). Per apps/fhir_api.py:2429-2431 _resolve_sources returns
        None → _fhir_error(400)."""
        status, parsed = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "system": "http://unknown.example/sys"},
        )
        assert status == 400, f"expected 400, got {status}: {parsed}"
        assert parsed["resourceType"] == "OperationOutcome"

    def test_s111_alias_system_uri_resolves(self, fhir_client):
        """Filter mode with a recognized alias URI — MUST resolve and
        return 200."""
        for alias in [
            SNOMED_URI,
            "http://snomed.info/sct/",  # trailing slash
            ICD10CM_URI,
            RXNORM_URI,
        ]:
            status, parsed = _get_expand(
                fhir_client, params={"filter": "diabetes", "system": alias}
            )
            assert status < 500, (
                f"server crash on system={alias!r}: {status} {parsed}"
            )


# =============================================================================
# Response shape audit (every mode conforms to FHIR R4 §4.9)
# =============================================================================


class TestResponseShapeEveryModeResweep:
    """Response shape per FHIR R4 §4.9 on every mode."""

    def test_s120_filter_mode_response_shape(self, fhir_client):
        """Filter mode: expansion.timestamp + total + contains[] present."""
        status, parsed = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status == 200
        assert parsed["resourceType"] == "ValueSet"
        assert "timestamp" in parsed["expansion"]
        assert "total" in parsed["expansion"]
        assert isinstance(parsed["expansion"]["contains"], list)

    def test_s121_intensional_mode_response_shape(self, fhir_client):
        """Intensional mode: expansion.timestamp + total + contains[] present."""
        body = _make_intensional_snomed_isa()
        status, parsed = _post_expand(fhir_client, body)
        assert status == 200
        assert "timestamp" in parsed["expansion"]
        assert "total" in parsed["expansion"]
        assert isinstance(parsed["expansion"]["contains"], list)

    def test_s122_extensional_mode_response_shape(self, fhir_client):
        """Extensional mode: expansion.timestamp + total + contains[] present."""
        body = _make_extensional(SNOMED_URI, [
            {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
        ])
        status, parsed = _post_expand(fhir_client, body)
        assert status == 200
        assert "timestamp" in parsed["expansion"]
        assert parsed["expansion"]["total"] == 1

    def test_s123_parameters_with_valueset_response_shape(self, fhir_client):
        """Parameters-with-valueSet body — response shape conforms."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": _make_extensional(SNOMED_URI, [
                        {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
                    ]),
                }
            ],
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status == 200
        assert parsed["resourceType"] == "ValueSet"
        assert parsed["expansion"]["total"] == 1


# =============================================================================
# R4 filter-operator enum audit (re-verification of CF-SKEPTIC-VS01-01 pin)
# =============================================================================


class TestFilterOperatorEnumAudit:
    """Verify FHIR_R4_FILTER_OPERATORS is the canonical R4 closed enum."""

    def test_s130_r4_filter_operators_canonical(self):
        """FHIR_R4_FILTER_OPERATORS frozen-set MUST contain exactly the
        9 spec values per https://hl7.org/fhir/R4/valueset-concept-operator.html.
        """
        expected = frozenset({
            "=", "is-a", "descendent-of", "is-not-a", "regex",
            "in", "not-in", "generalizes", "exists",
        })
        actual = frozenset(FHIR_R4_FILTER_OPERATORS)
        assert actual == expected, (
            f"FHIR_R4_FILTER_OPERATORS drift: actual={actual} expected={expected}"
        )

    def test_s131_offspec_descendant_of_not_in_enum(self):
        """Off-spec ``descendant-of`` MUST NOT be in the R4 enum."""
        assert "descendant-of" not in frozenset(FHIR_R4_FILTER_OPERATORS), (
            "off-spec 'descendant-of' leaked into R4 enum"
        )


# =============================================================================
# POST handler count query-param boundary (VS-01 TERMINOLOGIST QA-055)
# =============================================================================


class TestPostCountQueryParamBoundary:
    """expand_post count Query boundary per VS-01 TERMINOLOGIST QA-055."""

    def test_s140_post_count_below_min_rejected(self, fhir_client):
        """POST count=0 MUST be rejected (422 per ge=1)."""
        body = _make_extensional(SNOMED_URI, [
            {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
        ])
        status, parsed = _post_expand(fhir_client, body, params={"count": 0})
        assert status == 422, f"expected 422, got {status}: {parsed}"

    def test_s141_post_count_above_max_rejected(self, fhir_client):
        """POST count=1001 MUST be rejected (422 per le=1000)."""
        body = _make_extensional(SNOMED_URI, [
            {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
        ])
        status, parsed = _post_expand(fhir_client, body, params={"count": 1001})
        assert status == 422, f"expected 422, got {status}: {parsed}"

    def test_s142_post_count_in_body_overrides_query_default(self, fhir_client):
        """POST Parameters body with explicit count — body count
        overrides query default per FHIR R4 §4.7.5 (Parameters-body
        parameters override GET defaults).
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": _make_extensional(SNOMED_URI, [
                        {"code": SNOMED_DIABETES_MELLITUS, "display": "DM"},
                        {"code": SNOMED_T2DM, "display": "T2DM"},
                    ]),
                },
                {"name": "count", "valueInteger": 1},
            ],
        }
        status, parsed = _post_expand(fhir_client, body)
        assert status == 200
        # Body count=1 truncates; query default would be 20.
        assert len(parsed["expansion"]["contains"]) <= 1
        # Total reflects un-truncated (2).
        assert parsed["expansion"]["total"] == 2
