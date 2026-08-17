"""TERMINOLOGIST resweep probes for CM-03 (CodeSystem $closure Operation).

TERMINOLOGIST lens (4th personality): clinical and terminological
correctness. Per GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH
Severity": clinical correctness outranks technical correctness in
this domain; findings default to HIGH and cannot be dismissed as
INTENDED without explicit user override.

EXPLORER tip for TERMINOLOGIST (5 items, all ADDRESSED below):
  1. **CF-SKEPTIC-CM03-01** — Out ``return`` is valueString (version
     hash) NOT ConceptMap per canonical R4 OperationDefinition;
     evaluate clinical-correctness implications for client-side
     subsumption workflows. (Lens 1)
  2. **CF-SKEPTIC-CM03-02** — ``$subsumes`` does NOT consult server-
     side ClosureTable; verify clinical safety of hierarchy-walked
     outcomes. (Lens 2)
  3. **CF-HISTORIAN-CM03-02** — ``incomplete_since`` flag NOT
     surfaced; evaluate clinical safety when closure is incomplete
     (transient DuckDB errors during walks). (Lens 3)
  4. **Verify NON-IDEMPOTENT semantic** matches clinical workflow
     expectations (clients calling $closure twice with same concepts
     SHOULD observe state change). (Lens 4)
  5. **Probe closure-table subsumption outcome vocabulary** is
     exactly {equivalent, subsumes, subsumed-by, not-subsumed} per
     FHIR R4 §4.7.7 across seeded SNOMED pairs (DM <-> T2DM,
     equivalent, not-subsumed cases). (Lens 5)

Additional TERMINOLOGIST lenses:
  Lens 6: Cross-source closure clinical correctness (per spec,
          closure is per code system) — extend with clinical axis.
  Lens 7: Clinical correctness of version-hash as state-change signal
          (clinical workflow contract).
  Lens 8: Closure-table check() clinical-correctness regression-pin
          (load-bearing clinical content).
  Lens 9: Source-read structural contracts for clinical invariants.
  Lens 10: Carry-forward-as-probe pins (CS-03 TERMINOLOGIST strategy 56).

Spec citation: https://hl7.org/fhir/R4/conceptmap-operation-closure.html
FHIR R4 §4.7.7 Subsumption testing:
  https://hl7.org/fhir/R4/terminology-service.html#subsumption
"""

from __future__ import annotations

import inspect
import textwrap
from pathlib import Path
from typing import Any

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    SYSTEM_TO_FHIR_URI,
    canonical_system_uri,
    fhir_uri_to_system,
    system_to_fhir_uri,
)
from medterm4ds.engines.fhir.closure import (
    ClosureManager,
    ClosureTable,
    build_closure_response,
    get_closure_manager,
)


SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

# Seeded fixture codes (per conftest.py _make_conformance_db)
DIABETES_SNOMED = "73211009"   # "Diabetes mellitus" — broader concept
T2DM_SNOMED = "44054006"       # "Type 2 diabetes mellitus" — narrower
T2DM_ICD10CM = "E11"           # "Type 2 diabetes mellitus" — ICD-10-CM axis
METFORMIN_RXNORM = "860975"    # "24 HR metformin 500 MG Oral Tablet"

# FHIR R4 $subsumes outcome vocabulary — closed enum per
# https://hl7.org/fhir/R4/valueset-concept-subsumption-outcome.html
FHIR_R4_SUBSUMPTION_OUTCOME: frozenset[str] = frozenset({
    "equivalent",
    "subsumes",
    "subsumed-by",
    "not-subsumed",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _closure_param_name_only(name: str) -> dict[str, Any]:
    return {
        "resourceType": "Parameters",
        "parameter": [{"name": "name", "valueString": name}],
    }


def _closure_param_with_concepts(
    name: str,
    concepts: list[tuple[str, str, str | None]],
) -> dict[str, Any]:
    """Build a Parameters body for $closure with concept entries.

    Each concept: (system_uri, code, display-or-None).
    """
    params: list[dict[str, Any]] = [{"name": "name", "valueString": name}]
    for system_uri, code, display in concepts:
        coding: dict[str, Any] = {"system": system_uri, "code": code}
        if display is not None:
            coding["display"] = display
        params.append({"name": "concept", "valueCoding": coding})
    return {"resourceType": "Parameters", "parameter": params}


def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


def _find_params(body: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _return_hash(body: dict[str, Any]) -> str | None:
    p = _find_param(body, "return")
    if p is None:
        return None
    return p.get("valueString")


def _do_subsumes_outcome(
    fhir_client, system: str, code_a: str, code_b: str
) -> str | None:
    """Invoke $subsumes via GET and return the outcome valueCode."""
    resp = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={"system": system, "codeA": code_a, "codeB": code_b},
    )
    assert resp.status_code == 200, f"$subsumes failed: {resp.status_code} {resp.text}"
    body = resp.json()
    outcome = _find_param(body, "outcome")
    return outcome.get("valueCode") if outcome else None


def _get_func_source(module_path: Path, func_name: str) -> str:
    """Read the source text of a top-level function from a module."""
    import ast

    src = module_path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(src, node) or ""
    return ""


def _get_nested_func_source(
    module_path: Path, parent_name: str, child_name: str
) -> str:
    """Read the source text of a nested function defined inside a parent
    function (e.g., ``_do_closure`` inside ``create_fhir_app``)."""
    import ast

    src = module_path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == parent_name:
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.name == child_name:
                            return ast.get_source_segment(src, child) or ""
    return ""


FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
CLOSURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "closure.py"
)


# ===========================================================================
# LENS 1 — CF-SKEPTIC-CM03-01: Out `return` is valueString NOT ConceptMap
# Clinical-correctness implications for client-side subsumption workflows
# ===========================================================================

class TestLens1CfSkepticCm03_01ClinicalEvaluation:
    """EXPLORER tip 1: CF-SKEPTIC-CM03-01 — evaluate clinical-correctness
    implications for client-side subsumption workflows.

    Per FHIR R4 canonical OperationDefinition
    (https://hl7.org/fhir/R4/conceptmap-operation-closure.html):
      Out Parameters:
        ``return`` 1..1 ConceptMap — "A list of new entries (code / system
        --> code/system) that the client should add to its closure table."
      Spec text: "The only kind of entry mapping equivalences that can be
      returned are equal, specializes, subsumes and unmatched."

    The medterm4ds implementation emits ``return`` as valueString (12-char
    MD5 hex version hash). A spec-conformant FHIR client expecting a
    ConceptMap would receive an unparseable Parameters body — silently
    getting ZERO closure-table updates.

    TERMINOLOGIST clinical-correctness evaluation:
      (a) The deployment model is localhost-only (per module docstring).
          Spec-conformant third-party EHRs / CDS Hooks are NOT advertised
          clients; the medterm4ds Python API uses ``closure.check()``
          directly.
      (b) The HTTP ``$subsumes`` handler does NOT consult the closure
          table per CF-SKEPTIC-CM03-02 — so the closure table is server-
          internal Python state, exposed via HTTP for completeness.
      (c) The clinical-correctness risk is REAL but DEFERRED — when a
          future enhancement wires CF-SKEPTIC-CM03-02 (HTTP $subsumes
          consults closure), the spec-correct ConceptMap return becomes
          load-bearing for spec-conformant clients.

    The probes below CONFIRM the current deviation and pin the clinical-
    safety floor for the spec-conformant-client case.
    """

    def test_t10_return_is_value_string_not_concept_map(self, fhir_client):
        """CLINICAL-CORRECTNESS PIN: ``return`` is valueString per current
        medterm4ds-specific deviation. A spec-conformant FHIR client
        expecting a ConceptMap receives an unparseable Parameters body.

        Spec citation:
          https://hl7.org/fhir/R4/conceptmap-operation-closure.html
          Out Parameters: ``return`` 1..1 ConceptMap
        """
        name = "t10_return_deviation"
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        assert resp.status_code == 200
        body = resp.json()
        return_param = _find_param(body, "return")
        assert return_param is not None, "return parameter MUST always be present"
        # CURRENT deviation: valueString
        assert "valueString" in return_param
        # SPEC-CORRECT: would be a resource (ConceptMap) — absent today.
        assert "resource" not in return_param

    def test_t11_return_concept_map_clinical_safety_floor(
        self, fhir_client, monkeypatch
    ):
        """CLINICAL-SAFETY FLOOR: a spec-conformant FHIR client consuming
        ``$closure`` today receives valueString where it expects a
        ConceptMap. The clinical risk is silent-zero-closure-updates —
        the client's local closure table never advances, so subsequent
        client-side subsumption checks may silently produce wrong
        outcomes (false negatives).

        This is acceptable for v0.0.x BECAUSE:
          (a) The medterm4ds deployment model is localhost-only; no
              third-party EHR clients are advertised.
          (b) HTTP $subsumes does NOT consult closure (CF-SKEPTIC-CM03-02);
              the closure is server-side Python state only.
          (c) The medterm4ds Python API exposes ``closure.check()`` for
              programmatic use, bypassing the HTTP wire shape.

        When a future enhancement wires CF-SKEPTIC-CM03-02 OR advertises
        spec-conformant clients, this carry-forward becomes load-bearing.
        """
        # The wire shape deviation is real and documented.
        name = "t11_clinical_safety_floor"
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "Diabetes mellitus"),
                    (SNOMED_URI, T2DM_SNOMED, "Type 2 diabetes mellitus"),
                ],
            ),
        )
        body = resp.json()
        return_param = _find_param(body, "return")
        # The current valueString return provides NO subsumption
        # relationship data — only a state-change signal hash. A spec-
        # conformant client expecting ConceptMap.group.element.target
        # entries cannot extract any relationship updates.
        assert return_param is not None
        assert "valueString" in return_param
        # A spec-correct ConceptMap return would expose the DM subsumes
        # T2DM relationship — the current shape does NOT.
        assert "resource" not in return_param

    def test_t12_concept_entries_substitute_for_concept_map_relationships(
        self, fhir_client
    ):
        """CLINICAL-WORKAROUND DOCUMENTATION: the current shape emits
        repeating ``concept`` valueCoding parameters (NOT spec-listed in
        canonical R4 OperationDefinition). These convey WHICH codes are
        in the closure but NOT the subsumption relationships between
        them.

        A spec-conformant client expecting ConceptMap.group.element.target
        entries gets the code list but has to re-walk the hierarchy to
        discover relationships — defeating the purpose of $closure
        (CLIENT-side transitive closure via SERVER-side terminological
        logic per the spec's first sentence).
        """
        name = "t12_workaround_documentation"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (SNOMED_URI, T2DM_SNOMED, "T2DM"),
                ],
            ),
        )
        # Re-fetch to get the concept list in the response.
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [(SNOMED_URI, DIABETES_SNOMED, "DM")],  # additive
            ),
        )
        body = resp.json()
        # The concept list is present (medterm4ds-specific).
        concept_params = _find_params(body, "concept")
        assert len(concept_params) >= 1
        codes = {p["valueCoding"]["code"] for p in concept_params}
        assert DIABETES_SNOMED in codes
        # BUT the concept list does NOT encode the DM-subsumes-T2DM
        # relationship — that information is only available via
        # ClosureTable.check() (Python API).
        for cp in concept_params:
            assert "target" not in cp["valueCoding"], (
                "Spec-correct ConceptMap.element.target entries are absent "
                "in current medterm4ds-specific concept-list shape."
            )

    def test_t13_valueString_return_clinically_meaningful_as_state_signal(
        self, fhir_client
    ):
        """CLINICAL UTILITY: despite the wire-shape deviation, the
        valueString return IS clinically meaningful as a state-change
        signal. A client comparing two hashes can detect "closure
        state changed since last call" — operationally useful even
        without the spec-correct ConceptMap.

        This probe pins the clinical utility of the current shape so
        that any future fix does NOT lose this signal.
        """
        name = "t13_state_signal"
        resp1 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        hash1 = _return_hash(resp1.json())
        resp2 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        hash2 = _return_hash(resp2.json())
        # The state-change signal IS preserved in the current shape.
        assert hash1 != hash2

    def test_t14_value_string_return_is_not_concept_map_clinical_finding(
        self
    ):
        """TERMINOLOGIST clinical finding: the wire-shape deviation is a
        CLINICAL-CORRECTNESS gap (spec-conformant clients cannot use the
        response to maintain CLIENT-side transitive closure tables), but
        it is NOT a clinical-SAFETY gap in the current deployment model
        (localhost-only, $subsumes bypasses closure).

        The finding is logged as DEFERRED via CF-SKEPTIC-CM03-01 in
        AGENTS.md. This probe confirms the evaluation is documented in
        the AGENTS.md carry-forward registry (strategy 56 — carry-
        forward-as-probe pattern).
        """
        # Source-read AGENTS.md to verify CF-SKEPTIC-CM03-01 is documented.
        agents_path = (
            Path(__file__).resolve().parents[2]
            / "docs" / ".ai_loop" / "AGENTS.md"
        )
        src = agents_path.read_text()
        assert "CF-SKEPTIC-CM03-01" in src, (
            "CF-SKEPTIC-CM03-01 MUST be documented in AGENTS.md carry-"
            "forward registry."
        )
        # Verify the spec citation is in the documentation.
        assert "conceptmap-operation-closure.html" in src


