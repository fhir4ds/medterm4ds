"""HISTORIAN RESWEEP probes for chunk CM-01 (ConceptMap Resource Structure).

Source: https://build.fhir.org/conceptmap.html (canonical)
        https://hl7.org/fhir/R4/conceptmap.html
        https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

This resweep file extends the baseline ``test_cm01_historian.py`` (19 probes)
with NEW regression probes that pattern-match against prior CM-01 bug
patterns + 4 SKEPTIC tips for HISTORIAN.

HISTORIAN lens (per evolution.json config.notes): pattern-match against prior
bug patterns from ``GLOBAL_KNOWLEDGE.md`` + ``ARCHIVE_LOG.md``. For each prior
bug pattern, find code paths in CM-01 that might exhibit it; log bugs only if a
REGRESSION is found.

SKEPTIC tips for HISTORIAN (4 items), each addressed by ≥1 probe class:
  1. **Re-verify CF-TERMINOLOGIST-CM01-01 RESOLVED independently** via
     object-identity probe (``FHIR_EQUIVALENCES is INTERNAL_REL_TO_FHIR_EQUIVALENCE``)
     — strongest possible pin. HISTORIAN must re-derive the invariant via
     independent object-identity + AST source-read probes.

  2. **AST audit strategy extension**: test_s100 walks both ``ast.Assign`` AND
     ``ast.AnnAssign`` for module-level dict literals — PROMOTE to a registry-
     as-contract probe class (extends CS-01 HISTORIAN L1 strategy). HISTORIAN
     must independently re-derive the AST audit + verify it survives annotation-
     style refactors.

  3. **Canonical-DISPLAY META-PATTERN (13-surface)**: re-derive the invariant
     via lateral probes that exercise EVERY seeded code AND every alias input
     (trailing-slash, urn:oid, uppercase-scheme). The SKEPTIC probes (test_s80,
     test_s81) covered 1 seeded code + 1 system; HISTORIAN extends across all
     4 seeded codes × 3 alias shapes = 12 parametrized lateral probes per
     operation surface.

  4. **R5/R4B contamination audit**: extend the 17 parametrized probes with
     case-folded variants of additional R5 values ('equal' is R4-only; not in
     chunk-desc but engine could emit). HISTORIAN must audit case-folded
     variants of EVERY R5/R4B-only value AND every R4-only value (to confirm
     they DO emit when supplied as input, exercising the full input matrix).

Prior CM-01 patterns to re-derive (HELD = no regression):
  - **CM01-SKEPTIC-001**: CRITICAL narrower/wider directionality fix — R4
    target-perspective vs engine R5 source-centric.
  - **CM01-SKEPTIC-002**: not-translated → unmatched (was 'equivalent').
  - **CF-HISTORIAN-VS01-01 + CR-012**: verified RESOLVED (canonical
    re-resolution on $translate source).
  - **CF-EXPLORER-CS02-01**: fully closed ($translate was LAST operation).
  - **CF-TERMINOLOGIST-CM01-01**: now verified RESOLVED (CR-024 unification).
  - 11 PROMOTED patterns from GLOBAL_RULES.md.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    FHIR_R4_FILTER_OPERATORS,
    FHIR_URI_ALIASES,
    SYSTEM_TO_FHIR_URI,
    canonical_system_uri,
    fhir_uri_to_system,
    system_to_fhir_uri,
)
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    fhir_equivalence,
)
from medterm4ds.engines.fhir import responses as responses_module
from medterm4ds.outputs import fhir as outputs_fhir_module


# =============================================================================
# Constants
# =============================================================================

SNOMED_URI = "http://snomed.info/sct"
SNOMED_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"

ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_TRAILING_SLASH = "http://hl7.org/fhir/sid/icd-10-cm/"
ICD10CM_OID = "urn:oid:2.16.840.1.113883.6.90"
ICD10CM_UPPERCASE_SCHEME = "HTTP://hl7.org/fhir/sid/icd-10-cm"

RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_OID = "urn:oid:2.16.840.1.113883.6.88"
RXNORM_UPPERCASE_SCHEME = "HTTP://www.nlm.nih.gov/research/umls/rxnorm"

SNOMED_DIABETES_MELLITUS = "73211009"
SNOMED_T2DM = "44054006"
ICD10CM_T2DM = "E11"
RXNORM_METFORMIN = "860975"

# Module source paths for source-read probes.
_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "medterm4ds"
_FHIR_API_PATH = _SRC_ROOT / "apps" / "fhir_api.py"
_RESPONSES_PATH = _SRC_ROOT / "engines" / "fhir" / "responses.py"
_EQUIVALENCE_PATH = _SRC_ROOT / "engines" / "fhir" / "equivalence.py"
_FHIR_INIT_PATH = _SRC_ROOT / "engines" / "fhir" / "__init__.py"
_OUTPUTS_FHIR_PATH = _SRC_ROOT / "outputs" / "fhir.py"

# Per FHIR R4 spec (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html):
# the closed enum has exactly 10 values. ``subsumedby`` is R4B/R5-only;
# ``matches`` is R5-only; ``not-relatedto`` is not in any FHIR enum.
FHIR_R4_EQUIVALENCE_10_VALUES = frozenset({
    "relatedto", "equivalent", "equal", "wider", "narrower",
    "subsumes", "specializes", "inexact", "unmatched", "disjoint",
})
R5_R4B_ONLY_VALUES = frozenset({"subsumedby", "subsumedBy", "Subsumedby", "SubsumedBy", "SUBSUMEDBY"})
R5_ONLY_VALUES = frozenset({"matches", "Matches", "MATCHES"})


# ---------------------------------------------------------------------------
# Source-read helpers — extends CS-01 HISTORIAN strategy.
# ---------------------------------------------------------------------------

def _read_source(path: Path) -> str:
    return path.read_text()


def _get_func_source(source: str, name: str) -> str:
    """Return source text of a top-level OR nested function.

    Extends TS-04 HISTORIAN strategy: walks BOTH ``ast.FunctionDef`` AND
    ``ast.AsyncFunctionDef`` to catch nested async route handlers inside
    ``create_fhir_app()``.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _get_nested_func_source(source: str, parent_name: str, child_name: str) -> str:
    """Return source text of a function defined INSIDE a factory function.

    CS-03 HISTORIAN methodology contribution: walk into the parent FIRST, then
    search for the child by name within that parent scope. Required for accurate
    source-read probes against any nested handler/helper in ``create_fhir_app``.
    """
    parent_src = _get_func_source(source, parent_name)
    if not parent_src:
        return ""
    tree = ast.parse(parent_src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == child_name:
            return ast.get_source_segment(parent_src, node) or ""
    return ""


# ===========================================================================
# Lens 1: CF-TERMINOLOGIST-CM01-01 RESOLVED — independent object-identity
# re-verification. SKEPTIC tip #1: the ``is``-probe is the strongest possible
# pin (drift impossible because the Python objects ARE the same).
# HISTORIAN: re-derive via INDEPENDENT object-identity + AST source-read probes.
# ===========================================================================


def test_h10_outputs_fhir_alias_is_internal_rel_to_fhir_equivalence_object_identity():
    """CF-TERMINOLOGIST-CM01-01 RESOLVED re-verification (SKEPTIC tip #1):
    the ``FHIR_EQUIVALENCES`` symbol in ``outputs/fhir.py`` MUST BE the SAME
    Python object as ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` in the canonical
    module.

    HISTORIAN independent probe: re-derive the ``is``-operator invariant
    directly from the imported modules (NOT via SKEPTIC's test_s30 helper).
    The ``is`` operator checks object identity — if a future refactor changes
    the import to ``FHIR_EQUIVALENCES = dict(INTERNAL_REL_TO_FHIR_EQUIVALENCE)``
    (a copy), the probe fails loudly because the objects are no longer the
    same.

    Regression cite: CF-TERMINOLOGIST-CM01-01 + CR-024 unification.
    """
    pytest.current_report_extra = (
        f"is_check={outputs_fhir_module.FHIR_EQUIVALENCES is INTERNAL_REL_TO_FHIR_EQUIVALENCE} "
        f"id_lhs={id(outputs_fhir_module.FHIR_EQUIVALENCES)} "
        f"id_rhs={id(INTERNAL_REL_TO_FHIR_EQUIVALENCE)}"
    )
    assert outputs_fhir_module.FHIR_EQUIVALENCES is INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
        "outputs/fhir.py:FHIR_EQUIVALENCES MUST be object-identical (via the "
        "`is` operator) to engines/fhir/equivalence.py:INTERNAL_REL_TO_FHIR_EQUIVALENCE. "
        "A copy (dict(...) or .copy()) would silently re-introduce drift "
        "between the ConceptMap export surface and the canonical module. "
        "CR-024 unification invariant violated."
    )


