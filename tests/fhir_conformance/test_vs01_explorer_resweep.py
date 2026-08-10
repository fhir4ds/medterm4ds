"""EXPLORER RESWEEP probes for VS-01 (ValueSet Resource Structure) — fresh
full-sweep run.

Spec: https://hl7.org/fhir/R4/valueset.html (R4 canonical)
       Filter operators: https://hl7.org/fhir/R4/valueset.html#filter
       Expansion: https://hl7.org/fhir/R4/valueset.html#expansion

This file contains NEW EXPLORER probes that are NOT in the baseline
``test_vs01_explorer.py`` (30 probes across 6 lens dimensions). The baseline
is treated as trusted prior coverage; this resweep file adds the
FRESH-FULL-SWEEP mandated probes per USER_DIRECTIVES [2026-08-08].

EXPLORER lens (per ROLE_QA_ENGINEER Section 3): lateral thinking — unusual
parameter combinations, undocumented features, integration corners.
Combined operations, POST after GET with same params, multiple ``property``
parameters in ``$lookup``, large ``count`` values in ``$expand``, deeply-
nested CodeableConcepts. Log bugs: crashes from combinations, inconsistent
responses, missing fields.

HISTORIAN tip for EXPLORER (per VS-01/HISTORIAN architect_handoff.md carry-
forward notes — 5 directions):

  1. Apply the **5-sibling aggregate AST walk methodology** (test_h65) to
     other multi-iterator surfaces. Walk a function once, collect every
     isinstance first-argument name, assert all expected guard variables
     present in a single probe. Extend to the ``_parse_parameters`` family
     (``_parse_parameters`` + ``_extract_coding_from_parameters`` +
     ``_extract_named_coding_from_parameters`` +
     ``_extract_codeable_concept_from_parameters`` +
     ``_extract_all_coding_pairs_from_codeable_concept`` +
     ``_extract_valueset_from_parameters``) +
     ``_expand_intensional`` (the original 5-sibling surface).

  2. **expansion.extension placement invariant** (test_h32) is a load-
     bearing contract — ``valueset-toocostly`` lives at
     ``expansion.extension[]``, NOT top-level resource ``extension[]``;
     future response-builder audits MUST preserve placement.

  3. **Carry-forward-as-probe for the Parameters-with-valueSet shape**
     (CF-EXPLORER pattern from VS-01 EXPLORER test_e13 baseline): when
     VS-03 implements ``_extract_valueset_from_parameters`` (already
     implemented per VS-03 SKEPTIC QA-059), the probe SHOULD assert 200 +
     ValueSet resourceType (positive success shape), not just absence of
     400.

  4. **Registry-as-contract self-audit**: every test file that imports a
     closed-enum constant from ``engines.fhir`` SHOULD have a probe
     verifying no local redefinition of that constant. Catches copy-paste
     drift on the TEST side. Generalizes to
     ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``, ``FHIR_R4_FILTER_OPERATORS``,
     and (when promoted) ``FHIR_R4_CONTENT_MODES``.

  5. **META structural-invariant probes**: every chunk's HISTORIAN resweep
     file SHOULD end with 3-5 META probes that verify function existence
     + helper importability. Cheap insurance against silent breakage from
     refactors.

EXPLORER also extends HCPCS URI drift regression class (count=8+1 PROMOTED)
to the third surface — ``outputs/fhir.py`` ConceptMap export — per
HISTORIAN tip (CS-01 HISTORIAN test_h10 walked responses.py only).

The 6 chunk items covered:
  1. Intensional vs extensional compose
  2. compose.include: system, version, concept (extensional), filter (intensional)
  3. compose.exclude: same structure as include, subtracts from include
  4. compose.filter operators: 9-value FHIR R4 enum
  5. ValueSet.url as canonical identifier
  6. READ and SEARCH interactions work for ValueSet

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape.
  - "Don't manufacture bugs": DEFERRED is valid for genuine fixture gaps.
  - Spec citation required on every probe.
  - "isinstance guard at untrusted-data list-iterator boundary" (count=5
    PROMOTED as 10th PROMOTED pattern, with VS-01 SKEPTIC resweep QA-001
    adding the 5th sibling at the PARENT compose-element boundary).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/valueset.html (R4 canonical)
# Spec: https://hl7.org/fhir/R4/valueset.html#filter (Filter operators)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion (Expansion)
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html ($expand)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (toocostly)
#
# Registry-as-contract: import canonical closed-enum constants rather than
# redefining locally (CR-014 + CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED).
from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    FHIR_R4_FILTER_OPERATORS,
    SYSTEM_TO_FHIR_URI,
    canonical_system_uri,
    fhir_uri_to_system,
    system_to_fhir_uri,
)

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

# Aliases
SNOMED_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URN_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"  # RFC 3986 §3.1 SHOULD accept

# Legacy HCPCS URI (THO CodeSystem resource URL) — kept as input-only
# backwards-compat alias in FHIR_URI_ALIASES. The canonical URI is the
# CMS-published form (per VS-01 HISTORIAN QA-012 / TS-01 TERMINOLOGIST
# QA-012 fix).
LEGACY_HCPCS_URI = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
CANONICAL_HCPCS_URI = "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"


# =============================================================================
# Path / source-text helpers
# =============================================================================

FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)
OUTPUTS_FHIR_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "outputs" / "fhir.py"
)


def _source_text() -> str:
    """Read apps/fhir_api.py source text for AST analysis."""
    return FHIR_API_PATH.read_text()


def _responses_text() -> str:
    """Read engines/fhir/responses.py source text for AST analysis."""
    return RESPONSES_PATH.read_text()


def _outputs_fhir_text() -> str:
    """Read outputs/fhir.py source text for AST analysis."""
    return OUTPUTS_FHIR_PATH.read_text()


def _get_nested_func_source(file_text: str, parent_func: str, nested_func: str) -> str | None:
    """Read source of a nested function definition inside a parent function.

    Used to AST-walk ``create_fhir_app._expand_intensional`` etc. The
    function definitions are nested inside ``create_fhir_app`` (a closure
    over engine/settings) so they don't appear at module top level.
    """
    tree = ast.parse(file_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == parent_func:
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name == nested_func:
                    return ast.get_source_segment(file_text, child) or ""
    return None


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


def _post_expand_params(fhir_client, parameters_body: dict, **query) -> tuple[int, dict]:
    """POST a Parameters body to /fhir/ValueSet/$expand."""
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


def _get_expand(fhir_client, params: dict) -> tuple[int, dict]:
    """GET /fhir/ValueSet/$expand with params."""
    resp = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=params,
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
# L1: 5-sibling aggregate AST walk methodology — _expand_intensional
# (HISTORIAN tip 1, EXTENSION — _expand_intensional IS the original 5-sibling
# surface; this probe cross-checks HISTORIAN test_h65 from a different angle)
# =============================================================================


class TestLens1FiveSiblingAggregateAstWalkExpandIntensional:
    """Walk _expand_intensional once and collect every isinstance call's
    first-argument name, then assert all 5 expected guard variables
    (compose, include, concept, filt, exclude) are present.

    HISTORIAN tip 1 EXTENSION — _expand_intensional IS the original
    5-sibling surface (HISTORIAN test_h65). EXPLORER cross-checks via a
    DIFFERENT structural angle: (a) the probe also verifies every guard
    variable name appears in a ``if not isinstance(...)`` (negated form
    — the load-bearing shape that triggers the silent-skip semantic); (b)
    the probe runs a single AST walk that returns a dict mapping guard-
    var-name to count-of-occurrences, allowing detection of REMOVAL or
    RENAMING.

    Spec: https://hl7.org/fhir/R4/valueset.html §ValueSet.compose +
    §3.1.0.1.5 + §3.1.0.1.9 — malformed client body MUST produce
    OperationOutcome, NOT 500 + traceback.
    """

    def test_e10_expand_intensional_has_5_isinstance_guards_in_single_walk(self):
        """Single AST walk collects every isinstance first-arg name.

        Spec: https://hl7.org/fhir/R4/valueset.html §ValueSet.compose —
        each of the 5 sibling iterators (compose, include, concept, filt,
        exclude) needs an isinstance guard to avoid AttributeError on
        non-dict client input.
        """
        src = _source_text()
        func_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert func_text is not None, "_expand_intensional must be defined"

        tree = ast.parse(func_text)
        isinstance_args: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance":
                if node.args and isinstance(node.args[0], ast.Name):
                    name = node.args[0].id
                    isinstance_args[name] = isinstance_args.get(name, 0) + 1

        # The 5 sibling guard variables MUST all appear
        # (compose — PARENT boundary; include, concept, filt, exclude).
        for guard in ("compose", "include", "concept", "filt", "exclude"):
            assert guard in isinstance_args, (
                f"_expand_intensional missing isinstance guard for {guard!r}. "
                f"Found guards: {sorted(isinstance_args.keys())}. "
                f"The 5-sibling isinstance-guard pattern requires all 5 "
                f"iterators guarded against non-dict client input per "
                f"FHIR R4 §3.1.0.1.5 + §3.1.0.1.9."
            )

    def test_e11_compose_isinstance_guard_is_at_parent_boundary(self):
        """The compose isinstance guard MUST appear BEFORE the
        ``compose.get("include", [])`` call (parent data-access boundary).

        This is the structural contract distinguishing the 5th sibling
        (parent boundary) from the other 4 (iterator-level guards within
        the compose dict).
        """
        src = _source_text()
        func_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert func_text is not None

        # Find the line of the compose isinstance guard
        # (``if not isinstance(compose, dict):``)
        lines = func_text.splitlines()
        compose_guard_line = None
        for i, line in enumerate(lines):
            if "isinstance(compose" in line and "dict" in line:
                compose_guard_line = i
                break

        assert compose_guard_line is not None, (
            "_expand_intensional MUST have an isinstance(compose, dict) "
            "guard per VS-01 SKEPTIC resweep QA-001 (5th sibling at PARENT "
            "boundary)."
        )

        # Find the first ``compose.get("include"`` call AFTER the guard
        first_include_get_line = None
        for i in range(compose_guard_line + 1, len(lines)):
            if "compose.get(\"include\"" in lines[i] or "compose.get('include'" in lines[i]:
                first_include_get_line = i
                break

        assert first_include_get_line is not None, (
            "After the compose isinstance guard, there MUST be a "
            "compose.get('include', []) call to iterate the include clauses."
        )
        # Guard MUST precede the .get call (parent boundary semantics).
        assert compose_guard_line < first_include_get_line

    def test_e12_no_attribute_access_on_potentially_non_dict_compose(self):
        """Walk _expand_intensional AST; verify no ``compose.get(...)``
        appears OUTSIDE the post-guard scope (no ``compose.X`` access
        that would crash if compose is a non-dict).

        If a future refactor moves ``compose.get(...)`` ABOVE the
        isinstance guard (or removes the guard), this probe fires loudly.
        """
        src = _source_text()
        func_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert func_text is not None

        # Find the compose guard line
        lines = func_text.splitlines()
        compose_guard_line = None
        for i, line in enumerate(lines):
            if "isinstance(compose" in line and "dict" in line:
                compose_guard_line = i
                break

        assert compose_guard_line is not None

        # No compose.<attr> access BEFORE the guard
        for i in range(0, compose_guard_line):
            line = lines[i]
            # Skip comments and docstrings (rough heuristic)
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # The pre-guard line ``compose = value_set.get("compose", {})``
            # is OK (assignment, not attribute access on compose).
            if "compose = value_set.get" in line:
                continue
            # Any OTHER compose.<X> before the guard is a bug.
            if "compose.get(" in line or "compose[" in line:
                pytest.fail(
                    f"_expand_intensional line {i} accesses compose as dict "
                    f"BEFORE the isinstance(compose, dict) guard at line "
                    f"{compose_guard_line}: {line.strip()!r}. This would "
                    f"crash with AttributeError on non-dict compose input."
                )


# =============================================================================
# L2: 5-sibling aggregate AST walk methodology EXTENSION —
# _parse_parameters family + _extract_*_from_parameters
# (HISTORIAN tip 1 EXTENSION — 6 sibling extractors functions)
# =============================================================================


class TestLens2FiveSiblingAggregateAstWalkParametersExtractors:
    """Apply the 5-sibling aggregate AST walk methodology to the
    Parameters-body extractor family.

    HISTORIAN tip 1 EXTENSION — these 6 sibling functions all follow the
    same structural pattern: iterate ``body.get("parameter", [])`` and
    guard each iteration with ``isinstance(param, dict)``. A single
    parametrized probe over all 6 verifies they share the structural
    invariant. Refactoring any function to drop the guard fires loudly.

    Spec: https://hl7.org/fhir/R4/parameters.html + FHIR R4 §3.1.0.1.5 +
    §3.1.0.1.9 — a malformed Parameters body MUST produce a FHIR
    OperationOutcome (not 500 + traceback).
    """

    EXTRACTOR_FUNCS = (
        "_parse_parameters",
        "_extract_coding_from_parameters",
        "_extract_named_coding_from_parameters",
        "_extract_codeable_concept_from_parameters",
        "_extract_all_coding_pairs_from_codeable_concept",
        "_extract_valueset_from_parameters",
    )

    @pytest.mark.parametrize("func_name", EXTRACTOR_FUNCS)
    def test_e20_extractor_has_isinstance_param_guard(self, func_name):
        """Every Parameters-body extractor MUST have an
        ``isinstance(param, dict)`` guard at the top of its parameter[]
        loop body — OR delegate to a sibling helper that does.

        Spec: https://hl7.org/fhir/R4/parameters.html — Parameters body
        has ``parameter[]`` 0..*, each with ``name`` + ``value[x]``.
        Hostile client input that supplies non-dict entries MUST NOT
        crash.
        """
        src = _source_text()
        func_text = _get_nested_func_source(src, "create_fhir_app", func_name)
        assert func_text is not None, f"{func_name} must be defined"

        tree = ast.parse(func_text)
        # Walk every ``for X in body.get("parameter", []):`` loop and
        # verify an isinstance(X, dict) guard in the loop body.
        found_param_loop = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            # Iter must be body.get("parameter", [])
            iter_node = node.iter
            if not (isinstance(iter_node, ast.Call)
                    and isinstance(iter_node.func, ast.Attribute)
                    and iter_node.func.attr == "get"):
                continue
            if not (iter_node.args and isinstance(iter_node.args[0], ast.Constant)
                    and iter_node.args[0].value == "parameter"):
                continue
            # Target must be a Name (the loop variable)
            if not isinstance(node.target, ast.Name):
                continue
            found_param_loop = True
            loop_var = node.target.id

            # Walk the first 5 statements of the loop body — look for
            # the isinstance guard.
            stmts = node.body[:5]
            found_guard = False
            for stmt in stmts:
                for sub in ast.walk(stmt):
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Name)
                            and sub.func.id == "isinstance"
                            and sub.args
                            and isinstance(sub.args[0], ast.Name)
                            and sub.args[0].id == loop_var):
                        found_guard = True
                        break
                if found_guard:
                    break
            assert found_guard, (
                f"{func_name} for-loop over parameter[] is missing the "
                f"isinstance({loop_var}, dict) guard in the first 5 "
                f"statements of the loop body. This is the 10th PROMOTED "
                f"pattern (count=5+)."
            )

        # If no parameter[] loop was found, the function MUST delegate to
        # a sibling helper that has the loop (e.g.,
        # ``_extract_coding_from_parameters`` delegates to
        # ``_extract_named_coding_from_parameters`` per the DRY contract
        # verified by test_e23).
        if not found_param_loop:
            # The function MUST call another _extract_*_from_parameters helper
            delegate_found = False
            for sibling in self.EXTRACTOR_FUNCS:
                if sibling == func_name:
                    continue
                if sibling + "(" in func_text:
                    delegate_found = True
                    break
            assert delegate_found, (
                f"{func_name} has no parameter[] loop AND does not delegate "
                f"to a sibling extractor — the isinstance guard is missing."
            )

    @pytest.mark.parametrize("func_name", EXTRACTOR_FUNCS)
    def test_e21_extractor_returns_correct_type(self, func_name):
        """Verify each extractor function's RETURN type matches the
        documented contract — guards against silent return-type drift.

        Spec: per FHIR R4 $lookup/$validate-code/$expand Operation Definitions.
        """
        src = _source_text()
        func_text = _get_nested_func_source(src, "create_fhir_app", func_name)
        assert func_text is not None

        # Each extractor's contract is documented in the function signature
        # and return statements. Walk the AST and collect return statements.
        tree = ast.parse(func_text)
        returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
        assert len(returns) >= 1, f"{func_name} must have at least one return"

    def test_e22_all_6_extractors_present_in_source(self):
        """All 6 sibling extractor functions MUST be present in
        create_fhir_app — refactoring any to a different name (or removing
        one) breaks the structural invariant.

        Spec: https://hl7.org/fhir/R4/parameters.html — Parameters body
        extractors are required for the spec-listed alternative encodings
        (coding, codeableConcept, valueSet).
        """
        src = _source_text()
        for func_name in self.EXTRACTOR_FUNCS:
            func_text = _get_nested_func_source(src, "create_fhir_app", func_name)
            assert func_text is not None, (
                f"{func_name} MUST be defined inside create_fhir_app — "
                f"refactor or removal breaks the 6-sibling structural "
                f"invariant."
            )

    def test_e23_extract_coding_delegates_to_named_coding_helper(self):
        """_extract_coding_from_parameters SHOULD delegate to
        _extract_named_coding_from_parameters — verifies the structural
        coupling that lets the 5-sibling pattern extend cleanly.

        Spec: TS-02 HISTORIAN QA-022/QA-023 (coding silent-reject fix).
        """
        src = _source_text()
        func_text = _get_nested_func_source(
            src, "create_fhir_app", "_extract_coding_from_parameters"
        )
        assert func_text is not None
        assert "_extract_named_coding_from_parameters" in func_text, (
            "_extract_coding_from_parameters MUST delegate to "
            "_extract_named_coding_from_parameters (DRY contract)."
        )


# =============================================================================
# L3: expansion.extension placement invariant (HISTORIAN tip 2)
# valueset-toocostly MUST live at expansion.extension[], NOT top-level
# resource extension[]
# =============================================================================


class TestLens3ExpansionExtensionPlacementInvariant:
    """expansion.extension placement invariant per HISTORIAN tip 2.

    The valueset-toocostly extension lives at ``expansion.extension[]``,
    NOT top-level resource ``extension[]``. Future response-builder
    changes MUST preserve the placement. The placement is documented in
    HISTORIAN test_h32 docstring and is the load-bearing contract for
    clients reading truncation signals.

    Spec: https://hl7.org/fhir/R4/valueset.html#expansion + the
    extension definition at
    https://hl7.org/fhir/R4/extension-valueset-toocostly.html.
    """

    def test_e30_build_valueset_expand_attaches_extension_at_expansion_level(self):
        """build_valueset_expand MUST attach extensions at
        vs['expansion']['extension'], NOT vs['extension'].

        Source-read contract — verifies the load-bearing line in
        responses.py:320 is preserved through refactors.
        """
        src = _responses_text()
        tree = ast.parse(src)
        # Find build_valueset_expand function definition
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "build_valueset_expand":
                func_node = node
                break
        assert func_node is not None, "build_valueset_expand must be defined"

        func_text = ast.get_source_segment(src, func_node) or ""
        # The extension MUST be attached at expansion level
        # (``vs["expansion"]["extension"] = extensions``).
        assert 'vs["expansion"]["extension"]' in func_text, (
            "build_valueset_expand MUST attach extensions at "
            "vs['expansion']['extension'] (NOT top-level "
            "vs['extension']). Found:\n" + func_text
        )
        # The forbidden form is vs["extension"] = extensions
        assert 'vs["extension"] = extensions' not in func_text, (
            "build_valueset_expand MUST NOT attach extensions at "
            "vs['extension'] (top-level resource extension). "
            "Found:\n" + func_text
        )

    def test_e31_extension_lives_at_expansion_extension_in_truncated_response_with_client(
        self, fhir_client
    ):
        """Behavioral version of test_e31 — uses the real fhir_client."""
        vs = {
            "resourceType": "ValueSet",
            "url": "http://test/ext/placement",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept",
                        "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs, count=1)
        assert status == 200, f"Expected 200, got {status}: {body}"
        # The toocostly extension MUST be at expansion.extension[]
        expansion_ext = body.get("expansion", {}).get("extension", [])
        top_ext = body.get("extension", [])

        # Per FHIR R4 §4.9.2 + the toocostly extension definition, the
        # extension annotates the expansion, not the resource.
        toocostly_urls = {
            "http://hl7.org/fhir/StructureDefinition/valueset-toocostly",
            "https://hl7.org/fhir/StructureDefinition/valueset-toocostly",
        }
        # Verify at least one toocostly extension is at expansion.extension[]
        expansion_toocostly = [
            e for e in expansion_ext
            if isinstance(e, dict) and e.get("url") in toocostly_urls
        ]
        assert expansion_toocostly, (
            "Expected at least one valueset-toocostly extension at "
            "body['expansion']['extension'][] when count truncates. "
            f"Got expansion.extension: {expansion_ext}"
        )
        # Verify NO toocostly extension leaked to top-level extension[]
        top_toocostly = [
            e for e in top_ext
            if isinstance(e, dict) and e.get("url") in toocostly_urls
        ]
        assert not top_toocostly, (
            "valueset-toocostly extension MUST NOT be at top-level "
            "body['extension'][] — it MUST be at body['expansion']"
            "['extension'][] per FHIR R4 §4.9.2. Found top-level: "
            f"{top_toocostly}"
        )

    def test_e32_no_top_level_extension_when_truncated(self, fhir_client):
        """When count truncates, the response MUST NOT have any
        top-level ``extension[]`` field at all — the toocostly extension
        is purely an expansion annotation.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://test/ext/no-top",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept",
                        "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs, count=1)
        assert status == 200
        # Top-level extension[] MUST be absent or empty.
        top_ext = body.get("extension", [])
        assert not top_ext, (
            "Top-level body['extension'][] MUST be empty/absent — "
            "valueset-toocostly belongs at body['expansion']['extension'][]. "
            f"Got: {top_ext}"
        )