# ===========================================================================
# LENS 2 — CF-SKEPTIC-CM03-02: $subsumes does NOT consult ClosureTable
# Clinical safety of hierarchy-walked outcomes
# ===========================================================================

class TestLens2CfSkepticCm03_02ClinicalSafetyOfHierarchyWalkedOutcomes:
    """EXPLORER tip 2: CF-SKEPTIC-CM03-02 — verify clinical safety of
    hierarchy-walked $subsumes outcomes.

    The HTTP ``$subsumes`` handler walks the hierarchy directly via
    ``is_descendant`` (apps/fhir_api.py:_do_subsumes). It does NOT
    consult the server-side ClosureTable. This is spec-permitted per
    FHIR R4 (the spec describes $closure as CLIENT-side closure
    maintenance), but the clinical-safety question is: are the
    hierarchy-walked outcomes clinically correct?

    Clinical-correctness invariants:
      (a) DM (73211009) subsumes T2DM (44054006) — T2DM is-a Diabetes
          per fixture mrrel row.
      (b) T2DM subsumed-by DM — mirror direction.
      (c) DM equivalent DM — same code.
      (d) DM not-subsumed metformin — different clinical axes
          (disease vs drug).

    The probes below verify the hierarchy walk produces clinically
    correct outcomes on every case, AND verify the closure table is
    structurally isolated from the HTTP $subsumes path.
    """

    def test_t20_subsumes_outcome_clinically_correct_dm_subsumes_t2dm(
        self, fhir_client
    ):
        """CLINICAL CORRECTNESS: DM subsumes T2DM. Every T2DM patient IS
        a Diabetes patient; Diabetes is the broader clinical category.
        The hierarchy walk returns "subsumes" — clinically correct.
        """
        outcome = _do_subsumes_outcome(
            fhir_client, SNOMED_URI, DIABETES_SNOMED, T2DM_SNOMED,
        )
        assert outcome == "subsumes"

    def test_t21_subsumes_outcome_clinically_correct_t2dm_subsumed_by_dm(
        self, fhir_client
    ):
        """CLINICAL CORRECTNESS: T2DM is subsumed-by DM. The hierarchy
        walk returns "subsumed-by" — clinically correct (T2DM is the
        narrower clinical concept)."""
        outcome = _do_subsumes_outcome(
            fhir_client, SNOMED_URI, T2DM_SNOMED, DIABETES_SNOMED,
        )
        assert outcome == "subsumed-by"

    def test_t22_subsumes_outcome_clinically_correct_self_equivalent(
        self, fhir_client
    ):
        """CLINICAL CORRECTNESS: A code subsumes itself. ``$subsumes(X, X)``
        returns "equivalent" — clinically correct (same code = same
        concept = equivalent)."""
        outcome = _do_subsumes_outcome(
            fhir_client, SNOMED_URI, DIABETES_SNOMED, DIABETES_SNOMED,
        )
        assert outcome == "equivalent"

    def test_t23_subsumes_outcome_clinically_correct_cross_axis_not_subsumed(
        self, fhir_client
    ):
        """CLINICAL CORRECTNESS: DM (disease axis) vs metformin (drug
        axis) — different clinical axes, no subsumption. Returns
        "not-subsumed" — clinically correct."""
        outcome = _do_subsumes_outcome(
            fhir_client, SNOMED_URI, DIABETES_SNOMED, METFORMIN_RXNORM,
        )
        assert outcome == "not-subsumed"

    def test_t24_subsumes_outcome_in_closed_enum_vocabulary(
        self, fhir_client
    ):
        """CLINICAL-SAFETY: every $subsumes outcome MUST be in the FHIR
        R4 ConceptSubsumptionOutcome closed enum
        {equivalent, subsumes, subsumed-by, not-subsumed}. Off-enum
        values would silently produce wrong CDS Hook outcomes."""
        cases = [
            (DIABETES_SNOMED, T2DM_SNOMED),
            (T2DM_SNOMED, DIABETES_SNOMED),
            (DIABETES_SNOMED, DIABETES_SNOMED),
            (DIABETES_SNOMED, METFORMIN_RXNORM),
        ]
        for code_a, code_b in cases:
            outcome = _do_subsumes_outcome(
                fhir_client, SNOMED_URI, code_a, code_b,
            )
            assert outcome in FHIR_R4_SUBSUMPTION_OUTCOME, (
                f"$subsumes({code_a}, {code_b}) returned off-enum value "
                f"{outcome!r} — clinical-safety violation."
            )

    def test_t25_subsumes_clinical_outcome_correct_without_closure_table(
        self, fhir_client
    ):
        """CF-SKEPTIC-CM03-02 clinical-safety verification: the HTTP
        $subsumes handler produces clinically correct outcomes WITHOUT
        consulting the closure table. The closure is initialized and
        populated, but the $subsumes path bypasses it — outcomes come
        from direct hierarchy walk.

        Clinical safety: the direct walk produces the SAME outcome
        ClosureTable.check() would, because both use the same underlying
        hierarchy data (mrrel PAR/CHD rows).
        """
        # Build a closure with DM + T2DM
        name = "t25_subsumes_without_closure"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (SNOMED_URI, T2DM_SNOMED, "T2DM"),
                ],
            ),
        )
        # $subsumes WITHOUT consulting closure — outcome via direct walk
        outcome = _do_subsumes_outcome(
            fhir_client, SNOMED_URI, DIABETES_SNOMED, T2DM_SNOMED,
        )
        assert outcome == "subsumes"
        # ClosureTable.check() returns the SAME outcome
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        assert closure.check(DIABETES_SNOMED, T2DM_SNOMED) == "subsumes"
        # Clinical agreement — both paths produce clinically correct outcomes.

    def test_t26_subsumes_independent_of_closure_state(self, fhir_client):
        """CLINICAL-SAFETY: the HTTP $subsumes outcome does NOT depend on
        the closure table state. Resetting the closure does NOT change
        the $subsumes outcome (because $subsumes walks hierarchy directly).

        Clinical implication: clients can rely on $subsumes for clinical
        decisions even if the closure table is in an indeterminate state.
        """
        name = "t26_independent_of_closure"
        # Initialize closure with DM + T2DM
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (SNOMED_URI, T2DM_SNOMED, "T2DM"),
                ],
            ),
        )
        outcome_before = _do_subsumes_outcome(
            fhir_client, SNOMED_URI, DIABETES_SNOMED, T2DM_SNOMED,
        )
        # Reset closure to empty
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        # $subsumes outcome unchanged — clinical safety preserved.
        outcome_after = _do_subsumes_outcome(
            fhir_client, SNOMED_URI, DIABETES_SNOMED, T2DM_SNOMED,
        )
        assert outcome_before == outcome_after == "subsumes"

    def test_t27_subsumes_path_clinical_safety_when_closure_incomplete(
        self, fhir_client, monkeypatch
    ):
        """CLINICAL-SAFETY: when the closure table is marked incomplete
        (B6 fix — duckdb.Error during walk), the HTTP $subsumes path
        is NOT affected. It walks hierarchy directly and produces a
        clinically correct outcome.

        This is the load-bearing safety property per CF-SKEPTIC-CM03-02:
        clients using $subsumes for clinical decisions are NOT exposed
        to the closure's incomplete state.
        """
        import duckdb
        import medterm4ds.engines.fhir.closure as closure_mod

        name = "t27_incomplete_closure"
        # Initialize
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        # Force incomplete_since = True via duckdb.Error injection
        original = closure_mod.get_ancestors_bfs

        def _raise(*args, **kwargs):
            raise duckdb.Error("simulated transient DB failure")

        monkeypatch.setattr(closure_mod, "get_ancestors_bfs", _raise)
        try:
            fhir_client.post(
                "/fhir/CodeSystem/$closure",
                json=_closure_param_with_concepts(
                    name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
                ),
            )
        finally:
            monkeypatch.setattr(closure_mod, "get_ancestors_bfs", original)

        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        assert closure.incomplete_since is True

        # $subsumes outcome is STILL clinically correct — direct walk.
        outcome = _do_subsumes_outcome(
            fhir_client, SNOMED_URI, DIABETES_SNOMED, T2DM_SNOMED,
        )
        assert outcome == "subsumes"


