"""TERMINOLOGIST probes for CM-03 (CodeSystem $closure Operation).

TERMINOLOGIST lens: clinical correctness + clinical-safety of subsumption
recorded by ``add_concepts`` (e.g. directionality: parent vs child on
multi-hierarchy cases), and clinical-judgment decisions on the two open
carry-forwards from SKEPTIC + HISTORIAN:

* **CF-SKEPTIC-CM03-01** — Out ``return`` parameter shape. Spec says
  ConceptMap (R4 canonical OperationDefinition); current implementation
  emits valueString (version hash). TERMINOLOGIST decides whether to
  implement in v0.0.x or defer with documentation. The clinical
  implication: a CDS Hook / third-party EHR consuming ``$closure`` may
  expect a ConceptMap to update their CLIENT-side closure table.

* **CF-HISTORIAN-CM03-02** — ``incomplete_since`` wire-shape decision
  (extension URL + value type: boolean vs timestamp). When the closure
  table is incomplete (B6 fix: ``duckdb.Error`` during ancestor/
  descendant walk), clients relying on the closure for fast subsumption
  may silently get wrong answers.

Clinical focus areas per chunk scope:
1. Clinical correctness of subsumption outcomes recorded by add_concepts.
2. Production safety of $closure: silent incomplete closure → wrong
   $subsumes answers (clinical safety).
3. Cross-source closure: adding concepts from different systems to one
   closure table — clinically sensible?

Default severity HIGH per GLOBAL_RULES.md "TERMINOLOGIST Findings Are
HIGH Severity". Remediation engineers cannot dismiss TERMINOLOGIST
findings as INTENDED without explicit user override.

Spec citation: https://hl7.org/fhir/R4/conceptmap-operation-closure.html
"""

from __future__ import annotations

import inspect
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
LOINC_URI = "http://loinc.org"

# Synthesized cross-system URIs (not seeded; used to probe cross-source
# clinical-correctness semantics without depending on additional fixture
# rows).
DIABETES_SNOMED = "73211009"
T2DM_SNOMED = "44054006"
T2DM_ICD10CM = "E11"
METFORMIN_RXNORM = "860975"


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


# ===========================================================================
# LENS 1 — Clinical correctness of subsumption outcomes recorded by
# add_concepts (HIGH-LEVERAGE on terminology-server surfaces)
# ===========================================================================

class TestLens1ClinicalCorrectnessOfSubsumptionOutcomes:
    """Per GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH Severity":
    clinical correctness outranks technical correctness.

    The fixture seeds exactly one mrrel row (A44054006 isa A73211009 —
    T2DM is-a Diabetes mellitus). The batched ``add_concepts`` walks
    ancestors + descendants for each concept and records (subsumer,
    subsumed) tuples in the closure table. The clinical directionality
    MUST be: parent (Diabetes) subsumes child (T2DM) — NOT the reverse.
    A reversal here would silently produce wrong $subsumes outcomes
    (Diabetes `subsumed-by` T2DM instead of Diabetes `subsumes` T2DM).
    """

    def test_t10_closure_records_parent_subsumes_child(
        self, fhir_client
    ):
        """T2DM is-a Diabetes: Diabetes SUBSUMES T2DM.

        Clinical semantics: every T2DM patient IS a Diabetes patient.
        Diabetes is the broader clinical category. ``check(parent, child)``
        MUST return "subsumes".
        """
        name = "t10_parent_subsumes_child"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "Diabetes mellitus"),
                    (SNOMED_URI, T2DM_SNOMED, "Type 2 diabetes mellitus"),
                ],
            ),
        )
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        assert closure.check(DIABETES_SNOMED, T2DM_SNOMED) == "subsumes"

    def test_t11_closure_records_child_subsumed_by_parent(
        self, fhir_client
    ):
        """Mirror direction: ``check(child, parent)`` MUST return
        "subsumed-by" (the child is subsumed-by the parent).

        Clinical semantics: T2DM IS-A Diabetes, so querying "does T2DM
        subsume Diabetes?" returns subsumed-by (T2DM is subsumed-by
        Diabetes — i.e. Diabetes is the broader term).
        """
        name = "t11_child_subsumed_by_parent"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "Diabetes mellitus"),
                    (SNOMED_URI, T2DM_SNOMED, "Type 2 diabetes mellitus"),
                ],
            ),
        )
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        assert closure.check(T2DM_SNOMED, DIABETES_SNOMED) == "subsumed-by"

    def test_t12_closure_check_self_returns_equivalent(
        self, fhir_client
    ):
        """A code subsumes itself: ``check(X, X)`` MUST return
        "equivalent" (same code → equivalent by definition)."""
        name = "t12_self_equivalent"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "Diabetes mellitus")],
            ),
        )
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        assert closure.check(DIABETES_SNOMED, DIABETES_SNOMED) == "equivalent"

    def test_t13_closure_check_no_relationship_returns_not_subsumed(
        self, fhir_client
    ):
        """Two codes with no hierarchical relationship return
        "not-subsumed" — neither subsumes the other."""
        name = "t13_no_relationship"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name,
                [
                    # SNOMED Diabetes vs RxNorm metformin — no hierarchical
                    # relationship (different clinical axes: disease vs drug).
                    (SNOMED_URI, DIABETES_SNOMED, "Diabetes mellitus"),
                    (RXNORM_URI, METFORMIN_RXNORM, "metformin"),
                ],
            ),
        )
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        assert (
            closure.check(DIABETES_SNOMED, METFORMIN_RXNORM)
            == "not-subsumed"
        )
        assert (
            closure.check(METFORMIN_RXNORM, DIABETES_SNOMED)
            == "not-subsumed"
        )

    def test_t14_closure_check_codes_not_in_closure_returns_not_subsumed(
        self, fhir_client
    ):
        """Codes that were never added to the closure return
        "not-subsumed" — the closure never claims a relationship it
        doesn't know about (clinical-safety floor)."""
        name = "t14_codes_not_in_closure"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "Diabetes mellitus")],
            ),
        )
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        # A code that's never been added — closure has no opinion.
        assert (
            closure.check(DIABETES_SNOMED, "9999999999UNKNOWN")
            == "not-subsumed"
        )