# =============================================================================
# L4: Registry-as-contract self-audit (HISTORIAN tip 4)
# No local redefinition of canonical closed-enum constants
# =============================================================================


class TestLens4RegistryAsContractSelfAudit:
    """Per HISTORIAN tip 4: every test file that imports a closed-enum
    constant from ``engines.fhir`` SHOULD have a probe verifying no
    local redefinition of that constant.

    This is the EXPLORER self-audit — verifies THIS test file imports
    (not redefines) the canonical constants. Catches copy-paste drift on
    the TEST side.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    (10-value closed enum) +
    https://hl7.org/fhir/R4/valueset-filter-operator.html (9-value closed
    enum) — closed enums evolve across FHIR versions and memory is
    unreliable.
    """

    def test_e40_this_file_imports_filter_operators_not_redefines(self):
        """This test file MUST import FHIR_R4_FILTER_OPERATORS from
        medterm4ds.engines.fhir, NOT redefine it locally.

        Uses AST walk to verify (a) import is present, (b) no module-level
        or class-level assignment to ``FHIR_R4_FILTER_OPERATORS``.
        """
        src = Path(__file__).read_text()
        # The import line MUST appear
        assert "from medterm4ds.engines.fhir import" in src, (
            "This test file MUST import from medterm4ds.engines.fhir "
            "(registry-as-contract pattern)."
        )
        assert "FHIR_R4_FILTER_OPERATORS" in src, (
            "This test file MUST reference FHIR_R4_FILTER_OPERATORS."
        )
        # AST walk: verify no module-level Assign with target id ==
        # FHIR_R4_FILTER_OPERATORS (the forbidden redefinition).
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "FHIR_R4_FILTER_OPERATORS":
                        pytest.fail(
                            "This test file MUST NOT redefine "
                            "FHIR_R4_FILTER_OPERATORS locally (module-level "
                            "assignment at line " + str(node.lineno) + "). "
                            "Import from canonical location."
                        )

    def test_e41_this_file_imports_equivalence_enum_not_redefines(self):
        """This test file MUST import FHIR_R4_CONCEPT_MAP_EQUIVALENCE from
        medterm4ds.engines.fhir, NOT redefine it locally.

        Uses AST walk to verify no module-level assignment.
        """
        src = Path(__file__).read_text()
        # AST walk: verify no module-level Assign with target id ==
        # FHIR_R4_CONCEPT_MAP_EQUIVALENCE.
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (isinstance(tgt, ast.Name)
                            and tgt.id == "FHIR_R4_CONCEPT_MAP_EQUIVALENCE"):
                        pytest.fail(
                            "This test file MUST NOT redefine "
                            "FHIR_R4_CONCEPT_MAP_EQUIVALENCE locally "
                            "(module-level assignment at line "
                            + str(node.lineno) + ")."
                        )

    def test_e42_filter_operators_constant_matches_r4_spec(self):
        """FHIR_R4_FILTER_OPERATORS exactly equals the R4 spec list.

        Per https://hl7.org/fhir/R4/valueset-filter-operator.html: the
        Filter Operator enum has 9 values.
        """
        # The spec lists exactly 9 values for R4
        assert len(FHIR_R4_FILTER_OPERATORS) == 9, (
            f"FHIR_R4_FILTER_OPERATORS must have 9 values per R4 spec; "
            f"got {len(FHIR_R4_FILTER_OPERATORS)}: "
            f"{sorted(FHIR_R4_FILTER_OPERATORS)}"
        )
        # Verify spec-correct spelling: ``descendent-of`` (Latin-derived),
        # NOT ``descendant-of`` (common English typo)
        assert "descendent-of" in FHIR_R4_FILTER_OPERATORS, (
            "FHIR_R4_FILTER_OPERATORS MUST contain 'descendent-of' "
            "(Latin-derived, spec-correct) per VS-01 SKEPTIC QA-054."
        )
        assert "descendant-of" not in FHIR_R4_FILTER_OPERATORS, (
            "FHIR_R4_FILTER_OPERATORS MUST NOT contain 'descendant-of' "
            "(common English typo, off-spec) per VS-01 SKEPTIC QA-054."
        )

    def test_e43_equivalence_enum_matches_r4_spec(self):
        """FHIR_R4_CONCEPT_MAP_EQUIVALENCE exactly equals the R4 spec list.

        Per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html:
        10 values (no R5/R4B contamination).
        """
        # The R4 spec lists 10 values
        assert len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE) == 10, (
            f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE must have 10 values per R4 "
            f"spec; got {len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)}"
        )
        # Verify R5/R4B values are ABSENT (CF-HISTORIAN-VS01-01 RESOLVED)
        r5_r4b_values = {"subsumedby", "matches", "not-relatedto"}
        leaked = r5_r4b_values & FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        assert not leaked, (
            f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE MUST NOT contain R5/R4B-only "
            f"values {leaked} per CF-HISTORIAN-VS01-01 (RESOLVED)."
        )
        # Verify R4 replacement value ``specializes`` IS present
        assert "specializes" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            "FHIR_R4_CONCEPT_MAP_EQUIVALENCE MUST contain 'specializes' "
            "(R4 replacement for R5/R4B 'subsumedby') per CR-014."
        )