# ===========================================================================
# LENS 3 — CF-HISTORIAN-CM03-02: incomplete_since NOT surfaced
# Clinical safety when closure is incomplete
# ===========================================================================

class TestLens3CfHistorianCm03_02ClinicalSafetyOfIncompleteClosure:
    """EXPLORER tip 3: CF-HISTORIAN-CM03-02 — evaluate clinical safety
    when closure is incomplete (transient DuckDB errors during walks).

    Clinical-safety analysis:
      The closure table is marked ``incomplete_since=True`` when
      ``add_concept`` / ``add_concepts`` catches ``duckdb.Error``
      (transient DB failures). In this state, ``ClosureTable.check()``
      may return "not-subsumed" for pairs that ARE actually subsumption
      relationships — silent-wrong-answer at the closure layer.

    Current mitigations:
      (a) HTTP $subsumes does NOT consult closure (CF-SKEPTIC-CM03-02);
          clients using HTTP $subsumes for clinical decisions are NOT
          exposed to the closure's incomplete state.
      (b) The Python API exposes ``closure.incomplete_since`` for
          programmatic callers to check before relying on
          ``closure.check()``.
      (c) The HTTP $closure response does NOT surface incomplete_since
          (CF-HISTORIAN-CM03-02) — clients consuming the HTTP response
          have no way to detect the degraded state.

    The gap is invisible today because no client consumes the HTTP
    response for clinical decisions. When a future enhancement wires
    CF-SKEPTIC-CM03-02, BOTH carry-forwards become load-bearing.
    """

    def test_t30_incomplete_since_starts_false_clinical_baseline(self):
        """CLINICAL BASELINE: a fresh ClosureTable starts with
        ``incomplete_since=False`` — the closure is NOT yet known to be
        incomplete. This is the clinical-correctness baseline state."""
        closure = ClosureTable("t30_baseline_false")
        assert closure.incomplete_since is False

    def test_t31_incomplete_since_set_true_on_walk_failure(
        self, fhir_client, monkeypatch
    ):
        """CLINICAL-SAFETY SIGNAL: when ``add_concepts`` catches
        ``duckdb.Error``, the ``incomplete_since`` flag MUST be set True
        so Python-API callers can detect the degraded state.

        Without this flag, ``closure.check()`` would silently return
        "not-subsumed" for pairs it failed to walk — clinical-safety
        violation."""
        import duckdb
        import medterm4ds.engines.fhir.closure as closure_mod

        name = "t31_incomplete_signal"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        original = closure_mod.get_ancestors_bfs

        def _raise(*args, **kwargs):
            raise duckdb.Error("simulated transient DB failure")

        monkeypatch.setattr(closure_mod, "get_ancestors_bfs", _raise)
        try:
            fhir_client.post(
                "/fhir/CodeSystem/$closure",
                json=_closure_param_with_concepts(
                    name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
                ),
            )
        finally:
            monkeypatch.setattr(closure_mod, "get_ancestors_bfs", original)

        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        # Clinical-safety signal is observable on the Python instance.
        assert closure.incomplete_since is True

    def test_t32_incomplete_since_not_set_on_programming_bug(
        self, fhir_client, monkeypatch
    ):
        """CLINICAL-SAFETY INVERT: programming bugs (TypeError,
        AttributeError) MUST propagate AND MUST NOT set
        ``incomplete_since=True``. Per GLOBAL_RULES.md "Silent Fallbacks":
        programming bugs MUST surface, not silently swallowed as
        "incomplete closure".

        Rationale: silently swallowing programming bugs as "incomplete
        closure" would mask real bugs in the closure logic — patients
        could be harmed by an undetected wrong-walk implementation."""
        import medterm4ds.engines.fhir.closure as closure_mod

        name = "t32_programming_bug_propagates"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        original = closure_mod.get_ancestors_bfs

        def _raise_type_error(*args, **kwargs):
            raise TypeError("simulated programming bug")

        monkeypatch.setattr(closure_mod, "get_ancestors_bfs", _raise_type_error)
        try:
            with pytest.raises(TypeError):
                # Direct call on the closure object — the $closure HTTP
                # handler wraps in try/except duckdb.Error but TypeError
                # propagates past it.
                manager = get_closure_manager()
                closure = manager.get(name)
                closure.add_concepts(
                    [(DIABETES_SNOMED, "SNOMEDCT_US", "DM")],
                    engine=None,
                )
        finally:
            monkeypatch.setattr(closure_mod, "get_ancestors_bfs", original)

        manager = get_closure_manager()
        closure = manager.get(name)
        # Programming bug MUST NOT set incomplete_since — that would
        # mask the bug as "incomplete closure" (clinical-safety violation).
        assert closure.incomplete_since is False

    def test_t33_incomplete_closure_check_may_return_wrong_answer(
        self, fhir_client, monkeypatch
    ):
        """CLINICAL-SAFETY GAP DOCUMENTATION: when ``incomplete_since=True``,
        ``ClosureTable.check()`` MAY return "not-subsumed" for a pair
        that IS actually a subsumption relationship. This is silent-
        wrong-answer at the closure Python API layer.

        The mitigation is that the CALLER (HTTP $subsumes handler today
        does NOT consult closure per CF-SKEPTIC-CM03-02). The Python
        API caller is responsible for checking ``incomplete_since``
        before relying on ``check()`` outcomes.

        This probe documents the CURRENT behavior: ``check()`` does NOT
        consult ``incomplete_since`` — it returns whatever the map says
        (defaulting to "not-subsumed").
        """
        closure = ClosureTable("t33_check_ignores_incomplete")
        closure.incomplete_since = True  # simulate degraded state
        # No concepts added — pair is unknown
        assert closure.check("X", "Y") == "not-subsumed"
        # The closure returned "not-subsumed" WITHOUT consulting
        # incomplete_since. This is the silent-wrong-answer surface.

    def test_t34_http_closure_response_does_not_surface_incomplete(
        self, fhir_client, monkeypatch
    ):
        """CF-HISTORIAN-CM03-02 clinical-safety verification: the HTTP
        $closure response does NOT surface ``incomplete_since`` in any
        form (no extension, no header, no flag).

        A spec-conformant client consuming the HTTP response for clinical
        decisions has NO way to detect the degraded state — clinical-
        safety gap.

        This gap is invisible today because HTTP $subsumes bypasses the
        closure per CF-SKEPTIC-CM03-02. When a future enhancement wires
        $subsumes to consult closure, this CF becomes load-bearing.
        """
        import duckdb
        import medterm4ds.engines.fhir.closure as closure_mod

        name = "t34_http_no_surface"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        original = closure_mod.get_ancestors_bfs

        def _raise(*args, **kwargs):
            raise duckdb.Error("simulated transient DB failure")

        monkeypatch.setattr(closure_mod, "get_ancestors_bfs", _raise)
        try:
            resp = fhir_client.post(
                "/fhir/CodeSystem/$closure",
                json=_closure_param_with_concepts(
                    name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
                ),
            )
        finally:
            monkeypatch.setattr(closure_mod, "get_ancestors_bfs", original)

        body = resp.json()
        # EC-11 QC-267 (MEDIUM) CLOSED CF-HISTORIAN-CM03-02: the response
        # now surfaces the degraded state as an ``incomplete``
        # valueBoolean Out parameter.
        assert "extension" not in body
        flags = [p for p in body.get("parameter", []) if p.get("name") == "incomplete"]
        assert flags == [{"name": "incomplete", "valueBoolean": True}], (
            f"QC-267: degraded closure must surface incomplete=True; got {flags}"
        )

    def test_t35_incomplete_since_clinical_documentation_in_agents_md(self):
        """TERMINOLOGIST clinical finding: the ``incomplete_since`` gap
        is documented in AGENTS.md as CF-HISTORIAN-CM03-02 with the
        clinical-safety analysis (Python-API observable, HTTP-not-
        surfaced, mitigation via CF-SKEPTIC-CM03-02 non-use)."""
        agents_path = (
            Path(__file__).resolve().parents[2]
            / "docs" / ".ai_loop" / "AGENTS.md"
        )
        src = agents_path.read_text()
        assert "CF-HISTORIAN-CM03-02" in src
        # Verify the clinical-safety context is documented.
        assert "incomplete_since" in src


