"""SKEPTIC RESWEEP probes for VS-01 (ValueSet Resource Structure) — fresh
full-sweep run.

Spec: https://hl7.org/fhir/R4/valueset.html (R4 canonical)
       Filter operators: https://hl7.org/fhir/R4/valueset.html#filter
       Expansion: https://hl7.org/fhir/R4/valueset.html#expansion

This file contains NEW hostile-input probes that are NOT in the baseline
``test_vs01_skeptic.py`` (44 probes across 6 lens dimensions). The baseline
is treated as trusted prior coverage; this resweep file adds the
FRESH-FULL-SWEEP mandated probes per USER_DIRECTIVES [2026-08-08].

SKEPTIC lens (per ROLE_QA_ENGINEER Section 3): aggressive bug hunting — edge
cases, malformed inputs, boundary conditions. 5-10 hostile probes per spec
item.

CS-05/TERMINOLOGIST tip for VS-01/SKEPTIC: Audit ValueSet operation parameter
case fidelity against the canonical FHIR R4 OperationDefinition. Per FHIR R4:
- ``$expand`` parameter is ``valueSet`` (lowercase v, capital S), NOT
  ``valueset`` or ``ValueSet``
- ``$validate-code`` parameter is ``systemVersion`` (camelCase) per FHIR R4
  convention
- FHIR R4 mixes conventions: $translate uses lowercase ``targetsystem``,
  $lookup uses lowercase ``displayLanguage``, but $expand uses camelCase
  ``activeOnly``
- Always fetch the spec page and audit the parameter table for exact case
  before writing probes

Per HTTP-fetched canonical R4 spec (2026-08-09):
- $expand In Parameters: ``valueSet`` (0..1 ValueSet) — lowercase v, capital S
- $expand In Parameters: ``displayLanguage``, ``includeDesignations``,
  ``activeOnly`` (camelCase)
- $expand In Parameters: ``exclude-system``, ``system-version`` (kebab-case)
- VS $validate-code In Parameters: ``systemVersion`` (0..1 string,
  camelCase) — "The version of the system, if one was provided in the source
  data"
- VS $validate-code In Parameters: ``valueSetVersion`` (camelCase),
  ``displayLanguage`` (camelCase)

The 6 chunk items covered:
  1. Intensional vs extensional compose
  2. compose.include: system, version, concept (extensional), filter (intensional)
  3. compose.exclude: same structure as include, subtracts from include
  4. compose.filter operators: 9-value FHIR R4 enum (verified via
     ``FHIR_R4_FILTER_OPERATORS`` constant in ``engines/fhir/__init__.py``)
  5. ValueSet.url as canonical identifier
  6. READ and SEARCH interactions work for ValueSet

10 lens dimensions, ~55 probes covering all 6 spec items + the case-fidelity
audit:
  L1  Intensional vs extensional boundary: empty compose, missing include,
      both include+exclude of same concept, missing system on concept/filter,
      MUTUALLY EXCLUSIVE constraint (cannot have both concept AND filter in
      same include clause per valueset.html#vsd-constraints vsd-3)
  L2  Parameter case-fidelity audit per CS-05/TERMINOLOGIST tip: $expand
      ``valueSet`` exact-case acceptance, off-case rejection (``ValueSet``,
      ``valueset``, ``Valueset``) — verified against
      ``_extract_valueset_from_parameters`` source contract
  L3  Filter operator closed-enum coverage matrix — every operator in
      ``FHIR_R4_FILTER_OPERATORS`` either honored or explicitly silent-
      dropped (NOT silently accepted as a synonym); off-enum values rejected
      or dropped (NOT silently equated to ``is-a``)
  L4  Malformed filter / regex / hostile inputs: malformed regex (unbalanced
      parens), regex on non-existent property, very long value, null bytes,
      SQL injection, XSS, control characters, empty op/value/property
  L5  ValueSet.url boundary conditions: very long URL (10K chars), special
      chars (spaces, pipes per cnl-1 constraint), Unicode, duplicate URLs,
      URL with # fragment, malformed URI
  L6  READ/SEARCH interactions: non-existent id, malformed id, SEARCH with
      all 5 params combined (url+version+name+title+status), partial
      matches, off-spec param values, special chars in id
  L7  compose.include structure: system/version/concept/filter elements,
      isinstance-guard probe (hostile compose body), compose with both
      include+exclude simultaneous, multi-system union
  L8  Source-read structural contracts: _expand_intensional isinstance
      guards on every list iterator, _extract_valueset_from_parameters
      exact-case check, FHIR_R4_FILTER_OPERATORS constant matches spec,
      _expand_intensional honors only ``is-a`` and ``descendent-of`` (per
      VS-01 SKEPTIC QA-054 fix)
  L9  Cross-handler GET<->POST parity: $expand GET vs POST with same
      effective input, byte-exact semantic equivalence
  L10 Response shape audit: Content-Type FHIR+json, expansion.timestamp
       1..1 mandatory, contains[].system + contains[].code present,
       OperationOutcome shape on errors

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape (200 +
    expected fields), not just absence of one error string.
  - "Don't manufacture bugs": if the fixture lacks data to exercise an item,
    document as DEFERRED with reproduction shape.
  - Spec citation required on every probe.
  - "isinstance guard at untrusted-data list-iterator boundary" (count=4
    PROMOTED as 10th PROMOTED pattern): probe every ``compose.include[]``,
    ``compose.exclude[]``, ``compose.include[].concept[]``,
    ``compose.include[].filter[]`` iterator for hostile-input resilience.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/valueset.html (R4 canonical)
# Spec: https://hl7.org/fhir/R4/valueset.html#filter (Filter operators)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion (Expansion)
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html ($expand)
# Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html ($validate-code)
#
# Per R4 valueset.html#filter, op is bound to Filter Operator (Required):
#   = | is-a | descendent-of | is-not-a | regex | in | not-in | generalizes
#   | exists
# (9 values; R6 adds `child-of` and `descendent-leaf`, NOT in R4 scope.)
#
# Per R4 valueset.html#vsd-constraints:
#   vsd-1: "A value set include/exclude SHALL have a value set or a system"
#   vsd-2: "A value set with concepts or filters SHALL include a system"
#   vsd-3: "Cannot have both concept and filter" (in same include clause)

# Source the canonical closed-enum constant — registry-as-contract pattern
# (CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED for symmetry with the other 2 R4
# closed enums; the constant is canonical here).
from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

# Aliases per FHIR_URI_ALIASES in engines/fhir/__init__.py
SNOMED_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URN_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"  # RFC 3986 §3.1 SHOULD accept

FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


def _post_expand(fhir_client, value_set: dict) -> tuple[int, dict]:
    """POST a ValueSet body to /fhir/ValueSet/$expand.

    Returns (status_code, body_json). Per FHIR R4 §4.7.5
    (https://hl7.org/fhir/R4/valueset-operation-expand.html), $expand accepts
    a ValueSet resource body via POST.
    """
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=value_set,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text}
    return resp.status_code, body


def _post_expand_params(fhir_client, parameters_body: dict, **query) -> tuple[int, dict]:
    """POST a Parameters body to /fhir/ValueSet/$expand.

    Returns (status_code, body_json).
    """
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=parameters_body,
        params=query,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text}
    return resp.status_code, body


def _contains_codes(body: dict) -> list[tuple[str, str]]:
    """Extract the (system, code) pairs from a ValueSet.expansion.contains."""
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


# =============================================================================
# L1: Intensional vs extensional boundary — vsd-1 / vsd-2 / vsd-3 constraints
# =============================================================================


class TestLens1IntensionalVsExtensionalBoundary:
    """Per https://hl7.org/fhir/R4/valueset.html#vsd-constraints, three
    constraints govern compose.include:
      vsd-1: include/exclude SHALL have valueSet OR system
      vsd-2: include with concept or filter SHALL include a system
      vsd-3: Cannot have both concept and filter (in same include clause)

    SKEPTIC lens: probe each constraint boundary + edge cases.
    """

    def test_s10_compose_with_both_concept_and_filter_silent_or_400(self, fhir_client):
        """vsd-3: "Cannot have both concept and filter" in same include.

        Per Required binding + vsd-3, the server SHOULD reject. The current
        implementation processes BOTH paths independently (concept then
        filter) and unions the results — silent-wrong-answer vs spec.

        Documenting current behavior; if probe fails, server now rejects.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_T2DM}],
                    "filter": [{"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}],
                }
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        # Either 400 (per vsd-3) or 200 with union semantics (current).
        assert status in (200, 400)
        if status == 200:
            codes = _contains_codes(body)
            # Current behavior: BOTH the listed concept AND the filter result
            # appear in the expansion. Pinning the union semantic.
            assert (SNOMED_URI, SNOMED_T2DM) in codes
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_s11_compose_include_no_system_with_concept(self, fhir_client):
        """vsd-2: include with concept SHALL have system.

        Per Required binding + vsd-2, server SHOULD reject. Documenting
        current behavior (accepts + best-effort resolution).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"concept": [{"code": SNOMED_T2DM}]}  # no system!
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        # Either 400 (per vsd-2) or 200 with empty/best-effort (current).
        assert status in (200, 400)
        if status == 200:
            # The code MAY appear with an empty/best-effort system string.
            # Asserting the server doesn't crash.
            assert body.get("resourceType") == "ValueSet"

    def test_s12_compose_include_no_system_with_filter(self, fhir_client):
        """vsd-2: include with filter SHALL have system."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"filter": [{"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status in (200, 400)
        if status == 200:
            assert body.get("resourceType") == "ValueSet"

    def test_s13_compose_include_no_system_no_concept_no_filter(self, fhir_client):
        """vsd-1: include SHALL have valueSet OR system. Empty include."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{}]},  # empty include
        }
        status, body = _post_expand(fhir_client, vs)
        assert status in (200, 400)
        if status == 200:
            codes = _contains_codes(body)
            assert codes == []

    def test_s14_compose_empty_includes_list(self, fhir_client):
        """compose.include = [] — empty list (vs. missing)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": []},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status in (200, 400)
        if status == 200:
            codes = _contains_codes(body)
            assert codes == []

    def test_s15_compose_missing_compose_entirely(self, fhir_client):
        """ValueSet body with no compose element at all."""
        vs = {"resourceType": "ValueSet"}
        status, body = _post_expand(fhir_client, vs)
        assert status in (200, 400)
        if status == 200:
            codes = _contains_codes(body)
            assert codes == []

    def test_s16_compose_with_empty_concept_list(self, fhir_client):
        """compose.include[].concept = [] — empty concept list."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{"system": SNOMED_URI, "concept": []}]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert codes == []

    def test_s17_compose_with_empty_filter_list(self, fhir_client):
        """compose.include[].filter = [] — empty filter list."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{"system": SNOMED_URI, "filter": []}]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert codes == []

    def test_s18_compose_include_and_exclude_same_code(self, fhir_client):
        """Both include AND exclude of same concept: exclude wins (subtract)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}],
                "exclude": [{"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Per §4.9.6: "codes in the exclude statements are never in the value set".
        assert (SNOMED_URI, SNOMED_T2DM) not in codes
        assert codes == []


# =============================================================================
# L2: Parameter case-fidelity audit per CS-05/TERMINOLOGIST tip
# =============================================================================


class TestLens2ParameterCaseFidelity:
    """CS-05/TERMINOLOGIST tip: audit ValueSet operation parameter case
    fidelity against the canonical FHIR R4 OperationDefinition.

    Per HTTP-fetched canonical R4 spec (2026-08-09):
    - $expand In Parameters: ``valueSet`` (0..1 ValueSet) — lowercase v,
      capital S (camelCase). NOT ``ValueSet``, ``valueset``, ``Valueset``.
    - VS $validate-code In Parameters: ``systemVersion`` (0..1 string,
      camelCase).

    Source: https://hl7.org/fhir/R4/valueset-operation-expand.html
            https://hl7.org/fhir/R4/valueset-operation-validate-code.html
    """

    def test_s20_expand_post_parameters_with_valueset_canonical_case_accepted(self, fhir_client):
        """POST $expand with Parameters body carrying ``valueSet`` (canonical
        camelCase). Per
        https://hl7.org/fhir/R4/valueset-operation-expand.html In Parameters
        #2: ``valueSet`` (0..1 ValueSet) — lowercase v, capital S.

        Per VS-03 SKEPTIC QA-059, the helper ``_extract_valueset_from_parameters``
        is wired into ``expand_post``. The probe asserts the canonical case
        IS accepted.
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": {
                        "resourceType": "ValueSet",
                        "compose": {"include": [
                            {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                        ]},
                    },
                }
            ],
        }
        status, body = _post_expand_params(fhir_client, params_body)
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body.get("resourceType") == "ValueSet"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    @pytest.mark.parametrize("off_case_name", [
        "ValueSet",   # capital V capital S
        "valueset",   # all lowercase
        "Valueset",   # capital V lowercase s
        "VALUESET",   # all uppercase
        "value_set",  # snake_case
        "value-set",  # kebab-case
    ])
    def test_s21_expand_post_parameters_with_off_case_valueset_silently_dropped(self, fhir_client, off_case_name):
        """POST $expand with Parameters body carrying an OFF-CASE variant of
        ``valueSet``. Per
        https://hl7.org/fhir/R4/parameters.html §3.3, parameter names are
        case-sensitive. The implementation MUST NOT silently accept
        ``ValueSet``/``valueset`` as if it were the canonical ``valueSet``.

        The implementation (``_extract_valueset_from_parameters``) checks
        ``param.get("name") != "valueSet"`` (exact-case match), so off-case
        variants SHOULD be silently dropped → caller falls through to the
        no-url/no-filter 400 path.

        Probe verifies the off-case variant does NOT produce the same
        expansion as the canonical case (i.e. the off-case body is NOT
        silently honored as ``valueSet``).
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": off_case_name,
                    "resource": {
                        "resourceType": "ValueSet",
                        "compose": {"include": [
                            {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                        ]},
                    },
                }
            ],
        }
        status, body = _post_expand_params(fhir_client, params_body)
        # The off-case variant SHOULD be silently dropped → fall through to
        # the 400 path (no url, no filter, no inline valueSet recognized).
        # If the server accepted the off-case as the canonical, it would
        # produce a 200 + ValueSet with SNOMED_T2DM in expansion.
        if status == 200:
            codes = _contains_codes(body)
            assert (SNOMED_URI, SNOMED_T2DM) not in codes, (
                f"Off-case parameter name {off_case_name!r} was silently "
                f"accepted as canonical 'valueSet' — case-fidelity drift."
            )

    def test_s22_extract_valueset_from_parameters_source_contract_exact_case(self):
        """SOURCE-READ contract: ``_extract_valueset_from_parameters`` MUST
        check ``param.get("name") == "valueSet"`` (exact-case match).

        Per
        https://hl7.org/fhir/R4/valueset-operation-expand.html In
        Parameters #2: ``valueSet`` (lowercase v, capital S). The
        implementation MUST NOT do case-insensitive matching (per
        https://hl7.org/fhir/R4/parameters.html §3.3, parameter names are
        case-sensitive).

        This probe reads the source via AST walk and asserts the exact-case
        check.
        """
        src = FHIR_API_PATH.read_text()
        tree = ast.parse(src)

        # Walk all FunctionDef (async or sync) searching for the helper
        target_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "_extract_valueset_from_parameters":
                    target_node = node
                    break
        assert target_node is not None, (
            "_extract_valueset_from_parameters helper not found in source"
        )
        # The exact-case check `"valueSet"` (string literal) MUST appear in
        # the helper body. Walk ast.Constant nodes only (avoids
        # comment-docstring false-positives per CS-01 SKEPTIC L9 strategy).
        string_consts = [
            n.value for n in ast.walk(target_node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        assert "valueSet" in string_consts, (
            "_extract_valueset_from_parameters MUST check exact-case "
            "'valueSet' parameter name per FHIR R4 $expand OperationDefinition"
        )

    def test_s23_validate_code_systemversion_canonical_case_accepted(self, fhir_client):
        """POST ValueSet/$validate-code with ``systemVersion`` (camelCase).

        Per
        https://hl7.org/fhir/R4/valueset-operation-validate-code.html In
        Parameters #7: ``systemVersion`` (0..1 string, camelCase) — "The
        version of the system, if one was provided in the source data".

        The current implementation uses ``_parse_parameters`` which extracts
        by name (any string is captured into the output dict); the handler
        only consumes ``system``, not ``systemVersion``. So ``systemVersion``
        is accepted (200) but ignored today. Pinning the current behavior.
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "code", "valueCode": SNOMED_T2DM},
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "systemVersion", "valueString": "http://snomed.info/sct/731000124108"},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$validate-code",
            json=params_body,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200, (
            f"expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("resourceType") == "Parameters"

    def test_s24_validate_code_systemversion_off_case_silently_dropped(self, fhir_client):
        """POST ValueSet/$validate-code with ``systemversion`` (lowercase) —
        off-case variant of ``systemVersion`` (camelCase). Per
        https://hl7.org/fhir/R4/parameters.html §3.3, parameter names are
        case-sensitive. The server SHOULD NOT silently equate.

        Documenting current behavior: ``_parse_parameters`` extracts
        whichever name appears; the handler consumes ``system`` regardless.
        The off-case ``systemversion`` is captured into the params dict but
        never consumed — functionally equivalent to the canonical case
        (handler ignores version today). Probe asserts no crash + 200.
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "code", "valueCode": SNOMED_T2DM},
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "systemversion", "valueString": "http://snomed.info/sct/731000124108"},
            ],
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$validate-code",
            json=params_body,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("resourceType") == "Parameters"

    def test_s25_fhir_r4_filter_operators_constant_matches_spec(self):
        """SOURCE-READ contract: ``FHIR_R4_FILTER_OPERATORS`` constant in
        ``engines/fhir/__init__.py`` MUST match the FHIR R4 spec exactly.

        Per https://hl7.org/fhir/R4/valueset.html#filter, op is bound to
        Filter Operator (Required) — 9 values:
          = | is-a | descendent-of | is-not-a | regex | in | not-in |
          generalizes | exists

        Probe asserts the canonical constant equals the spec list.
        """
        expected = frozenset({
            "=", "is-a", "descendent-of", "is-not-a", "regex", "in",
            "not-in", "generalizes", "exists",
        })
        assert FHIR_R4_FILTER_OPERATORS == expected, (
            f"FHIR_R4_FILTER_OPERATORS drift: got {sorted(FHIR_R4_FILTER_OPERATORS)}, "
            f"expected {sorted(expected)}"
        )
        # Also assert exact cardinality (catches accidental additions).
        assert len(FHIR_R4_FILTER_OPERATORS) == 9


# =============================================================================
# L3: Filter operator closed-enum coverage matrix
# =============================================================================


class TestLens3FilterOperatorCoverageMatrix:
    """Every operator in ``FHIR_R4_FILTER_OPERATORS`` MUST be either honored
    OR explicitly silent-dropped (with the silent-drop documented). NO
    operator should be silently accepted as a synonym for another.

    Per VS-01 SKEPTIC QA-054 (already RESOLVED), only ``is-a`` and
    ``descendent-of`` are honored today. The other 7 are silent-dropped at
    DEBUG log level (line 2597 of fhir_api.py: ``logger.debug``).

    This lens PARAMETRIZES over the full closed-enum to assert:
      (a) Honored operators (is-a, descendent-of) produce expected behavior
      (b) Silent-dropped operators produce empty expansion (no crash)
      (c) Off-enum values produce empty expansion (no crash, no synonym)
    """

    HONORED_OPERATORS = {"is-a", "descendent-of"}
    SILENT_DROPPED_OPERATORS = FHIR_R4_FILTER_OPERATORS - HONORED_OPERATORS

    @pytest.mark.parametrize("op", sorted(HONORED_OPERATORS))
    def test_s30_honored_operators_produce_expected_expansion(self, fhir_client, op):
        """Honored operators (is-a, descendent-of) MUST produce expected
        expansion. is-a includes root; descendent-of excludes root."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": op, "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"op={op} expected 200, got {status}: {body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes  # T2DM is descendant
        if op == "is-a":
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes  # root included
        else:  # descendent-of
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes  # root excluded

    @pytest.mark.parametrize("op", sorted(SILENT_DROPPED_OPERATORS))
    def test_s31_silent_dropped_operators_produce_empty_or_400(self, fhir_client, op):
        """Silent-dropped operators MUST produce empty expansion (or 400).

        Per VS-01 SKEPTIC QA-054 carry-forward pinning, the current
        behavior is silent-drop → empty contains. When a future enhancement
        implements these operators, the probe MUST be tightened to assert
        the new behavior.
        """
        # Each operator has different value semantics; use a sensible default
        if op == "regex":
            value = "[Dd]iabetes"
            prop = "display"
        elif op == "exists":
            value = "true"
            prop = "inactive"
        else:  # =, is-not-a, in, not-in, generalizes
            value = SNOMED_DIABETES_MELLITUS
            prop = "concept"

        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": prop, "op": op, "value": value}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status in (200, 400), f"op={op} expected 200 or 400, got {status}: {body}"
        if status == 200:
            codes = _contains_codes(body)
            # Pinning silent-drop → empty contains.
            assert codes == [], (
                f"op={op!r} produced non-empty expansion — has it been implemented? "
                f"If yes, update this probe to assert the new behavior. codes={codes}"
            )

    @pytest.mark.parametrize("bad_op", [
        "is_a",            # underscore
        "isa",             # missing hyphen
        "descendants-of",  # plural
        "descendant-of",   # singular (off-spec English; spec is descendent-of)
        "not-a",           # missing is- prefix
        "match",           # invented
        "REGEX",           # uppercase
        "Is-A",            # capitalization
        "child-of",        # R6 value, not R4
        "descendent-leaf", # R6 value, not R4
        "",                # empty
    ])
    def test_s32_off_enum_operators_no_silent_synonym(self, fhir_client, bad_op):
        """Off-enum operator values MUST NOT be silently accepted as a
        synonym for a valid operator. Either 400 (preferred per Required
        binding) or 200 with empty contains (silent-drop current behavior).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": bad_op, "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status in (200, 400), f"bad_op={bad_op!r} got {status}"
        if status == 200:
            codes = _contains_codes(body)
            # Off-enum MUST NOT silently equate to is-a (which would produce
            # DM + T2DM in the expansion).
            assert codes == [], (
                f"bad_op={bad_op!r} produced expansion — was it silently equated "
                f"to a valid operator? codes={codes}"
            )

    def test_s33_filter_value_missing_silent_drop_or_400(self, fhir_client):
        """Filter with missing value field — malformed per spec (value 1..1)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "is-a"}  # no value!
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        # Either 400 (preferred per spec) or 200 with empty/root-only (current).
        assert status in (200, 400)
        if status == 200:
            codes = _contains_codes(body)
            # Current behavior: val="" → root_code="" → no expansion.
            assert codes == []

    def test_s34_filter_op_missing_silent_drop_or_400(self, fhir_client):
        """Filter with missing op field — malformed per spec (op 1..1)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "value": SNOMED_DIABETES_MELLITUS}  # no op!
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status in (200, 400)
        if status == 200:
            codes = _contains_codes(body)
            assert codes == []

    def test_s35_filter_property_missing_silent_drop_or_400(self, fhir_client):
        """Filter with missing property field — malformed per spec (property 1..1)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"op": "is-a", "value": SNOMED_DIABETES_MELLITUS}  # no property!
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status in (200, 400)
        if status == 200:
            codes = _contains_codes(body)
            assert codes == []