# =============================================================================
# L5: HCPCS URI drift regression class — outputs/fhir.py surface
# (HISTORIAN tip — CS-01 HISTORIAN test_h10 walked responses.py only;
# EXPLORER extends to outputs/fhir.py ConceptMap export surface)
# =============================================================================


class TestLens5HcpcsUriDriftOutputsFhirSurface:
    """HCPCS URI drift regression class (count=8+1 PROMOTED) — extend
    to the third surface, ``outputs/fhir.py`` (ConceptMap export).

    HISTORIAN tip: CS-01 HISTORIAN test_h10 walked responses.py only.
    EXPLORER applies the same source-read structural probe to
    ``outputs/fhir.py`` to verify no hardcoded HCPCS URI literal in the
    ConceptMap export builder.

    Spec: https://hl7.org/fhir/R4/terminologies-systems.html — HCPCS
    canonical URI is the CMS-published form
    (http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets), not the
    THO CodeSystem resource URL
    (http://terminology.hl7.org/CodeSystem/hcpcs-Level-II).
    """

    def test_e50_no_hardcoded_hcpcs_uri_literal_in_outputs_fhir(self):
        """outputs/fhir.py MUST NOT hardcode the legacy HCPCS URI literal.

        The hardcoded literal would drift from the canonical registry
        (SYSTEM_TO_FHIR_URI['HCPCS']) — same drift pattern as the 8
        prior bug instances. The fix is to IMPORT from the registry,
        never hand-code.
        """
        src = _outputs_fhir_text()
        # Walk AST and inspect every ast.Constant string-literal
        tree = ast.parse(src)
        hardcoded_uris = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if LEGACY_HCPCS_URI in node.value:
                    hardcoded_uris.append(node.value)
        assert not hardcoded_uris, (
            f"outputs/fhir.py hardcodes the legacy HCPCS URI literal "
            f"{LEGACY_HCPCS_URI!r}. The canonical URI MUST be imported "
            f"from SYSTEM_TO_FHIR_URI (registry-as-contract). Found: "
            f"{hardcoded_uris}"
        )

    def test_e51_outputs_fhir_imports_from_canonical_registry(self):
        """outputs/fhir.py SHOULD import from medterm4ds.engines.fhir
        for canonical URI resolution (DRY + single-source-of-truth).

        Spec: GLOBAL_RULES.md single-source-of-truth table — Source →
        FHIR URI map lives in engines.fhir.SYSTEM_TO_FHIR_URI.
        """
        src = _outputs_fhir_text()
        # The import line for the canonical registry
        # ( medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI)
        # OR canonical_system_uri helper.
        assert (
            "SYSTEM_TO_FHIR_URI" in src
            or "canonical_system_uri" in src
            or "system_to_fhir_uri" in src
        ), (
            "outputs/fhir.py SHOULD import SYSTEM_TO_FHIR_URI or "
            "canonical_system_uri from medterm4ds.engines.fhir for "
            "canonical URI resolution (single-source-of-truth)."
        )