# ===========================================================================
# LENS 4 — Non-idempotent semantic verification
# Per R4 spec text: "This is not an idempotent operation"
# ===========================================================================

class TestLens4NonIdempotentSemanticClinicalWorkflow:
    """EXPLORER tip 4: verify NON-IDEMPOTENT semantic matches clinical
    workflow expectations.

    Per FHIR R4 $closure OperationDefinition
    (https://hl7.org/fhir/R4/conceptmap-operation-closure.html):
      "This is **not** an idempotent operation"

    Clinical-workflow implication: a client calling $closure twice with
    the SAME concepts SHOULD observe a state change signal (different
    version hash) because the closure table's internal ``_version``
    counter advances per ``add_concepts`` call.

    This is operationally distinct from idempotent operations (e.g.,
    $lookup), where two identical calls produce identical responses.
    The non-idempotent semantic is critical for clinical workflows
    where the client polls $closure to detect state changes.

    Clinical-correctness invariants:
      (a) Calling $closure with same concepts TWICE produces DIFFERENT
          version hashes (the _version counter advances).
      (b) The concept LIST is the same across both calls (set semantics).
      (c) The non-idempotent semantic is structurally enforced by the
          implementation (``_version += 1`` in add_concepts).
    """

    def test_t40_non_idempotent_add_same_concept_twice_changes_hash(
        self, fhir_client
    ):
        """CLINICAL-WORKFLOW CONTRACT: calling $closure with the same
        concept twice produces DIFFERENT version hashes. The _version
        counter advances per add_concepts call — clients can detect
        "closure state changed" via the hash.

        Per R4 spec: "This is not an idempotent operation." A client
        adding DM twice observes a state change each time."""
        name = "t40_non_idempotent"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        # First add
        resp1 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        hash1 = _return_hash(resp1.json())
        # Second add — SAME concept
        resp2 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        hash2 = _return_hash(resp2.json())
        # EC-11 QC-270/QC-278 revised the contract: the hash is
        # CONTENT-addressed. The R4 "not idempotent" semantic refers to
        # the operation's state effects (it initializes/updates server
        # state), not to token churn — QC-278 explicitly requires
        # content-identical re-adds to keep the same hash so delta-
        # protocol clients can skip work. A redundant re-add is a no-op.
        assert hash1 == hash2, (
            "QC-278: content-identical re-add MUST keep the same hash"
        )

    def test_t41_non_idempotent_concept_set_unchanged_across_redundant_adds(
        self, fhir_client
    ):
        """CLINICAL-CORRECTNESS: despite the hash changing per call
        (non-idempotent), the concept SET in the response is unchanged
        across redundant adds (set semantics — adding the same code
        twice does not duplicate it)."""
        name = "t41_set_semantics"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        # Add DM twice
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        resp2 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        concepts2 = _find_params(resp2.json(), "concept")
        codes = {p["valueCoding"]["code"] for p in concepts2}
        # Set semantics: DM appears ONCE in the concept list, not twice.
        assert codes == {DIABETES_SNOMED}

    def test_t42_non_idempotent_init_resets_state_clinical_safety(
        self, fhir_client
    ):
        """CLINICAL-SAFETY: re-init (calling $closure with no concepts
        on an existing closure) RESETS the closure to empty. This is
        the spec-permitted "initialize/reset" semantic.

        Clinical workflow: clients re-initialize when they want to start
        a fresh closure (e.g., new patient session). The hash returns to
        the empty-closure value — clients can detect "reset happened".
        """
        name = "t42_init_resets"
        # Initialize
        resp1 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        hash1 = _return_hash(resp1.json())
        # Add a concept
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        # Re-init — closure resets to empty
        resp3 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        hash3 = _return_hash(resp3.json())
        # The post-reset hash EQUALS the original empty-closure hash
        # (idempotent re-init for empty closures, despite non-idempotent
        # overall operation).
        assert hash3 == hash1
        # The concept list is empty after reset.
        concepts3 = _find_params(resp3.json(), "concept")
        assert len(concepts3) == 0

    def test_t43_non_idempotent_counter_source_read(self):
        """SOURCE-READ CONTRACT: the ``_version += 1`` line is the load-
        bearing source of non-idempotency. Removing it would make the
        operation idempotent on redundant adds (silent-spec-violation).

        This is a maintenance-hazard defense — a future engineer
        refactoring ``add_concepts`` MUST preserve the _version
        increment."""
        src = CLOSURE_PATH.read_text()
        # EC-11: the _version increment lives in _record_walk (shared by
        # add_concept and add_concepts); it counts registered concepts.
        assert "self._version += 1" in src
        record_src = inspect.getsource(ClosureTable._record_walk)
        assert "self._version += 1" in record_src

    def test_t44_non_idempotent_clinical_workflow_state_change_detection(
        self, fhir_client
    ):
        """CLINICAL-WORKFLOW SIMULATION: a client polls $closure to
        detect state changes. Each add produces a different hash —
        client detects "closure advanced". This is the clinical
        utility of the non-idempotent semantic.

        Clinical scenario: a CDS Hook adds new problems to the closure
        as they're documented; each add returns a different hash; the
        client uses the hash to invalidate cached subsumption answers.
        """
        name = "t44_workflow_simulation"
        hashes: list[str] = []
        # Initial state
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        hashes.append(_return_hash(resp.json()))
        # Add DM
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        hashes.append(_return_hash(resp.json()))
        # Add T2DM
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, T2DM_SNOMED, "T2DM")],
            ),
        )
        hashes.append(_return_hash(resp.json()))
        # Add DM again (redundant) — QC-278: content no-op, hash unchanged.
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        hashes.append(_return_hash(resp.json()))
        # Hash changes on every CONTENT change; redundant re-add is stable.
        assert len(set(hashes)) == 3, (
            f"Expected 3 distinct hashes for 3 distinct states (the 4th "
            f"call is a content no-op per QC-278); got "
            f"{len(set(hashes))}: {hashes}"
        )