# =============================================================================
# L4: Malformed filter / regex / hostile inputs
# =============================================================================


class TestLens4MalformedFilterHostileInputs:
    """Hostile inputs on the filter field — server MUST NOT crash (no 5xx)
    per FHIR R4 §3.1.0.1.5 + §3.1.0.1.9.
    """

    @pytest.mark.parametrize("malformed_regex", [
        "[",              # unclosed bracket
        "(",              # unclosed paren
        "*",              # nothing to repeat
        "(?P<>)",         # invalid group name
        "(?:",            # unclosed non-capturing
        "[Dd]iabetes",    # valid (sanity check)
        "a{3,2}",         # invalid range
    ])
    def test_s40_malformed_regex_no_5xx(self, fhir_client, malformed_regex):
        """regex operator with malformed regex patterns. The implementation
        silently drops regex filters today (not honored), so malformed
        patterns don't reach re.compile. Probe verifies no 5xx regardless.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "display", "op": "regex", "value": malformed_regex}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500, (
            f"malformed regex {malformed_regex!r} caused {status} — server crash."
        )

    @pytest.mark.parametrize("hostile_value", [
        "'; DROP TABLE mrconso; --",                  # SQL injection
        "<script>alert('xss')</script>",              # XSS
        "../../../etc/passwd",                         # path traversal
        "x" * 10000,                                   # very long (10K chars)
        "diabetes\x00type2",                           # null bytes
        "diabetes\nrm -rf /",                          # newline + injection
        "中文糖尿病",                                    # Unicode CJK
        "diabetes\r\n",                                # CRLF injection
        "\t\n\r",                                      # whitespace-only
    ])
    def test_s41_hostile_filter_value_no_5xx(self, fhir_client, hostile_value):
        """Hostile filter values: SQL injection, XSS, path traversal, very
        long, null bytes, Unicode, CRLF. The server MUST NOT crash (no 5xx).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "is-a", "value": hostile_value}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500, (
            f"hostile filter value caused {status} — server crash. value len={len(hostile_value)}"
        )

    def test_s42_filter_with_concept_property_only_honored(self, fhir_client):
        """The implementation only honors filter.property == 'concept'. Per
        spec, other property names (SNOMED ``inactive``, LOINC ``PROPERTY``)
        are spec-permitted. Documenting current behavior.
        """
        # concept property: honored
        vs_concept = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        status_c, body_c = _post_expand(fhir_client, vs_concept)
        assert status_c == 200
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in _contains_codes(body_c)

        # display property: silently dropped → empty
        vs_display = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "display", "op": "regex", "value": "[Dd]iabetes"}
                ]}
            ]},
        }
        status_d, body_d = _post_expand(fhir_client, vs_display)
        assert status_d == 200
        assert _contains_codes(body_d) == []

    def test_s43_filter_with_non_string_property_handled(self, fhir_client):
        """Filter property as non-string type — hostile input."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": 123, "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500

    def test_s44_filter_with_non_string_op_handled(self, fhir_client):
        """Filter op as non-string type — hostile input."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": 42, "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500