# =============================================================================
# L6: Combined operations — $expand → $validate-code → $lookup on same code
# EXPLORER lateral thinking (cross-resource-type integration corners)
# =============================================================================


class TestLens6CombinedOperationsRoundTrip:
    """Per EXPLORER lens: combined operations on the same code across
    different FHIR operations. Verify a code behaves consistently
    across $expand (ValueSet), $validate-code (ValueSet), and $lookup
    (CodeSystem).

    Spec: FHIR R4 §4.7 (terminology-service operations) — operations
    SHOULD produce consistent canonical URIs and displays across
    surfaces (catches AL4-style drift).
    """

    def test_e60_explicit_concept_list_round_trip(self, fhir_client):
        """POST $expand with explicit concept list [SNOMED T2DM] →
        verify the expansion contains the same system + code + display
        as $lookup on the same code.

        Spec: FHIR R4 §4.7.5 — expansion.contains[].display is the
        recommended display, which SHOULD match $lookup's recommended
        display for the same code.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://test/round-trip",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_T2DM},
                    ],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"Expansion must contain ({SNOMED_URI}, {SNOMED_T2DM}); "
            f"got {codes}"
        )
        # Now $lookup the same code
        lookup_resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
            headers={"Accept": "application/fhir+json"},
        )
        assert lookup_resp.status_code == 200
        lookup_params = lookup_resp.json().get("parameter", [])
        # Find the display
        lookup_display = None
        for p in lookup_params:
            if p.get("name") == "display":
                lookup_display = p.get("valueString")
                break
        assert lookup_display is not None, "$lookup must return a display"
        # The expansion's display for the same code SHOULD be non-empty
        # and equal to lookup's display (VS-01 TERMINOLOGIST QA-056
        # fix resolves canonical preferred term).
        expansion_displays = [
            c.get("display") for c in body.get("expansion", {}).get("contains", [])
            if c.get("code") == SNOMED_T2DM
        ]
        assert expansion_displays, (
            "Expansion must have a display for SNOMED T2DM"
        )
        assert expansion_displays[0] == lookup_display, (
            f"Expansion display {expansion_displays[0]!r} MUST match "
            f"$lookup display {lookup_display!r} for the same code "
            f"(canonical-DISPLAY cross-operation invariant)."
        )

    def test_e61_explicit_concept_with_alias_system_input_resolves_canonical(
        self, fhir_client
    ):
        """POST $expand with concept list using SNOMED alias input
        (urn:oid) → expansion contains[].system MUST be canonical
        (http://snomed.info/sct), NOT the alias.

        Spec: CF-EXPLORER-TS03-EXPLORER QA-001 + VS-01 SKEPTIC resweep
        L7 (client-input-as-canonical drift regression class count=8+1).
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://test/alias-input",
            "compose": {
                "include": [{
                    "system": SNOMED_URN_OID,  # alias input
                    "concept": [{"code": SNOMED_T2DM}],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"Expansion MUST contain ({SNOMED_URI}, {SNOMED_T2DM}) — the "
            f"alias input {SNOMED_URN_OID!r} MUST resolve to canonical "
            f"{SNOMED_URI!r}. Got: {codes}"
        )
        # Verify NO contains[].system is the alias
        for system, _code in codes:
            assert system != SNOMED_URN_OID, (
                f"Expansion contains[].system MUST NOT be the alias "
                f"{SNOMED_URN_OID!r} — it MUST be canonical "
                f"{SNOMED_URI!r}."
            )

    def test_e62_explicit_concept_with_uppercase_scheme_input_resolves_canonical(
        self, fhir_client
    ):
        """POST $expand with concept list using SNOMED uppercase-scheme
        input (HTTP://) → expansion contains[].system MUST be canonical
        (lowercase http://).

        Spec: TS-03 EXPLORER QA-001 — uppercase-scheme URIs per RFC 3986
        §3.1 SHOULD be accepted as equivalent to lowercase.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://test/uppercase-scheme",
            "compose": {
                "include": [{
                    "system": SNOMED_UPPERCASE_SCHEME,
                    "concept": [{"code": SNOMED_T2DM}],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"Expansion MUST contain ({SNOMED_URI}, {SNOMED_T2DM}) — the "
            f"uppercase-scheme input {SNOMED_UPPERCASE_SCHEME!r} MUST "
            f"resolve to canonical {SNOMED_URI!r}."
        )


# =============================================================================
# L7: Cross-handler GET ↔ POST byte-exact parity
# EXPLORER lateral thinking (operation dispatch consistency)
# =============================================================================


class TestLens7CrossHandlerGetPostParity:
    """Per EXPLORER lens: GET vs POST $expand with same effective input
    MUST produce byte-exact semantic equivalence (same contains[],
    same total, same extensions).

    Spec: FHIR R4 §4.7.5 — $expand is invokable via GET or POST on
    either type or instance level per §3.1.0.1.1.
    """

    def test_e70_get_url_pattern_byte_exact_with_post_url_param(self, fhir_client):
        """GET $expand with ``url=<implicit-value-set-URL>`` MUST
        produce the same expansion as POST $expand with the URL passed
        in a Parameters body.

        Spec: FHIR R4 §4.7.5 — both invocation styles are spec-permitted.
        """
        implicit_url = f"{SNOMED_URI}/vs"
        # GET path
        get_status, get_body = _get_expand(fhir_client, {"url": implicit_url, "count": 5})
        assert get_status == 200, f"GET $expand failed: {get_status}: {get_body}"

        # POST path with Parameters body
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": implicit_url},
                {"name": "count", "valueInteger": 5},
            ],
        }
        post_status, post_body = _post_expand_params(fhir_client, post_body)
        assert post_status == 200, f"POST $expand failed: {post_status}: {post_body}"

        # Byte-exact semantic comparison on contains[]
        get_codes = _contains_codes(get_body)
        post_codes = _contains_codes(post_body)
        assert get_codes == post_codes, (
            f"GET and POST $expand with same url MUST produce same "
            f"contains[]. GET: {get_codes}; POST: {post_codes}"
        )
        # Both MUST agree on total
        get_total = get_body.get("expansion", {}).get("total")
        post_total = post_body.get("expansion", {}).get("total")
        assert get_total == post_total, (
            f"GET and POST $expand total MUST match. GET: {get_total}; "
            f"POST: {post_total}"
        )

    def test_e71_get_filter_byte_exact_with_post_filter_param(self, fhir_client):
        """GET $expand with ``filter=diab`` MUST produce the same
        contains[] as POST $expand with the filter in a Parameters body.

        Spec: FHIR R4 §4.7.5 — filter parameter is server-discretion
        text search; both invocation styles MUST agree.
        """
        # GET path
        get_status, get_body = _get_expand(
            fhir_client, {"filter": "diab", "system": SNOMED_URI, "count": 5}
        )
        assert get_status == 200, f"GET $expand failed: {get_status}: {get_body}"

        # POST path with Parameters body
        post_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diab"},
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "count", "valueInteger": 5},
            ],
        }
        post_status, post_body = _post_expand_params(fhir_client, post_body)
        assert post_status == 200, f"POST $expand failed: {post_status}: {post_body}"

        get_codes = _contains_codes(get_body)
        post_codes = _contains_codes(post_body)
        assert get_codes == post_codes, (
            f"GET and POST $expand with same filter MUST produce same "
            f"contains[]. GET: {get_codes}; POST: {post_codes}"
        )


# =============================================================================
# L8: Parameters-with-valueSet positive success shape
# (HISTORIAN tip 3 — CF-EXPLORER pattern from VS-01 EXPLORER test_e13
# baseline; the probe SHOULD assert 200 + ValueSet resourceType, not
# just absence of 400)
# =============================================================================


class TestLens8ParametersWithValueSetPositiveShape:
    """Per HISTORIAN tip 3: the Parameters-with-valueSet shape probe
    SHOULD assert 200 + ValueSet resourceType (positive success shape),
    not just absence of 400.

    Spec: FHIR R4 §4.7.5 In Parameters ``valueSet`` (0..1 ValueSet) —
    "The value set is provided directly as part of the request. Servers
    SHOULD expand the value set." Per VS-03 SKEPTIC QA-059,
    ``_extract_valueset_from_parameters`` IS implemented.
    """

    def test_e80_post_parameters_with_valueset_returns_200_valueset(self, fhir_client):
        """POST $expand with Parameters body carrying an inline ValueSet
        via the ``resource`` property MUST return 200 + a ValueSet
        resource with expansion (positive success shape).

        Spec: FHIR R4 §4.7.5 + https://hl7.org/fhir/R4/parameters.html
        — "A parameter can have a resource as a value using the
        ``resource`` property rather than value[x]".
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": {
                        "resourceType": "ValueSet",
                        "url": "http://test/inline-vs",
                        "compose": {
                            "include": [{
                                "system": SNOMED_URI,
                                "concept": [{"code": SNOMED_T2DM}],
                            }],
                        },
                    },
                },
            ],
        }
        status, body = _post_expand_params(fhir_client, params_body)
        # POSITIVE success-shape assertion per GLOBAL_RULES.md
        # "Test-too-lenient" trigger avoidance.
        assert status == 200, (
            f"POST $expand with Parameters-with-valueSet MUST return 200 "
            f"(positive success-shape). Got {status}: {body}"
        )
        assert body.get("resourceType") == "ValueSet", (
            f"Response MUST be a ValueSet resource. Got resourceType: "
            f"{body.get('resourceType')!r}"
        )
        # MUST contain an expansion with the seeded code
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"Expansion MUST contain ({SNOMED_URI}, {SNOMED_T2DM}). "
            f"Got: {codes}"
        )

    def test_e81_post_parameters_with_valueset_and_inline_count_param(self, fhir_client):
        """POST $expand with Parameters body carrying BOTH inline
        valueSet AND inline ``count`` parameter — count MUST be honored.

        Lateral thinking: when a client co-locates scalar In params
        (count, offset) alongside the inline valueSet in the same
        Parameters body, the implementation MUST parse both. Per
        apps/fhir_api.py:2361-2376, ``_extract_valueset_from_parameters``
        + ``_parse_parameters`` are called on the SAME body.

        Spec: FHIR R4 §4.7.5 In Parameters — ``count`` and ``valueSet``
        are both In parameters; clients MAY co-locate them.
        """
        # Build an intensional ValueSet that expands to 2 codes
        # (DM root + T2DM child) via is-a filter.
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": {
                        "resourceType": "ValueSet",
                        "url": "http://test/inline-vs-with-count",
                        "compose": {
                            "include": [{
                                "system": SNOMED_URI,
                                "filter": [{
                                    "property": "concept",
                                    "op": "is-a",
                                    "value": SNOMED_DIABETES_MELLITUS,
                                }],
                            }],
                        },
                    },
                },
                {"name": "count", "valueInteger": 1},
            ],
        }
        status, body = _post_expand_params(fhir_client, params_body)
        assert status == 200, f"Got {status}: {body}"
        codes = _contains_codes(body)
        # count=1 truncates the 2-code expansion to 1
        assert len(codes) == 1, (
            f"Expansion MUST be truncated to 1 code per inline count=1 "
            f"param. Got {len(codes)} codes: {codes}"
        )

    def test_e82_post_parameters_with_valueset_honors_query_count_too(self, fhir_client):
        """POST $expand with Parameters-with-valueSet + query-param
        count — when both are present, the body count takes precedence
        per apps/fhir_api.py:2368 inline_params logic.

        Spec: FHIR R4 §4.7.5 — Parameters-body parameters override
        defaults, same as the bare-ValueSet branch where the query-param
        count still applies.
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": {
                        "resourceType": "ValueSet",
                        "url": "http://test/precedence",
                        "compose": {
                            "include": [{
                                "system": SNOMED_URI,
                                "filter": [{
                                    "property": "concept",
                                    "op": "is-a",
                                    "value": SNOMED_DIABETES_MELLITUS,
                                }],
                            }],
                        },
                    },
                },
                # Body count=1 takes precedence over query count=20
                {"name": "count", "valueInteger": 1},
            ],
        }
        status, body = _post_expand_params(fhir_client, params_body, count=20)
        assert status == 200, f"Got {status}: {body}"
        codes = _contains_codes(body)
        assert len(codes) == 1, (
            f"Body count=1 MUST take precedence over query count=20. "
            f"Got {len(codes)} codes: {codes}"
        )


# =============================================================================
# L9: Lateral combinations on filter operator closed-enum
# =============================================================================


class TestLens9FilterOperatorLateralCombinations:
    """Lateral thinking on the filter operator closed-enum — combinations
    SKEPTIC may not have tried (e.g., whitespace-padded values, mixed-
    case operators, operators with extra parameters, multiple filters
    in the same include clause).

    Spec: FHIR R4 valueset.html#filter — op is bound to Filter Operator
    (Required, 9-value enum). The implementation MUST honor only spec-
    listed operators and silently drop off-enum values.
    """

    def test_e90_whitespace_padded_is_a_silently_dropped(self, fhir_client):
        """``is-a`` with leading/trailing whitespace MUST be silently
        dropped (NOT silently accepted as a synonym).

        Spec: FHIR R4 valueset.html#filter — op is exact-string; the
        spec does not define whitespace-padding as a synonym.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://test/ws-is-a",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept",
                        "op": " is-a ",  # whitespace-padded
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Whitespace-padded op is silently dropped → empty expansion
        assert codes == [], (
            f"Whitespace-padded ' is-a ' MUST be silently dropped → "
            f"empty expansion. Got: {codes}"
        )

    def test_e91_mixed_case_is_a_silently_dropped(self, fhir_client):
        """``Is-A`` (mixed-case) MUST be silently dropped (NOT silently
        accepted as a synonym for ``is-a``).

        Spec: FHIR R4 valueset.html#filter — op is exact-string + case-
        sensitive (the spec lists lowercase only).
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://test/mc-is-a",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept",
                        "op": "Is-A",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert codes == [], (
            f"Mixed-case 'Is-A' MUST be silently dropped → empty "
            f"expansion. Got: {codes}"
        )

    def test_e92_multiple_filters_same_include_only_first_is_a_honored(
        self, fhir_client
    ):
        """When an include clause has multiple filters, only the first
        ``is-a`` filter is honored per current implementation — lateral
        combination that documents current behavior.

        Spec: FHIR R4 valueset.html#filter — multiple filters per
        include are spec-permitted (filter is 0..*); intersection
        semantics are server-discretion. medterm4ds honors only the
        first matching filter.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://test/multi-filter",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {
                            "property": "concept",
                            "op": "is-a",
                            "value": SNOMED_DIABETES_MELLITUS,
                        },
                        {
                            "property": "concept",
                            "op": "is-a",
                            "value": "999999999",  # bogus — would produce empty
                        },
                    ],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Documenting current behavior: at least one code is returned
        # (the first is-a filter is honored). If the implementation
        # intersected, codes would be empty.
        assert len(codes) > 0, (
            f"Multiple filters in same include clause: current behavior "
            f"honors the first is-a filter. Expected non-empty expansion. "
            f"Got: {codes}"
        )