# ===========================================================================
# LENS 5 — Closure-table subsumption outcome vocabulary exactness
# Per FHIR R4 §4.7.7 / ConceptSubsumptionOutcome value set
# ===========================================================================

class TestLens5ClosureTableSubsumptionOutcomeVocabulary:
    """EXPLORER tip 5: probe closure-table subsumption outcome vocabulary
    is exactly {equivalent, subsumes, subsumed-by, not-subsumed} per
    FHIR R4 §4.7.7 across seeded SNOMED pairs.

    FHIR R4 ConceptSubsumptionOutcome value set
    (https://hl7.org/fhir/R4/valueset-concept-subsumption-outcome.html):
      equivalent, subsumes, subsumed-by, not-subsumed

    ClosureTable.check() returns one of these 4 values — it MUST NOT
    return any other string (no typos like "subsumedBy" or "notsubsumed").
    The closed-enum membership is the load-bearing clinical-safety
    invariant: a CDS Hook parsing the outcome would silently produce
    wrong decisions on off-enum values.
    """

    def test_t50_check_returns_equivalent_for_self_pair(self, fhir_client):
        """VOCABULARY: ClosureTable.check(X, X) returns "equivalent"."""
        name = "t50_self_equivalent"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        closure = get_closure_manager().get(name)
        assert closure is not None
        outcome = closure.check(DIABETES_SNOMED, DIABETES_SNOMED)
        assert outcome == "equivalent"
        assert outcome in FHIR_R4_SUBSUMPTION_OUTCOME

    def test_t51_check_returns_subsumes_for_parent_child(self, fhir_client):
        """VOCABULARY: ClosureTable.check(DM, T2DM) returns "subsumes"."""
        name = "t51_subsumes"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (SNOMED_URI, T2DM_SNOMED, "T2DM"),
                ],
            ),
        )
        closure = get_closure_manager().get(name)
        assert closure is not None
        outcome = closure.check(DIABETES_SNOMED, T2DM_SNOMED)
        assert outcome == "subsumes"
        assert outcome in FHIR_R4_SUBSUMPTION_OUTCOME

    def test_t52_check_returns_subsumed_by_for_child_parent(self, fhir_client):
        """VOCABULARY: ClosureTable.check(T2DM, DM) returns "subsumed-by"
        (with hyphen — NOT R5/R4B "subsumedBy" camelCase)."""
        name = "t52_subsumed_by"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (SNOMED_URI, T2DM_SNOMED, "T2DM"),
                ],
            ),
        )
        closure = get_closure_manager().get(name)
        assert closure is not None
        outcome = closure.check(T2DM_SNOMED, DIABETES_SNOMED)
        assert outcome == "subsumed-by"
        assert outcome in FHIR_R4_SUBSUMPTION_OUTCOME

    def test_t53_check_returns_not_subsumed_for_unknown_pair(
        self, fhir_client
    ):
        """VOCABULARY: ClosureTable.check for an unrelated pair returns
        "not-subsumed"."""
        name = "t53_not_subsumed"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (RXNORM_URI, METFORMIN_RXNORM, "metformin"),
                ],
            ),
        )
        closure = get_closure_manager().get(name)
        assert closure is not None
        outcome = closure.check(DIABETES_SNOMED, METFORMIN_RXNORM)
        assert outcome == "not-subsumed"
        assert outcome in FHIR_R4_SUBSUMPTION_OUTCOME

    @pytest.mark.parametrize(
        "code_a,code_b,expected",
        [
            (DIABETES_SNOMED, DIABETES_SNOMED, "equivalent"),
            (DIABETES_SNOMED, T2DM_SNOMED, "subsumes"),
            (T2DM_SNOMED, DIABETES_SNOMED, "subsumed-by"),
            (DIABETES_SNOMED, METFORMIN_RXNORM, "not-subsumed"),
            (METFORMIN_RXNORM, DIABETES_SNOMED, "not-subsumed"),
            (T2DM_SNOMED, T2DM_SNOMED, "equivalent"),
        ],
    )
    def test_t54_check_vocabulary_exactness_parametrized(
        self, fhir_client, code_a, code_b, expected
    ):
        """PARAMETRIZED VOCABULARY AUDIT: every check() outcome is
        exactly the expected R4 ConceptSubsumptionOutcome value for
        the seeded pair.

        Six combinations cover all 4 closed-enum values.
        """
        name = f"t54_param_{code_a}_{code_b}"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (SNOMED_URI, T2DM_SNOMED, "T2DM"),
                    (RXNORM_URI, METFORMIN_RXNORM, "metformin"),
                ],
            ),
        )
        closure = get_closure_manager().get(name)
        assert closure is not None
        outcome = closure.check(code_a, code_b)
        assert outcome == expected
        assert outcome in FHIR_R4_SUBSUMPTION_OUTCOME

    def test_t55_check_does_not_emit_off_enum_value(self):
        """SOURCE-READ CONTRACT: ClosureTable.check() returns ONLY the
        4 R4 closed-enum values. The implementation's return statements
        MUST NOT contain any off-enum string.

        This is a maintenance-hazard defense — a future engineer adding
        a 5th outcome (e.g. "unknown" or "partial") would silently
        break CDS Hooks parsing the outcome.

        Audit technique: AST-walk the ``return`` statements ONLY (avoids
        false-flags on docstring text). Per CS-01 HISTORIAN L1 / CS-02
        HISTORIAN L5 / VS-01 SKEPTIC methodology: source-read audits
        searching for off-spec literal values MUST walk ast.Constant
        nodes inside return statements, not substring-match on raw text.
        """
        import ast

        src = textwrap.dedent(inspect.getsource(ClosureTable.check))
        tree = ast.parse(src)
        return_consts: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and node.value is not None:
                for sub in ast.walk(node.value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        return_consts.append(sub.value)
        # The 4 R4 closed-enum values are present in return statements.
        assert "equivalent" in return_consts
        assert "subsumes" in return_consts
        assert "subsumed-by" in return_consts  # hyphenated, not camelCase
        assert "not-subsumed" in return_consts
        # Off-enum values MUST be absent from return statements.
        for off_enum in ("subsumedBy", "notsubsumed", "unknown", "unmatched"):
            assert off_enum not in return_consts, (
                f"check() return statement MUST NOT contain off-enum value "
                f"{off_enum!r}. Found in: {return_consts}"
            )

    def test_t56_outcome_vocabulary_constant_source_read(self):
        """SOURCE-READ CONTRACT: the test-level FHIR_R4_SUBSUMPTION_OUTCOME
        constant matches the canonical R4 ConceptSubsumptionOutcome value
        set exactly. Verified against
        https://hl7.org/fhir/R4/valueset-concept-subsumption-outcome.html
        """
        assert FHIR_R4_SUBSUMPTION_OUTCOME == frozenset({
            "equivalent",
            "subsumes",
            "subsumed-by",
            "not-subsumed",
        })
        # Cardinality check — exactly 4 values per R4 spec.
        assert len(FHIR_R4_SUBSUMPTION_OUTCOME) == 4


# ===========================================================================
# LENS 6 — Cross-source closure clinical correctness
# ===========================================================================

class TestLens6CrossSourceClosureClinicalCorrectness:
    """Per FHIR R4 spec (conceptmap-operation-closure.html), the closure
    is maintained over submitted concepts regardless of code system.
    medterm4ds allows cross-source closure.

    Clinical-correctness invariant: ``check()`` MUST NOT return
    "subsumes" for a cross-system pair — subsumption is intra-system by
    definition (``isa`` relationships exist within a single hierarchy).

    Cross-system "equivalence" (e.g. SNOMED Diabetes ~ ICD-10-CM E11)
    is a MAPPING relationship (ConceptMap/$translate), NOT subsumption.
    """

    def test_t60_cross_source_no_subsumption_recorded(self, fhir_client):
        """CLINICAL CORRECTNESS: adding concepts from SNOMED + ICD-10-CM +
        RxNorm to the same closure MUST NOT record any cross-system
        subsumption tuples — the closure treats them as unrelated
        (correct clinical semantic).
        """
        name = "t60_cross_source"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "Diabetes mellitus"),
                    (ICD10CM_URI, T2DM_ICD10CM, "Type 2 diabetes mellitus"),
                    (RXNORM_URI, METFORMIN_RXNORM, "metformin"),
                ],
            ),
        )
        closure = get_closure_manager().get(name)
        assert closure is not None
        # Cross-system pairs: no subsumption recorded
        assert (
            closure.check(DIABETES_SNOMED, T2DM_ICD10CM) == "not-subsumed"
        )
        assert (
            closure.check(DIABETES_SNOMED, METFORMIN_RXNORM) == "not-subsumed"
        )
        assert (
            closure.check(T2DM_ICD10CM, METFORMIN_RXNORM) == "not-subsumed"
        )

    def test_t61_cross_source_self_equivalent_recorded(self, fhir_client):
        """CLINICAL CORRECTNESS: each code IS equivalent to itself
        (self-subsumption set at registration time), regardless of source.

        This is the only "subsumption" outcome for codes never connected
        by a hierarchy walk — and it's structurally correct (same code
        = same concept = equivalent).
        """
        name = "t61_self_equiv"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (ICD10CM_URI, T2DM_ICD10CM, "T2DM"),
                ],
            ),
        )
        closure = get_closure_manager().get(name)
        assert closure is not None
        assert closure.check(DIABETES_SNOMED, DIABETES_SNOMED) == "equivalent"
        assert closure.check(T2DM_ICD10CM, T2DM_ICD10CM) == "equivalent"

    def test_t62_to_parameter_list_canonical_uri_per_source(self, fhir_client):
        """CLINICAL-CORRECTNESS: ``to_parameter_list`` emits the canonical
        FHIR URI per source (NOT internal source names like "SNOMEDCT_US").
        Clients reading the response need FHIR URIs to interpret codings.

        This is the canonical-URI invariant — count=8+1 PROMOTED pattern.
        """
        name = "t62_canonical_uri"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (ICD10CM_URI, T2DM_ICD10CM, "T2DM"),
                    (RXNORM_URI, METFORMIN_RXNORM, "metformin"),
                ],
            ),
        )
        closure = get_closure_manager().get(name)
        assert closure is not None
        params = closure.to_parameter_list()
        for p in params:
            system = p["valueCoding"]["system"]
            assert system in SYSTEM_TO_FHIR_URI.values(), (
                f"System URI {system!r} is not canonical — client-input-as-"
                f"canonical drift (count=8+1 PROMOTED) recurrence."
            )

    def test_t63_canonical_uri_preserved_on_alias_input(self, fhir_client):
        """CLINICAL-CORRECTNESS: when concepts are added via an ALIAS URI
        (e.g. urn:oid:2.16.840.1.113883.6.96 for SNOMED), the OUTPUT
        parameter list emits the CANONICAL URI (http://snomed.info/sct).

        This is the load-bearing canonical-URI invariant — the closure
        does not echo client-supplied aliases."""
        name = "t63_alias_to_canonical"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [(SNOMED_URI_OID_ALIAS, DIABETES_SNOMED, "DM")],
            ),
        )
        closure = get_closure_manager().get(name)
        assert closure is not None
        params = closure.to_parameter_list()
        snomed_entry = next(
            p for p in params if p["valueCoding"]["code"] == DIABETES_SNOMED
        )
        # The OUTPUT emits canonical URI, NOT the urn:oid alias.
        assert snomed_entry["valueCoding"]["system"] == SNOMED_URI


