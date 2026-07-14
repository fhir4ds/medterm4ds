"""Single source of truth for medterm4ds engine → FHIR R4 ConceptMapEquivalence
translation.

Per milestone-3 code review (CR-024, review-15.md Finding 1): two parallel maps
translated the same engine vocabulary (``CodeMapping.relationship`` /
``ConceptMapRow.relationship``) with divergent key/value pairs:

  * ``engines/fhir/responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` — used by
    ``build_parameters_translate`` (the $translate HTTP surface).
  * ``outputs/fhir.py:FHIR_EQUIVALENCES`` — used by ``concept_map_to_fhir``
    (the ConceptMap export surface).

Divergences resolved by this module:

  * Spelling drift: ``"not-relatedto"`` (responses.py, no hyphen) vs
    ``"not-related-to"`` (outputs/fhir.py, with hyphen). The engine never emits
    either spelling today (``conceptmap_relationship`` emits only
    ``equivalent``, ``source-is-narrower-than-target``,
    ``source-is-broader-than-target``, ``related-to``, ``not-translated``,
    ``unmatched``), but both are accepted defensively to keep the map resilient
    if a future engine vocabulary change uses either spelling.
  * Value drift: the prior ``outputs/fhir.py`` mapped ``"not-related-to"`` to
    ``disjoint``; the prior ``responses.py`` mapped ``"not-relatedto"`` to
    ``unmatched``. Per R4 spec
    (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html) both
    ``unmatched`` and ``disjoint`` are R4 enum values, but they mean different
    things: ``unmatched`` is the catch-all for "no mapping"; ``disjoint`` is
    the explicit assertion "the definitions of the concepts are disconnected".
    The unified map maps BOTH spellings to ``unmatched`` — the catch-all is
    the safer default for an unknown engine vocabulary token, and matches the
    milestone-2 remediation decision (CF-HISTORIAN-VS01-01, see
    ``responses.py`` commit history).

Both surfaces (``responses.py`` and ``outputs/fhir.py``) import
``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` and ``fhir_equivalence`` from this module.
The closed-enum membership assertion at module load applies to BOTH surfaces
uniformly — a future drift value cannot land on either surface without also
failing the assertion here.
"""

from __future__ import annotations

from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE

# Map medterm4ds internal CodeMapping.relationship / ConceptMapRow.relationship
# vocabulary to the FHIR R4 ConceptMapEquivalence enum
# (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html).
#
# The engine emits at minimum: "equivalent" (same-CUI mappings),
# "source-is-narrower-than-target" (hierarchy ancestor/descendant where source
# is more specific), "source-is-broader-than-target" (the reverse), "related-to"
# (component / first-axis / loinc-common), "not-translated" (original — no
# translation in the target system), and "unmatched" (no match).
#
# Direction-sensitive keys (R5 source-centric naming convention):
#   * ``source-is-narrower-than-target`` ⇒ target is wider ⇒ R4 ``wider``
#   * ``source-is-broader-than-target`` ⇒ target is narrower ⇒ R4 ``narrower``
# R4 ``equivalence`` is read from the TARGET perspective
# (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html):
#   * ``wider``    = "The target mapping is WIDER in meaning than the source."
#   * ``narrower`` = "The target mapping is NARROWER in meaning than the source."
# Found by SKEPTIC iteration CM-01 (CM01-SKEPTIC-001) — the prior responses.py
# map had these inverted.
#
# CF-HISTORIAN-VS01-01 (milestone-2 review): the prior responses.py map emitted
# two values that are NOT in the FHIR R4 closed enum:
#   * ``subsumedby`` (R5/R4B value; R4 spec-correct is ``specializes``).
#   * ``not-relatedto`` (not in ANY FHIR enum; R4 catch-all for "no mapping"
#     is ``unmatched``).
# Fixed in the milestone-2 structural remediation pass to use R4 spec-correct
# values. This module inherits the fix and applies it uniformly to both the
# $translate surface and the ConceptMap export surface.
#
# Defensive pass-through entries (keys the engine does not emit today but that
# are accepted to keep the map resilient if a future engine vocabulary change
# uses the R4 codes verbatim): ``wider``, ``narrower``, ``broader``,
# ``subsumes``, ``specializes``, ``relatedto``, ``disjoint``.
#
# Spelling-aliases (defensive — accept both hyphenated and unhyphenated forms):
#   * ``not-relatedto`` / ``not-related-to`` — both map to ``unmatched``. The
#     prior outputs/fhir.py used ``disjoint`` for the hyphenated form; unified
#     to ``unmatched`` for consistency with the responses.py milestone-2 fix
#     and the conservative "unknown engine vocabulary → catch-all" semantic.
#   * ``subsumedby`` / ``subsumed-by`` — both map to ``specializes``.
INTERNAL_REL_TO_FHIR_EQUIVALENCE: dict[str, str] = {
    # Engine pipeline values (the canonical relationship vocabulary):
    "equivalent": "equivalent",
    "same": "equal",
    "identical": "equal",
    "source-is-narrower-than-target": "wider",
    "source-is-broader-than-target": "narrower",
    "related-to": "relatedto",
    # ``not-translated``: there is no translation for this source concept in
    # the target system. The R4 catch-all for "no mapping" is ``unmatched``
    # (per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html).
    # The prior outputs/fhir.py used ``equivalent`` here — wrong (silent
    # wrong-answer: a client reading the ConceptMap export would treat a
    # missing translation as a confirmed equivalence). Found by SKEPTIC
    # iteration CM-01 (CM01-SKEPTIC-002).
    "not-translated": "unmatched",
    "unmatched": "unmatched",
    # Defensive pass-through (R4 enum values the engine does not emit today):
    "wider": "wider",
    "narrower": "narrower",
    "broader": "narrower",
    "subsumes": "subsumes",
    # R4 spec-correct value for "target is-a source" (target subsumes source).
    "subsumedby": "specializes",
    "subsumed-by": "specializes",
    "specializes": "specializes",
    "relatedto": "relatedto",
    # Spelling-aliases for "no relationship" — both hyphenated and
    # unhyphenated forms map to the R4 catch-all ``unmatched``.
    "not-relatedto": "unmatched",
    "not-related-to": "unmatched",
    "disjoint": "disjoint",
}

# Registry-as-contract: every emitted value MUST be in the R4 closed enum.
# A failure here is a programming bug (someone added an off-spec value to the
# map); let it propagate loudly rather than silently producing a
# non-conformant response. Applies to BOTH the $translate surface
# (``responses.py``) and the ConceptMap export surface (``outputs/fhir.py``)
# because both import from this module.
assert (
    set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    <= FHIR_R4_CONCEPT_MAP_EQUIVALENCE
), (
    "INTERNAL_REL_TO_FHIR_EQUIVALENCE emits values outside the FHIR R4 "
    "ConceptMapEquivalence closed enum. Drift values: "
    f"{set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()) - FHIR_R4_CONCEPT_MAP_EQUIVALENCE}"
)


def fhir_equivalence(relationship: str | None) -> str:
    """Map an internal relationship label to a FHIR R4 ConceptMapEquivalence
    code.

    Single source of truth for the engine-vocabulary → R4 enum translation.
    Used by both:

      * ``outputs/fhir.py:concept_map_to_fhir`` (ConceptMap export surface).
      * ``engines/fhir/responses.py:build_parameters_translate`` (the
        $translate HTTP surface) — via the ``_fhir_equivalence_from_relationship``
        thin wrapper that preserves the case-insensitive fallback behaviour.

    Returns ``"relatedto"`` for unknown / null / empty relationships — the
    FHIR enum's catch-all for "a relationship exists but isn't a strict
    equivalence". Never raises: the FHIR enum is closed, so unrecognized
    internal vocabularies MUST be translated rather than echoed raw
    (otherwise the response contains a value outside the FHIR R4 value set).
    """
    if not relationship:
        return "relatedto"
    return INTERNAL_REL_TO_FHIR_EQUIVALENCE.get(relationship, "relatedto")