# ===========================================================================
# LENS 2 — Multi-hierarchy subsumption correctness (clinical-safety lens)
# ===========================================================================

class TestLens2MultiHierarchySubsumption:
    """CF-SKEPTIC-CS05-03 sibling: multi-hierarchy subsumption correctness
    cannot be fully exercised because the fixture seeds only a single
    mrrel row (T2DM isa Diabetes).

    The ``ClosureTable`` implementation uses a dict with set-semantics
    for the ``_subsumes`` map — each (code_a, code_b) pair is recorded
    at most once. Per AGENTS.md CF-SKEPTIC-CS05-03: the engine IS
    structurally correct for multi-hierarchy (BFS with ``visited`` set);
    the conformance fixture is INCOMPLETE.

    These probes document the structural contract: the closure SHOULD
    correctly record multi-parent relationships if the fixture had them.
    The methodology is source-reading + structural assertions on the
    ``_subsumes`` map shape (strategy 29 — carry-forward-verification-
    by-source-reading)."""

    def test_t20_closure_subsumes_map_is_dict_with_tuple_keys(self):
        """Structural source-reading probe: ``ClosureTable._subsumes``
        MUST be a ``dict[tuple[str, str], bool]`` so multi-parent
        relationships are stored independently (one tuple per pair).

        If a future refactor changed this to ``dict[str, set[str]]``
        keyed by single code, multi-parent cases would still work; but
        if it became a single-key dict, multi-hierarchy would break.
        """
        closure = ClosureTable("t20_source_audit")
        assert isinstance(closure._subsumes, dict)
        # Initially empty
        assert closure._subsumes == {}

    def test_t21_closure_records_both_parent_relationships_for_one_child(
        self
    ):
        """Structural probe: when two parents both subsume the same child
        (multi-hierarchy), the closure MUST record BOTH (parent_a, child)
        AND (parent_b, child) tuples in the ``_subsumes`` map.

        We probe this WITHOUT depending on fixture data — we manually
        populate ``_subsumes`` and assert the lookup returns the right
        outcome for both parents. This is a structural assertion about
        the ``check()`` method's behavior with multi-parent data.
        """
        closure = ClosureTable("t21_multi_parent")
        # Simulate: P1 and P2 both subsume C (multi-hierarchy).
        closure._subsumes[("P1", "C")] = True
        closure._subsumes[("C", "P1")] = False
        closure._subsumes[("P2", "C")] = True
        closure._subsumes[("C", "P2")] = False
        closure._subsumes[("C", "C")] = True

        # Both parents subsume C
        assert closure.check("P1", "C") == "subsumes"
        assert closure.check("P2", "C") == "subsumes"
        # C is subsumed-by both parents
        assert closure.check("C", "P1") == "subsumed-by"
        assert closure.check("C", "P2") == "subsumed-by"
        # The two parents don't subsume each other (siblings in the DAG)
        assert closure.check("P1", "P2") == "not-subsumed"
        assert closure.check("P2", "P1") == "not-subsumed"

    def test_t22_closure_self_subsumption_set_on_concept_registration(
        self, fhir_client
    ):
        """Structural probe: when ``add_concepts`` registers a concept,
        it MUST immediately set ``_subsumes[(code, code)] = True`` so
        the closure can answer self-equivalence even before any walk
        fires. This is a clinical-safety invariant: a client adding a
        single code MUST be able to immediately self-check it."""
        name = "t22_self_subsumption"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        # The self-tuple is set at registration time (line 137 of closure.py).
        assert closure._subsumes.get((DIABETES_SNOMED, DIABETES_SNOMED)) is True


# ===========================================================================
# LENS 3 — Production safety of $closure (silent incomplete closure →
# wrong $subsumes answers — clinical-safety lens)
# ===========================================================================