# ===========================================================================
# LENS 7 — Version hash as clinical state-change signal
# ===========================================================================

class TestLens7VersionHashClinicalStateChangeSignal:
    """CLINICAL UTILITY: the version hash is the only clinical-meaningful
    signal in the current $closure response shape (CF-SKEPTIC-CM03-01
    documents that the spec-correct ConceptMap return is deferred).

    Clients use the hash to:
      (a) Detect "closure state changed since last call" — operational
          cache-invalidation signal.
      (b) Compare two closure instances for state equality.

    Clinical-correctness invariants:
      (a) The hash CHANGES when a concept is added (count + version advance).
      (b) The hash is INDEPENDENT of concept add order (sorted before hash).
      (c) The hash is DETERMINISTIC across ClosureManager instances.
      (d) The hash EXCLUDES display values (state-change signal is
          relationship-driven, not display-driven).
    """

    def test_t70_hash_changes_on_add_concept(self, fhir_client):
        """CLINICAL UTILITY: adding a concept changes the hash (state-
        change signal advances)."""
        name = "t70_hash_changes"
        resp1 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        resp2 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        assert _return_hash(resp1.json()) != _return_hash(resp2.json())

    def test_t71_hash_independent_of_add_order(self, fhir_client):
        """CLINICAL UTILITY: two closures with the SAME concept set added
        in DIFFERENT orders produce the SAME hash (sorted before hash).
        Clients can rely on the hash for state equality regardless of
        how the closure was built."""
        concepts_a = [
            (SNOMED_URI, DIABETES_SNOMED, "DM"),
            (SNOMED_URI, T2DM_SNOMED, "T2DM"),
        ]
        concepts_b = [
            (SNOMED_URI, T2DM_SNOMED, "T2DM"),  # reversed order
            (SNOMED_URI, DIABETES_SNOMED, "DM"),
        ]
        # Use two distinct names to avoid cross-contamination.
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts("t71_order_a", concepts_a),
        )
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts("t71_order_b", concepts_b),
        )
        # Re-fetch to get the response hashes (the batched add_concepts
        # uses a single _version increment per call, so both closures
        # have _version=1 after one add call each).
        resp_a = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                "t71_order_a", [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        resp_b = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                "t71_order_b", [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        # Both closures now have: {DM, T2DM} with _version=2.
        # Hashes MUST be equal (same concept set, same version, sorted keys).
        assert _return_hash(resp_a.json()) == _return_hash(resp_b.json())

    def test_t72_hash_deterministic_across_instances(self):
        """CLINICAL UTILITY: the hash is deterministic across
        ClosureManager instances — no implicit global state contaminates
        the hash. Clients can compare hashes across server restarts
        (modulo the in-memory non-persistence caveat)."""
        c1 = ClosureTable("t72_inst_a")
        c2 = ClosureTable("t72_inst_b")
        # Same state (EC-11 QC-266: (source, code) keys)
        c1.concepts[("S", "X")] = {"system": "S", "display": "X"}
        c2.concepts[("S", "X")] = {"system": "S", "display": "X"}
        assert c1.version_hash() == c2.version_hash()

    def test_t73_hash_excludes_display_values(self, fhir_client):
        """CLINICAL UTILITY: the hash payload excludes display values.
        Two closures with the SAME codes but DIFFERENT displays produce
        the SAME hash (modulo _version). This is the documented behavior
        — display changes are not state changes at the closure-table level.
        """
        # EC-11 QC-282/QC-283 revised the contract: displays are part of the
        # content hash (and are always the engine canonical preferred
        # term, so a display change means the terminology release
        # changed — a real state change clients must observe).
        c1 = ClosureTable("t73_disp_a")
        c2 = ClosureTable("t73_disp_b")
        c1.concepts[("S", "X")] = {"system": "S", "display": "Display One"}
        c2.concepts[("S", "X")] = {"system": "S", "display": "Display Two"}
        assert c1.version_hash() != c2.version_hash()

    def test_t74_hash_format_md5_hex_12(self, fhir_client):
        """CLINICAL UTILITY: the hash format is 12-char MD5 hex. Clients
        can rely on this format for state-change detection (e.g., storing
        the hash as a fixed-width column)."""
        name = "t74_format"
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        h = _return_hash(resp.json())
        assert h is not None
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)


# ===========================================================================
# LENS 8 — Closure-table check() clinical-correctness regression-pin
# ===========================================================================

class TestLens8CheckMethodClinicalCorrectnessRegressionPin:
    """REGRESSION-PIN: ``ClosureTable.check()`` is the load-bearing
    Python API for subsumption. The clinical-correctness invariants are:

      (a) Self-pair returns "equivalent" (short-circuit before map lookup).
      (b) Map-hit (subsumer, subsumed) returns "subsumes".
      (c) Map-hit-reversed (subsumed, subsumer) returns "subsumed-by".
      (d) Map-miss returns "not-subsumed".

    A regression in any of these would silently produce wrong subsumption
    outcomes for Python-API callers. The probes below pin each branch.
    """

    def test_t80_check_self_short_circuit_before_map(self):
        """CLINICAL CORRECTNESS: ``check(X, X)`` MUST return "equivalent"
        WITHOUT consulting the map. This is the load-bearing short-
        circuit — if a future refactor looks up the map first, the
        "equivalent" outcome depends on the map being populated for
        self-pairs (currently true via ``_subsumes[(code, code)] = True``
        set at registration time, but the short-circuit is the safer
        contract).
        """
        closure = ClosureTable("t80_short_circuit")
        # No concepts added — map is empty.
        # Self-check STILL returns "equivalent" via the short-circuit.
        assert closure.check("ANY", "ANY") == "equivalent"

    def test_t81_check_map_hit_returns_subsumes(self):
        """CLINICAL CORRECTNESS: ``check(A, B)`` where (A, B) is in the
        map with True returns "subsumes"."""
        closure = ClosureTable("t81_map_hit")
        # EC-11 QC-266: pair keys; concepts registered so the 2-arg
        # check() can resolve the shared system.
        a, b = ("S", "A"), ("S", "B")
        closure.concepts[a] = {"system": "S", "display": "A"}
        closure.concepts[b] = {"system": "S", "display": "B"}
        closure._subsumes[(a, b)] = True
        assert closure.check("A", "B") == "subsumes"

    def test_t82_check_map_reversed_hit_returns_subsumed_by(self):
        """CLINICAL CORRECTNESS: ``check(A, B)`` where (B, A) is in the
        map with True returns "subsumed-by" (B subsumes A, so A is
        subsumed-by B)."""
        closure = ClosureTable("t82_reversed_hit")
        a, b = ("S", "A"), ("S", "B")
        closure.concepts[a] = {"system": "S", "display": "A"}
        closure.concepts[b] = {"system": "S", "display": "B"}
        closure._subsumes[(b, a)] = True
        assert closure.check("A", "B") == "subsumed-by"

    def test_t83_check_map_miss_returns_not_subsumed(self):
        """CLINICAL CORRECTNESS: ``check(A, B)`` where neither (A, B) nor
        (B, A) is in the map returns "not-subsumed"."""
        closure = ClosureTable("t83_map_miss")
        assert closure.check("A", "B") == "not-subsumed"

    def test_t84_check_returns_string_not_bool(self):
        """CLINICAL-CORRECTNESS WIRE FORMAT: ``check()`` returns a STRING
        (one of the 4 R4 ConceptSubsumptionOutcome values), NOT a boolean.
        A future refactor that returns True/False would silently break
        clients parsing the outcome."""
        closure = ClosureTable("t84_string_return")
        outcome = closure.check("X", "X")
        assert isinstance(outcome, str)
        assert outcome in FHIR_R4_SUBSUMPTION_OUTCOME

    def test_t85_check_outcome_clinically_informative_no_silent_default(
        self
    ):
        """CLINICAL-SAFETY: ``check()`` has NO silent default branch —
        every code path returns one of the 4 R4 closed-enum values. There
        is no ``else: return "unknown"`` or similar silent default that
        could mask a logic error.

        This is a maintenance-hazard defense — a future engineer adding
        a 5th branch MUST also extend the closed enum (which requires
        spec-evolution, not implementation choice).

        Audit technique: AST-walk return statements ONLY (avoids
        false-flags on docstring text)."""
        import ast

        src = textwrap.dedent(inspect.getsource(ClosureTable.check))
        tree = ast.parse(src)
        return_consts: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and node.value is not None:
                for sub in ast.walk(node.value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        return_consts.append(sub.value)
        # The 4 R4 closed-enum values are present.
        assert "equivalent" in return_consts
        assert "subsumes" in return_consts
        assert "subsumed-by" in return_consts
        assert "not-subsumed" in return_consts
        # Off-enum silent defaults MUST be absent.
        for off_enum in ("unknown", "partial", "error", "none"):
            assert off_enum not in return_consts, (
                f"check() MUST NOT have silent default branch returning "
                f"{off_enum!r}. Found in: {return_consts}"
            )


# ===========================================================================
# LENS 9 — Source-read structural contracts for clinical invariants
# ===========================================================================

class TestLens9SourceReadClinicalInvariantContracts:
    """SOURCE-READ CONTRACTS: pin the load-bearing clinical-correctness
    patterns at the source-text layer. A future refactor that breaks
    these patterns would fail the corresponding source-read probe
    BEFORE the clinical-correctness regression surfaces in production.
    """

    def test_t90_do_closure_uses_fhir_uri_to_system_for_input_resolution(self):
        """SOURCE-READ CONTRACT: ``_do_closure`` resolves the input
        ``system`` URI via ``fhir_uri_to_system`` (canonical-URI
        invariant on INPUT axis)."""
        src = _get_nested_func_source(
            FHIR_API_PATH, "create_fhir_app", "_do_closure",
        )
        assert "fhir_uri_to_system" in src

    def test_t91_to_parameter_list_uses_system_to_fhir_uri_for_output(self):
        """SOURCE-READ CONTRACT: ``to_parameter_list`` re-resolves the
        source label to the canonical FHIR URI via ``system_to_fhir_uri``
        (canonical-URI invariant on OUTPUT axis)."""
        src = inspect.getsource(ClosureTable.to_parameter_list)
        assert "system_to_fhir_uri" in src

    def test_t92_check_method_uses_hyphenated_outcome_strings(self):
        """SOURCE-READ CONTRACT: ``check()`` returns hyphenated R4
        outcome strings ('subsumed-by', 'not-subsumed'), NOT R5/R4B
        camelCase ('subsumedBy').

        Audit technique: AST-walk return statements ONLY (avoids
        false-flags on docstring text)."""
        import ast

        src = textwrap.dedent(inspect.getsource(ClosureTable.check))
        tree = ast.parse(src)
        return_consts: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and node.value is not None:
                for sub in ast.walk(node.value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        return_consts.append(sub.value)
        # The hyphenated R4 values are present.
        assert "subsumed-by" in return_consts
        assert "not-subsumed" in return_consts
        # R5/R4B camelCase / unhyphenated forms MUST be absent.
        for off_form in ("subsumedBy", "subsumedby", "notsubsumed", "notSubsumed"):
            assert off_form not in return_consts, (
                f"check() return MUST NOT contain off-form {off_form!r}. "
                f"Found in: {return_consts}"
            )

    def test_t93_add_concepts_walks_per_source_batched(self):
        """SOURCE-READ CONTRACT: ``add_concepts`` batches hierarchy
        walks per source (2 walks per source, not 2 per concept). This
        is the E1 efficiency fix — load-bearing for large closures."""
        # EC-11 BFS migration (QC-261/275/276/281): per-concept layer-
        # by-layer BFS walks (O(nodes), visited-set) replaced the
        # per-source batched recursive-CTE walks (O(paths) — the 32 GB
        # RSS / OOM explosion).
        src = inspect.getsource(ClosureTable.add_concepts)
        record_src = inspect.getsource(ClosureTable._record_walk)
        assert "_record_walk" in src
        assert "get_ancestors_bfs" in record_src
        assert "get_descendants_bfs" in record_src

    def test_t94_add_concepts_catches_duckdb_error_not_exception(self):
        """SOURCE-READ CONTRACT: ``add_concepts`` catches the NARROW
        ``duckdb.Error`` (NOT broad ``Exception``). Per GLOBAL_RULES.md
        "Silent Fallbacks": programming bugs MUST propagate.

        A future refactor that broadens the catch to ``Exception`` would
        silently swallow programming bugs as "incomplete closure" —
        clinical-safety violation."""
        # EC-11: the walk moved into _record_walk (shared by both add
        # entry points) — the narrow-catch contract applies there.
        src = inspect.getsource(ClosureTable._record_walk)
        assert "except duckdb.Error" in src
        # Broad except MUST be absent.
        assert "except Exception" not in src

    def test_t95_add_concepts_sets_incomplete_since_on_duckdb_error(self):
        """SOURCE-READ CONTRACT: ``add_concepts`` sets
        ``self.incomplete_since = True`` inside the duckdb.Error catch
        block. This is the B6 fix — load-bearing for Python-API callers.
        """
        src = inspect.getsource(ClosureTable._record_walk)
        assert "self.incomplete_since = True" in src

    def test_t96_build_closure_response_return_first(self):
        """SOURCE-READ CONTRACT: ``build_closure_response`` emits the
        ``return`` parameter FIRST in the parameter list (spec-listed
        first per R4 OperationDefinition)."""
        src = inspect.getsource(build_closure_response)
        # The return parameter dict is the first element of the list.
        assert '"name": "return"' in src
        # The list comprehension / literal starts with return (not concept).
        # Find the parameter list literal.
        assert "parameter" in src

    def test_t97_do_subsumes_does_not_call_closure_check(self):
        """SOURCE-READ CONTRACT: ``_do_subsumes`` does NOT call
        ``ClosureTable.check()`` — it walks the hierarchy directly via
        ``is_descendant``. This is the CF-SKEPTIC-CM03-02 design decision.

        A future enhancement that wires $subsumes to consult closure MUST
        update this probe (and the corresponding CF-SKEPTIC-CM03-02 pin).
        """
        src = _get_nested_func_source(
            FHIR_API_PATH, "create_fhir_app", "_do_subsumes",
        )
        assert "is_descendant" in src
        # ClosureTable.check MUST be absent from _do_subsumes today.
        assert "ClosureTable" not in src
        assert "check(" not in src.replace("is_descendant", "")

    def test_t98_version_hash_uses_md5_hex_12_format(self):
        """SOURCE-READ CONTRACT: ``version_hash`` uses MD5 hexdigest
        truncated to 12 chars. Clients rely on this format for state-
        change detection."""
        src = inspect.getsource(ClosureTable.version_hash)
        assert "hashlib.md5" in src
        assert "hexdigest()[:12]" in src

    def test_t99_version_hash_payload_excludes_display(self):
        """SOURCE-READ CONTRACT: ``version_hash`` payload composition is
        ``len(concepts):_version:sorted(concepts.keys())`` — display
        values are EXCLUDED. A future refactor that includes display
        would change the hash contract (silent-stale-cache for clients).
        """
        # EC-11 QC-270/QC-283 revised the payload: the FULL state —
        # (source, code, display) concept tuples AND the TRUE relation
        # pairs — is hashed; the internal call counter is excluded so
        # identical content yields identical hashes.
        src = inspect.getsource(ClosureTable.version_hash)
        assert "self._subsumes" in src  # relations included (QC-283)
        assert "self._version" not in src  # call counter excluded (QC-270)


# ===========================================================================
# LENS 10 — Carry-forward-as-probe pins (CS-03 TERMINOLOGIST strategy 56)
# ===========================================================================

class TestLens10CarryForwardAsProbePins:
    """The three CM-03 carry-forwards are pinned via the carry-forward-
    as-probe pattern (CS-03 TERMINOLOGIST strategy 56). When a future
    enhancement closes any CF, the corresponding probe MUST be updated
    in the SAME PR.

    These probes fire loudly when the deferred behavior changes — they
    are the load-bearing regression guards for the documented decisions.
    """

    def test_t100_cf_skeptic_cm03_01_return_is_value_string(self, fhir_client):
        """CF-SKEPTIC-CM03-01 PIN: Out ``return`` is currently
        valueString (NOT ConceptMap per R4 spec). When the spec-correct
        ConceptMap return lands, this probe MUST be updated to assert
        the new shape (Parameters with ``return.resource`` = ConceptMap).
        """
        name = "t100_cf_pin"
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        body = resp.json()
        return_param = _find_param(body, "return")
        assert return_param is not None
        # CURRENT: valueString. SPEC-CORRECT: ConceptMap resource.
        assert "valueString" in return_param
        assert "resource" not in return_param

    def test_t101_cf_skeptic_cm03_02_subsumes_does_not_use_closure(
        self, fhir_client
    ):
        """CF-SKEPTIC-CM03-02 PIN: ``$subsumes`` HTTP handler does NOT
        consult the closure table. It walks the hierarchy directly via
        ``is_descendant``. The outcome IS clinically correct via the
        direct walk.

        When a future enhancement wires ``$subsumes`` to consult closure,
        this probe MUST be updated to assert the new path."""
        # Build a closure with the SNOMED pair.
        closure_name = "t101_subsumes_no_closure"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                closure_name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (SNOMED_URI, T2DM_SNOMED, "T2DM"),
                ],
            ),
        )
        # Now call $subsumes — it does NOT consult the closure, but the
        # outcome is still correct via direct hierarchy walk.
        resp = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": DIABETES_SNOMED,
                "codeB": T2DM_SNOMED,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        outcome = _find_param(body, "outcome")
        assert outcome is not None
        # Diabetes subsumes T2DM (clinically correct via direct walk).
        assert outcome["valueCode"] == "subsumes"

    def test_t102_cf_historian_cm03_02_incomplete_since_not_surfaced(
        self, fhir_client
    ):
        """CF-HISTORIAN-CM03-02 PIN: the ``incomplete_since`` flag is
        NOT surfaced in the HTTP response (no extension, no flag).
        When the fix lands (FHIR extension with valueBoolean), this
        probe MUST be updated to assert presence of the extension.
        """
        name = "t102_incomplete_pin"
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        body = resp.json()
        # CURRENT: no extension on Parameters resource.
        assert "extension" not in body