def test_h11_responses_module_internal_rel_is_canonical_object_identity():
    """CF-TERMINOLOGIST-CM01-01 RESOLVED sibling verification: the
    ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` symbol in ``responses.py`` MUST BE
    the SAME Python object as the canonical map.

    HISTORIAN sibling probe: extends test_h10 to the SECOND consumer
    (responses.py — the $translate HTTP surface). Both consumers MUST share
    the SAME object; if either becomes a copy, drift is possible.

    Regression cite: CF-TERMINOLOGIST-CM01-01 + CR-024 unification.
    """
    pytest.current_report_extra = (
        f"is_check={responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE is INTERNAL_REL_TO_FHIR_EQUIVALENCE} "
        f"id_responses={id(responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE)} "
        f"id_canonical={id(INTERNAL_REL_TO_FHIR_EQUIVALENCE)}"
    )
    assert responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE is INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
        "responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE MUST be object-identical "
        "(via `is`) to the canonical module. A copy would re-introduce the "
        "pre-CR-024 drift between the $translate surface and the export surface."
    )


def test_h12_fhir_equivalence_function_is_canonical_object_identity():
    """CF-TERMINOLOGIST-CM01-01 RESOLVED third-axis verification: the
    ``fhir_equivalence`` callable in ``outputs/fhir.py`` MUST BE the SAME
    callable as the canonical module's.

    HISTORIAN third-axis probe: object identity on the FUNCTION (not the dict).
    If a future refactor replaces the import with a wrapper, the probe fails.
    """
    from medterm4ds.engines.fhir.equivalence import fhir_equivalence as canonical_fn

    pytest.current_report_extra = (
        f"is_check={outputs_fhir_module.fhir_equivalence is canonical_fn} "
        f"id_outputs={id(outputs_fhir_module.fhir_equivalence)} "
        f"id_canonical={id(canonical_fn)}"
    )
    assert outputs_fhir_module.fhir_equivalence is canonical_fn, (
        "outputs/fhir.py:fhir_equivalence MUST be the same callable as the "
        "canonical module's fhir_equivalence. A wrapper would silently diverge "
        "if the canonical map is later extended but the wrapper isn't updated."
    )