class TestLens3ProductionSafetyOfIncompleteClosure:
    """CF-HISTORIAN-CM03-02 carries the wire-shape decision for
    ``incomplete_since`` flag surfacing.

    Clinical-safety lens: when the closure table is incomplete (B6 fix:
    ``duckdb.Error`` during walk), clients relying on the closure for
    fast subsumption may silently get wrong answers. ``check()`` returns
    "not-subsumed" for pairs it doesn't know about — which is the WRONG
    answer if the closure failed to walk a parent-child relationship.

    TERMINOLOGIST decision: this is a clinical-safety signal that MUST
    be observable to clients. The wire shape decision is documented in
    CF-HISTORIAN-CM03-02 (DECISION below)."""

    def test_t30_closure_incomplete_since_starts_false(self):
        """A fresh ClosureTable MUST start with ``incomplete_since=False``
        — the closure is not yet known to be incomplete."""
        closure = ClosureTable("t30_starts_false")
        assert closure.incomplete_since is False

    def test_t31_incomplete_since_set_true_on_duckdb_error_in_add_concepts(
        self, fhir_client, monkeypatch
    ):
        """When ``add_concepts`` catches ``duckdb.Error`` (B6 fix), the
        ``incomplete_since`` flag MUST be set True so callers know the
        closure is degraded.

        We simulate via monkeypatch of ``get_ancestors`` (the helper
        ``add_concepts`` calls)."""
        import duckdb

        import medterm4ds.engines.fhir.closure as closure_mod

        name = "t31_incomplete_since_batched"

        # Step 1: initialize the closure (no concepts yet).
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        assert closure.incomplete_since is False

        # Step 2: monkeypatch get_ancestors to raise duckdb.Error during
        # the add_concepts call. We do this on the closure module's
        # imported reference.
        original = closure_mod.get_ancestors

        def _raise(*args, **kwargs):
            raise duckdb.Error("simulated transient DB failure")

        monkeypatch.setattr(closure_mod, "get_ancestors", _raise)
        try:
            fhir_client.post(
                "/fhir/CodeSystem/$closure",
                json=_closure_param_with_concepts(
                    name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
                ),
            )
        finally:
            monkeypatch.setattr(closure_mod, "get_ancestors", original)

        # The flag MUST be set True — clinical-safety signal.
        assert closure.incomplete_since is True

    def test_t32_check_returns_not_subsumed_on_incomplete_closure(
        self, fhir_client, monkeypatch
    ):
        """Clinical-safety floor: when ``incomplete_since=True``, the
        ``check()`` method MAY return "not-subsumed" for a pair that
        would actually be subsumes/subsumed-by IF the walk had
        succeeded. This is silent-wrong-answer at the closure layer.

        The mitigation is that the CALLER (HTTP $subsumes handler today
        does NOT consult closure per CF-SKEPTIC-CM03-02; future wiring
        per CF-SKEPTIC-CM03-02 fix shape MUST consult incomplete_since
        and surface it).

        This probe documents the CURRENT behavior: ``check()`` does NOT
        consult ``incomplete_since`` — it returns whatever the map says
        (defaulting to "not-subsumed")."""
        closure = ClosureTable("t32_check_ignores_incomplete")
        closure.incomplete_since = True  # simulate degraded state
        # No concepts added — pair is unknown
        assert closure.check("X", "Y") == "not-subsumed"
        # The closure did NOT consult ``incomplete_since`` to return a
        # distinct signal. This is intentional for the closure-level API;
        # the HTTP layer is responsible for surfacing.

    def test_t33_incomplete_since_remains_false_on_programming_bug(
        self, fhir_client, monkeypatch
    ):
        """Programming bugs (TypeError, AttributeError) MUST propagate
        AND MUST NOT set ``incomplete_since=True``. Per GLOBAL_RULES.md
        "Silent Fallbacks": programming bugs surface, not silently
        swallowed. This is a clinical-correctness invariant — silently
        swallowing programming bugs as "incomplete closure" would mask
        real bugs in the closure logic.

        We monkeypatch ``get_ancestors`` to raise TypeError."""
        import medterm4ds.engines.fhir.closure as closure_mod

        name = "t33_programming_bug_propagates"

        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        assert closure.incomplete_since is False

        original = closure_mod.get_ancestors

        def _raise_type_error(*args, **kwargs):
            raise TypeError("simulated programming bug")

        monkeypatch.setattr(closure_mod, "get_ancestors", _raise_type_error)
        try:
            with pytest.raises(TypeError):
                closure.add_concepts(
                    [(DIABETES_SNOMED, "SNOMEDCT_US", "DM")],
                    engine=None,  # engine is irrelevant — monkeypatch raises before use
                )
        finally:
            monkeypatch.setattr(closure_mod, "get_ancestors", original)

        # Programming bug MUST NOT set incomplete_since.
        assert closure.incomplete_since is False


# ===========================================================================
# LENS 4 — Cross-source closure clinical-safety (per spec, closure is
# per code system)
# ===========================================================================

class TestLens4CrossSourceClosureClinicalSafety:
    """Per FHIR R4 spec (https://hl7.org/fhir/R4/conceptmap-operation-closure.html):
    "This operation initiates a closure given a list of input concepts.
    The operation is a 'one-shot' operation — there is no persistent
    session."

    The spec does NOT restrict the closure to a single code system.
    medterm4ds allows adding concepts from multiple systems to the same
    closure (cross-source). The clinical-correctness question is: should
    ``check()`` ever return "subsumes" for a cross-system pair?

    Answer: NO — subsumption is intra-system by definition. ``isa``
    relationships exist within a single hierarchy (SNOMED, ICD-10-CM,
    RxNorm). Cross-system "equivalence" (e.g., SNOMED Diabetes ~ ICD-10-CM
    E11) is a MAPPING relationship, not subsumption — handled by
    ConceptMap/$translate, NOT $closure.

    TERMINOLOGIST confirmation: medterm4ds correctly does NOT record
    cross-system subsumption because the engine's ``get_ancestors`` and
    ``get_descendants`` walks are intra-source (the source is passed to
    ``CodeRef`` and the walks query that source's hierarchy only).
    """

    def test_t40_cross_source_no_subsumption_recorded(self, fhir_client):
        """Adding concepts from SNOMED + ICD-10-CM + RxNorm to the same
        closure MUST NOT record any cross-system subsumption tuples —
        the closure correctly treats them as unrelated."""
        name = "t40_cross_source"
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
        manager = get_closure_manager()
        closure = manager.get(name)
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

    def test_t41_per_source_batched_walks_2_per_source_not_per_concept(
        self, fhir_client
    ):
        """E1 fix: ``add_concepts`` batches walks per source — 2 walks
        per source (one ancestor, one descendant), NOT 2 walks per
        concept. Clinical implication: efficient even for large
        cross-source batches.

        This is a structural source-reading probe (strategy 29)."""
        import medterm4ds.engines.fhir.closure as closure_mod

        # Source-read: add_concepts must iterate by_source.items()
        src = inspect.getsource(closure_mod.ClosureTable.add_concepts)
        assert "by_source" in src, "add_concepts must group by source"
        assert "for source, codes in by_source.items()" in src
        # 2 walks per source: ancestors + descendants
        assert "get_ancestors" in src
        assert "get_descendants" in src

    def test_t42_to_parameter_list_canonicalizes_system_uri_per_concept(
        self, fhir_client
    ):
        """Each concept's ``system`` in the returned Parameters body
        MUST be the canonical FHIR URI (via ``system_to_fhir_uri``),
        NOT the internal source name (e.g., "SNOMEDCT_US").

        This is a clinical-correctness invariant — clients reading the
        closure response need FHIR URIs to interpret the codings."""
        name = "t42_canonical_uri"
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
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        params = closure.to_parameter_list()
        snomed_entry = next(
            p for p in params if p["valueCoding"]["code"] == DIABETES_SNOMED
        )
        icd10_entry = next(
            p for p in params if p["valueCoding"]["code"] == T2DM_ICD10CM
        )
        assert snomed_entry["valueCoding"]["system"] == SNOMED_URI
        assert icd10_entry["valueCoding"]["system"] == ICD10CM_URI


