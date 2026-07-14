"""Regression tests for the Milestone 3 code review (review-15.md) fixes.

Covers CR-024 (cross-module parallel-map drift) and CR-025 (codeableConcept
branch canonical-URI echo) from ``docs/.ai_loop/spec_comp/reviews/review-15.md``.

Each fix has a tagged validation command (per PROC_VALIDATION.md §"Validation
Tagging") in the docstring of its test function so the engineer_handoff.md
can cite it directly.
"""

from __future__ import annotations

import inspect

import pytest

from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE


SNOMED_URI = "http://snomed.info/sct"
SNOMED_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_T2DM = "44054006"  # canonical display: "Type 2 diabetes mellitus"


def _param_value(parameters_body: dict, name: str):
    """Extract the value of a named parameter from a FHIR Parameters body."""
    for p in parameters_body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


# =============================================================================
# CR-024: Cross-module parallel-map drift — equivalence maps consolidated
# into ``engines/fhir/equivalence.py`` as single source of truth.
# =============================================================================


def test_cr024_canonical_equivalence_module_exists_and_is_importable():
    """CR-024 structural fix: ``engines/fhir/equivalence.py`` exists and
    exposes ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` and ``fhir_equivalence``.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone3_review_fixes.py
        ::test_cr024_canonical_equivalence_module_exists_and_is_importable -q``
    """
    from medterm4ds.engines.fhir.equivalence import (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE,
        fhir_equivalence,
    )

    assert isinstance(INTERNAL_REL_TO_FHIR_EQUIVALENCE, dict)
    assert callable(fhir_equivalence)


def test_cr024_both_consumers_import_same_map_object():
    """CR-024 structural fix: ``outputs/fhir.py:FHIR_EQUIVALENCES`` and
    ``responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` MUST be the SAME
    dict object (not just equal — identical). Future drift is impossible
    because both names reference the canonical map.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone3_review_fixes.py
        ::test_cr024_both_consumers_import_same_map_object -q``
    """
    from medterm4ds.engines.fhir.equivalence import (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE as canonical_map,
    )
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    assert FHIR_EQUIVALENCES is canonical_map, (
        "CR-024 regression: outputs/fhir.py:FHIR_EQUIVALENCES is not the "
        "canonical map object. The two surfaces can drift again."
    )
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE is canonical_map, (
        "CR-024 regression: responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE "
        "is not the canonical map object. The two surfaces can drift again."
    )


def test_cr024_canonical_module_load_assertion_present():
    """CR-024 structural fix: the canonical module has a module-load
    ``assert`` guarding closed-enum drift. Source-reading verification.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone3_review_fixes.py
        ::test_cr024_canonical_module_load_assertion_present -q``
    """
    from medterm4ds.engines.fhir import equivalence as equiv_module

    src = inspect.getsource(equiv_module)
    assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in src, (
        "engines/fhir/equivalence.py MUST reference "
        "FHIR_R4_CONCEPT_MAP_EQUIVALENCE in a module-load assertion "
        "guarding closed-enum drift (CR-024)."
    )
    assert "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()" in src, (
        "engines/fhir/equivalence.py MUST have a module-load assert "
        "enforcing INTERNAL_REL_TO_FHIR_EQUIVALENCE.values() <= "
        "FHIR_R4_CONCEPT_MAP_EQUIVALENCE."
    )


def test_cr024_spelling_divergence_resolved_to_unmatched():
    """CR-024: the ``not-relatedto`` (responses.py spelling) /
    ``not-related-to`` (outputs/fhir.py spelling) divergence is resolved.
    Both spellings are now keys in the unified map and both map to the
    R4 catch-all ``unmatched`` (not ``disjoint`` — that would be a
    stronger claim than the engine vocabulary warrants).

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone3_review_fixes.py
        ::test_cr024_spelling_divergence_resolved_to_unmatched -q``
    """
    from medterm4ds.engines.fhir.equivalence import (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE as m,
    )

    assert m["not-relatedto"] == "unmatched", (
        "CR-024 regression: 'not-relatedto' must map to 'unmatched' (R4 "
        "catch-all)."
    )
    assert m["not-related-to"] == "unmatched", (
        "CR-024 regression: 'not-related-to' (hyphenated) must map to "
        "'unmatched' (R4 catch-all). The prior 'disjoint' value was a "
        "stronger claim than the engine vocabulary warrants."
    )


def test_cr024_unified_map_emits_only_r4_values():
    """CR-024 invariant: every emitted value MUST be in the R4 closed
    enum. Applies to BOTH the $translate HTTP surface AND the ConceptMap
    export surface because both import the canonical map.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone3_review_fixes.py
        ::test_cr024_unified_map_emits_only_r4_values -q``
    """
    from medterm4ds.engines.fhir.equivalence import (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE as m,
    )

    drifted = set(m.values()) - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drifted, (
        f"CR-024 regression: unified equivalence map emits values outside "
        f"the FHIR R4 ConceptMapEquivalence closed enum: {drifted}."
    )