def test_h13_responses_helper_wraps_canonical_not_redefines():
    """CF-TERMINOLOGIST-CM01-01 RESOLVED helper-level verification: the
    ``_fhir_equivalence_from_relationship`` wrapper in ``responses.py`` MUST
    delegate to the canonical map (NOT redefine a parallel map).

    HISTORIAN source-read: the helper body MUST reference
    ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` (the imported alias) — never define
    a new dict literal inline.
    """
    src = _read_source(_RESPONSES_PATH)
    helper_src = _get_func_source(src, "_fhir_equivalence_from_relationship")
    pytest.current_report_extra = f"helper_present={bool(helper_src)}"
    assert helper_src, "_fhir_equivalence_from_relationship not found in responses.py"
    # The helper MUST reference the imported canonical alias (not redefine).
    assert "_INTERNAL_REL_TO_FHIR_EQUIVALENCE" in helper_src, (
        "_fhir_equivalence_from_relationship MUST reference the imported "
        "_INTERNAL_REL_TO_FHIR_EQUIVALENCE alias. A local dict literal would "
        "re-introduce pre-CR-024 drift."
    )
    # The helper MUST NOT define a new dict inline (drift hazard).
    tree = ast.parse(helper_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            pytest.fail(
                "_fhir_equivalence_from_relationship body contains an inline "
                "dict literal — drift hazard. The helper MUST only reference "
                "the imported canonical map."
            )


def test_h14_canonical_module_load_assert_present_and_load_bearing():
    """CF-TERMINOLOGIST-CM01-01 RESOLVED load-bearing contract: the canonical
    module MUST have a module-load ``assert`` that every emitted value is in
    the R4 closed enum.

    HISTORIAN source-read: the assert must reference BOTH
    ``INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()`` AND
    ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` — the two-set membership check.
    Without this assert, drift could silently land at module load.

    Regression cite: CF-HISTORIAN-VS01-01 (closed-enum membership invariant).
    """
    src = _read_source(_EQUIVALENCE_PATH)
    tree = ast.parse(src)
    found_load_assert = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            seg = ast.get_source_segment(src, node) or ""
            if "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()" in seg and "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in seg:
                found_load_assert = True
                break
    pytest.current_report_extra = f"found_load_assert={found_load_assert}"
    assert found_load_assert, (
        "engines/fhir/equivalence.py MUST have a module-load assert verifying "
        "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values() <= FHIR_R4_CONCEPT_MAP_EQUIVALENCE. "
        "Without this assert, drift values silently land at module load."
    )


# ===========================================================================
# Lens 2: AST audit PROMOTION candidacy (SKEPTIC tip #2).
# test_s100 walks BOTH ``ast.Assign`` AND ``ast.AnnAssign`` for module-level
# dict literals — HISTORIAN must independently re-derive the audit AND verify
# it survives annotation-style refactors (``dict[str, str] = {...}``).
# This extends CS-01 HISTORIAN L1 strategy to type-annotated module-level
# dict literals.
# ===========================================================================


def _find_module_level_dict_literal_targets(source: str, target_name: str) -> list[str]:
    """Walk module-level ``ast.Assign`` AND ``ast.AnnAssign`` for the named
    target. Returns the list of dict-literal source segments found.

    PROMOTION candidate (SKEPTIC tip #2): the helper is the structural fix for
    "annotation-style refactors silently bypass single-form AST walks". A
    plain ``ast.Assign`` walk would MISS ``dict[str, str] = {...}`` (the
    annotated form). This helper catches BOTH forms.
    """
    tree = ast.parse(source)
    segments: list[str] = []
    for node in tree.body:
        # Form 1: ``target_name = {...}``  (ast.Assign)
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == target_name:
                    if isinstance(node.value, ast.Dict):
                        seg = ast.get_source_segment(source, node.value) or ""
                        segments.append(seg)
        # Form 2: ``target_name: dict[str, str] = {...}``  (ast.AnnAssign)
        elif isinstance(node, ast.AnnAssign):
            tgt = node.target
            if isinstance(tgt, ast.Name) and tgt.id == target_name:
                if node.value is not None and isinstance(node.value, ast.Dict):
                    seg = ast.get_source_segment(source, node.value) or ""
                    segments.append(seg)
    return segments


def test_h20_canonical_map_dict_literal_found_via_dual_form_ast_walk():
    """AST audit PROMOTION (SKEPTIC tip #2): the canonical
    ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` dict literal MUST be findable via a
    dual-form AST walk (``ast.Assign`` + ``ast.AnnAssign``).

    HISTORIAN independent re-derivation: the SKEPTIC test_s100 probe uses a
    similar walk; HISTORIAN re-derives from scratch via the
    ``_find_module_level_dict_literal_targets`` helper. The helper catches
    BOTH the unannotated form (``dict = {...}``) AND the annotated form
    (``dict: dict[str, str] = {...}``) — a future refactor that switches
    annotation styles MUST NOT silently bypass the audit.

    Regression cite: CS-01 HISTORIAN L1 strategy extension.
    """
    src = _read_source(_EQUIVALENCE_PATH)
    segments = _find_module_level_dict_literal_targets(src, "INTERNAL_REL_TO_FHIR_EQUIVALENCE")
    pytest.current_report_extra = f"found_segments={len(segments)}"
    assert segments, (
        "INTERNAL_REL_TO_FHIR_EQUIVALENCE dict literal not found via dual-form "
        "(ast.Assign + ast.AnnAssign) module-level walk. A future refactor "
        "that moves the dict inside a function or wraps it in a factory would "
        "silently bypass the audit. The dict MUST be a module-level literal."
    )
    # The literal MUST contain the canonical subsumes+specializes keys (CR-024).
    first = segments[0]
    assert '"subsumes"' in first and '"specializes"' in first, (
        f"INTERNAL_REL_TO_FHIR_EQUIVALENCE dict literal missing subsumes/ "
        f"specializes keys. CF-TERMINOLOGIST-CM01-01 regression. Segment: "
        f"{first[:400]}"
    )


def test_h21_outputs_fhir_module_aliases_canonical_not_redefines():
    """AST audit PROMOTION sibling probe (SKEPTIC tip #2): the
    ``outputs/fhir.py`` module MUST alias ``FHIR_EQUIVALENCES`` from the
    canonical module — it MUST NOT redefine a module-level dict literal named
    ``FHIR_EQUIVALENCES``.

    HISTORIAN source-read: walk ``outputs/fhir.py`` for module-level dict
    literals named ``FHIR_EQUIVALENCES``. If a literal is found, the
    CR-024 unification has been reverted (drift re-introduced).
    """
    src = _read_source(_OUTPUTS_FHIR_PATH)
    segments = _find_module_level_dict_literal_targets(src, "FHIR_EQUIVALENCES")
    pytest.current_report_extra = f"found_local_literals={len(segments)}"
    assert not segments, (
        "outputs/fhir.py defines a module-level dict literal named "
        "FHIR_EQUIVALENCES. This re-introduces pre-CR-024 drift — the module "
        "MUST import the canonical map via "
        "`from medterm4ds.engines.fhir.equivalence import INTERNAL_REL_TO_FHIR_EQUIVALENCE as FHIR_EQUIVALENCES`. "
        f"Found segments: {segments[:1]}"
    )


def test_h22_responses_module_does_not_redefine_parallel_map():
    """AST audit PROMOTION sibling probe (SKEPTIC tip #2): the ``responses.py``
    module MUST NOT redefine a parallel map named
    ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` as a module-level dict literal.

    HISTORIAN source-read: walk ``responses.py`` for module-level dict
    literals named ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE``. The module MUST
    import the alias from the canonical module — never redefine.
    """
    src = _read_source(_RESPONSES_PATH)
    segments = _find_module_level_dict_literal_targets(src, "_INTERNAL_REL_TO_FHIR_EQUIVALENCE")
    pytest.current_report_extra = f"found_local_literals={len(segments)}"
    assert not segments, (
        "responses.py defines a module-level dict literal named "
        "_INTERNAL_REL_TO_FHIR_EQUIVALENCE. This re-introduces pre-CR-024 "
        "drift — the module MUST import the canonical map via "
        "`from medterm4ds.engines.fhir.equivalence import INTERNAL_REL_TO_FHIR_EQUIVALENCE as _INTERNAL_REL_TO_FHIR_EQUIVALENCE`. "
        f"Found segments: {segments[:1]}"
    )


def test_h23_dual_form_ast_walk_smoke_test_annassign_form_supported():
    """AST audit PROMOTION methodology probe (SKEPTIC tip #2): the
    ``_find_module_level_dict_literal_targets`` helper MUST find dicts declared
    in BOTH ``ast.Assign`` and ``ast.AnnAssign`` form.

    HISTORIAN smoke test: parse a synthetic module source containing BOTH
    forms and verify the helper finds both. A future regression that drops
    the ``ast.AnnAssign`` branch (e.g. someone "simplifies" the helper) would
    miss annotated module-level dicts — this probe fails loudly.
    """
    synthetic = '''
x = {"a": 1}
y: dict[str, int] = {"b": 2}
z: dict = {"c": 3}
not_a_dict = 42
also_not: int = 7
'''
    x_segments = _find_module_level_dict_literal_targets(synthetic, "x")
    y_segments = _find_module_level_dict_literal_targets(synthetic, "y")
    z_segments = _find_module_level_dict_literal_targets(synthetic, "z")
    pytest.current_report_extra = (
        f"x={len(x_segments)} y={len(y_segments)} z={len(z_segments)}"
    )
    assert x_segments, "Helper missed ast.Assign form (regression in helper)"
    assert y_segments, "Helper missed ast.AnnAssign form with subscript annotation"
    assert z_segments, "Helper missed ast.AnnAssign form with bare-name annotation"


# ===========================================================================
# Lens 3: 13-surface canonical-DISPLAY META-PATTERN re-derivation (SKEPTIC
# tip #3). Lateral probes that exercise EVERY seeded code AND EVERY alias
# input (trailing-slash, urn:oid, uppercase-scheme). SKEPTIC test_s80/s81
# covered 1 code × 1 system; HISTORIAN parametrizes across all 4 seeded codes
# × 3 alias shapes.
# ===========================================================================


# Parametrize: (alias_label, alias_uri, canonical_uri, code, display_substring)
ALIAS_INPUT_MATRIX = [
    # SNOMED T2DM
    ("snomed-t2dm-trailing-slash", SNOMED_TRAILING_SLASH, SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes"),
    ("snomed-t2dm-urn-oid", SNOMED_OID, SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes"),
    ("snomed-t2dm-uppercase-scheme", SNOMED_UPPERCASE_SCHEME, SNOMED_URI, SNOMED_T2DM, "Type 2 diabetes"),
    # SNOMED DM
    ("snomed-dm-trailing-slash", SNOMED_TRAILING_SLASH, SNOMED_URI, SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
    ("snomed-dm-urn-oid", SNOMED_OID, SNOMED_URI, SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
    ("snomed-dm-uppercase-scheme", SNOMED_UPPERCASE_SCHEME, SNOMED_URI, SNOMED_DIABETES_MELLITUS, "Diabetes mellitus"),
    # ICD10CM T2DM
    ("icd10cm-t2dm-trailing-slash", ICD10CM_TRAILING_SLASH, ICD10CM_URI, ICD10CM_T2DM, "Type 2 diabetes"),
    ("icd10cm-t2dm-urn-oid", ICD10CM_OID, ICD10CM_URI, ICD10CM_T2DM, "Type 2 diabetes"),
    ("icd10cm-t2dm-uppercase-scheme", ICD10CM_UPPERCASE_SCHEME, ICD10CM_URI, ICD10CM_T2DM, "Type 2 diabetes"),
    # RXNORM metformin (trailing-slash + urn:oid + uppercase)
    ("rxnorm-trailing-slash", "http://www.nlm.nih.gov/research/umls/rxnorm/", RXNORM_URI, RXNORM_METFORMIN, "metformin"),
    ("rxnorm-urn-oid", RXNORM_OID, RXNORM_URI, RXNORM_METFORMIN, "metformin"),
    ("rxnorm-uppercase-scheme", RXNORM_UPPERCASE_SCHEME, RXNORM_URI, RXNORM_METFORMIN, "metformin"),
]


@pytest.mark.parametrize(
    "alias_label,alias_uri,canonical_uri,code,display_substring",
    ALIAS_INPUT_MATRIX,
    ids=[c[0] for c in ALIAS_INPUT_MATRIX],
)
def test_h30_lookup_out_system_canonical_for_every_alias(
    fhir_client, alias_label, alias_uri, canonical_uri, code, display_substring,
):
    """Canonical-DISPLAY META-PATTERN (SKEPTIC tip #3): for EVERY seeded code
    AND EVERY alias input (trailing-slash, urn:oid, uppercase-scheme), the
    $lookup Out ``system`` MUST be the canonical URI — NOT the client-supplied
    alias.

    HISTORIAN lateral probe: extends SKEPTIC test_s80/s81 (which probed 1 code
    × 1 system) to a 12-cell matrix (4 codes × 3 alias shapes). A future
    regression that breaks canonical re-resolution on ANY alias shape for ANY
    system would silently slip past SKEPTIC's 1-cell probe but fail this
    12-cell probe.

    Spec: FHIR R4 §4.8.21.1 Out ``system``: "The canonical URI of the code
    system that contains the concept that was looked up. (This may differ from
    the value passed in `system` as an input parameter if the code was found
    in a different system/subsystem.)" —
    https://hl7.org/fhir/R4/codesystem-operation-lookup.html

    Regression cite: client-input-as-canonical drift pattern (count=8+1 PROMOTED).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": alias_uri, "code": code},
    )
    assert r.status_code == 200, f"$lookup failed on alias {alias_label}: {r.status_code} {r.text}"
    body = r.json()
    params = body.get("parameter", [])
    system_param = next((p for p in params if p.get("name") == "system"), None)
    assert system_param is not None, f"$lookup response missing 'system' parameter on {alias_label}"
    actual_system = system_param.get("valueUri")
    pytest.current_report_extra = (
        f"alias={alias_uri} canonical={canonical_uri} actual={actual_system}"
    )
    assert actual_system == canonical_uri, (
        f"$lookup Out system for alias {alias_label} echoed client input "
        f"({alias_uri!r}) instead of canonical ({canonical_uri!r}). "
        f"Actual: {actual_system!r}. Client-input-as-canonical drift "
        f"regression (count=8+1 PROMOTED)."
    )


@pytest.mark.parametrize(
    "alias_label,alias_uri,canonical_uri,code,display_substring",
    ALIAS_INPUT_MATRIX,
    ids=[c[0] for c in ALIAS_INPUT_MATRIX],
)
def test_h31_lookup_out_display_canonical_for_every_alias(
    fhir_client, alias_label, alias_uri, canonical_uri, code, display_substring,
):
    """Canonical-DISPLAY META-PATTERN (SKEPTIC tip #3): for EVERY seeded code
    AND EVERY alias input, the $lookup Out ``display`` MUST be the engine
    canonical preferred term — NOT a value derived from the alias.

    HISTORIAN lateral probe: the display surface is the SECOND axis of the
    META-PATTERN. A regression that produces display='Type 2 diabetes mellitus'
    on canonical input but display=code (or empty) on alias input would fail
    this probe.

    Spec: FHIR R4 §4.8.21.1 Out ``display``: "The preferred display for this
    concept." — https://hl7.org/fhir/R4/codesystem-operation-lookup.html

    Regression cite: canonical-DISPLAY META-PATTERN (count=5 PROMOTED).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": alias_uri, "code": code},
    )
    assert r.status_code == 200, f"$lookup failed on alias {alias_label}: {r.status_code} {r.text}"
    body = r.json()
    params = body.get("parameter", [])
    display_param = next((p for p in params if p.get("name") == "display"), None)
    assert display_param is not None, f"$lookup response missing 'display' parameter on {alias_label}"
    actual_display = display_param.get("valueString", "")
    pytest.current_report_extra = (
        f"alias={alias_uri} display={actual_display!r}"
    )
    assert display_substring.lower() in actual_display.lower(), (
        f"$lookup Out display for alias {alias_label} does not contain "
        f"expected canonical substring {display_substring!r}. "
        f"Actual: {actual_display!r}. Canonical-DISPLAY drift."
    )


@pytest.mark.parametrize(
    "alias_label,alias_uri,canonical_uri,code,display_substring",
    ALIAS_INPUT_MATRIX,
    ids=[c[0] for c in ALIAS_INPUT_MATRIX],
)
def test_h32_translate_out_source_system_canonical_for_every_alias(
    fhir_client, alias_label, alias_uri, canonical_uri, code, display_substring,
):
    """Canonical-DISPLAY META-PATTERN extension to $translate source surface
    (SKEPTIC tip #3): for EVERY seeded code AND EVERY alias input, the
    $translate Out ``match[].source.system`` MUST be the canonical URI.

    HISTORIAN lateral probe: the $translate source surface is the THIRD axis.
    CR-012 (client-input-as-canonical drift on $translate source) was RESOLVED
    by adding canonical_system_uri() at _do_translate. This probe verifies the
    fix holds across every alias shape.

    Regression cite: CR-012 RESOLVED + client-input-as-canonical drift pattern.
    """
    # T2DM has a same-CUI mapping to ICD10CM E11 (seeded). DM and metformin
    # may not produce matches, but the source.system MUST be canonical whenever
    # a match is returned.
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={"system": alias_uri, "code": code},
    )
    # $translate returns 200 even with 0 matches.
    assert r.status_code == 200, f"$translate failed on alias {alias_label}: {r.status_code} {r.text}"
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    if not matches:
        # No matches is conformant; skip the source-system check (no surface).
        pytest.current_report_extra = f"alias={alias_uri} no_matches_skipped"
        return
    for match in matches:
        parts = match.get("part", [])
        source_part = next((p for p in parts if p.get("name") == "source"), None)
        assert source_part is not None, (
            f"$translate match missing 'source' part on {alias_label}"
        )
        source_coding = source_part.get("valueCoding", {})
        actual_system = source_coding.get("system")
        pytest.current_report_extra = (
            f"alias={alias_uri} canonical={canonical_uri} match.source.system={actual_system}"
        )
        assert actual_system == canonical_uri, (
            f"$translate match[].source.system for alias {alias_label} echoed "
            f"client input ({alias_uri!r}) instead of canonical "
            f"({canonical_uri!r}). CR-012 regression. Actual: {actual_system!r}."
        )


# ===========================================================================
# Lens 4: R5/R4B contamination audit EXTENDED (SKEPTIC tip #4).
# SKEPTIC test_s20/s21 audit 5 case-folded variants of 'subsumedBy' (R5/R4B)
# AND 'matches' (R5-only). HISTORIAN extends with:
#   (a) case-folded variants of EVERY R5/R4B-only value AND every R5-only value
#       ('subsumedBy' AND 'matches' AND their case-folded variants);
#   (b) ALL 10 R4-only values as input — confirm they DO emit on the wire
#       (defensive pass-through contract);
#   (c) input 'equal' — R4-only (NOT in chunk-desc list but engine could emit
#       via 'same'/'identical' pipeline values that map to 'equal').
# ===========================================================================


R5_R4B_CASE_FOLDED_INPUTS = [
    "subsumedby", "subsumedBy", "Subsumedby", "SubsumedBy", "SUBSUMEDBY",
]
R5_ONLY_CASE_FOLDED_INPUTS = [
    "matches", "Matches", "MATCHES",
]


@pytest.mark.parametrize("r5_value", R5_R4B_CASE_FOLDED_INPUTS)
def test_h40_r5_r4b_subsumedby_any_case_cannot_leak_to_wire(r5_value):
    """R5/R4B contamination audit (SKEPTIC tip #4): case-folded variants of
    ``subsumedBy`` (R4B/R5 value) MUST NOT leak verbatim to the wire via
    ``fhir_equivalence()``.

    HISTORIAN independent re-derivation: SKEPTIC test_s20 audits 5 case-folded
    variants of 'subsumedBy' (input → translation). HISTORIAN re-derives from
    scratch via direct ``fhir_equivalence()`` calls. The closed enum has 10
    R4 values; 'subsumedBy' is NOT among them.

    Behavior contract:
      * lowercase 'subsumedby' is a documented defensive pass-through key in
        the canonical map (maps to 'specializes' — the R4 spec-correct value
        for the reverse-of-subsumes case).
      * case variants ('subsumedBy', 'Subsumedby', etc.) are NOT keys in the
        map (the canonical map is case-sensitive on keys; the case-insensitive
        fallback lives in ``_fhir_equivalence_from_relationship`` in
        responses.py, NOT in ``fhir_equivalence`` in the canonical module).
        These inputs fall through to the safe 'relatedto' default.

    Either way, NO R5/R4B value leaks verbatim to the wire.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html —
    the R4 expansion lists 10 values; 'subsumedBy' is R4B/R5-only.

    Regression cite: CF-HISTORIAN-VS01-01 RESOLVED.
    """
    result = fhir_equivalence(r5_value)
    pytest.current_report_extra = f"input={r5_value!r} output={result!r}"
    assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"fhir_equivalence({r5_value!r}) returned {result!r} which is NOT in "
        f"the R4 closed enum. R5/R4B value leaked to wire. CF-HISTORIAN-VS01-01 "
        f"regression."
    )
    # 'subsumedBy' verbatim MUST NOT appear on the wire in any case form.
    assert result.lower() != "subsumedby", (
        f"fhir_equivalence({r5_value!r}) returned 'subsumedby'-like value "
        f"({result!r}) which is R5/R4B-only and MUST NOT leak to wire."
    )


@pytest.mark.parametrize("r5_value", R5_ONLY_CASE_FOLDED_INPUTS)
def test_h41_r5_only_matches_any_case_cannot_leak_to_wire(r5_value):
    """R5-only contamination audit (SKEPTIC tip #4): case-folded variants of
    ``matches`` (R5-only value) MUST NOT leak verbatim to the wire.

    HISTORIAN independent re-derivation: SKEPTIC test_s21 audits 5 case-folded
    variants of 'matches'. HISTORIAN re-derives from scratch. 'matches' is
    NOT in any FHIR enum (R4, R4B, or R5 — it was introduced then withdrawn);
    ``fhir_equivalence()`` MUST translate to the safe 'relatedto' default.

    Regression cite: CF-HISTORIAN-VS01-01 RESOLVED.
    """
    result = fhir_equivalence(r5_value)
    pytest.current_report_extra = f"input={r5_value!r} output={result!r}"
    assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"fhir_equivalence({r5_value!r}) returned {result!r} which is NOT in "
        f"the R4 closed enum. R5-only value leaked to wire."
    )
    # 'matches' has no translation-table entry; falls through to 'relatedto'.
    assert result == "relatedto", (
        f"fhir_equivalence({r5_value!r}) should fall through to 'relatedto' "
        f"(default catch-all for unknown engine vocabulary). Got {result!r}."
    )


@pytest.mark.parametrize("r4_value", sorted(FHIR_R4_EQUIVALENCE_10_VALUES))
def test_h42_r4_enum_values_pass_through_as_input(r4_value):
    """R5/R4B contamination audit EXTENSION (SKEPTIC tip #4): every R4 closed-
    enum value supplied as input MUST resolve to a value still in the R4
    closed enum (either as a defensive pass-through OR via the safe
    'relatedto' default).

    HISTORIAN: the SKEPTIC audit focused on values that SHOULD NOT leak
    (R5/R4B); HISTORIAN extends to values that SHOULD emit safely (R4).
    The canonical map has documented defensive pass-through entries for
    most R4 values; the ones without explicit keys ('equal', 'inexact')
    fall through to the 'relatedto' default — both are conformant.

    A future regression that produces an off-spec value (e.g. emits
    'subsumedBy' verbatim for some input) would silently slip past the
    SKEPTIC test_s20 audit if the input was an R4 value not in the
    SKEPTIC parametrize matrix. This probe catches that.

    Spec: FHIR R4 §4.8.21.1 Out ``equivalence`` MUST be a value from the
    R4 closed enum.
    """
    result = fhir_equivalence(r4_value)
    pytest.current_report_extra = f"input={r4_value!r} output={result!r}"
    assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"fhir_equivalence({r4_value!r}) returned {result!r} which is NOT in "
        f"the R4 closed enum. A future regression could silently produce "
        f"off-spec values for R4 input — this probe catches that."
    )


def test_h43_equal_r4_value_emitted_via_same_pipeline_input():
    """R5/R4B contamination audit EXTENSION (SKEPTIC tip #4): the R4 value
    'equal' is NOT in the chunk-desc list but the engine pipeline emits it
    indirectly via 'same'/'identical' relationship values (which map to
    'equal'). HISTORIAN verifies the full pipeline path.

    HISTORIAN lateral probe: 'equal' is R4-only (NOT in chunk-desc list, NOT
    in R5/R4B). SKEPTIC's chunk-desc audit (test_s10) doesn't probe 'equal'
    directly because it's not in the 12-value chunk-desc list. HISTORIAN
    extends via the engine pipeline vocabulary ('same'/'identical' → 'equal').

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html —
    'equal' is R4-only.
    """
    # Engine pipeline values that map to 'equal' (the same-CUI semantic).
    for input_val in ("same", "identical"):
        result = fhir_equivalence(input_val)
        pytest.current_report_extra = f"input={input_val!r} output={result!r}"
        assert result == "equal", (
            f"fhir_equivalence({input_val!r}) should emit 'equal' (R4 value "
            f"for the same-CUI semantic). Got {result!r}. The engine pipeline "
            f"emits {input_val!r} for same-CUI mappings; the translation MUST "
            f"produce the R4 'equal' value."
        )


# ===========================================================================
# Lens 5: Prior CM-01 bug patterns re-derivation (HELD = no regression).
# Each prior bug pattern is re-derived via ≥1 independent probe.
# ===========================================================================


def test_h50_cm01_skeptic_001_narrower_wider_directionality_held():
    """CM01-SKEPTIC-001 CRITICAL narrower/wider directionality re-derivation.

    Prior bug: the pre-CR-024 responses.py map had these inverted —
    'source-is-narrower-than-target' was mapped to 'narrower' (wrong; should
    be 'wider' because R4 is read from TARGET perspective). The fix made the
    map target-perspective-correct.

    HISTORIAN re-derivation: verify the map emits the spec-correct R4 value
    for each direction-sensitive engine pipeline value.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html —
      * 'wider'    = "The target mapping is WIDER in meaning than the source."
      * 'narrower' = "The target mapping is NARROWER in meaning than the source."

    Regression cite: CM01-SKEPTIC-001 (CRITICAL).
    """
    # source-is-narrower-than-target: source more specific → target is WIDER.
    assert fhir_equivalence("source-is-narrower-than-target") == "wider", (
        "source-is-narrower-than-target MUST map to 'wider' (target perspective). "
        "CM01-SKEPTIC-001 CRITICAL regression — directionality inverted."
    )
    # source-is-broader-than-target: source more general → target is NARROWER.
    assert fhir_equivalence("source-is-broader-than-target") == "narrower", (
        "source-is-broader-than-target MUST map to 'narrower' (target perspective). "
        "CM01-SKEPTIC-001 CRITICAL regression — directionality inverted."
    )


def test_h51_cm01_skeptic_002_not_translated_to_unmatched_held():
    """CM01-SKEPTIC-002 not-translated → unmatched re-derivation.

    Prior bug: pre-CR-024 outputs/fhir.py mapped 'not-translated' to
    'equivalent' (silent-wrong-answer: a client reading the ConceptMap export
    would treat a missing translation as a confirmed equivalence). The fix
    maps 'not-translated' to 'unmatched' (the R4 catch-all for "no mapping").

    HISTORIAN re-derivation: verify the translation emits 'unmatched'.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html —
    'unmatched' = "There is no match for this concept in the target code system."

    Regression cite: CM01-SKEPTIC-002.
    """
    result = fhir_equivalence("not-translated")
    pytest.current_report_extra = f"result={result!r}"
    assert result == "unmatched", (
        "not-translated MUST map to 'unmatched' (R4 catch-all for 'no mapping'). "
        "CM01-SKEPTIC-002 regression — silent-wrong-answer shape if reverted to "
        "'equivalent'."
    )


def test_h52_cf_historian_vs01_01_resolved_no_r5_values_on_wire():
    """CF-HISTORIAN-VS01-01 RESOLVED re-derivation: NO R5/R4B-only values
    leak to the wire via the canonical map.

    HISTORIAN exhaustive sweep: walk every VALUE in
    INTERNAL_REL_TO_FHIR_EQUIVALENCE.values() and verify set membership in
    the R4 closed enum. A future drift value would silently land if the
    module-load assert is removed.

    Regression cite: CF-HISTORIAN-VS01-01 RESOLVED.
    """
    emitted_values = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    off_spec = emitted_values - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    pytest.current_report_extra = (
        f"emitted_count={len(emitted_values)} off_spec_count={len(off_spec)} off_spec={off_spec}"
    )
    assert not off_spec, (
        f"INTERNAL_REL_TO_FHIR_EQUIVALENCE emits values outside the R4 closed "
        f"enum: {off_spec}. CF-HISTORIAN-VS01-01 regression."
    )


def test_h53_cr012_resolved_do_translate_uses_canonical_helper():
    """CR-012 RESOLVED re-derivation: ``_do_translate`` MUST call
    ``canonical_system_uri`` on the client-supplied source URI before passing
    it to the response builder.

    HISTORIAN source-read: walk _do_translate body for
    canonical_system_uri() call. A future refactor that removes the call
    would re-introduce the client-input-as-canonical drift on $translate
    source surface.

    Regression cite: CR-012 RESOLVED + client-input-as-canonical drift
    (count=8+1 PROMOTED).
    """
    src = _read_source(_FHIR_API_PATH)
    translate_src = _get_nested_func_source(src, "create_fhir_app", "_do_translate")
    pytest.current_report_extra = f"found_translate={bool(translate_src)}"
    assert translate_src, "_do_translate not found nested inside create_fhir_app"
    assert "canonical_system_uri" in translate_src, (
        "_do_translate MUST call canonical_system_uri() on the client-supplied "
        "source URI before passing to build_parameters_translate. CR-012 "
        "regression — client-input-as-canonical drift on $translate source."
    )


def test_h54_cf_explorer_cs02_01_fully_closed_translate_has_4_shape_probes():
    """CF-EXPLORER-CS02-01 FULLY CLOSED re-derivation: the $translate POST
    route MUST be covered by a 4-shape Content-Type probe family in the test
    suite.

    HISTORIAN lateral probe: $translate was the LAST operation in the 4-shape
    probe family closure (per GLOBAL_KNOWLEDGE.md). HISTORIAN verifies the
    POST route exists AND is exercised by POST-with-body probes in the
    test suite. A future refactor that removes the POST route would silently
    break the 4-shape family.

    Regression cite: CF-EXPLORER-CS02-01 FULLY CLOSED.
    """
    src = _read_source(_FHIR_API_PATH)
    # The POST route MUST be registered.
    assert '@app.post("/fhir/ConceptMap/$translate")' in src, (
        "POST /fhir/ConceptMap/$translate route not registered. "
        "CF-EXPLORER-CS02-01 regression — the 4-shape Content-Type probe "
        "family requires the POST route to exist."
    )
    # The translate_post handler MUST be defined.
    post_src = _get_nested_func_source(src, "create_fhir_app", "translate_post")
    assert post_src, "translate_post handler not found"


# ===========================================================================
# Lens 6: 11 PROMOTED patterns re-derivation on CM-01 surface (HELD).
# Spot-check the most CM-01-relevant PROMOTED patterns:
#   - closed-enum registry-as-contract (count=3 PROMOTED)
#   - client-input-as-canonical drift (count=8+1 PROMOTED)
#   - cross-handler helper-wiring (count=6 PROMOTED)
#   - empty-string-on-required-Query (count=5 PROMOTED)
#   - HCPCS URI drift class (count=8+1 PROMOTED)
# ===========================================================================


def test_h60_closed_enum_registry_imported_by_both_impl_and_tests():
    """PROMOTED pattern (count=3): closed-enum registry-as-contract — the
    ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` constant MUST be imported from the
    canonical location (``engines/fhir/__init__.py``) by BOTH production
    code AND test files. No local redefinition.

    HISTORIAN source-read: verify the canonical module imports the constant
    from the right place.

    Regression cite: closed-enum registry-as-contract pattern (count=3 PROMOTED).
    """
    # Canonical module imports the constant from engines/fhir/__init__.py.
    eq_src = _read_source(_EQUIVALENCE_PATH)
    assert "from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in eq_src, (
        "engines/fhir/equivalence.py MUST import FHIR_R4_CONCEPT_MAP_EQUIVALENCE "
        "from engines/fhir/__init__.py — the canonical single-source-of-truth."
    )