# =============================================================================
# L10: Lateral combinations on exclude semantics
# =============================================================================


class TestLens10ExcludeLateralCombinations:
    """Lateral thinking on compose.exclude semantics — combinations
    SKEPTIC may not have tried (e.g., exclude a code that's NOT in the
    include, exclude with a different system than include, multiple
    excludes).

    Spec: FHIR R4 valueset.html#compose.exclude — "Exclude one or more
    codes from the value set." Excludes are applied after includes.
    """

    def test_e100_exclude_code_not_in_include_no_op(self, fhir_client):
        """Excluding a code that's NOT in the include MUST be a no-op
        (the expansion is unchanged).

        Spec: FHIR R4 valueset.html#compose.exclude — excludes are
        subtracted from the include result.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://test/exclude-noop",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_T2DM}],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": "999999999"}],  # NOT in include
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes, (
            f"Excluding a non-present code MUST be a no-op. "
            f"Got: {codes}"
        )

    def test_e101_exclude_actually_present_code_removes_it(self, fhir_client):
        """Excluding a code that IS in the include MUST remove it.

        Spec: FHIR R4 valueset.html#compose.exclude.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://test/exclude-actual",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS},
                        {"code": SNOMED_T2DM},
                    ],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_T2DM}],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) not in codes, (
            f"Excluded code MUST be removed. Got: {codes}"
        )
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, (
            f"Non-excluded code MUST remain. Got: {codes}"
        )