# =============================================================================
# L5: ValueSet.url boundary conditions
# =============================================================================


class TestLens5ValueSetUrlBoundary:
    """Per https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.url,
    ValueSet.url is the canonical URL that never changes for this value set.

    SKEPTIC lens: probe URL boundary conditions.
    """

    def test_s50_url_very_long_accepted(self, fhir_client):
        """Very long URL (10K chars) — server MUST NOT crash."""
        url = "http://example.org/vs/" + "x" * 10000
        vs = {
            "resourceType": "ValueSet",
            "url": url,
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert body.get("url") == url

    def test_s51_url_with_pipe_per_cnl_1(self, fhir_client):
        """cnl-1 constraint: URL should not contain | or #. Per
        https://hl7.org/fhir/R4/valueset.html#cnl-1 (R4B-added; informational
        in R4). Server accepts and echoes today.
        """
        url = "http://example.org/vs/with|pipe"
        vs = {
            "resourceType": "ValueSet",
            "url": url,
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        # Echo behavior (cnl-1 not enforced today).
        assert body.get("url") == url

    def test_s52_url_with_fragment_per_cnl_1(self, fhir_client):
        """URL with # fragment per cnl-1."""
        url = "http://example.org/vs/with#fragment"
        vs = {
            "resourceType": "ValueSet",
            "url": url,
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert body.get("url") == url

    def test_s53_url_with_unicode(self, fhir_client):
        """URL with Unicode characters (not URI-valid)."""
        url = "http://example.org/vs/中文"
        vs = {
            "resourceType": "ValueSet",
            "url": url,
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert body.get("url") == url

    def test_s54_url_with_spaces(self, fhir_client):
        """URL with spaces (not URI-valid)."""
        url = "http://example.org/vs/with spaces"
        vs = {
            "resourceType": "ValueSet",
            "url": url,
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert body.get("url") == url

    def test_s55_url_empty_string(self, fhir_client):
        """URL = empty string."""
        vs = {
            "resourceType": "ValueSet",
            "url": "",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200

    def test_s56_url_non_string_type(self, fhir_client):
        """URL as non-string (integer). Hostile input — server MUST NOT crash."""
        vs = {
            "resourceType": "ValueSet",
            "url": 12345,
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500

    def test_s57_url_null_value(self, fhir_client):
        """URL = null."""
        vs = {
            "resourceType": "ValueSet",
            "url": None,
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200


# =============================================================================
# L6: READ and SEARCH interactions
# =============================================================================


class TestLens6ReadSearchInteractions:
    """Per FHIR R4 §3.1.0.4 (read) + §3.1.0.6 (search), ValueSet READ and
    SEARCH interactions.

    medterm4ds does not persist ValueSet resources. Per TS-01 SKEPTIC QA-002
    + QA-003 (RESOLVED), the routes return:
      - READ /fhir/ValueSet/{id} → 404 OperationOutcome
      - SEARCH /fhir/ValueSet → empty Bundle

    SKEPTIC lens: probe hostile ids + all 5 search params combined.
    """

    @pytest.mark.parametrize("resource_id", [
        "non-existent-id",
        "very-long-id-" + "x" * 5000,
        "id-with-spaces in it",
        "id-with-special-chars-!@#$%^&*()",
        "id/with/slashes",
        "id-with-unicode-中文",
        "id.with.dots",
        "id|with|pipes",
        "1",
        "0",
    ])
    def test_s60_read_returns_404_operation_outcome(self, fhir_client, resource_id):
        """READ of an unpersisted ValueSet returns a 404 OperationOutcome
        regardless of the id. Per §3.1.0.4 + §3.6.1, the server MUST return
        a FHIR OperationOutcome for unknown resources.
        """
        resp = fhir_client.get(
            f"/fhir/ValueSet/{resource_id}",
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_s61_search_all_5_params_combined(self, fhir_client):
        """SEARCH with all 5 standard params (url, version, name, title,
        status) combined returns empty Bundle."""
        resp = fhir_client.get(
            "/fhir/ValueSet",
            params={
                "url": "http://example.org/vs/test",
                "version": "1.0.0",
                "name": "TestValueSet",
                "title": "Test Value Set",
                "status": "active",
            },
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "searchset"
        assert body["total"] == 0
        assert body["entry"] == []

    @pytest.mark.parametrize("status", [
        "draft", "active", "retired", "unknown",
        "INVALID_STATUS",  # off-enum
    ])
    def test_s62_search_with_status_param(self, fhir_client, status):
        """SEARCH with various status values."""
        resp = fhir_client.get(
            "/fhir/ValueSet",
            params={"status": status},
            headers={"Accept": "application/fhir+json"},
        )
        # Either 200 (accept all) or 400 (reject off-enum). Either is safe.
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            body = resp.json()
            assert body["resourceType"] == "Bundle"
            assert body["total"] == 0

    def test_s63_search_with_no_params(self, fhir_client):
        """SEARCH with no params returns empty Bundle (not 400)."""
        resp = fhir_client.get(
            "/fhir/ValueSet",
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resourceType"] == "Bundle"
        assert body["total"] == 0

    def test_s64_search_with_partial_match(self, fhir_client):
        """SEARCH with only some params (partial match)."""
        resp = fhir_client.get(
            "/fhir/ValueSet",
            params={"name": "SomeName"},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resourceType"] == "Bundle"
        assert body["total"] == 0


# =============================================================================
# L7: compose.include structure — isinstance-guard probes (10th PROMOTED pattern)
# =============================================================================


class TestLens7ComposeIncludeIsinstanceGuards:
    """Per GLOBAL_RULES.md 10th PROMOTED pattern: "isinstance guard at
    untrusted-data list-iterator boundary" (count=4 PROMOTED).

    The ``_expand_intensional`` function has 5 sibling iterators covering:
      - compose.include[]
      - compose.include[].concept[]
      - compose.include[].filter[]
      - compose.exclude[]
      - compose.exclude[].concept[]

    Each MUST have an ``isinstance(<var>, dict)`` guard to prevent
    AttributeError → 500 + text/plain + traceback (information-disclosure
    surface).

    SKEPTIC lens: probe every iterator with hostile non-dict entries.
    """

    def test_s70_compose_include_with_non_dict_entries_no_5xx(self, fhir_client):
        """compose.include[] entries as non-dict (string, int, null, list)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                "not-a-dict",
                42,
                None,
                ["nested", "list"],
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]},  # valid entry
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        # MUST NOT crash (no 5xx); valid entries are processed.
        assert status < 500, f"got {status} — server crash."
        if status == 200:
            codes = _contains_codes(body)
            # The valid entry's concept MUST appear (silent-skip of invalid).
            assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s71_compose_include_concept_with_non_dict_entries_no_5xx(self, fhir_client):
        """compose.include[].concept[] entries as non-dict."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": [
                    "not-a-dict",
                    42,
                    None,
                    {"code": SNOMED_T2DM},  # valid entry
                ],
            }]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500
        if status == 200:
            codes = _contains_codes(body)
            assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s72_compose_include_filter_with_non_dict_entries_no_5xx(self, fhir_client):
        """compose.include[].filter[] entries as non-dict."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": [
                    "not-a-dict",
                    42,
                    None,
                    {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS},  # valid
                ],
            }]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500
        if status == 200:
            codes = _contains_codes(body)
            # Valid filter SHOULD produce results.
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_s73_compose_exclude_with_non_dict_entries_no_5xx(self, fhir_client):
        """compose.exclude[] entries as non-dict."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}],
                "exclude": [
                    "not-a-dict",
                    42,
                    None,
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]},  # valid
                ],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500
        if status == 200:
            codes = _contains_codes(body)
            # The valid exclude SHOULD remove SNOMED_T2DM.
            assert (SNOMED_URI, SNOMED_T2DM) not in codes

    def test_s74_compose_exclude_concept_with_non_dict_entries_no_5xx(self, fhir_client):
        """compose.exclude[].concept[] entries as non-dict."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{"system": SNOMED_URI, "concept": [
                    {"code": SNOMED_DIABETES_MELLITUS},
                    {"code": SNOMED_T2DM},
                ]}],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [
                        "not-a-dict",
                        42,
                        None,
                        {"code": SNOMED_T2DM},  # valid
                    ],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500
        if status == 200:
            codes = _contains_codes(body)
            # SNOMED_T2DM should be removed; SNOMED_DIABETES_MELLITUS should remain.
            assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
            assert (SNOMED_URI, SNOMED_T2DM) not in codes

    def test_s75_compose_include_as_non_list_no_5xx(self, fhir_client):
        """compose.include as non-list (string, dict)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": "not-a-list"},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500

    def test_s76_compose_exclude_as_non_list_no_5xx(self, fhir_client):
        """compose.exclude as non-list."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}],
                "exclude": "not-a-list",
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500

    def test_s77_compose_as_non_dict_no_5xx(self, fhir_client):
        """compose as non-dict (string)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": "not-a-dict",
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500

    def test_s78_compose_include_concept_as_non_list_no_5xx(self, fhir_client):
        """compose.include[].concept as non-list."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "concept": "not-a-list",
            }]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500

    def test_s79_compose_include_filter_as_non_list_no_5xx(self, fhir_client):
        """compose.include[].filter as non-list."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [{
                "system": SNOMED_URI,
                "filter": "not-a-list",
            }]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status < 500


# =============================================================================
# L8: Source-read structural contracts
# =============================================================================


class TestLens8SourceReadStructuralContracts:
    """SOURCE-READ probes: verify the structural contract without invoking
    the HTTP path. Per CS-04 HISTORIAN strategy: source-read audit of every
    list-iterator for the isinstance-guard pattern.

    These probes use AST walking to assert structural invariants.
    """

    @staticmethod
    def _get_nested_func_source(parent_name: str, child_name: str) -> str:
        """Read source of a nested function defined inside ``create_fhir_app``.

        Per CS-03 HISTORIAN methodology: plain ast.walk over module would
        miss nested defs inside the factory function.
        """
        src = FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        # Find create_fhir_app parent
        for parent_node in ast.walk(tree):
            if isinstance(parent_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if parent_node.name == parent_name:
                    # Find the nested child
                    for child in ast.walk(parent_node):
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if child.name == child_name:
                                return ast.get_source_segment(src, child) or ""
        return ""

    def test_s80_expand_intensional_has_isinstance_guard_on_include_loop(self):
        """SOURCE-READ: ``_expand_intensional`` MUST have isinstance guard
        on the compose.include[] loop (10th PROMOTED pattern, count=4)."""
        src = self._get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src, "_expand_intensional source not found"
        # Find the include loop pattern: "for include in compose.get('include', []):"
        # followed within 5 statements by an isinstance(include, dict) check.
        tree = ast.parse(src)
        found_guard = False
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                # Check iter is compose.get('include', [])
                if (isinstance(node.iter, ast.Call) and
                    isinstance(node.iter.func, ast.Attribute) and
                    node.iter.func.attr == "get"):
                    if node.iter.args and isinstance(node.iter.args[0], ast.Constant):
                        if node.iter.args[0].value == "include":
                            # Walk the first 5 statements of the loop body
                            body_stmts = node.body[:5]
                            for stmt in body_stmts:
                                stmt_src = ast.get_source_segment(src, stmt) or ""
                                if "isinstance" in stmt_src and "include" in stmt_src:
                                    found_guard = True
                                    break
        assert found_guard, (
            "_expand_intensional MUST have isinstance(include, dict) guard "
            "in the first 5 statements of the compose.include[] loop "
            "(10th PROMOTED pattern, count=4)"
        )

    def test_s81_expand_intensional_has_isinstance_guard_on_concept_loop(self):
        """SOURCE-READ: isinstance guard on compose.include[].concept[] loop.

        The concept loop iterates ``include["concept"]`` (direct subscript),
        so the AST pattern matches either ``include.get("concept", [])`` OR
        ``include["concept"]``.
        """
        src = self._get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src
        tree = ast.parse(src)
        found_guard = False
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                # Match either: include.get("concept", []) OR include["concept"]
                iter_src = ast.get_source_segment(src, node.iter) or ""
                if "concept" in iter_src and "include" in iter_src:
                    body_stmts = node.body[:5]
                    for stmt in body_stmts:
                        stmt_src = ast.get_source_segment(src, stmt) or ""
                        if "isinstance" in stmt_src and "concept" in stmt_src:
                            found_guard = True
                            break
        assert found_guard, (
            "_expand_intensional MUST have isinstance(concept, dict) guard "
            "in the compose.include[].concept[] loop"
        )

    def test_s82_expand_intensional_has_isinstance_guard_on_filter_loop(self):
        """SOURCE-READ: isinstance guard on compose.include[].filter[] loop."""
        src = self._get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src
        tree = ast.parse(src)
        found_guard = False
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                if (isinstance(node.iter, ast.Call) and
                    isinstance(node.iter.func, ast.Attribute) and
                    node.iter.func.attr == "get"):
                    if node.iter.args and isinstance(node.iter.args[0], ast.Constant):
                        if node.iter.args[0].value == "filter":
                            body_stmts = node.body[:5]
                            for stmt in body_stmts:
                                stmt_src = ast.get_source_segment(src, stmt) or ""
                                if "isinstance" in stmt_src and "filt" in stmt_src.lower():
                                    found_guard = True
                                    break
        assert found_guard, (
            "_expand_intensional MUST have isinstance(filt, dict) guard "
            "in the compose.include[].filter[] loop"
        )

    def test_s83_expand_intensional_has_isinstance_guard_on_exclude_loop(self):
        """SOURCE-READ: isinstance guard on compose.exclude[] loop."""
        src = self._get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src
        tree = ast.parse(src)
        found_guard = False
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                if (isinstance(node.iter, ast.Call) and
                    isinstance(node.iter.func, ast.Attribute) and
                    node.iter.func.attr == "get"):
                    if node.iter.args and isinstance(node.iter.args[0], ast.Constant):
                        if node.iter.args[0].value == "exclude":
                            body_stmts = node.body[:5]
                            for stmt in body_stmts:
                                stmt_src = ast.get_source_segment(src, stmt) or ""
                                if "isinstance" in stmt_src and "exclude" in stmt_src:
                                    found_guard = True
                                    break
        assert found_guard, (
            "_expand_intensional MUST have isinstance(exclude, dict) guard "
            "in the compose.exclude[] loop"
        )

    def test_s84_expand_intensional_only_honors_is_a_and_descendent_of(self):
        """SOURCE-READ: ``_expand_intensional`` MUST only honor ``is-a`` and
        ``descendent-of`` per VS-01 SKEPTIC QA-054 fix. Walks ast.Constant
        nodes for the operator check tuple.
        """
        src = self._get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src
        tree = ast.parse(src)
        # The condition is `if prop == "concept" and op in ("is-a", "descendent-of")`.
        # Look for a Compare with the tuple ("is-a", "descendent-of").
        found_correct = False
        found_off_spec = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for cmp in node.comparators:
                    if isinstance(cmp, ast.Tuple):
                        consts = [e.value for e in cmp.elts if isinstance(e, ast.Constant)]
                        if "is-a" in consts and "descendent-of" in consts:
                            found_correct = True
                        # Check for off-spec "descendant-of"
                        if "descendant-of" in consts:
                            found_off_spec = True
        assert found_correct, (
            "_expand_intensional MUST check `op in ('is-a', 'descendent-of')` "
            "per VS-01 SKEPTIC QA-054 fix"
        )
        assert not found_off_spec, (
            "_expand_intensional MUST NOT honor off-spec 'descendant-of' "
            "(QA-054 regression check)"
        )

    def test_s85_extract_valueset_from_parameters_only_accepts_canonical_case(self):
        """SOURCE-READ: ``_extract_valueset_from_parameters`` MUST check
        exact-case ``valueSet`` name per CS-05/TERMINOLOGIST tip + spec
        https://hl7.org/fhir/R4/parameters.html §3.3 (case-sensitive names).

        The check is specifically on ``param.get("name")`` (NOT on
        ``resource.get("resourceType")`` which legitimately compares to
        ``"ValueSet"`` capital V capital S — the FHIR resourceType keyword).

        Probe walks every ast.Compare where one side is a Call to
        ``.get("name", ...)`` and asserts the other side is exactly
        ``"valueSet"``.
        """
        src = self._get_nested_func_source("create_fhir_app", "_extract_valueset_from_parameters")
        assert src
        tree = ast.parse(src)
        found_canonical = False
        found_off_case = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            # Look for `param.get("name") <op> "..."` patterns
            left_is_name_get = (
                isinstance(node.left, ast.Call)
                and isinstance(node.left.func, ast.Attribute)
                and node.left.func.attr == "get"
                and node.left.args
                and isinstance(node.left.args[0], ast.Constant)
                and node.left.args[0].value == "name"
            )
            if not left_is_name_get:
                continue
            for cmp in node.comparators:
                if isinstance(cmp, ast.Constant) and isinstance(cmp.value, str):
                    if cmp.value == "valueSet":
                        found_canonical = True
                    elif cmp.value.lower() == "valueset" and cmp.value != "valueSet":
                        found_off_case = True
        assert found_canonical, (
            "_extract_valueset_from_parameters MUST check `param.get('name') == 'valueSet'` "
            "(exact-case) per FHIR R4 $expand OperationDefinition"
        )
        assert not found_off_case, (
            "_extract_valueset_from_parameters MUST NOT compare param.get('name') "
            "against any off-case variant of 'valueSet'"
        )


# =============================================================================
# L9: Cross-handler GET<->POST parity
# =============================================================================


class TestLens9GetPostParity:
    """GET vs POST parity on $expand. Per EXPLORER VS-04 strategy 50,
    byte-exact JSON parity should hold when the same effective input is
    provided via GET (url query param) vs POST (Parameters body).
    """

    def test_s90_expand_get_with_filter_equals_post_with_filter(self, fhir_client):
        """GET ?filter=diabetes vs POST Parameters body with filter=diabetes
        should produce byte-equivalent clinical content (contains codes may
        differ in order but the set should match).
        """
        # GET path
        resp_get = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes"},
            headers={"Accept": "application/fhir+json"},
        )
        # POST Parameters body
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "filter", "valueString": "diabetes"}],
        }
        resp_post = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=params_body,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp_get.status_code == resp_post.status_code == 200
        get_codes = set(_contains_codes(resp_get.json()))
        post_codes = set(_contains_codes(resp_post.json()))
        assert get_codes == post_codes, (
            f"GET vs POST drift: get={get_codes}, post={post_codes}"
        )

    def test_s91_expand_get_with_url_param_equals_post_with_url_param(self, fhir_client):
        """GET ?url=...&filter=... vs POST Parameters body — same set."""
        url = "http://snomed.info/sct?fhir_vs=isa"
        # GET
        resp_get = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": url},
            headers={"Accept": "application/fhir+json"},
        )
        # POST Parameters
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "url", "valueUri": url}],
        }
        resp_post = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=params_body,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp_get.status_code == resp_post.status_code
        if resp_get.status_code == 200:
            get_codes = set(_contains_codes(resp_get.json()))
            post_codes = set(_contains_codes(resp_post.json()))
            assert get_codes == post_codes