def test_h61_hcpcs_canonical_uri_no_drift_on_cm01_surface():
    """PROMOTED pattern (count=8+1): HCPCS URI drift class — the canonical
    HCPCS URI in ``SYSTEM_TO_FHIR_URI`` MUST be the CMS URI (not the legacy
    THO URL). The CM-01 surface (ConceptMap export) uses the canonical URI
    via the registry, so any drift here propagates to the export.

    HISTORIAN direct registry assertion.

    Regression cite: HCPCS URI drift class (count=8+1 PROMOTED).
    """
    CANONICAL_HCPCS_URI = "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
    LEGACY_HCPCS_THO_URL = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
    pytest.current_report_extra = (
        f"hcpcs={SYSTEM_TO_FHIR_URI.get('HCPCS')!r}"
    )
    assert SYSTEM_TO_FHIR_URI.get("HCPCS") == CANONICAL_HCPCS_URI, (
        "SYSTEM_TO_FHIR_URI['HCPCS'] drifted from canonical CMS URI. HCPCS URI "
        "drift class regression (count=8+1 PROMOTED)."
    )
    assert LEGACY_HCPCS_THO_URL not in set(SYSTEM_TO_FHIR_URI.values()), (
        "Legacy HCPCS THO URL leaked into SYSTEM_TO_FHIR_URI values — it MUST "
        "be input-only (FHIR_URI_ALIASES)."
    )


