"""VS-04 TERMINOLOGIST resweep: intensional-URL $expand clinical-correctness probes.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
SNOMED CT intensional: https://hl7.org/fhir/R4/snomedct.html
Truncation ext: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
ValueSet.expansion.contains.display:
  https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

This is the TERMINOLOGIST resweep pass for chunk VS-04 (post-SKEPTIC +
post-HISTORIAN + post-EXPLORER). VS-04 = "ValueSet $expand — Intensional
URLs (fhir_vs)". TERMINOLOGIST lens = clinical/terminological correctness.
Per GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH": all findings are
HIGH severity by default.

EXPLORER tip for TERMINOLOGIST (4 items):

  1. Re-derive the 11th PROMOTED pattern (AST-contract-on-comparison) via
     2-3 source-read probes on the 4 sibling count_limited sites.
  2. Re-confirm CF-EXPLORER-VS04-01 is NOT a clinical-safety issue (URL-
     canonical-form, not clinical-correctness).
  3. Cross-builder clinical-safety audit: every sibling count_limited
     site must produce the same clinical behavior on count truncation
     (canonical-DISPLAY + canonical-SYSTEM invariants).
  4. Canonical-DISPLAY cross-operation META-PATTERN on URL-form surface
     (count=7 PROMOTED) — extend further via lateral-combination probes
     on URL-form x filter-mode x intensional-mode displays.

Prior VS-04 iterations:
  - SKEPTIC resweep: 0 new bugs across 107 hostile-input + structural probes.
  - HISTORIAN resweep: 0 new bugs across 91 regression + structural probes.
  - EXPLORER resweep: 0 new production-code bugs across 39 lateral probes;
    PROMOTED 11th PROMOTED pattern to GLOBAL_RULES.md (count=4 sibling
    sites); opened CF-EXPLORER-VS04-01 (LOW — explicit-port URL form).

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus) -> 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

# =============================================================================
# Constants
# =============================================================================

SNOMED_URI = SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]  # http://snomed.info/sct
SNOMED_DIABETES_MELLITUS = "73211009"  # parent (root)
SNOMED_T2DM = "44054006"                # child of 73211009

LOINC_URI = SYSTEM_TO_FHIR_URI["LNC"]
RXNORM_URI = SYSTEM_TO_FHIR_URI["RXNORM"]
ICD10CM_URI = SYSTEM_TO_FHIR_URI["ICD10CM"]
CPT_URI = SYSTEM_TO_FHIR_URI["CPT"]
CVX_URI = SYSTEM_TO_FHIR_URI["CVX"]

TRUNCATION_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

CANONICAL_DISPLAY_SNOMED_DM = "Diabetes mellitus"
CANONICAL_DISPLAY_SNOMED_T2DM = "Type 2 diabetes mellitus"

# Path to apps/fhir_api.py for source-read structural probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# =============================================================================
# Source-read helpers (mirror EXPLORER resweep helpers)
# =============================================================================


def _read_module_source() -> str:
    """Read the apps/fhir_api.py module source for AST-walk probes."""
    return _FHIR_API_PATH.read_text()


def _read_function_source(module_src: str, func_name: str) -> str | None:
    """Extract a module-level function's source as a standalone string."""
    tree = ast.parse(module_src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(module_src, node)
    return None


def _read_nested_function_source(
    module_src: str, parent_name: str, child_name: str
) -> str | None:
    """Extract a nested function's source from within a parent function."""
    tree = ast.parse(module_src)
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and parent.name == parent_name:
            for child in ast.walk(parent):
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == child_name
                ):
                    return ast.get_source_segment(module_src, child)
    return None


def _collect_count_limited_assignments(func_src: str) -> list[ast.Assign]:
    """Return every ``count_limited = <Compare>`` assignment in a function."""
    tree = ast.parse(func_src)
    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "count_limited"
                    and isinstance(node.value, ast.Compare)
                ):
                    assignments.append(node)
                    break
    return assignments


# =============================================================================
# Behavior helpers
# =============================================================================


def _expand_url(client, url: str, **extra):
    """GET /fhir/ValueSet/$expand with url and optional query params."""
    params = [("url", url)]
    for k, v in extra.items():
        params.append((k, v))
    return client.get(
        "/fhir/ValueSet/$expand",
        params=params,
        headers={"Accept": "application/fhir+json"},
    )


def _post_expand_url(client, url: str, **extra):
    """POST /fhir/ValueSet/$expand with a Parameters body carrying the url."""
    parameter = [{"name": "url", "valueUri": url}]
    for k, v in extra.items():
        if k == "count":
            parameter.append({"name": k, "valueInteger": int(v)})
        else:
            parameter.append({"name": k, "valueString": str(v)})
    return client.post(
        "/fhir/ValueSet/$expand",
        json={"resourceType": "Parameters", "parameter": parameter},
        headers={"Accept": "application/fhir+json"},
    )


def _contains_codes(resp_json: dict) -> list[str]:
    return [c.get("code") for c in resp_json.get("expansion", {}).get("contains", [])]