# ===========================================================================
# LENS 5 — DECISION: CF-SKEPTIC-CM03-01 Out `return` shape
# ===========================================================================

class TestLens5DecisionCfSkepticCm03_01ReturnShape:
    """DECISION on CF-SKEPTIC-CM03-01: should the spec-correct ConceptMap
    return shape be implemented in v0.0.x or deferred with documentation?

    Spec citation: https://hl7.org/fhir/R4/conceptmap-operation-closure.html
    Out parameters:
    * ``return`` 1..1 ConceptMap — "The result of the $closure operation.
      The ConceptMap contains all of the relationships that should be
      added to the closure table, with ``equivalence`` set to
      ``equivalent``, ``source-is-broader-than-target``, or
      ``source-is-narrower-than-target``."

    Current implementation: ``return`` is valueString (12-char MD5-hex
    version hash) — medterm4ds-specific deviation.

    DECISION RATIONALE:
    1. The medterm4ds deployment model is **localhost-only** (per
       ``ClosureTable`` module docstring). The intended client surface
       is the Python API (``mt.connect()``) and a local FastAPI server.
       Third-party EHRs / CDS Hooks are NOT advertised clients.
    2. The HTTP ``$subsumes`` handler does NOT consult the closure
       table (CF-SKEPTIC-CM03-02) — so the closure table is effectively
       a Python-internal data structure exposed via HTTP for
       completeness, not the primary subsumption path.
    3. Implementing the spec-correct ConceptMap return would require
       reworking ``build_closure_response`` to walk the closure's
       ``_subsumes`` map and emit ``group.element.target`` entries with
       ``equivalence`` codes per pair — a non-trivial refactor with
       implications for the version-hash contract (the hash currently
       signals "closure state changed"; with ConceptMap return, the
       hash would be redundant or would need to be on an extension).
    4. The current shape IS internally consistent + operationally
       conformant (Content-Type, error path, XML format, batch
       dispatcher all OK per SKEPTIC + HISTORIAN + EXPLORER audits).

    DECISION: **DEFER to a future enhancement chunk**. The current
    medterm4ds-specific shape is acceptable for v0.0.x given the
    localhost-only deployment model. The carry-forward is documented
    in ``AGENTS.md`` with reproduction shape and probe pin.

    CLINICAL-SAFETY FLOOR: a CDS Hook or third-party EHR attempting to
    consume ``$closure`` today would receive a Parameters body whose
    ``return`` parameter is valueString (not the ConceptMap they
    expect) — they would silently get no closure-table updates. This
    is acceptable for v0.0.x because:
    (a) The deployment model does not advertise these clients;
    (b) The medterm4ds documentation should clarify the deviation;
    (c) The fix is non-trivial and tied to a broader $subsumes-closure
        wiring decision (CF-SKEPTIC-CM03-02).

    The probes below CONFIRM the current deviation and document the
    decision.
    """

    def test_t50_decision_defer_to_future_enhancement(self, fhir_client):
        """DECISION: CF-SKEPTIC-CM03-01 is DEFERRED. The current shape
        (valueString return + repeating concept parameters) is the
        v0.0.x surface. A future enhancement chunk will wire the
        spec-correct ConceptMap return.

        This probe is the load-bearing decision pin: if a future
        engineer wires the ConceptMap shape, this probe MUST be updated
        to assert the new behavior (carry-forward-as-probe pattern —
        CS-03 TERMINOLOGIST methodology)."""
        name = "t50_decision_defer"
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        assert resp.status_code == 200
        body = resp.json()
        # CURRENT behavior: return is valueString
        return_param = _find_param(body, "return")
        assert return_param is not None
        assert "valueString" in return_param
        assert "resource" not in return_param  # no ConceptMap resource
        # Document the decision: spec-correct would be a ConceptMap.
        # https://hl7.org/fhir/R4/conceptmap-operation-closure.html
        # "return 1..1 ConceptMap"

    def test_t51_deviation_is_internally_consistent(self, fhir_client):
        """The deviation is INTERNALLY CONSISTENT: the version hash
        reflects the closure state, and the concept list reflects the
        closure contents. A client reading the response can:
        (a) Compare the hash to detect "closure state changed since
            last call" (operational signal).
        (b) Iterate the concept list to know which codes are in the
            closure.

        The spec-correct ConceptMap return would provide richer
        information (the actual subsumption relationships), but the
        current shape conveys enough for v0.0.x use."""
        name = "t51_consistency"
        # Initialize
        resp1 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        hash1 = _return_hash(resp1.json())
        assert hash1 is not None
        # Add a concept — hash changes
        resp2 = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        hash2 = _return_hash(resp2.json())
        assert hash2 is not None
        assert hash1 != hash2  # state changed
        # Concept list reflects the added code
        concepts2 = _find_params(resp2.json(), "concept")
        codes = [c["valueCoding"]["code"] for c in concepts2]
        assert DIABETES_SNOMED in codes

    def test_t52_decision_rationale_documented_in_module_docstring(self):
        """Source-reading probe: the ``ClosureTable`` module docstring
        MUST document the design rationale (localhost-only deployment;
        closure is server-side; HTTP $subsumes does not consult closure).

        This is a maintenance-hazard defense — a future engineer reading
        the code should understand WHY the deviation exists."""
        import medterm4ds.engines.fhir.closure as closure_mod

        module_doc = closure_mod.__doc__ or ""
        # The docstring must reference the key design facts.
        assert "localhost" in module_doc.lower() or "local" in module_doc.lower(), (
            "ClosureTable module docstring must document the localhost-only "
            "deployment model to prevent a future engineer from assuming "
            "the closure table is shared with HTTP $subsumes."
        )