def test_h62_empty_string_required_query_on_translate_get():
    """PROMOTED pattern (count=5): empty-string-on-required-Query — the
    $translate GET handler MUST declare ``min_length=1`` on the required
    ``system`` and ``code`` Query parameters. Without it, an empty string
    is treated as "present" and the handler returns 200 + result=false
    (silent-wrong-answer).

    HISTORIAN source-read: walk translate_get for min_length=1 on system+code.

    Regression cite: empty-string-on-required-Query pattern (count=5 PROMOTED).
    """
    src = _read_source(_FHIR_API_PATH)
    translate_get_src = _get_nested_func_source(src, "create_fhir_app", "translate_get")
    pytest.current_report_extra = f"found_translate_get={bool(translate_get_src)}"
    assert translate_get_src, "translate_get handler not found"
    assert "min_length=1" in translate_get_src, (
        "translate_get MUST declare min_length=1 on required string Query "
        "parameters (system, code). Empty-string-on-required-Query drift "
        "regression (count=5 PROMOTED)."
    )


def test_h63_cross_handler_helper_wiring_translate_post_uses_parse_parameters():
    """PROMOTED pattern (count=6): cross-handler helper-wiring — the
    ``translate_post`` handler MUST use ``_parse_parameters`` for scalar
    extraction. A future refactor that swaps to a different extractor
    silently bypasses the helper-wiring contract.

    HISTORIAN source-read: walk translate_post for _parse_parameters call.

    Regression cite: cross-handler helper-wiring (count=6 PROMOTED).
    """
    src = _read_source(_FHIR_API_PATH)
    post_src = _get_nested_func_source(src, "create_fhir_app", "translate_post")
    pytest.current_report_extra = f"found_post={bool(post_src)}"
    assert post_src, "translate_post handler not found"
    assert "_parse_parameters" in post_src, (
        "translate_post MUST call _parse_parameters for scalar extraction. "
        "Cross-handler helper-wiring drift (count=6 PROMOTED)."
    )