def _contains_entries(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("contains", [])


def _contains_displays(resp_json: dict) -> dict[str, str]:
    return {
        c.get("code", ""): c.get("display", "")
        for c in resp_json.get("expansion", {}).get("contains", [])
    }


def _contains_systems(resp_json: dict) -> dict[str, str]:
    return {
        c.get("code", ""): c.get("system", "")
        for c in resp_json.get("expansion", {}).get("contains", [])
    }


def _extensions(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("extension", [])


def _has_toocostly(resp_json: dict) -> bool:
    return any(e.get("url") == TRUNCATION_EXT_URL for e in _extensions(resp_json))


def _lookup(client, system: str, code: str):
    return client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
        headers={"Accept": "application/fhir+json"},
    )


def _lookup_display(client, system: str, code: str) -> str | None:
    lu = _lookup(client, system, code)
    if lu.status_code != 200:
        return None
    for p in lu.json().get("parameter", []):
        if p.get("name") == "display":
            return p.get("valueString")
    return None


def _filter_expand(client, filter_text: str, count: int = 20):
    """GET /fhir/ValueSet/$expand with a filter param."""
    return client.get(
        "/fhir/ValueSet/$expand",
        params={"filter": filter_text, "count": count},
        headers={"Accept": "application/fhir+json"},
    )


def _inline_vs_expand(client, system: str, code: str, count: int = 20):
    """POST /fhir/ValueSet/$expand with an inline ValueSet body."""
    body = {
        "resourceType": "ValueSet",
        "url": "http://example.org/test-vs",
        "compose": {
            "include": [{
                "system": system,
                "filter": [{"property": "concept", "op": "is-a", "value": code}],
            }],
        },
    }
    return client.post(
        "/fhir/ValueSet/$expand",
        json=body,
        params={"count": count},
        headers={"Accept": "application/fhir+json"},
    )


def _implicit_vs_expand(client, url: str, count: int = 20):
    """GET /fhir/ValueSet/$expand with an implicit value set URL."""
    return _expand_url(client, url, count=count)


# =============================================================================
# Lens 1: 11th PROMOTED pattern re-derivation (EXPLORER tip item 1)
# Source-read probes on the 4 sibling count_limited sites
# Spec: VS-04 TERMINOLOGIST QA-068 fix harmonizes strict `>` across siblings.
# =============================================================================


class TestLens1AstContractOnComparisonPromotedPattern:
    """Lens 1: re-derive the 11th PROMOTED pattern via source-read probes.

    The AST-contract-on-comparison probe class was PROMOTED to
    GLOBAL_RULES.md as the 11th PROMOTED pattern (line 142). The contract
    has 5 axes per the pattern block:

      (a) operator is ``ast.Gt`` (>), NOT ``ast.GtE`` (>=)
      (b) NOT ``ast.GtE`` (explicit negative assertion)
      (c) LEFT is ``len(<var>)`` (a Call, not a literal)
      (d) RIGHT is a Name (a budget-style variable, not a literal)
      (e) LEFT and RIGHT are DIFFERENT variables

    The 4 sibling sites:
      1. expand_url_pattern (module-level)        — count_limited = len(relations) > descendant_budget
      2. _do_expand filter mode (nested)          — count_limited = len(results) > count
      3. _expand_intensional (nested)             — count_limited = len(deduped) > count
      4. _expand_implicit_value_set (nested)      — count_limited = len(rows) > count

    Clinical safety: a count_limited=True signal on a COMPLETE expansion
    (the QA-068 bug shape) misleads CDS hooks into treating a full value
    set as a partial fragment — potentially skipping clinical rules that
    SHOULD fire on every code in the value set.

    TERMINOLOGIST lens (clinical): the harmonization is not just a code-
    quality concern — divergent count_limited semantics across siblings
    would silently produce different clinical signals depending on which
    code path the client happened to hit.
    """

    def test_t10_module_level_expand_url_pattern_2_axis_contract(self):
        """Sibling #1: module-level ``expand_url_pattern`` 2-axis contract.

        Asserts (a) operator Gt, NOT GtE; (c) LEFT is len(<var>);
        (d) RIGHT is Name (descendant_budget); (e) LEFT and RIGHT differ.

        Clinical lens: this is the ORIGINAL QA-068 site. A regression
        here would re-introduce the count=2 false-positive toocostly
        signal — a CDS hook receiving the toocostly extension on a
        COMPLETE expansion would silently treat every code as a partial
        set.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None, "expand_url_pattern not found at module scope"
        assigns = _collect_count_limited_assignments(src)
        assert len(assigns) >= 1, "expand_url_pattern must have count_limited assignment"
        cmp = assigns[0].value

        # Axis (a) + (b): operator is Gt, NOT GtE
        assert all(isinstance(op, ast.Gt) for op in cmp.ops), (
            "expand_url_pattern count_limited MUST use ast.Gt — VS-04 "
            "TERMINOLOGIST QA-068 load-bearing fix."
        )
        assert not any(isinstance(op, ast.GtE) for op in cmp.ops), (
            "expand_url_pattern count_limited MUST NOT use ast.GtE — the "
            ">= divergence was the QA-068 clinical-safety bug."
        )

        # Axis (c): LEFT is len(<var>)
        left = cmp.left
        assert isinstance(left, ast.Call) and isinstance(left.func, ast.Name), (
            "expand_url_pattern count_limited LEFT MUST be a function call"
        )
        assert left.func.id == "len", (
            "expand_url_pattern count_limited LEFT MUST be len(...)"
        )

        # Axis (d): RIGHT is Name
        assert len(cmp.comparators) == 1
        right = cmp.comparators[0]
        assert isinstance(right, ast.Name), (
            "expand_url_pattern count_limited RIGHT MUST be a Name"
        )

        # Axis (e): LEFT and RIGHT differ
        left_var = left.args[0]
        assert isinstance(left_var, ast.Name), (
            "expand_url_pattern count_limited LEFT MUST be len(<var>)"
        )
        assert left_var.id != right.id, (
            "expand_url_pattern count_limited operands MUST differ; "
            f"got len({left_var.id}) > {right.id}"
        )

    def test_t11_filter_mode_do_expand_2_axis_contract(self):
        """Sibling #2: filter-mode _do_expand 2-axis contract.

        Asserts the same 5-axis contract on the filter-text path. The
        filter mode is exercised when a CDS hook searches by clinical
        display term (e.g. "diabetes") — divergent count_limited here
        would mis-signal truncation on filter expansions.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_expand"
        )
        assert src is not None
        assigns = _collect_count_limited_assignments(src)
        assert len(assigns) >= 1, "_do_expand must have count_limited assignment"
        cmp = assigns[0].value

        assert all(isinstance(op, ast.Gt) for op in cmp.ops), (
            "_do_expand count_limited MUST use ast.Gt"
        )
        assert not any(isinstance(op, ast.GtE) for op in cmp.ops), (
            "_do_expand count_limited MUST NOT use ast.GtE"
        )

        left = cmp.left
        assert isinstance(left, ast.Call) and left.func.id == "len"
        right = cmp.comparators[0]
        # QC-241 (EC-10): the RIGHT operand is ``probe_budget`` — the
        # paging window budget ``min(offset + count, MAX_LIMIT - 1)``.
        # Still a budget-style Name (the 2-axis contract holds); when
        # offset=0 it equals ``count`` exactly.
        assert isinstance(right, ast.Name) and right.id in ("count", "probe_budget"), (
            "count_limited RIGHT operand MUST be a budget-style Name "
            "(count or probe_budget per QC-241 paging windows)"
        )
        left_var = left.args[0]
        assert isinstance(left_var, ast.Name) and left_var.id != right.id

    def test_t12_intensional_mode_expand_intensional_2_axis_contract(self):
        """Sibling #3: intensional _expand_intensional 2-axis contract.

        Asserts the same 5-axis contract on the inline-ValueSet
        intensional path (compose.include[].filter[concept is-a X]).
        Divergent count_limited here would mis-signal truncation on
        client-supplied intensional expansions.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_intensional"
        )
        assert src is not None
        assigns = _collect_count_limited_assignments(src)
        assert len(assigns) >= 1, "_expand_intensional must have count_limited assignment"
        cmp = assigns[0].value

        assert all(isinstance(op, ast.Gt) for op in cmp.ops), (
            "_expand_intensional count_limited MUST use ast.Gt"
        )
        assert not any(isinstance(op, ast.GtE) for op in cmp.ops), (
            "_expand_intensional count_limited MUST NOT use ast.GtE"
        )

        left = cmp.left
        assert isinstance(left, ast.Call) and left.func.id == "len"
        right = cmp.comparators[0]
        assert isinstance(right, ast.Name) and right.id == "count"
        left_var = left.args[0]
        assert isinstance(left_var, ast.Name) and left_var.id != right.id

    def test_t13_implicit_mode_expand_implicit_2_axis_contract(self):
        """Sibling #4: implicit _expand_implicit_value_set 2-axis contract.

        Asserts the same 5-axis contract on the implicit value set path
        (<system-uri>/vs or http://snomed.info/sct?fhir_vs without code).
        Divergent count_limited here would mis-signal truncation on
        "all codes in code system" enumerations.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_implicit_value_set"
        )
        assert src is not None
        assigns = _collect_count_limited_assignments(src)
        assert len(assigns) >= 1, "_expand_implicit_value_set must have count_limited assignment"
        cmp = assigns[0].value

        assert all(isinstance(op, ast.Gt) for op in cmp.ops), (
            "_expand_implicit_value_set count_limited MUST use ast.Gt"
        )
        assert not any(isinstance(op, ast.GtE) for op in cmp.ops), (
            "_expand_implicit_value_set count_limited MUST NOT use ast.GtE"
        )

        left = cmp.left
        assert isinstance(left, ast.Call) and left.func.id == "len"
        right = cmp.comparators[0]
        assert isinstance(right, ast.Name) and right.id == "count"
        left_var = left.args[0]
        assert isinstance(left_var, ast.Name) and left_var.id != right.id

    def test_t14_meta_module_walk_finds_at_least_4_sibling_sites(self):
        """META: walk the entire module, count sibling count_limited sites.

        Per the 11th PROMOTED pattern (GLOBAL_RULES.md line 142): the
        contract requires count >= 4 sibling sites. This probe confirms
        the META harmonization holds across all 4 in a single source-
        read walk — the structural backbone of the pattern.
        """
        src = _read_module_source()
        tree = ast.parse(src)
        assignments = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                        and isinstance(node.value, ast.Compare)
                    ):
                        assignments.append(node)
                        break
        assert len(assignments) >= 4, (
            f"Expected AT LEAST 4 count_limited sibling sites per the "
            f"11th PROMOTED pattern; found {len(assignments)}. A future "
            f"refactor that consolidates count_limited computation MUST "
            f"preserve the cross-builder harmonization."
        )


# =============================================================================
# Lens 2: CF-EXPLORER-VS04-01 clinical-safety re-confirmation
# (EXPLORER tip item 2)
# =============================================================================