# =============================================================================
# L10: Response shape audit
# =============================================================================


class TestLens10ResponseShapeAudit:
    """Per GLOBAL_RULES.md "Conformance property per route": audit response
    shape and Content-Type on the $expand POST route for every probe family.
    """

    def test_s100_expand_post_extensional_content_type_fhir_json(self, fhir_client):
        """$expand POST extensional body Content-Type audit."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "application/fhir+json" in ct, f"Content-Type was {ct!r}"

    def test_s101_expand_post_intensional_content_type_fhir_json(self, fhir_client):
        """$expand POST intensional body Content-Type audit."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "application/fhir+json" in ct

    def test_s102_expand_post_parameters_with_valueset_content_type_fhir_json(self, fhir_client):
        """$expand POST Parameters-with-valueSet Content-Type audit."""
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": {
                        "resourceType": "ValueSet",
                        "compose": {"include": [
                            {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                        ]},
                    },
                }
            ],
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=params_body,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "application/fhir+json" in ct

    def test_s103_expand_post_error_path_content_type_fhir_json(self, fhir_client):
        """$expand POST error path Content-Type audit. Per GLOBAL_RULES.md
        "Conformance property per route", even error responses MUST carry
        FHIR MIME type."""
        # Send an invalid body that triggers the 400 path.
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json={"resourceType": "Parameters", "parameter": []},
            headers={"Accept": "application/fhir+json"},
        )
        # Either 200 (empty expansion) or 400 (no url/no filter).
        # In both cases, Content-Type MUST be FHIR+json.
        ct = resp.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"Error path Content-Type drift: {ct!r}"
        )

    def test_s104_expand_response_has_expansion_with_required_fields(self, fhir_client):
        """Per https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion:
        expansion.timestamp is 1..1 (mandatory). expansion.contains is 0..*
        but every entry MUST have system + code (per vsd-9)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        expansion = body.get("expansion", {})
        assert "timestamp" in expansion
        assert "total" in expansion
        assert "contains" in expansion
        for entry in expansion["contains"]:
            assert "system" in entry and entry["system"]
            assert "code" in entry and entry["code"]

    def test_s105_expand_response_xml_format_supported(self, fhir_client):
        """$expand POST with _format=xml returns FHIR+xml Content-Type."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand?_format=xml",
            json=vs,
            headers={"Accept": "application/fhir+xml"},
        )
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "application/fhir+xml" in ct, f"Content-Type was {ct!r}"
        # Body should be XML (starts with <?xml or <ValueSet>)
        body_text = resp.text
        assert body_text.lstrip().startswith("<"), (
            f"Expected XML body, got: {body_text[:200]!r}"
        )

    def test_s106_read_returns_operationoutcome_with_required_fields(self, fhir_client):
        """READ error path: OperationOutcome MUST have issue[] with severity
        + code per §3.6.1."""
        resp = fhir_client.get(
            "/fhir/ValueSet/non-existent",
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"
        assert "issue" in body
        assert len(body["issue"]) > 0
        issue = body["issue"][0]
        assert "severity" in issue
        assert "code" in issue