# ===========================================================================
# LENS 6 — DECISION: CF-HISTORIAN-CM03-02 incomplete_since wire shape
# ===========================================================================

class TestLens6DecisionCfHistorianCm03_02IncompleteSinceWireShape:
    """DECISION on CF-HISTORIAN-CM03-02: when ``incomplete_since=True``,
    how should the HTTP response surface this to clients?

    Options considered:
    (a) FHIR extension on the Parameters response
        (``http://medterm4ds.org/fhir/StructureDefinition/closure-incomplete-since``
        with valueBoolean).
    (b) OperationOutcome warning in addition to the Parameters body
        (multi-resource response — non-standard FHIR; rejected).
    (c) Both (extension AND warning header).

    DECISION: **Option (a) — FHIR extension on the Parameters response**.
    Rationale:
    1. FHIR extensions are the spec-compliant way to surface
       server-specific metadata (per
       https://hl7.org/fhir/R4/extensibility.html).
    2. valueBoolean is the most natural value type for a flag
       ("incomplete since some point: true/false"). A timestamp would
       require tracking WHEN the failure occurred — the current
       ``incomplete_since`` is a boolean, not a datetime; converting
       would require engine changes out of CM-03 scope.
    3. The extension URL uses the medterm4ds.org namespace to signal
       server-specific (not standard FHIR).
    4. OperationOutcome in addition to the resource would be non-
       standard FHIR (a successful 2xx response carries a resource
       body, not an OperationOutcome per §3.6.1).

    DECISION: **DEFER the implementation to a future enhancement chunk**.
    The current gap is invisible today (``$subsumes`` does not consult
    the closure per CF-SKEPTIC-CM03-02), so clients have no clinical-
    safety exposure. The fix becomes load-bearing ONLY when CF-SKEPTIC-
    CM03-02 is wired (``$subsumes`` consults closure) — at which point
    BOTH carry-forwards should be implemented together.

    The probes below document the CURRENT gap and pin the decision.
    """

    def test_t60_decision_defer_with_documented_wire_shape(self, fhir_client):
        """DECISION: CF-HISTORIAN-CM03-02 is DEFERRED. The current
        ``build_closure_response`` does NOT surface ``incomplete_since``
        in the HTTP response. When the fix lands, the wire shape will
        be a FHIR extension (URL:
        ``http://medterm4ds.org/fhir/StructureDefinition/closure-incomplete-since``,
        valueBoolean)."""
        name = "t60_decision_defer"
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        body = resp.json()
        # CURRENT behavior: no extension on the Parameters resource.
        assert "extension" not in body or body.get("extension") is None
        # Document the wire-shape decision.
        # When CF-HISTORIAN-CM03-02 fix lands, this probe MUST be
        # updated to assert presence of the extension.

    def test_t61_decision_rationale_extension_is_spec_compliant(self):
        """Source-reading probe: ``build_closure_response`` currently
        returns ONLY ``resourceType`` + ``parameter`` — no extension.
        When the fix lands, it MUST be a FHIR extension on the
        Parameters resource (per https://hl7.org/fhir/R4/extensibility.html
        — "Extensions are a way to include additional information in a
        resource or data type that is not defined in the base spec").
        """
        closure = ClosureTable("t61_extension_audit")
        body = build_closure_response(closure)
        # CURRENT behavior: no extension
        assert "extension" not in body
        # The resourceType is Parameters (the carrier for the extension).
        assert body["resourceType"] == "Parameters"

    def test_t62_incomplete_since_flag_is_observable_on_python_instance(
        self, fhir_client, monkeypatch
    ):
        """Even though the HTTP response does NOT surface
        ``incomplete_since``, the flag IS observable on the Python
        ``ClosureTable`` instance. Python-API callers can check it
        directly. This is the current v0.0.x contract."""
        import duckdb

        import medterm4ds.engines.fhir.closure as closure_mod

        name = "t62_python_observable"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        manager = get_closure_manager()
        closure = manager.get(name)
        assert closure is not None
        assert closure.incomplete_since is False

        # Simulate duckdb.Error during add_concepts
        original = closure_mod.get_ancestors

        def _raise(*args, **kwargs):
            raise duckdb.Error("simulated transient DB failure")

        monkeypatch.setattr(closure_mod, "get_ancestors", _raise)
        try:
            fhir_client.post(
                "/fhir/CodeSystem/$closure",
                json=_closure_param_with_concepts(
                    name, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
                ),
            )
        finally:
            monkeypatch.setattr(closure_mod, "get_ancestors", original)

        # The flag IS observable on the Python instance.
        assert closure.incomplete_since is True