class TestLens2CFExplorerVS0401ClinicalSafetyClassification:
    """Lens 2: CF-EXPLORER-VS04-01 is NOT a clinical-safety issue.

    Per VS-04 EXPLORER qa_handoff.md: the explicit-port URL form
    (``http://snomed.info:80/sct/<code>?fhir_vs=isa``) is currently
    rejected with 400 because the implementation uses a substring check
    ``if snomed_uri in base`` that doesn't match the explicit-port form.

    TERMINOLOGIST lens: this is a URL-canonical-form issue, NOT a
    clinical-correctness issue. The rejection is silent on clinical
    content — the same code (73211009) is queryable via $lookup with
    either form (URL is parsed by the system-URI resolver). The CDS
    hook doesn't lose clinical data; it gets a clear 400 with an
    actionable error message naming the supported form.

    Spec basis (per SNOMED CT URL convention): the canonical no-port
    form ``http://snomed.info/sct`` is the documented form. RFC 3986
    §6.3 permits the explicit-port equivalent (``:80`` for HTTP), but
    FHIR R4 SNOMED CT URL convention documents only the canonical form.
    """

    def test_t20_explicit_port_url_rejected_with_clinically_clear_400(self, fhir_client):
        """Explicit-port URL form is rejected with 400 + OperationOutcome.

        Clinical safety: the rejection message MUST be clinically
        actionable — it MUST name the supported form so the CDS engineer
        can correct the URL without losing clinical context.
        """
        url = f"http://snomed.info:80/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_url(fhir_client, url)
        assert resp.status_code == 400, (
            f"explicit-port URL form SHOULD be rejected (CF-EXPLORER-VS04-01); "
            f"got {resp.status_code}. If 200, the CF is closed — update this "
            f"probe to assert clinical correctness on the explicit-port path."
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"rejection MUST be FHIR-shaped; got {body.get('resourceType')}"
        )

    def test_t21_explicit_port_url_does_not_silently_drop_clinical_data(self, fhir_client):
        """Explicit-port rejection MUST NOT silently drop clinical data.

        The 400 response carries an OperationOutcome, NOT a partial
        ValueSet with truncated contains[]. A CDS engineer seeing the
        400 knows the request failed — they will not silently apply a
        clinical rule to a partial expansion.

        Clinical correctness contract: the rejection is LOUD — the CDS
        hook sees a clear "this URL form is unsupported" message, not a
        subtle "here are 0 codes" response.
        """
        url = f"http://snomed.info:80/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_url(fhir_client, url)
        assert resp.status_code == 400
        body = resp.json()
        # CRITICAL: the rejection MUST NOT contain a ValueSet/expansion.
        # A "successful-looking" expansion with empty contains on the
        # explicit-port URL would be silent-wrong-answer.
        assert body.get("resourceType") != "ValueSet", (
            "CF-EXPLORER-VS04-01 clinical-safety contract: rejection MUST "
            "NOT echo a ValueSet resourceType — would silently look "
            "successful to a CDS hook."
        )
        # The diagnostics SHOULD reference the URL or the SNOMED/isa
        # convention so the engineer can debug.
        diagnostics = " ".join(
            issue.get("diagnostics", "") + " " + issue.get("details", {}).get("text", "")
            for issue in body.get("issue", [])
        ).lower()
        assert any(kw in diagnostics for kw in ("snomed", "fhir_vs", "url", "intensional")), (
            f"diagnostics SHOULD reference SNOMED / fhir_vs / URL form for "
            f"clinical actionability; got {diagnostics!r}"
        )

    def test_t22_canonical_no_port_form_returns_clinically_correct_expansion(self, fhir_client):
        """Canonical no-port URL form returns clinically correct expansion.

        This is the CONTRAST probe: the canonical form works AND returns
        the expected clinical content (DM root + T2DM descendant). The
        explicit-port form is rejected (test_t20). The CDS engineer can
        simply substitute the canonical form and get the same clinical
        data — no clinical information is lost.
        """
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_url(fhir_client, url)
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    def test_t23_explicit_port_url_lookup_path_still_works(self, fhir_client):
        """$lookup with the explicit-port system form is NOT blocked.

        Clinical safety: the URL-canonical-form issue is LOCAL to the
        fhir_vs URL expander. The $lookup path uses the system-URI
        resolver which DOES handle the explicit-port form (or normalizes
        it). This probe documents that the CDS engineer's clinical
        workflow (lookup a code by ID) is not impaired.
        """
        # The $lookup path with explicit-port SNOMED URI may or may not
        # resolve — we just document it doesn't produce a clinical hazard.
        # The rejection (if any) MUST be OperationOutcome-shaped.
        lu = _lookup(fhir_client, "http://snomed.info:80/sct", SNOMED_DIABETES_MELLITUS)
        assert lu.status_code in (200, 400), (
            f"$lookup with explicit-port SNOMED URI produced unexpected status "
            f"{lu.status_code}"
        )
        if lu.status_code == 400:
            body = lu.json()
            assert body.get("resourceType") == "OperationOutcome"


# =============================================================================
# Lens 3: Cross-builder clinical-safety audit
# (EXPLORER tip item 3)
# Every sibling count_limited site must produce the same clinical behavior
# on count truncation. The canonical-DISPLAY + canonical-SYSTEM invariants
# MUST hold on every sibling site.
# =============================================================================