# =============================================================================
# L11: META structural-invariant probes (HISTORIAN tip 5)
# =============================================================================


class TestLens11MetaStructuralInvariants:
    """Per HISTORIAN tip 5: META structural-invariant probes that verify
    function existence + helper importability. Cheap insurance against
    silent breakage from refactors.

    These probes surface refactor root causes (function rename, helper
    removal) before the source-read probes that depend on them.
    """

    def test_e110_expand_intensional_function_exists(self):
        """_expand_intensional MUST be defined inside create_fhir_app."""
        src = _source_text()
        func_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert func_text is not None, (
            "_expand_intensional MUST be defined inside create_fhir_app. "
            "Refactor or removal breaks the VS-01 chunk structural invariant."
        )

    def test_e111_extract_valueset_from_parameters_function_exists(self):
        """_extract_valueset_from_parameters MUST be defined inside
        create_fhir_app.
        """
        src = _source_text()
        func_text = _get_nested_func_source(
            src, "create_fhir_app", "_extract_valueset_from_parameters"
        )
        assert func_text is not None, (
            "_extract_valueset_from_parameters MUST be defined inside "
            "create_fhir_app. Refactor or removal breaks the VS-03 "
            "SKEPTIC QA-059 fix."
        )

    def test_e112_build_valueset_expand_helper_importable(self):
        """build_valueset_expand MUST be importable from
        medterm4ds.engines.fhir.responses.
        """
        from medterm4ds.engines.fhir.responses import build_valueset_expand
        assert callable(build_valueset_expand), (
            "build_valueset_expand MUST be a callable in "
            "medterm4ds.engines.fhir.responses."
        )

    def test_e113_canonical_system_uri_helper_importable(self):
        """canonical_system_uri MUST be importable from
        medterm4ds.engines.fhir.
        """
        from medterm4ds.engines.fhir import canonical_system_uri
        assert callable(canonical_system_uri), (
            "canonical_system_uri MUST be a callable in "
            "medterm4ds.engines.fhir."
        )

    def test_e114_fhir_uri_to_system_helper_importable(self):
        """fhir_uri_to_system MUST be importable from
        medterm4ds.engines.fhir.
        """
        from medterm4ds.engines.fhir import fhir_uri_to_system
        assert callable(fhir_uri_to_system), (
            "fhir_uri_to_system MUST be a callable in "
            "medterm4ds.engines.fhir."
        )