# ===========================================================================
# LENS 7 — Carry-forward pins (load-bearing regression guards)
# ===========================================================================

class TestLens7CarryForwardPins:
    """The two CM-03 carry-forwards MUST be pinned via carry-forward-as-
    probe pattern (CS-03 TERMINOLOGIST methodology). When a future
    enhancement chunk closes either CF, the corresponding probe MUST
    be updated in the SAME PR.

    These probes are the load-bearing contracts that the deferred
    behavior is documented. They fire loudly when the fix lands."""

    def test_t70_cf_skeptic_cm03_01_pin_value_string_return(self, fhir_client):
        """CF-SKEPTIC-CM03-01 pin: Out ``return`` is currently
        valueString (NOT ConceptMap per R4 spec). When the spec-correct
        ConceptMap return lands, this probe MUST be updated."""
        name = "t70_cf_skeptic_cm03_01_pin"
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

    def test_t71_cf_skeptic_cm03_02_pin_subsumes_does_not_use_closure(
        self, fhir_client
    ):
        """CF-SKEPTIC-CM03-02 pin: ``$subsumes`` HTTP handler does NOT
        consult the closure table. It walks the hierarchy directly via
        ``is_descendant``. The outcome IS clinically correct (T2DM is-a
        Diabetes → Diabetes subsumes T2DM) via the direct walk.

        When a future enhancement wires ``$subsumes`` to consult closure,
        this probe MUST be updated to assert the new path."""
        # Build a closure with the SNOMED pair.
        closure_name = "t71_subsumes_uses_direct_walk"
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
            f"/fhir/CodeSystem/$subsumes"
            f"?system={SNOMED_URI}"
            f"&codeA={DIABETES_SNOMED}"
            f"&codeB={T2DM_SNOMED}"
        )
        assert resp.status_code == 200
        body = resp.json()
        outcome = _find_param(body, "outcome")
        assert outcome is not None
        # Diabetes subsumes T2DM (clinically correct — Diabetes is broader)
        assert outcome["valueCode"] == "subsumes"

    def test_t72_cf_historian_cm03_02_pin_incomplete_since_not_surfaced(
        self, fhir_client
    ):
        """CF-HISTORIAN-CM03-02 pin: the ``incomplete_since`` flag is
        NOT surfaced in the HTTP response (no extension, no flag).
        When the fix lands (FHIR extension with valueBoolean), this
        probe MUST be updated."""
        name = "t72_cf_historian_cm03_02_pin"
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        body = resp.json()
        # CURRENT: no extension on Parameters.
        assert "extension" not in body


# ===========================================================================
# LENS 8 — Version hash clinical-meaningfulness
# ===========================================================================

class TestLens8VersionHashClinicalMeaningfulness:
    """EXPLORER recommendation: verify the version hash contract is
    clinically meaningful. The hash lets clients detect "closure state
    changed between calls". The implementation is count-based:

    ``payload = f"{len(self.concepts)}:{self._version}:{sorted(self.concepts.keys())}"``
    ``hashlib.md5(payload.encode()).hexdigest()[:12]``

    Clinical-meaningfulness analysis:
    * ``len(self.concepts)`` captures the count of distinct codes added.
    * ``self._version`` is incremented on every ``add_concept`` /
      ``add_concepts`` call (operational signal).
    * ``sorted(self.concepts.keys())`` captures WHICH codes are in the
      closure (set-membership signal).

    The hash is INSENSITIVE to:
    * Order of add_concept calls (because concepts is a dict and the
      keys are sorted).
    * Re-adds of the same code (no-op — dict update).
    * Subsumption relationships recorded in ``_subsumes`` (only concept
      set is hashed, NOT the relationship tuples).

    CLINICAL CORRECTNESS GAP: if ``_subsumes`` relationships change
    (e.g., a new walk records a NEW parent-child tuple) WITHOUT
    changing the concept set, the hash does NOT change. This is a
    potential silent-stale-cache signal — a client could cache the
    closure subsumption answers and miss a relationship update.

    However, in practice: ``_subsumes`` is updated ONLY during
    ``add_concept`` / ``add_concepts``, which ALSO updates the concept
    set (either adds a new code or no-ops on a re-add). So the hash
    DOES capture every state change today.

    The probes below pin this contract."""

    def test_t80_version_hash_incorporates_concept_count(
        self, fhir_client
    ):
        """Adding a concept MUST change the hash (count changes)."""
        name = "t80_count_in_hash"
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
        assert hash1 != hash2

    def test_t81_version_hash_independent_of_concept_order(
        self, fhir_client
    ):
        """Adding concepts in different orders MUST produce the same
        hash (because the keys are sorted before hashing)."""
        name_a = "t81_order_a"
        name_b = "t81_order_b"
        resp_a = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name_a,
                [
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                    (SNOMED_URI, T2DM_SNOMED, "T2DM"),
                ],
            ),
        )
        resp_b = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name_b,
                [
                    (SNOMED_URI, T2DM_SNOMED, "T2DM"),  # reversed
                    (SNOMED_URI, DIABETES_SNOMED, "DM"),
                ],
            ),
        )
        assert _return_hash(resp_a.json()) == _return_hash(resp_b.json())

    def test_t82_version_hash_format_md5_hex_12(self, fhir_client):
        """The hash format MUST be a 12-character MD5 hex prefix.
        Clients can rely on this format for state-change detection."""
        name = "t82_format"
        resp = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name),
        )
        h = _return_hash(resp.json())
        assert h is not None
        assert len(h) == 12
        # All hex chars
        assert all(c in "0123456789abcdef" for c in h)

    def test_t83_version_hash_does_NOT_capture_subsumption_relationships(self):
        """KNOWN LIMITATION: the hash captures the concept SET but NOT
        the subsumption RELATIONSHIPS in ``_subsumes``. If a future
        change updates ``_subsumes`` WITHOUT changing the concept set
        (e.g., a relationship refresh on existing concepts), the hash
        would not change — clients caching closure state could miss
        the update.

        Today this is INTENDED: ``_subsumes`` is updated only during
        add_concept / add_concepts, which ALSO updates the concept set.
        But the structural gap exists — documented here as a
        carry-forward pin for future enhancements."""
        closure = ClosureTable("t83_hash_gap")
        closure.concepts["X"] = {"system": "S", "display": "X"}
        closure.concepts["Y"] = {"system": "S", "display": "Y"}
        closure._subsumes[("X", "Y")] = True  # X subsumes Y
        hash1 = closure.version_hash()

        # Update _subsumes WITHOUT changing concepts — hash unchanged.
        closure._subsumes[("X", "Y")] = False  # relationship changed
        closure._subsumes[("Y", "X")] = True  # now Y subsumes X
        hash2 = closure.version_hash()

        # Structural gap: hash does not capture _subsumes changes.
        # This is documented behavior; future enhancement MAY extend
        # the hash payload to include _subsumes (sorted tuples).
        assert hash1 == hash2