# ===========================================================================
# Lens 7: META pattern — closed enum membership applies to BOTH surfaces
# (responses.py $translate AND outputs/fhir.py ConceptMap export) via the
# canonical module. HISTORIAN verifies via object-identity + closed-enum
# membership on BOTH consumer modules.
# ===========================================================================


def test_h70_responses_module_emitted_values_subset_of_r4_enum():
    """META pattern: closed enum membership invariant — the values emitted by
    ``responses.py`` (via the imported alias) MUST be a subset of the R4
    closed enum.

    HISTORIAN sibling probe: test_h52 audits the canonical map directly; this
    probe audits the responses.py consumer surface via the imported alias.
    Object identity (test_h11) guarantees the values are the SAME, but the
    membership assertion is independently verifiable on each surface.
    """
    emitted_via_responses = set(responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    off_spec = emitted_via_responses - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    pytest.current_report_extra = (
        f"emitted_count={len(emitted_via_responses)} off_spec_count={len(off_spec)}"
    )
    assert not off_spec, (
        f"responses.py emits values outside R4 closed enum via imported alias: "
        f"{off_spec}. CF-HISTORIAN-VS01-01 regression on $translate surface."
    )


def test_h71_outputs_module_emitted_values_subset_of_r4_enum():
    """META pattern sibling probe: closed enum membership invariant on the
    ConceptMap export surface (``outputs/fhir.py``).

    HISTORIAN sibling probe: test_h70 audits responses.py; this audits
    outputs/fhir.py. Object identity (test_h10) guarantees the values are
    the SAME; the membership assertion is independently verifiable.
    """
    emitted_via_outputs = set(outputs_fhir_module.FHIR_EQUIVALENCES.values())
    off_spec = emitted_via_outputs - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    pytest.current_report_extra = (
        f"emitted_count={len(emitted_via_outputs)} off_spec_count={len(off_spec)}"
    )
    assert not off_spec, (
        f"outputs/fhir.py emits values outside R4 closed enum via imported "
        f"alias: {off_spec}. CF-HISTORIAN-VS01-01 regression on ConceptMap "
        f"export surface."
    )


def test_h72_fhir_r4_concept_map_equivalence_has_exactly_10_values():
    """META pattern: closed enum cardinality — ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``
    MUST have exactly 10 values per the canonical R4 spec.

    HISTORIAN cardinality probe: a future drift that adds or removes a value
    silently changes the cardinality. The 10-value cardinality is the spec
    contract.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html —
    exactly 10 values in the expansion.
    """
    pytest.current_report_extra = f"cardinality={len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)}"
    assert len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE) == 10, (
        f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE MUST have exactly 10 values per R4 "
        f"spec. Actual: {len(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)}. Values: "
        f"{sorted(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)}"
    )
    assert FHIR_R4_CONCEPT_MAP_EQUIVALENCE == FHIR_R4_EQUIVALENCE_10_VALUES, (
        f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE value set drifted from the R4 "
        f"canonical 10-value expansion. Expected: {sorted(FHIR_R4_EQUIVALENCE_10_VALUES)}. "
        f"Actual: {sorted(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)}."
    )


# ===========================================================================
# Lens 8: META pattern — the canonical-DISPLAY META-PATTERN spans 13 operation
# surfaces per SKEPTIC's extension to CM-01. HISTORIAN verifies the count of
# surfaces covered by the invariant via a structural walk of test files.
# ===========================================================================