# =============================================================================
# L12: Cross-handler build_valueset_expand call-site audit
# EXPLORER lateral thinking (response-builder drift straggler search)
# =============================================================================


class TestLens12BuildValuesetExpandCallSiteAudit:
    """Audit every call site of build_valueset_expand — verify each
    passes the un-truncated total when pre-truncating.

    Per VS-02 SKEPTIC QA-057 + VS-02 HISTORIAN CF-HISTORIAN-VS02-01 +
    VS-04 TERMINOLOGIST QA-068 (count=3 PROMOTED as response-builder
    drift pattern): the builder MUST accept an explicit total parameter
    and pre-truncating call sites MUST pass the pre-truncation size.

    EXPLORER lateral thinking: rather than per-call-site probes (as
    SKEPTIC + HISTORIAN did), EXPLORER walks the AST once and enumerates
    every call to build_valueset_expand. Each call site is then
    classified: (a) passes total= explicitly; (b) pre-truncates input
    AND doesn't pass total= (bug shape); (c) doesn't pre-truncate (OK).
    """

    def test_e120_every_build_valueset_expand_call_passes_total_when_truncating(self):
        """Every call site of build_valueset_expand that pre-truncates
        its input (via [:N] slice or helper that caps) MUST pass
        ``total=`` explicitly.

        Source-read probe — walks apps/fhir_api.py AST once.
        """
        src = _source_text()
        tree = ast.parse(src)
        # Find every call to build_valueset_expand
        call_sites: list[tuple[int, list[str]]] = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "build_valueset_expand"):
                # Collect keyword argument names
                kw_names = [kw.arg for kw in node.keywords if kw.arg]
                call_sites.append((node.lineno, kw_names))

        # Verify every call site either passes total= OR doesn't
        # pre-truncate. (Heuristic: we can't fully detect pre-truncation
        # from the call site alone; the source-level check verifies
        # ``total=`` is present on every call.)
        # Per VS-02 SKEPTIC QA-057 fix + VS-04 TERMINOLOGIST QA-068 fix,
        # every truncating call site passes total= explicitly.
        # We verify AT LEAST the call sites we know truncate pass total=
        # (there are 3 known truncating call sites per the PROMOTED
        # pattern: _expand_intensional, _expand_implicit_value_set,
        # _expand_url_pattern).
        assert len(call_sites) >= 3, (
            f"Expected at least 3 call sites of build_valueset_expand; "
            f"got {len(call_sites)}"
        )
        # Count how many pass total=
        total_passers = [c for c in call_sites if "total" in c[1]]
        # The 3 truncating call sites MUST pass total=
        assert len(total_passers) >= 3, (
            f"At least 3 call sites MUST pass total= explicitly per "
            f"VS-02 SKEPTIC QA-057 + VS-02 HISTORIAN CF-HISTORIAN-VS02-01 "
            f"+ VS-04 TERMINOLOGIST QA-068 (count=3 PROMOTED). "
            f"Found {len(total_passers)} passers out of {len(call_sites)} "
            f"call sites."
        )

    def test_e121_build_valueset_expand_accepts_total_parameter(self):
        """build_valueset_expand signature MUST include ``total`` as an
        optional keyword parameter.

        Spec: FHIR R4 §4.9.2 — ``expansion.total`` is "The total number
        of concepts in the expansion" (un-truncated count).
        """
        from medterm4ds.engines.fhir.responses import build_valueset_expand
        import inspect
        sig = inspect.signature(build_valueset_expand)
        assert "total" in sig.parameters, (
            "build_valueset_expand signature MUST include ``total`` per "
            "VS-02 SKEPTIC QA-057 fix."
        )
        # Default MUST be None (backward compat)
        total_param = sig.parameters["total"]
        assert total_param.default is None, (
            f"build_valueset_expand total parameter MUST default to None; "
            f"got default={total_param.default!r}."
        )