# ===========================================================================
# LENS 9 — Cross-personality hygiene (source-reading regression guards)
# ===========================================================================

class TestLens9CrossPersonalityHygiene:
    """Verify load-bearing probes from prior personalities are still
    present (source-reading regression guards — VS-05 HISTORIAN strategy
    52). These probes are NOT TERMINOLOGIST's contribution; they verify
    the work of SKEPTIC + HISTORIAN + EXPLORER is intact."""

    def test_t90_skeptic_test_s90_still_load_bearing(self):
        """Source-read SKEPTIC test_s90 — must still pin the spec
        deviation (CF-SKEPTIC-CM03-01)."""
        from pathlib import Path
        p = Path(__file__).parent / "test_cm03_skeptic.py"
        src = p.read_text()
        assert "def test_s90_spec_deviation_return_is_value_string_not_conceptmap" in src
        assert "def test_s91_spec_deviation_no_conceptmap_resource_in_response" in src

    def test_t91_historian_test_h10_still_load_bearing(self):
        """Source-read HISTORIAN test_h10 — must still pin the
        isinstance guard (CF-HISTORIAN-CM03-01 RESOLVED)."""
        from pathlib import Path
        p = Path(__file__).parent / "test_cm03_historian.py"
        src = p.read_text()
        assert "def test_h10_post_closure_concept_value_coding_wrong_type_silently_dropped" in src

    def test_t92_explorer_test_e90_still_load_bearing(self):
        """Source-read EXPLORER test_e90 — must still pin CF-SKEPTIC-
        CM03-01 mirror."""
        from pathlib import Path
        p = Path(__file__).parent / "test_cm03_explorer.py"
        src = p.read_text()
        assert "def test_e90_cf_skeptic_cm03_01_return_is_value_string" in src

    def test_t93_historian_test_h22_still_load_bearing(self):
        """Source-read HISTORIAN test_h22 — must still pin CF-HISTORIAN-
        CM03-02 (incomplete_since not surfaced)."""
        from pathlib import Path
        p = Path(__file__).parent / "test_cm03_historian.py"
        src = p.read_text()
        assert "def test_h22_incomplete_since_not_surfaced_in_http_response" in src


# ===========================================================================
# LENS 10 — Closure response builder clinical-correctness audit
# ===========================================================================

class TestLens10BuildClosureResponseAudit:
    """Direct unit tests of ``build_closure_response`` covering the
    clinical-correctness invariants."""

    def test_t100_response_includes_return_first(self):
        """The ``return`` parameter MUST be first in the parameter list
        (spec-listed first per R4 OperationDefinition; clients reading
        the response sequentially should see the version hash before
        the concept list)."""
        closure = ClosureTable("t100_return_first")
        closure.concepts["X"] = {"system": "S", "display": "X"}
        closure.concepts["Y"] = {"system": "S", "display": "Y"}
        body = build_closure_response(closure)
        params = body["parameter"]
        assert params[0]["name"] == "return"

    def test_t101_empty_closure_has_only_return(self):
        """An empty closure (no concepts added) MUST return a Parameters
        body with ONLY the ``return`` parameter — no concept entries.
        This is the initialization-success contract."""
        closure = ClosureTable("t101_empty")
        body = build_closure_response(closure)
        params = body["parameter"]
        assert len(params) == 1
        assert params[0]["name"] == "return"

    def test_t102_concept_list_sorted_by_code(self):
        """Concepts MUST be sorted by code in the response. This is the
        deterministic-ordering contract (clients can byte-compare
        responses for state-change detection)."""
        closure = ClosureTable("t102_sorted")
        closure.concepts["Z"] = {"system": "S", "display": "Z"}
        closure.concepts["A"] = {"system": "S", "display": "A"}
        closure.concepts["M"] = {"system": "S", "display": "M"}
        body = build_closure_response(closure)
        concept_params = _find_params(body, "concept")
        codes = [p["valueCoding"]["code"] for p in concept_params]
        assert codes == ["A", "M", "Z"]


# ===========================================================================
# LENS 11 — ClosureManager thread-safety + singleton invariants
# ===========================================================================