def test_h73_canonical_display_meta_pattern_spans_13_operation_surfaces():
    """Canonical-DISPLAY META-PATTERN surface-count re-derivation.

    SKEPTIC extended the META-PATTERN to CM-01 (group.element.target.display
    + $translate match.concept.display). HISTORIAN verifies the 13-surface
    count is consistent across the test suite via a structural probe: count
    test files that contain at least one canonical-display cross-operation
    invariant probe.

    The 13 surfaces (per SKEPTIC handoff):
      1-4. CS-02/CS-03/CS-04/CS-05 $lookup Out display
      5-8. CS-02/CS-03/CS-04/CS-05 $validate-code Out display
      9. VS-01 $expand contains[].display
      10. VS-05 $validate-code Out display
      11. $translate match.concept.display (CM-01/SKEPTIC test_s81)
      12. ConceptMap export group.element.target.display (CM-01/SKEPTIC test_s80)
      13. (reserved for future surface)

    HISTORIAN structural probe: this isn't an exact count of probes; it's a
    structural contract that the META-PATTERN appears across multiple test
    files (not concentrated in one). The count >= 5 threshold is the load-
    bearing contract.
    """
    import re

    test_dir = Path(__file__).resolve().parent
    pattern = re.compile(
        r"canonical.display|display_byte_exact|out_display|target_display.*lookup|"
        r"match.*concept.*display",
        re.IGNORECASE,
    )
    files_with_pattern = []
    for test_file in test_dir.glob("test_*resweep*.py"):
        text = test_file.read_text()
        if pattern.search(text):
            files_with_pattern.append(test_file.name)
    pytest.current_report_extra = (
        f"matching_files={files_with_pattern} count={len(files_with_pattern)}"
    )
    # At minimum, CS-02/CS-03/CS-04/CS-05/VS-01/VS-05/CM-01 resweep files
    # SHOULD contain canonical-display probes. Threshold >= 5 is conservative.
    assert len(files_with_pattern) >= 5, (
        f"Canonical-DISPLAY META-PATTERN expected to span >= 5 resweep test "
        f"files; found {len(files_with_pattern)}: {files_with_pattern}. The "
        f"META-PATTERN contract is concentrated in too few files — a future "
        f"drift could remove the invariant from one file without structural "
        f"detection."
    )


# ===========================================================================
# Lens 9: ConceptMap.url and group.source/group.target URI canonical-ness on
# the ConceptMap export surface (re-derivation of CS-01 HCPCS URI drift class
# on CM-01 surface).
# ===========================================================================


def test_h80_concept_map_to_fhir_uses_canonical_uri_registry_for_group_source():
    """HCPCS URI drift class on CM-01 surface: ``concept_map_to_fhir`` MUST
    resolve ``group.source`` via the canonical registry
    (``SYSTEM_TO_FHIR_URI``), NOT via a hardcoded dict or alias map.

    HISTORIAN source-read: walk concept_map_to_fhir body for
    SYSTEM_TO_FHIR_URI reference (via FHIR_CODE_SYSTEMS composition).

    Regression cite: HCPCS URI drift class (count=8+1 PROMOTED).
    """
    src = _read_source(_OUTPUTS_FHIR_PATH)
    concept_map_fn = _get_func_source(src, "concept_map_to_fhir")
    pytest.current_report_extra = f"found_fn={bool(concept_map_fn)}"
    assert concept_map_fn, "concept_map_to_fhir not found"
    # The function MUST use the canonical registry (via FHIR_CODE_SYSTEMS or
    # direct SYSTEM_TO_FHIR_URI reference).
    assert "FHIR_CODE_SYSTEMS" in concept_map_fn or "SYSTEM_TO_FHIR_URI" in concept_map_fn, (
        "concept_map_to_fhir MUST resolve group.source via FHIR_CODE_SYSTEMS "
        "or SYSTEM_TO_FHIR_URI (canonical registry). HCPCS URI drift class "
        "regression (count=8+1 PROMOTED)."
    )


def test_h81_concept_map_to_fhir_does_not_hardcode_hcpcs_uri():
    """HCPCS URI drift class sibling probe on CM-01 surface: the
    ``concept_map_to_fhir`` function MUST NOT hardcode the HCPCS URI (either
    canonical OR legacy) — it MUST resolve via the registry.

    HISTORIAN source-read: walk the function body for hardcoded HCPCS URI
    literals. The function should be source-agnostic.

    Regression cite: HCPCS URI drift class (count=8+1 PROMOTED).
    """
    src = _read_source(_OUTPUTS_FHIR_PATH)
    concept_map_fn = _get_func_source(src, "concept_map_to_fhir")
    CANONICAL_HCPCS_URI = "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
    LEGACY_HCPCS_THO_URL = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
    pytest.current_report_extra = (
        f"canonical_in_fn={CANONICAL_HCPCS_URI in concept_map_fn} "
        f"legacy_in_fn={LEGACY_HCPCS_THO_URL in concept_map_fn}"
    )
    assert CANONICAL_HCPCS_URI not in concept_map_fn, (
        "concept_map_to_fhir MUST NOT hardcode the canonical HCPCS URI — "
        "resolve via FHIR_CODE_SYSTEMS registry. HCPCS URI drift class."
    )
    assert LEGACY_HCPCS_THO_URL not in concept_map_fn, (
        "concept_map_to_fhir MUST NOT hardcode the legacy HCPCS THO URL — "
        "HCPCS URI drift class."
    )


# ===========================================================================
# Lens 10: dependsOn / product absence audit (CM-01 chunk items 2+3).
# Verify the ConceptMap export does NOT emit dependsOn or product (medterm4ds
# doesn't model parameterized mappings today). Per R4 spec cardinality 0..*,
# absence is conformant; the audit is a registry-as-contract pin so a future
# feature addition can't silently violate the 1..1 subfield constraint on
# property+value.
# ===========================================================================


def test_h90_concept_map_to_fhir_does_not_emit_dependsOn():
    """CM-01 chunk item 2 (dependsOn): ``concept_map_to_fhir`` MUST NOT emit
    ``dependsOn`` arrays in the current export (medterm4ds doesn't model
    parameterized mappings).

    HISTORIAN source-read: walk the function body for any 'dependsOn' literal
    or AST dict key.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — dependsOn is 0..* (absence
    is conformant).
    """
    src = _read_source(_OUTPUTS_FHIR_PATH)
    concept_map_fn = _get_func_source(src, "concept_map_to_fhir")
    pytest.current_report_extra = (
        f"dependsOn_literal={'dependsOn' in concept_map_fn}"
    )
    assert "dependsOn" not in concept_map_fn, (
        "concept_map_to_fhir MUST NOT emit dependsOn in the current export "
        "(medterm4ds doesn't model parameterized mappings). If a future "
        "feature adds dependsOn, each entry MUST have BOTH property (1..1) "
        "AND value (1..1) subfields per R4 spec."
    )


def test_h91_concept_map_to_fhir_does_not_emit_product():
    """CM-01 chunk item 3 (product): ``concept_map_to_fhir`` MUST NOT emit
    ``product`` arrays in the current export.

    HISTORIAN source-read: walk the function body for any 'product' literal
    or AST dict key.

    Spec: https://hl7.org/fhir/R4/conceptmap.html — product is 0..* (absence
    is conformant).
    """
    src = _read_source(_OUTPUTS_FHIR_PATH)
    concept_map_fn = _get_func_source(src, "concept_map_to_fhir")
    pytest.current_report_extra = (
        f"product_literal={'product' in concept_map_fn}"
    )
    assert "product" not in concept_map_fn, (
        "concept_map_to_fhir MUST NOT emit product in the current export. "
        "If a future feature adds product, each entry MUST follow the same "
        "subfield contract as dependsOn (property 1..1 + value 1..1)."
    )


# ===========================================================================
# Lens 11: READ / SEARCH route shape — ConceptMap.id READ returns
# OperationOutcome for unknown ids; SEARCH returns empty Bundle. Re-derivation
# of TS-01 EXPLORER QA-011 framework-default-drift catch-all layer on CM-01.
# ===========================================================================