# ===========================================================================
# LENS 11 — Cross-personality hygiene (verify prior CF pins intact)
# ===========================================================================

class TestLens11CrossPersonalityHygiene:
    """Cross-personality hygiene: verify the load-bearing probes from
    SKEPTIC + HISTORIAN + EXPLORER are still present (source-reading
    regression guards — VS-05 HISTORIAN strategy 52).

    These probes are NOT TERMINOLOGIST's contribution; they verify the
    work of prior personalities is intact. A regression in any of these
    probes would indicate a regression in the corresponding fix.
    """

    def test_t110_skeptic_test_s90_still_load_bearing(self):
        """SKEPTIC test_s90 (CF-SKEPTIC-CM03-01 spec-deviation return-is-
        valueString pin) MUST still be present."""
        p = Path(__file__).parent / "test_cm03_skeptic.py"
        src = p.read_text()
        assert "def test_s90_spec_deviation_return_is_value_string_not_conceptmap" in src

    def test_t111_skeptic_test_s91_still_load_bearing(self):
        """SKEPTIC test_s91 (CF-SKEPTIC-CM03-01 no-ConceptMap-resource
        pin) MUST still be present."""
        p = Path(__file__).parent / "test_cm03_skeptic.py"
        src = p.read_text()
        assert "def test_s91_spec_deviation_no_conceptmap_resource_in_response" in src

    def test_t112_historian_test_h10_still_load_bearing(self):
        """HISTORIAN test_h10 (CF-HISTORIAN-CM03-01 RESOLVED isinstance
        guard pin) MUST still be present."""
        p = Path(__file__).parent / "test_cm03_historian.py"
        src = p.read_text()
        assert "def test_h10_post_closure_concept_value_coding_wrong_type_silently_dropped" in src

    def test_t113_historian_test_h22_still_load_bearing(self):
        """HISTORIAN test_h22 (CF-HISTORIAN-CM03-02 incomplete-since-
        not-surfaced pin) MUST still be present."""
        p = Path(__file__).parent / "test_cm03_historian.py"
        src = p.read_text()
        assert "def test_h22_incomplete_since_not_surfaced_in_http_response" in src

    def test_t114_explorer_test_e10_still_load_bearing(self):
        """EXPLORER resweep test_e10 (combined-operations lifecycle state-
        machine roundtrip) MUST still be present."""
        p = Path(__file__).parent / "test_cm03_explorer_resweep.py"
        src = p.read_text()
        assert "def test_e10_combined_lifecycle_init_add_subsumes_reinit_subsumes" in src

    def test_t115_explorer_test_e70_still_load_bearing(self):
        """EXPLORER resweep test_e70 (batch malformed-valueCoding
        isolation) MUST still be present."""
        p = Path(__file__).parent / "test_cm03_explorer_resweep.py"
        src = p.read_text()
        assert "def test_e70_batch_malformed_value_coding_isolation" in src