def test_cr024_outputs_fhir_helper_resolves_canonical_relationships():
    """CR-024: the ``outputs/fhir.py:fhir_equivalence`` helper now resolves
    the full engine + defensive pass-through surface (subsumes,
    specializes, etc.) instead of silently defaulting to ``relatedto``.
    Closes CF-TERMINOLOGIST-CM01-01 (latent gap on the ConceptMap export
    surface).

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone3_review_fixes.py
        ::test_cr024_outputs_fhir_helper_resolves_canonical_relationships -q``
    """
    from medterm4ds.outputs.fhir import fhir_equivalence

    # Pre-CR-024: outputs/fhir.py lacked these keys → silent default to
    # 'relatedto'. Post-CR-024: spec-correct R4 values are emitted.
    assert fhir_equivalence("subsumes") == "subsumes"
    assert fhir_equivalence("specializes") == "specializes"
    assert fhir_equivalence("subsumedby") == "specializes"
    assert fhir_equivalence("subsumed-by") == "specializes"
    # Engine pipeline values still resolve correctly:
    assert fhir_equivalence("equivalent") == "equivalent"
    assert fhir_equivalence("source-is-narrower-than-target") == "wider"
    assert fhir_equivalence("source-is-broader-than-target") == "narrower"
    assert fhir_equivalence("related-to") == "relatedto"
    assert fhir_equivalence("not-translated") == "unmatched"
    assert fhir_equivalence("unmatched") == "unmatched"


# =============================================================================
# CR-025: codeableConcept branch canonical-URI echo — wrap matched_uri
# through ``canonical_system_uri()`` in ``_do_validate`` and
# ``_do_vs_validate``.
# =============================================================================


def test_cr025_do_validate_codeable_concept_resolves_alias_to_canonical(fhir_client):
    """CR-025: ``POST /fhir/CodeSystem/$validate-code`` with a
    codeableConcept body containing a Coding whose ``system`` is the
    SNOMED OID alias MUST echo the CANONICAL FHIR URI
    (``http://snomed.info/sct``) in the Out ``system`` parameter, not
    the client-supplied alias.

    Pre-CR-025: the codeableConcept branch in ``_do_validate`` echoed
    ``matched_uri`` verbatim. The sibling scalar-system path was fixed
    in milestone-2 (CR-007); the codeableConcept branch was missed.
    Same client-input-as-canonical drift pattern (count=8 cumulative).

    Spec: FHIR R4 §4.8.21.1 Out ``system`` (canonical URI per CS-02
    TERMINOLOGIST DECISION (a)).

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone3_review_fixes.py
        ::test_cr025_do_validate_codeable_concept_resolves_alias_to_canonical -q``
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {
                                "system": SNOMED_OID_ALIAS,
                                "code": SNOMED_T2DM,
                            },
                        ],
                    },
                },
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True, (
        "codeableConcept with a valid SNOMED coding → result MUST be true."
    )
    out_system = _param_value(body, "system")
    assert out_system == SNOMED_URI, (
        f"CR-025 regression: codeableConcept branch echoed client alias "
        f"{out_system!r} verbatim; expected canonical {SNOMED_URI!r}."
    )


def test_cr025_do_vs_validate_codeable_concept_resolves_alias_to_canonical(fhir_client):
    """CR-025: ``POST /fhir/ValueSet/$validate-code`` with a
    codeableConcept body containing a Coding whose ``system`` is the
    SNOMED OID alias MUST echo the CANONICAL FHIR URI
    (``http://snomed.info/sct``) in the Out ``system`` parameter, not
    the client-supplied alias.

    Mirrors CR-025 on the ValueSet sibling handler. Pre-CR-025: the
    codeableConcept branch in ``_do_vs_validate`` echoed
    ``matched_uri`` verbatim.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone3_review_fixes.py
        ::test_cr025_do_vs_validate_codeable_concept_resolves_alias_to_canonical -q``
    """
    r = fhir_client.post(
        "/fhir/ValueSet/$validate-code",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {
                                "system": SNOMED_OID_ALIAS,
                                "code": SNOMED_T2DM,
                            },
                        ],
                    },
                },
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert _param_value(body, "result") is True, (
        "codeableConcept with a valid SNOMED coding → result MUST be true."
    )
    out_system = _param_value(body, "system")
    assert out_system == SNOMED_URI, (
        f"CR-025 regression: ValueSet codeableConcept branch echoed "
        f"client alias {out_system!r} verbatim; expected canonical "
        f"{SNOMED_URI!r}."
    )


def test_cr025_do_validate_codeable_concept_helper_wired_source_reading():
    """CR-025 source-reading verification: ``_do_validate`` and
    ``_do_vs_validate`` MUST call ``canonical_system_uri`` on the
    codeableConcept branch's ``matched_uri`` before passing it to the
    response builder. Source-reading probe so the test doesn't depend
    on fixture DB state.

    Validation: ``uv run --no-project pytest
        tests/fhir_conformance/test_milestone3_review_fixes.py
        ::test_cr025_do_validate_codeable_concept_helper_wired_source_reading -q``
    """
    from medterm4ds.apps.fhir_api import create_fhir_app

    src = inspect.getsource(create_fhir_app)
    # Both _do_validate and _do_vs_validate MUST wrap matched_uri through
    # canonical_system_uri on the codeableConcept branch. Count occurrences:
    # the scalar-system paths already call canonical_system_uri (milestone-2
    # CR-007/CR-011); the codeableConcept paths are the CR-025 additions.
    occurrences = src.count("canonical_system_uri(matched_uri)")
    assert occurrences >= 2, (
        f"CR-025 regression: expected at least 2 call sites of "
        f"canonical_system_uri(matched_uri) (one in _do_validate, one in "
        f"_do_vs_validate codeableConcept branches); found {occurrences}."
    )