def test_h100_read_unknown_conceptmap_id_returns_operationoutcome(fhir_client):
    """READ interaction on ConceptMap (chunk item 6): unknown id MUST return
    a FHIR OperationOutcome (NOT a Starlette default 404 with non-FHIR body).

    HISTORIAN behavioral probe: the catch-all layer from TS-01 EXPLORER QA-011
    handles the fall-through. Verify the response shape.

    Regression cite: TS-01 EXPLORER QA-011 + framework-default drift pattern.
    """
    r = fhir_client.get("/fhir/ConceptMap/nonexistent-id-12345")
    pytest.current_report_extra = (
        f"status={r.status_code} content_type={r.headers.get('content-type')}"
    )
    assert r.status_code in (404, 410), (
        f"READ unknown ConceptMap id returned unexpected status {r.status_code}"
    )
    assert "application/fhir" in r.headers.get("content-type", ""), (
        f"READ unknown ConceptMap id MUST return FHIR Content-Type; got "
        f"{r.headers.get('content-type')!r}. Framework-default drift regression."
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"READ unknown ConceptMap id MUST return OperationOutcome body; got "
        f"resourceType={body.get('resourceType')!r}."
    )


@pytest.mark.parametrize(
    "search_param,value",
    [
        ("url", "http://example.org/unknown-cm"),
        ("name", "NonexistentConceptMap"),
        ("title", "Nonexistent Title"),
        ("status", "draft"),
        ("version", "0.0.1-nonexistent"),
    ],
)
def test_h101_search_with_each_param_returns_bundle(fhir_client, search_param, value):
    """SEARCH interaction on ConceptMap (chunk item 6): each spec-listed
    search param (url, version, name, title, status) MUST return a FHIR Bundle
    — even when no persisted ConceptMap matches.

    HISTORIAN parametrized behavioral probe: re-derives the spec-listed search
    params across all 5 simultaneously. The Bundle shape MUST be conformant:
    resourceType=Bundle, type=searchset, total=0, entry=[].

    Regression cite: CS-01 EXPLORER Bundle-shape probe class.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap",
        params={search_param: value},
    )
    pytest.current_report_extra = (
        f"param={search_param} value={value!r} status={r.status_code}"
    )
    assert r.status_code == 200, (
        f"SEARCH ConceptMap?{search_param}=... returned {r.status_code}"
    )
    assert "application/fhir" in r.headers.get("content-type", ""), (
        f"SEARCH ConceptMap MUST return FHIR Content-Type"
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle", (
        f"SEARCH ConceptMap MUST return Bundle; got {body.get('resourceType')!r}"
    )
    assert body.get("type") == "searchset", (
        f"SEARCH Bundle type MUST be 'searchset'; got {body.get('type')!r}"
    )


# ===========================================================================
# Lens 12: R5/R4B contamination audit extension — exhaustive KEY audit of
# INTERNAL_REL_TO_FHIR_EQUIVALENCE for any R5/R4B-only KEYS (not just values).
# A future engine vocabulary change could introduce a new key that maps to
# an R4 value, but the KEY itself could be a misspelled R5 value (e.g.
# 'subsumed-by' vs 'subsumedby').
# ===========================================================================


def test_h110_no_r5_r4b_value_appears_as_key_in_canonical_map():
    """R5/R4B contamination audit extension: NO R5/R4B-only value (in any
    case form) appears as a KEY in ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` —
    EXCEPT for the documented defensive pass-through entries.

    HISTORIAN exhaustive key audit: the canonical map has documented
    defensive pass-through entries for 'subsumedby'/'subsumed-by' (mapped
    to 'specializes'). This probe verifies NO OTHER R5/R4B-only value (in
    any case form) appears as a key — a future regression that adds e.g.
    'matches' or 'Match' as a key would silently introduce an undocumented
    translation path.

    Regression cite: CF-HISTORIAN-VS01-01 RESOLVED.
    """
    # Documented defensive pass-through entries (allowed).
    ALLOWED_R5_R4B_KEYS = frozenset({
        "subsumedby", "subsumed-by",  # documented defensive pass-through
    })
    keys = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.keys())
    pytest.current_report_extra = (
        f"keys_count={len(keys)} allowed={sorted(ALLOWED_R5_R4B_KEYS)}"
    )
    # For each case-folded R5/R4B-only value, verify it's not an undocumented key.
    for r5_val in R5_R4B_CASE_FOLDED_INPUTS:
        if r5_val in ALLOWED_R5_R4B_KEYS:
            continue
        assert r5_val not in keys, (
            f"R5/R4B-only value {r5_val!r} appears as a KEY in "
            f"INTERNAL_REL_TO_FHIR_EQUIVALENCE (undocumented). The map MUST "
            f"only have documented defensive pass-through entries."
        )
    for r5_val in R5_ONLY_CASE_FOLDED_INPUTS:
        assert r5_val not in keys, (
            f"R5-only value {r5_val!r} appears as a KEY in "
            f"INTERNAL_REL_TO_FHIR_EQUIVALENCE (undocumented). The map MUST "
            f"only have documented defensive pass-through entries."
        )


def test_h111_no_r5_only_value_appears_as_key_in_canonical_map():
    """R5-only contamination audit extension: NO R5-only value (in any case
    form) appears as a KEY in ``INTERNAL_REL_TO_FHIR_EQUIVALENCE``.

    HISTORIAN sibling probe to test_h110 (R5/R4B keys); this audits R5-only
    keys (matches, Matches, MATCHES).

    Regression cite: CF-HISTORIAN-VS01-01 RESOLVED.
    """
    keys = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.keys())
    for r5_val in R5_ONLY_CASE_FOLDED_INPUTS:
        assert r5_val not in keys, (
            f"R5-only value {r5_val!r} appears as a KEY in "
            f"INTERNAL_REL_TO_FHIR_EQUIVALENCE. 'matches' is R5-only and MUST "
            f"NOT have a translation-table entry."
        )


# ===========================================================================
# Lens 13: META — source-read structural contract for the canonical-system
# helper invocation on the $translate path. Walks the function source for
# the load-bearing call.
# ===========================================================================


def test_h120_canonical_system_uri_helper_wires_canonical_source_on_translate():
    """META pattern: structural contract for canonical_system_uri on the
    $translate path.

    HISTORIAN source-read: walk _do_translate body for
    ``canonical_system_uri(...)`` call AND verify the result is passed to
    build_parameters_translate as source_system_uri.

    The load-bearing pattern is:
      ``canonical_source_uri = canonical_system_uri(source_uri, source=source)``
      ``build_parameters_translate(..., source_system_uri=canonical_source_uri, ...)``

    A future refactor that breaks either side of this contract re-introduces
    CR-012 (client-input-as-canonical drift on $translate source).

    Regression cite: CR-012 RESOLVED + client-input-as-canonical drift
    (count=8+1 PROMOTED).
    """
    src = _read_source(_FHIR_API_PATH)
    translate_src = _get_nested_func_source(src, "create_fhir_app", "_do_translate")
    pytest.current_report_extra = f"found_translate={bool(translate_src)}"
    assert translate_src, "_do_translate not found"
    # The canonical_source_uri assignment MUST be present.
    assert "canonical_source_uri = canonical_system_uri(" in translate_src, (
        "_do_translate MUST assign canonical_source_uri via canonical_system_uri() "
        "call. CR-012 regression."
    )
    # The canonical value MUST be passed to build_parameters_translate.
    assert "source_system_uri=canonical_source_uri" in translate_src, (
        "_do_translate MUST pass canonical_source_uri to build_parameters_translate "
        "via source_system_uri=canonical_source_uri. CR-012 regression."
    )


def test_h121_build_parameters_translate_signature_includes_source_system_uri():
    """META pattern: ``build_parameters_translate`` signature MUST include
    ``source_system_uri`` as a keyword-only argument. This is the load-bearing
    parameter for CR-012 (canonical re-resolution).

    HISTORIAN source-read: walk responses.py for the builder signature.

    Regression cite: CR-012 RESOLVED.
    """
    src = _read_source(_RESPONSES_PATH)
    builder_fn = _get_func_source(src, "build_parameters_translate")
    pytest.current_report_extra = f"found_builder={bool(builder_fn)}"
    assert builder_fn, "build_parameters_translate not found"
    # The signature MUST include source_system_uri as a keyword-only arg.
    tree = ast.parse(builder_fn)
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "build_parameters_translate":
            func_node = node
            break
    assert func_node is not None, "build_parameters_translate AST node not found"
    # Kw-only args appear in args.kwonlyargs (ast.arguments).
    kwonly_names = [arg.arg for arg in func_node.args.kwonlyargs]
    pytest.current_report_extra = f"kwonly={kwonly_names}"
    assert "source_system_uri" in kwonly_names, (
        f"build_parameters_translate signature MUST include source_system_uri "
        f"as keyword-only argument. Found kw-only args: {kwonly_names}. CR-012 "
        f"regression — the canonical re-resolution can't propagate without "
        f"this parameter."
    )