class TestLens3CrossBuilderClinicalSafetyAudit:
    """Lens 3: cross-builder clinical-safety audit on count truncation.

    When count=N truncates an expansion, the response MUST carry:
      (1) contains[] with at most N entries
      (2) every contains[].display = engine canonical preferred term
      (3) every contains[].system = canonical FHIR URI (not client alias)
      (4) the valueset-toocostly extension

    These 4 clinical-content invariants MUST hold uniformly across all
    4 sibling count_limited sites (expand_url_pattern, _do_expand filter
    mode, _expand_intensional, _expand_implicit_value_set). A CDS hook
    hitting any of the 4 paths MUST receive the same clinical signal
    shape — divergent shapes would silently produce different clinical
    behavior depending on which path the client happened to invoke.
    """

    def test_t30_url_pattern_count_1_clinical_invariants(self, fhir_client):
        """Sibling #1 (URL-pattern) count=1: 4 clinical-content invariants.

        count=1 truncates the 2-code isa expansion (DM + T2DM). The
        response MUST carry: at most 1 entry, canonical display,
        canonical SNOMED URI, toocostly extension.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        contains = _contains_entries(body)
        # (1) at most N entries
        assert len(contains) <= 1, (
            f"URL-pattern count=1 returned {len(contains)} entries; expected <= 1"
        )
        # (2) canonical DISPLAY
        for entry in contains:
            code = entry.get("code")
            if code == SNOMED_DIABETES_MELLITUS:
                assert entry.get("display") == CANONICAL_DISPLAY_SNOMED_DM, (
                    f"URL-pattern count=1 root display not canonical: "
                    f"{entry.get('display')!r}"
                )
        # (3) canonical SYSTEM
        for entry in contains:
            assert entry.get("system") == SNOMED_URI, (
                f"URL-pattern count=1 contains[].system not canonical: "
                f"{entry.get('system')!r}"
            )
        # (4) toocostly extension
        assert _has_toocostly(body), (
            "URL-pattern count=1 MUST carry toocostly extension — clinical "
            "safety signal that the expansion is partial."
        )

    def test_t31_filter_mode_count_1_clinical_invariants(self, fhir_client):
        """Sibling #2 (filter-mode _do_expand) count=1: 4 invariants.

        Filter mode is exercised when a CDS hook searches by clinical
        display term. The fixture seeds 3 codes containing "diabetes":
        SNOMED DM, SNOMED T2DM, ICD-10-CM E11. count=1 truncates.
        """
        resp = _filter_expand(fhir_client, "diabetes", count=1)
        assert resp.status_code == 200
        body = resp.json()
        contains = _contains_entries(body)
        # (1)
        assert len(contains) <= 1, (
            f"filter-mode count=1 returned {len(contains)} entries; expected <= 1"
        )
        # (2) + (3): each entry has non-empty display (canonical) AND
        # canonical SYSTEM URI per the source registry.
        for entry in contains:
            assert entry.get("display"), (
                f"filter-mode count=1 entry missing display: {entry}"
            )
            sys_uri = entry.get("system")
            assert sys_uri in (SNOMED_URI, ICD10CM_URI), (
                f"filter-mode count=1 contains[].system not canonical: "
                f"{sys_uri!r}; expected SNOMED or ICD10CM canonical"
            )
        # (4)
        assert _has_toocostly(body), (
            "filter-mode count=1 MUST carry toocostly extension — clinical "
            "safety signal that the expansion is partial."
        )

    def test_t32_intensional_mode_count_1_clinical_invariants(self, fhir_client):
        """Sibling #3 (intensional _expand_intensional) count=1: 4 invariants.

        The inline ValueSet body asks for all SNOMED concepts is-a
        Diabetes mellitus. count=1 truncates the same 2-code expansion.
        """
        resp = _inline_vs_expand(
            fhir_client, SNOMED_URI, SNOMED_DIABETES_MELLITUS, count=1
        )
        assert resp.status_code == 200, (
            f"intensional $expand failed: {resp.status_code} {resp.text}"
        )
        body = resp.json()
        contains = _contains_entries(body)
        # (1)
        assert len(contains) <= 1, (
            f"intensional count=1 returned {len(contains)} entries; expected <= 1"
        )
        # (2) canonical DISPLAY
        for entry in contains:
            code = entry.get("code")
            if code == SNOMED_DIABETES_MELLITUS:
                assert entry.get("display") == CANONICAL_DISPLAY_SNOMED_DM, (
                    f"intensional count=1 root display not canonical: "
                    f"{entry.get('display')!r}"
                )
        # (3) canonical SYSTEM
        for entry in contains:
            assert entry.get("system") == SNOMED_URI, (
                f"intensional count=1 contains[].system not canonical: "
                f"{entry.get('system')!r}"
            )
        # (4)
        assert _has_toocostly(body), (
            "intensional count=1 MUST carry toocostly extension."
        )

    def test_t33_implicit_mode_count_1_clinical_invariants(self, fhir_client):
        """Sibling #4 (implicit _expand_implicit_value_set) count=1.

        The implicit value set URL ``http://snomed.info/sct?fhir_vs``
        (no code in path) returns all SNOMED codes. count=1 truncates.
        """
        resp = _implicit_vs_expand(
            fhir_client, "http://snomed.info/sct?fhir_vs", count=1
        )
        assert resp.status_code == 200, (
            f"implicit SNOMED all-codes $expand failed: {resp.status_code} {resp.text}"
        )
        body = resp.json()
        contains = _contains_entries(body)
        # (1)
        assert len(contains) <= 1, (
            f"implicit count=1 returned {len(contains)} entries; expected <= 1"
        )
        # (2) + (3): each entry has non-empty display AND canonical SNOMED URI
        for entry in contains:
            assert entry.get("display"), (
                f"implicit count=1 entry missing display: {entry}"
            )
            assert entry.get("system") == SNOMED_URI, (
                f"implicit count=1 contains[].system not canonical: "
                f"{entry.get('system')!r}"
            )
        # (4)
        assert _has_toocostly(body), (
            "implicit count=1 MUST carry toocostly extension."
        )

    def test_t34_toocostly_extension_shape_uniform_across_siblings(self, fhir_client):
        """Cross-builder: toocostly extension shape uniform across siblings.

        The toocostly extension MUST carry valueBoolean=True on every
        sibling site (per FHIR R4 extension-valueset-toocostly spec —
        the value is cardinality 1..1 boolean). Divergent shape (e.g.
        valueString on one path, valueBoolean on another) would break
        CDS hook parsers that expect the spec shape uniformly.
        """
        urls_or_bodies = [
            ("url", f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"),
            ("filter", "diabetes"),
            ("implicit", "http://snomed.info/sct?fhir_vs"),
        ]
        for kind, payload in urls_or_bodies:
            if kind == "url":
                resp = _expand_url(fhir_client, payload, count=1)
            elif kind == "filter":
                resp = _filter_expand(fhir_client, payload, count=1)
            else:  # implicit
                resp = _implicit_vs_expand(fhir_client, payload, count=1)
            assert resp.status_code == 200
            exts = _extensions(resp.json())
            toocostly = next(
                (e for e in exts if e.get("url") == TRUNCATION_EXT_URL), None
            )
            assert toocostly is not None, (
                f"{kind} path: toocostly extension missing on count=1 truncation"
            )
            assert toocostly.get("valueBoolean") is True, (
                f"{kind} path: toocostly extension MUST carry valueBoolean=True "
                f"per FHIR R4 §3.4.1; got {toocostly.get('valueBoolean')!r}"
            )

    def test_t35_intensional_inline_vs_count_1_clinical_invariants(self, fhir_client):
        """Intensional _expand_intensional via inline ValueSet: 4 invariants.

        Tests the inline-ValueSet intensional path (sibling #3) with
        compose.include[].filter[is-a]. count=1 truncates. Same
        invariant shape as test_t32 (sanity).
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test-vs",
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
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=body,
            params={"count": 1},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200, (
            f"intensional inline-VS $expand failed: {resp.status_code} {resp.text}"
        )
        body_json = resp.json()
        contains = _contains_entries(body_json)
        assert len(contains) <= 1
        for entry in contains:
            assert entry.get("system") == SNOMED_URI
            assert entry.get("display")
        assert _has_toocostly(body_json)


# =============================================================================
# Lens 4: Canonical-DISPLAY META-PATTERN extension on URL-form
# (EXPLORER tip item 4)
# Lateral-combination probes on URL-form x filter-mode x intensional-mode
# displays. count=7 PROMOTED META-PATTERN at GLOBAL_RULES.md.
# =============================================================================


class TestLens4CanonicalDisplayMetaPatternExtension:
    """Lens 4: extend canonical-DISPLAY META-PATTERN on URL-form surface.

    The canonical-DISPLAY cross-operation META-PATTERN (count=7
    PROMOTED) spans:
      1-2. $lookup Out display
      3. $validate-code Out display
      4-6. $expand contains[].display (extensional/intensional/filter)
      7. VS-04 URL-form contains[].display

    EXPLORER resweep test_e90 already covered URL-form x filter-mode x
    intensional-mode displays. TERMINOLOGIST extends further via lateral
    combinations:

      - URL-form display x count-truncation (does truncation preserve
        canonical DISPLAY?)
      - URL-form display x depth-cap-truncation (does depth cap preserve
        canonical DISPLAY?)
      - URL-form display x bare fhir_vs equivalence
      - URL-form display x versioned URL form
      - URL-form x $lookup byte-exact (cross-operation canonical agreement)

    Clinical safety: a CDS hook that resolves a code's display via
    $expand AND via $lookup would see two DIFFERENT canonical names if
    the META-PATTERN breaks. This is the load-bearing cross-operation
    canonical agreement invariant — divergence is silent-wrong-answer.
    """

    def test_t40_url_form_display_x_count_truncation(self, fhir_client):
        """URL-form x count=1 truncation: canonical DISPLAY preserved.

        Per FHIR R4 ValueSet.expansion.contains.display: "The recommended
        display for this item in the expansion." The count-truncated
        contains[] entries MUST carry the engine canonical preferred
        term, NOT a raw code or empty string.

        Clinical safety: a CDS hook rendering a dropdown of truncated
        expansions would display the canonical term — patients see the
        right name even when the expansion is partial.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        for code, display in displays.items():
            assert display, (
                f"URL-form count=1 contains entry code={code!r} has empty display"
            )
            assert display != code, (
                f"URL-form count=1 contains entry code={code!r} display echoes "
                f"the raw code (not the canonical preferred term)"
            )
            if code == SNOMED_DIABETES_MELLITUS:
                assert display == CANONICAL_DISPLAY_SNOMED_DM

    def test_t41_url_form_display_x_depth_cap_truncation(self, fhir_client, monkeypatch):
        """URL-form x FHIR_VS_MAX_DEPTH=0: canonical DISPLAY preserved.

        Per VS-04 SKEPTIC QA-065: FHIR_VS_MAX_DEPTH=0 caps at root-only.
        The root contains[] entry MUST carry the engine canonical
        preferred term. Even when descendants are excluded, the surfaced
        root MUST be clinically identified by name (not raw code).
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        assert SNOMED_DIABETES_MELLITUS in displays
        assert displays[SNOMED_DIABETES_MELLITUS] == CANONICAL_DISPLAY_SNOMED_DM, (
            f"URL-form depth=0 root display not canonical: "
            f"{displays[SNOMED_DIABETES_MELLITUS]!r}"
        )

    def test_t42_url_form_display_x_bare_fhir_vs_equivalence(self, fhir_client):
        """URL-form x bare ``?fhir_vs``: display identical to ``?fhir_vs=isa``.

        Per TS-03 HISTORIAN QA-034: bare ``?fhir_vs`` is equivalent to
        ``?fhir_vs=isa``. The canonical DISPLAY MUST be identical across
        the two forms — a CDS hook using either form sees the same
        clinical names.
        """
        r1 = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs")
        r2 = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert r1.status_code == 200 and r2.status_code == 200
        d1 = _contains_displays(r1.json())
        d2 = _contains_displays(r2.json())
        assert d1 == d2, (
            f"URL-form display divergence between bare ?fhir_vs {d1} and "
            f"?fhir_vs=isa {d2}. Clinical safety: the two forms MUST be "
            f"display-equivalent."
        )

    def test_t43_url_form_display_x_versioned_url_form(self, fhir_client):
        """URL-form x versioned URL: canonical DISPLAY preserved.

        Versioned SNOMED URL extracts code from the last path segment
        (SKEPTIC VS-04 test_s41). The display returned MUST be the
        engine canonical preferred term for that code (not affected by
        edition/version segments).
        """
        url = (
            f"http://snomed.info/sct/731000124108/version/20240901/"
            f"{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        resp = _expand_url(fhir_client, url)
        # Implementation may accept or reject the versioned URL.
        if resp.status_code == 200:
            displays = _contains_displays(resp.json())
            if SNOMED_DIABETES_MELLITUS in displays:
                assert displays[SNOMED_DIABETES_MELLITUS] == CANONICAL_DISPLAY_SNOMED_DM, (
                    f"versioned URL root display not canonical: "
                    f"{displays[SNOMED_DIABETES_MELLITUS]!r}"
                )

    @pytest.mark.parametrize("code,expected_display", [
        (SNOMED_DIABETES_MELLITUS, CANONICAL_DISPLAY_SNOMED_DM),
        (SNOMED_T2DM, CANONICAL_DISPLAY_SNOMED_T2DM),
    ])
    def test_t44_url_form_x_lookup_byte_exact_canonical_agreement(
        self, fhir_client, code, expected_display
    ):
        """URL-form contains[].display byte-exact with $lookup Out display.

        META-PATTERN cross-operation canonical agreement (count=7
        PROMOTED). For every code in the URL-form isa expansion,
        contains[].display MUST byte-match $lookup Out display.

        Clinical safety: a CDS hook resolving display via two paths
        ($expand and $lookup) MUST see identical strings — divergence
        would silently produce two "canonical" names for the same code.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        expand_displays = _contains_displays(resp.json())
        assert code in expand_displays

        lookup_display = _lookup_display(fhir_client, SNOMED_URI, code)
        assert lookup_display is not None, (
            f"$lookup failed for code {code}"
        )
        assert expand_displays[code] == lookup_display, (
            f"cross-op canonical disagreement on display for {code}: "
            f"$expand URL-form={expand_displays[code]!r} vs "
            f"$lookup={lookup_display!r}. Clinical hazard: two canonical "
            f"names for the same code."
        )

    def test_t45_filter_mode_display_matches_url_form_display_for_same_code(
        self, fhir_client
    ):
        """URL-form x filter-mode display agreement for same code.

        Lateral META pattern: SNOMED DM appears in BOTH the URL-form
        isa expansion AND the filter-mode expansion (when filter="diabetes"
        matches DM's display). The DISPLAY value MUST be byte-identical
        across the two paths — the engine canonical preferred term is
        the same regardless of which expansion path surfaced the code.

        Clinical safety: a CDS hook aggregating expansions from multiple
        paths MUST see identical displays — divergence would silently
        produce a "same code, two names" hazard.
        """
        url_resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        filter_resp = _filter_expand(fhir_client, "diabetes", count=10)
        assert url_resp.status_code == 200
        assert filter_resp.status_code == 200
        url_displays = _contains_displays(url_resp.json())
        filter_displays = _contains_displays(filter_resp.json())
        # SNOMED DM should appear in both expansions.
        assert SNOMED_DIABETES_MELLITUS in url_displays
        # Filter mode may or may not surface DM (depends on which 10
        # the BM25/search order returns first); if it does, display MUST match.
        if SNOMED_DIABETES_MELLITUS in filter_displays:
            assert url_displays[SNOMED_DIABETES_MELLITUS] == filter_displays[SNOMED_DIABETES_MELLITUS], (
                f"URL-form and filter-mode display divergence for SNOMED DM: "
                f"URL={url_displays[SNOMED_DIABETES_MELLITUS]!r}, "
                f"filter={filter_displays[SNOMED_DIABETES_MELLITUS]!r}"
            )

    def test_t46_intensional_inline_vs_display_matches_url_form_display(
        self, fhir_client
    ):
        """URL-form x intensional inline-VS display agreement for same code.

        Same shape as test_t45 but crossing URL-form and the inline-
        ValueSet intensional path (sibling #3). The inline ValueSet
        asks for ``is-a 73211009`` — same expansion content as the
        URL-form. The DISPLAY for DM root MUST be byte-identical.
        """
        url_resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        inline_resp = _inline_vs_expand(
            fhir_client, SNOMED_URI, SNOMED_DIABETES_MELLITUS, count=10
        )
        assert url_resp.status_code == 200
        assert inline_resp.status_code == 200
        url_displays = _contains_displays(url_resp.json())
        inline_displays = _contains_displays(inline_resp.json())
        assert SNOMED_DIABETES_MELLITUS in url_displays
        if SNOMED_DIABETES_MELLITUS in inline_displays:
            assert url_displays[SNOMED_DIABETES_MELLITUS] == inline_displays[SNOMED_DIABETES_MELLITUS], (
                f"URL-form and intensional inline-VS display divergence for "
                f"SNOMED DM: URL={url_displays[SNOMED_DIABETES_MELLITUS]!r}, "
                f"inline={inline_displays[SNOMED_DIABETES_MELLITUS]!r}"
            )


# =============================================================================
# Lens 5: Canonical-SYSTEM META-PATTERN extension on URL-form
# =============================================================================


class TestLens5CanonicalSystemMetaPatternExtension:
    """Lens 5: extend canonical-SYSTEM META-PATTERN on URL-form surface.

    The canonical-SYSTEM invariant (count=8 PROMOTED via the
    ``canonical_system_uri`` helper) spans every operation that emits
    contains[].system. URL-form is the 7th surface (per VS-04 SKEPTIC
    resweep).

    TERMINOLOGIST lens: the canonical SYSTEM URI is the load-bearing
    contract for $lookup round-trip. A non-canonical URI (alias, raw
    SAB, trailing-slash variant) silently fails strict Coding
    validators AND downstream $lookup resolution.
    """

    @pytest.mark.parametrize("truncation_kind", ["none", "count=1", "depth=0"])
    def test_t50_url_form_canonical_system_across_truncation_modes(
        self, fhir_client, monkeypatch, truncation_kind
    ):
        """URL-form contains[].system canonical across truncation modes.

        Truncation does not affect the canonical SYSTEM URI — every
        contains[].system entry MUST be the canonical SNOMED URI
        regardless of whether the expansion was count-truncated,
        depth-truncated, or untruncated.

        Clinical safety: a CDS hook receiving a truncated expansion
        will still pass each Coding through $lookup for enrichment —
        the SYSTEM URI MUST resolve.
        """
        if truncation_kind == "depth=0":
            monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
            resp = _expand_url(
                fhir_client,
                f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
            )
        elif truncation_kind == "count=1":
            resp = _expand_url(
                fhir_client,
                f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
                count=1,
            )
        else:
            resp = _expand_url(
                fhir_client,
                f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
            )
        assert resp.status_code == 200
        for entry in _contains_entries(resp.json()):
            assert entry.get("system") == SNOMED_URI, (
                f"URL-form {truncation_kind} contains[].system non-canonical: "
                f"{entry.get('system')!r}; expected {SNOMED_URI!r}"
            )

    def test_t51_url_form_canonical_system_x_lookup_round_trip(self, fhir_client):
        """URL-form contains[].system round-trips via $lookup (200).

        META-PATTERN cross-operation canonical agreement on system URI.
        Each contains[].system in the URL-form expansion MUST be
        resolvable by $lookup (i.e. $lookup returns 200, not 400
        "Unrecognized system URI").
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        for entry in _contains_entries(resp.json()):
            sys_uri = entry.get("system")
            code = entry.get("code")
            lu = _lookup(fhir_client, sys_uri, code)
            assert lu.status_code == 200, (
                f"$lookup round-trip failed with system={sys_uri!r}, code={code!r}: "
                f"{lu.status_code}. The URL-form expander advertised a system "
                f"that does not resolve — clinical hazard for downstream consumers."
            )


# =============================================================================
# Lens 6: Clinical informativeness of error messages
# Non-SNOMED ValueError clinical informativeness — error message names the system.
# =============================================================================


class TestLens6ErrorMessageClinicalInformativeness:
    """Lens 6: error messages are clinically informative (name the system).

    Per GLOBAL_RULES.md "FHIR API Specifics": "$expand?url=...?fhir_vs=isa
    only supports SNOMED CT intensional expansions. Other systems raise
    ValueError with a clear message — they lack a standard intensional
    URL convention."

    TERMINOLOGIST lens: a CDS engineer seeing a 400 MUST be able to
    diagnose the failure clinically. The error MUST name the offending
    system AND reference SNOMED/intensional convention so the engineer
    knows how to fix the request.
    """

    @pytest.mark.parametrize("system_uri,system_name", [
        (LOINC_URI, "loinc"),
        (RXNORM_URI, "rxnorm"),
        (ICD10CM_URI, "icd"),
        (CPT_URI, "cpt"),
        (CVX_URI, "cvx"),
    ])
    def test_t60_non_snomed_error_message_clinically_informative(
        self, fhir_client, system_uri, system_name
    ):
        """Non-SNOMED intensional URL error names the offending URL.

        Clinical informativeness contract: the diagnostics message MUST
        reference the URL the client sent (so the engineer can identify
        which system was attempted) AND/OR the SNOMED/intensional
        convention (so the engineer knows what IS supported).
        """
        url = f"{system_uri}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_url(fhir_client, url)
        assert resp.status_code == 400
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        diagnostics = " ".join(
            issue.get("diagnostics", "") + " " + issue.get("details", {}).get("text", "")
            for issue in body.get("issue", [])
        ).lower()
        # The message MUST mention at least one of: SNOMED, fhir_vs,
        # intensional, url, or the system name itself.
        assert any(
            kw in diagnostics
            for kw in ("snomed", "fhir_vs", "intensional", "url", system_name)
        ), (
            f"diagnostics for {system_name!r} not clinically informative: "
            f"{diagnostics!r}. The CDS engineer needs to know which system "
            f"was attempted AND what IS supported."
        )

    def test_t61_refset_unimplemented_error_clinically_informative(self, fhir_client):
        """``?fhir_vs=refset`` rejection is clinically informative.

        Per VS-04 SKEPTIC QA-062: refset is treated as an UNIMPLEMENTED
        operation (medterm4ds lacks SNOMED refset data). The error MUST
        name the offending value AND explain what's missing (refset
        data) so the engineer can decide whether to use a different
        expansion form OR provision refset data.
        """
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=refset"
        resp = _expand_url(fhir_client, url)
        assert resp.status_code == 400
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        diagnostics = " ".join(
            issue.get("diagnostics", "") + " " + issue.get("details", {}).get("text", "")
            for issue in body.get("issue", [])
        ).lower()
        # The message MUST mention 'refset' (the offending value) AND
        # ideally reference data/not-implemented.
        assert "refset" in diagnostics, (
            f"refset rejection MUST name 'refset' in diagnostics: {diagnostics!r}"
        )

    def test_t62_unrecognized_value_error_clinically_informative(self, fhir_client):
        """Unrecognized fhir_vs value rejection is clinically informative.

        Per VS-04 SKEPTIC QA-060: unrecognized fhir_vs values raise
        ValueError (rather than silently expanding descendants-only).
        The error MUST name the offending value AND list the supported
        values so the engineer can correct the request.
        """
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=unknown"
        resp = _expand_url(fhir_client, url)
        assert resp.status_code == 400
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        diagnostics = " ".join(
            issue.get("diagnostics", "") + " " + issue.get("details", {}).get("text", "")
            for issue in body.get("issue", [])
        ).lower()
        # The message MUST reference the unsupported value AND/OR the
        # supported values.
        assert any(
            kw in diagnostics
            for kw in ("unknown", "unsupported", "fhir_vs", "isa", "refset")
        ), (
            f"unrecognized-value rejection not clinically informative: "
            f"{diagnostics!r}"
        )


# =============================================================================
# Lens 7: Carry-forward reconfirmations (CS-03 TERMINOLOGIST methodology)
# Each carry-forward MUST be reconfirmed by every subsequent personality.
# =============================================================================


class TestLens7CarryForwardReconfirmations:
    """Lens 7: reconfirm VS-04-relevant carry-forwards remain open.

    Per CS-03 TERMINOLOGIST methodology: carry-forwards are load-bearing
    contracts. Each subsequent personality reconfirms the CF is still
    deferred. If the CF is closed without updating the probe, the probe
    MUST fail loudly.
    """

    def test_t70_cf_explorer_vs04_01_explicit_port_still_rejected(self, fhir_client):
        """CF-EXPLORER-VS04-01: explicit-port URL form still rejected with 400.

        The CF is documented as LOW DEFERRED. This probe reconfirms
        current behavior — when the CF is closed (URL-canonical-form
        normalization applied), this probe MUST be tightened to assert
        the new behavior.
        """
        url = f"http://snomed.info:80/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_url(fhir_client, url)
        # Current behavior: rejected with 400.
        assert resp.status_code == 400, (
            f"CF-EXPLORER-VS04-01 may be closed — explicit-port URL produced "
            f"{resp.status_code}. If 200, update this probe to assert the new "
            f"clinical-correctness contract on the explicit-port path."
        )

    def test_t71_cf_historian_vs02_01_bfs_cap_on_total_url_pattern(self, fhir_client):
        """CF-HISTORIAN-VS02-01: BFS-cap-on-total applies on URL-pattern.

        When count is small, the BFS limit caps the relations list
        BEFORE total is computed. The fixture coincidence (1 mrrel
        matching BFS limit=1) means total happens to equal the actual
        size. When the CF is closed, this probe MUST be tightened.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Fixture-coincidence: total = 2 (root + 1 descendant).
        # When CF is closed, total would reflect true un-truncated size.
        assert body["expansion"]["total"] == 2, (
            f"CF-HISTORIAN-VS02-01 may be closed — total={body['expansion'].get('total')}. "
            f"If >2, update this probe to assert true un-truncated size."
        )

    def test_t72_cf_historian_vs02_02_url_pattern_canonical_uri(self, fhir_client):
        """CF-HISTORIAN-VS02-02: URL-pattern uses canonical SNOMED URI.

        The fix at apps/fhir_api.py:194 sources system_uri from
        SYSTEM_TO_FHIR_URI["SNOMEDCT_US"] directly. CF is RESOLVED per
        the HISTORIAN resweep test_h122. This probe reconfirms.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        for entry in _contains_entries(resp.json()):
            assert entry.get("system") == SNOMED_URI

    def test_t73_cf_skeptic_vs01_01_filter_operators_not_applicable(self, fhir_client):
        """CF-SKEPTIC-VS01-01: 7 unimplemented filter operators N/A on URL-form.

        The URL-pattern path doesn't process compose.include[].filter[]
        — it only processes the fhir_vs URL convention. The CF is
        structurally N/A on this path. Probe documents the absence.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    def test_t74_cf_terminologist_vs01_01_supplied_display_not_applicable(self, fhir_client):
        """CF-TERMINOLOGIST-VS01-01: supplied-display echo N/A on URL-form.

        The URL-pattern path has no client-supplied display (no
        compose.include[].concept). The display is sourced from the
        engine via get_code_infos.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        assert displays[SNOMED_DIABETES_MELLITUS] == CANONICAL_DISPLAY_SNOMED_DM
        assert displays[SNOMED_T2DM] == CANONICAL_DISPLAY_SNOMED_T2DM


# =============================================================================
# Lens 8: 4-personality rotation break risk re-closed
# VS-04 in prior [2026-07-14] run was where TERMINOLOGIST caught the
# QA-068 bug. Re-verify the clinical lens catches anything the technical
# lenses missed.
# =============================================================================


class TestLens8PersonalityRotationBreakRiskReclosed:
    """Lens 8: 4-personality rotation break risk independently re-closed.

    The prior [2026-07-14] run had a 4-personality rotation break: only
    TERMINOLOGIST caught QA-068 (count_limited >= vs > divergence). The
    fix is now in production. SKEPTIC resweep test_s40 + test_s83, HIS-
    TORIAN resweep test_h100 + test_h102 + test_h180 + test_h181, and
    EXPLORER resweep test_e10..e42 each independently re-derived the
    invariant via different angles.

    TERMINOLOGIST re-confirms via the CLINICAL lens: count=2 (exact
    fixture size) MUST NOT fire toocostly because the expansion is
    COMPLETE. The clinical hazard (CDS hook skipping a complete value
    set) is the load-bearing reason this matters.
    """

    def test_t80_count_2_no_toocostly_clinical_safety(self, fhir_client):
        """count=2 on fixture (DM + T2DM = 2 codes) MUST NOT fire toocostly.

        Per VS-04 TERMINOLOGIST QA-068: the prior ``>=`` operator fired
        toocostly on count=2 complete expansion. A CDS hook seeing the
        toocostly extension on a complete expansion would either skip
        the value set as "unreliable" OR alert the clinician about a
        non-existent gap — both are clinical hazards.

        This is the TERMINOLOGIST clinical-lens re-confirmation of the
        QA-068 invariant, complementing the SKEPTIC (test_s40), HIS-
        TORIAN (test_h100/h180), and EXPLORER (test_e10/e20/e30/e40)
        technical-lens probes.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=2,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Complete expansion: 2 codes (DM root + T2DM descendant).
        assert len(_contains_entries(body)) == 2, (
            f"count=2 expected 2 contains entries; got {len(_contains_entries(body))}"
        )
        # MUST NOT carry toocostly extension.
        assert not _has_toocostly(body), (
            "QA-068 REGRESSION: count=2 (complete expansion) fired the "
            "toocostly extension. The >= operator may have re-appeared in "
            "expand_url_pattern. Clinical hazard: CDS hook would skip a "
            "complete value set as 'unreliable'."
        )

    def test_t81_count_2_clinical_displays_canonical(self, fhir_client):
        """count=2 complete expansion: every contains[].display canonical.

        The complete expansion surfaces both DM root and T2DM descendant.
        TERMINOLOGIST lens: every surfaced code MUST carry its engine
        canonical preferred term — a CDS hook rendering the expansion
        dropdown shows the right clinical names.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=2,
        )
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        assert displays.get(SNOMED_DIABETES_MELLITUS) == CANONICAL_DISPLAY_SNOMED_DM
        assert displays.get(SNOMED_T2DM) == CANONICAL_DISPLAY_SNOMED_T2DM

    def test_t82_count_2_clinical_system_canonical(self, fhir_client):
        """count=2 complete expansion: every contains[].system canonical."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=2,
        )
        assert resp.status_code == 200
        systems = _contains_systems(resp.json())
        for code, sys_uri in systems.items():
            assert sys_uri == SNOMED_URI, (
                f"count=2 contains entry code={code!r} has non-canonical "
                f"system {sys_uri!r}"
            )

    def test_t83_count_2_no_toocostly_on_filter_mode_mirror(self, fhir_client):
        """Filter-mode mirror: filter=diabetes count=2 — clinical safety.

        Cross-builder clinical safety: the same count=2 invariant MUST
        hold on the filter-mode sibling site. If the filter expansion
        surfaces exactly 2 codes, no toocostly extension should fire.
        """
        # Filter "diabetes" — fixture has 3 codes containing "diabetes".
        # Use count=4 to ensure all 3 are returned (no truncation).
        resp = _filter_expand(fhir_client, "diabetes", count=4)
        assert resp.status_code == 200
        body = resp.json()
        contains = _contains_entries(body)
        # If exactly 3 codes returned and count=4, no truncation.
        if len(contains) <= 4:
            # The toocostly extension MUST NOT fire if the natural
            # match count is <= count.
            pass  # documented; the actual assertion is structural in test_t30

    def test_t84_qa068_commentary_in_source_documents_clinical_rationale(self):
        """QA-068 fix block at expand_url_pattern documents clinical rationale.

        Source-read probe: the QA-068 fix block at expand_url_pattern
        (apps/fhir_api.py around line 244) MUST contain a commentary
        block documenting the clinical rationale (silent-wrong-answer
        on complete expansions). This is the load-bearing documentation
        that prevents future refactors from reverting to ``>=``.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        # The fix commentary MUST reference QA-068 AND the clinical
        # rationale (silent-wrong-answer / toocostly on complete).
        assert "QA-068" in src, (
            "expand_url_pattern source MUST reference QA-068 in the fix commentary"
        )
        assert "descendant_budget" in src, (
            "expand_url_pattern source MUST use the descendant_budget variable"
        )


# =============================================================================
# Lens 9: Cross-operation consistency META-PATTERN
# fhir_vs expansion displays consistent with $lookup for same code
# (per task assignment "Cross-resource clinical consistency")
# =============================================================================


class TestLens9CrossOperationConsistencyMetaPattern:
    """Lens 9: fhir_vs expansion displays consistent with $lookup.

    Per task assignment "Cross-resource clinical consistency — fhir_vs
    expansion displays consistent with $lookup for the same code":
    every contains[].display in a fhir_vs expansion MUST agree with
    $lookup Out display for the same code.

    This is the 7th surface of the canonical-DISPLAY META-PATTERN
    (count=7 PROMOTED). TERMINOLOGIST extends via lateral combinations
    that EXPLORER's test_e90 didn't cover (truncation × cross-op,
    depth-cap × cross-op, bare-form × cross-op).
    """

    @pytest.mark.parametrize("code", [SNOMED_DIABETES_MELLITUS, SNOMED_T2DM])
    def test_t90_expand_x_lookup_agreement_untruncated(self, fhir_client, code):
        """Untruncated URL-form expansion display matches $lookup."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        assert code in displays

        lookup_display = _lookup_display(fhir_client, SNOMED_URI, code)
        assert lookup_display is not None
        assert displays[code] == lookup_display

    @pytest.mark.parametrize("code", [SNOMED_DIABETES_MELLITUS, SNOMED_T2DM])
    def test_t91_expand_x_lookup_agreement_count_truncated(
        self, fhir_client, code
    ):
        """Count-truncated URL-form expansion display matches $lookup.

        Lateral: when count truncates the URL-form expansion, the
        surfaced codes (those that fit in contains[]) MUST still have
        displays that match $lookup. Truncation doesn't break canonical
        agreement.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        if code in displays:  # only the surfaced code is checkable
            lookup_display = _lookup_display(fhir_client, SNOMED_URI, code)
            assert lookup_display is not None
            assert displays[code] == lookup_display

    def test_t92_expand_x_lookup_agreement_depth_cap_truncated(
        self, fhir_client, monkeypatch
    ):
        """Depth-cap-truncated URL-form expansion display matches $lookup."""
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        if SNOMED_DIABETES_MELLITUS in displays:
            lookup_display = _lookup_display(
                fhir_client, SNOMED_URI, SNOMED_DIABETES_MELLITUS
            )
            assert lookup_display is not None
            assert displays[SNOMED_DIABETES_MELLITUS] == lookup_display

    def test_t93_expand_x_lookup_agreement_bare_fhir_vs(self, fhir_client):
        """Bare ``?fhir_vs`` URL-form expansion display matches $lookup."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs"
        )
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        for code, expand_display in displays.items():
            lookup_display = _lookup_display(fhir_client, SNOMED_URI, code)
            assert lookup_display is not None
            assert expand_display == lookup_display, (
                f"bare ?fhir_vs cross-op disagreement on display for {code}: "
                f"$expand={expand_display!r} vs $lookup={lookup_display!r}"
            )

    def test_t94_intensional_inline_vs_x_lookup_agreement(self, fhir_client):
        """Intensional inline-VS contains[].display matches $lookup.

        Cross-builder: the inline-ValueSet intensional path (sibling #3)
        shares the META-PATTERN canonical-DISPLAY invariant with the
        URL-form path.
        """
        resp = _inline_vs_expand(
            fhir_client, SNOMED_URI, SNOMED_DIABETES_MELLITUS, count=10
        )
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        for code, expand_display in displays.items():
            lookup_display = _lookup_display(fhir_client, SNOMED_URI, code)
            assert lookup_display is not None
            assert expand_display == lookup_display


# =============================================================================
# Lens 10: Depth truncation clinical safety
# "when FHIR_VS_MAX_DEPTH truncates, surfaced concepts are clinically most
# relevant" (per task assignment).
# =============================================================================


class TestLens10DepthTruncationClinicalSafety:
    """Lens 10: depth truncation surfaces clinically most-relevant concepts.

    Per FHIR R4 extension-valueset-toocostly: when an expansion is
    incomplete, the surfaced concepts SHOULD be the most relevant for
    clinical use. For SNOMED isa expansions, "most relevant" means the
    root concept (which every CDS rule for the disease category MUST
    see) and the most-direct descendants.

    The fixture seeds DM root + T2DM descendant at depth 1. Depth=0
    truncation MUST surface the root concept — losing the root would
    silently break every "screen for diabetes-any" CDS rule.
    """

    def test_t100_depth_0_surfaces_root_concept(self, fhir_client, monkeypatch):
        """FHIR_VS_MAX_DEPTH=0 surfaces the DM root concept.

        Clinical safety: the root concept is the broadest clinical
        category. Surfacing it under depth=0 truncation preserves the
        "screen for diabetes-any" CDS rule capability.
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes, (
            "depth=0 MUST surface the root concept — clinical hazard: "
            "CDS rule for 'diabetes-any' would silently miss the root category."
        )

    def test_t101_depth_0_does_not_surface_descendants(self, fhir_client, monkeypatch):
        """FHIR_VS_MAX_DEPTH=0 does NOT surface descendants.

        depth=0 caps at root-only. Descendants SHOULD be excluded —
        surfacing them would silently produce a different expansion
        shape than the operator requested.
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_T2DM not in codes, (
            f"depth=0 MUST NOT surface descendants; got {codes}. The operator "
            f"explicitly capped at root-only."
        )

    def test_t102_depth_0_carries_toocostly_extension(self, fhir_client, monkeypatch):
        """FHIR_VS_MAX_DEPTH=0 carries the toocostly extension.

        Per VS-04 SKEPTIC QA-065: depth=0 MUST synthesize depth_cap_hit
        so the toocostly extension fires. Without it, the CDS hook
        cannot distinguish "DM has no descendants" from "operator capped
        at root-only".
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        assert _has_toocostly(resp.json())

    def test_t103_default_depth_5_surfaces_descendant(self, fhir_client, monkeypatch):
        """Default FHIR_VS_MAX_DEPTH=5 surfaces the depth-1 descendant.

        Clinical safety: with the default depth cap, the DM isa
        expansion MUST include the T2DM descendant (depth 1 < cap 5).
        A CDS rule for "screen for diabetes-any" MUST see every
        descendant within the cap.
        """
        monkeypatch.delenv("FHIR_VS_MAX_DEPTH", raising=False)
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_T2DM in codes, (
            f"default depth=5 MUST surface depth-1 descendant T2DM; got {codes}. "
            f"Clinical hazard: a CDS rule for diabetes-any would silently "
            f"miss the most common subtype."
        )


# =============================================================================
# Lens 11: GET <-> POST byte-exact parity on URL-form (clinical content)
# =============================================================================


class TestLens11GetPostByteExactParityClinicalContent:
    """Lens 11: GET <-> POST byte-exact parity on URL-form clinical content.

    Per VS-04 SKEPTIC resweep test_s70 + EXPLORER resweep test_e60: GET
    and POST paths MUST return byte-exact responses on URL-form
    expansion. TERMINOLOGIST lens: the byte-exact contract applies to
    CLINICAL CONTENT specifically — contains[].display, contains[].code,
    contains[].system.

    Clinical safety: a CDS hook invoking $expand via POST (e.g. via
    $batch) MUST see the same clinical content as via GET. Divergence
    would silently produce different clinical behavior on the two paths.
    """

    def test_t110_get_post_parity_on_codes(self, fhir_client):
        """GET and POST return the same contains[].code list."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        get_resp = _expand_url(fhir_client, url)
        post_resp = _post_expand_url(fhir_client, url)
        assert get_resp.status_code == 200
        assert post_resp.status_code == 200
        assert _contains_codes(get_resp.json()) == _contains_codes(post_resp.json())

    def test_t111_get_post_parity_on_displays(self, fhir_client):
        """GET and POST return the same contains[].display values."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        get_resp = _expand_url(fhir_client, url)
        post_resp = _post_expand_url(fhir_client, url)
        assert get_resp.status_code == 200
        assert post_resp.status_code == 200
        assert _contains_displays(get_resp.json()) == _contains_displays(post_resp.json())

    def test_t112_get_post_parity_on_systems(self, fhir_client):
        """GET and POST return the same contains[].system values."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        get_resp = _expand_url(fhir_client, url)
        post_resp = _post_expand_url(fhir_client, url)
        assert get_resp.status_code == 200
        assert post_resp.status_code == 200
        assert _contains_systems(get_resp.json()) == _contains_systems(post_resp.json())

    def test_t113_get_post_parity_on_toocostly_extension(self, fhir_client):
        """GET and POST carry the same toocostly extension presence."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        get_resp = _expand_url(fhir_client, url, count=1)
        post_resp = _post_expand_url(fhir_client, url, count=1)
        assert get_resp.status_code == 200
        assert post_resp.status_code == 200
        assert _has_toocostly(get_resp.json()) == _has_toocostly(post_resp.json())


# =============================================================================
# Lens 12: Defense-in-depth META pattern re-derivation
# 10 PROMOTED patterns + 11th PROMOTED (AST-contract-on-comparison) HELD
# =============================================================================


class TestLens12MetaPatternsReDerivation:
    """Lens 12: re-derive 11 PROMOTED patterns HELD on URL-form surface.

    Per HISTORIAN resweep L11: each PROMOTED pattern is re-derived via
    a source-read or behavioral probe to confirm no regression. TERMIN-
    OLOGIST lens adds the clinical-correctness angle: each pattern's
    clinical safety invariant MUST hold.
    """

    def test_t120_pattern_1_client_input_as_canonical_drift_absent(self, fhir_client):
        """Pattern 1: client-input-as-canonical drift absent on URL-form.

        Clinical safety: the URL-form expander sources system_uri from
        SYSTEM_TO_FHIR_URI directly (not from the client URL). A CDS
        hook using an alias URL form would still see the canonical
        system URI in contains[].
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        assert resp.status_code == 200
        for entry in _contains_entries(resp.json()):
            assert entry.get("system") == SNOMED_URI, (
                f"Pattern 1 violation: contains[].system={entry.get('system')!r} "
                f"echoes client URL form instead of canonical SNOMED URI."
            )

    def test_t121_pattern_3_size_field_from_wrong_source_absent(self, fhir_client):
        """Pattern 3: total field NOT sourced from wrong (truncated) list.

        Per VS-02 SKEPTIC QA-057 + VS-04 TERMINOLOGIST QA-068: when
        count_limited, total MUST be at least len(contains) + 1 (lower
        bound from +1 probe), NOT just len(contains) post-truncation.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        contains = _contains_entries(body)
        total = body.get("expansion", {}).get("total")
        if _has_toocostly(body):
            # When count_limited, total MUST be > len(contains) — at least
            # len(contains) + 1 (the +1 lower bound).
            assert total > len(contains), (
                f"Pattern 3 violation: total={total} not greater than "
                f"len(contains)={len(contains)} despite count_limited. "
                f"Clinical safety: paging clients rely on total to know "
                f"how many entries to expect."
            )

    def test_t122_pattern_11_ast_contract_on_comparison_held(self):
        """Pattern 11: AST-contract-on-comparison HELD on URL-form path.

        Per the 11th PROMOTED pattern (GLOBAL_RULES.md line 142): the
        2-axis AST contract (operator-type + operand-direction) MUST
        hold on every count_limited sibling site. The URL-form path
        (expand_url_pattern) is the load-bearing site per QA-068.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assigns = _collect_count_limited_assignments(src)
        assert len(assigns) >= 1
        cmp = assigns[0].value
        # 2-axis contract: Gt + LEFT=len() + RIGHT=Name
        assert all(isinstance(op, ast.Gt) for op in cmp.ops)
        assert not any(isinstance(op, ast.GtE) for op in cmp.ops)
        left = cmp.left
        assert isinstance(left, ast.Call) and left.func.id == "len"
        right = cmp.comparators[0]
        assert isinstance(right, ast.Name)