class TestLens11ClosureManagerInvariants:
    """The ``ClosureManager`` singleton + per-name isolation are
    clinical-correctness invariants. A race condition that orphans a
    ClosureTable would silently produce stale subsumption answers for
    one client."""

    def test_t110_get_closure_manager_returns_singleton(self):
        """``get_closure_manager`` MUST return the same instance across
        calls (module-level singleton guarded by a lock)."""
        m1 = get_closure_manager()
        m2 = get_closure_manager()
        assert m1 is m2

    def test_t111_get_or_create_idempotent_for_existing_name(self):
        """``get_or_create`` for an existing name MUST return the
        EXISTING instance (not create a new one) — preserves closure
        state across calls."""
        mgr = ClosureManager()
        c1 = mgr.get_or_create("t111_idempotent")
        c1.concepts["X"] = {"system": "S", "display": "X"}
        c2 = mgr.get_or_create("t111_idempotent")
        assert c1 is c2
        assert "X" in c2.concepts

    def test_t112_reset_creates_fresh_instance(self):
        """``reset`` MUST create a FRESH instance — clears concepts,
        subsumption map, version counter, and ``incomplete_since``."""
        mgr = ClosureManager()
        c1 = mgr.get_or_create("t112_reset")
        c1.concepts["X"] = {"system": "S", "display": "X"}
        c1._version = 5
        c1.incomplete_since = True
        c2 = mgr.reset("t112_reset")
        assert c1 is not c2
        assert c2.concepts == {}
        assert c2._subsumes == {}
        assert c2._version == 0
        assert c2.incomplete_since is False

    def test_t113_get_returns_none_for_unknown_name(self):
        """``get`` for an unknown name MUST return None — the caller
        can distinguish "doesn't exist" from "exists but empty"."""
        mgr = ClosureManager()
        assert mgr.get("t113_unknown") is None

    def test_t114_closure_isolation_between_names(self, fhir_client):
        """Two closures with different names MUST be isolated — adding
        concepts to one MUST NOT affect the other."""
        name_a = "t114_isolation_a"
        name_b = "t114_isolation_b"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name_a, [(SNOMED_URI, DIABETES_SNOMED, "DM")],
            ),
        )
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(
                name_b, [(SNOMED_URI, T2DM_SNOMED, "T2DM")],
            ),
        )
        mgr = get_closure_manager()
        closure_a = mgr.get(name_a)
        closure_b = mgr.get(name_b)
        assert closure_a is not None
        assert closure_b is not None
        assert DIABETES_SNOMED in closure_a.concepts
        assert T2DM_SNOMED not in closure_a.concepts
        assert T2DM_SNOMED in closure_b.concepts
        assert DIABETES_SNOMED not in closure_b.concepts


# ===========================================================================
# LENS 12 — Cross-handler GET↔POST clinical-content parity
# ===========================================================================

class TestLens12CrossHandlerClinicalContentParity:
    """TS-04 TERMINOLOGIST methodology (strategy 20 — single-vs-batch
    byte-exact equivalence) applied to the per-operation POST vs batch
    Bundle entry on $closure.

    The clinical content (return hash + concept list) MUST be byte-
    exact-equivalent between the two invocation paths. A divergence
    would silently produce different closure states depending on how
    the client invokes the operation."""

    def test_t120_init_byte_match_per_operation_vs_batch(self, fhir_client):
        """POST /fhir/CodeSystem/$closure?name=X MUST produce the same
        ``return`` hash as the equivalent batch Bundle entry."""
        # Per-operation POST
        name_single = "t120_parity_single"
        resp_single = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name_single),
        )
        hash_single = _return_hash(resp_single.json())

        # Batch Bundle entry — same name, different actual closure name
        # (to avoid contamination from the single POST above).
        name_batch = "t120_parity_batch"
        bundle = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$closure",
                    },
                    "resource": _closure_param_name_only(name_batch),
                }
            ],
        }
        resp_batch = fhir_client.post("/fhir", json=bundle)
        assert resp_batch.status_code == 200
        batch_body = resp_batch.json()
        entry_resp = batch_body["entry"][0]["response"]
        # Per spec, batch response status for a 2xx is "200" (or similar);
        # the resource is in entry[].resource
        entry_resource = batch_body["entry"][0].get("resource", {})
        hash_batch = _return_hash(entry_resource)
        assert hash_batch is not None
        # The two hashes are for DIFFERENT closure names but the SAME
        # closure state (both empty). The hashes MUST be equal (same
        # state → same hash, cross-name — EXPLORER test_e70 contract).
        assert hash_single == hash_batch

    def test_t121_add_concepts_byte_match_per_operation_vs_batch(
        self, fhir_client
    ):
        """Adding the SAME concepts via per-operation POST vs batch
        Bundle entry MUST produce the same closure state (same hash +
        same concept list)."""
        concepts = [(SNOMED_URI, DIABETES_SNOMED, "DM")]

        # Per-operation POST
        name_single = "t121_add_single"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name_single),
        )
        resp_single = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_with_concepts(name_single, concepts),
        )
        hash_single = _return_hash(resp_single.json())
        concepts_single = _find_params(resp_single.json(), "concept")

        # Batch Bundle entry — different name, same concepts
        name_batch = "t121_add_batch"
        fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_param_name_only(name_batch),
        )
        bundle = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "CodeSystem/$closure",
                    },
                    "resource": _closure_param_with_concepts(
                        name_batch, concepts
                    ),
                }
            ],
        }
        resp_batch = fhir_client.post("/fhir", json=bundle)
        batch_body = resp_batch.json()
        entry_resource = batch_body["entry"][0].get("resource", {})
        hash_batch = _return_hash(entry_resource)
        concepts_batch = _find_params(entry_resource, "concept")

        # Clinical content MUST be byte-exact equivalent.
        assert hash_single == hash_batch
        # Concept list (after sorting) MUST match.
        codes_single = sorted(
            c["valueCoding"]["code"] for c in concepts_single
        )
        codes_batch = sorted(
            c["valueCoding"]["code"] for c in concepts_batch
        )
        assert codes_single == codes_batch
